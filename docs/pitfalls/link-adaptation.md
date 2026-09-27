# 链路自适应：BLER / CQI / OLLA / HARQ / rank

> 本文件由 `CLAUDE.md`「踩过的坑」按主题拆出（2026-09-25），正文原样搬移，条目标题保留原文；索引在 `CLAUDE.md`。
> 每一条都是真实事故，**删之前先想清楚**；改动口径时同步更新本条和 `CLAUDE.md` 索引的一句话要点。

**什么时候读**：改 `linkadapt.py` / `bler_curves.py` / `amc_policy.py` / `csi_aging.py`，或 `system.py`/`experience.py` 的 AMC、HARQ 部分。

### QAM 互信息的 sigma 定义差一倍就是 3 dB

`linkadapt._pam_mi` 里 `sigma = 1/sqrt(γ)`，**不是** `1/sqrt(2γ)`。
约定是复符号 `E|x|²=1`、复噪声 `E|n|²=1/γ`；折到实维、星座归一化到单位能量后，
噪声方差正好是 `1/γ`。写错会整体多给 3 dB，而且**看起来完全正常**——
曲线形状对、饱和值对，只是位置平移。

抓它的办法是低信噪比处对香农：约束容量在 γ→0 时必须与 `log2(1+γ)` 重合。
这条自检在 `test_linkadapt` 第 1 节里。

### BLER 有分析模型和预置曲线两条后端，别混成一种证据

表 1/2 使用 `BlerModel`（有限码长形状 + 可配实现损失，没有 3GPP 参考曲线
兜底）；MCS/CQI 表逐字录自 38.214、TBS 按 §5.1.3.2 复刻、QAM 约束容量精确
求积。**这三样不能和分析 BLER 混为一谈。**

表 3 使用内置的 `preset_20b_256qam`：28 档 MCS、56 条 NewTx/ReTx 预置曲线、
1824 个点。原始数据在 `bler_data_20b.py`，查询/哈希/单调性/插值在
`bler_curves.py`。它保留离散瀑布形状，但**仍不是 3GPP 标准曲线**。
预置 profile 将源标签 `Es/No` 解释为经典 MMSE 的**单码字有效 SINR**；误块事件是
**一个用户在一个已调度 TTI 中的独立单码字 TB**，系统不单独查询或统计 CBLER。预置表
查询只使用 `MCS + codeword_effective_sinr_db`；跨 RBG、跨 rank stream 都做 dB
算术平均（RBG 内多个 RB 也先转 dB 再平均，与参考实现对齐）。TBS、RE、RBG 数、rank、场景、码字数和
译码器细节本阶段都不是曲线查询轴，这是已确认的通用曲线合同，不是待补数据缺口。
物理编码内部即使分成多个 CB，也不能在表 3 路径上再次套 CB→TB 合成。曲线范围外只能
保守钳位，不能外推。

表 3 的 HARQ 只允许**一次重传**，默认 IR、可选 CC；两者都只查询 NewTx 曲线，原始
ReTx 行保留作来源审计。CC 保持原 MCS 并把查询 SINR 增加 `10log10(2)=3.0103 dB`；
IR 把原 MCS 谱效除以 2，映射到不超过半谱效的最高 MCS 并在不变 SINR 上查表。
等效 MCS 只用于 BLER lookup；空口 MCS、RBG 数、rank 与 TBS 必须和初传完全一致。
重传失败后结束本次 HARQ；payload 已在首传发送时离开队列，末次失败只进
`residual_bler`，不回队列，也不得再挂第二次重传。

**"最多一次重传"是显式决定，不是待补的半成品**（用户 2026-09-02 确认：
"HARQ 我们先仅考虑一次重传"）。现场实现规格里是 `max_retx=3`（共 4 次传输）；
扩到那一档会改动全部 BLER/时延口径，因此**当前显式不做**。所有 residual BLER
都要按"一次重传后仍失败"来读。

**HARQ 进程数**（2026-09-04）：唯一系统路径 `experience_v2` 支持 N 进程，
`SystemConfig.harq_max_processes` 默认 **8**，38.213 §5.3 的下行上限是 16；
设成 1 精确退回历史单进程行为。full_buffer 与有限话务只是同一路径的不同工作点。

**收益在体验速率，不在小区吞吐。** 有限话务下小区吞吐通常 offered-limited，
小区吞吐是 offered-limited 的，被 HARQ 挡住的 UE 会被别人顶上，所以
UE 数 ≥4 时小区吞吐几乎不动（实测 ±0.3% 以内）。真正被单进程压住的是
**单个文件多久传完**：

| 当前受控夹具（4 UE / ftp3） | 1 进程 | 8 进程 |
|---|---|---|
| 体验中位 | 112.2 Mbps | **349.8 Mbps（+212%）** |
| 完成时延 p50 | 39.2 ms | **12.0 ms** |
| 小区吞吐 | 67.2 Mbps | 68.4 Mbps（几乎不动） |

10-TTI 的 `DDDSU` 饱和反例里 **4 个进程已经填满 8 个 D/S 机会**，8 与 16 不再增量；
默认 8 是给更长反馈链与时隙类型约束留余量，不是现场最优值的标定结论。
表 3 使用版本化256QAM映射：历史内部表行0..14为
`[0,2,4,6,8,10,12,14,16,18,20,22,24,26,28]`，对应上报4-bit CQI1..15；
上报CQI0是out-of-range。自动量化用各映射MCS的NewTx目标BLER门限。当前曲线仅
有MCS0..27，最高行请求MCS28但必须显式钳到27，不得伪造MCS28曲线。
所有高层链路/吞吐/SNR扫频接口默认都用表3的256QAM profile；
表1的64QAM和表2的标准256QAM只在调用方显式指定时使用。
`build_link_tables` 的三张表各走同一口径：表 1/2 的 CQI 门限、MCS 选择、首传与一次
IR/CC 重传统一使用有限码长解析 BLER；表 3 使用 preset_20b_256qam 曲线，不能交叉借表。
链路级工具可显式选择表 1/2，并标注“解析、未按特定译码器或现场曲线标定”；
系统级唯一主循环硬拒绝非表 3，因为它的 TBS/单码字 TBLER profile 只在表 3 上冻结。
`target_bler` 可配，但必须落在 28 档曲线的**共同实测区间 [0.001, 0.998]** 内，
越界提前硬失败而不是在深层抛一个看不出哪档的 ValueError。注意这条链里目标 BLER
**开环几乎抵消**（它同时出现在 CQI→Γ 与 Γ→MCS 两侧，两次平移方向相同），真正
吃到它的是 OLLA 闭环：实测 10%→30% 时稳态偏置 1.65→1.85 档、首传 BLER
0.067→0.178。

TDD AMC 已由 `tdd_mcs_adaptation` / `Dataset.tdd_mcs` / `sr_tdd_mcs` 实现：
`CQI表行/上报codepoint → 显式离散表映射初始 MCS → 该 MCS 的 NewTx 目标 BLER SINR 门限
→ + BF Gain → 按 SINR 重映射 MCS → + OLLA MCS offset → floor → 最终 MCS`。
CQI 是 PMI 权测得的 pre-BF 值。BF Gain 逐 RB、逐流计算为同一信道、CSI、rank、
功率、噪声、干扰与经典 MMSE 接收机下 `SINR_SVD - SINR_PMI`；默认物理发送权为
SVD 方向叠加 NEBF 每天线约束，因此也记为 `SINR_NEBF`。RB 先在每个 RBG 内做
dB 算术平均，再对RBG×流做dB算术平均。历史row0映射MCS0并对应上报CQI1；
上报CQI0不调度。OLLA的
单位是连续 MCS 档位，不是 dB；正值更激进，
最终结果严格向下取整并钳位到0..27。默认10%首传BLER下ACK +0.01、NACK -0.09，
反馈只作用于下一调度时刻。所有中间量与口径必须保留在结果中，不能只返回最终 MCS。

`anchor_check` 的单调性**只能在同一调制阶数内部要求**：标准表在调制切换点上
故意让 SE 重叠（MCS9 QPSK SE=1.3262 → MCS10 16QAM SE=1.3281，但码率只有 0.332），
门限小幅回落是正确物理。整体判单调会把这两点误报成失败。

### 解码 SINR 要取实际授予的那几个 RBG

误块抽签用的是**最终发送 MCS + 真实接收 SINR**，而这个 SINR 由同一个 gNB 发射权
作用到 `h_true`、经经典 MMSE 逐 RB 逐流算出——**是算出来的，不是从全带值折的**。
聚合只能在**本次 grant 实际占用的 RBG** 上做（`experience._granted_true_sinr_db`
与 MU 的 `_granted_pair_true_sinr_db`）。

宽带路径早先直接用全带均值，于是一个只占 1~2 个 RBG 的小包按 17 个 RBG 的平均
信道判误码，**两个方向都会错**：授到好子带时高估误块、授到坏子带时低估。量级
不小——最终 MCS15 在 15.1 dB 上的 NewTx BLER 是 0.0006，在 13.2 dB 上是 0.997，
不到 2 dB 跨越整条瀑布。手工构造、逐 RBG 宽度对不上载波栅格的链路表退回全带值。

### CSI 老化：零时延恒等式是地基，rank 必须由基站自己选

`csi_aging.rank_adaptation_aged` 用 `H_stale` 算预编码、用 `H_true` 评估。
两条不变量必须一直成立，破了任何一条整个模型就不可解释：

1. **零时延时逐位退化成原实现**。MMSE 后处理 SINR 在 `H_stale == H_true` 时
   `H W = UΣ_r` 是对角的，逆的对角元正好给出 `σ_k²·P/rank/σ_n²`——
   和 `mumimo.su_rank_adaptation` 的特征值公式**一模一样**（实测偏差 0.000000 dB）。
   不成立就说明老化是叠加上去的第二套物理，那样任何"老化损失"都只是两套物理的差。
2. **rank 由基站按陈旧 CSI 选**。拿真实 SINR 去挑 rank 等于让基站预知信道，
   它会自动避开老化最狠的那个 rank，**损失被凭空抹掉一大半**。
   同理 PF 调度的 `inst_se` 只能用基站估计的谱效，不能用真实谱效。

**但 `best_rank` 已经不是发送 rank 了**（2026-09-02，用户确认）。它是**逐快照的
瞬时谱效最优值**，默认 5 ms 就可能换一次；rank 一变，每流功率 `P/r`、TBS 和 OLLA
的收敛点全跟着变，链路自适应根本收敛不了。现在 rank 是 `amc_policy.RankConfig`
给的显式策略：默认 `fixed` + `fixed_rank=2`（现网基线），`link_table` 保留逐快照
跟随的历史行为**只作反向对照**，`adaptive` 按现场实现规格实现（用户 2026-09-02 给
的文档）。`best_rank` 现在只是诊断量与 `link_table` 模式的输入。

`adaptive` 的判决是**三层不同时间尺度**的状态机，别压成一层：

- **每 TTI**：累积一个谱效滤波样本（`se_filter_beta=0.1` 的一阶 IIR）+ 跑一次快速回退监测。
- **每 `period_tti`（1000）且样本数 ≥ `min_filter_samples`（3）**：判一次该不该换 rank。
- **切换判据（用户 2026-09-03 裁决）：按滤波谱效最大化选 rank，但任何方向的切换
  都要求最优 rank 超过当前 rank 10%。** 也就是 `switch_rule="unified_ratio"` +
  `gain_factor_raise = gain_factor_reduce = 1.1`，两个方向共用一条式子
  `se[best] > 1.1 · se[cur]`，常数含义相同、不会读反。实测 4% / 9% 不动、11% 才切，
  升降完全对称。
- **但当前 rank 被最小 MCS 闸门判死（谱效 0）时不讲迟滞**，直接降到候选里最稳的一档。
  否则 `se[cur] = se[best] = 0`，"超过 10%" 恒为假，UE 会卡在一个已知发不出去的
  rank 上。事件原因记作 `current_rank_gated_out`。
- 另一种写法 `spec_asymmetric`（实现规格文档那种：`se[cur] > G↓·se[best]` 时保持）
  **保留作对照**，因为现场到底是哪一种尚未确认。**同一个 `G↓=1.1` 在两种写法下
  行为相反**：`unified_ratio` 是降要 10% 余量，`spec_asymmetric` 是降立即生效；
  `spec_asymmetric + 0.9` 的等效降档余量是 11.1%，和默认那条同一个意图。
  `as_dict()` 直接报 `raise_margin_pct` / `reduce_margin_pct`，不要自己换算。

谱效估计前有两道修正，都会改判决：预估 MCS < `min_mcs_threshold`（9）的 rank 谱效
**置 0**（那一层根本发不出去，不能让它赢 argmax）；再乘 `resource_cost_ratio`
（`[1.0,0.97,0.95,0.93]`，高 rank 的 DMRS 开销）。

升 rank 后进 **快速回退监测窗**（`min(400, 周期−10)` TTI）：窗内新增 NACK > 90 **当场退**；
窗末初传 BLER ≥ 0.3 或新旧 rank 实测谱效比 < 1.0 也退；窗内调度 < 15 次样本不足，
不判成败直接退出。**回退要把 OLLA 一起退回**（`RankController.step()` 的返回值，两条主
循环都必须写回）——新 rank 上的 OLLA 是在错误工作点收敛的，只退 rank 会让旧 rank 带着
别人的偏置继续跑，KPI 上看不出来。每回退一次判决周期 `×2`，最多 `2^4`（1000→16000 TTI）。

**唯一一处本项目自己的口径选择是 `se_sample_scope`**：现场每 TTI 采一个谱效样本，
而这里的 AMC 坐标在一个快照内是分段常数，逐 TTI 采样会让 β=0.1 的平滑在快照之间完全
失效。默认 `snapshot`（一次新观测算一个样本），设 `tti` 复现现场节拍。**两者不等价。**PF 度量也要取**实际会用的那个 rank** 的估计谱效，不能继续用
`best_se_gnb`（那是"瞬时最优 rank 的谱效"，与实际发送的 rank 不是同一档）。

表里因此有两套量：`sinr/mcs/se`（真实，用于 BLER 与吞吐对账）与
`se_gnb/best_se_gnb`（基站以为的，用于 rank 与调度）。零时延时两套逐位相同。

### SINR_AMC_PRED 是 CQI 门限 + BF Gain，不是物理发送/接收 SINR

现场口径（用户 2026-08-03 确认）：`Γ(MCS(CQI)) + BFGain`。
**CQI 是长期滤波的宽带量**（终端在真实信道上用 Type I 宽带 PMI 权测），
**BF Gain 是瞬时量**（基站从自己的 SRS 信道算，`SINR_SVD − SINR_PMI`）。

早先写成"接收 SINR 的长期均值"是个**事后诸葛亮**的量——它已经包含了 SVD 的
实际增益，等于假设基站预先知道波束打得准不准。开 CSI 老化后这个错变得致命：
老化的全部代价就是"基站以为打准了其实没有"，而那个口径直接把它抹平。

两个实现细节：宽带 PMI 按 CSI report 周期更新并在周期内保持，不是每个 TTI 重搜；
每次更新只能使用该时刻可见的CSI，不能跨完整仿真时间先平均后偷看未来。历史
API的表行0映射MCS0、对应上报CQI1；真实上报**CQI=0是out-of-range**。

**CQI 的长期滤波是一阶 IIR，不是累计平均**（2026-09-02 换的口径）：
`s <- s + λ(x - s)`，第一次上报直接初始化状态。旧的 expanding mean 记忆无限长，
跑得越久新测量权重越小，移动性或负载变化时 CQI 根本不跟踪。λ 由
`CsiConfig.cqi_filter_lambda` 给，**0.25 已由负责人确认为当前工程默认，但尚未经现场
测量/设备数据标定**，
必须随结果报出来；λ=1 关闭滤波可作反向对照。`cqi_filter_domain` 默认
`cqi_index`（现场口径：在量化后的 CQI 档上滤波），`sinr_db` 只用于量化前后的
口径消融，两个域的结果不能混着比。

**CQI 上报从 2026-09-04 起是运行时事件驱动的**（`SystemConfig.cqi_report`，
默认 `CqiReportConfig(cqi_period_tti=None, csi_delay_tti=3,
ue_implementation_loss_db=1.5)`）。唯一系统主循环改成：每个上报周期让 UE 上报一次，
测的是 `tti − 上报周期 − csi_delay_tti` 时刻的信道，IIR 在线更新，基站读的时候
再按**当前快照**把瞬时 BF Gain 加回去。
`CqiReportConfig(enabled=False)` 退回建表阶段一次性算好的 `sinr_tx_db`，
**逐位一致**：与 #19 合并后的旧树在 4 个 full-buffer/FTP3 × 1/8 HARQ 进程场景
逐字对比，指纹 `fef442140fe30ef852bc4eee360d42c9d784d34b19b62974c3324ffcc466f6a7`。

**上报周期跟 CSI 报告周期，不跟上行 SRS 周期**（用户 2026-09-04 定）。
SRS 是上行探测、服务于互易性预编码；CQI 来自 CSI-RS + CSI 报告，周期由
`CsiConfig.csi_report_period_ms` 配（默认 20 ms，30 kHz 下 = **40 TTI**）。
`cqi_period_tti=None`（默认）表示"从链路表带出来的 `csi_report_period_ms` 换算"；
显式给整数只用于消融（`cqi_period_tti=1` 是理想 CQI 的上界）。
结果里 `cqi_period_source` 会写明是 `csi_report_period_ms` 还是 `explicit_override`。

**它和 `csi_aging` 是两个维度，不要合并**：`csi_aging` 管预编码权用的 `h_prec`
有多陈旧；`cqi_report` 管 MCS 决策输入 `sinr_tx_db` 多久更新一次；误块抽签用的
`h_eval` 真实 SINR 两者都不碰。

三件必须知道的事：

1. **离线那份 AMC 坐标系统性乐观**。同一夹具 OLLA 关掉时，离线预计算的首传
   BLER 是 **0.515**（目标 0.1），默认口径（40 TTI 周期 + 1.5 dB 实现损失）
   是 **0.016**，理想 CQI（周期 1 / 时延 0 / 无损失）是 0.086。
   小区吞吐 380.5 → 429.3 Mbps：离线那份太激进，误块把重传资源吃掉了；
   默认又明显过保守，1.5 dB 不能解释成已标定补偿。
2. **IIR 的 λ 是"每次上报"作用一次，不是"每毫秒"**。所以拉长 CQI 报告周期会同时
   拉长滤波器在时间上的记忆——周期 40 TTI 时实测 AMC 反而**更保守**
   （无实现损失时 BLER 0.065，而周期 4 时为 0.185），与"CQI 陈旧 → 更激进"
   的直觉相反。要让 λ 表示固定的时间
   常数，得按周期比例换算，当前**没有**这么做。
3. **1.5 dB 的 UE 实现损失只保证方向，不保证落点**。它一定让 AMC 更保守；
   是否"正好把陈旧带来的激进补回目标"依赖场景落在 MCS 量化台阶的哪一侧；
   当前夹具已经过冲到 0.016。**未经现场设备数据标定。**

`Dataset.tdd_mcs` / `sr_tdd_mcs` 也必须遵守同一因果边界：进入 MCS 的
`bf_gain_user_db` 只能在 gNB 可见的 `h_prec` 上计算。同一权打到
`h_true` 得到的 `bf_gain_true_user_db` 与 prediction error 只作事后审计，
不得回填当次 MCS。固定 `h_est` 只改 `h_true`，发送决策 BF Gain 必须逐位不变。

`Gamma(MCS(CQI))+BF Gain` 只是 `SINR_AMC_PRED`，不是物理 `SINR_TX`，也不是
接收端真值。实际发射分支应按方向与功率约束命名为 `SINR_NEBF/PEBF/EBF`
（默认 NEBF；方向另记为 SVD），并把同一个 gNB 设计出的物理 Q 作用到 `h_true`
得到 `SINR_*_RX`。最终 TB BLER 只能用 `final MCS + SINR_*_RX` 查表；仅有
CQI/BF/OLLA 标量时 BLER 必须是 unknown，不能拿 AMC 预测坐标查出一个伪 BLER。

### OLLA 是 MCS-domain 状态，步长比由目标 BLER 反解

`(1−p)·δ_up = p·δ_down` ⟹ `p = δ_up/(δ_up+δ_down)` ⟹ `δ_down = δ_up·(1−p)/p`。
目标 10%、`δ_up=0.01` 时**精确解是 0.09**；现网常说的 −0.1 给的是 9.09%。
`system.olla_step_down_for()` 负责反解。默认配置只存目标 BLER 与 up 步长，down 保持
`None`，进入仿真后才按链路表的目标值解析；只有目标恰为 10% 时结果才是 −0.09。
用户显式给 down 时视为有意 override，必须保留并在结果中标来源。

`olla_speedup` 等比放大两个步长，理论上不改目标 BLER，但会改收敛速度与
稳态抖动。最终发送档是 `floor(mcs_without_olla + olla_offset)`，所以新口径下
必须重新跑收敛标定；2026-08-23 之前 dB-domain OLLA 的 BLER/速率数字仅供历史
追溯，不能写成当前实测结论。

当前**没有**实现“每流固定 15 dB BF 惩罚”、CQI floor/reset 状态机或已标定的现场
OLLA 步骤；现有 BF Gain 是矩阵计算值，CQI/OLLA 常数是版本化工程近似。不得据此声称
现场等价，也不得在本次迁移中擅自补成另一套未确认算法。

**`olla_enabled=False` 只去掉"叠加偏置"这一步，决策坐标不变。** 早先它会掉进
另一条分支、改用**真实接收 SINR** 反折 MCS——那是上帝视角：首传 BLER 被构造在
目标值上，CSI 老化与 BF 失配的代价整个消失，于是"开/关 OLLA"的消融同时换掉了
链路自适应的信息面。只有链路表**根本没有** `sinr_tx_db`（手工构造的表）时才
退回表自带的 MCS，那不是"关 OLLA"，是"没有 CQI/BF 可用"。

**ACK/NACK 要等上行时隙**（2026-09-02 确认）。TB 在 D/S 发出，反馈搭其后第一个
`U` 时隙回来，OLLA 更新与该 TB 的重传资格都从**该 U 之后第一个 D/S** 起生效。
偏移由 TDD 图案算出（`amc_policy.feedback_effective_offsets`），`DDDSU` 在
30 kHz 下逐相位是 5/4/3/2 个 TTI；两个周期即 8 下行配 2 上行，与现场 8:2 一致。
**ACK 与 NACK 都建立 in-flight 状态**，占住发出它的那个 HARQ 进程；抽样结果只能在
反馈到达时交给 OLLA 与 RankController。唯一系统路径下一个 UE 可以同时有
`harq_max_processes` 个 TB 在途（默认 8），进程池空了才发不出新传；进程全满且
都在等反馈的 UE 计入
`harq_feedback_wait_skips`。图案里没有 `U` 时（`"D"`/`"DS"` 这类合成图案）退化成
零时延并在 notes 里说明。**k1/k2 与 PUCCH 资源仍不建模**：当前取的是最小 K1，
比 38.213 §5.3 Table 9.2-7 的查表值更快，OLLA 收敛也因此偏快。

唯一一次重传发出后，其终次 ACK/NACK 也保留为 `await_final_feedback`，直到同样的反馈
生效时刻才释放该 HARQ 进程。终次反馈只做释放：**不再进入首传 OLLA/Rank 学习，也不
触发第三次发送**。DDDSU 的最小反例是 t0 首传 NACK、t5 重传，下一份新 TB 最早 t10，
不能在 t6 提前发送（这条反例要在 `harq_max_processes=1` 下读；多进程时 t6 可以
发的是**另一个进程**的新 TB，那份被 NACK 的 TB 仍然只能等到 t10）。

顺带：`avg_mcs` 报的是 **OLLA 之后**的 MCS（`system.py` 先用
`sinr_tx_db` 反折 `mcs_without_olla`，再调 `apply_olla_mcs`），即实际调度下去的档位。
**但它的分母含重传**，而重传重放的是冻结的旧 MCS，所以它不是"链路自适应现在选到
哪一档"。要那个视角用 `avg_mcs_first_tx`（只统计首传），唯一系统路径直接提供。

`simulate_experience` 作为公开入口**自己**也会兑现"留空=按目标反解"：
它拿链路表的 `target_bler` 调 `resolved_for_target`，不再依赖调用方先解析
（旧版在参数校验处 `float(None)` 直接 TypeError，test_system 有回归）。
`resolved_for_target` 的产物在 `as_dict()` 里按方向分别标
`auto_from_target_bler` / `explicit_user_override`，不再只报含混的"已解析"。

### Type I 码本必须做秩自适应

38.214 的 Type I 反馈里 RI 和 PMI 是一起报的。`compute_precoder` 早期版本
把 type1 的秩硬定为 `max_rank`，在低秩信道上会输给 rank-1 的 DFT 波束——
总功率固定时多开的层每层分到的功率更少、SINR 更低。看起来像"码本不如单波束"，
其实是没做秩自适应。现在用与 SVD 同一套奇异值门限判据，两者才可比。

