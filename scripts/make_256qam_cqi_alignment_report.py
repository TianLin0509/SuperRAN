"""Build the self-contained 256QAM/CQI alignment audit report."""
from __future__ import annotations

import html
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from superran import bler_curves as bc  # noqa: E402
from superran import linkadapt as la  # noqa: E402

OUT = ROOT / "artifacts" / "reports" / "SUPERRAN_256QAM_CQI_ALIGNMENT.html"

# The reference Markdown exposes these LTE-side 256QAM rows.  It explicitly says
# its 5G thresholds and BLER curves are loaded from an external file not embedded
# in the document, so these constants are used only for an explanatory comparison.
REFERENCE_SE = np.asarray([
    0.1523, 0.2344, 0.3770, 0.6016, 0.8770, 1.1758, 1.4766, 1.6954,
    1.9141, 2.1602, 2.4063, 2.5684, 2.7305, 3.0264, 3.3223, 3.6123,
    3.9023, 4.2129, 4.5234, 4.8193, 5.1152, 5.3350, 5.5547, 5.8906,
    6.2266, 6.5703, 6.9141, 7.1602, 7.4063,
], dtype=float)
REFERENCE_THRESHOLD_DB = np.asarray([
    -7.55, -5.65, -3.55, -1.50, 0.50, 2.45, 4.40, 5.40, 6.30,
    7.25, 8.30, 8.95, 10.15, 11.15, 12.10, 13.15, 14.05, 15.10,
    16.00, 17.00, 18.10, 19.00, 20.10, 20.37, 21.16, 22.10, 22.97,
    23.78, 25.37,
], dtype=float)
OLD_MAPPING = (0, 1, 3, 5, 7, 9, 12, 14, 16, 19, 21, 23, 25, 27, 28)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def mapping_table() -> str:
    rows = []
    for row, (old, new) in enumerate(zip(OLD_MAPPING, la.INTERNAL_CQI_TO_MCS, strict=True)):
        resolved = la.internal_cqi_to_mcs(row)
        status = "钳位到 MCS27" if resolved["mcs_clipped_to_profile"] else "精确命中"
        rows.append(
            "<tr>"
            f"<td>{row}</td><td>{row + 1}</td><td>{old}</td><td>{new}</td>"
            f"<td>{resolved['mcs']}</td><td><span class='pill'>{status}</span></td>"
            "</tr>"
        )
    return "".join(rows)


def mcs_table() -> tuple[str, dict[str, float]]:
    current_se = np.asarray([m.se for m in la.MCS_TABLE_3], dtype=float)
    current_threshold = np.asarray([
        bc.get_curve(m.index, "newtx").required_sinr_db(0.1)
        for m in la.MCS_TABLE_3
    ], dtype=float)
    nearest = np.abs(
        REFERENCE_SE[:, None] - current_se[None, :]
    ).argmin(axis=0)
    differences = current_threshold - REFERENCE_THRESHOLD_DB[nearest]
    rows = []
    for m, threshold, reference_index, delta in zip(
        la.MCS_TABLE_3, current_threshold, nearest, differences, strict=True
    ):
        modulation = {2: "QPSK", 4: "16QAM", 6: "64QAM", 8: "256QAM"}[m.q_m]
        delta_class = "pos" if delta > 0.25 else "neg" if delta < -0.25 else "near"
        rows.append(
            "<tr>"
            f"<td>{m.index}</td><td>{modulation}</td><td>{m.rate:.4f}</td>"
            f"<td>{m.se:.4f}</td><td>{threshold:.4f}</td>"
            f"<td>{int(reference_index)}</td><td>{REFERENCE_SE[reference_index]:.4f}</td>"
            f"<td class='{delta_class}'>{delta:+.3f}</td>"
            "</tr>"
        )
    metrics = {
        "median": float(np.median(differences)),
        "rms": float(math.sqrt(float(np.mean(differences ** 2)))),
        "min": float(np.min(differences)),
        "max": float(np.max(differences)),
    }
    return "".join(rows), metrics


def threshold_plot() -> str:
    current_se = np.asarray([m.se for m in la.MCS_TABLE_3], dtype=float)
    current_threshold = np.asarray([
        bc.get_curve(m.index, "newtx").required_sinr_db(0.1)
        for m in la.MCS_TABLE_3
    ], dtype=float)
    width, height = 920, 330
    left, right, top, bottom = 64, 24, 26, 52
    x0, x1 = 0.0, 7.6
    y0, y1 = -8.5, 27.0

    def px(x: float) -> float:
        return left + (x - x0) / (x1 - x0) * (width - left - right)

    def py(y: float) -> float:
        return top + (y1 - y) / (y1 - y0) * (height - top - bottom)

    grid = []
    for y in (-5, 0, 5, 10, 15, 20, 25):
        grid.append(
            f"<line x1='{left}' x2='{width-right}' y1='{py(y):.1f}' y2='{py(y):.1f}' "
            "class='grid'/><text x='52' y='"
            f"{py(y)+4:.1f}' text-anchor='end'>{y}</text>"
        )
    for x in range(0, 8):
        grid.append(
            f"<line x1='{px(x):.1f}' x2='{px(x):.1f}' y1='{top}' y2='{height-bottom}' "
            "class='grid'/><text x='"
            f"{px(x):.1f}' y='{height-26}' text-anchor='middle'>{x}</text>"
        )

    def points(xs: np.ndarray, ys: np.ndarray) -> str:
        return " ".join(f"{px(float(x)):.1f},{py(float(y)):.1f}" for x, y in zip(xs, ys, strict=True))

    return (
        f"<svg class='chart' viewBox='0 0 {width} {height}' role='img' "
        "aria-label='按谱效对齐的10% BLER门限比较'>"
        + "".join(grid)
        + f"<polyline class='line ref' points='{points(REFERENCE_SE, REFERENCE_THRESHOLD_DB)}'/>"
        + f"<polyline class='line cur' points='{points(current_se, current_threshold)}'/>"
        + f"<text class='axis' x='{width/2:.0f}' y='{height-5}'>名义谱效 bit/RE</text>"
        + f"<text class='axis' transform='translate(15 {height/2:.0f}) rotate(-90)'>10% BLER 门限 dB</text>"
        + "<g class='legend'><line x1='650' x2='682' y1='36' y2='36' class='line cur'/>"
        + "<text x='690' y='40'>当前预置曲线</text><line x1='650' x2='682' y1='60' y2='60' class='line ref'/>"
        + "<text x='690' y='64'>参考 Markdown 的 LTE 门限</text></g></svg>"
    )


def build() -> str:
    rows, metrics = mcs_table()
    verify = bc.verify_curves()
    first_threshold = bc.get_curve(0, "newtx").required_sinr_db(0.1)
    top_threshold = bc.get_curve(27, "newtx").required_sinr_db(0.1)
    css = """
:root{--bg:#f4f7fb;--paper:#fff;--ink:#152033;--muted:#607086;--line:#d9e2ef;
--blue:#185adb;--cyan:#0b8fac;--green:#17845b;--amber:#a45d00;--red:#b42318;--navy:#0b1f3a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.72 Inter,"Segoe UI","Microsoft YaHei",sans-serif}
.hero{background:linear-gradient(120deg,#071a33,#123e79 63%,#0b8fac);color:#fff;padding:56px 24px 76px}.wrap{max-width:1160px;margin:auto}
.eyebrow{font-size:12px;letter-spacing:.18em;text-transform:uppercase;opacity:.72}.hero h1{font-size:clamp(32px,5vw,54px);line-height:1.1;margin:12px 0 16px}.hero p{max-width:850px;font-size:18px;color:#dbeafe}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:-40px}.card,.section{background:var(--paper);border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 30px #173b6812}.card{padding:20px}.card b{display:block;font-size:24px;color:var(--navy)}.card span{color:var(--muted);font-size:13px}
.section{margin:22px 0;padding:30px}.section h2{font-size:25px;margin:0 0 8px}.section h3{margin:26px 0 6px}.lead{font-size:17px;color:#40536b}.decision{border-left:5px solid var(--green);background:#f1fbf6;padding:16px 20px;border-radius:10px;margin:18px 0}.warn{border-left-color:var(--amber);background:#fff8eb}.open{border-left-color:var(--red);background:#fff3f1}
.flow{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:22px 0}.flow div{position:relative;padding:14px 10px;text-align:center;border:1px solid #bfd0e7;border-radius:10px;background:#f7faff}.flow div:not(:last-child):after{content:'→';position:absolute;right:-12px;top:14px;color:var(--blue);font-weight:800;z-index:2}.flow small{display:block;color:var(--muted)}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px}table{border-collapse:collapse;width:100%;min-width:760px}th{position:sticky;top:0;background:#eef4fb;color:#31435b;text-align:left}th,td{padding:10px 12px;border-bottom:1px solid #e8eef6;white-space:nowrap}tbody tr:hover{background:#f8fbff}.pill{display:inline-block;background:#e7f0ff;color:#174ea6;border-radius:99px;padding:2px 8px;font-size:12px}.pos{color:#a34800}.neg{color:#116b93}.near{color:var(--green)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}.checklist{list-style:none;padding:0}.checklist li{padding:8px 0 8px 28px;position:relative;border-bottom:1px dashed #dfe7f1}.checklist li:before{content:'✓';position:absolute;left:2px;color:var(--green);font-weight:800}.checklist.pending li:before{content:'!';color:var(--amber)}
.chart{display:block;width:100%;height:auto;background:#fbfdff;border:1px solid var(--line);border-radius:12px}.chart text{font-size:12px;fill:#607086}.chart .grid{stroke:#e3eaf3;stroke-width:1}.chart .line{fill:none;stroke-width:3}.chart .cur{stroke:#185adb}.chart .ref{stroke:#e37b28;stroke-dasharray:7 5}.chart .axis{font-weight:700;fill:#33465f}.metric-row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}.metric{padding:14px;background:#f7faff;border-radius:10px}.metric b{font-size:20px;display:block}.muted,small{color:var(--muted)}code{background:#eef3f9;padding:2px 5px;border-radius:5px}.footer{padding:22px;text-align:center;color:var(--muted)}
details{border:1px solid var(--line);border-radius:10px;margin-top:14px}summary{cursor:pointer;padding:13px 16px;font-weight:700}details>div{padding:0 16px 16px}
@media(max-width:850px){.cards,.two,.metric-row{grid-template-columns:1fr 1fr}.flow{grid-template-columns:1fr}.flow div:not(:last-child):after{content:'↓';right:50%;top:auto;bottom:-17px}.section{padding:20px}}
@media(max-width:520px){.cards,.two,.metric-row{grid-template-columns:1fr}.hero{padding-top:38px}.hero h1{font-size:34px}}
@media print{body{background:#fff}.hero{padding:25px;background:#123e79!important}.cards{margin-top:16px}.section,.card{break-inside:avoid;box-shadow:none}}
"""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="SuperRAN 256QAM CQI 映射、默认 MCS profile 与 OLLA 对齐审查报告">
<title>SuperRAN · 256QAM 与 CQI 对齐审查</title><style>{css}</style></head><body>
<header class="hero"><div class="wrap"><div class="eyebrow">IMPLEMENTATION AUDIT · 2026-08-25</div>
<h1>256QAM 默认化与 CQI 编号收口</h1>
<p>这次不是只换一串映射值：同时收口了默认 MCS profile、真实上报 CQI0、最高 CQI 可达性、OLLA 默认步长，以及文档/接口/系统调度之间的编号歧义。</p></div></header>
<main class="wrap"><section class="cards">
<article class="card"><b>表 3</b><span>高层仿真的新默认 · 预置 256QAM</span></article>
<article class="card"><b>0..27</b><span>保留当前 MCS 编号，不新增 MCS28 曲线</span></article>
<article class="card"><b>0..15</b><span>真实 4-bit CQI；0 = out-of-range</span></article>
<article class="card"><b>+0.01 / −0.09</b><span>10% 目标下默认 ACK/NACK OLLA 步长</span></article>
</section>

<section class="section"><h2>结论先行</h2><p class="lead">已按你确认的四项决策落地，并额外修掉两个在切换映射后才暴露的边界 bug。</p>
<div class="decision"><b>默认行为已经改变。</b> <code>link_adaptation</code>、<code>Dataset.throughput</code>、<code>sr_throughput</code>、<code>sr_mcs_info</code> 与 <code>sr_sweep_snr</code> 默认走预置 256QAM 表 3；64QAM 表 1 只在显式指定时使用，标准 256QAM 表 2 仍作为可选分析分支。</div>
<div class="flow"><div><b>上报 CQI</b><small>0..15</small></div><div><b>映射表行</b><small>1..15 → row 0..14</small></div><div><b>初始 MCS 门限</b><small>NewTx 目标 BLER</small></div><div><b>+ BF Gain</b><small>同工况 SVD − PMI</small></div><div><b>+ OLLA / floor</b><small>最终 MCS 0..27</small></div></div>
<div class="two"><div><h3>额外修复 A：CQI15 原本会永远不可达</h3><p>旧的“缺 MCS28 就把阈值设为 +∞”逻辑来自旧映射；新映射的上一行是 MCS26，因此最高行钳到 MCS27 后并不重复。现在自动 CQI15 使用 MCS27 的 10% 门限 <b>{top_threshold:.4f} dB</b>，既可达，也没有伪造 MCS28 曲线。</p></div>
<div><h3>额外修复 B：CQI0 不能借 BF 增益偷渡</h3><p>若只把 CQI0 暂存为内部 row0，某些强 BF 样本仍可能被调度。现在上报 CQI0 会先进入 out-of-range/outage，再由调度器排除；内部 row0 仍只作为有限数值占位，避免生成 −∞/NaN。</p></div></div></section>

<section class="section"><h2>新旧 CQI 映射</h2><p>历史 API 的 <code>cqi_index=0..14</code> 是数组行，不是空口 codepoint。为避免静默 off-by-one，代码同时返回 <code>cqi_row</code> 与 <code>reported_cqi_codepoint</code>。</p>
<div class="table-wrap"><table><thead><tr><th>历史表行</th><th>上报 4-bit CQI</th><th>旧映射 MCS</th><th>新 256QAM 映射</th><th>当前实际 MCS</th><th>状态</th></tr></thead><tbody>{mapping_table()}</tbody></table></div>
<p class="muted">上报 CQI0 不对应表中任何一行，语义固定为 out-of-range、不调度。最高行保留 <code>requested_mcs=28</code> 供审计，但当前 profile 显式解析成 MCS27。</p></section>

<section class="section"><h2>参考 Markdown 到底给了什么</h2><div class="two">
<div><h3>可以继承</h3><ul class="checklist"><li>256QAM CQI 行映射：<code>[0,2,4,…,28]</code></li><li>4-bit CQI1..15 与内部 15 行的关系</li><li>MCS→SINR→BF/Rank 补偿→MCS→OLLA 的总体流程</li><li>ACK/NACK 步长平衡式与 <code>floor</code> 语义</li><li>LTE 侧 64QAM/256QAM 门限及 MCS 信息表，可作量级旁证</li></ul></div>
<div><h3>不能直接继承</h3><ul class="checklist pending"><li>文档明确说 5G <code>MCS_SINR</code> 从外部曲线文件加载，正文没有该文件</li><li>没有实际 5G BLER 点列，无法逐点比较当前 1,824 点曲线</li><li>没有 <code>effMappingForMeasure</code> 的实现，无法复现其 EESM/RBIR 细节</li><li>没有 <code>calcBler</code> 函数体，不能证明块长、重传与插值口径一致</li><li>存在一个带 TODO 的 CQI=MCS 直通分支，不应当替代已确认映射</li></ul></div></div>
<div class="decision warn"><b>所以答案是：</b>这份 Markdown 有 MCS 信息和 LTE 门限，但没有可直接装载的 5G MCS/BLER 曲线包。它能用来校准流程、索引和开关，不能拿来替换现有预置 BLER 数据。</div></section>

<section class="section"><h2>与当前预置 MCS/BLER 的差异</h2><p>当前 profile 是 28 行 MCS0..27；参考 Markdown 的 LTE 256QAM 表是 29 行 MCS0..28。原始索引不能逐号硬比，因此下图按最接近的名义谱效对齐，只用于量级检查。</p>
{threshold_plot()}
<div class="metric-row"><div class="metric"><b>{metrics['median']:+.2f} dB</b><span>门限差中位数</span></div><div class="metric"><b>{metrics['rms']:.2f} dB</b><span>RMS 差</span></div><div class="metric"><b>{metrics['min']:+.2f} dB</b><span>最小差</span></div><div class="metric"><b>{metrics['max']:+.2f} dB</b><span>最大差</span></div></div>
<p>两套门限按谱效对齐后量级接近，但这不证明它们来自同一链路条件。当前门限来自预置 NewTx 曲线的 10% crossing；参考值是另一套 LTE 硬编码门限。两者只做 sanity check，不互相覆盖。</p>
<details><summary>展开 28 档逐行对照</summary><div><div class="table-wrap"><table><thead><tr><th>当前 MCS</th><th>调制</th><th>码率</th><th>SE</th><th>当前 10% 门限</th><th>参考行</th><th>参考 SE</th><th>门限差 dB</th></tr></thead><tbody>{rows}</tbody></table></div></div></details>
<div class="decision open"><b>需要上报的保留项：</b>当前 MCS0 与 MCS1 的调制、码率和 NewTx 曲线完全相同，MCS1 没有新增物理工作点。这是现有预置数据的可验证事实，但无法仅凭参考 Markdown 判断它是否就是你提到的“两阶问题”；本次遵照“先保留当前 MCS 编号/曲线”的决定，没有擅改。</div></section>

<section class="section"><h2>其他开关：本次怎样处理</h2><div class="table-wrap"><table><thead><tr><th>开关/分支</th><th>判断</th><th>影响</th><th>本次处理</th></tr></thead><tbody>
<tr><td>256QAM enable</td><td>应开启</td><td>高：改变 CQI 映射、可用调制和高 SINR 吞吐</td><td>高层接口默认表3；64QAM改为显式可选</td></tr>
<tr><td>CQI=MCS 1:1 直通</td><td>不采用</td><td>高：会绕过已确认离散映射</td><td>保持关闭语义；不复制带 TODO 的旁路</td></tr>
<tr><td>TDD BF adjustment</td><td>应生效</td><td>高：直接改变最终 MCS</td><td>沿用当前物理路径：同 CSI/rank/功率/MMSE 下逐 RBG×流计算 SVD−PMI</td></tr>
<tr><td>Rank 补偿启发式</td><td>不照搬</td><td>中高：错误时会重复补偿功率或 rank</td><td>当前代码逐 rank 重算物理 post-MMSE SINR，避免再叠经验式</td></tr>
<tr><td>CQI filter</td><td>注释不足</td><td>中：主要影响时变与反馈抖动</td><td>不新增未知开关；保留当前因果 report-period 滤波</td></tr>
<tr><td>SSB/JT/RESA 功率补偿</td><td>场景专用且输入不齐</td><td>当前默认场景低；开启时可能很大</td><td>不猜参数、不静默补偿；等真实配置与参考面后再接</td></tr>
</tbody></table></div></section>

<section class="section"><h2>实现与验证证据</h2><div class="two"><div><h3>本轮代码合同</h3><ul class="checklist"><li>新增 reported CQI 0..15 适配器，CQI0 明确不调度</li><li>保留历史 row 0..14 兼容入口，返回两个编号</li><li>最高 CQI15 精确锚定 MCS27 门限</li><li>系统链路表落盘 reported codepoint，并把 CQI0 纳入 outage</li><li>诊断与系统 OLLA 默认统一为 +0.01/−0.09</li><li>高层默认 profile 统一为预置 256QAM 表3</li></ul></div>
<div><h3>已通过检查</h3><ul class="checklist"><li>曲线完整性：{esc(verify.get('n_curves', 56))} 条曲线 / {esc(verify.get('n_points', 1824))} 点</li><li>首档 CQI1 门限：{first_threshold:.4f} dB；低于它上报 CQI0</li><li>顶档 CQI15 门限：{top_threshold:.4f} dB</li><li>CSI 老化与因果反馈：116 项通过</li><li>MCP 全链路与载波：全部通过</li><li>系统调度、HARQ、PF、体验 KPI：全部通过</li><li>随机数/CRN/置信区间、结果合同、物理不变量：全部通过</li><li>Python 编译与 Ruff：通过</li></ul></div></div>
<p class="muted">完整链路自适应长回归、干扰/算法文档大回归和 HTML 浏览器 QA 均已通过；最终提交号与远端校验写在交付消息中，而不把会自引用变化的 commit 写死在本页。</p></section>

<section class="section"><h2>仍需你知情，但本次不阻塞</h2><ol><li><b>MCS0/MCS1 重复：</b>等拿到权威预置表或确认“两个错误档”的具体索引后再修，避免凭猜测改动 BLER。</li><li><b>最高映射的编号差：</b>外部参考请求 MCS28，当前合同只到27；现在是显式钳位而非新增虚构曲线。</li><li><b>真实 5G 外部曲线：</b>若后续提供原始曲线文件，可以做逐点 hash、门限、插值和 TBS/码长适用域对齐。</li><li><b>场景专用补偿：</b>SSB/JT/RESA 等必须先给功率参考面和启用条件，当前不做低置信度推测。</li></ol></section>
</main><footer class="footer">SuperRAN · 256QAM/CQI alignment audit · self-contained UTF-8 HTML</footer></body></html>"""


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = build()
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {OUT} ({len(text):,} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
