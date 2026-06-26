#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.capture_rehearsal import (  # noqa: E402
    CAPTURE_REHEARSAL_BOUNDARY,
    CaptureRehearsalError,
    capture_rehearsal_report_to_jsonable,
    run_capture_rehearsal,
)
from self_harness.exceptions import CorpusSigningError  # noqa: E402
from self_harness.reproduction_readiness import (  # noqa: E402
    ReproductionReadinessError,
    load_readiness_matrix_report,
    load_reproduction_requirements,
)
from self_harness.signing import ExternalSignerError  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requirements = load_reproduction_requirements(args.requirements)
        readiness_matrix = load_readiness_matrix_report(args.readiness_matrix_result)
        report = run_capture_rehearsal(
            manifest_path=args.manifest,
            requirements=requirements,
            readiness_matrix_report=readiness_matrix,
            out_dir=args.out_dir,
            rehearsal_id=args.rehearsal_id,
            operator_label=args.operator_label,
            manifest_signature_path=args.manifest_signature,
            manifest_public_key=args.public_key,
            require_manifest_signature=args.require_manifest_signature,
            bundle_private_key=args.bundle_private_key,
            bundle_external_signer=args.bundle_external_signer,
            bundle_public_key=args.bundle_public_key,
            bundle_fingerprint=args.bundle_fingerprint,
            bundle_signature_path=args.bundle_signature_out,
            bundle_signature_provider=args.bundle_signature_provider,
            bundle_key_id=args.bundle_key_id,
            require_bundle_signature=args.require_bundle_signature,
        )
        payload = capture_rehearsal_report_to_jsonable(report)
    except (OSError, ReproductionReadinessError) as exc:
        payload = _error_payload(exc, code="input-error")
        _write_payload(payload, args.report_out)
        print(stable_json_dumps(payload), file=sys.stderr)
        return 3
    except (CaptureRehearsalError, CorpusSigningError, ExternalSignerError) as exc:
        payload = _error_payload(exc, code="capture-rehearsal-error")
        _write_payload(payload, args.report_out)
        print(stable_json_dumps(payload), file=sys.stderr)
        return 2

    _write_payload(payload, args.report_out)
    print(stable_json_dumps(payload))
    return 0 if report.ok else 2


def _write_payload(payload: dict[str, object], out: Path | None) -> None:
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def _error_payload(exc: BaseException, *, code: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "ok": False,
        "code": code,
        "error": str(exc),
        "reproduction_claimed": False,
        "boundary": CAPTURE_REHEARSAL_BOUNDARY,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rehearse a capture manifest against synthetic offline artifacts.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("docs/operations/benchmark_reproduction_requirements.json"),
    )
    parser.add_argument(
        "--readiness-matrix-result",
        type=Path,
        default=Path("dist/self-harness-readiness-matrix.json"),
    )
    parser.add_argument("--manifest-signature", type=Path)
    parser.add_argument("--public-key")
    parser.add_argument("--require-manifest-signature", action="store_true")
    parser.add_argument("--rehearsal-id", required=True)
    parser.add_argument("--operator-label", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, default=Path("dist/self-harness-capture-rehearsal.json"))
    bundle_signer = parser.add_mutually_exclusive_group()
    bundle_signer.add_argument("--bundle-private-key", type=Path)
    bundle_signer.add_argument("--bundle-external-signer")
    parser.add_argument("--bundle-public-key", type=Path)
    parser.add_argument("--bundle-fingerprint")
    parser.add_argument("--bundle-signature-out", type=Path)
    parser.add_argument("--bundle-signature-provider")
    parser.add_argument("--bundle-key-id")
    parser.add_argument("--require-bundle-signature", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
