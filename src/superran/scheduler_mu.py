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
    "validate_pair_graph",
]


def _finite_array(value: Any, shape: tuple[int, ...], name: str) -> None:
    array = np.asarray(value)
    if tuple(int(x) for x in array.shape) != tuple(int(x) for x in shape):
        raise ValueError(
            f"MU pair {name} 维度不一致：期望 {shape}，收到 {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"MU pair {name} 含 NaN/Inf")


def validate_pair_graph(tables: Iterable[Any]) -> dict[str, Any]:
    """Hard-check a complete, symmetric and dimensionally consistent pair graph.

    A length-only check is insufficient for three or more UEs: every UE may have
    at least one neighbour while edge ``1<->2`` is still missing.  The scheduler
    indexes links by table position, so the required graph is the complete graph
    over ``0..N-1`` and both directions must point to the same pair object.
    """
    rows = tuple(tables)
    n_ue = len(rows)
    if n_ue < 2:
        raise ValueError("MU pair graph 至少需要 2 个 UE")
    try:
        n_snap = int(np.asarray(rows[0].sinr_db).shape[0])
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("MU pair graph 缺少有效的 UE 链路表") from exc
    if n_snap < 1:
        raise ValueError("MU pair graph 的 snapshot 数必须至少为 1")

    expected_nodes = set(range(n_ue))
    for i, table in enumerate(rows):
        if int(getattr(table, "ue", -1)) != i:
            raise ValueError(
                f"MU pair graph 的表位置 {i} 与 table.ue={getattr(table, 'ue', None)!r} 不一致")
        sinr = np.asarray(getattr(table, "sinr_db", ()))
        if sinr.ndim != 2 or int(sinr.shape[0]) != n_snap:
            raise ValueError(
                f"MU pair graph 的 UE {i} sinr_db 维度不一致："
                f"期望 [snapshot,rank] 且 snapshot={n_snap}，收到 {sinr.shape}")
        links = getattr(table, "mu_links", None)
        if not isinstance(links, dict):
            raise ValueError(f"MU pair graph 的 UE {i} 缺少 mu_links 字典")
        if any(isinstance(key, (bool, np.bool_))
               or not isinstance(key, (int, np.integer)) for key in links):
            raise ValueError(f"MU pair graph 的 UE {i} 含非整数 partner key")
        actual = {int(key) for key in links}
        wanted = expected_nodes - {i}
        if actual != wanted:
            missing = sorted(wanted - actual)
            extra = sorted(actual - wanted)
            raise ValueError(
                f"MU pair graph 不完整：UE {i} 缺边 {missing}、非法边 {extra}；"
                "必须包含每一条双向 UE pair")

    checked = 0
    frequency_rbg: int | None = None
    vector_fields = ("correlation", "leakage_ratio", "predicted_leakage_ratio")
    matrix_fields = (
        "true_sinr_db", "predicted_sinr_db", "corr_loss_tx_db",
        "corr_loss_true_db",
    )
    rbg_fields = (
        "true_sinr_rbg_db", "predicted_sinr_rbg_db",
        "corr_loss_tx_rbg_db", "corr_loss_true_rbg_db",
    )
    for i in range(n_ue):
        for j in range(i + 1, n_ue):
            link = rows[i].mu_links[j]
            reverse = rows[j].mu_links[i]
            if reverse is not link:
                raise ValueError(
                    f"MU pair graph 不对称：{i}->{j} 与 {j}->{i} 不是同一 pair")
            users = tuple(int(x) for x in getattr(link, "users", ()))
            if len(users) != 2 or set(users) != {i, j}:
                raise ValueError(
                    f"MU pair graph 身份错配：边 {i}<->{j} 声明 users={users}")
            rank = int(getattr(link, "rank_per_user", 0))
            if rank < 1 or any(int(np.asarray(rows[u].sinr_db).shape[1]) < rank
                               for u in (i, j)):
                raise ValueError(
                    f"MU pair {i}<->{j} 的 rank_per_user={rank} 超出 SU 链路表")
            for name in matrix_fields:
                _finite_array(getattr(link, name, None), (n_snap, 2),
                              f"{i}<->{j}.{name}")
            for name in vector_fields:
                _finite_array(getattr(link, name, None), (n_snap,),
                              f"{i}<->{j}.{name}")
            if not np.isfinite(float(getattr(link, "power_loss_db", np.nan))):
                raise ValueError(f"MU pair {i}<->{j}.power_loss_db 非有限")
            optional = [getattr(link, name, None) for name in rbg_fields]
            present = [value is not None for value in optional]
            if any(present) and not all(present):
                raise ValueError(
                    f"MU pair {i}<->{j} 的逐 RBG 字段只提供了一部分")
            if all(present):
                pair_rbg = int(np.asarray(optional[0]).shape[-1])
                if pair_rbg < 1:
                    raise ValueError(f"MU pair {i}<->{j} 的 RBG 维度必须至少为 1")
                if frequency_rbg is None:
                    frequency_rbg = pair_rbg
                elif pair_rbg != frequency_rbg:
                    raise ValueError(
                        f"MU pair {i}<->{j} 的 RBG 数 {pair_rbg} 与图中 "
                        f"{frequency_rbg} 不一致")
                for name, value in zip(rbg_fields, optional, strict=True):
                    _finite_array(value, (n_snap, 2, pair_rbg),
                                  f"{i}<->{j}.{name}")
                for user in (i, j):
                    su_rbg = getattr(rows[user], "sinr_rbg_db", None)
                    if su_rbg is not None and (
                            np.asarray(su_rbg).ndim != 3
                            or int(np.asarray(su_rbg).shape[0]) != n_snap
                            or int(np.asarray(su_rbg).shape[-1]) != pair_rbg):
                        raise ValueError(
                            f"MU pair {i}<->{j} 的 RBG 维度与 UE {user} "
                            f"SU 链路表 {np.asarray(su_rbg).shape} 不一致")
            checked += 1
    return {
        "status": "pass",
        "users": int(n_ue),
        "pairs": int(checked),
        "expected_pairs": int(n_ue * (n_ue - 1) // 2),
        "snapshots": int(n_snap),
        "rbg": frequency_rbg,
        "contract": "complete symmetric pair graph with consistent dimensions",
    }


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
