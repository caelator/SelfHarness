"""Provider-specific reasoning effort policy for the code CLI."""

from __future__ import annotations

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
    "claude": ("low", "medium", "high", "xhigh", "max"),
}


def normalize_effort(value: str) -> str | None:
    normalized = value.strip().lower().replace("_", "-")
    return EFFORT_ALIASES.get(normalized)


def supported_efforts(provider: str) -> tuple[str, ...]:
    return SUPPORTED_EFFORTS_BY_PROVIDER.get(provider, ())


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
