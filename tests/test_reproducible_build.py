import json
import re
from pathlib import Path

import pytest
from scripts import verify_reproducible_build


def test_reproducible_build_accepts_matching_rebuilt_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sdist, wheel = _package_files(tmp_path, wheel_bytes=b"same-wheel")
    out = tmp_path / "reproducible-build.json"

    def fake_build_wheel_from_sdist(**kwargs: object) -> Path:
        wheelhouse = kwargs["wheelhouse"]
        assert isinstance(wheelhouse, Path)
        wheelhouse.mkdir(parents=True)
        rebuilt = wheelhouse / wheel.name
        rebuilt.write_bytes(b"same-wheel")
        return rebuilt

    monkeypatch.setattr(verify_reproducible_build, "_build_wheel_from_sdist", fake_build_wheel_from_sdist)

    exit_code = verify_reproducible_build.main(_args(repo, sdist, wheel, out))
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["schema_version"] == "1.0"
    assert payload["artifact_class"] == "reproducible_build"
    assert payload["ok"] is True
    assert payload["reproduction_claimed"] is False
    assert payload["build"]["network_contact"] is False
    assert payload["published_wheel"]["sha256"] == payload["rebuilt_wheel"]["sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", payload["report_hash"])
    assert {check["status"] for check in payload["checks"]} == {"pass"}


def test_reproducible_build_reports_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sdist, wheel = _package_files(tmp_path, wheel_bytes=b"published-wheel")
    out = tmp_path / "reproducible-build.json"

    def fake_build_wheel_from_sdist(**kwargs: object) -> Path:
        wheelhouse = kwargs["wheelhouse"]
        assert isinstance(wheelhouse, Path)
        wheelhouse.mkdir(parents=True)
        rebuilt = wheelhouse / wheel.name
        rebuilt.write_bytes(b"rebuilt-wheel")
        return rebuilt

    monkeypatch.setattr(verify_reproducible_build, "_build_wheel_from_sdist", fake_build_wheel_from_sdist)

    exit_code = verify_reproducible_build.main(_args(repo, sdist, wheel, out))
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["checks"][-1]["name"] == "wheel_sha256_match"
    assert payload["checks"][-1]["status"] == "fail"
    assert payload["published_wheel"]["sha256"] != payload["rebuilt_wheel"]["sha256"]


def test_reproducible_build_reports_corrupt_input(tmp_path: Path) -> None:
    repo, _sdist, wheel = _package_files(tmp_path)
    out = tmp_path / "reproducible-build.json"

    exit_code = verify_reproducible_build.main(_args(repo, repo / "dist" / "missing.tar.gz", wheel, out))
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert exit_code == 3
    assert payload["ok"] is False
    assert payload["checks"][-1]["name"] == "sdist_path"
    assert payload["checks"][-1]["status"] == "fail"
    assert "sdist path must point" in payload["error"]
    assert re.fullmatch(r"[0-9a-f]{64}", payload["report_hash"])


def test_reproducible_build_report_hash_is_deterministic(tmp_path: Path) -> None:
    repo, sdist, wheel = _package_files(tmp_path)
    digest = {"sha256": "a" * 64, "bytes": 4}
    checks = [{"name": "example", "status": "pass", "detail": "ok", "required": True}]

    first = verify_reproducible_build._status_payload(
        ok=True,
        checks=checks,
        sdist=sdist,
        sdist_digest=digest,
        published_wheel=wheel,
        published_digest=digest,
        rebuilt_wheel=wheel,
        rebuilt_digest=digest,
        source_date_epoch="315532800",
    )
    second = verify_reproducible_build._status_payload(
        ok=True,
        checks=checks,
        sdist=sdist,
        sdist_digest=digest,
        published_wheel=wheel,
        published_digest=digest,
        rebuilt_wheel=wheel,
        rebuilt_digest=digest,
        source_date_epoch="315532800",
    )

    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first["report_hash"])


def _package_files(
    tmp_path: Path,
    *,
    wheel_bytes: bytes = b"wheel",
) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    sdist = dist / "self_harness-0.1.0.tar.gz"
    wheel = dist / "self_harness-0.1.0-py3-none-any.whl"
    sdist.write_bytes(b"sdist")
    wheel.write_bytes(wheel_bytes)
    return repo, sdist, wheel


def _args(repo: Path, sdist: Path, wheel: Path, out: Path) -> list[str]:
    return [
        "--sdist",
        str(sdist),
        "--wheel",
        str(wheel),
        "--repo-root",
        str(repo),
        "--source-date-epoch",
        "315532800",
        "--out",
        str(out),
    ]
