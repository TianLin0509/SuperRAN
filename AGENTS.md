# SuperRAN Agent 入口

开始任何任务前，必须完整读取本仓库的 `CLAUDE.md`；它是无线物理、实现边界和测试映射的唯一开发规范。

团队 Agent 再按当前人的角色读取对应 Skill：

- 正式实现任务（普通组员或组长本人）：`skills/superran-member-task/SKILL.md`
- 组长分工、状态、PR 审核与合并：`skills/superran-lead/SKILL.md`
- 仿真设计、数据生成或性能结论：`skills/channel-sim/SKILL.md`

固定协作边界：

- `develop` 是所有实现 PR 的唯一目标分支；`main` 只由组长单独发起发布。
- 具体实现任务的 Agent 是 Author；人可以是普通组员，也可以是组长本人。Author Session 不做项目管理、最终审核或合并。
- 组长 Agent 不修改 Author 分支；它只给审核意见，并且只有组长明确批准当前完整 HEAD SHA 后才能合并。
- 标题含 `[REHEARSAL]` 的体验 PR 永不合并，只用于验证流程。
- GitHub 身份只决定向 Fork 还是上游 topic branch 推送，不能决定任务模式。正式实现必须显式使用 `TEAM_MODE: FORMAL`；演练必须显式使用 `TEAM_MODE: REHEARSAL`，不得因为登录账号是 Owner 自动进入演练。
- 组长本人可以是正式 PR Author；其 PR 必须由另一个全新 Agent Session 在隔离 worktree 中审核，同一 Author Session 不得自写自审。
- 任一 Author PR 提交或更新后，Author Agent 必须生成绑定当前远端 PR HEAD 的本地交互式改动说明 HTML，并把 PR 链接与 HTML 文件一起交给组长；报告不是审核通过证据，默认不提交到公开仓库。

与人交流时采用“双层表达”：保留准确技术术语，同时紧跟一句白话解释；关键物理概念再给一个贴近当前任务的小例子。例子只帮助理解，不能冒充代码事实、测试证据或性能结论。首次使用缩写时展开全称；实现前用“我理解为……，不等于……”复述边界。专家已明确理解时不要反复教学。
