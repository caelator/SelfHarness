from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from self_harness.types import stable_json_dumps


def source_hash(value: Any) -> str:
    return hashlib.sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
