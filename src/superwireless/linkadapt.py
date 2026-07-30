"""链路自适应与链路到系统映射 —— 从 SINR 到真实吞吐。

## 为什么需要这一层

`linklevel.py` 给的是 `SE = Σ_layer log2(1 + SINR)`，也就是**香农谱效**。
它是个上界，任何真实系统都达不到，原因有三：

1. **调制受限**。20 dB 时香农说 6.66 bit/s/Hz，但 64QAM 最多只能给 5.80——
   星座点数摆在那里。这一项这里**精确算**（约束容量，见 `qam_mi`）。
2. **码率离散**。MCS 只有 29 档，实际码率总是落在需要的码率之下。
3. **有限码长 + 实现损失**。LDPC 距容量约 1~2 dB，且短包更差。

业界做**系统级**仿真从不跑完整 PHY 链（LDPC 编解码、软解调），而是用
**链路到系统映射**：把逐 RE 的 SINR 矢量压成一个有效 SINR，再查 BLER。
3GPP 的评估方法学、ns-3、Vienna 模拟器走的都是这条路。这里实现的就是它。

## 哪些是算出来的，哪些是模型

**必须分清，否则会把模型当测量用：**

| 部分 | 性质 | 依据 |
|---|---|---|
| QAM 约束容量 `qam_mi` | **精确计算** | Gauss-Hermite 求积，可对香农上界自检 |
| 有效 SINR（MIESM/EESM） | **标准口径** | 互信息平均 / 指数平均 |
| MCS / CQI 表 | **标准查表** | 38.214 Table 5.1.3.1-1/-2、5.2.2.1-2/-3/-4 |
| TBS | **标准算法** | 38.214 §5.1.3.2，逐步复刻 |
| BLER | **模型** | 有限码长形状 + 可配的实现损失。**不是实测曲线** |

BLER 那一行是唯一的模型项。它没有 3GPP 参考曲线兜底，所以：
参数全部可配、默认值有出处、`anchor_check()` 会报出各 MCS 的 10% BLER 门限
供人工对照公开的 NR 链路级曲线。**别把它当成实测 BLER 用。**
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

_EPS = 1e-30


# ---------------------------------------------------------------------------
# 一、QAM 约束容量（精确计算）
# ---------------------------------------------------------------------------


def _pam_mi(order: int, snr_lin: np.ndarray, n_gh: int = 80) -> np.ndarray:
    """L-PAM 的对称信息率（bit / 实维），Gauss-Hermite 求积。

    约定：复符号 ``E|x|^2 = 1``、复噪声 ``E|n|^2 = 1/γ``。折到实维并把星座
    归一化到单位能量后，噪声方差正好是 ``1/γ`` —— 所以 ``sigma = 1/sqrt(γ)``。
    **写成 ``1/sqrt(2γ)`` 会整体多给 3 dB**，实现时踩过这个坑。
    """
    x = np.arange(order) * 2.0 - (order - 1)
    x = x / np.sqrt(np.mean(x**2))
    nodes, w = np.polynomial.hermite_e.hermegauss(n_gh)
    w = w / w.sum()
    g = np.atleast_1d(np.asarray(snr_lin, dtype=float))
    out = np.empty(g.size)
    for i, gam in enumerate(g):
        s = 1.0 / math.sqrt(max(gam, 1e-12))
        y = x[:, None] + s * nodes[None, :]
        num = (y[:, :, None] - x[None, None, :]) ** 2
        ref = (y - x[:, None]) ** 2
        inner = np.log2(np.sum(np.exp(-(num - ref[:, :, None]) / (2 * s * s)), axis=2))
        out[i] = math.log2(order) - float(np.sum(w[None, :] * inner) / order)
    return out


# MI 表的信噪比栅格。−30~45 dB 覆盖了所有实际工作点，0.25 dB 步长够插值。
_MI_GRID_DB = np.arange(-30.0, 45.01, 0.25)


@lru_cache(maxsize=8)
def _mi_table(m_order: int) -> np.ndarray:
    """方形 M-QAM 在 ``_MI_GRID_DB`` 上的互信息表（bit/复符号）。按需算一次并缓存。"""
    return 2.0 * _pam_mi(int(round(math.sqrt(m_order))), 10.0 ** (_MI_GRID_DB / 10.0))


def qam_mi(m_order: int, snr_db: Any) -> np.ndarray:
    """方形 M-QAM 的对称信息率（bit/复符号）。

    这是**调制受限下的容量**，恒 ≤ 香农 ``log2(1+γ)``，高信噪比处饱和到
    ``log2(M)``，低信噪比处与香农重合。三条性质都在测试里核过。

    M ∈ {4, 16, 64, 256, 1024}（QPSK / 16QAM / 64QAM / 256QAM / 1024QAM）。
    """
    if m_order not in (4, 16, 64, 256, 1024):
        raise ValueError(f"只支持方形 QAM（4/16/64/256/1024），收到 {m_order}")
    s = np.atleast_1d(np.asarray(snr_db, dtype=float))
    return np.interp(s, _MI_GRID_DB, _mi_table(m_order))


def qam_mi_inverse(m_order: int, mi: Any) -> np.ndarray:
    """互信息 → 信噪比（dB）。MIESM 反解用。"""
    tbl = _mi_table(m_order)
    v = np.clip(np.atleast_1d(np.asarray(mi, dtype=float)), tbl[0], tbl[-1] - 1e-9)
    return np.interp(v, tbl, _MI_GRID_DB)


# ---------------------------------------------------------------------------
# 二、有效 SINR（链路到系统映射）
# ---------------------------------------------------------------------------

# EESM 的 β 与 MCS 相关，必须逐 MCS 标定。这里给的是文献里按调制阶数分组的
# 常用值，**是近似**——精确用法要拿自己的链路级曲线标定后覆盖。
# 默认走 MIESM 就是为了绕开这个标定负担。
_EESM_BETA = {4: 1.57, 16: 4.56, 64: 14.35, 256: 45.0, 1024: 140.0}


def effective_sinr(
    sinr_db: Any,
    *,
    method: str = "miesm",
    m_order: int = 64,
    beta: float | None = None,
) -> float:
    """把逐 RE / 逐 RB 的 SINR 矢量压成一个有效 SINR（dB）。

    为什么不能直接取平均：BLER 由整个码块决定，而码块横跨所有 RE。
    线性平均会高估（好 RE 补不了坏 RE），dB 平均会低估。有效 SINR 的定义是
    "在 AWGN 下给出同样 BLER 的那个信噪比"。

    ``miesm``（默认）—— 互信息平均，也叫 RBIR：逐 RE 求互信息、平均、再反解。
    **不需要逐 MCS 标定 β**，且公认比 EESM 准。

    ``eesm`` —— 指数平均 ``-β·ln(mean(exp(-γ/β)))``。很多论文用它，但 β 要
    逐 MCS 标定；这里的默认值按调制阶数分组，是近似，用前请自行标定。
    """
    g = np.asarray(sinr_db, dtype=float).ravel()
    g = g[np.isfinite(g)]
    if g.size == 0:
        return float("nan")

    if method == "miesm":
        mi = qam_mi(m_order, g)
        return float(qam_mi_inverse(m_order, float(np.mean(mi)))[0])
    if method == "eesm":
        b = float(beta if beta is not None else _EESM_BETA.get(m_order, 14.35))
        lin = 10.0 ** (g / 10.0)
        val = -b * math.log(max(float(np.mean(np.exp(-lin / b))), 1e-300))
        return float(10.0 * math.log10(max(val, 1e-30)))
    raise ValueError(f"method 应为 miesm 或 eesm，收到 {method!r}")


# ---------------------------------------------------------------------------
# 三、38.214 的 MCS 与 CQI 表（逐字录入）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mcs:
    index: int
    q_m: int          # 调制阶数（2=QPSK, 4=16QAM, 6=64QAM, 8=256QAM）
    r_1024: float     # 目标码率 × 1024
    se: float         # 频谱效率 = q_m · r_1024 / 1024

    @property
    def rate(self) -> float:
        return self.r_1024 / 1024.0

    @property
    def m_order(self) -> int:
        return 1 << self.q_m

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "modulation": _MOD_NAME[self.q_m],
            "q_m": self.q_m, "code_rate": round(self.rate, 4), "se": self.se,
        }


_MOD_NAME = {2: "QPSK", 4: "16QAM", 6: "64QAM", 8: "256QAM", 10: "1024QAM"}

# 38.214 Table 5.1.3.1-1：MCS index table 1 for PDSCH（最高 64QAM）
MCS_TABLE_1: tuple[Mcs, ...] = tuple(
    Mcs(i, q, r, se) for i, q, r, se in [
        (0, 2, 120, 0.2344), (1, 2, 157, 0.3066), (2, 2, 193, 0.3770),
        (3, 2, 251, 0.4902), (4, 2, 308, 0.6016), (5, 2, 379, 0.7402),
        (6, 2, 449, 0.8770), (7, 2, 526, 1.0273), (8, 2, 602, 1.1758),
        (9, 2, 679, 1.3262), (10, 4, 340, 1.3281), (11, 4, 378, 1.4766),
        (12, 4, 434, 1.6953), (13, 4, 490, 1.9141), (14, 4, 553, 2.1602),
        (15, 4, 616, 2.4063), (16, 4, 658, 2.5703), (17, 6, 438, 2.5664),
        (18, 6, 466, 2.7305), (19, 6, 517, 3.0293), (20, 6, 567, 3.3223),
        (21, 6, 616, 3.6094), (22, 6, 666, 3.9023), (23, 6, 719, 4.2129),
        (24, 6, 772, 4.5234), (25, 6, 822, 4.8164), (26, 6, 873, 5.1152),
        (27, 6, 910, 5.3320), (28, 6, 948, 5.5547),
    ]
)

# 38.214 Table 5.1.3.1-2：MCS index table 2 for PDSCH（含 256QAM）
MCS_TABLE_2: tuple[Mcs, ...] = tuple(
    Mcs(i, q, r, se) for i, q, r, se in [
        (0, 2, 120, 0.2344), (1, 2, 193, 0.3770), (2, 2, 308, 0.6016),
        (3, 2, 449, 0.8770), (4, 2, 602, 1.1758), (5, 4, 378, 1.4766),
        (6, 4, 434, 1.6953), (7, 4, 490, 1.9141), (8, 4, 553, 2.1602),
        (9, 4, 616, 2.4063), (10, 4, 658, 2.5703), (11, 6, 466, 2.7305),
        (12, 6, 517, 3.0293), (13, 6, 567, 3.3223), (14, 6, 616, 3.6094),
        (15, 6, 666, 3.9023), (16, 6, 719, 4.2129), (17, 6, 772, 4.5234),
        (18, 6, 822, 4.8164), (19, 6, 873, 5.1152), (20, 8, 682.5, 5.3320),
        (21, 8, 711, 5.5547), (22, 8, 754, 5.8906), (23, 8, 797, 6.2266),
        (24, 8, 841, 6.5703), (25, 8, 885, 6.9141), (26, 8, 916.5, 7.1602),
        (27, 8, 948, 7.4063),
    ]
)

MCS_TABLES = {1: MCS_TABLE_1, 2: MCS_TABLE_2}


@dataclass(frozen=True)
class Cqi:
    index: int
    q_m: int
    r_1024: float
    se: float

    @property
    def m_order(self) -> int:
        return 1 << self.q_m


# 38.214 Table 5.2.2.1-2：4-bit CQI Table（最高 64QAM）。索引 0 = out of range
CQI_TABLE_1: tuple[Cqi, ...] = tuple(
    Cqi(i, q, r, se) for i, q, r, se in [
        (1, 2, 78, 0.1523), (2, 2, 120, 0.2344), (3, 2, 193, 0.3770),
        (4, 2, 308, 0.6016), (5, 2, 449, 0.8770), (6, 2, 602, 1.1758),
        (7, 4, 378, 1.4766), (8, 4, 490, 1.9141), (9, 4, 616, 2.4063),
        (10, 6, 466, 2.7305), (11, 6, 567, 3.3223), (12, 6, 666, 3.9023),
        (13, 6, 772, 4.5234), (14, 6, 873, 5.1152), (15, 6, 948, 5.5547),
    ]
)

# 38.214 Table 5.2.2.1-3：4-bit CQI Table 2（含 256QAM）
CQI_TABLE_2: tuple[Cqi, ...] = tuple(
    Cqi(i, q, r, se) for i, q, r, se in [
        (1, 2, 78, 0.1523), (2, 2, 193, 0.3770), (3, 2, 449, 0.8770),
        (4, 4, 378, 1.4766), (5, 4, 490, 1.9141), (6, 4, 616, 2.4063),
        (7, 6, 466, 2.7305), (8, 6, 567, 3.3223), (9, 6, 666, 3.9023),
        (10, 6, 772, 4.5234), (11, 6, 873, 5.1152), (12, 8, 711, 5.5547),
        (13, 8, 797, 6.2266), (14, 8, 885, 6.9141), (15, 8, 948, 7.4063),
    ]
)

CQI_TABLES = {1: CQI_TABLE_1, 2: CQI_TABLE_2}


def verify_tables() -> dict[str, Any]:
    """自检：频谱效率列必须等于 ``q_m × r_1024 / 1024``。

    这是表内部的冗余关系，抄错一个数就对不上——和 CDL 那次一样，
    **一份查表值必须有第二条独立路径核对**，这里用的是表自身的内蕴一致性。
    """
    bad = []
    for name, tbl in (("MCS_1", MCS_TABLE_1), ("MCS_2", MCS_TABLE_2),
                      ("CQI_1", CQI_TABLE_1), ("CQI_2", CQI_TABLE_2)):
        for e in tbl:
            want = e.q_m * e.r_1024 / 1024.0
            if abs(want - e.se) > 5e-4:
                bad.append(f"{name}[{e.index}] SE={e.se} 但 q_m·R/1024={want:.4f}")
    return {"consistent": not bad, "n_checked": sum(
        len(t) for t in (MCS_TABLE_1, MCS_TABLE_2, CQI_TABLE_1, CQI_TABLE_2)
    ), "mismatches": bad}


# ---------------------------------------------------------------------------
# 四、传输块大小（38.214 §5.1.3.2，逐步复刻）
# ---------------------------------------------------------------------------

# Table 5.1.3.2-1：N_info ≤ 3824 时的 TBS 取值表
_TBS_SMALL = (
    24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 128, 136, 144, 152,
    160, 168, 176, 184, 192, 208, 224, 240, 256, 272, 288, 304, 320, 336, 352,
    368, 384, 408, 432, 456, 480, 504, 528, 552, 576, 608, 640, 672, 704, 736,
    768, 808, 848, 888, 928, 984, 1032, 1064, 1128, 1160, 1192, 1224, 1256,
    1288, 1320, 1352, 1416, 1480, 1544, 1608, 1672, 1736, 1800, 1864, 1928,
    2024, 2088, 2152, 2216, 2280, 2408, 2472, 2536, 2600, 2664, 2728, 2792,
    2856, 2976, 3104, 3240, 3368, 3496, 3624, 3752, 3824,
)


def transport_block_size(n_re: int, rate: float, q_m: int, layers: int = 1) -> int:
    """按 38.214 §5.1.3.2 算传输块大小（bit）。

    ``n_re`` 是分配给 PDSCH 的资源单元总数（已扣掉 DM-RS 与开销）。
    步骤 3 走查表、步骤 4 走量化 + 分码块，两支都实现了——只做步骤 3
    会在大包时严重偏小。
    """
    n_info = float(n_re) * float(rate) * int(q_m) * int(layers)
    if n_info <= 0:
        return 0
    if n_info <= 3824:
        n = max(3, int(math.floor(math.log2(n_info))) - 6)
        n_info_q = max(24, (1 << n) * int(math.floor(n_info / (1 << n))))
        for tbs in _TBS_SMALL:
            if tbs >= n_info_q:
                return tbs
        return _TBS_SMALL[-1]

    n = int(math.floor(math.log2(n_info - 24))) - 5
    step = 1 << n
    # 标准要求 round 的平局向上取整
    n_info_q = max(3840, int(step * math.floor((n_info - 24) / step + 0.5)))
    if rate <= 0.25:
        c = math.ceil((n_info_q + 24) / 3816)
        return int(8 * c * math.ceil((n_info_q + 24) / (8 * c)) - 24)
    if n_info_q > 8424:
        c = math.ceil((n_info_q + 24) / 8424)
        return int(8 * c * math.ceil((n_info_q + 24) / (8 * c)) - 24)
    return int(8 * math.ceil((n_info_q + 24) / 8) - 24)


def re_per_slot(n_prb: int, n_symbols: int = 12, n_dmrs_per_prb: int = 12,
                overhead_per_prb: int = 0) -> int:
    """一个时隙内分配给 PDSCH 的 RE 数（38.214 §5.1.3.2 步骤 1）。

    每 PRB 的 RE 数 = 12·符号数 − DM-RS − 开销，且**上限 156**（标准明写），
    再乘 PRB 数。忘掉那个 156 上限会让大带宽下的 TBS 偏大。
    """
    per_prb = min(156, 12 * int(n_symbols) - int(n_dmrs_per_prb) - int(overhead_per_prb))
    return max(0, per_prb) * int(n_prb)


# ---------------------------------------------------------------------------
# 五、BLER 模型（**这是模型，不是实测曲线**）
# ---------------------------------------------------------------------------


# 38.212 §5.2.2 的 LDPC 码块最大长度。TB 超过它就要分段，
# 而分段直接决定 BLER：TB 只要有一个码块错就整块错。
_KCB_BG1 = 8448
_KCB_BG2 = 3840


def code_blocks(tbs_bits: int, rate: float) -> tuple[int, int]:
    """按 38.212 §5.2.2 算码块数与每块信息位长度。

    基图选择（§7.2.2）：TBS ≤ 292、或码率 ≤ 0.25、或（TBS ≤ 3824 且码率 ≤ 0.67）
    走 BG2（K_cb=3840），否则 BG1（K_cb=8448）。

    **不做分段会把 BLER 算得过于乐观**：273 PRB 的 TB 有两万多比特、切成
    二十多个码块，任一块错则整块错，TB 级 BLER 约是码块级的 C 倍。
    """
    b = int(tbs_bits) + 24  # TB CRC
    k_cb = _KCB_BG2 if (tbs_bits <= 292 or rate <= 0.25
                        or (tbs_bits <= 3824 and rate <= 0.67)) else _KCB_BG1
    if b <= k_cb:
        return 1, b
    c = math.ceil(b / (k_cb - 24))          # 每块要再加 24 bit 码块 CRC
    return int(c), int(math.ceil(b / c))


@dataclass
class BlerModel:
    """有限码长 + 实现损失的 BLER 模型。

    形式（正态近似的有限码长界，按**信道使用次数**即调制符号数）::

        码块 BLER = Q( (I(γ) − R·q_m) · sqrt(n_sym_per_cb) / c )
        TB   BLER = 1 − (1 − 码块BLER)^C

    * ``I(γ)`` —— 调制受限互信息，bit/符号，**这一项是精确算的**
    * ``R·q_m`` —— 实际信息率，同单位。两者之差就是"富余"
    * ``sqrt(n_sym)`` —— 有限码长的瀑布陡度，误差指数随 √n 收敛
    * ``c`` —— 把信道色散与译码器实现损失并进来的单一常数
    * ``C`` —— 码块数（38.212 分段）。**TB 只要有一块错就整块错**

    **参数怎么来的**：``implementation_loss_db`` 默认 1.0 dB，是 5G LDPC 在
    BLER=10%、中等码长下距容量的常见量级；``c`` 默认 2.2 由"瀑布区
    10%→1% 约 0.6~1 dB"反推，并使各 MCS 的 10% 门限落在公开 NR 曲线的
    常见区间（MCS0 约 −6 dB、MCS28 约 21 dB）。两者都可覆盖。

    **它不是什么**：不是实测 BLER 曲线，没有 3GPP 参考数据兜底。
    要严格的 BLER 请跑真正的链路级仿真（Sionna PHY / MATLAB 5G Toolbox）。
    用 ``anchor_check()`` 把各 MCS 的门限摆出来，对照公开曲线人工判断。
    """

    implementation_loss_db: float = 1.0
    c: float = 2.2

    def bler(self, sinr_eff_db: Any, mcs: Mcs, n_coded_bits: int,
             n_code_blocks: int = 1) -> np.ndarray:
        from scipy.stats import norm  # noqa: PLC0415

        g = np.atleast_1d(np.asarray(sinr_eff_db, dtype=float)) - self.implementation_loss_db
        # 富余按 bit/符号 算：I(γ) 是调制受限容量，R·q_m 是实际信息率
        margin = qam_mi(mcs.m_order, g) - mcs.rate * mcs.q_m
        n_cb = max(int(n_code_blocks), 1)
        n_sym_per_cb = max(int(n_coded_bits) / mcs.q_m / n_cb, 1.0)
        z = margin * math.sqrt(n_sym_per_cb) / max(self.c, 1e-9)
        p_cb = np.clip(norm.sf(z), 0.0, 1.0)      # 富余越多 BLER 越低
        return np.clip(1.0 - (1.0 - p_cb) ** n_cb, 0.0, 1.0)

    def required_sinr_db(self, mcs: Mcs, n_coded_bits: int,
                         target_bler: float = 0.1, n_code_blocks: int = 1) -> float:
        """达到目标 BLER 所需的有效 SINR（dB）。二分求解。"""
        lo, hi = -40.0, 50.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if float(self.bler(mid, mcs, n_coded_bits, n_code_blocks)[0]) > target_bler:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def anchor_check(self, table: int = 1, n_coded_bits: int = 20000,
                     target_bler: float = 0.1) -> dict[str, Any]:
        """报出各 MCS 的 10% BLER 门限，供人工对照公开的 NR 链路级曲线。

        **这不是自动判定**——没有参考数据就没法自动判。它做的是把模型的
        预测摆出来，让人一眼看出有没有离谱（比如 MCS0 要 20 dB 就明显不对）。
        另外核两条必须成立的性质：门限随 MCS 单调上升、且高于该 MCS 的
        香农极限对应信噪比。
        """
        tbl = MCS_TABLES[table]
        rows, thr = [], []
        for m in tbl:
            t = self.required_sinr_db(m, n_coded_bits, target_bler)
            shannon_db = 10.0 * math.log10(2.0 ** m.se - 1.0) if m.se > 0 else -np.inf
            rows.append({
                "mcs": m.index, "modulation": _MOD_NAME[m.q_m],
                "code_rate": round(m.rate, 3), "se": m.se,
                "required_sinr_db": round(t, 2),
                "shannon_limit_db": round(shannon_db, 2),
                "gap_to_shannon_db": round(t - shannon_db, 2),
            })
            thr.append(t)

        # **单调性只能在同一调制阶数内部要求。** 标准表在调制切换点上
        # 故意让 SE 有重叠（MCS9 QPSK SE=1.3262 → MCS10 16QAM SE=1.3281，
        # 但后者码率只有 0.332），所以切换点上门限小幅回落是正确物理，
        # 不是模型缺陷。整体判单调会把这两点误报成失败。
        switch = [i for i in range(1, len(tbl)) if tbl[i].q_m != tbl[i - 1].q_m]
        mono_within = all(
            thr[i] <= thr[i + 1] + 1e-6
            for i in range(len(thr) - 1) if (i + 1) not in switch
        )
        drops = [
            {"at_mcs": tbl[i].index,
             "from": _MOD_NAME[tbl[i - 1].q_m], "to": _MOD_NAME[tbl[i].q_m],
             "drop_db": round(thr[i - 1] - thr[i], 2)}
            for i in switch if thr[i] < thr[i - 1]
        ]
        above = all(r["gap_to_shannon_db"] > 0 for r in rows)
        return {
            "table": table, "target_bler": target_bler, "n_coded_bits": n_coded_bits,
            "monotonic_within_modulation": mono_within,
            "modulation_switch_drops": drops,
            "above_shannon_limit": above,
            "span_db": [rows[0]["required_sinr_db"], rows[-1]["required_sinr_db"]],
            "rows": rows,
            "expected_span_note": (
                "公开 NR 链路级曲线的常见量级：MCS0 约 −5~−7 dB，MCS28 约 20~23 dB。"
                "落在区间外说明模型参数需要重新标定。"
            ),
            "caveat": (
                "这是模型预测，不是实测 BLER 曲线。拿去和公开的 NR 链路级曲线对照；"
                "严格结论请跑真正的链路级仿真（Sionna PHY / MATLAB 5G Toolbox）。"
            ),
        }


DEFAULT_BLER = BlerModel()


# ---------------------------------------------------------------------------
# 六、链路自适应：选 MCS / 选 CQI
# ---------------------------------------------------------------------------


@dataclass
class LinkAdaptResult:
    """一次链路自适应的完整结果。"""

    sinr_eff_db: float
    mcs_index: int
    modulation: str
    code_rate: float
    se_mcs: float                 # 选中 MCS 的标称频谱效率
    layers: int
    bler: float
    tbs_bits: int
    n_re: int
    throughput_bps: float         # 计入 BLER 与 HARQ 后的有效吞吐
    throughput_ideal_bps: float   # 不计 BLER 的名义吞吐
    cqi: int
    se_shannon: float             # 同 SINR 下的香农谱效（上界）
    se_achieved: float            # 实际达到的谱效
    efficiency_vs_shannon: float  # 达成率
    harq_tx: float                # 平均传输次数

    def as_dict(self) -> dict[str, Any]:
        return {
            "sinr_eff_db": round(self.sinr_eff_db, 2),
            "mcs": self.mcs_index, "modulation": self.modulation,
            "code_rate": round(self.code_rate, 4), "layers": self.layers,
            "cqi": self.cqi,
            "bler": round(self.bler, 5),
            "tbs_bits": self.tbs_bits, "n_re": self.n_re,
            "throughput_mbps": round(self.throughput_bps / 1e6, 3),
            "throughput_ideal_mbps": round(self.throughput_ideal_bps / 1e6, 3),
            "se_shannon": round(self.se_shannon, 3),
            "se_achieved": round(self.se_achieved, 3),
            "efficiency_vs_shannon": round(self.efficiency_vs_shannon, 3),
            "harq_avg_tx": round(self.harq_tx, 3),
        }

    def text(self) -> str:
        return (
            f"有效 SINR {self.sinr_eff_db:.2f} dB → MCS {self.mcs_index}"
            f"（{self.modulation}, R={self.code_rate:.3f}）× {self.layers} 层，CQI {self.cqi}\n"
            f"  BLER {self.bler:.2%}  TBS {self.tbs_bits} bit  "
            f"吞吐 {self.throughput_bps/1e6:.2f} Mbps（名义 {self.throughput_ideal_bps/1e6:.2f}）\n"
            f"  谱效 {self.se_achieved:.3f} vs 香农 {self.se_shannon:.3f} "
            f"bit/s/Hz —— 达成 {self.efficiency_vs_shannon:.1%}"
        )


def select_cqi(sinr_eff_db: float, *, table: int = 1, target_bler: float = 0.1,
               n_coded_bits: int = 20000, n_code_blocks: int = 1,
               model: BlerModel | None = None) -> int:
    """按 38.214 的口径选 CQI：满足目标 BLER 的最高档。0 表示超出范围。"""
    mdl = model or DEFAULT_BLER
    best = 0
    for c in CQI_TABLES[table]:
        pseudo = Mcs(c.index, c.q_m, c.r_1024, c.se)
        if float(mdl.bler(sinr_eff_db, pseudo, n_coded_bits, n_code_blocks)[0]) <= target_bler:
            best = c.index
    return best


def select_mcs(sinr_eff_db: float, *, table: int = 1, target_bler: float = 0.1,
               n_coded_bits: int = 20000, n_code_blocks: int = 1,
               model: BlerModel | None = None) -> Mcs:
    """选满足目标 BLER 的最高 MCS。全都不满足时退回 MCS 0（并留下高 BLER）。"""
    mdl = model or DEFAULT_BLER
    tbl = MCS_TABLES[table]
    best = tbl[0]
    for m in tbl:
        if float(mdl.bler(sinr_eff_db, m, n_coded_bits, n_code_blocks)[0]) <= target_bler:
            best = m
    return best


def link_adaptation(
    sinr_per_rb_db: Any,
    *,
    n_prb: int = 273,
    layers: int = 1,
    mcs_table: int = 1,
    cqi_table: int = 1,
    target_bler: float = 0.1,
    slot_duration_s: float = 0.5e-3,
    n_symbols: int = 12,
    esm: str = "miesm",
    max_harq_tx: int = 4,
    model: BlerModel | None = None,
) -> LinkAdaptResult:
    """从逐 RB 的 SINR 到真实吞吐，走完整条链路到系统映射。

    步骤：逐 RB SINR → 有效 SINR（MIESM）→ 选 MCS（满足目标 BLER）→
    算 TBS（38.214 §5.1.3.2）→ 计入 BLER 与 HARQ 得有效吞吐。

    **HARQ 用的是理想合并的简化模型**：平均传输次数
    ``Σ_{k<K} BLER^k``，即每次重传相互独立、成功即停。真实 HARQ 有合并增益，
    重传的成功率更高，所以这个模型**偏保守**。
    """
    mdl = model or DEFAULT_BLER
    g = np.asarray(sinr_per_rb_db, dtype=float).ravel()
    g = g[np.isfinite(g)]
    if g.size == 0:
        raise ValueError("sinr_per_rb_db 里没有有效值")

    n_re = re_per_slot(n_prb, n_symbols=n_symbols)
    n_layers = max(1, int(layers))

    # 先用 64QAM 的 MI 曲线做一次有效 SINR 粗估来选 MCS，选定后按其调制阶数复算
    # ——MIESM 的 MI 曲线依赖调制阶数，不迭代一次会有系统性偏差。
    eff = effective_sinr(g, method=esm, m_order=64)
    mcs = select_mcs(eff, table=mcs_table, target_bler=target_bler,
                     n_coded_bits=n_re * 6 * n_layers, model=mdl)
    eff = effective_sinr(g, method=esm, m_order=mcs.m_order)
    n_coded = n_re * mcs.q_m * n_layers
    tbs0 = transport_block_size(n_re, mcs.rate, mcs.q_m, n_layers)
    n_cb, _ = code_blocks(tbs0, mcs.rate)
    mcs = select_mcs(eff, table=mcs_table, target_bler=target_bler,
                     n_coded_bits=n_coded, n_code_blocks=n_cb, model=mdl)
    n_coded = n_re * mcs.q_m * n_layers

    tbs = transport_block_size(n_re, mcs.rate, mcs.q_m, n_layers)
    n_cb, _ = code_blocks(tbs, mcs.rate)
    bler = float(mdl.bler(eff, mcs, n_coded, n_cb)[0])

    # HARQ：平均传输次数与最终残余错误
    p_fail_final = bler ** max_harq_tx
    avg_tx = sum(bler ** k for k in range(max_harq_tx))
    tput_ideal = tbs / slot_duration_s
    tput = tbs * (1.0 - p_fail_final) / (avg_tx * slot_duration_s)

    # 谱效按 RE 口径算（bit per RE），避免"带宽"定义上的歧义：
    # 保护带、DM-RS 开销算不算，不同口径能差 10%。
    se_achieved = tbs * (1.0 - p_fail_final) / (avg_tx * n_re)
    se_shannon = float(np.mean(np.log2(1.0 + 10.0 ** (g / 10.0)))) * n_layers

    return LinkAdaptResult(
        sinr_eff_db=eff, mcs_index=mcs.index, modulation=_MOD_NAME[mcs.q_m],
        code_rate=mcs.rate, se_mcs=mcs.se, layers=n_layers, bler=bler,
        tbs_bits=tbs, n_re=n_re, throughput_bps=tput,
        throughput_ideal_bps=tput_ideal,
        cqi=select_cqi(eff, table=cqi_table, target_bler=target_bler,
                       n_coded_bits=n_coded, n_code_blocks=n_cb, model=mdl),
        se_shannon=se_shannon, se_achieved=se_achieved,
        efficiency_vs_shannon=se_achieved / max(se_shannon, _EPS),
        harq_tx=avg_tx,
    )


# ---------------------------------------------------------------------------
# 七、数据集级：吞吐分布与边缘用户
# ---------------------------------------------------------------------------


@dataclass
class ThroughputStats:
    n: int
    mean_mbps: float
    median_mbps: float
    cell_edge_mbps: float      # 5% 分位 —— 3GPP 评估里的公平性指标
    peak_mbps: float           # 95% 分位
    mean_se: float
    cell_edge_se: float
    mcs_distribution: dict[int, int] = field(default_factory=dict)
    mean_bler: float = 0.0
    outage_ratio: float = 0.0  # 连 MCS 0 都达不到目标 BLER 的比例

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "mean_mbps": round(self.mean_mbps, 3),
            "median_mbps": round(self.median_mbps, 3),
            "cell_edge_mbps_5pct": round(self.cell_edge_mbps, 3),
            "peak_mbps_95pct": round(self.peak_mbps, 3),
            "mean_se": round(self.mean_se, 3),
            "cell_edge_se_5pct": round(self.cell_edge_se, 3),
            "mean_bler": round(self.mean_bler, 4),
            "outage_ratio": round(self.outage_ratio, 4),
            "mcs_distribution": {int(k): int(v) for k, v in
                                 sorted(self.mcs_distribution.items())},
            "note": (
                "5% 分位是 3GPP 评估里的边缘用户指标，比均值更能反映公平性。"
                "BLER 来自模型而非实测，见 linkadapt 模块文档。"
            ),
        }

    def text(self) -> str:
        return (
            f"吞吐（n={self.n}）：均值 {self.mean_mbps:.2f} / 中位 {self.median_mbps:.2f} / "
            f"边缘用户(5%) {self.cell_edge_mbps:.2f} / 峰值(95%) {self.peak_mbps:.2f} Mbps\n"
            f"  谱效 均值 {self.mean_se:.3f}，边缘 {self.cell_edge_se:.3f} bit/s/Hz\n"
            f"  平均 BLER {self.mean_bler:.2%}，中断比例 {self.outage_ratio:.2%}\n"
            f"  MCS 分布 {dict(sorted(self.mcs_distribution.items()))}"
        )


def throughput_stats(results: list[LinkAdaptResult]) -> ThroughputStats:
    """把一批逐样本的链路自适应结果汇总成吞吐分布。"""
    if not results:
        raise ValueError("results 是空的")
    t = np.array([r.throughput_bps for r in results]) / 1e6
    se = np.array([r.se_achieved for r in results])
    dist: dict[int, int] = {}
    for r in results:
        dist[r.mcs_index] = dist.get(r.mcs_index, 0) + 1
    return ThroughputStats(
        n=len(results),
        mean_mbps=float(t.mean()), median_mbps=float(np.median(t)),
        cell_edge_mbps=float(np.percentile(t, 5)),
        peak_mbps=float(np.percentile(t, 95)),
        mean_se=float(se.mean()), cell_edge_se=float(np.percentile(se, 5)),
        mcs_distribution=dist,
        mean_bler=float(np.mean([r.bler for r in results])),
        outage_ratio=float(np.mean([r.mcs_index == 0 and r.bler > 0.1 for r in results])),
    )
