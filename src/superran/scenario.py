"""场景快速探测。

生成一批全带宽信道要几十分钟；但"这个场景到底长什么样"——干扰多强、
覆盖多好、多少人在视距下——是**几何量**，和信道矩阵的频域分辨率无关。

等价性实测（同一 seed、num_rb 取 273 / 24 / 12）：

===========================  ==================================
量                            num_rb 改变后
===========================  ==================================
sir_dB                         **逐位相同**
pathloss_dB / distance_3d_m   **逐位相同**
is_los / rx_power / doppler   **逐位相同**
tau_rms_ns（配置值）           **逐位相同**
ue_position                   **逐位相同**
上行几何 SIR                   **逐位相同**
---------------------------  ----------------------------------
snr_dB                        加 ``-10log10(RB_full/RB_probe)`` 精确还原
sinr_dB                       用还原后的 SNR 与不变 SIR 精确重算
===========================  ==================================

原始 ``snr_dB`` 会变，因为整带总功率均匀分给 RB，定义里显式带
``-10log10(RB)``。273→24 实测差 10.56 dB，与
``10log10(273/24)=10.559`` 吻合到小数点后两位——所以这一项**可以精确还原**，
不是近似。``sinr_dB`` 含噪声，不能宣称原样不变；必须用还原后的 SNR 与
不变的业务几何 SIR 重新合成，IoT 也基于这个全带 SINR 计算。

``num_ofdm_symbols`` 同理：ChannelHub 现在把大尺度几何量与 OFDM symbol 网格
彻底解耦，14 降到 7 / 4 / 2 / 1 时上面那张表里的量都**逐位相同**。缩减后的
symbol 网格仍只是加速近似，不能拿来比较逐符号小尺度信道或信道估计质量。

于是探测模式成立：``num_rb`` 压到 24、``num_ofdm_symbols`` 压到 4、
关掉 SSB 测量，几何量一个不差。性能不是算法不变量：旧单簇内核曾测得
11.5x；切换为每簇 20 rays 后，2026-08-11 在 21 小区 16T/20 MHz、6 样本、
交错两轮下得到：

===================================  ==========  =========
配置                                  秒/样本      相对
===================================  ==========  =========
全量（标准 51 RB、14 符号、SSB 开）       7.80/9.45      1.00x
探测（24 RB、4 符号、SSB 关）             4.78/4.79     **约 1.80x**
===================================  ==========  =========

这组数只说明当前版本、当前工况的量级，不是 SLA；冷启动、缓存、阵列与小区数
都会改变绝对耗时。实际调用应读返回的 ``elapsed_s``，不能把 1.80x 写死成预算。

用途是**下单之前先看货**：确认场景确实是想要的干扰水平、覆盖水平，
再花时间跑全带宽。

**探测模式不能用来看什么**：任何从信道矩阵算出来的量。24 个 RB 只覆盖
8.64 MHz（不是稀疏采样 100 MHz），频率选择性、时延扩展估计、宽带预编码、
谱效与吞吐在这批数据上都不成立。所以 ``probe`` 只回几何量，
并在结果里明确列出"这次没算什么"。
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

# 探测用的 RB 数。24 个 RB 已经够 ChannelHub 内部的插值与 SRS 逻辑正常工作，
# 再往下压收益递减（12 RB 实测只快一点点，却更容易撞上各种最小尺寸假设）。
PROBE_NUM_RB = 24

# 探测用的 OFDM 符号数（正式生成默认 14）。ChannelHub 已把路径损耗、距离、
# LOS、几何 SIR 等大尺度量放在 symbol 网格之外计算，因此 14/7/4/2/1 的
# 这些几何量逐位相同。仍取 4 是为了让探测数据保留一小段时域结构，便于发现
# 意外误用；缩减网格会被 metadata 明确标成 speed-only approximation。
#
# 注意：这个旋钮**只对探测模式安全**。它会改变所保存的小尺度信道快照及导频
# 估计，正式生成必须用完整 14-symbol 网格；所以它不在 generate() 里生效。
PROBE_NUM_SYM = 4

# 探测模式下会被跳过或失真的量。列出来是为了让调用方**看得见**缺什么，
# 而不是拿到一份看起来完整、实际少了一半的报告。
PROBE_NOT_AVAILABLE = (
    "谱效 / 吞吐（只覆盖 8.64 MHz，不是全带宽）",
    "时延扩展的频域估计（频率分辨率不足）",
    "宽带预编码与子带 CQI",
    "信道估计 NMSE 的绝对值（导频数量随 RB 数变化）",
    "SSB RSRP / RSRQ（探测模式关掉了 SSB 测量以省时间）",
)


def _r(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 2) if math.isfinite(f) else None


def _dist(a: np.ndarray) -> dict[str, Any]:
    f = np.asarray(a, dtype=np.float64)
    f = f[np.isfinite(f)]
    if f.size == 0:
        return {"n": 0}
    return {
        "n": int(f.size),
        "min": _r(f.min()), "p5": _r(np.percentile(f, 5)),
        "median": _r(np.median(f)), "mean": _r(f.mean()),
        "p95": _r(np.percentile(f, 95)), "max": _r(f.max()),
    }


def probe_config(cfg: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    """把一份配置改造成探测配置。返回 ``(cfg, rb_probe, rb_full)``。

    **必须先补上 ``bs_panel``，探测模式的正确性依赖它。** 没有 panel 时
    ChannelHub 建不出 DFT 码本，几何 SINR 整条路径被跳过，``sinr_dB`` 退化成
    ``snr_dB``——而 ``snr_dB`` 的定义里带 ``-10log10(RB)``，于是压 num_rb 会让
    "SINR" 整体平移 10.56 dB。

    这个坑很隐蔽：`sir_dB` 那时是 49.9 哨兵、逐位相同，路损/距离/多普勒也
    逐位相同，**只有 sinr_dB 一个字段偏**，看起来像是探测模式本身有问题。
    实际是配置缺 panel 导致连全量跑出来的 SINR 都不是真 SINR。
    """
    from .generate import _ensure_bs_panel, _rb_from_bandwidth  # noqa: PLC0415

    out = dict(cfg)
    _ensure_bs_panel(out)
    rb_full = int(out.get("num_rb") or _rb_from_bandwidth(out))
    rb_probe = min(PROBE_NUM_RB, rb_full)
    out["num_rb"] = rb_probe

    # A preset may carry an explicit full-carrier C_SRS (for example 63 ->
    # 272 RB).  Probe mode deliberately replaces the carrier with 24 RB, so
    # retaining that resource would map pilots outside the synthetic grid.
    # Re-select a standards-table row for the probe and disclose the change in
    # the returned report; normal generation still hard-fails an inconsistent
    # explicit user resource instead of silently changing it.
    if rb_probe < rb_full and out.get("srs_c_srs") is not None:
        from .channelhub import auto_select_c_srs  # noqa: PLC0415

        b_srs = int(out.get("srs_b_srs", 1) or 0)
        b_hop = int(out.get("srs_b_hop", 0) or 0)
        out["srs_c_srs"] = int(
            auto_select_c_srs(
                rb_probe,
                B_SRS=b_srs,
                target_rb=16 if b_hop < b_srs else None,
            )
        )

    # 符号数只往下压，不往上抬 —— 调用方要是显式给了更小的值，尊重它。
    sym_full = int(out.get("num_ofdm_symbols") or 14)
    out["num_ofdm_symbols"] = max(min(PROBE_NUM_SYM, sym_full), 1)

    meas = dict(out.get("measurements") or {})
    meas["ssb_rsrp"] = False
    out["measurements"] = meas
    return out, rb_probe, rb_full


def snr_correction_db(rb_probe: int, rb_full: int) -> float:
    """探测模式的 ``snr_dB`` 加上这个值才是全带宽口径下的 ``snr_dB``。

    ``snr_dB = P_rx - N - 10log10(RB)``，RB 变小时这一项变小、snr_dB 变大。
    要还原成 RB_full 的口径就要再减回去：``-10log10(RB_full/RB_probe)``。
    """
    return -10.0 * math.log10(max(rb_full, 1) / max(rb_probe, 1))


def _sinr_from_snr_sir(snr_db: np.ndarray, sir_db: np.ndarray) -> np.ndarray:
    """Combine same-domain SNR and SIR in linear power, preserving NaNs."""
    snr = np.asarray(snr_db, dtype=np.float64)
    sir = np.asarray(sir_db, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        out = -10.0 * np.log10(
            np.power(10.0, -snr / 10.0) + np.power(10.0, -sir / 10.0)
        )
    return np.where(np.isfinite(snr) & np.isfinite(sir), out, np.nan)


def _probe_sinr_from_snr_sir(
    snr_db: np.ndarray, sir_db: np.ndarray, *, num_cells: int,
) -> np.ndarray:
    """探测模式下按拓扑恢复全带 SINR。

    单小区来源用 49.9 dB 表示“无干扰”的有限哨兵；多小区则必须把同一个数
    当作合法（可能被夹逼的）SIR。用拓扑而不是数值猜语义。
    """
    if int(num_cells) <= 1:
        return np.asarray(snr_db, dtype=float).copy()
    return _sinr_from_snr_sir(snr_db, sir_db)


def probe(
    cfg: dict[str, Any],
    num_samples: int = 30,
) -> dict[str, Any]:
    """快速探测一个场景的几何特征。不落盘，跑完就扔。

    参数
    ----
    cfg : 场景配置（和 ``sr_generate`` 用的是同一份）
    num_samples : 探测样本数。30 个足够看清中位数量级；要看 5% 分位建议 100+。

    返回的报告里 ``not_available`` 明确列出探测模式**没有**给出的量。
    """
    from . import channelhub as ch  # noqa: PLC0415
    from . import interference as itf  # noqa: PLC0415

    source = str(cfg.get("source", "internal_sim"))
    if source != "internal_sim":
        # 射线追踪的耗时由光线数与场景几何决定，压 num_rb 省不下来；
        # 而且它的"几何量"本来就来自真实建筑，没有便宜的等价替身。
        # 与其给一份慢得跟正式生成一样、还标着"探测"的报告，不如直说。
        raise ValueError(
            f"探测模式只支持 internal_sim，当前是 {source!r}。"
            "射线追踪的耗时由光线数与场景几何决定，压 num_rb 省不下来，"
            "没有便宜的探测版本。请直接用小 num_samples 跑 sr_generate。"
        )

    cfg_p, rb_probe, rb_full = probe_config(cfg)
    cfg_p.pop("source", None)

    from .generate import _align_to_ues, _ensure_bs_panel  # noqa: PLC0415

    panel, panel_derived = _ensure_bs_panel(cfg_p)
    n_ues = int(cfg_p.get("num_ues", 1) or 1)
    ask = _align_to_ues(int(num_samples), n_ues)

    cfg_p["num_samples"] = ask

    itf.install_geometry_capture()

    cols: dict[str, list[float]] = {
        k: [] for k in (
            "snr_dB", "sinr_dB", "sir_dB", "ul_snr_dB", "ul_sinr_dB",
            "ul_sir_dB", "dl_sir_dB", "ul_sir_geo_dB",
        )
    }
    metas: dict[str, list[float]] = {
        k: [] for k in (
            "pathloss_dB", "distance_3d_m", "is_los",
            "rx_power_serving_dbm", "doppler_hz", "sample_tau_rms_ns",
        )
    }
    n = 0
    t0 = time.perf_counter()
    for s in ch.iter_samples(source, cfg_p):
        for k in cols:
            if k == "ul_sir_geo_dB":
                continue
            v = getattr(s, k, None)
            cols[k].append(float("nan") if v is None else float(v))
        cols["ul_sir_geo_dB"].append(itf.take_ul_geometry_sir(s))
        m = s.meta if isinstance(s.meta, dict) else {}
        for k in metas:
            v = m.get(k)
            metas[k].append(float("nan") if v is None else float(v))
        n += 1
        if n >= ask:
            break
    elapsed = time.perf_counter() - t0
    if n == 0:
        raise RuntimeError(
            "探测没有收到任何样本（与 sr_generate 同一口径的硬失败）；"
            "请检查配置或先跑一次小样本 sr_generate 确认场景可用"
        )

    arr = {k: np.asarray(v, dtype=np.float64) for k, v in cols.items()}
    marr = {k: np.asarray(v, dtype=np.float64) for k, v in metas.items()}

    # SNR 先还原到全带宽口径，再与不变的几何 SIR 重合成 SINR。直接拿
    # probe 的 raw SINR 会把“总功率挤进更少 RB”的人工 PSD 增益带进报告。
    #
    # first-party source 不截断 SNR/SINR。保留 clamped 计数字段只为让
    # 历史结果结构继续可读；新生成的 probe 这里恒为 0。
    corr = snr_correction_db(rb_probe, rb_full)
    snr_clamped = np.zeros_like(arr["snr_dB"], dtype=bool)
    ul_snr_clamped = np.zeros_like(arr["ul_snr_dB"], dtype=bool)
    snr_full = np.where(
        snr_clamped, np.nan, arr["snr_dB"] + corr
    )
    ul_snr_full = np.where(
        ul_snr_clamped,
        np.nan,
        arr["ul_snr_dB"] + corr,
    )
    cells = int(cfg.get("num_sites", 1) or 1) * int(cfg.get("sectors_per_site", 1) or 1)
    # 单小区没有干扰源时 49.9 dB 只是有限哨兵（真实 SIR 是 ∞）。是否为哨兵
    # 必须由拓扑决定，不能只看数值：多小区弱干扰也可能合法夹到 49.9 dB。
    sinr_full = _probe_sinr_from_snr_sir(
        snr_full, arr["sir_dB"], num_cells=cells)
    ul_sinr_full = _probe_sinr_from_snr_sir(
        ul_snr_full, arr["ul_sir_geo_dB"], num_cells=cells)
    n_snr_clamped = int(np.sum(snr_clamped & np.isfinite(arr["snr_dB"])))

    dl_iot = itf.iot_stats(sinr_full, arr["sir_dB"])
    ul_iot = itf.iot_stats(ul_sinr_full, arr["ul_sir_geo_dB"])

    out: dict[str, Any] = {
        "mode": "probe",
        "num_samples": n,
        "elapsed_s": round(elapsed, 2),
        "seconds_per_sample": round(elapsed / max(n, 1), 3),
        "num_cells": cells,
        "bs_panel": list(panel),
        "bs_panel_derived": bool(panel_derived),
        "num_rb": {"probe": rb_probe, "full": rb_full,
                   "snr_correction_db": round(corr, 2),
                   "snr_clamped_out": n_snr_clamped},
        "num_ofdm_symbols": {"probe": cfg_p.get("num_ofdm_symbols"),
                             "full": int(cfg.get("num_ofdm_symbols") or 14)},
        "samples_per_ue": ask // max(n_ues, 1),
        "geometry": {
            "pathloss_dB": _dist(marr["pathloss_dB"]),
            "distance_3d_m": _dist(marr["distance_3d_m"]),
            "rx_power_serving_dbm": _dist(marr["rx_power_serving_dbm"]),
            "doppler_hz": _dist(marr["doppler_hz"]),
            "los_ratio": (
                round(float(np.nanmean(marr["is_los"])), 3)
                if np.isfinite(marr["is_los"]).any() else None
            ),
        },
        "link_budget": {
            "snr_dB": _dist(snr_full),
            "sinr_dB": _dist(sinr_full),
            "sir_dB": _dist(arr["sir_dB"]),
            "ul_snr_dB": _dist(ul_snr_full),
            "ul_sinr_dB": _dist(ul_sinr_full),
        },
        "interference": {
            "dl_iot": dl_iot.as_dict() if cells > 1 else None,
            "ul_iot": ul_iot.as_dict() if (cells > 1 and ul_iot.n_valid) else None,
        },
        "not_available": list(PROBE_NOT_AVAILABLE),
        "note": (
            f"探测模式：num_rb {rb_full}->{rb_probe}、"
            f"num_ofdm_symbols {int(cfg.get('num_ofdm_symbols') or 14)}"
            f"->{cfg_p.get('num_ofdm_symbols')}、关 SSB，"
            "SIR/路损/位置等几何量与全量**逐位相同**（实测），"
            f"SNR 已做 {corr:+.2f} dB 修正，SINR/IoT 已按全带口径重算。"
            "20-ray 内核的一组 21 小区基准约 1.80 倍速；不同配置会变，"
            "以本次 elapsed_s 为准。"
            "要谱效/吞吐/时延扩展请跑正式生成。"
        ),
    }
    original_c_srs = cfg.get("srs_c_srs")
    probe_c_srs = cfg_p.get("srs_c_srs")
    if original_c_srs is not None and probe_c_srs != original_c_srs:
        out["srs_resource"] = {
            "full_c_srs": int(original_c_srs),
            "probe_c_srs": int(probe_c_srs),
            "reason": "探针载波缩小后重新选择可落在 probe RB 网格内的标准 SRS 资源",
        }

    if n_snr_clamped:
        out["link_budget"]["snr_note"] = (
            f"{n_snr_clamped}/{n} 个样本的 snr_dB 在探测口径下撞上了 ChannelHub 的 "
            f"+50 dB 夹逼上限，修正后会是假值，已从分布中剔除。"
            "依赖该 SNR 重算的 SINR / IoT 同样剔除对应样本；SIR 不受影响。"
            "要完整的链路预算分布请跑正式生成。"
        )

    # 测量域（只有 link=BOTH 才有）
    md: dict[str, Any] = {}
    for key, label in (("ul_sir_dB", "SRS（上行导频）"), ("dl_sir_dB", "CSI-RS（下行导频）")):
        v = arr[key]
        real = v[np.isfinite(v) & ~np.isclose(v, 49.9, atol=1e-3)]
        if real.size:
            md[key] = {
                "pilot": label,
                "sir_dB": _dist(real),
                "classification": itf.classify_measurement_sir(float(np.median(real))),
                "nmse_floor_db": _r(float(np.median(itf.estimation_nmse_floor_db(real)))),
            }
    if md:
        out["measurement_domain"] = md
    elif str(cfg.get("link", "DL")).upper() != "BOTH":
        out["measurement_domain_note"] = (
            "link 不是 BOTH，没有测量域 SIR。要看 SRS/CSI-RS 受到的导频干扰，"
            "把 link 设成 BOTH 再探测。"
        )

    return out


def compare_probes(
    named_configs: dict[str, dict[str, Any]],
    num_samples: int = 30,
) -> dict[str, Any]:
    """并排探测多个场景，方便"这几个候选里选哪个"。

    每个场景独立探测，返回一张对照表 + 各自完整报告。
    """
    reports = {name: probe(cfg, num_samples) for name, cfg in named_configs.items()}
    table = []
    for name, r in reports.items():
        dl = (r["interference"] or {}).get("dl_iot") or {}
        table.append({
            "scenario": name,
            "num_cells": r["num_cells"],
            "iot_median_db": dl.get("median_db"),
            "iot_band": (dl.get("classification") or {}).get("band"),
            "sinr_median_db": r["link_budget"]["sinr_dB"].get("median"),
            "snr_median_db": r["link_budget"]["snr_dB"].get("median"),
            "pathloss_median_db": r["geometry"]["pathloss_dB"].get("median"),
            "los_ratio": r["geometry"]["los_ratio"],
            "seconds_per_sample": r["seconds_per_sample"],
        })
    return {"table": table, "reports": reports,
            "note": "探测模式只给几何量，谱效/吞吐类结论必须跑正式生成。"}
