# Task dispatch

Use this only when the lead wants to assign or shape a concrete member implementation
task. Project administration is internal; the member should see only wireless intent.

Inspect the current repository first. Ask at most one compact round for missing lead
decisions:

- desired capability or defect;
- priority and dependency on another active PR;
- physical behavior/information boundary that must be preserved;
- success scenario and strongest negative control;
- explicit non-goal.

Give a recommendation when information is missing. Then show:

Before the card, restate the boundary as “我理解要实现的是……，不等于……”. For any
non-obvious term, keep the standard term and add one plain-language sentence plus one
task-specific example. Mark code facts, lead decisions, recommendations, and open
assumptions separately.

| 组长任务卡 | 内容 |
|---|---|
| 任务名称 | |
| 给哪位组员 | 未指定时写“待组长分配” |
| 一句话目标 | |
| 必守物理边界 | |
| 验收与反向对照 | |
| 不做什么 | |
| 与当前 PR 的依赖/冲突 | 无 / concise item |

After the lead confirms, return one plain message to send the member:

> 请打开组员快速开始页，把里面唯一的 Prompt 发给你的 Agent。Agent 问“想完善
> 什么”时回答：<one-sentence task>. 后续按无线专业判断回答即可。

Create or update a GitHub Issue only when the lead explicitly asks to record the task.
Do not assign source files or implementation details to the human member; their Agent
must discover those from the current repository.
