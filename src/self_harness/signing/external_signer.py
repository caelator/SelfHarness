from __future__ import annotations

import base64
import binascii
import json
import shlex
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, NoReturn

from self_harness.corpus import TaskCorpus, corpus_integrity_payload
from self_harness.signing.external_signer_errors import ExternalSignerError, ExternalSignerFailure
from self_harness.types import stable_json_dumps

EXTERNAL_SIGNER_PROTOCOL_VERSION = 1
DEFAULT_SIGNER_TIMEOUT_SECONDS = 15.0
DEFAULT_SIGNER_MAX_OUTPUT_BYTES = 16_384
MIN_SIGNER_TIMEOUT_SECONDS = 1.0
MAX_SIGNER_TIMEOUT_SECONDS = 60.0
MIN_SIGNER_MAX_OUTPUT_BYTES = 256
MAX_SIGNER_MAX_OUTPUT_BYTES = 1_048_576


@dataclass(frozen=True)
class ExternalSignerResponse:
    signature: str
    public_key_b64: str
    fingerprint: str
    key_id: str
    provider: str
    request_id: str


def parse_external_signer_command(value: str) -> tuple[str, ...]:
    command = tuple(shlex.split(value))
    if not command:
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_command_empty",
                message="external signer command must be non-empty",
            )
        )
    return command


def sign_corpus_with_external_signer(
    corpus: TaskCorpus,
    command: tuple[str, ...],
    *,
    provider: str = "external",
    key_id: str = "",
    timeout_seconds: float = DEFAULT_SIGNER_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_SIGNER_MAX_OUTPUT_BYTES,
    expected_fingerprint: str | None = None,
) -> ExternalSignerResponse:
    return sign_payload_with_external_signer(
        _corpus_integrity_bytes(corpus),
        command,
        provider=provider,
        key_id=key_id,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        expected_fingerprint=expected_fingerprint,
    )


def sign_payload_with_external_signer(
    payload: bytes,
    command: tuple[str, ...],
    *,
    provider: str = "external",
    key_id: str = "",
    timeout_seconds: float = DEFAULT_SIGNER_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_SIGNER_MAX_OUTPUT_BYTES,
    expected_fingerprint: str | None = None,
) -> ExternalSignerResponse:
    timeout_seconds = _clamp_timeout(timeout_seconds)
    max_output_bytes = _clamp_max_output(max_output_bytes)
    request_id = _request_id(payload)
    request = {
        "schema_version": EXTERNAL_SIGNER_PROTOCOL_VERSION,
        "request_id": request_id,
        "deadline_ms": int(timeout_seconds * 1000),
        "payload_b64": base64.b64encode(payload).decode("ascii"),
    }
    try:
        completed = subprocess.run(
            command,
            input=(stable_json_dumps(request) + "\n").encode("utf-8"),
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_timeout",
                message="external signer timed out",
                provider=provider,
                key_id=key_id,
                request_id=request_id,
                timeout_ms=int(timeout_seconds * 1000),
                cause=_short_text(exc.stderr),
            )
        ) from exc
    except OSError as exc:
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_launch_failed",
                message="external signer could not be started",
                provider=provider,
                key_id=key_id,
                request_id=request_id,
                cause=_short_text(str(exc)),
            )
        ) from exc

    if completed.returncode != 0:
        raise ExternalSignerError(
            _nonzero_failure(
                stderr=completed.stderr,
                provider=provider,
                key_id=key_id,
                request_id=request_id,
                exit_status=completed.returncode,
            )
        )
    if len(completed.stdout) > max_output_bytes:
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_oversize",
                message="external signer stdout exceeded the configured byte limit",
                provider=provider,
                key_id=key_id,
                request_id=request_id,
                cause=f"stdout_bytes={len(completed.stdout)} max_output_bytes={max_output_bytes}",
            )
        )
    data = _json_object(completed.stdout, provider=provider, key_id=key_id, request_id=request_id)
    response = _response_from_json(data, provider=provider, key_id=key_id, request_id=request_id)
    if expected_fingerprint is not None and response.fingerprint != _fingerprint_hex(expected_fingerprint):
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_payload_mismatch",
                message="external signer returned an unexpected public key fingerprint",
                provider=response.provider,
                key_id=response.key_id,
                request_id=request_id,
            )
        )
    return response


def _response_from_json(
    data: dict[str, Any],
    *,
    provider: str,
    key_id: str,
    request_id: str,
) -> ExternalSignerResponse:
    schema_version = data.get("schema_version")
    if schema_version != EXTERNAL_SIGNER_PROTOCOL_VERSION:
        _raise_missing("schema_version", provider=provider, key_id=key_id, request_id=request_id)
    response_request_id = data.get("request_id")
    if response_request_id is not None and response_request_id != request_id:
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_payload_mismatch",
                message="external signer response request_id did not match the request",
                provider=provider,
                key_id=key_id,
                request_id=request_id,
            )
        )
    signature = _required_base64(data, "signature_b64", provider=provider, key_id=key_id, request_id=request_id)
    public_key = _required_base64(data, "public_key_b64", provider=provider, key_id=key_id, request_id=request_id)
    fingerprint = _fingerprint_hex(
        _required_str(data, "fingerprint", provider=provider, key_id=key_id, request_id=request_id)
    )
    response_key_id = _required_str(data, "key_id", provider=provider, key_id=key_id, request_id=request_id)
    response_provider = _required_str(
        data,
        "provider",
        provider=provider,
        key_id=response_key_id,
        request_id=request_id,
    )
    return ExternalSignerResponse(
        signature=signature,
        public_key_b64=public_key,
        fingerprint=fingerprint,
        key_id=response_key_id,
        provider=response_provider,
        request_id=request_id,
    )


def _json_object(
    value: bytes,
    *,
    provider: str,
    key_id: str,
    request_id: str,
) -> dict[str, Any]:
    try:
        data = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_malformed_json",
                message="external signer stdout must be a JSON object",
                provider=provider,
                key_id=key_id,
                request_id=request_id,
            )
        ) from exc
    if not isinstance(data, dict):
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_malformed_json",
                message="external signer stdout must be a JSON object",
                provider=provider,
                key_id=key_id,
                request_id=request_id,
            )
        )
    return data


def _nonzero_failure(
    *,
    stderr: bytes,
    provider: str,
    key_id: str,
    request_id: str,
    exit_status: int,
) -> ExternalSignerFailure:
    try:
        data = json.loads(stderr.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict) and data.get("type") == "external_signer_error":
        code = data.get("code")
        message = data.get("message")
        provider_text = _optional_str(data.get("provider"), provider) or provider
        key_id_text = _optional_str(data.get("key_id"), key_id) or key_id
        request_id_text = _optional_str(data.get("request_id"), request_id) or request_id
        return ExternalSignerFailure(
            code=code if isinstance(code, str) and code else "signer_nonzero_exit",
            message=message if isinstance(message, str) and message else "external signer exited non-zero",
            provider=provider_text,
            key_id=key_id_text,
            request_id=request_id_text,
            exit_status=exit_status,
            cause=_optional_str(data.get("cause"), None),
        )
    return ExternalSignerFailure(
        code="signer_nonzero_exit",
        message="external signer exited non-zero",
        provider=provider,
        key_id=key_id,
        request_id=request_id,
        exit_status=exit_status,
        cause=_short_text(stderr),
    )


def _required_str(data: dict[str, Any], key: str, *, provider: str, key_id: str, request_id: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        _raise_missing(key, provider=provider, key_id=key_id, request_id=request_id)
    return value


def _required_base64(data: dict[str, Any], key: str, *, provider: str, key_id: str, request_id: str) -> str:
    value = _required_str(data, key, provider=provider, key_id=key_id, request_id=request_id)
    try:
        base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_malformed_json",
                message=f"external signer field {key} must be base64",
                provider=provider,
                key_id=key_id,
                request_id=request_id,
            )
        ) from exc
    return value


def _raise_missing(key: str, *, provider: str, key_id: str, request_id: str) -> NoReturn:
    raise ExternalSignerError(
        ExternalSignerFailure(
            code="signer_missing_field",
            message=f"external signer response missing valid field: {key}",
            provider=provider,
            key_id=key_id,
            request_id=request_id,
        )
    )


def _fingerprint_hex(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ExternalSignerError(
            ExternalSignerFailure(
                code="signer_malformed_json",
                message="external signer fingerprint must be 64 lowercase hex characters",
            )
        )
    return normalized


def _optional_str(value: object, default: str | None) -> str | None:
    if isinstance(value, str) and value:
        return value
    return default


def _clamp_timeout(value: float) -> float:
    if value < MIN_SIGNER_TIMEOUT_SECONDS:
        return MIN_SIGNER_TIMEOUT_SECONDS
    if value > MAX_SIGNER_TIMEOUT_SECONDS:
        return MAX_SIGNER_TIMEOUT_SECONDS
    return value


def _clamp_max_output(value: int) -> int:
    if value < MIN_SIGNER_MAX_OUTPUT_BYTES:
        return MIN_SIGNER_MAX_OUTPUT_BYTES
    if value > MAX_SIGNER_MAX_OUTPUT_BYTES:
        return MAX_SIGNER_MAX_OUTPUT_BYTES
    return value


def _corpus_integrity_bytes(corpus: TaskCorpus) -> bytes:
    return stable_json_dumps(corpus_integrity_payload(corpus)).encode("utf-8")


def _request_id(payload: bytes) -> str:
    return f"self-harness-{sha256(payload).hexdigest()[:16]}"


def _short_text(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:256] if text else None
