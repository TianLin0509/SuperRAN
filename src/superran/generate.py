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
from collections.abc import Callable
from typing import Any

import numpy as np

from . import channelhub as ch
from . import provenance
from .paths import dataset_dir

_DEBUG = bool(os.environ.get("SUPERRAN_DEBUG"))


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
    "rician_k_db", "num_taps", "serving_pci", "ue_id", "ue_id_source",
    "tx_power_dbm", "ue_tx_power_dbm", "noise_figure_db",
    "serving_cell_index", "dl_signal_power_mw", "dl_thermal_noise_power_mw",
    "dl_power_decomposition_version",
    "tdd_slot_direction", "srs_active_in_slot",
    "indexed_slot_rs_schedule_valid", "rs_opportunity_abstraction_used",
    "effective_channel_model",
)

# 每个样本、每个小区/扇区一项的大尺度量。不能塞进 scalar，也不能只留第一条。
_VECTOR_META_FIELDS = (
    "pathloss_all_db",
    "rx_power_all_dbm",
    "antenna_gain_all_db",
    "is_los_all",
    "los_probability_all",
    "sample_tau_rms_all_ns",
    "shadow_fading_all_db",
    "physical_site_group_ids",
    "effective_channel_model_all",
    "dl_interference_power_per_slot_per_cell_mw",
)

# 逐样本收集的顶层标量字段
#
# ``ul_sir_dB`` / ``dl_sir_dB`` 是**测量域**的量（导频上的信干比），和业务域的
# ``sir_dB``（几何 SIR）完全不是一回事——前者决定信道估计准不准，后者决定吞吐。
# 早先只收了业务域那个，于是"SRS 被邻区 UE 打穿"这类场景在数据里完全看不见。
_SCALAR_SAMPLE_FIELDS = (
    "snr_dB", "sinr_dB", "sir_dB", "noise_power_dBm",
    "serving_cell_id", "dl_rank", "slot_duration_s",
    "ul_pre_sinr_dB", "ul_snr_dB", "ul_sinr_dB",
    "ul_sir_dB", "dl_sir_dB", "num_interfering_ues",
)

# 从 ChannelSample.meta 交接、旧版 ChannelHub 才靠兼容钩子采集的字段。
# 见 interference.install_geometry_capture / take_ul_geometry_sir。
_HOOKED_SAMPLE_FIELDS = ("ul_sir_geo_dB",)


def _as_float(v: Any) -> float:
    try:
        if v is None:
            return float("nan")
        if isinstance(v, bool):
            return float(v)
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _stable_ue_identity(
    source_name: str,
    cfg: dict[str, Any],
    meta: dict[str, Any],
    *,
    attempted_index: int,
) -> tuple[Any, str]:
    """Return a stable per-trajectory UE id and its provenance.

    ChannelHub's current first-party sources do not put ``ue_id`` into every
    sample.  Inferring identity from ``ue_position`` is valid only for static
    users: one moving UE has a different coordinate at every snapshot and
    would be counted as many independent users by the statistical gates.

    The synthesis below mirrors the source iterators rather than guessing from
    coordinates:

    * ``internal_sim`` static samples cycle ``global_index % num_ues``;
    * ``internal_sim`` mobility is one continuous serving-UE trajectory;
    * ``sionna_rt`` currently generates one serving position/trajectory per
      dataset, in both static and mobility modes.

    Unknown/external sources are never assigned an invented identity.  Their
    missing id remains visible so mobility inference is blocked safely.
    """
    raw = meta.get("ue_id")
    if raw is not None:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return raw, "invalid_source_meta"
        if np.isfinite(value) and value >= 0 and value.is_integer():
            return int(value), "source_meta"
        return raw, "invalid_source_meta"

    source = str(source_name).strip().lower()
    if source == "internal_sim":
        mobility = str(cfg.get("mobility_mode", "static")).strip().lower()
        # 镜像源端布局合同（internal_sim.iter_samples）：mobility=="static"
        # 或 ue_speed_kmh<=0 时走静态多 UE 轮转布局；移动模式且速度为正
        # 才生成一条服务轨迹。速度键缺失时源端默认 3.0 km/h（_dict_get 的
        # 默认值），这里必须同步，否则会把轨迹误判成静态。
        speed = _as_float(cfg.get("ue_speed_kmh", 3.0))
        if mobility == "static" or (np.isfinite(speed) and speed <= 0.0):
            n_ues = max(int(cfg.get("num_ues", 1) or 1), 1)
            offset = int(cfg.get("sample_index_offset", 0) or 0)
            return (offset + int(attempted_index)) % n_ues, "internal_sim_global_index"
        if not np.isfinite(speed):
            # 速度非有限：源端行为不可预测，不发明身份，交给统计门 block。
            return None, "unavailable_mobility_speed"
        return 0, "internal_sim_single_trajectory"
    if source == "sionna_rt":
        return 0, "sionna_rt_single_trajectory"
    return None, "unavailable"


def _slot_snapshot(h: Any, *, time_axis: int = 0) -> np.ndarray:
    """Reduce ChannelHub's intra-slot OFDM-symbol grid to one slot snapshot.

    System-level simulation advances once per slot/TTI.  Averaging complex H
    across 14 symbols can cancel phase and invent fading, so the contract keeps
    the middle symbol with a length-one time axis.  Link-level/estimation still
    used all symbols before this storage-boundary reduction.
    """
    arr = np.asarray(h, dtype=np.complex64)
    if arr.ndim <= time_axis or arr.shape[time_axis] <= 1:
        return arr
    middle = arr.shape[time_axis] // 2
    selector = [slice(None)] * arr.ndim
    selector[time_axis] = slice(middle, middle + 1)
    return arr[tuple(selector)]


def estimate_size_mb(cfg: dict[str, Any], num_samples: int) -> float:
    """预估落盘体积，MB。生成前告诉用户，免得跑完才发现几个 G。"""
    rb = int(cfg.get("num_rb") or _rb_from_bandwidth(cfg))
    bs = int(cfg.get("num_bs_tx_ant", 64))
    ue = int(cfg.get("num_ue_rx_ant", 4))
    t = int(cfg.get("num_slots_per_sample", 1) or 1)
    per = t * rb * bs * ue * 8  # complex64 = 8 字节
    # paired/BOTH now retains DL truth, DL estimate, UL truth and the
    # reciprocity-mapped precoding estimate.  Single-direction data keeps the
    # historical truth+estimate pair.  Optional interferer tensors remain a
    # separate, explicitly disclosed storage multiplier.
    arrays = 4 if str(cfg.get("link", "DL")).upper() == "BOTH" else 2
    return per * num_samples * arrays / 1e6


def _rb_from_bandwidth(cfg: dict[str, Any]) -> int:
    """Return the standardized NR carrier grid for ``bandwidth_hz``/SCS.

    A former implementation multiplied ``BW/(12*SCS)`` by 0.95.  That is not
    a valid inverse of the NR bandwidth table: for example it returned 52 RB
    for 20 MHz @ 30 kHz while 3GPP TS 38.104 defines 51.  Apart from changing
    tensor shapes, that one-RB error also changed noise bandwidth, RBG packing,
    TBS, probe corrections, and runtime estimates.  There must be one source
    of truth, so generation now uses the same table as ``physical.nr_rb_count``.

    Non-standard bandwidth/SCS pairs deliberately raise.  A caller that really
    wants a synthetic grid can provide ``num_rb`` explicitly instead of getting
    a silent approximation.
    """
    from .carrier import scs_khz_from_config  # noqa: PLC0415
    from .physical import nr_rb_count  # noqa: PLC0415

    raw_bw = cfg["bandwidth_hz"] if "bandwidth_hz" in cfg else 100e6
    try:
        bw = float(raw_bw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bandwidth_hz 必须是 Hz 数值，收到 {raw_bw!r}") from exc
    if not np.isfinite(bw) or bw <= 0:
        raise ValueError(f"bandwidth_hz 必须是有限正数，收到 {raw_bw!r}")
    scs = float(scs_khz_from_config(cfg) * 1000)
    return nr_rb_count(bw, scs)


# 端口数 → [n_h, n_v, n_p] 面板排布。按 3GPP AAU 的常规做法：
# 双极化、水平优先铺满，再往垂直方向长。
_PANEL_BY_PORTS = {
    2: [1, 1, 2], 4: [2, 1, 2], 8: [2, 2, 2], 16: [4, 2, 2], 32: [8, 2, 2],
    64: [8, 4, 2], 96: [8, 6, 2], 128: [16, 4, 2], 192: [16, 6, 2], 256: [16, 8, 2],
}


def _panel_from_ports(n_ports: int) -> list[int]:
    """把端口数拆成面板排布。

    ``bs_panel`` 决定端口的二维几何、双极化排布，以及 64T/256T 是否能启用
    已确认的 1 驱 3 / 1 驱 6 effective-subarray。当前 first-party 几何 SINR 已不再依赖旧 DFT
    码本分支，但信道矩阵、固定子阵方向图和预编码仍必须知道面板结构。

    对不在表里的端口数，退化成单极化水平线阵——能让码本建起来，
    但排布不一定符合用户预期，所以调用方应在摘要里如实说明。
    """
    n = max(int(n_ports), 1)
    if n in _PANEL_BY_PORTS:
        return list(_PANEL_BY_PORTS[n])
    if n % 2 == 0:
        return [n // 2, 1, 2]
    return [n, 1, 1]


def _ensure_bs_panel(cfg: dict[str, Any]) -> tuple[list[int], bool]:
    """确保 cfg 里有 bs_panel，返回 (排布, 是否为推导得来)。"""
    raw = cfg.get("bs_panel")
    if raw:
        p = [int(x) for x in raw]
        if len(p) != 3 or any(v < 1 for v in p):
            raise ValueError("bs_panel 必须是三个正整数 [N_H, N_V, N_P]")
        declared = cfg.get("num_bs_tx_ant")
        if declared is not None and int(declared) != p[0] * p[1] * p[2]:
            raise ValueError(
                f"bs_panel {p} 与 num_bs_tx_ant={declared} 矛盾；"
                "两个都给时必须一致，只给一个即可自动推导")
        cfg["num_bs_tx_ant"] = p[0] * p[1] * p[2]
        cfg["num_bs_rx_ant"] = p[0] * p[1] * p[2]
        return p, False
    panel = _panel_from_ports(int(cfg.get("num_bs_tx_ant", 64) or 64))
    cfg["bs_panel"] = panel
    return panel, True


def _ensure_ue_panel(cfg: dict[str, Any]) -> tuple[list[int], bool]:
    """确保 4R 等 UE 端口数有明确的阵列几何。

    没写 ``ue_panel`` 时按与端口数一致的双极化面板推导；例如 4R 得到
    ``[2, 1, 2]``。这是可覆盖的工程假设，不是终端天线实测值。
    与 BS 一样不把面板写死在 preset 里，才能让用户覆盖端口数时同步生效。
    """
    raw = cfg.get("ue_panel")
    if raw:
        p = [int(x) for x in raw]
        if len(p) != 3 or any(v < 1 for v in p):
            raise ValueError("ue_panel 必须是三个正整数 [N_H, N_V, N_P]")
        cfg["num_ue_rx_ant"] = p[0] * p[1] * p[2]
        return p, False
    panel = _panel_from_ports(int(cfg.get("num_ue_rx_ant", 4) or 4))
    cfg["ue_panel"] = panel
    return panel, True


def _align_to_ues(n: int, num_ues: int) -> int:
    """向上取整到 num_ues 的倍数。

    ChannelHub 要求 num_samples 能被 num_ues 整除（每个 UE 采样轮数相同）。
    这类耦合约束不该让用户操心——多生成几个，再截到用户要的数量。
    """
    if num_ues <= 1:
        return max(n, 1)
    return max(((n + num_ues - 1) // num_ues) * num_ues, num_ues)


def _requested_worker_count(
    workers: int | str,
    num_samples: int,
    cfg: dict[str, Any],
) -> int:
    """Resolve the CPU-work estimate before ChannelHub batch constraints."""
    if isinstance(workers, int) and workers != 0:
        n = workers
    else:
        est_total_s = estimate_seconds(cfg, num_samples)
        if est_total_s < _PARALLEL_MIN_TOTAL_S:
            return 1
        n = max(2, int(est_total_s // _MIN_WORK_S))
    if n <= 1:
        return 1
    return max(1, min(int(n), num_samples, (os.cpu_count() or 4)))


def _worker_batch_cap(num_samples: int, cfg: dict[str, Any]) -> int:
    """Cap workers so a ChannelHub UE batch is not repeated per tiny chunk.

    ChannelHub constructs samples in batches whose size is aligned to
    ``num_ues``.  Giving 20 one-sample chunks to a 20-UE scenario therefore
    makes every child process construct a 20-sample batch and discard 19
    samples.  The numerical stream stays correct, but work and memory can grow
    by almost 20x.  At most one worker per (possibly partial) UE batch avoids
    that amplification while retaining exact global ``sample_index`` slicing.
    """
    n = max(int(num_samples), 1)
    n_ues = max(int(cfg.get("num_ues", 1) or 1), 1)
    return max(1, min(n, (n + n_ues - 1) // n_ues))


def _resolve_workers(workers: int | str, num_samples: int, cfg: dict[str, Any]) -> int:
    """决定实际使用几个进程。

    ``"auto"`` 时按**粗略串行工作量**决定。20-ray CDL 落地后旧的 24 ms
    单样本标定已失效；当前热态锚点从 0.158 s 到 7.48 s/样本不等。这个估计
    只用于决定是否值得支付 Windows spawn/import 成本，不是向用户承诺的 ETA。
    """
    # 每个子进程要重新 import numpy/scipy/ChannelHub，实测约 4 秒。
    # 所以先按总工作量决定并行度，再按 UE 批次上限收口。第二层很重要：
    # 否则“20 样本 / 20 UE / 20 workers”会在 20 个进程里各构造一整个
    # 20-UE batch，变成接近 400 个样本的内部工作量。
    requested = _requested_worker_count(workers, num_samples, cfg)
    return min(requested, _worker_batch_cap(num_samples, cfg))


# 并行的两个门槛：总活少于这个数就不值得起进程；每个 worker 至少要分到这么多活。
_PARALLEL_MIN_TOTAL_S = 30.0
_MIN_WORK_S = 20.0


def estimate_seconds(cfg: dict[str, Any], num_samples: int) -> float:
    """Estimate serial work for worker scheduling, not a wall-clock SLA.

    Calibration revision ``20ray-2026-08-11`` (this workstation, warm Python
    process, ``internal_sim``):

    * 1 cell, 32T, 20 MHz/30 kHz: 8 and 24 sample batches both 0.158 s/sample;
    * 1 cell, 64T, 100 MHz/30 kHz: 8 sample batch 1.074 s/sample;
    * 21 cells, 16T, 20 MHz/30 kHz: 24 samples took 179.5 s, 7.48 s/sample.

    Cold one-sample calls varied from 1.15 to 3.03 s, so initialization and
    cache state dominate tiny batches.  Unmeasured configurations, RT scenes,
    CPU load, and future kernels can differ substantially.  The model is kept
    deliberately simple and conservative enough for the binary scheduling
    decision; callers must use returned ``elapsed_s`` for an actual run.
    """
    if num_samples <= 0:
        return 0.0
    cells = int(cfg.get("num_sites", 1) or 1) * int(cfg.get("sectors_per_site", 1) or 1)
    ants = int(cfg.get("num_bs_tx_ant", 64) or 64)
    rb = int(cfg.get("num_rb") or _rb_from_bandwidth(cfg))

    # observed sub-linear antenna/RB scaling: the 64T/273-RB estimate (old
    # calibration; current product grid is 272 RB) is
    # observed sub-linear antenna/RB scaling: the 64T/273-RB estimate is
    # 1.24 s versus the recorded 1.074 s (16% high).
    seconds_per_sample = 0.160 * (ants / 32.0) ** 0.9 * (rb / 51.0) ** 0.85
    if cells > 1:
        # Full 20-ray interfering links made the old 14x cap invalid.  A linear
        # interpolation to the measured 21-cell multiplier (91x relative to
        # the 32T/51-RB anchor) predicts the 21c/16T point within 5%.
        # This is intentionally a scheduling heuristic, not an extrapolated
        # performance claim for every antenna/bandwidth combination.
        seconds_per_sample *= 1.0 + 90.0 * min(1.0, (cells - 1) / 20.0)
    if str(cfg.get("scenario", "")) in ("munich", "custom_osm", "etoile",
                                        "florence", "san_francisco"):
        # RT cost is geometry/ray-count dependent and not covered by the CDL
        # anchors.  Keep a floor solely to avoid choosing serial for a large job.
        seconds_per_sample = max(seconds_per_sample, 3.0)
    # The historical SSB-off 0.72 factor was measured before the 20-ray kernel
    # and is no longer used: an unverified factor must not drive process count.
    return seconds_per_sample * num_samples


def _run_parallel(
    source_name: str,
    cfg_run: dict[str, Any],
    *,
    num_samples: int,
    n_workers: int,
    lo: float,
    hi: float,
    filtering: bool,
    base_seed: int,
    n_ues: int,
    ask_factor: int,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, Any], int, int, int, list[float]]:
    """把一个确定性样本流按全局 index 切块后交给进程池。

    每个 worker 保持相同 ``seed``，只设置互不重叠的
    ``sample_index_offset``。因此 static ``internal_sim`` 的几何、LSP、SRS
    时序和 fading stream 与串行生成逐样本相同；worker 数只改变执行调度。
    不支持这一合同的 source/mobility/filtering 会在上层显式回退串行。
    """
    import tempfile
    from concurrent.futures import ProcessPoolExecutor

    per = [num_samples // n_workers] * n_workers
    for i in range(num_samples % n_workers):
        per[i] += 1
    per = [p for p in per if p > 0]

    tmpdir = tempfile.mkdtemp(prefix="sr_gen_")
    jobs = []
    sample_offset = int(cfg_run.get("sample_index_offset", 0) or 0)
    for k, want in enumerate(per):
        c = dict(cfg_run)
        c["seed"] = base_seed
        c["sample_index_offset"] = sample_offset
        c["num_samples"] = _align_to_ues(want * ask_factor, n_ues)
        jobs.append((source_name, c, want, lo, hi, filtering,
                     os.path.join(tmpdir, f"chunk{k}.npz")))
        sample_offset += want

    paths: list[str] = []
    first_meta: dict[str, Any] = {}
    acc = att = rej = 0
    observed: list[float] = []
    done = 0
    with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
        for path, fm, st in pool.map(_chunk_worker, jobs):
            if path:
                paths.append(path)
            if fm and not first_meta:
                first_meta = fm
            acc += st["accepted"]
            att += st["attempted"]
            rej += st["rejected"]
            observed.extend(st["observed_sinr"])
            done += 1
            if progress:
                progress(min(acc, num_samples), num_samples)
            _dbg(f"  worker {done}/{len(jobs)} 回来，累计 {acc} 个样本")

    payload, dropped_fields = _merge_chunks(paths)
    # 各块可能多给一两个（对齐到 num_ues 的整数倍），统一截到要的数量
    if payload:
        n_have = len(next(iter(payload.values())))
        if n_have > num_samples:
            payload = {k: v[:num_samples] for k, v in payload.items()}
            acc = num_samples
    try:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
    except OSError:
        pass
    return payload, first_meta, acc, att, rej, observed, dropped_fields


def _parallel_exactness_blocker(
    source_name: str,
    cfg: dict[str, Any],
    *,
    filtering: bool,
) -> str | None:
    """Return why worker-count-invariant partitioning is unavailable."""
    if source_name != "internal_sim":
        return (
            f"source={source_name!r} 尚未实现全局 sample_index_offset；"
            "为保持逐样本语义改用串行"
        )
    if filtering:
        return "拒绝采样会改变各分块消费的全局 index；为保持同一事件流改用串行"
    mobility = str(cfg.get("mobility_mode", "static"))
    speed = float(cfg.get("ue_speed_kmh", 0.0) or 0.0)
    if mobility != "static" and speed > 0.0:
        return "移动轨迹跨样本有状态，尚不能无缝分块；为保持连续轨迹改用串行"
    return None


def _collect(
    source_name: str,
    cfg_run: dict[str, Any],
    *,
    want: int,
    lo: float,
    hi: float,
    filtering: bool,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, Any], dict[str, Any]]:
    """跑一批样本并打包成落盘用的数组字典。

    串行路径与每个并行 worker 用的都是这一个函数——**共用一份实现**，
    否则两条路径迟早会漂移，而漂移只在"并行结果和串行对不上"时才暴露。

    返回 ``(payload, first_meta, stats)``。stats 里带尝试数/拒绝数/观察到的
    信噪比，供上层合并统计与报错。
    """
    from . import interference as intf_mod  # noqa: PLC0415

    # 上行几何 SIR 在 ChannelSample 顶层没有位置。当前 first-party 后端用稳定
    # meta 键交接；安装器只为旧 checkout 保留兼容钩子，挂不上不影响新路径。
    intf_mod.install_geometry_capture()

    h_true: list[np.ndarray] = []
    h_ul_true: list[np.ndarray] = []
    h_est: list[np.ndarray] = []
    h_dl_est: list[np.ndarray] = []
    precoding_csi_sources: list[str] = []
    h_intf: list[np.ndarray] = []
    positions: list[np.ndarray] = []
    source_precoder_fields_ignored = 0
    scalars: dict[str, list[float]] = {
        k: [] for k in (*_SCALAR_SAMPLE_FIELDS, *_HOOKED_SAMPLE_FIELDS)
    }
    metas: dict[str, list[Any]] = {k: [] for k in _SCALAR_META_FIELDS}
    vector_metas: dict[str, list[np.ndarray]] = {k: [] for k in _VECTOR_META_FIELDS}
    ssb_rsrp: list[list[float]] = []
    ssb_sinr: list[list[float]] = []

    accepted = attempted = rejected = 0
    observed_sinr: list[float] = []
    first_meta: dict[str, Any] = {}
    ask = int(cfg_run.get("num_samples", want))

    for sample in ch.iter_samples(source_name, cfg_run):
        attempted += 1
        # 参数名是 snr_range_dB，过滤量就必须是 SNR：多小区场景 SINR 与
        # SNR 可差 30+ dB，拿 SINR 截出的样本其 SNR 分布可以完全落在用户
        # 区间之外（曾确实如此——参数名、文档与报错全都承诺 SNR）。
        snr = _as_float(getattr(sample, "snr_dB", None))
        if np.isfinite(snr):
            observed_sinr.append(snr)
        if filtering and not (lo <= snr <= hi):
            rejected += 1
            if attempted >= ask:
                break
            continue

        ht, he, hde, csi_source = ch.downlink_and_precoding_channels(sample)
        if ht is None:
            continue

        if he is None:
            raise RuntimeError(
                "信道源返回了 h_true，但没有 h_est；禁止静默把 h_true 复制成完美估计。"
                f"source={source_name!r}, channel_est_mode={cfg_run.get('channel_est_mode')!r}, "
                f"sample={attempted - 1}"
            )

        ht_arr = _slot_snapshot(ht)
        he_arr = _slot_snapshot(he)
        if ht_arr.shape != he_arr.shape:
            raise RuntimeError(
                f"下行真值/预编码 CSI 形状不一致：{ht_arr.shape} vs {he_arr.shape}。"
                "TDD SRS 要设计 64x4 下行权时，UE SRS 必须也是 4 端口；"
                "请把 num_ue_tx_ant 与 num_ue_rx_ant 对齐。"
            )
        if not np.isfinite(ht_arr).all() or not np.isfinite(he_arr).all():
            raise RuntimeError("h_true/h_est 含 NaN 或 Inf，拒绝落盘")

        h_true.append(ht_arr)
        h_est.append(he_arr)
        precoding_csi_sources.append(csi_source)
        hut = getattr(sample, "h_ul_true", None)
        if hut is not None:
            hut_arr = _slot_snapshot(hut)
            if hut_arr.shape != ht_arr.shape:
                raise RuntimeError(
                    "h_ul_true 与 h_dl_true 的 canonical 轴不一致："
                    f"{hut_arr.shape} vs {ht_arr.shape}"
                )
            if not np.isfinite(hut_arr).all():
                raise RuntimeError("h_ul_true 含 NaN 或 Inf，拒绝落盘")
            h_ul_true.append(hut_arr)
        if hde is not None:
            hde_arr = _slot_snapshot(hde)
            if hde_arr.shape != ht_arr.shape:
                raise RuntimeError(
                    f"h_dl_est 与 h_dl_true 形状不一致：{hde_arr.shape} vs {ht_arr.shape}"
                )
            if not np.isfinite(hde_arr).all():
                raise RuntimeError("h_dl_est 含 NaN 或 Inf，拒绝落盘")
            h_dl_est.append(hde_arr)

        hi_arr = getattr(sample, "h_interferers", None)
        if hi_arr is not None:
            # [cell, symbol, RB, BS, UE] -> keep one symbol, preserving axis.
            h_intf.append(_slot_snapshot(hi_arr, time_axis=1))

        pos = getattr(sample, "ue_position", None)
        positions.append(
            np.asarray(pos, dtype=np.float64) if pos is not None else np.full(3, np.nan)
        )

        # ``w_dl`` 是信道源的派生权，其轴序、共轭与功率约束可随
        # 外部实现漂移。SuperRAN 只导入原始/估计信道，之后用自己的
        # beamforming + power-constraint 模块重算发射权；这个字段即使
        # 存在也不再落盘。
        if getattr(sample, "w_dl", None) is not None:
            source_precoder_fields_ignored += 1

        for k in _SCALAR_SAMPLE_FIELDS:
            scalars[k].append(_as_float(getattr(sample, k, None)))
        # 当前优先从本样本 metadata 取；旧内核才读取“上一次调用”的兼容暂存，
        # 因此仍紧跟 sample 处理，避免旧路径串样本。
        scalars["ul_sir_geo_dB"].append(intf_mod.take_ul_geometry_sir(sample))

        meta = dict(sample.meta) if isinstance(sample.meta, dict) else {}
        ue_id, ue_id_source = _stable_ue_identity(
            source_name,
            cfg_run,
            meta,
            attempted_index=attempted - 1,
        )
        meta["ue_id"] = ue_id
        meta["ue_id_source"] = ue_id_source
        if not first_meta:
            first_meta = {
                k: v for k, v in meta.items()
                if isinstance(v, (str, int, float, bool, type(None)))
            }
            if isinstance(meta.get("channel_contract"), dict):
                first_meta["channel_contract"] = dict(meta["channel_contract"])
        for k in _SCALAR_META_FIELDS:
            metas[k].append(meta.get(k))
        for k in _VECTOR_META_FIELDS:
            if k in meta:
                vector_metas[k].append(np.asarray(meta[k]))

        ssb_rsrp.append(list(getattr(sample, "ssb_rsrp_dBm", None) or []))
        ssb_sinr.append(list(getattr(sample, "ssb_sinr_dB", None) or []))

        accepted += 1
        if progress:
            progress(accepted, want)
        if accepted >= want:
            break

    stats = {
        "accepted": accepted, "attempted": attempted, "rejected": rejected,
        "observed_sinr": observed_sinr,
        "source_precoder_fields_ignored": source_precoder_fields_ignored,
    }
    if accepted == 0:
        return {}, first_meta, stats

    payload: dict[str, np.ndarray] = {
        "h_true": np.stack(h_true),
        "h_est": np.stack(h_est),
        "ue_position": np.stack(positions),
        "metastr__precoding_csi_source": np.asarray(precoding_csi_sources),
    }
    if len(h_ul_true) == accepted:
        payload["h_ul_true"] = np.stack(h_ul_true)
    if len(h_dl_est) == accepted:
        payload["h_dl_est"] = np.stack(h_dl_est)
    if len(h_intf) == accepted and h_intf and all(a.shape == h_intf[0].shape for a in h_intf):
        payload["h_interferers"] = np.stack(h_intf)
    for k, vals in scalars.items():
        payload[f"scalar__{k}"] = np.asarray(vals, dtype=np.float64)
    for k, vals in metas.items():
        arr = np.asarray([_as_float(v) for v in vals], dtype=np.float64)
        if np.all(np.isnan(arr)):  # 非数值字段（如 tdd_slot_direction）存字符串
            payload[f"metastr__{k}"] = np.asarray([str(v) for v in vals])
        else:
            payload[f"meta__{k}"] = arr
    for k, vals in vector_metas.items():
        if len(vals) == accepted and vals and all(a.shape == vals[0].shape for a in vals):
            payload[f"metavec__{k}"] = np.stack(vals)
    if ssb_rsrp and all(len(x) == len(ssb_rsrp[0]) for x in ssb_rsrp) and ssb_rsrp[0]:
        payload["ssb_rsrp_dBm"] = np.asarray(ssb_rsrp, dtype=np.float64)
        payload["ssb_sinr_dB"] = np.asarray(ssb_sinr, dtype=np.float64)
    contract = first_meta.setdefault("channel_contract", {})
    if not isinstance(contract, dict):
        contract = {}
        first_meta["channel_contract"] = contract
    unique_sources = sorted(set(precoding_csi_sources))
    contract.update({
        "h_true_role": "downlink physical evaluation channel",
        "h_ul_true_role": (
            "physical uplink channel on canonical [time,rb,bs_port,ue_port] axes; "
            "retained for allocator-driven SRS waveform reception"
            if len(h_ul_true) == accepted else
            "not available from this source; ideal reciprocity fallback must be explicit"
        ),
        "h_est_role": (
            "gNB precoding CSI; canonical UL SRS estimate is "
            "reciprocity-mapped back to the DL complex convention for paired/BOTH data"
        ),
        "h_dl_est_role": (
            "UE-side CSI-RS estimate retained separately; not used as SRS precoding CSI"
            if len(h_dl_est) == accepted else "not available in legacy/single data"
        ),
        "precoding_csi_sources": unique_sources,
        "reciprocity_contract_version": ch.SUPERRAN_RECIPROCITY_CONTRACT,
        "canonical_channel_axes": list(ch.SUPERRAN_CANONICAL_CHANNEL_AXES),
        "ul_to_dl_precoding_map": "h_precoding_est = conjugate(h_ul_est)",
        "precoder_ownership": (
            "SuperRAN recomputes every transmit weight from normalized h_est; "
            "source-provided w_dl is ignored and never written to new datasets"
        ),
        "ue_identity": (
            "stable source metadata when available; otherwise synthesized from "
            "the documented first-party iterator contract, never from a moving coordinate"
        ),
        "ofdm_to_slot_reduction": (
            "middle-symbol snapshot; no complex averaging; all OFDM symbols "
            "were retained through channel estimation before reduction"
        ),
    })
    return payload, first_meta, stats


def _chunk_worker(args: tuple) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """并行 worker：生成一块、落到临时 npz、回句柄。

    **不把数组通过 pickle 传回主进程**——200 个样本的信道有几百 MB，
    走 IPC 既慢又容易撞进程间内存上限。落盘再由主进程合并便宜得多。

    子进程必须自己 ``warmup()``：scipy 的 C 扩展在工作线程/新进程里首次
    加载会撞 import 死锁，这条铁律对子进程同样成立（见 CLAUDE.md）。
    """
    source_name, cfg_run, want, lo, hi, filtering, tmp_path = args

    # **必须在 import numpy 之前把 BLAS 线程数压到 1。**
    # 否则每个 worker 各自开满 CPU 核数的线程：20 个 worker × 20 线程 = 400 个
    # 线程抢 20 个核，上下文切换的开销吃掉全部并行收益。实测不设的话
    # 10 个 worker 只有 1.34 倍加速，设了之后才拿到应有的加速比。
    # 进程级并行 + 单线程 BLAS 是数值计算里的标准组合。
    for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[_v] = "1"

    from . import channelhub as _ch

    _ch.warmup()
    payload, first_meta, stats = _collect(
        source_name, cfg_run, want=want, lo=lo, hi=hi, filtering=filtering
    )
    if payload:
        np.savez(tmp_path, **payload)
    return (tmp_path if payload else "", first_meta, stats)


def _merge_chunks(paths: list[str]) -> tuple[dict[str, np.ndarray], list[str]]:
    """把各 worker 落的 npz 沿样本轴拼起来。

    只保留**所有块都有**的字段：某块缺 h_interferers 而别块有时，拼出来会
    出现长度不一致的数组，后面读取会错位——宁可丢掉那个字段并在摘要里说明。
    """
    if not paths:
        return {}, []
    opened = [np.load(p, allow_pickle=False) for p in paths]
    try:
        common = set(opened[0].files)
        for z in opened[1:]:
            common &= set(z.files)
        all_keys: set[str] = set()
        for z in opened:
            all_keys |= set(z.files)
        dropped = sorted(all_keys - common)
        out: dict[str, np.ndarray] = {}
        for k in sorted(common):
            arrs = [z[k] for z in opened]
            if any(a.shape[1:] != arrs[0].shape[1:] for a in arrs):
                dropped.append(k)  # 形状不一致的字段直接丢，不做危险的补齐
                continue
            out[k] = np.concatenate(arrs, axis=0)
        return out, sorted(set(dropped))
    finally:
        for z in opened:
            z.close()


def generate(
    cfg: dict[str, Any],
    *,
    num_samples: int = 200,
    snr_range_dB: list[float] | None = None,
    plan_markdown: str = "",
    draft_id: str = "",
    prereg_id: str = "",
    progress: Callable[[int, int], None] | None = None,
    max_attempts_factor: int = 5,
    workers: int | str = 1,
    collect_ssb: bool | None = None,
) -> dict[str, Any]:
    """生成数据集并落盘，返回句柄与摘要。

    snr_range_dB 用拒绝采样实现——internal_sim 没有直接设定信噪比的参数，
    信噪比由路损、发射功率和噪声共同决定，只能生成后筛。接受率会如实报告。

    collect_ssb 控制要不要算每小区的 SSB RSRP/SINR。关掉会减少工作量，但
    加速比依赖信道内核、阵列与小区数；20-ray 内核落地后旧的 30% 标定不再
    当作当前承诺。代价是 ``Dataset.ssb`` 为空，小区选择、切换、波束管理类
    课题用不了。默认 None = 跟随配置里的 ``measurements.ssb_rsrp``，都没给
    就保留（**不静默减少数据**）。实际耗时以返回的 ``elapsed_s`` 为准。
    """
    cfg = dict(cfg)
    validated_prereg = None
    if prereg_id:
        from . import analysis as an  # noqa: PLC0415

        validated_prereg = an.load(prereg_id)
        if not an.verify(validated_prereg):
            raise ValueError(
                f"预注册 {prereg_id!r} 的摘要与内容不一致；"
                "拒绝生成，不能把被手改的口径标成生成前锁定"
            )
        if (validated_prereg.draft_id and draft_id
                and validated_prereg.draft_id != str(draft_id)):
            raise ValueError(
                f"预注册绑定 draft {validated_prereg.draft_id!r}，"
                f"本次生成使用 {draft_id!r}；拒绝跨 Draft 复用"
            )
    # 早期体验方案曾把 LMMSE 档写成 ``ls_lmmse``；ChannelHub 与公开配置的
    # 稳定枚举一直是 ``ls_mmse``（实现本质是 LS 后的频域 LMMSE）。保留别名
    # 只用于读旧配置，所有新产物都落 canonical 名称。
    if cfg.get("channel_est_mode") == "ls_lmmse":
        cfg["channel_est_mode"] = "ls_mmse"
    source_name = str(cfg.pop("source", "internal_sim"))
    # 外部源的隐式默认曾从5 ms改成0.5 ms。系统层不能再从SRS/CSI-RS周期
    # 猜快照间隔；新数据一律把SuperRAN的5-ms默认显式写进配置，用户显式值优先。
    from . import hardware as hw  # noqa: PLC0415

    raw_sample_interval = cfg.get("sample_interval_s")
    if raw_sample_interval is None:
        cfg["sample_interval_s"] = float(hw.COMPANY_SNAPSHOT_INTERVAL_S)
    else:
        if isinstance(raw_sample_interval, (bool, np.bool_)):
            raise ValueError("sample_interval_s 必须是有限正秒数")
        interval = float(raw_sample_interval)
        if not np.isfinite(interval) or interval <= 0.0:
            raise ValueError("sample_interval_s 必须是有限正秒数")
        cfg["sample_interval_s"] = interval
    cfg["num_samples"] = int(num_samples)
    panel, panel_derived = _ensure_bs_panel(cfg)
    ue_panel, ue_panel_derived = _ensure_ue_panel(cfg)

    # 真实阵列模型：64T/256T 面板分别自动切到 1 驱 3 / 1 驱 6，二者统一
    # pol_h_v + top_to_bottom。显式 legacy_64 只用于历史兼容或对照。
    hw.apply_array_defaults(cfg)
    array_applied = hw.strip_markers(cfg)
    array_block = hw.array_summary(cfg, array_applied)

    if collect_ssb is not None:
        meas = dict(cfg.get("measurements") or {})
        meas["ssb_rsrp"] = bool(collect_ssb)
        cfg["measurements"] = meas
    ssb_on = bool((cfg.get("measurements") or {}).get("ssb_rsrp", True))

    dataset_id = "ds_" + uuid.uuid4().hex[:8]
    out_dir = dataset_dir(dataset_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    lo, hi = (float(snr_range_dB[0]), float(snr_range_dB[1])) if snr_range_dB else (-np.inf, np.inf)
    filtering = np.isfinite(lo) or np.isfinite(hi)

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
    requested_workers = _requested_worker_count(workers, num_samples, cfg)
    n_workers = _resolve_workers(workers, num_samples, cfg)
    worker_cap_reason = None
    if n_workers < requested_workers:
        worker_cap_reason = (
            f"ChannelHub 按 num_ues={n_ues} 成批构造样本；为避免每个小分块重复"
            f"整批计算，workers 从 {requested_workers} 收口到 {n_workers}"
        )

    _dbg(f"进入迭代 ask={ask} n_ues={n_ues} workers={n_workers} source={source_name}")

    parallel_fallback: str | None = None
    dropped_fields: list[str] = []
    if n_workers > 1:
        parallel_fallback = _parallel_exactness_blocker(
            source_name, cfg_run, filtering=filtering
        )
        if parallel_fallback is not None:
            n_workers = 1
            _dbg(f"并行语义门未通过，改用串行：{parallel_fallback}")
        else:
            try:
                (payload, first_meta, accepted, attempted, rejected, observed_sinr,
                 dropped_fields) = _run_parallel(
                    source_name, cfg_run, num_samples=num_samples, n_workers=n_workers,
                    lo=lo, hi=hi, filtering=filtering, base_seed=int(cfg.get("seed", 0) or 0),
                    n_ues=n_ues, ask_factor=1,
                    progress=progress,
                )
            except Exception as exc:  # noqa: BLE001
                # Windows spawn 需要可导入的 __main__；内存/权限也可能失败。
                # 回退必须写进摘要，不能让用户误以为并行已生效。
                parallel_fallback = f"{type(exc).__name__}: {exc}"
                _dbg(f"并行失败，降级串行：{parallel_fallback}")
                n_workers = 1

    if n_workers <= 1:
        payload, first_meta, st = _collect(
            source_name, cfg_run, want=num_samples, lo=lo, hi=hi,
            filtering=filtering, progress=progress,
        )
        accepted = st["accepted"]
        attempted = st["attempted"]
        rejected = st["rejected"]
        observed_sinr = st["observed_sinr"]

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

    # 干扰是否真的进了 SINR。多小区下若 SIR 仍是无干扰哨兵 49.9，或 SINR 与
    # SNR 逐点相同，说明来源没有交付有效邻区功率；不再把根因绑定到旧 DFT 码本。
    sir_arr = payload.get("scalar__sir_dB")
    snr_arr = payload.get("scalar__snr_dB")
    sinr_is_snr = (
        snr_arr is not None and snr_arr.size and np.allclose(sinr_arr, snr_arr, atol=1e-6)
    )
    sir_sentinel = bool(sir_arr is not None and sir_arr.size and np.allclose(sir_arr, 49.9))
    interference_modeled = bool(cells_cfg > 1 and not sinr_is_snr and not sir_sentinel)
    interference_note = None
    if cells_cfg > 1 and not interference_modeled:
        interference_note = (
            "多小区配置但小区间干扰未进入 SINR —— 报出的 sinr_dB 等于纯热噪声 snr_dB。"
            "干扰相关的结论不成立。"
        )

    rs_abstraction_arr = payload.get("meta__rs_opportunity_abstraction_used")
    rs_opportunity_block = None
    if rs_abstraction_arr is not None and rs_abstraction_arr.size:
        _rs_raw = np.asarray(rs_abstraction_arr, dtype=float)
        _rs_known = np.isfinite(_rs_raw)
        if np.any(_rs_known):
            _rs_used = _rs_raw[_rs_known] > 0.5
            rs_opportunity_block = {
                "abstract_observation_count": int(np.sum(_rs_used)),
                "sample_count": int(_rs_used.size),
                "unknown_sample_count": int(_rs_raw.size - np.sum(_rs_known)),
                "abstract_observation_fraction": round(float(np.mean(_rs_used)), 3),
                "slot_accurate": bool(not np.any(_rs_used) and np.all(_rs_known)),
                "note": (
                    "false means one or more samples synthesized an RS observation outside the "
                    "indexed-slot TDD/periodicity opportunity; PHY estimation remains usable, "
                    "but the dataset is not a slot-accurate RS scheduler trace"
                ),
            }

    effective_model = first_meta.get("effective_channel_model")
    effective_model_counts: dict[str, int] = {}
    effective_model_arr = payload.get("metastr__effective_channel_model")
    if effective_model_arr is not None and effective_model_arr.size:
        _models, _counts = np.unique(
            np.asarray(effective_model_arr, dtype=str), return_counts=True
        )
        effective_model_counts = {
            str(model): int(count) for model, count in zip(_models, _counts, strict=True)
            if str(model) not in {"", "None"}
        }

    # IoT（噪声抬升）。主公式由同口径几何 SIR 与 SINR 推出。当前 first-party
    # SNR/SINR 也共享预波束每-RB参考，因此 snr-sinr 是等价的一致性旁证；主公式
    # 仍不依赖外部/旧数据源是否遵守该 SNR 契约。
    iot_block: dict[str, Any] | None = None
    if interference_modeled and sir_arr is not None and sir_arr.size:
        from . import interference as _intf  # noqa: PLC0415

        st = _intf.iot_stats(sinr_arr, sir_arr)
        iot_block = {"dl": st.as_dict()}
        ul_geo = payload.get("scalar__ul_sir_geo_dB")
        ul_sinr = payload.get("scalar__ul_sinr_dB")
        if ul_geo is not None and ul_sinr is not None and np.isfinite(ul_geo).any():
            iot_block["ul"] = _intf.iot_stats(ul_sinr, ul_geo).as_dict()
        else:
            _ul_why = _intf.last_install_failure()
            if _ul_why:
                iot_block["ul_missing_reason"] = _ul_why

    # 预注册口径随数据一起存档。**必须在生成时绑定，事后补绑没有意义**——
    # 预注册的全部价值就在于"看数据之前写下的"，事后写的只是记录。
    prereg_block = None
    if validated_prereg is not None:
        pr = validated_prereg
        prereg_block = {
            "prereg_id": pr.prereg_id,
            "digest": pr.digest,
            "primary_metric": pr.primary_metric,
            "metric_unit": pr.metric_unit,
            "baseline": pr.baseline,
            "csi_basis": pr.csi_basis,
            "expected_effect": pr.expected_effect,
            "higher_is_better": pr.higher_is_better,
            "secondary_metrics": pr.secondary_metrics,
            "locked_before_generation": True,
        }

    channel_contract = dict(first_meta.get("channel_contract") or {})
    channel_contract.update({
        "sample_interval_s": float(cfg["sample_interval_s"]),
        "sample_interval_source": (
            "explicit_config" if raw_sample_interval is not None
            else "superran_fixed_default_5ms"
        ),
        "dataset_axes": ["sample", "time_or_slot", "RB", "BS_port", "UE_port"],
        "dataset_shape": [int(v) for v in shape],
        "dataset_dtype": str(payload["h_true"].dtype),
        "h_true_h_est_same_shape": bool(payload["h_true"].shape == payload["h_est"].shape),
        "h_est_missing_policy": "hard_error; never copy h_true implicitly",
        "available_per_cell_arrays": sorted(
            k.removeprefix("metavec__") for k in payload if k.startswith("metavec__")
        ),
    })

    summary = {
        "dataset_id": dataset_id,
        "draft_id": draft_id,
        "prereg": prereg_block,
        "source": source_name,
        "num_samples": int(accepted),
        "requested": int(num_samples),
        "cells_configured": cells_cfg,
        "cells_actual": int(cells_real) if cells_real else None,
        "topology_note": topology_note,
        "bs_panel": list(panel),
        "bs_panel_derived": bool(panel_derived),
        "ue_panel": list(ue_panel),
        "ue_panel_derived": bool(ue_panel_derived),
        "ue_panel_note": (
            "未显式配置时按端口数推导；4R 默认 2H x 1V x 2pol。"
            "这是可覆盖的工程假设，不是终端阵列实测值。"
        ),
        "antenna_model": array_block,
        # static internal_sim uses one seed and a global sample index, so worker
        # count changes only scheduling, never the event stream.  Unsupported
        # stateful/filtering paths have already been forced to serial above.
        "parallel": {
            "requested_workers": int(requested_workers),
            "workers": int(n_workers),
            "cap_reason": worker_cap_reason,
            "seed_layout": (
                f"seed={int(cfg.get('seed', 0) or 0)},"
                f"sample_index={int(cfg.get('sample_index_offset', 0) or 0)}.."
                f"{int(cfg.get('sample_index_offset', 0) or 0) + int(num_samples) - 1}"
            ),
            "note": (
                None if n_workers <= 1
                else "同 seed 按全局 sample_index 切块；与 workers=1 逐样本、逐位一致"
            ),
            "fallback_reason": parallel_fallback,
        },
        # 某块缺字段时整个数据集丢掉该字段（如 h_interferers）——必须留痕
        "parallel_dropped_fields": dropped_fields or None,
        "interference_modeled": interference_modeled if cells_cfg > 1 else None,
        "interference_note": interference_note,
        "rs_opportunity": rs_opportunity_block,
        "iot": iot_block,
        "collect_ssb": ssb_on,
        "shape": {
            "N": int(shape[0]), "T": int(shape[1]), "RB": int(shape[2]),
            "BS_ant": int(shape[3]), "UE_ant": int(shape[4]),
        },
        "channel_contract": channel_contract,
        "elapsed_s": round(elapsed, 2),
        "seconds_per_sample": round(elapsed / max(accepted, 1), 3),
        "size_mb": round((out_dir / "channels.npz").stat().st_size / 1e6, 1),
        "snr_filter": {
            "enabled": bool(filtering),
            "filtered_quantity": "snr_dB",
            "range_dB": [lo, hi] if filtering else None,
            "attempted": attempted,
            "rejected": rejected,
            "accept_rate": round(accepted / max(attempted, 1), 3),
        },
        "sinr_dB": _distribution(finite),
        # ``channel_model`` is retained as the historical configured-value
        # key.  Always expose the unambiguous name alongside the per-sample
        # effective profile, because LOS/NLOS consistency may switch D/E to a
        # compatible A/B/C profile (or vice versa).
        "configured_channel_model": first_meta.get("channel_model"),
        "channel_model": first_meta.get("channel_model"),
        "effective_channel_model": effective_model,
        "effective_channel_model_counts": effective_model_counts,
        "scenario": first_meta.get("scenario"),
        "is_cdl": str(effective_model or first_meta.get("channel_model", "")).upper().startswith("CDL"),
        "tau_rms_ns": first_meta.get("tau_rms_ns"),
        "config": cfg,
        "sample_meta": first_meta,
        "provenance": provenance.snapshot(source=source_name),
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

    # 仿真说明书：配置敲定之后把"这次到底在仿什么"画出来。
    # 用真实撒点画拓扑图，所以放在生成之后而不是之前。
    # **失败不影响数据集**——说明书是解释性产物，不是数据的一部分。
    try:
        from . import spec as _spec  # noqa: PLC0415

        _pos = payload.get("ue_position")
        _ue_xy = (
            [(float(r[0]), float(r[1])) for r in _pos[:400] if np.isfinite(r[0])]
            if _pos is not None and _pos.ndim == 2 and _pos.shape[1] >= 2 else None
        )
        summary["spec_sheet"] = _spec.write_spec(
            dict(cfg, source=source_name),
            num_samples=int(accepted),
            dataset_id=dataset_id,
            title=f"仿真说明书 · {dataset_id}",
            ue_xy=_ue_xy,
        )
    except Exception as exc:  # noqa: BLE001
        summary["spec_sheet"] = {"error": f"{type(exc).__name__}: {exc}"}

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
