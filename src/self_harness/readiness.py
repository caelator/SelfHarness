from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path

SCHEMA_HEADING_RE = re.compile(r"^## (?P<version>\d+\.\d+)$")


def audit_tree_hash(root: Path) -> str:
    """Return a stable hash over relative audit paths and file bytes."""

    root = Path(root)
    hasher = sha256()
    audit_files = (item for item in root.rglob("*") if item.is_file())
    for path in sorted(audit_files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def schema_versions_from_changelog(path: Path) -> set[str]:
    versions: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = SCHEMA_HEADING_RE.match(line.strip())
        if match:
            versions.add(match.group("version"))
    return versions
