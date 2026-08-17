"""MU-MIMO：等效信道、配对、多用户预编码、功率分配、逐用户 SINR。

直接运行：python tests/test_mumimo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(errors="replace")

from superran import linklevel as ll  # noqa: E402
from superran import mumimo as mu  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def make_users(n_k=6, n_rb=4, n_bs=16, n_ue=4, seed=0, gains=None):
    """造一批用户信道 [T, RB, BS, UE]，可指定各自的增益差。"""
    rng = np.random.default_rng(seed)
    g = np.ones(n_k) if gains is None else np.asarray(gains, dtype=float)
    return [
        ((rng.standard_normal((1, n_rb, n_bs, n_ue))
          + 1j * rng.standard_normal((1, n_rb, n_bs, n_ue))) / np.sqrt(2) * g[k])
        for k in range(n_k)
    ]


# ---------------------------------------------------------------------------
sect("1  等效信道：把 UE 天线折叠成行向量")

_H = make_users()
_he = mu.effective_user_channels(_H, streams_per_user=1)
check(_he.shape == (6, 1, 4, 16), f"形状 [K,S,RB,BS]（实得 {_he.shape}）")
_he2 = mu.effective_user_channels(_H, streams_per_user=2)
check(_he2.shape == (6, 2, 4, 16), "多流时 S 维展开")
# 入口承诺同时支持 3D [RB,BS,UE]；去掉单快照维后必须与 4D 路径逐位一致。
_he_3d = mu.effective_user_channels([h[0] for h in _H], streams_per_user=2)
check(_he_3d.shape == _he2.shape and np.array_equal(_he_3d, _he2),
      "3D [RB,BS,UE] 与 4D [1,RB,BS,UE] 等效信道逐位一致")
# 第一条等效行的范数就是最大奇异值，第二条是次大 —— 顺序不能乱
_dl = _H[0].mean(axis=0)[0].conj().T
_sv = np.linalg.svd(_dl, compute_uv=False)
check(abs(np.linalg.norm(_he2[0, 0, 0]) - _sv[0]) < 1e-9, "第一流对应最大奇异值")
check(abs(np.linalg.norm(_he2[0, 1, 0]) - _sv[1]) < 1e-9, "第二流对应次大奇异值")

# ---------------------------------------------------------------------------
sect("2  配对：SUS 半正交用户选择")

# 造一组"有两个用户几乎共线"的信道，SUS 必须把其中一个踢掉
_rng = np.random.default_rng(4)
_base = (_rng.standard_normal((16,)) + 1j * _rng.standard_normal((16,))) / np.sqrt(2)
_Hc = make_users(n_k=5, seed=1)
_Hc[1] = _Hc[0] * 0.98 + _Hc[1] * 0.02          # 1 号与 0 号几乎共线
_hec = mu.effective_user_channels(_Hc)
_pr = mu.pair_users(_hec, criterion="sus", max_users=4, corr_threshold=0.5)
print(f"  选中 {_pr.users}，因相关被剔除 {_pr.dropped_by_corr}")
check(not (0 in _pr.users and 1 in _pr.users), "几乎共线的两个用户不会被配到一起")
check(bool(_pr.dropped_by_corr), "被剔除的用户如实记录，不是静默丢掉")
check(all(c <= 0.5 + 1e-9 for c in _pr.correlations),
      f"入选者与已选集的相关都不超过门限（实得 {np.round(_pr.correlations, 3)}）")

# 每个 RB 的公共复相位不携带功率/方向信息，不能改变宽带用户强度或配对。
_phase = np.exp(1j * _rng.uniform(-np.pi, np.pi, size=(_hec.shape[0], _hec.shape[2])))
_hec_rot = _hec * _phase[:, None, :, None]
_pr_rot = mu.pair_users(_hec_rot, criterion="sus", max_users=4, corr_threshold=0.5)
check(_pr_rot.users == _pr.users
      and _pr_rot.dropped_by_corr == _pr.dropped_by_corr
      and np.allclose(_pr_rot.correlations, _pr.correlations, atol=1e-10),
      "逐 RB 任意公共相位旋转不改变 SUS 配对（宽带只平均功率协方差）")
check(np.allclose(np.linalg.norm(mu._wideband_user_vectors(_hec_rot), axis=1),
                  np.linalg.norm(mu._wideband_user_vectors(_hec), axis=1), atol=1e-10),
      "逐 RB 相位翻转不让宽带用户强度凭空相消")

_pr_all = mu.pair_users(_hec, criterion="all")
check(len(_pr_all.users) == 5, "all 准则不做筛选")
_pr_one = mu.pair_users(_hec, criterion="best_single")
check(len(_pr_one.users) == 1, "best_single 只选一个")

# 流数不能超过发射天线数 —— 上限必须由代码兜住
_pr_cap = mu.pair_users(mu.effective_user_channels(make_users(n_k=40, n_bs=8),
                                                   streams_per_user=1),
                        criterion="sus", max_users=99, corr_threshold=0.99)
check(len(_pr_cap.users) <= 8, f"配对数不超过发射天线数（实得 {len(_pr_cap.users)}）")

# 权重必须真的改变选择结果（比例公平的基础）
_w = np.ones(5) * 0.01
_w[4] = 100.0
_pr_w = mu.pair_users(_hec, criterion="sus", max_users=1, weights=_w)
check(_pr_w.users == [4] and _pr_w.weights_used, "权重能把弱用户顶进配对（比例公平的基础）")

# ---------------------------------------------------------------------------
sect("3  预编码：方向与功率必须解耦")

_hs = mu.effective_user_channels(make_users(n_k=4, n_bs=16, seed=2))
_W, _p = mu.mu_precoder(_hs, method="zf", noise_power=0.01)
check(_W.shape == (4, 16, 4) and _p.shape == (4, 4), f"形状 (W {_W.shape}, p {_p.shape})")
_col = np.linalg.norm(_W[0], axis=0)
check(np.allclose(_col, 1.0), f"预编码逐列单位范数，只表示方向（实得 {np.round(_col, 4)}）")
check(np.allclose(_p.sum(axis=1), 1.0), "逐 RB 总功率归一到 1（与 SU 口径一致）")
check(np.allclose(_p[0], 0.25), "equal 分配就是等分")

_Wr, _pr2 = mu.mu_precoder(_hs, method="rzf", noise_power=0.01)
check(np.allclose(np.linalg.norm(_Wr[0], axis=0), 1.0), "RZF 同样逐列归一")
_Ww, _pw = mu.mu_precoder(_hs, method="zf", noise_power=0.01,
                          power_allocation="waterfilling")
check(np.allclose(_pw.sum(axis=1), 1.0, atol=1e-9), "注水后总功率仍归一")
check(not np.allclose(_pw[0], _pw[0][0]), "注水会给不同流不同功率（不是等分）")

# ZF 的定义就是把用户间干扰清零
_hm = _hs[:, :, 0, :].reshape(4, 16)
_G = _hm @ _W[0]
_off = np.abs(_G - np.diag(np.diag(_G))).max()
check(_off < 1e-8, f"ZF 后用户间耦合为零（最大非对角 {_off:.2e}）")

# 流数超过天线数必须直接报错，不能给一个看似正常的解
try:
    mu.mu_precoder(mu.effective_user_channels(make_users(n_k=20, n_bs=8)), method="zf")
    check(False, "流数超过天线数时报错")
except ValueError as _e:
    check("超过" in str(_e), f"流数超过天线数时报错（{_e}）")

# ---------------------------------------------------------------------------
sect("4  功率分配不能退化成信道求逆功控")

# **踩过的坑。** 早先用一个全局标量把 tr(WW^H) 归一，ZF 满足 HW=c·I，
# 于是所有用户接收电平被强行拉平、弱用户吃掉大部分功率，
# 公平度恒等于 1.000 —— 看起来像"MU 天生公平"，其实是功率分配被写死了。
_Hg = make_users(n_k=4, n_bs=16, seed=3, gains=[1.0, 1.0, 1.0, 0.2])
_res = mu.mu_link_performance(_Hg, noise_power=0.01, precoder="zf", criterion="all")
print(f"  逐用户谱效 {np.round(_res.se_per_user, 3)}  Jain {_res.jain_fairness:.4f}")
check(_res.se_per_user.std() > 1e-6,
      "增益差 5 倍的用户不该拿到一模一样的谱效（那是信道求逆功控的症状）")
check(_res.jain_fairness < 1.0 - 1e-6, f"公平度不恒等于 1（实得 {_res.jain_fairness}）")
check(_res.se_per_user[3] < _res.se_per_user[0], "弱用户谱效确实更低")
check(_res.power_allocation == "equal", "功率分配方式跟着结果一起返回")

# ---------------------------------------------------------------------------
sect("5  MU 增益与 CSI 敏感性")

_Hm = make_users(n_k=8, n_bs=32, n_ue=4, seed=5)
_su = float(np.mean([  # SU 对照：同样总功率，一次只服务一个用户
    mu.mu_link_performance([h], noise_power=0.01, criterion="all").sum_se for h in _Hm
]))
_mu4 = mu.mu_link_performance(_Hm, noise_power=0.01, precoder="rzf",
                              criterion="sus", max_users=4)
print(f"  SU（单用户单流）{_su:.3f} → MU 配 {len(_mu4.users)} 个 {_mu4.sum_se:.3f}")
check(_mu4.sum_se > _su, "MU 和谱效高于单用户单流（空间复用确实有增益）")
check(len(_mu4.users) > 1, "确实配了多个用户")

# CSI 变差 -> ZF 零陷变浅 -> 残余干扰上升 -> 谱效下降。三者必须同向。
_prev_leak, _prev_se = -1.0, 1e9
print(f"  {'CSI 误差':<12}{'和谱效':>9}{'残余干扰':>12}")
for _err in (0.0, 0.03, 0.1, 0.3):
    _rg = np.random.default_rng(7)
    _He = [h + (_rg.standard_normal(h.shape) + 1j * _rg.standard_normal(h.shape))
           * _err * float(np.std(np.abs(h))) for h in _Hm]
    _r = mu.mu_link_performance(_Hm, h_users_for_precoding=_He, noise_power=0.01,
                                precoder="zf", criterion="sus", max_users=4)
    print(f"  {_err:<12.2f}{_r.sum_se:>9.3f}{_r.leakage_ratio:>12.3e}")
    if _err > 0:
        check(_r.leakage_ratio > _prev_leak, f"CSI 越差残余干扰越大（err={_err}）")
        check(_r.sum_se < _prev_se, f"CSI 越差和谱效越低（err={_err}）")
        check(_r.csi_for_precoding == "h_est", "CSI 口径如实带回结果")
    _prev_leak, _prev_se = _r.leakage_ratio, _r.sum_se

# 理想 CSI + ZF 的检测后残余应为数值零。LMMSE 求逆会留下约 1e-8 的
# 浮点残差，不能拿 scalar-effective 路径的 1e-12 阈值误杀完整接收机。
_ideal = mu.mu_link_performance(_Hm, noise_power=0.01, precoder="zf",
                                 criterion="sus", max_users=4)
check(_ideal.leakage_ratio < 1e-6,
      f"理想 CSI 下 ZF 残余干扰为零（实得 {_ideal.leakage_ratio:.2e}）")
check(_ideal.csi_for_precoding == "h_true", "没传估计信道时标成 h_true")

# rank2 接收基旋转反例：两个用户占据互不重叠的发射子空间，因此没有真正的
# MU 干扰。UE0 的两条本用户流在 h_true 中旋转 45°；固定 scalar combiner 会把
# 另一条可联合解调的流误记成干扰，而每 UE LMMSE 应恢复两条流。
_h0p = np.zeros((1, 1, 4, 2), dtype=complex)
_h1p = np.zeros_like(_h0p)
_h0p[0, 0, 0, 0], _h0p[0, 0, 1, 1] = 2.0, 1.0
_h1p[0, 0, 2, 0], _h1p[0, 0, 3, 1] = 2.0, 1.0
_theta = np.pi / 4
_rot = np.eye(4)
_rot[:2, :2] = [[np.cos(_theta), -np.sin(_theta)],
                 [np.sin(_theta), np.cos(_theta)]]
_h0t = np.einsum("ab,trbc->trac", _rot, _h0p)
_he_t = mu.effective_user_channels([_h0t, _h1p], streams_per_user=2)
_he_p = mu.effective_user_channels([_h0p, _h1p], streams_per_user=2)
_scalar = mu.mu_link_performance_from_effective(
    _he_t, _he_p, noise_power=np.array([1e-3, 1e-3]),
    precoder="zf", rb_per_rbg=1)
_lmmse = mu.mu_link_performance_lmmse(
    [_h0t, _h1p], [_h0p, _h1p], noise_power=np.array([1e-3, 1e-3]),
    streams_per_user=2, precoder="zf", rb_per_rbg=1)
check(_scalar.sinr_per_user_db[0] < 3.0,
      "固定接收基反例确实会把本用户流旋转误判成强干扰")
check(_lmmse.sinr_per_user_db[0] > 20.0
      and _lmmse.sum_se > _scalar.sum_se + 10.0,
      "逐用户 LMMSE 联合解调恢复 rank2 接收基旋转，不把本用户流算成 MU 干扰")
check(_lmmse.receiver == "per_user_lmmse" and _lmmse.leakage_ratio < 1e-12,
      "MU 结果显式上报 LMMSE 接收机，正交用户反例的检测后泄漏为零")

# 随机非正交信道再用闭式误差协方差独立重算，防止上面的构造反例只验证了
# 一个特殊几何。对用户 u：E=(I+Gdᴴ Rn⁻¹ Gd)⁻¹，SINR_k=1/Ekk-1。
_rg_lmmse = np.random.default_rng(2026080917)
_hp_lmmse, _ht_lmmse = [], []
for _ in range(2):
    _hp_u = ((_rg_lmmse.standard_normal((1, 1, 8, 2))
              + 1j * _rg_lmmse.standard_normal((1, 1, 8, 2))) / np.sqrt(2))
    _ht_u = _hp_u + (
        _rg_lmmse.standard_normal(_hp_u.shape)
        + 1j * _rg_lmmse.standard_normal(_hp_u.shape)) * 0.08
    _hp_lmmse.append(_hp_u)
    _ht_lmmse.append(_ht_u)
_noise_lmmse = np.array([0.07, 0.11])
_random_lmmse = mu.mu_link_performance_lmmse(
    _ht_lmmse, _hp_lmmse, noise_power=_noise_lmmse,
    streams_per_user=2, precoder="rzf", rb_per_rbg=1)
_he_lmmse = mu.effective_user_channels(_hp_lmmse, streams_per_user=2)
_w_lmmse, _pw_lmmse = mu.mu_precoder(
    _he_lmmse, method="rzf", noise_power=_noise_lmmse,
    power_constraint="ebf")
_q_lmmse = _w_lmmse * np.sqrt(_pw_lmmse)[:, None, :]
_closed_form_se = []
for _u in range(2):
    _g = _ht_lmmse[_u][0, 0].conj().T @ _q_lmmse[0]
    _own = np.arange(_u * 2, (_u + 1) * 2)
    _other = np.setdiff1d(np.arange(4), _own)
    _gd, _gi = _g[:, _own], _g[:, _other]
    _rn = _noise_lmmse[_u] * np.eye(2) + _gi @ _gi.conj().T
    _error_cov = np.linalg.inv(
        np.eye(2) + _gd.conj().T @ np.linalg.solve(_rn, _gd))
    _sinr_closed = 1.0 / np.real(np.diag(_error_cov)) - 1.0
    _closed_form_se.append(float(np.sum(np.log2(1.0 + _sinr_closed))))
check(np.allclose(_random_lmmse.se_per_user, _closed_form_se,
                  rtol=1e-10, atol=1e-10),
      "随机 MU LMMSE 逐用户谱效与 1/Ekk-1 闭式解逐位一致")
# ---------------------------------------------------------------------------
sect("6  单码字谱效与 rank 自适应")

# 用户级 SINR：RBG 内线性平均、RBG 间与流间 dB 域平均
_s = np.full((32, 1), 10.0)
check(abs(mu.user_sinr_db(_s, rb_per_rbg=16) - 10.0) < 1e-9, "全平信道的用户级 SINR 就是它本身")
# **dB 域平均必须比线性平均保守** —— 单码字会被深衰的 RBG 拖下去
_v = np.array([[100.0]] * 16 + [[0.01]] * 16)
_db = mu.user_sinr_db(_v, rb_per_rbg=16)
_lin = 10 * np.log10(_v.mean())
print(f"  半好半坏：dB 域平均 {_db:.2f} dB，线性平均 {_lin:.2f} dB")
check(_db < _lin - 10, "dB 域平均显著低于线性平均（单码字被深衰 RBG 拖累）")
check(abs(_db) < 1e-6, f"两个 RBG 各 +20/-20 dB，dB 域平均是 0（实得 {_db}）")

# 谱效 = rank x MCS 谱效
_se1, _m1 = mu.se_from_sinr(20.0, 1)
_se2, _m2 = mu.se_from_sinr(20.0, 2)
check(abs(_se2 - 2 * _se1) < 1e-9, "同 SINR 下谱效严格正比于 rank")
check(mu.se_from_sinr(30.0, 1)[1].index >= _m1.index, "SINR 越高 MCS 不降")

# rank 自适应
_rng2 = np.random.default_rng(11)
_hh = ((_rng2.standard_normal((1, 32, 16, 4)) + 1j * _rng2.standard_normal((1, 32, 16, 4)))
       / np.sqrt(2))
_lo = mu.su_rank_adaptation(_hh, noise_power=mu.noise_from_geometric_sinr(_hh, 0.0))
_hi = mu.su_rank_adaptation(_hh, noise_power=mu.noise_from_geometric_sinr(_hh, 30.0))
print(f"  几何 SINR 0 dB -> rank {_lo.rank} MCS {_lo.mcs}；30 dB -> rank {_hi.rank} MCS {_hi.mcs}")
check(_lo.rank <= _hi.rank, "信噪比高时选的秩不低于低信噪比时")
check(len(_hi.candidates) == 4, "四个 rank 候选都算过并留在结果里")
check(all(c["rank"] == i + 1 for i, c in enumerate(_hi.candidates)), "候选按 rank 排列")
check(_hi.se == max(c["se"] for c in _hi.candidates), "选中的就是谱效最高的候选")

# **噪声口径**：当前几何 SINR 是预波束定义，必须锚到 mean(|h|²)。
_n_anchor = mu.noise_from_geometric_sinr(_hh, 15.0)
_n_expected = float(np.mean(np.abs(_hh) ** 2)) / 10 ** 1.5
check(np.isclose(_n_anchor, _n_expected, rtol=1e-12),
      "几何 SINR 噪声锚点等于 mean(|h|²)/SINR")

_r1 = [c for c in mu.su_rank_adaptation(
    _hh, noise_power=mu.noise_from_geometric_sinr(_hh, 12.0)).candidates
    if c["rank"] == 1][0]
_bf = 10 * np.log10(
    ll.rank1_reference_power(_hh) / ll.prebeam_reference_power(_hh)
)
check(abs(_r1["sinr_db"] - (12.0 + _bf)) < 1.5,
      f"rank1 用户级 SINR含 H 的数字 BF 增益（实得 {_r1['sinr_db']:.1f} dB）")

# ---------------------------------------------------------------------------
sect("7  SU / MU 自适应")

_Hs = make_users(n_k=6, n_rb=32, n_bs=32, n_ue=4, seed=13)
_npow = mu.noise_from_geometric_sinr(_Hs[0], 15.0)
_dec = mu.su_mu_adaptation(_Hs, noise_power=_npow)
print(f"  {_dec.note}")
print(f"  判决 {_dec.mode}：小区谱效 {_dec.cell_se:.3f}"
      f"（SU {_dec.su_se:.3f} / MU {_dec.mu_se:.3f}）")
check(_dec.mode in ("SU", "MU"), "给出明确判决")
check(abs(_dec.cell_se - max(_dec.su_se, _dec.mu_se)) < 1e-9, "小区谱效取两者中的高者")
check(_dec.su_rank <= mu.SU_MAX_RANK, f"SU 秩不超过 {mu.SU_MAX_RANK}")
check(all(d["rank"] <= mu.MU_MAX_RANK for d in _dec.mu_per_user),
      f"MU 每用户秩不超过 {mu.MU_MAX_RANK}（工程约束）")
check(bool(_dec.mu_users), "MU 方案确实配了人")
check(len(_dec.mu_per_user) == len(_dec.mu_users), "逐用户明细齐全")

# SU/MU 比较的 CSI 信息集必须对称：估计权与真实信道正交时，SU 也必须吃到
# 预编码失配，不能只让 MU 用 h_est、SU 偷看 h_true。
_h_true, _h_est = [], []
for _u in range(2):
    _ht = np.zeros((1, 32, 4, 1), dtype=np.complex128)
    _hp = np.zeros_like(_ht)
    _ht[:, :, _u, 0] = 1.0
    _hp[:, :, _u + 2, 0] = 1.0
    _h_true.append(_ht)
    _h_est.append(_hp)
_oracle = mu.su_mu_adaptation(
    _h_true, noise_power=np.array([0.01, 0.01]),
    max_mu_users=2, mu_rank=1)
_mismatch = mu.su_mu_adaptation(
    _h_true, h_users_for_precoding=_h_est,
    noise_power=np.array([0.01, 0.01]), max_mu_users=2, mu_rank=1)
check(_mismatch.su_se < _oracle.su_se / 5,
      "显式 h_est 时 SU 也只用估计信道选权，正交错估计会显著降低 SU 实际谱效")

# 功率按流均分：rank2 的用户拿 2 份
_he2 = mu.effective_user_channels(_Hs[:3], streams_per_user=2)
_, _pp = mu.mu_precoder(_he2, method="zf", noise_power=_npow)
check(abs(_pp[0].sum() - 1.0) < 1e-9, "总功率仍归一")
check(abs(_pp[0][0] - 1.0 / 6) < 1e-9, "6 条流每流 1/6，即 rank2 的用户拿 1/3")



# ---------------------------------------------------------------------------
sect("8  RBG 粒度：降 16 倍算量而不改结论")

_rng3 = np.random.default_rng(31)
_hb = ((_rng3.standard_normal((272, 32, 4)) + 1j * _rng3.standard_normal((272, 32, 4)))
       / np.sqrt(2))
_red = mu.rbg_reduce(_hb, 16)
check(_red.shape == (17, 32, 4), f"272 RB -> 17 RBG（实得 {_red.shape}）")
check(mu.rbg_reduce(_hb, 1).shape == (272, 32, 4), "rb_per_rbg=1 退回 RB 粒度")

_h4 = _hb[None]
_np = mu.noise_from_geometric_sinr(_h4, 15.0)
_rb_res = mu.su_rank_adaptation(_h4, noise_power=_np, rb_per_rbg=1)
_rbg_res = mu.su_rank_adaptation(_h4, noise_power=_np, rb_per_rbg=16)
print(f"  RB 粒度 rank {_rb_res.rank} MCS {_rb_res.mcs} SE {_rb_res.se:.3f}")
print(f"  RBG粒度 rank {_rbg_res.rank} MCS {_rbg_res.mcs} SE {_rbg_res.se:.3f}")
check(_rb_res.rank == _rbg_res.rank, "两种粒度选出同一个 rank")
check(abs(_rb_res.mcs - _rbg_res.mcs) <= 1, "MCS 最多差一档")
check(abs(_rb_res.se - _rbg_res.se) / max(_rb_res.se, 1e-9) < 0.05,
      f"谱效差 <5%（实得 {abs(_rb_res.se - _rbg_res.se) / max(_rb_res.se, 1e-9):.1%}）")

# **取代表点而不是平均。** 平均会把频选衰落抹平、抬高信道条件数，进而高估 rank。
_flat = np.repeat(_hb.mean(axis=0, keepdims=True), 17, axis=0)
_sv_avg = np.linalg.svd(_flat[0].conj().T, compute_uv=False)
_sv_rep = np.linalg.svd(_red[0].conj().T, compute_uv=False)
print(f"  平均后奇异值比 σ4/σ1 = {_sv_avg[3] / _sv_avg[0]:.3f}；"
      f"取代表点 {_sv_rep[3] / _sv_rep[0]:.3f}")
check(_sv_rep[3] / _sv_rep[0] < _sv_avg[3] / _sv_avg[0] * 3,
      "取代表点保留了真实的奇异值分布，没有被平均抹平")

# ---------------------------------------------------------------------------
sect("9  CSI 误差鲁棒 RZF：不与每天线约束混为一谈")

_reg = mu.robust_rzf_regularization(
    n_stream=4, n_bs=8, mean_noise_power=0.01,
    csi_error_variance=0.01)
check(abs(_reg.noise_loading - 0.04) < 1e-12,
      "常规 RZF 噪声加载为 N_stream·sigma_n²/P")
check(abs(_reg.csi_error_loading - 0.08) < 1e-12,
      "CSI 不确定性加载为 N_BS·sigma_e²")

_rng_r = np.random.default_rng(65)
_ht_r = ((_rng_r.standard_normal((4, 1, 4, 8))
          + 1j * _rng_r.standard_normal((4, 1, 4, 8))) / np.sqrt(2))
_err_std = 0.1
_he_r = _ht_r + _err_std * (
    _rng_r.standard_normal(_ht_r.shape) + 1j * _rng_r.standard_normal(_ht_r.shape)
) / np.sqrt(2)
_w0, _p0 = mu.mu_precoder(_he_r, method="rzf", noise_power=0.01)
_wz, _pz = mu.mu_precoder(
    _he_r, method="rzf", noise_power=0.01, csi_error_variance=0.0)
check(np.array_equal(_w0, _wz) and np.array_equal(_p0, _pz),
      "sigma_e²=0 与历史 RZF 逐位兼容")
_base_r = mu.mu_link_performance_from_effective(
    _ht_r, _he_r, noise_power=0.01, precoder="rzf")
_robust_r = mu.mu_link_performance_from_effective(
    _ht_r, _he_r, noise_power=0.01, precoder="rzf",
    csi_error_variance=_err_std ** 2)
print(f"  固定失配反例：noise-only {_base_r.sum_se:.4f}，"
      f"robust {_robust_r.sum_se:.4f} bit/s/Hz")
check(_robust_r.sum_se > _base_r.sum_se + 0.2,
      "固定 CSI 失配反例中鲁棒加载提高真实和谱效")
check(_robust_r.rzf_regularization is not None
      and _robust_r.rzf_regularization["csi_error_loading"] > 0,
      "结果显式带回鲁棒加载分解，不能只给一个来路不明的 alpha")

# ---------------------------------------------------------------------------
sect("10  频域聚合：非整数倍 RB 与批量化的等价性")
# 载波不是 RBG 大小整数倍时，最后不足 16 RB 的尾组也必须保留。
# 旧实现在 n_rb <= step 时原样返回，16 RB 的载波会被当成 16 个 RBG；
# 另一版又只保留完整分组，让 51 RB 的最后 3 RB 凭空消失。
for _nrb, _want in ((272, 17), (51, 4), (48, 3), (16, 1), (8, 1)):
    _hh = np.zeros((_nrb, 4, 2), dtype=complex)
    _got = mu.rbg_reduce(_hh, 16).shape[0]
    check(_got == _want, f"{_nrb} RB -> {_want} 个含尾组的 RBG（实得 {_got}）")
check(mu.rbg_reduce(np.zeros((272, 4, 2), dtype=complex), 1).shape[0] == 272,
      "rb_per_rbg=1 仍退回 RB 粒度")


def _ref_rbg_sinr_db(s, step):
    """逐组切片的参照实现——批量化只允许更快，不允许改数。"""
    s = np.asarray(s, dtype=float)
    if s.ndim == 1:
        s = s[:, None]
    n_rb = s.shape[0]
    st = max(1, min(int(step), n_rb))
    n = int(np.ceil(n_rb / st))
    lin = np.stack([s[i * st:(i + 1) * st].mean(axis=0) for i in range(n)])
    return np.mean(10.0 * np.log10(np.maximum(lin, 1e-12)), axis=1)


_rng_agg = np.random.default_rng(4242)
_agg_worst = 0.0
for _nrb, _st in ((17, 1), (272, 16), (272, 1), (51, 16), (100, 16), (16, 16)):
    _x = np.abs(_rng_agg.standard_normal((_nrb, 4))) + 0.01
    _agg_worst = max(_agg_worst, float(np.max(np.abs(
        mu.rbg_sinr_db(_x, rb_per_rbg=_st) - _ref_rbg_sinr_db(_x, _st)))))
print(f"  rbg_sinr_db 与逐组切片参照的最大绝对差 {_agg_worst:.3e}")
check(_agg_worst == 0.0, "rbg_sinr_db 的整除快路径与逐组切片逐位相同")

# effective_user_channels / mu_precoder 改成堆叠 SVD/pinv 之后必须逐位不变。
_rng_b = np.random.default_rng(20260815)
_hb_list = [((_rng_b.standard_normal((17, 32, 4))
              + 1j * _rng_b.standard_normal((17, 32, 4))) / np.sqrt(2))
            for _ in range(2)]
_he_b = mu.effective_user_channels([x[None] for x in _hb_list], streams_per_user=2)
_ref_he = np.zeros_like(_he_b)
for _u, _hx in enumerate(_hb_list):
    for _f in range(_hx.shape[0]):
        _, _sv, _vh = np.linalg.svd(_hx[_f].conj().T, full_matrices=False)
        for _s in range(min(2, _vh.shape[0])):
            _ref_he[_u, _s, _f] = _sv[_s] * _vh[_s]
check(np.array_equal(_he_b, _ref_he),
      "effective_user_channels 的堆叠 SVD 与逐 RB 循环逐位相同")

# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("MU-MIMO 配对、预编码、功率分配、CSI 敏感性全部通过。")
