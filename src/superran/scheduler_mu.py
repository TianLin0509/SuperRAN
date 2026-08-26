"""Full two-user MU candidate enumeration and deterministic scoring."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

__all__ = [
    "MuCandidateEvaluation",
    "MuCandidateDecision",
    "choose_mu_candidate",
    "summarize_mu_audits",
]


@dataclass(frozen=True)
class MuCandidateEvaluation:
    anchor_ue: int
    partner_ue: int
    pf_order: int
    feasible: bool
    rejection_reason: str | None
    correlation: float | None
    predicted_bler_max: float | None
    useful_bytes: int
    used_rbg: int
    useful_bytes_per_rbg: float
    final_mcs: tuple[int, ...]
    grant: Any = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["final_mcs"] = list(self.final_mcs)
        out["grant"] = None
        return out


@dataclass(frozen=True)
class MuCandidateDecision:
    anchor_ue: int
    selected_partner_ue: int | None
    selected_score: float | None
    selected_grant: Any
    evaluations: tuple[MuCandidateEvaluation, ...]

    @property
    def feasible_count(self) -> int:
        return sum(item.feasible for item in self.evaluations)

    @property
    def rejection_reasons(self) -> tuple[str, ...]:
        return tuple(
            item.rejection_reason
            for item in self.evaluations
            if item.rejection_reason is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchor_ue": int(self.anchor_ue),
            "selected_partner_ue": self.selected_partner_ue,
            "selected_score": self.selected_score,
            "candidate_count": len(self.evaluations),
            "feasible_count": self.feasible_count,
            "rejection_reasons": list(self.rejection_reasons),
            "evaluations": [item.as_dict() for item in self.evaluations],
        }


def choose_mu_candidate(
    anchor_ue: int,
    evaluations: Iterable[MuCandidateEvaluation],
) -> MuCandidateDecision:
    """Choose useful-byte density, then useful bytes, then lower correlation.

    PF has already selected the anchor and ordered partners.  The scorer does
    not replace PF; it prevents the first merely feasible partner from winning
    when a later candidate delivers more queue-limited useful bytes per RBG.
    """
    rows = tuple(evaluations)
    if any(int(item.anchor_ue) != int(anchor_ue) for item in rows):
        raise ValueError("all MU evaluations must belong to the same anchor")
    feasible = [
        item for item in rows
        if item.feasible and item.grant is not None and item.used_rbg > 0
        and np.isfinite(item.useful_bytes_per_rbg)
    ]
    if not feasible:
        return MuCandidateDecision(
            anchor_ue=int(anchor_ue), selected_partner_ue=None,
            selected_score=None, selected_grant=None, evaluations=rows)

    selected = max(
        feasible,
        key=lambda item: (
            float(item.useful_bytes_per_rbg),
            int(item.useful_bytes),
            -(float(item.correlation) if item.correlation is not None else 1.0),
            -int(item.pf_order),
            -int(item.partner_ue),
        ),
    )
    return MuCandidateDecision(
        anchor_ue=int(anchor_ue),
        selected_partner_ue=int(selected.partner_ue),
        selected_score=float(selected.useful_bytes_per_rbg),
        selected_grant=selected.grant,
        evaluations=rows,
    )


def summarize_mu_audits(
    decisions: Iterable[MuCandidateDecision],
) -> dict[str, Any]:
    rows = list(decisions)
    evaluations = [item for row in rows for item in row.evaluations]
    reasons: dict[str, int] = {}
    for item in evaluations:
        if item.rejection_reason is not None:
            reasons[item.rejection_reason] = reasons.get(item.rejection_reason, 0) + 1
    scores = [
        float(row.selected_score) for row in rows
        if row.selected_score is not None and np.isfinite(row.selected_score)
    ]
    return {
        "anchor_decisions": len(rows),
        "candidate_count": len(evaluations),
        "feasible_count": sum(item.feasible for item in evaluations),
        "selected_count": sum(row.selected_partner_ue is not None for row in rows),
        "selected_score_mean_useful_bytes_per_rbg": (
            float(np.mean(scores)) if scores else None),
        "rejection_reasons": reasons,
        "objective": (
            "PF anchor fixed; maximize queue-limited useful bytes per physical RBG; "
            "tie by useful bytes, lower correlation, earlier PF partner"
        ),
    }
