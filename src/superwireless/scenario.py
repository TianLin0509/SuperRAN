"""场景快速探测。

生成一批全带宽信道要几十分钟；但"这个场景到底长什么样"——干扰多强、
覆盖多好、多少人在视距下——是**几何量**，和信道矩阵的频域分辨率无关。

实测（同一 seed、num_rb 取 273 / 24 / 12）：

===========================  ==================================
量                            num_rb 改变后
===========================  ==================================
sinr_dB / sir_dB              **逐位相同**
pathloss_dB / distance_3d_m   **逐位相同**
is_los / rx_power / doppler   **逐位相同**
tau_rms_ns（配置值）           **逐位相同**
ue_position                   **逐位相同**
上行几何 SIR                   **逐位相同**
---------------------------  ----------------------------------
snr_dB                        差 ``10log10(RB_full/RB_probe)``
===========================  ==================================

唯一变的是 ``snr_dB``，因为它的定义里显式带了 ``-10log10(RB)``
（``internal_sim.py:2441``）。273→24 实测差 10.56 dB，与
``10log10(273/24)=10.559`` 吻合到小数点后两位——所以这一项**可以精确还原**，
不是近似。

``num_ofdm_symbols`` 同理：14 降到 7 / 4 / 2 时上面那张表里的量**同样逐位
相同**，但降到 1 会让 ``sir_dB`` 偏 16.1 dB（见 ``PROBE_NUM_SYM`` 的注释）。

于是探测模式成立：``num_rb`` 压到 24、``num_ofdm_symbols`` 压到 4、
关掉 SSB 测量，几何量一个不差。实测（21 小区 64T 100 MHz、交错重测 3 轮取
中位数、基准自身波动 17.7%）：

===================================  ==========  =========
配置                                  毫秒/样本    相对
===================================  ==========  =========
全量（263 RB、14 符号、SSB 开）              2602      1.00x
只压 RB + 关 SSB                            374      6.95x
再压符号数                                   226     **11.51x**
===================================  ==========  =========

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

# 探测用的 OFDM 符号数（默认 14）。**这个旋钮有一道悬崖，位置是 1。**
#
# 实测同一 seed 下把 num_ofdm_symbols 从 14 降到 7 / 4 / 2，几何量（sinr / sir /
# 路损 / 距离 / 视距 / 多普勒 / UE 位置 / 上行几何 SIR）**全部逐位相同**；
# 降到 1 时 sir_dB 直接偏 16.1 dB。所以 2 是安全的，1 不是。
#
# 取 4 而不是 2：**离悬崖两格，不贴着边站**。2 已经验证过没问题，但只要
# ChannelHub 内部任何一处对符号数的假设动一动，2 就可能跟着塌，而这种塌陷
# 不报错——它只是给出一组看起来正常的错数。4 换来的是同一量级的加速。
#
# 注意：这个旋钮**只对探测模式安全**。正式生成里它会实打实地改变信道矩阵
# （14→7 时 h_true 相对差 2.5e-2，14→1 时差 4.3），因为存下来的单快照是在
# 这些符号上平均出来的。所以它不在 generate() 里，只在 probe() 里。
PROBE_NUM_SYM = 4
PROBE_NUM_SYM_CLIFF = 1  # 实测在此处几何量失真，别把 PROBE_NUM_SYM 降到这里

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

    # 符号数只往下压，不往上抬 —— 调用方要是显式给了更小的值，尊重它。
    sym_full = int(out.get("num_ofdm_symbols") or 14)
    out["num_ofdm_symbols"] = max(min(PROBE_NUM_SYM, sym_full), PROBE_NUM_SYM_CLIFF + 1)

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


def probe(
    cfg: dict[str, Any],
    num_samples: int = 30,
    *,
    keep_dataset: bool = False,
) -> dict[str, Any]:
    """快速探测一个场景的几何特征。默认不落盘，跑完就扔。

    参数
    ----
    cfg : 场景配置（和 ``sw_generate`` 用的是同一份）
    num_samples : 探测样本数。30 个足够看清中位数量级；要看 5% 分位建议 100+。
    keep_dataset : 保留探测数据集。默认 False —— 探测数据只覆盖 8.64 MHz，
        留着容易被误当成正式数据用。

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
            "没有便宜的探测版本。请直接用小 num_samples 跑 sw_generate。"
        )

    cfg_p, rb_probe, rb_full = probe_config(cfg)
    cfg_p.pop("source", None)

    from .generate import _align_to_ues, _ensure_bs_panel  # noqa: PLC0415

    panel, panel_derived = _ensure_bs_panel(cfg_p)
    n_ues = int(cfg_p.get("num_ues", 1) or 1)
    ask = _align_to_ues(int(num_samples), n_ues)

    # **每个 UE 至少要有 2 个样本，否则多普勒恒为 0。**
    # ChannelHub 的 doppler_hz 来自同一个 UE 相邻样本之间的位移，
    # samples_per_ue == 1 时没有位移可算。实测 hst_350kmh（21 个 UE）：
    #   num_samples=21 -> 每 UE 1 个 -> 多普勒中位 0.0 Hz
    #   num_samples=42 -> 每 UE 2 个 -> 多普勒中位 817.94 Hz
    # 一个 350 km/h 的场景探测出"多普勒 0"，任谁都会以为移动配置没生效。
    # 与其给个带脚注的 0，不如把样本数补够——探测本来就便宜。
    bumped_from = None
    speed = float(cfg_p.get("ue_speed_kmh", 0.0) or 0.0)
    moving = speed > 3.0 or str(cfg_p.get("mobility_mode", "static")) != "static"
    if moving and ask // max(n_ues, 1) < 2:
        bumped_from, ask = ask, n_ues * 2
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

    arr = {k: np.asarray(v, dtype=np.float64) for k, v in cols.items()}
    marr = {k: np.asarray(v, dtype=np.float64) for k, v in metas.items()}

    # snr_dB 还原成全带宽口径 —— 这是探测模式唯一需要修正的量。
    #
    # **但修正只对没被夹逼的样本成立。** ChannelHub 把 snr_dB 夹到 ±50 dB
    # （契约约束），而探测模式的 snr_dB 比全带宽口径高 10log10(RB_full/RB_probe)
    # ≈ 10.4 dB——高信噪比场景在探测下会**先撞上 +50 的天花板再被我减回去**，
    # 得到一个看起来正常的假值。实测 InF 与密集城区两个完全不同的场景，
    # 探测出来的 SNR 都是 39.5 dB，就是这么来的（49.9 - 10.4）。
    # 撞顶的样本必须剔除并报数，不能混进分布。
    corr = snr_correction_db(rb_probe, rb_full)
    snr_clamped = np.abs(arr["snr_dB"]) > 49.85
    ul_snr_clamped = np.abs(arr["ul_snr_dB"]) > 49.85
    snr_full = np.where(snr_clamped, np.nan, arr["snr_dB"] + corr)
    ul_snr_full = np.where(ul_snr_clamped, np.nan, arr["ul_snr_dB"] + corr)
    n_snr_clamped = int(np.sum(snr_clamped & np.isfinite(arr["snr_dB"])))

    cells = int(cfg.get("num_sites", 1) or 1) * int(cfg.get("sectors_per_site", 1) or 1)
    dl_iot = itf.iot_stats(arr["sinr_dB"], arr["sir_dB"])
    ul_iot = itf.iot_stats(arr["ul_sinr_dB"], arr["ul_sir_geo_dB"])

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
            "sinr_dB": _dist(arr["sinr_dB"]),
            "sir_dB": _dist(arr["sir_dB"]),
            "ul_snr_dB": _dist(ul_snr_full),
            "ul_sinr_dB": _dist(arr["ul_sinr_dB"]),
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
            "几何量与全量**逐位相同**（实测），"
            f"仅 snr_dB 需要 {corr:+.2f} dB 修正、已修正。整体约 11.5 倍速。"
            "要谱效/吞吐/时延扩展请跑正式生成。"
        ),
    }
    if bumped_from is not None:
        out["num_samples_note"] = (
            f"样本数从 {bumped_from} 提到 {ask}：这个场景配了移动"
            f"（{speed:g} km/h / {cfg_p.get('mobility_mode', 'static')}），"
            f"而多普勒来自同一个 UE 相邻样本之间的位移——每个 UE 只有 1 个样本时"
            f"它恒为 0。实测 hst_350kmh：21 样本报 0.0 Hz，42 样本报 817.94 Hz。"
        )

    if n_snr_clamped:
        out["link_budget"]["snr_note"] = (
            f"{n_snr_clamped}/{n} 个样本的 snr_dB 在探测口径下撞上了 ChannelHub 的 "
            f"+50 dB 夹逼上限，修正后会是假值，已从分布中剔除。"
            "SINR / SIR / IoT 不受影响（它们不含 10log10(RB) 项）。"
            "要准确的 SNR 分布请跑正式生成。"
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

    if keep_dataset:
        out["warning"] = (
            "keep_dataset=True：探测数据只覆盖部分带宽，不要拿它算谱效或做正式对比。"
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
