from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from self_harness.types import stable_json_dumps

MockLLMMode = Literal["grounded", "ungrounded"]


@dataclass
class MockLLMClient:
    """Deterministic LLMClient implementation for engine-loop tests and examples."""

    seed: int = 0
    mode: MockLLMMode = "grounded"
    fabricated_pattern_id: str = "held_out__fabricated"
    system_prompts: list[str] = field(default_factory=list, init=False)
    user_prompts: list[str] = field(default_factory=list, init=False)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        payload = json.loads(user_prompt)
        patterns = payload.get("held_in_failure_patterns", [])
        if not isinstance(patterns, list):
            patterns = []
        if self.mode == "ungrounded":
            return stable_json_dumps({"proposals": [_ungrounded_proposal(self.fabricated_pattern_id)]})
        proposals = [
            proposal
            for index, pattern in enumerate(_pattern_rows(patterns))
            if (proposal := _proposal_for_pattern(pattern, index, self.seed)) is not None
        ]
        return stable_json_dumps({"proposals": proposals})

    @property
    def last_system_prompt(self) -> str:
        return self.system_prompts[-1] if self.system_prompts else ""

    @property
    def last_user_prompt(self) -> str:
        return self.user_prompts[-1] if self.user_prompts else ""


def _pattern_rows(patterns: list[object]) -> list[dict[str, object]]:
    return [pattern for pattern in patterns if isinstance(pattern, dict)]


def _proposal_for_pattern(pattern: dict[str, object], index: int, seed: int) -> dict[str, object] | None:
    pattern_id = pattern.get("id")
    mechanism = pattern.get("mechanism")
    if not isinstance(pattern_id, str) or not isinstance(mechanism, str):
        return None
    mapping = _proposal_mapping(mechanism)
    if mapping is None:
        return None
    surface, payload, expected_effect, risks = mapping
    return {
        "id_suffix": f"{seed}-{index}-{mechanism}",
        "pattern_id": pattern_id,
        "priority": 100 - index,
        "ops": [{"op": "AppendToSurface", "surface": surface, "payload": payload}],
        "rationale": f"Pattern {pattern_id} reports {mechanism}.",
        "expected_effect": expected_effect,
        "regression_risks": risks,
    }


def _proposal_mapping(mechanism: str) -> tuple[str, str, str, list[str]] | None:
    if mechanism == "missing_artifact":
        return (
            "bootstrap",
            (
                "When the task explicitly names a required output file, create that artifact early "
                "and update it after verification."
            ),
            "Create explicit required artifacts without changing unrelated tasks.",
            ["May be too narrow for implicit artifact requirements."],
        )
    if mechanism == "repeated_failed_command":
        return (
            "failure_recovery",
            "After a command fails, change strategy before retrying; do not repeat the exact failed command.",
            "Break repeated command failure loops.",
            ["Could add unnecessary branching for transient failures."],
        )
    if mechanism == "late_verification":
        return (
            "verification",
            (
                "Run targeted verification as soon as a candidate artifact or fix exists, "
                "while there is still time to recover."
            ),
            "Move verification earlier in the work loop.",
            ["Could spend too much budget on early checks."],
        )
    if mechanism == "environment_persistence":
        return (
            "execution",
            (
                "When environment setup changes PATH, installs tools, or exports variables, "
                "persist the change and verify it in a fresh shell."
            ),
            "Preserve environment state across command boundaries.",
            ["Could be unnecessary on stateless tasks."],
        )
    return None


def _ungrounded_proposal(pattern_id: str) -> dict[str, object]:
    return {
        "id_suffix": "fabricated",
        "pattern_id": pattern_id,
        "priority": 100,
        "ops": [{"op": "AppendToSurface", "surface": "bootstrap", "payload": "fabricated edit"}],
        "rationale": "This proposal is intentionally ungrounded.",
        "expected_effect": "Exercise invalid proposal auditing.",
        "regression_risks": [],
    }
