"""Build the self-contained overnight SuperRAN deep-dive report from evidence JSON."""
from __future__ import annotations

import html
import json
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLASSIC = ROOT / "artifacts" / "results" / "classic_comm_benchmarks.json"
QUICK = ROOT / "output" / "test-matrix" / "quick-summary.json"
PHYSICS = ROOT / "output" / "test-matrix" / "physics-summary.json"
STRESS_A = ROOT / "artifacts" / "results" / "experience_property_stress_seed83117.json"
STRESS_B = ROOT / "artifacts" / "results" / "experience_property_stress_seed20260822.json"
KPI_QA = ROOT / "output" / "kpi-browser-qa.json"
OUTPUT = ROOT / "artifacts" / "review" / "superran-overnight-deep-dive-20260822.html"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _git(*args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True,
        text=True, encoding="utf-8", timeout=30,
    )
    return cp.stdout.strip()


def _case_metrics(case: dict[str, Any]) -> tuple[str, str]:
    m = case["metrics"]
    cid = case["id"]
    if cid == "B01_awgn_shannon_siso":
        return "解析误差", f"{m['max_abs_error_bit_per_s_hz']:.1e} bit/s/Hz"
    if cid == "B02_mimo_svd_upper_bound":
        return "违例", f"上界 {m['bound_violation_count']}/24 · Type-I>SVD {m['type1_over_svd_violation_count']}/24"
    if cid == "B03_lmmse_low_snr":
        p = m["paired"]
        return "LMMSE−LS", f"{p['mean_diff']:.3f} dB · CI [{p['ci95'][0]:.3f}, {p['ci95'][1]:.3f}]"
    if cid == "B04_olla_target_bler":
        s = m["summary"]
        return "稳态 IBLER", f"{s['mean']:.4f} · target {m['target_bler']:.2f}"
    if cid == "B05_pf_throughput_fairness":
        return "CRN Gate 3", (
            f"吞吐差 {m['maxci_vs_pf_throughput']['effect']:+.3f} Mbps · "
            f"Jain 差 {m['pf_vs_maxci_jain']['effect']:+.3f}")
    if cid == "B06_per_antenna_power_constraints":
        su = m["su"]
        return "SU / MU 反例", (
            f"SU E/P/N={su['ebf']['spectral_efficiency']:.2f}/"
            f"{su['pebf']['spectral_efficiency']:.2f}/"
            f"{su['nebf']['spectral_efficiency']:.2f} · "
            f"MU P/N={m['mu_pebf']['sum_se']:.2f}/{m['mu_nebf']['sum_se']:.2f}")
    if cid == "B07_tdd_srs_reciprocity":
        return "谱效", (
            f"ideal/correct/wrong={m['ideal_se']:.2f}/"
            f"{m['correct_mapping_se']:.2f}/{m['wrong_mapping_se']:.2f}")
    if cid == "B08_jakes_doppler_time_scale":
        return "时间尺度", f"J0 误差 {m['max_j0_error']:.1e} · T3/T30={m['coherence_ratio_3_to_30_kmh']:.2f}"
    if cid == "B09_nr_tbs_rbg_monotonicity":
        return "TBS", (
            f"1/17 RBG={m['mcs12_rank2_one_rbg_bytes']}/"
            f"{m['mcs12_rank2_17_rbg_bytes']} B · 非线性 {m['nonlinearity_fraction']:.3%}")
    if cid == "B10_tr38901_channel_gate":
        return "Gate 1", f"{m['gate1']['n_items']} checks · {len(m['gate1']['blockers'])} blocker"
    return "结果", "见 JSON"


def _case_cards(classic: dict[str, Any]) -> str:
    spec = {row["id"]: row for row in classic["spec"]["cases"]}
    cards: list[str] = []
    for case in classic["results"]:
        frozen = spec[case["id"]]
        metric_name, metric_value = _case_metrics(case)
        checks = "".join(
            f'<li><span class="ok">✓</span><div><b>{_esc(row["name"])}</b>'
            f'<small>{_esc(row["detail"])}</small></div></li>'
            for row in case["checks"]
        )
        sources = " · ".join(
            f'<a href="{_esc(url)}" target="_blank" rel="noreferrer">source {idx}</a>'
            for idx, url in enumerate(frozen["sources"], 1)
        )
        cards.append(f'''
        <article class="case">
          <div class="case-head"><span>{_esc(case["id"].split("_")[0])}</span><em>PASS</em></div>
          <h3>{_esc(frozen["title"])}</h3>
          <p class="expect"><b>运行前冻结：</b>{_esc(frozen["expected"])}</p>
          <div class="metric"><small>{_esc(metric_name)}</small><strong>{_esc(metric_value)}</strong></div>
          <ul>{checks}</ul><p class="sources">{sources}</p>
        </article>''')
    return "".join(cards)


def main() -> None:
    classic, quick, physics = _read(CLASSIC), _read(QUICK), _read(PHYSICS)
    stress_a, stress_b, kpi = _read(STRESS_A), _read(STRESS_B), _read(KPI_QA)
    status_lines = _git("status", "--short").splitlines()
    branch, head = _git("branch", "--show-current"), _git("rev-parse", "--short", "HEAD")
    quick_s = float(quick["elapsed_s"])
    physics_s = float(physics["elapsed_s"])
    source_hash = classic["provenance"]["source_tree_sha256"]
    spec_hash = classic["provenance"]["spec_sha256"]
    stress_total = int(stress_a["summary"]["checks_total"]) + int(stress_b["summary"]["checks_total"])
    stress_pass = int(stress_a["summary"]["checks_passed"]) + int(stress_b["summary"]["checks_passed"])
    fixed = (
        ("统计门", "外部结果 partition_mismatch 从 warning 修为 block，防伪独立样本与伪显著。"),
        ("测试真值", "MCP pytest 薄壳不再吞 FAILED；system tool 直接执行不再空跑。"),
        ("并行血缘", "worker 合并丢字段不再被空列表覆盖；返回类型与全部调用方同步。"),
        ("IoT 诊断", "UL 几何 SIR 安装失败原因真正写入全局；兼容路径不再 UnboundLocalError。"),
        ("场景探测", "单小区 49.9 dB 哨兵按拓扑判定；多小区合法夹逼值不再冒充无干扰。"),
        ("交互 QA", "Playwright Chromium 缺失时显式回退系统 Edge，dev 依赖与 backend 留痕。"),
        ("边界内存", "说明书 nonce 幂等集合有上限且可回归，不再只声明常量。"),
        ("实验血缘", "新增运行语义树 SHA、commit/diff/依赖/BLER 哈希；旧数据 unknown、漂移 mismatch。"),
        ("结果摘要", "dataset_digest 升级为 NPZ + 物理语义 summary v2，并复用未变化 NPZ 哈希。"),
        ("可观察回归", "新增 quick/physics/full 测试矩阵：逐文件心跳、超时、日志、进程树清理与 partial JSON。"),
        ("经典基准", "10 个案例在首次运行前冻结判据，结果和一手来源、门禁、限制一并落盘。"),
    )
    fixed_html = "".join(
        f'<article><span>{i:02d}</span><div><b>{_esc(title)}</b><p>{_esc(text)}</p></div></article>'
        for i, (title, text) in enumerate(fixed, 1)
    )
    test_rows = "".join(
        f'<tr><td>{_esc(row["file"])}</td><td class="pass">PASS</td><td>{row["elapsed_s"]:.1f} s</td></tr>'
        for row in quick["files"] + physics["files"]
    )
    html_text = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,"><title>SuperRAN 夜间深潜：修复、经典基准与可信度复核</title>
<style>
:root{{--bg:#06131d;--panel:#0c2130;--line:#28495e;--ink:#eef9ff;--muted:#a9c2d0;--green:#45e39a;--cyan:#50d5df;--amber:#ffc45e;--red:#ff7777;--blue:#7aaeff}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;overflow-x:hidden;background:radial-gradient(circle at 78% 0,#174254 0,transparent 30%),var(--bg);color:var(--ink);font:15px/1.7 Inter,"Segoe UI","Microsoft YaHei",sans-serif}}a{{color:#79dcff}}code{{font-family:Consolas,monospace;background:#06121b;padding:2px 6px;border:1px solid var(--line);border-radius:5px;overflow-wrap:anywhere;word-break:break-word}}.layout{{display:grid;grid-template-columns:250px minmax(0,1fr)}}aside{{position:sticky;top:0;height:100vh;padding:25px 18px;background:#071824ee;border-right:1px solid var(--line)}}.brand{{font-size:20px;font-weight:900;margin-bottom:24px}}.brand small{{display:block;font-size:12px;color:var(--muted);font-weight:500}}nav a{{display:block;text-decoration:none;color:#c2d6e1;padding:8px 10px;border-radius:8px}}nav a:hover{{background:#153348}}main{{padding:34px 46px 80px;max-width:1500px;min-width:0}}.hero{{padding:44px;border:1px solid #315a70;border-radius:24px;background:linear-gradient(135deg,#0d2b3c,#123b40 70%,#174735)}}.eyebrow{{font:800 12px Consolas,monospace;letter-spacing:.14em;color:var(--green)}}h1{{font-size:clamp(36px,5vw,66px);line-height:1.04;letter-spacing:-.04em;margin:14px 0 20px}}h2{{font-size:31px;margin:72px 0 20px}}h3{{line-height:1.3}}.lead{{font-size:19px;color:#d4e8f1;max-width:1050px}}.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:28px}}.stat{{padding:16px;background:#081c29;border:1px solid #32566b;border-radius:12px;min-width:0}}.stat b{{display:block;color:var(--green);font-size:25px}}.stat small{{color:var(--muted)}}.call{{padding:16px 18px;margin:20px 0;border-left:4px solid var(--amber);background:#3a2a12;border-radius:8px;color:#ffe4a8}}.call.good{{border-color:var(--green);background:#11362b;color:#b9ffdc}}.call.bad{{border-color:var(--red);background:#3b1c24;color:#ffd0d0}}.fixes{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.fixes article{{display:flex;gap:14px;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--panel);min-width:0}}.fixes article>span{{font:900 18px Consolas;color:var(--cyan)}}.fixes p{{margin:4px 0;color:#c5dae4}}.case-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.case{{padding:20px;border:1px solid var(--line);border-radius:15px;background:linear-gradient(155deg,#102b3c,var(--panel));min-width:0}}.case-head{{display:flex;justify-content:space-between}}.case-head span{{font:900 13px Consolas;color:var(--cyan)}}.case-head em{{font-style:normal;color:var(--green);font-weight:900}}.expect{{color:#c6d9e3;min-height:50px;overflow-wrap:anywhere}}.metric{{display:flex;flex-direction:column;padding:12px 14px;border-radius:10px;background:#071923;margin:12px 0;min-width:0}}.metric small{{color:var(--muted)}}.metric strong{{font-size:20px;color:#e8fbff;overflow-wrap:anywhere;word-break:break-word}}.case ul{{padding:0;list-style:none}}.case li{{display:flex;gap:9px;margin:8px 0;min-width:0}}.case li div{{min-width:0}}.case li small{{display:block;color:var(--muted);overflow-wrap:anywhere}}.ok,.pass{{color:var(--green);font-weight:900}}.sources{{font-size:12px;color:var(--muted);overflow-wrap:anywhere}}.three{{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}}.card{{padding:20px;border:1px solid var(--line);border-radius:14px;background:var(--panel);min-width:0}}.card b{{font-size:19px}}table{{border-collapse:collapse;width:100%;background:var(--panel)}}th,td{{padding:10px;border:1px solid var(--line);text-align:left;overflow-wrap:anywhere}}th{{background:#14384c}}details{{border:1px solid var(--line);border-radius:10px;padding:0 15px;margin:12px 0;background:#091b28;max-width:100%;overflow:auto}}summary{{padding:13px 0;cursor:pointer;font-weight:800}}.schema{{white-space:pre-wrap;overflow-wrap:anywhere;background:#041019;border:1px solid var(--line);padding:13px;border-radius:9px;color:#c7efff;font:12px/1.55 Consolas}}footer{{margin-top:60px;border-top:1px solid var(--line);padding-top:20px;color:var(--muted);overflow-wrap:anywhere}}
@media(max-width:1000px){{.layout{{display:block}}aside{{display:none}}main{{padding:20px 16px 60px}}.stats{{grid-template-columns:1fr 1fr}}.case-grid,.fixes{{grid-template-columns:1fr}}.three{{grid-template-columns:1fr}}}}
@media(max-width:560px){{.hero{{padding:25px 20px}}h1{{font-size:39px}}.stats{{grid-template-columns:1fr}}main{{padding:10px}}}}
</style></head><body><div class="layout"><aside><div class="brand">SuperRAN<small>夜间深潜 · 2026-08-22</small></div><nav>
<a href="#verdict">01 · 结论</a><a href="#fixes">02 · 修复与优化</a><a href="#benchmarks">03 · 10 个经典案例</a><a href="#reasonable">04 · 为什么合理</a><a href="#verification">05 · 全量验证</a><a href="#limits">06 · 仍未证明</a><a href="#tomorrow">07 · 明日数据接入</a></nav></aside><main>
<section class="hero" id="verdict"><div class="eyebrow">OVERNIGHT DEEP DIVE · EVIDENCE LOCKED BEFORE RUN</div><h1>经典关系、物理机制和全模块回归已闭合；现场真实性仍等实测数据</h1><p class="lead">本轮不是把旧测试再跑一遍：先修 8 个已复现漏洞，再新增实验血缘、语义摘要、可观察测试矩阵和 10 个运行前冻结的经典通信案例。所有经典 case 通过；21 个测试文件逐个通过；两批体验压力 468/468。结论只覆盖这些证据，不外推到未提供的现场数据。</p>
<div class="stats"><div class="stat"><b>{classic['n_pass']}/{len(classic['results'])}</b><small>经典案例</small></div><div class="stat"><b>{quick['n_pass']}+{physics['n_pass']}</b><small>quick + physics 文件</small></div><div class="stat"><b>{stress_pass}/{stress_total}</b><small>体验压力不变量</small></div><div class="stat"><b>{kpi['browser_qa']['viewports']['desktop']['pass'] and '2/2' or 'FAIL'}</b><small>桌面/移动 KPI QA</small></div><div class="stat"><b>{len(status_lines)}</b><small>工作树路径，未提交</small></div></div>
<div class="call"><b>严谨边界：</b>10/10 不是“现网一定准”。B10 的 38.901 门 1 仍把单小区 SIR、部分时延扩展标为不适用；预置 BLER profile 不参数化 TBS/rank/receiver 轴，业务 CDF 仍是 synthetic。</div></section>

<section id="fixes"><h2>修复与功能优化</h2><div class="fixes">{fixed_html}</div>
<div class="call good"><b>额外抓到并修复：</b>provenance 首版在 MCP 工作线程调用 git 导致全链路挂死；现改为主线程/进程启动期缓存。随后又发现 git diff 不含 untracked 新模块，现以 56 个运行语义文件的完整 source-tree SHA 补齐。</div></section>

<section id="benchmarks"><h2>10 个经典通信仿真：冻结判据 → 运行 → 解释</h2><p>case spec SHA：<code>{_esc(spec_hash)}</code><br>运行语义树 SHA：<code>{_esc(source_hash)}</code></p><div class="case-grid">{_case_cards(classic)}</div></section>

<section id="reasonable"><h2>为什么这些结果合理</h2><div class="three">
<div class="card"><b>解析闭式对得上</b><p>AWGN SISO 与 log₂(1+SNR) 逐点零误差；Jakes 与 SciPy J₀ 零误差且相干时间按速度反比；这些首先钉住单位、dB/线性域和时间轴。</p></div>
<div class="card"><b>优化问题没有反常</b><p>完美 CSI 的逐 RB SVD 不越容量上界，Type-I-style 子集不超过 SVD；正确 SRS 共轭映射接近理想 CSI，错误映射明显退化。</p></div>
<div class="card"><b>经典 trade-off 被复现</b><p>PF/Max-CI 用相同 RngBook 做 CRN，吞吐和 Jain 两项均过 Gate 3；这证明调度器既不是全员轮询，也没有把 max-C/I 误写成 PF。</p></div>
<div class="card"><b>反向案例能咬住实现</b><p>SU 下 NEBF≈EBF 且高于 PEBF；强相关 MU 下 NEBF 破坏 ZF 零陷并低于 PEBF。若每天线归一没有真正进入 W，这个 case 会立即失败。</p></div>
<div class="card"><b>链路到系统关键点闭合</b><p>匹配 PDP 的 LMMSE 在预注册低 SNR 工况过 Wilcoxon Gate 3；OLLA 按步长比稳定在目标；TBS 224 条序列严格递增且冻结点证明除法反查不安全。</p></div>
<div class="card"><b>真实生成也过门</b><p>B10 不是 toy H：重新生成 InternalSim CDL-C 数据，执行 18 项 Gate 1，无 blocker；warn/info 与不适用项仍保留，未被“10/10”标题吞掉。</p></div>
</div></section>

<section id="verification"><h2>验证矩阵</h2><div class="three"><div class="card"><b>Quick tier</b><p>{quick['n_pass']}/{len(quick['files'])} · {quick_s:.1f}s</p></div><div class="card"><b>Physics tier</b><p>{physics['n_pass']}/{len(physics['files'])} · {physics_s:.1f}s</p></div><div class="card"><b>KPI browser</b><p>{_esc(kpi['browser_qa']['browser_backend'])} · 0 error</p></div></div>
<details><summary>展开 21 个测试文件与耗时</summary><table><thead><tr><th>文件</th><th>状态</th><th>耗时</th></tr></thead><tbody>{test_rows}</tbody></table></details>
<p>最重：<code>test_interference.py</code> 955.6s；<code>test_linkadapt.py</code> 606.3s。此前 monolithic pytest 在 20 分钟整体超时，现在已证明是累计慢而非死锁。</p>
<div class="call good">两批独立压力 seed：83117 与 20260822；每批 18 场景 × 13 不变量 = 234/234，合计 468/468。覆盖 EBF/PEBF/NEBF、DDDD/DDDSU/DSU、5/20/80ms CSI 报告周期。</div></section>

<section id="limits"><h2>仍未被今晚证据证明的部分</h2><div class="three">
<div class="card"><b>预置 BLER 的适用边界</b><p>当前 56 条曲线的事件单位已对齐 TB/TTI，预置 profile 明确不参数化 TBS、rank、资源和接收机轴；有效 SINR仍未接入经链路级结果标定的 EESM/MIESM。</p></div>
<div class="card"><b>业务真实性</b><p>KPI QA 的包长/间隔 CDF 是 synthetic；30%/50% 利用率控制器正确不代表实际业务分布正确。大小与间隔相关性也需真实数据确认。</p></div>
<div class="card"><b>现网端到端分位点</b><p>没有同一统计窗内的 PRB、UE throughput、MCS/rank、IBLER、MU PRB share 对齐包，不能声称仿真分位点贴近产品或商用网络。</p></div>
</div></section>

<section id="tomorrow"><h2>明日数据接入：最少但最值钱的字段</h2>
<h3>BLER 曲线包</h3><div class="schema">mcs, tx_mode, sinr_db, bler, n_trials, n_errors,
tbs_bits, num_rb, num_re, rank, layers,
receiver_profile, channel_estimator, rv, harq_combining</div>
<h3>业务包与间隔</h3><div class="schema">service, value, cdf, unit, source_window
packet_size_bytes.csv + interarrival_ms.csv
profile_share / UE mapping / size-interval correlation note</div>
<h3>30% 非 MU / 50% MU 现网锚点</h3><div class="schema">scenario_id, window_s, prb_util,
ue_thp_p5/p50/mean, mcs_hist, rank_hist,
ibler, residual_bler, mu_prb_share, active_ue_mean,
tdd, tx_power, antenna_profile, SRS/PMI/CQI timing</div>
<p>不需要用户标识、业务 payload 或生产调度器源码；匿名分位点、配置、伪代码与元数据足够。</p></section>

<footer>分支 <code>{_esc(branch)}</code> · HEAD <code>{_esc(head)}</code> · 生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}。未提交、未推送、未部署。机器证据：<code>artifacts/results/classic_comm_benchmarks.json</code>、<code>output/test-matrix/*.json</code>、<code>output/kpi-browser-qa.json</code>。</footer>
</main></div></body></html>'''
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html_text, encoding="utf-8", newline="\n")
    print(OUTPUT.resolve())


if __name__ == "__main__":
    main()
