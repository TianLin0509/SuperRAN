"""NR 载波与 RBG 栅格的单一真源。

当前 SuperRAN TDD 系统/体验仿真只有一个产品口径：100 MHz、
30 kHz SCS、272 RB、17 RBG × 16 RB。标准表的 273 RB 在生成前就按
工程简化去掉 1 RB，不在系统层生成第 18 个尾组。

本模块仍保留通用 38.214 Type-0 边界工具，供链路级、导入检查与
数学单测使用；对外的 TDD 系统入口必须走 :meth:`CarrierGrid.company_tdd`，
不得将通用工具暴露成可修改的系统参数。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

_VALID_SCS_KHZ = frozenset({15, 30, 60, 120, 240})


def _strict_int(name: str, value: Any, *, minimum: int | None = None) -> int:
    """拒绝 bool、浮点截断和数字字符串，避免载波几何被静默改写。"""
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, (int, np.integer)
    ):
        raise ValueError(f"{name} 必须是整数")
    result = int(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} 必须至少为 {minimum}")
    return result


def scs_khz_from_config(config: dict[str, Any]) -> int:
    """严格解析 numerology；缺省仍采用项目默认 30 kHz，坏值直接报错。"""
    raw = config.get("subcarrier_spacing", 30_000)
    if isinstance(raw, (bool, np.bool_)):
        raise ValueError("subcarrier_spacing 不能是布尔值")
    try:
        hz = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"subcarrier_spacing 必须是 Hz 数值，收到 {raw!r}") from exc
    if not np.isfinite(hz) or hz <= 0:
        raise ValueError(f"subcarrier_spacing 必须是有限正数，收到 {raw!r}")
    khz = hz / 1000.0
    rounded = int(round(khz))
    if not np.isclose(khz, rounded, rtol=0.0, atol=1e-9):
        raise ValueError(f"subcarrier_spacing 必须是整 kHz，收到 {raw!r} Hz")
    if rounded not in _VALID_SCS_KHZ:
        raise ValueError(
            f"不支持 {rounded} kHz SCS；当前支持 {sorted(_VALID_SCS_KHZ)}"
        )
    return rounded


def nominal_rbg_size(num_rb: int, *, size_config: int = 2) -> int:
    """返回 38.214 Type-0 RBG 名义大小 ``P``。

    ``size_config`` 对应 Table 5.1.2.2.1-1 的 Configuration 1/2。项目默认
    Configuration 2；272 RB 时两列都是 16，因此预置主场景结果不变。
    """
    n = _strict_int("num_rb", num_rb)
    if not 1 <= n <= 275:
        raise ValueError(f"Type-0 RBG 只定义在 BWP 1..275 PRB，收到 {num_rb}")
    cfg = _strict_int("rbg_size_config", size_config)
    if cfg not in (1, 2):
        raise ValueError("rbg_size_config 只支持 1 / 2")
    if n <= 36:
        return 2 if cfg == 1 else 4
    if n <= 72:
        return 4 if cfg == 1 else 8
    if n <= 144:
        return 8 if cfg == 1 else 16
    return 16


def rbg_boundaries(
    num_rb: int,
    *,
    nominal_size: int,
    bwp_start_rb: int = 0,
) -> tuple[tuple[int, int], ...]:
    """返回张量相对索引下的半开区间 ``(start, stop)``，完整覆盖每个 PRB。"""
    n = _strict_int("num_rb", num_rb, minimum=1)
    p = _strict_int("nominal_size", nominal_size, minimum=1)
    start_abs = _strict_int("bwp_start_rb", bwp_start_rb, minimum=0)

    first = p - (start_abs % p) if start_abs % p else p
    out: list[tuple[int, int]] = []
    cursor = 0
    width = min(first, n)
    out.append((0, width))
    cursor = width
    while cursor < n:
        stop = min(cursor + p, n)
        out.append((cursor, stop))
        cursor = stop
    return tuple(out)


def validate_boundaries(
    num_rows: int,
    boundaries: Sequence[Sequence[int]],
) -> tuple[tuple[int, int], ...]:
    """校验边界连续、无重叠、无漏项，并冻结成 tuple。"""
    n = _strict_int("num_rows", num_rows, minimum=1)
    out: list[tuple[int, int]] = []
    cursor = 0
    for i, pair in enumerate(boundaries):
        try:
            pair_len = len(pair)
        except TypeError as exc:
            raise ValueError(f"RBG 边界第 {i} 项不是 (start, stop)") from exc
        if pair_len != 2:
            raise ValueError(f"RBG 边界第 {i} 项不是 (start, stop)")
        start = _strict_int(f"RBG 边界第 {i} 项 start", pair[0])
        stop = _strict_int(f"RBG 边界第 {i} 项 stop", pair[1])
        if start != cursor or stop <= start or stop > n:
            raise ValueError(
                f"RBG 边界必须连续完整；第 {i} 项 {(start, stop)}，期望从 {cursor} 开始"
            )
        out.append((start, stop))
        cursor = stop
    if cursor != n:
        raise ValueError(f"RBG 边界只覆盖 {cursor}/{n} 个频域行")
    return tuple(out)


def uniform_boundaries(num_rows: int, rb_per_rbg: int) -> tuple[tuple[int, int], ...]:
    """兼容旧调用：按固定步长分组，但保留最后一个不足整组的尾组。"""
    return rbg_boundaries(
        _strict_int("num_rows", num_rows, minimum=1),
        nominal_size=_strict_int("rb_per_rbg", rb_per_rbg, minimum=1),
        bwp_start_rb=0,
    )


def expand_rbg_values(
    values: Sequence[Any], boundaries: Sequence[Sequence[int]], *, num_rows: int
) -> np.ndarray:
    """把逐 RBG 值按真实组宽展开为逐 RB/逐行数组。"""
    bounds = validate_boundaries(num_rows, boundaries)
    arr = np.asarray(values)
    if arr.ndim != 1 or arr.size != len(bounds):
        raise ValueError(f"逐 RBG 值长度 {arr.size} 与边界数 {len(bounds)} 不一致")
    return np.concatenate(
        [np.repeat(arr[i], stop - start) for i, (start, stop) in enumerate(bounds)]
    )


@dataclass(frozen=True)
class CarrierGrid:
    """一个可直接交给系统级仿真的 Type-0 载波栅格。"""

    num_rb: int
    scs_khz: int
    rbg_size_config: int
    nominal_rb_per_rbg: int
    bwp_start_rb: int
    boundaries: tuple[tuple[int, int], ...]
    profile_id: str = "nr-type0-generic-v1"
    user_configurable: bool = True
    standard_num_rb: int | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any], *, num_rb: int) -> CarrierGrid:
        n = _strict_int("num_rb", num_rb, minimum=1)
        size_cfg = _strict_int(
            "rbg_size_config", config.get("rbg_size_config", 2)
        )
        bwp_start = _strict_int(
            "bwp_start_rb", config.get("bwp_start_rb", 0), minimum=0
        )
        p = nominal_rbg_size(n, size_config=size_cfg)
        bounds = rbg_boundaries(n, nominal_size=p, bwp_start_rb=bwp_start)
        return cls(
            num_rb=n,
            scs_khz=scs_khz_from_config(config),
            rbg_size_config=size_cfg,
            nominal_rb_per_rbg=p,
            bwp_start_rb=bwp_start,
            boundaries=bounds,
        )

    @classmethod
    def company_tdd(cls, config: dict[str, Any], *, num_rb: int) -> CarrierGrid:
        """Return the frozen SuperRAN TDD system grid.

        ``num_rb`` is the actual channel-tensor width.  Configuration labels
        are checked too, so a stale ``20 MHz`` label paired with a 272-RB
        tensor cannot silently pass as the 100-MHz product profile.  Generic
        narrow-band channel generation remains available for link-level work;
        it is not a supported input to the current TDD system model.
        """
        from . import hardware as hw  # noqa: PLC0415

        n = _strict_int("num_rb", num_rb, minimum=1)
        if n != hw.COMPANY_NUM_RB:
            hint = (
                "（38.104 标准表里 100 MHz @ 30 kHz 是 273 RB，产品口径在生成前"
                "明确舍去 1 RB；这份数据若是改带宽后往返推导出的 273，"
                "请显式设 num_rb=272 重新生成）"
                if n == hw.COMPANY_NUM_RB + 1 else "")
            raise ValueError(
                "SuperRAN TDD 系统口径固定为 272 RB = 17 RBG × 16 RB；"
                f"信道张量实际是 {n} RB。当前不支持修改该格栅{hint}"
            )

        scs_khz = scs_khz_from_config(config)
        expected_scs_khz = int(hw.COMPANY_SCS_HZ // 1000)
        if scs_khz != expected_scs_khz:
            raise ValueError(
                "SuperRAN TDD 系统口径固定为 30 kHz SCS；"
                f"配置是 {scs_khz} kHz"
            )

        if "bandwidth_hz" in config:
            raw_bw = config["bandwidth_hz"]
            if isinstance(raw_bw, (bool, np.bool_)):
                raise ValueError("bandwidth_hz 不能是布尔值")
            try:
                bandwidth_hz = float(raw_bw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"bandwidth_hz 必须是 Hz 数值，收到 {raw_bw!r}"
                ) from exc
            if not np.isfinite(bandwidth_hz) or not np.isclose(
                bandwidth_hz, hw.COMPANY_BANDWIDTH_HZ, rtol=0.0, atol=1e-6
            ):
                raise ValueError(
                    "SuperRAN TDD 系统口径固定为 100 MHz；"
                    f"配置标签是 {raw_bw!r} Hz"
                )

        bwp_start = _strict_int(
            "bwp_start_rb", config.get("bwp_start_rb", 0), minimum=0
        )
        if bwp_start != 0:
            raise ValueError(
                "SuperRAN TDD 系统口径的 BWP 起点固定为 0，"
                "当前不支持修改"
            )

        size_cfg = _strict_int(
            "rbg_size_config", config.get("rbg_size_config", 2)
        )
        if size_cfg != 2:
            raise ValueError(
                "SuperRAN TDD 系统不暴露 rbg_size_config；"
                "固定资源结果是 17 RBG × 16 RB"
            )

        bounds = tuple(
            (i * hw.COMPANY_RB_PER_RBG, (i + 1) * hw.COMPANY_RB_PER_RBG)
            for i in range(hw.COMPANY_NUM_RBG)
        )
        return cls(
            num_rb=n,
            scs_khz=scs_khz,
            rbg_size_config=2,
            nominal_rb_per_rbg=hw.COMPANY_RB_PER_RBG,
            bwp_start_rb=0,
            boundaries=bounds,
            profile_id=hw.SUPERRAN_TDD_CARRIER_PROFILE_ID,
            user_configurable=False,
            standard_num_rb=hw.NR_TABLE_NUM_RB_100M_30K,
        )

    @property
    def num_rbg(self) -> int:
        return len(self.boundaries)

    @property
    def rbg_prb_sizes(self) -> tuple[int, ...]:
        return tuple(stop - start for start, stop in self.boundaries)

    @property
    def representative_rb_indices(self) -> tuple[int, ...]:
        return tuple((start + stop - 1) // 2 for start, stop in self.boundaries)

    @property
    def tti_ms(self) -> float:
        return 15.0 / float(self.scs_khz)

    def as_dict(self) -> dict[str, Any]:
        partial = [i for i, size in enumerate(self.rbg_prb_sizes)
                   if size != self.nominal_rb_per_rbg]
        return {
            "num_rb_in_channel": self.num_rb,
            "num_rbg": self.num_rbg,
            "rb_per_rbg": self.nominal_rb_per_rbg,
            "rbg_prb_sizes": list(self.rbg_prb_sizes),
            "rbg_boundaries": [list(pair) for pair in self.boundaries],
            "representative_rb_indices": list(self.representative_rb_indices),
            "simulated_num_rb": self.num_rb,
            "excluded_num_rb": 0,
            "partial_rbg_indices": partial,
            "scs_khz": self.scs_khz,
            "tti_ms": self.tti_ms,
            "bwp_start_rb": self.bwp_start_rb,
            "rbg_size_config": self.rbg_size_config,
            "profile_id": self.profile_id,
            "user_configurable": self.user_configurable,
            "standard_num_rb": self.standard_num_rb,
            "standard_tail_rb_omitted_before_generation": (
                self.standard_num_rb - self.num_rb
                if self.standard_num_rb is not None else 0
            ),
            "source": (
                "SuperRAN frozen TDD system profile, validated against channel shape"
                if not self.user_configurable
                else "derived from dataset channel shape and validated numerology"
            ),
            "rbg_size_basis": (
                "SuperRAN product baseline: 272 RB = 17 RBG x 16 RB; "
                "the standard 273rd RB is intentionally omitted before generation"
                if not self.user_configurable
                else "3GPP TS 38.214 Table 5.1.2.2.1-1 Type-0 RBG; "
                "first/last partial groups retained"
            ),
        }


def prb_count(indices: Iterable[int], sizes: Sequence[int]) -> int:
    """返回一个 RBG bitmap 实际占用的 PRB 数。"""
    width = tuple(
        _strict_int(f"RBG {i} 的 PRB 数", value, minimum=1)
        for i, value in enumerate(sizes)
    )
    total = 0
    for index in indices:
        i = _strict_int("RBG index", index, minimum=0)
        if not 0 <= i < len(width):
            raise ValueError(f"RBG index {i} 超出 0..{len(width) - 1}")
        total += width[i]
    return total
