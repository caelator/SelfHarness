from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = SCRIPT_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.types import stable_json_dumps  # noqa: E402

RELEASE_SMOKE_SCHEMA_VERSION = "1.0"
RELEASE_SMOKE_BOUNDARY = (
    "release smoke status is offline installability and artifact-parity evidence only; "
    "it does not validate PyPI trusted publishing, contact PyPI/TestPyPI, run Sigstore, "
    "or claim Terminal-Bench benchmark reproduction"
)
DEFAULT_STATUS_PATH = Path("dist/self-harness-release-smoke.json")
T = TypeVar("T")


class ReleaseSmokeStepError(RuntimeError):
    """Raised after a release-smoke step records a failed status check."""


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    out = _resolve_out(repo_root, args.out)
    checks: list[dict[str, object]] = []

    try:
        wheel = _record(
            checks,
            "wheel_path",
            "wheel path points to one built .whl file",
            lambda: _resolve_wheel(args.wheel),
        )
        _record(
            checks,
            "sdist_path",
            "source distribution path is absent or points to an existing file",
            lambda: _validate_sdist(args.sdist),
        )
        provenance = _record(
            checks,
            "provenance_path",
            "release provenance manifest is selected",
            lambda: _resolve_provenance(repo_root, args.provenance),
        )
        expected_hash = _record(
            checks,
            "canonical_audit_hash_fixture",
            "canonical audit hash fixture is readable",
            lambda: (repo_root / "tests" / "fixtures" / "canonical_audit_hash.txt").read_text(
                encoding="utf-8"
            ).strip(),
        )
        _record(
            checks,
            "provenance_verify",
            "release provenance manifest verifies against local artifacts",
            lambda: _verify_provenance(repo_root, provenance),
        )
        _record(
            checks,
            "provenance_signature_verify",
            "release provenance signature verifies when supplied",
            lambda: _verify_provenance_signature(
                repo_root,
                provenance,
                args.provenance_signature,
                args.provenance_public_key,
            ),
        )
        with tempfile.TemporaryDirectory(prefix="self-harness-release-smoke-") as tmp:
            tmp_path = Path(tmp)
            venv_dir = tmp_path / "venv"
            demo_dir = tmp_path / "demo"
            clean_env = _clean_env()
            _record(
                checks,
                "venv_create",
                "isolated virtual environment is created",
                lambda: _create_venv(venv_dir, clean_env),
            )
            python = _venv_python(venv_dir)
            cli = _venv_executable(venv_dir, "self-harness")

            _record(
                checks,
                "pip_upgrade",
                "pip upgrades inside the isolated virtual environment",
                lambda: _run([str(python), "-m", "pip", "install", "--upgrade", "pip"], env=clean_env),
            )
            _record(
                checks,
                "wheel_install",
                "built wheel installs inside the isolated virtual environment",
                lambda: _run([str(python), "-m", "pip", "install", str(wheel)], env=clean_env),
            )
            _record(
                checks,
                "installed_imports",
                "installed public API imports from the isolated virtual environment",
                lambda: _run(
                    [
                        str(python),
                        "-c",
                        (
                            "import self_harness; "
                            "from self_harness import EngineConfig, SelfHarnessEngine, audit_tree_hash"
                        ),
                    ],
                    env=clean_env,
                ),
            )
            _record(
                checks,
                "installed_demo",
                "installed CLI demo completes",
                lambda: _run(
                    [
                        str(cli),
                        "demo",
                        "--rounds",
                        "1",
                        "--seed",
                        "0",
                        "--out",
                        str(demo_dir),
                    ],
                    env=clean_env,
                ),
            )
            _record(
                checks,
                "installed_audit_trajectory",
                "installed CLI reads the generated audit trajectory",
                lambda: _run([str(cli), "audit-trajectory", str(demo_dir)], env=clean_env),
            )
            _record(
                checks,
                "installed_inspect_harness_schema",
                "installed inspect-harness CLI returns schema_version 1.0",
                lambda: _assert_inspect_harness_schema(cli, demo_dir, clean_env),
            )
            _record(
                checks,
                "installed_audit_summary_boundary",
                "installed audit-summary CLI does not claim benchmark reproduction",
                lambda: _assert_audit_summary_boundary(cli, demo_dir, clean_env),
            )
            _record(
                checks,
                "canonical_audit_hash_compare",
                "installed package reproduces the canonical Figure 3 audit hash",
                lambda: _assert_canonical_audit_hash(python, tmp_path / "canonical", clean_env, expected_hash),
            )
    except ReleaseSmokeStepError:
        payload = _status_payload(ok=False, checks=checks)
        _write_status(out, payload)
        print(stable_json_dumps(payload), file=sys.stderr)
        return 2

    payload = _status_payload(ok=True, checks=checks)
    _write_status(out, payload)
    print(stable_json_dumps(payload))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test a built Self-Harness wheel in an isolated venv.")
    parser.add_argument("--wheel", type=Path, required=True, help="Path to the built .whl file.")
    parser.add_argument("--repo-root", type=Path, required=True, help="Repository root containing canonical fixtures.")
    parser.add_argument("--sdist", type=Path, help="Optional source distribution path; checked for existence only.")
    parser.add_argument("--provenance", type=Path, help="Release provenance manifest path.")
    parser.add_argument("--provenance-signature", type=Path, help="Optional provenance signature sidecar path.")
    parser.add_argument(
        "--provenance-public-key",
        help="Trusted provenance signing public key path, raw bytes, or base64 raw bytes.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_STATUS_PATH,
        help="Path for the deterministic release-smoke status JSON.",
    )
    return parser


def _venv_python(venv_dir: Path) -> Path:
    return _venv_executable(venv_dir, "python")


def _venv_executable(venv_dir: Path, name: str) -> Path:
    if os.name == "nt":
        exe = venv_dir / "Scripts" / f"{name}.exe"
        if exe.exists():
            return exe
        return venv_dir / "Scripts" / name
    return venv_dir / "bin" / name


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["LANG"] = "C.UTF-8"
    env["TZ"] = "UTC"
    return env


def _create_venv(venv_dir: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, "-m", "venv", str(venv_dir)], env=env)


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(_command_error(completed))
    return completed


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
        raise ReleaseSmokeStepError(str(exc)) from exc
    checks.append(_check(name=name, status="pass", detail=detail))
    return result


def _check(*, name: str, status: str, detail: str) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "required": True,
    }


def _status_payload(*, ok: bool, checks: list[dict[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": RELEASE_SMOKE_SCHEMA_VERSION,
        "ok": ok,
        "checks": checks,
        "reproduction_claimed": False,
        "boundary": RELEASE_SMOKE_BOUNDARY,
    }
    payload["report_hash"] = sha256((stable_json_dumps(payload) + "\n").encode("utf-8")).hexdigest()
    return payload


def _write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _resolve_out(repo_root: Path, out: Path) -> Path:
    if out.is_absolute():
        return out
    return repo_root / out


def _resolve_wheel(path: Path) -> Path:
    wheel = path.resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError(f"wheel path must point to one .whl file: {wheel}")
    return wheel


def _validate_sdist(path: Path | None) -> Path | None:
    if path is None:
        return None
    sdist = path.resolve()
    if not sdist.is_file():
        raise ValueError(f"sdist path does not exist: {sdist}")
    return sdist


def _assert_inspect_harness_schema(cli: Path, demo_dir: Path, env: dict[str, str]) -> None:
    harness_inspection = _run([str(cli), "inspect-harness", str(demo_dir), "--json"], env=env).stdout.strip()
    if json.loads(harness_inspection).get("schema_version") != "1.0":
        raise ValueError("installed inspect-harness CLI returned an unexpected schema_version")


def _assert_audit_summary_boundary(cli: Path, demo_dir: Path, env: dict[str, str]) -> None:
    summary = _run([str(cli), "audit-summary", str(demo_dir)], env=env).stdout.strip()
    if json.loads(summary).get("reproduction_claimed") is True:
        raise ValueError("release smoke demo unexpectedly claimed reproduction")


def _assert_canonical_audit_hash(
    python: Path,
    canonical_dir: Path,
    env: dict[str, str],
    expected_hash: str,
) -> None:
    actual_hash = _run(
        [
            str(python),
            "-c",
            (
                "from pathlib import Path; "
                "from self_harness import EngineConfig, SelfHarnessEngine, audit_tree_hash, "
                "figure_3_harness, write_audit_trajectory; "
                "from self_harness.demo import DeterministicRunner, demo_tasks; "
                "from self_harness.proposer import HeuristicProposer; "
                f"out_dir = Path({str(canonical_dir)!r}); "
                "engine = SelfHarnessEngine("
                "tasks=demo_tasks(), "
                "runner=DeterministicRunner(seed=0), "
                "proposer=HeuristicProposer(), "
                "out_dir=out_dir, "
                "config=EngineConfig(rounds=1, seed=0), "
                "initial_spec=figure_3_harness(), "
                "); "
                "engine.run(); "
                "write_audit_trajectory(out_dir); "
                "print(audit_tree_hash(out_dir))"
            ),
        ],
        env=env,
    ).stdout.strip()
    if actual_hash != expected_hash:
        raise ValueError(f"canonical audit hash mismatch: expected {expected_hash}, got {actual_hash}")


def _command_error(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "").strip()
    if detail:
        detail = detail.splitlines()[-1]
        return f"command exited {completed.returncode}: {detail[:500]}"
    return f"command exited {completed.returncode}"


def _resolve_provenance(repo_root: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        provenance = explicit.resolve()
        if not provenance.is_file():
            raise ValueError(f"provenance path does not exist: {provenance}")
        return provenance
    candidates = sorted((repo_root / "dist").glob("*-provenance.json"))
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one dist/*-provenance.json file, found {len(candidates)}")
    return candidates[0].resolve()


def _verify_provenance(repo_root: Path, provenance: Path) -> None:
    _run(
        [
            sys.executable,
            str(repo_root / "scripts" / "verify_provenance.py"),
            "--manifest",
            str(provenance),
            "--repo-root",
            str(repo_root),
        ],
        env=_clean_env(),
    )


def _verify_provenance_signature(
    repo_root: Path,
    provenance: Path,
    explicit_signature: Path | None,
    public_key: str | None,
) -> None:
    signature = (
        explicit_signature.resolve()
        if explicit_signature is not None
        else provenance.with_name(provenance.name + ".sig")
    )
    if explicit_signature is None and not signature.exists():
        return
    if not signature.is_file():
        raise ValueError(f"provenance signature path does not exist: {signature}")
    command = [
        sys.executable,
        str(repo_root / "scripts" / "verify_provenance_signature.py"),
        "--manifest",
        str(provenance),
        "--signature",
        str(signature),
    ]
    if public_key is not None:
        command.extend(["--public-key", public_key])
    _run(command, env=_clean_env())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
