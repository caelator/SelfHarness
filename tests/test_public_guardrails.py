from pathlib import Path

CLAIM_TERMS = ["reproduction", "reproduce", "reproduces", "reproduced"]
NEGATING_TERMS = ["not", "no ", "never", "without", "cannot", "may not", "does not"]


def test_public_text_does_not_claim_benchmark_reproduction() -> None:
    project = Path(__file__).resolve().parents[1]
    offenders: list[str] = []

    paths = [
        *sorted((project / "src" / "self_harness").rglob("*.py")),
        *sorted((project / "tests").rglob("*.py")),
        project / "README.md",
        project / "RELEASE.md",
    ]
    for path in paths:
        if path.name == Path(__file__).name:
            continue
        _collect_reproduction_claims(path, path.relative_to(project), offenders)

    assert offenders == []


def _collect_reproduction_claims(path: Path, label: Path, offenders: list[str]) -> None:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        lowered = line.lower()
        if "terminal" + "-bench" not in lowered:
            continue
        if "reproduction_claimed" in lowered:
            continue
        if not any(term in lowered for term in CLAIM_TERMS):
            continue
        if any(term in lowered for term in NEGATING_TERMS):
            continue
        offenders.append(f"{label}:{line_no}:{line.strip()}")
