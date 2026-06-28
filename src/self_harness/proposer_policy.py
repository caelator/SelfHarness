from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256

from self_harness.types import FailurePattern, Proposal

ADDRESSABLE_SURFACE_BY_MECHANISM: dict[str, tuple[str, ...]] = {
    # Deterministic-runner mechanisms.
    "environment_persistence": ("execution",),
    "late_verification": ("verification",),
    "missing_artifact": ("bootstrap",),
    "repeated_failed_command": ("failure_recovery",),
    # Agentic-runner mechanisms (GLM solver + Codex judge). A codex-judge failure means the agent
    # finished but its output did not satisfy the success criteria — addressable by the surfaces that
    # shape how the agent works and self-checks before stopping. A solver error (the agent crashed /
    # mis-used a tool) is addressable by the failure-recovery guidance.
    "codex-judge": ("system_prompt", "verification", "execution"),
    "agent-solver-error": ("failure_recovery", "execution"),
    # System-level failures detected by the loop watchdog. A loop_timeout means the run exceeded
    # its wall-clock budget — the harness should add timeout awareness and early-exit guidance so
    # the agent doesn't hang on unresponsive API calls or infinite retry loops.
    "loop_timeout": ("failure_recovery", "execution", "runtime_policy"),
}


@dataclass(frozen=True)
class ProposalPolicy:
    """Policy for selecting and de-duplicating proposal targets."""

    min_pattern_support: int = 1
    # Paper §3.3: candidates must be "materially distinct" and not "merely restate the
    # same cluster, surface, or mechanism with different wording". Enforced by default so
    # the heuristic (non-LLM) proposer drops exact duplicates while still allowing
    # genuinely distinct hypotheses (different payloads) to reach the validation gate.
    require_distinct_surfaces: bool = True

    def __post_init__(self) -> None:
        if self.min_pattern_support < 1:
            raise ValueError("min_pattern_support must be at least 1")


def is_addressable(pattern: FailurePattern, editable_surfaces: Iterable[str]) -> bool:
    editable = set(editable_surfaces)
    return any(surface in editable for surface in ADDRESSABLE_SURFACE_BY_MECHANISM.get(pattern.signature.mechanism, ()))


def select_actionable_patterns(
    patterns: list[FailurePattern],
    editable_surfaces: Iterable[str],
    policy: ProposalPolicy,
) -> list[FailurePattern]:
    return [
        pattern
        for pattern in patterns
        if pattern.support >= policy.min_pattern_support and is_addressable(pattern, editable_surfaces)
    ]


def ensure_diverse(proposals: list[Proposal], policy: ProposalPolicy) -> list[Proposal]:
    if not policy.require_distinct_surfaces:
        return proposals

    seen: set[tuple[str, str, str, str]] = set()
    diverse: list[Proposal] = []
    for proposal in proposals:
        op = proposal.primary_op
        key = (proposal.pattern_id, op.surface, op.op, _payload_hash(op.payload))
        if key in seen:
            continue
        seen.add(key)
        diverse.append(proposal)
    return diverse


def _payload_hash(payload: object) -> str:
    return sha256(str(payload).encode("utf-8")).hexdigest()

