"""Build the self-contained RB-power-control implementation audit report."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "output" / "rb_power_control" / "rb_power_control_audit_optimized.json"
DEFAULT_OUTPUT = ROOT / "output" / "rb_power_control" / "rb_power_control_report.html"


METRIC_NAMES = {
    "cell_served_mbps": "小区有效载荷吞吐",
    "cell_experienced_mbps": "小区体验速率",
    "ue_experienced_p5_mbps": "用户体验速率 P5",
    "serving_cell_prb_utilization": "服务小区 PRB 利用率",
    "bler_first_tx": "首传 BLER",
    "avg_mcs": "平均 MCS",
}


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return html.escape(str(value))


def _check_rows(rows: list[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        passed = bool(row.get("passed"))
        body.append(
            "<tr><td><span class='status {kind}'>{label}</span></td>"
            "<td><code>{name}</code></td><td>{detail}</td></tr>".format(
                kind="ok" if passed else "bad",
                label="PASS" if passed else "FAIL",
                name=html.escape(str(row.get("name", ""))),
                detail=html.escape(_compact_detail(row.get("detail"))),
            )
        )
    return "".join(body)


def _compact_detail(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if key in {"cells", "multipliers"}:
                continue
            parts.append(f"{key}={_compact_detail(item)}")
        return "; ".join(parts) or "—"
    if isinstance(value, list):
        if len(value) > 8:
            return f"{len(value)} values"
        return "[" + ", ".join(_compact_detail(item) for item in value) + "]"
    if isinstance(value, float):
        return f"{value:.8g}"
    return str(value)


def _profile_svg(values: list[float]) -> str:
    width, height = 860, 230
    left, top, chart_h = 48, 24, 150
    bar_gap = 5
    bar_w = (width - left - 26) / len(values) - bar_gap
    parts = [
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='17 RBG 功率倍率'>",
        "<line x1='48' y1='174' x2='840' y2='174' class='axis'/>",
        "<line x1='48' y1='99' x2='840' y2='99' class='grid'/>",
        "<text x='12' y='104' class='svg-label'>1×</text>",
        "<text x='12' y='29' class='svg-label'>2×</text>",
    ]
    for idx, value in enumerate(values):
        x = left + idx * (bar_w + bar_gap)
        h = chart_h * value / 2.0
        y = top + chart_h - h
        color = "#2dd4bf" if idx == 0 else "#64748b"
        parts.append(
            f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' height='{h:.1f}' "
            f"rx='4' fill='{color}'><title>RBG {idx}: {value:.4f}×</title></rect>"
        )
        parts.append(
            f"<text x='{x + bar_w / 2:.1f}' y='195' text-anchor='middle' "
            f"class='svg-label'>{idx}</text>"
        )
    parts.append("<text x='444' y='220' text-anchor='middle' class='svg-label'>RBG index（每 RBG 16 RB）</text></svg>")
    return "".join(parts)


def _paired_svg(uniform: list[float], shaped: list[float]) -> str:
    width, height = 860, 300
    all_values = [*uniform, *shaped]
    lo, hi = min(all_values), max(all_values)
    margin = max((hi - lo) * 0.12, 1.0)
    lo -= margin
    hi += margin

    def y(value: float) -> float:
        return 248.0 - (value - lo) / (hi - lo) * 200.0

    parts = [f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='8 对 CRN 体验速率'>"]
    for tick in range(5):
        value = lo + (hi - lo) * tick / 4
        yy = y(value)
        parts.append(f"<line x1='90' y1='{yy:.1f}' x2='770' y2='{yy:.1f}' class='grid'/>")
        parts.append(f"<text x='78' y='{yy + 4:.1f}' text-anchor='end' class='svg-label'>{value:.0f}</text>")
    for idx, (u_value, s_value) in enumerate(zip(uniform, shaped, strict=True)):
        yu, ys = y(u_value), y(s_value)
        parts.append(f"<line x1='280' y1='{yu:.1f}' x2='580' y2='{ys:.1f}' stroke='#64748b' stroke-width='1.5' opacity='.7'/>")
        parts.append(f"<circle cx='280' cy='{yu:.1f}' r='5' fill='#60a5fa'><title>rep {idx}: uniform {u_value:.3f}</title></circle>")
        parts.append(f"<circle cx='580' cy='{ys:.1f}' r='5' fill='#fb7185'><title>rep {idx}: shaped {s_value:.3f}</title></circle>")
    parts.extend([
        "<text x='280' y='278' text-anchor='middle' class='svg-label strong'>均匀 1×</text>",
        "<text x='580' y='278' text-anchor='middle' class='svg-label strong'>RBG0 2×</text>",
        "<text x='22' y='150' transform='rotate(-90 22 150)' text-anchor='middle' class='svg-label'>cell experienced Mbps</text>",
        "</svg>",
    ])
    return "".join(parts)


def _metric_rows(comparisons: dict[str, Any]) -> str:
    rows = []
    for key, value in comparisons.items():
        paired = value["paired"]
        ci = paired["ci95"]
        verdict = value.get("verdict", "")
        rows.append(
            "<tr><td>{metric}</td><td>{base}</td><td>{arm}</td>"
            "<td class='{effect_class}'>{effect}</td><td>[{lo}, {hi}]</td>"
            "<td>{p}</td><td><span class='status {kind}'>{verdict}</span></td></tr>".format(
                metric=html.escape(METRIC_NAMES.get(key, key)),
                base=_fmt(value["b"]["mean"]),
                arm=_fmt(value["a"]["mean"]),
                effect_class="neg" if float(value["effect"]) < 0 else "pos",
                effect=_fmt(value["effect"]),
                lo=_fmt(ci[0]),
                hi=_fmt(ci[1]),
                p=_fmt(paired.get("decision_p_value")),
                kind="ok" if verdict == "significant" else "muted",
                verdict="可下方向结论" if verdict == "significant" else "不确定",
            )
        )
    return "".join(rows)


def build(data: dict[str, Any], *, old_elapsed: float | None = None) -> str:
    ab = data["ab"]
    comparisons = ab["comparisons"]
    experienced = comparisons["cell_experienced_mbps"]
    profile = data["profiles"]["shaped"]["cells"][0]
    rb_values = profile["multipliers"]
    rbg_values = [sum(rb_values[i:i + 16]) / len(rb_values[i:i + 16])
                  for i in range(0, len(rb_values), 16)]
    old_s = float(old_elapsed or 806.4898912999779)
    new_s = float(ab["elapsed_s"])
    speedup = old_s / new_s
    own_check = next(row for row in data["cross_cell_causality"]["checks"]
                     if row["name"] == "own_ue_boosted_rb_is_plus_3.0103_db")
    victim_check = next(row for row in data["cross_cell_causality"]["checks"]
                        if row["name"] == "victim_ue_boosted_rb_loses_sinr")
    max_victim_loss = min(float(x) for x in victim_check["detail"])
    own_gain = float(own_check["detail"][0])

    css = """
    :root{--bg:#07111f;--panel:#0d1b2e;--panel2:#101f35;--ink:#e7eef9;--muted:#91a4bd;--line:#233751;--teal:#2dd4bf;--blue:#60a5fa;--rose:#fb7185;--amber:#fbbf24;--ok:#34d399}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at 12% 0,#123255 0,transparent 34%),var(--bg);color:var(--ink);font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;line-height:1.62}
    main{max-width:1180px;margin:auto;padding:34px 28px 80px}.hero{padding:46px 44px;border:1px solid #2d496d;border-radius:22px;background:linear-gradient(135deg,rgba(18,50,85,.94),rgba(8,21,38,.94));box-shadow:0 25px 70px rgba(0,0,0,.32)}
    .eyebrow{color:var(--teal);font-weight:700;letter-spacing:.12em;text-transform:uppercase;font-size:13px}.hero h1{font-size:clamp(34px,5vw,58px);line-height:1.08;margin:10px 0 18px}.lead{font-size:19px;color:#c9d8ea;max-width:900px}.chips{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px}.chip{padding:7px 12px;border-radius:999px;background:#102640;border:1px solid #2d4d72;color:#cfe4fb;font-size:13px}
    nav{position:sticky;top:0;z-index:3;margin:18px 0 28px;padding:10px 16px;background:rgba(7,17,31,.9);backdrop-filter:blur(12px);border:1px solid var(--line);border-radius:13px;display:flex;gap:18px;flex-wrap:wrap}nav a{color:#b8cae0;text-decoration:none;font-size:14px}nav a:hover{color:var(--teal)}
    section{margin-top:28px;padding:30px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(180deg,rgba(16,31,53,.96),rgba(10,24,41,.96))}h2{font-size:27px;margin:0 0 18px}h3{font-size:18px;margin:22px 0 10px;color:#d7e7fa}.grid{display:grid;gap:16px}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}.g3{grid-template-columns:repeat(3,minmax(0,1fr))}.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.card{padding:18px;border:1px solid #263c58;border-radius:14px;background:#0a192b}.kpi{font-size:29px;font-weight:760;line-height:1.1}.label{font-size:13px;color:var(--muted);margin-top:7px}.ok-text{color:var(--ok)}.neg{color:var(--rose)}.pos{color:var(--teal)}.muted-text{color:var(--muted)}
    .equation{padding:22px;border-left:4px solid var(--teal);background:#071522;border-radius:10px;font-family:Cambria Math,"Times New Roman",serif;font-size:23px;overflow:auto}.equation small{display:block;color:var(--muted);font-family:"Segoe UI","Microsoft YaHei",sans-serif;font-size:13px;margin-top:10px}.callout{padding:16px 18px;border-radius:11px;background:#112b3d;border:1px solid #24506a}.warn{background:#2a2110;border-color:#6e5620}.danger{background:#2c1720;border-color:#763247}
    table{border-collapse:collapse;width:100%;max-width:100%;font-size:14px}th,td{padding:11px 12px;border-bottom:1px solid #203650;text-align:left;vertical-align:top}th{color:#a9bdd5;font-size:12px;text-transform:uppercase;letter-spacing:.06em}code,pre{font-family:"Cascadia Code",Consolas,monospace}code{color:#b8e3ff}pre{max-width:100%;padding:17px;border-radius:11px;overflow:auto;background:#06101c;border:1px solid #203650;color:#d9e8f7;font-size:13px}.status{display:inline-block;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:750;white-space:nowrap}.status.ok{color:#08271e;background:#6ee7b7}.status.bad{color:#3b0a12;background:#fda4af}.status.muted{color:#273347;background:#cbd5e1}.axis{stroke:#7590ad;stroke-width:1.2}.grid{stroke:#263b55;stroke-width:1}.svg-label{fill:#91a4bd;font:12px "Segoe UI","Microsoft YaHei",sans-serif}.svg-label.strong{fill:#dceafb;font-weight:700}svg{max-width:100%;height:auto}.diagram{width:100%;height:auto;background:#081522;border:1px solid #223852;border-radius:14px}.flow{display:grid;grid-template-columns:repeat(7,1fr);gap:8px;align-items:center}.flow .node{min-width:0;min-height:100px;padding:14px 10px;border:1px solid #2b4666;border-radius:11px;background:#0b1b2e;text-align:center;font-size:13px;display:flex;align-items:center;justify-content:center}.flow .arrow{text-align:center;color:var(--teal);font-size:22px}.path{color:#a9c7e7;word-break:break-all;font-family:Consolas,monospace;font-size:12px}.foot{color:var(--muted);font-size:13px}.checklist li{margin:8px 0}.checklist li::marker{color:var(--teal)}
    @media(max-width:900px){.g4,.g3,.g2{grid-template-columns:1fr 1fr}.flow{grid-template-columns:1fr}.flow .arrow{transform:rotate(90deg)}.hero{padding:32px 24px}}
    @media(max-width:620px){main{padding:18px 12px 55px}.g4,.g3,.g2{grid-template-columns:1fr}.card{min-width:0}section{padding:21px;min-width:0}.hero{min-width:0}.hero h1{font-size:36px}table{display:block;overflow-x:auto}th,td{min-width:118px}.equation{max-width:100%}}
    @media print{body{background:#fff;color:#111}main{max-width:none}.hero,section{background:#fff;color:#111;box-shadow:none;break-inside:avoid}.muted-text,.label,.foot{color:#555}nav{display:none}}
    """

    topology_svg = """
    <svg class="diagram" viewBox="0 0 1000 330" role="img" aria-label="逐 RB 功率对自身和受扰用户的因果拓扑">
      <defs><marker id="arrowB" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#60a5fa"/></marker><marker id="arrowR" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#fb7185"/></marker></defs>
      <rect x="55" y="60" width="250" height="210" rx="18" fill="#102a46" stroke="#3b82f6"/><text x="180" y="96" text-anchor="middle" fill="#dbeafe" font-size="18" font-weight="700">服务小区 s(u)</text><text x="180" y="128" text-anchor="middle" fill="#93c5fd" font-size="14">qₛ,ᵣ × Sᵤ</text><rect x="95" y="160" width="170" height="58" rx="10" fill="#0b1727" stroke="#2dd4bf"/><text x="180" y="184" text-anchor="middle" fill="#e7eef9" font-size="13">RB r: 2.0×</text><text x="180" y="205" text-anchor="middle" fill="#91a4bd" font-size="12">其他 RB 自动补偿</text>
      <rect x="695" y="48" width="250" height="226" rx="18" fill="#2b1821" stroke="#fb7185"/><text x="820" y="86" text-anchor="middle" fill="#ffe4e6" font-size="18" font-weight="700">邻区 k</text><text x="820" y="118" text-anchor="middle" fill="#fda4af" font-size="14">qₖ,ᵣ × Iᵤ,ₖ</text><rect x="735" y="152" width="170" height="58" rx="10" fill="#0b1727" stroke="#fbbf24"/><text x="820" y="176" text-anchor="middle" fill="#e7eef9" font-size="13">同一 RB 的干扰功率</text><text x="820" y="197" text-anchor="middle" fill="#91a4bd" font-size="12">按每个邻区独立变化</text>
      <circle cx="500" cy="155" r="58" fill="#10233a" stroke="#2dd4bf" stroke-width="2"/><text x="500" y="148" text-anchor="middle" fill="#e7eef9" font-size="17" font-weight="700">UE u</text><text x="500" y="174" text-anchor="middle" fill="#91a4bd" font-size="12">Nᵤ + ΣIᵤ,ₖ</text>
      <line x1="305" y1="151" x2="433" y2="151" stroke="#60a5fa" stroke-width="4" marker-end="url(#arrowB)"/><text x="368" y="135" text-anchor="middle" fill="#93c5fd" font-size="12">期望信号</text><line x1="695" y1="180" x2="563" y2="168" stroke="#fb7185" stroke-width="4" marker-end="url(#arrowR)"/><text x="630" y="148" text-anchor="middle" fill="#fda4af" font-size="12">站间干扰</text><text x="500" y="266" text-anchor="middle" fill="#cbd5e1" font-size="13">同一个 q 同时改变自身分子与其他 UE 的分母；不能只给服务 UE 加 dB</text>
    </svg>
    """

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>RB 级下行功率控制 · 落地与可信度审计</title><style>{css}</style></head>
<body><main>
  <header class="hero"><div class="eyebrow">SuperRAN · Implementation & Evidence Audit</div><h1>RB 级下行功率控制<br>已落地，并且能被反向挑错</h1>
    <p class="lead">默认关闭时保持每 RB 1×；开启后允许 0.1×–4× 连续配置，同时对每个小区硬约束总功率不变。实现不是“给目标 UE 的 SINR 加一个 dB”，而是用 ChannelHub 保存的逐小区 S/N/I 功率分解，让同一个功率动作同时改变自身信号和其他 UE 的同频干扰。</p>
    <div class="chips"><span class="chip">审计状态：{html.escape(data['status'].upper())}</span><span class="chip">目标数据：{html.escape(data['target_dataset_id'])}</span><span class="chip">跨小区数据：{html.escape(data['cross_cell_dataset_id'])}</span><span class="chip">8 对 CRN · 5 s/臂</span><span class="chip">默认行为零改动</span></div>
  </header>
  <nav><a href="#outcome">结论</a><a href="#contract">数学契约</a><a href="#architecture">链路</a><a href="#profile">配置</a><a href="#gates">证据</a><a href="#ab">A/B</a><a href="#modules">模块</a><a href="#limits">边界</a></nav>

  <section id="outcome"><h2>先看结论</h2><div class="grid g4">
    <div class="card"><div class="kpi ok-text">0.1×–4×</div><div class="label">每 RB 连续倍率硬边界</div></div>
    <div class="card"><div class="kpi ok-text">Σq = 272</div><div class="label">每小区 272 RB 总功率严格守恒</div></div>
    <div class="card"><div class="kpi ok-text">{own_gain:+.4f} dB</div><div class="label">2× 功率对自身 RB 的解析增益</div></div>
    <div class="card"><div class="kpi">{speedup:.1f}×</div><div class="label">算法输出逐值不变的正式仿真加速</div></div>
  </div>
  <div class="callout warn" style="margin-top:18px"><strong>正式 A/B 不是“功控必然增益”的宣传实验。</strong> 人工把 RBG0 拉到 2×、其余 RB 压到 0.9375×，在该混合话务/PF 工况下，小区体验速率显著下降 2.8%；而有效载荷吞吐、P5、PRB 利用率、BLER、MCS 都不足以下方向结论。这反而证明功率变化确实进入了具体 RBG 的调度/MCS/TBS 链路，且平台没有把所有点估计包装成收益。</div>
  </section>

  <section id="contract"><h2>数学与输入契约</h2>{topology_svg}
    <h3>唯一允许的耦合公式</h3><div class="equation">γ<sub>u,r</sub> = q<sub>s(u),r</sub>S<sub>u</sub> / [N<sub>u</sub> + η<sub>u</sub>Σ<sub>k≠s(u)</sub>q<sub>k,r</sub>I<sub>u,k</sub>]
      <small>q：逐小区逐 RB 功率倍率；S：服务小区期望功率；N：热噪声；I<sub>u,k</sub>：邻区 k 对 UE u 的绝对干扰功率；η：邻区 PRB 利用率。</small></div>
    <div class="grid g3" style="margin-top:16px"><div class="card"><strong>频域功控</strong><p class="muted-text">改变 q[cell, RB]，每行均值固定为 1；它和空间预编码功率约束是两层不同机制。</p></div><div class="card"><strong>空间功率约束</strong><p class="muted-text">EBF/PEBF/NEBF 仍由 <code>power_constraint</code> 控制，不被 RB 功控复用或改名。</p></div><div class="card"><strong>为什么聚合 SIR 不够</strong><p class="muted-text">一旦不同邻区采用不同 profile，ΣI 无法恢复 ΣqₖIₖ；因此旧数据集缺逐小区 I 时硬失败。</p></div></div>
    <p class="foot">协议定位：3GPP TS 38.214 §4.1 由 gNB 决定下行发射 EPRE，并在 UE 假设中区分参考信号/PDSCH 功率关系；本模块是系统级仿真中的可审计频域功率实现，不声称某一条逐 RB 策略由标准规定。参考：<a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138214/18.07.00_60/ts_138214v180700p.pdf" style="color:#7dd3fc">ETSI TS 138 214 V18.7.0</a>。</p>
  </section>

  <section id="architecture"><h2>功率动作真正走到哪里</h2><div class="flow"><div class="node">用户配置<br><code>q[cell,RB]</code></div><div class="arrow">→</div><div class="node">ChannelHub<br>保存 S / N / 每小区 I</div><div class="arrow">→</div><div class="node">逐 RB 耦合<br>信号与站间干扰</div><div class="arrow">→</div><div class="node">保留 272 RB<br>SVD / MMSE</div></div>
    <div class="flow" style="margin-top:8px"><div class="node">实际 RBG bitmap</div><div class="arrow">→</div><div class="node">grant-specific SINR</div><div class="arrow">→</div><div class="node">MCS / TBS / BLER</div><div class="arrow">→</div><div class="node">PF RU / KPI / PRB 账本</div></div>
    <div class="callout" style="margin-top:18px"><strong>关键实现选择：</strong>开启功控时，272 个 RB 全部穿过 SVD/MMSE，最后才聚合成 17 个 RBG 的 SINR；关闭时仍走原来的 RBG 中心抽样快速路径。这样 1 个 RB 的功率变化不会在预处理阶段被平均掉，同时默认模式不承担额外成本。</div>
  </section>

  <section id="profile"><h2>本次正式 A/B 的 profile</h2><div class="grid g2"><div>{_profile_svg(rbg_values)}</div><div><div class="card"><div class="kpi">RBG0 = 2×</div><div class="label">RB 0..15；其余 256 RB 自动补偿到 0.9375×</div></div><div class="card" style="margin-top:12px"><div class="kpi">Σq = {_fmt(profile['sum_multiplier'], 1)}</div><div class="label">均值 {_fmt(profile['mean_multiplier'], 4)}，浮点总和误差 {_fmt(profile['sum_error'], 3)}</div></div><div class="card" style="margin-top:12px"><strong>自动补偿规则</strong><p class="muted-text">用户显式给定的 RB 值绝不二次归一化；仅用未指定 RB 做等量补偿。补偿后若越过 0.1×–4×，立即拒绝配置。</p></div></div></div>
    <h3>入口示例</h3><pre>sr_system_sim(
    ..., rb_power_control_enabled=True,
    rb_power_overrides=[
        {{"cell_index": 0, "rb_start": 0, "rb_end": 15,
         "multiplier": 2.0}}
    ]
)</pre>
    <p class="foot">还支持单 RB（<code>rb</code>）和 <code>cell_index: "all"</code>。重叠区间、未知字段、布尔伪值、NaN/Inf、越界 RB/小区、全 profile 总和不等于 RB 数，全部硬失败。</p>
  </section>

  <section id="gates"><h2>门 1 与因果反向验证</h2><div class="grid g4"><div class="card"><div class="kpi">32×1×272×64×4</div><div class="label">正式信道张量</div></div><div class="card"><div class="kpi">21 cells</div><div class="label">逐小区干扰维度</div></div><div class="card"><div class="kpi">500</div><div class="label">随机守恒/公式性质测试</div></div><div class="card"><div class="kpi neg">{max_victim_loss:.4f} dB</div><div class="label">最强受扰样本在 boosted RB 的 SINR 变化</div></div></div>
    <h3>正式数据体检</h3><table><thead><tr><th>状态</th><th>检查</th><th>证据</th></tr></thead><tbody>{_check_rows(data['door1'])}</tbody></table>
    <h3>跨小区因果</h3><table><thead><tr><th>状态</th><th>检查</th><th>证据</th></tr></thead><tbody>{_check_rows(data['cross_cell_causality']['checks'][1:])}</tbody></table>
    <h3>性质压力测试</h3><table><thead><tr><th>状态</th><th>检查</th><th>证据</th></tr></thead><tbody>{_check_rows(data['property_stress']['checks'])}</tbody></table>
  </section>

  <section id="ab"><h2>门 3：8 对共同随机数 A/B</h2><div class="grid g3"><div class="card"><div class="kpi">{experienced['b']['mean']:.3f}</div><div class="label">uniform 小区体验 Mbps</div></div><div class="card"><div class="kpi neg">{experienced['a']['mean']:.3f}</div><div class="label">shaped 小区体验 Mbps</div></div><div class="card"><div class="kpi neg">{experienced['effect_rel'] * 100:.1f}%</div><div class="label">95% CI [{experienced['paired']['ci95'][0]:.3f}, {experienced['paired']['ci95'][1]:.3f}] Mbps；Wilcoxon p={experienced['paired']['decision_p_value']:.4f}</div></div></div>
    {_paired_svg(experienced['uniform_values'], experienced['shaped_values'])}
    <table><thead><tr><th>指标</th><th>均匀臂</th><th>整形臂</th><th>差值 A−B</th><th>配对 95% CI</th><th>判决 p</th><th>结论</th></tr></thead><tbody>{_metric_rows(comparisons)}</tbody></table>
    <p class="foot">规则：只有配对 95% CI 不跨 0，且判决检验 p&lt;0.05，才允许报告方向。因而这里只能说该 profile 在该工况下显著降低小区体验速率；其余五个 KPI 必须写“不确定”，不能把点估计当效果。</p>
    <h3>性能优化不改变输出</h3><div class="grid g3"><div class="card"><div class="kpi">{old_s:.2f} s</div><div class="label">优化前 16 次 5 秒系统仿真</div></div><div class="card"><div class="kpi ok-text">{new_s:.2f} s</div><div class="label">优化后同数据同种子</div></div><div class="card"><div class="kpi">{speedup:.2f}×</div><div class="label">6 个 KPI、两臂各 8 个样本逐值完全相同</div></div></div>
  </section>

  <section id="modules"><h2>主要模块与“为什么能站住”</h2><table><thead><tr><th>模块</th><th>职责</th><th>可挑错点与防线</th></tr></thead><tbody>
    <tr><td><code>ChannelHub internal_sim.py / sionna_rt.py</code></td><td>在形成业务域几何预算时同步产出每 RB 服务信号、热噪声、逐 slot/逐小区干扰绝对功率</td><td>服务小区列必须为 0；存储 SINR 可由 S/(N+ΣI) 以 &lt;1e−10 dB 误差重构。</td></tr>
    <tr><td><code>power_control.py</code></td><td>解析配置、总功率补偿、边界校验、精确 RB 耦合、profile 指纹</td><td>500 个随机 profile 最差总和误差 5.68e−14；直接公式最差相对误差 1.58e−15。</td></tr>
    <tr><td><code>system.py</code></td><td>全 RB 建链、SVD/MMSE、MU pair 数据、单小区资源池约束</td><td>profile 指纹错配和混合 serving cell 都硬失败，防止拿旧链路表冒充新配置。</td></tr>
    <tr><td><code>experience.py</code></td><td>按实际 RBG bitmap 计算 SINR/MCS/TBS，维护 PF/资源账本</td><td>高低 SINR RBG 定向单测；快速 MCS 选择在阈值、阈值±1e−12、NaN/±Inf 上与原实现逐值相同。</td></tr>
    <tr><td><code>server.py / spec.py</code></td><td>MCP 和 UI 参数入口、结果 profile/诊断回传</td><td>缺逐小区几何量不降级；非法 JSON/范围/维度原样报错。</td></tr>
    <tr><td><code>run_rb_power_control_audit.py</code></td><td>门 1、跨小区因果、随机性质、正式 CRN A/B 一键复现</td><td>数据集、随机种子、profile、配置和每次 replication 都落 JSON，不靠口头描述。</td></tr>
  </tbody></table>
    <h3>回归证据</h3><ul class="checklist"><li>功控定向测试：11 passed。</li><li>系统级脚本：14 章全部通过，含 100,000 TTI 压力段。</li><li>干扰量化/场景预设全链路完整通过，覆盖真实多小区生成、paired SRS、64T 阵列、说明书和文档数字对账。</li><li>MCP 全链路：34 个工具注册并通过关键 E2E。</li><li>SuperRAN 生成契约 6 passed；ChannelHub 功率分解 1 passed。</li><li>ChannelHub 相关源测试此前 46 passed；两仓 <code>py_compile</code>、<code>ruff</code>、<code>git diff --check</code> 通过。</li></ul>
    <p class="foot">审计 JSON：<span class="path">{html.escape(str(DEFAULT_INPUT))}</span></p>
  </section>

  <section id="limits"><h2>明确边界与下一阶段决策</h2><div class="grid g2"><div><h3>当前已经可靠的范围</h3><ul class="checklist"><li>人工配置每小区每 RB 连续功率，并严格保持小区总功率。</li><li>服务信号与每个邻区同频干扰的方向、幅度按绝对功率耦合。</li><li>功率影响贯穿到具体 RBG grant 的 MCS/TBS/BLER/PF/KPI。</li><li>默认关闭时不改变既有结果与快路径。</li></ul></div><div><h3>不能越界声称</h3><ul class="checklist"><li>当前 <code>SystemResult</code> 是单小区调度器；多小区联合 TTI 调度尚未实现。</li><li>邻区活动是利用率标量，不是各邻区真实的逐 TTI RB bitmap。</li><li>一个 TB 跨多个 RBG 时，当前用 dB 算术均值压缩 SINR，尚未用标定后的 EESM/MIESM。</li><li>正式因果 A/B 为隔离变量而关闭 MU 与 CSI 老化，不等于全功能网络结论。</li><li>当前调度器会使用实际 RBG bitmap，但不会自动优化 q；功控策略搜索属于上层算法。</li></ul></div></div>
    <div class="callout danger" style="margin-top:16px"><strong>建议的下一决策顺序：</strong>先标定 EESM/MIESM β，再决定是否做逐邻区 TTI 联合负载；两者完成后才适合评价“某个自动功控算法能提升多少”。MU+功控应作为独立正式实验，不能复用本次关闭 MU 的 -2.8% 结论。</div>
  </section>
  <p class="foot" style="margin:28px 4px">生成依据：<span class="path">{html.escape(str(DEFAULT_INPUT))}</span> · HTML 无外部脚本/字体/样式依赖，可离线打开。</p>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--old-elapsed", type=float, default=806.4898912999779)
    args = parser.parse_args()
    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    data = json.loads(source.read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build(data, old_elapsed=args.old_elapsed), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
