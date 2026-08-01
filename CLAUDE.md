# superwireless 开发规范

## 项目定位

复用 ChannelHub 的物理计算内核，通过 MCP 给任意 Agent 提供标准化的信道实例
与物理测量量。**只借 ChannelHub 的算法，不借它的产品外壳。**

## 环境

- Python ≥ 3.10，需要 numpy / scipy / pydantic v2 / pyyaml / structlog / mcp
- ChannelHub 源码由 `channelhub.channelhub_root()` 自动发现（同级或上级目录），
  环境变量 `SUPERWIRELESS_CHANNELHUB` 可覆盖
- 射线追踪需 `pip install sionna-rt`（连带 mitsuba + drjit，约 300 MB）；
  实测不会降级 numpy/scipy/torch
- **ChannelHub 自己的 CLAUDE.md 写的 `D:\MSG\.venv312` 是源工程路径，多数机器上不存在，不要照抄**

## 测试

```bash
python tests/test_e2e.py         # 端到端 39 项
python tests/test_mcp_server.py  # MCP 全链路 37 项
python tests/test_raytracing.py  # 射线追踪与决策层 40 项
python tests/test_linklevel.py   # 谱效、可信度、物理层、IRC 62 项
python tests/test_gates.py       # 校准、标准表、三道门、统计判决 86 项
python tests/test_results.py     # 外部算法结果契约、预注册 80 项
python tests/test_linkadapt.py   # 链路自适应、吞吐、并行生成 135 项
python tests/test_mumimo.py      # MU-MIMO 配对、预编码、rank/SU-MU 自适应、单码字 57 项
python tests/test_system.py      # 系统级：话务、PF 调度、HARQ、体验速率口径、守恒 39 项
python tests/test_interference.py # IoT、测量域、场景预设、探测模式、说明书回传、文档计数 262 项
```

改动 `measure.py` / `generate.py` / `plan.py` / `decisions.py` / `scenes.py`
后前三个都要跑；改动 `linklevel.py` / `validate.py` / `calibration.py` /
`gates.py` / `spec38901.py` 要跑 test_linklevel + test_gates；
改动 `results.py` / `analysis.py` / `loader.py` 要跑 test_results；
改动 `interference.py` / `scenario.py` / `presets.yaml` / `spec.py` / `bridge.py`
要跑 test_interference（说明书与回传桥都在它第 9 节）；
改动 `mumimo.py` 要跑 test_mumimo；改动 `system.py` 要跑 test_system；
改动 `algorithms.py` 要跑 test_interference（算法页签在它第 9.10 节）。

## 与 ChannelHub 的边界

| ChannelHub 部分 | 怎么对待 |
|---|---|
| `src/msg_embedding/` 物理内核 | 复用，当普通库调用 |
| `platform/backend`、`platform/frontend` | 不用 |
| 任务队列 / 数据库 / 数据集管理 | 不用，直接落盘 |
| `data/bridge.py` 特征桥接 | **绕开** |

绕开 bridge 的原因：它的输出是为 MAE token 服务的——PDP 除以峰值归一化
（`bridge.py:184`）、RSRP 截断到 [-160,-60]（`:195`）、SRS 只取前 4 个特征向量
（`:262`）、PMI 乘了基于 CQI 的门控（`extractor.py:232`）。拿这些做通用仿真会
得到看似正常但错误的结果。

## 三条不可动摇的约定

1. **不传数据**。MCP 只回句柄、摘要、取货代码。
2. **给物理量**。不归一化、不截断、不门控。单位标在函数文档里。
3. **生成与取货解耦**。测量量从信道现算，改主意重新 deliver，不重跑仿真。

## 踩过的坑（删之前先想清楚）

### scipy 子模块必须在主线程预热

`channelhub.warmup()` 里那串 `import scipy.interpolate / special / io / spatial / stats`
**不是冗余**。ChannelHub 是惰性 import 这些子模块的（用到才导），而 MCP 把工具
调用派到工作线程执行——在工作线程里首次加载 scipy 的 C 扩展会撞上 import 死锁。

症状极其隐蔽：请求永久无响应、无异常、无日志，看起来像仿真跑不完。
用 `faulthandler.dump_traceback_later` 抓栈才定位到卡在
`scipy/interpolate/_fitpack_impl.py` 的 `create_module`。

调试时设 `SUPERWIRELESS_DEBUG=1`，会开 faulthandler 并打点到 stderr。

### 配对的有效性靠样本 ID，统计查不出错位

`results.check_pairable` 逐个按序比 sample ID，不只比长度。**这不是多余的谨慎**：
把两个臂的 ID 顺序打乱一个位置，配对检验算出来的 p 值可以**一模一样**
（实测 1.63e-11 → 1.63e-11），因为统计只看数值数组，根本不知道第 i 个数
对应哪个信道实例。错位是统计层面**不可观测**的，只能靠 ID 契约拦。

所以 `register` 默认自动生成 ID，两臂都用默认就一定对齐；只有显式传 `ids=`
（跳过部分样本）时才可能出错，那时校验就是最后一道防线。

同理 `register` 拒绝含 nan/inf 的 values：配对时非有限值会被整行丢掉，
两臂样本数悄悄变少，而 p 值照样算得出来。

### 外部结果的 CSI 口径只能靠声明

内置 `compare_arms` 能查两臂用的是 `h_true` 还是 `h_est`，因为预编码是它自己跑的。
**外部结果是用户在自己进程里算的，MCP 看不到里面用了哪个。** 所以门 2 只能查
`method_metadata` 里的声明，查不到就给 warn 并说清"这条得你自己保证"——
不能假装查过了。

这个不对称是设计选择而非缺陷：让 MCP 去 exec 用户代码换取可观测性，
是把它从"数据供应站"变成任意代码执行面，代价远大于收益。

### 预注册只在生成前绑定才有意义

`sw_generate(prereg_id=...)` 把主指标写进 `summary.json`。**事后补绑没有价值**
——预注册的全部意义就是"这是看数据之前写下的"。所以没有"给已有数据集补绑"
的接口，也不要加。

未绑定时 `classify` 返回 `unregistered` 而**不是** `primary`：没登记过就不能
声称主指标是事先定的。这一条别放松成"默认 primary"。

### QAM 互信息的 sigma 定义差一倍就是 3 dB

`linkadapt._pam_mi` 里 `sigma = 1/sqrt(γ)`，**不是** `1/sqrt(2γ)`。
约定是复符号 `E|x|²=1`、复噪声 `E|n|²=1/γ`；折到实维、星座归一化到单位能量后，
噪声方差正好是 `1/γ`。写错会整体多给 3 dB，而且**看起来完全正常**——
曲线形状对、饱和值对，只是位置平移。

抓它的办法是低信噪比处对香农：约束容量在 γ→0 时必须与 `log2(1+γ)` 重合。
这条自检在 `test_linkadapt` 第 1 节里。

### BLER 有分析模型和用户曲线两条后端，别混成一种证据

表 1/2 使用 `BlerModel`（有限码长形状 + 可配实现损失，没有 3GPP 参考曲线
兜底）；MCS/CQI 表逐字录自 38.214、TBS 按 §5.1.3.2 复刻、QAM 约束容量精确
求积。**这三样不能和分析 BLER 混为一谈。**

表 3 使用用户提供的 `company_20b_256qam`：28 档 MCS、56 条 NewTx/ReTx 曲线、
1824 个点。原始数据在 `bler_data_20b.py`，查询/哈希/单调性/插值在
`bler_curves.py`。它比分析模型更贴近该接收机配置，但**仍不是 3GPP 标准曲线**。
数据所有者确认：源标签 `Es/No` 就是经典 MMSE 接收机的 SINR；TB/CB、块长、
信道模型、MIMO 层数和译码器细节暂不参数化。曲线范围外只能保守钳位，不能外推。

表 3 的 HARQ：首传用 NewTx；失败后用 ReTx。源数据每档只有一条 ReTx 曲线，
多次重传会复用它，结果必须保留 `harq_model=newtx_then_retx_curve_reused`。
表 3 没有 CQI 曲线，所以 CQI 仍用 38.214 Table 2 + 分析 BLER，并通过
`cqi_source` 明示，不能谎称 CQI 也来自公司曲线。

TDD AMC 已由 `tdd_mcs_adaptation` / `Dataset.tdd_mcs` / `sw_tdd_mcs` 实现：
`CQI index → 按谱效映射初始 MCS → 该 MCS 的 NewTx 目标 BLER SINR 门限
→ + BF Gain → 按 SINR 重映射 MCS → + OLLA MCS offset → floor → 最终 MCS`。
CQI 是 PMI 权测得的 pre-BF 值。BF Gain 逐 RB、逐流计算为同一信道、CSI、rank、
功率、噪声、干扰与经典 MMSE 接收机下 `SINR_SVD - SINR_PMI`；用户 SINR 对全部
RB×流在 dB 域做算术平均。OLLA 的单位是连续 MCS 档位，不是 dB；正值更激进，
最终结果严格向下取整并钳位到 0..27。默认 10% 首传 BLER 下 ACK +0.1、NACK -0.9，
反馈只作用于下一调度时刻。所有中间量与口径必须保留在结果中，不能只返回最终 MCS。

`anchor_check` 的单调性**只能在同一调制阶数内部要求**：标准表在调制切换点上
故意让 SE 重叠（MCS9 QPSK SE=1.3262 → MCS10 16QAM SE=1.3281，但码率只有 0.332），
门限小幅回落是正确物理。整体判单调会把这两点误报成失败。

### 多进程必须先压 BLAS 线程数

`_chunk_worker` 里在 import numpy **之前**把 `OMP_NUM_THREADS` 等设成 1。
不设的话每个 worker 各开满核数的线程：20 worker × 20 线程抢 20 个核，
上下文切换吃掉全部收益——实测 10 进程只有 1.34 倍加速，设了才拿到应有的加速比。

并行分块靠**给每块不同的 `seed`**。`ue_seed_offset` 实测对撒点没有影响
（同 offset 与不同 offset 给出逐位相同的路损），只有 `seed` 真正换随机流。
因此并行与串行不是同一批样本，统计等价但逐样本不同，摘要的 `parallel` 块必须写明。

多进程在某些宿主里起不来（Windows spawn 需要可导入的 `__main__`，REPL 和
`python -c` 里没有）。`generate` 会**降级串行并把原因记进摘要**，不让整次生成失败——
但也不能静默，否则用户会纳闷为什么没变快。

### 门 3 的判决必须显式说清用哪个检验

`PairedResult` 曾有个叫 `significant` 的属性，只看配对 t 检验；而文档写着
"两检验冲突时以 Wilcoxon 为准"。门 3 用的是前者，于是 t 显著、Wilcoxon 不显著的
样本被直接放行——**承诺的判据和代码实际用的判据是两回事**，这比判据宽松更危险。

现在 `significant` 已删除，改为 `t_significant` / `wilcoxon_significant` /
`tests_agree` / `decision_test` / `decision_p_value` / `decision_significant`。
判决以 Wilcoxon 为准（谱效差值分布常偏态，t 的正态假设不成立、小样本偏乐观），
Wilcoxon 算不出来才退回 t。`statement` 必须写出用的是哪个检验。

回归样本记在 `tests/test_gates.py` 第 6.5 节，别删：
`d = [-0.0811, 1.5561, 0.5308, 1.9896, 3.2605, -0.1125, 1.6908, -0.2045]`
（n=8，t p=0.044 显著、Wilcoxon p=0.109 不显著）。

### 零方差差值要分两种情况

`paired_compare` 里 `se <= _EPS` 时不能一律 `p = 0`：差值恒为 0（两臂完全相同）
应当 p=1，差值恒为非零常数才是 p=0。早先一律写 0，于是"自己跟自己比"得到
p=0（最显著），只是碰巧被"置信区间不跨零"拦住——靠运气拦住的不算拦住。
另外 `float("inf") * np.sign(0)` 是 nan，还会抛 RuntimeWarning。

### 子进程编码要在子进程侧统一，不能只在父进程解码

`test_e2e.py` 起子进程跑取货代码。只在父进程写 `encoding="utf-8",
errors="replace"` 是不够的：Windows 默认 GBK 时子进程按 GBK 输出中文，
父进程按 UTF-8 解码得到乱码，`errors="replace"` 把它换成 U+FFFD，
**测试照样"通过"**，等父进程把 U+FFFD 打到 GBK 控制台时才炸，且报的是父进程的错。

正确做法：给子进程设 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`，父进程用
`errors="strict"`，并断言输出里没有 U+FFFD、且预期中文短语在。

### 引擎清单长度不能随环境变化

`probe_capabilities()` 早先在找不到 ChannelHub 时只返回 `internal_sim` 一条，
于是调用方写 `engines["sionna_rt"]` 会 KeyError，看起来像工具坏了。
清单必须恒为三条，变的只是 `available` 与 `missing`。

### 离线包默认必须是完整包

`pip install -e .` 会起隔离构建环境去装 `build-system.requires`，离线时这一步
也得有轮子。早先的轻量包不含 `setuptools`，在全新 venv 里直接失败，而 pip 只说
"install build dependencies did not run successfully"，**完全看不出缺什么**。

现在默认打完整包（含 numpy/scipy + 构建后端），轻量包必须显式 `--thin`，
包型写进文件名和 `bundle-manifest.json` 的 `bundle_kind` / `self_contained` /
`requires_preinstalled`。改打包脚本前先想清楚：**接收方拿到包时没有网络，
所有"顺手 pip 一下"的假设都不成立。**

### mcp 1.x 与 2.x 的服务端类换了位置

`mcp 2.0` 删掉了整个 `mcp.server.fastmcp` 子模块，`FastMCP` 改名叫
`mcp.server.mcpserver.MCPServer`。两者 `.tool()` 与 `.run(transport=...)` 签名一致，
`server.py` 里用 try/except 兼容，`MCP_MAJOR` 记录当前版本。

不做这层兼容的后果：今天新装的用户 `pip install mcp` 拿到 2.x，服务端在 import
阶段就 `ModuleNotFoundError`，而且报的是 mcp 的错，看起来像用户环境问题。
**本机装的是 1.27，所以本地测试永远发现不了**——这个 bug 是打离线包时在干净
venv 里才暴露出来的。改 `server.py` 的导入前先想清楚两个版本都要能跑。

### stdio 传输下 stdout 是 JSON-RPC 通道

任何调试输出只能走 stderr。`_dbg()` 已经这么做了，别图省事用 `print()`。

### num_samples 必须能被 num_ues 整除

ChannelHub 的硬约束（`internal_sim.py:1103`）。`generate._align_to_ues()` 负责
向上取整并在够数后截断，不要让这个约束泄漏给用户。

### YAML 里的科学计数法

`bandwidth_hz: 100.0e6` 会被 YAML 1.1 解析成**字符串**（需要 `100.0e+6` 才是浮点）。
`presets.yaml` 一律写完整数字 `100000000.0`。

### 干扰小区信道默认不保存

ChannelHub 的 `measurements.interferer_channels` 默认 `False`——多小区场景下
干扰只体现在 SINR 里，`h_interferers` 会是 `None`。干扰协调类任务必须显式打开
（见 `decisions.py` 里 interference 任务的 `config_hints`）。代价是数据量按
干扰小区数翻倍。

### 路损对标时必须复刻仿真器的公式选择逻辑

`internal_sim` 选路损公式的规则有两层，比对时错一层就会看到几十 dB 的假偏差：

* `scenario` 已是 `*_LOS` 时，**所有链路**都用 LOS 公式（不看 `is_los`）；
* `scenario` 是 `*_NLOS` 时，逐链路按 `is_los` 在 NLOS/LOS 公式间切换。

另外容差要按**独立位置数**算而不是样本数——同一个 UE 的多个样本共用一次
阴影抽样，30 个样本可能只有 4 个独立位置。

### 38.901 路损公式的两个"看起来像 bug 其实不是"

1. **LOS 公式会低于自由空间**。断点内 `PL_LOS − FSPL = 2·log10(d) − 4.45`，
   d < 168 m 时必然为负，最多低 4.45 dB。它是拟合式，不是严格物理下界。
2. **含阴影的实测路损低于自由空间是常态**。阴影零均值双向扰动，视距场景下
   过半样本低于自由空间很正常。判据只能用**去阴影后的公式值**。

### 时延扩展的频域估计有固有误差

可观测最大时延是 `1/(12·SCS)`（与 RB 数无关），时延分辨率是它除以 RB 数。
尾部截断使估计偏小、粗分辨率使估计偏大，两者部分抵消：实测同一 CDL-C 剖面
20 MHz 比值 1.00、100 MHz 比值 0.80。所以这项只作数量级检查。

### 信噪比不是输入参数

它由路损、发射功率、撒点位置共同决定。`snr_range_dB` 默认 `None`（不筛选），
指定时走拒绝采样。默认值曾设成 `[0, 25]`，结果很多场景实际 SINR 中位数 35 dB，
样本全被拒——这是设计错误，别改回去。

### 射线追踪的 PLY 资产与 Mitsuba 版本

ChannelHub 里中国城市场景的 PLY 是 VTK 导出的，头部有 `obj_info` 行，
Mitsuba 3.8 的解析器直接报错。`scenes.prepare_scene()` 会复制到 artifacts
缓存再清理，**不动 ChannelHub 原文件**。加新城市场景时如果报
"invalid PLY header"，就是这个原因。

### ChannelHub 的 CDL-A/B/C 角度表与 38.901 不符

三张表都标着 "TS 38.901 Table 7.7.1-x"，**时延与功率两列是对的，角度列从中段起不是**。
CDL-C 有 23/24 簇有出入、占总功率 93.8%，按 Annex A.1 算 ASA 偏 14.5°、ASD 偏 7.1°。

`spec38901.py` 放了一份逐字核对过的标准表（**手抄 + 从 PDF 机械解析两条独立路径
对过账**），`apply_spec_tables()` 在 `channelhub._ensure_path()` 里灌回仿真器——
替换的是 `get_cdl_profile` 读的 `_PROFILES` 字典，**不改 ChannelHub 一行源码**。

挂在 `_ensure_path` 而不是只挂 `warmup` 是有意的：任何取 ChannelHub 东西的路径
都会先过它，包括直接调 `cdl_profile`、跑测试、REPL 里试。只挂 `warmup` 会出现
"跑 MCP 时信道是标准的、跑测试时不是"这种最难查的不一致。

`SUPERWIRELESS_CDL_SPEC=0` 可关闭。CDL-D/E 未覆盖——表结构含 `Cluster PAS` 列、
首簇拆成镜面与 Laplacian 两行，没逐字核对过的表宁可不放。

### 默认阵列是 1 驱 3，不是 64 个独立阵元

真实 AAU：**64 个 RF 端口（8H x 4V x 2pol），每端口固定驱动垂直相邻 3 个阵子，
共 192 个物理阵子；水平 0.5λ、垂直 0.67λ**（RF 端口垂直相位中心 2.01λ > λ，
垂直方向有栅瓣）。载波 n41 2.6 GHz / 30 kHz / 100 MHz / **272 RB**
（17 RBG x 16 RB；38.104 标准表是 273，口径不同），终端默认 **4R 下行**，
仿真粒度到 RB 为止。

ChannelHub 的 `phy_sim/effective_array.py` 就是照这套硬件写的（模块文档
"Target AAU" 一节逐条对得上），但默认没启用——`antenna_model_mode` 默认
`legacy_64`，把 64 个端口当成 64 个**独立**阵元、间距一律 0.5λ。
`hardware.apply_array_defaults()` 现在在面板是 8x4x2 时自动切到
`effective_subarray`。

**实测差距（同 seed、单小区 30 样本）**：

| | 真实 AAU | legacy_64 |
|---|---|---|
| SVD 谱效 | 28.20 | 33.23 |
| 吞吐均值 | 1055.5 Mbps | 1337.5 Mbps |
| 边缘用户 | 582.4 Mbps | 940.0 Mbps |

**legacy 把吞吐高估 27%、边缘用户高估 61%**——2026-07-31 之前生成的所有
谱效/吞吐数字都偏乐观。`validate.check_antenna_model` 会把它标出来。

三条边界：

* `h_serving_true` 与 legacy 的**相对差 4.03**，完全是另一个信道。
* `effective_subarray` 与 `physical_reference`（真跑 192 阵子再用 F 投影）
  相对差 **4.8e-7**，快路径复现了参考路径，放心用快的。
* **几何 SINR / SIR / IoT 逐位不变**。ChannelHub 的几何 SINR 走
  `_system_sinr.py` 自己那套简化模型（水平 0.5λ DFT 码本、垂直完全平坦），
  **不读阵列模型**。所以换阵列只改信道矩阵，不改干扰画像——
  预设里的 `expect`（IoT/SINR/路损）不用重测。

垂直 0.67λ 是用户实测纠正过的值（早期按 0.5λ 算，全盘产物失真），
见记忆 `project_reconfig_mimo_sim` 方法论教训第 4 条。**别改回 0.5。**

1 驱 3 是**这一款 AAU 的硬件事实，不是通用规律**，所以只对 8x4x2 生效；
16T/256T 之类的面板保持 legacy。

### 说明书文件名要唯一到秒以下

`spec.write_spec` 的文件名早先是 `spec-{秒级时间戳}.html`。同一秒里连着出两份
（先看预设、再看草稿）后一份会**直接覆盖前一份且不报错**——用户拿到的路径
指向的是别人的图。实测就这么丢过一份：高铁的线性拓扑被单小区的覆盖成了
一个孤零零的点，还一度以为是 SVG 画错了。

数据集句柄本身唯一，其余情况补 6 位随机后缀。`test_interference` 第 9.8 节
有回归：同一秒生成两份，断言文件名不同且内容各自独立。

### 内嵌 JS 里别用反斜杠转义

说明书的调参面板要往 payload 里塞换行。写成 `
` 得穿过**两层 f-string**
（`_interactive` 返回的 f-string 再被 `render_html` 的 f-string 嵌进去），
实测每次都塌成**真换行**，落进 JS 单引号字符串里就是硬 SyntaxError——
整段脚本不执行、页面点不动，而 HTML 结构检查完全看不出来。

现在用 `const NL=String.fromCharCode(10)`，一个转义都不写。
`test_interference` 有回归：逐行数引号，跨行的字符串字面量直接判失败；
另外断言脚本里不残留反斜杠转义的换行。

**验证内嵌 JS 只能靠真的跑一遍**：`node --check` 抽出来的脚本，
或者在浏览器里看 console。光看 Python 源码看不出来。

### SVG 的 <style> 是文档级的

一页上有两张内联 SVG 时，**后注入的 `<style>` 会盖掉前一张的同名 class**——
它们共用同一份文档样式表，不是各自私有。

调参面板的预览图定义 `.sec{fill:url(#sg2)}`，直接把静态拓扑图的 `.sec` 也改成
引用它自己那个渐变；而那个渐变藏在隐藏的 tab 里渲染不出来，于是**静态图的扇区
填充整个消失**。图还在、线还在，就是没有底色，看起来像"渐变写错了"。

跨图共享的东西（渐变、marker）用**写在元素上的 presentation attribute**
（`fill="url(#sg)"`），别走 class。id 也要各用各的（`sg` / `sg2`）。

### MMSE 与 IRC 的区别全在 R_n，不在公式

两者用**同一个**后处理 SINR 公式。`receiver="irc"` 把干扰的完整空间协方差
`R_uu` 放进 `R_n`；`receiver="mmse"` 只取 `tr(R_uu)/N_rx` 摊成白噪声。
所以 IRC 的增益**只能**来自 `R_uu` 的非白性——白干扰下两者必须逐位重合，
`test_linklevel` 第 10 节有这条反向自检。不写它的话，实现里多算了什么
（比如把干扰功率漏掉一半）会表现成"IRC 就是更好"，看不出来。

**这是一次语义变更。** 2026-08-01 之前 `receiver="mmse"` 传了 `h_interferers`
时用的是完整有色协方差——那其实就是 IRC，只是叫 MMSE。实测同配置
26.15（新 mmse）vs 28.52（=旧 mmse=新 irc）bit/s/Hz，**旧数字偏乐观 2.37**。
之前所有"MMSE 基线"的谱效都要按新口径重算才能和 IRC 比。

### ChannelHub 的干扰小区信道是秩 1 的

单个干扰小区的 `[BS, UE]` 切片奇异值是 `[1, 0, 0, 0]`——实测 96 个抽样
σ₂/σ₁ 中位 4.0e-8、最大 5.9e-8，而服务小区是满秩（归一奇异值
1 / 0.63 / 0.32 / 0.093）。

后果有两个方向：

* **IRC 处在最有利工况。** 3 个干扰小区、4 根接收天线，刚好全部零陷得掉。
  实测 IRC 增益 +2.37 bit/s/Hz（约 9%）。真实干扰不会这么干净，
  **这个数偏乐观**，引用时必须带上 `interference_rank`。
* **`interference_model="precoded"` 目前是个空转旋钮。** 干扰已经是秩 1 了，
  再过一次主特征波束还是同一个子空间，与 `isotropic` 逐位相同。
  留着它是为了信道模型哪天变了能立刻切；**但别声称它现在有用**。

`effective_rank(R_uu)` 会把有效秩报出来，逼近 `N_rx` 时 IRC 增益必然趋近 0。

### ChannelHub 早就有三档信道估计器，只是没暴露

`msg_embedding/channel_est/` 提供 `ideal` / `ls_linear` / `ls_mmse`
（LS + 线性插值 / LS + 频域 MMSE 用指数 PDP 先验 + 线性时域插值），
`internal_sim` 还多两档 `ls_hop_concat` / `ls_hop_sequential`（SRS 跳频，仅上行）。
默认 `ls_linear`。**配置是整个 dict 直通的，所以这个键一直可用**，
只是 superwireless 从没提过它——2026-08-01 之前的数据集全部默默走了默认档。

实测（company_64t4r_multicell，24 样本）：

| | 干扰 UE=0 | 干扰 UE=16 |
|---|---|---|
| `ls_linear` DL NMSE | −7.14 dB | −10.96 dB |
| `ls_mmse` DL NMSE | −9.79 dB | **−14.54 dB** |

**导频越挤，MMSE 赢得越多**（0.7 → 3.6 dB）——它靠 PDP 先验把被污染的部分压掉。

### num_interfering_ues 是上行旋钮，下行不读它

`_interference_estimation.py:604` 的 DL 分支按**小区**遍历
（`elif K_minus_1 > 0 and direction == "DL"`），全程不读 `num_interfering_ues`；
UL 分支才按 UE 数展开。实测 `ul_sir_dB` 49.90 → 9.43 → −2.61（0/4/16 个 UE），
而 `dl_sir_dB` 在 4 与 16 之间只差 0.13 dB（噪声）。

所以 `srs_congested` 这类"高测量干扰场景"**本质是上行场景**。
项目已明确只做下行，这些预设要么改用别的机制（下行 CSI-RS 复用同一图案的小区数），
要么标成上行专用。

### MU 的预编码矩阵只能表示方向，功率要单独给

`mu_precoder` 返回 `(W, p)`：`W` 逐列单位范数，`p` 是显式的功率分配。
**合成一个全局标量会退化成信道求逆功控。** ZF 满足 `H̃W = c·I`，
再用一个标量把 `tr(WW^H)` 归一，等于强行把所有用户的接收电平拉平——
弱用户为了达到同一电平吃掉大部分发射功率，这是公认的劣解。

症状极隐蔽：实测四个用户等效信道范数 12.0 / 11.7 / 10.7 / 7.2，
谱效却**一模一样都是 11.482**，Jain 公平度恒等于 **1.000000**，
看起来像"MU 天生公平"。修正后是 11.71 / 11.77 / 11.29 / 11.31。
`test_mumimo` 第 4 节用"增益差 5 倍的用户"钉住这条。

总功率仍归一到 1，与 SU 口径一致。**别照搬 Sionna 的 `tr(GG^H)=K`**
（每流各一份功率），那会让 MU 相对 SU 白拿 K 倍，"MU 增益"里一大半是功率增益。

### MU-MIMO 在导频污染下掉一半

实测（64 端口 / 12 用户 / SUS 配 4 个 / ZF）：

| 场景 | CSI NMSE | 理想 CSI | 实际 CSI | 掉幅 |
|---|---|---|---|---|
| 单小区 | −31.1 dB | 46.07 | 45.69 | −0.8% |
| 多小区 `ls_linear` | −8.6 dB | 46.28 | **23.92** | **−48.3%** |
| 多小区 `ls_mmse` | −10.7 dB | 46.28 | 27.80 | −39.9% |

**用 `h_true` 做预编码得出的 MU 增益一律不可信**，它默认基站有上帝视角。
`h_users_for_precoding` 与评估信道是分开的两个参数，结果带 `csi_for_precoding`。

顺带：单小区的 `h_est` 精度是 −31 dB，**在那上面测 CSI 敏感性会什么都测不出来**——
必须用多小区（导频被污染）才看得见。

### "不配对"不一定更差

64 端口只服务 12 个用户时空间自由度富余，ZF 撑得住 12 条流——实测全选
（74.24）反而高于 SUS 选 4 个（46.07）。配对真正起作用是在用户数逼近端口数、
或 CSI 有误差时。别把这张表读成"配对没用"。

### CSS 的十六进制转义会贪心吃满 6 位

想在 `content:` 里用转义写"要你拍板"这类中文，会连撞两层：

```
CSS 侧：反斜杠 + 十六进制最多吃满 6 位。连着写两个转义时，
        第二个会把后面的十六进制字符一起吃进去，读成另一个码点。
Python 侧：反斜杠 + 数字 是合法的八进制转义，
        在非 raw 字符串里直接变成一个不可见控制字符。
```

两层一起错，渲染出来是一串乱码。**本条目自己就被这个 bug 咬过一次**——
所以上面用文字描述而不是贴原文，贴了会再坏一遍。

结论：CSS 里要中文就**直接写中文**，文件本来就是 UTF-8；
真要写转义就用 raw 字符串，并给每个转义补足 6 位或补一个空格作结束符。
这个只有在浏览器里看 `getComputedStyle(el,'::before').content` 才发现得了。

### 样本数不是用户数

数据集里 `num_samples` 个样本分布在 `num_ues` 个 UE 位置上（轮转分配）。
同一个 UE 的多个样本是**时间相关的**（多普勒就是从相邻样本的位移算的），
正好当这个 UE 的信道快照序列用。

把每个样本当成一个独立用户，小区里就凭空多出好几倍的人。实测 40 样本 / 10 UE：
每用户谱效从应有的 0.32 掉到 **0.08**，5% 边缘从 0.194 掉到 0.040——
**表现出来像"边缘用户被调度器饿死了"**，我当时的第一反应就是去查 PF 有没有把人饿死，
查了半天 outage 全是 0%。真因只是分母大了 4 倍。

`system.group_samples_by_ue()` 负责分组，`build_link_tables(num_ues=...)` 用它。
`sw_system_sim` 从 `ds.config["num_ues"]` 自动读。

### 回执要先于落盘发出，而且必须幂等

说明书页面点「应用到仿真」是一次 POST。`do_POST` 的顺序是**先进内存收件箱、
立刻回执并 flush、最后才落盘**——回执之前不做任何可能阻塞的 I/O。

顺序反了的后果实测过：验证脚本在 `await_submission` 一返回就退出，服务随之消失，
回执还没写完 socket 就断了。**agent 已经收到改动，页面却显示「回传失败，请改用复制」**。
用户照做再粘一遍，同一份改动就进来两次——最坏的一种不一致：两边都以为自己是对的。

所以还有幂等键：页面 fetch 失败会带**同一个 nonce** 重发一次，服务端见过就回
`dup: true` 不再入库。nonce 里带 `Date.now()`，所以用户**真的点两次**仍然算两次
（那是他的本意），只有内部自动重试是幂等的。

连不上时页面说的是"连不上 agent（服务可能已退出）。**先看对话框**，
它收到就会复述改动"——不是"失败了，请重发"。**结果不确定时就别断言结果。**

### 说明书默认不弹浏览器，只给地址

`write_spec` / `sw_spec_sheet` 的 `open_browser` 默认 **False**——把 `url`
给用户，他自己在浏览器或 AI HUB 里点开。**自动弹窗对一部分人是打断而不是便利**，
这是用户明确要求的（2026-08-01）。只有他说"帮我打开"时才传 True。

测试文件仍在 **import spec 之前**设 `SUPERWIRELESS_NO_BROWSER=1` 兜底，
防止哪天有人把默认改回去、或某条路径显式传了 True。

真要打开时走 `os.startfile` 而不是 `webbrowser.open`：后者在没有 DISPLAY 或被
沙箱限制时会挨个试一串命令行浏览器，其中有些会往 **stdout** 写东西，
而 stdio 传输下 stdout 是 JSON-RPC 通道，一个字节杂音就能让会话崩掉。

### 回传接口的白名单只能有一份

`bridge._sanitize` 的白名单来自 `spec.editable_keys()`，也就是页面上那些控件
（`_EDITABLE`）。**别在 bridge 里另抄一份**——抄了就会漂，然后出现"页面上能改、
POST 回来被拒"或者反过来"页面没有的键也能塞进去"。

页面是我们自己生成的，但**接口一旦开着就得按"任何人都能戳"来写**：只绑
127.0.0.1、URL 里带每进程随机 token、POST 也要带同一个 token、值必须是标量。
`test_interference` 第 9.9 节逐条验：越权键、嵌套值、错 token、未注册的说明书。

### 正则找 SVG 前要先剥掉 script

调参面板的 JS 用字符串拼 SVG，`<svg.*?</svg>` 会把那段 JS 也捞成一张"图"——
它带 `${...}` 模板占位，XML 解析必然失败，看起来像"生成的 SVG 坏了"。
测试里的 `_svgs()` 先 `re.sub` 掉 `<script>...</script>` 再找。

### f-string 里不能有反斜杠（Python < 3.12）

`f'<td>{"<span class='a'>x</span>" if c else "..."}</td>'` 在 3.12 之前是
语法错误。本项目要求 >= 3.10，**本机是 3.12 所以跑得通、别的机器直接崩**。
把这类片段提成局部变量再插值。ruff 会报 `invalid-syntax`，别忽略它。

### preset 里不要写死 bs_panel

写死会让天线覆盖失效：用户传 `bs_antenna="4T4R"` 时 `num_bs_tx_ant` 变成 4，
而 `bs_panel` 还是 `[8,4,2]`（64 口），两者矛盾，生成出来的 `BS_ant` 不是 4。
`test_mcp_server` 的"用户指定的 4T4R 生效"当场抓到过。

让 `_ensure_bs_panel` 从 `num_bs_tx_ant` 推：64 -> `[8,4,2]` 正是要的，
4 -> `[2,1,2]` 也自动落回 legacy（4T 没有 1 驱 3）。

### 没有 bs_panel 时干扰根本不进 SINR

`internal_sim.py:1436` 只在 `bs_panel is not None` 时才建 DFT 码本，
而几何 SINR 的前提是 `self._sinr_codebook is not None and K > 1`（`:2446`）。
拿不到码本就走兜底：`sir_dB = 49.9`（贴着 ±50 dB 契约边界的哨兵值）、
`sinr_dB = snr_db`。**这条路径不报错、不告警、不打日志。**

后果是配了 21 个小区、报出来的"SINR"是单小区热噪声 SNR。实测同配置修复前后
SINR 中位数 35.7 → 18.1 dB。`generate._ensure_bs_panel()` 现在由 `num_bs_tx_ant`
推导排布，`validate.check_interference_modeled()` 用两条判据复查
（SINR 是否逐点等于 SNR、SIR 是否恒为 49.9）。

2026-07-29 之前生成的数据集都没有这一步，它们的干扰类结论不成立。

### Type I 码本必须做秩自适应

38.214 的 Type I 反馈里 RI 和 PMI 是一起报的。`compute_precoder` 早期版本
把 type1 的秩硬定为 `max_rank`，在低秩信道上会输给 rank-1 的 DFT 波束——
总功率固定时多开的层每层分到的功率更少、SINR 更低。看起来像"码本不如单波束"，
其实是没做秩自适应。现在用与 SVD 同一套奇异值门限判据，两者才可比。

### 射线追踪数据不能用 CDL 剖面算角度

`sionna_rt` 数据源的 `channel_model` 字段只是它内部 TDL 回退路径的标记，
不代表信道生成方式。判断真伪看 `meta["channel_generation_mode"]`
（`sionna_rt` / `tdl_fallback`）。射线追踪数据的多径来自真实建筑几何，
套用 CDL 标准剖面会得到一组与数据无关的假角度——所以 `loader.paths()`
在这种数据上直接抛 `NotImplementedError`，不返回错误结果。

### IoT 不是 snr_dB 减 sinr_dB

教科书上 IoT = SNR/SINR 成立，但 ChannelHub 这两个字段**口径不同**：
`snr_dB`（`internal_sim.py:2441`）不含阵列增益、还额外减了 `10log10(RB)`；
几何 `sinr_dB` 的信号项含 `N_ant·|w^H a|^2`。273 RB、64 天线时差**约 40 dB**。

相减得到的数形状对、随场景变化的趋势也对，**只是整体偏了几十 dB**——
拿它当 IoT 会把中等干扰场景报成"极高干扰"。

正确算法只用同口径的两个量（都出自 `compute_geometry_sinr_single_ue`）：

    IoT = SIR / (SIR - SINR)      （线性域）

`validate.check_interference_modeled` 的 `measured` 因此**只报"相同/不同"
而不报差值**——放个数字进去就会有人把它读成干扰强度。

`num_slots_per_sample > 1` 时这个式子只是近似：`sinr_dB` 是各 slot 的 dB 均值，
`sir_dB` 取最后一个 slot。`interference_report` 会把 `iot_exact` 标成 false。

### 业务域与测量域是两个量，别混

`sir_dB` 是**业务域**几何 SIR（决定吞吐）；`ul_sir_dB` / `dl_sir_dB` 是
**测量域**导频 SIR（决定信道估计精度）。两者可以差十几个 dB。

实测一组对照：`srs_congested` 与 `srs_clean_reference` 只差导频配置，
业务域 IoT 差 0.06 dB（噪声），SRS 测量域 SIR 差 **17.9 dB**（-10.50 vs +7.37）。
只看业务域会认为这两个场景是同一件事。

测量域两列**只在 `link="BOTH"`（paired）时才产生**，单向链路的数据里根本没有。

### 上行几何 SIR 只能靠钩子取，且必须自检

`compute_geometry_sinr_single_ue` 同时算出上下行几何 SIR，但 `internal_sim`
只把下行那个存进 `ChannelSample.sir_dB`，上行的在函数返回后就丢了
（`ChannelSample.ul_sir_dB` 存的是测量域的，完全是另一个量）。

`interference.install_geometry_capture()` 包一层暂存，`take_ul_geometry_sir(sample)`
取走。**包装函数自带自检**：把暂存里的 `dl_sinr_avg` / `sir_dl_db` 与样本自己的
`sinr_dB` / `sir_dB` 对一遍，对不上就返回 nan——一次调用对一个样本的假设一旦破了
（比如 ChannelHub 改成批量算），宁可没有上行 IoT 也不给错的。

取值必须**紧跟在 `_SCALAR_SAMPLE_FIELDS` 循环之后**，隔一个样本就串了。

### 负载类旋钮在下行完全不起作用

几何模型里每个非服务小区都**无条件**贡献一份波束泄漏，`pdsch_load` 只决定
对几个波束取平均——均值不变，只是方差变小。实测 0.2 与 1.0 两组的
SINR / SIR / IoT **逐位相同**。

上行的 `pusch_load` 是有效的，但调度 UE 数是 `max(1, round(num_ues/K x load))`，
每小区 UE 数不足 2 时取整后恒为 1，同样无效。

后果很具体：拿它做"轻载 vs 满载"对比会生成两批一模一样的数据，然后得出
「负载不影响性能」。`decisions.check_guards` 现在会拦，`_LOAD_SWEEP` 也已经
从 `prb_utilization` 改成 `isd_m`。**真正能动下行 IoT 的实测值**：
ISD 100 m 38.3 dB / 200 m 24.9 / 500 m 4.4 / 1732 m 0.2；
tx_power +16 dB 则 IoT +16 dB（SIR 一动不动）；NF 与带宽按噪声底精确换算。

### 压 num_rb 探测场景是安全的，但 snr_dB 会撞夹逼

同一 seed 下 `num_rb` 取 273 / 24 / 12，几何量**逐位相同**：sinr / sir / 路损 /
距离 / 视距 / 多普勒 / UE 位置 / 上行几何 SIR 全部零差异。唯一变的是 `snr_dB`，
因为它的定义里显式带 `-10log10(RB)`，273→24 实测差 10.56 dB，
与 `10log10(273/24)=10.559` 吻合——**可以精确还原**。

**但修正只对没被夹逼的样本成立。** ChannelHub 把 `snr_dB` 夹到 ±50 dB，
探测口径下 snr 高了 10.4 dB，高信噪比场景会**先撞天花板再被减回去**，
得到一个看起来正常的假值。实测 InF 与密集城区两个完全不同的场景，
探测出的 SNR 都是 39.5 dB（= 49.9 - 10.4）。`scenario.probe` 现在剔除并计数。
SINR / SIR / IoT 不受影响，它们不含 `10log10(RB)` 项。

`num_ofdm_symbols` 同样可压，但**有一道悬崖，位置是 1**：14 降到 7 / 4 / 2 时
几何量逐位相同，降到 1 时 `sir_dB` 直接偏 16.1 dB。`PROBE_NUM_SYM` 取 4 而不是 2
是有意的——离悬崖两格，不贴着边站。这个旋钮**只对探测模式安全**，正式生成里
它实打实地改信道矩阵（14→7 时 `h_true` 相对差 2.5e-2，14→1 差 4.3），
因为存下来的单快照是在这些符号上平均出来的。所以它只在 `probe()` 里，不在
`generate()` 里。

发货参数（num_rb 24 + num_ofdm_symbols 4 + 关 SSB）实测 **11.5 倍速**
（2602 → 226 ms/样本，交错重测 3 轮取中位数，基准自身波动 17.7%）。

### 探测模式的正确性依赖 bs_panel

**没有 `bs_panel` 时，压 num_rb 会让 `sinr_dB` 平移 10.56 dB。** 原因是缺 panel
时 ChannelHub 建不出 DFT 码本，几何 SINR 整条路径被跳过、`sinr_dB` 退化成
`snr_dB`，而后者带 `-10log10(RB)`。

这个坑极其隐蔽：那时 `sir_dB` 是 49.9 哨兵、逐位相同，路损/距离/多普勒也逐位
相同，**只有 sinr_dB 一个字段偏**，看起来像探测模式本身不可靠。实际是配置缺
panel 导致连全量跑出来的"SINR"都不是真 SINR（见前面 bs_panel 那一条）。

`probe_config` 现在自己调 `_ensure_bs_panel`。写探测相关的测试时，
**对照组也必须补 panel**，否则比的是两个都错的东西。

### 多普勒需要每个 UE 至少 2 个样本

ChannelHub 的 `doppler_hz` 来自**同一个 UE 相邻样本之间的位移**，
`samples_per_ue == 1` 时没有位移可算，恒为 0。实测 `hst_350kmh`（21 个 UE）：
`num_samples=21` 报 **0.0 Hz**，`num_samples=42` 报 **817.94 Hz**。

一个 350 km/h 的场景探测出"多普勒 0"，任谁都会以为移动配置没生效。
`scenario.probe` 现在检测到配了移动（`ue_speed_kmh > 3` 或
`mobility_mode != static`）而每 UE 不足 2 个样本时**自动补到 2 倍并写进
`num_samples_note`**——补，但不静默地补。静止场景不补，不白花时间。

同理，`num_samples` 恰好等于 `num_ues` 是个很自然的写法，
所以这个坑很容易撞上。

### 比耗时必须交错重测

第一版性能基准把变体一个接一个顺序跑，耗时单调下降（4830→4054→…→1591 ms），
得出"关掉 measurements 里的可选项能快 2.55 倍"。**全是预热与缓存的假象**——
代码层面 `internal_sim` 只读 `ssb_rsrp` 与 `interferer_channels` 两个开关，
其余四个根本没被引用。

正确方法：每轮把所有变体各跑一次、轮转多轮取中位数，并把基准自身的轮间波动
一起报出来。本机基准波动 **11.9%**，低于这个量级的"加速"就是噪声。
交错重测后只剩两条是真的：关 SSB **1.40×**、`num_interfering_ues` 设小
（0 → 1.62×、2 → 1.40×，但它改变物理）。

### 文档里的计数要和代码绑死

"19 项体检"这句话在 README / SKILL.md / 两份 HTML 里写了八处，
而 `full_report` 从第一版（f44b46a）起就只有 16 项——数字凭印象写的，从没对过账。
`test_interference` 第 10 节现在用正则从四份文档里抽计数，与
`len(full_report(ds).checks)` 和 `sw_` 工具数比对。加检查/加工具时会红。

### YAML 里以 `*` 开头的值是别名

`caveat: **别拿 los_ratio 当判据**` 会被 YAML 当成别名解析并报
"expected alphabetic or numeric character"。以 `*` 或 `&` 开头的值必须用
`>-` 折叠块或引号包起来。

### scenario 设成 *_LOS 不会让 los_ratio 变成 1

所有链路一律走 LOS 路损公式（不看逐链路的 `is_los`），但数据里的 `is_los`
仍按几何视距概率抽样——`umi_los_canyon` 实测 `los_ratio` 是 **0.46** 而不是 1。
判断一批数据是不是视距的看 `scenario` 字段，不看 `los_ratio`。

## 加东西的地方

- 新的 3GPP 校准量 → `calibration.py`，按条款号标注来源
- 新的 MCS/CQI 表或 TBS 分支 → `linkadapt.py`，**标准表必须过 `verify_tables` 的内蕴自检**
- 改分析 BLER 参数 → 跑 `anchor_check`，门限要落在公开 NR 曲线的常见区间
- 新的表驱动 BLER → 原始常量放独立数据模块，必须有 SHA-256、全 MCS 覆盖、
  横轴/BLER 单调、目标门限覆盖检查；来源不是标准就不能塞进 `verify_tables`
- 新的门禁判据 → `gates.py`（门 2/门 3）或 `validate.py`（门 1，会自动进门 1）
- 新的外部结果校验 → `results.check_pairable`，每条都必须是**硬拦截**不是告警
- 新指标 → `analysis.KNOWN_METRICS` 加单位；自定义指标也支持，单位由调用方给
- 新的标准查表值 → `spec38901.py`，**必须两条独立路径核对过**才录入
- 新的默认硬件/载波口径 → `hardware.py`，它是**默认配置的唯一真相源**
- 新的说明书示意图 → `spec.py` 加一个 `_svg_*` 函数并挂进 `render_html`；
  **画的必须是实际会跑的配置**，不是用户以为的那个
- 新的可在页面上改的参数 → `spec._EDITABLE`，**只加这一处**：控件、payload、
  回传白名单都从它派生（`spec.editable_keys()`）
- 新场景 → `presets/presets.yaml`，不改代码。**加完必须跑一遍 `sw_probe_scenario` 把实测值写进 `expect`**——preset 里的 label 是设计意图，写着「高干扰」实际只有 2 dB 的事发生过
- 新的干扰量或分级 → `interference.py`，门限改动等于改现场约定，先和用户对齐
- 新射线追踪城市 → ChannelHub 的 `configs/scenes/`，`scenes.py` 自动发现
- 新任务类型 / 新决策点 / 新对比组 / 新陷阱 → `decisions.py` 的
  `ALL_DECISIONS`、`_DESIGN`、`TASK_PROFILES`
- 新测量量 → `measure.py` 加函数 + `MEASUREMENT_CATALOG` + `_ALIASES` +
  `deliver.py` 的 `_BLOCKS` + `loader.py` 的方法

`_ALIASES` 加自然语言别名时注意：只有长度 ≥ 2 的别名参与子串匹配，
且别用"功率"这种过泛的词（会被"时延功率谱"误命中）。
