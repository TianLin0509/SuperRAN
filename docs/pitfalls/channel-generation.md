# 信道生成、几何、阵列与探测

> 本文件由 `CLAUDE.md`「踩过的坑」按主题拆出（2026-09-25），正文原样搬移，条目标题保留原文；索引在 `CLAUDE.md`。
> 每一条都是真实事故，**删之前先想清楚**；改动口径时同步更新本条和 `CLAUDE.md` 索引的一句话要点。

**什么时候读**：改 `native.py` / `generate.py` / `spec38901.py` / `hardware.py` / `scenario.py`，或 preset 的信道与阵列配置。

### num_samples 必须能被 num_ues 整除

first-party source 的等用户轮转合同。`generate._align_to_ues()` 负责
向上取整并在够数后截断，不要让这个约束泄漏给用户。

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

### CDL-A~E 表、20-ray 展开与 K 因子都有硬门

2026-08 审计确认历史外部实现曾出现 CDL 角度列、行数和可选字段漂移。现在
`spec38901.py` 是本仓唯一运行表真相源，五张表与 38.901 Table 7.7.1-1~5
逐字段一致（23/23/24/14/15 个表分量）；`native.get_channel_profile()` 直接读取它，
不再 monkey-patch 外部注册表。shape/字段自检失败会阻断生成，不能静默降级。

生成器对每个 diffuse component 按 Table 7.5-3 展开 20 rays，并使用每簇角扩展、逐 ray
XPR/Jones 相位和 Doppler。CDL-D/E 的镜面/Laplacian 功率差已经把 K=13.3/22 dB 写在
row 0/1 中，**禁止再做第二次 Rician K 混合**；回归测试通过篡改 K 元数据并要求输出逐位
不变来防止复发。

### 预置 64T/256T 使用同一端口顺序合同

真实 AAU：**64 个 RF 端口（8H x 4V x 2pol），每端口固定驱动垂直相邻 3 个阵子，
共 192 个物理阵子；水平 0.5λ、垂直 0.67λ**（RF 端口垂直相位中心 2.01λ > λ，
垂直方向有栅瓣）。载波 n41 2.6 GHz / 30 kHz / 100 MHz / **272 RB**
（17 RBG x 16 RB；38.104 标准表是 273，口径不同），终端默认 **4R 下行**，
仿真粒度到 RB 为止。

`native.EffectiveArray` 按这套硬件生成物理位置与稀疏馈电矩阵；
`hardware.apply_array_defaults()` 对两个已确认面板自动切到
`effective_subarray`：64T 的 8x4x2 / 1 驱 3，以及 256T 的
16x8x2 / 1 驱 6。

两者统一采用 **`pol_h_v + top_to_bottom`**，0-based 端口公式为
`r = p*N_H*N_V + h*N_V + v`，`v=0` 是物理顶部。64T 的 1-based 公式是
`p*32+h*4+v+1`，256T 是 `p*128+h*8+v+1`。历史 64T
`h_v_pol + bottom_to_top` 只用于读取旧样本；迁移必须把 H、W、F 同时按物理位置
置换，禁止只 reshape 或只改元数据。

**历史消融（2026-07-31 旧内核、同 seed、单小区 30 样本；百分比不能外推到当前版本）**：

| | 真实 AAU | legacy_64 |
|---|---|---|
| SVD 谱效 | 28.20 | 33.23 |
| 吞吐均值 | 1055.5 Mbps | 1337.5 Mbps |
| 边缘用户 | 582.4 Mbps | 940.0 Mbps |

上表是 2026-07-31 的**历史对照**，证明 legacy 与真实 1 驱 3 不是同一信道；
2026-08-11 重构了大尺度功率参考与链路级预波束锚点后，不能把 27%/61% 当成
当前通用增益。`validate.check_antenna_model` 仍会标出 legacy，但所有百分比必须
在当前版本、同一批信道上重跑配对实验。

三条边界：

* `h_serving_true` 与 legacy 的**相对差 4.03**，完全是另一个信道。
* `effective_subarray` 与 `physical_reference`（真跑 192 阵子再用 F 投影）
  当时相对差 **4.8e-7**。当前仍必须由 effective↔physical 数值等价门验证，
  不能把历史误差当成永久承诺。
* 阵元方向图、固定 1 驱 N 子阵、垂直几何与电下倾会进入 conducted-power
  链路预算，因而会改变几何 SNR/SIR/SINR；64 端口数字 BF 增益仍留在 H 中。
  预设 `expect` 必须随当前内核重新校准，不能沿用旧“几何量逐位不变”结论。

垂直 0.67λ 是用户实测纠正过的值（早期按 0.5λ 算，全盘产物失真），
见记忆 `project_reconfig_mimo_sim` 方法论教训第 4 条。**别改回 0.5。**

1 驱 N 是**具体 AAU 的硬件事实，不是按端口数猜出的通用规律**：只对已确认的
8x4x2（1 驱 3）和 16x8x2（1 驱 6）自动生效；16T 等未知面板保持 legacy。

### 多时隙的快照间隔是 5 ms，不是一个 TTI

first-party source 的 `sample_interval_s` 是独立显式时钟，默认 **5 ms**；
它既不是 0.5-ms TTI，也不再从 SRS/CSI-RS 周期反推。移动 UE 每个快照推进

    speed × sample_interval_s

默认即 **5 ms**。把它当成一个 TTI（0.5 ms）会让**所有时间相关的
结论差 10 倍**——CSI 老化、多普勒、移动性全部受影响。

**这个错误很难自己看出来。** 症状是"3 km/h 的信道相关系数 7 ms 内掉到 0.24"，
而按 2.6 GHz 算相干时间有 59 ms。当时第一反应是怀疑外部源每 slot
重抽了小尺度衰落——查下来**不是**：相关随滞后单调下降（滞后 1 为 0.987、
滞后≥8 为 0.374），而且 0.315→0.342→0.390→0.432 的回升正是 **J₀ 的负瓣**，
形状完全是 Jakes，只有时间尺度不对。

**抓它的办法是拿 Jakes 对时间轴**：ρ(τ)=|J₀(2π·f_d·τ)| 的首零点在
τ = 2.405/(2π·f_d)，3 km/h 时是 53 ms。实测极小值落在第 10 个快照，
53/10 = 5.3 ms/快照——对上了。

`system.snapshot_interval_ms(cfg)` 现在由配置算出来，别再硬编码。

### preset 里不要写死 bs_panel

写死会让天线覆盖失效：用户传 `bs_antenna="4T4R"` 时 `num_bs_tx_ant` 变成 4，
而 `bs_panel` 还是 `[8,4,2]`（64 口），两者矛盾，生成出来的 `BS_ant` 不是 4。
`test_mcp_server` 的"用户指定的 4T4R 生效"当场抓到过。

让 `_ensure_bs_panel` 从 `num_bs_tx_ant` 推：64 -> `[8,4,2]` 正是要的，
4 -> `[2,1,2]` 也自动落回 legacy（4T 没有 1 驱 3）。

### bs_panel 决定空间阵列，不再是几何干扰的开关

当前 first-party 后端直接从服务与全部邻区的 received-power budget 形成
`snr_dB / sir_dB / sinr_dB`，已经不再依赖旧 `_system_sinr` 的 DFT 码本分支。
`bs_panel` 仍不可省：它定义二维端口几何、双极化排布，并决定 64T/256T 能否启用
已确认的 1 驱 3 / 1 驱 6 effective-subarray；缺失时
`generate._ensure_bs_panel()` 会由端口数推导。

`validate.check_interference_modeled()` 仍保留来源退化门：多小区下若 SIR 恒为
49.9 dB 哨兵，或 SINR 与纯热噪声 SNR 逐点相同，则邻区功率没有进入预算，
干扰类结论全部阻断。根因应检查来源版本、真实小区数与 `rx_power_all_dbm`，
不能再归因于“没建 DFT 码本”。

### 压 num_rb 探测场景是安全的，但 snr_dB 会撞夹逼

同一 seed 下压 `num_rb` 时，传播状态、SIR、路损、距离、视距、多普勒、UE 位置与
上行几何 SIR 保持不变。总载波功率被均分到更少 RB，raw SNR 会按
`10log10(RB_full/RB_probe)` 升高；raw SINR 在噪声不可忽略时也会随之变化。
`scenario.probe` 先精确还原全带 SNR，再与不变 SIR 重算全带 SINR/IoT。

历史外部数据曾把 `snr_dB` 夹到 ±50 dB，探测修正会丢失被截断前的信息。
first-party source 不再截断 SNR/SINR；`scenario.probe` 的历史 clamped 计数字段保留，
但新数据应为 0。无干扰 SIR 仍使用 49.9 dB 有限哨兵，并由拓扑语义识别。

`num_ofdm_symbols` 同样可压；first-party source 已把几何量移出 symbol 网格，14 / 7 / 4 /
2 / 1 下 SINR、SIR、路损、距离、LOS 与位置逐位相同。正式输出的时间轴是 slot snapshot，
不会把 symbol 伪装成 TTI。`PROBE_NUM_SYM` 仍取 4，
是为了保留一小段时域结构并更容易暴露误用，不是因为 1 有几何悬崖。这个旋钮
**只对探测模式安全**：正式信道会随时间网格改变。适配器也不再做复信道平均——
历史 symbol-grid adapter 才取中间 symbol；first-party source 直接保留完整 slot 轴。

性能数字必须绑定内核版本。旧单簇内核曾测得 11.5×；20-ray 内核在 2026-08-11
用 21 小区 16T/20 MHz、6 样本交错两轮重测，full 为 7.80/9.45 s/样本，probe
为 4.78/4.79 s/样本，约 **1.80×**。绝对值和比例都不是 SLA，以调用返回的
`elapsed_s` 为准。

### 探测模式仍补齐 bs_panel，但原因是空间阵列一致

`probe_config` 调 `_ensure_bs_panel`，保证 probe 与 full 使用相同二维端口、极化和
effective-subarray 配置。当前几何干扰不再由 panel/DFT 码本开关控制；probe 的
raw SINR 变化来自每 RB PSD 改变，必须按上一节的 SNR→SINR 重构处理。

### 多普勒定义与移动轨迹边界

first-party source 的 `doppler_hz` 明确定义为最大 Doppler
`f_max = |v| / lambda`，CDL 内部再按每条 ray 的方向余弦投影一次。旧实现先把
速度投影到最近站的径向、随后又在 CDL 内投影一次，会把高铁场景严重压低；
`hst_350kmh` 在 2.6 GHz 下现在稳定为 **842.59 Hz**，与解析值一致。

`mobility_mode=static` 只表示跨 snapshot 的 UE 几何位置固定、样本间不构成连续
轨迹；它不覆盖 `ue_speed_kmh`。因此可用 `static + 3 km/h` 生成独立位置快照下的
步行小尺度时变。要真正零 Doppler，必须显式设 `ue_speed_kmh=0`。

### scenario 设成 *_LOS 不会让 los_ratio 变成 1

所有链路一律走 LOS 路损公式（不看逐链路的 `is_los`），但数据里的 `is_los`
仍按几何视距概率抽样——`umi_los_canyon` 实测 `los_ratio` 是 **0.46** 而不是 1。
判断一批数据是不是视距的看 `scenario` 字段，不看 `los_ratio`。
