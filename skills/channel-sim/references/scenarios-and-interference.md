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

两条实测边界：探测口径下 `snr_dB` 会因为定义里的 `-10log10(RB)` 而整体抬高，
高信噪比场景可能先撞 ±50 dB 夹逼再被减回去（`scenario.probe` 会剔除并计数）；
`doppler_hz` 是 `|v|/lambda` 最大 Doppler，CDL 内只做一次逐 ray 方向投影；
不再依赖每 UE 至少两个 snapshot。`mobility_mode=static` 仅冻结跨 snapshot 几何，
若 `ue_speed_kmh>0`，快照内部仍有相应的小尺度 Doppler。

显式 `UMa_LOS` / `UMi_LOS` 会强制 LOS 状态，并切到有效 CDL-D；历史名字
`*_NLOS` 仍按 38.901 的距离条件概率抽 LOS/NLOS，不能把后缀误读成强制 NLOS。
判断一批数据是不是视距看 `scenario` 字段，不看 `los_ratio`。

## 干扰强度用 IoT 说话

**业务域和测量域是两回事，混起来的结论一定是错的：**

| | 是什么 | 决定什么 | 怎么看 |
|---|---|---|---|
| 业务域 | PDSCH/PUSCH 受到的干扰 | 吞吐、MCS 选择 | IoT `(I+N)/N`，>= 20 dB 算高干扰 |
| 测量域 | SRS / CSI-RS 导频受到的干扰 | 信道估计精度、预编码好不好 | 导频域 SIR、估计 NMSE 下限 |

实网里最难查的一类问题正是"业务域 SINR 看着还行、测量域已经崩了"——
预编码用的是被污染的信道估计。测量域的量**只在 `link="BOTH"` 时才产生**。
实测一组对照：`srs_congested` 与 `srs_clean_reference` 只差导频配置，
业务域 IoT 差 0.06 dB（噪声），SRS 测量域 SIR 差 **17.9 dB**。

- `sr_interference_report(dataset_id)` —— 两个域一起给，含等效小区负载
- `sr_design_interference(target_iot_db=20)` —— 要造某个干扰强度该动哪些旋钮
- `sr_iot_convert(...)` —— IoT / 等效负载 / 分级之间换算

主算法用 `IoT = SIR/(SIR-SINR)`（线性域），`sr_interference_report` 已经这么算。
当前 first-party `snr_dB/sinr_dB` 共享**预数字波束、每 RB**参考，因此
`snr_dB-sinr_dB` 数学上等价，可作一致性旁证；外部/旧数据未声明信号参考时，
不能把这条等式当跨源契约。
`num_slots_per_sample > 1` 时这个式子只是近似（`sinr_dB` 是各 slot 的 dB 均值、
`sir_dB` 只取最后一个 slot），`iot_exact` 会标成 false。

**声称"高干扰"之前必须复核**：`sr_gate` 里的 IoT 自洽性检查会给出实测中位数与
等级。预设里的 `label` 写的是设计意图，不是保证达标的实测值。

当前信道级几何预算把每个非服务小区都视为活动，真正能动其 IoT 的是站间距、
功率、方向图和噪声底；`pdsch_load` 不参与这条预算。体验/系统级的 PRB 利用率与
`neighbor_prb_util` 才负责调度负载，不能把两层旋钮混用。所有目标 IoT 都必须
生成后用 `sr_interference_report` 实测校准，预设标签不是保证值。
系统级仿真里的邻区负载是另一回事，见 `system-sim.md` 的 `neighbor_prb_util`。

`num_interfering_ues` 是**上行旋钮，下行不读它**，所以 `srs_congested` 这类
"高测量干扰场景"本质是上行场景。

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
