# CHANGELOG

对外可见的改动按批次记录，最新在上。每条链接到 `docs/changes/` 的详细文档。
只在同步 GitHub 时更新，日常本地迭代不写这里。

## 2026-09-27 — 提问流程复审整合

- 保留用户扫描档位与单位归属，限定否定作用域；按真实站址选择中心服务小区，明确预设中的未实现边界。[详情](docs/changes/20260927-interview-review-integration.md)

## 2026-09-21 — 参考计算与证据边界

- 补齐参考 TBS 校准与非单调资源反查、29 档 LDPC 选档诊断、显式 SRS 逐 RB 功率口径；七档原始曲线仍缺，未切换系统 BLER。[详情](docs/changes/20260921-reference-reply.md)

## 2026-09-05 — 系统级第二批：HARQ N 进程 + 运行时 CQI

两个改动都动了 AMC/HARQ 链的**时序**，都以 #25 的唯一路径为基线，且第二个依赖第一个：
多进程 HARQ 必须建立在 #21「发送即扣 buffer」之上，否则同一批字节会被另一个进程再发一遍。

- **HARQ** 每 UE 从 1 个进程改为默认 8 个（`harq_max_processes`，上限 16，设 1 精确退回旧行为）。
  收益在体验速率不在小区吞吐：4 UE / ftp3 夹具体验中位 112.2→349.8 Mbps、完成时延 p50 39.2→12.0 ms，
  小区吞吐 67.2→68.4 Mbps —
  [详情](docs/changes/20260904-harq-多进程.md)（PR #19）
- **CSI/AMC** CQI 从建表时一次算好改为运行时按 CSI 报告周期（默认 20 ms = 40 TTI）事件上报，
  带 3 TTI 处理时延与 1.5 dB UE 实现损失（未标定）；`runtime_cqi_enabled=False` 逐位退回。
  离线 AMC 坐标系统性乐观：OLLA 关时首传 BLER 0.515 → 0.016，吞吐 380.5 → 429.3 Mbps；主循环慢约 5 倍 —
  [详情](docs/changes/20260904-csi-CQI运行时事件驱动.md)（PR #20）

**所有 2026-09-04 之前的 AMC / 吞吐 / 体验速率绝对数字都不能与本批次之后的数字放进同一张趋势图。**

## 2026-09-04 — MAC 层保真度批次（五个独立机制，各一提交）

- **TBS** 扣掉 DM-RS（6 RE/PRB）与 PDCCH（1 符号等效）：126 RE/PRB 而不是 144，TBS −12.5%，
  满缓冲小区吞吐约 −13.8%；S 时隙只折符号数、固定开销只扣一次 —
  [详情](docs/changes/20260904-amc-TBS扣PDCCH与DMRS开销.md)（PR #18）
- **KPI 口径** buffer 在首传发送时扣减、不看 ACK；重传只占资源。顺手修掉"重传落在上一个 busy period
  之后会让该 burst 从话统消失"的 bug（曾使误码越多体验越高） —
  [详情](docs/changes/20260904-kpi-buffer发送时扣减.md)（PR #21）
- **HARQ** 重传冻结 PRB 数而非 RBG 个数；不等长栅格下用带回溯的优先级搜索凑精确 PRB 数，等长栅格逐位不变 —
  [详情](docs/changes/20260904-harq-重传冻结PRB数.md)（PR #16）
- **MU-MIMO** 废除 `se_ratio_legacy` 标量记账，`pair_table` 成唯一路径；默认路径零变化 —
  [详情](docs/changes/20260904-mumimo-废除MU标量记账.md)（PR #17）
- **BLER / S 时隙 / MU 准入** BLER 后端显式工厂（表 3 预置曲线，表 1/2 解析近似标"未标定"）、EESM 压缩显式化、
  `s_slot_dl_fraction` 成显式配置（默认 0.7 逐位复现）、MU 新增 `min_pairing_mcs=4` / `pf_gain_threshold=0` /
  `orthogonalization_mode="select"` 三道准入门。独立审核判 REVISE、维护者审阅证据后放行 —
  [详情](docs/changes/20260904-amc-BLER显式化与S时隙折算与MU准入门.md)（PR #23）

## 2026-09-04 — Sionna RT 成为可选信道源，QuaDRiGa 退役

- **信道** `source: sionna_rt` 直连适配层：只换小尺度信道矩阵，撒点/路损/阵列/KPI 口径全部共用，CDL↔RT 差异可归因；
  默认仍是 CDL，默认路径经指纹验证逐位不变。三轮独立审核修掉四类"跑得通但物理错"的静默缺陷
  （场景名不透传、本地资产键名不对、移动相位算两遍、`rt_max_depth=0` 被换成 3） —
  [详情](docs/changes/20260903-channel-sionna-rt可选信道源.md) ·
  [审核返工](docs/changes/20260904-channel-RT审核返工.md)（PR #12）
- 审核顺带发现 develop 既有缺陷：`_spatial_panel_response` 的**垂直相位符号与 `top_to_bottom` 物理位置相反**，
  影响两个引擎所有 ≥2 行 RF 端口的阵列；翻符号后与权威模型残差 0.0000。**未修，等维护者定案**。

## 2026-09-03 — 容量模式合并进体验模式

系统级仿真从两条评估路径变成一条。`evaluation_mode` 删除，"容量仿真"现在是
体验路径上的一个话务配置 `traffic_model="full_buffer"`：队列无限使按需 RBG
反查退化成填满全部 RBG。**撤回**早先写的「每 TTI 一个 SU（或一对 MU）」：那只在频选关 +
MU 关时成立，出厂默认实测每忙 TTI 1.14 个用户、开 MU 1.86 个。**没有为 full_buffer 开任何特例
分支**，调度、AMC、HARQ、解调 SINR 聚合一律照体验口径走。

- **系统级** 删除 legacy 容量分支（system.py −958 行），容量口径改为 full_buffer 话务 —
  [详情](docs/changes/20260903-system-容量与体验模式合并.md)

顺带修掉一个**与容量无关的普遍右删失**：吞吐 KPI 过去只统计已排空的 busy
period，在飞的整条丢掉，而慢 burst 更容易没传完。实测普通有限话务过载
（8 UE / ftp3 20 Hz × 500 kB / 1 s）旧口径直接返回 `None`；5 Hz × 500 kB 那档
旧值是拿 9 个 burst 里唯一传完的 1 个算的，比修复后高 32%；轻载零扰动。
full_buffer 只是"每个 busy period 都在飞"的极端情形。修复后两个体验速率口径
（28.552 的 `drb_throughput_rel19_mbps` 与新增的 ITU-R M.2412 / TR 38.913
`ue_served_p5_mbps`）在满缓冲下都有值且收敛（7.05 vs 7.03）。同一组链路表上小区吞吐的差值**随配置变号**
（CRN + Gate 3 实测：开 OLLA 的默认配置下 −0.26%，关 OLLA 时 +0.57%~+3.27%），
B05 基准的两条判据仍成立但效应量缩约一半。**引用过旧绝对数的报告不可与新结果
拼在同一张趋势图里。** 系统级 preset 锚点：6 个单/多小区场景已在 #18/#19/#20 之后重测并标 `measured: true`，
其余 9 个仍为 `measured: false`（`expect` 里不许有数值）。

## 2026-09-03 — EDF 调度重新标定后合入

第四条线补齐。EDF 的算法实现没有问题（单独跑 45/45 通过），此前未随三线集成一起
合入是因为下行 AMC 链修正下修了吞吐，作废了它的三个数值锚点。本次在新基线上重新
标定并重测了全部结论数字。

- **调度** 新增 EDF / qos_pf_edf 包长感知调度，**默认不启用** —
  [详情](docs/changes/20260903-scheduler-EDF包长感知调度.md)

重测后 EDF 收益由 +2.3 pp 缩到 +0.45 pp、公平性代价由 Jain 0.4707→0.3032 变成
0.4764→0.2708。「当前场景不建议启用纯 edf」这个结论比原来更成立。
仓库里所有引用旧数字的地方（测试 docstring、开发者手册、channel-sim 技能参考）
都已同步为新基线数值，并保留旧值作为对照。

## 2026-09-03 — 三线集成

同一天有四条互不知情的开发线并行推进。本次把其中三条统一收口到 `develop`，
第四条（EDF 调度）经隔离实验判定需要重新标定后再进（见下）。

- **信道内核** 收编为 first-party 实现，移除外部源码依赖 —
  [详情](docs/changes/20260903-channel-核心收编first-party.md)（PR #6）
- **下行 AMC 链** 修正 rank 状态机、MU pair 记账、HARQ 反馈时序与解码 SINR —
  [详情](docs/changes/20260903-amc-harq-下行AMC链修正.md)（PR #5）
- **协作机制** 用 `.agents/` 合同取代旧的组长-组员流程，新增状态看板与固定报告生成器

### 合并时才暴露的跨线问题

1. **KPI 计数不一致**：PR #5 新增了 KPI（小区 26→27、用户 24→25），PR #6 的开发者手册
   文案仍写着旧计数。两个分支互不知情，单独看各自都自洽。已同步为 27/25。
2. **EDF 的数值标定被 PR #5 作废**：隔离实验证明 EDF 单独跑 45/45 通过，叠加 PR #5 后
   3 个工作点断言失败，再叠加 PR #6 失败集合不变。原因是 PR #5 下修了吞吐，
   EDF 标定的工作点随之移动。EDF 的算法实现没有问题，需要重新标定后再合入。
3. **生成物冲突**：三条线都重新生成过 `docs/index.html`，两两必冲突。
   一律用 `scripts/make_developer_guide.py` 重新生成，不手工解。
