r"""Frequency-domain downlink power control with exact inter-cell coupling.

The beamforming ``power_constraint`` (EBF/PEBF/NEBF) controls the spatial
matrix on one frequency.  This module is the orthogonal frequency-domain
layer: it assigns a continuous multiplier to every RB while keeping the cell
wideband power unchanged.

For victim UE ``u`` on RB ``r`` the physical update is

.. math::

   \gamma_{u,r} =
   \frac{q_{s(u),r} S_u}
        {N_u + \eta_u \sum_{k\ne s(u)} q_{k,r} I_{u,k}},

where ``q`` is constrained to ``[0.1, 4]`` and has mean one for every cell.
The per-cell ``I_{u,k}`` terms must come from ChannelHub's geometry engine;
an aggregate SIR cannot recover this expression after different cells choose
different RB profiles.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

MIN_RB_POWER_MULTIPLIER = 0.1
MAX_RB_POWER_MULTIPLIER = 4.0
_EPS = 1e-30
_SUM_TOL = 5e-12


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "on", "true", "yes"}:
            return True
        if normalized in {"", "0", "off", "false", "no"}:
            return False
        raise ValueError(
            "rb_power_control_enabled 只接受 on/off、true/false、yes/no 或 1/0")
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    raise ValueError("rb_power_control_enabled 必须是布尔值或布尔字符串")


def _strict_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
            value, (int, np.integer)):
        raise ValueError(f"{name} 必须是整数")
    out = int(value)
    if out < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}")
    return out


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} 必须是有限数，不能是布尔值")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限数") from exc
    if not np.isfinite(out):
        raise ValueError(f"{name} 必须是有限数")
    return out


@dataclass(frozen=True)
class RbPowerOverride:
    """One inclusive RB range override for all cells or one cell index."""

    cell_index: int | None
    rb_start: int
    rb_end: int
    multiplier: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "cell_index": "all" if self.cell_index is None else self.cell_index,
            "rb_start": self.rb_start,
            "rb_end": self.rb_end,
            "multiplier": self.multiplier,
        }


def parse_overrides(raw: str | Sequence[dict[str, Any]] | None,
                    *, num_rb: int) -> tuple[RbPowerOverride, ...]:
    """Parse the MCP/UI override array without silently repairing input.

    Each record is one of::

        {"cell_index": "all", "rb": 7, "multiplier": 2.0}
        {"cell_index": 3, "rb_start": 16, "rb_end": 31,
         "multiplier": 0.5}

    ``cell_index`` defaults to ``"all"``.  Range ends are inclusive.
    Unspecified RBs are balanced later so the per-cell mean is exactly one.
    """
    n_rb = _strict_int(num_rb, "num_rb", minimum=1)
    value: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"rb_power_overrides 不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列"
            ) from exc
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("rb_power_overrides 必须是 JSON/对象数组")

    allowed = {"cell_index", "rb", "rb_start", "rb_end", "multiplier"}
    out: list[RbPowerOverride] = []
    for pos, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"rb_power_overrides[{pos}] 必须是对象")
        unknown = set(item) - allowed
        if unknown:
            raise ValueError(
                f"rb_power_overrides[{pos}] 有未知字段：{sorted(unknown)}")
        if "multiplier" not in item:
            raise ValueError(f"rb_power_overrides[{pos}] 缺 multiplier")
        mult = _finite_float(item["multiplier"], f"overrides[{pos}].multiplier")
        if not MIN_RB_POWER_MULTIPLIER <= mult <= MAX_RB_POWER_MULTIPLIER:
            raise ValueError(
                f"overrides[{pos}].multiplier={mult:g} 超出 "
                f"[{MIN_RB_POWER_MULTIPLIER:g},{MAX_RB_POWER_MULTIPLIER:g}]")

        cell_raw = item.get("cell_index", "all")
        if isinstance(cell_raw, str) and cell_raw.strip().lower() == "all":
            cell: int | None = None
        else:
            cell = _strict_int(cell_raw, f"overrides[{pos}].cell_index")

        has_rb = "rb" in item
        has_range = "rb_start" in item or "rb_end" in item
        if has_rb == has_range:
            raise ValueError(
                f"rb_power_overrides[{pos}] 必须二选一：rb，或 rb_start+rb_end")
        if has_rb:
            start = end = _strict_int(item["rb"], f"overrides[{pos}].rb")
        else:
            if "rb_start" not in item or "rb_end" not in item:
                raise ValueError(
                    f"rb_power_overrides[{pos}] 的 rb_start/rb_end 必须成对出现")
            start = _strict_int(item["rb_start"], f"overrides[{pos}].rb_start")
            end = _strict_int(item["rb_end"], f"overrides[{pos}].rb_end")
        if start > end:
            raise ValueError(f"rb_power_overrides[{pos}] 的 rb_start 不能大于 rb_end")
        if end >= n_rb:
            raise ValueError(
                f"rb_power_overrides[{pos}] 的 RB {end} 越界；有效范围 0..{n_rb - 1}")
        out.append(RbPowerOverride(cell, start, end, mult))
    return tuple(out)


def _resolve_one_profile(num_rb: int, overrides: Sequence[RbPowerOverride],
                         *, label: str) -> np.ndarray:
    profile = np.full(int(num_rb), np.nan, dtype=float)
    for ov in overrides:
        view = profile[ov.rb_start:ov.rb_end + 1]
        if np.any(np.isfinite(view)):
            raise ValueError(
                f"{label} 的 RB {ov.rb_start}..{ov.rb_end} 与另一条 override 重叠")
        profile[ov.rb_start:ov.rb_end + 1] = float(ov.multiplier)

    missing = np.flatnonzero(~np.isfinite(profile))
    specified_sum = math.fsum(float(x) for x in profile[np.isfinite(profile)])
    target = float(num_rb)
    if missing.size:
        balance = (target - specified_sum) / int(missing.size)
        if not MIN_RB_POWER_MULTIPLIER <= balance <= MAX_RB_POWER_MULTIPLIER:
            raise ValueError(
                f"{label} 的指定功率无法在剩余 {missing.size} 个 RB 上补偿："
                f"需要 {balance:.9g}x，超出 "
                f"[{MIN_RB_POWER_MULTIPLIER:g},{MAX_RB_POWER_MULTIPLIER:g}]")
        profile[missing] = balance
        # Close the floating-point sum on one auto-balanced RB.  User-specified
        # values are never changed.
        residual = target - math.fsum(float(x) for x in profile)
        profile[int(missing[-1])] += residual
    else:
        error = specified_sum - target
        if abs(error) > _SUM_TOL * max(target, 1.0):
            raise ValueError(
                f"{label} 已覆盖全部 RB，但 multiplier 总和为 {specified_sum:.12g}，"
                f"必须等于 {target:g}（均值 1）")

    if np.any(profile < MIN_RB_POWER_MULTIPLIER - _SUM_TOL) or np.any(
            profile > MAX_RB_POWER_MULTIPLIER + _SUM_TOL):
        raise RuntimeError("内部错误：最终 RB 功率越界")
    if abs(math.fsum(float(x) for x in profile) - target) > _SUM_TOL * target:
        raise RuntimeError("内部错误：RB 总功率未守恒")
    return profile


@dataclass(frozen=True)
class RbPowerControlConfig:
    """Validated per-RB power-control input and resolver."""

    enabled: bool = False
    num_rb: int = 272
    overrides: tuple[RbPowerOverride, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, (bool, np.bool_)):
            raise ValueError("RbPowerControlConfig.enabled 必须是布尔值")
        _strict_int(self.num_rb, "num_rb", minimum=1)
        if not isinstance(self.overrides, tuple) or not all(
                isinstance(item, RbPowerOverride) for item in self.overrides):
            raise ValueError("RbPowerControlConfig.overrides 必须是 RbPowerOverride 元组")

    @classmethod
    def from_raw(cls, *, enabled: Any = False, num_rb: int = 272,
                 overrides: str | Sequence[dict[str, Any]] | None = None
                 ) -> RbPowerControlConfig:
        n_rb = _strict_int(num_rb, "num_rb", minimum=1)
        parsed = parse_overrides(overrides, num_rb=n_rb)
        return cls(enabled=_flag(enabled), num_rb=n_rb, overrides=parsed)

    def resolve_profiles(self, num_cells: int) -> np.ndarray:
        """Return ``[cell,RB]`` final multipliers, each row summing to N_RB."""
        n_cells = _strict_int(num_cells, "num_cells", minimum=1)
        if not self.enabled:
            return np.ones((n_cells, self.num_rb), dtype=float)
        bad = sorted({int(x.cell_index) for x in self.overrides
                      if x.cell_index is not None and int(x.cell_index) >= n_cells})
        if bad:
            raise ValueError(
                f"rb_power_overrides 引用了不存在的小区 {bad}；有效范围 0..{n_cells - 1}")
        global_ov = [x for x in self.overrides if x.cell_index is None]
        out = np.ones((n_cells, self.num_rb), dtype=float)
        for cell in range(n_cells):
            local = [x for x in self.overrides if x.cell_index == cell]
            out[cell] = _resolve_one_profile(
                self.num_rb, [*global_ov, *local], label=f"cell {cell}")
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "resolution": "RB",
            "num_rb": int(self.num_rb),
            "min_multiplier": MIN_RB_POWER_MULTIPLIER,
            "max_multiplier": MAX_RB_POWER_MULTIPLIER,
            "total_power_constraint": "sum(q[cell,:]) == num_rb (mean 1)",
            "unspecified_rb_policy": "uniform auto-balance; infeasible input hard-fails",
            "overrides": [x.as_dict() for x in self.overrides],
        }


@dataclass(frozen=True)
class DownlinkPowerGeometry:
    """Absolute geometry terms needed for exact cross-cell power coupling."""

    serving_cell_index: np.ndarray       # [sample]
    signal_power_mw: np.ndarray          # [sample]
    thermal_noise_power_mw: np.ndarray   # [sample]
    interference_power_mw: np.ndarray    # [sample,slot,cell]

    def __post_init__(self) -> None:
        serving = np.asarray(self.serving_cell_index)
        signal = np.asarray(self.signal_power_mw, dtype=float)
        noise = np.asarray(self.thermal_noise_power_mw, dtype=float)
        intf = np.asarray(self.interference_power_mw, dtype=float)
        if serving.ndim != 1 or signal.shape != serving.shape or noise.shape != serving.shape:
            raise ValueError("功率几何的 serving/signal/noise 必须是同长度一维数组")
        if intf.ndim == 2:
            intf = intf[:, None, :]
            object.__setattr__(self, "interference_power_mw", intf)
        if intf.ndim != 3 or intf.shape[0] != serving.size or intf.shape[2] < 1:
            raise ValueError("逐小区干扰必须是 [sample,slot,cell]")
        if serving.size < 1:
            raise ValueError("功率几何不能为空")
        if np.any(~np.isfinite(signal)) or np.any(signal <= 0):
            raise ValueError("dl_signal_power_mw 必须全为有限正数")
        if np.any(~np.isfinite(noise)) or np.any(noise <= 0):
            raise ValueError("dl_thermal_noise_power_mw 必须全为有限正数")
        if np.any(~np.isfinite(intf)) or np.any(intf < 0):
            raise ValueError("逐小区干扰功率必须全为有限非负数")
        if np.any(serving != np.floor(serving)):
            raise ValueError("serving_cell_index 必须是整数")
        serving_i = serving.astype(int)
        if np.any(serving_i < 0) or np.any(serving_i >= intf.shape[2]):
            raise ValueError("serving_cell_index 超出逐小区干扰数组范围")
        own = intf[np.arange(serving.size), :, serving_i]
        scale = np.maximum(np.sum(intf, axis=2), _EPS)
        if np.any(own > 1e-10 * scale + 1e-24):
            raise ValueError("逐小区干扰数组的 serving-cell 列必须为 0")
        object.__setattr__(self, "serving_cell_index", serving_i)
        object.__setattr__(self, "signal_power_mw", signal)
        object.__setattr__(self, "thermal_noise_power_mw", noise)

    @property
    def num_samples(self) -> int:
        return int(np.asarray(self.serving_cell_index).size)

    @property
    def num_cells(self) -> int:
        return int(np.asarray(self.interference_power_mw).shape[2])

    def slots_for_sample(self, sample: int, expected_slots: int) -> np.ndarray:
        rows = np.asarray(self.interference_power_mw[int(sample)], dtype=float)
        if rows.shape[0] != int(expected_slots):
            raise ValueError(
                f"样本 {sample} 的逐小区干扰有 {rows.shape[0]} 个 slot，"
                f"信道却有 {expected_slots} 个；RB 功控拒绝复制/平均干扰快照")
        return rows


def geometry_from_dataset(dataset: Any) -> DownlinkPowerGeometry:
    """Load the strict power-decomposition contract from a Dataset."""
    required = (
        "serving_cell_index", "dl_signal_power_mw", "dl_thermal_noise_power_mw")
    try:
        scalars = [np.asarray(dataset.scalar(name)) for name in required]
        intf = np.asarray(dataset.cell_geometry[
            "dl_interference_power_per_slot_per_cell_mw"])
    except (KeyError, AttributeError) as exc:
        raise ValueError(
            "该数据集没有 RB 功控所需的逐小区功率分解。请用当前 ChannelHub "
            "重新生成；不能从聚合 SINR/SIR 反推出不同邻区的干扰变化。") from exc
    return DownlinkPowerGeometry(
        serving_cell_index=scalars[0], signal_power_mw=scalars[1],
        thermal_noise_power_mw=scalars[2], interference_power_mw=intf)


@dataclass(frozen=True)
class RbPowerCoupling:
    """One victim snapshot's exact RB-domain power update."""

    channel_power_scale: np.ndarray      # q_serving * D_base / D_controlled
    desired_multiplier: np.ndarray       # [RB]
    baseline_denominator_mw: float
    controlled_denominator_mw: np.ndarray
    controlled_interference_mw: np.ndarray
    geometric_sinr_db: np.ndarray
    iot_db: np.ndarray


def couple_rb_power(*, signal_power_mw: float, thermal_noise_power_mw: float,
                    interference_power_per_cell_mw: np.ndarray,
                    serving_cell_index: int, profiles: np.ndarray,
                    neighbor_utilization: float) -> RbPowerCoupling:
    """Apply desired and every interferer's RB profile to one victim snapshot."""
    signal = _finite_float(signal_power_mw, "signal_power_mw")
    noise = _finite_float(thermal_noise_power_mw, "thermal_noise_power_mw")
    util = _finite_float(neighbor_utilization, "neighbor_utilization")
    if signal <= 0 or noise <= 0:
        raise ValueError("signal/noise power 必须为正数")
    if not 0.0 <= util <= 1.0:
        raise ValueError("neighbor_utilization 必须在 [0,1]")
    intf = np.asarray(interference_power_per_cell_mw, dtype=float)
    q = np.asarray(profiles, dtype=float)
    if (intf.ndim != 1 or q.ndim != 2 or q.shape[0] != intf.size
            or q.shape[1] < 1):
        raise ValueError("interference [cell] 与 profiles [cell,RB] 形状不一致")
    serving = _strict_int(serving_cell_index, "serving_cell_index")
    if serving >= q.shape[0]:
        raise ValueError("serving_cell_index 越界")
    if np.any(~np.isfinite(intf)) or np.any(intf < 0):
        raise ValueError("interference power 必须为有限非负数")
    if intf[serving] > 1e-10 * max(float(np.sum(intf)), _EPS) + 1e-24:
        raise ValueError("interference 的 serving-cell 项必须为 0")
    if np.any(~np.isfinite(q)) or np.any(q < MIN_RB_POWER_MULTIPLIER) or np.any(
            q > MAX_RB_POWER_MULTIPLIER):
        raise ValueError("最终 RB multiplier 非法")
    row_sums = np.sum(q, axis=1, dtype=np.float64)
    if np.any(np.abs(row_sums - q.shape[1]) > _SUM_TOL * q.shape[1]):
        raise ValueError("每个小区的 RB multiplier 总和必须等于 RB 数（均值 1）")

    baseline_i = util * float(np.sum(intf))
    baseline_d = noise + baseline_i
    controlled_i = util * (intf @ q)
    controlled_d = noise + controlled_i
    desired = q[serving]
    scale = desired * baseline_d / np.maximum(controlled_d, _EPS)
    sinr = desired * signal / np.maximum(controlled_d, _EPS)
    iot = controlled_d / noise
    return RbPowerCoupling(
        channel_power_scale=np.asarray(scale, dtype=float),
        desired_multiplier=np.asarray(desired, dtype=float),
        baseline_denominator_mw=float(baseline_d),
        controlled_denominator_mw=np.asarray(controlled_d, dtype=float),
        controlled_interference_mw=np.asarray(controlled_i, dtype=float),
        geometric_sinr_db=10.0 * np.log10(np.maximum(sinr, _EPS)),
        iot_db=10.0 * np.log10(np.maximum(iot, _EPS)),
    )


def profile_summary(profiles: np.ndarray) -> dict[str, Any]:
    q = np.asarray(profiles, dtype=float)
    if q.ndim != 2:
        raise ValueError("profiles 必须是 [cell,RB]")
    n_rb = q.shape[1]
    rows = []
    for cell, row in enumerate(q):
        rows.append({
            "cell_index": int(cell),
            "min_multiplier": float(np.min(row)),
            "max_multiplier": float(np.max(row)),
            "mean_multiplier": float(np.mean(row)),
            "sum_multiplier": float(math.fsum(float(x) for x in row)),
            "sum_error": float(math.fsum(float(x) for x in row) - n_rb),
            "changed_rb": int(np.count_nonzero(np.abs(row - 1.0) > 1e-12)),
            "multipliers": [float(x) for x in row],
        })
    return {"num_cells": int(q.shape[0]), "num_rb": int(n_rb), "cells": rows}


def config_fingerprint(config: RbPowerControlConfig) -> str:
    """Stable identity used to prevent mislabeled link-table reuse."""
    if not isinstance(config, RbPowerControlConfig):
        raise ValueError("config 必须是 RbPowerControlConfig")
    payload = json.dumps(
        config.as_dict(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
