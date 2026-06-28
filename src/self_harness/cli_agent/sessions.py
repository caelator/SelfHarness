"""Session persistence + resume for ``self-harness code``.

Each interactive session is written as one JSON file under ``<root>/runs/sessions/<id>.json`` after every
turn (so a crash loses nothing) and on exit. A saved session carries the full agentic ``history`` so
resuming continues the exact conversation, plus per-turn summaries and the harvested-bundle ids (so
``/harvested`` survives a resume). Timestamps are injected by the caller — this module never reads the
clock, keeping it deterministic and testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from self_harness.types import write_stable_json


def sessions_dir(root: Path) -> Path:
    return root / "runs" / "sessions"


@dataclass
class SessionRecord:
    """The persisted shape of one interactive session."""

    id: str
    workdir: str
    harness_hash: str
    created_at: str = ""
    updated_at: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    harvested: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workdir": self.workdir,
            "harness_hash": self.harness_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history": self.history,
            "turns": self.turns,
            "harvested": self.harvested,
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> SessionRecord:
        return cls(
            id=str(value.get("id", "")),
            workdir=str(value.get("workdir", "")),
            harness_hash=str(value.get("harness_hash", "")),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            history=list(value.get("history", []) or []),
            turns=list(value.get("turns", []) or []),
            harvested=list(value.get("harvested", []) or []),
        )


def save_session(root: Path, record: SessionRecord) -> Path:
    """Write ``record`` atomically-enough (single write) to its session file; returns the path."""

    directory = sessions_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.id}.json"
    write_stable_json(path, record.to_json())
    return path


def load_session(root: Path, session_id: str) -> SessionRecord | None:
    path = sessions_dir(root) / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return SessionRecord.from_json(value)


def list_sessions(root: Path) -> list[SessionRecord]:
    """All saved sessions, most-recently-updated first."""

    directory = sessions_dir(root)
    if not directory.is_dir():
        return []
    records: list[SessionRecord] = []
    for path in directory.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            records.append(SessionRecord.from_json(value))
    records.sort(key=lambda r: (r.updated_at, r.id), reverse=True)
    return records


def latest_session(root: Path) -> SessionRecord | None:
    records = list_sessions(root)
    return records[0] if records else None
