"""仿真说明书：配置敲定之后，把这次到底在仿什么画出来给用户看。

**为什么要有这个。** 计划书（``plan.render_plan_markdown``）给的是文字和
一串 key: value，用户要在脑子里把"64T"还原成 8x4x2 的双极化面板、把
"7 站 3 扇区 ISD 500"还原成六边形栅格上 21 个扇区的指向、把"272 RB"还原成
17 个 RBG。**这一步在脑子里做，就一定有人做错。**

所以配置敲定后出一份说明书：参数表 + 示意图，一次看清楚

* **阵列**——RF 端口怎么排、每个端口驱动几个物理阵子、间距多少；
* **拓扑**——站点在哪、扇区朝哪、UE 撒在哪、站间距多大；
* **频域**——多少 RB、怎么分 RBG、子载波间隔与带宽；
* **时域**——TDD 时隙配比；
* **信道**——CDL 剖面的时延功率谱；
* **链路预算**——从发射功率到 SINR 的每一跳。

产物是**自包含的离线 HTML**（SVG 内联，无外部依赖），落到项目
``artifacts/specs/``。对话里只回路径和摘要，不把图往回贴。

--- 两条纪律 -----------------------------------------------------------

1. **只画配置里真有的东西。** 站数被六边形栅格吸附（配 2 站实际 7 站）就画
   实际的 7 站并标注；阵列是 legacy 独立阵元就画 64 个独立阵元、不画 1 驱 3。
   说明书画的是**将要跑的那个仿真**，不是用户以为的那个。
2. **区分"用户定的"和"默认补的"。** 参数表里两者分开标注——用户看到
   "这 17 项是我没管、系统替我定的"才有机会喊停。
"""
from __future__ import annotations

import html
import json
import math
import time
import uuid
from typing import Any

from .paths import artifacts_root

# ---------------------------------------------------------------------------
# 结构化说明书
# ---------------------------------------------------------------------------

# 参数 -> (中文名, 单位/格式化). 只列值得摆在明面上的，其余进"其他配置"。
_KEY_LABELS: dict[str, tuple[str, str]] = {
    "scenario": ("传播场景", ""),
    "channel_model": ("信道剖面", ""),
    "carrier_freq_hz": ("载波频率", "ghz"),
    "bandwidth_hz": ("带宽", "mhz"),
    "subcarrier_spacing": ("子载波间隔", "khz"),
    "num_rb": ("RB 数", ""),
    "num_sites": ("站点数", ""),
    "sectors_per_site": ("每站扇区", ""),
    "isd_m": ("站间距", "m"),
    "num_ues": ("UE 数", ""),
    "num_interfering_ues": ("每邻区干扰 UE 数", ""),
    "num_bs_tx_ant": ("基站发射端口", ""),
    "num_bs_rx_ant": ("基站接收端口", ""),
    "num_ue_tx_ant": ("终端发射天线", ""),
    "num_ue_rx_ant": ("终端接收天线", ""),
    "tx_power_dbm": ("基站发射功率", "dbm"),
    "ue_tx_power_dbm": ("终端发射功率", "dbm"),
    "noise_figure_db": ("噪声系数", "db"),
    "tx_height_m": ("基站高度", "m"),
    "ue_height_m": ("终端高度", "m"),
    "ue_speed_kmh": ("终端速度", "kmh"),
    "mobility_mode": ("移动模型", ""),
    "topology_layout": ("拓扑布局", ""),
    "link": ("链路方向", ""),
    "channel_est_mode": ("信道估计", ""),
    "tdd_pattern": ("TDD 图案", ""),
    "num_slots_per_sample": ("每样本时隙数", ""),
    "num_ofdm_symbols": ("每时隙符号数", ""),
    "pdsch_load": ("下行负载", ""),
    "pusch_load": ("上行负载", ""),
    "srs_comb": ("SRS 梳齿", ""),
    "srs_periodicity": ("SRS 周期", "slot"),
    "ue_distribution": ("UE 分布", ""),
    "train_penetration_loss_db": ("车体穿透损耗", "db"),
}

_FMT = {
    "ghz": lambda v: f"{float(v) / 1e9:g} GHz",
    "mhz": lambda v: f"{float(v) / 1e6:g} MHz",
    "khz": lambda v: f"{float(v) / 1e3:g} kHz",
    "m": lambda v: f"{float(v):g} m",
    "db": lambda v: f"{float(v):g} dB",
    "dbm": lambda v: f"{float(v):g} dBm",
    "kmh": lambda v: f"{float(v):g} km/h",
    "slot": lambda v: f"{int(v)} 时隙",
    "": lambda v: f"{v}",
}


def _fmt(key: str, value: Any) -> str:
    kind = _KEY_LABELS.get(key, ("", ""))[1]
    try:
        return _FMT.get(kind, _FMT[""])(value)
    except (TypeError, ValueError):
        return str(value)


def _site_positions(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """真实站点/扇区位置。拿不到时回空列表 + 原因。

    **直接问 ChannelHub 的拓扑模块**，不自己重算——六边形栅格的站数吸附
    （1/7/19）和线性布局的两侧交错都在那边，重算一定会漂。
    """
    try:
        from .channelhub import _ensure_path  # noqa: PLC0415

        _ensure_path()
        from msg_embedding.topology.hex_grid import (  # noqa: PLC0415
            make_hex_grid,
            make_linear_grid,
        )
    except Exception as exc:  # noqa: BLE001
        return [], f"取不到拓扑模块：{type(exc).__name__}"

    n_sites = int(cfg.get("num_sites", 1) or 1)
    sectors = int(cfg.get("sectors_per_site", 1) or 1)
    isd = float(cfg.get("isd_m", 500.0) or 500.0)
    h = float(cfg.get("tx_height_m", 25.0) or 25.0)
    scen = str(cfg.get("scenario", "UMa_NLOS"))
    layout = str(cfg.get("topology_layout", "hexagonal"))

    try:
        if layout == "linear":
            cells = make_linear_grid(
                num_sites=n_sites, isd_m=isd, sectors=sectors, tx_height_m=h,
                scenario=scen, track_offset_m=float(cfg.get("track_offset_m", 80.0) or 80.0),
            )
        else:
            rings = 0 if n_sites <= 1 else (1 if n_sites <= 7 else 2)
            cells = make_hex_grid(
                num_rings=rings, isd_m=isd, sectors=sectors, tx_height_m=h, scenario=scen,
            )
    except Exception as exc:  # noqa: BLE001
        return [], f"拓扑构造失败：{type(exc).__name__}: {exc}"

    return [
        {
            "x": float(c.position[0]), "y": float(c.position[1]),
            "z": float(c.position[2]), "az": float(c.azimuth_deg),
            "site_id": int(getattr(c, "site_id", 0)),
        }
        for c in cells
    ], None


def build_spec(
    cfg: dict[str, Any],
    *,
    num_samples: int | None = None,
    user_set: list[str] | None = None,
    dataset_id: str | None = None,
    title: str = "",
) -> dict[str, Any]:
    """把配置整理成结构化说明书。不生成数据，纯解释。"""
    from . import hardware as hw  # noqa: PLC0415
    from .generate import _ensure_bs_panel, _rb_from_bandwidth  # noqa: PLC0415

    cfg = dict(cfg)
    cfg.pop("num_samples", None)
    source = str(cfg.pop("source", "internal_sim"))
    panel, panel_derived = _ensure_bs_panel(cfg)
    hw.apply_array_defaults(cfg)
    applied = hw.strip_markers(cfg)
    array = hw.array_summary(cfg, applied)

    n_rb = int(cfg.get("num_rb") or _rb_from_bandwidth(cfg))
    scs = float(cfg.get("subcarrier_spacing", 30000) or 30000)
    n_sites_cfg = int(cfg.get("num_sites", 1) or 1)
    sectors = int(cfg.get("sectors_per_site", 1) or 1)
    cells, cell_err = _site_positions(cfg)
    n_sites_real = len({c["site_id"] for c in cells}) if cells else n_sites_cfg

    user_keys = set(user_set or [])
    shown, others = [], []
    for k, v in sorted(cfg.items()):
        if k.startswith("_") or k in ("bs_antenna", "measurements", "bs_panel"):
            continue
        row = {
            "key": k, "value": _fmt(k, v),
            "label": _KEY_LABELS.get(k, (k, ""))[0],
            "by_user": k in user_keys,
        }
        (shown if k in _KEY_LABELS else others).append(row)
    shown.sort(key=lambda r: list(_KEY_LABELS).index(r["key"]))

    # RBG 划分：38.214 Table 5.1.2.2.1-1 Configuration 2 在 145~275 PRB 时 P=16
    rbg_size = 16 if n_rb >= 145 else (8 if n_rb >= 73 else 4)
    n_rbg = math.ceil(n_rb / rbg_size)

    notes: list[str] = []
    if n_sites_real != n_sites_cfg:
        notes.append(
            f"配置写的是 {n_sites_cfg} 站，六边形栅格按环数展开后实际是 "
            f"**{n_sites_real} 站**（只能取 1/7/19）。图上画的是实际值。"
        )
    if array.get("antenna_model_mode") == "legacy_64" and hw.is_company_panel(panel):
        notes.append(
            "**阵列走的是 legacy 独立阵元模型，不是本地 1 驱 3 硬件。**"
            "实测这样报出的吞吐偏高约 27%、边缘用户偏高约 61%。"
        )
    if cell_err:
        notes.append(f"站点位置画不出来：{cell_err}")
    if int(cfg.get("num_rb") or 0) and n_rb != _rb_from_bandwidth(cfg):
        notes.append(
            f"RB 数是显式指定的 {n_rb}，不是由带宽推的 "
            f"{_rb_from_bandwidth(cfg)}。"
        )

    return {
        "title": title or "仿真说明书",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "dataset_id": dataset_id,
        "num_samples": num_samples,
        "array": array,
        "panel": list(panel),
        "panel_derived": bool(panel_derived),
        "topology": {
            "layout": str(cfg.get("topology_layout", "hexagonal")),
            "num_sites_config": n_sites_cfg,
            "num_sites_actual": n_sites_real,
            "sectors_per_site": sectors,
            "num_cells": len(cells) or n_sites_cfg * sectors,
            "isd_m": float(cfg.get("isd_m", 0) or 0),
            "cells": cells,
        },
        "frequency": {
            "carrier_freq_hz": float(cfg.get("carrier_freq_hz", 0) or 0),
            "bandwidth_hz": float(cfg.get("bandwidth_hz", 0) or 0),
            "scs_hz": scs,
            "num_rb": n_rb,
            "rbg_size": rbg_size,
            "num_rbg": n_rbg,
            "occupied_hz": n_rb * 12 * scs,
        },
        "time": {
            "tdd_pattern": str(cfg.get("tdd_pattern", "DDDSU")),
            "slots_per_sample": int(cfg.get("num_slots_per_sample", 1) or 1),
            "symbols_per_slot": int(cfg.get("num_ofdm_symbols", 14) or 14),
        },
        "params": shown,
        "other_params": others,
        "notes": notes,
        "config": cfg,
    }


# ---------------------------------------------------------------------------
# SVG 示意图
# ---------------------------------------------------------------------------


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _svg_array(spec: dict[str, Any]) -> str:
    """阵列面板：RF 端口栅格 + 每端口驱动的物理阵子。"""
    arr = spec["array"]
    n_h, n_v, n_p = (list(spec["panel"]) + [1, 1, 1])[:3]
    m = int(arr.get("elements_per_rf_port") or 1)
    dv = float(arr.get("ae_vertical_spacing_lambda") or 0.5)
    dh = float(arr.get("horizontal_spacing_lambda") or 0.5)
    legacy = arr.get("antenna_model_mode") == "legacy_64"

    cw, ae_h, gap = 34, 13, 5          # 列宽、单个阵子高、阵子间隙
    port_h = m * ae_h + (m - 1) * gap
    pad_l, pad_t = 96, 54
    w = pad_l + n_h * cw + 268
    h = pad_t + n_v * (port_h + 16) + 74

    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" '
           f'role="img" aria-label="阵列面板示意图">']
    out.append('<style>.lb{font:11px ui-monospace,Consolas,monospace;fill:#6e6e73}'
               '.ti{font:600 12.5px system-ui;fill:currentColor}'
               '.ae{fill:#0071e3;opacity:.85}.ae2{fill:#5a3ec8;opacity:.85}'
               '.pt{fill:none;stroke:#0071e3;stroke-width:1.4;opacity:.55}</style>')
    out.append(f'<text class="ti" x="8" y="20">'
               f'{"64 个独立阵元（legacy）" if legacy else f"RF 端口 {n_h}x{n_v}x{n_p} = {n_h*n_v*n_p}，每端口 1 驱 {m}"}'
               f'</text>')
    out.append(f'<text class="lb" x="8" y="38">'
               f'{"间距一律 " + format(dh, "g") + "λ —— 不是本地硬件" if legacy else f"物理阵子 {n_h}x{n_v*m}x{n_p} = {n_h*n_v*m*n_p}"}'
               f'</text>')

    for v in range(n_v):
        y0 = pad_t + v * (port_h + 16)
        for hh in range(n_h):
            x0 = pad_l + hh * cw
            if not legacy:
                out.append(f'<rect class="pt" x="{x0 - 3}" y="{y0 - 3}" '
                           f'width="{cw - 8}" height="{port_h + 6}" rx="4"/>')
            for q in range(m):
                y = y0 + q * (ae_h + gap)
                # 双极化画成两个半宽块（+45 / -45）
                out.append(f'<rect class="ae" x="{x0}" y="{y}" width="{(cw-12)//2}" '
                           f'height="{ae_h}" rx="2"/>')
                if n_p == 2:
                    out.append(f'<rect class="ae2" x="{x0 + (cw-12)//2 + 2}" y="{y}" '
                               f'width="{(cw-12)//2}" height="{ae_h}" rx="2"/>')
        out.append(f'<text class="lb" x="{pad_l - 10}" y="{y0 + port_h/2 + 4}" '
                   f'text-anchor="end">端口 v={v}</text>')

    # 间距标注
    xr = pad_l + n_h * cw + 16
    out.append(f'<line x1="{xr}" y1="{pad_t}" x2="{xr}" y2="{pad_t + ae_h}" '
               f'stroke="#6e6e73" stroke-width="1"/>')
    out.append(f'<text class="lb" x="{xr + 8}" y="{pad_t + ae_h}">阵子垂直间距 {dv:g}λ</text>')
    if not legacy and m > 1:
        out.append(f'<line x1="{xr}" y1="{pad_t}" x2="{xr}" y2="{pad_t + port_h + 16}" '
                   f'stroke="#5a3ec8" stroke-width="1.4"/>')
        out.append(f'<text class="lb" x="{xr + 8}" y="{pad_t + port_h + 12}" fill="#5a3ec8">'
                   f'端口相位中心间距 {m * dv:g}λ{"（&gt;λ，有栅瓣）" if m * dv > 1 else ""}</text>')
    yb = pad_t + n_v * (port_h + 16) + 8
    out.append(f'<line x1="{pad_l}" y1="{yb}" x2="{pad_l + cw}" y2="{yb}" '
               f'stroke="#6e6e73" stroke-width="1"/>')
    out.append(f'<text class="lb" x="{pad_l}" y="{yb + 16}">水平间距 {dh:g}λ</text>')
    if n_p == 2:
        out.append(f'<rect class="ae" x="{pad_l + 150}" y="{yb + 4}" width="10" height="10" rx="2"/>'
                   f'<text class="lb" x="{pad_l + 164}" y="{yb + 13}">+45°</text>'
                   f'<rect class="ae2" x="{pad_l + 210}" y="{yb + 4}" width="10" height="10" rx="2"/>'
                   f'<text class="lb" x="{pad_l + 224}" y="{yb + 13}">-45°</text>')
    out.append("</svg>")
    return "".join(out)


def _svg_layout(spec: dict[str, Any], ue_xy: list[tuple[float, float]] | None = None) -> str:
    """站点布局：站点位置 + 扇区指向 + UE 撒点。"""
    topo = spec["topology"]
    cells = topo["cells"]
    if not cells:
        return '<p class="src">拿不到站点位置，无法绘制拓扑图。</p>'

    xs = [c["x"] for c in cells] + [p[0] for p in (ue_xy or [])]
    ys = [c["y"] for c in cells] + [p[1] for p in (ue_xy or [])]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 1.18
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    S = 460
    def px(x): return S / 2 + (x - cx) / span * S
    def py(y): return S / 2 - (y - cy) / span * S

    out = [f'<svg viewBox="0 0 {S} {S + 30}" width="100%" style="max-width:{S}px" '
           f'role="img" aria-label="站点布局示意图">']
    out.append('<style>.lb{font:10.5px ui-monospace,Consolas,monospace;fill:#6e6e73}'
               '.ue{fill:#34c759;opacity:.5}.st{fill:#ff3b30}'
               '.bs{stroke:#0071e3;stroke-width:2;opacity:.75}</style>')

    for x, y in (ue_xy or []):
        out.append(f'<circle class="ue" cx="{px(x):.1f}" cy="{py(y):.1f}" r="2.4"/>')

    seen: set[tuple[float, float]] = set()
    arm = max(S / span * topo["isd_m"] * 0.30, 14) if topo["isd_m"] else 22
    for c in cells:
        X, Y = px(c["x"]), py(c["y"])
        a = math.radians(c["az"])
        out.append(f'<line class="bs" x1="{X:.1f}" y1="{Y:.1f}" '
                   f'x2="{X + arm * math.cos(a):.1f}" y2="{Y - arm * math.sin(a):.1f}"/>')
        if (c["x"], c["y"]) not in seen:
            seen.add((c["x"], c["y"]))
            out.append(f'<circle class="st" cx="{X:.1f}" cy="{Y:.1f}" r="4"/>')

    if topo["isd_m"]:
        out.append(f'<text class="lb" x="8" y="{S + 20}">'
                   f'站间距 {topo["isd_m"]:g} m · {topo["num_sites_actual"]} 站 x '
                   f'{topo["sectors_per_site"]} 扇区 = {topo["num_cells"]} 小区'
                   f'{" · UE " + str(len(ue_xy)) + " 个" if ue_xy else ""}</text>')
    out.append('<circle class="st" cx="330" cy="' + str(S + 16) + '" r="4"/>'
               f'<text class="lb" x="340" y="{S + 20}">站点</text>'
               f'<line class="bs" x1="384" y1="{S + 16}" x2="400" y2="{S + 16}"/>'
               f'<text class="lb" x="404" y="{S + 20}">扇区指向</text>')
    out.append("</svg>")
    return "".join(out)


def _svg_freq(spec: dict[str, Any]) -> str:
    """频域：RB 按 RBG 分组。"""
    f = spec["frequency"]
    n_rb, size, n_rbg = f["num_rb"], f["rbg_size"], f["num_rbg"]
    W, bh, pad = 900, 26, 4
    per = (W - 2 * pad) / max(n_rb, 1)
    out = [f'<svg viewBox="0 0 {W} 92" width="100%" role="img" aria-label="频域 RB 布局">']
    out.append('<style>.lb{font:11px ui-monospace,Consolas,monospace;fill:#6e6e73}'
               '.rb{fill:#0071e3;opacity:.75}.rb2{fill:#5a3ec8;opacity:.75}</style>')
    for g in range(n_rbg):
        lo = g * size
        n_in = min(size, n_rb - lo)
        x = pad + lo * per
        out.append(f'<rect class="{"rb" if g % 2 == 0 else "rb2"}" x="{x:.2f}" y="26" '
                   f'width="{max(n_in * per - 1, 0.5):.2f}" height="{bh}" rx="2"/>')
        if n_rbg <= 24:
            out.append(f'<text class="lb" x="{x + n_in * per / 2:.1f}" y="20" '
                       f'text-anchor="middle">{g}</text>')
    out.append(f'<text class="lb" x="{pad}" y="70">'
               f'{n_rb} RB = {n_rbg} RBG x {size} RB'
               f'{"（末组 " + str(n_rb - (n_rbg - 1) * size) + " RB）" if n_rb % size else ""}'
               f' · 每 RB 12 个子载波 x {f["scs_hz"]/1e3:g} kHz = '
               f'{f["occupied_hz"]/1e6:.2f} MHz 占用</text>')
    out.append(f'<text class="lb" x="{pad}" y="86">'
               f'载波 {f["carrier_freq_hz"]/1e9:g} GHz · 标称带宽 {f["bandwidth_hz"]/1e6:g} MHz'
               f' · 仿真粒度到 RB 为止（不建模到 RE）</text>')
    out.append("</svg>")
    return "".join(out)


def _svg_tdd(spec: dict[str, Any]) -> str:
    """TDD 时隙图案。"""
    t = spec["time"]
    pat = t["tdd_pattern"].upper()
    if not pat or any(c not in "DUS" for c in pat):
        return ""
    cw, W = 40, 40 * len(pat) + 20
    col = {"D": "#0071e3", "U": "#34c759", "S": "#ff9f0a"}
    out = [f'<svg viewBox="0 0 {W} 74" width="100%" style="max-width:{W}px" '
           f'role="img" aria-label="TDD 时隙图案">']
    out.append('<style>.lb{font:11px ui-monospace,Consolas,monospace;fill:#6e6e73}'
               '.sl{font:600 13px ui-monospace,Consolas,monospace;fill:#fff}</style>')
    for i, c in enumerate(pat):
        x = 10 + i * cw
        out.append(f'<rect x="{x}" y="12" width="{cw - 4}" height="30" rx="4" '
                   f'fill="{col[c]}" opacity=".88"/>')
        out.append(f'<text class="sl" x="{x + (cw - 4)/2}" y="33" text-anchor="middle">{c}</text>')
    out.append(f'<text class="lb" x="10" y="60">图案 {pat}（D 下行 / U 上行 / S 特殊）'
               f' · 每样本 {t["slots_per_sample"]} 时隙 x {t["symbols_per_slot"]} 符号</text>')
    out.append("</svg>")
    return "".join(out)


def _svg_pdp(spec: dict[str, Any]) -> str:
    """CDL/TDL 剖面的时延功率谱。"""
    name = str(spec["config"].get("channel_model", ""))
    if not name:
        return ""
    try:
        from .channelhub import cdl_profile  # noqa: PLC0415

        prof = cdl_profile(name)
        # 属性名是 delays_norm / powers_dB（不是 delays / powers_db）——
        # 早先写错名字时 except 把它吞成"没有可画的剖面"，图就这么静静少了一张。
        delays = [float(x) for x in prof.delays_norm]
        powers = [float(x) for x in prof.powers_dB]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return (f'<p class="src">取不到 {_esc(name)} 的时延功率谱：'
                f'{type(exc).__name__}: {_esc(exc)}</p>')
    if not delays:
        return ""

    W, H, pad = 900, 150, 34
    dmax = max(delays) or 1.0
    pmin = min(min(powers), -30.0)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="时延功率谱">']
    out.append('<style>.lb{font:11px ui-monospace,Consolas,monospace;fill:#6e6e73}'
               '.cl{stroke:#0071e3;stroke-width:2.2;opacity:.8}</style>')
    for d, p in zip(delays, powers, strict=False):
        x = pad + (d / dmax) * (W - 2 * pad)
        y = H - 34 - (p - pmin) / (0 - pmin) * (H - 62)
        out.append(f'<line class="cl" x1="{x:.1f}" y1="{H-34}" x2="{x:.1f}" y2="{y:.1f}"/>')
    out.append(f'<line x1="{pad}" y1="{H-34}" x2="{W-pad}" y2="{H-34}" '
               f'stroke="#d2d2d7" stroke-width="1"/>')
    out.append(f'<text class="lb" x="{pad}" y="{H-14}">0</text>'
               f'<text class="lb" x="{W-pad}" y="{H-14}" text-anchor="end">'
               f'归一化时延 {dmax:g}</text>')
    out.append(f'<text class="lb" x="{pad}" y="16">{_esc(name)} · {len(delays)} 簇 · '
               f'纵轴功率 dB（{pmin:g} ~ 0）</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root{--bg:#fafafa;--card:#fff;--ink:#1d1d1f;--ink-soft:#6e6e73;--border:#d2d2d7;
 --accent:#0071e3;--tint:#f2f2f4;--tint-blue:#eef5fd;--tint-blue-ink:#0055b3;
 --tint-amber:#fdf5e8;--tint-amber-ink:#8a5a00;--tint-red:#fdeeed;--tint-red-ink:#a32620;}
@media(prefers-color-scheme:dark){:root{--bg:#1d1d1f;--card:#2c2c2e;--ink:#f5f5f7;
 --ink-soft:#aeaeb2;--border:#38383a;--accent:#0a84ff;--tint:#333336;
 --tint-blue:#12263c;--tint-blue-ink:#8ec2ff;--tint-amber:#33280c;--tint-amber-ink:#ffd77a;
 --tint-red:#3a1a18;--tint-red-ink:#ff9a92;}}
*{box-sizing:border-box}
body{margin:0;padding:36px 20px;background:var(--bg);color:var(--ink);
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
 font-size:15px;line-height:1.7}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:29px;font-weight:700;letter-spacing:-.03em;margin:0 0 6px}
h2{font-size:20px;font-weight:700;margin:40px 0 6px;padding-top:18px;border-top:1px solid var(--border)}
.meta{color:var(--ink-soft);font-size:12.5px;font-family:ui-monospace,Consolas,monospace;margin:0 0 6px}
.card{background:var(--card);border:1px solid var(--border);border-radius:13px;padding:20px;margin:16px 0}
.card>svg{display:block;margin:0 auto}
.tbl-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:10px;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px;background:var(--card)}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{background:var(--tint);color:var(--ink-soft);font-weight:600;font-size:12.5px;white-space:nowrap}
tr:last-child td{border-bottom:none}
code{font-family:ui-monospace,Consolas,monospace;background:var(--tint);padding:1px 5px;
 border-radius:4px;font-size:12.5px}
.pill{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:20px}
.p-user{background:var(--tint-blue);color:var(--tint-blue-ink)}
.p-auto{background:var(--tint);color:var(--ink-soft)}
.callout{border-radius:10px;padding:13px 17px;margin:14px 0;border-left:3px solid}
.c-amber{background:var(--tint-amber);color:var(--tint-amber-ink);border-left-color:#ff9f0a}
.c-blue{background:var(--tint-blue);color:var(--tint-blue-ink);border-left-color:var(--accent)}
.c-red{background:var(--tint-red);color:var(--tint-red-ink);border-left-color:#ff3b30}
.src{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--ink-soft)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.kv{display:flex;justify-content:space-between;gap:12px;padding:4px 0;font-size:13.5px;
 border-bottom:1px dashed var(--border)}
.kv:last-child{border-bottom:none}
.kv b{font-weight:600;font-family:ui-monospace,Consolas,monospace}
footer{margin-top:44px;padding-top:18px;border-top:1px solid var(--border);
 color:var(--ink-soft);font-size:12.5px}
"""


def render_html(spec: dict[str, Any], ue_xy: list[tuple[float, float]] | None = None) -> str:
    a = spec["array"]

    notes = "".join(
        f'<div class="callout {"c-red" if "**" in n else "c-amber"}">'
        f'<p>{_esc(n).replace("**", "")}</p></div>'
        for n in spec["notes"]
    )

    # 这两段单独拎出来：内联进 f-string 要转义引号，而 f-string 里的反斜杠
    # **Python 3.12 才允许**，本项目要求 >= 3.10。ruff 当场抓到的。
    pill_user = '<span class="pill p-user">用户指定</span>'
    pill_auto = '<span class="pill p-auto">默认</span>'

    def rows(items):
        return "".join(
            f'<tr><td>{_esc(r["label"] or r["key"])}<br>'
            f'<span class="src">{_esc(r["key"])}</span></td>'
            f'<td><b>{_esc(r["value"])}</b></td>'
            f'<td>{pill_user if r["by_user"] else pill_auto}</td></tr>'
            for r in items
        )

    n_user = sum(1 for r in spec["params"] + spec["other_params"] if r["by_user"])
    n_all = len(spec["params"]) + len(spec["other_params"])

    array_kv = "".join(
        f'<div class="kv"><span>{k}</span><b>{_esc(v)}</b></div>'
        for k, v in [
            ("RF 端口", f'{spec["panel"][0]}H x {spec["panel"][1]}V x {spec["panel"][2]}pol'
                        f' = {spec["panel"][0]*spec["panel"][1]*spec["panel"][2]}'),
            ("物理阵子", a.get("physical_elements") or "同端口数（legacy 独立阵元）"),
            ("馈电", f'1 驱 {a["elements_per_rf_port"]}' if a.get("elements_per_rf_port") else "无子阵"),
            ("水平间距", f'{a.get("horizontal_spacing_lambda", 0.5):g}λ'),
            ("垂直间距", f'{a.get("ae_vertical_spacing_lambda", 0.5):g}λ'),
            ("端口相位中心", f'{a.get("rf_vertical_spacing_lambda"):g}λ'
                            if a.get("rf_vertical_spacing_lambda") else "-"),
            ("固定下倾", f'{a.get("fixed_downtilt_deg", 0):g}°'),
            ("模型", a.get("antenna_model_mode")),
        ] if v not in (None, "-")
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(spec["title"])}</title><style>{_CSS}</style></head>
<body><div class="wrap">

<h1>{_esc(spec["title"])}</h1>
<p class="meta">{_esc(spec["created_at"])} · 引擎 {_esc(spec["source"])}
{" · 数据集 " + _esc(spec["dataset_id"]) if spec.get("dataset_id") else ""}
{" · " + str(spec["num_samples"]) + " 个样本" if spec.get("num_samples") else ""}</p>
<p class="src">这份说明书描述的是<b>将要跑（或已经跑过）的那个仿真</b>，
不是配置意图——站数被栅格吸附、阵列走了 legacy 之类的差异都按实际画。</p>

{notes}

<h2>一、基站阵列</h2>
<div class="card">{_svg_array(spec)}</div>
<div class="grid2"><div class="card">{array_kv}</div>
<div class="card"><p class="src">{_esc(a.get("note", ""))}</p></div></div>

<h2>二、站点拓扑</h2>
<div class="card">{_svg_layout(spec, ue_xy)}</div>

<h2>三、频域布局</h2>
<div class="card">{_svg_freq(spec)}</div>

<h2>四、时域与 TDD</h2>
<div class="card">{_svg_tdd(spec) or '<p class="src">无 TDD 图案。</p>'}</div>

<h2>五、信道剖面</h2>
<div class="card">{_svg_pdp(spec) or '<p class="src">该模型没有可画的时延功率谱。</p>'}</div>

<h2>六、参数全表</h2>
<p class="src">{n_user}/{n_all} 项由用户指定，其余走默认值。
<b>标着「默认」的都是系统替你定的</b>，不认可就改。</p>
<div class="tbl-wrap"><table>
<tr><th>参数</th><th>值</th><th>来源</th></tr>
{rows(spec["params"])}{rows(spec["other_params"])}
</table></div>

<footer>superwireless 仿真说明书 · 图与数均由本次配置生成，未经手工编辑</footer>
</div></body></html>
"""


def write_spec(
    cfg: dict[str, Any],
    *,
    num_samples: int | None = None,
    user_set: list[str] | None = None,
    dataset_id: str | None = None,
    title: str = "",
    ue_xy: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """生成说明书并落盘，返回结构化摘要 + 文件路径。

    HTML 落到 ``artifacts/specs/``；**对话里只回路径与摘要**，
    不要把图或整份 HTML 贴回去。
    """
    spec = build_spec(cfg, num_samples=num_samples, user_set=user_set,
                      dataset_id=dataset_id, title=title)
    out_dir = artifacts_root() / "specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    # **秒级时间戳不够唯一。** 同一秒里连着出两份说明书（先看预设、再看草稿），
    # 后一份会把前一份直接覆盖掉，而且不报错——用户拿到的路径指向的是别人的图。
    # 实测就这么丢过一份：HST 的拓扑图被单小区的覆盖成了一个孤零零的点。
    # 数据集句柄本身唯一，其余情况补一段随机后缀。
    stem = dataset_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    path = out_dir / f"spec-{stem}.html"
    path.write_text(render_html(spec, ue_xy), encoding="utf-8")

    (out_dir / f"spec-{stem}.json").write_text(
        json.dumps({k: v for k, v in spec.items() if k != "config"},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "html_path": str(path),
        "json_path": str(out_dir / f"spec-{stem}.json"),
        "headline": headline(spec),
        "notes": spec["notes"],
        "array": spec["array"],
        "topology": {k: v for k, v in spec["topology"].items() if k != "cells"},
        "frequency": spec["frequency"],
        "time": spec["time"],
        "num_params": len(spec["params"]) + len(spec["other_params"]),
        "num_user_set": sum(1 for r in spec["params"] + spec["other_params"] if r["by_user"]),
    }


def headline(spec: dict[str, Any]) -> str:
    """一句话概括这次仿真。给对话里口头转述用。"""
    a, f, t = spec["array"], spec["frequency"], spec["topology"]
    m = a.get("elements_per_rf_port")
    n_ports = spec["panel"][0] * spec["panel"][1] * spec["panel"][2]
    arr = (f"{n_ports}T（{spec['panel'][0]}x{spec['panel'][1]}x{spec['panel'][2]}"
           + (f"，1 驱 {m}、{a.get('physical_elements')} 阵子" if m else "，独立阵元")
           + "）")
    topo = (f"{t['num_sites_actual']} 站 x {t['sectors_per_site']} 扇区 = "
            f"{t['num_cells']} 小区" + (f"、站间距 {t['isd_m']:g} m" if t["isd_m"] else ""))
    return (f"{arr} · {topo} · {f['carrier_freq_hz']/1e9:g} GHz / "
            f"{f['scs_hz']/1e3:g} kHz / {f['num_rb']} RB "
            f"({f['num_rbg']}x{f['rbg_size']}) · {spec['config'].get('scenario','')} "
            f"{spec['config'].get('channel_model','')}")
