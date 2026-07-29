# superwireless

给 Agent 用的无线仿真信道供应站 —— **面向蒙特卡洛验证**。

你提一个无线算法优化思路，它给你可信的信道场景实例、配套的物理观察量，
以及 SINR / 谱效的完整评价链路。复用
[ChannelHub](https://github.com/wangxz0803-lab/ChannelHub_main) 的物理内核，
通过 MCP 向任意 Agent 开放。

配套的 `channel-sim` skill 提供 superpowers 式工作流：
**收敛式头脑风暴 → 计划书 → 生成 → 取货代码**。

## 三件事

**一、信道可信。** 内置对标体检：路损逐点对 3GPP 38.901、时延扩展对标准剖面、
时频能量守恒、谱效不超容量上界、预编码方案的性能排序、SISO 退化到香农公式、
蒙特卡洛置信区间。**不通过不会静默**，会告诉你哪里不可信、怎么改。

```python
rep = ds.validate()
print(rep.text())     # 12 项检查，含实测偏差与容差依据
```

**二、信道多样。** 5 个传播场景（UMa/UMi 各含视距与非视距、InF）× 10 个信道剖面
（CDL-A~E 有每径角度、TDL-A~E 无）× 任意小区数 × 10 个真实城市射线追踪场景。
上层没问到的参数一律**原样透传**——`internal_sim` 共 44 个、`sionna_rt` 49 个。

**三、谱效开箱即用。** 预编码 → 逐层 SINR → 频谱效率的完整链路，
含 SVD（理想上界）、宽带 SVD、38.214 Type I 码本、DFT 波束四种方案的横向对比。

```python
mc = ds.monte_carlo(method="svd")
print(f"{mc.se_mean:.2f} bit/s/Hz  收敛={mc.converged}")

for name, v in ds.compare_precoders().items():
    print(f"{name:<14}{v['se_mean']:6.2f}  (SVD 的 {v['vs_svd_pct']:.0f}%)")
# svd            32.26  (SVD 的 100%)
# svd_wideband   21.08  (SVD 的  65%)   ← 宽带损失
# type1          17.36  (SVD 的  54%)   ← 码本量化损失
# dft             9.77  (SVD 的  30%)   ← 单层波束
```

## 交互方式

```
你：帮我验证一个 CSI 压缩的想法，先弄一批单小区 64T4R 的信道数据。

Agent：配好了 64T4R、273 RB、CDL-C、100 MHz。
       第 1 轮 · 实验设计 —— 参数配错重跑就行，实验设计错了结论作废。

       ① 你的方法要跟什么比？
          1) 3GPP Type I 或 Type II 码本 —— 最常见的基线   ← 推荐
          2) 理想信道的 SVD 预编码 —— 理论天花板
          3) 某篇已发表方法 / 4) 还没定，先看可行性
       ② 用什么指标？
          1) 重建精度类：NMSE / 余弦相似度                ← 推荐
          2) 系统收益类：频谱效率或吞吐损失
          3) 任务专属：波束命中率 / 定位误差 / BLER
       或者你直接说。
```

**一轮 2~4 个问题，每题 3~4 个选项并标明推荐**，最后留"或者你直接说"。
典型 2~3 轮收敛，用户随时可以说"随便"直接生成。轮次由 MCP 自己记，
Agent 不用规划；`has_more_rounds` 为 false 或用户说"随便"就停。

设计参考 [superpowers](https://github.com/obra/superpowers) 的 brainstorming，
按仿真场景做了调整——它面对开放式设计所以一次一问，而仿真参数空间有限且已知。

## 文档

- **[能力手册 `CAPABILITIES.html`](CAPABILITIES.html)** —— 能产生哪些信道、能拿到哪些观察量（含形状与单位）、参数全表、能力边界
- **[实测场景演示 `SHOWCASE.html`](SHOWCASE.html)** —— 六个真实跑过的场景对话，含扩展性分析

## 四条设计铁律

**一、不传数据，传取货代码。** 单个信道样本几百 KB，序列化成 JSON 会膨胀到
十几 MB——进不了任何模型的上下文。MCP 只回句柄、统计摘要和可运行的 Python。

**二、给物理量，不给训练特征。** ChannelHub 的 `data/bridge.py` 会把这些量
归一化、截断、乘门控后打包成 16 个 MAE token——那是为表征学习服务的。
本项目**绕开它**：PDP 不归一化、RSRP 不截断、SRS 给完整协方差和全部特征值、
PMI 给码本索引而非嵌入向量。

**三、生成与取货解耦。** 测量量从信道现算，改主意重新取货**实测 1 毫秒**，
不重跑仿真。

**四、分轮问，先设计后参数。**

## 拦截"跑得出结果但没意义"的组合

| 组合 | 为什么拦 |
|---|---|
| 波束搜索 + TDL 模型 | TDL 没有每条径的角度，算法会输出看似正常的垃圾且不报错 |
| 信道预测 + 单时隙 | 样本间相互独立，没有可预测的时序结构 |
| 干扰协调 + 单小区 | 没有干扰源 |
| 视距场景 + 非视距剖面 | 路损与多径按不同假设生成，时延扩展偏离标称值数倍 |
| 射线追踪数据 + `ds.paths()` | 多径来自真实建筑几何，套用 CDL 剖面会得到与数据无关的假角度 |

## 安装

需要 Python ≥ 3.10。

```bash
git clone https://github.com/wangxz0803-lab/ChannelHub_main   # 物理内核
git clone https://github.com/TianLin0509/superwireless
cd superwireless && pip install -e .

pip install sionna-rt      # 可选，射线追踪（约 300 MB）
```

ChannelHub 会自动在同级/上级目录查找；放在别处就设 `SUPERWIRELESS_CHANNELHUB`。
不装射线追踪也能用，`sw_capabilities` 会如实报告缺什么。

```bash
claude mcp add superwireless -- python /path/to/superwireless/scripts/mcp_server.py
codex  mcp add superwireless -- python /path/to/superwireless/scripts/mcp_server.py

cp -r skills/channel-sim ~/.claude/skills/     # 可选：工作流编排
cp -r skills/channel-sim ~/.codex/skills/
```

## MCP 工具（11 个）

| 工具 | 作用 |
|---|---|
| `sw_capabilities` / `sw_list_presets` / `sw_list_scenes` | 能力与场景发现 |
| `sw_plan` / `sw_revise` | 分轮协商：实验设计 + 参数 + 对比组 + 陷阱 |
| `sw_generate` | 生成数据集，返回句柄与统计摘要 |
| `sw_deliver` | 按自然语言点单生成取货代码 |
| `sw_validate` | **可信度体检**：对标 38.901、物理定律、蒙特卡洛收敛 |
| `sw_link_performance` | **算谱效**：预编码 → SINR → 谱效，多方案横向对比 |
| `sw_describe_dataset` / `sw_list_datasets` | 数据集信息 |

## 观察量（12 类）

| 名称 | 内容 |
|---|---|
| `channel` | 频域信道矩阵，理想与估计两版 |
| `linkperf` | **链路性能**：预编码、逐层 SINR、谱效、容量上界、多方案对比 |
| `validate` | **可信度体检**：12 项检查 |
| `pdp` | 时延功率谱：未归一化功率 + 真实时延轴 + RMS 时延扩展 |
| `paths` | 每条径的时延、功率、角度（**CDL 才有角度**）|
| `srs` | 完整空间协方差、全部特征值、每天线增益、波束域 RSRP |
| `pmi` | 38.214 Type I 码本索引 + 预编码矩阵 + 秩 |
| `rsrp` / `sinr` / `capacity` | 功率、链路标量、容量与条件数 |
| `geometry` | 路损、阴影、3D 距离、视距判定、多普勒、位置 |
| `topology` | 多小区 SSB 测量与干扰小区信道 |

## 物理层工具箱

`superwireless.physical` 转发 ChannelHub 里已按 38.211/38.213/38.214 实现的模块，
主要用来**当基线**和**做导频层课题**：

```python
from superwireless import physical as ph

ph.nr_rb_count(100e6, 30000)       # 273（标准表，不是简单除法）
ph.tdd_pattern_info("DDDSU")       # 帧结构 + 特殊时隙符号级切分
ph.srs_config(273, b_srs=1)        # SRS 跳频：周期 17、每跳 16 RB、覆盖 6%
ph.zadoff_chu(25, 139)             # ZC 序列，实测峰旁比 151 dB
ph.ssb_sequences(42)               # PSS / SSS / PBCH-DMRS
ph.dft_codebook(8, 4, 2)           # CSI-RS 波束码本 [512, 64]
ph.estimate_channel(h, method="mmse", tau_rms_s=363e-9)   # LS / MMSE 估计基线
ph.project_interference(...)       # 干扰投影：不投影会高估干扰
```

## 场景与参数

**传播场景**：城区宏站视距/非视距 · 城区微站视距/非视距 · 室内工厂
**信道剖面**：CDL-A~E（有每径角度）· TDL-A~E（无角度）
**拓扑**：任意站数 × 扇区数（1 或 3），支持六边形栅格与线性布站、
超级小区、多 TRP、高铁车体穿透、自定义站点与用户坐标
**射线追踪**：慕尼黑 · 巴黎凯旋门 · 佛罗伦萨 · 旧金山 · 北京中关村 ·
上海陆家嘴 · 深圳福田 · 广州天河 · 杭州钱江 · 重庆解放碑
**子载波间隔**：15 / 30 / 60 / 120 kHz　**带宽**：5~100 MHz 共 13 档
**TDD 配比**：7 种　**支持任务**：12 类

加场景只改 `presets/presets.yaml`，加决策点只改 `decisions.py`。

## 已知约束

- **信噪比不能直接设定**。它由路损、发射功率和撒点位置决定；要求特定区间时
  走拒绝采样。想整体调整，改发射功率或站间距更有效。
- **视距比例由几何决定**，不是选 CDL-D 就能得到视距信道——剖面类别与几何
  判定不符时会被自动替换。想调视距比例改站间距（实测 200m→0.46、800m→0.13）。
- **射线追踪拿不到逐径几何**。ChannelHub 尚未导出 Sionna 的 `Paths` 对象，
  所以射线追踪数据集调 `ds.paths()` 会报错而非返回假角度。
- **时延扩展的频域估计有固有误差**。可观测最大时延是 `1/(12·SCS)`，
  实测比值 0.8~1.0，仅作数量级检查。
- **QuaDRiGa 未纳入**，需要 MATLAB/Octave 运行时。

## 测试

```bash
python tests/test_e2e.py         # 端到端 37 项
python tests/test_mcp_server.py  # MCP 全链路 21 项
python tests/test_raytracing.py  # 射线追踪与决策层 39 项
python tests/test_linklevel.py   # 谱效、可信度、物理层 35 项
```

共 **132 项**。

## 致谢

物理计算内核来自 [ChannelHub](https://github.com/wangxz0803-lab/ChannelHub_main)。
射线追踪基于 [Sionna RT](https://nvlabs.github.io/sionna/)。
工作流设计参考 [superpowers](https://github.com/obra/superpowers)。

## License

MIT
