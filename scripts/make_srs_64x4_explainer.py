"""Generate the self-contained KaTeX report for the 64x4 SRS/H matrix.

The report distinguishes three things that are easy to conflate:

1. the 192 physical antenna elements and 64 RF ports at the BS;
2. the physical MIMO channel H;
3. the SRS pilot matrix X used to estimate H.

Keep LaTeX strings out of f-string expressions.  This repository still needs
to run on Python versions older than 3.12, where backslashes inside f-string
expressions are a syntax error.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import katex, mathml  # noqa: E402

OUT = ROOT / "artifacts" / "SRS_64X4_MATRIX_EXPLAINED.html"


def formula(tex: str, *, display: bool = True) -> str:
    """KaTeX first, native MathML as the no-JS fallback."""
    fallback = mathml.render(tex, block=display)
    return katex.wrap(tex, fallback, display=display)


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>64×4 SRS 信道矩阵是怎么来的</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230b776f'/%3E%3Cpath d='M6 23V9m5 14V6m5 17V12m5 11V8m5 15V4' stroke='white' stroke-width='2.3' stroke-linecap='round'/%3E%3C/svg%3E">
__KATEX_HEAD__
<style>
:root{
  --ink:#18242d;--muted:#60707b;--paper:#f3f6f6;--card:#fff;--line:#d9e3e3;
  --navy:#102f3d;--teal:#08776f;--teal2:#13a095;--orange:#d97836;--red:#b34238;
  --amber:#93640b;--green:#23734a;--soft:#e8f4f2;--blue:#2f6986;--code:#0d2835;
}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif;line-height:1.68}
a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}code,.mono{font-family:Consolas,"Cascadia Mono",monospace}
.hero{background:linear-gradient(128deg,#082631 0%,#114958 62%,#08776f 100%);color:#fff;padding:56px 26px 46px;overflow:hidden;position:relative}
.hero:after{content:"";position:absolute;width:520px;height:520px;border-radius:50%;border:1px solid #ffffff24;right:-150px;top:-250px;box-shadow:0 0 0 65px #ffffff09,0 0 0 130px #ffffff06}
.hero-inner,.page{max-width:1220px;margin:auto;position:relative;z-index:1}.eyebrow{letter-spacing:.17em;text-transform:uppercase;color:#a4e6df;font-size:13px;font-weight:800}
.hero h1{font-size:clamp(35px,5.6vw,64px);line-height:1.04;margin:10px 0 18px;max-width:980px}.hero .lead{max-width:960px;color:#e5f2f2;font-size:19px;margin:0}
.hero-meta{display:flex;gap:9px;flex-wrap:wrap;margin-top:24px}.pill{border:1px solid #ffffff3d;background:#ffffff11;border-radius:999px;padding:7px 12px;font-size:13px}.pill.warn{background:#e5a43b35}.pill.ok{background:#47bd8a38}
.nav{position:sticky;top:0;z-index:30;background:#ffffffef;backdrop-filter:blur(10px);border-bottom:1px solid var(--line);overflow:auto;white-space:nowrap}.nav>div{max-width:1220px;margin:auto;padding:9px 19px}.nav a{display:inline-block;padding:7px 9px;color:#304650;font-size:13px;font-weight:700}
.page{padding:25px 23px 72px}.section{scroll-margin-top:72px;margin:30px 0 56px}.kicker{color:var(--teal);font-weight:850;font-size:13px;letter-spacing:.13em;text-transform:uppercase;margin-bottom:5px}.section h2{font-size:31px;line-height:1.22;color:var(--navy);margin:0 0 8px}.intro{color:var(--muted);max-width:1000px;margin:0 0 21px}
.grid{display:grid;gap:16px}.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.g3{grid-template-columns:repeat(3,minmax(0,1fr))}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:19px;box-shadow:0 8px 28px #1b3b4309}.card h3{font-size:18px;line-height:1.35;color:var(--navy);margin:0 0 8px}.card p:last-child{margin-bottom:0}.metric .value{font-size:31px;font-weight:850;color:var(--navy);line-height:1.1}.metric .label{color:var(--muted);font-size:13px;margin-top:5px}.metric .note{font-size:12px;color:var(--muted);margin-top:8px}
.verdict{border-left:5px solid var(--teal);border-radius:0 14px 14px 0;background:linear-gradient(90deg,#e3f3f0,#fff);padding:21px 23px;font-size:17px}.verdict strong{color:var(--navy)}
.callout{border:1px solid #ead49d;background:#fff7e6;color:#61480f;border-radius:13px;padding:16px 18px}.callout.red{background:#fff0ee;border-color:#efc1bb;color:#73342e}.callout.blue{background:#edf7fb;border-color:#c7e1ed;color:#28576b}.callout.green{background:#eaf6ef;border-color:#bfdfcb;color:#24583a}.callout h3{margin:0 0 6px;font-size:16px;color:inherit}
.status{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border-radius:999px;font-size:12px;font-weight:850}.status:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}.ok{color:var(--green);background:#e3f3ea}.warn{color:var(--amber);background:#fff0cf}.bad{color:var(--red);background:#fde6e3}.info{color:var(--blue);background:#e4f1f7}
.eq{background:var(--code);color:#edf9f8;border-radius:13px;margin:13px 0;padding:16px 18px;overflow:auto}.eq .kx{display:block;min-width:max-content}.eq .katex{font-size:1.08em}.eq .katex-display{margin:.25em 0}.eq-note{color:#b7cbd0;font-size:12px;margin-top:7px}
.flow{display:flex;align-items:stretch;gap:8px;overflow:auto;padding:7px 0 11px}.flow-step{min-width:160px;flex:1;background:#fff;border:1px solid var(--line);border-radius:13px;padding:13px}.flow-step b{display:block;color:var(--navy);margin:6px 0 3px}.flow-step small{color:var(--muted)}.num{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;background:var(--teal);color:#fff;font-size:12px;font-weight:850}.arrow{display:grid;place-items:center;color:var(--teal);font-size:25px}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border:1px solid var(--line);border-radius:13px;overflow:hidden;font-size:14px}th,td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}th{background:#eaf2f2;color:#294954;font-size:12px;letter-spacing:.025em}tr:last-child td{border-bottom:0}.table-wrap{overflow:auto;border-radius:13px}.table-wrap table{min-width:780px}
.axis{display:grid;grid-template-columns:1.05fr 1.45fr;gap:17px}.matrix-box{background:#0d2835;color:#effafa;border-radius:15px;padding:20px;display:grid;place-items:center;min-height:305px;overflow:auto}.matrix{display:grid;grid-template-columns:auto repeat(4,46px);grid-template-rows:auto repeat(8,31px);align-items:center;text-align:center;font-family:Consolas,monospace;font-size:12px}.matrix>span{border:1px solid #ffffff15;width:46px;height:31px;display:grid;place-items:center}.matrix .head{color:#75dfd4;border:0}.matrix .row{color:#ffca86;border:0;width:70px}.matrix .fade{color:#76909a}.matrix .brace{grid-column:2/6;border:0;width:auto;color:#b9d2d7;margin-top:4px}
.dimtag{font-family:Consolas,monospace;background:#e9f4f3;color:#12685f;border-radius:6px;padding:2px 6px;white-space:nowrap}.path{font-family:Consolas,"Cascadia Mono",monospace;font-size:12px;word-break:break-all;background:#f0f4f4;padding:8px 10px;border-radius:8px;color:#38515c}
.split{display:grid;grid-template-columns:1fr 1fr;gap:15px}.compare{border-top:4px solid var(--teal)}.compare.redtop{border-color:var(--red)}.compare h3{margin-top:2px}.checklist{padding-left:20px;margin:8px 0}.checklist li{margin:7px 0}.small{font-size:12px;color:var(--muted)}.foot{border-top:1px solid var(--line);margin-top:48px;padding:22px 0;color:var(--muted);font-size:12px}
.truth{background:#102f3d;color:#fff;border-radius:15px;padding:20px}.truth h3{color:#fff}.truth code{color:#8ee4da}.truth .muted{color:#b9cdd3}
ul.tight{margin:7px 0;padding-left:19px}ul.tight li{margin:4px 0}
@media(max-width:900px){.g4,.g3,.g2,.axis,.split{grid-template-columns:1fr}.hero{padding-top:42px}.page{padding-left:15px;padding-right:15px}.section h2{font-size:26px}.flow .arrow{transform:rotate(90deg);min-width:28px}.flow{flex-direction:column}.matrix-box{min-height:270px}}
@media print{.nav{display:none}.hero{padding:30px 20px}.section{break-inside:avoid}.card{box-shadow:none}.page{max-width:none}}
</style>
</head>
<body>
<header class="hero">
  <div class="hero-inner">
    <div class="eyebrow">SuperRAN · channel model walkthrough</div>
    <h1>64×4 “SRS 矩阵”<br>到底是怎么来的</h1>
    <p class="lead">从 192 个物理阵子、64 个 RF 端口、双极化 CDL 多径，一直推到 TDD 上行信道与四端口 SRS 估计；并以物理接收向量、端口解扩和 LMMSE 的当前实现为准。</p>
    <div class="hero-meta"><span class="pill ok">KaTeX 全公式</span><span class="pill">当前源码逐层对账</span><span class="pill ok">四端口物理 SRS 已落地</span><span class="pill">100 MHz · 30 kHz · 272 RB</span></div>
  </div>
</header>

<nav class="nav"><div>
  <a href="#answer">先给结论</a><a href="#axes">尺寸与轴</a><a href="#array">192→64 阵列</a><a href="#polar">极化</a><a href="#cdl">CDL 信道</a><a href="#timefreq">时频与归一</a><a href="#reciprocity">TDD 互易</a><a href="#srs">SRS 估计</a><a href="#gap">当前缺口</a><a href="#target">建议实现</a><a href="#anchors">源码锚点</a>
</div></nav>

<main class="page">
<section id="answer" class="section">
  <div class="kicker">00 · Direct answer</div><h2>先把名字和尺寸说准确</h2>
  <div class="verdict"><strong>SRS 本身不是 64×4。</strong>SRS 是 UE 发出的已知多端口导频矩阵 <span class="mono">X</span>；<strong>64×4 是 gNB 通过 SRS 想估计出的上行 MIMO 信道矩阵</strong> <span class="mono">H</span>。64 行对应 gNB 的 64 个接收 RF 端口，4 列对应 UE 的 4 个上行 SRS 发射端口。</div>
  <div class="eq">__EQ_H_COLUMNS__</div>
  <div class="grid g4" style="margin-top:17px">
    <div class="card metric"><div class="value">192</div><div class="label">BS 物理天线阵子 AE</div><div class="note">8H × 12V × 2pol</div></div>
    <div class="card metric"><div class="value">64</div><div class="label">BS RF / 数字端口</div><div class="note">8H × 4V × 2pol；每端口 1 驱 3</div></div>
    <div class="card metric"><div class="value">4</div><div class="label">UE 下行接收端口</div><div class="note">当前工程假设 2H × 1V × 2pol</div></div>
    <div class="card metric"><div class="value">4</div><div class="label">UE 默认上行 SRS 端口</div><div class="note">公司 BOTH preset 与 4R 对齐</div></div>
  </div>
  <div class="callout green" style="margin-top:16px"><h3>当前公司默认</h3><code>company_64t4r</code> 使用 <code>link=BOTH</code>、UE Tx/Rx 都为 4：<code>H_DL</code> 是 64×4，配对的真实 <code>H_UL</code> 和 SRS 估计也都是 64×4；两者物理角色仍须分开。</div>
  <div class="table-wrap" style="margin-top:16px"><table>
    <thead><tr><th>对象</th><th>配置条件</th><th>单 RB 矩阵</th><th>语义</th></tr></thead>
    <tbody>
      <tr><td>默认下行真值 / CSI-RS</td><td><code>BS_tx=64, UE_rx=4</code></td><td><span class="dimtag">64×4</span></td><td>从 64 个 BS 发射端口到 4 个 UE 接收端口</td></tr>
      <tr><td>默认上行真值 / SRS</td><td><code>BS_rx=64, UE_tx=4</code></td><td><span class="dimtag">64×4</span></td><td>从 4 个 UE 发射端口到 64 个 BS 接收端口；对外存储仍为 BS×UE</td></tr>
      <tr><td>公司配对数据</td><td><code>link=BOTH</code></td><td><span class="dimtag">[1,1,272,64,4]</span></td><td>DL 真值用于评估，UL SRS 估计用于 gNB 预编码</td></tr>
      <tr><td>物理恢复哨兵</td><td>4 port、64 BS、无噪声</td><td><span class="dimtag">[14,32,4,64]</span></td><td>最大恢复误差 2.46e−7，总 pilot 功率固定</td></tr>
    </tbody>
  </table></div>
</section>

<section id="axes" class="section">
  <div class="kicker">01 · Dimension ledger</div><h2>一眼看清：每个轴从哪里来</h2>
  <p class="intro">先固定统一存储约定，再谈物理公式。SuperRAN/ChannelHub 对外把天线轴统一存为 <code>[BS_port, UE_port]</code>，无论上下行；物理上行计算内部则先使用 <code>[UE_tx, BS_rx]</code>，最后转置回来。</p>
  <div class="axis">
    <div class="matrix-box">
      <div>
        <div style="text-align:center;font-weight:800;margin-bottom:12px">单个 RB 的存储矩阵 <span style="color:#75dfd4">H[k]</span></div>
        <div class="matrix" aria-label="64 by 4 matrix sketch">
          <span></span><span class="head">UE0</span><span class="head">UE1</span><span class="head">UE2</span><span class="head">UE3</span>
          <span class="row">BS0</span><span>h₀₀</span><span>h₀₁</span><span>h₀₂</span><span>h₀₃</span>
          <span class="row">BS1</span><span>h₁₀</span><span>h₁₁</span><span>h₁₂</span><span>h₁₃</span>
          <span class="row">BS2</span><span>h₂₀</span><span>h₂₁</span><span>h₂₂</span><span>h₂₃</span>
          <span class="row fade">⋮</span><span class="fade">⋮</span><span class="fade">⋮</span><span class="fade">⋮</span><span class="fade">⋮</span>
          <span class="row">BS31</span><span>h₃₁,₀</span><span>h₃₁,₁</span><span>h₃₁,₂</span><span>h₃₁,₃</span>
          <span class="row fade">⋮</span><span class="fade">⋮</span><span class="fade">⋮</span><span class="fade">⋮</span><span class="fade">⋮</span>
          <span class="row">BS62</span><span>h₆₂,₀</span><span>h₆₂,₁</span><span>h₆₂,₂</span><span>h₆₂,₃</span>
          <span class="row">BS63</span><span>h₆₃,₀</span><span>h₆₃,₁</span><span>h₆₃,₂</span><span>h₆₃,₃</span>
          <span class="brace">← 4 个 UE 端口；纵向共 64 个 BS 端口 →</span>
        </div>
      </div>
    </div>
    <div>
      <div class="card"><h3>完整样本张量</h3><div class="eq">__EQ_TENSOR__</div><ul class="tight"><li><b>sample：</b>不同 UE / 蒙特卡洛样本。</li><li><b>time_or_slot：</b>系统仿真消费的时隙快照，不是 14 个 symbol 全部落盘。</li><li><b>RB：</b>272 个频率点；一个 RB 一个复信道系数。</li><li><b>BS_port：</b>64 个 RF 端口，不是 192 个物理阵子。</li><li><b>UE_port：</b>DL 是 UE Rx 数；UL/SRS 是 UE Tx/SRS 端口数。</li></ul></div>
      <div class="callout blue" style="margin-top:14px"><h3>为什么最多只有 rank 4</h3>每个 RB 上矩阵是 64×4，所以空间秩上界由较小一侧决定。大量角度不同、极化不同的 ray 叠加，才可能把 4 个列方向撑开；一个理想单径外积通常只有很低的秩。<div class="eq">__EQ_RANK__</div></div>
    </div>
  </div>
</section>

<section id="array" class="section">
  <div class="kicker">02 · BS array</div><h2>192 个物理阵子，怎样压成 64 个 RF 端口</h2>
  <p class="intro">目标 AAU 的数字端口不是“一端口一阵子”。每个 RF 端口驱动同一水平位置、同一极化下的 3 个垂直相邻阵子；64 个端口因此覆盖 192 个物理阵子。</p>
  <div class="flow">
    <div class="flow-step"><span class="num">1</span><b>RF 端口格</b><small>8H × 4V × 2pol = 64</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">2</span><b>每端口 1 驱 3</b><small>同一 (h, vRF, pol) 对应 q=0,1,2</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">3</span><b>物理阵子格</b><small>8H × 12V × 2pol = 192</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">4</span><b>耦合矩阵 F</b><small>192×64，每列仅 3 个非零权</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">5</span><b>有效端口响应</b><small>Fᴴa_AE → 64 维</small></div>
  </div>
  <div class="grid g2">
    <div class="card"><h3>端口与阵子编号</h3><div class="eq">__EQ_INDEX__</div><p>其中 <code>h=0…7</code>，<code>v_RF=0…3</code>，<code>v_AE=0…11</code>，<code>p∈{0,1}</code>。一个端口 <code>(h,v_RF,p)</code> 只连接 <code>v_AE=3v_RF+q</code> 的三个阵子。</p></div>
    <div class="card"><h3>物理坐标</h3><div class="eq">__EQ_POS__</div><p>面板位于 y-z 平面，波束正前方是 +x。水平间距 0.5λ；物理垂直间距 0.67λ；相邻 RF 子阵相位中心约 3×0.67λ=2.01λ。</p></div>
    <div class="card"><h3>每个 1 驱 3 子阵的馈电</h3><div class="eq">__EQ_FEED__</div><p>默认 <code>A_q=1</code>、校准相位 <code>φ_q=0</code>、固定电下倾 6°。归一化保证每个 RF 端口馈电列的二范数等于 1。</p></div>
    <div class="card"><h3>192×64 耦合矩阵</h3><div class="eq">__EQ_F__</div><p>不同 RF 端口的三阵子集合互不重叠，因此在默认硬件映射下各列正交且单位范数；<code>F</code> 只负责模拟被动馈电网络，不把 192 个阵子暴露给系统层。</p></div>
  </div>
  <h3 style="margin-top:23px">方向到来后，64 维阵列向量怎样算</h3>
  <div class="grid g3">
    <div class="card"><h3>方向单位向量</h3><div class="eq">__EQ_DIRECTION__</div><p>方位角为 <span class="mono">az</span>，仰角从水平面计为 <span class="mono">el</span>。</p></div>
    <div class="card"><h3>192 阵子接收 steering</h3><div class="eq">__EQ_AE__</div><p>位置以参考载频波长归一；频率变化通过 <code>f/f_ref</code> 体现电长度变化。</p></div>
    <div class="card"><h3>压到 64 个 RF 端口</h3><div class="eq">__EQ_AEFF__</div><p>接收用 <code>Fᴴ</code>；发射侧按被动网络互易用 <code>Fᵀ</code>。CDL 函数最后的 BS 维始终是 64。</p></div>
  </div>
</section>

<section id="polar" class="section">
  <div class="kicker">03 · Polarisation</div><h2>双极化不是“把天线数乘 2”这么简单</h2>
  <p class="intro">当前实现有三层极化表达：端口索引中的两种极化、理想 ±45° Jones 基、以及每条 CDL ray 的 2×2 交叉极化耦合。三者的成熟度并不相同。</p>
  <div class="grid g3">
    <div class="card"><h3>① 端口极化索引</h3><p>BS 的每个 <code>(h,v)</code> 位置有 <code>p=0,1</code> 两个端口；UE 假设的 2H×1V×2pol 同样有两类。CDL 用布尔 mask 把 steering 向量拆成 pol-0 / pol-1 两组。</p><div class="eq">__EQ_POL_MASK__</div></div>
    <div class="card"><h3>② 理想 ±45° Jones 基</h3><div class="eq">__EQ_JONES__</div><p>这定义了理想斜极化方向。当前没有公司实测 Jones pattern；<code>parametric_temporary</code> 只是共同的标量幅度方向图。</p></div>
    <div class="card"><h3>③ 每条 ray 的交叉极化</h3><div class="eq">__EQ_GPOL__</div><p><span class="mono">κ=10^(XPR/10)</span>。同极化幅度为 1，交叉极化幅度为 <span class="mono">κ^-1/2</span>，四项各自有随机相位。</p></div>
  </div>
  <h3 style="margin-top:23px">阵元标量方向图</h3>
  <div class="eq">__EQ_PATTERN__</div>
  <div class="callout red"><h3>当前实现边界：Jones/XPD 还没有完全贯通</h3><code>element_jones()</code> 已定义理想 ±45° 基，但 CDL 空间 ray 当前并不直接调用它，而是用“极化端口 mask + 2×2 ray coupling matrix”。元素配置里的 <code>xpd_db=8</code> 主要作为元数据/回退值；CDL profile 自带 XPR 时优先使用 profile XPR。也就是说：<b>双极化结构和随机交叉极化已进入 H，但实测天线方向相关的 Jones/XPD 尚未进入 H。</b></div>
</section>

<section id="cdl" class="section">
  <div class="kicker">04 · CDL channel</div><h2>H 的核心：簇、20 条 ray、角度、时延、Doppler 与极化外积</h2>
  <p class="intro">默认使用 38.901 CDL-C；若同一场景按 LOS 概率抽到 LOS，当前代码会换成相应 LOS profile（审计样本中出现过 CDL-D）。每个非镜面簇展开为 20 条 ray。</p>
  <div class="flow">
    <div class="flow-step"><span class="num">1</span><b>CDL 表</b><small>Pₙ、τₙ、AOD/AOA/ZOD/ZOA</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">2</span><b>20-ray 展开</b><small>标准 offset + 各角度独立随机配对</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">3</span><b>阵列 steering</b><small>a_tx 与 a_rx</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">4</span><b>2×2 极化耦合</b><small>同极化 + 交叉极化</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">5</span><b>簇/ray 求和</b><small>形成 MIMO 空间矩阵</small></div><div class="arrow">→</div>
    <div class="flow-step"><span class="num">6</span><b>时延 DFT</b><small>得到每个 RB 的 H(t,k)</small></div>
  </div>
  <div class="card"><h3>第 n 簇、第 m 条 ray 的空间矩阵</h3><div class="eq">__EQ_SPATIAL__</div><p><code>a_t</code> 与 <code>a_r</code> 已经包含阵列几何；<code>M_t^(p_t)</code>、<code>M_r^(p_r)</code> 是极化 mask；<code>G_nm[p_t,p_r]</code> 是上节的 2×2 极化耦合。代码随后把每条 ray 的空间矩阵按 Frobenius 范数归一。</p></div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>Doppler：不同到达方向不同频移</h3><div class="eq">__EQ_DOPPLER__</div><p>UE 速度方向是 <span class="mono">φ_v</span>；ZOA 用天顶角，因此水平投影含 <span class="mono">sin(ZOA)</span>。</p></div>
    <div class="card"><h3>一个非镜面簇的时域矩阵</h3><div class="eq">__EQ_CLUSTER__</div><p><code>P_n</code> 是归一化簇功率，<code>M=20</code>。LOS specular 分量走确定性极化矩阵，不再重复叠加 K 因子。</p></div>
  </div>
  <div class="card" style="margin-top:16px"><h3>从时延域叠加到第 k 个 RB</h3><div class="eq">__EQ_FREQ__</div><p>当前频率步长不是单子载波 30 kHz，而是一个 RB 的 12×30 kHz=360 kHz。每个 RB 只保留一个代表性复矩阵。</p></div>
  <div class="callout blue" style="margin-top:16px"><h3>MIMO 的 64×4 正是在这里形成</h3>每条 ray 贡献一个由发射/接收 steering 外积构成的空间矩阵；不同簇、不同 ray、不同极化的矩阵相加，最终形成 64×4 的复数矩阵。每个元素同时带有幅度与相位，既不是路径损耗表，也不是单纯相关矩阵。</div>
  <div class="callout" style="margin-top:14px"><h3>几何旋转是工程映射</h3>CDL profile 的标称角度会整体旋到实际 BS↔UE 链路方向，但这不是完整的 38.901 §7.5 随机场景簇角生成。报告和结果中应称为“固定 CDL profile 的链路几何旋转”。</div>
</section>

<section id="timefreq" class="section">
  <div class="kicker">05 · Time, frequency, power</div><h2>一个 TTI 需要几个 H？H 里又包含哪些功率</h2>
  <div class="grid g2">
    <div class="card"><h3>内部生成 14 个 symbol，系统层保留一个 slot 快照</h3><div class="eq">__EQ_TIME__</div><p>ChannelHub 内部按 normal-CP 的平均 symbol 周期生成 14 个 OFDM symbol 上的 Doppler 相位，随后取中间 symbol 作为本 slot 的 H。这样不会把复数相位直接平均掉。</p><p><b>所以：</b>系统级每 TTI/slot 一个 H 快照通常足够；若研究高速时变或 symbol 级波束切换，才需要把 symbol 轴暴露出来。</p></div>
    <div class="card"><h3>一个 H 快照 ≠ 一个 SRS 观测维度</h3><p>四端口 SRS 可以在同一个 OFDM symbol 内，靠不同 cyclic shift / comb / CDM 的多个 RE 形成 4 个独立导频维度。因此“不需要为 14 个 symbol 各生成一份 H”与“四端口需要至少 4 个独立观测维度”并不矛盾。</p><div class="eq">__EQ_IDENTIFY__</div></div>
    <div class="card"><h3>存储 H 是归一化小尺度信道</h3><div class="eq">__EQ_NORM__</div><p>服务小区 H 的生成块均方元素功率归一到约 1。绝对路径损耗、阴影衰落、接收功率分别写在元数据中，不直接乘进服务 H。</p></div>
    <div class="card"><h3>方向图的绝对增益不会留在 H 中</h3><p>每条 CDL ray 的空间矩阵先除以 Frobenius 范数，最终 H 又做整体小尺度归一。因此阵元绝对增益、纯标量的子阵方向响应不会作为绝对功率留在 H；留下的是相对空间签名、相位、角度相关性和极化结构。</p><div class="eq">__EQ_RAY_NORM__</div></div>
  </div>
  <div class="callout red" style="margin-top:15px"><h3>这对 6° 固定下倾的解释很重要</h3>6° 馈电相位确实进入 <code>F</code> 和 steering；但对所有同构 1 驱 3 子阵共同形成的纯标量阵因子，在逐 ray 归一后会被消掉。若要让下倾和实测方向图改变不同簇/ray 的相对接收功率，后续需要把方向增益保留到 ray power，而不是在每条 ray 上全部归一掉。</div>
</section>

<section id="reciprocity" class="section">
  <div class="kicker">06 · TDD reciprocity</div><h2>下行 64×4 怎样变成上行 SRS 信道</h2>
  <p class="intro">配对模式先生成下行真值 <code>H_DL[BS,UE]</code>，再按 TDD 互易构造物理上行 <code>H_UL_phys[UE,BS]</code>。当前还叠加一个频率平滑、幅度默认 0.01 的校准误差。</p>
  <div class="eq">__EQ_RECIP__</div>
  <div class="grid g3">
    <div class="card"><h3>若 UE 上行 4 端口</h3><div class="eq">__EQ_UL4__</div><p>物理运算轴是 UE×BS；对外再转置成 BS×UE，于是得到熟悉的 64×4。</p></div>
    <div class="card"><h3>非公司 2Tx 场景</h3><div class="eq">__EQ_UL2__</div><p>通用配置仍可显式使用 2 个 UE Tx 端口；但 <code>link=BOTH</code> 必须使 Tx/Rx 端口合同一致，不能在配对数据中静默截列。</p></div>
    <div class="card"><h3>统一存储约定</h3><div class="eq">__EQ_STORE__</div><p>因此消费者始终看到最后两轴 <code>[BS_port, UE_port]</code>，但不能据此误以为上下行端口数天然相同。</p></div>
  </div>
</section>

<section id="srs" class="section">
  <div class="kicker">07 · SRS observation</div><h2>严格的四端口 SRS，应该怎样估出 64×4</h2>
  <p class="intro">先写正确的接收模型，再对照当前代码。设一个 RB 内选出 L 个可用于端口正交的 SRS RE / 码域观测；gNB 有 64 个接收端口，UE 有 4 个 SRS 端口。</p>
  <div class="truth"><h3>物理上正确的多端口观测</h3><div class="eq">__EQ_MULTI_OBS__</div><p class="muted">每一列是一个独立导频观测维度；同一 gNB 接收端口上，4 个 UE 端口的波形会先在空口相加，接收机必须借助 <code>X</code> 的正交性再分离。</p></div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>可辨识条件</h3><div class="eq">__EQ_RANKX__</div><p>只有 <code>X</code> 行满秩，四列信道才可分开。一个单独标量导频 RE 只有 <code>y=Hx</code> 的 64 个复观测，不能唯一恢复 256 个复信道系数。</p></div>
    <div class="card"><h3>四端口 LS</h3><div class="eq">__EQ_LS_MULTI__</div><p>若四端口序列正交且等能量，Gram 矩阵就是 <code>L·I₄</code>，LS 退化为相关/解扩。</p></div>
  </div>
  <h3 style="margin-top:24px">当前 SRS 序列是怎么生成的</h3>
  <div class="grid g3">
    <div class="card"><h3>频域序列长度</h3><div class="eq">__EQ_SRS_MSC__</div><p><code>K_TC=2</code> 时，每个 RB 有 6 个 SRS 子载波。<code>n_SRS_ID=PCI mod 1024</code>。</p></div>
    <div class="card"><h3>当前 1/2/4 端口</h3><p><code>N_ap</code> 来自 UE Tx 端口数；每端口按 38.211 分配 cyclic shift，并乘 <code>1/√N_ap</code> 固定 UE 总 SRS 功率。Rel-18 8-port TDM 尚未实现。</p></div>
    <div class="card"><h3>RB 中心到 SRS comb</h3><div class="eq">__EQ_DOWNSAMPLE__</div><p>RB 中心 H 先在时延域零填充并 Fourier 插值到 comb；物理接收、解扩后再取回 RB 中心，因此保留端口正交维而不暴露全 RE 张量。</p></div>
  </div>
</section>

<section id="gap" class="section">
  <div class="kicker">08 · Implemented correction</div><h2>旧的逐系数 oracle 观测已经怎样被替换</h2>
  <div class="split">
    <div class="card compare redtop"><span class="status bad">已删除的旧路径</span><h3>标量 pilot 广播到每个 H 元素</h3><div class="eq">__EQ_CURRENT_OBS__</div><p>这条路径把未知系数在观测前就分开，无法产生端口污染或不可辨识性，现已不再作为 UL SRS 估计入口。</p><div class="eq">__EQ_CURRENT_LS__</div></div>
    <div class="card compare"><span class="status ok">当前路径</span><h3>4 个端口先在每根 BS 天线上叠加</h3><div class="eq">__EQ_REAL_ONE__</div><p>同一个接收向量中加入服务端口、邻区端口、干扰和噪声，再通过 cyclic-shift 延迟窗解扩；无噪声 64×4 最大恢复误差 2.46e−7。</p></div>
  </div>
  <div class="callout green" style="margin-top:16px"><h3>准确结论</h3><ul class="tight"><li><b>64×4 真值 H：</b>阵列、CDL、极化、时延和 Doppler 均参与。</li><li><b>64×4 SRS 估计：</b>已由物理 Y、cyclic-shift 解扩和 LS/LMMSE 形成，不再逐元素偷看 H。</li><li><b>边界：</b>这是 RB 中心系统级抽象；8-port TDM、空间 covariance LMMSE 与实测 Jones 图仍待补。</li></ul></div>
  <div class="table-wrap" style="margin-top:16px"><table>
    <thead><tr><th>模块</th><th>当前状态</th><th>能宣称什么</th><th>不能宣称什么</th></tr></thead>
    <tbody>
      <tr><td>192 AE → 64 RF</td><td><span class="status ok">已实现</span></td><td>1 驱 3、0.5λ/0.67λ、F 耦合、64 端口输出</td><td>实测阵元方向图已校准</td></tr>
      <tr><td>CDL 多径 MIMO</td><td><span class="status ok">已实现</span></td><td>20 rays、角度、Doppler、时延、极化耦合</td><td>完整 §7.5 场景簇生成</td></tr>
      <tr><td>双极化</td><td><span class="status warn">结构已实现</span></td><td>pol mask + XPR 2×2 ray coupling</td><td>实测 Jones/XPD 已贯通</td></tr>
      <tr><td>64×4 UL 真值</td><td><span class="status ok">已实现</span></td><td>公司 BOTH preset 固定 4Tx/4Rx</td><td>任意 Tx/Rx 错配仍可配对</td></tr>
      <tr><td>四端口 SRS 估计</td><td><span class="status ok">已实现</span></td><td>物理端口叠加、解扩、干扰与噪声</td><td>Rel-18 8-port TDM / 全 RE 接收机</td></tr>
    </tbody>
  </table></div>
</section>

<section id="target" class="section">
  <div class="kicker">09 · Implemented baseline and next step</div><h2>RB 级物理多端口基线已落地；下一步只补真正需要的精度</h2>
  <p class="intro">当前实现保留真实 SRS comb、cyclic shift、端口功率、同一接收向量和干扰；系统层仍只存 RB 中心 H。后续按课题选择空间 LMMSE、8-port TDM 或全 RE 波形，不无条件扩大 14 倍数据。</p>
  <div class="grid g2">
    <div class="card compare"><span class="status ok">当前已做</span><h3>RB 中心 + SRS comb 多端口</h3><div class="eq">__EQ_TARGET_X__</div><ul class="tight"><li><code>N_ap=P∈{1,2,4}</code> 来自 UE Tx。</li><li>服务与邻区端口都先进入同一个 <code>Y</code>。</li><li>总 pilot 功率固定，禁止逐 H 元素广播。</li><li>LS 与频域 LMMSE 都从端口分离后的 pilot grid 估计。</li></ul></div>
    <div class="card compare"><span class="status info">后续精细化</span><h3>RE 级 SRS comb / cyclic shift / CDM</h3><ul class="tight"><li>保留每 RB 的 6 个 SRS RE（<code>K_TC=2</code>）。</li><li>按 38.211 端口、cyclic shift、comb offset 映射真实序列。</li><li>支持同/邻小区端口碰撞、非正交污染和端口特定 hopping。</li><li>仅在研究 SRS 资源设计、端口污染或估计器细节时开启。</li></ul></div>
  </div>
  <h3 style="margin-top:24px">必须补的反向测试</h3>
  <div class="grid g3">
    <div class="card"><h3>无噪声精确恢复</h3><p>随机 64×4 H 与满秩 X，断言 LS 误差接近机器精度；把 X 改成 rank 3，必须拒绝或明确报不可辨识。</p></div>
    <div class="card"><h3>端口交换等变</h3><p>同时置换 H 的列与 X 的行，估计结果只应做同样列置换，不能改变物理值。</p></div>
    <div class="card"><h3>污染反例</h3><p>让邻区复用完全相同 X，NMSE 必须显著恶化；换成正交 X 后恢复。若无差异，说明干扰没有真实进入观测。</p></div>
    <div class="card"><h3>2Tx / 4Tx 维度</h3><p>公司 BOTH 默认输出 64×4；通用 2Tx 仅用于非配对或 Tx/Rx 同为 2 的合同。禁止静默截列。</p></div>
    <div class="card"><h3>功率守恒</h3><p>端口数改变时总 SRS 功率口径必须固定并写清楚，避免 4 端口相当于凭空增加 6 dB 发射功率。</p></div>
    <div class="card"><h3>Gram 条件数</h3><p>落盘 <code>rank(X)</code>、<code>cond(XXᴴ)</code>、端口相关性；条件数异常必须告警。</p></div>
  </div>
  <div class="callout green" style="margin-top:16px"><h3>当前数据合同</h3><code>h_true</code>：DL 物理评估信道；<code>h_est</code>：UL SRS 预编码 CSI；<code>h_dl_est</code>：UE 侧 CSI-RS 估计。缺失 h_est 必须硬失败，绝不复制 h_true。</div>
</section>

<section id="tdl" class="section">
  <div class="kicker">10 · Alternative model</div><h2>补充：若不用 CDL，TDL 路径怎样构造 H</h2>
  <p class="intro">默认公司场景走 CDL，因此主推导以上节为准。TDL fallback 没有逐径角度，而是先生成 iid 复高斯 tap，再用 Kronecker 空间相关矩阵着色。</p>
  <div class="grid g2">
    <div class="card"><h3>空间相关与着色</h3><div class="eq">__EQ_TDL_CORR__</div><p><code>R_H</code>、<code>R_V</code> 按指数距离相关；<code>R_P</code> 用极化相关系数 μ。每个 tap 的 iid 高斯矩阵由左右 Cholesky 因子着色。</p></div>
    <div class="card"><h3>tap 到 RB 频响</h3><div class="eq">__EQ_TDL_FREQ__</div><p>TDL 能形成 64×4 频域矩阵，但空间结构是相关矩阵模型，不包含 CDL 的显式 AOD/AOA/ZOD/ZOA 与 20-ray 几何。</p></div>
  </div>
</section>

<section id="anchors" class="section">
  <div class="kicker">11 · Source anchors</div><h2>本文逐条对应的当前源码</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>主题</th><th>文件与行</th><th>关键事实</th></tr></thead>
    <tbody>
      <tr><td>公司硬件默认</td><td><div class="path">C:\Vibe\Wireless\superran\src\superran\hardware.py</div></td><td>8×4×2 RF、1 驱 3、192 AE、UE 4Tx/4Rx、±45°、272 RB</td></tr>
      <tr><td>默认 preset</td><td><div class="path">C:\Vibe\Wireless\superran\presets\presets.yaml</div></td><td><code>company_64t4r</code> 是 BOTH，UE Tx/Rx 均为 4</td></tr>
      <tr><td>F 与 steering</td><td><div class="path">C:\Vibe\AI\ChannelHub_main\src\msg_embedding\phy_sim\effective_array.py:163, 405, 421, 475, 506</div></td><td>馈电归一、位置、192×64 coupling、Jones 基、Fᴴ/Fᵀ</td></tr>
      <tr><td>CDL H</td><td><div class="path">C:\Vibe\AI\ChannelHub_main\src\msg_embedding\data\sources\internal_sim.py:712</div></td><td>20 rays、极化矩阵、Doppler、簇求和、时延 DFT</td></tr>
      <tr><td>链路维度选择</td><td><div class="path">C:\Vibe\AI\ChannelHub_main\src\msg_embedding\data\sources\internal_sim.py:2021</div></td><td>DL 用 BS Tx/UE Rx；UL 用 BS Rx/UE Tx</td></tr>
      <tr><td>SRS 序列</td><td><div class="path">C:\Vibe\AI\ChannelHub_main\src\msg_embedding\data\sources\_interference_estimation.py</div></td><td><code>N_ap∈{1,2,4}</code>、38.211 cyclic shift、固定总 pilot 功率</td></tr>
      <tr><td>互易与 UL 端口选择</td><td><div class="path">C:\Vibe\AI\ChannelHub_main\src\msg_embedding\data\sources\_interference_estimation.py:952</div></td><td>共轭转置 + 0.01 平滑校准误差；4R→2T 最强端口选择</td></tr>
      <tr><td>当前观测器</td><td><div class="path">C:\Vibe\AI\ChannelHub_main\src\msg_embedding\data\sources\_interference_estimation.py</div></td><td><code>Y=ΣHₚXₚ+I+N</code>，cyclic-shift 延迟窗分离</td></tr>
      <tr><td>标量 LS / LMMSE</td><td><div class="path">C:\Vibe\AI\ChannelHub_main\src\msg_embedding\channel_est\ls.py:29<br>C:\Vibe\AI\ChannelHub_main\src\msg_embedding\channel_est\mmse.py:43</div></td><td>标量 LS 与指数 PDP 频域 LMMSE</td></tr>
      <tr><td>现有审计证据</td><td><div class="path">C:\Vibe\Wireless\superran\artifacts\channel-generation-audit\evidence.json</div></td><td>DL 数据形状 [8,1,272,64,4]；SRS 审计形状 [4,1,272,64,4]</td></tr>
    </tbody>
  </table></div>
  <div class="callout" style="margin-top:16px"><h3>最终一句话</h3><b>64×4 H 来自“64 个 gNB RF 端口 × 4 个 UE SRS 端口”的双极化宽带 MIMO 通道；当前 SRS 已从同一物理接收向量恢复它。</b>仍需诚实标注 RB 中心抽象、无 8-port TDM、无空间 covariance LMMSE 和无实测 Jones 图。</div>
</section>

<footer class="foot">
  <div><b>SuperRAN · 64×4 SRS/H matrix explainer</b></div>
  <div>离线自包含 KaTeX：__KATEX_STATUS__ · 公式同时带原生 MathML 无 JS 兜底。生成脚本：<span class="mono">C:\Vibe\Wireless\superran\scripts\make_srs_64x4_explainer.py</span></div>
</footer>
</main>
__KATEX_UPGRADE__
</body>
</html>
"""


FORMULAS = {
    "__EQ_H_COLUMNS__": r"\mathbf H_{\mathrm{UL}}[k]=\begin{bmatrix}\mathbf h_1[k]&\mathbf h_2[k]&\mathbf h_3[k]&\mathbf h_4[k]\end{bmatrix}\in\mathbb C^{64\times4},\qquad \mathbf h_p[k]\in\mathbb C^{64}",
    "__EQ_TENSOR__": r"\mathbf H_{\mathrm{store}}\in\mathbb C^{N_{\mathrm{sample}}\times N_{\mathrm{slot}}\times272\times64\times P_{\mathrm{UE}}}",
    "__EQ_RANK__": r"\operatorname{rank}(\mathbf H[k])\le\min(64,4)=4",
    "__EQ_INDEX__": r"r(h,v_{\mathrm{RF}},p)=(4h+v_{\mathrm{RF}})\,2+p,\qquad e(h,v_{\mathrm{AE}},p)=(12h+v_{\mathrm{AE}})\,2+p",
    "__EQ_POS__": r"\frac{\mathbf x_e}{\lambda}=\begin{bmatrix}0\\(h-3.5)\cdot0.5\\(v_{\mathrm{AE}}-5.5)\cdot0.67\end{bmatrix}",
    "__EQ_FEED__": r"z_q=\left(q-\frac{M-1}{2}\right)d_v,\quad \widetilde w_q=A_q e^{j\phi_q}e^{j2\pi z_q\sin\theta_{\mathrm{tilt}}},\quad w_q=\frac{\widetilde w_q}{\sqrt{\sum_{i=0}^{M-1}|\widetilde w_i|^2}},\quad M=3",
    "__EQ_F__": r"F_{e,r}=\begin{cases}w_q,&e=e(h,3v_{\mathrm{RF}}+q,p),\ r=r(h,v_{\mathrm{RF}},p)\\0,&\text{otherwise}\end{cases},\quad \mathbf F\in\mathbb C^{192\times64},\quad \mathbf F^H\mathbf F=\mathbf I_{64}",
    "__EQ_DIRECTION__": r"\mathbf u(\varphi,\vartheta)=\begin{bmatrix}\cos\vartheta\cos\varphi&\cos\vartheta\sin\varphi&\sin\vartheta\end{bmatrix}^{T}",
    "__EQ_AE__": r"a_{\mathrm{AE},e}(\varphi,\vartheta,f)=g(\varphi,\vartheta)\exp\!\left[-j2\pi\frac{f}{f_{\mathrm{ref}}}\frac{\mathbf x_e}{\lambda}\cdot\mathbf u\right]",
    "__EQ_AEFF__": r"\mathbf a_{\mathrm{BS,rx}}=\mathbf F^H\mathbf a_{\mathrm{AE,rx}}\in\mathbb C^{64},\qquad \mathbf a_{\mathrm{BS,tx}}=\mathbf F^T\mathbf a_{\mathrm{AE,tx}}",
    "__EQ_POL_MASK__": r"\mathbf a^{(p)}=\mathbf a\odot\mathbf m^{(p)},\qquad m_i^{(p)}=\mathbb 1\{i\bmod2=p\}",
    "__EQ_JONES__": r"\mathbf e_{+45^{\circ}}=\frac{g}{\sqrt2}\begin{bmatrix}1\\1\end{bmatrix},\qquad \mathbf e_{-45^{\circ}}=\frac{g}{\sqrt2}\begin{bmatrix}1\\-1\end{bmatrix}",
    "__EQ_GPOL__": r"\mathbf G_{n,m}=\begin{bmatrix}e^{j\phi_{00}}&\kappa^{-1/2}e^{j\phi_{01}}\\\kappa^{-1/2}e^{j\phi_{10}}&e^{j\phi_{11}}\end{bmatrix},\qquad \kappa=10^{\mathrm{XPR}_{\mathrm{dB}}/10}",
    "__EQ_PATTERN__": r"A_H=\min\!\left(12\left(\frac{\varphi}{65^{\circ}}\right)^2,30\right),\quad A_V=\min\!\left(12\left(\frac{\vartheta}{65^{\circ}}\right)^2,30\right),\quad G_{\mathrm{dBi}}=8-\min(A_H+A_V,30),\quad g=10^{G_{\mathrm{dBi}}/20}",
    "__EQ_SPATIAL__": r"\widetilde{\mathbf S}_{n,m}=\sum_{p_t=0}^{1}\sum_{p_r=0}^{1}G_{n,m}[p_t,p_r]\left(\mathbf a_t\odot\mathbf m_t^{(p_t)}\right)\left(\mathbf a_r\odot\mathbf m_r^{(p_r)}\right)^H,\qquad \mathbf S_{n,m}=\frac{\widetilde{\mathbf S}_{n,m}}{\|\widetilde{\mathbf S}_{n,m}\|_F}",
    "__EQ_DOPPLER__": r"f_{D,n,m}=f_{D,\max}\sin(\mathrm{ZOA}_{n,m})\cos(\mathrm{AOA}_{n,m}-\varphi_v),\qquad d_{n,m}(t)=e^{j2\pi f_{D,n,m}t}",
    "__EQ_CLUSTER__": r"\mathbf H_n(t)=\sqrt{\frac{P_n}{M}}\sum_{m=1}^{M}d_{n,m}(t)\mathbf S_{n,m},\qquad M=20",
    "__EQ_FREQ__": r"\mathbf H(t,k)=\sum_{n=1}^{N_c}\mathbf H_n(t)e^{-j2\pi f_k\tau_n},\qquad f_k=k\Delta f_{\mathrm{RB}},\quad \Delta f_{\mathrm{RB}}=12\cdot30\,\mathrm{kHz}=360\,\mathrm{kHz}",
    "__EQ_TIME__": r"\mathbf H_{\mathrm{slot}}[k]=\mathbf H(t_{\mathrm{mid}},k),\qquad t_s=s\,\frac{T_{\mathrm{slot}}}{14},\quad s=0,\ldots,13",
    "__EQ_IDENTIFY__": r"N_{H\text{-snapshot}}=1\ \text{per slot},\qquad \operatorname{rank}(\mathbf X_{\mathrm{SRS}})=4\ \text{within the SRS resources}",
    "__EQ_NORM__": r"\mathbf H_{\mathrm{norm}}=\frac{\mathbf H_{\mathrm{raw}}}{\sqrt{\mathbb E_{t,k,b,u}\{|H_{\mathrm{raw}}(t,k,b,u)|^2\}}},\qquad \mathbb E\{|H_{\mathrm{norm}}|^2\}\approx1",
    "__EQ_RAY_NORM__": r"\mathbf S_{n,m}\leftarrow\frac{\mathbf S_{n,m}}{\|\mathbf S_{n,m}\|_F}\quad\Longrightarrow\quad \text{common scalar pattern gain is removed from that ray}",
    "__EQ_RECIP__": r"\mathbf H_{\mathrm{UL}}^{\mathrm{phys}}(t,k)=\mathbf H_{\mathrm{DL}}(t,k)^H+\epsilon_{\mathrm{cal}}\mathbf E_{\mathrm{smooth}}(t,k),\qquad \epsilon_{\mathrm{cal}}=0.01",
    "__EQ_UL4__": r"\mathbf H_{\mathrm{UL}}^{\mathrm{phys}}\in\mathbb C^{4\times64}\quad\xrightarrow{\text{store transpose}}\quad \mathbf H_{\mathrm{UL}}^{\mathrm{store}}\in\mathbb C^{64\times4}",
    "__EQ_UL2__": r"p^{\star}=\operatorname{Top2}_{p\in\{0,1,2,3\}}\ \mathbb E_{t,k,b}|H_{\mathrm{UL}}^{\mathrm{phys}}(t,k,p,b)|^2,\qquad \mathbf H_{\mathrm{UL}}^{\mathrm{store}}\in\mathbb C^{64\times2}",
    "__EQ_STORE__": r"H_{\mathrm{UL}}^{\mathrm{store}}[b,p]=H_{\mathrm{UL}}^{\mathrm{phys}}[p,b]",
    "__EQ_MULTI_OBS__": r"\underbrace{\mathbf Y[k]}_{64\times L}=\underbrace{\mathbf H_{\mathrm{UL}}[k]}_{64\times4}\underbrace{\mathbf X[k]}_{4\times L}+\sum_i\underbrace{\mathbf H_i[k]}_{64\times P_i}\underbrace{\mathbf X_i[k]}_{P_i\times L}+\underbrace{\mathbf N[k]}_{64\times L}",
    "__EQ_RANKX__": r"L\ge4,\qquad \operatorname{rank}(\mathbf X)=4,\qquad \det(\mathbf X\mathbf X^H)\ne0",
    "__EQ_LS_MULTI__": r"\widehat{\mathbf H}_{\mathrm{LS}}=\mathbf Y\mathbf X^H(\mathbf X\mathbf X^H)^{-1};\qquad \mathbf X\mathbf X^H=L\mathbf I_4\Rightarrow\widehat{\mathbf H}_{\mathrm{LS}}=\frac1L\mathbf Y\mathbf X^H",
    "__EQ_SRS_MSC__": r"M_{\mathrm{sc}}^{\mathrm{SRS}}=N_{\mathrm{RB}}^{\mathrm{SRS}}\frac{12}{K_{\mathrm{TC}}},\qquad K_{\mathrm{TC}}=2\Rightarrow M_{\mathrm{sc}}^{\mathrm{SRS}}=6N_{\mathrm{RB}}^{\mathrm{SRS}}",
    "__EQ_DOWNSAMPLE__": r"x_{\mathrm{RB}}[k]=x_{\mathrm{SRS}}\!\left[k\frac{12}{K_{\mathrm{TC}}}\right]",
    "__EQ_CURRENT_OBS__": r"Y_{\mathrm{current}}[k,b,p]=H[k,b,p]x[k]+I[k,b,p]+N[k,b,p]",
    "__EQ_CURRENT_LS__": r"\widehat H[k,b,p]=\frac{Y_{\mathrm{current}}[k,b,p]x[k]^*}{|x[k]|^2+\varepsilon}",
    "__EQ_REAL_ONE__": r"y_b[k,\ell]=\sum_{p=1}^{4}H[k,b,p]x_p[k,\ell]+n_b[k,\ell]",
    "__EQ_TARGET_X__": r"\mathbf X[k]=\sqrt{E_s}\,\mathbf U_{P\times L},\qquad \mathbf U\mathbf U^H=\mathbf I_P,\qquad P\in\{1,2,4\},\ L\ge P",
    "__EQ_TDL_CORR__": r"\mathbf R=\mathbf R_H\otimes\mathbf R_V\otimes\mathbf R_P,\quad R_H[i,j]=\rho_H^{|i-j|},\quad \mathbf R_P=\begin{bmatrix}1&\mu\\\mu&1\end{bmatrix},\quad \mu=10^{-\mathrm{XPD}_{\mathrm{dB}}/10},\quad \mathbf H_\ell=\mathbf L_{\mathrm{tx}}\mathbf G_\ell\mathbf L_{\mathrm{rx}}^H",
    "__EQ_TDL_FREQ__": r"\mathbf H(t,k)=\sum_{\ell=1}^{L_{\mathrm{tap}}}\mathbf H_\ell(t)e^{-j2\pi f_k\tau_\ell}",
}


def main() -> None:
    page = HTML
    replacements = {
        "__KATEX_HEAD__": katex.head_assets(),
        "__KATEX_UPGRADE__": katex.upgrade_script(),
        "__KATEX_STATUS__": (
            "已内联，页面无需联网" if katex.available() else "资产缺失，当前仅使用 MathML"
        ),
    }
    replacements.update({marker: formula(tex) for marker, tex in FORMULAS.items()})
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    missing = [marker for marker in list(replacements) + list(FORMULAS) if marker in page]
    if missing:
        raise RuntimeError("unexpanded markers: " + repr(sorted(set(missing))))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(OUT)
    print("bytes=" + str(OUT.stat().st_size))
    print("formulas=" + str(len(FORMULAS)))
    print("katex=" + str(katex.available()))
    print("title_ok=" + str("64×4 SRS 信道矩阵是怎么来的" in page))
    print("utf8_ok=" + str(OUT.read_text(encoding="utf-8").startswith("<!doctype html>")))


if __name__ == "__main__":
    main()
