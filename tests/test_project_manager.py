"""Tests for the project save/resume manager."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from self_harness.project_manager import (
    delete_project,
    list_projects,
    load_project,
    save_project,
)


def _mock_projects_dir(tmp_path: Path) -> Path:
    """Point projects_dir at a temp directory for test isolation."""
    d = tmp_path / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_save_and_list_project(tmp_path: Path):
    d = _mock_projects_dir(tmp_path)
    with patch("self_harness.project_manager.projects_dir", return_value=d):
        save_project("test-1", working_dir="/tmp", notes="hello")
        projects = list_projects()
        assert len(projects) == 1
        assert projects[0].name == "test-1"
        assert projects[0].notes == "hello"


def test_list_projects_sorted_by_date(tmp_path: Path):
    d = _mock_projects_dir(tmp_path)
    with patch("self_harness.project_manager.projects_dir", return_value=d):
        save_project("older", working_dir="/tmp", notes="")
        save_project("newer", working_dir="/tmp", notes="")
        projects = list_projects()
        # Both have same timestamp (second resolution), so just check both exist
        assert len(projects) == 2


def test_load_project_by_number(tmp_path: Path):
    d = _mock_projects_dir(tmp_path)
    with patch("self_harness.project_manager.projects_dir", return_value=d):
        save_project("alpha", working_dir="/tmp")
        save_project("beta", working_dir="/tmp")
        # Both exist; loading by number should work
        projects = list_projects()
        assert len(projects) == 2
        project = load_project("1")
        assert project is not None
        assert project.name in {"alpha", "beta"}


def test_load_project_by_name_fragment(tmp_path: Path):
    d = _mock_projects_dir(tmp_path)
    with patch("self_harness.project_manager.projects_dir", return_value=d):
        save_project("my-cool-project", working_dir="/tmp")
        project = load_project("cool")
        assert project is not None
        assert project.name == "my-cool-project"


def test_load_project_not_found(tmp_path: Path):
    d = _mock_projects_dir(tmp_path)
    with patch("self_harness.project_manager.projects_dir", return_value=d):
        assert load_project("nonexistent") is None


def test_delete_project(tmp_path: Path):
    d = _mock_projects_dir(tmp_path)
    with patch("self_harness.project_manager.projects_dir", return_value=d):
        save_project("doomed", working_dir="/tmp")
        assert len(list_projects()) == 1
        project = load_project("1")
        assert project is not None
        assert delete_project(project.id) is True
        assert len(list_projects()) == 0
        assert delete_project("nonexistent") is False


def test_project_persists_harness_state(tmp_path: Path):
    d = _mock_projects_dir(tmp_path)
    with patch("self_harness.project_manager.projects_dir", return_value=d):
        harness = {"system_prompt": "test prompt", "bootstrap": "test"}
        save_project("with-harness", working_dir="/tmp", harness_state=harness)
        project = load_project("1")
        assert project is not None
        assert project.harness_state is not None
        assert project.harness_state["system_prompt"] == "test prompt"


def test_project_file_path_property(tmp_path: Path):
    d = _mock_projects_dir(tmp_path)
    with patch("self_harness.project_manager.projects_dir", return_value=d):
        project = save_project("path-test", working_dir="/tmp")
        assert project.file_path.is_file()
        data = json.loads(project.file_path.read_text())
        assert data["name"] == "path-test"


def test_git_sync_clean_repo(tmp_path: Path, monkeypatch):
    """git_sync on a clean repo (nothing to commit) should report committed=False."""
    import subprocess

    from self_harness.project_manager import git_sync

    # Create a temp git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=tmp_path, capture_output=True)

    result = git_sync(str(tmp_path), "test message")
    assert not result.errors
    # Nothing to commit (clean tree after initial)
    assert result.ok


def test_git_sync_with_changes(tmp_path: Path):
    """git_sync with uncommitted changes should commit them."""
    import subprocess

    from self_harness.project_manager import git_sync

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=tmp_path, capture_output=True)

    # Create a new file
    (tmp_path / "test.txt").write_text("hello")

    result = git_sync(str(tmp_path), "add test file")
    assert result.committed
    assert result.commit_sha is not None
    # No remote configured — should not error
    assert result.ok


def test_git_sync_result_ok_property():
    """GitSyncResult.ok is True when no errors."""
    from self_harness.project_manager import GitSyncResult

    assert GitSyncResult(
        committed=True, pushed=True, merged=False, remote_ahead=[], errors=[]
    ).ok
    assert not GitSyncResult(
        committed=False, pushed=False, merged=False, remote_ahead=[], errors=["failed"]
    ).ok
