# 共享工作记录：agent 接入约定

仅在成员已配置 `SUPERRAN_REPORT_CONFIG`（私有 JSON 文件路径）时启用。
成员配置表示授权把本项 SuperRAN 工作的简短进展发往配置中的团队站点。
首次接入先核对 `site` 是负责人指定的站点；不得上传源代码、会话全文、密钥、原始数据或未获授权的公司材料。

## 何时主动更新

1. 开始工作：先查本任务交接中的工作编号；有编号就 `attach`，没有才 `start`。
2. 产生实质进展、遇到阻塞、自测结束、交付或修订时 `update`。不要每个工具调用都上报。
3. 每次工作开始和交付前 `flush`。若成员运行 `watch`，退出 agent 后仍可补传已有摘要；它不会自己生成摘要。
4. 不得把“写完代码”改写成“已验证／已合并”。对仿真结论保留场景、口径、验证边界。

命令入口：`apps/team_site/report.py`，仅需要 Python 标准库。
PowerShell 示例：

```powershell
python apps/team_site/report.py --config $env:SUPERRAN_REPORT_CONFIG start --title '完善 CSI 时延场景' --progress '正在核对反馈时序和已有验证。' --source codex
python apps/team_site/report.py --config $env:SUPERRAN_REPORT_CONFIG update --work-id '<返回的 UUID>' --progress '已完成实现和本地自测，等待独立审核；尚未合并。' --status '进行中'
python apps/team_site/report.py --config $env:SUPERRAN_REPORT_CONFIG flush
```

命令返回的 `work_id` 必须立刻写入本任务工作笔记或交接材料，继续工作沿用。
创建前也可自己生成并先保存 UUID，再传 `start --work-id`，避免中途退出丢失编号。
公司 agent 用 `--source company-agent`，个人 Codex 用 `--source codex`；两者是更新来源，不是新角色。
多行文字用 UTF-8 文件与 `--progress-file`，避免 shell 引号误执行。

## 同步规则

- 返回 `ok: true` 才能说已同步；返回 `protected` 表示这些字段保留人工内容，不能说 agent 文本已生效。
- 网络错误返回码 2：摘要已排队；下次 `flush` 重试原请求，不能重新 `start`。
- 并发冲突返回码 2：先 `show --work-id` 阅读当前内容和历史，再决定是否仍需该更新。
  核对后 `resolve --work-id … --reviewed-revision N`；使用新版本重试原摘要，人工保护依然有效。
  不得写循环自动读取版本然后盲目 `resolve`。
- 切换电脑：传工作编号，使用该成员在新电脑的配置 `attach --work-id …` 后继续更新；不要复制正在使用的 outbox。
- 同电脑多个工作区共用配置旁的 outbox，有进程间锁。跨电脑同时修改同一条记录会冲突，应核对后续传。
- Agent 不能修改别人记录、解除人工保护、执行审核或合并。网站本身不做真实性审核。

这里只依赖 agent 在工作节点主动调用命令，不声称所有产品都有通用结束钩子。
公司工具若不读取 AGENTS.md，需将本段接入约定放到该工具的项目指令中；公司网络到站点的连通性须在该电脑实测。
