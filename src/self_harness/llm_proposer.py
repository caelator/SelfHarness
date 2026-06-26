from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from self_harness.harness import OP_WHITELIST, validate_op
from self_harness.proposer import Proposer
from self_harness.proposer_policy import ProposalPolicy, select_actionable_patterns
from self_harness.types import (
    HarnessOp,
    HarnessPatch,
    Proposal,
    ProposerContext,
    Split,
    stable_json_dumps,
    to_jsonable,
)


class LLMClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass(frozen=True)
class LLMProposerPromptBundle:
    system_prompt: str
    user_prompt: str
    actionable_pattern_ids: frozenset[str]
    context_pattern_ids: frozenset[str]


@dataclass(frozen=True)
class LLMProposerRoundMetadata:
    attempted_proposals: int = 0


@dataclass(frozen=True)
class LLMProposer(Proposer):
    """Provider-neutral proposer that parses strict JSON from an LLM client."""

    client: LLMClient
    policy: ProposalPolicy = ProposalPolicy()
    last_round_metadata: LLMProposerRoundMetadata = field(
        default_factory=LLMProposerRoundMetadata,
        init=False,
        compare=False,
    )

    def propose(self, context: ProposerContext) -> list[Proposal]:
        prompts = render_llm_proposer_prompts(context, self.policy)
        if not prompts.actionable_pattern_ids:
            object.__setattr__(self, "last_round_metadata", LLMProposerRoundMetadata())
            return []

        response = self.client.complete(prompts.system_prompt, prompts.user_prompt)
        raw = _extract_json_object(response)
        if raw is None:
            object.__setattr__(self, "last_round_metadata", LLMProposerRoundMetadata())
            return []
        proposals = _parse_proposals(raw, context, set(prompts.context_pattern_ids))
        object.__setattr__(
            self,
            "last_round_metadata",
            LLMProposerRoundMetadata(attempted_proposals=len(proposals)),
        )
        proposals = _enforce_grounding_and_diversity(proposals, set(prompts.actionable_pattern_ids))

        filtered: list[Proposal] = []
        for proposal in sorted(proposals, key=lambda item: (-item.priority, item.id)):
            payload_size = sum(len(str(op.payload).encode("utf-8")) for op in proposal.patch.ops)
            if payload_size <= context.budget.max_payload_bytes:
                filtered.append(proposal)
            if len(filtered) >= context.budget.max_proposals:
                break
        return filtered


def render_llm_proposer_prompts(
    context: ProposerContext,
    policy: ProposalPolicy | None = None,
) -> LLMProposerPromptBundle:
    """Render prompts from held-in evidence only, even if a caller passes decoys."""

    policy = policy or ProposalPolicy()
    held_in_patterns = [pattern for pattern in context.held_in_patterns if pattern.split == Split.HELD_IN]
    held_in_summaries = [summary for summary in context.passing_summaries if summary.split == Split.HELD_IN]
    held_in_context = replace(
        context,
        held_in_patterns=held_in_patterns,
        passing_summaries=held_in_summaries,
    )
    actionable = select_actionable_patterns(held_in_patterns, held_in_context.editable_surfaces, policy)
    return LLMProposerPromptBundle(
        system_prompt=_system_prompt(held_in_context),
        user_prompt=_user_prompt(held_in_context, actionable),
        actionable_pattern_ids=frozenset(pattern.id for pattern in actionable),
        context_pattern_ids=frozenset(pattern.id for pattern in held_in_patterns),
    )


def _system_prompt(context: ProposerContext) -> str:
    return (
        "You are proposing bounded Self-Harness edits. "
        "Return only JSON. Do not include prose. "
        f"Allowed ops: {', '.join(sorted(OP_WHITELIST))}. "
        f"Editable surfaces: {', '.join(context.editable_surfaces)}. "
        "Schema: {\"proposals\":[{\"id_suffix\":str,\"pattern_id\":str,"
        "\"priority\":int,\"ops\":[{\"op\":str,\"surface\":str,\"payload\":any}],"
        "\"rationale\":str,\"expected_effect\":str,\"regression_risks\":[str]}]}."
    )


def _user_prompt(context: ProposerContext, actionable_patterns: list[Any]) -> str:
    payload = {
        "round_index": context.round_index,
        "budget": to_jsonable(context.budget),
        "editable_surfaces": context.editable_surfaces,
        "harness": to_jsonable(context.harness),
        "held_in_failure_patterns": [_pattern_evidence(pattern) for pattern in actionable_patterns],
        "held_in_passing_summaries": to_jsonable(context.passing_summaries),
        "attempted_edits": to_jsonable(context.attempted_edits),
    }
    return stable_json_dumps(payload)


def _pattern_evidence(pattern: Any) -> dict[str, object]:
    return {
        "id": pattern.id,
        "support": pattern.support,
        "task_ids": list(pattern.task_ids[:3]),
        "symptoms": list(pattern.symptoms[:3]),
        "verifier_evidence": list(pattern.verifier_evidence[:3]),
        "mechanism": pattern.signature.mechanism,
        "signature": to_jsonable(pattern.signature),
    }


def _parse_proposals(
    raw: Any,
    context: ProposerContext,
    context_pattern_ids: set[str],
) -> list[Proposal]:
    if not isinstance(raw, dict) or not isinstance(raw.get("proposals"), list):
        return []

    proposals: list[Proposal] = []
    for index, item in enumerate(raw["proposals"]):
        proposal = _parse_one_proposal(item, index, context, context_pattern_ids)
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def _parse_one_proposal(
    item: object,
    index: int,
    context: ProposerContext,
    context_pattern_ids: set[str],
) -> Proposal | None:
    if not isinstance(item, dict):
        return None
    id_suffix = item.get("id_suffix")
    pattern_id = item.get("pattern_id")
    priority = item.get("priority")
    rationale = item.get("rationale")
    expected_effect = item.get("expected_effect")
    risks = item.get("regression_risks")
    ops_raw = item.get("ops")
    if not (
        isinstance(id_suffix, str)
        and isinstance(pattern_id, str)
        and isinstance(priority, int)
        and isinstance(rationale, str)
        and isinstance(expected_effect, str)
        and isinstance(risks, list)
        and all(isinstance(risk, str) for risk in risks)
        and isinstance(ops_raw, list)
    ):
        return None

    ops: list[HarnessOp] = []
    for op_raw in ops_raw:
        if not isinstance(op_raw, dict):
            return None
        op_name = op_raw.get("op")
        surface = op_raw.get("surface")
        if not isinstance(op_name, str) or not isinstance(surface, str) or "payload" not in op_raw:
            return None
        op = HarnessOp(op=op_name, surface=surface, payload=op_raw["payload"])
        try:
            validate_op(op)
        except ValueError:
            return None
        ops.append(op)
    if not ops:
        return None

    return Proposal(
        id=f"r{context.round_index:02d}__llm__{_slug(id_suffix, index)}",
        round_index=context.round_index,
        pattern_id=pattern_id,
        patch=HarnessPatch(ops),
        priority=priority,
        rationale=rationale,
        expected_effect=expected_effect,
        regression_risks=list(risks),
        invalid_reason=None if pattern_id in context_pattern_ids else "ungrounded_proposal",
    )


def _enforce_grounding_and_diversity(proposals: list[Proposal], allowed_pattern_ids: set[str]) -> list[Proposal]:
    seen: set[tuple[str, str, str]] = set()
    checked: list[Proposal] = []
    for proposal in proposals:
        if proposal.invalid_reason is not None:
            checked.append(proposal)
            continue
        if proposal.pattern_id not in allowed_pattern_ids:
            checked.append(replace(proposal, invalid_reason="unaddressable_pattern"))
            continue
        op = proposal.primary_op
        key = (proposal.pattern_id, op.surface, op.op)
        if key in seen:
            checked.append(replace(proposal, invalid_reason="diversity_collision"))
            continue
        seen.add(key)
        checked.append(proposal)
    return checked


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from an LLM response, tolerating markdown fences and surrounding prose.

    Chat models (GLM included) frequently wrap JSON in ```json ... ``` fences or add a sentence of
    prose, even when asked for raw JSON. We try, in order: the whole string, the contents of a
    fenced code block, and the first balanced ``{...}`` span. Returns the decoded object, or None if
    no JSON object can be recovered.
    """

    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    fenced = _strip_code_fence(stripped)
    if fenced is not None and fenced != stripped:
        candidates.append(fenced)
    span = _first_balanced_object(stripped)
    if span is not None:
        candidates.append(span)

    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _strip_code_fence(text: str) -> str | None:
    if not text.startswith("```"):
        return None
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip() or None


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _slug(value: str, fallback: int) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value.strip().lower())
    return cleaned.strip("-_") or f"proposal-{fallback}"
