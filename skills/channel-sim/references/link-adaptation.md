# 链路自适应：谱效、吞吐、MCS 与 TDD

**什么时候读这一份**：用户要真实吞吐（Mbps）而不是谱效、问 MCS/CQI/BLER
是怎么来的、要 TDD 的 MCS 审计链，或者要画谱效/吞吐 vs SNR 曲线时。

## 领域事实层面的常见误读

| 心里的念头 | 实际情况 |
|---|---|
| "达成率只有 40%，算法不行" | 先看 MCS 分布：压在最高档就是表封顶，跟算法无关 |
| "表 3 算出 0.1%，所以所有配置都可靠" | 曲线只代表经典 MMSE 接收机的 SINR→BLER 映射，其他维度当前不建模 |
| "换 mcs_table=2 就一定更快" | 只有确实压在表顶时才有效。实测 29 dB 场景 1373 → 1690 Mbps，低信噪比处无差别 |
| "BLER 是 3GPP 实测的" | 表 1/2 是有限码长分析模型，表 3 是内置预置曲线，两者都不是标准曲线 |

## 香农谱效不是吞吐 —— `sr_throughput`

`sr_link_performance` / `ds.link()` 给的是所选预编码与接收机下的
`SE = Σ log2(1+SINR)`，即**高斯码本谱效，不是 MIMO 容量上界**；返回里的
`capacity_bound` 才是同一噪声/干扰口径下逐时频最优注水容量。真实 MCS/TBS
吞吐还会低于高斯码本谱效，原因有三条，都是可量化的：

1. **调制受限** —— 20 dB 时香农说 6.66 bit/s/Hz，64QAM 最多给 5.80
2. **码率离散** —— MCS 只有 29 档，实际码率总落在需要的码率之下
3. **有限码长 + 实现损失** —— LDPC 距容量 1~2 dB，码块越多 TB 越易错

`sr_throughput` 走业界做系统级仿真的标准路径（链路到系统映射：
有效 SINR → MCS/CQI → TBS → BLER → 吞吐），给出 **Mbps** 和
**5% 边缘用户吞吐**（3GPP 评估里的公平性指标，比均值更能说明问题）。

**返回里 `hint` 提示"大量样本压在最高档 MCS"时，一定转述给用户**——
那说明限制来自 MCS 表而不是信道或算法，换 `mcs_table=2`（含 256QAM）
通常直接提升 20% 以上。实测 29 dB 的场景：表 1 均值 1373 Mbps，表 2 1690 Mbps。

`sr_sweep_snr` 出**谱效/吞吐 vs SNR 曲线**，无线论文里最标准的那张图。
各点跑在同一批信道上、彼此配对，曲线不含信道抽样噪声。
达成率的走势最有信息量：低信噪比处 70~77%（受噪声限），
高信噪比处掉到 40% 以下（受 MCS 表封顶限）。

**工作点边界**：显式传 `snr_db=20` 表示合成的预波束 SNR，用来画受控曲线；
不传时才使用数据集逐样本几何 SINR。first-party 后端把它定义在**预数字波束、
每 RB**参考面：阵元方向图与固定子阵增益已进大尺度预算，数字多端口 BF 增益
仍在 H 中。因此用 `E[|H|²]` 反推总损伤，再由预编码器把数字 BF 增益贡献一次；
不能用 rank-1 后波束 `E[σ₁²]` 当默认锚点，否则会把真实 BF 增益抵消。
若同时有自洽的 SIR 和干扰信道，才把总损伤拆成噪声
与经几何功率重标定的空间协方差；条件不齐时把 I+N 全部当白损伤，不重复加干扰。

## 评价链路：谁出数、谁判决

- **`sr_link_performance` 出数** —— 一次调用在同一批信道上横评多个方案（默认
  svd / svd_wideband / type1 / dft，自研方案加进 `methods`），返回谱效均值、
  95% 置信区间与收敛判断。**只出数不过门**：均值差再好看，也不许直接写成结论。
  `use_estimated_csi: true` 是 CSI 反馈课题的核心对比——估计信道算预编码、
  理想信道评性能，量的是 CSI 误差的真实代价。
- **`sr_compare_arms` 判决** —— 横评筛出的决赛组合两两过它。

## MCS 表与 BLER 的口径边界

**一条必须守住的边界**：表 1/2 的 MCS/CQI/TBS 按 38.214 精确算，QAM 约束
容量精确求积，但 BLER 是**有限码长分析模型**。表 3 则是用户提供的 28 档 MCS +
56 条 NewTx/ReTx 解调曲线（1824 点），**不是 3GPP 标准曲线**。预置 profile 约定：
源标签 Es/No 在预置 profile 中解释为经典 MMSE 的单码字有效 SINR；误块事件是一个用户 grant/TTI 的
独立单码字 TB，系统不单独暴露 CBLER。查询输入只含 MCS + 码字级有效 SINR；跨 RBG、
跨 rank stream 都做 dB 平均。TBS/RE/rank/场景/码字数不是查询轴，这是通用曲线合同。
物理编码内部即使存在多个 CB，也不能在预置曲线后再次做 CB→TB 合成。曲线范围外只能
保守钳位，不能外推。

- `sr_mcs_info(table=1/2, show_bler_anchors=true)` —— 看分析模型门限
- `sr_mcs_info(table=3, show_bler_anchors=true)` —— 看用户曲线两套门限与哈希自检
- `sr_bler_curve(mcs=..., tx_mode="newtx"/"retx")` —— 取单档原始曲线或插值
- `sr_tdd_mcs(dataset_id=..., cqi=..., olla_mcs_offset=...)` —— TDD 最终 MCS 与逐流审计链

表 3 每 TB 最多一次重传，默认 IR、可选 CC，全部只从 NewTx 曲线推导；原始 ReTx 行
仅供审计。IR 用半谱效等效 MCS 查表，CC 用原 MCS +3.0103 dB；等效 MCS 不得写回空口。
重传保持原 MCS/RBG 数/rank/TBS，失败字节后续成为新 TB。不要把预置 TBLER 再按 CB 数放大。
表 3 使用已确认的内部 CQI0..14 离散表，不使用 38.214 CQI 编号。

## TDD 的 CQI、BF Gain 与 OLLA

用户要求 TDD MCS 或提到 CQI/BF Gain/OLLA 时，调用 `sr_tdd_mcs`，**不要在对话里
手算**。固定顺序是：`内部 CQI → 显式离散表映射初始 MCS → 该 MCS 的 NewTx 目标 BLER
SINR 门限 → + BF Gain → 重映射 MCS → + OLLA → floor → 钳位 0..27`。

- CQI 是 PMI 权测得的 **pre-BF** 索引，是**长期滤波的宽带量**；
  内部 CQI0 是最低可用档并映射 MCS0，不是 38.214 out-of-range
- BF Gain 是**瞬时量**，逐 RB、逐流计算 `post-MMSE SINR_SVD - post-MMSE SINR_PMI`
- 进入 MCS 的 BF Gain 只在 gNB 可见 `h_prec` 上计算；`h_true` 上的差只作事后审计，
  固定 `h_est` 时换 `h_true` 不得改变当次 `bf_gain_user_db`
- 两条链路必须共用信道、CSI、rank、功率、噪声、干扰和经典 MMSE 接收机，
  只改变预编码权；**rank 不同不是 BF Gain**
- 用户 SINR 对全部 RB×流在 **dB 域做算术平均**，不做线性域平均或 MIESM
- OLLA 单位是连续 MCS 档位，不是 dB；正值更激进；先相加再严格向下取整
- 内部表是 `[0,1,3,5,7,9,12,14,16,19,21,23,25,27,28]`；当前曲线
  只有 MCS0..27，所以 CQI14 保留请求 MCS28 但在该 profile 上显式钳到27
- 默认目标首传 BLER 10%，ACK +0.1、NACK -0.9；反馈只更新下一调度时刻

**发送侧 SINR 是 `Γ(MCS(CQI)) + BFGain`，不是接收 SINR 的均值。** 后者是个
事后诸葛亮的量——它已经包含了 SVD 的实际增益，等于假设基站预先知道波束打得准不准。
开 CSI 老化后这个错会变致命：老化的全部代价就是"基站以为打准了其实没有"。

转述结果时至少给出初始 MCS/门限、逐流 BF Gain、用户 SINR、BF 后 MCS、
OLLA offset 和最终 MCS；若 `clamped_low` / `mcs_clipped` 为真也必须说明。
