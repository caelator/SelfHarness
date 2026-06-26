from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from self_harness.corpus_signing import (
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    public_key_from_private_key_pem,
    public_key_raw_b64,
    sign_bytes,
    verify_bytes_signature,
)
from self_harness.exceptions import CorpusSigningError
from self_harness.operator_promotion.manifest import canonical_manifest_bytes
from self_harness.operator_promotion.types import (
    PROMOTION_SIGNATURE_ALGORITHM,
    PROMOTION_SIGNATURE_SCHEMA_VERSION,
    PromotionError,
    PromotionSignature,
)
from self_harness.signing import (
    DEFAULT_SIGNER_MAX_OUTPUT_BYTES,
    DEFAULT_SIGNER_TIMEOUT_SECONDS,
    ExternalSignerError,
    parse_external_signer_command,
    sign_payload_with_external_signer,
)
from self_harness.types import stable_json_dumps

SIGNATURE_FIELDS = frozenset(
    {
        "schema_version",
        "manifest_sha256",
        "signature_algorithm",
        "signature_b64",
        "public_key_b64",
        "fingerprint",
        "fingerprint_algorithm",
        "provider",
        "key_id",
        "mode",
        "manifest_filename",
        "request_id",
    }
)


def sign_promotion_manifest(
    manifest_path: Path,
    *,
    out_path: Path,
    private_key: Path | None = None,
    passphrase: str | None = None,
    external_signer: str | None = None,
    provider: str = "local-pem",
    key_id: str = "",
    signer_timeout_seconds: float = DEFAULT_SIGNER_TIMEOUT_SECONDS,
    signer_max_output_bytes: int = DEFAULT_SIGNER_MAX_OUTPUT_BYTES,
    expected_public_key: Path | None = None,
    expected_fingerprint: str | None = None,
) -> PromotionSignature:
    manifest_bytes = canonical_manifest_bytes(manifest_path)
    manifest_digest = sha256(manifest_bytes).hexdigest()
    expected = _expected_fingerprint(expected_public_key, expected_fingerprint)
    if external_signer is not None:
        if private_key is not None:
            raise PromotionError("--private-key cannot be combined with --external-signer")
        try:
            response = sign_payload_with_external_signer(
                manifest_bytes,
                parse_external_signer_command(external_signer),
                provider=provider,
                key_id=key_id,
                timeout_seconds=signer_timeout_seconds,
                max_output_bytes=signer_max_output_bytes,
                expected_fingerprint=expected,
            )
        except ExternalSignerError:
            raise
        except Exception as exc:
            raise PromotionError(str(exc)) from exc
        signature = PromotionSignature(
            schema_version=PROMOTION_SIGNATURE_SCHEMA_VERSION,
            manifest_sha256=manifest_digest,
            signature_algorithm=PROMOTION_SIGNATURE_ALGORITHM,
            signature_b64=response.signature,
            public_key_b64=response.public_key_b64,
            fingerprint=response.fingerprint,
            fingerprint_algorithm=FINGERPRINT_ALGORITHM,
            provider=response.provider,
            key_id=response.key_id,
            mode="external-signer",
            manifest_filename=manifest_path.name,
            request_id=response.request_id,
        )
    else:
        if private_key is None:
            raise PromotionError("--private-key is required unless --external-signer is used")
        try:
            private_pem = private_key.read_bytes()
            public_key = public_key_from_private_key_pem(private_pem, passphrase=passphrase)
            fingerprint = public_key_fingerprint(public_key)
            if expected is not None and fingerprint != expected:
                raise PromotionError("--private-key public fingerprint does not match expected fingerprint")
            signature = PromotionSignature(
                schema_version=PROMOTION_SIGNATURE_SCHEMA_VERSION,
                manifest_sha256=manifest_digest,
                signature_algorithm=PROMOTION_SIGNATURE_ALGORITHM,
                signature_b64=sign_bytes(manifest_bytes, private_pem, passphrase=passphrase),
                public_key_b64=public_key_raw_b64(public_key),
                fingerprint=fingerprint,
                fingerprint_algorithm=FINGERPRINT_ALGORITHM,
                provider=provider,
                key_id=key_id,
                mode="local-private-key",
                manifest_filename=manifest_path.name,
            )
        except OSError as exc:
            raise PromotionError(f"promotion signing key could not be read: {private_key}") from exc
        except CorpusSigningError as exc:
            raise PromotionError(str(exc)) from exc
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(stable_json_dumps(promotion_signature_to_jsonable(signature)) + "\n", encoding="utf-8")
    return signature


def load_promotion_signature(path: Path) -> PromotionSignature:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PromotionError(f"missing promotion signature sidecar: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PromotionError(f"invalid promotion signature JSON: {path}") from exc
    if not isinstance(data, dict):
        raise PromotionError("promotion signature JSON must be an object")
    payload = cast(dict[str, Any], data)
    unknown = sorted(set(payload) - SIGNATURE_FIELDS)
    if unknown:
        raise PromotionError(f"promotion signature has unknown fields: {', '.join(unknown)}")
    if payload.get("schema_version") != PROMOTION_SIGNATURE_SCHEMA_VERSION:
        raise PromotionError("unsupported promotion signature schema_version")
    algorithm = _required_str(payload, "signature_algorithm")
    if algorithm != PROMOTION_SIGNATURE_ALGORITHM:
        raise PromotionError(f"unsupported promotion signature algorithm: {algorithm}")
    fingerprint_algorithm = _required_str(payload, "fingerprint_algorithm")
    if fingerprint_algorithm != FINGERPRINT_ALGORITHM:
        raise PromotionError(f"unsupported promotion signature fingerprint algorithm: {fingerprint_algorithm}")
    return PromotionSignature(
        schema_version=PROMOTION_SIGNATURE_SCHEMA_VERSION,
        manifest_sha256=_sha256_field(payload, "manifest_sha256"),
        signature_algorithm=algorithm,
        signature_b64=_required_str(payload, "signature_b64"),
        public_key_b64=_required_str(payload, "public_key_b64"),
        fingerprint=_sha256_field(payload, "fingerprint"),
        fingerprint_algorithm=fingerprint_algorithm,
        provider=_required_str(payload, "provider"),
        key_id=_required_str(payload, "key_id", allow_empty=True),
        mode=_required_str(payload, "mode"),
        manifest_filename=_required_str(payload, "manifest_filename"),
        request_id=_optional_str(payload, "request_id"),
    )


def verify_promotion_signature(
    manifest_path: Path,
    signature_path: Path,
    *,
    trusted_public_key: Path | None = None,
) -> PromotionSignature:
    manifest_bytes = canonical_manifest_bytes(manifest_path)
    signature = load_promotion_signature(signature_path)
    manifest_digest = sha256(manifest_bytes).hexdigest()
    if signature.manifest_sha256 != manifest_digest:
        raise PromotionError("promotion signature manifest_sha256 does not match manifest")
    if signature.manifest_filename != manifest_path.name:
        raise PromotionError("promotion signature manifest_filename does not match manifest")
    try:
        embedded_fingerprint = public_key_fingerprint(signature.public_key_b64)
        if embedded_fingerprint != signature.fingerprint:
            raise PromotionError("promotion signature embedded public key does not match fingerprint")
        verification_key: Path | str = signature.public_key_b64
        if trusted_public_key is not None:
            trusted_fingerprint = public_key_fingerprint(trusted_public_key)
            if trusted_fingerprint != signature.fingerprint:
                raise PromotionError("trusted public key does not match promotion signature fingerprint")
            verification_key = trusted_public_key
        verify_bytes_signature(manifest_bytes, signature.signature_b64, verification_key)
    except CorpusSigningError as exc:
        raise PromotionError(str(exc)) from exc
    return signature


def promotion_signature_to_jsonable(signature: PromotionSignature) -> dict[str, object]:
    return {
        "schema_version": signature.schema_version,
        "manifest_sha256": signature.manifest_sha256,
        "signature_algorithm": signature.signature_algorithm,
        "signature_b64": signature.signature_b64,
        "public_key_b64": signature.public_key_b64,
        "fingerprint": signature.fingerprint,
        "fingerprint_algorithm": signature.fingerprint_algorithm,
        "provider": signature.provider,
        "key_id": signature.key_id,
        "mode": signature.mode,
        "manifest_filename": signature.manifest_filename,
        "request_id": signature.request_id,
    }


def _expected_fingerprint(expected_public_key: Path | None, expected_fingerprint: str | None) -> str | None:
    expected = expected_fingerprint.lower() if expected_fingerprint is not None else None
    if expected is not None and (
        len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise PromotionError("--fingerprint must be 64 lowercase hex characters")
    if expected_public_key is None:
        return expected
    public_key_fingerprint_value = public_key_fingerprint(expected_public_key)
    if expected is not None and expected != public_key_fingerprint_value:
        raise PromotionError("--public-key fingerprint does not match --fingerprint")
    return public_key_fingerprint_value


def _required_str(data: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise PromotionError(f"promotion signature missing non-empty string field: {key}")
    return value


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise PromotionError(f"promotion signature {key} must be null or a non-empty string")
    return value


def _sha256_field(data: dict[str, Any], key: str) -> str:
    value = _required_str(data, key)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise PromotionError(f"promotion signature {key} must be a lowercase sha256 digest")
    return value
