import json
import shutil
from pathlib import Path

import pytest

from self_harness.audit_verify import verify_audit_run
from self_harness.cli import main
from self_harness.config import EngineConfig
from self_harness.demo import ToyRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine
from self_harness.exceptions import AuditCorruptError
from self_harness.proposer import HeuristicProposer
from self_harness.types import stable_json_dumps


def test_verify_audit_run_accepts_canonical_demo_audit(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")

    report = verify_audit_run(audit_dir)

    assert report.ok is True
    assert report.audit_schema_version == "1.2"
    assert report.mode == "replay"
    assert report.reproduction_claimed is False
    assert report.held_out_leakage is False
    assert report.proposer_evidence_inspected is True
    assert report.changed_surfaces_recorded is True
    assert report.evaluation_repeats_recorded is True
    assert report.rejected_reasons_recorded is True
    assert len(report.report_hash) == 64
    assert "not benchmark reproduction evidence" in report.boundary
    assert {check.status for check in report.checks} == {"pass"}


def test_verify_audit_run_detects_tampered_harness_snapshot(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    harness_path = audit_dir / "rounds" / "0" / "harness_after.json"
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    harness["bootstrap"] += "\nTampered after the run."
    harness_path.write_text(stable_json_dumps(harness) + "\n", encoding="utf-8")

    report = verify_audit_run(audit_dir)

    assert report.ok is False
    assert _check(report, "round_0_harness_after_hash").status == "fail"


def test_verify_audit_run_detects_missing_round_dir(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    shutil.rmtree(audit_dir / "rounds" / "0")

    with pytest.raises(AuditCorruptError, match="missing round directory"):
        verify_audit_run(audit_dir)


def test_verify_audit_run_detects_mismatched_accepted_id(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    lineage_path = audit_dir / "lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    lineage[0]["accepted_proposal_ids"] = ["ghost-proposal"]
    lineage_path.write_text(stable_json_dumps(lineage) + "\n", encoding="utf-8")

    report = verify_audit_run(audit_dir)

    assert report.ok is False
    assert _check(report, "round_0_accepted_ids_match_lineage").status == "fail"


def test_verify_audit_run_detects_held_out_proposal_leakage(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    proposals_path = audit_dir / "rounds" / "0" / "proposals.jsonl"
    rows = _read_jsonl(proposals_path)
    rows[0]["pattern_id"] = "held_out__secret"
    proposals_path.write_text("".join(stable_json_dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = verify_audit_run(audit_dir)

    assert report.ok is False
    assert report.held_out_leakage is True
    assert _check(report, "round_0_proposal_held_out_leakage").status == "fail"


def test_verify_audit_run_detects_truncated_evaluations_jsonl(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    (audit_dir / "rounds" / "0" / "evaluations.jsonl").write_text("{", encoding="utf-8")

    with pytest.raises(AuditCorruptError, match="invalid JSONL"):
        verify_audit_run(audit_dir)


def test_verify_audit_run_detects_schema_version_mismatch(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    proposals_path = audit_dir / "rounds" / "0" / "proposals.jsonl"
    rows = _read_jsonl(proposals_path)
    rows[0]["schema_version"] = "1.4"
    proposals_path.write_text("".join(stable_json_dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = verify_audit_run(audit_dir)

    assert report.ok is False
    assert _check(report, "round_0_proposal_schema_versions").status == "fail"


def test_verify_audit_run_rejects_unsupported_schema(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    manifest_path = audit_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "9.9"
    manifest_path.write_text(stable_json_dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(AuditCorruptError, match="unsupported audit schema_version"):
        verify_audit_run(audit_dir)


def test_verify_audit_run_validates_migration_provenance_when_present(tmp_path: Path) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    manifest_path = audit_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["migration_applied"] = True
    manifest["migration_provenance"] = {
        "schema_version": "1.0",
        "source_audit_hash": "a" * 64,
        "source_schema_version": "1.0",
        "target_schema_version": "1.2",
        "classification": "lossless",
        "transform_ids": ["metadata-1.0-to-1.1", "metadata-1.1-to-1.2"],
        "notes": [],
        "lossy_allowed": False,
        "boundary": "release/operator migration copy",
    }
    manifest_path.write_text(stable_json_dumps(manifest) + "\n", encoding="utf-8")

    report = verify_audit_run(audit_dir)

    assert report.ok is True
    assert _check(report, "migration_provenance_target_schema").status == "pass"


def test_audit_verify_cli_writes_report_and_uses_exit_codes(tmp_path: Path, capsys) -> None:
    audit_dir = _write_demo_audit(tmp_path / "audit")
    out_path = tmp_path / "audit-verify.json"

    code = main(["audit-verify", str(audit_dir), "--json", "--out", str(out_path)])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert output["mode"] == "replay"
    assert output["reproduction_claimed"] is False
    assert output["changed_surfaces_recorded"] is True
    assert json.loads(out_path.read_text(encoding="utf-8"))["report_hash"] == output["report_hash"]

    (audit_dir / "rounds" / "0" / "evaluations.jsonl").write_text("{", encoding="utf-8")
    code = main(["audit-verify", str(audit_dir), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert code == 3
    assert output["reason"] == "audit-corrupt"


def _write_demo_audit(out_dir: Path) -> Path:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=ToyRunner(seed=0),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, seed=0),
    )
    engine.run()
    return out_dir


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _check(report, name: str):
    for check in report.checks:
        if check.name == name:
            return check
    raise AssertionError(f"missing check: {name}")
