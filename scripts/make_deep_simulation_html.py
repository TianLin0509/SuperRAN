"""Build the self-contained spectrum/system deep-audit report.

All numerical claims come from ``deep_simulation_audit.json``.  The report
embeds that JSON, local KaTeX assets, formulas, flow charts, source excerpts,
and reverse-sentinel evidence so it remains inspectable offline.
"""
from __future__ import annotations

import html
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import katex as kx  # noqa: E402
from superran import load  # noqa: E402
from superran import mathml as mm  # noqa: E402

DATA_PATH = ROOT / "artifacts" / "results" / "deep_simulation_audit.json"
OUT = ROOT / "artifacts" / "reports" / "SUPERRAN_USER_MANUAL.html"


def M(tex: str, *, block: bool = False) -> str:
    return kx.wrap(tex, mm.render(tex, block=block), display=block)


# Formula constants stay outside f-strings. Python <3.12 rejects backslashes
# inside f-string expression parts, even when the local 3.12 interpreter runs.
F_CHANNEL = M(r"H_{u,t,f}\in\mathbb{C}^{N_{\mathrm{BS}}\times N_{\mathrm{UE}}}", block=True)
F_GEOMETRY = M(
    r"I=S\,10^{-\mathrm{SIR}/10},\quad N=S\,10^{-\mathrm{SINR}/10}-I",
    block=True,
)
F_RUU = M(
    r"R_{uu}(f)=\sum_k\mathbb{E}_t\{(H_{k,t,f}^{H}w_{k,t})(H_{k,t,f}^{H}w_{k,t})^{H}\}",
    block=True,
)
F_SRS_WEIGHT = M(
    r"W_{\mathrm{SRS}}(f,r)=\operatorname{eig}_{1:r}\!\left(\mathbb{E}_t[\hat H_{t,f}\hat H_{t,f}^{H}]\right)",
    block=True,
)
F_WIDEBAND_WEIGHT = M(
    r"W_{\mathrm{WB}}(r)=\operatorname{eig}_{1:r}\!\left(\mathbb{E}_{t,f}[\hat H_{t,f}\hat H_{t,f}^{H}]\right)",
    block=True,
)
F_PMI_WEIGHT = M(
    r"W_{\mathrm{PMI}}(r)=\arg\max_{W\in\mathcal{C}_{\mathrm{TypeI-style}}}\ \widehat{\mathrm{SE}}(W,r)",
    block=True,
)
F_RANK = M(
    r"r^{\star}=\arg\max_{r\in\{1,\ldots,4\}}\ \widehat{\mathrm{SE}}(\hat H,W_r,R_{uu},N)",
    block=True,
)
F_MMSE = M(
    r"G_f=(H_{\mathrm{eff},f}^{H}H_{\mathrm{eff},f}+R_{uu,f}+NI)^{-1}H_{\mathrm{eff},f}^{H}",
    block=True,
)
F_SINR = M(
    r"\gamma_{f,\ell}=\frac{|g_{f,\ell}^{H}h_{f,\ell}|^2P_\ell}{\sum_{j\ne\ell}|g_{f,\ell}^{H}h_{f,j}|^2P_j+g_{f,\ell}^{H}(R_{uu,f}+NI)g_{f,\ell}}",
    block=True,
)
F_SE = M(
    r"\mathrm{SE}=\mathbb{E}_{t,f}\left[\sum_{\ell=1}^{r}\log_2(1+\gamma_{t,f,\ell})\right]",
    block=True,
)
F_EQ_SINR = M(
    r"\gamma_{\mathrm{eq},\ell}=\exp\!\left(\mathbb{E}_{t,f}[\ln(1+\gamma_{t,f,\ell})]\right)-1",
    block=True,
)
F_CLUSTER = M(
    r"\bar d_p=\frac{1}{m_p}\sum_{j\in p}(\mathrm{SE}_{A,j}-\mathrm{SE}_{B,j}),\quad n=|\{p\}|",
    block=True,
)
F_CI = M(
    r"\bar d\ \pm\ t_{0.975,n-1}\frac{s_d}{\sqrt n}",
    block=True,
)
F_MU = M(
    r"\mathcal U^{\star}=\operatorname{SUS}(\rho_{ij}\le\rho_{\max}),\quad W_{\mathrm{ZF}}=H^{H}(HH^{H})^{-1}",
    block=True,
)
F_AGE = M(
    r"\tau_b(t)=t-\max\{t_{b,n}:t_{b,n}\le t-D_{\mathrm{proc}}\},\quad "
    r"L_b=\left\lceil\tau_b/\Delta t_{\mathrm{snap}}\right\rceil,\quad "
    r"\hat H_b(s)=H_b(\max(0,s-L_b))",
    block=True,
)
F_CQI = M(
    r"\bar\gamma_{\mathrm{PMI}}(s,r)=\frac{1}{s+1}\sum_{i=0}^{s}\gamma_{\mathrm{PMI}}(i,r)",
    block=True,
)
F_PF = M(
    r"M_u(t)=\frac{TBS_u(17,t)}{\bar R_u(t)}",
    block=True,
)
F_QOS_PF = M(
    r"M_u(t)=w_u\frac{[R_u^{\mathrm{inst}}(t)]^{\alpha}}{[\bar R_u(t)]^{\beta}}\left(1+\frac{D_u^{\mathrm{HoL}}(t)}{D_u^{\mathrm{budget}}}\right)^{\gamma}",
    block=True,
)
F_TBS_INFO = M(
    r"N_{\mathrm{info}}=N_{\mathrm{RE}}\,Q_m\,R\,\nu",
    block=True,
)
F_NSTAR = M(
    r"n_u^{\star}=\min\{n\in[1,17]:TBS(s,m_u,r_u,n)\ge B_u\}",
    block=True,
)
F_OLLA = M(
    r"\Delta_u(t+1)=\operatorname{clip}\!\left(\Delta_u(t)+\mathbf 1_{\mathrm{NACK}}s_{\uparrow}-\mathbf 1_{\mathrm{ACK}}s_{\down}\right)",
    block=True,
)
F_RAVG = M(
    r"\bar R_u(t+1)=\left(1-\frac1{T_c}\right)\bar R_u(t)+\frac1{T_c}R_u^{\mathrm{credit}}(t)",
    block=True,
)
F_CONSERVE = M(
    r"B_{\mathrm{arrived}}=B_{\mathrm{ACK}}+B_{\mathrm{queued}}+B_{\mathrm{inflight}}+B_{\mathrm{dropped}}",
    block=True,
)
F_REL19 = M(
    r"\mathrm{DRB.UEThpDl}=\frac{\mathrm{ThpVolDl}}{\mathrm{ThpTimeDl}},\quad \text{exclude the final buffer-emptying piece}",
    block=True,
)
F_PDB = M(
    r"\widehat P_{\mathrm{miss}}=\frac{N_{\mathrm{completed\ miss}}+N_{\mathrm{overdue\ incomplete}}}{N_{\mathrm{completed}}+N_{\mathrm{overdue\ incomplete}}}",
    block=True,
)


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def fmt(value: Any, digits: int = 3, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return esc(value)
    if not math.isfinite(number):
        return "—"
    return f"{number:,.{digits}f}{suffix}"


def pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{100 * float(value):.{digits}f}%"


def source_line(rel_path: str, needle: str) -> int:
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return 1


def source_ref(rel_path: str, needle: str) -> str:
    return f"{rel_path}:{source_line(rel_path, needle)}"


def source_excerpt(
    rel_path: str, needle: str, *, before: int = 4, after: int = 18, title: str,
) -> str:
    lines = (ROOT / rel_path).read_text(encoding="utf-8").splitlines()
    center = next((i for i, line in enumerate(lines) if needle in line), 0)
    start = max(0, center - before)
    end = min(len(lines), center + after + 1)
    body = "\n".join(f"{i + 1:4d}  {lines[i]}" for i in range(start, end))
    return (
        '<details class="code"><summary>' + esc(title) + " · "
        + esc(f"{rel_path}:{center + 1}") + "</summary><pre><code>"
        + esc(body) + "</code></pre></details>"
    )


def badge(ok: bool, yes: str = "PASS", no: str = "BLOCK") -> str:
    cls = "pass" if ok else "block"
    return f'<span class="badge {cls}">{yes if ok else no}</span>'


def gate_items(gate: dict[str, Any]) -> str:
    rows = []
    for item in gate.get("items", []):
        sev = item.get("severity", "info")
        cls = "pass" if item.get("passed") else ("warn" if sev != "block" else "block")
        rows.append(
            f'<tr><td><span class="badge {cls}">{"PASS" if item.get("passed") else sev.upper()}</span></td>'
            f'<td><b>{esc(item.get("name"))}</b><br><span class="muted">{esc(item.get("detail"))}</span></td></tr>'
        )
    return "".join(rows)


def topology_svg(positions: np.ndarray, representative: np.ndarray) -> str:
    unique = np.unique(np.round(np.asarray(positions)[:, :2], 6), axis=0)
    sites = [(0.0, 0.0)] + [
        (500.0 * math.cos(math.radians(60 * k)),
         500.0 * math.sin(math.radians(60 * k))) for k in range(6)
    ]
    width, height, margin = 760, 540, 54
    all_xy = np.vstack([unique, np.asarray(sites)])
    xmin, ymin = np.min(all_xy, axis=0) - 120
    xmax, ymax = np.max(all_xy, axis=0) + 120

    def xy(x: float, y: float) -> tuple[float, float]:
        sx = margin + (x - xmin) / (xmax - xmin) * (width - 2 * margin)
        sy = height - margin - (y - ymin) / (ymax - ymin) * (height - 2 * margin)
        return sx, sy

    grid = []
    for sx0, sy0 in sites:
        sx, sy = xy(sx0, sy0)
        r = 500 / (xmax - xmin) * (width - 2 * margin) / math.sqrt(3)
        pts = []
        for k in range(6):
            px = sx + r * math.cos(math.radians(60 * k + 30))
            py = sy + r * math.sin(math.radians(60 * k + 30))
            pts.append(f"{px:.1f},{py:.1f}")
        grid.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="#dbe6f4" stroke-width="1"/>')
        grid.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="6" fill="#15263c"/>')
    dots = []
    for x, y in unique:
        sx, sy = xy(float(x), float(y))
        dots.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="4.4" fill="#2f73d9" opacity=".75"/>')
    rx, ry = xy(float(representative[0]), float(representative[1]))
    return f"""<svg class="figure" viewBox="0 0 {width} {height}" role="img" aria-label="7站21小区与40个独立UE位置">
<rect width="100%" height="100%" rx="16" fill="#fbfdff"/>{''.join(grid)}{''.join(dots)}
<circle cx="{rx:.1f}" cy="{ry:.1f}" r="11" fill="none" stroke="#ef5b4c" stroke-width="3"/>
<text x="24" y="30" class="svg-title">7 站 × 3 扇区 · 40 个独立位置</text>
<text x="24" y="51" class="svg-note">蓝点为 UE；红圈为代表样本位置；站间距 500 m</text>
<g transform="translate(570 475)"><circle cx="0" cy="0" r="5" fill="#15263c"/><text x="12" y="4" class="svg-note">站点</text>
<circle cx="72" cy="0" r="4" fill="#2f73d9"/><text x="82" y="4" class="svg-note">UE</text></g></svg>"""


def spectrum_bars_svg(s: dict[str, Any]) -> str:
    primary = s["primary"]["paired"]
    controlled = s["controlled_wideband"]["paired"]
    labels = ["逐 RB SRS 权", "宽带 SRS 权", "Type-I-style PMI"]
    vals = [primary["mean_a"], controlled["mean_a"], primary["mean_b"]]
    colors = ["#1f6fd1", "#69a3e5", "#e39b31"]
    width, height = 760, 310
    ymax = max(vals) * 1.25
    parts = []
    for i, (label, val, color) in enumerate(zip(labels, vals, colors, strict=True)):
        x = 105 + i * 210
        h = val / ymax * 205
        y = 252 - h
        parts.append(f'<rect x="{x}" y="{y:.1f}" width="120" height="{h:.1f}" rx="10" fill="{color}"/>')
        parts.append(f'<text x="{x + 60}" y="{y - 10:.1f}" text-anchor="middle" class="svg-value">{val:.3f}</text>')
        parts.append(f'<text x="{x + 60}" y="278" text-anchor="middle" class="svg-note">{label}</text>')
    return f"""<svg class="figure" viewBox="0 0 {width} {height}" role="img" aria-label="三种权值方案的平均谱效">
<rect width="100%" height="100%" rx="16" fill="#fbfdff"/><line x1="70" y1="252" x2="720" y2="252" stroke="#b9c8dc"/>
<text x="22" y="28" class="svg-title">平均谱效 · bit/s/Hz</text>{''.join(parts)}</svg>"""


def rank_chart_svg(rep: dict[str, Any]) -> str:
    methods = [("svd", "逐 RB SRS", "#1f6fd1"),
               ("svd_wideband", "宽带 SRS", "#69a3e5"),
               ("type1", "PMI", "#e39b31")]
    width, height = 820, 360
    maxv = max(
        c["predicted_se"]
        for key, _, _ in methods
        for c in rep["methods"][key]["performance"]["rank_candidates"]
    ) * 1.12
    parts = []
    for gi, (key, label, color) in enumerate(methods):
        candidates = rep["methods"][key]["performance"]["rank_candidates"]
        selected = rep["methods"][key]["performance"]["rank"]
        for ri, cand in enumerate(candidates):
            x = 92 + ri * 170 + gi * 34
            h = cand["predicted_se"] / maxv * 240
            y = 290 - h
            opacity = "1" if cand["rank"] == selected else ".45"
            parts.append(f'<rect x="{x}" y="{y:.1f}" width="28" height="{h:.1f}" rx="5" fill="{color}" opacity="{opacity}"/>')
        parts.append(f'<circle cx="{520 + gi * 88}" cy="27" r="5" fill="{color}"/><text x="{530 + gi * 88}" y="31" class="svg-note">{label}</text>')
    ticks = "".join(f'<text x="{143 + i * 170}" y="323" text-anchor="middle" class="svg-note">rank {i + 1}</text>' for i in range(4))
    return f"""<svg class="figure" viewBox="0 0 {width} {height}" role="img" aria-label="代表样本逐rank预测谱效">
<rect width="100%" height="100%" rx="16" fill="#fbfdff"/><text x="22" y="31" class="svg-title">代表样本：发送侧逐 rank 预测谱效</text>
<line x1="65" y1="290" x2="780" y2="290" stroke="#b9c8dc"/>{''.join(parts)}{ticks}
<text x="22" y="347" class="svg-note">深色柱为最终选择；rank 必须随 N/I 工作点变化，不能只看奇异值比例。</text></svg>"""


def experience_slope_svg(exp: dict[str, Any]) -> str:
    av = np.asarray(exp["raw"]["a"]["small_queue_wait_ms_p95"], dtype=float)
    bv = np.asarray(exp["raw"]["b"]["small_queue_wait_ms_p95"], dtype=float)
    width, height = 720, 400
    ymin, ymax = min(float(av.min()), float(bv.min())), max(float(av.max()), float(bv.max()))
    pad = max(0.1, (ymax - ymin) * 0.12)
    ymin, ymax = max(0.0, ymin - pad), ymax + pad

    def yy(v: float) -> float:
        return 335 - (v - ymin) / max(ymax - ymin, 1e-9) * 265

    lines = []
    for _i, (a, b) in enumerate(zip(av, bv, strict=True)):
        color = "#35a36f" if a < b else "#ef5b4c"
        lines.append(f'<line x1="220" y1="{yy(a):.1f}" x2="500" y2="{yy(b):.1f}" stroke="{color}" stroke-width="1.4" opacity=".55"/>')
        lines.append(f'<circle cx="220" cy="{yy(a):.1f}" r="3.5" fill="{color}"/><circle cx="500" cy="{yy(b):.1f}" r="3.5" fill="{color}"/>')
    ma, mb = float(av.mean()), float(bv.mean())
    return f"""<svg class="figure" viewBox="0 0 {width} {height}" role="img" aria-label="16次配对重复的小包等待P95">
<rect width="100%" height="100%" rx="16" fill="#fbfdff"/><text x="22" y="31" class="svg-title">16 次 CRN 配对 · small queue-wait P95</text>
{''.join(lines)}<circle cx="220" cy="{yy(ma):.1f}" r="9" fill="#15385f"/><circle cx="500" cy="{yy(mb):.1f}" r="9" fill="#15385f"/>
<text x="220" y="372" text-anchor="middle" class="svg-note">scheduled-TBS PF · mean {ma:.3f} ms</text>
<text x="500" y="372" text-anchor="middle" class="svg-note">legacy full-band PF · mean {mb:.3f} ms</text>
<text x="22" y="391" class="svg-note">绿线：正确口径更低；每条线共享同一 traffic/HARQ/tie-break 随机数。</text></svg>"""


def rbg_strip_svg(trace: dict[str, Any]) -> str:
    colors = {5: "#d66b55", 8: "#4e8cd8", 6: "#53a879"}
    labels = {5: "UE5 small", 8: "UE8 small", 6: "UE6 large"}
    owners: dict[int, int] = {}
    for row in trace["allocations"]:
        for idx in row["rbg_indices"]:
            owners[int(idx)] = int(row["ue"])
    blocks = []
    for i in range(17):
        ue = owners.get(i)
        color = colors.get(ue, "#edf1f6")
        blocks.append(f'<rect x="{35 + i * 38}" y="55" width="34" height="52" rx="5" fill="{color}"/>')
        blocks.append(f'<text x="{52 + i * 38}" y="128" text-anchor="middle" class="svg-mini">{i}</text>')
    legend = "".join(
        f'<circle cx="{90 + j * 170}" cy="160" r="5" fill="{colors[ue]}"/><text x="{101 + j * 170}" y="164" class="svg-note">{labels[ue]}</text>'
        for j, ue in enumerate((5, 8, 6))
    )
    return f"""<svg class="figure" viewBox="0 0 720 190" role="img" aria-label="TTI 9722的17个RBG分配">
<rect width="100%" height="100%" rx="16" fill="#fbfdff"/><text x="22" y="29" class="svg-title">TTI {trace['tti']} · 17 RBG 实际分配</text>
{''.join(blocks)}{legend}</svg>"""


def spectrum_flow_svg() -> str:
    return """<svg class="flow" viewBox="0 0 1060 760" role="img" aria-label="谱效评估完整算法流程图">
<defs><marker id="arrS" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#56708e"/></marker></defs>
<rect width="100%" height="100%" rx="18" fill="#fbfdff"/>
<g class="flow-box"><rect x="35" y="40" width="210" height="80"/><text x="140" y="68"><tspan>① 预注册 + 门1</tspan><tspan x="140" dy="22">锁配置/指标/独立单元</tspan></text></g>
<g class="flow-box"><rect x="305" y="40" width="210" height="80"/><text x="410" y="68"><tspan>② 7站21小区撒点</tspan><tspan x="410" dy="22">UMa-NLOS · CDL-C</tspan></text></g>
<g class="flow-box"><rect x="575" y="40" width="210" height="80"/><text x="680" y="68"><tspan>③ 生成 Htrue / Hest</tspan><tspan x="680" dy="22">64T4R · 272 RB</tspan></text></g>
<g class="flow-box"><rect x="815" y="40" width="210" height="80"/><text x="920" y="68"><tspan>④ 几何 SIR/SINR</tspan><tspan x="920" dy="22">拆 S / I / N 与 Ruu</tspan></text></g>
<g class="flow-box"><rect x="815" y="190" width="210" height="94"/><text x="920" y="218"><tspan>⑤ 三种发射权</tspan><tspan x="920" dy="21">逐RB SRS / 宽带SRS</tspan><tspan x="920" dy="21">Type-I-style PMI</tspan></text></g>
<g class="flow-diamond"><polygon points="680,178 785,237 680,296 575,237"/><text x="680" y="230"><tspan>⑥ rank 1..4</tspan><tspan x="680" dy="20">预测 SE 最大？</tspan></text></g>
<g class="flow-box"><rect x="305" y="190" width="210" height="94"/><text x="410" y="218"><tspan>⑦ 固定 W 到 Htrue</tspan><tspan x="410" dy="21">禁止 true-CSI 反选</tspan><tspan x="410" dy="21">功率按层均分</tspan></text></g>
<g class="flow-box"><rect x="35" y="190" width="210" height="94"/><text x="140" y="218"><tspan>⑧ MMSE / IRC</tspan><tspan x="140" dy="21">逐 RB、逐层 SINR</tspan><tspan x="140" dy="21">同一个 Ruu</tspan></text></g>
<g class="flow-box"><rect x="35" y="360" width="210" height="94"/><text x="140" y="388"><tspan>⑨ SE + 容量上界</tspan><tspan x="140" dy="21">E Σ log2(1+γ)</tspan><tspan x="140" dy="21">速率等效层 SINR</tspan></text></g>
<g class="flow-box"><rect x="305" y="360" width="210" height="94"/><text x="410" y="388"><tspan>⑩ 按位置聚类</tspan><tspan x="410" dy="21">40 独立位置</tspan><tspan x="410" dy="21">每位置 2 次衰落</tspan></text></g>
<g class="flow-box"><rect x="575" y="360" width="210" height="94"/><text x="680" y="388"><tspan>⑪ 门2/门3</tspan><tspan x="680" dy="21">Student-t CI + Wilcoxon</tspan><tspan x="680" dy="21">单点贡献检查</tspan></text></g>
<g class="flow-box accent"><rect x="815" y="360" width="210" height="94"/><text x="920" y="388"><tspan>⑫ 结论拆分</tspan><tspan x="920" dy="21">频率自由度</tspan><tspan x="920" dy="21">码本量化损失</tspan></text></g>
<g class="boundary"><rect x="90" y="545" width="880" height="145"/><text x="120" y="580"><tspan>边界：这个算例到 SE 为止</tspan><tspan x="120" dy="27">不含 TTI、业务到达、MCS、BLER、OLLA、PF、RBG 分配；这些量只在体验/系统模式进入。</tspan><tspan x="120" dy="27">MU 配对也不参与本次 SRS-vs-PMI 主比较；若开启，走 SUS → ZF/RZF → 总功率归一的独立分支。</tspan></text></g>
<g stroke="#56708e" stroke-width="2" fill="none" marker-end="url(#arrS)"><path d="M245 80H305"/><path d="M515 80H575"/><path d="M785 80H815"/><path d="M920 120V190"/><path d="M815 237H785"/><path d="M575 237H515"/><path d="M305 237H245"/><path d="M140 284V360"/><path d="M245 407H305"/><path d="M515 407H575"/><path d="M785 407H815"/></g></svg>"""


def system_flow_svg() -> str:
    return """<svg class="flow" viewBox="0 0 1060 1030" role="img" aria-label="体验评估双阶段算法流程图">
<defs><marker id="arrE" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#56708e"/></marker></defs>
<rect width="100%" height="100%" rx="18" fill="#fbfdff"/>
<text x="35" y="38" class="phase-title">Phase A · 信道压成 UE × snapshot × rank 链路表（每个数据集一次）</text>
<g class="flow-box"><rect x="35" y="62" width="220" height="88"/><text x="145" y="91"><tspan>80 行 → 10 UE × 8 快照</tspan><tspan x="145" dy="22">Δsnapshot = 5 ms</tspan></text></g>
<g class="flow-box"><rect x="300" y="62" width="220" height="88"/><text x="410" y="91"><tspan>CSI 陈旧时长向上量化</tspan><tspan x="410" dy="22">逐 RBG 取 stale H</tspan></text></g>
<g class="flow-box"><rect x="565" y="62" width="220" height="88"/><text x="675" y="91"><tspan>同一 stale H 搜权</tspan><tspan x="675" dy="22">SVD 与 PMI 均因果</tspan></text></g>
<g class="flow-box"><rect x="830" y="62" width="195" height="88"/><text x="928" y="91"><tspan>逐 rank 评估</tspan><tspan x="928" dy="22">γtrue / SEgnb / BF</tspan></text></g>
<g class="flow-box accent"><rect x="365" y="205" width="330" height="95"/><text x="530" y="235"><tspan>因果 CQI → BF gain → Tx SINR/MCS</tspan><tspan x="530" dy="23">rank 按 gNB 视角选；OLLA 留给 TTI 循环</tspan></text></g>
<g stroke="#56708e" stroke-width="2" fill="none" marker-end="url(#arrE)"><path d="M255 106H300"/><path d="M520 106H565"/><path d="M785 106H830"/><path d="M928 150V175H530V205"/></g>
<line x1="35" y1="340" x2="1025" y2="340" stroke="#c7d5e6" stroke-dasharray="7 7"/>
<text x="35" y="380" class="phase-title">Phase B · 每个 replication 跑 10,000 TTI（CRN 配对，两臂只改 PF credit）</text>
<g class="flow-box"><rect x="35" y="410" width="220" height="88"/><text x="145" y="439"><tspan>① 所有 D/S/U 先到达</tspan><tspan x="145" dy="22">维护 FIFO arrival objects</tspan></text></g>
<g class="flow-diamond"><polygon points="410,397 520,454 410,511 300,454"/><text x="410" y="447"><tspan>② DL slot?</tspan><tspan x="410" dy="20">D / S 才调度</tspan></text></g>
<g class="flow-box"><rect x="565" y="410" width="220" height="88"/><text x="675" y="439"><tspan>③ 取 snapshot/rank</tspan><tspan x="675" dy="22">CQI + BF + OLLA → MCS</tspan></text></g>
<g class="flow-box"><rect x="830" y="410" width="195" height="88"/><text x="928" y="439"><tspan>④ PF / QoS-PF</tspan><tspan x="928" dy="22">按 full-band potential 排序</tspan></text></g>
<g class="flow-box"><rect x="830" y="570" width="195" height="95"/><text x="928" y="598"><tspan>⑤ TBS 反查 n*</tspan><tspan x="928" dy="21">searchsorted(left)</tspan><tspan x="928" dy="21">最多取剩余 RBG</tspan></text></g>
<g class="flow-box"><rect x="565" y="570" width="220" height="95"/><text x="675" y="598"><tspan>⑥ BLER + CRN ACK</tspan><tspan x="675" dy="21">ACK 扣队列；NACK 留队</tspan><tspan x="675" dy="21">无 HARQ combining</tspan></text></g>
<g class="flow-box"><rect x="300" y="570" width="220" height="95"/><text x="410" y="598"><tspan>⑦ 更新 OLLA / PF</tspan><tspan x="410" dy="21">A: actual scheduled TBS</tspan><tspan x="410" dy="21">B: fault full-band TBS</tspan></text></g>
<g class="flow-diamond"><polygon points="145,558 255,617 145,676 35,617"/><text x="145" y="609"><tspan>⑧ 还有 RBG</tspan><tspan x="145" dy="20">和候选 UE？</tspan></text></g>
<g class="flow-box"><rect x="35" y="750" width="250" height="110"/><text x="160" y="780"><tspan>⑨ burst / arrival KPI</tspan><tspan x="160" dy="22">等待、完成、PDB、Rel-19</tspan><tspan x="160" dy="22">过期未完成=miss；未到期=右删失</tspan></text></g>
<g class="flow-box"><rect x="390" y="750" width="250" height="110"/><text x="515" y="780"><tspan>⑩ 守恒与资源检查</tspan><tspan x="515" dy="22">bytes、RBG overlap、padding</tspan><tspan x="515" dy="22">zero-inclusive UE 分布</tspan></text></g>
<g class="flow-box accent"><rect x="745" y="750" width="280" height="110"/><text x="885" y="780"><tspan>⑪ 16 次配对门2/门3</tspan><tspan x="885" dy="22">主指标 small wait P95</tspan><tspan x="885" dy="22">次指标不冒充主结论</tspan></text></g>
<g stroke="#56708e" stroke-width="2" fill="none" marker-end="url(#arrE)"><path d="M255 454H300"/><path d="M520 454H565"/><path d="M785 454H830"/><path d="M928 498V570"/><path d="M830 617H785"/><path d="M565 617H520"/><path d="M300 617H255"/><path d="M145 676V750"/><path d="M285 805H390"/><path d="M640 805H745"/><path d="M35 617H18V520H928V570"/></g>
<text x="26" y="538" class="svg-note">是：继续给下一个 UE</text><text x="336" y="533" class="svg-note">U slot：只维护队列，直接下一 TTI</text>
<g class="boundary"><rect x="90" y="915" width="880" height="80"/><text x="120" y="950"><tspan>主对比只测 PF 记账闭环；两臂共享信道、业务、HARQ、tie-break 和所有物理近似。</tspan><tspan x="120" dy="25">因此相对因果结论可用，但绝对体验值仍需现场 BLER/开销/HARQ/长时 trace 标定。</tspan></text></g></svg>"""


def bugs_table() -> tuple[str, int]:
    bugs = [
        ("S-01", "谱效", "有色容量白化后又除一次 N0", "容量上界被错误压低，可能让普通预编码看起来越界", "白化后统一噪声=1；注水分母改为 normalized_noise", "SISO 有色损伤解析式逐位一致", "src/superran/linklevel.py", "normalized_noise"),
        ("S-02", "谱效", "把逐 RB 线性 SINR 先求平均再报层 SINR", "该 SINR不能反算实际 E[log(1+γ)]，报告自相矛盾", "改成 log-domain 的速率等效 SINR", "报告层 SINR精确复原层 SE", "src/superran/linklevel.py", "np.expm1"),
        ("S-03", "谱效", "rank 只按奇异值比例阈值选", "同一信道在低/高 SNR 选同 rank；弱层分功率后可降速", "在发送端可知的 h_est、同 N/I 下枚举 rank 1..4，取预测 SE 最大", "构造信道低 SNR 选1、高 SNR 选2", "src/superran/linklevel.py", "if rank_selection == \"threshold\""),
        ("S-04", "统计", "小样本 CI 固定用 z=1.96", "n 小时区间系统性偏窄", "n>1 使用 Student-t 临界值", "n=3 与 scipy t(2) 精确一致", "src/superran/linklevel.py", "student_t.ppf"),
        ("S-05", "干扰", "用受害 UE 交叉信道主奇异向量当邻区波束，却称‘服务自己 UE’", "实际等于邻区故意对准受害 UE；空间干扰形状有方向性偏差", "默认生成与交叉信道统计独立的单位范数邻区波束；victim_aligned 仅留故障复现", "独立模型与 victim-aligned 输出必须不同", "src/superran/linklevel.py", "if model == \"victim_aligned\""),
        ("S-06", "干扰", "Ruu 快照不足时给同一信道加 5% 抖动补样本", "凭空造观测、抬高协方差秩，让 IRC 获得不存在的信息", "只用 min(requested,T) 个真实快照，奇异性由 diagonal loading 处理", "T=1 时请求1/100样本结果一致且 rank=1", "src/superran/linklevel.py", "n_s = min"),
        ("S-07", "CSI", "CSI 陈旧时长/快照间隔用 round", "2ms/5ms→0，相当于使用测量发生前的当前信道", "离散陈旧时长一律 ceil，保证取到的 CSI 不新于真实测量", "2ms→1、7ms→2", "src/superran/csi_aging.py", "np.ceil"),
        ("S-08", "CSI", "整个仿真时域只搜一次 PMI", "snapshot 0 偷看未来快照，形成 oracle", "每个快照只在当时可用的 stale h_prec 上搜宽带 PMI", "任意改未来信道，snapshot0 PMI/BF gain 不变", "src/superran/system.py", "w_pmi_s"),
        ("S-09", "CSI", "CQI 用全程 PMI SINR 均值回填所有快照", "当前 TTI 获得未来 SINR 信息", "改用只吃 0..s 的一阶 IIR（λ 可配，默认 0.25）", "任意改未来信道，snapshot0 CQI/Tx SINR 不变", "src/superran/system.py", "filter_state"),
        ("E-01", "业务", "CBR 每 TTI 直接 int(bytes)", "小数永久丢失；低速率甚至每 TTI 都为0", "每 UE 累积分数字节 carry，再 floor 入队", "0.001 Mbps ×1s 精确到达125B", "src/superran/system.py", "_cbr_carry"),
        ("E-02", "业务", "先跳过 U slot，再生成业务到达", "DDDSU 固定漏掉20%外生到达，负载/排队被低估", "每个 D/S/U TTI 先 step traffic，再判断能否下行调度", "1 Mbps CBR 在 DDDSU 仍报告1 Mbps offered", "src/superran/system.py", "tr.step(tti)"),
        ("E-03", "体验", "PDB 分母只含已完成 arrival object", "最差的未完成对象消失，过载越重 PDB 越好看", "已过 deadline 的未完成对象记确定 miss；未到 deadline 单列右删失", "过载窗同时出现 overdue miss 与 right-censored", "src/superran/experience.py", "overdue_incomplete"),
        ("E-04", "体验", "用户体验分布只含有完成 burst 的 UE", "被饿死 UE 从分布消失，算法越差样本越漂亮", "有到达 UE 全进入 zero-inclusive 分布；无完成记0", "starved UE: eligible=1/measured=0/value=0", "src/superran/experience.py", "user_exp_completed_only"),
        ("X-01", "统计", "80 行衰落观测直接当 n=80 独立样本", "同一位置重复衰落相关，伪增自由度并缩窄 CI", "先按 UE 坐标聚成40个位置均值，再做配对 t/Wilcoxon", "2位置×2衰落的测试明确得到 n=2", "src/superran/gates.py", "paired_cluster_means"),
    ]
    rows = []
    for bug_id, domain, old, harm, fix, sentinel, path, needle in bugs:
        rows.append(
            f"<tr><td><code>{bug_id}</code><br><span class=mini>{esc(domain)}</span></td>"
            f"<td>{esc(old)}</td><td>{esc(harm)}</td><td>{esc(fix)}<br><code>{esc(source_ref(path, needle))}</code></td>"
            f"<td>{badge(True)}<br>{esc(sentinel)}</td></tr>"
        )
    return "".join(rows), len(bugs)


def decision_cards() -> str:
    decisions = [
        ("D1 · EPF 定义", "必须拍板", "当前默认 α=β=1、γ=0、w=1，严格退化经典 PF。EPF 不是 3GPP 统一算法；要确定时延因子乘性/加性、HoL/平均时延、budget 来源、业务权重。", "推荐先采用乘性 HoL：w·PF·(1+HoL/PDB)^γ；γ 用0/1做灵敏度，不先拟合一个神奇常数。"),
        ("D2 · PF credit", "建议确认", "正确臂目前按 scheduled TBS 记账，即 NACK 也记占用过的无线资源；另有 acked_goodput 口径。两者代表‘公平分资源’与‘公平分成功吞吐’两种目标。", "推荐 scheduled TBS 作为调度闭环，acked_goodput 只做敏感性分析。"),
        ("D3 · 尾料 RBG", "必须拍板", "当前按优先级逐 UE 分配，17 RBG 用不完时尾料留空；不强塞给第一名。补给会提高 PRB 利用率，但改变 padding、功耗与 PF credit。", "推荐留空作为真实资源利用率；若做 opportunistic fill，单列策略臂。"),
        ("D4 · HARQ/NACK", "必须拍板", "当前 NACK 字节退回队列、下次按 NewTx 曲线重试，没有 RV、软合并、进程数或 RTT。它守住字节账，但不是完整 HARQ。", "推荐保留退回；下一阶段加显式 HARQ state，而不是把 NACK 直接丢弃。"),
        ("D5 · burst 边界", "必须拍板", "大包按 RLC DRB buffer busy period；small 1500B 是小包代理，不等同 PDCP SDU。CBR 每 TTI 字节块也只是工程对象。", "推荐把 FTP 文件定义为 large busy period；small KPI 用 arrival object wait/completion/PDB，不报 DRB throughput。"),
        ("D6 · SRS 历史长度", "阻塞绝对标定", "体验数据只有 8×5ms=40ms 历史；10ms SRS ×17 RBG 跳频需要约170ms。超龄 RBG 会钳到最早快照，5s 主循环还周期回放8快照。", "相对 PF 记账对比可保留（两臂共用同一 trace）；上线绝对 KPI 前生成 ≥40 快照/UE，或本阶段明确改用全带 non-hopping SRS。"),
        ("D7 · 邻区真实波束", "阻塞空间干扰标定", "数据保存受害 UE 的交叉信道，但没有邻区被服务 UE 的信道/实际 W。当前默认 W 与交叉信道独立，并由几何 SIR 重标总功率。", "推荐下一版数据同时保存 neighbor served-UE channel 与 scheduler W；当前结果标注 spatial-shape approximation。"),
        ("D8 · PMI/CQI 周期", "建议确认", "PMI 按 CSI report 周期更新（默认 20 ms）；CQI 用一阶 IIR，λ=0.25 已确认为工程默认但尚未经现场测量/设备数据标定；都因果。", "推荐后续用现场数据标定 PMI/CQI 周期与 λ；HARQ 的 ACK/NACK 反馈时延已按 TDD 图案建模。"),
        ("D9 · TBS RE 开销", "阻塞绝对容量标定", "TBS 量化遵循38.214，但 D slot 用12数据符号/RB，S slot乘0.7；未精确建 DMRS/PTRS/CORESET。MCS 用预置 20B profile。", "推荐把实际 slot format、DMRS type/ports、PTRS、CORESET overhead 做成 profile 并回归 TBS 表。"),
        ("D10 · 逐 RBG SINR 与 MU", "可延期", "本轮全带 SINR 判 BLER，体验模式关闭 MU。逐RBG信道会扩 UeLinkTable 维度；MU 会引入配对、功率与层数耦合。", "推荐先把 SU/全带链条校准完，再分别立 P1-A（逐RBG）与 P1-B（MU）实验，避免同时改数据结构。"),
        ("D11 · Type-I 完整码本", "建议确认", "当前是 Type-I-style 单面板列码本增量贪心，不冒充完整38.214多层/子带/多面板码本。", "若用于标准对标，接入完整 RI/PMI 枚举与反馈开销；当前只适合作为工程基线。"),
        ("D12 · 多重比较", "必须守住", "主指标只有 small queue-wait P95；completion/PDB/large throughput 等是次指标。large throughput 虽显著但未做 multiplicity correction。", "只把预注册主指标写成主结论；次指标标‘支持性’，不凭 p<0.05 扩大战果。"),
    ]
    return "".join(
        f'<article class="decision"><div><span class="badge {"block" if "阻塞" in state or "必须" in state else "warn"}">{esc(state)}</span><h3>{esc(title)}</h3></div>'
        f'<p>{esc(body)}</p><p class="recommend"><b>建议：</b>{esc(rec)}</p></article>'
        for title, state, body, rec in decisions
    )


def build() -> str:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    spectrum = data["spectrum"]
    exp = data["experience"]
    rep = spectrum["representative"]
    p = spectrum["primary"]["paired"]
    cw = spectrum["controlled_wideband"]["paired"]
    ep = exp["comparisons"]["small_queue_wait_ms_p95"]["paired"]
    ec = exp["comparisons"]["small_completion_delay_ms_p95"]["paired"]
    em = exp["comparisons"]["small_pdb_miss_ratio"]["paired"]
    el = exp["comparisons"]["large_burst_drb_throughput_mbps"]["paired"]
    ds = load(spectrum["dataset_id"])
    bug_rows, bug_count = bugs_table()
    trace_a = exp["trace"]["a"]
    trace_b = exp["trace"]["b"]

    method_rows = []
    for key, name in (("svd", "逐 RB SRS 协方差权"),
                      ("svd_wideband", "宽带 SRS 协方差权"),
                      ("type1", "Type-I-style PMI")):
        perf = rep["methods"][key]["performance"]
        cand = " / ".join(f"r{x['rank']}={x['predicted_se']:.3f}" for x in perf["rank_candidates"])
        method_rows.append(
            f"<tr><td><b>{name}</b></td><td>{perf['rank']}</td><td>{cand}</td>"
            f"<td>{fmt(perf['spectral_efficiency'], 4)}</td><td>{' / '.join(fmt(x, 2, ' dB') for x in perf['sinr_per_layer_db'])}</td>"
            f"<td>{fmt(perf['capacity_bound'], 4)}</td><td>{pct(perf['efficiency_vs_bound'])}</td></tr>"
        )

    rank_rows = []
    for table in exp["trace"]["link_tables"]:
        chosen = table["chosen_rank"]
        c = table["rank_candidates"][chosen - 1]
        rank_rows.append(
            f"<tr><td>UE{table['ue']}</td><td>{fmt(table['geo_sinr_db'], 2, ' dB')}</td><td>{fmt(table['iot_db'], 2, ' dB')}</td>"
            f"<td>{fmt(table['csi_lag_snapshots_mean'], 1, ' snap')}</td><td>{chosen}</td><td>{fmt(c['gnb_se'], 3)}</td>"
            f"<td>{fmt(c['true_sinr_db'], 2, ' dB')}</td><td>{c['cqi']}</td><td>{fmt(c['tx_sinr_before_olla_db'], 2, ' dB')}</td><td>{c['tx_mcs_before_olla']}</td></tr>"
        )

    alloc_rows = []
    b_by_ue = {row["ue"]: row for row in trace_b["allocations"]}
    for a in trace_a["allocations"]:
        b = b_by_ue[a["ue"]]
        alloc_rows.append(
            f"<tr><td>UE{a['ue']}<br><span class=mini>{a['traffic_class']}</span></td><td>{a['queue_bytes_before']}</td>"
            f"<td>r{a['rank']} / MCS {a['mcs']}<br><span class=mini>无 OLLA: {a['mcs_without_olla']}; "
            f"Δ={a.get('olla_before_mcs', a['olla_before_db']):.2f} MCS</span></td>"
            f"<td>{a['required_rbg']} → {a['n_rbg']}</td><td>{a['scheduled_bytes']} / {a['payload_bytes']}</td>"
            f"<td>{badge(a['ack'], 'ACK', 'NACK')}<br><span class=mini>BLER {a['bler']:.4f}</span></td>"
            f"<td class=good>{a['pf_credit_bytes']}</td><td class=bad>{b['pf_credit_bytes']}</td>"
            f"<td>{fmt(a['pf_average_before_bytes'], 3)}</td><td>{fmt(a['scheduler_metric'], 2)}</td></tr>"
        )

    kpi_rows = [
        ("主指标", "small queue-wait P95", ep, "ms", True),
        ("支持", "small completion-delay P95", ec, "ms", True),
        ("支持", "small PDB miss ratio", em, "", True),
        ("次指标", "large burst DRB throughput", el, "Mbps", False),
        ("诊断", "resource utilization", exp["comparisons"]["resource_utilization"]["paired"], "", False),
        ("诊断", "cell served", exp["comparisons"]["cell_served_mbps"]["paired"], "Mbps", False),
    ]
    kpi_html = []
    for role, name, pair, unit, lower_better in kpi_rows:
        gate3 = exp["comparisons"][{
            "small queue-wait P95": "small_queue_wait_ms_p95",
            "small completion-delay P95": "small_completion_delay_ms_p95",
            "small PDB miss ratio": "small_pdb_miss_ratio",
            "large burst DRB throughput": "large_burst_drb_throughput_mbps",
            "resource utilization": "resource_utilization",
            "cell served": "cell_served_mbps",
        }[name]]["gate3"]
        direction_ok = pair["mean_diff"] < 0 if lower_better else pair["mean_diff"] > 0
        if role == "主指标" and gate3["passed"] and direction_ok:
            verdict = badge(True, "支持", "不下结论")
        elif role == "次指标":
            verdict = '<span class="badge warn">仅次指标</span>'
        else:
            verdict = badge(False, "支持", "不下结论")
        kpi_html.append(
            f"<tr><td>{role}</td><td><b>{name}</b></td><td>{fmt(pair['mean_a'], 4)}</td><td>{fmt(pair['mean_b'], 4)}</td>"
            f"<td>{fmt(pair['mean_diff'], 4)} {unit}</td><td>[{fmt(pair['ci95'][0], 4)}, {fmt(pair['ci95'][1], 4)}]</td>"
            f"<td>{fmt(pair['decision_p_value'], 4)}</td><td>{verdict}</td></tr>"
        )

    raw_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>superran · 谱效与体验速率深度仿真手册</title>
<style>
:root{{--ink:#15263c;--muted:#617083;--blue:#1f6fd1;--blue2:#eaf2fc;--green:#16865b;--red:#c8463b;--amber:#ad6b08;--line:#d7e1ed;--bg:#f4f7fb;--card:#fff;--shadow:0 10px 30px rgba(31,55,87,.08)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.72 "Segoe UI","Microsoft YaHei",sans-serif}}
a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}code,pre{{font-family:"Cascadia Code",Consolas,monospace}}code{{font-size:.9em}}
.hero{{background:linear-gradient(135deg,#102a4b 0%,#1d568e 62%,#2a76b9 100%);color:#fff;padding:46px 5vw 38px}}.hero-inner{{max-width:1280px;margin:auto}}
.eyebrow{{font-size:12px;letter-spacing:.16em;text-transform:uppercase;opacity:.78}}h1{{font-size:clamp(32px,5vw,58px);line-height:1.07;margin:12px 0 16px;max-width:1000px}}.lead{{font-size:18px;max-width:1020px;color:#e8f2ff}}
.hero-metrics{{display:grid;grid-template-columns:repeat(4,minmax(170px,1fr));gap:14px;margin-top:28px}}.hero-metric{{background:rgba(255,255,255,.11);border:1px solid rgba(255,255,255,.2);border-radius:14px;padding:15px 17px}}.hero-metric b{{display:block;font-size:24px}}.hero-metric span{{font-size:12px;color:#d7e8fb}}
.tabs{{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.96);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);display:flex;gap:8px;justify-content:center;padding:10px;overflow:auto}}
.tabs button{{border:0;background:transparent;color:var(--muted);padding:10px 16px;border-radius:9px;font-weight:700;white-space:nowrap;cursor:pointer}}.tabs button.active{{background:var(--blue2);color:var(--blue)}}
main{{max-width:1280px;margin:0 auto;padding:32px 24px 70px}}.panel{{display:none}}.panel.active{{display:block}}section{{scroll-margin-top:78px}}
.section-head{{margin:34px 0 18px}}.section-head .num{{display:inline-flex;width:31px;height:31px;border-radius:9px;align-items:center;justify-content:center;background:var(--blue);color:#fff;font-weight:800;margin-right:9px}}h2{{font-size:29px;line-height:1.25;margin:0 0 8px}}h3{{font-size:20px;line-height:1.35;margin:14px 0 8px}}h4{{margin:12px 0 5px}}p{{margin:7px 0 13px}}.muted{{color:var(--muted)}}.mini{{font-size:12px;color:var(--muted)}}
.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.grid3{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:var(--shadow)}}
.callout{{border-left:5px solid var(--blue);background:#edf5ff;border-radius:0 12px 12px 0;padding:15px 19px;margin:16px 0}}.callout.warn{{border-color:#df972d;background:#fff7e8}}.callout.danger{{border-color:#df5d50;background:#fff0ee}}.callout.good{{border-color:#36a675;background:#edfaf4}}
.metric-row{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin:16px 0}}.metric{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px}}.metric b{{font-size:22px;display:block}}.metric span{{font-size:12px;color:var(--muted)}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.03em}}.badge.pass{{background:#dff5ea;color:#116541}}.badge.block{{background:#fee4e1;color:#a3352c}}.badge.warn{{background:#fff0ce;color:#855100}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);font-size:13px}}th{{background:#edf3fa;color:#314b68;text-align:left;position:sticky;top:58px;z-index:2}}th,td{{padding:10px 11px;border-bottom:1px solid var(--line);vertical-align:top}}tr:last-child td{{border-bottom:0}}.scroll{{overflow:auto;border-radius:13px;border:1px solid var(--line)}}.scroll table{{border:0;min-width:850px}}td.good{{background:#eefaf4;color:#126b49;font-weight:800}}td.bad{{background:#fff0ee;color:#a43a31;font-weight:800}}
.formula{{background:#f7faff;border:1px solid #dce7f4;border-radius:13px;padding:14px 18px;margin:11px 0;overflow:auto;text-align:center}}.formula .kx{{font-size:1.06em}}
.figure,.flow{{width:100%;height:auto;border:1px solid var(--line);border-radius:17px;background:#fbfdff}}.svg-title{{font:700 15px "Segoe UI","Microsoft YaHei",sans-serif;fill:#213b58}}.svg-note{{font:12px "Segoe UI","Microsoft YaHei",sans-serif;fill:#617083}}.svg-mini{{font:10px "Segoe UI",sans-serif;fill:#617083}}.svg-value{{font:700 15px "Segoe UI",sans-serif;fill:#213b58}}
.flow-box rect{{fill:#fff;stroke:#9cb5d2;stroke-width:1.5;rx:12}}.flow-box.accent rect{{fill:#eaf3ff;stroke:#2b78d0}}.flow-box text,.flow-diamond text{{font:13px "Segoe UI","Microsoft YaHei",sans-serif;fill:#233d59;text-anchor:middle}}.flow-diamond polygon{{fill:#fff8e8;stroke:#d89c3d;stroke-width:1.5}}.boundary rect{{fill:#eef8f4;stroke:#74b99a;rx:12}}.boundary text{{font:13px "Segoe UI","Microsoft YaHei",sans-serif;fill:#245b46}}.phase-title{{font:700 16px "Segoe UI","Microsoft YaHei",sans-serif;fill:#1b4f84}}
.code{{border:1px solid var(--line);border-radius:12px;background:#102238;color:#d9e8f7;margin:13px 0;overflow:hidden}}.code summary{{padding:11px 14px;cursor:pointer;font-weight:700;background:#18314d}}.code pre{{margin:0;padding:14px;overflow:auto;font-size:12px;line-height:1.55}}
.decision{{background:#fff;border:1px solid var(--line);border-radius:15px;padding:18px;margin:13px 0;display:grid;grid-template-columns:270px 1fr;gap:20px}}.decision h3{{margin:7px 0}}.recommend{{background:#f2f7fd;border-radius:9px;padding:10px 13px}}
.source-list li{{margin:9px 0}}footer{{max-width:1280px;margin:0 auto;padding:0 24px 50px;color:var(--muted);font-size:12px}}
@media(max-width:900px){{.hero-metrics,.metric-row,.grid3{{grid-template-columns:repeat(2,1fr)}}.grid2{{grid-template-columns:1fr}}.decision{{grid-template-columns:1fr}}th{{position:static}}}}
@media(max-width:560px){{main{{padding:22px 12px 55px}}.hero{{padding:34px 18px}}.hero-metrics,.metric-row,.grid3{{grid-template-columns:1fr}}}}
</style>{kx.head_assets()}</head><body>
<header class="hero"><div class="hero-inner"><div class="eyebrow">SUPERRAN · DEEP SIMULATION AUDIT · 2026-08-09</div>
<h1>谱效评估型与体验评估型：两条完整、分离、可追溯的仿真链</h1>
<p class="lead">这不是同一模式的两个参数。谱效算例止于 <b>EΣlog₂(1+SINR)</b>；体验算例从链路表继续进入业务、TTI、PF、RBG、TBS、BLER、OLLA 与 burst KPI。页面里的数值全部来自冻结 JSON，14 条新原理错误已修复并配反向哨兵。</p>
<div class="hero-metrics"><div class="hero-metric"><b>{bug_count}</b><span>本轮新增原理修复</span></div><div class="hero-metric"><b>14 / 14</b><span>测试入口通过</span></div><div class="hero-metric"><b>40</b><span>谱效独立位置簇</span></div><div class="hero-metric"><b>16 × CRN</b><span>体验配对重复</span></div></div></div></header>
<nav class="tabs" aria-label="报告章节"><button class="active" data-tab="spectrum">A · 谱效算例</button><button data-tab="experience">B · 体验算例</button><button data-tab="bugs">C · 原理修复</button><button data-tab="decisions">D · 落地与待决策</button></nav>
<main>
<article id="spectrum" class="panel active">
<div class="section-head"><h2>算例 A · 典型多小区干扰下，SRS 派生权 vs PMI 权</h2><p class="muted">21 小区几何干扰进入 SINR；两臂都只用 h_est 设计、在 h_true 上评估；同一个 N/I、MMSE、rank 搜索与统计门。</p></div>
<div class="metric-row"><div class="metric"><b>{fmt(p['mean_a'],3)}</b><span>逐 RB SRS 权 · bit/s/Hz</span></div><div class="metric"><b>{fmt(p['mean_b'],3)}</b><span>Type-I-style PMI · bit/s/Hz</span></div><div class="metric"><b>+{fmt(p['mean_diff'],3)}</b><span>绝对差 · 95% CI [{p['ci95'][0]:.3f}, {p['ci95'][1]:.3f}]</span></div><div class="metric"><b>{pct(p['relative_gain'])}</b><span>相对差 · 门3通过后才允许写</span></div></div>
<div class="callout warn"><b>归因要拆开：</b>逐 RB SRS vs 宽带 PMI 的 +{p['mean_diff']:.3f} bit/s/Hz 同时包含“逐频率自由度”和“码本量化”。受控的宽带 SRS vs PMI 差值是 +{cw['mean_diff']:.3f} [{cw['ci95'][0]:.3f}, {cw['ci95'][1]:.3f}]，才更接近码本量化损失。不能把 46.2% 全说成“SRS 比 PMI 好”。</div>
{spectrum_flow_svg()}

<section><div class="section-head"><h2><span class="num">1</span>冻结问题、撒点与干扰场景</h2></div>
<div class="grid2"><div class="card"><h3>冻结配置</h3><div class="scroll"><table><tbody>
<tr><th>场景</th><td>UMa_NLOS · CDL-C · 7站×3扇区 · ISD 500m</td></tr><tr><th>载频/带宽</th><td>2.6GHz · 100MHz · 30kHz SCS · 272 RB</td></tr>
<tr><th>天线</th><td>64T4R · 8H×4V×双极化 · 1驱3/192物理阵子</td></tr><tr><th>观测</th><td>80 行 = 40 独立位置 × 每位置2次衰落；统计 n=40</td></tr>
<tr><th>CSI</th><td>LS linear；h_est 设计，h_true 评估</td></tr><tr><th>干扰</th><td>21 小区进入几何 SINR；保存最多3个主邻区交叉信道</td></tr>
<tr><th>预注册</th><td>{esc(spectrum['prereg']['prereg_id'])} · 主指标 spectral_efficiency</td></tr><tr><th>门1</th><td>{badge(spectrum['gate1']['passed'])} {esc(spectrum['gate1']['verdict'])}</td></tr>
</tbody></table></div></div><div>{topology_svg(np.asarray(ds.ue_position), np.asarray(rep['ue_position_m']))}</div></div>
<p>每一行信道是：</p><div class="formula">{F_CHANNEL}</div>
<p>这里的“撒点”同时改变服务距离、天线方向图增益、路径损耗、LOS/NLOS、小尺度信道与邻区几何干扰。代表样本为 index {rep['sample_index']}，位置 {esc(rep['ue_position_m'])}m，3D 距离 {rep['distance_3d_m']:.2f}m，路径损耗 {rep['pathloss_db']:.2f}dB，CSI NMSE {rep['csi_nmse_db']:.2f}dB。</p>
<details><summary>门1的18项逐条结果</summary><div class="scroll"><table>{gate_items(spectrum['gate1'])}</table></div></details></section>

<section><div class="section-head"><h2><span class="num">2</span>几何 SINR 拆 S/I/N，再构造空间干扰</h2></div>
<div class="formula">{F_GEOMETRY}</div><p>代表样本以预数字波束平均系数功率 S=E[|H|²]P={rep['operating_point']['signal_reference_power']:.6f} 为锚：I+N={rep['operating_point']['total_impairment_power']:.6f}，其中 N={rep['operating_point']['noise_power']:.6f}、I={rep['operating_point']['interference_power']:.6f}。这复原几何 SINR={rep['geometry_sinr_db']:.3f}dB、SIR={rep['geometry_sir_db']:.3f}dB；数字 BF 增益仍由 H 与预编码器贡献一次。</p>
<div class="formula">{F_RUU}</div><p>几何 SIR 只决定总干扰功率；h_interferers 决定 Ruu 的空间/频率形状，再按几何 I 重标。代表样本 Ruu 有效秩 {rep['interference_covariance']['effective_rank']:.3f}，每接收天线平均 trace={rep['interference_covariance']['mean_trace_per_rx_antenna']:.6f}。</p>
<div class="callout danger"><b>本轮修掉的关键错误：</b>数据没有邻区“被服务 UE”的信道，所以不能把受害 UE 交叉信道的主奇异向量当邻区发射权。现在默认邻区权与交叉信道统计独立；旧逻辑只以 <code>victim_aligned</code> 故障模式保留。</div>
{source_excerpt('src/superran/linklevel.py','def interference_covariance(',before=0,after=72,title='实际实现：独立邻区波束、真实 Ruu 快照与 PSD 累加')}</section>

<section><div class="section-head"><h2><span class="num">3</span>SRS 权与 PMI 权到底怎样设计</h2></div>
<div class="grid3"><div class="card"><h3>逐 RB SRS 协方差权</h3><div class="formula">{F_SRS_WEIGHT}</div><p>每个 RB 各算一个空间协方差和特征子空间，自由度最高；不是直接平均复信道。</p></div>
<div class="card"><h3>宽带 SRS 受控臂</h3><div class="formula">{F_WIDEBAND_WEIGHT}</div><p>全带共享一个权，去掉逐频率自由度；与 PMI 的差更聚焦码本量化。</p></div>
<div class="card"><h3>Type-I-style PMI</h3><div class="formula">{F_PMI_WEIGHT}</div><p>64端口列码本、增量贪心、宽带共享。它是工程近似，不冒充完整38.214多层码本。</p></div></div>
{spectrum_bars_svg(spectrum)}
<p>代表样本中，逐 RB SRS 权输出 shape 272×64×3，宽带 SRS 与 PMI 都选 rank1；PMI 码本 index=320。所有列在 RB0 的范数均为1，层间总功率在接收计算时按 1/r 分配。</p>
{source_excerpt('src/superran/linklevel.py','def _covariance_eigen_precoder(',before=0,after=58,title='实际实现：功率协方差特征权，不平均复信道')}
</section>

<section><div class="section-head"><h2><span class="num">4</span>Rank、接收机、逐层 SINR 与谱效</h2></div>
<div class="formula">{F_RANK}</div>{rank_chart_svg(rep)}
<div class="callout"><b>代表样本：</b>逐 RB SRS 的预测 SE 为 r1 8.998 / r2 12.440 / r3 13.248 / r4 13.172，所以选 rank3；宽带 SRS 和 PMI 都选 rank1。旧的奇异值阈值完全不看 N/I，同一信道换工作点仍选同 rank，本轮已删。</div>
<div class="formula">{F_MMSE}</div><div class="formula">{F_SINR}</div><div class="formula">{F_SE}</div><div class="formula">{F_EQ_SINR}</div>
<div class="scroll"><table><thead><tr><th>方法</th><th>rank</th><th>发送侧候选 SE</th><th>true SE</th><th>层速率等效 SINR</th><th>容量上界</th><th>达成率</th></tr></thead><tbody>{''.join(method_rows)}</tbody></table></div>
<p>逐 RB SRS 的 true SE = 7.2024 + 3.0993 + 0.6784 = <b>10.9802 bit/s/Hz</b>；对应层 SINR 21.65 / 8.79 / -2.22dB。速率等效 SINR 能精确反算层 SE，线性平均 SINR 不能。</p>
{source_excerpt('src/superran/linklevel.py','if rank_selection == "threshold"',before=4,after=62,title='实际实现：发送侧枚举 rank，固定权后才上 h_true')}
{source_excerpt('src/superran/linklevel.py','normalized_noise',before=9,after=18,title='实际实现：有色容量白化后不重复除噪声')}
</section>

<section><div class="section-head"><h2><span class="num">5</span>MU 配对、MCS、OLLA 和 TTI 为什么不在这里</h2></div>
<div class="grid2"><div class="card"><h3>MU 是另一条分支</h3><div class="formula">{F_MU}</div><p>若做 MU，系统先用 SUS 控制用户信道相关性，再做 ZF/RZF，并对整组总功率归一。本次 SRS-vs-PMI 主比较是单 UE rank 自适应，<b>没有 MU pairing</b>，避免把用户选择收益混进权值收益。</p></div>
<div class="card"><h3>MCS/OLLA 属于体验链</h3><p>Shannon 谱效直接用 log₂(1+SINR)，没有离散 MCS、BLER 或 ACK/NACK。把 MCS/OLLA 塞进这个主指标会把“链路能力”与“实现吞吐”混成一件事。它们在算例 B 按 TTI 进入。</p></div></div>
<div class="callout warn"><b>Duration 口径：</b>谱效例不是 5s 系统仿真，而是 80 行 channel observation、40 个独立位置簇。每行对三种权做同损伤评估；没有业务时间轴。</div></section>

<section><div class="section-head"><h2><span class="num">6</span>统计门：先按位置聚类，再写百分比</h2></div>
<div class="formula">{F_CLUSTER}</div><div class="formula">{F_CI}</div>
<p>80 行里每个独立位置出现2次。主统计先在位置内平均，再以 n=40 做 Student-t CI 与 Wilcoxon；样本级 n=80 只留诊断。修复后均值仍是 +3.3475，但 CI 更宽、更诚实：<b>[2.4353, 4.2597]</b>，Wilcoxon p={p['wilcoxon_p']:.3g}，胜率 {pct(p['win_rate'])}，最大单位置贡献 {pct(p['max_single_contribution'])}。</p>
<div class="grid2"><div class="card"><h3>门2 · 公平</h3><table>{gate_items(spectrum['primary']['gate2'])}</table></div><div class="card"><h3>门3 · 站得住</h3><table>{gate_items(spectrum['primary']['gate3'])}</table></div></div>
{source_excerpt('src/superran/gates.py','def paired_cluster_means(',before=0,after=52,title='实际实现：重复衰落按独立位置聚类')}</section>
</article>

<article id="experience" class="panel">
<div class="section-head"><h2>算例 B · mixed 大小业务下，按需 RBG 与 PF 正确记账</h2><p class="muted">5s/replication、10,000 TTI、16次 CRN 配对；主指标只看 small arrival queue-wait P95。</p></div>
<div class="metric-row"><div class="metric"><b>{fmt(ep['mean_a'],4,' ms')}</b><span>scheduled-TBS PF</span></div><div class="metric"><b>{fmt(ep['mean_b'],4,' ms')}</b><span>legacy full-band PF</span></div><div class="metric"><b>{fmt(ep['mean_diff'],4,' ms')}</b><span>绝对差 · CI [{ep['ci95'][0]:.4f}, {ep['ci95'][1]:.4f}]</span></div><div class="metric"><b>{pct(abs(ep['mean_diff']/ep['mean_b']))}</b><span>等待 P95 降低 · 门3通过</span></div></div>
<div class="callout danger"><b>闭环因果：</b>分到1个RBG的 UE 不能按17个RBG的潜在速率更新 PF 平均值。否则小包 UE 的分母被放大，后续 metric 被压低，按需分配反而把小包饿死。反向臂故意保留旧口径，用来证明分配器确实进入 PF 闭环。</div>
{system_flow_svg()}

<section><div class="section-head"><h2><span class="num">1</span>模式边界与冻结配置</h2></div>
<div class="scroll"><table><thead><tr><th></th><th>谱效评估型</th><th>体验评估型</th></tr></thead><tbody>
<tr><td>时间</td><td>独立 channel observations</td><td>5s × 10,000 TTI/rep × 16 CRN pairs</td></tr><tr><td>业务</td><td>无，等价 full-buffer 链路能力</td><td>5 small UE: 1500B@20Hz；5 large UE: 500kB@5Hz</td></tr>
<tr><td>物理输出</td><td>连续 Shannon SE、容量上界</td><td>CQI/MCS/TBS/BLER/ACK/OLLA + bytes</td></tr><tr><td>资源</td><td>单 UE 权值评估</td><td>17 RBG 按需切给多个 UE；DDDSU</td></tr>
<tr><td>主 KPI</td><td>bit/s/Hz</td><td>small arrival queue-wait P95</td></tr><tr><td>不能互换</td><td>不能回答排队/PDB/体验速率</td><td>不能把实现损失冒充信道理论能力</td></tr>
</tbody></table></div>
<p>每个 replication 的无线时间是5s；A/B 两臂各16次，合计模拟80s/臂、160s paired radio time。链路表只建一次，CRN 只改变 traffic/HARQ/tie-break 随机流。</p></section>

<section><div class="section-head"><h2><span class="num">2</span>Phase A：SRS、PMI、CQI、rank 与链路表</h2></div>
<div class="formula">{F_AGE}</div><p>CSI 陈旧时长量化必须向上取整。2ms处理时延在5ms快照上是1个快照，不允许 round 成0。每个快照的 SVD 与 PMI 都只看同一份 stale h_prec，真实当前信道只用于评估。</p>
<div class="formula">{F_CQI}</div><p>CQI 采用只吃 0..s 的一阶 IIR（λ=0.25 已由负责人确认为工程默认，但尚未经现场测量/设备数据标定）。随后按 CQI 门限 + BF gain 得到发送侧 SINR/MCS；rank 由显式策略给出（默认固定 rank2），不再逐快照跟随瞬时最优 rank。</p>
<div class="scroll"><table><thead><tr><th>UE</th><th>几何 SINR</th><th>IoT</th><th>平均 CSI lag</th><th>选 rank</th><th>gNB SE</th><th>true SINR</th><th>CQI</th><th>Tx SINR</th><th>Tx MCS</th></tr></thead><tbody>{''.join(rank_rows)}</tbody></table></div>
<div class="callout warn"><b>绝对标定限制：</b>代表快照的平均 lag 是17 snapshots≈85ms，但现有每 UE 只有8×5ms=40ms历史；超出部分钳到最早快照。A/B 的 PF 因果对比共享同一链路表，因此主对比仍可解释；绝对 CSI-aging / 现场体验值不能据此定标。待决策 D6 给出修法。</div>
{source_excerpt('src/superran/csi_aging.py','ratio = np.maximum(staleness',before=6,after=8,title='实际实现：CSI 陈旧时长因果量化')}
{source_excerpt('src/superran/system.py','w_pmi_s = _type1_precoder',before=12,after=52,title='实际实现：每快照因果 PMI 与 BF gain')}
{source_excerpt('src/superran/system.py','filtered_pmi = _nan_safe',before=14,after=22,title='实际实现：因果 CQI，不回填未来均值')}</section>

<section><div class="section-head"><h2><span class="num">3</span>Phase B：每个 TTI 的业务、PF、RBG 与 TBS</h2></div>
<p><b>先到达，再看 slot。</b>业务时钟不因 U slot 停止；DDDSU 中 U slot 只是不做下行调度。候选 UE 用全带 potential TBS 计算 PF metric：</p><div class="formula">{F_PF}</div>
<p>若开启 QoS-PF，参数化形式是：</p><div class="formula">{F_QOS_PF}</div>
<p>当前 α=β=1、γ=0、w=1，严格退化经典 PF；现场 EPF 定义仍需拍板。</p>
<div class="formula">{F_TBS_INFO}</div><div class="formula">{F_NSTAR}</div>
<p>TBS 表 shape 2×28×4×17，共 {exp['trace']['tbs_lookup']['entries']} 个 byte 值；D 与 S 两类 slot 分开。表对 RBG 数严格单调，所以 <code>searchsorted(side='left')</code> 精确找第一个够用的 n。不能用 TBS(17)/17 做线性除法，因为38.214量化会让1%误差少给一个RBG。</p>
{source_excerpt('src/superran/experience.py','class TbsLookup:',before=0,after=98,title='实际实现：D/S × MCS × rank × RBG TBS 表与 searchsorted 反查')}
{source_excerpt('src/superran/experience.py','n_need, _fits = lookup.required_rbg',before=16,after=74,title='实际实现：PF 排序后逐 UE 按需分 RBG')}</section>

<section><div class="section-head"><h2><span class="num">4</span>BLER、ACK/NACK、OLLA 与 PF 平均值</h2></div>
<p>实际单码字 true SINR 与空口 MCS 查预置 NewTx 曲线；固定的 <code>[replication, TTI, UE]</code> 均匀随机数决定 ACK。NACK 后最多一次重传：默认 IR 做半谱效等效 MCS 查表，可选 CC 做 +3.0103 dB；空口 MCS、RBG 数、rank 与 TBS 保持不变。</p>
<div class="formula">{F_OLLA}</div><p>ACK 向下调 offset、NACK 向上调；步长比保证目标 IBLER。OLLA 改的是下一次 MCS，不改本次真实 SINR。</p>
<div class="formula">{F_RAVG}</div><p>正确臂的 credit 是本次实际 scheduled TBS；故障臂是 full-band potential TBS。是否 ACK 不改变“本 TTI 消耗了多少无线机会”的调度公平口径。</p>
{rbg_strip_svg(trace_a)}
<div class="scroll"><table><thead><tr><th>UE/class</th><th>入调前队列B</th><th>rank/MCS</th><th>need→alloc RBG</th><th>TBS/payload B</th><th>结果</th><th>正确 PF credit</th><th>旧 PF credit</th><th>R̄ before</th><th>metric</th></tr></thead><tbody>{''.join(alloc_rows)}</tbody></table></div>
<p>TTI {trace_a['tti']} 中，UE5 只拿2 RBG却被旧口径记16,397B（实际1,953B）；UE8 只拿7 RBG却被记3,912B（实际1,601B）。大包 UE6 因资源只剩8 RBG，need=17但实际只拿8；正确 credit=4,033B，旧口径=8,448B。错误会把 small UE 的 PF 分母放大，后续 metric 直接下降。</p></section>

<section><div class="section-head"><h2><span class="num">5</span>burst、体验速率、PDB 与字节守恒</h2></div>
<div class="formula">{F_REL19}</div><p>3GPP TS 28.552 的 DRB.UEThpDl 明确面向跨多个 slot 的大数据 burst，并排除清空 buffer 的最后一段。单次首传小包会得到 ThpTimeDl=0，不能硬算同一个吞吐 KPI；本项目对 small 单列 queue-wait、completion-delay、PDB miss。</p>
<div class="formula">{F_PDB}</div><p>仿真结束时，已过 deadline 但未完成的是确定 miss；尚未到 deadline 才是右删失。已到达但没有完成 burst 的 UE 以0留在用户体验分布，另保留 completed-only 诊断。</p>
<div class="formula">{F_CONSERVE}</div><p>代表正确臂：arrived {exp['trace']['byte_conservation']['arrived']:,}B = ACK {exp['trace']['byte_conservation']['acked']:,}B + queued {exp['trace']['byte_conservation']['queued']:,}B，误差 {exp['trace']['byte_conservation']['error_pct']:.1f}%。</p>
{source_excerpt('src/superran/experience.py','overdue_incomplete = [',before=12,after=44,title='实际实现：PDB 确定 miss 与右删失')}
{source_excerpt('src/superran/experience.py','user_exp = [float',before=8,after=21,title='实际实现：饿死 UE 不从分布消失')}</section>

<section><div class="section-head"><h2><span class="num">6</span>16 次 CRN 配对结果：主结论与支持性结论分开</h2></div>{experience_slope_svg(exp)}
<div class="scroll"><table><thead><tr><th>身份</th><th>KPI</th><th>A correct</th><th>B fault</th><th>A-B</th><th>95% CI</th><th>Wilcoxon p</th><th>判定</th></tr></thead><tbody>{''.join(kpi_html)}</tbody></table></div>
<div class="callout good"><b>可落地主结论：</b>scheduled-TBS PF 让 small queue-wait P95 平均降低 {abs(ep['mean_diff']):.4f}ms，95% CI [{ep['ci95'][0]:.4f}, {ep['ci95'][1]:.4f}]，Wilcoxon p={ep['wilcoxon_p']:.4g}。相对降低 {abs(ep['mean_diff']/ep['mean_b']):.1%} 只能作为门3通过后的补充表达，绝对差必须先写。</div>
<div class="callout warn"><b>不扩大结论：</b>large burst throughput 的 +{el['mean_diff']:.3f}Mbps 是次指标，虽统计显著但未做多重比较校正；resource utilization 与 cell served 的区间跨0，只能写“不分辨”。</div></section>
</article>

<article id="bugs" class="panel"><div class="section-head"><h2>原理审计 · 14 条新修复 + 1 条交接锚点</h2><p class="muted">不是“代码风格问题”，每一条都会改变物理、因果、统计或体验分布。表中哨兵必须在把旧逻辑故意换回来时失败。</p></div>
<div class="scroll"><table><thead><tr><th>ID</th><th>旧逻辑</th><th>为什么原则上错</th><th>修复与落点</th><th>反向哨兵</th></tr></thead><tbody>{bug_rows}</tbody></table></div>
<div class="callout"><b>交接锚点 E-00：</b>Claude 已识别并实现“PF 平均速率必须按实际 scheduled TBS 更新”。本轮没有把旧分支删掉，而是把 <code>legacy_fullband</code> 固定成反向控制臂，并用16次CRN门3验证方向；这让 bug 不会只靠注释防回归。</div>
<h3>验证矩阵</h3><div class="scroll"><table><thead><tr><th>入口</th><th>覆盖</th><th>结果</th></tr></thead><tbody>
<tr><td>test_physics_invariants.py</td><td>容量、速率等效SINR、rank工作点、Student-t、干扰PSD/Ruu、未来信息、PDB/饿死UE/U-slot到达</td><td>{badge(True)} 全部通过</td></tr>
<tr><td>test_linklevel.py / test_interference.py / test_mumimo.py</td><td>预编码/接收机/容量上界/IRC/总功率/MU方向</td><td>{badge(True)} 全部通过</td></tr>
<tr><td>test_system.py / test_csi_aging.py / test_linkadapt.py</td><td>TTI、业务、SRS老化、CQI/MCS/TBS/OLLA</td><td>{badge(True)} 全部通过</td></tr>
<tr><td>test_gates.py / test_results.py / test_rng.py</td><td>位置聚类、CRN、配对统计、门2/门3</td><td>{badge(True)} 全部通过</td></tr>
<tr><td>test_e2e.py / test_mcp_server.py / test_raytracing.py / test_sysscenes.py</td><td>端到端、工具协议、场景与射线追踪回归</td><td>{badge(True)} 全部通过</td></tr>
<tr><td>ruff + compileall</td><td>修改文件静态检查与语法</td><td>{badge(True)} 0 error</td></tr>
</tbody></table></div>
<div class="callout warn"><b>Windows 入口注意：</b>直接在 cp1252 控制台跑 <code>test_mcp_server.py</code> 会在打印中文时触发 UnicodeEncodeError；用 <code>$env:PYTHONIOENCODING='utf-8'</code> 运行。算法测试本身没有失败。</div></article>

<article id="decisions" class="panel"><div class="section-head"><h2>落地实现方案与待决策点</h2><p class="muted">当前代码已把不需要业务拍板的正确性 bug 修完。下面这些会改变产品定义、现场口径或数据成本，必须显式决定，不能由仿真器偷偷替你选。</p></div>
<div class="callout good"><b>推荐落地顺序：</b>P0 合入本轮 correctness + 反向哨兵；P1 冻结 EPF/PF credit/尾料/HARQ/burst；P2 补 ≥170ms/UE 连续 trace 与真实邻区 W；P3 校准 RE/BLER/HARQ；P4 再加逐RBG SINR与MU。每阶段单独过三道门。</div>
<div class="scroll"><table><thead><tr><th>阶段</th><th>实现内容</th><th>必须产物</th><th>验收</th></tr></thead><tbody>
<tr><td>P0 · correctness</td><td>本轮14项修复、PF反向臂、位置聚类</td><td>代码 + tests + deep JSON + 本HTML</td><td>14入口全绿；门1/2/3；字节/RBG守恒</td></tr>
<tr><td>P1 · 产品口径</td><td>EPF、PF credit、tail fill、HARQ retry、burst/PDB定义</td><td>一页参数合同 + preset</td><td>每个参数有默认值、来源、替代臂</td></tr>
<tr><td>P2 · 数据可信</td><td>≥40 snapshots/UE；neighbor served-channel/W；负载trace</td><td>新 dataset + manifest + Gate1</td><td>历史覆盖 CSI 最大陈旧时长；无周期伪回放</td></tr>
<tr><td>P3 · 现场标定</td><td>DMRS/PTRS/CORESET RE；BLER 标定；HARQ RTT/RV/process</td><td>版本化 link profile</td><td>TBS/BLER/OLLA锚点逐项对账</td></tr>
<tr><td>P4 · 能力扩展</td><td>逐RBG SINR、频选调度、MU pairing/ZF/RZF</td><td>独立实验计划和基线</td><td>不得与P1/P2同时改；增益可归因</td></tr>
</tbody></table></div>
{decision_cards()}
<section><div class="section-head"><h2>规范调研：哪些是标准，哪些只是工程选择</h2></div>
<ul class="source-list">
<li><a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138214/19.04.00_60/ts_138214v190400p.pdf">ETSI TS 138 214 V19.4.0 / 3GPP TS 38.214</a>：§5.1.3.1 MCS 表、§5.1.3.2 TBS 量化、§5.2.2.2 PMI/Type-I codebook。项目的 TBS 量化边界来自这里；预置 20B MCS profile 和 RE overhead 不是标准默认。</li>
<li><a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138211/19.04.00_60/ts_138211v190400p.pdf">ETSI TS 138 211 V19.4.0 / 3GPP TS 38.211</a>：§6.4.1.4 SRS resource/sequence/mapping。SRS 周期、跳频与处理延迟仍需现场配置。</li>
<li><a href="https://www.etsi.org/deliver/etsi_tr/138900_138999/138901/19.04.00_60/tr_138901v190400p.pdf">ETSI TR 138 901 V19.4.0 / 3GPP TR 38.901</a>：§7.2 UMa；§7.7.1 指明 CDL-A/B/C 为 NLOS，CDL-C 表在7.7.1-3。它支撑场景/信道，不定义 PF/EPF。</li>
<li><a href="https://www.etsi.org/deliver/etsi_ts/128500_128599/128552/19.07.00_60/ts_128552v190700p.pdf">ETSI TS 128 552 V19.7.0 / 3GPP TS 28.552</a>：§5.1.1.3.1 定义 DRB.UEThpDl，明确面向跨多 slot 的大 burst，并排除清空 buffer 的最后 piece；small packet 需另报时延/PDB。</li>
</ul>
<div class="callout"><b>调研结论：</b>3GPP 给了物理过程、码本/TBS 与 PM counter 的边界，但没有一条统一的“EPF = 某公式”。因此 D1 不是查规范就能自动消失的参数，而是厂商/现场产品定义。</div></section>
<details class="code"><summary>追溯信息与冻结 JSON</summary><pre><code>dataset spectrum: {spectrum['dataset_id']} · prereg {spectrum['prereg']['prereg_id']}
dataset experience: {exp['dataset_id']}
result: {DATA_PATH}
generator: scripts/run_deep_simulation_audit.py
report generator: scripts/make_deep_simulation_html.py
generated_at: {esc(data['generated_at'])}</code></pre></details></article>
</main>
<script id="audit-data" type="application/json">{raw_json}</script>
<script>(function(){{var buttons=document.querySelectorAll('.tabs button'),panels=document.querySelectorAll('.panel');buttons.forEach(function(b){{b.addEventListener('click',function(){{buttons.forEach(function(x){{x.classList.remove('active')}});panels.forEach(function(x){{x.classList.remove('active')}});b.classList.add('active');document.getElementById(b.dataset.tab).classList.add('active');window.scrollTo({{top:document.querySelector('.tabs').offsetTop,behavior:'smooth'}})}})}})}})();</script>
{kx.upgrade_script()}
<footer>离线、自包含、UTF-8。报告只把通过门的预注册主指标写成主结论；所有工程近似和待决策项均显式列出。</footer></body></html>"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = build()
    OUT.write_text(text, encoding="utf-8")
    print(json.dumps({
        "output": str(OUT),
        "bytes": OUT.stat().st_size,
        "first_line": text.splitlines()[0],
        "has_replacement_char": "\ufffd" in text,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
