# 对外文档与批次记录

> 推分支、开 PR 现在是 Author 的收尾动作，见 `.agents/AUTHOR.md`。
> 审 PR 与执行合并是 Reviewer 的事，见 `.agents/REVIEWER.md`。
> 这份文件只管**对外能读懂的改动记录**。

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

`develop` 是主线。`main` 跟着 `develop` 走，保持两者一致——
Reviewer 合完 PR 之后会顺手做这一步。

## 内网 Agent 回路（单向）

它拿到的是快照，给回来的 patch 大概率打不上当前 HEAD。所以：

- 内网 Agent **只产出问题清单**：文件 + 行号 + 物理论据 + 最强反例
- **不接受 patch、不做冲突合并**
- 本地 Agent 按清单当作新任务重新实现，走正常的 Author → PR → Reviewer 流程

两种用法，各有各的合同：

| 场景 | 打包命令 | 它读哪份合同 |
|---|---|---|
| 通审整个仓库 | `scripts\superran_company_zip.ps1` | `.agents/COMPANY.md` |
| 审一次具体改动 | `scripts\superran_review_pack.ps1 <分支名>` | `.agents/COMPANY_REVIEW.md` |

报告放 `docs/inbox/`（已 gitignore，不进公开仓库）。
