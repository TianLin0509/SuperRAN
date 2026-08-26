"""RBG subset selection decoupled from RB power control."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

__all__ = [
    "FrequencySelection",
    "rotated_order",
    "quality_order",
    "select_frequency_subset",
]


@dataclass(frozen=True)
class FrequencySelection:
    selected_indices: tuple[int, ...]
    selected_grant: Any
    baseline_indices: tuple[int, ...]
    baseline_useful_bytes: int
    selected_useful_bytes: int
    incremental_useful_bytes: int
    selection_score_gain: float
    evaluated_subset_count: int
    selected_source: str
    policy: str = "best-quality-prefix-with-sequential-safety-net-v1"

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["selected_indices"] = list(self.selected_indices)
        out["baseline_indices"] = list(self.baseline_indices)
        # The finalized grant has its own serializer and must not be recursively
        # expanded into a KPI row here.
        out["selected_grant"] = None
        return out


def rotated_order(
    indices: Sequence[int], *, cursor: int, total_rbg: int | None = None
) -> tuple[int, ...]:
    values = tuple(int(value) for value in indices)
    if len(set(values)) != len(values):
        raise ValueError("available RBG indices must be unique")
    if not values:
        return ()
    total_hint = (
        int(total_rbg) if total_rbg is not None
        else max(max(values) + 1, int(cursor) + 1)
    )
    if total_hint < 1 or any(value >= total_hint for value in values):
        raise ValueError("total_rbg must cover every available RBG")
    return tuple(sorted(
        values, key=lambda value: ((value - int(cursor)) % total_hint)))


def quality_order(
    indices: Sequence[int],
    per_rbg_score: Sequence[float],
    *,
    cursor: int,
) -> tuple[int, ...]:
    values = tuple(int(value) for value in indices)
    scores = np.asarray(per_rbg_score, dtype=float)
    if scores.ndim != 1 or any(value < 0 or value >= scores.size for value in values):
        raise ValueError("per_rbg_score must cover every available RBG")
    if not np.isfinite(scores[np.asarray(values, dtype=int)]).all():
        raise ValueError("per_rbg_score must be finite on available RBGs")
    baseline = rotated_order(values, cursor=cursor, total_rbg=int(scores.size))
    tie_rank = {value: rank for rank, value in enumerate(baseline)}
    return tuple(sorted(values, key=lambda value: (-scores[value], tie_rank[value])))


def _useful(grant: Any) -> int:
    values = (
        grant.get("useful_bytes", ())
        if isinstance(grant, dict) else grant.useful_bytes
    )
    if isinstance(values, (int, np.integer)):
        return int(values)
    return int(sum(int(value) for value in values))


def select_frequency_subset(
    available_indices: Sequence[int],
    per_rbg_score: Sequence[float],
    *,
    cursor: int,
    evaluate: Callable[[tuple[int, ...]], Any],
    sufficient: Callable[[Any], bool],
) -> FrequencySelection:
    """Choose the smallest sufficient prefix, or the most useful prefix.

    Both quality-sorted and sequential prefixes are evaluated.  The latter is
    a safety net: enabling frequency selection cannot reduce the gNB-predicted
    useful bytes relative to the old sequential allocation for this grant.
    """
    available = tuple(int(value) for value in available_indices)
    if not available:
        raise ValueError("available_indices cannot be empty")
    scores = np.asarray(per_rbg_score, dtype=float)
    sequential = rotated_order(
        available, cursor=cursor, total_rbg=int(scores.size))
    quality = quality_order(available, scores, cursor=cursor)
    candidates: list[tuple[tuple[int, ...], str, Any]] = []
    seen: set[frozenset[int]] = set()
    # Keep sequential rows even when both orders are identical; they are the
    # explicit counterfactual used by the no-regression safety check.
    for source, order in (("sequential", sequential), ("quality", quality)):
        for count in range(1, len(order) + 1):
            indices = tuple(order[:count])
            key = frozenset(indices)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((indices, source, evaluate(indices)))

    sufficient_rows = [row for row in candidates if bool(sufficient(row[2]))]

    def _mean_score(indices: tuple[int, ...]) -> float:
        return float(np.mean(scores[np.asarray(indices, dtype=int)]))

    if sufficient_rows:
        # Queue-limited objective: once the requested payload fits, fewer RBGs
        # leaves more resources for the next PF user.
        chosen = max(
            sufficient_rows,
            key=lambda row: (-len(row[0]), _useful(row[2]), _mean_score(row[0]),
                             row[1] == "quality"),
        )
    else:
        chosen = max(
            candidates,
            key=lambda row: (_useful(row[2]), -len(row[0]), _mean_score(row[0]),
                             row[1] == "quality"),
        )

    sequential_rows = [row for row in candidates if row[1] == "sequential"]
    if sufficient_rows:
        sequential_sufficient = [row for row in sequential_rows if bool(sufficient(row[2]))]
    else:
        sequential_sufficient = []
    if sequential_sufficient:
        baseline = max(
            sequential_sufficient,
            key=lambda row: (-len(row[0]), _useful(row[2]), _mean_score(row[0])),
        )
    else:
        baseline = max(
            sequential_rows,
            key=lambda row: (_useful(row[2]), -len(row[0]), _mean_score(row[0])),
        )
    selected_indices, selected_source, selected_grant = chosen
    baseline_indices, _baseline_source, baseline_grant = baseline
    selected_useful = _useful(selected_grant)
    baseline_useful = _useful(baseline_grant)
    return FrequencySelection(
        selected_indices=selected_indices,
        selected_grant=selected_grant,
        baseline_indices=baseline_indices,
        baseline_useful_bytes=baseline_useful,
        selected_useful_bytes=selected_useful,
        incremental_useful_bytes=selected_useful - baseline_useful,
        selection_score_gain=(
            _mean_score(selected_indices) - float(np.mean(scores[np.asarray(available)]))),
        evaluated_subset_count=len(candidates),
        selected_source=selected_source,
    )
