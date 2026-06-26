import json

from self_harness.adapters.terminal_bench.harbor_output import parse_harbor_output


def test_parse_harbor_output_pass_fixture() -> None:
    result = parse_harbor_output(
        json.dumps(
            {
                "task_id": "task-pass",
                "passed": True,
                "terminal_cause": "verifier-pass",
                "mechanism": "verifier",
                "verifier_output": "ok",
                "container_digest": "sha256:abc",
            }
        ),
        "",
        returncode=0,
        task_id="task-pass",
    )

    assert result.passed
    assert result.terminal_cause == "verifier-pass"
    assert result.container_digest == "sha256:abc"


def test_parse_harbor_output_missing_artifact_fixture() -> None:
    result = parse_harbor_output(
        json.dumps(
            {
                "task_id": "task-missing",
                "passed": False,
                "terminal_cause": "missing required artifact",
                "mechanism": "artifact-verifier",
                "verifier_output": "answer.txt missing",
            }
        ),
        "",
        returncode=1,
        task_id="task-missing",
    )

    assert not result.passed
    assert result.terminal_cause == "missing-artifact"
    assert result.mechanism == "artifact-verifier"


def test_parse_harbor_output_timeout_fixture_from_results_list() -> None:
    result = parse_harbor_output(
        json.dumps(
            {
                "results": [
                    {
                        "task_id": "task-timeout",
                        "passed": False,
                        "terminal_cause": "timeout",
                        "verifier_output": "timed out",
                    }
                ]
            }
        ),
        "",
        returncode=1,
        task_id="task-timeout",
    )

    assert not result.passed
    assert result.terminal_cause == "timeout"


def test_parse_harbor_output_falls_back_to_exit_code_for_plain_text() -> None:
    result = parse_harbor_output("plain output", "plain error", returncode=1, task_id="task-text")

    assert not result.passed
    assert result.terminal_cause == "verifier-fail"
    assert result.mechanism == "harbor-exit-code"
