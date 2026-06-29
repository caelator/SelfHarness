"""Continuous failure sourcing for the self-improvement loop.

The autoloop only learns when it sees a *held-in failure*: no failure means nothing to mine, nothing to
propose. A static corpus has a finite supply of failures, so to keep improving the loop needs a
continuous source of novel ones. This module supplies two, both feeding **held-in** tasks (the static
corpus's held-out stays a fixed regression yardstick):

1. **Ingestion** — real failing-test bundles (a command that currently fails + the files to run it
   against) are converted into held-in tasks whose success criterion is "that command now exits 0".
   Real failures are self-validating, so this is the low-risk primitive.
2. **Generation** — when no real failures are available, an LLM proposes new candidate tasks targeting
   the harness's current weak spots. An optional solve+verify guard (off by default) can quarantine
   malformed generated tasks before they enter the corpus.

The module is deliberately free of UI and network dependencies so it is unit-testable offline and
reusable against any project's corpus.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from self_harness.adapters.agentic.runner import load_agentic_metadata_keys
from self_harness.corpus import CORPUS_VERSION
from self_harness.llm_proposer import _extract_json_object

# Held-in is the learnable set; ingested/generated tasks always land here so the static corpus's
# held-out remains an unchanging regression yardstick (see the loop's monotonicity guarantee).
LEARNED_SPLIT = "held_in"
INGESTED_FAILURE_MODE = "ingested_failure"
GENERATED_FAILURE_MODE = "generated_task"
UX_COMPLAINT_FAILURE_MODE = "ux_complaint"
UX_BUNDLE_KIND = "ux_complaint"

_DISALLOWED_METADATA = load_agentic_metadata_keys()


class TaskSourceError(ValueError):
    """Raised when a bundle or generated task cannot be turned into a valid corpus task."""


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskSourceError(f"{field} must be a non-empty string")
    return value


def _validate_workspace_files(files: object) -> dict[str, str]:
    if not isinstance(files, dict):
        raise TaskSourceError("workspace_files must be an object of relative-path -> string content")
    out: dict[str, str] = {}
    for rel_path, content in files.items():
        if not isinstance(rel_path, str) or not isinstance(content, str):
            raise TaskSourceError("workspace_files entries must be string path -> string content")
        parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
        if rel_path.startswith("/") or ".." in parts or not parts:
            raise TaskSourceError(f"workspace_files path escapes the workspace: {rel_path}")
        out[rel_path] = content
    return out


def _reject_disallowed_metadata(metadata: Mapping[str, Any]) -> None:
    offending = sorted(key for key in metadata if key in _DISALLOWED_METADATA)
    if offending:
        raise TaskSourceError(f"task carries disallowed metadata keys: {', '.join(offending)}")


def make_task(
    *,
    task_id: str,
    instructions: str,
    success_criteria: str,
    description: str | None = None,
    workspace_files: Mapping[str, str] | None = None,
    failure_mode: str = INGESTED_FAILURE_MODE,
    extra_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a validated held-in corpus task dict from explicit fields.

    Mirrors the shape ``GLMAgenticRunner`` expects (``instructions`` + ``success_criteria`` in metadata,
    optional ``workspace_files``) and rejects the same disallowed metadata keys so a task can never
    smuggle solver/judge configuration.
    """

    tid = _require_str(task_id, "id")
    instr = _require_str(instructions, "instructions")
    criteria = _require_str(success_criteria, "success_criteria")
    metadata: dict[str, Any] = {"instructions": instr, "success_criteria": criteria}
    if workspace_files:
        metadata["workspace_files"] = _validate_workspace_files(workspace_files)
    if extra_metadata:
        _reject_disallowed_metadata(extra_metadata)
        for key, value in extra_metadata.items():
            metadata.setdefault(key, value)
    _reject_disallowed_metadata(metadata)
    return {
        "id": tid,
        "split": LEARNED_SPLIT,
        "failure_mode": failure_mode,
        "description": description.strip() if isinstance(description, str) and description.strip() else instr[:120],
        "metadata": metadata,
    }


def ingest_failing_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a failing-test bundle into a held-in corpus task.

    Bundle shape: ``{id, command, files?, description?}``. ``command`` is a shell command that currently
    fails; the derived success criterion is "running it exits 0". ``files`` (path -> content) seed the
    workspace so the command can run. The command is embedded in the instructions so the solver knows
    exactly what to make pass. Bundles carry no solver/judge config (same disallowed-keys guard).
    """

    if not isinstance(bundle, Mapping):
        raise TaskSourceError("bundle must be an object")
    task_id = _require_str(bundle.get("id"), "id")
    command = _require_str(bundle.get("command"), "command")
    files = bundle.get("files")
    workspace_files = _validate_workspace_files(files) if files is not None else None
    description = bundle.get("description")
    instructions = (
        f"The command `{command}` currently fails in this workspace. "
        "Inspect the files, make the smallest correct change so that the command succeeds, "
        "then run it yourself to confirm it now exits 0."
    )
    success_criteria = (
        f"Running `{command}` in the workspace exits with status 0 (it currently fails). "
        "The fix must make the command genuinely pass, not bypass or delete the check."
    )
    return make_task(
        task_id=task_id,
        instructions=instructions,
        success_criteria=success_criteria,
        description=description if isinstance(description, str) else f"Fix failing command: {command}",
        workspace_files=workspace_files,
        failure_mode=INGESTED_FAILURE_MODE,
    )


def ingest_ux_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Convert an admitted semantic/UX complaint bundle into a held-in task.

    Bundle shape:
    ``{id, kind:"ux_complaint", trigger, observation, checkable_criterion, files?, metadata?}``.
    Unlike command bundles, UX bundles are not self-validating. They must already carry a concrete
    criterion admitted by a secondary judge; vague complaints are rejected before they can enter the
    learned held-in set.
    """

    if not isinstance(bundle, Mapping):
        raise TaskSourceError("bundle must be an object")
    kind = bundle.get("kind", UX_BUNDLE_KIND)
    if kind != UX_BUNDLE_KIND:
        raise TaskSourceError(f"unsupported ux bundle kind: {kind!r}")
    task_id = _require_str(bundle.get("id"), "id")
    trigger = _require_str(bundle.get("trigger"), "trigger")
    observation = _require_str(bundle.get("observation"), "observation")
    criterion = _require_str(bundle.get("checkable_criterion"), "checkable_criterion")
    files = bundle.get("files")
    workspace_files = _validate_workspace_files(files) if files is not None else None
    expected = bundle.get("expected_behavior")
    observed = bundle.get("observed")
    description = bundle.get("description")
    metadata = bundle.get("metadata")
    extra_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    for key in ("operating_provider", "admitting_judge", "admission_reason", "trigger_kind"):
        value = bundle.get(key)
        if isinstance(value, str) and value.strip():
            extra_metadata.setdefault(key, value.strip())
    extra_metadata.setdefault("trigger", trigger)
    parts = [
        "A SelfHarness operator or automatic detector observed a semantic/control-plane UX failure.",
        f"Trigger: {trigger}",
        f"Observation: {observation}",
    ]
    if isinstance(observed, str) and observed.strip():
        parts.append(f"Observed output: {observed.strip()}")
    if isinstance(expected, str) and expected.strip():
        parts.append(f"Expected behavior: {expected.strip()}")
    parts.append("Make the smallest correct harness or CLI change so this scenario behaves as expected.")
    return make_task(
        task_id=task_id,
        instructions="\n".join(parts),
        success_criteria=criterion,
        description=description if isinstance(description, str) else f"Fix UX complaint: {trigger}",
        workspace_files=workspace_files,
        failure_mode=UX_COMPLAINT_FAILURE_MODE,
        extra_metadata=extra_metadata,
    )


def ingest_inbox_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch an inbox bundle to the correct held-in task ingester.

    Legacy command bundles omitted ``kind``; keep treating those as failing-command bundles.
    """

    kind = bundle.get("kind", "failing_command")
    if kind in {"failing_command", "command", None}:
        return ingest_failing_bundle(bundle)
    if kind == UX_BUNDLE_KIND:
        return ingest_ux_bundle(bundle)
    raise TaskSourceError(f"unsupported inbox bundle kind: {kind!r}")


def dedupe_tasks(tasks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep the last task for each id, preserving first-seen order (later submissions supersede)."""

    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for task in tasks:
        tid = str(task.get("id"))
        if tid not in by_id:
            order.append(tid)
        by_id[tid] = dict(task)
    return [by_id[tid] for tid in order]


def assemble_corpus(
    base_corpus: Mapping[str, Any],
    extra_held_in_tasks: Iterable[Mapping[str, Any]],
    *,
    corpus_id: str = "agentic-coding-live",
) -> dict[str, Any]:
    """Merge the static base corpus with accumulated held-in tasks into a loadable corpus dict.

    The base corpus's held-out tasks are preserved verbatim (the fixed regression yardstick). Extra
    tasks are forced to held-in and de-duplicated by id against each other and the base. A base task and
    an extra task sharing an id resolves to the extra (so a re-ingested failure updates in place).
    """

    base_tasks = base_corpus.get("tasks")
    if not isinstance(base_tasks, list):
        raise TaskSourceError("base corpus must contain a tasks list")
    forced = [{**dict(task), "split": LEARNED_SPLIT} for task in extra_held_in_tasks]
    merged = dedupe_tasks([*base_tasks, *forced])
    return {
        "corpus_version": CORPUS_VERSION,
        "corpus_id": corpus_id,
        "tasks": merged,
    }


def parse_generated_tasks(llm_text: str, *, id_prefix: str = "gen") -> list[dict[str, Any]]:
    """Parse an LLM response into validated held-in task dicts, dropping any malformed entries.

    Expects ``{"tasks": [{"id"?, "instructions", "success_criteria", "workspace_files"?}, ...]}``.
    Malformed entries are skipped rather than failing the batch, so one bad task doesn't waste a whole
    generation round. Ids are namespaced with ``id_prefix`` to avoid colliding with base/ingested tasks.
    """

    obj = _extract_json_object(llm_text)
    if obj is None:
        return []
    raw_tasks = obj.get("tasks")
    if not isinstance(raw_tasks, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw_tasks):
        if not isinstance(item, Mapping):
            continue
        raw_id = item.get("id")
        suffix = raw_id if isinstance(raw_id, str) and raw_id.strip() else str(index)
        try:
            task = make_task(
                task_id=f"{id_prefix}-{suffix}",
                instructions=item.get("instructions", ""),
                success_criteria=item.get("success_criteria", ""),
                description=item.get("description") if isinstance(item.get("description"), str) else None,
                workspace_files=item.get("workspace_files") if item.get("workspace_files") else None,
                failure_mode=GENERATED_FAILURE_MODE,
            )
        except TaskSourceError:
            continue
        out.append(task)
    return out


def generation_prompts(weak_spots: list[str], *, batch: int = 3) -> tuple[str, str]:
    """Build (system, user) prompts asking the model for new held-in tasks targeting weak spots.

    ``weak_spots`` are short human-readable summaries of recent failure signatures (or general areas if
    none are known). The model is asked for self-contained tasks with machine-checkable criteria.
    """

    system = (
        "You design small, self-contained coding tasks used to stress-test and improve an autonomous "
        "coding agent's harness. Each task must be solvable by editing/creating files in a working "
        "directory and must have a success criterion that a separate judge can verify by inspecting the "
        "resulting files or running a command. Tasks must be unambiguous and have exactly one correct "
        "outcome. Respond with ONLY a JSON object, no prose."
    )
    focus = "; ".join(weak_spots) if weak_spots else (
        "general agentic coding correctness: exact output formatting, edge cases, file I/O, and "
        "verifying results before finishing"
    )
    user = (
        f"Produce {batch} new tasks targeting these weak areas: {focus}.\n\n"
        'Return JSON of the form: {"tasks": [{"id": "short-slug", "description": "one line", '
        '"instructions": "what the agent must do, including any command it should make pass", '
        '"success_criteria": "an exact, checkable condition", '
        '"workspace_files": {"relative/path": "file contents"}}]}\n'
        "Rules: workspace_files is optional but recommended (seed inputs/tests). Make success_criteria "
        "exact (specify exact file contents, exit codes, or output). Do not include solver or judge "
        "configuration. Each task must be genuinely failable by a careless agent but solvable by a "
        "careful one."
    )
    return system, user


# A verifier callable runs one candidate task and reports whether it is solvable + well-specified.
# Signature: (task_dict) -> bool. The UI wires this to a fresh agent + Codex judge; tests stub it.
TaskVerifier = Callable[[Mapping[str, Any]], bool]


def filter_verified_tasks(
    tasks: Iterable[Mapping[str, Any]],
    verifier: TaskVerifier | None,
) -> list[dict[str, Any]]:
    """Apply the optional solve+verify guard. With no verifier, tasks pass through unchanged.

    The guard is off by default (verifier=None) per the project's configured policy: new tasks are
    held-in only and the acceptance gate rejects any edit that regresses the fixed held-out set, so a
    malformed task can at worst cause a rejected edit. Enabling the guard quarantines tasks that a fresh
    agent cannot solve (a strong signal the task is ill-specified) before they ever enter the corpus.
    """

    if verifier is None:
        return [dict(task) for task in tasks]
    kept: list[dict[str, Any]] = []
    for task in tasks:
        try:
            if verifier(task):
                kept.append(dict(task))
        except Exception:  # noqa: BLE001 - a verifier failure means "could not confirm" -> drop the task.
            continue
    return kept
