from __future__ import annotations

from pathlib import Path

from self_harness.types import FailureCategory, Task


def setup(task: Task, workdir: Path, attempt_index: int) -> None:
    if task.metadata.get("verifier_selector") == "setup-exception":
        raise RuntimeError("setup failed")
    (workdir / "setup.txt").write_text(f"{task.id}:{attempt_index}\n", encoding="utf-8")


def verify(task: Task, workdir: Path, attempt_index: int) -> dict[str, object]:
    selector = task.metadata.get("verifier_selector", "pass")
    if selector == "pass":
        return {
            "passed": True,
            "failure_category": None,
            "mechanism": "fixture-pass",
            "message": "fixture verifier passed",
        }
    if selector == "fail":
        return {
            "passed": False,
            "failure_category": FailureCategory.ASSERTION_FAIL.value,
            "mechanism": "fixture-assertion",
            "message": "fixture verifier failed",
        }
    if selector == "needs-setup":
        marker = workdir / "setup.txt"
        return {
            "passed": marker.exists(),
            "failure_category": None if marker.exists() else FailureCategory.MISSING_ARTIFACT.value,
            "mechanism": "fixture-setup-marker",
            "message": f"attempt={attempt_index}",
        }
    if selector == "unknown-category":
        return {
            "passed": False,
            "failure_category": "partial-pass",
            "mechanism": "fixture-unknown",
            "message": "unknown category",
        }
    if selector == "verify-exception":
        raise RuntimeError("verify failed")
    return {
        "passed": False,
        "failure_category": FailureCategory.VERIFIER_FAIL.value,
        "mechanism": f"selector:{selector}",
        "message": "selector fallback",
    }
