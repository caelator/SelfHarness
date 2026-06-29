"""Runtime model catalog discovery for `self-harness code`.

The interactive model picker should reflect the models a provider currently serves, not a baked-in
list that goes stale. Discovery is intentionally best-effort and side-effect free: try a provider's
native CLI catalog command when it exists, then the provider API when credentials are available, and
return a clear error for the UI to show when enumeration is not supported.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from self_harness.agentic_session import resolve_zai_api_key, resolve_zai_base_url
from self_harness.exceptions import AgenticRunnerError


@dataclass(frozen=True)
class ModelCatalog:
    models: tuple[str, ...]
    source: str
    error: str | None = None


@dataclass(frozen=True)
class EffortCatalog:
    efforts: tuple[str, ...]
    source: str
    error: str | None = None
    fallback_allowed: bool = True


def discover_provider_models(
    provider: str,
    *,
    binary: str | None = None,
    timeout_seconds: float = 8.0,
    env: Mapping[str, str] | None = None,
) -> ModelCatalog:
    """Return the currently served model names/ids for ``provider`` when the provider exposes them."""

    normalized = provider.strip().lower().replace("_", "-")
    source_env = env if env is not None else os.environ
    if normalized == "agy":
        return _first_catalog(
            _query_cli_models(binary or "agy", timeout_seconds=timeout_seconds, env=source_env),
        )
    if normalized == "codex":
        return _first_catalog(
            _query_codex_models_cache(env=source_env, use_user_home=env is None),
            _query_openai_models(timeout_seconds=timeout_seconds, env=source_env),
        )
    if normalized == "claude":
        return _query_anthropic_models(timeout_seconds=timeout_seconds, env=source_env)
    if normalized == "glm":
        return _query_zai_models(timeout_seconds=timeout_seconds, env=source_env)
    return ModelCatalog((), "unknown provider", f"unsupported provider: {provider}")


def discover_provider_efforts(
    provider: str,
    *,
    model: str | None = None,
    binary: str | None = None,
    timeout_seconds: float = 8.0,
    env: Mapping[str, str] | None = None,
) -> EffortCatalog:
    """Return effort levels for ``provider``/``model`` when the provider advertises them."""

    normalized = provider.strip().lower().replace("_", "-")
    source_env = env if env is not None else os.environ
    if normalized == "codex":
        return _query_codex_efforts(model, env=source_env, use_user_home=env is None)
    if normalized == "claude":
        return _query_claude_efforts(binary or "claude", timeout_seconds=timeout_seconds, env=source_env)
    if normalized == "glm":
        return _query_zai_efforts(model, timeout_seconds=timeout_seconds, env=source_env)
    if normalized == "agy":
        return EffortCatalog(
            (),
            "agy models",
            "Agy model choices encode effort in the model name; no separate effort flag is advertised",
            fallback_allowed=False,
        )
    return EffortCatalog((), "unknown provider", f"unsupported provider: {provider}", fallback_allowed=False)


def _first_catalog(*catalogs: ModelCatalog) -> ModelCatalog:
    errors: list[str] = []
    for catalog in catalogs:
        if catalog.models:
            return catalog
        if catalog.error:
            errors.append(catalog.error)
    detail = "; ".join(errors) if errors else "provider did not return any models"
    source = catalogs[-1].source if catalogs else "model discovery"
    return ModelCatalog((), source, detail)


def _query_cli_models(
    binary: str,
    *,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> ModelCatalog:
    try:
        completed = subprocess.run(
            [binary, "models"],
            env=dict(env),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return ModelCatalog((), f"{binary} models", f"{binary!r} binary not found")
    except subprocess.TimeoutExpired:
        return ModelCatalog((), f"{binary} models", f"{binary} models timed out after {timeout_seconds:g}s")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        return ModelCatalog((), f"{binary} models", f"{binary} models exited {completed.returncode}{suffix}")
    models = _parse_models_text(completed.stdout)
    if not models:
        return ModelCatalog((), f"{binary} models", f"{binary} models returned no parseable models")
    return ModelCatalog(models, f"{binary} models")


def _query_openai_models(*, timeout_seconds: float, env: Mapping[str, str]) -> ModelCatalog:
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        return ModelCatalog((), "OpenAI /v1/models", "OPENAI_API_KEY is not set")
    base_url = env.get("OPENAI_BASE_URL") or env.get("OPENAI_API_BASE") or "https://api.openai.com/v1"
    url = _join_api_path(base_url, "models")
    return _query_bearer_models(url, api_key=api_key, source="OpenAI /v1/models", timeout_seconds=timeout_seconds)


def _query_codex_models_cache(*, env: Mapping[str, str], use_user_home: bool) -> ModelCatalog:
    path = _codex_models_cache_path(env, use_user_home=use_user_home)
    if path is None:
        return ModelCatalog((), "Codex models cache", "Codex models cache path is unavailable")
    if not path.is_file():
        return ModelCatalog((), "Codex models cache", f"Codex models cache not found at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ModelCatalog((), "Codex models cache", f"Codex models cache is unreadable: {exc}")
    models = _parse_models_payload(payload)
    if not models:
        return ModelCatalog((), "Codex models cache", "Codex models cache returned no parseable models")
    return ModelCatalog(models, "Codex models cache")


def _query_codex_efforts(
    model: str | None,
    *,
    env: Mapping[str, str],
    use_user_home: bool,
) -> EffortCatalog:
    if not model:
        return EffortCatalog((), "Codex models cache", "Codex effort discovery needs a selected model")
    path = _codex_models_cache_path(env, use_user_home=use_user_home)
    if path is None:
        return EffortCatalog((), "Codex models cache", "Codex models cache path is unavailable")
    if not path.is_file():
        return EffortCatalog((), "Codex models cache", f"Codex models cache not found at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return EffortCatalog((), "Codex models cache", f"Codex models cache is unreadable: {exc}")
    item = _find_model_payload(payload, model)
    if item is None:
        return EffortCatalog((), "Codex models cache", f"Codex models cache has no metadata for {model!r}")
    efforts, effort_field_present = _efforts_from_model_mapping(item)
    if efforts:
        return EffortCatalog(efforts, "Codex models cache")
    return EffortCatalog(
        (),
        "Codex models cache",
        f"Codex models cache has no effort metadata for {model!r}",
        fallback_allowed=not effort_field_present,
    )


def _codex_models_cache_path(env: Mapping[str, str], *, use_user_home: bool) -> Path | None:
    codex_home = env.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "models_cache.json"
    if use_user_home:
        return Path.home() / ".codex" / "models_cache.json"
    return None


def _query_anthropic_models(*, timeout_seconds: float, env: Mapping[str, str]) -> ModelCatalog:
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ModelCatalog((), "Anthropic /v1/models", "ANTHROPIC_API_KEY is not set")
    url = _join_api_path(env.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"), "v1/models")
    request = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "anthropic-version": env.get("ANTHROPIC_VERSION", "2023-06-01"),
        },
    )
    return _query_models_request(request, source="Anthropic /v1/models", timeout_seconds=timeout_seconds)


def _query_claude_efforts(
    binary: str,
    *,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> EffortCatalog:
    try:
        completed = subprocess.run(
            [binary, "--help"],
            env=dict(env),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return EffortCatalog((), f"{binary} --help", f"{binary!r} binary not found")
    except subprocess.TimeoutExpired:
        return EffortCatalog((), f"{binary} --help", f"{binary} --help timed out after {timeout_seconds:g}s")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        return EffortCatalog((), f"{binary} --help", f"{binary} --help exited {completed.returncode}{suffix}")
    efforts = _parse_cli_effort_help(completed.stdout)
    if not efforts:
        return EffortCatalog((), f"{binary} --help", f"{binary} --help returned no parseable effort levels")
    return EffortCatalog(efforts, f"{binary} --help")


def _query_zai_models(*, timeout_seconds: float, env: Mapping[str, str]) -> ModelCatalog:
    try:
        api_key = resolve_zai_api_key(env)
    except AgenticRunnerError as exc:
        return ModelCatalog((), "Z.ai /models", str(exc))
    models_url = env.get("ZAI_MODELS_URL") or _zai_models_url(resolve_zai_base_url(env))
    return _query_bearer_models(models_url, api_key=api_key, source="Z.ai /models", timeout_seconds=timeout_seconds)


def _query_zai_efforts(
    model: str | None,
    *,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> EffortCatalog:
    if not model:
        model = "glm-5.2"
    catalog = _query_zai_models(timeout_seconds=timeout_seconds, env=env)
    if not catalog.models:
        return EffortCatalog((), catalog.source, catalog.error)
    if model not in catalog.models:
        return EffortCatalog((), catalog.source, f"Z.ai /models has no model {model!r}")
    if _zai_model_supports_reasoning_effort(model):
        return EffortCatalog(("none", "minimal", "low", "medium", "high", "xhigh", "max"), catalog.source)
    return EffortCatalog(
        (),
        catalog.source,
        f"{model} is listed by Z.ai but does not advertise GLM-5.2 reasoning effort support",
        fallback_allowed=False,
    )


def _query_bearer_models(
    url: str,
    *,
    api_key: str,
    source: str,
    timeout_seconds: float,
) -> ModelCatalog:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    return _query_models_request(request, source=source, timeout_seconds=timeout_seconds)


def _query_models_request(
    request: urllib.request.Request,
    *,
    source: str,
    timeout_seconds: float,
) -> ModelCatalog:
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip().splitlines()
        suffix = f": {detail[0][:160]}" if detail else ""
        return ModelCatalog((), source, f"{source} returned HTTP {exc.code}{suffix}")
    except urllib.error.URLError as exc:
        return ModelCatalog((), source, f"{source} unreachable: {exc.reason}")
    except TimeoutError:
        return ModelCatalog((), source, f"{source} timed out after {timeout_seconds:g}s")
    except (OSError, json.JSONDecodeError) as exc:
        return ModelCatalog((), source, f"{source} returned an invalid model catalog: {exc}")
    models = _parse_models_payload(payload)
    if not models:
        return ModelCatalog((), source, f"{source} returned no parseable models")
    return ModelCatalog(models, source)


def _parse_models_payload(payload: Any) -> tuple[str, ...]:
    if isinstance(payload, dict):
        for key in ("data", "models"):
            value = payload.get(key)
            parsed = _parse_models_payload(value)
            if parsed:
                return parsed
        return _dedupe_model_names(_model_name_from_mapping(payload))
    if isinstance(payload, list):
        names: list[str] = []
        for item in payload:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, Mapping):
                names.extend(_model_name_from_mapping(item))
        return _dedupe_model_names(names)
    return ()


def _find_model_payload(payload: Any, model: str) -> Mapping[str, Any] | None:
    wanted = model.strip()
    if not wanted:
        return None
    for item in _iter_model_mappings(payload):
        names = _model_name_from_mapping(item)
        display_name = item.get("display_name")
        if isinstance(display_name, str):
            names.append(display_name)
        if wanted in names:
            return item
    return None


def _iter_model_mappings(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        items: list[Mapping[str, Any]] = []
        for key in ("data", "models"):
            items.extend(_iter_model_mappings(payload.get(key)))
        if _model_name_from_mapping(payload):
            items.append(payload)
        return items
    if isinstance(payload, list):
        out: list[Mapping[str, Any]] = []
        for item in payload:
            out.extend(_iter_model_mappings(item))
        return out
    return []


def _model_name_from_mapping(item: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("id", "model", "name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            names.append(value)
    slug = item.get("slug")
    if isinstance(slug, str) and slug.strip():
        names.insert(0, slug)
    return names


def _efforts_from_model_mapping(item: Mapping[str, Any]) -> tuple[tuple[str, ...], bool]:
    field_present = False
    values: list[str] = []
    for key in (
        "supported_reasoning_levels",
        "supported_reasoning_efforts",
        "reasoning_efforts",
        "supported_efforts",
        "efforts",
    ):
        if key in item:
            field_present = True
            values.extend(_effort_values(item.get(key)))
    capabilities = item.get("capabilities")
    if isinstance(capabilities, Mapping):
        nested, nested_present = _efforts_from_model_mapping(capabilities)
        field_present = field_present or nested_present
        values.extend(nested)
    return _dedupe_efforts(values), field_present


def _effort_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: list[str] = []
        for key in ("effort", "name", "id", "level"):
            nested = value.get(key)
            if isinstance(nested, str):
                out.append(nested)
        for key in ("levels", "values", "supported"):
            out.extend(_effort_values(value.get(key)))
        return out
    if isinstance(value, list):
        list_values: list[str] = []
        for item in value:
            list_values.extend(_effort_values(item))
        return list_values
    return []


def _parse_models_text(text: str) -> tuple[str, ...]:
    try:
        return _parse_models_payload(json.loads(text))
    except json.JSONDecodeError:
        pass
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("Usage:", "Flags:", "Available subcommands:")):
            continue
        if line.startswith(("-", "--")) or line in {"Show help", "List available models"}:
            continue
        names.append(line.removeprefix("* ").removeprefix("- ").strip())
    return _dedupe_model_names(names)


def _parse_cli_effort_help(text: str) -> tuple[str, ...]:
    match = re.search(r"--effort[^\n]*\(([^)]+)\)", text)
    if not match:
        return ()
    return _dedupe_efforts(re.split(r"[,/| ]+", match.group(1)))


def _dedupe_model_names(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        name = value.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _dedupe_efforts(values: Sequence[str]) -> tuple[str, ...]:
    aliases = {
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
        "max": "max",
    }
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        effort = aliases.get(value.strip().lower().replace("_", "-"))
        if effort is None or effort in seen:
            continue
        seen.add(effort)
        out.append(effort)
    return tuple(out)


def _zai_model_supports_reasoning_effort(model: str) -> bool:
    match = re.fullmatch(r"glm-(\d+)(?:\.(\d+))?.*", model.strip().lower())
    if match is None:
        return False
    major = int(match.group(1))
    minor = int(match.group(2) or "0")
    return (major, minor) >= (5, 2)


def _join_api_path(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _zai_models_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if "api.z.ai" in stripped:
        return "https://api.z.ai/api/coding/paas/v4/models"
    if stripped.endswith("/chat/completions"):
        stripped = stripped[: -len("/chat/completions")]
    if stripped.endswith("/anthropic"):
        stripped = stripped[: -len("/anthropic")]
    return _join_api_path(stripped, "models")
