# 2026-09-04 信道生成 — 按 Codex 双席审核返工 Sionna RT 调用链

**分支 / SHA**：`feat/sionna-rt-source` / `<提交后回填>`　**风险档**：红
**第一轮审核**：`20260904-0537-reviewer-pr12-sionna-rt-review-codex.html`（BLOCKED）
**第二轮审核**：`20260904-0651-reviewer-pr12-r2-sionna-rt-review-codex.html`（REVISE）
**第三轮审核**：`20260904-0941-reviewer-pr12-cb2-final-review-codex.html`（REVISE，见文末）

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
| 真正移动 + 每 UE 多轮 + `num_slots_per_sample>1` | 父类每轮只前移**一个** `sample_interval_s`，样本却横跨 `n_time` 个间隔，相邻两轮窗口重叠（n_time=8 时重叠 7/8，16 个输出只有 9 个独特时刻），而 `system.py:1564-1574` 会直接展平当独立快照；单窗口移动多时隙合法 |

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

## 第三轮审核：守卫两处漏网 + 文档三处与实现相反

第三轮把移动相位、时间窗口和时间棘轮判为已修好，垂直相位符号也接受了
「develop 既有、不单独阻断本 PR」的定性。剩下的两类问题都是**边界没收干净**。

### 守卫漏网两处

| 反例 | 为什么溜过去 | 现在 |
|---|---|---|
| `linear` + `ue_speed_kmh=0`，两轮 | 守卫的 `moves` 只看 `mobility_mode`，而父类 `native.py:1505` 的真实条件是 `mobility_mode != "static"` **且** `speed_mps > 0.0`。名字像在动、实际位置不动，Doppler 也为零 | `moves` 改成与父类同源的两条件；棘轮直接断言父类源码里的那行条件字符串，父类改了守卫必须跟着改 |
| `static`，`num_samples=3` / `num_ues=2` | 轮数用 floor 除法算成「1 轮」，但轮转分配是 UE `[0, 1, 0]`——UE0 已经是第二轮 | 改成向上取整 `-(-n // m)` |

公开入口实跑复核（不是只调守卫方法）：

```
[OK] 反例1  linear + ue_speed_kmh=0，两轮        -> 被拒
[OK] 反例2  static，num_samples=3 / num_ues=2    -> 被拒
[OK] 合法1  static，num_samples=2，多时隙        -> 跑通，2 个样本
[OK] 合法2  linear + 30km/h，两轮，单时隙        -> 跑通，4 个样本
```

### 文档三处与实现相反——都是我上一轮写进去的

1. **README 与主手册写 `channel_source=sionna_rt`，而真实入口读的是 `source`**
   （`generate.py:844` 的 `cfg.pop("source", "internal_sim")`）。写错的键被
   静默忽略，直接跑成 `internal_sim`——用户明确要求 RT，拿到的却是统计信道，
   场景几何、径、空间结构和全部 KPI 都来自错误引擎。**这正是本 PR 一直在修的
   那类静默失败，却出现在我自己的文档里。**
2. **README 声称 `ds.paths()` 返回真实角度/时延**，而 `loader.py` 对射线追踪
   数据集抛 `NotImplementedError`。适配层把逐径几何合成成 CFR 之后就丢掉了，
   逐径角度/时延**没有落盘合同**。改成如实说明。
3. **`developer_guide_details.py` 仍留着已删除的 `time_offset_s = 轮次 × dt`
   时间语义与「未来 direct adapter」说法**，与同文件其它段自相矛盾。

### 后两条棘轮把文档钉在实现上

| 棘轮 | 撤销哪个修复会红 |
|---|---|
| `test_moving_by_name_only_is_still_refused` | `moves` 退回只看 `mobility_mode` |
| `test_non_divisible_tail_sample_counts_as_a_second_round` | 轮数退回 floor 除法 |
| `test_documented_config_key_is_the_one_the_real_entry_point_reads` | README **或**手册任一处写回 `channel_source` |
| `test_docs_do_not_promise_per_path_geometry_that_rt_never_persists` | README 又承诺 `ds.paths()` 可用 |

红态实测：五次变异各自只红对应项，恢复后 33 passed。

### 第三轮回归

已 rebase 到 `origin/develop`（**7d3dad1**，比审核报告里的 fd70f49 又前进了
一个 #22），**无冲突**。pytest **252 passed**；7 个 `__main__` 入口退出码 0；
`validate_team_contract` pass。

ruff 4 处，**全部在本 PR 没碰过的文件里**（`scripts/superran_board.py`、
`superran_task.py`、`superran_tasks.py`），后两个是随 develop 新提交进来的。

## 影响哪些 KPI

**上一轮（`603022e`…`927c1bd`）跑出来的移动 UE RT 结果作废**：等效 Doppler
被算了两倍。静止场景不受影响（那些配置现在直接报错）。

修前用非 munich 场景跑出来的所有 RT 结果作废——它们用的是慕尼黑的建筑几何。
`rt_max_depth=0` 的历史结果作废（实际跑的是 depth=3）。

meta 键：新增 `rt_sample_round`（诊断标签）与
`rt_sample_time_origin="per_sample_zero"`；**移除** `rt_time_offset_s`
（那个机制已经撤掉）。

## 需要维护者决定

1. **父类轨迹时钟的窗口合同。** 真正移动 + 每 UE 多轮 + `num_slots_per_sample>1` 时，
   位置每轮前移一个 `dt` 而样本横跨 `n_time` 个 `dt`。建议改成按完整 sample
   跨度前移（`travel = speed × dt × n_time × round`）。这会同时改 internal_sim
   与 RT 的语义——虽然仓库里现有 preset 和落盘 artifact 的
   `num_slots_per_sample` 全是 1，blast radius 为零，但它是跨引擎合同。
2. **`_spatial_panel_response` 垂直相位符号。** 上面已量化，改完与权威模型
   残差 0.0000。它会改掉所有 64T/256T 的既有 CDL 基线，需要单独 PR 与
   基线重签。

## 维护者授权后的最终收口

维护者明确授权本轮直接修改 PR #12 并在独立双审通过后合入。收口只处理第四轮
审核仍未闭合的边界，不夹带垂直相位修复：

1. `generate()` 对历史错误键 `channel_source` 直接报错并给出
   `source='sionna_rt'` 迁移方式，禁止静默落回 `internal_sim`。
2. 移动多时隙守卫只在**确有第二轮**时拦截；单窗口
   `linear + 30 km/h + T=2` 没有跨轮重叠，必须放行。
3. 主手册删除“第 k 轮从 k×dt 起算”和“跨轮 NMSE −18 dB 是正确结果”的旧说法；
   `_sample_round` 只作 meta 标签，不进入物理时间轴。
4. `CLAUDE.md` 统一为 direct adapter 已实现、引擎清单恒为两条。

棘轮同时进入 `tests/test_sionna_rt_source.py` 与
`tests/test_physics_invariants.py`：错误键必须硬失败，单窗口移动多时隙必须放行，
主手册与 `CLAUDE.md` 不得恢复旧合同。

## 双席再审后的文档合同收口

独立物理席与集成席都确认实现反例已经正确，但抓到用户文档仍有两处
与真实行为相反：一是还说 `channel_source` 会被静默忽略，二是把所有
移动多时隙都写成非法。现已同步修正 README、紧凑手册、详细手册与生成页：

- 旧错键会立即硬失败并提示迁移，不再描述成静默回落。
- 只在「真正移动 + 每 UE 多轮 + 多时隙」时存在跨轮窗口重叠；
  `num_samples<=num_ues` 的单窗口移动多时隙明确标为合法。
- 两条文档语义棘轮分开编写：分别恢复旧错键说法和过度拒绝说法时，
  会各自独立变红。

集成席在复审时又从详细手册的验收清单里找到第二个过宽旧句。该处已改成
「真正移动 + 每 UE 多轮 + 多时隙」，文档棘轮也由「正确句存在」加强为
「正确句存在，且所有已知过宽旧句为零」。
