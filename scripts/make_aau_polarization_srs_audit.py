"""Build the self-contained AAU/polarization/time/SRS audit report.

The report is intentionally generated from constants and marker replacement.
Do not put LaTeX backslashes inside f-string expressions: this repository must
remain runnable on Python versions older than 3.12.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import katex, mathml  # noqa: E402

OUT = ROOT / "artifacts" / "AAU_F_POLARIZATION_SRS_DEEP_AUDIT.html"
F_SVG = ROOT / "artifacts" / "diagrams" / "F_MATRIX_192X64_PHYSICAL_MEANING.svg"
POL_SVG = ROOT / "artifacts" / "diagrams" / "DUAL_POLARIZATION_CHANNEL_TOPOLOGY.svg"


def formula(tex: str) -> str:
    fallback = mathml.render(tex, block=True)
    return katex.wrap(tex, fallback, display=True)


def inline_svg(path: Path) -> str:
    if not path.exists():
        return f_matrix_svg() if path == F_SVG else dual_polarization_svg()
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^<\?xml[^>]*>\s*", "", text, count=1)


def f_matrix_svg() -> str:
    """Deterministic canonical 64T feed topology; no external image required."""
    return r'''<svg viewBox="0 0 1200 560" role="img" aria-label="64T canonical 端口与 192×64 馈电矩阵拓扑">
<defs><marker id="fm-arr" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#52727c"/></marker></defs>
<rect width="1200" height="560" rx="18" fill="#f8fbfb"/>
<text x="38" y="45" font-size="20" font-weight="800" fill="#0c3140">64T canonical：极化块 → 水平列 → 垂直行</text>
<text x="38" y="72" font-size="13" fill="#60737c">r=32p+4h+v；v=0 在物理顶部；历史 h_v_pol 只能经显式置换读取</text>
<rect x="38" y="102" width="275" height="352" rx="16" fill="#e8f4f2" stroke="#5aa69d"/>
<text x="58" y="132" font-size="16" font-weight="750" fill="#0c3140">RF / 数字端口 · 8H×4V×2pol</text>
<rect x="61" y="157" width="108" height="235" rx="12" fill="#fff" stroke="#cfdddd"/>
<rect x="183" y="157" width="108" height="235" rx="12" fill="#fff" stroke="#cfdddd"/>
<text x="115" y="184" text-anchor="middle" font-size="13" font-weight="700" fill="#c44c48">p=0 · +45°</text>
<text x="237" y="184" text-anchor="middle" font-size="13" font-weight="700" fill="#3570b4">p=1 · −45°</text>
<g font-family="Consolas,monospace" font-size="12" fill="#294a54"><text x="78" y="218">h0: 1 2 3 4</text><text x="78" y="248">h1: 5 6 7 8</text><text x="78" y="278">…</text><text x="78" y="308">h7: 29…32</text><text x="199" y="218">h0: 33…36</text><text x="199" y="248">h1: 37…40</text><text x="199" y="278">…</text><text x="199" y="308">h7: 61…64</text></g>
<text x="176" y="420" text-anchor="middle" font-size="12" fill="#60737c">锚点：1 / 5 / 33</text>
<path d="M313 278 L395 278" stroke="#52727c" stroke-width="2" marker-end="url(#fm-arr)"/>
<text x="354" y="264" text-anchor="middle" font-size="12" fill="#60737c">每端口 1→3</text>
<rect x="405" y="102" width="350" height="352" rx="16" fill="#fff" stroke="#cfdddd"/>
<text x="425" y="132" font-size="16" font-weight="750" fill="#0c3140">F ∈ ℂ¹⁹²ˣ⁶⁴ · 物理接线表</text>
<g stroke="#dde6e6" stroke-width="1"><line x1="438" y1="170" x2="722" y2="170"/><line x1="438" y1="218" x2="722" y2="218"/><line x1="438" y1="266" x2="722" y2="266"/><line x1="438" y1="314" x2="722" y2="314"/><line x1="438" y1="362" x2="722" y2="362"/></g>
<g font-family="Consolas,monospace" font-size="12" fill="#294a54"><text x="438" y="158">column</text><text x="520" y="158">physical rows (1-based)</text><text x="438" y="202">port 1</text><text x="520" y="202">1, 2, 3</text><text x="438" y="250">port 5</text><text x="520" y="250">13, 14, 15</text><text x="438" y="298">port 33</text><text x="520" y="298">97, 98, 99</text><text x="438" y="346">every r</text><text x="520" y="346">3 nonzeros · disjoint support</text></g>
<g fill="#12a397"><circle cx="675" cy="198" r="6"/><circle cx="675" cy="246" r="6"/><circle cx="675" cy="294" r="6"/></g>
<text x="580" y="399" text-anchor="middle" font-size="13" font-weight="700" fill="#247149">每列 ‖F[:,r]‖₂=1；FᴴF=I₆₄</text>
<path d="M755 278 L837 278" stroke="#52727c" stroke-width="2" marker-end="url(#fm-arr)"/>
<rect x="847" y="102" width="315" height="352" rx="16" fill="#eef5fb" stroke="#8eb5ce"/>
<text x="867" y="132" font-size="16" font-weight="750" fill="#0c3140">物理 AE · 8H×12V×2pol</text>
<g stroke="#8eb5ce" stroke-width="2" fill="#fff"><line x1="915" y1="170" x2="915" y2="352"/><circle cx="915" cy="185" r="10"/><circle cx="915" cy="225" r="10"/><circle cx="915" cy="265" r="10"/><circle cx="915" cy="305" r="10"/><circle cx="915" cy="345" r="10"/></g>
<g font-size="12" fill="#60737c"><text x="943" y="190">物理垂直间距 0.67λ</text><text x="943" y="230">相邻 RF 中心 2.01λ</text><text x="943" y="270">同一位置两条 ±45° feed</text><text x="943" y="310">192 行，64 维可控子空间</text><text x="943" y="350">a_port = Fᴴa_AE</text></g>
<rect x="38" y="482" width="1124" height="50" rx="12" fill="#0c3140"/>
<text x="600" y="514" text-anchor="middle" font-size="14" fill="#eafafa">物理含义：F 只分配端口功率与固定相位；它不创造 128 个额外数字自由度，也不等于数字预编码 W</text>
</svg>'''


def dual_polarization_svg() -> str:
    """Jones/XPR topology aligned with company p0=+45°, p1=-45°."""
    return r'''<svg viewBox="0 0 1200 430" role="img" aria-label="双极化 Jones 与射线耦合拓扑">
<defs><marker id="pol-arr" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#52727c"/></marker></defs>
<rect width="1200" height="430" rx="18" fill="#f8fbfb"/>
<text x="38" y="45" font-size="20" font-weight="800" fill="#0c3140">双极化不是“天线数 ×2”：每条 ray 都要做 Jones 收缩</text>
<rect x="38" y="92" width="250" height="245" rx="16" fill="#fff" stroke="#cfdddd"/>
<circle cx="163" cy="166" r="40" fill="#eef6f5" stroke="#6aa9a0"/><line x1="135" y1="194" x2="191" y2="138" stroke="#c44c48" stroke-width="5"/><line x1="135" y1="138" x2="191" y2="194" stroke="#3570b4" stroke-width="5"/>
<text x="163" y="235" text-anchor="middle" font-size="14" font-weight="700" fill="#c44c48">p0 = +45°</text><text x="163" y="262" text-anchor="middle" font-size="14" font-weight="700" fill="#3570b4">p1 = −45°</text><text x="163" y="302" text-anchor="middle" font-size="12" fill="#60737c">同一空间位置 · 两个端口</text>
<path d="M288 214 L365 214" stroke="#52727c" stroke-width="2" marker-end="url(#pol-arr)"/>
<rect x="375" y="92" width="300" height="245" rx="16" fill="#e8f4f2" stroke="#5aa69d"/>
<text x="525" y="128" text-anchor="middle" font-size="16" font-weight="750" fill="#0c3140">路径极化矩阵 Jℓ</text><text x="525" y="168" text-anchor="middle" font-family="Consolas,monospace" font-size="15" fill="#294a54">[ co-pol      xpol/√κ ]</text><text x="525" y="198" text-anchor="middle" font-family="Consolas,monospace" font-size="15" fill="#294a54">[ xpol/√κ    co-pol ]</text><text x="525" y="246" text-anchor="middle" font-size="13" fill="#60737c">XPR κ、四个随机相位、方向/时延/Doppler</text><text x="525" y="283" text-anchor="middle" font-size="13" font-weight="700" fill="#247149">cℓ = fRXᵀ Jℓ fTX</text>
<path d="M675 214 L752 214" stroke="#52727c" stroke-width="2" marker-end="url(#pol-arr)"/>
<rect x="762" y="92" width="400" height="245" rx="16" fill="#eef5fb" stroke="#8eb5ce"/>
<text x="962" y="128" text-anchor="middle" font-size="16" font-weight="750" fill="#0c3140">进入每条 ray 的 MIMO 系数</text><text x="962" y="176" text-anchor="middle" font-family="Consolas,monospace" font-size="14" fill="#294a54">Hℓ ∝ √Pℓ · cℓ · aRX aTXᴴ</text><text x="962" y="208" text-anchor="middle" font-family="Consolas,monospace" font-size="14" fill="#294a54">· exp(−j2πfτℓ) · exp(j2πνℓt)</text><text x="962" y="258" text-anchor="middle" font-size="13" fill="#60737c">方向图决定复场幅相；F 形成有效端口；</text><text x="962" y="284" text-anchor="middle" font-size="13" fill="#60737c">数字 W 在端口域随后计算，不能重复加增益</text>
<text x="600" y="385" text-anchor="middle" font-size="13" fill="#8b620f">当前 ±45° 与参数化包络是理想模型；预置 (az,el,f) 复 Jones、XPD 与馈电标定仍需实测输入</text>
</svg>'''


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AAU 馈电、双极化、时域采样与 SRS 估计深审</title>
__KATEX_HEAD__
<style>
:root{--ink:#162832;--muted:#60737c;--paper:#f3f7f7;--card:#fff;--line:#d5e2e2;--navy:#0c3140;--teal:#087c73;--teal2:#12a397;--orange:#d6742f;--red:#aa3d35;--amber:#8b620f;--green:#247149;--blue:#326c8d;--code:#0d2d3a}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif;line-height:1.68}a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}code,.mono{font-family:Consolas,"Cascadia Mono",monospace}
.hero{background:linear-gradient(125deg,#082a38 0%,#104f5c 62%,#087c73 100%);color:white;padding:55px 24px 48px;position:relative;overflow:hidden}.hero:after{content:"";position:absolute;width:560px;height:560px;border:1px solid #ffffff22;border-radius:50%;right:-190px;top:-285px;box-shadow:0 0 0 72px #ffffff0a,0 0 0 144px #ffffff06}.wrap,.page{max-width:1240px;margin:auto;position:relative;z-index:1}.eyebrow{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:#9fe7de;font-weight:800}.hero h1{font-size:clamp(34px,5vw,62px);line-height:1.07;margin:10px 0 17px;max-width:1100px}.lead{font-size:19px;color:#e2f1f1;max-width:1040px;margin:0}.pills{display:flex;gap:9px;flex-wrap:wrap;margin-top:23px}.pill{border:1px solid #ffffff40;background:#ffffff12;border-radius:999px;padding:6px 11px;font-size:13px}.pill.warn{background:#e49b3135}
.nav{position:sticky;top:0;z-index:20;background:#ffffffef;border-bottom:1px solid var(--line);backdrop-filter:blur(10px);overflow:auto;white-space:nowrap}.nav>div{max-width:1240px;margin:auto;padding:9px 18px}.nav a{display:inline-block;padding:6px 9px;color:#324d57;font-size:13px;font-weight:750}.page{padding:26px 22px 72px}.section{scroll-margin-top:70px;margin:27px 0 54px}.kicker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--teal);font-weight:850}.section h2{font-size:31px;line-height:1.25;color:var(--navy);margin:4px 0 9px}.intro{color:var(--muted);max-width:1050px;margin:0 0 19px}
.grid{display:grid;gap:15px}.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.g3{grid-template-columns:repeat(3,minmax(0,1fr))}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}.card{background:white;border:1px solid var(--line);border-radius:15px;padding:18px;box-shadow:0 8px 28px #15384009}.card h3{font-size:18px;line-height:1.35;margin:0 0 8px;color:var(--navy)}.card p:last-child{margin-bottom:0}.metric .big{font-size:30px;line-height:1.1;color:var(--navy);font-weight:880}.metric .lab{font-size:13px;color:var(--muted);margin-top:5px}.metric .tiny{font-size:12px;color:var(--muted);margin-top:8px}
.verdict{background:linear-gradient(90deg,#e3f3f0,#fff);border-left:5px solid var(--teal);border-radius:0 14px 14px 0;padding:20px 22px;font-size:17px}.verdict b{color:var(--navy)}.callout{border:1px solid #e6cf99;background:#fff7e5;color:#644a10;border-radius:13px;padding:15px 17px}.callout.red{background:#fff0ed;border-color:#edc1bb;color:#71332e}.callout.blue{background:#edf7fb;border-color:#c8e0ea;color:#28586c}.callout.green{background:#eaf6ef;border-color:#bfdfcb;color:#255b3b}.callout h3{margin:0 0 5px;color:inherit;font-size:16px}
.status{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border-radius:999px;font-size:12px;font-weight:850;white-space:nowrap}.status:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}.ok{color:var(--green);background:#e4f3ea}.warn{color:var(--amber);background:#fff0cf}.bad{color:var(--red);background:#fde6e3}.info{color:var(--blue);background:#e5f1f7}
.eq{background:var(--code);color:#edfafa;border-radius:13px;padding:15px 18px;margin:12px 0;overflow:auto}.eq .kx{display:block;min-width:max-content}.eq .katex-display{margin:.25em 0}.eq .katex{font-size:1.06em}.eq-note{color:#afc8cd;font-size:12px;margin-top:6px}
table{width:100%;border-collapse:separate;border-spacing:0;background:white;border:1px solid var(--line);border-radius:13px;overflow:hidden;font-size:14px}th,td{text-align:left;vertical-align:top;padding:11px 12px;border-bottom:1px solid var(--line)}th{background:#eaf2f2;color:#294a54;font-size:12px;letter-spacing:.025em}tr:last-child td{border-bottom:0}.table-wrap{overflow:auto;border-radius:13px}.table-wrap table{min-width:900px}.tight{margin:7px 0;padding-left:19px}.tight li{margin:5px 0}.small{font-size:12px;color:var(--muted)}.path{font-family:Consolas,"Cascadia Mono",monospace;font-size:12px;word-break:break-all;background:#eef3f3;border-radius:8px;padding:7px 9px;color:#36515b}
.figure{background:white;border:1px solid var(--line);border-radius:16px;padding:10px;overflow:hidden}.figure svg{display:block;width:100%;height:auto;border-radius:11px}.caption{font-size:12px;color:var(--muted);padding:8px 5px 2px}.flow{display:flex;gap:8px;align-items:stretch;overflow:auto;padding:6px 0}.step{min-width:165px;flex:1;background:white;border:1px solid var(--line);border-radius:13px;padding:13px}.step b{display:block;color:var(--navy);margin:4px 0}.step small{color:var(--muted)}.arrow{display:grid;place-items:center;font-size:24px;color:var(--teal)}.n{display:inline-grid;place-items:center;width:25px;height:25px;border-radius:50%;background:var(--teal);color:white;font-weight:850;font-size:12px}
.decision{border-left:4px solid var(--teal)}.decision .rec{margin-top:10px;padding:8px 10px;border-radius:9px;background:#e8f4f2;color:#1e5d58;font-size:13px}.truth{background:var(--navy);color:white;border-radius:15px;padding:18px}.truth h3{color:white}.truth code{color:#8fe3db}.truth .muted{color:#b8cdd3}.sources li{margin:7px 0}.foot{border-top:1px solid var(--line);margin-top:46px;padding-top:20px;color:var(--muted);font-size:12px}
@media(max-width:900px){.g4,.g3,.g2{grid-template-columns:1fr}.page{padding-left:14px;padding-right:14px}.section h2{font-size:26px}.flow{flex-direction:column}.arrow{transform:rotate(90deg)}}@media print{.nav{display:none}.hero{padding:30px 20px}.card{box-shadow:none}.section{break-inside:avoid}.page{max-width:none}}
</style>
</head>
<body>
<header class="hero"><div class="wrap">
  <div class="eyebrow">SuperRAN × ChannelHub × Sionna · physical-model audit</div>
  <h1>AAU 馈电矩阵、双极化、slot 采样与 SRS 估计深审</h1>
  <p class="lead">回答 6° 电下倾从哪来、192×64 的 F 为什么成立、+45° 信息还缺什么、14-symbol 是否必要，以及 LS/MMSE 会不会改变干扰方向性。结论严格区分“数学自洽”“当前代码行为”和“已由实物证明”。</p>
  <div class="pills"><span class="pill">源码逐行对账</span><span class="pill">标准与 Sionna 2.0.1 对照</span><span class="pill">Internal / RT / QuaDRiGa 分层验证</span><span class="pill warn">实测标定仍待输入</span><span class="pill">2026-08-13</span></div>
</div></header>
<nav class="nav"><div><a href="#answer">结论</a><a href="#tilt">6° 下倾</a><a href="#fmatrix">F 矩阵</a><a href="#polar">双极化</a><a href="#compare">三方对照</a><a href="#time">slot 采样</a><a href="#srs">SRS/估计</a><a href="#roadmap">落地路线</a><a href="#decisions">待决策</a><a href="#evidence">证据</a></div></nav>

<main class="page">
<section id="answer" class="section">
  <div class="kicker">00 · Direct answers</div><h2>先给结论：哪些成立，哪些只是“看上去成立”</h2>
  <div class="verdict"><b>F 的拓扑和功率归一在数学上成立；参数化方向图、固定下倾与理想 ±45° 已贯通 InternalSim 和 Sionna RT，但实测复 Jones/馈电标定仍未闭环。</b> 旧版逐 ray Frobenius 归一已经移除，不同方向 ray 的相对阵元/子阵增益会保留。slot 级系统仿真只保留一个 H 是合理抽象，但 InternalSim 目前仍不能把 <code>num_ofdm_symbols</code> 直接改成 1，因为单点 Doppler 分支尚未在非零 slot midpoint 求相位。</div>
  <div class="grid g4" style="margin-top:16px">
    <div class="card metric"><span class="status warn">参数未溯源</span><div class="big" style="margin-top:10px">6°</div><div class="lab">2026-08-01 提交时加入的默认值</div><div class="tiny">commit 说明没有给产品规格、测量或标准出处</div></div>
    <div class="card metric"><span class="status ok">数学通过</span><div class="big" style="margin-top:10px">192×64</div><div class="lab">F：64 个互不重叠的 1→3 馈电列</div><div class="tiny">rank=64；FᴴF=I；每列 3 非零</div></div>
    <div class="card metric"><span class="status ok">后端已对齐</span><div class="big" style="margin-top:10px">±45°</div><div class="lab">p0=+45° / p1=−45°</div><div class="tiny">Internal Jones 与 Sionna 专用 slant registry 同序；仍非实测 Jones</div></div>
    <div class="card metric"><span class="status warn">需改后降维</span><div class="big" style="margin-top:10px">14 → 1</div><div class="lab">系统级默认可降为 slot midpoint</div><div class="tiny">先修 T=1 Doppler 与 Sionna 采样频率</div></div>
  </div>
  <div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>用户问题</th><th>明确回答</th><th>当前实现</th><th>建议</th></tr></thead><tbody>
    <tr><td>6° 从哪来？</td><td>仓库里找不到外部依据，是工程默认，不是已验证产品事实。</td><td>低层可传任意浮点数；普通用户面板未暴露。</td><td>暴露 <code>electrical_downtilt_deg</code>，机械下倾另设；未拿到标定前标记 assumed。</td></tr>
    <tr><td>F 为什么对？</td><td>它精确编码“每个 RF 端口只驱动同位置、同极化的 3 个垂直阵子”；列支撑不相交且单位范数。</td><td>数值结构和功率守恒通过；产品幅相、互耦、端口序未实测闭环。</td><td>保留 F 拓扑，加载预置馈电幅相/互耦矩阵后再称产品级正确。</td></tr>
    <tr><td>预置 +45° 信息够吗？</td><td>有用但不够。还需第二支路角度、端口顺序、XPD/XPR、方向/频率相关复 Jones、幅相校准。</td><td>当前临时合同为 p0=+45°、p1=−45°；Internal 与 Sionna RT 已同序执行并落盘。</td><td>拿到端口定义和实测 Jones 后替换临时模型，不改 canonical 端口编号。</td></tr>
    <tr><td>一 slot 一个 H 行吗？</td><td>对 RBG/TTI 系统级仿真是典型且推荐的；对 DMRS 插值、ICI、symbol 波束切换不够。</td><td>Internal 先生成 symbol 网格再取中点；RT 已显式传 symbol sampling frequency 与 UE velocity。</td><td>先补 Internal 单点 midpoint Doppler，再把 <code>slot_midpoint</code> 作为系统默认。</td></tr>
    <tr><td>LS 会让干扰没方向吗？</td><td>不会。污染项仍包含干扰信道的空间向量；LS 只是不会利用协方差去抑制它。</td><td><code>ls_mmse</code> 只有频域 PDP LMMSE，不是时频空 MMSE；更大的问题是当前观测不是物理的 Y=HX。</td><td>先修多端口观测，再提供 LS、LMMSE-f/t/s、Kalman 等可选档。</td></tr>
  </tbody></table></div>
</section>

<section id="tilt" class="section">
  <div class="kicker">01 · Electrical downtilt</div><h2>6° 是怎么来的，以及为什么“可配置”还不等于“生效”</h2>
  <p class="intro"><code>company_antenna_block()</code> 在 2026-08-01 的提交中把默认值写成 6.0；提交说明详细记录了 1→3、0.5λ/0.67λ，却没有说明 6° 来自 BOM、天线规格书、RET 配置还是方向图测量。因此它只能被称为当前工程假设。</p>
  <div class="grid g2">
    <div class="card"><h3>1→3 子阵的下倾相位</h3><div class="eq">__EQ_TILT__</div><p>对于 <code>z=[−0.67,0,+0.67]λ</code>、等幅馈电和 β=6°，三路附加相位约为 <b>[−25.21°, 0°, +25.21°]</b>。按当前符号约定，发射阵因子在水平面以下 6° 附近取峰值。</p></div>
    <div class="card"><h3>任意配置：低层已支持，产品层未支持</h3><ul class="tight"><li><b>支持：</b><code>FixedVerticalSubarrayConfig.fixed_downtilt_deg</code> 接受浮点值，<code>company_antenna_block(fixed_downtilt_deg=x)</code> 也能传入。</li><li><b>未支持：</b>普通用户的顶层可编辑参数没有该键，只能直接提供嵌套 <code>bs_antenna</code> 配置。</li><li><b>标签已修复：</b><code>calibration_id</code> 现在同时编码端口布局版本与下倾角，例如 <code>...pol-h-v-top-down-v2-dt6deg</code>；改下倾不会再沿用旧标签。</li><li><b>应区分：</b>电下倾改变馈电相位；机械下倾改变整个面板坐标系，不能共用一个参数。</li></ul></div>
  </div>
  <div class="callout green" style="margin-top:15px"><h3>逐 ray 归一缺陷已修复，并有反向门</h3>当前 InternalSim 不再把每条 ray 除以自身 Frobenius 范数；只在所有簇/ray 合成后执行一次全信道小尺度归一。因此 HPBW、isotropic-vs-directional 与 0°/6°/10° 下倾会改变不同到离角 ray 的相对功率。单方向增益、multi-cluster 方向形状和绝对链路预算均有回归测试；尚未完成的是实测复 Jones/馈电数据，而不是“6° 完全不生效”。</div>
  <div class="callout blue" style="margin-top:13px"><h3>推荐接口</h3><code>electrical_downtilt_deg</code>：用户可配置、按天线校准版本校验范围；<code>mechanical_downtilt_deg</code>：独立旋转面板；<code>downtilt_source</code>：<code>measured | product_spec | assumed</code>。在实测值未确认前，默认 6°可以保留以兼容，但结果页必须显示“假设值”。</div>
</section>

<section id="fmatrix" class="section">
  <div class="kicker">02 · Fixed analog feed</div><h2>192×64 的 F：物理接线表的线性代数表达</h2>
  <p class="intro">64 是 RF/数字端口数：8H×4V×2pol；192 是极化支路数：8H×12V×2pol，也就是 96 个空间位置、每个位置两条极化 feed。F 不是“凭空把 64 维扩成 192 维”，而是 64 个不可独立调节的固定模拟子阵。</p>
  <div class="figure">__F_SVG__<div class="caption">图 1：F 的行/列、1→3 馈电以及 192 条物理极化支路。SVG 可单独用于文档。</div></div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>canonical 索引和非零元素</h3><div class="eq">__EQ_F_INDEX__</div><p>64T 与 256T 统一使用 <code>pol_h_v + top_to_bottom</code>：极化块最慢，垂直行最快。每个列索引 r 只连接 <code>e(h,3v+q,p)</code> 三行，不跨水平位置、不跨 RF 垂直端口，也不跨极化。若硬件存在有意的交叉极化馈电或互耦，则需要在 F 前后另加校准/互耦矩阵，不能靠改端口数解决。</p></div>
    <div class="card"><h3>功率守恒与可控子空间</h3><div class="eq">__EQ_F_PROOF__</div><p>不同列支撑不相交；每列的 <code>w</code> 已归一。因此 F 是半酉矩阵：它保留 64 端口向量的总能量。<code>FFᴴ</code> 不是 192 维单位阵，而是秩 64 的投影，说明另 128 个物理模式不能由数字基带独立控制——这正是固定模拟馈电的物理含义。</p></div>
  </div>
  <div class="truth" style="margin-top:15px"><h3>“F 正确”的三层证据，不能混为一谈</h3><ol class="tight"><li><b>结构正确：</b>shape=[192,64]，每列 3 个非零、每行 1 个非零，极化不串线。</li><li><b>数学正确：</b>rank=64，最大 <code>|FᴴF−I|=2.23×10⁻¹⁶</code>，随机向量相对功率误差 <code>4.20×10⁻¹⁶</code>。</li><li><b>产品正确：</b><span class="muted">尚未证明。</span>还需实测/规格给出的三路幅相、端口顺序、频率响应、互耦和 Jones 方向图。当前 effective 与 physical-reference 一致性测试共用同一个 F，只能证明实现一致，不能独立证明硬件。</li></ol></div>
</section>

<section id="polar" class="section">
  <div class="kicker">03 · Dual polarization</div><h2>双极化不是天线数乘 2：空间位置、Jones 端口和传播耦合是三件事</h2>
  <div class="figure">__POL_SVG__<div class="caption">图 2：两个共址斜极化端口，经每条 ray 的 2×2 极化信道耦合；F 只描述接线，不负责制造交叉极化泄漏。</div></div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>阵元端口是复 Jones 向量</h3><div class="eq">__EQ_JONES__</div><p>±45°是阵元局部坐标里的 slant angle。只在波束中心、理想对称模型下，才可简写成 <code>[1,±1]/√2</code>。离开波束中心后，<code>Cθ</code>、<code>Cφ</code> 会随 θ、φ、频率变化并带复相位。</p></div>
    <div class="card"><h3>每条 ray 还有 2×2 传播耦合</h3><div class="eq">__EQ_POL_RAY__</div><p>κ 是 ray 的 XPR；四个 Φ 是传播相位。最终系数由接收 Jones、传播 G、发射 Jones、阵列几何相位共同决定。双极化增加的是两个共址可激励端口，不是两个独立空间位置。</p></div>
  </div>
  <div class="callout" style="margin-top:14px"><h3>“预置用 +45°”是需要的信息，但还不能完成配置</h3>它至少确认了一条支路的斜角。还要确认：第二支路是否 −45°；<code>p=0/p=1</code> 的物理端口顺序；发射和接收端口是否同序；XPD/XPR 随方向、频率的定义；两支路相对幅相；1→3 馈电在两极化上是否同一标定。建议临时预设为 <code>p0=+45°, p1=−45°</code>，但元数据明确写 <code>provisional=true</code>。</div>
  <div class="callout green" style="margin-top:13px"><h3>两条后端的临时极化合同已对齐</h3><b>InternalSim：</b><code>element_jones()</code> 的理想 +45°/−45° 会与每条 ray 的 2×2 XPR coupling 收缩。<b>Sionna RT：</b>没有误用内置 <code>cross</code>（其顺序是 −45°/+45°），而是按配置注册专用 <code>[+π/4,−π/4]</code> slant 列表，保持 p0/p1 与 canonical 端口一致。边界仍是：两者使用参数化/理想 Jones，尚未导入预置方向与频率相关的实测复场。</div>
</section>

<section id="compare" class="section">
  <div class="kicker">04 · ChannelHub vs Sionna</div><h2>取长补短：谁已经有哪块能力，谁又在哪些地方踩空</h2>
  <div class="table-wrap"><table><thead><tr><th>维度</th><th>ChannelHub internal</th><th>ChannelHub 的 Sionna RT 适配层</th><th>Sionna 原生能力</th><th>SuperRAN 目标</th></tr></thead><tbody>
    <tr><td>阵列/F</td><td>有 1→3 的 192×64 F、端口排列、相位中心与两条等价计算路径。</td><td>能用 64 有效端口或 192 AE 后投影 F。</td><td>双极化共址位置、一/双极化 pattern、任意自定义 pattern。</td><td>保留 F；补产品标定、互耦、统一端口语义。</td></tr>
    <tr><td>方向图/下倾</td><td>3GPP 式抛物线标量 pattern；相对 ray 增益保留；measured_jones 尚未实现。</td><td>自定义 port/element pattern 已进入逐 path 响应。</td><td>pattern 返回复 <code>(Cθ,Cφ)</code>，可计算增益并注册自定义 pattern。</td><td>用同一份预置 Jones 数据替换两端临时模型，禁止后端口径漂移。</td></tr>
    <tr><td>双极化</td><td>理想 +45/−45 Jones + 2×2 XPR 随机耦合。</td><td>专用 slant registry 保持 +45/−45 与 canonical 同序。</td><td><code>cross=[−π/4,+π/4]</code>，支持两种 38.901 极化模型。</td><td>临时合同已对齐；下一步导入实测复 Jones/XPD。</td></tr>
    <tr><td>时间采样</td><td>symbol 网格后取中点，能做逐 ray Doppler；T=1 时非零 offset 仍有缺口。</td><td>CFR 已显式传 symbol sampling frequency，Receiver 已设置 velocity。</td><td>CFR 支持指定采样频率、时间步数和 path Doppler。</td><td>补 Internal 单中点求值；系统默认单中点，PHY 研究才保留 14。</td></tr>
    <tr><td>SRS</td><td>comb 2/4/8、带宽表、跳频、长 ZC 与短低 PAPR 序列都较完整；默认生成端仍写死单端口。</td><td>复用了同一估计抽象。</td><td>PHY 提供通用资源栅格 LS 与时/频/空 LMMSE，但不是替代 NR SRS 资源生成器。</td><td>复用当前 38.211 SRS 生成器；重建物理多端口 Y=HX 观测。</td></tr>
    <tr><td>估计器</td><td>ideal、LS+线性、频域 PDP-LMMSE、两类跳频 LS。</td><td>同上。</td><td>LS 基线；LMMSE 可按 f/t/s 顺序组合，PUSCH 默认仍是 LS+线性。</td><td>估计器可配置，但先确保协方差来源与观测模型真实。</td></tr>
  </tbody></table></div>
</section>

<section id="time" class="section">
  <div class="kicker">05 · Time abstraction</div><h2>14 个 symbol 还是 1 个 slot 快照：两种都典型，关键看消费者</h2>
  <div class="grid g3">
    <div class="card"><h3>系统级 RBG/TTI</h3><span class="status ok">推荐默认</span><p>调度、PF、TBS、PRB 利用率以 slot/TTI 为步长，通常每 slot 一个代表性 H/SINR 即可。Sionna SYS 的系统循环也是按 slot 推进，而不是要求调度器消费 14 个 H。</p></div>
    <div class="card"><h3>链路级 OFDM</h3><span class="status info">按需开启</span><p>研究 DMRS→data 插值、symbol 间波束切换、极高速时变、ICI/相位噪声时，应保留 14-symbol 资源栅格。Sionna PHY 的示例资源栅格正是这类用法。</p></div>
    <div class="card"><h3>折中档</h3><span class="status info">很实用</span><p><code>pilot_and_data</code> 只生成参考信号时刻和数据代表时刻两个样本，可表达 CSI 老化，又比 14 点便宜。对于 SRS 周期与 DL 使用时刻分离尤其合适。</p></div>
  </div>
  <div class="eq">__EQ_TIME__</div>
  <div class="flow">
    <div class="step"><span class="n">1</span><b>slot_midpoint</b><small>1 点；系统仿真默认，t=t_slot+T_slot/2</small></div><div class="arrow">→</div>
    <div class="step"><span class="n">2</span><b>pilot_and_data</b><small>2 点；显式建模 SRS/CSI-RS 到数据时刻的相位演化</small></div><div class="arrow">→</div>
    <div class="step"><span class="n">3</span><b>symbol_grid</b><small>14 点；只给 symbol 级 PHY/ICI/插值研究</small></div>
  </div>
  <div class="callout red" style="margin-top:14px"><h3>不能现在直接设 T=1</h3>30 kHz SCS 下 slot=0.5 ms，平均 symbol 周期约 35.714 μs，当前取索引 7，即 250 μs。确定性检查显示：CDL 的 14 点中点与当前 T=1 相对差约 <b>0.306</b>，TDL 约 <b>2.046</b>；而 T=1 改变 <code>t_offset_s</code> 的结果完全不变。根因是 TDL 只在 <code>T&gt;1</code> 时加 Doppler，CDL 的单点 Doppler helper 也直接返回 1。正确优化是“直接在中点求一次相位”，不是“把 T 改成 1 然后沿用旧分支”。</div>
  <div class="callout green" style="margin-top:13px"><h3>Sionna RT 时间采样已修复</h3>适配层现在从 normal-CP 平均 symbol 周期计算 <code>sampling_frequency</code>，并把真实三维 UE velocity 写入 Receiver；120 km/h 的真实 RT 回归会检查相邻 symbol 的 H 确实变化。剩余优化是 InternalSim 的单点 midpoint Doppler，以及跨 slot 相位连续的显式合同。</div>
</section>

<section id="srs" class="section">
  <div class="kicker">06 · SRS and channel estimation</div><h2>当前是不是 ZC、是不是 LS，以及 MMSE 到底解决什么</h2>
  <div class="grid g2">
    <div class="card"><h3>SRS 序列：默认大带宽时是 ZC 派生，但名字不应一概叫 ZC</h3><p>当前实现覆盖 comb 2/4/8、64 行带宽表、cyclic shift、group/sequence hopping。长度 <code>Msc≥36</code> 使用长 ZC 基序列并循环扩展；<code>Msc∈{6,12,18,24}</code> 使用标准短低 PAPR 表序列。100 MHz、272 RB、comb2 的默认场景属于长序列，因此此时“ZC”没有错；通用枚举更准确的名字应是 <code>nr_srs_low_papr</code>，保留 <code>srs_zc</code> 兼容别名。</p></div>
    <div class="card"><h3>端口能力：生成器有 cyclic shift，但调用链仍是单端口抽象</h3><p><code>srs_sequence()</code> 接受 N_ap 和 port index，但当前 serving pilot 生成器写死 <code>N_ap=1,n_ap_index=0,n_cs=0</code>；观测端把 64×UE 的每个系数当成独立接收通道逐元素乘 X。38.211 Release 18 的 SRS resource 已允许 1/2/4/8 端口，而当前 helper 只接受 1/2/4。</p></div>
  </div>
  <div class="card" style="margin-top:15px"><h3>物理多端口观测应该是矩阵方程</h3><div class="eq">__EQ_SRS_OBS__</div><p>Y 是 64 个 gNB 接收端口上的实际观测，X 是多个 SRS 端口在一组 RE 上的已知序列。要恢复 64×4 的 H，X 必须有足够的独立维度；可以来自 cyclic shift、comb、CDM 或多 symbol。当前 <code>Y_at_pilots=h_noisy*X</code> 再把 <code>BS×UE</code> flatten 的做法，相当于上帝视角逐系数观测，不是射频接收机看到的 Y。</p></div>
  <div class="grid g2" style="margin-top:15px">
    <div class="card"><h3>LS 不会抹掉干扰方向性</h3><div class="eq">__EQ_LS_CONTAM__</div><p>若干扰导频与目标 X 不正交，污染项包含 <code>H_i</code>，因此它仍然沿干扰用户的空间信道方向进入估计。LS 的问题是“不知道该方向应该被压制”，不是“把它变成各天线独立白噪声”。</p></div>
    <div class="card"><h3>LMMSE 利用协方差做方向性抑制</h3><div class="eq">__EQ_LMMSE__</div><p>只有当 <code>R_h</code>、干扰协方差 <code>R_i</code> 和噪声协方差可信时，空间 LMMSE 才能压制与目标协方差不一致的方向。协方差错配时，LMMSE 也可能引入偏差，所以它应是可选算法而不是“默认必胜开关”。</p></div>
  </div>
  <div class="table-wrap" style="margin-top:15px"><table><thead><tr><th>建议枚举</th><th>能力</th><th>依赖</th><th>定位</th></tr></thead><tbody>
    <tr><td><code>ideal</code></td><td>直接取真值</td><td>无</td><td>上界/调试，不代表可实现接收机</td></tr>
    <tr><td><code>ls_linear</code></td><td>导频 LS + 时频线性插值</td><td>正确的 X/Y/RE 布局</td><td>透明、稳健的默认基线；Sionna PUSCH 默认同类路线</td></tr>
    <tr><td><code>ls_delay_denoise</code></td><td>LS 后转 delay 域截窗/去噪</td><td>最大时延或 CP 先验</td><td>低成本增强</td></tr>
    <tr><td><code>lmmse_f</code></td><td>频域 LMMSE</td><td>PDP/τrms；当前所谓 ls_mmse 只做到这里</td><td>频率插值/去噪</td></tr>
    <tr><td><code>lmmse_tf</code></td><td>时频 LMMSE</td><td>PDP + Doppler/速度</td><td>高速与稀疏 SRS 周期</td></tr>
    <tr><td><code>lmmse_tfs</code></td><td>时频空 LMMSE</td><td>空间信道与干扰协方差</td><td>能真正利用干扰方向性</td></tr>
    <tr><td><code>kalman_tfs</code></td><td>递归跟踪</td><td>状态转移/过程噪声</td><td>连续移动与 CSI 老化</td></tr>
    <tr><td><code>sparse_angle_delay</code></td><td>角度-时延稀疏估计</td><td>阵列流形/网格或 off-grid 算法</td><td>研究档，不宜先做默认</td></tr>
  </tbody></table></div>
  <div class="callout green" style="margin-top:13px"><h3>推荐顺序</h3>先重建物理多端口 <code>Y=HX+I+N</code>，让 LS 在无噪声正交导频下能恢复 H；再接入频域、时域、空间 LMMSE。用 MMSE 包住错误观测模型，会让指标更漂亮，却不能让仿真更真实。</div>
</section>

<section id="roadmap" class="section">
  <div class="kicker">07 · Implementation route</div><h2>落地调整：先修物理正确性，再做复杂算法</h2>
  <div class="table-wrap"><table><thead><tr><th>优先级</th><th>改动</th><th>主要模块</th><th>验收方式</th></tr></thead><tbody>
    <tr><td><span class="status ok">已完成</span></td><td>移除逐 ray 功率归一，保留 Jones/方向图/子阵阵因子的相对路径功率。</td><td><code>internal_sim.py::_spatial_ray</code> 与最终小尺度归一</td><td>单方向、multi-cluster、isotropic/directional 与链路预算测试已通过。</td></tr>
    <tr><td><span class="status ok">已完成</span></td><td>统一临时极化合同：p0=+45°、p1=−45°；Sionna 使用专用 slant registry。</td><td><code>effective_array.py</code>、<code>sionna_rt.py</code>、配置/元数据</td><td>Internal Jones、Sionna registry、real-RT effective/physical 等价门均通过。</td></tr>
    <tr><td><span class="status warn">部分完成</span></td><td>RT sampling_frequency/velocity 已修；Internal 单时刻 midpoint Doppler 待补。</td><td>TDL/CDL generator、Sionna RT adapter</td><td><code>slot_midpoint(T=1)</code> 与 symbol 网格中点同 seed 等价；静止 UE 不变，移动 UE 相位连续。</td></tr>
    <tr><td><span class="status bad">P0</span></td><td>把 SRS 观测改成物理矩阵 Y=HX，并保留 RE/port 结构。</td><td>SRS resource、pilot builder、channel_est pipeline</td><td>1/2/4（后续 8）端口无噪声精确恢复；非正交干扰产生可预测空间污染。</td></tr>
    <tr><td><span class="status info">P1</span></td><td>暴露电/机械下倾、时间采样模式、估计器与协方差来源。</td><td>SuperRAN spec/UI、ChannelHub config contract</td><td>结果元数据完整、非法组合早失败、旧配置可迁移。</td></tr>
    <tr><td><span class="status info">P1</span></td><td>实现 <code>lmmse_tf</code> 与 <code>lmmse_tfs</code>，接口对齐 Sionna 的 f/t/s 可组合思想。</td><td>channel_est</td><td>匹配先验的 Monte Carlo MSE/BLER 在置信区间内优于 LS；错配场景明确标注退化。</td></tr>
    <tr><td><span class="status warn">P2</span></td><td>加载预置 measured Jones、馈电幅相、互耦与频率响应。</td><td>hardware calibration registry</td><td>方向图切面、端口 S 参数/幅相、空口或暗室测量闭环。</td></tr>
  </tbody></table></div>
  <div class="callout blue" style="margin-top:14px"><h3>复杂度收益怎么承诺</h3>把纯时间轴的 14 点改为 1 点，理论上能让该部分计算和临时张量最多缩小约 14 倍；端到端不会必然快 14 倍，因为 ray 几何、频率 DFT、路径损耗和文件 I/O 不随 T 等比减少。应在 P0 正确性回归通过后，用 internal/RT 两后端分别基准。</div>
</section>

<section id="decisions" class="section">
  <div class="kicker">08 · Decisions</div><h2>需要外部数据拍板的 5 项</h2>
  <div class="grid g2">
    <div class="card decision"><h3>D1 · 极化端口契约</h3><p>现有输入说明“+45°”具体是端口 0，还是面板极化对的统称？另一支是否 −45°？发/收端口顺序是否一致？</p><div class="rec"><b>推荐：</b>暂定 p0=+45°、p1=−45°，但标记 provisional，拿端口定义表后冻结。</div></div>
    <div class="card decision"><h3>D2 · 6° 的产品来源</h3><p>需要天线规格书/RET 默认、暗室方向图或网络规划配置，确认它是固定电下倾还是场景级 RET 值。</p><div class="rec"><b>推荐：</b>6°暂保留兼容；用户可改；电下倾与机械下倾分开；结果展示来源。</div></div>
    <div class="card decision"><h3>D3 · 系统时间抽象</h3><p>系统层是否将 slot midpoint 设为默认，symbol_grid 仅在明确研究 DMRS/ICI 时开启？</p><div class="rec"><b>推荐：</b>同意。体验速率/调度默认 1 点；CSI 老化场景优先使用 2 点 pilot_and_data。</div></div>
    <div class="card decision"><h3>D4 · 估计器默认档</h3><p>物理观测修好后，默认用透明的 LS+线性，还是直接用依赖先验的 LMMSE？</p><div class="rec"><b>推荐：</b>默认 LS；LMMSE-t/f/s 作为可配置算法与对照组。Sionna 的 PUSCH receiver 默认也采用 LS+线性。</div></div>
    <div class="card decision"><h3>D5 · 实测标定输入格式</h3><p>能提供的是 2D/3D gain 切面、复 Jones 表、S 参数、三路馈电幅相，还是只有 HPBW/XPD 标量？</p><div class="rec"><b>推荐：</b>优先索要按频点、θ、φ、port 的复 <code>Eθ/Eφ</code>，以及每 RF 端口三路馈电幅相；标量只能作为临时模型。</div></div>
  </div>
</section>

<section id="evidence" class="section">
  <div class="kicker">09 · Evidence ledger</div><h2>源码锚点、确定性验证与官方依据</h2>
  <div class="grid g2">
    <div class="card"><h3>本轮验证</h3><ul class="tight"><li>MSG 当前源码全量 unit：<b>427 passed</b>、14 skipped；Sionna 专项含真实 RT：<b>11 passed</b>。</li><li>SuperRAN 64T/256T 合同：<b>14 passed</b>；真实 Octave/QuaDRiGa 已跑 64T <code>get_channels→fr()</code>，并用真实 builder 验证 64T/256T 的 192×64 与 1536×256 coupling。</li><li>64T F 数值审计：rank=64、192 nonzeros、每列 3、每行 1、<code>max|FᴴF−I|=2.23e−16</code>。</li><li>下倾/方向图敏感性反例与 T=1 Doppler 差异：固定 seed 的确定性对比，不是趋势猜测。</li></ul></div>
    <div class="card"><h3>关键源码锚点</h3><div class="path">SuperRAN: src/superran/hardware.py:81-110</div><div class="path">ChannelHub: src/msg_embedding/phy_sim/effective_array.py:133-187, 421-443, 475-493</div><div class="path">ChannelHub: src/msg_embedding/data/sources/internal_sim.py:860-889, 1958-1987, 3419-3433</div><div class="path">ChannelHub: src/msg_embedding/data/sources/sionna_rt.py:1148-1172, 1588-1620</div><div class="path">ChannelHub: src/msg_embedding/ref_signals/srs.py:1-13, 164-181, 240-321</div><div class="path">ChannelHub: src/msg_embedding/channel_est/pipeline.py:120-165</div></div>
  </div>
  <div class="card sources" style="margin-top:15px"><h3>官方资料</h3><ul>
    <li><a href="https://www.etsi.org/deliver/etsi_tr/138900_138999/138901/19.03.00_60/tr_138901v190300p.pdf">3GPP TR 38.901 V19.3.0（ETSI）</a>：交叉极化面板、±45° slant、Jones 场分量、XPR 与 ray 信道系数。</li>
    <li><a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138211/18.06.00_60/ts_138211v180600p.pdf">3GPP TS 38.211 V18.6.0（ETSI）</a>：低 PAPR 序列、SRS 端口/comb/cyclic shift/带宽与映射。</li>
    <li><a href="https://nvlabs.github.io/sionna/rt/api/antenna_array.html">Sionna RT · Antenna Arrays</a>：双极化端口共址、array_size 与 num_ant 的区别。</li>
    <li><a href="https://nvlabs.github.io/sionna/rt/api/antenna_pattern.html">Sionna RT · Antenna Patterns</a>：复 Jones <code>(Cθ,Cφ)</code> 与 38.901 polarisation model。</li>
    <li><a href="https://nvlabs.github.io/sionna/_modules/sionna/rt/antenna_pattern.html">Sionna RT · polarization registry source</a>：<code>VH=[0,π/2]</code>，<code>cross=[−π/4,+π/4]</code>。</li>
    <li><a href="https://nvlabs.github.io/sionna/rt/api/paths.html">Sionna RT · Paths.cfr</a>：<code>sampling_frequency</code> 默认 1 Hz 与 Doppler 时间演化定义。</li>
    <li><a href="https://nvlabs.github.io/sionna/_modules/sionna/phy/ofdm/channel_estimation.html">Sionna PHY · OFDM channel estimation</a>：LS、频/时/空 LMMSE 与组合顺序。</li>
    <li><a href="https://nvlabs.github.io/sionna/_modules/sionna/phy/nr/pusch_receiver.html">Sionna PHY · PUSCH receiver</a>：默认 PUSCH LS estimator + linear interpolation。</li>
    <li><a href="https://nvlabs.github.io/sionna/v2.0.0/sys/tutorials/notebooks/End-to-End_Example.html">Sionna SYS · end-to-end system example</a>：slot 级系统循环与 PHY resource-grid 抽象的分层。</li>
  </ul></div>
  <div class="callout" style="margin-top:14px"><h3>结论边界</h3>本报告完成了代码行为、标准能力和确定性数学性质的审查，并给出了可实施路线；没有把缺失的预置端口定义、实测 Jones 方向图或馈电标定“猜成已知”。在这些输入到位前，F 可称为拓扑正确/功率正确，不能称为目标产品全物理正确。</div>
</section>

<footer class="foot"><b>SuperRAN · AAU/F/polarization/time/SRS audit</b><br>离线自包含 KaTeX：__KATEX_STATUS__；公式带原生 MathML 回退。生成脚本：<span class="mono">C:\Vibe\Wireless\SuperRAN\scripts\make_aau_polarization_srs_audit.py</span></footer>
</main>
__KATEX_UPGRADE__
</body></html>
"""


FORMULAS = {
    "__EQ_TILT__": r"w_q=\frac{A_q e^{j\phi_q}e^{j2\pi z_q\sin\beta}}{\sqrt{\sum_{\ell=0}^{2}A_\ell^2}},\qquad z_q\in\{-0.67,0,+0.67\}\lambda",
    "__EQ_F_INDEX__": r"\begin{aligned}r(h,v,p)&=32p+4h+v,\quad e(h,v_{\rm AE},p)=96p+12h+v_{\rm AE}\\F_{e(h,3v+q,p),\,r(h,v,p)}&=w_q,\quad q\in\{0,1,2\}\end{aligned}",
    "__EQ_F_PROOF__": r"\begin{aligned}\operatorname{supp}(F_{:,r})\cap\operatorname{supp}(F_{:,s})&=\varnothing\ (r\ne s),\quad \|F_{:,r}\|_2=1\\F^{\mathrm H}F&=I_{64},\quad \|Fx\|_2=\|x\|_2,\quad \operatorname{rank}(F)=\operatorname{rank}(FF^{\mathrm H})=64\end{aligned}",
    "__EQ_JONES__": r"\mathbf f^{(p)}(\theta,\varphi,f)=\begin{bmatrix}C_\theta^{(p)}(\theta,\varphi,f)\\C_\varphi^{(p)}(\theta,\varphi,f)\end{bmatrix},\qquad \mathbf f_{\pm45^\circ}\approx\frac{g}{\sqrt2}\begin{bmatrix}1\\\pm1\end{bmatrix}",
    "__EQ_POL_RAY__": r"\mathbf G_{n,m}=\begin{bmatrix}e^{j\Phi_{\theta\theta}}&\kappa_{n,m}^{-1/2}e^{j\Phi_{\theta\varphi}}\\\kappa_{n,m}^{-1/2}e^{j\Phi_{\varphi\theta}}&e^{j\Phi_{\varphi\varphi}}\end{bmatrix},\qquad h_{n,m}\propto \mathbf f_{\rm rx}^{\mathsf T}\mathbf G_{n,m}\mathbf f_{\rm tx}\,e^{j\psi_{\rm array}}e^{j2\pi\nu_{n,m}t}",
    "__EQ_TIME__": r"\Delta f=30\,\mathrm{kHz}:\quad T_{\rm slot}=0.5\,\mathrm{ms},\qquad \bar T_{\rm sym}=\frac{T_{\rm slot}}{14}=35.714\,\mu\mathrm{s},\qquad t_{\rm mid}=7\bar T_{\rm sym}=250\,\mu\mathrm{s}",
    "__EQ_SRS_OBS__": r"\mathbf Y[k]=\mathbf H[k]\mathbf X[k]+\sum_i\mathbf H_i[k]\mathbf X_i[k]+\mathbf N[k],\qquad \widehat{\mathbf H}_{\rm LS}=\mathbf Y\mathbf X^{\mathrm H}(\mathbf X\mathbf X^{\mathrm H})^{-1}",
    "__EQ_LS_CONTAM__": r"\widehat{\mathbf H}_{\rm LS}=\mathbf H+\sum_i\mathbf H_i\mathbf X_i\mathbf X^{\mathrm H}(\mathbf X\mathbf X^{\mathrm H})^{-1}+\mathbf N\mathbf X^{\mathrm H}(\mathbf X\mathbf X^{\mathrm H})^{-1}",
    "__EQ_LMMSE__": r"\widehat{\mathbf h}_{\rm LMMSE}=\mathbf R_h\mathbf A^{\mathrm H}\left(\mathbf A\mathbf R_h\mathbf A^{\mathrm H}+\mathbf R_i+\mathbf R_n\right)^{-1}\mathbf y",
}


def main() -> None:
    page = HTML
    replacements = {
        "__KATEX_HEAD__": katex.head_assets(),
        "__KATEX_UPGRADE__": katex.upgrade_script(),
        "__KATEX_STATUS__": "已内联，页面无需联网" if katex.available() else "资产缺失，仅使用 MathML",
        "__F_SVG__": inline_svg(F_SVG),
        "__POL_SVG__": inline_svg(POL_SVG),
    }
    replacements.update({marker: formula(tex) for marker, tex in FORMULAS.items()})
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    missing = re.findall(r"__[A-Z0-9_]+__", page)
    if missing:
        raise RuntimeError("unexpanded markers: " + repr(sorted(set(missing))))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    check = OUT.read_text(encoding="utf-8")
    print(OUT)
    print("bytes=" + str(OUT.stat().st_size))
    print("formulas=" + str(len(FORMULAS)))
    print("katex=" + str(katex.available()))
    print("utf8_ok=" + str(check.startswith("<!doctype html>") and "�" not in check))
    print("svg_count=" + str(check.count("<svg")))


if __name__ == "__main__":
    main()
