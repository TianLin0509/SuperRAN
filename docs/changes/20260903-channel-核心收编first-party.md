# 2026-09-03 信道内核 — 收编为 SuperRAN first-party 实现，移除外部源码依赖

**来源**：PR #6，分支 `author/TianLin0509/independent-channel-core` @ `dc4740184507d94d7104903feff0805cd4323adb`
**风险档**：红　**审核**：全量测试通过 + CI + 浏览器 QA

## 改了什么物理机制

原始信道生成此前经由一个惰性 ChannelHub / `msg_embedding` 适配器取数，等于把物理内核的
一部分寄放在外部工程里。新增 `src/superran/native.py`（1770 行）把生成、轴序、
TDD 互易、预编码与功率约束的口径全部收进本仓，成为 first-party 实现。

## 为什么

外部源码是一个不受本仓合同约束的边界：它的轴序或归一化一变，本仓的物理结论就静默失真，
而测试查不出来。收编之后，信道合同由本仓自己的 `test_native_independence.py` 与
`test_channel_generation_contract.py` 守住。

## 证据

quick 17/17、physics 10/10、pytest 173、经典基准 10/10、压力 362 cases、专项审计 15/15，
CI 与浏览器 QA 全通过。集成后在主仓库实测 `channelhub.warmup()` 返回 `ok=True`，
且 `channelhub_root()` 指向仓库自身而非外部目录。

**一个原先没被记下的连带收益**：外部 ChannelHub 是按仓库位置就近发现的，所以在
linked worktree 里会解析到不兼容的那一份，导致测试会话根本起不来
（`source contract mismatch: array_port_order`）。收编 first-party 之后，
worktree 里设好 `PYTHONPATH` 就能正常跑测试，`channelhub_root()` 指向该工作区自身。
这消除了「worktree 里无法做可信验证」这一整类问题。

## 没证明什么

**不证明现场校准**——与实测网络的一致性没有验证。**不证明 direct RT**——射线追踪直连
路径本次延后。**非 UMa/UMi 场景走显式工程 fallback**，不是标准实现。不证明性能收益。
本仓也未独立复核「与旧路径逐位一致」这条声明。

## 影响哪些 KPI

理论上应与旧路径逐位一致（这正是压力测试要守的），因此 KPI 不应改变。
若出现差异即为回归。
