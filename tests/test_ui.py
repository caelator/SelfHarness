from __future__ import annotations

import time
from pathlib import Path

import pytest

from self_harness.ui import HarnessUiApp


def test_ui_state_lists_completed_runs(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))

    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    finished = _wait_for_job(app, str(job["id"]))

    state = app.state()
    detail = app.run_detail(str(finished["run_id"]))

    assert finished["status"] == "completed"
    assert state["reproduction_claimed"] is False
    assert state["runs"][0]["id"] == finished["run_id"]
    assert detail["summary"]["final_held_in_score"] == 1.0
    assert detail["summary"]["final_held_out_score"] == 1.0
    assert detail["trajectory"][0]["after_held_in_passed"] == 4
    assert "bootstrap" in detail["inspection"]["final_harness_surfaces"]


def test_ui_state_reports_glm_proposer_mode(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"), proposer_mode="zai")

    state = app.state()

    assert state["proposer_mode"] == "glm"
    assert state["model"] == "glm-5.2"


def test_ui_rejects_invalid_run_ids(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))

    with pytest.raises(ValueError, match="invalid run id"):
        app.run_detail("../outside")


def _wait_for_job(app: HarnessUiApp, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = app.state()
        for job in state["jobs"]:
            if job["id"] == job_id and job["status"] in {"completed", "failed"}:
                return job
        time.sleep(0.05)
    raise AssertionError("UI job did not finish")
