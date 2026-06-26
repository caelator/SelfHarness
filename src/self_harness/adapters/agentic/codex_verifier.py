from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from self_harness.adapters.verifier_result import VerifierResult, outcome_from_verifier_result
from self_harness.exceptions import CodexVerifierError
from self_harness.types import FailureCategory, VerifierOutcome

DEFAULT_CODEX_BINARY = "codex"
DEFAULT_JUDGE_TIMEOUT_SECONDS = 180
JUDGE_MECHANISM = "codex-judge"

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "passed": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["passed", "reason"],
}


@dataclass(frozen=True)
class CodexVerifier:
    """Judge task success with the Codex CLI (``codex exec``) in a read-only sandbox.

    The judge inspects the post-attempt workspace and returns a structured ``{passed, reason}``
    verdict. Tooling failures (binary missing, timeout, unparseable output) are mapped to a stable
    ``environment-error`` outcome distinct from a genuine ``verifier-fail`` so the two never cluster
    together. The judge runs read-only and cannot modify the workspace.
    """

    binary: str = DEFAULT_CODEX_BINARY
    timeout_seconds: int = DEFAULT_JUDGE_TIMEOUT_SECONDS

    def judge(self, *, success_criteria: str, task_description: str, workdir: Path) -> VerifierOutcome:
        prompt = _build_prompt(success_criteria=success_criteria, task_description=task_description)
        schema_path = workdir / ".self-harness-judge-schema.json"
        try:
            schema_path.write_text(json.dumps(_OUTPUT_SCHEMA), encoding="utf-8")
            verdict = self._invoke(prompt, workdir=workdir, schema_path=schema_path)
        except CodexVerifierError as exc:
            return _unavailable_outcome(str(exc))
        finally:
            schema_path.unlink(missing_ok=True)
        return _outcome_from_verdict(verdict)

    def _invoke(self, prompt: str, *, workdir: Path, schema_path: Path) -> dict[str, Any]:
        command = [
            self.binary,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "--cd",
            str(workdir),
            "--output-schema",
            str(schema_path),
        ]
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CodexVerifierError(f"codex binary not found: {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CodexVerifierError(f"codex judge timed out after {self.timeout_seconds}s") from exc
        if completed.returncode != 0 and not completed.stdout.strip():
            detail = completed.stderr.strip()[:300] or f"exit {completed.returncode}"
            raise CodexVerifierError(f"codex judge failed: {detail}")
        return parse_codex_verdict(completed.stdout)


def parse_codex_verdict(stdout: str) -> dict[str, Any]:
    """Extract the final ``{passed, reason}`` verdict from ``codex exec --json`` JSONL output."""

    final_message: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            final_message = item["text"]
    if final_message is None:
        raise CodexVerifierError("codex judge produced no agent message")
    try:
        verdict = json.loads(final_message)
    except json.JSONDecodeError as exc:
        raise CodexVerifierError("codex judge message was not valid JSON") from exc
    if not isinstance(verdict, dict) or not isinstance(verdict.get("passed"), bool):
        raise CodexVerifierError("codex judge verdict missing boolean 'passed'")
    return verdict


def _build_prompt(*, success_criteria: str, task_description: str) -> str:
    return (
        "You are an objective grader. Inspect the files in the current working directory to decide "
        "whether a coding task was completed successfully. Do not modify anything.\n\n"
        f"TASK: {task_description}\n\n"
        f"SUCCESS CRITERIA: {success_criteria}\n\n"
        "Return ONLY a JSON object matching the schema: set passed=true only if the success "
        "criteria are fully met by the current workspace state, and give a one-sentence reason."
    )


def _outcome_from_verdict(verdict: dict[str, Any]) -> VerifierOutcome:
    passed = bool(verdict.get("passed"))
    reason = verdict.get("reason")
    message = reason if isinstance(reason, str) and reason else ("passed" if passed else "verifier rejected")
    result: VerifierResult = {
        "passed": passed,
        "failure_category": None if passed else FailureCategory.VERIFIER_FAIL.value,
        "mechanism": JUDGE_MECHANISM,
        "message": message,
    }
    return outcome_from_verifier_result(
        result,
        default_mechanism=JUDGE_MECHANISM,
        error_factory=CodexVerifierError,
    )


def _unavailable_outcome(detail: str) -> VerifierOutcome:
    return VerifierOutcome(
        passed=False,
        terminal_cause=FailureCategory.ENVIRONMENT_ERROR.value,
        causal_status="environment",
        mechanism="codex-judge-unavailable",
        message=f"codex judge unavailable: {detail}"[:300],
    )
