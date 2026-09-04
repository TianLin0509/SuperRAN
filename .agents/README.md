# SuperRAN 协作机制（人看这一份就够）

维护者只有一个人（你）。Agent 是执行者，不是决策者。
本目录是**唯一的规则来源**：所有 Agent 开工前读它，不靠聊天里粘贴的 prompt。

---

## 你只需要做这几件事

| 你想干什么 | 你说这一句 |
|---|---|
| 让 Agent 改代码 | `请根据 C:\Vibe\Wireless\SuperRAN\.agents\AUTHOR.md 展开工作。任务：<一句话>` |
| 审核 + 合并 PR | 把任务 Agent 结尾生成的那段**原样转发**给新会话。它审完通过就直接合 |
| **每天开工先看这个** | 运行 `scripts\superran_tasks.ps1` —— 它直接告诉你「现在该做什么」，并给出可复制的命令 |
| 看工作区细节 | 运行 `scripts\superran_board.ps1` |
| 补对外改动文档 | `读 .agents/SYNC.md 按它工作` |
| 让内网 Agent 通审整个仓库 | 跑 `scripts\superran_company_zip.ps1`，把它打的 zip 发过去 |
| 让内网 Agent 审一次改动 | 跑 `scripts\superran_review_pack.ps1 <分支名>`，把审核包发过去 |
| 处理内网审来的意见 | 把它的 md 放进 `docs\inbox\`，然后说「处理 docs\inbox 里的内网审阅报告」 |
| 把多条并行线合到一起 | `读 .agents/INTEGRATOR.md 按它工作` |

除此之外不需要记任何路径、SHA、分支名或命令。

---

## 任务 ID：把所有东西串起来的那根线

每个任务开工时由 Agent 生成一个 ID，形如 `T20260904-cqi-event-driven`，
**四样东西都用它**，你一眼就能认出哪个文件属于哪个任务：

```
分支      T20260904-cqi-event-driven
PR 标题   [T20260904-cqi-event-driven] CQI 改为运行时事件驱动
审核包    T20260904-cqi-event-driven_CQI改为运行时事件驱动.zip
内网意见  docs\inbox\T20260904-cqi-event-driven_内网审核.md
```

工作台也靠它把一条任务的所有节点串成一条泳道。

## 流程长什么样

```
你说一句要干什么
    ↓
① Author 实现 → 自查 → push 分支 + 开 PR
                          ↓  红档才继续走 ②③
② 打审核包 → 你带进内网 → 内网 Agent 出意见 md
   （这期间分支冻住，别再往上推提交）
                          ↓
③ Author 按意见改 → 更新 PR + 附「意见对照表」
                          ↓
④ Reviewer 审 PR + 核对对照表 → PASS 就由它合并
```

绿档、黄档直接从 ① 跳到 ④。**你只做两件事：说要干什么，转发一段话。**

Agent **能开 PR，但不能合自己的 PR**。合并权只在 Reviewer 手里，
而且它有三条硬闸：不许审自己写的、只有 PASS 才合、合的必须是它亲自验过的那个 SHA。

**你只做两件事**：说要干什么，转发一段话。

Agent **能开 PR，但不能合自己的 PR**。合并权只在 Reviewer 手里，
而且它有三条硬闸：不许审自己写的、只有 PASS 才合、合的必须是它亲自验过的那个 SHA。

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

- `OUTPUT.md` — **怎么跟你说话**。所有角色开工前必读，讲人话的五条铁律
- `AUTHOR.md` — 任务 Agent 指令（含任务 ID 与全流程）
- `MERGER.md` — 合并 Agent 指令。**审 PR，通过就由它执行合并**（含三条硬闸）
- `TESTING.md` — 怎么跑测试。**两个坑会让 Agent 得出假的"测试通过"**，
  Author 和 Reviewer 都必读
- `RISK.md` — 风险分档（按文件路径写死，Agent 不许自己判断）
- `SYNC.md` — 对外改动文档与批次记录
- `INTEGRATOR.md` — 多条并行开发线合到一起时用（含解冲突与定位失败的方法）
- `COMPANY.md` — 内网 Agent **通审整个仓库**的合同，**含保密红线**
- `COMPANY_REVIEW.md` — 内网 Agent **审一次具体改动**的合同，打包时会自动放进审核包
- `report.example.json` — 报告字段样例
