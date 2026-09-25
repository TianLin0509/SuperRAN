# SRS 与信道估计

> 本文件由 `CLAUDE.md`「踩过的坑」按主题拆出（2026-09-25），正文原样搬移，条目标题保留原文；索引在 `CLAUDE.md`。
> 每一条都是真实事故，**删之前先想清楚**；改动口径时同步更新本条和 `CLAUDE.md` 索引的一句话要点。

**什么时候读**：改 `srs_*.py` / 估计器 / SRS 跳频与端口合同。

### LMMSE 必须从真实 pilot 位置直接映射到目标频点

本地估计器对外稳定名字是 `ideal` / `ls_linear` / `ls_mmse`；
`ls_lmmse` 是完全等价、物理命名更精确的兼容 alias。`internal_sim` 还多两档 `ls_hop_concat` /
`ls_hop_sequential`（SRS 跳频，仅上行），默认仍为 `ls_linear`。

旧 LMMSE 只在 compact pilot grid 上做一次平滑，随后仍用线性插值补洞；对非均匀、punctured
或 hopping SRS，这并不是 LMMSE。当前实现直接计算
`R_tp (R_pp + R_v)^-1 h_LS,p`，支持任意唯一 pilot position 和可选有色 `R_v`；没有 Doppler
协方差时，时间维明确保持线性插值，不冒充 2D LMMSE。

验收口径不是“每个 realization、每个 SNR 都优于 LS”——先验失配时这不成立。硬门是：
匹配指数 PDP 的低 SNR Monte Carlo 显著降 MSE、非均匀位置正确、输出有限、full-pilot 高 SNR
极限收敛到 LS，且 `ls_mmse == ls_lmmse`。

### "17 倍跳频"是 38.211 里逐字有的

`SRS_BW_TABLE` 第 63 行：`m_SRS=(272,16,8,4)`、`N=(1,17,2,2)`。
取 `B_SRS=1`、`b_hop=0` 时每次 SRS 占 **16 RB 正好一个 RBG**，**17 跳**扫完 272 RB，
和本项目 17 RBG × 16 RB 的载波配置 1:1 对上。实测 `srs_hopping_cycle_length` 返回 17、
标准扫描顺序是 RBG `0,8,16,7,15,6,14,5,13,4,12,3,11,2,10,1,9`，17 跳并集恰好覆盖全带。

**跳频是老化的主导项**：`T_SRS=10 ms` 时全带扫一遍要 170 ms，
某个 RBG 的 CSI 陈旧时长在 0~160 ms 之间轮转（平均 80 ms，另加周期相位与处理时延），而 2.6 GHz、30 km/h 的
相干时间只有约 3 ms。实测 MU/SU 比值 0.816 → 0.449（−45%），SU 谱效 −27%；
把信道换成慢变（ρ=0.99）后损失掉到 10%——**这条对照证明损失确实来自时变**。

序列由 SuperRAN 本地 `native.srs_rb_indices` / `hardware.COMPANY_SRS_17_HOP_ORDER_RBG`
共同冻结。没有依赖缺失时的恒等扫描兜底；非 272-RB 产品 profile 直接拒绝。

### 2T4R 的 4 是待探测天线，不是 4Tx 同时发

当前终端基线是 2T4R：数据张量仍保存 4 个 UE 逻辑天线端口以形成完整
`64×4` 互易信道，但任一 SRS 机会只发送 2 ports。端口 0/1 在当前可用
SRS 机会发送，端口 2/3 在下一个可用机会（如 slot7→17，间隔 5 ms）发送；
两次 `64×2` 按天线身份拼成 `64×4`。两个端口组必须分别维护 CSI lag，
不能先拼成一个同时刻矩阵再统一老化。

工程资源 profile 只开放 **4 个 CS**，分成 `(0,1)` / `(2,3)` 两块，
一个时频/RBG 叶子可承载两个 UE 的当次 2T 发送。38.211 在某些 comb 下
允许更多 CS 是标准能力上限，不得据此把项目基线改回 8。加粗
`symbol11/comb1` 与 `symbol13/comb0` 是 BBL 保留叶子，普通 H 必须排除。
每个 UE 的两腿保持同一 frequency resource，完成后才推进 17-hop。

全局周期从 10 ms 起在 10/20/40 ms 中选最短可容纳值；只能用本 PCI 模3颜色，
本色不足就全局升周期，禁止跨色或占用 BBL。2T4R 每色容量依次为
68/136/272 UE。
