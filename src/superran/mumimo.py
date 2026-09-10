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

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from . import beamforming as bf
from . import carrier as carrier_grid

_EPS = 1e-30

PairingCriterion = Literal["sus", "greedy_sum_rate", "all", "best_single"]
OrthogonalizationMode = Literal["none", "select", "schmidt"]
MuPrecoder = Literal["ezf", "zf", "rzf", "mrt"]
#: 现场口径 MU 求逆的对角加载常数（加在归一化 Gram 矩阵上，1/1024）。
EZF_DIAGONAL_LOADING = 1.0 / 1024.0
PowerAllocation = Literal["equal", "waterfilling"]

# 工程约定（用户 2026-08-02 给的现场口径）
MU_MAX_RANK = 2          # MU 配对时每用户最多 2 流，硬约束
SU_MAX_RANK = 4          # SU 发送可以到 4 流
RB_PER_RBG = 16          # 17 RBG × 16 RB = 272


@dataclass(frozen=True)
class RZFRegularization:
    """Auditable decomposition of the robust RZF diagonal loading.

    For ``H = H_hat + E`` with i.i.d. per-coefficient error variance
    ``sigma_e^2``, ``E[E E^H] = N_bs sigma_e^2 I``.  The robust Gram matrix
    therefore adds that uncertainty loading to the conventional noise loading.
    """

    noise_loading: float
    csi_error_loading: float
    total_loading: float
    n_stream: int
    n_bs: int
    csi_error_variance: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "noise_loading": self.noise_loading,
            "csi_error_loading": self.csi_error_loading,
            "total_loading": self.total_loading,
            "n_stream": self.n_stream,
            "n_bs": self.n_bs,
            "csi_error_variance": self.csi_error_variance,
            "model": "E[E E^H] = N_bs * sigma_e^2 * I",
        }


def robust_rzf_regularization(
    *,
    n_stream: int,
    n_bs: int,
    mean_noise_power: float,
    total_power: float = 1.0,
    csi_error_variance: float = 0.0,
    alpha: float | None = None,
) -> RZFRegularization:
    """Return noise + CSI-uncertainty loading for RZF.

    ``alpha`` overrides only the conventional noise term; the independently
    declared CSI uncertainty is still added.  Setting
    ``csi_error_variance=0`` is exactly backward compatible.
    """
    if int(n_stream) < 1 or int(n_bs) < 1:
        raise ValueError("n_stream and n_bs must be positive")
    vals = (mean_noise_power, total_power, csi_error_variance)
    if any(not np.isfinite(float(v)) for v in vals):
        raise ValueError("RZF powers and CSI error variance must be finite")
    if mean_noise_power < 0 or total_power <= 0 or csi_error_variance < 0:
        raise ValueError("noise/error variance must be non-negative and total_power positive")
    if alpha is not None and (not np.isfinite(float(alpha)) or float(alpha) < 0):
        raise ValueError("alpha must be finite and non-negative")
    noise_loading = (
        float(alpha) if alpha is not None
        else int(n_stream) * float(mean_noise_power) / float(total_power)
    )
    error_loading = int(n_bs) * float(csi_error_variance)
    return RZFRegularization(
        noise_loading=noise_loading,
        csi_error_loading=error_loading,
        total_loading=noise_loading + error_loading,
        n_stream=int(n_stream),
        n_bs=int(n_bs),
        csi_error_variance=float(csi_error_variance),
    )


# ---------------------------------------------------------------------------
# 0 · 单码字谱效：SINR → MCS → rank × 谱效
# ---------------------------------------------------------------------------
def rbg_sinr_db(sinr_lin_per_rb: np.ndarray, *,
                rb_per_rbg: int = RB_PER_RBG,
                rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
                ) -> np.ndarray:
    """把逐 RB/流 SINR 压成逐 RBG SINR（dB）。

    逐 RB/流先转 dB，RBG 内与跨流均取 dB 算术平均，与参考实现对齐；
    返回 ``[RBG]``。
    保留这一级是 RB 功控能正确评估 1-RBG grant 的前提。
    """
    s = np.asarray(sinr_lin_per_rb, dtype=float)
    if s.ndim == 1:
        s = s[:, None]
    if s.ndim != 2 or s.shape[0] < 1 or s.shape[1] < 1:
        raise ValueError(f"sinr 应为非空 [RB,stream]，收到 {s.shape}")
    n_rb = s.shape[0]
    step = max(1, min(int(rb_per_rbg), n_rb))
    bounds = (
        carrier_grid.validate_boundaries(n_rb, rbg_boundaries)
        if rbg_boundaries is not None
        else carrier_grid.uniform_boundaries(n_rb, step)
    )
    n_rbg = len(bounds)
    s_db = 10.0 * np.log10(np.maximum(s, _EPS))
    # 逐 RBG 切片 + mean 在 step=1（输入已是 RBG 粒度）时是纯开销：实测一次
    # 12 UE 建表里光 ndarray.mean 就被调了 10 万次。整除时 reshape 一次算完，
    # 元素与顺序完全一样；除不尽才退回按组切片。
    if rbg_boundaries is None and step == 1:
        rbg_db = s_db
    elif rbg_boundaries is None and n_rb % step == 0:
        rbg_db = s_db.reshape(n_rbg, step, s.shape[1]).mean(axis=1)
    else:
        rbg_db = np.stack([
            s_db[start:stop].mean(axis=0) for start, stop in bounds])
    return np.mean(rbg_db, axis=1)


def user_sinr_db(sinr_lin_per_rb: np.ndarray, *, rb_per_rbg: int = RB_PER_RBG,
                 rbg_boundaries: tuple[tuple[int, int], ...] | None = None) -> float:
    """把 ``[RB, stream]`` 的线性 SINR 压成一个**用户级 SINR**（dB）。

    口径（用户 2026-09-10 核对参考实现）::

        逐 RB SINR → RBG 内聚合 → 各 RBG 的 dB 值算术平均 → 各流的 dB 值算术平均

    **为什么不是逐 RB 算谱效再平均。** 一个用户一个 TTI 只发**一个码字**，
    这个码字用同一个 MCS 覆盖全部 RB 与全部流。所以必须先把 SINR 压成一个数
    再查 MCS，而不是逐 RB 查完再平均——后者等于假设每个 RB 能用不同 MCS，
    会系统性高估。两者的差正是单码字相对多码字的损失。

    本接口按参考实现采用 dB 域平均（即几何平均），比线性平均保守：
    深衰的那几个 RBG 会把整个码字拖下去，线性平均会把它们的影响冲淡。

    RBG **内部**用 dB 域平均，与参考实现对齐；
    RBG **之间**仍对每组的 dB 值等权平均，不按组内 RB 数加权。
    """
    # RBG 与流两个维度都在 dB 域取算术平均（顺序无关）。
    return float(np.mean(rbg_sinr_db(
        sinr_lin_per_rb, rb_per_rbg=rb_per_rbg,
        rbg_boundaries=rbg_boundaries)))


def se_from_sinr(sinr_db: float, rank: int, *, table: int = 3,
                 target_bler: float = 0.1) -> tuple[float, Any]:
    """用户级 SINR + rank → ``(谱效, Mcs)``。谱效 = ``rank × MCS 的谱效``。

    表 3 是内置的 20B NewTx 预置曲线（28 档 MCS）。
    """
    from . import linkadapt as la  # noqa: PLC0415

    mcs = la.select_mcs(float(sinr_db), table=table, target_bler=target_bler)
    return float(rank) * float(mcs.se), mcs


# ---------------------------------------------------------------------------
# 1 · 逐用户等效信道
# ---------------------------------------------------------------------------
def effective_user_channels(
    h_users: list[np.ndarray] | np.ndarray,
    *,
    streams_per_user: int = 1,
) -> np.ndarray:
    """把每个用户的 MIMO 信道压成 ``streams_per_user`` 条等效行向量。

    输入每个用户 ``[1, RB, BS_ant, UE_ant]``（单个调度快照），输出
    ``[K, S, RB, BS_ant]``。

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
    bad_shape = [tuple(h.shape) for h in hs if h.ndim not in (3, 4)]
    if bad_shape:
        raise ValueError(
            f"每用户信道应为 [RB,BS,UE] 或 [1,RB,BS,UE]，收到 {bad_shape}")
    if int(streams_per_user) < 1:
        raise ValueError("streams_per_user 必须至少为 1")
    # **形状必须一致，不一致要当场报错。** 不查的话 numpy 会在赋值时
    # 抛一个看不出所以然的 broadcast 错，或者更糟——形状恰好能广播时
    # 静默给出错误结果。
    _shapes = {tuple(np.asarray(h).shape[-3:]) for h in hs}
    if len(_shapes) > 1:
        raise ValueError(f"各用户的 [RB, BS, UE] 形状必须一致，实得 {sorted(_shapes)}")
    bad_t = [int(h.shape[0]) for h in hs if h.ndim == 4 and h.shape[0] != 1]
    if bad_t:
        raise ValueError(
            "MU 等效信道一次只接受一个调度快照（T=1）；多时隙请逐时隙调用再平均速率，"
            "不能先平均复信道")
    # 同时支持 [RB,BS,UE] 与 [1,RB,BS,UE]。这里必须从尾维取，不能固定
    # 用 shape[1]/shape[2]：3D 输入下那会把 BS 当成 RB、UE 当成 BS。
    n_rb = hs[0].shape[-3]
    n_bs = hs[0].shape[-2]
    s_max = int(streams_per_user)

    out = np.zeros((len(hs), s_max, n_rb, n_bs), dtype=np.complex128)
    for u, h in enumerate(hs):
        hb = h[0] if h.ndim == 4 else h                  # [RB, BS, UE]
        # **逐 RB 的 Python 循环换成堆叠 SVD。** numpy 的 svd 原生吃
        # ``[..., M, N]``，内层循环在 C 里跑；矩阵只有 4×64 这种尺寸时，
        # 原来的开销几乎全是 Python 调度（一次 8 UE 建表实测 45888 次 svd 调用）。
        # 数值上是同一个 LAPACK 例程逐个矩阵地算，结果逐位相同。
        dl = np.conj(np.transpose(hb, (0, 2, 1)))        # [RB, UE, BS] 下行矩阵
        _, sv, vh = np.linalg.svd(dl, full_matrices=False)
        # np.linalg.svd 返回的 ``vh[s]`` 本身就是 v_s^H。旧代码又做一次
        # conj，会在复信道上把发射方向翻成 v_s^T，范数不变却破坏 ZF。
        n_take = min(s_max, vh.shape[1])
        out[u, :n_take] = np.transpose(
            sv[:, :n_take, None] * vh[:, :n_take, :], (1, 0, 2))  # σ_s · v_s^H
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
    dropped_by_mcs: list[int] = field(default_factory=list)
    pairing_mcs: list[int] = field(default_factory=list)
    pf_gain_threshold: float = 0.0
    weighted_rate_ratios: list[float] = field(default_factory=list)
    orthogonalization_mode: str = "select"

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
            "dropped_by_mcs": self.dropped_by_mcs,
            "pairing_mcs": self.pairing_mcs,
            "pf_gain_threshold": float(self.pf_gain_threshold),
            "weighted_rate_ratios": [
                round(float(value), 6) for value in self.weighted_rate_ratios],
            "orthogonalization_mode": self.orthogonalization_mode,
        }


def _wideband_user_vectors(h_eff: np.ndarray) -> np.ndarray:
    """用 ``E_f[h_f^H h_f]`` 给每个用户构造相位不敏感的宽带主方向。

    直接对复信道跨 RB 求均值会让不同子载波的公共相位相消：同一物理功率只因
    每 RB 乘了 ``exp(jφ_f)``，用户强度和 SUS 配对就会改变。这里取第一流的
    发射侧协方差主特征向量，向量范数为主特征值平方根；既保留宽带强度，又只
    平均功率而不平均复幅度。它仍是宽带配对近似，不冒充逐 RB 最优配对。
    """
    he = np.asarray(h_eff)
    if he.ndim != 4:
        raise ValueError(f"h_eff 应为 [K,S,RB,BS]，收到 {he.shape}")
    n_k, n_s, n_rb, n_bs = he.shape
    if n_s < 1 or n_rb < 1 or n_bs < 1:
        raise ValueError(f"h_eff 各维必须非空，收到 {he.shape}")
    out = np.zeros((n_k, n_bs), dtype=np.complex128)
    for k in range(n_k):
        rows = he[k, 0]  # [RB,BS]
        cov = rows.conj().T @ rows / n_rb
        ev, vec = np.linalg.eigh(cov)
        top = int(np.argmax(ev.real))
        # Hermitian PSD 矩阵的主特征向量本身就是主方向；取共轭得到的是
        # C^T 的特征向量，当发射方向用会让 w^H C w ≠ λ_max。
        out[k] = np.sqrt(max(float(ev[top].real), 0.0)) * vec[:, top]
    return out


def pair_users(
    h_eff: np.ndarray,
    *,
    criterion: PairingCriterion = "sus",
    max_users: int = 4,
    corr_threshold: float = 0.5,
    weights: np.ndarray | None = None,
    noise_power: float = 0.0,
    min_pairing_mcs: int = 4,
    pf_gain_threshold: float = 0.0,
    orthogonalization_mode: OrthogonalizationMode = "select",
    mcs_indices: np.ndarray | None = None,
    mcs_table: int = 3,
    target_bler: float = 0.1,
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

    ``min_pairing_mcs`` 在配对前排除低档用户；给 ``mcs_indices`` 时使用调用方
    已有的因果 MCS，否则从 ``h_eff + noise_power`` 的单用户链路估计。
    ``pf_gain_threshold`` 只作用于 ``greedy_sum_rate``，比较加入候选前后的
    加权速率比；0 关闭并精确保持旧行为。``orthogonalization_mode='select'``
    使用当前相关性筛选，``none`` 不筛，``schmidt`` 尚未实现并硬失败。
    """
    he = np.asarray(h_eff)
    if criterion not in ("sus", "greedy_sum_rate", "all", "best_single"):
        raise ValueError(f"未知 MU 配对准则 {criterion!r}")
    if orthogonalization_mode not in ("none", "select", "schmidt"):
        raise ValueError("orthogonalization_mode 只支持 none / select / schmidt")
    if orthogonalization_mode == "schmidt":
        raise NotImplementedError("TODO: Schmidt 正交化未实现")
    if (isinstance(min_pairing_mcs, (bool, np.bool_))
            or not isinstance(min_pairing_mcs, (int, np.integer))
            or int(min_pairing_mcs) < 0):
        raise ValueError("min_pairing_mcs 必须是非负整数")
    if not np.isfinite(pf_gain_threshold) or float(pf_gain_threshold) < 0.0:
        raise ValueError("pf_gain_threshold 必须是有限非负数")
    if not np.isfinite(corr_threshold) or not 0.0 <= float(corr_threshold) <= 1.0:
        raise ValueError("corr_threshold 必须落在 [0,1]")
    if (isinstance(mcs_table, (bool, np.bool_))
            or not isinstance(mcs_table, (int, np.integer))):
        raise ValueError("mcs_table 必须是整数表号")
    n_k, n_s, _n_rb, n_bs = he.shape
    # 宽带配对：对 E_f[h^H h] 取主方向；不能跨 RB 直接平均复信道。
    g = _wideband_user_vectors(he)                        # [K, BS]
    norms = np.linalg.norm(g, axis=1)
    w = np.ones(n_k) if weights is None else np.asarray(weights, dtype=float)
    if w.shape != (n_k,):
        raise ValueError(f"weights 长度必须是 {n_k}，实得 {w.shape}")
    if np.any(~np.isfinite(w)) or np.any(w <= 0.0):
        raise ValueError("weights 必须全部是有限正数")

    if mcs_indices is None:
        if int(min_pairing_mcs) > 0:
            from . import linkadapt as la  # noqa: PLC0415

            inferred = []
            for user in range(n_k):
                perf = mu_link_performance_from_effective(
                    he[[user]], he[[user]], noise_power=float(noise_power),
                    precoder="zf")
                inferred.append(int(la.select_mcs(
                    float(perf.sinr_per_user_db[0]), table=int(mcs_table),
                    target_bler=float(target_bler)).index))
            pairing_mcs = np.asarray(inferred, dtype=int)
        else:
            pairing_mcs = np.full(n_k, -1, dtype=int)
    else:
        raw_mcs = np.asarray(mcs_indices)
        if (raw_mcs.shape != (n_k,)
                or not np.issubdtype(raw_mcs.dtype, np.integer)
                or np.any(raw_mcs < 0)):
            raise ValueError(f"mcs_indices 必须是长度 {n_k} 的非负整数数组")
        pairing_mcs = raw_mcs.astype(int, copy=True)
        from . import linkadapt as la  # noqa: PLC0415

        if int(mcs_table) not in la.MCS_TABLES:
            raise ValueError(f"未知 mcs_table={mcs_table}")
        if np.any(pairing_mcs >= len(la.MCS_TABLES[int(mcs_table)])):
            raise ValueError(
                f"mcs_indices 超出 table {mcs_table} 的 0.."
                f"{len(la.MCS_TABLES[int(mcs_table)]) - 1}")
    eligible = [
        user for user in range(n_k)
        if int(min_pairing_mcs) == 0
        or int(pairing_mcs[user]) >= int(min_pairing_mcs)
    ]
    dropped_by_mcs = sorted(set(range(n_k)) - set(eligible))

    cap = max(1, min(int(max_users), n_bs // max(n_s, 1), n_k))

    if criterion == "best_single":
        users = ([max(eligible, key=lambda user: float(norms[user] * w[user]))]
                 if eligible else [])
        return Pairing(
            users, criterion, cap, corr_threshold,
            weights_used=weights is not None, dropped_by_mcs=dropped_by_mcs,
            pairing_mcs=[int(value) for value in pairing_mcs],
            pf_gain_threshold=float(pf_gain_threshold),
            orthogonalization_mode=orthogonalization_mode)
    if criterion == "all":
        return Pairing(
            eligible, criterion, cap, corr_threshold,
            weights_used=weights is not None, dropped_by_mcs=dropped_by_mcs,
            pairing_mcs=[int(value) for value in pairing_mcs],
            pf_gain_threshold=float(pf_gain_threshold),
            orthogonalization_mode=orthogonalization_mode)
    if criterion == "greedy_sum_rate":
        return _greedy_sum_rate(
            he, cap, noise_power, w, corr_threshold,
            candidates=eligible, dropped_by_mcs=dropped_by_mcs,
            pairing_mcs=pairing_mcs,
            pf_gain_threshold=float(pf_gain_threshold),
            orthogonalization_mode=orthogonalization_mode)

    # --- SUS ---
    sel: list[int] = []
    corrs: list[float] = []
    dropped: list[int] = []
    cand = set(eligible)
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
        if orthogonalization_mode == "select":
            for i in list(cand):
                c = abs(complex(g[i].conj() @ g[best])) / max(
                    norms[i] * norms[best], _EPS)
                if c > corr_threshold:
                    cand.discard(i)
                    dropped.append(i)

    return Pairing(
        sel, "sus", cap, corr_threshold, corrs, sorted(dropped),
        weights_used=weights is not None, dropped_by_mcs=dropped_by_mcs,
        pairing_mcs=[int(value) for value in pairing_mcs],
        pf_gain_threshold=float(pf_gain_threshold),
        orthogonalization_mode=orthogonalization_mode)


def _greedy_sum_rate(
    he: np.ndarray,
    cap: int,
    noise_power: float,
    w: np.ndarray,
    corr_threshold: float,
    *,
    candidates: list[int],
    dropped_by_mcs: list[int],
    pairing_mcs: np.ndarray,
    pf_gain_threshold: float,
    orthogonalization_mode: str,
) -> Pairing:
    """每轮真算 ZF 加权速率；增量或配置的 PF 增益比不过门就停。"""
    sel: list[int] = []
    best_rate = 0.0
    accepted_ratios: list[float] = []
    while len(sel) < cap:
        gain_best, cand_best, rate_best = 0.0, -1, 0.0
        for i in candidates:
            if i in sel:
                continue
            trial = [*sel, i]
            r = float(np.sum(_zf_sum_rate(he[trial], noise_power) * w[trial]))
            if r - best_rate > gain_best:
                gain_best, cand_best, rate_best = r - best_rate, i, r
        if cand_best < 0:
            break
        if sel and pf_gain_threshold > 0.0:
            ratio = rate_best / max(best_rate, _EPS)
            if ratio < pf_gain_threshold + 1e-9:
                break
            accepted_ratios.append(float(ratio))
        sel.append(cand_best)
        best_rate = rate_best
    return Pairing(
        sel, "greedy_sum_rate", cap, corr_threshold,
        weights_used=not np.allclose(w, 1.0),
        dropped_by_mcs=dropped_by_mcs,
        pairing_mcs=[int(value) for value in pairing_mcs],
        pf_gain_threshold=float(pf_gain_threshold),
        weighted_rate_ratios=accepted_ratios,
        orthogonalization_mode=orthogonalization_mode)


def _zf_sum_rate(he_sel: np.ndarray, noise_power: float) -> np.ndarray:
    """给定用户集，ZF 之后的逐用户谱效（宽带平均），用于贪心搜索。"""
    res = mu_link_performance_from_effective(he_sel, he_sel, noise_power=noise_power,
                                             precoder="zf")
    return res.se_per_user


# ---------------------------------------------------------------------------
# 2.5 · 残留相关性：基站在发射侧预测配对代价（现场口径）
# ---------------------------------------------------------------------------
def mu_power_split_db(ranks: Sequence[int]) -> np.ndarray:
    """等分总功率下每个配对用户的功率分摊 ``10log10(rank_u / Σrank)`` dB。

    对齐现场口径「该用户层数 / 配对组总层数」取对数。总功率在**层**
    上等分，所以一个用户拿到的份额只由它自己的层数与配对组总层数决定：
    2 用户 × rank2 两侧都是 -3.0103 dB；rank1+rank2 则是
    ``[-4.7712, -1.7609]`` dB——层多的那个用户分到更多功率。
    """
    values = [int(r) for r in ranks]
    if len(values) < 2 or any(r < 1 for r in values):
        raise ValueError(f"配对层数必须是至少两个正整数，收到 {list(ranks)}")
    total = float(sum(values))
    return np.array([10.0 * np.log10(r / total) for r in values], dtype=float)


SU_WEIGHT_RANK_SCALING = ("none", "sqrt_rank")


def su_weight_correlation_matrix(
    w_su_users: list[np.ndarray], *, rank_scaling: str = "none") -> np.ndarray:
    """各用户 SU 发射权之间的功率相关矩阵 ``|w_k^H w_q|²``。

    ``w_su_users`` 每项形状 ``[F, BS_ant, rank_u]``（列为单位范数的发射方向，
    与 :func:`csi_aging.svd_precoder` 同一约定）；返回
    ``[F, N_stream, N_stream]``，``N_stream = Σ rank_u``，流的排列顺序是
    用户内先 rank、用户间按入参顺序（与 :func:`mu_precoder` 一致）。

    对齐现场口径：把各用户的 SU 发射权拼成一个矩阵，取它的 Gram 矩阵
    再取模方。列范数在这里显式归一：现场实现拿到的 SU 波束权本来就是单位列，
    本仓的 PEBF/NEBF 权是「单位方向 × 显式功率」分解后的物理矩阵，直接取模方
    会把功率差混进相关度。归一后对角线恒为 1，与现场的相关度矩阵同义。

    ``rank_scaling`` 对应现场 ``getSubfWeight`` 里那句 ``mxWeight *= sqrt(rank)``。
    **默认不加，理由是实测而不是偏好**：把它加进互相关之后，rank2 配对下跨用户
    ``rho`` 最大到 2.87、有 13.45% 的元素 ``>= 1``，``(1-rho)`` 变成非正数，
    残留相关性连乘与随后的 dB 转换当场失效（单位列时 rho 最大 0.717，从不越界）。
    所以那个 ``sqrt(rank)`` 携带的是**发射功率**，本仓已经在「单位方向 × 显式
    功率」的分解里单独记账，再乘一次就是重复计入。``"sqrt_rank"`` 保留为可显式
    选择的对照口径，供想复现该分支的人使用。
    """
    if len(w_su_users) < 2:
        raise ValueError("残留相关性至少需要两个用户")
    if str(rank_scaling) not in SU_WEIGHT_RANK_SCALING:
        raise ValueError(
            f"rank_scaling 只支持 {SU_WEIGHT_RANK_SCALING}，收到 {rank_scaling!r}")
    mats = []
    for w in w_su_users:
        a = np.asarray(w)
        if a.ndim != 3:
            raise ValueError(f"SU 发射权应为 [F,BS,rank]，收到 {a.shape}")
        col = np.linalg.norm(a, axis=1, keepdims=True)
        unit = a / np.maximum(col, _EPS)
        if str(rank_scaling) == "sqrt_rank":
            unit = unit * math.sqrt(float(a.shape[2]))
        mats.append(unit)
    shapes = {(m.shape[0], m.shape[1]) for m in mats}
    if len(shapes) > 1:
        raise ValueError(f"各用户的 [F, BS] 必须一致，实得 {sorted(shapes)}")
    w_all = np.concatenate(mats, axis=2)                       # [F, BS, N_str]
    gram = np.conj(np.transpose(w_all, (0, 2, 1))) @ w_all     # [F, N_str, N_str]
    return np.abs(gram) ** 2


def wideband_weight_correlation(
    w_su_users: list[np.ndarray],
    *,
    rb_per_rbg: int = RB_PER_RBG,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
    rank_scaling: str = "none",
) -> tuple[np.ndarray, np.ndarray]:
    """现场取 SU 权相关矩阵的两级平均，返回 ``(逐 RBG [RBG,N,N], 宽带 [N,N])``。

    与 ``GDlSerialScheduler`` 的顺序逐字对齐：

    1. **逐 RB** 取每个 UE 的 SU 发射权、拼成 ``mxSuBf``，算它的 Gram 模方
       （:func:`su_weight_correlation_matrix`，对应 ``cov(mxSuBf, true, mxUhU)``）；
    2. **RBG 内平均**：``mxNormUhU = mxUhU / RB数``；
    3. **宽带平均**：``mxWbUhU = sum(mxNormUhU) / RBG数``。

    **顺序不能反。** 先在 RB 上做非线性（连乘、取 dB）再平均，和先平均再做
    非线性是两个不同的量；本仓的残留相关性连乘按第 1~2 步的结果做，宽带矩阵
    是第 3 步的结果，两者服务于不同的判决（前者定逐 RBG 的配对代价，后者是
    现场用来做宽带配对预筛的那一个）。
    """
    corr_rb = su_weight_correlation_matrix(
        w_su_users, rank_scaling=rank_scaling)                   # [F, N, N]
    n_rb = int(corr_rb.shape[0])
    step = max(1, min(int(rb_per_rbg), n_rb))
    bounds = (
        carrier_grid.validate_boundaries(n_rb, rbg_boundaries)
        if rbg_boundaries is not None
        else carrier_grid.uniform_boundaries(n_rb, step)
    )
    if rbg_boundaries is None and step == 1:
        corr = corr_rb
    elif rbg_boundaries is None and n_rb % step == 0:
        corr = corr_rb.reshape(len(bounds), step, *corr_rb.shape[1:]).mean(axis=1)
    else:
        corr = np.stack([corr_rb[start:stop].mean(axis=0)
                         for start, stop in bounds])             # [RBG, N, N]
    return corr, corr.mean(axis=0)


def wideband_pair_correlation(
    w_su_users: list[np.ndarray],
    *,
    rb_per_rbg: int = RB_PER_RBG,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
) -> float:
    """两个用户之间的**宽带**归一化波束相关系数，取值 [0,1]。

    这是 :func:`wideband_weight_correlation` 第二级输出（现场的 ``mxWbUhU``）
    的跨用户块：把两个用户各自的流之间的 ``|w_k^H w_q|^2`` 取平均，再开方折回
    幅度量纲，好和 ``mu_corr_threshold`` 这类幅度门限直接比较。

    **它就是配对判决要用的那个数。** 现场的顺序是"逐 RB 取互相关 → RBG 内平均
    → 宽带平均 → 决定配不配"，宽带矩阵算出来不是为了看，是为了在这一步用。
    """
    ranks = [int(np.asarray(w).shape[2]) for w in w_su_users]
    if len(ranks) != 2:
        raise ValueError("宽带配对相关系数只定义在两个用户之间")
    _rbg, wideband = wideband_weight_correlation(
        w_su_users, rb_per_rbg=rb_per_rbg, rbg_boundaries=rbg_boundaries)
    block = wideband[:ranks[0], ranks[0]:ranks[0] + ranks[1]]
    return float(np.sqrt(max(float(np.mean(block)), 0.0)))


def residual_correlation_loss_db(
    w_su_users: list[np.ndarray],
    *,
    rb_per_rbg: int = RB_PER_RBG,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
    wideband_coupling: bool = False,
    rank_scaling: str = "none",
) -> np.ndarray:
    """现场的残留相关性连乘法：配对后每个用户的 SINR 损失（dB，≤0）。

    返回 ``[K, RBG]``，逐用户逐 RBG。做法分三步，与
    ``GDlSerialScheduler::calcSpatialPairSe`` / ``calcMuSinrCorrLoss`` 对齐：

    1. **RBG 内先平均相关度。** 现场实现在 RBG 内逐 RB 累加 ``|w_k^H w_q|²``
       再除以 RB 数，得到该 RBG 的一份 CorrMat，之后才连乘。顺序不能反过来
       （先逐 RB 连乘再平均是另一个量）。
    2. **逐流连乘 ``(1−ρ)``。** 第 k 条流对所有**别的用户**的每条流各乘一个
       ``1−ρ``，得到残留相关系数 ``RemCorr_k``；乘出非正数时钳到机器 eps
       （现场实现同样有一个下限兜底），否则 dB 会变 ``-inf``。
    2b. **宽带相关矩阵再连乘一道**（``wideband_coupling``，**默认关**）。
       现场第 4~5 步先把逐 RBG 的归一相关矩阵平均成宽带矩阵 ``mxWbUhU``，
       再"与每 RBG 残差连乘"。这里按那句话的字面实现：每一对流各多乘一个
       ``1 - rho_wideband``。**默认关掉的理由是实测，不是偏好**：

       * 宽带矩阵按定义就是逐 RBG 矩阵在 RBG 上的平均，再乘一次等于把同一份
         相关性算两遍。实测配对代价直接翻倍（rank1 −0.561 → −1.122 dB，
         rank2 −1.133 → −2.263 dB）；
       * 打开后本仓自己的 MU 夹具里**配对率掉到 0**（平均服务用户数 1.0000），
         也就是 MU 在任何场景下都不再划算——这不是一个"更精确"的模型该有的
         行为，更像重复计入。

       宽带矩阵**已经在判决链里**：它的跨用户块就是 ``mu_corr_threshold`` 比较
       的那个配对相关系数（见 :func:`wideband_pair_correlation`）。
       **现场参考实现的源码在本机不可得**，所以"连乘"这一步到底乘的是哪两个量
       无法对着代码核实；开关留在这里，拿到源码后要改的就是这一个默认值。

    3. **折成 dB 再压成单码字。** ``10log10(RemCorr_k)`` 是该流的损失；
       本仓一个用户一个 TTI 只发一个码字，所以用**逐流 dB 算术平均**压成一个
       用户级数值——与 :func:`rbg_sinr_db` / :func:`user_sinr_db` 的单码字口径
       完全一致。现场实现的对应片段写的是逐层求和；两者在 rank2 上差整整一倍，
       口径归属尚未与维护者确认，本仓先按自己的单码字约定实现。

    **它替代的是什么。** 这是基站在**发射侧**用两个用户各自的 SU 波束方向
    解析算出来的配对代价，不需要知道终端用什么接收机。它不是接收端联合检测
    的结果——那条路（LMMSE）留给真实解码 SINR。
    """
    if len(w_su_users) < 2:
        raise ValueError("残留相关性至少需要两个用户")
    ranks = [int(np.asarray(w).shape[2]) for w in w_su_users]
    corr, wideband = wideband_weight_correlation(
        w_su_users, rb_per_rbg=rb_per_rbg, rbg_boundaries=rbg_boundaries,
        rank_scaling=rank_scaling)
    n_rbg = int(corr.shape[0])
    # **rho 必须是 [0,1] 里的相关度。** 超过 1 时 (1-rho) 变负，而每条流要对
    # 别的用户的每条流各乘一个因子——偶数个负因子相乘会翻成正数，于是这个
    # 按定义只能是**损失**的量冒出了增益（实测 +7.12 dB，评审那侧 +9.54 dB）。
    # 旧写法只对连乘结果做下限钳位，挡得住 -inf，挡不住"负负得正"。
    # rho>1 只可能来自输入本身不是相关度（例如把携带功率的 sqrt(rank) 权直接
    # 送进来），那是口径错误，必须硬失败而不是钳一下继续跑。
    peak = float(np.max(corr)) if corr.size else 0.0
    if peak > 1.0 + 1e-9:
        raise ValueError(
            f"SU 权互相关出现 rho={peak:.4f} > 1，它不是相关度："
            "(1-rho) 会变负，偶数个负因子相乘还会翻成正的增益。"
            "最常见的原因是 rank_scaling='sqrt_rank'——现场那个 sqrt(rank) "
            "携带的是发射功率，本仓已在「单位方向 x 显式功率」里单独记账，"
            "不能再送进相关度。请用 rank_scaling='none'。")
    start = [0]
    for r in ranks:
        start.append(start[-1] + r)
    out = np.zeros((len(ranks), n_rbg), dtype=float)
    for i, rank_i in enumerate(ranks):
        stream_db = np.zeros((rank_i, n_rbg), dtype=float)
        for k in range(rank_i):
            rem = np.ones(n_rbg, dtype=float)
            for j, rank_j in enumerate(ranks):
                if j == i:
                    continue
                for q in range(rank_j):
                    rem *= (1.0 - corr[:, start[i] + k, start[j] + q])
                    if wideband_coupling:
                        # 现场的第 4~5 步：宽带相关矩阵与每 RBG 的残差**连乘**。
                        # 物理含义是"这一对用户在整band 上就有多相关"给每个 RBG
                        # 再压一道——逐 RBG 残差只看本地正交性，宽带那一项把
                        # 全带的耦合也算进配对代价。
                        rem *= (1.0 - wideband[start[i] + k, start[j] + q])
            # 残留相关性是损失：结果必须落在 (0,1]，dB 必须 <= 0。
            rem = np.clip(rem, _EPS, 1.0)
            stream_db[k] = 10.0 * np.log10(rem)
        out[i] = np.mean(stream_db, axis=0)
    return out


# ---------------------------------------------------------------------------
# 3 · 多用户预编码
# ---------------------------------------------------------------------------
def mu_precoder(
    h_eff_sel: np.ndarray | Sequence[np.ndarray],
    *,
    method: MuPrecoder = "rzf",
    noise_power: float | np.ndarray = 0.0,
    alpha: float | None = None,
    csi_error_variance: float = 0.0,
    total_power: float = 1.0,
    power_allocation: PowerAllocation = "equal",
    power_constraint: bf.PowerConstraint | str = "ebf",
) -> tuple[np.ndarray, np.ndarray]:
    """多用户预编码，返回 ``(方向 W, 功率 p)``。

    ``h_eff_sel`` 是已配对用户的等效信道。等流数时给 ``[K_sel, S, RB, BS]``；
    **各用户流数不同时给一个列表**，每项 ``[S_u, RB, BS]``（异 rank 配对，
    例如一个 UE 单层、另一个双层）。两种写法在等流数下逐位相同。
    ``W`` 形状 ``[RB, BS_ant, N_stream]`` 且**每列单位范数**，
    ``p`` 形状 ``[RB, N_stream]`` 且逐 RB 满足 ``Σp = total_power``。
    流的排列顺序恒为「用户内先流、用户间按入参顺序」。

    * ``"ezf"`` 现场口径的 MU 求逆：先把 H 的每条流除以自己的范数，
      再在**归一化后**的 Gram 矩阵上加 ``1/1024`` 对角加载求逆。列归一化
      本身会被后面的「列各自归一」吸收掉（与 ZF 的方向逐位相同），真正起
      作用的是那个 ε：它让加载量与各流信道强度**无关**，强弱用户混配时
      不会出现「弱流被加载淹掉、强流几乎没加载」的失衡。ε=0 时 EZF 退化
      成 ZF。
    * ``"zf"``  ``W ∝ H^H (H H^H)^{-1}`` —— 完全消除用户间干扰，代价是噪声放大
    * ``"rzf"`` ``W ∝ H^H (H H^H + αI)^{-1}`` —— α 的噪声项默认
      ``N_stream·σ²/P``；声明每系数 CSI 误差方差 ``sigma_e²`` 时，再加
      ``N_BS·sigma_e²``。低信噪比/高不确定性趋向 MRT，CSI 准确时趋近 ZF
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
    if method not in ("ezf", "zf", "rzf", "mrt"):
        raise ValueError(f"未知 MU 预编码 {method!r}")
    if power_allocation not in ("equal", "waterfilling"):
        raise ValueError(f"未知 MU 功率分配 {power_allocation!r}")
    if not np.isfinite(total_power) or float(total_power) <= 0:
        raise ValueError("total_power 必须是有限正数")
    if isinstance(h_eff_sel, (list, tuple)):
        # 异 rank：逐用户 [S_u, RB, BS]。等 rank 时与四维写法逐位相同，
        # 因为堆叠顺序仍是「用户内先流」。
        per_user = [np.asarray(x) for x in h_eff_sel]
        if not per_user or any(x.ndim != 3 for x in per_user):
            raise ValueError("逐用户 h_eff_sel 每项应为 [S_u, RB, BS]")
        if len({x.shape[1:] for x in per_user}) != 1:
            raise ValueError("各用户的 [RB, BS] 必须一致")
        stream_counts = [int(x.shape[0]) for x in per_user]
        n_k = len(per_user)
        n_rb, n_bs = int(per_user[0].shape[1]), int(per_user[0].shape[2])
        hs = None
    else:
        hs = np.asarray(h_eff_sel)
        if hs.ndim != 4:
            raise ValueError(f"h_eff_sel 应为 [K,S,RB,BS]，收到 {hs.shape}")
        n_k, n_s, n_rb, n_bs = hs.shape
        stream_counts = [int(n_s)] * int(n_k)
    n_str = int(sum(stream_counts))
    if any(s < 1 for s in stream_counts):
        raise ValueError(f"每个用户至少要有一条流，收到 {stream_counts}")
    if n_str > n_bs:
        raise ValueError(f"流数 {n_str} 超过发射天线数 {n_bs}，ZF/RZF 无解")

    noise_in = np.asarray(noise_power, dtype=float)
    if noise_in.ndim == 0:
        noise_stream = np.full(n_str, float(noise_in))
    elif noise_in.shape == (n_k,):
        noise_stream = np.repeat(noise_in, stream_counts)
    elif noise_in.shape == (n_str,):
        noise_stream = noise_in.copy()
    else:
        raise ValueError(
            f"noise_power 应为标量、逐用户 ({n_k},) 或逐流 ({n_str},)，收到 {noise_in.shape}")
    if np.any(~np.isfinite(noise_stream)) or np.any(noise_stream < 0):
        raise ValueError("noise_power 必须是有限非负数")
    mean_noise = float(np.mean(noise_stream))
    reg_info = robust_rzf_regularization(
        n_stream=n_str,
        n_bs=n_bs,
        mean_noise_power=mean_noise,
        total_power=float(total_power),
        csi_error_variance=csi_error_variance,
        alpha=alpha,
    )

    # **整个频域一次算完。** ``hs`` 是 [K,S,RB,BS]，把 (K,S) 压成流维、
    # RB 提到最前，就能直接喂 numpy 的堆叠 ``pinv``——它对 ``[..., M, N]``
    # 原生成批处理。逐 RB 的 Python 循环在 17 个 RBG × 上千次调用下开销
    # 全在调度上（实测一次 8 UE 建表 22848 次 pinv）。流的排列顺序保持
    # ``u * S + s``，与原来的 ``reshape(n_str, n_bs)`` 逐位一致。
    if hs is None:
        h_all = np.concatenate(
            [np.transpose(x, (1, 0, 2)) for x in per_user], axis=1)
    else:
        h_all = np.transpose(hs, (2, 0, 1, 3)).reshape(n_rb, n_str, n_bs)
    h_all_h = np.conj(np.transpose(h_all, (0, 2, 1)))    # [RB, BS, N_str]
    if method == "mrt":
        w_all = h_all_h
    elif method == "ezf":
        # 现场口径的 MU 求逆：第 1 步取 Gram 对角（每条流的信道能量），
        # Step 2 逐流归一化，Step 3 在归一化后的 Gram 上加 1/1024，
        # Step 4 求逆。归一化后 Gram 对角恒为 1，所以 ε 对每条流等权。
        gram = h_all @ h_all_h                           # [RB, N_str, N_str]
        energy = np.real(np.diagonal(gram, axis1=1, axis2=2))   # [RB, N_str]
        h_norm = h_all / np.sqrt(np.maximum(energy, _EPS))[:, :, None]
        h_norm_h = np.conj(np.transpose(h_norm, (0, 2, 1)))     # [RB, BS, N_str]
        a = h_norm @ h_norm_h
        w_all = h_norm_h @ np.linalg.pinv(
            a + EZF_DIAGONAL_LOADING * np.eye(n_str))
    else:
        a = h_all @ h_all_h                              # [RB, N_str, N_str]
        reg = 0.0 if method == "zf" else reg_info.total_loading
        w_all = h_all_h @ np.linalg.pinv(a + reg * np.eye(n_str))
    # 逐列归一：W 只表示方向
    col = np.linalg.norm(w_all, axis=1)                  # [RB, N_str]
    w_out = np.ascontiguousarray(w_all / np.maximum(col, _EPS)[:, None, :])
    p_out = np.zeros((n_rb, n_str), dtype=np.float64)
    if power_allocation == "waterfilling":
        if method == "mrt":
            raise ValueError(
                "mrt + waterfilling 是错配组合：注水目标函数只取 |diag(HW)|²"
                "（并行无干扰信道），而 MRT 的主要损伤正是用户间干扰；"
                "请改用 zf/rzf 或 equal 功率分配")
        # 等效增益 |h_k w_k|^2；注水到 Σp = total_power。注水本身是逐 RB 的
        # 二分求解，保持原样——它不是热点，也不该为了形式统一而改数值。
        gains = np.abs(np.einsum("fkb,fbk->fk", h_all, w_out)) ** 2
        for f in range(n_rb):
            p_out[f] = _waterfill(gains[f], noise_stream, float(total_power))
    else:
        p_out[:] = float(total_power) / n_str

    # EBF 保留历史的“单位方向 + 显式逐流功率”表示。PEBF/NEBF 先在物理矩阵
    # Q=W diag(sqrt(p)) 上施加每天线约束，再唯一分解回同一 API：列范数平方是
    # 新的 p，单位列是新的 W。这样所有旧 SINR 公式仍计算同一个物理 Q。
    if str(power_constraint).lower() != "ebf":
        q, _ = bf.allocated_power_weights(
            w_out, p_out, mode=power_constraint, total_power=total_power)
        col_power = np.sum(np.abs(q) ** 2, axis=1).real
        w_new = np.zeros_like(q)
        nz = col_power > _EPS
        for f in range(n_rb):
            # 先切出二维视图再做布尔列索引，避免 NumPy 高级索引把列维移到最前面。
            w_new[f][:, nz[f]] = (
                q[f][:, nz[f]] / np.sqrt(col_power[f, nz[f]])[None, :])
        w_out, p_out = w_new, col_power
    return w_out, p_out


def _waterfill(gain: np.ndarray, noise_power: float | np.ndarray,
               total_power: float) -> np.ndarray:
    """经典注水：``p_k = max(0, μ - σ²/g_k)``，二分求 μ 使 ``Σp = P``。"""
    g = np.maximum(np.asarray(gain, dtype=float), _EPS)
    n = np.asarray(noise_power, dtype=float)
    if n.ndim == 0:
        n = np.full_like(g, float(n))
    if n.shape != g.shape:
        raise ValueError(f"注水噪声应为标量或 {g.shape}，收到 {n.shape}")
    inv = n / g
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
    """一次 MU 传输的评估结果。

    ``se_per_user`` / ``sum_se`` 是逐 RB 香农均值的容量族口径
    （``E_f[log2(1+γ_f)]``），不是逐 RB 查 MCS 的单码字口径；跨家族比较
    （如与 ``su_mu_adaptation`` 的 MCS 口径）时注意不要混用。
    """

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
    noise_power_per_user: np.ndarray | None = None
    sinr_per_user_rbg_db: np.ndarray | None = None  # [user,RBG]
    power_constraint: str = "ebf"
    power_diagnostics: dict[str, Any] | None = None
    receiver: str = "scalar_effective"
    csi_error_variance: float = 0.0
    rzf_regularization: dict[str, Any] | None = None

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
            "noise_power_per_user": (
                None if self.noise_power_per_user is None
                else [float(x) for x in self.noise_power_per_user]),
            "sinr_per_user_rbg_db": (
                None if self.sinr_per_user_rbg_db is None
                else [[float(v) for v in row]
                      for row in np.asarray(self.sinr_per_user_rbg_db)]),
            "power_constraint": self.power_constraint,
            "power_diagnostics": self.power_diagnostics,
            "receiver": self.receiver,
            "csi_error_variance": self.csi_error_variance,
            "rzf_regularization": self.rzf_regularization,
        }


def mu_link_performance_from_effective(
    h_eval: np.ndarray,
    h_precode: np.ndarray,
    *,
    noise_power: float | np.ndarray,
    precoder: MuPrecoder = "rzf",
    alpha: float | None = None,
    csi_error_variance: float = 0.0,
    total_power: float = 1.0,
    power_allocation: PowerAllocation = "equal",
    pairing: Pairing | None = None,
    users: list[int] | None = None,
    csi_label: str = "h_true",
    power_constraint: bf.PowerConstraint | str = "ebf",
    rb_per_rbg: int = RB_PER_RBG,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
) -> MuPerformance:
    """在等效信道上算逐用户 SINR。``h_eval`` 用于评估、``h_precode`` 用于算 W。

    **两者分开传是刻意的。** 传同一个就是理想 CSI（上界），
    传 ``h_est`` 才是真实系统——ZF 的零陷深度完全由 CSI 精度决定，
    这个差距是 MU-MIMO 最重要的一条结论，不能让它被默认值糊过去。
    """
    hv = np.asarray(h_eval)
    hp = np.asarray(h_precode)
    if hv.ndim != 4 or hp.shape != hv.shape:
        raise ValueError(
            f"h_eval/h_precode 必须同为 [K,S,RB,BS] 且形状一致，收到 {hv.shape}/{hp.shape}")
    n_k, n_s, n_rb, _ = hv.shape
    n_str = n_k * n_s

    noise_in = np.asarray(noise_power, dtype=float)
    if noise_in.ndim == 0:
        noise_user = np.full(n_k, float(noise_in))
    elif noise_in.shape == (n_k,):
        noise_user = noise_in.copy()
    else:
        raise ValueError(f"noise_power 应为标量或逐用户 ({n_k},)，收到 {noise_in.shape}")
    if np.any(~np.isfinite(noise_user)) or np.any(noise_user < 0):
        raise ValueError("noise_power 必须是有限非负数")
    noise_stream = np.repeat(noise_user, n_s)

    w, pw = mu_precoder(hp, method=precoder, noise_power=noise_user,
                        alpha=alpha, csi_error_variance=csi_error_variance,
                        total_power=total_power,
                        power_allocation=power_allocation,
                        power_constraint=power_constraint)
    # ``mu_precoder`` 对 PEBF/NEBF 已经把归一后的 Q 唯一分解回 W/p；这里只
    # 重建同一个物理矩阵做诊断，不能再施加第二次每天线缩放。
    q = w * np.sqrt(pw)[:, None, :]
    pdiag = bf.physical_matrix_diagnostics(
        q, mode=power_constraint, total_power=total_power)

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
            sinr[f, k] = sig / max(mui + noise_stream[k], _EPS)
            leak_num += mui
            leak_den += float(np.sum(p[k])) + noise_stream[k]

    se_rb = np.log2(1.0 + np.maximum(sinr, 0.0))
    se_str = se_rb.mean(axis=0)                          # [N_str]
    se_user = se_str.reshape(n_k, n_s).sum(axis=1)       # 同一用户的多流相加
    s = float(np.sum(se_user))
    jain = (s ** 2 / (n_k * float(np.sum(se_user ** 2)))) if np.any(se_user > 0) else 0.0

    sinr_user_rbg_db = np.stack([
        rbg_sinr_db(
            sinr[:, u * n_s:(u + 1) * n_s], rb_per_rbg=rb_per_rbg,
            rbg_boundaries=rbg_boundaries)
        for u in range(n_k)
    ])
    sinr_user_db = np.mean(sinr_user_rbg_db, axis=1)
    reg_diag = (
        robust_rzf_regularization(
            n_stream=n_str, n_bs=hv.shape[-1],
            mean_noise_power=float(np.mean(noise_user)), total_power=total_power,
            csi_error_variance=csi_error_variance, alpha=alpha,
        ).as_dict()
        if precoder == "rzf" else None
    )
    return MuPerformance(
        users=list(users) if users is not None
        else (list(pairing.users) if pairing else list(range(n_k))),
        sinr_per_user_db=sinr_user_db,
        se_per_user=se_user,
        sum_se=s,
        precoder=precoder,
        pairing=pairing,
        noise_power=float(np.mean(noise_user)),
        csi_for_precoding=csi_label,
        power_allocation=power_allocation,
        jain_fairness=jain,
        leakage_ratio=leak_num / max(leak_den, _EPS),
        noise_power_per_user=noise_user,
        sinr_per_user_rbg_db=sinr_user_rbg_db,
        power_constraint=str(power_constraint).lower(),
        power_diagnostics=pdiag.as_dict(),
        receiver="scalar_effective",
        csi_error_variance=float(csi_error_variance),
        rzf_regularization=reg_diag,
    )


def mu_link_performance_lmmse(
    h_eval_users: list[np.ndarray],
    h_precode_users: list[np.ndarray],
    *,
    noise_power: float | np.ndarray,
    streams_per_user: int | Sequence[int] = MU_MAX_RANK,
    precoder: MuPrecoder = "zf",
    alpha: float | None = None,
    csi_error_variance: float = 0.0,
    total_power: float = 1.0,
    power_allocation: PowerAllocation = "equal",
    pairing: Pairing | None = None,
    users: list[int] | None = None,
    csi_label: str = "h_est",
    power_constraint: bf.PowerConstraint | str = "ebf",
    rb_per_rbg: int = RB_PER_RBG,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
) -> MuPerformance:
    """保留每个 UE 的接收天线，以逐用户 LMMSE 检测计算 MU SINR。

    预编码仍由 ``h_precode_users`` 的前 ``streams_per_user`` 个单用户空间模
    构造 ZF/RZF 权；但评估时不把每条流预先压成一根固定接收行。对用户 ``u``，
    它的本用户多流联合检测，其他用户的流进入干扰协方差::

        R_u = sigma_u^2 I + G_interf G_interf^H
        E_u = (I + G_desired^H R_u^-1 G_desired)^-1
        SINR_{u,k} = 1 / E_u[k,k] - 1

    这点对有 CSI 误差/老化的 rank>1 尤其关键：真实与估计的 SVD 接收基会
    旋转，本用户另一条可联合解调的数据流不能被误记成 MU 残留干扰。

    ``streams_per_user`` 给标量表示所有用户同流数；**给序列表示逐用户流数**
    （异 rank 配对，例如一个 UE 单层、另一个双层）。等流数时两种写法逐位相同。
    """
    if not h_eval_users or len(h_eval_users) != len(h_precode_users):
        raise ValueError("评估/预编码信道必须包含相同的非零用户数")
    if isinstance(streams_per_user, (int, np.integer)) and not isinstance(
            streams_per_user, (bool, np.bool_)):
        ranks = [int(streams_per_user)] * len(h_eval_users)
    else:
        ranks = [int(value) for value in streams_per_user]
        if len(ranks) != len(h_eval_users):
            raise ValueError(
                f"逐用户 streams_per_user 长度必须等于用户数 {len(h_eval_users)}")
    if any(r < 1 for r in ranks):
        raise ValueError("streams_per_user 必须至少为 1")
    rank = max(ranks)

    def _snapshot(h: np.ndarray, label: str) -> np.ndarray:
        x = np.asarray(h)
        if x.ndim == 4:
            if x.shape[0] != 1:
                raise ValueError(f"{label} 一次只接受一个调度快照（T=1）")
            x = x[0]
        if x.ndim != 3:
            raise ValueError(f"{label} 应为 [RB,BS,UE] 或 [1,RB,BS,UE]，收到 {x.shape}")
        return x

    hv = [_snapshot(h, "h_eval") for h in h_eval_users]
    hp = [_snapshot(h, "h_precode") for h in h_precode_users]
    for u, (he_u, hp_u) in enumerate(zip(hv, hp, strict=True)):
        if he_u.shape != hp_u.shape:
            raise ValueError(
                f"UE {u} 的评估/预编码信道形状不一致：{he_u.shape} vs {hp_u.shape}")
        if he_u.shape[2] < ranks[u]:
            raise ValueError(
                f"UE {u} 只有 {he_u.shape[2]} 根接收天线，无法检测 rank{ranks[u]}")
    common = {tuple(x.shape[:2]) for x in hv}
    if len(common) != 1:
        raise ValueError("各用户的 RB 数与基站天线数必须一致")

    n_k = len(hv)
    n_rb, n_bs = hv[0].shape[:2]
    n_str = int(sum(ranks))
    stream_start = [0]
    for r in ranks:
        stream_start.append(stream_start[-1] + r)
    if n_str > n_bs:
        raise ValueError(f"流数 {n_str} 超过发射天线数 {n_bs}")
    noise_in = np.asarray(noise_power, dtype=float)
    if noise_in.ndim == 0:
        noise_user = np.full(n_k, float(noise_in))
    elif noise_in.shape == (n_k,):
        noise_user = noise_in.copy()
    else:
        raise ValueError(f"noise_power 应为标量或逐用户 ({n_k},)，收到 {noise_in.shape}")
    if np.any(~np.isfinite(noise_user)) or np.any(noise_user < 0):
        raise ValueError("noise_power 必须是有限非负数")

    # 逐用户各取自己的流数：等 rank 时这与一次性 effective_user_channels
    # 逐位相同（同一批 SVD、同一个堆叠顺序），异 rank 时才走参差路径。
    he_prec = [
        effective_user_channels([hp[u][None]], streams_per_user=ranks[u])[0]
        for u in range(n_k)]
    w, pw = mu_precoder(
        he_prec, method=precoder, noise_power=noise_user, alpha=alpha,
        csi_error_variance=csi_error_variance,
        total_power=total_power, power_allocation=power_allocation,
        power_constraint=power_constraint)
    q = w * np.sqrt(pw)[:, None, :]
    pdiag = bf.physical_matrix_diagnostics(
        q, mode=power_constraint, total_power=total_power)

    sinr_users: list[np.ndarray] = [
        np.zeros((n_rb, r), dtype=float) for r in ranks]
    leak_num = 0.0
    leak_den = 0.0
    # 每个用户的接收链在频域上是同一套矩阵运算，只是矩阵不同——整段按 RB 堆叠
    # 交给 numpy，Python 只留用户这一层循环。原来 [RB×UE] 双层循环里每格一次
    # ``pinv``，在 17 RBG / 2 用户下就是 34 次小矩阵求逆，全是调度开销。
    for u in range(n_k):
        h_dl = np.conj(np.transpose(hv[u], (0, 2, 1)))   # [RB, UE_ant, BS_ant]
        g = h_dl @ q                                      # [RB, UE_ant, all streams]
        own = np.arange(stream_start[u], stream_start[u + 1])
        other = np.concatenate((np.arange(0, stream_start[u]),
                                np.arange(stream_start[u + 1], n_str)))
        gd = g[:, :, own]
        gi = g[:, :, other]
        gd_h = np.conj(np.transpose(gd, (0, 2, 1)))
        rn = (float(noise_user[u])
              * np.eye(h_dl.shape[1], dtype=complex)[None, :, :])
        if other.size:
            rn = rn + gi @ np.conj(np.transpose(gi, (0, 2, 1)))
        # 并行 LMMSE 滤波器；由它的输出耦合矩阵同时计算逐流 SINR 和
        # **检测后**他用户残留。不能用接收天线口的原始 Gi 能量冒充残留，
        # 否则理想 CSI/ZF 中落在可抑制正交维的能量也会被算成干扰。
        filt = np.linalg.pinv(rn + gd @ gd_h) @ gd        # [RB, UE_ant, own]
        coupling = np.conj(np.transpose(filt, (0, 2, 1))) @ g  # [RB, own, all]
        post_noise = float(noise_user[u]) * np.sum(
            np.abs(filt) ** 2, axis=1)                    # [RB, own]
        post_power = np.abs(coupling) ** 2                # [RB, own, all]
        signal = post_power[:, np.arange(ranks[u]), own]  # [RB, own]
        interference = np.sum(post_power, axis=2) - signal
        sinr_users[u] = signal / np.maximum(interference + post_noise, _EPS)
        if other.size:
            leak_num += float(np.sum(post_power[:, :, other]))
        leak_den += float(np.sum(post_power) + np.sum(post_noise))

    se_user = np.array([
        float(np.sum(np.log2(1.0 + np.maximum(x, 0.0)).mean(axis=0)))
        for x in sinr_users], dtype=float)
    total_se = float(np.sum(se_user))
    jain = (total_se ** 2 / (n_k * float(np.sum(se_user ** 2)))) \
        if np.any(se_user > 0) else 0.0
    sinr_user_rbg_db = np.stack([
        rbg_sinr_db(
            sinr_users[u], rb_per_rbg=rb_per_rbg,
            rbg_boundaries=rbg_boundaries)
        for u in range(n_k)])
    sinr_user_db = np.mean(sinr_user_rbg_db, axis=1)
    # **报告的加载必须与 mu_precoder 实际用的逐位相同。**
    # 它内部按**逐流**平均噪声算（``np.repeat(noise_user, ranks)``），这里
    # 原来按**逐用户**平均重算了一遍：每个用户流数相同时两者恰好相等，所以
    # 一直没暴露；异 rank 时流多的用户权重更大，报告值就对不上实际发射权，
    # 拿它去重构 W 会得到另一个矩阵。口径统一到逐流。
    reg_diag = (
        robust_rzf_regularization(
            n_stream=n_str, n_bs=n_bs,
            mean_noise_power=float(np.mean(np.repeat(noise_user, ranks))),
            total_power=total_power,
            csi_error_variance=csi_error_variance, alpha=alpha,
        ).as_dict()
        if precoder == "rzf" else None
    )
    return MuPerformance(
        users=(list(users) if users is not None else list(range(n_k))),
        sinr_per_user_db=sinr_user_db,
        se_per_user=se_user,
        sum_se=total_se,
        precoder=precoder,
        pairing=pairing,
        noise_power=float(np.mean(noise_user)),
        csi_for_precoding=csi_label,
        power_allocation=power_allocation,
        jain_fairness=jain,
        leakage_ratio=leak_num / max(leak_den, _EPS),
        noise_power_per_user=noise_user,
        sinr_per_user_rbg_db=sinr_user_rbg_db,
        power_constraint=str(power_constraint).lower(),
        power_diagnostics=pdiag.as_dict(),
        receiver="per_user_lmmse",
        csi_error_variance=float(csi_error_variance),
        rzf_regularization=reg_diag,
    )


def noise_from_geometric_sinr(h: np.ndarray, sinr_db: float, *,
                              total_power: float = 1.0) -> float:
    """由预波束几何 ``sinr_dB`` 反推等效总损伤 ``I+N``。

    ChannelHub 标量以单系数功率 ``E[|h|²]·P`` 为信号参考；固定子阵与阵元
    方向图增益已进入大尺度链路预算，数字多端口预编码增益仍保留在 H 中。即::

        I+N = E[|h|²] · P / 10^(sinr_dB/10)

    旧实现用 ``σ₁²`` 作参考，把数字波束增益预先吸收到噪声里；那只在旧版
    ``sinr_dB`` 已是后波束口径时成立。当前 first-party 源明确输出预波束口径，
    继续用旧锚点会把真实数字波束增益抵消掉。
    """
    # 单一真源放在 linklevel，SU 与系统仿真必须逐位使用同一个预波束锚点。
    # 局部 import 避免模块初始化时形成依赖环。
    from .linklevel import prebeam_reference_power

    signal = prebeam_reference_power(h, total_power=total_power)
    return signal / max(10.0 ** (float(sinr_db) / 10.0), _EPS)


@dataclass
class RankChoice:
    """一次 rank 自适应的结果与它的全部候选。"""

    rank: int
    sinr_db: float
    mcs: int
    se: float
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"rank": self.rank, "sinr_db": round(self.sinr_db, 2),
                "mcs": self.mcs, "se": round(self.se, 4),
                "candidates": self.candidates}


def rbg_reduce(h: np.ndarray, rb_per_rbg: int = RB_PER_RBG, *,
               rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
               ) -> np.ndarray:
    """把 ``[RB, BS, UE]`` 的信道按 RBG 聚合，返回 ``[RBG, BS, UE]``。

    **降到 RBG 粒度是安全的，因为 RB 级的分辨率没有任何算法在用。**
    一个 RBG 内的 16 个 RB 共用同一个 MCS、同一次调度决策、同一个预编码；
    仿真到 RB 只是多算 16 倍的 SVD。272 RB → 17 RBG 省 16 倍。

    会受影响的只有**频选调度**与**导频图案**，两者都还没做——
    真要做时把 ``rb_per_rbg`` 设成 1 就退回 RB 粒度。

    聚合方式是 RBG 内**取中间那个 RB**，不是平均：平均会把频选衰落
    抹平、人为抬高信道的条件数（奇异值分布变平），进而高估 rank。
    取代表点保留了真实的空间结构。

    **不足名义 P 的首尾组仍是有效 RBG。** 边界显式给出时严格按边界取代表点；
    旧的固定步长调用也会保留尾组，不再让频域资源凭空消失。
    """
    hh = np.asarray(h)
    n_rb = hh.shape[0]
    step = max(1, int(rb_per_rbg))
    if rbg_boundaries is None and step <= 1:
        return hh
    bounds = (
        carrier_grid.validate_boundaries(n_rb, rbg_boundaries)
        if rbg_boundaries is not None
        else carrier_grid.uniform_boundaries(n_rb, step)
    )
    idx = np.asarray(
        [(start + stop - 1) // 2 for start, stop in bounds], dtype=int
    )
    return hh[idx]


def su_rank_adaptation(h: np.ndarray, *, noise_power: float,
                       max_rank: int = SU_MAX_RANK, table: int = 3,
                       target_bler: float = 0.1,
                       total_power: float = 1.0,
                       rb_per_rbg: int = 1,
                       rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
                       power_constraint: bf.PowerConstraint | str = "ebf") -> RankChoice:
    """单用户 rank 自适应：遍历 rank 1..max_rank，取谱效最高的那个。

    **这是个真实的权衡，不是"rank 越高越好"。** 总功率固定，rank 个流均分，
    所以：

    * rank 1 —— 全部功率压在最强流上，BF 增益最大、SINR 最高、MCS 最高，
      但 ``SE = 1 × MCS谱效``，只有一条流，吃亏在流数上。
    * rank 4 —— 每流只有 P/4，弱流的 SINR 很低，把用户级 SINR（dB 域平均）
      拖下去，MCS 掉档，但乘的是 4。

    最优点通常在中间。用户给的现网锚点是**平均 rank 2.7**，
    可以用 :func:`calibration_summary` 对一下。

    SINR 口径：SVD 预编码后逐流 ``|σ_k|²·(P/rank)/σ_n²``，
    再按 :func:`user_sinr_db` 压成一个数（单码字）。
    """
    hh = np.asarray(h)
    if hh.ndim == 3:
        hh = hh[None]
    if hh.ndim != 4:
        raise ValueError(f"h 应为 [T,RB,BS,UE] 或 [RB,BS,UE]，收到 {hh.shape}")
    # 时间是独立性能样本，不能先平均复信道。把每个时隙的频域行串起来，
    # 等价于在共同 rank/MCS 下对全部时频资源做单码字聚合。
    rows = [
        rbg_reduce(x, rb_per_rbg, rbg_boundaries=rbg_boundaries)
        if rb_per_rbg > 1 else x
        for x in hh
    ]
    hb = np.concatenate(rows, axis=0)
    n_rb = hb.shape[0]
    # Boundaries are defined inside one snapshot.  When several snapshots are
    # concatenated for a common rank/MCS decision, repeat them with offsets;
    # otherwise a partial tail from snapshot t could be grouped together with
    # the first RBs of snapshot t+1.
    aggregation_boundaries: tuple[tuple[int, int], ...] | None = None
    if rb_per_rbg <= 1:
        per_snapshot = (
            carrier_grid.validate_boundaries(hh.shape[1], rbg_boundaries)
            if rbg_boundaries is not None
            else carrier_grid.uniform_boundaries(hh.shape[1], RB_PER_RBG)
        )
        aggregation_boundaries = tuple(
            (t * hh.shape[1] + start, t * hh.shape[1] + stop)
            for t in range(hh.shape[0])
            for start, stop in per_snapshot
        )
    r_max = max(1, min(int(max_rank), hb.shape[1], hb.shape[2]))

    # 逐 RB 的 SVD 一次算好给所有 rank 复用。EBF 仍走奇异值闭式以守住历史
    # 基线；PEBF/NEBF 必须把归一后的物理矩阵真正打回信道，因为 NEBF 会破坏
    # 流间正交性，不能只给奇异值乘一个功率系数。
    svd = [np.linalg.svd(hb[f].conj().T, full_matrices=False) for f in range(n_rb)]
    sv = np.stack([x[1] for x in svd])
    w_full = np.stack([x[2].conj().T for x in svd])

    best: RankChoice | None = None
    cands: list[dict[str, Any]] = []
    for r in range(1, r_max + 1):
        if str(power_constraint).lower() == "ebf":
            p_per = float(total_power) / r
            sinr = (sv[:, :r] ** 2) * p_per / max(float(noise_power), _EPS)
        else:
            q, _model, _diag = bf.equal_power_weights(
                w_full[:, :, :r], mode=power_constraint, total_power=total_power)
            sinr = np.zeros((n_rb, r), dtype=float)
            for f in range(n_rb):
                g = hb[f].conj().T @ q[f]
                gram = g.conj().T @ g / max(float(noise_power), _EPS)
                inv = np.linalg.pinv(np.eye(r) + gram)
                sinr[f] = np.maximum(
                    1.0 / np.maximum(np.real(np.diag(inv)), _EPS) - 1.0, 0.0)
        # 已经降过粒度的话每行就是一个 RBG，不能再按 16 分组
        s_db = user_sinr_db(
            sinr,
            rb_per_rbg=1 if rb_per_rbg > 1 else RB_PER_RBG,
            rbg_boundaries=aggregation_boundaries,
        )
        se, mcs = se_from_sinr(s_db, r, table=table, target_bler=target_bler)
        cands.append({"rank": r, "sinr_db": round(s_db, 2), "mcs": mcs.index,
                      "se": round(se, 4)})
        if best is None or se > best.se:
            best = RankChoice(r, s_db, mcs.index, se)
    assert best is not None
    best.candidates = cands
    return best


@dataclass
class CellDecision:
    """SU / MU 自适应的判决：这一个 TTI 到底怎么发。"""

    mode: str                      # "SU" 或 "MU"
    cell_se: float                 # 小区谱效（一个 TTI 内的和谱效）
    su_se: float
    mu_se: float
    su_user: int
    su_rank: int
    su_mcs: int
    mu_users: list[int]
    mu_per_user: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "cell_se": round(self.cell_se, 4),
            "su_se": round(self.su_se, 4), "mu_se": round(self.mu_se, 4),
            "gain_of_mu": round(self.mu_se - self.su_se, 4),
            "su": {"user": self.su_user, "rank": self.su_rank, "mcs": self.su_mcs},
            "mu": {"users": self.mu_users, "per_user": self.mu_per_user},
            "note": self.note,
        }


def su_mu_adaptation(
    h_users: list[np.ndarray],
    *,
    noise_power: float | np.ndarray,
    h_users_for_precoding: list[np.ndarray] | None = None,
    mu_rank: int = MU_MAX_RANK,
    su_max_rank: int = SU_MAX_RANK,
    max_mu_users: int = 4,
    precoder: MuPrecoder = "zf",
    csi_error_variance: float = 0.0,
    table: int = 3,
    target_bler: float = 0.1,
    power_constraint: bf.PowerConstraint | str = "ebf",
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
    min_pairing_mcs: int = 4,
    pf_gain_threshold: float = 0.0,
    pf_weights: np.ndarray | None = None,
    corr_threshold: float = 0.5,
    orthogonalization_mode: OrthogonalizationMode = "none",
) -> CellDecision:
    """SU / MU 自适应：同一个 TTI 里，两种发法哪个小区谱效高就用哪个。

    **判据是小区谱效，不是用户间正交度。** 这是用户 2026-08-02 定的口径——
    现场没有明确的相关性门限，实际做法就是把两种方案都算一遍取高的：

    * **SU** —— 一个 TTI 只服务一个用户，**没有 MU 干扰**，rank 可以到 4。
      取所有用户里 rank 自适应后谱效最高的那个。
    * **MU** —— 配对多个用户同时发，每用户 rank 固定 2（工程约束），
      功率按流均分（rank2 的用户拿 2 份），代价是 CSI 误差导致的残余干扰。

    SU 之所以能赢，是因为它没干扰且能开到 rank 4；MU 之所以能赢，
    是因为流数多。两者不是一个总能压另一个。

    配对候选用贪心粗选（用户说"简单粗估即可，不用特别精细"）：
    按等效信道范数排序，逐个加入直到 ``max_mu_users`` 或加了反而更差。

    ``min_pairing_mcs`` 使用每个用户在 ``mu_rank`` 下、基站可见 CSI 算出的 MCS
    做 MU 前置筛选；被排除的用户仍可参加 SU。``pf_gain_threshold`` 比较加入候选
    前后的加权谱效比，0 表示关闭；只有传入历史速率倒数 ``pf_weights`` 时它才是
    PF（Proportional Fair，比例公平）口径，否则只是和谱效增益门。
    本低层函数的 ``orthogonalization_mode`` 默认 ``none`` 以保持原贪心路径；
    系统级配置会显式传入 ``select``，与相关性准入门使用同一选择。
    """
    n_k = len(h_users)
    if (isinstance(min_pairing_mcs, (bool, np.bool_))
            or not isinstance(min_pairing_mcs, (int, np.integer))
            or int(min_pairing_mcs) < 0):
        raise ValueError("min_pairing_mcs 必须是非负整数")
    if not np.isfinite(pf_gain_threshold) or float(pf_gain_threshold) < 0.0:
        raise ValueError("pf_gain_threshold 必须是有限非负数")
    if orthogonalization_mode not in ("none", "select", "schmidt"):
        raise ValueError("orthogonalization_mode 只支持 none / select / schmidt")
    if orthogonalization_mode == "schmidt":
        raise NotImplementedError("TODO: Schmidt 正交化未实现")
    if not np.isfinite(corr_threshold) or not 0.0 <= float(corr_threshold) <= 1.0:
        raise ValueError("corr_threshold 必须落在 [0,1]")
    pf_w = (np.ones(n_k, dtype=float) if pf_weights is None
            else np.asarray(pf_weights, dtype=float))
    if pf_w.shape != (n_k,) or np.any(~np.isfinite(pf_w)) or np.any(pf_w <= 0.0):
        raise ValueError(f"pf_weights 必须是长度 {n_k} 的有限正数数组")
    if h_users_for_precoding is None:
        hp = h_users
    else:
        if len(h_users_for_precoding) != n_k:
            raise ValueError(
                "h_users_for_precoding 与 h_users 的用户数必须一致")
        hp = h_users_for_precoding
        for u, (he_u, hp_u) in enumerate(zip(h_users, hp, strict=True)):
            if np.asarray(he_u).shape != np.asarray(hp_u).shape:
                raise ValueError(
                    f"UE {u} 的评估/预编码信道形状不一致："
                    f"{np.asarray(he_u).shape} vs {np.asarray(hp_u).shape}")
    noise_user = np.asarray(noise_power, dtype=float)
    if noise_user.ndim == 0:
        noise_user = np.full(n_k, float(noise_user))
    if noise_user.shape != (n_k,):
        raise ValueError(
            f"noise_power 应为标量或逐用户 ({n_k},)，收到 {noise_user.shape}")
    if np.any(~np.isfinite(noise_user)) or np.any(noise_user < 0):
        raise ValueError("noise_power 必须是有限非负数")

    # --- SU 候选：逐用户 rank 自适应，取最好的那个 ---
    su_best, su_user = None, 0
    su_choices: list[RankChoice] = []
    for u in range(n_k):
        if h_users_for_precoding is None:
            rc = su_rank_adaptation(
                h_users[u], noise_power=float(noise_user[u]),
                max_rank=su_max_rank, table=table,
                target_bler=target_bler,
                rbg_boundaries=rbg_boundaries,
                power_constraint=power_constraint)
        else:
            # SU 与 MU 必须拥有完全相同的 CSI 信息集：都只用 h_est 选权/rank，
            # 再把选中的权打到 h_true 上评估。旧代码只把 h_est 给 MU，SU 却用
            # h_true 做 SVD，导致所谓 SU/MU 自适应比较的两臂不是同一实验。
            from . import csi_aging as ca  # noqa: PLC0415

            he_u = np.asarray(h_users[u])
            hp_u = np.asarray(hp[u])
            if he_u.ndim == 4:
                if he_u.shape[0] != 1:
                    raise ValueError(
                        "su_mu_adaptation 一次只接受一个调度快照（T=1）")
                he_u, hp_u = he_u[0], hp_u[0]
            if he_u.ndim != 3 or hp_u.ndim != 3:
                raise ValueError(
                    "每用户信道应为 [RB,BS,UE] 或 [1,RB,BS,UE]")
            aged = ca.rank_adaptation_aged(
                hp_u, he_u, noise_power=float(noise_user[u]),
                max_rank=su_max_rank, table=table,
                target_bler=target_bler, rb_per_rbg=RB_PER_RBG,
                rbg_boundaries=rbg_boundaries,
                power_constraint=power_constraint)
            rc = RankChoice(
                aged.rank, aged.sinr_db, aged.mcs, aged.se,
                candidates=aged.candidates)
        su_choices.append(rc)
        if su_best is None or rc.se > su_best.se:
            su_best, su_user = rc, u
    assert su_best is not None

    # --- MU 候选：贪心加人，每人 rank=mu_rank ---
    he_all = effective_user_channels(hp, streams_per_user=mu_rank)
    pairing_mcs: list[int] = []
    for choice in su_choices:
        row = next(
            (candidate for candidate in choice.candidates
             if int(candidate["rank"]) == int(mu_rank)),
            None,
        )
        pairing_mcs.append(int(row["mcs"]) if row is not None else int(choice.mcs))
    excluded = [
        user for user, mcs in enumerate(pairing_mcs)
        if int(mcs) < int(min_pairing_mcs)
    ]
    excluded_set = set(excluded)
    eligible = [user for user in range(n_k) if user not in excluded_set]
    wideband_vectors = _wideband_user_vectors(he_all)
    wideband_norms = np.linalg.norm(wideband_vectors, axis=1)
    strength_order = list(np.argsort(-wideband_norms))
    eligible_set = set(eligible)
    order = [int(user) for user in strength_order if int(user) in eligible_set]
    cap = max(1, min(int(max_mu_users), n_k, he_all.shape[-1] // max(mu_rank, 1)))

    sel: list[int] = []
    mu_best_se, mu_best_detail = 0.0, []
    mu_best_weighted = 0.0
    pf_rejected: list[tuple[int, float]] = []
    corr_rejected: list[tuple[int, float]] = []
    for cand in order:
        if len(sel) >= cap:
            break
        if orthogonalization_mode == "select" and sel:
            correlation = max(
                abs(complex(wideband_vectors[cand].conj() @ wideband_vectors[user]))
                / max(float(wideband_norms[cand] * wideband_norms[user]), _EPS)
                for user in sel)
            if correlation > float(corr_threshold):
                corr_rejected.append((int(cand), float(correlation)))
                continue
        trial = [*sel, int(cand)]
        se, detail = _mu_cell_se(h_users, hp, trial, noise_power=noise_user,
                                 rank=mu_rank, precoder=precoder, table=table,
                                 target_bler=target_bler,
                                 power_constraint=power_constraint,
                                 csi_error_variance=csi_error_variance,
                                 rbg_boundaries=rbg_boundaries)
        if se <= mu_best_se and sel:          # 加了反而更差就不加
            continue
        weighted = float(sum(
            float(row["se"]) * float(pf_w[int(row["user"])])
            for row in detail))
        if sel and float(pf_gain_threshold) > 0.0:
            ratio = weighted / max(mu_best_weighted, _EPS)
            if ratio < float(pf_gain_threshold) + 1e-9:
                pf_rejected.append((int(cand), float(ratio)))
                continue
        sel, mu_best_se, mu_best_detail = trial, se, detail
        mu_best_weighted = weighted

    # 用户口径是 SU > MU 才走 SU；有真实两用户方案时平局归 MU。只有一个
    # 用户的 rank2 候选不是 MU，不能借平局被错误标成 MU。
    mode = "MU" if len(sel) >= 2 and mu_best_se >= su_best.se else "SU"
    note_parts = [
        f"MU 配 {len(sel)} 个用户每人 rank{mu_rank}，"
        f"SU 单用户 rank{su_best.rank}；"
        f"{'MU' if mode == 'MU' else 'SU'} 高 "
        f"{abs(mu_best_se - su_best.se):.3f} bit/s/Hz"
    ]
    note_parts.extend(
        f"UE{user} excluded: mcs={pairing_mcs[user]} < "
        f"min_pairing_mcs={int(min_pairing_mcs)}"
        for user in excluded)
    note_parts.extend(
        f"UE{user} excluded: weighted-rate ratio={ratio:.4f} < "
        f"pf_gain_threshold={float(pf_gain_threshold):g}"
        for user, ratio in pf_rejected)
    note_parts.extend(
        f"UE{user} excluded: correlation={correlation:.4f} > "
        f"corr_threshold={float(corr_threshold):g}"
        for user, correlation in corr_rejected)
    return CellDecision(
        mode=mode,
        cell_se=max(mu_best_se, su_best.se),
        su_se=su_best.se, mu_se=mu_best_se,
        su_user=su_user, su_rank=su_best.rank, su_mcs=su_best.mcs,
        mu_users=sel, mu_per_user=mu_best_detail,
        note="；".join(note_parts),
    )


def _mu_cell_se(h_eval: list[np.ndarray], h_prec: list[np.ndarray], users: list[int],
                *, noise_power: float | np.ndarray, rank: int, precoder: MuPrecoder,
                table: int, target_bler: float,
                power_constraint: bf.PowerConstraint | str = "ebf",
                csi_error_variance: float = 0.0,
                rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
                ) -> tuple[float, list[dict[str, Any]]]:
    """给定配对集合，算这一个 TTI 的小区谱效（单码字口径）。"""
    n_k = len(users)
    n_bs = np.asarray(h_eval[users[0]]).shape[-2]
    if n_k * rank > n_bs:
        return 0.0, []

    noise_all = np.asarray(noise_power, dtype=float)
    if noise_all.ndim == 0:
        noise_selected = np.full(n_k, float(noise_all))
    elif noise_all.shape == (len(h_eval),):
        noise_selected = noise_all[np.asarray(users, dtype=int)]
    elif noise_all.shape == (n_k,):
        noise_selected = noise_all
    else:
        raise ValueError(
            "noise_power 必须是标量、全候选逐用户数组，或当前配对逐用户数组")
    if np.any(~np.isfinite(noise_selected)) or np.any(noise_selected < 0):
        raise ValueError("noise_power 必须是有限非负数")

    perf = mu_link_performance_lmmse(
        [h_eval[u] for u in users], [h_prec[u] for u in users],
        noise_power=noise_selected, streams_per_user=rank, precoder=precoder,
        csi_error_variance=csi_error_variance,
        total_power=1.0, power_allocation="equal",
        users=[int(u) for u in users], csi_label="h_est",
        power_constraint=power_constraint,
        rbg_boundaries=rbg_boundaries)
    total, detail = 0.0, []
    for i, u in enumerate(users):
        s_db = float(perf.sinr_per_user_db[i])
        se, mcs = se_from_sinr(s_db, rank, table=table, target_bler=target_bler)
        total += se
        detail.append({"user": int(u), "rank": rank, "sinr_db": round(s_db, 2),
                       "mcs": mcs.index, "se": round(se, 4)})
    return total, detail


def mu_link_performance(
    h_users: list[np.ndarray],
    *,
    snr_db: float | None = None,
    noise_power: float | np.ndarray | None = None,
    h_users_for_precoding: list[np.ndarray] | None = None,
    streams_per_user: int = 1,
    precoder: MuPrecoder = "rzf",
    criterion: PairingCriterion = "sus",
    max_users: int = 4,
    corr_threshold: float = 0.5,
    weights: np.ndarray | None = None,
    min_pairing_mcs: int = 4,
    pf_gain_threshold: float = 0.0,
    orthogonalization_mode: OrthogonalizationMode = "select",
    mcs_indices: np.ndarray | None = None,
    alpha: float | None = None,
    csi_error_variance: float = 0.0,
    total_power: float = 1.0,
    power_allocation: PowerAllocation = "equal",
    power_constraint: bf.PowerConstraint | str = "ebf",
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
    noise_all = np.asarray(noise_power, dtype=float)
    if noise_all.ndim == 0:
        noise_all = np.full(len(h_users), float(noise_all))
    if noise_all.shape != (len(h_users),):
        raise ValueError(
            f"noise_power 应为标量或逐用户 ({len(h_users)},)，收到 {noise_all.shape}")
    pr = pair_users(he_prec, criterion=criterion, max_users=max_users,
                     corr_threshold=corr_threshold, weights=weights,
                     noise_power=float(np.mean(noise_all)),
                     min_pairing_mcs=min_pairing_mcs,
                     pf_gain_threshold=pf_gain_threshold,
                     orthogonalization_mode=orthogonalization_mode,
                     mcs_indices=mcs_indices)
    if not pr.users:
        raise ValueError("配对结果为空")

    precode_source = h_users if h_users_for_precoding is None else h_users_for_precoding
    return mu_link_performance_lmmse(
        [h_users[u] for u in pr.users],
        [precode_source[u] for u in pr.users],
        noise_power=noise_all[pr.users], streams_per_user=streams_per_user,
        precoder=precoder, alpha=alpha,
        csi_error_variance=csi_error_variance,
        total_power=total_power, power_allocation=power_allocation,
        pairing=pr, users=pr.users, csi_label=label,
        power_constraint=power_constraint,
    )
