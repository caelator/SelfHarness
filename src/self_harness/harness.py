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


def initial_harness() -> HarnessSpec:
    # Verbatim from the paper's Figure 3 (arXiv:2606.09498, page 8): the minimal
    # DeepAgent-based initial harness used as the starting point for Self-Harness.
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


def harness_hash(spec: HarnessSpec) -> str:
    return sha256(stable_json_dumps(spec).encode("utf-8")).hexdigest()


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
