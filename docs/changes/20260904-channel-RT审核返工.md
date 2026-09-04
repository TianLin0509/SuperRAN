# 2026-09-04 信道生成 — 按 Codex 双席审核返工 Sionna RT 调用链

**分支 / SHA**：`feat/sionna-rt-source` / `<提交后回填>`　**风险档**：红
**上一轮审核**：`C:\VibeData\Artifacts\Reports\SuperRAN\20260904-0537-reviewer-pr12-sionna-rt-review-codex.html`（BLOCKED）

## 改了什么物理机制

不是新机制，是**把配置从公开入口送到射线追踪引擎这条链修对**。上一轮审核复现的
四条缺陷有一个共同点：**失败时看起来完全正常**——跑得通、有结果、meta 也自洽，
只有物理是错的。

| # | 曾经的静默行为 | 现在 |
|---|---|---|
| 1 | `scene` 只展开成 `scenario`/`osm_path`，名字本身停在 SuperRAN-only 配置里；适配层拿不到就默认 munich。**etoile / florence / san_francisco 与本地城市全跑成慕尼黑，结果仍标 `sionna_rt`** | `plan.translate()` 把名字一并写进引擎配置；认不出的场景名**当场报错** |
| 2 | 适配层读 `scene_file`，而 `prepare_scene()` 返回的键一直是 `osm_path`。**所有本地城市场景恒被判成「资产缺失」** | 优先用上层已解析的 `cfg["osm_path"]`，没有才自己准备一次，读的也是 `osm_path` |
| 3 | 每个样本的时间轴都从 `t=0` 重来。射线追踪是确定性的，**静止 UE 的第 2、3、4… 个样本与第 1 个逐位相同** | 第 k 轮从 `k × sample_interval_s` 起算；静止且零多普勒的配置在 `iter_samples` 入口**硬失败** |
| 4 | `cfg.get(k, d) or d` 把 `rt_max_depth=0`（LOS-only 负向对照）悄悄换成 3，**对照里含三阶反射而 meta 还写着 3** | 统一走 `_cfg_num()`，**只有缺省或 None 才用默认值** |
| 5 | 开发者手册描述「slot 内 14 个 symbol 抽中间那个」的时间链和「引擎清单恒为 3」——都不是当前实现 | 改成按 slot 直接生成、引擎恒为 2；并把上面四条合同写进手册 |

## 修第 3 条时我自己又踩了一次同类坑

第一版写成：

```python
for local_index, sample in enumerate(super().iter_samples()):
    self._sample_round = (...)      # 太晚了
```

生成器在循环体执行前就已经把样本算完了，于是**信道用的是上一轮的时间原点，
而 meta 里记着这一轮**。实测：样本 0 与样本 2 逐位相同（NMSE −300 dB），
meta 却显示 `rt_sample_round: [0, 0, 1, 1]`。数字自洽，物理不对——和审核抓的
四条是同一类错误。

改成手动 `next()`、在取样本**之前**设轮次后，实测
`rt_sample_round: [0, 0, 1, 1, 2, 2]`，同一 UE 相邻两轮 NMSE 约 **−18 dB**
（正确的时间相关差异，不是独立重画）。这条顺序本身也有专门的棘轮
（`test_sample_round_advances_before_the_parent_builds_the_sample`），
用假父类把「父类看到的轮次序列」钉成 `[0,0,1,1,2,2]`。

## 证据

### 四条反例：修前失败 → 修后通过

```
反例1  requested=etoile        修前 适配器='munich'   修后 'etoile'
       requested=florence      修前 'munich'          修后 'florence'
       requested=san_francisco 修前 'munich'          修后 'san_francisco'
       拼错的 'munchen'        修前 静默跑 munich     修后 ValueError
反例2  本地资产 osm_path       修前 恒 None→资产缺失   修后 正确读到
反例3  静止 UE 样本0 vs 样本2  修前 BIT_EQUAL=True    修后 NMSE −18.6 dB
       静止且零多普勒的配置    修前 静默产重复矩阵     修后 入口 ValueError
反例4  rt_max_depth=0          修前 meta 记 3         修后 记 0
```

`rt_max_depth=0` 的端到端语义也验过（munich preset 的 6 个 UE 位置）：
LOS 可见的 UE1 从 depth=3 的 8 条径降到 depth=0 的 **1 条**（就是 LOS 那条），
UE5 从 5 条降到 1 条；LOS 被挡的 UE0/2/3/4 在 depth=0 下无径——**这是正确的
LOS-only 行为，不是 bug**。

### 棘轮：六条，红态逐条实测

`tests/test_sionna_rt_source.py` 末尾新增一节，**全部不需要 sionna**
（只看配置路由与参数解析），所以在没装 RT 的机器上也照跑：

| 棘轮 | 撤销哪个修复会变红 |
|---|---|
| `test_public_entry_carries_the_requested_scene_all_the_way_down` | 删 `plan.py` 的 `ch["scene"]` |
| `test_unknown_scene_is_a_hard_error_not_a_silent_munich_fallback` | 删 `_assert_scene_known` |
| `test_local_city_asset_is_read_from_osm_path_not_scene_file` | 把 `osm_path` 改回 `scene_file` |
| `test_zero_is_a_legal_config_value_and_survives_to_the_solver` | 把 `_cfg_num` 换回 `or` 兜底 |
| `test_deterministic_duplicate_samples_are_refused_at_the_entry` | 同上 |
| `test_sample_round_advances_before_the_parent_builds_the_sample` | 把轮次设回循环体里 |

三次红态实测：撤销 scene 透传 → 1 红；撤销 osm_path + 未知场景硬失败 → 2 红；
换回 `or` 兜底 → 2 红。每次恢复后 6 passed。

### 回归

`superran.__file__` 指向本工作区。
`__main__` 入口（退出码 0）：test_raytracing / test_physics_invariants /
test_linklevel / test_gates / test_e2e / test_interference / test_system。
pytest：test_sionna_rt_source **26 passed**、test_developer_guide 11 passed。
`validate_team_contract` status=pass。ruff 回到 develop 基线的 1 处
（`scripts/superran_board.py` F401）。

已 rebase 到 `origin/develop`（f93c528），`docs/index.html` 冲突按「重新生成」
解决，不是手工挑行。

## 没证明什么

- **本地城市场景仍然没有端到端跑过。** 本机没有登记 `rt_shanghai_lujiazui` /
  `rt_shenzhen_futian` 的实际资产，棘轮是用 mock 把 `get_scene`/`prepare_scene`
  的返回钉住的——它证明的是**键名合同接通了**，不是「这套资产能被 Mitsuba 读进去」。
  要真正闭合这一条，需要在有资产的机器上跑一次。
- **样本时间轴与父类挪 UE 位置的时钟一致，但两者与 `num_slots_per_sample>1`
  的关系没有重新推敲。** 父类每轮只把位置推进一个 `sample_interval_s`，而一个
  样本内部有 `n_time` 个间隔——`n_time>1` 时窗口会重叠。这是父类既有约定，
  本次只是跟着它，**没有验证这个约定本身对不对**。
- **静止 + 零多普勒改成硬失败，没有量化它挡掉了多少既有用法。** 仓库里的
  RT preset 都没显式设 `ue_speed_kmh=0`，所以默认路径不受影响；但外部脚本若
  依赖旧行为会直接报错。
- **没有验证多端口阵列相位与 Sionna 全阵列逐项等价**（上一轮审核也列在
  「没有证明」里，本次没有新增证据）。
- **没有做逐径阵元方向图**：阵元方向图只进链路预算的标量，没有逐径加权。
- 上一轮审核报告里 #13、#15 的问题不在本 PR 范围内。

## 影响哪些 KPI

**修前用非 munich 场景跑出来的所有 RT 结果都作废**——它们用的是慕尼黑的建筑
几何。本地城市场景此前根本跑不起来，没有历史结果。

`rt_max_depth=0` 的历史结果作废（实际跑的是 depth=3）。

静止场景下 `num_samples > num_ues` 的历史 RT 数据集，其「样本数」是虚的：
多出来的样本与第一个逐位相同。这类数据集要重新生成。

新增 meta 键 `rt_sample_round` / `rt_time_offset_s`。
