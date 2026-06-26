import json
import subprocess
import sys
from pathlib import Path

from self_harness.harbor_discovery import (
    HarborDiscoveryCommand,
    build_harbor_discovery_request,
    parse_harbor_artifact_response,
    run_harbor_discovery,
)
from self_harness.image_policy import evaluate_image_policy, load_image_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
HARBOR_DISCOVERY = REPO_ROOT / "scripts" / "harbor_discovery.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "harbor"
VALID_DIGEST = "sha256:" + "e" * 64


def test_harbor_discovery_request_construction_redacts_auth_in_cli() -> None:
    command = HarborDiscoveryCommand(
        url="https://harbor.example",
        project="terminal-bench",
        repository="agents/verifier",
        reference="stable",
        authorization_header="Bearer secret-token",
    )

    request = build_harbor_discovery_request(command)

    assert request.method == "GET"
    assert "/api/v2.0/projects/terminal-bench/repositories/agents%2Fverifier/artifacts/stable?" in request.url
    assert ("Authorization", "Bearer secret-token") in request.headers


def test_harbor_discovery_replay_parses_valid_fixture() -> None:
    result = run_harbor_discovery(
        HarborDiscoveryCommand(
            url="https://harbor.example",
            project="terminal-bench",
            repository="agents/verifier",
            reference="stable",
        ),
        replay_response=FIXTURES / "harbor_artifact_valid.json",
    )

    assert result.ok
    assert result.discovered_images[0].image == "harbor.example/terminal-bench/agents/verifier"
    assert result.discovered_images[0].digest == VALID_DIGEST
    assert result.discovered_images[0].tags == ("1.0.0", "stable")
    assert result.discovered_images[0].child_digests == ("sha256:" + "f" * 64,)


def test_harbor_discovery_malformed_fixtures_fail_closed() -> None:
    missing = run_harbor_discovery(
        HarborDiscoveryCommand(
            url="https://harbor.example",
            project="terminal-bench",
            repository="agents/verifier",
            reference="stable",
        ),
        replay_response=FIXTURES / "harbor_artifact_missing_digest.json",
    )
    malformed = run_harbor_discovery(
        HarborDiscoveryCommand(
            url="https://harbor.example",
            project="terminal-bench",
            repository="agents/verifier",
            reference="stable",
        ),
        replay_response=FIXTURES / "harbor_artifact_malformed.json",
    )

    assert not missing.ok
    assert "digest" in (missing.reason or "")
    assert not malformed.ok
    assert "invalid Harbor artifact JSON" in (malformed.reason or "")


def test_harbor_discovery_digest_binds_to_image_policy(tmp_path: Path) -> None:
    images = parse_harbor_artifact_response(
        (FIXTURES / "harbor_artifact_valid.json").read_text(encoding="utf-8"),
        image="harbor.example/terminal-bench/agents/verifier",
        reference="stable",
    )
    policy = load_image_policy(_write_image_policy(tmp_path / "image-policy.json", images[0].image, VALID_DIGEST))

    decision = evaluate_image_policy(policy, images[0].image, images[0].digest, require_digest=True)

    assert decision.allowed
    assert decision.code == "allowed"


def test_harbor_discovery_live_requires_auth() -> None:
    result = run_harbor_discovery(
        HarborDiscoveryCommand(
            url="https://harbor.example",
            project="terminal-bench",
            repository="agents/verifier",
            reference="stable",
        )
    )

    assert not result.ok
    assert result.mode == "live"
    assert "authorization" in (result.reason or "")


def test_harbor_discovery_cli_dry_run_redacts_auth(tmp_path: Path) -> None:
    completed = _run_cli(
        "--url",
        "https://harbor.example",
        "--project",
        "terminal-bench",
        "--repository",
        "agents/verifier",
        "--reference",
        "stable",
        "--dry-run",
    )
    report = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert report["ok"] is True
    assert report["request"]["method"] == "GET"
    assert "agents%2Fverifier" in report["request"]["url"]
    assert report["discovered_images"] == []


def test_harbor_discovery_cli_replay(tmp_path: Path) -> None:
    completed = _run_cli(
        "--url",
        "https://harbor.example",
        "--project",
        "terminal-bench",
        "--repository",
        "agents/verifier",
        "--reference",
        "stable",
        "--replay",
        str(FIXTURES / "harbor_artifact_valid.json"),
    )
    report = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert report["ok"] is True
    assert report["discovered_images"][0]["digest"] == VALID_DIGEST


def _write_image_policy(path: Path, image: str, digest: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "policy_version": "1",
                "entries": [
                    {
                        "image": image,
                        "digest": digest,
                        "status": "active",
                        "labels": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARBOR_DISCOVERY), *args],
        text=True,
        capture_output=True,
        check=False,
    )
