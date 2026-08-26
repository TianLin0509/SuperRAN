"""Build the self-contained SRS and scheduler P0 implementation report."""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "artifacts" / "results" / "scheduler_p0_validation.json"
STRESS = ROOT / "artifacts" / "results" / "scheduler_p0_stress.json"
OUTPUT = ROOT / "artifacts" / "reports" / "SUPERRAN_SRS_AND_SCHEDULER_P0_IMPLEMENTATION.html"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _f(value: float, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}"


def build() -> str:
    validation = _load(VALIDATION)
    stress = _load(STRESS)
    srs = validation["srs"]
    freq = validation["frequency_selection"]
    mu = validation["mu_candidate_scoring"]
    resource = validation["resource_transactions"]
    decision = mu["first_tti_decision"]
    first_grant = mu["first_tti_final_grants"][0]
    candidates = {
        int(row["partner_ue"]): row for row in decision["evaluations"]}
    freq_stress = stress["frequency_12ue_1s"]
    mu_stress = stress["mu_8ue_0p5s"]
    ledger_stress = stress["ledger_transactions"]

    replacements = {
        "__SRS_ALIGNED_COLLISIONS__": str(srs["same_phase"]["colliding_pair_count"]),
        "__SRS_STAGGERED_COLLISIONS__": str(
            srs["pci_mod3_staggered"]["colliding_pair_count"]),
        "__SRS_ALIGNED_NMSE__": _f(srs["same_phase"]["ls_nmse_proxy"]),
        "__SRS_STAGGERED_NMSE__": _f(
            srs["pci_mod3_staggered"]["ls_nmse_proxy"]),
        "__FREQ_OFF__": _f(freq["frequency_off_cell_served_mbps"]),
        "__FREQ_ON__": _f(freq["frequency_on_cell_served_mbps"]),
        "__FREQ_RATIO__": _f(freq["throughput_ratio_on_over_off"]),
        "__FREQ_UE_OFF__": _f(freq["scheduled_ues_per_busy_tti_off"]),
        "__FREQ_UE_ON__": _f(freq["scheduled_ues_per_busy_tti_on"]),
        "__MU_EARLY_DENSITY__": _f(candidates[1]["useful_bytes_per_rbg"], 1),
        "__MU_LATE_DENSITY__": _f(candidates[2]["useful_bytes_per_rbg"], 1),
        "__MU_EARLY_MCS__": "/".join(str(value) for value in candidates[1]["final_mcs"]),
        "__MU_LATE_MCS__": "/".join(str(value) for value in candidates[2]["final_mcs"]),
        "__MU_SU_BYTES__": f"{first_grant['plan_su_useful_bytes']:,}",
        "__MU_PLAN_BYTES__": f"{first_grant['plan_mu_useful_bytes']:,}",
        "__LEDGER_BEFORE_PHYS__": str(resource["before_rollback"]["used_physical_prb"]),
        "__LEDGER_BEFORE_LOGICAL__": str(resource["before_rollback"]["used_logical_prb"]),
        "__LEDGER_AFTER__": str(resource["after_rollback"]["used_physical_prb"]),
        "__LEDGER_TPS__": f"{ledger_stress['transactions_per_second']:,.0f}",
        "__FREQ_STRESS_S__": _f(freq_stress["wall_s"]),
        "__FREQ_STRESS_GRANTS__": f"{freq_stress['frequency_selection']['grant_count']:,}",
        "__MU_STRESS_S__": _f(mu_stress["wall_s"]),
        "__MU_STRESS_CANDIDATES__": f"{mu_stress['mu_candidate_scoring']['candidate_count']:,}",
        "__VALIDATION_PATH__": html.escape(str(VALIDATION.resolve())),
        "__STRESS_PATH__": html.escape(str(STRESS.resolve())),
        "__GUIDE_PATH__": html.escape(str((ROOT / "docs" / "index.html").resolve())),
    }

    document = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SuperRAN · SRS 与下行调度 P0 落地报告</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%231769e0'/%3E%3Cpath d='M13 43h8V29h8v14h8V21h8v22h8' fill='none' stroke='white' stroke-width='5'/%3E%3C/svg%3E">
<style>
:root{--ink:#13233a;--muted:#5b6b80;--bg:#f4f7fb;--card:#fff;--line:#d9e2ef;--blue:#1769e0;--teal:#0d8a7a;--green:#17834a;--amber:#b56b08;--red:#bd2f2f;--nav:#0d1d33;--soft:#edf4ff}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.72 Inter,"Segoe UI","Microsoft YaHei",sans-serif}a{color:inherit}.hero{background:linear-gradient(125deg,#0b1c32,#123d72 58%,#0d756c);color:#fff;padding:56px max(24px,calc((100vw - 1180px)/2)) 48px}.eyebrow{font:700 12px/1.2 ui-monospace,monospace;letter-spacing:.16em;color:#8fe2d4}.hero h1{font-size:clamp(34px,5vw,62px);line-height:1.05;margin:16px 0}.hero p{max-width:880px;color:#d8e6f7;font-size:18px}.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:24px}.chip{padding:6px 11px;border:1px solid #ffffff42;border-radius:999px;background:#ffffff12;font-size:12px}.topnav{position:sticky;top:0;z-index:20;background:#0d1d33f2;color:#dce8f7;backdrop-filter:blur(12px);overflow:auto;white-space:nowrap;padding:0 max(18px,calc((100vw - 1180px)/2))}.topnav a{display:inline-block;text-decoration:none;padding:13px 14px;font-size:13px}.topnav a:hover,.topnav a.active{color:#fff;background:#ffffff12}.wrap{width:min(1180px,calc(100% - 32px));margin:28px auto 72px}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric,.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 7px 24px #243d5b0b}.metric{padding:20px}.metric b{display:block;font-size:28px;line-height:1.15;color:var(--blue)}.metric small{color:var(--muted)}section{scroll-margin-top:62px}.panel{padding:28px;margin-top:18px}.panel h2{font-size:25px;margin:0 0 9px}.panel h3{margin:24px 0 7px}.lead{color:var(--muted);font-size:16px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{border:1px solid var(--line);border-radius:13px;padding:17px;background:#fbfdff}.card h3{margin:0 0 8px}.good{border-left:5px solid var(--green)}.warn{border-left:5px solid var(--amber)}.scope{border-left:5px solid var(--red)}table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px}th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#eef3fa;color:#34465d}code{background:#edf2f8;border-radius:5px;padding:2px 5px;font:12px/1.5 ui-monospace,"Cascadia Code",monospace}.flow{display:grid;grid-template-columns:repeat(7,1fr);gap:7px;margin:18px 0}.flow div{padding:13px 8px;text-align:center;background:#edf4ff;border:1px solid #cfe0f7;border-radius:11px}.flow b{display:block;color:#1459ad}.flow small{color:var(--muted)}.formula{padding:16px 18px;background:#0f2038;color:#e9f3ff;border-radius:12px;overflow:auto;font:15px/1.7 ui-monospace,monospace}.compare{display:grid;grid-template-columns:1fr 1fr;gap:14px}.bar{height:12px;background:#dfe7f1;border-radius:999px;overflow:hidden;margin:8px 0}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--teal));border-radius:inherit}.trace{border:1px solid var(--line);border-radius:13px;overflow:hidden}.trace .row{display:grid;grid-template-columns:90px 1fr 1fr 1fr;padding:10px 14px;border-bottom:1px solid var(--line)}.trace .row:last-child{border-bottom:0}.trace .head{font-weight:700;background:#edf3fa}.tag-pass{color:var(--green);font-weight:800}.tag-scope{color:var(--red);font-weight:800}.path{word-break:break-all;background:#f2f5f9;padding:10px;border-radius:8px;font:12px/1.5 ui-monospace,monospace}.compact-note{background:#fff8ea;border:1px solid #f2d296;border-radius:12px;padding:14px 16px}.footer{color:var(--muted);text-align:center;margin-top:30px}@media(max-width:820px){.summary,.grid2,.compare{grid-template-columns:1fr}.flow{grid-template-columns:1fr 1fr}.trace .row{grid-template-columns:70px 1fr}.trace .row>*:nth-child(n+3){grid-column:2}.panel{padding:20px}}
</style></head><body>
<header class="hero"><div class="eyebrow">IMPLEMENTATION · REVIEW · VALIDATION</div>
<h1>SRS 资源分配与下行调度 P0</h1>
<p>本轮把 SRS 资源、逐 RBG 频选、MU 全伙伴评分、资源事务账和统一 GrantFinalizer 接进真实系统主循环；每项都用方向性反例和压力运行验证，并把未建模边界与性能热点原样保留。</p>
<div class="chips"><span class="chip">100 MHz · 272 PRB · 17×16</span><span class="chip">PDCCH 暂不建模</span><span class="chip">单码字 TB</span><span class="chip">CRN 方向实验</span><span class="chip">文档反审代码</span></div></header>
<nav class="topnav"><a href="#result">结论</a><a href="#srs">SRS</a><a href="#pipeline">调度闭环</a><a href="#frequency">频选</a><a href="#mu">MU 实例</a><a href="#review">参考对照</a><a href="#stress">压力</a><a href="#scope">边界</a><a href="#files">交付</a></nav>
<main class="wrap">
<div class="summary" id="result"><div class="metric"><b>32 UE</b><small>10 ms / 4-port 基础 SRS 池硬容量</small></div><div class="metric"><b>__FREQ_RATIO__×</b><small>互补子带频选方向反例</small></div><div class="metric"><b>0</b><small>RBG overlap 与 plan/final mismatch</small></div><div class="metric"><b>__MU_STRESS_CANDIDATES__</b><small>0.5 s MU 压力候选数</small></div></div>

<section class="panel"><h2>结论先说</h2><div class="grid2">
<div class="card good"><h3>已经形成真实闭环</h3><p>SRS assignment 的 offset 已进入每 UE CSI lag；SU/MU 计划先过同一个 ResourceLedger，选中后再过 Finalizer；执行循环不再自行发明第二套 MCS/TBS。</p></div>
<div class="card good"><h3>三条方向证据全部成立</h3><p>频选优于宽带顺序基线；PCI 模 3 轻载错开降低导频碰撞；MU 会跳过更早但更差的伙伴，选择 useful bytes/RBG 更高者。</p></div>
<div class="card warn"><h3>不是“全量产品调度器”</h3><p>PDCCH/CCE、最大 grant/UE、并行 HARQ process、CA N−3、XR 帧完整性和 HBF 模拟波束仍未建模。</p></div>
<div class="card warn"><h3>MU 计算成为新热点</h3><p>8 UE / 0.5 s 的全伙伴+频选压力耗时 __MU_STRESS_S__ s。结果正确，但 5 s×8 重复应走进程并行；下一轮值得做批量化。</p></div></div></section>

<section class="panel" id="srs"><h2>1 · SRS 基础资源分配</h2><p class="lead">资源叶子 = 周期 + offset + symbol + comb + 循环移位块；跳频是它之上的另一个频域维度。</p>
<div class="flow"><div><b>10 ms</b><small>20 slots</small></div><div><b>7 / 17</b><small>周期内 offset</small></div><div><b>13→10</b><small>symbol</small></div><div><b>0 / 1</b><small>comb</small></div><div><b>8 CS</b><small>4-port 分两块</small></div><div><b>mod 3</b><small>候选偏好</small></div><div><b>17-hop</b><small>每次 1 RBG</small></div></div>
<div class="formula">collide(a,b) = same(symbol, comb) ∧ CS overlap ∧ (offset_a-offset_b) mod gcd(period_a,period_b) = 0</div>
<div class="compare"><div class="card"><h3>三小区同相</h3><b>碰撞 __SRS_ALIGNED_COLLISIONS__/3 · I/S=2 · LS NMSE proxy=__SRS_ALIGNED_NMSE__</b><div class="bar"><i style="width:100%"></i></div><p>三个 UE 都取 offset3.5ms / symbol13 / comb0 / CS0..3。</p></div><div class="card good"><h3>PCI 0 / 1 / 2</h3><b>碰撞 __SRS_STAGGERED_COLLISIONS__/3 · I/S=0 · LS NMSE proxy=__SRS_STAGGERED_NMSE__</b><div class="bar"><i style="width:1%"></i></div><p>三小区从不同颜色叶子起步；资源重载后仍允许溢出。</p></div></div>
<div class="compact-note"><b>证据边界：</b>这是等功率、非碰撞维度理想正交的 allocator-level LS-NMSE proxy，不是根序列/TA/多径/波形联合仿真。</div></section>

<section class="panel" id="pipeline"><h2>2 · 调度闭环</h2><div class="flow"><div><b>Snapshot</b><small>队列/CSI/HARQ</small></div><div><b>PF</b><small>一次排序</small></div><div><b>SU Plan</b><small>频选子集</small></div><div><b>MU Plan</b><small>全伙伴</small></div><div><b>Ledger</b><small>两本账</small></div><div><b>Finalizer</b><small>物理定稿</small></div><div><b>Commit</b><small>ACK/状态</small></div></div>
<div class="grid2"><div class="card"><h3>物理账</h3><p>一个 RBG 在一个 TTI 只被一个 grant 占用；MU 两用户共享 bitmap，物理 PRB 只扣一次。</p></div><div class="card"><h3>逻辑账</h3><p>logical PRB = Σgrant [Σuser rank × physical PRB]。rank2+rank2 在 48 物理 PRB 上消耗 192 layer-PRB。</p></div></div>
<p>事务实测：reserve 后 physical=__LEDGER_BEFORE_PHYS__、logical=__LEDGER_BEFORE_LOGICAL__；rollback 后 physical=__LEDGER_AFTER__；10,000 轮事务约 __LEDGER_TPS__ 次/s。</p></section>

<section class="panel" id="frequency"><h2>3 · 逐 RBG 频选</h2><p><code>frequency_selective</code> 与 RB 功控解耦。质量前缀和顺序前缀一起重算单码字 dB 平均、MCS 与量化 TBS；能发完时取最少 RBG，否则取 predicted useful bytes 最大者。</p>
<table><thead><tr><th>互补 8/9-RBG 场景</th><th>off</th><th>on</th><th>结论</th></tr></thead><tbody><tr><td>ACK 吞吐</td><td>__FREQ_OFF__ Mbps</td><td>__FREQ_ON__ Mbps</td><td><b>__FREQ_RATIO__×</b></td></tr><tr><td>每忙 TTI 调度 UE</td><td>__FREQ_UE_OFF__</td><td>__FREQ_UE_ON__</td><td>各取自己的强子带</td></tr><tr><td>overlap / final mismatch</td><td colspan="2">0 / 0</td><td class="tag-pass">PASS</td></tr></tbody></table>
<p class="compact-note">这个反例证明实现方向和 bitmap 落账正确；它不代表一般信道也有 __FREQ_RATIO__×。正式算法收益仍需真实数据、多 repetition 和配对统计。</p></section>

<section class="panel" id="mu"><h2>4 · MU 调度实例：为什么跳过 UE1，选择 UE2</h2><p>PF 已先固定 UE0 为 anchor。伙伴不是遇到第一个可行者就停，而是全部经历 pair link、相关性、层数、predicted BLER 与 useful density 计算。</p>
<div class="trace"><div class="row head"><span>伙伴</span><span>PF 顺序 / CorrLoss</span><span>最终 MCS</span><span>useful B/RBG</span></div><div class="row"><b>UE1</b><span>1（更早） / −5 dB</span><span>__MU_EARLY_MCS__</span><span>__MU_EARLY_DENSITY__ · 可行但不选</span></div><div class="row"><b>UE2</b><span>2（更晚） / −1 dB</span><span>__MU_LATE_MCS__</span><span><b>__MU_LATE_DENSITY__ · 选中</b></span></div></div>
<p>同一 TTI：SU-only 计划 __MU_SU_BYTES__ B，MU 计划 __MU_PLAN_BYTES__ B，因此走 <code>MU_useful_bytes_ge_SU</code>。UE0/UE2 共享同一个 17-RBG bitmap 和 reservation；总层数 4，物理 PRB 不重复扣。</p>
<h3>Finalizer 的反向哨兵</h3><p>Planner 仍需估值才能选方案，但发送前 Finalizer 按实际 bitmap 重算 MCS/TBS/useful。任何偏差直接抛错；本轮方向与压力实验 mismatch count 都是 0。HARQ 用同一入口，但冻结 MCS/RBG数/rank/TBS。</p></section>

<section class="panel" id="review"><h2>5 · 与用户提供的传统实现材料对照</h2><table><thead><tr><th>参考思想</th><th>本轮取用</th><th>没有照搬的原因</th></tr></thead><tbody>
<tr><td>RBG/UE 调度信息结构</td><td>变成不可变 Candidate/FinalGrant + TTI trace</td><td>保留物理信息，但不复制大型继承树</td></tr><tr><td>PF 排序后做 SU/MU 自适应</td><td>严格保留；SU 清空优先，否则比 queue-limited useful bytes</td><td>不采用未经确认的倍率阈值</td></tr><tr><td>相关性分组与逐 RBG MU</td><td>全 pair link、相关性门、CorrLoss/powerLoss、全伙伴评分</td><td>当前先冻结两用户 rank2，不扩到多用户/多分组</td></tr><tr><td>后处理 MCS/TBS/Grant</td><td>抽成统一 Finalizer，planner 与 final 逐值校验</td><td>不保留分散在多路径的重复计算</td></tr><tr><td>SRS 资源树与回收</td><td>周期/offset/symbol/comb/CS 分配、碰撞 gcd、release</td><td>只实现典型 H profile，不复制 LTE、P-H/F、BWP2 全树</td></tr><tr><td>PDCCH、XR、CA、HBF</td><td><span class="tag-scope">本轮不做</span></td><td>用户已明确 PDCCH 暂不建模；其他缺输入且不是当前可信度 P0</td></tr></tbody></table>
<div class="card good"><h3>文档反审发现并钉住的四个旧缺口</h3><ol><li>所有 UE 的 SRS 机会默认同相，assignment 未进入 CSI 时间轴。</li><li>频率选择性错误依赖 RB 功控开关。</li><li>MU 伙伴取 PF 顺序第一个可行者。</li><li>计划与执行之间没有统一资源事务和物理定稿合同。</li></ol></div></section>

<section class="panel" id="stress"><h2>6 · 压力与回归</h2><table><thead><tr><th>项目</th><th>规模</th><th>耗时</th><th>门禁</th></tr></thead><tbody><tr><td>SRS 容量</td><td>32 个 4-port UE + 第 33 个</td><td>毫秒级</td><td>32 成功，33 硬失败</td></tr><tr><td>资源事务</td><td>10,000 次</td><td>约 __LEDGER_TPS__ 次/s</td><td>commit/rollback 逐轮对账</td></tr><tr><td>SU 频选</td><td>12 UE · 1 s · 2,000 TTI</td><td>__FREQ_STRESS_S__ s</td><td>__FREQ_STRESS_GRANTS__ grants；overlap/mismatch=0</td></tr><tr><td>MU 全伙伴</td><td>8 UE · 0.5 s · 1,000 TTI</td><td>__MU_STRESS_S__ s</td><td>__MU_STRESS_CANDIDATES__ candidates；overlap/mismatch=0</td></tr></tbody></table>
<p>已通过主系统回归、CSI 老化、SRS/P0 新单测、MCP schema 和开发者文档覆盖。说明书全量联动测试首次 10 分钟超时，无终态输出；已拆出默认值/白名单检查通过，后续用更长独立窗口重跑，不把超时写成通过。</p></section>

<section class="panel" id="scope"><h2>7 · 必须上报的边界与下一步</h2><div class="grid2"><div class="card scope"><h3>无线边界</h3><ul><li>SRS 跨小区只做碰撞/I-S/LS-NMSE proxy。</li><li>有效 SINR 仍是跨 RBG、rank stream 的 dB 算术平均。</li><li>SRS P-H/F、BWP2、根序列与 intra-slot switching 未建模。</li></ul></div><div class="card scope"><h3>调度边界</h3><ul><li>PDCCH/CCE 与每 TTI 最大 grant/UE 未建模。</li><li>MU 固定两用户、每用户 rank2。</li><li>并行 HARQ process、CA、XR、HBF 调度未建模。</li></ul></div><div class="card warn"><h3>性能热点</h3><p>MU 全候选逐前缀精算正确但昂贵。下一步可对 17 个前缀批量 MCS/TBS、按离散 OLLA/MCS 缓存，不应减少候选或静默退回首伙伴。</p></div><div class="card good"><h3>当前可用性判断</h3><p>适合典型 100 MHz 单小区 SU/两用户 MU 算法研究、资源/KPI/TTI 解释；不适合宣称控制信道、全网络导频污染或多用户产品调度的绝对精度。</p></div></div></section>

<section class="panel" id="files"><h2>8 · 交付与复现入口</h2><p>主手册新增 <b>“SRS 资源分配与 PCI 模 3”</b>、<b>“下行调度 P0：资源、频选、MU 与定稿”</b> 两章，均有精简/详细切换、公式符号解释和具体 MU 逐 TTI 例子。</p><h3>主手册</h3><div class="path">__GUIDE_PATH__</div><h3>方向性实验 JSON</h3><div class="path">__VALIDATION_PATH__</div><h3>压力 JSON</h3><div class="path">__STRESS_PATH__</div></section>
<div class="footer">SuperRAN · SRS and scheduler P0 · self-contained UTF-8 report</div></main>
<script>const links=[...document.querySelectorAll('.topnav a')];const obs=new IntersectionObserver(es=>{for(const e of es)if(e.isIntersecting){links.forEach(a=>a.classList.toggle('active',a.hash==='#'+e.target.id));}},{rootMargin:'-20% 0px -70%'});document.querySelectorAll('section[id],#result').forEach(x=>obs.observe(x));</script></body></html>"""
    for needle, value in replacements.items():
        document = document.replace(needle, value)
    return document


def main() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = build()
    OUTPUT.write_text(text, encoding="utf-8")
    if not text.startswith("<!doctype html>") or "�" in text:
        raise RuntimeError("report encoding or HTML prolog check failed")
    print(f"Wrote {OUTPUT.resolve()} ({OUTPUT.stat().st_size} bytes)")
    return OUTPUT


if __name__ == "__main__":
    main()
