# 风险分档（按文件路径查表，Agent 不许自己判断）

改动**触碰到的最高档位**决定需要几个 Reviewer。

## 🔴 红档 — 物理核心，需要 2 个 Reviewer（Physics + Integration）

命中以下任一文件即为红档：

| 领域 | 文件 |
|---|---|
| 链路自适应 / AMC / HARQ / rank | `linkadapt.py` `amc_policy.py` |
| BLER 曲线与标定 | `bler_curves.py` `bler_data_20b.py` `calibration.py` |
| 调度 | `scheduler_resource.py` `scheduler_frequency.py` `scheduler_mu.py` `scheduler_finalize.py` |
| MU-MIMO | `mumimo.py` |
| SRS | `srs_resource.py` `srs_waveform.py` `srs_metrics.py` |
| CSI 时延 | `csi_aging.py` |
| 信道生成 | `native.py` `channelhub.py` `generate.py` `spec38901.py` `carrier.py` |
| 物理层 / 链路级 | `physical.py` `linklevel.py` `interference.py` |
| 功率 / 波束 | `power_control.py` `beamforming.py` |
| 随机数与实验统计 | `rng.py` `gates.py` |
| KPI 口径 | `system.py` `experience.py` `measure.py` `kpi_compare.py` `analysis.py` |

## 🟡 黄档 — 一般功能，需要 1 个 Reviewer

`src/` 下其余 `.py`、`tests/`、`scripts/` 里参与仿真或判决的脚本、`presets/`、
MCP 与服务端（`server.py` `bridge.py` `results.py` `validate.py` `loader.py`）。

## 🟢 绿档 — 不需要 Reviewer，跑相关测试即可

`*.md`、`*.html`、`docs/`、`.agents/`、报告排版（`deliver.py` `katex.py` `mathml.py`
`webui.py` 的纯样式改动）、纯注释与文档字符串。

> **判定规则**：绿档改动里只要夹带一行黄档或红档文件的逻辑修改，整次按高档位走。
> 拿不准就往高了算——多一个 Reviewer 的成本远低于一个静默的物理错误。

## 测试环境的一个坑（2026-09-03 集成时实测）

`__editable__.superran-0.1.0.pth` 硬指向 `C:\Vibe\Wireless\SuperRAN\src`，
`pyproject.toml` 里没有 pytest 的 `pythonpath` 配置。因此：

- 在任何 linked worktree 里 `import superran` 拿到的是**主仓库**的代码，不是本工作区检出的 SHA；
- 而基于文件路径的断言（读 `CLAUDE.md`、`skills/`、`docs/`）用的又是本工作区的文件。

同一次测试里两个真相并存，且不报错。**合并前的最终全量回归一律在
`C:\Vibe\Wireless\SuperRAN` 跑。** 一定要在 worktree 里跑，先设
`PYTHONPATH=<该 worktree>\src`，并在报告里写明你验证过 `superran.__file__` 指向哪。
