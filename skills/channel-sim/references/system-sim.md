# 系统级仿真参数详解 —— `sr_system_sim`

**什么时候读这一份**：主文件「第 5 段」里的旋钮不够用、要解释某个 KPI
是怎么算的、要调话务或邻区负载、或者 `notes` 报了一条你不确定怎么处理的。

主文件里已经写死的三条不在这里重复：**每 UE 要 ≥8 个快照**（可由多个单时隙样本组成）、
**`cell` 与 `users` 两级都要看**、**`notes` 逐条转述**。

## 完整签名

```python
sr_system_sim(
    dataset_id,
    evaluation_mode="capacity", duration_s=5.0,
    traffic_model="ftp3", file_bytes=500_000, arrival_rate_hz=2.0,
    small_ue_share=0.5, small_file_bytes=1_500, small_arrival_rate_hz=20.0,
    small_pdb_ms=20.0, large_pdb_ms=300.0,
    packet_size_cdf=None, interarrival_cdf=None,
    packet_size_scale=1.0, interarrival_scale=1.0,
    interarrival_cdf_unit="ms", traffic_profiles=None,
    target_prb_utilization=None, load_calibration_axis="interarrival",
    load_calibration_tolerance=0.02, load_calibration_max_iterations=6,
    load_calibration_replications=2, load_calibration_formal_refinements=2,
    scheduler="pf", pf_window_tti=100, pf_accounting="auto",
    target_bler=0.1, olla_step_up_db=0.01, olla_step_down_db=None,
    qos_avg_rate_exponent=1.0, qos_instant_rate_exponent=1.0,
    qos_delay_exponent=0.0, qos_priority_weighting="none", mu_enabled=False,
    mu_olla_step_up_db=0.01, mu_olla_step_down_db=None,
    trim="tail", small_burst_policy="fractional_slot", tdd_pattern="DDDSU",
    neighbor_prb_util=0.3, neighbor_load_jitter=0.05,
    csi_aging=True, srs_period_ms=10.0, srs_hopping=True,
    csi_processing_delay_ms=2.0, csi_report_period_ms=20.0,
    warmup_s=1.0, olla_speedup=1.0, olla_warmup_speedup=1.0,
    precoder="svd", power_constraint="nebf", seed=0, num_replications=8,
    kpi_focus=None, kpi_intent="",
)
```

## 先选模式：`capacity` 与 `experience`

| `evaluation_mode` | 版本 | 调度/资源 | 失败包 | KPI 边界 | 用途 |
|---|---|---|---|---|---|
| `capacity` | `legacy_v1` | 历史全带口径，单次选择一个 SU 或一对 MU（MU 读 pair 表，默认） | 每 TB 最多一次 IR/CC；同 MCS/RBG 数/rank/TBS；重传恒为 SU | `trim=none/tail/head_tail` | 复现旧结果、满缓冲容量、公平性 |
| `experience` | `experience_v2` | TBS 反查最小 RBG，同 TTI 可排多个 UE，尾料可留空 | 每 TB 最多一次 IR/CC；失败字节留 FIFO，后续成为新 TB | DRB busy-period + FIFO 到达对象；小 burst 可按 fractional slot | 大小包混跑、等待/PDB、按需分配 |

两者是**两个评估 profile**，不是一个算法的快慢档。当前 `experience_v2` 支持
两用户、每用户 rank2 的数据受限 SU/MU 自适应；矩阵运算集中在
`build_link_tables` 建表相，TTI 主循环只查 pair 表。体验模式多了逐 TTI 的 FIFO 与 RBG 分配，因此旧版的
“40000 TTI 只要 0.2 秒”不能当成它的性能承诺。

## 重复实验与置信区间 `num_replications` / `seed`

**每个 KPI 都是 `{mean, std, ci95, n_rep, cv, rel_half_width, min, max}`，不是裸数。**
默认 `num_replications=8`（对应 ns-3 的 `RngRun`），区间用 **t 分布**算——n 小的时候
用 z 会把区间报窄 20%。返回里的 `kpi_format` 块会把这套格式解释给用户。

**默认 8 是算出来的，不是拍的：**

- `n <= 5` 时 Wilcoxon 符号秩检验最小可达 p 是 `2/2^n > 0.05`，**无论数据多干净都
  不可能宣告显著**——而它照样会算出漂亮的百分比。n=6 是硬下界（p_min=0.031），
  8 留了余量（p_min=0.0078）。
- 代价可控：`build_link_tables` **与随机种子无关，只建一次表**，重复的只有 TTI 主循环。
  实测 ds_6e9715bc 上建表 5.14 s、单次主循环 0.99 s，n=8 是 13.0 s vs 单次 6.1 s，
  **墙钟 +113%**。建表越贵这个比例越低（按 10.5 s / 1.1 s 算是 +66%）。
- 区间随 n 收窄（从 64 次重复里重抽 500 次取平均半宽，总体变异系数 9.4%）：

  | n | 2 | 4 | 6 | 8 | 12 | 16 | 32 |
  |---|---|---|---|---|---|---|---|
  | 半宽/均值 | 60.9% | 13.7% | 9.4% | 7.6% | 5.8% | 5.0% | 3.4% |

  **"按 `1/√n` 收窄"精确成立的是标准误，不是区间半宽**——半宽还乘着
  `t_{0.975,n-1}`，小 n 时 t 大得多（`t₃=3.18` vs `t₁₅=2.13`），
  所以半宽实际收得**比 `1/√n` 更快**（n 4→16 是 0.357 而不是 0.5）。混为一谈会差 30%。

`num_replications=1` 退回"单次运行、无区间"，并在 `notes` 里明确告警——**这种结果
不能用来做任何比较**。

**`seed` 是实验批次的主种子（`RngSeed`），重复实验不要靠改它**——改它等于换一整个
宇宙，两批之间没有"流不重叠"的保证。要重复就调 `num_replications`。

### 重复实验进程 `replication_workers`

MCP 默认 `"auto"`：以 `n_rep × num_tti × num_ues` 判断是否值得支付 Windows spawn
和链路表序列化成本，小任务串行，长任务最多 4 进程；用户可显式设 1/2/4/8。
库函数 `simulate_replications` 默认仍为 1，保护 REPL 和没有安全 `__main__` 的脚本。
显式值失败或超过 CPU/重复数/内存安全上限时硬报错；只有 auto 可带明确
`fallback_reason` 回退。结果的 `parallel` 字段记录 requested/actual/backend/阈值。

本机交错三轮中位基准（6 UE、8 次重复）：5 s 为 1.601→0.994 s（1.61×），50 s 为
14.197→4.495 s（3.16×）；有限 KPI exact、非有限类别一致。4 线程为 0.72~0.74×，
因此不提供线程后端。该数字只说明当前版本的调度开销，实际任务仍以返回的
`elapsed_s` 为准。

### 随机流是分开的

`rng.STREAMS` 把随机数按用途分流：`channel`（生成与撒点）/ `traffic`（FTP3/mixed 泊松到达、
legacy bimodal 的 RBG 尺寸抽样）/ `scheduler`（度量打平时的随机决胜）/ `harq`（ACK/NACK 伯努利）
/ `neighbor_load`（邻区利用率逐快照抖动）。

**分流的好处非常具体**：改话务模型不会连带改变信道实现，A/B 才是受控的。
分流之前一个 `rng` 同时喂话务和 HARQ——改一下 `arrival_rate_hz`，抽到的到达次数变了，
后面 HARQ 的伯努利序列**整个错位**，于是"话务模型的影响"里混着"HARQ 换了一批随机数"。
**这类污染在结果里看不出来。**

`experience_v2` 在 `traffic` 顶层流内还按稳定名字拆成
`profile_assignment / arrival_count / packet_size / interarrival / initial_phase` 五条子流。
子流键同样取名字的 CRC32，而不是列表下标。于是把 `interarrival_scale` 从 1 改成 0.5
不会因为到达事件增多而把后续包长抽样整体错位；双标量校准比较的仍是同一批基础 CDF
抽样。结果的 `traffic_samples.substream_scheme` 会记录这一点。

### A/B 必须用公共随机数（CRN）—— `rng.compare_replications`

CRN 是经典的方差缩减技术：比较两个方案时对应的两次运行用**同一批伪随机数**，
让观测到的差异归因于方案本身而不是随机波动（`Var(a−b) = Var(a)+Var(b)−2Cov(a,b)`，
CRN 就是把那个协方差做正）。**两臂拿同一个 `rng.replications(master_seed, n)` 的
返回值就是 CRN。**

实测收益（A/B：PF 窗 100 vs 1000，n_rep=8，真实效应约 −10 Mbps）：

| | 效应 | 95% CI | 半宽 | Wilcoxon p | 判决 |
|---|---|---|---|---|---|
| CRN | −10.64 | [−14.14, −7.15] | 3.49 | 0.0078 | significant |
| 独立随机数 | −14.97 | [−28.66, −1.27] | 13.69 | 0.078 | inconclusive |

**同一个真实效应，CRN 判得出来、独立种子判不出来**：区间窄 **3.92 倍**，
差值标准差 4.18 vs 16.38。注意独立那一栏的区间其实不跨零，但**判决以 Wilcoxon 为准**
（复用 `gates.paired_compare`，就是门 3 那套），照样拦住了。

`rng.check_pairable(books_a, books_b)` 是**硬拦截**：两臂重复次数不一致、
或者第 k 次重复的 `(master_seed, replication)` 对不上，直接返回
`verdict="not_pairable"` 而不给 p 值。**顺序被打乱这种错位在统计层面完全不可观测**
——统计只看数值数组，不知道第 i 个数对应哪一次重复，错配数据照样算得出漂亮的 p 值。
没传 `books` 时 `crn` 报 `None`（"没法查"），**不会当成查过了**。
`require_crn=False` 允许独立随机数，结果仍然是对的，只是区间明显更宽。

判据有两种等价说法，`verdict_text` 两句都写：**"95% 置信区间跨零"**与
**"效应比置信区间还小"**——对称 t 区间下 `|mean| < h` 与"区间含 0"是充要的。

`sr_system_sim(..., algorithm_label=...)` 现在会把聚合 KPI 之外的比较证据保存进
`kpi_view.result_id` 对应 JSON sidecar：逐 replication 小区 KPI、RngBook、算法标签和
有界 TTI trace。2..5 个算法臂交给 `sr_compare_system_results`，由它复用
`rng.compare_replications` / Gate 3；同一主 KPI 有多个候选时追加 Holm step-down。
任何 dataset、模式、时长、载波、TDD、话务、KPI 或逐位 RngRun 不一致都会硬拒绝。

TTI trace 默认 `sampled`：一半预算是跨算法共同的均匀 TTI 锚点，另一半是
MU/NACK/重传/多 UE/outage 关键事件；`full` 显式保留测量窗全部 DL TTI，`off` 关闭。
详情包括 RBG bitmap、候选/调度 UE、MCS/rank、预测与接收 SINR、BLER 与随机 draw、
ACK、OLLA 前后、PF metric/平均速率及 SU/MU 选择原因。**单 TTI 是机制诊断，不是
统计样本，绝不能替代跨 replication 的 Gate 3。**
只有 dataset 的生成前 prereg 同时匹配主 KPI 与基线标签时才允许标 publishable winner；
否则即使统计显著也保持 exploratory_unregistered。

### 区间覆盖什么、不覆盖什么

各次重复共用**同一批信道与同一张链路表**，所以 `ci95` 覆盖的是**话务到达、ACK/NACK 抽样、
调度决胜**这三条流。返回里的 `rng.covered_by_ci` / `not_covered_by_ci` / `ci_scope`
把这件事显式写出来——**别把它当成"全部不确定度"**。

**信道实现本身的不确定度是另一个、更大的方差分量**，这个函数不做也做不到——
要覆盖它得用不同 `seed` 重新 `sr_generate` 再比。

这个取舍是量过的（`measurements/rng_replication.json`）：64 次 replication（表固定）
与 32 次 master seed 扫描（每次重建表、负载抖动重抽）的变异系数对照里，
五个 KPI 有四个的区间重叠——**冻结链路表并没有可分辨地把离散度报小**，
系统级的主导方差就是话务与 HARQ，正好是区间覆盖的那几条流。

顺带一个必须记住的量级：**n=8 时变异系数自身的 95% 区间是 0.66×~2.04×**。
`measurements/seed_variance.json` 里那个 11.4% 是 8 个种子测的，真值可能在 7.5%~23%
之间——**那张表上的 CoV 只精确到大约 2 倍**，别拿它做精细比较。

### `rel_half_width` 是这次实验的分辨率

区间半宽 / 均值。**比它小的差异，这次实验分辨不出来。** `notes` 会在头条 KPI
（体验速率、边缘体验速率、`cell_served_mbps`、`avg_mcs`、`avg_rank`、`bler_first_tx`）
里挑相对区间最宽的那个单独点名。要下更细的结论就加 `num_replications`。

**这条规则有过真实事故：** 同一批信道、同一套配置只改种子，`cell_experienced_mbps`
的变异系数实测 11.4%，而上一轮把这 11.4% 的噪声报成了「+14% 提升」。

## 载波栅格与 numerology（固定产品合同，不是用户参数）

`sr_system_sim` **不接受**带宽/RBG 数/子载波间隔参数，因为当前 TDD 系统口径已经
冻结为 100 MHz @ 30 kHz、272 RB = 17 RBG × 16 RB。38.104 的标准表值是 273 RB；
SuperRAN 在系统级信道生成前明确舍去 1 RB。张量宽度必须真是 272，配置中的带宽、
SCS、BWP 起点与 RBG configuration 若存在也必须匹配；任何错配立即失败，不自动生成
一套窄带系统口径。返回 `carrier` 段供交叉核对：

| 字段 | 含义 |
|---|---|
| `num_rb_in_channel` | 信道里真实的 RB 数 |
| `num_rbg` × `rb_per_rbg` | 固定 17 × 16 |
| `standard_num_rb` / `standard_tail_rb_omitted_before_generation` | 标准 273 与生成前明确舍去的 1 RB |
| `scs_khz` / `tti_ms` | numerology 与 TTI 长度 |
| `profile_id` / `user_configurable` | 版本化产品合同；后者固定为 false |

`CarrierGrid.from_config()` 等通用 Type-0 工具仍服务链路级、导入诊断和数学回归；
保留这些内部能力不代表 `sr_system_sim` 已支持 51/106/273 RB。未来扩带宽时需要新增
版本化 profile、SRS 资源、TBS/功率/KPI 分母的整套合同，不能只放开一个输入框。

历史坑：这里曾经把 `num_rbg` 写死 17（= 272 RB）、`scs_khz` 从来不设（默认 30），
而同一个函数里的快照间隔一直是从 `subcarrier_spacing` 算的。**一半跟数据集走、
一半写死**，不报错也不告警——实测 51 RB 的数据集上小区吞吐报 756 Mbps，
按真实带宽只有 133 Mbps。

## 话务 `traffic_model`

| 取值 | 是什么 | 什么时候用 |
|---|---|---|
| `ftp3` | 3GPP FTP Model 3，泊松到达的固定大小文件 | **默认**，评价体验速率的标准话务 |
| `mixed` | 一部分 UE 发 1500 B 小文件，另一部分 UE 发大文件；包长和到达率都是外生量 | **experience_v2 推荐**，验证“小包不再偷走整个 TTI” |
| `cdf` | 两份 `value,cdf` 文件分别驱动包大小与逐 UE renewal 包间隔 | 接现场话务 CDF；外部曲线未接入前只能用明确标注的 synthetic 输入 |
| `bimodal` | legacy_v1 按目标 RBG 数反推包长的历史模型 | 只复现旧结果；experience_v2 因因果倒置会拒绝 |
| `full_buffer` | 缓冲区永不空 | 只看容量上限。**体验速率在这个模型下没有意义** |
| `cbr` | 恒定比特率 | 固定码率业务 |

`ftp3` 的负载由 `file_bytes × 8 × arrival_rate_hz` 决定，
返回的 `config.traffic.offered_load_mbps_per_ue` 直接给出每用户提供负载。
**太高会积压**，`notes` 会拦（`backlog_bytes > 15%` 的到达量时报出来）。

`mixed` 由 `small_ue_share`、`small_file_bytes`、`small_arrival_rate_hz` 和大文件侧
`file_bytes`、`arrival_rate_hz` 定义。大小 UE 的类别在仿真开始前固定，不由信道好坏或
目标 RBG 数反推。只有大小混跑才有可识别的资源共享效应：全大包时每人仍要全带；
全小包时没有大包体验可比较。

### 经验 CDF 与多 profile

CDF 文件是 UTF-8 两列，默认表头为 `value,cdf`；分隔符支持逗号、分号、tab 或空格，
概率可写 0..1 或 0..100，value 必须为有限正数且严格递增，CDF 必须单调并收敛到 1。
包大小单位固定 byte；包间隔默认 ms，也可把 `interarrival_cdf_unit="s"`。相对路径固定
从 superran 项目根解析，结果保存绝对来源路径和 SHA-256，避免 MCP cwd 改变后读错文件。

存在包间隔 CDF 时，到达过程是逐 UE renewal process，`arrival_rate_hz` 不再决定该 profile
的到达时刻；存在包大小 CDF 时，`file_bytes` 只作无 CDF 时的 fallback。全局与 profile
局部标量相乘：

```
effective_size_scale = global_size_scale * profile_size_scale
effective_interval_scale = global_interval_scale * profile_interval_scale
offered_load ∝ effective_size_scale / effective_interval_scale
```

`traffic_profiles` 每项使用 `TrafficClassConfig` 字段。`ue_ids=[...]` 可把 video/XR 明确
绑定到用户；显式 ID 先分配，剩余 UE 再按 `ue_share` 分配。跨 replication 随机分配的
profile 会在用户级显示 `varies_across_replications` 与逐类计数，绝不拿第 1 轮标签冒充固定。

项目提供的 `presets/traffic/synthetic_*.csv` 只用于跑通接口与校准流程，**不是实测话务、
不是 3GPP 标准 CDF，也不能拿它下现场结论**。

### 目标 PRB 利用率校准

`target_prb_utilization=0.30` 表示设计目标，不是结果覆盖值。控制器默认只调包间隔：
负载不足就减小 `interarrival_scale`，负载过高就增大；`packet_size` 轴只调包长，
`balanced` 在 log 域把倍率均分给两轴。每轮 probe 固定同一个 master seed 与 replication ID，
保存倍率、双标量、实测利用率和 CI；选出最接近点后，正式 `num_replications` 使用同一
master seed 下与 probe 不重叠的 RngRun 区间独立汇总。正式反馈轮内部继续复用同一组
RngRun，保证每次只比较负载倍率。

只有正式均值落在 `target ± load_calibration_tolerance` 才返回 `status="target_met"`；否则
返回 `formal_result_outside_tolerance` 并把实测值保留在结果与 notes。校准是场景设计，不是
算法 A/B 判决；probe 默认 2 次只为控制器降噪，不能拿 probe 区间声称算法显著。若首轮正式
均值仍偏离，默认最多做两轮正式样本反馈校正，最后选择正式轮中离目标最近的一轮；完整轨迹
保存在 `formal_history`，不会只留下“成功”的最后一点。

## KPI 口径：legacy `trim` 与 experience busy-period

`legacy_v1` 才读取 `trim`：`tail` 扣清空缓冲区的末 slice，`head_tail` 还把起点挪到
首次调度，`none` 不扣。这是历史实现，用于复现，不再冒充 Rel-19 的唯一口径。

`experience_v2` 忽略 `trim` 的数值作用，改用事件记录器：

1. buffer 从空变非空创建 DRB busy period，之后到达的数据合并，直到 ACK 后重新为空。
2. 大 burst 的标准吞吐时间从**第一次传输**开始，末端排除最终让 buffer 变空的 ACK piece；
   从到达到首传的 queue wait 单独上报。
3. 如果所有 buffered data 在一次初传 TB 内送完，`small_burst_policy="fractional_slot"`
   用 `(TBVol-PaddingVol)/TBVol × slot` 折算有效时长；`exclude` 可显式保留旧式盲区。
4. 每个 FTP/mixed 文件还是一个独立 FIFO arrival object，分别记录 first-schedule wait、
   completion delay 与 PDB miss；一个 DRB busy period 可以包含多个 arrival object。
5. `first_packet_delay_ms_*` 是每个 arrival object 从生成到第一次调度的时延；从未调度的
   对象作为右删失单列，不伪造成有限时延。
6. `*_head_inclusive_*` 与对应掐头去尾速率使用相同 payload numerator 和去尾规则，
   唯一差异是把 busy period 的 arrival→first schedule 等待加回 denominator。

因此 `small_queue_wait_ms_p95` / `small_completion_delay_ms_p95` /
`small_pdb_miss_ratio` 与 busy-period throughput 是不同层级的指标，不能互换。

### 本小区 PRB 利用率与逐 TTI RBG 分布

`serving_cell_prb_utilization` 是**结果 KPI**：KPI 窗口内所有可用 DL 调度机会的
`allocated PRB-equivalent / available PRB-equivalent`；D slot 权重 1，S slot 按当前
工程口径权重 0.7。分母不纳入纯 UL/保护 TTI，所以 full-buffer 下应为 100%。旧字段
`resource_utilization` 是它的兼容别名。

`tti_occupied_rbg_distribution` 每个可用 DL TTI 只记一次，横轴固定为 0..17 个已占用
RBG，**包含 idle TTI 的 0 桶**。不要拿 `rbg_size_hist` 代替：后者是每个非零 grant
分了几个 RBG，一个 TTI 多用户时会记录多次，回答的是另一个问题。

`mu_paired_prb_share_of_used` 是 MU 生效 PRB-equivalent / 已占用 PRB-equivalent；
`mu_paired_prb_utilization` 是 MU 生效 PRB-equivalent / 全部可用 PRB-equivalent；
`mu_paired_prb_equivalent` 给原始数量。50% 小区负载且全部已用资源都成功配对时，前两者
分别是 100% 与 50%，不能只报一个含糊的“MU 配对比例”。

用户级同时给两套资源口径：`grant_prb_equivalent` 在共享 MU PRB 上对每个配对 UE 都计一次，
适合回答“该 UE 有多少资源处于 MU”，但不能跨 UE 相加；
`allocated_prb_equivalent_attributed` 将共享 MU PRB 等分给配对 UE，硬不变量是
`sum(user attributed PRB) == cell allocated PRB`。用户级 MU 主比例是
`mu_paired_prb_share_of_user_used`。

### Agent 自适应 KPI 页面

`kpi_view` 顶层固定为“小区级 / 用户级”两个 tab。用户级每个支持指标同时给按 UE 图、
跨 UE 经验 CDF 和全量明细表；这里的 CDF 样本是“各 UE 的 replication 均值”，不是包级 CDF。
调用本工具的 LLM/Agent 应根据用户问题显式传 `kpi_focus`（KPI key 或关注词）；页面优先展示
这些指标、其余折叠，并在 `kpi_view.kpi_selection` 保存来源、标签、理由和完整排序。
未传时才按 `kpi_intent` 关键词与场景配置做确定性兜底。库内不暗调另一个模型，保证复现与审计。

**换口径数字会明显变，所以报数时必须带上用的是哪个 trim。**

## 邻区负载 `neighbor_prb_util`

ChannelHub 的几何 SINR 是按**所有邻区都在发**算出来的，等于 100% PRB 利用率。
真实 5G 网络典型是 10% / 30% / 50%。按 full buffer 算会把干扰放大到不真实的程度，
所以默认取 **0.3**；设 1.0 退化成原行为。

**当前入口只支持全网统一值**。这是产品接口边界，不再是数据边界：新数据集保存
`dl_interference_power_per_slot_per_cell_mw[sample,slot,cell]`，RB 功控已经能逐邻区
重算干扰；逐小区负载表尚未开放。

`neighbor_load_jitter=0.05` 让实际生效负载在配置值 ±5% 内逐快照波动。
恒定负载会让所有快照的干扰完全一样，结果比现网干净。

这和结果里的 `serving_cell_prb_utilization` 也不是一回事：前者是邻区干扰输入，后者由
本小区话务、用户撒点、链路和调度共同决定。10%/30%/50% 是目标负载场景时，应调话务并
以实测 KPI 验收，不能把目标数硬写回结果；一般业务主场景可聚焦 30%，MU 压力场景可聚焦 50%。

注意这和信道生成阶段的 `pdsch_load` 不是一回事：后者在下行**完全不起作用**
（见 `scenarios-and-interference.md`），`neighbor_prb_util` 是系统级仿真自己
在链路表上做的折算。

## 逐 RB 功控 `rb_power_control_enabled` / `rb_power_overrides`

默认关闭，等价于每小区每个 RB 都是 `1x`。开启后，每个小区得到一行连续倍率
`q[cell,rb]`，硬约束是：

```text
0.1 <= q[cell,rb] <= 4.0
sum_rb q[cell,rb] == N_RB
```

用户只需写要改的 RB，未指定 RB 统一补偿到总和守恒；补偿值越界、区间重叠、
RB/小区越界、全覆盖但总和不等于 `N_RB` 都硬失败，不会偷偷归一用户指定值。

```json
[
  {"cell_index": 0, "rb_start": 0, "rb_end": 15, "multiplier": 2.0},
  {"cell_index": 3, "rb": 80, "multiplier": 0.5}
]
```

对服务小区为 `s(u)` 的 UE，逐 RB 使用绝对功率项重算：

```text
SINR[u,r] = q[s(u),r] * S[u]
            / (N[u] + eta[u] * sum(k != s(u), q[k,r] * I[u,k]))
```

所以服务小区倍率改变期望信号；任一邻区倍率只改变该邻区对这个 UE 的干扰。
聚合 `SINR/SIR` 无法恢复这条式子，旧数据集缺逐小区分解时必须重新生成。

它与 `power_constraint=ebf/pebf/nebf` 正交：后者归一空间预编码矩阵，前者在其后
乘频域功率倍率。正式 A/B 应比较“功控开启但全 1x”与“功控开启且有 override”，
这样两臂都走逐 RB 物理路径，不把旧的 RBG 中心采样近似混入算法效应。

当前边界要随结果转述：逐 RB 的信号/邻区干扰耦合是精确功率运算；一个 TB 跨多个
RBG 时，有效 SINR 仍沿用项目既有的 dB 算术平均，尚未用链路级 BLER 曲线标定
EESM/MIESM。邻区是否占用该 RB 也仍由统一 `neighbor_prb_util` 概率折算，而非多小区
联合 TTI 调度。

预置 BLER 事件按一次已调度 TTI 中该用户的独立单码字 TB 计：grant 确定 TBS、MCS 和资源后，
以跨 RBG、跨 rank stream 做 dB 平均的码字级有效 SINR + MCS 查询一次通用 NewTx 曲线，
并只抽一次 ACK/NACK；CB 不作为系统层独立事件。TBS/RE/rank/场景不进入 BLER lookup 是
已确认的产品口径，不是数据缺口；它们仍用于承载字节、PF 记账与冻结重传身份。

## CSI 老化 `csi_aging` / `srs_period_ms` / `srs_hopping`

**默认开。** 关掉退化成零时延完美 CSI——那是个上界不是现网，MU 增益会被系统性高估。
保留这个开关是为了能做 A/B，把老化的代价量出来，而不是让它悄悄混进所有结果。

- 固定100 MHz系统且`srs_resource_allocation=on`时，`srs_period_ms`是最短候选，
  `srs_period_adaptive=on`默认从10 ms起选择能容纳全部小区的全局最短10/20/40 ms；
  5 ms只保留在关闭资源分配的链路级老化敏感性接口
- `srs_hopping` 默认开，对应 38.211 Table 6.4.1.4.3-1 的 `C_SRS=63` / `B_SRS=1`：
  `m_SRS=(272,16,8,4)`、`N=(1,17,2,2)`，每跳 16 RB 正好一个 RBG，
  按 `0,8,16,7,15,6,14,5,13,4,12,3,11,2,10,1,9` **17 跳**扫完 272 RB
- 这是当前唯一 hopping profile；`b_hop=0/n_RRC=0` 与顺序由 SuperRAN 本地版本化，
  非 272 RB 或其他跳频参数硬失败，不调用外部 helper、不做 identity fallback
- 2T4R终端一次只发送2个SRS ports：当前机会测端口0/1，下一可用机会
  （slot7→17，间隔5 ms）测端口2/3；同一RBG两腿完成后才推进hop。因此10 ms周期下
  全带仍约170 ms，但共有34次2-port SRS发送，64×4两列组有5 ms测量偏差
- **跳频是老化的主导项**：10 ms周期下某个RBG的端口组年龄在
  0~160 ms 之间轮转（平均 80 ms），而 2.6 GHz、30 km/h 的相干时间只有约 3 ms。
  实测 MU/SU 比值 0.816 → 0.449（−45%），SU 谱效 −27%
- `csi_processing_delay_ms=2.0` 是信道估计 + 预编码计算 + 调度下发的固定时延
- `srs_resource_allocation`默认开：每个机会8个symbol/comb格中排除两个加粗BBL格，
  每色保留2格；工程基线只开放4 CS，每次2T使用2 CS，同一叶子可放两个UE；
  再展开17个frequency resource。禁止跨PCI颜色借资源
- `srs_pci_mod3=0/1/2`是硬颜色分区；不同小区实验应显式给各自值
- 分配结果不是metadata-only：两个leg的offset和frequency id分别生成
  `[snapshot,2,RBG]` lag，再从不同历史快照拼端口0/1与2/3。10/20/40 ms
  每个PCI颜色容量为68/136/272个2T4R UE
- P-H/F、BWP2、根序列规划和真正的多端口导频波形/跨小区污染
  当前未建模。`scheduler_p0_validation.json` 中的 PCI 模3结论是等功率 LS-NMSE proxy

返回里的 `csi_aging.requested_config` 保存用户请求下限，`effective_config` / `config`
保存 allocator 真正采用的全局周期；全带扫描时间读 `config.full_sweep_ms`，平均年龄读
`mean_csi_staleness_ms`。只能转述 effective 值，并同时说明 requested→effective 是否升档。
显式 `ue_speed_kmh=0` 是合法静止条件，不得当成缺省 3 km/h。

**快照间隔不是一个 TTI。** ChannelHub 的多时隙输出是连续的 SRS/CSI-RS 机会，
默认 `10 × 0.5 ms = 5 ms`，由 `system.snapshot_interval_ms(cfg)` 从配置算出。
把它当成 0.5 ms 会让**所有时间相关的结论差 10 倍**。

## 调度与 OLLA

`scheduler="pf"` 比例公平，度量 `R_inst / R_avg`，`R_avg` 按 `pf_window_tti=100`
的指数窗更新。`legacy_v1` 的 `R_inst` 是历史全带 `best_se`；`experience_v2` 的默认
`pf_accounting="scheduled_tbs"` 是这次实际分配的 TBS/TTI。**部分带宽 UE 若仍按全带
记账，会让它的 PF 平均速率虚高最多约 17 倍，随后被错误饿死。**

`scheduler="qos_pf"` 使用显式参数
`w(priority) * R_inst^beta / R_avg^alpha * delay_factor^gamma`。默认
`alpha=beta=1, gamma=0, w=1` 严格退化经典 PF；这只是参数化 QoS-PF，**不冒充尚未确认
厂商定义的 EPF**。

**基站按陈旧 CSI 选 rank 和调度**（`best_se_gnb`），不是按真实 SINR——
拿真实 SINR 挑 rank 等于让基站预知信道，老化损失会被凭空抹掉一大半。

OLLA 默认只要求用户给 `target_bler`。ACK 步长默认 **+0.01 MCS**，NACK 步长为
`None` 时按 `down=up*(1-target)/target` 自动反解；目标 10% 时才得到 **−0.09 MCS**。
顺序是先由 `SINR_AMC_PRED`（CQI 门限 + gNB 可见 BF Gain，不是物理 TX/RX
SINR）反折无 OLLA MCS，再加连续 MCS offset、floor 并钳位；BLER 只查真实接收
`SINR_NEBF/PEBF/EBF_RX`。
用户显式填写 SU/MU down 步长时保留该值并在结果标为 override；SU 与 MU 各自反解、
状态仍按用户独立维护。（现网口头常说的 −0.1 对应 9.09%。）
历史 `*_db` 参数名仅为 API 兼容保留，值的单位不是 dB。
**稳态与步长绝对值无关**，所以 `olla_speedup` 等比放大只改收敛速度与稳态抖动：
但 MCS-domain + floor 口径下的具体收敛速度和稳态偏差必须重新标定。
2026-08-23 之前 dB-domain OLLA 的 IBLER/速率数字只作历史追溯。
**出正式结论设回 1.0**，非 1.0 时结果里会带一条显式告警。

`avg_mcs` 报的是 **OLLA 之后**的 MCS，即实际调度下去的档位。

`experience_v2` 目前只接受 `preset_20b_256qam / MCS table 3` 预置表，Table 1/2
传入后硬失败。代码保留显式 `mcs_table/profile` 边界与带数据指纹的 BLER cache key，
但必须等下一套 MCS、TBLER/TBS 元数据完整接入后才允许扩展。

## 调度 P0：资源账、逐 RBG 频选与统一 FinalGrant

`frequency_selective` 与 RB 功控相互独立：`auto` 在 17 个逐 RBG predicted/receive
SINR 字段完整时开启，`on` 缺字段硬失败，`off` 是宽带/轮转基线。质量排序前缀和
顺序前缀同时评估；能清空队列时取最少 RBG，否则取 predicted useful bytes 最大者。
一个 grant 跨 RBG 的有效 SINR 仍按已确认的 dB 算术平均，再选一个单码字 MCS/TBS。

每套 SU/MU plan 在比较前都通过 `ResourceLedger`：

- 物理 RBG/PRB 一个 TTI 只能被一个 grant 占用；MU 共享 bitmap 只扣一次
- 每 RBG 总层数默认最多 4；rank2+rank2 正好占满
- 逻辑资源按 `sum(rank) × physical PRB` 记账，默认预算 `272×4=1088 layer-PRB`
- reserve/commit/rollback 不修改队列、HARQ、OLLA 或随机流
- PDCCH/CCE、最大 grant/UE 数按已确认范围暂不建模，结果会显式标注

选中计划统一进入 `GrantFinalizer`，重算 predicted SINR 输入、OLLA 后 MCS、实际
bitmap TBS、payload/padding/useful bytes，并与 planner 估值逐值硬比较。SU/MU/NewTx/ReTx
都带 `finalizer_version` 和 `reservation_id`；HARQ 的 MCS/RBG数/rank/TBS 仍冻结。

## MU `mu_enabled`

默认 **False**，先看清 SU 基线。**两种模式现在都读同一张 pair 表**
（`mu_accounting="pair_table"`，默认）：在建表阶段预计算所有两用户、每用户 rank2
的 pair 链路，MCS 输入按 `CorrLoss + PowerLoss` 平移、TBS 按该 MCS 全带算、
误块抽签用 pair 的 `true_sinr_db`。`legacy_v1` 的历史聚合 `mu_gain` 标量近似降级为
`mu_accounting="se_ratio_legacy"`，**只用于复现旧结果**——它只缩 TBS、不进误块抽签，
结果系统性乐观，选用时会写进 `notes`。capacity 的 SU/MU 判决是逐 TTI 比聚合谱效
（还要过 predicted BLER ≤ 0.5 的准入），拒配对的两种原因分别计入
`mu_pair_rejects` 与 `mu_su_wins`；重传恒按 SU 重发（冻结身份不许改 SINR/TBS）。TTI 主循环先固定 PF anchor，再枚举全部伙伴；缺 pair、相关性超门限、
总层数超限或 predicted BLER > 0.5 都留下明确 rejection reason。可行伙伴按
`sum(min(queue,TBS))/shared_RBG` 评分，不再取 PF 顺序里的第一个可行者。

MU MCS 口径是 `CQI + BF + SU-OLLA + CorrLoss + powerLoss + MU-OLLA`：两个 rank2 UE
相对 SU rank2 的等流功率损失固定为 `−10log10(2)=−3.0103 dB`；CorrLoss 来自 pair
的基站侧预测 SINR 差分；SU/MU OLLA 是两组独立的用户级状态，不按配对关系拆分。

每个 DL TTI 只做一次 PF 排序。若 SU 能发完所有队列就强制 SU；否则比较队列封顶后的
useful payload bytes，只有 MU 不小于 SU 才选 MU。接收端用 per-user LMMSE，预编码只看
`h_est`，BLER 用 `h_true`。当前工程边界仍是两用户 rank2、ZF/RZF；更一般的 MU 配对与
现场算法待后续接入。

方向性证据（固定合成反例，不是一般现场收益承诺）：互补 8/9-RBG 两用户在相同 CRN 下，
频选 on/off 的 ACK 吞吐为 486.52/148.71 Mbps；MU 例中 UE1 虽是更早伙伴，但 −5 dB
CorrLoss 只得 3315.8 useful B/RBG，后到 UE2 的 −1 dB CorrLoss 得 4701.6 B/RBG，
最终选择 UE2。完整逐 TTI 证据在 `artifacts/results/scheduler_p0_validation.json`。

## 发射权 `precoder`

- `svd`（默认）：逐 RBG 协方差特征波束。单快照时等价于瞬时 SVD；多快照时是
  `E[HHᴴ]` 上的一组静态权，**不是逐时隙 Shannon 容量上界**。
- `type1`：Type-I-style 单面板宽带**列码本子集近似**。过采样 DFT/双极化列来自
  Type-I 结构，多层用增量贪心列选择，尚未枚举 38.214 完整多层矩阵码本。码本自由度少，
  在 CSI 老化下可能更耐受。`type1` 时 BF Gain 恒为 0
  （发射权就是 CQI 的参照权）

CQI 的参照权始终是 `type1_wideband`，返回的 `precoder` 块会写明两者。

## `notes` 全清单

按触发条件列，每一条都是"这组数字在什么条件下不成立"：

| 触发条件 | 说的是什么 |
|---|---|
| 快照数 < 4 | 时间起伏被严重低估，PF 的多用户分集拿不到 |
| 快照数 ≤ 1 且开着老化 | 陈旧信道与当前信道是同一个矩阵，**老化效果恒为 0** |
| `csi_aging=False` | 预编码用零时延完美信道，是上界不是现网，MU 增益被系统性高估 |
| `measured_bursts < 20` | 进入体验速率统计的 burst 太少，加长 `duration_s` 或提高到达率 |
| 积压 > 15% 到达量 | 系统在这个负载下没收敛，体验速率被排队时间拖低 |
| 对账误差 > 1% | **这是 bug 不是现象**（发出去的 + 还压着的 应等于到达的） |
| `edge_mcs_p5 > 8` | 边缘 MCS 偏高（现场经验 <5），多半是撒点没覆盖到边缘或邻区负载设太低 |
| 首传 BLER > 目标 ×1.6 | 外环还没收敛完 |
| 有效 IoT 用户 < 90% | 多半是 `num_slots_per_sample > 1` 导致 SIR 与 SINR 口径不同 |
| IoT 中位 < 3 dB | 几乎是噪声受限，检查站间距或邻区负载 |
| `bimodal` PRB 利用率偏离 30% | 折合负载和现网口径对不上 |
| legacy `bimodal` 小包体验速率为 None | legacy 掐尾口径下单 slice burst 没有可测量时间；experience 可改用 fractional slot |
| `outage_ue > 0` | 有用户全程够不到 MCS 0 的门限，已从调度剔除——**这本身就是结论** |
| `serving_cell_prb_utilization > 98%` | 已过载，此时体验速率反映的是容量上限而不是用户体验；不能用“有调度的 TTI 占比”代替 |
| `olla_speedup != 1.0` | 步长被放大，稳态抖动更大，出正式结论要设回 1.0 |
| `num_replications < 6` | 判决检验结构上不可能显著，区间也不可信 |
| 头条 KPI 相对半宽 > 5% | 点名最宽的那个——比它更小的差异这次实验分辨不出来 |

多次重复的 `notes` **按"抹掉数字后的模板"去重**并标注命中几次
（"（6/8 次重复都触发；上面的数值取自第 1 次）"）——同一条告警在 8 次重复里
只差几个数字，全列出来会把真正不同的那几条淹掉。

## 对账三件套

`offered_mbps` / `completed_bursts` / `backlog_bytes` 一起报，
才能解释"实际吞吐 105 Mbps vs 话务负载 144 Mbps"这种缺口——
它可能是队列积压（正常），也可能是漏数据（bug）。`accounting_error_pct`
就是用来分辨这两种的。
