# SuperRAN

给 Agent 用的无线仿真信道供应站 —— **面向蒙特卡洛验证**。

你提一个无线算法优化思路，它给你可信的信道场景实例、配套的物理观察量，
以及 SINR / 谱效的完整评价链路。统计信道、标准表、阵列、参考信号与估计器
均由 SuperRAN 本仓维护，并通过 MCP 向任意 Agent 开放。

配套的 `channel-sim` skill 提供 superpowers 式工作流：
**头脑风暴 → 计划书 → 生成 → 门 1 体检 → 跑实验 → 门 2/门 3 → 结论**。

## 七件事

**一、信道可信。** 18 项体检，分四类：对 3GPP 标准（路损逐点对 38.901、
**CDL 剖面逐簇对 Table 7.7.1-x**、Annex A.1 角度扩展）、对物理定律（时频能量守恒、
谱效不超容量上界、SISO 退化到香农）、对配置（场景与剖面视距类别、小区数是否被
栅格吸附、**干扰是否真的进了 SINR**）、对统计（收敛、信噪比覆盖）。
**不通过不会静默**，会告诉你哪里不可信、偏了多少、怎么改。

```python
print(ds.gate().text())      # 门 1：18 项，含实测偏差与容差依据
print(ds.calibrate().text()) # 3GPP §7.8 口径的校准量，对 R1-165975 参考曲线
```

**二、信道多样。** 5 个传播场景（UMa/UMi 各含视距与非视距、InF）× 10 个信道剖面
（CDL-A~E 有每径角度、TDL-A~E 无）× 任意小区数 × 10 个真实城市射线追踪场景。
上层没问到的参数一律**原样透传**——`internal_sim` 共 44 个、`sionna_rt` 49 个。

**23 个场景预设分 10 组**，每个都真跑过并把实测特征写在清单里，
不是只给个名字让你猜：

```python
# 干扰场景 · 测量干扰 · 大站间距 · 移动性 · 高铁 · 传播条件 · 多小区干扰 · 基线 · 射线追踪 · 室内与专网
r = sr_probe_scenario(preset="high_iot_dense", num_samples=63)   # 几十秒，不是几十分钟
# 干扰画像、链路预算、路损/距离/视距/多普勒分布
```

**探测模式把 `num_rb` 压到 24、`num_ofdm_symbols` 压到 4，几何量与全量逐位相同**
（实测 num_rb 273/24/12 与 nsym 14/7/4/2/1 各档，SINR/SIR/路损/距离/视距/
多普勒/UE 位置全部零差异）。唯一变的 `snr_dB` 有解析修正。性能随内核而变：
当前 20-ray 版本在 21 小区 16T/20MHz 的交错对照约 **1.80×**，不是旧单簇内核的
11.5×；实际看返回的 `elapsed_s`。探测给不了谱效与吞吐，返回里会列清楚。

**三、谱效开箱即用。** 预编码 → 逐层 SINR → 频谱效率的完整链路，
含逐 RB 协方差特征波束（单快照时等价瞬时 SVD）、宽带协方差特征波束、
Type-I-style 单面板列码本子集近似、DFT 波束四种方案的横向对比。
真正的 Shannon 容量上界由独立的注水容量函数给出，不能把 `svd` 曲线直接叫容量上界。

```python
mc = ds.monte_carlo(method="svd")
print(f"{mc.se_mean:.2f} bit/s/Hz  收敛={mc.converged}")

for name, v in ds.compare_precoders().items():
    print(f"{name:<14}{v['se_mean']:6.2f}  (SVD 的 {v['vs_svd_pct']:.0f}%)")
# svd            30.54  (SVD 的 100%)
# svd_wideband   20.44  (SVD 的  67%)   ← 宽带损失
# type1          17.41  (SVD 的  57%)   ← 码本量化 + 秩自适应
# dft            11.19  (SVD 的  37%)   ← 单层波束
```

**四、结论守得住。** 三道评审门拦住站不住的结论。**信道对了结论照样可以是错的**
——两组配置除被测变量外还有别的不同、一边用理想 CSI 另一边用估计 CSI、
样本量不足到置信区间比效应还宽、只比均值没做检验。

```python
r = ds.compare_arms({"name": "我的方法", "method": "svd_wideband", "csi": "estimated"},
                    {"name": "基线",     "method": "type1",        "csi": "estimated"})
print(r.statement())
# 我的方法 相对 基线：谱效 20.932 vs 13.177 bit/s/Hz，差值 +7.755（+58.9%），
# 95% CI [+6.989, +8.521]，n=200，Wilcoxon 符号秩检验 p=2.72e-31。结论成立。
```

两臂跑在同一批信道上 → 天然配对，共同的场景起伏被差分抵消，
**样本量需求常比非配对少一个数量级**。门 2 拦口径不公平，门 3 拦统计站不住，
过不了门时 `statement` 自己会写"结论不成立"及原因。

**五、自研算法也进得来。** 上面的 `method` 只认六种内置预编码。你自己的
CSI 压缩、信道估计、波束管理、调度算法走结果契约：

```python
# 1. 生成前锁口径（预注册），生成时绑上
pr = sr_lock_analysis(primary_metric="spectral_efficiency", baseline="type1")
ds = sr_generate(..., prereg_id=pr.prereg_id)

# 2. 导一份评测脚本，把 my_algorithm 换成你的算法（不改也能跑）
code = sr_export_eval_template(dataset_id)["code"]

# 3. 你的脚本里注册结果 —— MCP 不执行你的代码，只收标准化的逐样本值
art = ds.register_results("我的方法", values, metric="spectral_efficiency",
                          method_metadata={"csi": "estimated"})

# 4. 交给 MCP 判决，与内置方案用同一套统计与门控
sr_compare_results(art_a.result_id, art_b.result_id)
```

注册时锁死数据集内容摘要、样本 ID **逐个按序**比对、指标与单位——
配对检验的有效性全靠"第 i 个数对应同一个信道实例"，**错配时它照样会算出
一个看起来很显著的 p 值**。

**六、香农谱效不是吞吐。** 上面的 `se_mean` 是上界，真实系统达不到。
链路到系统映射默认使用预置256QAM profile（`mcs_table=3`）：

```python
st = ds.throughput()  # 默认 mcs_table=3，64QAM 只在显式指定 table=1 时使用
print(st.text())
# 输出均值/中位/边缘用户吞吐、谱效、MCS分布、BLER与HARQ摘要
```

三项损失：**调制与表封顶**、**MCS码率离散**、**有限码长与实现损失**。
默认表3含28档预置MCS profile + 56条NewTx/ReTx原始解调曲线（1824点）。
系统只消费28条NewTx曲线；ReTx行用于审计。HARQ
最多一次重传，默认 IR（半谱效等效 MCS），可选 CC（原 MCS、SINR +3.0103 dB）：

```python
st = ds.throughput()
sr_bler_curve(mcs=15, tx_mode="newtx", sinr_db_list=[14.0, 14.05])
# BLER = [0.132, 0.0949]，10% 门限 14.042 dB
```

表1 **64QAM**和表2 **标准256QAM**仍可显式选择，它们使用38.214表和有限码长
分析BLER模型；不会被默认路径静默触发。表3 **不是3GPP标准表**；预置profile将
源标签`Es/No`解释为经典MMSE的单码字
有效 SINR。每个用户 grant/TTI 是一个独立单码字 TB，不另算 CBLER；曲线按预置
口径跨 TBS/RE/rank/场景通用，只用 MCS+SINR 查询。
`sr_mcs_info(show_bler_anchors=true)`默认查表3，可查看全部门限、码率和哈希自检。

TDD AMC 已支持完整的 `内部 CQI 离散表 → 初始 MCS → NewTx SINR 门限 → BF Gain →
重映射 MCS → OLLA → floor` 链路。BF Gain 是同一信道、CSI、rank、功率、
干扰和经典 MMSE 接收机下，SVD 权相对 PMI 权的逐 RB、逐流 post-MMSE SINR
差值；用户 SINR 对全部 RB×流在 dB 域做算术平均：

```python
sr_tdd_mcs(dataset_id="ds_xxxxxxxx", cqi=9, olla_mcs_offset=-0.2,
           feedback_ack=False)
```

在 Claude Code / Codex CLI 里不需要自己写 Python，直接告诉 Agent：
“请调用 superran 的 `sr_tdd_mcs`，对数据集 `ds_xxxxxxxx`、CQI 9、
OLLA -0.2 MCS 计算最终 MCS，并解释逐流 BF Gain。”Agent 会调用 MCP 并返回
完整中间量。默认 `cqi_numbering="internal_row"` 保留历史0..14数组行；需要解析
真实上报日志时传 `reported_4bit`，此时CQI0明确为out-of-range、CQI1..15映射表行为
`[0,2,4,6,8,10,12,14,16,18,20,22,24,26,28]`。当前预置曲线只覆盖
`MCS0..27`，所以最高行请求MCS28时会显式钳到27。反馈只更新下一时刻OLLA；
10%目标下诊断与系统默认统一为ACK +0.01、NACK -0.09 MCS。
诊断和系统仿真统一使用 MCS-domain OLLA：先由 `SINR_AMC_PRED`（CQI 门限 +
gNB 可见 BF Gain，不是物理发送/接收 SINR）反折无 OLLA MCS，
再加连续 MCS offset、`floor` 并钳到当前 profile。系统 API 中历史 `*_db` 参数名
暂为兼容保留，其值不再解释为 dB。

`sr_sweep_snr` 出谱效/吞吐 vs SNR 曲线——实测低信噪比达成 77%、
高信噪比因 MCS 封顶掉到 38%。

**六点五、干扰强度用 IoT 说话。** "高干扰"是个数不是形容词。
IoT（噪声抬升 `(I+N)/N`）由几何 SIR 与 SINR **精确推出**——
当前 first-party 后端的 `snr_dB` 与 `sinr_dB` 共享预数字波束、每 RB 参考，
所以两者之差是等价旁证；正式实现仍用 SIR+SINR，以兼容口径未声明的外部/旧数据。

```python
sr_interference_report(dataset_id)
# traffic_domain.dl.iot   → 28.3 dB，高干扰，等效负载 0.9985
# measurement_domain.ul_srs → SIR -10.5 dB，测量已失效，NMSE 底 10.5 dB
```

**业务域和测量域是两回事。** 实测一组对照：`srs_congested` 与
`srs_clean_reference` 只差导频配置，业务域 IoT 差 **0.06 dB**（噪声），
SRS 测量域 SIR 差 **17.9 dB**（−10.50 vs +7.37）。
只看业务域 SINR 会认为这两个场景是同一件事。

哪些旋钮真的能动 IoT 是**实测过的**，`sr_design_interference` 会给出实测值——
其中两条与直觉相反：`pdsch_load` 对下行 IoT **完全无效**（0.2 与 1.0 逐位相同），
`num_interfering_ues` 影响的是测量域而非业务域上行 IoT。

**七、跑得快但不换样本。** `workers="auto"` 按配置预估耗时自动决定要不要多进程；
static internal_sim 用同 seed + 全局 sample index 分块，worker 数变化时逐样本逐位一致。
移动轨迹、拒绝采样或未支持索引的外部源会带原因回退串行。
20-ray 内核的热态历史锚点是：单小区 32T/20MHz 约 0.158 s/样本、单小区
64T/100MHz 约 1.074 s/样本、21 小区 16T/20MHz 约 7.48 s/样本；24 样本同一
多小区配置实测串行 179.5s、4 进程 49.3s。大批轻配置现在也可能值得并行。

系统级还有独立的 `replication_workers="auto"`：链路表只建一次，把 8 个 RngRun
的 TTI 主循环分给进程。本机 6 UE 固定基准中，5 s 为 1.60→0.99 s（1.61×），
50 s 为 14.20→4.49 s（3.16×）；有限 KPI 精确相同，非有限值类别相同。4 线程只有
0.72~0.74×，所以不提供线程旋钮。逐 RB post-MMSE/IRC/ZF 又通过批量线性代数得到
约 9.8~11.4×，固定输入逐值一致。原始机制记录在
`artifacts/results/performance_audit.json`。

`collect_ssb=False` 会减少工作量，但旧 30% 标定属于 20-ray 之前的版本，不再当作
当前承诺。**比较耗时必须交错重测**；顺序跑变体会把预热效应读成“加速”，而冷态
单样本与热态批量的差异本轮已达一个数量级。`estimate_seconds()` 只负责进程调度，
不是 SLA；实际耗时认返回的 `elapsed_s`。

样本数是**算出来的**，不是问用户的：

```python
sr_sample_size(std_diff=2.14, expected_effect=1.5)   # → 需要 64 个样本
sr_sample_size(std_diff=2.14, n_current=20)          # → 最小可检出 2.70 —— 比预期还大，白跑
```

## 交互方式

```
你：帮我验证一个 CSI 压缩的想法，先弄一批单小区 64T4R 的信道数据。

Agent：配好了 64T4R、272 RB（17 RBG × 16 RB）、CDL-C、100 MHz。
       第 1 轮 · 实验设计 —— 参数配错重跑就行，实验设计错了结论作废。

       ① 你的方法要跟什么比？
          1) 3GPP Type I 或 Type II 码本 —— 最常见的基线   ← 推荐
          2) 理想 CSI 下的逐 RB 特征预编码 —— 乐观参考（非 Shannon 容量上界）
          3) 某篇已发表方法 / 4) 还没定，先看可行性
       ② 用什么指标？
          1) 重建精度类：NMSE / 余弦相似度                ← 推荐
          2) 系统收益类：频谱效率或吞吐损失
          3) 任务专属：波束命中率 / 定位误差 / BLER
       或者你直接说。
```

**一轮 2~4 个问题，每题 3~4 个选项并标明推荐**，最后留"或者你直接说"。
典型 2~3 轮收敛，用户随时可以说"随便"直接生成。轮次由 MCP 自己记，
Agent 不用规划；`has_more_rounds` 为 false 或用户说"随便"就停。

设计参考 [superpowers](https://github.com/obra/superpowers) 的 brainstorming，
按仿真场景做了调整——它面对开放式设计所以一次一问，而仿真参数空间有限且已知。

## 文档

- **[SuperRAN 开发者文档 `docs/index.html`](docs/index.html)** —— 当前实现的主入口：无线物理、64T4R/192×64 阵列、SRS/LMMSE、EBF/PEBF/NEBF、独立 BF Gain 章节、SU/MU、capacity/experience、话务/PF/KPI、35 个 MCP 工具、Skill、全部公开 API 与本次审计修复；单文件离线可打开
- **[安装说明 `SETUP.html`](SETUP.html)** —— 由哪几块拼成、要装什么、怎么装、装完先跑什么、排错
- **[`INSTALL_AGENT.md`](INSTALL_AGENT.md)** —— 写给 AI agent 看的安装步骤，丢给它自己装
- **[能力手册 `CAPABILITIES.html`](CAPABILITIES.html)** —— 能产生哪些信道、能拿到哪些观察量（含形状与单位）、参数全表、能力边界
- **[实测场景演示 `SHOWCASE.html`](SHOWCASE.html)** —— 真实跑过的场景对话、三道门、踩过的坑
- **[接入自研算法 `EXTERNAL_ALGO.html`](EXTERNAL_ALGO.html)** —— 让你自己的算法进门 2/门 3、预注册分析口径、边界与局限
- **[从 SINR 到真实吞吐 `LINK_ADAPTATION.html`](LINK_ADAPTATION.html)** —— L1 链路自适应、38.214 MCS/CQI、SNR 扫描曲线、并行生成
- **[测试体系历史说明 `TESTS.html`](TESTS.html)** —— 2026-07-31 的历史快照，用于理解测试理念与事故案例；当前文件/接口清单以开发者文档为准
- **仿真说明书 / 运行前工作台** —— `sr_spec_sheet` 出的 HTML，默认只返回 URL、不打断用户；明确传 `open_browser=True` 才弹浏览器。页面以真实拓扑与用户/默认来源打头，其余折进 7 个页签；改参数时会标出信道/链路表/TTI/KPI 哪些层需要重算，点「应用到仿真」把 delta 送回 agent（`sr_await_config` 接）。同时支持说明书/Resolved config JSON 下载、摘要复制、页面截图、系统分享与打印/PDF；拷走用 `file://` 打开时自动退回复制粘贴
- **CDF 话务与目标负载校准** —— 包大小/包间隔各读一份 `value,cdf`，支持全局×profile 双标量、多 profile 与 `ue_ids` 显式用户映射；`target_prb_utilization=0.30` 用公共随机数调话务，最后另跑正式重复实验，未达容差绝不回填目标值。内置 synthetic CDF 只用于接口演示，后续可直接替换现场 CDF
- **Agent 自适应 KPI 工作台** —— `sr_system_sim(evaluation_mode="experience")` 自动返回 `kpi_view.html_path/url`，顶层为“小区级 / 用户级”；用户级指标同时支持按 UE 图、跨 UE 经验 CDF 和明细表。调用 Agent 可传 `kpi_focus` 优先展示相关 KPI，其余折叠且不丢失，选择理由完整回传。页面含首包时延、含头速率、本小区 PRB 利用率、0..17 RBG 分布、MU 配对比例与用户级 PRB 归因，并可一键下载完整 JSON、小区 CSV、用户长表 CSV，复制摘要、导出页面截图、系统分享或打印/PDF；所有动作离线可用且只读结果
- **2~5 算法对比与单 TTI 复盘** —— 每次 `sr_system_sim(..., algorithm_label=...)` 同步保存严格 JSON sidecar；`sr_compare_system_results` 将同一 dataset/话务/KPI/RngRun 的基线与候选放入同一工作台。算法保持固定颜色，六个 Tab 按“总览/KPI 矩阵/用户分布/TTI 趋势/单 TTI/统计门禁”分工；主 KPI 走配对 Gate 3 与多候选 Holm 校正，sampled trace 以均匀锚点加关键事件保存 RBG、MCS/rank、SINR、BLER/draw、ACK、OLLA 与 PF 证据。不同配置或 RngRun 会硬拒绝；没有生成前 prereg 时即使显著也保持 `exploratory_unregistered`
- **体验仿真的冻结合同** —— 当前 TDD 系统只接受 100 MHz @ 30 kHz、272 RB = 17×16，标准 273 RB 在生成前明确舍去 1 RB；SRS hopping 只接受本地版本化的 C_SRS=63/B_SRS=1/b_hop=0/n_RRC=0 17-hop profile。`experience_v2` 只用 `preset_20b_256qam / MCS table 3` 预置表；OLLA 默认由 `target_bler` 与 ACK 步长反解 NACK 步长，仍允许显式 override，结果会标注来源。通用载波/MCS 接口保留给链路级与未来扩展，不会静默混入当前体验结果
- **SRS资源与调度P0已闭环** —— 固定载波下排除BBL叶子，按PCI模3硬分区、
  4 CS、17频域相位给2T4R UE分配相邻两个2-port SRS资源；两个offset分别进入
  端口组CSI老化并拼成64×4。全局周期自动选择最短可容纳的10/20/40 ms，
  禁止跨颜色借资源。体验调度的逐RBG频选已与RB功控解耦，MU枚举全部伙伴并按
  useful bytes/RBG评分。独立<code>srs_waveform</code>后端已经能用显式的UE→受害gNB
  UL cross-link做RE级叠加、TA/CFO、解扩、双腿64×4与UL IoT证据；尚未完成的是
  系统主循环自动生成这些cross-link并把波形H-hat注入调度。PDCCH/CCE、P-H/F、BWP2
  也仍在范围外；方向性证据见`artifacts/results/scheduler_p0_validation.json`
- **物理时钟、SRS测量与场景资产合同** —— 新数据显式保存`sample_interval_s`，默认5 ms，
  不再从0.5-ms slot、SRS双腿或报告周期猜测。`srs_metrics`区分per-active-RE、per-RB
  与全分配底噪，提供开环UL功控、绝对SRS链路预算和线性域PreSINR IIR；UL IoT可写入
  原子NPZ sidecar并复算IoT/双SHA。城市RT缓存使用稳定进程锁、源/准备后双指纹及独立
  RF材料revision，缓存手改自动重建，中断发布journal硬失败。
- **[MU-MIMO 算法流程 `MU_MIMO.html`](MU_MIMO.html)** —— 配对/预编码/功率分配逐步展开，含六个待确认的设计选择与实测数字
- **[通宵成果与待审 `TONIGHT.html`](TONIGHT.html)** —— 6 个 bug、5 个新需求提案、8 个待拍板的决策点
- **[通宵进展与待审问题 `MORNING_REVIEW.html`](MORNING_REVIEW.html)** —— 3GPP/ITU 对标结果 + 12 个待拍板的问题
- **[还缺什么 `ROADMAP.html`](ROADMAP.html)** —— 对着 Sionna / MATLAB 5G Toolbox / QuaDRiGa / 5G-LENA 逐模块点名。**只下行 · 只 TDD · BLER 一律查表**，边界写在第七节
- **[场景拓展与干扰量化 `SCENARIOS.html`](SCENARIOS.html)** —— IoT 噪声抬升、业务域 vs 测量域、21 个场景的实测画像、场景探测、哪些提速是真的

## 四条设计铁律

**一、不传数据，传取货代码。** 单个信道样本几百 KB，序列化成 JSON 会膨胀到
十几 MB——进不了任何模型的上下文。MCP 只回句柄、统计摘要和可运行的 Python。

**二、给物理量，不给训练特征。** 本项目没有 MAE token/特征桥：
PDP 不归一化、RSRP 不截断、SRS 给完整协方差和全部特征值、
PMI 给码本索引而非嵌入向量。

**三、生成与取货解耦。** 测量量从信道现算，改主意重新取货**实测 1 毫秒**，
不重跑仿真。

**四、分轮问，先设计后参数；能算的不问。** 样本数由期望效应量和试点方差算出来，
不问用户"你想跑多少次"——把自己该做的功课推回去是这类协作最常见的偷懒。

## 拦截"跑得出结果但没意义"的组合

| 组合 | 为什么拦 |
|---|---|
| 波束搜索 + TDL 模型 | TDL 没有每条径的角度，算法会输出看似正常的垃圾且不报错 |
| 信道预测 + 单时隙 | 样本间相互独立，没有可预测的时序结构 |
| 干扰协调 + 单小区 | 没有干扰源 |
| 视距场景 + 非视距剖面 | 路损与多径按不同假设生成，时延扩展偏离标称值数倍 |
| 射线追踪数据 + `ds.paths()` | 多径来自真实建筑几何，套用 CDL 剖面会得到与数据无关的假角度 |
| 多小区但 SINR = 纯热噪声 SNR | 干扰没进计算，干扰类结论全不成立 |
| 一臂理想 CSI、另一臂估计 CSI | 增益里混着"提前知道答案"的部分 |
| 置信区间跨零却说"有提升" | 方向都不能确定 |
| 把香农谱效当吞吐报 | 真实系统要打 4~6 折，差的是调制受限+码率离散+码长 |
| 声称实测 BLER | 表 1/2 是分析模型；表 3 是用户曲线插值，二者都不是 3GPP 实测 |

## 团队 Agent 开发

- 普通组员或组长本人做具体实现时，都打开 `docs/team/member-start.html`；该页面固定启动正式 `FORMAL` Author 流程。
- 组长使用 `docs/team/lead-start.html` 分任务、看状态、审核 PR，并按当前完整 SHA 决定合并。
- `develop` 是所有实现 PR 的目标分支；`main` 是组长单独控制的发布分支。

GitHub Owner 身份不会再自动触发演练：Owner 在正式模式下直接推送上游 topic branch，
普通组员推送自己的 Fork。演练只能从组长页复制明确的 `TEAM_MODE: REHEARSAL` Prompt。
组长本人提交正式 PR 后，用另一个全新 Agent Session 和隔离 worktree 完成审核。

两份页面会自动引导 Agent 安装仓库版本的 `channel-sim`、`superran-member-task` 与
`superran-lead`，无需人手改 Prompt 或复制 Skill 文件。

任一 Author PR 提交或更新后，Author Agent 还会生成绑定当前远端 PR HEAD 的离线交互式改动
说明 HTML；人类 Author 把该文件与 PR 链接一起交给组长审核会话。它用于理解改动，不代表审核通过，
默认不提交到公开仓库。

## 安装

### 最省事：让 agent 自己装

把这句话发给你的 Claude Code / Codex：

> 帮我装 superran：读 https://github.com/TianLin0509/superran/blob/main/INSTALL_AGENT.md
> 按里面的步骤装好并验证，装完告诉我能不能用。

[`INSTALL_AGENT.md`](INSTALL_AGENT.md) 是**写给 agent 看的**：每步带验证命令与预期输出，
标了哪些事该问你、哪些该自己查，附失败对照表。

### 内网 / 不能联网

在一台能联网的机器上打包，拷进去：

```bash
python scripts/make_offline_bundle.py          # 完整包 65 MB，全新 venv 可全程离线装
python scripts/make_offline_bundle.py --thin   # 轻量包 17 MB，要求目标机已有 numpy/scipy
```

产出 `dist/superran-offline-<包型>-<平台>-py<版本>.zip`，里面有源码、skill、
依赖 wheel、`bundle-manifest.json`（各文件 SHA-256）、`INSTALL_AGENT.md`
和给人看的 `开始安装.txt`。接收方解压后把那句话发给自己的 agent 即可。

**默认打完整包。** 轻量包不含 numpy/scipy 与构建后端，在全新 venv 里
`pip install --no-index -e .` 会失败（先卡在缺 setuptools，而报错只说
"install build dependencies did not run successfully"，看不出缺什么）。
包型写进了文件名和 manifest，`requires_preinstalled` 直接列出需自备什么。

**wheel 是平台相关的**，必须在与目标机器同平台、同 Python 大版本的机器上打包。

> 包内已经包含 first-party 统计信道物理内核，不需要第二个源码仓库。
> 可选 Sionna RT / QuaDRiGa 仍按各自许可证和运行时单独安装。

### 手动

需要 Python ≥ 3.10。

```bash
git clone https://github.com/TianLin0509/superran
cd superran && pip install -e .

pip install sionna-rt      # 可选，射线追踪（约 300 MB）
```

不装射线追踪也能用，`sr_capabilities` 会如实报告缺什么。
安装后必须让 Agent 运行 `channelhub.probe_source_contract()`；它校验本仓 first-party
窄腰，只有 `compatible=true` 才能生成正式数据，且不会改接外部源码树。

```bash
claude mcp add superran -- python /path/to/superran/scripts/mcp_server.py
codex  mcp add superran -- python /path/to/superran/scripts/mcp_server.py

# Codex 团队 Skill（按当前角色选一个）
python scripts/install_agent_skills.py --role member
python scripts/install_agent_skills.py --role lead
```

## 评审门控

| 门 | 什么时候过 | 拦什么 |
|---|---|---|
| **门 1 · 信道可信** | 生成之后 | 18 项体检，硬性项不通过即拦截 |
| **门 2 · 比较公平** | 跑对比时 | 两臂不同数据集、配置漂移、**CSI 口径不一致** |
| **门 3 · 结论站得住** | 写结论前 | 置信区间跨零、检验不显著、单样本主导、声称值超出区间 |
| **预注册身份** | 写结论时 | 用的指标不是事先定的 → 标 `exploratory`，不许冒充主结论 |

门 3 的显著性**以 Wilcoxon 符号秩检验判决**，配对 t 只作参考——谱效的逐样本差值
分布常是偏的，t 检验的正态假设不成立、小样本下偏乐观。两个检验冲突时 `statement`
会把冲突明写出来。

门 2 的 CSI 口径检查是无线论文评审最常抓的一条——自己的方法用理想信道预编码、
基线用估计信道，测出来的"增益"里混着"提前知道答案"的部分。

3GPP 口径的校准量按 **TR 38.901 §7.8** 出：耦合损耗 CDF（§7.8.1 指标1）、
几何量含噪与不含噪两条（指标2）、时延与角度扩展 ASD/ASA/ZSD/ZSA
（§7.8.2 指标3，Annex A.1 圆周定义）、PRB 奇异值最大/次大/比值三条 CDF
（指标4，10log10 尺度）。参考曲线在 R1-165974 / R1-165975 / R1-1909704。

## MCP 工具（35 个）

| 工具 | 作用 |
|---|---|
| `sr_capabilities` / `sr_list_presets` / `sr_list_scenes` | 能力与场景发现 |
| `sr_missing_slots` | **结论模板还缺哪些槽** —— 决定该主动问什么 |
| `sr_plan` / `sr_revise` | 分轮协商：实验设计 + 参数 + 对比组 + 陷阱 |
| `sr_generate` | 生成数据集，返回句柄与统计摘要 |
| `sr_deliver` | 按自然语言点单生成取货代码 |
| `sr_validate` / `sr_gate` | **可信度体检 / 门 1**：18 项 |
| `sr_calibrate` | **3GPP §7.8 校准量**：耦合损耗、几何、时延角度扩展、PRB 奇异值 |
| `sr_link_performance` | **算谱效**：预编码 → SINR → 谱效，多方案横向对比 |
| `sr_compare_arms` | **配对比较 + 门 2 + 门 3**，返回可直接引用的结论句 |
| `sr_sample_size` | **功效分析**：样本数 ↔ 最小可检出效应 |
| `sr_lock_analysis` | **预注册**：生成前把主指标与基线定下来 |
| `sr_export_eval_template` | **自研算法评测脚本骨架**，替换一个函数即可 |
| `sr_compare_results` | **判决外部算法结果** + 门 2 + 门 3 + 预注册身份 |
| `sr_list_results` | 已注册的结果与预注册记录 |
| `sr_throughput` | **真实吞吐 Mbps** + 5% 边缘用户（链路到系统映射） |
| `sr_sweep_snr` | **谱效/吞吐 vs SNR 曲线**，各点配对无抽样噪声 |
| `sr_mcs_info` | 表 1/2：38.214 + 分析模型；表 3：用户 MCS + NewTx/ReTx 门限 |
| `sr_bler_curve` | 查单档原始 BLER 曲线、10% 门限，并在任意 SINR 点做对数域插值 |
| `sr_tdd_mcs` | **TDD AMC**：CQI → PMI/SVD BF Gain → MCS → OLLA，返回逐 RB/流审计链 |
| `sr_system_sim` | **系统级仿真**：连续几秒 TTI + PF 调度 + 话务，出体验速率等现网 KPI |
| `sr_compare_system_results` | **2~5 算法 KPI 对比**：CRN 配对、用户 CDF、TTI 趋势/钻取、Gate 3 + Holm |
| `sr_spec_sheet` | **仿真说明书**：拓扑图 + 分级页签 + 调参面板；默认只返回 URL，`open_browser=True` 才弹浏览器 |
| `sr_await_config` | 等用户在说明书上点「应用到仿真」，**改动直接回来**，免复制粘贴 |
| `sr_describe_dataset` / `sr_list_datasets` | 数据集信息 |

## 观察量（12 类）

| 名称 | 内容 |
|---|---|
| `channel` | 频域信道矩阵，理想与估计两版 |
| `linkperf` | **链路性能**：预编码、逐层 SINR、谱效、容量上界、多方案对比 |
| `validate` | **可信度体检**：18 项检查 |
| `pdp` | 时延功率谱：未归一化功率 + 真实时延轴 + RMS 时延扩展 |
| `paths` | 每条径的时延、功率、角度（**CDL 才有角度**）|
| `srs` | 完整空间协方差、全部特征值、每天线增益、波束域 RSRP |
| `pmi` | Type-I-style 单面板列码本子集近似：列索引 + 预编码矩阵 + 秩 |
| `rsrp` / `sinr` / `capacity` | 功率、链路标量、容量与条件数 |
| `geometry` | 路损、阴影、3D 距离、视距判定、多普勒、位置 |
| `topology` | 多小区 SSB 测量与干扰小区信道 |

## 物理层工具箱

`superran.physical` 公开本仓按 38.211/38.213/38.214 实现并版本化的模块，
主要用来**当基线**和**做导频层课题**：

```python
from superran import physical as ph

ph.nr_rb_count(100e6, 30000)       # 273（标准表，不是简单除法）
ph.tdd_pattern_info("DDDSU")       # 帧结构 + 特殊时隙符号级切分
ph.srs_config(272, b_srs=1)        # SRS 跳频：周期 17、每跳 16 RB、覆盖整带
ph.zadoff_chu(25, 139)             # ZC 序列，实测峰旁比 151 dB
ph.ssb_sequences(42)               # PSS / SSS / PBCH-DMRS
ph.dft_codebook(8, 4, 2)           # CSI-RS 波束码本 [512, 64]
ph.estimate_channel(h, method="mmse", tau_rms_s=363e-9)   # LS / MMSE 估计基线
ph.project_interference(...)       # 干扰投影：不投影会高估干扰
```

## 场景与参数

**传播场景**：城区宏站视距/非视距 · 城区微站视距/非视距 · 室内工厂
**信道剖面**：CDL-A~E（有每径角度）· TDL-A~E（无角度）
**拓扑**：任意站数 × 扇区数（1 或 3），支持六边形栅格与线性布站、
超级小区、多 TRP、高铁车体穿透、自定义站点与用户坐标
**射线追踪**：慕尼黑 · 巴黎凯旋门 · 佛罗伦萨 · 旧金山 · 北京中关村 ·
上海陆家嘴 · 深圳福田 · 广州天河 · 杭州钱江 · 重庆解放碑
**子载波间隔**：15 / 30 / 60 / 120 kHz　**带宽**：5~100 MHz 共 13 档
**TDD 配比**：7 种　**支持任务**：12 类

加场景只改 `presets/presets.yaml`，加决策点只改 `decisions.py`。

## 已知约束

- **信噪比不能直接设定**。它由路损、发射功率和撒点位置决定；要求特定区间时
  走拒绝采样。想整体调整，改发射功率或站间距更有效。
- **视距比例由几何决定**，不是选 CDL-D 就能得到视距信道——剖面类别与几何
  判定不符时会被自动替换。想调视距比例改站间距（实测 200m→0.46、800m→0.13）。
- **射线追踪当前没有 direct adapter**。统计信道主功能不受影响；未来 direct
  Sionna 适配器必须显式导出逐径几何，否则 `ds.paths()` 仍应硬失败而非返回假角度。
- **时延扩展的频域估计有固有误差**。可观测最大时延是 `1/(12·SCS)`，
  实测比值 0.8~1.0，仅作数量级检查。
- **QuaDRiGa 未纳入**，需要 MATLAB/Octave 运行时。
- **场景资产与源码解耦**。内置场景只依赖可选 Sionna 包；自有 OSM/PLY 数据通过
  `SUPERRAN_SCENES` 指向独立数据目录，不从其他源码 checkout 静默借用。
- **CDL-A~E 都有标准表硬门**。`spec38901` 是本仓唯一运行表真相源，覆盖
  38.901 Table 7.7.1-1~5（23/23/24/14/15 个表分量），启动时逐字段自检，失败即阻断生成。diffuse component
  按 20 rays 展开；CDL-D/E 的 K 已在表的镜面/散射功率差中，只计一次。
  `SUPERRAN_CDL_SPEC=0` 仅用于复现历史非标准结果。
- **`bs_panel` 决定空间阵列，不再决定邻区干扰是否进入几何预算**。当前
  first-party 后端直接从服务/邻区接收功率形成 SNR/SIR/SINR；面板仍是二维端口
  几何、双极化和 64T 1 驱 3 effective-subarray 的必要输入。门 1 会另行拦截
  多小区却 `SIR=49.9` 或 `SINR=SNR` 的退化数据。

## 测试

```bash
python tests/test_e2e.py
python tests/test_mcp_server.py
python tests/test_raytracing.py
python tests/test_linklevel.py
python tests/test_gates.py
python tests/test_results.py
python tests/test_linkadapt.py
python tests/test_mumimo.py
python tests/test_system.py
python tests/test_scheduler_p0.py
python tests/test_srs_resource.py
python tests/test_srs_waveform.py
python tests/test_interference.py
python tests/test_csi_aging.py
python tests/test_rng.py
python tests/test_sysscenes.py
python tests/test_power_control.py
python tests/test_physics_contract_extensions.py
python tests/test_physics_invariants.py
python tests/test_channel_generation_contract.py
python tests/test_native_independence.py
python tests/test_developer_guide.py
python tests/test_carrier.py
python tests/test_company_256t.py
python tests/test_system_sim_tool.py
python tests/test_benchmarks.py
```

当前共 **28 个可执行测试文件**。运行时检查会在循环中按场景展开，因此不维护一个
容易失真的手写“总项数”；以实际运行输出和开发者文档的自动盘点为准。

经典通信正确性套件先冻结判据再运行：

```bash
python scripts/run_classic_comm_benchmarks.py
```

结果落在 `artifacts/results/classic_comm_benchmarks.json`，包含 commit、dirty diff、
依赖版本、预置 BLER 哈希、逐 case 门禁和限制。它用于判断实现是否满足经典关系，
不替代现场 BLER/话务/现网 KPI 校准。

可观察、可终止的逐文件回归：

```bash
python scripts/run_test_matrix.py --tier quick
python scripts/run_test_matrix.py --tier physics
python scripts/run_test_matrix.py --tier full
```

## 致谢

统计信道物理内核由 SuperRAN 独立维护；其设计参考了既有信道平台的窄腰思想。
可选射线追踪计划直接对接 [Sionna RT](https://nvlabs.github.io/sionna/)。
工作流设计参考 [superpowers](https://github.com/obra/superpowers)。

## License

MIT
