from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.corpus_signing import public_key_fingerprint  # noqa: E402
from self_harness.freshness_policy import FreshnessPolicyError, load_freshness_policy  # noqa: E402
from self_harness.harbor_discovery import (  # noqa: E402
    HarborDiscoveryCommand,
    HarborDiscoveryError,
    harbor_discovery_result_to_jsonable,
    run_harbor_discovery,
)
from self_harness.image_policy import ImagePolicyError, load_image_policy  # noqa: E402
from self_harness.operator_bundle import (  # noqa: E402
    OperatorPolicyBundle,
    OperatorPolicyBundleError,
    load_operator_policy_bundle,
    operator_policy_bundle_to_jsonable,
)
from self_harness.scanner_db_freshness import (  # noqa: E402
    ScannerDbFreshnessError,
    load_scanner_db_freshness_policy,
)
from self_harness.scanner_db_update import (  # noqa: E402
    ScannerDbUpdateCommand,
    ScannerDbUpdateError,
    build_trivy_db_update_command,
)
from self_harness.scanner_execution import ScannerCommand, ScannerExecutionError, build_trivy_command  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402
from self_harness.vulnerability_policy import VulnerabilityPolicyError, load_vulnerability_policy  # noqa: E402

OPERATOR_PREFLIGHT_SCHEMA_VERSION = "1.0"
DEFAULT_IMAGE = "registry.example/trusted/verifier:1"
DEFAULT_DIGEST = "sha256:" + "c" * 64
BOUNDARY = (
    "release/operator offline preflight; checks policy wiring and deterministic command construction "
    "without contacting Harbor, Docker, registries, scanners, PyPI, Sigstore, or cloud providers; "
    "not benchmark reproduction evidence"
)


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str
    detail: str
    required: bool = True
    metadata: dict[str, object] | None = None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    checks: list[PreflightCheck] = []
    bundle: OperatorPolicyBundle | None = None
    try:
        bundle = load_operator_policy_bundle(args.bundle, today=_parse_today(args.today))
    except OperatorPolicyBundleError as exc:
        checks.append(_fail("bundle", str(exc)))
    if bundle is not None:
        checks.extend(_policy_checks(bundle))
        checks.extend(_trusted_public_key_checks(bundle))
    checks.append(
        _scanner_command_check(
            image=args.image,
            digest=args.digest,
            output_path=args.scan_out,
            db_registry_config_path=args.db_registry_config,
            trivy_binary=args.trivy_binary,
        )
    )
    checks.append(
        _scanner_db_update_check(
            cache_dir=args.scanner_db_cache_dir,
            db_registry_config_path=args.db_registry_config,
            trivy_binary=args.trivy_binary,
        )
    )
    checks.append(_registry_config_path_check(args.db_registry_config))
    checks.append(
        _harbor_discovery_check(
            url=args.harbor_url,
            project=args.harbor_project,
            repository=args.harbor_repository,
            reference=args.harbor_reference,
            replay=args.harbor_replay,
        )
    )
    ok = all(check.status == "pass" for check in checks if check.required)
    report = {
        "schema_version": OPERATOR_PREFLIGHT_SCHEMA_VERSION,
        "ok": ok,
        "bundle": operator_policy_bundle_to_jsonable(bundle) if bundle is not None else None,
        "checks": [_check_to_jsonable(check) for check in checks],
        "boundary": BOUNDARY,
    }
    output = stable_json_dumps(report) + "\n"
    if args.result_out is not None:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if ok else 2


def _policy_checks(bundle: OperatorPolicyBundle) -> list[PreflightCheck]:
    return [
        _optional_loader_check("image_policy", bundle.image_policy, load_image_policy),
        _optional_loader_check("freshness_policy", bundle.freshness_policy, load_freshness_policy),
        _optional_loader_check("vulnerability_policy", bundle.vulnerability_policy, load_vulnerability_policy),
        _optional_loader_check(
            "scanner_db_freshness_policy",
            bundle.scanner_db_freshness_policy,
            load_scanner_db_freshness_policy,
        ),
    ]


def _optional_loader_check(
    name: str,
    path: Path | None,
    loader: Callable[[Path], object],
) -> PreflightCheck:
    if path is None:
        return PreflightCheck(name=name, status="skipped", detail="not referenced by bundle", required=False)
    try:
        loader(path)
    except (
        OSError,
        ImagePolicyError,
        FreshnessPolicyError,
        VulnerabilityPolicyError,
        ScannerDbFreshnessError,
    ) as exc:
        return _fail(name, str(exc), metadata={"path": str(path)})
    return _pass(name, "loaded", metadata={"path": str(path)})


def _trusted_public_key_checks(bundle: OperatorPolicyBundle) -> list[PreflightCheck]:
    if not bundle.trusted_public_keys:
        return [
            PreflightCheck(
                name="trusted_public_keys",
                status="skipped",
                detail="not referenced by bundle",
                required=False,
            )
        ]
    checks: list[PreflightCheck] = []
    for index, path in enumerate(bundle.trusted_public_keys):
        try:
            fingerprint = public_key_fingerprint(path)
        except Exception as exc:
            checks.append(_fail(f"trusted_public_key_{index}", str(exc), metadata={"path": str(path)}))
            continue
        checks.append(
            _pass(
                f"trusted_public_key_{index}",
                "loaded",
                metadata={"path": str(path), "fingerprint": fingerprint},
            )
        )
    return checks


def _scanner_command_check(
    *,
    image: str,
    digest: str | None,
    output_path: Path,
    db_registry_config_path: Path | None,
    trivy_binary: str,
) -> PreflightCheck:
    try:
        command = build_trivy_command(
            ScannerCommand(
                image=image,
                digest=digest,
                output_path=output_path,
                db_registry_config_path=db_registry_config_path,
            ),
            trivy_binary=trivy_binary,
        )
    except ScannerExecutionError as exc:
        return _fail("scanner_dry_run_command", str(exc))
    return _pass("scanner_dry_run_command", "constructed", metadata={"command": command})


def _scanner_db_update_check(
    *,
    cache_dir: Path,
    db_registry_config_path: Path | None,
    trivy_binary: str,
) -> PreflightCheck:
    try:
        command = build_trivy_db_update_command(
            ScannerDbUpdateCommand(cache_dir=cache_dir, db_registry_config_path=db_registry_config_path),
            trivy_binary=trivy_binary,
        )
    except ScannerDbUpdateError as exc:
        return _fail("scanner_db_update_dry_run_command", str(exc))
    return _pass("scanner_db_update_dry_run_command", "constructed", metadata={"command": command})


def _registry_config_path_check(path: Path | None) -> PreflightCheck:
    if path is None:
        return PreflightCheck(
            name="scanner_db_registry_config_path",
            status="skipped",
            detail="no registry config path supplied",
            required=False,
        )
    if path.is_file():
        return _pass("scanner_db_registry_config_path", "present", metadata={"path": str(path.resolve())})
    return _fail(
        "scanner_db_registry_config_path",
        "missing scanner DB registry config file",
        metadata={"path": str(path)},
    )


def _harbor_discovery_check(
    *,
    url: str | None,
    project: str | None,
    repository: str | None,
    reference: str | None,
    replay: Path | None,
) -> PreflightCheck:
    values = (url, project, repository, reference)
    if not any(values) and replay is None:
        return PreflightCheck(
            name="harbor_discovery_offline",
            status="skipped",
            detail="no Harbor discovery input supplied",
            required=False,
        )
    if not all(values):
        return _fail("harbor_discovery_offline", "Harbor discovery requires url, project, repository, and reference")
    try:
        result = run_harbor_discovery(
            HarborDiscoveryCommand(
                url=url or "",
                project=project or "",
                repository=repository or "",
                reference=reference or "",
            ),
            dry_run=replay is None,
            replay_response=replay,
        )
    except HarborDiscoveryError as exc:
        return _fail("harbor_discovery_offline", str(exc))
    status = "pass" if result.ok else "fail"
    return PreflightCheck(
        name="harbor_discovery_offline",
        status=status,
        detail=result.reason or result.mode,
        metadata={"result": harbor_discovery_result_to_jsonable(result)},
    )


def _parse_today(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit("--today must use YYYY-MM-DD") from exc


def _pass(name: str, detail: str, *, metadata: dict[str, object] | None = None) -> PreflightCheck:
    return PreflightCheck(name=name, status="pass", detail=detail, metadata=metadata)


def _fail(name: str, detail: str, *, metadata: dict[str, object] | None = None) -> PreflightCheck:
    return PreflightCheck(name=name, status="fail", detail=detail, metadata=metadata)


def _check_to_jsonable(check: PreflightCheck) -> dict[str, object]:
    return {
        "name": check.name,
        "status": check.status,
        "detail": check.detail,
        "required": check.required,
        "metadata": check.metadata,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline release/operator policy bundle preflight checks.")
    parser.add_argument("--bundle", type=Path, required=True, help="Operator policy bundle JSON.")
    parser.add_argument("--today", help="Evaluation date for deterministic bundle expiry tests.")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Image reference used for scanner command dry-run.")
    parser.add_argument("--digest", default=DEFAULT_DIGEST, help="sha256 digest used for scanner command dry-run.")
    parser.add_argument("--scan-out", type=Path, default=Path("dist/self-harness-trivy-report.json"))
    parser.add_argument("--scanner-db-cache-dir", type=Path, default=Path("tests/fixtures/vuln/trivy_db"))
    parser.add_argument("--db-registry-config", type=Path, help="Optional Trivy registry config path to validate.")
    parser.add_argument("--trivy-binary", default="trivy", help="Trivy executable name used for command construction.")
    parser.add_argument("--harbor-url", help="Harbor URL used for discovery dry-run or replay.")
    parser.add_argument("--harbor-project", help="Harbor project used for discovery dry-run or replay.")
    parser.add_argument("--harbor-repository", help="Harbor repository used for discovery dry-run or replay.")
    parser.add_argument(
        "--harbor-reference",
        help="Harbor tag or digest reference used for discovery dry-run or replay.",
    )
    parser.add_argument("--harbor-replay", type=Path, help="Optional Harbor artifact JSON replay fixture.")
    parser.add_argument("--result-out", type=Path, help="Optional path for the operator preflight JSON result.")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
