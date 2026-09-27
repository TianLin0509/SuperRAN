# SuperRAN

Agent 开发入口：[安装与接入](INSTALL_AGENT.md) → [协作合同](AGENTS.md) → [跨电脑交接](.agents/SYNC.md)。
日常代码以 [阿里云私有仓库](https://ai.lt-stockpartner.tech/repos/superran/SuperRAN) 为准；GitHub 仅保留历史。

SuperRAN 用来研究无线算法：生成信道，模拟用户业务与调度，再检查观察到的差异是否足以支持结论。你可以直接调用 Python，也可以让 Agent 通过 MCP（模型上下文协议）完成配置、运行和取数。

统计信道、阵列、参考信号、估计器与系统仿真由本仓维护。默认使用 CDL（簇时延线）信道；Sionna RT 是显式选择的可选射线追踪后端。

**第一次使用看[安装说明](INSTALL_AGENT.md)，理解算法看[离线技术手册](docs/index.html)，复用旧结果先核对下面的当前口径。**

## 先选要回答的问题

| 你的问题 | 用什么 | 结果应该怎么看 |
|---|---|---|
| 要信道矩阵、功率时延谱或 SRS 测量量 | 计划 → 生成 → 体检 → 取数 | 核对形状、单位、时间轴；无需虚构算法基线 |
| 换一种估计或预编码，链路表现如何 | 链路谱效、吞吐与配对比较 | 高斯码本谱效不等于业务吞吐；均值差不等于比较结论 |
| 多用户竞争资源，谁能更快发完 | 系统仿真、有限到达话务 | 同时看用户速率、完成时延、误块率和资源占用 |
| 研究满缓冲容量或边缘用户速率 | 同一系统入口，`traffic_model="full_buffer"` | 看窗口内发送净荷速率；已排空忙期吞吐没有样本 |
| 同一用户跨多个载波发数据 | 显式 `ca_config` | 队列共享、载波状态独立；见 [CA 使用说明](docs/ca.md) |

`channel-sim` skill 按任务选择流程：要数据就交付数据，要比较才组织对照实验。`superran` 是同一手册的显式点名入口，正文由安装器从 `skills/channel-sim/` 生成。

## 当前口径：复用旧配置前先核对

本节按本地 `develop` 截至 **2026-09-18** 已合入的实现整理。日期表示实现基线，不表示完成了现场标定；每次运行仍需核对返回的配置与代码身份。

| 环节 | 当前实现 | 对使用的影响 |
|---|---|---|
| 下行资源 | D 时隙净 **132 RE/PRB**；S 时隙默认 `floor(132 × 0.715) = 94` | 替代旧 126/78 RE 口径，旧吞吐绝对值需重跑 |
| SINR 聚合 | RBG 内先转 dB 再平均，之后在实际授予的 RBG 与流上做 dB 平均 | 不再使用 RBG 内线性平均；小包不能借用全带真值 |
| 多用户传输 | 两用户 MU（多用户 MIMO），每用户 rank 1–2，允许不等 rank；默认预编码 `ezf` | MU 默认关闭；开启后按实际层数分功率，不能一律减 3 dB |
| MU 相关性损失 | 逐 RBG 平均相关度，逐流连乘残余项，再把各流损失按 dB 求和 | 预测 MCS 与真实接收判错分开计算 |
| 连续信道 | 同一 UE 各轮样本使用同一组散射体，沿时间推进；逐径 Doppler 使用 UE 运动方向 | 相邻快照不是独立样本；`static` 固定几何位置，不自动关闭小尺度时变 |
| 载波聚合 | 同站、同步、30 kHz；共享队列与用户统计，逐载波维护 CQI、rank、OLLA、HARQ | 已有 100+20 MHz 示例；普通单载波入口仍不接受 20 MHz |
| SRS 开环功控 | 带宽项为 `10log10((SCS/15kHz) × M_RB)`，随后施加 UE 功率上限 | 已接入测量预算；尚未驱动信道生成器或调度主循环 |
| 独立 TBS 对拍 | 29 档参考谱效表与 `fg_adjust_tbs` 独立可调用 | 尚未接入系统 AMC；不能拿其 MCS 下标查询现有 28 档 BLER 曲线 |

RE 是一个子载波、一个 OFDM 符号上的资源单元；PRB 是物理资源块，RBG 是一起分配的资源块组。当前资源预算从 14 个符号的 168 RE 中扣除等效 DM-RS 预留 24 RE、PDCCH 预留 12 RE，得到 132。**这是指定场景的工程预算**，不是逐 PRB 的实际导频/控制信道映射，也不是 3GPP 对所有配置的统一规定。

S 时隙先折算已经扣完开销的净 RE，再按每 PRB 向下取整。例如分配 16 PRB，D/S 分别有 2,112/1,504 RE。传输块大小（TBS）还要另做量化，因此不能把 0.715 直接当成吞吐比。

<details>
<summary>实现与回归依据</summary>

| 说明 | 实现入口 | 回归入口 |
|---|---|---|
| RE、TBS 与独立参考计算 | `linkadapt.PdschOverhead`、`experience.TbsLookup`、`linkadapt.calc_tbs_reference` | `test_linkadapt.py`、`test_system.py` |
| SRS 功控 | `srs_metrics.open_loop_ul_tx_power_dbm` | `test_physics_contract_extensions.py` |
| SINR 与 MU | `system.py`、`mumimo.py`、`scheduler_mu.py` | `test_csi_aging.py`、`test_mumimo.py`、`test_scheduler_p0.py` |
| 连续时钟、运动方向 | `native.InternalSimSource` | `test_channel_generation_contract.py`、`test_physics_invariants.py` |
| CA 队列、指纹与逐载波来源 | `ca.py`、`ca_engine.py`、`ca_server.py` | `test_ca.py` |

这里列的是验证入口，不是本次运行记录。[历史变更记录](docs/changes/README.md)保留当时的决策；[CHANGELOG](CHANGELOG.md)记录对外同步批次，可能落后于本地实现。

</details>

## 默认系统实验怎样运行

单载波基线为 **100 MHz / 30 kHz / 272 RB = 17 RBG × 16 RB**。标准表中的 273 RB 在生成前明确舍去一个；系统入口校验真实信道轴，不会读取后静默截尾。其他合法栅格用于链路级，或走显式 CA 路径。

默认有限话务为 FTP3、固定 rank=2、MU 关闭、8 次重复、每用户 8 个 HARQ（混合自动重传）进程。一个传输块最多重传一次：默认 IR，可选 CC；重传冻结 MCS、rank、PRB 数、TBS 与时隙类型，不带新队列数据。

CQI（信道质量指示）在运行时按 CSI 报告周期更新，默认 20 ms。基站据可见 CSI 计算波束增益，再叠加 OLLA（外环链路自适应）偏置选择 MCS；真实接收 SINR 只用于判错，不能提前参与发送决策。CQI 平滑系数 0.25、UE 实现损失 1.5 dB 是工程默认，尚未经现场设备数据标定。

队列在**首传发出时**扣除净荷。重传占用资源、推迟后续业务；末次失败计入 `residual_bler`，不把字节放回队列。因此发送速率不能当成成功交付速率。

| 指标 | 分母与样本 | 满缓冲时 |
|---|---|---|
| `ue_served_p5_mbps` | 每 UE 窗口内发送净荷 ÷ 观测窗，再取用户间 5% 分位 | 有值，适合看边缘用户 |
| `drb_throughput_rel19_mbps` | 按 TS 28.552 已排空忙期形成样本 | `None`，不能填零 |
| `active_window_goodput_mbps` | 窗内仍在进行的忙期片段，工程口径 | 可有值，不能冒充标准已完成样本 |
| `serving_cell_prb_utilization` | 已分配 / 可用的下行 PRB 等效资源 | D 权重 1，S 按配置权重；不是吞吐利用率 |

默认调度为 PF（比例公平）。可选 EDF 在本项目指 **Earliest Drain First，最早排空优先**，不是按截止时间排序。详细时序、参数和指标见[系统仿真参考](skills/channel-sim/references/system-sim.md)。

## 跑通第一个实验

安装后，让 Agent 执行：

> 使用 superran，先检查当前能力，再按 SRS 与 PMI 的 Hello World 示例生成数据、做体检和配对比较。解释结论是否成立，并给出证据文件；不把点估计写成已证明的收益。

也可以在仓库目录直接运行配套脚本：

```powershell
python -u scripts/run_srs_pmi_hello_world.py
```

[手册快速开始](docs/index.html#/quickstart)解释该实验改变了什么、固定了什么，以及结果不足以支持收益时如何报告。生成后新增测量量可直接重新取数，无需重跑信道。

MCP 返回数据集句柄、摘要和取数代码，大数组保存在文件中。取数后用 `Dataset` 读取信道、PDP（功率时延谱）、协方差与 PMI（预编码矩阵指示）；单位、轴序和边界见[测量量章节](docs/index.html#/measurements)。

## 哪些结果可以写成结论

- **门 1：信道体检。** 当前 18 项体检覆盖标准表、物理关系、配置与统计条件。失败时先诊断，不能发布性能结论。
- **门 2：比较条件。** 核对数据身份、样本顺序、CSI 口径、随机流以及允许变化的因素。
- **门 3：统计判决。** 使用配对差值与明确的检验；两个单臂均值或置信区间不能替代比较判决。

正式比较在生成前锁定主指标与基线。事后提出的新问题只能作为探索性结果，不能补签成预注册实验。系统比较通过 `sr_compare_system_results`，可将 2–5 个算法放在同一工作台，并查看单 TTI（传输时间间隔）的调度轨迹。

同一数据集重复 8 次主要覆盖话务、调度与 ACK/NACK 随机性，**不覆盖重新撒点和生成信道的不确定性**。外推到其他信道条件，需要事先设计独立信道种子。历史数值必须同时核对代码版本、配置与数据来源；不一致的结果可用于历史复现，不能当作当前版本证据。

## 已实现能力与边界

| 能力 | 已实现 | 使用边界 |
|---|---|---|
| 统计信道 | CDL-A–E、TDL-A–E，阵列与多普勒 | TDL 不提供逐径角度；相邻时刻不能当成独立位置 |
| Sionna RT | 显式 `source="sionna_rt"` 直连适配 | 缺依赖或服务链路无径时报错；`Dataset.paths()` 不支持 RT 数据；重跑不保证逐位相同 |
| SRS | 2T4R 双腿探测、PCI 模 3 分区、4 个循环移位、17 跳资源与老化 | 波形后端已有独立验证；系统尚未自动生成上行交叉链路并将波形估计注入调度 |
| BLER | 系统表 3 使用 28 档 MCS、28 条 NewTx 曲线；另外 28 条 ReTx 原始曲线供审计 | 预置曲线不是 3GPP 标准曲线；独立参考 TBS 所需的分块曲线尚缺 |
| 接收机与谱效 | MMSE、IRC、ZF 等，独立注水容量上界 | 所选预编码的高斯码本谱效不能直接叫 MIMO 容量上界 |
| CA | 同站同步、多载波共享业务，整数分流与 CORT 资源扩展 | 不支持混合子载波间隔、异步 TDD、跨站协调或自动辅小区激活；暂无 GUI 表单 |
| 控制信道 | TBS 预算包含等效 PDCCH 开销 | 未模拟 PDCCH/CCE 调度容量，不能据此判断小包控制信道瓶颈 |

场景探测会压缩频域与符号网格。位置、路损、SIR 等保持原几何条件；SNR 随每 RB 功率改变，探测器先还原全带 SNR，再由 SIR 重算 SINR。探测结果用于选场景，不代替全量谱效或吞吐实验。

信道生成与系统重复实验有两套并行参数：`workers` 与 `replication_workers`。支持的统计信道分块保持同 seed 与全局样本索引；不支持的配置会明确报告回退原因。耗时以本次 `elapsed_s` 为准，历史加速倍数不作当前性能承诺。

## 安装与文档

需要 Python ≥ 3.10。完整步骤、MCP 配置与离线安装见 [INSTALL_AGENT.md](INSTALL_AGENT.md)。

```powershell
python -m pip install -e .
python scripts/install_agent_skills.py --role simulation
python scripts/install_agent_skills.py --role simulation --check
```

可选射线追踪另装 `sionna-rt`。内网交付用 `python scripts/make_offline_bundle.py`，默认完整包包含构建依赖；`--thin` 要求接收端自备依赖。离线 wheel 必须匹配目标平台与 Python 版本。

| 文档 | 用途 |
|---|---|
| [技术手册](docs/index.html) | 算法、公式、手算例子、配置表与实现索引；单文件离线阅读 |
| [CA 使用说明](docs/ca.md) | 多载波输入、分流规则、KPI 与不支持项 |
| [Agent 仿真手册](skills/channel-sim/SKILL.md) | 需求落实、数据交付、比较证据与结果解释 |
| [协作规则](.agents/README.md) | 工作位、独立合并位与本地验证 |
| [历史变更](docs/changes/README.md) | 查当时为什么这样改；数值按当时版本理解 |

根目录旧版专题 HTML 为历史快照；当前算法与接口以 `docs/index.html` 为准。重新生成主手册：`python scripts/make_developer_guide.py`。

## MCP 工具（36 个）

工具按需求与配置、信道生成与体检、链路测量、系统仿真与比较、外部结果、干扰诊断分组。完整签名从源码自动生成，见[工具索引](docs/index.html#/tools)；运行时以 `sr_capabilities` 和客户端实际工具 schema 为准。

## 从一句需求开始

`sr_plan` 先提取原话已给出的阵型、场景、数量和扫描档位，再询问会影响结论的条件。
`sr_revise` 的回答直接更新执行配置；`accept_recommended=True` 接受可推荐项，保留明确要求。
不支持的室内穿透、阵型及非法档位会阻断生成。UMi 推荐落实 10 m 站高；显式 LOS/NLOS 保留。
用户指定的预期和自定义研究目标由用户回答。原话、假设、未建模条件与最终配置随计划一并返回。

## 开发与验证

阿里云 `develop` 是开发主线；每台电脑保留一份主仓库，任务在独立 worktree 中修改和自测，上传候选分支后交由另一合并位验证。合并位通过完整版本闸门后发布到云端。历史 `superran-lead` / `superran-member-task` 流程已废弃，按 `.agents/` 工作。

当前共 **30 个可执行测试文件**。先将 `PYTHONPATH` 指向本次工作区的 `src`，检查 `superran.__file__` 确认导入正确，再跑相关文件。聚合入口按文件执行并核对注册集：

```powershell
$env:PYTHONPATH = Join-Path $PWD 'src'
$env:PYTHONIOENCODING = 'utf-8'
python -c "import superran; print(superran.__file__)"
python scripts/run_test_matrix.py --tier full
```

文档生成器改动运行 `python tests/test_developer_guide.py`，页面交互运行 `python scripts/run_developer_guide_qa.py`。经典机制基准使用 `python scripts/run_classic_comm_benchmarks.py`；它验证实现关系，不替代现场校准。

## License

MIT。可选 Sionna RT 按其自身许可证使用。
