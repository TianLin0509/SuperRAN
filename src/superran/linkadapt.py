"""链路自适应与链路到系统映射 —— 从 SINR 到真实吞吐。

## 为什么需要这一层

`linklevel.py` 给的是 `SE = Σ_layer log2(1 + SINR)`，也就是**香农谱效**。
它是个上界，任何真实系统都达不到，原因有三：

1. **调制受限**。20 dB 时香农说 6.66 bit/s/Hz，但 64QAM 最多只能给 5.80——
   星座点数摆在那里。这一项这里**精确算**（约束容量，见 `qam_mi`）。
2. **码率离散**。MCS 只有 29 档，实际码率总是落在需要的码率之下。
3. **有限码长 + 实现损失**。LDPC 距容量约 1~2 dB，且短包更差。

系统级仿真通常不跑完整 PHY 链（LDPC 编解码、软解调），而是用
**链路到系统映射**：把逐 RE 的 SINR 矢量压成一个有效 SINR，再查 BLER。
本模块提供 MIESM/EESM 作为通用链路级能力；当前 ``experience_v2 + table=3``
并未接入它们，而是使用 RBG 内线性、跨 RBG/stream 的 dB 平均后查预置曲线。

## 哪些是算出来的，哪些是模型

**必须分清，否则会把模型当测量用：**

| 部分 | 性质 | 依据 |
|---|---|---|
| QAM 约束容量 `qam_mi` | **精确计算** | Gauss-Hermite 求积，可对香农上界自检 |
| 有效 SINR（MIESM/EESM） | **可选链路级能力** | 互信息平均 / 指数平均；当前体验主链不使用 |
| MCS / CQI 表 | **标准查表** | 38.214 Table 5.1.3.1-1/-2、5.2.2.1-2/-3/-4 |
| TBS | **标准算法** | 38.214 §5.1.3.2，逐步复刻 |
| BLER（默认） | **分析模型** | 有限码长形状 + 可配的实现损失。**不是实测曲线** |
| BLER（表 3） | **预置曲线** | 20B 256QAM 曲线；横轴为单码字有效 SINR，系统重传由 NewTx 曲线推导 |

默认 BLER 模型没有 3GPP 参考曲线兜底，所以：
参数全部可配、默认值有出处、`anchor_check()` 会报出各 MCS 的 10% BLER 门限
供人工对照公开的 NR 链路级曲线。**别把它当成实测 BLER 用。**

`mcs_table=3` 改用内置的 28 档预置表驱动曲线。它比分析模型多了离散曲线形状，
但**仍不是 3GPP 标准曲线**。源载荷虽写 `Es/No`，本预置表将它解释为
经典 MMSE 接收机的单码字有效 SINR；该预置 profile 跨 TBS/RE/rank/场景通用。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from . import bler_curves as bc
from . import carrier as carrier_grid

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


# MI 表的信噪比栅格。-30~45 dB 覆盖了所有实际工作点，0.25 dB 步长够插值。
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

# 预置 20B 256QAM MCS profile。码率是源表的小数值，不冒充 38.214。
MCS_TABLE_3: tuple[Mcs, ...] = tuple(
    Mcs(
        int(row["index"]), int(row["q_m"]), float(row["newtx_code_rate"]) * 1024.0,
        float(row["q_m"]) * float(row["newtx_code_rate"]),
    )
    for row in bc.mcs_profile_rows()
)

MCS_TABLES = {1: MCS_TABLE_1, 2: MCS_TABLE_2, 3: MCS_TABLE_3}
MCS_TABLE_SOURCES = {
    1: "3GPP TS 38.214 Table 5.1.3.1-1",
    2: "3GPP TS 38.214 Table 5.1.3.1-2",
    3: "bundled preset_20b_256qam profile",
}


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

    每 PRB 的 RE 数 = 12·符号数 - DM-RS - 开销，且**上限 156**（标准明写），
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
    # TB CRC 长度按 38.212 §5.2.2：A > 3824 才挂 24 bit CRC24A，否则 16 bit。
    b = int(tbs_bits) + (24 if int(tbs_bits) > 3824 else 16)
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

        码块 BLER = Q( (I(γ) - R·q_m) · sqrt(n_sym_per_cb) / c )
        TB   BLER = 1 - (1 - 码块BLER)^C

    * ``I(γ)`` —— 调制受限互信息，bit/符号，**这一项是精确算的**
    * ``R·q_m`` —— 实际信息率，同单位。两者之差就是"富余"
    * ``sqrt(n_sym)`` —— 有限码长的瀑布陡度，误差指数随 √n 收敛
    * ``c`` —— 把信道色散与译码器实现损失并进来的单一常数
    * ``C`` —— 码块数（38.212 分段）。**TB 只要有一块错就整块错**

    **参数怎么来的**：``implementation_loss_db`` 默认 1.0 dB，是 5G LDPC 在
    BLER=10%、中等码长下距容量的常见量级；``c`` 默认 2.2 由"瀑布区
    10%→1% 约 0.6~1 dB"反推，并使各 MCS 的 10% 门限落在公开 NR 曲线的
    常见区间（MCS0 约 -6 dB、MCS28 约 21 dB）。两者都可覆盖。

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
                "公开 NR 链路级曲线的常见量级：MCS0 约 -5~-7 dB，MCS28 约 20~23 dB。"
                "落在区间外说明模型参数需要重新标定。"
            ),
            "caveat": (
                "这是模型预测，不是实测 BLER 曲线。拿去和公开的 NR 链路级曲线对照；"
                "严格结论请跑真正的链路级仿真（Sionna PHY / MATLAB 5G Toolbox）。"
            ),
        }


@dataclass(frozen=True)
class CurveBlerModel:
    """BLER provider backed by the user-supplied NewTx/ReTx demodulation curves.

    ``n_coded_bits`` and ``n_code_blocks`` remain in the method signature so this
    provider can share the analytic model's pipeline. They do not reshape a tabulated
    curve. The preset defines one scheduled TTI's single-codeword TB as one BLER event
    and does not expose CB errors separately. Lookup intentionally uses only MCS,
    transmission kind and codeword-level effective SINR; TBS/RE/rank/scenario are not
    axes of this preset and no CB-to-TB conversion is synthesized.
    """

    tx_mode: str = "newtx"
    source_id: str = "preset_20b_256qam"

    def _curve(self, mcs: Mcs) -> bc.DemodCurve:
        curve = bc.get_curve(mcs.index, self.tx_mode)
        if curve.q_m != mcs.q_m:
            raise ValueError(
                f"curve MCS {mcs.index} uses Qm={curve.q_m}, but table uses Qm={mcs.q_m}"
            )
        if self.tx_mode == "newtx" and abs(curve.code_rate - mcs.rate) > 5e-4:
            raise ValueError(
                f"curve MCS {mcs.index} uses R={curve.code_rate}, but table uses R={mcs.rate}"
            )
        return curve

    def bler(self, sinr_eff_db: Any, mcs: Mcs, n_coded_bits: int,
             n_code_blocks: int = 1) -> np.ndarray:
        del n_coded_bits, n_code_blocks
        return self._curve(mcs).evaluate(sinr_eff_db)

    def required_sinr_db(self, mcs: Mcs, n_coded_bits: int,
                         target_bler: float = 0.1, n_code_blocks: int = 1) -> float:
        del n_coded_bits, n_code_blocks
        return self._curve(mcs).required_sinr_db(target_bler)


DEFAULT_BLER = BlerModel()
DEFAULT_CURVE_BLER = CurveBlerModel("newtx")
DEFAULT_CURVE_RETX_BLER = CurveBlerModel("retx")


def _default_bler_model(table: int) -> BlerModel | CurveBlerModel:
    if table not in MCS_TABLES:
        raise ValueError(f"mcs table must be one of {sorted(MCS_TABLES)}, got {table}")
    return DEFAULT_CURVE_BLER if table == 3 else DEFAULT_BLER


def curve_anchor_check(target_bler: float = 0.1) -> dict[str, Any]:
    """Return NewTx/ReTx thresholds for every MCS in the tabulated profile."""
    rows = []
    for mcs in MCS_TABLE_3:
        new = bc.get_curve(mcs.index, "newtx")
        retx = bc.get_curve(mcs.index, "retx")
        new_thr = new.required_sinr_db(target_bler)
        retx_thr = retx.required_sinr_db(target_bler)
        shannon_db = 10.0 * math.log10(2.0 ** mcs.se - 1.0) if mcs.se > 0 else -np.inf
        rows.append({
            "mcs": mcs.index,
            "modulation": _MOD_NAME[mcs.q_m],
            "newtx_code_rate": round(new.code_rate, 4),
            "retx_code_rate": round(retx.code_rate, 4),
            "newtx_required_sinr_db": round(new_thr, 3),
            "retx_required_sinr_db": round(retx_thr, 3),
            "retx_gain_db": round(new_thr - retx_thr, 3),
            "shannon_limit_db": round(shannon_db, 3),
            "newtx_gap_to_shannon_db": round(new_thr - shannon_db, 3),
        })
    return {
        "source_id": bc.data.SOURCE_ID,
        "target_bler": target_bler,
        "axis_source_name": bc.data.SOURCE_AXIS_NAME,
        "axis_original_label": bc.data.SOURCE_AXIS_ORIGINAL_LABEL,
        "axis_interpretation": bc.data.SOURCE_AXIS_USAGE,
        "receiver_model": bc.data.RECEIVER_MODEL,
        "profile_scope": bc.data.PROFILE_SCOPE,
        "verify": bc.verify_curves(target_bler),
        "rows": rows,
        "caveat": (
            "These are bundled preset demodulation curves, not 3GPP reference BLER. "
            "The source label Es/No denotes codeword-level effective SINR for a "
            "classic MMSE receiver. The MCS curve is universal across TBS/RE/rank/"
            "scenario by product decision. Raw ReTx rows are audit-only for the "
            "current system HARQ model."
        ),
    }


def bler_curve(mcs: int, tx_mode: str = "newtx", target_bler: float = 0.1,
               sinr_db: Any | None = None) -> dict[str, Any]:
    """Return a raw curve plus an optional interpolated BLER query."""
    curve = bc.get_curve(mcs, tx_mode)
    out = curve.as_dict(include_points=True)
    out["target_bler"] = float(target_bler)
    out["required_sinr_db"] = round(curve.required_sinr_db(target_bler), 4)
    if sinr_db is not None:
        query = np.atleast_1d(np.asarray(sinr_db, dtype=float))
        out["query"] = {
            "sinr_db": [float(v) for v in query],
            "bler": [float(v) for v in curve.evaluate(query)],
        }
    return out


CHASE_COMBINING_GAIN_DB = 10.0 * math.log10(2.0)


def mcs_for_spectral_efficiency(target_se: float, *, table: int = 3) -> Mcs:
    """返回谱效不超过目标值的最高 MCS；低于表下界时钳到 MCS0。"""
    value = float(target_se)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"target_se 必须是有限非负数，收到 {target_se!r}")
    if table not in MCS_TABLES:
        raise ValueError(f"mcs table must be one of {sorted(MCS_TABLES)}, got {table}")
    candidates = [m for m in MCS_TABLES[table] if m.se <= value + 1e-12]
    return max(candidates, key=lambda m: m.index) if candidates else MCS_TABLES[table][0]


def harq_retransmission_bler(
    transmitted_mcs: int,
    codeword_sinr_db: float,
    *,
    combining: str = "ir",
    table: int = 3,
) -> dict[str, Any]:
    """一次重传的预置 TB-BLER 工程抽象。

    发射侧 MCS、RBG 数、rank/TBS 由 HARQ TB 状态原样保留；这里返回的
    ``lookup_mcs`` 只是 BLER 查表用的等效档位，绝不改写重传 DCI/MCS。

    * ``cc``：重复同一编码块，合并等价为 SINR 增加 ``10log10(2)`` dB；
    * ``ir``：默认。两次传输共同解一份数据，等效谱效为原 MCS 的一半，
      在同一 MCS 表中向下映射后，以当前码字 SINR 查询 NewTx 曲线。
    """
    if table != 3:
        raise ValueError("预置 HARQ 合并抽象当前只支持 MCS table 3")
    if (isinstance(transmitted_mcs, (bool, np.bool_))
            or not isinstance(transmitted_mcs, (int, np.integer))):
        raise ValueError(
            f"transmitted_mcs 必须是 0..27 的整数，收到 {transmitted_mcs!r}")
    mcs = int(transmitted_mcs)
    if mcs < 0 or mcs >= len(MCS_TABLES[table]):
        raise ValueError(f"transmitted_mcs 必须在 0..27，收到 {transmitted_mcs}")
    scheme = str(combining).strip().lower()
    if scheme not in ("cc", "ir"):
        raise ValueError("harq combining 只支持 ir / cc")
    if scheme == "cc":
        lookup_mcs = mcs
        equivalent_se = MCS_TABLES[table][mcs].se
    else:
        equivalent_se = MCS_TABLES[table][mcs].se / 2.0
        lookup_mcs = mcs_for_spectral_efficiency(
            equivalent_se, table=table).index
    sinr = float(codeword_sinr_db)
    if math.isnan(sinr) or sinr == float("-inf"):
        lookup_sinr = -60.0
        bler = 1.0
    else:
        if scheme == "cc":
            lookup_sinr = sinr + CHASE_COMBINING_GAIN_DB
        else:
            lookup_sinr = sinr
        lookup_sinr = float(np.clip(lookup_sinr, -60.0, 60.0))
        bler = float(bc.get_curve(lookup_mcs, "newtx").evaluate(lookup_sinr)[0])
    return {
        "combining": scheme,
        "transmitted_mcs": mcs,
        "lookup_mcs": int(lookup_mcs),
        "input_codeword_sinr_db": sinr,
        "lookup_sinr_db": float(lookup_sinr),
        "chase_combining_gain_db": (
            CHASE_COMBINING_GAIN_DB if scheme == "cc" else 0.0),
        "original_spectral_efficiency": MCS_TABLES[table][mcs].se,
        "equivalent_spectral_efficiency": float(equivalent_se),
        "bler": float(np.clip(bler, 0.0, 1.0)),
        "curve_tx_mode": "newtx",
        "transmission_identity": "same transmitted MCS/RBG/rank/TBS; lookup-only abstraction",
    }


# ---------------------------------------------------------------------------
# 六 · A、TDD AMC：CQI → PMI/SVD BF Gain → MCS → OLLA
# ---------------------------------------------------------------------------


# 预置链路的 256QAM CQI 行 → MCS 离散表。``cqi_index`` 是历史 API 使用的
# 0..14 表行；对外 4-bit CQI codepoint 则是 1..15，codepoint 0 表示 out-of-range。
# 两个编号必须同时返回，避免把“数组第 0 行”误写成“上报 CQI=0”。最后一行
# 请求 MCS28；当前 preset_20b_256qam 只有 MCS0..27，因此必须显式标出钳位。
INTERNAL_CQI_TO_MCS: tuple[int, ...] = (
    0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28,
)
INTERNAL_CQI_PROFILE_ID = "internal_256qam_cqi_to_mcs_v2"
INTERNAL_CQI_SOURCE = "versioned 256QAM CQI-row-to-MCS mapping"


def internal_cqi_to_mcs(
    cqi_index: int,
    *,
    mcs_table: int = 3,
) -> dict[str, Any]:
    """Resolve the confirmed internal CQI index through its explicit MCS table.

    ``cqi_index`` is the legacy internal table row 0..14, not the reported 4-bit
    codepoint. Row 0 corresponds to reported CQI 1; reported CQI 0 is out-of-range
    and is handled by :func:`reported_cqi_to_mcs`. The requested mapping is preserved
    separately from the active profile so a missing top-end curve cannot be hidden.
    """
    if mcs_table not in MCS_TABLES:
        raise ValueError(f"mcs table must be one of {sorted(MCS_TABLES)}, got {mcs_table}")
    if (isinstance(cqi_index, (bool, np.bool_))
            or not isinstance(cqi_index, (int, np.integer))):
        raise ValueError(
            f"internal CQI must be an integer in 0..{len(INTERNAL_CQI_TO_MCS) - 1}, "
            f"got {cqi_index!r}"
        )
    idx = int(cqi_index)
    if idx < 0 or idx >= len(INTERNAL_CQI_TO_MCS):
        raise ValueError(
            f"internal CQI must be 0..{len(INTERNAL_CQI_TO_MCS) - 1}, got {cqi_index}"
        )

    requested = int(INTERNAL_CQI_TO_MCS[idx])
    table = MCS_TABLES[mcs_table]
    lo, hi = int(table[0].index), int(table[-1].index)
    resolved = min(max(requested, lo), hi)
    mcs = next(m for m in table if m.index == resolved)
    clipped = resolved != requested
    return {
        "scheduled": True,
        "cqi": idx,
        "index": idx,
        "cqi_row": idx,
        "reported_cqi_codepoint": idx + 1,
        "reported_cqi_zero_semantics": "out_of_range",
        "cqi_numbering": "internal_row",
        "cqi_profile": INTERNAL_CQI_PROFILE_ID,
        "cqi_source": INTERNAL_CQI_SOURCE,
        "mcs_table": int(mcs_table),
        "requested_mcs": requested,
        "mcs": resolved,
        "modulation": _MOD_NAME[mcs.q_m],
        "code_rate": round(float(mcs.rate), 6),
        "mcs_spectral_efficiency": mcs.se,
        "mcs_profile_min": lo,
        "mcs_profile_max": hi,
        "mcs_clipped_to_profile": clipped,
        "mapping": "explicit internal CQI-to-MCS lookup table",
    }


def reported_cqi_to_mcs(
    cqi_codepoint: int,
    *,
    mcs_table: int = 3,
) -> dict[str, Any]:
    """Resolve a reported 4-bit CQI codepoint 0..15 through the 256QAM profile.

    Codepoint 0 is an explicit out-of-range state and schedules no MCS. Codepoints
    1..15 map to legacy internal rows 0..14. Keeping this adapter separate avoids a
    silent off-by-one change for scripts that historically passed table-row indices.
    """
    if mcs_table not in MCS_TABLES:
        raise ValueError(f"mcs table must be one of {sorted(MCS_TABLES)}, got {mcs_table}")
    if (isinstance(cqi_codepoint, (bool, np.bool_))
            or not isinstance(cqi_codepoint, (int, np.integer))):
        raise ValueError(f"reported CQI must be an integer in 0..15, got {cqi_codepoint!r}")
    codepoint = int(cqi_codepoint)
    if not 0 <= codepoint <= 15:
        raise ValueError(f"reported CQI must be 0..15, got {cqi_codepoint}")
    if codepoint == 0:
        return {
            "scheduled": False,
            "cqi": 0,
            "reported_cqi_codepoint": 0,
            "reported_cqi_zero_semantics": "out_of_range",
            "cqi_row": None,
            "cqi_numbering": "reported_4bit",
            "cqi_profile": INTERNAL_CQI_PROFILE_ID,
            "cqi_source": INTERNAL_CQI_SOURCE,
            "mcs_table": int(mcs_table),
            "requested_mcs": None,
            "mcs": None,
            "mapping": "reported 4-bit CQI 0 means out-of-range",
        }
    row = internal_cqi_to_mcs(codepoint - 1, mcs_table=mcs_table)
    return {
        **row,
        # ``cqi`` follows this adapter's input convention.  The legacy row remains
        # available as ``cqi_row`` / ``index`` so neither numbering is implicit.
        "cqi": codepoint,
        "cqi_numbering": "reported_4bit",
        "mapping": "reported 4-bit CQI codepoint to versioned 256QAM MCS table",
    }


def internal_cqi_mapping_rows(*, mcs_table: int = 3) -> list[dict[str, Any]]:
    """Return all internal CQI rows, including any profile-limit clipping."""
    return [
        internal_cqi_to_mcs(cqi, mcs_table=mcs_table)
        for cqi in range(len(INTERNAL_CQI_TO_MCS))
    ]


@lru_cache(maxsize=32)
def _internal_cqi_thresholds(
    target_bler: float,
    mcs_table: int,
) -> tuple[float, ...]:
    """NewTx target-BLER thresholds for selectable internal CQI entries.

    A requested MCS outside the active profile uses the explicitly clipped MCS.  In
    the current mapping the highest row requests MCS28 and resolves to the otherwise
    unused MCS27, so reported CQI15 remains reachable at the MCS27 threshold.  If a
    future mapping clips two adjacent rows to the same MCS, only the first codepoint
    is reachable; the duplicate row is assigned ``+inf``.
    """
    if mcs_table != 3:
        raise ValueError("internal CQI threshold lookup currently requires mcs_table=3")
    out: list[float] = []
    previous_resolved_mcs: int | None = None
    for cqi in range(len(INTERNAL_CQI_TO_MCS)):
        row = internal_cqi_to_mcs(cqi, mcs_table=mcs_table)
        resolved_mcs = int(row["mcs"])
        if resolved_mcs == previous_resolved_mcs:
            out.append(float("inf"))
        else:
            out.append(float(
                bc.get_curve(resolved_mcs, "newtx").required_sinr_db(target_bler)
            ))
        previous_resolved_mcs = resolved_mcs
    return tuple(out)


def select_internal_cqi(
    sinr_eff_db: float,
    *,
    target_bler: float = 0.1,
    mcs_table: int = 3,
) -> int:
    """Quantise PMI-SINR to the confirmed internal CQI scale.

    The return value is a legacy table row 0..14. Below the first row's target-BLER
    threshold it still returns row 0; callers that need a reported 4-bit codepoint
    must use :func:`select_reported_cqi`, which returns out-of-range codepoint 0.
    """
    target = float(target_bler)
    if not (math.isfinite(target) and 0.0 < target < 1.0):
        raise ValueError(f"target_bler must be in (0,1), got {target_bler!r}")
    value = float(sinr_eff_db)
    if not math.isfinite(value):
        return 0
    best = 0
    for cqi, threshold in enumerate(_internal_cqi_thresholds(target, int(mcs_table))):
        if threshold <= value:
            best = cqi
    return int(best)


def select_reported_cqi(
    sinr_eff_db: float,
    *,
    target_bler: float = 0.1,
    mcs_table: int = 3,
) -> int:
    """Quantise SINR to a reported 4-bit CQI codepoint 0..15."""
    target = float(target_bler)
    if not (math.isfinite(target) and 0.0 < target < 1.0):
        raise ValueError(f"target_bler must be in (0,1), got {target_bler!r}")
    value = float(sinr_eff_db)
    if not math.isfinite(value):
        return 0
    thresholds = _internal_cqi_thresholds(target, int(mcs_table))
    if value < thresholds[0]:
        return 0
    return select_internal_cqi(
        value, target_bler=target, mcs_table=mcs_table) + 1


def cqi_to_mcs_by_se(
    cqi_index: int,
    *,
    cqi_table: int = 2,
    mcs_table: int = 3,
) -> dict[str, Any]:
    """Map CQI to the highest MCS whose spectral efficiency does not exceed CQI.

    CQI 0 means out of range and is never silently converted to MCS 0.  If CQI 1/2
    falls below the lowest preset-profile MCS, the mapping clamps to MCS 0 and marks
    ``clamped_low`` so the caller can see that the tables do not overlap there.
    """
    if cqi_table not in CQI_TABLES:
        raise ValueError(f"cqi table must be one of {sorted(CQI_TABLES)}, got {cqi_table}")
    if mcs_table not in MCS_TABLES:
        raise ValueError(f"mcs table must be one of {sorted(MCS_TABLES)}, got {mcs_table}")
    idx = int(cqi_index)
    if idx < 0 or idx > 15:
        raise ValueError(f"CQI must be 0..15, got {cqi_index}")
    if idx == 0:
        return {
            "scheduled": False,
            "cqi": 0,
            "mcs": None,
            "reason": "CQI 0 is out of range; no transmission is scheduled",
        }

    cqi = next((c for c in CQI_TABLES[cqi_table] if c.index == idx), None)
    if cqi is None:
        raise ValueError(f"CQI {idx} is not present in table {cqi_table}")
    table = MCS_TABLES[mcs_table]
    candidates = [m for m in table if m.se <= cqi.se + 1e-12]
    clamped_low = not candidates
    mcs = max(candidates, key=lambda m: m.index) if candidates else table[0]
    return {
        "scheduled": True,
        "cqi": idx,
        "cqi_table": cqi_table,
        "cqi_spectral_efficiency": cqi.se,
        "mcs_table": mcs_table,
        "mcs": mcs.index,
        "mcs_spectral_efficiency": mcs.se,
        "clamped_low": clamped_low,
        "mapping": "highest MCS spectral efficiency <= CQI spectral efficiency",
    }


def update_olla_mcs(
    current_offset_mcs: float,
    feedback_ack: bool,
    *,
    target_bler: float = 0.1,
    ack_step_mcs: float = 0.01,
) -> dict[str, float | str | bool]:
    """Advance an OLLA state expressed in continuous MCS-index units.

    A positive offset is aggressive. ACK raises it by ``ack_step_mcs``; NACK lowers
    it by ``ack_step_mcs * (1-target)/target`` so the zero-drift point is the target
    first-transmission BLER. The returned value is for the *next* scheduling decision.
    """
    target = float(target_bler)
    step_up = float(ack_step_mcs)
    current = float(current_offset_mcs)
    if not (0.0 < target < 1.0):
        raise ValueError(f"target_bler must be in (0, 1), got {target_bler}")
    if not math.isfinite(current):
        raise ValueError("current_offset_mcs must be finite")
    if not math.isfinite(step_up) or step_up <= 0.0:
        raise ValueError(f"ack_step_mcs must be finite and > 0, got {ack_step_mcs}")
    step_down = step_up * (1.0 - target) / target
    delta = step_up if bool(feedback_ack) else -step_down
    return {
        "feedback_ack": bool(feedback_ack),
        "current_offset_mcs": current,
        "delta_mcs": delta,
        "next_offset_mcs": current + delta,
        "ack_step_mcs": step_up,
        "nack_step_mcs": step_down,
        "target_bler": target,
        "sign_convention": "positive offset is more aggressive",
    }


def apply_olla_mcs(
    mcs_without_olla: int,
    olla_mcs_offset: float,
    *,
    mcs_table: int = 3,
) -> dict[str, Any]:
    """Apply continuous MCS-domain OLLA after the SINR-to-MCS decision.

    The order is contractual: first select the no-OLLA MCS from SINR, then add the
    user-level OLLA state, apply mathematical ``floor``, and clip to the active MCS
    profile.  A tiny negative offset therefore lowers an integer base MCS by one.
    """
    if mcs_table not in MCS_TABLES:
        raise ValueError(f"mcs table must be one of {sorted(MCS_TABLES)}, got {mcs_table}")
    if (isinstance(mcs_without_olla, (bool, np.bool_))
            or not isinstance(mcs_without_olla, (int, np.integer))):
        raise ValueError(f"mcs_without_olla must be an integer, got {mcs_without_olla!r}")
    base = int(mcs_without_olla)
    table = MCS_TABLES[mcs_table]
    lo, hi = int(table[0].index), int(table[-1].index)
    if base < lo or base > hi:
        raise ValueError(f"mcs_without_olla must be {lo}..{hi}, got {base}")
    offset = float(olla_mcs_offset)
    if not math.isfinite(offset):
        raise ValueError("olla_mcs_offset must be finite")
    before_floor = float(base) + offset
    floored = math.floor(before_floor)
    final = min(max(floored, lo), hi)
    return {
        "mcs_without_olla": base,
        "olla_mcs_offset": offset,
        "mcs_before_floor": before_floor,
        "mcs_after_floor": int(floored),
        "final_mcs": int(final),
        "mcs_clipped": final != floored,
        "olla_domain": "continuous_mcs_index",
        "operation_order": "SINR-to-MCS, then OLLA, then floor, then profile clip",
    }


def _sinr_grid_db(values: Any, name: str) -> np.ndarray:
    grid = np.asarray(values, dtype=float)
    if grid.ndim == 1:
        grid = grid[:, None]
    if grid.ndim != 2 or grid.size == 0:
        raise ValueError(f"{name} must have shape [RB, layer], got {grid.shape}")
    if not np.all(np.isfinite(grid)):
        raise ValueError(f"{name} must contain only finite values")
    return grid


def codeword_sinr_db(
    sinr_per_rb_stream_db: Any,
    *,
    rb_per_rbg: int = 16,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
) -> dict[str, Any]:
    """Aggregate ``[RB, stream]`` post-MMSE SINR to the current codeword view.

    RBGs average RB SINR in the **linear power domain**.  The resulting
    ``[RBG, stream]`` values are converted to dB, then averaged arithmetically
    across RBGs and streams.  This mirrors the system-table contract and is not
    presented as a calibrated EESM/MIESM mapping.
    """
    grid_db = _sinr_grid_db(sinr_per_rb_stream_db, "sinr_per_rb_stream_db")
    if (isinstance(rb_per_rbg, (bool, np.bool_))
            or not isinstance(rb_per_rbg, (int, np.integer))
            or int(rb_per_rbg) < 1):
        raise ValueError("rb_per_rbg must be a positive integer")
    n_rb = int(grid_db.shape[0])
    bounds = (
        carrier_grid.validate_boundaries(n_rb, rbg_boundaries)
        if rbg_boundaries is not None
        else carrier_grid.uniform_boundaries(n_rb, min(int(rb_per_rbg), n_rb))
    )
    lin = 10.0 ** (grid_db / 10.0)
    rbg_lin = np.stack([
        np.mean(lin[start:stop], axis=0) for start, stop in bounds
    ])
    rbg_stream_db = 10.0 * np.log10(np.maximum(rbg_lin, _EPS))
    stream_db = np.mean(rbg_stream_db, axis=0)
    return {
        "user_db": float(np.mean(stream_db)),
        "stream_db": np.asarray(stream_db, dtype=float),
        "rbg_stream_db": np.asarray(rbg_stream_db, dtype=float),
        "n_rb": n_rb,
        "n_rbg": len(bounds),
        "rb_per_rbg": int(rb_per_rbg),
        "rbg_boundaries": tuple((int(a), int(b)) for a, b in bounds),
        "aggregation": (
            "RB linear mean within each RBG; arithmetic mean across "
            "RBG x stream in dB domain"
        ),
    }


def tdd_mcs_adaptation(
    cqi_index: int,
    svd_sinr_per_rb_db: Any,
    pmi_sinr_per_rb_db: Any,
    *,
    olla_mcs_offset: float = 0.0,
    target_bler: float = 0.1,
    cqi_table: int | None = None,
    mcs_table: int = 3,
    feedback_ack: bool | None = None,
    olla_ack_step_mcs: float = 0.01,
    rb_per_rbg: int = 1,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
) -> dict[str, Any]:
    """Run the agreed TDD CQI/BF-gain/OLLA MCS decision with a full audit trail.

    ``BF Gain = SINR_SVD - SINR_PMI`` under the same channel, rank, power
    allocation, noise/interference, CSI and classic MMSE receiver.  The internal
    CQI is first mapped through the confirmed discrete table, then to that MCS's
    NewTx target-BLER SINR.  RBs are first combined into RBGs in the linear domain;
    the RBG/stream deltas are then averaged arithmetically in dB.  Finally this
    *gNB-side AMC prediction* is remapped to MCS and the continuous MCS-domain OLLA
    offset is added, floored, and clipped to the table range.

    This scalar helper has no receive-side ``h_true`` observation.  It therefore
    never reports a physical BLER: the selected MCS must be paired with the actual
    receive-side codeword SINR before querying a BLER curve.
    """
    if mcs_table != 3:
        raise ValueError("TDD preset-curve adaptation currently requires mcs_table=3")
    if cqi_table is not None:
        raise ValueError(
            "cqi_table is not applicable to the confirmed internal CQI profile; "
            "omit it and use internal CQI 0..14"
        )
    if not (math.isfinite(float(target_bler)) and 0.0 < float(target_bler) < 1.0):
        raise ValueError(f"target_bler must be in (0,1), got {target_bler!r}")
    # 目标超出预置曲线覆盖范围时，深层 required_sinr_db 会抛 ValueError——
    # 全 56 条曲线的通用可用区间实测约 [0.001, 0.998]，单曲线更窄。
    mapping = internal_cqi_to_mcs(cqi_index, mcs_table=mcs_table)
    olla = float(olla_mcs_offset)
    if not math.isfinite(olla):
        raise ValueError("olla_mcs_offset must be finite")
    svd = _sinr_grid_db(svd_sinr_per_rb_db, "svd_sinr_per_rb_db")
    pmi = _sinr_grid_db(pmi_sinr_per_rb_db, "pmi_sinr_per_rb_db")
    if svd.shape != pmi.shape:
        raise ValueError(
            "SVD and PMI SINR grids must have identical [RB, layer] shape; "
            f"got {svd.shape} and {pmi.shape}"
        )

    # Pair strongest stream with strongest stream for diagnostics. The user-level mean
    # is invariant to this permutation, but the per-stream audit is easier to interpret.
    svd_order = np.argsort(-np.mean(svd, axis=0))
    pmi_order = np.argsort(-np.mean(pmi, axis=0))
    svd = svd[:, svd_order]
    pmi = pmi[:, pmi_order]
    svd_agg = codeword_sinr_db(
        svd, rb_per_rbg=rb_per_rbg, rbg_boundaries=rbg_boundaries)
    pmi_agg = codeword_sinr_db(
        pmi, rb_per_rbg=rb_per_rbg, rbg_boundaries=rbg_boundaries)
    bf_delta_rbg = (
        np.asarray(svd_agg["rbg_stream_db"], dtype=float)
        - np.asarray(pmi_agg["rbg_stream_db"], dtype=float)
    )

    initial_mcs = int(mapping["mcs"])
    initial_curve = bc.get_curve(initial_mcs, "newtx")
    cqi_mcs_sinr_db = initial_curve.required_sinr_db(target_bler)
    bf_gain_per_stream = np.mean(bf_delta_rbg, axis=0)
    bf_gain_user_db = float(np.mean(bf_gain_per_stream))
    predicted_tx_sinr_db = float(cqi_mcs_sinr_db + bf_gain_user_db)

    thresholds = [
        (m.index, bc.get_curve(m.index, "newtx").required_sinr_db(target_bler))
        for m in MCS_TABLE_3
    ]
    feasible = [
        idx for idx, threshold in thresholds
        if threshold <= predicted_tx_sinr_db
    ]
    below_mcs0 = not feasible
    mcs_after_bf = max(feasible) if feasible else MCS_TABLE_3[0].index

    olla_decision = apply_olla_mcs(
        int(mcs_after_bf), olla, mcs_table=mcs_table)
    mcs_before_floor = float(olla_decision["mcs_before_floor"])
    mcs_floored = int(olla_decision["mcs_after_floor"])
    final_mcs = int(olla_decision["final_mcs"])
    final_curve = bc.get_curve(final_mcs, "newtx")

    olla_update = (
        update_olla_mcs(
            olla, feedback_ack, target_bler=target_bler,
            ack_step_mcs=olla_ack_step_mcs,
        )
        if feedback_ack is not None else None
    )
    return {
        **mapping,
        "scheduled": True,
        "receiver": bc.data.RECEIVER_MODEL,
        "rank": int(svd.shape[1]),
        "n_rb": int(svd.shape[0]),
        "n_rbg": int(svd_agg["n_rbg"]),
        "rb_per_rbg": int(svd_agg["rb_per_rbg"]),
        "rbg_boundaries": [list(x) for x in svd_agg["rbg_boundaries"]],
        "target_bler": float(target_bler),
        "cqi_initial_mcs": initial_mcs,
        "cqi_mcs_sinr_db": round(float(cqi_mcs_sinr_db), 4),
        "pmi_stream_sinr_db": [
            round(float(x), 4) for x in pmi_agg["stream_db"]],
        "svd_stream_sinr_db": [
            round(float(x), 4) for x in svd_agg["stream_db"]],
        "bf_gain_per_stream_db": [
            round(float(x), 4) for x in bf_gain_per_stream
        ],
        "bf_gain_user_db": round(bf_gain_user_db, 4),
        "sinr_svd_gnb_db": round(float(svd_agg["user_db"]), 4),
        "sinr_pmi_gnb_db": round(float(pmi_agg["user_db"]), 4),
        "estimated_svd_stream_sinr_db": [
            round(float(cqi_mcs_sinr_db + x), 4)
            for x in bf_gain_per_stream
        ],
        # ``user_sinr_db`` is retained as a legacy alias.  It is a gNB prediction,
        # never the true receive-side SINR used for BLER.
        "user_sinr_db": round(predicted_tx_sinr_db, 4),
        "gnb_predicted_amc_sinr_db": round(predicted_tx_sinr_db, 4),
        "gnb_predicted_amc_sinr_definition": (
            "target-BLER threshold of MCS(CQI) plus gNB-visible SVD-minus-PMI BF gain"
        ),
        "actual_receive_sinr_db": None,
        "sinr_aggregation": str(svd_agg["aggregation"]),
        "stream_pairing": "descending dB-domain mean SINR",
        "bf_gain_definition": "SVD post-MMSE SINR minus PMI post-MMSE SINR",
        "mcs_after_bf": int(mcs_after_bf),
        "below_mcs0_after_bf": below_mcs0,
        "olla_mcs_offset": olla,
        "mcs_before_floor": mcs_before_floor,
        "mcs_after_floor": int(mcs_floored),
        "final_mcs": int(final_mcs),
        "mcs_clipped": final_mcs != mcs_floored,
        "olla_domain": olla_decision["olla_domain"],
        "operation_order": olla_decision["operation_order"],
        "final_mcs_required_sinr_db": round(
            final_curve.required_sinr_db(target_bler), 4
        ),
        "final_mcs_margin_to_predicted_amc_db": round(
            predicted_tx_sinr_db - final_curve.required_sinr_db(target_bler), 4
        ),
        "final_mcs_newtx_bler": None,
        "actual_bler_available": False,
        "bler_status": "unknown_without_true_receive_sinr",
        "bler_sinr_source": None,
        "olla_update": olla_update,
        "olla_next_offset_mcs": (
            olla_update["next_offset_mcs"] if olla_update is not None else olla
        ),
        "fairness_contract": (
            "same channel, CSI, rank, total/per-layer power, noise, interference and "
            "classic MMSE receiver; only precoding weight changes from PMI to SVD"
        ),
    }


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
    retx_bler: float | None
    target_bler: float
    bler_source: str
    bler_axis_source: str
    harq_model: str
    tbs_bits: int
    n_re: int
    throughput_bps: float         # 计入 BLER 与 HARQ 后的有效吞吐
    throughput_ideal_bps: float   # 不计 BLER 的名义吞吐
    cqi: int
    cqi_source: str
    se_shannon: float             # 同 SINR 下的香农谱效（上界）
    se_achieved: float            # 实际达到的谱效
    efficiency_vs_shannon: float  # 达成率
    harq_tx: float                # 平均传输次数

    def as_dict(self) -> dict[str, Any]:
        out = {
            "sinr_eff_db": round(self.sinr_eff_db, 2),
            "mcs": self.mcs_index, "modulation": self.modulation,
            "code_rate": round(self.code_rate, 4), "layers": self.layers,
            "cqi": self.cqi,
            "bler": round(self.bler, 5),
            "retx_bler": None if self.retx_bler is None else round(self.retx_bler, 5),
            "target_bler": self.target_bler,
            "bler_source": self.bler_source,
            "bler_axis_source": self.bler_axis_source,
            "harq_model": self.harq_model,
            "cqi_source": self.cqi_source,
            "tbs_bits": self.tbs_bits, "n_re": self.n_re,
            "throughput_mbps": round(self.throughput_bps / 1e6, 3),
            "throughput_ideal_mbps": round(self.throughput_ideal_bps / 1e6, 3),
            "se_shannon": round(self.se_shannon, 3),
            "se_achieved": round(self.se_achieved, 3),
            "efficiency_vs_shannon": round(self.efficiency_vs_shannon, 3),
            "harq_avg_tx": round(self.harq_tx, 3),
        }
        out["bler_note"] = (
            "Bundled preset demodulation curve; not a 3GPP reference. The source label "
            "Es/No denotes codeword-level effective SINR for a classic MMSE receiver; "
            "the curve is universal across TBS/RE/rank/scenario."
            if self.bler_source == bc.data.SOURCE_ID
            else "Finite-blocklength analytic BLER model; not measured BLER."
        )
        return out

    def text(self) -> str:
        return (
            f"有效 SINR {self.sinr_eff_db:.2f} dB → MCS {self.mcs_index}"
            f"（{self.modulation}, R={self.code_rate:.3f}）× {self.layers} 层，CQI {self.cqi}\n"
            f"  NewTx BLER {self.bler:.2%}"
            + (f" / ReTx {self.retx_bler:.2%}" if self.retx_bler is not None else "")
            + f"  TBS {self.tbs_bits} bit  "
            f"吞吐 {self.throughput_bps/1e6:.2f} Mbps（名义 {self.throughput_ideal_bps/1e6:.2f}）\n"
            f"  谱效 {self.se_achieved:.3f} vs 香农 {self.se_shannon:.3f} "
            f"bit/s/Hz —— 达成 {self.efficiency_vs_shannon:.1%}\n"
            f"  BLER 来源 {self.bler_source}，HARQ {self.harq_model}"
        )


def _bracket_aware_threshold(mdl: Any, m: Any, n_coded_bits: int,
                             target_bler: float, n_code_blocks: int
                             ) -> tuple[float, bool]:
    """目标 BLER 门限 + inclusive 标志；二分钉在括号边界时给 ±inf。

    ``BlerModel.required_sinr_db`` 在 [-40, 50] dB 内二分，且不报告穿越是否
    真的发生在括号里：目标在该档永远不可达时门限被钉在 50 dB，缓存查表路径
    会把一个**永远达不到目标 BLER** 的档选出来（逐档路径会正确拒绝）。
    钉边检测：bler(50) > target → +inf（永不满足）；bler(-40) <= target →
    −inf（恒满足）。两者与逐档循环的语义逐点一致。
    """
    hi_bl = float(mdl.bler(50.0, m, n_coded_bits, n_code_blocks)[0])
    if hi_bl > target_bler:
        return float("inf"), False
    lo_bl = float(mdl.bler(-40.0, m, n_coded_bits, n_code_blocks)[0])
    if lo_bl <= target_bler:
        return float("-inf"), True
    thr = float(mdl.required_sinr_db(m, n_coded_bits, target_bler, n_code_blocks))
    inc = bool(float(mdl.bler(thr, m, n_coded_bits, n_code_blocks)[0]) <= target_bler)
    return thr, inc


@lru_cache(maxsize=64)
def _cqi_thresholds(table: int, target_bler: float, n_coded_bits: int,
                    n_code_blocks: int) -> tuple[tuple[float, ...], tuple[bool, ...]]:
    """默认解析模型下每档 CQI 的目标 BLER SINR 门限，以及门限点本身算不算满足。

    ``inclusive`` 这一列不是多余的谨慎：``required_sinr_db`` 是二分解，
    落在门限**正上方**时 ``bler <= target`` 未必成立。逐点复刻这个边界，
    才能保证查表版与逐档求解版**在同一个输入上给同一个 CQI**。
    """
    thresholds: list[float] = []
    inclusive: list[bool] = []
    for c in CQI_TABLES[table]:
        pseudo = Mcs(c.index, c.q_m, c.r_1024, c.se)
        thr, inc = _bracket_aware_threshold(
            DEFAULT_BLER, pseudo, n_coded_bits, target_bler, n_code_blocks)
        thresholds.append(thr)
        inclusive.append(inc)
    return tuple(thresholds), tuple(inclusive)


def select_cqi(sinr_eff_db: float, *, table: int = 1, target_bler: float = 0.1,
               n_coded_bits: int = 20000, n_code_blocks: int = 1,
               model: Any | None = None) -> int:
    """按 38.214 的口径选 CQI：满足目标 BLER 的最高档。0 表示超出范围。

    **默认模型走缓存门限表。** 逐档求解析 BLER 实测 **0.81 ms/次**，
    而它在建表阶段是逐 UE、逐快照、逐 rank 调用的；门限只取决于
    ``(table, target_bler, n_coded_bits, n_code_blocks)``，算一次就够，
    查表版实测 **2.4 µs/次**。这和 :mod:`experience` 早先对 MCS 做的
    ``_select_mcs_10pct`` 是同一件事，当时漏了 CQI 这一侧。

    自定义 ``model`` 或非有限 SINR 一律回落到逐档求解的原路径——
    **加速只允许发生在能证明等价的分支上**。
    """
    if not (math.isfinite(float(target_bler)) and 0.0 < float(target_bler) < 1.0):
        raise ValueError(f"target_bler 必须在 (0,1) 内，收到 {target_bler!r}")
    mdl = model or DEFAULT_BLER
    if mdl is DEFAULT_BLER and math.isfinite(float(sinr_eff_db)):
        thr, inc = _cqi_thresholds(int(table), float(target_bler),
                                   int(n_coded_bits), int(n_code_blocks))
        s = float(sinr_eff_db)
        best = 0
        for k, c in enumerate(CQI_TABLES[table]):
            if thr[k] < s or (thr[k] == s and inc[k]):
                best = c.index
        return best
    best = 0
    for c in CQI_TABLES[table]:
        pseudo = Mcs(c.index, c.q_m, c.r_1024, c.se)
        if float(mdl.bler(sinr_eff_db, pseudo, n_coded_bits, n_code_blocks)[0]) <= target_bler:
            best = c.index
    return best


@lru_cache(maxsize=64)
def _mcs_thresholds(table: int, target_bler: float, n_coded_bits: int,
                    n_code_blocks: int
                    ) -> tuple[tuple[float, ...], tuple[bool, ...]] | None:
    """默认 BLER 后端下每档 MCS 的目标门限；算不出来就返回 ``None`` 让调用方回落。

    表 3 的 :class:`CurveBlerModel` 在目标 BLER 落在实测曲线范围之外时会抛
    ``ValueError``——那种情况下逐档循环仍有定义（谁都不满足就退回 MCS 0），
    所以这里**不能**把异常变成失败，只能放弃加速。
    """
    tbl = MCS_TABLES[table]
    mdl = _default_bler_model(table)
    thresholds: list[float] = []
    inclusive: list[bool] = []
    try:
        for m in tbl:
            thr, inc = _bracket_aware_threshold(
                mdl, m, n_coded_bits, target_bler, n_code_blocks)
            thresholds.append(thr)
            inclusive.append(inc)
    except (ValueError, KeyError):
        return None
    return tuple(thresholds), tuple(inclusive)


def select_mcs(sinr_eff_db: float, *, table: int = 1, target_bler: float = 0.1,
               n_coded_bits: int = 20000, n_code_blocks: int = 1,
               model: Any | None = None) -> Mcs:
    """选满足目标 BLER 的最高 MCS。全都不满足时退回 MCS 0（并留下高 BLER）。

    **非有限的 SINR 必须自己兜住，不能让底层抛。** nan 是能真实到达这里的：
    ChannelHub 的 ``sinr_dB`` 在被拒样本上是 nan、全零信道算出来的用户级 SINR
    也是 nan，一路传下来就崩在 BLER 曲线的有限性检查上。
    整条系统级仿真会因为一个用户的一个快照整个挂掉，
    而报的错是「sinr_db must contain only finite values」，
    完全看不出是哪个 UE 的哪个 rank。

    约定：nan / −inf → MCS 0（发不出去），+inf → 最高档。

    **默认后端走缓存门限表。** 逐档求 BLER 意味着每次查询都要评估整张表
    （表 3 是 28 条实测曲线）；建表阶段实测一次 12 UE 的调用就产生 43k 次曲线
    求值、占了 22% 的时间。门限只取决于
    ``(table, target_bler, n_coded_bits, n_code_blocks)``，算一次即可。
    自定义 ``model`` 或门限算不出来时一律回落到逐档循环。
    """
    if not (math.isfinite(float(target_bler)) and 0.0 < float(target_bler) < 1.0):
        raise ValueError(f"target_bler 必须在 (0,1) 内，收到 {target_bler!r}")
    tbl = MCS_TABLES[table]
    _s = float(sinr_eff_db)
    if _s != _s:                       # nan
        return tbl[0]
    if _s == float("-inf"):
        return tbl[0]
    if _s == float("inf"):
        return tbl[-1]
    if model is None:
        cached = _mcs_thresholds(int(table), float(target_bler),
                                 int(n_coded_bits), int(n_code_blocks))
        if cached is not None:
            thr, inc = cached
            best = tbl[0]
            for k, m in enumerate(tbl):
                if thr[k] < _s or (thr[k] == _s and inc[k]):
                    best = m
            return best
    mdl = model or _default_bler_model(table)
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
    mcs_table: int = 3,
    cqi_table: int | None = None,
    target_bler: float = 0.1,
    slot_duration_s: float = 0.5e-3,
    n_symbols: int = 12,
    esm: str = "miesm",
    max_harq_tx: int = 2,
    harq_combining: str = "ir",
    model: Any | None = None,
    retx_model: Any | None = None,
) -> LinkAdaptResult:
    """从逐 RB 的 SINR 到真实吞吐，走完整条链路到系统映射。

    步骤：逐 RB SINR → 有效 SINR（MIESM）→ 选 MCS（满足目标 BLER）→
    算 TBS（38.214 §5.1.3.2）→ 计入 BLER 与 HARQ 得有效吞吐。

    默认使用预置 256QAM profile（表 3）。表 1 的 64QAM 与表 2 的标准
    256QAM 仍可显式选择，但不会被默认路径静默触发。

    表 1/2 的 HARQ 沿用 i.i.d. BLER 简化。表 3 只允许一次重传，且所有
    BLER 都从 NewTx 曲线推导：CC 把码字 SINR 抬升 10log10(2) dB；IR（默认）
    把原 MCS 谱效减半后映射到等效低档 MCS，在不变 SINR 上查曲线。
    """
    if not (math.isfinite(float(target_bler)) and 0.0 < float(target_bler) < 1.0):
        raise ValueError(f"target_bler 必须在 (0,1) 内，收到 {target_bler!r}")
    use_preset_harq = model is None and retx_model is None and mcs_table == 3
    mdl = model or _default_bler_model(mcs_table)
    # The preset curve profile uses the versioned 256QAM CQI-row mapping; the result
    # exposes reported 4-bit codepoints. Standard MCS tables retain their 38.214 family.
    resolved_cqi_table: int | None
    if mcs_table == 3:
        resolved_cqi_table = None
    else:
        resolved_cqi_table = int(
            cqi_table if cqi_table is not None else (2 if mcs_table == 2 else 1)
        )
        if resolved_cqi_table not in CQI_TABLES:
            raise ValueError(
                f"cqi table must be one of {sorted(CQI_TABLES)}, got {cqi_table}"
            )
    if (isinstance(max_harq_tx, (bool, np.bool_))
            or not isinstance(max_harq_tx, (int, np.integer))):
        raise ValueError(f"max_harq_tx 必须是整数 1 或 2，收到 {max_harq_tx!r}")
    max_harq_tx = int(max_harq_tx)
    if max_harq_tx not in (1, 2):
        raise ValueError("SuperRAN 只允许初传 + 最多一次重传（max_harq_tx=1/2）")
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
    # 预置表 3 的接口直接给单码字 TB-BLER，不暴露 CBLER，也不允许把曲线
    # 再按物理 CB 数合成一次。表 1/2 分析后端才需要 38.212 分段数。
    n_cb = 1 if mcs_table == 3 else code_blocks(tbs0, mcs.rate)[0]
    mcs = select_mcs(eff, table=mcs_table, target_bler=target_bler,
                     n_coded_bits=n_coded, n_code_blocks=n_cb, model=mdl)
    n_coded = n_re * mcs.q_m * n_layers

    tbs = transport_block_size(n_re, mcs.rate, mcs.q_m, n_layers)
    n_cb = 1 if mcs_table == 3 else code_blocks(tbs, mcs.rate)[0]
    bler = float(mdl.bler(eff, mcs, n_coded, n_cb)[0])

    # 预置表 3 的系统口径只用 NewTx 曲线；原始 ReTx 行继续保留作来源审计，
    # 但不进入当前 HARQ 计算。表 1/2 仍保留可注入的研究模型。
    if use_preset_harq and max_harq_tx == 2:
        retx_eval = harq_retransmission_bler(
            mcs.index, eff, combining=harq_combining, table=3)
        retx_bler = float(retx_eval["bler"])
        p_fail_final = bler * retx_bler
        avg_tx = 1.0 + bler
        harq_model = (
            f"one_retransmission_{str(harq_combining).lower()}_derived_from_newtx")
    elif max_harq_tx == 1:
        retx_bler = None
        p_fail_final = bler
        avg_tx = 1.0
        harq_model = "newtx_only"
    elif retx_model is not None:
        resolved_retx_model = retx_model
        retx_bler = float(resolved_retx_model.bler(eff, mcs, n_coded, n_cb)[0])
        p_fail_final = bler * retx_bler ** (max_harq_tx - 1)
        avg_tx = 1.0 + bler * sum(retx_bler ** k for k in range(max_harq_tx - 1))
        harq_model = "one_retransmission_explicit_retx_model"
    else:
        retx_bler = None
        p_fail_final = bler ** max_harq_tx
        avg_tx = sum(bler ** k for k in range(max_harq_tx))
        harq_model = "one_retransmission_iid_same_bler"
    tput_ideal = tbs / slot_duration_s
    tput = tbs * (1.0 - p_fail_final) / (avg_tx * slot_duration_s)

    # 谱效按 RE 口径算（bit per RE），避免"带宽"定义上的歧义：
    # 保护带、DM-RS 开销算不算，不同口径能差 10%。
    se_achieved = tbs * (1.0 - p_fail_final) / (avg_tx * n_re)
    se_shannon = float(np.mean(np.log2(1.0 + 10.0 ** (g / 10.0)))) * n_layers

    bler_source = getattr(mdl, "source_id", "analytic_finite_blocklength")
    bler_axis_source = (
        f"{bc.data.SOURCE_AXIS_NAME}; receiver={bc.data.RECEIVER_MODEL}"
        if bler_source == bc.data.SOURCE_ID else "effective SINR from MIESM/EESM"
    )
    cqi_source = INTERNAL_CQI_SOURCE if mcs_table == 3 else bler_source
    cqi_value = (
        select_reported_cqi(eff, target_bler=target_bler, mcs_table=3)
        if mcs_table == 3
        else select_cqi(
            eff, table=int(resolved_cqi_table), target_bler=target_bler,
            n_coded_bits=n_coded, n_code_blocks=n_cb, model=mdl,
        )
    )

    return LinkAdaptResult(
        sinr_eff_db=eff, mcs_index=mcs.index, modulation=_MOD_NAME[mcs.q_m],
        code_rate=mcs.rate, se_mcs=mcs.se, layers=n_layers, bler=bler,
        retx_bler=retx_bler, target_bler=float(target_bler),
        bler_source=bler_source, bler_axis_source=bler_axis_source,
        harq_model=harq_model,
        tbs_bits=tbs, n_re=n_re, throughput_bps=tput,
        throughput_ideal_bps=tput_ideal,
        cqi=int(cqi_value),
        cqi_source=cqi_source,
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
    mean_retx_bler: float | None = None
    outage_ratio: float = 0.0  # 连 MCS 0 都达不到目标 BLER 的比例
    bler_source: str = "analytic_finite_blocklength"
    bler_axis_source: str = "effective SINR from MIESM/EESM"
    harq_model: str = "one_retransmission_iid_same_bler"

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
            "mean_retx_bler": (
                None if self.mean_retx_bler is None else round(self.mean_retx_bler, 4)
            ),
            "outage_ratio": round(self.outage_ratio, 4),
            "bler_source": self.bler_source,
            "bler_axis_source": self.bler_axis_source,
            "harq_model": self.harq_model,
            "mcs_distribution": {int(k): int(v) for k, v in
                                 sorted(self.mcs_distribution.items())},
            "note": self._note(),
        }

    def _note(self) -> str:
        base = "5% 分位是 3GPP 评估里的边缘用户指标，比均值更能反映公平性。"
        if self.bler_source == bc.data.SOURCE_ID:
            return (
                base + "BLER 来自预置 20B NewTx/ReTx 解调曲线，不是 3GPP "
                "标准曲线；源标签 Es/No 表示经典 MMSE 接收机 SINR。"
            )
        return base + "BLER 来自有限码长分析模型而非实测，见 linkadapt 模块文档。"

    def text(self) -> str:
        return (
            f"吞吐（n={self.n}）：均值 {self.mean_mbps:.2f} / 中位 {self.median_mbps:.2f} / "
            f"边缘用户(5%) {self.cell_edge_mbps:.2f} / 峰值(95%) {self.peak_mbps:.2f} Mbps\n"
            f"  谱效 均值 {self.mean_se:.3f}，边缘 {self.cell_edge_se:.3f} bit/s/Hz\n"
            f"  平均 BLER {self.mean_bler:.2%}，中断比例 {self.outage_ratio:.2%}\n"
            f"  MCS 分布 {dict(sorted(self.mcs_distribution.items()))}\n"
            f"  BLER 来源 {self.bler_source}，HARQ {self.harq_model}"
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
    retx = [r.retx_bler for r in results if r.retx_bler is not None]
    sources = {r.bler_source for r in results}
    axes = {r.bler_axis_source for r in results}
    harq_models = {r.harq_model for r in results}
    return ThroughputStats(
        n=len(results),
        mean_mbps=float(t.mean()), median_mbps=float(np.median(t)),
        cell_edge_mbps=float(np.percentile(t, 5)),
        peak_mbps=float(np.percentile(t, 95)),
        mean_se=float(se.mean()), cell_edge_se=float(np.percentile(se, 5)),
        mcs_distribution=dist,
        mean_bler=float(np.mean([r.bler for r in results])),
        mean_retx_bler=float(np.mean(retx)) if retx else None,
        outage_ratio=float(np.mean([
            r.mcs_index == 0 and r.bler > r.target_bler for r in results
        ])),
        bler_source=next(iter(sources)) if len(sources) == 1 else "mixed",
        bler_axis_source=next(iter(axes)) if len(axes) == 1 else "mixed",
        harq_model=next(iter(harq_models)) if len(harq_models) == 1 else "mixed",
    )
