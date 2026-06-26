from pathlib import Path

from self_harness.adapters.local_subprocess import LocalSubprocessRunner
from self_harness.config import EngineConfig
from self_harness.engine import SelfHarnessEngine
from self_harness.proposer import HeuristicProposer
from self_harness.types import Split, Task


def test_engine_artifacts_are_deterministic_with_local_subprocess_runner(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    _run(first)
    _run(second)

    assert _tree_bytes(first) == _tree_bytes(second)


def _run(out_dir: Path) -> None:
    engine = SelfHarnessEngine(
        tasks=[
            _task("held-in-pass", Split.HELD_IN),
            _task("held-out-pass", Split.HELD_OUT),
        ],
        runner=LocalSubprocessRunner(),
        proposer=HeuristicProposer(),
        out_dir=out_dir,
        config=EngineConfig(rounds=1, evaluation_repeats=2),
    )
    engine.run()


def _task(id_: str, split: Split) -> Task:
    return Task(
        id=id_,
        split=split,
        failure_mode="local_subprocess",
        description=id_,
        metadata={
            "solve_command": "printf ok > answer.txt",
            "verify_command": "test -f answer.txt",
        },
    )


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }
