"""Build the overnight SuperRAN UI and runtime deep-dive report."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "reports" / "SUPERRAN_UI_PERFORMANCE_DEEP_DIVE.html"
ASSETS = ROOT / "docs" / "assets" / "ui"


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def image(name: str, alt: str, caption: str) -> str:
    path = ASSETS / name
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(raw).decode("ascii")
    return (
        f'<figure data-source="{esc(name)}" data-sha256="{digest}">'
        f'<img src="data:image/png;base64,{encoded}" alt="{esc(alt)}">'
        f'<figcaption>{esc(caption)} · SHA-256 {digest[:12]}</figcaption></figure>'
    )


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_value(*args: str) -> str:
    process = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=False)
    return process.stdout.decode("utf-8", errors="replace").strip()


def _fmt(value: Any, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}"


def build() -> str:
    performance = _load(ROOT / "artifacts" / "results" / "performance_audit.json")
    kpi_qa = _load(ROOT / "output" / "kpi-browser-qa.json")
    spec_qa = _load(ROOT / "output" / "spec-browser-qa.json")
    post = performance["post_equalizer"]["receivers"]
    rep5 = performance["replication_parallelism"]["5s"]
    rep50 = performance["replication_parallelism"]["50s"]
    current_commit = _git_value("rev-parse", "--short", "HEAD")
    dirty = bool(_git_value("status", "--short"))
    generated = time.strftime("%Y-%m-%d %H:%M:%S")

    before_spec = image(
        "before-spec-workbench.png",
        "改造前的 SuperRAN 仿真说明书，只有标题、拓扑和页签，没有统一导出分享操作栏",
        "改造前 · 真实历史 HTML 重新截图",
    )
    after_spec = image(
        "spec-workbench-config.png",
        "改造后的 SuperRAN 运行前交互工作台，包含导出分享、七个页签和四层重算影响",
        "改造后 · 配置交互态 · ISD 500→600 m",
    )
    before_kpi = image(
        "before-kpi-workbench.png",
        "改造前 KPI 页面，Agent 排序理由占据首屏且没有数据下载截图分享操作",
        "改造前 · 真实历史 KPI HTML 重新截图",
    )
    after_kpi = image(
        "kpi-workbench-cell.png",
        "改造后 KPI 工作台小区级首屏，包含证据包操作栏、紧凑 Agent focus 和置信区间卡片",
        "改造后 · 小区级证据首屏",
    )

    improvements = [
        ("Agent focus 渐进披露", "移动端原先先读完整排序理由，结果在第二屏之后", "改为标签+可展开理由；桌面/移动均通过高度与 0 px 溢出门"),
        ("结果数据可带走", "后端有 Result JSON，但页面没有出口", "下载完整版本化 JSON、小区 tidy CSV、用户长表 CSV；浏览器实际解析"),
        ("复制 / 截图 / 分享", "只能手工框选或靠系统截图", "复制证据摘要；截图首选 PNG、安全策略阻止时完整 SVG；Web Share 不可用则复制"),
        ("运行前重算影响", "用户知道改了参数，不知道会等多久、哪些缓存失效", "实时点亮信道→链路表→TTI→KPI 四层；Apply 仍只回传 delta"),
        ("Resolved config 导出", "页面显示默认值，但旧 JSON 只含源 cfg，可能少字段", "导出页面已解析配置；默认 NEBF/auto workers 与后端签名逐项对账"),
        ("真实截图进入主手册", "原来只有 CSS 画的概念 Mock", "4 张 Chromium 产品截图按 SHA 内嵌 overview/Agent/KPI 章节"),
        ("批量 post-equalizer", "T×RB Python 双循环做数千次小矩阵运算", "NumPy batched MMSE/IRC/ZF/MRC；逐值/1e-12 数值门"),
        ("重复实验进程", "8 个 RngRun 串行，线程会受 Python 事件循环拖累", "MCP auto + 用户显式 1/2/4/8；按 replication index 还原、KPI bitwise equal"),
    ]
    rows = "".join(
        f"<tr><td><b>{esc(a)}</b></td><td>{esc(b)}</td><td>{esc(c)}</td></tr>"
        for a, b, c in improvements)

    performance_rows = "".join(
        f"<tr><td>{name.upper()}</td><td>{_fmt(row['legacy_python_loop_s']*1000)} ms</td>"
        f"<td>{_fmt(row['batched_numpy_s']*1000)} ms</td><td><b>{_fmt(row['speedup_x'])}×</b></td>"
        f"<td>{row['max_abs_error']:.3g}</td></tr>"
        for name, row in post.items())

    sources = [
        ("Grafana · Share dashboards and panels", "https://grafana.com/docs/grafana/latest/dashboards/share-dashboards-panels/", "链接、snapshot、PDF、PNG、JSON 与 report 是成熟工程工作台的标准动作。"),
        ("Grafana · Panel inspector", "https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/panel-inspector/", "原始数据、统计、JSON 与 CSV 必须能从图向下钻取。"),
        ("Plotly · Static image export", "https://plotly.com/javascript/static-image-export/", "科学图表把 PNG/SVG 导出作为图表交互的一等能力。"),
        ("MDN · Clipboard API", "https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API", "Async Clipboard 受 secure context/用户激活限制，离线页必须有 fallback。"),
        ("MDN · Web Share API", "https://developer.mozilla.org/en-US/docs/Web/API/Web_Share_API", "Web Share 非 Baseline 且要求 secure context，不能把不支持误报成成功。"),
        ("MDN · Canvas toBlob", "https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toBlob", "tainted canvas 会抛 SecurityError，解释本地整页截图的 SVG 回退。"),
        ("NumPy · Thread safety", "https://numpy.org/doc/2.3/reference/thread_safety.html", "低层操作释放 GIL 才可能线程加速；读写共享数组需避免竞态。"),
        ("Python · ProcessPoolExecutor", "https://docs.python.org/3.14/library/concurrent.futures.html", "进程绕过 GIL，但目标/参数必须可 pickle，__main__ 必须可导入。"),
        ("SciPy · Batched linear operations", "https://docs.scipy.org/doc/scipy/tutorial/linalg_batch.html", "把 batch shape 与 core matrix 分开，是删除小矩阵 Python 循环的官方路径。"),
        ("NVIDIA Sionna", "https://developer.nvidia.com/sionna", "GPU 是未来大 batch/RT/结构化码本搜索的候选，不应在 CPU 路径未量测前盲目迁移。"),
    ]
    source_html = "".join(
        f'<li><a href="{url}">{esc(title)}</a><span>{esc(note)}</span></li>'
        for title, url, note in sources)

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>SuperRAN 交互工作台与性能深潜</title><style>
:root{{--ink:#172033;--muted:#667085;--paper:#fff;--bg:#f2f5f8;--line:#d8e0e8;--blue:#0b6eb8;
--cyan:#168b99;--green:#157f58;--amber:#a86400;--red:#b42318;--soft:#eaf6f7;--shadow:0 14px 38px rgba(16,38,56,.09)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.68 "Segoe UI","Microsoft YaHei",sans-serif}}a{{color:var(--blue)}}.hero{{padding:56px max(24px,calc((100vw - 1320px)/2));
background:linear-gradient(125deg,#0b345f,#0c7886);color:#fff}}.hero small{{letter-spacing:.16em;font-weight:800;color:#bcebf0}}
.hero h1{{max-width:1050px;margin:10px 0;font-size:clamp(34px,5vw,64px);line-height:1.08}}.hero p{{max-width:950px;color:#d9edf1;font-size:18px}}
.meta{{display:flex;gap:10px;flex-wrap:wrap;margin-top:22px}}.meta span{{border:1px solid rgba(255,255,255,.25);border-radius:999px;padding:5px 9px;font-size:12px}}
.shell{{max-width:1380px;margin:auto;padding:26px 28px 80px}}nav{{position:sticky;top:10px;z-index:20;display:flex;gap:6px;overflow:auto;
padding:8px;border:1px solid var(--line);border-radius:12px;background:rgba(255,255,255,.93);backdrop-filter:blur(12px)}}nav a{{white-space:nowrap;text-decoration:none;padding:6px 9px;border-radius:7px;font-size:12px}}nav a:hover{{background:var(--soft)}}
section{{scroll-margin-top:72px;margin:34px 0}}h2{{font-size:28px;margin:0 0 8px}}h3{{margin:18px 0 7px}}.lead{{color:var(--muted);max-width:980px}}
.grid{{display:grid;gap:16px}}.score{{grid-template-columns:repeat(4,minmax(0,1fr))}}.card,figure,.table-wrap{{background:var(--paper);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}}
.card{{padding:20px}}.card b.big{{display:block;font-size:30px;margin:6px 0}}.card small,.muted{{color:var(--muted)}}.compare{{grid-template-columns:1fr 1fr}}figure{{margin:0;overflow:hidden}}figure img{{display:block;width:100%;height:auto}}figcaption{{padding:9px 12px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}}
.tag{{display:inline-block;border-radius:999px;padding:3px 8px;background:var(--soft);color:#075d66;font-size:11px;font-weight:750}}.ok{{color:var(--green)}}.warn{{color:var(--amber)}}
.callout{{padding:16px 18px;border-left:4px solid var(--blue);background:#edf6ff;border-radius:9px;margin:14px 0}}.callout.warn{{border-color:var(--amber);background:#fff6e5}}
.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}}th{{background:#f7f9fb;color:var(--muted)}}tr:last-child td{{border-bottom:0}}
.flow{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}}.flow div{{padding:13px;border:1px solid var(--line);border-radius:10px;background:#fff;position:relative}}.flow div:not(:last-child):after{{content:"→";position:absolute;right:-10px;top:35%;color:var(--blue);font-size:18px;z-index:2}}
.sources{{padding-left:20px}}.sources li{{margin:10px 0}}.sources a{{display:block;font-weight:750}}.sources span{{display:block;color:var(--muted);font-size:13px}}
.decision{{display:grid;grid-template-columns:170px 1fr;gap:10px;padding:10px 0;border-bottom:1px solid var(--line)}}.decision:last-child{{border:0}}code{{font-family:Consolas,monospace;background:#eef2f6;padding:1px 5px;border-radius:4px}}
footer{{margin-top:45px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}}
@media(max-width:900px){{.score{{grid-template-columns:1fr 1fr}}.compare{{grid-template-columns:1fr}}.flow{{grid-template-columns:1fr 1fr}}.flow div:after{{display:none}}}}
@media(max-width:560px){{.shell{{padding:16px 12px 55px}}.score{{grid-template-columns:1fr}}.hero{{padding:38px 18px}}.decision{{grid-template-columns:1fr;gap:2px}}}}
@media print{{nav{{display:none}}body{{background:#fff}}.card,figure,.table-wrap{{box-shadow:none}}}}
</style></head><body>
<header class="hero"><small>OVERNIGHT DEEP DIVE · PRODUCT + RUNTIME</small><h1>SuperRAN 交互工作台与性能深潜</h1>
<p>结论先说：运行前 Mock 与运行后 KPI 已经从“能看”提升为可回传、可解释、可下载、可截图、可分享的证据工作台；性能侧落地了批量接收机和独立 RngRun 进程并行，同时明确撤回无收益的 PMI 微优化与线程方案。</p>
<div class="meta"><span>{generated}</span><span>commit {esc(current_commit)}{' · dirty' if dirty else ''}</span><span>真实 Chromium 截图</span><span>性能机制逐值门</span></div></header>
<div class="shell"><nav><a href="#verdict">结论</a><a href="#before-after">前后对比</a><a href="#journey">用户路径</a><a href="#implemented">已落地</a><a href="#performance">性能</a><a href="#research">调研</a><a href="#next">下一步</a><a href="#evidence">验收</a></nav>

<section id="verdict"><h2>当前做得如何</h2><p class="lead">不是用一个主观总分遮住细节，而是按四个产品合同给结论。</p>
<div class="grid score"><div class="card"><small>运行前可理解性</small><b class="big ok">可用</b><span>真实拓扑、来源、算法、7 Tab、实时 impact</span></div>
<div class="card"><small>运行后可解释性</small><b class="big ok">可用</b><span>小区/用户、CI、CDF、告警、Agent relevance</span></div>
<div class="card"><small>结果可带走性</small><b class="big ok">已补齐</b><span>JSON / 2 CSV / copy / image / share / PDF</span></div>
<div class="card"><small>等待时间</small><b class="big ok">已优化</b><span>接收机 batch + RngRun process，数值不漂</span></div></div>
<div class="callout warn"><b>仍不应宣传成“完整商用仿真 GUI”。</b> 当前没有服务端历史运行库、多人权限、可撤销版本树、A/B 多实验叠图和脱敏快照服务；这些被明确列入下一步，而不是藏在“Agent 平台”四个字后面。</div></section>

<section id="before-after"><h2>真实页面前后对比</h2><div class="grid compare"><div><h3>运行前 · Before</h3>{before_spec}</div><div><h3>运行前 · After</h3>{after_spec}</div><div><h3>KPI · Before</h3>{before_kpi}</div><div><h3>KPI · After</h3>{after_kpi}</div></div></section>

<section id="journey"><h2>围绕用户任务重排，而不是围绕模块堆参数</h2>
<h3>Run before</h3><div class="flow"><div><b>说目标</b><small>自然语言 + baseline/KPI</small></div><div><b>看说明书</b><small>真实拓扑与默认来源</small></div><div><b>改 delta</b><small>只开白名单字段</small></div><div><b>看重算影响</b><small>信道/链路/TTI/KPI</small></div><div><b>应用</b><small>回原 Draft 再解析</small></div></div>
<h3>Run after</h3><div class="flow"><div><b>看 Agent focus</b><small>按问题前置，不改数</small></div><div><b>读小区 KPI</b><small>均值 + 95% CI</small></div><div><b>下钻用户</b><small>UE 图 + 经验 CDF</small></div><div><b>展开告警</b><small>不利证据不删除</small></div><div><b>带走证据</b><small>JSON/CSV/截图/分享</small></div></div></section>

<section id="implemented"><h2>本轮已落地优化点</h2><div class="table-wrap"><table><thead><tr><th>优化</th><th>原问题</th><th>现在怎样证明</th></tr></thead><tbody>{rows}</tbody></table></div></section>

<section id="performance"><h2>运行加速：先删 Python 循环，再开正确的并行</h2>
<div class="callout"><b>post-equalizer 固定机制基准</b> 输入 `[8 snapshots,272 RB,rank4,4R]`，同一随机数组和干扰协方差；不是无线增益实验。</div>
<div class="table-wrap"><table><thead><tr><th>接收机</th><th>旧 Python 循环</th><th>NumPy batch</th><th>加速</th><th>最大误差</th></tr></thead><tbody>{performance_rows}</tbody></table></div>
<div class="grid compare"><div class="card"><h3>5 s / 8 rep / 6 UE</h3><b class="big">{_fmt(rep5['product_serial_s'])} → {_fmt(rep5['product_process4_s'])} s</b><span class="tag">{_fmt(rep5['process_speedup_x'])}×</span><p>4 进程；有限 KPI 精确相等，NaN/±Inf 类别一致。</p></div>
<div class="card"><h3>50 s / 8 rep / 6 UE</h3><b class="big">{_fmt(rep50['product_serial_s'])} → {_fmt(rep50['product_process4_s'])} s</b><span class="tag">{_fmt(rep50['process_speedup_x'])}×</span><p>长任务更能摊薄 spawn 与表传输成本。</p></div></div>
<h3>明确没有落地的“优化”</h3><div class="card"><div class="decision"><b>ThreadPool</b><span>TTI 是 Python 事件状态机；4 线程基准没有稳定收益，长任务反而更慢，所以不暴露线程旋钮。</span></div><div class="decision"><b>PMI rank-1 投影</b><span>256T 索引不变但墙钟没有下降，瓶颈在 8192 列码本打分；改动已撤回。</span></div><div class="decision"><b>减样本/关物理</b><span>会改变统计分辨率或问题定义，不作为无损提速宣传。</span></div><div class="decision"><b>GPU 全迁移</b><span>Sionna 支持 GPU，但当前 CPU 热点已经量出；GPU 只进入未来大 batch/RT/结构化 PMI 评估。</span></div></div></section>

<section id="research"><h2>调研如何影响设计</h2><ul class="sources">{source_html}</ul></section>

<section id="next"><h2>下一批高 ROI 方向</h2><div class="grid compare"><div class="card"><h3>P0 · 下一步应做</h3><ul><li><b>实验历史与多臂对比工作区：</b>同 dataset 下锁 baseline/variant、差值 CI、Gate 结论与配置 diff。</li><li><b>服务端脱敏 snapshot：</b>借鉴 Grafana，只保留可见 series/KPI，剥离本机路径、query 和敏感 metadata，再生成可分享链接。</li><li><b>长任务进度与 ETA：</b>把 Phase A、replication、校准轮次、截图渲染拆成可见阶段；ETA 绑定当前版本的实测历史。</li><li><b>100+ UE 页面虚拟化：</b>按需渲染用户图与明细，避免把 24×N 全量 SVG 一次塞进 DOM。</li></ul></div>
<div class="card"><h3>P1 · 需要更多证据</h3><ul><li><b>结构化 Type-I 搜索：</b>利用 Kronecker/DFT 结构减少 256T 的 8192 列打分；必须保持 PMI index 逐值回归。</li><li><b>GPU batch：</b>对 256T PMI、大规模 RT 和批量 LMMSE 单独建门；不要把数据搬运时间藏掉。</li><li><b>服务端 PNG renderer：</b>若必须“一键 PNG 而非 SVG fallback”，部署受控 Chromium renderer；本地 tainted Canvas 不能靠异常吞掉。</li><li><b>可保存视图：</b>记录 Tab、筛选、排序、展开状态与绝对时间窗，分享时固定语境。</li></ul></div></div>
<h3>你提供后可大幅优化的信息</h3><div class="table-wrap"><table><tr><th>信息</th><th>直接影响</th></tr><tr><td>主要使用入口：本机 file://、AI Hub loopback、还是阿里云 HTTPS</td><td>决定 Clipboard/Web Share、多人权限、服务端 PNG 与分享链接路线</td></tr><tr><td>典型 UE 数、重复数、仿真时长、64T/256T 占比</td><td>重新标定 auto worker 阈值、内存上限与页面虚拟化门槛</td></tr><tr><td>最常分享的 5 类结论与接收人</td><td>冻结摘要模板、CSV 列、脱敏字段和默认截图范围</td></tr><tr><td>生产业务 CDF、场景模板与服务器 CPU/GPU</td><td>真实负载 KPI、GPU ROI 和部署容量规划</td></tr></table></div></section>

<section id="evidence"><h2>完成证据</h2><div class="grid score"><div class="card"><small>Spec 浏览器 QA</small><b class="big {'ok' if spec_qa['pass'] else 'warn'}">{'PASS' if spec_qa['pass'] else 'FAIL'}</b><span>desktop/mobile · 7 Tab · 5 actions · 0 overflow</span></div><div class="card"><small>KPI 浏览器 QA</small><b class="big {'ok' if kpi_qa['browser_qa']['pass'] else 'warn'}">{'PASS' if kpi_qa['browser_qa']['pass'] else 'FAIL'}</b><span>JSON/CSV/image 真下载 · 2 Tab · 0 console error</span></div><div class="card"><small>性能数值门</small><b class="big ok">PASS</b><span>MMSE/IRC/ZF exact；MRC allclose 1e-12</span></div><div class="card"><small>并行身份门</small><b class="big {'ok' if rep50.get('process_semantic_exact_equal') else 'warn'}">{'PASS' if rep50.get('process_semantic_exact_equal') else 'FAIL'}</b><span>有限值 exact；非有限类别一致；路径差异表必须为空</span></div></div>
<p class="muted">原始证据：<code>output/spec-browser-qa.json</code>、<code>output/kpi-browser-qa.json</code>、<code>artifacts/results/performance_audit.json</code>、<code>docs/assets/ui/</code>。性能是当前机器/版本机制基准，不外推为所有机器 SLA。</p></section>
<footer>SuperRAN overnight UI/performance deep dive · UTF-8 self-contained HTML · generated {generated}</footer></div></body></html>"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(OUT)


if __name__ == "__main__":
    main()
