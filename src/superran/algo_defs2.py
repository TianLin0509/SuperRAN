"""算法族定义（续）：信道生成、发射、话务与 KPI 侧。

和 :mod:`algo_defs` 拆成两个文件纯粹是为了每个文件不至于太长，
结构与约定完全一致——每族列全部可选实现、标出当前采用、配一张流程图。
"""
from __future__ import annotations

from typing import Any

from .algo_defs import Family, Flow, Option


def _antenna(cfg: dict[str, Any]) -> Family:
    from . import hardware as hw  # noqa: PLC0415

    n_bs = int(cfg.get("num_bs_tx_ant", 64) or 64)
    panel = list(cfg.get("bs_panel") or [])
    profile = hw.company_profile_for_panel(panel)
    if profile is None and not panel:
        profile = {64: "64t", 256: "256t"}.get(n_bs)
    mode = str(cfg.get("antenna_model_mode") or (
        "effective_subarray" if profile is not None else "legacy_64"))
    m = 3 if profile == "64t" else 6 if profile == "256t" else int(
        ((cfg.get("bs_antenna") or {}).get("fixed_vertical_subarray") or {}).get(
            "elements_per_rf_port", 1))
    n_ae = n_bs * m
    if profile == "64t":
        shape = "8H×4V×2pol"
    elif profile == "256t":
        shape = "16H×8V×2pol"
    else:
        shape = f"{n_bs} 端口"
    return Family(
        key="antenna_model",
        name="天线阵列模型",
        stage="信道生成",
        current=mode,
        config_key="num_bs_tx_ant",
        intro=f"同样写「{n_bs}T」，把数字端口当独立阵元，还是按真实馈电投影，"
              "得到的是两套不同物理模型。64T/256T 的新数据共享同一编号合同。",
        formula=(rf"H_{{eff}} = F^H H_{{phys}}, \quad "
                 rf"F \in \mathbb{{C}}^{{{n_ae} \times {n_bs}}}"),
        caveat="端口换序本身必须是严格置换：H、W、F 一起换序后有效信道应逐数等价。"
               "旧 64T 数据按 h_v_pol + bottom_to_top 显式兼容；"
               "新数据和新模块只允许 pol_h_v + top_to_bottom。未知面板不得猜 1 驱 N。",
        source="ChannelHub phy_sim/effective_array.py",
        options=[
            Option("effective_subarray", f"effective_subarray（1 驱 {m} 真实阵列）",
                   formula=r"d_H = 0.5\lambda, \quad d_V = 0.67\lambda, \quad "
                           rf"d_{{RF,V}} = {m} \times 0.67\lambda",
                   summary=(f"{n_bs} 个 RF 端口，每端口固定驱动垂直相邻 {m} 个阵子，"
                            f"共 {n_ae} 阵子"),
                   detail=(f"真实 AAU：{shape} = {n_bs} 个 RF 端口。"
                           "64T/256T 都按 pol_h_v + top_to_bottom 展平，"
                           "编号公式统一为 r=p·N_H·N_V+h·N_V+v。"),
                   when="已确认的 8×4×2 64T 或 16×8×2 256T 面板自动启用",
                   cost="与 legacy 相同（用等效阵列快路径）"),
            Option("legacy_64", "legacy_64（独立阵元）",
                   formula=r"d_H = d_V = 0.5\lambda",
                   summary="把 64 个端口当 64 个独立阵元，间距一律半波长",
                   detail="ChannelHub 的历史默认。没有 1 驱 3 的耦合、没有栅瓣，"
                          "自由度被高估。",
                   when="面板馈电结构未确认；或显式对照历史结果",
                   cost="最省"),
            Option("physical_reference", f"physical_reference（真跑 {n_ae} 阵子）",
                   summary=f"按 {n_ae} 个物理阵子建模再用耦合矩阵投影回 {n_bs} 端口",
                   detail="慢路径参考实现；是否与快路径等价必须由当前版本的数值门验证，"
                          "不能把历史测得误差当成永久承诺。",
                   when="要验证快路径没写错",
                   cost="慢很多，只用于校验"),
        ],
        flow=Flow(steps=[
            ("按物理阵子建信道", f"{n_ae} 个阵子各自的 38.901 信道系数"),
            ("组耦合矩阵 F", f"每个 RF 端口驱动垂直相邻 {m} 个阵子，F 是 {n_ae}×{n_bs} 稀疏阵"),
            ("投影到 RF 端口", f"H_eff = F^H · H_phys，得到 {n_bs} 端口等效信道"),
            ("锁定布局", "pol_h_v + top_to_bottom；旧布局只经显式置换进入"),
        ], branches=[(1, "面板馈电结构未确认", "落回 legacy_64，不猜 1 驱 N")]),
    )


def _precoder_su() -> Family:
    return Family(
        key="precoder_su",
        name="单用户预编码",
        stage="发射",
        current="svd",
        config_key="precoder",
        intro="把要发的流映射到发射端口。代码名 svd 的实现是协方差特征波束；"
              "单快照时等价瞬时 SVD，多快照时是一组静态权。",
        formula=r"R_f=E_t[H_{tf}H_{tf}^H], \quad W_f=\operatorname{eigvec}_{1:r}(R_f)",
        caveat="用 h_true 是理想 CSI 乐观参考，但**不是 Shannon 容量上界**；"
               "真正上界由逐时频注水容量单独给出。",
        source="本项目 linklevel.compute_precoder；38.214 §5.2.2（码本边界）",
        options=[
            Option("svd", "逐 RB 协方差特征预编码",
                   formula=r"W_f = \operatorname{eigvec}_{1:r}(E_t[H_{tf}H_{tf}^H])",
                   summary="T=1 等价瞬时 SVD；T>1 为相位不敏感的静态协方差波束",
                   detail="每 RB 对时间样本的功率协方差做特征分解，不能先平均复信道。"
                          "理想 CSI 时是乐观参考，但不等于逐时隙最优或容量上界。",
                   when="要一个理想 CSI 特征波束参考",
                   cost="逐 RB 一次 SVD"),
            Option("svd_wideband", "宽带 SVD",
                   summary="全带宽共用一组预编码，而不是逐 RB 各算各的",
                   detail="更接近真实系统（反馈开销受限），比逐 RB SVD 低一些。",
                   when="要看宽带预编码的损失",
                   cost="一次 SVD"),
            Option("type1", "Type-I-style 列码本近似",
                   formula=r"W \in \mathcal{W}_{DFT}, \quad "
                           r"(i_{1,1}, i_{1,2}, i_2) \to W",
                   summary="从 DFT 波束码本里选一个，**含秩自适应**",
                   detail="列集合采用 Type-I 单面板的过采样 DFT/双极化结构；"
                          "多层用增量贪心选列，**尚未枚举 38.214 完整多层矩阵码本**。"
                          "38.214 的 Type I 反馈里 RI 和 PMI 是一起报的，"
                          "所以码本方案必须做秩自适应——早期版本把秩硬定成 max_rank，"
                          "在低秩信道上会输给 rank-1 的 DFT 波束，"
                          "看起来像「码本不如单波束」，其实是没做秩自适应。",
                   when="要贴近真实系统的 CSI 反馈",
                   cost="遍历码本",
                   source="38.214 §5.2.2.2.1"),
            Option("dft", "DFT 波束",
                   summary="固定的 DFT 波束，不看信道细节",
                   detail="最简单的波束赋形，作为下界对照。",
                   when="对照实验",
                   cost="最省"),
            Option("mrt", "MRT（最大比发射）",
                   formula=r"W \propto H^H",
                   summary="共轭匹配，单流时最优",
                   when="rank=1",
                   cost="最省"),
        ],
        flow=Flow(steps=[
            ("取用于预编码的信道", "h_true（上界）或 h_est（真实系统）——**这一步决定结论性质**"),
            ("逐 RB 构造功率协方差", "R_f = E_t[H_tf H_tf^H]，相位翻转不会相消"),
            ("秩判决", "按奇异值门限决定开几流（码本方案也必须做，不能硬定）"),
            ("取前 rank 个发射端特征向量", "W = eigvec_top(R_f)"),
            ("功率归一", "总功率 P=1 在 rank 条流上均分"),
        ], branches=[(1, "用的是 h_est", "预编码不再匹配真实信道，损失即 CSI 代价")]),
    )


def _rbg() -> Family:
    return Family(
        key="rbg_granularity",
        name="仿真粒度",
        stage="信道生成",
        current="rbg",
        config_key="rb_per_rbg",
        intro="默认链路表按 17 个 RBG 建；启用 RB 功率控制时，必须保留全部 272 RB "
              "直到逐 RB SINR 算完后再聚合。两个路径共享 RBG 级调度/MCS，但不能混成同一种近似。",
        formula=(r"\text{off}: H_{RBG}[b]=H_{RB}[16b+8],\qquad "
                 r"\text{on}: \gamma_b=\operatorname{mean}_{r\in b}^{lin}(\gamma_r)"),
        caveat="关闭功控时取中心 RB，避免先平均复信道而人为改善条件数；但它不支持 RBG 内频选"
               "调度或导频图样。开启功控后 q[c,r] 同时改变服务信号和邻区干扰，中心 RB 快捷路径"
               "会漏掉功率起伏，必须走 272-RB 精确路径。first-party 几何 SNR/SIR/SINR 的工作点"
               "是**预数字波束、每 RB**参考面；数字 BF 增益仍由 H 与预编码器贡献一次。"
               "当前宽带有效 SINR 还不是已校准的 EESM/MIESM。",
        source="本项目 mumimo.rbg_reduce / rbg_sinr_db；system.build_link_tables；power_control",
        options=[
            Option("rbg", "中心 RB 快路径（功控关闭）",
                   summary="每个 RBG 取中间那个 RB 作代表",
                   detail="不先平均复信道；保留代表点的空间结构。它是系统链路表默认快路径，"
                          "不是所有谱效 API 的统一口径。",
                   when="默认且 RB 功率控制关闭",
                   cost="272 → 17，SVD 少算 16 倍"),
            Option("rb", "逐 RB 精确路径（功控开启）",
                   summary="逐 RB 耦合 q[c,r]、算 post-MMSE SINR，再在 RBG 内线性平均",
                   detail="一根 RB 抬功率会让本小区该 RB 信号变强，也让邻区同 RB 干扰变强；"
                          "任何先降成 17 行的路径都无法恢复这个耦合。",
                   when="RB 功率控制；未来频选调度 / 导频图样",
                   cost="16 倍的 SVD"),
        ],
        flow=Flow(steps=[
            ("拿到逐 RB 的信道", "[272, BS, UE]"),
            ("判断是否启用 RB 功控", "关闭走中心 RB 快路径；开启则保留 272 行"),
            ("算数字预编码与 post-MMSE SINR", "快路径算 17 行；精确路径算 272 行"),
            ("形成逐 RBG SINR", "快路径每行已是一个 RBG；精确路径对每 16 RB 在线性功率域平均"),
            ("压成单码字宽带 SINR", "跨 RBG 与跨流在 dB 域取算术平均，再查一个 MCS"),
        ], branches=[(2, "RB power control = on", "保留 272 RB，禁止中心 RB 采样")]),
    )


def _traffic() -> Family:
    return Family(
        key="traffic",
        name="话务模型",
        stage="系统级",
        current="ftp3",
        config_key="traffic_model",
        intro="用户什么时候有数据要发、一次发多少。**体验速率这个 KPI 只在有 burst 边界时才有意义。**",
        formula=r"P(\text{到达}) = \lambda \cdot T_{TTI}, \quad "
                r"\text{负载} = \lambda \cdot S_{file} \cdot 8",
        caveat="full buffer 下**体验速率没有意义**——缓冲区永不空、没有 burst 边界。"
               "到达率太高会积压，此时体验速率反映的是容量上限而不是用户体验，"
               "积压超过到达量 15% 会主动告警。",
        source="3GPP TR 36.814 Annex A.2.1.3.1 / TR 38.802 §A.2.1.3",
        options=[
            Option("ftp3", "FTP Model 3",
                   formula=r"\text{到达} \sim \text{Poisson}(\lambda), \quad "
                           r"S_{file} = \text{const}",
                   summary="泊松到达固定大小的文件",
                   detail="3GPP 评价体验速率的**标准话务模型**。到达率控制负载。",
                   when="默认；对标 3GPP 参考值时",
                   cost="最省",
                   source="TR 36.814 Annex A.2.1.3.1"),
            Option("bimodal", "现网两头高中间低",
                   formula=r"P(1\,\text{RBG}) = 0.3, \quad P(N_{RBG}) = 0.3, "
                           r"\quad P(\text{空闲 TTI}) = 0.3",
                   summary="按**占用 RBG 数**分布：小包和满带宽各占 30%，中间均匀",
                   detail="现网口径（用户 2026-08-02）。**这是一次传输占多少频域资源的分布，"
                          "不是文件大小的分布**——两者完全不同。"
                          "小包与大包的体验速率分开报，因为前者由调度时延主导。",
                   when="要贴近现网话务",
                   cost="最省"),
            Option("full_buffer", "full buffer",
                   summary="永远有数据要发",
                   detail="用来测容量上限。**体验速率在这个模型下没有意义。**",
                   when="测小区容量、对标 ITU 的平均小区谱效",
                   cost="最省"),
            Option("cbr", "CBR（恒定速率）",
                   summary="每 TTI 固定字节数到达",
                   when="模拟固定码率业务",
                   cost="最省"),
        ],
        flow=Flow(steps=[
            ("每 TTI 抽一次到达", "伯努利近似泊松，p = λ·T_TTI"),
            ("决定这次的大小", "ftp3 用固定文件大小；bimodal 抽 RBG 数再折算字节"),
            ("进缓冲区", "该 UE 没有活跃 burst 就直接激活，否则排队"),
            ("被调度时扣减", "记录首次/末次被调度的 TTI，供体验速率统计用"),
            ("发完出队", "下一个排队的 burst 接上"),
        ], loop_back=(5, 1, "持续到仿真结束")),
    )


def _exp_thp() -> Family:
    return Family(
        key="experienced_throughput",
        name="体验速率口径",
        stage="系统级",
        current="experience_v2",
        config_key="evaluation_mode",
        intro="先分 profile：legacy_v1 的 trim 只为复现；experience_v2 用 DRB busy-period"
              "事件与 FIFO 到达对象，二者不是同一 KPI 的精度开关。",
        formula=(r"Thp_{large}=\frac{\sum V_{ACK}-V_{final}}{T_{penultimateACK}-T_{firstTX}},\quad "
                 r"T_{small}=\frac{TBVol-PaddingVol}{TBVol}T_{slot}"),
        caveat="**标准 burst 吞吐、到首次调度等待、arrival→completion 与 PDB miss"
               "分层上报。** 小区体验速率是用户均值而非求和。experience_v2 的"
               "NACK 字节留队但不做 HARQ 软合并。",
        source="ETSI TS 28.552 V19.5.0；本项目 experience.py",
        options=[
            Option("experience_v2", "DRB busy-period + fractional small burst",
                   formula=r"buffer: empty\to nonempty\to empty",
                   summary="大 burst 排除末 ACK piece；小 burst 用 TB padding 比例折时长",
                   detail="排队等待单独从 arrival 到 first TX 计算；每个文件是一个 FIFO"
                          "arrival object，即使多个文件落在同一 busy period 也各自算"
                          "等待、完成时延和 PDB。",
                   when="按需 RBG 与大小包混跑",
                   cost="逐 TTI FIFO + RBG 分配",
                   source="TS 28.552 V19.5.0"),
            Option("tail", "legacy 掐尾（仅复现）",
                   formula=r"V \leftarrow V - V_{last}, \quad T \leftarrow T - T_{last}",
                   summary="历史实现，排除清空缓冲区的末 slice",
                   detail="只在 evaluation_mode=capacity 的 legacy_v1 生效；不再冒充"
                          "Rel-19 唯一标准口径。单 slice 小包会不可测。",
                   when="复现旧结果",
                   cost="零",
                   source="本项目 legacy_v1"),
            Option("head_tail", "legacy 掐头去尾",
                   formula=r"T \leftarrow T_{last} - T_{first\_sched}",
                   summary="起点从数据到达挪到**首次被调度**",
                   detail="话务到达但还没被调度的等待时间**不计入分母**"
                          "（用户 2026-08-02 明确）。轻载时两者差别很大。",
                   when="复现旧运营商口径",
                   cost="零"),
            Option("none", "不掐",
                   summary="含清空缓冲区的那个 TTI",
                   detail="**数值虚高，不建议**。只作为理解口径影响的对照。",
                   when="对照实验",
                   cost="零"),
        ],
        flow=Flow(steps=[
            ("识别 profile", "capacity→legacy_v1；experience→experience_v2"),
            ("记录 busy period", "buffer 空→非空开始，ACK 后重新为空才结束"),
            ("记录 FIFO arrival", "每个文件各自保留 arrival/firstTX/completion/PDB"),
            ("计算大/小 burst", "大 burst 排末 ACK；单 TB 小 burst 用 padding 比例折时长"),
            ("按层级聚合", "busy-period 吞吐与 arrival-object 时延分开"),
            ("按小区聚合", "**各用户体验速率的平均，不是求和**"),
        ], branches=[(4, "只有 1 个 slice", "丢弃——小包的固有盲区")]),
    )


def _harq() -> Family:
    return Family(
        key="harq",
        name="HARQ",
        stage="系统级",
        current="ir",
        config_key="harq_combining",
        intro="每个单码字 TB 最多一次重传；空口 MCS、RBG 数、rank 与 TBS 保持不变。",
        formula=(r"P_{\mathrm{res}}=P_{\mathrm{N}}(m,\gamma)"
                 r"P_{\mathrm{R}}(m,\gamma)"),
        caveat="这是系统级工程抽象，不展开 RV、LLR、并行 HARQ process 与标准时序。"
               "原始 ReTx 行只保留用于来源审计，不进入当前系统重传判错。",
        source="预置表口径：通用 NewTx 曲线 + 一次 CC/IR 重传",
        options=[
            Option("ir", "IR 增量冗余（默认）",
                   formula=r"\eta_{eq}=\eta_m/2,\quad m_{eq}=\max\{j:\eta_j\leq\eta_{eq}\}",
                   summary="半谱效映射等效低档 MCS，在原 SINR 上查 NewTx 曲线",
                   detail="m_eq 只用于 BLER 查表；重传空口仍发送初传 m 和相同 RBG 数。",
                   when="默认系统仿真",
                   cost="查表"),
            Option("cc", "CC 追逐合并",
                   formula=r"\gamma_{eq,dB}=\gamma_{dB}+10\log_{10}2",
                   summary="同一 MCS 的 NewTx 曲线，查询 SINR 增加 3.0103 dB",
                   detail="两次等功率重复发送的一次合并近似。",
                   when="对照实验",
                   cost="查表"),
        ],
        flow=Flow(steps=[
            ("首传", "按发送侧 SINR + OLLA 选 MCS，按接收侧 SINR 查 NewTx 曲线"),
            ("抽 ACK / NACK", "伯努利，概率就是查到的 BLER"),
            ("NACK 则进重传队列", "该 UE 在重传完成前不开新的首传"),
            ("唯一一次重传", "IR 半谱效映射或 CC +3.0103 dB；只查 NewTx 曲线"),
            ("结束本次 HARQ", "失败字节留队，后续作为新 TB，不再第二次重传"),
        ],
           branches=[(2, "ACK", "数据交付，OLLA 上调偏置")]),
    )


def _neighbor() -> Family:
    return Family(
        key="neighbor_load",
        name="邻区负载",
        stage="系统级",
        current="jitter",
        config_key="neighbor_prb_util",
        intro="ChannelHub 的几何 SINR 是按**所有邻区都在发**算的，等于 100% PRB 利用率。"
              "真实网络 5G 典型是 10% / 30% / 50%。",
        formula=r"SINR' = \frac{S}{\eta I + N}, \quad SIR' = \frac{SIR}{\eta}, "
                r"\quad IoT'_{lin} = 1 + \eta (IoT_{lin} - 1)",
        caveat="**SINR 和 SIR 必须一起折算。** 只改 SINR 会让 "
               "IoT = SIR/(SIR−SINR) 拿两个不同口径的量算，直接报 inf。"
               "另外实测现网密集城区 IoT &gt;20 dB 对应的是**接近满负载**："
               "100% 负载下 32.9 dB、10% 负载下只有 22.9 dB。"
               "<br>**当前只支持全网统一值**（用户 2026-08-03 定）。"
               "<br>**这个限制的原因 2026-08-07 被推翻了一半。** 原来写的是"
               "「几何 SIR 只给聚合量、拿不到逐邻区贡献」——存下来的确实只有聚合量，"
               "但分解是可恢复的：干扰求和式是 "
               "<code>I = Σ_k rx_lin[k]·N_ant·avg_leak_k</code>，而全码本平均下 "
               "<code>avg_leak</code> 跨小区是常数（Parseval），"
               "所以<b>在期望意义上逐小区份额精确等于 RSRP 份额</b>；"
               "ChannelHub 已经算出了全部小区的 RSRP，只是 SuperRAN 的"
               "标量字段过滤把这个数组丢了，补上只要约 168 字节/样本。"
               "<br>但**单次实现下这个分解不成立**："
               "<code>n_dl_sched = max(1, round(ues_per_cell·pdsch_load))</code> "
               "在默认预设下恒等于 1，每个干扰小区只随机抽<b>一个</b>波束，"
               "实测由此带来 <b>4.74 dB</b> 的抽签噪声。"
               "**要做逐小区负载，得先把这个解决掉。**",
        source="5G 典型 PRB 利用率；本项目 system.apply_neighbor_load",
        options=[
            Option("scaled", "按 PRB 利用率线性折算",
                   formula=r"I' = \eta I, \quad N' = N",
                   summary="干扰按利用率缩放，噪声不变",
                   detail="邻区没在发的那些 PRB 上，本小区用户根本不受干扰。"
                          "η=1 时退化成原来的 full buffer 行为。",
                   when="默认（0.3）",
                   cost="零"),
            Option("jitter", "带 ±5% 抖动的线性折算",
                   formula=r"\eta_s \sim \mathcal{U}\big(0.95\,\eta,\; 1.05\,\eta\big)",
                   summary="每个快照抽一份自己的利用率",
                   detail="恒定负载会让所有快照的干扰**完全一样**，结果比现网干净。"
                          "真实网络的负载逐 TTI 就在抖。抖动是乘性的，"
                          "0.3 → [0.285, 0.315]。<b>这是当前默认</b>"
                          "（用户 2026-08-03：「实际结果可以在配置值 ±5% 范围内波动」）。",
                   when="默认",
                   cost="零"),
            Option("full", "full buffer（η = 1）",
                   summary="所有邻区都在发",
                   detail="ChannelHub 几何 SINR 的原始假设。**要复现现网 IoT &gt;20 dB "
                          "就该用接近这个值。**",
                   when="对标现网高干扰场景",
                   cost="零"),
        ],
        flow=Flow(steps=[
            ("拿到几何 SINR 与 SIR", "都来自 ChannelHub 的同一次几何计算，口径一致"),
            ("反推干扰与噪声", "令 S=1：I = 1/SIR，N = 1/SINR − I"),
            ("按利用率缩放干扰", "I' = η·I，噪声不动"),
            ("重算 SINR 与 SIR", "SINR' = 1/(η I + N)，SIR' = SIR/η —— **必须一起改**"),
            ("再算 IoT", "用折算后的同口径两个量"),
        ], branches=[(1, "单小区（SIR 是 49.9 哨兵）", "没有干扰可折算，原样返回")]),
    )


def _two_phase() -> Family:
    return Family(
        key="two_phase",
        name="两相架构（性能）",
        stage="系统级",
        current="table_lookup",
        config_key="",
        intro="十万个 TTI 的主循环里**不能有任何矩阵运算**。"
              "把贵的都挪到第一相，主循环只查表。",
        formula=r"O(N_{UE} \cdot N_{snap} \cdot N_{rank}) \text{ 次 SVD} "
                r"+ O(N_{TTI} \cdot N_{UE}) \text{ 次查表}",
        caveat="**MU 在主循环里是标量近似**：逐 TTI 真做配对要每 TTI 做 SVD + 矩阵求逆，"
               "跑不完。建表阶段用真实的 su_mu_adaptation 测出 MU/SU 聚合比值，"
               "主循环按 ratio/K 折算。返回值带逐快照比值与离散度——"
               "实测离散度 3.7%~13.1%，**超过 30% 就不该用标量**。",
        source="本项目 system.build_link_tables / simulate",
        options=[
            Option("table_lookup", "两相：建表 + 查表",
                   summary="SVD 只在第一相做，主循环纯查表加算术",
                   detail="实测 **100000 TTI × 8 UE 只要 0.38 秒**。"
                          "把 SVD 放进主循环的话同规模要几十分钟。",
                   when="默认",
                   cost="第一相 0.55 秒，第二相每 10 万 TTI 0.38 秒"),
            Option("per_tti", "逐 TTI 全算（未实现）",
                   summary="每个 TTI 重新做 SVD 与配对",
                   detail="最准，但十万 TTI 跑不完。**如果哪天要精确的逐 TTI MU 配对，"
                          "得先把这一层的性能问题解决掉。**",
                   when="不可用",
                   cost="慢两个数量级"),
        ],
        flow=Flow(steps=[
            ("第一相：逐 UE 逐快照", "对每个 rank 1..4 算 SINR / MCS / 谱效，存成表"),
            ("第一相：测 MU/SU 比值", "在若干快照上跑真实的 SU/MU 自适应，取中位数"),
            ("第一相：判覆盖外", "用户级 SINR 够不到 MCS 0 门限的快照标出来"),
            ("第二相：TTI 主循环", "只读表 + 算 PF 度量 + 更新缓冲区，**无矩阵运算**"),
            ("第二相：BLER 查表", "按预置源曲线 0.05 dB 网格缓存，不降采样瀑布区"),
        ], branches=[(2, "MU 比值离散度 > 30%", "标量近似不成立，结果里告警")]),
    )


def _csi_aging() -> Family:
    return Family(
        key="csi_aging",
        name="CSI 反馈时延与老化",
        stage="发射",
        current="srs_hop_17",
        config_key="srs_period_ms",
        intro="**基站永远不知道「现在」的信道。** TDD 下行靠互易性从上行 SRS 取 CSI，"
              "所以从探测到发送之间隔着一整条时延链：SRS 发送 → 信道估计 → "
              "预编码计算 → PDSCH 发送。这段时间信道一直在变。"
              "平台在此之前默认零时延完美 CSI——预编码与评估用同一个矩阵，"
              "SVD 永远精确匹配、ZF 零陷永远打得准，"
              "**这系统性地高估 MU 增益**，因为 MU 的全部收益就建立在零陷打得准上。",
        formula=r"W = \mathrm{SVD}(H_{t-\tau}), \quad "
                r"\mathrm{SINR}_k = \frac{1}{\left[\left(I + "
                r"\tfrac{P}{r} (H_t W)^H (H_t W)\right)^{-1}\right]_{kk}} - 1",
        caveat="**零时延时这套公式必须逐位退化成 σ_k²·P/rank/σ_n²**，"
               "也就是原来的 su_rank_adaptation 用的那个特征值公式——"
               "因为那时 H_t W = UΣ_r 是对角的。这条恒等式是老化模型的地基，"
               "不成立就说明它是叠加上去的第二套物理，任何「老化损失」都不可解释。"
               "test_csi_aging 第 1 节实测最大偏差 0 dB。"
               "<br>**另一个极易写错的地方：rank 必须由基站按自己的陈旧 CSI 选。** "
               "拿真实 SINR 去挑 rank 等于让基站预知信道，它会自动避开老化最狠的 rank，"
               "损失被凭空抹掉一大半。",
        source="38.211 §6.4.1.4.3 与 Table 6.4.1.4.3-1；跳频序列直接调 ChannelHub 的 "
               "srs_rb_indices，不自己重写",
        options=[
            Option("srs_hop_17", "SRS 跳频（C_SRS=63，17 跳 × 16 RB）",
                   formula=(r"\Delta_{\mathrm{CSI}}(k,t) = t - "
                            r"t_{\mathrm{SRS,last\,usable}}(k,t)"),
                   summary="每次 SRS 只探 1 个 RBG，17 跳扫完全带",
                   detail="38.211 Table 6.4.1.4.3-1 的 C_SRS=63 行："
                          "m_SRS=(272,16,8,4)、N=(1,17,2,2)。取 B_SRS=1 时"
                          "每次 SRS 占 <b>16 RB，正好 1 个 RBG</b>，"
                          "要 <b>17 跳</b>才扫完 272 RB——和本项目的 17 RBG × 16 RB "
                          "载波配置 1:1 对上。"
                          "<br><b>这是老化的主导项</b>：T_SRS=10 ms 时全带扫一遍要 "
                          "<b>170 ms</b>，某个 RBG 的 CSI 陈旧时长在 0~160 ms 之间轮转，"
                          "平均 80 ms。而 2.6 GHz、30 km/h 的相干时间只有约 3 ms。"
                           "<br>CSI 陈旧时长<b>随时间轮转</b>，不会有某几个 RBG 永远最差。",
                   when="默认（现网为省上行开销、提高导频功率密度普遍开跳频）",
                   cost="实测 MU/SU 比值 0.816 → 0.449（−45%），SU 谱效 −27%",
                   source="38.211 Table 6.4.1.4.3-1 第 63 行"),
            Option("srs_nohop", "不跳频（每次探全带）",
                   formula=(r"\Delta_{\mathrm{CSI}}(t) = t - "
                            r"t_{\mathrm{SRS,last\,usable}}(t)"),
                   summary="全带 CSI 陈旧时长相同，只剩周期内相位 + 处理时延",
                   detail="上行开销大得多（一次要占满 272 RB），"
                          "但 CSI 新鲜得多。实测 SU 谱效只掉 10%（跳频掉 27%）。",
                   when="SRS 资源充裕、或要单独看跳频的代价",
                   cost="上行开销 × 17"),
            Option("perfect", "零时延完美 CSI（关掉老化）",
                   summary="预编码与评估用同一个信道矩阵",
                   detail="<b>这是上界，不是现网。</b>保留它是为了能做 A/B 对比——"
                          "老化的代价必须能被量出来，而不是悄悄混进所有结果里。",
                   when="要上界基线时",
                   cost="系统性高估 MU 增益"),
        ],
        flow=Flow(steps=[
            ("定 SRS 周期", "5 / 10 / 20 / 40 ms，对应 38.331 的 sl10/20/40/80（30 kHz）"),
            ("查跳频序列", "调 ChannelHub 的 srs_rb_indices（38.211 §6.4.1.4.3 完整跳频树），"
                           "C_SRS=63 / B_SRS=1 给出 RBG 0→8→16→7→…→1→9 循环"),
            ("算逐 RBG 陈旧时长", "令 pos(k)=标准跳频序列中 RBG k 的位置；"
                                   "age(k)=((n−pos(k)) mod 17)·T_SRS + 周期内相位 + 处理时延"),
            ("量化成整数快照", "lag(k) = round(age(k) / 快照间隔)，快照间隔默认 5 ms"),
            ("拼出基站以为的信道", "第 k 个 RBG 取自 lag(k) 个快照之前；"
                                   "**越界钳到最早快照，绝不回绕**（回绕=拿未来当过去）"),
            ("用陈旧信道算预编码", "W = SVD(H_stale)，逐 RBG"),
            ("用当前信道评估", "SINR = MMSE(H_true, W)，失配表现为 BF 增益下降 + 流间泄漏"),
            ("rank 也按陈旧 CSI 选", "基站不知道真实信道支持几流——"
                                     "高速下「点了 rank4、实际只撑得住 rank1」正是老化损失的一环"),
        ], branches=[
            (0, "关掉老化", "H_stale = H_true，整条链退化成零时延，结果与原实现逐位相同"),
            (3, "滞后全部量化成 0", "老化模型此时几乎不起作用，aging_summary 主动告警"),
        ]),
    )


def _tx_sinr() -> Family:
    return Family(
        key="tx_sinr",
        name="AMC 预测坐标（CQI + BF Gain）",
        stage="链路自适应",
        current="cqi_bf",
        config_key="",
        intro="**SINR_AMC_PRED 不是物理 SINR。** 基站选 MCS 时手里只有 CQI 反馈和"
              "它自己能算的 BF 增益；真正的 SINR_NEBF/PEBF/EBF_RX 要等同一个 Q 打到"
              "h_true 后才得到。两者的差由误码经 OLLA 闭环吸收。",
        formula=r"\mathrm{SINR}_{AMC,pred} = \underbrace{\Gamma\big(\mathrm{MCS}"
                r"(\mathrm{CQI})\big)}_{\text{长期滤波的宽带上报}} + "
                r"\underbrace{\overline{\mathrm{SINR}_{SVD} - \mathrm{SINR}_{PMI}}}"
                r"_{\text{基站自算，逐次调度}}",
        caveat="**CQI 是长期滤波的宽带量，BF Gain 是 gNB 可见 CSI 上的预测量**——这个分工不能混。"
               "CQI 由终端在真实信道上用 PMI 权测得、上报周期远长于一个 TTI；"
               "BF Gain 基站从自己的 SRS 信道算，所以开老化时它算的是"
               "<b>滞后那一刻</b>的增益，会系统性高估（以为预编码是匹配的），"
               "于是 MCS 点高了、误码上来、OLLA 再拉回去。"
               "<br>早先版本把 AMC 预测写成「接收 SINR 的长期均值」，"
               "那是个<b>事后诸葛亮</b>的量：它已经包含了 SVD 的实际增益，"
               "等于假设基站预先知道自己波束打得准不准。"
               "<br><b>已核查的一处近似：</b>宽带 PMI 已改为在发射空间协方差 "
               "<code>R = E[H Hᴴ]</code> 上选，公共相位翻转不会让信道相消。"
               "当前仍是 Type-I-style 单面板<b>列码本子集</b>：多层采用增量"
               "贪心列选择，而非枚举 38.214 完整多层矩阵码本。"
               "另外 iid 瑞利信道上 BF 增益高达 13 dB <b>不是 bug 而是真实物理</b>："
               "空间白信道没有结构，任何宽带码本波束都对不准。"
               "<br><b>最终 BLER 只查 final MCS + actual_receive_sinr_db。</b>"
               "拿 SINR_AMC_PRED 查曲线只能得到伪精确的预测值，不能当真实 TB BLER。",
        source="已确认的 TDD AMC 流程；38.214 §5.2.2",
        options=[
            Option("cqi_bf", "CQI 门限 + BF Gain",
                   formula=r"\mathrm{CQI} \to \mathrm{MCS} \to \Gamma_{10\%} "
                           r"\to +\,\mathrm{BFGain} \to \mathrm{MCS}' \to "
                           r"+\,\mathrm{OLLA} \to \lfloor \cdot \rfloor",
                   summary="现场口径：内部 CQI 查离散表得 MCS，取该 MCS 的目标 BLER SINR 门限，加 BF 增益",
                   detail="内部 CQI0..14 表为 [0,1,3,5,7,9,12,14,16,19,21,23,25,27,28]，"
                          "不是 38.214 CQI 编号。"
                          "PMI 走 <b>Type-I-style 宽带列码本近似</b>——全带共用一个权，"
                          "正对应现场的<b>全带 CQI</b>（不做子带 CQI、不做频选调度）。"
                           "<br>宽带 PMI 是慢时间尺度的量，所以按 CSI report 周期在当时"
                           "可见功率协方差上更新，并在周期内保持；不能跨完整仿真时域先搜一次。"
                           "<br><b>CQI=0 直接映射 MCS0</b>，是最低可用档，不是 out-of-range。",
                   when="默认",
                   cost="每 UE 每 rank 一次 Type I 码本搜索，约 40 ms"),
            Option("rx_longterm", "接收 SINR 的长期均值（已弃用）",
                   summary="拿接收侧 SINR 在快照上取均值当 AMC 预测",
                   detail="<b>事后诸葛亮</b>：这个量里已经含了 SVD 的实际增益，"
                          "等于让基站预知波束打得准不准。开 CSI 老化后它的问题变得致命——"
                          "老化的全部代价就是「基站以为打准了其实没有」，"
                          "而这个口径直接把它抹平了。",
                   when="不再使用",
                   cost="抹掉 CSI 老化的主要损失"),
            Option("interference_free", "完全无干扰（已弃用）",
                   summary="按反推出的无干扰 SNR 选 MCS",
                   detail="极端假设。实测发送侧 40.7 dB、接收侧 12.7 dB，<b>差 28 dB</b>，"
                          "OLLA 的钳位根本追不上，首传 BLER 飙到 0.85。",
                   when="不再使用",
                   cost="OLLA 发散"),
        ],
        flow=Flow(steps=[
            ("终端测 CQI", "用基站下发的 Type I 宽带 PMI 权，在**真实信道**上测，"
                           "含干扰；长期滤波后上报一个宽带值"),
            ("量化成 CQI index", "按内部映射 MCS 的 10% BLER 门限量化到 0..14"),
            ("CQI → 初始 MCS", "逐项查内部离散表，不用线性公式或谱效近似"),
            ("取该 MCS 的 SINR 门限", "该档 NewTx 曲线上 BLER=10% 对应的 SINR"),
            ("基站自算 BF Gain", "同一信道、同一 rank、同一功率、同一接收机下 "
                                 "SINR_SVD − SINR_PMI，逐 RBG 逐流在 dB 域平均；"
                                 "**开老化时算的是陈旧信道上的增益**"),
            ("相加得 SINR_AMC_PRED", "Γ(MCS(CQI)) + BF Gain；它不是物理 SINR"),
            ("重映射 MCS", "按 SINR_AMC_PRED 选满足目标 BLER 的最高基准 MCS"),
            ("加 OLLA 偏置", "在重映射 MCS 后加连续 MCS 偏置，再 floor/钳位"),
            ("接收端判误码", "同一个 Q 作用到 h_true 得到 SINR_*_RX，只用 final MCS + 该真值查 BLER"),
        ], branches=[
            (1, "CQI = 0", "查表得 MCS0，继续完整 BF/OLLA 链"),
        ], loop_back=(8, 7, "ACK/NACK 反馈驱动 OLLA，下一次调度生效")),
    )


def extra_families(cfg: dict[str, Any]) -> list[Family]:
    return [
        _antenna(cfg),
        _rbg(),
        _precoder_su(),
        _csi_aging(),
        _tx_sinr(),
        _traffic(),
        _exp_thp(),
        _harq(),
        _neighbor(),
        _two_phase(),
    ]
