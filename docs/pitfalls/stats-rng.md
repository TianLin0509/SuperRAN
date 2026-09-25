# 随机数、重复实验与统计判决

> 本文件由 `CLAUDE.md`「踩过的坑」按主题拆出（2026-09-25），正文原样搬移，条目标题保留原文；索引在 `CLAUDE.md`。
> 每一条都是真实事故，**删之前先想清楚**；改动口径时同步更新本条和 `CLAUDE.md` 索引的一句话要点。

**什么时候读**：改 `rng.py` / `gates.py` / `results.py`，或要下任何 A/B、算法对比、KPI 提升结论。

### 系统级 KPI 不带置信区间就是在报噪声

同一批信道、同一套配置**只改种子**跑 `sr_system_sim`，实测
`cell_experienced_mbps` 的变异系数 **9.4%**（64 次重复）、
`ue_experienced_p5_mbps` **18.6%**。**已经发生过把噪声当效应的事故**：
上一轮报「Type I 权老化后 +14%」，而噪声 1σ 就有 11%。

链路级早就有三道门（`gates.py`），系统级一直绕过去了——在 `system.py` 里搜
`confidence|t_test|wilcoxon|paired` 命中数曾经是 0。现在 `simulate_replications`
把每个 KPI 报成 `mean/std/ci95/n_rep`，`sr_system_sim` 默认 `num_replications=8`。
**统计一律复用 `gates.paired_compare` / `gate_conclusion`，不另写一套。**

### 多算法 KPI：算法不是 Tab，单 TTI 不是结论

2~5 个算法比较时，算法必须是贯穿总览、KPI 矩阵、用户 CDF、TTI 趋势与详情的
固定颜色系列；Tab 按读者问题划分，不能把每个算法藏进独立 Tab 逼用户凭记忆比较。
基线不可隐藏。每个单臂结果保存严格 JSON sidecar，比较入口硬校验 dataset、模式、
时长、载波、TDD、话务、KPI 口径和逐位 `(master_seed, replication)`；主 KPI 复用
Gate 3，多候选对同一基线时 Holm 只收紧、不放宽判决。
只有 dataset 的生成前 prereg 同时匹配主 KPI 与基线标签时才允许标 publishable winner；
否则即使统计显著也保持 exploratory_unregistered。

TTI trace 默认 `sampled`：一半预算保存跨算法共同的均匀锚点，一半保存
MU/NACK/重传/多 UE/outage 事件；`full` 必须显式开启。缺采样与真实 idle 是两个状态。
单 TTI 的 RBG/MCS/rank/SINR/BLER draw/ACK/OLLA/PF 只能解释机制分叉，绝不能从一个
事件外推“算法提升”。

四条不能忘的量级：

* **n ≤ 5 时判决检验永远不可能显著。** 双侧 Wilcoxon 最小可达 p 是 `2/2^n`，
  n=5 给 0.0625 > 0.05——**无论数据多干净都判不出显著**，而它照样会算出漂亮的
  百分比。n=6 是硬下界，默认 8 留余量。
* **"按 1/√n 收窄"精确成立的是标准误，不是置信区间半宽。** 半宽还乘着
  `t_{0.975,n-1}`，小 n 时 t 大得多（t₃=3.18 vs t₁₅=2.13），实际收得**更快**：
  n 4→16 是 0.357 而不是 0.5。混为一谈差 30%。
* **变异系数自身也有置信区间。** n=8 时是 `0.66x ~ 2.04x`——
  `measurements/seed_variance.json` 里那个 11.4% 的真值可能在 7.5%~23% 之间，
  **只精确到大约 2 倍**，别拿它做精细比较。
* **效应小于置信区间就不能下结论**，这和"区间跨零"是同一件事
  （对称 t 区间 `mean ± h`，`|mean| < h` ⟺ 含 0）。
  `rng.compare_replications` 的 `verdict` 只有 significant / inconclusive /
  not_pairable 三种，inconclusive 时明确写"不要报这个百分比"。

### `seed + 1` 的问题是撞车，不是相关

原始猜想「seed 与 seed+1 会给出相关的流」在现代 numpy 上**是错的**，实测 200 对
`default_rng(s)` / `default_rng(s+1)` 的最大 |r| 只有 0.07（噪声量级）——
`SeedSequence` 用带雪崩效应的整数散列混合 entropy，相邻种子的初态相距极远。
**这条主张证明不了就不能写进理由里。**

真正的问题是 NumPy 并行随机数文档点名的那个：`root_seed + worker_id` 被标成
"UNSAFE! Do not do this!"，因为**换一次 root 之后两批会撞车**。实测 base=100 与
base=105 各取 8 条流，有 **3 对逐位相同**——不是"相关"，是同一条流被当成两次
独立重复，而这在结果里完全看不出来。

所以 `rng.py` 用两级：`master_seed`（≈ ns-3 的 `RngSeed`，换它 = 换一个宇宙）
+ `replication`（≈ `RngRun`，同一宇宙里的第 k 次重复）。ns-3 手册的原话是
"the more statistically rigorous way to configure multiple independent
replications is to use a fixed seed and to advance the run number"。
**重复实验换 replication，别写 `seed+1`；要给只收 int 的外部接口就走
`RngBook.integer_seed()`。**

### 随机流要按用途分开，共用一个 rng 会串味

分流前 `simulate()` 里**一个** `rng` 同时喂话务和 HARQ：改一下
`arrival_rate_hz`，抽到的到达次数变了，后面 HARQ 的伯努利序列**整个错位**，
于是"话务模型的影响"里混着"HARQ 换了一批随机数"。这类污染在结果里看不出来。

现在五条流各管一摊：`channel` / `traffic` / `scheduler` / `harq` /
`neighbor_load`。两个实现细节别改：

* 流键是 **`zlib.crc32(名字)`**，不是名字在表里的下标（加一条流会把后面所有流
  挪位），更不是 `hash()`（对 str **每进程随机加盐**，换进程就不可复现，
  而本进程内自洽——最难查的一类）。
* 用**显式 `spawn_key`** 而不是 `SeedSequence.spawn()`：后者有状态
  （`n_children_spawned` 会推进），先要 `traffic` 还是先要 `harq` 拿到的流就不一样。
  两者底层机制相同，`test_rng` 第 1 节逐位验证了等价性。

### A/B 不用公共随机数等于白白把区间放宽 4 倍

实测（PF 窗 100 vs 1000，n_rep=8，真实效应约 −10 Mbps）：

| | 效应 | 95% CI | 半宽 | Wilcoxon p | 判决 |
|---|---|---|---|---|---|
| 公共随机数 | −10.64 | [−14.14, −7.15] | 3.49 | 0.0078 | **significant** |
| 独立随机数 | −14.97 | [−28.66, −1.27] | 13.69 | 0.078 | inconclusive |

**同一个真实效应，CRN 下判得出来，独立种子下判不出来。** 原理是
`Var(a−b) = Var(a) + Var(b) − 2Cov(a,b)`，CRN 把那个协方差做正。
做法就一句话：**两臂用同一个 `rng.replications(master, n)` 的返回值。**

注意上表独立那一栏的区间其实不跨零，是判决以 Wilcoxon 为准才拦住的——
和「门 3 的判决必须显式说清用哪个检验」是同一条。

`rng.check_pairable` 照抄 `results.check_pairable` 的 ID 契约思想：系统级的
"样本 ID" 就是 `(master_seed, replication)`，顺序错一位统计层面**不可观测**。
没给 books 时 `crn` 返回 **None 而不是 True**——查不到不能当它对。

### 建表与随机种子无关，所以只建一次

`build_link_tables` 除了邻区负载抖动之外**完全确定性**（SVD、码本搜索、
MCS 查表都不含随机），所以 n 次重复只重跑 TTI 主循环。代价是
`(n−1)·T_loop / (T_build + T_loop)`——实测 ds_6e9715bc 建表 5.14 s、
单次主循环 0.99 s，n=8 是 13.0 s vs 单次 6.1 s（**+113%**）；
建表越贵比例越低，按 10.5 s / 1.1 s 算是 +66%。

代价是**邻区负载抖动被冻结在表里，不进置信区间**。这个取舍量过：
64 次 replication 与 32 次 master seed 扫描（每次重建表）的变异系数，
五个 KPI 里四个的区间重叠——**冻结并没有可分辨地把离散度报小**，
系统级的主导方差就是话务与 HARQ。数据在 `measurements/rng_replication.json`。
**信道实现本身的不确定度是另一个更大的分量，那个只能重新 `sr_generate`。**

### 配对的有效性靠样本 ID，统计查不出错位

`results.check_pairable` 逐个按序比 sample ID，不只比长度。**这不是多余的谨慎**：
把两个臂的 ID 顺序打乱一个位置，配对检验算出来的 p 值可以**一模一样**
（实测 1.63e-11 → 1.63e-11），因为统计只看数值数组，根本不知道第 i 个数
对应哪个信道实例。错位是统计层面**不可观测**的，只能靠 ID 契约拦。

所以 `register` 默认自动生成 ID，两臂都用默认就一定对齐；只有显式传 `ids=`
（跳过部分样本）时才可能出错，那时校验就是最后一道防线。

同理 `register` 拒绝含 nan/inf 的 values：配对时非有限值会被整行丢掉，
两臂样本数悄悄变少，而 p 值照样算得出来。

### 外部结果的 CSI 口径只能靠声明

内置 `compare_arms` 能查两臂用的是 `h_true` 还是 `h_est`，因为预编码是它自己跑的。
**外部结果是用户在自己进程里算的，MCP 看不到里面用了哪个。** 所以门 2 只能查
`method_metadata` 里的声明，查不到就给 warn 并说清"这条得你自己保证"——
不能假装查过了。

这个不对称是设计选择而非缺陷：让 MCP 去 exec 用户代码换取可观测性，
是把它从"数据供应站"变成任意代码执行面，代价远大于收益。

### 预注册只在生成前绑定才有意义

`sr_generate(prereg_id=...)` 把主指标写进 `summary.json`。**事后补绑没有价值**
——预注册的全部意义就是"这是看数据之前写下的"。所以没有"给已有数据集补绑"
的接口，也不要加。

未绑定时 `classify` 返回 `unregistered` 而**不是** `primary`：没登记过就不能
声称主指标是事先定的。这一条别放松成"默认 primary"。

### 门 3 的判决必须显式说清用哪个检验

`PairedResult` 曾有个叫 `significant` 的属性，只看配对 t 检验；而文档写着
"两检验冲突时以 Wilcoxon 为准"。门 3 用的是前者，于是 t 显著、Wilcoxon 不显著的
样本被直接放行——**承诺的判据和代码实际用的判据是两回事**，这比判据宽松更危险。

现在 `significant` 已删除，改为 `t_significant` / `wilcoxon_significant` /
`tests_agree` / `decision_test` / `decision_p_value` / `decision_significant`。
判决以 Wilcoxon 为准（谱效差值分布常偏态，t 的正态假设不成立、小样本偏乐观），
Wilcoxon 算不出来才退回 t。`statement` 必须写出用的是哪个检验。

回归样本记在 `tests/test_gates.py` 第 6.5 节，别删：
`d = [-0.0811, 1.5561, 0.5308, 1.9896, 3.2605, -0.1125, 1.6908, -0.2045]`
（n=8，t p=0.044 显著、Wilcoxon p=0.109 不显著）。

### 零方差差值要分两种情况

`paired_compare` 里 `se <= _EPS` 时不能一律 `p = 0`：差值恒为 0（两臂完全相同）
应当 p=1，差值恒为非零常数才是 p=0。早先一律写 0，于是"自己跟自己比"得到
p=0（最显著），只是碰巧被"置信区间不跨零"拦住——靠运气拦住的不算拦住。
另外 `float("inf") * np.sign(0)` 是 nan，还会抛 RuntimeWarning。
