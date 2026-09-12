# 需求澄清方法的来源与适配

**什么时候读**：维护本 skill、设计问答案例或判断为何采用当前流程时。
调研日期 2026-09-12；以下为原始仓库的固定提交链接。采用思想重述与领域适配，
不是完整复制第三方 skill，也不自动执行这些库的 hooks、安装或审批流程。

| 来源 | 借鉴的动作 | 无线仿真中的落点 | 没有照搬的内容 |
|---|---|---|---|
| [grilling（grill-me 的当前上游）](https://github.com/mattpocock/skills/blob/3cca18b368ae95cdbdebbff572ccafa662551015/skills/productivity/grilling/SKILL.md) | 建决策依赖树，只问前提已清的问题；每答一次重算缺口；可查事实自己查 | 先明确容量/有限到达业务，再选速率/时延口径；不重复问已答条件 | 不无休止穷举分支，不把软件实现选择交给通信用户 |
| [grill-with-docs](https://github.com/mattpocock/skills/blob/3cca18b368ae95cdbdebbff572ccafa662551015/skills/engineering/grill-with-docs/SKILL.md) 与其调用的 [domain-modeling](https://github.com/mattpocock/skills/blob/3cca18b368ae95cdbdebbff572ccafa662551015/skills/engineering/domain-modeling/SKILL.md) | 前者是组合入口；后者检验模糊术语、举边界案例、当场写术语及决定 | “体验”先区分窗口速率/忙期吞吐/完成时延；复用已有记录并核对实际行为 | 不把仓库架构审问搬进仿真对话，术语表不掺实现细节 |
| [superpowers brainstorming](https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/skills/brainstorming/SKILL.md) | 先理解目标约束，给推荐与取舍，按 spike/bounded/architectural 范围调节设计深度 | 概念解释、数据交付、探索试点、正式比较采用不同完成条件 | 不要求每批 PDP 都经历软件设计、计划和实现仪式 |
| [Spec Kit clarify](https://github.com/github/spec-kit/blob/d848fb4e18f44640ad6b42e60a280551ee90cdce/templates/commands/clarify.md) | 内部覆盖扫描，按影响×不确定性选问题；逐次写回对应条款、删除冲突、验证可判定性 | 覆盖物理条件、指标分母、随机性、预算；回答同步到生成/系统参数 | 不照搬五题或一次一题模板，不运行软件项目 hooks |

## 原有基础与最短改进路径

SuperRAN 已有任务槽位、草稿与修订、场景预设、配置说明、生成前预注册、数据体检和
结果配对。缺的不是再起一个对话框架，而是把这些记录连成“人已确认 → 实际执行 →
结果解释”的可核对链条。

本轮先用手册落实：任务分类、问答选择、实验约定、执行前后核对、按证据解释。
运行时下一步优先做**约定与实际参数的自动差异检查**，其次是**从已有实验继续/改单项**，
再做**围绕原问题生成解释与异常清单**。均不需要新增无线仿真物理功能。

验收使用自然表述与多轮改变条件的案例，看是否保留通信条件、减少无效追问、避免
无证据结论；工具单测通过或手册词语齐全，都不足以证明真人交互已经可靠。
