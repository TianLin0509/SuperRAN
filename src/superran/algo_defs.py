"""算法族的详细定义：有哪些实现、当前选哪个、流程是什么、公式怎么写。

**这是文档，也是自查清单。** 把每个算法摊成流程步骤之后，
「代码是不是真这么做的」就变成一个可以逐条对照的问题。
写这份文件的过程本身就发现了实现与描述不符的地方。

公式用 LaTeX 子集写，由 :mod:`mathml` 转成 MathML——浏览器原生渲染，
不引 CDN、不塞 1 MB 的 JS，离线双击照样好看。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Option:
    """一族算法里的一个可选实现。"""

    key: str
    name: str
    formula: str = ""
    summary: str = ""
    detail: str = ""
    when: str = ""
    cost: str = ""
    source: str = ""

    def as_dict(self, current: bool = False) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if v}
        d["current"] = current
        return d


@dataclass
class Flow:
    """算法流程：一串步骤，可带分支与回环。"""

    steps: list[tuple[str, str]] = field(default_factory=list)
    branches: list[tuple[int, str, str]] = field(default_factory=list)
    loop_back: tuple[int, int, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [{"title": a, "desc": b} for a, b in self.steps],
            "branches": [{"at": i, "cond": c, "goto": g} for i, c, g in self.branches],
            "loop_back": ({"frm": self.loop_back[0], "to": self.loop_back[1],
                           "desc": self.loop_back[2]} if self.loop_back else None),
        }


@dataclass
class Family:
    """一族算法。"""

    key: str
    name: str
    stage: str
    intro: str = ""
    formula: str = ""
    current: str = ""
    config_key: str = ""
    caveat: str = ""
    source: str = ""
    options: list[Option] = field(default_factory=list)
    flow: Flow | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()
             if v and k not in ("options", "flow")}
        d["options"] = [o.as_dict(o.key == self.current) for o in self.options]
        if self.flow:
            d["flow"] = self.flow.as_dict()
        d["current_name"] = next((o.name for o in self.options
                                  if o.key == self.current), self.current)
        return d


# ---------------------------------------------------------------------------
# 接收机
# ---------------------------------------------------------------------------
def _receiver(multi: bool) -> Family:
    return Family(
        key="receiver",
        name="接收机 / 后处理 SINR",
        stage="接收",
        current="irc" if multi else "mmse",
        config_key="receiver",
        intro="接收机决定「同样的信道能解出多少信噪比」。四种实现共用同一个"
              "后处理 SINR 公式，**区别全在噪声加干扰协方差 R_n 怎么给**——"
              "这是理解它们差异的唯一钥匙。",
        formula=r"\text{SINR}_k = \frac{1}{\left[ \left( I + \frac{P}{r} "
                r"G^H R_n^{-1} G \right)^{-1} \right]_{kk}} - 1",
        caveat=("实测 ChannelHub 的**单个干扰小区信道是秩 1 的**"
                "（次大/最大奇异值比中位 4.0e−8，96 个抽样全部如此）。"
                "3 个秩 1 干扰配 4 根接收天线，刚好能全部零陷掉——"
                "**这是 IRC 最有利的工况，实测 +2.37 bit/s/Hz 偏乐观**。"
                "引用增益时必须同时给出 interference_rank。"
                if multi else
                "单小区场景没有邻区干扰，R_uu 为空，IRC 与 MMSE 数学上完全等价。"),
        source="经典 MMSE-IRC；本项目 linklevel.post_equalizer_sinr",
        options=[
            Option(
                "mmse", "MMSE（最小均方误差）",
                formula=r"R_n = \left( N_0 + \frac{I_{tot}}{N_{rx}} \right) I",
                summary="把干扰的总功率摊到各接收天线，当成白噪声处理",
                detail="业界默认的对照基线。它知道干扰有多强，"
                       "但**不知道干扰从哪个方向来**，所以只能把干扰功率平摊成等效噪声。"
                       "干扰本来就接近白的场景下这已经是最优的。",
                when="要一条公允的基线；或干扰源多到空间上已接近白",
                cost="一次 N_rx×N_rx 求逆",
                source="经典线性 MMSE 接收机"),
            Option(
                "irc", "IRC（干扰抑制合并）",
                formula=r"R_n = N_0 I + R_{uu}, \quad R_{uu} = \sum_k "
                        r"(H_k W_k)(H_k W_k)^H",
                summary="用干扰的完整空间协方差，在干扰来向上打零陷",
                detail="**和 MMSE 是同一个公式**，只是 R_n 保留了干扰的空间结构。"
                       "N_rx 根接收天线最多零陷 N_rx−1 个独立干扰方向，"
                       "所以 R_uu 的有效秩逼近 N_rx 时 IRC 增益必然趋近 0——"
                       "结果里返回 interference_rank 就是让人能判断处在哪个区间。",
                when="多小区、干扰空间上有色（有效秩明显小于 N_rx）",
                cost="要估 R_uu；快照数少于天线数时它奇异，必须对角加载",
                source="3GPP 常用 IRC；本项目 linklevel.interference_covariance"),
            Option(
                "zf", "ZF（迫零）",
                formula=r"\text{SINR}_k = \frac{P/r}{[(G^H R_n^{-1} G)^{-1}]_{kk}}",
                summary="完全消除层间干扰，代价是噪声放大",
                detail="强制把层间串扰压到零。低信噪比时噪声放大明显，通常不如 MMSE；"
                       "高信噪比时两者趋同。",
                when="高信噪比；或要一个不含层间干扰的干净对照",
                cost="与 MMSE 同量级"),
            Option(
                "mrc", "MRC（最大比合并）",
                formula=r"w_k = R_n^{-1} g_k",
                summary="逐层最大化信噪比，**不消除层间干扰**",
                detail="单层传输时是最优的；多层时层间干扰完全不处理，"
                       "SINR 会明显低于 MMSE。主要作为下界对照。",
                when="rank=1；或要看「完全不做干扰抑制」能差多少",
                cost="最省，只有向量乘"),
        ],
        flow=Flow(steps=[
            ("取有效信道 G", "G = H_eff^H，形状 [N_rx, rank]。H_eff 已经含了发射预编码"),
            ("组噪声加干扰协方差 R_n", "MMSE/ZF/MRC 取 (N0 + I_tot/N_rx)·I；"
                                      "IRC 取 N0·I + R_uu，保留空间结构"),
            ("求逆 R_n^{-1}", "用伪逆，防止 R_uu 秩亏时崩掉"),
            ("算耦合矩阵 A = G^H R_n^{-1} G", "rank×rank，对角是有用信号、非对角是层间干扰"),
            ("按接收机类型出逐层 SINR", "MMSE/IRC 取 (I + (P/r)A)^{-1} 的对角求倒数减一；"
                                        "ZF 取 A^{-1} 的对角；MRC 逐层显式算泄漏"),
            ("逐 RB 重复", "输出形状 [RB, rank]，随后按单码字口径压成一个用户级 SINR"),
        ]),
    )


# ---------------------------------------------------------------------------
# 信道估计
# ---------------------------------------------------------------------------
def _channel_est(est: str) -> Family:
    return Family(
        key="channel_est",
        name="信道估计",
        stage="接收",
        current=est,
        config_key="channel_est_mode",
        intro="接收端只有导频上的观测，要把整个时频栅格的信道估出来。"
              "**估得准不准直接决定预编码有多准**——MU-MIMO 尤其敏感。",
        formula=r"\hat{H}_{LS} = \frac{Y_{RS}}{X_{RS}}, \quad "
                r"\hat{H}_{MMSE} = R_{hh}(R_{hh} + \frac{1}{\gamma} I)^{-1} \hat{H}_{LS}",
        caveat="用 ideal 做预编码会得到教科书曲线——**MU-MIMO 尤其致命**："
               "实测 CSI NMSE 从 −31 dB 掉到 −8.6 dB，MU 和谱效直接掉一半"
               "（46.28 → 23.92）。任何用 h_true 做预编码得出的 MU 增益都不可信。",
        source="ChannelHub msg_embedding/channel_est/",
        options=[
            Option("ideal", "ideal（理想信道）",
                   formula=r"\hat{H} = H",
                   summary="直接拿真值，**是上界不是可实现性能**",
                   detail="绕过全部估计器。用来算「如果 CSI 完美能到多少」，"
                          "是所有 CSI 相关结论的分子。**不能拿它当系统性能报出去。**",
                   when="要一个 CSI 无损的上界做对照",
                   cost="零"),
            Option("ls_linear", "ls_linear（LS + 线性插值）",
                   formula=r"\hat{H}_{LS} = \frac{Y}{X}, \quad "
                           r"\hat{H}(f,t) = \text{interp}_{2D}(\hat{H}_{LS})",
                   summary="导频上做最小二乘，再在频/时两维线性插值",
                   detail="ChannelHub 的**默认档**——2026-08-01 之前所有数据集都走的它。"
                          "最朴素也最快，不需要任何先验。缺点是把导频上的噪声"
                          "原封不动带进插值结果。",
                   when="默认；不知道 PDP 先验时",
                   cost="最省",
                   source="LS + 可分离二维线性插值"),
            Option("ls_mmse", "ls_mmse（LS + 频域 MMSE）",
                   formula=r"R_{hh}[m,n] = \frac{1}{1 + j 2\pi (m-n) \Delta f \tau_{rms}}",
                   summary="LS 之后用指数 PDP 先验做频域 MMSE，再线性插值时域",
                   detail="靠时延扩展先验把不像信道的部分（噪声、被污染的导频）压掉。"
                          "**导频越挤赢得越多**：实测邻区干扰 UE=0 时比 ls_linear 好 0.7 dB，"
                          "=16 时好 3.6 dB。时域仍走线性插值，因为 MMSE 需要多普勒先验。",
                   when="导频有污染的多小区场景；要看估计器对 MU 的影响",
                   cost="每个频域块一次矩阵求逆",
                   source="Sesia et al., LTE — The UMTS Long Term Evolution §9.2"),
        ],
        flow=Flow(steps=[
            ("从收到的信号里取导频", "Y_RS：导频位置上的观测；X_RS：发的是什么"),
            ("LS 估计", "H_LS = Y_RS / X_RS，逐导频点，不做任何平滑"),
            ("（仅 ls_mmse）频域 MMSE", "用指数 PDP 先验组 R_hh，"
                                        "H_MMSE = R_hh (R_hh + I/γ)^{-1} H_LS"),
            ("二维插值到全栅格", "频域与时域可分离地线性插值，补出非导频位置"),
            ("交给预编码与检测", "这份 h_est 才是真实系统能拿到的 CSI"),
        ], branches=[(2, "mode == ls_linear", "跳过 MMSE，直接插值")]),
    )


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------
def _scheduler() -> Family:
    return Family(
        key="scheduler",
        name="调度器",
        stage="系统级",
        current="pf",
        config_key="scheduler",
        intro="每个 TTI 要决定把资源给谁。三种准则代表了「吞吐「与「公平」的两个极端"
              "和中间的折中。",
        formula=r"\text{PF: } u^\star = \arg\max_u \frac{R_u^{inst}}{\bar{R}_u},"
                r"\quad \bar{R}_u \leftarrow (1 - \frac{1}{T_c}) \bar{R}_u "
                r"+ \frac{1}{T_c} R_u^{served}",
        caveat="**PF 有个经典病理**：一个永远发不成功的用户，平均速率趋近 0、"
               "度量发散，调度器会死盯着他不放。实测这能把全小区首传 BLER "
               "从 0.011 拖到 0.811。现在覆盖外的用户（SINR 够不到 MCS 0 门限）"
               "会被剔出调度并单独报出。",
        source="经典 PF；本项目 system.simulate",
        options=[
            Option("pf", "PF（比例公平）",
                   formula=r"m_u = \frac{R_u^{inst}}{\bar{R}_u}",
                   summary="瞬时速率除以历史平均速率，兼顾吞吐与公平",
                   detail="信道好的时候多给、但给多了之后权重自然下降。"
                          "时间窗 T_c 决定公平的尺度：**太小接近 max-C/I**（只喂近点用户），"
                          "**太大接近轮询**（不利用信道起伏）。",
                   when="默认；要一个贴近现网的基线",
                   cost="每 TTI 一次除法加一次指数平滑"),
            Option("max_ci", "max-C/I（最大载干比）",
                   formula=r"m_u = R_u^{inst}",
                   summary="永远给信道最好的那个用户",
                   detail="小区吞吐的**上界**，但极不公平——远点用户可能永远轮不到。"
                          "实测 Jain 公平度只有 0.125，而 PF 是 0.551。",
                   when="要看小区吞吐的天花板",
                   cost="最省"),
            Option("rr", "RR（轮询）",
                   formula=r"u = (t + u_0) \bmod N",
                   summary="轮流来，完全不看信道",
                   detail="最公平也最浪费——好信道的时刻没被利用上。"
                          "作为公平性的另一个极端参照。",
                   when="要一个与信道无关的对照",
                   cost="最省"),
            Option("edf", "EDF（包长感知）",
                   formula=r"m_u = \frac{TBS_u}{Buffer_u}\cdot\frac{1}{p_u}",
                   summary="优先调度最快能传完的用户",
                   detail="Buffer/TBS 是“还需几个调度机会才能排空缓冲区”，取"
                          "倒数当优先级：缓冲区小 + 信道好的用户先走，一次传完"
                          "就释放资源；缓冲区大 + 信道差的排后面——传了也清不"
                          "空。**分母是当前队列而不是历史吞吐量，所以它无状态**。"
                          "代价是长期公平性：重载下大包用户可能被饿死，要看 "
                          "ue_experienced 的低分位数而不只看小区吞吐。需要有限"
                          "队列，full_buffer 与容量口径硬失败。",
                   when="小包（信令/IoT/IM）占比高、时延敏感的混合业务",
                   cost="每 TTI 一次除法，比 PF 还省——不用维护历史平均"),
            Option("qos_pf_edf", "EPF+EDF 混合",
                   formula=(r"m_u = \left[(1-w)\,s\,\mathrm{EPF}_u"
                            r" + w\,\mathrm{EDF}_u\right]\cdot\frac{1}{p_u}"),
                   summary="长期公平与小包时延之间连续可调",
                   detail="照抄蓝本加权混合模式的原式。**两个分量"
                          "不同量纲**：EPF 是 bytes^β/bytes^α，EDF 是无量纲比值，"
                          "s（蓝本的 thp_filter）就是用来配平量级的。它没标"
                          "定时名义 w=0.5 可能实际等价于 0.99，因此结果里必须看 "
                          "scheduler_mixed_component_scale 的 effective_edf_share。"
                          "w=0 严格退化成 qos_pf，w=1 严格退化成 edf。",
                   when="既要长期公平又要小包低时延的混合业务",
                   cost="两个分量都要算，略贵于任一单独模式"),
        ],
        flow=Flow(steps=[
            ("筛出有数据要发的用户", "缓冲区非空，且这个快照下不处于覆盖外"),
            ("算调度度量", "PF 取 R_inst/R_avg；EDF 取 TBS/Buffer；max-C/I 取 R_inst；RR 按轮次"),
            ("排序取最优", "SU 取第一名；MU 取前 K 名（还要过 SU/MU 自适应那一关）"),
            ("发送并判 ACK/NACK", "按接收侧 SINR 查 BLER 曲线抽一次"),
            ("更新历史平均速率", "R_avg ← (1−1/Tc)·R_avg + (1/Tc)·本次实发"),
        ], loop_back=(5, 1, "下一个 TTI")),
    )


# ---------------------------------------------------------------------------
# MU-MIMO 与 SU/MU 自适应
# ---------------------------------------------------------------------------
def _mu() -> Family:
    return Family(
        key="mu_mimo",
        name="MU-MIMO 预编码（EZF）",
        stage="多用户",
        current="zf",
        config_key="precoder",
        intro="多个用户同时占用全部频域资源，靠**空间波束**区分。"
              "EZF 分两级：先用各自的 SVD 把用户内的流分开，"
              "再用 ZF 把用户间的干扰清零。",
        formula=r"W \propto \tilde{H}^H (\tilde{H} \tilde{H}^H + \alpha I)^{-1},"
                r"\quad \alpha = \frac{N_{stream} \sigma^2}{P}",
        caveat="**预编码矩阵只能表示方向，功率必须单独给。** 合成一个全局标量会退化成"
               "信道求逆功控——ZF 满足 H̃W = c·I，所有用户接收电平被强行拉平，"
               "弱用户吃掉大部分功率。症状极隐蔽：等效信道范数 12.0/11.7/10.7/7.2 的"
               "四个用户拿到**一模一样的谱效 11.482**、Jain 公平度恒等于 1.000000，"
               "看起来像「MU 天生公平」。",
        source="用户 2026-08-02 给的现场算法；Sionna rzf_precoding_matrix（逐列归一）",
        options=[
            Option("zf", "ZF（迫零）",
                   formula=r"W \propto \tilde{H}^H (\tilde{H} \tilde{H}^H)^{-1}",
                   summary="把用户间干扰彻底清零",
                   detail="理想 CSI 下用户间耦合矩阵严格对角（实测非对角 < 1e−8）。"
                          "**零陷的深度完全由 CSI 精度决定**——CSI 一差，"
                          "残余干扰立刻上来。",
                   when="CSI 精度高；要一个干净的 MU 上界",
                   cost="一次 N_stream×N_stream 求逆"),
            Option("rzf", "RZF（正则化迫零）",
                   formula=r"\alpha = \frac{N_{stream} \sigma^2}{P}",
                   summary="加正则项，在噪声放大与干扰残留之间折中",
                   detail="低信噪比时退化成 MRT、高信噪比时趋近 ZF。"
                          "α 取 N_stream·σ²/P 是使和速率最大的经典取值。",
                   when="低信噪比或 CSI 有误差时比 ZF 稳",
                   cost="与 ZF 同"),
            Option("mrt", "MRT（最大比发射）",
                   formula=r"W \propto \tilde{H}^H",
                   summary="朝每个用户各自的最强方向发，不管用户间干扰",
                   detail="天线数远大于用户数时用户间信道近似正交，MRT 就够用。"
                          "本项目 64 端口服务十几个用户，这个假设不太成立。",
                   when="大规模天线、用户数远小于端口数",
                   cost="最省，无求逆"),
        ],
        flow=Flow(steps=[
            ("逐用户 SVD", "对下行信道 H_u^H 做 SVD，取前 rank 个左奇异向量当接收合并权，"
                           "得到等效行向量 σ_s·v_s^H"),
            ("MU 秩上限截断", "MU 下每用户最多 2 流（工程约束），SU 可以到 4"),
            ("堆叠成 H̃", "把已配对用户的等效行向量摞成 [N_stream, N_BS]"),
            ("ZF / RZF 求预编码方向", "W ∝ H̃^H(H̃H̃^H + αI)^{-1}，**逐列归一到单位范数**"),
            ("功率分配", "等分 P/N_stream（rank2 的用户自然拿 2 份），或对 ZF 后的增益注水"),
            ("算逐用户 SINR", "G = H̃_eval·W，对角是信号、非对角乘功率是残余干扰"),
        ], branches=[(4, "CSI 用 h_est 而非 h_true", "零陷不干净，非对角项即残余干扰")]),
    )


def _su_mu() -> Family:
    return Family(
        key="su_mu",
        name="SU / MU 自适应",
        stage="多用户",
        current="useful_bytes",
        config_key="mu_enabled",
        intro="同一个 TTI 到底是给一个用户独占（SU），还是配对几个用户一起发（MU）？"
              "**判据是完整计划真实能交付的业务字节**，不是名义谱效或第一个可行伙伴。",
        formula=r"B_{\mathrm{useful}}=\sum_u\min(Q_u,TBS_u),\qquad "
                r"\mathrm{mode}=\arg\max(B_{\mathrm{SU}},B_{\mathrm{MU}})",
        caveat="**别想当然认为配对总是更好。** SU 无 MU 干扰且可到 rank4；"
               "MU 每用户最多 rank2、均分功率并承受 CorrLoss。小包超出队列的 "
               "TBS 是 padding，不能算 MU 收益。若 SU 已清空全部队列，直接 SU。",
        source="当前 experience_v2 已确认调度合同",
        options=[
            Option("useful_bytes", "比真实可交付字节",
                   summary="两套完整 TTI 计划都按队列封顶，取 useful bytes 更高者",
                   detail="PF 先固定 anchor，MU 枚举全部伙伴并过相关性/层数/BLER门。"
                          "若 SU 能清空全部可服务队列，直接 SU；否则平局归 MU。",
                   when="默认",
                   cost="每次判决构造两套无副作用计划"),
            Option("sus", "SUS 半正交用户选择",
                   formula=r"\text{drop } i \text{ if } \frac{|\tilde{h}_i "
                           r"\tilde{h}_j^H|}{\|\tilde{h}_i\| \|\tilde{h}_j\|} > \alpha",
                   summary="贪心选在已选集正交补里投影最长的用户",
                   detail="Yoo & Goldsmith 2006 的经典准则，复杂度 O(K²·max_users)。"
                          "**门限 α 是个需要标定的经验值**，现场没有明确取值，"
                          "所以默认不用它而用小区谱效直接比。",
                   when="用户数远多于端口数、需要快速筛选时",
                   cost="O(K²·max_users)",
                   source="Yoo & Goldsmith, IEEE JSAC 2006"),
            Option("greedy_sum_rate", "贪心和速率",
                   summary="每轮真算一遍 ZF 和速率，选增量最大的",
                   detail="比 SUS 准，但每轮要做 K 次矩阵求逆。增量为负就停。",
                   when="用户数不多、要最优配对时",
                   cost="每轮 K 次求逆"),
        ],
        flow=Flow(steps=[
            ("PF 排序", "只做一次；固定 anchor，不让 MU 评分篡改公平顺序"),
            ("算完整 SU plan", "按 PF 顺序和真实队列分配 RBG，累计 useful bytes"),
            ("检查是否全部清空", "能的话直接 SU——配对不会再减少等待"),
            ("算完整 MU plan", "枚举全部伙伴，按 useful bytes/RBG 选 pair，并处理剩余 RBG"),
            ("比较与留证", "MU useful≥SU 才走 MU；保存两套字节数、伙伴评分和拒绝原因"),
        ], branches=[(2, "SU 一个 TTI 能传完", "直接 SU，跳过 MU 计算")]),
    )


# ---------------------------------------------------------------------------
# 链路自适应
# ---------------------------------------------------------------------------
def _rank_mcs() -> Family:
    return Family(
        key="rank_mcs",
        name="Rank 自适应 + 单码字 MCS",
        stage="链路自适应",
        current="rank_sweep",
        config_key="",
        intro="一个用户一个 TTI 只发**一个码字**，同一个 MCS 覆盖全部 RB 与全部流。"
              "所以要先把逐 RB 逐流的 SINR 压成**一个数**再查 MCS，"
              "而不是逐 RB 查完再平均。",
        formula=r"\text{SINR}_{user} = \frac{1}{N_{RBG} \cdot r} \sum_{b,s} "
                r"10\log_{10}\left( \text{SINR}_{b,s} \right), \quad "
                r"SE = r \cdot SE(\text{MCS})",
        caveat="**dB 域平均比线性平均保守得多。** 实测半好半坏（+20/−20 dB 各半）的信道，"
               "dB 域给 0 dB、线性给 17 dB，**差 17 dB**——深衰的 RBG 会把整个码字拖下去。"
               "逐 RB 查 MCS 再平均等于假设每个 RB 能用不同 MCS，系统性高估。",
        source="38.214 §5.1.3；预置 20B 曲线；项目系统级链路自适应口径",
        options=[
            Option("rank_sweep", "遍历 rank 1~4 取谱效最高",
                   formula=r"r^\star = \arg\max_r \; r \cdot SE(\text{MCS}"
                           r"(\text{SINR}_{user}(r)))",
                   summary="每个 rank 算一遍，比 rank × MCS谱效",
                   detail="**这是个真实的权衡，不是秩越高越好。** "
                          "rank1 全功率压最强流、BF 增益最大、MCS 最高，但只有一条流；"
                          "rank4 每流只有 P/4，弱流把用户级 SINR 拖下去、MCS 掉档，"
                          "但乘的是 4。最优点通常在中间——现网锚点是平均 rank 2.7。",
                   when="默认",
                   cost="4 次 MCS 查表，SVD 可复用"),
            Option("fixed", "固定 rank",
                   summary="按配置定死，不自适应",
                   detail="MU 侧就是这么做的（硬顶 rank 2，工程约束）。"
                          "SU 侧固定 rank 主要用于对照实验。",
                   when="要控制变量做对照",
                   cost="零"),
        ],
        flow=Flow(steps=[
            ("对每个候选 rank r", "总功率 P 在 r 条流上均分，每流 P/r"),
            ("算逐 RBG 逐流 SINR", "SINR_{b,s} = σ_s²·(P/r) / σ_n²"),
            ("RBG 内线性平均", "同一个调度单位，功率域相加合理"),
            ("RBG 间与流间 dB 域平均", "**先平均再查表**，这是单码字约束的体现"),
            ("查 MCS", "选满足 10% 首传 BLER 的最高 MCS（预置表 3）"),
            ("算谱效 SE = r × SE(MCS)", "乘 rank 是因为 r 条流各发一份"),
            ("取所有 rank 里 SE 最大的", "这就是 rank 自适应的判决"),
        ], loop_back=(6, 1, "换下一个 rank")),
    )


def _olla() -> Family:
    return Family(
        key="olla",
        name="OLLA 外环 + 发送/接收侧 SINR 分离",
        stage="系统级",
        current="olla_mcs (legacy API name: olla_db)",
        config_key="olla_enabled",
        intro="**这是干扰影响吞吐的第一性路径。** 发送端不知道瞬时干扰，"
              "只有 CQI 反馈的粗略统计值；接收端实打实吃着干扰、SINR 更低、于是误码；"
              "OLLA 用 ACK/NACK 把这个差压回来。",
        formula=r"\Delta \leftarrow \begin{cases} \Delta + \delta_{up} & \text{ACK} \\ "
                r"\Delta - \delta_{down} & \text{NACK} \end{cases}, \quad "
                r"\text{BLER}_{\infty} = \frac{\delta_{up}}{\delta_{up} + \delta_{down}}",
        caveat="**AMC 预测别做成「完全无干扰」。** 应走内部 CQI 门限 + "
               "BF Gain（见「AMC 预测坐标」那一族），先反折基准 MCS，再叠加 OLLA。"
               "<br>**+0.01/−0.1 对应的稳态是 9.09% 而不是 10%。** "
               "稳态解 p = δ_up/(δ_up+δ_down)，要 10% 得取 δ_down = <b>0.09</b>。"
               "<br>由于最终使用 floor(MCS+Δ)，理论步长比仍需用新口径"
               "重新做收敛校验；旧 dB-domain OLLA 的实测数字不再作当前证据。",
        source="已确认的内部 AMC 顺序（2026-08-23）；步长比按 target BLER 反解",
        options=[
            Option("olla_db", "MCS 域偏置（历史 key，目标 10%）",
                   formula=r"\delta_{down} = \delta_{up}\cdot\frac{1-p}{p} "
                            r"= 0.01 \cdot \frac{0.9}{0.1} = 0.09\ \text{MCS}",
                   summary="ACK 小步加、NACK 大步减，稳态 BLER 收敛到步长比",
                   detail="步长**比例**决定稳态 BLER，**绝对大小**只决定收敛速度。"
                          "目标 10%、δ_up=0.01 时精确解是 δ_down=<b>0.09</b>；"
                          "常说的 −0.1 给的是 9.09%。"
                           "<br>OLLA 是连续 MCS 状态，发送时对基准 MCS + offset 严格 floor。"
                           "未收敛时结果里会主动告警。",
                   when="默认；出正式结论用它",
                   cost="每次 ACK/NACK 一次加减"),
            Option("olla_fast", "等比放大加速收敛（olla_speedup）",
                   formula=r"(\delta_{up}, \delta_{down}) \to k\,(\delta_{up}, "
                           r"\delta_{down}), \quad p_{\infty} \text{ 不变}",
                   summary="比例不变、绝对值等比放大",
                    detail="步长比不变时理论稳态不变，放大主要改变收敛速度和抖动。"
                           "MCS-domain 口径下的实测幅度要重新校验，不复用旧数据。"
                          "非 1.0 时结果里带显式告警。**出正式结论设回 1.0。**",
                   when="快速迭代、看趋势",
                   cost="稳态附近抖动更大"),
            Option("off", "关闭 OLLA",
                   summary="MCS 直接由 SINR_AMC_PRED 决定，不做外环修正",
                   detail="用来看「如果发送端对干扰一无所知会怎样」——"
                          "这正是 OLLA 存在的理由。",
                   when="对照实验",
                   cost="零"),
        ],
        flow=Flow(steps=[
            ("算 SINR_AMC_PRED", "CQI 门限 + BF Gain（见同名算法族），不是物理 TX/RX SINR"),
            ("反折基准 MCS", "按 SINR_AMC_PRED 查门限，选满足目标 BLER 的最高档"),
            ("加 OLLA 偏置", "floor(MCS_base + Δ) 并钳到 profile，Δ 初值 0"),
            ("按接收侧 SINR 判误码", "接收侧含**瞬时**干扰，所以实际 BLER 高于发送端预期"),
            ("ACK → Δ += 0.01；NACK → Δ −= 0.09", "钳位在 [−20,+3] MCS；稳态 p = 0.01/(0.01+0.09) = 10%"),
        ], loop_back=(5, 1, "下一次调度"), branches=[
            (4, "NACK", "进入唯一一次 IR/CC 重传；只从 NewTx 曲线推导 BLER")]),
    )


def _families(cfg: dict[str, Any]) -> list[Family]:
    from .algo_defs2 import extra_families  # noqa: PLC0415

    n_cell = int(cfg.get("num_sites", 1) or 1) * int(cfg.get("sectors_per_site", 1) or 1)
    est = str(cfg.get("channel_est_mode", "ls_linear"))
    return [*extra_families(cfg), 
        _receiver(n_cell > 1),
        _channel_est(est),
        _rank_mcs(),
        _mu(),
        _su_mu(),
        _scheduler(),
        _olla(),
    ]


STAGES = ["信道生成", "发射", "接收", "链路自适应", "多用户", "系统级"]


def families(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """这次配置下的算法族清单，每族含全部可选实现、当前选中项与流程图。"""
    return [f.as_dict() for f in _families(cfg or {})]
