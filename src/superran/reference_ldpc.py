"""Reference LDPC lookup planning from supplied source excerpts (2026-09-21).

This is a diagnostic path, separate from the bundled system TB-BLER profile.
No curve data are bundled here: a lookup plan cannot establish a BLER value.
Evidence: source excerpts, not execution of the original simulator.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from numbers import Integral

from .linkadapt import REFERENCE_MCS_SE

REFERENCE_BLOCK_LABELS = (5000, 3824, 1000, 500, 200, 100, 50)
REFERENCE_BLOCK_THRESHOLDS = (3840, 2000, 750, 250, 150, 75, 0)
REFERENCE_QM = (2,) * 6 + (4,) * 6 + (6,) * 9 + (8,) * 8

REFERENCE_BG1_RATES = (
    (0.333, 0.333, 0.333, 0.333),
    (0.333, 0.333, 0.333, 0.333),
    (0.333, 0.333, 0.333, 0.333),
    (0.333, 0.333, 0.333, 0.333),
    (0.438, 0.333, 0.333, 0.333),
    (0.588, 0.333, 0.333, 0.333),
    (0.369, 0.333, 0.333, 0.333),
    (0.424, 0.333, 0.333, 0.333),
    (0.479, 0.333, 0.333, 0.333),
    (0.54, 0.333, 0.333, 0.333),
    (0.602, 0.333, 0.333, 0.333),
    (0.643, 0.333, 0.333, 0.333),
    (0.455, 0.333, 0.333, 0.333),
    (0.505, 0.333, 0.333, 0.333),
    (0.554, 0.333, 0.333, 0.333),
    (0.602, 0.333, 0.333, 0.333),
    (0.65, 0.333, 0.333, 0.333),
    (0.702, 0.351, 0.342, 0.333),
    (0.754, 0.377, 0.354, 0.333),
    (0.803, 0.401, 0.364, 0.333),
    (0.853, 0.426, 0.374, 0.333),
    (0.667, 0.333, 0.333, 0.333),
    (0.694, 0.347, 0.34, 0.333),
    (0.736, 0.368, 0.35, 0.333),
    (0.778, 0.389, 0.359, 0.333),
    (0.821, 0.41, 0.368, 0.333),
    (0.864, 0.432, 0.376, 0.333),
    (0.895, 0.447, 0.382, 0.333),
    (0.926, 0.463, 0.387, 0.333),
)

REFERENCE_BG2_RATES = (
    (0.2, 0.2, 0.2, 0.2),
    (0.2, 0.2, 0.2, 0.2),
    (0.2, 0.2, 0.2, 0.2),
    (0.301, 0.2, 0.2, 0.2),
    (0.438, 0.219, 0.209, 0.2),
    (0.588, 0.294, 0.244, 0.204),
    (0.369, 0.2, 0.2, 0.2),
    (0.424, 0.212, 0.206, 0.2),
    (0.479, 0.239, 0.218, 0.2),
    (0.54, 0.27, 0.23, 0.2),
    (0.602, 0.301, 0.248, 0.206),
    (0.643, 0.321, 0.262, 0.21),
    (0.455, 0.228, 0.213, 0.2),
    (0.505, 0.252, 0.223, 0.2),
    (0.554, 0.277, 0.232, 0.2),
    (0.602, 0.301, 0.248, 0.206),
    (0.65, 0.325, 0.265, 0.211),
    (0.702, 0.351, 0.282, 0.216),
    (0.754, 0.377, 0.298, 0.221),
    (0.803, 0.401, 0.313, 0.228),
    (0.853, 0.426, 0.328, 0.237),
    (0.667, 0.333, 0.27, 0.213),
    (0.694, 0.347, 0.279, 0.216),
    (0.736, 0.368, 0.293, 0.219),
    (0.778, 0.389, 0.306, 0.224),
    (0.821, 0.411, 0.319, 0.231),
    (0.864, 0.432, 0.332, 0.24),
    (0.895, 0.448, 0.341, 0.247),
    (0.926, 0.463, 0.35, 0.254),
)

REFERENCE_CURVE_RATES = (
    (0.301, 0.438, 0.588, 0.200, 0.333, 0.294, 0.219, 0.244, 0.209, 0.204),
    (0.369, 0.424, 0.479, 0.540, 0.602, 0.643, 0.333, 0.212, 0.239, 0.270,
     0.301, 0.321, 0.200, 0.206, 0.218, 0.230, 0.248, 0.262, 0.2055, 0.210),
    (0.455, 0.505, 0.554, 0.602, 0.650, 0.702, 0.754, 0.803, 0.853, 0.228,
     0.252, 0.277, 0.301, 0.325, 0.351, 0.377, 0.401, 0.426, 0.213, 0.223,
     0.232, 0.248, 0.265, 0.342, 0.354, 0.364, 0.374, 0.200, 0.206, 0.211, 0.333),
    (0.667, 0.694, 0.736, 0.778, 0.821, 0.864, 0.895, 0.926, 0.333, 0.347,
     0.368, 0.389, 0.411, 0.432, 0.448, 0.463, 0.270, 0.340, 0.350, 0.359,
     0.368, 0.376, 0.382, 0.388, 0.213),
)


def _integer(name: str, value: int, low: int, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    if value < low or (high is not None and value > high):
        raise ValueError(f"{name} outside supported range")
    return int(value)


def reference_block_bucket(block_bits: int) -> int:
    """First descending threshold met; labels are not decision boundaries."""
    size = _integer("block_bits", block_bits, 1)
    return next(i for i, threshold in enumerate(REFERENCE_BLOCK_THRESHOLDS)
                if size >= threshold)


@dataclass(frozen=True)
class ReferenceLdpcPlan:
    mcs: int
    tbs_bits: int
    rv_index: int
    spectral_efficiency: float
    modulation_order: int
    effective_code_rate: float
    base_graph: str
    curve_code_rate: float
    curve_rate_index: int
    matched_curve_rate: float
    block_count: int
    lookup_block_bits: int
    bucket_index: int
    bucket_label: int
    bucket_threshold: int
    curve_data_status: str

    def as_dict(self) -> dict:
        result = asdict(self)
        result.update(
            evidence_type="source_inference",
            block_size_rule="min_tb_bg_limit_from_reply",
            system_profile_enabled=False,
        )
        return result


def reference_ldpc_plan(tbs_bits: int, mcs: int, rv_index: int = 0) -> ReferenceLdpcPlan:
    """Resolve explicit reference MCS/BG/IR/size/rate indices without fake BLER.

    RV indices 0..3 describe source table entries, not permission to change the
    system's one-retransmission contract. The reply's min(TB, BG limit) lookup
    size remains source-inferred until the complete lookup function is supplied.
    CRC/LDPC lifting is not implemented by this engineering approximation.
    """
    bits = _integer("tbs_bits", tbs_bits, 1)
    index = _integer("mcs", mcs, 0, 28)
    rv = _integer("rv_index", rv_index, 0, 3)
    se, qm = REFERENCE_MCS_SE[index], REFERENCE_QM[index]
    effective = se / qm
    bg2 = bits <= 308 or (bits <= 3840 and effective < 0.67) or effective <= 0.25
    if not bg2 and not ((308 < bits <= 3840 and effective > 0.67)
                       or (bits > 3840 and effective > 0.25)):
        raise ValueError("reference base-graph boundary is undefined")
    bg = "BG2" if bg2 else "BG1"
    limit = 3840 if bg2 else 8448
    rates = REFERENCE_BG2_RATES if bg2 else REFERENCE_BG1_RATES
    rate = rates[index][rv]
    # C++ fixed arrays zero-fill omitted trailing initializers to 40 entries.
    # Strict-less-than search retains the first equal-distance/duplicate entry.
    axis = REFERENCE_CURVE_RATES[qm // 2 - 1]
    padded_axis = axis + (0.0,) * (40 - len(axis))
    rate_index = min(range(40), key=lambda i: abs(rate - padded_axis[i]))
    lookup_bits = min(bits, limit)
    bucket = reference_block_bucket(lookup_bits)
    known_missing = (bucket == 4 and qm == 8) or (bucket >= 5 and qm > 2)
    return ReferenceLdpcPlan(
        index, bits, rv, se, qm, effective, bg, rate, rate_index,
        padded_axis[rate_index], (bits + limit - 1) // limit, lookup_bits,
        bucket, REFERENCE_BLOCK_LABELS[bucket], REFERENCE_BLOCK_THRESHOLDS[bucket],
        "known_missing" if known_missing else "not_loaded",
    )


def reference_tb_bler(cbler: float, block_count: int) -> float:
    """Combine an explicitly supplied CBLER under independent-block assumption.

    Does not look up a curve or reinterpret the bundled system TBLER as CBLER.
    Caller must supply measured/validated CBLER; missing values are rejected.
    """
    count = _integer("block_count", block_count, 1)
    if isinstance(cbler, bool) or cbler is None:
        raise ValueError("cbler must be finite and in [0,1]")
    probability = float(cbler)
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("cbler must be finite and in [0,1]")
    if probability == 1:
        return 1.0
    return -math.expm1(count * math.log1p(-probability))
