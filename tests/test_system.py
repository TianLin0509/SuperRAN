"""系统级仿真：话务、PF 调度、HARQ、体验速率口径、守恒对账。

直接运行：python tests/test_system.py
"""
from __future__ import annotations

import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(errors="replace")

from superran import bler_curves as bc  # noqa: E402
from superran import carrier as cgrid  # noqa: E402
from superran import experience as expm  # noqa: E402
from superran import kpi_compare as kcmp  # noqa: E402
from superran import kpi_view  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import mumimo as mu  # noqa: E402
from superran import rng as rg  # noqa: E402
from superran import system as sysm  # noqa: E402
from superran import traffic as trafm  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(t: str) -> None:
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


class pytest_raises:
    """极小的 raises 上下文；这个文件是脚本式测试，不引 pytest。"""

    def __init__(self, *exc_types):
        self.exc_types = exc_types or (Exception,)
        self.raised = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            print(f"  FAIL  应当抛出 {self.exc_types} 却没有")
            FAILED.append(f"应当抛出 {self.exc_types} 却没有")
            return False
        if issubclass(exc_type, self.exc_types):
            self.raised = exc
            return True
        return False


def fake_tables(n_ue=8, n_snap=8, sinr_lo=0.0, sinr_hi=25.0, seed=0,
                power_constraint="nebf"):
    """造一批链路表，SINR 从近点到远点铺开，带时间起伏。"""
    rng = np.random.default_rng(seed)
    geo = np.linspace(sinr_hi, sinr_lo, n_ue)
    hs = []
    for _ in range(n_ue):
        h = ((rng.standard_normal((n_snap, 24, 16, 4))
              + 1j * rng.standard_normal((n_snap, 24, 16, 4))) / np.sqrt(2))
        hs.append(h)
    return sysm.build_link_tables(
        hs, list(geo), power_constraint=power_constraint)


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
_w = _r.diagnostics["measurement_window"]
check(int(_w["balance_error_bytes"]) == 0,
      "测量窗内整数原始字节严格守恒，不靠 Mbps 舍入容差")

# ---------------------------------------------------------------------------
sect("3  体验速率：28.552 DRB busy-period 是唯一口径")

# legacy 的 trim（none/tail/head_tail）随容量路径一起下线。现在只剩一个口径，
# 但它是分层的：busy-period 吞吐、含头速率、到达对象的等待与完成时延各报各的。
_rr = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=5.0, seed=2),
                    traffic=sysm.TrafficConfig(model="ftp3", file_bytes=200_000,
                                               arrival_rate_hz=2.0))
_res = {
    "rel19": _rr.cell["drb_throughput_rel19_mbps"],
    "head_inclusive": _rr.cell["drb_throughput_head_inclusive_mbps"],
    "median": _rr.cell["ue_experienced_median_mbps"],
}
for _k, _v in _res.items():
    print(f"  {_k:<16} {_v:8.2f} Mbps")
check(all(v > 0 for v in _res.values()), "三个分层口径都算得出数")
check(_res["head_inclusive"] <= _res["rel19"] + 1e-9,
      "含头速率把首包等待加进分母，只可能更低")
check(len({round(v, 3) for v in _res.values()}) >= 2,
      f"三个分层口径给出不同的数（实得 {_res}）——口径必须跟着结果走")
check("busy-period" in _rr.config["kpi"]["throughput_note"]
      and "full_buffer" in _rr.config["kpi"]["throughput_note"],
      "口径与它的解释一起返回，并说明 full_buffer 下报 None")
# legacy 的 trim 旋钮随容量路径下线：给了就硬失败，不静默忽略。
with pytest_raises(TypeError):
    sysm.KpiConfig(trim="tail")

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

# full buffer 下 buffer 永不排空 ⇒ 每个 UE 恰好一个永不结束的 busy period。
# TS 128 552 V19.5.0 p54：样本只在 "DRB DL buffer emptied" 事件上形成，
# **所以标准 KPI 在这里没有样本**——这是标准的定义，不是实现缺陷。
# 需要数的用户看两个工程字段：ue_served_*（ITU 口径）与 active_window_goodput。
_fb = sysm.simulate(_T, sys_cfg=sysm.SystemConfig(duration_s=2.0),
                    traffic=sysm.TrafficConfig(model="full_buffer"))
check(_fb.cell["drb_throughput_completed_bursts"] == 0
      and _fb.cell["drb_throughput_inflight_bursts"] == len(_T),
      "full buffer 下每个 UE 恰好一个在飞 busy period，没有已完成的")
check(_fb.cell["measured_bursts"] == 0,
      f"标准样本数为 0（实得 {_fb.cell['measured_bursts']}）——在飞段不算标准样本")
check(_fb.cell["drb_throughput_rel19_mbps"] is None
      and _fb.cell["cell_experienced_mbps"] is None,
      "标准 KPI 报 None：工程量不许顶 TS 28.552 的名字")
check(_fb.cell["active_window_goodput_mbps"] is not None
      and _fb.cell["active_window_goodput_mbps"] > 0
      and _fb.cell["ue_served_p5_mbps"] > 0,
      "两个工程字段照常有值，用户拿得到数")

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
                                               arrival_rate_hz=5.0),
                    kpi=sysm.KpiConfig(warmup_s=0.0))
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

# 用户数直接决定每用户谱效 —— 同样的小区容量摊给不同人数。
# **OLLA 必须关掉**：这条守的是"资源怎么分"，不是"OLLA 收敛得多快"。
# 这个夹具是全 1 信道、rank2 下 SINR ≈ 0 dB，正好压在 MCS 表最底下一档的
# 边界上；OLLA 开着时 10 个 UE 每人被调度的次数是 40 个 UE 的 4 倍，
# 偏置爬升速度差 4 倍，于是"小区总吞吐"里混进了一个与分组方式无关的
# 收敛暂态。关掉 OLLA 后两边逐值相等（相对差 0.0000、每用户比值精确 4.00），
# 比原来 0.15 的容差强得多。
_r10 = sysm.simulate(_merged, sys_cfg=sysm.SystemConfig(duration_s=2.0, seed=11),
                     traffic=sysm.TrafficConfig(model="full_buffer"),
                     sched=sysm.SchedulerConfig(olla_enabled=False))
_r40 = sysm.simulate(_unmerged, sys_cfg=sysm.SystemConfig(duration_s=2.0, seed=11),
                     traffic=sysm.TrafficConfig(model="full_buffer"),
                     sched=sysm.SchedulerConfig(olla_enabled=False))
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

_g_3d = sysm.measure_mu_gain([h[0] for h in _hm], [15.0] * 8)
_g_4d_one = sysm.measure_mu_gain([h[0:1] for h in _hm], [15.0] * 8)
check(_g_3d["per_snapshot"] == _g_4d_one["per_snapshot"]
      and _g_3d["ratio"] == _g_4d_one["ratio"],
      "measure_mu_gain 的 3D 单快照与显式 T=1 完全一致，不把 RB 当时间")

_g_loaded = sysm.measure_mu_gain(
    _hm, [15.0] * 8, geo_sir_db=[18.0] * 8,
    neighbor_load=0.3, neighbor_load_jitter=0.0)
_g_full = sysm.measure_mu_gain(
    _hm, [15.0] * 8, geo_sir_db=[18.0] * 8,
    neighbor_load=1.0, neighbor_load_jitter=0.0)
_eff_loaded = np.asarray(_g_loaded["effective_geo_sinr_db"])
_eff_full = np.asarray(_g_full["effective_geo_sinr_db"])
check(np.all(_eff_loaded > _eff_full)
      and _g_loaded["neighbor_load"]["prb_utilization"] == 0.3,
      "measure_mu_gain 与链路表复用同一邻区负载工作点，不再拿 full-buffer 比值回乘")

_g_bad = sysm.measure_mu_gain(_hm[:1], [15.0], max_mu_users=4)
check(_g_bad["measured"] is False, "单用户时 MU 增益明确标成未测得")
check(bool(_g_bad.get("errors")), "MU 配对失败返回可审计错误，而不是静默吞掉")
check("禁止用于仿真" in _g_bad["note"], "诊断占位 1.0 不冒充可用 MU 增益")

# **标量比值口径 se_ratio_legacy 已于 2026-09-04 废除**（#17），它的另一个宿主
# legacy 容量主循环也随本 PR 一起下线。它只把 TBS 乘 mu_se_ratio/K，配对代价
# 不进误块抽签——「包变小但不更容易错」。保留成兜底只会让这种乐观静默发生，
# 所以在**配置构造**这一层就硬失败，而不是等跑完给一段 notes。
_T_legacy_ebf = fake_tables(power_constraint="ebf")
with pytest_raises(ValueError) as _exc_se:
    sysm.SchedulerConfig(mu_enabled=True, mu_accounting="se_ratio_legacy")
check("se_ratio_legacy" in str(_exc_se.raised),
      "标量 MU 记账口径已下线，错误信息点名它")
with pytest_raises(ValueError) as _exc_pfa:
    sysm.SchedulerConfig(pf_accounting="legacy_best_se")
check("legacy_best_se" in str(_exc_pfa.raised),
      "legacy PF 记账口径已下线，错误信息点名它")
# pair 表就是按两用户 × rank2 建的，别的维度不属于合法域
with pytest_raises(ValueError):
    sysm.SchedulerConfig(mu_enabled=True, max_mu_users=4)
with pytest_raises(ValueError):
    sysm.SchedulerConfig(mu_enabled=True, mu_rank_per_user=1)

# --- 开 MU 走 pair_table 但没建 pair 表要硬失败，不静默降级 -------------
try:
    sysm.simulate(
        _T_legacy_ebf,
        sys_cfg=sysm.SystemConfig(duration_s=0.05, seed=12,
                                  power_constraint="ebf"),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(mu_enabled=True,
                                   mu_accounting="pair_table"),
        kpi=sysm.KpiConfig(warmup_s=0.0))
except ValueError as _exc:
    check("pair graph" in str(_exc) and "完整" in str(_exc),
          "开 MU 走 pair_table 但没建 pair 表时硬失败")
else:
    check(False, "缺 pair 表却没有报错")

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
from superran import linkadapt as _la2  # noqa: E402

for _v, _want in ((float("nan"), 0), (float("-inf"), 0), (float("inf"), 27)):
    check(_la2.select_mcs(_v, table=3).index == _want,
          f"select_mcs({_v}) -> MCS {_want}")

# **实际 RBG 占用必须跟着配置的 num_rbg 走，且由真实 TBS 反查决定。**
# 退役的 bimodal 是反过来的：先抽"占几个 RBG"再乘写死的谱效折成字节，
# 于是 num_rbg=8 的配置照样抽出 1~17 个 RBG。现在这个方向不可能再出现。
_tb8 = sysm.build_link_tables([np.ones((2, 8, 8, 2), dtype=complex)] * 4, [20.0] * 4)
_r8 = sysm.simulate(_tb8, sys_cfg=sysm.SystemConfig(duration_s=3.0, num_rbg=8, seed=2),
                    traffic=sysm.TrafficConfig(model="ftp3", file_bytes=20_000,
                                               arrival_rate_hz=6.0),
                    kpi=sysm.KpiConfig(warmup_s=0.0))
_h8 = _r8.cell["actual_rbg_size_hist"]
print(f"  num_rbg=8：实际 grant 平均 {_h8['mean_rbg']:.2f} 个 RBG，"
      f"满带宽占比 {_h8['p_full']:.2f}，样本 {_h8['n']}")
check(_h8["mean_rbg"] <= 8.0, f"RBG 尺寸不超过 num_rbg（实得均值 {_h8['mean_rbg']}）")
check(_h8["scope"] == "nonzero_grant_size_not_tti_total",
      "这是实际 grant 的分布，不是话务侧反推出来的目标 RBG 数")
with pytest_raises(ValueError) as _exc_bim:
    sysm.TrafficConfig(model="bimodal", arrival_rate_hz=6.0)
check("cdf" in str(_exc_bim.raised),
      "bimodal 已下线，错误信息指向 cdf/mixed 的迁移路径")

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
                    traffic=sysm.TrafficConfig(model="ftp3", arrival_rate_hz=5.0),
                    kpi=sysm.KpiConfig(warmup_s=0.0))
check(_rz.cell["outage_ue"] == 3, "全员覆盖外时如实报 3 个")
check(np.isfinite(_rz.cell["cell_served_mbps"]), "吞吐是有限值不是 nan")
check(any("覆盖外" in n for n in _rz.notes), "notes 里点明覆盖外")

# 邻区负载为 0 必须等价于无干扰
check(abs(sysm.apply_neighbor_load(10.0, 12.0, 0.0)
          - sysm.interference_free_sinr(10.0, 12.0)) < 1e-6,
      "邻区负载 0 等价于无干扰")

# API 的抖动幅度必须真的进入建表，不能无论传什么都暗中固定成 ±5%。
_load_h = np.ones((1, 16, 8, 2), dtype=np.complex128)
_load_fixed = sysm.build_link_tables(
    [_load_h], [10.0], geo_sir_db=[12.0], neighbor_load=0.3,
    neighbor_load_jitter=0.0, load_jitter_rng=np.random.default_rng(7))
_load_wide = sysm.build_link_tables(
    [_load_h], [10.0], geo_sir_db=[12.0], neighbor_load=0.3,
    neighbor_load_jitter=0.5, load_jitter_rng=np.random.default_rng(7))
check(abs(_load_fixed[0].geo_sinr_db - _load_wide[0].geo_sinr_db) > 0.1,
      "neighbor_load_jitter 的实参真实改变逐快照负载折算，不再写死 ±5%")
try:
    sysm.NeighborLoadConfig(prb_utilization=1.1)
    check(False, "非法邻区利用率应硬失败")
except ValueError:
    check(True, "邻区利用率与抖动范围在入口硬校验")

# **快照间隔是独立时钟。** 新数据显式保存sample_interval_s；旧数据缺字段时
# 才从SRS/CSI-RS周期回退推断。不能拿0.5-ms slot、5-ms双腿间隔或10-ms
# 四端口SRS周期互相替代。
check(abs(sysm.snapshot_interval_ms({}) - 5.0) < 1e-9,
      f"旧数据回退默认快照间隔 5 ms（实得 {sysm.snapshot_interval_ms({})}）")
check(abs(sysm.snapshot_interval_ms({"srs_periodicity": 20}) - 10.0) < 1e-9,
      "旧数据回退时SRS周期翻倍使推断间隔翻倍")
check(abs(sysm.snapshot_interval_ms({"subcarrier_spacing": 15000}) - 10.0) < 1e-9,
      "旧数据回退时15 kHz SCS的slot是1 ms")
check(abs(sysm.snapshot_interval_ms({"sample_interval_s": 0.0005,
                                     "srs_periodicity": 80}) - 0.5) < 1e-9,
      "显式0.5 ms快照不再被80-slot SRS周期覆盖")
check(abs(sysm.SystemConfig().snapshot_update_ms - 5.0) < 1e-9,
      "默认值就是算出来的那个，不是拍脑袋的 10.0")

# ---------------------------------------------------------------------------
sect("11  2026-08-07 自审修掉的三个口径 bug")
# ---------------------------------------------------------------------------
import inspect as _insp  # noqa: E402

_src = _insp.getsource(expm.simulate_experience)

# --- bug A：重传必须保留实发 MCS，等效低档只能作为 BLER lookup_mcs ---
check("harq_retransmission_bler" in _src
      and "mcs, sinr, combining=harq_combining" in _src,
      "重传以冻结的实发 MCS 调预置 CC/IR 抽象")
check('_bler_lookup(m, sinr, "retx")' not in _src
      and 'get_curve(int(mcs), "retx")' not in _src,
      "legacy 系统路径不再直接消费原始 ReTx 曲线")

# --- bug D：预置 BLER 源曲线是 0.05 dB 网格，缓存不能粗化成 0.5 dB ---
# MCS15 在 14.24 dB 的细网格值是 14.25 dB/2.53%，旧 0.5 dB 路径会取
# 14.0 dB/13.2%，ACK/NACK 概率差五倍，不是可忽略的性能优化。
_x_bler = 14.24
_curve15 = bc.get_curve(15, "newtx")
_want_bler = float(_curve15.evaluate(14.25)[0])
_coarse_bler = float(_curve15.evaluate(14.0)[0])
check(abs(sysm._bler_lookup(15, _x_bler) - _want_bler) < 1e-12,
      "legacy 系统 BLER 缓存保持预置曲线 0.05 dB 分辨率")
check(abs(expm._bler_lookup(15, _x_bler) - _want_bler) < 1e-12,
      "experience_v2 BLER 缓存保持预置曲线 0.05 dB 分辨率")
check(abs(_coarse_bler - _want_bler) > 0.05,
      "反向哨兵：旧 0.5 dB 量化在瀑布区确会造成显著概率偏差")
check(expm._bler_lookup(15, float("nan")) == 1.0
      and expm._bler_lookup(15, float("-inf")) == 1.0,
      "experience_v2 把 NaN/-Inf SINR 判为不可发送")
check(abs(expm._bler_lookup(15, float("inf"))
          - sysm._bler_lookup(15, float("inf"))) < 1e-12
      and expm._bler_lookup(15, float("inf")) < 1.0,
      "+Inf SINR 与建表相一致钳到预置曲线高 SINR 尾部")

# --- bug B：S 时隙的 RE 与 dl_ratio 必须用同一个系数 ---
check(abs(sysm.S_SLOT_DL_FRACTION - 0.7) < 1e-9, "S 时隙折合系数 0.7")
_slot_lut = expm.TbsLookup.build(17, 16, sysm.S_SLOT_DL_FRACTION)
check(int(_slot_lut.values[1, 12, 1, -1])
      < int(_slot_lut.values[0, 12, 1, -1]),
      "TBS 按 D/S 时隙各自的可用 RE 计算，不是所有时隙一个数")
_dd = sysm.SystemConfig(tdd_pattern="DDDD").dl_ratio
_ds = sysm.SystemConfig(tdd_pattern="DDDS").dl_ratio
check(abs(_dd - 1.0) < 1e-9 and abs(_ds - (3 + 0.7) / 4) < 1e-9,
      f"dl_ratio 用同一个常量（DDDD={_dd:.3f}, DDDS={_ds:.3f}）")
check(abs(sysm.infer_s_slot_fraction("DDDSU") - 10 / 14) < 1e-12,
      "DDDSU 的 S 时隙建议值来自 10/14 个下行符号")
check(abs(sysm.infer_s_slot_fraction("DDDDDDDSUU") - 6 / 14) < 1e-12,
      "DDDDDDDSUU 的 S 时隙建议值来自 6/14 个下行符号")
_ds_custom = sysm.SystemConfig(
    tdd_pattern="DDDS", s_slot_dl_fraction=0.82).dl_ratio
check(abs(_ds_custom - (3 + 0.82) / 4) < 1e-12,
      "dl_ratio 读取 SystemConfig.s_slot_dl_fraction，不再写死 0.7")
# 纯 D 与含 S 的图案，实发字节必须有可分辨的差——否则说明 S 还是被当成满下行
_tb_s = fake_tables(n_ue=6, n_snap=6, seed=17)
_rd = sysm.simulate(_tb_s, sys_cfg=sysm.SystemConfig(duration_s=1.0, tdd_pattern="DDDD"),
                    traffic=sysm.TrafficConfig(model="full_buffer"),
                    kpi=sysm.KpiConfig(warmup_s=0.0))
_rs = sysm.simulate(_tb_s, sys_cfg=sysm.SystemConfig(duration_s=1.0, tdd_pattern="SSSS"),
                    traffic=sysm.TrafficConfig(model="full_buffer"),
                    kpi=sysm.KpiConfig(warmup_s=0.0))
# S 时隙折算是可配置的（PR #23）：换成 0.82 后承载应当按比例跟着变。
_rs82 = sysm.simulate(
    _tb_s,
    sys_cfg=sysm.SystemConfig(
        duration_s=1.0, tdd_pattern="SSSS", s_slot_dl_fraction=0.82),
    traffic=sysm.TrafficConfig(model="full_buffer"),
    kpi=sysm.KpiConfig(warmup_s=0.0))
_bd = _rd.as_dict()["cell"]["cell_served_mbps"]
_bs = _rs.as_dict()["cell"]["cell_served_mbps"]
_bs82 = _rs82.as_dict()["cell"]["cell_served_mbps"]
# **承载之比不等于 s_slot_dl_fraction 本身。** DM-RS 与 PDCCH 是每时隙固定
# 开销，不随下行符号数缩水：S 时隙的符号数按该系数折算，固定开销却照扣一份，
# 于是可用 RE 之比比系数更小（0.7 → 78/126 = 0.619，0.82 → 102/126 = 0.810）。
# 期望值直接从口径本身算出来，不写死成常数——换 DM-RS/PDCCH 参数时这两条断言
# 应该跟着走，而不是需要人来改数字。
_oh_ds = sysm.SystemConfig().pdsch_overhead


def _expect_s_over_d(fraction: float) -> float:
    return _oh_ds.re_per_prb("S", fraction) / _oh_ds.re_per_prb("D")


_expect_ds = _expect_s_over_d(sysm.S_SLOT_DL_FRACTION)
_expect_ds82 = _expect_s_over_d(0.82)
print(f"  全 D {_bd:.1f} Mbps vs 全 S {_bs:.1f} Mbps，比值 "
      f"{_bs / max(_bd, 1e-9):.3f}（RE 口径预期 {_expect_ds:.3f}）；"
      f"系数 0.82 时 {_bs82 / max(_bd, 1e-9):.3f}（预期 {_expect_ds82:.3f}）")
check(abs(_expect_ds - 78.0 / 126.0) < 1e-9
      and abs(_expect_ds82 - 102.0 / 126.0) < 1e-9,
      f"默认口径下 S/D 每 PRB 的 RE 之比：0.7→78/126、0.82→102/126"
      f"（实得 {_expect_ds:.4f} / {_expect_ds82:.4f}）")
check(abs(_bs / max(_bd, 1e-9) - _expect_ds) < 0.06,
      f"全 S 图案的吞吐约为全 D 的 {_expect_ds:.3f} 倍"
      f"（实得 {_bs / max(_bd, 1e-9):.3f}）")
check(abs(_bs82 / max(_bd, 1e-9) - _expect_ds82) < 0.06,
      f"自定义系数 0.82 后承载约为全 D 的 {_expect_ds82:.3f} 倍"
      f"（实得 {_bs82 / max(_bd, 1e-9):.3f}）——系数确实被主循环读到了")

# --- bug C：p_idle_tti / expected_prb_util 这类"对标锚点不驱动仿真"的旋钮，
# 随 bimodal 一起下线。空闲 TTI 由到达率与信道决定，如实测出来。
check(not hasattr(sysm.TrafficConfig(), "p_idle_tti")
      and not hasattr(sysm.TrafficConfig(), "expected_prb_util"),
      "不驱动仿真的 p_idle_tti / expected_prb_util 已随 bimodal 下线")
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
_r_iot = sysm.simulate(_tb_iot, sys_cfg=sysm.SystemConfig(duration_s=1.0),
                    kpi=sysm.KpiConfig(warmup_s=0.0))
_d_iot = _r_iot.as_dict()
check(_d_iot["cell"]["iot_sample_valid_share"] < 0.9
      and _d_iot["cell"]["iot_valid_ue_share"] > 0.9,
      "小区级两个口径同时报出来，差异可见")
_ntxt = "".join(_d_iot["notes"])
check("IoT 不可信" in _ntxt, "触发的是「IoT 不可信」而不是「检查站间距」")
check("检查是不是站间距太大" not in _ntxt, "误导性的那条被抑制了")


# ---------------------------------------------------------------------------
sect("12  experience_v2：DRB busy-period、按需 RBG 与 Rel-19 小 burst")
# ---------------------------------------------------------------------------

# --- TBS 表：D/S × 28 MCS × rank1..4 × 17 RBG，反查必须给最小够用值 ---
_lut = expm.TbsLookup.build(17, 16, sysm.S_SLOT_DL_FRACTION)
_olla_point = SimpleNamespace(
    sinr_tx_rbg_db=np.full((1, 1, 17), 14.0),
    sinr_rbg_db=np.full((1, 1, 17), 14.0),
)
_olla_values = expm._frequency_su_values(
    table=_olla_point, snap=0, rank=1, indices=(0,),
    olla_db=1.0, olla_enabled=True, lookup=_lut, slot="D")
check(_olla_values["mcs_without_olla"]
      == la.select_mcs(14.0, table=3, target_bler=0.1).index
      and _olla_values["mcs"]
      == _olla_values["mcs_without_olla"] + 1
      and _olla_values["mcs"] > _olla_values["mcs_without_olla"],
      "SU 先按预置 BLER 表+基准 SINR 得到基准 MCS，再叠加 MCS-domain OLLA")
_olla_sinr = np.full((8, 1), 14.2)
_olla_mcs = la.select_mcs(14.2, table=3, target_bler=0.1).index
_olla_mcs_grid = np.full((8, 1), _olla_mcs, dtype=int)
_olla_se = np.full((8, 1), la.MCS_TABLES[3][_olla_mcs].se)
_olla_link = sysm.UeLinkTable(
    ue=0, sinr_db=_olla_sinr, mcs=_olla_mcs_grid, se=_olla_se,
    best_rank=np.ones(8, dtype=int), best_se=_olla_se[:, 0],
    geo_sinr_db=14.2, outage=np.zeros(8, dtype=bool), iot_db=0.0, sir_db=40.0,
    sinr_tx_db=_olla_sinr.copy(), mcs_tx=_olla_mcs_grid.copy(),
    se_gnb=_olla_se.copy(), best_se_gnb=_olla_se[:, 0].copy())
_olla_closed_loop = sysm.simulate(
    [_olla_link],
    sys_cfg=sysm.SystemConfig(
        duration_s=1.0, tdd_pattern="D", seed=42),
    traffic=sysm.TrafficConfig(model="full_buffer"),
    sched=sysm.SchedulerConfig(mu_enabled=False),
    kpi=sysm.KpiConfig(warmup_s=0.2))
check(abs(_olla_closed_loop.cell["bler_first_tx"] - 0.1) < 0.02
      and _olla_closed_loop.cell["olla_convergence"][
          "all_active_modes_converged"] is True
      and _olla_closed_loop.cell["olla_domain"] == "continuous_mcs_index",
      "MCS-domain OLLA 真实 ACK/NACK 闭环在1秒内收敛到目标 10% BLER")
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
# 守的是"TBS 对 PRB 数**不是线性的**，不能用 bytes/bytes_per_rbg 反推 RBG 数"。
# 扣掉 DM-RS+PDCCH 之后每 PRB 从 144 RE 变成 126 RE，落点换了一格量化台阶，
# 偏差从 +1.119% 变成 -0.027%（符号变了，非线性本身还在）。
check(abs(_nonlinear + 0.00026790156531053544) < 1e-9,
      f"MCS12/rank2 的 17 RBG TBS 偏离线性外推 -0.027%（实得 {_nonlinear:.3%}）")
check(abs(_nonlinear) > 1e-5, "TBS 量化的非线性没有被口径变化抹平")

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
check(_q.active is None and len(_q.done) == 1 and _q.done[0].bytes_sent == 150,
      "buffer 重新变空时才结束 busy period，发送字节完整")
check(len(_q.done_items) == 2
      and expm.arrival_item_metrics(_q.done_items[0], 0.5, 10.0) == (1.0, 2.5, False)
      and expm.arrival_item_metrics(_q.done_items[1], 0.5, 10.0) == (1.5, 2.0, False),
      "busy period 内每个 FIFO 到达对象单独记录首调度等待、完成时延与 PDB")

# --- 28.552 large burst：首传起算，排除清空 buffer 的最后一段 ---
_bp = expm.BusyPeriod(start_tti=0, traffic_class="large", pdb_ms=10,
                      bytes_arrived=350, bytes_sent=350,
                      first_tx_tti=2, last_tx_tti=7, tx_attempts=3,
                      tx_events=[expm.TxEvent(2, 100, 100, 0),
                                 expm.TxEvent(4, 200, 200, 0),
                                 expm.TxEvent(7, 50, 100, 50)])
_bm = expm.burst_metrics(_bp, 0.5)
check(abs((_bm.throughput_mbps or 0) - 1.6) < 1e-9,
      f"large burst 用首传→倒数第二 ACK 的 300 B/1.5 ms（实得 {_bm.throughput_mbps} Mbps）")
check(abs((_bm.head_inclusive_throughput_mbps or 0) - 0.96) < 1e-9,
      "含头速率保持 300 B numerator 与去尾规则，只把 1.0 ms 首调度等待加进分母")
check(_bm.queue_wait_ms == 1.0 and _bm.completion_delay_ms == 4.0,
      "排队等待与 arrival→completion 时延单独上报，不混进标准吞吐")

# --- Rel-19 小 burst：有效时间按 payload/TBVol 折成 slot 的一部分 ---
_sp = expm.BusyPeriod(start_tti=3, traffic_class="small", pdb_ms=1,
                      bytes_arrived=250, bytes_sent=250,
                      first_tx_tti=3, last_tx_tti=3, tx_attempts=1,
                      tx_events=[expm.TxEvent(3, 250, 1000, 750)])
_sm = expm.burst_metrics(_sp, 0.5, "fractional_slot")
check(abs((_sm.throughput_mbps or 0) - 16.0) < 1e-9
      and _sm.head_inclusive_throughput_mbps == _sm.throughput_mbps
      and _sm.throughput_kind == "rel19_fractional_slot",
      f"小 burst 250/1000 TB 折成 0.125 ms，吞吐 16 Mbps（实得 {_sm.throughput_mbps}）")
_sp_wait = expm.BusyPeriod(
    start_tti=1, traffic_class="small", pdb_ms=10,
    bytes_arrived=250, bytes_sent=250, first_tx_tti=3, last_tx_tti=3,
    tx_attempts=1, tx_events=[expm.TxEvent(3, 250, 1000, 750)])
_sm_wait = expm.burst_metrics(_sp_wait, 0.5, "fractional_slot")
check(abs((_sm_wait.head_inclusive_throughput_mbps or 0) - (2000 / 0.001125 / 1e6)) < 1e-9,
      "单 TB 小包含头速率 = payload / (首包时延 + fractional-slot airtime)")
check(expm.burst_metrics(_sp, 0.5, "exclude").throughput_mbps is None,
      "可显式保留旧式 exclude 口径，但不再声称小 burst 永远不可测")

# queue-wait 在 first TX 发生时已经是完整观测，不应等对象完成才保留。
_incomplete_started = expm.ArrivalItem(
    arrival_tti=2, total_bytes=1_000, remaining_bytes=500,
    first_tx_tti=6, completion_tti=-1)
_iwait, _icomp, _imiss = expm.arrival_item_metrics(
    _incomplete_started, 0.5, 20.0)
check(_iwait == 2.0 and _icomp is None and _imiss is None,
      "未完成但已首传的 arrival 保留精确 queue-wait，不被完成条件删样")

# --- 真正跑 experience_v2：一 TTI 多 UE、实际 RBG、scheduled-TBS PF、守恒 ---
_ex_cfg = sysm.SystemConfig(duration_s=0.8,
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
_trace = _ex.diagnostics["tti_trace"]
_trace_grants = [grant for row in _trace["rows"] for grant in row["grants"]]
check(_trace["schema"] == "superran_tti_trace_v1"
      and _trace["mode"] == "sampled"
      and 0 < len(_trace["rows"]) <= 256,
      "默认 TTI trace 有界采样，不把完整 5 秒轨迹塞进每个 replication")
check(any("uniform" in row["sample_reasons"] for row in _trace["rows"])
      and all(row["tti"] >= 0 for row in _trace["rows"]),
      "TTI trace 含跨算法可对齐的均匀锚点并保留绝对 TTI")
check(bool(_trace_grants)
      and all("harq_random_draw" in grant
              and "su_olla_after_mcs" in grant
              and "pf_average_after_bytes" in row
              for row in _trace["rows"] for grant in row["grants"]),
      "单 TTI 证据足以复盘 BLER 抽签、OLLA 前后与 PF 更新后状态")
check(_ex.diagnostics["rbg_overlap_violations"] == 0
      and _ex.diagnostics["max_rbg_in_any_tti"] <= 17,
      "同 TTI RBG 不重叠且总分配不超过 17")
check(_ex.cell["multi_ue_tti_share"] > 0,
      f"按需分配后同一 TTI 确实能服务多个 UE（占比 {_ex.cell['multi_ue_tti_share']:.1%}）")
check(_ex.cell["accounting_error_pct"] < 1e-9,
      f"experience 字节守恒：arrived=acked+queued（误差 {_ex.cell['accounting_error_pct']}%）")
check(_ex.cell["measurement_accounting_error_pct"] < 1e-9,
      "测量窗口独立守恒：start backlog + arrivals = ACK + end backlog")
check(_ex.cell["small_burst_fractional_mbps"] is not None,
      "单 TTI 小 burst 用 Rel-19 fractional-slot KPI 得到可测样本")
check(_ex.cell["small_queue_wait_ms_p95"] is not None
      and _ex.cell["completed_arrival_objects"] > 0
      and _ex.cell["class_arrival_kpis"]["small"]["is_small"],
      "小包 FIFO 到达对象的等待/PDB 与 DRB busy-period 吞吐分层统计")
check(_ex.cell["first_packet_delay_ms_p95"] == _ex.cell["arrival_queue_wait_ms_p95"]
      and _ex.cell["first_packet_delay_observed_share"]
      == _ex.cell["queue_wait_observed_share"],
      "首包时延复用每个 arrival object 的生成→首次调度事件，并保留右删失覆盖率")
check(_ex.cell["drb_throughput_head_inclusive_mbps"]
      <= _ex.cell["drb_throughput_rel19_mbps"] + 1e-12,
      "含头速率只扩大 denominator，因此逐汇总不高于对应掐头去尾速率")
check(0.0 <= _ex.cell["queue_wait_observed_share"] <= 1.0
      and "queue_wait_observation" in _ex.diagnostics,
      "queue-wait 报告已观测/从未调度右删失覆盖率，不把删样藏起来")
check("small 到达对象等待 P95" in _ex.text(),
      "experience_v2 单次结果摘要使用体验模式字段，不访问 legacy 专属字段")
check(_ex.cell["resource_utilization"] <= _ex.cell["occupancy"] + 1e-12,
      "resource utilization 与 busy-TTI occupancy 分开，不用 padding 伪装满带")
check(_ex.cell["serving_cell_prb_utilization"] == _ex.cell["resource_utilization"],
      "本小区 PRB 利用率用明确新名称输出，旧 resource_utilization 仅作兼容别名")
_tti_dist = _ex.cell["tti_occupied_rbg_distribution"]
check(len(_tti_dist["bins"]) == 18
      and sum(b["tti_count"] for b in _tti_dist["bins"]) == _ex.cell["dl_tti"]
      and abs(sum(b["tti_share"] for b in _tti_dist["bins"]) - 1.0) < 1e-12,
      "逐 TTI RBG 分布覆盖 0..17，包含 idle，并与测量窗全部 DL 调度机会严格对账")
check(_ex.cell["actual_rbg_size_hist"]["scope"]
      == "nonzero_grant_size_not_tti_total",
      "旧 RBG histogram 明示为 per-grant，禁止冒充逐 TTI 小区占用分布")
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

# --- HARQ 行为级回归：冻结 TB 身份、只重传一次、D/S 类型不串 -------
_orig_newtx_lookup = expm._bler_lookup
_orig_retx_lookup = la.harq_retransmission_bler
expm._bler_lookup = lambda _mcs, _sinr: 1.0


def _forced_failed_retx(mcs, sinr, *, combining="ir", table=3):
    return {
        "bler": 1.0, "lookup_mcs": max(0, int(mcs) - 1),
        "lookup_sinr_db": float(sinr), "combining": combining,
    }


la.harq_retransmission_bler = _forced_failed_retx
try:
    _harq_run = sysm.simulate(
        _T[:1],
        sys_cfg=sysm.SystemConfig(
            duration_s=0.03,
            seed=9041, tdd_pattern="DS", harq_combining="ir"),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(
            mu_enabled=False, olla_enabled=False, pf_accounting="auto"),
        kpi=sysm.KpiConfig(warmup_tti=0),
    )
finally:
    expm._bler_lookup = _orig_newtx_lookup
    la.harq_retransmission_bler = _orig_retx_lookup

_harq_rows = _harq_run.diagnostics["allocation_sample"]
_harq_new = {a["tti"]: a for a in _harq_rows
             if a["harq_tx_mode"] == "newtx"}
_harq_retx = [a for a in _harq_rows if a["harq_tx_mode"] == "retx"]
check(bool(_harq_retx), "强制 NACK 轨迹确实产生 HARQ 重传")
check(all(
    a["original_tb_tti"] in _harq_new
    and (a["mcs"], a["n_rbg"], a["rank"], a["scheduled_bytes"])
    == (_harq_new[a["original_tb_tti"]]["mcs"],
        _harq_new[a["original_tb_tti"]]["n_rbg"],
        _harq_new[a["original_tb_tti"]]["rank"],
        _harq_new[a["original_tb_tti"]]["scheduled_bytes"])
    and a["slot"] == _harq_new[a["original_tb_tti"]]["slot"]
    and a["plan_selected_reason"] == "HARQ_retx_priority"
    for a in _harq_retx),
    "重传逐 TB 保持 MCS/RBG 数/rank/TBS 与 D/S 类型，并强制 SU 优先")
check(all(sum(1 for row in _harq_rows
              if row["original_tb_tti"] == first_tti) == 2
          for first_tti in (a["tti"] for a in _harq_new.values())
          if any(r["original_tb_tti"] == first_tti for r in _harq_retx)),
      "每个获得重传机会的原 TB 恰有初传+一次重传，不存在第二次重传")
check(_harq_run.cell["retx_bler"] == 1.0
      and _harq_run.cell["residual_bler"] == 1.0,
      "强制重传失败轨迹的重传 BLER 与右删失后残留 BLER 都精确为 1")

# 预置曲线机制点：MCS20 / 16 dB 位于初传失败、CC 瀑布、IR 低档尾部。
_harq_point = sysm.UeLinkTable(
    ue=0,
    sinr_db=np.array([[16.0]]),
    mcs=np.array([[20]], dtype=int),
    se=np.array([[la.MCS_TABLE_3[20].se]]),
    best_rank=np.array([1], dtype=int),
    best_se=np.array([la.MCS_TABLE_3[20].se]),
    geo_sinr_db=16.0,
    outage=np.array([False]),
    mcs_table=3,
    target_bler=0.1,
)
_harq_real: dict[str, sysm.SystemResult] = {}
for _combining in ("cc", "ir"):
    _harq_real[_combining] = sysm.simulate(
        [_harq_point],
        sys_cfg=sysm.SystemConfig(
            duration_s=1.0,
            tdd_pattern="D", seed=230823, harq_combining=_combining),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
        kpi=sysm.KpiConfig(warmup_tti=0),
    )
# **IR 的好处不再体现在 served bytes 上。** 现场速率统计口径是"发送即计入、
# 不看这个 TB 对不对"，所以两个合并方案的 cell_served_mbps 逐值相同
# （都是 184.424）——它们发出去的首传一样多。IR 更好体现在**残留误块**上：
# 重传失败率 0.0 vs 0.025。拿吞吐去比 IR/CC 在新口径下是个空断言。
check(_harq_real["ir"].cell["retx_bler"]
      < _harq_real["cc"].cell["retx_bler"]
      and _harq_real["ir"].cell["residual_bler"]
      < _harq_real["cc"].cell["residual_bler"],
      "真实 MCS20/16 dB 系统轨迹满足 IR 重传 BLER < CC，残留误块也更低")
check(abs(_harq_real["ir"].cell["cell_served_mbps"]
          - _harq_real["cc"].cell["cell_served_mbps"]) < 1e-9,
      "两个合并方案的已发送字节逐值相同——发送即计入，与 TB 对错无关")

# 服务小区 PRB 利用率是内生 KPI：full-buffer 必须 100%，无到达必须 0%。
_load_cfg = sysm.SystemConfig(
    duration_s=0.02, seed=410, tdd_pattern="DDDSU")
_load_sched = sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False)
_load_kpi = sysm.KpiConfig(warmup_tti=0)
_full_load = sysm.simulate(
    _T, sys_cfg=_load_cfg, traffic=sysm.TrafficConfig(model="full_buffer"),
    sched=_load_sched, kpi=_load_kpi)
_idle_load = sysm.simulate(
    _T, sys_cfg=_load_cfg,
    traffic=sysm.TrafficConfig(
        model="mixed", small_ue_share=1.0,
        small_arrival_rate_hz=0.0, arrival_rate_hz=0.0),
    sched=_load_sched, kpi=_load_kpi)
check(abs(_full_load.cell["serving_cell_prb_utilization"] - 1.0) < 1e-12
      and _full_load.cell["tti_occupied_rbg_distribution"]["bins"][17]["tti_share"] == 1.0,
      "full-buffer 占满每个可用 DL TTI，服务小区 PRB 利用率和 17-RBG 桶都为 100%")
check(_idle_load.cell["serving_cell_prb_utilization"] == 0.0
      and _idle_load.cell["tti_occupied_rbg_distribution"]["bins"][0]["tti_share"] == 1.0,
      "无到达时 PRB 利用率为 0，全部 DL TTI 落入 0-RBG idle 桶")

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

# 两 UE 饱和反向哨兵：宽松主场景的 P95 会卡在 0.5 ms 地板，不能据此误判
# PF 记账没有影响。固定链路、固定 CRN、关闭 OLLA 后，唯一自变量就是 RU 更新口径。
_pf_sentinel_tables: list[sysm.UeLinkTable] = []
for _u in range(2):
    _sinr = np.full((2, 1), 15.0)
    _mcs = np.full((2, 1), 12, dtype=int)
    _se = np.full((2, 1), la.MCS_TABLES[3][12].se)
    _pf_sentinel_tables.append(sysm.UeLinkTable(
        ue=_u, sinr_db=_sinr, mcs=_mcs, se=_se,
        best_rank=np.ones(2, dtype=int), best_se=_se[:, 0],
        geo_sinr_db=15.0, outage=np.zeros(2, dtype=bool),
        iot_db=3.0, sir_db=12.0, se_gnb=_se.copy(),
        best_se_gnb=_se[:, 0].copy()))
_pf_sentinel_cfg = sysm.SystemConfig(
    duration_s=1.0,
    # This counterexample isolates PF accounting.  Use the explicit no-delay
    # control so the single-HARQ-process wait window is not a second variable.
    tdd_pattern="D", harq_feedback_delay=False, seed=9)
_pf_sentinel_traffic = sysm.TrafficConfig(
    model="mixed", small_ue_share=0.5,
    small_file_bytes=1500, small_arrival_rate_hz=1500.0,
    file_bytes=500_000, arrival_rate_hz=20.0)
_pf_sentinel_common = dict(
    sys_cfg=_pf_sentinel_cfg, traffic=_pf_sentinel_traffic,
    kpi=sysm.KpiConfig(warmup_tti=0))
_pf_correct = sysm.simulate(
    _pf_sentinel_tables,
    sched=sysm.SchedulerConfig(
        algorithm="pf", mu_enabled=False, olla_enabled=False,
        pf_accounting="scheduled_tbs"),
    rng=rg.RngBook(123, 0), **_pf_sentinel_common)
_pf_wrong = sysm.simulate(
    _pf_sentinel_tables,
    sched=sysm.SchedulerConfig(
        algorithm="pf", mu_enabled=False, olla_enabled=False,
        pf_accounting="legacy_fullband"),
    rng=rg.RngBook(123, 0), **_pf_sentinel_common)
check((_pf_wrong.cell["small_queue_wait_ms_mean"]
       - _pf_correct.cell["small_queue_wait_ms_mean"]) > 0.5
      and (_pf_wrong.cell["small_queue_wait_ms_p95"]
           > _pf_correct.cell["small_queue_wait_ms_p95"])
      and (_pf_wrong.cell["small_immediate_service_ratio"]
           < _pf_correct.cell["small_immediate_service_ratio"]),
      "反向哨兵：全带 PF 误记账显著拉长小包等待并降低到达 TTI 即时服务率")

# **只有一条路径**：model_version 不再随模式变，容量仿真也是 experience_v2。
check(sysm.SystemConfig().as_dict()["model_version"] == "experience_v2",
      "唯一评估路径，model_version 恒为 experience_v2")
check("evaluation_mode" not in sysm.SystemConfig().as_dict(),
      "评估模式开关已彻底删除，结果里也不再出现")
check(sysm.SystemConfig().power_constraint == "nebf",
      "系统/TDD 默认空间约束为每天线 P/M 且用满总功率的 NEBF")
try:
    sysm.simulate(
        _T, sys_cfg=sysm.SystemConfig(power_constraint="pebf"),
        sched=sysm.SchedulerConfig(mu_enabled=False))
    check(False, "链路表与系统功率约束不一致应硬失败")
except ValueError as _e:
    check("功率约束" in str(_e) and "不一致" in str(_e),
          "capacity/experience 都拒绝错配的 EBF/PEBF/NEBF 链路表")
# --- 数据受限 SU/MU 自适应：PF 先排序，方案比较后才执行 ---
_mu_rg = np.random.default_rng(23)
_mu_h = [((_mu_rg.standard_normal((4, 32, 16, 2))
           + 1j * _mu_rg.standard_normal((4, 32, 16, 2))) / np.sqrt(2))
         for _ in range(4)]
_mu_tables = sysm.build_link_tables(
    _mu_h, [15.0] * 4, num_snapshots=4, rb_per_rbg=16,
    csi=sysm.ca.CsiConfig(enabled=False), max_rank=2, mu_enabled=True)
_pair = _mu_tables[0].mu_links[1]
_recon = np.column_stack((_mu_tables[0].sinr_db[:, 1],
                          _mu_tables[1].sinr_db[:, 1])) \
    + _pair.power_loss_db + _pair.corr_loss_true_db
check(np.allclose(_recon, _pair.true_sinr_db, atol=1e-10),
      "MU true SINR 可逐点重构为 SU SINR + powerLoss + CorrLoss")
check(abs(_pair.power_loss_db + 10 * np.log10(2)) < 1e-12,
      "两个 rank2 UE 相对 SU rank2 的功率损失精确为 -3.0103 dB")
check("P/4" in _pair.as_dict()["power_loss_scope"],
      "MU powerLoss 输出显式限定为 rank2+rank2 等功率分流口径")
check(_pair.as_dict()["receiver"] == "per_user_lmmse",
      "MU pair 表显式记录逐用户 LMMSE 接收机，不再冒充固定标量接收基")

# 鲁棒 RZF 必须真正贯通系统建表，而不是只存在于 mumimo 单元函数。
_rzf_tables = sysm.build_link_tables(
    _mu_h, [15.0] * 4, num_snapshots=4, rb_per_rbg=16,
    csi=sysm.ca.CsiConfig(enabled=False), max_rank=2, mu_enabled=True,
    mu_precoder="rzf", mu_csi_error_variance=0.03)
_rzf_pair = _rzf_tables[0].mu_links[1]
_rzf_diag = _rzf_pair.as_dict()
check(_rzf_diag["precoder"] == "rzf"
      and abs(_rzf_diag["csi_error_variance"] - 0.03) < 1e-15
      and len(_rzf_diag["rzf_regularization"]) == 4,
      "系统建表把 RZF 与 CSI 误差方差传到每个 MU 快照并保留诊断")
check(all(abs(x["csi_error_loading"] - 16 * 0.03) < 1e-12
          for x in _rzf_diag["rzf_regularization"]),
      "鲁棒 RZF 的 CSI 加载逐快照精确为 N_BS·sigma_e²")
check(sysm.SchedulerConfig(
          mu_precoder="rzf", mu_csi_error_variance=0.03).as_dict()[
              "mu_csi_error_variance"] == 0.03,
      "系统配置与结果合同显式携带鲁棒 RZF 误差方差")
_mu_gate_cfg = sysm.SchedulerConfig()
check(_mu_gate_cfg.min_pairing_mcs == 4
      and _mu_gate_cfg.pf_gain_threshold == 0.0
      and _mu_gate_cfg.orthogonalization_mode == "select",
      "MU 门限默认值显式进入 SchedulerConfig；PF 门默认关闭以保兼容")
try:
    sysm.SchedulerConfig(mu_csi_error_variance=-1e-3)
    check(False, "负 CSI 误差方差应硬失败")
except ValueError as _e:
    check("mu_csi_error_variance" in str(_e),
          "负 CSI 误差方差在启动仿真前硬失败")

_mu_cfg = sysm.SystemConfig(duration_s=0.5,
                            seed=51, tdd_pattern="DDDSU")
_large = sysm.simulate(
    _mu_tables, sys_cfg=_mu_cfg,
    traffic=sysm.TrafficConfig(model="mixed", small_ue_share=0.0,
                               file_bytes=500_000, arrival_rate_hz=20.0),
    sched=sysm.SchedulerConfig(mu_enabled=True, max_mu_users=2,
                               mu_corr_threshold=0.99),
    kpi=sysm.KpiConfig(warmup_tti=0))
check(_large.cell["mu_share"] > 0
      and _large.cell["su_mu_plan"]["mu_selected"] > 0,
      "大队列下数据受限 MU 方案确实被选中，不再用固定 MU 增益比例")
_mcs_blocked = sysm.simulate(
    _mu_tables,
    sys_cfg=sysm.SystemConfig(
        duration_s=0.1, seed=51, tdd_pattern="DDDSU"),
    traffic=sysm.TrafficConfig(
        model="mixed", small_ue_share=0.0,
        file_bytes=500_000, arrival_rate_hz=20.0),
    sched=sysm.SchedulerConfig(
        mu_enabled=True, max_mu_users=2, mu_corr_threshold=0.99,
        min_pairing_mcs=28),
    kpi=sysm.KpiConfig(warmup_tti=0))
check(_mcs_blocked.cell["mu_share"] == 0
      and (_mcs_blocked.cell["mu_candidate_scoring"]["rejection_reasons"].get(
          "mcs_below_min_pairing", 0) > 0),
      "experience_v2 的最低 MCS 门在实际 pair 计划入口否决并留原因")
_pf_blocked = sysm.simulate(
    _mu_tables,
    sys_cfg=sysm.SystemConfig(
        duration_s=0.1, seed=51, tdd_pattern="DDDSU"),
    traffic=sysm.TrafficConfig(
        model="mixed", small_ue_share=0.0,
        file_bytes=500_000, arrival_rate_hz=20.0),
    sched=sysm.SchedulerConfig(
        mu_enabled=True, max_mu_users=2, mu_corr_threshold=0.99,
        min_pairing_mcs=0, pf_gain_threshold=100.0),
    kpi=sysm.KpiConfig(warmup_tti=0))
check(_pf_blocked.cell["mu_share"] == 0
      and _pf_blocked.cell["su_mu_plan"]["pf_gain_rejects"] > 0,
      "experience_v2 的 PF 增益否决读取真实 R_avg，不用和速率冒充")
check(0 < _large.cell["mu_paired_prb_utilization"]
      <= _large.cell["serving_cell_prb_utilization"] + 1e-12
      and 0 < _large.cell["mu_paired_prb_share_of_used"] <= 1.0
      and _large.cell["mu_paired_prb_equivalent"] > 0,
      "MU 同时报配对 PRB 原始等效量、占全部可用 PRB 比例和占已用 PRB 比例")
check(abs(_large.cell["mu_paired_prb_utilization"]
          - _large.cell["serving_cell_prb_utilization"]
          * _large.cell["mu_paired_prb_share_of_used"]) < 1e-12,
      "MU 配对绝对占用 = 本小区 PRB 利用率 × 已用资源中的 MU 配对比例")
_mu_alloc = [a for a in _large.diagnostics["allocation_sample"]
             if a["transmission_mode"] == "MU"]
check(bool(_mu_alloc) and _large.diagnostics["rbg_overlap_violations"] == 0,
      "MU 两个 TB 共享同一物理 RBG group，资源账只扣一次且无非法重叠")
_grp: dict[int, list[dict]] = {}
for _a in _mu_alloc:
    _grp.setdefault(int(_a["mu_group_id"]), []).append(_a)
_full_groups = [v for v in _grp.values() if len(v) == 2]
check(bool(_full_groups) and all(
    x[0]["rbg_indices"] == x[1]["rbg_indices"] for x in _full_groups),
    "同一 MU group 的两用户使用完全相同的 RBG bitmap")
check(all(a["pf_credit_bytes"] == a["scheduled_bytes"] for a in _mu_alloc),
      "MU PF-RU 同样按本 UE 实际 scheduled TBS 更新，不按全带或和速率记账")
_a0 = _mu_alloc[0]
_formula_sinr = (_a0["base_tx_sinr_db"]
                 + _a0["corr_loss_db"] + _a0["power_loss_db"])
_formula_base_mcs = la.select_mcs(
    _formula_sinr, table=3, target_bler=0.1).index
_formula_final_mcs = la.apply_olla_mcs(
    _formula_base_mcs,
    _a0["su_olla_before_mcs"] + _a0["mu_olla_before_mcs"],
    mcs_table=3)["final_mcs"]
check(_formula_base_mcs == _a0["mcs_without_olla"]
      and _formula_final_mcs == _a0["mcs"],
      "MU 先在 SINR 域加 CorrLoss/powerLoss 反折基准 MCS，再叠加 SU/MU OLLA")
_lookup = expm.TbsLookup.build(17, 16)
_snap = 0
_ordered = list(range(len(_mu_tables)))
_rank_of = {u: int(_mu_tables[u].best_rank[_snap]) for u in _ordered}
_mcs_of = {u: int(_mu_tables[u].mcs_tx[_snap, _rank_of[u] - 1]) for u in _ordered}
_base_of = {u: float(_mu_tables[u].sinr_tx_db[_snap, _rank_of[u] - 1])
            for u in _ordered}
_true_of = {u: float(_mu_tables[u].sinr_db[_snap, _rank_of[u] - 1])
            for u in _ordered}
_potential_of = {u: _lookup.tbs_bytes("D", _mcs_of[u], _rank_of[u], 17)
                 for u in _ordered}
_mixed_queue_plan = expm._build_mu_plan(
    _ordered[:2], queue_bytes={0: 1_000, 1: 500_000}, lookup=_lookup,
    slot="D", num_rbg=17, rank_of=_rank_of, mcs_of=_mcs_of,
    base_tx_sinr_of=_base_of, mcs_without_olla_of=_mcs_of,
    true_sinr_of=_true_of, potential_of=_potential_of,
    tables=_mu_tables, snap=_snap,
    sched=sysm.SchedulerConfig(
        mu_enabled=True, mu_corr_threshold=1.0, min_pairing_mcs=0),
    su_olla_db=np.zeros(len(_ordered)),
    mu_olla_db=np.zeros(len(_ordered)), blocked_data=False)
_mixed_mu = next(
    grant for grant in _mixed_queue_plan.grants if grant.mode == "MU")
check(_mixed_mu.n_rbg == min(max(_mixed_mu.required_rbg), 17)
      and _mixed_mu.useful_bytes[0] == 1_000,
      "小包+大包MU共享bitmap持续到两者都满足或资源耗尽，不在小包先完成时遗留RBG")
_mu_frequency_rg = np.random.default_rng(2323)
_mu_frequency_h = [
    ((_mu_frequency_rg.standard_normal((2, 272, 8, 2))
      + 1j * _mu_frequency_rg.standard_normal((2, 272, 8, 2))) / np.sqrt(2))
    for _ in range(2)
]
_mu_frequency_tables = sysm.build_link_tables(
    _mu_frequency_h, [15.0, 14.0], num_snapshots=2, rb_per_rbg=16,
    csi=sysm.ca.CsiConfig(enabled=False), max_rank=2, mu_enabled=True)
_mu_frequency_order = [0, 1]
_mu_frequency_rank = {
    u: int(_mu_frequency_tables[u].best_rank[0]) for u in _mu_frequency_order}
_mu_frequency_mcs = {
    u: int(_mu_frequency_tables[u].mcs_tx[0, _mu_frequency_rank[u] - 1])
    for u in _mu_frequency_order}
_mu_frequency_base = {
    u: float(_mu_frequency_tables[u].sinr_tx_db[0, _mu_frequency_rank[u] - 1])
    for u in _mu_frequency_order}
_mu_frequency_true = {
    u: float(_mu_frequency_tables[u].sinr_db[0, _mu_frequency_rank[u] - 1])
    for u in _mu_frequency_order}
_mu_frequency_potential = {
    u: _lookup.tbs_bytes(
        "D", _mu_frequency_mcs[u], _mu_frequency_rank[u], 17)
    for u in _mu_frequency_order}
_mixed_frequency_plan = expm._build_mu_plan(
    _mu_frequency_order, queue_bytes={0: 1_000, 1: 500_000}, lookup=_lookup,
    slot="D", num_rbg=17, rank_of=_mu_frequency_rank,
    mcs_of=_mu_frequency_mcs, base_tx_sinr_of=_mu_frequency_base,
    mcs_without_olla_of=_mu_frequency_mcs,
    true_sinr_of=_mu_frequency_true,
    potential_of=_mu_frequency_potential,
    tables=_mu_frequency_tables, snap=0,
    sched=sysm.SchedulerConfig(mu_enabled=True, mu_corr_threshold=1.0),
    su_olla_db=np.zeros(2), mu_olla_db=np.zeros(2), blocked_data=False,
    frequency_aware=True)
_mixed_frequency_mu = next(
    grant for grant in _mixed_frequency_plan.grants if grant.mode == "MU")
check(_mixed_frequency_mu.n_rbg > min(_mixed_frequency_mu.required_rbg)
      and _mixed_frequency_mu.useful_bytes[0] == 1_000
      and _mixed_frequency_mu.useful_bytes[1] > 1_000,
      "频选MU的all条件同样持续分配共享bitmap，不因小包先满足而提前停止")

_serviceable_su = expm._build_su_plan(
    _ordered[:2], queue_bytes={0: 100, 1: 100}, lookup=_lookup,
    slot="D", num_rbg=17, rank_of=_rank_of, mcs_of=_mcs_of,
    base_tx_sinr_of=_base_of, mcs_without_olla_of=_mcs_of,
    true_sinr_of=_true_of, potential_of=_potential_of,
    blocked_data=True, cursor=0, tables=_mu_tables, snap=_snap,
    su_olla_db=np.zeros(len(_ordered)), olla_enabled=False,
    frequency_aware=False)
check(_serviceable_su.useful_bytes == 200
      and _serviceable_su.clears_all_queues,
      "系统别处有outage/错slot backlog时，SU清空全部可服务队列仍必须触发强制SU")

_audit_scores = np.asarray([30.0] + [-20.0] * 16)[None, None, :]
_audit_tables = [
    SimpleNamespace(
        sinr_tx_rbg_db=_audit_scores.copy(),
        sinr_rbg_db=_audit_scores.copy(),
        sinr_tx_db=np.asarray([[0.0]]), sinr_db=np.asarray([[0.0]]))
    for _ in range(2)
]
# 队列长度是**场景参数**：要落在"整条载波刚好装得下、剩余池装不下"的窗口里。
# 扣开销后满带 TBS 从 2112 B 降到 1857 B，1_900 已经越过上沿，窗口整体下移。
_audit_plan = expm._build_su_plan(
    [0, 1], queue_bytes={0: 1_700, 1: 1_700}, lookup=_lookup,
    slot="D", num_rbg=17, rank_of={0: 1, 1: 1}, mcs_of={0: 10, 1: 10},
    base_tx_sinr_of={0: 0.0, 1: 0.0},
    mcs_without_olla_of={0: 10, 1: 10},
    true_sinr_of={0: 0.0, 1: 0.0},
    potential_of={
        0: _lookup.tbs_bytes("D", 10, 1, 17),
        1: _lookup.tbs_bytes("D", 10, 1, 17)},
    blocked_data=False, cursor=0, tables=_audit_tables, snap=0,
    su_olla_db=np.zeros(2), olla_enabled=False, frequency_aware=True)
_audit_second = _audit_plan.grants[1]
check(_audit_second.fits_in_fullband == (True,)
      and _audit_second.fits_in_remaining_pool == (False,)
      and _audit_second.required_rbg == (1,)
      and _audit_second.required_rbg_from_remaining_pool == (16,)
      and _audit_second.potential_fullband_bytes[0] >= 1_700,
      "频选审计拆清完整载波池与当前剩余池，不再让fits_in_fullband冒充remaining")
_floor_plan = expm._build_mu_plan(
    _ordered, queue_bytes={u: 500_000 for u in _ordered}, lookup=_lookup,
    slot="D", num_rbg=17, rank_of=_rank_of, mcs_of=_mcs_of,
    base_tx_sinr_of=_base_of, mcs_without_olla_of=_mcs_of,
    true_sinr_of=_true_of, potential_of=_potential_of,
    tables=_mu_tables, snap=_snap,
    sched=sysm.SchedulerConfig(
        mu_enabled=True, mu_corr_threshold=1.0, min_pairing_mcs=0),
    su_olla_db=np.full(len(_ordered), -100.0),
    mu_olla_db=np.zeros(len(_ordered)), blocked_data=False)
check(_floor_plan.has_mu,
      "MCS-domain 负 OLLA 只降低发送档位，不伪造更低的物理 SINR")
_unusable_tables = deepcopy(_mu_tables)
for _table in _unusable_tables:
    if _table.sinr_tx_db is not None:
        _table.sinr_tx_db[:] = -30.0
_unusable_base = {
    u: float(_unusable_tables[u].sinr_tx_db[_snap, _rank_of[u] - 1])
    for u in _ordered
}
_unusable_plan = expm._build_mu_plan(
    _ordered, queue_bytes={u: 500_000 for u in _ordered}, lookup=_lookup,
    slot="D", num_rbg=17, rank_of=_rank_of, mcs_of=_mcs_of,
    base_tx_sinr_of=_unusable_base, mcs_without_olla_of=_mcs_of,
    true_sinr_of=_true_of, potential_of=_potential_of,
    tables=_unusable_tables, snap=_snap,
    sched=sysm.SchedulerConfig(mu_enabled=True, mu_corr_threshold=1.0),
    su_olla_db=np.full(len(_ordered), -100.0),
    mu_olla_db=np.zeros(len(_ordered)), blocked_data=False)
check(not _unusable_plan.has_mu,
      "真实发送侧 SINR 下 MCS0 仍超过 50% BLER 时才判 pair 不可用")
_olla_state = _large.diagnostics["olla_state_final"]
check(_olla_state["su_db"] != _olla_state["mu_db"]
      and "not pair-specific" in _olla_state["scope"],
      "SU/MU OLLA 是两组独立用户级状态，不按配对关系拆分")
_target_cfg = sysm.SystemConfig(
    duration_s=0.02, seed=151, tdd_pattern="DDDSU")
_target_run = sysm.simulate(
    _mu_tables, sys_cfg=_target_cfg,
    traffic=sysm.TrafficConfig(model="mixed", small_ue_share=0.0,
                               file_bytes=500_000, arrival_rate_hz=100.0),
    sched=sysm.SchedulerConfig(
        mu_enabled=True, mu_corr_threshold=1.0,
        olla_step_up_db=0.01, olla_step_down_db=0.09,
        mu_olla_step_up_db=0.02, mu_olla_step_down_db=0.08),
    kpi=sysm.KpiConfig(warmup_tti=0))
_targets = _target_run.cell["olla_convergence"]["target_bler_by_mode"]
check(abs(_targets["SU"] - 0.1) < 1e-12 and abs(_targets["MU"] - 0.2) < 1e-12,
      "OLLA 收敛门按 SU/MU 各自步长比检查，不拿 SU target 误判 MU")

_small = sysm.simulate(
    _mu_tables, sys_cfg=_mu_cfg,
    traffic=sysm.TrafficConfig(model="mixed", small_ue_share=1.0,
                               small_file_bytes=500, small_arrival_rate_hz=200.0),
    sched=sysm.SchedulerConfig(mu_enabled=True, olla_enabled=False,
                               max_mu_users=2, mu_corr_threshold=0.99),
    kpi=sysm.KpiConfig(warmup_tti=0))
check(_small.cell["mu_share"] == 0
      and _small.cell["su_mu_plan"]["su_forced_all_queues_clear"] > 0,
      "SU 能在本 TTI 清完全部队列时强制 SU，尾部 RBG 留空")
check(sysm.KpiConfig().resolve_warmup_tti(0.5) == 2000,
      "默认预启动 1 s 在 30 kHz SCS 下精确换算为 2000 TTI")
_warm_cfg = sysm.SystemConfig(duration_s=1.2,
                              seed=52, tdd_pattern="DDDSU")
_warm = sysm.simulate(
    _mu_tables, sys_cfg=_warm_cfg,
    traffic=sysm.TrafficConfig(model="mixed", small_ue_share=1.0,
                               small_file_bytes=500, small_arrival_rate_hz=200.0),
    sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
    kpi=sysm.KpiConfig())
check(_warm.cell["measurement_start_s"] == 1.0
      and abs(_warm.cell["measurement_duration_s"] - 0.2) < 1e-12,
      "1.2 s 仿真默认前 1 s 只收敛状态，KPI 统计后 0.2 s")
check(_warm.cell["measurement_accounting_error_pct"] == 0.0
      and _warm.diagnostics["measurement_window"]["balance_error_bytes"] == 0,
      "预启动跨界 backlog 显式带入测量窗字节守恒，不混成窗内 offered load")
check(all(a["tti"] >= 2000 for a in _warm.diagnostics["allocation_sample"]),
      "吞吐/BLER/资源 allocation 样本全部从预启动结束后开始")
try:
    sysm.simulate(
        _T, sys_cfg=sysm.SystemConfig(duration_s=0.01),
        traffic=_ex_tr,
        sched=sysm.SchedulerConfig(
            mu_enabled=False, olla_step_up_db=0.0, olla_step_down_db=0.0),
        kpi=sysm.KpiConfig(warmup_tti=0))
    check(False, "零 OLLA 步长应被拒绝")
except ValueError as _e:
    check("olla_step_up_db" in str(_e), "OLLA 步长非正时硬失败，不产生零除或伪 target")

# ---------------------------------------------------------------------------
sect("13  配置入口：非法值硬失败，不静默钳位")


def _expect_value_error(factory, needle: str, label: str) -> None:
    try:
        factory()
        check(False, label)
    except ValueError as exc:
        check(needle in str(exc), f"{label}（{exc}）")


_expect_value_error(
    lambda: sysm.TrafficConfig(arrival_rate_hz=float("nan")),
    "arrival_rate_hz", "话务到达率 NaN 在配置入口被拒绝")
_expect_value_error(
    lambda: sysm.TrafficConfig(model="bimodal"),
    "cdf", "因果倒置的 bimodal 在配置入口被拒绝，并给出迁移路径")
_expect_value_error(
    lambda: sysm.TrafficConfig(small_priority=0),
    "small_priority", "业务优先级拒绝深层钳位为 1")
_expect_value_error(
    lambda: sysm.TrafficConfig(classes=(
        sysm.TrafficClassConfig(
            name="zero", ue_share=0.0, file_bytes=1, arrival_rate_hz=0.0),)),
    "ue_share 之和", "自定义业务类不能全部为零权重")
_expect_value_error(
    lambda: sysm.SchedulerConfig(pf_window_tti=1.5),
    "pf_window_tti", "PF 窗口拒绝非整数")
_expect_value_error(
    lambda: sysm.SchedulerConfig(qos_delay_exponent=float("inf")),
    "qos_delay_exponent", "QoS 指数拒绝无穷值")
_expect_value_error(
    lambda: sysm.SchedulerConfig(mu_rank_per_user=True),
    "mu_rank_per_user", "MU rank 拒绝布尔值冒充整数")
_expect_value_error(
    lambda: sysm.SchedulerConfig(min_pairing_mcs=-1),
    "min_pairing_mcs", "最低配对 MCS 拒绝负数")
_expect_value_error(
    lambda: sysm.SchedulerConfig(pf_gain_threshold=float("nan")),
    "pf_gain_threshold", "PF 增益门限拒绝 NaN")
try:
    sysm.SchedulerConfig(orthogonalization_mode="schmidt")
    check(False, "SchedulerConfig 不应把 schmidt 静默降级为 select")
except NotImplementedError as _exc:
    check("TODO" in str(_exc) and "Schmidt" in str(_exc),
          "SchedulerConfig 的 schmidt 未实现时显式硬失败")
_expect_value_error(
    lambda: sysm.KpiConfig(warmup_tti=-1),
    "warmup_tti", "预启动 TTI 拒绝负数")
_expect_value_error(
    lambda: sysm.KpiConfig().resolve_warmup_tti(float("nan")),
    "tti_ms", "预启动换算拒绝 NaN TTI")
_expect_value_error(
    lambda: sysm.SystemConfig(duration_s=float("nan")),
    "duration_s", "仿真时长拒绝 NaN")
_expect_value_error(
    lambda: sysm.SystemConfig(num_rbg=17.0),
    "num_rbg", "RBG 数拒绝浮点数冒充整数")
_expect_value_error(
    lambda: sysm.SystemConfig(tdd_pattern="DDDX"),
    "tdd_pattern", "TDD pattern 拒绝 D/S/U 之外的字符")
_expect_value_error(
    lambda: sysm.SystemConfig(tdd_pattern="UUU"),
    "至少需要一个 D 或 S", "下行仿真拒绝没有任何下行机会的 TDD pattern")
_expect_value_error(
    lambda: sysm.SystemConfig(harq_combining="magic"),
    "ir / cc", "HARQ 合并只接受 IR/CC")
_expect_value_error(
    lambda: sysm.SystemConfig(seed=-1),
    "seed", "系统随机种子拒绝负数")
_expect_value_error(
    lambda: sysm.NeighborLoadConfig(seed=-1),
    "seed", "邻区负载随机种子拒绝负数")
_expect_value_error(
    lambda: sysm.NeighborLoadConfig().realized(1.5),
    "n", "邻区负载样本数拒绝浮点数冒充整数")
_expect_value_error(
    lambda: sysm.apply_neighbor_load(10.0, 12.0, float("nan")),
    "utilization", "邻区利用率 NaN 不再传播到 SINR")
_expect_value_error(
    lambda: expm.TbsLookup.build(17.0, 16),
    "num_rbg", "TBS 表维度拒绝浮点数截断")
_expect_value_error(
    lambda: expm.TbsLookup.build(17, 16, float("nan")),
    "s_slot_fraction", "TBS S 时隙比例拒绝 NaN")
_expect_value_error(
    lambda: _lut.row("D", 12.5, 2),
    "MCS", "TBS MCS 索引拒绝浮点数截断")
_expect_value_error(
    lambda: _lut.tbs_bytes("D", 12, 2, 1.5),
    "n_rbg", "TBS RBG 索引拒绝浮点数截断")
_expect_value_error(
    lambda: _lut.required_rbg("D", 12, 2, 0),
    "payload_bytes", "TBS 反查拒绝零字节伪需求")
_expect_value_error(
    lambda: sysm.simulate_replications(_T, num_replications=1.5),
    "必须为整数", "多次仿真入口不再截断浮点重复次数")
_expect_value_error(
    lambda: sysm.simulate_replications(_T, num_replications=1, master_seed=1.5),
    "master_seed", "多次仿真入口不再截断浮点主种子")
_expect_value_error(
    lambda: sysm.simulate_replications(_T, num_replications=1,
                                       replication_start=-1),
    "replication", "多次仿真入口拒绝负数 RngRun 起点")
_expect_value_error(
    lambda: sysm.simulate_replications(
        _T, num_replications=1, replication_workers=0),
    "replication_workers", "重复实验进程数拒绝 0 与静默串行降级")
_expect_value_error(
    lambda: sysm.simulate_replications(
        _T, num_replications=1, replication_workers=2),
    "安全上限", "显式进程数超过重复数时硬失败而不是静默收口")

# ---------------------------------------------------------------------------
sect("14  CDF 话务、双标量、显式用户映射与资源归因")

with tempfile.TemporaryDirectory() as _tmp:
    _tmp_path = Path(_tmp)
    _size_cdf = _tmp_path / "packet-size.csv"
    _interval_cdf = _tmp_path / "packet-interval.csv"
    _size_cdf.write_text(
        "bytes,cdf\n100,50\n200,100\n", encoding="utf-8")
    _interval_cdf.write_text(
        "interval_ms,cdf\n2,0.5\n4,1.0\n", encoding="utf-8")
    _parsed = trafm.load_empirical_cdf(
        _size_cdf, kind="packet_size", value_unit="byte")
    check(_parsed.probability_input_scale == "percent"
          and _parsed.quantile(0.5) == 100.0
          and _parsed.mean == 150.0
          and len(_parsed.sha256) == 64,
          "value,cdf 支持百分数、逆查、离散均值与输入 SHA-256")
    _draw_a = _parsed.sample(np.random.default_rng(7), 64)
    _draw_b = _parsed.sample(np.random.default_rng(7), 64)
    check(np.array_equal(_draw_a, _draw_b), "CDF 逆变换在同种子下逐位可复现")

    _bad_cdf = _tmp_path / "bad.csv"
    _bad_cdf.write_text("value,cdf\n100,0.8\n90,1.0\n", encoding="utf-8")
    _expect_value_error(
        lambda: trafm.load_empirical_cdf(
            _bad_cdf, kind="packet_size", value_unit="byte"),
        "严格递增", "CDF value 倒序硬失败，不排序后假装输入正确")

    _base_cfg = sysm.TrafficConfig(
        model="cdf", packet_size_cdf=str(_size_cdf),
        interarrival_cdf=str(_interval_cdf))
    _half_size_cfg = sysm.TrafficConfig(
        model="cdf", packet_size_cdf=str(_size_cdf),
        interarrival_cdf=str(_interval_cdf), packet_size_scale=0.5)
    _base_tr = expm.ExperienceTraffic(
        _base_cfg, 2, 0.5, np.random.default_rng(123))
    _half_size_tr = expm.ExperienceTraffic(
        _half_size_cfg, 2, 0.5, np.random.default_rng(123))
    for _tti in range(80):
        _base_tr.step(_tti)
        _half_size_tr.step(_tti)
    _base_sizes = _base_tr.traffic_samples["packet_size_bytes"]
    _half_sizes = _half_size_tr.traffic_samples["packet_size_bytes"]
    check(len(_base_sizes) == len(_half_sizes) > 0
          and _half_sizes == [int(x * 0.5) for x in _base_sizes],
          "包长 scale=0.5 在相同基础抽样上逐包精确减半")

    _half_interval_cfg = sysm.TrafficConfig(
        model="cdf", packet_size_cdf=str(_size_cdf),
        interarrival_cdf=str(_interval_cdf), interarrival_scale=0.5)
    _base_interval_tr = expm.ExperienceTraffic(
        _base_cfg, 2, 0.5, np.random.default_rng(321))
    _half_interval_tr = expm.ExperienceTraffic(
        _half_interval_cfg, 2, 0.5, np.random.default_rng(321))
    for _tti in range(120):
        _base_interval_tr.step(_tti)
        _half_interval_tr.step(_tti)
    _base_intervals = _base_interval_tr.traffic_samples["interarrival_ms"]
    _half_intervals = _half_interval_tr.traffic_samples["interarrival_ms"]
    check(_half_interval_tr.arrival_events > _base_interval_tr.arrival_events
          and np.allclose(
              _half_intervals[:len(_base_intervals)],
              np.asarray(_base_intervals) * 0.5),
          "包间隔 scale=0.5 保持基础 interval 抽样并产生更密的到达")

    _profiles = (
        sysm.TrafficClassConfig(
            name="video", ue_share=0.0, file_bytes=100,
            arrival_rate_hz=0.0, packet_size_cdf=str(_size_cdf),
            interarrival_cdf=str(_interval_cdf), ue_ids=(0,)),
        sysm.TrafficClassConfig(
            name="xr", ue_share=0.0, file_bytes=100,
            arrival_rate_hz=0.0, packet_size_cdf=str(_size_cdf),
            interarrival_cdf=str(_interval_cdf), ue_ids=(1,), is_small=True),
    )
    _profile_cfg = sysm.TrafficConfig(model="cdf", classes=_profiles)
    _profile_tr = expm.ExperienceTraffic(
        _profile_cfg, 2, 0.5, np.random.default_rng(8))
    check([q.traffic_class.name for q in _profile_tr.queues] == ["video", "xr"],
          "ue_ids 可显式绑定多话务 profile；全覆盖时允许 ue_share 全为 0")
    _expect_value_error(
        lambda: expm.ExperienceTraffic(
            _profile_cfg, 1, 0.5, np.random.default_rng(8)),
        "只有 0..0", "profile 指向越界 UE 时在生成前硬失败")

    _cdf_run = sysm.simulate(
        _T[:2],
        sys_cfg=sysm.SystemConfig(
            duration_s=0.08, tdd_pattern="D"),
        traffic=_profile_cfg,
        sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
        kpi=sysm.KpiConfig(warmup_tti=0))
    _user_attr = sum(
        float(row["allocated_prb_equivalent_attributed"])
        for row in _cdf_run.users)
    check(abs(_user_attr - _cdf_run.cell["allocated_prb_equivalent"]) < 1e-9,
          "用户 attributed PRB 跨 UE 求和严格等于小区已用 PRB")
    check(_cdf_run.diagnostics["traffic_profiles"][0]["packet_size_cdf"]["sha256"]
          == _parsed.sha256
          and _cdf_run.diagnostics["traffic_samples"]["packet_size_bytes"],
          "结果保留 CDF 来源哈希、profile 摘要与抽样前缀供审计")

    _cdf_rep = sysm.simulate_replications(
        _T[:2], num_replications=2, master_seed=9,
        sys_cfg=sysm.SystemConfig(
            duration_s=0.08, tdd_pattern="D"),
        traffic=_profile_cfg,
        sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
        kpi=sysm.KpiConfig(warmup_tti=0))
    check(all(isinstance(row["allocated_prb_equivalent_attributed"], dict)
              for row in _cdf_rep.users)
          and "traffic_samples" in _cdf_rep.as_dict(),
          "用户新增 KPI 自动跨 replication 汇总，CDF 证据进入序列化结果")
    _offset_rep = sysm.simulate_replications(
        _T[:2], num_replications=1, master_seed=9, replication_start=5,
        sys_cfg=sysm.SystemConfig(
            duration_s=0.04, tdd_pattern="D"),
        traffic=_profile_cfg,
        sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
        kpi=sysm.KpiConfig(warmup_tti=0))
    check(_offset_rep.as_dict()["replications"] == [5]
          and _offset_rep.config["rng"]["replication"] == "5..5",
          "simulate_replications 可选择不重叠的 RngRun 起点且完整回传")

    _calibration_cfg = sysm.TrafficConfig(
        model="cdf", packet_size_cdf=str(_size_cdf),
        interarrival_cdf=str(_interval_cdf), packet_size_scale=20.0)
    _calibration = sysm.calibrate_traffic_to_prb(
        _T[:2], target_prb_utilization=0.30, tolerance=0.05,
        max_iterations=7, probe_replications=2, num_replications=4,
        master_seed=9,
        sys_cfg=sysm.SystemConfig(
            duration_s=0.3, tdd_pattern="D"),
        traffic=_calibration_cfg,
        sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
        kpi=sysm.KpiConfig(warmup_tti=0))
    _cal_dict = _calibration.as_dict()
    check(_calibration.status == "target_met"
          and abs(_cal_dict["achieved_prb_utilization"] - 0.30) <= 0.05,
          "双标量控制器最终以正式仿真实测值落入 30%±5% PRB 目标")
    check(len(_calibration.history) >= 2
          and _calibration.history[0]["offered_load_factor_vs_input"] == 1.0
          and "ci95" in _calibration.history[0]
          and "common_random_numbers" in _cal_dict,
          "校准保留初始点、每轮置信区间、负载倍率与公共随机数证据")
    check(not set(_cal_dict["probe_replication_ids"])
          & set(_cal_dict["formal_replication_ids"])
          and _cal_dict["formal_replication_ids"]
          == _calibration.result.as_dict()["replications"],
          "probe 与正式反馈使用同 master seed 下互不重叠的 RngRun")
    # 话务开到最大（full_buffer）没有可调的负载标量，双标量校准无从谈起。
    _expect_value_error(
        lambda: sysm.calibrate_traffic_to_prb(
            _T[:2], target_prb_utilization=0.30,
            sys_cfg=sysm.SystemConfig(),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            sched=sysm.SchedulerConfig(mu_enabled=False)),
        "不支持双标量 PRB 校准",
        "容量口径（full_buffer）不允许把目标 PRB 话务校准混进来")
    for _invalid_target in (0.0, 1.0, float("nan")):
        _expect_value_error(
            lambda _target=_invalid_target: sysm.calibrate_traffic_to_prb(
                _T[:2], target_prb_utilization=_target,
                sys_cfg=sysm.SystemConfig(
                    duration_s=0.05),
                traffic=_calibration_cfg,
                sched=sysm.SchedulerConfig(mu_enabled=False)),
            "必须是 (0,1)",
            f"目标 PRB 利用率 {_invalid_target!r} 在启动仿真前硬失败")
    _expect_value_error(
        lambda: sysm.calibrate_traffic_to_prb(
            _T[:2], target_prb_utilization=0.30, formal_refinements=-1,
            sys_cfg=sysm.SystemConfig(
                duration_s=0.05),
            traffic=_calibration_cfg,
            sched=sysm.SchedulerConfig(mu_enabled=False)),
        "formal_refinements 必须是非负整数",
        "正式反馈轮数拒绝负数而不是静默钳到 0")
    _expect_value_error(
        lambda: expm.ExperienceTraffic(
            sysm.TrafficConfig(
                model="cdf",
                packet_size_cdf=str(_tmp_path / "missing-size.csv"),
                interarrival_cdf=str(_interval_cdf)),
            1, 0.5, np.random.default_rng(1)),
        "CDF 文件不存在",
        "缺失 CDF 在生成到达前硬失败并返回解析后的绝对路径")
    _expect_value_error(
        lambda: sysm.TrafficConfig(
            model="cdf",
            classes=(
                sysm.TrafficClassConfig(
                    name="a", ue_share=0.0, file_bytes=100,
                    arrival_rate_hz=0.0, packet_size_cdf=str(_size_cdf),
                    interarrival_cdf=str(_interval_cdf), ue_ids=(0,)),
                sysm.TrafficClassConfig(
                    name="b", ue_share=0.0, file_bytes=100,
                    arrival_rate_hz=0.0, packet_size_cdf=str(_size_cdf),
                    interarrival_cdf=str(_interval_cdf), ue_ids=(0,)),
            )),
        "被重复分配",
        "一个 UE 不能同时绑定两个 traffic profile")
    _expect_value_error(
        lambda: sysm.TrafficConfig(
            model="cdf",
            classes=(sysm.TrafficClassConfig.from_dict({
                "name": "bad-share", "ue_share": "0.5",
                "file_bytes": 100, "arrival_rate_hz": 0.0,
                "packet_size_cdf": str(_size_cdf),
                "interarrival_cdf": str(_interval_cdf),
            }),)),
        "ue_share 必须有限非负",
        "嵌套 profile 的字符串数值返回 ValueError，不泄漏 TypeError")
    _expect_value_error(
        lambda: sysm.TrafficConfig(
            model="cdf",
            classes=(sysm.TrafficClassConfig.from_dict({
                "name": "bad-bool", "ue_share": 1.0,
                "file_bytes": 100, "arrival_rate_hz": 0.0,
                "is_small": "false",
                "packet_size_cdf": str(_size_cdf),
                "interarrival_cdf": str(_interval_cdf),
            }),)),
        "is_small 必须是布尔值",
        "字符串 false 不会被 Python 真值规则误当成小包 profile")
    _expect_value_error(
        lambda: sysm.TrafficConfig(model="cdf",
                                   packet_size_cdf=str(_size_cdf),
                                   interarrival_cdf=str(_interval_cdf),
                                   packet_size_scale=True),
        "packet_size_scale 必须是有限正数",
        "布尔值不能冒充全局话务缩放标量")

# ---------------------------------------------------------------------------
sect("15  载波栅格与 MCS 表口径必须跟着链路表走")
# 主循环早先写死 table=3 / target_bler=0.1。经 MCP 走默认值恰好对得上，
# 直接调 Python API 时就会出现"rank 按 A 判据选、MCS 按 B 判据选"，
# 而这种错配在结果里没有任何症状——只能靠入口断言拦。
_rng15 = np.random.default_rng(1508)
_h15 = [((_rng15.standard_normal((4, 51, 4, 4))
          + 1j * _rng15.standard_normal((4, 51, 4, 4))) / np.sqrt(2))
        for _ in range(3)]
_grid15 = cgrid.CarrierGrid.from_config(
    {"subcarrier_spacing": 30_000}, num_rb=51)
_t15 = sysm.build_link_tables(
    _h15, [18.0] * 3, num_ues=3, max_rank=2,
    table=3, target_bler=0.1, rb_per_rbg=_grid15.nominal_rb_per_rbg,
    rbg_boundaries=_grid15.boundaries)
check(abs(_t15[0].target_bler - 0.1) < 1e-12,
      "target_bler 随链路表带出，主循环不必自己猜")
check(int(_t15[0].mcs_table) == 3, "mcs_table 随链路表带出")
# 38.214 Configuration 2: 51 RB 的 P=8，6 个整组 + 3 RB 尾组全部参与。
check(_t15[0].sinr_rbg_db is not None and _t15[0].sinr_rbg_db.shape[2] == 7,
      f"51 RB -> 7 个 RBG（实得 {_t15[0].sinr_rbg_db.shape[2]}）")
check(_grid15.rbg_prb_sizes == (8, 8, 8, 8, 8, 8, 3),
      f"51 RB 尾组 3 RB 不丢弃（实得 {_grid15.rbg_prb_sizes}）")
_cfg15 = sysm.SystemConfig(duration_s=0.1,
                           num_rbg=7, rb_per_rbg=8, scs_khz=30,
                           rbg_prb_sizes=_grid15.rbg_prb_sizes)
_kpi15 = sysm.KpiConfig(warmup_s=0.0)
_ok15 = sysm.simulate(_t15, sys_cfg=_cfg15, kpi=_kpi15,
                      traffic=sysm.TrafficConfig(model="full_buffer"))
check(_ok15.cell["cell_served_mbps"] > 0, "与带宽一致的配置能正常跑完")
_t15[1].mcs_table = 1
_expect_value_error(
    lambda: sysm.simulate(_t15, sys_cfg=_cfg15, kpi=_kpi15,
                          traffic=sysm.TrafficConfig(model="full_buffer")),
    "MCS 表 / 目标 BLER 在 UE 之间不一致",
    "各 UE 的 MCS 表不一致时必须硬失败，不能一半用表 1 一半用表 3")
_t15[1].mcs_table = 3
_lookup10 = expm.TbsLookup.build(17, 16, target_bler=0.1)
_lookup20 = expm.TbsLookup.build(17, 16, target_bler=0.2)
check(expm._select_mcs(-2.78, _lookup10) == 0
      and expm._select_mcs(-2.78, _lookup20) == 1,
      "experience MCS 真正读取链路 target_bler：同一 SINR 下 10%→MCS0、20%→MCS1")

# ---------------------------------------------------------------------------
# --- N1 回归：simulate_experience 直调 + 默认 SchedulerConfig 不得崩溃 --------
# 默认 olla_step_down_db=None 的合同是"留空 = 按链路表 target_bler 自动反解"。
# 公开入口必须自己兑现，不能指望调用方先解析（旧版在参数校验处 float(None)
# 直接 TypeError，HEAD 之外任何直调都踩）。
_sys_cfg_exp = sysm.SystemConfig(duration_s=0.1,
                                 num_rbg=7, rb_per_rbg=8, scs_khz=30,
                                 rbg_prb_sizes=_grid15.rbg_prb_sizes)
_exp_run = expm.simulate_experience(
    _t15, sys_cfg=_sys_cfg_exp, traffic_cfg=sysm.TrafficConfig(model="full_buffer"),
    sched=sysm.SchedulerConfig(mu_enabled=False), kpi=sysm.KpiConfig(warmup_s=0.0),
    book=rg.RngBook(0, 0))
check(_exp_run is not None,
      "simulate_experience 直调默认 SchedulerConfig 不再 float(None) 崩溃")
_sched_src = sysm.SchedulerConfig().resolved_for_target(0.1).as_dict()
check(_sched_src["olla_down_source"] == "auto_from_target_bler"
      and sysm.SchedulerConfig(olla_step_down_db=0.09)
      .resolved_for_target(0.1).as_dict()["olla_down_source"]
      == "explicit_user_override",
      "OLLA down 步长来源在 Python API 结果里可区分（自动反解 vs 显式覆盖）")

# ---------------------------------------------------------------------------
sect("16  多算法 KPI 工作台：CRN、Holm 与 TTI 钻取")
check(kpi_view._json_ready(np.array([1.0, np.nan])) == [1.0, None]
      and kcmp._json_ready(np.array([1, 2], dtype=np.int64)) == [1, 2],
      "KPI JSON 导出保留 ndarray 结构并把非有限值显式写成 null，不退化成字符串")
_old_kpi_root = kpi_view.artifacts_root
_old_compare_root = kcmp.artifacts_root
with tempfile.TemporaryDirectory() as _compare_tmp:
    _compare_root = Path(_compare_tmp)
    kpi_view.artifacts_root = lambda: _compare_root
    kcmp.artifacts_root = lambda: _compare_root
    try:
        _books16 = rg.replications(20260823, 8)
        _base16 = np.arange(100.0, 108.0)

        def _comparison_result(label: str, scheduler: str, shift: float) -> dict:
            _values = _base16 + shift
            _user_values = _values / 10.0
            return {
                "dataset_id": "synthetic-compare-contract",
                "analysis_identity": {
                    "prereg_id": "pr_system_compare_test",
                    "digest": "fixed-test-digest",
                    "primary_metric": "cell_experienced_mbps",
                    "baseline": "经典 PF",
                },
                "algorithm": {"label": label, "scheduler": scheduler},
                "config": {
                    "system": {
                        "model_version": "experience_v2",
                        "duration_s": 0.8,
                        "tti_ms": 0.5,
                        "tdd_pattern": "DDDSU",
                        "num_rb": 272,
                        "num_rbg": 17,
                        "rb_per_rbg": 16,
                    },
                    "traffic": {"model": "mixed", "arrival_rate_hz": 2.0},
                    "kpi": {"warmup_s": 0.0, "tti_trace_mode": "sampled",
                            "tti_trace_max_points": 256},
                    "scheduler": {"algorithm": scheduler},
                },
                "cell": {
                    "cell_experienced_mbps": rg.summarize(
                        _values, "cell_experienced_mbps").as_dict(),
                    "first_packet_delay_ms_p95": rg.summarize(
                        30.0 - _values / 10.0, "first_packet_delay_ms_p95").as_dict(),
                },
                "users": [{
                    "ue": 0,
                    "traffic_class": "video",
                    "experienced_mbps": rg.summarize(
                        _user_values, "experienced_mbps").as_dict(),
                }],
                "n_rep": 8,
                "replications": list(range(8)),
                "comparison_evidence": {
                    "schema": "superran_system_comparison_evidence_v1",
                    "rng_books": [book.as_dict() for book in _books16],
                    "cell_samples_by_replication": {
                        "cell_experienced_mbps": _values.tolist(),
                        "first_packet_delay_ms_p95": (30.0 - _values / 10.0).tolist(),
                    },
                },
                "tti_trace": deepcopy(_trace),
                "notes": [],
            }

        _reports16 = []
        for _label, _scheduler16, _shift16 in (
            ("经典 PF", "pf", 0.0),
            ("候选 QoS-PF", "qos_pf", 10.0),
            ("候选 Max-C/I", "max_ci", 20.0),
        ):
            _reports16.append(kpi_view.write_kpi_report(
                _comparison_result(_label, _scheduler16, _shift16), serve=False))
        _comparison16 = kcmp.build_comparison(
            [report["result_id"] for report in _reports16],
            baseline_result_id=_reports16[0]["result_id"],
        )
        check(len(_comparison16["arms"]) == 3
              and _comparison16["fairness"]["pairable"],
              "2..5 个算法结果按同一 RngRun 硬校验后进入同一比较合同")
        check(all(row["holm_reject"] and row["publishable_winner"]
                  for row in _comparison16["comparisons"]
                  ["cell_experienced_mbps"].values()),
              "主 KPI 的两个候选先过 Gate 3，再通过 Holm 家族校正")
        check(all(arm["trace"].get("rows") for arm in _comparison16["arms"]),
              "每个算法臂都带同一 TTI 可钻取的 sampled trace")
        check(kcmp._holm_rejections({"a": 0.03, "b": 0.04})
              == {"a": False, "b": False},
              "Holm 首项不过时后续全部拒绝发布，不挑单个显著候选")
        check(all(Path(report["result_json_path"]).is_file()
                  for report in _reports16),
              "单臂 KPI 页面同步落严格 JSON sidecar，供后续对比工具按句柄读取")
        _five_reports16 = list(_reports16)
        for _label, _scheduler16, _shift16 in (
            ("候选 4", "pf", 30.0), ("候选 5", "pf", 40.0),
        ):
            _five_reports16.append(kpi_view.write_kpi_report(
                _comparison_result(_label, _scheduler16, _shift16), serve=False))
        check(len(kcmp.build_comparison(
            [report["result_id"] for report in _five_reports16])
            ["arms"]) == 5,
              "工作台真实接受基线 + 4 个候选，共 5 个算法臂")
        _expect_value_error(
            lambda: kcmp.build_comparison(
                [report["result_id"] for report in _five_reports16] + ["sixth"]),
            "2..5",
            "超过 5 个算法时硬拒绝，避免颜色、表格和多重比较失控")
        _unfair16 = _comparison_result("错配话务候选", "pf", 5.0)
        _unfair16["config"]["traffic"]["arrival_rate_hz"] = 9.0
        _unfair_report16 = kpi_view.write_kpi_report(_unfair16, serve=False)
        _expect_value_error(
            lambda: kcmp.build_comparison([
                _reports16[0]["result_id"], _unfair_report16["result_id"]]),
            "不可公平比较",
            "话务或 KPI 口径错配时拒绝生成漂亮但无意义的对比页")
    finally:
        kpi_view.artifacts_root = _old_kpi_root
        kcmp.artifacts_root = _old_compare_root


# ---------------------------------------------------------------------------
sect("17  下行 AMC 链：rank 策略、HARQ 反馈时序、决策坐标与解码 SINR")

from superran import amc_policy as ap  # noqa: E402

# --- 17.1 HARQ 反馈偏移完全由 TDD 图案决定 --------------------------------
check(ap.feedback_effective_offsets("DDDSU") == (5, 4, 3, 2, 6),
      "DDDSU：D/D/D/S 发送的反馈分别在 5/4/3/2 个 TTI 后生效")
check(ap.feedback_effective_offsets("DDDSUDDDSU")
      == (5, 4, 3, 2, 6, 5, 4, 3, 2, 6),
      "图案重复两遍（8 下行 : 2 上行）得到同一组偏移")
check(ap.feedback_effective_offsets("D") == (1,)
      and ap.feedback_effective_offsets("DS") == (1, 1),
      "纯下行图案没有上行承载反馈，退化成零时延并由 notes 说明")
for _bad in ("", "X", "UU"):
    try:
        ap.feedback_effective_offsets(_bad)
        check(False, f"非法图案 {_bad!r} 应当被拒")
    except ValueError:
        check(True, f"非法图案 {_bad!r} 被拒，不静默给一组偏移")

_fb_cfg = sysm.SystemConfig(duration_s=0.05,
                            tdd_pattern="DDDSU", seed=17)
check(_fb_cfg.as_dict()["harq_feedback_offsets_tti"] == [5, 4, 3, 2, 6]
      and _fb_cfg.as_dict()["harq_feedback_delay"] is True,
      "反馈偏移与开关随 SystemConfig 一起进结果合同")

# --- 17.2 OLLA 关闭只去掉叠加，不换决策坐标 --------------------------------
# 真值坐标与 AMC 预测坐标故意拉开 12 dB：如果关掉 OLLA 会掉回真值坐标，
# 选出来的 MCS 会明显不同，这条就会红。
_amc_table = sysm.UeLinkTable(
    ue=0,
    sinr_db=np.full((1, 2), 22.0),
    mcs=np.array([[la.select_mcs(22.0, table=3, target_bler=0.1).index] * 2],
                 dtype=int),
    se=np.full((1, 2), 1.0),
    best_rank=np.array([1], dtype=int),
    best_se=np.array([1.0]),
    geo_sinr_db=22.0,
    outage=np.array([False]),
    sinr_tx_db=np.full((1, 2), 10.0),
    sinr_rbg_db=np.full((1, 2, 17), 22.0),
    sinr_tx_rbg_db=np.full((1, 2, 17), 10.0),
    mcs_table=3, target_bler=0.1,
)
_amc_runs = {}
for _olla in (True, False):
    _amc_runs[_olla] = sysm.simulate(
        [_amc_table],
        sys_cfg=sysm.SystemConfig(duration_s=0.02,
                                  tdd_pattern="D", seed=5),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(
            mu_enabled=False, olla_enabled=_olla,
            rank=ap.RankConfig(fixed_rank=1)),
        kpi=sysm.KpiConfig(warmup_tti=0, tti_trace_mode="off"),
    )
_amc_rows = {
    k: v.diagnostics["allocation_sample"][0] for k, v in _amc_runs.items()}
_amc_expect = la.select_mcs(10.0, table=3, target_bler=0.1).index
check(_amc_rows[False]["mcs"] == _amc_expect
      and _amc_rows[False]["mcs_without_olla"] == _amc_expect,
      f"关掉 OLLA 后发送档仍由 CQI+BF 坐标决定（MCS{_amc_expect}）")
check(_amc_rows[False]["base_tx_sinr_db"] == 10.0
      and _amc_rows[True]["base_tx_sinr_db"] == 10.0,
      "两种配置的决策坐标都是 sinr_tx_db，不是真实接收 SINR")
check(_amc_rows[False]["mcs"]
      != la.select_mcs(22.0, table=3, target_bler=0.1).index,
      "关掉 OLLA 不会退回用真实接收 SINR 反折 MCS 的上帝视角")
check(_amc_rows[True]["olla_before_db"] == 0.0
      and _amc_rows[True]["mcs"] == _amc_expect,
      "开启 OLLA 时首个 TTI 偏置为 0，与关闭 OLLA 给出同一档")

# --- 17.2a 单 HARQ 进程：ACK 也必须等反馈，rank 不能提前看 NACK ---------
# 强制所有首传 ACK。DDDSU 的 t0 反馈到 t5 才生效，所以 10 TTI 内同一 UE
# 只能在 t0/t5 发两次；旧实现只为 NACK 建 pending，会在 8 个 D/S 全部连发。
# **capacity 现在默认 8 进程**，这里显式钉成 1 —— 单进程仍是受支持的配置，
# 这一节守的就是它的时序合同。多进程的合同在 17.2c。experience 侧本来就
# 只有一个槽位，harq_max_processes 对它无效。
_old_system_bler = sysm._bler_lookup
_old_experience_bler = expm._bler_lookup
_feedback_calls: dict[str, list[int]] = {"capacity": [], "experience": []}
_old_record_feedback = ap.RankController.record_feedback
_feedback_mode = "capacity"


def _spy_feedback(self, ue, **kwargs):
    value = kwargs.get("feedback_tti")
    if value is not None:
        _feedback_calls[_feedback_mode].append(int(value))
    return _old_record_feedback(self, ue, **kwargs)


try:
    sysm._bler_lookup = lambda _mcs, _sinr: 0.0
    expm._bler_lookup = lambda _mcs, _sinr: 0.0
    ap.RankController.record_feedback = _spy_feedback
    # 反馈时序由 amc_policy 统一实现；只剩一条评估路径，跑一次即可。
    _feedback_mode = "experience"
    _feedback_runs = {_feedback_mode: sysm.simulate(
        [_amc_table],
        sys_cfg=sysm.SystemConfig(
            duration_s=0.005,
            tdd_pattern="DDDSU", harq_feedback_delay=True, seed=650,
            harq_max_processes=1),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(
            mu_enabled=False, olla_enabled=True,
            rank=ap.RankConfig(fixed_rank=1)),
        kpi=sysm.KpiConfig(warmup_tti=0, tti_trace_mode="full"),
    )}
finally:
    sysm._bler_lookup = _old_system_bler
    expm._bler_lookup = _old_experience_bler
    ap.RankController.record_feedback = _old_record_feedback

for _feedback_mode, _run_feedback in _feedback_runs.items():
    check(_run_feedback.cell["scheduled_tti"] == 2
          and _run_feedback.cell["harq_feedback_wait_skips"] == 6,
          f"{_feedback_mode}：max_processes=1 时 ACK 也占住进程，"
          "DDDSU 只在 t0/t5 发送")
    check(_feedback_calls[_feedback_mode] == [5],
          f"{_feedback_mode}：首个 ACK 只在反馈到达 t5 交给 OLLA/rank，不在 t0 偷看")

# 重传 ACK/NACK 也必须等终次反馈才释放单进程，但终次反馈不再进入首传
# OLLA/rank 学习。强制 t0 首传 NACK、t5 重传，下一份新 TB 只能在 t10 发。
_terminal_runs = {}
_terminal_feedback_calls = {}
_old_terminal_system_bler = sysm._bler_lookup
_old_terminal_experience_bler = expm._bler_lookup
_old_terminal_retx_bler = la.harq_retransmission_bler
_old_terminal_record = ap.RankController.record_feedback
_terminal_key = ""


def _terminal_spy(self, ue, **kwargs):
    if kwargs.get("feedback_tti") is not None:
        _terminal_feedback_calls.setdefault(_terminal_key, []).append(
            int(kwargs["feedback_tti"]))
    return _old_terminal_record(self, ue, **kwargs)


try:
    sysm._bler_lookup = lambda _mcs, _sinr: 1.0
    expm._bler_lookup = lambda _mcs, _sinr: 1.0
    ap.RankController.record_feedback = _terminal_spy
    for _terminal_ack in (True, False):
        _terminal_bler = 0.0 if _terminal_ack else 1.0

        def _terminal_retx(mcs, sinr, *, combining="ir", table=3,
                           _bler=_terminal_bler):
            return {
                "bler": float(_bler), "lookup_mcs": int(mcs),
                "lookup_sinr_db": float(sinr), "combining": str(combining),
                "table": int(table),
            }

        la.harq_retransmission_bler = _terminal_retx
        for _terminal_mode in ("experience",):
            _terminal_key = f"{_terminal_mode}/retx_{'ack' if _terminal_ack else 'nack'}"
            _terminal_runs[_terminal_key] = sysm.simulate(
                [_amc_table],
                sys_cfg=sysm.SystemConfig(
                    duration_s=0.006, tdd_pattern="DDDSU",
                    harq_feedback_delay=True, seed=651,
                    harq_max_processes=1),
                traffic=sysm.TrafficConfig(model="full_buffer"),
                sched=sysm.SchedulerConfig(
                    mu_enabled=False, olla_enabled=True,
                    rank=ap.RankConfig(fixed_rank=1)),
                kpi=sysm.KpiConfig(warmup_tti=0, tti_trace_mode="full"),
            )
finally:
    sysm._bler_lookup = _old_terminal_system_bler
    expm._bler_lookup = _old_terminal_experience_bler
    la.harq_retransmission_bler = _old_terminal_retx_bler
    ap.RankController.record_feedback = _old_terminal_record

for _terminal_key, _terminal_run in _terminal_runs.items():
    check(_terminal_run.cell["scheduled_tti"] == 3
          and _terminal_run.cell["harq_feedback_wait_skips"] == 7,
          f"{_terminal_key}：t5 重传后继续占住进程，下一份新 TB 最早 t10")
    check(_terminal_feedback_calls[_terminal_key] == [5],
          f"{_terminal_key}：终次反馈只释放进程，不再次进入首传 OLLA/rank 学习")
    check(abs(float(_terminal_run.cell["olla_mcs_mean"]) + 0.09) < 1e-12,
          f"{_terminal_key}：重传 ACK/NACK 都不二次更新 OLLA")
    if _terminal_key.startswith("experience/"):
        _terminal_alloc = _terminal_run.diagnostics["allocation_sample"]
        check([(row["tti"], row["harq_tx_mode"]) for row in _terminal_alloc]
              == [(0, "newtx"), (5, "retx"), (10, "newtx")],
              f"{_terminal_key}：逐 TTI 轨迹明确没有 t6 新 TB，也没有第三次重传")

# --- 17.2c 多 HARQ 进程：唯一系统路径里不同进程号互不阻塞 ----------------
# **棘轮。** 把 harq_inflight 换回"每 UE 一个槽位"会让这一节全红。
# 同一份夹具、同样强制 ACK：单进程下 10 个 TTI 只发得出 2 次（t0/t5），
# 8 进程下 8 个 D/S 时隙每一个都能发。
_old_mp_bler = expm._bler_lookup
try:
    expm._bler_lookup = lambda _mcs, _sinr: 0.0
    _mp_runs = {}
    for _mp in (1, 2, 4, 8, 16):
        _mp_runs[_mp] = sysm.simulate(
            [_amc_table],
            sys_cfg=sysm.SystemConfig(
                duration_s=0.005, tdd_pattern="DDDSU",
                harq_feedback_delay=True, seed=650,
                harq_max_processes=_mp),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            sched=sysm.SchedulerConfig(
                mu_enabled=False, olla_enabled=True,
                rank=ap.RankConfig(fixed_rank=1)),
            kpi=sysm.KpiConfig(warmup_tti=0, tti_trace_mode="full"))
finally:
    expm._bler_lookup = _old_mp_bler

_mp_sched = {k: int(v.cell["scheduled_tti"]) for k, v in _mp_runs.items()}
_mp_skip = {k: int(v.cell["harq_feedback_wait_skips"]) for k, v in _mp_runs.items()}
print(f"  进程数 -> 已调度 TTI {_mp_sched}；被在途反馈挡住 {_mp_skip}")
check(_mp_sched[1] == 2, f"1 进程：10 个 TTI 只发得出 2 次（实得 {_mp_sched[1]}）")
check(_mp_sched[8] == 8,
      f"8 进程：DDDSU 的 8 个 D/S 时隙全部发得出（实得 {_mp_sched[8]}）")
check(all(_mp_sched[a] <= _mp_sched[b]
          for a, b in zip((1, 2, 4, 8), (2, 4, 8, 16), strict=True)),
      "已调度 TTI 数随进程数单调不降")
check(_mp_skip[1] > 0 and _mp_skip[8] == 0,
      f"进程够用时不再有 UE 被在途反馈挡住（1 进程 {_mp_skip[1]} 次 → "
      f"8 进程 {_mp_skip[8]} 次）")
check(_mp_sched[8] == _mp_sched[16],
      "DDDSU 下 8 个进程已经够用，加到 16 不再有增量")
_mp_sys_cfg = _mp_runs[8].config["system"]
check(_mp_sys_cfg["harq_max_processes"] == 8
      and "up to 8 TBs in flight" in _mp_sys_cfg["harq_feedback_contract"],
      "进程数与合同文字随结果显式上报")
check(int(_mp_runs[8].cell["harq_max_processes"]) == 8,
      "cell 层也带上进程数，分析脚本不必去 config 里翻")
# --- 17.2e experience 侧多进程：收益在**用户体验速率**，不在小区吞吐 ------
# **棘轮。** 把 experience 的 harq_inflight 换回"每 UE 一个槽位"会让这一节全红。
# 全带/按需分配下小区吞吐由话务决定（offered-limited），多进程几乎不动它；
# 真正被单进程压住的是**单个文件多久传完**，也就是体验速率。
_mp_exp_rng = np.random.default_rng(20260904)
_mp_exp_tabs = []
for _u in range(4):
    _s = np.full((20, 4), 14.0 + _mp_exp_rng.normal(0, 2.0))
    _m = np.stack([np.full(20, la.select_mcs(float(_s[0, k]), table=3).index)
                   for k in range(4)], axis=1).astype(int)
    _se = np.stack([np.full(20, la.MCS_TABLE_3[int(_m[0, k])].se)
                    for k in range(4)], axis=1)
    _rbg = np.repeat(_s[:, :, None], 17, axis=2)
    _mp_exp_tabs.append(sysm.UeLinkTable(
        ue=_u, sinr_db=_s, mcs=_m, se=_se,
        best_rank=np.full(20, 2, dtype=int), best_se=_se[:, 1],
        geo_sinr_db=14.0, outage=np.zeros(20, dtype=bool), iot_db=3.0,
        sir_db=12.0, se_gnb=_se.copy(), best_se_gnb=_se[:, 1].copy(),
        sinr_rbg_db=_rbg, sinr_tx_db=_s.copy(), sinr_tx_rbg_db=_rbg.copy()))


def _mp_exp_run(procs: int):
    return sysm.simulate(
        _mp_exp_tabs,
        sys_cfg=sysm.SystemConfig(duration_s=3.0, tdd_pattern="DDDSU",
                                  harq_max_processes=procs),
        traffic=sysm.TrafficConfig(model="ftp3", file_bytes=500_000,
                                   arrival_rate_hz=4.0),
        sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
        kpi=sysm.KpiConfig(warmup_tti=0), rng=rg.RngBook(7, 0)).cell


_mp_exp1 = _mp_exp_run(1)
_mp_exp8 = _mp_exp_run(8)
print(f"  experience 1→8 进程：体验中位 {_mp_exp1['ue_experienced_median_mbps']:.1f}→"
      f"{_mp_exp8['ue_experienced_median_mbps']:.1f} Mbps，完成 p50 "
      f"{_mp_exp1['completion_delay_ms_p50']:.1f}→"
      f"{_mp_exp8['completion_delay_ms_p50']:.1f} ms，小区 "
      f"{_mp_exp1['cell_served_mbps']:.1f}→{_mp_exp8['cell_served_mbps']:.1f} Mbps")
check(_mp_exp8["ue_experienced_median_mbps"]
      > 2.0 * _mp_exp1["ue_experienced_median_mbps"],
      f"experience 侧多进程让体验速率中位翻倍以上"
      f"（{_mp_exp1['ue_experienced_median_mbps']:.1f} → "
      f"{_mp_exp8['ue_experienced_median_mbps']:.1f} Mbps）")
check(_mp_exp8["completion_delay_ms_p50"]
      < 0.5 * _mp_exp1["completion_delay_ms_p50"],
      f"文件完成时延 p50 减半以上（{_mp_exp1['completion_delay_ms_p50']:.1f} → "
      f"{_mp_exp8['completion_delay_ms_p50']:.1f} ms）")
check(abs(_mp_exp8["cell_served_mbps"] / _mp_exp1["cell_served_mbps"] - 1.0) < 0.05,
      f"小区吞吐几乎不动（{_mp_exp1['cell_served_mbps']:.1f} → "
      f"{_mp_exp8['cell_served_mbps']:.1f} Mbps）：它由话务决定，不是被 HARQ 挡住的")
check(_mp_exp1["harq_feedback_wait_skips"] > 0
      and _mp_exp8["harq_feedback_wait_skips"] == 0,
      f"进程够用后没有 UE 再被在途反馈挡住"
      f"（{_mp_exp1['harq_feedback_wait_skips']} → "
      f"{_mp_exp8['harq_feedback_wait_skips']}）")

# --- 17.2f 多进程必须建立在“发送即扣 buffer”之上（内网审核 4609d9d）---
# #25 已删除 legacy capacity 主循环；这里把原审核反例迁到唯一 experience 路径。
# **棘轮。** 把 DrbQueue 改回「只有 ACK 才扣、重传照扣」会让这一节变红。
#
# 单进程时「ACK 才扣」是自洽的：被 NACK 的字节留在缓冲区，只有它自己的重传能
# 把它们发走。**多进程放开后不再自洽**：本 UE 的另一个进程会从同一个缓冲区头
# 再取一份组成新 TB，等原 TB 的重传成功时又扣一次，于是有一批字节被记成送达
# 却从来没上过空口。唯一系统路径必须继续使用 #21 的发送时扣减口径。
_cap_point = sysm.UeLinkTable(
    ue=0, sinr_db=np.array([[16.0]] * 8), mcs=np.array([[20]] * 8),
    se=np.array([[la.MCS_TABLE_3[20].se]] * 8),
    best_rank=np.ones(8, dtype=int),
    best_se=np.full(8, la.MCS_TABLE_3[20].se),
    geo_sinr_db=16.0, outage=np.zeros(8, dtype=bool), mcs_table=3,
    target_bler=0.1)

# 1) 单元级：重传不带新数据，返回 0 且不动缓冲区；NACK 首传照样扣。
_cap_cls = sysm.TrafficClassConfig("ratchet", 1.0, 1000, 1.0)
_cap_tr = expm.DrbQueue(0, _cap_cls)
_cap_tr.arrive(0, 1000)
_cap_before = _cap_tr.queued_bytes
_cap_retx_sent = _cap_tr.transmit(
    1, scheduled_bytes=1000, payload_bytes=1000, ack=True, is_retx=True)
check(_cap_retx_sent == 0 and _cap_tr.queued_bytes == _cap_before,
      f"唯一系统路径：重传返回 0 且不动缓冲区（实得 {_cap_retx_sent}、"
      f"{_cap_before}→{_cap_tr.queued_bytes}）")
_cap_new_sent = _cap_tr.transmit(
    1, scheduled_bytes=1000, payload_bytes=1000, ack=False)
check(_cap_new_sent == 1000 and _cap_tr.queued_bytes == _cap_before - 1000,
      "唯一系统路径：NACK 首传仍在发送时扣 buffer")

# 2) 端到端：强制首传全 NACK。发送即扣之后，已发送字节只由首传次数决定，
#    与重传成功与否无关——这正是「KPI 不看这个 TB 对不对」。
_cap_old_bler = expm._bler_lookup
_cap_old_retx = la.harq_retransmission_bler
_cap_runs = {}


def _cap_retx_stub(mcs, sinr, *, combining="ir", table=3, _v=0.0):
    return {"bler": float(_v), "lookup_mcs": int(mcs),
            "lookup_sinr_db": float(sinr), "combining": str(combining),
            "table": int(table)}


try:
    for _cap_name, _cap_retx_p in (("retx_ok", 0.0), ("retx_fail", 1.0)):
        expm._bler_lookup = lambda _m, _s: 1.0            # 首传必错
        la.harq_retransmission_bler = (
            lambda m, s, _p=_cap_retx_p, **kw: _cap_retx_stub(m, s, _v=_p, **kw))
        _cap_runs[_cap_name] = sysm.simulate(
            [_cap_point],
            sys_cfg=sysm.SystemConfig(
                duration_s=1.0, tdd_pattern="DDDSU",
                seed=99, harq_max_processes=8, harq_feedback_delay=True),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
            kpi=sysm.KpiConfig(warmup_tti=0)).cell
finally:
    expm._bler_lookup = _cap_old_bler
    la.harq_retransmission_bler = _cap_old_retx
_cap_ok, _cap_bad = _cap_runs["retx_ok"], _cap_runs["retx_fail"]
print(f"  唯一路径 8 进程 · 首传全错：重传全对 {_cap_ok['cell_served_mbps']:.2f} Mbps"
      f" / 重传全丢 {_cap_bad['cell_served_mbps']:.2f} Mbps")
check(abs(_cap_ok["cell_served_mbps"] - _cap_bad["cell_served_mbps"]) < 1e-9,
      f"唯一系统路径：重传全对与重传全丢的已发送字节逐值相同"
      f"（{_cap_ok['cell_served_mbps']:.4f} vs {_cap_bad['cell_served_mbps']:.4f}）"
      "——重传不带新数据，KPI 也不看它对不对")

# 3) 字节守恒：有限话务下，已发送字节不能超过到达字节。
#    多进程 + 重传照扣时这条会被顶破（同一批字节扣两次）。
_cap_conserve = {}
_cap_old_bler2 = expm._bler_lookup
try:
    expm._bler_lookup = lambda _m, _s: 0.5
    for _cap_proc in (1, 8):
        _cap_conserve[_cap_proc] = sysm.simulate(
            [_cap_point],
            sys_cfg=sysm.SystemConfig(
                duration_s=1.0, tdd_pattern="DDDSU",
                seed=99, harq_max_processes=_cap_proc, harq_feedback_delay=True),
            traffic=sysm.TrafficConfig(model="ftp3", file_bytes=400_000,
                                       arrival_rate_hz=3.0),
            sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
            kpi=sysm.KpiConfig(warmup_tti=0))
finally:
    expm._bler_lookup = _cap_old_bler2
for _cap_proc, _cap_run in _cap_conserve.items():
    _cap_bytes = _cap_run.diagnostics["byte_conservation"]
    check(_cap_bytes["acked"] <= _cap_bytes["arrived"],
          f"唯一系统路径 {_cap_proc} 进程：已发送字节不超过到达字节"
          f"（{_cap_bytes['acked']} ≤ {_cap_bytes['arrived']}）")

# 4) ``acked_goodput`` 的合同必须独立于“发送即扣 buffer”：ACK 给已发送净荷，
#    NACK 给 0。把 experience.py 的分支退回 ``credit = sent``，下面第二条会变红。
_pf_credit_runs = {}
_pf_credit_old_bler = expm._bler_lookup
try:
    for _pf_label, _pf_bler in (("ack", 0.0), ("nack", 1.0)):
        expm._bler_lookup = lambda _m, _s, _v=_pf_bler: _v
        _pf_credit_runs[_pf_label] = sysm.simulate(
            [_cap_point],
            sys_cfg=sysm.SystemConfig(
                duration_s=0.01, tdd_pattern="D", seed=101,
                harq_max_processes=8),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            sched=sysm.SchedulerConfig(
                mu_enabled=False, olla_enabled=False,
                pf_accounting="acked_goodput", frequency_selective="off",
                rank=ap.RankConfig(fixed_rank=1)),
            kpi=sysm.KpiConfig(warmup_tti=0, tti_trace_mode="full"),
        )
finally:
    expm._bler_lookup = _pf_credit_old_bler

_pf_ack_rows = [
    row for row in _pf_credit_runs["ack"].diagnostics["allocation_sample"]
    if row["harq_tx_mode"] == "newtx"
]
_pf_nack_rows = [
    row for row in _pf_credit_runs["nack"].diagnostics["allocation_sample"]
    if row["harq_tx_mode"] == "newtx"
]
check(bool(_pf_ack_rows) and all(
    row["ack"] and row["pf_credit_bytes"] == row["payload_bytes"] > 0
    for row in _pf_ack_rows),
      "acked_goodput：ACK 首传按实际发送净荷更新 PF")
check(bool(_pf_nack_rows) and all(
    (not row["ack"]) and row["payload_bytes"] > 0
    and row["pf_credit_bytes"] == 0
    for row in _pf_nack_rows),
      "acked_goodput：NACK 首传的 PF credit 严格为 0（与发送时扣 buffer 正交）")

# 进程数非法值必须在配置入口就被拒
for _bad_mp in (0, 17, 1.5, True):
    _expect_value_error(
        lambda v=_bad_mp: sysm.SystemConfig(harq_max_processes=v),
        "harq_max_processes", f"进程数 {_bad_mp!r} 在配置入口被拒")

# --- 17.3 运行时 CQI 上报：新鲜度、测量时延与 UE 实现损失 ------------------
# **棘轮。** 把 attach_runtime_cqi 换回"直接用建表那份 sinr_tx_db"会让这一节
# 全红：上报计数与 CQI 年龄会变成 None，BLER 也退回离线那份系统性乐观的值。
_cqi_rng = np.random.default_rng(20260904)
_cqi_h = [((_cqi_rng.standard_normal((20, 32, 8, 4))
            + 1j * _cqi_rng.standard_normal((20, 32, 8, 4))) / np.sqrt(2))
          for _ in range(3)]
_cqi_tabs = sysm.build_link_tables(
    _cqi_h, [14.0, 12.0, 10.0], max_rank=2, rb_per_rbg=16,
    csi=sysm.ca.CsiConfig(enabled=False))


def _cqi_run(cfg, *, mode="capacity", olla=True, duration_s=3.0):
    return sysm.simulate(
        _cqi_tabs,
        sys_cfg=sysm.SystemConfig(evaluation_mode=mode, duration_s=duration_s,
                                  tdd_pattern="DDDSU", cqi_report=cfg),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=olla),
        kpi=sysm.KpiConfig(warmup_tti=0), rng=rg.RngBook(5, 0))


# 0) 运行时上报默认必须真的接上了。放在最前面，这样把 attach_runtime_cqi
#    改回"永远返回 None"时这一节给出的是清清楚楚的 FAIL，而不是往下走到
#    float(None) 崩掉——棘轮要能读，不能只是炸。
_cqi_default_run = _cqi_run(ap.CqiReportConfig(), duration_s=0.5)
check(_cqi_default_run.cell["cqi_update_count_mean"] is not None
      and _cqi_default_run.cell["cqi_age_tti_max"] is not None,
      "默认配置下运行时 CQI 上报确实生效（两个新鲜度诊断不是 None）")

# 1) 上报节拍：次数随 TTI 线性增长，年龄峰值 = 周期 - 1
_cqi_num_tti = sysm.SystemConfig(duration_s=3.0).num_tti
for _per in (1, 4, 40):
    _run_p = _cqi_run(ap.CqiReportConfig(cqi_period_tti=_per))
    _cnt_raw = _run_p.cell["cqi_update_count_mean"]
    _age_raw = _run_p.cell["cqi_age_tti_max"]
    if _cnt_raw is None or _age_raw is None:
        check(False, f"SRS 周期 {_per} TTI：运行时上报没生效，新鲜度诊断为 None")
        continue
    _cnt = float(_cnt_raw)
    _age = int(_age_raw)
    check(abs(_cnt - _cqi_num_tti / _per) <= 1.0,
          f"SRS 周期 {_per} TTI：上报次数 {_cnt:.0f} ≈ {_cqi_num_tti}/{_per}")
    check(_age == _per - 1,
          f"SRS 周期 {_per} TTI：CQI 年龄峰值 {_age} = 周期 - 1")

# 2) 关掉运行时上报 → 退回建表那份数组，诊断键显式为 None（不是 0）
_cqi_off = _cqi_run(ap.CqiReportConfig(enabled=False))
check(_cqi_off.cell["cqi_update_count_mean"] is None
      and _cqi_off.cell["cqi_age_tti_max"] is None,
      "关闭运行时上报时两个新鲜度诊断是 None——离线预计算没有『上报时刻』")
check(_cqi_off.config["system"]["cqi_report"]["enabled"] is False,
      "CQI 上报口径随结果显式上报")
# 默认周期跟 **CSI 报告周期**，不跟上行 SRS 周期（用户 2026-09-04 定）
_cqi_default_cfg = ap.CqiReportConfig()
check(_cqi_default_cfg.cqi_period_tti is None
      and _cqi_default_cfg.resolve_period_tti(20.0, 0.5) == 40
      and _cqi_default_cfg.resolve_period_tti(5.0, 0.5) == 10,
      "默认上报周期由 csi_report_period_ms 换算：20 ms / 0.5 ms = 40 TTI")
check(_cqi_tabs[0].csi_report_period_ms == 20.0,
      f"链路表把建表时的 CSI 报告周期带出来（实得 "
      f"{_cqi_tabs[0].csi_report_period_ms}）")
_cqi_auto = _cqi_run(ap.CqiReportConfig(), duration_s=3.0)
check(_cqi_auto.cell["cqi_age_tti_max"] == 39
      and _cqi_auto.config["system"]["cqi_report"]["cqi_period_source"]
      == "csi_report_period_ms",
      f"不给 cqi_period_tti 时实际按 40 TTI 上报（年龄峰值 "
      f"{_cqi_auto.cell['cqi_age_tti_max']}）")
check(ap.CqiReportConfig(cqi_period_tti=4).resolve_period_tti(20.0, 0.5) == 4,
      "显式给 cqi_period_tti 时覆盖 CSI 报告周期，只用于消融")

# 3) 决策坐标的准确度：OLLA 关掉才能看见 AMC 坐标本身准不准
_cqi_target = 0.1
_bler_off = {}
for _label, _cfg in (
        ("offline", ap.CqiReportConfig(enabled=False)),
        ("ideal", ap.CqiReportConfig(cqi_period_tti=1, csi_delay_tti=0,
                                     ue_implementation_loss_db=0.0)),
        ("stale_no_loss", ap.CqiReportConfig(cqi_period_tti=4, csi_delay_tti=3,
                                             ue_implementation_loss_db=0.0)),
        ("default", ap.CqiReportConfig()),
):
    _bler_off[_label] = float(_cqi_run(_cfg, olla=False).cell["bler_first_tx"])
print("  OLLA 关闭时的首传 BLER（目标 0.1）：" + "，".join(
    f"{k}={v:.4f}" for k, v in _bler_off.items()))
check(_bler_off["offline"] > 0.25,
      f"离线预计算的 AMC 坐标系统性乐观：BLER {_bler_off['offline']:.4f} 远高于 0.1")
check(abs(_bler_off["ideal"] - _cqi_target)
      < abs(_bler_off["offline"] - _cqi_target) / 3.0,
      f"理想 CQI（周期1/时延0/无损失）的目标偏差至少小一大截："
      f"{abs(_bler_off['ideal'] - _cqi_target):.4f} vs "
      f"{abs(_bler_off['offline'] - _cqi_target):.4f}")
check(_bler_off["stale_no_loss"] > _bler_off["ideal"],
      f"CQI 陈旧（周期4+时延3）让 MCS 偏激进，BLER 从 "
      f"{_bler_off['ideal']:.4f} 抬到 {_bler_off['stale_no_loss']:.4f}")
# **只断言方向。** 1.5 dB 一定让 AMC 更保守；但它是否"正好落回 target"
# 依赖场景落在 MCS 量化台阶的哪一侧——换个信道实测会过冲。不要把
# "在某个夹具上刚好落回目标"写成普适结论。
check(_bler_off["default"] < _bler_off["stale_no_loss"],
      f"1.5 dB 的 UE 实现损失让 AMC 更保守："
      f"{_bler_off['stale_no_loss']:.4f} → {_bler_off['default']:.4f}"
      "（是否正好落回 target 与场景有关，不作断言）")

# 4) 长周期下**不是**更激进而是更保守——IIR 的时间常数随周期一起变长。
#    这条与任务书的猜测相反，实测如此，显式钉住免得以后当成 bug 修。
_bler_long = float(_cqi_run(
    ap.CqiReportConfig(cqi_period_tti=40, csi_delay_tti=3,
                       ue_implementation_loss_db=0.0), olla=False
).cell["bler_first_tx"])
print(f"  周期 40 TTI（OLLA 关）首传 BLER {_bler_long:.4f}，"
      f"对照周期 4 的 {_bler_off['stale_no_loss']:.4f}")
check(_bler_long < _bler_off["stale_no_loss"],
      "周期拉长到 40 TTI 反而更保守：IIR 的 lambda 是**每次上报**作用一次，"
      "周期越长，滤波器在时间上的记忆越长，持有的 CQI 越贴近长期均值")

# 5) 不许偷看未来：上报测的是 srs_period + srs_delay 个 TTI **之前**的信道
_probe = ap.CqiReporter(
    _cqi_tabs, ap.CqiReportConfig(cqi_period_tti=4, csi_delay_tti=3),
    snap_every=10, cqi_filter_lambda=0.25, cqi_filter_domain="cqi_index",
    period_tti=4)
check(_probe.measurement_lag_tti == 7,
      f"测量滞后 = 上报周期 + 处理时延 = 4+3（实得 {_probe.measurement_lag_tti}）")
check(_probe._measure_snapshot(0) == 0 and _probe._measure_snapshot(7) == 0
      and _probe._measure_snapshot(17) == 1,
      "测量快照 = (max(0, tti-7) // snap_every) % n_snap，冷启动钳到 0")
check(all(_probe._measure_snapshot(_t) <= max(0, _t) // 10
          for _t in range(0, 200)),
      "任何 TTI 的测量快照都不晚于当前快照——运行时 CQI 不偷看未来信道")

# 快速回退的 NACK 门限只能在反馈到达后触发。先升到 rank2，在 t1 发送一个
# NACK；t2..t4 必须保持 rank2，t5 将该 feedback 应用后才允许回退。
_delayed_rank = ap.RankController(
    ap.RankConfig(
        mode="adaptive", fixed_rank=1, period_tti=30,
        min_filter_samples=1, se_filter_beta=1.0,
        min_mcs_threshold=0, resource_cost_ratio=(1.0, 1.0),
        gain_factor_raise=1.1, fallback_enabled=True,
        quick_fallback_nack_thld=1, quick_fallback_window_tti=20,
        quick_fallback_min_sched=1),
    1, tti_ms=0.5, max_rank_available=2)
_delayed_rank.observe_link(0, 0, [1.0, 2.0], [20, 20])
_delayed_rank._last_judge_tti[0] = -29
_delayed_rank.step(1, olla_by_ue=np.zeros(1))
check(_delayed_rank.rank_of(0) == 2, "rank 反例先进入 rank2 快速回退监测窗")
_delayed_event = ap.FirstTxFeedback(
    ue=0, ack=False, mcs=20, rank=2, realized_se=0.0,
    tx_tti=1, effective_tti=5, use_mu_olla=False, olla_delta_mcs=-0.09)
_delayed_su_olla = np.zeros(1)
_delayed_mu_olla = np.zeros(1)
for _tti in range(2, 5):
    _delayed_rank.step(_tti, olla_by_ue=_delayed_su_olla)
check(_delayed_rank.rank_of(0) == 2 and not _delayed_event.due(4),
      "反馈到达前 NACK 不可见，rank 不得提前回退")
_delayed_event.apply(
    rank_controller=_delayed_rank,
    su_olla=_delayed_su_olla, mu_olla=_delayed_mu_olla,
    olla_min=-20.0, olla_max=3.0)
_delayed_rank.step(5, olla_by_ue=_delayed_su_olla)
check(_delayed_rank.rank_of(0) == 2,
      "第一个已到达 NACK 未超过硬门限，rank2 继续监测")
_delayed_event_2 = ap.FirstTxFeedback(
    ue=0, ack=False, mcs=20, rank=2, realized_se=0.0,
    tx_tti=6, effective_tti=10, use_mu_olla=False, olla_delta_mcs=-0.09)
for _tti in range(6, 10):
    _delayed_rank.step(_tti, olla_by_ue=_delayed_su_olla)
check(_delayed_rank.rank_of(0) == 2 and not _delayed_event_2.due(9),
      "第二个 NACK 反馈到达前仍不能提前越过回退门限")
_delayed_event_2.apply(
    rank_controller=_delayed_rank,
    su_olla=_delayed_su_olla, mu_olla=_delayed_mu_olla,
    olla_min=-20.0, olla_max=3.0)
_delayed_rank.step(10, olla_by_ue=_delayed_su_olla)
check(_delayed_rank.rank_of(0) == 1
      and _delayed_rank.diagnostics()["events"][-1]["tti"] == 10,
      "第二个 NACK 到达 t10 后才超过门限并回退，不在发送时刻窥见反馈")

# --- 17.3b 运行时 CQI 的门限必须走**表相关**的那个入口 ---------------------
# **棘轮。** 把 _cqi_quantiser 换回"自己去 bler_curves 按预置曲线另算一份行门限"
# 这一节会红：table 1 抛 `ValueError: MCS must be 0..27, got 28`，table 2 静默
# 拿到 table 3 的门限（15 行全偏，最大 3.29 dB）。
#
# bc.get_curve 是**表无关**的——只按 MCS 序号取预置曲线，那批曲线就是 table 3 的。
# la._internal_cqi_thresholds 对 table 1/2 走的是解析 BLER 模型，只有 table 3 才
# 回落到预置曲线。内部 CQI→MCS 的序号映射 table 2 与 table 3 重合（都止于 MCS27）、
# table 1 止于 MCS28，所以同一个错误在两张表上一个崩、一个无声。
#
# 量化（"有多少个门限 <= 观测值"）与反查（"同一组门限按行下标取值"）是同一组门限
# 的两个方向，必须共用 la._internal_cqi_thresholds 这一份；
# system._cqi_threshold_sinr 用的也是它——离线与运行时同源。
# 先放这条：它是单条最有诊断力的检查，而且**不会崩**。红态下 table 1 会直接抛
# ValueError 中断整个文件，把它排在前面才看得见可读的 FAIL 而不是只有堆栈。
# 同理下面的循环按 3→2→1 排：先过默认表，再让 table 2 给出可读 FAIL，最后才是
# table 1 的崩溃。
_q_t2 = np.asarray(ap._cqi_quantiser(0.1, 2), dtype=float)
_q_t3 = np.asarray(ap._cqi_quantiser(0.1, 3), dtype=float)
_q_gap = float(np.max(np.abs(_q_t2 - _q_t3)))
check(_q_gap > 1.0,
      f"table 2 的门限来自它自己的解析 BLER 模型，不是 table 3 的预置曲线"
      f"（两表最大差 {_q_gap:.2f} dB）")

_q_probe = np.arange(-15.0, 35.0, 0.25)
for _q_table in (3, 2, 1):
    for _q_bler in (0.1, 0.01):
        _q_edges = np.asarray(ap._cqi_quantiser(_q_bler, _q_table), dtype=float)
        _q_ref = np.asarray(
            la._internal_cqi_thresholds(_q_bler, _q_table), dtype=float)
        check(_q_edges.shape == _q_ref.shape
              and bool(np.array_equal(_q_edges, _q_ref)),
              f"table {_q_table} / 目标 BLER {_q_bler}：运行时门限与 linkadapt "
              "逐值同源，没有第二份")
        check(bool(np.all(np.diff(_q_edges) >= 0)),
              f"table {_q_table} / 目标 BLER {_q_bler}：门限单调不减"
              "——searchsorted 的前提")
        _q_bad = [
            float(_v) for _v in _q_probe
            if int(np.searchsorted(_q_edges, float(_v), side="right"))
            != int(la.select_reported_cqi(
                float(_v), target_bler=_q_bler, mcs_table=_q_table))]
        check(not _q_bad,
              f"table {_q_table} / 目标 BLER {_q_bler}：量化与 "
              f"la.select_reported_cqi 在 {_q_probe.size} 个探测点上逐值等价"
              f"（不等的有 {len(_q_bad)} 个）")

# 端到端：三张 MCS 表都要能跑完运行时上报，而且真的上报了。
# experience_v2 只收 table 3，所以这里走 capacity。
for _q_table in (3, 2, 1):
    _q_tabs = sysm.build_link_tables(
        [np.ones((2, 16, 4, 2), dtype=complex)], [10.0],
        table=_q_table, num_snapshots=2)
    _q_cell = sysm.simulate(
        _q_tabs,
        sys_cfg=sysm.SystemConfig(evaluation_mode="capacity", duration_s=0.05,
                                  tdd_pattern="DDDSU"),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(olla_enabled=False),
        kpi=sysm.KpiConfig(warmup_tti=0)).cell
    check(_q_cell["cqi_update_count_mean"] is not None
          and float(_q_cell["cqi_update_count_mean"]) > 0.0,
          f"table {_q_table}：运行时 CQI 端到端跑通且真的上报了"
          f"（平均上报 {_q_cell['cqi_update_count_mean']} 次）")


# --- 17.3 解码 SINR 只在实际授予的 RBG 上取 --------------------------------
# 前 8 个 RBG 极好、后 9 个极差；小包只会拿到少数几个 RBG。
_grant_rbg = np.concatenate([np.full(8, 26.0), np.full(9, -4.0)])
_grant_table = sysm.UeLinkTable(
    ue=0,
    sinr_db=np.full((1, 1), float(np.mean(_grant_rbg))),
    mcs=np.array([[la.select_mcs(float(np.mean(_grant_rbg)), table=3,
                                 target_bler=0.1).index]], dtype=int),
    se=np.full((1, 1), 1.0),
    best_rank=np.array([1], dtype=int),
    best_se=np.array([1.0]),
    geo_sinr_db=float(np.mean(_grant_rbg)),
    outage=np.array([False]),
    sinr_tx_db=np.full((1, 1), 20.0),
    sinr_rbg_db=_grant_rbg[None, None, :].copy(),
    sinr_tx_rbg_db=np.full((1, 1, 17), 20.0),
    mcs_table=3, target_bler=0.1,
)
_grant_run = sysm.simulate(
    [_grant_table],
    sys_cfg=sysm.SystemConfig(duration_s=0.05,
                              tdd_pattern="D", seed=77),
    traffic=sysm.TrafficConfig(model="cbr", cbr_mbps=1.0),
    sched=sysm.SchedulerConfig(
        mu_enabled=False, olla_enabled=False, frequency_selective="off",
        rank=ap.RankConfig(fixed_rank=1)),
    kpi=sysm.KpiConfig(warmup_tti=0),
)
_grant_rows = [r for r in _grant_run.diagnostics["allocation_sample"]
               if r["n_rbg"] < 17]
check(bool(_grant_rows), "小包确实只拿到部分 RBG，可用于检查解码 SINR 口径")
_wideband_db = float(np.mean(_grant_rbg))
check(all(abs(r["sinr_db"] - _wideband_db) > 1.0 for r in _grant_rows),
      f"部分授权的解码 SINR 不再等于全带均值 {_wideband_db:.2f} dB")
check(all(
    abs(r["sinr_db"]
        - float(np.mean(_grant_rbg[np.asarray(r["rbg_indices"], dtype=int)])))
    < 1e-4
    for r in _grant_rows),
    "解码 SINR 精确等于被授 RBG 上真值的 dB 域均值")

# --- 17.4 Rank 策略：默认固定、可切历史模式、越界钳位 -----------------------
check(sysm.SchedulerConfig().rank.mode == "fixed"
      and sysm.SchedulerConfig().rank.fixed_rank == 2,
      "默认 rank 策略是固定 rank2，不跟随逐快照 best_rank")
for _bad_rank in ({"mode": "auto"}, {"fixed_rank": 0}, {"period_tti": 0},
                  {"gain_factor_raise": 0.5}, {"se_filter_beta": 0.0},
                  {"se_sample_scope": "slot"}, {"min_filter_samples": 0},
                  {"quick_fallback_ibler_thld": 1.5},
                  {"resource_cost_ratio": ()},
                  {"max_backoff_times": -1}):
    try:
        ap.RankConfig(**_bad_rank)
        check(False, f"非法 rank 配置 {_bad_rank} 应当被拒")
    except ValueError:
        check(True, f"非法 rank 配置 {_bad_rank} 被拒")

_rank_ctl = ap.RankController(
    ap.RankConfig(fixed_rank=4), 2, tti_ms=0.5, max_rank_available=2)
check(_rank_ctl.rank_of(0) == 2 and _rank_ctl.rank_for(0, 1) == 2,
      "fixed_rank 超过链路表可用 rank 时钳位，且不被链路表反向拉走")
_legacy_ctl = ap.RankController(
    ap.RankConfig(mode="link_table"), 2, tti_ms=0.5, max_rank_available=4)
check(_legacy_ctl.rank_for(0, 3) == 3 and _legacy_ctl.rank_for(0, 9) == 4,
      "link_table 历史模式跟随链路表并钳到可用上限")

_rank_runs = {}
for _mode, _cfg in (("fixed2", ap.RankConfig(fixed_rank=2)),
                    ("legacy", ap.RankConfig(mode="link_table"))):
    _rank_runs[_mode] = sysm.simulate(
        _T[:4],
        sys_cfg=sysm.SystemConfig(duration_s=0.05,
                                  tdd_pattern="DDDSU", seed=909),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(mu_enabled=False, rank=_cfg),
        kpi=sysm.KpiConfig(warmup_tti=0, tti_trace_mode="off"),
    )
check(all(abs(r["rank"] - 2) < 1e-9
          for r in _rank_runs["fixed2"].diagnostics["allocation_sample"]),
      "固定模式下每一次 grant 的 rank 都是 2")
check(_rank_runs["fixed2"].cell["avg_rank"] == 2.0,
      "固定模式的小区平均 rank 精确为 2，没有任何切换")
check(_rank_runs["legacy"].cell["avg_rank"] != 2.0,
      "历史模式确实会用到别的 rank，两个模式不是同一条轨迹")
check(_rank_runs["fixed2"].config["scheduler"]["rank"]["mode"] == "fixed"
      and "best_rank" in _rank_runs["fixed2"].config["scheduler"]["rank"][
          "adaptation_note"],
      "rank 策略随结果一起交付，并写明 best_rank 不再参与发送决策")

# --- 17.5 自适应模式：现场规格的判决状态机 --------------------------------
# 常数全部来自用户 2026-09-02 给的现场实现规格：判决周期 1000 TTI、
# 升 rank 谱效比 1.1、谱效滤波 beta=0.1、最少 3 个样本、最小 MCS 闸门 9、
# 资源消耗系数 [1.0,0.97,0.95,0.93]、快速回退（NACK 90 / IBLER 0.3 /
# 谱效比 1.0）、判决周期指数退避 x2^n（n<=4）。
_SPEC_RANK = ap.RankConfig(mode="adaptive")
check(int(_SPEC_RANK.period_tti) == 1000
      and float(_SPEC_RANK.gain_factor_raise) == 1.1
      and float(_SPEC_RANK.gain_factor_reduce) == 1.1
      and float(_SPEC_RANK.se_filter_beta) == 0.1
      and int(_SPEC_RANK.min_filter_samples) == 3
      and int(_SPEC_RANK.min_mcs_threshold) == 9
      and tuple(_SPEC_RANK.resource_cost_ratio) == (1.0, 0.97, 0.95, 0.93)
      and int(_SPEC_RANK.quick_fallback_nack_thld) == 90
      and float(_SPEC_RANK.quick_fallback_ibler_thld) == 0.3
      and float(_SPEC_RANK.quick_fallback_se_ratio_thld) == 1.0
      and int(_SPEC_RANK.max_backoff_times) == 4,
      "rank 自适应的默认常数逐项等于现场规格")


def _rank_ctl_spec(**kw):
    base = dict(mode="adaptive", fixed_rank=1, period_tti=100,
                min_filter_samples=1, se_filter_beta=1.0,
                min_mcs_threshold=0,
                resource_cost_ratio=(1.0, 1.0, 1.0, 1.0),
                fallback_enabled=False)
    base.update(kw)
    return ap.RankController(ap.RankConfig(**base), 1, tti_ms=0.5,
                             max_rank_available=4)


def _drive(ctl, se, mcs=(20, 20, 20, 20), n_tti=401, olla=None):
    for _tti in range(n_tti):
        ctl.observe_link(0, _tti, list(se), list(mcs))
        ctl.step(_tti, olla_by_ue=olla)
    return ctl


# 升 rank：必须**严格超过** 1.1 倍才切
_r105 = _drive(_rank_ctl_spec(), [1.0, 1.05, 0.0, 0.0])
_r110 = _drive(_rank_ctl_spec(), [1.0, 1.10, 0.0, 0.0])
_r111 = _drive(_rank_ctl_spec(), [1.0, 1.11, 0.0, 0.0])
print(f"  升 rank 迟滞：比值 1.05→rank{_r105.rank_of(0)}、"
      f"1.10→rank{_r110.rank_of(0)}、1.11→rank{_r111.rank_of(0)}")
check(_r105.rank_of(0) == 1 and _r110.rank_of(0) == 1,
      "最优 rank 谱效没有严格超过当前的 1.1 倍时不升（现场 GainFactor=1.1）")
check(_r111.rank_of(0) == 2
      and _r111.diagnostics()["count_switch_up"] == 1,
      "超过 1.1 倍后升到 rank2，并记录一次抬升")
check(_r110.diagnostics()["count_blocked_by_raise_hysteresis"] > 0,
      "被迟滞挡下的次数显式计数，不是静默不动")

# **默认判据（用户 2026-09-03 裁决）：按滤波谱效最大化选 rank，但任何方向的
# 切换都要求最优 rank 超过当前 rank 10%。** 迟滞因此是对称的。
_SYM = ap.RankConfig(mode="adaptive")
check(_SYM.switch_rule == "unified_ratio"
      and float(_SYM.gain_factor_raise) == 1.1
      and float(_SYM.gain_factor_reduce) == 1.1,
      "默认是对称 10% 迟滞：两个方向共用同一条判据、同一个常数")
check(_SYM.as_dict()["raise_margin_pct"] == 10.0
      and _SYM.as_dict()["reduce_margin_pct"] == 10.0,
      "两个方向的等效余量直接算成百分比报出来，不让人自己换算")


def _margin_trial(se_by_rank, start_rank: int, **kw):
    _c = _rank_ctl_spec(**kw)
    _c._rank[0] = int(start_rank)
    _drive(_c, se_by_rank)
    return _c.rank_of(0)


# 同一个 5.00 基准，最优候选分别高 4% / 9% / 11%；升降两个方向对称
_sym_rows = [
    ("降：当前 rank3，rank1 高 4%", [5.20, 4.0, 5.00, 3.0], 3, 3),
    ("降：当前 rank3，rank1 高 9%", [5.45, 4.0, 5.00, 3.0], 3, 3),
    ("降：当前 rank3，rank1 高 11%", [5.55, 4.0, 5.00, 3.0], 3, 1),
    ("升：当前 rank1，rank3 高 9%", [5.00, 4.0, 5.45, 3.0], 1, 1),
    ("升：当前 rank1，rank3 高 11%", [5.00, 4.0, 5.55, 3.0], 1, 3),
]
for _label, _se, _start, _want in _sym_rows:
    _got = _margin_trial(_se, _start)
    check(_got == _want, f"对称 10% 迟滞 · {_label} → rank{_want}")
print("  对称 10% 迟滞：4%/9% 不动、11% 才切，升降两个方向一致")

# **当前 rank 被最小 MCS 闸门判死时不讲迟滞。** 否则 se[cur]=se[best]=0，
# "超过 10%" 恒为假，UE 会卡在一个已知发不出去的 rank 上。
_gated = _rank_ctl_spec(min_mcs_threshold=9)
_gated._rank[0] = 3
_drive(_gated, [1.0, 1.0, 1.0, 1.0], mcs=(8, 8, 8, 8))
check(_gated.rank_of(0) == 1
      and _gated.diagnostics()["events"][-1]["reason"]
      == "current_rank_gated_out",
      "所有 rank 都被闸门判死时退到最低档，并显式记下原因")

# **两份现场来源对降档判据的写法不一致，而且让 G↓ 的含义正好相反。**
# 我们的默认（unified_ratio + 1.1）是用户裁决的对称 10% 迟滞；另一种写法保留
# 作对照，因为现场到底是哪一种尚未确认，而这正是排查"rank 卡在高档下不来"
# 时首先要钉死的一条。四种组合的行为逐一锁住，防止有人只改一个常数就以为
# 换了一种行为。
def _reduce_trial(rule: str, gd: float, se_by_rank, start_rank: int):
    _c = ap.RankController(
        ap.RankConfig(mode="adaptive", fixed_rank=1, period_tti=100,
                      min_filter_samples=1, se_filter_beta=1.0,
                      min_mcs_threshold=0,
                      resource_cost_ratio=(1.0, 1.0, 1.0, 1.0),
                      fallback_enabled=False, switch_rule=rule,
                      gain_factor_reduce=gd),
        1, tti_ms=0.5, max_rank_available=4)
    _c._rank[0] = int(start_rank)
    for _tti in range(401):
        _c.observe_link(0, _tti, list(se_by_rank), [20, 20, 20, 20])
        _c.step(_tti)
    return _c.rank_of(0), _c.diagnostics()["count_blocked_by_reduce_hysteresis"]


# 当前 rank3，最优 rank1 只高 4%（不到 10%）——四种组合两两相反
_SE_NARROW = [5.20, 4.0, 5.00, 3.0]
_r_spec_11 = _reduce_trial("spec_asymmetric", 1.1, _SE_NARROW, 3)
_r_spec_09 = _reduce_trial("spec_asymmetric", 0.9, _SE_NARROW, 3)
_r_unif_11 = _reduce_trial("unified_ratio", 1.1, _SE_NARROW, 3)
_r_unif_09 = _reduce_trial("unified_ratio", 0.9, _SE_NARROW, 3)
print(f"  降档四组合（最优只高 4%）：spec/1.1→rank{_r_spec_11[0]}、"
      f"spec/0.9→rank{_r_spec_09[0]}、unified/1.1→rank{_r_unif_11[0]}、"
      f"unified/0.9→rank{_r_unif_09[0]}")
check(_r_spec_11 == (1, 0),
      "spec_asymmetric + G↓=1.1：降 rank 立即生效（保持不变的条件恒为假）——"
      "同一个常数、相反的行为，所以两者不能只看数值")
check(_r_spec_09[0] == 3 and _r_spec_09[1] > 0,
      "spec_asymmetric + G↓=0.9：当前 rank 还有最优的 90% 以上就不降")
check(_r_unif_11[0] == 3 and _r_unif_11[1] > 0,
      "unified_ratio + G↓=1.1（默认）：降 rank 同样要 10% 余量，迟滞对称")
check(_r_unif_09 == (1, 0),
      "unified_ratio + G↓=0.9：降 rank 立即生效")
check(_r_spec_11[0] != _r_unif_11[0],
      "同一个 G↓=1.1 在两种写法下给出相反的降档行为，必须连 switch_rule 一起读")
# 差距够大时四种组合一致，说明差异只在"临界带"里
_SE_WIDE = [7.0, 4.0, 5.0, 3.0]
check(all(_reduce_trial(_rule, _gd, _SE_WIDE, 3)[0] == 1
          for _rule in ("spec_asymmetric", "unified_ratio")
          for _gd in (1.1, 0.9)),
      "最优 rank 高 40% 时四种组合都降，写法差异只体现在临界带")
check(ap.RankConfig(mode="adaptive").switch_rule == "unified_ratio",
      "默认是用户裁决的对称 10% 写法；实现规格文档那种保留作对照")
check(round(ap.RankConfig(
    mode="adaptive", switch_rule="spec_asymmetric",
    gain_factor_reduce=0.9).as_dict()["reduce_margin_pct"], 1) == 11.1,
      "spec_asymmetric+0.9 的等效降档余量是 11.1%，与默认那条同一个意图")
try:
    ap.RankConfig(mode="adaptive", switch_rule="whatever")
    check(False, "非法 switch_rule 应当被拒")
except ValueError:
    check(True, "非法 switch_rule 被拒")

# 最小 MCS 闸门：谱效再高，预估 MCS 低于门限也不算有效层
_gate = _drive(_rank_ctl_spec(min_mcs_threshold=9),
               [1.0, 2.0, 0.0, 0.0], mcs=(20, 8, 8, 8), n_tti=301)
check(_gate.rank_of(0) == 1,
      "rank2 估计谱效高一倍，但预估 MCS 8 < 9 被闸门置零，不升")

# 资源消耗加权：现场系数逐 rank 乘进滤波前的观测
_cost = ap.RankController(
    ap.RankConfig(mode="adaptive", fixed_rank=1, se_filter_beta=1.0,
                  min_mcs_threshold=0),
    1, tti_ms=0.5, max_rank_available=4)
_cost.observe_link(0, 0, [1.0, 1.0, 1.0, 1.0], [20, 20, 20, 20])
check(all(abs(float(_cost._se_filt[0, _i]) - _v) < 1e-12
          for _i, _v in enumerate((1.0, 0.97, 0.95, 0.93))),
      "各 rank 的谱效按 DMRS 开销系数 [1.0,0.97,0.95,0.93] 加权")

# 最少样本数：样本不够就不判决
_few = _rank_ctl_spec(min_filter_samples=3)
for _tti in range(501):
    _few.observe_link(0, min(_tti, 1), [1.0, 5.0, 0.0, 0.0], [20, 20, 20, 20])
    _few.step(_tti)
check(_few.rank_of(0) == 1
      and _few.diagnostics()["count_blocked_by_filter_samples"] > 0,
      "谱效滤波样本不足 min_filter_samples 时不判决（现场默认 3）")

# 周期没到不判决
_early = _rank_ctl_spec()
_drive(_early, [1.0, 5.0, 0.0, 0.0], n_tti=50)
check(_early.rank_of(0) == 1,
      "周期没到就不决策——rank 不会每个快照跟着谱效跳")

# 快速回退：NACK 硬门限立即触发，rank 与 OLLA 一起退回，判决周期翻倍
_fb_ctl = _rank_ctl_spec(period_tti=1000, fallback_enabled=True,
                         quick_fallback_nack_thld=90)
_olla = np.array([-1.5])
_drive(_fb_ctl, [1.0, 5.0, 0.0, 0.0], n_tti=1001, olla=_olla)
check(_fb_ctl.rank_of(0) == 2, "先抬升到 rank2 并进入快速回退监测")
_olla[0] = -4.0          # 新 rank 上 OLLA 已经收敛到别的工作点
_restores = []
for _tti in range(1001, 1200):
    _fb_ctl.record_first_tx(0, ack=False, mcs=20, realized_se=0.0)
    _restores += _fb_ctl.step(_tti, olla_by_ue=_olla)
    if _restores:
        break
_fb_diag = _fb_ctl.diagnostics()
print(f"  快速回退：{_fb_diag['events'][-1]['reason']} @ TTI "
      f"{_fb_diag['events'][-1]['tti']}，OLLA 恢复 {_restores}，"
      f"判决周期 {_fb_diag['effective_judge_period_by_ue']}")
check(_fb_ctl.rank_of(0) == 1
      and _fb_diag["events"][-1]["reason"] == "quick_fallback_hard_nack",
      "监测期内 NACK 超过硬门限立即回退，不等窗口结束")
check(_restores == [(0, -1.5)],
      "回退把 OLLA 一起退回抬升前的偏置，而不是留下新 rank 收敛出来的那个")
check(_fb_diag["backoff_times_by_ue"] == [1]
      and _fb_diag["effective_judge_period_by_ue"] == [2000],
      "回退一次后判决周期指数退避 ×2")

# 指数退避封顶
_bo = _rank_ctl_spec(period_tti=100, fallback_enabled=True,
                     quick_fallback_nack_thld=5, max_backoff_times=4)
_bo_olla = np.zeros(1)
_bo_periods = set()
for _tti in range(40000):
    _bo.observe_link(0, _tti, [1.0, 5.0, 0.0, 0.0], [20, 20, 20, 20])
    _bo.step(_tti, olla_by_ue=_bo_olla)
    if _bo.rank_of(0) > 1:
        _bo.record_first_tx(0, ack=False, mcs=20, realized_se=0.0)
    _bo_periods.add(_bo.judge_period_tti(0))
check(sorted(_bo_periods) == [100, 200, 400, 800, 1600],
      "判决周期按 ×2^n 退避并在 max_backoff_times 处封顶（100→1600）")
check(_bo.diagnostics()["count_fallback"] > 1,
      "反复失败的抬升被反复回退，退避让重试越来越稀")

# --- 17.6 avg_mcs 含重传，avg_mcs_first_tx 只看首传 -------------------------
_amc_cell = _rank_runs["fixed2"].cell
check("avg_mcs_first_tx" in _amc_cell
      and "avg_mcs_first_tx" in _rank_runs["fixed2"].users[0],
      "小区级与用户级都给出只统计首传的平均 MCS")
check("retransmissions included" in _amc_cell["avg_mcs_definition"],
      "结果里写明 avg_mcs 的分母含重传，avg_mcs_first_tx 才是链路自适应视角")

# --- 17.7 目标 BLER 可配，并且贯穿量化门限、选档与闭环 ---------------------
check(la.select_mcs(12.0, table=3, target_bler=0.3).index
      > la.select_mcs(12.0, table=3, target_bler=0.1).index,
      "同一 SINR 下放宽目标 BLER 允许更高的 MCS（查表原语单调）")
_thr10 = la._internal_cqi_thresholds(0.1, 3)
_thr30 = la._internal_cqi_thresholds(0.3, 3)
check(all(b < a for a, b in zip(_thr10, _thr30, strict=True)),
      "CQI 量化门限逐档随目标 BLER 下移，量化侧也吃到了这个参数")

_bler_h = [((np.random.default_rng(_s).standard_normal((8, 24, 16, 4))
             + 1j * np.random.default_rng(_s + 50).standard_normal(
                 (8, 24, 16, 4))) / np.sqrt(2))
           for _s in range(3)]
_bler_tables = {
    _target: sysm.build_link_tables(_bler_h, [6.0, 2.0, -2.0],
                                    target_bler=_target)
    for _target in (0.1, 0.3)
}
check(_bler_tables[0.3][0].target_bler == 0.3,
      "目标 BLER 跟着链路表走，主循环不再自己写死 10%")
# **开环大部分抵消**：目标同时出现在 CQI→门限 和 门限→MCS 两侧，两次平移
# 方向相同、幅度接近，选出的 MCS 索引因此多数不变。抵消不精确——内部 CQI 表
# 只取 MCS 0,2,…,26,28 这个子集，量化边界上两侧的位移会差一点。
# 实测 6 个信道 × 4 个几何点 × 4 个 rank 共 384 个样本：354 个完全相同，
# 其余 30 个偏高 1~4 档，**方向恒为「放宽目标选更高档」，没有一个偏低**。
# 所以这里断言的是方向性质而不是逐值相等；真正吃到目标的是 OLLA 闭环。
_open_loop_delta = np.concatenate([
    (sysm.build_link_tables([_h], [_geo], target_bler=0.3)[0].mcs_tx
     - sysm.build_link_tables([_h], [_geo], target_bler=0.1)[0].mcs_tx).ravel()
    for _seed in range(3)
    for _h in [((np.random.default_rng(_seed).standard_normal((4, 24, 16, 4))
                 + 1j * np.random.default_rng(_seed + 90).standard_normal(
                     (4, 24, 16, 4))) / np.sqrt(2))]
    for _geo in (-8.0, -2.0, 4.0, 10.0)
])
check(bool(np.all(_open_loop_delta >= 0)),
      "放宽目标 BLER 在开环上从不选到更低的 MCS 档")
check(float(np.mean(_open_loop_delta == 0)) > 0.8,
      f"开环 MCS 多数对目标不敏感（{np.mean(_open_loop_delta == 0):.0%} 逐值相同）："
      "同一个目标在量化与选档两侧大部分抵消")
_bler_runs = {}
for _target, _tabs in _bler_tables.items():
    # **这一组守的是 OLLA 的稳态，所以要跑到稳态、而且不能被钳位截住。**
    # 三件事叠在一起把这条锚点推离了稳态：buffer 改成发送时扣减、多进程放开、
    # CQI 改成运行时上报（默认口径含 1.5 dB UE 实现损失 + 测量时延，比建表那份
    # 离线值保守约一档）。1 s 不够 OLLA 收敛（实测两臂 1.65 / 1.64，差异淹在
    # 噪声里），而默认 olla_max_db=3.0 下两臂又都顶在 2.96/2.99 —— 那时候比的
    # 是钳位值不是稳态偏置。跑满 2 s 并把钳位放到 6 dB 之后两臂才真正分开。
    _bler_runs[_target] = sysm.simulate(
        _tabs,
        sys_cfg=sysm.SystemConfig(duration_s=2.0,
                                  tdd_pattern="DDDSU", seed=11),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(mu_enabled=False, olla_max_db=6.0),
        kpi=sysm.KpiConfig(warmup_tti=0, tti_trace_mode="off"))
_c10, _c30 = _bler_runs[0.1].cell, _bler_runs[0.3].cell
check(max(_c10["olla_mcs_mean"], _c30["olla_mcs_mean"]) < 5.5,
      f"两臂的 OLLA 都没顶到 6 dB 钳位，比的是稳态而不是钳位值"
      f"（{_c10['olla_mcs_mean']:.2f} / {_c30['olla_mcs_mean']:.2f}）")
check(_c30["olla_mcs_mean"] > _c10["olla_mcs_mean"],
      f"目标放宽后 OLLA 稳态偏置更激进（{_c10['olla_mcs_mean']:.2f} → "
      f"{_c30['olla_mcs_mean']:.2f} MCS 档）")
check(_c30["avg_mcs_first_tx"] > _c10["avg_mcs_first_tx"],
      f"闭环下首传平均 MCS 随目标放宽而升高（{_c10['avg_mcs_first_tx']:.2f} → "
      f"{_c30['avg_mcs_first_tx']:.2f}）")
# 实测值不会精确落在目标上：OLLA 的稳态推导是连续偏置，而空口 MCS 是整数档，
# 偏置要累积到跨过一整档才改变发送，因此实测值围绕目标抖且系统性偏低。
check(_c30["bler_first_tx"] > 1.5 * _c10["bler_first_tx"],
      f"实测首传 BLER 跟着目标显著上移（{_c10['bler_first_tx']:.3f} → "
      f"{_c30['bler_first_tx']:.3f}），但整数 MCS 档使它不精确等于目标")
for _bad_target in (0.0, 1.0, 0.0005, 0.999):
    try:
        sysm.build_link_tables([np.ones((1, 4, 2, 2), dtype=complex)], [10.0],
                               target_bler=_bad_target)
        check(False, f"target_bler={_bad_target} 应当被拒")
    except ValueError:
        check(True, f"target_bler={_bad_target} 越出预置曲线覆盖区间，被拒")
for _analytic_table in (1, 2):
    _analytic_tabs = sysm.build_link_tables(
        [np.ones((2, 16, 4, 2), dtype=complex)], [10.0],
        table=_analytic_table, num_snapshots=2)
    check(_analytic_tabs[0].mcs_table == _analytic_table,
          f"build_link_tables table={_analytic_table} 走同表解析 BLER")
    # #23 引入的表 1/2 有限码长解析 BLER **仍然存在且仍然被显式标注**，
    # 只是消费者从（已下线的）容量主循环收回到链路级。标注不许退化成静默。
    _analytic_probe = la.harq_retransmission_bler(
        10, 8.0, combining="cc", table=_analytic_table)
    check(_analytic_probe["bler_source"] == "finite_blocklength_analytic"
          and _analytic_probe["curve_tx_mode"] == "analytic",
          f"链路级 table={_analytic_table} 显式标注解析 BLER，不冒充预置曲线")
    check(la.harq_retransmission_bler(
              10, 8.0, combining="cc", table=3)["bler_source"]
          != "finite_blocklength_analytic",
          "表 3 仍走预置 NewTx 曲线，不与解析模型交叉借表")
    # 系统级只有一条路径，而它没有表 1/2 的 TBS/BLER profile：必须显式硬失败，
    # 且报错要指到「系统级只支持表 3」，不能让人对着下游 experience_v2 的话猜。
    try:
        sysm.simulate(
            _analytic_tabs,
            sys_cfg=sysm.SystemConfig(duration_s=0.01, tdd_pattern="DDDSU"),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            kpi=sysm.KpiConfig(warmup_s=0.0))
        check(False, f"系统级不应接受 table={_analytic_table}")
    except ValueError as _exc:
        check("系统级仿真只支持预置 MCS table 3" in str(_exc)
              and f"table={_analytic_table}" in str(_exc),
              f"系统级 table={_analytic_table} 硬失败且报错点名收到的表号")


# ---------------------------------------------------------------------------
sect("18  MU：代价必须同时进 MCS 决策与误块抽签")

# 历史口径（se_ratio_legacy）只把 TBS 乘一个标量比值——「包变小但不更容易错」，
# 物理上说不通，已随 legacy 容量路径下线。这一节证明三件事：pair 表口径确实
# 把配对代价压进了 MCS；误块抽签换成了 pair 真值；SU/MU 自适应在信道高度
# 相关时会判 SU 赢。话务用 full_buffer（容量口径），走的仍是同一条路径。

_MU_SNAP = 6


def _mu_pair_tables(corr: float, seed: int = 20260902):
    """两个 UE，空间相关系数可控。corr→1 时 ZF 没有零陷空间可用。"""
    _r = np.random.default_rng(seed)
    _a = ((_r.standard_normal((_MU_SNAP, 272, 16, 4))
           + 1j * _r.standard_normal((_MU_SNAP, 272, 16, 4))) / np.sqrt(2))
    _e = ((_r.standard_normal((_MU_SNAP, 272, 16, 4))
           + 1j * _r.standard_normal((_MU_SNAP, 272, 16, 4))) / np.sqrt(2))
    _b = corr * _a + np.sqrt(max(1.0 - corr ** 2, 0.0)) * _e
    return sysm.build_link_tables(
        [_a, _b], [14.0, 12.0], max_rank=2, rb_per_rbg=16, mu_enabled=True,
        csi=sysm.ca.CsiConfig(enabled=False))


def _mu_run(tables, *, mu_on: bool, processes: int = 1):
    # **HARQ 进程数在这组对照里必须钉死。** 本节比的是 SU 与 MU 两个臂的
    # 物理差异，进程数是另一个自变量：放开它，"发得出多少 TB" 会跟着变，
    # OLLA 看到的反馈量随之变化，配对准入的通过率也跟着动（见 17.2d）。
    # 钉在 1 还有一个好处：这一节的数字与多进程改动之前逐值可比。
    return sysm.simulate(
        tables,
        sys_cfg=sysm.SystemConfig(duration_s=0.6,
                                  tdd_pattern="DDDSU", seed=4242,
                                  harq_max_processes=processes),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(mu_enabled=mu_on),
        kpi=sysm.KpiConfig(warmup_tti=0, tti_trace_mode="off"),
        rng=rg.RngBook(4242, 0))


_T_indep = _mu_pair_tables(0.0)

# 三 UE 反例：每个 UE 至少还有一个邻居，但缺 1<->2 仍必须硬失败。
_g_rng = np.random.default_rng(20260903)
_g_h = [((_g_rng.standard_normal((2, 32, 8, 2))
          + 1j * _g_rng.standard_normal((2, 32, 8, 2))) / np.sqrt(2))
        for _ in range(3)]
_T_graph = sysm.build_link_tables(
    _g_h, [12.0, 11.0, 10.0], max_rank=2, rb_per_rbg=16,
    mu_enabled=True, csi=sysm.ca.CsiConfig(enabled=False))
check(_T_graph[0].mu_links.keys() == {1, 2}
      and _T_graph[1].mu_links.keys() == {0, 2}
      and _T_graph[2].mu_links.keys() == {0, 1},
      "三 UE 建表形成完整双向 pair graph")


def _pair_graph_error(tables, needle):
    try:
        sysm.simulate(
            tables,
            sys_cfg=sysm.SystemConfig(duration_s=0.01, tdd_pattern="D", seed=19),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            sched=sysm.SchedulerConfig(mu_enabled=True, mu_accounting="pair_table"),
                    kpi=sysm.KpiConfig(warmup_s=0.0))
    except ValueError as exc:
        return needle in str(exc)
    return False


_T_missing_edge = deepcopy(_T_graph)
del _T_missing_edge[1].mu_links[2]
del _T_missing_edge[2].mu_links[1]
check(_pair_graph_error(_T_missing_edge, "UE 1 缺边 [2]"),
      "三 UE 即使各自仍有邻居，缺 1↔2 边也在入口硬失败")
_T_asymmetric = deepcopy(_T_graph)
_T_asymmetric[2].mu_links[1] = deepcopy(_T_asymmetric[2].mu_links[1])
check(_pair_graph_error(_T_asymmetric, "不对称"),
      "pair graph 单向残缺被识别为不对称，不在候选枚举时静默跳过")
_T_bad_dim = deepcopy(_T_graph)
_T_bad_dim[1].mu_links[2].true_sinr_db = \
    _T_bad_dim[1].mu_links[2].true_sinr_db[:, :1]
check(_pair_graph_error(_T_bad_dim, "维度不一致"),
      "pair graph 的 snapshot×两用户维度在调度前硬校验")

# MU 准入必须查询叠加 SU+MU OLLA 后的实发 MCS。用一个阶跃 BLER
# 反例：OLLA 前 MCS 全部可用，第一次 MU ACK 令 MU OLLA +3 档；下一次候选
# 的实发 MCS 越过 0.5 门，必须拒配。旧实现仍用 OLLA 前 MCS，会继续放行。
_olla_link = _T_indep[0].mu_links[1]
_olla_base_mcs = []
for _u in (0, 1):
    _side = int(_olla_link.side(_u))
    _pred = (float(_T_indep[_u].sinr_tx_db[0, 1])
             + float(_olla_link.corr_loss_tx_db[0, _side])
             + float(_olla_link.power_loss_db))
    _olla_base_mcs.append(int(la.select_mcs(
        _pred, table=3, target_bler=0.1).index))
_olla_bler_step = max(_olla_base_mcs) + 1
check(_olla_bler_step <= 27,
      "MU OLLA 准入反例位于有效 MCS 范围内")
_old_mu_admission_bler = sysm._bler_lookup
try:
    sysm._bler_lookup = lambda mcs, _sinr: (
        0.9 if int(mcs) >= _olla_bler_step else 0.0)
    _olla_admission_run = sysm.simulate(
        _T_indep,
        sys_cfg=sysm.SystemConfig(
            duration_s=0.01,
            tdd_pattern="DDDSU", seed=313),
        traffic=sysm.TrafficConfig(model="full_buffer"),
        sched=sysm.SchedulerConfig(
            mu_enabled=True, mu_accounting="pair_table",
            mu_corr_threshold=1.0, mu_olla_step_up_db=3.0,
            olla_max_db=6.0),
        rng=rg.RngBook(313, 0),
                    kpi=sysm.KpiConfig(warmup_s=0.0))
finally:
    sysm._bler_lookup = _old_mu_admission_bler
_olla_rejects = _olla_admission_run.cell["mu_candidate_scoring"]["rejection_reasons"]
check(_olla_admission_run.cell["mu_share"] > 0
      and sum(int(v) for v in _olla_rejects.values()) > 0,
      f"正 MU OLLA 令实发 MCS 的预测 BLER 越过 0.5 后拒绝后续配对（{_olla_rejects}）")
check(_olla_admission_run.cell["mu_pair_graph"]["status"] == "pass"
      and _olla_admission_run.cell["mu_pair_graph"]["pairs"] == 1,
      "有效 pair graph 的完整性证据随结果交付")

_su_arm = _mu_run(_T_indep, mu_on=False)
_mu_arm = _mu_run(_T_indep, mu_on=True)
print(f"  独立信道：SU 首传 MCS {_su_arm.cell['avg_mcs_first_tx']:.2f} → "
      f"MU {_mu_arm.cell['avg_mcs_first_tx']:.2f}，"
      f"MU 占比 {_mu_arm.cell['mu_share']:.0%}")
check(_mu_arm.cell["mu_share"] > 0.3, "独立信道下确实发生了配对")

# --- 17.2d 多进程与 MU 的交互：不是 bug，是 OLLA 看到了更多反馈 ----------
# 放开进程数后同一场景的 MU 占比会**下降**。原因不是配对逻辑变了，而是
# UE 不再被自己的在途反馈挡住 → 发出的 TB 多出 3 倍 → MU OLLA 收到的反馈
# 也多 3 倍、偏置爬得更高 → 实发 MCS 的预测 BLER 更容易越过 0.5 准入线 →
# 更多 TTI 一个可接受的配对都没有。把这条交互显式钉住，免得以后有人看到
# mu_share 掉了就以为配对坏了。
_mu_arm8 = _mu_run(_T_indep, mu_on=True, processes=8)
_mu_reject1 = sum(int(v) for v in
                  _mu_arm.cell["mu_candidate_scoring"]["rejection_reasons"].values())
_mu_reject8 = sum(int(v) for v in
                  _mu_arm8.cell["mu_candidate_scoring"]["rejection_reasons"].values())
print(f"  进程 1→8：已调度 TTI {_mu_arm.cell['scheduled_tti']}→"
      f"{_mu_arm8.cell['scheduled_tti']}，MU 占比 "
      f"{_mu_arm.cell['mu_share']:.3f}→{_mu_arm8.cell['mu_share']:.3f}，"
      f"配对拒绝记录 {_mu_reject1}→{_mu_reject8}")
check(_mu_arm8.cell["scheduled_tti"] > 2 * _mu_arm.cell["scheduled_tti"],
      "8 进程让这两个 UE 发得出 2 倍以上的 TB")
check(_mu_reject8 > 5 * _mu_reject1,
      "配对被拒的 TTI 数同步暴涨——MU 占比下降的原因在准入，不在配对逻辑")
check(_mu_arm8.cell["mu_share"] < _mu_arm.cell["mu_share"],
      "因此 MU 占比下降；这是已知交互，不是配对失效")
check(_mu_arm8.cell["cell_served_mbps"] > _mu_arm.cell["cell_served_mbps"],
      "尽管 MU 占比下降，小区吞吐仍然更高（少了被反馈挡住的空转）")
check(_mu_arm.cell["avg_mcs_first_tx"] < _su_arm.cell["avg_mcs_first_tx"],
      "pair 表口径下配对的代价进了 MCS 决策：MU 的首传 MCS 低于 SU")
check(_mu_arm.config["scheduler"]["mu_accounting"] == "pair_table",
      "pair_table 是唯一的 MU 记账口径，并随结果上报")
check(abs(_mu_arm.cell["mu_olla_mcs_mean"]) > 0
      or _mu_arm.cell["mu_share"] == 0.0,
      "MU 的 OLLA 是独立状态，配对发生时它会动")

# 误块抽签换没换坐标：同一档 MCS 在 SU 真值与 pair 真值上的 BLER 差多少。
_link = _T_indep[0].mu_links[1]
_bler_su, _bler_mu, _delta_db = [], [], []
for _s in range(_MU_SNAP):
    for _side, _u in ((0, 0), (1, 1)):
        _base = float(_T_indep[_u].sinr_tx_db[_s, 1])
        _shift = (float(_link.corr_loss_tx_db[_s, _side])
                  + float(_link.power_loss_db))
        _m = int(la.select_mcs(_base + _shift, table=3, target_bler=0.1).index)
        _su_true = float(_T_indep[_u].sinr_db[_s, 1])
        _mu_true = float(_link.true_sinr_db[_s, _side])
        _bler_su.append(sysm._bler_lookup(_m, _su_true))
        _bler_mu.append(sysm._bler_lookup(_m, _mu_true))
        _delta_db.append(_mu_true - _su_true)
print(f"  同一档 MCS：SU 真值 BLER {np.mean(_bler_su):.4f} vs "
      f"pair 真值 {np.mean(_bler_mu):.4f}（真值差 {np.mean(_delta_db):.2f} dB）")
check(float(np.mean(_delta_db)) < -1.0,
      "pair 真值系统性低于 SU 真值：功率分摊 + 残余干扰确实存在")
check(float(np.mean(_bler_mu)) > float(np.mean(_bler_su)),
      "用 SU 真值抽签会低估误块率——这正是历史口径漏掉的那一半代价")

# **MCS 决策平移量的恒等式**：CorrLoss + powerLoss == pred_MU − pred_SU。
# 也就是 −3.01 这个常数标签在决策里精确抵消，实际用的是矩阵算出来的差。
_su_pred_back = (_link.predicted_sinr_db - _link.corr_loss_tx_db
                 - _link.power_loss_db)
check(bool(np.allclose(
    _link.corr_loss_tx_db + _link.power_loss_db,
    _link.predicted_sinr_db - _su_pred_back, atol=1e-9)),
    "MU 决策平移量恒等于 pred_MU − pred_SU，3.01 dB 只是记账标签")

# 反向对照：高度相关时 SU/MU 自适应必须判 SU 赢，配对率坍塌。
_T_corr = _mu_pair_tables(0.999)
_corr_arm = _mu_run(_T_corr, mu_on=True)
_corr_plan = _corr_arm.cell["su_mu_plan"]
_corr_reject = _corr_arm.cell["mu_candidate_scoring"]["rejection_reasons"]
print(f"  相关系数 0.999：MU 占比 {_corr_arm.cell['mu_share']:.0%}，"
      f"判定单发更划算 {_corr_plan['su_selected']} 个 TTI，"
      f"拒配原因 {_corr_reject}")
check(_corr_arm.cell["mu_share"] < 0.05,
      "信道几乎同向时 ZF 无处零陷，SU/MU 自适应判 SU 赢")
# PR #23 的意图保留：**具体查相关性门**，不是笼统数一下总拒配数。
# 容量路径的 mu_pair_rejection_reasons 随该分支下线，体验路径的同名证据在
# mu_candidate_scoring.rejection_reasons 里。
check(int(_corr_reject.get("correlation_threshold", 0)) > 0,
      f"信道同向时正是相关性门在否决配对（实得 "
      f"{_corr_reject.get('correlation_threshold', 0)} 次）")
check(int(_corr_plan["su_selected"]) > 0
      and sum(int(v) for v in _corr_reject.values()) > 0,
      "拒配对的原因被显式计数，不是静默不配")

# 历史标量口径 se_ratio_legacy 已下线，给了就在配置入口硬失败——上面第 9 节验过。
# 它那条反向对照（「配对完全不压 MCS」）随之删除，因为那条路径现在根本构造不出来。
# 它想守住的性质由上面 _su_arm / _mu_arm 的正向断言覆盖：pair 表口径下 MU 的
# 首传 MCS 必须低于 SU，且 MU 专用 OLLA 确实在动。
check(sysm.SchedulerConfig().mu_accounting == "pair_table",
      "MU 记账口径只剩 pair_table 一个合法值")


# --- 18 现场速率统计口径：buffer 在发送时扣减，不看 TB 对不对 --------------
# **棘轮。** 把 DrbQueue.transmit 换回"只有 ACK 才扣队列"会让这一节全红。
# 用户 2026-09-04 给的三条合同：
#   1) 发出一个包后 buffer 空了，KPI 当场可统计，**完全不管这个包正确与否**；
#   2) 误码与重传对速率的影响主要是**重传占资源**；
#   3) 重传优先级高：发完还没空时 NACK 回来会插队，**拉长掐头去尾时间**。
sect("18  速率统计口径：发送即扣 buffer")

_bd_cls = sysm.TrafficClassConfig("small", 1.0, 100, 1.0, pdb_ms=10.0, is_small=True)

# 合同 1：最后一个 TB 被 NACK，busy period 照样在**发送**那一刻结束
_bd_q = expm.DrbQueue(0, _bd_cls)
_bd_q.arrive(0, 100)
_bd_sent = _bd_q.transmit(2, 120, 100, ack=False)      # 首传就 NACK
check(_bd_sent == 100 and _bd_q.queued_bytes == 0,
      f"NACK 的首传照样把 payload 从 buffer 扣掉（实得 sent={_bd_sent}、"
      f"剩余={_bd_q.queued_bytes}）")
check(_bd_q.active is None and len(_bd_q.done) == 1
      and _bd_q.done[0].last_tx_tti == 2 and _bd_q.done[0].bytes_sent == 100,
      "busy period 在清空 buffer 的那次**发送**结束，不等 ACK")

# 合同 2：重传不带新数据，只占资源
_bd_q2 = expm.DrbQueue(0, _bd_cls)
_bd_q2.arrive(0, 300)
_bd_q2.transmit(1, 100, 100, ack=False)                 # 首传 NACK
_bd_after_first = _bd_q2.queued_bytes
_bd_retx = _bd_q2.transmit(3, 100, 100, ack=True, is_retx=True)
check(_bd_after_first == 200 and _bd_retx == 0
      and _bd_q2.queued_bytes == 200,
      f"重传返回 0 且不动 buffer（首传后 {_bd_after_first} B，重传后 "
      f"{_bd_q2.queued_bytes} B）")
check(_bd_q2.active is not None and _bd_q2.active.tx_attempts == 1
      and len(_bd_q2.active.tx_events) == 1,
      "重传对 DRB 队列是纯空操作：连 tx_attempts 都不加（原因见 18b 节）")

# 合同 3：重传插队 → 掐头去尾时间被拉长 → 速率下降。
# 同一条链路、同一份话务，只把首传 BLER 从 0 抬到 1（强制每个 TB 都要重传）。
_bd_point = sysm.UeLinkTable(
    ue=0, sinr_db=np.array([[16.0]]), mcs=np.array([[20]]),
    se=np.array([[la.MCS_TABLE_3[20].se]]),
    best_rank=np.array([1], dtype=int), best_se=np.array([la.MCS_TABLE_3[20].se]),
    geo_sinr_db=16.0, outage=np.array([False]), mcs_table=3, target_bler=0.1)
# 三条轨迹：首传全对 / 首传全错但重传全对 / 首传与重传都全错。
_bd_old_bler = expm._bler_lookup
_bd_old_retx = la.harq_retransmission_bler
_bd_runs = {}


def _bd_retx_bler(mcs, sinr, *, combining="ir", table=3, _v=1.0):
    return {"bler": float(_v), "lookup_mcs": int(mcs),
            "lookup_sinr_db": float(sinr), "combining": str(combining),
            "table": int(table)}


try:
    for _bd_name, _bd_first, _bd_retx_p in (
            ("no_error", 0.0, 0.0), ("retx_ok", 1.0, 0.0),
            ("retx_fail", 1.0, 1.0)):
        expm._bler_lookup = lambda _m, _s, _v=_bd_first: _v
        la.harq_retransmission_bler = (
            lambda m, s, _p=_bd_retx_p, **kw: _bd_retx_bler(m, s, _v=_p, **kw))
        _bd_runs[_bd_name] = sysm.simulate(
            [_bd_point],
            sys_cfg=sysm.SystemConfig(duration_s=1.0, tdd_pattern="D",
                                      seed=230823),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
            kpi=sysm.KpiConfig(warmup_tti=0))
finally:
    expm._bler_lookup = _bd_old_bler
    la.harq_retransmission_bler = _bd_old_retx
_bd_clean = _bd_runs["no_error"].cell
_bd_dirty = _bd_runs["retx_ok"].cell
_bd_lost = _bd_runs["retx_fail"].cell
print(f"  首传全对 {_bd_clean['cell_served_mbps']:.1f} Mbps / 首传全错重传全对 "
      f"{_bd_dirty['cell_served_mbps']:.1f} Mbps / 首传重传都错 "
      f"{_bd_lost['cell_served_mbps']:.1f} Mbps"
      f"（重传次数 {_bd_dirty.get('retx_attempts')}）")
check(_bd_dirty["retx_attempts"] > 0 and _bd_clean["retx_attempts"] == 0,
      "强制 NACK 轨迹确实产生重传，对照轨迹一次都没有")
# 合同 2：误码影响速率的方式是"重传占资源"，不是"传丢的不算"
check(_bd_dirty["cell_served_mbps"] < 0.6 * _bd_clean["cell_served_mbps"],
      f"重传吃掉资源让吞吐掉到不足六成（{_bd_clean['cell_served_mbps']:.1f} → "
      f"{_bd_dirty['cell_served_mbps']:.1f} Mbps）——这就是误码影响速率的方式")
# 合同 1：正确与否完全不进已发送字节
check(abs(_bd_dirty["cell_served_mbps"] - _bd_lost["cell_served_mbps"]) < 1e-9,
      f"重传全对与重传全丢的已发送字节逐值相同"
      f"（{_bd_dirty['cell_served_mbps']:.4f} vs "
      f"{_bd_lost['cell_served_mbps']:.4f}）——KPI 不看 TB 对不对")
check(_bd_lost["residual_bler"] > 0.99 and _bd_dirty["residual_bler"] < 1e-9,
      f"传丢的部分只体现在 residual_bler（{_bd_dirty['residual_bler']:.3f} → "
      f"{_bd_lost['residual_bler']:.3f}），不从已发送字节里扣回去")


# --- 18b 重传对 DRB 队列必须是纯空操作（含 busy period 的计数器）----------
# **棘轮。** 让 is_retx 的那次去碰 `b.tx_attempts` 就会全红。
#
# 发送即扣减之后，一个 TB 的重传经常落在**它自己那个 busy period 已经关闭之后**
# （那次首传正好把 buffer 清空）。这时 `self.active` 指的是**下一个** busy
# period，给它加 tx_attempts 等于把上一个包的重传记到下一个包头上。后果不是
# 多记一次，而是 burst_metrics 的「len(events)==1 and tx_attempts==1」这道小包
# 闸门被顶开，那个 burst 的吞吐变成 None、**从话统里整个消失**；被丢掉的又恰好
# 是「期间有重传」的慢样本，于是误码越多体验速率反而越高。
_rt_cls = sysm.TrafficClassConfig("small", 1.0, 100, 1.0, pdb_ms=10.0,
                                  is_small=True)
_rt_q = expm.DrbQueue(0, _rt_cls)
_rt_q.arrive(0, 100)
_rt_q.transmit(2, 120, 100, ack=False)          # 首传 NACK，buffer 清空，busy 关闭
check(_rt_q.active is None and len(_rt_q.done) == 1,
      "首传清空 buffer 后 busy period 立刻关闭（不等 ACK）")
_rt_q.arrive(4, 250)                            # 新包 → 新 busy period
_rt_ret = _rt_q.transmit(6, 120, 100, ack=True, is_retx=True)   # 旧 TB 的重传
_rt_new = _rt_q.active
check(_rt_ret == 0 and _rt_new.tx_attempts == 0
      and len(_rt_new.tx_events) == 0 and _rt_new.bytes_sent == 0,
      f"上一个包的重传不碰下一个 busy period 的任何计数器"
      f"（tx_attempts={_rt_new.tx_attempts}）")
_rt_q.transmit(7, 1000, 250, ack=True)          # 新包一次发完
_rt_burst = _rt_q.done[-1]
_rt_m = expm.burst_metrics(_rt_burst, 0.5, "fractional_slot")
check(_rt_m.throughput_mbps is not None
      and _rt_m.throughput_kind == "rel19_fractional_slot",
      f"新的小 burst 仍走 fractional-slot 口径、没有从话统里消失"
      f"（吞吐={_rt_m.throughput_mbps}）")

# 端到端：**误码只能让体验速率变差，不可能变好。**
# 这条是上面那个 bug 最直接的行为学判据——它变红过（首传全错 62.47 Mbps >
# 首传全对 57.57 Mbps），修好之后单调性恢复。
_rt_n = 40
_rt_point = sysm.UeLinkTable(
    ue=0, sinr_db=np.full((_rt_n, 4), 18.0), mcs=np.full((_rt_n, 4), 16),
    se=np.full((_rt_n, 4), la.MCS_TABLE_3[16].se),
    best_rank=np.ones(_rt_n, dtype=int),
    best_se=np.full(_rt_n, la.MCS_TABLE_3[16].se), geo_sinr_db=18.0,
    outage=np.zeros(_rt_n, dtype=bool), mcs_table=3, target_bler=0.1,
    sinr_rbg_db=np.full((_rt_n, 4, 17), 18.0),
    sinr_tx_db=np.full((_rt_n, 4), 18.0),
    sinr_tx_rbg_db=np.full((_rt_n, 4, 17), 18.0))
_rt_old_bler = expm._bler_lookup
_rt_runs = {}
try:
    for _rt_p in (0.0, 0.3, 1.0):
        expm._bler_lookup = lambda _m, _s, _v=_rt_p: _v
        _rt_runs[_rt_p] = sysm.simulate(
            [_rt_point],
            sys_cfg=sysm.SystemConfig(duration_s=3.0, tdd_pattern="DDDSU"),
            traffic=sysm.TrafficConfig(model="ftp3", file_bytes=2_000_000,
                                       arrival_rate_hz=0.8),
            sched=sysm.SchedulerConfig(mu_enabled=False, olla_enabled=False),
            kpi=sysm.KpiConfig(warmup_tti=0), rng=rg.RngBook(3, 0)).cell
finally:
    expm._bler_lookup = _rt_old_bler
_rt_rate = [_rt_runs[p]["ue_experienced_median_mbps"] for p in (0.0, 0.3, 1.0)]
_rt_delay = [_rt_runs[p]["completion_delay_ms_p50"] for p in (0.0, 0.3, 1.0)]
print(f"  首传误块 0/30%/100%：体验中位 "
      f"{_rt_rate[0]:.1f}/{_rt_rate[1]:.1f}/{_rt_rate[2]:.1f} Mbps，"
      f"完成时延 p50 {_rt_delay[0]:.1f}/{_rt_delay[1]:.1f}/{_rt_delay[2]:.1f} ms")
check(_rt_rate[0] > _rt_rate[1] > _rt_rate[2],
      f"误码越多体验速率越低，单调（{_rt_rate[0]:.1f} > {_rt_rate[1]:.1f} > "
      f"{_rt_rate[2]:.1f} Mbps）——重传占资源、拉长掐头去尾时间")
check(_rt_delay[0] < _rt_delay[1] < _rt_delay[2],
      f"完成时延同向变长（{_rt_delay[0]:.1f} < {_rt_delay[1]:.1f} < "
      f"{_rt_delay[2]:.1f} ms）")
check(abs(_rt_runs[0.0]["cell_served_mbps"]
          - _rt_runs[1.0]["cell_served_mbps"]) < 1e-9,
      "已发送字节不随误码变化——发送即计入，KPI 不看这个 TB 对不对")

print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("系统级仿真：话务、调度、HARQ、体验速率口径、守恒对账全部通过。")
