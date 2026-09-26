# 场景、干扰与射线追踪

**什么时候读这一份**：用户提到某种场景（高干扰、覆盖、移动性、高铁、室内、
真实城市地图）、提到 IoT / 干扰强度 / 导频污染，或者要选预设时。

## 场景是不是想要的那个 —— 先探测，再下单

**用户说"高干扰场景"时，"高"是个数，不是形容词。** 别照着预设名字就开跑，
先花几十秒确认。

- `sr_list_presets(group=...)` 按组看 **23 个场景**：干扰场景 / 测量干扰 /
  大站间距 / 移动性 / 高铁 / 传播条件 / 多小区干扰 / 基线 / 射线追踪 / 室内与专网
- `sr_probe_scenario(preset=..., num_samples=30)` —— **下单前先看货**。
  把 `num_rb` 压到 24、`num_ofdm_symbols` 压到 4、关掉 SSB，几何量与全量
  **逐位相同**（实测）。20-ray 内核的一组 21 小区对照约 **1.80×**；不同配置
  会变，实际看 `elapsed_s`，不要继承旧单簇内核的 11.5×。
  回干扰画像、链路预算、路损/距离/视距/多普勒分布
- `sr_compare_scenarios([...])` —— 几个候选并排比一张表

探测**给不了**谱效、吞吐、时延扩展估计、宽带预编码——这些必须跑正式生成，
返回里的 `not_available` 会列清楚。

两条实测边界：探测把载波压到 24 RB 时原始 `snr_dB` 会整体抬高 `10log10(272/24)`，
报告已减回全带口径；探测与 `sr_generate` 使用同一套默认 AAU 阵列（2026-09-26
修复前漏了 12.77 dB 阵列增益，旧探测的 SNR/IoT 偏低，SIR 不受影响）；
`doppler_hz` 是 `|v|/lambda` 最大 Doppler，CDL 内只做一次逐 ray 方向投影；
不再依赖每 UE 至少两个 snapshot。`mobility_mode=static` 仅冻结跨 snapshot 几何，
若 `ue_speed_kmh>0`，快照内部仍有相应的小尺度 Doppler。

显式 `UMa_LOS` / `UMi_LOS` 会强制 LOS 状态，并切到有效 CDL-D；历史名字
`*_NLOS` 仍按 38.901 的距离条件概率抽 LOS/NLOS，不能把后缀误读成强制 NLOS。
判断一批数据是不是视距看 `scenario` 字段，不看 `los_ratio`。

## 下行干扰：先对齐假设，再看数

**SuperRAN 当前只仿下行业务。** 上行业务、上行 IoT 不在范围内，不要向用户询问或
报告；SRS 只作为下行预编码的信道估计来源出现。

### 先问什么：影响因子清单

`sr_plan` 对干扰类意图会附带 `factor_checklist`（来源 `src/superran/factors.py`），
按对下行 IoT/SIR 的影响排序，并标明平台是否建模：

| 因素 | 平台 | 对 IoT | 对 SIR |
|---|---|---|---|
| 发射功率 | 建模，默认 46 dBm | dB 对 dB | 不变 |
| 室内比例 / O2I | **未建模**，全室外 | 室内部署会低十几 dB（38.901 低损模型估算，未实测） | 近似不变 |
| 邻区负载 | **信道层未建模**，恒满发 | η=50% 约 -3 dB（系统级才有） | 若建模 +3 dB |
| 站间距 | 建模 | 大幅变化 | 几 dB（视距概率、断点、下倾） |
| 统计对象 | 部分：无 wrap-around | 含外圈小区偏低 | 偏高 |
| 撒点 | **未实现**，只有均匀撒点 | 热点会改变尾部 | 同左 |
| 场景、噪声系数 | 建模 | 见清单 | 见清单 |

用法：impact=1 的因素用户没提到时，主动说出平台取值；`must_disclose` 里的未建模项
必须告诉用户会让结果偏向哪边。

**有些配置键仿真器根本不读**（`factors.INERT_CONFIG_KEYS`）：`ue_distribution`、`num_hotspots`、`prb_utilization`/`pdsch_load`（信道层）、`train_penetration_loss_db`、`hypercell_size`。设了也和不设逐位相同，`sr_plan` 会警告；不要把它们当成可扫的变量。发射功率、噪声系数、邻区负载三条说法由
`tests/test_interference.py` 第 11 节逐样本对账，表和仿真器不会悄悄不一致。

**先写预期，再探测对照。** 让用户先说预期量级与来源；`sr_probe_scenario`
几十秒给出同口径的 IoT/SIR/SINR（与正式生成用同一套 AAU 阵列，逐位一致）。
偏差超过约 5 dB 先回到清单查假设，对齐后再正式生成。

### IoT 怎么读

IoT = (I+N)/N，回答“干扰比热噪声高多少”，也就是“噪声还剩多大影响”：
SINR 比 SIR 低 `10·log10(IoT/(IoT-1))` dB——3 dB 时低 3 dB，13 dB 时低 0.22 dB，
20 dB 时低 0.04 dB。**不要把下行 IoT 换算成“等效负载”**，那是上行极点容量关系；
下行 IoT 很高常常只是“信号与干扰都远高于噪声”（例如小站距配宏站功率）。

主算法用 `IoT = SIR/(SIR-SINR)`（线性域）。当前 first-party `snr_dB/sinr_dB`
共享**预数字波束、每 RB**参考，`snr_dB-sinr_dB` 可作一致性旁证；外部/旧数据未声明
信号参考时不能当跨源契约。`num_slots_per_sample > 1` 时式子只是近似，`iot_exact`
会标成 false。

- `sr_interference_report(dataset_id)` —— 下行业务域 IoT/SIR/SINR，外加
  `not_modeled`（室内/O2I、信道层负载、拓扑边缘）。**解读绝对值前先看这一栏。**
- `sr_design_interference(target_iot_db)` —— 各旋钮的方向与斜率；其中实测数字带
  `levers_measured_under`（UMi、33 dBm、7 站的历史条件），不能直接当本次预期。
- `sr_iot_convert(...)` —— IoT 分级与噪声造成的 SINR 损失；`load` 换算只是上行口径。

### 导频（测量域）干扰：目前是占位

数据集里的 SRS 导频 SIR 是 `10 - 10·log10(干扰 UE 数)` 的解析式，与几何、站距、
功率无关；CSI-RS 导频 SIR 逐样本等于业务域 SIR。报告会把它们标成
`analytic_placeholder` / `same_as_traffic_sir` 且不分级。**不能用它们比较场景**，
也不能说“业务域还行、测量域已崩”——那需要逐样本导频干扰仿真，当前没有。

**声称"高干扰"之前必须复核**：`sr_gate` 的 IoT 自洽性检查给出实测中位数与等级；
预设 `label` 是设计意图，不是实测值。

## 射线追踪

`sr_list_scenes` 查场景。内置 4 个（慕尼黑、巴黎凯旋门、佛罗伦萨、旧金山）
开箱即用；中国城市 6 个（北京中关村、上海陆家嘴、深圳福田、广州天河、
杭州钱江、重庆解放碑）首次使用自动准备资产。

切场景在 overrides 里写 `{"scene": "shenzhen_futian"}`，或用
`rt_munich` / `rt_shanghai_lujiazui` / `rt_shenzhen_futian` 预设。

**比统计信道慢一个量级**（约 2~6 秒/样本 vs 0.2 秒）。要几百个样本时先提醒耗时。

**射线追踪数据集不能调 `ds.paths()`** —— 多径来自真实建筑几何，套用 CDL
标准剖面得到的角度与数据无关，MCP 会直接报错而不是返回错误结果。

典型用法是**先用统计信道快速迭代，定型后换真实地图复核**——两者取货代码一样，
算法代码一行不用改。
