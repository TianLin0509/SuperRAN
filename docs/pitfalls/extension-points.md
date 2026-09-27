# 加东西的地方

> 本文件由 `CLAUDE.md`「加东西的地方」整节搬出（2026-09-25），正文原样。

### 多载波 CA

显式 CA 入口与旧单载波固定格栅分开，使用说明见 `docs/ca.md`。
`ca.py` 定义载波、输入身份与整数配额；`ca_engine.py` 协调原 experience 状态机；
`ca_server.py` 复用原 MCP 数据准备与独立建表。不得另复制一套 PHY 或 busy-period 公式。
载波事件用 `RngBook.namespaced_generator` 在原用途流下派生，不修改全局登记表。
改动以上模块运行 `test_ca`、`test_system`、`test_scheduler_p0`、`test_scheduler_edf`、
`test_csi_aging`、`test_rng`、`test_carrier`、`test_system_sim_tool` 和 `test_physics_invariants`。
关闭 CORT 扩 RBG 时，棘轮必须在实际首传净荷上变红；缺 API 的 ImportError 不算反证。

### 其他扩展

- 新的 3GPP 校准量 → `calibration.py`，按条款号标注来源
- 新的 MCS/CQI 表或 TBS 分支 → `linkadapt.py`，**标准表必须过 `verify_tables` 的内蕴自检**
- 改分析 BLER 参数 → 跑 `anchor_check`，门限要落在公开 NR 曲线的常见区间
- 新的表驱动 BLER → 原始常量放独立数据模块，必须有 SHA-256、全 MCS 覆盖、
  横轴/BLER 单调、目标门限覆盖检查；来源不是标准就不能塞进 `verify_tables`
- 新的门禁判据 → `gates.py`（门 2/门 3）或 `validate.py`（门 1，会自动进门 1）
- 新的 rank 策略 / HARQ 反馈时序 → `amc_policy.py`；只有一条评估路径，实现只该有
  一份，别在 `system.py` 和 `experience.py` 里各写一套
- 新的随机流 → `rng.register_stream(名字, 用途)`，**别直接改 `STREAMS` 的顺序**
  （流键来自名字的 crc32，加流不会扰动已有流；改顺序也不会，但改用途会）。
  统计判决一律走 `rng.compare_replications`，它复用 `gates.py`，不要另写
- 新的外部结果校验 → `results.check_pairable`，每条都必须是**硬拦截**不是告警
- 新指标 → `analysis.KNOWN_METRICS` 加单位；自定义指标也支持，单位由调用方给
- 新的标准查表值 → `spec38901.py`，**必须两条独立路径核对过**才录入
- 新的默认硬件/载波口径 → `hardware.py`，它是**默认配置的唯一真相源**
- 新的说明书示意图 → `spec.py` 加一个 `_svg_*` 函数并挂进 `render_html`；
  **画的必须是实际会跑的配置**，不是用户以为的那个
- 新的可在页面上改的参数 → `spec._EDITABLE`，**只加这一处**：控件、payload、
  回传白名单都从它派生（`spec.editable_keys()`）
- 新场景 → `presets/presets.yaml`，不改代码。**加完必须跑一遍 `sr_probe_scenario` 把实测值写进 `expect`**——preset 里的 label 是设计意图，写着「高干扰」实际只有 2 dB 的事发生过
- 新的干扰量或分级 → `interference.py`，门限改动等于改现场约定，先和用户对齐
- 新射线追踪城市 → 独立数据目录并用 `SUPERRAN_SCENES` 指向；不得放进别的源码仓库
- 新任务类型 / 新决策点 / 新对比组 / 新陷阱 → `decisions.py` 的
  `ALL_DECISIONS`、`_DESIGN`、`TASK_PROFILES`
- 新测量量 → `measure.py` 加函数 + `MEASUREMENT_CATALOG` + `_ALIASES` +
  `deliver.py` 的 `_BLOCKS` + `loader.py` 的方法

`_ALIASES` 加自然语言别名时注意：只有长度 ≥ 2 的别名参与子串匹配，
且别用"功率"这种过泛的词（会被"时延功率谱"误命中）。
