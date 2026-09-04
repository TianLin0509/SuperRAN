# 2026-09-03 信道 — Sionna RT 接成 SuperRAN 自己的可选信道源，默认仍是 CDL

**分支 / SHA**：`feat/sionna-rt-source` / `0e914de1ba6aa34d29dae6474fa7b29f3a343bda`　**风险档**：红
**报告**：`C:\VibeData\Artifacts\Reports\SuperRAN\20260903-sionna-rt-source.html`

## 改了什么物理机制

只换了小尺度信道矩阵这一段。原来这一段的多径来自 38.901 的标准簇（CDL/TDL 表里
的时延、功率、角度），现在多了一个可选来源：真实建筑几何的射线追踪。

射线追踪给出的是每条径的**离开方位角/天顶角、到达方位角/天顶角、时延、多普勒频移，
以及一个 2x2 极化耦合矩阵**。前四个量在 CDL 里是查表加随机角扩展得到的，现在是
几何算出来的；极化耦合矩阵在 CDL 里是按 XPR 合成的随机 Jones 矩阵，现在是 Sionna
按材质算出来的真实反射/折射极化响应。

这些径按下式叠成信道矩阵：

    H += g_p[u,b] · exp(j2π f_d,p t) · exp(-j2π (f_c + f) τ_p) · a_BS,p · conj(a_UE,p)

其中载波项 exp(-j2π f_c τ_p) 是 CDL 路径没有的。CDL 的时延是合成的、每个簇另有
一个独立随机相位，所以载波项被吸收掉了；射线追踪的径长差是真实的，径与径之间的
相对相位就来自这一项，漏掉它多径叠加就是错的。

**BS 阵列模型一个字都没改。** 端口阵因子和 1 驱 M 固定子阵方向图调的是 CDL 路径
用的同两个函数（`_spatial_panel_response` 与新提出来的 `fixed_subarray_response`），
不是另写一份。64T 的 8x4x2 / 1 驱 3 / 192 阵子、256T 的 16x8x2 / 1 驱 6 / 1536 阵子、
水平 0.5λ、垂直 0.67λ、`pol_h_v + top_to_bottom` 全部照旧。

## 为什么

原来 SuperRAN 是"借"外部适配层跑射线追踪的。2026-09-03 信道内核收编 first-party
时只重写了统计信道，RT 那条路跟着断了：`rt_munich` 等三个预设还在，但真跑会硬报
"引擎不可用"。现在把它补成本仓自己的直连适配层。

选择"只换信道矩阵、大尺度仍走 38.901 公式"，是为了让 CDL↔RT 的 KPI 差异**可归因**。
接缝以上的站点布局、撒点、LOS 抽样、路损、阴影衰落、服务小区选择、预波束 S/N/I
预算，接缝以下的估计噪声、SSB、TDD 成对与元数据，全部共用——两边只差信道矩阵一项。
射线追踪自己算出来的路损与时延扩展写在 `meta.rt_pathloss_db` /
`meta.rt_delay_spread_ns` 里作旁证，不驱动链路预算。要让 RT 接管大尺度必须是
另一次显式改动。

## 证据

* 物理锚点：单端口单极化下，本仓的合成与 Sionna 自己的 `Paths.cfr()` 相对误差
  **< 2e-3**，量级等于 Sionna 内部 float32 的相位精度（载波项在 f_c=2.6 GHz、
  τ~1 μs 时是 1e4 量级的角度）。时延、载波相位、多普勒三个约定错任何一个，
  误差都会跳到 O(1)。
* 阵列合同：单径下 BS 端口响应与"`_spatial_panel_response`（端口间距 3×0.67λ）
  × `fixed_subarray_response`（6° 固定下倾）"逐端口成常数比；把固定下倾改成 0°
  信道就变，说明馈电网络真的接上了。
* 归因性：同一套几何、同一套阵列，只换引擎——信道矩阵不同，而 `pathloss_dB`、
  `snr_dB`、`sir_dB`、`sinr_dB`、服务小区、UE 位置**逐位相同**。
* 不回退：把依赖探测打成"缺 sionna"，`require_source("sionna_rt")` 硬报错，
  不会返回 `internal_sim`。服务链路零径抛"覆盖空洞"，干扰小区零径返回零信道。
* 实跑 munich：3 样本 4.2 s，64T 1 驱 3，服务信道秩 4（归一化奇异值
  1 / 0.50 / 0.32 / 0.069），时延扩展 14.7~362 ns，时隙间因多普勒而不同。
* 接口重构不改行为：两次纯重构提交都用同一组 SHA-256 指纹（7 站 21 小区
  64T 1 驱 3 CDL-C + UMi_LOS CDL-D）验证 CDL 输出逐位不变。
* 测试：`test_sionna_rt_source` 17 passed；`test_native_independence`、
  `test_channel_generation_contract`、`test_lazy_imports`、
  `test_physics_contract_extensions` 合计 66 passed；脚本式
  `test_physics_invariants` / `test_linklevel` / `test_gates` /
  `test_raytracing` / `test_interference` / `test_e2e` 退出码均为 0。

## 没证明什么

* **不证明与现网一致**。射线追踪只在 Sionna 自带的四个场景上跑过，材质用的是
  Sionna 默认 ITU 材质，没有做任何材质标定。
* **不证明 RT 与 CDL 谁更准**。只证明了两者不同，以及差异可归因到信道矩阵。
  没有跑任何一组对照实验去比 KPI。
* **不覆盖真实 OSM 城市场景**。本机没有配 `SUPERRAN_SCENES`，
  `rt_shanghai_lujiazui` / `rt_shenzhen_futian` 两个预设的资产不存在，
  这条路径没有实跑过。
* **大尺度是工程折中**。路损用 38.901 公式而不是 RT 自己的结果，这是有意为之的
  归因性取舍，不是"RT 的路损更准所以该用"的结论。
* **UE 位置需要人工挑**。慕尼黑老城随机撒点有相当比例落在全遮挡处（实测原点站位
  下 40 个点只有 6 个有径）。`rt_munich` 预设写死了 6 个实测有覆盖的位置；换站高、
  换场景、换平移量都要重新扫。这不是覆盖率结论，只是能跑通的参数。
* **默认关闭衍射与漫反射**。`rt_edge_diffraction` / `rt_diffuse_reflection`
  默认 False，深阴影区的场强会偏低。
* **没测性能上限**。3 样本 4.2 s 是 1 站 3 扇区、16 RB、max_depth=4 的数字，
  没有扫过站数、UE 数、RB 数对耗时的影响。

## 影响哪些 KPI

默认路径 **零影响**：`internal_sim` 仍是默认引擎，CDL 输出经指纹验证逐位不变。

显式选 `source: sionna_rt` 时，信道矩阵的秩结构、频率选择性与空间相关性都会变，
因而 CSI 压缩、预编码、谱效、MU 配对这类结论都会跟着变。**方向与量级未测量**——
本次没有跑 CDL↔RT 的对照实验。
