# 2026-09-04 AMC / TDD / MU — BLER 后端显式化、S 时隙资源折算与 SU/MU 准入门

**分支 / SHA**：`feat/bler-sslot-sumu-20260904` / 审核 SHA `f589afd02c9a3646e004f281ad964c21b61d122b`，
同步主线后 `0e949d246be36ee91f5981484e82b718c9bcba73`，合入 `develop` 为 `3174dfe4`（PR #23）　**风险档**：红
**报告**：`C:\VibeData\Artifacts\Reports\SuperRAN\20260904-0955-reviewer-pr-bler-sslot-sumu-integration-claude1.html`
**任务 ID**：`T20260904-bler-eesm`

> 本文档由 2026-09-05 的文档刷新补写（合入时缺失），内容取自 PR #23 的合并说明、
> 审核报告与当前 `develop` 代码；未重新跑任何仿真。

## 改了什么物理机制

三个彼此独立、但都属于"把隐含假设变成显式输入"的改动，合在一个 PR 里是任务书
（任务 3/4/5）的安排，不是物理上有耦合。

**一、BLER 后端变成显式工厂。** 以前"用哪条 BLER 曲线"散在几处默认值里。现在统一由
`linkadapt.make_bler_model(table, config=...)` 构造：表 3 返回预置曲线模型
（`preset_20b_256qam`，28 档 NewTx 曲线），表 1/2 返回有限码长解析模型
（`c=2.2`、`implementation_loss_db=1.0` 可配）。未知配置项**硬失败**，不会以为 override
生效了其实被静默忽略。频选场景下逐 RBG SINR 的压缩也显式化为 `eesm_compress(sinr_db, beta)`
——`-β·ln(mean(exp(-γ/β)))`，`β` 由 `DEFAULT_BETA_BY_MCS_TABLE` 按调制阶数分组给出。
**唯一系统主循环仍只接受表 3**；表 1/2 只能在链路级工具里显式选择，结果标注"解析、未按
译码器或现场曲线标定"。

**二、S 时隙的下行占比成为显式配置。** 新增 `SystemConfig.s_slot_dl_fraction`（默认 0.7）。
报告里的 `dl_ratio`、每 TTI 可用 RE、体验路径 `TbsLookup` 查表三处**读同一个值**，
不再各自硬编码。`infer_s_slot_fraction(pattern)` 只认已登记的产品图案
（`DDDSU` → 10/14，`DDDDDDDSUU` → 6/14），其他图案要求用户显式给值，
**不凭 D/S/U 三个字母猜特殊时隙内部的 DwPTS/GP/UpPTS 配比**。

**三、MU 配对多了三道显式准入门**（`mumimo.pair_users` 与 `SchedulerConfig` 同名字段）：

| 门 | 默认 | 作用 | 关掉的办法 |
|---|---|---|---|
| `min_pairing_mcs` | 4 | 首传 MCS 低于该档的用户只参与 SU，不进配对候选 | 设 0 |
| `pf_gain_threshold` | 0.0（关） | 启用后用 `Σ(useful_bytes / PF_R_avg)` 比较 MU/SU 计划，增益不够就否决 | 保持 0 |
| `orthogonalization_mode` | `select` | 相关性筛选；`none` 关闭筛选；`schmidt` **未实现，硬报 `NotImplementedError`** | 设 `none` |

这些是准入门，不替代最终的小区谱效 / useful-bytes 方案比较。

## 为什么

- BLER 后端不显式，就没法说清一个数字是"预置曲线查出来的"还是"解析模型算出来的"，
  两者证据等级不同（见 `CLAUDE.md`「BLER 有分析模型和预置曲线两条后端」）。
- S 时隙占比曾以 `0.7` 直接乘在 RE 上（`int(144 × PRB × 0.7)`），与 #18 的
  "先折符号数、开销只扣一次"口径冲突；先把它变成显式配置，#18 才能在同一个入口上修。
- MU 准入以前只有相关性阈值一道门，低 MCS 用户被配对后 BLER 抬高的问题没有开关可控。

## 证据

- `run_test_matrix.py --tier quick`：18 PASS / 0 FAIL，101.9 s；矩阵外脚本式测试
  （test_physics_invariants、test_linkadapt、test_interference、test_linklevel、test_gates、
  test_csi_aging）全部 exit=0；审核 SHA 上另跑 `pytest tests/ -q` 222 passed。
- **兼容性逐位对照**：S 时隙默认 0.7 三场景吞吐逐位一致；experience 模式 BASE/HEAD 逐字段
  一致；新旋钮能穿过 spawn 多进程 replication；EESM 重构正常区间最大差 5.33e-15 dB。
- 棘轮（`tests/test_physics_invariants.py`）：`test_s_slot_fraction_single_source_of_truth`
  （报告占比与配置同源、两种图案的推断值）、`test_bler_factory_and_eesm_are_explicit`
  （工厂返回类型与配置生效、EESM 广播形状）、`test_mu_admission_gates_are_explicit_and_reversible`
  （`min_pairing_mcs=4` 剔除 MCS3 用户、设 0 恢复旧行为、`schmidt` 硬失败）。
- 独立集成审核判 **REVISE**，维护者审阅证据后**明确放行**。这触犯合并合同硬闸 2
  （只有 PASS 才合），在此留痕。

## 没证明什么

- **两条棘轮当时被审核指出"revert 后不变红"**：S 时隙那条不碰调度侧；MU 准入那条只覆盖
  `mumimo.pair_users`，不覆盖主循环里的准入分支。审核所指的 capacity 主循环已被 #25
  整体删除，但**本次文档刷新没有重新验证**这两条棘轮在唯一路径上是否已具备红态。
- 审核报告第 1 条（capacity 主循环里从未生效的 `mu_corr_threshold` 硬门被激活，MU 占比
  62.2%→25.7%、吞吐 225.82→293.65 Mbps）随 legacy 路径一起消失，**不再是现状**；
  但"`min_pairing_mcs=0, pf_gain_threshold=0` 可复现旧准入"这句话在当时的 capacity 下
  不成立（还需 `orthogonalization_mode="none"`），拿旧基线做 A/B 前要知道这一点。
- 表 1/2 的解析 BLER **未按任何译码器或现场曲线标定**；`DEFAULT_BETA_BY_MCS_TABLE` 是公开
  调制阶数分组近似。EESM 压缩本身是显式了，但系统主循环当前的跨 RBG 聚合仍是 dB 算术平均
  （见 `experience.py` 模块说明），EESM 尚未成为主循环的默认聚合。
- `infer_s_slot_fraction` 只覆盖 `DDDSU` 与 `DDDDDDDSUU`。
- Schmidt 正交化未实现。`register_bler_curve_source` 无 MCP 出口，进程级注册表进不了 `simulate`。
- 没有预注册 A/B 或门 3；本 PR 不声称任何吞吐 / 谱效 / 边缘用户 KPI 提升。
  对照实验用 2~6 UE 合成信道与 0.3~1.0 s 短仿真。

## 影响哪些 KPI

默认配置（`s_slot_dl_fraction=0.7`、`min_pairing_mcs=4`、`pf_gain_threshold=0`、
`orthogonalization_mode="select"`、MU 默认关）下**逐位复现旧结果**。
显式开 MU 时 `min_pairing_mcs=4` 会把低档用户留在 SU：`mu_share` 下降、边缘用户较少
参与配对——这正是 #25 之后受控对照里"MU 给小区容量 +64%、边缘用户只 +7%"的成因之一。
表 1/2 结果的 BLER 证据等级降为"解析未标定"，链路级工具会在返回里标注。
