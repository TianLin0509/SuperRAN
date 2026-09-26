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
#
# SuperRAN 当前只仿下行，所以每档的含义按下行物理写：IoT 回答的是"热噪声
# 还剩多大影响"。SINR 比 SIR 低多少由它唯一决定：
#     SIR - SINR = 10·log10(IoT / (IoT - 1))      （IoT 取线性值）
#   3 dB -> 3.0 dB、6 dB -> 1.3 dB、13 dB -> 0.22 dB、20 dB -> 0.04 dB。
# 旧版把 IoT 换算成"等效负载 1-1/IoT"，那是上行噪声抬升/CDMA 极点容量的
# 关系，放在下行会把"信号和干扰都远高于噪声"误读成"小区快满载了"，已撤下。
IOT_BANDS: tuple[tuple[float, str, str], ...] = (
    (3.0, "噪声受限", "干扰低于热噪声：提高功率或缩小站距能直接抬高 SINR"),
    (6.0, "低干扰", "干扰与热噪声同量级：噪声仍让 SINR 比 SIR 低 1.3~3 dB"),
    (13.0, "中等干扰", "干扰主导：噪声只让 SINR 比 SIR 低 0.2~1.3 dB，再加功率收益递减"),
    (20.0, "较高干扰", "干扰主导：SINR 已基本等于 SIR，加功率几乎不改善 SINR"),
    (30.0, "高干扰", "强干扰受限：SINR 与 SIR 相差不到 0.05 dB，只有降干扰（协调/波束/功控）有效"),
    (float("inf"), "极高干扰", "干扰比热噪声高 30 dB 以上：通常意味着发射功率相对站距偏大，先核对功率与室内比例假设"),
)

# "算不算高干扰"的门限。改它等于改现场约定，改之前先和用户对齐。
HIGH_IOT_THRESHOLD_DB = 20.0

# 会明显改变下行 IoT、但 first-party 信道层当前没有建模的因素。报告里原样
# 列出，避免把"模型没有这个机制"读成"这个因素不重要"。
DL_IOT_NOT_MODELED: tuple[str, ...] = (
    "室内用户与 O2I 穿透损耗：所有 UE 在室外、高 1.5 m；38.901 UMa/UMi 评估假设"
    " 80% 用户在室内。室内用户的信号与干扰同时衰减，SIR 近似不变，但干扰相对热"
    "噪声（IoT）会显著降低——当前 IoT 相对这类部署偏高。",
    "邻区负载：信道层每个邻区恒满功率发射，pdsch_load / prb_utilization 不进入"
    "下行 IoT；负载只在系统级 neighbor_prb_util 生效。",
    "拓扑边缘：没有 wrap-around，统计包含外圈小区的用户，它们的邻区不完整，"
    "IoT 相对中心站偏低。",
)

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

    **上行口径**：来自上行极点容量关系，噪声抬升 = 1/(1-η)。SuperRAN 当前
    只仿下行，下行报告不再使用它；仅保留给 ``sr_iot_convert`` 的显式换算。
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


def noise_sinr_loss_db(iot: Any) -> np.ndarray:
    """热噪声让下行 SINR 比 SIR 低多少（dB）：``10·log10(IoT/(IoT-1))``。

    由 ``1/SINR = 1/SIR + N/S`` 与 ``IoT = (I+N)/N`` 直接推出，逐样本精确。
    IoT → 0 dB（无干扰）时趋于无穷：此时 SINR 就是 SNR，谈不上"与 SIR 之差"。
    """
    v = np.power(10.0, np.asarray(iot, dtype=np.float64) / 10.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 10.0 * np.log10(v / (v - 1.0))
    return np.where(v > 1.0, out, np.inf)


def classify_iot(value: float) -> dict[str, Any]:
    """把一个下行 IoT 值翻成人能读的等级，以及噪声还让 SINR 损失多少。"""
    label, why = _classify(value, IOT_BANDS)
    loss = float(noise_sinr_loss_db(value)) if math.isfinite(value) else float("nan")
    return {
        "iot_db": round(float(value), 2) if math.isfinite(value) else None,
        "band": label,
        "meaning": why,
        "noise_sinr_loss_db": round(loss, 3) if math.isfinite(loss) else None,
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


def describe_measurement_domain(
    ul_sir_meas: Any, dl_sir_meas: Any, traffic_sir: Any, *, num_interfering_ues: int,
) -> tuple[dict[str, Any], list[str]]:
    """导频（测量域）SIR 的画像；只有逐样本真正仿出来的量才分级。

    两种已知的非仿真来源要识别出来而不是分级：
      * SRS：按干扰 UE 数的解析式 ``10 - 10·log10(N)``，与几何、站距无关；
      * CSI-RS：逐样本等于业务域 SIR，是复用而不是单独建模。
    返回 ``(blocks, not_modeled_notes)``。
    """
    ul = np.asarray(ul_sir_meas, dtype=np.float64)
    dl = np.asarray(dl_sir_meas, dtype=np.float64)
    tsir = np.asarray(traffic_sir, dtype=np.float64)
    n_intf = int(num_interfering_ues or 0)
    srs_formula = max(10.0 - 10.0 * math.log10(max(n_intf, 1)), -20.0)
    blocks: dict[str, Any] = {}
    for name, arr, label in (
        ("ul_srs", ul, "SRS（上行导频，基站侧收，用作下行预编码 CSI）"),
        ("dl_csirs", dl, "CSI-RS（下行导频，终端侧收）"),
    ):
        if not arr.size or not np.isfinite(arr).any():
            continue
        finite = arr[np.isfinite(arr)]
        is_sentinel = np.isclose(finite, _NO_INTF_SENTINEL, atol=1e-3)
        real = finite[~is_sentinel]
        block: dict[str, Any] = {
            "pilot": label,
            "sir_dB": _dist(real if real.size else finite),
            "n_no_interferer": int(is_sentinel.sum()),
        }
        if name == "ul_srs" and real.size and np.allclose(real, srs_formula, atol=1e-6):
            block["model"] = "analytic_placeholder"
            block["meaning"] = (
                f"未逐样本仿真：SIR = 10 - 10·log10(干扰 UE 数 {n_intf}) = "
                f"{srs_formula:.2f} dB，与几何、站距、功率无关，不能用来比较场景。"
            )
        elif (
            name == "dl_csirs" and real.size and tsir.shape == arr.shape
            and np.allclose(arr, tsir, atol=1e-6, equal_nan=True)
        ):
            block["model"] = "same_as_traffic_sir"
            block["meaning"] = "逐样本等于业务域 SIR（复用），没有单独的导频干扰模型。"
        elif real.size:
            block["model"] = "simulated"
            block["classification"] = classify_measurement_sir(float(np.median(real)))
            block["nmse_floor_db"] = _r(float(np.median(estimation_nmse_floor_db(real))))
            block["frac_below_15db"] = round(float(np.mean(real < 15.0)), 4)
        blocks[name] = block
    placeholders = [
        k for k, v in blocks.items()
        if v.get("model") in {"analytic_placeholder", "same_as_traffic_sir"}
    ]
    notes = []
    if placeholders:
        notes.append(
            "导频（测量域）干扰：" + "、".join(placeholders)
            + " 不是逐样本仿真结果，未分级；详见各块的 meaning。"
        )
    return blocks, notes


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
        "scope": "downlink",
        "traffic_domain": {},
        "measurement_domain": {},
        "not_modeled": [],
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

    if (summary.get("sample_meta") or {}).get("implementation") == "superran-first-party":
        out["not_modeled"].extend(DL_IOT_NOT_MODELED)

    # 上行业务域不在当前能力范围：数据集里的 ul_sinr_dB 由一个按干扰 UE 数
    # 的解析占位式合成，ul_sir_geo_dB 直接复用下行几何 SIR。给它分级只会
    # 让人把占位值当成"上行轻载"。
    if ul_sinr.size or ul_sir_geo.size:
        out["not_modeled"].append(
            "上行业务域干扰：SuperRAN 当前只仿下行，数据里的上行 SINR 是占位值、"
            "上行几何 SIR 复用下行，不输出上行 IoT。"
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
    md, md_notes = describe_measurement_domain(
        ul_sir_meas, dl_sir_meas, sir,
        num_interfering_ues=int(cfg.get("num_interfering_ues", 0) or 0),
    )
    out["measurement_domain"] = md
    out["not_modeled"].extend(md_notes)
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
#       -> 下行 IoT 24.9 dB（输出时随 LEVER_ANCHOR_CONDITIONS 一起给）
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
)


# ``measured`` 数字的来源条件。它们与用户当前场景（功率、站数、传播场景、
# 阵列）通常不同，直接拿来当预期会差十几 dB——输出时必须带着条件一起给。
LEVER_ANCHOR_CONDITIONS = (
    "历史实测（当前版本未重测）：7 站 21 小区、UMi_NLOS、ISD 200 m、33 dBm、"
    "NF 7 dB、100 MHz、64T、每档 42 样本。数字只说明方向与斜率；"
    "换功率/站数/场景后绝对值会平移，以本次 sr_probe_scenario 为准。"
)


def design_hint(target_iot_db: float) -> dict[str, Any]:
    """给一个目标 IoT，回一份"该动哪些旋钮"的说明。

    **不返回保证能达标的配置。** IoT 由几何、负载、功率共同决定，
    唯一可靠的确认方式是生成一批再用 ``interference_report`` 复核。
    """
    target = float(target_iot_db)
    band, why = _classify(target, IOT_BANDS)
    loss = float(noise_sinr_loss_db(target))
    return {
        "target_iot_db": round(target, 2),
        "band": band,
        "meaning": why,
        "noise_sinr_loss_db": round(loss, 3) if math.isfinite(loss) else None,
        "levers": list(IOT_LEVERS),
        "levers_measured_under": LEVER_ANCHOR_CONDITIONS,
        "not_modeled": list(DL_IOT_NOT_MODELED),
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
        return "multicell_7site（信道层负载不影响下行 IoT，靠站距/功率调）"
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
