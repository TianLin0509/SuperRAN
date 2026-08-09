"""系统级仿真：话务、PF 调度、HARQ、体验速率口径、守恒对账。

直接运行：python tests/test_system.py
"""
from __future__ import annotations

import inspect as _inspect
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(errors="replace")

from superwireless import bler_curves as bc  # noqa: E402
from superwireless import experience as expm  # noqa: E402
from superwireless import mumimo as mu  # noqa: E402
from superwireless import system as sysm  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(t: str) -> None:
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def fake_tables(n_ue=8, n_snap=8, sinr_lo=0.0, sinr_hi=25.0, seed=0):
    """造一批链路表，SINR 从近点到远点铺开，带时间起伏。"""
    rng = np.random.default_rng(seed)
    geo = np.linspace(sinr_hi, sinr_lo, n_ue)
    hs = []
    for _ in range(n_ue):
        h = ((rng.standard_normal((n_snap, 24, 16, 4))
              + 1j * rng.standard_normal((n_snap, 24, 16, 4))) / np.sqrt(2))
        hs.append(h)
    return sysm.build_link_tables(hs, list(geo))


# ---------------------------------------------------------------------------
sect("1  第一相：把信道压成查表")

_T = fake_tables()
check(len(_T) == 8, "每个 UE 一张表")
check(_T[0].sinr_db.shape == (8, 4), f"表形状 [快照, rank]（实得 {_T[0].sinr_db.shape}）")
check(np.all(_T[0].best_rank >= 1) and np.all(_T[0].best_rank <= 4), "选中的 rank 在 1..4")
check(np.all(_T[0].best_se == _T[0].se[np.arange(8), _T[0].best_rank - 1]),
      "best_se 与 best_rank 一致")
print(f"  近点 UE0 几何 {_T[0].geo_sinr_db:.1f} dB → 平均 rank {_T[0].best_rank.mean():.2f}")
print(f"  远点 UE7 几何 {_T[-1].geo_sinr_db:.1f} dB → 平均 rank {_T[-1].best_rank.mean():.2f}")
check(_T[0].best_rank.mean() >= _T[-1].best_rank.mean(), "近点用户的秩不低于远点")
check(_T[0].best_se.mean() > _T[-1].best_se.mean(), "近点用户谱效更高")

# ---------------------------------------------------------------------------
sect("2  守恒：发出去的 + 还压着的 = 到达的")

# **这条对账抓到过真 bug。** HARQ 重传成功时漏了累加 served，
# 字节进了缓冲区却没进统计，差 4.5%——不做对账根本发现不了。
_r = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=3.0, seed=1),
                   traffic=sysm.TrafficConfig(model="ftp3", file_bytes=200_000,
                                              arrival_rate_hz=2.0))
_c = _r.cell
print(f"  到达 {_c['offered_mbps']:.1f} Mbps / 发出 {_c['cell_served_mbps']:.1f} Mbps"
      f" / 积压 {_c['backlog_bytes'] * 8 / 1e6:.1f} Mb")
check(_c["accounting_error_pct"] < 1.0,
      f"字节对得上账（误差 {_c['accounting_error_pct']}%）")
check(_c["cell_served_mbps"] <= _c["offered_mbps"] + 1e-6, "发出去的不可能多于到达的")

# ---------------------------------------------------------------------------
sect("3  体验速率的三种口径")

_res = {}
for _t in ("none", "tail", "head_tail"):
    _rr = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=5.0, seed=2),
                        traffic=sysm.TrafficConfig(model="ftp3", file_bytes=200_000,
                                                   arrival_rate_hz=2.0),
                        kpi=sysm.KpiConfig(trim=_t))
    _res[_t] = _rr.cell["ue_experienced_median_mbps"]
    print(f"  trim={_t:<10} 用户中位体验速率 {_res[_t]:8.2f} Mbps")
check(_res["none"] > 0 and _res["tail"] > 0, "三种口径都算得出数")
# 掐尾把"只发了一点点就清空缓冲"的那个 TTI 去掉，分子分母同减，
# 结果通常更低——但方向不是恒定的，所以只断言三者确实不同。
check(len({round(v, 3) for v in _res.values()}) >= 2,
      f"不同口径给出不同的数（实得 {_res}）——口径必须跟着结果走")
check(_rr.config["kpi"]["trim"] == "head_tail"
      and "掐头去尾" in _rr.config["kpi"]["trim_note"], "口径与它的解释一起返回")

# ---------------------------------------------------------------------------
sect("4  调度器：PF vs max-C/I vs 轮询")

_out = {}
for _alg in ("pf", "max_ci", "rr"):
    _rr = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=5.0, seed=3),
                        traffic=sysm.TrafficConfig(model="full_buffer"),
                        sched=sysm.SchedulerConfig(algorithm=_alg, pf_window_tti=100))
    _u = [x["served_mbps"] for x in _rr.users]
    _jain = sum(_u) ** 2 / (len(_u) * sum(x * x for x in _u)) if sum(_u) > 0 else 0
    _out[_alg] = (_rr.cell["cell_served_mbps"], _jain)
    print(f"  {_alg:<8} 小区吞吐 {_out[_alg][0]:8.1f} Mbps   Jain 公平度 {_jain:.3f}")

check(_out["max_ci"][0] >= _out["pf"][0] - 1e-6,
      "max-C/I 的小区吞吐不低于 PF（它只喂最好的用户）")
check(_out["pf"][1] > _out["max_ci"][1],
      f"PF 比 max-C/I 公平（{_out['pf'][1]:.3f} vs {_out['max_ci'][1]:.3f}）")
check(_out["rr"][1] > _out["max_ci"][1], "轮询也比 max-C/I 公平")

# full buffer 下"体验速率"没有意义——缓冲区永不空，没有 burst 边界
_fb = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=2.0),
                    traffic=sysm.TrafficConfig(model="full_buffer"))
check(_fb.cell["measured_bursts"] == 0, "full buffer 下没有 burst 可测，体验速率为空")

# ---------------------------------------------------------------------------
sect("5  负载与告警")

# 过载时必须主动说，不能给一个好看的体验速率就完事
_hi = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=5.0, seed=4),
                    traffic=sysm.TrafficConfig(model="ftp3", file_bytes=2_000_000,
                                               arrival_rate_hz=20.0))
print(f"  重载：占用率 {_hi.cell['occupancy']:.1%}，"
      f"积压 {_hi.cell['backlog_bytes'] * 8 / 1e6:.0f} Mb")
for _n in _hi.notes:
    print("    ! " + _n[:70])
check(bool(_hi.notes), "过载时给出告警而不是闷头报数")
check(any("积压" in n or "过载" in n for n in _hi.notes), "告警点明是积压/过载")

# 信道快照太少要拦：PF 拿不到多用户分集
_flat = sysm.build_link_tables(
    [np.ones((1, 8, 8, 2), dtype=complex) * (i + 1) for i in range(4)],
    [10.0] * 4)
_fr = sysm.simulate(_flat, sys_cfg=sysm.SystemConfig(duration_s=1.0),
                    traffic=sysm.TrafficConfig(model="ftp3", file_bytes=50_000,
                                               arrival_rate_hz=5.0))
check(any("快照" in n for n in _fr.notes), "信道快照不足时明确告警")

# ---------------------------------------------------------------------------
sect("6  用户级与小区级都要有")

_r6 = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=6.0, seed=6),
                    traffic=sysm.TrafficConfig(model="ftp3", file_bytes=200_000,
                                               arrival_rate_hz=2.0))
check(len(_r6.users) == len(_T), "每个用户都有一行")
_need = {"experienced_mbps", "avg_mcs", "avg_rank", "bler_first_tx",
         "residual_bler", "geo_sinr_db", "bursts", "sched_tti"}
check(_need <= set(_r6.users[0]), f"用户级字段齐全（缺 {_need - set(_r6.users[0])}）")
_cell_need = {"cell_experienced_mbps", "ue_experienced_median_mbps",
              "ue_experienced_p5_mbps", "avg_mcs", "avg_rank", "bler_first_tx",
              "occupancy", "cell_served_mbps"}
check(_cell_need <= set(_r6.cell), f"小区级字段齐全（缺 {_cell_need - set(_r6.cell)}）")

# 小区体验速率是各用户的平均，不是求和 —— 求和会超过物理峰值
_ue_exp = [x["experienced_mbps"] for x in _r6.users if x["bursts"] > 0]
check(abs(_r6.cell["cell_experienced_mbps"] - float(np.mean(_ue_exp))) < 1e-3,
      "小区体验速率 = 各用户体验速率的平均（不是求和）")

# 近点用户的体验速率应当高于远点
_near = [x for x in _r6.users if x["geo_sinr_db"] > 20 and x["bursts"] > 0]
_far = [x for x in _r6.users if x["geo_sinr_db"] < 5 and x["bursts"] > 0]
if _near and _far:
    print(f"  近点 {_near[0]['experienced_mbps']:.1f} Mbps（MCS {_near[0]['avg_mcs']:.1f}）"
          f" vs 远点 {_far[-1]['experienced_mbps']:.1f} Mbps（MCS {_far[-1]['avg_mcs']:.1f}）")
    check(_near[0]["avg_mcs"] > _far[-1]["avg_mcs"], "近点用户的平均 MCS 高于远点")

# BLER 要落在目标附近 —— 链路自适应的目标就是 10% 首传 BLER
print(f"  首传 BLER {_r6.cell['bler_first_tx']:.3f}（目标 0.10），"
      f"残留 {_r6.cell['residual_bler']:.4f}，覆盖外用户 {_r6.cell['outage_ue']}")
# 链路自适应的目标就是 10% 首传 BLER；调度器已经把覆盖外的用户剔掉了，
# 所以剩下的这些必须落在目标附近——否则就是 MCS 选择和 BLER 查表口径不一致。
check(_r6.cell["bler_first_tx"] < 0.25,
      f"首传 BLER 不显著高于目标 10%（实得 {_r6.cell['bler_first_tx']:.3f}）")
check(_r6.cell["residual_bler"] <= _r6.cell["bler_first_tx"],
      "重传之后的残留 BLER 不高于首传")

# 覆盖外的用户必须被剔除并明确报出，而不是让 PF 死盯着他们
_edge = fake_tables(n_ue=6, sinr_lo=-25.0, sinr_hi=25.0, seed=9)
_re = sysm.simulate(_edge, sys_cfg=sysm.SystemConfig(duration_s=3.0, seed=8),
                    traffic=sysm.TrafficConfig(model="ftp3", file_bytes=100_000,
                                               arrival_rate_hz=3.0))
print(f"  含深度弱覆盖用户：outage_ue={_re.cell['outage_ue']}，"
      f"首传 BLER {_re.cell['bler_first_tx']:.3f}")
check(_re.cell["outage_ue"] >= 1, "深度弱覆盖的用户被判为覆盖外")
check(_re.cell["bler_first_tx"] < 0.3,
      f"剔除覆盖外用户后 BLER 回到合理区间（实得 {_re.cell['bler_first_tx']:.3f}）")
check(any("覆盖外" in n for n in _re.notes), "覆盖外用户在 notes 里明确报出")

# ---------------------------------------------------------------------------
sect("7  速度：十万 TTI 要能秒级跑完")

import time as _time  # noqa: E402

_t0 = _time.perf_counter()
_big = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=50.0, seed=7),
                     traffic=sysm.TrafficConfig(model="ftp3", file_bytes=200_000,
                                                arrival_rate_hz=2.0))
_el = _time.perf_counter() - _t0
print(f"  {_big.config['system']['num_tti']} TTI × {len(_T)} UE 耗时 {_el:.2f}s")
check(_big.config["system"]["num_tti"] >= 100_000, "确实跑了十万个 TTI")
check(_el < 20.0, f"十万 TTI 在 20 秒内跑完（实得 {_el:.2f}s）")
check(mu.MU_MAX_RANK == 2 and mu.SU_MAX_RANK == 4, "MU/SU 秩上限是现场定的工程约束")


# ---------------------------------------------------------------------------
sect("8  样本数不是用户数")

# **这条从一次真实误判来的。** 40 个样本分布在 10 个 UE 位置上，
# 把每个样本当独立用户，小区里凭空多出 4 倍的人，每用户谱效被摊薄 4 倍——
# 表现出来是"5% 边缘用户谱效差一个数量级"，看起来像调度器把人饿死了。
_g = sysm.group_samples_by_ue(40, 10)
check(len(_g) == 10, f"40 个样本分成 10 个 UE（实得 {len(_g)}）")
check(sorted(i for grp in _g for i in grp) == list(range(40)), "样本不重不漏")
check(all(len(grp) == 4 for grp in _g), "每个 UE 拿到 4 个样本")
check(_g[0] == [0, 10, 20, 30], f"按轮转分组（实得 {_g[0]}）")

_hs = [np.ones((2, 12, 16, 4), dtype=complex) for _ in range(40)]
_merged = sysm.build_link_tables(_hs, [15.0] * 40, num_ues=10)
check(len(_merged) == 10, f"按 num_ues 合并后是 10 个用户（实得 {len(_merged)}）")
check(_merged[0].sinr_db.shape[0] == 8, "合并后每 UE 有 4 样本 x 2 时隙 = 8 个快照")
_unmerged = sysm.build_link_tables(_hs, [15.0] * 40)
check(len(_unmerged) == 40, "不给 num_ues 时仍是每样本一个用户（向后兼容）")

# 用户数直接决定每用户谱效 —— 同样的小区容量摊给不同人数
_r10 = sysm.simulate(_merged, sys_cfg=sysm.SystemConfig(duration_s=2.0, seed=11),
                     traffic=sysm.TrafficConfig(model="full_buffer"))
_r40 = sysm.simulate(_unmerged, sys_cfg=sysm.SystemConfig(duration_s=2.0, seed=11),
                     traffic=sysm.TrafficConfig(model="full_buffer"))
_p10 = float(np.mean([x["served_mbps"] for x in _r10.users]))
_p40 = float(np.mean([x["served_mbps"] for x in _r40.users]))
print(f"  10 用户每人 {_p10:.1f} Mbps / 40 用户每人 {_p40:.1f} Mbps  比值 {_p10 / _p40:.2f}")
check(_p10 > _p40 * 2.5, f"用户数翻 4 倍，每用户吞吐大致降到 1/4（比值 {_p10 / _p40:.2f}）")
check(abs(_r10.cell["cell_served_mbps"] - _r40.cell["cell_served_mbps"])
      / max(_r10.cell["cell_served_mbps"], 1) < 0.15,
      "小区总吞吐基本不变——变的只是分给几个人")

# ---------------------------------------------------------------------------
sect("9  MU 增益：实测比值，不是拍脑袋的常数")

_hm = [((np.random.default_rng(50 + u).standard_normal((4, 12, 32, 4))
         + 1j * np.random.default_rng(80 + u).standard_normal((4, 12, 32, 4)))
        / np.sqrt(2)) for u in range(8)]
_g = sysm.measure_mu_gain(_hm, [15.0] * 8)
print(f"  MU/SU 比值 {_g['ratio']:.3f}  逐快照 {_g.get('per_snapshot')}  "
      f"离散度 {_g.get('relative_spread')}")
check(_g["measured"] is True, "确实测出来了而不是回落到默认值")
check(_g["ratio"] > 0, "比值为正")
check(len(_g["per_snapshot"]) >= 2, "多个快照各测一次")
check("标量近似" in _g["note"], "把这是个近似说清楚了")
check("relative_spread" in _g, "离散度一起返回——它就是这个近似的可信度")

_g_bad = sysm.measure_mu_gain(_hm[:1], [15.0], max_mu_users=4)
check(_g_bad["measured"] is False, "单用户时 MU 增益明确标成未测得")
check(bool(_g_bad.get("errors")), "MU 配对失败返回可审计错误，而不是静默吞掉")
check("禁止用于仿真" in _g_bad["note"], "诊断占位 1.0 不冒充可用 MU 增益")

# 比值 <= 1 时调度器不该切 MU（SU 无干扰且可到 rank4）
_r_su = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=2.0, seed=12),
                      traffic=sysm.TrafficConfig(model="full_buffer"),
                      sched=sysm.SchedulerConfig(mu_enabled=True), mu_se_ratio=0.8)
check(_r_su.cell["mu_share"] == 0.0, "MU 不划算时（比值<1）自适应选 SU，不强行配对")
_r_mu = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=2.0, seed=12),
                      traffic=sysm.TrafficConfig(model="full_buffer"),
                      sched=sysm.SchedulerConfig(mu_enabled=True), mu_se_ratio=1.6)
print(f"  比值 0.8 -> MU 占比 {_r_su.cell['mu_share']:.0%}；"
      f"比值 1.6 -> MU 占比 {_r_mu.cell['mu_share']:.0%}")
check(_r_mu.cell["mu_share"] > 0.5, "MU 划算时确实切过去")
check(_r_mu.cell["cell_served_mbps"] > _r_su.cell["cell_served_mbps"],
      "切到 MU 之后小区吞吐确实更高")

# ---------------------------------------------------------------------------
sect("10  健壮性回归：这些都是真踩到过的")

# **nan 的几何 SINR 能真到这儿**（被拒样本、全零信道、几何量缺失）。
# 早先一个用户的一个快照就能把整条系统级仿真挂掉，
# 报的还是 "cannot convert float NaN to integer"，看不出是谁。
_tn = sysm.build_link_tables([np.ones((2, 8, 8, 2), dtype=complex)] * 2,
                             [20.0, float("nan")])
check(len(_tn) == 2, "nan 几何 SINR 不抛异常")
check(bool(_tn[1].outage.all()), "nan 的用户判为覆盖外，而不是随便给个 MCS")
check(bool(np.all(np.isfinite(_tn[0].se))), "正常用户不受影响")

# MCS 选择必须自己兜住非有限输入
from superwireless import linkadapt as _la2  # noqa: E402

for _v, _want in ((float("nan"), 0), (float("-inf"), 0), (float("inf"), 27)):
    check(_la2.select_mcs(_v, table=3).index == _want,
          f"select_mcs({_v}) -> MCS {_want}")

# **RBG 尺寸分布必须跟着配置的 num_rbg 走。**
# 早先 draw_rbg 签名给了默认 17、调用处又不传，num_rbg=8 的配置照样抽 1~17，
# 实测平均 9.03 个 RBG 却只有 8 个可用、满带宽占比从 0.30 变成 0.586。
_tb8 = sysm.build_link_tables([np.ones((2, 8, 8, 2), dtype=complex)] * 4, [20.0] * 4)
_r8 = sysm.simulate(_tb8, sys_cfg=sysm.SystemConfig(duration_s=3.0, num_rbg=8, seed=2),
                    traffic=sysm.TrafficConfig(model="bimodal", arrival_rate_hz=6.0))
_h8 = _r8.cell["rbg_size_hist"]
print(f"  num_rbg=8：1RBG {_h8['p_1rbg']:.2f} 满带宽 {_h8['p_full']:.2f} "
      f"平均 {_h8['mean_rbg']:.2f}")
check(_h8["mean_rbg"] <= 8.0, f"RBG 尺寸不超过 num_rbg（实得均值 {_h8['mean_rbg']}）")
check(0.15 < _h8["p_full"] < 0.5, f"满带宽占比回到 0.30 附近（实得 {_h8['p_full']}）")

# **PF 度量必须和实发口径一致。** MU 下实发被限到 rank≤2，
# 记 rank4 的谱效会让 PF 以为给足了，公平性判据整个偏掉。
_src = _inspect.getsource(sysm.simulate)
check("min(int(tables[u].best_rank[snap]), mu.MU_MAX_RANK)" in _src,
      "PF 更新在 MU 下用 rank≤2 的谱效")

# 形状不一致要当场报错，不能静默广播出错误结果
try:
    mu.effective_user_channels([np.ones((1, 4, 8, 2), dtype=complex),
                                np.ones((1, 8, 8, 2), dtype=complex)])
    check(False, "各用户形状不一致时报错")
except ValueError as _e:
    check("形状必须一致" in str(_e), f"各用户形状不一致时报错（{_e}）")

# 全员覆盖外不能崩，且要说清楚
_tz = sysm.build_link_tables([np.ones((2, 8, 8, 2), dtype=complex)] * 3, [-40.0] * 3)
_rz = sysm.simulate(_tz, sys_cfg=sysm.SystemConfig(duration_s=1.0),
                    traffic=sysm.TrafficConfig(model="ftp3", arrival_rate_hz=5.0))
check(_rz.cell["outage_ue"] == 3, "全员覆盖外时如实报 3 个")
check(np.isfinite(_rz.cell["cell_served_mbps"]), "吞吐是有限值不是 nan")
check(any("覆盖外" in n for n in _rz.notes), "notes 里点明覆盖外")

# 邻区负载为 0 必须等价于无干扰
check(abs(sysm.apply_neighbor_load(10.0, 12.0, 0.0)
          - sysm.interference_free_sinr(10.0, 12.0)) < 1e-6,
      "邻区负载 0 等价于无干扰")

# **快照间隔是 5 ms 不是一个 TTI。** ChannelHub 的多时隙输出是连续的
# SRS/CSI-RS 机会（每快照推进 max(srs_per,csirs_per)×slot_duration），
# 当成一个 TTI 会让所有时间相关的结论差 10 倍。
check(abs(sysm.snapshot_interval_ms({}) - 5.0) < 1e-9,
      f"默认快照间隔 5 ms（实得 {sysm.snapshot_interval_ms({})}）")
check(abs(sysm.snapshot_interval_ms({"srs_periodicity": 20}) - 10.0) < 1e-9,
      "SRS 周期翻倍则快照间隔翻倍")
check(abs(sysm.snapshot_interval_ms({"subcarrier_spacing": 15000}) - 10.0) < 1e-9,
      "15 kHz SCS 的 slot 是 1 ms，快照间隔 10 ms")
check(abs(sysm.SystemConfig().snapshot_update_ms - 5.0) < 1e-9,
      "默认值就是算出来的那个，不是拍脑袋的 10.0")

# ---------------------------------------------------------------------------
sect("11  2026-08-07 自审修掉的三个口径 bug")
# ---------------------------------------------------------------------------
import inspect as _insp  # noqa: E402

_src = _insp.getsource(sysm.simulate)

# --- bug A：重传 BLER 查的必须是实发 MCS，不是真实 SINR 反查的理想档 ---
# 用低档查 ReTx 曲线 → 重传几乎必然成功 → 残留 BLER 系统性偏低，
# 而这个偏差不会以任何方式报出来。
check('_bler_lookup(m, float(tables[u].sinr_db[snap, r - 1]), "retx")' in _src,
      "重传 BLER 查的是实发 MCS m")
check('_bler_lookup(int(tables[u].mcs[snap, r - 1]),' not in _src,
      "旧的错误写法（拿理想档查重传）已经不在了")

# --- bug D：公司 BLER 源曲线是 0.05 dB 网格，缓存不能粗化成 0.5 dB ---
# MCS15 在 14.24 dB 的细网格值是 14.25 dB/2.53%，旧 0.5 dB 路径会取
# 14.0 dB/13.2%，ACK/NACK 概率差五倍，不是可忽略的性能优化。
_x_bler = 14.24
_curve15 = bc.get_curve(15, "newtx")
_want_bler = float(_curve15.evaluate(14.25)[0])
_coarse_bler = float(_curve15.evaluate(14.0)[0])
check(abs(sysm._bler_lookup(15, _x_bler) - _want_bler) < 1e-12,
      "legacy 系统 BLER 缓存保持公司曲线 0.05 dB 分辨率")
check(abs(expm._bler_lookup(15, _x_bler) - _want_bler) < 1e-12,
      "experience_v2 BLER 缓存保持公司曲线 0.05 dB 分辨率")
check(abs(_coarse_bler - _want_bler) > 0.05,
      "反向哨兵：旧 0.5 dB 量化在瀑布区确会造成显著概率偏差")

# --- bug B：S 时隙的 RE 与 dl_ratio 必须用同一个系数 ---
check(abs(sysm.S_SLOT_DL_FRACTION - 0.7) < 1e-9, "S 时隙折合系数 0.7")
check("_re_of[_slot]" in _src, "主循环按时隙类型取 RE，不是所有时隙一个数")
_dd = sysm.SystemConfig(tdd_pattern="DDDD").dl_ratio
_ds = sysm.SystemConfig(tdd_pattern="DDDS").dl_ratio
check(abs(_dd - 1.0) < 1e-9 and abs(_ds - (3 + 0.7) / 4) < 1e-9,
      f"dl_ratio 用同一个常量（DDDD={_dd:.3f}, DDDS={_ds:.3f}）")
# 纯 D 与含 S 的图案，实发字节必须有可分辨的差——否则说明 S 还是被当成满下行
_tb_s = fake_tables(n_ue=6, n_snap=6, seed=17)
_rd = sysm.simulate(_tb_s, sys_cfg=sysm.SystemConfig(duration_s=1.0, tdd_pattern="DDDD"),
                    traffic=sysm.TrafficConfig(model="full_buffer"))
_rs = sysm.simulate(_tb_s, sys_cfg=sysm.SystemConfig(duration_s=1.0, tdd_pattern="SSSS"),
                    traffic=sysm.TrafficConfig(model="full_buffer"))
_bd = _rd.as_dict()["cell"]["cell_served_mbps"]
_bs = _rs.as_dict()["cell"]["cell_served_mbps"]
print(f"  全 D {_bd:.1f} Mbps vs 全 S {_bs:.1f} Mbps，比值 {_bs / max(_bd, 1e-9):.3f}")
check(abs(_bs / max(_bd, 1e-9) - 0.7) < 0.06,
      f"全 S 图案的吞吐约为全 D 的 0.7 倍（实得 {_bs / max(_bd, 1e-9):.3f}）")

# --- bug C：p_idle_tti 是对标锚点不是仿真输入，偏离要告警 ---
# **它从来不生成空闲 TTI**，改它只改报告里的解析式。不说清楚的话，
# 用户会以为设了 30% 就真是 30%。
_c0 = sysm.TrafficConfig(model="bimodal", p_idle_tti=0.30)
_c1 = sysm.TrafficConfig(model="bimodal", p_idle_tti=0.90)
_r0 = sysm.simulate(_tb_s, sys_cfg=sysm.SystemConfig(duration_s=1.0, seed=3), traffic=_c0)
_r1 = sysm.simulate(_tb_s, sys_cfg=sysm.SystemConfig(duration_s=1.0, seed=3), traffic=_c1)
check(abs(_r0.as_dict()["cell"]["occupancy"]
          - _r1.as_dict()["cell"]["occupancy"]) < 1e-12,
      "p_idle_tti 改了 0.30→0.90，实际占用率**逐位不变**（它确实不驱动仿真）")
check(_c0.expected_prb_util() != _c1.expected_prb_util(),
      "但它确实改变了报告里的 expected_prb_util —— 这正是容易被误读的地方")
# --- bug D：IoT 有效率必须按**样本**算，不是按用户 ---
# 一个用户 8 个快照里 4 个算不出 IoT，nanmedian 照样给有限值 → 该用户算"有效"
# → 小区级报 100% → 正确的多时隙告警从不触发，反而触发"检查站间距"那条，
# **把用户支使去查一个根本没问题的配置**。
# 关键是**同一个用户身上有好有坏**——8 个样本按 group_samples_by_ue 并成 4 个 UE，
# UE u 拿到样本 u（SIR 20，有效）和 u+4（SIR 5 < SINR，物理上不可能）。
# 这样每个 UE 的 nanmedian 都是有限值，逐用户口径于是报 100%。
_bad_g = [12.0] * 8
_bad_s = [20.0] * 4 + [5.0] * 4
_tb_iot = sysm.build_link_tables(
    [np.stack([np.ones((8, 8, 2), dtype=complex) * (i % 4 + 1)] * 2) for i in range(8)],
    _bad_g, geo_sir_db=_bad_s, num_ues=4, num_snapshots=2)
_sv = [t.iot_sample_valid for t in _tb_iot]
print(f"  逐样本有效率 {np.mean(_sv):.0%}（构造成一半不可能）")
check(np.mean(_sv) < 0.9, f"逐样本口径抓得住（实得 {np.mean(_sv):.0%}）")
check(all(np.isfinite(t.iot_db) for t in _tb_iot),
      "而逐用户口径全都是有限值——正是它骗人的地方")
_r_iot = sysm.simulate(_tb_iot, sys_cfg=sysm.SystemConfig(duration_s=1.0))
_d_iot = _r_iot.as_dict()
check(_d_iot["cell"]["iot_sample_valid_share"] < 0.9
      and _d_iot["cell"]["iot_valid_ue_share"] > 0.9,
      "小区级两个口径同时报出来，差异可见")
_ntxt = "".join(_d_iot["notes"])
check("IoT 不可信" in _ntxt, "触发的是「IoT 不可信」而不是「检查站间距」")
check("检查是不是站间距太大" not in _ntxt, "误导性的那条被抑制了")

# p_idle_tti=0.30（意图 30% 空闲）而实测接近全空，差得远 → 必须告警
_hint = "".join(_r0.as_dict()["notes"])
check("不驱动仿真" in _hint,
      "实测空闲率与 p_idle_tti 差得远时，notes 里明说它不驱动仿真")
# **既有的那条 PRB 利用率告警曾经在给错建议**（让人去调 p_idle_tti 对齐现网）
check("别指望调 p_idle_tti" in _hint or "p_idle_tti 或把中间段" not in _hint,
      "PRB 利用率告警不再建议去调那个不起作用的旋钮")

# ---------------------------------------------------------------------------
sect("12  experience_v2：DRB busy-period、按需 RBG 与 Rel-19 小 burst")
# ---------------------------------------------------------------------------

# --- TBS 表：D/S × 28 MCS × rank1..4 × 17 RBG，反查必须给最小够用值 ---
_lut = expm.TbsLookup.build(17, 16, sysm.S_SLOT_DL_FRACTION)
check(_lut.values.shape == (2, 28, 4, 17),
      f"TBS 表覆盖 D/S、28 MCS、rank1..4、17 RBG（实得 {_lut.values.shape}）")
check(bool(np.all(np.diff(_lut.values, axis=-1) > 0)),
      "全部 224 条 D/S×MCS×rank 序列严格递增")
_minimal = True
for _slot in ("D", "S"):
    for _m in range(28):
        for _rank in range(1, 5):
            _row = _lut.row(_slot, _m, _rank)
            for _n, _bytes in enumerate(_row, start=1):
                _got, _fits = _lut.required_rbg(_slot, _m, _rank, int(_bytes))
                _minimal &= _fits and _got == _n
check(_minimal, "searchsorted 对每个表项都返回最小够用 RBG")
_m12 = _lut.row("D", 12, 2)
_nonlinear = float(_m12[-1] / (17 * _m12[0]) - 1.0)
check(abs(_nonlinear - 0.011193141224100867) < 1e-9,
      f"MCS12/rank2 的 17 RBG TBS 比线性外推高 1.119%（实得 {_nonlinear:.3%}）")

# --- busy period 是 buffer 空→非空→空；期间新 arrival 合并，不按 file 硬切 ---
_cls = sysm.TrafficClassConfig("small", 1.0, 100, 1.0, pdb_ms=10.0, is_small=True)
_q = expm.DrbQueue(0, _cls)
_q.arrive(0, 100)
_first_obj = _q.active
_q.arrive(1, 50)
check(_q.active is _first_obj and _q.active.bytes_arrived == 150,
      "非空期间的新文件并入同一个 DRB busy period")
_q.transmit(2, 80, 60, ack=True)
_q.transmit(4, 100, 90, ack=True)
check(_q.active is None and len(_q.done) == 1 and _q.done[0].bytes_acked == 150,
      "buffer 重新变空时才结束 busy period，ACK 字节完整")
check(len(_q.done_items) == 2
      and expm.arrival_item_metrics(_q.done_items[0], 0.5, 10.0) == (1.0, 2.5, False)
      and expm.arrival_item_metrics(_q.done_items[1], 0.5, 10.0) == (1.5, 2.0, False),
      "busy period 内每个 FIFO 到达对象单独记录首调度等待、完成时延与 PDB")

# --- 28.552 large burst：首传起算，排除清空 buffer 的最后一段 ---
_bp = expm.BusyPeriod(start_tti=0, traffic_class="large", pdb_ms=10,
                      bytes_arrived=350, bytes_acked=350,
                      first_tx_tti=2, last_ack_tti=7, tx_attempts=3,
                      ack_events=[expm.AckEvent(2, 100, 100, 0),
                                  expm.AckEvent(4, 200, 200, 0),
                                  expm.AckEvent(7, 50, 100, 50)])
_bm = expm.burst_metrics(_bp, 0.5)
check(abs((_bm.throughput_mbps or 0) - 1.6) < 1e-9,
      f"large burst 用首传→倒数第二 ACK 的 300 B/1.5 ms（实得 {_bm.throughput_mbps} Mbps）")
check(_bm.queue_wait_ms == 1.0 and _bm.completion_delay_ms == 4.0,
      "排队等待与 arrival→completion 时延单独上报，不混进标准吞吐")

# --- Rel-19 小 burst：有效时间按 payload/TBVol 折成 slot 的一部分 ---
_sp = expm.BusyPeriod(start_tti=3, traffic_class="small", pdb_ms=1,
                      bytes_arrived=250, bytes_acked=250,
                      first_tx_tti=3, last_ack_tti=3, tx_attempts=1,
                      ack_events=[expm.AckEvent(3, 250, 1000, 750)])
_sm = expm.burst_metrics(_sp, 0.5, "fractional_slot")
check(abs((_sm.throughput_mbps or 0) - 16.0) < 1e-9
      and _sm.throughput_kind == "rel19_fractional_slot",
      f"小 burst 250/1000 TB 折成 0.125 ms，吞吐 16 Mbps（实得 {_sm.throughput_mbps}）")
check(expm.burst_metrics(_sp, 0.5, "exclude").throughput_mbps is None,
      "可显式保留旧式 exclude 口径，但不再声称小 burst 永远不可测")

# --- 真正跑 experience_v2：一 TTI 多 UE、实际 RBG、scheduled-TBS PF、守恒 ---
_ex_cfg = sysm.SystemConfig(evaluation_mode="experience", duration_s=0.8,
                            seed=41, tdd_pattern="DDDSU")
_ex_tr = sysm.TrafficConfig(model="mixed", small_ue_share=1.0,
                            small_file_bytes=500, small_arrival_rate_hz=200.0,
                            arrival_rate_hz=0.0)
_ex = sysm.simulate(
    _T, sys_cfg=_ex_cfg, traffic=_ex_tr,
    sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False,
                               pf_accounting="auto"),
    kpi=sysm.KpiConfig(warmup_tti=0, small_burst_policy="fractional_slot"))
check(_ex.config["system"]["model_version"] == "experience_v2"
      and _ex.cell["pf_accounting"] == "scheduled_tbs",
      "模式与 PF 口径显式版本化：experience_v2 / scheduled_tbs")
check(_ex.diagnostics["tbs_lookup"]["entries"] == 3808,
      "结果携带实际使用的 3808 项 TBS 表口径")
check("[TTI,UE]" in _ex.diagnostics["crn_event_mapping"],
      "ACK/NACK 与 tie-break 随机数按 [TTI,UE] 固定映射，A/B 调度分叉不串流")
check(_ex.diagnostics["rbg_overlap_violations"] == 0
      and _ex.diagnostics["max_rbg_in_any_tti"] <= 17,
      "同 TTI RBG 不重叠且总分配不超过 17")
check(_ex.cell["multi_ue_tti_share"] > 0,
      f"按需分配后同一 TTI 确实能服务多个 UE（占比 {_ex.cell['multi_ue_tti_share']:.1%}）")
check(_ex.cell["accounting_error_pct"] < 1e-9,
      f"experience 字节守恒：arrived=acked+queued（误差 {_ex.cell['accounting_error_pct']}%）")
check(_ex.cell["small_burst_fractional_mbps"] is not None,
      "单 TTI 小 burst 用 Rel-19 fractional-slot KPI 得到可测样本")
check(_ex.cell["small_queue_wait_ms_p95"] is not None
      and _ex.cell["completed_arrival_objects"] > 0
      and _ex.cell["class_arrival_kpis"]["small"]["is_small"],
      "小包 FIFO 到达对象的等待/PDB 与 DRB busy-period 吞吐分层统计")
check("small 到达对象等待 P95" in _ex.text(),
      "experience_v2 单次结果摘要使用体验模式字段，不访问 legacy 专属字段")
check(_ex.cell["resource_utilization"] <= _ex.cell["occupancy"] + 1e-12,
      "resource utilization 与 busy-TTI occupancy 分开，不用 padding 伪装满带")
_partial = [a for a in _ex.diagnostics["allocation_sample"] if a["n_rbg"] < 17]
check(bool(_partial) and all(a["pf_credit_bytes"] == a["scheduled_bytes"] for a in _partial),
      "只拿部分 RBG 的 UE 按实际 scheduled TBS 记 PF，不按全带记账")
_by_tti: dict[int, set[int]] = {}
_sample_overlap = False
for _a in _ex.diagnostics["allocation_sample"]:
    _seen = _by_tti.setdefault(_a["tti"], set())
    _sample_overlap |= bool(_seen.intersection(_a["rbg_indices"]))
    _seen.update(_a["rbg_indices"])
check(not _sample_overlap, "allocation 明细中的 RBG bitmap 也逐 TTI 无重叠")

# 反向控制只验证“口径真的错开”，不把收益方向写成单测硬门禁。
_wrong = sysm.simulate(
    _T, sys_cfg=_ex_cfg, traffic=_ex_tr,
    sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False,
                               pf_accounting="legacy_fullband"),
    kpi=sysm.KpiConfig(warmup_tti=0))
_wrong_partial = [a for a in _wrong.diagnostics["allocation_sample"] if a["n_rbg"] < 17]
check(bool(_wrong_partial)
      and any(a["pf_credit_bytes"] > a["scheduled_bytes"] for a in _wrong_partial),
      "反向控制 legacy_fullband 确实会给部分带宽用户按全带记账；效果方向留给门3")

# legacy 路径保持独立；不允许跨模式参数静默退化。
check(sysm.SystemConfig().as_dict()["model_version"] == "legacy_v1",
      "默认 capacity 路径仍标 legacy_v1，历史结果可复现")
try:
    sysm.simulate(_T, sys_cfg=sysm.SystemConfig(evaluation_mode="experience"),
                  traffic=sysm.TrafficConfig(model="mixed"),
                  sched=sysm.SchedulerConfig(mu_enabled=True))
    check(False, "experience_v2 开 MU 时硬报错")
except ValueError as _e:
    check("只支持 SU" in str(_e), f"experience_v2 开 MU 时硬报错（{_e}）")
try:
    sysm.simulate(_T, sys_cfg=sysm.SystemConfig(evaluation_mode="experience"),
                  traffic=sysm.TrafficConfig(model="bimodal"),
                  sched=sysm.SchedulerConfig(mu_enabled=False))
    check(False, "experience_v2 拒绝按目标 RBG 反推包长")
except ValueError as _e:
    check("请用 mixed" in str(_e), f"experience_v2 拒绝因果倒置的 bimodal（{_e}）")

# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("系统级仿真：话务、调度、HARQ、体验速率口径、守恒对账全部通过。")
