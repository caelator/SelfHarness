from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from self_harness.exceptions import TaskLoadError
from self_harness.types import Split, Task, stable_json_dumps

CORPUS_VERSION = "1"
LEGACY_CORPUS_ID = "legacy-tasks-json"


class TaskLoadReason(StrEnum):
    MISSING_FILE = "missing-file"
    INVALID_JSON = "invalid-json"
    INVALID_SCHEMA = "invalid-schema"
    UNSUPPORTED_VERSION = "unsupported-version"
    CHECKSUM_MISMATCH = "checksum-mismatch"
    INVALID_SIGNATURE = "invalid-signature"
    SPLIT_BALANCE = "split-balance"


@dataclass(frozen=True)
class TaskCorpus:
    corpus_version: str
    corpus_id: str
    tasks: list[Task]
    checksum: str | None = None
    signature: str | None = None


def load_corpus(
    path: Path,
    *,
    allow_legacy: bool = False,
    verify_checksum: bool = True,
    verify_signature_key: Path | str | None = None,
    min_per_split: int = 0,
) -> TaskCorpus:
    """Load a versioned task corpus from JSON.

    Legacy files shaped as ``{"tasks": [...]}`` are accepted only when
    ``allow_legacy`` is true. New production corpora should include
    ``corpus_version`` and ``corpus_id``.
    """

    data = _read_json_object(path)
    corpus = _corpus_from_data(data, allow_legacy=allow_legacy)
    if verify_checksum and corpus.checksum is not None:
        actual = corpus_checksum(corpus)
        if corpus.checksum != actual:
            raise TaskLoadError(
                f"corpus checksum mismatch: expected {corpus.checksum}, got {actual}",
                reason=TaskLoadReason.CHECKSUM_MISMATCH.value,
            )
    if verify_signature_key is not None:
        verify_corpus_signature(corpus, verify_signature_key)
    validate_split_balance(corpus, min_per_split=min_per_split)
    return corpus


def corpus_integrity_payload(corpus: TaskCorpus) -> dict[str, object]:
    return {
        "corpus_version": corpus.corpus_version,
        "corpus_id": corpus.corpus_id,
        "tasks": corpus.tasks,
    }


def corpus_checksum(corpus: TaskCorpus) -> str:
    return hashlib.sha256(_corpus_integrity_bytes(corpus)).hexdigest()


def verify_corpus_signature(corpus: TaskCorpus, public_key: Path | str) -> None:
    """Verify a corpus Ed25519 signature over the canonical corpus payload."""

    if corpus.signature is None:
        raise TaskLoadError(
            f"corpus {corpus.corpus_id} is missing required signature",
            reason=TaskLoadReason.INVALID_SIGNATURE.value,
        )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise TaskLoadError(
            "cryptography is required for corpus signature verification; install self-harness[provenance]",
            reason=TaskLoadReason.INVALID_SIGNATURE.value,
        ) from exc

    try:
        signature = _decode_base64(corpus.signature, "corpus signature")
        public_key_bytes = _public_key_bytes(public_key)
        try:
            loaded_key = serialization.load_pem_public_key(public_key_bytes)
        except ValueError:
            loaded_key = Ed25519PublicKey.from_public_bytes(_decode_public_key_bytes(public_key_bytes))
        if not isinstance(loaded_key, Ed25519PublicKey):
            raise TypeError("public key is not an Ed25519 public key")
        loaded_key.verify(signature, _corpus_integrity_bytes(corpus))
    except (InvalidSignature, TypeError, ValueError, OSError, binascii.Error) as exc:
        raise TaskLoadError(
            f"corpus signature verification failed for {corpus.corpus_id}",
            reason=TaskLoadReason.INVALID_SIGNATURE.value,
        ) from exc


def split_counts(corpus: TaskCorpus) -> dict[str, int]:
    return {
        split.value: sum(1 for task in corpus.tasks if task.split == split)
        for split in [Split.HELD_IN, Split.HELD_OUT]
    }


def validate_split_balance(corpus: TaskCorpus, *, min_per_split: int = 0) -> None:
    if min_per_split < 0:
        raise TaskLoadError(
            "min_per_split must be non-negative",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    if min_per_split == 0:
        return
    counts = split_counts(corpus)
    missing = [split for split, count in counts.items() if count < min_per_split]
    if missing:
        raise TaskLoadError(
            f"corpus {corpus.corpus_id} has fewer than {min_per_split} task(s) for split(s): {', '.join(missing)}",
            reason=TaskLoadReason.SPLIT_BALANCE.value,
        )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaskLoadError(
            f"missing tasks file: {path}",
            reason=TaskLoadReason.MISSING_FILE.value,
        ) from exc
    except json.JSONDecodeError as exc:
        raise TaskLoadError(
            f"invalid tasks JSON: {path}",
            reason=TaskLoadReason.INVALID_JSON.value,
        ) from exc
    if not isinstance(value, dict):
        raise TaskLoadError(
            "task corpus JSON must be an object",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    return value


def _corpus_from_data(data: dict[str, Any], *, allow_legacy: bool) -> TaskCorpus:
    legacy = "corpus_version" not in data and "corpus_id" not in data
    if legacy and not allow_legacy:
        raise TaskLoadError(
            "task corpus must include corpus_version and corpus_id",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    corpus_version = CORPUS_VERSION if legacy else _row_str(data, "corpus_version", "task corpus")
    corpus_id = LEGACY_CORPUS_ID if legacy else _row_str(data, "corpus_id", "task corpus")
    if not legacy and corpus_version != CORPUS_VERSION:
        raise TaskLoadError(
            f"unsupported corpus_version: {corpus_version}",
            reason=TaskLoadReason.UNSUPPORTED_VERSION.value,
        )
    checksum = data.get("checksum")
    if checksum is not None and (not isinstance(checksum, str) or not checksum):
        raise TaskLoadError(
            "checksum must be a non-empty string when provided",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    signature = data.get("signature")
    if signature is not None and (not isinstance(signature, str) or not signature):
        raise TaskLoadError(
            "signature must be a non-empty string when provided",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    rows = data.get("tasks")
    if not isinstance(rows, list):
        raise TaskLoadError(
            "task corpus must include a tasks list",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    tasks = [_task_from_row(row, index) for index, row in enumerate(rows)]
    _validate_unique_task_ids(tasks)
    return TaskCorpus(
        corpus_version=corpus_version,
        corpus_id=corpus_id,
        tasks=tasks,
        checksum=checksum,
        signature=signature,
    )


def _task_from_row(row: object, index: int) -> Task:
    label = f"task row {index}"
    if not isinstance(row, dict):
        raise TaskLoadError(
            f"{label} must be an object",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    try:
        task_id = _row_str(row, "id", label)
        split = Split(_row_str(row, "split", label))
        failure_mode = _row_str(row, "failure_mode", label)
        description = _row_str(row, "description", label)
    except ValueError as exc:
        raise TaskLoadError(
            f"{label} has invalid split",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        ) from exc
    metadata = row.get("metadata", {})
    if not isinstance(metadata, dict):
        raise TaskLoadError(
            f"{label} metadata must be an object",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    return Task(
        id=task_id,
        split=split,
        failure_mode=failure_mode,
        description=description,
        metadata=dict(metadata),
    )


def _row_str(row: dict[str, Any], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise TaskLoadError(
            f"{label} missing string field: {key}",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )
    return value


def _validate_unique_task_ids(tasks: list[Task]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for task in tasks:
        if task.id in seen:
            duplicates.add(task.id)
        seen.add(task.id)
    if duplicates:
        raise TaskLoadError(
            f"duplicate task id(s): {', '.join(sorted(duplicates))}",
            reason=TaskLoadReason.INVALID_SCHEMA.value,
        )


def _corpus_integrity_bytes(corpus: TaskCorpus) -> bytes:
    return stable_json_dumps(corpus_integrity_payload(corpus)).encode("utf-8")


def _decode_base64(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError(f"{label} must be base64") from exc


def _public_key_bytes(public_key: Path | str) -> bytes:
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
    return _decode_base64(text, "Ed25519 public key")
