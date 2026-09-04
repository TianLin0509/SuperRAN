# 2026-09-04 AMC/TBS — TBS 扣掉 DM-RS 与 PDCCH 开销

**分支 / SHA**：`feat/tbs-pdsch-overhead-20260904` / `<提交后回填>`　**风险档**：红
**报告**：`C:\VibeData\Artifacts\Reports\SuperRAN\20260904-tbs-pdsch-overhead.html`

## 改了什么物理机制

TBS 计算的第一步——**一个下行时隙里 PDSCH 到底拿得到多少 RE**。

改前两条主调度路径都按 `PRB × 12 子载波 × 12 符号 = 144 RE/PRB` 算，
不扣任何开销（`system.py` 的注释写着"扣 DM-RS 与控制开销"，代码没扣）。
改后走 38.214 §5.1.3.2 步骤 1：

    N_RE = min(156, 12·符号数 − N_DMRS − N_OTH) × N_PRB

新增 `linkadapt.PdschOverhead`（`pdsch_symbols=12` / `dmrs_re_per_prb=6` /
`pdcch_symbols=1`），由 `SystemConfig.pdsch_overhead` 携带，
`system` 的 legacy 主循环与 `experience.TbsLookup` **共用同一个实例**。
DM-RS 走 38.211 §7.4.1.1 的 Configuration type 1 单符号（6 RE/PRB，双符号 12）；
PDCCH 按**等效法**折成前置符号数 × 12 RE/PRB，不做 RB 级 CORESET 账本。

**S 时隙的口径同时被修正。** 改前是 `int(144 × PRB × 0.7)`，等于把
`s_slot_fraction` 直接乘在 RE 上；现在只用它折**符号数**
（`round(12 × 0.7) = 8`），DM-RS 与 PDCCH 随后只扣一次。这是物理的：
它们是每时隙固定开销，不随下行符号数缩水。

## 为什么

不扣开销让 TBS 系统性偏大约 12.5%，吞吐、PF credit、所需 RBG 数全部跟着偏乐观。
项目里本来就有正确实现 `linkadapt.re_per_slot()`（含 156 上限），
但只在 `link_to_system_mapping()` 内部用，两条主调度路径没接进去。

对照 AirView 的 `GDlScheduler.cc:2674-2695`：逐 RB 动态记账
`iTotalusedRe += pdcchReNum + dmrsReNum`。本次采用用户 2026-09-04 确认的等效法，
不做 RB 级 CORESET 精细账本。

## 证据

- **验收数字逐条对上**：100 PRB / D 时隙，`N_RE` 由 14400 降到
  `min(156, 144−6−12) × 100 = 126 × 100 = 12600`；同一 MCS/rank 下 TBS 下降
  **12.55%**（任务书给的是"约 12.5%"）。
- 156 上限没有被绕过：`PdschOverhead(pdsch_symbols=14, dmrs=0, pdcch=0)`
  每 PRB 仍是 156。
- **棘轮**（`tests/test_physics_invariants.py` 第 9 节）：判据不是"数值等于多少"，
  而是"改开销配置，两条路径的吞吐必须同比变化"。把 `_re_of` 或 `TbsLookup`
  换回硬编码 `12×12` 后实测三条同时变红，比值正好退化成 **1.000**；恢复后全绿。
- `tests/test_linkadapt.py` 第 3 节新增 `PdschOverhead` 的默认值、126 RE/PRB、
  12600 RE、12.5% 降幅、S 时隙 8 符号 / 78 RE、S/D 比 < 0.7、156 上限与
  7 组非法参数拒绝。
- 回归：test_system / test_physics_invariants / test_csi_aging / test_rng /
  test_e2e（`__main__` 入口，退出码 0）；test_linkadapt + test_carrier 37 passed；
  test_scheduler_p0 19 passed；test_scheduler_edf 45 passed；
  test_power_control 13 passed；test_developer_guide 11 passed。
  `validate_team_contract` status=pass；ruff 回到 develop 基线的 2 处。

### 更新到新基线的数值锚点（没有放宽任何断言）

| 位置 | 旧值 | 新值 | 守的性质 |
|---|---|---|---|
| `test_system` S/D 吞吐比 | 0.700 ± 0.06 | 改为**从口径算出**的 78/126 ± 0.06 | S 不是满下行 |
| `test_system` TBS 非线性度 | +1.119% | −0.027%（另加 `\|·\|>1e-5`） | TBS 对 PRB 数不线性 |
| `test_system` 频选审计队列 | 1900 B | 1700 B（**场景参数**） | 满带装得下、剩余池装不下 |
| `test_linkadapt` 预置曲线 TBS | (1729, 29722) | (1537, 26122) | 短/长 TB 查同一条曲线 |
| `test_linkadapt` 码块数 | (2, 29) | (2, 25) | 同上 |
| `test_carrier` 平台用例 | (MCS0, rank1) | (MCS0, rank2)（**场景参数**） | 平台上 first-fit 反查成立 |

## 没证明什么

- **`pdsch_symbols=12` 这个入口值本身没有被验证。** 一个 14 符号的时隙里，
  12 已经隐含扣掉了 2 个符号；现在又为 PDCCH 再扣 1 个符号的 RE。
  如果维护者认为 PDCCH 占符号 0、PDSCH 跨符号 1..13，那么应该是
  `pdsch_symbols=13`（甚至 14），TBS 会**上升**而不是下降。
  本次按任务书给定的验收数字实现，把 `pdsch_symbols` 做成了可配参数，
  **这个口径需要维护者拍板**。
- **`link_to_system_mapping()` 没有接入。** 它内部仍用
  `re_per_slot(n_prb, n_symbols=n_symbols)`，默认 `n_dmrs_per_prb=12`、
  PDCCH=0——与主路径的 6+12 是**两套口径**。任务书说不要改它，本次没改。
- **`algorithms.py:393` 的峰值速率展示没有改**（`273×12×12`）。那里是与
  TS 38.306 峰值公式对标，峰值定义本来就不含调度开销。
- **`system.py:3003` 的小包尺寸启发式没有改**（`rb_per_rbg×12×12×3.0/8`）。
  它是话务模型的包长参数，不是 TBS。
- 没有做 RB 级 CORESET 账本；没有区分 CCE 聚合等级、DCI 格式或 PDCCH 实际占用的 RB。
- 没有量化"TBS 下降 12.5% 之后，OLLA 稳态与 BLER 分布怎么变"——测试只验证
  数值与方向，没有做统计判决。

## 影响哪些 KPI

小区吞吐与用户体验速率**全面下降约 12%**（legacy 主循环实测
167.95 → 147.55 Mbps，−12.1%）。所需 RBG 数上升，PRB 利用率上升。
S 时隙相对 D 的承载从 0.70 降到 0.62，含 S 的图案（`DDDSU`）跌幅略大于纯 D。
**改前的所有吞吐结论都偏乐观，不能与改后的数字放进同一张趋势图。**
