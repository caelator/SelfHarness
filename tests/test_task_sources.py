from __future__ import annotations

import pytest

from self_harness.corpus import TaskCorpus, load_corpus
from self_harness.task_sources import (
    GENERATED_FAILURE_MODE,
    INGESTED_FAILURE_MODE,
    LEARNED_SPLIT,
    UX_BUNDLE_KIND,
    UX_COMPLAINT_FAILURE_MODE,
    TaskSourceError,
    assemble_corpus,
    dedupe_tasks,
    filter_verified_tasks,
    ingest_failing_bundle,
    ingest_inbox_bundle,
    ingest_ux_bundle,
    make_task,
    parse_generated_tasks,
)


def _task(tid: str, split: str) -> dict:
    return {
        "id": tid,
        "split": split,
        "failure_mode": "x",
        "description": "d",
        "metadata": {"success_criteria": "c", "instructions": "i"},
    }


BASE = {
    "corpus_version": "1",
    "corpus_id": "base",
    "tasks": [_task("in-1", "held_in"), _task("out-1", "held_out"), _task("out-2", "held_out")],
}


def test_ingest_failing_bundle_makes_held_in_task() -> None:
    task = ingest_failing_bundle(
        {"id": "fix-add", "command": "python3 test.py", "files": {"test.py": "assert add(2,3)==5\n"}}
    )
    assert task["id"] == "fix-add"
    assert task["split"] == LEARNED_SPLIT
    assert task["failure_mode"] == INGESTED_FAILURE_MODE
    assert "python3 test.py" in task["metadata"]["instructions"]
    assert "exits with status 0" in task["metadata"]["success_criteria"]
    assert task["metadata"]["workspace_files"] == {"test.py": "assert add(2,3)==5\n"}


def test_ingest_bundle_requires_id_and_command() -> None:
    with pytest.raises(TaskSourceError):
        ingest_failing_bundle({"command": "python3 t.py"})
    with pytest.raises(TaskSourceError):
        ingest_failing_bundle({"id": "x"})


def test_ingest_bundle_rejects_workspace_escape() -> None:
    with pytest.raises(TaskSourceError):
        ingest_failing_bundle({"id": "x", "command": "c", "files": {"../evil": "y"}})
    with pytest.raises(TaskSourceError):
        ingest_failing_bundle({"id": "x", "command": "c", "files": {"/abs": "y"}})


def test_ingest_ux_bundle_makes_held_in_task() -> None:
    task = ingest_ux_bundle(
        {
            "id": "ux-identity",
            "kind": UX_BUNDLE_KIND,
            "trigger": "identity-query",
            "observation": "GLM via Z.ai answered that it was Claude.",
            "expected_behavior": "The CLI reports GLM via Z.ai and glm-5.2.",
            "observed": "I'm Claude, made by Anthropic.",
            "checkable_criterion": "Asking `what model are you` reports provider glm and model glm-5.2.",
            "operating_provider": "glm",
            "admitting_judge": "codex",
            "admission_reason": "contradicts configured provider",
            "files": {"notes.txt": "identity transcript\n"},
        }
    )

    assert task["id"] == "ux-identity"
    assert task["split"] == LEARNED_SPLIT
    assert task["failure_mode"] == UX_COMPLAINT_FAILURE_MODE
    assert "GLM via Z.ai answered" in task["metadata"]["instructions"]
    assert "provider glm and model glm-5.2" in task["metadata"]["success_criteria"]
    assert task["metadata"]["workspace_files"] == {"notes.txt": "identity transcript\n"}
    assert task["metadata"]["operating_provider"] == "glm"
    assert task["metadata"]["admitting_judge"] == "codex"


def test_ingest_ux_bundle_requires_checkable_fields() -> None:
    base = {"id": "ux", "kind": UX_BUNDLE_KIND, "trigger": "wrong", "observation": "bad"}
    with pytest.raises(TaskSourceError, match="checkable_criterion"):
        ingest_ux_bundle(base)
    with pytest.raises(TaskSourceError, match="trigger"):
        ingest_ux_bundle({**base, "trigger": "", "checkable_criterion": "c"})
    with pytest.raises(TaskSourceError, match="workspace_files path escapes"):
        ingest_ux_bundle({**base, "checkable_criterion": "c", "files": {"../escape": "x"}})


def test_ingest_inbox_bundle_dispatches_legacy_and_ux() -> None:
    command = ingest_inbox_bundle({"id": "cmd", "command": "pytest"})
    ux = ingest_inbox_bundle(
        {
            "id": "ux",
            "kind": UX_BUNDLE_KIND,
            "trigger": "wrong",
            "observation": "bad answer",
            "checkable_criterion": "answer is corrected",
        }
    )

    assert command["failure_mode"] == INGESTED_FAILURE_MODE
    assert ux["failure_mode"] == UX_COMPLAINT_FAILURE_MODE


def test_make_task_rejects_disallowed_metadata() -> None:
    # api_key et al. must never ride in via extra_metadata (no solver/judge config smuggling).
    with pytest.raises(TaskSourceError):
        make_task(task_id="t", instructions="i", success_criteria="c", extra_metadata={"api_key": "secret"})


def test_assemble_preserves_base_held_out_and_forces_extras_held_in() -> None:
    extra = ingest_failing_bundle({"id": "new-fail", "command": "pytest"})
    corpus = assemble_corpus(BASE, [extra])
    by_id = {t["id"]: t for t in corpus["tasks"]}
    # Base held_out is the fixed yardstick — untouched.
    assert by_id["out-1"]["split"] == "held_out"
    assert by_id["out-2"]["split"] == "held_out"
    # New task is held_in.
    assert by_id["new-fail"]["split"] == LEARNED_SPLIT
    # The assembled corpus is loadable by the real loader.
    loaded = load_corpus_from_dict(corpus)
    assert {t.id for t in loaded.tasks} == {"in-1", "out-1", "out-2", "new-fail"}


def test_assemble_dedupes_and_extra_supersedes_base() -> None:
    # An extra task sharing a base id replaces it (re-ingested failure updates in place).
    replacement = make_task(task_id="in-1", instructions="updated", success_criteria="updated criteria")
    corpus = assemble_corpus(BASE, [replacement])
    ids = [t["id"] for t in corpus["tasks"]]
    assert ids.count("in-1") == 1
    by_id = {t["id"]: t for t in corpus["tasks"]}
    assert by_id["in-1"]["metadata"]["instructions"] == "updated"


def test_dedupe_keeps_last_in_first_seen_order() -> None:
    a1 = {"id": "a", "v": 1}
    b = {"id": "b", "v": 1}
    a2 = {"id": "a", "v": 2}
    result = dedupe_tasks([a1, b, a2])
    assert [t["id"] for t in result] == ["a", "b"]
    assert result[0]["v"] == 2


def test_parse_generated_drops_malformed_and_namespaces_ids() -> None:
    text = (
        '{"tasks": ['
        '{"id": "good", "instructions": "do x", "success_criteria": "x done"},'
        '{"instructions": "no criteria"},'  # malformed: missing success_criteria
        '{"not": "a task"}'
        ']}'
    )
    tasks = parse_generated_tasks(text, id_prefix="gen")
    assert [t["id"] for t in tasks] == ["gen-good"]
    assert tasks[0]["split"] == LEARNED_SPLIT
    assert tasks[0]["failure_mode"] == GENERATED_FAILURE_MODE


def test_parse_generated_handles_non_json_and_missing_tasks() -> None:
    assert parse_generated_tasks("sorry, I cannot help") == []
    assert parse_generated_tasks('{"notes": "no tasks here"}') == []


def test_parse_generated_tolerates_code_fence() -> None:
    text = '```json\n{"tasks": [{"id": "x", "instructions": "i", "success_criteria": "c"}]}\n```'
    tasks = parse_generated_tasks(text)
    assert len(tasks) == 1


def test_filter_verified_passthrough_when_no_guard() -> None:
    tasks = [make_task(task_id="a", instructions="i", success_criteria="c")]
    assert filter_verified_tasks(tasks, None) == tasks


def test_filter_verified_keeps_only_passing_and_swallows_verifier_errors() -> None:
    tasks = [
        make_task(task_id="keep", instructions="i", success_criteria="c"),
        make_task(task_id="drop", instructions="i", success_criteria="c"),
        make_task(task_id="boom", instructions="i", success_criteria="c"),
    ]

    def verifier(task: object) -> bool:
        tid = task["id"]  # type: ignore[index]
        if tid == "boom":
            raise RuntimeError("judge unavailable")
        return tid == "keep"

    kept = filter_verified_tasks(tasks, verifier)
    assert [t["id"] for t in kept] == ["keep"]


def load_corpus_from_dict(corpus: dict) -> TaskCorpus:
    # Round-trip through the real loader to prove the assembled corpus is valid.
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        p.write_text(json.dumps(corpus), encoding="utf-8")
        return load_corpus(p, allow_legacy=False)
