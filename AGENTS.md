# SuperRAN Agent 入口

开始任何任务前，必须完整读取本仓库的 `CLAUDE.md`；它是无线物理、实现边界和测试映射的唯一开发规范。

当前入口是 AI 群聊「开发」场景，一个群聊一项任务：

- 工作位：`.agents/AUTHOR.md`，在自己的 worktree 实现、自测、本地提交。
- 合并位：`.agents/MERGER.md`，独立审核并运行绑定任务与主干完整 SHA 的闸门。
- 风险与知识：`.agents/RISK.md`、`.agents/TESTING.md`、`.agents/OUTPUT.md`。
- 仿真设计、数据生成或性能结论：`skills/channel-sim/SKILL.md`。
- 共享工作记录：配置了 `SUPERRAN_REPORT_CONFIG` 时读取 `.agents/TEAM_SITE.md`，在工作进展变化时主动上报；它不代替开发、自测和合并流程。

`develop` 是唯一开发主线；`main` 是独立发布引用，只有维护者明确发起发布时才更新。
日常不推送、不建 PR；远端同步另按 `.agents/SYNC.md` 执行。工作位不得审核或合并自己写的分支。
合并位只有亲验 PASS 后才可执行本地合并，任务或主干 SHA 变化就重审。

`skills/superran-lead/`、`skills/superran-member-task/` 和 `docs/team/` 是历史流程档案，
保留供兼容与追溯；不得把其 FORMAL/REHEARSAL、Fork、PR 指令混入当前开发场景。

与人交流时采用“双层表达”：保留准确技术术语，同时紧跟一句白话解释；关键物理概念再给一个贴近当前任务的小例子。例子只帮助理解，不能冒充代码事实、测试证据或性能结论。首次使用缩写时展开全称；实现前用“我理解为……，不等于……”复述边界。专家已明确理解时不要反复教学。
