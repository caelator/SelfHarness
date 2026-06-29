from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from self_harness.exceptions import InvalidPatchError
from self_harness.harness import apply_patch, dump_harness_spec, harness_hash, initial_harness, load_harness_spec
from self_harness.types import (
    HarnessLayers,
    HarnessOp,
    HarnessOverlay,
    HarnessPatch,
    HarnessSpec,
    ProfileRef,
    SmokeCertification,
    stable_json_dumps,
    to_jsonable,
)

STATE_SCHEMA_VERSION = "2.0"
DEFAULT_PROFILE_MODEL = "provider-default"
DEFAULT_SMOKE_CORPUS_REF = "examples/agentic_corpus.json#held_out"


def make_profile_ref(provider: str, model: str | None) -> ProfileRef:
    provider_id = provider.strip().lower().replace("_", "-")
    model_id = model.strip() if isinstance(model, str) and model.strip() else DEFAULT_PROFILE_MODEL
    return ProfileRef(provider=provider_id, model=model_id)


def profile_key(profile: ProfileRef) -> str:
    return f"{profile.provider}/{profile.model}"


def load_harness_state(path: Path) -> HarnessLayers:
    if not path.is_file():
        return HarnessLayers(base=initial_harness())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, InvalidPatchError):
        return HarnessLayers(base=initial_harness())
    if not isinstance(payload, dict):
        return HarnessLayers(base=initial_harness())
    return load_harness_state_payload(payload)


def load_harness_state_payload(payload: dict[str, Any]) -> HarnessLayers:
    base_payload = payload.get("base")
    if not isinstance(base_payload, dict):
        base_payload = payload.get("harness")
    base = load_harness_spec(base_payload) if isinstance(base_payload, dict) else initial_harness()

    overlays_payload = payload.get("overlays")
    provider_overlays: dict[str, HarnessOverlay] = {}
    model_overlays: dict[str, HarnessOverlay] = {}
    if isinstance(overlays_payload, dict):
        provider_overlays = _overlay_map_from_payload(overlays_payload.get("providers"))
        model_overlays = _overlay_map_from_payload(overlays_payload.get("models"))

    certified_profiles = _profiles_from_payload(payload.get("certified_profiles"))
    smoke_corpus_ref = payload.get("smoke_corpus_ref")
    return HarnessLayers(
        base=base,
        provider_overlays=provider_overlays,
        model_overlays=model_overlays,
        certified_profiles=certified_profiles,
        smoke_corpus_ref=(
            smoke_corpus_ref
            if isinstance(smoke_corpus_ref, str) and smoke_corpus_ref
            else DEFAULT_SMOKE_CORPUS_REF
        ),
    )


def write_harness_state(
    path: Path,
    layers: HarnessLayers,
    *,
    active_profile: ProfileRef | None = None,
    source_run: str | None = None,
    updated_at: str | None = None,
) -> None:
    legacy_harness = effective_harness(layers, active_profile) if active_profile is not None else layers.base
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": updated_at or _now(),
        "source_run": source_run,
        "active_profile": to_jsonable(active_profile) if active_profile is not None else None,
        "harness_hash": harness_hash(legacy_harness),
        "harness": dump_harness_spec(legacy_harness),
        "base": dump_harness_spec(layers.base),
        "overlays": {
            "providers": _overlay_map_to_payload(layers.provider_overlays),
            "models": _overlay_map_to_payload(layers.model_overlays),
        },
        "certified_profiles": to_jsonable(layers.certified_profiles),
        "smoke_corpus_ref": layers.smoke_corpus_ref,
        "lineage_tail": to_jsonable(layers.lineage_tail[-10:]),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")


def effective_harness(layers: HarnessLayers, profile: ProfileRef | None) -> HarnessSpec:
    spec = layers.base
    if profile is None:
        return spec
    provider_overlay = layers.provider_overlays.get(profile.provider)
    if provider_overlay is not None and provider_overlay.ops:
        spec = _apply_ops(spec, provider_overlay.ops)
    model_overlay = layers.model_overlays.get(profile_key(profile))
    if model_overlay is not None and model_overlay.ops:
        spec = _apply_ops(spec, model_overlay.ops)
    return spec


def register_profile(layers: HarnessLayers, provider: str, model: str | None) -> tuple[HarnessLayers, ProfileRef, bool]:
    profile = make_profile_ref(provider, model)
    provider_overlays = dict(layers.provider_overlays)
    model_overlays = dict(layers.model_overlays)
    created = False
    if profile.provider not in provider_overlays:
        provider_overlays[profile.provider] = HarnessOverlay()
        created = True
    key = profile_key(profile)
    if key not in model_overlays:
        model_overlays[key] = HarnessOverlay()
        created = True
    if not created:
        return layers, profile, False
    return replace(layers, provider_overlays=provider_overlays, model_overlays=model_overlays), profile, True


def apply_patch_to_layers(
    layers: HarnessLayers,
    patch: HarnessPatch,
    target_profile: ProfileRef | None,
) -> tuple[HarnessLayers, HarnessPatch, HarnessSpec]:
    if target_profile is None:
        candidate, reverse = apply_patch(layers.base, patch)
        return replace(layers, base=candidate), reverse, candidate

    layers, profile, _created = register_profile(layers, target_profile.provider, target_profile.model)
    current = effective_harness(layers, profile)
    candidate, reverse = apply_patch(current, patch)
    key = profile_key(profile)
    current_overlay = layers.model_overlays.get(key, HarnessOverlay())
    updated_overlay = replace(current_overlay, ops=[*current_overlay.ops, *patch.ops])
    model_overlays = dict(layers.model_overlays)
    model_overlays[key] = updated_overlay
    return replace(layers, model_overlays=model_overlays), reverse, candidate


def mark_profile_certified(
    layers: HarnessLayers,
    profile: ProfileRef | None,
    certification: SmokeCertification | None,
) -> HarnessLayers:
    if profile is None:
        return layers
    layers, profile, _created = register_profile(layers, profile.provider, profile.model)
    certified_profiles = list(layers.certified_profiles)
    if profile not in certified_profiles:
        certified_profiles.append(profile)
    key = profile_key(profile)
    overlay = layers.model_overlays.get(key, HarnessOverlay())
    model_overlays = dict(layers.model_overlays)
    model_overlays[key] = replace(
        overlay,
        certified_at=_now(),
        certified_by_smoke=certification,
    )
    return replace(layers, certified_profiles=certified_profiles, model_overlays=model_overlays)


def _apply_ops(spec: HarnessSpec, ops: list[HarnessOp]) -> HarnessSpec:
    patched, _reverse = apply_patch(spec, HarnessPatch(list(ops)))
    return patched


def _overlay_map_from_payload(value: Any) -> dict[str, HarnessOverlay]:
    if not isinstance(value, dict):
        return {}
    overlays: dict[str, HarnessOverlay] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not isinstance(raw, dict):
            continue
        ops = _ops_from_payload(raw.get("ops"))
        certification = _certification_from_payload(raw.get("certified_by_smoke"))
        certified_at = raw.get("certified_at")
        overlays[key] = HarnessOverlay(
            ops=ops,
            certified_at=certified_at if isinstance(certified_at, str) else None,
            certified_by_smoke=certification,
        )
    return overlays


def _overlay_map_to_payload(overlays: dict[str, HarnessOverlay]) -> dict[str, dict[str, Any]]:
    return {key: to_jsonable(overlay) for key, overlay in sorted(overlays.items())}


def _ops_from_payload(value: Any) -> list[HarnessOp]:
    if not isinstance(value, list):
        return []
    ops: list[HarnessOp] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        op_name = item.get("op")
        surface = item.get("surface")
        if isinstance(op_name, str) and isinstance(surface, str) and "payload" in item:
            ops.append(HarnessOp(op=op_name, surface=surface, payload=item["payload"]))
    return ops


def _profiles_from_payload(value: Any) -> list[ProfileRef]:
    if not isinstance(value, list):
        return []
    profiles: list[ProfileRef] = []
    for item in value:
        profile = _profile_from_payload(item)
        if profile is not None and profile not in profiles:
            profiles.append(profile)
    return profiles


def _profile_from_payload(value: Any) -> ProfileRef | None:
    if not isinstance(value, dict):
        return None
    provider = value.get("provider")
    model = value.get("model")
    if not isinstance(provider, str) or not isinstance(model, str):
        return None
    return make_profile_ref(provider, model)


def _certification_from_payload(value: Any) -> SmokeCertification | None:
    if not isinstance(value, dict):
        return None
    corpus_ref = value.get("corpus_ref")
    passed = value.get("passed")
    reason = value.get("reason")
    tolerance = value.get("tolerance")
    return SmokeCertification(
        profiles=_profiles_from_payload(value.get("profiles")),
        corpus_ref=corpus_ref if isinstance(corpus_ref, str) else "",
        tolerance=tolerance if isinstance(tolerance, int) else 0,
        passed=passed if isinstance(passed, bool) else False,
        reason=reason if isinstance(reason, str) else "",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
