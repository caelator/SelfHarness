from __future__ import annotations

import json
import random
from dataclasses import dataclass

from self_harness.cli_agent.ux_harvest import (
    AdmissionResult,
    JudgeProviderRegistry,
    SecondaryModelJudge,
    UxCandidate,
    UxFailureHarvester,
)


@dataclass
class StubProvider:
    provider_id: str
    result: AdmissionResult | Exception
    calls: int = 0

    def admit(self, candidate: UxCandidate) -> AdmissionResult:
        del candidate
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_secondary_judge_excludes_operating_provider_and_admits() -> None:
    active = StubProvider("glm", AdmissionResult(True, "glm", "c", "bad"))
    codex = StubProvider("codex", AdmissionResult(True, "codex", "criterion", "valid"))
    judge = SecondaryModelJudge(
        registry=JudgeProviderRegistry(providers=[active, codex]),
        rng=random.Random(0),
    )

    result = judge.admit(UxCandidate("trigger", "observation", "glm"))

    assert result.admitted is True
    assert result.judge_provider == "codex"
    assert result.checkable_criterion == "criterion"
    assert active.calls == 0
    assert codex.calls == 1


def test_secondary_judge_retries_different_provider_on_failure() -> None:
    broken = StubProvider("codex", RuntimeError("offline"))
    claude = StubProvider("claude", AdmissionResult(True, "claude", "criterion", "ok"))
    judge = SecondaryModelJudge(
        registry=JudgeProviderRegistry(providers=[broken, claude]),
        rng=random.Random(1),
    )

    result = judge.admit(UxCandidate("trigger", "observation", "glm"))

    assert result.admitted is True
    assert result.judge_provider == "claude"
    assert result.attempts == ("codex", "claude")
    assert broken.calls == 1
    assert claude.calls == 1


def test_secondary_judge_no_eligible_provider_rejects() -> None:
    judge = SecondaryModelJudge(
        registry=JudgeProviderRegistry(providers=[StubProvider("glm", AdmissionResult(True, "glm", "c", "ok"))])
    )

    result = judge.admit(UxCandidate("trigger", "observation", "glm"))

    assert result.admitted is False
    assert result.reason == "no_eligible_judge"


def test_ux_harvester_writes_admitted_and_rejected_bundles(tmp_path) -> None:  # type: ignore[no-untyped-def]
    admitted = StubProvider("codex", AdmissionResult(True, "codex", "checkable", "valid"))
    harvester = UxFailureHarvester(
        inbox_dir=tmp_path / "inbox",
        workdir=tmp_path,
        judge=SecondaryModelJudge(registry=JudgeProviderRegistry(providers=[admitted])),
    )

    harvester.report(trigger="identity", observation="said Claude", operating_provider="glm")
    written, rejected = harvester.flush(id_prefix="cli-001")

    assert written == ["cli-001-ux-01"]
    assert rejected == []
    bundle = json.loads((tmp_path / "inbox" / "cli-001-ux-01.json").read_text(encoding="utf-8"))
    assert bundle["kind"] == "ux_complaint"
    assert bundle["checkable_criterion"] == "checkable"
    assert bundle["admitting_judge"] == "codex"

    rejector = StubProvider("codex", AdmissionResult(False, "codex", None, "vague"))
    harvester = UxFailureHarvester(
        inbox_dir=tmp_path / "inbox2",
        workdir=tmp_path,
        judge=SecondaryModelJudge(registry=JudgeProviderRegistry(providers=[rejector])),
    )
    harvester.report(trigger="vague", observation="bad", operating_provider="glm")
    written, rejected = harvester.flush(id_prefix="cli-002")

    assert written == []
    assert rejected == ["cli-002-ux-01"]
    assert (tmp_path / "inbox2" / "processed" / "cli-002-ux-01.json.rejected").is_file()


def test_ux_harvester_auto_detects_identity_contradiction(tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = StubProvider("codex", AdmissionResult(True, "codex", "identity fixed", "valid"))
    harvester = UxFailureHarvester(
        inbox_dir=tmp_path / "inbox",
        workdir=tmp_path,
        judge=SecondaryModelJudge(registry=JudgeProviderRegistry(providers=[provider])),
    )

    harvester.observe_turn(
        user_text="what model are you",
        final_text="I'm Claude, made by Anthropic.",
        stop_reason="end_turn",
        error=None,
        tool_activity=[],
        operating_provider="glm",
        model_status="provider: glm",
    )
    written, rejected = harvester.flush(id_prefix="cli-003")

    assert written == ["cli-003-ux-01"]
    assert rejected == []
