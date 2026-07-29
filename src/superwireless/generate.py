"""信道生成与落盘。

数据一律落盘，MCP 只回句柄和摘要——信道矩阵进不了对话上下文，
详见设计文档 v1 第二节。

存储用 .npz（numpy 原生，不依赖 torch），一个数据集一个目录。
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, Callable

import numpy as np

from . import channelhub as ch
from .paths import dataset_dir

_DEBUG = bool(os.environ.get("SUPERWIRELESS_DEBUG"))


def _dbg(msg: str) -> None:
    """打点只走 stderr —— stdio 传输下 stdout 是 JSON-RPC 通道。"""
    if _DEBUG:
        print(f"[sw.gen] {msg}", file=sys.stderr, flush=True)

# ChannelSample.meta 里逐样本收集的标量物理量。
# 这些是 internal_sim 生成过程中算出来的量，本来会被丢弃。
_SCALAR_META_FIELDS = (
    "pathloss_dB", "distance_3d_m", "is_los", "los_probability",
    "rx_power_serving_dbm", "doppler_hz", "sample_tau_rms_ns",
    "noise_power_dbm", "antenna_gain_serving_db", "tau_rms_ns",
    "rician_k_db", "num_taps", "serving_pci", "ue_id",
    "tx_power_dbm", "ue_tx_power_dbm", "noise_figure_db",
    "tdd_slot_direction", "srs_active_in_slot",
)

# 逐样本收集的顶层标量字段
_SCALAR_SAMPLE_FIELDS = (
    "snr_dB", "sinr_dB", "sir_dB", "noise_power_dBm",
    "serving_cell_id", "dl_rank", "slot_duration_s",
    "ul_pre_sinr_dB", "ul_snr_dB", "ul_sinr_dB",
)


def _as_float(v: Any) -> float:
    try:
        if v is None:
            return float("nan")
        if isinstance(v, bool):
            return float(v)
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def estimate_size_mb(cfg: dict[str, Any], num_samples: int) -> float:
    """预估落盘体积，MB。生成前告诉用户，免得跑完才发现几个 G。"""
    rb = int(cfg.get("num_rb") or _rb_from_bandwidth(cfg))
    bs = int(cfg.get("num_bs_tx_ant", 64))
    ue = int(cfg.get("num_ue_rx_ant", 4))
    t = int(cfg.get("num_slots_per_sample", 1) or 1)
    per = t * rb * bs * ue * 8  # complex64 = 8 字节
    return per * num_samples * 2 / 1e6  # 理想 + 估计两份


def _rb_from_bandwidth(cfg: dict[str, Any]) -> int:
    bw = float(cfg.get("bandwidth_hz", 100e6) or 100e6)
    scs = float(cfg.get("subcarrier_spacing", 30000) or 30000)
    return max(1, int(bw / (12 * scs) * 0.95))


def _align_to_ues(n: int, num_ues: int) -> int:
    """向上取整到 num_ues 的倍数。

    ChannelHub 要求 num_samples 能被 num_ues 整除（每个 UE 采样轮数相同）。
    这类耦合约束不该让用户操心——多生成几个，再截到用户要的数量。
    """
    if num_ues <= 1:
        return max(n, 1)
    return max(((n + num_ues - 1) // num_ues) * num_ues, num_ues)


def generate(
    cfg: dict[str, Any],
    *,
    num_samples: int = 200,
    snr_range_dB: list[float] | None = None,
    plan_markdown: str = "",
    draft_id: str = "",
    progress: Callable[[int, int], None] | None = None,
    max_attempts_factor: int = 5,
) -> dict[str, Any]:
    """生成数据集并落盘，返回句柄与摘要。

    snr_range_dB 用拒绝采样实现——internal_sim 没有直接设定信噪比的参数，
    信噪比由路损、发射功率和噪声共同决定，只能生成后筛。接受率会如实报告。
    """
    cfg = dict(cfg)
    source_name = str(cfg.pop("source", "internal_sim"))
    cfg["num_samples"] = int(num_samples)

    dataset_id = "ds_" + uuid.uuid4().hex[:8]
    out_dir = dataset_dir(dataset_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    lo, hi = (float(snr_range_dB[0]), float(snr_range_dB[1])) if snr_range_dB else (-np.inf, np.inf)
    filtering = np.isfinite(lo) or np.isfinite(hi)

    h_true: list[np.ndarray] = []
    h_est: list[np.ndarray] = []
    h_intf: list[np.ndarray] = []
    positions: list[np.ndarray] = []
    w_dl: list[np.ndarray] = []
    scalars: dict[str, list[float]] = {k: [] for k in _SCALAR_SAMPLE_FIELDS}
    metas: dict[str, list[Any]] = {k: [] for k in _SCALAR_META_FIELDS}
    ssb_rsrp: list[list[float]] = []
    ssb_sinr: list[list[float]] = []

    accepted = 0
    attempted = 0
    rejected = 0
    observed_sinr: list[float] = []  # 含被拒样本，用于失败时给出可操作的提示
    first_meta: dict[str, Any] = {}
    t0 = time.perf_counter()

    # 拒绝采样时多要一些样本；再对齐到 num_ues 的整数倍（ChannelHub 的约束）
    ask = int(num_samples * max_attempts_factor) if filtering else int(num_samples)
    n_ues = int(cfg.get("num_ues", 1) or 1)
    ask = _align_to_ues(ask, n_ues)
    cfg_run = dict(cfg)
    cfg_run["num_samples"] = ask

    _dbg(f"进入迭代 ask={ask} n_ues={n_ues} source={source_name}")
    for sample in ch.iter_samples(source_name, cfg_run):
        attempted += 1
        if attempted <= 2 or attempted % 20 == 0:
            _dbg(f"  收到第 {attempted} 个样本")
        sinr = _as_float(getattr(sample, "sinr_dB", None))
        if np.isfinite(sinr):
            observed_sinr.append(sinr)
        if filtering and not (lo <= sinr <= hi):
            rejected += 1
            if attempted >= ask:
                break
            continue

        ht = ch.serving_channel(sample, estimated=False)
        he = ch.serving_channel(sample, estimated=True)
        if ht is None:
            continue

        h_true.append(np.asarray(ht, dtype=np.complex64))
        h_est.append(np.asarray(he if he is not None else ht, dtype=np.complex64))

        hi_arr = getattr(sample, "h_interferers", None)
        if hi_arr is not None:
            h_intf.append(np.asarray(hi_arr, dtype=np.complex64))

        pos = getattr(sample, "ue_position", None)
        positions.append(np.asarray(pos, dtype=np.float64) if pos is not None else np.full(3, np.nan))

        w = getattr(sample, "w_dl", None)
        if w is not None:
            w_dl.append(np.asarray(w, dtype=np.complex64))

        for k in _SCALAR_SAMPLE_FIELDS:
            scalars[k].append(_as_float(getattr(sample, k, None)))

        meta = sample.meta if isinstance(sample.meta, dict) else {}
        if not first_meta:
            first_meta = {
                k: v for k, v in meta.items()
                if isinstance(v, (str, int, float, bool, type(None)))
            }
        for k in _SCALAR_META_FIELDS:
            metas[k].append(meta.get(k))

        ssb_rsrp.append(list(getattr(sample, "ssb_rsrp_dBm", None) or []))
        ssb_sinr.append(list(getattr(sample, "ssb_sinr_dB", None) or []))

        accepted += 1
        if progress:
            progress(accepted, num_samples)
        if accepted >= num_samples:
            break

    elapsed = time.perf_counter() - t0
    if accepted == 0:
        if filtering and observed_sinr:
            obs = np.asarray(observed_sinr)
            raise RuntimeError(
                f"信噪比筛选区间 [{lo:g}, {hi:g}] dB 与该场景的实际分布不重叠：\n"
                f"  尝试了 {attempted} 个样本，实际信噪比落在 "
                f"[{obs.min():.1f}, {obs.max():.1f}] dB（中位数 {np.median(obs):.1f} dB）。\n"
                f"信噪比由路损、发射功率和噪声共同决定，不能直接设定。可以：\n"
                f"  · 去掉筛选（snr_range_dB=null），先看自然分布\n"
                f"  · 把区间改到 [{obs.min():.0f}, {obs.max():.0f}] dB 之内\n"
                f"  · 想整体压低信噪比，调小 tx_power_dbm 或调大 isd_m"
            )
        raise RuntimeError(
            "没有生成出任何样本。"
            + (f"信噪比区间 [{lo:g}, {hi:g}] dB 全部被拒。" if filtering else "请检查配置。")
        )

    # ---- 落盘 ----
    payload: dict[str, np.ndarray] = {
        "h_true": np.stack(h_true),
        "h_est": np.stack(h_est),
        "ue_position": np.stack(positions),
    }
    if h_intf and all(a.shape == h_intf[0].shape for a in h_intf):
        payload["h_interferers"] = np.stack(h_intf)
    if w_dl and all(a.shape == w_dl[0].shape for a in w_dl):
        payload["w_dl"] = np.stack(w_dl)
    for k, vals in scalars.items():
        payload[f"scalar__{k}"] = np.asarray(vals, dtype=np.float64)
    for k, vals in metas.items():
        arr = np.asarray([_as_float(v) for v in vals], dtype=np.float64)
        if np.all(np.isnan(arr)):  # 非数值字段（如 tdd_slot_direction）存字符串
            payload[f"metastr__{k}"] = np.asarray([str(v) for v in vals])
        else:
            payload[f"meta__{k}"] = arr
    if ssb_rsrp and all(len(x) == len(ssb_rsrp[0]) for x in ssb_rsrp) and ssb_rsrp[0]:
        payload["ssb_rsrp_dBm"] = np.asarray(ssb_rsrp, dtype=np.float64)
        payload["ssb_sinr_dB"] = np.asarray(ssb_sinr, dtype=np.float64)

    _dbg(f"迭代结束 accepted={accepted}，开始写盘")
    np.savez_compressed(out_dir / "channels.npz", **payload)
    _dbg("写盘完成")

    shape = payload["h_true"].shape
    sinr_arr = payload["scalar__sinr_dB"]
    finite = sinr_arr[np.isfinite(sinr_arr)]

    # 六边形栅格会把站数吸附到环数（0→1 站、1→7 站、2→19 站），
    # 所以"配了 6 站"可能实际跑的是 7 站。这里对比配置值与实际值，
    # 不一致时在 summary 里显式记下——否则用户拿着错误的小区数下结论。
    cells_cfg = int(cfg.get("num_sites", 1) or 1) * int(cfg.get("sectors_per_site", 1) or 1)
    cells_real = first_meta.get("num_cells")
    topology_note = None
    if cells_real and int(cells_real) != cells_cfg:
        topology_note = (
            f"配置为 {cfg.get('num_sites')} 站 × {cfg.get('sectors_per_site')} 扇区 "
            f"= {cells_cfg} 小区，实际生成 {cells_real} 小区。"
            f"六边形栅格的站数只能是 1/7/19（按环数展开），会向上吸附。"
            f"需要精确站数请用 topology_layout='linear' 或 custom_site_positions。"
        )

    summary = {
        "dataset_id": dataset_id,
        "draft_id": draft_id,
        "source": source_name,
        "num_samples": int(accepted),
        "requested": int(num_samples),
        "cells_configured": cells_cfg,
        "cells_actual": int(cells_real) if cells_real else None,
        "topology_note": topology_note,
        "shape": {
            "N": int(shape[0]), "T": int(shape[1]), "RB": int(shape[2]),
            "BS_ant": int(shape[3]), "UE_ant": int(shape[4]),
        },
        "elapsed_s": round(elapsed, 2),
        "seconds_per_sample": round(elapsed / max(accepted, 1), 3),
        "size_mb": round((out_dir / "channels.npz").stat().st_size / 1e6, 1),
        "snr_filter": {
            "enabled": bool(filtering),
            "range_dB": [lo, hi] if filtering else None,
            "attempted": attempted,
            "rejected": rejected,
            "accept_rate": round(accepted / max(attempted, 1), 3),
        },
        "sinr_dB": _distribution(finite),
        "channel_model": first_meta.get("channel_model"),
        "scenario": first_meta.get("scenario"),
        "is_cdl": str(first_meta.get("channel_model", "")).upper().startswith("CDL"),
        "tau_rms_ns": first_meta.get("tau_rms_ns"),
        "config": cfg,
        "sample_meta": first_meta,
        "created_at": time.time(),
        "path": str(out_dir),
    }

    for key, label in (("meta__pathloss_dB", "pathloss_dB"),
                       ("meta__distance_3d_m", "distance_3d_m"),
                       ("meta__doppler_hz", "doppler_hz")):
        if key in payload:
            v = payload[key][np.isfinite(payload[key])]
            if v.size:
                summary[label] = _distribution(v)
    if "meta__is_los" in payload:
        los = payload["meta__is_los"]
        los = los[np.isfinite(los)]
        if los.size:
            summary["los_ratio"] = round(float(los.mean()), 3)

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    if plan_markdown:
        (out_dir / "plan.md").write_text(plan_markdown, encoding="utf-8")

    return summary


def _distribution(v: np.ndarray) -> dict[str, float]:
    if v.size == 0:
        return {}
    q = np.percentile(v, [5, 50, 95])
    return {
        "min": round(float(v.min()), 2),
        "p5": round(float(q[0]), 2),
        "median": round(float(q[1]), 2),
        "p95": round(float(q[2]), 2),
        "max": round(float(v.max()), 2),
        "mean": round(float(v.mean()), 2),
    }


def load_summary(dataset_id: str) -> dict[str, Any]:
    p = dataset_dir(dataset_id) / "summary.json"
    if not p.is_file():
        raise KeyError(f"找不到数据集 {dataset_id!r}")
    return json.loads(p.read_text(encoding="utf-8"))


def list_datasets() -> list[dict[str, Any]]:
    from .paths import datasets_dir

    out = []
    for d in sorted(datasets_dir().glob("ds_*"), key=lambda p: p.name):
        f = d / "summary.json"
        if f.is_file():
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            out.append(
                {
                    "dataset_id": s.get("dataset_id"),
                    "num_samples": s.get("num_samples"),
                    "shape": s.get("shape"),
                    "channel_model": s.get("channel_model"),
                    "scenario": s.get("scenario"),
                    "size_mb": s.get("size_mb"),
                    "created_at": s.get("created_at"),
                }
            )
    return out
