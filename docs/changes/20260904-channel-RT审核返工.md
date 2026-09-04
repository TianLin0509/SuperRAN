# 2026-09-04 信道生成 — 按 Codex 双席审核返工 Sionna RT 调用链

**分支 / SHA**：`feat/sionna-rt-source` / `<提交后回填>`　**风险档**：红
**第一轮审核**：`20260904-0537-reviewer-pr12-sionna-rt-review-codex.html`（BLOCKED）
**第二轮审核**：`20260904-0651-reviewer-pr12-r2-sionna-rt-review-codex.html`（REVISE）

## 改了什么物理机制

不是新机制，是**把配置与时间语义从公开入口送到射线追踪引擎这条链修对**。
两轮审核复现的缺陷有一个共同点：**失败时看起来完全正常**——跑得通、有结果、
meta 也自洽，只有物理是错的。

### 第一轮修的四条（已通过复审）

| # | 曾经的静默行为 | 现在 |
|---|---|---|
| 1 | `scene` 只展开成 `scenario`/`osm_path`，名字本身停在 SuperRAN-only 配置里。**etoile / florence / san_francisco 与本地城市全跑成慕尼黑，结果仍标 `sionna_rt`** | `plan.translate()` 把名字一并写进引擎配置；认不出的场景名**当场报错** |
| 2 | 适配层读 `scene_file`，而 `prepare_scene()` 返回的键一直是 `osm_path`。**所有本地城市场景恒被判成「资产缺失」** | 优先用上层已解析的 `cfg["osm_path"]` |
| 3 | 每个样本的时间轴都从 `t=0` 重来，**静止 UE 的多个样本逐位相同** | 见下——第一轮我修错了方向，第二轮改成入口拒绝 |
| 4 | `cfg.get(k, d) or d` 把 `rt_max_depth=0`（LOS-only 负向对照）悄悄换成 3 | 统一走 `_cfg_num()`，只有缺省或 None 才用默认值 |

## 第一轮我把第 3 条修反了

第一轮我让第 k 个样本从 `k × sample_interval_s` 起算，想以此消除静止 UE 的
重复样本。**这是错的，而且错得比原来的 bug 更隐蔽。**

**父类已经把 UE 位置推进了 `round × dt`**（`native.py:1497-1512`），RT 在新位置
重追出来的径里**已经含这段几何相位**；我再叠加同样时长的 Doppler 相位，就是
把移动相位算了两遍。

端到端实测（munich，linear 30 km/h，heading 0°，dt=5 ms，depth=3，
同一个 UE 的第 0 轮 vs 第 1 轮）：

```
现状（时间原点已撤掉）      相位步进 +0.795264 rad   NMSE(轮0 vs 轮1) -1.55 dB
撤销修复（接回时间原点）    相位步进 +1.594551 rad   NMSE(轮0 vs 轮1) +3.08 dB
额外被叠上去的相位 = +0.799287 rad
比值（双算 / 单算） = 2.0051
```

**等效 Doppler 接近翻倍**，相干时间偏短，CSI 老化被系统性夸大，协方差、
rank/MCS、BLER 与移动性结论全部受影响。

它还有第二个后果：**把 `static` 偷偷改成了连续轨迹**。CLAUDE.md 的跨引擎合同
（1188-1191、1414-1416）明确写着 `static` 只固定几何位置、各样本小尺度实现
**独立**；连续轨迹只属于非 static。我那个改法让 CDL 与 RT 的 A/B 不再是
「只换信道矩阵」。

### 现在怎么做

**撤掉时间原点，样本内部时间轴恒从 0 起算，与 CDL 逐字一致**
（`native.py:1279` 的 `times = np.arange(n_time) * interval`）。
样本之间的差异只来自父类挪位置后 RT 重追的几何，不叠加任何额外相位。

静止 UE 多样本重复这个问题，**正确的解法不是偷偷改时间轴，而是在入口拒绝**：
RT 是确定性引擎、没有 CDL 的 `rng_small` 随机源，它**产不出**独立实现，
那就不要假装产得出。

## 守卫要拦全（第二轮补的）

上一版守卫只在 `static + ue_speed_kmh=0` 时才拦，漏掉两条：

| 配置 | 为什么必然重复 |
|---|---|
| `static` + 多轮，**任意速度** | 几何固定、两轮走的又是同一段 `[0, n_time×dt)`，输出逐位相同。给多普勒救不回来 |
| `num_slots_per_sample>1` + `ue_speed_kmh=0` | 所有径 Doppler 为 0，时间相位恒为 1，样本内部 N 个 slot 逐位相同。旧守卫在 `rounds<=1` 时直接 return，完全漏掉 |
| 非 static + `num_slots_per_sample>1` | 父类每轮只前移**一个** `sample_interval_s`，样本却横跨 `n_time` 个间隔，相邻两轮窗口重叠（n_time=8 时重叠 7/8，16 个输出只有 9 个独特时刻），而 `system.py:1564-1574` 会直接展平当独立快照 |

三条都在 `iter_samples` 入口硬失败，错误信息各自给出出路。

第三条**本可以直接修**（把父类的 `travel` 从 `dt × round` 改成
`n_time × dt × round`），但那要改父类的轨迹时钟、同时影响 internal_sim 与 RT
两个引擎的跨引擎合同，**需要维护者定案，不在本 PR 范围内**。在定案前 RT 拒绝
产出这种数据，而不是静默产出重叠窗口。

## 棘轮改成钉住真实合成输出

第二轮审核指出：上一轮六条棘轮**只看配置路由**，把真实合成里的
`time_offset_s` 手工改回 0，两条声称守时间修复的测试**仍然是绿的**。这条批评
成立——它们守的是「配置有没有传到」，不是「信道对不对」。

现在的时间类棘轮直接驱动 `synthesize_channel`（纯 numpy、不需要 sionna）：

| 棘轮 | 钉的不变量 | 撤销哪个修复会红 |
|---|---|---|
| `test_sample_internal_time_axis_starts_at_zero_like_the_cdl_path` | `t=0` 时 Doppler 项恒为 1，**第 0 个 slot 的值不依赖 `doppler_hz`**；外加签名与调用点不许再出现时间原点 | 时间轴加任何非零原点 |
| `test_doppler_phase_advances_exactly_once_per_slot` | 相位每 slot 只推进 `2π f_d dt`，一次（解析值比对） | 时间相位算两遍 |
| `test_static_multi_round_is_refused_even_with_doppler` | static+多轮在任意速度下都被拒；并**先证明**「逐位相同」是真的 | 守卫退回只拦零速度 |
| `test_multislot_duplicates_and_window_overlap_are_refused` | 多时隙零多普勒、非 static 多时隙都被拒；合法组合不误伤 | 去掉任一条检查 |
| `test_sample_round_is_labelled_before_the_parent_builds_the_sample` | 轮次标签在父类算样本**之前**设好 | 轮次挪到样本之后 |

红态实测（逐条撤销 → 只红对应那条，恢复后 29 passed）：

```
撤销：样本时间轴又从轮次原点起算    3 failed  -> time_axis_starts_at_zero / doppler_advances_once
                                              / synthesis_matches_sionna_own_frequency_response
撤销：时间相位被算两遍              3 failed  -> doppler_advances_once / doppler_becomes_a_time_phase_ramp
                                              / synthesis_matches_sionna_own_frequency_response
撤销：守卫退回上一版                1 failed  -> static_multi_round_is_refused_even_with_doppler
撤销：不再检查样本内部多时隙重复    1 failed  -> multislot_duplicates_and_window_overlap_are_refused
撤销：不再检查跨轮窗口重叠          1 failed  -> multislot_duplicates_and_window_overlap_are_refused
撤销：轮次设在父类算完样本之后      1 failed  -> sample_round_is_labelled_before...
                                              （assert [0,0,0,1,1,2] == [0,0,1,1,2,2]）
```

注意前两条把 `test_synthesis_matches_sionna_own_frequency_response` 也带红了——
那是与 Sionna 自己 `Paths.cfr()` 的对拍，**本机装了 sionna-rt**，这条对拍是真跑的。

## 回归

`superran.__file__` 指向本工作区。
pytest：**248 passed**（含 `test_sionna_rt_source` 29、`test_developer_guide` 11）。
`__main__` 入口全部退出码 0：test_raytracing / test_physics_invariants /
test_linklevel / test_gates / test_e2e / test_interference / test_system。
`validate_team_contract` status=pass。
ruff 回到 develop 基线的 1 处（`scripts/superran_board.py:13` F401）。

## 没证明什么

- **多端口垂直阵列相位与物理位置符号相反——这是 develop 上的既有缺陷，
  不在本 PR 修。** 审核复现属实，我独立量化了一遍：把
  `_spatial_panel_response` 的垂直相位符号翻过来之后，复合响应与权威模型
  `EffectiveArray.effective_tx_steering`（`coupling_matrix` + 真实
  `physical_positions_lambda`）**逐项完全吻合**：

  | rf_shape | 1驱M | 下倾 | 现状最差残差 | 翻符号后 |
  |---|---|---|---|---|
  | (2,2,1) | 1 | 0° | 0.8694 | **0.0000** |
  | (2,2,1) | 1 | 6° | 0.8694 | **0.0000** |
  | (4,2,1) | 3 | 6° | 0.8142 | **0.0000** |
  | (8,2,1) | 3 | 6° | 0.8142 | **0.0000** |
  | (8,4,1) | 3 | 6° | 0.8271 | **0.0000** |

  根因：`native.py:1020` 的垂直项按 `+v·sin(elevation)`（阵元下标向上increase），
  而同一个类的 `physical_positions_lambda()`（`native.py:587-596`）用的是
  `top_to_bottom`——`z = z0 − v·d`，下标向下。两者符号相反。

  **它是 develop 的既有代码，不是本 PR 引入的**：`origin/develop` 的
  `native.py:1020` 就是这一行，来自 PR #7（`e5c7c20`）。
  **它影响两个引擎**：`native.py:1354/1375`（internal_sim/CDL）与
  `sionna_rt.py:260/273`（RT）调的是同一个函数。
  **而且是活的、不是潜伏的**：仓库里的 BS 面板形状 `[8,4,2]`（64T）与
  `[16,8,2]`（256T）垂直 RF 端口数都 ≥ 2。

  固定子阵那一层是**对的**（实测 `fixed_subarray_response` 与
  `coupling_matrix` 的峰值仰角都等于 `−downtilt`，0°/6°/12° 三档都对）；
  错的只有 RF 端口的垂直下标方向。

  修它会改掉所有既有 CDL 基线，跨引擎、跨冻结产品检查，属于独立 PR
  + 维护者定案。本 PR 只把证据留在这里，**不夹带**。因此本 PR 不新增
  多端口阵列对拍棘轮——那条棘轮应该和修复一起进它自己的 PR。

- **本地城市场景仍然没有端到端跑过。** 本机没有登记 `rt_shanghai_lujiazui` /
  `rt_shenzhen_futian` 的实际资产，棘轮是用 mock 把 `get_scene`/`prepare_scene`
  的返回钉住的——证明的是**键名合同接通了**，不是「这套资产能被 Mitsuba 读进去」。
- **`num_slots_per_sample>1` 与多轮的全局时间轴没有做到严格单调**，只是被
  拒绝了。真正的修法在父类轨迹时钟，见上。
- **`static` 在 RT 下彻底不支持多轮**，没有量化这挡掉了多少既有用法。仓库里的
  RT preset（`_rt_cfg`：num_samples=num_ues=2）不受影响。
- **没有验证多端口阵列相位与 Sionna 全阵列逐项等价**（两轮审核都列在这里，
  本次仍未新增证据——见上面那条，先要定 develop 的符号）。
- **没有做逐径阵元方向图**：阵元方向图只进链路预算的标量，没有逐径加权。

## 影响哪些 KPI

**上一轮（`603022e`…`927c1bd`）跑出来的移动 UE RT 结果作废**：等效 Doppler
被算了两倍。静止场景不受影响（那些配置现在直接报错）。

修前用非 munich 场景跑出来的所有 RT 结果作废——它们用的是慕尼黑的建筑几何。
`rt_max_depth=0` 的历史结果作废（实际跑的是 depth=3）。

meta 键：新增 `rt_sample_round`（诊断标签）与
`rt_sample_time_origin="per_sample_zero"`；**移除** `rt_time_offset_s`
（那个机制已经撤掉）。

## 需要维护者决定

1. **父类轨迹时钟的窗口合同。** 非 static + `num_slots_per_sample>1` 时，
   位置每轮前移一个 `dt` 而样本横跨 `n_time` 个 `dt`。建议改成按完整 sample
   跨度前移（`travel = speed × dt × n_time × round`）。这会同时改 internal_sim
   与 RT 的语义——虽然仓库里现有 preset 和落盘 artifact 的
   `num_slots_per_sample` 全是 1，blast radius 为零，但它是跨引擎合同。
2. **`_spatial_panel_response` 垂直相位符号。** 上面已量化，改完与权威模型
   残差 0.0000。它会改掉所有 64T/256T 的既有 CDL 基线，需要单独 PR 与
   基线重签。
