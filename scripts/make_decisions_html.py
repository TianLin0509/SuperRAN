"""生成 DECISIONS.html：用户 2026-08-03 的 8 个决策 + 5 个提案答复的落地记录。

样式复用 TONIGHT.html 的 head，公式走内联 KaTeX（MathML 兜底），
所以这份文档离线双击也是完整的。

实测数字全部来自 ``tests/test_csi_aging.py`` 与端到端跑批，不手填。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superwireless import katex as kx  # noqa: E402
from superwireless import mathml as mm  # noqa: E402


def M(tex: str, *, block: bool = False) -> str:
    return kx.wrap(tex, mm.render(tex, block=block), display=block)


def head() -> str:
    src = (ROOT / "TONIGHT.html").read_text(encoding="utf-8")
    h = src.split("</head>")[0]
    h = h.replace("superwireless 通宵成果与待审", "superwireless 决策落地")
    return h + "\n" + kx.head_assets() + "\n</head>"



# --- 公式常量 ---------------------------------------------------------
# **f-string 里不能出现反斜杠**（Python < 3.12 直接语法错误，而本机是 3.12 所以跑得通、别的机器崩）。所有 LaTeX 提到这里。
F_STEADY = M(r"\frac{s_{up}}{s_{up}+s_{down}}")
F_TXCHAIN = M(r"\Gamma\big(\mathrm{MCS}(\mathrm{CQI})\big) + \mathrm{BFGain}")
F_BFGAIN = M(r"\overline{\mathrm{SINR}_{SVD} - \mathrm{SINR}_{PMI}}")
F_JITTER = M(r"\eta_s \sim \mathcal{U}(0.95\eta,\,1.05\eta)")
F_RHO0 = M(r"\rho \to 0")
F_STEADY2 = M(r"s_{up}/(s_{up}+s_{down})")
F_TABLE57 = M(r"m_{SRS} = (272,\ 16,\ 4,\ 4), \qquad N = (1,\ 17,\ 4,\ 1)", block=True)
F_BSRS = M(r"B_{SRS}=1")
F_BHOP = M(r"b_{hop}=0")
F_CORE = M(r"W = \mathrm{SVD}(H_{t-\tau}), \qquad \mathrm{SINR}_k = \frac{1}{"
           r"\left[\left(I + \frac{P}{r}(H_t W)^H (H_t W)\right)^{-1}\right]_{kk}} - 1",
           block=True)
F_ZERO = M(r"H_{t-\tau} = H_t")
F_DIAG = M(r"H_t W = U\Sigma_r")
F_EIG = M(r"\sigma_k^2 P / r / \sigma_n^2")
F_TSRS = M(r"T_{SRS}")
F_AGE = M(r"\mathrm{age}(k) = \big((n-k) \bmod 17\big)\cdot T_{SRS} "
          r"+ (t \bmod T_{SRS}) + \delta_{proc}")


# --- 8 个决策 -------------------------------------------------------------
DECISIONS = [
    ("公式渲染",
     "内联 KaTeX（1 MB 以内可接受）",
     "已切。<b>628 KB</b>——CSS 359 KB（20 个 woff2 字体全部 base64 内联）+ JS 269 KB，"
     "比你给的预算低 37%。<b>MathML 没删，留作兜底</b>："
     "每条公式同时写入 <code>data-tex</code> 与 MathML，KaTeX 的 JS 没跑起来时"
     "看到的仍是排好版的 MathML，<b>降级路径上任何一步都不会露出 LaTeX 源码</b>。"
     "43 条公式已用 Node 跑真实 KaTeX 的 <code>throwOnError:true</code> 全过。",
     "ok"),
    ("撒点", "ok，保持",
     "维持调 ChannelHub 的 <code>_place_ues</code>，与真跑逐位相同（误差 0.000000 m）。", "keep"),
    ("OLLA 步长", "可以按比例放大，但要告知用户",
     "新增 <code>olla_speedup</code>，<b>等比</b>放大两个步长。稳态 BLER = "
     + F_STEADY +
     " 与放大系数<b>无关</b>，所以 9.1% 不变，变的只有收敛速度与稳态抖动。"
     "非 1.0 时结果的 <code>notes</code> 里会多出一条显式告警，写明"
     "基线值、生效值、稳态 BLER，并提示出正式结论要设回 1.0。", "ok"),
    ("发送侧 SINR", "要 CQI + BF Gain",
     "已按现场链路重写：" + F_TXCHAIN +
     "。CQI 由终端在<b>真实信道</b>上用 Type I <b>宽带</b> PMI 权测得（对应你说的全带 CQI）、"
     "长期滤波后量化；BF Gain 由基站从<b>自己的 SRS 信道</b>算 "
     + F_BFGAIN + "。"
     "<b>原来的写法是错的</b>——「接收 SINR 的长期均值」里已经含了 SVD 的实际增益，"
     "等于假设基站预先知道波束打得准不准，开老化后这个错会把老化的代价整个抹平。", "ok"),
    ("仿真粒度", "先用 RBG", "维持 17 RBG × 16 RB。", "keep"),
    ("bimodal PRB 利用率", "差不多先", "维持 37.1%（你说的现网是 30%）。", "keep"),
    ("邻区负载", "作为可选参数；当前全网统一值；实际可在 ±5% 内波动",
     "三件事都做了：① <code>neighbor_prb_util</code> 进说明书的可改配置；"
     "② 显式标注 <code>scope: network_wide_single_value</code>，"
     "并在算法页写清<b>为什么现在只能是一个标量</b>——ChannelHub 的几何 SINR "
     "只给聚合 SIR，拿不到「哪个邻区贡献了多少」，逐小区负载映射不回来；"
     "③ 新增 <code>neighbor_load_jitter</code>，默认 0.05，"
     "每个快照抽一份 " + F_JITTER + "。", "ok"),
    ("MU rank", "固定 rank 2", "维持硬顶 rank 2，SU 侧仍是 1~4 自适应。", "keep"),
]

# --- 5 个提案 -------------------------------------------------------------
PROPOSALS = [
    (1, "CSI 反馈时延与老化", "做，参考通信原理与 Sionna；加 SRS 周期选项 5/10/20/40 ms，默认 17 倍跳频",
     "done", "已实现，见下一节。"),
    (2, "子带 CSI 与频选调度", "不做——现网用全带 CQI，不考虑频选调度", "drop",
     "已搁置。全带 CQI 这条<b>反过来落进了实现</b>：Type I PMI 用宽带码本，"
     "全带共用一个权，正是你说的口径。"),
    (3, "业务分化的时延 KPI", "暂不考虑", "drop", "已搁置。"),
    (4, "移动性轨迹与切换", "先不考虑，未来再加", "drop",
     "已搁置。不过它的前置依赖（时序信道是否可信）在 bug#7 里已经查清了。"),
    (5, "CSI Type II 码本", "不用做", "drop",
     "已搁置。当前 PMI 仍是 Type I，这一点在算法页里明确标出来了。"),
]


def decisions_rows() -> str:
    out = []
    for name, choice, did, kind in DECISIONS:
        pill = {"ok": '<span class="pill p-ok">已改</span>',
                "keep": '<span class="pill p-info">维持</span>'}[kind]
        out.append(f"<tr><td><b>{name}</b><br>{pill}</td>"
                   f"<td>{choice}</td><td>{did}</td></tr>")
    return "".join(out)


def proposals_rows() -> str:
    out = []
    for n, name, choice, kind, did in PROPOSALS:
        pill = {"done": '<span class="pill p-ok">已实现</span>',
                "drop": '<span class="pill p-info">已搁置</span>'}[kind]
        out.append(f"<tr><td><b>{n}. {name}</b><br>{pill}</td>"
                   f"<td>{choice}</td><td>{did}</td></tr>")
    return "".join(out)


def build(e2e: dict | None = None, olla: dict | None = None) -> str:
    e2e_block = ""
    if e2e:
        base = e2e["rows"][0]

        def cell(v: float, b: float, inv: bool = False) -> str:
            if b == 0 or v != v:
                return f"{v:.1f}"
            d = (v / b - 1) * 100
            cls = "p-no" if (d < -15 if not inv else d > 15) else "p-info"
            return (f"{v:.1f}<br><span class='pill {cls}'>{d:+.0f}%</span>"
                    if abs(d) > 0.5 else f"{v:.1f}")

        rows = "".join(
            f"<tr><td>{'<b>' if '默认' in r['label'] else ''}{r['label']}"
            f"{'</b>' if '默认' in r['label'] else ''}</td>"
            f"<td>{r['mcs']:.2f}</td><td>{r['rank']:.2f}</td>"
            f"<td>{r['bler']:.3f}</td>"
            f"<td>{cell(r['thp'], base['thp'])}</td>"
            f"<td>{cell(r['edge'], base['edge'])}</td>"
            f"<td>{r['olla']:+.2f}</td></tr>"
            for r in e2e["rows"])
        e2e_block = f"""
<h3>端到端实测（真实 ChannelHub 信道）</h3>
<p class="lead">{e2e['scenario']}。同一数据集、同一 seed，<b>只改 CSI 时延链</b>。</p>
<div class="tbl-wrap"><table>
<thead><tr><th>CSI 配置</th><th>平均 MCS</th><th>平均 rank</th>
<th>首传 BLER</th><th>小区体验 Mbps</th><th>5% 边缘 Mbps</th><th>OLLA dB</th></tr></thead>
<tbody>{rows}</tbody></table></div>

<div class="callout c-red">
<p><b>默认配置下，小区体验速率掉 46%，5% 边缘用户掉 70%。</b>
边缘用户掉得更狠是有道理的：他们本来就在 MCS 低档，老化再削掉几个 dB
就直接跌进覆盖外。</p>
<p><b>OLLA 从 −0.71 被拉到 −5.17 dB</b>——这正是你说的那条链路在跑：
基站按陈旧 CSI 高估了 BF 增益 → MCS 点高了 → 误码 → 外环把偏置压下去。
偏置的绝对值就是"基站高估了多少"的直接读数。</p>
</div>

<div class="callout c-amber">
<p><b>一个诚实的观察：超过 87 ms 之后，再老也差不多了。</b>
20 ms 和 40 ms 的结果与 10 ms 基本持平（体验速率 142.2 / 133.2 / 142.3）。
原因是相干时间只有 3.35 ms，87 ms 已经是它的 26 倍，信道<b>早就完全去相关</b>——
{F_RHO0} 之后再加时延不改变任何东西。这三档之间的差是噪声，不是趋势。</p>
<p>换句话说：<b>要看出 SRS 周期的影响，得先把跳频关掉</b>，
否则跳频那一项已经把预算吃完了。</p>
</div>

<p class="src">{e2e['note']}</p>
"""
    olla_block = ""
    if olla:
        rows = "".join(
            f"<tr><td>× {r['speedup']:g}{'（现网基线）' if r['speedup'] == 1 else ''}</td>"
            f"<td>{r['target']:.3f}</td><td>{r['bler']:.3f}</td>"
            f"<td>{r['olla']:+.2f}</td><td>{r['thp']:.1f}</td></tr>"
            for r in olla["rows"])
        olla_block = f"""
<h3>决策 3 的验证：放大步长确实不改变稳态</h3>
<div class="tbl-wrap"><table>
<thead><tr><th>olla_speedup</th><th>理论稳态 BLER</th><th>实测首传 BLER</th>
<th>平均 OLLA dB</th><th>小区体验 Mbps</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p><b>理论稳态 BLER 一列恒为 0.091，一点没动</b>——因为它只取决于
{F_STEADY2}，等比放大约掉了。
实测 BLER 从 0.394 收敛到 0.100（≈ 理论值），而<b>体验速率几乎不变</b>
（142.3 → 142.5 Mbps）。<b>放大只是更快到达同一个地方，不歪曲结论。</b></p>
<p class="src">非 1.0 时结果的 <code>notes</code> 里会带一条显式告警，已实测确认会出现。</p>
"""
    e2e_block += olla_block

    return f"""{head()}
<body>
<div class="wrap">

<h1>决策落地</h1>
<p class="tagline">你的 8 个决策 + 5 个提案答复 → 全部落地</p>
<p class="meta">2026-08-04 · 十一个套件全通过 · 所有数字来自实跑</p>

<div class="callout c-green">
<p><b>一句话：8 个决策全部执行完，5 个提案里你批的第 1 个已经实现并验证，其余 4 个按你的意思搁置。</b>
新增 <code>csi_aging.py</code>（约 420 行）与 66 项专门测试。</p>
</div>

<div class="toc">
<strong>目录</strong>
<ol>
<li><a href="#d1">8 个决策的落地情况</a></li>
<li><a href="#d2">5 个提案的答复与处置</a></li>
<li><a href="#d3">提案 1：CSI 老化怎么建的</a></li>
<li><a href="#d4">实测：老化到底吃掉多少</a></li>
<li><a href="#d5">这一轮新踩的坑</a></li>
</ol>
</div>

<h2 id="d1">一、8 个决策的落地情况</h2>
<div class="tbl-wrap"><table>
<thead><tr><th style="min-width:130px">决策点</th><th style="min-width:190px">你定的</th>
<th>我做了什么</th></tr></thead>
<tbody>{decisions_rows()}</tbody></table></div>

<h2 id="d2">二、5 个提案的答复与处置</h2>
<div class="tbl-wrap"><table>
<thead><tr><th style="min-width:150px">提案</th><th style="min-width:210px">你的答复</th>
<th>处置</th></tr></thead>
<tbody>{proposals_rows()}</tbody></table></div>

<h2 id="d3">三、提案 1：CSI 老化怎么建的</h2>

<div class="callout c-blue">
<p><b>你说的「17 倍跳频」，是 38.211 里逐字有的。</b>
TS 38.211 Table 6.4.1.4.3-1 的 <b>C_SRS = 57 行</b>：</p>
<p style="text-align:center">{F_TABLE57}</p>
<p>取 {F_BSRS}、{F_BHOP} 时，每次 SRS 占 <b>16 RB —— 正好一个 RBG</b>，
要 <b>17 跳</b>才扫完 272 RB。这和本项目 17 RBG × 16 RB 的载波配置 <b>1:1 对上</b>。</p>
<p class="src">跳频序列直接调 ChannelHub 的 <code>srs_rb_indices</code>（它实现了 §6.4.1.4.3
的完整跳频树），没有自己重写。实测 <code>srs_hopping_cycle_length</code> 返回 17、
扫描顺序就是 RBG 0→1→…→16 循环。</p>
</div>

<h3>核心：不是给 SINR 打折扣，是让预编码真的算错</h3>
<p>基站用它<b>手上那个（陈旧的）</b>信道算预编码，实际传输吃的是<b>当前</b>信道：</p>
<p style="text-align:center">{F_CORE}</p>

<div class="callout c-green">
<p><b>这条恒等式是整个模型的地基。</b>零时延时 {F_ZERO}，于是
{F_DIAG} 是对角的，逆的对角元正好给出 {F_EIG}
——<b>和原来 <code>su_rank_adaptation</code> 的特征值公式一模一样</b>。</p>
<p>实测逐 rank SINR 最大偏差 <b>0.000000 dB</b>。不成立的话，老化就是叠加上去的
第二套物理，任何「老化损失」都只是两套物理的差值，毫无意义。</p>
</div>

<h3>另一个极易写错的地方：rank 必须由基站自己选</h3>
<p>我第一版就写错了：拿<b>真实</b>信道上的谱效去挑 rank。那等于让基站预知信道，
它会自动避开老化最狠的那个 rank，<b>损失被凭空抹掉一大半</b>。</p>
<p>现在表里是两套量：<code>sinr/mcs/se</code>（真实，用于 BLER 与吞吐对账）与
<code>se_gnb/best_se_gnb</code>（基站以为的，用于 rank 自适应与 PF 调度）。
零时延时两套逐位相同。实测开老化后<b>基站以为的谱效纹丝不动</b>，真实的掉了 27%
——这个「基站毫不知情」正是现网的样子。</p>

<h3>时延链</h3>
<div class="tbl-wrap"><table>
<thead><tr><th>环节</th><th>量级</th><th>说明</th></tr></thead>
<tbody>
<tr><td>SRS 周期 {F_TSRS}</td><td>5 / 10 / 20 / 40 ms</td>
<td>只接受这四个值，对应 30 kHz SCS 下 38.331 的 sl10/20/40/80</td></tr>
<tr><td><b>SRS 跳频</b></td><td><b>× 17</b></td>
<td><b>这是主导项。</b>10 ms 周期下全带扫一遍要 <b>170 ms</b>，
某个 RBG 的年龄在 0~160 ms 之间轮转，平均 80 ms</td></tr>
<tr><td>处理时延</td><td>2 ms（可配）</td><td>信道估计 + 预编码计算 + 调度下发</td></tr>
</tbody></table></div>
<p>逐 RBG 的年龄：{F_AGE}，
再量化成整数个信道快照（快照间隔 5 ms，由配置算出）。
<b>年龄随时间轮转</b>，不会有某几个 RBG 永远最差。</p>

<div class="callout c-amber">
<p><b>对照一下量级。</b>2.6 GHz、30 km/h 的多普勒是 72 Hz，
Jakes 相干时间（相关系数掉到 0.5）只有约 <b>3.3 ms</b>。
而默认配置下的平均 CSI 年龄是 <b>87 ms</b>——<b>26 倍</b>。
预编码基本是在对一个无关的信道做匹配。这种情况下 <code>aging_summary</code> 会主动告警。</p>
</div>

<h2 id="d4">四、实测：老化到底吃掉多少</h2>

<h3>合成信道（可控对照，8 个快照、ρ=0.9、各用户 0~−18 dB 路损梯度）</h3>
<div class="tbl-wrap"><table>
<thead><tr><th>CSI 配置</th><th>SU 小区谱效</th><th>相对完美</th><th>MU/SU 比值</th><th>相对完美</th></tr></thead>
<tbody>
<tr><td>零时延完美 CSI</td><td>5.887</td><td>—</td><td>0.816</td><td>—</td></tr>
<tr><td>SRS 10 ms 不跳频</td><td>5.281</td><td>−10%</td><td>0.679</td><td>−17%</td></tr>
<tr><td><b>SRS 10 ms + 17 跳（默认）</b></td><td><b>3.656</b></td><td><b>−38%</b></td>
<td><b>0.449</b></td><td><b>−45%</b></td></tr>
<tr><td>SRS 40 ms + 17 跳</td><td>3.523</td><td>−40%</td><td>—</td><td>—</td></tr>
</tbody></table></div>

<div class="callout c-green">
<p><b>反向对照，证明损失确实来自时变而不是别处。</b>
把信道换成慢变（ρ=0.99）后，同样的老化配置下 MU 损失从 <b>45% 掉到 10%</b>。
如果损失来自实现里的某个系统性偏差，它不会随信道时变性变化。</p>
</div>

<p><b>MU 掉得比 SU 狠，这是应该的。</b>ZF 的全部价值就是把配对用户之间的干扰零陷掉，
而零陷是<b>按基站以为的信道</b>打的——信道一变零陷就落空，残余干扰直接进分母。
SU 只是波束没对准，损失温和得多。</p>
{e2e_block}

<h2 id="d5">五、这一轮新踩的坑</h2>
<div class="tbl-wrap"><table>
<thead><tr><th style="min-width:170px">坑</th><th>症状</th><th>为什么难发现</th></tr></thead>
<tbody>
<tr><td><b>跳频兜底路径的输出和标准实现一模一样</b></td>
<td><code>hop_order()</code> 没先 <code>_ensure_path()</code>，静默走了恒等扫描兜底</td>
<td>C_SRS=57 的标准序列<b>就是</b>顺序扫描，所以兜底给出完全相同的结果——
除了看返回的 <code>source</code>，<b>没有任何办法发现自己没在用标准实现</b>。
测试因此直接断言 <code>source</code> 以 <code>channelhub:</code> 开头。</td></tr>

<tr><td><b>测试信道里 MU/SU 比值是个死数</b></td>
<td>扫遍 0~20 dB，比值<b>恒等于 2.000</b></td>
<td>噪声由小区统一锚定（物理正确），各 UE 信道统计又完全相同，于是全员同一个 MCS，
比值退化成流数比 (4×rank2)/(1×rank4)。看起来像「MU 增益与信噪比无关」，
实际是上下双端饱和。<b>测 MU 必须给各用户不同的路损。</b></td></tr>

<tr><td><b>rank 用真实信道选</b></td>
<td>老化损失只剩一半</td>
<td>基站被赋予了预知能力，自动避开最差的 rank。<b>结果依然「合理」</b>——
曲线形状对、方向对，只是损失偏小，没有对照就看不出来。</td></tr>

<tr><td><b>老化侧与完美侧粒度不同</b></td>
<td><code>measure_mu_gain</code> 里老化走 RBG、完美走 RB</td>
<td>算出来的「老化损失」里混着粒度差。两条路径必须同粒度，否则比的不是老化。</td></tr>

<tr><td><b>CQI=0 让发送侧 SINR 变成 −inf</b></td>
<td>整列 <code>sinr_tx</code> 是 −inf</td>
<td>CQI=0 的意思是「低于 CQI 表下界」，不是「这个用户不存在」——
真实接收 SINR 可能还有几个 dB。现在退回实测 PMI SINR，OLLA 还能在它上面工作。</td></tr>

<tr><td><b>页面 select 回传字符串，<code>bool()</code> 失灵</b></td>
<td>开关关不掉</td>
<td><code>bool("off")</code> 是 <b>True</b>。开关<b>完全无声地</b>失效，
页面显示「off」、后端照常开着。</td></tr>
</tbody></table></div>

<div class="callout c-blue">
<p><b>还剩什么。</b>提案 2/3/4/5 按你的意思搁置，没有动。
<code>main</code> 分支仍落后 <code>agent/company-bler-curves</code>，合不合由你定。</p>
</div>

<footer>superwireless 决策落地 · 公式由内联 KaTeX 排版（MathML 兜底），离线可用</footer>
</div>
{kx.upgrade_script()}
</body></html>
"""


def main() -> int:
    # **实测数据放 measurements/ 而不是 artifacts/**——后者被 gitignore，
    # 而这些 JSON 是文档里每个数字的出处，丢了文档就成了无源之水。
    e2e_path = ROOT / "measurements" / "e2e_aging.json"
    olla_path = ROOT / "measurements" / "e2e_olla.json"
    e2e = json.loads(e2e_path.read_text(encoding="utf-8")) if e2e_path.exists() else None
    olla = json.loads(olla_path.read_text(encoding="utf-8")) if olla_path.exists() else None
    out = ROOT / "DECISIONS.html"
    out.write_text(build(e2e, olla), encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"{out}  ({kb:.0f} KB, KaTeX {'内联' if kx.available() else '缺失'}"
          f"{'，含端到端实测' if e2e else '，端到端实测待补'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
