from pathlib import Path

from self_harness.harness import (
    apply_patch,
    dump_harness_spec,
    initial_harness,
    load_harness_spec,
    render_initial_harness_source,
    structurally_mergeable,
)
from self_harness.types import HarnessOp, HarnessPatch, stable_json_dumps


def test_patch_reversal_round_trip() -> None:
    spec = initial_harness()
    patch = HarnessPatch([HarnessOp("AppendToSurface", "bootstrap", "Create explicit artifacts early.")])

    changed, reverse = apply_patch(spec, patch)
    restored, _ = apply_patch(changed, reverse)

    assert changed != spec
    assert restored == spec


def test_default_harness_declares_paper_aligned_surfaces() -> None:
    spec = initial_harness()

    assert spec.tools == []
    assert spec.skills == []
    assert spec.memory_sources == ["/AGENTS.md"]
    assert spec.subagents == []


def test_merge_structural_rules() -> None:
    bootstrap = HarnessPatch([HarnessOp("AppendToSurface", "bootstrap", "A")])
    bootstrap_2 = HarnessPatch([HarnessOp("AppendToSurface", "bootstrap", "B")])
    execution = HarnessPatch([HarnessOp("AppendToSurface", "execution", "C")])
    replace_bootstrap = HarnessPatch([HarnessOp("ReplaceSurface", "bootstrap", "D")])

    assert structurally_mergeable(bootstrap, bootstrap_2)
    assert structurally_mergeable(bootstrap, execution)
    assert not structurally_mergeable(bootstrap, replace_bootstrap)


def test_list_surface_patch_reversal_round_trip() -> None:
    spec = initial_harness()
    patch = HarnessPatch([HarnessOp("AppendToListSurface", "skills", "artifact-recovery")])

    changed, reverse = apply_patch(spec, patch)
    restored, _ = apply_patch(changed, reverse)

    assert changed.skills == ["artifact-recovery"]
    assert restored == spec


def test_list_surface_payload_validation() -> None:
    spec = initial_harness()

    invalid_cases = [
        HarnessPatch([HarnessOp("AppendToListSurface", "subagents", "not-a-dict")]),
        HarnessPatch([HarnessOp("AppendToListSurface", "tools", {"name": "bash"})]),
        HarnessPatch([HarnessOp("AppendToSurface", "skills", "wrong-op")]),
        HarnessPatch([HarnessOp("AppendToListSurface", "bootstrap", "wrong-op")]),
    ]

    for patch in invalid_cases:
        try:
            apply_patch(spec, patch)
        except ValueError:
            pass
        else:
            raise AssertionError("expected invalid list surface patch to fail")


def test_list_surface_merge_rules() -> None:
    skills_a = HarnessPatch([HarnessOp("AppendToListSurface", "skills", "a")])
    skills_b = HarnessPatch([HarnessOp("AppendToListSurface", "skills", "b")])
    replace_skills = HarnessPatch([HarnessOp("ReplaceSurface", "skills", [])])

    assert structurally_mergeable(skills_a, skills_b)
    assert not structurally_mergeable(skills_a, replace_skills)


def test_subagent_serialization_is_stable() -> None:
    spec = initial_harness()
    patch = HarnessPatch([HarnessOp("AppendToListSurface", "subagents", {"id": "worker", "role": "test"})])
    changed, _ = apply_patch(spec, patch)

    assert stable_json_dumps(changed) == stable_json_dumps(changed)


def test_non_declared_surface_is_rejected() -> None:
    spec = initial_harness()
    patch = HarnessPatch([HarnessOp("AppendToSurface", "hidden_rule", "nope")])

    try:
        apply_patch(spec, patch)
    except ValueError as exc:
        assert "surface is not editable" in str(exc)
    else:
        raise AssertionError("expected invalid patch to fail")


def test_harness_spec_dump_load_round_trip() -> None:
    spec = initial_harness()
    surfaces = dump_harness_spec(spec)
    assert set(surfaces) == {
        "system_prompt", "bootstrap", "execution", "verification", "failure_recovery",
        "runtime_policy", "tools", "skills", "memory_sources", "subagents",
    }
    assert load_harness_spec(surfaces) == spec


def test_load_harness_spec_rejects_bad_surface() -> None:
    surfaces = dump_harness_spec(initial_harness())
    surfaces["system_prompt"] = 123
    try:
        load_harness_spec(surfaces)
    except ValueError as exc:
        assert "system_prompt" in str(exc)
    else:
        raise AssertionError("expected invalid harness surface to fail")


def test_render_initial_harness_source_reparses_to_same_spec() -> None:
    spec = initial_harness()
    patch = HarnessPatch([HarnessOp("AppendToSurface", "bootstrap", "Create explicit artifacts early.")])
    evolved, _ = apply_patch(spec, patch)

    source = render_initial_harness_source(evolved)
    # The rendered block must be a self-contained, re-parseable initial_harness() definition.
    namespace: dict[str, object] = {}
    preamble = "from self_harness.types import HarnessSpec\n"
    body = source.replace(
        "# >>> SELF_HARNESS_INITIAL_HARNESS_START (machine-managed; promote-to-source rewrites this block)",
        "",
    ).replace("# <<< SELF_HARNESS_INITIAL_HARNESS_END", "")
    exec(preamble + body, namespace)  # noqa: S102 - exercising the generated source on purpose
    rebuilt = namespace["initial_harness"]()  # type: ignore[operator]
    assert dump_harness_spec(rebuilt) == dump_harness_spec(evolved)


def test_render_initial_harness_source_passes_ruff_with_long_surface(tmp_path: Path) -> None:
    # An evolved surface is arbitrary-length prose; the rendered repr line routinely exceeds 120 chars.
    # The promote-to-source gate runs ruff, so every field line must carry noqa or the gate fails (which
    # is exactly what blocked a real auto-promotion until the renderer was fixed).
    import subprocess
    import sys

    spec = initial_harness()
    long_text = (
        "Before concluding, verify the result with the most targeted command. "
        "When writing a single-value answer to a file (e.g. answer.txt, sum.txt), use printf '%s' "
        "instead of echo to avoid trailing newlines; confirm with xxd <file> | tail -1."
    )
    patch = HarnessPatch([HarnessOp("AppendToSurface", "verification", long_text)])
    evolved, _ = apply_patch(spec, patch)

    module = tmp_path / "rendered_harness.py"
    body = render_initial_harness_source(evolved)
    module.write_text("from self_harness.types import HarnessSpec\n\n\n" + body + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "E501", str(module)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"ruff E501 failed on rendered harness:\n{result.stdout}\n{result.stderr}"
