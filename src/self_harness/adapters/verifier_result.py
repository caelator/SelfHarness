from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypedDict

from self_harness.types import FailureCategory, VerifierOutcome


class VerifierResult(TypedDict):
    passed: bool
    failure_category: str | None
    mechanism: str
    message: str


StructuredVerifierErrorFactory = Callable[[str], Exception]

def outcome_from_verifier_result(
    raw_result: VerifierResult | Mapping[str, object],
    *,
    default_mechanism: str,
    error_factory: StructuredVerifierErrorFactory,
) -> VerifierOutcome:
    result = normalize_verifier_result(raw_result, default_mechanism=default_mechanism, error_factory=error_factory)
    if result["passed"]:
        return VerifierOutcome(
            passed=True,
            terminal_cause=FailureCategory.VERIFIER_PASS.value,
            causal_status="confirmed",
            mechanism=default_mechanism,
            message=result["message"] or "verifier passed",
        )
    failure_category = result["failure_category"] or FailureCategory.VERIFIER_FAIL.value
    try:
        category = FailureCategory(failure_category)
    except ValueError as exc:
        raise error_factory(f"invalid-failure-category: {failure_category}") from exc
    return VerifierOutcome(
        passed=False,
        terminal_cause=category.value,
        causal_status="rejected",
        mechanism=result["mechanism"] or default_mechanism,
        message=result["message"] or category.value,
    )


def normalize_verifier_result(
    raw_result: VerifierResult | Mapping[str, object],
    *,
    default_mechanism: str,
    error_factory: StructuredVerifierErrorFactory,
) -> VerifierResult:
    if not isinstance(raw_result, Mapping):
        raise error_factory("verifier result must be a mapping")
    passed = raw_result.get("passed")
    failure_category = raw_result.get("failure_category")
    mechanism = raw_result.get("mechanism", default_mechanism)
    message = raw_result.get("message", "")
    if not isinstance(passed, bool):
        raise error_factory("verifier result field passed must be bool")
    if failure_category is not None and not isinstance(failure_category, str):
        raise error_factory("verifier result field failure_category must be string or null")
    if not isinstance(mechanism, str):
        raise error_factory("verifier result field mechanism must be string")
    if not isinstance(message, str):
        raise error_factory("verifier result field message must be string")
    return {
        "passed": passed,
        "failure_category": failure_category,
        "mechanism": mechanism,
        "message": message,
    }
