#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PROTOCOL_VERSION = 1
SEED = bytes(range(32))


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    if mode == "sleep":
        time.sleep(2)
    if mode == "oversize":
        sys.stdout.write("x" * 20_000)
        return 0
    if mode == "malformed":
        sys.stdout.write("{")
        return 0
    if mode == "nonzero":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "type": "external_signer_error",
                    "code": "fixture_nonzero",
                    "message": "fixture signer failed",
                    "provider": "fixture",
                    "key_id": "fixture-key-1",
                    "request_id": "",
                    "cause": "requested nonzero mode",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 7

    request = json.loads(sys.stdin.read())
    key = Ed25519PrivateKey.from_private_bytes(SEED)
    public_key = key.public_key()
    raw_public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    spki_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    response = {
        "schema_version": PROTOCOL_VERSION,
        "signature_b64": base64.b64encode(key.sign(base64.b64decode(request["payload_b64"]))).decode("ascii"),
        "public_key_b64": base64.b64encode(raw_public).decode("ascii"),
        "fingerprint": hashlib.sha256(spki_der).hexdigest(),
        "key_id": "fixture-key-1",
        "provider": "fixture",
        "request_id": request["request_id"],
    }
    if mode == "missing":
        del response["signature_b64"]
    if mode == "mismatch":
        response["request_id"] = "wrong-request"
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
