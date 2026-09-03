# SuperRAN 协作机制（人看这一份就够）

维护者只有一个人（你）。Agent 是执行者，不是决策者。
本目录是**唯一的规则来源**：所有 Agent 开工前读它，不靠聊天里粘贴的 prompt。

---

## 你只需要做四件事

| 你想干什么 | 你说这一句 |
|---|---|
| 让 Agent 改代码 | `读 .agents/AUTHOR.md 按它工作。任务：<一句话>` |
| 让另一个 Agent 审核 | 把 Author 结尾自动生成的那段**原样转发**给新会话 |
| 看现在什么状态 | 运行 `scripts\superran_board.ps1`，它会打开一张总览页 |
| 同步到 GitHub | `读 .agents/SYNC.md 按它工作` |

除此之外不需要记任何路径、SHA、分支名或命令。

---

## 唯一可信的地方

- **主线**：`C:\Vibe\Wireless\SuperRAN`（分支 `develop`）。这就是你和 Agent 打开的那个目录。
- **任务工作区**：`C:\Vibe\Worktrees\SuperRAN\<任务名>`。用完即弃。
- **禁止**再 clone 一份 SuperRAN 到别处。要并行就用 `git worktree add`。
- **上游**：`https://github.com/TianLin0509/SuperRAN.git`，只在你明确说"同步 GitHub"时才动。

## 三条铁律（Agent 违反即返工）

1. **一个提交只动一个物理机制。** AMC / HARQ / 调度 / SRS / 信道生成 / 随机数 / KPI 统计
   这几块每次只碰一块。跨模块必须先提不改行为的接口提交。
2. **棘轮：审核发现的每个物理 bug，修复时必须带一条"在旧代码上会失败"的测试**，
   并入 `tests/test_physics_invariants.py`。没有这条测试，不算修完。
   → 这是让库单向变好、不依赖 Agent 记性的唯一机制。
3. **不许静默降级。** 跑不动、数据缺失、用了工程近似，必须写在报告的"没证明什么"里。

## 每次工作结束你会收到什么

聊天里最多 5 行：**结论 / 做了什么 / 证明了什么·没证明什么 / 需要你决定什么 / 报告路径**。
外加一份用无线语言写的 HTML 报告，代码和命令默认折叠。
所有报告自动汇总到 `C:\VibeData\Artifacts\Reports\SuperRAN\index.html`。

## 目录里其他文件

- `AUTHOR.md` — 实现者合同
- `REVIEWER.md` — 审核者合同
- `RISK.md` — 风险分档（按文件路径写死，Agent 不许自己判断）
- `SYNC.md` — 同步 GitHub 的流程
