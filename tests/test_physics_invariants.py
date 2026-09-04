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
from scipy.stats import t as student_t

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superran import beamforming as bf  # noqa: E402
from superran import csi_aging as ca  # noqa: E402
from superran import experience as ex  # noqa: E402
from superran import interference as itf  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import linklevel as ll  # noqa: E402
from superran import measure  # noqa: E402
from superran import mumimo as mu  # noqa: E402
from superran import rng as rg  # noqa: E402
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
check(over.cell["ue_experience_eligible"] == 1
      and over.cell["ue_experience_measured"] == 0
      and over.cell["cell_experienced_mbps"] == 0.0,
      "有到达但无完成 burst 的饿死 UE 以 0 留在体验分布中")

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
# 全带宽、每忙 TTI 恰好一个 SU，且体验类 KPI 报 None 而不是 0。
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
      "满缓冲下按需 RBG 退化成全带宽：每个忙 TTI 恰好服务 1 个 SU")
check(abs(float(fb.cell["resource_utilization"]) - 1.0) < 1e-9,
      "满缓冲下 RBG 全部用满，没有留空的尾料")
check(fb.cell["cell_experienced_mbps"] is None
      and fb.cell["drb_throughput_rel19_mbps"] is None,
      "busy period 永不结束，体验速率报 None 而不是假装测到 0")
check(float(fb.cell["cell_served_mbps"]) > 0.0,
      "容量口径仍然照常输出 cell_served_mbps")


print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for item in FAILED:
        print("  - " + item)
    raise SystemExit(1)
print("跨模块物理不变量全部通过。")
