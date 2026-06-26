from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from self_harness.model_backend_preflight import (
    evaluate_model_backend_preflight,
    model_backend_preflight_report_to_jsonable,
)
from self_harness.types import stable_json_dumps

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts") / "model_backend_preflight.py"
FIXTURES = Path("tests") / "fixtures" / "model_backend"


class FakeTransport:
    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        assert payload["model"] == "glm-5.2"
        return {
            "choices": [{"message": {"content": "glm live preflight ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }


class FailingTransport:
    def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        raise RuntimeError("provider unavailable")


def test_model_backend_preflight_dry_run_is_no_contact_not_ready() -> None:
    report = evaluate_model_backend_preflight(mode="dry-run", backend_ids=("all",), env={})
    payload = model_backend_preflight_report_to_jsonable(report)

    assert payload["ok"] is False
    assert payload["mode"] == "dry-run"
    assert payload["reproduction_claimed"] is False
    assert {check["status"] for check in payload["checks"]} == {"not-run"}
    assert "not benchmark reproduction evidence" in payload["boundary"]


def test_model_backend_preflight_replay_parses_all_paper_backends() -> None:
    report = evaluate_model_backend_preflight(
        mode="replay",
        backend_ids=("all",),
        env={},
        replay_path=REPO_ROOT / FIXTURES,
        today="2026-06-24",
    )
    payload = model_backend_preflight_report_to_jsonable(report)

    assert payload["ok"] is True
    assert payload["mode"] == "replay"
    assert payload["backends"] == ["minimax", "qwen", "glm"]
    assert {check["status"] for check in payload["checks"]} == {"pass"}
    assert all(check["metadata"]["usage"]["total_tokens"] > 0 for check in payload["checks"])


def test_model_backend_preflight_live_uses_injected_transport_without_provider_contact() -> None:
    report = evaluate_model_backend_preflight(
        mode="live",
        backend_ids=("glm",),
        env={"ZAI_BASE_URL": "https://example.invalid/api/paas/v4", "ZAI_API_KEY": "secret"},
        transport_overrides={"glm": FakeTransport()},
    )
    payload = model_backend_preflight_report_to_jsonable(report)

    assert payload["ok"] is True
    check = payload["checks"][0]
    assert check["backend"] == "glm"
    assert check["status"] == "pass"
    assert check["metadata"]["credential_env"] == "ZAI_API_KEY"
    assert check["metadata"]["usage"]["total_tokens"] == 8


def test_model_backend_preflight_live_fails_closed_on_missing_env() -> None:
    report = evaluate_model_backend_preflight(mode="live", backend_ids=("glm",), env={})
    payload = model_backend_preflight_report_to_jsonable(report)

    # The GLM coding plan defaults its endpoint, so only the API key is strictly required; the
    # check must still fail closed when the key is absent.
    assert payload["ok"] is False
    assert payload["checks"][0]["status"] == "fail"
    assert "ZAI_API_KEY" in payload["checks"][0]["detail"]
    assert "ZAI_BASE_URL" not in payload["checks"][0]["detail"]


def test_model_backend_preflight_live_transport_error_is_failed_check() -> None:
    report = evaluate_model_backend_preflight(
        mode="live",
        backend_ids=("glm",),
        env={"ZAI_BASE_URL": "https://example.invalid/api/paas/v4", "ZAI_API_KEY": "secret"},
        transport_overrides={"glm": FailingTransport()},
    )
    payload = model_backend_preflight_report_to_jsonable(report)

    assert payload["ok"] is False
    assert payload["checks"][0]["status"] == "fail"
    assert "provider unavailable" in payload["checks"][0]["detail"]


def test_model_backend_preflight_cli_rejects_replay_fixture_claiming_reproduction(tmp_path: Path) -> None:
    replay = tmp_path / "glm_chat_completion_replay.json"
    out = tmp_path / "model-backend-preflight.json"
    replay.write_text(
        stable_json_dumps(
            {
                "schema_version": "1.0",
                "backend": "glm",
                "reproduction_claimed": True,
                "response": {"choices": [{"message": {"content": "bad"}}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "replay",
            "--backend",
            "glm",
            "--replay",
            str(replay),
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 3
    assert "claims benchmark reproduction" in completed.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is False
