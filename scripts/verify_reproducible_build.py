from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.types import stable_json_dumps  # noqa: E402

SCHEMA_VERSION = "1.0"
ARTIFACT_CLASS = "reproducible_build"
DEFAULT_OUT = Path("dist/self-harness-reproducible-build.json")
DEFAULT_SOURCE_DATE_EPOCH = "315532800"
BUILD_MODE = "pip-wheel-no-index-no-deps-no-build-isolation"
BOUNDARY = (
    "sdist-to-wheel reproducible build evidence only; does not contact PyPI or TestPyPI, "
    "does not validate trusted publishing or provenance signing, and does not claim "
    "Terminal-Bench benchmark reproduction"
)
T = TypeVar("T")


class ReproducibleBuildError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    out = _resolve_out(repo_root, args.out)
    checks: list[dict[str, object]] = []

    try:
        sdist = _record(
            checks,
            "sdist_path",
            "source distribution path points to one .tar.gz file",
            lambda: _resolve_sdist(args.sdist),
        )
        published_wheel = _record(
            checks,
            "published_wheel_path",
            "published wheel path points to one .whl file",
            lambda: _resolve_wheel(args.wheel),
        )
        sdist_digest = _file_digest(sdist)
        published_digest = _file_digest(published_wheel)
        source_date_epoch = _source_date_epoch(args.source_date_epoch)
        env = _build_env(source_date_epoch)
        with tempfile.TemporaryDirectory(prefix="self-harness-reproducible-build-") as tmp:
            wheelhouse = Path(tmp) / "wheelhouse"
            rebuilt_wheel = _record(
                checks,
                "sdist_wheel_rebuild",
                "source distribution rebuilds to exactly one wheel without build isolation",
                lambda: _build_wheel_from_sdist(
                    python=args.python,
                    sdist=sdist,
                    wheelhouse=wheelhouse,
                    env=env,
                ),
            )
            rebuilt_digest = _file_digest(rebuilt_wheel)
            filename_match = published_wheel.name == rebuilt_wheel.name
            digest_match = published_digest["sha256"] == rebuilt_digest["sha256"]
            _append_comparison_check(checks, "wheel_filename_match", filename_match)
            _append_comparison_check(checks, "wheel_sha256_match", digest_match)
            payload = _status_payload(
                ok=filename_match and digest_match,
                checks=checks,
                sdist=sdist,
                sdist_digest=sdist_digest,
                published_wheel=published_wheel,
                published_digest=published_digest,
                rebuilt_wheel=rebuilt_wheel,
                rebuilt_digest=rebuilt_digest,
                source_date_epoch=source_date_epoch,
            )
    except ReproducibleBuildError as exc:
        payload = _error_payload(checks=checks, error=str(exc), source_date_epoch=args.source_date_epoch)
        _write_status(out, payload)
        print(stable_json_dumps(payload), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        payload = _error_payload(checks=checks, error=str(exc), source_date_epoch=args.source_date_epoch)
        _write_status(out, payload)
        print(stable_json_dumps(payload), file=sys.stderr)
        return 3

    _write_status(out, payload)
    output = stable_json_dumps(payload)
    if payload["ok"] is True:
        print(output)
        return 0
    print(output, file=sys.stderr)
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify that rebuilding a wheel from the release sdist is byte-reproducible."
    )
    parser.add_argument("--sdist", type=Path, required=True, help="Path to the built .tar.gz source distribution.")
    parser.add_argument("--wheel", type=Path, required=True, help="Path to the built .whl distribution.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Repository root used to resolve relative output.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable whose pip will rebuild the wheel from the sdist.",
    )
    parser.add_argument(
        "--source-date-epoch",
        default=os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_SOURCE_DATE_EPOCH),
        help="SOURCE_DATE_EPOCH used for the rebuild; defaults to the project reproducible-build epoch.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Path for the deterministic status JSON.")
    return parser


def _record(
    checks: list[dict[str, object]],
    name: str,
    detail: str,
    action: Callable[[], T],
) -> T:
    try:
        result = action()
    except Exception as exc:
        checks.append(_check(name=name, status="fail", detail=f"{detail}: {exc}"))
        raise
    checks.append(_check(name=name, status="pass", detail=detail))
    return result


def _append_comparison_check(checks: list[dict[str, object]], name: str, matched: bool) -> None:
    checks.append(
        _check(
            name=name,
            status="pass" if matched else "fail",
            detail="rebuilt wheel matches the published wheel" if matched else "rebuilt wheel differs",
        )
    )


def _check(*, name: str, status: str, detail: str) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "required": True,
    }


def _status_payload(
    *,
    ok: bool,
    checks: list[dict[str, object]],
    sdist: Path,
    sdist_digest: dict[str, object],
    published_wheel: Path,
    published_digest: dict[str, object],
    rebuilt_wheel: Path,
    rebuilt_digest: dict[str, object],
    source_date_epoch: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_class": ARTIFACT_CLASS,
        "ok": ok,
        "checks": checks,
        "sdist": _artifact_summary(sdist, sdist_digest),
        "published_wheel": _artifact_summary(published_wheel, published_digest),
        "rebuilt_wheel": _artifact_summary(rebuilt_wheel, rebuilt_digest),
        "build": {
            "mode": BUILD_MODE,
            "source_date_epoch": source_date_epoch,
            "network_contact": False,
        },
        "reproduction_claimed": False,
        "boundary": BOUNDARY,
    }
    payload["report_hash"] = _report_hash(payload)
    return payload


def _error_payload(*, checks: list[dict[str, object]], error: str, source_date_epoch: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_class": ARTIFACT_CLASS,
        "ok": False,
        "checks": checks,
        "error": error,
        "build": {
            "mode": BUILD_MODE,
            "source_date_epoch": source_date_epoch,
            "network_contact": False,
        },
        "reproduction_claimed": False,
        "boundary": BOUNDARY,
    }
    payload["report_hash"] = _report_hash(payload)
    return payload


def _artifact_summary(path: Path, digest: dict[str, object]) -> dict[str, object]:
    return {
        "filename": path.name,
        "sha256": digest["sha256"],
        "bytes": digest["bytes"],
    }


def _report_hash(payload: dict[str, object]) -> str:
    without_hash = {key: value for key, value in payload.items() if key != "report_hash"}
    return sha256((stable_json_dumps(without_hash) + "\n").encode("utf-8")).hexdigest()


def _write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _resolve_out(repo_root: Path, out: Path) -> Path:
    if out.is_absolute():
        return out
    return repo_root / out


def _resolve_sdist(path: Path) -> Path:
    sdist = path.resolve()
    if not sdist.is_file() or "".join(sdist.suffixes[-2:]) != ".tar.gz":
        raise ReproducibleBuildError(f"sdist path must point to one .tar.gz file: {sdist}")
    return sdist


def _resolve_wheel(path: Path) -> Path:
    wheel = path.resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ReproducibleBuildError(f"wheel path must point to one .whl file: {wheel}")
    return wheel


def _source_date_epoch(value: str) -> str:
    if not value.isdecimal():
        raise ReproducibleBuildError("SOURCE_DATE_EPOCH must be a non-negative integer")
    return value


def _build_env(source_date_epoch: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["LANG"] = "C.UTF-8"
    env["TZ"] = "UTC"
    env["PYTHONHASHSEED"] = "0"
    env["SOURCE_DATE_EPOCH"] = source_date_epoch
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def _build_wheel_from_sdist(*, python: str, sdist: Path, wheelhouse: Path, env: dict[str, str]) -> Path:
    wheelhouse.mkdir(parents=True, exist_ok=True)
    command = [
        python,
        "-m",
        "pip",
        "wheel",
        "--no-index",
        "--no-deps",
        "--no-build-isolation",
        "--wheel-dir",
        str(wheelhouse),
        str(sdist),
    ]
    completed = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise ReproducibleBuildError(_command_error(completed))
    wheels = sorted(wheelhouse.glob("*.whl"))
    if len(wheels) != 1:
        raise ReproducibleBuildError(f"expected exactly one rebuilt wheel, found {len(wheels)}")
    return wheels[0].resolve()


def _file_digest(path: Path) -> dict[str, object]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReproducibleBuildError(str(exc)) from exc
    return {
        "sha256": sha256(data).hexdigest(),
        "bytes": len(data),
    }


def _command_error(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "").strip()
    if detail:
        return f"wheel rebuild exited {completed.returncode}: {detail.splitlines()[-1][:500]}"
    return f"wheel rebuild exited {completed.returncode}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
