# SuperRAN 协作机制（人看这一份就够）

维护者只有一个人（你）。Agent 是执行者，不是决策者。
本目录是**唯一的规则来源**：所有 Agent 开工前读它，不靠聊天里粘贴的 prompt。

---

## 你只需要做这几件事

| 你想干什么 | 你说这一句 |
|---|---|
| 让 Agent 改代码 | `读 .agents/AUTHOR.md 按它工作。任务：<一句话>` |
| 让另一个 Agent 审核 | 把 Author 结尾自动生成的那段**原样转发**给新会话 |
| 看现在什么状态 | 运行 `scripts\superran_board.ps1`，它会打开一张总览页 |
| 同步到 GitHub | `读 .agents/SYNC.md 按它工作` |
| 让公司 Agent 审阅 | 把 GitHub 下载的 zip + `.agents/COMPANY.md` 一起给它 |
| 处理公司审来的意见 | 把它的 md 放进 `docs\inbox\`，然后说「处理 docs\inbox 里的公司审阅报告」 |
| 把多条并行线合到一起 | `读 .agents/INTEGRATOR.md 按它工作` |

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

## 防冲突：主工作区不许提交

`.githooks/pre-commit` 会**拒绝在 `C:\Vibe\Wireless\SuperRAN` 里提交**，逼着 Agent
去建自己的工作区。新机器上装一次就行（worktree 自动继承）：

```
git config core.hooksPath .githooks
```

老实说清楚它的边界：**它拦得住「提交到主工作区」，拦不住「在主工作区改文件」。**
所以看板第一行显示「主线：有 N 个未提交文件」时就是报警信号——
正常情况下主线永远应该是干净的。

真要在主工作区提交（比如你自己）：`git commit --no-verify`。

## 目录里其他文件

- `AUTHOR.md` — 实现者合同
- `REVIEWER.md` — 审核者合同
- `TESTING.md` — 怎么跑测试。**两个坑会让 Agent 得出假的"测试通过"**，
  Author 和 Reviewer 都必读
- `RISK.md` — 风险分档（按文件路径写死，Agent 不许自己判断）
- `SYNC.md` — 同步 GitHub 的流程
- `INTEGRATOR.md` — 多条并行开发线合到一起时用（含解冲突与定位失败的方法）
- `COMPANY.md` — 给公司内网 Agent 的审阅合同，**含保密红线**
- `report.example.json` — 报告字段样例
