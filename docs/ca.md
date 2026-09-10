# 同站同步载波聚合（CA，Carrier Aggregation）

CA 让一个 UE 的同一份业务在多个载波上发送。队列、业务到达与用户统计只有一份；各载波的 CQI（信道质量指示）、Rank（空间层数）、OLLA（外环链路自适应）和 HARQ（混合自动重传）各自推进。

## 可运行入口

在本地工作位设置 `PYTHONPATH=<worktree>\src`，从该目录执行：

```powershell
$env:SUPERRAN_NO_BROWSER='1'
python scripts/run_ca_example.py examples/ca_100_20.json --make-fixture
```

`--make-fixture` 只在首次演示时使用：明确创建两份**合成功能夹具**，两载波共用用户位置和时间轴；它们不是原生信道生成或真实 SRS 测量证据，不能用于性能结论。再次运行去掉该选项。已有数据集不会被覆盖。

真实输入时把 JSON 中两个 `dataset_id` 换成已有数据。相同 UE 身份、逐样本位置、服务小区和快照时钟必须一致；缺少或错位会报错。20 MHz 需要显式兼容的 CSI 配置，例如 `srs_hopping:false`，保留 CSI 老化与 SRS 估计读取，只关闭不适用的 272-PRB/17-hop 模式。

MCP 仍调用 `sr_system_sim`，新增可选 `ca_config`；不填时保持旧单载波入口，包括拒绝 20 MHz 的原合同。CA JSON 示范是 100 MHz（272 PRB，17×16）＋20 MHz（51 PRB，6×8＋3）。支持三个及更多合法同站、30 kHz 载波，只有一个 PCC（主分量载波）；其余为 SCC（辅分量载波）。TDD（时分双工）采用共同时钟和帧结构。

Python 调用使用 `superran.ca.build_ca_link_tables(CarrierSet(...), inputs, config=CaSchedulerConfig(...))`，返回 `CaLinkTables`，再交给原 `system.simulate` 或 `system.simulate_replications`。每个输入含独立的 `h_users`、`geo_sinr_db`、显式 `csi=CsiConfig(...)`、`geometry={ue_positions,snapshot_times_ms}` 和原建表参数；`build_link_tables` 签名没有变化。每个 UE 的信道为 `[snapshot,PRB,BS,UE]`。预建表入口必须携带各 CC 的共享几何/时序来源 manifest。

## 分流与预调度口径

- `off`：按 PCC 身份走原单载波路径，说明未用 SCC。单 CC 的所有模式都回到原路径，既不改随机数也不改 KPI（关键性能指标）。
- `independent`：每 TTI（传输时间间隔）先冻结各 CC 配额，再调度。`average` 等份；`bandwidth` 按真实 PRB 数×当前层数的预测谱效；`rbnum` 按当前资源估计和逐 RBG（资源块组）预测容量。只用发送端可知的预测 SINR（信干噪比），真实 SINR 只用于解码。rbnum 对就绪重传先扣其组数，再按可用组数取高容量组，是预调度容量估计，不预测未来 TTI。
- 小包门限为 `max(72000 bit×PCC带宽/100MHz, 全局PF历史平均字节/TTI×8)`，等于门限仍是小包。显式设置 0 时同时绕过历史门限。average/bandwidth 小包只发 PCC，rbnum 选预计容量最高的可服务 CC。无可服务资源就留在原队列。
- 分流以整字节计算，再输出 bit；余数字节按最大余数分配，平局 PCC 优先、再按载波身份排序。例如 9001 B 平分为 4501/4500 B，9003 B 按 2∶1 分成 6002/3001 B。未使用配额不在同一 TTI 借给其他 CC。
- `cort`：先用等份配额形成首轮计划，记录每 CC **首传净荷**预占；再按各 CC 总预测容量降序修正，平局 PCC 优先。每 CC 可用水量＝TTI 开始的原始队列－其他 CC 预占，绝不减掉已扣过的队列第二次。保留当前 grant，只用空闲 RBG 扩大；每次重新算 MCS（调制编码档位）、真实 PRB 数、TBS（传输块大小）及两名 MU（多用户）用户的水量，重新通过资源账本。既不缩原 grant，也不改变重传身份。这是明确的工程首轮/修正规则，尚未证明与 对标实现 逐阶段等价。

原 HARQ 时序、默认 8 进程、最多一次重传、反馈后外环学习继续生效。重传不带新队列水量；原始 TBS 含填充和重传，不能当作唯一首传净荷。

## 结果和边界

每个 TTI 的全局 PF（比例公平）平均值只按跨 CC 汇总的记账信用更新一次。同 UE、同 TTI 的首传合并后再算 busy period（队列由非空到排空的一段业务），不会把并行载波时长串接或重复用户人口。满缓冲仍在同一状态机里，标准的已完成 busy-period 吞吐保留 `null`。

结果的 `ca`/`diagnostics.ca` 提供组合指纹、逐 CC 身份、原始水量、冻结配额、实际 TBS、首传预占/发送量、扩展 RBG、CQI/Rank/OLLA/HARQ 与逐 TTI trace。全局 trace 使用载波偏移后的 RBG 索引并标记 `carrier_id`；逐 CC trace 保持本载波索引。IoT（干扰噪声比）跨频没有可加意义，保留在逐 CC 诊断中，聚合字段为 `null`。随机流按 RngBook＋载波身份派生，事件绑定 `[TTI,UE]`。

静态 `active_mask={carrier_id:[bool,...]}` 按 UE 控制载波参与。自动 SCell（辅小区）激活、延迟 FIFO（先进先出队列）、非零 PCC/SCC 延迟、混合 numerology（子载波参数）、异步 TDD、跨站协调和跨 CC 预编码均不支持；相应启用配置硬失败。多载波当前要求 PF 和逐 RBG 频选。MCP 自动负载校准尚不支持。GUI 控制面没有增加 CA 表单，当前入口是 Python/JSON/MCP。

比较结果必须匹配组合来源指纹和公共随机数；Gate 2/3（样本充分性与统计显著性）继续由原比较入口执行。单元测试、字节守恒或扩容反例都不能替代正式性能实验。
