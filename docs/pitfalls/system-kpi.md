# 系统级仿真：栅格、队列记账与 KPI 口径

> 本文件由 `CLAUDE.md`「踩过的坑」按主题拆出（2026-09-25），正文原样搬移，条目标题保留原文；索引在 `CLAUDE.md`。
> 每一条都是真实事故，**删之前先想清楚**；改动口径时同步更新本条和 `CLAUDE.md` 索引的一句话要点。

**什么时候读**：改 `system.py` / `experience.py` / `scheduler_*.py` / `traffic.py` / `kpi_*.py`，或报系统级 KPI。

### 仿真粒度降到 RBG 是安全的

一个 RBG 内的 16 个 RB 共用同一个 MCS、同一次调度决策、同一个预编码，
**RB 级的分辨率没有任何已实现的算法在用**。降到 RBG（272 → 17）实测
rank 与 MCS **逐位相同**、谱效差 0.1%，建表快一倍。

聚合方式是 RBG 内**取中间那个 RB 作代表**，不是平均。平均会把频选衰落抹平、
让奇异值分布变平（信道条件数被人为改善），进而**高估 rank**。

会受影响的只有频选调度与导频图案，两者都还没做。真要做时把
`rb_per_rbg=1` 设回去就退回 RB 粒度。

> 整理注（2026-09-25，拆分时核对代码所加，非原文）：「会受影响的只有频选调度与导频图案，两者都还没做」疑似过时：`system.py` 已有 `frequency_selective="auto"`（`scheduler_frequency.py`），SRS 资源/波形也已实现。「取中间 RB 作代表」仍在 `mumimo.py`、`carrier.representative_rb_indices` 中使用；而表 3 BLER 查询是 RBG 内先转 dB 再平均（见 `link-adaptation.md` BLER 条），两者作用于不同环节。未找到明确推翻本条的记录，原文保留，待维护者裁决。

### TDD 系统栅格是固定产品合同，不允许拿链路级带宽混跑

`sr_system_sim` 早先把 `num_rbg` 写死 17（= 272 RB / 100 MHz），`scs_khz` 更是
从头到尾没设过（默认 30）。而同一个函数里的 `snapshot_update_ms` **一直**是从
`ds.config["subcarrier_spacing"]` 算出来的——**同一份配置一半跟数据集走、一半写死，
是最难发现的那种不一致**：它不报错、不告警，`notes` 里也没有任何线索。

后果按带宽平方级放大：实测 51 RB（20 MHz@30 kHz）的信道上小区吞吐报
**756.3 Mbps**，按真实带宽只有 **133.5 Mbps**。`presets.yaml` 里
`single_cell_4t4r`、`large_isd_1732m`（都是 20 MHz）、`large_isd_5km`
（10 MHz + **15 kHz**）全部落在这条路径上；15 kHz 那个还多吃一层——TTI 被当成
0.5 ms 而不是 1 ms，一秒里的调度机会翻倍，所有 ms 口径的时延 KPI 一起偏。

最终产品口径不是“动态适配任意数据集”，而是用户确认的固定合同：
**100 MHz、30 kHz SCS、272 个仿真 RB、17 个 RBG、每 RBG 16 RB**。38.104
表中的标准值仍是 273 RB；SuperRAN 在生成系统级信道之前就明确舍去 1 RB，不能把
273 RB 张量读进来后再悄悄截尾。`num_rb`、`rbg_size_config` 和 BWP 起点均不向
系统仿真 UI 开放。

现在 `server._carrier_grid(ds.config, num_rb=h.shape[2])` 只做**合同校验**：信道轴必须
实际等于 272，配置标签若存在则必须是 100 MHz / 30 kHz，BWP 从 0 开始且 RBG
Configuration 必须对应 16 RB。任一项不符立即拒绝运行。结果中的 `carrier` 段会记录
版本化 `profile_id`、`standard_num_rb=273`、`standard_tail_rb_omitted_before_generation=1`、17×16
边界以及 `user_configurable=false`，让报告可以独立复核。

`CarrierGrid.from_config()` 和通用 Type-0 边界函数仍保留，但只服务链路级、导入诊断和
数学单测；它们不能进入当前 TDD 系统/体验仿真。早先发现的窄带 CSS、单 RBG reduce
等通用 bug 仍保留回归测试，避免未来真的扩展其他带宽时复发，但这不等于当前产品已支持
动态系统栅格。

### 速率统计口径：buffer 在发送时扣减，不看这个 TB 对不对

**这是现场的统计方式（用户 2026-09-04 给的），三条合同：**

1. **发出一个包后 buffer 空了，这个包的 KPI 当场就能统计**——掐头去尾业务量与
   掐头去尾时间——**完全不管这个包正确与否**。不等 ACK。
2. 误码与重传对体验速率的影响，主要体现在**重传要占资源**：时频资源被吃掉，
   别人（和自己后面的数据）发不了。
3. **重传优先级高于新传。** 发完这个包 buffer 还没空时，时间会继续统计；
   NACK 回来时如果 buffer 还没空，就得先把误码的包重发，把后面的数据往后推，
   于是掐头去尾时间被进一步拉长、速率下降。

代码里：`DrbQueue.transmit(..., is_retx=False)` 在**发送时**扣 `queued_bytes`，
busy period 在"清空 buffer 的那一次发送"结束（`last_tx_tti`），`tx_events` 只记
首传。重传走 `is_retx=True`，对 DRB 队列是纯空操作：**不动队列、不产生 TxEvent、
不推进 busy period，也不碰下一个 busy period 的 `tx_attempts`**；资源占用由
`retx_count`、allocation 与 PRB 账本记录。

传丢的部分不从已发送字节里扣回去，只进 `residual_bler`。实测最小反例
（强制首传全错、`tdd_pattern="D"`）：首传全对 368.8 Mbps → 首传全错重传全对
184.4 Mbps（重传吃掉一半资源）→ 首传重传都错 **184.4 Mbps，逐值相同**，
差别只在 `residual_bler` 0.0 → 1.0。

**这条口径不只是 KPI 偏好，多进程 HARQ 必须靠它才自洽。** 按 ACK 扣减时，
被 NACK 的 TB 其字节仍留在队列里，同一个 UE 的另一个 HARQ 进程会把**同一批字节**
再组成一个新 TB 发一遍，然后原 TB 的重传发现队列已经不够冻结的 payload。
2026-09-04 实测轨迹：t3025 发 33822 B 被 NACK（队列不减），t3026 用另一个进程把
同一批字节又发了一遍并 ACK，到 t3028 队列只剩 17814 B，t3025 的重传直接硬失败。

因此 `scheduler_finalize` 与 `_build_su_plan` 里"队列必须 ≥ 冻结 payload"的两道
校验都已删除：重传的字节在首传时就走了，队列剩多少与它无关。

内网审核曾发现已删除的 legacy capacity `_Traffic` 仍按 ACK 扣减；#25 删除那条主循环后，
当前只剩 `DrbQueue` 一份实现。原反例已迁到唯一系统路径：1/8 进程有限话务都守
`sent <= arrived`，重传全对/全丢的发送字节逐值相同。不得恢复第二套队列实现。

**`pf_accounting="acked_goodput"` 的合同是「NACK 给 0」**，别拿发送字节当 credit。
buffer 改成发送时扣减之后 `sent` 在 NACK 时也是正数，直接用它会把这条合同悄悄改掉
——PF 会把没送达的字节也算成该用户已获得的服务，坏链路用户的 `r_avg` 涨得更快、
优先级掉得更快。默认口径 `auto`（= `scheduled_tbs`）用的是 `tb_bytes`，本来就与
ACK 无关，不受影响。
`tests/test_system.py` 第 17.2f 节有独立棘轮：ACK 首传的 credit 等于发送净荷，
NACK 首传即使已从 buffer 扣字节，`pf_credit_bytes` 仍严格为 0。

### 系统仿真入口的两道硬校验（2026-08-17 第三轮审查）

`sr_system_sim` 在 server 边界新增两道硬失败，都是"静默算错不如直接拒绝"：

* **SRS provenance**：主链路把 `h_est` 当基站侧 SRS 预编码 CSI（CSI 老化的
  物理语义），但此前不做任何来源校验，而比较门对 `csi='srs'` 已硬查
  `precoding_csi_sources`。现在来源非全 `ul_srs_estimate` 时：开 CSI 老化
  硬失败；不开老化则在结果 `notes` 首位带来源告警。旧数据集没有标签
  （`legacy_unspecified`）在开老化时同样会被拒，需要重新生成。
* **样本→UE 布局**：`group_samples_by_ue` 按接受序号轮转分组，而 SINR 拒绝
  采样下 `ue_id` 按 attempted_index 合成，两者会错位——继续跑会把不同 UE
  混进同一"用户"。现在 `ue_id` 与 `i % num_ues` 不一致时直接报错，
  提示关筛选重生成或先按 ue_id 归并。

配套变化：统计门 `_position_clusters` 在静态数据上要求 ue_id 聚类与
`ue_position` 聚类给出**同一个划分**（独立锚点对账，防生成端假设自证），
聚类失败原因改为结构化状态（`mobility_missing_id` / `partition_mismatch`
block，其余 warn），不再靠文案子串定严重度；移动模式的身份合成严格镜像
源端布局合同（`internal_sim` 速度 ≤0 时退到静态轮转、键缺失按源端默认
3.0 km/h 走单轨迹），只有速度非有限才不给身份
（`unavailable_mobility_speed`）交给移动数据 block。
探测类工具（`sr_probe_scenario` / `sr_compare_scenarios` / `sr_spec_sheet`）
的 preset+overrides 合并也走 `_apply_dependent_overrides`，改带宽不会再
把 preset 的 `num_rb`/SRS 几何带进探测。

### 样本数不是用户数

数据集里 `num_samples` 个样本分布在 `num_ues` 个 UE 位置上（轮转分配）。
同一个 UE 的多个样本**永远**是同一条物理信道上的连续时刻（2026-09-08 起）：
first-party 源给每条轨迹一套固定的散射体（`rng_small` 按 `ue_id` 派生，不再按
`global_index`），样本之间只推进绝对时刻，第 r 轮覆盖
`[r·num_slots_per_sample·dt, (r+1)·…)`，首尾相接不重叠。相邻样本的复相关系数
因此等于 Jakes 的 `J0(2π·f_d·Δt)`（实测 3 km/h @2.6 GHz、Δt=5 ms：0.986 vs 理论
0.987，六个滞后上最大偏差 0.019）。`mobility_mode` 现在只决定**几何位置**动不动，
不再决定小尺度衰落是否连续。

两个直接后果，必须知道：
- **相邻样本不再是独立实现。** 3 km/h、5 ms 间隔下相关度 0.987，一批 200 个样本的
  有效独立样本数远小于 200。要更多独立实现就加 `num_ues`（不同位置 = 不同散射体），
  或者把 `sample_interval_s` 拉长到相干时间以外。
- **`ue_speed_kmh=0` 且每 UE 多于一轮会硬失败。** 零多普勒 + 位置不动 = 逐位重复的
  矩阵，与 `sionna_rt` 的同类守卫同一个理由：不静默产出重复矩阵。

射线追踪引擎**不吃**这个轨迹时钟（`time_offset_s` 在 RT 侧显式丢弃）：RT 的径相位
来自真实径长，位置一动几何相位就已经算过一遍，再叠加多普勒相位等于算两遍。
`doppler_hz=|v|/lambda` 描述最大 Doppler，不是“相邻样本距离变化”这个统计量。

把每个样本当成一个独立用户，小区里就凭空多出好几倍的人。实测 40 样本 / 10 UE：
每用户谱效从应有的 0.32 掉到 **0.08**，5% 边缘从 0.194 掉到 0.040——
**表现出来像"边缘用户被调度器饿死了"**，我当时的第一反应就是去查 PF 有没有把人饿死，
查了半天 outage 全是 0%。真因只是分母大了 4 倍。

`system.group_samples_by_ue()` 负责分组，`build_link_tables(num_ues=...)` 用它。
`sr_system_sim` 从 `ds.config["num_ues"]` 自动读。

### 系统级只有一条评估路径

**没有"容量模式"这个分支了。** `evaluation_mode` 已删除，`system.simulate()` 恒走
`experience.simulate_experience()`。文档和对话里说的"容量仿真"指的是
**`traffic_model="full_buffer"` 这个话务配置**：缓冲区永不空 ⇒ 调度器始终有足量数据
填满全部 RBG（`resource_utilization == 1.0`），这正是容量口径。

> **⚠️ 撤回：满缓冲保证的是"RBG 用满"，不是"每忙 TTI 只服务一个用户"。**
> 早先这里写着"每 TTI 一个 SU（或一对 MU）"，那是**频选关 + MU 关**时的退化解。
> 合成互补频选信道上实测这张 2×2 表（棘轮在
> `tests/test_physics_invariants.py` 第 8 节）：
>
> | `scheduled_ues_per_busy_tti` | MU 关 | MU 开 |
> |---|---|---|
> | 频选 `off` | **1.0000** | 2.0000 |
> | 频选 `on` | 1.3800 | 2.7200 |
>
> 频选打开时调度器把"少几个但信道更好的 RBG"给第一个用户、余料给下一个；
> MU 打开时一个 TTI 本来就配对两个用户。**两者都是正确的物理行为。**
> 出厂默认是 `frequency_selective="auto"` + `mu_enabled=False`。
> **六个实测 preset 锚点没有一个是 1.0**：
> capacity 1.1402、capacity_mu 1.8565、ftp3 1.2114、mixed 1.2750、
> 多小区中心站 1.0627、多小区边缘站 1.0002。
> 后果：满缓冲口径**不等于**经典"单用户占满全带"的容量定义，跨版本、跨工具
> 引用容量数会有系统性偏差——`sys_single_cell_capacity` 的 preset 注释里量过。

**不许为 full_buffer 开任何特例分支。** 调度、AMC、HARQ、解调 SINR 聚合（按本次
实际授予的 RBG 算）一律照体验模式的定义走。
想让容量工况跑快一点的诱惑（比如满缓冲时跳过频选搜索）**明确否决**——那会重新造出
一条只在特定话务下成立的路径，也就是这次合并要消灭的东西。棘轮已经守住这条：
把 `frequency_aware` 在满缓冲下强制置 False，第 8 节的 2×2 表立刻变红。

### 「用户体验速率」有两个口径，别混

这是最容易说错的一处：**不能笼统说「full_buffer 下体验速率无定义」**。

| 口径 | 键 | 分母是什么 | full_buffer 下 |
|---|---|---|---|
| ITU-R M.2412 / TR 38.913 | `ue_served_p5_mbps` / `_median_` / `_mean_` | **观测窗长**（每 UE 已发送净荷 ÷ 窗长，跨 UE 取分布） | **照常有值——这就是满缓冲评估的主指标**，5% 分位即 cell-edge user throughput |
| TS 28.552 busy-period（**标准**） | `cell_experienced_mbps` / `drb_throughput_rel19_mbps` | **已排空的 busy period 时长**（首传 → 清空 buffer 前一段首传发送） | **报 `None`**：样本只在 "DRB DL buffer emptied" 事件上形成（TS 128 552 V19.5.0 p54），满缓冲下该事件不发生 |
| 在飞窗内段（**工程**，非标准） | `active_window_goodput_mbps` | 在飞 busy period 落在测量窗内那一段的 goodput | **有值**。与 ITU 口径数值接近，但**那不是交叉验证**，见下 |

它们**不是同一个数的两种精度**。preset `sys_single_cell_experience_ftp3` 实测：
`ue_served_mean_mbps=24.45` 而 `cell_experienced_mbps=424.29`，同一次仿真差 17.4 倍
——前者是 UE 全时段平均，后者是它的 burst 在传时的速率。满缓冲下 ITU 口径与工程
口径数值接近（UE 一直活跃），preset `sys_single_cell_capacity` 实测 52.915 vs
53.003，差 0.17%。

> **⚠️ 撤回：这个约 0.17% 不是交叉验证，别再当证据用。**
> 这两个数的**分子是同一份发送净荷记账**——`experience.py` 里 `tr.transmit()`
> 返回的那个 `payload`，一边累进 `served_measured`、一边塞进 `TxEvent`。
> 只有分母不同：一个除观测窗长，一个除首传到末次窗内发送的跨度。满缓冲下 UE 从头忙到
> 尾，两个分母本就重合，**所以吻合是必然的**。它能证明的只有"满缓冲确实让每个 UE
> 全程活跃"，这是关于话务模型的陈述，**不是关于记账正确性的**。
> 历史 pre-#18 反例：把净荷记账整体放大 10%，小区吞吐从 618.10 错成 683.93
> （错 10.6%），而两个同源指标的相对差仍约 0.16%，一个报警都不响。
>
> **那什么能抓住记账整体缩放？有限话务下的字节守恒。** `accounting_error_pct`
> 比的是「到达 = 已发 + 积压」，而**到达量由话务模型独立决定**，记账一缩放这个
> 等式立刻破。**满缓冲下它不可用**（offered 是无界的种子字节），所以
> `accounting_error_pct` 在满缓冲下报 `None` 而不是 `0.0`——报 0.0 是拿漂亮数
> 冒充测量，跟旧容量分支报 3.7e21 拿垃圾数冒充测量是同一种错。
> **满缓冲场景的净荷口径正确性，是靠同一套代码在有限话务下过了字节守恒间接保证
> 的，不是靠满缓冲自己的自查。**

**标准字段绝不能混进在飞样本。** 在飞段确实要报，但另起字段
（`active_window_goodput_mbps`，`throughput_kind = "engineering_active_window"`，
名字里不许出现 `rel19`）。理由是右删失：只数已排空的 busy period 会把"慢到没传完"
的 burst 系统性丢掉，这**不是 full_buffer 特有的**——8 UE / ftp3 20 Hz × 500 kB / 1 s
的普通过载下，标准字段同样返回 `None`（0 个 burst 传完）。
**偏差方向不固定**：实测既见过只数已完成偏乐观（11.59 → 含在飞 8.78），
也见过偏悲观（126.0 → 131.9），取决于慢 burst 属于差信道还是好信道用户，
所以**不许声称"只数已完成一定偏乐观"**。样本构成由
`drb_throughput_completed_bursts` / `_inflight_bursts` / `_inflight_share` 如实上报。

**在飞段不能冒充标准样本。** #21 之后 buffer 在首传发送时扣减，重传对 DRB 队列是
空操作。最小反例是在同一个 busy period 到达 101 B、只首传 100 B：虽然首传已发生，
buffer 仍剩 1 B，没有 `buffer-emptied` 事件；它只能形成
`engineering_active_window`，不能进入 28.552。把最后 1 B 也发走后才形成标准边界。

full_buffer 下只有这几个键留 `None`，因为它们**明确需要 burst 真的传完**：
`cell_experienced_completed_only_mbps`、`cell_head_inclusive_experienced_mbps`
（需要该 busy period 起始于窗内）、完成时延分位数、`pdb_miss_ratio`。

`ue_served_*` **无条件计算，不按话务模型分支**——任何话务下它都有意义，
这也是它不构成「为 full_buffer 开特例」的原因。

容量工况还要看 `cell_served_mbps`、`serving_cell_prb_utilization`、
`avg_mcs_first_tx`、`bler_first_tx`。

### 多小区数据集：挑哪个小区是物理选择，不是随便挑一个

3GPP TR 36.814 的标准撒点密度是每扇区 10 个 UE，21 扇区共 210 个；而仿真器一次
只调度一个小区，所以 `sr_system_sim(serving_cell=<编号>)` 必须挑一批出来。

**撒点没有 wrap-around，边缘小区的邻区不完整、干扰被系统性低估。**
挑被邻区包围最完整的那个（几何 SIR 中位最低）。实测同一份 210 UE 数据集，
只换 `serving_cell`、其余逐字相同（preset `sys_multicell_center_cell` ↔
`sys_multicell_edge_cell`）：

| | 中心站（小区 1） | 边缘站（小区 11） | 差 |
|---|---|---|---|
| 几何 SIR 中位 | 4.22 dB | 16.25 dB | 12.0 dB |
| IoT 中位（仿真） | 23.23 dB | 16.78 dB | 低估 6.45 dB |
| `cell_served_mbps` | 541.11 | 687.99 | **高估 27.1%** |
| `ue_served_p5_mbps` | 24.90 | 61.72 | **高估 148%** |
| `bler_first_tx` | 0.0793（0.79×） | 0.0328（**0.33×**） | |

**边缘站那一列同时说明「信道太好的场景不能当外环锚点」**：它的
`avg_mcs_first_tx` 是 26.08，已经贴着表 3 的最高档 27，`olla_db_mean` 为 **+1.64 dB**
（外环在往上推却推不动），于是 BLER 只能低于目标。中心站是 21.42 档 / −1.87 dB，
外环在正常回退。**MCS 撞天花板 ⇒ 外环失去调节余量 ⇒ BLER 偏低**，这不是 bug，
是场景选错。

`serving_cell_selection` 会把判断依据一起回报：选中小区的几何 SIR 中位与 IoT 中位
（`IoT = SIR/(SIR−SINR)`，与结果里的 `iot_db_median` 同一公式）。
算不出来时给 `selected_interference_note` 说明原因，**不留哑 `None`**。
它与 `rb_power_control_enabled` 同开硬失败（逐 RB 功控的几何量不随样本筛选走）。

随容量分支一起下线的配置，给了都硬失败、不静默降级：
`evaluation_mode`、`traffic_model="bimodal"`（连同 `p_small_rbg`/`p_full_rbg`/
`p_idle_tti`/`expected_prb_util`）、`KpiConfig.trim`/`min_burst_tti`、
`mu_accounting="se_ratio_legacy"`、`pf_accounting="legacy_best_se"`、
`max_mu_users`/`mu_rank_per_user` 的非 2 取值、`simulate(mu_se_ratio=...)`。
现网两头高中间低的话务画像迁到 `traffic_model="cdf"`，CDF 文件在
`presets/traffic_cdf/`，由 `scripts/make_field_bimodal_cdf.py` 生成。
