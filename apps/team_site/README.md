# SuperRAN 共享工作记录

沿用周报站的轻量共享表格：agent 主动上报，同事可修改，全员查看。
一项工作只有一条记录：标题、负责人、进行中/受阻/已完成、进展、可选成果链接。
SQLite 历史与请求编号是内部可靠性数据，不要求成员维护额外实体。

## 与周报站的关系

已阅读参考项目 `C:\AIWork\20260923-team-weekly-app` 的正式前端、FastAPI 后端、共享编辑/历史机制、当前公网部署说明。

| 周报站现状 | 本实现 |
| --- | --- |
| 浅绿白底共享表格，历史按需展开 | 沿用视觉语言，工作记录按最近更新排序 |
| 人在浏览器修改，4 秒轮询 | agent 上报和人修改共用服务端记录，4 秒同步 |
| SQLite 事务、版本号、浏览器草稿 | 沿用；另加逐字段人工保护、稳定请求编号与离线补传 |
| 当前公网免登录，任何人可编辑 | 公开查看与共同编辑，无登录或访问码；agent 上报单独鉴权 |
| 人/周两栏、轮值、海报 | 工作持续更新；无周次、轮值、海报或新的审批流程 |
| Windows ECS + Caddy `/weekly/` | 独立进程、独立数据库、保留前缀的 `/superran/` 路由 |

不依赖周报私有配置、名单、数据库、生图连接器或 AI Hub。只有实际摘要进入站点；不读会话。

正式入口：https://ai.lt-stockpartner.tech/superran/ 。免登录公开编辑；历史中的人工修改统一记为“网页编辑（未署名）”，不冒认具体成员。

## 本机运行

若只想试用虚构记录，可从仓库根目录运行：

```powershell
python apps/team_site/preview.py --directory C:\VibeData\SuperRAN\20260925-team-site-preview
```

脚本启动专用回环服务，返回预览地址，打开即可使用。
预览中所有成员和记录均明确标注演示；不能视为真实团队进度。

在本目录执行；建议独立虚拟环境。已验证依赖版本见 `requirements-lock.txt`。

```powershell
python -m pip install -r requirements-lock.txt
python admin.py --data C:\VibeData\SuperRAN\site member --name '负责人' --role admin --output C:\VibeData\SuperRAN\private\leader.json --site http://127.0.0.1:18770/superran
python server.py --data C:\VibeData\SuperRAN\site
```

打开 `http://127.0.0.1:18770/superran/` 即可共同查看与编辑，不需要访问码。
私有文件中的 `agent_token` 只用于本机 reporter，网页不索取它。每位成员分别用 `admin.py member` 创建。
配置只发本人，不放仓库或发布包。文件所在目录应限制为本人及管理员可读。
轮换凭证用同名成员 `--rotate` 和新的输出文件；旧 agent 密钥立即失效。

```powershell
$env:SUPERRAN_REPORT_CONFIG = 'C:\VibeData\SuperRAN\private\leader.json'
python report.py --config $env:SUPERRAN_REPORT_CONFIG start --title '联调工作记录' --progress '已启动，验证共享上报。'
python report.py --config $env:SUPERRAN_REPORT_CONFIG update --work-id '<返回编号>' --progress '完成本地验证，尚未部署。' --status '进行中'
python report.py --config $env:SUPERRAN_REPORT_CONFIG watch
```

配置环境变量后仓库 AGENTS.md 会引导 agent 读取 `.agents/TEAM_SITE.md`。
`watch` 可选，只补传已有摘要，不替 agent 生成进展。可用成员已有常驻工具运行它。
首版不自动安装全机计划任务。公司侧指令接入和网络访问需逐机验证。

## 可靠性与边界

- 服务端在同一事务中写当前记录、历史与回执；请求编号+内容指纹防重复。
- 使用最新版本才能写。两个 agent 同时修改不会静默覆盖；409 冲突需核对。
- 人修改的字段自动保护。agent 可继续更新其他字段；被保护内容保留在历史里。
- 人选“交回 agent”才解除保护，且不会立刻采用之前被挡住的旧摘要。
- outbox 在发网络请求前落盘完整请求；回执丢失后重发同一编号。每条工作更新按顺序发送。
- 队列前端冲突会暂停本机整个队列，避免越过未知状态；界面/CLI 明确提示。用 `show`、`resolve` 核对后继续。
- 新记录 UUID 在本机生成并由服务器确认；不存在的编号仅在 revision=0 且提供标题时创建。
- 网页有草稿恢复、冲突核对与同步中断提示。历史展示最近 100 条，数据库保留全量。
- 单实例适合小组使用；未做大规模压测、主从容灾、自动清理历史或跨云高可用。
- 网站记录不改变独立审核和合并闸门。不得将“已完成”解读成“已合并”或仿真结论真实有效。

## 验证

```powershell
python -m pytest apps/team_site/test_site.py -q
node --check apps/team_site/web.js
python apps/team_site/verify_browser.py
```

以上从仓库根目录执行。浏览器验证启动专用临时服务和虚构成员，使用独立 Edge 会话；不触碰周报站或正式数据。
无线核心无改动，因此不把这些检查称为 SuperRAN 仿真全量测试。
