from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.corpus_signing import (  # noqa: E402
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    public_key_raw_b64,
    verify_bytes_signature,
)
from self_harness.exceptions import CorpusSigningError  # noqa: E402

SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "ed25519"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = args.manifest.resolve()
    signature = args.signature.resolve()
    public_key = _public_key_arg(args.public_key)
    try:
        verify_signature(manifest_path=manifest, signature_path=signature, public_key=public_key)
    except (CorpusSigningError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"provenance signature verified: {signature}")
    return 0


def verify_signature(
    *,
    manifest_path: Path,
    signature_path: Path,
    public_key: Path | str | bytes | None = None,
) -> None:
    try:
        manifest_bytes = manifest_path.read_bytes()
    except FileNotFoundError as exc:
        raise CorpusSigningError(f"missing provenance manifest: {manifest_path}") from exc
    sidecar = _load_sidecar(signature_path)
    _verify_sidecar_schema(sidecar)
    if sidecar["manifest_filename"] != manifest_path.name:
        raise CorpusSigningError("signature sidecar manifest_filename does not match manifest path")
    actual_manifest_hash = sha256(manifest_bytes).hexdigest()
    if sidecar["manifest_sha256"] != actual_manifest_hash:
        raise CorpusSigningError(
            "signature sidecar manifest_sha256 mismatch: "
            f"expected {sidecar['manifest_sha256']}, got {actual_manifest_hash}"
        )

    embedded_public_key = str(sidecar["public_key_b64"])
    embedded_fingerprint = public_key_fingerprint(embedded_public_key)
    if sidecar["fingerprint"] != embedded_fingerprint:
        raise CorpusSigningError("signature sidecar fingerprint does not match embedded public key")

    verification_key = embedded_public_key if public_key is None else public_key
    verification_fingerprint = public_key_fingerprint(verification_key)
    if sidecar["fingerprint"] != verification_fingerprint:
        raise CorpusSigningError("signature sidecar fingerprint does not match trusted public key")
    if public_key is not None and sidecar["public_key_b64"] != public_key_raw_b64(public_key):
        raise CorpusSigningError("signature sidecar embedded public key does not match trusted public key")

    verify_bytes_signature(manifest_bytes, str(sidecar["signature_b64"]), verification_key)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a Self-Harness release provenance signature sidecar.")
    parser.add_argument("--manifest", type=Path, required=True, help="Release provenance manifest path.")
    parser.add_argument("--signature", type=Path, required=True, help="Signature sidecar path.")
    parser.add_argument(
        "--public-key",
        help="Trusted public key path; when omitted, verifies sidecar self-consistency only.",
    )
    return parser


def _public_key_arg(value: str | None) -> Path | str | None:
    if value is None:
        return None
    try:
        path = Path(value)
        if path.exists():
            return path.resolve()
    except OSError:
        pass
    return value


def _load_sidecar(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusSigningError(f"missing provenance signature sidecar: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusSigningError(f"invalid provenance signature JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CorpusSigningError("provenance signature sidecar must be a JSON object")
    return value


def _verify_sidecar_schema(sidecar: dict[str, Any]) -> None:
    if sidecar.get("schema_version") != SCHEMA_VERSION:
        raise CorpusSigningError(f"unsupported provenance signature schema_version: {sidecar.get('schema_version')}")
    _require_str(sidecar, "manifest_filename")
    if Path(sidecar["manifest_filename"]).name != sidecar["manifest_filename"]:
        raise CorpusSigningError("signature sidecar manifest_filename must be a basename")
    _require_hash(sidecar, "manifest_sha256")
    if sidecar.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        raise CorpusSigningError(f"unsupported signature_algorithm: {sidecar.get('signature_algorithm')}")
    _require_base64(sidecar, "signature_b64")
    public_key_b64 = _require_base64(sidecar, "public_key_b64")
    try:
        raw_public_key = base64.b64decode(public_key_b64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise CorpusSigningError("signature sidecar public_key_b64 must be base64") from exc
    if len(raw_public_key) != 32:
        raise CorpusSigningError("signature sidecar public_key_b64 must encode a raw Ed25519 public key")
    _require_hash(sidecar, "fingerprint")
    if sidecar.get("fingerprint_algorithm") not in {None, FINGERPRINT_ALGORITHM}:
        raise CorpusSigningError(f"unsupported fingerprint_algorithm: {sidecar.get('fingerprint_algorithm')}")
    _require_str(sidecar, "provider")
    key_id = sidecar.get("key_id")
    if not isinstance(key_id, str):
        raise CorpusSigningError("signature sidecar missing valid field: key_id")


def _require_str(sidecar: dict[str, Any], key: str) -> str:
    value = sidecar.get(key)
    if not isinstance(value, str) or not value:
        _raise_missing(key)
    return value


def _require_base64(sidecar: dict[str, Any], key: str) -> str:
    value = _require_str(sidecar, key)
    try:
        base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise CorpusSigningError(f"signature sidecar field {key} must be base64") from exc
    return value


def _require_hash(sidecar: dict[str, Any], key: str) -> str:
    value = _require_str(sidecar, key)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise CorpusSigningError(f"signature sidecar field {key} must be 64 lowercase hex characters")
    return value


def _raise_missing(key: str) -> NoReturn:
    raise CorpusSigningError(f"signature sidecar missing valid field: {key}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
