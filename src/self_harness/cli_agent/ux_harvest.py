"""Semantic/control-plane UX failure harvesting for ``self-harness code``."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from self_harness.agentic_session import resolve_zai_api_key, resolve_zai_base_url
from self_harness.cli_agent.harvest import _MAX_SNAPSHOT_BYTES, _MAX_SNAPSHOT_FILES
from self_harness.cli_agent.session import headless_binary_for_backend
from self_harness.llm_proposer import LLMClient, _extract_json_object
from self_harness.task_sources import UX_BUNDLE_KIND
from self_harness.types import write_stable_json

CORRECTION_MARKERS = (
    "wrong",
    "not what i asked",
    "that's not",
    "that is not",
    "you missed",
    "you didn't",
    "you did not",
    "try again",
    "still failing",
    "this is strange",
)
APOLOGY_MARKERS = ("sorry", "apolog", "cannot", "can't", "unable", "not able")
IDENTITY_MISMATCH_MARKERS = ("i'm claude", "i am claude", "made by anthropic", "anthropic")


@dataclass(frozen=True)
class UxCandidate:
    trigger: str
    observation: str
    operating_provider: str
    expected_behavior: str = ""
    observed: str = ""
    checkable_criterion: str = ""
    files: Mapping[str, str] | None = None
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True)
class AdmissionResult:
    admitted: bool
    judge_provider: str | None
    checkable_criterion: str | None
    reason: str
    attempts: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AdmissionVerdict:
    admit: bool
    checkable_criterion: str | None
    reason: str


class JudgeProvider(Protocol):
    provider_id: str

    def admit(self, candidate: UxCandidate) -> AdmissionResult:
        ...


@dataclass
class JudgeProviderRegistry:
    env: Mapping[str, str] | None = None
    providers: Sequence[JudgeProvider] | None = None

    def available(self, *, exclude: str) -> list[JudgeProvider]:
        if self.providers is not None:
            return [provider for provider in self.providers if provider.provider_id != exclude]
        env = self.env if self.env is not None else os.environ
        out: list[JudgeProvider] = []
        if _has_zai_key(env):
            out.append(_CompletionJudgeProvider("glm", _glm_client(env)))
        if env.get("MINIMAX_API_KEY") and env.get("MINIMAX_BASE_URL"):
            out.append(_CompletionJudgeProvider("minimax", _minimax_client(env)))
        if env.get("QWEN_SGLANG_BASE_URL"):
            out.append(_CompletionJudgeProvider("qwen", _qwen_client(env)))
        for provider in ("codex", "agy", "claude"):
            binary = headless_binary_for_backend(provider)
            if shutil.which(binary):
                out.append(_HeadlessJudgeProvider(provider, binary))
        return [provider for provider in out if provider.provider_id != exclude]


@dataclass
class SecondaryModelJudge:
    registry: JudgeProviderRegistry = field(default_factory=JudgeProviderRegistry)
    rng: random.Random = field(default_factory=random.SystemRandom)
    max_attempts: int = 2

    def admit(self, candidate: UxCandidate) -> AdmissionResult:
        eligible = self.registry.available(exclude=candidate.operating_provider)
        if not eligible:
            return AdmissionResult(False, None, None, "no_eligible_judge")
        remaining = list(eligible)
        attempts: list[str] = []
        last_error = ""
        for _ in range(min(self.max_attempts, len(remaining))):
            index = self.rng.randrange(len(remaining))
            provider = remaining.pop(index)
            attempts.append(provider.provider_id)
            try:
                result = provider.admit(candidate)
            except Exception as exc:  # noqa: BLE001 - judge provider failure means retry/quarantine.
                last_error = f"{provider.provider_id}: {exc}"
                continue
            return AdmissionResult(
                result.admitted,
                result.judge_provider or provider.provider_id,
                result.checkable_criterion,
                result.reason,
                tuple(attempts),
            )
        return AdmissionResult(
            False,
            attempts[-1] if attempts else None,
            None,
            last_error or "judge_unavailable",
            tuple(attempts),
        )


@dataclass
class UxFailureHarvester:
    inbox_dir: Path
    workdir: Path
    judge: SecondaryModelJudge
    enabled: bool = True
    _candidates: list[UxCandidate] = field(default_factory=list)
    _written: list[str] = field(default_factory=list)
    _rejected: list[str] = field(default_factory=list)

    def report(
        self,
        *,
        trigger: str,
        observation: str,
        operating_provider: str,
        expected_behavior: str = "",
        observed: str = "",
        checkable_criterion: str = "",
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if not self.enabled:
            return
        self._candidates.append(
            UxCandidate(
                trigger=trigger.strip(),
                observation=observation.strip(),
                operating_provider=operating_provider.strip(),
                expected_behavior=expected_behavior.strip(),
                observed=observed.strip(),
                checkable_criterion=checkable_criterion.strip(),
                files=self._snapshot_paths(set()),
                metadata=metadata,
            )
        )

    def observe_turn(
        self,
        *,
        user_text: str,
        final_text: str,
        stop_reason: str,
        error: str | None,
        tool_activity: Sequence[str],
        operating_provider: str,
        model_status: str,
        previous_assistant_text: str = "",
    ) -> None:
        if not self.enabled:
            return
        lowered_user = user_text.lower()
        lowered_final = final_text.lower()
        if any(marker in lowered_user for marker in CORRECTION_MARKERS) and previous_assistant_text:
            self.report(
                trigger="operator-correction",
                observation=f"Operator correction: {user_text}",
                operating_provider=operating_provider,
                expected_behavior="The previous assistant response should satisfy the operator request.",
                observed=previous_assistant_text[:1000],
                metadata={"trigger_kind": "operator-correction"},
            )
        if operating_provider != "claude" and any(marker in lowered_final for marker in IDENTITY_MISMATCH_MARKERS):
            self.report(
                trigger="provider-identity-contradiction",
                observation=f"Assistant response appears to contradict active provider {operating_provider}.",
                operating_provider=operating_provider,
                expected_behavior=(
                    f"Identity/status answers should report provider {operating_provider} "
                    "from SelfHarness runtime state."
                ),
                observed=final_text[:1000],
                checkable_criterion=(
                    "Asking `what model are you` in the SelfHarness Code CLI reports the active "
                    "SelfHarness provider/model instead of claiming an unrelated provider."
                ),
                metadata={"trigger_kind": "provider-identity-contradiction"},
            )
        if "ignored invalid" in model_status:
            self.report(
                trigger="invalid-provider-state",
                observation=f"Model status included invalid state: {model_status}",
                operating_provider=operating_provider,
                expected_behavior="Provider/model/effort status should not present invalid values as active.",
                checkable_criterion=(
                    "The status text marks invalid provider options as ignored or clears them "
                    "before a model turn."
                ),
                metadata={"trigger_kind": "invalid-provider-state"},
            )
        failed = [item for item in tool_activity if item.endswith("(error)")]
        if len(failed) != len(set(failed)):
            self.report(
                trigger="repeated-identical-tool-failure",
                observation="A turn repeated the same failing tool command.",
                operating_provider=operating_provider,
                expected_behavior="The agent should adjust strategy after an identical tool failure.",
                observed="\n".join(failed[:6]),
                metadata={"trigger_kind": "repeated-identical-tool-failure"},
            )
        if (error or failed) and any(marker in lowered_final for marker in APOLOGY_MARKERS):
            self.report(
                trigger="apology-after-failure",
                observation="The assistant ended with an apology/hedge after a failed tool or model error.",
                operating_provider=operating_provider,
                expected_behavior="The assistant should surface the concrete failure and next repair step.",
                observed=final_text[:1000],
                metadata={"trigger_kind": "apology-after-failure"},
            )
        if stop_reason == "max_steps":
            self.report(
                trigger="max-steps-exhausted",
                observation="The turn exhausted the step budget without completing.",
                operating_provider=operating_provider,
                expected_behavior="The CLI should preserve and learn from step-budget exhaustion patterns.",
                observed=final_text[:1000],
                metadata={"trigger_kind": "max-steps-exhausted"},
            )

    def flush(self, *, id_prefix: str) -> tuple[list[str], list[str]]:
        if not self.enabled or not self._candidates:
            return [], []
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        processed = self.inbox_dir / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        rejected: list[str] = []
        for index, candidate in enumerate(self._candidates, start=1):
            admission = self.judge.admit(candidate)
            bundle_id = f"{id_prefix}-ux-{index:02d}"
            bundle = _bundle_from_candidate(bundle_id, candidate, admission)
            if admission.admitted and admission.checkable_criterion:
                write_stable_json(self.inbox_dir / f"{bundle_id}.json", bundle)
                written.append(bundle_id)
            else:
                write_stable_json(processed / f"{bundle_id}.json.rejected", bundle)
                rejected.append(bundle_id)
        self._written.extend(written)
        self._rejected.extend(rejected)
        self._candidates.clear()
        return written, rejected

    @property
    def written_ids(self) -> list[str]:
        return list(self._written)

    @property
    def rejected_ids(self) -> list[str]:
        return list(self._rejected)

    def seed_written(self, ids: list[str]) -> None:
        self._written.extend(ids)

    def _snapshot_paths(self, extra: set[str]) -> dict[str, str]:
        files: dict[str, str] = {}
        for rel in sorted(extra):
            if len(files) >= _MAX_SNAPSHOT_FILES:
                break
            target = (self.workdir / rel).resolve()
            try:
                if self.workdir.resolve() not in target.parents and target != self.workdir.resolve():
                    continue
                if not target.is_file():
                    continue
                text = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if len(text.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
                continue
            files[rel] = text
        return files


def _bundle_from_candidate(bundle_id: str, candidate: UxCandidate, admission: AdmissionResult) -> dict[str, Any]:
    metadata = dict(candidate.metadata or {})
    metadata.setdefault("operating_provider", candidate.operating_provider)
    if admission.judge_provider:
        metadata.setdefault("admitting_judge", admission.judge_provider)
    metadata.setdefault("admission_reason", admission.reason)
    if admission.attempts:
        metadata.setdefault("judge_attempts", list(admission.attempts))
    bundle: dict[str, Any] = {
        "id": bundle_id,
        "kind": UX_BUNDLE_KIND,
        "trigger": candidate.trigger,
        "observation": candidate.observation,
        "checkable_criterion": admission.checkable_criterion or candidate.checkable_criterion,
        "operating_provider": candidate.operating_provider,
        "admitting_judge": admission.judge_provider or "",
        "admission_reason": admission.reason,
        "metadata": metadata,
    }
    if candidate.expected_behavior:
        bundle["expected_behavior"] = candidate.expected_behavior
    if candidate.observed:
        bundle["observed"] = candidate.observed
    if candidate.files:
        bundle["files"] = dict(candidate.files)
    return bundle


class _CompletionJudgeProvider:
    def __init__(self, provider_id: str, client: LLMClient) -> None:
        self.provider_id = provider_id
        self.client = client

    def admit(self, candidate: UxCandidate) -> AdmissionResult:
        text = self.client.complete(*_judge_prompts(candidate))
        verdict = _parse_admission_json(text)
        return AdmissionResult(
            verdict.admit,
            self.provider_id,
            verdict.checkable_criterion,
            verdict.reason,
        )


class _HeadlessJudgeProvider:
    def __init__(self, provider_id: str, binary: str) -> None:
        self.provider_id = provider_id
        self.binary = binary

    def admit(self, candidate: UxCandidate) -> AdmissionResult:
        system, user = _judge_prompts(candidate)
        prompt = f"{system}\n\n{user}"
        with tempfile.TemporaryDirectory(prefix="self-harness-ux-judge-") as tmp:
            out = _run_headless_judge(self.provider_id, self.binary, prompt, Path(tmp))
        verdict = _parse_admission_json(out)
        return AdmissionResult(
            verdict.admit,
            self.provider_id,
            verdict.checkable_criterion,
            verdict.reason,
        )


def _judge_prompts(candidate: UxCandidate) -> tuple[str, str]:
    system = (
        "You are a secondary admission judge for SelfHarness UX failure harvesting. "
        "Admit only real, actionable semantic/control-plane failures. Reject vague complaints. "
        "Return ONLY JSON: {\"admit\": boolean, \"checkable_criterion\": string|null, \"reason\": string}. "
        "The criterion must be exact enough for another model judge to verify from a fresh run, "
        "for example an output substring, status text condition, exit code, or file content."
    )
    user = (
        f"Operating provider: {candidate.operating_provider}\n"
        f"Trigger: {candidate.trigger}\n"
        f"Observation: {candidate.observation}\n"
        f"Expected behavior: {candidate.expected_behavior or '(not supplied)'}\n"
        f"Observed output: {candidate.observed or '(not supplied)'}\n"
        f"Operator-supplied criterion: {candidate.checkable_criterion or '(not supplied)'}"
    )
    return system, user


def _parse_admission_json(text: str) -> _AdmissionVerdict:
    parsed = _extract_json_object(text)
    if parsed is None:
        raise ValueError("judge did not return JSON")
    admit = parsed.get("admit")
    reason = parsed.get("reason")
    criterion = parsed.get("checkable_criterion")
    if not isinstance(admit, bool):
        raise ValueError("judge verdict missing boolean admit")
    if not isinstance(reason, str) or not reason.strip():
        reason = "admitted" if admit else "rejected"
    if criterion is not None and not isinstance(criterion, str):
        criterion = None
    if admit and not (isinstance(criterion, str) and criterion.strip()):
        admit = False
        criterion = None
        reason = "admitted verdict omitted checkable_criterion"
    return _AdmissionVerdict(
        admit=admit,
        checkable_criterion=criterion.strip() if isinstance(criterion, str) else None,
        reason=reason.strip(),
    )


def _run_headless_judge(provider: str, binary: str, prompt: str, workdir: Path) -> str:
    if provider == "codex":
        output_path = workdir / "judge.txt"
        command = [
            binary,
            "exec",
            "--skip-git-repo-check",
            "--cd",
            str(workdir),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        completed = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=90, check=False)
        if output_path.is_file():
            return output_path.read_text(encoding="utf-8")
        return completed.stdout + "\n" + completed.stderr
    if provider == "agy":
        command = [binary, "--print", "--dangerously-skip-permissions", "--print-timeout", "90s"]
    elif provider == "claude":
        command = [binary, "--print", "--dangerously-skip-permissions"]
    else:
        raise ValueError(f"unsupported headless judge provider: {provider}")
    completed = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=90, check=False)
    if completed.returncode != 0 and not completed.stdout.strip():
        raise RuntimeError(completed.stderr.strip() or f"{provider} judge exited {completed.returncode}")
    return completed.stdout


def _has_zai_key(env: Mapping[str, str]) -> bool:
    if env.get("ZAI_API_KEY"):
        return True
    if env is os.environ:
        try:
            resolve_zai_api_key()
            return True
        except Exception:  # noqa: BLE001 - absence means unavailable for selection.
            return False
    return False


def _glm_client(env: Mapping[str, str]) -> LLMClient:
    from self_harness.adapters.agentic.runner import DEFAULT_GLM_MODEL
    from self_harness.adapters.llm.paper_models import GLMClient
    from self_harness.model_backend_preflight import build_zai_transport

    api_key = resolve_zai_api_key(env if env is not os.environ else None)
    base_url = resolve_zai_base_url(env if env is not os.environ else None)
    return GLMClient(
        model=DEFAULT_GLM_MODEL,
        max_tokens=512,
        temperature=0.0,
        transport=build_zai_transport(base_url=base_url, api_key=api_key),
    )


def _minimax_client(env: Mapping[str, str]) -> LLMClient:
    from self_harness.adapters.llm.paper_models import MiniMaxClient
    from self_harness.model_backend_preflight import build_minimax_transport

    return MiniMaxClient(
        max_tokens=512,
        temperature=0.0,
        transport=build_minimax_transport(base_url=env["MINIMAX_BASE_URL"], api_key=env["MINIMAX_API_KEY"]),
    )


def _qwen_client(env: Mapping[str, str]) -> LLMClient:
    from self_harness.adapters.llm.paper_models import QwenClient
    from self_harness.model_backend_preflight import build_qwen_transport

    return QwenClient(
        max_tokens=512,
        temperature=0.0,
        transport=build_qwen_transport(base_url=env["QWEN_SGLANG_BASE_URL"], api_key=env.get("QWEN_API_KEY")),
    )
