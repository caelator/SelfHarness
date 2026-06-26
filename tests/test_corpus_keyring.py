import json
from pathlib import Path

import pytest

from self_harness.corpus import TaskCorpus, TaskLoadReason, corpus_checksum
from self_harness.corpus_keyring import (
    KEYRING_VERSION,
    KeyringError,
    KeyringStatus,
    add_keyring_entry,
    empty_keyring,
    keyring_to_jsonable,
    load_keyring,
    save_keyring,
    set_keyring_entry_status,
    verify_corpus_with_keyring,
)
from self_harness.corpus_signing import FINGERPRINT_ALGORITHM, generate_keypair, sign_corpus
from self_harness.exceptions import TaskLoadError
from self_harness.types import Split, Task, stable_json_dumps


def test_keyring_add_save_load_round_trip_is_stable(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    del private_pem
    path = tmp_path / "corpus.keyring.json"
    keyring = add_keyring_entry(
        empty_keyring(),
        corpus_id="signed-local",
        public_key=public_pem,
        labels={"environment": "ci"},
    )

    save_keyring(keyring, path)
    first_bytes = path.read_text(encoding="utf-8")
    loaded = load_keyring(path)
    save_keyring(loaded, path)

    assert path.read_text(encoding="utf-8") == first_bytes
    assert loaded.keyring_version == KEYRING_VERSION
    assert loaded.entries[0].fingerprint_algorithm == FINGERPRINT_ALGORITHM
    assert loaded.entries[0].status == KeyringStatus.ACTIVE
    assert loaded.entries[0].labels == {"environment": "ci"}
    assert "PRIVATE KEY" not in first_bytes


def test_keyring_rejects_duplicate_fingerprint_for_same_corpus() -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    del private_pem
    keyring = add_keyring_entry(empty_keyring(), corpus_id="signed-local", public_key=public_pem)

    with pytest.raises(KeyringError, match="duplicate keyring entry"):
        add_keyring_entry(keyring, corpus_id="signed-local", public_key=public_pem)


def test_keyring_load_rejects_fingerprint_tampering(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    del private_pem
    path = tmp_path / "corpus.keyring.json"
    save_keyring(add_keyring_entry(empty_keyring(), corpus_id="signed-local", public_key=public_pem), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"][0]["fingerprint"] = "0" * 64
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(KeyringError, match="fingerprint mismatch"):
        load_keyring(path)


def test_keyring_status_transitions_gate_signature_verification() -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    corpus = _signed_corpus(private_pem)
    keyring = add_keyring_entry(empty_keyring(), corpus_id=corpus.corpus_id, public_key=public_pem)

    trusted = verify_corpus_with_keyring(corpus, keyring)
    revoked = set_keyring_entry_status(
        keyring,
        corpus_id=corpus.corpus_id,
        fingerprint=trusted.fingerprint,
        status=KeyringStatus.REVOKED,
    )

    with pytest.raises(TaskLoadError) as exc:
        verify_corpus_with_keyring(corpus, revoked)

    assert exc.value.reason == TaskLoadReason.INVALID_SIGNATURE.value


def test_keyring_verification_rejects_wrong_corpus_id() -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    corpus = _signed_corpus(private_pem)
    keyring = add_keyring_entry(empty_keyring(), corpus_id="other-corpus", public_key=public_pem)

    with pytest.raises(TaskLoadError) as exc:
        verify_corpus_with_keyring(corpus, keyring)

    assert exc.value.reason == TaskLoadReason.INVALID_SIGNATURE.value


def test_keyring_writer_emits_exact_public_fields() -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    del private_pem
    keyring = add_keyring_entry(empty_keyring(), corpus_id="signed-local", public_key=public_pem)
    payload = keyring_to_jsonable(keyring)

    assert set(payload) == {"entries", "keyring_version"}
    assert set(payload["entries"][0]) == {
        "corpus_id",
        "fingerprint",
        "fingerprint_algorithm",
        "labels",
        "public_key_pem",
        "status",
    }
    assert "PRIVATE KEY" not in stable_json_dumps(payload)


def _signed_corpus(private_pem: bytes) -> TaskCorpus:
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="signed-local",
        tasks=[
            Task("held-in", Split.HELD_IN, "local_subprocess", "held-in", {"solve_command": "true"}),
            Task("held-out", Split.HELD_OUT, "local_subprocess", "held-out", {"verify_command": "true"}),
        ],
    )
    return TaskCorpus(
        corpus_version=corpus.corpus_version,
        corpus_id=corpus.corpus_id,
        tasks=corpus.tasks,
        checksum=corpus_checksum(corpus),
        signature=sign_corpus(corpus, private_pem),
    )
