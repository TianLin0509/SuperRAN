"""Transactional physical/logical resource ledger for one scheduling TTI.

PDCCH/CCE is intentionally absent from this first P0 contract.  The ledger
keeps the resources that are already trustworthy in the simulator:

* physical RBG/PRB occupancy (an MU grant consumes a shared RBG once);
* spatial layers on every occupied RBG;
* logical layer-PRB work (MU rank-2 + rank-2 consumes four layer-PRBs per PRB);
* reserve / commit / rollback semantics with no queue or RNG side effects.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

__all__ = [
    "ResourceBudget",
    "ResourceReservation",
    "ResourceRejection",
    "ResourceLedgerSnapshot",
    "ResourceAdmission",
    "ResourceLimitError",
    "ResourceLedger",
]


def _integer(name: str, value: int, minimum: int = 0) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
            value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    out = int(value)
    if out < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return out


@dataclass(frozen=True)
class ResourceBudget:
    num_rbg: int
    rbg_prb_sizes: tuple[int, ...]
    max_layers_per_rbg: int = 4
    max_logical_prb: int | None = None

    def __post_init__(self) -> None:
        n = _integer("num_rbg", self.num_rbg, 1)
        sizes = tuple(self.rbg_prb_sizes)
        if len(sizes) != n or any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer))
            or int(value) < 1
            for value in sizes
        ):
            raise ValueError("rbg_prb_sizes must contain num_rbg positive integers")
        _integer("max_layers_per_rbg", self.max_layers_per_rbg, 1)
        if self.max_logical_prb is not None:
            _integer("max_logical_prb", self.max_logical_prb, 1)

    @property
    def total_prb(self) -> int:
        return int(sum(int(value) for value in self.rbg_prb_sizes))

    @property
    def resolved_max_logical_prb(self) -> int:
        return (
            int(self.max_logical_prb)
            if self.max_logical_prb is not None
            else self.total_prb * int(self.max_layers_per_rbg)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "num_rbg": int(self.num_rbg),
            "rbg_prb_sizes": [int(value) for value in self.rbg_prb_sizes],
            "total_prb": self.total_prb,
            "max_layers_per_rbg": int(self.max_layers_per_rbg),
            "max_logical_prb": self.resolved_max_logical_prb,
            "pdcch_cce": "not_modelled_by_explicit_scope",
            "max_grants": None,
            "max_scheduled_ues": None,
        }


@dataclass(frozen=True)
class ResourceReservation:
    reservation_id: str
    grant_index: int
    mode: str
    users: tuple[int, ...]
    ranks: tuple[int, ...]
    rbg_indices: tuple[int, ...]
    physical_prb: int
    layers_per_rbg: int
    logical_prb: int
    status: str = "pending"

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["users"] = list(self.users)
        out["ranks"] = list(self.ranks)
        out["rbg_indices"] = list(self.rbg_indices)
        return out


@dataclass(frozen=True)
class ResourceRejection:
    grant_index: int
    reason: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceLedgerSnapshot:
    used_rbg_indices: tuple[int, ...]
    used_physical_prb: int
    used_logical_prb: int
    max_layers_used: int
    scheduled_users: tuple[int, ...]
    reservation_count: int
    committed_count: int
    budget: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["used_rbg_indices"] = list(self.used_rbg_indices)
        out["scheduled_users"] = list(self.scheduled_users)
        return out


@dataclass(frozen=True)
class ResourceAdmission:
    accepted_grant_indices: tuple[int, ...]
    reservation_ids: tuple[str, ...]
    rejections: tuple[ResourceRejection, ...]
    snapshot: ResourceLedgerSnapshot

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted_grant_indices": list(self.accepted_grant_indices),
            "reservation_ids": list(self.reservation_ids),
            "rejections": [item.as_dict() for item in self.rejections],
            "snapshot": self.snapshot.as_dict(),
        }


class ResourceLimitError(RuntimeError):
    def __init__(self, reason: str, detail: str, *, structural: bool = False) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = str(reason)
        self.detail = str(detail)
        self.structural = bool(structural)


class ResourceLedger:
    """One-TTI resource account with deterministic transaction IDs."""

    def __init__(self, budget: ResourceBudget, *, tti: int = 0) -> None:
        if not isinstance(budget, ResourceBudget):
            raise ValueError("budget must be ResourceBudget")
        self.budget = budget
        self.tti = _integer("tti", tti)
        self._counter = 0
        self._reservations: dict[str, ResourceReservation] = {}

    @property
    def reservations(self) -> tuple[ResourceReservation, ...]:
        return tuple(self._reservations.values())

    def _usage_without(self, reservation_id: str | None = None) -> tuple[
            set[int], np.ndarray, int, set[int]]:
        used: set[int] = set()
        layers = np.zeros(int(self.budget.num_rbg), dtype=int)
        logical = 0
        users: set[int] = set()
        for key, reservation in self._reservations.items():
            if key == reservation_id:
                continue
            used.update(reservation.rbg_indices)
            for index in reservation.rbg_indices:
                layers[int(index)] += int(reservation.layers_per_rbg)
            logical += int(reservation.logical_prb)
            users.update(int(user) for user in reservation.users)
        return used, layers, logical, users

    def reserve(
        self,
        *,
        grant_index: int,
        mode: str,
        users: Sequence[int],
        ranks: Sequence[int],
        rbg_indices: Sequence[int],
    ) -> ResourceReservation:
        index = _integer("grant_index", grant_index)
        mode_value = str(mode).upper()
        user_values = tuple(_integer("user", value) for value in users)
        rank_values = tuple(_integer("rank", value, 1) for value in ranks)
        rbg_values = tuple(_integer("rbg_index", value) for value in rbg_indices)
        if not user_values or len(user_values) != len(rank_values):
            raise ResourceLimitError(
                "invalid_grant", "users and ranks must be non-empty and aligned",
                structural=True)
        if mode_value not in ("SU", "MU"):
            raise ResourceLimitError(
                "unknown_mode", f"mode={mode_value!r}", structural=True)
        if len(set(user_values)) != len(user_values):
            raise ResourceLimitError(
                "duplicate_user", f"users={user_values}", structural=True)
        if not rbg_values or len(set(rbg_values)) != len(rbg_values):
            raise ResourceLimitError(
                "invalid_rbg_bitmap", f"rbg_indices={rbg_values}", structural=True)
        if any(value >= int(self.budget.num_rbg) for value in rbg_values):
            raise ResourceLimitError(
                "rbg_out_of_range", f"rbg_indices={rbg_values}", structural=True)
        if (mode_value == "SU" and len(user_values) != 1) or (
                mode_value == "MU" and len(user_values) < 2):
            raise ResourceLimitError(
                "mode_user_mismatch",
                f"mode={mode_value}, users={user_values}", structural=True)

        used, layers, logical_used, scheduled_users = self._usage_without()
        repeated_users = sorted(scheduled_users.intersection(user_values))
        if repeated_users:
            raise ResourceLimitError(
                "duplicate_user_in_tti",
                f"UEs already have another grant in this TTI: {repeated_users}",
                structural=True)
        overlap = sorted(used.intersection(rbg_values))
        if overlap:
            raise ResourceLimitError(
                "physical_rbg_overlap", f"already occupied RBG={overlap}",
                structural=True)
        grant_layers = int(sum(rank_values))
        if grant_layers > int(self.budget.max_layers_per_rbg):
            raise ResourceLimitError(
                "layer_limit",
                f"grant layers={grant_layers} > {self.budget.max_layers_per_rbg}")
        physical_prb = int(sum(
            int(self.budget.rbg_prb_sizes[value]) for value in rbg_values))
        logical_prb = physical_prb * grant_layers
        if logical_used + logical_prb > self.budget.resolved_max_logical_prb:
            raise ResourceLimitError(
                "logical_prb_budget",
                f"{logical_used}+{logical_prb} > {self.budget.resolved_max_logical_prb}")

        reservation_id = f"tti{self.tti}-res{self._counter}"
        self._counter += 1
        reservation = ResourceReservation(
            reservation_id=reservation_id,
            grant_index=index,
            mode=mode_value,
            users=user_values,
            ranks=rank_values,
            rbg_indices=rbg_values,
            physical_prb=physical_prb,
            layers_per_rbg=grant_layers,
            logical_prb=logical_prb,
        )
        self._reservations[reservation_id] = reservation
        return reservation

    def commit(self, reservation_id: str) -> ResourceReservation:
        key = str(reservation_id)
        try:
            current = self._reservations[key]
        except KeyError as exc:
            raise KeyError(f"unknown reservation {key!r}") from exc
        if current.status == "committed":
            return current
        committed = ResourceReservation(**{
            **asdict(current), "status": "committed"})
        self._reservations[key] = committed
        return committed

    def rollback(self, reservation_id: str) -> ResourceReservation:
        key = str(reservation_id)
        try:
            return self._reservations.pop(key)
        except KeyError as exc:
            raise KeyError(f"unknown reservation {key!r}") from exc

    def snapshot(self) -> ResourceLedgerSnapshot:
        used, layers, logical, users = self._usage_without()
        return ResourceLedgerSnapshot(
            used_rbg_indices=tuple(sorted(used)),
            used_physical_prb=int(sum(
                int(self.budget.rbg_prb_sizes[index]) for index in used)),
            used_logical_prb=int(logical),
            max_layers_used=int(np.max(layers)) if layers.size else 0,
            scheduled_users=tuple(sorted(users)),
            reservation_count=len(self._reservations),
            committed_count=sum(
                reservation.status == "committed"
                for reservation in self._reservations.values()),
            budget=self.budget.as_dict(),
        )

    def admit_grants(
        self,
        grants: Iterable[Any],
        *,
        structural_errors_are_fatal: bool = True,
    ) -> ResourceAdmission:
        accepted: list[int] = []
        reservation_ids: list[str] = []
        rejected: list[ResourceRejection] = []
        for grant_index, grant in enumerate(grants):
            try:
                reservation = self.reserve(
                    grant_index=grant_index,
                    mode=str(grant.mode),
                    users=tuple(int(value) for value in grant.users),
                    ranks=tuple(int(value) for value in grant.ranks),
                    rbg_indices=tuple(int(value) for value in grant.rbg_indices),
                )
            except ResourceLimitError as exc:
                if exc.structural and structural_errors_are_fatal:
                    raise
                rejected.append(ResourceRejection(
                    grant_index=grant_index, reason=exc.reason, detail=exc.detail))
                continue
            committed = self.commit(reservation.reservation_id)
            accepted.append(grant_index)
            reservation_ids.append(committed.reservation_id)
        return ResourceAdmission(
            accepted_grant_indices=tuple(accepted),
            reservation_ids=tuple(reservation_ids),
            rejections=tuple(rejected),
            snapshot=self.snapshot(),
        )
