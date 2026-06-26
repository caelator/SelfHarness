import json
from pathlib import Path

import pytest

from self_harness.audit import (
    SCHEMA_CHANGELOG_DOC,
    SUPPORTED_SCHEMA_VERSIONS,
    audit_trajectory_rows,
    inspect_harness_run,
    load_audit_run,
    summarize_audit_run,
    write_audit_trajectory,
    write_harness_inspection,
)
from self_harness.cli import main
from self_harness.demo import ToyRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine
from self_harness.exceptions import AuditCorruptError
from self_harness.proposer import HeuristicProposer


def test_load_and_summarize_audit_run(tmp_path: Path) -> None:
    _run_demo(tmp_path)

    audit = load_audit_run(tmp_path)
    summary = summarize_audit_run(tmp_path)

    assert audit.manifest["schema_version"] == "1.2"
    # Algorithm 1 runs the full T rounds (default 3); rounds after convergence
    # carry the harness forward with zero proposals rather than breaking early.
    assert len(audit.lineage) == 3
    assert len(audit.rounds) == 3
    assert summary.schema_version == "1.2"
    assert summary.rounds == 3
    assert summary.final_held_in_score == 1.0
    assert summary.final_held_out_score == 1.0
    assert summary.accepted_count == 4
    assert summary.rejected_count == 1


def test_load_audit_rejects_missing_and_corrupt_artifacts(tmp_path: Path) -> None:
    with pytest.raises(AuditCorruptError):
        load_audit_run(tmp_path)

    (tmp_path / "manifest.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(AuditCorruptError):
        load_audit_run(tmp_path)


def test_load_audit_rejects_unknown_schema_and_missing_round(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"schema_version": "9.9"}), encoding="utf-8")
    (tmp_path / "lineage.json").write_text("[]", encoding="utf-8")
    with pytest.raises(AuditCorruptError):
        load_audit_run(tmp_path)

    (tmp_path / "manifest.json").write_text(json.dumps({"schema_version": "1.2"}), encoding="utf-8")
    (tmp_path / "lineage.json").write_text(json.dumps([{"round": 0}]), encoding="utf-8")
    (tmp_path / "rounds").mkdir()
    with pytest.raises(AuditCorruptError):
        load_audit_run(tmp_path)


def test_supported_schema_versions_point_to_changelog() -> None:
    assert "1.2" in SUPPORTED_SCHEMA_VERSIONS
    assert "1.3" in SUPPORTED_SCHEMA_VERSIONS
    assert "1.4" in SUPPORTED_SCHEMA_VERSIONS
    assert SCHEMA_CHANGELOG_DOC == "docs/architecture/schema_changelog.md"


def test_audit_trajectory_rows_capture_committed_lineage(tmp_path: Path) -> None:
    _run_demo(tmp_path)

    rows = audit_trajectory_rows(tmp_path)

    assert [row["round"] for row in rows] == [0, 1, 2]
    assert rows[0]["schema_version"] == "1.0"
    assert isinstance(rows[0]["harness_before_hash"], str)
    assert rows[-1]["after_held_in_passed"] == 8
    assert rows[-1]["after_held_out_passed"] == 2
    assert any(proposal["changed_surfaces"] == ["bootstrap"] for proposal in rows[0]["proposals"])
    assert {proposal["primary_op"] for proposal in rows[0]["proposals"]} == {"AppendToSurface"}


def test_audit_trajectory_cli_writes_default_jsonl(tmp_path: Path, capsys) -> None:
    _run_demo(tmp_path)

    code = main(["audit-trajectory", str(tmp_path)])
    output = capsys.readouterr().out
    rows = [json.loads(line) for line in (tmp_path / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]

    assert code == 0
    assert f"Trajectory: {tmp_path / 'trajectory.jsonl'}" in output
    assert len(rows) == 3
    assert rows[0]["schema_version"] == "1.0"


def test_write_audit_trajectory_supports_explicit_path(tmp_path: Path) -> None:
    _run_demo(tmp_path / "run")
    out_path = tmp_path / "trajectory.jsonl"

    result = write_audit_trajectory(tmp_path / "run", out_path)

    assert result == out_path
    assert out_path.exists()


def test_inspect_harness_run_reports_retained_edits_and_final_surfaces(tmp_path: Path) -> None:
    _run_demo(tmp_path)

    inspection = inspect_harness_run(tmp_path)

    assert inspection.schema_version == "1.0"
    assert inspection.audit_schema_version == "1.2"
    assert inspection.retained_ops_count == 4
    assert inspection.retained_changed_surfaces == ["bootstrap", "execution", "failure_recovery", "verification"]
    assert inspection.rounds[0]["proposal_status_counts"] == {"merged": 4, "rejected": 1}
    assert inspection.rounds[0]["changed_surfaces"] == [
        "bootstrap",
        "execution",
        "failure_recovery",
        "verification",
    ]
    assert inspection.rounds[1]["proposal_status_counts"] == {}
    assert inspection.final_harness_surfaces["bootstrap"]["kind"] == "text"
    assert "explicitly names a required output file" in inspection.final_harness_surfaces["bootstrap"]["value"]


def test_write_harness_inspection_supports_default_and_explicit_paths(tmp_path: Path) -> None:
    _run_demo(tmp_path / "run")

    default_path = write_harness_inspection(tmp_path / "run")
    explicit_path = write_harness_inspection(tmp_path / "run", tmp_path / "inspection.json")

    assert default_path == tmp_path / "run" / "harness_inspection.json"
    assert explicit_path == tmp_path / "inspection.json"
    assert json.loads(default_path.read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_harness_inspection_cli_writes_and_prints_json(tmp_path: Path, capsys) -> None:
    _run_demo(tmp_path)

    code = main(["inspect-harness", str(tmp_path)])
    output = capsys.readouterr().out
    assert code == 0
    assert f"Harness inspection: {tmp_path / 'harness_inspection.json'}" in output

    code = main(["inspect-harness", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["retained_ops_count"] == 4
    assert payload["rounds"][0]["accepted_proposal_ids"]


def _run_demo(out_dir: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=ToyRunner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
    )
    engine.run()
