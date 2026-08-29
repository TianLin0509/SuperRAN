"""本地硬件与载波配置：64T 1驱3（默认）+ 可选 256T 1驱6 AAU。

这个文件是**默认信道配置的唯一真相源**。早期实现一直走 ChannelHub
的 ``antenna_model_mode="legacy_64"``——把 64 个端口当成 64 个**独立**阵元、
间距一律 0.5λ。真实硬件不是这样：

===============================  ==========================================
项                                已确认预置 AAU
===============================  ==========================================
RF / 数字端口                      64T: 8H x 4V x 2pol；256T: 16H x 8V x 2pol
物理阵子                           64T: 192；256T: 1536
馈电                              64T 每端口 1 驱 3；256T 每端口 1 驱 6
水平阵子间距                       **0.5λ**
垂直阵子间距                       **0.67λ**（不是 0.5λ）
RF 端口垂直相位中心间距             64T: 2.01λ；256T: 4.02λ
端口展平                           ``pol_h_v + top_to_bottom``（两者统一）
===============================  ==========================================

ChannelHub 的 ``phy_sim/effective_array.py`` 就是照这套硬件写的
（模块文档里 "Target AAU" 一节逐条对得上），只是默认没启用。启用它要两件事：
``antenna_model_mode="effective_subarray"`` 加一个 ``bs_antenna`` 配置块。

**历史实测证据**（旧内核、同 seed、64T/4R、2.6 GHz、272 RB）：

* ``h_serving_true`` 与 legacy 的**相对差 4.03**——完全是另一个信道。
  所有从信道算出来的量（预编码、谱效、吞吐、CSI 压缩）都跟着变。
* ``effective_subarray`` 与 ``physical_reference``（真跑物理阵子再用 F 投影）
  的相对差曾为 **4.8e-7**。当前版本仍必须由数值门重新验证，不能把历史误差
  当永久承诺。
* **数字 BF 与大尺度链路预算分层**。当前 ChannelHub 的 conducted-power 预算会
  读取阵元方向图、固定 1 驱 3 子阵、垂直几何与电下倾，所以它们会改变服务/邻区
  接收功率及几何 SNR/SIR/SINR；64 端口数字预编码增益则刻意留在 ``H`` 中，
  由 link-level 只计算一次。旧结论“阵列模型完全不影响几何量”已经作废。

垂直 0.67λ 这个数是用户实测纠正过的值（早期按 0.5λ 算，全盘产物失真），
见记忆 ``project_reconfig_mimo_sim`` 的方法论教训第 4 条。**别改回 0.5。**

--- 载波与带宽 ---------------------------------------------------------

面向 5G，n41 频段 2.6 GHz、30 kHz 子载波间隔、100 MHz 带宽。

RB 数用 **272**（17 个 RBG x 每 RBG 16 个 RB）。注意 3GPP 38.104 的标准表
在 100 MHz / 30 kHz 下给的是 **273**——272 是按 RBG 对齐取的整数倍，
差的那 1 个 RB 在 RBG 划分里本来就是残块。两个数都对，只是口径不同，
所以这里显式写死 272 而不是让它走标准表。

仿真粒度到 **RB 为止**：每个 RB 有 12 个子载波（RE），但 RE 级建模的复杂度
换不来系统级结论的精度，所以信道矩阵的频率轴就是 272 个 RB。
"""
from __future__ import annotations

from typing import Any

# --- 阵列 -----------------------------------------------------------------

COMPANY_RF_PANEL: list[int] = [8, 4, 2]          # 默认 64T：N_H, N_V, N_pol
COMPANY_UE_PANEL: list[int] = [2, 1, 2]          # 暂定 2H x 1V x 2pol -> 4R
COMPANY_ELEMENTS_PER_PORT = 3                     # 1 驱 3
# 64T/256T 统一的产品端口编号合同：先极化块，再水平列，最后垂直行；
# v=0 在物理顶部。新模块只能引用这两个 canonical 常量。旧 64T 顺序仅用于
# 读取历史数据，不能再成为新配置的默认值。
COMPANY_CANONICAL_PORT_ORDER = "pol_h_v"
COMPANY_CANONICAL_VERTICAL_INDEX_ORDER = "top_to_bottom"
COMPANY_LEGACY_64T_PORT_ORDER = "h_v_pol"
COMPANY_LEGACY_64T_VERTICAL_INDEX_ORDER = "bottom_to_top"
COMPANY_PORT_ORDER = COMPANY_CANONICAL_PORT_ORDER
COMPANY_VERTICAL_INDEX_ORDER = COMPANY_CANONICAL_VERTICAL_INDEX_ORDER
COMPANY_256T_RF_PANEL: list[int] = [16, 8, 2]     # 图示 256T
COMPANY_256T_ELEMENTS_PER_PORT = 6                # 每个垂直 T 后接 1 驱 6
COMPANY_256T_PORT_ORDER = COMPANY_CANONICAL_PORT_ORDER
COMPANY_256T_VERTICAL_INDEX_ORDER = COMPANY_CANONICAL_VERTICAL_INDEX_ORDER
COMPANY_H_SPACING_LAMBDA = 0.5
COMPANY_V_SPACING_LAMBDA = 0.67                   # 实测值，别改回 0.5
DEFAULT_ELECTRICAL_DOWNTILT_DEG = 6.0              # 工程基线；用户可在配置中覆盖
COMPANY_NUM_PORTS = COMPANY_RF_PANEL[0] * COMPANY_RF_PANEL[1] * COMPANY_RF_PANEL[2]
COMPANY_NUM_ELEMENTS = COMPANY_NUM_PORTS * COMPANY_ELEMENTS_PER_PORT   # 192
COMPANY_256T_NUM_PORTS = (
    COMPANY_256T_RF_PANEL[0] * COMPANY_256T_RF_PANEL[1] * COMPANY_256T_RF_PANEL[2]
)
COMPANY_256T_NUM_ELEMENTS = (
    COMPANY_256T_NUM_PORTS * COMPANY_256T_ELEMENTS_PER_PORT
)  # 1536

# --- 载波 -----------------------------------------------------------------

COMPANY_CARRIER_HZ = 2.6e9        # n41
COMPANY_SCS_HZ = 30_000
COMPANY_BANDWIDTH_HZ = 100e6
COMPANY_NUM_RBG = 17
COMPANY_RB_PER_RBG = 16
COMPANY_NUM_RB = COMPANY_NUM_RBG * COMPANY_RB_PER_RBG   # 272
COMPANY_SC_PER_RB = 12            # 只作记录，仿真到 RB 为止
NR_TABLE_NUM_RB_100M_30K = 273    # 38.104 标准表值，与上面的 272 口径不同
# 系统信道快照默认每5 ms更新。它与0.5-ms NR slot、两条2T SRS腿的5-ms
# 间隔、10-ms四端口SRS周期是三套不同的时钟，必须独立保存。
COMPANY_SNAPSHOT_INTERVAL_S = 5e-3
# 系统/体验层的 TDD 资源格栅是项目合同，不是页面参数。
# 链路级仍可生成 10/20 MHz 等数据用于算法实验；但这些数据
# 不能直接送入当前的 TDD 系统仿真并冒充预置 100 MHz 基线。
SUPERRAN_TDD_CARRIER_PROFILE_ID = "superran-tdd-100m-30khz-272rb-17x16-v1"

# 当前唯一受支持的 SRS hopping 产品合同。参数来自 38.211
# Table 6.4.1.4.3-1 的 C_SRS=63 行；SuperRAN 只承载预置 100 MHz
# 载波上 B_SRS=1、b_hop=0、n_RRC=0 这一档。未来扩展其他带宽时必须
# 新增独立 profile，而不是复用这条 17-hop 顺序。
COMPANY_SRS_C_SRS = 63
COMPANY_SRS_B_SRS = 1
COMPANY_SRS_B_HOP = 0
COMPANY_SRS_N_RRC = 0
COMPANY_SRS_CONFIGURED_CS = 4
COMPANY_SRS_TX_PORTS_PER_OCCASION = 2
COMPANY_SRS_LOGICAL_ANTENNA_PORTS = 4
COMPANY_SRS_RESOURCES_PER_UE = 2
COMPANY_SRS_17_HOP_ORDER_RBG = (
    0, 8, 16, 7, 15, 6, 14, 5, 13, 4, 12, 3, 11, 2, 10, 1, 9,
)
SUPERRAN_SRS_HOPPING_PROFILE_ID = "superran-srs-c63-b1-17hop-2t4r-4cs-v2"

# --- 收发 -----------------------------------------------------------------

COMPANY_UE_RX_ANT = 4             # 默认 4R 接收
# 数据张量仍需保存4个UE天线端口，才能形成完整64x4互易信道；这不代表终端
# 有4条同时工作的Tx RF链。产品基线是2T4R：一次SRS只发2 ports，下一可用
# SRS机会切到另两根天线。这里的4是“待探测逻辑天线端口数”。
COMPANY_UE_TX_ANT = 4
COMPANY_UE_SIMULTANEOUS_TX = 2
COMPANY_LINK = "BOTH"             # DL 真值 + UL SRS 估计，TDD 成对生成

# 预置面板采用交叉极化 +45/-45 度。即便 ChannelHub 当前也把它作为双极化
# 默认值，这里仍要显式落盘：硬件真相不能依赖下游库某个可能漂移的默认参数。
COMPANY_POLARIZATION_SLANTS_DEG: list[float] = [45.0, -45.0]

_SUPPORTED_PORT_ORDERS = {
    COMPANY_CANONICAL_PORT_ORDER,
    COMPANY_LEGACY_64T_PORT_ORDER,
}
_SUPPORTED_VERTICAL_INDEX_ORDERS = {
    COMPANY_CANONICAL_VERTICAL_INDEX_ORDER,
    COMPANY_LEGACY_64T_VERTICAL_INDEX_ORDER,
}


def port_flat_index(
    h: int,
    v: int,
    p: int,
    *,
    n_h: int,
    n_v: int,
    n_p: int,
    port_order: str = COMPANY_CANONICAL_PORT_ORDER,
) -> int:
    """Return the flat RF-port index under the declared layout contract.

    New code should normally omit ``port_order`` and therefore use the shared
    ``pol_h_v`` contract.  ``h_v_pol`` exists only at an explicit historical
    64T compatibility boundary.
    """
    dims = (int(n_h), int(n_v), int(n_p))
    coords = (int(h), int(v), int(p))
    if any(size < 1 for size in dims):
        raise ValueError(f"port dimensions must be positive, got {dims}")
    if not all(0 <= value < size for value, size in zip(coords, dims, strict=True)):
        raise IndexError(f"port coordinate {coords} outside dimensions {dims}")
    if port_order not in _SUPPORTED_PORT_ORDERS:
        raise ValueError(
            f"port_order {port_order!r} not in {sorted(_SUPPORTED_PORT_ORDERS)}"
        )
    if port_order == COMPANY_LEGACY_64T_PORT_ORDER:
        return (coords[0] * dims[1] + coords[1]) * dims[2] + coords[2]
    return coords[2] * (dims[0] * dims[1]) + coords[0] * dims[1] + coords[1]


def type1_to_port_permutation(
    n_h: int,
    n_v: int,
    n_p: int = 2,
    *,
    port_order: str = COMPANY_CANONICAL_PORT_ORDER,
    vertical_index_order: str = COMPANY_CANONICAL_VERTICAL_INDEX_ORDER,
) -> list[int]:
    """Map 38.214 Type-I row indices into a channel's RF-port order.

    The returned list obeys ``perm[type1_index] = channel_port_index``.
    Type-I rows are polarization blocks with ``v`` outer / ``h`` inner::

        s_type1 = p * (N_V * N_H) + v * N_H + h

    The vertical-order argument is validated and carried at the boundary even
    though this row permutation uses the layout's *logical* ``v``.  A physical
    top/bottom reversal belongs to the separate channel-layout migration, not
    to codebook row reindexing.  Keeping this tiny implementation in SuperRAN
    prevents offline ``Dataset.pmi()`` from acquiring a runtime dependency on
    the MSG source tree.
    """
    dims = (int(n_h), int(n_v), int(n_p))
    if any(size < 1 for size in dims):
        raise ValueError(f"port dimensions must be positive, got {dims}")
    if vertical_index_order not in _SUPPORTED_VERTICAL_INDEX_ORDERS:
        raise ValueError(
            "vertical_index_order "
            f"{vertical_index_order!r} not in {sorted(_SUPPORTED_VERTICAL_INDEX_ORDERS)}"
        )
    total = dims[0] * dims[1] * dims[2]
    perm = [0] * total
    for p in range(dims[2]):
        for v in range(dims[1]):
            for h in range(dims[0]):
                source = p * (dims[1] * dims[0]) + v * dims[0] + h
                perm[source] = port_flat_index(
                    h,
                    v,
                    p,
                    n_h=dims[0],
                    n_v=dims[1],
                    n_p=dims[2],
                    port_order=port_order,
                )
    return perm


def company_antenna_block(
    *,
    carrier_freq_hz: float = COMPANY_CARRIER_HZ,
    fixed_downtilt_deg: float = DEFAULT_ELECTRICAL_DOWNTILT_DEG,
    profile: str = "64t",
) -> dict[str, Any]:
    """ChannelHub ``bs_antenna`` 配置块（64T/1驱3 或 256T/1驱6）。

    ``fixed_downtilt_deg`` 是**馈电网络内部**的固定电下倾，正值把主瓣压到
    水平面以下。它做在子阵内部，所有端口共用同一套馈电，改它等于换硬件
    校准版本——所以 ``calibration_id`` 跟着带上。
    """
    profile_key = str(profile).strip().lower()
    if profile_key in {"64", "64t", "company_64t"}:
        m = COMPANY_ELEMENTS_PER_PORT
        port_order = COMPANY_PORT_ORDER
        profile_id = "company-64T-1to3-192ae-pol-h-v-top-down-v2"
    elif profile_key in {"256", "256t", "company_256t"}:
        m = COMPANY_256T_ELEMENTS_PER_PORT
        port_order = COMPANY_256T_PORT_ORDER
        profile_id = "company-256T-1to6-1536ae-pol-h-v-top-down-v1"
    else:
        raise ValueError(f"unknown company antenna profile {profile!r}")

    # 下倾进入 F 的复馈电权，所以不同下倾必须有不同 calibration_id。
    # 用纯 ASCII、文件名安全的短表示，避免 6 与 6.0 被误判成两个校准版本。
    tilt_tag = f"{float(fixed_downtilt_deg):.6f}".rstrip("0").rstrip(".")
    tilt_tag = tilt_tag.replace("-", "m").replace(".", "p")
    calibration_id = f"{profile_id}-dt{tilt_tag}deg"

    return {
        "port_order": port_order,
        "vertical_index_order": COMPANY_CANONICAL_VERTICAL_INDEX_ORDER,
        "horizontal_port_spacing_lambda": COMPANY_H_SPACING_LAMBDA,
        "reference_frequency_hz": float(carrier_freq_hz),
        "element_pattern": {
            # parametric_temporary 是 3GPP 式的抛物线幅度模型，**不是实测方向图**。
            # ChannelHub 会把这一点写进 element_pattern_is_measured=False，
            # 对外说明时不能说成实测。
            "source": "parametric_temporary",
            # 用户给出的产品先验：水平 3 dB 波宽约 110°。ChannelHub 当前
            # 用可配置的 3GPP 式抛物线 dB 包络实现，尚不是实测 cos/Jones 表。
            "horizontal_hpbw_deg": 110.0,
            "vertical_hpbw_deg": 65.0,
            "peak_gain_dbi": 8.0,
            "xpd_db": 8.0,
            "polarization_slant_angles_deg": list(COMPANY_POLARIZATION_SLANTS_DEG),
        },
        "fixed_vertical_subarray": {
            "elements_per_rf_port": m,
            "ae_vertical_spacing_lambda": COMPANY_V_SPACING_LAMBDA,
            "calibration_vertical_index_order": (
                COMPANY_CANONICAL_VERTICAL_INDEX_ORDER
            ),
            "fixed_downtilt_deg": float(fixed_downtilt_deg),
            "calibration_id": calibration_id,
        },
    }


def is_company_panel(panel: Any) -> bool:
    """这个面板是不是已确认物理结构的预置 64T 或 256T。

    64T 使用 1 驱 3、256T 使用 1 驱 6；二者统一采用图示
    ``pol_h_v + top_to_bottom``。其他端口数不推断馈电结构。
    """
    try:
        p = [int(x) for x in panel]
    except (TypeError, ValueError):
        return False
    return p in (COMPANY_RF_PANEL, COMPANY_256T_RF_PANEL)


def company_profile_for_panel(panel: Any) -> str | None:
    """Return ``64t``/``256t`` only for physically confirmed panels."""
    try:
        p = [int(x) for x in panel]
    except (TypeError, ValueError):
        return None
    if p == COMPANY_RF_PANEL:
        return "64t"
    if p == COMPANY_256T_RF_PANEL:
        return "256t"
    return None


def apply_array_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """给配置补上真实阵列模型。**就地修改并返回同一个 dict。**

    只在三个条件都满足时才动手：

    1. 调用方没有显式指定 ``antenna_model_mode``（显式指定的一律尊重）；
    2. 面板是已确认的 64T/8x4x2 或 256T/16x8x2；
    3. 没有已存在的 ``bs_antenna`` 块。

    返回的 dict 里带一个 ``_array_defaults_applied`` 标记供上层记录；
    这个键在下发给 ChannelHub 之前会被 :func:`strip_markers` 摘掉。
    """
    if cfg.get("antenna_model_mode"):
        cfg["_array_defaults_applied"] = "explicit"
        return cfg
    panel = cfg.get("bs_panel")
    profile = company_profile_for_panel(panel)
    if profile is None:
        cfg["_array_defaults_applied"] = "skipped_unconfirmed_array"
        return cfg
    if cfg.get("bs_antenna"):
        cfg["_array_defaults_applied"] = "explicit_bs_antenna"
        return cfg

    cfg["antenna_model_mode"] = "effective_subarray"
    cfg["bs_antenna"] = company_antenna_block(
        carrier_freq_hz=float(cfg.get("carrier_freq_hz") or COMPANY_CARRIER_HZ),
        profile=profile,
    )
    cfg["_array_defaults_applied"] = (
        "company_1to3_192ae" if profile == "64t" else "company_256t_1to6_1536ae"
    )
    return cfg


def strip_markers(cfg: dict[str, Any]) -> str | None:
    """摘掉内部标记键并返回它的值。ChannelHub 不认识这个键。"""
    return cfg.pop("_array_defaults_applied", None)


def array_summary(cfg: dict[str, Any], applied: str | None) -> dict[str, Any]:
    """给 summary.json 用的阵列口径说明。"""
    mode = cfg.get("antenna_model_mode", "legacy_64")
    out: dict[str, Any] = {
        "antenna_model_mode": mode,
        "applied": applied,
        "bs_panel": list(cfg.get("bs_panel") or []),
        "polarization_slant_angles_deg": list(
            ((cfg.get("bs_antenna") or {}).get("element_pattern") or {}).get(
                "polarization_slant_angles_deg", COMPANY_POLARIZATION_SLANTS_DEG
            )
        ) if mode == "effective_subarray" else None,
    }
    if mode == "legacy_64":
        n_ports = 1
        if cfg.get("bs_panel"):
            n_ports = int(cfg["bs_panel"][0]) * int(cfg["bs_panel"][1]) * int(cfg["bs_panel"][2])
        out["port_order"] = COMPANY_LEGACY_64T_PORT_ORDER
        out["vertical_index_order"] = COMPANY_LEGACY_64T_VERTICAL_INDEX_ORDER
        out["port_layout_contract_version"] = "explicit-legacy-layout-v1"
        out["note"] = (
            f"{n_ports} 个端口按**独立阵元**建模、间距一律 0.5λ。"
            "这是显式历史兼容/对照模式，不是已确认的预置 64T/256T 馈电结构。"
        )
        return out
    ant = cfg.get("bs_antenna") or {}
    sub = ant.get("fixed_vertical_subarray") or {}
    elem = ant.get("element_pattern") or {}
    m = int(sub.get("elements_per_rf_port", COMPANY_ELEMENTS_PER_PORT))
    dv = float(sub.get("ae_vertical_spacing_lambda", COMPANY_V_SPACING_LAMBDA))
    resolved_port_order = ant.get("port_order", COMPANY_CANONICAL_PORT_ORDER)
    resolved_vertical_order = ant.get(
        "vertical_index_order", COMPANY_CANONICAL_VERTICAL_INDEX_ORDER
    )
    if (
        resolved_port_order == COMPANY_CANONICAL_PORT_ORDER
        and resolved_vertical_order == COMPANY_CANONICAL_VERTICAL_INDEX_ORDER
    ):
        layout_version = "pol_h_v-top_to_bottom-v1"
    elif (
        resolved_port_order == COMPANY_LEGACY_64T_PORT_ORDER
        and resolved_vertical_order == COMPANY_LEGACY_64T_VERTICAL_INDEX_ORDER
    ):
        layout_version = "h_v_pol-bottom_to_top-legacy-v1"
    else:
        layout_version = f"{resolved_port_order}-{resolved_vertical_order}-custom-v1"
    out.update({
        "elements_per_rf_port": m,
        "physical_elements": (
            int(cfg["bs_panel"][0]) * int(cfg["bs_panel"][1]) * int(cfg["bs_panel"][2]) * m
            if cfg.get("bs_panel") else None
        ),
        "horizontal_spacing_lambda": float(
            ant.get("horizontal_port_spacing_lambda", COMPANY_H_SPACING_LAMBDA)
        ),
        "ae_vertical_spacing_lambda": dv,
        "rf_vertical_spacing_lambda": round(m * dv, 4),
        "fixed_downtilt_deg": float(sub.get("fixed_downtilt_deg", 0.0)),
        "calibration_id": sub.get("calibration_id"),
        "calibration_vertical_index_order": sub.get(
            "calibration_vertical_index_order",
            ant.get(
                "vertical_index_order", COMPANY_CANONICAL_VERTICAL_INDEX_ORDER
            ),
        ),
        "element_pattern_is_measured": False,
        "element_horizontal_hpbw_deg": float(elem.get("horizontal_hpbw_deg", 110.0)),
        "element_vertical_hpbw_deg": float(elem.get("vertical_hpbw_deg", 65.0)),
        "element_pattern_source": elem.get("source", "parametric_temporary"),
        "port_order": resolved_port_order,
        "vertical_index_order": resolved_vertical_order,
        "port_layout_contract_version": layout_version,
        "note": (
            f"真实 AAU：1 驱 {m}、{out.get('physical_elements')} 物理阵子、"
            "水平 0.5λ / 垂直 0.67λ。"
            "阵元方向图与固定子阵增益会进入 conducted-power 几何预算；"
            "数字预编码增益仍留在 H 中，由链路级只计算一次。"
            "阵元方向图是 3GPP 式参数化模型，不是实测方向图。"
        ),
    })
    return out


# --- 全套默认 -------------------------------------------------------------

def company_carrier_defaults() -> dict[str, Any]:
    """载波侧默认值。只在调用方没写的键上生效。"""
    return {
        "carrier_freq_hz": COMPANY_CARRIER_HZ,
        "subcarrier_spacing": COMPANY_SCS_HZ,
        "bandwidth_hz": COMPANY_BANDWIDTH_HZ,
        "num_rb": COMPANY_NUM_RB,
        "num_bs_tx_ant": COMPANY_NUM_PORTS,
        "num_bs_rx_ant": COMPANY_NUM_PORTS,
        "num_ue_rx_ant": COMPANY_UE_RX_ANT,
        "num_ue_tx_ant": COMPANY_UE_TX_ANT,
        "bs_panel": list(COMPANY_RF_PANEL),
        # 这里只是把原先隐式的 4 阵元 ULA 改成明确且可覆盖的工程假设。
        # 尚无终端阵列实测参数，不能把它描述成真实手机天线。
        "ue_panel": list(COMPANY_UE_PANEL),
        "link": COMPANY_LINK,
    }


def describe() -> dict[str, Any]:
    """人可读的默认配置说明，给 sr_capabilities / 文档用。"""
    return {
        "array": {
            "rf_ports": COMPANY_NUM_PORTS,
            "rf_shape": f"{COMPANY_RF_PANEL[0]}H x {COMPANY_RF_PANEL[1]}V x "
                        f"{COMPANY_RF_PANEL[2]}pol",
            "physical_elements": COMPANY_NUM_ELEMENTS,
            "feed": f"1 驱 {COMPANY_ELEMENTS_PER_PORT}（每端口驱动垂直相邻 "
                    f"{COMPANY_ELEMENTS_PER_PORT} 个阵子）",
            "horizontal_spacing_lambda": COMPANY_H_SPACING_LAMBDA,
            "ae_vertical_spacing_lambda": COMPANY_V_SPACING_LAMBDA,
            "rf_vertical_spacing_lambda": round(
                COMPANY_ELEMENTS_PER_PORT * COMPANY_V_SPACING_LAMBDA, 4),
            "grating_lobe": "RF 端口垂直间距 2.01λ > λ，垂直方向有栅瓣",
            "port_order": COMPANY_PORT_ORDER,
            "vertical_index_order": COMPANY_VERTICAL_INDEX_ORDER,
            "drawing_formula_1based": "p*32 + h*4 + v + 1",
            "port_layout_contract_version": "pol_h_v-top_to_bottom-v1",
            "legacy_64t_compatibility": {
                "port_order": COMPANY_LEGACY_64T_PORT_ORDER,
                "vertical_index_order": COMPANY_LEGACY_64T_VERTICAL_INDEX_ORDER,
                "policy": "只读取历史数据；新生成与新模块不得使用",
            },
        },
        "optional_arrays": {
            "company_256t": {
                "rf_ports": COMPANY_256T_NUM_PORTS,
                "rf_shape": "16H x 8V x 2pol",
                "physical_elements": COMPANY_256T_NUM_ELEMENTS,
                "feed": "每个垂直 T 固定 1 驱 6",
                "port_order": COMPANY_256T_PORT_ORDER,
                "vertical_index_order": COMPANY_256T_VERTICAL_INDEX_ORDER,
                "drawing_formula_1based": "p*128 + h*8 + v + 1",
                "horizontal_spacing_lambda": COMPANY_H_SPACING_LAMBDA,
                "ae_vertical_spacing_lambda": COMPANY_V_SPACING_LAMBDA,
                "rf_vertical_spacing_lambda": round(
                    COMPANY_256T_ELEMENTS_PER_PORT * COMPANY_V_SPACING_LAMBDA, 4
                ),
            }
        },
        "carrier": {
            "band": "n41",
            "carrier_freq_hz": COMPANY_CARRIER_HZ,
            "subcarrier_spacing_hz": COMPANY_SCS_HZ,
            "bandwidth_hz": COMPANY_BANDWIDTH_HZ,
            "num_rb": COMPANY_NUM_RB,
            "rbg_layout": f"{COMPANY_NUM_RBG} RBG x {COMPANY_RB_PER_RBG} RB",
            "nr_table_num_rb": NR_TABLE_NUM_RB_100M_30K,
            "num_rb_note": (
                f"272 = {COMPANY_NUM_RBG} x {COMPANY_RB_PER_RBG}（按 RBG 对齐）；"
                f"38.104 标准表在 100 MHz/30 kHz 下是 {NR_TABLE_NUM_RB_100M_30K}。"
                "两个数口径不同，这里显式用 272。"
            ),
            "granularity": f"仿真到 RB 为止（每 RB {COMPANY_SC_PER_RB} 个子载波，不建模到 RE）",
        },
        "link": {
            "direction": COMPANY_LINK,
            "ue_rx_ant": COMPANY_UE_RX_ANT,
            "ue_tx_ant": COMPANY_UE_TX_ANT,
            "ue_panel": list(COMPANY_UE_PANEL),
            "ue_panel_assumption": (
                "暂按 2H x 1V x 2pol 的 4R 面板建模；这是可配置的工程假设，"
                "不是终端阵列实测值"
            ),
        },
    }
