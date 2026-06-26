import json
from datetime import date
from pathlib import Path

import pytest

from self_harness.operator_bundle import (
    OperatorPolicyBundleError,
    load_operator_policy_bundle,
    operator_policy_bundle_to_jsonable,
)

FIXTURE_BUNDLE = Path("tests/fixtures/operator_bundle/valid.json")


def test_load_operator_policy_bundle_resolves_paths() -> None:
    bundle = load_operator_policy_bundle(FIXTURE_BUNDLE, today=date(2026, 6, 24))
    payload = operator_policy_bundle_to_jsonable(bundle)

    assert bundle.bundle_version == "1"
    assert bundle.owner == "self-harness-tests"
    assert bundle.expires_on == date(2026, 12, 31)
    assert bundle.image_policy is not None
    assert bundle.image_policy.name == "image_policy.json"
    assert bundle.freshness_policy is not None
    assert bundle.freshness_policy.name == "freshness_policy.json"
    assert bundle.scanner_db_freshness_policy is not None
    assert bundle.trusted_public_keys[0].name == "trusted.ed25519.pub"
    assert payload["owner"] == "self-harness-tests"


def test_operator_policy_bundle_rejects_expired_bundle(tmp_path: Path) -> None:
    bundle_path = _write_bundle(tmp_path, expires_on="2026-01-01")

    with pytest.raises(OperatorPolicyBundleError, match="expired"):
        load_operator_policy_bundle(bundle_path, today=date(2026, 6, 24))


def test_operator_policy_bundle_rejects_missing_referenced_file(tmp_path: Path) -> None:
    bundle_path = _write_bundle(tmp_path, image_policy="missing-image-policy.json")

    with pytest.raises(OperatorPolicyBundleError, match="image_policy file does not exist"):
        load_operator_policy_bundle(bundle_path, today=date(2026, 6, 24))


def test_operator_policy_bundle_rejects_unknown_fields(tmp_path: Path) -> None:
    bundle_path = _write_bundle(tmp_path, extra={"inline_policy": {}})

    with pytest.raises(OperatorPolicyBundleError, match="unknown fields"):
        load_operator_policy_bundle(bundle_path, today=date(2026, 6, 24))


def test_operator_policy_bundle_rejects_malformed_json(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text("{", encoding="utf-8")

    with pytest.raises(OperatorPolicyBundleError, match="invalid operator policy bundle JSON"):
        load_operator_policy_bundle(bundle_path, today=date(2026, 6, 24))


def _write_bundle(
    tmp_path: Path,
    *,
    expires_on: str = "2026-12-31",
    image_policy: str = "image-policy.json",
    extra: dict[str, object] | None = None,
) -> Path:
    bundle_path = tmp_path / "bundle.json"
    (tmp_path / "image-policy.json").write_text('{"policy_version":"1","entries":[]}', encoding="utf-8")
    payload = {
        "bundle_version": "1",
        "owner": "tests",
        "expires_on": expires_on,
        "image_policy": image_policy,
        **(extra or {}),
    }
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")
    return bundle_path
