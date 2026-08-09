"""跨模块物理不变量：方向错了就必须硬失败。

直接运行：python tests/test_physics_invariants.py

这些检查刻意不用某个随机场景的“平均趋势”代替物理定律。能逐点成立的量
（噪声、PSD 干扰、负载折算、功率与字节守恒）逐点断言；CSI 老化这类只在
统计意义成立的关系留在 test_csi_aging.py，不伪造逐 realization 单调性。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superwireless import csi_aging as ca  # noqa: E402
from superwireless import experience as ex  # noqa: E402
from superwireless import interference as itf  # noqa: E402
from superwireless import linkadapt as la  # noqa: E402
from superwireless import linklevel as ll  # noqa: E402
from superwireless import measure  # noqa: E402
from superwireless import mumimo as mu  # noqa: E402
from superwireless import rng as rg  # noqa: E402
from superwireless import system as sy  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def section(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


rng = np.random.default_rng(20260809)


# ---------------------------------------------------------------------------
section("1  时间/频率只能平均功率或速率，不能先平均复信道")
h1 = np.array([[[[1.0 + 0.0j]]]], dtype=np.complex64)
h_same = np.concatenate([h1, h1], axis=0)
h_flip = np.concatenate([h1, -h1], axis=0)
r_same = ll.link_performance(h_same, noise_power=0.1, method="identity", max_rank=1)
r_flip = ll.link_performance(h_flip, noise_power=0.1, method="identity", max_rank=1)
check(abs(r_same.spectral_efficiency - r_flip.spectral_efficiency) < 1e-10,
      "公共相位翻转不改变谱效（不再发生复均值相消）")
for _method in ("svd", "svd_wideband", "mrt", "dft", "type1"):
    _a = ll.link_performance(h_same, noise_power=0.1, method=_method, max_rank=1)
    _b = ll.link_performance(h_flip, noise_power=0.1, method=_method, max_rank=1)
    check(abs(_a.spectral_efficiency - _b.spectral_efficiency) < 1e-10,
          f"{_method} 预编码对跨时隙公共相位翻转不敏感")
check(abs(r_same.capacity_bound - r_flip.capacity_bound) < 1e-10,
      "公共相位翻转不改变容量上界")
check(np.allclose(measure.beam_domain_rsrp_db(h_same),
                  measure.beam_domain_rsrp_db(h_flip), atol=1e-10),
      "公共相位翻转不改变波束域 RSRP")
try:
    mu.effective_user_channels([np.ones((2, 4, 8, 2), dtype=np.complex64)])
    check(False, "MU 多时隙输入应拒绝复信道均值")
except ValueError as exc:
    check("逐时隙" in str(exc), "MU 多时隙输入硬拒绝，要求逐时隙算速率")


# ---------------------------------------------------------------------------
section("2  噪声与正半定干扰增大，固定链路性能逐点不升")
h = ((rng.standard_normal((3, 9, 6, 3))
      + 1j * rng.standard_normal((3, 9, 6, 3))) / np.sqrt(2)).astype(np.complex64)
w = ll.compute_precoder(h, method="identity", max_rank=3).w
h_eff = ll.effective_channel(h, w)

noise_grid = (0.01, 0.1, 1.0)
for rx in ("mmse", "zf", "mrc", "irc"):
    se = [float(np.mean(np.sum(np.log2(1.0 + ll.post_equalizer_sinr(
        h_eff, n0, receiver=rx)), axis=1))) for n0 in noise_grid]
    check(bool(np.all(np.diff(se) <= 1e-10)),
          f"{rx.upper()}：噪声功率增大时谱效不升（{[round(x, 3) for x in se]}）")

base_cov = np.diag([0.2, 0.7, 1.3]).astype(np.complex128)
for rx in ("mmse", "irc"):
    se = []
    for scale in (0.0, 0.25, 1.0, 4.0):
        s = ll.post_equalizer_sinr(
            h_eff, 0.1, receiver=rx, interference_cov=scale * base_cov)
        se.append(float(np.mean(np.sum(np.log2(1.0 + s), axis=1))))
    check(bool(np.all(np.diff(se) <= 1e-10)),
          f"{rx.upper()}：PSD 干扰增大时谱效不升（{[round(x, 3) for x in se]}）")

white = np.eye(3) * 0.7
s_mmse = ll.post_equalizer_sinr(h_eff, 0.1, receiver="mmse", interference_cov=white)
s_irc = ll.post_equalizer_sinr(h_eff, 0.1, receiver="irc", interference_cov=white)
check(np.allclose(s_mmse, s_irc, rtol=1e-10, atol=1e-10),
      "白干扰下 IRC 严格退化成 MMSE")

# 几何工作点拆分：固定服务信道/热噪声，只增干扰。SIR 与 SINR 联动后，
# 标定协方差的平均功率必须与 S/I 对账，链路谱效和有色噪声容量都逐点不升。
hi_op = ((rng.standard_normal((2, 3, 9, 6, 3))
          + 1j * rng.standard_normal((2, 3, 9, 6, 3))) / np.sqrt(2)).astype(np.complex64)
s_ref = ll.rank1_reference_power(h)
n_fixed = s_ref / (10.0 ** (20.0 / 10.0))
op_se: list[float] = []
op_cap: list[float] = []
power_ok = True
for sir_db in (30.0, 10.0, 0.0):
    i_target = s_ref / (10.0 ** (sir_db / 10.0))
    sinr_db = 10.0 * np.log10(s_ref / (n_fixed + i_target))
    op = ll.geometric_impairment(
        h, sinr_db, sir_db=sir_db, h_interferers=hi_op)
    cov_power = float(np.mean(np.trace(
        op.interference_cov, axis1=1, axis2=2).real) / h.shape[-1])
    power_ok &= bool(np.isclose(cov_power, i_target, rtol=1e-10))
    power_ok &= bool(np.isclose(op.noise_power, n_fixed, rtol=1e-10))
    rp = ll.link_performance(
        h, noise_power=op.noise_power, interference_cov=op.interference_cov,
        method="svd", receiver="mmse", max_rank=1, rank_threshold=0.0)
    op_se.append(rp.spectral_efficiency)
    op_cap.append(rp.capacity_bound)
check(power_ok, "几何 SINR/SIR 拆出的 N、I 与输入功率逐点对账")
check(bool(np.all(np.diff(op_se) < 0)),
      f"几何干扰增大时默认链路谱效严格下降（{[round(x, 3) for x in op_se]}）")
check(bool(np.all(np.diff(op_cap) < 0)),
      f"几何干扰增大时注水容量严格下降（{[round(x, 3) for x in op_cap]}）")


# ---------------------------------------------------------------------------
section("3  容量是真上界，并随噪声单调下降")
caps = [ll.capacity_upper_bound(h, n0) for n0 in noise_grid]
check(bool(np.all(np.diff(caps) < 0)),
      f"最优注水容量随噪声严格下降（{[round(x, 3) for x in caps]}）")
for method in ("identity", "mrt", "dft", "type1", "svd"):
    r = ll.link_performance(h, noise_power=0.1, method=method, max_rank=3)
    check(r.spectral_efficiency <= r.capacity_bound * (1.0 + 1e-7),
          f"{method} 谱效不超过逐时频最优注水容量")


# ---------------------------------------------------------------------------
section("4  干扰协方差必须 Hermitian/PSD，新增干扰贡献不能是负功率")
hi = ((rng.standard_normal((3, 2, 5, 6, 3))
       + 1j * rng.standard_normal((3, 2, 5, 6, 3))) / np.sqrt(2)).astype(np.complex64)
c2 = ll.interference_covariance(hi[:2], model="precoded", r_uu_source="true")
c3 = ll.interference_covariance(hi, model="precoded", r_uu_source="true")
check(np.allclose(c3, np.swapaxes(c3.conj(), -1, -2), atol=1e-10),
      "R_uu 是 Hermitian")
check(float(np.min(np.linalg.eigvalsh(c3).real)) >= -1e-9,
      "R_uu 是正半定")
check(float(np.min(np.linalg.eigvalsh(c3 - c2).real)) >= -1e-7,
      "增加一个干扰源只会增加一个 PSD 协方差贡献")


# ---------------------------------------------------------------------------
section("5  邻区负载折算端点与方向")
sinr0, sir0 = 10.0, 12.0
loads = np.linspace(0.0, 1.0, 11)
adjusted = np.array([sy.apply_neighbor_load(sinr0, sir0, x) for x in loads])
check(bool(np.all(np.diff(adjusted) <= 1e-10)),
      "邻区 PRB 利用率升高时折算 SINR 不升")
check(abs(adjusted[-1] - sinr0) < 1e-10,
      "邻区负载 100% 精确退化成原几何 SINR")
check(abs(adjusted[0] - sy.interference_free_sinr(sinr0, sir0)) < 1e-10,
      "邻区负载 0 精确退化成无干扰 SNR")

# 直接构造同一 S/N、只增 I，IoT 必须上升，SINR 必须下降。
S, N = 1.0, 0.1
i_grid = np.array([0.01, 0.1, 1.0, 10.0])
sinr = 10 * np.log10(S / (N + i_grid))
sir = 10 * np.log10(S / i_grid)
iot = itf.iot_db(sinr, sir)
check(bool(np.all(np.diff(sinr) < 0) and np.all(np.diff(iot) > 0)),
      "物理干扰功率增大：SINR 严格下降、IoT 严格上升")


# ---------------------------------------------------------------------------
section("6  链路自适应的单调量")
grid = np.linspace(-20.0, 35.0, 221)
all_bler_mono = True
for m in range(28):
    curve = la.bler_curve(m, "newtx", sinr_db=grid)["query"]["bler"]
    all_bler_mono &= bool(np.all(np.diff(curve) <= 1e-12))
check(all_bler_mono, "28 档 NewTx BLER 均随 SINR 单调不升")
chosen = np.array([la.select_mcs(x, table=3).index for x in grid])
check(bool(np.all(np.diff(chosen) >= 0)), "选定 MCS 随 SINR 单调不降")

lut = ex.TbsLookup.build(17, 16, sy.S_SLOT_DL_FRACTION)
check(bool(np.all(np.diff(lut.values, axis=-1) > 0)),
      "D/S × 28 MCS × rank1..4 的 TBS 对 RBG 数严格递增")
for method in ("miesm", "eesm"):
    a = la.effective_sinr(np.array([-5.0, 0.0, 5.0]), method=method, m_order=64)
    b = la.effective_sinr(np.array([-2.0, 3.0, 8.0]), method=method, m_order=64)
    check(b >= a - 1e-10, f"{method.upper()} 对逐元素改善保持单调")


# ---------------------------------------------------------------------------
section("7  SU/MU 总功率归一与接收机噪声方向")
he = ((rng.standard_normal((3, 1, 5, 8))
       + 1j * rng.standard_normal((3, 1, 5, 8))) / np.sqrt(2)).astype(np.complex64)
for method in ("zf", "rzf", "mrt"):
    ww, pp = mu.mu_precoder(he, method=method, noise_power=0.1, total_power=1.0)
    check(np.allclose(np.linalg.norm(ww, axis=1), 1.0, atol=1e-10)
          and np.allclose(pp.sum(axis=1), 1.0, atol=1e-12),
          f"{method.upper()}：方向逐列单位范数、逐 RB 总功率为 1")

h_eval = h[0]
w_svd = ca.svd_precoder(h_eval)
s_lo = ca.mmse_stream_sinr(h_eval, w_svd[:, :, :2],
                            power_per_stream=0.5, noise_power=0.1)
s_hi = ca.mmse_stream_sinr(h_eval, w_svd[:, :, :2],
                            power_per_stream=0.5, noise_power=1.0)
check(bool(np.all(s_hi <= s_lo + 1e-10)), "CSI 老化子模块的 MMSE SINR 随噪声不升")


# ---------------------------------------------------------------------------
section("8  体验模式：QoS-PF 默认退化、低速 CBR 不丢小数字节")
tc = sy.TrafficConfig(model="cbr", cbr_mbps=0.001)
traffic = ex.ExperienceTraffic(tc, 1, 0.5, np.random.default_rng(3))
for tti in range(2000):
    traffic.step(tti)
check(traffic.offered_bytes == 125,
      f"0.001 Mbps × 1 s 精确到达 125 B（实得 {traffic.offered_bytes} B）")

tables: list[sy.UeLinkTable] = []
for u, mcs in enumerate((8, 10, 12, 14)):
    sinr_u = np.full((2, 1), 8.0 + u)
    mcs_u = np.full((2, 1), mcs, dtype=int)
    se_u = np.full((2, 1), la.MCS_TABLES[3][mcs].se)
    tables.append(sy.UeLinkTable(
        ue=u, sinr_db=sinr_u, mcs=mcs_u, se=se_u,
        best_rank=np.ones(2, dtype=int), best_se=se_u[:, 0],
        geo_sinr_db=8.0 + u, outage=np.zeros(2, dtype=bool),
        iot_db=3.0, sir_db=12.0, se_gnb=se_u.copy(), best_se_gnb=se_u[:, 0].copy()))

cfg = sy.SystemConfig(evaluation_mode="experience", duration_s=0.2,
                      tdd_pattern="DDDSU", seed=9)
tr = sy.TrafficConfig(model="mixed", small_ue_share=1.0,
                      small_file_bytes=500, small_arrival_rate_hz=250.0)
kpi = sy.KpiConfig(warmup_tti=0)
base_sched = dict(mu_enabled=False, olla_enabled=False, pf_accounting="scheduled_tbs")
pf = sy.simulate(tables, sys_cfg=cfg, traffic=tr,
                 sched=sy.SchedulerConfig(algorithm="pf", **base_sched), kpi=kpi,
                 rng=rg.RngBook(77, 0))
qpf = sy.simulate(tables, sys_cfg=cfg, traffic=tr,
                  sched=sy.SchedulerConfig(algorithm="qos_pf", **base_sched), kpi=kpi,
                  rng=rg.RngBook(77, 0))
seq_pf = [(x["tti"], x["ue"], x["n_rbg"]) for x in pf.diagnostics["allocation_sample"]]
seq_qpf = [(x["tti"], x["ue"], x["n_rbg"]) for x in qpf.diagnostics["allocation_sample"]]
check(seq_pf == seq_qpf and pf.cell["cell_served_mbps"] == qpf.cell["cell_served_mbps"],
      "QoS-PF 默认 alpha=beta=1、gamma=0、w=1 时逐分配退化经典 PF")
check(pf.cell["accounting_error_pct"] == 0.0
      and pf.diagnostics["rbg_overlap_violations"] == 0
      and 0.0 <= pf.cell["resource_utilization"] <= 1.0,
      "体验模式字节守恒、RBG 不重叠、资源利用率在 [0,1]")


print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for item in FAILED:
        print("  - " + item)
    raise SystemExit(1)
print("跨模块物理不变量全部通过。")
