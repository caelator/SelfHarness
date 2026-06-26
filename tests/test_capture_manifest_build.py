import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from self_harness.capture_manifest import (
    capture_manifest_report_to_jsonable,
    verify_capture_manifest,
)
from self_harness.corpus_signing import generate_keypair
from self_harness.reproduction_readiness import load_reproduction_requirements
from self_harness.types import stable_json_dumps
from test_reproduction_readiness import (
    FIXTURE_SIGNER,
    REPO_ROOT,
    REQUIREMENTS,
    _class_shaped_payloads,
)

BUILD_SCRIPT = Path("scripts") / "capture_manifest_build.py"
SIGN_SCRIPT = Path("scripts") / "sign_capture_manifest.py"
VERIFY_SCRIPT = Path("scripts") / "capture_manifest_verify.py"
EXPECTED_BUILD_HASH = "ba86a1f91423a1b152e16d59b007f769c8937926df2140bba1613d225f9d55fc"


def test_capture_manifest_build_script_is_deterministic_and_verifies(tmp_path: Path) -> None:
    manifest = tmp_path / "capture-manifest.json"

    first = _run_build(
        *_base_build_args(manifest),
        "--entry-note",
        "live_harbor_preflight_report=planned by operator preflight",
    )
    first_bytes = manifest.read_bytes()
    second = _run_build(
        *_base_build_args(manifest),
        "--entry-note",
        "live_harbor_preflight_report=planned by operator preflight",
    )
    payload = json.loads(first.stdout)
    report = verify_capture_manifest(manifest, load_reproduction_requirements(REPO_ROOT / REQUIREMENTS))
    report_payload = capture_manifest_report_to_jsonable(report)

    assert first.returncode == 0
    assert second.returncode == 0
    assert manifest.read_bytes() == first_bytes
    assert sha256(first_bytes).hexdigest() == EXPECTED_BUILD_HASH
    assert payload["reproduction_claimed"] is False
    assert payload["planned_run"]["model_backends"] == ["minimax", "qwen", "glm"]
    assert {entry["planned_source"]["provider"] for entry in payload["entries"]} == {"harbor"}
    assert any(entry.get("notes") == "planned by operator preflight" for entry in payload["entries"])
    assert report.ok is True
    assert report_payload["reproduction_claimed"] is False


def test_capture_manifest_build_uses_templates_entry_overrides_and_installed_cli(tmp_path: Path) -> None:
    template = tmp_path / "harbor-template.json"
    cli_manifest = tmp_path / "cli-capture-manifest.json"
    harbor_template = dict(_class_shaped_payloads()["live_harbor_preflight_report"])
    harbor_template["harbor_version"] = "2.11.0-planned"
    template.write_text(stable_json_dumps(harbor_template) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "self_harness.cli",
            "capture-manifest",
            "build",
            *_base_build_args(cli_manifest),
            "--planned-artifact",
            f"live_harbor_preflight_report={template}",
            "--entry-source",
            "live_harbor_preflight_report:provider=harbor-canary",
            "--entry-note",
            "live_harbor_preflight_report=planned Harbor preflight template",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    harbor_entry = next(
        entry for entry in payload["entries"] if entry["required_artifact_class"] == "live_harbor_preflight_report"
    )
    report = verify_capture_manifest(cli_manifest, load_reproduction_requirements(REPO_ROOT / REQUIREMENTS))

    assert completed.returncode == 0
    assert harbor_entry["planned_artifact"]["harbor_version"] == "2.11.0-planned"
    assert harbor_entry["planned_source"]["provider"] == "harbor-canary"
    assert harbor_entry["planned_source"]["captured_after"] == "2026-06-24T00:00:00Z"
    assert harbor_entry["notes"] == "planned Harbor preflight template"
    assert report.ok is True


def test_capture_manifest_build_signs_and_verifies_round_trip(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    manifest = tmp_path / "signed-capture-manifest.json"
    _run_build(*_base_build_args(manifest))
    secret = "capture-build-passphrase"
    private_key, public_key = generate_keypair(passphrase=secret)
    private_path = tmp_path / "capture.ed25519"
    public_path = tmp_path / "capture.ed25519.pub"
    passphrase_path = tmp_path / "passphrase.txt"
    private_path.write_bytes(private_key)
    public_path.write_bytes(public_key)
    passphrase_path.write_text(secret + "\n", encoding="utf-8")

    local_signature = _run_sign(
        "--manifest",
        str(manifest),
        "--private-key",
        str(private_path),
        "--public-key",
        str(public_path),
        "--passphrase-file",
        str(passphrase_path),
        "--provider",
        "local-fixture",
        "--key-id",
        "capture-manifest-build-test",
    ).stdout.strip()
    local_verify = _run_verify(
        "--manifest",
        str(manifest),
        "--signature",
        local_signature,
        "--public-key",
        str(public_path),
        "--require-signature",
    )
    external_signature = _run_sign(
        "--manifest",
        str(manifest),
        "--external-signer",
        f"{sys.executable} {REPO_ROOT / FIXTURE_SIGNER}",
        "--provider",
        "fixture",
        "--out",
        str(tmp_path / "external.sig"),
    ).stdout.strip()
    external_verify = _run_verify("--manifest", str(manifest), "--signature", external_signature, "--require-signature")

    assert local_verify.returncode == 0
    assert external_verify.returncode == 0
    assert secret not in Path(local_signature).read_text(encoding="utf-8")


def test_capture_manifest_build_rejects_unsafe_inputs(tmp_path: Path) -> None:
    manifest = tmp_path / "unsafe-capture-manifest.json"
    template = tmp_path / "split.json"
    template.write_text(
        stable_json_dumps({"schema_version": "1.0", "mode": "live", "reproduction_claimed": False}) + "\n",
        encoding="utf-8",
    )
    claimed = tmp_path / "claimed.json"
    payload = dict(_class_shaped_payloads()["live_harbor_preflight_report"])
    payload["reproduction_claimed"] = True
    claimed.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")

    missing_backend = _run_build(*_base_build_args(manifest, model_backends=("minimax", "qwen")))
    non_live = _run_build(*_base_build_args(manifest), "--mode", "replay")
    wrong_protocol = _run_build(*_base_build_args(manifest), "--benchmark-protocol", "terminal-bench@1.0")
    inverted_window = _run_build(
        *_base_build_args(
            manifest,
            captured_after="2026-06-25T00:00:00Z",
            captured_before="2026-06-24T00:00:00Z",
        )
    )
    invalid_shape = _run_build(
        *_base_build_args(manifest),
        "--planned-artifact",
        f"live_terminal_bench_split_manifest={template}",
    )
    claimed_input = _run_build(
        *_base_build_args(manifest),
        "--planned-artifact",
        f"live_harbor_preflight_report={claimed}",
    )
    duplicate = _run_build(
        *_base_build_args(manifest),
        "--planned-artifact",
        f"live_harbor_preflight_report={claimed}",
        "--planned-artifact",
        f"live_harbor_preflight_report={claimed}",
    )
    unknown = _run_build(
        *_base_build_args(manifest),
        "--planned-artifact",
        f"unknown_artifact_class={claimed}",
    )

    assert missing_backend.returncode == 2
    assert "model_backends must cover" in missing_backend.stderr
    assert non_live.returncode == 2
    assert "mode must be live" in non_live.stderr
    assert wrong_protocol.returncode == 2
    assert "benchmark_protocol must be terminal-bench@2.0" in wrong_protocol.stderr
    assert inverted_window.returncode == 2
    assert "captured_after must not exceed captured_before" in inverted_window.stderr
    assert invalid_shape.returncode == 2
    assert "invalid planned artifact" in invalid_shape.stderr
    assert claimed_input.returncode == 2
    assert "reproduction_claimed=false" in claimed_input.stderr
    assert duplicate.returncode == 2
    assert "duplicate planned artifact class" in duplicate.stderr
    assert unknown.returncode == 2
    assert "unknown planned artifact class" in unknown.stderr


def _base_build_args(
    out: Path,
    *,
    model_backends: tuple[str, ...] = ("minimax", "qwen", "glm"),
    captured_after: str = "2026-06-24T00:00:00Z",
    captured_before: str = "2026-06-25T00:00:00Z",
    signing_provider: str = "local-fixture",
    signing_key_id: str = "capture-manifest-build-test",
) -> tuple[str, ...]:
    args = [
        "--manifest-id",
        "terminal-bench-2.0-capture-plan-001",
        "--bundle-id",
        "terminal-bench-2.0-operator-run-001",
        "--operator-label",
        "self-harness-tests",
        "--created-at",
        "2026-06-24T00:00:00Z",
        "--run-id",
        "terminal-bench-2.0-live-001",
    ]
    for backend in model_backends:
        args.extend(["--model-backend", backend])
    args.extend(
        [
            "--evaluator",
            "terminal-bench-verifier",
            "--tool-set",
            "minimal-terminal-tools",
            "--tool-budget-json",
            '{"max_tokens":8192,"max_tool_calls":100}',
            "--outbound-bandwidth-cap-bps",
            "2000000",
            "--mirrored-resource",
            "https://resources.example/terminal-bench",
            "--source-provider",
            "harbor",
            "--source-captured-after",
            captured_after,
            "--source-captured-before",
            captured_before,
            "--signing-provider",
            signing_provider,
            "--key-id",
            signing_key_id,
            "--out",
            str(out),
        ]
    )
    return tuple(args)


def _run_build(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_sign(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SIGN_SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_verify(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
