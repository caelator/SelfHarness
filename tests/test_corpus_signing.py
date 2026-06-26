import base64
import json
from pathlib import Path

import pytest

from self_harness.corpus import TaskCorpus, TaskLoadReason, load_corpus
from self_harness.corpus_signing import (
    FINGERPRINT_ALGORITHM,
    PASSPHRASE_ERROR,
    generate_keypair,
    public_key_fingerprint,
    sign_corpus,
)
from self_harness.exceptions import CorpusSigningError, TaskLoadError
from self_harness.types import Split, Task, stable_json_dumps, to_jsonable


def test_generate_keypair_signs_and_verifies_corpus(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    corpus = _corpus()
    signature = sign_corpus(corpus, private_pem)
    public_key = tmp_path / "corpus.pub"
    signed = tmp_path / "signed.json"
    public_key.write_bytes(public_pem)
    _write_signed_corpus(signed, corpus, signature)

    loaded = load_corpus(signed, verify_signature_key=public_key)

    assert loaded.corpus_id == corpus.corpus_id
    assert loaded.signature == signature
    assert b"PRIVATE KEY" in private_pem
    assert b"PRIVATE KEY" not in signed.read_bytes()


def test_encrypted_keypair_signs_and_verifies_corpus(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    passphrase = "correct-horse-battery"
    private_pem, public_pem = generate_keypair(passphrase=passphrase)
    corpus = _corpus()
    signature = sign_corpus(corpus, private_pem, passphrase=passphrase)
    public_key = tmp_path / "corpus.pub"
    signed = tmp_path / "signed.json"
    public_key.write_bytes(public_pem)
    _write_signed_corpus(signed, corpus, signature)

    loaded = load_corpus(signed, verify_signature_key=public_key)

    assert b"ENCRYPTED PRIVATE KEY" in private_pem
    assert loaded.signature == signature
    assert passphrase not in signed.read_text(encoding="utf-8")


def test_encrypted_keypair_wrong_or_missing_passphrase_is_redacted() -> None:
    pytest.importorskip("cryptography")
    passphrase = "correct-horse-battery"
    wrong = "wrong-passphrase"
    private_pem, _public_pem = generate_keypair(passphrase=passphrase)

    for candidate in [None, wrong]:
        with pytest.raises(CorpusSigningError) as exc:
            sign_corpus(_corpus(), private_pem, passphrase=candidate)
        message = str(exc.value)
        assert message == PASSPHRASE_ERROR
        assert passphrase not in message
        assert wrong not in message


def test_keypair_passphrase_must_be_non_empty() -> None:
    pytest.importorskip("cryptography")

    with pytest.raises(CorpusSigningError, match="must be non-empty"):
        generate_keypair(passphrase="")


def test_public_key_fingerprint_is_stable_across_pem_raw_and_base64(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    del private_pem
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    public_path = tmp_path / "corpus.pub"
    public_path.write_bytes(public_pem)
    loaded_key = serialization.load_pem_public_key(public_pem)
    raw = loaded_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    assert public_key_fingerprint(public_path) == public_key_fingerprint(raw)
    assert public_key_fingerprint(public_path) == public_key_fingerprint(base64.b64encode(raw).decode("ascii"))
    assert len(public_key_fingerprint(public_path)) == 64


def test_signed_corpus_json_has_allowed_keys_and_no_private_material(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    private_pem, _public_pem = generate_keypair()
    corpus = _corpus()
    signature = sign_corpus(corpus, private_pem)
    signed = tmp_path / "signed.json"

    _write_signed_corpus(signed, corpus, signature)
    payload = json.loads(signed.read_text(encoding="utf-8"))

    assert set(payload) == {"checksum", "corpus_id", "corpus_version", "signature", "tasks"}
    assert "PRIVATE KEY" not in signed.read_text(encoding="utf-8")


def test_tampering_signed_payload_fails_signature_verification(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    corpus = _corpus()
    signed = tmp_path / "signed.json"
    public_key = tmp_path / "corpus.pub"
    public_key.write_bytes(public_pem)
    _write_signed_corpus(signed, corpus, sign_corpus(corpus, private_pem))
    payload = json.loads(signed.read_text(encoding="utf-8"))
    payload["tasks"][0]["description"] = "tampered"
    signed.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(TaskLoadError) as exc:
        load_corpus(signed, verify_signature_key=public_key, verify_checksum=False)

    assert exc.value.reason == TaskLoadReason.INVALID_SIGNATURE.value


def test_tampering_checksum_fails_before_signature_verification(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    private_pem, public_pem = generate_keypair()
    corpus = _corpus()
    signed = tmp_path / "signed.json"
    public_key = tmp_path / "corpus.pub"
    public_key.write_bytes(public_pem)
    _write_signed_corpus(signed, corpus, sign_corpus(corpus, private_pem))
    payload = json.loads(signed.read_text(encoding="utf-8"))
    payload["checksum"] = "bad"
    signed.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(TaskLoadError) as exc:
        load_corpus(signed, verify_signature_key=public_key)

    assert exc.value.reason == TaskLoadReason.CHECKSUM_MISMATCH.value


def test_fingerprint_algorithm_name_is_explicit() -> None:
    assert FINGERPRINT_ALGORITHM == "sha256-spki-der-hex"


def _corpus() -> TaskCorpus:
    return TaskCorpus(
        corpus_version="1",
        corpus_id="signed-local",
        tasks=[
            Task("held-in", Split.HELD_IN, "local_subprocess", "held-in", {"solve_command": "true"}),
            Task("held-out", Split.HELD_OUT, "local_subprocess", "held-out", {"verify_command": "true"}),
        ],
    )


def _write_signed_corpus(path: Path, corpus: TaskCorpus, signature: str) -> None:
    from self_harness.corpus import corpus_checksum

    payload = {
        "corpus_version": corpus.corpus_version,
        "corpus_id": corpus.corpus_id,
        "tasks": to_jsonable(corpus.tasks),
        "checksum": corpus_checksum(corpus),
        "signature": signature,
    }
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
