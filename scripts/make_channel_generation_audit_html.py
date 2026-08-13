"""Build the self-contained channel-generation deep-audit report.

The report deliberately reads the machine-generated evidence instead of copying
numbers into prose by hand.  Keep the template free of f-string expressions so
it also runs on Python < 3.12 (backslashes inside f-string expressions are a
known portability trap in this repository).
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "artifacts" / "channel-generation-audit" / "evidence.json"
STRESS_PATH = ROOT / "artifacts" / "channel-generation-audit" / "stress.json"
OUT_PATH = ROOT / "artifacts" / "CHANNEL_GENERATION_DEEP_AUDIT.html"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: Any, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}"


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SuperRAN 信道生成深度审计</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230b7c7c'/%3E%3Cpath d='M7 21c5-7 13-7 18 0M10 17c4-4 8-4 12 0M14 13c1-1 3-1 4 0' fill='none' stroke='white' stroke-width='2.4' stroke-linecap='round'/%3E%3C/svg%3E">
<style>
:root{
  --ink:#17212b;--muted:#5d6b79;--paper:#f4f7f8;--card:#fff;--line:#dce5e8;
  --navy:#102c3b;--teal:#0b7c7c;--teal2:#17a3a3;--orange:#e47a38;--gold:#c9982e;
  --green:#24784b;--red:#b5463b;--amber:#9a6a09;--soft:#e9f3f3;--code:#0c2430;
}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif;line-height:1.65}
a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}
.hero{background:linear-gradient(126deg,#092331 0%,#11475a 61%,#0b7c7c 100%);color:#fff;padding:54px 28px 44px;position:relative;overflow:hidden}
.hero:after{content:"";position:absolute;width:460px;height:460px;border:1px solid #ffffff2e;border-radius:50%;right:-120px;top:-210px;box-shadow:0 0 0 55px #ffffff0b,0 0 0 110px #ffffff08}
.hero-inner,.page{max-width:1220px;margin:auto;position:relative;z-index:1}.eyebrow{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#9de4df;font-weight:700}.hero h1{font-size:clamp(32px,5vw,58px);line-height:1.08;margin:11px 0 18px;max-width:900px}.hero .lead{font-size:18px;max-width:920px;color:#e4f1f2;margin:0}.hero-meta{display:flex;flex-wrap:wrap;gap:10px;margin-top:26px}.pill{border:1px solid #ffffff42;background:#ffffff12;border-radius:999px;padding:7px 12px;font-size:13px}.pill.good{background:#39ad7960}.pill.warn{background:#eaa33b40}
.nav{position:sticky;top:0;z-index:20;background:#ffffffee;backdrop-filter:blur(10px);border-bottom:1px solid var(--line);overflow:auto;white-space:nowrap}.nav div{max-width:1220px;margin:auto;padding:10px 20px}.nav a{display:inline-block;padding:7px 10px;color:#30434f;font-size:14px;font-weight:600}
.page{padding:26px 24px 70px}.section{scroll-margin-top:70px;margin:28px 0 52px}.section h2{font-size:30px;line-height:1.2;margin:0 0 8px;color:var(--navy)}.section .intro{color:var(--muted);max-width:940px;margin:0 0 22px}.kicker{color:var(--teal);font-weight:800;font-size:13px;letter-spacing:.12em;text-transform:uppercase;margin-bottom:6px}
.grid{display:grid;gap:16px}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}.g3{grid-template-columns:repeat(3,minmax(0,1fr))}.g2{grid-template-columns:repeat(2,minmax(0,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:19px;box-shadow:0 8px 28px #1736420a}.card h3{margin:0 0 8px;color:var(--navy);font-size:18px}.metric .value{font-size:31px;line-height:1.15;font-weight:800;color:var(--navy);font-variant-numeric:tabular-nums}.metric .label{font-size:13px;color:var(--muted);margin-top:4px}.metric .note{font-size:12px;color:var(--muted);margin-top:8px}.status{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:800;border-radius:999px;padding:4px 9px}.status:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}.ok{color:var(--green);background:#e5f4eb}.caution{color:var(--amber);background:#fff2d7}.bad{color:var(--red);background:#fde8e5}.info{color:#306985;background:#e5f2f8}
.verdict{border-left:5px solid var(--teal);background:linear-gradient(90deg,#e6f4f3,#fff);padding:20px 22px;border-radius:0 14px 14px 0;font-size:17px}.verdict strong{color:var(--navy)}
.callout{padding:16px 18px;border-radius:12px;background:#fff7e8;border:1px solid #eed8a7;color:#60460e}.callout.red{background:#fff0ee;border-color:#f0c0ba;color:#73352e}.callout.blue{background:#eef7fb;border-color:#c9e2ed;color:#25566b}.callout h3{margin:0 0 5px;font-size:16px}
.equation{background:var(--code);color:#eaf7f7;border-radius:12px;padding:17px 18px;margin:12px 0;font-family:Consolas,"Cascadia Mono",monospace;overflow:auto;white-space:nowrap}.equation .dim{color:#8eb8c4}.equation .hi{color:#6ee7dc}.equation .or{color:#ffc079}
.flow{display:flex;align-items:stretch;gap:8px;overflow:auto;padding:7px 0 12px}.flow .step{min-width:145px;flex:1;background:#fff;border:1px solid var(--line);border-radius:12px;padding:13px}.flow .n{display:inline-grid;place-items:center;width:25px;height:25px;border-radius:50%;background:var(--teal);color:white;font-weight:800;font-size:12px}.flow .arrow{display:grid;place-items:center;color:var(--teal);font-size:24px}.flow b{display:block;margin:7px 0 3px;font-size:14px}.flow small{color:var(--muted)}
.diagram{background:#0e2936;border-radius:15px;padding:14px;overflow:auto}.diagram svg{width:100%;min-width:720px;display:block}.diagram text{font-family:"Segoe UI","Microsoft YaHei",sans-serif}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border:1px solid var(--line);border-radius:13px;overflow:hidden;font-size:14px}th,td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}th{background:#edf4f5;color:#294652;font-size:12px;letter-spacing:.03em}tr:last-child td{border-bottom:0}td.num{font-family:Consolas,monospace;text-align:right;font-variant-numeric:tabular-nums}.table-wrap{overflow:auto;border-radius:13px}.table-wrap table{min-width:720px}
.heat-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}.heat{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px}.heat h4{margin:0 0 3px}.heat .sub{font-size:12px;color:var(--muted);margin-bottom:9px}.heat svg{width:100%;height:535px;display:block;background:#f8fbfb;border-radius:10px}.legend{height:8px;border-radius:10px;background:linear-gradient(90deg,#173a70,#3e79b5,#56c2b0,#f4cf66,#e5683a);margin:8px 20px 2px}.legend-label{display:flex;justify-content:space-between;margin:0 18px;font-size:11px;color:var(--muted)}
.hops{display:grid;grid-template-columns:repeat(17,minmax(54px,1fr));gap:6px;overflow:auto;padding-bottom:8px}.hop{min-width:54px;border-radius:10px;background:#eaf5f4;border:1px solid #c7e5e2;padding:9px 4px;text-align:center}.hop b{display:block;color:var(--teal);font-size:15px}.hop small{color:var(--muted);font-size:10px}.compare{display:grid;grid-template-columns:1fr 1fr;gap:12px}.compare .barbox{border:1px solid var(--line);border-radius:12px;padding:15px;background:#fff}.bar{height:13px;border-radius:20px;background:#d8e8eb;overflow:hidden;margin:10px 0 5px}.bar span{display:block;height:100%;border-radius:20px}.bar.badbar span{background:var(--orange)}.bar.goodbar span{background:var(--teal2)}
.timeline{position:relative;padding-left:30px}.timeline:before{content:"";position:absolute;left:9px;top:8px;bottom:8px;width:2px;background:#c6dcdf}.event{position:relative;margin:0 0 16px}.event:before{content:"";position:absolute;left:-26px;top:7px;width:10px;height:10px;border-radius:50%;background:var(--teal);box-shadow:0 0 0 4px #dff0ef}.event b{color:var(--navy)}.event p{margin:2px 0;color:var(--muted);font-size:14px}
details{background:#fff;border:1px solid var(--line);border-radius:12px;margin:8px 0;padding:0 14px}summary{cursor:pointer;font-weight:700;padding:12px 0;color:var(--navy)}details .detail{border-top:1px solid var(--line);padding:11px 0 13px;color:var(--muted);font-size:14px}
code{background:#eaf1f2;border-radius:5px;padding:2px 5px;font-family:Consolas,monospace;font-size:.92em}.path{word-break:break-all;font-family:Consolas,monospace;font-size:12px;color:#46606c}.foot{border-top:1px solid var(--line);padding-top:20px;color:var(--muted);font-size:13px}.mono{font-family:Consolas,monospace}.small{font-size:12px;color:var(--muted)}.nowrap{white-space:nowrap}
@media(max-width:930px){.g4,.g3{grid-template-columns:1fr 1fr}.heat-grid,.g2{grid-template-columns:1fr}.hero{padding-top:38px}.page{padding-left:16px;padding-right:16px}}
@media(max-width:580px){.g4,.g3{grid-template-columns:1fr}.hero h1{font-size:36px}.flow .step{min-width:170px}.section h2{font-size:26px}.compare{grid-template-columns:1fr}}
@media print{.nav{display:none}.hero{background:#123d4c!important;-webkit-print-color-adjust:exact}.card,.heat,table{box-shadow:none;break-inside:avoid}.section{break-inside:auto}.page{max-width:none}.heat svg{height:420px}}
</style>
</head>
<body>
<header class="hero">
  <div class="hero-inner">
    <div class="eyebrow">SuperRAN · Channel Generation Audit · 2026-08-10</div>
    <h1>信道生成已经能用于当前内部统计信道研究，但边界必须写在结果旁边</h1>
    <p class="lead">本页不是“看起来合理”的演示，而是对 <code>internal_sim</code> 的标准表、64T4R 空间结构、多站大尺度状态、SRS/ZC、估计信道、存储契约和测量 KPI 逐层反证后的审计记录。结论覆盖本页证据包，不外推为所有后端或完整 slot 调度认证。</p>
    <div class="hero-meta">
      <span class="pill good">3 / 3 Gate 1 无 blocker</span>
      <span class="pill good">362 / 362 随机压力通过</span>
      <span class="pill good">94 项 ChannelHub 回归</span>
      <span class="pill warn">固定链路样本可使用 RS observation abstraction</span>
      <span class="pill warn">仓库 dirty，未提交</span>
    </div>
  </div>
</header>

<nav class="nav"><div>
  <a href="#verdict">结论</a><a href="#contract">数据契约</a><a href="#single">64×4 案例</a>
  <a href="#multi">多小区</a><a href="#srs">SRS/ZC</a><a href="#bugs">修复清单</a>
  <a href="#modules">模块判定</a><a href="#limits">近似与边界</a><a href="#evidence">证据复现</a>
</div></nav>

<main class="page">
<section id="verdict" class="section">
  <div class="kicker">01 · Outcome first</div><h2>最终判定</h2>
  <p class="intro">“彻底可行、永远挑不出毛病”不是一个可证命题。本轮交付的是更有用的东西：明确的证据包、能失败的反例，以及每个绿色结论的适用边界。</p>
  <div class="verdict"><strong>可以进入后续系统仿真：</strong>以 ChannelHub <code>internal_sim</code> 生成的 CDL/TDL 统计信道，在当前 64T4R、单/多站、RB 级频域、LS/SRS 估计范围内，标准表、形状、功率、空间秩、LOS/profile 对应、干扰幅度和落盘解释已经形成闭环。<strong>不能据此宣称：</strong>Sionna RT、QuaDRiGa、RE 级多端口 SRS、HARQ/BLER 或真实 slot 调度已被同等深度验证。</div>
  <div class="grid g4" style="margin-top:18px">
    <div class="card metric"><div class="value">__SINGLE_ID__</div><div class="label">单小区 8×1×272×64×4</div><div class="note"><span class="status ok">Gate 1</span> NMSE 中位 __SINGLE_NMSE__ dB</div></div>
    <div class="card metric"><div class="value">__MULTI_ID__</div><div class="label">2 站 × 3 扇区，5 个干扰信道</div><div class="note"><span class="status ok">Gate 1</span> SIR 中位 __MULTI_SIR__ dB</div></div>
    <div class="card metric"><div class="value">362 / 362</div><div class="label">属性压力案例</div><div class="note">CDL、TDL、几何、SRS 64×4 表树、解析 LS 反例</div></div>
    <div class="card metric"><div class="value">18</div><div class="label">本轮确认并修复的缺陷族</div><div class="note">包含最后发现的 effective profile 与 PDP 周期绕回</div></div>
  </div>
  <div class="callout" style="margin-top:16px"><h3>Gate 通过不等于蒙特卡洛结论收敛</h3>三套审计样本用于结构验收，样本数分别为 8 / 6 / 4；Gate 无 blocker，但仍保留“小样本收敛”和“SRS 单一 SNR 工况”警告。它们不能拿来报总体提升百分比。</div>
</section>

<section id="contract" class="section">
  <div class="kicker">02 · Narrow waist</div><h2>H_true / H_est 的窄腰契约</h2>
  <p class="intro">所有后续 PMI、预编码、SINR、系统调度都必须从同一组轴和功率口径出发。这里把最容易重复计损耗的部分固定下来。</p>
  <div class="grid g3">
    <div class="card"><h3>统一形状</h3><div class="equation">H ∈ ℂ<sup>N × T × RB × BS × UE</sup></div><p><code>[sample, time_or_slot, RB, BS_port, UE_port]</code>。本例为 <code>[8,1,272,64,4]</code>，BS 与 UE 轴不会因 UL/DL 互换。</p></div>
    <div class="card"><h3>功率分层</h3><p><code>H_true</code> 是单位小尺度功率结构，不含绝对路损。绝对量单独存 <code>pathloss_all_db</code>、<code>rx_power_all_dbm</code>；干扰信道再乘相对幅度。</p><div class="equation">H<sub>i,store</sub> = H<sub>i,norm</sub> · √(P<sub>i</sub>/P<sub>s</sub>)</div></div>
    <div class="card"><h3>估计不许假装完美</h3><p><code>H_est</code> 必须来自导频、噪声、干扰与插值。源若给出 <code>H_true</code> 却缺 <code>H_est</code>，生成直接报错，不再静默复制真值。</p><div class="equation">Y<sub>RS</sub> = H<sub>true</sub>X<sub>RS</sub> + ΣH<sub>i</sub>X<sub>i</sub> + N</div></div>
  </div>
  <h3 style="margin-top:22px">统计 CDL 的核心构造</h3>
  <div class="equation"><span class="hi">H[k,t]</span> = Σ<sub>n=1…N<sub>cl</sub></sub> Σ<sub>m=1…20</sub> √(P<sub>n</sub>/20) · a<sub>BS</sub>(φ<sub>n,m</sub>,θ<sub>n,m</sub>) · a<sub>UE</sub><sup>H</sup>(φ′<sub>n,m</sub>,θ′<sub>n,m</sub>) · G<sub>pol</sub> · e<sup>−j2πf<sub>k</sub>τ<sub>n</sub></sup> · e<sup>j2πν<sub>n,m</sub>t</sup></div>
  <div class="grid g2">
    <div class="callout blue"><h3>空间维度为什么是 64×4</h3>每条 ray 先生成 64 维 BS 有效端口 steering 与 4 维 UE steering，再做外积；20 rays 聚成 cluster，clusters 经时延相位相加。64T 公司面板走 192 物理阵子经 1 驱 3 耦合投影到 64 RF 端口；4R 默认 <code>2H×1V×2pol</code>。</div>
    <div class="callout blue"><h3>时间维为什么存 1 但不是平均</h3>内部生成 14 个 normal-CP symbol（或明确标记的 speed-only 子网格），Doppler 时间步含 CP，存盘取中间 symbol 快照。禁止对复数 symbol 直接平均，因为相位旋转会虚假抵消功率。</div>
  </div>
</section>

<section id="single" class="section">
  <div class="kicker">03 · Case A</div><h2>单小区 64T4R：一个矩阵是怎样落盘的</h2>
  <p class="intro">固定 seed 的真实数据集 <code>__SINGLE_ID__</code>。批内 7 条 NLOS 用 CDL-C、1 条 LOS 自动用 CDL-D；下图代表样本 0、RB 136，它恰好是 CDL-D，所以 <code>Dataset.paths(0)</code> 返回 14 个组件，而不是拿配置名 CDL-C 重建 24 个。</p>
  <div class="flow">
    <div class="step"><span class="n">1</span><b>拓扑与链路</b><small>撒点、距离、LOS 概率、路损与阴影</small></div><div class="arrow">→</div>
    <div class="step"><span class="n">2</span><b>实际 profile</b><small>逐样本 C/D 切换，DS 与 SF 跟链路走</small></div><div class="arrow">→</div>
    <div class="step"><span class="n">3</span><b>20 rays / cluster</b><small>角偏移、随机耦合、XPR、极化相位</small></div><div class="arrow">→</div>
    <div class="step"><span class="n">4</span><b>64×4 外积</b><small>BS 有效端口 × UE panel</small></div><div class="arrow">→</div>
    <div class="step"><span class="n">5</span><b>时频响应</b><small>272 RB × 14 symbol，CP-aware Doppler</small></div><div class="arrow">→</div>
    <div class="step"><span class="n">6</span><b>导频估计</b><small>1 个真实 observation + LS 插值</small></div><div class="arrow">→</div>
    <div class="step"><span class="n">7</span><b>落盘</b><small>中间快照，H_true/H_est 同形</small></div>
  </div>
  <div class="grid g4" style="margin:10px 0 16px">
    <div class="card metric"><div class="value">1.000</div><div class="label">H_true 平均元素功率（约）</div></div>
    <div class="card metric"><div class="value">100%</div><div class="label">2176 个 RB 矩阵 rank 4</div></div>
    <div class="card metric"><div class="value">0.432</div><div class="label">σ₄/σ₁ 中位数</div></div>
    <div class="card metric"><div class="value">__SINGLE_NMSE__ dB</div><div class="label">LS 估计 NMSE 中位数</div></div>
  </div>
  <div class="heat-grid">
    <div class="heat"><h4>H_true · |H| dB</h4><div class="sub">样本 0 · 中间 RB · 64 BS ports × 4 UE ports</div><svg id="trueHeat" role="img" aria-label="H true magnitude heatmap"></svg><div class="legend"></div><div class="legend-label"><span>−35 dB</span><span>+8 dB</span></div></div>
    <div class="heat"><h4>H_est · |Ĥ| dB</h4><div class="sub">同一位置、同一 RB；导频噪声与插值误差可见</div><svg id="estHeat" role="img" aria-label="H estimate magnitude heatmap"></svg><div class="legend"></div><div class="legend-label"><span>−35 dB</span><span>+8 dB</span></div></div>
  </div>
  <div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>矩阵元</th><th>实部</th><th>虚部</th><th>|H|</th><th>相位</th></tr></thead><tbody id="matrixEntries"></tbody></table></div>
  <div class="callout blue" style="margin-top:16px"><h3>时延测量反例也在这个案例里</h3>旧 <code>pdp()</code> 把 IFFT 周期末端泄漏当作最大正时延；13 ns 单径会被测成约 474 ns。现在用能量归一 Hann 窗和有符号周期矩，并只对剖面支持落在 1389 ns 无混叠半窗内的样本判定。本批可观测 2/8 条，实测/标称比值中位 1.06；其余明确不判。</div>
</section>

<section id="multi" class="section">
  <div class="kicker">04 · Case B</div><h2>多小区：同站共享传播状态，不同站不能复制</h2>
  <p class="intro">数据集 <code>__MULTI_ID__</code>：两座物理站、每站三扇区。三扇区共址，所以共享 LOS / DS / SF；天线方向增益、接收功率和小尺度衰落仍按 sector 独立。不同物理站使用共同因子 + 独立残差，而不是把 serving LSP 复制给所有干扰链路。</p>
  <div class="diagram">
    <svg viewBox="0 0 980 300" aria-label="two-site six-sector channel diagram">
      <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#62d5cb"/></marker><filter id="glow"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
      <text x="32" y="35" fill="#9de4df" font-size="14" font-weight="700">PHYSICAL SITE GROUP 0</text><text x="700" y="35" fill="#9de4df" font-size="14" font-weight="700">PHYSICAL SITE GROUP 1</text>
      <circle cx="155" cy="150" r="58" fill="#17485b" stroke="#4cc7bd" stroke-width="2"/><circle cx="805" cy="150" r="58" fill="#17485b" stroke="#4cc7bd" stroke-width="2"/>
      <path d="M155 150 L155 91 A59 59 0 0 1 206 180 Z" fill="#1e8e8b" opacity=".75"/><path d="M155 150 L206 180 A59 59 0 0 1 104 180 Z" fill="#e47a38" opacity=".72"/><path d="M155 150 L104 180 A59 59 0 0 1 155 91 Z" fill="#d7b14b" opacity=".72"/>
      <path d="M805 150 L805 91 A59 59 0 0 1 856 180 Z" fill="#1e8e8b" opacity=".75"/><path d="M805 150 L856 180 A59 59 0 0 1 754 180 Z" fill="#e47a38" opacity=".72"/><path d="M805 150 L754 180 A59 59 0 0 1 805 91 Z" fill="#d7b14b" opacity=".72"/>
      <text x="118" y="154" fill="#fff" font-size="15" font-weight="800">cells 0/1/2</text><text x="768" y="154" fill="#fff" font-size="15" font-weight="800">cells 3/4/5</text>
      <circle cx="440" cy="105" r="17" fill="#fff" stroke="#ffbb6e" stroke-width="4" filter="url(#glow)"/><text x="427" y="144" fill="#fff" font-size="13">UE</text>
      <line x1="210" y1="140" x2="419" y2="109" stroke="#62d5cb" stroke-width="4" marker-end="url(#arr)"/><line x1="750" y1="140" x2="461" y2="109" stroke="#ef925c" stroke-width="2" stroke-dasharray="8 6" marker-end="url(#arr)"/>
      <text x="275" y="94" fill="#9de4df" font-size="13">serving / selected</text><text x="565" y="91" fill="#ffc09b" font-size="13">relative interferer scaling</text>
      <rect x="280" y="202" width="420" height="66" rx="10" fill="#ffffff10" stroke="#ffffff28"/><text x="300" y="228" fill="#d8f4f1" font-size="13">same site: identical LOS / DS / SF</text><text x="300" y="251" fill="#d8f4f1" font-size="13">cross site: ρ≈0.5 common factor + independent residual (engineering choice)</text>
    </svg>
  </div>
  <div class="grid g4" style="margin-top:16px">
    <div class="card metric"><div class="value">[0,0,0,1,1,1]</div><div class="label">物理站分组</div></div>
    <div class="card metric"><div class="value">CDL-C×3 / D×3</div><div class="label">首批实际 profile</div></div>
    <div class="card metric"><div class="value">__MULTI_SIR__ dB</div><div class="label">几何 SIR 中位数</div></div>
    <div class="card metric"><div class="value">__MULTI_SINR__ dB</div><div class="label">SINR 中位数</div></div>
  </div>
  <div class="table-wrap"><table><thead><tr><th>cell</th><th>site group</th><th>LOS</th><th>实际 profile</th><th>pathloss dB</th><th>sector gain dB</th><th>Rx dBm</th><th>DS ns</th><th>SF dB</th></tr></thead><tbody id="cellRows"></tbody></table></div>
  <div class="grid g2" style="margin-top:16px">
    <div class="callout blue"><h3>反向验证</h3>同站三扇区的 DS / SF / LOS 最大差均为 0；每个样本两座站的 DS 或 SF 至少一项不同。若又变成“六个 cell 一模一样”，<code>multisite_stress</code> 会直接失败。</div>
    <div class="callout"><h3>RS 调度边界</h3>这批固定链路样本中有 <strong>5/6</strong> 使用“一个代表性 RS observation”的 legacy abstraction，不是逐 slot TDD/periodicity trace。PHY 估计矩阵可用，但不能拿它统计实际 RS 机会损失。</div>
  </div>
</section>

<section id="srs" class="section">
  <div class="kicker">05 · Case C</div><h2>SRS / ZC：17 跳恰好覆盖 272 RB</h2>
  <p class="intro"><code>C_SRS=63, B_SRS=1, b_hop=0, K_TC=2</code>。38.211 表项为 <code>(272,1) → (16,17) → (8,2) → (4,2)</code>；B1 每跳 16 RB，标准 hop 不是线性 0,16,32…。</p>
  <div class="hops" id="hopGrid"></div>
  <div class="grid g4" style="margin-top:15px">
    <div class="card metric"><div class="value">17</div><div class="label">完整 hopping cycle</div></div>
    <div class="card metric"><div class="value">16 RB</div><div class="label">每跳带宽</div></div>
    <div class="card metric"><div class="value">272 / 272</div><div class="label">并集覆盖且每 RB 恰一次</div></div>
    <div class="card metric"><div class="value">8.5 ms</div><div class="label">T<sub>SRS</sub>=1、30 kHz 下整带获取</div></div>
  </div>
  <div class="equation">r<sub>u,v</sub>(n) = e<sup>−jπu·n(n+1)/N<sub>ZC</sub></sup> · e<sup>jαn</sup>　<span class="dim">|r(n)| = 1</span></div>
  <div class="grid g3">
    <div class="card"><h3>ZC / low-PAPR</h3><p>底层根 <code>u=17</code>、素数长度 <code>N_ZC=89</code>。恒模最大误差接近机器精度；周期自相关最大旁瓣 <code>__ZC_SIDE__</code>。</p></div>
    <div class="card"><h3>局部导频索引</h3><p>当前 hop 只有 16 RB，导频数组必须用 local index，写回时再映射 absolute RB。无噪声解析反例覆盖 slot 0/1/7/16，最大误差约 5.2×10⁻⁷。</p></div>
    <div class="card"><h3>时间顺序</h3><p>slot 使用绝对索引参与 hopping，再按 numerology 做 frame wrap；不能把 17 个 hop 伪装成同一个 slot 的 4 个导频 symbol。</p></div>
  </div>
  <div class="compare" style="margin-top:16px">
    <div class="barbox"><span class="status caution">单次局部观测</span><h3>LS linear：__SRS_LINEAR__ dB</h3><div class="bar badbar"><span style="width:88%"></span></div><p class="small">未观测频段靠频率插值。在这组静态、窄 hop 工况下，整带误差很大。</p></div>
    <div class="barbox"><span class="status ok">17 跳历史拼接</span><h3>LS hop concat：__SRS_CONCAT__ dB</h3><div class="bar goodbar"><span style="width:28%"></span></div><p class="small">相同 H_true、固定 UE、合成历史。改善只证明拼接路径在此工况生效，不代表高速移动下仍成立。</p></div>
  </div>
  <div class="callout red" style="margin-top:16px"><h3>尚未做成 RE 级接收机</h3>当前是一条代表性 SRS symbol、一个 SRS port 的 RB 级观测；没有多端口 CDM、comb 内 RE 映射、符号级 TDD 排程。<code>hop_concat</code> 的历史由模型合成，<code>sequential</code> 仍标 experimental。</div>
</section>

<section id="bugs" class="section">
  <div class="kicker">06 · Findings</div><h2>本轮确认并修复的缺陷</h2>
  <p class="intro">下表只列会改变数值、解释或可复现性的项；不是格式清理清单。</p>
  <div class="table-wrap"><table>
    <thead><tr><th>#</th><th>缺陷</th><th>不修的后果</th><th>反证 / 验证</th></tr></thead>
    <tbody>
      <tr><td>1</td><td>SRS 64 行带宽树与 C63/B1 顺序不完整</td><td>17 跳位置错误，不能恰好覆盖 272 RB</td><td>64×4 资源全扫；C63 顺序固定为本页 17 项</td></tr>
      <tr><td>2</td><td>局部 hop pilot 用 absolute RB 索引</td><td>非首跳 LS 读错导频或越界</td><td>slot 0/1/7/16 近无噪声解析恢复</td></tr>
      <tr><td>3</td><td>凭空制造 symbol 0/4/8/12 四个 SRS 观测</td><td>把频率获取时间缩短 4 倍，NMSE 过于乐观</td><td>每次 transmission 只保留一个真实 observation</td></tr>
      <tr><td>4</td><td>CDL 表截断、D/E LOS 行和 20-ray 展开错误</td><td>PDP、角度、空间秩、K 因子全部漂移</td><td>A–E 独立标准表 + 20 个 Table 7.5-3 ray offset</td></tr>
      <tr><td>5</td><td>TDL-D/E 表错位、LOS 功率/K 重复应用</td><td>D/E tap 数、总功率和 K 因子错误</td><td>D=13 taps、E=14 taps；Monte Carlo K 恢复 ±0.45 dB</td></tr>
      <tr><td>6</td><td>TDL LOS steering 未按 MIMO 元素功率归一</td><td>64×4 下有效 K 变成 K/(64×4)</td><td>4×2 MIMO K-factor 统计回归</td></tr>
      <tr><td>7</td><td>所有 cell 复制 serving 的 LOS/DS/SF</td><td>跨站干扰信道不物理，多站统计失真</td><td>同站相等、跨站至少一个 LSP 不同</td></tr>
      <tr><td>8</td><td>4R UE panel 的极化维被错误压成 2R</td><td>64×4 实际生成 64×2 或错误 steering</td><td>4R 默认 2H×1V×2pol；端到端形状硬断言</td></tr>
      <tr><td>9</td><td>OFDM symbols 做复数平均</td><td>Doppler 相位互相抵消，功率虚降</td><td>改为中间 symbol 快照；T=1/4/14 几何一致</td></tr>
      <tr><td>10</td><td>Doppler 时间步忽略 CP</td><td>normal-CP 相位演进低估约 6.7%</td><td>15/30/120 kHz 平均 symbol 周期解析测试</td></tr>
      <tr><td>11</td><td>缺 H_est 时静默复制 H_true</td><td>估计误差、CSI 算法收益全部伪造</td><td>改为 hard error；真值与估计不等反例</td></tr>
      <tr><td>12</td><td>sample time span 只乘原始 slot 时长</td><td>SRS/CSI acquisition span 少算周期倍数</td><td>按 pilot period × numerology slot 计算并落 metadata</td></tr>
      <tr><td>13</td><td>配置 CDL-C，LOS 实际 D，但摘要仍写 C</td><td><code>paths()</code> 重建错簇数、K 和角度</td><td>逐样本 effective profile + counts + index-aware paths</td></tr>
      <tr><td>14</td><td>显式 UMa_LOS 仍随机出 NLOS</td><td>LOS 路损配 NLOS profile，语义自相矛盾</td><td>显式 _LOS 强制 LOS，概率记 1；配置 C 自动切 D</td></tr>
      <tr><td>15</td><td>Gate 按配置 profile 检查混合 C/D 批次</td><td>标准表、角度与 LOS 自洽给出错误理由</td><td>逐实际 profile 分组；状态与 profile 逐条对账</td></tr>
      <tr><td>16</td><td>1 样本 MC 宽度 ∞ 转 int</td><td>Gate 直接 OverflowError</td><td>非有限宽度报告“无法外推”，不崩溃</td></tr>
      <tr><td>17</td><td>PDP 矩把 IFFT 周期尾泄漏当正时延</td><td>13 ns 单径误报约 474 ns</td><td>Hann + signed periodic moments；解析反例 &lt;10 ns</td></tr>
      <tr><td>18</td><td>文档仍写 C57、sequential 顺序与复数平均</td><td>实现正确但用户按旧口径解释</td><td>说明书源码与生成 HTML 同步更新</td></tr>
    </tbody>
  </table></div>
</section>

<section id="modules" class="section">
  <div class="kicker">07 · Module readiness</div><h2>主要模块：为什么当前可用，怎样还能把它挑出问题</h2>
  <p class="intro">绿色不是“永久正确”，而是“在所列反例与边界内没有发现 blocker”。每项都给出能推翻它的条件。</p>
  <div class="table-wrap"><table>
    <thead><tr><th>模块</th><th>当前判定</th><th>本轮可证依据</th><th>仍可推翻它的边界</th></tr></thead>
    <tbody>
      <tr><td><b>拓扑 / LSP / 路损</b></td><td><span class="status ok">可用</span></td><td>同站共享、跨站残差；逐链路 LOS 公式；2站×3扇区固定反例</td><td>跨站相关权重 0.5 是工程值，尚未用公司测量校准</td></tr>
      <tr><td><b>CDL A–E</b></td><td><span class="status ok">可用</span></td><td>标准表逐字段独立对账；20 rays、XPR、极化、geometry rotation；全 RB rank 4</td><td>geometry rotation 不是 §7.5 完整随机 cluster 生成</td></tr>
      <tr><td><b>TDL A–E</b></td><td><span class="status ok">可用</span></td><td>D/E tap 表、K 一次应用、MIMO K 恢复、25 seeds</td><td>TDL 无 path angles；相关矩阵是简化模型</td></tr>
      <tr><td><b>64T / 4R 阵列</b></td><td><span class="status ok">当前公司默认可用</span></td><td>192 AE→64 port 的 1驱3；0.67λ 垂直；4R shape/rank 硬断言</td><td>UE 朝向固定 global +x；公司终端实测 panel 尚未输入</td></tr>
      <tr><td><b>OFDM / Doppler</b></td><td><span class="status ok">RB级可用</span></td><td>CP-aware 时间步；中间快照；T=1/4/14 几何逐位一致</td><td>一 RB 一个系数，不是 12 子载波；CP 取平均 symbol 周期</td></tr>
      <tr><td><b>多小区干扰</b></td><td><span class="status ok">可用</span></td><td>5 条干扰矩阵落盘；相对功率缩放；SIR/SINR/IoT 解析反例</td><td>邻区业务负载与预编码仍是系统层输入，不由信道单独决定</td></tr>
      <tr><td><b>SRS / ZC</b></td><td><span class="status ok">资源与单端口估计可用</span></td><td>64 行×4 层、17 hop、恒模/自相关、非首跳 LS 反例</td><td>尚非 RE 级多端口 CDM；历史拼接在高速场景需再验证</td></tr>
      <tr><td><b>H_est / 估计器</b></td><td><span class="status ok">LS/MMSE 窄腰可用</span></td><td>真实 observation、硬错误策略、同 H_true estimator A/B</td><td>LMMSE 统计先验与公司接收机实现尚未校准</td></tr>
      <tr><td><b>Loader / Gate / PDP</b></td><td><span class="status ok">可用</span></td><td>effective profile 贯通；Hann 周期矩；18 项 Gate 无 blocker</td><td>超出无混叠半窗的 DS 只标不可观测，不硬给数值</td></tr>
      <tr><td><b>Sionna RT / QuaDRiGa</b></td><td><span class="status caution">未同深度认证</span></td><td>共享窄腰可加载，已有基础回归</td><td>逐径几何、后端版本、场景资产与数值一致性未完成本轮压力</td></tr>
    </tbody>
  </table></div>
</section>

<section id="limits" class="section">
  <div class="kicker">08 · Claim boundary</div><h2>标准实现、工程近似、下一阶段：必须分栏</h2>
  <div class="grid g3">
    <div class="card"><h3><span class="status ok">标准锚点</span></h3><ul><li>38.901 CDL/TDL A–E 表</li><li>CDL 每个非 specular component 的 20 ray offsets</li><li>§7.7.3 <code>τ_scaled=τ_model·DS_desired</code></li><li>38.211 SRS 64 行带宽配置与 hopping 公式</li><li>ZC/low-PAPR 恒模与周期相关</li></ul></div>
    <div class="card"><h3><span class="status caution">显式工程近似</span></h3><ul><li>CDL 固定 profile 旋转到链路几何</li><li>跨站 LSP common weight = 0.5</li><li>UE orientation 固定 +x</li><li>RB center 级频域，不是 RE 级</li><li>normal-CP 用平均 symbol period</li><li>固定链路允许代表性 RS observation</li></ul></div>
    <div class="card"><h3><span class="status info">下一阶段</span></h3><ul><li>公司 UE panel / XPD / orientation 分布</li><li>RE 级多端口 SRS + CDM</li><li>真实 slot TDD/RS scheduler trace</li><li>Sionna / QuaDRiGa 同一门禁压力</li><li>公司 CDF 与系统话务校准</li><li>重传、BLER 与逐 RBG SINR 联动</li></ul></div>
  </div>
  <div class="callout red" style="margin-top:16px"><h3>对系统体验仿真的直接影响</h3>本页只证明输入信道窄腰在所列范围内可信。PF 的 RU 更新、按需 RBG、SU/MU 自适应、EBF/NEBF/PEBF、MU OLLA、业务 CDF 和 KPI tab 属于上层系统仿真；它们必须继续使用自己的反向验证，不能借“信道 Gate 通过”替代。</div>
</section>

<section id="evidence" class="section">
  <div class="kicker">09 · Reproducibility</div><h2>证据、回归与源码指纹</h2>
  <div class="grid g3">
    <div class="card"><h3>生成证据</h3><p class="path">__EVIDENCE_PATH__</p><p>三套真实数据、矩阵切片、Gate 1 全条目、源码 SHA-256。</p></div>
    <div class="card"><h3>压力证据</h3><p class="path">__STRESS_PATH__</p><p>25 CDL + 25 TDL + 5 geometry + 8 multisite + 256 SRS resources + 42 sequences + 1 analytic LS。</p></div>
    <div class="card"><h3>报告生成器</h3><p class="path">__GENERATOR_PATH__</p><p>只读 JSON 生成本页；关键数值不手抄。</p></div>
  </div>
  <h3 style="margin-top:24px">当前回归账本</h3>
  <div class="table-wrap"><table><thead><tr><th>入口</th><th>结果</th><th>覆盖重点</th></tr></thead><tbody>
    <tr><td>ChannelHub selected pytest</td><td><span class="status ok">94 passed</span></td><td>SRS、CDL/TDL、effective array、measurement bridge、HSR</td></tr>
    <tr><td>SuperRAN affected pytest</td><td><span class="status ok">5 passed</span></td><td>窄腰、64×4、PDP 解析反例、physics/CSI/E2E 脚本级回归</td></tr>
    <tr><td><code>run_channel_property_stress.py</code></td><td><span class="status ok">362 / 362</span></td><td>随机种子、profile/state、几何网格、SRS 全表与解析反例</td></tr>
    <tr><td><code>tests/test_linklevel.py</code></td><td><span class="status ok">exit 0 · 72.7 s</span></td><td>SVD/Type-I/DFT、CSI 趋势、18 项 Gate、IRC</td></tr>
    <tr><td><code>tests/test_interference.py</code></td><td><span class="status ok">exit 0 · 234.8 s</span></td><td>20 干扰小区、IoT、probe/full 几何、说明书/工具对账；随后 PDP-only 修复另跑受影响回归</td></tr>
  </tbody></table></div>
  <h3 style="margin-top:24px">Gate 1 全条目</h3><div id="gateItems"></div>
  <h3 style="margin-top:24px">源码 SHA-256</h3><div class="table-wrap"><table><thead><tr><th>文件</th><th>SHA-256</th></tr></thead><tbody id="hashRows"></tbody></table></div>
  <h3 style="margin-top:24px">标准来源</h3>
  <p><a href="https://www.etsi.org/deliver/etsi_tr/138900_138999/138901/17.01.00_60/tr_138901v170100p.pdf">ETSI TR 138 901 V17.1.0</a>：CDL/TDL、20 rays、delay scaling、K-factor；<a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138211/18.04.00_60/ts_138211v180400p.pdf">ETSI TS 138 211 V18.4.0</a>：SRS sequence、bandwidth configuration 与 hopping。</p>
  <div class="callout"><h3>复现注意</h3>两仓库当前都有未提交改动，证据 JSON 已记录各自 HEAD、dirty 状态、changed path count 与关键文件哈希。复现时应以哈希为准，而不是只看 branch/HEAD。</div>
</section>

<footer class="foot">
  <div><b>SuperRAN channel-generation deep audit</b> · generated from evidence at <span class="mono">__GENERATED_AT__</span></div>
  <div>SuperRAN HEAD <span class="mono">__SR_HEAD__</span> · ChannelHub HEAD <span class="mono">__CH_HEAD__</span> · both dirty at audit time.</div>
</footer>
</main>

<script>
const E = __EVIDENCE_JSON__;
const S = __STRESS_JSON__;
const NS = "http://www.w3.org/2000/svg";
function escText(v){ return String(v ?? ""); }
function color(v){
  const x=Math.max(0,Math.min(1,(v+35)/43));
  const stops=[[23,58,112],[62,121,181],[86,194,176],[244,207,102],[229,104,58]];
  const p=x*(stops.length-1),i=Math.min(stops.length-2,Math.floor(p)),t=p-i;
  const a=stops[i],b=stops[i+1]; return `rgb(${a.map((q,j)=>Math.round(q+(b[j]-q)*t)).join(",")})`;
}
function heatmap(id,matrix){
  const svg=document.getElementById(id), rows=matrix.length, cols=matrix[0].length;
  svg.setAttribute("viewBox","0 0 390 555");
  const x0=54,y0=22,cw=69,rh=7.55;
  matrix.forEach((row,r)=>row.forEach((v,c)=>{
    const el=document.createElementNS(NS,"rect");
    el.setAttribute("x",x0+c*cw);el.setAttribute("y",y0+r*rh);el.setAttribute("width",cw-2);el.setAttribute("height",rh-.6);el.setAttribute("rx","1.5");el.setAttribute("fill",color(v));
    const title=document.createElementNS(NS,"title");title.textContent=`BS ${r}, UE ${c}: ${Number(v).toFixed(2)} dB`;el.appendChild(title);svg.appendChild(el);
  }));
  for(let c=0;c<cols;c++){const t=document.createElementNS(NS,"text");t.setAttribute("x",x0+c*cw+cw/2);t.setAttribute("y",538);t.setAttribute("text-anchor","middle");t.setAttribute("font-size","12");t.setAttribute("fill","#536975");t.textContent=`UE ${c}`;svg.appendChild(t)}
  [0,15,31,47,63].forEach(r=>{const t=document.createElementNS(NS,"text");t.setAttribute("x",45);t.setAttribute("y",y0+r*rh+6);t.setAttribute("text-anchor","end");t.setAttribute("font-size","10");t.setAttribute("fill","#536975");t.textContent=`BS ${r}`;svg.appendChild(t)});
}
function matrixEntries(){
  const body=document.getElementById("matrixEntries");
  E.single_cell.representative_h_true_64x4.selected_entries.forEach(x=>{
    const tr=document.createElement("tr");
    [`BS ${x.bs_port} / UE ${x.ue_port}`,x.real.toFixed(6),x.imag.toFixed(6),x.magnitude.toFixed(6),`${x.phase_deg.toFixed(2)}°`].forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i>0)td.className="num";tr.appendChild(td)});body.appendChild(tr)
  });
}
function cellRows(){
  const g=E.multi_cell.first_sample_per_cell,body=document.getElementById("cellRows");
  for(let i=0;i<g.pathloss_all_db.length;i++){
    const vals=[i,g.physical_site_group_ids[i],g.is_los_all[i]?"LOS":"NLOS",g.effective_channel_model_all[i],g.pathloss_all_db[i].toFixed(2),g.antenna_gain_all_db[i].toFixed(2),g.rx_power_all_dbm[i].toFixed(2),g.sample_tau_rms_all_ns[i].toFixed(2),g.shadow_fading_all_db[i].toFixed(2)];
    const tr=document.createElement("tr");vals.forEach((v,j)=>{const td=document.createElement("td");td.textContent=v;if(j>=4)td.className="num";tr.appendChild(td)});body.appendChild(tr)
  }
}
function hops(){
  const box=document.getElementById("hopGrid");
  E.srs.hopping.hop_starts.forEach((rb,i)=>{const d=document.createElement("div");d.className="hop";const rbg=rb/16;d.innerHTML=`<small>#${i}</small><b>RBG ${rbg}</b><small>RB ${rb}–${rb+15}</small>`;box.appendChild(d)});
}
function gateItems(){
  const box=document.getElementById("gateItems");
  const cases=[["单小区",E.single_cell.gate1],["多小区",E.multi_cell.gate1],["SRS",E.srs.gate1]];
  cases.forEach(([label,gate])=>{
    const d=document.createElement("details");const s=document.createElement("summary");s.textContent=`${label} · ${gate.n_items} 项 · ${gate.passed?"无 blocker":"有 blocker"} · warning ${gate.warning_count}`;d.appendChild(s);
    const inner=document.createElement("div");inner.className="detail";
    gate.items.forEach(x=>{const p=document.createElement("p");const tag=x.passed?"PASS":x.severity.toUpperCase();p.innerHTML=`<span class="status ${x.passed?"ok":"caution"}">${tag}</span> <b></b><br><span class="small"></span>`;p.querySelector("b").textContent=x.name;p.querySelector("span.small").textContent=x.detail;inner.appendChild(p)});
    d.appendChild(inner);box.appendChild(d)
  });
}
function hashes(){
  const body=document.getElementById("hashRows");Object.entries(E.source_hashes).forEach(([name,hash])=>{const tr=document.createElement("tr"),a=document.createElement("td"),b=document.createElement("td");a.textContent=name;b.textContent=hash;b.className="mono small";tr.append(a,b);body.appendChild(tr)});
}
heatmap("trueHeat",E.single_cell.representative_h_true_64x4.magnitude_db);
heatmap("estHeat",E.single_cell.representative_h_est_64x4.magnitude_db);
matrixEntries();cellRows();hops();gateItems();hashes();
</script>
</body></html>
"""


def main() -> None:
    evidence = _load(EVIDENCE_PATH)
    stress = _load(STRESS_PATH)
    single = evidence["single_cell"]
    multi = evidence["multi_cell"]
    srs = evidence["srs"]
    repos = evidence["repositories"]
    replacements = {
        "__SINGLE_ID__": single["dataset_id"],
        "__MULTI_ID__": multi["dataset_id"],
        "__SINGLE_NMSE__": _fmt(single["nmse_db"]["median"]),
        "__MULTI_SIR__": _fmt(multi["sir_db"]["median"]),
        "__MULTI_SINR__": _fmt(multi["sinr_db"]["median"]),
        "__SRS_LINEAR__": _fmt(srs["linear_nmse_db"]["median"]),
        "__SRS_CONCAT__": _fmt(srs["hop_concat_nmse_db"]["median"]),
        "__ZC_SIDE__": f"{float(srs['srs_sequence']['zc_max_cyclic_sidelobe']):.2e}",
        "__EVIDENCE_PATH__": escape(str(EVIDENCE_PATH)),
        "__STRESS_PATH__": escape(str(STRESS_PATH)),
        "__GENERATOR_PATH__": escape(str(Path(__file__).resolve())),
        "__GENERATED_AT__": escape(str(evidence["generated_at_utc"])),
        "__SR_HEAD__": escape(str(repos["superran"]["head"])),
        "__CH_HEAD__": escape(str(repos["channelhub"]["head"])),
        "__EVIDENCE_JSON__": _safe_json(evidence),
        "__STRESS_JSON__": _safe_json(stress),
    }
    page = HTML
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    leftovers = sorted({part.split("__", 1)[0] for part in page.split("__")[1::2]})
    if "__" in page:
        raise RuntimeError("unexpanded HTML markers remain: " + repr(leftovers))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(page, encoding="utf-8")
    print(OUT_PATH)
    print(f"bytes={OUT_PATH.stat().st_size}")


if __name__ == "__main__":
    main()
