# 任务 Agent 工作指令

**请按本文件展开工作。** 如果维护者没说清这次要做什么，先问一句，问清再动手。

你在给 SuperRAN（无线仿真平台）做优化。维护者是**无线通信工程师，不是软件工程师**。

> ⚠️ **开工前先读 `.agents/OUTPUT.md`（很短）。**
> 他的原话是「你说的东西我看得很费劲，理解不了」。
> **他看不懂你在干什么，你干得再对也等于没干。**
> 汇报讲无线、不讲代码；函数名行号一律进折叠区；结论先行；不贴日志。

---

## ① 开工：先建任务，拿到贯穿始终的 ID

```
cd C:\Vibe\Wireless\SuperRAN
python scripts/superran_task.py new "<一句话中文标题，说清在干嘛>" --seat <你的席位>
```

它会返回一个任务 ID，形如 `T20260904-cqi-event-driven`。
**这个 ID 之后要出现在四个地方**，维护者靠它一眼认出哪个文件属于哪个任务：

| 东西 | 命名 |
|---|---|
| 分支与工作区 | `T20260904-cqi-event-driven` |
| PR 标题 | `[T20260904-cqi-event-driven] CQI 改为运行时事件驱动` |
| 审核包 | `T20260904-cqi-event-driven_CQI改为运行时事件驱动.zip` |
| 内网意见 | `docs\inbox\T20260904-cqi-event-driven_内网审核.md` |

然后建自己的工作区（`new` 命令会直接把这条打印给你）：

```
git worktree add -b <任务ID> C:\Vibe\Worktrees\SuperRAN\<任务ID> develop
```

**主仓库 `C:\Vibe\Wireless\SuperRAN` 是最终版本的陈列柜，不是干活的地方**，
那里的 pre-commit 钩子会拒绝你提交。不要 clone，不要动别人的工作区。

接着读两份很短的文件：
`.agents/RISK.md`（判风险档，按文件路径查表，**不许自己估**）、
`.agents/TESTING.md`（**不读会得出假的「测试通过」结论**）。

---

## ② 实现

1. **一个提交只动一个物理机制**。AMC / HARQ / 调度 / SRS / 信道生成 / 随机数 / KPI 统计
   每次只碰一块。要跨模块，先提一个不改行为、测试全绿的接口提交。
2. **棘轮**：修的如果是审核发现的物理 bug，必须同时写一条
   **把修复 revert 掉就会变红**的测试，且亲手验过红绿两态。
3. 不许偷看未来信息（`h_true` / 尚未回传的 ACK-NACK / 未来时隙），不许静默降级。
   用了工程近似就明说是近似。
4. **测试失败不许放宽断言让它变绿。** 基线变化导致数值锚点失效的，
   去调场景参数让断言重新成立；改不动就停下来说明。
5. 动手前最多问一轮问题，只问**真正影响物理实现**的，并给出你的推荐。

### 交付前自查（这不叫审核）

**你审不了自己的盲点，独立审核在后面。报告里不许写"已通过审核"。**

- [ ] 按 `TESTING.md` 跑过测试，确认过 `superran.__file__` 指向自己的工作区
- [ ] 跑的是**相关**测试，不是只跑 `pytest tests/`（它只覆盖 28 个文件里的 16 个）
- [ ] 引用的每个数字都是这次实测的，不是从旧报告抄的
- [ ] "没证明什么"写了实话，不是"无"

### 出报告并记一步

报告**不要手写 HTML**：写一个 JSON（字段照抄 `.agents/report.example.json`），
然后 `python scripts/make_agent_report.py <你的.json>`。

```
python scripts/superran_task.py log <任务ID> 实现 --report <报告绝对路径> --sha <完整SHA>
```

---

## ③ 红档：打审核包送内网

查 `.agents/RISK.md`。**红档必走内网评审**（约 20 分钟，维护者手动同步）；
黄档你自己判断；绿档不用，直接跳到 ⑤。

```
powershell -File C:\Vibe\Wireless\SuperRAN\scripts\superran_review_pack.ps1 <任务ID>
python scripts/superran_task.py log <任务ID> 送内网 --zip <zip绝对路径>
```

把 zip 路径给维护者，一句话告诉他：**右键这个 zip → AI HUB 同步选项**。

**包送出之后这个分支就冻住** —— 意见回来之前不要再往上推提交，
否则意见指的行号和代码会对不上。

## ④ 收到内网意见后修改

意见在 `docs\inbox\<任务ID>_内网审核.md`。逐条处理，然后：

```
python scripts/superran_task.py log <任务ID> 修改 --note "<例：3 修 1 不采纳>"
```

**PR 正文里必须附一张意见对照表**：

| 内网说 | 怎么处理的 |
|---|---|
| 问题 1 一句话 | 已修，测试 `xxx` |
| 问题 2 一句话 | **不采纳**，理由是…… |

**"不采纳"允许，但必须写理由。** 悄悄跳过一条会被合并 Agent 查出来。

---

## ⑤ 提 PR

```
git push -u origin HEAD:refs/heads/<任务ID>
gh pr create --base develop --head <任务ID> --title "[<任务ID>] <中文标题>" --body "<见下>"
python scripts/superran_task.py log <任务ID> 提PR --pr <编号>
```

PR 正文必须写清五项，合并 Agent 靠它对照 diff：

1. 改了哪个无线环节（物理因果链）
2. 为什么这么改
3. 证据：跑了哪些测试、关键数字，以及 `superran.__file__` 指向哪里
4. **没证明什么** —— 不许写"无"
5. 风险档与本次触碰的物理机制；走过内网的附意见对照表

**禁止自己合并 PR**，也禁止合并别人的。合并是合并 Agent 的事。

---

## ⑥ 交付给维护者

**聊天里最多 5 行**（细节全进 HTML 报告，这里不展开）：

```
结论：
做了什么：
证明了什么 / 没证明什么：
需要你决定什么：
报告：<绝对路径>
```

最后输出一段**可直接转发**的交接词，尖括号全部填好：

```
请根据 C:\Vibe\Wireless\SuperRAN\.agents\MERGER.md 展开工作。
任务 <任务ID>，审 PR #<号>，通过就由你合并。
PR head SHA：<40 位>
风险档：<绿/黄/红>   本次触碰的物理机制：<模块名>
棘轮测试：<测试函数名，或"本次无">
```

---

## 被打回怎么办

合并 Agent 判 REVISE 或 BLOCKED 时，维护者会把结论转回给你。逐条修，然后：

```
python scripts/superran_task.py log <任务ID> 修改 --note "<按合并 Agent 的 N 条意见修改>"
python scripts/superran_task.py log <任务ID> 提PR --pr <同一个编号>
```

**如果打回的问题涉及物理正确性，你可以主动建议再走一次内网评审**——
在 5 行汇报的「需要你决定什么」里说明理由，让维护者拍板。

---

> 维护者会跑 `scripts\superran_tasks.ps1` 看工作台。
> **你每 `log` 一步，他那张图就自动往前走一格。别忘了 log。**
