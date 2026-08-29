"""Single physical finalizer for SU/MU and NewTx/ReTx grants."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np

from . import linkadapt as la

__all__ = [
    "CandidateGrant",
    "FinalGrant",
    "finalize_candidate_grant",
]


@dataclass(frozen=True)
class CandidateGrant:
    """All information visible before a grant becomes executable."""

    mode: str
    users: tuple[int, ...]
    rbg_indices: tuple[int, ...]
    ranks: tuple[int, ...]
    base_predicted_sinr_db: tuple[float, ...]
    receive_sinr_db: tuple[float, ...]
    corr_loss_db: tuple[float, ...]
    power_loss_db: float
    olla_mcs: tuple[float, ...]
    queue_bytes: tuple[int, ...]
    required_rbg: tuple[int, ...]
    fits_in_fullband: tuple[bool, ...]
    potential_fullband_bytes: tuple[int, ...]
    required_rbg_from_remaining_pool: tuple[int, ...] = ()
    fits_in_remaining_pool: tuple[bool, ...] = ()
    pair_correlation: float | None = None
    frozen_mcs: tuple[int | None, ...] = ()
    frozen_tbs_bytes: tuple[int | None, ...] = ()
    frozen_payload_bytes: tuple[int | None, ...] = ()
    explicit_newtx_mcs: tuple[int | None, ...] = ()
    candidate_score: float | None = None
    candidate_count: int = 0
    rejected_candidate_reasons: tuple[str, ...] = ()
    frequency_selection_score_gain: float = 0.0
    frequency_incremental_useful_bytes: int = 0
    frequency_evaluated_subsets: int = 0
    frequency_selected_source: str = "wideband_or_sequential"

    def __post_init__(self) -> None:
        n = len(self.users)
        aligned = (
            self.ranks,
            self.base_predicted_sinr_db,
            self.receive_sinr_db,
            self.corr_loss_db,
            self.olla_mcs,
            self.queue_bytes,
            self.required_rbg,
            self.fits_in_fullband,
            self.potential_fullband_bytes,
        )
        if n < 1 or any(len(values) != n for values in aligned):
            raise ValueError("candidate grant user fields must be non-empty and aligned")
        mode = str(self.mode).upper()
        if mode not in ("SU", "MU"):
            raise ValueError("candidate grant mode must be SU or MU")
        if (mode == "SU" and n != 1) or (mode == "MU" and n < 2):
            raise ValueError("candidate grant mode and user count are inconsistent")
        if (any(isinstance(value, (bool, np.bool_))
                or not isinstance(value, (int, np.integer))
                or int(value) < 0 for value in self.users)
                or len(set(int(value) for value in self.users)) != n):
            raise ValueError("candidate grant users must be unique non-negative integers")
        if not self.rbg_indices or len(set(self.rbg_indices)) != len(self.rbg_indices):
            raise ValueError("candidate grant needs a non-empty unique RBG bitmap")
        if any(isinstance(value, (bool, np.bool_))
               or not isinstance(value, (int, np.integer))
               or int(value) < 0 for value in self.rbg_indices):
            raise ValueError("candidate grant RBG indices must be non-negative integers")
        if any(isinstance(value, (bool, np.bool_))
               or not isinstance(value, (int, np.integer))
               or int(value) < 1 for value in self.ranks):
            raise ValueError("candidate grant ranks must be positive integers")
        for name, values in (
            ("base_predicted_sinr_db", self.base_predicted_sinr_db),
            ("receive_sinr_db", self.receive_sinr_db),
            ("corr_loss_db", self.corr_loss_db),
            ("olla_mcs", self.olla_mcs),
        ):
            if not all(np.isfinite(float(value)) for value in values):
                raise ValueError(f"{name} must contain only finite values")
        if not np.isfinite(float(self.power_loss_db)):
            raise ValueError("power_loss_db must be finite")
        for name, values in (
            ("queue_bytes", self.queue_bytes),
            ("required_rbg", self.required_rbg),
            ("potential_fullband_bytes", self.potential_fullband_bytes),
        ):
            if any(isinstance(value, (bool, np.bool_))
                   or not isinstance(value, (int, np.integer))
                   or int(value) < 0 for value in values):
                raise ValueError(f"{name} must contain non-negative integers")
        if any(not isinstance(value, (bool, np.bool_))
               for value in self.fits_in_fullband):
            raise ValueError("fits_in_fullband must contain booleans")
        for name, values in (
            ("required_rbg_from_remaining_pool",
             self.required_rbg_from_remaining_pool),
        ):
            if values and (
                    len(values) != n
                    or any(isinstance(value, (bool, np.bool_))
                           or not isinstance(value, (int, np.integer))
                           or int(value) < 0 for value in values)):
                raise ValueError(
                    f"{name} must be empty or contain aligned non-negative integers")
        if self.fits_in_remaining_pool and (
                len(self.fits_in_remaining_pool) != n
                or any(not isinstance(value, (bool, np.bool_))
                       for value in self.fits_in_remaining_pool)):
            raise ValueError(
                "fits_in_remaining_pool must be empty or contain aligned booleans")
        if self.pair_correlation is not None and not np.isfinite(
                float(self.pair_correlation)):
            raise ValueError("pair_correlation must be finite when provided")
        for name, values in (
            ("frozen_mcs", self.frozen_mcs),
            ("frozen_tbs_bytes", self.frozen_tbs_bytes),
            ("frozen_payload_bytes", self.frozen_payload_bytes),
            ("explicit_newtx_mcs", self.explicit_newtx_mcs),
        ):
            if values and len(values) != n:
                raise ValueError(f"{name} must be empty or aligned with users")
            if values and any(
                    value is not None
                    and (isinstance(value, (bool, np.bool_))
                         or not isinstance(value, (int, np.integer))
                         or int(value) < 0)
                    for value in values):
                raise ValueError(
                    f"{name} must contain non-negative integers or None")
        if (isinstance(self.candidate_count, (bool, np.bool_))
                or not isinstance(self.candidate_count, (int, np.integer))
                or int(self.candidate_count) < 0):
            raise ValueError("candidate_count must be a non-negative integer")
        if (isinstance(self.frequency_evaluated_subsets, (bool, np.bool_))
                or not isinstance(
                    self.frequency_evaluated_subsets, (int, np.integer))
                or int(self.frequency_evaluated_subsets) < 0):
            raise ValueError(
                "frequency_evaluated_subsets must be a non-negative integer")
        if (isinstance(self.frequency_incremental_useful_bytes, (bool, np.bool_))
                or not isinstance(
                    self.frequency_incremental_useful_bytes, (int, np.integer))
                or int(self.frequency_incremental_useful_bytes) < 0):
            raise ValueError(
                "frequency_incremental_useful_bytes must be a non-negative integer")
        for name, value in (
            ("frequency_selection_score_gain",
             self.frequency_selection_score_gain),
            ("candidate_score", self.candidate_score),
        ):
            if value is not None and not np.isfinite(float(value)):
                raise ValueError(f"{name} must be finite when provided")


@dataclass(frozen=True)
class FinalGrant:
    """Immutable, executable grant.  No MCS/TBS calculation remains downstream."""

    mode: str
    users: tuple[int, ...]
    rbg_indices: tuple[int, ...]
    n_rbg: int
    ranks: tuple[int, ...]
    mcs: tuple[int, ...]
    base_tx_sinr_db: tuple[float, ...]
    mcs_input_sinr_db: tuple[float, ...]
    mcs_without_olla: tuple[int, ...]
    true_sinr_db: tuple[float, ...]
    corr_loss_db: tuple[float, ...]
    power_loss_db: float
    required_rbg: tuple[int, ...]
    fits_in_fullband: tuple[bool, ...]
    tbs_bytes: tuple[int, ...]
    useful_bytes: tuple[int, ...]
    potential_fullband_bytes: tuple[int, ...]
    required_rbg_from_remaining_pool: tuple[int, ...]
    fits_in_remaining_pool: tuple[bool, ...]
    pair_correlation: float | None = None
    is_retransmission: tuple[bool, ...] = ()
    reservation_id: str | None = None
    candidate_score: float | None = None
    candidate_count: int = 0
    rejected_candidate_reasons: tuple[str, ...] = ()
    frequency_selection_score_gain: float = 0.0
    frequency_incremental_useful_bytes: int = 0
    frequency_evaluated_subsets: int = 0
    frequency_selected_source: str = "wideband_or_sequential"
    one_codeword_per_user: bool = True
    finalizer_version: str = "grant-finalizer-v1"

    def with_reservation(self, reservation_id: str) -> FinalGrant:
        return replace(self, reservation_id=str(reservation_id))

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in (
            "users", "rbg_indices", "ranks", "mcs", "base_tx_sinr_db",
            "mcs_input_sinr_db", "mcs_without_olla", "true_sinr_db",
            "corr_loss_db", "required_rbg", "fits_in_fullband", "tbs_bytes",
            "useful_bytes", "potential_fullband_bytes", "is_retransmission",
            "required_rbg_from_remaining_pool", "fits_in_remaining_pool",
            "rejected_candidate_reasons",
        ):
            out[key] = list(out[key])
        return out


def _optional(values: tuple[Any, ...], index: int) -> Any | None:
    return values[index] if values else None


def finalize_candidate_grant(
    candidate: CandidateGrant,
    *,
    lookup: Any,
    slot: str,
    olla_enabled: bool = True,
) -> FinalGrant:
    """Resolve MCS/TBS/useful bytes once for every grant path.

    NewTx follows ``CQI/BF base + CorrLoss + powerLoss -> MCS -> OLLA``.
    ReTx preserves its frozen MCS/rank/RBG-count/TBS identity and verifies that
    the current bitmap reproduces the same TBS.
    """
    if str(slot) not in ("D", "S"):
        raise ValueError("slot must be D or S")
    table_id = int(lookup.mcs_table)
    target_bler = float(lookup.target_bler)
    if table_id not in la.MCS_TABLES:
        raise ValueError(f"unknown MCS table {table_id}")
    if not np.isfinite(target_bler) or not 0.0 < target_bler < 1.0:
        raise ValueError("lookup.target_bler must be in (0,1)")

    final_mcs: list[int] = []
    no_olla_mcs: list[int] = []
    mcs_inputs: list[float] = []
    tbs_values: list[int] = []
    useful_values: list[int] = []
    retx_flags: list[bool] = []
    for side, _user in enumerate(candidate.users):
        base = float(candidate.base_predicted_sinr_db[side])
        corr = float(candidate.corr_loss_db[side])
        power = float(candidate.power_loss_db) if str(candidate.mode).upper() == "MU" else 0.0
        mcs_input = base + corr + power
        no_olla = int(la.select_mcs(
            mcs_input, table=table_id, target_bler=target_bler).index)
        frozen_mcs = _optional(candidate.frozen_mcs, side)
        frozen_tbs = _optional(candidate.frozen_tbs_bytes, side)
        frozen_payload = _optional(candidate.frozen_payload_bytes, side)
        explicit_newtx_mcs = _optional(candidate.explicit_newtx_mcs, side)
        is_retx = frozen_mcs is not None or frozen_tbs is not None or frozen_payload is not None
        if is_retx and (frozen_mcs is None or frozen_tbs is None or frozen_payload is None):
            raise ValueError("retransmission must freeze MCS, TBS and payload together")
        if is_retx:
            mcs = int(frozen_mcs)
        elif explicit_newtx_mcs is not None:
            mcs = int(explicit_newtx_mcs)
        elif olla_enabled:
            mcs = int(la.apply_olla_mcs(
                no_olla, float(candidate.olla_mcs[side]),
                mcs_table=table_id)["final_mcs"])
        else:
            mcs = no_olla
        tbs = int(lookup.tbs_bytes_for_indices(
            slot, mcs, int(candidate.ranks[side]), candidate.rbg_indices))
        if is_retx and tbs != int(frozen_tbs):
            raise RuntimeError(
                "HARQ retransmission did not reproduce frozen TBS: "
                f"actual={tbs}, expected={int(frozen_tbs)}"
            )
        queue = int(candidate.queue_bytes[side])
        if queue < 0:
            raise ValueError("queue_bytes must be non-negative")
        if is_retx:
            if queue < int(frozen_payload):
                raise RuntimeError(
                    f"HARQ queue {queue} B is smaller than frozen payload {int(frozen_payload)} B"
                )
            useful = int(frozen_payload)
        else:
            useful = min(queue, tbs)
        final_mcs.append(mcs)
        no_olla_mcs.append(no_olla)
        mcs_inputs.append(mcs_input)
        tbs_values.append(tbs)
        useful_values.append(useful)
        retx_flags.append(is_retx)

    return FinalGrant(
        mode=str(candidate.mode).upper(),
        users=tuple(int(value) for value in candidate.users),
        rbg_indices=tuple(int(value) for value in candidate.rbg_indices),
        n_rbg=len(candidate.rbg_indices),
        ranks=tuple(int(value) for value in candidate.ranks),
        mcs=tuple(final_mcs),
        base_tx_sinr_db=tuple(float(value) for value in candidate.base_predicted_sinr_db),
        mcs_input_sinr_db=tuple(mcs_inputs),
        mcs_without_olla=tuple(no_olla_mcs),
        true_sinr_db=tuple(float(value) for value in candidate.receive_sinr_db),
        corr_loss_db=tuple(float(value) for value in candidate.corr_loss_db),
        power_loss_db=float(candidate.power_loss_db),
        required_rbg=tuple(int(value) for value in candidate.required_rbg),
        fits_in_fullband=tuple(bool(value) for value in candidate.fits_in_fullband),
        tbs_bytes=tuple(tbs_values),
        useful_bytes=tuple(useful_values),
        potential_fullband_bytes=tuple(
            int(value) for value in candidate.potential_fullband_bytes),
        required_rbg_from_remaining_pool=tuple(
            int(value) for value in (
                candidate.required_rbg_from_remaining_pool
                or candidate.required_rbg)),
        fits_in_remaining_pool=tuple(
            bool(value) for value in (
                candidate.fits_in_remaining_pool
                or candidate.fits_in_fullband)),
        pair_correlation=(
            None if candidate.pair_correlation is None
            else float(candidate.pair_correlation)),
        is_retransmission=tuple(retx_flags),
        candidate_score=(
            None if candidate.candidate_score is None
            else float(candidate.candidate_score)),
        candidate_count=int(candidate.candidate_count),
        rejected_candidate_reasons=tuple(candidate.rejected_candidate_reasons),
        frequency_selection_score_gain=float(
            candidate.frequency_selection_score_gain),
        frequency_incremental_useful_bytes=int(
            candidate.frequency_incremental_useful_bytes),
        frequency_evaluated_subsets=int(candidate.frequency_evaluated_subsets),
        frequency_selected_source=str(candidate.frequency_selected_source),
    )
