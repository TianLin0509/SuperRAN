# superwireless

给 Agent 用的无线仿真信道供应站。

复用 [ChannelHub](https://github.com/wangxz0803-lab/ChannelHub_main) 的物理计算内核，
通过 MCP 向任意 Agent 提供**标准化的信道实例和配套物理测量量**，
让"验证一个无线通信 idea"从每次重写一套仿真，变成一句话拿到数据。

配套的 `channel-sim` skill 提供 superpowers 式工作流：
**收敛式头脑风暴 → 计划书 → 生成 → 取货代码**。

## 它解决什么

以前每验证一个 idea，都要从零搭一遍信道仿真——参数怎么配、数据怎么存、
PDP/PMI 怎么算，每个人写得都不一样，结果无法横向对比。

现在：

```
你：帮我验证一个 CSI 压缩的想法，先弄一批单小区 64T4R 的信道数据。

Agent：配好了 64T4R、273 RB、CDL-C、100 MHz。先对齐两件事：
       ① 你的方法要跟什么比？（Type II 码本 / SVD 上界 / 已发表方法）
       ② 用什么指标？（NMSE / 频谱效率损失 / BLER）

       另外三个选择会影响结论：
       ① 信道模型 CDL-C（非视距，压缩难度接近真实城区）——
          换 CDL-D 视距的话信道近似低秩，压缩率会偏乐观
       ② 要不要限定信噪比范围（默认不限）
       ③ 样本数 200

       建议同时跑 CDL-C / CDL-D 两组：视距下很多方法都显得特别好，
       两者都跑才知道结论是否普遍成立。

       还能调：移动速度、导频类型、TDD配比、载波频率、随机种子……
```

## 四条设计铁律

**一、不传数据，传取货代码。** 单个信道样本 `[T, RB, BS_ant, UE_ant]` 就有
数百 KB，序列化成 JSON 会膨胀到十几 MB——进不了任何模型的上下文。
所以 MCP 返回句柄、统计摘要和一段能直接跑的 Python：

```python
from superwireless import load
ds = load("ds_a3f21c")
H    = ds.h_true          # [N, T, RB, BS_ant, UE_ant] complex64
pmi  = ds.pmi(0)          # 38.214 Type I 码本索引 + 预编码矩阵
pdp  = ds.pdp(0)          # 未归一化功率 + 真实时延轴
aoa  = ds.paths().aoa_rad # 每条径的到达角
geo  = ds.geometry        # 路损、阴影、视距判定、多普勒
```

**二、给物理量，不给训练特征。** ChannelHub 的 `data/bridge.py` 会把这些量
归一化、截断、乘门控后打包成 16 个 MAE token——那是为表征学习服务的。
本项目**绕开它**，直接从信道现算物理量：PDP 不归一化、RSRP 不截断、
SRS 给完整协方差和全部特征值、PMI 给码本索引而非嵌入向量。

**三、生成与取货解耦。** 测量量都是从信道现算的，所以生成一次可以反复用
不同组合取货。用户半小时后说"我还想看 PMI"，秒级给新代码，不重跑仿真。

**四、先问实验设计，再问参数。** 参数配错重跑就行，实验设计错了整个结论作废。
所以 MCP 先问"跟什么比、用什么指标、想推广到哪"，再问信道模型和天线数。

## 交互方式

MCP 不替用户做决定，也不把几十个参数甩给 Agent。它按任务类型判断**哪几件事
一改结论就翻盘**，只问这几件，每件都附上"为什么值得问"。其余参数只报名字，
让用户知道边界在哪。用户说"随便"就直接用默认生成，并如实列出替他做的决定。

还会拦截**跑得出结果但结果没意义**的组合，例如：

| 组合 | 为什么拦 |
|---|---|
| 波束搜索 + TDL 模型 | TDL 没有每条径的角度，算法会输出看似正常的垃圾且不报错 |
| 信道预测 + 单时隙 | 样本间相互独立，没有可预测的时序结构 |
| 干扰协调 + 单小区 | 没有干扰源 |
| 射线追踪数据 + `ds.paths()` | 多径来自真实建筑几何，套用 CDL 剖面会得到与数据无关的假角度 |

## 安装

需要 Python ≥ 3.10。

```bash
git clone https://github.com/wangxz0803-lab/ChannelHub_main   # 物理内核
git clone <本仓库>
cd superwireless
pip install -e .
```

ChannelHub 会自动在本项目的同级/上级目录查找；放在别处就设环境变量
`SUPERWIRELESS_CHANNELHUB` 指向它。

射线追踪（可选，约 300 MB）：

```bash
pip install sionna-rt      # 连带装 mitsuba + drjit
```

不装也能用，`sw_capabilities` 会如实报告哪些引擎可用、不可用的缺什么。

### 注册到 Agent

```bash
claude mcp add superwireless -- python /path/to/superwireless/scripts/mcp_server.py
codex  mcp add superwireless -- python /path/to/superwireless/scripts/mcp_server.py
```

Skill（可选，提供完整工作流编排）：把 `skills/channel-sim/` 拷到
`~/.claude/skills/` 或 `~/.codex/skills/`。两者用同一套 `SKILL.md` 格式。

## MCP 工具

| 工具 | 作用 |
|---|---|
| `sw_capabilities` | 引擎可用性；不可用的如实报缺什么 |
| `sw_list_presets` | 场景预设与任务类型 |
| `sw_list_scenes` | 射线追踪场景清单 |
| `sw_plan` | 意图 → 实验设计问题 + 参数提案 + 对比组建议 + 常见陷阱 |
| `sw_revise` | 差分修正，用户只说改什么 |
| `sw_generate` | 生成数据集，返回句柄与统计摘要 |
| `sw_deliver` | 按自然语言点单生成取货代码 |
| `sw_describe_dataset` | 数据集维度、分布、可用字段 |
| `sw_list_datasets` | 已有数据集 |

## 可用测量量

| 名称 | 内容 |
|---|---|
| `channel` | 频域信道矩阵，理想与估计两版 |
| `pdp` | 时延功率谱：未归一化功率 + 真实时延轴 + RMS 时延扩展 |
| `paths` | 每条径的时延、功率、角度（**CDL 才有角度**）|
| `srs` | 完整空间协方差、全部特征值、每天线增益、波束域 RSRP |
| `pmi` | 38.214 Type I 码本索引 + 预编码矩阵 + 秩 |
| `rsrp` | 每天线信道增益（不截断）|
| `sinr` | 信噪比 / 信干比 / 信干噪比 |
| `capacity` | MIMO 容量与条件数 |
| `geometry` | 路损、阴影、3D 距离、视距判定、多普勒、UE 位置 |
| `topology` | 多小区 SSB 测量与干扰小区信道 |

## 支持的任务类型

CSI 压缩/反馈 · 波束管理 · 信道估计 · 预编码与码本 · 定位与时延估计 ·
干扰协调与调度 · 移动性切换 · 上下行互易性与 SRS 老化 · 信道预测 ·
链路自适应 · 信道表征学习

每类都有自己的实验设计问题、该问的参数、建议对比组和常见陷阱。
加新任务类型只改 `decisions.py`。

## 场景

**统计信道**（`internal_sim`，纯 numpy，约 0.2 秒/样本）：
单小区 64T4R / 单小区 4T4R 最小配置 / 7 站 21 扇区 / 19 站 57 扇区（38.901 Case 1）/ 室内工厂 InF

**射线追踪**（`sionna_rt`，约 2~6 秒/样本）：

| 内置（Sionna 自带） | 真实 OSM 建筑 |
|---|---|
| 慕尼黑老城、巴黎凯旋门、佛罗伦萨、旧金山 | 北京中关村、上海陆家嘴、深圳福田、广州天河、杭州钱江、重庆解放碑 |

真实 OSM 场景首次使用会自动准备资产（ChannelHub 里那些 PLY 是 VTK 导出的，
头部含 `obj_info` 字段，Mitsuba 3.8 不接受）。准备结果带缓存，**不修改 ChannelHub 原文件**。

加场景只改 `presets/presets.yaml`。

## 已知约束

- **信噪比不能直接设定**。它由路损、发射功率和撒点位置决定。要求特定区间时
  走拒绝采样，可能很慢或取不到；想整体调整，改发射功率或站间距更有效。
- **射线追踪拿不到逐径几何**。ChannelHub 尚未把 Sionna 的 `Paths` 对象导出到
  数据契约，所以射线追踪数据集调 `ds.paths()` 会报错（而不是返回假角度）。
  需要角度请用 CDL 模型。
- **QuaDRiGa 未纳入**，它需要 MATLAB/Octave 运行时。

## 测试

```bash
python tests/test_e2e.py         # 端到端 35 项
python tests/test_mcp_server.py  # MCP 全链路 20 项
python tests/test_raytracing.py  # 射线追踪与决策层 29 项
```

## 致谢

物理计算内核来自 [ChannelHub](https://github.com/wangxz0803-lab/ChannelHub_main)。
射线追踪基于 [Sionna RT](https://nvlabs.github.io/sionna/)。
工作流设计参考了 [superpowers](https://github.com/obra/superpowers) 的
brainstorm → plan → execute 三段式。

## License

MIT
