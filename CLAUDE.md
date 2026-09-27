# SuperRAN 开发规范

## Agent 协作入口（先读这里）

SuperRAN 由**一位维护者**（无线通信工程师）主导，Agent 是执行者不是决策者。
所有协作规则在 `.agents/` 目录，**开工前必读，不要按聊天里粘贴的 prompt 工作**：

- 实现任务（工作位）：`.agents/AUTHOR.md`
- 独立验证并合入主干（合并位）：`.agents/MERGER.md`
- 需要几个 Reviewer：`.agents/RISK.md`（按文件路径查表，不许自己估）
- 主干闸门与项目配置：`.agents/project.json`（主干名、合并前跑哪些测试、worktree 放哪）
- 阿里云跨电脑交接：`.agents/SYNC.md`；唯一日常远端为项目配置里的私有 Gitea，GitHub 仅保留历史读取
- 仿真设计、数据生成或性能结论：`skills/channel-sim/SKILL.md`

三条铁律：**一个提交只动一个物理机制**；**审核发现的物理 bug，修复时必须补一条
“revert 掉就会变红”的测试**并入 `tests/test_physics_invariants.py`；**不许静默降级**，
用了工程近似要写进报告的“没证明什么”。

报告统一用 `python scripts/make_agent_report.py <report.json>` 生成，字段照抄
`.agents/report.example.json`，**不要手写 HTML**。看当前状态用
`python scripts/superran_board.py`。

`develop` 是唯一开发主线；每台电脑首次从阿里云 clone 一份主仓库，路径由本机决定。
同一台电脑的并行任务用独立 worktree，不重复 clone；首次接入见 `INSTALL_AGENT.md`。
工作位上传候选分支，由不同会话的合并位拉取同一完整 SHA、独立审核并运行本地闸门，
再按回执发布到云端；跨电脑传输不依赖共享 `.git`。主仓库禁止直接提交。

> `skills/superran-lead/` 与 `skills/superran-member-task/` 是已废弃的多人「组长-组员」
> 流程（含 FORMAL/REHEARSAL 模式、Fork 推送等），**不要再按它们工作**，一律以
> `.agents/` 为准。

与人交流采用“双层表达”：保留准确技术术语，同时紧跟一句白话解释；关键物理概念再给
一个贴近当前任务的小例子。例子只帮助理解，不能冒充代码事实、测试证据或性能结论。
首次使用缩写时展开全称；实现前用“我理解为……，不等于……”复述边界。专家已明确
理解时不要反复教学。

## 项目定位

SuperRAN 是独立维护、独立演进的 Agent 式无线仿真平台。信道轴序、
TDD 互易、预编码、功率约束、RBG/TBS、链路自适应和 KPI 都以
SuperRAN 本仓的合同为准。统计信道生成、CDL/TDL 表、NR 载波/TDD、参考序列、
阵列、LMMSE 与几何 S/N/I 均由 ``src/superran`` first-party 实现；运行时不得
搜索、导入或修改 MSG-Platform / ChannelHub 源码树。

``channelhub.py`` 只保留为历史 Python API 的兼容门面，实际实现固定指向
``native.py``。历史环境变量 ``SUPERRAN_CHANNELHUB`` 被有意忽略，不能改变生成字节。
Sionna RT 只能作为显式 direct optional adapter 接入；不可用时硬报告，
不得退回另一个仓库。**QuaDRiGa 路线已于 2026-09-04 明确不做并从代码与文档中删除**
（需要 MATLAB/Octave 运行时，成本与收益不成比例）；要空间一致性就按 38.901 §7.6.3
自己实现一个子集。

**默认信道是 CDL**（``internal_sim``）。``sionna_rt`` 是本仓自己的直连适配层
（``src/superran/sionna_rt.py``），装了 sionna-rt 才可用，必须在配置里显式写
``source: sionna_rt`` 才会走。引擎清单恒为这两条。

## 环境

- Python ≥ 3.10，需要 numpy / scipy / pydantic v2 / pyyaml / structlog / mcp
- 射线追踪需 `pip install sionna-rt`（连带 mitsuba + drjit，约 300 MB）；
  direct adapter 已实现，但本地 OSM 资产、材料标定与多端口垂直相位仍须按报告边界验证

## 测试

```bash
python tests/test_e2e.py                    # 端到端
python tests/test_mcp_server.py             # MCP 全链路
python tests/test_raytracing.py             # 射线追踪与决策层
python tests/test_linklevel.py              # 谱效、可信度、物理层、IRC
python tests/test_gates.py                  # 校准、标准表、三道门、统计判决
python tests/test_results.py                # 外部算法结果契约、预注册
python tests/test_linkadapt.py              # 链路自适应、吞吐、并行生成
python tests/test_mumimo.py                 # MU-MIMO、单码字、RBG 粒度
python tests/test_system.py                 # 系统级仿真（单路径；容量=full_buffer 话务）
python tests/test_scheduler_p0.py           # 调度资源账本、频选、MU 评分与 Finalizer
python tests/test_srs_resource.py           # PCI 模3、4 CS、2T4R 双腿与全局周期容量
python tests/test_srs_waveform.py           # RE级波形、解扩、CFO/时偏与UL IoT证据
python tests/test_interference.py           # IoT、预设、说明书、算法页、文档计数
python tests/test_csi_aging.py              # CSI 时延、SRS 跳频、真实/估计视角
python tests/test_rng.py                    # 随机数分流、重复实验、CRN、置信区间
python tests/test_sysscenes.py              # 系统场景预设与成对受控性
python tests/test_power_control.py          # EBF/PEBF/NEBF 与逐 RB 功率耦合
python tests/test_physics_contract_extensions.py # 快照时钟、SRS测量口径与场景资产合同
python tests/test_physics_invariants.py     # 极化、子阵、SRS/LMMSE 物理不变量
python tests/test_channel_generation_contract.py # first-party 信道生成合同与最小网格
python tests/test_native_independence.py       # 外部根/导入阻断、v1/v2互易、工具清单
python tests/test_developer_guide.py         # 开发者文档覆盖、离线结构与漂移检查
python tests/test_carrier.py                 # 载波栅格、Type-0 边界、本地 TDD 合同
python tests/test_ca.py                      # 共享队列 CA、整数分流、CORT 扩容与跨载波隔离
python tests/test_company_256t.py            # 256T 阵列与码本
python tests/test_system_sim_tool.py          # sr_system_sim 行为级（硬失败路径）
python tests/test_benchmarks.py               # 预注册经典通信基准与 provenance
```

当前共 **30 个可执行测试文件**。**两种执行方式必须看到同一个真理**：
pytest 原生文件都有 `__main__` 入口（直接 `python tests/test_x.py` 不再是
0 检查假绿）；脚本式文件必须在 pytest 收集/薄壳路径中同样以异常或非零退出
传播失败，不能只在 `if __name__ == '__main__'` 里检查全局 FAILED。
新增测试文件时两个入口都要，别只加一种。
不要手写"总检查项"——循环内检查数会随配置展开，
静态 `check()` 调用点也不等于运行时检查数；以实际运行输出和开发者文档自动盘点为准。

改动 `measure.py` / `generate.py` / `plan.py` / `decisions.py` / `scenes.py`
后前三个都要跑；改动 `linklevel.py` / `validate.py` / `calibration.py` /
`gates.py` / `spec38901.py` 要跑 test_linklevel + test_gates；
改动 `results.py` / `analysis.py` / `loader.py` 要跑 test_results；
改动 `interference.py` / `scenario.py` / `presets.yaml` / `spec.py` / `bridge.py`
要跑 test_interference（说明书与回传桥都在它第 9 节）；
改动 `mumimo.py` / `beamforming.py` / `power_control.py` 要跑 test_mumimo +
test_power_control + test_physics_invariants；
改动 `system.py` / `experience.py` / `traffic.py` / `kpi_view.py` / `kpi_compare.py` 要跑 test_system +
test_csi_aging + test_rng；改动 `scheduler_*.py` / `experience.py` 要额外跑 test_scheduler_p0 + test_scheduler_edf；
改动 `csi_aging.py` / `srs_resource.py` 要跑 test_csi_aging + test_srs_resource；
改动 `srs_waveform.py` 或 SRS 的 `h_ul_true` 数据合同要跑 test_srs_waveform +
test_physics_contract_extensions + test_channel_generation_contract + test_results；
改动 `scenes.py` / `scene_assets.py` 要跑 test_physics_contract_extensions + test_raytracing；
改动 `amc_policy.py` 要跑 test_system + test_scheduler_p0 + test_csi_aging；
改动 `native.py` / `channelhub.py` / `physical.py` 要跑 test_native_independence +
test_channel_generation_contract + test_physics_invariants + test_linklevel + test_gates；
改动 `sionna_rt.py` 或 `native.py` 里的 `_small_scale_channel` / `_spatial_panel_response` /
`fixed_subarray_response` 要额外跑 test_sionna_rt_source + test_raytracing；
改动 `rng.py` 要跑 test_rng + test_system；
改动 `algorithms.py` / `algo_defs*.py` 要跑 test_interference（算法页签在它第 9.10 节）；
改动开发者文档生成器要跑 `tests/test_developer_guide.py`。

经典通信基准先读 `presets/classic_benchmarks.json` 的冻结判据，再运行
`python scripts/run_classic_comm_benchmarks.py`。它验证解析关系、标准合同与机制，
不是现场性能门；新增 case 必须先改 spec 并评审，不能看完结果后改判据。
夜间全回归用 `python scripts/run_test_matrix.py --tier full`：逐文件心跳、超时、
独立日志和 JSON 终态，避免 monolithic pytest 在收集阶段执行顶层脚本却长时间无输出。

新数据集在 `summary.json.provenance` 保存 commit、dirty diff SHA-256、依赖和预置
BLER 哈希；系统仿真把数据集 provenance 与当前 runtime 对账。`mismatch` 时只能做
历史复现，正式结论应重新生成；旧数据缺 provenance 时必须报 `unknown`，不能当 match。

公式渲染是**两层**：`katex.py` 内联 KaTeX（628 KB，资产由
`python scripts/vendor_katex.py` 生成）负责排版，`mathml.py` 是没有 JS 时的兜底，
两份内容一起写进每个 `.kx` 容器。改公式后要用 Node 真跑一遍
（`katex.renderToString(..., {throwOnError:true})`）——MathML 解析器容忍的写法
KaTeX 未必收，光看 Python 源码看不出来。

### 主手册首先是算法阐释，其次才是开发者索引

主手册的直接读者包括只关心通信原理和仿真方案的需求者，不能默认他们想看类名、字段和
源码路径。每个关键技术章节必须按以下顺序组织：

1. 先讲要解决的无线问题、物理因果和适用场景；第一屏不得以代码结构开场。
2. 再讲算法输入/输出、资源维度、公式逐符号解释、完整配置表、逐步例子和取舍边界。
3. 标准定义与本项目工程预置必须分栏；不得把预置表冒充 3GPP 强制算法。
4. 最后才给实现映射、类/函数/文件与反向测试，优先放进可折叠的“开发者实现”区域。
5. 复杂因果链必须优先给一个可手算的 toy example：用最小维度、明确数字逐步走过
   输入→中间量→输出，再说明怎样扩展到真实 64×4/多小区；不能只写抽象公式或字段。
6. toy example 必须把“当前已端到端接通”“独立存在但未桥接”“仅验证方向的 proxy”
   分开标注，避免用户把机制示例误读成现场收益或已实现能力。

凡是表驱动算法（PCI 模3、MCS/CQI、BLER、TDD/SRS 资源等），文档表必须从代码的唯一
真相源生成，并有逐格一致性测试。只写“见代码”、只给字段清单，或用 metadata 表替代
算法解释，都不算文档完成。

## 物理内核与可选后端的边界

| 外部部分 | 怎么对待 |
|---|---|
| 部分 | 怎么对待 |
|---|---|
| `src/superran/native.py` | first-party 统计信道和 PHY 窄腰；本仓真相源，**默认引擎** |
| `channelhub.py` | 只作旧 API 名兼容，不发现外部源码 |
| `src/superran/sionna_rt.py` | 本仓自己的 Sionna RT 直连适配层；只换信道矩阵，阵列/大尺度/KPI 口径全部共用 |
| QuaDRiGa | **不做**。已从代码与文档删除，不要再当作待办 |
| 平台后端、训练、数据库、任务队列 | 不纳入；数据直接按 SuperRAN 合同落盘 |
| 特征桥 / MAE token | 不纳入；只输出未归一化、未截断、未门控的物理量 |
| source `w_dl` | 不接受；只从本地 `h_est` 重算 EBF/PEBF/NEBF |

## 四条不可动摇的约定

1. **不传数据**。MCP 只回句柄、摘要、取货代码。
2. **给物理量**。不归一化、不截断、不门控。单位标在函数文档里。
3. **生成与取货解耦**。测量量从信道现算，改主意重新 deliver，不重跑仿真。
4. **TDD 系统格栅固定**。100 MHz @ 30 kHz，272 RB = 17 RBG × 16 RB；
   273 是标准表对照值，不进入当前系统分母。其他带宽可用于链路级，
   但 `sr_system_sim` 必须硬拒绝，页面不开放 `num_rb/rbg_size_config`。

## 踩过的坑：索引（删之前先想清楚）

正文按主题放在 `docs/pitfalls/`，每条都是真实事故。**碰到下面列出的模块或场景，先读对应文件里
那一条的全文再动手**；标题保留原文，在文件里直接搜标题即可定位。改口径时同步改条目正文和这里的一句话要点，
不要只改一处。

### 随机数、重复实验与统计判决 → `docs/pitfalls/stats-rng.md`

触发：改 `rng.py` / `gates.py` / `results.py`，或要下任何 A/B、算法对比、KPI 提升结论。

- 系统级 KPI 不带置信区间就是在报噪声 —— 只改种子 CV 就有 9.4%/18.6%；KPI 报 mean/std/ci95/n_rep，统计复用 `gates`
- 多算法 KPI：算法不是 Tab，单 TTI 不是结论 —— 算法=贯穿全页的固定颜色系列；sidecar 逐位对齐；Holm 只收紧；n≤5 永不显著
- `seed + 1` 的问题是撞车，不是相关 —— 重复实验换 `replication`，别写 `seed+1`；外部 int 走 `RngBook.integer_seed()`
- 随机流要按用途分开，共用一个 rng 会串味 —— 五条流；流键用 `crc32(名字)`、显式 `spawn_key`，别改
- A/B 不用公共随机数等于白白把区间放宽 4 倍 —— 两臂用同一个 `rng.replications()`；查不到 CRN 返回 None 不是 True
- 建表与随机种子无关，所以只建一次 —— 重复只重跑 TTI 主循环；邻区负载抖动冻结在表里、不进 CI
- 配对的有效性靠样本 ID，统计查不出错位 —— `check_pairable` 逐个比 ID；`register` 拒 nan/inf
- 外部结果的 CSI 口径只能靠声明 —— 门 2 只能查 `method_metadata` 声明，查不到给 warn，别假装查过
- 预注册只在生成前绑定才有意义 —— 不加事后补绑接口；未绑定=`unregistered` 不是 `primary`
- 门 3 的判决必须显式说清用哪个检验 —— 以 Wilcoxon 为准；`statement` 写明检验；`test_gates` 6.5 节样本别删
- 零方差差值要分两种情况 —— 差值恒 0 → p=1；恒为非零常数 → p=0

### 链路自适应：BLER / CQI / OLLA / HARQ / rank → `docs/pitfalls/link-adaptation.md`

触发：改 `linkadapt.py` / `bler_curves.py` / `amc_policy.py` / `csi_aging.py`，或 `system.py`/`experience.py` 的 AMC、HARQ 部分。

- QAM 互信息的 sigma 定义差一倍就是 3 dB —— `sigma=1/sqrt(γ)`；低 SNR 对香农自检
- BLER 有分析模型和预置曲线两条后端，别混成一种证据 —— 表1/2 解析、表3 `preset_20b`；HARQ 只一次重传（用户确认）；进程默认 8；`target_bler` 区间；TDD AMC 链
- 解码 SINR 要取实际授予的那几个 RBG —— 误块抽签=最终 MCS+本次授予 RBG 上的真实 SINR，不用全带均值
- CSI 老化：零时延恒等式是地基，rank 必须由基站自己选 —— 零时延逐位退化；rank 由陈旧 CSI 选；默认 fixed rank2；adaptive 三层时间尺度，回退连 OLLA 一起退
- SINR_AMC_PRED 是 CQI 门限 + BF Gain，不是物理发送/接收 SINR —— CQI 一阶 IIR λ=0.25、事件驱动、周期跟 CSI 报告；BF Gain 只能用 `h_prec`；BLER 只用 final MCS+`SINR_*_RX`
- OLLA 是 MCS-domain 状态，步长比由目标 BLER 反解 —— `δ_down=δ_up(1−p)/p`；关 OLLA 不换决策坐标；ACK/NACK 等 U 时隙；`avg_mcs` 含重传
- Type I 码本必须做秩自适应 —— RI 与 PMI 一起报，与 SVD 用同一套奇异值门限

### 系统级仿真：栅格、队列记账与 KPI 口径 → `docs/pitfalls/system-kpi.md`

触发：改 `system.py` / `experience.py` / `scheduler_*.py` / `traffic.py` / `kpi_*.py`，或报系统级 KPI。

- 信道采样粒度与 SINR 聚合不能混为一谈 —— 接收 SINR 在 RBG 内、实际授予 RBG 与流之间按 dB 平均
- TDD 系统栅格是固定产品合同，不允许拿链路级带宽混跑 —— 只收 100 MHz/30 kHz/272 RB/17×16；通用 `CarrierGrid` 只服务链路级
- 速率统计口径：buffer 在发送时扣减，不看这个 TB 对不对 —— 发送即扣 buffer；重传对队列空操作；`acked_goodput` 下 NACK 给 0
- 系统仿真入口的两道硬校验（2026-08-17 第三轮审查） —— SRS provenance 校验；样本→UE 布局错位直接报错
- 样本数不是用户数 —— 样本轮转到 UE；同 UE 样本是连续时刻（Jakes 相关）；零速多轮硬失败
- 系统级只有一条评估路径 —— 没有容量模式，`full_buffer` 只是话务；不许为它开特例；每忙 TTI 不止 1 个 UE
- 「用户体验速率」有两个口径，别混 —— ITU `ue_served_*` vs 28.552 busy-period；0.17% 吻合不是交叉验证；在飞段另起字段
- 多小区数据集：挑哪个小区是物理选择，不是随便挑一个 —— 挑邻区最完整的；MCS 撞顶=场景选错；已下线配置一律硬失败

### MU-MIMO：预编码、配对与记账 → `docs/pitfalls/mu-mimo.md`

触发：改 `mumimo.py` / `scheduler_mu.py` / `beamforming.py` / `power_control.py`，或报 MU 增益。

- MU 的预编码矩阵只能表示方向，功率要单独给 —— `(W, p)` 分开；总功率归一到 1，别照搬 `tr(GG^H)=K`
- MU-MIMO 在导频污染下掉一半 —— `h_true` 预编码的 MU 增益不可信；测 CSI 敏感性要用多小区
- "不配对"不一定更差 —— 端口富余时全选可能高于 SUS，别读成配对没用
- MU 是空间复用，不是频率复用 —— 两用户各 rank1–2，按层数分功率；`pair_table` 记账两半；`se_ratio_legacy` 已删；准入三层门；R4 CorrLoss 口径
- 测试信道所有用户统计相同时，MU/SU 比值是个死数 —— 测 MU 必须给各 UE 不同路损
- 报"容量上界"必须开 MU —— `mu_enabled` 默认 False 不要改；问容量上界必须开 MU；MU 主要给容量不给边缘

### 信道生成、几何、阵列与探测 → `docs/pitfalls/channel-generation.md`

触发：改 `native.py` / `generate.py` / `spec38901.py` / `hardware.py` / `scenario.py`，或 preset 的信道与阵列配置。

- num_samples 必须能被 num_ues 整除 —— `_align_to_ues()` 负责，别把约束泄漏给用户
- 路损对标时必须复刻仿真器的公式选择逻辑 —— `*_LOS` 全链路走 LOS 公式；容差按独立位置数算
- 38.901 路损公式的两个"看起来像 bug 其实不是" —— LOS 公式可低于自由空间；判据用去阴影后的值
- 时延扩展的频域估计有固有误差 —— 只作数量级检查
- 信噪比不是输入参数 —— `snr_range_dB` 默认 None，别改回 `[0, 25]`
- CDL-A~E 表、20-ray 展开与 K 因子都有硬门 —— `spec38901.py` 唯一表真相源；禁止第二次 Rician K 混合
- 预置 64T/256T 使用同一端口顺序合同 —— `pol_h_v + top_to_bottom`；垂直 0.67λ 别改回 0.5；1 驱 N 只对已确认面板
- 多时隙的快照间隔是 5 ms，不是一个 TTI —— `sample_interval_s` 默认 5 ms；用 Jakes 首零点对时间轴
- preset 里不要写死 bs_panel —— 让 `_ensure_bs_panel` 从端口数推
- bs_panel 决定空间阵列，不再是几何干扰的开关 —— 干扰退化门查来源版本与 `rx_power_all_dbm`，不是 DFT 码本
- 压 num_rb 探测场景是安全的，但 snr_dB 会撞夹逼 —— probe 先还原全带 SNR 再算 SINR/IoT；性能数字绑内核版本
- 探测模式仍补齐 bs_panel，但原因是空间阵列一致 —— probe 与 full 同一阵列配置；依赖上一条的 SNR→SINR 重构
- 多普勒定义与移动轨迹边界 —— `doppler_hz=|v|/λ`；`static` 不等于零 Doppler
- scenario 设成 *_LOS 不会让 los_ratio 变成 1 —— 判断视距看 `scenario` 字段，不看 `los_ratio`

### 干扰、IoT 与接收机 → `docs/pitfalls/interference.md`

触发：改 `interference.py` / `linklevel.py` 接收机 / 干扰类 preset，或下干扰、IRC 结论。

- 干扰小区信道默认不保存 —— `interferer_channels` 默认 False，干扰协调任务要显式开
- MMSE 与 IRC 的区别全在 R_n，不在公式 —— 白干扰下两者必须逐位重合；2026-08-01 前的 MMSE 基线偏乐观
- 历史外部源的干扰小区信道是秩 1 的 —— IRC 增益偏乐观要带 `interference_rank`；`precoded` 旋钮空转
- num_interfering_ues 是上行旋钮，下行不读它 —— `srs_congested` 本质是上行场景（条目附整理注：代码位置与实测数字来自外部源时代）
- IoT 的主契约是 SIR + SINR；first-party 的 SNR 差值可作旁证 —— `IoT = SIR/(SIR−SINR)`（线性域）
- 业务域与测量域是两个量，别混 —— `sir_dB` 定吞吐、`ul/dl_sir_dB` 定估计精度；测量域只在 `BOTH` 时有
- 上行几何 SIR 走显式 metadata，钩子只兼容旧内核 —— 读 `meta["ul_geometry_sir_dB"]`；不得 monkey-patch 已删内部函数
- 信道级几何预算不是业务负载仿真 —— `pdsch_load` 不进几何预算；负载效应在系统/体验层
- 逐小区干扰分母项直接落盘（2026-08-11 契约恢复） —— `SINR=S/(N+ΣI_k)` 逐小区落盘；旧数据缺字段硬失败；存 `h_interferers` 不值得

### SRS 与信道估计 → `docs/pitfalls/srs-csi-estimation.md`

触发：改 `srs_*.py` / 估计器 / SRS 跳频与端口合同。

- LMMSE 必须从真实 pilot 位置直接映射到目标频点 —— `R_tp(R_pp+R_v)^-1 h_LS`；验收看低 SNR MC 降 MSE，不是每点优于 LS
- "17 倍跳频"是 38.211 里逐字有的 —— 跳频是老化主导项；序列本地冻结，非 272 RB 直接拒绝
- 2T4R 的 4 是待探测天线，不是 4Tx 同时发 —— 两腿分别维护 CSI lag；只开 4 个 CS；BBL 叶子排除；周期 10/20/40 ms

### 射线追踪（Sionna RT） → `docs/pitfalls/raytracing.md`

触发：改 `sionna_rt.py` / `scenes.py` / `scene_assets.py`，或使用 RT 数据集。

- 射线追踪的 PLY 资产与 Mitsuba 版本 —— VTK 导出的 `obj_info` 头会让 Mitsuba 3.8 报错；缓存里清理、不动源资产
- 射线追踪信道：换的是矩阵，不是口径 —— 只覆写 `_small_scale_channel`；载波相位、极化顺序、站点平移、覆盖空洞硬错误、非逐位可复现、阵列响应 `z0 - v·d_v`
- 射线追踪数据不能用 CDL 剖面算角度 —— `loader.paths()` 在 RT 数据上直接抛 `NotImplementedError`（条目附整理注）

### MCP 服务端、进程与性能 → `docs/pitfalls/runtime.md`

触发：改 `server.py` / warmup / `generate` 并行 / 子进程 / 性能基准。

- scipy 子模块必须在主线程预热 —— `warmup()` 不是冗余，工作线程首次 import scipy 会死锁；调试设 `SUPERRAN_DEBUG=1`
- MCP 服务端的内存：先别为没用的依赖买单，再压线程 arena —— 可选依赖只用 `find_spec` 探顶层名；BLAS 默认 1 线程；别把 numpy 改懒加载
- 多进程必须先压 BLAS 线程数 —— worker 先设线程=1；并行分块不能换 seed；降级串行要记原因
- 子进程编码要在子进程侧统一，不能只在父进程解码 —— 子进程设 `PYTHONIOENCODING`/`PYTHONUTF8`，父进程 strict
- 引擎清单长度不能随环境变化 —— 恒为 `internal_sim` / `sionna_rt` 两条
- mcp 1.x 与 2.x 的服务端类换了位置 —— `server.py` 的 try/except 兼容两个大版本
- stdio 传输下 stdout 是 JSON-RPC 通道 —— 调试输出只走 stderr
- 比耗时必须交错重测 —— 变体轮转多轮取中位数，报轮间波动
- 先批量线性代数，再谈 workers —— `[T,RB]` 小矩阵走 NumPy batch；不要加线程后端

### 说明书页面、HTML/SVG/JS 与回传桥 → `docs/pitfalls/spec-page-frontend.md`

触发：改 `spec.py` / `bridge.py` / `webui.py` / `deliver.py`，或任何生成 HTML/SVG/JS 的代码。

- 说明书文件名要唯一到秒以下 —— 同秒两份会静默覆盖；补随机后缀
- 内嵌 JS 里别用反斜杠转义 —— 用 `String.fromCharCode(10)`；验证 JS 只能真跑一遍
- SVG 的 <style> 是文档级的 —— 后注入的同名 class 覆盖前一张（含第二次踩：类名撞车）；共享用 presentation attribute
- CSS 的十六进制转义会贪心吃满 6 位 —— CSS 里要中文就直接写中文
- 页面上的 select 回传的是字符串，bool() 会失灵 —— 开关走 `_flag()`；`spec._SIM_DEFAULTS` 与签名一致
- 回执要先于落盘发出，而且必须幂等 —— 先入内存、回执、再落盘；同一 nonce 重发幂等
- 说明书默认不弹浏览器，只给地址 —— `open_browser` 默认 False；真打开用 `os.startfile`
- 回传接口的白名单只能有一份 —— 白名单只从 `spec.editable_keys()` 派生
- 正则找 SVG 前要先剥掉 script —— 先 `re.sub` 掉 `<script>` 再找 `<svg>`
- 自包含页面的截图不是天然可转 PNG —— tainted canvas 回退 SVG 并如实回执；下载要真浏览器验证

### 离线包、YAML 与 Python 兼容 → `docs/pitfalls/packaging-config.md`

触发：改 `make_offline_bundle.py` / `presets*.yaml` / f-string 拼 HTML。

- 离线包默认必须是完整包 —— 接收方没网；轻量包必须显式 `--thin`
- YAML 里的科学计数法 —— `100.0e6` 会被读成字符串；写完整数字
- f-string 里不能有反斜杠（Python < 3.12） —— 项目要求 ≥3.10；片段先提成局部变量
- YAML 里以 `*` 开头的值是别名 —— 以 `*`/`&` 开头的值要用 `>-` 或引号

### 文档与手册写作 → `docs/pitfalls/docs-writing.md`

触发：改 README / 开发者手册生成器 / 说明书文案 / 公式。

- 主手册首先是算法阐释，其次才是开发者索引 —— 先讲无线问题再讲实现；标准与工程预置分栏；表驱动算法的文档表从代码真相源生成
- 公式渲染是两层：KaTeX 排版 + MathML 兜底 —— 改公式后用 Node 真跑 `katex.renderToString(..., {throwOnError:true})`
- 文档里的计数要和代码绑死 —— 文档计数由测试从代码抽取比对，别凭印象写

## 加东西的地方

新功能该放哪（多载波 CA、新表/新随机流/新门禁/新场景/新测量量……）见 `docs/pitfalls/extension-points.md`，
加任何新模块、新配置或新 preset 前先读。要点：默认硬件/载波口径只在 `hardware.py`；页面可改参数只加
`spec._EDITABLE` 一处；新随机流走 `rng.register_stream`；新场景加完必须跑 `sr_probe_scenario` 把实测值写进
`expect`（preset 里的 label 是设计意图，不是实测）；多载波 CA 不得另复制一套 PHY 或 busy-period 公式。
