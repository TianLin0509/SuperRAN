"""MU-MIMO：用户配对 + 多用户预编码 + 逐用户 SINR。

64 端口 AAU 的价值有一大半在多用户复用上。SU 只回答"一个用户能跑多快"，
MU 才回答"这套阵列一个小区能扛多少"。

三段拆开，和 SU 那条链一样可以各自替换：

    逐用户等效信道  →  配对（选谁一起发）  →  多用户预编码  →  逐用户 SINR

**功率约定与 SU 严格一致：全小区总发射功率 = 1。**
预编码矩阵只表示**方向**（逐列单位范数，与 Sionna 的 ``rzf_precoding_matrix``
一致），分多少功率由 ``power_allocation`` 显式决定。这两件事必须分开——
合成一个全局标量会退化成信道求逆功控，见 :func:`mu_precoder` 里那段。
但**总功率不能照搬 Sionna 的 ``tr(GG^H)=K``**：那等于每流各给一份，
MU 相对 SU 白拿 K 倍，"MU 增益"里一大半就成了功率增益。

**CSI 口径是这一块最容易出事的地方。** 用 ``h_true`` 做 ZF 能得到教科书里
那条漂亮曲线，用 ``h_est`` 会掉一大截——因为 ZF 零陷的深度完全由 CSI 精度决定。
所以 ``h_for_precoding`` 和评估用的信道是分开的两个参数，
和 SU 的 :func:`linklevel.link_performance` 同一套约定，门 2 能查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

_EPS = 1e-30

PairingCriterion = Literal["sus", "greedy_sum_rate", "all", "best_single"]
MuPrecoder = Literal["zf", "rzf", "mrt"]
PowerAllocation = Literal["equal", "waterfilling"]


# ---------------------------------------------------------------------------
# 1 · 逐用户等效信道
# ---------------------------------------------------------------------------
def effective_user_channels(
    h_users: list[np.ndarray] | np.ndarray,
    *,
    streams_per_user: int = 1,
) -> np.ndarray:
    """把每个用户的 MIMO 信道压成 ``streams_per_user`` 条等效行向量。

    输入每个用户 ``[T, RB, BS_ant, UE_ant]``，输出 ``[K, S, RB, BS_ant]``。

    做法：在每个 RB 上对下行信道 ``H_u^H``（``[UE_ant, BS_ant]``）做 SVD，
    取前 S 个左奇异向量当接收合并权，得到等效行 ``u_s^H H_u^H = σ_s v_s^H``。

    **这一步把"用户有几根天线"折叠掉了。** 之后的配对与预编码都在
    等效行向量上做——这是 MU-MIMO 的标准处理，也是 SUS、SLNR 这些准则
    赖以成立的前提（它们都假设每流一个行向量）。

    代价要说清：接收合并权是**单用户最优**选的（不知道最终会和谁配对），
    严格最优应当和配对联合优化。业界普遍接受这个次优，因为联合优化是 NP 难。
    """
    hs = [np.asarray(h) for h in h_users]
    if not hs:
        raise ValueError("至少要有一个用户")
    n_rb = hs[0].shape[1]
    n_bs = hs[0].shape[2]
    s_max = int(streams_per_user)

    out = np.zeros((len(hs), s_max, n_rb, n_bs), dtype=np.complex128)
    for u, h in enumerate(hs):
        hb = h.mean(axis=0) if h.ndim == 4 else h        # [RB, BS, UE]
        for f in range(n_rb):
            dl = hb[f].conj().T                          # [UE, BS] 下行矩阵
            uu, sv, vh = np.linalg.svd(dl, full_matrices=False)
            for s in range(min(s_max, vh.shape[0])):
                out[u, s, f] = sv[s] * vh[s].conj()      # σ_s · v_s^H 的共轭转置形式
    return out


# ---------------------------------------------------------------------------
# 2 · 配对
# ---------------------------------------------------------------------------
@dataclass
class Pairing:
    """一次配对的结果与它的依据。"""

    users: list[int]
    criterion: str
    max_users: int
    corr_threshold: float
    correlations: list[float] = field(default_factory=list)  # 入选时与已选集的最大相关
    dropped_by_corr: list[int] = field(default_factory=list)
    weights_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "users": self.users,
            "num_paired": len(self.users),
            "criterion": self.criterion,
            "max_users": self.max_users,
            "corr_threshold": self.corr_threshold,
            "correlations": [round(float(c), 4) for c in self.correlations],
            "dropped_by_corr": self.dropped_by_corr,
            "weights_used": self.weights_used,
        }


def pair_users(
    h_eff: np.ndarray,
    *,
    criterion: PairingCriterion = "sus",
    max_users: int = 4,
    corr_threshold: float = 0.5,
    weights: np.ndarray | None = None,
    noise_power: float = 0.0,
) -> Pairing:
    """选出这一次一起发的用户集合。

    ``h_eff`` 是 :func:`effective_user_channels` 的输出 ``[K, S, RB, BS]``；
    配对在**全带宽平均**的等效信道上做（宽带配对，与逐 RB 配对相对——
    后者更优但要求逐 RB 反馈，现网基本不用）。

    准则
    ----
    ``"sus"``（默认，Yoo & Goldsmith 2006 半正交用户选择）
        贪心：先选最强的，之后每轮选**在已选集正交补空间里投影最长**的那个，
        并剔除与已选集相关系数超过 ``corr_threshold`` 的候选。
        复杂度 O(K²·max_users)，是业界最常用的实用准则。
    ``"greedy_sum_rate"``
        贪心：每轮真的把候选加进去算一遍 ZF 和速率，选增量最大的，
        增量为负就停。更准，代价是每轮要做 ``K`` 次矩阵求逆。
    ``"best_single"``
        只选最强的一个用户（SU 对照组）。
    ``"all"``
        全选，不做正交性筛选。**别想当然认为它更差**：64 端口只服务 12 个用户时
        空间自由度富余，实测 12 用户全选（74.24）反而高于 SUS 选 4 个（46.07）。
        配对真正开始起作用是在用户数逼近端口数、或 CSI 有误差的时候——
        那时把强相关的用户塞进同一组，ZF 会互相把功率吃光。

    ``weights`` 给比例公平用：长度 ``K`` 的正权重（通常取历史速率的倒数），
    会乘进选择度量。不给就是纯吞吐最大化，**会饿死边缘用户**。
    """
    he = np.asarray(h_eff)
    n_k, n_s, _n_rb, n_bs = he.shape
    # 宽带配对：等效信道对 RB 平均后每用户拿一条主行向量
    g = he.mean(axis=2)[:, 0, :]                          # [K, BS]
    norms = np.linalg.norm(g, axis=1)
    w = np.ones(n_k) if weights is None else np.asarray(weights, dtype=float)
    if w.shape != (n_k,):
        raise ValueError(f"weights 长度必须是 {n_k}，实得 {w.shape}")

    cap = max(1, min(int(max_users), n_bs // max(n_s, 1), n_k))

    if criterion == "best_single":
        return Pairing([int(np.argmax(norms * w))], criterion, cap, corr_threshold,
                       weights_used=weights is not None)
    if criterion == "all":
        return Pairing(list(range(n_k)), criterion, cap, corr_threshold,
                       weights_used=weights is not None)
    if criterion == "greedy_sum_rate":
        return _greedy_sum_rate(he, cap, noise_power, w, corr_threshold)

    # --- SUS ---
    sel: list[int] = []
    corrs: list[float] = []
    dropped: list[int] = []
    cand = set(range(n_k))
    basis: list[np.ndarray] = []                          # 已选方向的正交基

    while cand and len(sel) < cap:
        best, best_val, best_corr = -1, -1.0, 0.0
        for i in sorted(cand):
            gi = g[i].copy()
            for b in basis:                               # 投到已选集的正交补
                gi = gi - b * (b.conj() @ gi)
            val = float(np.linalg.norm(gi)) * float(w[i])
            if val > best_val:
                best, best_val = i, val
                best_corr = (
                    max(abs(complex(g[i].conj() @ g[j]))
                        / max(norms[i] * norms[j], _EPS) for j in sel)
                    if sel else 0.0
                )
        if best < 0 or best_val <= _EPS:
            break
        sel.append(best)
        corrs.append(best_corr)
        cand.discard(best)

        gb = g[best].copy()
        for b in basis:
            gb = gb - b * (b.conj() @ gb)
        nb = float(np.linalg.norm(gb))
        if nb > _EPS:
            basis.append(gb / nb)

        # 剔除与刚选中的这个太相关的候选——它们再进来只会互相压
        for i in list(cand):
            c = abs(complex(g[i].conj() @ g[best])) / max(norms[i] * norms[best], _EPS)
            if c > corr_threshold:
                cand.discard(i)
                dropped.append(i)

    return Pairing(sel, "sus", cap, corr_threshold, corrs, sorted(dropped),
                   weights_used=weights is not None)


def _greedy_sum_rate(he: np.ndarray, cap: int, noise_power: float,
                     w: np.ndarray, corr_threshold: float) -> Pairing:
    """每轮真算一遍 ZF 和速率，选增量最大的；增量为负就停。"""
    n_k = he.shape[0]
    sel: list[int] = []
    best_rate = 0.0
    while len(sel) < cap:
        gain_best, cand_best = 0.0, -1
        for i in range(n_k):
            if i in sel:
                continue
            trial = [*sel, i]
            r = float(np.sum(_zf_sum_rate(he[trial], noise_power) * w[trial]))
            if r - best_rate > gain_best:
                gain_best, cand_best = r - best_rate, i
        if cand_best < 0:
            break
        sel.append(cand_best)
        best_rate += gain_best
    return Pairing(sel, "greedy_sum_rate", cap, corr_threshold,
                   weights_used=not np.allclose(w, 1.0))


def _zf_sum_rate(he_sel: np.ndarray, noise_power: float) -> np.ndarray:
    """给定用户集，ZF 之后的逐用户谱效（宽带平均），用于贪心搜索。"""
    res = mu_link_performance_from_effective(he_sel, he_sel, noise_power=noise_power,
                                             precoder="zf")
    return res.se_per_user


# ---------------------------------------------------------------------------
# 3 · 多用户预编码
# ---------------------------------------------------------------------------
def mu_precoder(
    h_eff_sel: np.ndarray,
    *,
    method: MuPrecoder = "rzf",
    noise_power: float = 0.0,
    alpha: float | None = None,
    total_power: float = 1.0,
    power_allocation: PowerAllocation = "equal",
) -> tuple[np.ndarray, np.ndarray]:
    """多用户预编码，返回 ``(方向 W, 功率 p)``。

    ``h_eff_sel`` 是已配对用户的等效信道 ``[K_sel, S, RB, BS]``；
    ``W`` 形状 ``[RB, BS_ant, N_stream]`` 且**每列单位范数**，
    ``p`` 形状 ``[RB, N_stream]`` 且逐 RB 满足 ``Σp = total_power``。

    * ``"zf"``  ``W ∝ H^H (H H^H)^{-1}`` —— 完全消除用户间干扰，代价是噪声放大
    * ``"rzf"`` ``W ∝ H^H (H H^H + αI)^{-1}`` —— α 默认 ``N_stream·σ²/P``，
      低信噪比退化成 MRT、高信噪比趋近 ZF
    * ``"mrt"`` ``W ∝ H^H`` —— 最大比发射，不管用户间干扰

    **方向与功率必须解耦，这是踩过的坑。**
    早先的写法是整个矩阵乘一个全局标量把 ``tr(WW^H)`` 归到总功率。看着合理，
    实际上 ZF 满足 ``H W = c·I``——**所有用户的接收电平被强行拉平**，
    弱用户为了达到同一电平会吃掉大部分发射功率。这就是信道求逆功控，
    公认的劣解，而且它让公平度恒等于 1.000（实测四个用户等效信道范数
    12.0 / 11.7 / 10.7 / 7.2，谱效却一模一样都是 11.482），
    看起来像"MU 天生公平"，其实是功率分配被悄悄写死了。

    现在列各自归一（只表示方向，与 Sionna 的 ``rzf_precoding_matrix`` 一致），
    再由 ``power_allocation`` 显式决定每流分多少：

    * ``"equal"`` 等分 ``P/N_stream``——最常用的基线
    * ``"waterfilling"`` 对 ZF 后的等效增益注水，最大化和速率

    总功率仍然是 ``total_power=1``，与 SU 侧口径一致；
    **不能照搬 Sionna 的 ``tr(GG^H)=K``**，那会让 MU 白拿 K 倍功率。
    """
    hs = np.asarray(h_eff_sel)
    n_k, n_s, n_rb, n_bs = hs.shape
    n_str = n_k * n_s
    if n_str > n_bs:
        raise ValueError(f"流数 {n_str} 超过发射天线数 {n_bs}，ZF/RZF 无解")

    if alpha is None:
        alpha = n_str * float(noise_power) / max(float(total_power), _EPS)

    w_out = np.zeros((n_rb, n_bs, n_str), dtype=np.complex128)
    p_out = np.zeros((n_rb, n_str), dtype=np.float64)
    for f in range(n_rb):
        h_mat = hs[:, :, f, :].reshape(n_str, n_bs)      # [N_stream, BS]
        if method == "mrt":
            w = h_mat.conj().T
        else:
            a = h_mat @ h_mat.conj().T                   # [N_str, N_str]
            reg = 0.0 if method == "zf" else float(alpha)
            w = h_mat.conj().T @ np.linalg.pinv(a + reg * np.eye(n_str))
        # 逐列归一：W 只表示方向
        col = np.linalg.norm(w, axis=0)
        w = w / np.maximum(col, _EPS)
        w_out[f] = w

        if power_allocation == "waterfilling":
            # 等效增益 |h_k w_k|^2；注水到 Σp = total_power
            gain = np.abs(np.einsum("kb,bk->k", h_mat, w)) ** 2
            p_out[f] = _waterfill(gain, float(noise_power), float(total_power))
        else:
            p_out[f] = float(total_power) / n_str
    return w_out, p_out


def _waterfill(gain: np.ndarray, noise_power: float, total_power: float) -> np.ndarray:
    """经典注水：``p_k = max(0, μ - σ²/g_k)``，二分求 μ 使 ``Σp = P``。"""
    g = np.maximum(np.asarray(gain, dtype=float), _EPS)
    inv = noise_power / g
    lo, hi = 0.0, float(total_power) + float(np.max(inv))
    for _ in range(80):
        mu_ = 0.5 * (lo + hi)
        s = float(np.sum(np.maximum(mu_ - inv, 0.0)))
        if s > total_power:
            hi = mu_
        else:
            lo = mu_
    p = np.maximum(0.5 * (lo + hi) - inv, 0.0)
    tot = float(np.sum(p))
    return p * (total_power / tot) if tot > _EPS else np.full_like(p, total_power / len(p))


# ---------------------------------------------------------------------------
# 4 · 逐用户 SINR 与谱效
# ---------------------------------------------------------------------------
@dataclass
class MuPerformance:
    """一次 MU 传输的评估结果。"""

    users: list[int]
    sinr_per_user_db: np.ndarray
    se_per_user: np.ndarray
    sum_se: float
    precoder: str
    pairing: Pairing | None
    noise_power: float
    csi_for_precoding: str
    power_allocation: str
    jain_fairness: float
    leakage_ratio: float          # 用户间残余干扰 / 总接收功率

    def as_dict(self) -> dict[str, Any]:
        return {
            "users": self.users,
            "num_paired": len(self.users),
            "sinr_per_user_db": [round(float(x), 2) for x in self.sinr_per_user_db],
            "se_per_user": [round(float(x), 4) for x in self.se_per_user],
            "sum_se": round(float(self.sum_se), 4),
            "precoder": self.precoder,
            "pairing": self.pairing.as_dict() if self.pairing else None,
            "noise_power": float(self.noise_power),
            "csi_for_precoding": self.csi_for_precoding,
            "power_allocation": self.power_allocation,
            "jain_fairness": round(float(self.jain_fairness), 4),
            "leakage_ratio": round(float(self.leakage_ratio), 6),
        }


def mu_link_performance_from_effective(
    h_eval: np.ndarray,
    h_precode: np.ndarray,
    *,
    noise_power: float,
    precoder: MuPrecoder = "rzf",
    alpha: float | None = None,
    total_power: float = 1.0,
    power_allocation: PowerAllocation = "equal",
    pairing: Pairing | None = None,
    users: list[int] | None = None,
    csi_label: str = "h_true",
) -> MuPerformance:
    """在等效信道上算逐用户 SINR。``h_eval`` 用于评估、``h_precode`` 用于算 W。

    **两者分开传是刻意的。** 传同一个就是理想 CSI（上界），
    传 ``h_est`` 才是真实系统——ZF 的零陷深度完全由 CSI 精度决定，
    这个差距是 MU-MIMO 最重要的一条结论，不能让它被默认值糊过去。
    """
    hv = np.asarray(h_eval)
    hp = np.asarray(h_precode)
    n_k, n_s, n_rb, _ = hv.shape
    n_str = n_k * n_s

    w, pw = mu_precoder(hp, method=precoder, noise_power=noise_power,
                        alpha=alpha, total_power=total_power,
                        power_allocation=power_allocation)

    sinr = np.zeros((n_rb, n_str))
    leak_num = leak_den = 0.0
    for f in range(n_rb):
        h_mat = hv[:, :, f, :].reshape(n_str, -1)        # [N_str, BS]
        g = h_mat @ w[f]                                 # [N_str, N_str] 收到的耦合
        # **功率在这里进来，不在预编码矩阵里。** 第 j 列的功率是 pw[f, j]。
        p = (np.abs(g) ** 2) * pw[f][None, :]
        for k in range(n_str):
            sig = p[k, k]
            mui = float(np.sum(p[k]) - sig)              # 用户间干扰（ZF 下理应≈0）
            sinr[f, k] = sig / max(mui + noise_power, _EPS)
            leak_num += mui
            leak_den += float(np.sum(p[k])) + noise_power

    se_rb = np.log2(1.0 + np.maximum(sinr, 0.0))
    se_str = se_rb.mean(axis=0)                          # [N_str]
    se_user = se_str.reshape(n_k, n_s).sum(axis=1)       # 同一用户的多流相加
    s = float(np.sum(se_user))
    jain = (s ** 2 / (n_k * float(np.sum(se_user ** 2)))) if np.any(se_user > 0) else 0.0

    return MuPerformance(
        users=list(users) if users is not None
        else (list(pairing.users) if pairing else list(range(n_k))),
        sinr_per_user_db=10.0 * np.log10(
            np.maximum(sinr.mean(axis=0).reshape(n_k, n_s).mean(axis=1), _EPS)),
        se_per_user=se_user,
        sum_se=s,
        precoder=precoder,
        pairing=pairing,
        noise_power=float(noise_power),
        csi_for_precoding=csi_label,
        power_allocation=power_allocation,
        jain_fairness=jain,
        leakage_ratio=leak_num / max(leak_den, _EPS),
    )


def mu_link_performance(
    h_users: list[np.ndarray],
    *,
    snr_db: float | None = None,
    noise_power: float | None = None,
    h_users_for_precoding: list[np.ndarray] | None = None,
    streams_per_user: int = 1,
    precoder: MuPrecoder = "rzf",
    criterion: PairingCriterion = "sus",
    max_users: int = 4,
    corr_threshold: float = 0.5,
    weights: np.ndarray | None = None,
    alpha: float | None = None,
    total_power: float = 1.0,
    power_allocation: PowerAllocation = "equal",
) -> MuPerformance:
    """一站式：等效信道 → 配对 → 多用户预编码 → 逐用户 SINR。

    ``h_users`` 是同一小区、同一时刻的一批用户信道，每个
    ``[T, RB, BS_ant, UE_ant]``。``h_users_for_precoding`` 不给就等于理想 CSI。
    """
    if noise_power is None:
        if snr_db is None:
            raise ValueError("snr_db 与 noise_power 至少给一个")
        sig = float(np.mean([np.mean(np.abs(np.asarray(h)) ** 2) for h in h_users]))
        noise_power = sig / max(10.0 ** (snr_db / 10.0), _EPS)

    he_eval = effective_user_channels(h_users, streams_per_user=streams_per_user)
    if h_users_for_precoding is None:
        he_prec, label = he_eval, "h_true"
    else:
        he_prec = effective_user_channels(h_users_for_precoding,
                                          streams_per_user=streams_per_user)
        label = "h_est"

    # **配对用的是预编码侧的 CSI。** 真实基站看不到 h_true，
    # 用它来配对等于偷看，会得到偏乐观的 MU 增益。
    pr = pair_users(he_prec, criterion=criterion, max_users=max_users,
                    corr_threshold=corr_threshold, weights=weights,
                    noise_power=float(noise_power))
    if not pr.users:
        raise ValueError("配对结果为空")

    return mu_link_performance_from_effective(
        he_eval[pr.users], he_prec[pr.users],
        noise_power=float(noise_power), precoder=precoder, alpha=alpha,
        total_power=total_power, power_allocation=power_allocation,
        pairing=pr, users=pr.users, csi_label=label,
    )
