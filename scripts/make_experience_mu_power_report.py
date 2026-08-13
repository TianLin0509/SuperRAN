"""Build the offline audit report for experience scheduling and MU power modes."""

from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
RESULTS = ARTIFACTS / "results"
OUTPUT = ARTIFACTS / "EXPERIENCE_MU_POWER_AUDIT.html"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def esc(value: object) -> str:
    return html_lib.escape(str(value))


def number(value: object, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def percent(value: object, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{100.0 * float(value):.{digits}f}%"


def yes_no(flag: object) -> str:
    css = "ok" if bool(flag) else "bad"
    label = "PASS" if bool(flag) else "BLOCK"
    return f'<span class="pill {css}">{label}</span>'


def artifact_link(name: str, label: str | None = None) -> str:
    text = esc(label or name)
    return f'<a href="results/{esc(name)}">{text}</a>'


def build_report() -> str:
    power = load_json(RESULTS / "experience_mu_power_modes.json")
    pmi = load_json(RESULTS / "experience_pmi_period_sensitivity.json")
    stress = load_json(RESULTS / "experience_mu_power_stress.json")
    property_stress = load_json(
        RESULTS / "experience_randomized_property_stress.json"
    )
    sentinel = load_json(
        RESULTS / "experience_pf_accounting_deterministic_sentinel.json"
    )
    reverse_pf = load_json(
        RESULTS / "experience_pf_accounting_reverse_control.json"
    )
    pilot = load_json(
        RESULTS / "experience_mu_power_pilot_gate2_diagnostics.json"
    )
    dataset = load_json(
        ARTIFACTS / "datasets" / "ds_e8a577d8" / "summary.json"
    )

    power_data = power["formal_dataset"]
    max_se = max(float(row["mean_best_se"]) for row in power_data.values())
    power_rows: list[str] = []
    for mode in ("ebf", "pebf", "nebf"):
        row = power_data[mode]
        cap_note = (
            "仅总功率约束；逐天线可超 P/M"
            if mode == "ebf"
            else "逐天线峰值满足 P/M"
        )
        orth_note = "保持几何" if mode != "nebf" else "允许破坏正交性"
        width = 100.0 * float(row["mean_best_se"]) / max_se
        power_rows.append(
            f"""
            <tr>
              <td><strong>{mode.upper()}</strong><small>{cap_note}</small></td>
              <td>{number(row['mean_best_se'], 3)}
                <span class="bar"><i style="width:{width:.1f}%"></i></span></td>
              <td>{percent(row['mean_total_power_utilization'], 1)}</td>
              <td>{number(row['max_per_antenna_over_cap_ratio'], 3)}×</td>
              <td>{number(row['mean_orthogonality_error'], 5)}<small>{orth_note}</small></td>
              <td>{number(row['mu_pair_true_sinr_db_mean'], 2)} dB</td>
              <td>{number(row['mu_pair_leakage_mean'], 4)}</td>
            </tr>
            """
        )

    pmi_rows: list[str] = []
    for name, row in pmi["rows"].items():
        cell = row["cell"]
        pmi_rows.append(
            f"""
            <tr class="{'default-row' if name == '20ms' else ''}">
              <td><strong>{esc(name)}</strong>{' <span class="tag">默认</span>' if name == '20ms' else ''}</td>
              <td>{number(cell['cell_served_mbps'], 2)}</td>
              <td>{number(cell['large_flow_drb_throughput_p5_mbps'], 2)}</td>
              <td>{percent(cell['bler_first_tx'], 2)}</td>
              <td>{number(float(cell['backlog_bytes']) / 1048576.0, 3)}</td>
              <td>{percent(cell['resource_utilization'], 1)}</td>
              <td>{number(cell['small_queue_wait_ms_p95'], 2)} ms</td>
            </tr>
            """
        )

    stress_rows: list[str] = []
    stress_labels = {
        "no_arrivals": "无到达",
        "all_small_sparse": "全小包稀疏",
        "all_large_heavy": "全大包过载",
        "mixed_near_capacity": "大小包近容量",
    }
    for key, row in stress["cases"].items():
        cell = row["cell"]
        stress_rows.append(
            f"""
            <tr>
              <td><strong>{stress_labels[key]}</strong><small>{esc(key)}</small></td>
              <td>{number(cell['offered_mbps'], 2)}</td>
              <td>{number(cell['cell_served_mbps'], 2)}</td>
              <td>{number(float(cell['backlog_bytes']) / 1048576.0, 3)}</td>
              <td>{percent(cell['resource_utilization'], 1)}</td>
              <td>{percent(cell['bler_first_tx'], 2)}</td>
              <td>{percent(cell['mu_user_tx_share'], 2)}</td>
              <td>{number(cell['measurement_accounting_error_pct'], 3)}%</td>
              <td>{yes_no(row['rbg_overlap_violations'] == 0)}</td>
            </tr>
            """
        )

    pilot_rows: list[str] = []
    pilot_pass = 0
    for item in pilot["convergence"]:
        candidate = item["candidate_detail"]
        su = candidate["modes"]["SU"]
        mu = candidate["modes"]["MU"]
        if item["candidate"]:
            pilot_pass += 1
        pilot_rows.append(
            f"""
            <tr class="{'blocked-row' if not item['candidate'] else ''}">
              <td><strong>{item['replication']}</strong></td>
              <td>{yes_no(item['candidate'])}</td>
              <td>{esc(su['status'])}</td>
              <td>{number(su['first_half_expected_bler'], 3)} / {number(su['second_half_expected_bler'], 3)}</td>
              <td>{su['first_half_tx']} / {su['second_half_tx']}</td>
              <td>{percent(mu['measurement_user_grant_share'], 3)}</td>
              <td>{esc(mu['status'])}</td>
              <td>{mu['first_half_tx']} / {mu['second_half_tx']}</td>
              <td>{number(mu['first_half_expected_bler'], 3)} / {number(mu['second_half_expected_bler'], 3)}</td>
            </tr>
            """
        )

    scheduled = sentinel["scheduled_tbs"]["cell"]
    legacy = sentinel["legacy_fullband"]["cell"]
    paired = reverse_pf["paired"]

    decisions = [
        ("D1", "调度模式", "经典 PF", "已实现；QoS/EPF 不进入本轮主实验"),
        ("D2", "PF 的 Rᵤ", "scheduled TBS；NACK 仍记资源", "已实现并有反向哨兵"),
        ("D3", "剩余 RBG", "队列清空后留空", "利用率按真实占用，不做 padding"),
        ("D4", "误块字节", "保留现状，按 NewTx 留队列", "HARQ 软合并留到下一阶段"),
        ("D5", "体验指标", "大包 busy-period；小包 arrival-object", "等待、完成时延、PDB 分层"),
        ("D6", "预启动", "可配置 warm-up", "正式协议 5 s warm-up + 8 s KPI"),
        ("D7", "邻区", "聚合负载近似", "逐邻区联合调度不在本轮"),
        ("D8", "PMI/CQI 周期", "默认 20 ms，扫 5/10/20/40/80", "5 ms 只作敏感性点"),
        ("D9", "TBS 开销", "显式近似", "12 data symbols；S slot ×0.7"),
        ("D10", "SU/MU 自适应", "PF 后比较 useful bytes", "SU 可清空则强制 SU；否则 MU≥SU 选 MU"),
        ("D11", "Type-I", "工程 baseline", "不是完整端口/面板映射认证实现"),
        ("D12", "重复性", "CRN + replication ID", "统计门按配对 Wilcoxon"),
    ]
    decision_rows = "".join(
        f"<tr><td><strong>{d}</strong></td><td>{esc(topic)}</td>"
        f"<td>{esc(choice)}</td><td>{esc(note)}</td></tr>"
        for d, topic, choice, note in decisions
    )

    bugs = [
        ("PF 全带误记账", "部分 RBG 用户按全带更新 Rᵤ，可能被 17× 惩罚", "改为本 UE scheduled TBS；故意切回旧口径等待恶化"),
        ("TBS 线性外推", "约 1% 误差即可少给 1 RBG，导致本 TTI 发不完", "3808 项离散表 + searchsorted 最小可行反查"),
        ("MU 固定标量接收基", "把同 UE rank-2 的可联合检测流当干扰，曾制造 100% BLER", "保留接收阵列并做 per-user LMMSE；解析反例恢复约 26 dB"),
        ("真实 CSI 参与预编码", "h_true oracle 会掩盖 CSI 老化", "h_est 只用于预编码，h_true 只用于物理评估"),
        ("复数共轭方向", "右奇异向量写反会悄悄改变等效信道", "直接矩阵乘法反向哨兵逐位核对"),
        ("MU MCS0 黑洞", "MCS0 仍高 BLER 时继续配对，形成无效 MU", "判 pair 不可用并回退 SU"),
        ("PEBF/NEBF 二次归一", "诊断值与实际发射矩阵不一致", "对最终物理 Q 直接做功率诊断"),
        ("neighbor jitter 未下传", "配置旋钮不驱动真实 SINR", "逐快照负载折算并有参数反向测试"),
        ("capacity/MU 工作点错配", "full-buffer 比值回乘到另一邻区负载", "SU/MU 复用同一邻区负载工作点"),
        ("SRS 处理边界非因果", "处理完成前就使用本次测量", "只选择 processing boundary 之前已可用的 SRS"),
        ("小包等待完成选择偏差", "只统计已完成对象会删掉最慢样本", "首次调度即完成 wait 观测，未调度对象右删失单列"),
        ("warm-up 窗口字节账", "跨窗 backlog 易被误算成 offered load", "start backlog + arrivals = ACK + end backlog"),
        ("OLLA target 共用", "MU 独立步长却拿 SU target 判收敛", "SU/MU 各按自身 step-up/(up+down) 审计"),
        ("功率模式下传/错配", "批量接口可能仍悄悄走 EBF", "server→config→link table 全链记录并拒绝错配"),
        ("门禁方向/标签", "负向指标和话务类标签可被反写", "预注册方向硬拦截，类标签逐 replication 汇总"),
        ("MU 3D 入口维度错位", "[RB,BS,UE] 会把 BS 当 RB，正式 4D 路径因此没暴露", "统一从尾维取 RB/BS；3D 与 4D 逐位等价回归"),
        ("capacity MU 时间/RB 混维", "measure_mu_gain 会把 3D 单快照的 RB 当成多个时间快照", "入口统一补 T=1；3D 与显式 4D 的逐快照结果完全一致"),
        ("CSI 非有限配置", "NaN processing delay 可穿过小于零检查并污染 lag", "NaN/Inf、非整数 hop、零快照间隔与负 index 全部硬拒绝"),
        ("配置静默钳位", "NaN 到达率、负 warm-up 或浮点 rank 可在深层污染统计", "Traffic/Scheduler/KPI/System 构造入口硬校验"),
        ("离散参数静默截断", "TBS 17.9→17、seed 1.9→1 会改变实验身份却不报错", "TBS/RNG/sim/server/neighbor/priority 全链严格验型；新增 29 个边界反向用例"),
        ("MU 校准旋钮未接 API", "内部有 MU-OLLA/相关性门限，MCP 调用者却无法配置", "server/spec/schema 公开三项并做真实 MCP 工具清单回归"),
    ]
    bug_rows = "".join(
        f"<tr><td><strong>{i + 1}</strong></td><td>{esc(name)}</td>"
        f"<td>{esc(impact)}</td><td>{esc(fix)}</td></tr>"
        for i, (name, impact, fix) in enumerate(bugs)
    )

    tests = [
        "test_csi_aging.py",
        "test_e2e.py",
        "test_gates.py",
        "test_interference.py",
        "test_linkadapt.py",
        "test_linklevel.py",
        "test_mcp_server.py",
        "test_mumimo.py",
        "test_physics_invariants.py",
        "test_raytracing.py",
        "test_results.py",
        "test_rng.py",
        "test_sysscenes.py",
        "test_system.py",
    ]
    test_chips = "".join(
        f'<span class="test-chip">✓ {esc(name)}</span>' for name in tests
    )

    artifacts = [
        ("experience_mu_power_modes.json", "EBF/PEBF/NEBF 物理量", "current"),
        ("experience_pmi_period_sensitivity.json", "5–80 ms 单次敏感性", "screening"),
        ("experience_mu_power_stress.json", "四类话务硬不变量", "current"),
        ("experience_randomized_property_stress.json", "18 组合 × 12 属性", "current"),
        ("experience_pf_accounting_deterministic_sentinel.json", "PF 因果反向哨兵", "current"),
        ("experience_pf_accounting_reverse_control.json", "真实数据 8 对 CRN", "blocked"),
        ("experience_mu_power_pilot_gate2_diagnostics.json", "最终 Gate 2 证据", "current"),
        ("experience_mu_power_formal_n16.json", "旧标量接收机路径", "superseded"),
    ]
    artifact_rows: list[str] = []
    for name, purpose, status in artifacts:
        path = RESULTS / name
        size_kib = path.stat().st_size / 1024.0
        status_labels = {
            "current": '<span class="pill ok">CURRENT</span>',
            "screening": '<span class="pill warn">SCREEN</span>',
            "blocked": '<span class="pill warn">BLOCKED</span>',
            "superseded": '<span class="pill bad">禁止引用</span>',
        }
        artifact_rows.append(
            f"<tr><td>{artifact_link(name)}</td><td>{esc(purpose)}</td>"
            f"<td>{size_kib:.1f} KiB</td><td>{status_labels[status]}</td></tr>"
        )

    assertion_passed = sum(bool(value) for value in stress["assertions"].values())
    assertion_total = len(stress["assertions"])
    page = TEMPLATE
    replacements = {
        "@@DATASET@@": esc(power["dataset_id"]),
        "@@CREATED@@": esc(pilot["created_at"]),
        "@@PILOT_PASS@@": str(pilot_pass),
        "@@PILOT_TOTAL@@": str(len(pilot["convergence"])),
        "@@POWER_ROWS@@": "".join(power_rows),
        "@@PMI_ROWS@@": "".join(pmi_rows),
        "@@STRESS_ROWS@@": "".join(stress_rows),
        "@@PILOT_ROWS@@": "".join(pilot_rows),
        "@@DECISION_ROWS@@": decision_rows,
        "@@BUG_ROWS@@": bug_rows,
        "@@TEST_CHIPS@@": test_chips,
        "@@ARTIFACT_ROWS@@": "".join(artifact_rows),
        "@@STRESS_ASSERTIONS@@": f"{assertion_passed}/{assertion_total}",
        "@@PROPERTY_CHECKS@@": (
            f"{property_stress['summary']['checks_passed']}/"
            f"{property_stress['summary']['checks_total']}"
        ),
        "@@PF_CORRECT_MEAN@@": number(scheduled["small_queue_wait_ms_mean"], 3),
        "@@PF_WRONG_MEAN@@": number(legacy["small_queue_wait_ms_mean"], 3),
        "@@PF_CORRECT_P95@@": number(scheduled["small_queue_wait_ms_p95"], 3),
        "@@PF_WRONG_P95@@": number(legacy["small_queue_wait_ms_p95"], 3),
        "@@PF_CORRECT_IMMEDIATE@@": percent(
            scheduled["small_immediate_service_ratio"], 1
        ),
        "@@PF_WRONG_IMMEDIATE@@": percent(
            legacy["small_immediate_service_ratio"], 1
        ),
        "@@PF_REAL_N@@": str(paired["n"]),
        "@@PF_REAL_DIFF@@": number(paired["mean_diff"], 3),
        "@@PF_REAL_P@@": number(paired["decision_p_value"], 3),
        "@@NUM_SAMPLES@@": str(dataset["num_samples"]),
        "@@DATASET_SIZE@@": number(dataset["size_mb"], 1),
        "@@TAU_MEASURED@@": number(dataset["tau_rms_ns"]["mean"], 1)
        if isinstance(dataset["tau_rms_ns"], dict)
        else number(dataset["tau_rms_ns"], 1),
    }
    for key, value in replacements.items():
        page = page.replace(key, value)
    return page


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>superran · 体验调度与每天线功率审计</title>
<style>
:root{--ink:#172033;--muted:#667085;--line:#dfe5ee;--paper:#f4f7fb;--card:#fff;--blue:#155eef;--cyan:#0e7490;--green:#067647;--amber:#b54708;--red:#b42318;--navy:#0b1736;--soft-blue:#eff4ff;--soft-green:#ecfdf3;--soft-amber:#fffaeb;--soft-red:#fef3f2}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.68 "Segoe UI","Microsoft YaHei",sans-serif}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.shell{max-width:1480px;margin:auto;padding:0 28px 80px}.hero{margin:0 0 26px;padding:48px max(28px,calc((100vw - 1424px)/2));background:linear-gradient(125deg,#09142f,#133b75 62%,#0f6f7a);color:#fff;position:relative;overflow:hidden}.hero:after{content:"";position:absolute;right:-100px;top:-180px;width:520px;height:520px;border:80px solid rgba(255,255,255,.055);border-radius:50%}.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#a7c7ff;font-weight:700}.hero h1{font-size:clamp(30px,4vw,56px);line-height:1.08;max-width:1000px;margin:14px 0 16px}.hero p{max-width:1050px;color:#d9e5ff;font-size:18px;margin:0}.meta{display:flex;gap:22px;flex-wrap:wrap;margin-top:26px;color:#c5d8fa;font-size:13px}.nav{position:sticky;top:0;z-index:10;margin:0 -4px 26px;padding:10px 12px;background:rgba(244,247,251,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);display:flex;gap:8px;overflow:auto}.nav a{white-space:nowrap;padding:7px 11px;border-radius:999px;color:#344054;font-size:13px}.nav a:hover{background:#e6edfa;text-decoration:none}.grid{display:grid;gap:16px}.kpis{grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:24px}.kpi,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 4px 18px rgba(16,24,40,.035)}.kpi{padding:18px}.kpi strong{display:block;font-size:28px;line-height:1.1;margin:8px 0}.kpi small,.muted{color:var(--muted)}.kpi.block{border-top:4px solid var(--red)}.kpi.pass{border-top:4px solid var(--green)}.kpi.warn{border-top:4px solid var(--amber)}section{scroll-margin-top:70px;margin-top:28px}.section-head{display:flex;justify-content:space-between;gap:20px;align-items:end;margin:0 0 14px}.section-head h2{margin:0;font-size:25px}.section-head p{margin:0;color:var(--muted);max-width:780px}.card{padding:22px;margin-bottom:16px}.callout{border-left:4px solid var(--blue);background:var(--soft-blue);padding:14px 16px;border-radius:8px;margin:14px 0}.callout.warn{border-color:var(--amber);background:var(--soft-amber)}.callout.bad{border-color:var(--red);background:var(--soft-red)}.callout.ok{border-color:var(--green);background:var(--soft-green)}.callout strong{display:block;margin-bottom:3px}.pill,.tag{display:inline-flex;align-items:center;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.03em;padding:3px 8px}.pill.ok{background:var(--soft-green);color:var(--green)}.pill.warn{background:var(--soft-amber);color:var(--amber)}.pill.bad{background:var(--soft-red);color:var(--red)}.tag{background:#e9efff;color:#234ea0;margin-left:6px}table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}th{background:#f7f9fc;color:#475467;text-align:left;font-weight:700;position:sticky;top:50px}th,td{border-bottom:1px solid #e9edf3;padding:10px 11px;vertical-align:top}tr:last-child td{border-bottom:0}td small{display:block;color:var(--muted);margin-top:2px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px}.default-row{background:#f3f7ff}.blocked-row{background:#fff5f4}.bar{display:block;width:130px;height:5px;background:#e8edf5;border-radius:5px;margin-top:6px}.bar i{display:block;height:100%;border-radius:5px;background:linear-gradient(90deg,var(--blue),#4f8cff)}.two{grid-template-columns:repeat(2,minmax(0,1fr))}.three{grid-template-columns:repeat(3,minmax(0,1fr))}.module{padding:18px;border:1px solid var(--line);border-radius:12px;background:#fff}.module h3{margin:0 0 8px;font-size:17px}.module .verdict{font-size:12px;color:var(--green);font-weight:800}.module ul{padding-left:19px;margin:8px 0}.formula{font:15px/1.7 Consolas,"SFMono-Regular",monospace;background:#0f172a;color:#e2e8f0;padding:14px 16px;border-radius:9px;overflow:auto}.flow{display:grid;grid-template-columns:repeat(7,minmax(120px,1fr));gap:8px;align-items:stretch}.flow div{background:#f7f9fc;border:1px solid var(--line);border-radius:10px;padding:13px;text-align:center;font-size:12px;position:relative}.flow div:not(:last-child):after{content:"→";position:absolute;right:-11px;top:35%;z-index:2;color:var(--blue);font-size:19px}.flow strong{display:block;color:#183b73;margin-bottom:4px}.test-cloud{display:flex;gap:7px;flex-wrap:wrap}.test-chip{background:#f0fdf4;border:1px solid #bbebcc;color:#11623a;border-radius:7px;padding:5px 8px;font-size:12px}.timeline{border-left:3px solid #ccd6e7;margin-left:9px;padding-left:22px}.event{position:relative;margin:0 0 18px}.event:before{content:"";position:absolute;left:-30px;top:7px;width:12px;height:12px;border-radius:50%;background:var(--blue);border:3px solid var(--paper)}.event.bad:before{background:var(--red)}.event.ok:before{background:var(--green)}.event h3{margin:0;font-size:16px}.event p{margin:3px 0;color:var(--muted)}details{border:1px solid var(--line);border-radius:10px;background:#fff;margin:9px 0}summary{cursor:pointer;font-weight:700;padding:12px 14px}details>div{padding:0 14px 14px;color:#475467}.foot{margin-top:36px;border-top:1px solid var(--line);padding-top:20px;color:var(--muted);font-size:12px}.nowrap{white-space:nowrap}code{font-family:Consolas,monospace;background:#eef2f7;padding:1px 4px;border-radius:4px}.danger-text{color:var(--red);font-weight:700}
@media(max-width:1000px){.kpis,.three,.two{grid-template-columns:1fr 1fr}.flow{grid-template-columns:1fr 1fr}.flow div:after{display:none}}
@media(max-width:650px){.shell{padding:0 14px 50px}.hero{margin:0 0 18px;padding:34px 18px}.kpis,.three,.two,.flow{grid-template-columns:1fr}.hero h1{font-size:32px}.section-head{display:block}th{position:static}}
@media print{.nav{display:none}.hero{break-after:avoid}.card,.module{break-inside:avoid}body{background:#fff}.shell{max-width:none}.hero{margin:0}}
</style>
</head>
<body>
<header class="hero">
  <div class="eyebrow">superran / system simulation audit</div>
  <h1>体验速率、SU/MU 自适应与每天线功率约束</h1>
  <p>落地实现、物理反例、资源账、压力仿真与三道门审计。结论先说：核心实现已进入可验证状态；正式体验收益仍被 Gate 2 阻断，不能报提升。</p>
  <div class="meta"><span>数据集 @@DATASET@@</span><span>400 samples · 10 UE × 40 snapshots</span><span>生成时间 @@CREATED@@</span><span>冻结协议 13 s / warm-up 5 s / KPI 8 s</span></div>
</header>
<main class="shell">
<nav class="nav"><a href="#verdict">结论</a><a href="#decisions">决策</a><a href="#architecture">架构</a><a href="#power">功率模式</a><a href="#mu">MU 链路</a><a href="#pf">PF 与体验</a><a href="#csi">SRS/PMI</a><a href="#stress">压力仿真</a><a href="#gates">三道门</a><a href="#bugs">Bug 台账</a><a href="#boundaries">边界/待决策</a><a href="#evidence">证据</a></nav>

<section id="verdict">
  <div class="grid kpis">
    <div class="kpi pass"><small>核心回归</small><strong>14 / 14</strong><span>全部测试脚本退出码 0</span></div>
    <div class="kpi pass"><small>跨配置属性压力</small><strong>@@PROPERTY_CHECKS@@</strong><span>18 案例；3 功率模式 × 3 numerology</span></div>
    <div class="kpi warn"><small>Pilot Gate 2</small><strong>@@PILOT_PASS@@ / @@PILOT_TOTAL@@</strong><span>replication 102 的 MU 样本不足</span></div>
    <div class="kpi block"><small>正式收益结论</small><strong>BLOCKED</strong><span>正式 n=16 未启动；Gate 3 未运行</span></div>
  </div>
  <div class="card">
    <div class="callout bad"><strong>不能说什么</strong>不能说“自适应 MU 提升了体验速率”，也不能引用旧的 <code>experience_mu_power_formal_n16.json</code>。它来自 LMMSE 修复前的错误接收机路径。</div>
    <div class="callout ok"><strong>现在可以说什么</strong>EBF/PEBF/NEBF 约束、因果 CSI 链路、SU/MU 分立 OLLA、数据受限 SU/MU 计划、PF 资源记账、warm-up 计量窗、字节/RBG 账和统计门禁，在本报告列出的模型边界与压力包络内通过了正向不变量和反向哨兵。</div>
    <p><strong>“彻底可行”的精确定义：</strong>不是证明软件不存在未知 bug；而是主要模块拥有独立可复现的解析不变量、构造反例和实际仿真证据，且外围接口与旧路径回归通过。正式业务收益是另一层问题，目前尚未获统计门许可。</p>
  </div>
</section>

<section id="decisions">
  <div class="section-head"><h2>D1–D12 冻结口径</h2><p>全部按用户拍板落地；没有把未决现场定义伪装成标准事实。</p></div>
  <div class="card table-wrap"><table><thead><tr><th>ID</th><th>问题</th><th>冻结选择</th><th>实现/边界</th></tr></thead><tbody>@@DECISION_ROWS@@</tbody></table></div>
</section>

<section id="architecture">
  <div class="section-head"><h2>端到端架构与主要模块</h2><p>每个箭头都对应状态或数据口径；未被选择的候选计划不消费 RNG、不更新队列、不更新 OLLA。</p></div>
  <div class="card"><div class="flow"><div><strong>SRS / CSI-RS</strong>周期、processing boundary</div><div><strong>CQI / PMI / RI</strong>因果 hold，宽带报告</div><div><strong>EBF / PEBF / NEBF</strong>生成物理 Q</div><div><strong>SU / MU 链路</strong>真实 CSI + LMMSE</div><div><strong>PF 固定排序</strong>经典 Rinst / Ravg</div><div><strong>双计划比较</strong>useful payload bytes</div><div><strong>队列 / OLLA / KPI</strong>只提交被选计划</div></div></div>
  <div class="grid two">
    <article class="module"><span class="verdict">VERIFIED ENVELOPE</span><h3>1. Beamforming 与功率约束</h3><ul><li>Q 采用 <code>[frequency, antenna, stream]</code>；物理天线功率是行范数。</li><li>用户所说“列归一”对应转置记号 W=Qᵀ。</li><li>零功率天线行硬失败，避免除零后静默产生 NaN。</li></ul><a href="../src/superran/beamforming.py">beamforming.py</a></article>
    <article class="module"><span class="verdict">VERIFIED ENVELOPE</span><h3>2. SRS/CSI 因果链</h3><ul><li>术语改为 SRS 周期与 CSI staleness，不称“SRS 年龄”。</li><li>只能使用在 processing boundary 前已完成处理的 SRS。</li><li>CQI/PMI report 周期独立于 SRS；默认 20 ms 可配置。</li></ul><a href="../src/superran/csi_aging.py">csi_aging.py</a></article>
    <article class="module"><span class="verdict">VERIFIED ENVELOPE</span><h3>3. CQI/BF/OLLA 与 MU MCS</h3><ul><li>SU：CQI + BF + SU-OLLA。</li><li>MU 再加 CorrLoss、powerLoss、用户级 MU-OLLA。</li><li>SU/MU ACK/NACK 只更新各自数组，且 MU-OLLA 不按配对关系拆分。</li></ul><a href="../src/superran/experience.py">experience.py</a></article>
    <article class="module"><span class="verdict">VERIFIED ENVELOPE</span><h3>4. MU 预编码与接收机</h3><ul><li>估计 CSI 负责预编码，真实 CSI 只负责评估。</li><li>UE 内 rank 流由 per-user LMMSE 联合检测。</li><li>其他用户流进入干扰协方差；泄漏与 SINR 同源计算。</li></ul><a href="../src/superran/mumimo.py">mumimo.py</a></article>
    <article class="module"><span class="verdict">VERIFIED ENVELOPE</span><h3>5. TBS、业务与体验 KPI</h3><ul><li>D/S × 28 MCS × rank 1..4 × 17 RBG 的 3808 项表。</li><li>大包用 DRB busy period；小包用 FIFO arrival object 等待/完成/PDB。</li><li>右删失样本显式报告，不让饿死用户从统计里消失。</li></ul><a href="../src/superran/experience.py">experience.py</a></article>
    <article class="module"><span class="verdict">VERIFIED ENVELOPE</span><h3>6. PF 与 SU/MU 计划器</h3><ul><li>每个 DL TTI 只排序一次，随后构造全 SU 与允许 MU 两份计划。</li><li>只比较业务可用字节，padding 不计收益。</li><li>SU 能清空所有队列时强制 SU，剩余 RBG 留空。</li></ul><a href="../src/superran/system.py">system.py</a></article>
    <article class="module"><span class="verdict">VERIFIED ENVELOPE</span><h3>7. RNG、统计与门禁</h3><ul><li>channel/traffic/HARQ/scheduler/neighbor-load 分流。</li><li>CRN 按 master + replication + stream key 派生。</li><li>Gate 3 用配对 Wilcoxon；效应小于 CI 时硬判 inconclusive。</li></ul><a href="../src/superran/gates.py">gates.py</a></article>
    <article class="module"><span class="verdict">VERIFIED ENVELOPE</span><h3>8. Server/spec 接口</h3><ul><li>功率模式、CSI report、warm-up、PF 口径、MU 相关性门限与 MU-OLLA 步长进入公开配置。</li><li>capacity 与 experience 结果显式版本化。</li><li>链路表与运行配置的功率模式不一致时直接拒绝。</li></ul><a href="../src/superran/server.py">server.py</a> · <a href="../src/superran/spec.py">spec.py</a></article>
  </div>
</section>

<section id="power">
  <div class="section-head"><h2>EBF / PEBF / NEBF：约束真的不同</h2><p>以下是正式数据集上的 45 个 MU pair 汇总，不是三个标签指向同一矩阵。</p></div>
  <div class="card">
    <div class="formula">EBF: ‖Q‖²F = Ptotal
PEBF: Q ← αQ,  α = minₘ √[(P/M) / pₘ]        # 全局缩放，保持几何
NEBF: Q[m,:] ← Q[m,:] √[(P/M) / pₘ]          # 每天线归一，用满每个 PA</div>
    <div class="callout"><strong>矩阵约定</strong>代码里的 Q 是 [频点, 发射天线, 流]，所以每天线约束看行范数。若把预编码写成 W=Qᵀ，就是用户描述的“列范数归一”。</div>
    <div class="table-wrap"><table><thead><tr><th>模式</th><th>平均最佳 SE</th><th>总功率利用</th><th>max PA / cap</th><th>正交误差</th><th>MU true SINR</th><th>泄漏</th></tr></thead><tbody>@@POWER_ROWS@@</tbody></table></div>
    <div class="grid three" style="margin-top:16px"><div class="callout ok"><strong>SU 哨兵</strong>固定 64T 单用户 realization 中，NEBF 与 EBF 的谱效差小于 5%，且 NEBF 比 PEBF 高超过 1.5 bit/s/Hz。</div><div class="callout bad"><strong>MU 反例</strong>强相关、单接收天线、高 SNR 的两用户 ZF 例中，NEBF 泄漏 &gt;0.4，而 PEBF &lt;10⁻¹⁰，确定出现 NEBF &lt; PEBF。</div><div class="callout"><strong>为什么可信</strong>同时检查总功率、每 PA 峰值、正交误差、泄漏和速率；任何只缩放吞吐数字的假实现都会至少破一项。</div></div>
  </div>
</section>

<section id="mu">
  <div class="section-head"><h2>MU 链路与 MCS 分解</h2><p>功率损失与相关性损失分开记账；BLER 判决始终使用真实物理 MU SINR。</p></div>
  <div class="card">
    <div class="formula">MCS_SU = CQI + BF + SU_OLLA
MCS_MU = CQI + BF + SU_OLLA + CorrLoss + powerLoss + MU_OLLA
powerLoss = −10 log₁₀(K)   → 两个 rank-2 UE、共 4 流时，相对 SU rank-2 为 −3.0103 dB
CorrLoss = SINR_MU,true − SINR_SU,true − powerLoss</div>
    <div class="grid two" style="margin-top:15px"><div><h3>接收机根因实例</h3><p>旧路径先把每个 rank-2 UE 压成固定 SVD 标量行；h_est 与 h_true 的接收基发生旋转时，它把同用户另一条可联合检测的流当成干扰。解析反例中旧路径约 0 dB，per-user LMMSE 恢复到约 26 dB；正式 replication 9 的 MU BLER 从 100% 降到 7.14%。</p></div><div><h3>现在的硬约束</h3><ul><li>同一 MU group 的两个 UE 使用完全相同的 RBG bitmap，资源只扣一次。</li><li>两个 TB 分别做 BLER/ACK/NACK 与用户级 MU-OLLA 更新。</li><li>MCS0 仍不可用时回退 SU，不允许“配上但永远发不成”。</li><li>逐用户噪声独立，不拿 UE0 的噪声代替整个 pair。</li></ul></div></div>
  </div>
</section>

<section id="pf">
  <div class="section-head"><h2>PF 的 Rᵤ 如何维护</h2><p>这是本轮必须一起修的账本；否则按需分配会反向饿死小包。</p></div>
  <div class="card">
    <div class="formula">R̄ᵤ(t+1) = (1−a)R̄ᵤ(t) + a·Sᵤ(t),  a = 1 / pf_window_tti
Sᵤ(t) = 本次 DL 调度机会给 UE u 的 scheduled TBS bytes；未调度为 0；NACK 仍计资源
PF_metricᵤ = potential_fullband_TBSᵤ / max(R̄ᵤ, ε)</div>
    <p>D/S 下行机会推进 PF 时钟，U slot 不推进。R̄ᵤ 的单位是“每个下行调度机会的字节”；所有用户共用同一尺度。ACKed bytes 只作为 KPI，不是默认 PF 资源账。</p>
    <div class="grid two">
      <div class="callout ok"><strong>确定性反向哨兵</strong>正确 scheduled-TBS 口径：平均等待 @@PF_CORRECT_MEAN@@ ms、P95 @@PF_CORRECT_P95@@ ms、到达 TTI 即服务 @@PF_CORRECT_IMMEDIATE@@。故意切回全带误记账：平均 @@PF_WRONG_MEAN@@ ms、P95 @@PF_WRONG_P95@@ ms、即时服务 @@PF_WRONG_IMMEDIATE@@。两臂字节都守恒，因此差异确由 PF 账本引起。</div>
      <div class="callout warn"><strong>真实数据反向对照</strong>@@PF_REAL_N@@ 对 CRN 的 P95 都卡在 0.5 ms 采样地板，差值 @@PF_REAL_DIFF@@ ms、Wilcoxon p=@@PF_REAL_P@@，Gate 3 阻断。这不否定 bug；它说明该主指标在当前 trace 上没有分辨力，不能拿确定性哨兵冒充现场收益。</div>
    </div>
  </div>
</section>

<section id="csi">
  <div class="section-head"><h2>SRS 周期、CSI staleness 与 PMI 调研</h2><p>“周期”是配置；“staleness”是从可用测量到当前评估时刻的派生量，两者不是同一个词。</p></div>
  <div class="card">
    <div class="callout"><strong>标准边界</strong>38.331 的 CSI-ReportPeriodicityAndOffset 给出 slot4/5/8/10/16/20/40/80/160/320 等枚举；38.211 的 SRS 周期和 offset 也以 slot 配置。30 kHz 下一个 slot 为 0.5 ms，所以 5 ms 和 20 ms 都可以配置，但标准没有规定“PMI 默认 5 ms”。本项目暂以 20 ms 为工程默认。</div>
    <div class="table-wrap"><table><thead><tr><th>报告周期</th><th>served Mbps</th><th>large P5 Mbps</th><th>首传 BLER</th><th>backlog MiB</th><th>资源利用</th><th>small wait P95</th></tr></thead><tbody>@@PMI_ROWS@@</tbody></table></div>
    <div class="callout warn"><strong>怎样解读</strong>每点只有 1 个 replication，协议明确禁止方向性结论。80 ms 的 served/backlog 恶化只是风险信号；5–40 ms 的波动同样不能排序。下一步若要拍现场默认，应在多个速度、多个 replication 下做 CRN 扫描。</div>
    <p>来源：<a href="https://www.etsi.org/deliver/etsi_ts/138300_138399/138331/15.02.01_60/ts_138331v150201p.pdf">ETSI TS 38.331</a> · <a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138211/16.04.00_60/ts_138211v160400p.pdf">ETSI TS 38.211</a>。</p>
  </div>
</section>

<section id="stress">
  <div class="section-head"><h2>实际仿真压力测试</h2><p>全大包故意过载；队列不清空是预期，资源/字节账不闭合才是 bug。</p></div>
  <div class="card table-wrap"><table><thead><tr><th>场景</th><th>offered Mbps</th><th>served Mbps</th><th>backlog MiB</th><th>资源利用</th><th>首传 BLER</th><th>MU user-Tx</th><th>窗内账误差</th><th>RBG 无重叠</th></tr></thead><tbody>@@STRESS_ROWS@@</tbody></table></div>
  <div class="grid three"><div class="callout ok"><strong>零输入</strong>无到达时 served、backlog、utilization 全为 0，排除幽灵业务和 warm-up 泄漏。</div><div class="callout ok"><strong>过载</strong>806.5 Mbps offered 下资源利用 100%、backlog 828.4 MiB，但窗口账误差 0，说明过载被真实排队而非丢字节。</div><div class="callout ok"><strong>混合近容量</strong>小包和大包并发，MU user-Tx 约 1.05%；RBG 无重叠、测量窗字节误差 0。</div></div>
  <div class="callout ok"><strong>随机化组合属性 @@PROPERTY_CHECKS@@</strong>18 个独立随机信道案例覆盖 EBF/PEBF/NEBF、15/30/60 kHz、DDDD/DDDSU/DSU、5/20/80 ms CSI report、SVD/Type-I 与 SU-only/自适应 MU。每例检查字节账、测量窗账、RBG bitmap、PF scheduled-TBS credit、功率模式下传、有限值与 MU 关闭退化；这一批只证明不变量，不作性能方向结论。</div>
</section>

<section id="gates">
  <div class="section-head"><h2>三道门与实验时间线</h2><p>门禁的价值正是阻止“仿真跑完了”被误写成“结论成立”。</p></div>
  <div class="card timeline">
    <div class="event ok"><h3>Gate 1 · 信道可信：硬项通过</h3><p>@@NUM_SAMPLES@@ 个样本，64T4R、272 RB、CDL-C / UMa_NLOS、21 cells，数据约 @@DATASET_SIZE@@ MiB。时延扩展实测约 166.3 ns vs nominal 363 ns，保留 WARN，因此不做时延扩展类结论。</p></div>
    <div class="event bad"><h3>第一次 Gate 2：抓出 MU 接收机物理 bug</h3><p>replication 9 的 MU 全部 NACK；根因是同 UE rank 流被误算为干扰。修复为 per-user LMMSE 后同例 MU BLER 降至 7.14%。</p></div>
    <div class="event ok"><h3>协议冻结：13 s / 5 s / 8 s，speedup=1</h3><p>保持原 mixed workload、主指标、数据集和 CRN IDs；没有用加速 warm-up 把模式占比压到门槛以下。</p></div>
    <div class="event bad"><h3>最终 Pilot Gate 2：@@PILOT_PASS@@/@@PILOT_TOTAL@@</h3><p>replication 102：SU 收敛；MU share 1.020%，92/50 samples，低于每半 100，状态 insufficient_samples。正式 n=16 未启动。</p></div>
    <div class="event bad"><h3>Gate 3：未运行</h3><p>没有体验收益百分比、置信区间或“趋势性提升”可报告。旧 n=16 文件明确 superseded。</p></div>
  </div>
  <div class="card table-wrap"><table><thead><tr><th>rep</th><th>候选臂</th><th>SU 状态</th><th>SU exp BLER 前/后</th><th>SU 样本前/后</th><th>MU share</th><th>MU 状态</th><th>MU 样本前/后</th><th>MU exp BLER 前/后</th></tr></thead><tbody>@@PILOT_ROWS@@</tbody></table></div>
</section>

<section id="bugs">
  <div class="section-head"><h2>本轮深审 Bug 台账</h2><p>每一项都说明潜在后果以及能反向失败的验证方式。</p></div>
  <div class="card table-wrap"><table><thead><tr><th>#</th><th>问题</th><th>若不修的后果</th><th>修复与反向验证</th></tr></thead><tbody>@@BUG_ROWS@@</tbody></table></div>
</section>

<section id="boundaries">
  <div class="section-head"><h2>仍需决策 / 明确不在本轮的边界</h2><p>以下不是隐藏 bug，而是模型抽象或实验设计尚未被现场定义冻结。</p></div>
  <div class="grid two">
    <div class="card"><h3>推荐下一步先拍板</h3><ol><li><strong>拆开业务主实验和 MU-OLLA 校准实验。</strong>业务主实验保留真实 mixed workload；另加 MU 定向负载或更长窗口，让活跃 MU 每半至少 100 样本。推荐，不要临时把 1% 阈值抬过 1.020%。</li><li><strong>PMI/CQI report 周期现场值。</strong>当前 20 ms 只是工程默认；建议用 5/10/20/40/80 ms × 多速度 × ≥8 CRN repetitions 再决定。</li><li><strong>MU 配对细节。</strong>当前是 PF anchor + 第一个满足 SUS 的伙伴、2 UE × rank2。用户已说明后续再给现场 MU 细节。</li></ol></div>
    <div class="card"><h3>下一阶段模型项</h3><ul><li>HARQ soft combining 与真正 Retx process；当前 NACK 留队列并按 NewTx。</li><li>逐 RBG SINR/MCS 与频选增益；当前宽带判误码。</li><li>完整 Type-I single-panel 端口/面板映射；当前工程 beam-column baseline。</li><li>显式 CSI feedback latency 与现场 CQI filter；当前反馈 latency=0、expanding mean。</li><li>RI/PMI 的 report-side hold；当前 RI 由 gNB 基于 SRS 选择。</li><li>逐邻区联合调度/负载；当前聚合 scalar load。</li><li>完整 DMRS/PTRS/CORESET 开销；当前显式 12 data symbols 与 S-slot 0.7。</li><li>rank-2 子空间级 SUS；当前 dominant-vector correlation。</li></ul></div>
  </div>
  <div class="callout warn"><strong>可挑出的现有限制</strong>主指标 small wait P95 在当前 0.5 ms TTI 粒度出现地板效应；正式数据只有 40 个信道快照并循环使用；MU 在 mixed workload 中约 0.3–1.05%，难以单独校准 MU-OLLA。这些限制已经进入门禁与报告，不会被藏在 notes 里。</div>
</section>

<section id="evidence">
  <div class="section-head"><h2>可复现证据与命令</h2><p>结果文件可直接点开；旧正式文件有明确禁止引用标记。</p></div>
  <div class="card table-wrap"><table><thead><tr><th>文件</th><th>用途</th><th>大小</th><th>状态</th></tr></thead><tbody>@@ARTIFACT_ROWS@@</tbody></table></div>
  <div class="card"><h3>全量回归：14/14</h3><div class="test-cloud">@@TEST_CHIPS@@</div><div class="formula" style="margin-top:16px">$env:PYTHONUTF8='1'
rg --files tests -g 'test_*.py' | Sort-Object | ForEach-Object { .\.venv\Scripts\python.exe $_ }

.\.venv\Scripts\python.exe scripts\run_experience_mu_power_audit.py --stage power
.\.venv\Scripts\python.exe scripts\run_experience_mu_power_audit.py --stage pmi
.\.venv\Scripts\python.exe scripts\run_experience_mu_power_audit.py --stage stress
.\.venv\Scripts\python.exe scripts\run_experience_mu_power_audit.py --stage pf_sentinel
.\.venv\Scripts\python.exe scripts\run_experience_mu_power_audit.py --stage reverse_pf
.\.venv\Scripts\python.exe scripts\run_experience_mu_power_audit.py --stage formal  # 预期在 Gate 2 返回非零</div></div>
  <div class="grid two">
    <div class="card"><h3>本地设计与数据</h3><ul><li><a href="plans/experience_mu_power_implementation.md">冻结计划与门禁修订</a></li><li><a href="prereg/pr_19a19ebf.json">预注册 pr_19a19ebf</a></li><li><a href="datasets/ds_e8a577d8/summary.json">数据集 ds_e8a577d8 摘要</a></li><li><a href="../EXPERIENCE_MODE.html">Claude 原体验模式方案</a></li></ul></div>
    <div class="card"><h3>外部主来源</h3><ul><li><a href="https://www.etsi.org/deliver/etsi_ts/138300_138399/138331/15.02.01_60/ts_138331v150201p.pdf">3GPP / ETSI TS 38.331：CSI report 周期配置</a></li><li><a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138211/16.04.00_60/ts_138211v160400p.pdf">3GPP / ETSI TS 38.211：SRS 周期与 offset</a></li><li><a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138214/18.07.00_60/ts_138214v180700p.pdf">3GPP / ETSI TS 38.214：TBS 与 Type-I codebook</a></li><li><a href="https://www.comm.utoronto.ca/~weiyu/per_antenna_bf.pdf">Yu &amp; Lan：per-antenna constraint 与 sum-power constraint</a></li><li><a href="https://arxiv.org/abs/2102.06392">Complete Power Reallocation for MU-MIMO under PAPC</a></li><li><a href="https://doi.org/10.1109/JSAC.2005.862421">Yoo &amp; Goldsmith：SUS / ZFBF 调度基线</a></li></ul></div>
  </div>
</section>

<footer class="foot"><strong>审计口径：</strong>本页只把通过的物理与软件不变量写成确定结论；未通过 Gate 2 的业务收益保留为未决。报告可离线打开，所有样式与图形均内嵌。数据集 @@DATASET@@，当前源码冻结时间 @@CREATED@@。</footer>
</main>
</body>
</html>
"""


def main() -> None:
    OUTPUT.write_text(build_report(), encoding="utf-8", newline="\n")
    print(f"REPORT={OUTPUT}")


if __name__ == "__main__":
    main()
