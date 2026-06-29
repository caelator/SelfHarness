"""Provider-specific reasoning effort policy for the code CLI."""

from __future__ import annotations

from collections.abc import Sequence

EFFORT_ALIASES = {
    "none": "none",
    "minimal": "minimal",
    "min": "minimal",
    "low": "low",
    "medium": "medium",
    "med": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "x-high": "xhigh",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "extra high": "xhigh",
    "max": "max",
}

SUPPORTED_EFFORTS_BY_PROVIDER = {
    "codex": ("none", "minimal", "low", "medium", "high", "xhigh"),
    "glm": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
    "claude": ("low", "medium", "high", "xhigh", "max"),
}


def normalize_effort(value: str) -> str | None:
    normalized = value.strip().lower().replace("_", "-")
    return EFFORT_ALIASES.get(normalized)


def supported_efforts(provider: str) -> tuple[str, ...]:
    return SUPPORTED_EFFORTS_BY_PROVIDER.get(provider, ())


def resolve_supported_efforts(
    provider: str,
    *,
    discovered_efforts: Sequence[str] | None = None,
    fallback_allowed: bool = True,
) -> tuple[str, ...]:
    baseline = supported_efforts(provider)
    if discovered_efforts:
        discovered = _dedupe_efforts(discovered_efforts)
        if baseline:
            return tuple(effort for effort in discovered if effort in baseline)
        return discovered
    if fallback_allowed:
        return baseline
    return ()


def effort_help(provider: str) -> str:
    supported = supported_efforts(provider)
    if not supported:
        return f"{provider} does not support reasoning effort"
    return ", ".join(supported)


def validate_effort_for_provider(provider: str, effort: str | None) -> str | None:
    if effort is None:
        return None
    supported = supported_efforts(provider)
    if not supported:
        raise ValueError(f"{provider} does not support reasoning effort")
    if effort not in supported:
        raise ValueError(f"effort for {provider} must be one of: {', '.join(supported)}")
    return effort


def valid_effort_or_none(provider: str, effort: str | None) -> str | None:
    try:
        return validate_effort_for_provider(provider, effort)
    except ValueError:
        return None


def _dedupe_efforts(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        effort = normalize_effort(value)
        if effort is None or effort in seen:
            continue
        seen.add(effort)
        out.append(effort)
    return tuple(out)
