import base64
import json
from pathlib import Path

import pytest

from self_harness.corpus import (
    TaskCorpus,
    TaskLoadReason,
    corpus_checksum,
    corpus_integrity_payload,
    load_corpus,
    split_counts,
)
from self_harness.exceptions import TaskLoadError
from self_harness.types import Split, Task, stable_json_dumps


def test_loads_versioned_task_corpus_and_checksum(tmp_path: Path) -> None:
    unsigned = TaskCorpus(
        corpus_version="1",
        corpus_id="local-smoke",
        tasks=[
            _task("held-in", Split.HELD_IN),
            _task("held-out", Split.HELD_OUT),
        ],
    )
    path = tmp_path / "corpus.json"
    _write_corpus(path, unsigned, checksum=corpus_checksum(unsigned))

    corpus = load_corpus(path, min_per_split=1)

    assert corpus.corpus_id == "local-smoke"
    assert split_counts(corpus) == {"held_in": 1, "held_out": 1}
    assert corpus.checksum == corpus_checksum(unsigned)


def test_loads_legacy_tasks_only_when_allowed(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"tasks": [_task_row("legacy", "held_in")]}), encoding="utf-8")

    with pytest.raises(TaskLoadError) as strict_error:
        load_corpus(path)

    corpus = load_corpus(path, allow_legacy=True)

    assert strict_error.value.reason == TaskLoadReason.INVALID_SCHEMA.value
    assert corpus.corpus_id == "legacy-tasks-json"
    assert corpus.tasks[0].id == "legacy"


def test_rejects_checksum_mismatch_and_split_imbalance(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "corpus_version": "1",
                "corpus_id": "bad",
                "checksum": "not-the-real-checksum",
                "tasks": [_task_row("one", "held_in")],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(TaskLoadError) as checksum_error:
        load_corpus(path)
    with pytest.raises(TaskLoadError) as balance_error:
        load_corpus(path, verify_checksum=False, min_per_split=1)

    assert checksum_error.value.reason == TaskLoadReason.CHECKSUM_MISMATCH.value
    assert balance_error.value.reason == TaskLoadReason.SPLIT_BALANCE.value


def test_signed_corpus_loads_with_matching_public_key(tmp_path: Path) -> None:
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="signed-local",
        tasks=[
            _task("held-in", Split.HELD_IN),
            _task("held-out", Split.HELD_OUT),
        ],
    )
    signature, public_key_path = _sign_corpus(tmp_path, corpus)
    path = tmp_path / "signed.json"
    _write_corpus(path, corpus, signature=signature)

    loaded = load_corpus(path, verify_signature_key=public_key_path)

    assert loaded.corpus_id == "signed-local"
    assert loaded.signature == signature


def test_tampered_signed_corpus_fails_closed(tmp_path: Path) -> None:
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="signed-local",
        tasks=[
            _task("held-in", Split.HELD_IN),
            _task("held-out", Split.HELD_OUT),
        ],
    )
    signature, public_key_path = _sign_corpus(tmp_path, corpus)
    path = tmp_path / "signed.json"
    _write_corpus(path, corpus, signature=signature)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tasks"][0]["description"] = "tampered after signing"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(TaskLoadError) as exc:
        load_corpus(path, verify_signature_key=public_key_path)

    assert exc.value.reason == TaskLoadReason.INVALID_SIGNATURE.value


def test_required_signature_rejects_unsigned_corpus(tmp_path: Path) -> None:
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="unsigned-local",
        tasks=[_task("held-in", Split.HELD_IN)],
    )
    path = tmp_path / "unsigned.json"
    _write_corpus(path, corpus)

    with pytest.raises(TaskLoadError) as exc:
        load_corpus(path, verify_signature_key=tmp_path / "public.key")

    assert exc.value.reason == TaskLoadReason.INVALID_SIGNATURE.value


def _task(id_: str, split: Split) -> Task:
    return Task(
        id=id_,
        split=split,
        failure_mode="local_subprocess",
        description=id_,
        metadata={
            "solve_command": "true",
            "verify_command": "true",
        },
    )


def _task_row(id_: str, split: str) -> dict[str, object]:
    return {
        "id": id_,
        "split": split,
        "failure_mode": "local_subprocess",
        "description": id_,
        "metadata": {
            "solve_command": "true",
            "verify_command": "true",
        },
    }


def _sign_corpus(tmp_path: Path, corpus: TaskCorpus) -> tuple[str, Path]:
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signature = private_key.sign(stable_json_dumps(corpus_integrity_payload(corpus)).encode("utf-8"))
    public_key_path = tmp_path / "ed25519.pub"
    public_key_path.write_text(base64.b64encode(public_key).decode("ascii"), encoding="utf-8")
    return base64.b64encode(signature).decode("ascii"), public_key_path


def _write_corpus(
    path: Path,
    corpus: TaskCorpus,
    checksum: str | None = None,
    signature: str | None = None,
) -> None:
    data: dict[str, object] = {
        "corpus_version": corpus.corpus_version,
        "corpus_id": corpus.corpus_id,
        "tasks": [_task_row(task.id, task.split.value) for task in corpus.tasks],
    }
    if checksum is not None:
        data["checksum"] = checksum
    if signature is not None:
        data["signature"] = signature
    path.write_text(json.dumps(data), encoding="utf-8")
