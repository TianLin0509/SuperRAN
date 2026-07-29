"""3GPP TR 38.901 §7.8 口径的信道模型校准量。

业界判断"信道生成得对不对"的标准做法不是看几条曲线好不好看，而是**按标准
规定的口径算出指定的几个统计量，跟各公司提交的参考曲线对**。38.901 §7.8 把
这件事写死了，分三档：

* **§7.8.1 大尺度校准**（Table 7.8-1，参考结果 R1-165974）——不建模快衰落，只对两项：

  1. ``Coupling loss – serving cell (based on LOS pathloss)``
  2. ``Geometry (based on LOS pathloss) with and without white noise``

* **§7.8.2 全校准**（Table 7.8-2，参考结果 R1-165975）——加上快衰落，四项：

  1. ``Coupling loss – serving cell``
  2. ``Wideband SIR before receiver without noise``
  3. ``CDF of Delay Spread and Angle Spread (ASD, ZSD, ASA, ZSA) from the serving
     cell (according to circular angle spread definition of TR 25.996)``
  4. ``CDF of largest (1st) / smallest (2nd) PRB singular values ... and the ratio
     ... plotted in 10*log10 scale``；并注明
     ``The PRB singular values of a PRB are the eigenvalues of the mean covariance
     matrix in the PRB``

* **§7.8.4 室内工厂**（Table 7.8-7，参考结果 R1-1909704）——再加首径附加时延。

角度扩展按 Annex A.1 的**圆周标准差**定义（不是普通标准差）::

    AS = sqrt( −2·ln| Σ_n Σ_m P_{n,m}·exp(j·φ_{n,m}) / Σ_n Σ_m P_{n,m} | )

这里实现的是"按标准口径把数算出来"。**参考曲线本身是 3GPP 的会议文稿（R1-…），
不在本模块内**——所以除了少数能自洽判定的项，其余只出数并标注该对哪份文稿，
不假装有判据。哪一项适用取决于信道怎么生成的：CDL 剖面的时延/角度是查表得来的
固定值，它的 CDF 是退化的，这时报点值并说明，而不是画一条假 CDF。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

_EPS = 1e-30

# 3GPP 校准曲线惯用的分位点
_PCTS = (5, 10, 50, 90, 95)


@dataclass
class Metric:
    """一个按 38.901 口径算出来的校准量。"""

    name: str
    clause: str
    unit: str
    values: np.ndarray | None = None
    applicable: bool = True
    reference: str = ""
    note: str = ""

    @property
    def percentiles(self) -> dict[str, float]:
        if self.values is None:
            return {}
        v = np.asarray(self.values, dtype=float)
        v = v[np.isfinite(v)]
        if v.size == 0:
            return {}
        return {f"p{p}": round(float(np.percentile(v, p)), 2) for p in _PCTS}

    @property
    def n(self) -> int:
        if self.values is None:
            return 0
        v = np.asarray(self.values, dtype=float)
        return int(np.isfinite(v).sum())

    @property
    def spread(self) -> float:
        """5%~95% 跨度。退化分布（如 CDL 的固定时延扩展）会接近 0。"""
        p = self.percentiles
        if not p:
            return 0.0
        return round(p["p95"] - p["p5"], 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "clause": self.clause,
            "unit": self.unit,
            "applicable": self.applicable,
            "n": self.n,
            "percentiles": self.percentiles,
            "spread_p5_p95": self.spread,
            "reference": self.reference,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# 指标 1：耦合损耗
# ---------------------------------------------------------------------------


def coupling_loss_db(ds: Any) -> Metric:
    """服务小区耦合损耗（dB），38.901 §7.8.1/§7.8.2 指标 1。

    耦合损耗 = 发射功率 − 接收功率 = 路损 − 天线增益（收发两端）。
    它把路损模型、天线方向图、下倾角、小区选择规则**串起来一起考**，
    所以是最基础也最灵敏的一项：任何一环错了这条 CDF 都会整体平移。
    """
    tx = np.asarray(ds.scalar("tx_power_dbm"), dtype=float)
    rx = np.asarray(ds.scalar("rx_power_serving_dbm"), dtype=float)
    return Metric(
        name="耦合损耗（服务小区）",
        clause="38.901 §7.8.1 指标1 / §7.8.2 指标1",
        unit="dB",
        values=tx - rx,
        reference="R1-165974（大尺度）/ R1-165975（全校准）",
        note="= 发射功率 − 接收功率 = 路损 − 天线增益。串联检验路损模型 + 天线方向图 + 小区选择。",
    )


# ---------------------------------------------------------------------------
# 指标 2：几何（宽带 SINR / SIR）
# ---------------------------------------------------------------------------


def geometry_db(ds: Any, *, with_noise: bool = True) -> Metric:
    """几何量（dB），38.901 §7.8.1 指标 2 / §7.8.2 指标 2。

    标准要求出两条：``with and without white noise``。不含噪声的那条就是宽带
    SIR，只反映网络拓扑与干扰；含噪声的是宽带 SINR。

    **这项是查"干扰到底进没进计算"最直接的手段。** 单小区时 SIR 无定义；
    多小区时如果 SINR 与纯热噪声 SNR 逐点相同，说明干扰根本没算进去。
    """
    key = "sinr_dB" if with_noise else "sir_dB"
    v = np.asarray(ds.scalar(key), dtype=float)
    snr = np.asarray(ds.scalar("snr_dB"), dtype=float)
    finite = np.isfinite(v)

    note = "含白噪声（宽带 SINR）" if with_noise else "不含噪声（宽带 SIR）"
    applicable = bool(finite.any())
    if not applicable:
        note += "；本数据集无该字段（单小区无干扰时 SIR 无定义）"
    elif with_noise and snr.size == v.size and np.allclose(v[finite], snr[finite], atol=1e-6):
        applicable = False
        note += "；**与纯热噪声 SNR 逐点相同 —— 干扰未进入计算**"
    elif not with_noise and np.allclose(v[finite], 49.9):
        applicable = False
        note += "；**恒为 49.9 dB —— 这是 ChannelHub 的兜底哨兵值，几何 SINR 未被计算**"

    return Metric(
        name=f"几何量（{'含噪声' if with_noise else '不含噪声'}）",
        clause="38.901 §7.8.1 指标2 / §7.8.2 指标2",
        unit="dB",
        values=v,
        applicable=applicable,
        reference="R1-165974 / R1-165975",
        note=note,
    )


# ---------------------------------------------------------------------------
# 指标 3a：时延扩展
# ---------------------------------------------------------------------------


def delay_spread_ns(ds: Any) -> Metric:
    """均方根时延扩展（ns），38.901 §7.8.2 指标 3。

    优先取仿真器逐样本给的 ``sample_tau_rms_ns``（由实际信道实现算出），
    而不是配置里的标称值 ``tau_rms_ns``。

    CDL 的时延表是**查表固定**的，所以标称值在整个数据集里是常数；逐样本值
    的离散只来自衰落实现与估计噪声，不是 38.901 系统级模型那种按 Table 7.5-6
    对数正态抽样得来的。这条 CDF 不能拿去跟 R1-165975 的曲线直接比——
    比的话应该比标称值是否等于该 CDL 剖面的表值（见 ``validate.check_delay_spread_vs_profile``）。
    """
    try:
        v = np.asarray(ds.scalar("sample_tau_rms_ns"), dtype=float)
    except KeyError:
        v = None
    nominal = None
    try:
        nom = np.asarray(ds.scalar("tau_rms_ns"), dtype=float)
        nominal = float(np.nanmedian(nom))
    except KeyError:
        pass

    is_cdl = str(getattr(ds, "channel_model", "") or "").upper().startswith(("CDL", "TDL"))
    note = f"标称值 {nominal:.1f} ns（配置）" if nominal is not None else ""
    if is_cdl:
        note += "；CDL/TDL 的时延表固定，此 CDF 的离散只来自衰落与估计噪声，不可与 R1-165975 曲线直接比"

    return Metric(
        name="时延扩展",
        clause="38.901 §7.8.2 指标3",
        unit="ns",
        values=v,
        applicable=v is not None and not is_cdl,
        reference="R1-165975",
        note=note,
    )


# ---------------------------------------------------------------------------
# 指标 3b：角度扩展（Annex A.1 圆周定义）
# ---------------------------------------------------------------------------


def circular_angular_spread_rad(angles_rad: np.ndarray, powers_linear: np.ndarray) -> float:
    """38.901 Annex A.1 的圆周角度扩展（弧度）。

    ::

        AS = sqrt( −2·ln| Σ P_{n,m}·exp(j·φ_{n,m}) / Σ P_{n,m} | )

    **不是**角度的普通标准差。普通标准差在角度绕回 0/360° 时会算出荒谬的大值；
    圆周定义把角度放到单位圆上做功率加权矢量和，天然免疫绕回。这也是标准
    在 §7.8.2 指标 3 里特意写明 "according to circular angle spread definition
    of TR 25.996" 的原因。
    """
    a = np.asarray(angles_rad, dtype=float).ravel()
    p = np.asarray(powers_linear, dtype=float).ravel()
    if a.size == 0 or p.sum() <= 0:
        return float("nan")
    r = np.abs(np.sum(p * np.exp(1j * a)) / p.sum())
    r = min(max(r, _EPS), 1.0)
    # sqrt(−2·ln r) 在 r→1 附近会放大浮点误差：r 差 ε，结果差 √(2ε)——
    # 平方根放大。角度完全集中时 r 本该恰为 1，实际会是 1−1e-16，
    # 于是算出 ~1e-8 rad 而不是 0。判据别写成"等于 0"，写成"小于 1e-6 rad"。
    return float(math.sqrt(-2.0 * math.log(r)))


_ANGLE_KEYS = {"ASD": "aod_rad", "ASA": "aoa_rad", "ZSD": "zod_rad", "ZSA": "zoa_rad"}


def angular_spread_deg(ds: Any, kind: str = "ASA") -> Metric:
    """角度扩展（度），38.901 §7.8.2 指标 3。

    ``kind`` ∈ {ASD, ASA, ZSD, ZSA}。用 ``ds.paths()`` 给出的多径角度与功率，
    按 Annex A.1 计算。

    射线追踪数据没有 CDL 意义上的"剖面角度"，``loader.paths()`` 会直接抛异常，
    这里如实标为不适用而不是编一组假角度。
    """
    kind = kind.upper()
    if kind not in _ANGLE_KEYS:
        raise ValueError(f"kind 应为 {sorted(_ANGLE_KEYS)} 之一，收到 {kind!r}")

    try:
        st = ds.paths()
    except (NotImplementedError, KeyError, AttributeError) as exc:
        return Metric(
            name=f"角度扩展 {kind}",
            clause="38.901 §7.8.2 指标3 / Annex A.1",
            unit="deg",
            applicable=False,
            reference="R1-165975",
            note=f"无法取得多径角度：{exc}",
        )

    ang = getattr(st, _ANGLE_KEYS[kind])
    pw = getattr(st, "powers_linear")
    as_rad = circular_angular_spread_rad(ang, pw)
    return Metric(
        name=f"角度扩展 {kind}",
        clause="38.901 §7.8.2 指标3 / Annex A.1",
        unit="deg",
        values=np.asarray([math.degrees(as_rad)]),
        applicable=True,
        reference="R1-165975",
        note=(
            f"由 {st.num_paths} 条径按 Annex A.1 圆周定义算出，是该 CDL 剖面的**点值**"
            f"（剖面角度查表固定，无 CDF）"
        ),
    )


# ---------------------------------------------------------------------------
# 指标 4：PRB 奇异值
# ---------------------------------------------------------------------------


@dataclass
class SingularValueMetrics:
    """38.901 §7.8.2 指标 4 的三条 CDF。"""

    largest_db: np.ndarray
    smallest_db: np.ndarray
    ratio_db: np.ndarray
    absolute: bool
    n_streams: int
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        def pct(v: np.ndarray) -> dict[str, float]:
            v = np.asarray(v, dtype=float)
            v = v[np.isfinite(v)]
            if v.size == 0:
                return {}
            return {f"p{p}": round(float(np.percentile(v, p)), 2) for p in _PCTS}

        return {
            "clause": "38.901 §7.8.2 指标4",
            "reference": "R1-165975",
            "absolute_scale": self.absolute,
            "n_streams": self.n_streams,
            "largest_db": pct(self.largest_db),
            "smallest_db": pct(self.smallest_db),
            "ratio_db": pct(self.ratio_db),
            "note": self.note,
        }


def prb_singular_values_db(
    ds: Any, *, max_samples: int = 200, absolute: bool = True
) -> SingularValueMetrics:
    """PRB 奇异值的三条 CDF，38.901 §7.8.2 指标 4。

    标准的原话是 ``The PRB singular values of a PRB are the eigenvalues of the
    mean covariance matrix in the PRB``，且 ``at t=0``、``plotted in 10*log10 scale``。
    所以这里对每个 RB 取 ``R = H^H·H`` 的特征值 λ，报 ``10·log10(λ)``——
    注意是特征值不是奇异值本身，两者差一个平方（即 20log10 与 10log10 的区别）。

    ``absolute=True`` 时把耦合损耗折算回去。因为落盘的 ``h_true`` 是归一化的
    （每元素平均功率 ≈ 1，路损单独存在 meta 里），不折算的话最大奇异值那条曲线
    只反映阵列增益，与 R1-165975 的绝对电平差一个耦合损耗。**比值那条不受影响**，
    它是尺度无关的，也因此是三条里最稳的判据。
    """
    h = np.asarray(ds.h_true)
    n = min(int(h.shape[0]), int(max_samples))
    cl = None
    if absolute:
        try:
            cl = np.asarray(ds.scalar("tx_power_dbm"), dtype=float) - np.asarray(
                ds.scalar("rx_power_serving_dbm"), dtype=float
            )
        except KeyError:
            absolute = False

    largest: list[float] = []
    smallest: list[float] = []
    ratio: list[float] = []
    n_streams = 0

    for i in range(n):
        hi = h[i][0]  # t = 0，标准明确要求
        # hi: [RB, BS_ant, UE_ant]；R = H^H H 的特征值即各流增益
        r = np.einsum("rba,rbc->rac", hi.conj(), hi)  # [RB, UE, UE]
        lam = np.linalg.eigvalsh(r)  # 升序实特征值
        lam = np.maximum(lam[:, ::-1], _EPS)  # 降序
        n_streams = lam.shape[1]
        lam_db = 10.0 * np.log10(lam)
        if absolute and cl is not None:
            lam_db = lam_db - cl[i]
        largest.extend(lam_db[:, 0].tolist())
        smallest.extend(lam_db[:, min(1, n_streams - 1)].tolist())
        ratio.extend((lam_db[:, 0] - lam_db[:, min(1, n_streams - 1)]).tolist())

    note = (
        "已折算耦合损耗，为绝对电平" if absolute else "归一化信道，仅相对电平；比值仍可比"
    )
    return SingularValueMetrics(
        largest_db=np.asarray(largest),
        smallest_db=np.asarray(smallest),
        ratio_db=np.asarray(ratio),
        absolute=bool(absolute),
        n_streams=n_streams,
        note=note + f"；第 2 大取第 2 个特征值（共 {n_streams} 流）",
    )


# ---------------------------------------------------------------------------
# 跨引擎交叉验证
# ---------------------------------------------------------------------------


def ks_statistic(a: np.ndarray, b: np.ndarray) -> float:
    """两样本 Kolmogorov–Smirnov 统计量 D（两条经验 CDF 的最大垂直距离）。"""
    a = np.sort(np.asarray(a, dtype=float)[np.isfinite(a)])
    b = np.sort(np.asarray(b, dtype=float)[np.isfinite(b)])
    if a.size == 0 or b.size == 0:
        return float("nan")
    grid = np.concatenate([a, b])
    fa = np.searchsorted(a, grid, side="right") / a.size
    fb = np.searchsorted(b, grid, side="right") / b.size
    return float(np.max(np.abs(fa - fb)))


def ks_critical(n_a: int, n_b: int, alpha: float = 0.05) -> float:
    """KS 检验在给定显著性下的临界值。D 超过它就认为两分布不同。"""
    c = {0.10: 1.22, 0.05: 1.36, 0.01: 1.63}.get(alpha, 1.36)
    if n_a <= 0 or n_b <= 0:
        return float("nan")
    return c * math.sqrt((n_a + n_b) / (n_a * n_b))


def cross_engine_compare(
    ds_a: Any, ds_b: Any, *, metrics: tuple[str, ...] = ("coupling_loss", "geometry")
) -> dict[str, Any]:
    """两个引擎（如 internal_sim 与 quadriga_real）同配置结果的分布一致性。

    交叉验证是校准之外最有力的一招：QuaDRiGa 本身是独立通过 38.901 校准的实现，
    两个各自独立写出来的仿真器在同一配置下给出同分布，比任何单边自查都更有说服力。

    判据用 KS 检验。**注意样本量大时 KS 极易显著**——分布只要有一点点系统差异，
    几百个样本就足以拒绝原假设。所以除了 D 与临界值，这里同时报中位数之差，
    由使用者判断差异有没有工程意义。
    """
    out: dict[str, Any] = {"metrics": {}}
    getters = {
        "coupling_loss": coupling_loss_db,
        "geometry": lambda d: geometry_db(d, with_noise=True),
        "geometry_no_noise": lambda d: geometry_db(d, with_noise=False),
        "delay_spread": delay_spread_ns,
    }
    for m in metrics:
        if m not in getters:
            continue
        ma, mb = getters[m](ds_a), getters[m](ds_b)
        if ma.values is None or mb.values is None:
            out["metrics"][m] = {"comparable": False, "reason": "至少一边没有该量"}
            continue
        d = ks_statistic(ma.values, mb.values)
        crit = ks_critical(ma.n, mb.n)
        med_a = float(np.nanmedian(ma.values))
        med_b = float(np.nanmedian(mb.values))
        out["metrics"][m] = {
            "comparable": True,
            "ks_D": round(d, 4),
            "ks_critical_0.05": round(crit, 4),
            "same_distribution": bool(d <= crit),
            "median_a": round(med_a, 2),
            "median_b": round(med_b, 2),
            "median_diff": round(med_a - med_b, 2),
            "n_a": ma.n,
            "n_b": mb.n,
        }
    return out


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


@dataclass
class CalibrationReport:
    metrics: list[Metric] = field(default_factory=list)
    singular_values: SingularValueMetrics | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        n_app = sum(1 for m in self.metrics if m.applicable)
        return {
            "standard": "3GPP TR 38.901 §7.8",
            "context": self.context,
            "n_metrics": len(self.metrics),
            "n_applicable": n_app,
            "metrics": [m.as_dict() for m in self.metrics],
            "singular_values": (
                self.singular_values.as_dict() if self.singular_values else None
            ),
            "how_to_use": (
                "把这里的分位点与 3GPP 会议文稿 R1-165974（大尺度）、R1-165975（全校准）、"
                "R1-1909704（InF）里各公司提交的 CDF 曲线对照。标为不适用的项说明了原因，"
                "不要拿去比。"
            ),
        }

    def text(self) -> str:
        lines = [f"3GPP TR 38.901 §7.8 校准量（{self.context.get('dataset_id', '')}）", ""]
        for m in self.metrics:
            mark = "适用" if m.applicable else "不适用"
            p = m.percentiles
            body = (
                "  ".join(f"{k}={v}" for k, v in p.items()) if p else "（无数据）"
            )
            lines.append(f"[{mark}] {m.name}  [{m.unit}]  n={m.n}")
            lines.append(f"       {body}")
            if m.note:
                lines.append(f"       {m.note}")
        if self.singular_values:
            d = self.singular_values.as_dict()
            lines.append(f"[适用] PRB 奇异值（{d['n_streams']} 流，t=0，10log10）")
            for k in ("largest_db", "smallest_db", "ratio_db"):
                lines.append(
                    f"       {k}: " + "  ".join(f"{a}={b}" for a, b in d[k].items())
                )
            lines.append(f"       {d['note']}")
        return "\n".join(lines)


def calibration_report(ds: Any, *, max_samples: int = 200) -> CalibrationReport:
    """按 38.901 §7.8 的口径把这批数据的校准量全算出来。

    出数用的，不做通过/不通过判定——判定在 ``validate`` 和 ``gates`` 里。
    """
    metrics = [
        coupling_loss_db(ds),
        geometry_db(ds, with_noise=True),
        geometry_db(ds, with_noise=False),
        delay_spread_ns(ds),
        angular_spread_deg(ds, "ASD"),
        angular_spread_deg(ds, "ASA"),
        angular_spread_deg(ds, "ZSD"),
        angular_spread_deg(ds, "ZSA"),
    ]
    return CalibrationReport(
        metrics=metrics,
        singular_values=prb_singular_values_db(ds, max_samples=max_samples),
        context={
            "dataset_id": getattr(ds, "dataset_id", ""),
            "n_samples": int(getattr(ds, "n", 0)),
            "scenario": ds.config.get("scenario"),
            "channel_model": getattr(ds, "channel_model", None),
            "num_sites": ds.config.get("num_sites"),
            "sectors_per_site": ds.config.get("sectors_per_site"),
        },
    )
