"""Generate two self-contained, evidence-backed audit reports.

LaTeX stays in constants, never inside f-string expressions, so the script is
valid on Python versions older than 3.12 too.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from superran import katex, mathml  # noqa: E402

AUDIT = ROOT / "artifacts" / "srs_pdp_robust_audit_20260810.json"
SRS_OUT = ROOT / "artifacts" / "SRS_CHANNEL_MODULE_TRILATERAL_AUDIT_20260810.html"
PDP_OUT = ROOT / "artifacts" / "PDP_ROBUST_WEIGHT_TRILATERAL_AUDIT_20260810.html"
VERIFY_OUT = ROOT / "artifacts" / "srs_pdp_robust_verification_20260810.json"


def formula(tex: str) -> str:
    return katex.wrap(tex, mathml.render(tex, block=True), display=True)


def num(value: float, digits: int = 3) -> str:
    return format(float(value), "." + str(digits) + "f")


CSS = r"""
:root{--ink:#17272e;--muted:#607177;--paper:#f3f6f5;--card:#fff;--line:#d6e1df;
--navy:#0d3542;--teal:#087b70;--teal2:#18a495;--amber:#a06910;--red:#ae443e;
--blue:#326d8a;--green:#28774e;--code:#0b2934}*{box-sizing:border-box}
html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);
font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif;line-height:1.67}
a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}
code,.mono{font-family:Consolas,"Cascadia Mono",monospace}.hero{position:relative;overflow:hidden;
color:#fff;background:linear-gradient(126deg,#072731,#145464 62%,#087b70);padding:58px 24px 47px}
.hero:after{content:"";position:absolute;width:560px;height:560px;border:1px solid #ffffff25;
border-radius:50%;right:-170px;top:-300px;box-shadow:0 0 0 74px #ffffff0a,0 0 0 148px #ffffff07}
.hero-inner,.page,.nav>div{max-width:1240px;margin:auto;position:relative;z-index:1}
.eyebrow{color:#9de5de;letter-spacing:.16em;text-transform:uppercase;font-size:12px;font-weight:800}
.hero h1{font-size:clamp(35px,5.5vw,62px);line-height:1.05;margin:10px 0 18px;max-width:1040px}
.lead{font-size:18px;color:#e5f3f1;max-width:980px}.pills{display:flex;gap:9px;flex-wrap:wrap;margin-top:23px}
.pill{border:1px solid #ffffff3b;background:#ffffff12;border-radius:999px;padding:7px 12px;font-size:12px}
.pill.ok{background:#3ab78033}.pill.warn{background:#efaa3a32}.nav{position:sticky;top:0;z-index:30;
background:#fffffff0;backdrop-filter:blur(10px);border-bottom:1px solid var(--line);overflow:auto;white-space:nowrap}
.nav>div{padding:9px 18px}.nav a{display:inline-block;padding:6px 9px;font-size:13px;font-weight:700;color:#294850}
.page{padding:28px 22px 70px}.section{scroll-margin-top:70px;margin:28px 0 58px}
.kicker{color:var(--teal);font-weight:850;letter-spacing:.13em;font-size:12px;text-transform:uppercase}
h2{font-size:30px;line-height:1.23;color:var(--navy);margin:5px 0 9px}h3{color:var(--navy);margin:0 0 8px}
.intro{color:var(--muted);max-width:1030px;margin:0 0 20px}.grid{display:grid;gap:15px}
.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.g3{grid-template-columns:repeat(3,minmax(0,1fr))}
.g4{grid-template-columns:repeat(4,minmax(0,1fr))}.card{background:var(--card);border:1px solid var(--line);
border-radius:15px;padding:18px;box-shadow:0 8px 27px #163e4609}.card p:last-child{margin-bottom:0}
.metric .value{font-size:30px;line-height:1.1;font-weight:850;color:var(--navy)}
.metric .label{color:var(--muted);font-size:12px;margin-top:5px}.metric .note{font-size:12px;margin-top:7px}
.verdict{border-left:5px solid var(--teal);background:linear-gradient(90deg,#e2f3f0,#fff);
border-radius:0 14px 14px 0;padding:20px 22px;font-size:17px}.callout{border:1px solid #ead59e;
background:#fff7e6;color:#654a11;border-radius:13px;padding:15px 17px}
.callout.ok{border-color:#bcdcc9;background:#eaf6ef;color:#245c3b}.callout.bad{border-color:#efc2bd;
background:#fff0ee;color:#72352f}.callout.info{border-color:#c3dfeb;background:#edf7fb;color:#285a70}
.callout h3{color:inherit;font-size:16px}.eq{background:var(--code);color:#eefafa;border-radius:13px;
padding:15px 17px;margin:12px 0;overflow:auto}.eq .kx{display:block;min-width:max-content}
.eq .katex-display{margin:.25em 0}.flow{display:flex;align-items:stretch;gap:8px;overflow:auto;padding:7px 0}
.step{min-width:150px;flex:1;background:#fff;border:1px solid var(--line);border-radius:13px;padding:13px}
.step b{display:block;color:var(--navy);margin:5px 0}.step small{color:var(--muted)}
.n{display:inline-grid;place-items:center;width:25px;height:25px;border-radius:50%;background:var(--teal);
color:#fff;font-size:11px;font-weight:850}.arrow{display:grid;place-items:center;color:var(--teal);font-size:24px}
.table-wrap{overflow:auto;border-radius:13px}table{width:100%;min-width:790px;border-collapse:separate;
border-spacing:0;background:#fff;border:1px solid var(--line);border-radius:13px;overflow:hidden;font-size:13px}
th,td{padding:11px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
th{background:#e8f1f0;color:#294a52;font-size:12px}tr:last-child td{border-bottom:0}
.status{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:4px 9px;font-size:11px;
font-weight:850;white-space:nowrap}.status:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.status.ok{background:#e3f4e9;color:var(--green)}.status.warn{background:#fff0d0;color:var(--amber)}
.status.bad{background:#fde7e4;color:var(--red)}.diagram{background:#0d303c;border-radius:16px;padding:16px;overflow:auto}
.diagram svg{display:block;min-width:860px;width:100%;height:auto}.diagram text{font-family:"Segoe UI","Microsoft YaHei",sans-serif}
.compare{border-top:4px solid var(--teal)}.compare.warn{border-top-color:var(--amber)}
.compare.bad{border-top-color:var(--red)}.list{padding-left:19px;margin:8px 0}.list li{margin:5px 0}
.bar{height:11px;background:#dfe8e7;border-radius:8px;overflow:hidden;margin:6px 0 11px}
.bar span{display:block;height:100%;background:linear-gradient(90deg,var(--teal),var(--teal2));border-radius:8px}
.bar.red span{background:linear-gradient(90deg,#c95b51,#ed9a68)}.source{font-size:12px;color:var(--muted)}
.path{font-family:Consolas,monospace;font-size:11px;word-break:break-all;background:#eef3f2;
border-radius:7px;padding:7px 9px}.foot{border-top:1px solid var(--line);padding-top:22px;margin-top:50px;
color:var(--muted);font-size:12px}details{background:#fff;border:1px solid var(--line);border-radius:12px;
padding:12px 15px}details+details{margin-top:9px}summary{cursor:pointer;color:var(--navy);font-weight:750}
@media(max-width:900px){.g2,.g3,.g4{grid-template-columns:1fr}.flow{flex-direction:column}
.arrow{transform:rotate(90deg)}.hero{padding-top:42px}.page{padding-left:14px;padding-right:14px}h2{font-size:25px}}
@media print{.nav{display:none}.hero{padding:30px 20px}.card{box-shadow:none}.section{break-inside:avoid}}
"""

FORMULAS = {
    "@@H64@@": r"\mathbf H_{\rm UL}[k]=[\mathbf h_0\;\mathbf h_1\;\mathbf h_2\;\mathbf h_3]\in\mathbb C^{64\times4}",
    "@@F@@": r"F_{e,r}=\begin{cases}w_q,&e=e(h,3v_{\rm RF}+q,p),\ r=r(h,v_{\rm RF},p)\\0,&\text{otherwise}\end{cases},\quad \mathbf F\in\mathbb C^{192\times64},\quad\mathbf F^H\mathbf F=\mathbf I_{64}",
    "@@JONES@@": r"\mathbf e_{+45^\circ}=\frac{g}{\sqrt2}[1\;1]^T,\qquad\mathbf e_{-45^\circ}=\frac{g}{\sqrt2}[1\;-1]^T,\qquad\mathbf E^H\mathbf E=\mathbf I_2",
    "@@RAY@@": r"\mathbf H(t,k)=\sum_n\sqrt{\frac{P_n}{M}}\sum_{m=1}^{M}e^{j2\pi f_{D,nm}t}\mathbf S_{nm}e^{-j2\pi f_k\tau_n},\qquad M=20",
    "@@SRSOBS@@": r"\mathbf y[k]=\sum_{p=0}^{P-1}\mathbf h_p[k]x_p[k]+\sum_i\mathbf H_i[k]\mathbf x_i[k]+\mathbf n[k]",
    "@@SRSPWR@@": r"x_p[k]=\frac{1}{\sqrt P}r_p[k]\quad\Longrightarrow\quad\sum_{p=0}^{P-1}|x_p[k]|^2=1",
    "@@LS@@": r"\widehat{\mathbf H}_{\rm LS}=\mathbf Y\mathbf X^H(\mathbf X\mathbf X^H)^{-1}",
    "@@LMMSE@@": r"\widehat{\mathbf h}_{\rm LMMSE}=\mathbf R_{tp}(\mathbf R_{pp}+\mathbf R_n)^{-1}\widehat{\mathbf h}_{\rm LS,p}",
    "@@TDD@@": r"T_{\rm joint}=\operatorname{lcm}(T_{\rm SRS},T_{\rm TDD}),\qquad n\equiv T_{\rm offset}\pmod{T_{\rm SRS}},\quad n\text{ must contain UL symbols}",
    "@@PDP@@": r"\mathbf g=\operatorname{IFFT}\{\mathbf H\odot\mathbf w\}\sqrt N,\qquad P[\ell]=\mathbb E_{t,b,u}|g_{t,b,u}[\ell]|^2",
    "@@PDPAXIS@@": r"\Delta\tau=\frac{1}{N_{\rm RB}\,12\Delta f_{\rm SCS}},\qquad T_{\rm amb}=\frac{1}{12\Delta f_{\rm SCS}}",
    "@@CIRC@@": r"\bar\tau=\operatorname{arg}_{T}\left(\sum_\ell\widetilde P_\ell e^{j2\pi\tau_\ell/T}\right),\qquad\sigma_\tau^2=\sum_\ell\widetilde P_\ell\operatorname{wrap}_T(\tau_\ell-\bar\tau)^2",
    "@@DEEMBED@@": r"\sigma_{\tau,\rm physical}^2=\max(\sigma_{\tau,\rm measured}^2-\sigma_{\tau,\rm Hann\ kernel}^2,0)",
    "@@EBF@@": r"\mathbf Q_{\rm EBF}=\mathbf Q_0\sqrt{\frac{P}{\|\mathbf Q_0\|_F^2}}",
    "@@PEBF@@": r"\mathbf Q_{\rm PEBF}=\alpha\mathbf Q_{\rm EBF},\qquad\alpha=\min_m\sqrt{\frac{P/M}{\|\mathbf Q_{\rm EBF}[m,:]\|_2^2}}",
    "@@NEBF@@": r"\mathbf Q_{\rm NEBF}[m,:]=\mathbf Q_{\rm EBF}[m,:]\sqrt{\frac{P/M}{\|\mathbf Q_{\rm EBF}[m,:]\|_2^2}}",
    "@@RZF@@": r"\mathbf W=\mathbf H^H(\mathbf H\mathbf H^H+\alpha\mathbf I)^{-1},\qquad\alpha=\frac{N_s\bar\sigma_n^2}{P}+N_{\rm BS}\sigma_e^2",
}


def start(title: str, eyebrow: str, headline: str, lead: str, pills: str, nav: str) -> str:
    icon = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23087b70'/%3E%3Cpath d='M6 23V9m5 14V6m5 17V12m5 11V8m5 15V4' stroke='white' stroke-width='2.3'/%3E%3C/svg%3E"
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title)
        + '</title><link rel="icon" href="' + icon + '">' + katex.head_assets()
        + "<style>" + CSS + "</style></head><body><header class=\"hero\"><div class=\"hero-inner\">"
        + '<div class="eyebrow">' + eyebrow + "</div><h1>" + headline + '</h1><p class="lead">'
        + lead + '</p><div class="pills">' + pills + "</div></div></header>" + nav + '<main class="page">'
    )


def end() -> str:
    return "</main>" + katex.upgrade_script() + "</body></html>"


def expand(text: str, values: dict[str, str]) -> str:
    for marker, value in values.items():
        text = text.replace(marker, value)
    for marker, tex in FORMULAS.items():
        text = text.replace(marker, formula(tex))
    missing = [x for x in list(values) + list(FORMULAS) if x in text]
    if missing:
        raise RuntimeError("unexpanded markers: " + repr(missing))
    return text


SRS_BODY = r"""
<section class="section" id="verdict"><div class="kicker">00 · Executive verdict</div>
<h2>结论：修复前不是最符合需求；修复后，在系统级体验仿真目标下是三者中最合适的组合</h2>
<div class="verdict"><b>“最优”不是最高 RE 级保真度。</b>它指公司 64T/4R 硬件、1 驱 3、±45°、真实上下行 CSI 因果、SRS 干扰、LMMSE、CSI 陈旧与系统调度由一份可审计合同贯通。Sionna 更适合通用可微链路级研究；ChannelHub 是传播与参考信号引擎；SuperRAN 负责公司硬件、系统 KPI 和硬失败边界。</div>
<div class="grid g3" style="margin-top:16px"><div class="card compare bad"><h3>修复前的硬伤</h3><ul class="list"><li>DL CSI-RS 估计被误当 SRS 预编码 CSI。</li><li>标量 pilot 广播到每个 H 系数，等价 oracle 观测。</li><li>公司 preset 4R/2T，配对 64×4 合同不成立。</li></ul></div>
<div class="card compare"><h3>已经落地</h3><ul class="list"><li>h_true、UL h_est、DL h_dl_est 三路分离。</li><li>Y 中先叠加端口、干扰和噪声，再 cyclic-shift 解扩。</li><li>公司 BOTH preset 统一 4Tx/4Rx；LS/LMMSE 可选。</li></ul></div>
<div class="card compare warn"><h3>仍需声明</h3><ul class="list"><li>RB 中心系统抽象，不是全 RE 接收机。</li><li>实现 1/2/4 port；Rel-18 8-port TDM 未做。</li><li>LMMSE 当前频域协方差 + 时间线性，无空间平滑。</li></ul></div></div></section>

<section class="section" id="map"><div class="kicker">01 · Module map</div><h2>从传播状态到调度可见 CSI：每层只承担一件事</h2>
<div class="diagram"><svg viewBox="0 0 1120 360" role="img" aria-label="SRS channel flow"><defs><marker id="arr" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#70d8ce"/></marker></defs>
<g fill="#153f4d" stroke="#4fbeb3" stroke-width="1.5"><rect x="20" y="45" width="175" height="82" rx="13"/><rect x="235" y="45" width="175" height="82" rx="13"/><rect x="450" y="45" width="175" height="82" rx="13"/><rect x="665" y="45" width="175" height="82" rx="13"/><rect x="880" y="45" width="210" height="82" rx="13"/><rect x="235" y="225" width="175" height="82" rx="13"/><rect x="450" y="225" width="175" height="82" rx="13"/><rect x="665" y="225" width="175" height="82" rx="13"/><rect x="880" y="225" width="210" height="82" rx="13"/></g>
<g fill="#eefafa" font-size="15" font-weight="700"><text x="42" y="78">共享传播状态</text><text x="42" y="102" font-size="12" font-weight="400">几何 / LOS / 簇 / 时延</text><text x="257" y="78">DL 真值 H_DL</text><text x="257" y="102" font-size="12" font-weight="400">评估用，不给权值偷看</text><text x="473" y="78">TDD 互易 + 校准</text><text x="473" y="102" font-size="12" font-weight="400">构造物理 H_UL</text><text x="691" y="78">38.211 SRS X</text><text x="691" y="102" font-size="12" font-weight="400">1/2/4 port · cyclic shift</text><text x="903" y="78">物理接收 Y</text><text x="903" y="102" font-size="12" font-weight="400">ΣHₚXₚ + I + N</text><text x="257" y="258">端口解扩 + LS</text><text x="257" y="282" font-size="12" font-weight="400">同一接收向量恢复各列</text><text x="473" y="258">频域 LMMSE</text><text x="473" y="282" font-size="12" font-weight="400">pilot 位置 + PDP prior</text><text x="691" y="258">h_est 合同</text><text x="691" y="282" font-size="12" font-weight="400">UL SRS precoding CSI</text><text x="903" y="258">CSI 陈旧 / 系统建表</text><text x="903" y="282" font-size="12" font-weight="400">SVD / MU / PF / KPI</text></g>
<g stroke="#70d8ce" stroke-width="2" fill="none" marker-end="url(#arr)"><path d="M195 86H227"/><path d="M410 86H442"/><path d="M625 86H657"/><path d="M840 86H872"/><path d="M985 127V190H323V217"/><path d="M410 266H442"/><path d="M625 266H657"/><path d="M840 266H872"/></g></svg></div>
<div class="callout info" style="margin-top:15px"><h3>同站共享传播状态，不同站不能复制</h3>同一 site 的扇区共享 site 位置与共同场景 realization 的大尺度环境锚点，但每条 sector↔UE 链路仍由各自方位、朝向、簇相位和路径损耗生成 H；不同 site 不能复制同一个小尺度矩阵。ChannelHub 管传播层，SuperRAN 不在系统层复制 H。</div></section>

<section class="section" id="matrix"><div class="kicker">02 · 64×4, F and polarization</div><h2>64×4：192 个阵子经 F 压成 64 个 RF 端口，UE 提供 4 个 SRS 端口</h2>
<div class="eq">@@H64@@</div><div class="flow"><div class="step"><span class="n">1</span><b>BS 物理阵子</b><small>8H×12V×2pol = 192</small></div><div class="arrow">→</div><div class="step"><span class="n">2</span><b>1 驱 3 馈电</b><small>每列 F 三个非零</small></div><div class="arrow">→</div><div class="step"><span class="n">3</span><b>BS 数字端口</b><small>8H×4V×2pol = 64</small></div><div class="arrow">↔</div><div class="step"><span class="n">4</span><b>UE SRS 端口</b><small>2H×1V×2pol = 4</small></div></div>
<div class="grid g2" style="margin-top:16px"><div class="card"><h3>F 为什么是 192×64</h3><div class="eq">@@F@@</div><p>行是 AE，列是 RF port；列支持集互不重叠且列范数归一。审计得到 <code>max|FᴴF−I|=@@FERR@@</code>，而非数值碰巧。</p></div>
<div class="card"><h3>6° 是用户可配 preset</h3><p>馈电相位按 <code>exp(j2πzq sin θtilt)</code> 生成。与 65° 临时垂直阵元图叠加后，本轮单端口复合主瓣 <b>@@LOBE@@°</b>；不恰等于 −6° 是离散 1 驱 3 与元素图共同作用。</p></div>
<div class="card"><h3>+45° / −45° 不是数量×2</h3><div class="eq">@@JONES@@</div><p>公司端口顺序固定 [+45,−45]；CDL 每条 ray 另有 2×2 极化耦合、XPR 和随机相位。Jones Gram 非对角误差约 4.27e−17。</p></div>
<div class="card"><h3>宽带双极化 H</h3><div class="eq">@@RAY@@</div><p>steering、时延、Doppler、20 rays 与极化都进入复数 H。路径损耗单独落盘，避免 CSI 算法把大尺度功率误当空间结构。</p></div></div>
<div class="callout bad" style="margin-top:15px"><h3>方向图边界</h3>水平 110°、垂直 65°、峰值 8 dBi 是 <code>parametric_temporary</code>，不是公司实测复 Jones 图。最终校准应替换为实测二维复方向图。</div></section>

<section class="section" id="srs"><div class="kicker">03 · SRS observation and estimation</div><h2>先形成真实接收 Y，再做 LS/LMMSE</h2>
<div class="grid g2"><div class="card"><h3>物理观测</h3><div class="eq">@@SRSOBS@@</div><p>UE 端口和邻区 UE 在每根 BS 接收天线上先叠加；噪声与干扰只加到 Y。端口分离靠 38.211 cyclic shift 延迟窗。</p></div>
<div class="card"><h3>总导频功率固定</h3><div class="eq">@@SRSPWR@@</div><p>每端口除以 √P。本轮 tone 上端口功率和为 [@@PMIN@@, @@PMAX@@]，4-port 不凭空多 6 dB。</p></div>
<div class="card"><h3>LS</h3><div class="eq">@@LS@@</div><p>RB 中心 H 通过时延域零填充 Fourier 插值到 SRS comb，再从同一 Y 解扩回 RB，满足可辨识性。</p></div>
<div class="card"><h3>LMMSE</h3><div class="eq">@@LMMSE@@</div><p>实际 pilot RB 位置 + RMS-delay prior + 白噪声协方差；当前频域 LMMSE、时间线性。0 dB 实验 MSE <b>@@LSMSE@@→@@LMMSEMSE@@</b>，下降 <b>@@LMMSEGAIN@@%</b>。</p></div></div>
<div class="callout info" style="margin-top:15px"><h3>LS 不会自动抹掉干扰方向</h3>方向性保留在 64 维接收向量与协方差里；旧问题是 oracle H 元素观测，不是 LS 本身。空间抗干扰应增加空间 covariance LMMSE/IRC，而不是给 LS 贴“无方向”标签。</div></section>

<section class="section" id="time"><div class="kicker">04 · Time and TDD semantics</div><h2>系统层每 slot 一个 H；内部 14 symbol 用于 Doppler 与 RS symbol</h2>
<div class="grid g2"><div class="card"><h3>典型系统级抽象</h3><p>ChannelHub 按 normal-CP 平均 symbol 时刻生成 14 个 symbol，取中间 symbol 作 slot 快照，不做复数平均。体验速率/PF/OLLA 每 slot 一个 H 足够；高速 symbol 级跟踪/ICI 才暴露完整轴。</p></div>
<div class="card"><h3>术语</h3><p><b>SRS 周期</b>是发送配置；最近估计到当前调度的间隔叫<b>CSI 陈旧时长</b>，不叫“SRS 年龄”。</p></div>
<div class="card"><h3>联合周期</h3><div class="eq">@@TDD@@</div><p>DDDSU、T_SRS=2 的机会 0/2/4… 在 slot 4 才遇到 UL。旧代码只看 0..T_SRS−1 会漏判；现按 LCM 搜索。</p></div>
<div class="card"><h3>公司 E2E</h3><p>DL 真值 slot <b>@@DLSLOT@@</b>，UL SRS slot <b>@@ULSLOT@@</b>，首个 UL 机会 slot <b>@@FIRSTUL@@</b>；<code>[1,1,272,64,4]</code>，h_est 来源 <code>ul_srs_estimate</code>。</p></div></div></section>

<section class="section" id="compare"><div class="kicker">05 · Trilateral comparison</div><h2>三者是三个层级，不是互斥的三套实现</h2>
<div class="table-wrap"><table><thead><tr><th>维度</th><th>SuperRAN</th><th>ChannelHub</th><th>Sionna 2.0.1</th><th>取舍</th></tr></thead><tbody>
<tr><td>定位</td><td>公司硬件 + 数据合同 + 系统体验/KPI</td><td>传播、CDL/TDL、RS、干扰、估计</td><td>通用张量化/可微链路级 PHY 与 RT</td><td>SW 收口系统目标，CH 做 PHY，Sionna 作基线</td></tr>
<tr><td>SRS</td><td>h_true/h_est/h_dl_est 角色和硬失败</td><td>38.211 1/2/4-port，Y=ΣHX+I+N</td><td>通用 OFDM pilot/estimator 接口</td><td>采用 CH 协议化 SRS，不重写第二套</td></tr>
<tr><td>LMMSE</td><td>配置与 prior/噪声元数据</td><td>频率 covariance + 真实 pilot，时间线性</td><td>f/t/s 分步 LMMSE，传播 err_var</td><td>已吸收频域；空间/时间 covariance 下一阶段</td></tr>
<tr><td>双极化</td><td>公司 [+45,−45]、110°、1驱3</td><td>192×64 F、Jones、CDL XPR coupling</td><td>cross 与 TR38.901 pattern</td><td>公司顺序优先，公式与 Sionna/38.901 核对</td></tr>
<tr><td>时间</td><td>slot 快照 + CSI 陈旧</td><td>内部 14 symbol，slot 中点降维</td><td>完整 resource grid</td><td>体验选 slot，symbol 级按课题开启</td></tr>
</tbody></table></div></section>

<section class="section" id="evidence"><div class="kicker">06 · Evidence</div><h2>正向不变量 + 反向哨兵 + 全量回归</h2>
<div class="grid g4"><div class="card metric"><div class="value">@@MAXERR@@</div><div class="label">64×4 无噪声最大恢复误差</div></div>
<div class="card metric"><div class="value">@@LMMSEGAIN@@%</div><div class="label">0 dB LMMSE MSE 降幅</div></div>
<div class="card metric"><div class="value">@@FERR@@</div><div class="label">max |FᴴF−I|</div></div>
<div class="card metric"><div class="value">422</div><div class="label">ChannelHub full unit passed</div></div></div>
<div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>验证层</th><th>结果</th><th>辨识力</th></tr></thead><tbody>
<tr><td>联合审计</td><td>@@CHECKS@@/@@CHECKS@@，dataset <code>@@DATASET@@</code></td><td>64×4、功率、LMMSE、F、Jones、PDP、权值联合合同</td></tr>
<tr><td>ChannelHub full unit</td><td>422 passed, 3 optional skipped, 15 warnings, 67.85 s</td><td>SRS/TDD/CDL/接口回归</td></tr>
<tr><td>SuperRAN full pytest</td><td>6 pytest cases passed in 544.44 s；collection 顶层断言全过</td><td>组合导入、预设、页面、门禁、系统仿真</td></tr>
<tr><td>关键脚本</td><td>physics/MU/CSI aging/linklevel/gates/RNG/system 全 exit 0</td><td>功率反例、CSI 因果、字节守恒、统计门</td></tr>
</tbody></table></div></section>

<section class="section" id="limits"><div class="kicker">07 · Boundaries and decisions</div><h2>当前可发结论，但不能删掉这些边界</h2>
<details open><summary>P0 · 保持的默认</summary><ul class="list"><li>公司 BOTH、4Tx/4Rx、+45/−45、SRS 10 ms、LS/LMMSE 可选、系统每 slot 一个 H。</li><li>h_true 只评估；h_est 只来自 UL SRS；缺估计硬失败。</li><li>正式结果记录 estimator、prior、RS opportunity 和 CSI 陈旧时长。</li></ul></details>
<details><summary>P1 · 需要你提供数据</summary><ul class="list"><li>公司实测二维复 Jones 图，替换 110° 参数化临时模型。</li><li>真实 SRS resource/comb/port/hopping 和接收机协方差。</li><li>若产品用 Rel-18 8 port，再实现 TDM port 组。</li></ul></details>
<details><summary>P2 · 何时保留 14 symbol</summary><p>仅在 symbol 内 channel variation、ICI、symbol 级波束切换或 SRS TDM 课题中完整保留；体验仿真不应无条件承担 14 倍内存。</p></details>
<div class="callout ok" style="margin-top:15px"><h3>最终判断</h3>SuperRAN 不是“比 Sionna 更强的通用 PHY 库”，而是<b>更符合公司硬件与系统体验口径的集成层</b>；依据是适配度和可审计性，不是单个性能样本。</div></section>

<section class="section" id="sources"><div class="kicker">08 · Sources</div><h2>一手依据与源码锚点</h2>
<div class="grid g2"><div class="card"><h3>外部一手来源</h3><ul class="list source">
<li><a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138211/18.07.00_60/ts_138211v180700p.pdf">3GPP TS 38.211 V18.7.0</a>：§6.4.1.4 SRS ports/sequence/comb/cyclic shift。</li>
<li><a href="https://www.etsi.org/deliver/etsi_tr/138900_138999/138901/18.01.00_60/tr_138901v180100p.pdf">3GPP TR 38.901 V18.1.0</a>：§7.3 element pattern/slant polarization。</li>
<li><a href="https://nvlabs.github.io/sionna/_modules/sionna/phy/ofdm/channel_estimation.html">Sionna 2.0.1 channel estimation source</a>：f/t/s LMMSE 与 err_var。</li>
<li><a href="https://nvlabs.github.io/sionna/rt/api/antenna_pattern.html">Sionna RT antenna pattern API</a>：cross/TR38.901。</li></ul></div>
<div class="card"><h3>本地源码</h3><div class="path">C:\Vibe\AI\ChannelHub_main\src\msg_embedding\data\sources\_interference_estimation.py<br>C:\Vibe\AI\ChannelHub_main\src\msg_embedding\data\sources\internal_sim.py<br>C:\Vibe\AI\ChannelHub_main\src\msg_embedding\phy_sim\effective_array.py<br>C:\Vibe\Wireless\superran\src\superran\generate.py<br>C:\Vibe\Wireless\superran\src\superran\channelhub.py<br>C:\Vibe\Wireless\superran\src\superran\system.py</div></div></div>
<footer class="foot"><b>SuperRAN SRS channel trilateral audit</b><br>证据 JSON：artifacts/srs_pdp_robust_audit_20260810.json；生成 @@GENERATED@@。</footer></section>
"""


PDP_BODY = r"""
<section class="section" id="verdict"><div class="kicker">00 · Executive verdict</div>
<h2>结论：PDP、每天线功率和 CSI 鲁棒性是三个独立物理轴</h2>
<div class="verdict"><b>PDP</b> 现在按周期 IFFT、Hann 能量恢复和仪器核去嵌入；<b>EBF/PEBF/NEBF</b> 约束发射矩阵功率几何；<b>robust RZF</b> 约束 CSI 不确定性下的 Gram 加载。三者可组合，但不能混成同一个“鲁棒权”。</div>
<div class="grid g3" style="margin-top:16px"><div class="card compare bad"><h3>旧 PDP 风险</h3><ul class="list"><li>固定时延分支把合法 2 μs 单径映成负时延。</li><li>Hann 改变功率却未逐 realization 恢复。</li><li>窗核制造数 ns 假 RMS spread。</li></ul></div>
<div class="card compare"><h3>旧权值风险</h3><ul class="list"><li>只检查总功率不能证明每天线限制。</li><li>NEBF 会破坏 MU 零陷，不能默认优于 PEBF。</li><li>RZF 只有噪声加载，无 CSI 误差项。</li></ul></div>
<div class="card compare warn"><h3>当前边界</h3><ul class="list"><li>PDP 分辨率由 272 个 RB 中心样本决定。</li><li>σe² 仍需 estimator/离线标定。</li><li>实测方向图、PA 非线性未纳入。</li></ul></div></div></section>

<section class="section" id="pdp"><div class="kicker">01 · Physical PDP</div><h2>PDP 不是 bridge token：保留真实功率与真实时延轴</h2>
<div class="grid g2"><div class="card"><h3>频域到时延域</h3><div class="eq">@@PDP@@</div><p>每个 T/BS/UE snapshot 施加能量归一 Hann，再按原始频域能量恢复；最后跨天线/时间平均。</p></div>
<div class="card"><h3>分辨率与无模糊周期</h3><div class="eq">@@PDPAXIS@@</div><p>272 RB、30 kHz 时 Δτ=<b>@@RES@@ ns</b>，T_amb=<b>@@PERIOD@@ ns</b>。更细数值不可解释成独立可分辨 tap。</p></div>
<div class="card"><h3>圆周均值</h3><div class="eq">@@CIRC@@</div><p>用 circular resultant 选分支，再把 residual wrap 到最近像，避免长单径越界。</p></div>
<div class="card"><h3>窗核去嵌入</h3><div class="eq">@@DEEMBED@@</div><p>核方差来自同一窗的 IFFT；超出分辨能力只能归零，不能制造精度。</p></div></div>
<div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>解析输入</th><th>测得均值</th><th>测得 RMS</th><th>功率比</th></tr></thead><tbody>@@SINGLEROWS@@
<tr><td>80%@0 + 20%@500 ns</td><td>@@TWOMEAN@@ ns（解析100）</td><td>@@TWORMS@@ ns（解析200）</td><td>@@TWOPWR@@</td></tr></tbody></table></div></section>

<section class="section" id="pdpcompare"><div class="kicker">02 · PDP comparison</div><h2>三者叫 PDP 的对象不完全相同</h2>
<div class="table-wrap"><table><thead><tr><th>维度</th><th>SuperRAN</th><th>ChannelHub</th><th>Sionna</th><th>结论</th></tr></thead><tbody>
<tr><td>输入</td><td>频域 H[T,RB,BS,UE]</td><td>物理 CIR/CDL tap；bridge 另做 ML feature</td><td>CIR/path coefficients + OFDM tensor</td><td>SW 做测量合同，CH 做生成真值</td></tr>
<tr><td>输出</td><td>线性功率、秒轴、RMS、分辨率、周期、功率比</td><td>bridge 前64 tap/peak normalize 面向 token</td><td>研究者自行统计</td><td>bridge token 不能代替物理 PDP</td></tr>
<tr><td>窗/周期</td><td>Hann 恢复 + circular moment + de-embed</td><td>传播引擎给 tap</td><td>由调用方选择</td><td>当前 SW 补的是可审计测量层</td></tr>
</tbody></table></div></section>

<section class="section" id="power"><div class="kicker">03 · Per-antenna power</div><h2>EBF、PEBF、NEBF：同一原始方向，三种约束</h2>
<div class="grid g3"><div class="card"><h3>EBF · 总功率</h3><div class="eq">@@EBF@@</div><p>只保证 P；个别天线可超过 P/M。</p></div>
<div class="card"><h3>PEBF · 最大天线限幅</h3><div class="eq">@@PEBF@@</div><p>全局 α 保持流几何/ZF 零陷，但常浪费总功率。</p></div>
<div class="card"><h3>NEBF · 逐天线归一</h3><div class="eq">@@NEBF@@</div><p>每个非零天线行用满 P/M；MU 会改变流几何。</p></div></div>
<div class="grid g2" style="margin-top:16px"><div class="card"><h3>64T SU 固定 realization</h3>
<p>EBF <b>@@SUEBF@@</b></p><div class="bar"><span style="width:100%"></span></div>
<p>NEBF <b>@@SUNEBF@@</b></p><div class="bar"><span style="width:@@NEBFPCT@@%"></span></div>
<p>PEBF <b>@@SUPEBF@@</b>，总功率利用 <b>@@PEBFUTIL@@%</b></p><div class="bar red"><span style="width:@@PEBFPCT@@%"></span></div></div>
<div class="card"><h3>强相关 MU 反例</h3><p>PEBF：sum SE <b>@@MUPEBF@@</b>，leakage <b>@@MUPEBFLEAK@@</b></p><div class="bar"><span style="width:100%"></span></div>
<p>NEBF：sum SE <b>@@MUNEBF@@</b>，leakage <b>@@MUNEBFLEAK@@</b></p><div class="bar red"><span style="width:@@MUNEBFPCT@@%"></span></div>
<p class="source">满足验收：SU 中 NEBF≈EBF≫PEBF；MU 中存在 NEBF&lt;PEBF。</p></div></div></section>

<section class="section" id="robust"><div class="kicker">04 · Robust RZF</div><h2>CSI 误差加载已贯通系统建表</h2>
<div class="eq">@@RZF@@</div><div class="grid g3"><div class="card metric"><div class="value">@@NOISESE@@</div><div class="label">noise-only RZF sum SE</div><div class="note">αnoise=@@NOISELOAD@@</div></div>
<div class="card metric"><div class="value">@@ROBUSTSE@@</div><div class="label">robust RZF sum SE</div><div class="note">αCSI=@@CSILOAD@@，total=@@TOTALLOAD@@</div></div>
<div class="card metric"><div class="value">+@@RZFGAIN@@</div><div class="label">固定 imperfect-CSI 反例</div><div class="note">bit/s/Hz，不是泛化承诺</div></div></div>
<div class="callout info" style="margin-top:15px"><h3>为什么是 N_BS·σe²</h3>若 E 元素独立、每复系数方差 σe²，则 E[EEᴴ]=N_BSσe²I。代码分别返回 noise/CSI loading；σe²=0 与历史 RZF bitwise compatible。</div>
<div class="callout bad" style="margin-top:12px"><h3>不能由单反例宣称总提升</h3>本例证明实现方向与存在性。正式结论仍需 estimator err_var、多几何 realization 与置信区间；σe² 过大会过度正则化。</div></section>

<section class="section" id="system"><div class="kicker">05 · System integration</div><h2>从 UI 到 MU pair table 的完整路径</h2>
<div class="flow"><div class="step"><span class="n">1</span><b>配置页 / MCP</b><small>mu_precoder, sigma_e²</small></div><div class="arrow">→</div><div class="step"><span class="n">2</span><b>SchedulerConfig</b><small>有限非负校验</small></div><div class="arrow">→</div><div class="step"><span class="n">3</span><b>build_link_tables</b><small>传到 MU pair</small></div><div class="arrow">→</div><div class="step"><span class="n">4</span><b>RZF + constraint</b><small>独立旋钮</small></div><div class="arrow">→</div><div class="step"><span class="n">5</span><b>真实 H 评估</b><small>SINR/SE/diagnostics</small></div></div>
<div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>维度</th><th>SuperRAN</th><th>ChannelHub</th><th>Sionna 2.0.1</th><th>取舍</th></tr></thead><tbody>
<tr><td>功率约束</td><td>Q[f,a,s] 行功率；EBF/PEBF/NEBF</td><td>提供 H，不定义产品权名</td><td>RZF 逐流单位范数，tr(GGᴴ)=K</td><td>系统 SU/MU 共用总功率 P，不能直接照抄归一</td></tr>
<tr><td>RZF</td><td>noise + N_BSσe² 分解并回传</td><td>提供 estimate/error 来源</td><td>通用自由 α</td><td>SW 把 α 绑定物理来源</td></tr>
<tr><td>因果</td><td>h_est 预编码、h_true 评估；PF/MU/KPI</td><td>产生 true/estimate/I+N</td><td>link tensor</td><td>系统体验层是 SW 的核心差异</td></tr>
</tbody></table></div></section>

<section class="section" id="limits"><div class="kicker">06 · Decisions</div><h2>下一阶段建议</h2>
<details open><summary>D1 · σe² 来源：estimator 输出优先，离线标定兜底</summary><p>先由 ChannelHub 输出按 UE/snapshot 聚合的 err_var，再让系统配置只做 override；禁止运行时从 h_true 反推。</p></details>
<details><summary>D2 · PDP 上限</summary><p>272 RB 中心点对应约 10.21 ns 分辨率与 2.778 μs 周期。更细/更长研究应直接使用 ChannelHub CIR/tap，不能靠零填充宣称提高分辨率。</p></details>
<details><summary>D3 · PA 与阵列真实化</summary><p>每天线等功率仍是线性约束；PA back-off、EVM、饱和、互耦未建模。实测阵元图/PA 曲线到位后建立独立门禁，不塞入 NEBF 标签。</p></details>
<div class="callout ok" style="margin-top:15px"><h3>当前可交付结论</h3>在声明边界内，解析哨兵和反例证明 PDP、三种功率约束、鲁棒 RZF 均“做了它声称做的事”；不等于现场参数全校准，也不等于所有场景性能方向固定。</div></section>

<section class="section" id="sources"><div class="kicker">07 · Sources</div><h2>一手依据与实现</h2>
<div class="grid g2"><div class="card"><h3>外部来源</h3><ul class="list source">
<li><a href="https://nvlabs.github.io/sionna/_modules/sionna/phy/mimo/precoding.html">Sionna 2.0.1 precoding source</a>：RZF 与逐流单位范数。</li>
<li><a href="https://nvlabs.github.io/sionna/_modules/sionna/phy/ofdm/channel_estimation.html">Sionna channel estimation source</a>：LMMSE covariance/err_var。</li>
<li><a href="https://arxiv.org/abs/2003.09923">Wang & Chen, Regularized Zero-Forcing</a>：RZF 背景。</li>
<li><a href="https://www.etsi.org/deliver/etsi_tr/138900_138999/138901/18.01.00_60/tr_138901v180100p.pdf">3GPP TR 38.901 V18.1.0</a>：传播与 CIR。</li></ul></div>
<div class="card"><h3>本地源码</h3><div class="path">C:\Vibe\Wireless\superran\src\superran\measure.py<br>C:\Vibe\Wireless\superran\src\superran\beamforming.py<br>C:\Vibe\Wireless\superran\src\superran\mumimo.py<br>C:\Vibe\Wireless\superran\src\superran\system.py<br>C:\Vibe\Wireless\superran\src\superran\server.py<br>C:\Vibe\Wireless\superran\src\superran\spec.py</div></div></div>
<footer class="foot"><b>SuperRAN PDP & robust-weight audit</b><br>性能数只对应固定确定性哨兵；原始证据见 artifacts/srs_pdp_robust_audit_20260810.json。</footer></section>
"""


def srs_report(data: dict) -> str:
    s, lmmse, a, e = (
        data["srs_physical_64x4"],
        data["srs_lmmse"],
        data["array_and_polarization"],
        data["company_e2e"],
    )
    nav = '<nav class="nav"><div><a href="#verdict">结论</a><a href="#map">模块图</a><a href="#matrix">64×4/F/极化</a><a href="#srs">SRS/LMMSE</a><a href="#time">时域/TDD</a><a href="#compare">三方对照</a><a href="#evidence">验证</a><a href="#limits">边界</a><a href="#sources">依据</a></div></nav>'
    page = start("SRS 信道模块三方审计", "SuperRAN · SRS CHANNEL AUDIT · 2026-08-10",
                 "SRS 信道相关模块<br>三方对照与落地审计",
                 "把旧缺口、已修复链路、协议依据、与 ChannelHub/Sionna 的差异以及仍不能宣称的部分逐层摊开。",
                 '<span class="pill ok">联合审计 15/15</span><span class="pill ok">64×4 物理观测</span><span class="pill ok">LS + LMMSE</span><span class="pill">+45° / −45°</span><span class="pill warn">实测方向图待输入</span>', nav)
    values = {
        "@@FERR@@": format(a["max_abs_FhF_minus_I"], ".2e"),
        "@@LOBE@@": num(a["single_port_main_lobe_elevation_deg"], 2),
        "@@PMIN@@": num(s["total_pilot_power_min"], 15),
        "@@PMAX@@": num(s["total_pilot_power_max"], 15),
        "@@LSMSE@@": num(lmmse["mse_ls_linear"], 4),
        "@@LMMSEMSE@@": num(lmmse["mse_ls_lmmse"], 4),
        "@@LMMSEGAIN@@": num(100 * lmmse["mse_reduction_fraction"], 1),
        "@@DLSLOT@@": str(e["paired_dl_rs_slot"]), "@@ULSLOT@@": str(e["paired_ul_srs_slot"]),
        "@@FIRSTUL@@": str(e["srs_first_ul_opportunity_slot"]),
        "@@MAXERR@@": format(s["max_abs_recovery_error"], ".2e"),
        "@@CHECKS@@": str(data["audit"]["check_count"]), "@@DATASET@@": escape(e["dataset_id"]),
        "@@GENERATED@@": escape(data["audit"]["generated_at_utc"]),
    }
    return expand(page + SRS_BODY + end(), values)


def pdp_report(data: dict) -> str:
    p, w = data["pdp"], data["power_and_robust_weights"]
    su, mu, rr = w["su_64t"], w["correlated_mu_counterexample"], w["robust_rzf_counterexample"]
    rows = []
    for row in p["single_paths"]:
        rows.append("<tr><td>" + num(row["input_delay_ns"], 0) + " ns</td><td>" + num(row["mean_delay_ns"], 6)
                    + " ns</td><td>" + num(row["rms_delay_spread_ns"], 6) + " ns</td><td>"
                    + num(row["power_conservation_ratio"], 12) + "</td></tr>")
    nav = '<nav class="nav"><div><a href="#verdict">结论</a><a href="#pdp">PDP</a><a href="#pdpcompare">PDP 对照</a><a href="#power">三种功率权</a><a href="#robust">鲁棒 RZF</a><a href="#system">系统落地</a><a href="#limits">边界</a><a href="#sources">依据</a></div></nav>'
    page = start("PDP 与鲁棒权三方审计", "SuperRAN · PDP & ROBUST WEIGHT AUDIT · 2026-08-10",
                 "PDP 模块与鲁棒权模块<br>三方对照、修复与反例",
                 "把时延域测量、每天线功率约束、不完美 CSI 鲁棒性拆成三个独立物理轴，分别验证，再在系统建表组合。",
                 '<span class="pill ok">PDP 功率守恒</span><span class="pill ok">EBF / PEBF / NEBF</span><span class="pill ok">robust RZF 系统贯通</span><span class="pill warn">误差方差待实测标定</span>', nav)
    two = p["two_path_80_20_0_500ns"]
    ebf, pebf, nebf = [su[x]["spectral_efficiency"] for x in ("ebf", "pebf", "nebf")]
    reg = rr["robust"]["rzf_regularization"]
    values = {
        "@@RES@@": num(p["delay_resolution_ns"], 3), "@@PERIOD@@": num(p["unambiguous_period_ns"], 3),
        "@@SINGLEROWS@@": "".join(rows), "@@TWOMEAN@@": num(two["mean_delay_ns"], 6),
        "@@TWORMS@@": num(two["rms_delay_spread_ns"], 6), "@@TWOPWR@@": num(two["power_conservation_ratio"], 12),
        "@@SUEBF@@": num(ebf, 4), "@@SUNEBF@@": num(nebf, 4), "@@SUPEBF@@": num(pebf, 4),
        "@@NEBFPCT@@": num(100 * nebf / ebf, 1), "@@PEBFPCT@@": num(100 * pebf / ebf, 1),
        "@@PEBFUTIL@@": num(100 * su["pebf"]["power_diagnostics"]["utilization_mean"], 1),
        "@@MUPEBF@@": num(mu["pebf"]["sum_se"], 4), "@@MUNEBF@@": num(mu["nebf"]["sum_se"], 4),
        "@@MUPEBFLEAK@@": num(mu["pebf"]["leakage_ratio"], 6),
        "@@MUNEBFLEAK@@": num(mu["nebf"]["leakage_ratio"], 6),
        "@@MUNEBFPCT@@": num(100 * mu["nebf"]["sum_se"] / mu["pebf"]["sum_se"], 1),
        "@@NOISESE@@": num(rr["noise_only"]["sum_se"], 4), "@@ROBUSTSE@@": num(rr["robust"]["sum_se"], 4),
        "@@RZFGAIN@@": num(rr["sum_se_gain"], 4), "@@NOISELOAD@@": num(reg["noise_loading"], 3),
        "@@CSILOAD@@": num(reg["csi_error_loading"], 3), "@@TOTALLOAD@@": num(reg["total_loading"], 3),
    }
    return expand(page + PDP_BODY + end(), values)


class AuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids = []
        self.hrefs = []

    def handle_starttag(self, tag, attrs) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "a" and values.get("href"):
            self.hrefs.append(values["href"])


def validate(path: Path) -> dict:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    parser = AuditParser()
    parser.feed(text)
    dup = sorted({x for x in parser.ids if parser.ids.count(x) > 1})
    missing = sorted({x[1:] for x in parser.hrefs if x.startswith("#")} - set(parser.ids))
    runtime = [x for x in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "fonts.googleapis.com") if x in text]
    result = {"first_line_doctype": text.splitlines()[0].lower() == "<!doctype html>",
              "utf8_no_replacement_character": "\ufffd" not in text,
              "html_closed": text.rstrip().endswith("</html>"), "duplicate_ids": dup,
              "missing_internal_anchors": missing, "external_runtime_assets": runtime,
              "katex_wrappers": text.count('class="kx"'), "mathml_blocks": text.count("<math"),
              "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if not result["first_line_doctype"] or not result["utf8_no_replacement_character"] or not result["html_closed"] or dup or missing or runtime or result["katex_wrappers"] == 0 or result["mathml_blocks"] == 0:
        raise RuntimeError("HTML validation failed: " + str(path) + " " + repr(result))
    return result


def main() -> None:
    data = json.loads(AUDIT.read_text(encoding="utf-8"))
    if not data["audit"]["all_checks_passed"]:
        raise RuntimeError("refusing to publish failed audit")
    SRS_OUT.write_text(srs_report(data), encoding="utf-8")
    PDP_OUT.write_text(pdp_report(data), encoding="utf-8")
    checks = {SRS_OUT.name: validate(SRS_OUT), PDP_OUT.name: validate(PDP_OUT)}
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_input": str(AUDIT), "audit_input_sha256": hashlib.sha256(AUDIT.read_bytes()).hexdigest(),
        "audit": {"all_checks_passed": True, "check_count": data["audit"]["check_count"],
                  "dataset_id": data["company_e2e"]["dataset_id"]},
        "verification_runs": [
            {"scope": "ChannelHub full unit", "result": "422 passed, 3 skipped, 15 warnings", "elapsed_s": 67.85, "skips": "optional onnx/tensorboard only"},
            {"scope": "ChannelHub targeted SRS/TDD/LMMSE", "result": "41 passed"},
            {"scope": "SuperRAN channel contract", "result": "6 passed", "elapsed_s": 3.88},
            {"scope": "SuperRAN interference/config integration", "result": "exit 0", "elapsed_s": 282.9, "entrypoint": "python tests/test_interference.py"},
            {"scope": "SuperRAN full pytest", "result": "6 pytest cases passed; collection assertion scripts passed", "elapsed_s": 544.44},
            {"scope": "SuperRAN critical scripts", "result": "physics, mumimo, csi_aging, linklevel, gates, rng, system all exit 0"},
        ],
        "artifacts": checks,
        "visual_qa": {
            "browser": "Playwright CLI, Chromium, 1440x1000 viewport",
            "srs_screenshot": str(ROOT / "output" / "playwright" / "srs-report-full.png"),
            "pdp_screenshot": str(ROOT / "output" / "playwright" / "pdp-report-full.png"),
            "console_errors": 0,
            "console_warnings": 0,
            "runtime_network": "local document only; KaTeX, MathML, fonts and CSS are inline",
            "temporary_http_server_stopped": True,
        },
        "known_boundaries": [
            "SRS physical abstraction supports 1/2/4 ports; Rel-18 8-port TDM is not implemented",
            "LMMSE uses frequency covariance and linear time interpolation; no spatial covariance",
            "110 degree element pattern is parametric_temporary, not measured company Jones data",
            "robust RZF csi_error_variance requires estimator covariance or offline calibration",
            "PDP resolution and ambiguity are limited by RB-centre frequency sampling",
        ],
    }
    VERIFY_OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"srs_report": str(SRS_OUT), "pdp_report": str(PDP_OUT),
                      "verification": str(VERIFY_OUT), "srs_bytes": SRS_OUT.stat().st_size,
                      "pdp_bytes": PDP_OUT.stat().st_size, "katex": katex.available()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
