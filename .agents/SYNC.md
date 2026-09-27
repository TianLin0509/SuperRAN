# 阿里云跨电脑交接与改动记录

> 工作位本地提交后交分支与完整 SHA，见 `.agents/AUTHOR.md`。
> 验证与执行合并是合并位的事，见 `.agents/MERGER.md`。**日常合并不经过 GitHub。**
> 阿里云私有 Gitea 是唯一日常远端。GitHub 只保留历史读取，不再日常写入。

## Agent 最短工作回路

首次 clone 地址为 `https://ai.lt-stockpartner.tech/repos/superran/SuperRAN.git`。
每台电脑一份主仓库，安装依赖见 `INSTALL_AGENT.md`；在主仓库执行：

```bash
python scripts/agent_repo.py init
python scripts/agent_repo.py doctor --online
python scripts/agent_repo.py start feat/<任务-日期-席位> <仓库外的新worktree路径>
```

`start` 读取云端 develop 的确定版本并输出完整基线 SHA，不移动本机 develop。
进入新 worktree，设置 `PYTHONPATH` 指向该目录的 `src` 并读回 `superran.__file__`。
按 AUTHOR 合同实现、自测、本地提交，再上传候选：

```bash
python scripts/agent_repo.py submit --base <开工记录的完整基线SHA>
```

把输出的分支、完整候选 SHA、基线、风险档、验证命令与结论交给独立合并位。
证据须在候选里提交可分享的文本记录，或通过双方可访问的交付渠道提供；
不要只发作者电脑上的绝对路径。大数据、凭据和生成数据集不得作为代码提交。

合并位在另一会话、本机主仓库取回候选，命令核对的是分支**当前**完整 SHA：

```bash
python scripts/agent_repo.py fetch-candidate <云端任务分支> --sha <候选完整SHA>
```

输出 `review/<完整SHA>` 本地分支。先 fetch 云端 develop，核对与本机 develop 一致；
本机仅落后时可在干净主仓库 `git merge --ff-only origin/develop`，分叉则停止，保留双方历史。
按 MERGER 和 RISK 进行独立审核；红档保留 Physics / Integration 两个角色。
用同一候选与主干完整 SHA 完成 dry-run，然后由合并位执行：

```bash
python scripts/agent_repo.py merge review/<候选完整SHA> --sha <候选完整SHA> --base <已审核主干完整SHA>
python scripts/agent_repo.py publish --sha <本次合并完整SHA> --expected-remote <已审核主干完整SHA>
```

`merge` 调用原有 `merge_task.py`，完整测试、并发锁、现场保护均保留；成功后核对两个父提交，
在本机 Git 公共目录生成回执。`publish` 核对回执、云端基线和服务器协商的旧 SHA，
只做普通 fast-forward push 并读回。候选、主干或工作区变化均停止，不强推。
网络失败时本地合并仍保留，核对云端状态后重试 publish，不重做合并、不 reset。

本地回执是可审计的防误操作记录，**不是数字签名，也不能证明 Reviewer 独立性**。
真正的跨电脑权限由 Gitea 负责：成员只推任务分支，独立合并账号才可推 develop；
作者不得持有合并账号凭据。服务器目前不执行仿真或 CI，也不替代物理审核。
本地钩子禁用旧 `HUB_ALLOW_TRUNK_PUSH` 的云端绕行，main 仍走维护者单独发布流程。

## 首次迁移与恢复

迁移前保留所有本地 worktree、分支、GitHub 历史和已有数据。`init` 仅切换远端与钩子，
不会推送、删除文件或改变索引。旧 origin 若是 GitHub 则改名为 github-archive，并禁用其 push URL。
未知远端或已有同名历史入口冲突会拒绝自动修改，先核对归属。

云端确为空且维护者授权首次迁移时，最后一次独立合并可使用 `merge ... --initial`；
发布为 `publish --sha <合并完整SHA> --expected-remote EMPTY --initial`。
该路径仍要求刚执行的完整合并闸门回执，不把现有任意提交直接当成已验版本。
首次推送会携带 develop 可达的完整提交历史，旧未合分支继续保留在原本机；
需要团队协作的候选另按 submit 上传。main 和 tags 不随之自动更新。

从尚无云客户端的旧版本切换时，先由独立合并位显式运行候选的
`verify_cloud_workflow.py`，再用旧 `merge_task.py` 和完整 SHA 合入本工具分支。
旧闸门读取的是试合前配置，不能把新增加的测试命令当作已经自动运行。
工具进入主仓库后，再对后续独立审核的集成候选使用 `merge --initial` 形成发布回执。
若没有后续候选，则保留本地已合状态，不能为发布而伪造空提交、手写回执或改主目录文件。

如果回执丢失，不手写回执或手设放行变量。保留现有提交，交由独立合并位重新规划并验证，
不得通过强推或覆盖主干找回所谓一致状态。服务器备份与恢复由维护者单独维护。

## 什么时候写

改动进主线之后。不是每个 PR 都要写——只有**外面的人需要理解**的才写：

- 改了物理机制、KPI 口径、算法行为 → **要写**
- 纯工具、纯排版、纯注释 → 不用写

## 写什么

`docs/changes/YYYYMMDD-<模块>-<一句话>.md`，模板见 `docs/changes/_TEMPLATE.md`。
五节固定，用无线语言写，**不要贴代码**：

1. 改了什么物理机制
2. 为什么
3. 证据
4. **没证明什么** —— 这一节不许写"无"
5. 影响哪些 KPI

然后在 `CHANGELOG.md` 顶部加一行摘要，链到那份文档。

## 一条铁律

**引用性能数字时，必须写清它是在哪个基线上测的。**

基线一变，数字就作废。实测过一次：EDF 的结论数字在 AMC 链修正后全部失效，
收益从 +2.3 pp 缩到 +0.45 pp。写文档时如果引用了旧数字而不标基线，
后面的人会当成事实继续引用。

改动进主线时如果发现某个已有数字被作废了，**全仓搜一遍同步掉，并保留旧值作对照**。

## main 与 develop

`develop` 是开发主线；日常本地合并只推进它。`main` 是独立发布引用，只有维护者
明确发起发布并选定完整 SHA 后才更新，不在合并后自动同步。

`merge_task.py` 本身不执行 fetch/push；跨电脑传输与云端发布由上述客户端负责。
离线可实现和自测，网络恢复后再提交候选；没有云端读回时不能宣称团队主干已更新。

## 内网 Agent 回路（单向）

它拿到的是快照，给回来的 patch 大概率打不上当前 HEAD。所以：

- 内网 Agent **只产出问题清单**：文件 + 行号 + 物理论据 + 最强反例
- **不接受 patch、不做冲突合并**
- 本地 Agent 按清单当作新任务重新实现，走工作位 → 独立合并位的本地流程

两种用法，各有各的合同：

| 场景 | 打包命令 | 它读哪份合同 |
|---|---|---|
| 通审整个仓库 | `scripts\superran_company_zip.ps1` | `.agents/COMPANY.md` |
| 审一次具体改动 | `scripts\superran_review_pack.ps1 <分支名>` | `.agents/COMPANY_REVIEW.md` |

报告放 `docs/inbox/`（已 gitignore，不进公开仓库）。
