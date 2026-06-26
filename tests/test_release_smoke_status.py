import json
import re
import subprocess
from pathlib import Path

import pytest
from scripts import release_smoke


def test_release_smoke_writes_success_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    out = tmp_path / "release-smoke.json"
    _install_fast_smoke(monkeypatch, canonical_hash="abc123")

    exit_code = release_smoke.main(_args(repo, out))
    payload = json.loads(out.read_text(encoding="utf-8"))
    check_names = {check["name"] for check in payload["checks"]}

    assert exit_code == 0
    assert payload["schema_version"] == "1.0"
    assert payload["ok"] is True
    assert payload["reproduction_claimed"] is False
    assert "not validate PyPI trusted publishing" in payload["boundary"]
    assert re.fullmatch(r"[0-9a-f]{64}", payload["report_hash"])
    assert {"wheel_path", "provenance_verify", "wheel_install", "canonical_audit_hash_compare"} <= check_names
    assert {check["status"] for check in payload["checks"]} == {"pass"}
    assert {check["required"] for check in payload["checks"]} == {True}


def test_release_smoke_writes_failure_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    out = tmp_path / "release-smoke.json"
    _install_fast_smoke(monkeypatch, canonical_hash="wrong")

    exit_code = release_smoke.main(_args(repo, out))
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["reproduction_claimed"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", payload["report_hash"])
    assert payload["checks"][-1]["name"] == "canonical_audit_hash_compare"
    assert payload["checks"][-1]["status"] == "fail"
    assert "canonical audit hash mismatch" in payload["checks"][-1]["detail"]


def test_release_smoke_writes_failure_status_for_missing_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = tmp_path / "release-smoke.json"
    args = _args(repo, out)
    args[args.index("--provenance") + 1] = str(repo / "dist" / "missing-provenance.json")

    exit_code = release_smoke.main(args)
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["checks"][-1]["name"] == "provenance_path"
    assert payload["checks"][-1]["status"] == "fail"
    assert "does not exist" in payload["checks"][-1]["detail"]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    dist = repo / "dist"
    fixtures = repo / "tests" / "fixtures"
    dist.mkdir(parents=True)
    fixtures.mkdir(parents=True)
    (dist / "self_harness-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "self_harness-0.1.0.tar.gz").write_bytes(b"sdist")
    (dist / "self-harness-0.1.0-provenance.json").write_text("{}\n", encoding="utf-8")
    (fixtures / "canonical_audit_hash.txt").write_text("abc123\n", encoding="utf-8")
    return repo


def _args(repo: Path, out: Path) -> list[str]:
    return [
        "--wheel",
        str(repo / "dist" / "self_harness-0.1.0-py3-none-any.whl"),
        "--sdist",
        str(repo / "dist" / "self_harness-0.1.0.tar.gz"),
        "--provenance",
        str(repo / "dist" / "self-harness-0.1.0-provenance.json"),
        "--repo-root",
        str(repo),
        "--out",
        str(out),
    ]


def _install_fast_smoke(monkeypatch: pytest.MonkeyPatch, *, canonical_hash: str) -> None:
    def fake_run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        if command[1:3] == ["-m", "venv"]:
            Path(command[3]).mkdir(parents=True)
            stdout = ""
        elif "inspect-harness" in command:
            stdout = '{"schema_version":"1.0"}\n'
        elif "audit-summary" in command:
            stdout = '{"reproduction_claimed":false}\n'
        elif any("audit_tree_hash" in part for part in command):
            stdout = canonical_hash + "\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(release_smoke, "_run", fake_run)
    monkeypatch.setattr(release_smoke, "_verify_provenance", lambda *args: None)
    monkeypatch.setattr(release_smoke, "_verify_provenance_signature", lambda *args: None)
