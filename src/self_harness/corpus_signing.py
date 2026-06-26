from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Any, cast

from self_harness.corpus import TaskCorpus, corpus_integrity_payload
from self_harness.exceptions import CorpusSigningError
from self_harness.types import stable_json_dumps

FINGERPRINT_ALGORITHM = "sha256-spki-der-hex"
PRIVATE_KEY_ENCRYPTION_PROFILE = "pkcs8-best-available"
PASSPHRASE_ERROR = "private key passphrase is required or incorrect"


def generate_keypair(passphrase: str | None = None) -> tuple[bytes, bytes]:
    """Return Ed25519 private/public PEM bytes for offline corpus signing."""

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise CorpusSigningError(
            "cryptography is required for corpus signing; install self-harness[provenance]"
        ) from exc

    private_key = Ed25519PrivateKey.generate()
    if passphrase is None:
        encryption_algorithm: serialization.KeySerializationEncryption = serialization.NoEncryption()
    else:
        encryption_algorithm = serialization.BestAvailableEncryption(_passphrase_bytes(passphrase))
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption_algorithm,
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def sign_corpus(corpus: TaskCorpus, private_key_pem: bytes, *, passphrase: str | None = None) -> str:
    """Return a base64 Ed25519 signature over a corpus integrity payload."""

    return sign_bytes(_corpus_integrity_bytes(corpus), private_key_pem, passphrase=passphrase)


def sign_bytes(payload: bytes, private_key_pem: bytes, *, passphrase: str | None = None) -> str:
    """Return a base64 Ed25519 signature over exact payload bytes."""

    loaded_key = _load_ed25519_private_key(private_key_pem, passphrase=passphrase)
    signature = loaded_key.sign(payload)
    return base64.b64encode(signature).decode("ascii")


def verify_bytes_signature(payload: bytes, signature_b64: str, public_key: Path | str | bytes) -> None:
    """Verify a base64 Ed25519 signature over exact payload bytes."""

    try:
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise CorpusSigningError(
            "cryptography is required for signature verification; install self-harness[provenance]"
        ) from exc

    try:
        signature = base64.b64decode(signature_b64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise CorpusSigningError("signature must be base64") from exc
    try:
        _load_ed25519_public_key(public_key).verify(signature, payload)
    except InvalidSignature as exc:
        raise CorpusSigningError("signature verification failed") from exc


def public_key_from_private_key_pem(private_key_pem: bytes, *, passphrase: str | None = None) -> bytes:
    """Return the Ed25519 public PEM bytes derived from a private PEM key."""

    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise CorpusSigningError(
            "cryptography is required for public-key derivation; install self-harness[provenance]"
        ) from exc

    return cast(
        bytes,
        _load_ed25519_private_key(private_key_pem, passphrase=passphrase).public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def public_key_fingerprint(public_key: Path | str | bytes) -> str:
    """Return the stable SHA-256 SPKI DER fingerprint for an Ed25519 public key."""

    spki_der = _public_key_spki_der(public_key)
    return hashlib.sha256(spki_der).hexdigest()


def public_key_raw_b64(public_key: Path | str | bytes) -> str:
    """Return base64-encoded raw Ed25519 public key bytes."""

    return base64.b64encode(_public_key_raw_bytes(public_key)).decode("ascii")


def _corpus_integrity_bytes(corpus: TaskCorpus) -> bytes:
    return stable_json_dumps(corpus_integrity_payload(corpus)).encode("utf-8")


def _passphrase_bytes(passphrase: str) -> bytes:
    if not passphrase:
        raise CorpusSigningError("private key passphrase must be non-empty")
    return passphrase.encode("utf-8")


def _load_ed25519_private_key(private_key_pem: bytes, *, passphrase: str | None = None) -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise CorpusSigningError(
            "cryptography is required for corpus signing; install self-harness[provenance]"
        ) from exc

    password = _passphrase_bytes(passphrase) if passphrase is not None else None
    try:
        loaded_key = serialization.load_pem_private_key(private_key_pem, password=password)
    except TypeError as exc:
        if password is not None:
            try:
                loaded_key = serialization.load_pem_private_key(private_key_pem, password=None)
            except (TypeError, ValueError) as retry_exc:
                raise CorpusSigningError(PASSPHRASE_ERROR) from retry_exc
        else:
            raise CorpusSigningError(PASSPHRASE_ERROR) from exc
    except ValueError as exc:
        raise CorpusSigningError(PASSPHRASE_ERROR) from exc
    if not isinstance(loaded_key, Ed25519PrivateKey):
        raise CorpusSigningError("private key is not an Ed25519 private key")
    return loaded_key


def _load_ed25519_public_key(public_key: Path | str | bytes) -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise CorpusSigningError(
            "cryptography is required for corpus fingerprinting; install self-harness[provenance]"
        ) from exc

    public_key_bytes = _public_key_bytes(public_key)
    try:
        loaded_key = serialization.load_pem_public_key(public_key_bytes)
    except ValueError:
        try:
            loaded_key = Ed25519PublicKey.from_public_bytes(_decode_public_key_bytes(public_key_bytes))
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise CorpusSigningError("public key must be Ed25519 PEM, raw bytes, or base64 raw bytes") from exc
    if not isinstance(loaded_key, Ed25519PublicKey):
        raise CorpusSigningError("public key is not an Ed25519 public key")
    return loaded_key


def _public_key_spki_der(public_key: Path | str | bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise CorpusSigningError(
            "cryptography is required for corpus fingerprinting; install self-harness[provenance]"
        ) from exc

    return cast(
        bytes,
        _load_ed25519_public_key(public_key).public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def _public_key_raw_bytes(public_key: Path | str | bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise CorpusSigningError(
            "cryptography is required for public-key encoding; install self-harness[provenance]"
        ) from exc

    return cast(
        bytes,
        _load_ed25519_public_key(public_key).public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )


def _public_key_bytes(public_key: Path | str | bytes) -> bytes:
    if isinstance(public_key, bytes):
        return public_key
    if isinstance(public_key, Path):
        return public_key.read_bytes()
    try:
        path = Path(public_key)
        if path.exists():
            return path.read_bytes()
    except OSError:
        pass
    return public_key.encode("utf-8")


def _decode_public_key_bytes(public_key_bytes: bytes) -> bytes:
    if len(public_key_bytes) == 32:
        return public_key_bytes
    text = public_key_bytes.decode("ascii").strip()
    return base64.b64decode(text.encode("ascii"), validate=True)
