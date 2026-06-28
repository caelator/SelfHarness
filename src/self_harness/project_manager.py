"""Save and resume self-harness projects.

A "project" captures everything needed to restart work at exactly the point
you left off:

  - The working directory
  - The corpus file used
  - The current harness state (evolved instructions)
  - A label and timestamp
  - Optional notes (what you were doing, what's next)

Projects are stored as JSON files under ``~/.config/self-harness/projects/``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def projects_dir() -> Path:
    """Directory where saved projects live."""
    d = Path.home() / ".config" / "self-harness" / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class SavedProject:
    """A snapshot of a self-harness project at a point in time."""

    id: str
    name: str
    working_dir: str
    corpus_path: str | None
    harness_state: dict[str, Any] | None
    rounds_completed: int
    notes: str
    saved_at: str
    # Metadata for display
    held_in_score: float | None = None
    held_out_score: float | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def file_path(self) -> Path:
        return projects_dir() / f"{self.id}.json"


def _generate_id() -> str:
    """Short timestamp-based ID with random suffix for uniqueness."""
    import random
    import string
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"proj-{int(time.time())}-{suffix}"


def save_project(
    name: str,
    *,
    working_dir: str | None = None,
    corpus_path: str | None = None,
    harness_state: dict[str, Any] | None = None,
    rounds_completed: int = 0,
    notes: str = "",
    held_in_score: float | None = None,
    held_out_score: float | None = None,
    tags: list[str] | None = None,
) -> SavedProject:
    """Save a project snapshot to disk."""
    project = SavedProject(
        id=_generate_id(),
        name=name,
        working_dir=working_dir or str(Path.cwd()),
        corpus_path=corpus_path,
        harness_state=harness_state,
        rounds_completed=rounds_completed,
        notes=notes,
        saved_at=time.strftime("%Y-%m-%d %H:%M"),
        held_in_score=held_in_score,
        held_out_score=held_out_score,
        tags=tags or [],
    )
    project.file_path.write_text(
        json.dumps(asdict(project), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return project


def list_projects() -> list[SavedProject]:
    """List all saved projects, most recent first."""
    projects: list[SavedProject] = []
    for path in projects_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            projects.append(SavedProject(**data))
        except (json.JSONDecodeError, TypeError):
            continue
    projects.sort(key=lambda p: (p.saved_at, p.id), reverse=True)
    return projects


def load_project(project_id: str) -> SavedProject | None:
    """Load a project by ID (supports partial match on the list number)."""
    projects = list_projects()
    # Try exact ID match
    for p in projects:
        if p.id == project_id:
            return p
    # Try list number (1-based index)
    try:
        idx = int(project_id) - 1
        if 0 <= idx < len(projects):
            return projects[idx]
    except ValueError:
        pass
    # Try name prefix match
    matches = [p for p in projects if project_id.lower() in p.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def delete_project(project_id: str) -> bool:
    """Delete a saved project. Returns True if deleted."""
    project = load_project(project_id)
    if project is None:
        return False
    project.file_path.unlink(missing_ok=True)
    return True
