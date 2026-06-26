from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Split(StrEnum):
    HELD_IN = "held_in"
    HELD_OUT = "held_out"


class FailureCategory(StrEnum):
    VERIFIER_PASS = "verifier-pass"
    VERIFIER_FAIL = "verifier-fail"
    TIMEOUT = "timeout"
    MISSING_ARTIFACT = "missing-artifact"
    ASSERTION_FAIL = "assertion-fail"
    ENVIRONMENT_ERROR = "environment-error"


@dataclass(frozen=True)
class TraceEvent:
    kind: str
    message: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class VerifierOutcome:
    passed: bool
    terminal_cause: str
    causal_status: str
    mechanism: str
    message: str


@dataclass(frozen=True)
class FailureSignature:
    terminal_cause: str
    causal_status: str
    mechanism: str

    @property
    def key(self) -> str:
        return "|".join([self.terminal_cause, self.causal_status, self.mechanism])

    @property
    def stable_id(self) -> str:
        parts = [self.terminal_cause, self.causal_status, self.mechanism]
        return "__".join(_slug(part) for part in parts)


@dataclass(frozen=True)
class Task:
    id: str
    split: Split
    failure_mode: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunRecord:
    task_id: str
    split: Split
    passed: bool
    trace: list[TraceEvent]
    outcome: VerifierOutcome
    attempt_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailurePattern:
    id: str
    split: Split
    signature: FailureSignature
    support: int
    task_ids: list[str]
    symptoms: list[str]
    verifier_evidence: list[str]


@dataclass(frozen=True)
class HarnessSpec:
    system_prompt: str
    bootstrap: str
    execution: str
    verification: str
    failure_recovery: str
    runtime_policy: dict[str, Any]
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    memory_sources: list[str] = field(default_factory=list)
    subagents: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class HarnessOp:
    op: str
    surface: str
    payload: Any


@dataclass(frozen=True)
class HarnessPatch:
    ops: list[HarnessOp]


@dataclass(frozen=True)
class Proposal:
    id: str
    round_index: int
    pattern_id: str
    patch: HarnessPatch
    priority: int
    rationale: str
    expected_effect: str
    regression_risks: list[str]
    invalid_reason: str | None = None

    @property
    def primary_op(self) -> HarnessOp:
        return self.patch.ops[0]


@dataclass(frozen=True)
class ProposalBudget:
    max_proposals: int = 8
    max_payload_bytes: int = 600


@dataclass(frozen=True)
class PassingSummary:
    task_id: str
    split: Split
    attempt_index: int
    trace_messages: list[str]
    verifier_message: str


@dataclass(frozen=True)
class AttemptedEdit:
    proposal_id: str
    round_index: int
    pattern_id: str
    changed_surfaces: list[str]
    status: str
    decision_reason: str | None


@dataclass(frozen=True)
class ProposerContext:
    held_in_patterns: list[FailurePattern]
    passing_summaries: list[PassingSummary]
    attempted_edits: list[AttemptedEdit]
    editable_surfaces: list[str]
    harness: HarnessSpec
    round_index: int
    budget: ProposalBudget


@dataclass(frozen=True)
class SplitResult:
    split: Split
    passed: int
    failed: int

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def score(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total


@dataclass(frozen=True)
class EvaluationResult:
    held_in: SplitResult
    held_out: SplitResult
    records: list[RunRecord]
    evaluation_repeats: int = 1


@dataclass(frozen=True)
class AcceptDecision:
    accepted: bool
    reason: str
    baseline_held_in: SplitResult
    baseline_held_out: SplitResult
    candidate_held_in: SplitResult
    candidate_held_out: SplitResult


@dataclass(frozen=True)
class LineageRecord:
    round: int
    harness_before_hash: str
    harness_after_hash: str
    ops_applied: list[HarnessOp]
    reverse_ops: list[HarnessOp]
    accepted_proposal_ids: list[str]
    schema_version: str = "1.2"


def to_jsonable(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        # typeshed cannot yet narrow arbitrary values through is_dataclass().
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def stable_json_dumps(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def write_stable_json(path: Path, value: Any) -> None:
    path.write_text(stable_json_dumps(value) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[Any]) -> None:
    text = "".join(stable_json_dumps(row) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _slug(value: str) -> str:
    chars: list[str] = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-") or "empty"
