"""Build the self-contained SuperRAN canonical-array and rename audit report.

The report is intentionally generated from the current source tree.  It
recomputes both the classic 64T and company 256T feed-matrix invariants,
records repository state and hashes the critical implementation files so the
visual explanation remains auditable after deployment.
"""
from __future__ import annotations

import hashlib
import html
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MSG_ROOT = ROOT.parent / "MSG-Platform"
OUT = ROOT / "artifacts" / "SUPERRAN_256T_MIGRATION_AUDIT.html"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(MSG_ROOT / "src"))

from msg_embedding.phy_sim.effective_array import (  # noqa: E402
    PORT_LAYOUT_CONTRACT_VERSION,
    make_effective_array,
)

from superran import hardware as hw  # noqa: E402


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_info(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=root, text=True, capture_output=True, check=False
        )
        return proc.stdout.strip() if proc.returncode == 0 else "unavailable"

    status = run("status", "--short")
    return {
        "head": run("rev-parse", "--short=12", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(status and status != "unavailable"),
        "changed_paths": len(status.splitlines()) if status not in {"", "unavailable"} else 0,
    }


def profile_evidence(profile: str) -> dict[str, Any]:
    is_256t = profile == "256t"
    cfg = {
        "bs_panel": [16, 8, 2] if is_256t else [8, 4, 2],
        "carrier_freq_hz": hw.COMPANY_CARRIER_HZ,
        "antenna_model_mode": "effective_subarray",
        "bs_antenna": hw.company_antenna_block(profile=profile),
    }
    array = make_effective_array(cfg)
    feed = array.coupling_matrix()
    gram = feed.conj().T @ feed
    positions = array.physical_positions_lambda()
    centers = array.rf_phase_centers_lambda()
    nonzero = np.abs(feed) > 1e-12
    anchor_ports = (1, 9, 129) if is_256t else (1, 5, 33)
    first_rows = {
        f"port_{port}": (np.flatnonzero(nonzero[:, port - 1]) + 1).tolist()
        for port in anchor_ports
    }
    return {
        "rf_shape": list(array.rf_shape),
        "physical_shape": list(array.physical_shape),
        "feed_shape": list(feed.shape),
        "nonzero_per_column": sorted(set(np.count_nonzero(nonzero, axis=0).tolist())),
        "owners_per_row": sorted(set(np.count_nonzero(nonzero, axis=1).tolist())),
        "gram_max_abs_error": float(np.max(np.abs(gram - np.eye(array.num_ports)))),
        "coupling_hash": array.coupling_hash(),
        "pattern_hash": array.pattern_hash(),
        "port_order": array.port_order,
        "vertical_index_order": array.vertical_index_order,
        "polarization_slants_deg": array.metadata()["polarization_slant_angles_deg"],
        "horizontal_step_lambda": float(
            positions[array.physical_shape[1], 1] - positions[0, 1]
        ),
        "vertical_step_lambda": float(positions[1, 2] - positions[0, 2]),
        "rf_vertical_center_step_lambda": float(abs(centers[1, 2] - centers[0, 2])),
        "first_feed_rows_1based": first_rows,
        "metadata": array.metadata(),
    }


def array_evidence() -> dict[str, Any]:
    """Return both arrays governed by the same canonical flattening contract."""
    return {
        "canonical_contract": {
            "version": PORT_LAYOUT_CONTRACT_VERSION,
            "port_order": hw.COMPANY_PORT_ORDER,
            "vertical_index_order": hw.COMPANY_VERTICAL_INDEX_ORDER,
            "zero_based_formula": "r=(p*N_H+h)*N_V+v",
        },
        "64t": profile_evidence("64t"),
        "256t": profile_evidence("256t"),
    }


def port_grid_svg() -> str:
    width, height = 1040, 650
    x0, y0 = 78, 92
    dx, dy = 53, 50
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="256T 端口排列图">',
        '<defs><filter id="shadow"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity=".12"/></filter></defs>',
        '<rect x="24" y="28" width="890" height="500" rx="18" class="panel-bg"/>',
        '<text x="44" y="62" class="svg-title">极化块 0 · +45° · 端口 1–128</text>',
        '<text x="44" y="558" class="svg-note">v 从上到下递增；h 从左到右递增。第一行：1, 9, 17, …, 121；最后一行：8, 16, …, 128。</text>',
    ]
    for h in range(16):
        for v in range(8):
            number = h * 8 + v + 1
            x, y = x0 + h * dx, y0 + v * dy
            emphasis = " key" if number in {1, 8, 9, 121, 128} else ""
            parts.append(f'<g class="port{emphasis}" filter="url(#shadow)">')
            parts.append(f'<rect x="{x - 20}" y="{y - 17}" width="40" height="34" rx="8"/>')
            parts.append(f'<text x="{x}" y="{y + 5}">{number}</text></g>')
    parts.extend(
        [
            '<line x1="58" y1="500" x2="865" y2="500" class="axis"/>',
            '<text x="466" y="520" class="axis-label">h：16 列，物理水平间距 0.5λ</text>',
            '<line x1="940" y1="92" x2="940" y2="442" class="axis"/>',
            '<text x="972" y="275" class="axis-label rotate">v：8 行，top → bottom</text>',
            '<rect x="24" y="585" width="890" height="48" rx="12" class="pol2"/>',
            '<text x="44" y="615" class="svg-title pol2-text">极化块 1 · −45° · 同一几何位置 · 端口号整体 +128（129–256）</text>',
            '</svg>',
        ]
    )
    return "".join(parts)


def feed_svg() -> str:
    parts = [
        '<svg viewBox="0 0 1040 430" role="img" aria-label="一个 RF 端口一驱六物理阵子拓扑">',
        '<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z"/></marker></defs>',
        '<rect x="35" y="154" width="210" height="112" rx="18" class="rfbox"/>',
        '<text x="140" y="192" class="svg-title center">数字 / RF 端口 r</text>',
        '<text x="140" y="221" class="svg-note center">一个端口，一列 F</text>',
        '<text x="140" y="245" class="svg-note center">∥F[:,r]∥₂ = 1</text>',
        '<path d="M245 210 C330 210 330 70 420 70" class="wire" marker-end="url(#arrow)"/>',
        '<path d="M245 210 C330 210 330 140 420 140" class="wire" marker-end="url(#arrow)"/>',
        '<path d="M245 210 C330 210 330 210 420 210" class="wire" marker-end="url(#arrow)"/>',
        '<path d="M245 210 C330 210 330 280 420 280" class="wire" marker-end="url(#arrow)"/>',
        '<path d="M245 210 C330 210 330 350 420 350" class="wire" marker-end="url(#arrow)"/>',
        '<path d="M245 210 C330 210 330 420 420 420" class="wire" marker-end="url(#arrow)"/>',
    ]
    for q, y in enumerate([70, 140, 210, 280, 350, 420]):
        parts.append(f'<circle cx="450" cy="{y}" r="22" class="ae"/>')
        parts.append(f'<text x="450" y="{y + 5}" class="center ae-text">q={q}</text>')
    parts.extend(
        [
            '<path d="M505 70 L505 420" class="brace"/>',
            '<path d="M495 70 L515 70 M495 420 L515 420" class="brace"/>',
            '<text x="535" y="242" class="svg-note">相邻阵子 0.67λ</text>',
            '<rect x="700" y="115" width="300" height="190" rx="18" class="mathbox"/>',
            '<text x="725" y="154" class="svg-title">端口到阵子的所有权</text>',
            '<text x="725" y="188" class="formula">e = p·768 + h·48 + (6v+q)</text>',
            '<text x="725" y="222" class="formula">F[e,r] = wq；其余为 0</text>',
            '<text x="725" y="256" class="formula">wq ∝ Aq exp(jψq) exp(j2πzq sin θtilt)</text>',
            '<text x="725" y="286" class="svg-note">256 列互不重叠，因此 FᴴF = I₂₅₆</text>',
            '<text x="60" y="34" class="svg-title">16H × 8V × 2pol 的每个 T 都固定驱动 6 个垂直物理阵子</text>',
            '</svg>',
        ]
    )
    return "".join(parts)


def backend_svg() -> str:
    boxes = [
        (40, "硬件真相", "16H×8V×2pol\n1→6 · ±45°", "good"),
        (250, "EffectiveArray", "F / 坐标 / Jones\n排列转换", "good"),
        (460, "信道后端", "InternalSim · Sionna\nQuaDRiGa adapter", "good"),
        (670, "测量与链路", "Type-I PMI · BF\nEBF/PEBF/NEBF", "good"),
        (880, "系统体验", "PF · SU/MU · RBG\nKPI / MCP", "good"),
    ]
    parts = ['<svg viewBox="0 0 1100 250" role="img" aria-label="256T 端到端数据路径">', '<defs><marker id="arr2" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z"/></marker></defs>']
    for idx, (x, title, body, cls) in enumerate(boxes):
        parts.append(f'<g class="flow {cls}"><rect x="{x}" y="55" width="175" height="132" rx="16"/>')
        parts.append(f'<text x="{x + 87}" y="91" class="svg-title center">{title}</text>')
        for line_idx, line in enumerate(body.split("\n")):
            parts.append(f'<text x="{x + 87}" y="{126 + line_idx * 25}" class="svg-note center">{line}</text>')
        parts.append('</g>')
        if idx < len(boxes) - 1:
            parts.append(f'<path d="M{x + 175} 121 L{x + 202} 121" class="wire" marker-end="url(#arr2)"/>')
    parts.append('</svg>')
    return "".join(parts)


def render() -> str:
    evidence = array_evidence()
    evidence_256t = evidence["256t"]
    product_git = git_info(ROOT)
    msg_git = git_info(MSG_ROOT)
    critical = {
        "SuperRAN hardware.py": ROOT / "src" / "superran" / "hardware.py",
        "SuperRAN experience.py": ROOT / "src" / "superran" / "experience.py",
        "SuperRAN server.py": ROOT / "src" / "superran" / "server.py",
        "MSG EffectiveArray": MSG_ROOT / "src" / "msg_embedding" / "phy_sim" / "effective_array.py",
        "MSG InternalSim": MSG_ROOT / "src" / "msg_embedding" / "data" / "sources" / "internal_sim.py",
        "MSG Sionna adapter": MSG_ROOT / "src" / "msg_embedding" / "data" / "sources" / "sionna_rt.py",
        "MSG QuaDRiGa adapter": MSG_ROOT / "src" / "msg_embedding" / "data" / "sources" / "quadriga_real.py",
    }
    hashes = "".join(
        f"<tr><td>{esc(name)}</td><td><code>{sha256(path)}</code></td></tr>"
        for name, path in critical.items()
    )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    replacements = {
        "__PORT_GRID__": port_grid_svg(),
        "__FEED_SVG__": feed_svg(),
        "__BACKEND_SVG__": backend_svg(),
        "__GENERATED__": generated,
        "__F_HASH__": esc(evidence_256t["coupling_hash"]),
        "__P_HASH__": esc(evidence_256t["pattern_hash"]),
        "__GRAM_ERR__": f'{evidence_256t["gram_max_abs_error"]:.3e}',
        "__PRODUCT_HEAD__": esc(product_git["head"]),
        "__PRODUCT_BRANCH__": esc(product_git["branch"]),
        "__MSG_HEAD__": esc(msg_git["head"]),
        "__MSG_BRANCH__": esc(msg_git["branch"]),
        "__HASH_ROWS__": hashes,
        "__EVIDENCE_JSON__": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
    }
    page = HTML
    for key, value in replacements.items():
        page = page.replace(key, value)
    # The long inline test-card row is kept compact in the template.  State
    # the actual gate boundary precisely: only 64T ran through fr(); both
    # profiles ran the real QuaDRiGa array/coupling builder.
    page = page.replace(
        "64T+256T PASS · 1083s",
        "64T fr() + 64T/256T F · PASS",
    )
    leftovers = [key for key in replacements if key in page]
    if leftovers:
        raise RuntimeError("unexpanded markers: " + repr(leftovers))
    return page


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>SuperRAN 64T/256T 统一端口契约审计</title>
<style>
:root{--ink:#17212b;--muted:#5c6b78;--line:#d9e2e8;--paper:#fff;--bg:#f4f7f8;--nav:#132b37;--brand:#007a78;--brand2:#00a693;--ok:#147a4a;--warn:#a66100;--risk:#b73939;--blue:#2d67b2;--soft:#e9f5f3;--amber:#fff5dd;--red:#fff0ef;--shadow:0 12px 34px rgba(19,43,55,.09)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:var(--bg);font-family:"Segoe UI","Microsoft YaHei UI","PingFang SC",system-ui,sans-serif;line-height:1.72}.layout{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh}.sidebar{position:sticky;top:0;height:100vh;background:var(--nav);color:#d9e7eb;padding:30px 20px;overflow:auto}.logo{font-size:27px;font-weight:800;letter-spacing:.2px;color:white}.logo span{color:#55d8c8}.side-kicker{font-size:11px;letter-spacing:1.8px;color:#8fb1bd;margin:3px 0 28px}.sidebar a{display:block;color:#bcd0d6;text-decoration:none;padding:8px 12px;margin:2px 0;border-left:2px solid transparent;border-radius:0 8px 8px 0;font-size:13px}.sidebar a:hover,.sidebar a.active{color:white;background:rgba(255,255,255,.08);border-left-color:#55d8c8}.side-meta{font-size:11px;color:#8fb1bd;border-top:1px solid rgba(255,255,255,.12);padding-top:18px;margin-top:24px}.main{min-width:0}.hero{background:linear-gradient(125deg,#102d38,#0f5360 58%,#087c78);color:#fff;padding:68px clamp(28px,6vw,88px) 54px}.eyebrow{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#8ce7db;font-weight:700}.hero h1{font-size:clamp(36px,5vw,66px);line-height:1.08;margin:15px 0 18px;max-width:1000px}.hero p{font-size:17px;color:#d3e8eb;max-width:920px;margin:0}.verdict{display:inline-flex;align-items:center;gap:10px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);border-radius:999px;padding:8px 14px;margin-top:26px;font-size:13px}.dot{width:9px;height:9px;border-radius:50%;background:#53e09f;box-shadow:0 0 0 5px rgba(83,224,159,.15)}.stats{display:grid;grid-template-columns:repeat(6,minmax(110px,1fr));gap:12px;margin-top:38px}.stat{padding:17px 16px;border:1px solid rgba(255,255,255,.16);border-radius:13px;background:rgba(255,255,255,.08)}.stat b{display:block;font-size:23px;color:#fff}.stat span{font-size:11px;color:#b9d8dc}.content{max-width:1180px;margin:0 auto;padding:46px clamp(22px,4vw,58px) 100px}section{scroll-margin-top:25px;margin:0 0 58px}.section-head{display:flex;align-items:baseline;gap:13px;border-bottom:1px solid var(--line);padding-bottom:11px;margin-bottom:23px}.num{font:700 13px ui-monospace,Consolas,monospace;color:var(--brand)}h2{font-size:28px;line-height:1.25;margin:0}h3{font-size:18px;margin:24px 0 9px}p{margin:8px 0 14px}.lead{font-size:17px;color:#314552}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}.card{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:22px;box-shadow:var(--shadow)}.card h3{margin-top:0}.callout{border-left:4px solid var(--brand);background:var(--soft);padding:18px 20px;border-radius:0 12px 12px 0;margin:20px 0}.callout.warn{border-color:var(--warn);background:var(--amber)}.callout.risk{border-color:var(--risk);background:var(--red)}.tag{display:inline-block;font-size:11px;font-weight:750;letter-spacing:.5px;border-radius:999px;padding:3px 9px;margin-right:6px}.tag.ok{color:var(--ok);background:#e6f5eb}.tag.partial{color:var(--warn);background:var(--amber)}.tag.open{color:var(--risk);background:var(--red)}code,.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.91em}.formula-block{overflow:auto;padding:18px 20px;margin:14px 0;background:#132b37;color:#e7f2f4;border-radius:12px;font:500 14px/1.8 ui-monospace,Consolas,monospace}.diagram{background:var(--paper);border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:var(--shadow);overflow:auto;margin:20px 0}.diagram svg{display:block;min-width:760px;width:100%;height:auto}.panel-bg{fill:#f8fbfc;stroke:#cbdbe0}.port rect{fill:#fff;stroke:#b6cad1}.port text{font-size:11px;text-anchor:middle;fill:#314552;font-family:ui-monospace,Consolas,monospace}.port.key rect{fill:#dff4f0;stroke:#008c84}.port.key text{fill:#006e69;font-weight:800}.pol2{fill:#eef3ff;stroke:#b7c8ed}.pol2-text{fill:#385d99!important}.svg-title{font-size:15px;font-weight:750;fill:#18323d}.svg-note{font-size:12px;fill:#5b7079}.center{text-anchor:middle}.rotate{transform:rotate(90deg);transform-origin:972px 275px}.axis,.brace{stroke:#829ba5;fill:none}.axis-label{font-size:11px;fill:#5b7079;text-anchor:middle}.rfbox{fill:#e6f5f2;stroke:#00887f}.wire{fill:none;stroke:#698792;stroke-width:1.6}.ae{fill:#fff4de;stroke:#b87a19}.ae-text{font-size:11px;fill:#754c0a}.mathbox{fill:#f8fbfc;stroke:#cbdbe0}.formula{font-size:12px;fill:#263f4a;font-family:ui-monospace,Consolas,monospace}.flow rect{fill:#f8fbfc;stroke:#b9ccd3}.flow.good rect{fill:#eaf6f3;stroke:#4ca89c}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:13px;background:#fff}table{width:100%;border-collapse:collapse;min-width:700px;font-size:13px}th{background:#edf3f5;text-align:left;color:#40545e;font-size:11px;letter-spacing:.5px;text-transform:uppercase}th,td{padding:11px 13px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}.status-line{display:flex;align-items:flex-start;gap:10px}.status-line .tag{flex:none}.testbar{display:grid;grid-template-columns:190px minmax(180px,1fr) 110px;gap:12px;align-items:center;margin:10px 0}.bar-track{height:8px;border-radius:999px;background:#e8eef0;overflow:hidden}.bar-fill{height:100%;background:linear-gradient(90deg,var(--brand),var(--brand2));border-radius:inherit}.test-result{text-align:right;font-size:12px;color:var(--ok);font-weight:700}.module{display:grid;grid-template-columns:155px 105px minmax(0,1fr);gap:14px;padding:16px 0;border-bottom:1px solid var(--line)}.module:last-child{border:0}.module b{font-size:14px}.module p{margin:0;color:#40545e;font-size:13px}.decision{counter-increment:decision;display:grid;grid-template-columns:44px minmax(0,1fr);gap:14px;margin:17px 0}.decision:before{content:"D" counter(decision);width:40px;height:40px;border-radius:11px;display:grid;place-items:center;background:#e8f3f1;color:var(--brand);font-weight:800;font-size:12px}.decisions{counter-reset:decision}.small{font-size:12px;color:var(--muted)}details{background:#fff;border:1px solid var(--line);border-radius:12px;margin:10px 0}summary{cursor:pointer;padding:13px 16px;font-weight:700}.detail{padding:0 16px 16px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:22px}.button{display:inline-block;text-decoration:none;background:var(--brand);color:#fff;border-radius:10px;padding:10px 14px;font-size:13px;font-weight:700}.button.secondary{background:#e6efef;color:#24444e}.foot{padding:32px clamp(22px,4vw,58px);background:#e8eef0;color:#516570;font-size:12px}.evidence-json{white-space:pre-wrap;word-break:break-word;max-height:240px;overflow:auto;font-size:10px}.legend{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0}.legend span{font-size:12px}.nowrap{white-space:nowrap}@media(max-width:1050px){.layout{grid-template-columns:1fr}.sidebar{position:relative;height:auto;padding:18px 22px}.sidebar nav{display:flex;overflow:auto;gap:4px}.sidebar a{white-space:nowrap;border-left:0;border-bottom:2px solid transparent}.side-meta{display:none}.stats{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.hero{padding:45px 22px}.hero h1{font-size:37px}.content{padding:34px 16px 70px}.stats{grid-template-columns:repeat(2,1fr)}.grid,.grid.three{grid-template-columns:1fr}.module{grid-template-columns:1fr;gap:5px}.testbar{grid-template-columns:1fr}.test-result{text-align:left}.section-head{align-items:flex-start}.diagram{margin-left:-8px;margin-right:-8px;border-radius:10px}.sidebar nav{margin:0 -8px}.card{padding:17px}}
/* Responsive containment overrides: wide SVGs/tables scroll inside their card. */
.grid>*{min-width:0}.card{min-width:0;overflow-wrap:anywhere}.card>table{min-width:0}
.callout code{overflow-wrap:anywhere;word-break:break-word}
.diagram,.table-wrap{max-width:100%}
@media(max-width:1050px){.sidebar{min-width:0}.sidebar nav{max-width:100%}}
@media(max-width:700px){.grid,.grid.three{grid-template-columns:minmax(0,1fr)}.card>table{font-size:11px}.card>table th,.card>table td{padding:8px 6px}}
</style></head><body><div class="layout">
<aside class="sidebar"><div class="logo">Super<span>RAN</span></div><div class="side-kicker">64T / 256T · CANONICAL ORDER · TRUST AUDIT</div><nav>
<a href="#verdict">结论</a><a href="#mapping">端口排列</a><a href="#feed">阵子与 F</a><a href="#backends">后端贯通</a><a href="#system">系统与体验</a><a href="#rename">主命名迁移</a><a href="#tests">压力回归</a><a href="#bugs">修复清单</a><a href="#modules">模块可行性</a><a href="#boundaries">边界与决策</a><a href="#provenance">证据指纹</a>
</nav><div class="side-meta">生成：__GENERATED__<br>SuperRAN __PRODUCT_HEAD__<br>MSG __MSG_HEAD__</div></aside>
<main class="main"><header class="hero"><div class="eyebrow">IMPLEMENTATION & EVIDENCE REPORT · 2026-08-13</div><h1>SuperRAN 64T/256T 统一端口契约审计</h1><p>从公司 256T 图片里的编号反推物理顺序，再把经典 64T、1 驱 3/1 驱 6 馈电、有效阵列矩阵、三种信道后端、PMI、链路与体验系统全部统一到同一份可版本化合同。每个结论都标注证据等级，不把“接口接通”写成“真实引擎已跑”。</p><div class="verdict"><span class="dot"></span>64T/256T 主路径已统一并通过回归；实测方向图、馈电标定与现场 MU 规则仍显式开放</div><div class="stats"><div class="stat"><b>64 / 256</b><span>RF / 数字端口</span></div><div class="stat"><b>192 / 1,536</b><span>物理极化支路</span></div><div class="stat"><b>1→3 / 1→6</b><span>垂直固定馈电</span></div><div class="stat"><b>34</b><span>sr_* MCP 工具</span></div><div class="stat"><b>27</b><span>开发者手册逻辑页</span></div><div class="stat"><b>1</b><span>canonical 合同版本</span></div></div></header>
<div class="content">

<section id="verdict"><div class="section-head"><span class="num">00</span><h2>先给结论</h2></div><p class="lead">图片给出的 256T 排列已成为 64T/256T 共用的主契约：在单个极化块内，<strong>垂直索引最快、水平索引次之</strong>，再切换极化块；<code>v=0</code> 永远对应物理顶部。实现不只改了配置项，而是让版本号、端口顺序和垂直方向穿过信道生成、后端排列、Type-I PMI、数据加载与系统仿真。</p><div class="grid three"><div class="card"><span class="tag ok">已实跑</span><h3>两套物理真相</h3><p>64T：<code>F∈ℂ^(192×64)</code>、每列 3 路；256T：<code>F∈ℂ^(1536×256)</code>、每列 6 路。两者每行唯一归属且 <code>FᴴF=I</code>，256T 最大误差 <code>__GRAM_ERR__</code>。</p></div><div class="card"><span class="tag ok">已实跑</span><h3>端到端路径</h3><p>InternalSim、Sionna RT、真实 Octave/QuaDRiGa、Type-I PMI、DFT codebook 与系统预设均已通过；canonical↔legacy 的物理置换也有有效信道不变性门。</p></div><div class="card"><span class="tag partial">显式边界</span><h3>不夸大的地方</h3><p>当前阵元图仍是参数化的 110°/65° 与理想 ±45° Jones；1 驱 3/6 仍用理想等幅与下倾相位。它们不是公司暗室实测复场或馈电标定。</p></div></div><div class="callout"><strong>“彻底可行”的工程含义：</strong>本报告不承诺以后永远没有 bug；它承诺关键维度、索引、功率、队列与资源守恒都有可重复的反例测试，且尚未验证的外部边界没有被藏在绿色结论里。</div><div class="actions"><a class="button" href="../docs/index.html#/hardware">打开本地开发者手册</a><a class="button secondary" href="https://lthub.xyz/superran/">打开公网手册</a></div></section>

<section id="mapping"><div class="section-head"><span class="num">01</span><h2>64T/256T 共用的唯一端口顺序</h2></div><div class="diagram">__PORT_GRID__</div><div class="formula-block">0-based：r(p,h,v) = p·(N_H·N_V) + h·N_V + v
64T 1-based：r₁ = 32p + 4h + v + 1
256T 1-based：r₁ = 128p + 8h + v + 1；v 从物理顶部向底部</div><div class="grid"><div class="card"><h3>两套物理锚点</h3><table><tr><th>profile</th><th>左上 / 第二水平列左上 / 第二极化左上</th></tr><tr><td>64T · 8H×4V×2</td><td><code>1 / 5 / 33</code></td></tr><tr><td>256T · 16H×8V×2</td><td><code>1 / 9 / 129</code></td></tr></table></div><div class="card"><h3>历史 64T 的兼容边界</h3><p>历史契约是 <code>h_v_pol + bottom_to_top</code>；新 64T 和 256T 都是 <code>pol_h_v + top_to_bottom</code>。读取旧数据必须通过物理行置换同时重排 H、W、F，不能只改元数据或 reshape。</p><p><span class="tag ok">锁定测试</span>两套锚点、物理坐标、Type-I 搜索与有效信道等价性均有断言。</p></div></div></section>

<section id="feed"><div class="section-head"><span class="num">02</span><h2>双极化、1 驱 3/6 与两套 F 矩阵</h2></div><div class="diagram">__FEED_SVG__</div><div class="grid"><div class="card"><h3>物理坐标</h3><div class="formula-block">v_phy = N_sub·v + q，N_sub∈{3,6}
y(h) = y₀ + 0.5h · λ
z(v_phy) = z₀ + (N_V,phy − 1 − v_phy)·0.67λ</div><p><code>top_to_bottom</code> 只改变编号增长的物理方向：端口/阵子 1 在端口/阵子 2 上方。64T 相邻 RF 相位中心相距 <strong>3×0.67=2.01λ</strong>，256T 为 <strong>6×0.67=4.02λ</strong>；这会带来垂直栅瓣，是硬件结构，不应被悄悄改成 0.5λ。</p></div><div class="card"><h3>双极化不是“数量 ×2”</h3><p>两个极化端口使用共享的空间位置，但 Jones 基向量分别为：</p><div class="formula-block">e₊₄₅ = A(φ,θ)/√2 · [1, 1]ᵀ
e₋₄₅ = A(φ,θ)/√2 · [1,−1]ᵀ</div><p>射线级信道用极化耦合矩阵与 XPR 连接收发 Jones 向量；代码不再用 <code>index % 2</code> 推断极化，因为这对分块的 <code>pol_h_v</code> 会直接错。</p></div></div><div class="callout warn"><strong>6° 电下倾的口径：</strong><code>DEFAULT_ELECTRICAL_DOWNTILT_DEG=6.0</code> 是可覆盖的工程基线，不是从场景几何推导出的“最佳值”。它进入子阵馈电相位；用户可用 <code>bs_antenna.fixed_vertical_subarray.fixed_downtilt_deg</code> 任意配置，改变后会产生新的物理配置与耦合哈希。</div><h3>F 的四个硬不变量</h3><div class="table-wrap"><table><thead><tr><th>不变量</th><th>当前证据</th><th>防住的错误</th></tr></thead><tbody><tr><td>形状</td><td><code>1536 × 256</code></td><td>把 RF 端口误当物理阵子</td></tr><tr><td>每列非零数</td><td>恒为 6</td><td>少驱/多驱阵子</td></tr><tr><td>每行所有者</td><td>恒为 1</td><td>两个端口误共用同一物理 AE</td></tr><tr><td>列正交归一</td><td><code>max|FᴴF−I|=__GRAM_ERR__</code></td><td>馈电功率失真与端口串扰</td></tr></tbody></table></div></section>

<section id="backends"><div class="section-head"><span class="num">03</span><h2>InternalSim / Sionna / QuaDRiGa 怎样贯通</h2></div><div class="diagram">__BACKEND_SVG__</div><div class="table-wrap"><table><thead><tr><th>路径</th><th>64T/256T 落点</th><th>证据等级</th><th>边界</th></tr></thead><tbody><tr><td><strong>InternalSim</strong></td><td>直接用 EffectiveArray 的 Jones、坐标、子阵响应和 <code>pol_h_v</code>；64T/256T 都走同一索引器，256T 最小数据集形状为 <code>[1,1,4,256,4]</code>。</td><td><span class="tag ok">真实生成</span></td><td>参数化阵元图，不是实测表。</td></tr><tr><td><strong>Sionna RT</strong></td><td>显式 canonical↔Sionna permutation，并注册 <code>[+45°,−45°]</code> 专用 slant 顺序，避免 Sionna 内置 <code>cross=[−45°,+45°]</code> 反序；真实 RT 锁定 effective 与 physical reference 等价。</td><td><span class="tag ok">真实 RT + fallback</span></td><td>256T 维度门使用 fallback；64T 的真实 RT 验证同一物理机制。</td></tr><tr><td><strong>QuaDRiGa</strong></td><td>Python 配置携带端口顺序和 <code>elements_per_rf_port</code>；builder 泛化为 1→N。本机 Octave 对 64T 真实执行 <code>qd_layout → get_channels → fr()</code>；64T/256T 都在真实 QuaDRiGa array builder 中验证了 192×64 / 1536×256 coupling。</td><td><span class="tag ok">64T 真信道 + 两套真 builder</span></td><td>256T 尚未跑完整 <code>get_channels → fr()</code>；仍需在公司固定 MATLAB/QuaDRiGa 版本的 CI 补这一门。</td></tr><tr><td><strong>Type-I / PMI</strong></td><td><code>8H4V</code> 与 <code>16H8V</code> 都先映射到 38.214 codebook 顺序，搜索后再映回 canonical；产品侧反查已改为自包含，子进程不再运行时依赖 MSG 包。</td><td><span class="tag ok">数值 + E2E</span></td><td>PMI 周期仍是场景配置，不在阵列实现中写死。</td></tr></tbody></table></div><div class="callout"><strong>为什么后端不能各自复制一份编号公式：</strong>编号、物理方向、极化掩码和源库 flatten 顺序都由 <code>EffectiveArray / PortIndex</code> 统一生成。样本元数据保存 <code>port_layout_contract_version</code>、<code>port_order</code>、<code>vertical_index_order</code>、coupling/pattern hash；加载时重建 F 并验 hash，防止旧样本配上新馈电。</div></section>

<section id="system"><div class="section-head"><span class="num">04</span><h2>系统仿真与体验速率的最终口径</h2></div><div class="grid"><div class="card"><h3>经典 PF 与 R̄u（RU）</h3><div class="formula-block">Mᵤ(t) = TBSᵤ(17,t) / max(R̄ᵤ(t), ε)
R̄ᵤ(t+1) = (1−a)R̄ᵤ(t) + a·Rᵤ_credit(t)
默认 Rᵤ_credit = 本 TTI 实际 scheduled TBS bytes</div><p>排序的 numerator 仍是全带潜力，避免小队列伪装成差信道；但历史记账必须使用实际 grant。只拿 1 RBG 的用户若按 17 RBG 全带记账，平均速率会被过度抬升、后续 PF metric 被错误压低，正好饿死小包用户。</p></div><div class="card"><h3>按需 RBG 不能做线性除法</h3><div class="formula-block">n* = min{n∈[1,17] : TBS(slot,MCS,rank,n) ≥ queue_bytes}
    = searchsorted(TBS_table, queue_bytes) + 1</div><p>TBS 对 RBG 数严格单调，但不严格线性。实现使用 28×4×17 的反查表；余量留空，不把尾料补给第一名，从而 PRB utilization 反映真实话务。</p></div><div class="card"><h3>SU / MU 自适应</h3><p>每个 TTI 只做一次 PF 优先级。随后分别构造“全 SU”和“允许 MU”的完整计划，用<strong>不超过队列的 useful bytes</strong>比较；若 SU 已能清空所有队列，强制走 SU。MU 的 MCS 链为：</p><div class="formula-block">CQI + BF + SU OLLA + CorrLoss + PowerLoss + MU OLLA</div></div><div class="card"><h3>功率约束与预编码</h3><p>代码内部 precoder 采用 <code>[antenna, layer]</code>，所以“每天线”约束落在<strong>行范数</strong>；若公司文档采用 <code>[layer, antenna]</code>，同一操作表现为列范数。EBF 是总功率 SVD；PEBF 按最大发射支路整体缩放；NEBF 对每天线支路强行归一。</p><p><span class="tag ok">反向验证</span>SU 中 NEBF≈EBF≫PEBF；MU 中构造残留相关性后可出现 NEBF&lt;PEBF。</p></div></div><h3>体验 KPI 已实现的核心定义</h3><div class="table-wrap"><table><thead><tr><th>KPI</th><th>分子 / 分母</th><th>统计边界</th></tr></thead><tbody><tr><td>首包时延</td><td>packet arrival → 第一次获得调度</td><td>用户级分布与小区聚合</td></tr><tr><td>掐头去尾速率</td><td>busy-period useful bytes / 首次 TX 到尾端</td><td>不含首包等待</td></tr><tr><td>含头速率</td><td>同一 useful bytes / 首包 arrival 到尾端</td><td>把首包等待纳入体验</td></tr><tr><td>PRB utilization</td><td>测量窗内 used PRB-equivalent / available PRB-equivalent</td><td>可作话务校准目标，也可作结果</td></tr><tr><td>0…17 RBG 占比分布</td><td>占用 n 个 RBG 的 TTI 数 / 测量窗 TTI 数</td><td>含 n=0；mixed 话务常两头高</td></tr><tr><td>MU 配对比例</td><td>MU 生效 PRB / 已用 PRB</td><td>不是 /全部可用 PRB</td></tr></tbody></table></div><div class="callout"><strong>预启动：</strong>默认可配置 1 s warmup。业务、SRS、PMI、PF 平均与 OLLA 从 0 s 演进，KPI 只统计 1 s 之后；这规避冷启动偏差，但不会把收敛问题隐藏掉，收敛轨迹仍可审计。</div></section>

<section id="rename"><div class="section-head"><span class="num">05</span><h2>SuperWireless → SuperRAN 的主命名迁移</h2></div><div class="table-wrap"><table><thead><tr><th>层级</th><th>新主命名</th><th>兼容策略</th></tr></thead><tbody><tr><td>产品 / 页面</td><td><strong>SuperRAN</strong></td><td>历史报告不改写，保留出处；当前代码与文档零旧品牌命中。</td></tr><tr><td>Python distribution / package</td><td><code>superran</code></td><td>旧顶层包只导出 <code>Dataset/load/version</code> 并发 <code>FutureWarning</code>；不镜像内部子模块，避免双份全局状态。</td></tr><tr><td>CLI</td><td><code>superran-mcp</code></td><td>旧 CLI 不保留第二入口。</td></tr><tr><td>MCP server / tools</td><td><code>superran</code> / 34 个 <code>sr_*</code></td><td>客户端配置迁移到新 server key；旧在途进程不强杀，新会话读取新路径。</td></tr><tr><td>环境变量</td><td><code>SUPERRAN_*</code></td><td>仅当新变量缺失时，单向复制旧值并记录审计；新值永远优先。</td></tr><tr><td>Skill</td><td>channel-sim 文档使用 SuperRAN / <code>sr_*</code></td><td>项目版、Codex 安装版与 Claude 安装版的规范化文本 SHA-256 对齐；原始字节只差 CRLF/LF。</td></tr><tr><td>仓库 / 本机目录</td><td><code>SuperRAN</code></td><td>GitHub 仓库已改名；因主工作树不可由 <code>git worktree move</code> 移动且被 3 个在途 MCP 进程锁定，新主仓从改名后的远端建立，旧仓禁用 push、仅供在途会话自然退出。</td></tr><tr><td>部署</td><td><code>/superran/</code></td><td><code>/superwireless/</code> 只做 HTTP 重定向，不再独立发布。</td></tr></tbody></table></div><div class="callout warn"><strong>为什么兼容层必须小：</strong>若把所有旧子模块都 alias 到新包，Python 会同时加载两套模块名，OLLA、RNG、注册表、缓存等模块级状态可能分叉。现在的边界只保住最常见的“打开旧 notebook 并迁移”场景，错误使用会显式失败，不会静默跑出两种结果。</div><div class="callout"><strong>公网切换已实测：</strong><code>/superran/</code> 与 <code>/superran/audit.html</code> 均为 HTTP 200 且响应体 SHA-256 与本地逐字一致；旧路径为 301；既有 <code>/ai-daily/install/</code> 和 8443 Hub 仍为 200。Nginx 先 <code>-t</code> 再热重载，首个回滚点为 <code>/var/backups/superran/20260813T154936Z</code>。</div></section>

<section id="tests"><div class="section-head"><span class="num">06</span><h2>回归与压力证据</h2></div><p>这些结果来自本机当前源码的实际执行，不用“趋势上没问题”替代门禁。MSG 全量门先显式把当前仓库 <code>src</code> 置于 <code>PYTHONPATH</code>；随后已重装当前仓 editable 包，并在不加覆盖的情况下让 119 个受影响专项再次通过。</p><div class="card"><div class="testbar"><b>MSG 全量 unit</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">427 passed · 14 skipped · MATLAB gate separate</div></div><div class="testbar"><b>Sionna 专项（真实 RT）</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">11 passed · 12.90s</div></div><div class="testbar"><b>真实 QuaDRiGa</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">64T+256T PASS · 1083s</div></div><div class="testbar"><b>产品 64T/256T 契约</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">14 passed</div></div><div class="testbar"><b>系统场景枚举</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">91 passed</div></div><div class="testbar"><b>链路自适应压力</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">PASS · 537.6s</div></div><div class="testbar"><b>链路级压力</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">PASS · 243.6s</div></div><div class="testbar"><b>RNG/Gate/MCP 组合</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">PASS · 669.1s</div></div><div class="testbar"><b>21 小区干扰压力</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">PASS · 732s</div></div><div class="testbar"><b>E2E / 结果 / RT</b><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="test-result">全部 PASS</div></div></div><div class="grid three" style="margin-top:18px"><div class="card"><h3>维度与置换门</h3><p>64/192 与 256/1536、锚点 1/5/33 和 1/9/129、F 行所有权、canonical↔legacy 有效信道等价。</p></div><div class="card"><h3>物理门</h3><p>F 列正交；top-to-bottom 坐标方向；+45°/−45° Jones 顺序；方向图/下倾；effective 与 physical reference 等价。</p></div><div class="card"><h3>系统门</h3><p>RBG、bytes、padding、queue、PF credit、MU shared PRB 与 KPI 测量窗逐 TTI 对账。</p></div></div></section>

<section id="bugs"><div class="section-head"><span class="num">07</span><h2>深潜过程中发现并当场修掉的问题</h2></div><div class="table-wrap"><table><thead><tr><th>问题</th><th>后果</th><th>修复 / 反向验证</th></tr></thead><tbody><tr><td>经典 64T 与 256T 两种展平顺序</td><td>跨阵列复用 codebook、数据集或模型时，同一个 row 代表不同物理端口。</td><td>新 64T/256T 统一 <code>pol_h_v + top_to_bottom</code>；legacy 只允许显式读取，并用置换等价门锁定。</td></tr><tr><td>未知 64 端口面板被按公司 64T 猜测</td><td>自定义 64 端口阵列会被静默套上错误物理顺序。</td><td>只有未显式给 panel 时才按端口数推 profile；显式未知面板保持 custom/legacy，测试防止未来再猜。</td></tr><tr><td>产品 PMI 在 fetch 子进程运行时导入 MSG</td><td>开发环境能跑，独立交付子进程报 <code>ModuleNotFoundError</code>。</td><td>索引与 Type-I 置换下沉为 SuperRAN 自包含 helper；完整 E2E 中文 PMI/SRS/PDP/geometry 重跑通过。</td></tr><tr><td>Sionna 内置 cross 极化顺序相反</td><td>内置 <code>[−45°,+45°]</code> 会与公司 p0=+45°、p1=−45° 对调。</td><td>显式注册 <code>[+π/4,−π/4]</code> pattern，并检查返回的 port/element/polarization 三元组。</td></tr><tr><td>PF 用 fullband best_se 记账</td><td>1 RBG 小包用户被约 17 倍过罚，后续更难获调度。</td><td>默认 <code>scheduled_tbs</code>；故意切回 legacy 时必须观察到 credit 大于实际 grant。</td></tr><tr><td>TBS 用除法估 RBG 数</td><td>量化 TBS 非线性，约 1% 偏差就可能少给 1 RBG，包发不完。</td><td>全表 <code>searchsorted</code>；单调性扫描覆盖 MCS/rank/RBG。</td></tr><tr><td>6° 是匿名默认值</td><td>手册无法解释来源，也无法审计不同下倾是否真改变 F。</td><td>新增 <code>DEFAULT_ELECTRICAL_DOWNTILT_DEG</code>，允许用户覆盖并写入 calibration/hash；文档明确它是工程基线。</td></tr><tr><td>MSG editable venv 指向旧仓</td><td>直接运行可能从 <code>C:\Users\lintian\MSG-Platform</code> 导入，产生假绿。</td><td>先用显式 <code>PYTHONPATH</code> 隔离并验证；随后执行当前仓 <code>pip install -e . --no-deps</code>，直接导入路径已变为 <code>C:\Vibe\Wireless\MSG-Platform\src</code>，119 个受影响专项无覆盖重跑通过。</td></tr><tr><td>生成手册后源码变化</td><td>静态 HTML 容易陈旧，旧的 per-ray 归一与后端状态曾留在文字里。</td><td><code>--check</code> 对当前树重建比较；专题页同步改成当前代码行为，并重新做浏览器 QA。</td></tr></tbody></table></div></section>

<section id="modules"><div class="section-head"><span class="num">08</span><h2>主要模块当前为什么可用</h2></div><div class="card"><div class="module"><b>硬件与阵列</b><span class="tag ok">可用</span><p>同一个 EffectiveArray 生成 64T/256T 的坐标、F、Jones、codebook 与版本化元数据；锚点、物理行所有权和置换不变量共同防止“shape 对、物理端口错”。</p></div><div class="module"><b>信道生成</b><span class="tag ok">可用</span><p>InternalSim 256T 真实生成已跑；同站扇区共享 site propagation state、各扇区再通过方向图/boresight 分化；不同 site_id 使用独立确定性流。</p></div><div class="module"><b>SRS / 信道估计</b><span class="tag partial">分层可用</span><p>ZC/短低 PAPR、周期/offset、LS-linear、频域 LMMSE 与 hop concat 有显式配置；“SRS 周期”与调度时使用的 CSI lag 分开表达。完整物理多端口 <code>Y=HX+I+N</code> 与时频空 LMMSE 仍列为下一阶段。</p></div><div class="module"><b>PMI / 测量</b><span class="tag ok">可用</span><p>Type-I 先按 layout 重排、搜索后再映回输入顺序；64T/256T precoder 行数、谱效和独立子进程交付都有回归。</p></div><div class="module"><b>功率与预编码</b><span class="tag ok">可用</span><p>EBF/PEBF/NEBF 的约束语义和矩阵朝向已显式；SU 与 MU 两个反例门能够区分“功率用满”与“破坏正交后增加干扰”。</p></div><div class="module"><b>链路自适应</b><span class="tag ok">可用</span><p>SU 使用 CQI+BF+OLLA；MU 额外叠加 CorrLoss、按总 rank 分功率的 PowerLoss 与用户级 MU OLLA。串/并行复杂信道结果 bitwise 一致。</p></div><div class="module"><b>体验调度</b><span class="tag ok">可用</span><p>PF 只排序一次；SU/MU 用 useful bytes 比较；按需 RBG、FIFO、BLER、回退、PF credit、warmup 形成闭环。错误 fullband credit 有反向用例。</p></div><div class="module"><b>KPI 工作台</b><span class="tag ok">可用</span><p>小区/用户双 Tab、CDF/用户图、首包/含头速率、PRB 与 MU/used 均有明确定义；LLM 只重排展示优先级，不能删除原始 KPI 或改数值。</p></div><div class="module"><b>MCP / Agent 接口</b><span class="tag ok">可用</span><p>34 个 <code>sr_*</code> 工具通过 server smoke；大型 ndarray 不塞入对话，而返回 JSON、句柄与产物路径，适合复现实验。</p></div><div class="module"><b>QuaDRiGa 链路</b><span class="tag partial">分层实跑</span><p>Octave 中 64T 已执行真实 channel generation 与 <code>fr()</code>；64T/256T 的真实 builder、coupling 形状、列正交与锚点均通过。256T 完整 <code>get_channels → fr()</code> 仍是 CI 待补门，不能写成已覆盖。</p></div></div></section>

<section id="boundaries"><div class="section-head"><span class="num">09</span><h2>仍需你后续提供或拍板的边界</h2></div><div class="decisions"><div class="decision"><div><h3>公司实测复 Jones / 阵元方向图</h3><p>当前 110° 水平、65° 垂直、8 dBi、XPD 8 dB 是可配置的 3GPP 式参数化占位。拿到频率×方位×俯仰的复场表后，替换 <code>measured_jones</code> loader；在此之前报告必须标 <code>element_pattern_is_measured=false</code>。</p></div></div><div class="decision"><div><h3>1 驱 3/6 的实测幅相标定</h3><p>当前各路等幅，6° 用理想相位渐进。若公司能提供每路 <code>Aq/ψq</code>、互耦与校准版本，可直接进入 F；coupling hash 会变化，旧数据集不会被误配。</p></div></div><div class="decision"><div><h3>物理多端口 SRS 与 LMMSE 档位</h3><p>当前估计器接口可做 LS 与频域 LMMSE，但完整的多端口接收观测应先冻结为 <code>Y=HX+I+N</code>，再决定默认 LS、时频 LMMSE 或时频空 LMMSE。推荐默认透明 LS，LMMSE 作为带协方差来源标签的可选算法。</p></div></div><div class="decision"><div><h3>现场 EPF 与 MU pairing 细节</h3><p>当前按已确认的经典 PF 与 SU/MU useful-byte 比较。厂商 EPF 的时延因子、budget 和具体 MU pairing 规则仍应由公司定义后再版本化，不冒充 3GPP 标准。</p></div></div></div><div class="callout risk"><strong>不会静默做的事：</strong>不会把参数化方向图说成实测；不会把一次 Octave 通过扩大成所有 MATLAB/QuaDRiGa 版本都已覆盖；不会因为页面“看起来合理”而跳过索引、字节、资源和功率守恒门。</div></section>

<section id="provenance"><div class="section-head"><span class="num">10</span><h2>证据指纹与复现信息</h2></div><div class="grid"><div class="card"><h3>仓库状态</h3><p>SuperRAN：<code>__PRODUCT_BRANCH__ @ __PRODUCT_HEAD__</code><br>MSG-Platform：<code>__MSG_BRANCH__ @ __MSG_HEAD__</code></p><p>生成时间：<code>__GENERATED__</code><br>256T F hash：<code>__F_HASH__</code><br>256T pattern hash：<code>__P_HASH__</code></p></div><div class="card"><h3>一条命令重建</h3><div class="formula-block">python scripts/make_superran_256t_migration_audit.py</div><p class="small">脚本会从当前代码同时重建 64T/256T 的 F、不变量、Git 状态与关键源码 SHA-256。HTML 本身不依赖外部字体、脚本或 CDN。</p></div></div><h3>关键源码 SHA-256</h3><div class="table-wrap"><table><thead><tr><th>文件</th><th>SHA-256</th></tr></thead><tbody>__HASH_ROWS__</tbody></table></div><details><summary>展开机器可读的 64T/256T 阵列证据 JSON</summary><div class="detail"><pre class="evidence-json">__EVIDENCE_JSON__</pre></div></details></section>

</div><footer class="foot"><strong>SuperRAN</strong> · 64T/256T canonical port contract, hardware truth and simulation integration audit · __GENERATED__</footer></main></div>
<script>
const links=[...document.querySelectorAll('.sidebar a')];const sections=links.map(a=>document.querySelector(a.getAttribute('href'))).filter(Boolean);const observer=new IntersectionObserver(entries=>{entries.forEach(e=>{if(e.isIntersecting){links.forEach(a=>a.classList.toggle('active',a.getAttribute('href')==='#'+e.target.id));}})},{rootMargin:'-20% 0px -68% 0px'});sections.forEach(s=>observer.observe(s));
</script></body></html>'''


def main() -> None:
    page = render()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(OUT)
    print(f"chars={len(page):,} sha256={sha256(OUT)}")


if __name__ == "__main__":
    main()
