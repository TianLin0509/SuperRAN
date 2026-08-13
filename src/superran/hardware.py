"""本地硬件与载波配置：64T 1驱3（默认）+ 可选 256T 1驱6 AAU。

这个文件是**默认信道配置的唯一真相源**。早期实现一直走 ChannelHub
的 ``antenna_model_mode="legacy_64"``——把 64 个端口当成 64 个**独立**阵元、
间距一律 0.5λ。真实硬件不是这样：

===============================  ==========================================
项                                真实 AAU
===============================  ==========================================
RF / 数字端口                      8H x 4V x 2pol = **64**
物理阵子                           8H x 12V x 2pol = **192**
馈电                              每个 RF 端口固定驱动同一 (h, pol) 列上
                                  **垂直相邻的 3 个阵子**（1 驱 3）
水平阵子间距                       **0.5λ**
垂直阵子间距                       **0.67λ**（不是 0.5λ）
RF 端口垂直相位中心间距             3 x 0.67 = **2.01λ**（> λ，有栅瓣）
===============================  ==========================================

ChannelHub 的 ``phy_sim/effective_array.py`` 就是照这套硬件写的
（模块文档里 "Target AAU" 一节逐条对得上），只是默认没启用。启用它要两件事：
``antenna_model_mode="effective_subarray"`` 加一个 ``bs_antenna`` 配置块。

**实测影响**（同 seed、64T/4R、2.6 GHz、272 RB）：

* ``h_serving_true`` 与 legacy 的**相对差 4.03**——完全是另一个信道。
  所有从信道算出来的量（预编码、谱效、吞吐、CSI 压缩）都跟着变。
* ``effective_subarray`` 与 ``physical_reference``（真跑 192 阵子再用 F 投影）
  的相对差 **4.8e-7**——快路径复现了参考路径，可以放心用快的。
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
COMPANY_PORT_ORDER = "h_v_pol"
COMPANY_256T_RF_PANEL: list[int] = [16, 8, 2]     # 图示 256T
COMPANY_256T_ELEMENTS_PER_PORT = 6                # 每个垂直 T 后接 1 驱 6
COMPANY_256T_PORT_ORDER = "pol_h_v"               # pol block, h, v fastest
COMPANY_256T_VERTICAL_INDEX_ORDER = "top_to_bottom"  # 图中 1 在上、8 在下
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

# --- 收发 -----------------------------------------------------------------

COMPANY_UE_RX_ANT = 4             # 默认 4R 接收
# 体验仿真的下行预编码来自 TDD SRS。若这里只给 2Tx，SRS 只能估到 64x2，
# 却拿它去设计 64x4 下行权，维度与物理端口都对不上。公司 64T4R 基线因此
# 明确采用 4Tx/4Rx UE；只有显式做 2Tx 终端课题时才覆盖成 2。
COMPANY_UE_TX_ANT = 4
COMPANY_LINK = "BOTH"             # DL 真值 + UL SRS 估计，TDD 成对生成

# 公司面板采用交叉极化 +45/-45 度。即便 ChannelHub 当前也把它作为双极化
# 默认值，这里仍要显式落盘：硬件真相不能依赖下游库某个可能漂移的默认参数。
COMPANY_POLARIZATION_SLANTS_DEG: list[float] = [45.0, -45.0]


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
        calibration_id = "company-64T-1to3-192ae-v1"
    elif profile_key in {"256", "256t", "company_256t"}:
        m = COMPANY_256T_ELEMENTS_PER_PORT
        port_order = COMPANY_256T_PORT_ORDER
        calibration_id = "company-256T-1to6-1536ae-v1"
    else:
        raise ValueError(f"unknown company antenna profile {profile!r}")

    return {
        "port_order": port_order,
        "vertical_index_order": (
            "bottom_to_top" if profile_key in {"64", "64t", "company_64t"}
            else COMPANY_256T_VERTICAL_INDEX_ORDER
        ),
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
            "fixed_downtilt_deg": float(fixed_downtilt_deg),
            "calibration_id": calibration_id,
        },
    }


def is_company_panel(panel: Any) -> bool:
    """这个面板是不是已确认物理结构的公司 64T 或 256T。

    64T 使用 1 驱 3 / ``h_v_pol``；256T 使用 1 驱 6 / 图示
    ``pol_h_v``。其他端口数不推断馈电结构。
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
        out["note"] = (
            "64 个端口按**独立阵元**建模、间距一律 0.5λ —— 这不是真实 AAU。"
            "真实硬件是 1 驱 3、192 阵子、垂直 0.67λ。"
            "面板为 8x4x2 时会自动切到真实模型；其他面板保持 legacy。"
        )
        return out
    ant = cfg.get("bs_antenna") or {}
    sub = ant.get("fixed_vertical_subarray") or {}
    elem = ant.get("element_pattern") or {}
    m = int(sub.get("elements_per_rf_port", COMPANY_ELEMENTS_PER_PORT))
    dv = float(sub.get("ae_vertical_spacing_lambda", COMPANY_V_SPACING_LAMBDA))
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
        "element_pattern_is_measured": False,
        "element_horizontal_hpbw_deg": float(elem.get("horizontal_hpbw_deg", 110.0)),
        "element_vertical_hpbw_deg": float(elem.get("vertical_hpbw_deg", 65.0)),
        "element_pattern_source": elem.get("source", "parametric_temporary"),
        "port_order": ant.get("port_order", COMPANY_PORT_ORDER),
        "vertical_index_order": ant.get("vertical_index_order", "bottom_to_top"),
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
        # 尚无公司终端阵列实测参数，不能把它描述成真实手机天线。
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
                "不是公司终端阵列实测值"
            ),
        },
    }
