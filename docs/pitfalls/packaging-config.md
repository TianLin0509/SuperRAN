# 离线包、YAML 与 Python 兼容

> 本文件由 `CLAUDE.md`「踩过的坑」按主题拆出（2026-09-25），正文原样搬移，条目标题保留原文；索引在 `CLAUDE.md`。
> 每一条都是真实事故，**删之前先想清楚**；改动口径时同步更新本条和 `CLAUDE.md` 索引的一句话要点。

**什么时候读**：改 `make_offline_bundle.py` / `presets*.yaml` / f-string 拼 HTML。

### 离线包默认必须是完整包

`pip install -e .` 会起隔离构建环境去装 `build-system.requires`，离线时这一步
也得有轮子。早先的轻量包不含 `setuptools`，在全新 venv 里直接失败，而 pip 只说
"install build dependencies did not run successfully"，**完全看不出缺什么**。

现在默认打完整包（含 numpy/scipy + 构建后端），轻量包必须显式 `--thin`，
包型写进文件名和 `bundle-manifest.json` 的 `bundle_kind` / `self_contained` /
`requires_preinstalled`。改打包脚本前先想清楚：**接收方拿到包时没有网络，
所有"顺手 pip 一下"的假设都不成立。**

### YAML 里的科学计数法

`bandwidth_hz: 100.0e6` 会被 YAML 1.1 解析成**字符串**（需要 `100.0e+6` 才是浮点）。
`presets.yaml` 一律写完整数字 `100000000.0`。

### f-string 里不能有反斜杠（Python < 3.12）

`f'<td>{"<span class='a'>x</span>" if c else "..."}</td>'` 在 3.12 之前是
语法错误。本项目要求 >= 3.10，**本机是 3.12 所以跑得通、别的机器直接崩**。
把这类片段提成局部变量再插值。ruff 会报 `invalid-syntax`，别忽略它。

### YAML 里以 `*` 开头的值是别名

`caveat: **别拿 los_ratio 当判据**` 会被 YAML 当成别名解析并报
"expected alphabetic or numeric character"。以 `*` 或 `&` 开头的值必须用
`>-` 折叠块或引号包起来。

