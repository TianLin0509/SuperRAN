# SuperRAN 协作机制（人看这一份就够）

维护者只有一个人（你）。Agent 是执行者，不是决策者。
本目录是**唯一的规则来源**：所有 Agent 开工前读它，不靠聊天里粘贴的 prompt。

---

## 日常怎么干活：开一个 AI 群聊

一个群聊 = 一个任务。两个席位：**工作位**实现，**合并位**独立验证并合入主干。

| 你想干什么 | 你做什么 |
|---|---|
| **让 Agent 改代码** | AI HUB 新建群聊 → 场景选「**开发**」→ 工作目录选 `C:\Vibe\Wireless\SuperRAN` → 成员两个 → 打一句人话，回车 |
| **看进度** | Hub 顶栏「开发」看板：每个任务走到哪一步、验了什么、有什么风险 |
| **中途想插话** | 直接在群里说 |
| 下一个需求 | **开新群聊**，别在老群里接着提 |
| 补对外改动文档 | `读 .agents/SYNC.md 按它工作` |
| 让内网 Agent 通审整个仓库 | 跑 `scripts\superran_company_zip.ps1`，把它打的 zip 发过去 |
| 让内网 Agent 审一次改动 | 跑 `scripts\superran_review_pack.ps1 <分支名>`，把审核包发过去 |
| 处理内网审来的意见 | 把它的 md 放进 `docs\inbox\`，然后**开一个新群聊**说「处理 docs\inbox 里的内网审阅报告」 |
| 把多条并行线合到一起 | `读 .agents/INTEGRATOR.md 按它工作`（日常不需要） |

不需要记路径、SHA、分支名或命令。

## 流程长什么样

```
你在群里打一句要干什么
    ↓
① 工作位  从阿里云开自己的 worktree → 实现 → 自测 → 上传候选并交完整 SHA
          交四行人话：干了什么 / 验了什么 / 有什么风险 / 报告在哪
    ↓
② 合并位  独立跑一遍验证（不采信①说的）
          物理 bug 还要做棘轮反证：新测试跑未修复主干必须变红
          PASS → 由它执行合并并发布云端 develop；FAIL → 交回 BLOCKERS
    ↓
③ 工作位  只修 BLOCKERS 列的，提交同一分支并交新 SHA → 回到 ②
```

最多 3 轮。PASS 就结束。**你只做两件事：说要干什么，看看板。**

合并权只在合并位手里，它有三条硬闸：**不许审自己写的、只有 PASS 才合、
合的必须是它亲自验过的那个提交**。

### 内网审核在哪一环

**不在这条流水线里。** 它是**两个独立任务之间的空隙**：
一个任务合完 → 你想送审时手动打包 → 审回来的意见，**另开一个群聊**当新任务处理。

这么定的好处是流水线不必为它分叉，你也不用在任务中途冻住分支。

---

## 唯一可信的地方

- **权威主线**：[阿里云私有仓库](https://ai.lt-stockpartner.tech/repos/superran/SuperRAN) 的 `develop`；`main` 只在明确发布时更新。
- **本机主仓库**：每台电脑 clone 一份，路径由本机选择；维护者现有目录为 `C:\Vibe\Wireless\SuperRAN`。
- **任务工作区**：本机主仓库外的独立 worktree，名称带任务和席位；保留在途内容，清理前核对归属。
- **跨电脑交接**：`scripts/agent_repo.py` 的 start / submit / fetch-candidate；详见 `SYNC.md`。
- **历史**：GitHub 仅保留读取，本地 `github-archive` 关闭推送；日常维护不依赖 GitHub。

## 三条铁律（Agent 违反即返工）

1. **一个提交只动一个物理机制。** AMC / HARQ / 调度 / SRS / 信道生成 / 随机数 / KPI 统计
   这几块每次只碰一块。跨模块必须先提不改行为的接口提交。
2. **棘轮：审核发现的每个物理 bug，修复时必须带一条「在旧代码上会失败」的测试**，
   并入 `tests/test_physics_invariants.py`。没有这条测试，不算修完。
   合并位会**亲自反证**：把新测试拿去跑未修复的主干，不红就判 FAIL。
   → 这是让库单向变好、不依赖 Agent 记性的唯一机制。
3. **不许静默降级。** 跑不动、数据缺失、用了工程近似，必须写在报告的「没证明什么」里。

## 主干只有一个入口

```
python scripts/merge_task.py <分支> --expected-head <任务SHA> --expected-trunk <主干SHA>
# 同一命令加 --dry-run，只验不合。两次必须绑定同一对亲自验过的完整 SHA。
```

它会核对本地 SHA → 试合（先不提交）→ **亲自跑流程验证与全量仿真测试** → 过了才提交。失败且现场未变化时撤销试合；发现额外暂存、文件变更或冲突时保留现场并非零退出，先核对归属再处理。
实际合并用 `agent_repo.py merge` 包装上述闸门，成功后生成版本绑定回执；
再由 `agent_repo.py publish` 核对云端基线并发布。已有未提交内容时拒绝并保留现场。
必须在主工作目录跑（worktree 里的导入会解析到主仓库，证据是假的）。

两个钩子守着这条唯一入口：

- `.githooks/pre-commit` —— 拒绝在主工作目录提交，逼 Agent 去开自己的 worktree
- `.githooks/pre-push` —— develop 发布须有匹配回执和云端基线；main 保持独立发布边界

新机器上装一次（worktree 自动继承）：

```
python scripts/agent_repo.py init
python scripts/agent_repo.py doctor --online
```

说清它的边界：**拦得住「提交到主工作区」，拦不住「在主工作区改文件」。**
所以主线出现未提交文件就是报警信号——正常情况下它永远应该是干净的。

日常提交始终走 worktree；合并脚本只在测试通过后的提交带本地放行变量。

## 每次工作结束你会收到什么

群里最多四行：**PROGRESS / VERIFIED / RISK / REPORT**，讲无线不讲代码。
需要详报时另出一份 HTML，代码和命令默认折叠，
汇总在 `C:\VibeData\Artifacts\Reports\SuperRAN\index.html`。

## 目录里其他文件

- `OUTPUT.md` — **怎么跟你说话**。所有角色开工前必读，讲人话的五条铁律
- `AUTHOR.md` — **工作位合同**（实现 → 自测 → 本地提交 → 四行人话）
- `MERGER.md` — **合并位合同**（独立验证 → 棘轮反证 → PASS 才合，含三条硬闸）
- `project.json` — 项目配置。主干名、闸门跑哪些测试、worktree 放哪。**钩子和合并脚本都读它**
- `TESTING.md` — 怎么跑测试。**两个坑会让 Agent 得出假的「测试通过」**，两个席位都必读
- `RISK.md` — 风险分档（按文件路径写死，Agent 不许自己判断）
- `SYNC.md` — 阿里云跨电脑交接、发布与改动记录
- `INTEGRATOR.md` — 多条并行开发线合到一起时用（日常单条任务不需要）
- `COMPANY.md` — 内网 Agent **通审整个仓库**的合同，**含保密红线**
- `COMPANY_REVIEW.md` — 内网 Agent **审一次具体改动**的合同，打包时会自动放进审核包
- `report.example.json` — 报告字段样例
