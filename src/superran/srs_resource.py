"""Basic, auditable SRS resource allocation for the fixed TDD carrier.

The system-level simulator currently has one validated carrier profile:
100 MHz at 30 kHz SCS, 272 PRB = 17 RBG x 16 PRB, with an 8:2 TDD
pattern.  This module deliberately models only the ordinary periodic ``H``
resource used by that profile:

* SRS occasions repeat every 10 slots and use slot phase 7;
* symbols 10..13 and comb offsets 0/1 form eight visible leaves per occasion;
  ``(11,1)`` and ``(13,0)`` are bold BBL reservations and never enter the
  ordinary-H pool;
* the remaining six leaves are split evenly across PCI mod 3 (two per colour
  per SRS occasion), with no cross-colour spill-over;
* the product baseline exposes four cyclic shifts.  A 2T4R UE transmits two
  SRS ports at a time, so CS ``(0,1)`` and ``(2,3)`` let two UEs share one
  time/frequency leaf;
* one 2T4R assignment is a pair of two-port transmissions: antenna pair
  ``(0,1)`` now and ``(2,3)`` at the next available SRS opportunity.  Both
  transmissions keep the same frequency-resource phase; the 17-hop index is
  advanced only after the pair is complete;
* all UEs use one global period.  Allocation tries 10/20/40 ms in order and
  selects the shortest period whose own-colour pool fits every cell.

P-H/F resources, BWP2, intra-slot antenna switching and network-wide
root-sequence planning remain outside this first profile.  The allocator keeps
an inexpensive collision proxy; :mod:`superran.srs_waveform` can additionally
synthesise the exact RE observation when the caller supplies every desired and
interfering UE-to-victim-gNB UL channel.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from math import gcd

import numpy as np

from . import hardware as hw

__all__ = [
    "BASIC_SRS_PROFILE_ID",
    "PCI_MOD3_COLOR_BY_SYMBOL_COMB",
    "SRS_LEAF_ROLE_BY_SYMBOL_COMB",
    "SRS_BBL_LEAVES",
    "SRS_CYCLIC_SHIFT_COUNT",
    "SRS_FREQUENCY_RESOURCE_COUNT",
    "pci_mod3_resource_color",
    "pci_mod3_preference_order",
    "srs_leaf_role",
    "SrsResourceRequest",
    "SrsTransmissionLeg",
    "SrsResourceAssignment",
    "SrsCollisionReport",
    "SrsResourceAllocator",
    "resources_collide",
    "cross_cell_collision_report",
    "allocate_basic_srs_resources",
    "summarize_assignments",
]


BASIC_SRS_PROFILE_ID = "superran-srs-basic-100m-30khz-2t4r-4cs-17fdm-v3"
SLOT_DURATION_MS = 0.5
SRS_SLOT_PHASE = 7
SRS_OCCASION_STRIDE_SLOTS = 10
SRS_PAIR_STRIDE_SLOTS = 2 * SRS_OCCASION_STRIDE_SLOTS
SRS_SYMBOLS = (13, 12, 11, 10)
SRS_COMB_OFFSETS = (0, 1)
SRS_CYCLIC_SHIFT_COUNT = hw.COMPANY_SRS_CONFIGURED_CS
SRS_TX_PORTS_PER_OCCASION = hw.COMPANY_SRS_TX_PORTS_PER_OCCASION
SRS_LOGICAL_ANTENNA_PORTS = hw.COMPANY_SRS_LOGICAL_ANTENNA_PORTS
SRS_FREQUENCY_RESOURCE_COUNT = hw.COMPANY_NUM_RBG
SRS_ANTENNA_PORT_GROUPS = ((0, 1), (2, 3))
SUPPORTED_PERIOD_MS = (10.0, 20.0, 40.0)
SUPPORTED_PORTS = (SRS_LOGICAL_ANTENNA_PORTS,)

# Ordinary-H part of the user-confirmed PCI-mod-3 resource table.  The full
# field table also contains P-H/F, BWP2 and special long-period reservations;
# those cells remain outside the current basic profile.  Colour is attached to
# the time-frequency leaf rather than the cyclic shift: cross-cell timing error
# can erode cyclic-shift orthogonality, so neighbouring PCI classes first avoid
# the same symbol/comb resource before relying on code-domain separation.
SRS_BBL_LEAVES = frozenset({(11, 1), (13, 0)})
SRS_LEAF_ROLE_BY_SYMBOL_COMB: dict[tuple[int, int], int | str] = {
    (10, 0): 0,
    (10, 1): 1,
    (11, 0): 2,
    (11, 1): "bbl",
    (12, 0): 1,
    (12, 1): 2,
    (13, 0): "bbl",
    (13, 1): 0,
}
PCI_MOD3_COLOR_BY_SYMBOL_COMB: dict[tuple[int, int], int] = {
    key: int(role)
    for key, role in SRS_LEAF_ROLE_BY_SYMBOL_COMB.items()
    if role != "bbl"
}


def srs_leaf_role(symbol: int, comb_offset: int) -> int | str:
    """Return PCI colour 0/1/2 or ``"bbl"`` for one visible table leaf."""
    raw_symbol = _strict_int("symbol", symbol)
    raw_comb = _strict_int("comb_offset", comb_offset)
    try:
        return SRS_LEAF_ROLE_BY_SYMBOL_COMB[(raw_symbol, raw_comb)]
    except KeyError as exc:
        raise ValueError(
            "basic SRS table only supports symbols 10..13 and comb 0/1"
        ) from exc


def pci_mod3_resource_color(symbol: int, comb_offset: int) -> int:
    """Return the ordinary-H table colour for one symbol/comb leaf."""
    role = srs_leaf_role(symbol, comb_offset)
    if role == "bbl":
        raise ValueError(
            f"symbol={symbol}/comb={comb_offset} is a reserved BBL leaf"
        )
    return int(role)


def pci_mod3_preference_order(pci_mod3: int) -> tuple[int]:
    """Return the only allowed colour; cross-colour spill-over is forbidden."""
    pci = _strict_int("pci_mod3", pci_mod3)
    if pci not in (0, 1, 2):
        raise ValueError("pci_mod3 must be 0, 1 or 2")
    return (pci,)


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
    """One 2T4R UE's request for the basic periodic SRS pool.

    ``n_ports=4`` is the number of logical antenna ports to sound, not the
    number transmitted simultaneously.  The profile always sends two ports
    per SRS opportunity and switches to the other pair at the next one.
    """

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
    """Two linked two-port SRS transmissions allocated to one 2T4R UE."""

    ue_id: int
    cell_id: int
    pci_mod3: int
    period_ms: float
    period_slots: int
    legs: tuple[SrsTransmissionLeg, SrsTransmissionLeg]
    hopping: bool
    profile_id: str = BASIC_SRS_PROFILE_ID
    c_srs: int = 63
    b_srs: int = 1
    b_hop: int = 0
    n_rrc: int = 0
    frequency_scope: str = (
        "17 frequency-resource phases; both 2T legs sound the same 16-PRB "
        "RBG before the hop index advances"
    )

    @property
    def offset_slots(self) -> int:
        """Compatibility view: first 2T leg offset."""
        return int(self.legs[0].offset_slots)

    @property
    def offset_ms(self) -> float:
        return float(self.legs[0].offset_ms)

    @property
    def symbol(self) -> int:
        return int(self.legs[0].symbol)

    @property
    def comb_offset(self) -> int:
        return int(self.legs[0].comb_offset)

    @property
    def cyclic_shifts(self) -> tuple[int, ...]:
        return tuple(self.legs[0].cyclic_shifts)

    @property
    def frequency_resource_id(self) -> int:
        return int(self.legs[0].frequency_resource_id)

    @property
    def n_ports(self) -> int:
        return SRS_LOGICAL_ANTENNA_PORTS

    @property
    def tx_ports_per_occasion(self) -> int:
        return SRS_TX_PORTS_PER_OCCASION

    @property
    def resource_color(self) -> int:
        return int(self.pci_mod3)

    @property
    def preference_tier(self) -> int:
        # Kept only as a compatibility field.  Cross-colour spill is forbidden.
        return 0

    @property
    def antenna_port_groups(self) -> tuple[tuple[int, ...], ...]:
        return tuple(tuple(leg.antenna_ports) for leg in self.legs)

    def as_dict(self) -> dict[str, object]:
        out = asdict(self)
        out["legs"] = [leg.as_dict() for leg in self.legs]
        out.update({
            "offset_slots": self.offset_slots,
            "offset_ms": self.offset_ms,
            "symbol": self.symbol,
            "comb_offset": self.comb_offset,
            "cyclic_shifts": list(self.cyclic_shifts),
            "frequency_resource_id": self.frequency_resource_id,
            "n_ports": self.n_ports,
            "tx_ports_per_occasion": self.tx_ports_per_occasion,
            "resource_color": self.resource_color,
            "preference_tier": self.preference_tier,
            "antenna_port_groups": [list(x) for x in self.antenna_port_groups],
        })
        out["scope"] = (
            "ordinary periodic H only; BBL leaves excluded; four configured "
            "cyclic shifts; 2T4R antenna switching; own PCI-mod-3 colour only; "
            "waveform contamination is evaluated by srs_waveform only when "
            "explicit UL cross-links are supplied"
        )
        return out


@dataclass(frozen=True)
class SrsTransmissionLeg:
    """One two-port transmission in a 2T4R antenna-switching pair."""

    leg_index: int
    antenna_ports: tuple[int, int]
    offset_slots: int
    offset_ms: float
    symbol: int
    comb_offset: int
    cyclic_shifts: tuple[int, ...]
    frequency_resource_id: int

    def as_dict(self) -> dict[str, object]:
        out = asdict(self)
        out["antenna_ports"] = list(self.antenna_ports)
        out["cyclic_shifts"] = list(self.cyclic_shifts)
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


def _candidate_pair_offsets(period_slots: int) -> tuple[int, ...]:
    """First-leg offsets for disjoint current/next-opportunity 2T pairs.

    10 ms has one pair (slot phases 7->17), 20 ms has two
    (7->17 and 27->37), and 40 ms has four.  Pairing disjoint opportunities
    avoids counting the same periodic air resource twice.
    """
    offsets = tuple(range(SRS_SLOT_PHASE, int(period_slots), SRS_PAIR_STRIDE_SLOTS))
    if not offsets:
        raise ValueError("period does not contain a complete 2T4R SRS pair")
    if any(offset + SRS_OCCASION_STRIDE_SLOTS >= period_slots for offset in offsets):
        raise ValueError("period leaves an incomplete 2T4R SRS pair")
    return offsets


def _cyclic_shift_blocks() -> tuple[tuple[int, ...], ...]:
    """The two non-overlapping 2-CS blocks in the four-CS baseline."""
    return tuple(
        tuple(range(start, start + SRS_TX_PORTS_PER_OCCASION))
        for start in range(
            0, SRS_CYCLIC_SHIFT_COUNT, SRS_TX_PORTS_PER_OCCASION)
    )


def _raw_candidates(request: SrsResourceRequest) -> list[SrsResourceAssignment]:
    period_slots = _period_slots(request.period_ms)
    raw: list[SrsResourceAssignment] = []
    pci = int(request.resolved_pci_mod3)
    for offset in _candidate_pair_offsets(period_slots):
        for symbol in SRS_SYMBOLS:
            for comb in SRS_COMB_OFFSETS:
                role = srs_leaf_role(symbol, comb)
                if role == "bbl" or int(role) != pci:
                    continue
                frequency_count = (
                    SRS_FREQUENCY_RESOURCE_COUNT if request.hopping else 1)
                for frequency_resource_id in range(frequency_count):
                    for shifts in _cyclic_shift_blocks():
                        legs = tuple(
                            SrsTransmissionLeg(
                                leg_index=leg_index,
                                antenna_ports=tuple(antenna_ports),
                                offset_slots=int(
                                    offset + leg_index * SRS_OCCASION_STRIDE_SLOTS),
                                offset_ms=float(
                                    offset + leg_index * SRS_OCCASION_STRIDE_SLOTS
                                ) * SLOT_DURATION_MS,
                                symbol=int(symbol),
                                comb_offset=int(comb),
                                cyclic_shifts=tuple(int(x) for x in shifts),
                                frequency_resource_id=int(frequency_resource_id),
                            )
                            for leg_index, antenna_ports in enumerate(
                                SRS_ANTENNA_PORT_GROUPS)
                        )
                        raw.append(SrsResourceAssignment(
                            ue_id=int(request.ue_id),
                            cell_id=int(request.cell_id),
                            pci_mod3=pci,
                            period_ms=float(request.period_ms),
                            period_slots=int(period_slots),
                            legs=legs,  # type: ignore[arg-type]
                            hopping=bool(request.hopping),
                            b_srs=(1 if request.hopping else 0),
                            frequency_scope=(
                                "17 frequency-resource phases; both 2T legs "
                                "sound one 16-PRB RBG before advancing"
                                if request.hopping else
                                "full-band no-hopping engineering upper bound"
                            ),
                        ))
    return raw


def _legs_collide(
    left: SrsTransmissionLeg,
    right: SrsTransmissionLeg,
    *,
    left_period_slots: int,
    right_period_slots: int,
) -> bool:
    if int(left.symbol) != int(right.symbol):
        return False
    if int(left.comb_offset) != int(right.comb_offset):
        return False
    if int(left.frequency_resource_id) != int(right.frequency_resource_id):
        return False
    if not set(left.cyclic_shifts).intersection(right.cyclic_shifts):
        return False
    period_gcd = gcd(int(left_period_slots), int(right_period_slots))
    return (int(left.offset_slots) - int(right.offset_slots)) % period_gcd == 0


def resources_collide(
    left: SrsResourceAssignment,
    right: SrsResourceAssignment,
) -> bool:
    """Whether any two legs overlap in time/symbol/comb/FDM/CS."""
    return any(
        _legs_collide(
            left_leg, right_leg,
            left_period_slots=left.period_slots,
            right_period_slots=right.period_slots,
        )
        for left_leg in left.legs for right_leg in right.legs
    )


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
        if same_cell and any(
                float(row.period_ms) != float(request.period_ms)
                for row in same_cell):
            raise ValueError(
                f"cell {request.cell_id} already uses global SRS period "
                f"{same_cell[0].period_ms:g} ms; cannot mix "
                f"{request.period_ms:g} ms")
        if same_cell and any(
                int(row.pci_mod3) != int(request.resolved_pci_mod3)
                for row in same_cell):
            raise ValueError(
                f"cell {request.cell_id} cannot mix PCI-mod-3 colours")
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
    adaptive_period: bool = True,
) -> tuple[SrsResourceAssignment, ...]:
    """Allocate a batch with one global, shortest sufficient period.

    ``period_ms`` is the starting/minimum candidate.  With
    ``adaptive_period=True`` (default), trials proceed atomically through the
    supported periods at or above it.  A trial may use only each cell's own
    PCI-mod-3 colour.  No partial allocation or cross-colour spill survives a
    failed trial.
    """
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
    identities = list(zip(cells, ues, strict=True))
    if len(set(identities)) != len(identities):
        raise ValueError(
            "同一 cell 内的 ue_id 必须唯一；批量输入不能把幂等重复请求"
            "误算成两个已分配用户"
        )
    resolved_pci = [
        int(cell) % 3 if pci is None else int(pci)
        for cell, pci in zip(cells, pci_values, strict=True)
    ]
    by_cell_pci: dict[int, int] = {}
    for cell, pci in zip(cells, resolved_pci, strict=True):
        previous = by_cell_pci.setdefault(int(cell), int(pci))
        if previous != int(pci):
            raise ValueError(
                f"all UEs in cell {cell} must share one pci_mod3; "
                f"got {previous} and {pci}"
            )
    if not isinstance(adaptive_period, (bool, np.bool_)):
        raise ValueError("adaptive_period must be boolean")
    start_period = float(period_ms)
    _period_slots(start_period)
    if not ues:
        return ()

    periods = tuple(
        value for value in SUPPORTED_PERIOD_MS if value >= start_period
    ) if adaptive_period else (start_period,)
    failures: list[str] = []
    for selected_period in periods:
        allocator = SrsResourceAllocator()
        out: list[SrsResourceAssignment] = []
        try:
            for ue, port_count, cell, pci in zip(
                    ues, ports, cells, resolved_pci, strict=True):
                out.append(allocator.allocate(SrsResourceRequest(
                    ue_id=ue, cell_id=cell, pci_mod3=pci,
                    period_ms=selected_period, n_ports=port_count,
                    hopping=hopping)))
        except RuntimeError as exc:
            failures.append(f"{selected_period:g}ms: {exc}")
            continue
        return tuple(out)
    hopping_hint = (
        "srs_hopping=true keeps the 17 frequency-resource dimension"
        if hopping else
        "srs_hopping=false removes the 17 frequency-resource dimension; "
        "enable hopping, reduce the per-colour UE count, or allow a longer period"
    )
    raise RuntimeError(
        "no global SRS period can fit all 2T4R UEs in their own PCI-mod-3 "
        f"resources ({hopping_hint}); " + " | ".join(failures)
    )


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
            "exact periodic time/symbol/comb/frequency-resource/cyclic-shift "
            "collision across both 2T4R legs; equal pilot powers; ideal "
            "orthogonality otherwise; allocator-level validation proxy"
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
    unique_legs = {
        (
            int(row.cell_id), int(row.period_slots), int(leg.offset_slots),
            int(leg.symbol), int(leg.comb_offset),
            int(leg.frequency_resource_id), tuple(leg.cyclic_shifts),
        )
        for row in rows for leg in row.legs
    }
    selected_periods = sorted({float(row.period_ms) for row in rows})
    hopping_modes = {bool(row.hopping) for row in rows}
    return {
        "enabled": bool(rows),
        "profile_id": BASIC_SRS_PROFILE_ID,
        "assigned_ues": len(rows),
        "unique_intra_cell_transmission_legs": len(unique_legs),
        "by_cell": by_cell,
        "preference_tiers": preference_tiers,
        "selected_global_period_ms": (
            selected_periods[0] if len(selected_periods) == 1 else None),
        "period_is_global": len(selected_periods) <= 1,
        "cyclic_shift_count": SRS_CYCLIC_SHIFT_COUNT,
        "tx_ports_per_occasion": SRS_TX_PORTS_PER_OCCASION,
        "logical_antenna_ports": SRS_LOGICAL_ANTENNA_PORTS,
        "transmission_legs_per_ue": 2,
        "frequency_resource_count": (
            SRS_FREQUENCY_RESOURCE_COUNT
            if hopping_modes == {True} else 1 if hopping_modes == {False}
            else None),
        "scope": (
            "100 MHz/30 kHz/272 PRB ordinary periodic H resource; BBL "
            "excluded; 2T4R inter-opportunity antenna switching; four CS; "
            "17 frequency-resource phases; own PCI-mod-3 colour only"
        ),
        "leaf_role_by_symbol_comb": {
            f"symbol{symbol}_comb{comb}": role
            for (symbol, comb), role in sorted(
                SRS_LEAF_ROLE_BY_SYMBOL_COMB.items())
        },
        "not_modelled": [
            "P-H/F and BWP2 resources",
            "network-wide n_SRS_ID/root-sequence planning",
            "automatic construction of interfering UE-to-victim-gNB UL cross-links",
        ],
        "waveform_backend": (
            "srs_waveform: exact RE synthesis, CFO/timing, LS+delay-gate, "
            "two-leg 64x4 assembly and hashable raw UL IoT evidence"
        ),
    }
