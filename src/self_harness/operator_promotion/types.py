from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PROMOTION_MANIFEST_SCHEMA_VERSION = "1.0"
PROMOTION_SIGNATURE_SCHEMA_VERSION = 1
PROMOTION_VERIFICATION_SCHEMA_VERSION = "1.0"
PROMOTION_SIGNATURE_ALGORITHM = "ed25519"
PROMOTION_BOUNDARY = (
    "operator policy promotion material only; versions and attests operator-owned release policy files, "
    "does not run Harbor, Docker, registries, scanners, PyPI, Sigstore, models, or cloud providers, "
    "and is not benchmark reproduction evidence"
)

PolicyKind = Literal[
    "image_policy",
    "freshness_policy",
    "scanner_db_freshness_policy",
    "vulnerability_policy",
    "trusted_public_keys",
]
PromotionStatus = Literal["draft", "candidate", "active", "retired"]

POLICY_KINDS: frozenset[str] = frozenset(
    {
        "image_policy",
        "freshness_policy",
        "scanner_db_freshness_policy",
        "vulnerability_policy",
        "trusted_public_keys",
    }
)
PROMOTION_STATUSES: frozenset[str] = frozenset({"draft", "candidate", "active", "retired"})
PROMOTION_STATUS_ORDER: dict[str, int] = {"draft": 0, "candidate": 1, "active": 2, "retired": 3}


@dataclass(frozen=True)
class PromotionEntry:
    name: str
    kind: PolicyKind
    path: str
    sha256: str
    byte_size: int
    status: PromotionStatus


@dataclass(frozen=True)
class PromotionManifest:
    schema_version: str
    entries: tuple[PromotionEntry, ...]
    boundary: str


@dataclass(frozen=True)
class PromotionSignature:
    schema_version: int
    manifest_sha256: str
    signature_algorithm: str
    signature_b64: str
    public_key_b64: str
    fingerprint: str
    fingerprint_algorithm: str
    provider: str
    key_id: str
    mode: str
    manifest_filename: str
    request_id: str | None = None


@dataclass(frozen=True)
class PromotionCheck:
    name: str
    status: str
    detail: str
    path: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class PromotionVerificationReport:
    schema_version: str
    manifest_path: str
    manifest_sha256: str | None
    signature_path: str | None
    trusted_public_key: str | None
    ok: bool
    checks: tuple[PromotionCheck, ...]
    report_hash: str
    boundary: str


class PromotionError(RuntimeError):
    """Raised when operator promotion material is malformed or unsafe to promote."""
