import json
from pathlib import Path

import pytest

from self_harness.adapters.terminal_bench.capture import capture_single_task
from self_harness.adapters.terminal_bench.corpus import load_terminal_bench_manifest
from self_harness.adapters.terminal_bench.preflight import load_preflight_report, run_preflight
from self_harness.adapters.terminal_bench.runner import HarborRunner
from self_harness.cli import main
from self_harness.exceptions import PaperFidelityError
from self_harness.harness import initial_harness

FIXTURE_DIR = Path("tests/fixtures/terminal_bench")
MANIFEST = FIXTURE_DIR / "manifest.json"
VALID_DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def test_preflight_reports_missing_runtime() -> None:
    report = run_preflight(
        "terminal-bench@2.0",
        harbor_executable="__missing_harbor_for_self_harness__",
        docker_executable="__missing_docker_for_self_harness__",
    )
    checks = {check.name: check.status for check in report.checks}

    assert not report.passed
    assert checks["harbor_present"] == "fail"
    assert checks["docker_cli_present"] == "fail"
    assert checks["docker_daemon_reachable"] == "fail"


def test_preflight_report_round_trips(tmp_path: Path) -> None:
    out_dir = tmp_path / "preflight"

    code = main(
        [
            "terminal-bench-preflight",
            "--dataset",
            "terminal-bench@2.0",
            "--manifest",
            str(MANIFEST),
            "--harbor-executable",
            "__missing_harbor_for_self_harness__",
            "--out",
            str(out_dir),
            "--json",
        ]
    )
    report = load_preflight_report(out_dir / "preflight.json")

    assert code == 2
    assert not report.passed


def test_live_mode_preflight_failure_writes_report_without_rounds(tmp_path: Path) -> None:
    out_dir = tmp_path / "live"

    code = main(
        [
            "terminal-bench",
            "--mode",
            "live",
            "--dataset",
            "terminal-bench@2.0",
            "--manifest",
            str(MANIFEST),
            "--harbor-executable",
            "__missing_harbor_for_self_harness__",
            "--out",
            str(out_dir),
        ]
    )

    assert code == 2
    assert (out_dir / "preflight.json").exists()
    assert not (out_dir / "rounds").exists()


def test_terminal_bench_image_policy_requires_trusted_image_before_preflight(tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "tb"
    policy = _write_image_policy(tmp_path / "policy.json", "ghcr.io/example/terminal-bench:latest", None)

    code = main(
        [
            "terminal-bench",
            "--mode",
            "live",
            "--dataset",
            "terminal-bench@2.0",
            "--manifest",
            str(MANIFEST),
            "--image-policy",
            str(policy),
            "--harbor-executable",
            "__missing_harbor_for_self_harness__",
            "--out",
            str(out_dir),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["reason"] == "invalid-verifier"
    assert not (out_dir / "preflight.json").exists()
    assert not (out_dir / "rounds").exists()


def test_terminal_bench_require_image_digest_fails_without_trusted_digest(tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "tb"

    code = main(
        [
            "terminal-bench",
            "--mode",
            "dry-run",
            "--dataset",
            "terminal-bench@2.0",
            "--manifest",
            str(MANIFEST),
            "--fixture-dir",
            str(FIXTURE_DIR),
            "--require-image-digest",
            "--out",
            str(out_dir),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["reason"] == "invalid-verifier"
    assert not (out_dir / "rounds").exists()


def test_terminal_bench_dry_run_image_policy_allows_and_is_deterministic(tmp_path: Path, capsys) -> None:
    policy = _write_image_policy(tmp_path / "policy.json", "ghcr.io/example/terminal-bench:latest", None)
    first = tmp_path / "first"
    second = tmp_path / "second"
    command = [
        "terminal-bench",
        "--mode",
        "dry-run",
        "--dataset",
        "terminal-bench@2.0",
        "--manifest",
        str(MANIFEST),
        "--fixture-dir",
        str(FIXTURE_DIR),
        "--image-policy",
        str(policy),
        "--trust-container-image",
        "ghcr.io/example/terminal-bench:latest",
        "--rounds",
        "1",
        "--evaluation-repeats",
        "1",
        "--out",
    ]

    assert main([*command, str(first)]) == 0
    capsys.readouterr()
    assert main([*command, str(second)]) == 0
    capsys.readouterr()

    assert _tree_bytes(first) == _tree_bytes(second)


def test_capture_single_task_with_synthetic_harbor_fixture(tmp_path: Path) -> None:
    harbor = _write_fake_harbor(tmp_path / "fake-harbor", exit_code=0)
    fixture_dir = tmp_path / "captured"

    capture = capture_single_task(
        "terminal-bench@2.0",
        MANIFEST,
        "held-out-smoke",
        fixture_dir,
        harbor_executable=str(harbor),
    )
    fixture = json.loads((fixture_dir / "held-out-smoke.json").read_text())
    corpus_task = [task for task in load_terminal_bench_manifest(MANIFEST).tasks if task.id == "held-out-smoke"][0]
    replay = HarborRunner(dataset="terminal-bench@2.0", fixture_dir=fixture_dir).run(
        corpus_task,
        initial_harness(),
    )

    assert capture.reproduction_claimed is False
    assert capture.task_source_hash == corpus_task.metadata["task_source_hash"]
    assert fixture["benchmark_protocol"] == "terminal-bench@2.0"
    assert fixture["capture_source"] == "single-task-harbor-run"
    assert fixture["task_source_hash"] == corpus_task.metadata["task_source_hash"]
    assert replay.passed


def test_captured_fixture_rejects_stale_manifest_task_hash(tmp_path: Path) -> None:
    harbor = _write_fake_harbor(tmp_path / "fake-harbor", exit_code=0)
    fixture_dir = tmp_path / "captured"
    capture_single_task(
        "terminal-bench@2.0",
        MANIFEST,
        "held-out-smoke",
        fixture_dir,
        harbor_executable=str(harbor),
    )
    stale_manifest = tmp_path / "manifest.json"
    manifest_data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_data["tasks"][1]["instruction"] = "This held-out task changed after capture."
    stale_manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    stale_task = [
        task for task in load_terminal_bench_manifest(stale_manifest).tasks if task.id == "held-out-smoke"
    ][0]

    with pytest.raises(PaperFidelityError):
        HarborRunner(dataset="terminal-bench@2.0", fixture_dir=fixture_dir).run(
            stale_task,
            initial_harness(),
        )


def test_harbor_runner_live_mode_parses_structured_fake_harbor_output(tmp_path: Path) -> None:
    argv_path = tmp_path / "argv.txt"
    harbor = _write_structured_fake_harbor(tmp_path / "fake-harbor", argv_path)
    task = [task for task in load_terminal_bench_manifest(MANIFEST).tasks if task.id == "held-out-smoke"][0]

    record = HarborRunner(
        dataset="terminal-bench@2.0",
        mode="live",
        harbor_executable=str(harbor),
        model="anthropic/claude-haiku-4-5",
    ).run(task, initial_harness())

    argv = argv_path.read_text(encoding="utf-8")
    assert record.passed
    assert record.outcome.terminal_cause == "verifier-pass"
    assert record.metadata["container_image_digest"] == "sha256:test"
    assert "--dataset terminal-bench@2.0" in argv
    assert "--model anthropic/claude-haiku-4-5" in argv
    assert "--task held-out-smoke" in argv


def test_terminal_bench_live_mode_accepts_matching_image_policy(tmp_path: Path, capsys) -> None:
    argv_path = tmp_path / "argv.txt"
    harbor = _write_structured_fake_harbor(tmp_path / "fake-harbor", argv_path, digest=VALID_DIGEST)
    policy = _write_image_policy(tmp_path / "policy.json", "ghcr.io/example/terminal-bench:latest", VALID_DIGEST)
    out_dir = tmp_path / "live"

    code = main(
        [
            "terminal-bench",
            "--mode",
            "live",
            "--dataset",
            "terminal-bench@2.0",
            "--manifest",
            str(MANIFEST),
            "--harbor-executable",
            str(harbor),
            "--skip-docker-preflight",
            "--image-policy",
            str(policy),
            "--trust-container-image",
            "ghcr.io/example/terminal-bench:latest",
            "--trust-container-image-digest",
            VALID_DIGEST,
            "--require-image-digest",
            "--rounds",
            "1",
            "--evaluation-repeats",
            "1",
            "--out",
            str(out_dir),
        ]
    )
    capsys.readouterr()

    assert code == 0
    assert (out_dir / "preflight.json").exists()
    assert (out_dir / "rounds" / "0").is_dir()


def test_terminal_bench_live_mode_rejects_container_digest_mismatch(tmp_path: Path, capsys) -> None:
    argv_path = tmp_path / "argv.txt"
    harbor = _write_structured_fake_harbor(tmp_path / "fake-harbor", argv_path, digest=OTHER_DIGEST)
    out_dir = tmp_path / "live"

    code = main(
        [
            "terminal-bench",
            "--mode",
            "live",
            "--dataset",
            "terminal-bench@2.0",
            "--manifest",
            str(MANIFEST),
            "--harbor-executable",
            str(harbor),
            "--skip-docker-preflight",
            "--trust-container-image-digest",
            VALID_DIGEST,
            "--rounds",
            "1",
            "--evaluation-repeats",
            "1",
            "--out",
            str(out_dir),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["reason"] == "invalid-verifier"
    assert (out_dir / "preflight.json").exists()
    assert not (out_dir / "rounds").exists()


def test_harbor_runner_live_mode_preserves_and_parses_artifacts(tmp_path: Path) -> None:
    harbor = _write_artifact_fake_harbor(tmp_path / "fake-harbor")
    task = [task for task in load_terminal_bench_manifest(MANIFEST).tasks if task.id == "held-out-smoke"][0]
    keep_run_dir = tmp_path / "preserved"

    record = HarborRunner(
        dataset="terminal-bench@2.0",
        mode="live",
        harbor_executable=str(harbor),
        keep_run_dir=keep_run_dir,
    ).run(task, initial_harness())

    assert record.passed
    assert record.metadata["reward_value"] == 1.0
    assert record.metadata["reward_source"] == "reward.json"
    assert record.metadata["trajectory_event_count"] == 1
    assert (keep_run_dir / "held-out-smoke" / "0" / "reward.json").exists()


def _write_fake_harbor(path: Path, *, exit_code: int) -> Path:
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'harbor fake 0.0.0'; exit 0; fi\n"
        "echo 'fake harbor run'\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_artifact_fake_harbor(path: Path) -> Path:
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'harbor fake 0.0.0'; exit 0; fi\n"
        "printf '{\"task_id\":\"held-out-smoke\"}' > metadata.json\n"
        "printf '{\"reward\":1.0}' > reward.json\n"
        "printf '{\"kind\":\"assistant\",\"message\":\"done\"}\\n' > trajectory.jsonl\n"
        "printf '{\"task_id\":\"held-out-smoke\",\"passed\":true,\"terminal_cause\":\"verifier-pass\","
        "\"mechanism\":\"fake-harbor\",\"verifier_output\":\"ok\"}\\n'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_image_policy(path: Path, image: str, digest: str | None) -> Path:
    path.write_text(
        json.dumps(
            {
                "policy_version": "1",
                "entries": [
                    {
                        "image": image,
                        "digest": digest,
                        "status": "active",
                        "labels": {"purpose": "terminal-bench-test"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _write_structured_fake_harbor(path: Path, argv_path: Path, *, digest: str = "sha256:test") -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('harbor fake 0.0.0')\n"
        "    raise SystemExit(0)\n"
        f"open({str(argv_path)!r}, 'w', encoding='utf-8').write(' '.join(sys.argv[1:]))\n"
        "task_id = 'unknown'\n"
        "for index, item in enumerate(sys.argv):\n"
        "    if item == '--task' and index + 1 < len(sys.argv):\n"
        "        task_id = sys.argv[index + 1]\n"
        "print(json.dumps({\n"
        "    'task_id': task_id,\n"
        "    'passed': True,\n"
        "    'terminal_cause': 'verifier-pass',\n"
        "    'mechanism': 'fake-harbor',\n"
        "    'verifier_output': 'ok',\n"
        f"    'container_digest': {digest!r},\n"
        "}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
