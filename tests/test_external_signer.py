import os
import subprocess
import sys
from pathlib import Path

import pytest

from self_harness.corpus import TaskCorpus, corpus_checksum, load_corpus
from self_harness.corpus_signing import verify_bytes_signature
from self_harness.signing import (
    ExternalSignerError,
    sign_corpus_with_external_signer,
    sign_payload_with_external_signer,
)
from self_harness.types import Split, Task, stable_json_dumps, to_jsonable

FIXTURE_SIGNER = Path("tests/fixtures/external_signer.py")


def test_external_signer_fixture_signature_verifies_and_is_deterministic(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    corpus = _corpus()
    command = _signer_command()

    first = sign_corpus_with_external_signer(corpus, command, provider="fixture")
    second = sign_corpus_with_external_signer(corpus, command, provider="fixture")
    signed = tmp_path / "signed.json"
    _write_signed_corpus(signed, corpus, first.signature)

    loaded = load_corpus(signed, verify_signature_key=first.public_key_b64)

    assert loaded.signature == first.signature
    assert first.signature == second.signature
    assert first.fingerprint == second.fingerprint
    assert first.key_id == "fixture-key-1"
    assert first.provider == "fixture"


def test_external_signer_can_sign_exact_payload_bytes() -> None:
    pytest.importorskip("cryptography")
    payload = b'{"manifest":"exact bytes"}\n'

    response = sign_payload_with_external_signer(payload, _signer_command(), provider="fixture")

    verify_bytes_signature(payload, response.signature, response.public_key_b64)
    assert response.key_id == "fixture-key-1"
    assert response.provider == "fixture"


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("oversize", "signer_oversize"),
        ("malformed", "signer_malformed_json"),
        ("nonzero", "fixture_nonzero"),
        ("missing", "signer_missing_field"),
        ("mismatch", "signer_payload_mismatch"),
    ],
)
def test_external_signer_failures_are_structured(mode: str, code: str) -> None:
    pytest.importorskip("cryptography")

    with pytest.raises(ExternalSignerError) as exc:
        sign_corpus_with_external_signer(_corpus(), _signer_command(mode), provider="fixture", max_output_bytes=512)

    assert exc.value.failure.to_jsonable()["schema_version"] == 1
    assert exc.value.failure.code == code


def test_external_signer_timeout_is_structured() -> None:
    pytest.importorskip("cryptography")

    with pytest.raises(ExternalSignerError) as exc:
        sign_corpus_with_external_signer(_corpus(), _signer_command("sleep"), provider="fixture", timeout_seconds=0.01)

    assert exc.value.failure.code == "signer_timeout"
    assert exc.value.failure.timeout_ms == 1000


def test_core_import_does_not_import_cryptography() -> None:
    code = (
        "import builtins\n"
        "real_import = builtins.__import__\n"
        "def blocked(name, globals=None, locals=None, fromlist=(), level=0):\n"
        "    if name == 'cryptography' or name.startswith('cryptography.'):\n"
        "        raise ImportError('blocked cryptography import')\n"
        "    return real_import(name, globals, locals, fromlist, level)\n"
        "builtins.__import__ = blocked\n"
        "import self_harness\n"
        "print('ok')\n"
    )
    env = {**os.environ, "PYTHONPATH": "src"}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_source_signer_path_does_not_use_shell_true() -> None:
    assert "shell=True" not in Path("src/self_harness/signing/external_signer.py").read_text(encoding="utf-8")


def _signer_command(mode: str | None = None) -> tuple[str, ...]:
    command = [sys.executable, str(FIXTURE_SIGNER)]
    if mode is not None:
        command.append(mode)
    return tuple(command)


def _corpus() -> TaskCorpus:
    return TaskCorpus(
        corpus_version="1",
        corpus_id="external-signer-local",
        tasks=[
            Task("held-in", Split.HELD_IN, "local_subprocess", "held-in", {"solve_command": "true"}),
            Task("held-out", Split.HELD_OUT, "local_subprocess", "held-out", {"verify_command": "true"}),
        ],
    )


def _write_signed_corpus(path: Path, corpus: TaskCorpus, signature: str) -> None:
    payload = {
        "corpus_version": corpus.corpus_version,
        "corpus_id": corpus.corpus_id,
        "tasks": to_jsonable(corpus.tasks),
        "checksum": corpus_checksum(corpus),
        "signature": signature,
    }
    path.write_text(stable_json_dumps(payload) + "\n", encoding="utf-8")
