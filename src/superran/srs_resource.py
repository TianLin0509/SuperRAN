"""Basic, auditable SRS resource allocation for the fixed TDD carrier.

The system-level simulator currently has one validated carrier profile:
100 MHz at 30 kHz SCS, 272 PRB = 17 RBG x 16 PRB, with an 8:2 TDD
pattern.  This module deliberately models only the ordinary periodic ``H``
resource used by that profile:

* SRS occasions repeat every 10 slots and use slot phase 7;
* symbols 10..13 and comb offsets 0/1 are available;
* eight cyclic shifts are divided into consecutive blocks for 1/2/4 ports;
* PCI modulo three changes the deterministic preference order.  It is not a
  hard partition: a loaded cell may spill into the other colour groups;
* allocations in the same cell are orthogonal under the time/symbol/comb/
  cyclic-shift model.  Resource exhaustion is a hard failure.

P-H/F resources, BWP2, intra-slot antenna switching, root-sequence planning
and waveform-level cross-cell pilot contamination remain outside this first
profile.  The explicit collision report below provides a deterministic pilot
interference proxy without pretending that those omitted effects are already
implemented in the channel generator.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from math import gcd

import numpy as np

__all__ = [
    "BASIC_SRS_PROFILE_ID",
    "SrsResourceRequest",
    "SrsResourceAssignment",
    "SrsCollisionReport",
    "SrsResourceAllocator",
    "resources_collide",
    "cross_cell_collision_report",
    "allocate_basic_srs_resources",
    "summarize_assignments",
]


BASIC_SRS_PROFILE_ID = "superran-srs-basic-100m-30khz-8d2-v1"
SLOT_DURATION_MS = 0.5
SRS_SLOT_PHASE = 7
SRS_OCCASION_STRIDE_SLOTS = 10
SRS_SYMBOLS = (13, 12, 11, 10)
SRS_COMB_OFFSETS = (0, 1)
SRS_CYCLIC_SHIFT_COUNT = 8
SUPPORTED_PERIOD_MS = (5.0, 10.0, 20.0, 40.0)
SUPPORTED_PORTS = (1, 2, 4)


def _strict_int(name: str, value: int, *, minimum: int = 0) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
            value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    out = int(value)
    if out < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return out


def _period_slots(period_ms: float) -> int:
    value = float(period_ms)
    if not np.isfinite(value) or value not in SUPPORTED_PERIOD_MS:
        raise ValueError(
            f"period_ms only supports {SUPPORTED_PERIOD_MS}; got {period_ms!r}"
        )
    raw = value / SLOT_DURATION_MS
    rounded = int(round(raw))
    if not np.isclose(raw, rounded, rtol=0.0, atol=1e-12):
        raise ValueError("period_ms must map to an integer number of 30 kHz slots")
    return rounded


@dataclass(frozen=True)
class SrsResourceRequest:
    """One UE's request for the basic periodic SRS pool."""

    ue_id: int
    cell_id: int = 0
    pci_mod3: int | None = None
    period_ms: float = 10.0
    n_ports: int = 4
    hopping: bool = True

    def __post_init__(self) -> None:
        _strict_int("ue_id", self.ue_id)
        _strict_int("cell_id", self.cell_id)
        _period_slots(self.period_ms)
        ports = _strict_int("n_ports", self.n_ports, minimum=1)
        if ports not in SUPPORTED_PORTS:
            raise ValueError(f"n_ports only supports {SUPPORTED_PORTS}")
        if not isinstance(self.hopping, (bool, np.bool_)):
            raise ValueError("hopping must be boolean")
        if self.pci_mod3 is not None:
            pci = _strict_int("pci_mod3", self.pci_mod3)
            if pci not in (0, 1, 2):
                raise ValueError("pci_mod3 must be 0, 1 or 2")

    @property
    def resolved_pci_mod3(self) -> int:
        return int(self.cell_id) % 3 if self.pci_mod3 is None else int(self.pci_mod3)


@dataclass(frozen=True)
class SrsResourceAssignment:
    """A concrete resource leaf allocated to one UE."""

    ue_id: int
    cell_id: int
    pci_mod3: int
    period_ms: float
    period_slots: int
    offset_slots: int
    offset_ms: float
    symbol: int
    comb_offset: int
    cyclic_shifts: tuple[int, ...]
    n_ports: int
    hopping: bool
    resource_color: int
    preference_tier: int
    profile_id: str = BASIC_SRS_PROFILE_ID
    c_srs: int = 63
    b_srs: int = 1
    b_hop: int = 0
    n_rrc: int = 0
    frequency_scope: str = "17-hop H resource; one 16-PRB RBG per SRS occasion"

    def as_dict(self) -> dict[str, object]:
        out = asdict(self)
        out["cyclic_shifts"] = list(self.cyclic_shifts)
        out["scope"] = (
            "basic periodic H resource; intra-cell orthogonality enforced; "
            "PCI mod3 is preference ordering; cross-cell waveform contamination not modelled"
        )
        return out


@dataclass(frozen=True)
class SrsCollisionReport:
    """Cross-cell collision and LS-estimation error proxy."""

    assignment_count: int
    cross_cell_pair_count: int
    colliding_pair_count: int
    collision_pair_rate: float
    mean_colliders_per_assignment: float
    pilot_interference_to_signal_ratio: float
    ls_nmse_proxy: float
    noise_to_signal_ratio: float
    model_scope: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _candidate_offsets(period_slots: int) -> tuple[int, ...]:
    offsets = tuple(range(SRS_SLOT_PHASE, int(period_slots), SRS_OCCASION_STRIDE_SLOTS))
    if not offsets:
        raise ValueError("period does not contain a supported SRS occasion")
    return offsets


def _cyclic_shift_blocks(n_ports: int) -> tuple[tuple[int, ...], ...]:
    ports = int(n_ports)
    return tuple(
        tuple(range(start, start + ports))
        for start in range(0, SRS_CYCLIC_SHIFT_COUNT, ports)
    )


def _raw_candidates(request: SrsResourceRequest) -> list[SrsResourceAssignment]:
    period_slots = _period_slots(request.period_ms)
    raw: list[SrsResourceAssignment] = []
    linear = 0
    for offset in _candidate_offsets(period_slots):
        for symbol in SRS_SYMBOLS:
            for comb in SRS_COMB_OFFSETS:
                for shifts in _cyclic_shift_blocks(request.n_ports):
                    colour = linear % 3
                    raw.append(SrsResourceAssignment(
                        ue_id=int(request.ue_id),
                        cell_id=int(request.cell_id),
                        pci_mod3=int(request.resolved_pci_mod3),
                        period_ms=float(request.period_ms),
                        period_slots=int(period_slots),
                        offset_slots=int(offset),
                        offset_ms=float(offset) * SLOT_DURATION_MS,
                        symbol=int(symbol),
                        comb_offset=int(comb),
                        cyclic_shifts=tuple(int(x) for x in shifts),
                        n_ports=int(request.n_ports),
                        hopping=bool(request.hopping),
                        resource_color=int(colour),
                        preference_tier=(colour - request.resolved_pci_mod3) % 3,
                        b_srs=(1 if request.hopping else 0),
                        frequency_scope=(
                            "17-hop H resource; one 16-PRB RBG per SRS occasion"
                            if request.hopping else
                            "full-band no-hopping engineering upper bound"
                        ),
                    ))
                    linear += 1
    # Own colour first.  Within each tier keep the transparent last-symbol-first
    # and increasing comb/CS order above.  This makes three lightly loaded cells
    # start on different leaves while retaining deterministic spill-over.
    return sorted(raw, key=lambda item: item.preference_tier)


def resources_collide(
    left: SrsResourceAssignment,
    right: SrsResourceAssignment,
) -> bool:
    """Whether two periodic resources overlap under the basic pool model."""
    if int(left.symbol) != int(right.symbol):
        return False
    if int(left.comb_offset) != int(right.comb_offset):
        return False
    if not set(left.cyclic_shifts).intersection(right.cyclic_shifts):
        return False
    period_gcd = gcd(int(left.period_slots), int(right.period_slots))
    return (int(left.offset_slots) - int(right.offset_slots)) % period_gcd == 0


class SrsResourceAllocator:
    """Deterministic allocator with hard intra-cell collision prevention."""

    def __init__(self) -> None:
        self._assignments: dict[tuple[int, int], SrsResourceAssignment] = {}

    @property
    def assignments(self) -> tuple[SrsResourceAssignment, ...]:
        return tuple(self._assignments[key] for key in sorted(self._assignments))

    def allocate(self, request: SrsResourceRequest) -> SrsResourceAssignment:
        key = (int(request.cell_id), int(request.ue_id))
        if key in self._assignments:
            current = self._assignments[key]
            if (
                current.period_ms == float(request.period_ms)
                and current.n_ports == int(request.n_ports)
                and current.pci_mod3 == int(request.resolved_pci_mod3)
                and current.hopping == bool(request.hopping)
            ):
                return current
            raise ValueError(
                f"UE {request.ue_id} in cell {request.cell_id} already has a different SRS resource"
            )

        same_cell = [
            assignment
            for assignment in self._assignments.values()
            if int(assignment.cell_id) == int(request.cell_id)
        ]
        for candidate in _raw_candidates(request):
            if not any(resources_collide(candidate, used) for used in same_cell):
                self._assignments[key] = candidate
                return candidate
        raise RuntimeError(
            "basic SRS resource pool exhausted for "
            f"cell={request.cell_id}, period={request.period_ms:g} ms, "
            f"ports={request.n_ports}; allocated={len(same_cell)}"
        )

    def release(self, cell_id: int, ue_id: int) -> SrsResourceAssignment:
        key = (_strict_int("cell_id", cell_id), _strict_int("ue_id", ue_id))
        try:
            return self._assignments.pop(key)
        except KeyError as exc:
            raise KeyError(f"no SRS assignment for cell={key[0]}, UE={key[1]}") from exc

    def capacity_ues(
        self, *, period_ms: float = 10.0, n_ports: int = 4, cell_id: int = 0
    ) -> int:
        request = SrsResourceRequest(
            ue_id=0, cell_id=cell_id, period_ms=period_ms, n_ports=n_ports)
        return len(_raw_candidates(request))

    def summary(self) -> dict[str, object]:
        return summarize_assignments(self.assignments)


def allocate_basic_srs_resources(
    ue_ids: Sequence[int],
    *,
    period_ms: float = 10.0,
    n_ports_by_ue: Sequence[int] | int = 4,
    cell_ids: Sequence[int] | int = 0,
    pci_mod3_by_ue: Sequence[int | None] | int | None = None,
    hopping: bool = True,
) -> tuple[SrsResourceAssignment, ...]:
    """Allocate a batch while preserving the caller's UE order."""
    ues = [_strict_int("ue_id", value) for value in ue_ids]
    if isinstance(n_ports_by_ue, (bool, np.bool_)):
        raise ValueError("n_ports_by_ue cannot be boolean")
    if isinstance(n_ports_by_ue, (int, np.integer)):
        ports = [_strict_int("n_ports", n_ports_by_ue, minimum=1)] * len(ues)
    else:
        ports = [_strict_int("n_ports", value, minimum=1) for value in n_ports_by_ue]
    if isinstance(cell_ids, (bool, np.bool_)):
        raise ValueError("cell_ids cannot be boolean")
    if isinstance(cell_ids, (int, np.integer)):
        cells = [_strict_int("cell_id", cell_ids)] * len(ues)
    else:
        cells = [_strict_int("cell_id", value) for value in cell_ids]
    if isinstance(pci_mod3_by_ue, (bool, np.bool_)):
        raise ValueError("pci_mod3_by_ue cannot be boolean")
    if pci_mod3_by_ue is None or isinstance(pci_mod3_by_ue, (int, np.integer)):
        pci_values = [
            None if pci_mod3_by_ue is None
            else _strict_int("pci_mod3", pci_mod3_by_ue)
        ] * len(ues)
    else:
        pci_values = [
            None if value is None else _strict_int("pci_mod3", value)
            for value in pci_mod3_by_ue]
    if not (len(ports) == len(cells) == len(pci_values) == len(ues)):
        raise ValueError("UE, port, cell and PCI arrays must have the same length")

    allocator = SrsResourceAllocator()
    out: list[SrsResourceAssignment] = []
    for ue, port_count, cell, pci in zip(
            ues, ports, cells, pci_values, strict=True):
        out.append(allocator.allocate(SrsResourceRequest(
            ue_id=ue, cell_id=cell, pci_mod3=pci,
            period_ms=period_ms, n_ports=port_count, hopping=hopping)))
    return tuple(out)


def cross_cell_collision_report(
    assignments: Iterable[SrsResourceAssignment],
    *,
    noise_to_signal_ratio: float = 0.01,
) -> SrsCollisionReport:
    """Estimate cross-cell pilot interference from exact resource collisions.

    Each colliding external UE contributes one unit of interference relative to
    the desired pilot.  The resulting ``LS NMSE proxy = N/S + I/S`` is a
    deliberately simple validation experiment, not a waveform-level claim.
    """
    rows = list(assignments)
    noise = float(noise_to_signal_ratio)
    if not np.isfinite(noise) or noise < 0:
        raise ValueError("noise_to_signal_ratio must be finite and non-negative")
    cross_pairs = 0
    collisions = 0
    exposure = np.zeros(len(rows), dtype=float)
    for i, left in enumerate(rows):
        for j in range(i + 1, len(rows)):
            right = rows[j]
            if int(left.cell_id) == int(right.cell_id):
                continue
            cross_pairs += 1
            if resources_collide(left, right):
                collisions += 1
                exposure[i] += 1.0
                exposure[j] += 1.0
    mean_exposure = float(np.mean(exposure)) if rows else 0.0
    return SrsCollisionReport(
        assignment_count=len(rows),
        cross_cell_pair_count=int(cross_pairs),
        colliding_pair_count=int(collisions),
        collision_pair_rate=float(collisions / max(cross_pairs, 1)),
        mean_colliders_per_assignment=mean_exposure,
        pilot_interference_to_signal_ratio=mean_exposure,
        ls_nmse_proxy=float(noise + mean_exposure),
        noise_to_signal_ratio=noise,
        model_scope=(
            "exact periodic time/symbol/comb/cyclic-shift collision; equal pilot powers; "
            "ideal orthogonality otherwise; allocator-level validation proxy"
        ),
    )


def summarize_assignments(
    assignments: Iterable[SrsResourceAssignment],
) -> dict[str, object]:
    rows = list(assignments)
    by_cell: dict[str, int] = {}
    preference_tiers: dict[str, int] = {}
    for row in rows:
        key = str(int(row.cell_id))
        by_cell[key] = by_cell.get(key, 0) + 1
        tier = str(int(row.preference_tier))
        preference_tiers[tier] = preference_tiers.get(tier, 0) + 1
    unique = {
        (
            int(row.cell_id), int(row.period_slots), int(row.offset_slots),
            int(row.symbol), int(row.comb_offset), tuple(row.cyclic_shifts),
        )
        for row in rows
    }
    return {
        "enabled": bool(rows),
        "profile_id": BASIC_SRS_PROFILE_ID,
        "assigned_ues": len(rows),
        "unique_intra_cell_resources": len(unique),
        "by_cell": by_cell,
        "preference_tiers": preference_tiers,
        "scope": (
            "100 MHz/30 kHz/272 PRB ordinary periodic H resource; 1/2/4 ports; "
            "intra-cell orthogonality; PCI mod3 preference ordering"
        ),
        "not_modelled": [
            "P-H/F and BWP2 resources",
            "root-sequence planning and non-ideal cyclic-shift orthogonality",
            "waveform-level cross-cell pilot contamination",
            "intra-slot antenna switching",
        ],
    }
