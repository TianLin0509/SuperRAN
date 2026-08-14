"""可信度验证：这批信道能不能拿来下结论。

蒙特卡洛仿真的结论只有在信道可信时才有意义。这里实现三类检查，
都是业界标定仿真器时的常规做法：

1. **对标准** —— 路损与 3GPP 38.901 公式逐点比对；CDL 时延扩展与标准剖面比对。
2. **对物理定律** —— 时频能量守恒、谱效不超容量上界、预编码方案的性能排序、
   SISO 退化到香农公式。这些是"不用查表也必须成立"的性质。
3. **对统计** —— 蒙特卡洛样本量够不够、信噪比分布是否覆盖到关心的区间。

用法：``report = full_report(dataset)``，或者单独调用某一项。
每项返回 ``passed`` 与实测偏差，**不通过时不会静默**。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

_EPS = 1e-30
_C = 299_792_458.0


@dataclass
class Check:
    """一项检查的结果。"""

    name: str
    passed: bool
    detail: str
    measured: Any = None
    expected: Any = None
    tolerance: str = ""
    severity: str = "error"  # error / warn / info

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "measured": self.measured,
            "expected": self.expected,
            "tolerance": self.tolerance,
            "severity": self.severity,
        }


# ---------------------------------------------------------------------------
# 一、对 3GPP 标准
# ---------------------------------------------------------------------------


# 38.901 Table 7.4.1-1 的适用距离范围（d_2D）。公式在此范围外未定义，
# 外推得到的值可能违反物理约束——这是实测踩到的坑，见 check_pathloss_range。
UMA_VALID_D2D_M = (10.0, 5000.0)


def pathloss_38901_uma_nlos(
    d_3d_m: float, fc_hz: float, h_ut_m: float = 1.5, *, apply_los_floor: bool = True
) -> float:
    """38.901 Table 7.4.1-1 的 UMa NLOS 路损（dB）。

    标准给的是 ``PL_NLOS = max(PL_LOS, PL'_NLOS)``，其中

        PL'_NLOS = 13.54 + 39.08·log10(d_3D) + 20·log10(fc_GHz) - 0.6·(h_UT - 1.5)
        PL_LOS   = 28.0  + 22.0 ·log10(d_3D) + 20·log10(fc_GHz)

    **那个 max 不是可选项。** NLOS 项的距离指数（39.08）比 LOS 项（22.0）大，
    所以近距离时 NLOS 公式反而给出更小的值；不取 max 会得到"非视距比视距还好"
    甚至低于自由空间的结果。

    ``apply_los_floor=False`` 可复现只算 NLOS 项的行为，用于定位差异来源。
    """
    fc_ghz = fc_hz / 1e9
    d = max(d_3d_m, 1.0)
    pl_nlos = 13.54 + 39.08 * math.log10(d) + 20.0 * math.log10(fc_ghz) - 0.6 * (h_ut_m - 1.5)
    if not apply_los_floor:
        return pl_nlos
    pl_los = 28.0 + 22.0 * math.log10(d) + 20.0 * math.log10(fc_ghz)
    return max(pl_nlos, pl_los)


def pathloss_38901_uma_los(d_3d_m: float, fc_hz: float, *, h_bs_m: float = 25.0,
                           h_ut_m: float = 1.5) -> float:
    """38.901 Table 7.4.1-1 的 UMa LOS 路损（dB），含断点距离分段。"""
    fc_ghz = fc_hz / 1e9
    d = max(d_3d_m, 1.0)
    h_e = 1.0
    d_bp = 4.0 * (h_bs_m - h_e) * max(h_ut_m - h_e, 0.5) * fc_hz / _C
    d_2d = math.sqrt(max(d**2 - (h_bs_m - h_ut_m) ** 2, 1.0))
    if d_2d <= d_bp:
        return 28.0 + 22.0 * math.log10(d) + 20.0 * math.log10(fc_ghz)
    return (
        28.0
        + 40.0 * math.log10(d)
        + 20.0 * math.log10(fc_ghz)
        - 9.0 * math.log10(d_bp**2 + (h_bs_m - h_ut_m) ** 2)
    )


def free_space_pathloss(d_m: float, fc_hz: float) -> float:
    """自由空间路损（dB）。

    注意这是**去掉阴影后**的下界。38.901 的 LOS 路损在断点内约等于自由空间，
    再叠加零均值的对数正态阴影（σ=4 dB）后，约一半视距样本会低于它——
    这是标准模型的正常行为，不是实现缺陷。所以对含阴影的实测路损
    不能硬性要求 ≥ 自由空间。
    """
    d = max(d_m, 1e-3)
    return 20.0 * math.log10(4.0 * math.pi * d * fc_hz / _C)


def check_pathloss_vs_38901(ds: Any, *, tol_db: float = 3.0) -> Check:
    """把数据集里逐样本的路损与 38.901 公式重算值比对。

    仿真器的路损含对数正态阴影衰落（UMa NLOS 的 σ 约 6 dB），所以逐样本会有
    随机偏差；这里比的是**均值偏差**，它应当接近 0——阴影是零均值的。
    """
    geo = ds.geometry
    if "pathloss_dB" not in geo or "distance_3d_m" not in geo:
        return Check("路损对标 38.901", False, "数据集缺少 pathloss_dB 或 distance_3d_m",
                     severity="warn")

    cfg = ds.config
    fc = float(cfg.get("carrier_freq_hz", 3.5e9) or 3.5e9)
    scenario = str(cfg.get("scenario", "UMa_NLOS"))
    if not scenario.startswith("UMa"):
        return Check("路损对标 38.901", True,
                     f"场景为 {scenario}，本项只针对 UMa 做逐点比对，跳过",
                     severity="info")

    pl = np.asarray(geo["pathloss_dB"], dtype=float)
    d3 = np.asarray(geo["distance_3d_m"], dtype=float)
    m = np.isfinite(pl) & np.isfinite(d3) & (d3 > 0)
    if not m.any():
        return Check("路损对标 38.901", False, "没有有效样本", severity="warn")

    # 只用落在 38.901 适用距离内的样本比对；范围外的公式本就未定义
    lo, hi = UMA_VALID_D2D_M
    in_range = m & (d3 >= lo) & (d3 <= hi)
    n_out = int(m.sum() - in_range.sum())
    if not in_range.any():
        return Check("路损对标 38.901", False,
                     f"全部 {int(m.sum())} 个样本的距离都在 38.901 适用范围 "
                     f"[{lo:g}, {hi:g}] m 之外，无法比对", severity="warn")

    # 参考公式必须复刻仿真器的选择逻辑，否则会看到几十 dB 的假偏差：
    #   · scenario 已是 _LOS 时，所有链路都用 LOS 公式；
    #   · scenario 是 _NLOS 时，逐链路按 is_los 在 NLOS/LOS 公式间切换。
    # 这两条都是实测踩出来的。
    is_los = np.asarray(geo.get("is_los", np.zeros_like(pl)), dtype=float) > 0.5
    h_bs = float(ds.config.get("tx_height_m", 25.0) or 25.0)
    scenario_is_los = scenario.endswith("_LOS")

    use_los = np.ones_like(is_los) if scenario_is_los else is_los

    parts: list[str] = []
    biases: list[float] = []
    for tag, mask, use_los_formula, sigma in (
        ("按 NLOS 公式", in_range & ~use_los, False, 6.0),
        ("按 LOS 公式", in_range & use_los, True, 4.0),
    ):
        if not mask.any():
            continue
        ref = np.array([
            pathloss_38901_uma_los(float(x), fc, h_bs_m=h_bs)
            if use_los_formula
            else pathloss_38901_uma_nlos(float(x), fc, apply_los_floor=False)
            for x in d3[mask]
        ])
        dev = pl[mask] - ref
        b, sd = float(dev.mean()), float(dev.std())
        biases.append(abs(b))
        parts.append(
            f"{tag} {int(mask.sum())} 个：偏差 {b:+.2f} dB、散布 {sd:.2f} dB（标准 σ={sigma:g}）"
        )

    if not parts:
        return Check("路损对标 38.901", False, "没有可比对的样本", severity="warn")

    # 容差要按**独立位置数**算，不是按样本数。同一个 UE 的多个样本共用一次
    # 阴影抽样，所以有效独立样本 ≈ 不同距离的个数。用 σ=6 dB、n_eff 个独立样本
    # 的均值标准误 6/√n_eff 的 3 倍作为容差——这才是统计上说得通的判据。
    n_eff = int(np.unique(np.round(d3[in_range], 3)).size)
    tol_eff = max(tol_db, 3.0 * 6.0 / math.sqrt(max(n_eff, 1)))
    ok = max(biases) <= tol_eff

    detail = "；".join(parts)
    if scenario_is_los:
        detail += "。场景已指定视距，仿真器对全部链路使用 LOS 公式"
    detail += (
        f"。独立位置 {n_eff} 个（同一 UE 的多个样本共用一次阴影抽样），"
        f"故容差取 ±{tol_eff:.1f} dB"
    )
    if n_out:
        detail += f"；另有 {n_out} 个样本超出适用距离已排除"
    return Check(
        "路损对标 38.901",
        ok,
        detail,
        measured=round(max(biases), 2),
        expected=0.0,
        tolerance=f"|偏差| ≤ {tol_eff:.1f} dB（按 {n_eff} 个独立位置的阴影标准误推算）",
    )


def check_scenario_model_consistency(ds: Any) -> Check:
    """逐样本 LOS/NLOS 状态与实际生效的 CDL/TDL 剖面是否自洽。

    配置剖面只是家族入口；当前生成器会按链路状态把 NLOS 剖面切到 D（LOS），
    或把 LOS 剖面切到 C（NLOS）。因此不能再拿配置名直接判定生成结果。
    """
    cfg = ds.config
    scenario = str(cfg.get("scenario", ""))
    configured_model = str(ds.channel_model or cfg.get("channel_model", "")).upper()
    has_effective = (
        "metastr__effective_channel_model" in ds.keys()
        or bool(ds.summary.get("effective_channel_model"))
    )
    if not scenario or not configured_model:
        return Check("场景与信道模型自洽", True, "信息不足，跳过", severity="info")

    if not has_effective:  # 老数据集：没有逐样本实际剖面，只能保留旧式静态检查。
        scen_los = scenario.endswith("_LOS")
        model_los = configured_model in ("CDL-D", "CDL-E", "TDL-D", "TDL-E")
        return Check(
            "场景与信道模型自洽",
            scen_los == model_los,
            f"老数据集没有 effective_channel_model；仅按配置检查 {scenario} + "
            f"{configured_model}。建议重新生成以获得逐样本核验。",
            measured=f"{scenario} + {configured_model}",
            expected="两者 LOS/NLOS 类别一致",
            tolerance="旧数据仅能静态判断",
            severity="warn",
        )

    models = np.asarray(ds.effective_channel_models, dtype=str)
    model_is_los = np.isin(
        np.char.upper(models), ("CDL-D", "CDL-E", "TDL-D", "TDL-E")
    )
    try:
        observed_los = np.asarray(ds.scalar("is_los"), dtype=float) > 0.5
    except KeyError:
        observed_los = np.asarray([], dtype=bool)
    if observed_los.size != models.size:
        return Check(
            "场景与信道模型自洽", False,
            f"实际剖面有 {models.size} 条，但 LOS 状态有 {observed_los.size} 条，无法逐样本对账",
            measured=f"model={models.size}, LOS={observed_los.size}",
            expected="数量相等",
            tolerance="逐样本一一对应",
        )

    mismatch = int(np.sum(model_is_los != observed_los))
    forced_los_violation = int(np.sum(~observed_los)) if scenario.endswith("_LOS") else 0
    unique, counts = np.unique(models, return_counts=True)
    profile_counts = " / ".join(f"{m}×{int(n)}" for m, n in zip(unique, counts, strict=True))
    mode_note = (
        "显式 LOS 场景"
        if scenario.endswith("_LOS")
        else "基础场景按 38.901 LOS 概率逐链路抽样"
    )
    detail = (
        f"{scenario}：{mode_note}；LOS {int(np.sum(observed_los))}/{models.size}；"
        f"实际剖面 {profile_counts}；状态与剖面匹配 {models.size - mismatch}/{models.size}"
    )
    if configured_model not in set(models.tolist()):
        detail += f"；配置入口 {configured_model} 已按链路状态自动切换"
    ok = mismatch == 0 and forced_los_violation == 0
    if forced_los_violation:
        detail += f"；显式 LOS 场景却出现 {forced_los_violation} 条 NLOS 链路"
    return Check(
        "场景与信道模型自洽", ok, detail,
        measured=f"mismatch={mismatch}, forced_los_violation={forced_los_violation}",
        expected="每条 LOS 链路用 D/E，每条 NLOS 链路用 A/B/C；显式 _LOS 不得出现 NLOS",
        tolerance="0 条不匹配",
    )


def check_cell_count(ds: Any) -> Check:
    """配置的小区数与实际生成的是否一致。

    六边形栅格按环数展开，站数只能是 1 / 7 / 19（0/1/2 环），配 2 站会实际
    生成 7 站。而 ``describe()`` 只回显配置值，不会告诉你这件事——
    用户拿着"我跑的是 6 小区"下结论，实际数据是 21 小区。

    需要精确站数用 ``topology_layout="linear"`` 或 ``custom_site_positions``。
    """
    s = ds.summary
    cfg_n = s.get("cells_configured")
    real_n = s.get("cells_actual")
    if not cfg_n or not real_n:
        return Check("小区数与配置一致", True, "数据集未记录小区数对比，跳过", severity="info")
    if int(cfg_n) == int(real_n):
        return Check(
            "小区数与配置一致", True,
            f"配置 {cfg_n} 小区，实际 {real_n} 小区",
        )
    return Check(
        "小区数与配置一致",
        False,
        s.get("topology_note")
        or f"配置 {cfg_n} 小区，实际生成 {real_n} 小区",
        measured=int(real_n),
        expected=int(cfg_n),
        tolerance="必须一致，否则干扰环境与预期不符",
    )


def check_pathloss_range(ds: Any) -> Check:
    """样本距离是否落在 38.901 公式的适用范围内。

    标准只在 10 m ≤ d_2D ≤ 5000 m 定义了 UMa/UMi 路损。撒点撒得离基站太近时，
    公式被外推，可能给出低于自由空间的荒谬值——**这不是仿真器的错，是配置的错**。
    调大站间距或设置最小接入距离即可。
    """
    geo = ds.geometry
    if "distance_3d_m" not in geo:
        return Check("距离在公式适用范围内", False, "缺少 distance_3d_m", severity="warn")
    d3 = np.asarray(geo["distance_3d_m"], dtype=float)
    d3 = d3[np.isfinite(d3)]
    if not d3.size:
        return Check("距离在公式适用范围内", False, "无有效距离", severity="warn")
    lo, hi = UMA_VALID_D2D_M
    n_near = int(np.sum(d3 < lo))
    n_far = int(np.sum(d3 > hi))
    ok = (n_near + n_far) == 0
    detail = (
        f"距离范围 {d3.min():.1f} ~ {d3.max():.1f} m，"
        f"适用区间 [{lo:g}, {hi:g}] m"
    )
    if n_near:
        detail += f"；{n_near}/{d3.size} 个样本过近，路损公式被外推，建议调大站间距"
    if n_far:
        detail += f"；{n_far}/{d3.size} 个样本过远"
    return Check(
        "距离在公式适用范围内",
        ok,
        detail,
        measured=f"{d3.min():.1f}~{d3.max():.1f} m",
        expected=f"[{lo:g}, {hi:g}] m",
        tolerance="全部样本在范围内",
        severity="warn",
    )


def check_pathloss_above_free_space(ds: Any, *, max_deficit_db: float = 5.0) -> Check:
    """去阴影后的路损与自由空间的关系是否在合理范围。

    这里**不能**硬性要求"≥ 自由空间"。38.901 的 LOS 公式是拟合式，不是严格
    物理下界：断点内 ``PL_LOS - FSPL = 2·log10(d) - 4.45``，所以 d < 168 m 时
    公式值必然低于自由空间，最多低 4.45 dB。这是标准本身的特性。

    真正该报警的是低得离谱（超过 ``max_deficit_db``），那才说明路损模型有问题。
    含阴影的实测值低于自由空间更是常态——阴影是零均值的双向扰动。
    """
    geo = ds.geometry
    if "pathloss_dB" not in geo or "distance_3d_m" not in geo:
        return Check("去阴影路损 ≥ 自由空间", False, "缺少必要字段", severity="warn")
    cfg = ds.config
    fc = float(cfg.get("carrier_freq_hz", 3.5e9) or 3.5e9)
    h_bs = float(cfg.get("tx_height_m", 25.0) or 25.0)
    d3 = np.asarray(geo["distance_3d_m"], dtype=float)
    pl = np.asarray(geo["pathloss_dB"], dtype=float)
    is_los = np.asarray(geo.get("is_los", np.zeros_like(pl)), dtype=float) > 0.5
    m = np.isfinite(d3) & (d3 > 0)
    if not m.any():
        return Check("去阴影路损 ≥ 自由空间", False, "无有效样本", severity="warn")

    scen_los = str(cfg.get("scenario", "")).endswith("_LOS")
    model = np.array([
        pathloss_38901_uma_los(float(d), fc, h_bs_m=h_bs)
        if (scen_los or los) else pathloss_38901_uma_nlos(float(d), fc, apply_los_floor=False)
        for d, los in zip(d3[m], is_los[m], strict=True)
    ])
    fspl = np.array([free_space_pathloss(float(x), fc) for x in d3[m]])
    margin = model - fspl
    worst = float(np.min(margin))
    n_excess = int(np.sum(margin < -max_deficit_db))

    shadow_below = int(np.sum(pl[m] < fspl - 0.5))
    ratio = shadow_below / max(int(m.sum()), 1)

    detail = (
        f"去阴影后最低比自由空间低 {abs(min(worst, 0.0)):.2f} dB"
        f"（38.901 LOS 拟合式在 d<168 m 处本就最多低 4.45 dB，属标准特性）；"
        f"超出 {max_deficit_db:g} dB 的样本 {n_excess}/{int(m.sum())} 个"
    )
    if shadow_below:
        detail += (
            f"。含阴影的实测路损有 {shadow_below} 个（{ratio:.0%}）低于自由空间，"
            f"这是零均值阴影的正常双向扰动，不作判据"
        )
    return Check(
        "路损与自由空间的关系合理",
        n_excess == 0,
        detail,
        measured=round(worst, 2),
        expected=f"≥ -{max_deficit_db:g} dB",
        tolerance=f"去阴影后不低于自由空间 {max_deficit_db:g} dB 以上",
    )


def check_delay_spread_vs_profile(ds: Any, *, tol_ratio: float = 0.35) -> Check:
    """PDP 实测的 RMS 时延扩展 vs 信道模型剖面的标称值。

    从频域反变换估计时延扩展有两个固有误差源，方向相反：

    * **尾部截断** —— 可观测的最大时延是 ``1/(12·SCS)``（与 RB 数无关），
      超出部分丢失，使估计偏小；
    * **分辨率粗糙** —— 时延分辨率 ``1/(RB·12·SCS)``，粗采样把能量摊到整格，
      使估计偏大。

    实测两者部分抵消：同一 CDL-C 剖面下 20 MHz 比值 1.00、100 MHz 比值 0.80。
    所以这项是**警告级**，用来发现数量级异常，而不是精确标定。
    """
    n = min(int(ds.n), 8)
    try:
        sample_nom = np.asarray(ds.scalar("sample_tau_rms_ns"), dtype=float)[:n]
    except KeyError:
        tau_nom_ns = ds.summary.get("tau_rms_ns")
        if not tau_nom_ns:
            return Check(
                "时延扩展对标剖面", False,
                "数据集既没有逐样本 sample_tau_rms_ns，也没有摘要标称时延扩展",
                severity="warn",
            )
        sample_nom = np.full(n, float(tau_nom_ns), dtype=float)

    cfg = ds.config
    scs = float(cfg.get("subcarrier_spacing", 30000) or 30000)
    rb = int(ds.summary.get("shape", {}).get("RB", 0) or 0)
    max_tau_ns = 1e9 / (12.0 * scs)
    res_ns = max_tau_ns / max(rb, 1)

    measured = np.asarray(
        [ds.pdp(i).rms_delay_spread_s * 1e9 for i in range(n)], dtype=float
    )
    max_profile_delay_ns = np.full(n, np.inf, dtype=float)
    for i in range(n):
        try:
            max_profile_delay_ns[i] = float(np.max(ds.paths(index=i).delays_s) * 1e9)
        except (NotImplementedError, KeyError, AttributeError):
            pass
    observable = max_profile_delay_ns <= max_tau_ns / 2.0
    valid = (
        np.isfinite(measured)
        & np.isfinite(sample_nom)
        & (sample_nom > 0.0)
        & observable
    )
    if not np.any(valid):
        return Check(
            "时延扩展对标剖面", True,
            f"{n} 条样本的剖面支持均超过无混叠半窗 {max_tau_ns / 2.0:.0f} ns，"
            "RB 频域观测无法可靠反演 RMS 时延扩展；跳过数值判定",
            severity="info",
        )
    ratios = measured[valid] / sample_nom[valid]
    got = float(np.median(measured[valid]))
    nom = float(np.median(sample_nom[valid]))
    ratio = float(np.median(ratios))
    q5, q95 = np.percentile(ratios, [5, 95])
    ok = abs(ratio - 1.0) <= tol_ratio

    detail = (
        f"可观测样本逐条对账 {int(np.sum(valid))}/{n}：实测中位 {got:.1f} ns，"
        f"链路标称中位 {nom:.1f} ns，比值中位 {ratio:.2f}"
        f"（5%~95% {q5:.2f}~{q95:.2f}）"
        f"（Hann 窗；无混叠半窗 {max_tau_ns / 2.0:.0f} ns、分辨率 {res_ns:.1f} ns）"
    )
    if not ok and ratio > 1.5:
        detail += (
            "。比值仍明显偏大，需检查频域采样分辨率、残余窗泄漏或信道生成的时延缩放；"
            "LOS/profile 类别已由另一项逐样本独立核验，不能在此直接归因"
        )
    return Check(
        "时延扩展对标剖面",
        ok,
        detail,
        measured=round(got, 1),
        expected=round(nom, 1),
        tolerance=f"比值在 1±{tol_ratio}（受频域观测限制，仅作数量级检查）",
        severity="warn",
    )


# ---------------------------------------------------------------------------
# 二、对物理定律
# ---------------------------------------------------------------------------


def check_parseval(ds: Any, *, tol: float = 1e-3) -> Check:
    """时频能量守恒：频域总功率 == 时延域总功率（IFFT 后乘 √RB）。

    PDP 计算依赖这一步，不守恒说明变换实现有误。
    """
    h = ds.h_true[0]
    rb = h.shape[1]
    e_freq = float(np.sum(np.abs(h) ** 2))
    h_t = np.fft.ifft(h, axis=1) * np.sqrt(rb)
    e_time = float(np.sum(np.abs(h_t) ** 2))
    rel = abs(e_freq - e_time) / max(e_freq, _EPS)
    return Check(
        "时频能量守恒",
        rel <= tol,
        f"频域 {e_freq:.6e}，时延域 {e_time:.6e}，相对误差 {rel:.2e}",
        measured=f"{rel:.2e}",
        expected="0",
        tolerance=f"≤ {tol}",
    )


def check_siso_capacity(*, snr_db: float = 15.0, tol: float = 0.02) -> Check:
    """SISO 退化检查：单发单收时容量应等于香农公式 log2(1+SNR)。

    这项不依赖数据集，是对容量实现本身的单元检查。
    """
    from .linklevel import capacity_upper_bound

    rng = np.random.default_rng(0)
    h = (rng.standard_normal((1, 8, 1, 1)) + 1j * rng.standard_normal((1, 8, 1, 1))) / np.sqrt(2)
    h = h.astype(np.complex64)
    sig = float(np.mean(np.abs(h) ** 2))
    n0 = sig / (10 ** (snr_db / 10))
    got = capacity_upper_bound(h, n0)

    # 逐 RB 的瞬时容量再平均（信道是随机的，不能直接用平均 SNR）
    per_rb = [math.log2(1 + float(abs(h[0, f, 0, 0]) ** 2) / n0) for f in range(h.shape[1])]
    ref = float(np.mean(per_rb))
    rel = abs(got - ref) / max(ref, _EPS)
    return Check(
        "SISO 容量 = 香农公式",
        rel <= tol,
        f"实现给出 {got:.4f}，香农公式 {ref:.4f} bit/s/Hz，相对误差 {rel:.2e}",
        measured=round(got, 4),
        expected=round(ref, 4),
        tolerance=f"相对误差 ≤ {tol}",
    )


def check_se_below_capacity(ds: Any, *, n: int = 5, snr_db: float = 20.0) -> Check:
    """任何预编码方案的谱效都不该超过同信道的容量上界。"""
    from .linklevel import link_performance

    worst = 1.0
    bad = 0
    k = min(int(ds.n), n)
    for i in range(k):
        r = link_performance(ds.h_true[i], snr_db=snr_db, method="svd", receiver="mmse")
        ratio = r.spectral_efficiency / max(r.capacity_bound, _EPS)
        worst = max(worst, ratio)
        if ratio > 1.001:
            bad += 1
    return Check(
        "谱效 ≤ 容量上界",
        bad == 0,
        f"{k} 个样本中 {bad} 个越界，最大比值 {worst:.4f}",
        measured=round(worst, 4),
        expected="≤ 1.0",
        tolerance="容差 0.1%",
    )


# 判定预编码偏序所需的最小样本数。样本太少时受限码本与宽带上界的
# 均值差可能被抽样噪声淹没——判定它只会制造假警报，而习惯了假警报
# 的人会连真警报一起忽略。
_ORDERING_MIN_N = 20


def check_precoder_ordering(ds: Any, *, snr_db: float = 20.0, n: int = 24) -> Check:
    """检查有物理保证的预编码偏序。

    硬关系是逐 RB SVD ≥ 宽带 SVD ≥ 受限码本。Type I 与本项目的 DFT
    波束来自不同码本，且 RI/层数不同，二者没有全局全序；把 ``TypeI ≥ DFT``
    写成门禁会把合法场景误报成物理错误。这里两者都与宽带 SVD 比，彼此只报数。

    样本数不足 ``_ORDERING_MIN_N`` 时只报数不判定：这时相邻档位的差距被抽样
    噪声淹没，判定结果没有意义。
    """
    from .linklevel import monte_carlo

    k = min(int(ds.n), n)
    ch = ds.h_true[:k]
    res = {}
    for m in ("svd", "svd_wideband", "type1", "dft"):
        res[m] = monte_carlo(ch, snr_db=snr_db, method=m).se_mean

    order = ["svd", "svd_wideband", "type1", "dft"]
    txt = (f"svd({res['svd']:.2f}) ≥ svd_wideband({res['svd_wideband']:.2f}) ≥ "
           f"max[type1({res['type1']:.2f}), dft({res['dft']:.2f})]")
    if k < _ORDERING_MIN_N:
        return Check(
            "预编码性能排序", True,
            f"仅 {k} 个样本（需 ≥{_ORDERING_MIN_N} 才判定），实测 {txt} bit/s/Hz",
            measured=[round(res[m], 3) for m in order],
            severity="info",
        )
    ok = (res["svd"] >= res["svd_wideband"] - 1e-6
          and res["svd_wideband"] >= max(res["type1"], res["dft"]) - 1e-6)
    return Check(
        "预编码性能排序",
        ok,
        f"{k} 个样本，实测 {txt} bit/s/Hz",
        measured=[round(res[m], 3) for m in order],
        expected="svd ≥ svd_wideband ≥ max(type1, dft)；type1 与 dft 无硬排序",
        tolerance="允许 1e-6 数值误差",
    )


def check_estimation_error_sane(ds: Any) -> Check:
    """信道估计误差应为负 dB（有误差但不离谱），理想模式下应趋近 -∞。"""
    nmse = np.asarray(ds.estimation_error_nmse_db(), dtype=float)
    finite = nmse[np.isfinite(nmse)]
    mode = str(ds.config.get("channel_est_mode", "?"))
    if mode == "ideal":
        ok = bool(np.all(finite < -100)) if finite.size else True
        return Check("信道估计误差合理", ok,
                     f"理想模式，NMSE 中位数 {np.median(finite) if finite.size else float('-inf'):.1f} dB",
                     severity="info")
    if not finite.size:
        return Check("信道估计误差合理", False, "无有效 NMSE", severity="warn")
    med = float(np.median(finite))
    ok = -60.0 < med < 0.0
    return Check(
        "信道估计误差合理",
        ok,
        f"估计模式 {mode}，NMSE 中位数 {med:.1f} dB",
        measured=round(med, 1),
        expected="-60 ~ 0 dB",
        tolerance="估计误差应存在但不失控",
    )


# ---------------------------------------------------------------------------
# 三、对统计
# ---------------------------------------------------------------------------


def check_monte_carlo_convergence(
    ds: Any, *, snr_db: float = 20.0, ci_target: float = 0.05, n: int | None = None
) -> Check:
    """样本量够不够：谱效均值的 95% 置信区间相对宽度是否小于目标。

    不收敛时两个方案的差异可能只是随机波动——这是蒙特卡洛最常见的错误来源。
    """
    from .linklevel import monte_carlo

    k = int(ds.n) if n is None else min(int(ds.n), n)
    r = monte_carlo(ds.h_true[:k], snr_db=snr_db, method="svd", ci_target=ci_target)
    need = None
    if not r.converged and r.se_mean > 0 and np.isfinite(r.relative_ci_width):
        # n ∝ 1/宽度^2，据此外推所需样本量
        need = int(math.ceil(k * (r.relative_ci_width / ci_target) ** 2))
    insufficient_note = (
        "；样本不足，置信区间宽度非有限，无法可靠外推所需样本量"
        if not np.isfinite(r.relative_ci_width) else ""
    )
    return Check(
        "蒙特卡洛收敛",
        r.converged,
        f"{k} 个样本，谱效 {r.se_mean:.3f} bit/s/Hz，95% 置信区间相对宽度 "
        f"{r.relative_ci_width:.1%}"
        + (f"；达到 {ci_target:.0%} 大约需要 {need} 个样本" if need else "")
        + insufficient_note,
        measured=(round(r.relative_ci_width, 4) if np.isfinite(r.relative_ci_width) else None),
        expected=f"< {ci_target}",
        tolerance=f"相对宽度 < {ci_target:.0%}",
        severity="warn",
    )


def check_sinr_distribution(ds: Any) -> Check:
    """信噪比分布是否覆盖了值得关心的区间。

    分布过窄意味着只测了一种工况，结论外推不了；这在蒙特卡洛里很常见——
    比如所有用户都撒在近点。
    """
    s = np.asarray(ds.sinr_dB, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < 2:
        return Check("信噪比分布覆盖", False, "样本太少", severity="warn")
    span = float(np.percentile(s, 95) - np.percentile(s, 5))
    ok = span >= 10.0
    return Check(
        "信噪比分布覆盖",
        ok,
        f"5%~95% 跨度 {span:.1f} dB（中位数 {np.median(s):.1f} dB）"
        + ("" if ok else "；分布偏窄，结论只对这一种工况成立"),
        measured=round(span, 1),
        expected="≥ 10 dB",
        tolerance="跨度 ≥ 10 dB",
        severity="warn",
    )


def check_interference_modeled(ds: Any) -> Check:
    """多小区配置下，小区间干扰是否真的进了 SINR。

    first-party 后端直接从服务与邻区接收功率形成同口径 SNR/SIR/SINR。若来源
    在多小区配置下仍返回无干扰哨兵 ``sir_dB=49.9``，或 SINR 与 SNR 逐点相同，
    就说明邻区功率没有进入预算。干扰相关结论此时全部无效。
    """
    cells = int(ds.config.get("num_sites", 1) or 1) * int(
        ds.config.get("sectors_per_site", 1) or 1
    )
    if cells <= 1:
        return Check(
            "干扰是否进入 SINR", True, f"单小区配置（{cells} 小区），无小区间干扰，不适用",
            severity="info",
        )

    sinr = np.asarray(ds.sinr_dB, dtype=float)
    snr = np.asarray(ds.snr_dB, dtype=float)
    try:
        sir = np.asarray(ds.scalar("sir_dB"), dtype=float)
    except KeyError:
        sir = np.full_like(sinr, np.nan)

    same = bool(sinr.size and snr.size == sinr.size and np.allclose(sinr, snr, atol=1e-6))
    sentinel = bool(np.isfinite(sir).any() and np.allclose(sir[np.isfinite(sir)], 49.9))
    ok = not (same or sentinel)

    why = []
    if same:
        why.append("SINR 与纯热噪声 SNR 逐点相同")
    if sentinel:
        why.append("SIR 恒为 49.9 dB（兜底哨兵值）")
    detail = (
        f"{cells} 小区，干扰已进入 SINR"
        f"（SINR 中位 {np.nanmedian(sinr):.1f} dB vs SNR 中位 {np.nanmedian(snr):.1f} dB）"
        if ok
        else (
            f"{cells} 小区但 " + "、".join(why) + "。报出的 SINR 实为单小区 SNR，"
            "干扰相关结论不成立。请检查来源版本、实际小区数与 rx_power_all_dbm；"
            "用当前 first-party 后端重新生成。"
        )
    )
    return Check(
        "干扰是否进入 SINR", ok, detail,
        # 主门只报告“是否退化”，IoT 数值统一由 check_iot_sane / interference
        # 输出。当前 first-party 的 snr-sinr 可作等价旁证，但不是跨源契约。
        measured=("SINR == SNR" if same else "SINR != SNR"),
        expected="SINR != SNR（干扰使其下降）",
        tolerance="SINR 与 SNR 不得逐点相同",
    )


def check_antenna_model(ds: Any) -> Check:
    """基站阵列模型是不是本地真实硬件。

    已确认两个真实 AAU：64T 是 8H x 4V x 2pol / 1 驱 3 / 192 AE；
    256T 是 16H x 8V x 2pol / 1 驱 6 / 1536 AE。二者水平 0.5λ、
    物理垂直 **0.67λ**，并统一 ``pol_h_v + top_to_bottom``。
    ChannelHub 的 ``legacy_64`` 是独立端口历史基线，不是这两款硬件。

    实测同 seed 单小区 30 样本：legacy 的 SVD 谱效 33.23 vs 真实 28.20，
    吞吐 1337.5 vs 1055.5 Mbps，边缘用户 940.0 vs 582.4 Mbps——
    以上仅是 2026-07-31 旧内核历史消融，不能作为当前通用百分比。

    这一项是 warn 不是 error：16T 等未确认面板不能猜 1 驱 N，legacy 合理；
    显式指定 legacy 做对照也合理。但公司 64T/256T 拿 legacy 报结果必须被看见。
    """
    from . import hardware as hw  # noqa: PLC0415

    summary = getattr(ds, "summary", {}) or {}
    block = summary.get("antenna_model") or {}
    mode = block.get("antenna_model_mode")
    panel = block.get("bs_panel") or ds.config.get("bs_panel")

    if not mode:
        return Check(
            "基站阵列模型", False,
            "数据集没有记录 antenna_model —— 2026-07-31 之前生成的都没有这一项，"
            "它们按 legacy_64 独立阵元解释。旧内核消融曾测到吞吐 +27%，"
            "但该百分比不能外推到当前版本；请重新成对生成。",
            measured=None, expected="effective_subarray（64T 面板）",
            severity="warn",
        )

    is_company = hw.is_company_panel(panel)
    expected_m = 3 if list(panel or []) == hw.COMPANY_RF_PANEL else 6
    if mode != "legacy_64":
        detail = (
            f"{block.get('elements_per_rf_port')} 驱动/端口、"
            f"{block.get('physical_elements')} 物理阵子、"
            f"水平 {block.get('horizontal_spacing_lambda')}λ / "
            f"垂直 {block.get('ae_vertical_spacing_lambda')}λ"
            f"（RF 端口垂直 {block.get('rf_vertical_spacing_lambda')}λ）"
        )
        ok = (
            abs(float(block.get("ae_vertical_spacing_lambda") or 0) - 0.67) < 1e-9
            if is_company else True
        )
        if is_company:
            ok = ok and int(block.get("elements_per_rf_port") or 0) == expected_m
            ok = ok and block.get("port_order") == hw.COMPANY_CANONICAL_PORT_ORDER
            ok = ok and (
                block.get("vertical_index_order")
                == hw.COMPANY_CANONICAL_VERTICAL_INDEX_ORDER
            )
        if not ok:
            detail += (
                " —— **公司阵列合同不匹配**：应为垂直 0.67λ、"
                f"1 驱 {expected_m}、pol_h_v + top_to_bottom")
        return Check("基站阵列模型", ok, detail,
                     measured=mode, expected="effective_subarray",
                     tolerance="公司 64T/256T 必须匹配馈电数、垂直间距和 canonical 端口布局",
                     severity="warn")

    if not is_company:
        return Check(
            "基站阵列模型", True,
            f"面板 {panel} 的馈电结构未确认，legacy_64 是可接受的独立端口基线",
            measured=mode, severity="info",
        )
    return Check(
        "基站阵列模型", False,
        f"公司面板 {panel} 却用了 legacy_64 独立阵元模型。"
        f"当前硬件合同应为 1 驱 {expected_m}、垂直 0.67λ、"
        "pol_h_v + top_to_bottom；只有显式历史兼容/消融实验才应这样做。",
        measured=mode, expected="effective_subarray",
        severity="warn",
    )


def check_iot_sane(ds: Any) -> Check:
    """IoT（噪声抬升）的物理自洽性。

    三条硬约束，每条都是物理上必须成立的：

    1. 逐样本 ``SIR > SINR``——SINR = S/(I+N) 一定比 SIR = S/I 小，因为分母多了
       热噪声。反过来只可能是夹逼或口径错配。
    2. ``IoT >= 0 dB``——噪声抬升不可能为负。
    3. 不可信样本（贴 ±50 dB 边界、49.9 哨兵）占比不能过半，否则这批数据的
       干扰结论建立在夹逼过的数上。

    通过之后顺带把 IoT 中位数与等级报出来，省得再调一次报告。
    """
    from . import interference as itf  # noqa: PLC0415

    cells = int(ds.config.get("num_sites", 1) or 1) * int(
        ds.config.get("sectors_per_site", 1) or 1
    )
    if cells <= 1:
        return Check("IoT 自洽性", True, f"单小区配置（{cells} 小区），无 IoT 可言",
                     severity="info")
    try:
        sinr = np.asarray(ds.sinr_dB, dtype=float)
        sir = np.asarray(ds.scalar("sir_dB"), dtype=float)
    except KeyError:
        return Check("IoT 自洽性", False, "数据集缺 sir_dB，算不出 IoT", severity="warn")

    st = itf.iot_stats(sinr, sir)
    both = np.isfinite(sinr) & np.isfinite(sir)
    violations = int(np.sum(both & (sir <= sinr)))
    untrusted = st.n_clamped + st.n_no_interferer
    frac_untrusted = untrusted / max(st.n_total, 1)

    ok = violations == 0 and st.n_valid > 0 and frac_untrusted <= 0.5
    if st.n_valid:
        ok = ok and st.median_db >= -1e-6

    parts = [f"{st.n_valid}/{st.n_total} 个样本可算 IoT"]
    if st.n_valid:
        cls = itf.classify_iot(st.median_db)
        parts.append(
            f"中位数 {st.median_db:.1f} dB（{cls['band']}，等效负载 {cls['equivalent_load']}）"
        )
        parts.append(f"5%~95% {st.p5_db:.1f}~{st.p95_db:.1f} dB")
    if violations:
        parts.append(f"**{violations} 个样本 SIR <= SINR，物理上不可能**")
    if untrusted:
        parts.append(f"{untrusted} 个样本不可信（贴边 {st.n_clamped} / 无干扰源 {st.n_no_interferer}）")

    return Check(
        "IoT 自洽性", ok, "；".join(parts),
        measured=round(st.median_db, 2) if st.n_valid else None,
        expected="SIR > SINR 逐样本成立，IoT >= 0 dB",
        tolerance="不可信样本 <= 50%",
    )


def check_cdl_table_vs_38901(ds: Any, *, tol: float = 0.05) -> Check:
    """所用 CDL 剖面的查表值是否与 38.901 原表一致。

    仿真器内部那份剖面表抄错了不会报错——时延、功率、角度都还是"合理"的数，
    只是不再是标准剖面。用一份独立录入的标准表（``spec38901``）逐簇对，
    是唯一能发现这类错误的办法。

    严重度按**功率加权**给：只有末尾几个弱簇不符，影响有限；如果不符的簇
    加起来占了大半功率，那这个剖面的名字就名不副实了。
    """
    from . import spec38901

    models = {
        str(model).upper() for model in getattr(ds, "effective_channel_models", [])
        if str(model).upper().startswith("CDL-")
    }
    configured = str(getattr(ds, "channel_model", "") or "").upper()
    if not models and configured.startswith("CDL-"):
        models = {configured}
    covered_models = sorted(model for model in models if model in spec38901.COVERED)
    skipped_models = sorted(models - set(covered_models))
    if not covered_models:
        return Check(
            "CDL 剖面对标 38.901", True,
            f"{'/'.join(skipped_models) or configured or '未知剖面'} 未录入标准表"
            f"（本模块只覆盖 {'/'.join(spec38901.COVERED)}），跳过",
            severity="info",
        )

    from .channelhub import cdl_profile
    failures: list[str] = []
    passed: list[str] = []
    max_share = 0.0
    for model in covered_models:
        spec = spec38901.as_arrays(model)
        prof = cdl_profile(model)
        n = len(spec["powers_dB"])
        impl_n = len(np.asarray(prof.powers_dB, dtype=float))
        if impl_n != n:
            failures.append(f"{model} 簇数：标准 {n}、实现 {impl_n}")
            max_share = 1.0
            continue
        bad = np.zeros(n, dtype=bool)
        worst: list[str] = []
        for profile_field in (
            "delays_norm", "powers_dB", "aod_deg", "aoa_deg", "zod_deg", "zoa_deg"
        ):
            delta = np.abs(
                spec[profile_field]
                - np.asarray(getattr(prof, profile_field), dtype=float)
            )
            hit = delta > tol
            bad |= hit
            if hit.any():
                worst.append(
                    f"{profile_field} {int(hit.sum())} 簇（最大差 {delta.max():.1f}）"
                )
        power = 10.0 ** (spec["powers_dB"] / 10.0)
        power = power / power.sum()
        share = float(power[bad].sum())
        max_share = max(max_share, share)
        if bad.any():
            failures.append(
                f"{model}：{int(bad.sum())}/{n} 簇有出入，占功率 {share:.1%}；"
                + "；".join(worst)
            )
        else:
            passed.append(f"{model}（{spec38901.CDL_TABLES[model]['table']}）")

    ok = not failures
    skipped_note = f"；未覆盖 {'/'.join(skipped_models)}" if skipped_models else ""
    return Check(
        "CDL 剖面对标 38.901", ok,
        (
            f"实际生效剖面 {' / '.join(passed)} 均逐簇一致{skipped_note}"
            if ok
            else (
                f"实际生效剖面与标准不符：{'；'.join(failures)}。"
                f"时延与功率对得上、只有角度不符时，PDP 类结论仍可用，"
                f"但空间相关性、波束赋形增益、到达角估计这类依赖角度的结论会偏。"
            )
        ),
        measured=round(max_share, 4),
        expected="0（逐簇一致）",
        tolerance=f"逐项差 ≤ {tol}",
        severity="error" if max_share > 0.10 else "warn",
    )


def check_angular_spread_vs_spec(ds: Any, *, tol_deg: float = 3.0) -> Check:
    """角度扩展（Annex A.1 圆周定义）与 38.901 原表算出的值是否一致。

    这是 ``check_cdl_table_vs_38901`` 的"后果"版本：逐簇差异最终体现为多大的
    角度扩展偏差，直接对应波束宽度与空间相关性的偏差量级。
    """
    from . import spec38901
    from .calibration import circular_angular_spread_rad

    effective = np.asarray(getattr(ds, "effective_channel_models", []), dtype=str)
    model_to_index: dict[str, int] = {}
    for index, raw_model in enumerate(effective):
        model = str(raw_model).upper()
        if model in spec38901.COVERED and model not in model_to_index:
            model_to_index[model] = index
    if not model_to_index:
        model = str(getattr(ds, "channel_model", "") or "").upper()
        if model in spec38901.COVERED:
            model_to_index[model] = 0
    if not model_to_index:
        return Check(
            "角度扩展对标 38.901", True,
            f"{str(getattr(ds, 'channel_model', '') or '未知剖面').upper()} 无 CDL 标准表，跳过",
            severity="info",
        )

    pairs = (("ASD", "aod_deg", "aod_rad"), ("ASA", "aoa_deg", "aoa_rad"),
             ("ZSD", "zod_deg", "zod_rad"), ("ZSA", "zoa_deg", "zoa_rad"))

    lines: list[str] = []
    worst = 0.0
    try:
        for model, index in sorted(model_to_index.items()):
            st = ds.paths(index=index)
            spec = spec38901.as_arrays(model)
            pw_spec = 10.0 ** (spec["powers_dB"] / 10.0)
            profile_lines: list[str] = []
            for label, sf, df in pairs:
                want = math.degrees(
                    circular_angular_spread_rad(np.radians(spec[sf]), pw_spec)
                )
                got = math.degrees(
                    circular_angular_spread_rad(getattr(st, df), st.powers_linear)
                )
                diff = got - want
                worst = max(worst, abs(diff))
                profile_lines.append(
                    f"{label} {want:.1f}°→{got:.1f}°（{diff:+.1f}°）"
                )
            lines.append(f"{model}：" + "，".join(profile_lines))
    except (NotImplementedError, KeyError, AttributeError) as exc:
        return Check(
            "角度扩展对标 38.901", True, f"取不到多径角度：{exc}", severity="info"
        )

    ok = worst <= tol_deg
    return Check(
        "角度扩展对标 38.901", ok,
        "；".join(lines) + ("" if ok else "。角度扩展决定波束宽度与空间相关性，偏差会传导到预编码增益"),
        measured=round(worst, 2),
        expected=f"|差| ≤ {tol_deg}°",
        tolerance=f"Annex A.1 圆周定义，容差 {tol_deg}°",
        severity="warn",
    )


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "error")

    def as_dict(self) -> dict[str, Any]:
        errs = [c for c in self.checks if c.severity == "error"]
        warns = [c for c in self.checks if c.severity == "warn"]
        return {
            "passed": self.passed,
            "n_checks": len(self.checks),
            "n_failed_error": sum(1 for c in errs if not c.passed),
            "n_failed_warn": sum(1 for c in warns if not c.passed),
            "checks": [c.as_dict() for c in self.checks],
            "summary": (
                "全部硬性检查通过" if self.passed else "存在未通过的硬性检查，结论不可信"
            ),
        }

    def text(self) -> str:
        lines = []
        for c in self.checks:
            mark = "PASS" if c.passed else ("WARN" if c.severity != "error" else "FAIL")
            lines.append(f"[{mark}] {c.name}\n       {c.detail}")
        lines.append("")
        lines.append(self.as_dict()["summary"])
        return "\n".join(lines)


def full_report(ds: Any, *, snr_db: float = 20.0) -> ValidationReport:
    """跑全部检查。生成数据后调一次，确认这批数据能不能拿来下结论。"""
    checks = [
        check_pathloss_vs_38901(ds),
        check_cdl_table_vs_38901(ds),
        check_angular_spread_vs_spec(ds),
        check_scenario_model_consistency(ds),
        check_cell_count(ds),
        check_interference_modeled(ds),
        check_iot_sane(ds),
        check_antenna_model(ds),
        check_pathloss_range(ds),
        check_pathloss_above_free_space(ds),
        check_delay_spread_vs_profile(ds),
        check_parseval(ds),
        check_siso_capacity(),
        check_se_below_capacity(ds, snr_db=snr_db),
        check_precoder_ordering(ds, snr_db=snr_db),
        check_estimation_error_sane(ds),
        check_monte_carlo_convergence(ds, snr_db=snr_db),
        check_sinr_distribution(ds),
    ]
    return ValidationReport(checks=checks)
