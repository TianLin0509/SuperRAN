# 阿里云 Windows ECS 部署交接

当前周报服务实际使用 Windows ECS + Caddy + 独立 Python 3.13 环境，而不是旧文档中的 Docker 部署。
本应用匹配该基础设施，使用独立目录 `C:\SuperRANTeam`、独立数据目录和回环端口 18770。
**本文件记录部署步骤；实际部署回执另存于本机私有云部署目录。**

## 准备

将本目录代码（排除缓存、测试输出和任何私有文件）传到 `C:\SuperRANTeam\app`。
确认端口18770未被占用；若占用，选择新端口并同步下文配置，不停止占用者。
在服务器为本服务建立独立虚拟环境，然后安装 `requirements-lock.txt`。

```powershell
python -m venv C:\SuperRANTeam\venv
C:\SuperRANTeam\venv\Scripts\python.exe -m pip install -r C:\SuperRANTeam\app\requirements-lock.txt
C:\SuperRANTeam\venv\Scripts\python.exe C:\SuperRANTeam\app\admin.py --data C:\SuperRANTeam\data member --name '负责人' --role admin --output C:\SuperRANTeam\private\leader.json --site https://ai.lt-stockpartner.tech/superran
```

目录 ACL：服务账户能读写 data、读取程序；private 仅管理员可访问，逐成员安全交付配置。
不得沿用周报 connector 密钥；不得将 private 映射到静态网站。

## 新进程与路由

新建仅运行本应用的 `SuperRANTeamAPI` 计划任务（服务器启动触发，失败自动重启，无时长上限）：

```text
程序：C:\SuperRANTeam\venv\Scripts\python.exe
参数：C:\SuperRANTeam\app\server.py --data C:\SuperRANTeam\data --origin https://ai.lt-stockpartner.tech --base-path /superran --port 18770
```

Caddy 先备份当前配置，再在现有域名块内添加：

```caddyfile
redir /superran /superran/ 308
handle /superran/* {
    request_body {
        max_size 100KB
    }
    reverse_proxy 127.0.0.1:18770
}
```

本服务路由保留 `/superran` 前缀：使用 `handle`，**不要使用会剥离前缀的 `handle_path`**。
原 `/weekly/` 和其他路由保持原样。Caddy validate 通过后平滑 reload；不重启周报API、Hub或连接器。
首次发布前记录原周报 health/state 的只读响应，发布后复核周报仍可访问。

## 验收、回退、备份

1. 公网 `/superran/api/health` 返回 ok；访问首页和记录API无需登录；伪造 agent 凭证返回401。
2. 用两个临时验收成员验证上报、跨浏览器刷新、人工保护、断网补传，明确标记验收摘要。
3. 在一位实际同事电脑验证出站 HTTPS 和 agent 项目指令，再交付给其他人。
4. 若回退，只移除新增路由并停本服务任务；保留数据库及 outbox，不能回退密钥或删除工作记录。
5. 使用 `admin.py --data ... backup --output <不存在的新路径>` 做 SQLite 在线一致性备份，
   不仅复制运行中的 sqlite 主文件。私有接入文件单独加密备份。

用户已明确授权免登录公开访问及直接上云。各公司电脑的网络连通性仍需逐机验证。
