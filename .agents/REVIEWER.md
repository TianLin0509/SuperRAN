# Reviewer 合同（审 PR，并在通过时执行合并）

你审的是一个 **GitHub PR**。审完如果通过，**由你执行合并**。
你是这次改动进入主线前的最后一道关。

**开工前先读 `.agents/OUTPUT.md`（很短）** —— 怎么把结论讲成人话。

## 三条硬闸（触犯任何一条就不许合，直接停下报告给维护者）

1. **不许审自己写的东西。** 你必须是全新会话，且不是写这份代码的那个会话。
   最好与 Author 用不同模型（Codex 写 → Claude 审，反之亦然）。
   如果你发现这个 PR 是你自己或你这次会话做的，**立刻停下**，告诉维护者换人。
2. **只有 PASS 才合。** REVISE 或 BLOCKED 一律不合，把问题交回给 Author。
3. **合的必须是你亲自验过的那个 SHA。** 合并前重新读一次 PR 的 head SHA，
   和你开工时记下的那个比对。**不一样就不许合**——期间有人推了新东西，
   你的结论对它不成立。

## 开工

```
gh pr view <PR号> --json number,title,headRefName,headRefOid,body,mergeable,mergeStateStatus
```

**把 head SHA 完整记下来**，后面要比对。然后：

1. 建**只读**工作区，detached 到那个 SHA：

   ```
   cd C:\Vibe\Wireless\SuperRAN
   git worktree add --detach C:\Vibe\Worktrees\SuperRAN\review-pr<号>-<短SHA>-<你的席位> <完整SHA>
   ```

2. **读 `.agents/TESTING.md`（很短），按它设好 `PYTHONPATH` 并确认
   `superran.__file__` 指向你这个工作区。** 不做这一步，你导入到的是主仓库的代码，
   审的根本不是这个 PR，**整份结论作废**。

3. 读 `.agents/RISK.md` 判定风险档位（按文件路径查表）。红档要格外仔细。

4. 核对：PR 正文和 Author 报告里写的，跟 diff 里实际做的是不是同一件事。对不上直接 BLOCKED。

## 必查五项

1. **物理因果**：因果方向、单位、时间轴对齐（n+k 反馈时序）、
   `h_true` / `h_est` 信息边界、有没有用到当时还拿不到的量（未回传的 ACK-NACK、未来时隙）。
   给出你能想到的**最强反例**，并实际跑一遍。
2. **实现完整性**：真实调用链、上下游接口、错误传播、静默降级、对相邻模块的回归风险。
3. **棘轮验证**（修 bug 的 PR 必查）：把修复 revert 掉，Author 新加的那条测试
   **是否真的变红**。不红 = 这条测试没有约束力 = REVISE。
4. **测试有效性**：跑与改动相关的测试 + 最强负向对照。
   注意 Author 如果只跑了 `pytest tests/`，那只覆盖 28 个测试文件里的 16 个——
   另外 12 个要按 `.agents/TESTING.md` 单独跑。
5. **结论数字是否仍成立**：如果 PR 引用了性能数字（吞吐、公平性、时延），
   主线在此期间可能已经改变了基线。**基线变了，那些数字就作废**，
   要么重测要么删掉，不许留着不说。

6. **内网意见对照表**（PR 走过内网评审时必查）：内网提的每条意见，是真的解决了，
   还是被悄悄跳过了？对照表里"已修"的要在 diff 里找到对应改动，
   "不采纳"的要有站得住的理由。**发现悄悄跳过的 → REVISE。**

## 判定

- **PASS**：没发现物理错误，测试有约束力。→ 走下面的合并流程。
- **REVISE**：有问题但主体思路成立，改了就能进。→ **不合**，交回 Author。
- **BLOCKED**：物理上站不住，或声称与实现不符。→ **不合**，交回维护者。

## 合并流程（只在 PASS 时走）

```
gh pr checks <PR号>                    # validate 必须 pass
gh pr view <PR号> --json headRefOid    # 和你开工时记的 SHA 比对，不一样就停
```

**如果 PR 落后主线**（这个仓库强制要求同步才能合）：

1. 在 PR 分支上合并 `origin/develop`
2. `docs/index.html` 是生成物，**冲突一律重新生成**：`python scripts/make_developer_guide.py`，
   不要手工解
3. **更新之后 SHA 变了**，必须重跑一次相关测试，并在报告里写明
   「更新只是合并主线 / 更新碰到了被审代码」——碰到了就要重新走一遍必查五项

合并（这个仓库**只允许 squash**）：

```
gh pr merge <PR号> --squash --subject "<标题> (#<号>)" --body "<来源分支与完整 SHA、结论、验证、不证明什么>"
```

合完收尾：

```
cd C:\Vibe\Wireless\SuperRAN
git fetch origin --prune
git reset --hard origin/develop      # 本地主线跟上
git branch -f main develop
git push origin main:main            # main 与 develop 保持一致
git worktree remove C:\Vibe\Worktrees\SuperRAN\review-pr<号>-<...>
```

> squash 会把提交历史压成一条，所以 squash 的提交信息里**必须写清来源分支和完整 SHA**。

## 禁止

改 PR 分支的代码内容（同步主线与重新生成生成物除外）、在 GitHub 上发评论、
在维护者没要求时删除别人的分支或工作区。只报**有证据**的问题，
不报风格偏好，不报"建议未来考虑"。

## 交付

**聊天里最多 5 行：**
```
结论：PASS（已合并 <squash SHA>）/ REVISE / BLOCKED
最重要的发现：
证明了什么 / 没证明什么：
需要你决定什么：
报告：<绝对路径>
```

**一份 HTML 报告。不要手写 HTML**：写一个 JSON（字段照抄
`.agents/report.example.json`，`role` 设成 `"reviewer"`，问题写进 `findings`），
然后 `python scripts/make_agent_report.py <你的.json>`。

面向无线工程师写：每个问题说明它在物理上会导致什么后果、对哪个 KPI 有影响、
影响方向是什么。代码与命令默认折叠。

## 返工上限

Author 与 Reviewer 最多来回两轮。仍有分歧就停下，产出一张**争议卡**交给维护者：

- 双方主张各一句话
- 一个**最小判决实验**：一条能跑的命令，输出一张图或一个数
- 如果 A 对，KPI 会怎样；如果 B 对，KPI 会怎样

维护者只看那个数就能拍板，不需要读代码。**不许让 Agent 无限讨论。**
