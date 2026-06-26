#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from self_harness.corpus_signing import (  # noqa: E402
    FINGERPRINT_ALGORITHM,
    public_key_fingerprint,
    public_key_from_private_key_pem,
    public_key_raw_b64,
    sign_bytes,
    verify_bytes_signature,
)
from self_harness.exceptions import CorpusSigningError  # noqa: E402
from self_harness.reproduction_bundle import (  # noqa: E402
    REPRODUCTION_BUNDLE_BOUNDARY,
    REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
    REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
    ReproductionBundleError,
    load_reproduction_bundle,
)
from self_harness.signing import (  # noqa: E402
    DEFAULT_SIGNER_MAX_OUTPUT_BYTES,
    DEFAULT_SIGNER_TIMEOUT_SECONDS,
    ExternalSignerError,
    parse_external_signer_command,
    sign_payload_with_external_signer,
)
from self_harness.types import stable_json_dumps  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    bundle = args.bundle.resolve()
    output = args.out.resolve() if args.out is not None else bundle.with_name(bundle.name + ".sig")
    try:
        load_reproduction_bundle(bundle)
        bundle_bytes = bundle.read_bytes()
        sidecar = _build_sidecar(args, bundle=bundle, bundle_bytes=bundle_bytes)
    except ExternalSignerError as exc:
        print(stable_json_dumps(exc.failure.to_jsonable()), file=sys.stderr)
        return 2
    except (CorpusSigningError, OSError, ReproductionBundleError) as exc:
        payload = {
            "schema_version": "1.0",
            "ok": False,
            "reason": "reproduction-bundle-signing-error",
            "message": str(exc),
            "reproduction_claimed": False,
            "boundary": REPRODUCTION_BUNDLE_BOUNDARY,
        }
        print(stable_json_dumps(payload), file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(stable_json_dumps(sidecar) + "\n", encoding="utf-8")
    print(output)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sign exact reproduction bundle manifest bytes.")
    parser.add_argument("--bundle", type=Path, required=True, help="Reproduction evidence bundle manifest path.")
    key_group = parser.add_mutually_exclusive_group(required=True)
    key_group.add_argument("--private-key", type=Path, help="Local Ed25519 private PEM key.")
    key_group.add_argument("--external-signer", help="Trusted external signer command.")
    parser.add_argument("--public-key", type=Path, help="Expected or recorded Ed25519 public key.")
    parser.add_argument("--fingerprint", help="Expected signer public-key fingerprint.")
    parser.add_argument("--provider", help="Signer provider label for the signature sidecar.")
    parser.add_argument("--key-id", default="", help="Signer key id for the signature sidecar.")
    parser.add_argument("--signer-timeout", type=float, default=DEFAULT_SIGNER_TIMEOUT_SECONDS)
    parser.add_argument("--signer-max-output", type=int, default=DEFAULT_SIGNER_MAX_OUTPUT_BYTES)
    parser.add_argument("--out", type=Path, help="Signature sidecar path; defaults to BUNDLE.sig.")
    _add_passphrase_args(parser)
    return parser


def _build_sidecar(args: argparse.Namespace, *, bundle: Path, bundle_bytes: bytes) -> dict[str, object]:
    if args.external_signer is not None:
        return _external_sidecar(args, bundle=bundle, bundle_bytes=bundle_bytes)
    if args.private_key is None:
        raise CorpusSigningError("--private-key is required unless --external-signer is used")
    return _local_sidecar(args, bundle=bundle, bundle_bytes=bundle_bytes)


def _local_sidecar(args: argparse.Namespace, *, bundle: Path, bundle_bytes: bytes) -> dict[str, object]:
    passphrase = _resolve_passphrase_args(args)
    private_pem = args.private_key.read_bytes()
    public_key: Path | bytes
    public_key = args.public_key if args.public_key is not None else public_key_from_private_key_pem(
        private_pem,
        passphrase=passphrase,
    )
    fingerprint = _expected_fingerprint(public_key, args.fingerprint)
    return _sidecar(
        bundle=bundle,
        bundle_bytes=bundle_bytes,
        signature_b64=sign_bytes(bundle_bytes, private_pem, passphrase=passphrase),
        public_key_b64=public_key_raw_b64(public_key),
        fingerprint=fingerprint,
        key_id=args.key_id,
        provider=args.provider or "local-pem",
    )


def _external_sidecar(args: argparse.Namespace, *, bundle: Path, bundle_bytes: bytes) -> dict[str, object]:
    if args.passphrase is not None or args.passphrase_file is not None or args.passphrase_env is not None:
        raise CorpusSigningError("passphrase arguments are only valid with --private-key")
    expected = (
        _expected_fingerprint(args.public_key, args.fingerprint)
        if args.public_key is not None
        else args.fingerprint
    )
    response = sign_payload_with_external_signer(
        bundle_bytes,
        parse_external_signer_command(args.external_signer),
        provider=args.provider or "external",
        key_id=args.key_id,
        timeout_seconds=args.signer_timeout,
        max_output_bytes=args.signer_max_output,
        expected_fingerprint=expected,
    )
    return _sidecar(
        bundle=bundle,
        bundle_bytes=bundle_bytes,
        signature_b64=response.signature,
        public_key_b64=response.public_key_b64,
        fingerprint=response.fingerprint,
        key_id=response.key_id,
        provider=response.provider,
    )


def _sidecar(
    *,
    bundle: Path,
    bundle_bytes: bytes,
    signature_b64: str,
    public_key_b64: str,
    fingerprint: str,
    key_id: str,
    provider: str,
) -> dict[str, object]:
    if public_key_fingerprint(public_key_b64) != fingerprint:
        raise CorpusSigningError("signer public key fingerprint does not match signature sidecar fingerprint")
    verify_bytes_signature(bundle_bytes, signature_b64, public_key_b64)
    return {
        "schema_version": REPRODUCTION_BUNDLE_SIGNATURE_SCHEMA_VERSION,
        "manifest_filename": bundle.name,
        "manifest_sha256": sha256(bundle_bytes).hexdigest(),
        "signature_algorithm": REPRODUCTION_BUNDLE_SIGNATURE_ALGORITHM,
        "signature_b64": signature_b64,
        "public_key_b64": public_key_b64,
        "fingerprint": fingerprint,
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "key_id": key_id,
        "provider": provider,
    }


def _expected_fingerprint(public_key: Path | str | bytes | None, expected_fingerprint: str | None) -> str:
    expected = _fingerprint_hex(expected_fingerprint) if expected_fingerprint is not None else None
    if public_key is None:
        if expected is None:
            raise CorpusSigningError("--fingerprint is required when --public-key is omitted")
        return expected
    public_key_fingerprint_value = public_key_fingerprint(public_key)
    if expected is not None and expected != public_key_fingerprint_value:
        raise CorpusSigningError("--public-key fingerprint does not match --fingerprint")
    return public_key_fingerprint_value


def _fingerprint_hex(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise CorpusSigningError("--fingerprint must be 64 lowercase hex characters")
    return normalized


def _add_passphrase_args(parser: argparse.ArgumentParser) -> None:
    passphrase_group = parser.add_mutually_exclusive_group()
    passphrase_group.add_argument(
        "--passphrase",
        help="literal private-key passphrase; prefer --passphrase-env or --passphrase-file in CI",
    )
    passphrase_group.add_argument("--passphrase-file", type=Path, help="file containing the private-key passphrase")
    passphrase_group.add_argument("--passphrase-env", help="environment variable containing the private-key passphrase")


def _resolve_passphrase_args(args: argparse.Namespace) -> str | None:
    if args.passphrase is not None:
        return _require_passphrase(args.passphrase, "private key passphrase")
    if args.passphrase_file is not None:
        try:
            return _require_passphrase(
                args.passphrase_file.read_text(encoding="utf-8").rstrip("\r\n"),
                "private key passphrase file",
            )
        except OSError as exc:
            raise CorpusSigningError("private key passphrase file could not be read") from exc
    if args.passphrase_env is not None:
        value = os.environ.get(args.passphrase_env)
        if value is None:
            raise CorpusSigningError(f"private key passphrase environment variable is not set: {args.passphrase_env}")
        return _require_passphrase(value, "private key passphrase environment variable")
    return None


def _require_passphrase(value: str, label: str) -> str:
    if not value:
        raise CorpusSigningError(f"{label} must be non-empty")
    return value


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
