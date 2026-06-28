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


@dataclass
class GitSyncResult:
    """Result of a git commit/merge/push operation."""
    committed: bool
    pushed: bool
    merged: bool
    remote_ahead: list[str]
    errors: list[str]
    commit_sha: str | None = None

    @property
    def ok(self) -> bool:
        """True if no errors occurred (committed or nothing-to-commit, pushed successfully)."""
        return not self.errors


def git_sync(
    working_dir: str,
    message: str,
    *,
    author_name: str = "self-harness",
    author_email: str = "self-harness@local",
) -> GitSyncResult:
    """Commit, merge, and push a project directory to its git remote.

    This runs the full sequence:
    1. ``git add -A`` — stage all changes
    2. If nothing staged, return early (nothing to commit)
    3. ``git commit`` with the given message
    4. ``git fetch origin``
    5. If remote is ahead, ``git merge origin/<branch>``
    6. ``git push origin <branch>``

    Returns a GitSyncResult with the outcome. Never raises — errors are
    captured in the result so the caller can display them.
    """
    import subprocess

    result = GitSyncResult(
        committed=False, pushed=False, merged=False, remote_ahead=[], errors=[]
    )

    def _run_git(args: list[str]) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["git"] + args,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return -1, "", str(exc)

    # 1. Stage everything
    code, _out, err = _run_git(["add", "-A"])
    if code != 0:
        result.errors.append(f"git add failed: {err}")
        return result

    # 2. Check if there's anything to commit
    code, out, _err = _run_git(["diff", "--cached", "--quiet"])
    if code == 0:
        # Nothing staged — working tree is clean
        pass  # continue to push in case remote is behind
    else:
        # 3. Commit
        code, out, err = _run_git([
            "-c", f"user.name={author_name}",
            "-c", f"user.email={author_email}",
            "commit", "-m", message,
        ])
        if code != 0:
            result.errors.append(f"git commit failed: {err}")
            return result
        result.committed = True
        # Extract commit SHA
        code2, sha, _err2 = _run_git(["rev-parse", "--short", "HEAD"])
        if code2 == 0:
            result.commit_sha = sha

    # 4. Check for remote
    code, out, _err = _run_git(["remote"])
    if code != 0 or not out:
        # No remote configured — commit only
        return result
    remote = out.splitlines()[0].strip() if out else "origin"

    # 5. Fetch
    code, _out, _err = _run_git(["fetch", remote])
    if code != 0:
        # Fetch failed (offline?) — still committed, just can't push
        result.errors.append(f"git fetch failed (offline?): {_err}")
        return result

    # 6. Determine current branch
    code, branch_out, _err = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        result.errors.append("cannot determine current branch")
        return result
    branch = branch_out.strip()

    # 7. Check if remote is ahead
    code, out, _err = _run_git(["rev-list", "--count", f"HEAD..{remote}/{branch}"])
    if code == 0 and out.strip():
        ahead_count = int(out.strip())
        if ahead_count > 0:
            result.remote_ahead = [f"{ahead_count} commits on {remote}/{branch}"]
            # 8. Merge
            code, out, err = _run_git([
                "-c", f"user.name={author_name}",
                "-c", f"user.email={author_email}",
                "merge", f"{remote}/{branch}", "--no-edit",
            ])
            if code != 0:
                result.errors.append(f"git merge failed: {err}")
                # Abort the merge to leave clean state
                _run_git(["merge", "--abort"])
                return result
            result.merged = True

    # 9. Push
    code, _out, err = _run_git(["push", remote, branch])
    if code != 0:
        result.errors.append(f"git push failed: {err}")
        return result
    result.pushed = True

    return result


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
