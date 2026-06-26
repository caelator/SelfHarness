import json
from pathlib import Path

import pytest

from self_harness.adapters.container_preflight import run_container_preflight
from self_harness.adapters.container_verifier import (
    ContainerCommandSpec,
    ContainerVerifierRunner,
    ContainerVerifierTaskAdapter,
    build_container_run_command,
)
from self_harness.cli import main
from self_harness.config import EngineConfig
from self_harness.corpus import TaskCorpus
from self_harness.engine import SelfHarnessEngine
from self_harness.exceptions import TaskLoadError
from self_harness.harness import initial_harness
from self_harness.image_policy import load_image_policy
from self_harness.proposer import HeuristicProposer
from self_harness.types import Split, Task

FIXTURE_DIR = Path("tests/fixtures/container_verifier")
VALID_DIGEST = "sha256:" + "a" * 64


def test_container_verifier_rejects_corpus_selected_image_or_command() -> None:
    adapter = ContainerVerifierTaskAdapter(image="trusted:latest")
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="bad",
        tasks=[
            Task(
                "bad",
                Split.HELD_IN,
                "container_verifier",
                "bad",
                {"verifier_selector": "pass", "container_image": "evil:latest"},
            )
        ],
    )

    with pytest.raises(TaskLoadError):
        adapter.load(corpus)

    registry_corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="bad-registry",
        tasks=[
            Task(
                "bad-registry",
                Split.HELD_IN,
                "container_verifier",
                "bad",
                {"verifier_selector": "pass", "registry_password": "secret"},
            )
        ],
    )
    with pytest.raises(TaskLoadError):
        adapter.load(registry_corpus)

    policy_corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="bad-policy",
        tasks=[
            Task(
                "bad-policy",
                Split.HELD_IN,
                "container_verifier",
                "bad",
                {"verifier_selector": "pass", "image_policy": "policy.json"},
            )
        ],
    )
    with pytest.raises(TaskLoadError):
        adapter.load(policy_corpus)


def test_container_verifier_dry_run_command_spec_is_stable(tmp_path: Path) -> None:
    spec = ContainerCommandSpec(
        image="trusted/verifier:1",
        image_digest="sha256:abc",
        command=("verify", "--json"),
        workdir=tmp_path,
        env_files=(tmp_path / "env.list",),
    )

    command = build_container_run_command(spec, docker_executable="docker")

    assert command == [
        "docker",
        "run",
        "--rm",
        "--workdir",
        "/work",
        "-v",
        f"{tmp_path}:/work",
        "--env-file",
        str(tmp_path / "env.list"),
        "trusted/verifier:1@sha256:abc",
        "verify",
        "--json",
    ]


def test_container_verifier_dry_run_fixture_replay() -> None:
    runner = ContainerVerifierRunner(image="trusted:latest", fixture_dir=FIXTURE_DIR)

    passed = runner.run(_task("pass", "pass"), initial_harness())
    failed = runner.run(_task("fail", "fail"), initial_harness())
    missing = runner.run(_task("missing", "missing"), initial_harness())

    assert passed.passed
    assert passed.outcome.mechanism == "container-verifier"
    assert not failed.passed
    assert failed.outcome.terminal_cause == "assertion-fail"
    assert not missing.passed
    assert missing.outcome.mechanism == "container-dry-run-no-fixture"


def test_container_preflight_failure_reports_missing_docker() -> None:
    report = run_container_preflight(
        "trusted:latest",
        docker_executable="__missing_docker_for_self_harness__",
    )

    checks = {check.name: check.status for check in report.checks}

    assert not report.passed
    assert checks["docker_cli_present"] == "fail"


def test_container_demo_live_preflight_failure_writes_report_without_rounds(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus)
    out_dir = tmp_path / "live"

    code = main(
        [
            "container-demo",
            str(corpus),
            "--trust-container-image",
            "trusted:latest",
            "--mode",
            "live",
            "--docker-executable",
            "__missing_docker_for_self_harness__",
            "--out",
            str(out_dir),
        ]
    )

    assert code == 2
    assert (out_dir / "preflight.json").exists()
    assert not (out_dir / "rounds").exists()


def test_container_verifier_live_uses_fake_docker_and_digest(tmp_path: Path) -> None:
    argv_path = tmp_path / "argv.json"
    docker = _write_fake_docker(tmp_path / "docker", argv_path)

    runner = ContainerVerifierRunner(
        image="trusted/verifier:1",
        image_digest="sha256:abc",
        command=("verify",),
        mode="live",
        docker_executable=str(docker),
    )
    record = runner.run(_task("pass", "pass"), initial_harness())
    argv = json.loads(argv_path.read_text(encoding="utf-8"))

    assert record.passed
    assert "trusted/verifier:1@sha256:abc" in argv


def test_container_verifier_env_file_and_docker_config_are_not_traced(tmp_path: Path) -> None:
    argv_path = tmp_path / "argv.json"
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    docker = _write_fake_docker_with_env_probe(tmp_path / "docker-auth", argv_path)
    secret = "SECRET_TOKEN=s3cr3t"

    runner = ContainerVerifierRunner(
        image="trusted/verifier:1",
        command=("verify",),
        mode="live",
        docker_executable=str(docker),
        extra_env=(("SECRET_TOKEN", "s3cr3t"),),
        docker_config_dir=docker_config,
    )
    record = runner.run(_task("pass", "pass"), initial_harness())
    payload = json.loads(argv_path.read_text(encoding="utf-8"))
    trace_event = next(event for event in record.trace if event.kind == "container-command")
    trace_json = json.dumps(trace_event.metadata)

    assert record.passed
    assert "--env-file" in payload["argv"]
    assert secret in payload["env_file_contents"][0]
    assert secret not in json.dumps(payload["argv"])
    assert payload["docker_config"] == str(docker_config)
    assert "<redacted-env-file>" in trace_json
    assert secret not in trace_json


def test_container_demo_cli_trust_boundary_and_dry_run(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus)
    out_dir = tmp_path / "run"

    with pytest.raises(SystemExit) as exc:
        main(["container-demo", str(corpus), "--out", str(out_dir)])
    assert exc.value.code == 2

    code = main(
        [
            "container-demo",
            str(corpus),
            "--trust-container-image",
            "trusted:latest",
            "--fixture-dir",
            str(FIXTURE_DIR),
            "--rounds",
            "1",
            "--evaluation-repeats",
            "2",
            "--out",
            str(out_dir),
        ]
    )
    output = capsys.readouterr().out
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert code == 0
    assert "not a benchmark reproduction" in output
    assert manifest["model_id"] == "container-verifier-dry-run"


def test_container_demo_image_policy_allows_dry_run(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus.json"
    policy = _write_image_policy(tmp_path / "policy.json", "trusted:latest", VALID_DIGEST)
    _write_corpus(corpus)
    out_dir = tmp_path / "run"

    code = main(
        [
            "container-demo",
            str(corpus),
            "--trust-container-image",
            "trusted:latest",
            "--trust-container-image-digest",
            VALID_DIGEST,
            "--image-policy",
            str(policy),
            "--require-image-digest",
            "--fixture-dir",
            str(FIXTURE_DIR),
            "--rounds",
            "1",
            "--evaluation-repeats",
            "2",
            "--out",
            str(out_dir),
        ]
    )
    capsys.readouterr()

    assert code == 0
    assert (out_dir / "rounds" / "0").is_dir()


def test_container_verifier_image_policy_allows_image_level_entry_with_digest(tmp_path: Path) -> None:
    policy = load_image_policy(_write_image_policy(tmp_path / "policy.json", "trusted:latest", None))

    adapter = ContainerVerifierTaskAdapter(
        image="trusted:latest",
        image_digest=VALID_DIGEST,
        image_policy=policy,
        require_image_digest=True,
        fixture_dir=FIXTURE_DIR,
    )
    record = adapter.runner().run(_task("pass", "pass"), initial_harness())

    assert record.passed


def test_container_demo_image_policy_rejects_before_rounds_or_docker(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus.json"
    policy = _write_image_policy(tmp_path / "policy.json", "other:latest", VALID_DIGEST)
    docker_probe = tmp_path / "docker-called"
    docker = _write_probe_docker(tmp_path / "docker", docker_probe)
    _write_corpus(corpus)
    out_dir = tmp_path / "run"

    code = main(
        [
            "container-demo",
            str(corpus),
            "--trust-container-image",
            "trusted:latest",
            "--trust-container-image-digest",
            VALID_DIGEST,
            "--image-policy",
            str(policy),
            "--mode",
            "live",
            "--docker-executable",
            str(docker),
            "--out",
            str(out_dir),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["reason"] == "invalid-verifier"
    assert not (out_dir / "rounds").exists()
    assert not (out_dir / "preflight.json").exists()
    assert not docker_probe.exists()


def test_container_demo_require_image_digest_fails_without_digest(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus)
    out_dir = tmp_path / "run"

    code = main(
        [
            "container-demo",
            str(corpus),
            "--trust-container-image",
            "trusted:latest",
            "--require-image-digest",
            "--fixture-dir",
            str(FIXTURE_DIR),
            "--out",
            str(out_dir),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["reason"] == "invalid-verifier"
    assert not (out_dir / "rounds").exists()


def test_container_verifier_engine_artifacts_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    _run_engine(first)
    _run_engine(second)

    assert _tree_bytes(first) == _tree_bytes(second)


def _run_engine(out_dir: Path) -> None:
    adapter = ContainerVerifierTaskAdapter(image="trusted:latest", fixture_dir=FIXTURE_DIR)
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="container-fixture",
        tasks=[
            _task("held-in-pass", "pass", split=Split.HELD_IN),
            _task("held-out-pass", "pass", split=Split.HELD_OUT),
        ],
    )
    SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=adapter.runner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, evaluation_repeats=2, model_id="container-verifier-dry-run"),
    ).run()


def _write_corpus(path: Path) -> None:
    payload = {
        "corpus_version": "1",
        "corpus_id": "container-cli-fixture",
        "tasks": [
            _task_row("held-in-pass", "held_in", "pass"),
            _task_row("held-out-pass", "held_out", "pass"),
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_image_policy(path: Path, image: str, digest: str | None, *, status: str = "active") -> Path:
    payload = {
        "policy_version": "1",
        "entries": [
            {
                "image": image,
                "digest": digest,
                "status": status,
                "labels": {"purpose": "test"},
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _task(id_: str, selector: str, *, split: Split = Split.HELD_IN) -> Task:
    return Task(
        id=id_,
        split=split,
        failure_mode="container_verifier",
        description=id_,
        metadata={"verifier_selector": selector},
    )


def _task_row(id_: str, split: str, selector: str) -> dict[str, object]:
    return {
        "id": id_,
        "split": split,
        "failure_mode": "container_verifier",
        "description": id_,
        "metadata": {"verifier_selector": selector},
    }


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _write_fake_docker(path: Path, argv_path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1:3] == ['info', '--format']:\n"
        "    print('fake-docker')\n"
        "    raise SystemExit(0)\n"
        f"open({str(argv_path)!r}, 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'passed': True, 'failure_category': None, "
        "'mechanism': 'fake-container', 'message': 'ok'}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_fake_docker_with_env_probe(path: Path, argv_path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "contents = []\n"
        "for index, item in enumerate(argv):\n"
        "    if item == '--env-file' and index + 1 < len(argv):\n"
        "        contents.append(open(argv[index + 1], encoding='utf-8').read())\n"
        f"open({str(argv_path)!r}, 'w', encoding='utf-8').write(json.dumps({{\n"
        "    'argv': argv,\n"
        "    'docker_config': os.environ.get('DOCKER_CONFIG'),\n"
        "    'env_file_contents': contents,\n"
        "}))\n"
        "print(json.dumps({'passed': True, 'failure_category': None, "
        "'mechanism': 'fake-container', 'message': 'ok'}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_probe_docker(path: Path, probe_path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        f"open({str(probe_path)!r}, 'w', encoding='utf-8').write('called')\n"
        "print('fake-docker')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
