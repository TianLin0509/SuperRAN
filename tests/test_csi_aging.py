"""CSI 反馈时延与老化。

分节：
1. 零时延恒等式——**这是本模块的地基**
2. 38.211 跳频序列
 3. CSI 陈旧时长与滞后的量化
4. 老化的物理方向（越老越差、跳频比不跳频差、MU 比 SU 掉得多）
5. 基站视角与真实视角必须分开
6. 配置校验与告警
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SUPERRAN_NO_BROWSER", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

from superran import csi_aging as ca  # noqa: E402
from superran import mumimo as mu  # noqa: E402
from superran import system as sy  # noqa: E402

_n_pass = 0
_n_fail = 0


def check(cond: bool, msg: str) -> None:
    global _n_pass, _n_fail  # noqa: PLW0603
    if cond:
        _n_pass += 1
        print(f"  PASS  {msg}")
    else:
        _n_fail += 1
        print(f"  FAIL  {msg}")


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _chan(rng: np.random.Generator, n_rb: int = 272, bs: int = 64, ue: int = 4):
    return ((rng.standard_normal((n_rb, bs, ue))
             + 1j * rng.standard_normal((n_rb, bs, ue))) / np.sqrt(2)).astype(np.complex64)


def _seq(rng: np.random.Generator, n_snap: int, rho: float,
         gain_db: float = 0.0, **kw):
    """时间相关的快照序列（一阶 AR，粗略模拟 Jakes 的单调段）。

    ``gain_db`` 给各用户不同的路损。**不给的话所有用户统计完全相同**，
    而噪声又由小区统一锚定，于是全员落在同一个 MCS 上——
    MU/SU 比值会退化成流数比 8/4 = 2.000 这个死数，任何老化都测不出来。
    这个坑真踩过：0 dB 到 20 dB 扫了一遍比值全是 2.000。
    """
    h = [_chan(rng, **kw)]
    for _ in range(n_snap - 1):
        w = _chan(rng, **kw)
        h.append((rho * h[-1] + np.sqrt(1 - rho ** 2) * w).astype(np.complex64))
    return (np.stack(h) * 10.0 ** (gain_db / 20.0)).astype(np.complex64)


# ---------------------------------------------------------------------------
section("1  零时延恒等式：老化模型必须是原物理的严格推广")
# ---------------------------------------------------------------------------
# 这一节是整个模块的地基。MMSE 后处理 SINR 在完美 CSI 下必须**逐位**退化成
# 特征值公式 σ_k²·P/rank/σ_n²，也就是 mumimo.su_rank_adaptation 用的那个。
# 不成立就说明老化是叠加上去的第二套物理，那样任何"老化损失"都不可解释。
rng = np.random.default_rng(7)
_worst = 0.0
for _ in range(5):
    h = _chan(rng)
    npow = 0.05
    old = mu.su_rank_adaptation(h, noise_power=npow, max_rank=4, rb_per_rbg=16)
    hr = mu.rbg_reduce(h, 16)
    new = ca.rank_adaptation_aged(hr, hr, noise_power=npow, max_rank=4)
    for a, b in zip(old.candidates, new.candidates, strict=True):
        _worst = max(_worst, abs(a["sinr_db"] - b["sinr_db"]))
        check(a["mcs"] == b["mcs"] and abs(a["se"] - b["se"]) < 1e-9,
              f"rank{a['rank']} MCS/SE 与 su_rank_adaptation 相同")
    check(old.rank == new.rank, f"选中的 rank 相同（{old.rank}）")
check(_worst < 1e-6, f"逐 rank SINR 最大偏差 {_worst:.2e} dB（应为 0）")

# 零时延时基站视角与真实视角也必须完全一致
h = _chan(rng)
hr = mu.rbg_reduce(h, 16)
r0 = ca.rank_adaptation_aged(hr, hr, noise_power=0.05, max_rank=4)
check(all(abs(a["sinr_db"] - b["sinr_db"]) < 1e-9
          for a, b in zip(r0.candidates, r0.gnb_candidates, strict=True)),
      "零时延时 gNB 视角与真实视角逐位相同")

# MMSE SINR 的解析自检：单流时应等于 |σ₁|²·P/σ_n²
hr1 = mu.rbg_reduce(_chan(rng), 16)
w = ca.svd_precoder(hr1)[:, :, :1]
got = ca.mmse_stream_sinr(hr1, w, power_per_stream=1.0, noise_power=0.05)
sv = np.stack([np.linalg.svd(hr1[f].conj().T, compute_uv=False) for f in range(hr1.shape[0])])
want = sv[:, :1] ** 2 / 0.05
check(np.allclose(got, want, rtol=1e-5), "rank1 MMSE SINR = sigma1^2 * P / sigma_n^2（解析式）")

# ---------------------------------------------------------------------------
section("2  38.211 跳频序列")
# ---------------------------------------------------------------------------
order, source = ca.hop_order(17, rb_per_rbg=16, hop_factor=17)
check(source.startswith("superran:"), f"跳频序列由 SuperRAN 自有合同提供（{source}）")
check(len(order) == 17, f"cycle = 17（C_SRS=63 / B_SRS=1 的 N1）实测 {len(order)}")
check(sorted(order.tolist()) == list(range(17)), "17 跳恰好覆盖 17 个 RBG，不重不漏")
expected_order = [0, 8, 16, 7, 15, 6, 14, 5, 13, 4, 12, 3, 11, 2, 10, 1, 9]
check(order.tolist() == expected_order,
      f"奇数 N1=17 按 floor(N1/2)=8 镜像跳频，实测 {order.tolist()}")

# ---------------------------------------------------------------------------
section("3  CSI 陈旧时长与滞后的量化")
# ---------------------------------------------------------------------------
cfg = ca.CsiConfig(srs_period_ms=10.0, hopping=True, processing_delay_ms=0.0)
check(abs(cfg.full_sweep_ms - 170.0) < 1e-9, f"10 ms × 17 跳 = 170 ms 扫完全带（{cfg.full_sweep_ms}）")
ages = ca.rbg_csi_staleness_ms(cfg, 17, 0.0)
check(len(set(np.round(ages, 6))) == 17, "各 RBG 的 CSI 陈旧时长互不相同")
check(abs(max(ages) - 160.0) < 1e-9,
      f"最陈旧 RBG 为 160 ms（16 × 10），实测 {max(ages)}")
check(abs(min(ages)) < 1e-9, f"最新 RBG 为 0 ms，实测 {min(ages)}")

# 年龄随时间轮转——不能有"某几个 RBG 永远最差"
a0 = ca.rbg_csi_staleness_ms(cfg, 17, 0.0)
a1 = ca.rbg_csi_staleness_ms(cfg, 17, 10.0)
check(int(np.argmax(a0)) != int(np.argmax(a1)), "最老的那个 RBG 随时间轮转，不是固定几个")

nohop = ca.CsiConfig(srs_period_ms=10.0, hopping=False, processing_delay_ms=0.0)
check(len(set(np.round(ca.rbg_csi_staleness_ms(nohop, 17, 3.0), 6))) == 1,
      "不跳频时全带 CSI 陈旧时长相同")
check(abs(nohop.full_sweep_ms - 10.0) < 1e-9, "不跳频时一个周期就扫完全带")

lags = ca.rbg_lag_snapshots(cfg, 17, snapshot_ms=5.0, snapshot_index=0)
_hop_order, _ = ca.hop_order(17)
_expected_lags = np.empty(17, dtype=int)
for _j, _rbg in enumerate(_hop_order):
    _expected_lags[int(_rbg)] = ((-_j) % 17) * 2
check(lags.tolist() == _expected_lags.tolist(),
      "10 ms 陈旧时长 / 5 ms 快照 = 2 个快照，逐 RBG 对得上")
check((lags >= 0).all(), "滞后非负")

# 离散快照必须选“不新于真实测量”的那一个：2 ms/5 ms 不能四舍五入成当前信道。
lag_2ms = ca.rbg_lag_snapshots(
    ca.CsiConfig(srs_period_ms=5.0, hopping=False, processing_delay_ms=2.0),
    1, snapshot_ms=5.0, snapshot_index=0)
lag_7ms = ca.rbg_lag_snapshots(
    ca.CsiConfig(srs_period_ms=5.0, hopping=False, processing_delay_ms=7.0),
    1, snapshot_ms=5.0, snapshot_index=0)
check(lag_2ms.tolist() == [1] and lag_7ms.tolist() == [2],
      "CSI 陈旧时长向上量化守因果：2 ms→1 快照，7 ms→2 快照")

# 处理时延必须改变“哪一次 SRS 已经可用”，不能只在选完本次机会后机械加时延。
# t=10 ms 恰好是新机会；processing=2 ms 时它尚未处理完，只能用 t=0 ms 的
# 上一次测量。到 t=15 ms 时新测量已可用，陈旧时长才降到 5 ms。
_boundary_cfg = ca.CsiConfig(
    srs_period_ms=10.0, hopping=False, processing_delay_ms=2.0)
_at_boundary = ca.rbg_csi_staleness_ms(_boundary_cfg, 1, 10.0)
_after_processing = ca.rbg_csi_staleness_ms(_boundary_cfg, 1, 15.0)
check(_at_boundary.tolist() == [10.0] and _after_processing.tolist() == [5.0],
      "SRS 周期边界守因果：未处理完用上次测量，处理完成后才切到本次测量")

_hop_boundary = ca.rbg_csi_staleness_ms(
    ca.CsiConfig(srs_period_ms=10.0, hopping=True, processing_delay_ms=2.0),
    17, 10.0)
check(int(np.argmin(_hop_boundary)) == 0 and float(np.min(_hop_boundary)) == 10.0,
      "跳频边界不提前切换 RBG phase（t=10 ms 时 phase-1 SRS 尚不可用）")

off = ca.CsiConfig(enabled=False)
check(ca.rbg_lag_snapshots(off, 17, snapshot_ms=5.0, snapshot_index=3).max() == 0,
      "enabled=False 时滞后恒为 0")

# stale_channel 越界要钳住，**不能回绕**——回绕等于拿未来的信道当过去用
seq = [np.full((3, 2, 2), float(t), dtype=np.complex64) for t in range(4)]
got = ca.stale_channel(seq, 1, np.array([0, 5, 1]))
check(got[0, 0, 0].real == 1 and got[1, 0, 0].real == 0 and got[2, 0, 0].real == 0,
      "滞后越界钳到最早快照，不回绕到未来")

# ---------------------------------------------------------------------------
section("4  老化的物理方向")
# ---------------------------------------------------------------------------
rng = np.random.default_rng(11)
_gains = np.linspace(0.0, -18.0, 6)          # 近点到远点 18 dB 的路损差
hs = [_seq(rng, 8, 0.9, gain_db=g) for g in _gains]
geo = [12.0] * 6


def _se(csi):
    tb = sy.build_link_tables(hs, geo, num_snapshots=8, csi=csi, snapshot_ms=5.0,
                              max_rank=4)
    return float(np.mean([t.best_se.mean() for t in tb]))


se_perfect = _se(None)
se_nohop = _se(ca.CsiConfig(srs_period_ms=10.0, hopping=False))
se_hop = _se(ca.CsiConfig(srs_period_ms=10.0, hopping=True))
se_slow = _se(ca.CsiConfig(srs_period_ms=40.0, hopping=True))
print(f"  完美 {se_perfect:.3f} / 不跳频 {se_nohop:.3f} / 17跳 {se_hop:.3f} / 40ms {se_slow:.3f}")
check(se_perfect > se_nohop, "有时延必然差于零时延")
check(se_nohop > se_hop, "跳频（CSI 陈旧时长跨度 0~160 ms）比不跳频差")
check(se_hop >= se_slow - 1e-6, "SRS 周期越长越差")
check(se_perfect > se_hop * 1.1, f"17 跳的损失是量级可见的（{(1 - se_hop / se_perfect) * 100:.0f}%）")

# 关掉老化必须与不传 csi 逐位相同
tb_a = sy.build_link_tables(hs, geo, num_snapshots=8, csi=None, snapshot_ms=5.0)
tb_b = sy.build_link_tables(hs, geo, num_snapshots=8,
                            csi=ca.CsiConfig(enabled=False), snapshot_ms=5.0)
check(all(np.array_equal(a.best_se, b.best_se) for a, b in zip(tb_a, tb_b, strict=True)),
      "enabled=False 与不传 csi 逐位相同")

# 评估信道和基站可见估计信道必须是两条独立数据流。真实数据同时提供 h_true/h_est；
# 若 h_est 没进这个入口，所谓 SRS/CSI 失配其实只是在延迟真信道上做预编码。
_h_true = _seq(np.random.default_rng(111), 2, 0.8, n_rb=8, bs=4, ue=2)
_h_est = _h_true.copy()
_h_est[:, :, [0, 1]] = _h_est[:, :, [1, 0]]
_tb_est = sy.build_link_tables(
    [_h_true], [10.0], h_for_precoding_users=[_h_est], rb_per_rbg=1)
check(np.array_equal(_tb_est[0].h_true_rbg, _h_true),
      "h_true 只进入真实接收评估缓存")
check(np.array_equal(_tb_est[0].h_prec_rbg, _h_est),
      "显式 h_est 进入预编码缓存，不被 h_true 偷换")
check(_tb_est[0].precoding_csi_source == "explicit_estimate",
      "链路表显式标记预编码 CSI 来自估计信道")
try:
    sy.build_link_tables([_h_true], [10.0], h_for_precoding_users=[])
except ValueError:
    _bad_prec_len_rejected = True
else:
    _bad_prec_len_rejected = False
check(_bad_prec_len_rejected, "h_true/h_est 样本数不一致时硬报错")

# 老化必须降低 MU 的绝对可用谱效；LMMSE 也会让 SU/MU 对老化的相对敏感度
# 随 realization 改变，因此不能把“MU/SU 比值一定下降”当成物理定律。
g_perfect = sy.measure_mu_gain(hs, geo, max_mu_users=4, max_snapshots=4, csi=None)
g_hop = sy.measure_mu_gain(hs, geo, max_mu_users=4, max_snapshots=4,
                           csi=ca.CsiConfig(srs_period_ms=10.0, hopping=True),
                           snapshot_ms=5.0)
g_nohop = sy.measure_mu_gain(hs, geo, max_mu_users=4, max_snapshots=4,
                             csi=ca.CsiConfig(srs_period_ms=10.0, hopping=False),
                             snapshot_ms=5.0)
print(f"  MU/SU 比值：完美 {g_perfect['ratio']:.3f} / 不跳频 {g_nohop['ratio']:.3f}"
      f" / 17跳 {g_hop['ratio']:.3f}")
check(g_hop["mu_se_median"] < g_perfect["mu_se_median"],
      f"老化后 MU 绝对谱效下降（{g_perfect['mu_se_median']:.3f} → "
      f"{g_hop['mu_se_median']:.3f}）")
check(g_hop["mu_se_median"] < g_nohop["mu_se_median"],
      "跳频比不跳频的 MU 绝对谱效更低")
check(g_aged_flag := (g_hop.get("csi_aging") is True),
      "MU 增益结果里标注了用的是陈旧 CSI")

# 信道变化慢时老化的代价必须显著变小——否则说明损失来自别处而不是时变
hs_slow = [_seq(np.random.default_rng(11), 8, 0.99, gain_db=g) for g in _gains]
g_slow = sy.measure_mu_gain(hs_slow, geo, max_mu_users=4, max_snapshots=4,
                            csi=ca.CsiConfig(srs_period_ms=10.0, hopping=True),
                            snapshot_ms=5.0)
g_slow0 = sy.measure_mu_gain(hs_slow, geo, max_mu_users=4, max_snapshots=4, csi=None)
loss_fast = 1 - g_hop["mu_se_median"] / g_perfect["mu_se_median"]
loss_slow = 1 - g_slow["mu_se_median"] / g_slow0["mu_se_median"]
print(f"  老化损失：快变信道 {loss_fast * 100:.0f}%，慢变信道 {loss_slow * 100:.0f}%")
check(loss_slow < loss_fast / 2,
      "慢变信道的老化损失显著小于快变（证明损失确实来自时变而非别处）")

# ---------------------------------------------------------------------------
section("5  基站视角与真实视角必须分开")
# ---------------------------------------------------------------------------
tb = sy.build_link_tables(hs, geo, num_snapshots=8, snapshot_ms=5.0,
                          csi=ca.CsiConfig(srs_period_ms=10.0, hopping=True))
se_true = float(np.mean([t.best_se.mean() for t in tb]))
se_gnb = float(np.mean([t.best_se_gnb.mean() for t in tb]))
print(f"  基站以为 {se_gnb:.3f}，实际 {se_true:.3f}")
check(se_gnb > se_true, "基站高估自己（它只看得到陈旧信道上的表现）")

tb0 = sy.build_link_tables(hs, geo, num_snapshots=8, snapshot_ms=5.0, csi=None)
check(all(np.allclose(t.best_se, t.best_se_gnb) for t in tb0),
      "零时延时两个视角逐位相同")

# 发送侧 SINR = CQI 门限 + BF Gain，必须是有限值
allfin = all(np.isfinite(t.sinr_tx_db).all() for t in tb)
check(allfin, "发送侧 SINR 全部有限（CQI=0 时退回实测 PMI SINR，不是 -inf）")
check(all(t.bf_gain_db is not None and (t.bf_gain_db > 0).mean() > 0.9 for t in tb),
      "BF Gain 绝大多数为正（SVD 本就该赢过 Type I 码本）")
check(all(t.cqi_index is not None and ((t.cqi_index >= 0) & (t.cqi_index <= 15)).all()
          for t in tb), "CQI index 落在 0..15")

# ---------------------------------------------------------------------------
section("6  配置校验与告警")
# ---------------------------------------------------------------------------
for bad in (7.0, 0.0, 100.0):
    try:
        ca.CsiConfig(srs_period_ms=bad)
        check(False, f"srs_period_ms={bad} 应当被拒")
    except ValueError:
        check(True, f"srs_period_ms={bad} 被拒（只允许 5/10/20/40）")

for _kwargs, _label in (
    ({"processing_delay_ms": float("nan")}, "NaN processing delay"),
    ({"processing_delay_ms": float("inf")}, "Inf processing delay"),
    ({"hop_factor": 1.5}, "非整数 hop factor"),
    ({"hop_factor": True}, "bool hop factor"),
):
    try:
        ca.CsiConfig(**_kwargs)
        check(False, f"{_label} 应当被拒")
    except ValueError:
        check(True, f"{_label} 被拒，不把非法配置带进 CSI 时延链")

for _call, _label in (
    (lambda: ca.hop_order(0), "零 RBG"),
    (lambda: ca.hop_order(8, rb_per_rbg=16, hop_factor=8),
     "非 100 MHz / 17-hop profile"),
    (lambda: ca.rbg_lag_snapshots(ca.CsiConfig(), 17, snapshot_ms=0.0,
                                  snapshot_index=0), "零 snapshot interval"),
    (lambda: ca.rbg_lag_snapshots(ca.CsiConfig(), 17, snapshot_ms=5.0,
                                  snapshot_index=-1), "负 snapshot index"),
):
    try:
        _call()
        check(False, f"{_label} 应当被拒")
    except ValueError:
        check(True, f"{_label} 被拒，不进入 fallback/量化路径")

summ = ca.aging_summary(ca.CsiConfig(srs_period_ms=10.0, hopping=True),
                        num_rbg=17, snapshot_ms=5.0, speed_kmh=30.0)
check(summ["hop_order_source"].startswith("superran:"), "摘要里标注了跳频序列来源")
check(summ["coherence_time_ms"] is not None and summ["coherence_time_ms"] < 10,
      f"30 km/h 相干时间 {summ['coherence_time_ms']} ms（应当 < 10）")
check(len(summ["warnings"]) > 0, "平均 CSI 陈旧时长远大于相干时间时给出告警")
check("mean_csi_staleness_ms" in summ and "mean_age_ms" not in summ,
      "结果字段使用 CSI 陈旧时长，不再把 SRS 周期误称为 SRS 年龄")

# 5 ms 是 trace 快照间隔，不是 PMI 固定周期。默认 20 ms 时 PMI/CQI 每 4 个
# 快照更新一次；显式 5 ms 才会逐快照更新。
check(np.array_equal(tb[0].csi_report_source_snapshot,
                     np.array([0, 0, 0, 0, 4, 4, 4, 4])),
      "默认 20 ms CSI report 在 5 ms trace 上持有 4 个快照")
check(np.all(tb[0].cqi_index_per_snapshot[:4]
             == tb[0].cqi_index_per_snapshot[0]),
      "CQI 在两次 report 之间真正保持，不只是改输出标签")
tb_fast_report = sy.build_link_tables(
    hs, geo, num_snapshots=8, snapshot_ms=5.0,
    csi=ca.CsiConfig(srs_period_ms=10.0, hopping=True,
                     csi_report_period_ms=5.0))
check(np.array_equal(tb_fast_report[0].csi_report_source_snapshot, np.arange(8)),
      "显式 5 ms CSI report 才逐快照更新 PMI/CQI")
check(ca.CsiConfig().as_dict()["csi_report_period_ms"] == 20.0,
      "PMI/CQI 工程默认 20 ms，并与 SRS 周期、trace 间隔分开")
_csi_dict = ca.CsiConfig().as_dict()
check(_csi_dict["csi_report_feedback_latency_ms"] == 0.0
      and "expanding mean" in _csi_dict["cqi_filter"],
      "CSI report 的零额外反馈时延与因果 expanding-mean 近似显式上报")

# 周期 trace 历史只有显式开启才可用；它代表预启动前上一轮 trace，冷启动仍钳零。
_trace = [np.full((1, 1, 1), x, dtype=float) for x in (10, 20, 30)]
_cold = ca.stale_channel(_trace, 0, np.array([1]), periodic_history=False)
_steady = ca.stale_channel(_trace, 0, np.array([1]), periodic_history=True)
check(float(_cold[0, 0, 0]) == 10.0 and float(_steady[0, 0, 0]) == 30.0,
      "冷启动钳最早快照；预启动周期重放显式使用上一轮因果历史")

# 5 ms 周期 + 不跳频 + 5 ms 快照 → 滞后全部量化成 0，模型失效必须警告
degenerate = ca.aging_summary(
    ca.CsiConfig(srs_period_ms=5.0, hopping=False, processing_delay_ms=0.0),
    num_rbg=17, snapshot_ms=5.0, speed_kmh=3.0)
check(any("量化成 0" in w for w in degenerate["warnings"]),
      "滞后被量化成 0 个快照时明确告警（模型此时几乎不起作用）")

# Jakes 相关：3 km/h 的相干时间必须远长于 30 km/h
check(ca.coherence_time_ms(3.0) > 5 * ca.coherence_time_ms(30.0),
      "3 km/h 相干时间远长于 30 km/h（相干时间 ∝ 1/v）")
check(abs(ca.jakes_correlation(0.0, 30.0) - 1.0) < 1e-12, "零滞后相关系数为 1")

# ---------------------------------------------------------------------------
section("7  OLLA 步长的稳态反解")
# ---------------------------------------------------------------------------
# 稳态条件：(1−p)·s_up = p·s_down ⟹ s_down = s_up·(1−p)/p
# **只取决于两个步长的比**，绝对值只影响收敛速度——这正是 olla_speedup 的依据。
for _p, _want in ((0.10, 0.09), (0.20, 0.04), (0.05, 0.19), (0.50, 0.01)):
    _got = sy.olla_step_down_for(_p, 0.01)
    check(abs(_got - _want) < 1e-9,
          f"目标 IBLER {_p:.0%} 时 s_down = {_want}（实得 {_got:.4f}）")
# 反过来代回去必须自洽
for _sd in (0.04, 0.09, 0.19):
    _p = 0.01 / (0.01 + _sd)
    check(abs(sy.olla_step_down_for(_p, 0.01) - _sd) < 1e-9, f"s_down={_sd} 往返自洽")
_auto_olla = sy.SchedulerConfig()
check(_auto_olla.as_dict()["olla_target_bler"] is None,
      "用户不填 down 步长时保持自动态，不在 target 未知时偷填 10%")
_resolved_olla = _auto_olla.resolved_for_target(0.1)
check(abs(_resolved_olla.as_dict()["olla_target_bler"] - 0.1) < 1e-9,
      "target BLER=10% 时自动反解 +0.01/-0.09")
check(abs(_resolved_olla.as_dict()["mu_olla_target_bler"] - 0.1) < 1e-9,
      "MU 独立 OLLA 同样默认按 target BLER 反解")
_explicit_olla = sy.SchedulerConfig(
    olla_step_down_db=0.2, mu_olla_step_down_db=0.3
).resolved_for_target(0.1)
check(_explicit_olla.olla_step_down_db == 0.2
      and _explicit_olla.mu_olla_step_down_db == 0.3,
      "用户显式填的 SU/MU OLLA down 步长不被 target BLER 覆盖")
# 等比放大不改变稳态——这是 olla_speedup 的全部依据
_a = sy.SchedulerConfig(olla_speedup=1.0).resolved_for_target(0.1).as_dict()["olla_target_bler"]
_b = sy.SchedulerConfig(olla_speedup=25.0).resolved_for_target(0.1).as_dict()["olla_target_bler"]
check(_a == _b, f"等比放大 25 倍后稳态 IBLER 不变（{_a} vs {_b}）")
check(abs(sy.SchedulerConfig(olla_speedup=25.0).resolved_for_target(0.1).step_down
          - 0.09 * 25) < 1e-9,
      "放大系数确实作用在生效步长上")
for _bad in (0.0, 1.0, -0.1):
    try:
        sy.olla_step_down_for(_bad)
        check(False, f"target_bler={_bad} 应当被拒")
    except ValueError:
        check(True, f"target_bler={_bad} 被拒")

# ---------------------------------------------------------------------------
section("8  Type I 码本当发射权（和 SVD 对比）")
# ---------------------------------------------------------------------------
_csi = ca.CsiConfig(srs_period_ms=10.0, hopping=True)
tb_svd = sy.build_link_tables(hs, geo, num_snapshots=8, snapshot_ms=5.0,
                              csi=_csi, precoder="svd")
tb_pmi = sy.build_link_tables(hs, geo, num_snapshots=8, snapshot_ms=5.0,
                              csi=_csi, precoder="type1")
_se_svd = float(np.mean([t.best_se.mean() for t in tb_svd]))
_se_pmi = float(np.mean([t.best_se.mean() for t in tb_pmi]))
print(f"  老化下：SVD 权 {_se_svd:.3f}，Type I 权 {_se_pmi:.3f}")
check(_se_svd > 0 and _se_pmi > 0, "两种发射权都能出结果")
# **BF Gain 是发射权相对 PMI 参照权的增益**，type1 时两者同一个权，必须恒为 0
check(all(np.allclose(t.bf_gain_db, 0.0, atol=1e-9) for t in tb_pmi),
      "precoder='type1' 时 BF Gain 恒为 0（发射权就是参照权）")
check(any((t.bf_gain_db > 0.5).any() for t in tb_svd),
      "precoder='svd' 时 BF Gain 明显为正")
try:
    sy.build_link_tables(hs, geo, num_snapshots=8, precoder="dft")
    check(False, "未知的 precoder 应当被拒")
except ValueError:
    check(True, "未知的 precoder 被拒（不静默退回默认）")
# w_override 的形状必须校验，错了要早报而不是广播成奇怪的东西
_hr = mu.rbg_reduce(_chan(rng), 16)
try:
    ca.rank_adaptation_aged(_hr, _hr, noise_power=0.05,
                            w_override=np.zeros((3, 3, 2), dtype=complex))
    check(False, "w_override 形状不匹配应当被拒")
except ValueError:
    check(True, "w_override 形状不匹配被拒")

# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print(f"CSI 老化：{_n_pass} 通过，{_n_fail} 失败")
print("=" * 70)
if _n_fail:
    sys.exit(1)
print("CSI 反馈时延与老化全部通过。")
