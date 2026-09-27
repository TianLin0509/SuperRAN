# 干扰、IoT 与接收机

> 本文件由 `CLAUDE.md`「踩过的坑」按主题拆出（2026-09-25），正文原样搬移，条目标题保留原文；索引在 `CLAUDE.md`。
> 每一条都是真实事故，**删之前先想清楚**；改动口径时同步更新本条和 `CLAUDE.md` 索引的一句话要点。

**什么时候读**：改 `interference.py` / `linklevel.py` 接收机 / 干扰类 preset，或下干扰、IRC 结论。

### 干扰小区信道默认不保存

first-party source 的 `measurements.interferer_channels` 默认 `False`——多小区场景下
干扰只体现在 SINR 里，`h_interferers` 会是 `None`。干扰协调类任务必须显式打开
（见 `decisions.py` 里 interference 任务的 `config_hints`）。代价是数据量按
干扰小区数翻倍。

### MMSE 与 IRC 的区别全在 R_n，不在公式

两者用**同一个**后处理 SINR 公式。`receiver="irc"` 把干扰的完整空间协方差
`R_uu` 放进 `R_n`；`receiver="mmse"` 只取 `tr(R_uu)/N_rx` 摊成白噪声。
所以 IRC 的增益**只能**来自 `R_uu` 的非白性——白干扰下两者必须逐位重合，
`test_linklevel` 第 10 节有这条反向自检。不写它的话，实现里多算了什么
（比如把干扰功率漏掉一半）会表现成"IRC 就是更好"，看不出来。

**这是一次语义变更。** 2026-08-01 之前 `receiver="mmse"` 传了 `h_interferers`
时用的是完整有色协方差——那其实就是 IRC，只是叫 MMSE。实测同配置
26.15（新 mmse）vs 28.52（=旧 mmse=新 irc）bit/s/Hz，**旧数字偏乐观 2.37**。
之前所有"MMSE 基线"的谱效都要按新口径重算才能和 IRC 比。

### 历史外部源的干扰小区信道是秩 1 的

单个干扰小区的 `[BS, UE]` 切片奇异值是 `[1, 0, 0, 0]`——实测 96 个抽样
σ₂/σ₁ 中位 4.0e-8、最大 5.9e-8，而服务小区是满秩（归一奇异值
1 / 0.63 / 0.32 / 0.093）。

后果有两个方向：

* **IRC 处在最有利工况。** 3 个干扰小区、4 根接收天线，刚好全部零陷得掉。
  实测 IRC 增益 +2.37 bit/s/Hz（约 9%）。真实干扰不会这么干净，
  **这个数偏乐观**，引用时必须带上 `interference_rank`。
* **`interference_model="precoded"` 目前是个空转旋钮。** 干扰已经是秩 1 了，
  再过一次主特征波束还是同一个子空间，与 `isotropic` 逐位相同。
  留着它是为了信道模型哪天变了能立刻切；**但别声称它现在有用**。

`effective_rank(R_uu)` 会把有效秩报出来，逼近 `N_rx` 时 IRC 增益必然趋近 0。

### num_interfering_ues 是上行旋钮，下行不读它

`_interference_estimation.py:604` 的 DL 分支按**小区**遍历
（`elif K_minus_1 > 0 and direction == "DL"`），全程不读 `num_interfering_ues`；
UL 分支才按 UE 数展开。实测 `ul_sir_dB` 49.90 → 9.43 → −2.61（0/4/16 个 UE），
而 `dl_sir_dB` 在 4 与 16 之间只差 0.13 dB（噪声）。

所以 `srs_congested` 这类"高测量干扰场景"**本质是上行场景**。
项目已明确只做下行，这些预设要么改用别的机制（下行 CSI-RS 复用同一图案的小区数），
要么标成上行专用。

### IoT 的主契约是 SIR + SINR；first-party 的 SNR 差值可作旁证

当前 InternalSim 与 Sionna RT 都把 `snr_dB / sir_dB / sinr_dB` 定义在同一个
**预数字波束、每 RB**参考面：总载波功率先减 `10log10(N_RB)`，阵元方向图与
固定子阵增益已进入 received-power budget，64 端口数字 BF 增益仍留在 H 中。
因此对当前 first-party 数据，`snr_dB - sinr_dB` 数学上就是 IoT。

实现仍以同口径的 SIR+SINR 为稳定跨源契约：

    IoT = SIR / (SIR - SINR)      （线性域）

原因是外部或旧数据源未必声明 SNR 信号参考面；而业务域 SIR/SINR 是报告接口已经
要求的一对。`snr-sinr` 用于 first-party 一致性反查，不作为跨源唯一真相。

first-party `num_slots_per_sample > 1` 会保留完整 slot 信道轴；几何 S/N/I 在这些
slot 上采用同一大尺度预算，因此 IoT 仍为 exact。只有历史来源缺少逐 slot 口径时，
`interference_report` 才把 `iot_exact` 标成 false。

### 业务域与测量域是两个量，别混

`sir_dB` 是**业务域**几何 SIR（决定吞吐）；`ul_sir_dB` / `dl_sir_dB` 是
**测量域**导频 SIR（决定信道估计精度）。两者可以差十几个 dB。

**当前 first-party 数据里的测量域两列不是逐样本仿真**：`ul_sir_dB` 是
`10 - 10·log10(num_interfering_ues)` 的解析占位，`dl_sir_dB` 逐样本等于 `sir_dB`。
`interference_report` 会标 `analytic_placeholder` / `same_as_traffic_sir` 并不分级；
旧文档里 17.9 dB 的测量域对照来自旧内核。SuperRAN 只仿下行业务，上行 IoT 不输出。

### 上行几何 SIR 走显式 metadata，钩子只兼容旧内核

`ChannelSample.ul_sir_dB` 是 SRS 导频测量域，不能承载业务域上行几何 SIR。
当前 first-party 后端通过稳定键 `meta["ul_geometry_sir_dB"]` 交接，并同时写明
`ul_geometry_sir_model=shared_dl_geometry_sir_symmetric_neighbour_power_v1`：这是
“上下行邻区活动/功率对称”的粗粒度工程假设，不是逐 UE 上行功控模型。

`interference.take_ul_geometry_sir(sample)` 优先读该 metadata；只有旧 checkout
才尝试兼容钩子。新代码不得再 monkey-patch 已移除的 `_system_sinr` 内部函数。

### 信道级几何预算不是业务负载仿真

当前 first-party 几何预算把每个非服务小区的 received power 都计入分母，
`pdsch_load` 不参与这条计算。它描述“邻区均活动时的大尺度工作点”，不是逐 TTI
调度轨迹。拿 `pdsch_load` 做轻载/满载扫参不会得到可解释的业务负载结论，
`decisions.check_guards` 会拦这种混层设计。

真正的负载效应在系统/体验层：业务 CDF 与到达间隔决定本小区 PRB 利用率，
`neighbor_prb_util` 决定邻区在一个 TTI 是否占用 PRB；RB 功控再用每个邻区的
独立 (I_k) 计算 `q_serv*S/(N+ηΣq_kI_k)`。目标 10%/30%/50% PRB 利用率必须
通过业务生成后的 KPI 校准，不能拿信道级 `pdsch_load` 代替。

### 逐小区干扰分母项直接落盘（2026-08-11 契约恢复）

InternalSim 与 Sionna RT 在形成业务域几何预算时，同时保存同一参考面、同一单位的
`dl_signal_power_mw`、`dl_thermal_noise_power_mw` 与
`dl_interference_power_per_slot_per_cell_mw[slot,cell]`；服务小区列严格为 0。
当前来源时轴是一个 slot 内的 OFDM symbol 网格，因此 metadata 只写 **1 个 slot 行**，
不会把 14 个 symbol 伪装成 14 个独立 TTI。`generate.py` 原样落成
`[sample,1,cell]`，门 1 / RB 功控入口验证

    SINR = S / (N + Σ_k I_k)

这比聚合 SIR 更强：不同邻区采用不同 RB profile 后，只有逐小区 (I_k) 能重算
`q_serv*S/(N+ηΣq_k I_k)`。旧数据集缺字段时硬失败并要求重新生成，禁止从聚合
SIR 按比例猜。2026-08-11 审查发现旧 `_system_sinr` 移除后这些字段曾断链，
现已在两套后端恢复并加了 source→NPZ→loader 的端到端重构测试。

**反过来，存 `h_interferers` 不值得**：`max_per_ue_intf_cells` 默认只存 3 个，
设成 20 是 11.14 MB/样本（服务信道才 0.56 MB）；而干扰信道已经是秩 1 的、
`precoded` 与 `isotropic` 逐位相同——**20 倍数据量买回来的空间自由度是假的**。

