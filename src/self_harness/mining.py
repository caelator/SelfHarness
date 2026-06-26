from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from self_harness.proposer_policy import is_addressable
from self_harness.types import FailurePattern, FailureSignature, RunRecord, Split


def signature_of(run: RunRecord) -> FailureSignature:
    return FailureSignature(
        terminal_cause=run.outcome.terminal_cause,
        causal_status=run.outcome.causal_status,
        mechanism=run.outcome.mechanism,
    )


def cluster_failures(
    records: list[RunRecord],
    split: Split | None = None,
    editable_surfaces: Iterable[str] | None = None,
) -> list[FailurePattern]:
    groups: dict[tuple[Split, str], list[RunRecord]] = defaultdict(list)
    for record in records:
        if record.passed:
            continue
        if split is not None and record.split != split:
            continue
        signature = signature_of(record)
        groups[(record.split, signature.key)].append(record)

    patterns: list[FailurePattern] = []
    for (record_split, _key), grouped in groups.items():
        first = grouped[0]
        signature = signature_of(first)
        task_ids = sorted(record.task_id for record in grouped)
        symptoms = sorted({event.message for record in grouped for event in record.trace})[:5]
        evidence = sorted({record.outcome.message for record in grouped})[:5]
        patterns.append(
            FailurePattern(
                id=f"{record_split.value}__{signature.stable_id}",
                split=record_split,
                signature=signature,
                support=len(grouped),
                task_ids=task_ids,
                symptoms=symptoms,
                verifier_evidence=evidence,
            )
        )

    # Paper §3.2: clusters are "ordered by their support and estimated actionability, so that
    # the proposer is exposed first to recurring mechanisms that are more likely to map to a
    # high-value harness modification." When the editable surfaces are known, a cluster whose
    # mechanism maps to an addressable surface is ranked ahead of an equally-supported one that
    # does not; the remaining keys keep the order deterministic.
    surfaces = None if editable_surfaces is None else list(editable_surfaces)

    def order_key(item: FailurePattern) -> tuple[int, int, str, str]:
        actionable = 0 if surfaces is None else (0 if is_addressable(item, surfaces) else 1)
        return (-item.support, actionable, item.signature.key, item.id)

    return sorted(patterns, key=order_key)

