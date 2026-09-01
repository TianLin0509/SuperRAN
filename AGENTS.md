# SuperRAN Agent 入口

开始任何任务前，必须完整读取本仓库的 `CLAUDE.md`；它是无线物理、实现边界和测试映射的唯一开发规范。

团队 Agent 再按当前人的角色读取对应 Skill：

- 组员实现任务：`skills/superran-member-task/SKILL.md`
- 组长分工、状态、PR 审核与合并：`skills/superran-lead/SKILL.md`
- 仿真设计、数据生成或性能结论：`skills/channel-sim/SKILL.md`

固定协作边界：

- `develop` 是组员 PR 的唯一目标分支；`main` 只由组长单独发起发布。
- 组员 Agent 是 Author，不做项目管理、最终审核或合并。
- 组长 Agent 不修改组员分支；它只给审核意见，并且只有组长明确批准当前完整 HEAD SHA 后才能合并。
- 标题含 `[REHEARSAL]` 的体验 PR 永不合并，只用于验证流程。
