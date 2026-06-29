from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.freshness_policy import (  # noqa: E402
    FreshnessPolicyError,
    evaluate_freshness_policy,
    freshness_decision_to_jsonable,
    load_freshness_policy,
    load_trivy_report_timestamp,
)
from self_harness.image_policy import (  # noqa: E402
    ImagePolicy,
    ImagePolicyDecision,
    ImagePolicyError,
    evaluate_image_policy,
    load_image_policy,
)
from self_harness.types import stable_json_dumps  # noqa: E402
from self_harness.vulnerability_policy import (  # noqa: E402
    TrivyImageReference,
    VulnerabilityFinding,
    VulnerabilityPolicyDecision,
    VulnerabilityPolicyError,
    decision_to_jsonable,
    empty_vulnerability_policy,
    evaluate_vulnerability_policy,
    findings_from_pip_audit_report,
    load_pip_audit_report,
    load_trivy_image_references,
    load_trivy_report,
    load_vulnerability_policy,
)

REPORT_SCHEMA_VERSION = "1.0"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_vulnerability_check(
            wheel=args.wheel,
            audit_json=args.audit_json,
            report_format=args.format,
            policy_path=args.policy,
            image_policy_path=args.image_policy,
            freshness_policy_path=args.freshness_policy,
            today=args.today,
            python=args.python,
        )
    except (OSError, VulnerabilityPolicyError, ImagePolicyError, FreshnessPolicyError) as exc:
        print(stable_json_dumps({"ok": False, "reason": "vulnerability-check-error", "message": str(exc)}))
        return 2
    output = stable_json_dumps(report) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["ok"] is True else 2


def run_vulnerability_check(
    *,
    wheel: Path | None = None,
    audit_json: Path | None = None,
    report_format: str = "pip-audit",
    policy_path: Path | None = None,
    image_policy_path: Path | None = None,
    freshness_policy_path: Path | None = None,
    today: str | None = None,
    python: str = sys.executable,
) -> dict[str, object]:
    policy = load_vulnerability_policy(policy_path) if policy_path is not None else empty_vulnerability_policy()
    image_policy_report = None
    freshness_report = None
    if audit_json is not None:
        findings = _load_report(audit_json, report_format=report_format)
        image_policy_report = _evaluate_image_policy_report(audit_json, report_format, image_policy_path)
        freshness_report = _evaluate_freshness_report(
            audit_json,
            report_format,
            freshness_policy_path,
            today=_parse_today(today),
        )
        requirements: tuple[str, ...] = ()
        source = str(audit_json.resolve())
    else:
        if image_policy_path is not None:
            raise VulnerabilityPolicyError("--image-policy is only valid with --format trivy --audit-json")
        if freshness_policy_path is not None:
            raise VulnerabilityPolicyError("--freshness-policy is only valid with --format trivy --audit-json")
        wheel_path = _resolve_wheel(wheel)
        requirements = runtime_requirements_from_wheel(wheel_path)
        findings = _audit_wheel_runtime_tree(wheel_path, requirements, python=python)
        source = str(wheel_path.resolve())
    decisions = evaluate_vulnerability_policy(policy, findings, today=_parse_today(today))
    return vulnerability_report(
        source=source,
        requirements=requirements,
        findings=findings,
        decisions=decisions,
        image_policy_report=image_policy_report,
        freshness_report=freshness_report,
    )


def vulnerability_report(
    *,
    source: str,
    requirements: tuple[str, ...],
    findings: tuple[VulnerabilityFinding, ...],
    decisions: tuple[VulnerabilityPolicyDecision, ...],
    image_policy_report: dict[str, object] | None = None,
    freshness_report: dict[str, object] | None = None,
) -> dict[str, object]:
    unallowed = tuple(decision for decision in decisions if not decision.allowed)
    image_allowed = image_policy_report is None or image_policy_report.get("allowed") is True
    freshness_allowed = freshness_report is None or freshness_report.get("allowed") is True
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "ok": not unallowed and image_allowed and freshness_allowed,
        "source": source,
        "requirements": list(requirements),
        "finding_count": len(findings),
        "allowed_count": len(decisions) - len(unallowed),
        "unallowed_count": len(unallowed),
        "decisions": [decision_to_jsonable(decision) for decision in decisions],
    }
    if image_policy_report is not None:
        report["image_policy"] = image_policy_report
    if freshness_report is not None:
        report["freshness"] = freshness_report
    return report


def runtime_requirements_from_wheel(wheel: Path) -> tuple[str, ...]:
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise VulnerabilityPolicyError(f"wheel path must point to one .whl file: {wheel}")
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise VulnerabilityPolicyError(f"expected exactly one wheel METADATA file, found {len(metadata_names)}")
        metadata_text = archive.read(metadata_names[0]).decode("utf-8")
    metadata = Parser().parsestr(metadata_text)
    requirements = tuple(
        requirement
        for raw_requirement in metadata.get_all("Requires-Dist", [])
        for requirement in [_runtime_requirement(raw_requirement)]
        if requirement is not None
    )
    return tuple(sorted(set(requirements)))


def _runtime_requirement(raw_requirement: str) -> str | None:
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError:
        if "extra ==" in raw_requirement or 'extra == "' in raw_requirement:
            return None
        return raw_requirement
    try:
        requirement = Requirement(raw_requirement)
    except InvalidRequirement as exc:
        raise VulnerabilityPolicyError(f"invalid wheel requirement: {raw_requirement}") from exc
    if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
        return None
    return str(requirement)


def _audit_wheel_runtime_tree(
    wheel: Path,
    requirements: tuple[str, ...],
    *,
    python: str,
) -> tuple[VulnerabilityFinding, ...]:
    if not requirements:
        return ()
    with tempfile.TemporaryDirectory(prefix="self-harness-vuln-check-") as tmp:
        target_path = Path(tmp) / "target"
        install = subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--target",
                str(target_path),
                "--only-binary=:all:",
                "--upgrade",
                str(wheel),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if install.returncode != 0:
            raise VulnerabilityPolicyError(_subprocess_error("wheel runtime dependency install", install))
        completed = subprocess.run(
            [
                python,
                "-m",
                "pip_audit",
                "--format",
                "json",
                "--path",
                str(target_path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    if not completed.stdout.strip():
        raise VulnerabilityPolicyError(_pip_audit_error(completed))
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VulnerabilityPolicyError(_pip_audit_error(completed)) from exc
    if not isinstance(report, dict):
        raise VulnerabilityPolicyError("pip-audit output must be a JSON object")
    if completed.returncode not in {0, 1}:
        raise VulnerabilityPolicyError(_pip_audit_error(completed))
    return findings_from_pip_audit_report(report)


def _pip_audit_error(completed: subprocess.CompletedProcess[str]) -> str:
    return _subprocess_error("pip-audit", completed)


def _subprocess_error(label: str, completed: subprocess.CompletedProcess[str]) -> str:
    stderr = completed.stderr.replace("\r", " ").replace("\n", " ").strip()
    stdout = completed.stdout.replace("\r", " ").replace("\n", " ").strip()
    detail = stderr or stdout or f"exit_status={completed.returncode}"
    return f"{label} failed: {detail[:600]}"


def _resolve_wheel(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    candidates = sorted((REPO_ROOT / "dist").glob("*.whl"))
    if len(candidates) != 1:
        raise VulnerabilityPolicyError(f"expected exactly one dist/*.whl file, found {len(candidates)}")
    return candidates[0].resolve()


def _parse_today(value: str | None):
    if value is None:
        return None
    from datetime import date

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise VulnerabilityPolicyError("--today must use YYYY-MM-DD") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate built release dependencies against vulnerability policy.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--wheel", type=Path, help="Built wheel to inspect for runtime requirements.")
    source.add_argument("--audit-json", type=Path, help="Existing pip-audit JSON report for deterministic checks.")
    parser.add_argument(
        "--format",
        choices=["pip-audit", "trivy"],
        default="pip-audit",
        help="Input format for --audit-json reports.",
    )
    parser.add_argument("--policy", type=Path, help="Operator-owned vulnerability policy JSON.")
    parser.add_argument(
        "--image-policy",
        type=Path,
        help="Operator-owned image policy JSON; only valid with --format trivy --audit-json.",
    )
    parser.add_argument(
        "--freshness-policy",
        type=Path,
        help="Operator-owned scanner report freshness policy JSON; only valid with --format trivy --audit-json.",
    )
    parser.add_argument("--out", type=Path, help="Optional JSON report output path.")
    parser.add_argument("--today", help="Override evaluation date for deterministic tests.")
    parser.add_argument("--python", default=sys.executable, help="Python executable used to run pip-audit.")
    return parser


def _load_report(path: Path, *, report_format: str) -> tuple[VulnerabilityFinding, ...]:
    if report_format == "pip-audit":
        return load_pip_audit_report(path)
    if report_format == "trivy":
        return load_trivy_report(path)
    raise VulnerabilityPolicyError(f"unsupported vulnerability report format: {report_format}")


def _evaluate_image_policy_report(
    report_path: Path,
    report_format: str,
    image_policy_path: Path | None,
) -> dict[str, object] | None:
    if image_policy_path is None:
        return None
    if report_format != "trivy":
        raise VulnerabilityPolicyError("--image-policy is only valid with --format trivy --audit-json")
    image_policy = load_image_policy(image_policy_path)
    references = load_trivy_image_references(report_path)
    if not references:
        return {
            "required": True,
            "allowed": False,
            "code": "missing-digest",
            "message": "Trivy report did not include Metadata.RepoDigests",
            "candidates": [],
        }
    candidates = [_image_policy_candidate(image_policy, reference) for reference in references]
    allowed_candidates = [candidate for candidate in candidates if candidate["allowed"] is True]
    selected = allowed_candidates[0] if allowed_candidates else candidates[0]
    return {
        "required": True,
        "allowed": bool(allowed_candidates),
        "code": selected["code"],
        "message": selected["message"],
        "image": selected["image"],
        "digest": selected["digest"],
        "candidates": candidates,
    }


def _image_policy_candidate(policy: ImagePolicy, reference: TrivyImageReference) -> dict[str, object]:
    decision = evaluate_image_policy(
        policy,
        reference.image,
        reference.digest,
        require_digest=True,
    )
    return _image_policy_decision_to_jsonable(reference, decision)


def _image_policy_decision_to_jsonable(
    reference: TrivyImageReference,
    decision: ImagePolicyDecision,
) -> dict[str, object]:
    return {
        "allowed": decision.allowed,
        "code": decision.code,
        "message": decision.message,
        "image": reference.image,
        "digest": reference.digest,
    }


def _evaluate_freshness_report(
    report_path: Path,
    report_format: str,
    freshness_policy_path: Path | None,
    *,
    today,
) -> dict[str, object] | None:
    if freshness_policy_path is None:
        return None
    if report_format != "trivy":
        raise VulnerabilityPolicyError("--freshness-policy is only valid with --format trivy --audit-json")
    policy = load_freshness_policy(freshness_policy_path)
    timestamp = load_trivy_report_timestamp(report_path)
    decision = evaluate_freshness_policy(policy, timestamp, evaluated_at=today)
    return freshness_decision_to_jsonable(decision)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
