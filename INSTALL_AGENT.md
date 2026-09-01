# SuperRAN 安装说明（写给 AI Agent 看的）

> 这份文档的读者是 **AI coding agent**，不是人。用户把它交给你，意思是
> **"你来装，装完告诉我能不能用"**。
>
> 人看的版本在 `SETUP.html`（组成、架构、三种用法）和 `README.md`。

## 你的任务

把 superran 装到用户机器上并验证可用。产出是一句话：能用 / 不能用及原因。

**不要问用户能自己查出来的事**（Python 在哪、是什么系统、装没装 numpy）——
跑命令去查。只在下面明确标了 **【问用户】** 的地方停下来问。

---

## 第 0 步：判断走哪条路

```
有 wheels/ 目录  →  离线模式（这是从 .zip 解出来的包）
没有             →  联网模式
```

离线模式再看包型 —— **先读 `bundle-manifest.json`**：

| `bundle_kind` | `self_contained` | 含义 |
|---|---|---|
| `full` | true | wheels 里有 numpy/scipy 与构建后端，**全新 venv 也能装** |
| `thin` | false | 没有 numpy/scipy，**要求目标机器已有科学计算栈** |
| `nodeps` | false | 只有源码，依赖得另外想办法 |

manifest 里的 `requires_preinstalled` 直接列出需要目标机器自备什么。
拿不到 manifest 就自己看：`ls wheels/ | grep -i -E "numpy|scipy"` 有没有命中。

三条路的差别只在"依赖从哪来"和"ChannelHub 从哪来"，其余相同。

---

## 第 1 步：确定 Python 解释器

要求 **≥ 3.10**。

```bash
python --version
python -c "import sys; print(sys.executable)"
```

**把 `sys.executable` 的输出记下来，后面每一步都用这个绝对路径**，不要用
`python` 这个名字。原因见第 5 步——这是最常见的安装失败原因。

Windows 上如果 `python` 打不开（跳到应用商店），试 `py -3`、
`where python`、或去 `%LOCALAPPDATA%\Programs\Python\` 下找。

版本低于 3.10 时停下来，**【问用户】** 有没有更新的 Python 或者要不要建 venv。

---

## 第 2 步：放好两个仓库

superran 依赖 **ChannelHub** 作为物理内核。两者建议放成兄弟目录：

```
<某个目录>/
├── ChannelHub_main/      ← 物理内核
└── superran/        ← 本项目
```

### 联网模式

```bash
git clone https://github.com/wangxz0803-lab/ChannelHub_main  # 候选源，仍必须过下面的合同体检
git clone https://github.com/TianLin0509/superran
```

公开候选源可能落后于 SuperRAN 当前物理接口；能 clone、能 import 都不算通过。
若团队已有批准的 ChannelHub，优先使用组长给出的版本。

### 离线模式

superran 就是你手上这份。ChannelHub **不在离线包里**——它没有开源许可证，
不能随包转发。

先让 Agent 在当前项目目录、同级项目目录和团队约定的代码目录中查找。
**不得为了找依赖递归扫描整个 C: / D: 或根目录。**

找不到就停下来 **【问用户】**：ChannelHub 源码在哪？需要一个目录，
里面有 `src/msg_embedding/data/contract.py`。没有它整个项目跑不起来。

### 让 superran 找到 ChannelHub

放成兄弟目录时会自动发现。放在别处就设环境变量：

```bash
export SUPERRAN_CHANNELHUB=/abs/path/to/ChannelHub_main          # macOS/Linux
[Environment]::SetEnvironmentVariable("SUPERRAN_CHANNELHUB","D:\path\to\ChannelHub_main","User")  # Windows
```

**验证：**

```bash
<PYTHON> -c "import sys; sys.path.insert(0,'src'); from superran import channelhub; print(channelhub.channelhub_root())"
```

打印出的路径下必须存在 `src/msg_embedding/data/contract.py`。随后还必须通过当前物理接口合同：

```bash
<PYTHON> -c "import sys; sys.path.insert(0,'src'); from superran import channelhub as ch; r=ch.probe_source_contract(); print(r.as_dict()); raise SystemExit(0 if r.compatible else 1)"
```

只有 `compatible: true` 才能继续。任何 blocker（尤其 `array_port_order`）都必须停止并向
组长索取批准的 ChannelHub；不允许把不兼容源当成可选告警。

---

## 第 3 步：装依赖

六个：`numpy scipy pydantic pyyaml structlog mcp`。

### 联网模式

```bash
cd superran
<PYTHON> -m pip install -e .
```

### 离线模式 · full 包

```bash
cd superran
<PYTHON> -m pip install --no-index --find-links wheels -e .
```

已实测：全新 venv（只有 pip）里全程 `--no-index` 装成功，35 个包含
superran 自身。

### 离线模式 · thin 包

**不要加 `-e .`。** 可编辑安装会起一个隔离的构建环境去拉 `setuptools`，
thin 包里没有它，`--no-index` 下也无处可拉——而且报错只说
`pip subprocess to install build dependencies did not run successfully`，
完全看不出缺的是什么。改成直接装依赖：

```bash
cd superran
<PYTHON> -m pip install --no-index --find-links wheels mcp pydantic pyyaml structlog
```

thin 包**不含 numpy/scipy**，靠目标机器自带。装完先验：

```bash
<PYTHON> -c "import numpy, scipy; print(numpy.__version__, scipy.__version__)"
```

报缺就停下来 **【问用户】** 怎么补（受控内网 pip 源 / 另找 wheel /
换成 full 包重打）。**不要自己去联网装**——离线环境里那也不会成功。

### 关于 `pip install -e .` 到底要不要做

**可以不做。** `scripts/mcp_server.py` 和 `sr_deliver` 生成的取货代码都会自己把
`src/` 加进 `sys.path`。它的真正作用只有两个：把依赖装齐、让任意目录下能
`import superran`。

依赖已经齐了又不想动 site-packages 时跳过是可以的——
**但要在最终报告里说清楚你跳过了，以及后果**。

**验证依赖：**

```bash
<PYTHON> -c "import numpy,scipy,pydantic,yaml,structlog,mcp; print('deps ok')"
```

---

## 第 4 步：可选装射线追踪

```bash
<PYTHON> -m pip install sionna-rt      # 约 300 MB，连带 mitsuba + drjit
```

**不装完全不影响主功能**，统计信道照常跑。离线模式下 `wheels/` 里通常不含它
（体积太大且强平台相关）。

不要为了装它去降级用户已有的 numpy/scipy/torch。实测正常安装不会降级；
如果 pip 提示要降级，停下来 **【问用户】**。

---

## 第 5 步：注册 MCP

**用第 1 步记下的 Python 绝对路径，和 `scripts/mcp_server.py` 的绝对路径。**

```bash
# Claude Code
claude mcp add superran -- <PYTHON绝对路径> <仓库绝对路径>/scripts/mcp_server.py

# Codex
codex mcp add superran -- <PYTHON绝对路径> <仓库绝对路径>/scripts/mcp_server.py
```

Codex 也可以直接写 `~/.codex/config.toml`：

```toml
[mcp_servers.superran]
command = 'C:\path\to\python.exe'
args = ['C:\path\to\superran\scripts\mcp_server.py']
```

其它支持 MCP 的 agent：stdio 传输，命令就是上面那两个绝对路径。

> **这一步用相对路径或裸 `python` 是最常见的失败原因。**
> agent 起子进程时的 PATH 和你当前 shell 不一定一样，表现为
> "MCP 连上了但一调用就说缺依赖"——那是连到了另一个 Python。

**验证：**

```bash
claude mcp list        # 应看到 superran ... ✔ Connected
```

连不上时先手动跑一次，stderr 会打印预热结果：

```bash
<PYTHON> <仓库>/scripts/mcp_server.py
# 期望第一行类似：[superran] warmup ok 3.2s
# 然后它会等 stdin，Ctrl-C 退出即可
```

---

## 第 6 步：装 Skill

仓库是 Skill 的版本真源。不要从聊天记录复制旧版本；按角色运行统一安装器：

```bash
# 组员：仿真门 + 实现任务问答/开发/PR
<PYTHON> scripts/install_agent_skills.py --role member

# 组长：再增加分任务、状态、PR 审核与合并
<PYTHON> scripts/install_agent_skills.py --role lead
```

安装器会复制并逐树 SHA-256 对账：

- `channel-sim`：仿真设计与门 1/2/3；
- `superran-member-task`：组员的需求问答、实现、测试与 PR；
- `superran-lead`：仅组长使用的分工、状态、审核与 SHA 绑定合并。

当前会话仍要直接完整读取仓库里的对应 `SKILL.md`；新会话会从用户 Skill 目录自动发现。
MCP 即使不装 Skill 也能调用，但团队开发流程不得省略角色 Skill。

---

## 第 7 步：验证

```bash
cd <仓库>
<PYTHON> scripts/run_test_matrix.py --tier quick
<PYTHON> scripts/run_test_matrix.py --tier physics
<PYTHON> scripts/run_test_matrix.py --tier full
```

测试文件和运行时检查数会随实现扩展，不手写总项数。以矩阵 JSON 终态、逐文件退出码和
日志为准；失败、超时、缺文件或 UTF-8 解码失败都会给非零退出。

没装 sionna-rt 时 `test_raytracing.py` 的射线追踪实跑段会自动跳过并说明原因，
**这不算失败**。

**没有 ChannelHub 时所有测试都会失败**，这是正常的——它是物理内核。
这时至少要能验证降级行为正确：

```bash
<PYTHON> -c "
from superran import channelhub as ch
for c in ch.probe_capabilities():
    print(c.name, c.available, c.missing)
"
# 期望：三个引擎都列出，都是 False，missing 里含 'ChannelHub'
```

MCP 冒烟（无 ChannelHub 也应当能起）：

```bash
<PYTHON> -c "
import asyncio; from superran import server
print('tools:', len(asyncio.run(server.mcp.list_tools())), 'mcp major:', server.MCP_MAJOR)
"
# 期望：tools: 35
```

---

## 第 8 步：报告

跟用户说清楚这几件事，**不要只说"装好了"**：

1. **能不能用** —— MCP 是否 Connected、测试过了几项
2. **装在哪** —— superran 和 ChannelHub 的绝对路径、用的哪个 Python
3. **哪些没装** —— 射线追踪装没装、skill 装没装、`pip install -e .` 做没做
4. **下一步** —— 给一句可以直接说的话：
   > "用 superran 生成一批单小区 64T4R 的信道，我要验证一个 CSI 压缩的想法。"

---

## 失败对照表

| 症状 | 原因 | 怎么办 |
|---|---|---|
| MCP 显示 Failed / 连不上 | Python 路径不对，或依赖没装齐 | 手动跑 `<PYTHON> scripts/mcp_server.py` 看 stderr |
| MCP 连上但一调用就报缺依赖 | agent 子进程用的不是你 pip install 的那个 Python | 用 python.exe 绝对路径重新注册 |
| 报找不到 ChannelHub | 目录不在自动查找范围内 | 设 `SUPERRAN_CHANNELHUB`，指到含 `src/msg_embedding/data/contract.py` 的目录 |
| `sr_generate` 卡住不返回 | 历史上是 scipy 在工作线程里的 import 死锁 | 已修（启动预热）。仍遇到就设 `SUPERRAN_DEBUG=1`，会开 faulthandler 打栈到 stderr |
| 测试报 `ModuleNotFoundError: superran` | 没在仓库根目录跑 | `cd` 到仓库根再跑；测试脚本自己会加 `src/` 到 path |
| 射线追踪报 `invalid PLY header` | 中国城市场景的 PLY 是 VTK 导出的，Mitsuba 3.8 解析不了 | 已自动处理（复制到缓存后清理头部）。加新场景时若再遇到就是这个原因 |
| pip 要降级 numpy/scipy/torch | 装 sionna-rt 时的版本冲突 | 停下来问用户。正常情况不会降级 |
| Windows 上 `python` 跳到应用商店 | 系统别名占用 | 用 `py -3` 或 `%LOCALAPPDATA%\Programs\Python\` 下的绝对路径 |

---

## 卸载

```bash
claude mcp remove superran
codex  mcp remove superran
rm -rf ~/.codex/skills/channel-sim ~/.codex/skills/superran-member-task ~/.codex/skills/superran-lead
<PYTHON> -m pip uninstall superran
# 生成的数据在仓库的 artifacts/ 下，删仓库即可全清
```

---

## 装完之后：这东西是干什么的

一句话：**给 Agent 用的无线仿真信道供应站，面向蒙特卡洛验证。**

用户提一个无线算法优化思路，它给出可信的信道场景实例、配套物理观察量，
以及 SINR / 谱效的完整评价链路，并用三道门拦住站不住的结论。

- 35 个 MCP 工具，从探能力、问需求、生成、取货，到 BLER/TDD AMC、3GPP 校准、三道评审门
- **数据永远不进对话** —— MCP 只回句柄、统计摘要和可运行的取货代码
- 详见 `SETUP.html`（组成与用法）、`CAPABILITIES.html`（能力边界）、
  `SHOWCASE.html`（实测演示）
