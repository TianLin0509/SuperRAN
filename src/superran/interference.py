"""干扰强度量化：IoT（噪声抬升）与测量域干扰。

**两个域必须分开，混起来的结论一定是错的。**

* **业务域**（traffic）——PDSCH/PUSCH 承载数据时受到的干扰，决定吞吐。
  量化用 IoT ``(I+N)/N``，也叫噪声抬升 noise rise。
* **测量域**（measurement）——SRS / CSI-RS 导频受到的干扰，决定信道估计
  质量，进而决定预编码好不好、CQI 准不准。量化用导频域 SIR。

同一个场景这两个量可以差很远：SRS 有梳齿（comb）与循环移位提供正交性，
测量域 SIR 通常高于业务域 SIR；但一旦邻区 UE 数上去、序列跳变关掉，
测量域会先崩——这时业务域 SINR 看着还行，实际预编码已经不可用了。

--- IoT 怎么算 ---------------------------------------------------------

当前 first-party internal/Sionna 源把整带总发射功率均匀分到 RB：
``S_RB = P_rx,total / N_RB``，单 RB 噪声是 ``kT·12·SCS·NF``。因此
``snr_dB`` 与业务域 ``sinr_dB`` 在当前版本确实共用同一个信号定标，
``snr_dB - sinr_dB`` 在数学上等于 IoT。

实现仍选 ``sir_dB + sinr_dB`` 作为落盘主口径：它们被契约明确绑定在同一个
业务几何域，而外部/旧版数据的 ``snr_dB`` 可能是全带、单 RB 或接收机后口径。
这样导入历史/第三方数据时不会因为 SNR 定标漂移而静默改写 IoT：

    SINR = S/(I+N)、SIR = S/I
    1/SINR - 1/SIR = N/S
    IoT = (I+N)/N = (S/N)/(S/(I+N)) = SIR/(SIR - SINR)    （线性域）

于是 IoT 完全由已落盘的两个业务域字段决定，不需要额外仿真；在 first-party
数据上还会用 ``snr_dB-sinr_dB`` 做一致性旁证，而不是把它当跨源契约。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# 历史外部数据把 SNR/SIR/SINR 夹到 ±50 dB。first-party source 不截断；
# 但读取旧数据时，落在旧边界上的值仍须单独计数。
_CLAMP_DB = 50.0
_CLAMP_EPS = 0.15
_NO_INTF_SENTINEL = 49.9   # 没有干扰源时的有限哨兵值

# IoT 分级。**"20 dB 以上算高干扰"是硬约定**，档位按它对齐：>= 20 dB 一律
# 落在"高干扰"或"极高干扰"，``high_interference`` 标志就是 ``>= 20``。
# 其余切分参考负载—噪声抬升关系 η = 1 - 10^(-IoT/10)：
#   3 dB -> 50% 负载、6 dB -> 75%、10 dB -> 90%、13 dB -> 95%、
#   20 dB -> 99%、30 dB -> 99.9%。
IOT_BANDS: tuple[tuple[float, str, str], ...] = (
    (3.0, "轻载", "干扰远低于热噪声，等效负载 < 50%，接近单小区"),
    (6.0, "低干扰", "干扰与热噪声同量级，等效负载 50%~75%"),
    (13.0, "中等干扰", "干扰主导但仍有噪声余量，等效负载 75%~95%"),
    (20.0, "较高干扰", "干扰主导，等效负载 95%~99%，但还没到现场认定的高干扰线"),
    (30.0, "高干扰", "等效负载 99%~99.9%，链路自适应会持续压在低阶 MCS"),
    (float("inf"), "极高干扰", "等效负载 > 99.9%，接近极点容量，边缘用户基本不可用"),
)

# "算不算高干扰"的门限。改它等于改现场约定，改之前先和用户对齐。
HIGH_IOT_THRESHOLD_DB = 20.0

# 测量域 SIR 分级。门限取自导频污染对 LS 估计的影响：
# 残余干扰功率决定信道估计 NMSE 的下限，SIR 15 dB 对应 NMSE 底 ~-15 dB，
# 已经和典型 CSI 反馈量化误差同量级；10 dB 以下预编码增益开始明显塌陷。
MEAS_SIR_BANDS: tuple[tuple[float, str, str], ...] = (
    (0.0, "测量已失效", "导频干扰强于导频本身，估计出的是干扰的信道"),
    (10.0, "测量严重受损", "估计 NMSE 底 > -10 dB，预编码增益大幅塌陷"),
    (15.0, "测量受损", "估计 NMSE 底 -10~-15 dB，与量化误差同量级"),
    (25.0, "测量可用", "估计误差仍以热噪声为主"),
    (float("inf"), "测量干净", "导频域几乎无干扰"),
)


# ---------------------------------------------------------------------------
# 标量换算
# ---------------------------------------------------------------------------


def iot_db(sinr_db: Any, sir_db: Any) -> np.ndarray:
    """由同口径的 SINR 与 SIR 推 IoT（dB）。

    ``IoT = SIR / (SIR - SINR)``（线性域）。两个输入必须来自同一个 S，
    见模块文档。当前 first-party 源的 ``snr_dB - sinr_dB`` 与之等价，
    但 SNR 在历史/外部数据中的定标不属于跨源稳定契约。

    SIR ≤ SINR 时返回 ``inf``（物理上不可能，只会因为夹逼或口径错配出现），
    调用方应当把 inf 单独计数而不是求均值。
    """
    sinr = np.asarray(sinr_db, dtype=np.float64)
    sir = np.asarray(sir_db, dtype=np.float64)
    s_lin = np.power(10.0, sinr / 10.0)
    r_lin = np.power(10.0, sir / 10.0)
    denom = r_lin - s_lin
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(denom > 0, r_lin / denom, np.inf)
        out = 10.0 * np.log10(ratio)
    # SIR 与 SINR 都非有限时结果无意义
    out = np.where(np.isfinite(sinr) & np.isfinite(sir), out, np.nan)
    return np.asarray(out, dtype=np.float64)


def load_factor_from_iot(iot: Any) -> np.ndarray:
    """由 IoT 反推等效小区负载 η = 1 - 10^(-IoT/10)。

    来自上行极点容量关系：噪声抬升 = 1/(1-η)。这是个**解释性**换算，
    用来把 "IoT 20 dB" 翻译成 "等效 99% 负载" 这种直觉，
    不代表仿真里真的按这个负载调度。
    """
    v = np.asarray(iot, dtype=np.float64)
    return 1.0 - np.power(10.0, -v / 10.0)


def iot_from_load(load: float) -> float:
    """由等效负载算 IoT（dB）。``load_factor_from_iot`` 的逆。"""
    lo = min(max(float(load), 0.0), 0.999999)
    return -10.0 * math.log10(1.0 - lo)


def _classify(value: float, bands: tuple[tuple[float, str, str], ...]) -> tuple[str, str]:
    if not math.isfinite(value):
        return "未定义", "输入非有限值"
    for hi, label, why in bands:
        if value < hi:
            return label, why
    return bands[-1][1], bands[-1][2]


def classify_iot(value: float) -> dict[str, Any]:
    """把一个 IoT 值翻成人能读的等级 + 等效负载。"""
    label, why = _classify(value, IOT_BANDS)
    return {
        "iot_db": round(float(value), 2) if math.isfinite(value) else None,
        "band": label,
        "meaning": why,
        "equivalent_load": (
            round(float(load_factor_from_iot(value)), 4) if math.isfinite(value) else None
        ),
        "high_interference": bool(math.isfinite(value) and value >= HIGH_IOT_THRESHOLD_DB),
    }


def classify_measurement_sir(value: float) -> dict[str, Any]:
    label, why = _classify(value, MEAS_SIR_BANDS)
    return {
        "sir_db": round(float(value), 2) if math.isfinite(value) else None,
        "band": label,
        "meaning": why,
    }


def estimation_nmse_floor_db(meas_sir_db: Any, snr_db: Any = None) -> np.ndarray:
    """导频域干扰给信道估计带来的 NMSE 下限（dB）。

    LS 估计里除以已知导频后，残余干扰直接落在估计上，NMSE >= 1/SIR。
    给了 ``snr_db`` 就把热噪声一并算上：``NMSE >= 1/SIR + 1/SNR``。

    这是**下限**不是实测值——插值、平滑、MMSE 先验都会改变实际 NMSE。
    用途是回答"这个测量干扰水平下，估计精度最好能到多少"。
    """
    sir = np.asarray(meas_sir_db, dtype=np.float64)
    inv = np.power(10.0, -sir / 10.0)
    if snr_db is not None:
        inv = inv + np.power(10.0, -np.asarray(snr_db, dtype=np.float64) / 10.0)
    with np.errstate(divide="ignore"):
        return 10.0 * np.log10(inv)


# ---------------------------------------------------------------------------
# 数据集级报告
# ---------------------------------------------------------------------------


@dataclass
class IotStats:
    """一批样本的 IoT 分布。"""

    n_total: int
    n_valid: int
    n_clamped: int
    n_no_interferer: int
    median_db: float
    mean_db: float
    p5_db: float
    p95_db: float
    frac_above_20db: float
    frac_above_13db: float
    bands: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "n_total": self.n_total,
            "n_valid": self.n_valid,
            "n_clamped": self.n_clamped,
            "n_no_interferer": self.n_no_interferer,
            "median_db": _r(self.median_db),
            "mean_db": _r(self.mean_db),
            "p5_db": _r(self.p5_db),
            "p95_db": _r(self.p95_db),
            "frac_above_20db": round(self.frac_above_20db, 4),
            "frac_above_13db": round(self.frac_above_13db, 4),
            "bands": self.bands,
        }
        if self.n_valid:
            d["classification"] = classify_iot(self.median_db)
        return d


def _r(v: float) -> float | None:
    return round(float(v), 2) if v is not None and math.isfinite(v) else None


def iot_stats(sinr_db: Any, sir_db: Any) -> IotStats:
    """算一批样本的 IoT 分布，并把不可信的样本分类计数而不是丢掉。

    三类需要单独计数：
      * ``n_no_interferer``——``sir_dB`` 是 49.9 哨兵，说明这批数据压根没有
        干扰源（或者 ``bs_panel`` 缺失导致干扰没进 SINR，见 generate.py）。
      * ``n_clamped``——SINR 或 SIR 贴在 ±50 dB 的契约边界上，真值在夹逼之外。
      * 其余非有限值。
    """
    sinr = np.asarray(sinr_db, dtype=np.float64)
    sir = np.asarray(sir_db, dtype=np.float64)
    n_total = int(sinr.size)

    sentinel = np.isclose(sir, _NO_INTF_SENTINEL, atol=1e-3)
    # Historical sources clipped *onto* ±50 dB.  New first-party values are
    # unbounded, so a legitimate 55 dB value is not itself evidence of clipping;
    # only a value sitting on the old boundary is classified as such.
    clamped = (
        np.isclose(np.abs(sinr), _CLAMP_DB, atol=_CLAMP_EPS)
        | np.isclose(np.abs(sir), _CLAMP_DB, atol=_CLAMP_EPS)
    ) & ~sentinel

    values = iot_db(sinr, sir)
    ok = np.isfinite(values) & ~sentinel & ~clamped
    good = values[ok]

    bands: dict[str, int] = {label: 0 for _, label, _ in IOT_BANDS}
    for v in good:
        bands[_classify(float(v), IOT_BANDS)[0]] += 1

    if good.size == 0:
        return IotStats(
            n_total=n_total, n_valid=0,
            n_clamped=int(clamped.sum()), n_no_interferer=int(sentinel.sum()),
            median_db=float("nan"), mean_db=float("nan"),
            p5_db=float("nan"), p95_db=float("nan"),
            frac_above_20db=0.0, frac_above_13db=0.0, bands=bands,
        )

    return IotStats(
        n_total=n_total,
        n_valid=int(good.size),
        n_clamped=int(clamped.sum()),
        n_no_interferer=int(sentinel.sum()),
        median_db=float(np.median(good)),
        mean_db=float(np.mean(good)),
        p5_db=float(np.percentile(good, 5)),
        p95_db=float(np.percentile(good, 95)),
        frac_above_20db=float(np.mean(good >= HIGH_IOT_THRESHOLD_DB)),
        frac_above_13db=float(np.mean(good >= 13.0)),
        bands=bands,
    )


def _dist(v: np.ndarray) -> dict[str, Any]:
    f = v[np.isfinite(v)]
    if f.size == 0:
        return {"n": 0}
    return {
        "n": int(f.size),
        "min": _r(float(f.min())),
        "p5": _r(float(np.percentile(f, 5))),
        "median": _r(float(np.median(f))),
        "mean": _r(float(f.mean())),
        "p95": _r(float(np.percentile(f, 95))),
        "max": _r(float(f.max())),
    }


def interference_report(dataset_id: str) -> dict[str, Any]:
    """一个数据集的完整干扰画像：业务域 IoT + 测量域 SIR。

    只读已落盘的标量，不重跑仿真。
    """
    from .loader import Dataset  # noqa: PLC0415

    ds = Dataset(dataset_id)
    summary = ds.summary
    cfg = summary.get("config", {}) or {}

    def col(name: str) -> np.ndarray:
        try:
            return np.asarray(ds.scalar(name), dtype=np.float64)
        except KeyError:
            return np.array([], dtype=np.float64)

    sinr = col("sinr_dB")
    sir = col("sir_dB")
    snr = col("snr_dB")
    ul_sinr = col("ul_sinr_dB")
    ul_sir_meas = col("ul_sir_dB")
    dl_sir_meas = col("dl_sir_dB")
    ul_sir_geo = col("ul_sir_geo_dB")

    n_cells = int(cfg.get("num_sites", 1) or 1) * int(cfg.get("sectors_per_site", 1) or 1)
    n_slots = int(cfg.get("num_slots_per_sample", 1) or 1)

    out: dict[str, Any] = {
        "dataset_id": dataset_id,
        "num_cells": n_cells,
        "num_interfering_ues": cfg.get("num_interfering_ues"),
        "pdsch_load": cfg.get("pdsch_load"),
        "pusch_load": cfg.get("pusch_load"),
        "traffic_domain": {},
        "measurement_domain": {},
        "notes": [],
    }

    # --- 业务域 ---------------------------------------------------------
    if sinr.size and sir.size:
        st = iot_stats(sinr, sir)
        out["traffic_domain"]["dl"] = {
            "iot": st.as_dict(),
            "sinr_dB": _dist(sinr),
            "sir_dB": _dist(sir),
            "snr_dB": _dist(snr) if snr.size else None,
        }
        if st.n_no_interferer:
            out["notes"].append(
                f"{st.n_no_interferer}/{st.n_total} 个样本的 sir_dB 是 49.9 哨兵值 —— "
                "这批数据里小区间干扰没有进入 SINR，IoT 无从谈起。"
                "多小区配置下出现这种情况通常是 bs_panel 缺失（见 generate._ensure_bs_panel）。"
            )
        if st.n_clamped:
            out["notes"].append(
                f"{st.n_clamped}/{st.n_total} 个样本的 SINR 或 SIR 贴在 ±50 dB 契约边界上，"
                "真值在夹逼之外，已从 IoT 统计中剔除。"
            )
    else:
        out["notes"].append("数据集缺 sinr_dB 或 sir_dB，无法算业务域 IoT。")

    if ul_sinr.size and ul_sir_geo.size:
        st_ul = iot_stats(ul_sinr, ul_sir_geo)
        out["traffic_domain"]["ul"] = {
            "iot": st_ul.as_dict(),
            "sinr_dB": _dist(ul_sinr),
            "sir_dB": _dist(ul_sir_geo),
        }
    elif ul_sinr.size:
        out["traffic_domain"]["ul"] = {"sinr_dB": _dist(ul_sinr), "iot": None}
        out["notes"].append(
            "上行只有 SINR 没有几何 SIR，算不出上行 IoT。"
            "first-party source 会把该量显式写进 sample.meta；旧数据集可能没有这一列。"
        )

    first_party_slots = (
        (summary.get("sample_meta") or {}).get("implementation")
        == "superran-first-party"
    )
    if n_slots > 1 and not first_party_slots:
        out["notes"].append(
            f"num_slots_per_sample={n_slots} > 1：历史来源的聚合 SIR/SINR 口径未知，"
            "IoT 只作近似。"
        )
        out["iot_exact"] = False
    else:
        out["iot_exact"] = True

    # --- 测量域 ---------------------------------------------------------
    for name, arr, label in (
        ("ul_srs", ul_sir_meas, "SRS（上行导频，基站侧收）"),
        ("dl_csirs", dl_sir_meas, "CSI-RS（下行导频，终端侧收）"),
    ):
        if not arr.size or not np.isfinite(arr).any():
            continue
        finite = arr[np.isfinite(arr)]
        sentinel_n = int(np.isclose(finite, _NO_INTF_SENTINEL, atol=1e-3).sum())
        real = finite[~np.isclose(finite, _NO_INTF_SENTINEL, atol=1e-3)]
        block: dict[str, Any] = {
            "pilot": label,
            "sir_dB": _dist(real if real.size else finite),
            "n_no_interferer": sentinel_n,
        }
        if real.size:
            med = float(np.median(real))
            block["classification"] = classify_measurement_sir(med)
            block["nmse_floor_db"] = _r(float(np.median(estimation_nmse_floor_db(real))))
            block["frac_below_15db"] = round(float(np.mean(real < 15.0)), 4)
        out["measurement_domain"][name] = block

    if not out["measurement_domain"]:
        out["notes"].append(
            "数据集里没有测量域 SIR（ul_sir_dB / dl_sir_dB）。"
            "这两列只在 link_pairing=paired（配置 link='UL+DL'）时产生。"
        )

    return out


# ---------------------------------------------------------------------------
# 场景设计：反过来，给目标 IoT 推配置
# ---------------------------------------------------------------------------

# 各旋钮对 IoT 的实际作用。**每条的 measured 都是在这套引擎上真跑出来的**
# （21 小区 UMi_NLOS、64T、100 MHz、每档 42 样本），不是照搬教科书直觉——
# 其中至少两条与直觉相反，见下面的 note。
#
# 基线：7 站 21 小区、ISD 200 m、UMi 默认 33 dBm、NF 7 dB、100 MHz
#       -> 下行 IoT 24.9 dB、上行 IoT 0.7 dB
IOT_LEVERS: tuple[dict[str, Any], ...] = (
    {
        "key": "isd_m",
        "direction": "调小 -> 提高 IoT",
        "why": "站间距越小，邻区到本 UE 的路损越接近服务小区",
        "range": "100 ~ 5000",
        "measured": "100 m: 38.3 dB / 200 m: 24.9 / 500 m: 4.4 / 1732 m: 0.2",
        "note": "**作用范围最大的旋钮**。代价是同时改变 SNR（站距大则服务小区也远），"
                "拿它做对照组会混两个变量。",
    },
    {
        "key": "tx_power_dbm",
        "direction": "调大 -> 提高 IoT",
        "why": "信号与干扰同比例上升，SIR 不变，但 I/N 上升",
        "range": "23 ~ 49（UMi 默认 33，UMa 默认 43）",
        "measured": "33 -> 49 dBm：IoT 24.9 -> 40.9 dB，正好 +16 dB；SIR 15.84 一动不动",
        "note": "**dB 对 dB 线性**，是抬 IoT 而不改变 SIR 分布的干净手段。",
    },
    {
        "key": "noise_figure_db / bandwidth_hz",
        "direction": "噪声底调低 -> 提高 IoT",
        "why": "IoT 是相对热噪声的抬升，噪声底降多少 IoT 就抬多少",
        "range": "NF 3~9 dB；带宽 10~100 MHz",
        "measured": "NF 7->3 dB：+4.0 dB（理论 +4）；带宽 100->20 MHz：+7.0 dB（理论 +6.99）",
        "note": "两者都精确到 0.01 dB。改 NF 等于改接收机质量，别只为凑 IoT 设成不现实的值。",
    },
    {
        "key": "num_sites",
        "direction": "调大 -> 提高 IoT",
        "why": "干扰源数量",
        "range": "1 / 7 / 19（六边形栅格只能取这三个）",
        "measured": "1 站 3 小区: 5.3 dB / 7 站 21 小区: 24.9 / 19 站 57 小区: 33.1",
        "note": "代价是每样本耗时按小区数近似线性增长（21 小区 444 ms，57 小区 920 ms）。",
    },
    {
        "key": "pdsch_load / prb_utilization",
        "direction": "**对下行 IoT 完全无效**",
        "why": "几何模型里每个邻区都无条件贡献一份泄漏，负载只决定对几个波束取平均——"
               "均值不变，只是方差变小",
        "range": "—",
        "measured": "0.2 与 1.0 两组的 SINR / SIR / IoT **逐位相同**",
        "note": "**这条与直觉相反。** 拿它做「轻载 vs 满载」对比会得到两批一模一样的数据，"
                "从而得出「负载不影响性能」的假结论。要造下行强弱干扰对比请用 isd_m。",
    },
    {
        "key": "pusch_load × (num_ues / 小区数)",
        "direction": "调大 -> 提高上行 IoT",
        "why": "上行干扰按同时发射的邻区 UE 数线性叠加",
        "range": "每小区 UE 数 >= 2 才有效",
        "measured": "21 UE（每小区 1 个）：满载与轻载无差别；"
                    "105 UE（每小区 5 个）+ 满载：上行 IoT 0.7 -> 5.4 dB",
        "note": "调度 UE 数是 max(1, 每小区UE数 x 负载)，每小区只有 1 个 UE 时取整后恒为 1。",
    },
    {
        "key": "num_interfering_ues",
        "direction": "**主要影响测量域，不是业务域**",
        "why": "它决定每个邻区同时发 SRS 的 UE 数，直接打在导频上；"
               "而进入上行 SINR 的数量另有上限（见 pusch_load 那条）",
        "range": "0 ~ 32",
        "measured": "10 -> 24：上行 IoT 0.71 -> 0.69（无变化）；"
                    "但 12 个干扰 UE 就能把 SRS 测量域 SIR 打到 -2.4 dB（测量已失效）",
        "note": "**这条也与直觉相反。** 另外它按 max_per_ue_intf_cells x N 生成信道，"
                "是耗时大头：设 0 比设 10 快 1.62 倍、设 2 快 1.40 倍（交错重测中位数）。",
    },
)


def design_hint(target_iot_db: float) -> dict[str, Any]:
    """给一个目标 IoT，回一份"该动哪些旋钮"的说明。

    **不返回保证能达标的配置。** IoT 由几何、负载、功率共同决定，
    唯一可靠的确认方式是生成一批再用 ``interference_report`` 复核。
    """
    target = float(target_iot_db)
    band, why = _classify(target, IOT_BANDS)
    load = load_factor_from_iot(target)
    return {
        "target_iot_db": round(target, 2),
        "band": band,
        "meaning": why,
        "equivalent_load": round(float(load), 4),
        "levers": list(IOT_LEVERS),
        "suggested_preset": _suggest_preset(target),
        "verification": (
            "生成后调 sr_interference_report 复核 IoT 中位数；"
            "达不到目标就按 levers 里的方向继续调，别用估算值下结论。"
        ),
    }


def _suggest_preset(target: float) -> str:
    if target >= 20.0:
        return "high_iot_dense"
    if target >= 13.0:
        return "multicell_7site（pdsch_load 提到 0.9）"
    if target >= 6.0:
        return "multicell_19site"
    return "single_cell_64t4r（无小区间干扰）"


# ---------------------------------------------------------------------------
# 上行几何 SIR 的稳定交接（兼容旧版 ChannelHub 钩子）
# ---------------------------------------------------------------------------
# 新版 ChannelHub 直接把业务域 UL 几何 SIR 放在 sample.meta 的稳定键中；
# ``ul_sir_dB`` 仍只表示 SRS/估计域 SIR。当前粗粒度系统模型假设邻区上下行
# 活跃度和功率对称，因此 UL 与 DL 共用同一个 aggregate geometry SIR，并把
# 这个工程假设显式写进 metadata。旧版才需要下面的一次调用暂存钩子。

_capture: dict[str, Any] = {}
_installed = False
_install_failure = ""


def last_install_failure() -> str:
    """最近一次安装 UL 几何 SIR 交接失败的原因；未失败为空串。"""
    return _install_failure


def install_geometry_capture() -> bool:
    """Confirm the first-party metadata handoff for UL geometry SIR.

    The former implementation monkey-patched an external private module.  The
    local source writes ``meta['ul_geometry_sir_dB']`` directly, so installing
    a hook is neither necessary nor permitted.
    """
    global _installed, _install_failure
    if _installed:
        return True
    try:
        from .native import InternalSimSource  # noqa: PLC0415

        if getattr(InternalSimSource, "UL_GEOMETRY_SIR_META_KEY", None) == (
            "ul_geometry_sir_dB"
        ):
            _installed = True
            _install_failure = ""
            return True
    except Exception as exc:  # noqa: BLE001
        _install_failure = f"metadata 路径不可用：{type(exc).__name__}: {exc}"
    return False


def take_ul_geometry_sir(sample: Any) -> float:
    """读取业务域 UL 几何 SIR；新版读 metadata，旧版取暂存值。

    旧版自检：暂存的下行量必须与 sample 自己报的 ``sinr_dB`` / ``sir_dB`` 一致
    （ChannelHub 会把它们夹到 ±50 dB，所以比较时也夹一下）。
    对不上说明调用与样本不是一一对应，直接放弃——**给错的上行 IoT 比没有更糟**。
    """
    meta = getattr(sample, "meta", None)
    if isinstance(meta, dict) and "ul_geometry_sir_dB" in meta:
        _capture.clear()
        try:
            direct = float(meta["ul_geometry_sir_dB"])
        except (TypeError, ValueError):
            return float("nan")
        if math.isfinite(direct):
            return direct
        return float("nan")

    if not _capture:
        return float("nan")
    cap = dict(_capture)
    _capture.clear()

    def _clamp(x: float) -> float:
        return max(-_CLAMP_DB, min(_CLAMP_DB, x))

    for cap_key, attr in (("dl_sinr_avg", "sinr_dB"), ("sir_dl_db", "sir_dB")):
        want = getattr(sample, attr, None)
        if want is None:
            continue
        try:
            if abs(_clamp(cap[cap_key]) - float(want)) > 1e-6:
                return float("nan")
        except (KeyError, TypeError, ValueError):
            return float("nan")
    return _clamp(cap.get("sir_ul_db", float("nan")))
