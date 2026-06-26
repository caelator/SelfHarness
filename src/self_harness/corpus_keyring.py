from __future__ import annotations

import base64
import binascii
import json
import string
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from self_harness.corpus import TaskCorpus, TaskLoadReason, verify_corpus_signature
from self_harness.corpus_signing import FINGERPRINT_ALGORITHM, public_key_fingerprint
from self_harness.exceptions import CorpusSigningError, KeyringError, TaskLoadError
from self_harness.types import stable_json_dumps

KEYRING_VERSION = "1"


class KeyringStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"
    REVOKED = "revoked"


@dataclass(frozen=True)
class KeyringEntry:
    corpus_id: str
    fingerprint: str
    fingerprint_algorithm: str
    public_key_pem: str
    status: KeyringStatus
    labels: dict[str, str]


@dataclass(frozen=True)
class CorpusKeyring:
    keyring_version: str
    entries: tuple[KeyringEntry, ...]


def empty_keyring() -> CorpusKeyring:
    return CorpusKeyring(keyring_version=KEYRING_VERSION, entries=())


def load_keyring(path: Path) -> CorpusKeyring:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KeyringError(f"missing corpus keyring: {path}") from exc
    except json.JSONDecodeError as exc:
        raise KeyringError(f"invalid corpus keyring JSON: {path}") from exc
    if not isinstance(data, dict):
        raise KeyringError("corpus keyring JSON must be an object")
    version = _required_str(data, "keyring_version", "corpus keyring")
    if version != KEYRING_VERSION:
        raise KeyringError(f"unsupported keyring_version: {version}")
    rows = data.get("entries")
    if not isinstance(rows, list):
        raise KeyringError("corpus keyring must include an entries list")
    return CorpusKeyring(
        keyring_version=version,
        entries=tuple(_entry_from_row(row, index) for index, row in enumerate(rows)),
    )


def save_keyring(keyring: CorpusKeyring, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(keyring_to_jsonable(keyring)) + "\n", encoding="utf-8")


def keyring_to_jsonable(keyring: CorpusKeyring) -> dict[str, object]:
    return {
        "keyring_version": keyring.keyring_version,
        "entries": [
            {
                "corpus_id": entry.corpus_id,
                "fingerprint": entry.fingerprint,
                "fingerprint_algorithm": entry.fingerprint_algorithm,
                "labels": dict(entry.labels),
                "public_key_pem": entry.public_key_pem,
                "status": entry.status.value,
            }
            for entry in keyring.entries
        ],
    }


def add_keyring_entry(
    keyring: CorpusKeyring,
    *,
    corpus_id: str,
    public_key: Path | str | bytes,
    status: KeyringStatus | str = KeyringStatus.ACTIVE,
    labels: Mapping[str, str] | None = None,
) -> CorpusKeyring:
    if not corpus_id:
        raise KeyringError("corpus_id must be a non-empty string")
    normalized_status = _status(status)
    normalized_labels = _labels(labels or {}, "labels")
    public_key_pem = normalize_public_key_pem(public_key)
    fingerprint = _fingerprint(public_key_pem)
    if any(entry.corpus_id == corpus_id and entry.fingerprint == fingerprint for entry in keyring.entries):
        raise KeyringError(f"duplicate keyring entry for corpus {corpus_id} and fingerprint {fingerprint}")
    entry = KeyringEntry(
        corpus_id=corpus_id,
        fingerprint=fingerprint,
        fingerprint_algorithm=FINGERPRINT_ALGORITHM,
        public_key_pem=public_key_pem,
        status=normalized_status,
        labels=normalized_labels,
    )
    return CorpusKeyring(keyring_version=keyring.keyring_version, entries=(*keyring.entries, entry))


def set_keyring_entry_status(
    keyring: CorpusKeyring,
    *,
    corpus_id: str,
    fingerprint: str,
    status: KeyringStatus | str,
) -> CorpusKeyring:
    normalized_status = _status(status)
    normalized_fingerprint = _fingerprint_hex(fingerprint, "fingerprint")
    entries: list[KeyringEntry] = []
    matched = False
    for entry in keyring.entries:
        if entry.corpus_id == corpus_id and entry.fingerprint == normalized_fingerprint:
            matched = True
            entries.append(
                KeyringEntry(
                    corpus_id=entry.corpus_id,
                    fingerprint=entry.fingerprint,
                    fingerprint_algorithm=entry.fingerprint_algorithm,
                    public_key_pem=entry.public_key_pem,
                    status=normalized_status,
                    labels=dict(entry.labels),
                )
            )
        else:
            entries.append(entry)
    if not matched:
        raise KeyringError(f"no keyring entry for corpus {corpus_id} and fingerprint {normalized_fingerprint}")
    return CorpusKeyring(keyring_version=keyring.keyring_version, entries=tuple(entries))


def entries_for(
    keyring: CorpusKeyring,
    corpus_id: str,
    *,
    status: KeyringStatus | str | None = None,
) -> tuple[KeyringEntry, ...]:
    normalized_status = _status(status) if status is not None else None
    return tuple(
        entry
        for entry in keyring.entries
        if entry.corpus_id == corpus_id and (normalized_status is None or entry.status == normalized_status)
    )


def verify_corpus_with_keyring(corpus: TaskCorpus, keyring: CorpusKeyring) -> KeyringEntry:
    candidates = entries_for(keyring, corpus.corpus_id, status=KeyringStatus.ACTIVE)
    if not candidates:
        raise TaskLoadError(
            f"no active trusted key for corpus {corpus.corpus_id}",
            reason=TaskLoadReason.INVALID_SIGNATURE.value,
        )
    for entry in candidates:
        try:
            verify_corpus_signature(corpus, entry.public_key_pem)
            return entry
        except TaskLoadError:
            continue
    raise TaskLoadError(
        f"corpus signature verification failed for {corpus.corpus_id} against active keyring entries",
        reason=TaskLoadReason.INVALID_SIGNATURE.value,
    )


def normalize_public_key_pem(public_key: Path | str | bytes) -> str:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise KeyringError("cryptography is required for corpus keyrings; install self-harness[provenance]") from exc

    public_key_bytes = _public_key_bytes(public_key)
    try:
        loaded_key = serialization.load_pem_public_key(public_key_bytes)
    except ValueError:
        try:
            loaded_key = Ed25519PublicKey.from_public_bytes(_decode_public_key_bytes(public_key_bytes))
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise KeyringError("public key must be Ed25519 PEM, raw bytes, or base64 raw bytes") from exc
    if not isinstance(loaded_key, Ed25519PublicKey):
        raise KeyringError("public key is not an Ed25519 public key")
    public_pem = loaded_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return public_pem.decode("ascii")


def _entry_from_row(row: object, index: int) -> KeyringEntry:
    label = f"keyring entry {index}"
    if not isinstance(row, dict):
        raise KeyringError(f"{label} must be an object")
    corpus_id = _required_str(row, "corpus_id", label)
    fingerprint = _fingerprint_hex(_required_str(row, "fingerprint", label), f"{label} fingerprint")
    fingerprint_algorithm = _required_str(row, "fingerprint_algorithm", label)
    if fingerprint_algorithm != FINGERPRINT_ALGORITHM:
        raise KeyringError(f"{label} uses unsupported fingerprint_algorithm: {fingerprint_algorithm}")
    public_key_pem = _required_str(row, "public_key_pem", label)
    if "PRIVATE KEY" in public_key_pem:
        raise KeyringError(f"{label} must not contain private key material")
    normalized_pem = normalize_public_key_pem(public_key_pem)
    actual_fingerprint = _fingerprint(normalized_pem)
    if actual_fingerprint != fingerprint:
        raise KeyringError(
            f"{label} fingerprint mismatch: expected {fingerprint}, got {actual_fingerprint}",
        )
    return KeyringEntry(
        corpus_id=corpus_id,
        fingerprint=actual_fingerprint,
        fingerprint_algorithm=fingerprint_algorithm,
        public_key_pem=normalized_pem,
        status=_status(_required_str(row, "status", label)),
        labels=_labels(row.get("labels", {}), f"{label} labels"),
    )


def _required_str(row: dict[str, Any], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise KeyringError(f"{label} missing string field: {key}")
    return value


def _status(value: KeyringStatus | str) -> KeyringStatus:
    try:
        return value if isinstance(value, KeyringStatus) else KeyringStatus(value)
    except ValueError as exc:
        allowed = ", ".join(status.value for status in KeyringStatus)
        raise KeyringError(f"status must be one of: {allowed}") from exc


def _labels(value: Mapping[str, str] | object, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise KeyringError(f"{label} must be an object")
    labels: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise KeyringError(f"{label} keys must be non-empty strings")
        if not isinstance(item, str):
            raise KeyringError(f"{label} values must be strings")
        labels[key] = item
    return labels


def _fingerprint(public_key_pem: str) -> str:
    try:
        return public_key_fingerprint(public_key_pem)
    except CorpusSigningError as exc:
        raise KeyringError(str(exc)) from exc


def _fingerprint_hex(value: str, label: str) -> str:
    lowered = value.lower()
    if len(lowered) != 64 or any(character not in string.hexdigits for character in lowered):
        raise KeyringError(f"{label} must be a 64-character hex string")
    return lowered


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
