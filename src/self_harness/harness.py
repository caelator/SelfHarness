from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any

from self_harness.exceptions import InvalidPatchError
from self_harness.types import HarnessOp, HarnessPatch, HarnessSpec, stable_json_dumps

TEXT_SURFACES = {
    "system_prompt",
    "bootstrap",
    "execution",
    "verification",
    "failure_recovery",
}
LIST_SURFACES = {"tools", "skills", "memory_sources", "subagents"}
POLICY_SURFACES = {"runtime_policy"}
EDITABLE_SURFACES = TEXT_SURFACES | LIST_SURFACES | POLICY_SURFACES
OP_WHITELIST = {"AppendToListSurface", "AppendToSurface", "ReplaceSurface", "SetPolicy"}
SURFACE_KINDS = {
    **{surface: "text" for surface in TEXT_SURFACES},
    **{surface: "list" for surface in LIST_SURFACES},
    "runtime_policy": "policy",
}


def figure_3_harness() -> HarnessSpec:
    """The frozen paper baseline — verbatim from Figure 3 (arXiv:2606.09498, page 8).

    This is the immutable reference point for paper-fidelity checks. It is deliberately OUTSIDE the
    machine-managed marker block below: ``promote-to-source`` only ever rewrites ``initial_harness()``,
    so an evolved/promoted harness never changes what the fidelity fixtures hash against. Tests that
    assert paper fidelity should build their audits from this, not from ``initial_harness()``.
    """

    return HarnessSpec(
        system_prompt=(
            "You are running inside a Terminal Bench 2 Harbor task environment.\n\n"
            "Use the built-in filesystem and shell tools to inspect the workspace, make\n"
            "concrete edits, and verify outcomes against the actual task environment.\n\n"
            "Do not assume synthetic datasets, domain-specific tools, or hidden fixtures\n"
            "unless you discover them in the repo or runtime."
        ),
        bootstrap="Start by inspecting the workspace and identifying the smallest relevant edit surface.",
        execution="Prefer concrete repo changes over generic advice, and keep edits tightly scoped to the task.",
        verification=(
            "Before concluding, verify the result with the most targeted command, file read, "
            "or test you can run."
        ),
        failure_recovery="If a tool call fails, inspect the error and adapt; do not blindly retry the same action.",
        runtime_policy={
            "enabled": False,
            "max_recent_tool_errors": None,
            "max_total_tool_messages": None,
            "instruction": None,
        },
        tools=[],
        skills=[],
        memory_sources=["/AGENTS.md"],
        subagents=[],
    )


# >>> SELF_HARNESS_INITIAL_HARNESS_START (machine-managed; promote-to-source rewrites this block)
def initial_harness() -> HarnessSpec:
    # Promoted from an evolved Self-Harness lineage via the operator console (promote-to-source).
    return HarnessSpec(
        system_prompt='You are running inside a Terminal Bench 2 Harbor task environment.\n\nUse the built-in filesystem and shell tools to inspect the workspace, make\nconcrete edits, and verify outcomes against the actual task environment.\n\nDo not assume synthetic datasets, domain-specific tools, or hidden fixtures\nunless you discover them in the repo or runtime.',  # noqa: E501
        bootstrap='Start by inspecting the workspace and identifying the smallest relevant edit surface.',  # noqa: E501
        execution='Prefer concrete repo changes over generic advice, and keep edits tightly scoped to the task.\nWhen writing any output file, ALWAYS use printf \'%s\' "VALUE" (never echo, never echo -n, never redirect from heredoc, never use write_file) to guarantee no leading/trailing whitespace or stray newlines. Example: printf \'%s\' "9" > answer.txt\nAfter writing any output file, always read it back and confirm its exact bytes match what the task requires before ending your turn.',  # noqa: E501
        verification='Before concluding, verify the result with the most targeted command, file read, or test you can run.\nCRITICAL: When writing output files (answer.txt, etc.), ensure the file contains EXACTLY the expected value with NO leading/trailing whitespace, NO extra blank lines, and NO extra characters. Use printf or echo -n to avoid trailing newlines when the verifier expects exact content. Before finishing, cat the output file and visually confirm it has no stray whitespace.\nFor exact-match output files, after cat-based visual confirmation, also run `wc -c < file` and compare the byte count to the expected length. If the file should contain only "9" with no newline, the byte count must be exactly 1.',  # noqa: E501
        failure_recovery='If a tool call fails, inspect the error and adapt; do not blindly retry the same action.',  # noqa: E501
        runtime_policy={'enabled': True, 'instruction': 'If you are about to end your turn after writing an output file, first read that file back and confirm it contains exactly the expected content with no extra whitespace or characters.', 'max_recent_tool_errors': 3, 'max_total_tool_messages': 100},  # noqa: E501
        tools=[],  # noqa: E501
        skills=[],  # noqa: E501
        memory_sources=['/AGENTS.md'],  # noqa: E501
        subagents=[],  # noqa: E501
    )
# <<< SELF_HARNESS_INITIAL_HARNESS_END


def harness_hash(spec: HarnessSpec) -> str:
    return sha256(stable_json_dumps(spec).encode("utf-8")).hexdigest()


INITIAL_HARNESS_START_MARKER = (
    "# >>> SELF_HARNESS_INITIAL_HARNESS_START (machine-managed; promote-to-source rewrites this block)"
)
INITIAL_HARNESS_END_MARKER = "# <<< SELF_HARNESS_INITIAL_HARNESS_END"


def render_initial_harness_source(spec: HarnessSpec) -> str:
    """Render the ``initial_harness()`` source block (between the sentinel markers) for a given spec.

    Used by the gated promote-to-source action to write an evolved harness back into this module. Output
    is deterministic and ``repr``-based so it re-parses to an identical spec.
    """

    surfaces = dump_harness_spec(spec)
    # Evolved surfaces are arbitrary-length prose, so repr lines routinely exceed the 120-char limit.
    # Tag every field line with noqa so a faithfully-rendered harness always passes the promote gate's
    # ruff check (the content is data, not hand-maintained code).
    nq = "  # noqa: E501"
    lines = [
        INITIAL_HARNESS_START_MARKER,
        "def initial_harness() -> HarnessSpec:",
        "    # Promoted from an evolved Self-Harness lineage via the operator console (promote-to-source).",
        "    return HarnessSpec(",
        f"        system_prompt={surfaces['system_prompt']!r},{nq}",
        f"        bootstrap={surfaces['bootstrap']!r},{nq}",
        f"        execution={surfaces['execution']!r},{nq}",
        f"        verification={surfaces['verification']!r},{nq}",
        f"        failure_recovery={surfaces['failure_recovery']!r},{nq}",
        f"        runtime_policy={surfaces['runtime_policy']!r},{nq}",
        f"        tools={surfaces['tools']!r},{nq}",
        f"        skills={surfaces['skills']!r},{nq}",
        f"        memory_sources={surfaces['memory_sources']!r},{nq}",
        f"        subagents={surfaces['subagents']!r},{nq}",
        "    )",
        INITIAL_HARNESS_END_MARKER,
    ]
    return "\n".join(lines)


_HARNESS_SURFACE_TYPES: dict[str, type] = {
    "system_prompt": str,
    "bootstrap": str,
    "execution": str,
    "verification": str,
    "failure_recovery": str,
    "runtime_policy": dict,
    "tools": list,
    "skills": list,
    "memory_sources": list,
    "subagents": list,
}


def dump_harness_spec(spec: HarnessSpec) -> dict[str, Any]:
    """Serialize a HarnessSpec to the canonical surface dict.

    This matches the ``harness_before.json`` / ``harness_after.json`` snapshot shape written per round,
    so a dumped spec round-trips through ``load_harness_spec`` and through the audit verifier.
    """

    return {
        "system_prompt": spec.system_prompt,
        "bootstrap": spec.bootstrap,
        "execution": spec.execution,
        "verification": spec.verification,
        "failure_recovery": spec.failure_recovery,
        "runtime_policy": dict(spec.runtime_policy),
        "tools": list(spec.tools),
        "skills": list(spec.skills),
        "memory_sources": list(spec.memory_sources),
        "subagents": [dict(item) for item in spec.subagents],
    }


def load_harness_spec(value: dict[str, Any]) -> HarnessSpec:
    """Reconstruct a HarnessSpec from a surface dict, validating every surface's type.

    Raises ``InvalidPatchError`` on any missing or wrongly-typed surface. The audit verifier reuses this
    so persisted/evolved harness state and audited snapshots share one validation path.
    """

    for key, expected_type in _HARNESS_SURFACE_TYPES.items():
        if key not in value or not isinstance(value[key], expected_type):
            raise InvalidPatchError(f"harness snapshot missing valid {key}")
    if not all(isinstance(item, str) for item in value["tools"]):
        raise InvalidPatchError("harness tools must be strings")
    if not all(isinstance(item, str) for item in value["skills"]):
        raise InvalidPatchError("harness skills must be strings")
    if not all(isinstance(item, str) for item in value["memory_sources"]):
        raise InvalidPatchError("harness memory_sources must be strings")
    if not all(isinstance(item, dict) for item in value["subagents"]):
        raise InvalidPatchError("harness subagents must be objects")
    return HarnessSpec(
        system_prompt=value["system_prompt"],
        bootstrap=value["bootstrap"],
        execution=value["execution"],
        verification=value["verification"],
        failure_recovery=value["failure_recovery"],
        runtime_policy=dict(value["runtime_policy"]),
        tools=list(value["tools"]),
        skills=list(value["skills"]),
        memory_sources=list(value["memory_sources"]),
        subagents=[dict(item) for item in value["subagents"]],
    )


def validate_op(op: HarnessOp) -> None:
    if op.op not in OP_WHITELIST:
        raise InvalidPatchError(f"unsupported harness op: {op.op}")
    if op.surface not in EDITABLE_SURFACES:
        raise InvalidPatchError(f"surface is not editable: {op.surface}")
    if op.op == "AppendToSurface" and op.surface not in TEXT_SURFACES:
        raise InvalidPatchError("AppendToSurface can only target text surfaces")
    if op.op == "AppendToListSurface" and op.surface not in LIST_SURFACES:
        raise InvalidPatchError("AppendToListSurface can only target list surfaces")
    if op.op == "SetPolicy" and op.surface != "runtime_policy":
        raise InvalidPatchError("SetPolicy can only target runtime_policy")
    if op.op == "AppendToSurface" and not isinstance(op.payload, str):
        raise InvalidPatchError("AppendToSurface payload must be a string")
    if op.op == "AppendToListSurface":
        _validate_list_payload(op.surface, op.payload)
    if op.op == "ReplaceSurface" and not _is_valid_replacement_payload(op.surface, op.payload):
        raise InvalidPatchError("surface replacement payload must be a string or dict")
    if op.op == "SetPolicy" and not isinstance(op.payload, dict):
        raise InvalidPatchError("SetPolicy payload must be a dict")


def apply_op(spec: HarnessSpec, op: HarnessOp) -> tuple[HarnessSpec, HarnessOp]:
    validate_op(op)
    current = getattr(spec, op.surface)
    if op.op == "AppendToSurface":
        assert isinstance(current, str)
        payload = str(op.payload).strip()
        next_text = current if not payload else current.rstrip() + "\n" + payload
        return _replace_surface(spec, op.surface, next_text), HarnessOp(
            "ReplaceSurface",
            op.surface,
            current,
        )
    if op.op == "AppendToListSurface":
        if not isinstance(current, list):
            raise InvalidPatchError("AppendToListSurface can only target list surfaces")
        next_list = list(current) + [op.payload]
        return _replace_surface(spec, op.surface, next_list), HarnessOp(
            "ReplaceSurface",
            op.surface,
            list(current),
        )
    if op.op == "ReplaceSurface":
        return _replace_surface(spec, op.surface, op.payload), HarnessOp(
            "ReplaceSurface",
            op.surface,
            current,
        )
    if op.op == "SetPolicy":
        next_policy = dict(spec.runtime_policy)
        next_policy.update(op.payload)
        return replace(spec, runtime_policy=next_policy), HarnessOp(
            "ReplaceSurface",
            "runtime_policy",
            dict(spec.runtime_policy),
        )
    raise InvalidPatchError(f"unsupported harness op: {op.op}")


def _replace_surface(spec: HarnessSpec, surface: str, value: Any) -> HarnessSpec:
    if surface == "system_prompt":
        return replace(spec, system_prompt=_require_text(value, surface))
    if surface == "bootstrap":
        return replace(spec, bootstrap=_require_text(value, surface))
    if surface == "execution":
        return replace(spec, execution=_require_text(value, surface))
    if surface == "verification":
        return replace(spec, verification=_require_text(value, surface))
    if surface == "failure_recovery":
        return replace(spec, failure_recovery=_require_text(value, surface))
    if surface == "runtime_policy":
        if not isinstance(value, dict):
            raise InvalidPatchError("runtime_policy replacement payload must be a dict")
        return replace(spec, runtime_policy=dict(value))
    if surface == "tools":
        return replace(spec, tools=_require_string_list(value, surface))
    if surface == "skills":
        return replace(spec, skills=_require_string_list(value, surface))
    if surface == "memory_sources":
        return replace(spec, memory_sources=_require_string_list(value, surface))
    if surface == "subagents":
        return replace(spec, subagents=_require_dict_list(value, surface))
    raise InvalidPatchError(f"surface is not editable: {surface}")


def _require_text(value: Any, surface: str) -> str:
    if not isinstance(value, str):
        raise InvalidPatchError(f"{surface} replacement payload must be a string")
    return value


def _require_string_list(value: Any, surface: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidPatchError(f"{surface} replacement payload must be a list of strings")
    return list(value)


def _require_dict_list(value: Any, surface: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise InvalidPatchError(f"{surface} replacement payload must be a list of dicts")
    return [dict(item) for item in value]


def _validate_list_payload(surface: str, payload: Any) -> None:
    if surface in {"tools", "skills", "memory_sources"} and not isinstance(payload, str):
        raise InvalidPatchError(f"{surface} append payload must be a string")
    if surface == "subagents" and not isinstance(payload, dict):
        raise InvalidPatchError("subagents append payload must be a dict")


def _is_valid_replacement_payload(surface: str, payload: Any) -> bool:
    if surface in TEXT_SURFACES:
        return isinstance(payload, str)
    if surface in POLICY_SURFACES:
        return isinstance(payload, dict)
    if surface in {"tools", "skills", "memory_sources"}:
        return isinstance(payload, list) and all(isinstance(item, str) for item in payload)
    if surface == "subagents":
        return isinstance(payload, list) and all(isinstance(item, dict) for item in payload)
    return False


def apply_patch(spec: HarnessSpec, patch: HarnessPatch) -> tuple[HarnessSpec, HarnessPatch]:
    next_spec = spec
    reverse_ops: list[HarnessOp] = []
    for op in patch.ops:
        next_spec, reverse_op = apply_op(next_spec, op)
        reverse_ops.insert(0, reverse_op)
    return next_spec, HarnessPatch(reverse_ops)


def structurally_mergeable(left: HarnessPatch, right: HarnessPatch) -> bool:
    for left_op in left.ops:
        for right_op in right.ops:
            if not _ops_mergeable(left_op, right_op):
                return False
    return True


def merge_patches(patches: list[HarnessPatch]) -> HarnessPatch:
    merged: list[HarnessOp] = []
    for patch in patches:
        for op in patch.ops:
            validate_op(op)
            merged.append(op)
    return HarnessPatch(merged)


def patch_surface_key(patch: HarnessPatch) -> str:
    if not patch.ops:
        return ""
    return patch.ops[0].surface


def _ops_mergeable(left: HarnessOp, right: HarnessOp) -> bool:
    validate_op(left)
    validate_op(right)
    if left.surface != right.surface:
        return True
    return (left.op == right.op == "AppendToSurface") or (
        left.op == right.op == "AppendToListSurface"
    )


def op_to_audit(op: HarnessOp) -> dict[str, Any]:
    return {"op": op.op, "surface": op.surface, "payload": op.payload}
