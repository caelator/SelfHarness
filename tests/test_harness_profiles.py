from __future__ import annotations

import json
from pathlib import Path

from self_harness.config import EngineConfig
from self_harness.engine import SelfHarnessEngine
from self_harness.harness import dump_harness_spec, harness_hash, initial_harness
from self_harness.harness_state import (
    effective_harness,
    load_harness_state,
    profile_key,
    register_profile,
    write_harness_state,
)
from self_harness.types import (
    HarnessLayers,
    HarnessOp,
    HarnessOverlay,
    HarnessPatch,
    ProfileRef,
    Proposal,
    ProposerContext,
    RunRecord,
    Split,
    Task,
    TraceEvent,
    VerifierOutcome,
)


def test_v1_harness_state_loads_as_layered_base(tmp_path: Path) -> None:
    path = tmp_path / "harness_state.json"
    base = initial_harness()
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "harness_hash": harness_hash(base),
                "harness": dump_harness_spec(base),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    layers = load_harness_state(path)

    assert harness_hash(layers.base) == harness_hash(base)
    assert layers.provider_overlays == {}
    assert layers.model_overlays == {}


def test_effective_harness_applies_provider_then_model_overlay(tmp_path: Path) -> None:
    profile = ProfileRef(provider="codex", model="gpt-profiled")
    layers = HarnessLayers(
        base=initial_harness(),
        provider_overlays={
            "codex": HarnessOverlay([HarnessOp("AppendToSurface", "bootstrap", "provider guidance")])
        },
        model_overlays={
            profile_key(profile): HarnessOverlay([HarnessOp("AppendToSurface", "bootstrap", "model guidance")])
        },
    )

    effective = effective_harness(layers, profile)

    assert "provider guidance" in effective.bootstrap
    assert "model guidance" in effective.bootstrap
    assert effective.bootstrap.index("provider guidance") < effective.bootstrap.index("model guidance")


def test_register_profile_cold_starts_without_cloning_existing_overlay() -> None:
    old_profile = ProfileRef(provider="codex", model="gpt-old")
    layers = HarnessLayers(
        base=initial_harness(),
        model_overlays={
            profile_key(old_profile): HarnessOverlay([HarnessOp("AppendToSurface", "bootstrap", "old quirk")])
        },
    )

    layers, new_profile, created = register_profile(layers, "codex", "gpt-new")

    assert created is True
    assert layers.model_overlays[profile_key(new_profile)].ops == []
    assert "old quirk" not in effective_harness(layers, new_profile).bootstrap


def test_write_harness_state_preserves_flat_legacy_harness(tmp_path: Path) -> None:
    path = tmp_path / "harness_state.json"
    profile = ProfileRef(provider="codex", model="gpt-profiled")
    layers = HarnessLayers(
        base=initial_harness(),
        model_overlays={
            profile_key(profile): HarnessOverlay([HarnessOp("AppendToSurface", "bootstrap", "profile guidance")])
        },
    )

    write_harness_state(path, layers, active_profile=profile, source_run="run-1", updated_at="2026-06-29T00:00:00Z")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "2.0"
    assert payload["harness"]["bootstrap"] == effective_harness(layers, profile).bootstrap
    assert payload["base"]["bootstrap"] == initial_harness().bootstrap


class _ProfilePatchProposer:
    def __init__(self, profile: ProfileRef, payload: str) -> None:
        self.profile = profile
        self.payload = payload

    def propose(self, context: ProposerContext) -> list[Proposal]:
        return [
            Proposal(
                id=f"r{context.round_index:02d}__profile_patch",
                round_index=context.round_index,
                pattern_id="profile-failure",
                patch=HarnessPatch([HarnessOp("AppendToSurface", "bootstrap", self.payload)]),
                priority=100,
                rationale="target this profile only",
                expected_effect="fix target task",
                regression_risks=[],
                target_profile=self.profile,
            )
        ]


class _BasePatchProposer:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def propose(self, context: ProposerContext) -> list[Proposal]:
        return [
            Proposal(
                id=f"r{context.round_index:02d}__base_patch",
                round_index=context.round_index,
                pattern_id="base-failure",
                patch=HarnessPatch([HarnessOp("AppendToSurface", "bootstrap", self.payload)]),
                priority=100,
                rationale="patch canonical base",
                expected_effect="fix target task",
                regression_risks=[],
            )
        ]


class _KeywordRunner:
    def __init__(self, keyword: str, *, smoke_fails_on_keyword: bool = False) -> None:
        self.keyword = keyword
        self.smoke_fails_on_keyword = smoke_fails_on_keyword

    def run(self, task: Task, harness, attempt_index: int = 0) -> RunRecord:  # type: ignore[no-untyped-def]
        if task.id == "target":
            passed = self.keyword in harness.bootstrap
        elif task.id == "smoke":
            passed = not (self.smoke_fails_on_keyword and self.keyword in harness.bootstrap)
        else:
            passed = True
        return RunRecord(
            task_id=task.id,
            split=task.split,
            passed=passed,
            trace=[TraceEvent("runner", "keyword check")],
            outcome=VerifierOutcome(
                passed=passed,
                terminal_cause="ok" if passed else "assertion",
                causal_status="verified",
                mechanism="keyword",
                message="ok" if passed else "missing keyword",
            ),
            attempt_index=attempt_index,
        )


def test_default_smoke_includes_glm_5_2_alongside_active_profile(tmp_path: Path) -> None:
    active = ProfileRef(provider="codex", model="gpt-profiled")
    engine = SelfHarnessEngine(
        tasks=[
            Task("target", Split.HELD_IN, "missing", "must learn keyword"),
            Task("stable", Split.HELD_OUT, "stable", "always passes"),
        ],
        runner=_KeywordRunner("keyword"),
        proposer=_BasePatchProposer("keyword"),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1, evaluation_repeats=1),
        target_profile=active,
    )

    assert engine.smoke_profiles == [
        active,
        ProfileRef(provider="glm", model="glm-5.2"),
    ]


def test_profile_targeted_proposal_patches_overlay_and_records_lineage(tmp_path: Path) -> None:
    profile = ProfileRef(provider="codex", model="gpt-profiled")
    keyword = "profile-only-keyword"
    engine = SelfHarnessEngine(
        tasks=[
            Task("target", Split.HELD_IN, "missing", "must learn keyword"),
            Task("stable", Split.HELD_OUT, "stable", "always passes"),
        ],
        runner=_KeywordRunner(keyword),
        proposer=_ProfilePatchProposer(profile, keyword),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1, evaluation_repeats=1),
        target_profile=profile,
        smoke_profiles=[profile],
        smoke_tasks=[Task("smoke", Split.HELD_OUT, "smoke", "smoke")],
    )

    summaries = engine.run()
    lineage = json.loads((tmp_path / "lineage.json").read_text(encoding="utf-8"))

    assert summaries[0].accepted == 1
    assert keyword not in engine.layers.base.bootstrap
    assert keyword in effective_harness(engine.layers, profile).bootstrap
    assert lineage[0]["target_profile"] == {"model": "gpt-profiled", "provider": "codex"}
    assert lineage[0]["producer_profile"]["effort"] is None
    assert lineage[0]["certification"]["passed"] is True


def test_profile_smoke_regression_blocks_promotion(tmp_path: Path) -> None:
    profile = ProfileRef(provider="codex", model="gpt-profiled")
    keyword = "profile-breaks-smoke"
    engine = SelfHarnessEngine(
        tasks=[
            Task("target", Split.HELD_IN, "missing", "must learn keyword"),
            Task("stable", Split.HELD_OUT, "stable", "always passes"),
        ],
        runner=_KeywordRunner(keyword, smoke_fails_on_keyword=True),
        proposer=_ProfilePatchProposer(profile, keyword),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1, evaluation_repeats=1),
        target_profile=profile,
        smoke_profiles=[profile],
        smoke_tasks=[Task("smoke", Split.HELD_OUT, "smoke", "smoke")],
    )

    summaries = engine.run()
    rows = [
        json.loads(line)
        for line in (tmp_path / "rounds" / "0" / "proposals.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert summaries[0].accepted == 0
    assert rows[0]["status"] == "rejected"
    assert rows[0]["decision_reason"].startswith("rejected:certification:")
    assert keyword not in effective_harness(engine.layers, profile).bootstrap


def test_default_glm_smoke_regression_blocks_base_promotion(tmp_path: Path) -> None:
    active = ProfileRef(provider="codex", model="gpt-profiled")
    glm = ProfileRef(provider="glm", model="glm-5.2")
    keyword = "base-breaks-glm-smoke"
    engine = SelfHarnessEngine(
        tasks=[
            Task("target", Split.HELD_IN, "missing", "must learn keyword"),
            Task("stable", Split.HELD_OUT, "stable", "always passes"),
        ],
        runner=_KeywordRunner(keyword),
        runner_for=lambda profile: _KeywordRunner(keyword, smoke_fails_on_keyword=profile == glm),
        proposer=_BasePatchProposer(keyword),
        out_dir=tmp_path,
        config=EngineConfig(rounds=1, evaluation_repeats=1),
        target_profile=active,
        smoke_tasks=[Task("smoke", Split.HELD_OUT, "smoke", "smoke")],
    )

    summaries = engine.run()
    rows = [
        json.loads(line)
        for line in (tmp_path / "rounds" / "0" / "proposals.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert summaries[0].accepted == 0
    assert rows[0]["status"] == "rejected"
    assert rows[0]["decision_reason"].startswith("rejected:certification:")
    assert "glm/glm-5.2 smoke regressed" in rows[0]["decision_reason"]
    assert keyword not in engine.layers.base.bootstrap
