import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from self_harness.adapters.container_verifier import ContainerVerifierTaskAdapter
from self_harness.adapters.http_verifier import HttpVerifierTaskAdapter
from self_harness.adapters.in_process_python import InProcessPythonTaskAdapter
from self_harness.adapters.terminal_bench.capture import validate_capture_claims
from self_harness.adapters.terminal_bench.corpus import load_terminal_bench_manifest
from self_harness.adapters.terminal_bench.runner import HarborRunner
from self_harness.audit import SCHEMA_CHANGELOG_DOC, SUPPORTED_SCHEMA_VERSIONS, load_audit_run, write_audit_trajectory
from self_harness.audit_verify import verify_audit_run
from self_harness.config import EngineConfig
from self_harness.corpus import TaskCorpus
from self_harness.demo import ToyRunner, demo_tasks
from self_harness.engine import SelfHarnessEngine, validate_benchmark_claims, validate_proposer_context
from self_harness.evaluation import acceptance_rule, evaluate
from self_harness.exceptions import InvalidPatchError, PaperFidelityError
from self_harness.harness import apply_patch, harness_hash, initial_harness
from self_harness.llm_proposer import LLMProposer, render_llm_proposer_prompts
from self_harness.mining import cluster_failures
from self_harness.proposer import HeuristicProposer
from self_harness.readiness import audit_tree_hash, schema_versions_from_changelog
from self_harness.testing import MockLLMClient
from self_harness.types import (
    EvaluationResult,
    FailurePattern,
    FailureSignature,
    HarnessOp,
    HarnessPatch,
    PassingSummary,
    Proposal,
    ProposalBudget,
    ProposerContext,
    RunRecord,
    Split,
    SplitResult,
    Task,
    TraceEvent,
    VerifierOutcome,
)

TB_FIXTURE_DIR = Path("tests/fixtures/terminal_bench")
TB_MANIFEST = TB_FIXTURE_DIR / "manifest.json"
PYTHON_VERIFIER_MODULE = Path("tests/fixtures/in_process_verifier.py")


def test_harness_lineage_hashes_match_committed_round_state(tmp_path: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=ToyRunner(seed=0),
        proposer=HeuristicProposer(),
        out_dir=tmp_path,
        config=EngineConfig(rounds=2, seed=0),
    )

    engine.run()
    audit = load_audit_run(tmp_path)

    for round_ in audit.rounds:
        lineage = audit.lineage[round_.index]
        assert lineage["harness_before_hash"] == harness_hash(_harness_from_json(round_.harness_before))
        assert lineage["harness_after_hash"] == harness_hash(_harness_from_json(round_.harness_after))
        if round_.index > 0:
            previous = audit.rounds[round_.index - 1]
            assert round_.harness_before == previous.harness_after


def test_loop_runs_all_rounds_and_does_not_break_after_an_empty_round(tmp_path: Path) -> None:
    # Algorithm 1 (lines 18-23) carries the harness forward when a round accepts nothing and
    # CONTINUES the fixed t=0..T-1 loop. A proposer that is silent on round 0 but proposes an
    # accepted edit on round 1 must still produce that edit; the engine must not stop early.
    class LateProposer:
        def propose(self, context: ProposerContext) -> list[Proposal]:
            if context.round_index == 0:
                return []
            return [
                Proposal(
                    id=f"r{context.round_index:02d}__late",
                    round_index=context.round_index,
                    pattern_id="held_in__late",
                    patch=HarnessPatch(
                        [HarnessOp("AppendToSurface", "failure_recovery", "do not repeat the exact failed command")]
                    ),
                    priority=50,
                    rationale="late edit",
                    expected_effect="fix repeated command failures",
                    regression_risks=[],
                )
            ]

    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=ToyRunner(seed=0),
        proposer=LateProposer(),
        out_dir=tmp_path,
        config=EngineConfig(rounds=3, seed=0),
    )

    summaries = engine.run()
    audit = load_audit_run(tmp_path)

    assert len(summaries) == 3  # no early break after the empty round 0
    assert summaries[0].accepted == 0
    assert any(summary.accepted >= 1 for summary in summaries[1:])
    assert len(audit.lineage) == 3


def test_engine_rejects_overlapping_split_partition(tmp_path: Path) -> None:
    # Paper §4.1: the evaluated set is PARTITIONED into held-in and held-out before running
    # Self-Harness. The engine must reject a task id that appears in both splits, otherwise the
    # same task would serve as both proposer evidence and held-out regression check.
    overlapping = [
        Task("shared", Split.HELD_IN, "missing_artifact", "held-in instance"),
        Task("shared", Split.HELD_OUT, "missing_artifact", "held-out instance"),
    ]

    with pytest.raises(PaperFidelityError):
        SelfHarnessEngine(
            tasks=overlapping,
            runner=ToyRunner(seed=0),
            proposer=HeuristicProposer(),
            out_dir=tmp_path,
            config=EngineConfig(rounds=1, seed=0),
        )


def test_proposer_context_rejects_held_out_pattern_and_summary_leakage() -> None:
    base_context = ProposerContext(
        held_in_patterns=[],
        passing_summaries=[],
        attempted_edits=[],
        editable_surfaces=["bootstrap"],
        harness=initial_harness(),
        round_index=0,
        budget=ProposalBudget(),
    )
    validate_proposer_context(base_context)

    leaked_pattern = FailurePattern(
        id="held_out__leak",
        split=Split.HELD_OUT,
        signature=FailureSignature("verifier-fail", "rejected", "late-verification"),
        support=1,
        task_ids=["held-out-task"],
        symptoms=[],
        verifier_evidence=[],
    )
    leaked_summary = PassingSummary(
        task_id="held-out-pass",
        split=Split.HELD_OUT,
        attempt_index=0,
        trace_messages=[],
        verifier_message="passed",
    )

    with pytest.raises(PaperFidelityError):
        validate_proposer_context(replace(base_context, held_in_patterns=[leaked_pattern]))
    with pytest.raises(PaperFidelityError):
        validate_proposer_context(replace(base_context, passing_summaries=[leaked_summary]))


def test_llm_proposer_renderer_uses_held_in_evidence_only() -> None:
    context = ProposerContext(
        held_in_patterns=[
            FailurePattern(
                id="held_in__missing",
                split=Split.HELD_IN,
                signature=FailureSignature("missing-artifact", "confirmed", "missing_artifact"),
                support=1,
                task_ids=["held-in-task"],
                symptoms=["held-in symptom"],
                verifier_evidence=["held-in verifier evidence"],
            ),
            FailurePattern(
                id="held_out__secret",
                split=Split.HELD_OUT,
                signature=FailureSignature("secret", "secret", "missing_artifact"),
                support=1,
                task_ids=["held-out-task-secret"],
                symptoms=["held-out-secret-symptom"],
                verifier_evidence=["held-out-secret-evidence"],
            ),
        ],
        passing_summaries=[
            PassingSummary(
                task_id="held-in-pass",
                split=Split.HELD_IN,
                attempt_index=0,
                trace_messages=["held-in pass trace"],
                verifier_message="held-in verifier passed",
            ),
            PassingSummary(
                task_id="held-out-pass-secret",
                split=Split.HELD_OUT,
                attempt_index=0,
                trace_messages=["held-out-secret-trace"],
                verifier_message="held-out-secret-verifier",
            ),
        ],
        attempted_edits=[],
        editable_surfaces=["bootstrap"],
        harness=initial_harness(),
        round_index=0,
        budget=ProposalBudget(),
    )

    prompts = render_llm_proposer_prompts(context)

    rendered = prompts.system_prompt + prompts.user_prompt
    assert "held-in-task" in rendered
    assert "held-out-task-secret" not in rendered
    assert "held-out-secret-symptom" not in rendered
    assert "held-out-secret-trace" not in rendered
    assert prompts.context_pattern_ids == frozenset({"held_in__missing"})


def test_failure_clustering_uses_exact_signature_not_semantic_similarity() -> None:
    records = [
        _failed_record("a", "missing-artifact", "rejected", "write-output"),
        _failed_record("b", "missing-artifact", "rejected", "verify-output"),
        _failed_record("c", "missing-artifact", "rejected", "write-output"),
    ]

    patterns = cluster_failures(records, split=Split.HELD_IN)

    assert len(patterns) == 2
    assert sorted(pattern.support for pattern in patterns) == [1, 2]
    assert {pattern.signature.mechanism for pattern in patterns} == {"verify-output", "write-output"}


def test_bounded_patch_whitelist_rejects_uneditable_surfaces() -> None:
    with pytest.raises(InvalidPatchError):
        apply_patch(
            initial_harness(),
            HarnessPatch([HarnessOp("AppendToSurface", "held_out_trace_log", "leak evidence")]),
        )


def test_acceptance_rule_rejects_ties_and_split_regressions() -> None:
    baseline = _evaluation(held_in_passed=2, held_out_passed=2)
    tie = _evaluation(held_in_passed=2, held_out_passed=2)
    regression = _evaluation(held_in_passed=3, held_out_passed=1)
    improvement = _evaluation(held_in_passed=3, held_out_passed=2)

    assert not acceptance_rule(baseline, tie).accepted
    assert not acceptance_rule(baseline, regression).accepted
    assert acceptance_rule(baseline, improvement).accepted


def test_evaluation_repeats_are_aggregate_pass_counts() -> None:
    result = evaluate(ToyRunner(seed=0), initial_harness(), demo_tasks(), repeats=2)

    assert result.evaluation_repeats == 2
    assert result.held_in.total == 8
    assert result.held_out.total == 2
    assert {record.attempt_index for record in result.records} == {0, 1}


def test_schema_versions_match_changelog() -> None:
    changelog_versions = schema_versions_from_changelog(Path(SCHEMA_CHANGELOG_DOC))

    assert set(SUPPORTED_SCHEMA_VERSIONS) == changelog_versions


def test_terminal_bench_audit_cannot_claim_reproduction() -> None:
    validate_benchmark_claims(
        {
            "benchmark_protocol": "terminal-bench@2.0",
            "reproduction_claimed": False,
        }
    )

    with pytest.raises(PaperFidelityError):
        validate_benchmark_claims(
            {
                "benchmark_protocol": "terminal-bench@2.0",
                "reproduction_claimed": True,
            }
        )


def test_terminal_bench_capture_artifacts_cannot_claim_reproduction() -> None:
    validate_capture_claims(
        {
            "benchmark_protocol": "terminal-bench@2.0",
            "reproduction_claimed": False,
        }
    )

    with pytest.raises(PaperFidelityError):
        validate_capture_claims(
            {
                "benchmark_protocol": "terminal-bench@2.0",
                "reproduction_claimed": True,
            }
        )


def test_llm_driven_terminal_bench_audit_cannot_claim_reproduction(tmp_path: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=ToyRunner(seed=0),
        proposer=LLMProposer(MockLLMClient()),
        out_dir=tmp_path,
        config=EngineConfig(
            rounds=1,
            seed=0,
            schema_version="1.4",
            model_id="mock-llm-proposer",
            benchmark_metadata={
                "benchmark_protocol": "terminal-bench@2.0",
                "reproduction_claimed": True,
            },
        ),
    )

    with pytest.raises(PaperFidelityError):
        engine.run()


def test_llm_proposer_rejects_ungrounded_pattern_id(tmp_path: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=ToyRunner(seed=0),
        proposer=LLMProposer(MockLLMClient(mode="ungrounded")),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1, seed=0, schema_version="1.4", model_id="mock-llm-proposer"),
    )

    engine.run()
    audit = load_audit_run(tmp_path)

    assert audit.rounds[0].proposals[0]["status"] == "invalid"
    assert audit.rounds[0].proposals[0]["decision_reason"] == "ungrounded_proposal"


def test_canonical_audit_hash_matches_fixture_and_detects_layout_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("TZ", "UTC")
    out_dir = tmp_path / "canonical"
    _run_canonical_demo(out_dir)

    canonical_hash = audit_tree_hash(out_dir)
    expected_hash = Path("tests/fixtures/canonical_audit_hash.txt").read_text(encoding="utf-8").strip()

    assert canonical_hash == expected_hash

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["audit_byte_layout_mutation"] = True
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    assert audit_tree_hash(out_dir) != expected_hash


def test_canonical_audit_hash_is_stable_under_ambient_environment_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("TZ", "UTC")
    _run_canonical_demo(first)

    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    _run_canonical_demo(second)

    assert audit_tree_hash(first) == audit_tree_hash(second)


def test_in_process_python_canonical_audit_hash_matches_fixture(tmp_path: Path) -> None:
    out_dir = tmp_path / "python-canonical"
    _run_in_process_python_demo(out_dir)

    canonical_hash = audit_tree_hash(out_dir)
    expected_hash = Path("tests/fixtures/canonical_python_audit_hash.txt").read_text(encoding="utf-8").strip()

    assert canonical_hash == expected_hash


def test_http_verifier_canonical_audit_hash_matches_fixture(tmp_path: Path) -> None:
    out_dir = tmp_path / "http-canonical"
    with _http_verifier_server() as url:
        _run_http_verifier_demo(out_dir, url)

    canonical_hash = audit_tree_hash(out_dir)
    expected_hash = Path("tests/fixtures/canonical_http_audit_hash.txt").read_text(encoding="utf-8").strip()

    assert canonical_hash == expected_hash


def test_container_verifier_canonical_audit_hash_matches_fixture(tmp_path: Path) -> None:
    out_dir = tmp_path / "container-canonical"
    _run_container_verifier_demo(out_dir)

    canonical_hash = audit_tree_hash(out_dir)
    expected_hash = Path("tests/fixtures/canonical_container_audit_hash.txt").read_text(encoding="utf-8").strip()

    assert canonical_hash == expected_hash


def test_generated_fixture_audits_verify_internal_integrity(tmp_path: Path) -> None:
    demo_dir = tmp_path / "demo"
    python_dir = tmp_path / "python"
    http_dir = tmp_path / "http"
    container_dir = tmp_path / "container"
    terminal_bench_dir = tmp_path / "terminal-bench"

    _run_canonical_demo(demo_dir)
    _run_in_process_python_demo(python_dir)
    with _http_verifier_server() as url:
        _run_http_verifier_demo(http_dir, url)
    _run_container_verifier_demo(container_dir)
    _run_terminal_bench_dry_run(terminal_bench_dir)

    for audit_dir in [demo_dir, python_dir, http_dir, container_dir, terminal_bench_dir]:
        report = verify_audit_run(audit_dir)
        assert report.ok, audit_dir


def test_terminal_bench_dry_run_hash_is_stable_under_ambient_environment_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first-tb"
    second = tmp_path / "second-tb"

    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("TZ", "UTC")
    _run_terminal_bench_dry_run(first)

    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    _run_terminal_bench_dry_run(second)

    assert audit_tree_hash(first) == audit_tree_hash(second)


def _run_canonical_demo(out_dir: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=demo_tasks(),
        runner=ToyRunner(seed=0),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, seed=0),
    )
    engine.run()
    write_audit_trajectory(out_dir)


def _run_in_process_python_demo(out_dir: Path) -> None:
    adapter = InProcessPythonTaskAdapter(module_path=str(PYTHON_VERIFIER_MODULE))
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="python-fixture",
        tasks=[
            Task(
                "held-in-pass",
                Split.HELD_IN,
                "in_process_python",
                "held-in-pass",
                {"verifier_selector": "needs-setup"},
            ),
            Task(
                "held-out-pass",
                Split.HELD_OUT,
                "in_process_python",
                "held-out-pass",
                {"verifier_selector": "pass"},
            ),
        ],
    )
    engine = SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=adapter.runner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, evaluation_repeats=2, model_id="in-process-python-verifier"),
    )
    engine.run()


def _run_http_verifier_demo(out_dir: Path, url: str) -> None:
    adapter = HttpVerifierTaskAdapter(verifier_url=url)
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="http-fixture",
        tasks=[
            Task(
                "held-in-pass",
                Split.HELD_IN,
                "http_verifier",
                "held-in-pass",
                {"verifier_selector": "pass"},
            ),
            Task(
                "held-out-pass",
                Split.HELD_OUT,
                "http_verifier",
                "held-out-pass",
                {"verifier_selector": "pass"},
            ),
        ],
    )
    engine = SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=adapter.runner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, evaluation_repeats=2, model_id="http-verifier"),
    )
    engine.run()


def _run_container_verifier_demo(out_dir: Path) -> None:
    adapter = ContainerVerifierTaskAdapter(
        image="trusted:latest",
        fixture_dir=Path("tests/fixtures/container_verifier"),
    )
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="container-fixture",
        tasks=[
            Task(
                "held-in-pass",
                Split.HELD_IN,
                "container_verifier",
                "held-in-pass",
                {"verifier_selector": "pass"},
            ),
            Task(
                "held-out-pass",
                Split.HELD_OUT,
                "container_verifier",
                "held-out-pass",
                {"verifier_selector": "pass"},
            ),
        ],
    )
    engine = SelfHarnessEngine(
        tasks=adapter.load(corpus),
        runner=adapter.runner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, evaluation_repeats=2, model_id="container-verifier-dry-run"),
    )
    engine.run()


@contextmanager
def _http_verifier_server():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            length = int(self.headers.get("Content-Length", "0"))
            json.loads(self.rfile.read(length).decode("utf-8"))
            payload = json.dumps(
                {
                    "passed": True,
                    "failure_category": None,
                    "mechanism": "fixture-http-pass",
                    "message": "fixture HTTP verifier passed",
                },
                sort_keys=True,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}/verify"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _run_terminal_bench_dry_run(out_dir: Path) -> None:
    corpus = load_terminal_bench_manifest(TB_MANIFEST)
    engine = SelfHarnessEngine(
        tasks=corpus.tasks,
        runner=HarborRunner(dataset="terminal-bench@2.0", fixture_dir=TB_FIXTURE_DIR),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(
            rounds=2,
            seed=0,
            schema_version="1.3",
            model_id="harbor-dry-run-runner",
            benchmark_metadata={
                "benchmark_protocol": "terminal-bench@2.0",
                "benchmark_dataset_version": corpus.corpus_id,
                "benchmark_dataset": corpus.corpus_id,
                "harbor_version": "dry-run",
                "container_image_digest": "dry-run",
                "reproduction_claimed": False,
            },
        ),
    )
    engine.run()


def _failed_record(task_id: str, terminal_cause: str, causal_status: str, mechanism: str) -> RunRecord:
    return RunRecord(
        task_id=task_id,
        split=Split.HELD_IN,
        passed=False,
        trace=[TraceEvent(kind="trace", message="shared symptom")],
        outcome=VerifierOutcome(
            passed=False,
            terminal_cause=terminal_cause,
            causal_status=causal_status,
            mechanism=mechanism,
            message="failed",
        ),
    )


def _evaluation(held_in_passed: int, held_out_passed: int) -> EvaluationResult:
    return EvaluationResult(
        held_in=SplitResult(split=Split.HELD_IN, passed=held_in_passed, failed=4 - held_in_passed),
        held_out=SplitResult(split=Split.HELD_OUT, passed=held_out_passed, failed=2 - held_out_passed),
        records=[],
        evaluation_repeats=2,
    )


def _harness_from_json(value: dict[str, object]):
    from self_harness.types import HarnessSpec

    runtime_policy = value.get("runtime_policy")
    if not isinstance(runtime_policy, dict):
        raise AssertionError("runtime_policy missing from harness snapshot")
    return HarnessSpec(
        system_prompt=str(value["system_prompt"]),
        bootstrap=str(value["bootstrap"]),
        execution=str(value["execution"]),
        verification=str(value["verification"]),
        failure_recovery=str(value["failure_recovery"]),
        runtime_policy=runtime_policy,
        tools=[str(item) for item in value.get("tools", [])],
        skills=[str(item) for item in value.get("skills", [])],
        memory_sources=[str(item) for item in value.get("memory_sources", [])],
        subagents=[dict(item) for item in value.get("subagents", []) if isinstance(item, dict)],
    )
