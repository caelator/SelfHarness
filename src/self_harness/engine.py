from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from self_harness.config import EngineConfig
from self_harness.evaluation import Runner, acceptance_rule, evaluate
from self_harness.exceptions import PaperFidelityError
from self_harness.harness import (
    EDITABLE_SURFACES,
    OP_WHITELIST,
    SURFACE_KINDS,
    apply_patch,
    harness_hash,
    initial_harness,
    merge_patches,
    patch_surface_key,
    structurally_mergeable,
)
from self_harness.llm_proposer import LLMClient, LLMProposer
from self_harness.mining import cluster_failures
from self_harness.proposer import Proposer
from self_harness.types import (
    AcceptDecision,
    AttemptedEdit,
    EvaluationResult,
    HarnessPatch,
    HarnessSpec,
    LineageRecord,
    PassingSummary,
    Proposal,
    ProposalBudget,
    ProposerContext,
    RunRecord,
    Split,
    Task,
    stable_json_dumps,
    to_jsonable,
    write_jsonl,
    write_stable_json,
)


@dataclass(frozen=True)
class RoundSummary:
    round: int
    baseline_held_in: str
    baseline_held_out: str
    after_held_in: str
    after_held_out: str
    proposals: int
    accepted: int
    rejected: int


@dataclass(frozen=True)
class ProposerRequestCall:
    system_prompt: str
    user_prompt: str
    request_sha256: str
    response_sha256: str
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class ProposerRoundRecord:
    round_index: int
    proposer_client: str
    request_sha256: str
    response_sha256: str
    prompt_tokens: int
    completion_tokens: int
    attempted_proposals: int
    committed_proposals: int


@dataclass
class RecordingLLMClient:
    """LLMClient wrapper that records stable prompt/response hashes."""

    client: LLMClient
    calls: list[ProposerRequestCall]

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.complete(system_prompt, user_prompt)
        self.calls.append(
            ProposerRequestCall(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                request_sha256=proposer_request_sha256(system_prompt, user_prompt),
                response_sha256=sha256(response.encode("utf-8")).hexdigest(),
                prompt_tokens=0,
                completion_tokens=0,
            )
        )
        return response


def proposer_request_sha256(system_prompt: str, user_prompt: str) -> str:
    payload = {"system_prompt": system_prompt, "user_prompt": user_prompt}
    return sha256((stable_json_dumps(payload) + "\n").encode("utf-8")).hexdigest()


class SelfHarnessEngine:
    def __init__(
        self,
        tasks: list[Task],
        runner: Runner,
        proposer: Proposer,
        out_dir: Path,
        seed: int = 0,
        proposal_budget: ProposalBudget | None = None,
        initial_spec: HarnessSpec | None = None,
        evaluation_repeats: int = 2,
        config: EngineConfig | None = None,
    ) -> None:
        if config is None:
            config = EngineConfig(
                seed=seed,
                evaluation_repeats=evaluation_repeats,
                proposal_budget=proposal_budget or ProposalBudget(),
            )
        validate_split_partition(tasks)
        self.tasks = tasks
        self.runner = runner
        self.proposer = proposer
        self.out_dir = out_dir
        self.config = config
        self.seed = config.seed
        self.proposal_budget = config.proposal_budget
        self.evaluation_repeats = config.evaluation_repeats
        self.harness = initial_spec or initial_harness()
        self.lineage: list[LineageRecord] = []
        self.attempted_edits: list[AttemptedEdit] = []
        self.proposer_request_log: list[ProposerRoundRecord] | None = None
        self._recording_client: RecordingLLMClient | None = None

    def enable_proposer_request_log(self) -> None:
        """Record proposer LLM request/response hashes for later live capture extraction."""

        if self.proposer_request_log is None:
            self.proposer_request_log = []

    def run(self, max_rounds: int | None = None) -> list[RoundSummary]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._maybe_wrap_llm_proposer()
        self._write_manifest()
        summaries: list[RoundSummary] = []
        round_limit = self.config.rounds if max_rounds is None else max_rounds

        for round_index in range(round_limit):
            round_dir = self.out_dir / "rounds" / str(round_index)
            round_dir.mkdir(parents=True, exist_ok=True)
            write_stable_json(round_dir / "harness_before.json", self.harness)

            baseline = evaluate(
                self.runner,
                self.harness,
                self.tasks,
                repeats=self.evaluation_repeats,
            )
            patterns = cluster_failures(
                baseline.records,
                split=Split.HELD_IN,
                editable_surfaces=EDITABLE_SURFACES,
            )
            context = ProposerContext(
                held_in_patterns=patterns,
                passing_summaries=_passing_summaries(baseline.records),
                attempted_edits=list(self.attempted_edits),
                editable_surfaces=sorted(EDITABLE_SURFACES),
                harness=self.harness,
                round_index=round_index,
                budget=self.proposal_budget,
            )
            validate_proposer_context(context)
            calls_before = len(self._recording_client.calls) if self._recording_client is not None else 0
            proposals = self.proposer.propose(context)
            proposer_attempted = _proposer_attempted_count(self.proposer)

            candidate_results: list[tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision]] = []
            proposal_rows: list[dict[str, object]] = []
            evaluation_rows: list[dict[str, object]] = _result_rows(
                "__baseline__",
                "baseline",
                baseline,
                self.config.schema_version,
            )

            for proposal in proposals:
                if proposal.invalid_reason is not None:
                    proposal_rows.append(
                        _invalid_proposal_row(
                            proposal,
                            baseline,
                            proposal.invalid_reason,
                            self.config.schema_version,
                        )
                    )
                    continue
                if not proposal.patch.ops:
                    proposal_rows.append(
                        _invalid_proposal_row(
                            proposal,
                            baseline,
                            "proposal does not modify any editable surface",
                            self.config.schema_version,
                        )
                    )
                    continue
                try:
                    candidate_spec, reverse_patch = apply_patch(self.harness, proposal.patch)
                except Exception as exc:
                    proposal_rows.append(
                        _invalid_proposal_row(
                            proposal,
                            baseline,
                            f"patch failed: {exc}",
                            self.config.schema_version,
                        )
                    )
                    continue
                try:
                    candidate_eval = evaluate(
                        self.runner,
                        candidate_spec,
                        self.tasks,
                        repeats=self.evaluation_repeats,
                    )
                except Exception as exc:
                    proposal_rows.append(
                        _invalid_proposal_row(
                            proposal,
                            baseline,
                            f"evaluation failed: {exc}",
                            self.config.schema_version,
                        )
                    )
                    continue
                decision = acceptance_rule(baseline, candidate_eval)
                candidate_results.append((proposal, candidate_spec, reverse_patch, candidate_eval, decision))
                evaluation_rows.extend(
                    _evaluation_rows(proposal.id, baseline, candidate_eval, self.config.schema_version)
                )

            accepted = _sort_accepted(candidate_results)
            chosen_proposals: list[Proposal] = []
            chosen_patch = HarnessPatch([])
            reverse_patch = HarnessPatch([])
            after_spec = self.harness
            after_eval = baseline
            merge_decision: AcceptDecision | None = None

            if accepted:
                merge_group = _build_merge_group(accepted)
                merged_patch = merge_patches([proposal.patch for proposal, *_rest in merge_group])
                merged_spec, merged_reverse = apply_patch(self.harness, merged_patch)
                merged_eval = evaluate(
                    self.runner,
                    merged_spec,
                    self.tasks,
                    repeats=self.evaluation_repeats,
                )
                merge_decision = acceptance_rule(baseline, merged_eval)
                evaluation_rows.extend(
                    _evaluation_rows("__merge__", baseline, merged_eval, self.config.schema_version)
                )
                if merge_decision.accepted:
                    chosen_proposals = [proposal for proposal, *_rest in merge_group]
                    chosen_patch = merged_patch
                    reverse_patch = merged_reverse
                    after_spec = merged_spec
                    after_eval = merged_eval
                else:
                    best_proposal, best_spec, best_reverse, best_eval, _decision = accepted[0]
                    chosen_proposals = [best_proposal]
                    chosen_patch = best_proposal.patch
                    reverse_patch = best_reverse
                    after_spec = best_spec
                    after_eval = best_eval

            for proposal, _spec, _reverse, candidate_eval, decision in candidate_results:
                status = _proposal_status(proposal, decision, chosen_proposals, merge_decision)
                proposal_rows.append(
                    _proposal_row(
                        proposal,
                        status,
                        baseline,
                        candidate_eval,
                        decision,
                        self.config.schema_version,
                    )
                )
            proposal_rows = sorted(proposal_rows, key=lambda row: str(row["id"]))

            write_jsonl(round_dir / "proposals.jsonl", proposal_rows)
            write_jsonl(round_dir / "evaluations.jsonl", evaluation_rows)
            write_stable_json(round_dir / "harness_after.json", after_spec)
            self._record_proposer_round(round_index, calls_before, proposer_attempted, len(proposal_rows))
            self.attempted_edits.extend(_attempted_edits_from_rows(proposal_rows))

            lineage_record = LineageRecord(
                round=round_index,
                harness_before_hash=harness_hash(self.harness),
                harness_after_hash=harness_hash(after_spec),
                ops_applied=chosen_patch.ops,
                reverse_ops=reverse_patch.ops,
                accepted_proposal_ids=[proposal.id for proposal in chosen_proposals],
                schema_version=self.config.schema_version,
            )
            self.lineage.append(lineage_record)
            write_stable_json(self.out_dir / "lineage.json", self.lineage)

            summaries.append(
                RoundSummary(
                    round=round_index,
                    baseline_held_in=_score_text(baseline.held_in.passed, baseline.held_in.total),
                    baseline_held_out=_score_text(baseline.held_out.passed, baseline.held_out.total),
                    after_held_in=_score_text(after_eval.held_in.passed, after_eval.held_in.total),
                    after_held_out=_score_text(after_eval.held_out.passed, after_eval.held_out.total),
                    proposals=len(proposals),
                    accepted=len(chosen_proposals),
                    rejected=sum(1 for row in proposal_rows if row["status"] in {"invalid", "rejected"}),
                )
            )

            # Algorithm 1 (lines 18-23): when no candidate is accepted, carry the
            # harness forward unchanged (h_{t+1} = h_t) and CONTINUE the fixed
            # t=0..T-1 loop. A stochastic proposer may still surface an acceptable
            # edit in a later round, so the loop must not stop early here.
            self.harness = after_spec

        self._write_proposer_request_log()
        return summaries

    def _maybe_wrap_llm_proposer(self) -> None:
        if self.proposer_request_log is None or self._recording_client is not None:
            return
        if not isinstance(self.proposer, LLMProposer):
            return
        recorder = RecordingLLMClient(self.proposer.client, [])
        self.proposer = replace(self.proposer, client=recorder)
        self._recording_client = recorder

    def _record_proposer_round(
        self,
        round_index: int,
        calls_before: int,
        attempted_proposals: int,
        committed_proposals: int,
    ) -> None:
        if self.proposer_request_log is None or self._recording_client is None:
            return
        calls = self._recording_client.calls
        if len(calls) <= calls_before:
            return
        call = calls[-1]
        self.proposer_request_log.append(
            ProposerRoundRecord(
                round_index=round_index,
                proposer_client="primary",
                request_sha256=call.request_sha256,
                response_sha256=call.response_sha256,
                prompt_tokens=call.prompt_tokens,
                completion_tokens=call.completion_tokens,
                attempted_proposals=attempted_proposals,
                committed_proposals=committed_proposals,
            )
        )

    def _write_proposer_request_log(self) -> None:
        if not self.proposer_request_log:
            return
        rows = [
            {
                "round_index": record.round_index,
                "proposer_client": record.proposer_client,
                "request_sha256": record.request_sha256,
                "response_sha256": record.response_sha256,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "attempted_proposals": record.attempted_proposals,
                "committed_proposals": record.committed_proposals,
            }
            for record in self.proposer_request_log
        ]
        write_jsonl(self.out_dir / "proposer_llm_request_log.jsonl", rows)

    def _write_manifest(self) -> None:
        manifest: dict[str, object] = {
            "protocol_hash": "toy-self-harness-v1",
            "protocol_version": self.config.protocol_version,
            "schema_version": self.config.schema_version,
            "model_id": self.config.model_id,
            "decoding_budget": {
                "max_payload_bytes": self.proposal_budget.max_payload_bytes,
                "max_proposals": self.proposal_budget.max_proposals,
            },
            "evaluation_repeats": self.evaluation_repeats,
            "seed": self.seed,
            "surface_whitelist": sorted(EDITABLE_SURFACES),
            "surface_kinds": {surface: SURFACE_KINDS[surface] for surface in sorted(SURFACE_KINDS)},
            "op_whitelist": sorted(OP_WHITELIST),
        }
        manifest.update(self.config.benchmark_metadata)
        validate_benchmark_claims(manifest)
        write_stable_json(self.out_dir / "manifest.json", manifest)


def _sort_accepted(
    results: list[tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision]],
) -> list[tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision]]:
    return sorted(
        [result for result in results if result[4].accepted],
        key=lambda item: (-item[0].priority, patch_surface_key(item[0].patch), item[0].id),
    )


def _proposer_attempted_count(proposer: Proposer) -> int:
    if isinstance(proposer, LLMProposer):
        return proposer.last_round_metadata.attempted_proposals
    return 0


def validate_split_partition(tasks: list[Task]) -> None:
    """Enforce the paper's §4.1 precondition: held-in and held-out are a disjoint partition.

    Held-out isolation is only meaningful if no task instance appears in both splits;
    otherwise the same task would serve as both proposer evidence and held-out
    regression check. The corpus loader rejects duplicate ids within a split, but the
    engine must also reject the same id appearing across splits.
    """

    held_in = {task.id for task in tasks if task.split == Split.HELD_IN}
    held_out = {task.id for task in tasks if task.split == Split.HELD_OUT}
    overlap = sorted(held_in & held_out)
    if overlap:
        raise PaperFidelityError(
            "held-in and held-out splits must be disjoint; overlapping task id(s): " + ", ".join(overlap)
        )


def validate_proposer_context(context: ProposerContext) -> None:
    leaked_patterns = [pattern.id for pattern in context.held_in_patterns if pattern.split != Split.HELD_IN]
    leaked_summaries = [summary.task_id for summary in context.passing_summaries if summary.split != Split.HELD_IN]
    if leaked_patterns or leaked_summaries:
        details = []
        if leaked_patterns:
            details.append(f"held-out failure patterns: {', '.join(sorted(leaked_patterns))}")
        if leaked_summaries:
            details.append(f"held-out passing summaries: {', '.join(sorted(leaked_summaries))}")
        raise PaperFidelityError("proposer context must contain held-in evidence only: " + "; ".join(details))


def validate_benchmark_claims(manifest: dict[str, object]) -> None:
    if manifest.get("benchmark_protocol") == "terminal-bench@2.0" and manifest.get("reproduction_claimed") is True:
        raise PaperFidelityError("terminal-bench@2.0 audit manifests may not claim reproduction")


def _build_merge_group(
    accepted: list[tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision]],
) -> list[tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision]]:
    group: list[tuple[Proposal, HarnessSpec, HarnessPatch, EvaluationResult, AcceptDecision]] = []
    for candidate in accepted:
        candidate_patch = candidate[0].patch
        if all(structurally_mergeable(candidate_patch, existing[0].patch) for existing in group):
            group.append(candidate)
    return group


def _proposal_status(
    proposal: Proposal,
    decision: AcceptDecision,
    chosen: list[Proposal],
    merge_decision: AcceptDecision | None,
) -> str:
    if not decision.accepted:
        return "rejected"
    chosen_ids = {item.id for item in chosen}
    if proposal.id in chosen_ids and len(chosen) > 1 and (merge_decision and merge_decision.accepted):
        return "merged"
    if proposal.id in chosen_ids:
        return "accepted"
    return "superseded"


def _proposal_row(
    proposal: Proposal,
    status: str,
    baseline: EvaluationResult,
    candidate: EvaluationResult,
    decision: AcceptDecision,
    schema_version: str,
) -> dict[str, object]:
    primary = proposal.primary_op
    decision_reason = _decision_reason(status, decision)
    rejection_reason = None if status in {"accepted", "merged"} else decision_reason
    return {
        "id": proposal.id,
        "schema_version": schema_version,
        "round": proposal.round_index,
        "pattern_id": proposal.pattern_id,
        "op": primary.op,
        "surface": primary.surface,
        "changed_surfaces": changed_surfaces(proposal.patch),
        "payload": primary.payload,
        "status": status,
        "priority": proposal.priority,
        "score_held_in": candidate.held_in.score,
        "score_held_out": candidate.held_out.score,
        "passed_held_in": candidate.held_in.passed,
        "passed_held_out": candidate.held_out.passed,
        "baseline_held_in": baseline.held_in.score,
        "baseline_held_out": baseline.held_out.score,
        "baseline_passed_held_in": baseline.held_in.passed,
        "baseline_passed_held_out": baseline.held_out.passed,
        "evaluation_repeats": candidate.evaluation_repeats,
        "decision_reason": decision_reason,
        "rejection_reason": rejection_reason,
        "rationale": proposal.rationale,
        "expected_effect": proposal.expected_effect,
        "regression_risks": proposal.regression_risks,
    }


def _invalid_proposal_row(
    proposal: Proposal,
    baseline: EvaluationResult,
    reason: str,
    schema_version: str,
) -> dict[str, object]:
    primary = proposal.patch.ops[0] if proposal.patch.ops else None
    return {
        "id": proposal.id,
        "schema_version": schema_version,
        "round": proposal.round_index,
        "pattern_id": proposal.pattern_id,
        "op": primary.op if primary else None,
        "surface": primary.surface if primary else None,
        "changed_surfaces": changed_surfaces(proposal.patch),
        "payload": primary.payload if primary else None,
        "status": "invalid",
        "priority": proposal.priority,
        "score_held_in": baseline.held_in.score,
        "score_held_out": baseline.held_out.score,
        "passed_held_in": baseline.held_in.passed,
        "passed_held_out": baseline.held_out.passed,
        "baseline_held_in": baseline.held_in.score,
        "baseline_held_out": baseline.held_out.score,
        "baseline_passed_held_in": baseline.held_in.passed,
        "baseline_passed_held_out": baseline.held_out.passed,
        "evaluation_repeats": baseline.evaluation_repeats,
        "decision_reason": reason,
        "rejection_reason": reason,
        "rationale": proposal.rationale,
        "expected_effect": proposal.expected_effect,
        "regression_risks": proposal.regression_risks,
    }


def _evaluation_rows(
    proposal_id: str,
    baseline: EvaluationResult,
    candidate: EvaluationResult,
    schema_version: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for arm, result in [("baseline", baseline), ("candidate", candidate)]:
        rows.extend(_result_rows(proposal_id, arm, result, schema_version))
    return rows


def _result_rows(
    proposal_id: str,
    arm: str,
    result: EvaluationResult,
    schema_version: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    records = sorted(result.records, key=lambda record: (record.split.value, record.task_id, record.attempt_index))
    for record in records:
        row: dict[str, object] = {
            "proposal_id": proposal_id,
            "schema_version": schema_version,
            "split": record.split.value,
            "task_id": record.task_id,
            "attempt_index": record.attempt_index,
            "arm": arm,
            "verifier_pass": 1 if record.passed else 0,
            "verifier_fail": 0 if record.passed else 1,
            "terminal_cause": record.outcome.terminal_cause,
            "failure_category": record.outcome.terminal_cause,
            "mechanism": record.outcome.mechanism,
            "evaluation_repeats": result.evaluation_repeats,
        }
        task_source_hash = record.metadata.get("task_source_hash")
        if isinstance(task_source_hash, str):
            row["task_source_hash"] = task_source_hash
        artifact_provenance = record.metadata.get("harbor_artifact_provenance")
        if artifact_provenance is not None:
            row["harbor_artifact_provenance"] = to_jsonable(artifact_provenance)
        reward_value = record.metadata.get("reward_value")
        if isinstance(reward_value, int | float):
            row["reward_value"] = float(reward_value)
        reward_source = record.metadata.get("reward_source")
        if isinstance(reward_source, str):
            row["reward_source"] = reward_source
        trajectory_event_count = record.metadata.get("trajectory_event_count")
        if isinstance(trajectory_event_count, int):
            row["trajectory_event_count"] = trajectory_event_count
        rows.append(row)
    for split_result in [result.held_in, result.held_out]:
        rows.append(
            {
                "proposal_id": proposal_id,
                "schema_version": schema_version,
                "split": split_result.split.value,
                "task_id": "__split_total__",
                "attempt_index": None,
                "arm": arm,
                "verifier_pass": split_result.passed,
                "verifier_fail": split_result.failed,
                "score": split_result.score,
                "terminal_cause": None,
                "failure_category": None,
                "mechanism": None,
                "evaluation_repeats": result.evaluation_repeats,
            }
        )
    return rows


def _score_text(passed: int, total: int) -> str:
    return f"{passed}/{total}"


def _passing_summaries(records: list[RunRecord]) -> list[PassingSummary]:
    summaries: list[PassingSummary] = []
    for record in sorted(records, key=lambda item: (item.task_id, item.attempt_index)):
        if record.split != Split.HELD_IN or not record.passed:
            continue
        summaries.append(
            PassingSummary(
                task_id=record.task_id,
                split=record.split,
                attempt_index=record.attempt_index,
                trace_messages=[event.message for event in record.trace],
                verifier_message=record.outcome.message,
            )
        )
    return summaries


def changed_surfaces(patch: HarnessPatch) -> list[str]:
    return sorted({op.surface for op in patch.ops})


def _decision_reason(status: str, decision: AcceptDecision) -> str:
    if status in {"accepted", "merged"}:
        return decision.reason
    if status == "superseded":
        return "accepted independently but not committed after merge selection"
    return decision.reason


def _attempted_edits_from_rows(rows: list[dict[str, object]]) -> list[AttemptedEdit]:
    edits: list[AttemptedEdit] = []
    for row in rows:
        changed = row.get("changed_surfaces", [])
        round_value = row["round"]
        round_index = round_value if isinstance(round_value, int) else int(str(round_value))
        edits.append(
            AttemptedEdit(
                proposal_id=str(row["id"]),
                round_index=round_index,
                pattern_id=str(row["pattern_id"]),
                changed_surfaces=[str(surface) for surface in changed] if isinstance(changed, list) else [],
                status=str(row["status"]),
                decision_reason=str(row["decision_reason"]) if row.get("decision_reason") is not None else None,
            )
        )
    return edits
