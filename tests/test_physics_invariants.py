"""跨模块物理不变量：方向错了就必须硬失败。

直接运行：python tests/test_physics_invariants.py

这些检查刻意不用某个随机场景的“平均趋势”代替物理定律。能逐点成立的量
（噪声、PSD 干扰、负载折算、功率与字节守恒）逐点断言；CSI 老化这类只在
统计意义成立的关系留在 test_csi_aging.py，不伪造逐 realization 单调性。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from scipy.stats import t as student_t

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superran import amc_policy as ap  # noqa: E402
from superran import beamforming as bf  # noqa: E402
from superran import csi_aging as ca  # noqa: E402
from superran import experience as ex  # noqa: E402
from superran import generate as gen  # noqa: E402
from superran import interference as itf  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import linklevel as ll  # noqa: E402
from superran import measure  # noqa: E402
from superran import mumimo as mu  # noqa: E402
from superran import rng as rg  # noqa: E402
from superran import sionna_rt as srt  # noqa: E402
from superran import system as sy  # noqa: E402

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
s_ref = ll.prebeam_reference_power(h)
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

# SISO 有色损伤可解析：白化后奇异值已经含 1/(N+I)，容量公式不能再除一次 N。
h_siso = np.ones((1, 1, 1, 1), dtype=np.complex64)
c_colored = ll.capacity_upper_bound(
    h_siso, 0.5, interference_cov=np.array([[1.5]], dtype=np.complex128))
check(abs(c_colored - np.log2(1.0 + 1.0 / 2.0)) < 1e-10,
      "有色容量白化后不重复除噪声（SISO 解析值精确一致）")

# 报告 SINR 必须逐层复原报告 SE；线性平均 SINR 在频选信道上做不到这一点。
h_freq = np.array([[[[0.1]]], [[[10.0]]]], dtype=np.complex64).transpose(1, 0, 2, 3)
r_freq = ll.link_performance(
    h_freq, noise_power=1.0, method="identity", max_rank=1)
se_from_reported_sinr = np.log2(1.0 + 10.0 ** (r_freq.sinr_per_layer_db[0] / 10.0))
check(abs(se_from_reported_sinr - r_freq.se_per_layer[0]) < 1e-10,
      "逐层报告的是速率等效 SINR，可精确反算逐层谱效")

# Rank 必须随工作点变化：弱第二层在低 SNR 会分走功率，高 SNR 才值得开启。
h_rank = np.zeros((1, 1, 2, 2), dtype=np.complex64)
h_rank[0, 0] = np.diag([1.0, 0.2])
r_low = ll.link_performance(
    h_rank, noise_power=1.0, method="svd", max_rank=2)
r_high = ll.link_performance(
    h_rank, noise_power=1e-4, method="svd", max_rank=2)
check(r_low.rank == 1 and r_high.rank == 2,
      f"Rank 按预计谱效自适应工作点（低 SNR={r_low.rank} / 高 SNR={r_high.rank}）")

# 小样本均值 CI 用 Student-t；n=3 时不能偷用 1.96 把区间缩窄。
mc_small = ll.monte_carlo(
    np.asarray([h_siso, 2 * h_siso, 4 * h_siso]),
    noise_powers=np.ones(3), method="identity", max_rank=1)
expected_half = float(student_t.ppf(0.975, 2) * mc_small.se_std / np.sqrt(3))
actual_half = (mc_small.se_ci95[1] - mc_small.se_ci95[0]) / 2.0
check(abs(actual_half - expected_half) < 1e-10,
      "n=3 蒙特卡洛均值 CI 使用 Student-t 临界值")


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

# 数据集没有邻区被服务 UE 的信道。默认波束必须独立于受害 UE；旧口径另留成
# victim_aligned 故障复现，不能再冒充“邻区服务自己的用户”。
hi_aniso = np.zeros((1, 1, 1, 2, 2), dtype=np.complex64)
hi_aniso[0, 0, 0] = np.diag([10.0, 1.0])
c_ind = ll.interference_covariance(hi_aniso, model="precoded", seed=7)
c_victim = ll.interference_covariance(hi_aniso, model="victim_aligned", seed=7)
check(not np.allclose(c_ind, c_victim),
      "默认邻区波束与受害 UE 交叉信道独立，不再偷偷做 victim-aligned")

# 只有一个真实快照时，请求 100 个样本也只能用 1 个；不得加人工抖动造新秩。
c_s1 = ll.interference_covariance(
    hi_aniso, model="precoded", r_uu_source="sample",
    r_uu_samples=1, diagonal_loading=0.0, seed=11)
c_s100 = ll.interference_covariance(
    hi_aniso, model="precoded", r_uu_source="sample",
    r_uu_samples=100, diagonal_loading=0.0, seed=11)
check(np.allclose(c_s1, c_s100, atol=1e-12)
      and np.linalg.matrix_rank(c_s100[0], tol=1e-10) == 1,
      "R_uu 样本估计只用真实快照，快照不足不再用 5% 抖动伪造秩")


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

# 三种功率约束不是一个模式的三个参数，而是三个可独立审计的物理矩阵。
# 项目矩阵是 Q[F,antenna,stream]，所以用户口径的“列归一”在这里对应天线行归一。
z = rng.standard_normal((7, 64, 4)) + 1j * rng.standard_normal((7, 64, 4))
q_dir, _ = np.linalg.qr(z)
power_rows: dict[str, bf.PowerDiagnostics] = {}
for mode in ("ebf", "pebf", "nebf"):
    _q, _wm, _pd = bf.equal_power_weights(q_dir, mode=mode, total_power=1.0)
    power_rows[mode] = _pd
    check(float(np.max(_pd.total_power_used)) <= 1.0 + 1e-10,
          f"{mode.upper()}：总发射功率不越界")
check(float(np.max(power_rows["pebf"].per_antenna_power)) <= 1 / 64 + 1e-10,
      "PEBF：最大天线满足 P/M，且只做全局缩放")
check(np.allclose(power_rows["nebf"].per_antenna_power, 1 / 64, atol=1e-12),
      "NEBF：每根非零天线都恰好使用 P/M")
check(np.allclose(power_rows["nebf"].total_power_used, 1.0, atol=1e-12),
      "NEBF：64 根非零天线时总功率用满")
check(np.allclose(power_rows["pebf"].orthogonality_error,
                  power_rows["ebf"].orthogonality_error, atol=1e-12),
      "PEBF：全局缩放保持流间几何关系")
check(float(np.mean(power_rows["nebf"].orthogonality_error))
      > float(np.mean(power_rows["ebf"].orthogonality_error)) + 1e-4,
      "NEBF：逐天线归一会改变流间正交性")

# EBF 是历史默认基线，显式指定与省略参数必须逐位一致。
h_ebf = ((rng.standard_normal((1, 5, 8, 3))
          + 1j * rng.standard_normal((1, 5, 8, 3))) / np.sqrt(2))
ebf_default = ll.link_performance(h_ebf, noise_power=0.1, max_rank=3)
ebf_explicit = ll.link_performance(
    h_ebf, noise_power=0.1, max_rank=3, power_constraint="ebf")
check(ebf_default.rank == ebf_explicit.rank
      and np.array_equal(ebf_default.sinr_per_rb_db, ebf_explicit.sinr_per_rb_db)
      and ebf_default.spectral_efficiency == ebf_explicit.spectral_efficiency,
      "显式 EBF 与历史默认路径逐位一致")
_pebf_batch = ll.compare_precoders(
    h_ebf[None], methods=("svd",), snr_db=10.0,
    power_constraint="pebf")
check(_pebf_batch["svd"]["power_constraint"] == "pebf",
      "批量 Monte Carlo/compare_precoders 真正下传并记录每天线功率模式")

# 64T SU 的代表例：NEBF 使用全部每天线功率，速率接近 EBF；PEBF 被峰值天线
# 限住，只使用约 20% 总功率。这里断言具体 realization，不用“总体趋势”救结论。
rg_su = np.random.default_rng(0)
h_su64 = ((rg_su.standard_normal((1, 17, 64, 1))
           + 1j * rg_su.standard_normal((1, 17, 64, 1))) / np.sqrt(2))
su_power = {
    mode: ll.link_performance(
        h_su64, noise_power=0.1, method="svd", max_rank=1,
        power_constraint=mode)
    for mode in ("ebf", "pebf", "nebf")
}
check(abs(su_power["nebf"].spectral_efficiency
          / su_power["ebf"].spectral_efficiency - 1.0) < 0.05,
      "64T SU：NEBF 谱效与 EBF 相差 <5%")
check(su_power["nebf"].spectral_efficiency
      > su_power["pebf"].spectral_efficiency + 1.5,
      "64T SU：NEBF 明显高于受峰值天线限制的 PEBF")

# 强相关 MU + 高 SNR + 单接收天线 UE 是 NEBF 破坏 ZF 的反向哨兵：
# 接收侧没有多余自由度替发射机再置零，因此即使 NEBF 用满功率，残余干扰
# 也能让它输给只用部分功率但保持零陷的 PEBF。
rg_mu = np.random.default_rng(0)
shape_mu = (1, 4, 4, 1)
h_mu0 = ((rg_mu.standard_normal(shape_mu) + 1j * rg_mu.standard_normal(shape_mu))
         / np.sqrt(2))
h_mu1 = h_mu0 + 0.001 * (
    rg_mu.standard_normal(shape_mu) + 1j * rg_mu.standard_normal(shape_mu)) / np.sqrt(2)
mu_pebf = mu.mu_link_performance(
    [h_mu0, h_mu1], noise_power=1e-8, streams_per_user=1,
    criterion="all", precoder="zf", power_constraint="pebf")
mu_nebf = mu.mu_link_performance(
    [h_mu0, h_mu1], noise_power=1e-8, streams_per_user=1,
    criterion="all", precoder="zf", power_constraint="nebf")
check(mu_nebf.leakage_ratio > 0.4 and mu_pebf.leakage_ratio < 1e-10,
      "强相关 MU：NEBF 产生残余干扰，PEBF 保持 ZF 零陷")
check(mu_nebf.sum_se < mu_pebf.sum_se,
      "强相关 MU：存在 NEBF < PEBF 的确定性反例")

# 每个 UE 的几何工作点不同，MU 分母必须使用逐用户噪声，不能拿 UE0 代替全组。
he_orth = np.zeros((2, 1, 1, 2), dtype=np.complex128)
he_orth[0, 0, 0, 0] = 1.0
he_orth[1, 0, 0, 1] = 1.0
mu_noise = mu.mu_link_performance_from_effective(
    he_orth, he_orth, noise_power=np.array([0.1, 1.0]), precoder="zf",
    rb_per_rbg=1)
check(np.allclose(mu_noise.sinr_per_user_db, [10 * np.log10(5.0),
                                             10 * np.log10(0.5)], atol=1e-10),
      "MU 使用逐用户噪声功率（正交两用户解析 SINR 精确一致）")


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

cfg = sy.SystemConfig(duration_s=0.2,
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

# 未来快照无论怎么改，都不能改变 snapshot 0 的 PMI 权、BF gain 或 CQI。
h0 = np.zeros((17, 4, 2), dtype=np.complex64)
h0[:, 0, 0], h0[:, 1, 1] = 1.0, 0.7
h_future_a = np.zeros_like(h0)
h_future_a[:, 2, 0], h_future_a[:, 3, 1] = 20.0, 15.0
h_future_b = np.zeros_like(h0)
h_future_b[:, 1, 0], h_future_b[:, 0, 1] = 25.0, 18.0
ta = sy.build_link_tables(
    [np.stack([h0, h_future_a])], [10.0], num_snapshots=2,
    max_rank=2, rb_per_rbg=1, power_constraint="ebf")[0]
tb = sy.build_link_tables(
    [np.stack([h0, h_future_b])], [10.0], num_snapshots=2,
    max_rank=2, rb_per_rbg=1, power_constraint="ebf")[0]
check(np.allclose(ta.bf_gain_db[0], tb.bf_gain_db[0], atol=1e-10)
      and np.allclose(ta.pmi_sinr_db[0], tb.pmi_sinr_db[0], atol=1e-10),
      "snapshot 0 的 PMI/BF gain 不读取未来信道")
check(np.array_equal(ta.cqi_index_per_snapshot[0], tb.cqi_index_per_snapshot[0])
      and np.allclose(ta.sinr_tx_db[0], tb.sinr_tx_db[0], atol=1e-10),
      "snapshot 0 的 CQI 滤波与发送 SINR 不读取未来样本")

# 过载观测窗：超过 deadline 仍未完成的是确定 miss；未到 deadline 的才右删失。
over_cfg = sy.SystemConfig(
    duration_s=0.25, tdd_pattern="DDDSU")
over = sy.simulate(
    tables[:1], sys_cfg=over_cfg,
    traffic=sy.TrafficConfig(model="cbr", cbr_mbps=1000.0),
    sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False,
                             pf_accounting="scheduled_tbs"),
    kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(91, 0))
check(over.cell["deadline_missed_incomplete_arrival_objects"] > 0
      and over.cell["pdb_right_censored_arrival_objects"] > 0
      and over.cell["pdb_decidable_arrival_objects"]
      > over.cell["completed_arrival_objects"],
      "PDB 分母纳入已超时未完成对象，并把未到 deadline 的对象单列右删失")
# **过载 ≠ 饿死，但标准 KPI 不该替它编一个数。** 这个 UE 一个 burst 都没传完
# （measured=0），TS 28.552 的样本在 buffer emptied 事件上形成，因此标准字段
# 没有样本——这是标准的定义，不是缺陷。它一直在被服务（约 33.6 Mbps）这件事，
# 由**工程字段**如实给出：active_window_goodput_mbps 与 ue_served_mean_mbps。
# 曾经把在飞段混进标准字段，等于让工程量顶标准的名字，已撤回。
check(over.cell["ue_experience_eligible"] == 1
      and over.cell["ue_experience_measured"] == 0,
      "有到达但无完成 burst 的 UE 仍留在体验分布里，删失由 eligible/measured 之差暴露")
check(over.cell["cell_experienced_mbps"] == 0.0,
      "标准口径无样本：zero-inclusive 分布里该 UE 记 0，不替它编数")
check(int(over.cell["drb_throughput_completed_bursts"]) == 0
      and int(over.cell["drb_throughput_inflight_bursts"]) > 0,
      "样本构成如实上报：0 个已完成、有在飞——标准 KPI 无样本这件事看得见")
check(over.cell["active_window_goodput_mbps"] is not None
      and abs(float(over.cell["active_window_goodput_mbps"])
              - float(over.cell["cell_served_mbps"]))
      <= 0.20 * float(over.cell["cell_served_mbps"]),
      "工程字段给出它实际的速率（与 cell_served_mbps 同量级）")

# **外生到达与时隙类型无关。** 话务进缓冲区是 UE 侧的事，和这个 TTI 是 D/S/U
# 没有关系；只有"发不发得出去"才看时隙。历史上到达只在下行 TTI 走了一遍，
# DDDSU 就静默少 20% 的 CBR——offered load 被打折，而结果里看不出来。
cbr = sy.simulate(
    tables[:1],
    sys_cfg=sy.SystemConfig(duration_s=0.05, tdd_pattern="DDDSU"),
    traffic=sy.TrafficConfig(model="cbr", cbr_mbps=1.0),
    sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False),
    kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(92, 0))
check(abs(cbr.cell["offered_mbps"] - 1.0) < 1e-9,
      "D/S/U 每个 TTI 都维护业务到达，DDDSU 不再漏掉 U 时隙的 20% CBR")

# **"容量仿真"必须是同一条路径上的一个话务配置，不是另一套语义。**
# 把这条 revert 掉（恢复独立容量分支）就会红：满缓冲下按需 RBG 必须退化成
# 全带宽、RBG 全部用满，且体验类 KPI 报 None 而不是 0。
#
# **注意下面这条 == 1.0 只在「频选关 + MU 关」成立**，它是退化解不是普遍规律。
# 普遍规律在本节末尾那张 2x2 表里，别拿这一条去理解满缓冲。
fb = sy.simulate(
    tables,
    sys_cfg=sy.SystemConfig(duration_s=0.05, tdd_pattern="DDDD"),
    traffic=sy.TrafficConfig(model="full_buffer"),
    sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False,
                             frequency_selective="off"),
    kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(93, 0))
check(fb.config["system"]["model_version"] == "experience_v2",
      "full_buffer 走的就是体验路径，没有第二个 model_version")
check(abs(float(fb.cell["scheduled_ues_per_busy_tti"]) - 1.0) < 1e-9,
      "频选关+MU关时，满缓冲下按需 RBG 退化成全带宽：每忙 TTI 恰好 1 个 SU")
check(abs(float(fb.cell["resource_utilization"]) - 1.0) < 1e-9,
      "满缓冲下 RBG 全部用满，没有留空的尾料")
check(float(fb.cell["cell_served_mbps"]) > 0.0,
      "容量口径仍然照常输出 cell_served_mbps")

# **标准与工程必须分字段。** TS 128 552 V19.5.0 p54：样本只在 DRB DL buffer
# emptied 事件上形成。full buffer 下 buffer 永不排空 ⇒ 标准字段没有样本，报
# None 是对的。把在飞段混回 drb_throughput_rel19_mbps 就会让下面第一条红。
check(fb.cell["drb_throughput_rel19_mbps"] is None
      and fb.cell["cell_experienced_mbps"] is None,
      "full buffer 下标准 KPI 无样本、报 None——工程量不许顶标准的名字")
check(int(fb.cell["drb_throughput_completed_bursts"]) == 0
      and int(fb.cell["drb_throughput_inflight_bursts"]) == len(tables),
      "样本构成如实上报：0 个已完成、每个 UE 一个在飞")
# **但用户仍然拿得到数**，走两个工程字段，任何话务下都有值。
_fb_active = fb.cell["active_window_goodput_mbps"]
_fb_served_mean = float(fb.cell["ue_served_mean_mbps"])
check(_fb_active is not None and float(_fb_active) > 0.0,
      "full buffer 下工程口径 active_window_goodput_mbps 有值")
# **这条验的是分母，不是分子——别再当成交叉验证。**
# 两个数的分子是**同一份**发送净荷记账（experience.py 里 tr.transmit() 返回的
# payload，一边累进 served_measured、一边塞进 TxEvent）。只有分母不同：
# 一个除观测窗长，一个除首传到末次窗内发送的跨度。所以它能证明的只有
# 「满缓冲下每个 UE 确实从头忙到尾，两个分母重合」——这是关于话务模型的陈述。
# **净荷记账整体缩放时这条读数不变**（实测把记账放大 10%，小区吞吐错 10.6%，
# 这条仍读 0.156%）。真正能抓记账缩放的是有限话务下的字节守恒
# （到达 = 已发 + 积压，到达量由话务模型独立决定），见本节末尾。
check(abs(float(_fb_active) - _fb_served_mean) <= 0.10 * _fb_served_mean,
      "满缓冲下每个 UE 从头忙到尾，两个口径的分母重合（差 <10%）"
      "——**这验的是分母，不验净荷记账**")

# **用户体验速率在 full buffer 下是有定义的，只是走另一个口径。**
# ITU-R M.2412 / TR 38.913：每 UE 已发送净荷 / 观测窗长，5% 分位就是
# cell-edge user throughput。把 ue_served_* 删掉或让它在 full buffer 下返回
# None，这几条就会红——满缓冲评估的主指标不许消失。
_fb_served = [float(row["served_mbps"]) for row in fb.users]
check(all(np.isfinite(fb.cell[k]) and fb.cell[k] > 0 for k in
          ("ue_served_mean_mbps", "ue_served_median_mbps", "ue_served_p5_mbps")),
      "full buffer 下用户体验速率（ITU 口径）照常有值，不是 None")
check(fb.cell["ue_served_p5_mbps"] <= fb.cell["ue_served_median_mbps"] + 1e-9
      <= fb.cell["ue_served_mean_mbps"] + abs(fb.cell["ue_served_mean_mbps"]),
      "5% 分位不高于中位；三个统计量来自同一个跨 UE 分布")
check(abs(sum(_fb_served) - float(fb.cell["cell_served_mbps"])) < 1e-6,
      "各 UE 已服务速率之和恰好等于小区吞吐——用户级与小区级同源")
check(_fb_served[0] > _fb_served[-1],
      "几何最好的 UE 拿到的用户吞吐高于最差的（PF 不抹平几何差异）")

# --- 满缓冲下"每忙 TTI 服务几个用户"的真实规律：一张 2x2 表 ------------------
# **上面那条 == 1.0 是退化解，不是普遍规律。** 本 PR 早先把它写成了物理定律，
# 而守卫它的断言恰好跑在「频选关 + MU 关」——两个会让它失败的开关都关掉了。
# 真实规律是：**满缓冲保证的是 RBG 用满，不是只服务一个用户。**
#   * 频选打开：调度器把"少几个但信道更好的 RBG"给第一个用户、余料给下一个；
#   * MU 打开：被准入的 MU TTI 会配对两个用户；相关性、MCS、PF 增益等门限仍可拒配。
# 出厂默认是 frequency_selective="auto" + mu_enabled=False，在真实锚点数据集上
# 实测每忙 TTI 1.14；开 MU 是 1.86、小区吞吐 +64%。
# 这里用互补频选的合成信道把四个格子都钉住，谁把任何一格改回 1.0 都会红。
_fsrng = np.random.default_rng(7)
_fsH = []
for _u in range(4):
    _h = ((_fsrng.standard_normal((2, 272, 8, 2))
           + 1j * _fsrng.standard_normal((2, 272, 8, 2))) / np.sqrt(2))
    _g = np.full(272, 0.15)
    _g[_u * 68:(_u + 1) * 68] = 1.0          # UE u 只在自己那 1/4 频段上强
    _fsH.append((_h * _g[None, :, None, None]).astype(complex))
_fsT = sy.build_link_tables(_fsH, [12.0] * 4, num_snapshots=2, mu_enabled=True)


def _fs_arm(fs: str, mu: bool):
    return sy.simulate(
        _fsT,
        sys_cfg=sy.SystemConfig(duration_s=0.05, tdd_pattern="DDDD", num_rbg=17),
        traffic=sy.TrafficConfig(model="full_buffer"),
        sched=sy.SchedulerConfig(olla_enabled=False, mu_enabled=mu,
                                 frequency_selective=fs),
        kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(93, 0)).cell


_a_ff, _a_sf = _fs_arm("off", False), _fs_arm("on", False)
_a_fm, _a_sm = _fs_arm("off", True), _fs_arm("on", True)
_ues = lambda c: float(c["scheduled_ues_per_busy_tti"])  # noqa: E731
print(f"  满缓冲每忙 TTI 服务 UE 数：频选关/MU关 {_ues(_a_ff):.4f}、"
      f"频选开/MU关 {_ues(_a_sf):.4f}、频选关/MU开 {_ues(_a_fm):.4f}、"
      f"频选开/MU开 {_ues(_a_sm):.4f}")
check(abs(_ues(_a_ff) - 1.0) < 1e-9,
      "频选关+MU关：每忙 TTI 恰好 1 个（这是退化解）")
check(_ues(_a_sf) > 1.0 + 1e-9,
      f"**频选开就 >1**：调度器按 RBG 把带宽切给多个用户（实得 {_ues(_a_sf):.4f}）")
_fm_mu_share = float(_a_fm["mu_share"])
check(_fm_mu_share > 0.0
      and abs(_ues(_a_fm) - (1.0 + _fm_mu_share)) < 1e-9,
      f"**MU 开后确实发生二用户配对**：平均服务数 = 1 + MU TTI 占比"
      f"（实得 {_ues(_a_fm):.4f} = 1 + {_fm_mu_share:.4f}）")
check(_ues(_a_sm) > _ues(_a_fm) + 1e-9,
      f"频选与 MU 叠加还会更多（实得 {_ues(_a_sm):.4f}）")
# **四种配置里真正不变的是这个**：满缓冲把 RBG 用满，没有留空的尾料。
check(all(abs(float(c["resource_utilization"]) - 1.0) < 1e-9
          for c in (_a_ff, _a_sf, _a_fm, _a_sm)),
      "满缓冲下 RBG 全部用满——**这才是满缓冲真正保证的不变量**")

# --- 满缓冲下字节守恒不适用，必须报 None 而不是 0.0 -------------------------
# 它比的是「到达 = 已发 + 积压」，而满缓冲的 offered 是无界的种子字节。
# 旧容量分支在这里算出 3.7e21（垃圾数冒充测量）；合并初版改成硬编码 0.0
# （漂亮数冒充测量），**同一种错**，而且我还拿这个 0.0 当过"对账正确"的证据。
# 旁边三个兄弟字段满缓冲下都报 None，这个也必须一致。
check(_a_ff["accounting_error_pct"] is None,
      "满缓冲下字节守恒不适用，accounting_error_pct 报 None 而不是 0.0")
check(fb.diagnostics["byte_conservation"]["error_pct"] is None
      and fb.diagnostics["byte_conservation"]["not_applicable_reason"] is not None,
      "byte_conservation 同样报 None，并说明为什么不适用")

# 反向对照：同一条路径换成有限话务，两个口径给出**不同**的数。
# 它们不是同一个量的两种精度——轻载下 UE 全时段平均远低于它 burst 在飞时的速率。
_fin = sy.simulate(
    tables,
    sys_cfg=sy.SystemConfig(duration_s=0.4, tdd_pattern="DDDD"),
    traffic=sy.TrafficConfig(model="ftp3", arrival_rate_hz=5.0, file_bytes=50_000),
    sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False,
                             frequency_selective="off"),
    kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(94, 0))
check(np.isfinite(_fin.cell["ue_served_mean_mbps"])
      and _fin.cell["cell_experienced_mbps"] is not None,
      "有限话务下两个口径同时有值")
check(_fin.cell["ue_served_mean_mbps"] < _fin.cell["cell_experienced_mbps"],
      "轻载下 UE 全时段平均低于 busy-period 吞吐——证明它们是两个定义，不可互换")
check(int(_fin.cell["drb_throughput_inflight_bursts"]) == 0
      and abs(float(_fin.cell["drb_throughput_inflight_share"])) < 1e-9,
      "轻载下 burst 都传完了，没有在飞样本——修复对良性场景零扰动")

# **反向哨兵：只统计已完成 burst 会系统性偏乐观。** 过载下慢 burst 更不容易
# 传完，把它们丢掉等于只留漂亮样本。构造一个大部分 burst 传不完的有限话务，
# 断言"只看已完成"给出的数明显高于把在飞段也计入的数。
_ovl = sy.simulate(
    tables,
    sys_cfg=sy.SystemConfig(duration_s=1.0, tdd_pattern="DDDD"),
    traffic=sy.TrafficConfig(model="ftp3", arrival_rate_hz=5.0,
                             file_bytes=500_000),
    sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False,
                             frequency_selective="off"),
    kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(95, 0))
check(int(_ovl.cell["drb_throughput_inflight_bursts"]) > 0
      and int(_ovl.cell["drb_throughput_completed_bursts"]) > 0,
      "过载场景里已完成与在飞 busy period 同时存在（哨兵前提成立）")
# **只断言两个数不同，不断言方向。** 偏差方向随场景而定：慢 burst 更不容易传完
# 会让 completed-only 偏乐观；但若在飞的恰好是刚起步的好信道用户，方向就反过来。
# 实测两种都见过（中载 11.59→8.78；本例 126.0→131.9）。
#
# **纠正（审核 2026-09-04 指出）**：这两个数的差**不是**"含不含在飞样本"，
# 在飞样本从来不进 drb_throughput_rel19_mbps——那正是本 PR 的核心约定。
# 它们差的是**聚合权重**：
#   * drb_throughput_rel19_mbps        = 所有已完成 burst 的**汇池均值**
#   * cell_experienced_completed_only  = 每个 UE 先各自平均，再对 UE 取均值
# 同一个样本集、两种加权，burst 数在 UE 之间不均时必然不等。
# ---------------------------------------------------------------------------
# **反例棘轮：没有 buffer-emptied 事件的在飞段不许冒充标准样本。**
# #21 改成"发送即扣队列"后，重传对 DRB 队列是空操作；旧的"NACK 留队、重传
# 清空"构造已经失效。新反例在同一个 busy period 里多放 1 B，只发送其中 100 B：
#   * 没有那 1 B → buffer emptied → 形成标准小 burst 样本；
#   * 有那 1 B   → 仍在飞 → 只能形成 engineering_active_window 样本。
# 首传故意取 NACK，证明工程发送速率与标准样本边界都不等 ACK；末次失败只影响
# residual_bler，不能把发送过的 100 B 放回队列。
_TC = type("_Tc", (), {"name": "ratchet", "pdb_ms": 0.0})()


def _one_byte_tail_queue(trailing_byte: bool) -> ex.DrbQueue:
    q = ex.DrbQueue(ue=0, traffic_class=_TC)
    q.arrive(0, 100)
    if trailing_byte:
        q.arrive(0, 1)
    q.transmit(0, scheduled_bytes=1000, payload_bytes=100, ack=False)
    return q


_q_no, _q_yes = _one_byte_tail_queue(False), _one_byte_tail_queue(True)
check(len(_q_no.done) == 1 and _q_no.active is None
      and len(_q_yes.done) == 0 and _q_yes.active is not None,
      "反例构造成立：1 B 尾随到达把 busy period 从「已完成」变成「在飞」")
check(_q_yes.active.tx_events[-1].padding_bytes == 900
      and not _q_yes.active.tx_events[-1].ack,
      "在飞 busy period 的 NACK 首传带 900 B padding，但发送的 100 B 仍已记账")
_m_yes = ex.active_window_goodput(_q_yes.active, 0.5, 0)
check(_m_yes.throughput_kind == "engineering_active_window"
      and "rel19" not in str(_m_yes.throughput_kind)
      and _m_yes.throughput_mbps is not None,
      "NACK 的在飞发送只形成工程样本，不冒充 TS 28.552 样本")
check(ex.burst_metrics(_q_yes.active, 0.5).throughput_kind is None,
      "没有 buffer-emptied 事件的在飞 busy period 不产生标准样本")
check(ex.burst_metrics(_q_no.done[0], 0.5).throughput_kind == "rel19_fractional_slot",
      "同样的 NACK 首传只要清空 buffer，就按发送时口径形成标准小 burst 样本")
# 端到端：同一场景加 1 B，标准字段的样本数不许改变。
_ratchet_cfg = dict(
    sys_cfg=sy.SystemConfig(duration_s=0.05, tdd_pattern="D"),
    sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False),
    kpi=sy.KpiConfig(warmup_tti=0))
_r_small = sy.simulate(
    tables[:1], traffic=sy.TrafficConfig(model="cbr", cbr_mbps=1e-3),
    rng=rg.RngBook(96, 0), **_ratchet_cfg)
check(int(_r_small.cell["drb_throughput_completed_bursts"])
      + int(_r_small.cell["drb_throughput_inflight_bursts"])
      == int(_r_small.cell["drb_throughput_completed_bursts"])
      + int(_r_small.cell["active_window_goodput_samples"]),
      "在飞计数与工程样本数同源，标准样本数独立于它")

check(_ovl.cell["cell_experienced_completed_only_mbps"] is not None
      and abs(float(_ovl.cell["cell_experienced_completed_only_mbps"])
              - float(_ovl.cell["drb_throughput_rel19_mbps"])) > 1e-9,
      "汇池均值与逐用户均值不等——两者同一样本集、两种加权，不可混引")


def test_s_slot_fraction_single_source_of_truth() -> None:
    """报告占比和调度承载必须读取同一个显式 S 时隙系数。"""
    default = sy.SystemConfig(tdd_pattern="DDDSU")
    custom = sy.SystemConfig(tdd_pattern="DDDSU", s_slot_dl_fraction=0.82)
    assert abs(default.s_slot_dl_fraction - sy.S_SLOT_DL_FRACTION) < 1e-12
    assert abs(default.dl_ratio - (3 + sy.S_SLOT_DL_FRACTION) / 5) < 1e-12
    assert abs(custom.dl_ratio - (3 + 0.82) / 5) < 1e-12
    assert abs(sy.infer_s_slot_fraction("DDDSU") - 10 / 14) < 1e-12
    assert abs(sy.infer_s_slot_fraction("DDDDDDDSUU") - 6 / 14) < 1e-12


test_s_slot_fraction_single_source_of_truth()


def test_bler_factory_and_eesm_are_explicit() -> None:
    """BLER 后端选择和频选 SINR 压缩都必须是显式、可审计输入。"""
    model = la.make_bler_model(
        1, config={"c": 2.5, "implementation_loss_db": 1.5})
    assert isinstance(model, la.BlerModel)
    assert model.c == 2.5 and model.implementation_loss_db == 1.5
    assert isinstance(la.make_bler_model(3), la.CurveBlerModel)
    assert la.eesm_compress(
        np.array([0.0, 5.0, 10.0]), beta=np.array([1.0, 10.0])).shape == (2,)


test_bler_factory_and_eesm_are_explicit()


def test_mu_admission_gates_are_explicit_and_reversible() -> None:
    """低 MCS、PF 增益和 Schmidt 缺口都不能被静默吞掉。"""
    h_eff = np.zeros((3, 1, 1, 3), dtype=np.complex128)
    h_eff[0, 0, 0, 0] = 1.0
    h_eff[1, 0, 0, 1] = 0.9
    h_eff[2, 0, 0, 2] = 0.8
    gated = mu.pair_users(
        h_eff, criterion="all", mcs_indices=np.array([3, 10, 10]),
        min_pairing_mcs=4)
    assert gated.users == [1, 2] and gated.dropped_by_mcs == [0]
    legacy = mu.pair_users(
        h_eff, criterion="all", mcs_indices=np.array([3, 10, 10]),
        min_pairing_mcs=0)
    assert legacy.users == [0, 1, 2]
    try:
        mu.pair_users(h_eff, orthogonalization_mode="schmidt")
    except NotImplementedError as exc:
        assert "TODO" in str(exc) and "Schmidt" in str(exc)
    else:
        raise AssertionError("schmidt 未实现时必须硬失败")


test_mu_admission_gates_are_explicit_and_reversible()

# ---------------------------------------------------------------------------
section("9  唯一系统主循环必须消费同一个 PDSCH 开销口径（38.214 §5.1.3.2 步骤 1）")
# **棘轮。** 把 experience.TbsLookup 或系统集成入口换回硬编码的 `PRB x 12 x 12`，
# 下面几条会变红：那等于假设 DM-RS 与 PDCCH 都不占资源。判据不是"数值等于多少"，
# 而是"改开销配置，TBS 表和真实系统吞吐都必须跟着动"；硬编码路径的比值会退化成 1.000。
_oh_default = la.PdschOverhead()                                  # 126 RE/PRB
_oh_free = la.PdschOverhead(dmrs_re_per_prb=0, pdcch_symbols=0)   # 144 RE/PRB
_re_ratio = _oh_free.re_per_prb("D") / _oh_default.re_per_prb("D")
check(abs(_re_ratio - 144.0 / 126.0) < 1e-12,
      f"两组开销参数的每 PRB RE 之比 = 144/126（实得 {_re_ratio:.6f}）")

# --- experience 侧：TbsLookup 必须消费传进去的开销 ---
_lut_default = ex.TbsLookup.build(17, 16, sy.S_SLOT_DL_FRACTION)
_lut_free = ex.TbsLookup.build(17, 16, sy.S_SLOT_DL_FRACTION, overhead=_oh_free)
_tbs_ratio = (int(_lut_free.values[0, 12, 0, -1])
              / int(_lut_default.values[0, 12, 0, -1]))
check(abs(_tbs_ratio - _re_ratio) < 0.01,
      f"TbsLookup 的 TBS 随开销口径同比变化（实得 {_tbs_ratio:.4f} vs "
      f"RE 比 {_re_ratio:.4f}）")


# --- 系统集成侧：唯一主循环必须消费 sys_cfg.pdsch_overhead ---
def _system_served(overhead):
    return sy.simulate(
        tables[:1],
        sys_cfg=sy.SystemConfig(duration_s=0.05, tdd_pattern="DDDD",
                                pdsch_overhead=overhead),
        traffic=sy.TrafficConfig(model="full_buffer"),
        sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False),
        kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(92, 0),
    ).cell["cell_served_mbps"]


_served_default = _system_served(None)
_served_free = _system_served(_oh_free)
_served_ratio = _served_free / max(_served_default, 1e-12)
print(f"  唯一系统主循环：126 RE/PRB -> {_served_default:.2f} Mbps，"
      f"144 RE/PRB -> {_served_free:.2f} Mbps，比值 {_served_ratio:.4f}")
check(_served_ratio > 1.05,
      f"唯一系统主循环确实读了 sys_cfg.pdsch_overhead（比值 {_served_ratio:.4f}；"
      "硬编码 12x12 时会退化成 1.000）")
check(abs(_served_ratio - _re_ratio) < 0.03,
      f"系统吞吐随开销口径同比变化（实得 {_served_ratio:.4f} vs "
      f"RE 比 {_re_ratio:.4f}）")
check(_lut_default.overhead == _oh_default
      and sy.SystemConfig().pdsch_overhead == _oh_default,
      "TBS 表与系统入口的默认口径是同一个 PdschOverhead()，不会各自漂移")


# ---------------------------------------------------------------------------
section("9  Sionna RT 入口不能静默选错引擎，也不能误伤单窗口时间轴")


def test_sionna_rt_source_key_and_single_window_contract() -> None:
    """审核发现的两条边界必须在物理不变量层形成棘轮。"""
    try:
        gen.generate({"channel_source": "sionna_rt"}, num_samples=1)
    except ValueError as exc:
        check("channel_source" in str(exc) and "source" in str(exc)
              and "internal_sim" in str(exc),
              "错误 channel_source 键被硬拒绝，不再静默生成统计信道")
    else:
        check(False, "错误 channel_source 键必须硬失败")

    try:
        srt.SionnaRTSource({
            "scene": "munich", "num_ues": 1, "num_samples": 1,
            "num_slots_per_sample": 2, "mobility_mode": "linear",
            "ue_speed_kmh": 30.0,
        })._assert_samples_are_distinct()
    except ValueError as exc:
        check(False, f"单窗口移动多时隙没有跨轮重叠，不应拒绝（{exc}）")
    else:
        check(True, "单窗口移动多时隙合法，不被跨轮守卫误伤")


test_sionna_rt_source_key_and_single_window_contract()


# ---------------------------------------------------------------------------
# MU 的层数必须是 min(该 UE 当下的 SU rank, 配对上限)
#
# 踩过的坑：MU 配对表按一个全局层数上限离线建好，TTI 主循环直接拿来用，于是
# rank 策略说"只发一层"时 MU 仍然按两层发——一个 TTI 真的送出四层，而功率
# 分摊、预测 SINR 和传输块大小全部按四层记账。KPI 上完全看不出来：吞吐只是
# "看起来高一点"。这两条把发送层数与 rank 策略钉在一起。
# ---------------------------------------------------------------------------
def _mu_layer_fixture(seed: int = 20260907, mixed: bool = False):
    rng = np.random.default_rng(seed)
    snap = 6
    ue0 = ((rng.standard_normal((snap, 272, 16, 4))
            + 1j * rng.standard_normal((snap, 272, 16, 4))) / np.sqrt(2))
    if mixed:
        # UE1 的四根接收天线几乎同相 —— 空间上只有一层，SU rank 必然是 1。
        seedv = ((rng.standard_normal((snap, 272, 16, 1))
                  + 1j * rng.standard_normal((snap, 272, 16, 1))) / np.sqrt(2))
        ue1 = np.repeat(seedv, 4, axis=3) + 0.02 * (
            (rng.standard_normal((snap, 272, 16, 4))
             + 1j * rng.standard_normal((snap, 272, 16, 4))) / np.sqrt(2))
    else:
        ue1 = ((rng.standard_normal((snap, 272, 16, 4))
                + 1j * rng.standard_normal((snap, 272, 16, 4))) / np.sqrt(2))
    return sy.build_link_tables(
        [ue0, ue1], [16.0, 14.0], max_rank=2, rb_per_rbg=16, mu_enabled=True,
        csi=ca.CsiConfig(enabled=False))


def _mu_grants(tables, *, cap: int, rank_mode: str, fixed_rank: int = 2,
               seed: int = 4242):
    run = sy.simulate(
        tables,
        sys_cfg=sy.SystemConfig(duration_s=0.05, tdd_pattern="DDDSU", seed=seed),
        traffic=sy.TrafficConfig(model="full_buffer"),
        sched=sy.SchedulerConfig(
            mu_enabled=True, mu_rank_per_user=cap,
            rank=ap.RankConfig(mode=rank_mode, fixed_rank=fixed_rank)),
        kpi=sy.KpiConfig(warmup_tti=0, tti_trace_mode="full"),
        rng=rg.RngBook(seed, 0))
    rows = []
    for row in run.diagnostics["tti_trace"]["rows"]:
        for grant in row.get("grants", ()):
            if grant.get("transmission_mode") == "MU":
                rows.append(grant)
    return run, rows


def test_mu_layers_follow_min_su_rank_and_pairing_cap() -> None:
    tables = _mu_layer_fixture()
    # SU 策略说只发一层，配对上限 2：MU 也只能发一层。
    run1, mu1 = _mu_grants(tables, cap=2, rank_mode="fixed", fixed_rank=1)
    layers1 = sorted({int(g["rank"]) for g in mu1})
    print(f"  SU 固定 1 层 / 配对上限 2：MU 授权 {len(mu1)} 条，逐用户层数 {layers1}")
    check(bool(mu1) and layers1 == [1],
          "SU 只发一层时 MU 每个用户也只发一层（旧实现按上限发两层，一个 TTI 送四层）")
    # 功率分摊必须与实际层数一致：两人各一层 => 总层数 2 => 各 -3.0103 dB。
    check(all(abs(float(g["power_loss_db"]) + 10.0 * np.log10(2.0)) < 1e-9
              for g in mu1),
          "一层 + 一层的功率分摊按总层数 2 算，不是按上限的 4 层")

    # 配对上限压到 1，SU 仍是两层：MU 发一层，SU 单发仍可发两层。
    run2, mu2 = _mu_grants(tables, cap=1, rank_mode="fixed", fixed_rank=2)
    su2 = [g for row in run2.diagnostics["tti_trace"]["rows"]
           for g in row.get("grants", ())
           if g.get("transmission_mode") == "SU"]
    print(f"  SU 固定 2 层 / 配对上限 1：MU 层数 "
          f"{sorted({int(g['rank']) for g in mu2})}，"
          f"SU 层数 {sorted({int(g['rank']) for g in su2})}")
    check(bool(mu2) and {int(g["rank"]) for g in mu2} == {1},
          "配对上限压到 1 时 MU 只发一层")
    check(bool(su2) and {int(g["rank"]) for g in su2} == {2},
          "配对上限只约束 MU，不影响单发的层数")

    # 上限与 SU 都是 2 时保持历史行为。
    _run3, mu3 = _mu_grants(tables, cap=2, rank_mode="fixed", fixed_rank=2)
    check(bool(mu3) and {int(g["rank"]) for g in mu3} == {2},
          "SU 两层 + 上限两层仍然发两层（历史工作点不动）")


def test_mu_supports_mixed_layer_pairing() -> None:
    tables = _mu_layer_fixture(mixed=True)
    best = [int(t.best_rank[0]) for t in tables]
    print(f"  两个 UE 的单发最优层数 {best}")
    check(best[0] != best[1], "夹具确实造出了单发层数不同的两个 UE")

    # 建表侧：每种层数组合都在，异 rank 不再抛错。
    combos = sorted(tables[0].mu_links_by_rank[1])
    check((1, 2) in combos and (2, 1) in combos and (2, 2) in combos,
          f"配对表覆盖全部层数组合 {combos}（旧实现对异 rank 直接抛错）")
    mixed_link = tables[0].mu_links_by_rank[1][(2, 1)]
    check(bool(np.allclose(mixed_link.power_loss_db,
                           [10.0 * np.log10(2 / 3), 10.0 * np.log10(1 / 3)])),
          "两层 + 一层的功率分摊是 [-1.76, -4.77] dB，层多的用户分到更多功率")

    # 运行侧：同一个 MU 组里两个用户的层数确实不同，且各自等于自己的单发层数。
    _run, mu_rows = _mu_grants(tables, cap=2, rank_mode="link_table")
    groups: dict[int, dict[int, int]] = {}
    powers: dict[int, dict[int, float]] = {}
    for grant in mu_rows:
        gid = int(grant["mu_group_id"])
        groups.setdefault(gid, {})[int(grant["ue"])] = int(grant["rank"])
        powers.setdefault(gid, {})[int(grant["ue"])] = float(grant["power_loss_db"])
    mixed_groups = [gid for gid, d in groups.items() if len(set(d.values())) > 1]
    print(f"  MU 组 {len(groups)} 个，其中两侧层数不同的 {len(mixed_groups)} 个")
    check(bool(groups) and len(mixed_groups) == len(groups),
          "异 rank 配对真的发生了：每个 MU 组都是一个用户两层、另一个一层")
    check(all(d.get(0) == best[0] and d.get(1) == best[1]
              for d in groups.values()),
          "每个用户在 MU 里的层数等于它自己的单发层数（不是取两者的最大或上限）")
    # 功率、SINR、传输块必须共用同一组层数：功率分摊只由实际层数决定。
    ok_power = all(
        abs(powers[gid][0] - 10.0 * np.log10(
            groups[gid][0] / (groups[gid][0] + groups[gid][1]))) < 1e-9
        and abs(powers[gid][1] - 10.0 * np.log10(
            groups[gid][1] / (groups[gid][0] + groups[gid][1]))) < 1e-9
        for gid in groups)
    check(ok_power,
          "逐用户功率分摊与实际发送层数逐组一致，没有两套层数并存")


test_mu_layers_follow_min_su_rank_and_pairing_cap()
test_mu_supports_mixed_layer_pairing()


# ---------------------------------------------------------------------------
# MU 层数上限必须从建表一路贯通到发送，中间不许有第二个真相源
#
# 三个踩过的坑，都是"上限只在一半路径上生效"的不同表现：
#   1. 仿真入口写死"开了 MU 就必须支持两层"，于是只有单层能力的终端（1 收）
#      根本进不了门，哪怕它完全可以在 MU 里发一层。
#   2. 逐用户上限只活在建表阶段，运行时仍拿调度配置里那个全局标量去查表；
#      给 UE0 只备了一层的表，运行时却按两层去找，找不到就整批拒配。
#   3. 重新建表时旧的层数组合没清掉，运行时可能查中一张**已经不该存在**的表，
#      比如上限已经压到一层却仍按残留的两层组合发送。KPI 上完全看不出来。
# ---------------------------------------------------------------------------
def _mu_cap_fixture(n_rx: int, seed: int, *, max_rank: int, mu: bool = True):
    rng = np.random.default_rng(seed)
    snap = 4
    chans = [((rng.standard_normal((snap, 17, 32, n_rx))
               + 1j * rng.standard_normal((snap, 17, 32, n_rx))) / np.sqrt(2))
             for _ in range(2)]
    return sy.build_link_tables(
        chans, [15.0, 13.0], num_snapshots=snap, rb_per_rbg=1,
        csi=ca.CsiConfig(enabled=False), max_rank=max_rank, mu_enabled=mu)


def _mu_grant_layers(tables, *, cap: int, fixed_rank: int, seed: int = 7):
    run = sy.simulate(
        tables,
        sys_cfg=sy.SystemConfig(duration_s=0.02, tdd_pattern="DDDSU", seed=seed),
        traffic=sy.TrafficConfig(model="full_buffer"),
        sched=sy.SchedulerConfig(
            mu_enabled=True, mu_rank_per_user=cap,
            rank=ap.RankConfig(mode="fixed", fixed_rank=fixed_rank)),
        kpi=sy.KpiConfig(warmup_tti=0, tti_trace_mode="full"),
        rng=rg.RngBook(seed, 0))
    groups: dict[int, dict[int, int]] = {}
    for row in run.diagnostics["tti_trace"]["rows"]:
        for grant in row.get("grants", ()):
            if grant.get("transmission_mode") == "MU":
                groups.setdefault(int(grant["mu_group_id"]), {})[
                    int(grant["ue"])] = int(grant["rank"])
    return run, groups


def test_single_layer_terminals_can_pair() -> None:
    """只有单层能力的终端必须能进 MU，并且只发一层。"""
    tables = _mu_cap_fixture(1, 20260907, max_rank=1)
    check(all(t.sinr_db.shape[1] == 1 for t in tables),
          "夹具确实只有单层链路表")
    run, groups = _mu_grant_layers(tables, cap=1, fixed_rank=1)
    layers = sorted({v for d in groups.values() for v in d.values()})
    print(f"  单层终端：MU 组 {len(groups)} 个，逐用户层数 {layers}，"
          f"配对占比 {run.cell['mu_share']:.3f}")
    check(bool(groups) and layers == [1],
          "单层能力的终端照样能配对，每人发一层（旧实现在入口就报错拒绝入场）")

    # 全局上限调大也不能突破该 UE 的建表能力。
    run2, groups2 = _mu_grant_layers(tables, cap=2, fixed_rank=2)
    layers2 = sorted({v for d in groups2.values() for v in d.values()})
    print(f"  全局上限调到 2：层数仍是 {layers2}")
    check(bool(groups2) and layers2 == [1],
          "全局上限调大也不能突破该 UE 自己的建表上限")


def test_per_ue_cap_reaches_the_air_and_rebuild_clears_stale_combinations() -> None:
    tables = _mu_cap_fixture(4, 4242, max_rank=2, mu=False)
    sy.build_mu_pair_tables(tables, rank_per_user=2)
    first = sorted(tables[0].mu_links_by_rank[1])
    check(first == [(1, 1), (1, 2), (2, 1), (2, 2)],
          f"上限 2 时四种层数组合都建好了（实得 {first}）")

    # 同一批表换成逐用户上限 (1,2) 重建：旧的 (2,*) 必须消失。
    sy.build_mu_pair_tables(tables, rank_per_user=(1, 2))
    after = sorted(tables[0].mu_links_by_rank[1])
    caps = [getattr(t, "mu_rank_cap", None) for t in tables]
    print(f"  重建为逐用户上限 (1,2)：组合 {after}，链路表记录的上限 {caps}")
    check(after == [(1, 1), (1, 2)],
          "重建配对表会清掉上一次的组合，不留下已经不该存在的两层表")
    check(caps == [1, 2],
          "每个 UE 的层数上限记在它自己的链路表上，供运行时读取")
    check(tuple(tables[0].mu_links[1].rank_per_user) == (1, 2),
          "默认那张表指向本次上限的组合，不是上一次的")

    # 运行时：全局上限给 2、SU 也给 2，但 UE0 的表只备了一层 —— 必须发 (1,2)，
    # 既不能越限发两层，也不能因为查不到表而整批拒配。
    run, groups = _mu_grant_layers(tables, cap=2, fixed_rank=2)
    rejects = run.cell["mu_candidate_scoring"]["rejection_reasons"]
    print(f"  运行时：配对占比 {run.cell['mu_share']:.3f}，拒配 {rejects}，"
          f"逐组层数样例 {list(groups.items())[:2]}")
    check(bool(groups) and all(d == {0: 1, 1: 2} for d in groups.values()),
          "逐用户上限贯通到空口：UE0 发一层、UE1 发两层")
    check("missing_pair_link" not in rejects,
          "逐用户上限不齐时不会因为查不到表而整批拒配")


def test_rzf_reported_loading_equals_the_one_actually_used() -> None:
    """报告的正则化加载必须能重构出实际用的发射权。

    正则化迫零的对角加载由**逐流**平均噪声决定（预编码内部就是这么算的）。
    报告那一份原来按**逐用户**平均重算：每人流数相同时两者恰好相等，所以一直
    没暴露；异 rank 时流多的用户权重更大，报告值就对不上实际发射权。
    """
    rng = np.random.default_rng(4242)
    chans = [((rng.standard_normal((5, 16, 4))
               + 1j * rng.standard_normal((5, 16, 4))) / np.sqrt(2))
             for _ in range(2)]
    noise = np.array([0.02, 0.20])          # 两个用户噪声差一个数量级
    worst = 0.0
    for ranks in ([1, 2], [2, 1], [2, 2], [1, 1]):
        res = mu.mu_link_performance_lmmse(
            chans, chans, noise_power=noise, streams_per_user=ranks,
            precoder="rzf", power_constraint="nebf", rb_per_rbg=1)
        reported = float(res.rzf_regularization["total_loading"])
        actual = mu.robust_rzf_regularization(
            n_stream=int(sum(ranks)), n_bs=16,
            mean_noise_power=float(np.mean(np.repeat(noise, ranks))),
            total_power=1.0, csi_error_variance=0.0, alpha=None).total_loading
        worst = max(worst, abs(reported - float(actual)))
        if ranks == [1, 2]:
            print(f"  异 rank [1,2]：报告 {reported:.6f} / 实际 {float(actual):.6f}")
    check(worst == 0.0,
          f"RZF 报告的对角加载与实际使用的逐位相同（最大差 {worst:.3e}）")

    # 等 rank 时两种平均**数学上恒等**，但求和顺序不同，浮点上差一个末位。
    # 说清楚哪个变了：真正进入发射权计算的加载一直是逐流平均，一个字节没动；
    # 变的只是**报告**出来的那个数，且只在最后一位。不要写成「逐位不变」。
    equal_rank = mu.mu_link_performance_lmmse(
        chans, chans, noise_power=noise, streams_per_user=2,
        precoder="rzf", power_constraint="nebf", rb_per_rbg=1)
    legacy = float(mu.robust_rzf_regularization(
        n_stream=4, n_bs=16, mean_noise_power=float(np.mean(noise)),
        total_power=1.0, csi_error_variance=0.0, alpha=None).total_loading)
    now = float(equal_rank.rzf_regularization["total_loading"])
    rel = abs(now - legacy) / max(abs(legacy), 1e-300)
    print(f"  等 rank：新报告 {now!r} vs 旧报告 {legacy!r}，相对差 {rel:.2e}")
    check(rel <= 4e-16,
          "等 rank 时报告值与历史只差浮点末位（数学恒等，求和顺序不同）")


def test_mac_throughput_meters_separate_sent_from_received() -> None:
    """收发两侧 MAC 吞吐与 Head/Body/Tail 分段的守恒关系。

    棘轮守三件事，任何一件被 revert 都会变红：

    1. **分段账守恒。** 一个 busy period 从首次调度到清空 buffer 之间的每一个
       TTI 都必须落进 Head/Body/Tail 之一，包括没轮到它的 TTI 和上行/保护
       时隙。漏掉这些 TTI 不会报错，只会让分段速率静默偏高（DDDSU 下漏掉
       上行时隙就是 +25%）。
    2. **接收侧只认收对的。** 首传全错时接收侧吞吐必须严格低于发送侧；
       如果哪天有人把接收侧改回"发送即计"，这两个数会重新相等。
    3. **发送侧含重传。** 重传把同一份净荷再占一次资源，所以首传全错时
       发送侧必须严格高于体验口径（后者只记首传、且不看对错）。
    """
    n_sample = 40
    point = sy.UeLinkTable(
        ue=0, sinr_db=np.full((n_sample, 4), 18.0),
        mcs=np.full((n_sample, 4), 16),
        se=np.full((n_sample, 4), la.MCS_TABLE_3[16].se),
        best_rank=np.ones(n_sample, dtype=int),
        best_se=np.full(n_sample, la.MCS_TABLE_3[16].se), geo_sinr_db=18.0,
        outage=np.zeros(n_sample, dtype=bool), mcs_table=3, target_bler=0.1,
        sinr_rbg_db=np.full((n_sample, 4, 17), 18.0),
        sinr_tx_db=np.full((n_sample, 4), 18.0),
        sinr_tx_rbg_db=np.full((n_sample, 4, 17), 18.0))
    old_lookup = ex._bler_lookup
    old_retx = la.harq_retransmission_bler

    def _all_lost_retx(mcs, sinr_db, **kw):
        row = dict(old_retx(mcs, sinr_db, **kw))
        row["bler"] = 1.0
        return row

    runs = {}
    try:
        for p_bler in (0.0, 1.0):
            ex._bler_lookup = lambda _m, _s, _v=p_bler: _v
            runs[p_bler] = sy.simulate(
                [point],
                sys_cfg=sy.SystemConfig(duration_s=2.0, tdd_pattern="DDDSU"),
                traffic=sy.TrafficConfig(model="ftp3", file_bytes=2_000_000,
                                         arrival_rate_hz=0.8),
                sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False),
                kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(3, 0)).cell
        # 首传与唯一一次重传全部失败：一个 TB 都没送达。
        ex._bler_lookup = lambda _m, _s: 1.0
        la.harq_retransmission_bler = _all_lost_retx
        all_lost = sy.simulate(
            [point],
            sys_cfg=sy.SystemConfig(duration_s=2.0, tdd_pattern="DDDSU"),
            traffic=sy.TrafficConfig(model="ftp3", file_bytes=2_000_000,
                                     arrival_rate_hz=0.8),
            sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False),
            kpi=sy.KpiConfig(warmup_tti=0), rng=rg.RngBook(3, 0)).cell
    finally:
        ex._bler_lookup = old_lookup
        la.harq_retransmission_bler = old_retx

    for p_bler, cell in runs.items():
        seg = cell["burst_segment_rates"]
        print(f"  首传误块 {p_bler:.0%}：接收 {cell['dl_rx_mac_tput_mbps']:.2f} / "
              f"体验 {cell['cell_served_mbps']:.2f} / 发送 "
              f"{cell['dl_tx_mac_tput_mbps']:.2f} Mbps；分段残差 "
              f"{seg['segment_accounting_error_bytes']} B / "
              f"{seg['segment_accounting_error_tti']} TTI")
        check(seg["segment_accounting_error_bytes"] == 0,
              f"误块 {p_bler:.0%}：分段字节守恒（三段之和 = 已发净荷）")
        check(seg["segment_accounting_error_tti"] == 0,
              f"误块 {p_bler:.0%}：分段 TTI 守恒（含空闲与上行时隙）")
        check(cell["dl_rx_mac_tput_mbps"] <= cell["cell_served_mbps"] + 1e-9
              <= cell["dl_tx_mac_tput_mbps"] + 1e-9,
              f"误块 {p_bler:.0%}：接收侧 <= 体验口径 <= 发送侧")

    check(runs[1.0]["dl_rx_mac_tput_mbps"]
          < runs[1.0]["dl_tx_mac_tput_mbps"] - 1e-9,
          "首传全错时接收侧严格低于发送侧（接收侧只认 HARQ 收对的 TB）")
    check(runs[1.0]["dl_tx_mac_tput_mbps"]
          > runs[1.0]["cell_served_mbps"] + 1e-9,
          "首传全错时发送侧严格高于体验口径（重传再占一次资源）")
    check(abs(runs[0.0]["dl_tx_mac_tput_mbps"]
              - runs[0.0]["cell_served_mbps"]) < 1e-9,
          "零误码时没有重传，发送侧与体验口径逐值相同")
    check(runs[0.0]["dl_rx_mac_tput_mbps"]
          > runs[1.0]["dl_rx_mac_tput_mbps"] + 1e-9,
          "误码越多接收侧吞吐越低，而体验口径对误码不敏感")
    # 上面四条都在 warmup=0 下成立（测量窗从第一个 TTI 就开始，没有前沿）。
    # 有预热期时接收侧的归属规则见下一条棘轮。

    # 首传与重传全丢：一个 TB 都没送达，接收侧必须是 0。这条钉住"接收侧
    # 只在 ACK 时记账"——把 ACK 判据去掉，它会立刻变成与体验口径同量级。
    print(f"  首传+重传全丢：接收 {all_lost['dl_rx_mac_tput_mbps']:.2f} / "
          f"体验 {all_lost['cell_served_mbps']:.2f} / 发送 "
          f"{all_lost['dl_tx_mac_tput_mbps']:.2f} Mbps，"
          f"残余误块 {all_lost['residual_bler']:.3f}")
    check(all_lost["dl_rx_mac_tput_mbps"] == 0.0,
          "首传与重传都失败时接收侧 MAC 吞吐恒为 0")
    check(all_lost["cell_served_mbps"] > 0.0
          and all_lost["dl_tx_mac_tput_mbps"] > all_lost["cell_served_mbps"],
          "同一次仿真里体验口径与发送侧照常为正（口径确实互相独立）")
    check(all_lost["burst_segment_rates"]["segment_accounting_error_bytes"] == 0
          and all_lost["burst_segment_rates"]["segment_accounting_error_tti"] == 0,
          "全丢场景下分段账仍然守恒")
# ---------------------------------------------------------------------------
# SRS 导频污染：谁污染由 SRS 资源分配决定，不是由 PCI 颜色决定
#
# 反向意义（revert 掉哪一条会变红）：
#   * 多小区默认不再生成下行干扰信道 -> 第 1 条红
#   * 把上行交叉链路换成 h_interferers（下行链路）-> 第 5 条红
#   * 拿"同色"当"碰撞"用 -> 第 3 条红（同色候选里必须两种都有，且不碰撞的
#     邻区一个 RB 都不许污染）
#   * 污染重新覆盖全带 272 RB 而不是本次探测的 16 RB -> 第 6 条红
#   * 三种估计模式退回同一条全带代理 -> 第 7 条红
#   * 落盘丢掉逐样本干扰源身份 -> 第 8 条红
#   * 把开关折成 or 默认值让显式 false 失效 -> 第 9 条红
# ---------------------------------------------------------------------------

_SRS_RB = 272
_SRS_HOP_RB = 16


def _cross_link_cfg(**extra):
    cfg = dict(
        num_rb=_SRS_RB, num_bs_tx_ant=8, num_ue_rx_ant=4, num_ue_tx_ant=4,
        subcarrier_spacing=30000.0, topology="hex", num_sites=7,
        sectors_per_site=3, isd_m=300.0, scenario="UMa_NLOS",
        channel_model="CDL-C", link="BOTH", num_ues=63, num_samples=6, seed=11,
        channel_est_mode="ls_linear", num_interfering_ues=3,
    )
    cfg.update(extra)
    return cfg


def test_srs_ul_cross_link_and_pilot_contamination() -> None:
    print()
    print("[SRS 导频污染] 上行交叉链路、真实资源碰撞与跳频取样")
    from superran.native import InternalSimSource

    off = list(InternalSimSource(_cross_link_cfg()).iter_samples())
    on = list(InternalSimSource(_cross_link_cfg(
        measurements={"srs_cross_link_channels": True})).iter_samples())
    polluted = list(InternalSimSource(_cross_link_cfg(
        measurements={"srs_cross_link_channels": True},
        srs_pilot_contamination_rho=1.0)).iter_samples())

    # 1. 多小区默认就要有下行干扰信道；单小区不生成
    check(all(s.h_interferers is not None for s in off),
          "多小区场景默认生成下行干扰信道")
    single = list(InternalSimSource(_cross_link_cfg(
        num_sites=1, sectors_per_site=1, num_samples=1)).iter_samples())[0]
    check(single.h_interferers is None,
          "单小区场景不生成干扰信道（行为与修复前一致）")
    check(off[0].h_interferers.shape[0] == 3,
          f"默认只保留 3 个邻区（实得 {off[0].h_interferers.shape[0]}），不是全部邻区")
    worst_gap = 0.0
    strongest_ok = True
    for s in off:
        rx = np.asarray(s.meta["rx_power_all_dbm"])
        serving = int(s.meta["serving_cell_index"])
        # 稳定排序：同站三扇区位置相同、方向图对称，接收电平会精确打平，
        # 用默认快排的话参考值本身就不确定。
        want = [
            int(k) for k in np.argsort(-rx, kind="stable") if k != serving
        ][:3]
        got = [int(v) for v in np.asarray(s.meta["interferer_cell_ids"])]
        strongest_ok = strongest_ok and want == got
        by_index = [k for k in range(len(rx)) if k != serving][:3]
        worst_gap = max(worst_gap, float(rx[want[0]] - rx[by_index[0]]))
    print(f"  按编号取前三个时，最强邻区会被漏掉最多 {worst_gap:.1f} dB")
    check(strongest_ok,
          "保留的三个邻区就是接收电平最强的三个（按小区编号取前三个会选错人）")
    check(all(
        np.asarray(s.meta["interferer_cell_ids"]).shape[0]
        == np.asarray(s.h_interferers).shape[0] for s in off),
        "h_interferers 的每一根都带着它属于哪个邻区，选择结果可独立复核")
    aligned = True
    for s in off:
        rx = np.asarray(s.meta["rx_power_all_dbm"])
        ids = np.asarray(s.meta["interferer_cell_ids"])
        pw = np.asarray(s.meta["interferer_rx_power_dbm"])
        aligned = aligned and np.allclose(pw, [rx[k] for k in ids])
        aligned = aligned and bool(np.all(np.diff(pw) <= 1e-12))
    check(aligned,
          "身份的小区号与电平两列同序且按电平降序（分别取一遍会错位）")

    # 2. 上行交叉链路仍是显式打开的（它只服务导频污染实验）
    check(all(s.h_ul_cross is None for s in off), "上行交叉链路默认不生成")
    check(all(s.h_ul_cross is not None for s in on),
          "显式打开后每个样本都有上行交叉链路")

    # 3. 同色只筛候选，真正决定污染的是 SRS 资源碰撞
    colour_ok = []
    for s in on:
        c = int(s.meta["srs_cross_link_serving_pci_mod3"])
        colour_ok.extend(int(p) % 3 == c for p in s.meta["srs_cross_link_pci"])
    # 碰撞与否取决于「邻区在我们这个槽位号上有没有人」，样本太少看不到两种，
    # 所以这一条单独跑一批覆盖每个 UE 一次的数据。
    from superran.native import InternalSimSource as _S0
    wide = list(_S0(_cross_link_cfg(
        num_samples=63, measurements={"srs_cross_link_channels": True},
    )).iter_samples())
    collide = np.asarray([
        int(v) for s in wide for v in s.meta["srs_cross_link_collides"]])
    check(bool(colour_ok) and all(colour_ok), "候选全部与本小区同 PCI mod3 颜色")
    print(f"  同色候选 {collide.size} 个，其中真碰撞 {int(collide.sum())} 个、"
          f"不碰撞 {int((collide == 0).sum())} 个")
    check(collide.sum() > 0 and (collide == 0).sum() > 0,
          "同色候选里碰撞与不碰撞两种都出现（同色不等于碰撞）")

    # 4. 干扰 UE 必须真的被它自己那个小区服务
    d_victim = [d for s in on for d in s.meta["srs_cross_link_distance_to_victim_m"]]
    check(bool(d_victim) and min(d_victim) > 100.0,
          f"干扰 UE 到本站最近 {min(d_victim):.0f} m > 100 m（服务小区一致性拒绝采样生效）")

    # 5. 上行交叉链路与下行干扰信道是两根不同的链路
    def _corr(x, y):
        x, y = np.asarray(x).ravel(), np.asarray(y).ravel()
        return abs(np.vdot(x, y)) / (np.linalg.norm(x) * np.linalg.norm(y))

    # 判据必须自校准：同一套 CDL 时延剖面生成的任意两根信道本来就有相关性，
    # 实测两根下行干扰信道之间就能到 0.34。所以"上下行是两根不同的链路"只能
    # 表述为"跨类相关性不超过同类相关性"，拿一个拍脑袋的常数当门槛没有意义。
    same_class, cross_class = [], []
    for sample in on:
        dl = np.asarray(sample.h_interferers)
        ul = np.asarray(sample.h_ul_cross)
        for a in range(dl.shape[0]):
            for b in range(a + 1, dl.shape[0]):
                same_class.append(_corr(dl[a], dl[b]))
        for a in range(ul.shape[0]):
            for b in range(a + 1, ul.shape[0]):
                same_class.append(_corr(ul[a], ul[b]))
            for b in range(dl.shape[0]):
                cross_class.append(_corr(ul[a], dl[b]))
    same_max = float(np.max(same_class))
    cross_max = float(np.max(cross_class))
    print(f"  信道相关性：同类最大 {same_max:.3f}，跨类（上行交叉链路 vs 下行干扰"
          f"信道）最大 {cross_max:.3f}")
    check(cross_max <= same_max + 0.05 and cross_max < 0.9,
          "上行交叉链路与下行干扰信道的相似度不超过同类信道之间的相似度"
          "（两根不同的链路，互相替代不成立）")

    # 6. 污染只落在本次 SRS 探测的 16 个 RB 上，不碰撞的邻区一个 RB 都不碰
    touched = []
    for clean, dirty in zip(on, polluted):
        d = np.abs(np.asarray(dirty.h_ul_est) - np.asarray(clean.h_ul_est))[0]
        touched.append(int(np.count_nonzero(d.reshape(d.shape[0], -1).max(axis=1) > 0)))
    print(f"  ls_linear 下被污染改动的 RB 数 = {sorted(set(touched))}"
          f"（真实 SRS 一跳 = {_SRS_HOP_RB} RB，全带 = {_SRS_RB}）")
    check(set(touched) <= {0, _SRS_HOP_RB},
          "污染只作用在本次 SRS 探测的那一跳上，不再抹平整个载波")

    zero_collision_seen = False
    for clean, dirty in zip(on, polluted):
        if not int(np.asarray(dirty.meta["srs_cross_link_collides"]).sum()):
            zero_collision_seen = True
            check(np.array_equal(clean.h_ul_est, dirty.h_ul_est),
                  "同色但资源不碰撞的邻区不产生任何导频污染")
            break
    if not zero_collision_seen:
        # 构造一个全不碰撞的对照：把干扰 UE 全部换成另一组资源叶子
        print("  （本批样本每个都至少有一个碰撞源，改用逐链路对照）")
        ok = True
        for clean, dirty in zip(on, polluted):
            flags = np.asarray(dirty.meta["srs_cross_link_collides"])
            if int(flags.sum()) == flags.size:
                continue
            d = np.abs(np.asarray(dirty.h_ul_est) - np.asarray(clean.h_ul_est))[0]
            rb_hit = np.flatnonzero(d.reshape(d.shape[0], -1).max(axis=1) > 0)
            start = int(dirty.meta["srs_victim_rb_start"])
            width = int(dirty.meta["srs_victim_rb_count"])
            ok = ok and rb_hit.size == width and int(rb_hit[0]) == start
        check(ok, "被污染的 RB 恰好等于本 UE 这次探测的那一跳，不多不少")

    # 7. 三种估计模式必须走不同的取样路径
    modes = {}
    for mode in ("ls_linear", "ls_hop_concat", "ls_hop_sequential"):
        modes[mode] = np.asarray(list(InternalSimSource(_cross_link_cfg(
            channel_est_mode=mode, num_samples=1,
            measurements={"srs_cross_link_channels": True},
            srs_pilot_contamination_rho=1.0)).iter_samples())[0].h_ul_est)
    names = list(modes)
    pairs_differ = True
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if np.array_equal(modes[names[i]], modes[names[j]]):
                pairs_differ = False
                print(f"  {names[i]} 与 {names[j]} 逐位相同")
    check(pairs_differ, "普通线性、拼接跳频、顺序跳频三种估计不再是同一条全带代理")

    # 8. 逐样本干扰源身份必须落盘并能绑回邻区 UE
    m = on[0].meta
    n_intf = int(np.asarray(on[0].h_ul_cross).shape[0])
    id_ok = all(
        np.asarray(m[k]).shape == (n_intf,)
        for k in ("srs_cross_link_cell_ids", "srs_cross_link_ue_ids",
                  "srs_cross_link_collides",
                  "srs_cross_link_frequency_resource_id",
                  "srs_cross_link_ul_sir_db_vec")
    )
    check(id_ok, "每根交叉链路都带小区号、UE 号、频率相位、碰撞标志与上行 SIR")
    check(int(m["srs_victim_rb_count"]) == _SRS_HOP_RB
          and 0 <= int(m["srs_victim_rb_start"]) < _SRS_RB,
          f"本 UE 这次探测的 RB 区间已记录（起点 {int(m['srs_victim_rb_start'])}，"
          f"宽 {int(m['srs_victim_rb_count'])}）")

    # 9. 显式 false 必须真的关掉，哪怕默认值被翻成 True
    import superran.native as _nv
    saved = (_nv._STORE_INTERFERER_CHANNELS_DEFAULT,
             _nv._STORE_SRS_CROSS_LINK_DEFAULT)
    try:
        _nv._STORE_INTERFERER_CHANNELS_DEFAULT = True
        _nv._STORE_SRS_CROSS_LINK_DEFAULT = True
        forced_on = list(InternalSimSource(
            _cross_link_cfg(num_samples=1)).iter_samples())[0]
        forced_off = list(InternalSimSource(_cross_link_cfg(
            num_samples=1,
            measurements={"interferer_channels": False,
                          "srs_cross_link_channels": False},
        )).iter_samples())[0]
    finally:
        (_nv._STORE_INTERFERER_CHANNELS_DEFAULT,
         _nv._STORE_SRS_CROSS_LINK_DEFAULT) = saved
    check(forced_on.h_interferers is not None and forced_on.h_ul_cross is not None,
          "默认为 True 时两个张量都会生成")
    check(forced_off.h_interferers is None and forced_off.h_ul_cross is None,
          "配置里写 false 能真正关掉（折成 or 默认值就会关不掉）")

    # 10. 污染只会让 CSI 更差；rho 越界硬失败
    def _nmse(samples):
        return float(np.mean([
            np.linalg.norm(np.asarray(s.h_ul_est) - np.asarray(s.h_ul_true)) ** 2
            / np.linalg.norm(np.asarray(s.h_ul_true)) ** 2 for s in samples]))

    a, b = _nmse(on), _nmse(polluted)
    print(f"  CSI NMSE {10 * math.log10(a):.2f} dB -> {10 * math.log10(b):.2f} dB")
    check(b > a, "导频污染只会让 CSI 更差，不会更好")
    bad = 0
    for value in (1.5, -0.1, [1.0, 1.0]):
        try:
            list(InternalSimSource(_cross_link_cfg(
                num_samples=1,
                measurements={"srs_cross_link_channels": True},
                srs_pilot_contamination_rho=value)).iter_samples())
        except ValueError:
            bad += 1
    check(bad == 3, "越界或长度不符的导频残留相关系数一律硬失败")

    # 10b. 移动 UE 换小区后，SRS 资源必须来自新的服务小区
    # 每个 UE 必须被采样多次才可能观察到切换：样本按 UE 轮转，
    # num_samples 要显著大于 num_ues。
    moving = list(InternalSimSource(_cross_link_cfg(
        num_ues=4, num_samples=12, mobility_mode="linear", ue_speed_kmh=200.0,
        sample_interval_s=0.5,
        measurements={"srs_cross_link_channels": True},
        srs_pilot_contamination_rho=1.0)).iter_samples())
    handovers = 0
    seen_cell = {}
    for s in moving:
        ue = int(s.meta["ue_id"])
        now = int(s.meta["srs_serving_cell_id"])
        if seen_cell.setdefault(ue, now) != now:
            handovers += 1
            seen_cell[ue] = now
    mismatched = [
        (int(s.meta["ue_id"]), int(s.meta["srs_serving_cell_id"]),
         int(s.meta["srs_victim_cell_id"]))
        for s in moving
        if int(s.meta["srs_victim_cell_id"]) != int(s.meta["srs_serving_cell_id"])
    ]
    print(f"  移动场景里发生 {handovers} 次换小区，SRS 资源仍绑在旧小区的样本 "
          f"{len(mismatched)} 个")
    check(handovers > 0, "构造出的移动场景确实发生了换小区（否则这条检查是空的）")
    check(not mismatched,
          "换小区后 SRS 资源来自新的服务小区，不再绑着旧小区（否则碰撞会被漏掉）")

    # 10c. 同一小区、同一时刻，两个用户绝不能拿到同一份 SRS 资源
    #      （两个用户各自在原小区排「槽位 0」，迁入同一小区后仍都拿槽位 0，
    #       资源就完全重合——这是切换后没有重新配置资源的典型症状）
    from superran.native import InternalSimSource as _Src
    dup_total = 0
    checked = 0
    for n_ue, speed, n_smp in ((4, 200.0, 24), (63, 200.0, 252), (63, 350.0, 189)):
        rows = list(_Src(_cross_link_cfg(
            num_ues=n_ue, num_samples=n_smp, mobility_mode="linear",
            ue_speed_kmh=speed, sample_interval_s=0.5,
            measurements={"srs_cross_link_channels": True})).iter_samples())
        per_instant = {}
        for r in rows:
            per_instant.setdefault(int(r.meta["round_idx"]), []).append(
                (int(r.meta["ue_id"]), int(r.meta["srs_serving_cell_id"]),
                 int(r.meta["srs_victim_slot"])))
        for members in per_instant.values():
            seen = set()
            for _ue, cell, slot in sorted(members):
                checked += 1
                if (cell, slot) in seen:
                    dup_total += 1
                seen.add((cell, slot))
    print(f"  移动场景共核对 {checked} 个用户样本，同小区同槽位重复 {dup_total} 次")
    check(dup_total == 0,
          "同一时刻同一小区内，任意两个用户的 SRS 资源必须互异")

    # 10d. 切换进来的用户只能占空闲槽位，不许顶掉已经在这个小区的用户
    src = _Src(_cross_link_cfg(num_ues=63, num_samples=1, mobility_mode="linear",
                               ue_speed_kmh=350.0, sample_interval_s=0.5))
    sites_probe = src._build_sites()
    evicted = handovers = 0
    previous = None
    for step in range(10):
        assign, _occ = src._srs_slot_state(sites_probe, "UMa_NLOS", 1, step)
        if previous is not None:
            for ue, (cell, slot) in assign.items():
                old_cell, old_slot = previous[ue]
                if cell != old_cell:
                    handovers += 1
                elif slot != old_slot:
                    evicted += 1
        previous = assign
    print(f"  10 个时刻共 {handovers} 次切换，留在原小区却被换掉槽位 {evicted} 次")
    check(handovers > 0, "构造出的场景确实发生了切换（否则这条检查是空的）")
    check(evicted == 0,
          "已经在这个小区的用户不会被切换进来的人顶掉槽位（老用户保槽）")

    # 10e. 槽位池不够时必须硬失败，不许静默让两个人共用
    exhausted = False
    try:
        list(_Src(_cross_link_cfg(
            num_ues=63, num_samples=6, srs_slots_per_cell=1,
            mobility_mode="linear", ue_speed_kmh=200.0, sample_interval_s=0.5,
            measurements={"srs_cross_link_channels": True})).iter_samples())
    except RuntimeError:
        exhausted = True
    check(exhausted,
          "每小区只留 1 个槽位却有多个用户时硬失败，不静默共用同一份资源")

    # 10f. 空槽不许在取货后变成发射源——必须走真实的落盘与取货，
    #      只查生成端的占用表会漏掉这个接口。正反两例都要有：
    #      没人发射 -> 接收机干扰恒为 0；有人发射且资源碰撞 -> 干扰必须还在。
    import dataclasses as _dc

    from superran import generate as _gen
    from superran.loader import load as _load
    from superran.srs_resource import (  # noqa: PLC0415
        allocate_basic_srs_resources as _alloc,
    )
    from superran.srs_waveform import SrsWaveformConfig as _WCfg

    def _dataset(n_ue, n_smp):
        c = _cross_link_cfg(
            num_ues=n_ue, num_samples=n_smp, bandwidth_hz=100000000.0,
            measurements={"srs_cross_link_channels": True})
        c["source"] = "internal_sim"
        return _load(_gen.generate(c, num_samples=n_smp, workers=1)["dataset_id"])

    def _receive(ds, index, n_link, forced):
        victim = _alloc([0], cell_ids=0, adaptive_period=False)[0]
        if forced:      # 强制资源全同：碰撞的上界
            others = tuple(_dc.replace(victim, ue_id=100 + k, cell_id=3)
                           for k in range(n_link))
        else:
            others = _alloc(list(range(1, 1 + n_link)), cell_ids=3,
                            adaptive_period=False)
        sigs = ds.srs_cross_link_signals(index, others, n_srs_ids=[0] * n_link)
        rx = ds.srs_waveform(index, victim, n_srs_id=0, interferers=sigs,
                             config=_WCfg(noise_power_linear=1e-9))
        return len(sigs), float(np.mean(np.abs(rx.h_est_interference_rb) ** 2))

    ds_idle = _dataset(4, 4)
    n_link = int(ds_idle.h_ul_cross.shape[1])
    occ_idle = ds_idle.srs_cross_link.get("slot_occupied")
    check(occ_idle is not None,
          "占用状态随张量一起落盘（只留在内存里，取货端就看不到）")
    empty = [i for i in range(occ_idle.shape[0]) if int(occ_idle[i].sum()) == 0]
    check(bool(empty), "构造出了三个邻区槽位全空的样本（否则反例是空的）")
    if empty:
        idx = empty[0]
        power = float(np.mean(
            np.abs(np.asarray(ds_idle.h_ul_cross[idx])) ** 2))
        check(power == 0.0,
              f"没人发射时交叉链路信道本身就是零（实测功率 {power:.3e}）")
        n_sig, energy = _receive(ds_idle, idx, n_link, forced=True)
        print(f"  反例：三个槽位全空且强制资源全同 -> 发射源 {n_sig} 个，"
              f"接收机干扰能量 {energy:.3e}")
        check(n_sig == 0 and energy == 0.0,
              "没人发射的槽位在取货后不产生任何干扰（否则是凭空造出的污染）")

    ds_busy = _dataset(63, 8)
    occ_busy = ds_busy.srs_cross_link["slot_occupied"]
    busy = [i for i in range(occ_busy.shape[0]) if int(occ_busy[i].sum()) > 0]
    check(bool(busy), "构造出了槽位上真有人发射的样本（否则正例是空的）")
    if busy:
        idx = busy[0]
        n_hit, hit = _receive(ds_busy, idx, n_link, forced=True)
        _, miss = _receive(ds_busy, idx, n_link, forced=False)
        print(f"  正例：{int(occ_busy[idx].sum())} 个槽位有人 -> 发射源 {n_hit} 个，"
              f"资源全同时干扰 {hit:.3e}，资源正交时 {miss:.3e}")
        check(n_hit > 0 and hit > 0.0,
              "有人发射且资源碰撞时干扰必须还在（不能连真的一起杀掉）")
        check(hit > miss * 5.0,
              "资源碰撞时的干扰显著高于资源正交时（解扩正交性仍然成立）")

    # 数据集没有占用状态时，取货端必须拒绝而不是默认「都在发」
    refused = False
    try:
        ds_idle._npz.files  # noqa: SLF001  仅为触发惰性加载
        broken = ds_idle.srs_cross_link
        saved = broken.pop("slot_occupied")
        try:
            _receive(ds_idle, 0, n_link, forced=False)
        finally:
            broken["slot_occupied"] = saved
    except ValueError:
        refused = True
    check(refused,
          "旧数据集缺占用状态时取货端硬拒绝，不默认当成「每个槽位都有人在发」")

    # 11. SRS 资源计划不许随分块方式改变，否则并行生成会换一套碰撞结构
    hop = dict(channel_est_mode="ls_hop_sequential",
               measurements={"srs_cross_link_channels": True},
               srs_pilot_contamination_rho=1.0)
    whole = list(InternalSimSource(_cross_link_cfg(num_samples=6, **hop)).iter_samples())
    chunks = []
    for offset in (0, 3):
        chunks += list(InternalSimSource(_cross_link_cfg(
            num_samples=3, sample_index_offset=offset, **hop)).iter_samples())
    check(
        all(np.array_equal(a.h_ul_est, b.h_ul_est) for a, b in zip(whole, chunks))
        and all(
            np.array_equal(
                np.asarray(a.meta["srs_cross_link_collides"]),
                np.asarray(b.meta["srs_cross_link_collides"]),
            ) for a, b in zip(whole, chunks)
        ),
        "串行一整批与分块生成给出同一套 SRS 资源与碰撞结构（并行不变）",
    )


test_single_layer_terminals_can_pair()
test_per_ue_cap_reaches_the_air_and_rebuild_clears_stale_combinations()
test_rzf_reported_loading_equals_the_one_actually_used()
def test_receive_side_counts_acknowledgements_not_transmissions() -> None:
    """接收侧按**反馈到达时刻**归属测量窗，跨窗的净荷一个字节都不能丢。

    这条棘轮钉住一次真实的漏计：早先按"这个 TB 的首传发生在哪"归属，
    为的是让接收侧成为体验口径的严格子集。代价是**预热期发出、测量窗内
    才被确认的净荷被整段丢掉**——2 ms 短窗下接收侧报 0，而同一次仿真的
    体验口径是 637 Mbps。那不是保守，是丢数据。

    改回按首传归属，第一条就会红。

    顺带钉住"前沿项是个固定量"：同一批信道下，把窗从 2 ms 拉到 1900 ms，
    跨窗字节数**逐值不变**（它只取决于 HARQ 反馈时延，与窗长无关），
    占比从 100% 掉到 0.13%。所以窗长远大于反馈时延时才能说
    接收侧 <= 体验口径。
    """
    n_sample = 40
    point = sy.UeLinkTable(
        ue=0, sinr_db=np.full((n_sample, 4), 18.0),
        mcs=np.full((n_sample, 4), 16),
        se=np.full((n_sample, 4), la.MCS_TABLE_3[16].se),
        best_rank=np.ones(n_sample, dtype=int),
        best_se=np.full(n_sample, la.MCS_TABLE_3[16].se), geo_sinr_db=18.0,
        outage=np.zeros(n_sample, dtype=bool), mcs_table=3, target_bler=0.1,
        sinr_rbg_db=np.full((n_sample, 4, 17), 18.0),
        sinr_tx_db=np.full((n_sample, 4), 18.0),
        sinr_tx_rbg_db=np.full((n_sample, 4, 17), 18.0))

    def _run(duration_s: float, warmup_tti: int) -> dict:
        return sy.simulate(
            [point],
            sys_cfg=sy.SystemConfig(duration_s=duration_s, tdd_pattern="DDDSU"),
            traffic=sy.TrafficConfig(model="full_buffer"),
            sched=sy.SchedulerConfig(mu_enabled=False, olla_enabled=False),
            kpi=sy.KpiConfig(warmup_tti=warmup_tti), rng=rg.RngBook(3, 0)).cell

    old_lookup = ex._bler_lookup
    try:
        ex._bler_lookup = lambda _m, _s: 0.0      # 首传全部收对
        # 极短窗：预热 40 TTI、总共 44 TTI。DDDSU 下反馈要等上行时隙，
        # 所以窗内到达的每一份反馈都对应预热期发出的 TB。
        short = _run(0.022, 40)
        long_run = _run(2.0, 200)
    finally:
        ex._bler_lookup = old_lookup

    def _bytes(cell: dict, key: str) -> int:
        return int(round(cell[key] * 1e6 * cell["measurement_duration_s"] / 8))

    short_rx = _bytes(short, "dl_rx_mac_tput_mbps")
    short_pre = _bytes(short, "dl_rx_mac_tput_pre_window_mbps")
    long_pre = _bytes(long_run, "dl_rx_mac_tput_pre_window_mbps")
    print(f"  2 ms 窗：接收侧 {short['dl_rx_mac_tput_mbps']:.1f} Mbps "
          f"（{short_rx} B），其中跨窗 {short_pre} B、"
          f"占比 {short['dl_rx_mac_tput_pre_window_share']:.0%}")
    print(f"  1900 ms 窗：跨窗 {long_pre} B、"
          f"占比 {long_run['dl_rx_mac_tput_pre_window_share']:.2%}，"
          f"接收侧 {long_run['dl_rx_mac_tput_mbps']:.1f} / 体验 "
          f"{long_run['cell_served_mbps']:.1f} Mbps")

    check(short_rx > 0 and short["cell_served_mbps"] > 0,
          "短窗下窗内确认收到的净荷必须被记进接收侧，不能因为它发在预热期就归零")
    check(short["dl_rx_mac_tput_pre_window_share"] == 1.0 and short_pre == short_rx,
          "短窗下接收侧全部来自跨窗 TB，且这部分被单独报出来可核对")
    check(short_pre == long_pre,
          f"前沿项只由 HARQ 反馈时延决定，与窗长无关（两个窗都是 {short_pre} B）")
    check(long_run["dl_rx_mac_tput_pre_window_share"] < 0.01,
          "窗长远大于反馈时延时跨窗项可忽略（实测占比 < 1%）")
    check(long_run["dl_rx_mac_tput_mbps"] <= long_run["cell_served_mbps"] + 1e-9
          <= long_run["dl_tx_mac_tput_mbps"] + 1e-9,
          "长窗下接收侧 <= 体验口径 <= 发送侧仍然成立")


test_mac_throughput_meters_separate_sent_from_received()
test_receive_side_counts_acknowledgements_not_transmissions()
test_srs_ul_cross_link_and_pilot_contamination()


print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for item in FAILED:
        print("  - " + item)
    raise SystemExit(1)
print("跨模块物理不变量全部通过。")
