"""这次仿真到底用了哪些算法——一份可以摊开给用户看的清单。

**为什么要有这个模块。** 一次仿真里嵌着十几个算法选择：预编码用什么、
接收机怎么算 SINR、MCS 怎么选、rank 怎么定、多用户怎么配对、调度器什么准则、
体验速率怎么掐头去尾。每一个都会改变最终数字，但它们平时**全都藏在代码里**——
用户看到的只有一个"谱效 26.3"。

这里把它们逐条写出来：**是什么、怎么算的、为什么这么选、什么时候会失真**。
说明书里的「算法」页签直接渲染它。

写在这里的每一条都必须和代码对得上。加算法就在这里加一条，
`test_algorithms` 会检查清单与实际实现没有漂开。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 现场口径（用户 2026-08-02 确认）
FIELD_ANCHORS = {
    "avg_mcs": 15.0,
    "avg_mcs_near": 25.0,
    "avg_mcs_far": 5.0,
    "avg_rank": 2.7,
    "source": "现网话统，用户 2026-08-02 提供",
}


@dataclass
class Algorithm:
    """一个算法的完整交代。"""

    key: str
    name: str
    stage: str                      # 属于链路的哪一段
    choice: str                     # 这次实际用的是哪一个
    formula: str = ""
    why: str = ""
    caveat: str = ""                # 什么时候这个选择会让结论失真
    source: str = ""                # 标准条款或文献
    alternatives: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}


def _algorithms(cfg: dict[str, Any]) -> list[Algorithm]:
    from . import hardware as hw  # noqa: PLC0415

    n_bs = int(cfg.get("num_bs_tx_ant", 64) or 64)
    n_cell = int(cfg.get("num_sites", 1) or 1) * int(cfg.get("sectors_per_site", 1) or 1)
    est = str(cfg.get("channel_est_mode", "ls_linear"))
    multi = n_cell > 1
    panel = list(cfg.get("bs_panel") or [])
    profile = hw.company_profile_for_panel(panel)
    if profile is None and not panel:
        profile = {64: "64t", 256: "256t"}.get(n_bs)
    mode = str(cfg.get("antenna_model_mode") or (
        "effective_subarray" if profile is not None else "legacy_64"))
    if mode == "legacy_64":
        antenna_choice = f"legacy_64（{n_bs} 个独立阵元）"
        antenna_why = (
            "显式历史兼容/对照模式：每个数字端口被当成一个独立阵元。"
            "它不代表已确认的预置 64T/256T 馈电结构。")
    else:
        m = 3 if profile == "64t" else 6 if profile == "256t" else int(
            ((cfg.get("bs_antenna") or {}).get("fixed_vertical_subarray") or {}).get(
                "elements_per_rf_port", 1))
        n_ae = n_bs * m
        antenna_choice = (
            f"{mode}（1 驱 {m}，{n_bs} RF 端口 × {m} 阵子 = {n_ae}）")
        antenna_why = (
            f"该 AAU 是 {n_bs} 个 RF 端口、每端口固定驱动垂直相邻 {m} 个阵子。"
            "64T 与 256T 统一采用 pol_h_v + top_to_bottom："
            "先极化块、再水平列、垂直行最快，v=0 对应物理顶部。"
            "水平间距 0.5λ、物理垂直阵子间距 0.67λ。")

    return [
        Algorithm(
            key="antenna_model",
            name="天线阵列模型",
            stage="信道生成",
            choice=antenna_choice,
            why=antenna_why,
            caveat="端口顺序只改变数组坐标，不应改变同一物理信道的结果；迁移必须同时重排 H、W、F。"
                   "历史 64T 样本缺少新布局字段时只能按 h_v_pol + bottom_to_top 读取，"
                   "不能静默套用新默认。1 驱 N 是具体硬件事实，未知面板不得猜测。",
            source="SuperRAN native.EffectiveArray + hardware.py",
            alternatives=["legacy_64", "physical_reference（真跑物理阵子，慢但可作参考）"],
        ),
        Algorithm(
            key="channel_est",
            name="信道估计",
            stage="接收",
            choice={"ideal": "ideal（直接拿真值，是上界不是可实现性能）",
                    "ls_linear": "ls_linear（LS + 频/时域线性插值）",
                    "ls_mmse": "ls_mmse（LS + 频域 MMSE 用指数 PDP 先验 + 线性时域插值）",
                    }.get(est, est),
            why="ls_linear 是 ChannelHub 的默认档。ls_mmse 靠 PDP 先验压掉被污染的部分，"
                "**导频越挤赢得越多**：实测干扰 UE=0 时好 0.7 dB，=16 时好 3.6 dB。",
            caveat="用 ideal 做预编码会得到教科书曲线——**MU-MIMO 尤其致命**，"
                   "实测 CSI NMSE 从 −31 dB 掉到 −8.6 dB，MU 和谱效直接掉一半。",
            source="ChannelHub msg_embedding/channel_est/",
            alternatives=["ideal", "ls_linear", "ls_mmse"],
        ),
        Algorithm(
            key="precoder_su",
            name="单用户预编码",
            stage="发射",
            choice="逐 RB 协方差特征预编码（代码名 svd）",
            formula="R_f = E_t[H_tf H_tf^H] → W_f = eigvec_top(R_f)，总功率按流均分",
            why="T=1 时它等价于瞬时 SVD 发射特征向量；T>1 时用功率协方差求一组"
                "相位不敏感的静态权，不能先平均复信道。",
            caveat="h_true 给的是理想 CSI 乐观参考，不等于 Shannon 容量上界；"
                   "容量上界要单独用逐时频注水计算。当前 type1 是 Type-I-style"
                   "列码本子集/贪心多层近似，不是完整矩阵码本枚举。",
            source="本项目 linklevel.compute_precoder；38.214 §5.2.2 为码本边界参考",
            alternatives=["svd_wideband", "dft", "type1（列码本近似）", "mrt"],
        ),
        Algorithm(
            key="rank_adaptation",
            name="Rank 自适应",
            stage="链路自适应",
            choice="遍历 rank 1..4，取 rank × MCS谱效 最大的那个",
            formula="对每个 r：每流功率 P/r → 逐流 SINR → 用户级 SINR（dB 域平均）"
                    " → 查 MCS → SE = r × SE(MCS)；取 argmax",
            why="**这是个真实的权衡，不是秩越高越好。** rank1 全功率压最强流、"
                "BF 增益最大、MCS 最高，但只有一条流；rank4 每流只有 P/4，"
                "弱流把用户级 SINR 拖下去、MCS 掉档，但乘的是 4。最优点通常在中间。",
            caveat=f"现网锚点是平均 rank {FIELD_ANCHORS['avg_rank']}。"
                   "仿真跑出来明显偏高（比如 3.9）时，先审计每 RB 功率分摊、"
                   "几何工作点锚点与 MCS 封顶，再判断是否真是信道更好。",
            source="用户 2026-08-02 给的现场算法",
        ),
        Algorithm(
            key="noise_reference",
            name="噪声功率口径",
            stage="链路自适应",
            choice="锚定预数字波束平均系数功率：I+N = E[|H|²]·P / 10^(几何SINR/10)",
            formula="P_tx,RB=P_tx,total/N_RB；SNR_RB=P_rx,total−10log10(N_RB)−N_RB,dBm",
            why="ChannelHub 的 first-party 几何 SNR/SIR/SINR 是**预数字波束、每 RB**口径："
                "阵元方向图与固定子阵增益已经进链路预算，64 端口数字预编码增益仍留在 H。"
                "链路级以 E[|H|²] 锚定损伤后，SVD/Type-I/EBF 的数字 BF 增益只会贡献一次。",
            caveat="若错用 rank-1 后波束 σ₁² 锚点，会把 H 中真实的数字 BF 增益抵消；"
                   "若忘记总载波功率按 RB 均分，则 273 RB 会被高估 10log10(273)=24.36 dB。"
                   "外部/旧数据源未声明 signal_reference 时，不能假定同一口径。",
            source="ChannelHub internal_sim.py / sionna_rt.py；SuperRAN linklevel.prebeam_reference_power",
        ),
        Algorithm(
            key="mcs_selection",
            name="MCS 选择（单码字）",
            stage="链路自适应",
            choice="表 3（预置 20B NewTx 曲线，28 档）+ 10% 首传 BLER",
            formula="逐 RB SINR → RBG 内线性平均 → RBG 间与流间 dB 域平均"
                    " → 用户级 SINR → 选满足目标 BLER 的最高 MCS",
            why="**一个用户一个 TTI 只发一个码字**，同一个 MCS 覆盖全部 RB 与全部流。"
                "所以必须先把 SINR 压成一个数再查表，不能逐 RB 查完再平均——"
                "后者等于假设每 RB 能用不同 MCS，系统性高估。两者的差正是单码字的损失。",
            caveat="dB 域平均比线性平均保守：实测半好半坏（+20/−20 dB）的信道，"
                   "dB 域给 0 dB、线性给 17 dB，**差 17 dB**。深衰的 RBG 会把整个码字拖下去。",
            source="38.214 §5.1.3；预置 20B 曲线（bler_data_20b.py，含 SHA-256）",
            alternatives=["表 1/2：38.214 标准表 + 分析 BLER 模型"],
        ),
        Algorithm(
            key="receiver",
            name="接收机",
            stage="接收",
            choice="MMSE（把干扰当白噪声）/ IRC（用完整空间协方差打零陷）",
            formula="SINR_k = 1/[(I + (P/rank)·G^H R_n^{-1} G)^{-1}]_kk − 1；"
                    "MMSE 取 R_n=(N0+I_tot/N_rx)·I，IRC 取 R_n=N0·I+R_uu",
            why="**公式相同，区别全在 R_n。** IRC 的增益只能来自干扰的非白性——"
                "干扰真白的时候两者必然重合。",
            caveat="实测 ChannelHub 的**单个干扰小区信道是秩 1 的**（σ₂/σ₁ 中位 4.0e−8）。"
                   "3 个秩 1 干扰 + 4 根收天线 = 刚好全零陷得掉，"
                   "**这是 IRC 最有利的工况，实测 +2.37 bit/s/Hz 偏乐观**。"
                   "引用时必须带上 interference_rank。"
                   if multi else "单小区场景下没有邻区干扰，IRC 与 MMSE 等价。",
            source="经典 MMSE-IRC；本项目 linklevel.post_equalizer_sinr",
        ),
        Algorithm(
            key="mu_pairing",
            name="MU-MIMO 配对（EZF）",
            stage="多用户",
            choice="每用户 SVD 取前 rank 流 → 堆叠后对配对用户 ZF 迫零",
            formula="H̃ = 各用户等效行向量堆叠；W ∝ H̃^H(H̃H̃^H)^(−1)，逐列归一；"
                    "功率按流均分（rank2 的用户拿 2 份）",
            why="EZF：先用各自的 SVD 把用户内的流分开，再用 ZF 把用户间的干扰清零。"
                "**MU 每用户最多 rank 2**（工程约束），SU 可以到 rank 4。",
            caveat="**预编码矩阵只能表示方向，功率必须单独给。** 合成一个全局标量会退化成"
                   "信道求逆功控——ZF 满足 H̃W=c·I，所有用户接收电平被强行拉平，"
                   "弱用户吃掉大部分功率。症状是等效信道范数 12.0/11.7/10.7/7.2 的四个用户"
                   "拿到一模一样的谱效、Jain 公平度恒等于 1.000000。",
            source="用户 2026-08-02 给的现场算法；Sionna rzf_precoding_matrix（逐列归一）",
        ),
        Algorithm(
            key="su_mu_adaptation",
            name="SU/MU 自适应",
            stage="多用户",
            choice="PF排序后分别构造完整SU/MU计划，比较队列封顶的useful payload bytes",
            formula="B_useful=Σmin(queue,TBS)；SU能清空全部队列时强制SU，否则MU≥SU才选MU",
            why="SU赢在无MU干扰且可到rank4；MU赢在同一RBG并行两位rank2用户。"
                "使用useful bytes可自动剔除超出业务包的padding，并保留实际队列收益。",
            caveat="MU伙伴不是第一个相关性过门者：PF只固定anchor，全部伙伴仍需经过"
                   "pair link、相关性、层数、预测BLER和useful bytes/RBG评分。"
                   "当前仅支持两用户、每用户rank2。",
            source="当前 experience_v2 已确认调度合同",
        ),
        Algorithm(
            key="scheduler",
            name="调度器",
            stage="系统级",
            choice="比例公平 PF",
            formula="度量 = R_inst / R_avg；R_avg(t+1) = (1−1/Tc)·R_avg(t) + (1/Tc)·R_served(t)",
            why="Tc 决定公平的时间尺度：太小接近 max-C/I（只喂近点用户），"
                "太大接近轮询（不利用信道起伏）。",
            caveat="**PF 有个经典病理**：一个永远发不成功的用户 R_avg 趋近 0、度量发散，"
                   "调度器会死盯着他。实测这能把全小区首传 BLER 从 0.011 拖到 0.811。"
                   "现在覆盖外的用户（SINR 够不到 MCS 0 门限）会被剔出调度并单独报出。",
            source="经典 PF；本项目 system.simulate",
            alternatives=["max_ci（吞吐最大但极不公平）", "rr（轮询）",
                          "edf（包长感知，小包优先，牺牲公平性）",
                          "qos_pf_edf（EPF+EDF 混合，需标定量纲）"],
        ),
        Algorithm(
            key="traffic",
            name="话务模型",
            stage="系统级",
            choice="FTP Model 3（泊松到达固定大小文件）",
            why="**评价体验速率的标准话务模型。** full buffer 下体验速率没有意义——"
                "缓冲区永不空，没有 burst 边界可言。",
            caveat="到达率太高会积压，此时体验速率反映的是容量上限而不是用户体验。"
                   "积压超过到达量 15% 时会主动告警。",
            source="3GPP TR 36.814 Annex A.2.1.3.1 / TR 38.802 §A.2.1.3",
            alternatives=["full_buffer", "cbr"],
        ),
        Algorithm(
            key="experienced_throughput",
            name="体验速率口径",
            stage="系统级",
            choice="唯一口径：experience_v2 的 DRB busy-period + FIFO 到达对象",
            formula="large: (ΣACK pieces − final piece)/(T_penultimate_ACK − T_first_TX)；"
                    "small: (TBVol−PaddingVol)/fractional-slot",
            why="experience_v2 把标准 burst 吞吐、到首次调度的等待、每个 FIFO 到达对象"
                "的完成时延/PDB 分开记录；同一个 DRB busy period 可合并多个到达对象。",
            caveat="历史的 trim（掐尾/掐头去尾）已随 legacy 容量路径下线。NACK 字节留在"
                   "队列，下一次仍按 NewTx 判错；当前没有 HARQ 软合并。小区体验速率是"
                   "用户均值而非求和。**full_buffer（容量口径）下 busy period 永不结束，"
                   "本 KPI 按定义无定义、报 None。**",
            source="ETSI TS 28.552 V19.5.0；本项目 experience.py",
            alternatives=["fractional_slot", "exclude"],
        ),
        Algorithm(
            key="tx_rx_sinr",
            name="发送侧 / 接收侧 SINR 分离",
            stage="系统级",
            choice="发送侧 = Γ(MCS(CQI)) + BF Gain；接收侧 = 当前真实信道的瞬时 post-MMSE SINR",
            formula="MCS_base = select_mcs(Γ(MCS(CQI)) + BF Gain)；"
                    "MCS_tx = floor(MCS_base + OLLA)；"
                    "BLER = curve(MCS)@SINR_rx",
            why="**这是基站可知量与接收端真实量的边界。** CQI 给出终端测得、长期滤波的"
                "宽带基线；基站再用自己的 SRS/PMI 信道估计计算瞬时 BF Gain。接收端则在"
                "当前真实信道与干扰下判错，二者的偏差由 OLLA 用 ACK/NACK 闭环校正。",
            caveat="CQI 是长期宽带量，BF Gain 是瞬时量，不能把接收侧真实 SINR 的长期均值"
                   "偷换成发送侧输入——那会向调度器泄露 oracle 信息。开 CSI 老化时，"
                   "BF Gain 必须来自陈旧 SRS；Type-I 码本基线的 BF Gain 定义为 0 dB。",
            source="本项目 algo_defs2 / system.build_link_tables / linkadapt",
        ),
        Algorithm(
            key="olla",
            name="OLLA 外环链路自适应",
            stage="系统级",
            choice="ACK +0.01 MCS / NACK −0.09 MCS（项目基线）",
            formula="稳态 BLER → step_up / (step_up + |step_down|) = 0.01/0.10 = 10%",
            why="外环用 ACK/NACK 把发送端不知道的那部分干扰补偿掉。"
                "步长比例决定稳态 BLER，绝对大小决定收敛速度。",
            caveat="**步长小收敛很慢**：每次 NACK 只压 0.09 MCS，而发送档会 floor，"
                   "小步长常常压不动一档。短仿真里 BLER 可能还未回到 10%。"
                   "要看稳态结论就加长时长；要快收敛就临时调大步长"
                   "（比例不变则稳态 BLER 不变）。未收敛时结果里会主动告警。",
            source="用户 2026-08-02 给的现网基线",
        ),
        Algorithm(
            key="neighbor_load",
            name="邻区负载",
            stage="系统级",
            choice="按 PRB 利用率折算干扰（默认 30%）",
            formula="SINR' = S / (η·I + N)，SIR' = SIR / η；"
                    "等价于 IoT'_lin = 1 + η·(IoT_lin − 1)",
            why="ChannelHub 的几何 SINR 按**所有邻区都在发**算，等于 100% PRB 利用率；"
                "5G 典型是 10%/30%/50%。邻区没发的 PRB 上本小区根本不受干扰。",
            caveat="**SINR 和 SIR 必须一起折算。** 只改 SINR 会让 IoT = SIR/(SIR−SINR) "
                   "拿两个不同口径的量算，直接报 inf。"
                   "另外现场说密集城区 IoT 常 >20 dB，实测 100% 负载下是 32.9 dB、"
                   "10% 负载下只有 22.9 dB——**反过来说明那些小区的邻区负载接近满**。",
            source="5G 典型 PRB 利用率；本项目 system.apply_neighbor_load",
        ),
        Algorithm(
            key="rbg_granularity",
            name="仿真粒度",
            stage="信道生成",
            choice="默认链路表按 17 RBG；启用 RB 功控时保留 272 RB 到 SINR 后再聚合",
            formula="功控关闭：H_RBG[b] = H_RB[16b+8]；功控开启：逐 RB 施加 q[c,r]、"
                    "计算 MMSE SINR，再在线性域聚合到 RBG",
            why="调度、MCS 与按需资源分配的基本粒度是 RBG，默认取每组中心 RB 可避免"
                "对信道先平均而人为改善条件数。**RB 功率控制是明确例外**：功率变化会"
                "同时改变本小区信号和邻区干扰，必须保留全部 272 RB 才能精确耦合。",
            caveat="中心 RB 代表法不支持 RBG 内频选调度或导频图样研究；开启 RB 功控后"
                   "不能再走中心 RB 快捷路径。当前宽带有效 SINR仍采用 dB 算术平均，"
                   "尚未校准 EESM/MIESM。",
            source="本项目 mumimo.rbg_reduce / power_control / experience",
        ),
        Algorithm(
            key="two_phase",
            name="两相架构（性能）",
            stage="系统级",
            choice="Phase A 预计算单用户表与 MU pair（配对）链路表；"
                   "Phase B 按 TTI 纯查表、排队与记账",
            why="矩阵分解、预编码和干扰计算放在 Phase A：逐 UE、逐快照、逐 rank 生成 SU 表，"
                "并为候选两用户组合生成真实 MU pair 表。Phase B 只做 PF 排序、"
                "SU/MU 计划比较、按需 RBG 分配、BLER 抽样与 FIFO/KPI 更新。",
            caveat="历史的 MU/SU 标量比值（se_ratio_legacy）已随 legacy 容量路径下线；"
                   "现在一律要求完整 pair 链路表，边界固定为两用户、每用户 rank2、"
                   "ZF/RZF。扩到一般 rank/多用户需扩充表维度与候选裁剪策略。",
            source="本项目 system.build_link_tables / mumimo.build_mu_pair_tables / experience",
        ),
        Algorithm(
            key="harq",
            name="HARQ",
            stage="系统级",
            choice="两个 profile 均为每 TB 最多一次重传；默认 IR，可选 CC",
            why="初传与重传都只消费预置 NewTx 曲线：IR 用半谱效等效 MCS，CC 用原 MCS "
                "+3.0103 dB；空口 MCS/RBG 数/rank/TBS 冻结。",
            caveat="这是 BLER 级工程抽象，尚未展开 RV、LLR、并行 process 与标准 timing。"
                   "重传失败字节留队，后续作为新 TB，而不是第二次重传。",
            source="预置表的通用曲线与一次 CC/IR 口径",
        ),
    ]


def algorithm_list(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """这次配置下实际生效的算法清单。"""
    return [a.as_dict() for a in _algorithms(cfg)]


def stages() -> list[str]:
    return ["信道生成", "发射", "接收", "链路自适应", "多用户", "系统级"]


# ---------------------------------------------------------------------------
# 对标量的推导过程 —— 每一步都摊开，供人工核对
# ---------------------------------------------------------------------------
def derivations(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """峰值速率/谱效等对标量的**逐步推导**，供用户亲自核对。

    **只给一个"29.63 vs 30.0，偏差 −1.2%"是不可核对的。** 这里把每一步的
    输入、公式、中间结果全列出来，任何一步对不上都能当场指出来。
    数字全部从代码现算，不是抄进来的常量。
    """
    from . import hardware as hw  # noqa: PLC0415
    from . import linkadapt as la  # noqa: PLC0415

    cfg = cfg or {}
    out: list[dict[str, Any]] = []

    # --- 峰值谱效 ---
    m27 = la.MCS_TABLES[3][27]
    out.append({
        "key": "peak_se",
        "name": "峰值谱效",
        "result": f"{4 * m27.se:.3f} bit/s/Hz",
        "reference": "30.0 bit/s/Hz",
        "ref_src": "ITU-R M.2412 / IMT-2020 最低要求，DL 峰值谱效",
        "steps": [
            ("最高档 MCS", "表 3 的 MCS 27",
             f"调制阶数 q_m = {m27.q_m}（{2 ** m27.q_m}QAM），"
             f"目标码率 R = {m27.r_1024:.0f}/1024 = {m27.rate:.4f}"),
            ("单流谱效", "SE₁ = q_m × R",
             f"{m27.q_m} × {m27.rate:.4f} = {m27.se:.4f} bit/s/Hz"),
            ("最大层数", "SU 最多 4 流",
             f"终端 {hw.COMPANY_UE_RX_ANT}R，rank 上限 = 4"),
            ("峰值谱效", "SE_peak = rank × SE₁",
             f"4 × {m27.se:.4f} = {4 * m27.se:.4f} bit/s/Hz"),
            ("与参考对比", "ITU-R 要求 30.0",
             f"偏差 {(4 * m27.se - 30.0) / 30.0 * 100:+.1f}%。"
             f"差的这一点来自码率——IMT-2020 的 30 是按 q_m=8、R=0.9375 "
             f"（=960/1024）算的，表 3 最高档是 {m27.rate:.4f}"),
        ],
    })

    # --- 峰值速率 ---
    n_prb, oh = 273, 0.14
    ts = 1e-3 / 14 / 2
    r_max = 948 / 1024
    peak = 4 * 8 * r_max * (n_prb * 12 / ts) * (1 - oh)
    re_tti = hw.COMPANY_NUM_RB * 12 * 12
    tbs = la.transport_block_size(re_tti, m27.rate, m27.q_m, layers=4)
    out.append({
        "key": "peak_rate",
        "name": "峰值速率",
        "result": f"{tbs / 0.5e-3 / 1e9:.3f} Gbps",
        "reference": f"{peak / 1e9:.3f} Gbps",
        "ref_src": "3GPP TS 38.306 §4.1.2 峰值速率公式",
        "steps": [
            ("标准公式", "R = v · Q_m · f · R_max · (N_PRB×12 / T_s) · (1−OH)",
             f"v=4 层，Q_m=8，f=1（无缩放），R_max={r_max:.4f}（948/1024），"
             f"N_PRB={n_prb}，T_s={ts * 1e6:.2f} μs（30 kHz，14 符号/0.5 ms），"
             f"OH=0.14（DL FR1 开销）"),
            ("标准公式结果", "代入",
             f"4 × 8 × {r_max:.4f} × ({n_prb}×12 / {ts:.3e}) × {1 - oh:.2f} "
             f"= {peak / 1e9:.4f} Gbps"),
            ("本仿真器的 RE 数", "N_RE = N_RB × 12 子载波 × 12 数据符号",
             f"{hw.COMPANY_NUM_RB} × 12 × 12 = {re_tti} 个 RE/TTI"
             f"（14 符号扣掉 2 个给 DM-RS 与控制）"),
            ("按 38.214 §5.1.3.2 算 TBS", "transport_block_size(N_RE, R, q_m, layers=4)",
             f"= {tbs} bit"),
            ("折成速率", "TBS / TTI 时长",
             f"{tbs} / 0.5 ms = {tbs / 0.5e-3 / 1e9:.4f} Gbps"),
            ("与公式对比", "两条独立路径",
             f"偏差 {(tbs / 0.5e-3 - peak) / peak * 100:+.1f}%。"
             f"差异来自 RB 数（{hw.COMPANY_NUM_RB} vs {n_prb}）与开销口径——"
             f"我们按 12/14 符号扣，标准按固定 OH=0.14 扣"),
        ],
    })

    # --- 小区谱效的 TDD 归一 ---
    pat = str(cfg.get("tdd_pattern", "DDDSU")).upper() or "DDDSU"
    dl_ratio = (pat.count("D") + 0.7 * pat.count("S")) / len(pat)
    out.append({
        "key": "tdd_normalize",
        "name": "小区谱效的 TDD 归一",
        "result": f"下行占比 {dl_ratio:.4f}",
        "reference": "ITU 的小区谱效是按全下行定义的",
        "ref_src": "ITU-R M.2412 Dense Urban DL 平均小区谱效 7.8 bit/s/Hz/TRxP",
        "steps": [
            ("TDD 图案", f"{pat}",
             f"{pat.count('D')} 个 D + {pat.count('S')} 个 S + "
             f"{pat.count('U')} 个 U，周期 {len(pat)} 个时隙"),
            ("S 时隙折算", "按 0.7 个下行算",
             "S 时隙大部分符号是下行，剩下给 GP 和上行导频"),
            ("下行占比", "(D + 0.7×S) / 周期",
             f"({pat.count('D')} + 0.7×{pat.count('S')}) / {len(pat)} = {dl_ratio:.4f}"),
            ("归一", "仿真谱效 / 下行占比",
             f"仿真里一秒只有 {dl_ratio:.0%} 的时隙能发下行，"
             f"而 ITU 的参考值是按全下行定义的，所以要除以 {dl_ratio:.4f} 才可比"),
        ],
    })

    # --- 总载波功率到每 RB 工作点 ---
    out.append({
        "key": "noise_ref",
        "name": "总载波功率 → 每 RB → 链路级工作点",
        "result": "P_RB=P_total/N_RB；I+N=E[|H|²]·P/10^(SINR_geo/10)",
        "reference": "273 RB 的功率分摊项为 24.36 dB",
        "ref_src": "ChannelHub first-party 信号参考契约 + linklevel 单一真源",
        "steps": [
            ("输入功率", "P_tx,total",
             "配置的 tx_power_dbm 是整个活动载波的导通总功率，不是每个 RB 都有一份"),
            ("每 RB 热噪声", "N_RB,dBm = −174 + 10log10(12·SCS) + NF",
             "noise_power_dBm 明确定义为一个活动 RB 的 kTB+NF"),
            ("均匀频域分功率", "P_tx,RB,dBm = P_tx,total,dBm − 10log10(N_RB)",
             "100 MHz / 30 kHz 的 273 RB 对应 −24.36 dB；漏掉会整体高估工作点"),
            ("几何信号参考", "pre-digital-beam per-RB",
             "阵元/固定子阵增益已在 P_rx,total 中，数字多端口 BF 增益仍在归一化 H 中"),
            ("链路级反标", "I+N = E[|H|²]·P / 10^(SINR_geo/10)",
             "预编码器再作用于 H；SVD、Type-I、EBF/PEBF/NEBF 的增益或损失只算一次"),
            ("反例门", "禁止用 E[σ₁²] 当默认锚点",
             "rank-1 后波束功率是诊断量；拿它反标损伤会人为抵消数字 BF 增益"),
        ],
    })
    return out
