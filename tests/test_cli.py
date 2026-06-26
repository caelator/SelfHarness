import json
from pathlib import Path

import pytest

from self_harness.cli import main
from self_harness.corpus_signing import PASSPHRASE_ERROR


def test_demo_cli_wires_runtime_config(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo"

    code = main(
        [
            "demo",
            "--rounds",
            "1",
            "--seed",
            "7",
            "--evaluation-repeats",
            "1",
            "--max-proposals",
            "2",
            "--max-payload-bytes",
            "500",
            "--out",
            str(out_dir),
        ]
    )

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert code == 0
    assert manifest["seed"] == 7
    assert manifest["evaluation_repeats"] == 1
    assert manifest["decoding_budget"] == {"max_payload_bytes": 500, "max_proposals": 2}


def test_demo_cli_fail_on_empty_returns_nonzero(tmp_path: Path) -> None:
    out_dir = tmp_path / "empty"

    code = main(
        [
            "demo",
            "--rounds",
            "1",
            "--max-payload-bytes",
            "1",
            "--fail-on-empty",
            "--out",
            str(out_dir),
        ]
    )

    assert code == 2


def test_audit_summary_cli_outputs_json(tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "demo"
    assert main(["demo", "--rounds", "1", "--out", str(out_dir)]) == 0

    code = main(["audit-summary", str(out_dir)])
    output = json.loads(capsys.readouterr().out.splitlines()[-1])

    assert code == 0
    assert output["schema_version"] == "1.2"
    assert output["rounds"] == 1
    assert output["final_held_in_score"] == 1.0
    assert output["final_held_out_score"] == 1.0


def test_inspect_harness_cli_outputs_retained_edit_report(tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "demo"
    assert main(["demo", "--rounds", "1", "--out", str(out_dir)]) == 0

    code = main(["inspect-harness", str(out_dir), "--json"])
    output = json.loads(capsys.readouterr().out.splitlines()[-1])

    assert code == 0
    assert output["schema_version"] == "1.0"
    assert output["rounds"][0]["proposal_status_counts"]["merged"] == 4
    assert "bootstrap" in output["final_harness_surfaces"]


def test_local_demo_cli_runs_subprocess_tasks(tmp_path: Path) -> None:
    tasks_json = tmp_path / "tasks.json"
    tasks_json.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "local-pass",
                        "split": "held_in",
                        "failure_mode": "local_subprocess",
                        "description": "local subprocess pass",
                        "metadata": {
                            "solve_command": "printf ok > answer.txt",
                            "verify_command": "test -f answer.txt",
                        },
                    },
                    {
                        "id": "local-pass-held-out",
                        "split": "held_out",
                        "failure_mode": "local_subprocess",
                        "description": "local subprocess held-out pass",
                        "metadata": {
                            "solve_command": "printf ok > answer.txt",
                            "verify_command": "test -f answer.txt",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "local"

    code = main(["local-demo", str(tasks_json), "--rounds", "1", "--out", str(out_dir)])

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert code == 0
    assert manifest["model_id"] == "local-subprocess-runner"


def test_local_demo_cli_accepts_versioned_corpus_flag(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path)
    out_dir = tmp_path / "local"

    code = main(["local-demo", "--corpus", str(corpus_path), "--rounds", "1", "--out", str(out_dir)])

    assert code == 0
    assert (out_dir / "manifest.json").exists()


def test_validate_tasks_cli_outputs_summary(tmp_path: Path, capsys) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path)

    code = main(["validate-tasks", str(corpus_path), "--min-per-split", "1"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert output["split_counts"] == {"held_in": 1, "held_out": 1}
    assert isinstance(output["checksum"], str)


def test_validate_tasks_cli_reports_structured_errors(tmp_path: Path, capsys) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{}", encoding="utf-8")

    code = main(["validate-tasks", str(path)])
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["ok"] is False
    assert output["reason"] == "invalid-schema"


def test_validate_tasks_cli_requires_corpus_signature(tmp_path: Path, capsys) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path)

    code = main(["validate-tasks", str(corpus_path), "--require-corpus-signature", str(tmp_path / "ed25519.pub")])
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["ok"] is False
    assert output["reason"] == "invalid-signature"


def test_corpus_keygen_sign_fingerprint_and_validate_cli_round_trip(tmp_path: Path, capsys) -> None:
    corpus_path = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    keyring_path = tmp_path / "keys" / "corpus.keyring.json"
    private_path = tmp_path / "keys" / "corpus.key"
    _write_corpus(corpus_path)

    code = main(["corpus-keygen", "--out", str(private_path)])
    keygen = json.loads(capsys.readouterr().out)
    assert code == 0
    assert keygen["ok"] is True
    assert Path(keygen["private_key"]).exists()
    assert Path(keygen["public_key"]).exists()

    code = main(["corpus-fingerprint", "--public-key", keygen["public_key"]])
    fingerprint = json.loads(capsys.readouterr().out)
    assert code == 0
    assert fingerprint["fingerprint"] == keygen["fingerprint"]

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus_path),
            "--private-key",
            str(private_path),
            "--out",
            str(signed_path),
        ]
    )
    signed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert signed["ok"] is True
    assert set(json.loads(signed_path.read_text(encoding="utf-8"))) == {
        "checksum",
        "corpus_id",
        "corpus_version",
        "signature",
        "tasks",
    }

    code = main(["validate-tasks", str(signed_path), "--require-corpus-signature", keygen["public_key"]])
    validation = json.loads(capsys.readouterr().out)
    assert code == 0
    assert validation["ok"] is True

    code = main(["corpus-keyring", "init", "--out", str(keyring_path)])
    initialized = json.loads(capsys.readouterr().out)
    assert code == 0
    assert initialized["ok"] is True

    code = main(
        [
            "corpus-keyring",
            "add",
            "--keyring",
            str(keyring_path),
            "--corpus-id",
            "cli-corpus",
            "--public-key",
            keygen["public_key"],
            "--label",
            "environment=ci",
        ]
    )
    added = json.loads(capsys.readouterr().out)
    assert code == 0
    assert added["fingerprint"] == keygen["fingerprint"]

    code = main(["corpus-keyring", "inspect", "--keyring", str(keyring_path), "--json"])
    inspected = json.loads(capsys.readouterr().out)
    assert code == 0
    assert inspected["entries"][0]["labels"] == {"environment": "ci"}

    code = main(["validate-tasks", str(signed_path), "--require-corpus-keyring", str(keyring_path)])
    keyring_validation = json.loads(capsys.readouterr().out)
    assert code == 0
    assert keyring_validation["ok"] is True
    assert keyring_validation["trusted_key_fingerprint"] == keygen["fingerprint"]

    code = main(
        [
            "local-demo",
            "--corpus",
            str(signed_path),
            "--require-corpus-keyring",
            str(keyring_path),
            "--rounds",
            "1",
            "--out",
            str(tmp_path / "local-keyring"),
        ]
    )
    capsys.readouterr()
    assert code == 0


def test_corpus_keygen_refuses_overwrite_without_force(tmp_path: Path, capsys) -> None:
    private_path = tmp_path / "corpus.key"

    assert main(["corpus-keygen", "--out", str(private_path)]) == 0
    capsys.readouterr()
    code = main(["corpus-keygen", "--out", str(private_path)])
    refused = json.loads(capsys.readouterr().out)
    code_force = main(["corpus-keygen", "--out", str(private_path), "--force"])

    assert code == 2
    assert refused["reason"] == "key-exists"
    assert code_force == 0


def test_encrypted_corpus_keygen_sign_validate_cli_round_trip(tmp_path: Path, capsys, monkeypatch) -> None:
    secret = "test-secret-passphrase"
    monkeypatch.setenv("CORPUS_TEST_PASSPHRASE", secret)
    corpus_path = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    keyring_path = tmp_path / "corpus.keyring.json"
    private_path = tmp_path / "corpus.key"
    _write_corpus(corpus_path)

    code = main(["corpus-keygen", "--out", str(private_path), "--passphrase-env", "CORPUS_TEST_PASSPHRASE"])
    captured = capsys.readouterr()
    keygen = json.loads(captured.out)
    assert code == 0
    assert keygen["private_key_encrypted"] is True
    assert secret not in captured.out + captured.err
    assert b"ENCRYPTED PRIVATE KEY" in private_path.read_bytes()

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus_path),
            "--private-key",
            str(private_path),
            "--out",
            str(signed_path),
            "--passphrase-env",
            "CORPUS_TEST_PASSPHRASE",
        ]
    )
    captured = capsys.readouterr()
    signed = json.loads(captured.out)
    assert code == 0
    assert signed["ok"] is True
    assert secret not in captured.out + captured.err
    assert secret not in signed_path.read_text(encoding="utf-8")

    assert main(["validate-tasks", str(signed_path), "--require-corpus-signature", keygen["public_key"]]) == 0
    capsys.readouterr()
    assert main(
        [
            "corpus-keyring",
            "add",
            "--keyring",
            str(keyring_path),
            "--corpus-id",
            "cli-corpus",
            "--public-key",
            keygen["public_key"],
        ]
    ) == 0
    capsys.readouterr()
    assert main(["validate-tasks", str(signed_path), "--require-corpus-keyring", str(keyring_path)]) == 0
    capsys.readouterr()
    assert secret not in keyring_path.read_text(encoding="utf-8")


def test_encrypted_corpus_sign_accepts_passphrase_file(tmp_path: Path, capsys) -> None:
    secret = "file-secret-passphrase"
    corpus_path = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    private_path = tmp_path / "corpus.key"
    passphrase_file = tmp_path / "passphrase.txt"
    passphrase_file.write_text(secret + "\n", encoding="utf-8")
    _write_corpus(corpus_path)

    assert main(["corpus-keygen", "--out", str(private_path), "--passphrase-file", str(passphrase_file)]) == 0
    keygen = json.loads(capsys.readouterr().out)
    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus_path),
            "--private-key",
            str(private_path),
            "--out",
            str(signed_path),
            "--passphrase-file",
            str(passphrase_file),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert main(["validate-tasks", str(signed_path), "--require-corpus-signature", keygen["public_key"]]) == 0


def test_encrypted_corpus_sign_missing_passphrase_is_redacted(tmp_path: Path, capsys, monkeypatch) -> None:
    secret = "do-not-print-this"
    monkeypatch.setenv("CORPUS_TEST_PASSPHRASE", secret)
    corpus_path = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    private_path = tmp_path / "corpus.key"
    _write_corpus(corpus_path)

    assert main(["corpus-keygen", "--out", str(private_path), "--passphrase-env", "CORPUS_TEST_PASSPHRASE"]) == 0
    capsys.readouterr()
    monkeypatch.delenv("CORPUS_TEST_PASSPHRASE")

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus_path),
            "--private-key",
            str(private_path),
            "--out",
            str(signed_path),
            "--passphrase-env",
            "CORPUS_TEST_PASSPHRASE",
        ]
    )
    missing_source = capsys.readouterr()
    missing_output = json.loads(missing_source.out)
    assert code == 2
    assert missing_output["reason"] == "corpus-signing-error"
    assert secret not in missing_source.out + missing_source.err

    code = main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus_path),
            "--private-key",
            str(private_path),
            "--out",
            str(signed_path),
        ]
    )
    missing_passphrase = capsys.readouterr()
    missing_passphrase_output = json.loads(missing_passphrase.out)
    assert code == 2
    assert missing_passphrase_output["message"] == PASSPHRASE_ERROR
    assert secret not in missing_passphrase.out + missing_passphrase.err


def test_corpus_keyring_revoked_key_fails_validate_tasks(tmp_path: Path, capsys) -> None:
    corpus_path = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    private_path = tmp_path / "corpus.key"
    keyring_path = tmp_path / "corpus.keyring.json"
    _write_corpus(corpus_path)

    assert main(["corpus-keygen", "--out", str(private_path)]) == 0
    keygen = json.loads(capsys.readouterr().out)
    assert main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus_path),
            "--private-key",
            str(private_path),
            "--out",
            str(signed_path),
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "corpus-keyring",
            "add",
            "--keyring",
            str(keyring_path),
            "--corpus-id",
            "cli-corpus",
            "--public-key",
            keygen["public_key"],
        ]
    ) == 0
    capsys.readouterr()

    code = main(
        [
            "corpus-keyring",
            "set-status",
            "--keyring",
            str(keyring_path),
            "--corpus-id",
            "cli-corpus",
            "--fingerprint",
            keygen["fingerprint"],
            "--status",
            "revoked",
        ]
    )
    status = json.loads(capsys.readouterr().out)
    assert code == 0
    assert status["status"] == "revoked"

    code = main(["validate-tasks", str(signed_path), "--require-corpus-keyring", str(keyring_path)])
    validation = json.loads(capsys.readouterr().out)

    assert code == 2
    assert validation["reason"] == "invalid-signature"


def test_corpus_keyring_wrong_corpus_id_fails_validate_tasks(tmp_path: Path, capsys) -> None:
    corpus_path = tmp_path / "corpus.json"
    signed_path = tmp_path / "signed.json"
    private_path = tmp_path / "corpus.key"
    keyring_path = tmp_path / "corpus.keyring.json"
    _write_corpus(corpus_path)

    assert main(["corpus-keygen", "--out", str(private_path)]) == 0
    keygen = json.loads(capsys.readouterr().out)
    assert main(
        [
            "corpus-sign",
            "--corpus",
            str(corpus_path),
            "--private-key",
            str(private_path),
            "--out",
            str(signed_path),
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "corpus-keyring",
            "add",
            "--keyring",
            str(keyring_path),
            "--corpus-id",
            "other-corpus",
            "--public-key",
            keygen["public_key"],
        ]
    ) == 0
    capsys.readouterr()

    code = main(["validate-tasks", str(signed_path), "--require-corpus-keyring", str(keyring_path)])
    validation = json.loads(capsys.readouterr().out)

    assert code == 2
    assert validation["reason"] == "invalid-signature"


def test_validate_tasks_rejects_mixed_signature_and_keyring_flags(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _write_corpus(corpus_path)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "validate-tasks",
                str(corpus_path),
                "--require-corpus-signature",
                str(tmp_path / "ed25519.pub"),
                "--require-corpus-keyring",
                str(tmp_path / "keyring.json"),
            ]
        )

    assert exc.value.code == 2


def test_audit_diff_cli_compares_runs(tmp_path: Path, capsys) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    assert main(["demo", "--rounds", "1", "--out", str(left)]) == 0
    assert main(["demo", "--rounds", "1", "--out", str(right)]) == 0

    code = main(["audit-diff", str(left), str(right), "--json"])
    output = json.loads(capsys.readouterr().out.splitlines()[-1])

    assert code == 0
    assert output["equal"] is True


def _write_corpus(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "corpus_version": "1",
                "corpus_id": "cli-corpus",
                "tasks": [
                    {
                        "id": "local-pass",
                        "split": "held_in",
                        "failure_mode": "local_subprocess",
                        "description": "local subprocess pass",
                        "metadata": {
                            "solve_command": "printf ok > answer.txt",
                            "verify_command": "test -f answer.txt",
                        },
                    },
                    {
                        "id": "local-held-out",
                        "split": "held_out",
                        "failure_mode": "local_subprocess",
                        "description": "local subprocess held-out pass",
                        "metadata": {
                            "solve_command": "true",
                            "verify_command": "true",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_model_preflight_cli_dry_run_glm(capsys) -> None:
    code = main(["model-preflight", "--backend", "glm", "--mode", "dry-run"])
    output = json.loads(capsys.readouterr().out)

    assert code == 2  # dry-run does not contact the provider, so the backend is not "ready"
    assert output["mode"] == "dry-run"
    assert output["backends"] == ["glm"]
    assert output["reproduction_claimed"] is False
    assert output["checks"][0]["backend"] == "glm"
    assert output["checks"][0]["metadata"]["default_model"] == "glm-5.2"


def test_model_preflight_cli_replay_glm_passes(capsys) -> None:
    code = main(["model-preflight", "--backend", "glm", "--mode", "replay"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert output["checks"][0]["status"] == "pass"
