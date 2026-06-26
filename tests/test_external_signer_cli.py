import json
import sys
from pathlib import Path

import pytest

from self_harness.cli import main
from self_harness.corpus_signing import generate_keypair

FIXTURE_SIGNER = Path("tests/fixtures/external_signer.py")
EXAMPLE_SIGNER = Path("scripts/example_external_signer.py")


def test_corpus_sign_external_signer_cli_round_trip(tmp_path: Path, capsys) -> None:
    pytest.importorskip("cryptography")
    corpus = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    _write_corpus(corpus)

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus),
            "--external-signer",
            _signer_command(),
            "--out",
            str(signed_path),
            "--signer-provider",
            "fixture",
            "--key-id",
            "fixture-key-1",
        ]
    )
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    signed_payload = json.loads(signed_path.read_text(encoding="utf-8"))
    public_key = tmp_path / "external_signer.pub"
    public_key.write_text(output["signer"]["public_key_b64"], encoding="utf-8")

    assert code == 0
    assert captured.err == ""
    assert output["ok"] is True
    assert output["signer"]["mode"] == "external-signer"
    assert output["signer"]["provider"] == "fixture"
    assert output["signer"]["key_id"] == "fixture-key-1"
    assert output["signer"]["public_key_b64"]
    assert set(signed_payload) == {"checksum", "corpus_id", "corpus_version", "signature", "tasks"}
    assert main(["validate-tasks", str(signed_path), "--require-corpus-signature", str(public_key)]) == 0


def test_example_external_signer_cli_round_trip(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cryptography")
    corpus = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    private_path = tmp_path / "example.ed25519"
    private_pem, public_pem = generate_keypair()
    private_path.write_bytes(private_pem)
    public_key = tmp_path / "example.ed25519.pub"
    public_key.write_bytes(public_pem)
    _write_corpus(corpus)
    monkeypatch.setenv("SELF_HARNESS_EXAMPLE_SIGNER_KEY", str(private_path))
    monkeypatch.setenv("SELF_HARNESS_EXAMPLE_SIGNER_PROVIDER", "example-test")
    monkeypatch.setenv("SELF_HARNESS_EXAMPLE_SIGNER_KEY_ID", "example-test-key")

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus),
            "--external-signer",
            f"{sys.executable} {EXAMPLE_SIGNER}",
            "--out",
            str(signed_path),
            "--signer-provider",
            "example-test",
            "--key-id",
            "example-test-key",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert output["signer"]["mode"] == "external-signer"
    assert output["signer"]["provider"] == "example-test"
    assert output["signer"]["key_id"] == "example-test-key"
    assert main(["validate-tasks", str(signed_path), "--require-corpus-signature", str(public_key)]) == 0


def test_corpus_sign_external_signer_failure_goes_to_stderr(tmp_path: Path, capsys) -> None:
    pytest.importorskip("cryptography")
    corpus = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    _write_corpus(corpus)

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus),
            "--external-signer",
            _signer_command("malformed"),
            "--out",
            str(signed_path),
            "--signer-provider",
            "fixture",
        ]
    )
    captured = capsys.readouterr()
    output = json.loads(captured.err)

    assert code == 2
    assert captured.out == ""
    assert output["schema_version"] == 1
    assert output["type"] == "external_signer_error"
    assert output["code"] == "signer_malformed_json"
    assert not signed_path.exists()


def test_corpus_sign_external_signer_rejects_passphrase_options(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    _write_corpus(corpus)

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus),
            "--external-signer",
            _signer_command(),
            "--out",
            str(signed_path),
            "--passphrase",
            "not-used",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["reason"] == "corpus-signing-error"
    assert "passphrase" in output["message"]


def test_corpus_sign_external_signer_rejects_unexpected_fingerprint(tmp_path: Path, capsys) -> None:
    pytest.importorskip("cryptography")
    corpus = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    _write_corpus(corpus)

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus),
            "--external-signer",
            _signer_command(),
            "--out",
            str(signed_path),
            "--fingerprint",
            "0" * 64,
        ]
    )
    captured = capsys.readouterr()
    output = json.loads(captured.err)

    assert code == 2
    assert output["code"] == "signer_payload_mismatch"
    assert not signed_path.exists()


def _signer_command(mode: str | None = None) -> str:
    command = f"{sys.executable} {FIXTURE_SIGNER}"
    if mode is not None:
        command = f"{command} {mode}"
    return command


def _write_corpus(path: Path) -> None:
    payload = {
        "corpus_version": "1",
        "corpus_id": "external-signer-cli",
        "tasks": [
            {
                "id": "held-in",
                "split": "held_in",
                "failure_mode": "local_subprocess",
                "description": "held-in",
                "metadata": {"solve_command": "true"},
            },
            {
                "id": "held-out",
                "split": "held_out",
                "failure_mode": "local_subprocess",
                "description": "held-out",
                "metadata": {"verify_command": "true"},
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
