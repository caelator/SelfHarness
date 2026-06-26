from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

IMAGE_POLICY_VERSION = "1"


class ImagePolicyStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"
    REVOKED = "revoked"


@dataclass(frozen=True)
class ImagePolicyEntry:
    image: str
    digest: str | None
    status: ImagePolicyStatus
    labels: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ImagePolicy:
    policy_version: str
    entries: tuple[ImagePolicyEntry, ...]


@dataclass(frozen=True)
class ImagePolicyDecision:
    allowed: bool
    code: str
    message: str
    entry: ImagePolicyEntry | None = None


class ImagePolicyError(RuntimeError):
    """Raised when a container image policy is invalid or rejects an image."""

    def __init__(self, decision: ImagePolicyDecision) -> None:
        self.decision = decision
        super().__init__(decision.message)


def empty_image_policy() -> ImagePolicy:
    return ImagePolicy(policy_version=IMAGE_POLICY_VERSION, entries=())


def load_image_policy(path: Path) -> ImagePolicy:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImagePolicyError(_decision("policy-missing", f"missing image policy: {path}")) from exc
    except json.JSONDecodeError as exc:
        raise ImagePolicyError(_decision("policy-invalid-json", f"invalid image policy JSON: {path}")) from exc
    if not isinstance(data, dict):
        raise ImagePolicyError(_decision("policy-invalid-schema", "image policy JSON must be an object"))
    version = _required_str(data, "policy_version", "image policy")
    if version != IMAGE_POLICY_VERSION:
        raise ImagePolicyError(_decision("policy-unsupported-version", f"unsupported image policy version: {version}"))
    rows = data.get("entries")
    if not isinstance(rows, list):
        raise ImagePolicyError(_decision("policy-invalid-schema", "image policy must include an entries list"))
    entries = tuple(_entry_from_row(row, index) for index, row in enumerate(rows))
    _reject_duplicates(entries)
    return ImagePolicy(policy_version=version, entries=entries)


def evaluate_image_policy(
    policy: ImagePolicy | None,
    image: str,
    digest: str | None,
    *,
    require_digest: bool = False,
) -> ImagePolicyDecision:
    if not image:
        return _decision("image-missing", "container image must be non-empty")
    if digest is not None and (policy is not None or require_digest):
        digest_error = _digest_error(digest)
        if digest_error is not None:
            return digest_error
    if require_digest and digest is None:
        return _decision("missing-digest", "container image digest is required")
    if policy is None:
        return _decision("allowed", "container image allowed", allowed=True)

    image_entries = tuple(entry for entry in policy.entries if entry.image == image)
    if not image_entries:
        return _decision("missing-policy", f"container image is not allowlisted: {image}")
    if digest is None:
        image_only = tuple(entry for entry in image_entries if entry.digest is None)
        if image_only:
            return _entry_status_decision(image_only[0])
        return _decision("missing-digest", f"container image digest is required by policy: {image}")

    exact = tuple(entry for entry in image_entries if entry.digest == digest)
    if exact:
        return _entry_status_decision(exact[0])
    image_only = tuple(entry for entry in image_entries if entry.digest is None)
    if image_only:
        return _entry_status_decision(image_only[0])
    if any(entry.digest is not None for entry in image_entries):
        return _decision("digest-mismatch", f"container image digest is not allowlisted for {image}")
    return _decision("missing-policy", f"container image digest is not allowlisted for {image}")


def ensure_image_allowed(
    policy: ImagePolicy | None,
    image: str,
    digest: str | None,
    *,
    require_digest: bool = False,
) -> ImagePolicyDecision:
    decision = evaluate_image_policy(policy, image, digest, require_digest=require_digest)
    if not decision.allowed:
        raise ImagePolicyError(decision)
    return decision


def validate_image_digest(digest: str) -> str:
    decision = _digest_error(digest)
    if decision is not None:
        raise ImagePolicyError(decision)
    return digest


def _entry_from_row(row: object, index: int) -> ImagePolicyEntry:
    label = f"image policy entry {index}"
    if not isinstance(row, dict):
        raise ImagePolicyError(_decision("policy-invalid-schema", f"{label} must be an object"))
    image = _required_str(row, "image", label)
    digest = row.get("digest")
    if digest is not None:
        if not isinstance(digest, str) or not digest:
            raise ImagePolicyError(_decision("policy-invalid-schema", f"{label} digest must be a non-empty string"))
        validate_image_digest(digest)
    status = _status(_required_str(row, "status", label), label)
    labels = _labels(row.get("labels", {}), f"{label} labels")
    return ImagePolicyEntry(image=image, digest=digest, status=status, labels=labels)


def _entry_status_decision(entry: ImagePolicyEntry) -> ImagePolicyDecision:
    if entry.status == ImagePolicyStatus.ACTIVE:
        return _decision("allowed", "container image allowed by policy", allowed=True, entry=entry)
    return _decision(
        "not-active",
        f"container image policy entry is {entry.status.value}",
        entry=entry,
    )


def _reject_duplicates(entries: tuple[ImagePolicyEntry, ...]) -> None:
    seen: set[tuple[str, str | None]] = set()
    for entry in entries:
        key = (entry.image, entry.digest)
        if key in seen:
            raise ImagePolicyError(_decision("policy-duplicate-entry", "duplicate image policy entry"))
        seen.add(key)


def _digest_error(digest: str) -> ImagePolicyDecision | None:
    prefix = "sha256:"
    hex_part = digest.removeprefix(prefix)
    if not digest.startswith(prefix) or len(hex_part) != 64:
        return _decision("invalid-digest", "container image digest must use sha256:<64 lowercase hex>")
    if any(character not in "0123456789abcdef" for character in hex_part):
        return _decision("invalid-digest", "container image digest must use sha256:<64 lowercase hex>")
    return None


def _required_str(row: dict[str, Any], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ImagePolicyError(_decision("policy-invalid-schema", f"{label} missing string field: {key}"))
    return value


def _status(value: str, label: str) -> ImagePolicyStatus:
    try:
        return ImagePolicyStatus(value)
    except ValueError as exc:
        allowed = ", ".join(status.value for status in ImagePolicyStatus)
        raise ImagePolicyError(_decision("policy-invalid-schema", f"{label} status must be one of: {allowed}")) from exc


def _labels(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise ImagePolicyError(_decision("policy-invalid-schema", f"{label} must be an object"))
    labels: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ImagePolicyError(_decision("policy-invalid-schema", f"{label} keys must be non-empty strings"))
        if not isinstance(item, str):
            raise ImagePolicyError(_decision("policy-invalid-schema", f"{label} values must be strings"))
        labels[key] = item
    return tuple(sorted(labels.items()))


def _decision(
    code: str,
    message: str,
    *,
    allowed: bool = False,
    entry: ImagePolicyEntry | None = None,
) -> ImagePolicyDecision:
    return ImagePolicyDecision(allowed=allowed, code=code, message=message, entry=entry)
