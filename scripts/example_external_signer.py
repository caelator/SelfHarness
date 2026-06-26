#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.corpus_signing import (  # noqa: E402
    public_key_fingerprint,
    public_key_from_private_key_pem,
    public_key_raw_b64,
    sign_bytes,
)
from self_harness.signing import EXTERNAL_SIGNER_PROTOCOL_VERSION  # noqa: E402
from self_harness.types import stable_json_dumps  # noqa: E402

KEY_ENV = "SELF_HARNESS_EXAMPLE_SIGNER_KEY"
PASSPHRASE_ENV = "SELF_HARNESS_EXAMPLE_SIGNER_PASSPHRASE"
PROVIDER_ENV = "SELF_HARNESS_EXAMPLE_SIGNER_PROVIDER"
KEY_ID_ENV = "SELF_HARNESS_EXAMPLE_SIGNER_KEY_ID"


def main() -> int:
    request_id = ""
    try:
        request = _read_request()
        request_id = str(request.get("request_id", ""))
        payload = _payload_from_request(request)
        private_key = _read_private_key()
        passphrase = os.environ.get(PASSPHRASE_ENV)
        public_key = public_key_from_private_key_pem(private_key, passphrase=passphrase)
        response = {
            "schema_version": EXTERNAL_SIGNER_PROTOCOL_VERSION,
            "signature_b64": sign_bytes(payload, private_key, passphrase=passphrase),
            "public_key_b64": public_key_raw_b64(public_key),
            "fingerprint": public_key_fingerprint(public_key),
            "key_id": os.environ.get(KEY_ID_ENV, "example-local-ed25519"),
            "provider": os.environ.get(PROVIDER_ENV, "example-local-pem"),
            "request_id": request_id,
        }
    except Exception as exc:
        _write_error(
            code="example_signer_failed",
            message="example external signer failed",
            request_id=request_id,
            cause=_safe_cause(exc),
        )
        return 2
    print(stable_json_dumps(response))
    return 0


def _read_request() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        raise ValueError("stdin must be a JSON signer request") from exc
    if not isinstance(value, dict):
        raise ValueError("stdin must be a JSON signer request object")
    if value.get("schema_version") != EXTERNAL_SIGNER_PROTOCOL_VERSION:
        raise ValueError("unsupported signer request schema_version")
    if not isinstance(value.get("request_id"), str) or not value["request_id"]:
        raise ValueError("signer request must include request_id")
    if not isinstance(value.get("payload_b64"), str) or not value["payload_b64"]:
        raise ValueError("signer request must include payload_b64")
    return value


def _payload_from_request(request: dict[str, Any]) -> bytes:
    try:
        return base64.b64decode(request["payload_b64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError("payload_b64 must be base64") from exc


def _read_private_key() -> bytes:
    key_path = os.environ.get(KEY_ENV)
    if not key_path:
        raise ValueError(f"{KEY_ENV} is not set")
    try:
        return Path(key_path).read_bytes()
    except OSError as exc:
        raise ValueError("private key file could not be read") from exc


def _write_error(*, code: str, message: str, request_id: str, cause: str) -> None:
    payload = {
        "schema_version": EXTERNAL_SIGNER_PROTOCOL_VERSION,
        "type": "external_signer_error",
        "code": code,
        "message": message,
        "provider": os.environ.get(PROVIDER_ENV, "example-local-pem"),
        "key_id": os.environ.get(KEY_ID_ENV, "example-local-ed25519"),
        "request_id": request_id,
        "cause": cause,
    }
    print(stable_json_dumps(payload), file=sys.stderr)


def _safe_cause(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return exc.__class__.__name__
    return text[:200]


if __name__ == "__main__":
    raise SystemExit(main())
