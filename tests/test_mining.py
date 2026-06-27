from self_harness.demo import DeterministicRunner, demo_tasks
from self_harness.evaluation import evaluate
from self_harness.harness import initial_harness
from self_harness.mining import cluster_failures
from self_harness.types import RunRecord, Split, TraceEvent, VerifierOutcome


def test_clusters_failed_held_in_records_by_signature() -> None:
    result = evaluate(DeterministicRunner(), initial_harness(), demo_tasks())
    patterns = cluster_failures(result.records, split=Split.HELD_IN)

    assert [pattern.support for pattern in patterns] == [1, 1, 1, 1]
    assert {pattern.signature.mechanism for pattern in patterns} == {
        "missing_artifact",
        "repeated_failed_command",
        "late_verification",
        "environment_persistence",
    }
    assert all(pattern.split == Split.HELD_IN for pattern in patterns)


def test_actionability_ranks_addressable_mechanism_ahead_of_equal_support() -> None:
    # Two equally-supported clusters: one maps to an editable surface (missing_artifact ->
    # bootstrap), one does not (unknown_mechanism). Paper §3.2 orders by support AND estimated
    # actionability, so the addressable cluster must come first despite a later signature key.
    records = [
        _failed_record("t1", "missing-artifact", "missing_artifact"),
        _failed_record("t2", "capability-limit", "unknown_mechanism"),
    ]

    without_surfaces = cluster_failures(records, split=Split.HELD_IN)
    with_surfaces = cluster_failures(records, split=Split.HELD_IN, editable_surfaces=["bootstrap"])

    # Without surface knowledge, ordering falls back to signature key ("capability-limit" < "missing").
    assert [p.signature.mechanism for p in without_surfaces] == ["unknown_mechanism", "missing_artifact"]
    # With surfaces, the addressable mechanism is promoted ahead of the non-addressable one.
    assert [p.signature.mechanism for p in with_surfaces] == ["missing_artifact", "unknown_mechanism"]


def _failed_record(task_id: str, terminal_cause: str, mechanism: str) -> RunRecord:
    return RunRecord(
        task_id=task_id,
        split=Split.HELD_IN,
        passed=False,
        trace=[TraceEvent(kind="agent", message="symptom")],
        outcome=VerifierOutcome(
            passed=False,
            terminal_cause=terminal_cause,
            causal_status="agent_causal",
            mechanism=mechanism,
            message="failed",
        ),
    )

