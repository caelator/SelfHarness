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
    with pytest.raises(ValueError, match="invalid run id"):
        app.round_detail("../outside", 0)
    with pytest.raises(ValueError, match="invalid run id"):
        app.harness_detail("../outside")


def test_ui_round_detail_exposes_patterns_proposals_evaluations(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    finished = _wait_for_job(app, str(job["id"]))

    round_detail = app.round_detail(str(finished["run_id"]), 0)

    assert round_detail["round"] == 0
    assert round_detail["patterns"]  # the persisted evidence bundle B_t
    pattern = round_detail["patterns"][0]
    assert pattern["split"] == "held_in"
    assert {"terminal_cause", "causal_status", "mechanism"} <= set(pattern["signature"])
    assert round_detail["proposals"]
    assert round_detail["evaluations"]


def test_ui_harness_detail_exposes_initial_and_final_surfaces(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    finished = _wait_for_job(app, str(job["id"]))

    harness = app.harness_detail(str(finished["run_id"]))

    assert "system_prompt" in harness["initial_harness"]
    assert "bootstrap" in harness["initial_harness"]
    # Figure 3 initial system prompt is the Terminal-Bench Harbor prompt.
    assert "Terminal Bench 2 Harbor" in harness["initial_harness"]["system_prompt"]
    assert harness["final_harness"] is not None


def test_ui_preflight_reports_glm_status_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))

    preflight = app.preflight()

    assert preflight["model"] == "glm-5.2"
    assert preflight["key_present"] is False
    assert preflight["mode"] == "dry-run"
    assert preflight["status"] == "not_checked"
    assert preflight["reproduction_claimed"] is False


def _wait_for_job(app: HarnessUiApp, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = app.state()
        for job in state["jobs"]:
            if job["id"] == job_id and job["status"] in {"completed", "failed"}:
                return job
        time.sleep(0.05)
    raise AssertionError("UI job did not finish")


def test_ui_job_records_run_mode_and_deterministic_default(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    finished = _wait_for_job(app, str(job["id"]))
    assert finished["run_mode"] == "deterministic"
    assert finished["status"] == "completed"


def test_ui_persists_and_evolves_harness_across_runs(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))

    # First run promotes edits (toy heuristic accepts the artifact/recovery edits) and persists them.
    first = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    _wait_for_job(app, str(first["id"]))
    status = app.state()["harness_state"]
    assert status["evolving"] is True
    assert status["source_run"] == first["run_id"]
    evolved_hash = status["harness_hash"]
    assert evolved_hash

    # Second run should start FROM the persisted harness, not initial_harness().
    second = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    finished2 = _wait_for_job(app, str(second["id"]))
    harness2 = app.harness_detail(str(finished2["run_id"]))
    # The round-0 "before" harness of run 2 is the evolved harness from run 1 (not the Figure-3 baseline).
    from self_harness.harness import harness_hash, initial_harness, load_harness_spec
    before2 = load_harness_spec(harness2["initial_harness"])
    assert harness_hash(before2) != harness_hash(initial_harness())


def test_ui_reset_harness_state(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    _wait_for_job(app, str(job["id"]))
    assert app.state()["harness_state"]["evolving"] is True

    result = app.reset_harness_state()
    assert result["ok"] is True
    assert result["harness_state"]["evolving"] is False
    assert app.state()["harness_state"]["evolving"] is False


def test_ui_run_can_opt_out_of_evolution(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0, "evolve": False})
    _wait_for_job(app, str(job["id"]))
    # With evolve disabled, no lineage file is written.
    assert app.state()["harness_state"]["evolving"] is False


def test_ui_chat_validates_messages_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    with pytest.raises(ValueError, match="non-empty list"):
        app.chat({"messages": []})
    with pytest.raises(ValueError, match="final message must be a user turn"):
        app.chat({"messages": [{"role": "assistant", "content": "hi"}]})


def test_ui_dev_task_validates_inputs_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    with pytest.raises(ValueError, match="instructions"):
        app.dev_task({"success_criteria": "x"})
    with pytest.raises(ValueError, match="success_criteria"):
        app.dev_task({"instructions": "do a thing"})


def test_ui_promote_to_source_dry_run(tmp_path: Path) -> None:
    # Build a self-contained repo copy with a real harness.py so promote can render + diff without
    # touching the developer's working tree.
    import shutil

    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "src" / "self_harness").mkdir(parents=True)
    harness_copy = tmp_path / "src" / "self_harness" / "harness.py"
    shutil.copy2(repo / "src" / "self_harness" / "harness.py", harness_copy)
    original_text = harness_copy.read_text()

    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    finished = _wait_for_job(app, str(job["id"]))

    preview = app.promote_to_source(str(finished["run_id"]), {"apply": False})
    assert preview["applied"] is False
    assert preview["changed"] is True
    assert "def initial_harness()" in preview["diff"]
    # Dry-run must not modify the source file at all.
    assert harness_copy.read_text() == original_text


def test_ui_auto_integrates_reviewer_approved_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # When the acceptance gate (the reviewer) promotes an edit, the run auto-writes the evolved harness
    # back into harness.py with NO manual approval step. The correctness gate is stubbed so this test
    # stays hermetic (no ruff/mypy subprocess), focusing on the auto-integration wiring.
    import shutil

    import self_harness.ui as ui_mod

    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "src" / "self_harness").mkdir(parents=True)
    harness_copy = tmp_path / "src" / "self_harness" / "harness.py"
    shutil.copy2(repo / "src" / "self_harness" / "harness.py", harness_copy)
    original_text = harness_copy.read_text()

    monkeypatch.setattr(ui_mod, "_run_source_gate", lambda root, expected_hash: {"ok": True, "checks": []})

    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    assert app.state()["auto_promote_to_source"] is True
    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    finished = _wait_for_job(app, str(job["id"]))

    promotion = finished["source_promotion"]
    assert promotion is not None
    assert promotion["applied"] is True
    # Source was actually rewritten and a backup of the original was kept.
    assert harness_copy.read_text() != original_text
    assert "def initial_harness()" in harness_copy.read_text()
    assert (tmp_path / "src" / "self_harness" / "harness.py.bak").read_text() == original_text


def test_ui_auto_integration_can_be_disabled(tmp_path: Path) -> None:
    import shutil

    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "src" / "self_harness").mkdir(parents=True)
    harness_copy = tmp_path / "src" / "self_harness" / "harness.py"
    shutil.copy2(repo / "src" / "self_harness" / "harness.py", harness_copy)
    original_text = harness_copy.read_text()

    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"), auto_promote_to_source=False)
    assert app.state()["auto_promote_to_source"] is False
    job = app.start_run({"rounds": 1, "evaluation_repeats": 1, "seed": 0})
    finished = _wait_for_job(app, str(job["id"]))

    # No auto-write occurred; source is untouched and the job records no promotion.
    assert finished["source_promotion"] is None
    assert harness_copy.read_text() == original_text


def test_ui_static_asset_route_serves_vendored_alpine(tmp_path: Path) -> None:
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    from self_harness.ui import _make_handler

    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

        conn.request("GET", "/")
        index = conn.getresponse()
        body = index.read().decode("utf-8")
        assert index.status == 200
        # The page must reference the LOCAL asset, not a CDN (offline-safe).
        assert "/static/alpine-3.14.1.min.js" in body
        assert "cdn.jsdelivr.net" not in body

        conn.request("GET", "/static/alpine-3.14.1.min.js")
        asset = conn.getresponse()
        asset_body = asset.read()
        assert asset.status == 200
        assert asset.getheader("Content-Type", "").startswith("text/javascript")
        assert b"Alpine" in asset_body

        conn.request("GET", "/static/../ui.py")
        traversal = conn.getresponse()
        traversal.read()
        assert traversal.status == 404
    finally:
        server.shutdown()
        server.server_close()
