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
python tests/test_mcp_server.py  # MCP 全链路 21 项
python tests/test_raytracing.py  # 射线追踪与决策层 39 项
python tests/test_linklevel.py   # 谱效、可信度、物理层 35 项
python tests/test_gates.py       # 校准、标准表、三道门、统计判决 86 项
python tests/test_results.py     # 外部算法结果契约、预注册 80 项
python tests/test_linkadapt.py   # 链路自适应、吞吐、并行生成 102 项
python tests/test_interference.py # IoT、测量域、场景预设、文档计数 109 项
```

改动 `measure.py` / `generate.py` / `plan.py` / `decisions.py` / `scenes.py`
后前三个都要跑；改动 `linklevel.py` / `validate.py` / `calibration.py` /
`gates.py` / `spec38901.py` 要跑 test_linklevel + test_gates；
改动 `results.py` / `analysis.py` / `loader.py` 要跑 test_results；
改动 `interference.py` / `scenario.py` / `presets.yaml` 要跑 test_interference。

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

### BLER 是模型，MCS/TBS 不是

`linkadapt` 里只有 `BlerModel` 是模型（有限码长形状 + 可配实现损失，
没有 3GPP 参考曲线兜底）。MCS/CQI 表逐字录自 38.214、TBS 按 §5.1.3.2 复刻、
QAM 约束容量精确求积——**这三样不能和 BLER 混为一谈**。
对外说明必须分清，否则用户会把模型当实测用。

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
- 新的 MCS/CQI 表或 TBS 分支 → `linkadapt.py`，**必须过 `verify_tables` 的内蕴自检**
- 改 BLER 模型参数 → 跑 `anchor_check`，门限要落在公开 NR 曲线的常见区间
- 新的门禁判据 → `gates.py`（门 2/门 3）或 `validate.py`（门 1，会自动进门 1）
- 新的外部结果校验 → `results.check_pairable`，每条都必须是**硬拦截**不是告警
- 新指标 → `analysis.KNOWN_METRICS` 加单位；自定义指标也支持，单位由调用方给
- 新的标准查表值 → `spec38901.py`，**必须两条独立路径核对过**才录入
- 新场景 → `presets/presets.yaml`，不改代码。**加完必须跑一遍 `sw_probe_scenario` 把实测值写进 `expect`**——preset 里的 label 是设计意图，写着「高干扰」实际只有 2 dB 的事发生过
- 新的干扰量或分级 → `interference.py`，门限改动等于改现场约定，先和用户对齐
- 新射线追踪城市 → ChannelHub 的 `configs/scenes/`，`scenes.py` 自动发现
- 新任务类型 / 新决策点 / 新对比组 / 新陷阱 → `decisions.py` 的
  `ALL_DECISIONS`、`_DESIGN`、`TASK_PROFILES`
- 新测量量 → `measure.py` 加函数 + `MEASUREMENT_CATALOG` + `_ALIASES` +
  `deliver.py` 的 `_BLOCKS` + `loader.py` 的方法

`_ALIASES` 加自然语言别名时注意：只有长度 ≥ 2 的别名参与子串匹配，
且别用"功率"这种过泛的词（会被"时延功率谱"误命中）。
