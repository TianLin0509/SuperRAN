"""物理层工具箱：3GPP 标准序列、帧结构、波束与估计基线。

这里既有 SuperRAN 自己冻结的产品合同，也有仍经数据源适配器调用的通用
链路级工具。暴露出来主要有两个用途：

* **当基线** —— LS/MMSE 估计、SVD 预编码、DFT 码本、SSB 波束扫描，
  都是"你的方法要跟什么比"的现成答案。
* **做导频层课题** —— SRS 跳频、序列相关性、导频污染这类研究，
  需要真实的序列而不是随机数。

100 MHz / 272 RB / 17-hop SRS 资源映射由 SuperRAN 本地版本化；其他
参考序列和通用物理工具目前仍经 ChannelHub 数据源适配器调用。两类来源会在
返回值或文档中显式标注，不能把后者说成整仓零依赖。
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .channelhub import _ensure_path

# ---------------------------------------------------------------------------
# NR 帧结构与资源
# ---------------------------------------------------------------------------


def nr_rb_count(
    bandwidth_hz: float,
    scs_hz: float,
    *,
    frequency_range: str = "FR1",
) -> int:
    """按 38.101 的信道带宽配置表查 RB 数。

    不是简单地用 ``带宽/(12·SCS)`` —— 标准表格考虑了保护带，
    100 MHz @30 kHz 是 273 个 RB 而非 277。
    """
    _ensure_path()
    from msg_embedding.phy_sim.nr_rb_table import nr_rb_lookup  # noqa: PLC0415

    return int(
        nr_rb_lookup(
            float(bandwidth_hz),
            float(scs_hz),
            frequency_range=frequency_range,
        )
    )


def nr_valid_configs(*, frequency_range: str = "FR1") -> dict[str, Any]:
    """列出 NR 支持的子载波间隔与各自的合法带宽。"""
    _ensure_path()
    from msg_embedding.phy_sim.nr_rb_table import (  # noqa: PLC0415
        nr_valid_bandwidths,
        nr_valid_scs,
    )

    scs_list = [int(x) for x in nr_valid_scs(frequency_range=frequency_range)]
    return {
        "frequency_range": str(frequency_range).upper(),
        "subcarrier_spacing_khz": scs_list,
        "bandwidths_mhz": {
            int(s): [
                int(b)
                for b in nr_valid_bandwidths(int(s), frequency_range=frequency_range)
            ]
            for s in scs_list
        },
        "note": "带宽单位 MHz，子载波间隔单位 kHz。RB 数用 nr_rb_count() 查。",
    }


def tdd_pattern_info(name: str = "DDDSU") -> dict[str, Any]:
    """TDD 帧结构详情，含特殊时隙的符号级切分。

    普通配比只说"哪些时隙是下行/上行"，但特殊时隙 S 内部还要分
    下行符号、保护间隔、上行符号——做时隙级仿真时这个切分很关键。
    """
    _ensure_path()
    from msg_embedding.phy_sim.tdd_config import get_tdd_pattern  # noqa: PLC0415

    p = get_tdd_pattern(name)
    out: dict[str, Any] = {"name": name}
    for attr in ("pattern", "period_slots", "num_dl", "num_ul", "num_special",
                 "periodicity_ms", "slots"):
        if hasattr(p, attr):
            v = getattr(p, attr)
            out[attr] = list(v) if isinstance(v, (list, tuple)) else v
    sp = getattr(p, "special", None) or getattr(p, "special_slot", None)
    if sp is not None:
        out["special_slot"] = {
            k: getattr(sp, k) for k in ("dl_symbols", "gp_symbols", "ul_symbols")
            if hasattr(sp, k)
        }
    return out


def list_tdd_patterns() -> list[str]:
    _ensure_path()
    from msg_embedding.phy_sim.tdd_config import list_tdd_patterns as _l  # noqa: PLC0415

    return [str(x) for x in _l()]


# ---------------------------------------------------------------------------
# 参考信号序列（38.211）
# ---------------------------------------------------------------------------


def srs_config(
    num_rb: int,
    *,
    c_srs: int | None = None,
    b_srs: int = 0,
    b_hop: int = 0,
    comb: int = 2,
    n_rrc: int = 0,
    periodicity: int = 10,
    offset: int = 0,
    n_ports: int = 1,
) -> dict[str, Any]:
    """SRS 资源配置与跳频参数（38.211 §6.4.1.4）。

    跳频当前只支持公司 100 MHz profile：272 RB、C_SRS=63、B_SRS=1、
    b_hop=0、n_RRC=0，每次 16 RB、17 跳覆盖全带。其他跳频组合直接拒绝，
    避免看似成功却套用了未验证的资源映射。非跳频配置仍保留为链路级工具。
    返回里的 ``hopping_cycle_length`` 是跳完整个带宽所需的 SRS 发送次数——
    信道老化分析要用它换算总的获取时延：
    ``获取时延 = 跳频周期 × SRS周期 × 时隙长度``。

    注意跳频只在 ``b_hop < b_srs`` 时启用；``b_hop ≥ b_srs`` 表示不跳频。
    """
    _hopping = int(b_hop) < int(b_srs)
    if _hopping:
        from . import hardware as hw  # noqa: PLC0415

        for name, value in (
            ("num_rb", num_rb), ("c_srs", c_srs), ("b_srs", b_srs),
            ("b_hop", b_hop), ("n_rrc", n_rrc), ("comb", comb),
            ("periodicity", periodicity), ("offset", offset),
            ("n_ports", n_ports),
        ):
            if value is None and name == "c_srs":
                continue
            if isinstance(value, (bool, np.bool_)) or not isinstance(
                value, (int, np.integer)
            ):
                raise ValueError(f"{name} 必须是整数")
        if int(comb) not in (2, 4):
            raise ValueError("comb 只支持 2 / 4")
        if int(periodicity) < 1 or int(offset) < 0 or int(n_ports) < 1:
            raise ValueError("periodicity/n_ports 必须为正整数，offset 必须为非负整数")
        actual = {
            "num_rb": int(num_rb),
            "c_srs": hw.COMPANY_SRS_C_SRS if c_srs is None else int(c_srs),
            "b_srs": int(b_srs),
            "b_hop": int(b_hop),
            "n_rrc": int(n_rrc),
        }
        expected = {
            "num_rb": hw.COMPANY_NUM_RB,
            "c_srs": hw.COMPANY_SRS_C_SRS,
            "b_srs": hw.COMPANY_SRS_B_SRS,
            "b_hop": hw.COMPANY_SRS_B_HOP,
            "n_rrc": hw.COMPANY_SRS_N_RRC,
        }
        if actual != expected:
            raise ValueError(
                "SRS 跳频当前只支持 SuperRAN 100 MHz / 272 RB / "
                "C_SRS=63 / B_SRS=1 / b_hop=0 / n_RRC=0 的 17-hop profile；"
                f"实得 {actual}"
            )
        order = list(hw.COMPANY_SRS_17_HOP_ORDER_RBG)
        return {
            "profile_id": hw.SUPERRAN_SRS_HOPPING_PROFILE_ID,
            "source": "SuperRAN-owned fixed 38.211 profile",
            "num_rb": hw.COMPANY_NUM_RB,
            "c_srs": hw.COMPANY_SRS_C_SRS,
            "b_srs": hw.COMPANY_SRS_B_SRS,
            "b_hop": hw.COMPANY_SRS_B_HOP,
            "n_rrc": hw.COMPANY_SRS_N_RRC,
            "comb": int(comb),
            "periodicity_slots": int(periodicity),
            "offset_slots": int(offset),
            "n_ports": int(n_ports),
            "hopping_enabled": True,
            "hopping_cycle_length": len(order),
            "hop_order_rbg": order,
            "rb_per_hop": hw.COMPANY_RB_PER_RBG,
            "coverage_ratio": round(
                hw.COMPANY_RB_PER_RBG / hw.COMPANY_NUM_RB, 3
            ),
            "first_hop_rb_range": [0, hw.COMPANY_RB_PER_RBG - 1],
            "note": (
                "固定 17-hop 轮转；一次测 16 RB，17 次 SRS 机会覆盖 272 RB。"
                "获取时延需计入 hopping_cycle_length × periodicity × 时隙长度。"
            ),
        }

    _ensure_path()
    from msg_embedding.ref_signals.srs import (  # noqa: PLC0415
        SRSResourceConfig,
        auto_select_c_srs,
        srs_hopping_cycle_length,
        srs_rb_indices,
    )

    c = (
        int(auto_select_c_srs(
            int(num_rb),
            B_SRS=int(b_srs),
            target_rb=16 if _hopping else None,
        ))
        if c_srs is None else int(c_srs)
    )
    cfg = SRSResourceConfig(
        C_SRS=c,
        B_SRS=int(b_srs),
        K_TC=int(comb),
        n_RRC=int(n_rrc),
        b_hop=int(b_hop),
        n_SRS_ID=0,
        T_SRS=int(periodicity),
        T_offset=int(offset),
        N_ap=int(n_ports),
    )
    cycle = int(srs_hopping_cycle_length(cfg))
    idx0 = np.asarray(srs_rb_indices(cfg, 0, 0, int(num_rb)))  # (slot, symbol, total_rb)
    return {
        "c_srs": c,
        "b_srs": int(b_srs),
        "b_hop": int(b_hop),
        "comb": int(comb),
        "periodicity_slots": int(periodicity),
        "hopping_enabled": bool(cfg.hopping_enabled),
        "hopping_cycle_length": cycle,
        "rb_per_hop": int(idx0.size),
        "coverage_ratio": round(float(idx0.size) / max(int(num_rb), 1), 3),
        "first_hop_rb_range": [int(idx0.min()), int(idx0.max())] if idx0.size else None,
        "note": (
            "跳频周期 >1 时一次只测部分带宽，跳完才有完整的宽带估计。"
            "老化分析要把 hopping_cycle_length × periodicity × 时隙长度 计入获取时延。"
        ),
    }


def srs_sequence(
    *, length: int, u: int = 0, v: int = 0, cyclic_shift: float = 0.0
) -> np.ndarray:
    """SRS 基序列（Zadoff-Chu 或短序列），含循环移位。"""
    _ensure_path()
    from msg_embedding.ref_signals.srs import srs_base_sequence  # noqa: PLC0415

    seq = np.asarray(srs_base_sequence(int(u), int(v), int(length)))
    if cyclic_shift:
        n = np.arange(seq.size)
        seq = seq * np.exp(1j * float(cyclic_shift) * n)
    return seq.astype(np.complex64)


def zadoff_chu(root: int, length: int) -> np.ndarray:
    """Zadoff-Chu 序列。恒模、理想周期自相关，是 SRS/PRACH 的基础。"""
    _ensure_path()
    from msg_embedding.ref_signals.zc import zadoff_chu as _zc  # noqa: PLC0415

    return np.asarray(_zc(int(root), int(length))).astype(np.complex64)


def ssb_sequences(pci: int) -> dict[str, np.ndarray]:
    """SSB 的三种序列：PSS、SSS、PBCH-DMRS。小区搜索与同步用。"""
    _ensure_path()
    from msg_embedding.ref_signals.ssb import pbch_dmrs, pss, sss  # noqa: PLC0415

    n_id2 = int(pci) % 3
    n_id1 = int(pci) // 3
    return {
        "pss": np.asarray(pss(n_id2)),
        "sss": np.asarray(sss(int(pci))),
        "pbch_dmrs": np.asarray(pbch_dmrs(int(pci), 0)),
        "n_id_1": n_id1,
        "n_id_2": n_id2,
    }


def gold_sequence(c_init: int, length: int) -> np.ndarray:
    """38.211 的 Gold 伪随机序列。CSI-RS、DMRS、PDSCH 加扰都基于它。"""
    _ensure_path()
    from msg_embedding.ref_signals.gold import pseudo_random  # noqa: PLC0415

    return np.asarray(pseudo_random(int(c_init), int(length)))


def sequence_correlation(a: np.ndarray, b: np.ndarray | None = None) -> dict[str, Any]:
    """序列的自相关/互相关特性。导频设计与污染分析的基本工具。"""
    a = np.asarray(a).ravel()
    x = a if b is None else np.asarray(b).ravel()
    n = min(a.size, x.size)
    a, x = a[:n], x[:n]
    corr = np.array([abs(np.vdot(a, np.roll(x, k))) for k in range(n)]) / max(n, 1)
    peak = float(corr[0])
    side = corr[1:] if n > 1 else corr
    return {
        "length": int(n),
        "peak": round(peak, 6),
        "max_sidelobe": round(float(side.max()) if side.size else 0.0, 6),
        "peak_to_sidelobe_db": round(
            20 * np.log10(peak / max(float(side.max()) if side.size else 1e-12, 1e-12)), 2
        ),
        "is_autocorrelation": b is None,
    }


# ---------------------------------------------------------------------------
# 波束与预编码基线
# ---------------------------------------------------------------------------


def dft_codebook(
    n_h: int,
    n_v: int,
    n_p: int = 2,
    *,
    port_order: str | None = None,
    vertical_index_order: str | None = None,
) -> np.ndarray:
    """CSI-RS 的 DFT 波束码本 ``[n_beams, n_ports]``。

    与 ChannelHub 生成 CSI-RS 波束时用的是同一份实现，
    所以拿它当基线和仿真是自洽的。
    """
    _ensure_path()
    from msg_embedding.phy_sim.csirs_precoding import (  # noqa: PLC0415
        CSIRSBeamConfig,
        generate_dft_codebook,
    )

    codebook = np.asarray(
        generate_dft_codebook(CSIRSBeamConfig(n_h=n_h, n_v=n_v, n_p=n_p))
    )
    if port_order is None:
        return codebook
    from .hardware import type1_to_port_permutation

    resolved_vertical_order = vertical_index_order or (
        "bottom_to_top" if port_order == "h_v_pol" else "top_to_bottom"
    )
    perm = np.asarray(
        type1_to_port_permutation(
            n_h,
            n_v,
            n_p,
            port_order=port_order,
            vertical_index_order=resolved_vertical_order,
        ),
        dtype=np.intp,
    )
    out = np.empty_like(codebook)
    out[:, perm] = codebook
    return out


def select_beam(codebook: np.ndarray, h: np.ndarray) -> int:
    """在码本里选最优波束，返回索引。"""
    _ensure_path()
    from msg_embedding.phy_sim.csirs_precoding import select_csirs_beam  # noqa: PLC0415

    return int(select_csirs_beam(np.asarray(codebook), np.asarray(h)))


def project_interference(
    h_interferers: np.ndarray,
    h_serving_of_interferers: list[np.ndarray],
    *,
    bs_panel: tuple[int, int, int] | None = None,
    max_rank: int = 4,
) -> dict[str, Any]:
    """把干扰小区的信道投影到它自己的预编码子空间。

    没投影的"裸"干扰信道会高估干扰——真实网络里干扰小区也在做波束赋形，
    只有落进你方向的那部分才构成干扰。做干扰协调课题时这一步不能省。

    给 ``bs_panel`` 时用 DFT 码本（贴近真实网络的 Type I CSI 行为），
    否则用 SVD。
    """
    _ensure_path()
    from msg_embedding.phy_sim.precoding import project_interference_channels  # noqa: PLC0415

    proj, ranks = project_interference_channels(
        np.asarray(h_interferers),
        [np.asarray(x) for x in h_serving_of_interferers],
        max_rank=max_rank,
        bs_panel=bs_panel,
    )
    raw = float(np.mean(np.abs(np.asarray(h_interferers)) ** 2))
    got = float(np.mean(np.abs(proj) ** 2))
    return {
        "projected": proj,
        "ranks": list(ranks),
        "raw_power": raw,
        "projected_power": got,
        "reduction_db": round(10 * np.log10(max(got, 1e-30) / max(raw, 1e-30)), 2),
        "method": "dft_codebook" if bs_panel else "svd",
    }


# ---------------------------------------------------------------------------
# 信道估计基线
# ---------------------------------------------------------------------------


def estimate_channel(
    h_true: np.ndarray,
    *,
    method: str = "ls",
    snr_db: float = 20.0,
    pilot_spacing: int = 4,
    seed: int = 0,
    tau_rms_s: float | None = None,
    scs_hz: float = 30000.0,
) -> dict[str, Any]:
    """信道估计基线：在理想信道上加噪声、抽导频、再估回来。

    用于给自研估计器做对照。``method``：

    * ``ls``     最小二乘 + 线性插值。最简单的基线。
    * ``mmse``   维纳滤波，频域相关由 ``tau_rms_s`` 决定（知道时延扩展的
      理想化上界；实际系统需要先估计它）。
    * ``ideal``  直接返回真值，用于确认评估链路本身没问题。

    **导频间隔要和相干带宽匹配**：相干带宽约 ``1/(5·τ_rms)``，间隔过大时
    插值误差会主导，此时提高信噪比也没用——这一点在返回的 NMSE 上看得很清楚。
    """
    h = np.asarray(h_true)
    rng = np.random.default_rng(seed)
    sig = float(np.mean(np.abs(h) ** 2))
    n0 = sig / max(10 ** (snr_db / 10), 1e-30)

    noise = (rng.standard_normal(h.shape) + 1j * rng.standard_normal(h.shape)).astype(np.complex64)
    noise *= np.sqrt(n0 / 2)
    h_noisy = (h + noise).astype(np.complex64)

    if method == "ideal":
        h_hat = h.copy()
    else:
        rb = h.shape[1]
        pilots = np.arange(0, rb, max(int(pilot_spacing), 1))
        h_p = h_noisy[:, pilots]
        if method == "mmse":
            # 直接复用 ChannelHub 的 pilot→全 RB LMMSE，不在适配层另抄一套。
            # 它使用实际 pilot index，先验是指数 PDP 的频域协方差；因此“平均
            # MSE 不劣于 LS”只在先验匹配的统计意义下成立，不能对任意单条 CDL
            # realization 或错误 tau_rms 宣称逐样本支配。
            tau_s = float(tau_rms_s) if tau_rms_s else 300e-9
            df = 12.0 * float(scs_hz)  # 相邻 RB 的频率间隔
            _ensure_path()
            from msg_embedding.channel_est import (  # noqa: PLC0415
                lmmse_frequency_interpolate,
            )

            # Library convention is [pilot, ...]; local convention is [T, P, B, U].
            estimate = lmmse_frequency_interpolate(
                np.moveaxis(h_p, 1, 0),
                pilots,
                np.arange(rb),
                tau_s,
                df,
                sig / max(n0, 1e-30),
                dtype="complex64",
            )
            h_hat = np.moveaxis(estimate, 0, 1)
        else:
            # LS：导频点直接取观测值，其余频点线性插值（实部虚部分开插）
            grid = np.arange(rb)
            h_hat = np.empty_like(h, dtype=np.complex64)
            for t in range(h.shape[0]):
                for b in range(h.shape[2]):
                    for u in range(h.shape[3]):
                        col = h_p[t, :, b, u]
                        h_hat[t, :, b, u] = (
                            np.interp(grid, pilots, col.real)
                            + 1j * np.interp(grid, pilots, col.imag)
                        )
        h_hat = np.asarray(h_hat, dtype=np.complex64).reshape(h.shape)

    err = float(np.sum(np.abs(h_hat - h) ** 2))
    den = float(np.sum(np.abs(h) ** 2))
    return {
        "h_hat": h_hat,
        "method": method,
        "nmse_db": round(10 * np.log10(max(err / max(den, 1e-30), 1e-30)), 2),
        "snr_db": snr_db,
        "pilot_spacing": pilot_spacing,
        "n_pilots": int(np.ceil(h.shape[1] / max(pilot_spacing, 1))),
    }
