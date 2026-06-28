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
    assert detail["incomplete"] is False


def test_ui_run_detail_of_in_progress_run_is_partial_not_error(tmp_path: Path) -> None:
    # A run that has a manifest but no lineage.json yet (still running) must NOT make run_detail throw;
    # the console auto-selects the newest run and would otherwise show "load failed: missing audit
    # artifact". It should return a partial detail flagged incomplete instead.
    from self_harness.ui import UiJob

    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    run_id = "ui-inprogress-0000"
    run_path = app.runs_dir / run_id
    (run_path / "rounds" / "0").mkdir(parents=True)
    (run_path / "manifest.json").write_text("{}", encoding="utf-8")
    app._jobs["job-x"] = UiJob(
        id="job-x",
        run_id=run_id,
        path=run_path,
        status="running",
        config={},
        created_at="2026-06-27T00:00:00Z",
    )

    detail = app.run_detail(run_id)
    assert detail["incomplete"] is True
    assert detail["status"] == "running"
    assert detail["summary"] is None
    assert detail["error"] is None  # not surfaced as an error while the job is still running
    assert detail["trajectory"] == []


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


def test_ui_autoloop_runs_continuously_until_stopped(tmp_path: Path) -> None:
    # The continuous loop launches evolving runs back-to-back. Using the deterministic runner keeps this
    # offline and fast. It must complete more than one run and stop cleanly on request.
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    started = app.start_autoloop({"run_mode": "deterministic", "rounds": 1, "evaluation_repeats": 1, "seed": 0})
    assert started["ok"] is True
    assert app.state()["autoloop"]["active"] is True

    # Starting twice is a no-op while already running.
    again = app.start_autoloop({"run_mode": "deterministic", "rounds": 1})
    assert again["ok"] is False

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and app.state()["autoloop"]["runs_completed"] < 2:
        time.sleep(0.1)
    assert app.state()["autoloop"]["runs_completed"] >= 2

    app.stop_autoloop()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and app.state()["autoloop"]["active"]:
        time.sleep(0.1)
    final = app.state()["autoloop"]
    assert final["active"] is False
    assert final["error"] is None
    # The deterministic demo accepts edits, so the loop should record at least one promoted edit.
    assert final["edits_promoted"] >= 1


def test_ui_stop_autoloop_when_not_running_is_safe(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    result = app.stop_autoloop()
    assert result["ok"] is True
    assert app.state()["autoloop"]["active"] is False


def test_ui_inbox_submit_and_drain_into_learned_tasks(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    # Submitting a failing-test bundle lands in the inbox and shows up in state.
    res = app.submit_inbox_bundle({"id": "fix-thing", "command": "python3 t.py", "files": {"t.py": "assert False\n"}})
    assert res["ok"] is True
    assert app.state()["inbox_depth"] == 1
    assert app.state()["learned_task_count"] == 0

    # Draining converts the bundle into a held-in learned task and clears the inbox.
    ingested = app._drain_inbox()
    assert ingested == 1
    assert app.state()["inbox_depth"] == 0
    assert app.state()["learned_task_count"] == 1
    learned = app._load_learned_tasks()
    assert learned[0]["id"] == "fix-thing"
    assert learned[0]["split"] == "held_in"
    # Processed bundle is preserved (audit trail), not deleted.
    assert (app.inbox_processed_dir / list(app.inbox_processed_dir.iterdir())[0].name).is_file()


def test_ui_submit_inbox_rejects_malformed_bundle(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    with pytest.raises(ValueError):
        app.submit_inbox_bundle({"command": "no id"})


def test_ui_assembled_corpus_has_base_held_out_plus_learned_held_in(tmp_path: Path) -> None:
    import json
    import shutil

    # Give the app a real base corpus to assemble against.
    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "examples").mkdir(parents=True)
    shutil.copy2(repo / "examples" / "agentic_corpus.json", tmp_path / "examples" / "agentic_corpus.json")

    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    app.submit_inbox_bundle({"id": "learned-1", "command": "python3 t.py", "files": {"t.py": "assert False\n"}})
    app._drain_inbox()

    out_dir = app.runs_dir / "probe"
    corpus_path = app._assemble_iteration_corpus(out_dir)
    assert corpus_path is not None and corpus_path.is_file()
    corpus = json.loads(corpus_path.read_text())
    by_id = {t["id"]: t for t in corpus["tasks"]}
    # The learned task is present as held_in.
    assert by_id["learned-1"]["split"] == "held_in"
    # The base corpus's held_out tasks are still held_out (fixed yardstick), proving they're preserved.
    base = json.loads((tmp_path / "examples" / "agentic_corpus.json").read_text())
    base_held_out = {t["id"] for t in base["tasks"] if t["split"] == "held_out"}
    for tid in base_held_out:
        assert by_id[tid]["split"] == "held_out"


def test_ui_assemble_corpus_returns_none_without_learned_tasks(tmp_path: Path) -> None:
    app = HarnessUiApp(root=tmp_path, runs_dir=Path("runs"))
    assert app._assemble_iteration_corpus(app.runs_dir / "probe") is None


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
    # A real unified diff against harness.py is produced (the exact changed lines depend on whether the
    # source already carries a promoted lineage, so assert on the diff header, not a specific code line).
    assert "harness.py" in preview["diff"]
    assert preview["diff"].lstrip().startswith("--- harness.py")
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
