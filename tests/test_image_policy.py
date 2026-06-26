import json
from pathlib import Path

import pytest

from self_harness.image_policy import (
    ImagePolicyError,
    evaluate_image_policy,
    load_image_policy,
    validate_image_digest,
)

VALID_DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def test_image_policy_allows_active_image_digest(tmp_path: Path) -> None:
    path = _write_policy(tmp_path / "policy.json", [{"image": "trusted/verifier:1", "digest": VALID_DIGEST}])
    policy = load_image_policy(path)

    decision = evaluate_image_policy(policy, "trusted/verifier:1", VALID_DIGEST, require_digest=True)

    assert decision.allowed
    assert decision.code == "allowed"
    assert decision.entry is not None
    assert decision.entry.labels == ()


def test_image_policy_allows_image_level_entry_with_supplied_digest(tmp_path: Path) -> None:
    path = _write_policy(tmp_path / "policy.json", [{"image": "trusted/verifier:1"}])
    policy = load_image_policy(path)

    decision = evaluate_image_policy(policy, "trusted/verifier:1", VALID_DIGEST, require_digest=True)

    assert decision.allowed
    assert decision.code == "allowed"
    assert decision.entry is not None
    assert decision.entry.digest is None


@pytest.mark.parametrize(
    ("image", "digest", "code"),
    [
        ("missing/verifier:1", VALID_DIGEST, "missing-policy"),
        ("trusted/verifier:1", None, "missing-digest"),
        ("trusted/verifier:1", OTHER_DIGEST, "digest-mismatch"),
    ],
)
def test_image_policy_rejects_missing_or_mismatched_entries(
    tmp_path: Path,
    image: str,
    digest: str | None,
    code: str,
) -> None:
    policy = load_image_policy(
        _write_policy(tmp_path / "policy.json", [{"image": "trusted/verifier:1", "digest": VALID_DIGEST}])
    )

    decision = evaluate_image_policy(policy, image, digest)

    assert not decision.allowed
    assert decision.code == code


def test_image_policy_rejects_retired_and_revoked_entries(tmp_path: Path) -> None:
    policy = load_image_policy(
        _write_policy(
            tmp_path / "policy.json",
            [
                {"image": "retired/verifier:1", "digest": VALID_DIGEST, "status": "retired"},
                {"image": "revoked/verifier:1", "digest": VALID_DIGEST, "status": "revoked"},
            ],
        )
    )

    assert evaluate_image_policy(policy, "retired/verifier:1", VALID_DIGEST).code == "not-active"
    assert evaluate_image_policy(policy, "revoked/verifier:1", VALID_DIGEST).code == "not-active"


def test_empty_policy_is_valid_and_denies_all(tmp_path: Path) -> None:
    policy = load_image_policy(_write_policy(tmp_path / "policy.json", []))

    decision = evaluate_image_policy(policy, "trusted/verifier:1", VALID_DIGEST)

    assert not decision.allowed
    assert decision.code == "missing-policy"


def test_require_digest_without_policy_still_requires_valid_digest() -> None:
    assert evaluate_image_policy(None, "trusted/verifier:1", VALID_DIGEST, require_digest=True).allowed
    assert evaluate_image_policy(None, "trusted/verifier:1", None, require_digest=True).code == "missing-digest"
    assert evaluate_image_policy(None, "trusted/verifier:1", "sha256:bad", require_digest=True).code == "invalid-digest"


def test_image_policy_load_rejects_bad_status_duplicate_and_digest(tmp_path: Path) -> None:
    with pytest.raises(ImagePolicyError):
        load_image_policy(_write_policy(tmp_path / "bad-status.json", [{"image": "x", "status": "unknown"}]))
    with pytest.raises(ImagePolicyError):
        load_image_policy(
            _write_policy(
                tmp_path / "duplicate.json",
                [
                    {"image": "x", "digest": VALID_DIGEST},
                    {"image": "x", "digest": VALID_DIGEST},
                ],
            )
        )
    with pytest.raises(ImagePolicyError):
        validate_image_digest("sha256:" + "A" * 64)


def _write_policy(path: Path, entries: list[dict[str, object]]) -> Path:
    normalized = [
        {
            "image": entry["image"],
            "digest": entry.get("digest"),
            "status": entry.get("status", "active"),
            "labels": entry.get("labels", {}),
        }
        for entry in entries
    ]
    path.write_text(json.dumps({"policy_version": "1", "entries": normalized}), encoding="utf-8")
    return path
