from __future__ import annotations

from typing import Protocol

from self_harness.corpus import TaskCorpus
from self_harness.evaluation import Runner
from self_harness.types import Task


class TaskAdapter(Protocol):
    """Boundary for loading an external task corpus and providing its runner."""

    def load(self, corpus: TaskCorpus) -> list[Task]:
        ...

    def runner(self) -> Runner:
        ...
