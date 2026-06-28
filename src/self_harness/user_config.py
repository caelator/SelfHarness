"""Persistent, user-level configuration for the SelfHarness CLI.

Stores settings (API key, base URL, model, and interactive-run defaults) in a single JSON file under
the user's config directory — ``$XDG_CONFIG_HOME/self-harness/config.json`` or ``~/.config/self-harness/
config.json`` — written with owner-only (0600) permissions because it can hold a secret. This is what
lets ``self-harness`` "just work" without sourcing an env file each session.

Resolution order for the API key (and base URL / model) is: explicit argument → environment variable
(``ZAI_API_KEY`` etc., so existing setups keep working) → saved config file → default/None. Nothing here
imports ``rich`` or touches the network; it is pure file I/O so it can be used from any entry point.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.z.ai/api/anthropic"
DEFAULT_MODEL = "glm-5.2"

# Keys that may be stored in the config file. ``api_key`` is the only secret.
_SECRET_KEYS = frozenset({"api_key"})
_KNOWN_KEYS = (
    "api_key",
    "base_url",
    "model",
    "max_steps",
    "tool_timeout_seconds",
    "auto_promote",
    "harvest",
    "share_central_harness",
)
_INT_KEYS = frozenset({"max_steps", "tool_timeout_seconds"})
_BOOL_KEYS = frozenset({"auto_promote", "harvest", "share_central_harness"})


def config_dir() -> Path:
    """The directory holding the config file (respects ``XDG_CONFIG_HOME``)."""

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "self-harness"


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class UserConfig:
    """An in-memory view of the saved config; mutate then :meth:`save`."""

    values: dict[str, Any]
    path: Path

    # -- accessors --------------------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a known key, coercing ints/bools from strings so the CLI can pass raw text."""

        if key not in _KNOWN_KEYS:
            raise KeyError(f"unknown setting: {key!r} (known: {', '.join(_KNOWN_KEYS)})")
        self.values[key] = _coerce(key, value)

    def unset(self, key: str) -> None:
        self.values.pop(key, None)

    # -- resolution (config value, used as a fallback under env/args by the resolvers below) -------

    @property
    def api_key(self) -> str | None:
        value = self.values.get("api_key")
        return value or None

    @property
    def base_url(self) -> str:
        return self.values.get("base_url") or DEFAULT_BASE_URL

    @property
    def model(self) -> str | None:
        return self.values.get("model") or None

    # -- persistence ------------------------------------------------------------------------------

    def save(self) -> Path:
        """Write the config file atomically with owner-only permissions."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.values, indent=2, sort_keys=True) + "\n"
        tmp = self.path.with_suffix(".json.tmp")
        # Create the temp file with 0600 from the start so the secret is never briefly world-readable.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self.path)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        return self.path

    def redacted(self) -> dict[str, Any]:
        """A copy safe to print: secrets masked to a short fingerprint."""

        out: dict[str, Any] = {}
        for key, value in self.values.items():
            if key in _SECRET_KEYS and isinstance(value, str) and value:
                out[key] = mask_secret(value)
            else:
                out[key] = value
        return out


def load_config() -> UserConfig:
    """Load the config file, or return an empty config if none exists/parses."""

    path = config_path()
    values: dict[str, Any] = {}
    if path.is_file():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                values = {k: v for k, v in parsed.items() if k in _KNOWN_KEYS}
        except (OSError, json.JSONDecodeError):
            values = {}
    return UserConfig(values=values, path=path)


def _coerce(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in _INT_KEYS and not isinstance(value, int):
        return int(str(value).strip())
    if key in _BOOL_KEYS and not isinstance(value, bool):
        return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}
    return str(value).strip() if isinstance(value, str) else value


def mask_secret(value: str) -> str:
    """Show only enough of a secret to recognize it (first 6 chars + length)."""

    if not value:
        return "(unset)"
    head = value[:6]
    return f"{head}… ({len(value)} chars)"


# ----- resolution helpers (env wins over config, so existing exports keep working) ------------------


def resolve_api_key(explicit: str | None = None, config: UserConfig | None = None) -> str | None:
    if explicit:
        return explicit
    env = os.environ.get("ZAI_API_KEY")
    if env:
        return env
    cfg = config if config is not None else load_config()
    return cfg.api_key


def resolve_base_url(explicit: str | None = None, config: UserConfig | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("ZAI_BASE_URL")
    if env:
        return env
    cfg = config if config is not None else load_config()
    return cfg.base_url


def resolve_model(explicit: str | None = None, config: UserConfig | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("ZAI_MODEL")
    if env:
        return env
    cfg = config if config is not None else load_config()
    return cfg.model or DEFAULT_MODEL
