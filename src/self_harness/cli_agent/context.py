"""Expand ``@path`` mentions in a user line into inlined file context, confined to the workdir.

Typing ``explain @src/foo.py`` inlines that file's contents into the turn so GLM sees it without a
round-trip read. Only paths that resolve *inside* the working directory are inlined (reusing the same
confinement guard as the file tools); anything that escapes, is missing, or is not a regular file is left
as literal text. Tab-completion is out of scope — this is plain ``@path`` substitution.
"""

from __future__ import annotations

import re
from pathlib import Path

from self_harness.adapters.agentic.tools import _resolve_in_workdir

# A mention is `@` followed by a path-ish token. Stops at whitespace; trailing sentence punctuation
# (.,;:!?) is trimmed so "see @README.md." resolves to README.md, not "README.md.".
_MENTION = re.compile(r"@([^\s]+)")
_MAX_FILE_BYTES = 16_000  # per-file cap so a huge file can't blow up the prompt.


def expand_mentions(line: str, workdir: Path) -> tuple[str, list[str]]:
    """Return ``(augmented_line, inlined_paths)``.

    ``augmented_line`` is the original line followed by a fenced block per resolved file. ``inlined_paths``
    lists the workdir-relative paths that were inlined (for UI feedback). If nothing resolves, the line is
    returned unchanged with an empty list.
    """

    seen: dict[str, Path] = {}
    for match in _MENTION.finditer(line):
        token = match.group(1).rstrip(".,;:!?)")
        if not token or token in seen:
            continue
        resolved = _resolve_in_workdir(token, workdir)
        if resolved is None or not resolved.is_file():
            continue
        seen[token] = resolved

    if not seen:
        return line, []

    blocks: list[str] = []
    inlined: list[str] = []
    for token, path in seen.items():
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        truncated = raw[:_MAX_FILE_BYTES]
        note = "" if len(raw) <= _MAX_FILE_BYTES else f"\n... (truncated, {len(raw)} bytes total)"
        blocks.append(f"Contents of {token}:\n```\n{truncated}{note}\n```")
        inlined.append(token)

    if not blocks:
        return line, []
    return line + "\n\n" + "\n\n".join(blocks), inlined
