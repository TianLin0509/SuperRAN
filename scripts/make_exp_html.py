"""生成 EXPERIENCE_MODE.html：单小区 burst 业务 + 掐头去尾体验速率方案。

用户 2026-08-09 的口径：
* superran 分两种模式——谱效评估型（现状，full buffer）与体验评估型（新增）
* 体验评估型先做**单小区、不考虑重传**
* 顺序：业务模型 → EPF 优先级 → 按需 RBG 分配（恰够传完，大包吃全带）→ 掐头去尾体验速率
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import katex as kx  # noqa: E402
from superran import mathml as mm  # noqa: E402


def M(tex: str, *, block: bool = False) -> str:
    return kx.wrap(tex, mm.render(tex, block=block), display=block)


F_EPF = M(r"P_u(t) \;=\; \underbrace{w_u}_{\text{业务权重}} \cdot "
          r"\underbrace{\frac{\left[R_u^{\mathrm{inst}}(t)\right]^{\alpha}}"
          r"{\left[\bar{R}_u(t)\right]^{\beta}}}_{\text{广义 PF}} \cdot "
          r"\underbrace{\left(1 + \frac{D_u(t)}{D_u^{\mathrm{budget}}}\right)^{\gamma}}"
          r"_{\text{时延因子}}", block=True)
F_RAVG = M(r"\bar{R}_u(t{+}1) = \left(1-\tfrac{1}{T_c}\right)\bar{R}_u(t) "
           r"+ \tfrac{1}{T_c}\, R_u^{\mathrm{served}}(t)", block=True)
F_NEED = M(r"n_u^{\ast} = \min\Big\{\, n \in [1, 17] \;:\; "
           r"\mathrm{TBS}\big(n,\, m_u,\, r_u\big) \;\ge\; B_u \,\Big\}", block=True)
F_TBS = M(r"\mathrm{TBS}(n, m, r) \;\text{由}\; n_{RE} = n \cdot 16 \cdot 12 \cdot 12 "
          r"\;\text{经 38.214 §5.1.3.2 算出}", block=True)
F_THP = M(r"\mathrm{Thp}_{\mathrm{DL}} = \frac{V_{\mathrm{total}} - V_{\mathrm{last}}}"
          r"{T_{\mathrm{last}} - T_{0}}", block=True)
F_SE = M(r"R_u^{\mathrm{inst}}")
F_RS = M(r"R_u^{\mathrm{served}}")
F_ALPHA = M(r"\alpha = \beta = 1")
F_G0 = M(r"\gamma = 0")
F_PRB = M(r"\mathrm{PRB}_{\mathrm{util}} = \frac{\sum_t \sum_u n_u(t)}"
          r"{N_{\mathrm{DL\,TTI}} \cdot 17}", block=True)



# 表格里逐个用到的小公式。**必须提成常量**——
# f-string 里出现反斜杠在 Python < 3.12 是语法错误，
# 而本机是 3.12 所以跑得通、别的机器直接崩（CLAUDE.md 记过这条）。
F_W = M(r"w_u")
F_RINST = M(r"R_u^{\mathrm{inst}}")
F_RBAR = M(r"\bar R_u")
F_TC = M(r"T_c")
F_A = M(r"\alpha")
F_B = M(r"\beta")
F_D = M(r"D_u")
F_GAM = M(r"\gamma")
F_NSTAR = M(r"n^{\ast}")


def head() -> str:
    src = (ROOT / "TONIGHT.html").read_text(encoding="utf-8")
    h = src.split("</head>")[0]
    h = h.replace("superran 通宵成果与待审", "superran 体验评估模式方案")
    extra = """
<style>
  .mode{display:flex;gap:16px;margin:20px 0;flex-wrap:wrap}
  .mcard{flex:1;min-width:300px;border:2px solid var(--border);border-radius:14px;
         padding:0;overflow:hidden;background:var(--card)}
  .mcard.now{border-color:#8e8e93}
  .mcard.new{border-color:#0071e3}
  .mcard>h4{margin:0;padding:12px 18px;font-size:17px;border-bottom:1px solid var(--border)}
  .mcard.now>h4{background:#f5f5f7}
  .mcard.new>h4{background:#e8f2ff;color:#0071e3}
  .mcard>.mb{padding:6px 18px 14px;font-size:14.5px}
  .mcard table{font-size:13.5px}
  .step{border-left:3px solid #0071e3;padding:2px 0 2px 18px;margin:24px 0}
  .step>h3{margin:0 0 8px;font-size:19px}
  .step>h3 .sn{display:inline-block;width:26px;height:26px;line-height:26px;
      border-radius:8px;background:#0071e3;color:#fff;text-align:center;
      font-size:14px;margin-right:9px}
  .inv{background:#f0fff4;border-left:4px solid #34c759;padding:12px 18px;
       margin:10px 0;border-radius:0 8px 8px 0}
  .q{background:#fff8e1;border:1px solid #ffd54f;border-radius:10px;
     padding:14px 18px;margin:14px 0}
  .q b.qt{display:block;color:#c77700;margin-bottom:6px}
  code{font-size:.92em}
</style>"""
    return h + extra + "\n" + kx.head_assets() + "\n</head>"


INVARIANTS = [
    ("I-1 · 退化（最重要）",
     "<code>mode=\"se\"</code> 时全部 KPI 与今天<b>逐位相同</b>。"
     "体验模式是叠加上去的能力，不是把旧行为改掉——"
     "<b>这条保证今天所有的谱效结论不用重算</b>。"),
    ("I-2 · 单用户退化",
     "体验模式下若只有 1 个用户有数据且缓冲区够大，它必然拿到全部 17 个 RBG，"
     "TBS 与 BLER 与谱效模式<b>逐位相同</b>。"),
    ("I-3 · 资源守恒",
     "每个下行 TTI <code>Σ_u n_u(t) ≤ 17</code>，且实测 PRB 利用率与逐 TTI 累加值"
     "严格相等（整数运算，不允许误差）。"),
    ("I-4 · 字节守恒",
     "现有的 <code>accounting_error_pct &lt; 1%</code> 继续成立。"
     "<b>这条抓到过真 bug</b>（HARQ 重传漏计 served，差 4.5%），不能丢。"),
    ("I-5 · 恰够分配确实恰够",
     "对每个分到 <code>n &lt; 17</code> 个 RBG 的用户，断言 "
     "<code>TBS(n) ≥ B_u</code> 且 <code>TBS(n−1) &lt; B_u</code>。"
     "<b>少一个 RBG 就发不完</b>——这是「恰够」的定义，写成断言。"),
    ("I-6 · 小包容量必须上去",
     "纯小包话务下开 FDM 后，<b>每 TTI 平均服务用户数</b>必须明显大于 1，"
     "而 PRB 利用率仍远低于 1。<b>服务用户数没涨就是分配器没生效。</b>"),
    ("I-7 · 大包体验速率必须上升",
     "混合话务（大包 + 小包）下，大包用户的体验速率必须<b>高于</b>谱效模式——"
     "它不再被小包偷走整个 TTI。<b>这是这项工作最直接的收益，测不出来就是白做。</b>"),
    ("I-8 · 小包仍然测不到（盲区不可修）",
     "单 TTI 就发完的 burst 在掐尾下<b>仍然返回 None</b>。"
     "<b>如果改完突然测得到了，说明掐尾被改坏了</b>——"
     "这是 TS 28.552 口径的固有盲区，不是 bug。"),
    ("I-9 · 公平性口径自洽",
     "把 " + F_RS + " 换成全带口径（故意用错），小包用户的平均等待时间必须"
     "<b>明显变长</b>。这条是对「PF 平均速率必须用实发」那个坑的反向验证——"
     "<b>如果换了没区别，说明分配器根本没在按需分</b>。"),
]

OPEN = [
    ("EPF 的具体形式",
     "<b>EPF 不是 3GPP 标准算法，是厂商术语</b>，不同厂商的定义不一样。"
     "我给的参数化形式是「广义 PF × 业务权重 × 时延因子」，"
     "默认 " + F_ALPHA + "、" + F_G0 + " 退化成经典 PF。",
     "<b>你们现场的 EPF 具体是哪一套？</b>时延因子是乘性还是加性、"
     "用的是 HoL 时延还是平均时延、budget 从哪来（5QI 的 PDB？）"
     "——这些直接决定小包用户的调度顺序。"),
    ("尾料怎么处理",
     "按优先级逐个分「恰够」之后可能有剩（比如 3 个用户各要 2/3/5 个，"
     "还剩 7 个）。我的方案是<b>继续往下分给后面的用户；全部满足后仍有剩就留空</b>。",
     "留空的好处是 <b>PRB 利用率是真实测出来的</b>。"
     "但现网调度器常见的做法是把尾料补给第一名（提高单用户速率）。"
     "<b>你要哪种？</b>补给第一名会让 PRB 利用率恒等于 1，"
     "那这个指标就废了。"),
    ("不考虑重传时，误块的字节怎么办",
     "两条路：<b>（A）字节退回缓冲区，下个 TTI 当新数据重发</b>"
     "——保住字节守恒，BLER 仍然影响吞吐，但没有合并增益（偏悲观）；"
     "<b>（B）直接丢弃</b>——字节对不上账，现有的守恒断言会红。",
     "<b>我推荐 A</b>，因为它保住了项目已有的字节对账不变量，"
     "而那条抓到过真 bug。代价是相对真实 HARQ 偏悲观（缺合并增益），"
     "要在结果里写明。<b>你同意吗？</b>"),
    ("burst 的边界定义（P1 评审里那条，仍然悬着）",
     "现在一个 burst = 一个文件。TS 28.552 的 data burst 是"
     "<b>缓冲区连续非空的一段</b>。两个文件到达时间重叠时，"
     "现网话统算一个 burst，现在的代码算两个。",
     "<b>这条会改变体验速率的分母</b>。"
     "本次方案<b>先不动</b>（保持文件边界），"
     "但如果你确认现网是缓冲区忙期口径，那要在这一步一起改——"
     "越晚改，作废的历史数字越多。"),
    ("小包的体验替代指标",
     "小包在掐尾口径下<b>永远测不到体验速率</b>（单 TTI burst 分母为 0）。"
     "方案里加了<b>排队时延</b>（到达 → 首次被调度）与"
     "<b>完成时延</b>（到达 → 最后一个字节发出）的 p50/p95/p99。",
     "<b>这两个指标够不够？</b>还是你们现场用别的口径衡量小包体验"
     "（比如首包时延、或者按 TB 数折算的等效速率）？"),
]


def inv_html() -> str:
    return "".join(f'<div class="inv"><p><b>{n}</b> —— {d}</p></div>'
                   for n, d in INVARIANTS)


def open_html() -> str:
    return "".join(
        f'<div class="q"><b class="qt">待拍板 {i} · {n}</b>'
        f'<p>{what}</p><p><b>要你定：</b>{ask}</p></div>'
        for i, (n, what, ask) in enumerate(OPEN, 1))


def build() -> str:
    return f"""{head()}
<body>
<div class="wrap">

<h1>体验评估模式</h1>
<p class="tagline">单小区 · burst 业务 · EPF 排序 · 按需 RBG 分配 · 掐头去尾体验速率</p>
<p class="meta">2026-08-09 · 不考虑重传的第一步 · 待评审</p>

<div class="callout c-red">
<p><b>先说一条会咬人的：PF 的平均速率更新口径必须一起改，否则这项工作会适得其反。</b></p>
<p>现在 <code>system.py</code> 里 {F_RAVG} 的 {F_RS} 用的是
<code>best_se[snap]</code>——<b>全带谱效</b>。今天没问题，因为每个被调度的用户
确实拿全带。<b>但按需分配一上来就错了</b>：只分到 1 个 RBG 的用户，
{F_SE} 会按全带记账，它的 PF 度量掉得快 <b>17 倍</b>，
于是被饿死——<b>方向正好和这项工作的目的相反</b>。</p>
<p>必须改成<b>实际发出去的字节</b>折算的速率。这一条我写成了硬不变量 I-9，
并且做成<b>反向验证</b>：故意换回全带口径时，小包用户的等待时间必须明显变长。</p>
</div>

<div class="toc">
<strong>目录</strong>
<ol>
<li><a href="#m">两种模式：为什么是模式不是参数</a></li>
<li><a href="#s1">① 业务模型</a></li>
<li><a href="#s2">② EPF 优先级</a></li>
<li><a href="#s3">③ 按需 RBG 分配</a></li>
<li><a href="#s4">④ 掐头去尾体验速率</a></li>
<li><a href="#i">硬不变量</a></li>
<li><a href="#q">待你拍板的 5 个点</a></li>
</ol>
</div>

<h2 id="m">一、两种模式：为什么是模式不是参数</h2>

<p class="lead">你说的这个切分我认为是对的，而且<b>应该做成一等公民的模式开关，
不是又一个布尔参数</b>——因为两种模式下<b>连该报哪些 KPI 都不一样</b>。</p>

<div class="mode">
<div class="mcard now"><h4>谱效评估型 <code>mode="se"</code>（现状）</h4><div class="mb">
<div class="tbl-wrap"><table><tbody>
<tr><td style="width:78px"><b>业务</b></td><td>full buffer，缓冲区永不空</td></tr>
<tr><td><b>频域</b></td><td>一个 TTI 一个用户，占满全带</td></tr>
<tr><td><b>问的问题</b></td><td>这个信道 / 这套算法能跑多快</td></tr>
<tr><td><b>KPI</b></td><td>谱效、吞吐、rank、MCS、BLER、Jain 公平度</td></tr>
<tr><td><b>不报</b></td><td><b>体验速率</b>——缓冲区永不空，掐尾口径下
一个完整 burst 都找不到，实测 <code>measured_bursts = 0</code></td></tr>
</tbody></table></div></div></div>

<div class="mcard new"><h4>体验评估型 <code>mode="experience"</code>（新增）</h4><div class="mb">
<div class="tbl-wrap"><table><tbody>
<tr><td style="width:78px"><b>业务</b></td><td>burst 到达，有大有小</td></tr>
<tr><td><b>频域</b></td><td><b>按需分 RBG</b>，恰够传完；大包吃全带</td></tr>
<tr><td><b>问的问题</b></td><td>这个小区里的用户<b>实际体验</b>到多快</td></tr>
<tr><td><b>KPI</b></td><td>掐头去尾体验速率（大 / 小包分开）、
<b>真实</b> PRB 利用率、排队与完成时延分布、每 TTI 服务用户数</td></tr>
<tr><td><b>本次范围</b></td><td><b>单小区 · 仅 SU · 不考虑重传</b></td></tr>
</tbody></table></div></div></div>
</div>

<div class="callout c-blue">
<p><b>做成模式的三条实际好处。</b></p>
<p><b>①</b> KPI 可以按模式裁剪。今天 full buffer 下也会去算体验速率，
然后报一个 <code>0.0</code> 加一条 note——<b>那本来就不该被算</b>。</p>
<p><b>②</b> 校验可以按模式收紧。谱效模式下「一个 TTI 一个人」是<b>正确行为</b>，
体验模式下它是<b>故障</b>（说明分配器没生效）。同一套断言分不了这两种情况。</p>
<p><b>③</b> 说明书和 skill 可以按模式给不同的引导，用户不用先学会哪些参数在哪种模式下有意义。</p>
</div>

<h2 id="steps">二、四个环节</h2>

<div class="step" id="s1">
<h3><span class="sn">1</span>业务模型</h3>
<p>沿用现有的 <code>_Traffic</code>（burst 到达 + 单活跃 burst + FIFO 队列），
<b>只补一件事：包大小分布要能表达现网画像</b>。</p>
<div class="tbl-wrap"><table>
<thead><tr><th style="min-width:110px">模型</th><th>参数</th><th>用在哪</th></tr></thead>
<tbody>
<tr><td><code>ftp3</code></td><td>文件大小 + 泊松到达率</td>
<td>3GPP 评价体验速率的标准话务（TR 36.814 A.2.1.3.1）</td></tr>
<tr><td><code>bimodal</code></td><td>小包占比 / 满包占比 / 中间段分布</td>
<td>你给的现网画像：两头高中间低</td></tr>
<tr><td><b><code>mixed</code>（新增）</b></td>
<td>按比例混合若干个「业务类型」，每类有自己的包大小分布与到达率</td>
<td><b>本方案的主力</b>——要同时看大包和小包的体验，
就得让它们<b>在同一个小区里竞争</b></td></tr>
</tbody></table></div>
<div class="callout c-amber">
<p><b>为什么要 <code>mixed</code>：单一话务下这项工作没有可测的收益。</b>
全是大包 ⇒ 每个用户都要全带，按需分配退化成今天的行为；
全是小包 ⇒ 谁都测不到体验速率。<b>只有大小混跑，
「小包不再偷走整个 TTI」这个收益才显现出来</b>——这正是 I-7 要断言的东西。</p>
</div>
<p class="src"><b>本次不做</b>：IP 包层与分段。粒度仍是 burst。
逐包时延要等 P1-A 的包模型，本方案只出<b>逐 burst</b> 的排队与完成时延。</p>
</div>

<div class="step" id="s2">
<h3><span class="sn">2</span>EPF 优先级</h3>
<p>先算优先级排序，<b>再</b>按这个顺序分 RBG。两步分开是关键——
排序用<b>全带</b>口径（分配前还不知道给几个 RBG），分配用实际需求。</p>
{F_EPF}
<div class="tbl-wrap"><table>
<thead><tr><th style="min-width:70px">因子</th><th>含义</th><th>默认</th></tr></thead>
<tbody>
<tr><td>{F_W}</td><td>业务权重（5QI / QCI 映射）</td><td>1（本次不分业务）</td></tr>
<tr><td>{F_RINST}</td>
<td>本 TTI 若被调度能达到的速率，用<b>全带</b>估
（<code>best_se_gnb</code>，基站视角，不能用真实值）</td><td>—</td></tr>
<tr><td>{F_RBAR}</td><td>指数窗平均<b>实发</b>速率</td>
<td>窗长 {F_TC} = 100 TTI</td></tr>
<tr><td>{F_A}</td><td>信道质量指数</td><td>1</td></tr>
<tr><td>{F_B}</td><td>公平性指数。0 → max-C/I，1 → 经典 PF</td><td>1</td></tr>
<tr><td>{F_D}</td><td>队头（HoL）等待时延</td><td>—</td></tr>
<tr><td>{F_GAM}</td><td>时延因子指数。<b>0 时整个退化成经典 PF</b></td>
<td><b>0</b>（先跑通再开）</td></tr>
</tbody></table></div>
<div class="callout c-red">
<p><b>平均速率必须用实发，这是本方案最容易写错的一处。</b></p>
{F_RAVG}
<p>{F_RS} 必须是<b>这个 TTI 实际发出去的字节</b>折算的速率，
<b>不是</b> <code>best_se[snap]</code>（全带谱效）。
现在的代码用的是后者——今天正确（人人拿全带），
<b>按需分配之后就是错的</b>：只拿到 1/17 带宽的用户被按全带记账，
度量掉得快 17 倍，直接饿死。</p>
<p class="src">顺带一个「恰够分配」的<b>副作用是好的</b>：因为按需分配几乎没有填充，
「实发字节」与「消耗资源」在数值上几乎一致，
PF 的两种记账口径（按交付量 vs 按占用资源）在这里自然收敛，不用纠结选哪个。</p>
</div>
</div>

<div class="step" id="s3">
<h3><span class="sn">3</span>按需 RBG 分配</h3>
<p>核心就一句：<b>按优先级顺序，每个用户分到恰好能把它缓冲区清空的 RBG 数</b>；
不够分时给剩下的全部，下个 TTI 接着发。</p>
{F_NEED}
<pre><code>free = 17
alloc = {{}}
for u in EPF 降序:
    if free == 0: break
    n_star = need_table[mcs_u][rank_u].searchsorted(B_u) + 1   # O(log 17)
    n = min(n_star, free)
    alloc[u] = n
    free -= n
# free &gt; 0 说明所有人都满足了 —— 留空，PRB 利用率如实反映</code></pre>
<p><b>「大包用户依然允许其使用全带」是自动成立的</b>：
它的 {F_NSTAR} 会被钳到 17，只要它排第一就拿走整band。
不需要为它写特例。</p>

<h4>反查表：为什么不能用除法</h4>
<p>TBS 对 RBG 数<b>不是线性的</b>——有量化和码块分割。我实测过：
MCS12/rank2 下 1 个 RBG 是 1729 B，×17 = 29393 B，
而 17 个 RBG 实际是 <b>29722 B</b>，<b>差 +1.1%</b>。
用除法算「要几个 RBG」，<b>1% 的误差就可能少给一个 RBG，导致这一次发不完</b>。</p>
{F_TBS}
<div class="inv"><p><b>好消息：TBS 对 RBG 数严格单调递增</b>
（28 档 MCS × rank 1/2/4 全扫过，无一处持平或下降），
所以 <code>np.searchsorted</code> 的反查是<b>精确</b>的，不是近似。
表大小 28 × 4 × 17 = <b>1904 个 int</b>，第一相尾巴上建一次，常驻内存。</p></div>

<h4>MCS 与 rank 在分配之前就定好</h4>
<p>因为<b>没有子带 CQI</b>（你明确否掉了），MCS 与 rank 由全带 SINR 决定，
<b>与分到哪几个 RBG 无关</b>。所以顺序是：查表得 MCS/rank → 算需求 → 分 RBG。
<b>反过来（先分 RBG 再定 MCS）就等于偷偷做了频选调度。</b></p>

<h4>分到的 RBG 上的真实 SINR</h4>
<p>判误码时<b>应当</b>用实际分到的那几个 RBG 上的 SINR，而不是全带平均——
分到 3 个 RBG 的用户运气不好抽到 3 个深衰的，误码率就该高于全带平均。</p>
<p><b>但这一项本方案暂不做</b>，理由是：本次<b>不考虑重传</b>，
BLER 的作用被削弱；而逐 RBG SINR 会让 <code>UeLinkTable</code>
多一个维度，和 P1-A 的改动撞在同一个数据结构上。
<b>先把分配器和体验速率跑通，逐 RBG SINR 放到 P1-A 一起做</b>——
本次统一用全带 SINR 判误码，并<b>在结果里显式标注这个近似</b>。</p>
</div>

<div class="step" id="s4">
<h3><span class="sn">4</span>掐头去尾体验速率</h3>
<p>这一层<b>已经实现了</b>（<code>_burst_throughput_mbps</code>），本次不改公式，
只是终于有了合适的输入。</p>
{F_THP}
<div class="tbl-wrap"><table>
<thead><tr><th style="min-width:92px">口径</th><th>分子</th><th>分母起点</th><th>出处</th></tr></thead>
<tbody>
<tr><td><code>none</code></td><td>全部字节</td><td>数据到达</td>
<td>不掐，<b>数值虚高，不建议</b></td></tr>
<tr><td><code>tail</code></td><td>扣掉清空缓冲区那一片的字节</td><td>数据到达</td>
<td><b>3GPP TS 28.552 §5.1.1.3</b></td></tr>
<tr><td><code>head_tail</code></td><td>同上</td><td><b>首次被调度</b></td>
<td>运营商话统常用，<b>不是标准口径</b>——排队等调度的时间不计入</td></tr>
</tbody></table></div>
<div class="callout c-amber">
<p><b>掐尾为什么必要，以及它必然带来的盲区。</b>
清空缓冲区的那个 TTI 通常只用了一部分就发完了，
把它算进去等于<b>用「半个 TTI 的时间」去除「半个 TTI 的数据」</b>，
得到一个虚高的瞬时速率。</p>
<p>代价是：<b>单个 TTI 就发完的 burst 分母为 0，完全无法测量</b>。
小包永远落在这个盲区里——<b>这不是 bug，是这个 KPI 的固有性质</b>，
I-8 专门钉住它「不许被修好」。</p>
</div>

<h4>因此小包要另外的指标</h4>
<div class="tbl-wrap"><table>
<thead><tr><th style="min-width:118px">新增 KPI</th><th>定义</th><th>为什么</th></tr></thead>
<tbody>
<tr><td><b>排队时延</b></td><td>到达 → 首次被调度，p50 / p95 / p99</td>
<td>小包的体验就是「等了多久才轮到我」</td></tr>
<tr><td><b>完成时延</b></td><td>到达 → 最后一个字节发出，p50 / p95 / p99</td>
<td>端到端感知</td></tr>
<tr><td><b>真实 PRB 利用率</b></td><td>{F_PRB}</td>
<td><b>今天这个数是解析式算出来印上去的，不是仿真出来的</b>。
本方案第一次让它变成实测值</td></tr>
<tr><td><b>每 TTI 服务用户数</b></td><td>均值与分布</td>
<td>分配器有没有真的生效，看这一个数就够（I-6）</td></tr>
</tbody></table></div>
<p class="src"><code>system.py</code> 里那条 note 现在写着「小包的体验速率测不出来……
要看小包体验请看调度时延分布」——<b>而调度时延分布并不存在</b>。
本方案补上之后这条 note 才不是空头支票。</p>
</div>

<h2 id="i">三、硬不变量</h2>
<p class="lead">照项目「零时延必须逐位退化」的文化写。
<b>I-1 和 I-9 是两条最重要的</b>：前者保证不破坏现有结论，后者防那个会让工作适得其反的坑。</p>
{inv_html()}

<h2 id="q">四、待你拍板的 5 个点</h2>
{open_html()}

<div class="callout c-blue">
<p><b>工作量与边界。</b>估计 <code>system.py</code> ~320 行（模式开关、
<code>mixed</code> 话务、EPF、分配器、TBS 反查表、新 KPI）、
<code>server.py</code> ~40 行、<code>spec.py</code> 的 <code>_SIM_DEFAULTS</code> 同步 ~10 行、
测试 ~160 行。<b>合计约 530 行。</b></p>
<p><b>本次明确不做</b>：重传与 HARQ 时序（P1-B）、IP 包层与分段、
逐 RBG SINR（P1-A）、多小区（P1-C）、MU 配对（体验模式下先只跑 SU）。</p>
</div>

<footer>superran 体验评估模式方案 · 待评审 ·
公式由内联 KaTeX 排版（MathML 兜底），离线可用</footer>
</div>
{kx.upgrade_script()}
</body></html>
"""


def main() -> int:
    out = ROOT / "EXPERIENCE_MODE.html"
    out.write_text(build(), encoding="utf-8")
    print(f"{out}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
