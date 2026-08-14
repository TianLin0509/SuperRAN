"""Build the self-contained SuperRAN developer documentation site.

The output is a single ``docs/index.html`` with hash-routed logical pages.
Curated algorithm explanations live in this file; volatile inventories
(modules, public APIs, MCP tools, tests, presets and Skill references) are
derived from the current repository on every build so counts cannot drift.

Python 3.10 compatibility matters.  In particular, formula expressions with
backslashes stay in module-level constants instead of f-string expressions.
"""
from __future__ import annotations

import ast
import cmath
import html
import json
import math
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "superran"
OUT = ROOT / "docs" / "index.html"
GITHUB = "https://github.com/TianLin0509/superran/blob/main/"
sys.path.insert(0, str(ROOT / "src"))

from superran import katex as kx  # noqa: E402
from superran import mathml as mm  # noqa: E402


def M(tex: str, *, block: bool = True) -> str:
    """KaTeX-upgradable formula with MathML fallback."""
    return kx.wrap(tex, mm.render(tex, block=block), display=block)


# Keep LaTeX outside f-string expression parts for Python < 3.12.
F_CHANNEL = M(
    r"H_u(t,f)=\sum_{\ell=1}^{L}\sqrt{P_\ell}\,e^{-j2\pi f\tau_\ell}"
    r"e^{j2\pi\nu_\ell t}\,a_{\mathrm{UE},\ell}\,J_\ell\,"
    r"a_{\mathrm{BS},\ell}^{H}",
)
F_CHANNEL_SHAPE = M(
    r"H_{\mathrm{DL}}\in\mathbb C^{N_{\mathrm{UE,Rx}}\times N_{\mathrm{BS,Tx}}}"
    r"=\mathbb C^{4\times64},\qquad "
    r"H_{\mathrm{UL}}\in\mathbb C^{64\times4}",
)
F_DATASET = M(
    r"h_{\mathrm{true}},h_{\mathrm{est}}\in"
    r"\mathbb C^{N\times T\times RB\times N_{\mathrm{BS}}\times N_{\mathrm{UE}}}",
)
F_PATTERN = M(
    r"A_H(\phi)=\min\!\left(12(\phi/\phi_{3\mathrm{dB}})^2,A_m\right),"
    r"\qquad A_V(\epsilon)=\min\!\left(12(\epsilon/\theta_{3\mathrm{dB}})^2,A_m\right)",
)
F_PATTERN_COMBINE = M(
    r"G_E(\phi,\epsilon)=G_{\max}-\min\!\left(A_H(\phi)+A_V(\epsilon),A_m\right),"
    r"\qquad g_E(\phi,\epsilon)=10^{G_E(\phi,\epsilon)/20}",
)
F_JONES = M(
    r"f_p(\phi,\epsilon)=g_E(\phi,\epsilon)"
    r"\begin{bmatrix}\cos\zeta_p\\\sin\zeta_p\end{bmatrix},"
    r"\qquad \zeta_0=+45^\circ,\quad\zeta_1=-45^\circ",
)
F_SUBARRAY_PATTERN = M(
    r"S_M^{\mathrm{RX}}(\epsilon,f)=\sum_{q=0}^{M-1}w_q^{*}"
    r"e^{-j2\pi(f/f_{\mathrm{ref}})z_q\sin\epsilon},"
    r"\qquad a_{\mathrm{port}}=F^Ha_{\mathrm{AE}}",
)
F_RAY_POLARIZATION = M(
    r"c_{\ell,p_t,p_r}=f_{\mathrm{RX},p_r}^{T}J_\ell f_{\mathrm{TX},p_t},"
    r"\qquad H_\ell\propto\sqrt{P_\ell}\,c_{\ell,p_t,p_r}\,"
    r"a_{\mathrm{RX},\ell}a_{\mathrm{TX},\ell}^{H}"
    r"e^{-j2\pi f\tau_\ell}e^{j2\pi\nu_\ell t}",
)
F_FEED = M(
    r"w_q=\frac{A_qe^{j\psi_q}e^{j2\pi z_q\sin\theta_{\mathrm{tilt}}}}"
    r"{\left\|[A_ke^{j\psi_k}e^{j2\pi z_k\sin\theta_{\mathrm{tilt}}}]_k\right\|_2}",
)
F_COUPLING = M(
    r"F_{e,r}=\begin{cases}w_q,&e=e(h,3v_{\mathrm{RF}}+q,p),\ "
    r"r=r(h,v_{\mathrm{RF}},p)\\0,&\text{otherwise}\end{cases},"
    r"\qquad F\in\mathbb C^{192\times64}",
)
F_COUPLING_256 = M(
    r"r_{256}(p,h,v)=p\cdot128+h\cdot8+v,\qquad "
    r"F_{e,r}=w_q\ \text{for}\ e=e(p,h,6v+q),\ q=0,\ldots,5,\qquad "
    r"F\in\mathbb C^{1536\times256}",
)
F_EFFECTIVE = M(
    r"a_{\mathrm{port}}=F^Ha_{\mathrm{AE}},\qquad "
    r"H_{\mathrm{port}}=H_{\mathrm{AE}}F",
)
F_SRS_RX = M(
    r"Y_{\mathrm{SRS}}[k]=H_{\mathrm{UL}}[k]X_{\mathrm{SRS}}[k]+I[k]+N[k],"
    r"\quad H_{\mathrm{UL}}[k]\in\mathbb C^{64\times4}",
)
F_LS = M(
    r"\widehat H_{\mathrm{LS}}[k]=Y_{\mathrm{SRS}}[k]X_{\mathrm{SRS}}[k]^{\dagger}",
)
F_LMMSE = M(
    r"\widehat h_{t,\mathrm{LMMSE}}=R_{tp}\left(R_{pp}+R_v\right)^{-1}"
    r"\widehat h_{p,\mathrm{LS}}",
)
F_SRS_LAG = M(
    r"\tau_b(t)=t-t_{\mathrm{last\ SRS},b}-D_{\mathrm{proc}},\qquad "
    r"\widehat H_b(s)=H_b\!\left(\max(0,s-\lceil\tau_b/\Delta t_{\mathrm{snap}}\rceil)\right)",
)
F_EBF = M(
    r"Q_{\mathrm{EBF}}=W\sqrt{P/L},\qquad "
    r"\operatorname{tr}(QQ^H)\le P",
)
F_PEBF = M(
    r"Q_{\mathrm{PEBF}}=\alpha Q_{\mathrm{EBF}},\qquad "
    r"\alpha=\min\!\left(1,\sqrt{\frac{P/M}{\max_m\|q_{m,:}\|_2^2}}\right)",
)
F_NEBF = M(
    r"q_{m,:}^{\mathrm{NEBF}}=\sqrt{P/M}\,\frac{q_{m,:}^{\mathrm{EBF}}}"
    r"{\|q_{m,:}^{\mathrm{EBF}}\|_2},\qquad m=1,\ldots,M",
)
F_MMSE = M(
    r"G=(H_{\mathrm{eff}}^HH_{\mathrm{eff}}+R_{uu}+N_0I)^{-1}H_{\mathrm{eff}}^H",
)
F_STREAM_SINR = M(
    r"\gamma_\ell=\frac{|g_\ell^Hh_\ell|^2P_\ell}"
    r"{\sum_{j\ne\ell}|g_\ell^Hh_j|^2P_j+g_\ell^H(R_{uu}+N_0I)g_\ell}",
)
F_RB_LINK_BUDGET = M(
    r"P_{\mathrm{tx,RB}}[\mathrm{dBm}]=P_{\mathrm{tx,total}}-10\log_{10}N_{\mathrm{RB}},"
    r"\qquad N_{\mathrm{RB}}[\mathrm{dBm}]=-174+10\log_{10}(12\Delta f)+NF",
)
F_PREBEAM_ANCHOR = M(
    r"S_0=\mathbb E[|H|^2]P,\qquad I+N=\frac{S_0}{10^{\gamma_{\mathrm{geo,dB}}/10}},"
    r"\qquad \gamma_{\mathrm{rank1}}=\gamma_{\mathrm{geo}}"
    r"\frac{\mathbb E[\sigma_1^2]}{\mathbb E[|H|^2]}",
)
F_RANK = M(
    r"r^\star=\arg\max_{r\in\{1,2,3,4\}}\ r\cdot\eta\!\left("
    r"\gamma_{\mathrm{eff}}(r)\right)",
)
F_TX_SINR = M(
    r"\gamma_{\mathrm{tx,SU}}=\Gamma(\mathrm{MCS}(\mathrm{CQI}))"
    r"+G_{\mathrm{BF}}+\Delta_{\mathrm{OLLA}}\qquad[\mathrm{dB}]",
)
F_MU_SINR = M(
    r"\gamma_{\mathrm{tx,MU}}=\Gamma(\mathrm{MCS}(\mathrm{CQI}))+G_{\mathrm{BF}}"
    r"+\Delta_{\mathrm{SU\ OLLA}}+L_{\mathrm{corr}}+L_{\mathrm{power}}"
    r"+\Delta_{\mathrm{MU\ OLLA}}",
)
F_POWER_LOSS = M(r"L_{\mathrm{power}}=-10\log_{10}K_{\mathrm{MU}}\ \mathrm{dB}")
F_TBS = M(r"N_{\mathrm{info}}=N_{\mathrm{RE}}Q_mR\nu,\qquad TBS=Q_{38.214}(N_{\mathrm{info}})")
F_RBG_SEARCH = M(
    r"n_u^\star=\min\{n\in[1,17]:TBS(s,m_u,r_u,n)\ge B_u\}"
    r"=\operatorname{searchsorted}(\mathbf{TBS}_{s,m,r},B_u)+1",
)
F_PF = M(r"M_u(t)=\frac{TBS_u(17,t)}{\max(\bar R_u(t),\epsilon)}")
F_QOS_PF = M(
    r"M_u=w_u\frac{[R_u^{\mathrm{inst}}]^\beta}{[\bar R_u]^\alpha}"
    r"\left(1+\frac{D_u^{\mathrm{HoL}}}{D_u^{\mathrm{budget}}}\right)^\gamma",
)
F_RAVG = M(
    r"\bar R_u(t+1)=(1-a)\bar R_u(t)+aR_u^{\mathrm{credit}}(t),\qquad "
    r"a=1/T_{\mathrm{PF}}",
)
F_OLLA = M(
    r"\Delta(t+1)=\operatorname{clip}\!\left(\Delta(t)+"
    r"\mathbf1_{\mathrm{ACK}}s_{\uparrow}-\mathbf1_{\mathrm{NACK}}s_{\downarrow}\right)",
)
F_BUSY_RATE = M(
    r"R_{\mathrm{trim}}=\frac{\sum_{i=1}^{K-1}B_i}"
    r"{t_{\mathrm{ACK},K-1}-t_{\mathrm{first\ TX}}},\qquad "
    r"R_{\mathrm{head}}=\frac{\sum_{i=1}^{K-1}B_i}"
    r"{t_{\mathrm{ACK},K-1}-t_{\mathrm{arrival},1}}",
)
F_FIRST_PACKET = M(r"D_{\mathrm{first}}=t_{\mathrm{first\ scheduled}}-t_{\mathrm{arrival}}")
F_PRB_UTIL = M(
    r"U_{\mathrm{PRB}}=\frac{\sum_t n_{\mathrm{RBG,used}}(t)f_{\mathrm{slot}}(t)}"
    r"{17\sum_t f_{\mathrm{slot}}(t)},\qquad "
    r"U_{\mathrm{MU}}=\frac{\mathrm{MU\ PRB\ equivalent}}{\mathrm{used\ PRB\ equivalent}}",
)
F_RB_COUPLING = M(
    r"\gamma_{u,r}=\frac{q_{c(u),r}S_{u,c(u),r}}"
    r"{\sum_{c\ne c(u)}q_{c,r}I_{u,c,r}+N_{u,r}}",
)
F_IOT = M(
    r"\mathrm{IoT}=10\log_{10}\frac{I+N}{N},\qquad "
    r"I=S10^{-\mathrm{SIR}/10},\quad N=S10^{-\mathrm{SINR}/10}-I",
)
F_CRN = M(
    r"d_i=Y_i^{(A)}-Y_i^{(B)}\ \text{with identical }"
    r"(\text{drop},\text{traffic},\text{BLER},\text{scheduler})\text{ streams}",
)
F_CONSERVE = M(
    r"B_{\mathrm{arrived}}=B_{\mathrm{ACK}}+B_{\mathrm{queued}}+"
    r"B_{\mathrm{inflight}}+B_{\mathrm{dropped}}",
)


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
    return s or "section"


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def source_line(rel: str, needle: str) -> int:
    for number, line in enumerate(read(rel).splitlines(), 1):
        if needle in line:
            return number
    return 1


def source_ref(rel: str, needle: str, label: str | None = None) -> str:
    line = source_line(rel, needle)
    text = label or f"{rel}:{line}"
    href = GITHUB + rel.replace("\\", "/") + f"#L{line}"
    return f'<a class="src" href="{esc(href)}" target="_blank" rel="noreferrer">{esc(text)}</a>'


def code(text: str, language: str = "python") -> str:
    return (
        '<div class="codebox"><div class="codebar"><span>' + esc(language)
        + '</span><button class="copy" type="button">复制</button></div><pre><code>'
        + esc(text.strip("\n")) + "</code></pre></div>"
    )


def callout(kind: str, title: str, body: str) -> str:
    icons = {
        "note": "i", "good": "✓", "warn": "!", "danger": "×", "decision": "?",
    }
    return (
        f'<aside class="callout {esc(kind)}"><span class="callout-icon">'
        f'{esc(icons.get(kind, "i"))}</span><div><strong>{esc(title)}</strong>{body}</div></aside>'
    )


def steps(items: Iterable[tuple[str, str]]) -> str:
    rows = []
    for index, (title, body) in enumerate(items, 1):
        rows.append(
            f'<li><span class="step-no">{index}</span><div><strong>{esc(title)}</strong>{body}</div></li>'
        )
    return '<ol class="steps">' + "".join(rows) + "</ol>"


def metric_cards(items: Iterable[tuple[str, str, str]]) -> str:
    return '<div class="metrics">' + "".join(
        f'<div class="metric"><span>{esc(label)}</span><b>{esc(value)}</b><small>{esc(note)}</small></div>'
        for label, value, note in items
    ) + "</div>"


def table(headers: list[str], rows: Iterable[Iterable[str]], *, raw: set[int] | None = None) -> str:
    raw = raw or set()
    head = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = []
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            cells.append(f"<td>{value if index in raw else esc(value)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return '<div class="table-wrap"><table><thead><tr>' + head + '</tr></thead><tbody>' + "".join(body) + "</tbody></table></div>"


@dataclass
class MemberDoc:
    name: str
    kind: str
    line: int
    signature: str
    doc: str


@dataclass
class SymbolDoc:
    module: str
    name: str
    kind: str
    line: int
    signature: str
    doc: str
    members: list[MemberDoc] = field(default_factory=list)


@dataclass
class ModuleDoc:
    name: str
    rel: str
    lines: int
    doc: str
    symbols: list[SymbolDoc]


@dataclass
class Page:
    key: str
    title: str
    group: str
    eyebrow: str
    summary: str
    body: str
    tags: tuple[str, ...] = ()


def first_paragraph(doc: str | None, limit: int = 360) -> str:
    if not doc:
        return "—"
    part = re.split(r"\n\s*\n", doc.strip())[0]
    part = re.sub(r"\s+", " ", part)
    return part if len(part) <= limit else part[: limit - 1] + "…"


def _annotation(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "…"


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    ret = _annotation(node.returns)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args})" + (f" -> {ret}" if ret else "")


def scan_modules() -> list[ModuleDoc]:
    modules: list[ModuleDoc] = []
    for path in sorted(SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        symbols: list[SymbolDoc] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("_"):
                continue
            if isinstance(node, ast.ClassDef):
                bases = ", ".join(_annotation(x) for x in node.bases)
                signature = f"class {node.name}" + (f"({bases})" if bases else "")
                members: list[MemberDoc] = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                        members.append(MemberDoc(
                            item.name, "method", item.lineno, _function_signature(item),
                            first_paragraph(ast.get_docstring(item)),
                        ))
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) \
                            and not item.target.id.startswith("_"):
                        default = ""
                        if item.value is not None:
                            try:
                                default = " = " + ast.unparse(item.value)
                            except Exception:
                                default = " = …"
                        members.append(MemberDoc(
                            item.target.id, "field", item.lineno,
                            f"{item.target.id}: {_annotation(item.annotation)}{default}", "数据字段",
                        ))
                symbols.append(SymbolDoc(
                    path.stem, node.name, "class", node.lineno, signature,
                    first_paragraph(ast.get_docstring(node)), members,
                ))
            else:
                symbols.append(SymbolDoc(
                    path.stem, node.name, "function", node.lineno,
                    _function_signature(node), first_paragraph(ast.get_docstring(node)),
                ))
        modules.append(ModuleDoc(
            path.stem, str(path.relative_to(ROOT)).replace("\\", "/"),
            text.count("\n") + 1, first_paragraph(ast.get_docstring(tree)), symbols,
        ))
    return modules


def scan_tools(modules: list[ModuleDoc]) -> list[SymbolDoc]:
    server = next(m for m in modules if m.name == "server")
    return [s for s in server.symbols if s.name.startswith("sr_")]


def scan_tests() -> list[dict[str, Any]]:
    out = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        check_sites = len(re.findall(r"(?<!def )\bcheck\s*\(", text))
        assert_sites = sum(isinstance(n, ast.Assert) for n in ast.walk(tree))
        sections = re.findall(r"(?:sect|section)\(\s*[\"']([^\"']+)", text)
        out.append({
            "name": path.name,
            "rel": str(path.relative_to(ROOT)).replace("\\", "/"),
            "lines": text.count("\n") + 1,
            "check_sites": check_sites,
            "assert_sites": assert_sites,
            "sections": sections,
        })
    return out


def scan_skills() -> list[dict[str, Any]]:
    base = ROOT / "skills" / "channel-sim"
    paths = [base / "SKILL.md"] + sorted((base / "references").glob("*.md"))
    out = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        headings = [m.group(2).strip() for m in re.finditer(r"^(#{1,3})\s+(.+)$", text, re.M)]
        out.append({
            "name": path.name,
            "rel": str(path.relative_to(ROOT)).replace("\\", "/"),
            "lines": text.count("\n") + 1,
            "headings": headings,
        })
    return out


def scan_presets() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for rel in ("presets/presets.yaml", "presets/system_presets.yaml"):
        with (ROOT / rel).open("r", encoding="utf-8") as handle:
            result[rel] = yaml.safe_load(handle) or {}
    return result


def svg_box(x: int, y: int, w: int, h: int, title: str, sub: str, cls: str = "b") -> str:
    return (
        f'<g class="{esc(cls)}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>'
        f'<text class="dt" x="{x + 14}" y="{y + 24}">{esc(title)}</text>'
        f'<text class="ds" x="{x + 14}" y="{y + 44}">{esc(sub)}</text></g>'
    )


def arrow(x1: int, y1: int, x2: int, y2: int, label: str = "") -> str:
    midx = (x1 + x2) // 2
    midy = (y1 + y2) // 2
    text = f'<text class="al" x="{midx}" y="{midy - 7}">{esc(label)}</text>' if label else ""
    return f'<path class="arr" marker-end="url(#arrow)" d="M{x1},{y1} L{x2},{y2}"/>' + text


def svg_wrap(body: str, width: int, height: int, label: str) -> str:
    defs = (
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>'
    )
    return (
        f'<figure class="diagram"><svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{esc(label)}">{defs}{body}</svg><figcaption>{esc(label)}</figcaption></figure>'
    )


def architecture_svg() -> str:
    boxes = [
        (20, 34, 128, 64, "Agent / CLI", "自然语言目标"),
        (178, 34, 128, 64, "MCP server", "34 个 sr_* 工具"),
        (336, 34, 128, 64, "Plan / Spec", "冻结配置与说明书"),
        (494, 34, 128, 64, "Generate", "ChannelHub / Sionna"),
        (652, 34, 128, 64, "Dataset", "h_true / h_est"),
        (810, 34, 128, 64, "Algorithms", "链路 / 系统仿真"),
        (968, 34, 128, 64, "Gates / KPI", "证据与结论"),
    ]
    body = "".join(svg_box(*b) for b in boxes)
    for a, b in zip(boxes, boxes[1:], strict=False):
        body += arrow(a[0] + a[2], 66, b[0], 66)
    body += svg_box(494, 140, 286, 64, "物理内核边界", "复用算法，不复用 ChannelHub 产品壳", "accent")
    body += arrow(636, 140, 636, 104, "标准化")
    body += svg_box(810, 140, 286, 64, "两条评估模式", "capacity / experience，不是精度档位", "good")
    body += arrow(952, 140, 952, 104, "显式 profile")
    return svg_wrap(body, 1120, 230, "SuperRAN 从 Agent 请求到可信结论的完整数据流")


def topology_svg() -> str:
    centers = [(320, 150), (500, 150), (590, 300), (500, 450), (320, 450), (230, 300), (410, 300)]
    body = '<g class="site-lines">'
    for i, (x, y) in enumerate(centers):
        body += f'<circle cx="{x}" cy="{y}" r="42" class="site"/><text class="site-t" x="{x}" y="{y + 5}">站{i}</text>'
        for angle in (-90, 30, 150):
            import math
            x2 = x + 66 * math.cos(math.radians(angle))
            y2 = y + 66 * math.sin(math.radians(angle))
            body += f'<line class="sector" x1="{x}" y1="{y}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
    body += '</g>'
    body += svg_box(720, 80, 330, 88, "同站三个扇区", "共享 site-level LSP / cluster birth-death", "good")
    body += svg_box(720, 214, 330, 88, "不同站", "独立传播状态；不能复制同一 realization", "danger")
    body += svg_box(720, 348, 330, 88, "仍可相关", "同一 UE 几何、场景统计、遮挡规则可相关", "accent")
    body += arrow(590, 190, 720, 124, "同站共享")
    body += arrow(590, 300, 720, 258, "跨站独立")
    return svg_wrap(body, 1080, 520, "7 站 21 小区传播状态拓扑：共享的是同站环境状态，不是复制信道矩阵")


def array_svg() -> str:
    body = ""
    x0, y0 = 70, 50
    for h in range(8):
        for v in range(12):
            for p, color in enumerate(("polp", "polm")):
                x = x0 + h * 56 + p * 16
                y = y0 + v * 31
                body += f'<circle class="ae {color}" cx="{x}" cy="{y}" r="5"/>'
    for h in range(8):
        for vrf in range(4):
            y = y0 + (3 * vrf + 1) * 31
            x = x0 + h * 56 + 8
            body += f'<rect class="feed" x="{x - 18}" y="{y - 40}" width="52" height="80" rx="8"/>'
    body += '<text class="dt" x="55" y="445">8H × 12V × 2pol = 192 physical AEs</text>'
    body += '<circle class="ae polp" cx="570" cy="86" r="7"/><text class="ds" x="586" y="91">+45°</text>'
    body += '<circle class="ae polm" cx="570" cy="118" r="7"/><text class="ds" x="586" y="123">−45°</text>'
    body += svg_box(550, 160, 390, 74, "一个 RF 端口", "固定驱动同列相邻 3 个 AE（1 驱 3）", "accent")
    body += svg_box(550, 258, 390, 74, "64 个 RF 端口", "8H × 4V × 2pol；端口间垂直 2.01λ", "good")
    body += svg_box(550, 356, 390, 74, "F: 192 × 64", "每列仅 3 个非零复馈电权，列范数 = 1", "b")
    body += arrow(510, 205, 550, 197)
    body += arrow(510, 285, 550, 295)
    body += arrow(510, 365, 550, 393)
    return svg_wrap(body, 980, 480, "公司 AAU 阵列拓扑：双极化、1 驱 3 与 192×64 耦合矩阵一一对应")


def array_256_svg() -> str:
    """Product-drawing port order plus the vertical 1-to-6 physical feed."""
    body = ""
    x0, y0 = 42, 50
    for h in range(16):
        for v in range(8):
            x = x0 + h * 34
            y = y0 + v * 35
            port = h * 8 + v + 1
            body += f'<circle class="ae polp" cx="{x}" cy="{y}" r="5"/>'
            if h in (0, 1, 15) or v in (0, 7):
                body += f'<text class="tiny" x="{x + 8}" y="{y + 3}">{port}</text>'
    body += '<text class="dt" x="42" y="360">极化块 1：端口 1…128；行从上到下，列从左到右</text>'
    body += '<text class="ds" x="42" y="385">r = p·128 + h·8 + v + 1（图中 1-based）</text>'
    body += svg_box(660, 48, 380, 76, "第二极化块", "同一 16H×8V 位置；端口 129…256", "accent")
    body += svg_box(660, 154, 380, 76, "每个 T 后的物理阵子", "垂直 1 驱 6；AE 间距 0.67λ", "good")
    body += svg_box(660, 260, 380, 76, "F: 1536 × 256", "每列 6 个非零；FᴴF=I₂₅₆", "b")
    body += arrow(585, 112, 660, 86, "pol block")
    body += arrow(585, 205, 660, 192, "1→6")
    body += arrow(585, 288, 660, 298, "coupling")
    return svg_wrap(body, 1080, 420, "公司 256T 图纸顺序：16H×8V×2pol 端口与 1 驱 6 的 1536×256 耦合")


def port_contract_svg() -> str:
    """Show that layout migration is a physical permutation, not a reshape."""
    body = svg_box(24, 22, 250, 64, "物理位置不变", "同一 (h, physical-v, p)", "good")
    body += svg_box(365, 22, 310, 64, "canonical · 新 64T/256T", "pol_h_v + top_to_bottom", "accent")
    body += svg_box(766, 22, 310, 64, "legacy · 仅旧 64T", "h_v_pol + bottom_to_top", "warn")
    y_rows = (132, 186, 240, 294)
    canonical = (1, 2, 3, 4)
    legacy = (7, 5, 3, 1)
    for row, y in enumerate(y_rows):
        body += f'<circle class="physical-dot" cx="146" cy="{y}" r="9"/>'
        body += f'<text class="tiny" x="146" y="{y + 28}">top+{row}</text>'
        body += f'<rect class="index-cell canonical" x="452" y="{y - 18}" width="136" height="36" rx="8"/>'
        body += f'<text class="index-text" x="520" y="{y + 5}">port {canonical[row]}</text>'
        body += f'<rect class="index-cell legacy" x="853" y="{y - 18}" width="136" height="36" rx="8"/>'
        body += f'<text class="index-text" x="921" y="{y + 5}">port {legacy[row]}</text>'
        body += arrow(158, y, 452, y, "same AE")
        body += arrow(588, y, 853, y, "P")
    body += '<text class="ds" x="146" y="350">示例：64T、第一水平列、第一极化；编号为 1-based</text>'
    body += '<text class="ds" x="720" y="386">H_new = P H_old，W_new = P W_old ⇒ W_newᴴH_new = W_oldᴴH_old</text>'
    return svg_wrap(
        body,
        1100,
        420,
        "64T 新旧端口合同的物理置换：必须同步重排 H、W、F，不能只改 shape 或元数据",
    )


def element_pattern_svg() -> str:
    """Formula-generated pattern cuts; explicitly illustrative, not measured data."""

    def point(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
        angle = math.radians(angle_deg)
        return cx + radius * math.cos(angle), cy - radius * math.sin(angle)

    def radial_path(
        cx: float,
        cy: float,
        values: list[tuple[float, float]],
        *,
        close_to_center: bool = False,
    ) -> str:
        coords = []
        for angle_deg, gain_db in values:
            radius = 18.0 + 102.0 * max(0.0, min(1.0, (gain_db + 30.0) / 30.0))
            coords.append(point(cx, cy, radius, angle_deg))
        start = f"M{cx:.1f},{cy:.1f} " if close_to_center else "M"
        return start + " ".join(
            f"{'L' if i or close_to_center else ''}{x:.1f},{y:.1f}"
            for i, (x, y) in enumerate(coords)
        ) + (" Z" if close_to_center else "")

    def horizontal_gain(phi_deg: float) -> float:
        return -min(12.0 * (phi_deg / 110.0) ** 2, 30.0)

    z = (-0.67, 0.0, 0.67)
    tilt = math.radians(6.0)
    weights = tuple(cmath.exp(1j * 2.0 * math.pi * zi * math.sin(tilt)) / math.sqrt(3.0) for zi in z)

    vertical_raw: list[tuple[float, float, float]] = []
    for elevation_deg in range(-90, 91):
        elevation = math.radians(elevation_deg)
        element_db = -min(12.0 * (elevation_deg / 65.0) ** 2, 30.0)
        response = sum(
            w.conjugate() * cmath.exp(-1j * 2.0 * math.pi * zi * math.sin(elevation))
            for w, zi in zip(weights, z, strict=True)
        )
        combined_db = element_db + 10.0 * math.log10(max(abs(response) ** 2, 1e-12))
        vertical_raw.append((float(elevation_deg), element_db, combined_db))
    peak_db = max(value[2] for value in vertical_raw)
    vertical_element = [(angle, element_db) for angle, element_db, _ in vertical_raw]
    vertical_port = [(angle, max(-30.0, combined_db - peak_db)) for angle, _, combined_db in vertical_raw]

    body = '<rect class="plot-panel" x="18" y="20" width="340" height="350" rx="14"/>'
    body += '<rect class="plot-panel" x="382" y="20" width="340" height="350" rx="14"/>'
    body += '<rect class="plot-panel" x="746" y="20" width="340" height="350" rx="14"/>'
    for cx in (188, 552):
        for radius, label in ((120, "0 dB"), (86, "−10"), (52, "−20"), (18, "−30")):
            body += f'<circle class="pattern-grid" cx="{cx}" cy="202" r="{radius}"/>'
            body += f'<text class="pattern-tick" x="{cx + radius - 4}" y="198">{label}</text>'
        body += f'<line class="pattern-axis" x1="{cx - 132}" y1="202" x2="{cx + 132}" y2="202"/>'
        body += f'<line class="pattern-axis" x1="{cx}" y1="70" x2="{cx}" y2="334"/>'

    horizontal = [(float(phi), horizontal_gain(float(phi))) for phi in range(-180, 181, 2)]
    body += f'<path class="pattern-lobe horizontal" d="{radial_path(188, 202, horizontal)}"/>'
    for angle, label in ((55.0, "+55°"), (-55.0, "−55°")):
        x, y = point(188, 202, 114, angle)
        body += f'<line class="hpbw" x1="188" y1="202" x2="{x:.1f}" y2="{y:.1f}"/>'
        body += f'<text class="pattern-note" x="{x:.1f}" y="{y - 5:.1f}">{label}</text>'
    body += '<text class="dt" x="188" y="48" text-anchor="middle">水平元素切面 · 110° HPBW</text>'
    body += '<text class="ds" x="188" y="354">±55° 处为 −3 dB；后向受 30 dB floor 截断</text>'

    body += f'<path class="pattern-lobe element" d="{radial_path(552, 202, vertical_element, close_to_center=True)}"/>'
    body += f'<path class="pattern-lobe port" d="{radial_path(552, 202, vertical_port, close_to_center=True)}"/>'
    down_x, down_y = point(552, 202, 130, -6.0)
    body += f'<line class="tilt-ray" x1="552" y1="202" x2="{down_x:.1f}" y2="{down_y:.1f}"/>'
    body += f'<text class="pattern-note" x="{down_x - 28:.1f}" y="{down_y + 18:.1f}">约 −6°</text>'
    body += '<text class="dt" x="552" y="48" text-anchor="middle">垂直切面 · 元素 × 1驱3</text>'
    body += '<line class="legend element" x1="430" y1="349" x2="465" y2="349"/><text class="pattern-note" x="472" y="353">65° 元素</text>'
    body += '<line class="legend port" x1="565" y1="349" x2="600" y2="349"/><text class="pattern-note" x="607" y="353">有效端口</text>'

    body += svg_box(776, 58, 280, 60, "① dBi → 场幅", "gE = 10^(GE/20)，不是 /10", "accent")
    body += svg_box(776, 140, 280, 60, "② ±45° Jones", "标量包络 × 极化方向向量", "b")
    body += svg_box(776, 222, 280, 60, "③ 固定子阵因子", "wq 相干叠加；产生下倾/栅瓣", "good")
    body += svg_box(776, 304, 280, 60, "④ 射线与数字 BF", "进入 Jℓ、H；W 在端口域另算", "warn")
    body += arrow(916, 118, 916, 140)
    body += arrow(916, 200, 916, 222)
    body += arrow(916, 282, 916, 304)
    return svg_wrap(
        body,
        1105,
        395,
        "阵元方向图示意：曲线由当前参数公式生成并归一化，不是公司实测方向图",
    )


def ray_construction_svg() -> str:
    items = (
        (20, "元素方向图", "gE(φ,ε)"),
        (202, "极化基", "f±45"),
        (384, "路径耦合", "2×2 Jℓ / XPR"),
        (566, "子阵/steering", "FᴴaAE"),
        (748, "时延与 Doppler", "τℓ / νℓ"),
        (930, "MIMO H(t,f)", "逐 ray/cluster 求和"),
    )
    body = ""
    for i, (x, title, sub) in enumerate(items):
        body += svg_box(x, 52, 158, 72, title, sub, "accent" if i in (0, 5) else "b")
        if i:
            body += arrow(items[i - 1][0] + 158, 88, x, 88)
    body += '<text class="ds" x="550" y="174">方向图改的是每条 ray 的复场系数；随后才由数字预编码 W 决定多端口合成与用户间干扰</text>'
    return svg_wrap(body, 1110, 205, "从阵元方向图到每个 RB 的 MIMO 信道系数")


def srs_matrix_svg() -> str:
    body = svg_box(25, 38, 185, 80, "UE 4Tx", "4 路正交 SRS 端口", "accent")
    body += svg_box(280, 38, 210, 80, "空口", "ZC/短序列 + comb + hopping", "b")
    body += svg_box(560, 38, 210, 80, "gNB 64Rx", "每端口观测 4 个 UE 端口", "good")
    body += svg_box(840, 38, 230, 80, "ĤSRS[64,4]", "LS → 频时插值 / LMMSE", "accent")
    body += arrow(210, 78, 280, 78, "X[4]")
    body += arrow(490, 78, 560, 78, "Y[64]")
    body += arrow(770, 78, 840, 78, "估计")
    body += '<text class="ds" x="550" y="142">目标物理链路；当前 serving pilot 仍固定 N_ap=1，观测按 BS×UE 系数广播</text>'
    colors = ["#2563eb", "#0f766e", "#7c3aed", "#c2410c", "#64748b"]
    order = [0, 8, 16, 7, 15, 6, 14, 5, 13, 4, 12, 3, 11, 2, 10, 1, 9]
    for i, value in enumerate(order):
        x = 30 + i * 61
        body += f'<rect x="{x}" y="190" width="52" height="48" rx="8" fill="{colors[i % len(colors)]}" opacity=".9"/>'
        body += f'<text class="slot" x="{x + 26}" y="220">{value}</text>'
        body += f'<text class="tiny" x="{x + 26}" y="256">{i * 10} ms</text>'
    body += '<text class="dt" x="30" y="166">17-hop order · C_SRS=63 / B_SRS=1 · 16 RB per hop</text>'
    body += '<path class="brace" d="M30,278 L30,292 L1058,292 L1058,278"/>'
    body += '<text class="ds" x="544" y="318">10 ms SRS 周期时，扫完整带约 170 ms；再加处理时延</text>'
    return svg_wrap(body, 1100, 350, "目标 64×4 SRS 物理链路与当前 17 RBG 跳频时间线")


def power_constraints_svg() -> str:
    vals = {
        "EBF": [0.18, 0.07, 0.12, 0.03, 0.22, 0.11, 0.16, 0.11],
        "PEBF": [0.10, 0.04, 0.07, 0.02, 0.125, 0.06, 0.09, 0.06],
        "NEBF": [0.125] * 8,
    }
    body = '<line class="cap" x1="70" y1="106" x2="1000" y2="106"/><text class="tiny left" x="1005" y="110">P/M</text>'
    for group, (name, arr) in enumerate(vals.items()):
        gx = 75 + group * 330
        body += f'<text class="dt" x="{gx + 120}" y="36">{name}</text>'
        for i, value in enumerate(arr):
            h = value / 0.24 * 150
            x = gx + i * 34
            y = 260 - h
            cls = "bar bad" if value > 0.1250001 else "bar"
            body += f'<rect class="{cls}" x="{x}" y="{y:.1f}" width="23" height="{h:.1f}" rx="3"/>'
        note = {"EBF": "总功率满；个别天线可超 P/M", "PEBF": "整体缩放；正交性保留但功率未用满", "NEBF": "逐天线拉满；可能破坏 MU 零陷"}[name]
        body += f'<text class="ds" x="{gx + 120}" y="292">{esc(note)}</text>'
    return svg_wrap(body, 1080, 325, "EBF、PEBF、NEBF 的每天线功率分布 toy example（8 天线示意）")


def link_flow_svg() -> str:
    items = [
        (25, "CQI", "长期宽带"), (175, "Γ(MCS(CQI))", "CQI 门限反映射"),
        (365, "BF Gain", "SVD − PMI"), (525, "SU OLLA", "用户级闭环"),
        (680, "MCS", "发送侧选择"), (820, "真实 SINR", "当前 h_true"),
        (975, "BLER", "查表判错"),
    ]
    body = ""
    boxes = []
    for x, t, s in items:
        w = 130 if x != 175 else 160
        boxes.append((x, 52, w, 68, t, s))
        body += svg_box(x, 52, w, 68, t, s, "accent" if t in ("CQI", "MCS") else "b")
    for a, b in zip(boxes[:5], boxes[1:5], strict=False):
        body += arrow(a[0] + a[2], 86, b[0], 86, "+")
    body += arrow(810, 86, 820, 86)
    body += arrow(950, 86, 975, 86, "curve")
    body += svg_box(365, 185, 180, 70, "CorrLoss", "MU 残留相关干扰", "danger")
    body += svg_box(575, 185, 180, 70, "PowerLoss", "−10log10(KMU)", "danger")
    body += svg_box(785, 185, 180, 70, "MU OLLA", "用户级、非 pair 级", "warn")
    body += arrow(455, 185, 610, 120, "+")
    body += arrow(665, 185, 660, 120, "+")
    body += arrow(875, 185, 710, 120, "+")
    return svg_wrap(body, 1140, 285, "CQI + BF + OLLA 到 MCS；MU 在 SU 链上再加 CorrLoss、PowerLoss 与 MU OLLA")


def mu_decision_svg() -> str:
    body = svg_box(25, 25, 220, 72, "PF 排序一次", "得到 anchor → candidates", "accent")
    body += svg_box(315, 25, 220, 72, "构造 SU plan", "按序给最小够用 RBG", "b")
    body += svg_box(605, 25, 220, 72, "构造 MU plan", "真实 pair 表；2UE×rank2", "b")
    body += svg_box(895, 25, 220, 72, "比较 useful bytes", "超出队列的 padding 不计", "good")
    body += arrow(245, 61, 315, 61)
    body += arrow(535, 61, 605, 61)
    body += arrow(825, 61, 895, 61)
    body += svg_box(260, 165, 250, 78, "SU 清空全部队列？", "是 → 强制 SU，剩余 RBG 留空", "good")
    body += svg_box(605, 165, 250, 78, "否则 MU ≥ SU？", "是 → MU；否 → SU", "accent")
    body += arrow(1005, 97, 385, 165, "先判断")
    body += arrow(510, 204, 605, 204, "否")
    body += '<text class="yes" x="386" y="267">是 → SU</text><text class="yes" x="730" y="267">是 → MU；否 → SU</text>'
    return svg_wrap(body, 1140, 295, "experience_v2 每个 DL TTI 的 SU/MU 自适应决策")


def phases_svg() -> str:
    body = svg_box(35, 36, 485, 82, "Phase A · 链路预计算", "H → CSI → rank/MCS/SINR；MU pair tables", "accent")
    body += svg_box(620, 36, 485, 82, "Phase B · TTI 主循环", "traffic → PF → plan → grant → BLER → KPI", "good")
    body += arrow(520, 77, 620, 77, "纯查表")
    for i, (title, sub) in enumerate((
        ("UE/Snapshot/Rank", "SVD/PMI/BF gain"), ("SU table", "best_rank / SINR / MCS"),
        ("MU pair table", "CorrLoss / true SINR"), ("RB-PC table", "逐 RBG/RB 可选"),
    )):
        body += svg_box(35 + i * 270, 180, 235, 66, title, sub, "b")
        if i:
            body += arrow(35 + (i - 1) * 270 + 235, 213, 35 + i * 270, 213)
    body += '<text class="ds" x="570" y="286">主循环禁止重复矩阵分解；legacy_v1 标量 MU 与 experience_v2 pair 表必须分开解释</text>'
    return svg_wrap(body, 1140, 315, "系统仿真的两相架构")


def traffic_kpi_svg() -> str:
    heights = [150, 112, 54, 34, 28, 22, 18, 16, 18, 20, 22, 27, 33, 45, 62, 94, 142, 172]
    body = '<line class="axis" x1="60" y1="260" x2="680" y2="260"/><line class="axis" x1="60" y1="40" x2="60" y2="260"/>'
    for i, h in enumerate(heights):
        x = 70 + i * 33
        body += f'<rect class="hist" x="{x}" y="{260-h}" width="24" height="{h}" rx="3"/>'
        if i in (0, 1, 5, 9, 13, 17):
            body += f'<text class="tiny" x="{x + 12}" y="280">{i}</text>'
    body += '<text class="dt" x="280" y="310">每个 TTI 的占用 RBG 数（0..17）</text>'
    body += svg_box(760, 48, 310, 64, "首包时延", "arrival → first scheduled", "accent")
    body += svg_box(760, 142, 310, 64, "含头速率", "分母额外包含首包等待", "good")
    body += svg_box(760, 236, 310, 64, "MU 配对比例", "MU PRB / 已用 PRB", "warn")
    body += arrow(680, 130, 760, 80)
    body += arrow(680, 180, 760, 174)
    body += arrow(680, 230, 760, 268)
    return svg_wrap(body, 1120, 340, "mixed 话务常见的两头高 RBG 占用分布及三个关键体验 KPI")


def rb_power_svg() -> str:
    body = svg_box(35, 45, 240, 72, "Cell A · RBG0 ↑", "qA,0 增大；总功率守恒", "accent")
    body += svg_box(35, 175, 240, 72, "Cell A · 其他 RBG ↓", "qA,r 被迫降低", "warn")
    body += svg_box(440, 25, 255, 72, "UE A on RBG0", "服务信号增强", "good")
    body += svg_box(440, 125, 255, 72, "邻区 UE on RBG0", "来自 Cell A 的干扰增强", "danger")
    body += svg_box(440, 225, 255, 72, "UE A on other RBG", "服务信号减弱", "danger")
    body += svg_box(850, 125, 250, 72, "系统结果", "取决于调度/干扰/频选联合", "accent")
    body += arrow(275, 81, 440, 61, "+S")
    body += arrow(275, 81, 440, 161, "+I")
    body += arrow(275, 211, 440, 261, "−S")
    body += arrow(695, 61, 850, 150)
    body += arrow(695, 161, 850, 161)
    body += arrow(695, 261, 850, 172)
    return svg_wrap(body, 1140, 330, "为什么抬升 RBG0、压低其他 RBG 可能让整体性能下降")


def gates_svg() -> str:
    body = svg_box(35, 55, 255, 84, "门 1 · 数据体检", "18 checks；物理/合同/样本", "accent")
    body += svg_box(355, 55, 255, 84, "门 2 · 结果可信", "paired/clustered CI；CRN", "good")
    body += svg_box(675, 55, 255, 84, "门 3 · 可发布", "预注册、效应量、边界", "warn")
    body += arrow(290, 97, 355, 97, "PASS")
    body += arrow(610, 97, 675, 97, "PASS")
    body += svg_box(355, 205, 255, 70, "BLOCK", "不写提升百分比；补数据/修配置", "danger")
    body += arrow(482, 139, 482, 205, "FAIL")
    body += svg_box(675, 205, 255, 70, "LIMITED", "只写观察值与适用边界", "b")
    body += arrow(802, 139, 802, 205, "证据不足")
    return svg_wrap(body, 970, 305, "三道门把“能运行”与“能下结论”分开")


def skill_flow_svg() -> str:
    labels = [
        (25, "1 头脑风暴", "问题/基线/主指标"), (300, "2 计划书", "四项可见计划"),
        (575, "3 生成 + 门1", "数据先体检"), (850, "4 实验 + 门2/3", "证据后结论"),
    ]
    body = ""
    for i, (x, title, sub) in enumerate(labels):
        body += svg_box(x, 55, 230, 78, title, sub, "accent" if i == 0 else "b")
        if i:
            body += arrow(labels[i - 1][0] + 230, 94, x, 94)
    body += '<text class="ds" x="550" y="190">HARD-GATE：未通过时不能用“趋势上/总体来看”绕过，也不能手算救结论</text>'
    return svg_wrap(body, 1110, 225, "channel-sim Skill 的强制收敛与证据工作流")


def overview_page(modules: list[ModuleDoc], tools: list[SymbolDoc], tests: list[dict[str, Any]],
                  skills: list[dict[str, Any]]) -> Page:
    source_lines = sum(m.lines for m in modules)
    top_symbols = sum(len(m.symbols) for m in modules)
    nested_members = sum(len(s.members) for m in modules for s in m.symbols)
    test_lines = sum(t["lines"] for t in tests)
    body = metric_cards((
        ("源码模块", str(len(modules)), f"{source_lines:,} 行 Python"),
        ("公开顶层 API", str(top_symbols), f"另含 {nested_members} 个公开成员/字段"),
        ("MCP 工具", str(len(tools)), "由 server.py AST 实时提取"),
        ("测试文件", str(len(tests)), f"{test_lines:,} 行可执行检查"),
        ("Skill 文档", str(len(skills)), "1 主文件 + references"),
    ))
    body += architecture_svg()
    body += """
<h2>一句话定位</h2>
<p><strong>superran 是给 Agent 使用的无线仿真实验编排与证据平台。</strong>
它把 ChannelHub/Sionna 等物理内核包装成稳定的数据合同、MCP 工具、系统仿真和三道证据门。
它的目标不是“能画一条曲线”，而是让配置、真值、估计、随机数、统计和结论都能回溯。</p>
"""
    body += callout(
        "warn", "最重要的边界",
        "<p><code>capacity / legacy_v1</code> 与 <code>experience / experience_v2</code> "
        "是两种评估模式，不是同一算法的精度开关。前者复现全带调度历史行为；后者实现 FIFO、"
        "按需 RBG、真实 MU pair、PF 实际 TBS 记账和体验 KPI。跨模式比较必须把语义差异写出来。</p>",
    )
    body += """
<h2>推荐阅读路径</h2>
<div class="paths-grid">
  <a href="#/quickstart"><b>第一次运行</b><span>安装 → 首个 MCP 调用 → 生成与 Gate 1</span></a>
  <a href="#/antenna"><b>复核 64T 物理</b><span>双极化 → 1 驱 3 → F(192×64) → 端口信道</span></a>
  <a href="#/experience"><b>实现体验速率</b><span>Phase A/B → traffic → PF → RBG → KPI</span></a>
  <a href="#/extension"><b>扩展算法</b><span>接口边界 → 不变量 → 测试 → MCP/Skill 文档同步</span></a>
</div>
<h2>本页如何保持可信</h2>
<p>页面中的易漂移数字由生成器直接扫描当前 AST 和文件系统。算法解释旁的
<span class="src">源码</span>链接定位到实现；“当前边界”与“推荐演进”分开写。
历史 HTML 仍保留，但它们是某次审计快照，不再承担当前 API 真相源。</p>
"""
    return Page(
        "overview", "项目总览", "开始", "SUPERRAN DEVELOPER GUIDE",
        "从物理信道到系统 KPI，再到可信结论的一张全景地图。", body,
        ("MCP", "ChannelHub", "系统仿真", "开发者"),
    )


def quickstart_page() -> Page:
    install = r"""
cd C:\Vibe\Wireless\SuperRAN
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .

# 可选：射线追踪能力（约 300 MB 依赖）
python -m pip install -e ".[rt]"

# stdio MCP 服务；也可用安装后的 superran-mcp
python -m superran.server
"""
    mcp = r'''{
  "mcpServers": {
    "superran": {
      "command": "C:\\Vibe\\Wireless\\superran\\.venv\\Scripts\\python.exe",
      "args": ["-m", "superran.server"],
      "env": {
        "SUPERRAN_CHANNELHUB": "C:\\Vibe\\Wireless\\MSG-Platform"
      }
    }
  }
}'''
    first = r'''# Agent 侧的逻辑顺序（实际由 MCP tool call 完成）
caps = sr_capabilities()
draft = sr_plan(intent="验证 64T4R 下 SRS 权与 Type-I 权的谱效差异")
data = sr_generate(preset="company_64t4r_multicell", num_samples=64)
gate = sr_gate(dataset_id=data["dataset_id"])
result = sr_link_performance(dataset_id=data["dataset_id"], method="svd")
'''
    body = """
<h2>环境与安装</h2>
<p>最低 Python 版本是 3.10。基础包只要求 NumPy/SciPy/Pydantic/PyYAML/structlog/MCP；
Sionna RT 是显式可选依赖。ChannelHub 源码默认在项目周边自动发现，也可用
<code>SUPERRAN_CHANNELHUB</code> 指定。</p>
""" + code(install, "PowerShell")
    body += callout(
        "note", "Windows 路径注意",
        "<p>不要照抄 ChannelHub 旧文档中的某台机器虚拟环境路径。MCP 配置应写当前机器的"
        "绝对 Python 路径；项目产物默认落在 <code>artifacts/</code>，可由环境变量整体迁移。</p>",
    )
    body += "<h2>把服务接给 Agent</h2>" + code(mcp, "json")
    body += """
<p>不同 Agent 宿主的配置外壳略有差异，但核心永远是 stdio 启动命令。服务端不主动执行
外部自研代码；外部算法通过结果契约注册逐样本值，再进入相同的门 2/门 3。</p>
<h2>第一个可信实验</h2>
""" + code(first)
    body += steps((
        ("先发现能力", "<p>确认 internal_sim、Sionna RT、ChannelHub、预设和观察量是否真的可用。</p>"),
        ("再冻结问题", "<p>先定基线、主指标、CSI 公平性和样本单位；不要先生成再补故事。</p>"),
        ("生成并过门 1", "<p>数据合同、路径损耗、干扰、形状、收敛等 18 项检查未通过就停止。</p>"),
        ("实验后过门 2/3", "<p>用同一样本配对比较；统计不足时只报告观察值，不写“提升 X%”。</p>"),
    ))
    body += """
<h2>开发内环与发布外环</h2>
<div class="compare"><div><h3>秒级开发内环</h3><p>AST/合同测试、纯 NumPy toy example、文档结构和公式检查。
适合每次编辑后执行。</p></div><div><h3>真实物理外环</h3><p>ChannelHub/Sionna 生成、多小区干扰、蒙特卡洛与浏览器 QA。
适合算法改动和发布前执行，可能需要数分钟到数小时。</p></div></div>
"""
    return Page(
        "quickstart", "安装与第一次实验", "开始", "GETTING STARTED",
        "从空环境到第一个通过 Gate 1 的数据集。", body,
        ("安装", "MCP", "PowerShell", "Gate 1"),
    )


def architecture_page() -> Page:
    body = architecture_svg()
    body += """
<h2>五层，而不是一个大脚本</h2>
"""
    body += table(
        ["层", "职责", "主要模块", "禁止越界"],
        [
            ("编排层", "自然语言意图、分轮决策、预设、说明书", "server / plan / decisions / spec / sysscenes", "不能偷偷替用户改变实验问题"),
            ("数据层", "生成、落盘、加载、观察量", "generate / channelhub / loader / measure / scenes", "h_est 缺失时禁止复制 h_true"),
            ("算法层", "预编码、接收、MCS、MU、功控", "beamforming / linklevel / linkadapt / mumimo / power_control", "设计 CSI 与评估真值必须分离"),
            ("系统层", "连续 TTI、话务、PF、FIFO、KPI", "system / experience / traffic / kpi_view / rng", "capacity 与 experience 不混口径"),
            ("证据层", "校准、Gate、预注册、结果合同", "validate / calibration / gates / analysis / results", "Gate 不通过不得发布强结论"),
        ],
    )
    body += """
<h2>ChannelHub 边界</h2>
<p>superran 复用 ChannelHub 的物理算法与数据源，但不复用其产品壳。适配层负责发现源码、
注入核实过的 38.901 表、预热依赖、取出 serving/interference/估计信道，并把不稳定的内部对象
压成项目自己的数据合同。</p>
<p>物理代码根与射线追踪资产根允许分离：当前可从相邻 <code>MSG-Platform</code> 加载最新
<code>src/msg_embedding</code>，同时从完整 ChannelHub checkout 读取 <code>configs/scenes</code>。
用户可用 <code>SUPERRAN_CHANNELHUB</code> 指定代码根、用 <code>SUPERRAN_SCENES</code>
独立指定场景目录；资产回退绝不会把 Python 物理实现一起退回旧版本。</p>
"""
    body += callout(
        "danger", "为什么不直接透传 ChannelHub bridge",
        "<p>产品桥可能做归一化、截断或门控，物理量会在不显眼处改变。"
        "SuperRAN 只从明确的数据源/算法入口取值，并在落盘前校验形状、有限性和角色。</p>",
    )
    body += """
<h2>h_true 与 h_est 是架构轴</h2>
""" + F_DATASET
    body += """
<p><code>h_true</code> 是下行物理评估信道；<code>h_est</code> 是 gNB 设计预编码时可见的 CSI。
在 <code>link=BOTH</code> 的 TDD 数据里，后者来自上行 SRS，而不是下行 CSI-RS。
二者同形不代表同值；若估计源缺失，生成器硬失败。</p>
<h2>数据流中的三个时间尺度</h2>
"""
    body += table(
        ["尺度", "当前对象", "典型用途", "常见混淆"],
        [
            ("OFDM symbol", "ChannelHub 内部可生成 14-symbol slot", "TDD 导频/估计、符号选择性", "系统层不需要每 TTI 重放 14 个 symbol"),
            ("channel snapshot", "系统链路表的物理快照", "CSI 老化、PMI/CQI 更新、真实 SINR", "快照周期不是 SRS 周期也不是 PMI 周期"),
            ("TTI", "Phase B 队列与调度步", "到达、PF、grant、BLER、OLLA、KPI", "一个 TTI 只需引用一个 snapshot"),
        ],
    )
    body += callout(
        "good", "复杂度选择",
        "<p>物理数据源可保留 1~14 个 symbol 来做导频映射、估计和 Doppler 演化；"
        "superran 的窄腰适配器随后取中间 symbol，保留长度为 1 的时间轴作为 slot snapshot。"
        "绝不能对复信道跨 symbol 求均值，否则旋转相位会相消并制造假衰落。Phase B 每 TTI 只查一个 "
        "snapshot。这是典型的链路到系统抽象，且显著降低复杂度。"
        "若研究 symbol-level mini-slot/DMRS，则必须显式扩展系统状态，不能假装现有 TTI 表已覆盖。</p>",
    )
    body += "<p class=source-row>实现入口：" + source_ref("src/superran/generate.py", '"h_true_role"') + " · " + source_ref("src/superran/channelhub.py", "def serving_channel") + " · " + source_ref("src/superran/system.py", "def build_link_tables") + "</p>"
    return Page(
        "architecture", "架构与数据合同", "开始", "ARCHITECTURE",
        "五层职责、ChannelHub 边界、h_true/h_est 与三个时间尺度。", body,
        ("h_true", "h_est", "ChannelHub", "Phase A", "Phase B"),
    )


def hardware_page() -> Page:
    body = metric_cards((
        ("RF 端口", "64", "8H × 4V × 2pol"),
        ("物理阵子", "192", "8H × 12V × 2pol"),
        ("终端", "4Tx / 4Rx", "BOTH：UL SRS + DL 真值"),
        ("载波", "2.6 GHz", "n41 · 100 MHz · 30 kHz"),
        ("频域", "272 RB", "17 RBG × 16 RB"),
    ))
    body += array_svg()
    body += array_256_svg()
    body += """
<h2>两套已确认的公司阵列合同</h2>
"""
    body += table(
        ["profile", "RF 端口", "物理 AE / 馈电", "端口顺序", "垂直编号"],
        [
            ("64T 基线", "8H×4V×2pol = 64", "8H×12V×2pol = 192；1 驱 3", "pol_h_v", "top_to_bottom"),
            ("公司 256T", "16H×8V×2pol = 256", "16H×48V×2pol = 1536；1 驱 6", "pol_h_v", "top_to_bottom"),
        ],
    )
    body += callout(
        "good", "64T/256T 统一按产品图规则锁定",
        "<p>两者都采用 <code>r=p·N_H·N_V+h·N_V+v</code>（0-based），"
        "即先极化块、再水平列、垂直行最快，且 v=0 是物理顶部。64T 的关键端口是"
        " 1/5/33，256T 是 1/9/129。该顺序已同时贯通 InternalSim、Sionna RT、"
        "QuaDRiGa、Type-I/DFT 码本与系统链路表；历史 64T 顺序只经显式置换读取。</p>",
    )
    body += """
<h2>272 不是标准表写错</h2>
<p>38.104 在 100 MHz / 30 kHz 下给 273 RB；项目显式取 272，是为了得到完整的
17×16 RBG，丢弃最后一个残块 RB。凡是调度/TBS/RBG 统计都用 272；凡是解释标准表时
同时标出 273，不能混写。SRS 仍能按标准表覆盖这 272 RB：<code>C_SRS=63</code>、
<code>B_SRS=1</code> 时顶层带宽 272 RB、单次 16 RB、17 次完整轮转。</p>
<p>标准反查还必须知道频率范围：50 MHz / 60 kHz 在 FR1 是 65 RB，在 FR2 是 66 RB；
100 MHz / 60 kHz 则分别是 135 与 132 RB。因此实现保留两张独立表，默认公司 n41 场景用
<code>FR1</code>，载频不低于 24.25 GHz 的物理后端显式选择 <code>FR2</code>。非标准带宽不会
再用除法猜一个 RB 数；合成频域网格必须显式给 <code>num_rb</code>。</p>
<h2>默认值如何生效</h2>
"""
    body += steps((
        ("carrier defaults", "<p>补 2.6 GHz、30 kHz、100 MHz、272 RB、64T/4T4R 和 BOTH。</p>"),
        ("panel guard", "<p><code>[8,4,2]</code> 挂载 64T/1 驱 3；<code>[16,8,2]</code> 挂载 256T/1 驱 6。其它面板不猜馈电结构。</p>"),
        ("metadata", "<p>summary 记录模式、阵子数、间距、极化、方向图来源、calibration_id 与端口布局合同版本。</p>"),
        ("Gate", "<p>端口数、BOTH 上下行 UE 端口、方向图真实性和默认值漂移均有检查。</p>"),
    ))
    body += callout(
        "warn", "UE 面板仍是工程假设",
        "<p><code>2H×1V×2pol</code> 的 4R UE 不是公司实测手机天线。它可配置，文档和结果都不能称为硬件真值。</p>",
    )
    body += """
<h2>6° 电下倾从哪来</h2>
<p>当前 <code>fixed_downtilt_deg=6.0</code> 是公司 AAU 配置块的默认工程基线，不是由场景几何
反推出来的自然常数。它进入每个固定垂直子阵（64T 的 1 驱 3、256T 的 1 驱 6）内部相位递进；用户可以通过
<code>bs_antenna.fixed_vertical_subarray.fixed_downtilt_deg</code> 任意覆盖。改变它等价于改变馈电
校准，应同时改变/记录 <code>calibration_id</code>，并用垂直波束峰值与 F 矩阵列范数回归。</p>
""" + F_FEED
    body += "<p class=source-row>唯一默认真相源：" + source_ref("src/superran/hardware.py", "def company_antenna_block") + "</p>"
    return Page(
        "hardware", "默认硬件与载波", "物理内核", "HARDWARE BASELINE",
        "64T 基线与公司 256T 可选阵列、272 RB 和 6° 电下倾的来源、作用与覆盖方式。", body,
        ("64T4R", "256T", "1驱6", "272 RB", "n41", "downtilt"),
    )


def channel_page() -> Page:
    body = topology_svg() + ray_construction_svg()
    body += """
<h2>信道矩阵如何建模</h2>
<p>每条 path/cluster 把功率、时延、多普勒、收发角、阵列响应和极化 Jones 耦合成复数 MIMO
系数；频域相位由时延决定，时间相位由多普勒决定。CDL 有逐径角度，可显式形成阵列方向；
TDL 只有功率时延轮廓，空间相关是统计近似。</p>
""" + F_RAY_POLARIZATION + F_CHANNEL + F_CHANNEL_SHAPE
    body += """
<p>落盘时项目把链路方向整理为 <code>[T,RB,BS_ant,UE_ant]</code>，因此默认下行单个频点可看作
64×4（发射端优先）存储约定；通信教材常写成 4×64。矩阵乘法时必须先看函数约定，不能凭
“64×4”猜转置。</p>
<h2>CDL-A~E 现在展开到什么粒度</h2>
<p>五张 profile 的 delay、power、AOD/AOA/ZOD/ZOA、每簇角扩展和 XPR 均与
38.901 Table 7.7.1-1~5 逐字段交叉核对。每个 diffuse table component 按 Table 7.5-3
展开成 20 条 ray：四组角度 offset 会独立随机耦合，每条 ray 有自己的交叉极化矩阵、初相和
Doppler。profile 中心角再整体旋到实际 BS→UE 几何；到达方位是反向 bearing，不是把 AOD 原样复制。</p>
"""
    body += table(
        ["profile", "表分量", "实际 ray 项", "关键口径"],
        [
            ("CDL-A", "23", "23×20 = 460", "NLOS；cASD/cASA/cZSD/cZSA/XPR 全进入"),
            ("CDL-B", "23", "23×20 = 460", "NLOS；同上"),
            ("CDL-C", "24", "24×20 = 480", "NLOS；不是旧实现的 23 行"),
            ("CDL-D", "14", "1 + 13×20 = 261", "row 0 为确定性镜面项；K=13.3 dB 已在 row 0/1 功率差中"),
            ("CDL-E", "15", "1 + 14×20 = 281", "row 0 为确定性镜面项；K=22 dB 已在 row 0/1 功率差中"),
        ],
    )
    body += callout(
        "danger", "CDL-D/E 的 K 因子只能算一次",
        "<p>标准表已经把镜面项与 diffuse 首项的 K 比例写进功率列。若生成器再做一次 Rician "
        "<code>sqrt(K/(K+1))</code> 混合，会人为把 LOS 变得过强。回归测试会把 profile 的 "
        "<code>k_factor_dB</code> 元数据改成 100 dB，并要求同 seed 输出逐位不变。</p>",
    )
    body += callout(
        "note", "configured profile 不一定是 effective profile",
        "<p><code>configured_channel_model</code> 是用户入口；逐链路 LOS/NLOS 状态若与它不兼容，"
        "生成器会切到同家族的兼容剖面，并把真正在每个样本使用的名称写进 "
        "<code>effective_channel_model(s)</code>。例如 NLOS realization 配置 CDL-D 时实际会用 "
        "CDL-C。摘要、repr 和体检必须同时展示两者，不能只报配置名。</p>",
    )
    body += """
<h3>TDL 的边界</h3>
<p>TDL 没有 CDL 那套逐簇中心角表，因此不能伪造 20-ray 几何。当前实现保留标准 PDP，使用
空间相关投影形成多天线统计结构；LOS 分量使用实际 AOD/AOA/ZOD/ZOA 和同一套 Jones/阵列响应。
它适合快速统计回退，但不能替代场景确定性 ray tracing。</p>
<h2>极化在 H 中不是“天线数乘 2”</h2>
<p>每个路径带 2×2 极化耦合/XPR，收发阵元有各自的复 Jones 向量。+45°/-45° 只是局部极化
基；经过路径 Jones 矩阵后会发生共极化与交叉极化耦合。只有把该耦合与空间 steering 一起
代入，双极化才真正影响相关性、秩和 MU 干扰。</p>
""" + F_JONES
    body += callout(
        "note", "Jones/XPR 的当前精确边界",
        "<p>公司 <code>effective_subarray / physical_reference</code> 的 InternalSim CDL 路径已经调用 "
        "<code>element_jones()</code>，将理想 ±45° 基与逐 ray 的 2×2 <code>Jℓ</code> 收缩；legacy 面板仍按 "
        "V/H 基兼容。<code>element_xpd_db=8</code> 不是公司实测方向相关 XPD：CDL 优先使用 profile "
        "自带 XPR，它只在无 profile 值或统计回退路径中生效。方向相关复 Jones/XPD 仍需公司实测表。</p>",
    )
    body += """
<h2>同站共享、异站不复制</h2>
<p>这不是个人偏好：3GPP TR 38.901 §7.5 明确要求不同 BS–UT 链路的 LSP 不相关，且同址 BS
扇区的 LSP 要相关；§7.6.3.3 再给出了多链路 LSP 相关过程。同一 site 的扇区因此共享站点级
大尺度环境状态，但各扇区仍因方位图、端口和链路角度得到不同复信道；不同 site 不能复制
同一份 realization。规范原文可在 <a href="https://www.etsi.org/deliver/etsi_tr/138900_138999/138901/19.03.00_60/tr_138901v190300p.pdf" target="_blank" rel="noreferrer">ETSI TR 138 901 V19.3.0</a> 复核。</p>
"""
    body += callout(
        "warn", "共享不等于相同",
        "<p>同站扇区可共享 LSP/cluster state，但波束方位、路径相位、端口响应和服务/干扰角色仍不同；"
        "不同站即便属于同一 UMa 统计分布，也要使用独立随机流。当前项目要用不变量验证，而不能只在"
        "文档里宣称。</p>",
    )
    body += """
<h2>ChannelHub、Sionna RT 与系统层各做什么</h2>
"""
    body += table(
        ["来源", "擅长", "项目中的角色", "边界"],
        [
            ("ChannelHub internal_sim", "38.901 风格 CDL/TDL、多小区几何、导频估计", "默认快速物理源", "部分阵列/干扰模型是工程近似"),
            ("Sionna RT", "场景网格、材料、射线与确定性路径", "可选城市/室内 RT 源", "依赖重；场景/材料质量决定可信度"),
            ("SuperRAN", "合同、硬件默认、算法、TTI、统计门", "编排与证据层", "不重新发明传播求解器"),
        ],
    )
    body += """
<h3>Sionna RT 的 slot 快照为什么这样做</h3>
<p>Sionna 的 <code>Paths.cfr()</code> 会按设备速度计算路径 Doppler，但调用方必须同时设置
<code>Receiver.velocity</code> 和物理采样率。当前实现用完整 UE 速度向量，并令采样率为
<code>1 / 平均 OFDM-symbol 周期</code>；RB 频率网格围绕载波中心对称。旧实现没设 velocity、
还沿用 1 Hz 默认采样，14 个 symbol 只是静态重复，本轮已修。调用语义可在
<a href="https://nvlabs.github.io/sionna/rt/api/paths.html" target="_blank" rel="noreferrer">Sionna RT Paths API</a>
与<a href="https://nvlabs.github.io/sionna/rt/tutorials/Mobility.html" target="_blank" rel="noreferrer">官方 Mobility 教程</a>复核。</p>
<p>系统层每个 TTI 只需要一个信道快照：14-symbol 网格先服务导频/估计和真实 symbol 级 Doppler，
落盘边界再取中间 symbol，保留长度为 1 的时间轴。不能对复信道做 symbol 平均——相位抵消会
凭空制造深衰；也不能把 14 个 symbol 当成 14 个 TTI。</p>
"""
    body += callout(
        "good", "标准表错误现在会阻断生成",
        "<p>相邻 MSG-Platform 的 CDL-A~E 已直接修正；superran 仍保留独立标准副本，用来兼容"
        "旧 checkout。启动时会按安装版本支持的 dataclass 字段安全覆盖并逐表核对；若校准异常，"
        "<code>channelhub._ensure_path()</code> 硬失败，不再吞掉异常后继续产出伪标准数据。</p>",
    )
    body += "<p class=source-row>适配入口：" + source_ref("src/superran/channelhub.py", "def probe_capabilities") + " · " + source_ref("src/superran/scenes.py", "class SceneInfo") + "</p>"
    return Page(
        "channel", "信道、场景与传播状态", "物理内核", "CHANNEL MODEL",
        "H 的路径公式、极化耦合、同站/异站状态与 ChannelHub/Sionna 分工。", body,
        ("CDL", "TDL", "Sionna", "Jones", "传播状态"),
    )


def antenna_page() -> Page:
    body = array_svg()
    body += array_256_svg()
    body += port_contract_svg()
    body += """
<h2>F(192×64) 不是拍脑袋矩阵</h2>
<p>64T 的逻辑端口按 <code>r=p·32+h·4+v</code> 展平，物理阵子按
<code>e=p·96+h·12+(3v+q)</code> 展平；v=0 是顶部。端口 r 只连接
<code>q=0,1,2</code> 三个相邻物理阵子，所以每列恰有三个非零值。</p>
""" + F_FEED + F_COUPLING + F_EFFECTIVE
    body += """
<h2>公司 256T：同一个展平合同，不同的面板与馈电规模</h2>
<p>256T 不是把 64T 的 shape 改成 256；两者端口轴顺序现在相同。256T RF 端口是
16H×8V×2pol，按 <code>r=p·128+h·8+v</code>（0-based）展平。每个 T 在其背后驱动 6 个
垂直物理 AE，因此实际为 16H×48V×2pol=1536 AE，RF 垂直相位中心间距为 6×0.67λ=4.02λ。</p>
""" + F_COUPLING_256
    body += """
<h3>物理意义</h3>
<ul>
  <li><strong>列范数为 1：</strong>一个 RF 端口的单位输入功率被 3 或 6 个阵子重新分配，而不是凭空放大。</li>
  <li><strong>不同列不重叠：</strong>两种固定馈电下每个阵子只属于一个 RF 端口；因此 F 的列天然正交。</li>
  <li><strong>相位随 6° 下倾递进：</strong>子阵阵子相干叠加，使端口方向图主瓣向水平面下方转动；top-to-bottom 只是编号，不能翻转物理下倾。</li>
  <li><strong>快路径可验证：</strong><code>effective_subarray</code> 直接算端口响应；<code>physical_reference</code>
  先生成 192/1536-AE 信道再乘 F。二者必须在数值容差内一致。</li>
</ul>
<h2>阵元方向图</h2>
<p>当前有单元阵子方向图，但它是<strong>可配置的参数化临时模型</strong>，不是公司实测表。
水平 HPBW 110° 来自产品先验；垂直 65°、峰值 8 dBi 与 30 dB 截断当前仍是工程参数。
下图曲线由当前公式和默认参数直接生成，作用是帮助理解，不代表暗室实测。</p>
""" + element_pattern_svg() + F_PATTERN + F_PATTERN_COMBINE + F_JONES
    body += table(
        ["量", "当前默认", "在模型中的作用", "不能误读为"],
        [
            ("φ3dB", "110°", "水平功率增益在 ±55° 约下降 3 dB", "已导入的公司实测 cos 表"),
            ("θ3dB", "65°", "垂直元素包络；与固定子阵因子相乘", "整机端口垂直波宽"),
            ("Gmax", "8 dBi", "由 /20 转成复场幅，再参与每条 ray", "数字 64T/256T 波束增益"),
            ("Am", "30 dB", "参数化前后向衰减截断", "真实 front-to-back ratio"),
            ("ζp", "+45° / −45°", "理想线极化 Jones 基", "方向相关复 Jones 实测值"),
            ("element_xpd_db", "8 dB", "无 profile XPR/统计回退值与 provenance", "已贯通的公司方向相关天线 XPD"),
        ],
    )
    body += callout(
        "danger", "不要把它称为 cos 实测方向图",
        "<p>实现是 3GPP 风格的抛物线 dB 包络，<code>measured_jones</code> 入口目前硬报"
        " <code>NotImplementedError</code>。拿到公司 (az,el,f) 复 Jones 数据后，应新增插值、频率轴、"
        "极化端口校准与 hash，而不是只替换一个 HPBW 数字。</p>",
    )
    body += """
<h3>方向图怎样一步步影响最终信号</h3>
<ol>
  <li><strong>dBi 是功率增益：</strong>复电场幅度必须用 <code>10^(GE/20)</code>；若误用 /10，信道幅度会多平方一次。</li>
  <li><strong>极化方向：</strong>同一个标量包络乘 ±45° Jones 向量；InternalSim 在实现中把标量放在 steering、单位 Jones 放在极化收缩，乘积与公式一致。</li>
  <li><strong>固定馈电：</strong>每个物理 AE 的复场按 <code>wq</code> 相干叠加，形成 1 驱 3/1 驱 6 的下倾、旁瓣和潜在栅瓣，而不是把功率增益简单乘 3/6。</li>
  <li><strong>逐 ray 耦合：</strong>收发 Jones 经 <code>Jℓ</code>/XPR 收缩，并和收发 steering、时延、多普勒一起进入 <code>H(t,f)</code>。</li>
  <li><strong>数字波束：</strong>SVD/PMI/ZF/EBF 权在 RF 端口域作用于 H；它改变多端口相干合成与 MU 干扰，但不能再重复加一次元素/子阵增益。</li>
</ol>
""" + F_SUBARRAY_PATTERN + F_RAY_POLARIZATION
    body += """
<p>参数化阵子增益与固定 1 驱 3/1 驱 6 子阵因子先形成<strong>有效 RF 端口绝对增益</strong>，再进入
conducted-power 链路预算；数字 SVD/PMI/ZF 增益随后在端口域计算。InternalSim 的服务 H 会在
全部 ray 合成后做一次整体小尺度归一化，但保留不同 ray 之间的相对方向权重；绝对端口增益由
long-term link budget 单独带入。旧字段 <code>sector_gain_all_db</code> 为兼容保留，在有效阵列模式下
其物理语义是 element×subarray gain，不只是传统扇区包络。</p>
"""
    body += """
<h2>如何证明 F 正确</h2>
"""
    body += table(
        ["不变量", "期望", "失败意味着"],
        [
            ("shape", "64T=(192,64)；256T=(1536,256)", "阵子/端口索引或极化维错误"),
            ("nnz per column", "64T 每列 3；256T 每列 6", "固定馈电拓扑错误"),
            ("column norm", "每列 ||F[:,r]||₂=1", "端口输入功率不守恒"),
            ("column overlap", "FᴴF=I₆₄ / I₂₅₆", "不同 RF 端口错误共享阵子"),
            ("downtilt peak", "+6° 配置对应主瓣约 −6° elevation", "相位符号/Tx-Rx 共轭错误"),
            ("reference equivalence", "effective 与 192-AE reference 相对误差在容差内", "快路径公式或投影方向错误"),
            ("port permutation", "canonical↔Sionna↔Type-I 往返为 identity", "码本/物理端口错位"),
        ],
    )
    body += "<p class=source-row>项目配置：" + source_ref("src/superran/hardware.py", "COMPANY_RF_PANEL") + " · ChannelHub 物理实现由 <code>msg_embedding/phy_sim/effective_array.py</code> 承担。</p>"
    return Page(
        "antenna", "阵列、双极化与 F 矩阵", "物理内核", "ARRAY & POLARIZATION",
        "从 +45/-45° 阵元到 64T/1 驱 3 与 256T/1 驱 6，再到可验证的 F 矩阵。", body,
        ("F矩阵", "1驱3", "1驱6", "256T", "+45/-45", "方向图", "下倾"),
    )


def srs_page() -> Page:
    body = srs_matrix_svg()
    body += """
<h2>64×4 到底怎么来</h2>
<p>上行 SRS 时 UE 有 4 个发射端口，gNB 有 64 个接收端口。每个 UE 端口使用可分离的
参考信号资源，gNB 在每个接收端口上解扩四路，因此得到 64×4 的上行端口信道估计。
TDD 互易假设下，它经 RF 校准后转置/共轭到下行预编码约定；不是“凭空把 4×64 复制一份”。
这是应实现的物理矩阵模型，不等于当前调用链已经完成。</p>
""" + F_SRS_RX + F_LS
    body += callout(
        "danger", "当前观测边界：仍不是物理多端口 Y=HX",
        "<p>底层 <code>srs_sequence()</code> 能生成 1/2/4 端口序列，但 serving pilot 调用当前仍固定 "
        "<code>N_ap=1,n_ap_index=0</code>；观测端再把标量 X 广播到每个 BS×UE 系数。这样可以验证 "
        "LS/频域 LMMSE 数值，却无法真实产生端口 rank 不足、同码污染或空间协方差抑制。下一阶段必须先"
        "构造同一接收向量上的 <code>Y=HX+I+N</code>，再讨论时频空 LMMSE。</p>",
    )
    body += """
<h2>当前是 ZC 吗</h2>
<p>序列生成层是。<code>pilot_type_ul="srs_zc"</code> 走 38.211 SRS 基序列：长度足够时使用 Zadoff–Chu，
短长度走规范短序列；支持 comb、循环移位、group/sequence hopping 和频域 hopping。
“SRS 周期”指发送周期；某个 RBG 距离最近一次有效 SRS 的时间应叫
<strong>CSI 陈旧时长/lag</strong>，不叫“SRS 年龄”。</p>
<p>频域资源完整实现 38.211 Table 6.4.1.4.3-1 的 64 行；<code>n_RRC</code>
（freqDomainPosition）与 <code>n_shift</code>（freqDomainShift）分开建模，奇/偶
<code>N_b</code> 的 <code>F_b(n_SRS)</code> 均按规范原式计算。默认公司预设使用
<code>T_SRS=20 slot</code>；在 30 kHz SCS 下 1 slot=0.5 ms，故发送周期为 10 ms，
17 跳的完整宽带采集窗为 170 ms。可对照
<a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138211/18.07.00_60/ts_138211v180700p.pdf" target="_blank" rel="noreferrer">ETSI TS 138 211 V18.7.0 §6.4.1.4.3</a>。</p>
<h2>LS、LMMSE 与方向性</h2>
""" + F_LS + F_LMMSE
    body += table(
        ["模式", "做法", "保留方向性吗", "成本/边界"],
        [
            ("ideal", "直接返回真值", "是", "乐观上界，不是可实现估计"),
            ("ls_linear", "导频点 LS，再做频时线性插值", "是；每个 BS×UE 复系数独立估计", "噪声/干扰直接进入估计，低 SIR 方差大"),
            ("ls_mmse", "从真实、可非均匀的 pilot 位置直接以 R_tp 映射到全部目标 RB", "是；每个 BS×UE 复系数独立估计", "公开配置 canonical；需要 tau_rms/SNR prior；时间轴因无 Doppler prior 仍线性插值"),
            ("ls_lmmse", "与 ls_mmse 完全相同", "同上", "物理命名更精确的兼容 alias，不是另一种算法"),
            ("ls_hop_concat", "跨 hopping occasion 合并部分带估计", "是", "完整带宽获得时间变长，需显式记录 lag"),
        ],
    )
    body += callout(
        "note", "LS 不会让干扰失去方向",
        "<p>在正确的多端口 <code>Y=HX+I+N</code> 中，LS 只是用已知导频解扩；污染项仍携带干扰"
        "信道的 64 维空间向量，所以 LS 本身不会把方向抹掉。当前逐系数观测抽象却不能用来证明"
        "这件事，因为干扰在物理接收合成之前已经被分开。当前 LMMSE prior 主要是频域指数 PDP；"
        "它不等价于完整的时频空 MMSE/IRC。</p>",
    )
    body += callout(
        "warn", "LMMSE 不是每个 realization、每个 SNR 都必胜",
        "<p>匹配先验的低 SNR Monte Carlo 应优于 LS，高 SNR 且 full-pilot 时应退化回 LS；但指数 PDP "
        "若与某个固定 CDL realization 不匹配，单次高 SNR 误差可能高于线性插值。测试因此锁定"
        "统计性质、有限性和高 SNR 极限，不写不成立的逐样本万能不等式。</p>",
    )
    body += """
<h2>周期、报告与处理时延是三件事</h2>
""" + F_SRS_LAG
    body += table(
        ["参数", "当前默认", "物理含义"],
        [
            ("srs_period_ms", "10 ms", "UE 发 SRS 的周期；hopping 时每次只覆盖一部分带宽"),
            ("ChannelHub srs_periodicity", "20 slot", "30 kHz SCS 下等于 10 ms；不要把 slot 数直接写成 ms"),
            ("srs hopping", "on；17-hop", "C_SRS=63/B_SRS=1/b_hop=0 下每次 16 RB，轮转扫 17 RBG"),
            ("csi_processing_delay_ms", "2 ms", "估计到权值可用于调度的处理延迟"),
            ("csi_report_period_ms", "20 ms", "宽带 PMI/CQI 何时更新并保持；不是 5 ms 快照"),
            ("snapshot_ms", "由链路表配置", "物理信道采样间隔，只用于把毫秒 lag 离散成快照索引"),
        ],
    )
    body += callout(
        "warn", "关于 PMI 周期",
        "<p>项目把 CSI report 周期显式独立为 20 ms 工程默认，避免误用 5 ms snapshot。"
        "它不是宣称所有商用网都固定 20 ms；若现场 RRC/日志给出周期，应覆盖并记录。"
        "SRS 周期和 CSI report 周期不可合并成一个旋钮。</p>",
    )
    body += "<p class=source-row>实现入口：" + source_ref("src/superran/physical.py", "def srs_config") + " · " + source_ref("src/superran/csi_aging.py", "class CsiConfig") + "</p>"
    return Page(
        "srs", "SRS、64×4 与信道估计", "物理内核", "SRS & CHANNEL ESTIMATION",
        "ZC 序列、LS/LMMSE、17 跳频、周期/报告/处理时延的清晰边界。", body,
        ("SRS", "64x4", "LS", "LMMSE", "ZC", "PMI周期"),
    )


def measurements_page(modules: list[ModuleDoc]) -> Page:
    measure = next(m for m in modules if m.name == "measure")
    funcs = [s for s in measure.symbols if s.kind == "function"]
    rows = []
    for item in funcs:
        rows.append((
            f"<code>{esc(item.name)}</code>", item.doc,
            source_ref(measure.rel, f"def {item.name}", f"L{item.line}"),
        ))
    body = """
<h2>落盘合同</h2>
""" + F_DATASET
    body += """
<p>每个数据集包含复信道、summary/config、位置、几何 SINR/SIR 与可选干扰/预编码量。
所有生成样本在落盘前必须满足 h_true/h_est 同形、有限、角色明确；summary 记录数据源、
阵列、标准表版本、随机种子与近似边界。</p>
<h2>观察量计算入口</h2>
"""
    body += table(["API", "用途", "源码"], rows, raw={0, 2})
    body += """
<h2>12 类 MCP 观察量与底层函数</h2>
<p>MCP 对外把细粒度函数组合为 12 类可发现观察量：PDP/时延、空间协方差与特征谱、
条件数、信道增益/RSRP、SRS 特征、PMI、几何/干扰、预编码谱效、链路吞吐、系统 KPI 等。
数量以 <code>sr_capabilities</code> 返回的 catalog 为准；底层函数表保留更细的开发入口。</p>
"""
    body += callout(
        "warn", "绝对量与归一量不要混",
        "<p><code>channel_gain_db</code>、绝对 RSRP、几何 SINR/SIR 和归一化协方差有不同的功率参考。"
        "做算法 A/B 时必须锁定相同的噪声、功率和归一化；不能拿归一化 H 的“增益”解释覆盖。</p>",
    )
    body += """
<h2>toy：从一个样本取可复核链路量</h2>
""" + code(r'''from superran.loader import load

ds = load("ds_xxxxxxxx")
H = ds.h_true[0]                 # [T, RB, BS_ant, UE_ant]
Hhat = ds.h_est[0]               # 同形，预编码侧可见 CSI
print(ds.nmse_db())
print(ds.pdp(0))
print(ds.pmi(0, max_rank=4))
print(ds.link_performance(0, h_for_precoding=Hhat))
''')
    return Page(
        "measurements", "数据集与观察量", "物理内核", "DATA CONTRACT",
        "h_true/h_est 的形状、角色与 measure/loader 全部公开入口。", body,
        ("Dataset", "PDP", "PMI", "RSRP", "NMSE"),
    )


def beamforming_page() -> Page:
    body = power_constraints_svg()
    body += """
<h2>先固定矩阵约定</h2>
<p>项目统一使用 <code>Q[frequency, antenna, stream]</code>。因此第 m 根天线的功率是
<code>||Q[:,m,:]||²</code>（二维时是 Q 的<strong>行</strong>范数平方）。现场文档若把权写成
<code>[stream, antenna]</code>，它所说的“列归一”在本项目里就是“天线行归一”。</p>
<h2>三种功率约束</h2>
""" + F_EBF + F_PEBF + F_NEBF
    body += table(
        ["模式", "约束与缩放", "总功率", "流间几何", "典型表现"],
        [
            ("EBF", "SVD/码本方向按流等分 P", "通常用满", "保持", "总功率基线；可能有单天线超过 P/M"),
            ("PEBF", "由峰值天线决定一个全局 α", "通常用不满", "保持", "满足每天线功率且不破坏 ZF 零陷"),
            ("NEBF", "每根非零天线分别拉到 P/M", "用满", "可能破坏", "SU 常接近 EBF；强相关 MU 可低于 PEBF"),
        ],
    )
    body += """
<h2>为什么 SU 中 NEBF ≈ EBF，而 MU 中可能 NEBF &lt; PEBF</h2>
<p>SU 只有本用户流间干扰，接收侧 MMSE 还有自由度；逐天线重标通常主要改变阵列幅度，且把
功率用满，所以 NEBF 常接近总功率 EBF并明显优于被峰值天线卡住的 PEBF。MU ZF/RZF 的关键
是不同用户波束的精确相消。NEBF 对每根天线使用不同缩放，相当于左乘一个非标量对角矩阵，
原来的 <code>H_i W_j=0</code> 一般不再成立；高 SNR/强相关/单接收天线时残余干扰可压过功率收益。</p>
"""
    body += callout(
        "good", "反向哨兵",
        "<p>测试同时固定两个确定性例子：64T SU 中 <code>|SE_NEBF/SE_EBF−1|&lt;5%</code> 且"
        " NEBF&gt;PEBF；强相关 MU 中 NEBF 产生可测残余干扰并出现 NEBF&lt;PEBF。只测前者不足以证明"
        "每天线实现正确。</p>",
    )
    body += """
<h2>实现为什么返回三件东西</h2>
<p><code>equal_power_weights</code> 返回物理 Q、兼容旧 SINR 公式的 W_model、以及
<code>PowerDiagnostics</code>。诊断包含逐天线功率、总功率利用率、最大越界和 Gram 非对角能量。
这避免“函数名叫 NEBF”被误当作约束已经满足，也避免 PEBF/NEBF 被二次归一。</p>
""" + code(r'''from superran.beamforming import equal_power_weights

Q, W_model, diag = equal_power_weights(W_svd, mode="nebf", total_power=1.0)
assert diag.max_per_antenna_violation == 0
print(diag.as_dict()["utilization_mean"])
print(diag.as_dict()["orthogonality_error_mean"])
''')
    body += "<p class=source-row>实现：" + source_ref("src/superran/beamforming.py", "def constrain_physical_matrix") + " · 哨兵：" + source_ref("tests/test_physics_invariants.py", "64T SU") + "</p>"
    return Page(
        "beamforming", "预编码与每天线功率约束", "链路算法", "BEAMFORMING",
        "EBF、PEBF、NEBF 的矩阵约定、物理取舍与 SU/MU 反向验证。", body,
        ("EBF", "PEBF", "NEBF", "每天线功率", "SVD"),
    )


def sinr_page() -> Page:
    body = """
<h2>从 H 和 W 到 post-MMSE SINR</h2>
<p>预编码权由 <code>h_est</code> 设计，等效信道和判错由当前 <code>h_true</code> 评估。
接收机看到期望流、同用户其他流、MU 其他用户、邻区空间协方差和热噪声。</p>
""" + F_MMSE + F_STREAM_SINR
    body += """
<h2>先钉住信号参考面：总载波、每 RB、数字 BF 是三层</h2>
""" + F_RB_LINK_BUDGET + F_PREBEAM_ANCHOR
    body += """
<p><code>tx_power_dbm</code> 是整个活动载波的导通总功率，<code>noise_power_dBm</code> 是一个
活动 RB 的 kTB+NF；所以 273 RB 必须先减 24.36 dB。大尺度预算已包含阵元方向图、固定子阵和
电下倾，但不包含 64 端口数字预编码。ChannelHub 的 first-party SNR/SIR/SINR 因而都在
<strong>预数字波束、每 RB</strong>参考面。</p>
<p>链路级用 <code>E[|H|²]</code> 反标总损伤；rank-1 的 <code>E[σ₁²]</code> 是后波束诊断量。
上式给出一个直接反例：若 H 的数字 BF 增益为 14 dB，那么 rank-1 post-BF SINR 应比几何
SINR 高 14 dB；拿 σ₁² 反标噪声会把这 14 dB 人为抵消。</p>
"""
    body += callout(
        "warn", "两个量都叫 SINR，但所在参考面不同",
        "<p><code>sinr_dB</code> 是大尺度预数字波束工作点；<code>sinr_per_rb_stream_db</code> 是指定"
        "预编码、rank、接收机后的逐 RB/逐流结果。前者不是后者的宽带平均值，而是后者的功率"
        "锚点。文档、代码和测试必须把两者名字带全。</p>",
    )
    body += """
<h2>“全带谱效”现在如何计算</h2>
"""
    body += steps((
        ("逐快照、逐候选 rank", "<p>对 r=1..4 用 gNB 可见 CSI 设计 SVD/Type-I 权，并在真实当前信道上算逐 RB×流 post-MMSE SINR。</p>"),
        ("RB → RBG", "<p>每 16 RB 在线性功率域平均各流 SINR，得到 17 个 RBG；若输入已经是 RBG，组长为 1。</p>"),
        ("RBG/流 → 一个宽带 SINR", "<p>每个 RBG 先对流取 dB 均值，再对 17 RBG 的 dB 值取算术平均；顺序等价。</p>"),
        ("单码字 MCS", "<p>用该宽带 SINR 查目标 BLER 10% 的最高 MCS；不能逐 RB 各选一档再平均。</p>"),
        ("rank 谱效", "<p><code>SE(r)=r×MCS.se</code>，选择 SE 最大的 rank；不是直接把 Shannon log2(1+SINR) 当系统 MCS 谱效。</p>"),
    ))
    body += F_RANK
    body += table(
        ["名字", "使用的视角", "计算/用途"],
        [
            ("se / best_se", "h_est 设计、h_true 评估", "真实接收 SINR → 单码字 MCS → rank×SE；用于结果/legacy 实发记账"),
            ("se_gnb / best_se_gnb", "全在 gNB 当前可见 CSI 上", "调度器估计的可达谱效；避免偷看未来/真实信道"),
            ("sinr_tx", "CQI 门限 + gNB BF Gain", "发送侧 MCS 输入；OLLA 在 TTI 循环再加"),
            ("TBS(17)", "slot、MCS、rank、17 RBG", "experience PF 排序的 fullband potential，单位 bytes"),
            ("grant TBS(n)", "实际 grant bitmap", "experience 实发与 PF credit；功控时对 subset 重聚合/重选 MCS"),
        ],
    )
    body += callout(
        "warn", "当前有效 SINR近似",
        "<p>跨 RBG 使用 dB 算术平均，是透明、偏保守的工程基线，但不是经 BLER 曲线标定的 EESM/MIESM。"
        "因此本文把它称为“宽带有效 SINR近似”，不称为标准链路抽象。要对频选深衰给出高精度 BLER，"
        "下一阶段应按 MCS/码块标定 β 或 MI 映射。</p>",
    )
    body += """
<h2>Shannon 谱效在哪里</h2>
<p><code>linklevel.link_performance</code> 仍提供 <code>Σlog₂(1+γ)</code> 作为物理链路谱效和独立
注水容量参考；系统仿真使用离散 MCS/TBS。二者都叫“谱效”时必须带限定词，不能把 SVD 曲线
冒充容量上界，也不能把 MCS 表值解释成 Shannon 容量。</p>
"""
    body += "<p class=source-row>聚合：" + source_ref("src/superran/mumimo.py", "def user_sinr_db") + " · 建表：" + source_ref("src/superran/system.py", "def build_link_tables") + "</p>"
    return Page(
        "sinr", "接收机、SINR 与全带谱效", "链路算法", "POST-MMSE SINR",
        "明确回答全带谱效的五步计算、gNB/真实视角和当前有效 SINR近似。", body,
        ("MMSE", "全带谱效", "best_se", "单码字", "EESM"),
    )


def linkadapt_page() -> Page:
    body = link_flow_svg()
    body += """
<h2>发送侧不是接收侧长期均值</h2>
""" + F_TX_SINR
    body += """
<p>CQI 是终端用 Type-I/PMI 参照权在真实信道上测得并按报告周期更新的长期宽带量；先映射
初始 MCS，再取该 MCS 的 10% BLER SINR 门限 Γ。BF Gain 则是 gNB 在自己可见的（可能陈旧）
SRS CSI 上，实际发送权相对 PMI 权的 post-MMSE SINR差。两者相加后重选 MCS，最后由用户级
SU OLLA 调整。</p>
"""
    body += callout(
        "danger", "禁止 oracle",
        "<p>把真实接收 SINR 的全仿真均值回填给发送侧，会同时泄露未来信道和实际波束命中效果。"
        "这会掩盖 CSI 老化与 OLLA 的作用。CQI expanding mean 只能使用 0..s 的历史报告，"
        "BF Gain 必须来自 gNB 自己的 CSI。</p>",
    )
    body += """
<h2>OLLA 如何闭环</h2>
""" + F_OLLA
    body += """
<p>项目基线 ACK 上调 +0.01 dB、NACK 下调 −0.09 dB，因此理想稳态目标 BLER 为
0.01/(0.01+0.09)=10%。warmup 可用同一比例的加速因子加快收敛；测量窗和预热窗分别记录，
不能把加速后的短期轨迹冒充现场时间常数。SU OLLA 与 MU OLLA 都是用户级数组，后者不区分
具体配对关系。</p>
<h2>TBS 为什么不能用除法反推 RBG</h2>
""" + F_TBS + F_RBG_SEARCH
    body += """
<div class="toy"><div><b>实算：MCS 12 / rank 2 / D slot</b>
<p>1 RBG = 1,729 B；若线性外推，17×1,729 = 29,393 B；38.214 量化后的真实 17 RBG
= 29,722 B，偏 +1.119%。</p></div><div><b>会怎样错</b><p>payload=29,394 B 时，除法会认为“17 个也不够”或在其他边界少给一个；
<code>searchsorted</code> 在严格递增表上准确返回 17 且可装下。</p></div></div>
"""
    body += callout(
        "good", "表合同",
        "<p><code>TbsLookup</code> 建 2×28×4×17 = 3,808 个 int64（D/S 两类 slot）。"
        "初始化时全扫并要求每行严格递增；一旦 38.214 实现或 RE 模型使序列持平/下降，"
        "构造当场失败，而不是让 searchsorted 静默给错资源。</p>",
    )
    body += """
<h2>BLER 与 HARQ 边界</h2>
<p>表 1/2 是 38.214 MCS/CQI + 分析 BLER 模型；表 3 是用户提供的 20B 256QAM 28 档
NewTx/ReTx 曲线。capacity/legacy 可查 ReTx 曲线，但没有真实软缓冲；experience_v2 的 NACK
payload 留队、下一次按 NewTx 再判错，明确不称为标准 HARQ 软合并。</p>
"""
    body += "<p class=source-row>发送侧：" + source_ref("src/superran/system.py", "发送侧 SINR = CQI") + " · TBS：" + source_ref("src/superran/experience.py", "class TbsLookup") + "</p>"
    return Page(
        "linkadapt", "CQI、BF、OLLA、MCS 与 TBS", "链路算法", "LINK ADAPTATION",
        "发送/接收 SINR 分离、因果 CQI、OLLA 与非线性 TBS 反查。", body,
        ("CQI", "BF Gain", "OLLA", "MCS", "TBS", "searchsorted"),
    )


def mu_page() -> Page:
    body = link_flow_svg() + mu_decision_svg()
    body += """
<h2>MU 的发送侧 MCS</h2>
""" + F_MU_SINR + F_POWER_LOSS
    body += """
<p>SU 链先得到 CQI + BF + SU OLLA。MU 再加三项：用户间残留相关性折算的
<code>CorrLoss≤0</code>；同一 RBG 总功率在全部 MU layers/users 间平分的
<code>PowerLoss</code>（两个 rank2 用户相对单用户 rank2 为 −3 dB）；以及独立的用户级 MU OLLA。
真实接收 SINR来自 pair 信道、ZF/RZF 权和当前 h_true，仍不等于这些 dB 项的简单和。</p>
<h2>Phase A 的真实 pair 表</h2>
"""
    body += steps((
        ("候选对", "<p>按用户有效信道相关性与门限筛选，两用户一组。</p>"),
        ("预编码", "<p>当前 experience 边界为 2 用户、每用户 rank2，使用 ZF 或带噪声/CSI-error loading 的 RZF。</p>"),
        ("双视角", "<p>在 gNB 估计 CSI 上得到预测 CorrLoss/MCS 输入；在真实当前信道上得到逐用户/逐 RBG SINR 与 BLER 输入。</p>"),
        ("持久表", "<p>保存 correlation、CorrLoss、PowerLoss、true/predicted SINR 与可选逐 RBG 数组；Phase B 不做矩阵求逆。</p>"),
    ))
    body += """
<h2>Phase B 为什么比较 useful bytes</h2>
<p>PF 先排一次优先级，然后分别构造“全 SU”和“允许 MU”的完整 TTI 计划。两者都按队列实际剩余
字节截断收益：TBS 超出业务包的 padding 不算谱效收益。若 SU 能传完所有当前队列，强制 SU；否则
MU useful bytes ≥ SU 时走 MU。这个规则避免在轻载/小包场景为了理论空间复用而引入无意义干扰。</p>
"""
    body += table(
        ["对象", "当前口径", "常见错误"],
        [
            ("MU PRB", "共享 RBG 在小区物理资源只计一次", "给两个用户各计一份导致利用率>100%"),
            ("用户 exposure", "每个配对用户都暴露于该 MU RBG", "误以为每人只拿一半频域"),
            ("用户归因", "共享 RBG 在两 UE 间等分，跨用户可加", "把 exposure 相加做小区资源"),
            ("MU OLLA", "每用户一条、所有 pair 共用", "误称为 pair-specific OLLA"),
            ("legacy MU", "MU/SU 聚合标量比值", "把它当 experience_v2 的 pair 实现"),
        ],
    )
    body += callout(
        "decision", "下一阶段 MU 细化",
        "<p>当前落地的是可验证的最小真实 MU：2UE×rank2、ZF/RZF、用户级 MU OLLA。"
        "一般 rank 组合、3/4 用户、pair-specific OLLA、HARQ 进程与更大候选图仍需业务/性能约束后再扩展。</p>",
    )
    body += "<p class=source-row>pair 表：" + source_ref("src/superran/system.py", "def build_mu_pair_tables") + " · TTI 决策：" + source_ref("src/superran/experience.py", "SU_clears_all_queues") + "</p>"
    return Page(
        "mu", "MU-MIMO 与 SU/MU 自适应", "链路算法", "MU-MIMO",
        "CorrLoss、PowerLoss、双 OLLA、真实 pair 表和 useful-bytes 计划比较。", body,
        ("MU", "ZF", "RZF", "CorrLoss", "PowerLoss", "pair table"),
    )


def modes_page() -> Page:
    body = phases_svg()
    body += """
<h2>两种模式，不是两档精度</h2>
"""
    body += table(
        ["维度", "capacity / legacy_v1", "experience / experience_v2"],
        [
            ("问题", "满带调度下的容量/历史 KPI 复现", "有限业务包下的排队、资源与用户体验"),
            ("每 TTI 调度", "SU 或 legacy 标量 MU；用户通常拿全带", "一次 PF 排序后可服务多个 UE，按需 RBG"),
            ("MU", "预计算聚合 ratio 后主循环标量折算", "候选 pair 的真实链路表与完整 SU/MU plan"),
            ("PF numerator", "gNB best_se", "假设全带 TBS(17)"),
            ("PF credit", "best_se/受 MU rank 修正的 SE", "默认实际 scheduled TBS；可选 ACK goodput"),
            ("队列", "历史 traffic/burst 抽象", "arrival-object FIFO、NACK 留队、warmup 切窗"),
            ("体验速率", "legacy trim", "DRB busy-period + fractional small burst + 含头速率"),
            ("资源 KPI", "整带占用为主", "PRB utilization、0..17 占用、MU/used、用户归因"),
            ("HARQ", "NewTx/ReTx 曲线近似", "无软合并；NACK 下次按 NewTx"),
        ],
    )
    body += callout(
        "danger", "禁止横向偷换",
        "<p>不能把 experience 的按需 RBG 结果与 capacity 的全带 legacy 结果直接相减后称为“算法提升”；"
        "两边必须共享 evaluation profile、话务、CSI、功率、warmup 和 KPI 定义。"
        "同名字段若语义不同，结果 JSON 会带 profile/version/notes。</p>",
    )
    body += """
<h2>为什么要预热</h2>
<p>例如总仿真 5 s、<code>warmup_s=1</code>：业务、SRS、CSI/PMI、PF 平均和 OLLA 从 0 s 开始运行，
但体验/KPI 只统计 1–5 s。这样既让状态真实收敛，又不把初始空队列、SRS 未扫齐和 OLLA 冷启动损失
混入稳态指标。预热时可以加速 OLLA，但测量窗应恢复正常步长，并回传切窗时的状态。</p>
"""
    body += "<p class=source-row>模式路由：" + source_ref("src/superran/system.py", "evaluation_mode") + " · 体验入口：" + source_ref("src/superran/experience.py", "def simulate_experience") + "</p>"
    return Page(
        "modes", "容量评估与体验评估", "系统仿真", "EVALUATION PROFILES",
        "capacity/legacy_v1 与 experience/experience_v2 的语义、实现和 KPI 边界。", body,
        ("capacity", "experience", "legacy_v1", "experience_v2", "warmup"),
    )


def experience_page() -> Page:
    body = phases_svg() + mu_decision_svg()
    body += """
<h2>一个 DL TTI 的完整顺序</h2>
"""
    body += steps((
        ("话务先到达", "<p>所有 D/S/U/G 时隙都执行 arrival step；上行时隙不能让业务凭空消失。</p>"),
        ("选择物理快照", "<p><code>snap=(tti//snap_every)%n_snap</code>，读取 gNB/真实链路表与 OLLA。</p>"),
        ("计算 fullband potential", "<p>按当前 rank/MCS 计算 TBS(17)，作为 PF numerator；队列小不改变排序的链路机会。</p>"),
        ("PF 排序一次", "<p>经典 PF 默认；QoS-PF 在 α=β=1、γ=0、w=1 时逐分配退化为经典 PF。</p>"),
        ("构造 SU/MU plan", "<p>按顺序用 searchsorted 给最小够用 RBG；没有候选需求时剩余资源留空。</p>"),
        ("按 useful bytes 选 plan", "<p>SU 能清空所有队列则强制 SU；否则 MU≥SU 才选 MU。</p>"),
        ("真实 grant 判错", "<p>按实际 bitmap/MCS/TBS，真实 SINR 查 BLER；NACK payload 留队。</p>"),
        ("更新 OLLA/PF/KPI", "<p>记录分配、资源归因、arrival/busy period、首包与完成时延，再更新平均速率。</p>"),
    ))
    body += """
<h2>PF 的 R̄u（RU）到底怎样维护</h2>
""" + F_PF + F_RAVG
    body += """
<p><code>r_avg</code> 的单位是<strong>每个可下行调度 TTI 的 EWMA 字节机会</strong>。每个 D/S TTI
统一更新一次；未被调度用户本次 <code>R_credit=0</code>，平均值自然衰减。U/G TTI 不更新，因为没有
下行资源机会。初值为 1e−6 防除零；<code>a=1/pf_window_tti</code>。</p>
"""
    body += table(
        ["pf_accounting", "R_credit", "含义与取舍"],
        [
            ("scheduled_tbs（默认）", "实际 grant 的 TB bytes，不论 ACK/NACK", "与占用资源机会一致，避免无线随机失败让 PF 过度补偿"),
            ("acked_goodput", "本次 ACK 的 payload bytes", "面向实际好吞吐，但 BLER 随机性直接影响公平平均"),
            ("legacy_fullband", "同 MCS/rank 的 TBS(17)", "反向哨兵/兼容；按需 RBG 下会严重高记"),
        ],
    )
    body += """
<div class="toy"><div><b>正确记账</b><p>若 TPF=100、旧 R̄=1,000 B、用户只获 1 RBG，
MCS12/rank2 的 TBS=1,729 B：新 R̄=0.99×1,000+0.01×1,729=<strong>1,007.29 B</strong>。</p></div>
<div><b>旧全带 bug</b><p>若误记 17 RBG 的 29,722 B：新 R̄=<strong>1,287.22 B</strong>。
同一次 1-RBG 服务把平均速率抬高约 40 倍增量，后续 PF metric 被过度压低，小包用户被饿死。</p></div></div>
"""
    body += F_QOS_PF
    body += callout(
        "note", "经典 PF 已冻结",
        "<p>当前决策 D1 先使用经典 PF。QoS-PF 作为参数化扩展保留，但默认 α=β=1、γ=0、"
        "priority weighting=none，必须逐分配退化为经典 PF；现场 EPF 定义未冻结前不冒充标准算法。</p>",
    )
    body += "<p class=source-row>排序/计划/记账：" + source_ref("src/superran/experience.py", "potential[i] = lookup.tbs_bytes") + " · " + source_ref("src/superran/experience.py", 'accounting == "scheduled_tbs"') + "</p>"
    return Page(
        "experience", "体验模式调度与 PF 记账", "系统仿真", "EXPERIENCE_V2",
        "逐 TTI 的 PF、SU/MU plan、按需 RBG 和 R_avg 正确记账。", body,
        ("PF", "R_avg", "RU", "按需RBG", "scheduled_tbs", "QoS-PF"),
    )


def traffic_page() -> Page:
    body = traffic_kpi_svg()
    body += """
<h2>话务由包大小与包间隔共同定义</h2>
<p>经验 CDF 文件使用 <code>value,cdf</code> 两列；分别对 packet size 和 inter-arrival 做逆变换采样。
所有用户可共享一个 profile，也可按 <code>ue_ids</code> 映射到 video/XR/FTP 等不同 profile。</p>
""" + code(r'''traffic:
  model: cdf
  profiles:
    - name: video
      packet_size_cdf: presets/traffic/video_size.csv
      inter_arrival_cdf: presets/traffic/video_interval.csv
      packet_size_scale: 0.5
      inter_arrival_scale: 1.0
      ue_ids: [0, 1, 2, 3]
    - name: xr
      packet_size_cdf: presets/traffic/xr_size.csv
      inter_arrival_cdf: presets/traffic/xr_interval.csv
      packet_size_scale: 1.0
      inter_arrival_scale: 0.5
      ue_ids: [4, 5]
''', "yaml")
    body += table(
        ["旋钮", "业务量效果", "同时改变什么"],
        [
            ("packet_size_scale ×0.5", "平均 offered bytes 约减半", "包完成所需 RBG、padding、busy period"),
            ("inter_arrival_scale ×0.5", "来包约加密一倍", "并发队列、MU 触发概率、首包等待"),
            ("用户数增加", "总 offered load 增加", "多用户分集、PF 竞争与 pair 候选"),
            ("UE profile mix", "改变小/大包比例", "RBG 占用直方图与用户级公平性"),
        ],
    )
    body += """
<h2>按目标 PRB 利用率校准</h2>
<p><code>target_prb_utilization=0.30</code> 不是把结果字段硬写成 30%。校准器用公共随机数重复运行，
调整包大小与包间隔标量，直到测得利用率进入容差；随后用独立正式重复实验验证。失败时保留实测值并
报告未达标，不能回填目标。</p>
"""
    body += callout(
        "warn", "30% 是场景，不是算法常数",
        "<p>10%/30%/50% 常用于轻/中/重载；MU 研究常看 50%，日常体验常聚焦 30%。"
        "最终 PRB 利用率由 CDF、标量、用户数、无线条件、调度和空包共同决定。"
        "话务校准必须与算法 A/B 分离：先校准 baseline scene，再用同一 offered process 比算法。</p>",
    )
    body += """
<h2>为什么 mixed 才能看出按需分配收益</h2>
<p>全大包时每人都需要 17 RBG，按需分配退化成全带；全小包时缺少大流量体验对象，收益难落到
体验速率。mixed 让小包不再偷走整个 TTI，同时保留大包用户作为体验速率测量对象，RBG 占用呈
0/1 与 17 两端高、中间低。</p>
"""
    body += "<p class=source-row>CDF 合同：" + source_ref("src/superran/traffic.py", "class EmpiricalCdf") + " · 话务配置：" + source_ref("src/superran/system.py", "class TrafficConfig") + "</p>"
    return Page(
        "traffic", "话务模型与 PRB 负载校准", "系统仿真", "TRAFFIC",
        "包大小/间隔 CDF、多 profile、双标量校准与 mixed 话务物理意义。", body,
        ("CDF", "包大小", "包间隔", "30% PRB", "mixed", "校准"),
    )


def kpi_page() -> Page:
    body = traffic_kpi_svg()
    body += """
<h2>体验速率、首包时延与含头速率</h2>
""" + F_FIRST_PACKET + F_BUSY_RATE
    body += """
<p>首包时延对每个 arrival object 记录“生成 → 第一次实际调度”的等待。掐头去尾速率从第一次
发送开始计时，并按 DRB busy-period 规则排除最后一次排空 piece；含头速率使用相同分子，但
分母从首个 arrival 开始，因此把首包等待纳入体验。小 burst 可用 fractional-slot 口径，不能把
1,500 B 除以完整 0.5 ms 后称为用户体验。</p>
<h2>PRB 利用率与 0..17 RBG 分布</h2>
""" + F_PRB_UTIL
    body += table(
        ["KPI", "分子", "分母/样本", "边界"],
        [
            ("serving_cell_prb_utilization", "测量窗每 TTI 实际占用 RBG×slot_fraction", "可用 17 RBG×D/S slot_fraction", "只算本小区；full buffer≈100%"),
            ("TTI occupancy 0..17", "恰占 k 个物理 RBG 的 TTI 数", "所有测量窗 DL/S TTI", "0 必须入直方图；不是 per-grant 大小"),
            ("MU share of used", "生效 MU 的 PRB-equivalent", "已用 PRB-equivalent", "用户确认口径；共享 MU RBG 只计一次"),
            ("MU utilization", "生效 MU 的 PRB-equivalent", "全部可用 PRB-equivalent", "另一个辅助 KPI，不替代上一项"),
        ],
    )
    body += """
<h2>小区级与用户级两个 Tab</h2>
<p>小区页展示体验/吞吐、首包/PDB、资源、MCS/rank/BLER 与负载；用户页提供按 UE 柱图、跨 UE
经验 CDF 和明细表。MU 资源同时提供 <em>grant exposure</em>（每个配对 UE 都看到完整共享 RBG）
和 <em>attributed PRB</em>（配对 UE 等分，跨 UE 可加）以避免资源对账混乱。</p>
<h2>Agent 自适应编排，不在库内暗调 LLM</h2>
<p>调用本工具的 Agent/LLM 根据用户问题传 <code>kpi_focus</code>；库内只做可审计的 tag/关键词与
场景兜底排序，并返回 <code>source / tags / reasons / full_order</code>。排序只影响首屏，所有可用
KPI 仍保留在折叠区和结果 JSON。这保留 agent 式灵活性，也避免结果页偷偷改变数值。</p>
"""
    body += callout(
        "good", "例：用户问 MU 为什么没收益",
        "<p>Agent 可优先传 <code>[mu_paired_prb_share_of_used, mu_bler_first_tx, "
        "serving_cell_prb_utilization, payload_fill_ratio]</code>；页面先呈现“MU 是否真正生效、"
        "MU BLER 是否恶化、负载是否足够、padding 是否吃掉理论收益”，其余体验/CDF 仍可展开。</p>",
    )
    body += """
<h2>统计窗与覆盖率</h2>
<p>首包时延只对在测量窗内实际获得首次调度的 arrival 可观察，因此必须同时报告
<code>first_packet_delay_observed_share</code>；未完成/过期 arrival 进入 PDB miss 分母，避免只统计
成功样本的幸存者偏差。所有 KPI 都要标 warmup、测量窗和 replication 聚合方式。</p>
"""
    body += "<p class=source-row>定义与页面：" + source_ref("src/superran/kpi_view.py", "CELL_KPIS") + " · 资源对账：" + source_ref("src/superran/experience.py", "TTI RBG occupancy") + "</p>"
    return Page(
        "kpi", "体验 KPI 与自适应呈现", "系统仿真", "KPI WORKBENCH",
        "首包/含头速率、PRB/MU 口径、用户级 CDF 与 Agent 可审计编排。", body,
        ("首包时延", "含头速率", "PRB利用率", "MU比例", "用户CDF", "KPI Tab"),
    )


def interference_page() -> Page:
    body = rb_power_svg()
    body += """
<h2>先把 S、I、N 拆对</h2>
""" + F_IOT
    body += """
<p>IoT 是干扰加噪声相对热噪声的抬升。<code>snr_dB−sinr_dB</code> 只有在两个量共享同一
信号/功率/聚合口径时才有意义；当前 first-party 后端恰好共享“预数字波束、每 RB”参考，
所以差值可作一致性旁证。项目主契约仍用同一样本的几何 SIR 与 SINR 反解 S/I/N，兼容
参考面未声明的外部/旧数据。SIR≈SINR 表示干扰可忽略，此时 IoT→0 dB，而不是无穷。</p>
<h2>邻区 PRB 负载</h2>
<p>ChannelHub 几何 SINR默认邻区都在发，相当于 100% 资源负载。系统场景通过
<code>neighbor_prb_util=η</code> 把干扰项缩为 ηI，同时保持 SIR/SINR 同口径；30% 是默认中载
场景，不是所有网络的事实。</p>
<h2>逐 RB 功率控制的精确耦合</h2>
""" + F_RB_COUPLING
    body += """
<p><code>q[c,r]</code> 同时作用于小区 c 在 RB r 上对自己 UE 的服务信号和对所有邻区 UE 的干扰。
每小区满足总功率/均值约束与逐 RB 上下界。计算路径保留 272 RB 到 MMSE SINR 后，再在线性域
聚合成 17 RBG；不能先压成中心 RB 后只改一个标量。InternalSim 与 Sionna RT 会在形成几何
预算时落下同参考面的 <code>S/N/I_k</code>，来源的 symbol 网格只对应一个 slot，因此元数据只保留
一个 slot 行，避免把 14 个 symbol 冒充 14 个 TTI。</p>
"""
    body += callout(
        "warn", "为什么 RBG0 抬升可能整体变差",
        "<p>RBG0 上本小区目标 UE 的 S 增强，但邻区 UE 的 I 也增强；总功率守恒又迫使本小区其他"
        "RBG 的 S 降低。若 RBG0 原本不是瓶颈、被调度概率低，或它造成的跨小区干扰代价大于本小区"
        "增益，整体 useful bytes/边缘体验就会下降。NEBF/PEBF 是空间维功率约束，RB power control"
        "是频域分配，两层必须作用到同一个物理 Q 后再算 SINR。</p>",
    )
    body += table(
        ["层", "对象", "守恒/约束", "错误捷径"],
        [
            ("空间", "Q[f,antenna,stream]", "总功率 P 或每天线 P/M", "只归一 W 方向却不核对物理 Q"),
            ("频率", "q[cell,RB]", "每小区跨 RB 均值/总和 + 上下界", "只改服务小区，不改它对邻区的干扰"),
            ("调度", "RBG bitmap", "物理共享 MU RBG 只计一次", "用 17-RBG 平均替代 grant subset"),
        ],
    )
    body += "<p class=source-row>IoT：" + source_ref("src/superran/interference.py", "def iot_db") + " · RB 耦合：" + source_ref("src/superran/power_control.py", "def couple_rb_power") + "</p>"
    return Page(
        "interference", "干扰、IoT 与 RB 功率控制", "可信度", "INTERFERENCE",
        "S/I/N、邻区负载与逐 RB 信号/干扰精确耦合。", body,
        ("IoT", "SIR", "SINR", "RB功控", "邻区负载"),
    )


def rng_page() -> Page:
    body = """
<h2>随机数按用途分流</h2>
<p><code>RngBook(master_seed, replication)</code> 使用稳定的 stream key，把 channel、neighbor_load、
traffic、scheduler、harq 等流彼此隔离。调用顺序改变不能改变某条流；新增用途必须先注册，禁止
临时从一个全局 RNG 多抽一次。</p>
""" + code(r'''books = rng.replications(master_seed=20260811, n=8)

# A/B 两臂复用同一批 RngBook
run_a = simulate_replications(tables_a, books=books, ...)
run_b = simulate_replications(tables_b, books=books, ...)
comparison = rng.compare_replications(run_a, run_b, books_a=books, books_b=books)
''')
    body += """
<h2>master seed、replication 与 CRN</h2>
""" + F_CRN
    body += table(
        ["量", "角色", "何时改变"],
        [
            ("master_seed", "一个实验宇宙 / ns-3 RngSeed", "换整批物理/业务宇宙时"),
            ("replication", "同一配置下独立重复 / ns-3 RngRun", "估计 KPI 分布与置信区间时"),
            ("stream name", "同一重复内的随机用途", "新增独立随机机制时注册"),
            ("CRN", "A/B 第 k 次使用相同 (master,replication,stream,event index)", "公平比较算法时必须"),
        ],
    )
    body += callout(
        "good", "事件索引也必须稳定",
        "<p>HARQ 与 scheduler tie-break 预先按 <code>[TTI,UE]</code> 生成，而不是“谁被调度才抽一次”。"
        "否则 A/B 调度路径一分叉，后续随机数立即错位，公共随机数名存实亡。</p>",
    )
    body += """
<h2>workers 只能改变调度，不能改变样本</h2>
<p>static <code>internal_sim</code> 的所有 worker 共享同一个 seed，并用不重叠的
<code>sample_index_offset</code> 切全局事件流；串行/并行的复信道、SINR、LSP 和 SRS 时序必须
逐样本逐位相同。旧做法给每个 worker 用不同 seed，会把一个固定 UE 几何的数据集变成多个
几何的混合分布，不能称为“统计等价”。移动轨迹、拒绝采样及尚无全局 index 的 source 当前
显式回退串行，摘要同时记录 requested/effective workers 和原因。</p>
<h3>耗时估计只用于调度，不是 SLA</h3>
<p>20-ray CDL 落地后，旧的 24 ms/样本标定失效。2026-08-11 热态锚点为：
1 cell/32T/20 MHz 约 0.158 s，1 cell/64T/100 MHz 约 1.074 s，
21 cells/16T/20 MHz 约 7.48 s；最后一组 24 样本串行/4 workers 为
179.5/49.3 s。冷态单样本又测到 1.15~3.03 s，说明初始化与缓存不能忽略。
<code>estimate_seconds()</code> 因而只做 worker 决策，实际运行必须读
<code>elapsed_s</code>。probe 的当前一组交错对照约 1.80x，也不是跨版本常数。</p>
"""
    body += """
<h2>为什么至少 6 次、默认 8 次</h2>
<p>一次系统仿真的末位数字只是一个 realization；项目对少于 6 次给硬警告，默认 8 次，并回传
均值、95% CI、标准差和 n。想分辨更小差异应做功效分析或增加 replication，不能仅靠延长单次
仿真假装获得独立样本。</p>
"""
    body += "<p class=source-row>实现：" + source_ref("src/superran/rng.py", "class RngBook") + " · 系统重复：" + source_ref("src/superran/system.py", "def simulate_replications") + "</p>"
    return Page(
        "rng", "随机数、重复实验与 CRN", "可信度", "RANDOMNESS",
        "稳定分流、RngRun 语义、事件索引和 A/B 公共随机数。", body,
        ("RngBook", "CRN", "replication", "HARQ stream"),
    )


def gates_page() -> Page:
    body = gates_svg()
    body += """
<h2>门 1 的 18 项当前清单</h2>
"""
    checks = [
        "路损对标 38.901", "CDL 表逐簇对标", "角度扩展对标", "场景/信道模型自洽",
        "小区数与配置一致", "干扰确实进入 SINR", "IoT 自洽", "基站阵列模型",
        "距离在公式范围", "路损不低于自由空间", "时延扩展与 profile", "Parseval 能量守恒",
        "SISO 退化到 Shannon", "谱效不超独立容量", "预编码排序合理", "估计误差合理",
        "蒙特卡洛收敛", "SINR 分布覆盖",
    ]
    body += '<ol class="check-grid">' + "".join(f"<li><span>{i}</span>{esc(name)}</li>" for i, name in enumerate(checks, 1)) + "</ol>"
    body += """
<h2>门 2：比较口径与统计</h2>
<p>两臂必须逐样本/逐 replication 可配对，除被测变量外配置一致；优先按独立 drop/position 聚类，
再对 cluster difference 做置信区间与 Wilcoxon。CI 跨 0、样本不足或 CRN 无法核实时，强结论阻断。</p>
<h2>门 3：可发布性</h2>
<p>生成前锁主指标/基线；报告效应绝对值、相对值、CI、n、检验和适用边界；支持性 KPI 不能替代
预注册主指标。失败时结论句必须写“不成立/证据不足”，不能用“总体来看”“趋势上”绕门。</p>
"""
    body += callout(
        "danger", "测试通过 ≠ 物理正确",
        "<p>Gate/测试能证明合同、自洽、不漂移；公司 BLER 曲线、实测 Jones 方向图、现场 CQI filter"
        "若没有独立外部数据，测试只能保护 hash/边界，不能证明模型等同真实网络。</p>",
    )
    body += """
<h2>字节与资源守恒</h2>
""" + F_CONSERVE
    body += """
<p>体验模式还逐 TTI 对账 RBG bitmap、MU 共享资源、scheduled/payload/padding、arrival/queue/ACK。
这些是系统结果可信的第一层；任何一项不守恒都应硬失败，而不是在 KPI 汇总时“修平”。</p>
"""
    body += "<p class=source-row>18 项列表：" + source_ref("src/superran/validate.py", "def full_report") + " · 三门统计：" + source_ref("src/superran/gates.py", "def paired_compare") + "</p>"
    return Page(
        "gates", "三道门与统计结论", "可信度", "EVIDENCE GATES",
        "18 项数据体检、配对/聚类统计与可发布结论边界。", body,
        ("Gate1", "Gate2", "Gate3", "Wilcoxon", "置信区间", "守恒"),
    )


def tools_page(tools: list[SymbolDoc]) -> Page:
    groups: list[tuple[str, tuple[str, ...]]] = [
        ("发现与规划", ("sr_capabilities", "sr_system_scene", "sr_list_presets", "sr_plan", "sr_revise", "sr_list_scenes", "sr_missing_slots")),
        ("生成与交付", ("sr_generate", "sr_deliver", "sr_describe_dataset", "sr_list_datasets", "sr_spec_sheet", "sr_await_config")),
        ("可信度与统计", ("sr_validate", "sr_calibrate", "sr_gate", "sr_compare_arms", "sr_sample_size", "sr_lock_analysis")),
        ("链路与吞吐", ("sr_link_performance", "sr_throughput", "sr_mcs_info", "sr_bler_curve", "sr_tdd_mcs", "sr_sweep_snr")),
        ("干扰与场景", ("sr_interference_report", "sr_iot_convert", "sr_design_interference", "sr_probe_scenario", "sr_compare_scenarios")),
        ("外部算法", ("sr_export_eval_template", "sr_compare_results", "sr_list_results")),
        ("系统仿真", ("sr_system_sim",)),
    ]
    by_name = {t.name: t for t in tools}
    seen: set[str] = set()
    sections = []
    for title, names in groups:
        cards = []
        for name in names:
            tool = by_name.get(name)
            if tool is None:
                continue
            seen.add(name)
            cards.append(
                f'<details class="api tool"><summary><code>{esc(tool.name)}</code><span>{esc(tool.doc)}</span></summary>'
                f'<div><pre class="signature">{esc(tool.signature)}</pre><p>{esc(tool.doc)}</p>'
                f'<p>{source_ref("src/superran/server.py", "def " + tool.name, "server.py:L" + str(tool.line))}</p></div></details>'
            )
        sections.append(f"<h2>{esc(title)}</h2>" + "".join(cards))
    missing = [t.name for t in tools if t.name not in seen]
    if missing:
        sections.append(callout("danger", "工具分类遗漏", "<p>" + esc(", ".join(missing)) + "</p>"))
    body = metric_cards((("当前工具数", str(len(tools)), "AST 自动扫描 server.py"),))
    body += """
<p>下列签名来自当前源码，不是手写摘要。MCP 工具只返回 JSON/路径/代码，不把大型 ndarray 塞进对话；
生成数据用 dataset_id 引用，外部算法用结果契约进入统计门。</p>
""" + "".join(sections)
    body += callout(
        "note", "默认不弹浏览器",
        "<p><code>sr_spec_sheet(open_browser=False)</code> 默认只返回 URL。只有用户明确要求时传 true；"
        "KPI 结果页同样应把路径/URL作为可审计产物，而不是依赖当前桌面焦点。</p>",
    )
    return Page(
        "tools", "34 个 MCP 工具", "平台接口", "MCP TOOL REFERENCE",
        "按工作流分组的全部 sr_* 签名、职责与源码入口。", body,
        tuple(t.name for t in tools),
    )


def skill_page(skills: list[dict[str, Any]]) -> Page:
    body = skill_flow_svg()
    body += """
<h2>Skill 不是提示词装饰</h2>
<p><code>channel-sim</code> 规定何时追问、计划如何收敛、门 1/2/3 何时阻断、CRN 如何保持、
系统级 A/B 如何写结论。它还规定可见计划恰为四项，避免用十几个待办制造“很专业”的错觉。</p>
"""
    rows = []
    for item in skills:
        headings = " · ".join(item["headings"][:8])
        if len(item["headings"]) > 8:
            headings += " · …"
        rows.append((
            f'<code>{esc(item["rel"])}</code>', str(item["lines"]), esc(headings),
        ))
    body += table(["文件", "行数", "承载内容"], rows, raw={0, 2})
    body += """
<h2>reference 路由</h2>
"""
    body += table(
        ["需要回答", "读取"],
        [
            ("怎样问清实验问题", "asking.md"),
            ("默认 64T4R/载波", "default-hardware.md"),
            ("Gate、统计与结论句", "gates-and-stats.md"),
            ("MCS/TBS/BLER/OLLA", "link-adaptation.md"),
            ("性能、样本数、并行", "performance.md"),
            ("压力测试与已知事故", "pressure-tests.md"),
            ("场景、干扰与 IoT", "scenarios-and-interference.md"),
            ("说明书/回传页面", "spec-sheet.md"),
            ("capacity/experience 系统仿真", "system-sim.md"),
        ],
    )
    body += callout(
        "danger", "HARD-GATE",
        "<p>未通过 Gate 时，不得直接报提升百分比，不得手算一个检验去“救”结论，不得把 notes 压成"
        "“仅供参考”。阻断是产品行为，不是写作风格。</p>",
    )
    body += "<p class=source-row>主 Skill：" + source_ref("skills/channel-sim/SKILL.md", "<HARD-GATE>") + "</p>"
    return Page(
        "skill", "channel-sim Skill 工作流", "平台接口", "AGENT WORKFLOW",
        "四阶段收敛、HARD-GATE 与全部 reference 的职责地图。", body,
        ("Skill", "HARD-GATE", "头脑风暴", "计划", "门"),
    )


def presets_page(presets: dict[str, Any]) -> Page:
    channel = presets.get("presets/presets.yaml", {})
    system = presets.get("presets/system_presets.yaml", {})
    body = metric_cards((
        ("信道预设", str(len(channel)), "拓扑 + 信道 + 测量配置"),
        ("系统场景", str(len(system)), "generate + system + evidence"),
        ("覆盖优先级", "用户 > preset > 默认", "最终 resolved_config 可审计"),
    ))
    body += """
<h2>配置不是一张扁平字典</h2>
<p>一个可复现实验由四层组成：信道预设给物理骨架；用户 overrides 改显式旋钮；
<code>plan.py</code> 把 64T4R 等人话展开为 ChannelHub 参数；系统仿真再追加话务、调度、
OLLA、MU 与 KPI 口径。最终写入数据集的是解析后的配置，不是用户输入的片段。</p>
"""
    body += code(r'''draft = sr_plan(
    intent="30% PRB 话务下比较 SU 与 MU 的用户体验",
    preset="company_64t4r_multicell",
    overrides={"channel_est_mode": "ls_mmse"},
)
# 人工确认 draft 后：sr_generate(...) -> sr_system_sim(...)
''')
    body += callout(
        "warn", "label 是意图，不是实测保证",
        "<p>预设名写“高干扰”不代表结果一定高 IoT。只有 <code>expect.measured=true</code> "
        "且带数据集、重复次数、模型版本和区间的锚点才能当证据；旧的 "
        "<code>legacy_v1_pre_physics_audit</code> 只用于历史回归。</p>",
    )

    def preset_cards(items: dict[str, Any], *, system_mode: bool) -> str:
        cards = []
        for name, item in items.items():
            item = item or {}
            label = str(item.get("label", name))
            summary = str(item.get("summary", ""))
            group = str(item.get("group", "未分组"))
            if system_mode:
                cfg = item.get("system", {}) or {}
                meta = (
                    f'channel=<code>{esc(item.get("channel_preset", "—"))}</code> · '
                    f'mode=<code>{esc(cfg.get("evaluation_mode", "—"))}</code> · '
                    f'traffic=<code>{esc(cfg.get("traffic_model", "—"))}</code>'
                )
                expect = item.get("expect", {}) or {}
                evidence = (
                    '<span class="badge ok">有实测锚点</span>'
                    if expect.get("measured") else '<span class="badge">未实测</span>'
                )
            else:
                cfg = item.get("config", {}) or {}
                meta = (
                    f'source=<code>{esc(cfg.get("source", "internal_sim"))}</code> · '
                    f'{esc(cfg.get("num_sites", 1))}站×{esc(cfg.get("sectors_per_site", 1))}扇区 · '
                    f'<code>{esc(cfg.get("channel_model", "—"))}</code>'
                )
                evidence = '<span class="badge">信道骨架</span>'
            cards.append(
                f'<details class="preset"><summary><span><small>{esc(group)}</small>'
                f'<strong><code>{esc(name)}</code> · {esc(label)}</strong></span>{evidence}</summary>'
                f'<div><p>{esc(summary) if summary else "无摘要"}</p><p class="muted">{meta}</p>'
                f'<pre class="mini-json">{esc(json.dumps(cfg, ensure_ascii=False, indent=2))}</pre></div></details>'
            )
        return "".join(cards)

    body += "<h2>信道预设全集</h2>" + preset_cards(channel, system_mode=False)
    body += "<h2>系统级场景全集</h2>" + preset_cards(system, system_mode=True)
    body += callout(
        "good", "对照组由代码校验",
        "<p><code>pair_with</code> 两边除 <code>pair_varies</code> 外必须逐字相同。"
        "这使“只改一个变量”从文案承诺变成可失败的契约。</p>",
    )
    body += "<p class=source-row>解析：" + source_ref("src/superran/plan.py", "def load_presets") + " · 系统预设：" + source_ref("src/superran/sysscenes.py", "def check_pairs") + "</p>"
    return Page(
        "presets", "配置、预设与场景契约", "平台接口", "CONFIGURATION",
        "全部信道/系统预设、覆盖规则、历史锚点边界与成对场景约束。", body,
        tuple(channel) + tuple(system) + ("resolved_config", "pair_with"),
    )


def extension_page() -> Page:
    body = """
<h2>扩展点的共同原则：先定义窄腰，再接入口</h2>
<p>新增功能不能只在 MCP 工具里“能调用”。它至少要同时有：数据合同、实现、反向测试、
可发现入口、Skill 路由、文档和发布边界。下面五条路径覆盖最常见扩展。</p>
"""
    body += steps((
        ("新增链路/调度算法", "<p>在独立模块实现纯函数或 dataclass；输入只取 Dataset/Phase-A 表，输出带版本和诊断。把算法挂进 profile 选择，不改基线默认。</p>"),
        ("新增 KPI", "<p>先写分子/分母、统计窗口、用户级与小区级聚合，再把字段加入 <code>ReplicationResult</code> 与 <code>kpi_view.py</code>；展示优先级不得改变数值。</p>"),
        ("新增 MCP 工具", "<p>在 <code>server.py</code> 增加公开 <code>sr_*</code>；返回 JSON/句柄，不返回 ndarray；补 server 测试并重建本页，工具数自动更新。</p>"),
        ("新增随机机制", "<p>先 <code>register_stream(name,purpose)</code>，再按稳定事件索引取 RNG；A/B 两臂必须能核验同 stream fingerprint。</p>"),
        ("新增场景/预设", "<p>预设写设计意图；只有跑过 Gate 与重复实验后才写 <code>expect.measured=true</code>。对照组声明 <code>pair_with/pair_varies</code>。</p>"),
    ))
    body += """
<h2>toy example：加一个用户级 jitter KPI</h2>
""" + code(r'''# 1) 口径：只在 KPI window 内，以每个用户成功 ACK 间隔计算
@dataclass
class UserKpi:
    ack_gap_jitter_ms: float

# 2) 仿真：记录原始 ACK 时间，最后统一聚合；不要在循环里滚动“修平”
gaps = np.diff(user_ack_times_s) * 1e3
value = float(np.std(gaps, ddof=1)) if len(gaps) >= 2 else np.nan

# 3) 展示：CELL_KPIS/USER_KPIS 注册定义、单位、方向和可画 CDF 属性
# 4) 测试：恒定间隔 -> 0；少于 2 个 gap -> NaN；warm-up ACK 不得进入
''')
    body += callout(
        "decision", "Agent 式展示的边界",
        "<p>LLM 可以根据用户问题重排 KPI、解释为什么优先；不能删掉不相关 KPI 的原始结果，"
        "不能改指标定义，也不能让同一仿真因提示词不同而产生不同数值。当前实现把排序证据与"
        "完整 KPI 一起写入 HTML/JSON。</p>",
    )
    body += table(
        ["扩展", "必须新增的哨兵", "最常见错误"],
        [
            ("算法", "基线退化 + 极端反例", "只测‘能跑’，不测方向"),
            ("KPI", "手算 toy trace", "统计窗口或分母漂移"),
            ("工具", "签名/工具数/JSON 可序列化", "把大数组塞回对话"),
            ("随机流", "调用顺序不变性", "从全局 RNG 临时多抽一次"),
            ("预设", "pair contract + Gate", "把 label 当实测结论"),
        ],
    )
    body += "<p class=source-row>KPI 页面：" + source_ref("src/superran/kpi_view.py", "def render_kpi_html") + " · RNG 注册：" + source_ref("src/superran/rng.py", "def register_stream") + " · 外部结果：" + source_ref("src/superran/results.py", "def register") + "</p>"
    return Page(
        "extension", "如何扩展而不破坏可信度", "平台接口", "EXTENSION GUIDE",
        "算法、KPI、工具、随机流和预设的端到端扩展清单。", body,
        ("extension", "KPI", "MCP", "register_stream", "preset"),
    )


def tests_page(tests: list[dict[str, Any]]) -> Page:
    lines = sum(item["lines"] for item in tests)
    checks = sum(item["check_sites"] + item["assert_sites"] for item in tests)
    body = metric_cards((
        ("测试文件", str(len(tests)), "tests/test_*.py 自动扫描"),
        ("测试代码", f"{lines:,} 行", "当前工作树"),
        ("静态断言点", str(checks), "check(...) + assert；非运行总数"),
    ))
    body += """
<h2>快速内环与重型验收</h2>
<p>测试文件不是同一种成本：公式/合同测试适合每次改动跑；信道生成、浏览器与多重复压力测试
用于阶段性验收。不要把“某次运行通过 1,227 项”写成永恒事实；测试总数由参数化和环境决定。</p>
"""
    rows = []
    for item in tests:
        purpose = " · ".join(item["sections"][:4]) or "以文件内断言为准"
        rows.append((
            f'<code>{esc(item["name"])}</code>', str(item["lines"]),
            str(item["check_sites"]), str(item["assert_sites"]), esc(purpose),
            source_ref(item["rel"], "", "源码"),
        ))
    body += table(
        ["文件", "行", "check", "assert", "章节/职责", "入口"], rows,
        raw={0, 4, 5},
    )
    body += """
<h2>本次全项目审计中当场修复的纰漏</h2>
"""
    audit_rows = [
        ("ChannelHub 跨站传播状态", "一个 UE 级 LOS/DS/SF 被复制到所有小区；扇区反而换 seed", "按 physical site 独立抽样；同站扇区共享状态与 cluster seed，方位角作用于阵列", "MSG-Platform 21/21"),
        ("自定义站点三扇区", "custom positions 永远只建 sector 0", "按 0/120/240° 展开，2站×3扇区 toy case 固定为 6 cells", "契约测试"),
        ("扇区服务选择", "azimuth_deg 不进 path gain，三扇区同功率、按列表先后胜出", "110° 水平阵子图给相对 sector gain；pathloss 保持纯传播量", "boresight 反例"),
        ("SRS 时序", "样本 idx 直接当 slot；可在 DL/guard slot 合成 SRS", "idx 映射到第 n 个满足 TDD+T_SRS+offset 的真实机会；无交集硬失败", "paired 3→13 slot toy"),
        ("SRS 带宽与跳频", "ChannelHub 只硬编码 C_SRS 0..17，默认 row 3；多级 F_b 有空循环且混淆 n_RRC/n_shift", "补全 64 行 38.211 表，分离 freqDomainPosition/freqDomainShift，奇偶 N_b 逐式实现；公司预设冻结 63/1/0、20 slot", "64 行×各 B_SRS/b_hop 穷举 + 17 跳覆盖 272 RB"),
        ("小载波 SRS 默认值", "Sionna/QuaDRiGa 固定 C_SRS=3；4 RB toy carrier 在历史 hopping 回看时映射到 RB[8,12) 并崩溃", "四种 source 均按实际载波自动选最宽合法 C_SRS；显式非法资源仍硬失败", "跨 backend 86 passed / 1 conditional skip"),
        ("CDL 标准表校准", "旧 A/B/C 角度错、D/E 行数短；新 dataclass 字段又让兼容覆盖 TypeError，异常被吞后继续生成", "MSG A~E 源表直接修正；兼容层只写已支持字段；shape mismatch 全表判错且校准异常阻断生成", "A/B/C/D/E 分别 23/23/24/14/15 行，逐字段 0 mismatch"),
        ("CDL ray 与 LOS", "每簇只生成一个 rank-1 方向，忽略 20-ray spread/XPR；D/E 又二次混 K；显式 UMa_LOS 仍随机出 NLOS", "20-ray 偏移/角耦合/逐 ray Jones+Doppler；D/E K 只用表功率；显式 LOS 强制 LOS/CDL-D", "CDL 定向 19/19 + LOS 反例"),
        ("配置/实际剖面", "摘要只突出 configured CDL-D，但 NLOS 链路实际由 CDL-C 生成，24-component 结果容易被误读成 D", "新增 configured_channel_model；repr、摘要与 E2E 同时展示 effective_channel_model_counts", "NLOS configured D→effective C 反例"),
        ("TDL/阵列链路预算", "TDL 缺少实际 ZOD/ZOA/Jones；有效阵子峰值和电下倾未进入 conducted link budget", "TDL LOS 接实际几何与 Jones；element×subarray absolute gain 进入预算，数字 BF 单独计算", "方向性功率、下倾与 physical-reference 等价哨兵"),
        ("CSI-RS DFT 码本", "physical.dft_codebook() 导入不存在的 csirs_precoding，真实调用直接崩", "补 2D oversampled DFT 码本、明确端口顺序；选 beam 时先算功率再跨时频平均", "8H×4V×2pol → (512,64)，unit norm"),
        ("LMMSE 路径", "只在 compact pilot grid 上平滑，再线性补洞；非均匀 SRS 不是 LMMSE；模式名漂移", "直接 R_tp(R_pp+R_v)^−1 从真实 pilot 映射所有目标 RB；公开配置 canonical 为 ls_mmse，ls_lmmse 是精确 alias", "非均匀位置 + 匹配先验 Monte Carlo + 高 SNR 极限"),
        ("业务域/测量域 SIR", "paired 估计完成后用 pilot-domain best SIR 覆盖 sir_dB，业务干扰画像被悄悄换域", "sir_dB 永久保留业务几何聚合；ul_sir_dB/dl_sir_dB 只承载导频估计域；metadata 声明 domain contract", "InternalSim + Sionna paired 反例"),
        ("总载波功率到每 RB", "tx_power_dbm 是全载波总功率、noise 是单 RB kTB+NF，但旧 SNR 漏减 10log10(N_RB)，273 RB 高估 24.36 dB", "两后端统一 P_RB=P_total/N_RB 并落 per_rb_tx_power_dbm；4→8 RB 精确下降 3.0103 dB", "两后端单元测试 + full/probe 解析重构"),
        ("链路级工作点锚点", "把几何 SINR 锚到 rank-1 σ₁²，抵消了 H 中数字 BF 增益；64T 权的真实增益被归一化抹掉", "first-party 标量明确为预数字波束每 RB；以 E[|H|²] 反标 I+N，rank-1 σ₁² 仅保留为诊断", "linklevel/MU/物理不变量反例"),
        ("几何 SINR 锚点测试", "旧断言把 rate-equivalent SINR 与线性功率域几何 SINR直接比较；20-ray 频选增强后 Jensen gap 被误报成重复 BF", "以逐 RB SINR 转线性后的均值核对几何锚点；另锁定 rate-equivalent 不高于功率均值", "实测锚点误差 0.014 dB，Jensen gap 0.335 dB"),
        ("逐小区 S/N/I 契约", "旧 _system_sinr 移除后，RB 功控依赖的 dl_signal/noise/interference metadata 一并消失，单元算法虽绿但真实数据无法启用", "InternalSim/Sionna 在同一几何预算落每 RB S/N/I_k；一个 symbol 网格只写一个 slot 行；缺字段仍硬失败", "source→NPZ→loader 重构误差 3.6e−15 dB"),
        ("Doppler 投影", "先把速度投影到最近站径向，CDL 再按每 ray 方向余弦投影，方向作用两次", "metadata 交付 f_max=|v|/λ 与完整速度方向，CDL 每 ray 只投影一次；static 仅冻结跨 snapshot 几何", "350 km/h @ 2.6 GHz = 842.59 Hz"),
        ("static Doppler 测试夹具", "预设没写 ue_speed_kmh 时，测试把缺失键当成 0；但 InternalSim 的公开默认是 3 km/h，实际 9.72 Hz 被误报为实现失败", "反例显式固定 static + 36 km/h，以 f_max=|v|/λ 核对；不再从可漂移的预设缺省推断期望", "完整 interference 回归 1002 s 全绿"),
        ("paired UL 天线轴测试", "旧单测期待公开 h_ul_true 保持物理 [UE,BS] 转置布局；实际 ChannelSample 窄腰早已统一为 [BS,UE]，把正确输出误判失败", "锁定两层语义：内部物理 H_UL=H_DL^H；返回时恢复 canonical [T,RB,BS,UE]，零校准误差时数值为 conj(H_DL)", "interference/bridge/mobility/export 定向 41 passed / 1 ONNX skip"),
        ("track 移动性测试", "MOBILITY_MODES 新增 track 后，两个遍历全模式的旧测试未传必需 waypoints，因 ValueError 失败", "为 track 夹具给显式两点轨道；形状与高度守恒继续覆盖全部模式", "test_mobility 定向回归"),
        ("Sionna RT 时变", "Receiver.velocity 未设置且 Paths.cfr 默认 1 Hz 采样，多个 symbol 实为静态重复；频率网格还从 0 单边展开", "写完整 UE 速度，CFR 采样率=1/平均 OFDM symbol 周期，RB 频率以载波中心对称", "真实 Munich RT symbol 演进反例"),
        ("Probe SRS 资源", "全带显式 C_SRS=63 覆盖 272 RB，直接塞进 24-RB probe 后越界", "probe-only 重新选最宽合法标准资源并报告 63→7；正式生成仍对显式非法配置硬失败", "company_64t4r probe 回归"),
        ("系统时间轴", "14 symbol 被误当 14 个 TTI 落盘", "14 symbol 先完成估计，再取中间 symbol 为 1 slot snapshot；禁复数平均", "64×4 E2E"),
        ("PF 平均速率", "按需 1 RBG 用户若用全带 best_se 记账会被约 17×过罚", "默认用实际 scheduled TBS credit，ACK bytes 只作独立 KPI", "experience invariant"),
        ("文档合同漂移", "33/34 tools、273/272 RB、17/18 Gate、默认弹浏览器等旧说法", "README/Skill/算法卡与源码统一，并加语义哨兵", "test_interference"),
        ("说明书 RB 粒度合同", "独立 algo_defs2 页面仍宣称‘RB 级没有算法使用’，与已上线的逐 RB 功控精确路径矛盾；且未说明几何量的预数字波束参考面", "明确区分功控关闭的中心 RB 快路径与功控开启的 272-RB→post-MMSE→RBG 路径，并写出 prebeam/per-RB/EESM 边界", "说明书关键短语哨兵 + 完整 interference 回归"),
        ("代码根/场景资产根", "当前 MSG-Platform 代码可用但不带 configs/scenes，能力探测报 Sionna 可用而场景列表为 0", "代码与资产独立发现；SUPERRAN_SCENES 可显式覆盖，候选目录必须真的含 JSON 才接受", "10 场景恢复 + prepare_scene 资产回归"),
        ("并行样本语义", "static 串行固定 1 个 UE 几何；旧 worker 各用 seed+id，4 进程混入 4 个几何，KS p=0", "ChannelHub 增加 sample_index_offset；同 seed 全局索引分块；有状态/拒绝采样路径显式回退", "workers=1/2/4 的 h_true、h_est、SINR 逐位一致"),
        ("带宽→RB 反查", "生成器曾用 0.95×BW/(12·SCS) 近似；随后共享表又把 FR1/FR2 字典覆盖合并，使 50/100 MHz@60 kHz 的 FR1 值被 FR2 静默替换", "按 FR1/FR2 保留独立 38.101 表；后端依据载频显式选 range；非标准组合硬失败，synthetic grid 要显式 num_rb", "20M@30k→51；50M@60k FR1/FR2=65/66；100M=135/132；9 单测"),
        ("系统字节守恒显示", "offered_mbps 先四舍五入到 3 位而 served_mbps 保留全精度；恰好供需平衡的 trace 被显示成多发送 0.000333 Mbps", "保留吞吐全精度并新增 offered_bytes/served_bytes 整数真相源；守恒断言只用整数", "100k TTI×8 UE 全系统 14 章 + 精确字节反例"),
        ("性能标定漂移", "20-ray 内核上线后仍宣称旧单簇 24 ms/样本、probe 11.5x，CI 又把硬编码旧数字打印成‘实测’", "重测热/冷与 21-cell 对照；估时降格为版本化调度启发式；文档、Skill、MCP 统一以 elapsed_s 为准", "0.158/1.074/7.48 s anchors；probe 交错两轮约 1.80x"),
    ]
    body += table(["问题", "原症状", "修复", "证据"], audit_rows)
    body += callout(
        "note", "测试证据的准确读法",
        "<p>最终工作树已通过两个仓库全仓 Ruff；MSG-Platform 新增/受影响路径为 NR RB 表 9 passed，"
        "export/interference/paired bridge/mobility 41 passed + 1 个缺 ONNX 的条件 skip。SuperRAN 的 "
        "interference 全套约 732 s、link adaptation 压力约 538 s、RNG/Gate/MCP 组合约 669 s、results 约 18 s；"
        "E2E、真实 RT、linklevel、MU、system、CSI-aging 与物理不变量均通过；浏览器全站 QA 在最终生成后单独执行。"
        "这些证据证明当前合同与反例，不替代公司实测方向图、完整 Type-I 多层码本或 BLER 曲线再标定。</p>",
    )
    return Page(
        "tests", "测试、压力验证与本次审计", "可信度", "VERIFICATION",
        "全测试文件地图、分层运行策略及本轮实现修复与证据。", body,
        tuple(item["name"] for item in tests) + ("audit", "regression"),
    )


def limitations_page() -> Page:
    body = """
<h2>已经拍板并写进默认行为</h2>
"""
    body += table(
        ["主题", "当前决定", "原因/影响"],
        [
            ("PF", "经典 PF；α=β=1、γ=0、无业务权重", "现场 EPF 定义未知前不自造厂商算法"),
            ("尾料 RBG", "业务传完即留空", "PRB 利用率反映真实话务，不虚构 padding 调度"),
            ("误块", "沿用当前 HARQ/队列语义", "下一阶段再细化丢弃/退回策略"),
            ("小 burst", "fractional-slot 推荐口径", "保留单时隙 burst，不制造体验 KPI 盲区"),
            ("预启动", "默认 1 s，PF/OLLA/SRS 演进但不计 KPI", "避开冷启动；结果仍检查收敛"),
            ("物理 SRS", "C_SRS=63/B_SRS=1/b_hop=0，T_SRS=20 slot", "30 kHz 下每 10 ms 发 16 RB，17 跳覆盖 272 RB；与系统级 srs_period_ms 分清单位"),
            ("PMI/CQI 周期", "20 ms 工程基线，可配置 5/10/20/40/80 ms", "协议是 slot 配置，不存在统一 5 ms"),
            ("SU/MU", "先 PF 排序；比较 useful bytes；SU 可清空则强制 SU", "超出队列的谱效不算收益"),
            ("MU 比例", "MU PRB / 已用 PRB", "不是 MU TTI / 全部 TTI"),
        ],
    )
    body += """
<h2>仍是工程近似，不能包装成标准真值</h2>
"""
    body += table(
        ["边界", "当前实现", "升级需要"],
        [
            ("阵子方向图", "110°×65° 参数化 3GPP-style cos/抛物近似，+45/−45° Jones", "公司实测复 Jones pattern、频率/温度/校准版本"),
            ("电下倾", "默认 6° 产品先验，可任意配置并进入 F", "实际 AAU 校准表与波束档位"),
            ("LMMSE", "真实 pilot→target 的频域 LMMSE；指数 PDP + 白噪声默认，时间仍线性", "实测/在线 PDP、Doppler/空间协方差、Kalman 或 2D LMMSE 路径"),
            ("宽带有效 SINR", "RBG 内线性均值；跨流/RBG dB 算术均值", "公司链路级标定后的 EESM/MIESM β"),
            ("PMI", "Type-I-style 工程码本与 SVD 上界", "严格 38.214 codebook subset/restriction/feedback pipeline"),
            ("MU", "SUS + ZF/RZF、pair table、用户级 MU-OLLA", "现场配对细则、最大用户/层数、接收机与 CSI error 标定"),
            ("BLER", "内置曲线与 OLLA 闭环", "公司 MCS×rank×TBS×场景曲线"),
            ("话务 CDF", "可插拔经验 CDF + 标量 size/interval 校准", "公司视频/XR/FTP CDF 文件与用户 mix"),
            ("CDL 几何", "标准 profile 的 20-ray 相对几何旋到实际链路；仍非场景确定性 ray tracing", "Sionna RT Paths 或实测 CIR/角度"),
        ],
    )
    body += callout(
        "danger", "最容易误读的三个词",
        "<p><strong>全带谱效</strong>是当前宽带聚合口径，不等于 EESM；"
        "<strong>真实谱效</strong>在 SU/MU 决策里指不计 padding 的 useful bytes，不等于实验真值；"
        "<strong>perfect CSI</strong>是上界臂，不是现场可实现方案。</p>",
    )
    body += """
<h2>下一批需要业务/产品拍板</h2>
<ol>
<li>现场 EPF 的确切公式：乘性/加性时延因子、HoL/平均时延、budget 来源。</li>
<li>公司 AAU 的实测 Jones pattern、6° 下倾来源与频段/波束校准编号。</li>
<li>MU 配对/层数/接收机的产品细节，以及用户级 MU-OLLA 是否需按场景再分状态。</li>
<li>公司话务 CDF 与 BLER 曲线；它们决定 30%/50% 负载校准是否有现场意义。</li>
<li>有效 SINR 是否引入 EESM/MIESM，以及 β 的链路级标定协议。</li>
</ol>
"""
    body += "<p class=source-row>默认配置：" + source_ref("src/superran/hardware.py", "DEFAULT_ELECTRICAL_DOWNTILT_DEG") + " · KPI/调度：" + source_ref("src/superran/experience.py", "def simulate_experience") + "</p>"
    return Page(
        "limitations", "当前限制、已决策项与路线图", "参考", "BOUNDARIES",
        "明确哪些是默认合同、哪些仍是近似、哪些必须等待产品数据。", body,
        ("limitations", "EPF", "EESM", "Jones", "roadmap"),
    )


def api_page(modules: list[ModuleDoc]) -> Page:
    symbol_count = sum(len(module.symbols) for module in modules)
    member_count = sum(len(symbol.members) for module in modules for symbol in module.symbols)
    body = metric_cards((
        ("Python 模块", str(len(modules)), "src/superran/*.py"),
        ("公开顶层符号", str(symbol_count), "非下划线 class/function"),
        ("公开成员/字段", str(member_count), "类内 method + annotated field"),
    ))
    body += """
<p>本页由 AST 从当前源码构建，签名、行号与首段 docstring 不手抄。它是“去哪找”的全量地图；
算法物理含义仍以前面的主题页为准。内部下划线函数未列入公开 API，但会在对应源码页出现。</p>
"""
    for module in modules:
        symbols = []
        for symbol in module.symbols:
            members = ""
            if symbol.members:
                member_rows = [
                    (
                        f'<code>{esc(member.name)}</code>', esc(member.kind),
                        f'<code>{esc(member.signature)}</code>', esc(member.doc),
                        source_ref(module.rel, "", f"L{member.line}"),
                    )
                    for member in symbol.members
                ]
                members = "<h4>公开成员</h4>" + table(
                    ["名称", "类型", "签名/字段", "说明", "源码"], member_rows,
                    raw={0, 2, 3, 4},
                )
            symbols.append(
                f'<details class="api"><summary><code>{esc(symbol.name)}</code>'
                f'<span>{esc(symbol.kind)} · L{symbol.line} · {esc(symbol.doc)}</span></summary>'
                f'<div><pre class="signature">{esc(symbol.signature)}</pre>{members}</div></details>'
            )
        body += (
            f'<section class="module-card" data-module="{esc(module.name)}"><h2>'
            f'<code>{esc(module.name)}</code></h2><p>{esc(module.doc)}</p>'
            f'<p class="muted">{module.lines:,} 行 · {len(module.symbols)} 个公开顶层符号 · '
            f'{source_ref(module.rel, "", module.rel)}</p>{"".join(symbols) if symbols else "<p>无公开顶层符号。</p>"}</section>'
        )
    return Page(
        "api", "全量 Python API 图谱", "参考", "API ATLAS",
        "由当前源码 AST 生成的全部模块、公开符号、签名、成员与源码链接。", body,
        tuple(module.name for module in modules) + ("API", "signature"),
    )


def glossary_page() -> Page:
    terms = [
        ("AE", "Antenna Element，物理阵子。64T 基线 192 个；公司 256T 为 1536 个；都不是同数量的独立 RF 链。"),
        ("RF port", "基带/射频可独立加权的端口。64T 为 64 个、每端口驱动 3 AE；256T 为 256 个、每端口驱动 6 AE。"),
        ("F", "被动馈电/耦合矩阵；64T 为 192×64，256T 为 1536×256。列范数 1，表达固定馈电、相位和下倾。"),
        ("configured / effective profile", "configured 是用户请求的剖面入口；effective 是按逐链路 LOS/NLOS 自洽后真正用于生成的剖面。"),
        ("EBF / PEBF / NEBF", "总功率 SVD 权 / 全局缩放满足每天线 / 每天线逐行归一。"),
        ("SRS 周期", "两次配置 SRS occasion 的周期；不要称 SRS 年龄。年龄是当前 CSI 距上次观测的时长。"),
        ("CQI", "长期/量化链路质量输入；当前发送 MCS 链不把当前 h_true 偷渡进 CQI。"),
        ("OLLA", "Outer Loop Link Adaptation；SU 与 MU 分开维护用户级 offset。"),
        ("RBG", "Resource Block Group。默认 16 RB，100 MHz/30 kHz 下 17 RBG=272 RB。"),
        ("TBS", "Transport Block Size，38.214 离散量化后的可发送字节/比特规模；对 RBG 单调但不线性。"),
        ("PF R_avg", "PF 的历史服务量。体验模式按实际 scheduled TBS credit 更新，不能记全带速率。"),
        ("useful bytes", "SU/MU 方案比较中真正属于队列的字节；超出业务包的 padding 不计。"),
        ("首包时延", "包到达/生成到第一次被调度的时长。"),
        ("掐头去尾速率", "busy period 去掉首包等待与尾端定义后的体验口径。"),
        ("含头速率", "与掐头去尾分子相同，但分母从首包到达开始，包含首包等待。"),
        ("PRB 利用率", "统计窗口内已用 PRB equivalent / 可用 PRB equivalent；也是话务校准目标，不是配置本身。"),
        ("MU 配对比例", "生效 MU 的 PRB equivalent / 已用 PRB equivalent。"),
        ("CRN", "Common Random Numbers；A/B 复用同一物理、话务、BLER、tie-break 事件流。"),
        ("Gate 1/2/3", "数据体检 / 结果统计可信 / 可发布性。上一门失败时不能跨门写强结论。"),
        ("capacity mode", "谱效评估型：持续可发或统一资源口径，回答承载能力。"),
        ("experience mode", "体验评估型：显式 packet/burst/FIFO/等待，回答有业务时用户多快。"),
    ]
    body = '<div class="glossary">' + "".join(
        f'<div><dt>{esc(term)}</dt><dd>{esc(definition)}</dd></div>'
        for term, definition in terms
    ) + "</div>"
    body += """
<h2>从问题反查源码</h2>
"""
    body += table(
        ["问题", "第一入口", "再追"],
        [
            ("64T/公司256T/阵子图/F", "hardware.py", "MSG-Platform effective_array.py"),
            ("H 如何生成/同站状态", "channelhub.py + generate.py", "MSG-Platform internal_sim.py / sionna_rt.py"),
            ("SRS/LMMSE/老化", "physical.py + csi_aging.py", "MSG-Platform ref_signals/channel_est"),
            ("EBF/PEBF/NEBF", "beamforming.py", "linklevel.py"),
            ("CQI/MCS/TBS/BLER", "linkadapt.py", "bler_data_20b.py"),
            ("SU/MU", "mumimo.py", "system.py / experience.py"),
            ("话务/PF/KPI", "traffic.py + experience.py", "kpi_view.py"),
            ("RB 功控/IoT", "power_control.py", "interference.py / system.py"),
            ("随机数/统计/Gate", "rng.py + gates.py", "validate.py / analysis.py"),
            ("Agent/MCP/Skill", "server.py", "skills/channel-sim/"),
        ],
    )
    body += callout(
        "note", "文档版本事实",
        "<p>页面页脚的构建清单来自当前工作树。源码链接指向 GitHub main；若本地改动尚未推送，"
        "本页签名比远端链接更新，应以本地文件和测试证据为准。</p>",
    )
    return Page(
        "glossary", "术语表与源码反查", "参考", "GLOSSARY",
        "无线、系统仿真与平台术语的项目内含义，以及问题到源码的最短路径。", body,
        tuple(term for term, _ in terms),
    )


DOC_CSS = r"""
:root{
  color-scheme:light;--bg:#f6f7f4;--paper:#fff;--ink:#18201d;--muted:#65716b;
  --line:#dce3de;--soft:#eef2ef;--brand:#0b6b5d;--brand2:#0d4f85;--warm:#b85f19;
  --danger:#aa342c;--ok:#1d7a4d;--shadow:0 12px 38px rgba(18,43,35,.08);
  --header-h:64px;--left-w:286px;--right-w:248px;--content-w:900px;
}
html[data-theme="dark"]{
  color-scheme:dark;--bg:#101614;--paper:#17201d;--ink:#e9f0ec;--muted:#9fada6;
  --line:#304039;--soft:#202c28;--brand:#5fd0bd;--brand2:#79b8ef;--warm:#f2a35f;
  --danger:#ff8178;--ok:#75d7a2;--shadow:0 15px 44px rgba(0,0,0,.28);
}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:84px}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.76;text-rendering:optimizeLegibility}
.sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
button,input{font:inherit}.skip{position:fixed;left:12px;top:-60px;z-index:99;padding:8px 14px;background:var(--paper);color:var(--ink);border:1px solid var(--brand);border-radius:8px}.skip:focus{top:10px}
.topbar{height:var(--header-h);position:fixed;inset:0 0 auto;z-index:30;background:color-mix(in srgb,var(--paper) 92%,transparent);backdrop-filter:blur(14px);border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 18px;gap:15px}
.brand{display:flex;align-items:center;gap:10px;width:calc(var(--left-w) - 18px);text-decoration:none;color:var(--ink);font-weight:780;letter-spacing:-.02em}.brand svg{width:35px;height:35px;flex:none}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:.14em;font-weight:700}
.search-wrap{position:relative;flex:1;max-width:720px}.search-wrap input{width:100%;height:40px;border:1px solid var(--line);background:var(--soft);color:var(--ink);border-radius:10px;padding:0 90px 0 40px;outline:none}.search-wrap input:focus{border-color:var(--brand);box-shadow:0 0 0 3px color-mix(in srgb,var(--brand) 18%,transparent)}
.search-icon{position:absolute;left:13px;top:8px;color:var(--muted)}.kbd{position:absolute;right:10px;top:8px;color:var(--muted);border:1px solid var(--line);border-bottom-width:2px;background:var(--paper);border-radius:5px;padding:0 7px;font-size:12px}
.top-actions{margin-left:auto;display:flex;gap:7px}.icon-btn{height:38px;min-width:38px;padding:0 11px;border:1px solid var(--line);border-radius:9px;background:var(--paper);color:var(--ink);cursor:pointer}.icon-btn:hover{border-color:var(--brand);color:var(--brand)}
.menu-btn{display:none}.progress{position:absolute;left:0;bottom:-1px;height:2px;background:var(--brand);width:0}
.sidebar{position:fixed;top:var(--header-h);bottom:0;left:0;width:var(--left-w);padding:20px 15px 32px 18px;overflow:auto;border-right:1px solid var(--line);background:var(--paper);z-index:20}
.nav-group{margin:0 0 20px}.nav-group h2{margin:0 8px 6px;font-size:11px;letter-spacing:.13em;color:var(--muted);text-transform:uppercase}.nav-group a{display:flex;gap:9px;align-items:center;padding:7px 9px;margin:2px 0;border-radius:8px;color:var(--muted);text-decoration:none;font-size:14px;line-height:1.35}.nav-group a span{font-variant-numeric:tabular-nums;font-size:11px;opacity:.65;width:19px}.nav-group a:hover{background:var(--soft);color:var(--ink)}.nav-group a.active{background:color-mix(in srgb,var(--brand) 12%,var(--paper));color:var(--brand);font-weight:700}
.side-meta{border-top:1px solid var(--line);padding:16px 9px 0;color:var(--muted);font-size:12px}.side-meta b{color:var(--ink)}
.toc{position:fixed;top:var(--header-h);bottom:0;right:0;width:var(--right-w);padding:27px 22px;overflow:auto;border-left:1px solid var(--line);background:var(--bg)}.toc strong{font-size:12px;letter-spacing:.1em}.toc a{display:block;padding:5px 0;color:var(--muted);text-decoration:none;font-size:13px;line-height:1.4}.toc a.h3{padding-left:13px;font-size:12px}.toc a:hover,.toc a.active{color:var(--brand)}
.main{margin-left:var(--left-w);margin-right:var(--right-w);padding:calc(var(--header-h) + 42px) 44px 90px;min-height:100vh}.doc-page{max-width:var(--content-w);margin:0 auto}.doc-page[hidden]{display:none!important}
.page-hero{padding:0 0 28px;border-bottom:1px solid var(--line);margin-bottom:34px}.eyebrow{font-weight:800;color:var(--brand);font-size:12px;letter-spacing:.15em}.page-hero h1{font-size:clamp(32px,4vw,50px);line-height:1.12;letter-spacing:-.045em;margin:10px 0 14px}.lead{font-size:19px;line-height:1.72;color:var(--muted);max-width:780px;margin:0}.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:18px}.tag,.badge{display:inline-flex;align-items:center;border:1px solid var(--line);background:var(--soft);color:var(--muted);border-radius:99px;padding:3px 9px;font-size:11px}.badge.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 35%,var(--line))}
h2{font-size:27px;line-height:1.3;letter-spacing:-.025em;margin:55px 0 16px;scroll-margin-top:84px}h3{font-size:20px;margin:34px 0 11px;scroll-margin-top:84px}h4{font-size:15px;margin:24px 0 9px}p{margin:10px 0 18px}a{color:var(--brand2);text-underline-offset:3px}strong{font-weight:750}code{font-family:"Cascadia Code",Consolas,monospace;font-size:.88em;background:var(--soft);border:1px solid color-mix(in srgb,var(--line) 70%,transparent);padding:.1em .32em;border-radius:5px;word-break:break-word}
.heading-link{border:0;background:transparent;color:var(--muted);font-size:.7em;opacity:0;margin-left:8px;cursor:pointer}.doc-page h2:hover .heading-link,.doc-page h3:hover .heading-link,.heading-link:focus{opacity:1}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:25px 0 34px}.metric{background:var(--paper);border:1px solid var(--line);border-radius:13px;padding:16px;box-shadow:0 4px 18px rgba(20,45,36,.035)}.metric span{display:block;color:var(--muted);font-size:12px}.metric b{display:block;font-size:25px;line-height:1.2;margin:5px 0;color:var(--brand)}.metric small{color:var(--muted)}
.callout{display:grid;grid-template-columns:31px 1fr;gap:12px;margin:24px 0;padding:17px 18px;border:1px solid var(--line);border-left:4px solid var(--brand2);background:color-mix(in srgb,var(--brand2) 5%,var(--paper));border-radius:10px}.callout p{margin:5px 0 0}.callout-icon{width:27px;height:27px;display:grid;place-items:center;border-radius:50%;background:var(--brand2);color:#fff;font-weight:800}.callout.good{border-left-color:var(--ok);background:color-mix(in srgb,var(--ok) 6%,var(--paper))}.callout.good .callout-icon{background:var(--ok)}.callout.warn,.callout.decision{border-left-color:var(--warm);background:color-mix(in srgb,var(--warm) 7%,var(--paper))}.callout.warn .callout-icon,.callout.decision .callout-icon{background:var(--warm)}.callout.danger{border-left-color:var(--danger);background:color-mix(in srgb,var(--danger) 6%,var(--paper))}.callout.danger .callout-icon{background:var(--danger)}
.steps{list-style:none;padding:0;margin:25px 0}.steps li{display:grid;grid-template-columns:38px 1fr;gap:13px;position:relative;padding:0 0 25px}.steps li:not(:last-child):before{content:"";position:absolute;left:18px;top:36px;bottom:0;border-left:1px solid var(--line)}.step-no{width:37px;height:37px;border-radius:50%;background:var(--brand);color:#fff;display:grid;place-items:center;font-weight:800}.steps p{margin:4px 0}
.table-wrap{overflow:auto;margin:20px 0 28px;border:1px solid var(--line);border-radius:11px;background:var(--paper)}table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.5}th{text-align:left;background:var(--soft);font-size:11px;letter-spacing:.04em;color:var(--muted);position:sticky;top:0}th,td{padding:11px 13px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}tbody tr:hover{background:color-mix(in srgb,var(--brand) 3%,transparent)}
.codebox{margin:21px 0 28px;border:1px solid var(--line);border-radius:11px;overflow:hidden;background:#101916;color:#dceae4;box-shadow:var(--shadow)}.codebar{height:38px;display:flex;align-items:center;justify-content:space-between;padding:0 12px;background:#192620;color:#9eb5ab;font-size:12px}.copy{border:1px solid #40544b;background:#22352d;color:#dceae4;border-radius:6px;padding:3px 9px;cursor:pointer}.codebox pre{margin:0;padding:17px 19px;overflow:auto;line-height:1.62}.codebox code{font-size:12.5px;background:none;border:0;padding:0;color:inherit;white-space:pre}.signature,.mini-json{overflow:auto;background:var(--soft);border:1px solid var(--line);padding:12px;border-radius:8px;font:12px/1.55 "Cascadia Code",Consolas,monospace;white-space:pre-wrap}.mini-json{max-height:310px;white-space:pre}
.diagram{margin:26px 0 34px;background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:15px;box-shadow:var(--shadow);overflow:auto}.diagram svg{display:block;width:100%;min-width:650px;height:auto}.diagram figcaption{text-align:center;color:var(--muted);font-size:12px;margin-top:7px}.diagram rect{fill:var(--soft);stroke:var(--line)}.diagram .accent rect{fill:color-mix(in srgb,var(--brand) 12%,var(--paper));stroke:var(--brand)}.diagram .good rect{fill:color-mix(in srgb,var(--ok) 10%,var(--paper));stroke:var(--ok)}.diagram .danger rect{fill:color-mix(in srgb,var(--danger) 9%,var(--paper));stroke:var(--danger)}.diagram .warn rect{fill:color-mix(in srgb,var(--warm) 11%,var(--paper));stroke:var(--warm)}.diagram text{font-family:inherit;fill:var(--ink)}.diagram .dt{font-size:14px;font-weight:750}.diagram .ds{font-size:11px;fill:var(--muted);text-anchor:middle}.diagram .b .ds,.diagram .accent .ds,.diagram .good .ds,.diagram .danger .ds,.diagram .warn .ds{text-anchor:start}.diagram .arr{stroke:var(--muted);stroke-width:1.5;fill:none}.diagram marker path{fill:var(--muted)}.diagram .al,.diagram .tiny{font-size:9px;fill:var(--muted);text-anchor:middle}.diagram .site{fill:color-mix(in srgb,var(--brand) 10%,var(--paper));stroke:var(--brand)}.diagram .site-t{font-size:12px;text-anchor:middle}.diagram .sector{stroke:var(--brand);stroke-width:3}.diagram .ae.polp{fill:#e45c5c;stroke:none}.diagram .ae.polm{fill:#357bd8;stroke:none}.diagram .feed{fill:none;stroke:var(--muted);stroke-dasharray:4 3}.diagram .slot{font-size:12px;fill:#fff;text-anchor:middle;font-weight:700}.diagram .brace,.diagram .axis,.diagram .cap{fill:none;stroke:var(--muted)}.diagram .bar{fill:var(--brand);stroke:none}.diagram .bar.bad{fill:var(--danger)}.diagram .hist{fill:var(--brand);stroke:none}.diagram .yes{font-size:11px;fill:var(--ok);text-anchor:middle}
.diagram .plot-panel{fill:color-mix(in srgb,var(--soft) 58%,var(--paper));stroke:var(--line)}.diagram .pattern-grid{fill:none;stroke:color-mix(in srgb,var(--muted) 38%,transparent);stroke-width:1}.diagram .pattern-axis{stroke:var(--line);stroke-width:1}.diagram .pattern-lobe{stroke-width:2.2}.diagram .pattern-lobe.horizontal{fill:color-mix(in srgb,var(--brand) 18%,transparent);stroke:var(--brand)}.diagram .pattern-lobe.element{fill:none;stroke:var(--muted);stroke-dasharray:6 4}.diagram .pattern-lobe.port{fill:color-mix(in srgb,var(--ok) 16%,transparent);stroke:var(--ok)}.diagram .pattern-tick{font-size:8px;fill:var(--muted);text-anchor:end}.diagram .pattern-note{font-size:10px;fill:var(--muted)}.diagram .hpbw{stroke:var(--warm);stroke-dasharray:4 3}.diagram .tilt-ray{stroke:var(--danger);stroke-width:1.5;stroke-dasharray:5 4}.diagram .legend{stroke-width:3}.diagram .legend.element{stroke:var(--muted);stroke-dasharray:6 4}.diagram .legend.port{stroke:var(--ok)}.diagram .physical-dot{fill:var(--ok);stroke:none}.diagram .index-cell.canonical{fill:color-mix(in srgb,var(--brand) 13%,var(--paper));stroke:var(--brand)}.diagram .index-cell.legacy{fill:color-mix(in srgb,var(--warm) 13%,var(--paper));stroke:var(--warm)}.diagram .index-text{font-size:12px;font-weight:700;text-anchor:middle}
.kx[data-display="1"]{display:block;overflow:auto;text-align:center;padding:13px 4px;margin:18px 0}.kx math{font-size:1.12em}.source-row{color:var(--muted);font-size:12px;border-top:1px dashed var(--line);padding-top:12px}.src{font-family:"Cascadia Code",Consolas,monospace;font-size:11px}.muted{color:var(--muted)}
details.api,details.preset{border:1px solid var(--line);background:var(--paper);border-radius:10px;margin:8px 0;overflow:hidden}details.api>summary,details.preset>summary{cursor:pointer;display:flex;gap:12px;align-items:flex-start;justify-content:space-between;padding:13px 15px;list-style:none}details.api>summary::-webkit-details-marker,details.preset>summary::-webkit-details-marker{display:none}details.api>summary:before,details.preset>summary:before{content:"+";color:var(--brand);font-weight:800}details[open]>summary:before{content:"−"}details.api>summary code{flex:none}details.api>summary span{color:var(--muted);font-size:12px;flex:1}details.api>div,details.preset>div{padding:0 16px 16px;border-top:1px solid var(--line)}details.preset>summary span{display:flex;flex-direction:column;gap:3px}details.preset>summary strong{font-size:14px}details.preset>summary small{color:var(--muted)}.module-card{border-top:3px solid var(--brand);padding-top:1px;margin-top:58px}.module-card>h2{margin-top:22px}.module-card>h2 code{font-size:.8em}.check-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:8px;list-style:none;padding:0}.check-grid li{background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:10px;font-size:13px}.check-grid li span{display:inline-grid;place-items:center;width:23px;height:23px;border-radius:50%;background:var(--soft);color:var(--brand);font-weight:800;margin-right:8px}.glossary>div{display:grid;grid-template-columns:185px 1fr;border-bottom:1px solid var(--line);padding:13px 0}.glossary dt{font-weight:800;color:var(--brand)}.glossary dd{margin:0;color:var(--muted)}
.page-nav{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:70px;border-top:1px solid var(--line);padding-top:23px}.page-nav a{display:block;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--paper);text-decoration:none}.page-nav a.next{text-align:right}.page-nav small{display:block;color:var(--muted)}
.search-panel{position:fixed;top:58px;left:calc(var(--left-w) + 20px);width:min(720px,calc(100vw - var(--left-w) - 300px));max-height:70vh;overflow:auto;z-index:50;background:var(--paper);border:1px solid var(--line);border-radius:12px;box-shadow:0 22px 70px rgba(0,0,0,.22);padding:8px}.search-panel[hidden]{display:none}.search-result{display:block;padding:10px 12px;border-radius:8px;text-decoration:none;color:var(--ink)}.search-result:hover,.search-result.active{background:var(--soft)}.search-result small{display:block;color:var(--muted)}.search-empty{padding:18px;color:var(--muted)}
.backdrop{display:none}.doc-footer{margin:55px auto 0;max-width:var(--content-w);color:var(--muted);font-size:12px;text-align:center}
@media(max-width:1180px){:root{--right-w:0px}.toc{display:none}.main{margin-right:0}}
@media(max-width:820px){.menu-btn{display:inline-block}.brand{width:auto;flex:1}.brand-text{display:none}.topbar{padding:0 10px}.search-wrap{position:absolute;left:57px;right:104px}.kbd{display:none}.sidebar{transform:translateX(-102%);transition:transform .2s ease;box-shadow:var(--shadow)}body.menu-open .sidebar{transform:none}.backdrop{display:block;position:fixed;inset:var(--header-h) 0 0;background:rgba(0,0,0,.38);z-index:19;opacity:0;pointer-events:none;transition:opacity .2s}body.menu-open .backdrop{opacity:1;pointer-events:auto}.main{margin-left:0;padding:calc(var(--header-h) + 30px) 22px 70px}.search-panel{left:12px;right:12px;width:auto}.page-hero h1{font-size:36px}.lead{font-size:17px}}
@media(max-width:500px){.main{padding-left:15px;padding-right:15px}.top-actions .print-btn{display:none}.search-wrap{right:57px}.page-hero h1{font-size:31px}.metrics{grid-template-columns:1fr 1fr}.metric b{font-size:20px}.callout{grid-template-columns:27px 1fr;padding:14px}.page-nav{grid-template-columns:1fr}.glossary>div{grid-template-columns:1fr;gap:4px}.diagram{margin-left:-5px;margin-right:-5px;padding:8px}.diagram::before{content:"\2194  \5de6\53f3\6ed1\52a8\67e5\770b\5b8c\6574\56fe";display:block;position:sticky;left:0;width:max-content;margin:0 0 7px;padding:4px 8px;border:1px solid var(--line);border-radius:999px;background:var(--paper);color:var(--muted);font-size:11px;letter-spacing:.02em}.table-wrap{margin-left:-4px;margin-right:-4px}.page-nav a.next{text-align:left}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
@media print{.topbar,.sidebar,.toc,.search-panel,.backdrop,.page-nav,.heading-link{display:none!important}.main{margin:0;padding:0}.doc-page[hidden]{display:block!important;page-break-before:always}.doc-page:first-child{page-break-before:auto}.diagram,.metric,.callout,details{break-inside:avoid;box-shadow:none}details>div{display:block!important}body{background:#fff;color:#111}.page-hero{padding-top:15mm}}
"""


DOC_JS = r"""
(function(){
  'use strict';
  const pages=window.__DOC_PAGES__||[];
  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>Array.from(r.querySelectorAll(s));
  const articles=new Map($$('.doc-page').map(a=>[a.dataset.page,a]));
  const navLinks=$$('.sidebar a[data-page]');
  const toc=$('#toc-links'), titleNode=$('#doc-title'), progress=$('#progress');
  let current=pages[0]&&pages[0].key;

  function cleanSlug(s){return s.trim().toLowerCase().replace(/[\s/]+/g,'-').replace(/[^\w\u3400-\u9fff-]/g,'').replace(/-+/g,'-')||'section'}
  articles.forEach((article,pageKey)=>{
    const used=new Set();
    $$('h2,h3',article).forEach(h=>{
      let id=cleanSlug(h.textContent), base=id, i=2; while(used.has(id))id=base+'-'+i++;
      used.add(id); h.id=id;
      const b=document.createElement('button'); b.className='heading-link'; b.type='button'; b.title='复制本节地址'; b.textContent='#';
      b.addEventListener('click',()=>copyText(location.href.split('#')[0]+'#/'+pageKey+'/'+id,b)); h.appendChild(b);
    });
  });

  function route(){
    const raw=(location.hash||'#/overview').replace(/^#\/?/,'');
    const parts=raw.split('/').filter(Boolean), key=articles.has(parts[0])?parts[0]:(pages[0]&&pages[0].key);
    const section=parts.slice(1).join('/');
    if(!key)return;
    const changed=current!==key; current=key;
    articles.forEach((a,k)=>a.hidden=k!==key);
    navLinks.forEach(a=>{const on=a.dataset.page===key;a.classList.toggle('active',on);if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')});
    const page=pages.find(p=>p.key===key); if(page){document.title=page.title+' · SuperRAN';titleNode.textContent=page.title}
    buildToc(articles.get(key)); document.body.classList.remove('menu-open');
    requestAnimationFrame(()=>{if(section&&document.getElementById(section))document.getElementById(section).scrollIntoView();else if(changed)window.scrollTo(0,0)});
  }
  function buildToc(article){
    toc.innerHTML=''; if(!article)return;
    $$('h2,h3',article).forEach(h=>{const a=document.createElement('a');a.href='#/'+current+'/'+h.id;a.textContent=h.childNodes[0].textContent.trim();if(h.tagName==='H3')a.className='h3';toc.appendChild(a)});
  }
  function copyText(textValue,button){
    const done=()=>{const old=button.textContent;button.textContent='已复制';setTimeout(()=>button.textContent=old,1000)};
    if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(textValue).then(done);else{const t=document.createElement('textarea');t.value=textValue;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();done()}
  }
  $$('.copy').forEach(b=>b.addEventListener('click',()=>copyText($('code',b.closest('.codebox')).textContent,b)));

  const themeBtn=$('#theme');
  function setTheme(value){document.documentElement.dataset.theme=value;themeBtn.textContent=value==='dark'?'☀':'◐';localStorage.setItem('sw-doc-theme',value)}
  const saved=localStorage.getItem('sw-doc-theme');setTheme(saved||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'));
  themeBtn.addEventListener('click',()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
  $('#menu').addEventListener('click',()=>document.body.classList.toggle('menu-open'));$('#backdrop').addEventListener('click',()=>document.body.classList.remove('menu-open'));
  $('#print').addEventListener('click',()=>window.print());

  const search=$('#search'), panel=$('#search-panel'); let searchItems=[];
  articles.forEach((a,key)=>{const p=pages.find(x=>x.key===key);searchItems.push({key,title:p.title,summary:p.summary,text:(a.textContent+' '+(p.tags||[]).join(' ')).toLowerCase()})});
  function doSearch(){const q=search.value.trim().toLowerCase();if(!q){panel.hidden=true;panel.innerHTML='';return}const tokens=q.split(/\s+/);const hits=searchItems.filter(x=>tokens.every(t=>x.text.includes(t))).slice(0,14);panel.innerHTML=hits.length?hits.map(x=>'<a class="search-result" href="#/'+x.key+'"><strong>'+escapeHtml(x.title)+'</strong><small>'+escapeHtml(x.summary)+'</small></a>').join(''):'<div class="search-empty">没有匹配页面；可尝试模块名、函数名或无线术语。</div>';panel.hidden=false}
  function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  search.addEventListener('input',doSearch);search.addEventListener('keydown',e=>{if(e.key==='Escape'){search.value='';panel.hidden=true;search.blur()}});
  document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap')&&!e.target.closest('.search-panel'))panel.hidden=true});
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();search.focus();search.select()}else if(e.key==='/'&&!/input|textarea/i.test(document.activeElement.tagName)){e.preventDefault();search.focus()}});
  window.addEventListener('scroll',()=>{const d=document.documentElement;const max=d.scrollHeight-d.clientHeight;progress.style.width=(max?100*d.scrollTop/max:0)+'%'});
  window.addEventListener('hashchange',route);route();
})();
"""


def render_page(page: Page, index: int, pages: list[Page]) -> str:
    prev_page = pages[index - 1] if index else None
    next_page = pages[index + 1] if index + 1 < len(pages) else None
    tags = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in page.tags[:10])
    prev_html = (
        f'<a href="#/{prev_page.key}"><small>← 上一页</small>{esc(prev_page.title)}</a>'
        if prev_page else "<span></span>"
    )
    next_html = (
        f'<a class="next" href="#/{next_page.key}"><small>下一页 →</small>{esc(next_page.title)}</a>'
        if next_page else "<span></span>"
    )
    return (
        f'<article class="doc-page" data-page="{esc(page.key)}" hidden>'
        f'<header class="page-hero"><div class="eyebrow">{index + 1:02d} · {esc(page.eyebrow)}</div>'
        f'<h1>{esc(page.title)}</h1><p class="lead">{esc(page.summary)}</p>'
        f'<div class="tags">{tags}</div></header>{page.body}'
        f'<nav class="page-nav" aria-label="前后章节">{prev_html}{next_html}</nav></article>'
    )


def build() -> str:
    modules = scan_modules()
    tools = scan_tools(modules)
    tests = scan_tests()
    skills = scan_skills()
    presets = scan_presets()

    pages = [
        overview_page(modules, tools, tests, skills), quickstart_page(), architecture_page(),
        hardware_page(), channel_page(), antenna_page(), srs_page(), measurements_page(modules),
        beamforming_page(), sinr_page(), linkadapt_page(), mu_page(),
        modes_page(), experience_page(), traffic_page(), kpi_page(),
        interference_page(), rng_page(), gates_page(), tests_page(tests),
        tools_page(tools), skill_page(skills), presets_page(presets), extension_page(),
        api_page(modules), limitations_page(), glossary_page(),
    ]
    groups: list[tuple[str, list[Page]]] = []
    for page in pages:
        if not groups or groups[-1][0] != page.group:
            groups.append((page.group, []))
        groups[-1][1].append(page)
    nav = []
    number = 1
    for group, members in groups:
        links = []
        for page in members:
            links.append(
                f'<a href="#/{page.key}" data-page="{page.key}"><span>{number:02d}</span>{esc(page.title)}</a>'
            )
            number += 1
        nav.append(f'<section class="nav-group"><h2>{esc(group)}</h2>{"".join(links)}</section>')

    page_json = json.dumps([
        {"key": p.key, "title": p.title, "summary": p.summary, "tags": list(p.tags)}
        for p in pages
    ], ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    meta = {
        "modules": len(modules),
        "source_lines": sum(m.lines for m in modules),
        "public_symbols": sum(len(m.symbols) for m in modules),
        "mcp_tools": len(tools),
        "test_files": len(tests),
        "test_lines": sum(t["lines"] for t in tests),
        "skill_files": len(skills),
        "logical_pages": len(pages),
        "katex_inline": kx.available(),
    }
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    articles = "".join(render_page(page, index, pages) for index, page in enumerate(pages))
    logo = (
        '<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="11" fill="#0b6b5d"/>'
        '<path d="M8 25c5-8 8-8 12 0s7 8 12 0M8 17c5-8 8-8 12 0s7 8 12 0" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/></svg>'
    )
    return (
        '<!doctype html>\n<html lang="zh-CN" data-theme="light"><head><meta charset="utf-8">'
        '<link rel="icon" href="data:,">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="description" content="SuperRAN 开发者文档：无线物理、链路算法、系统仿真、MCP、Skill、API 与验证。">'
        '<title>SuperRAN 开发者文档</title>' + kx.head_assets()
        + '<style>' + DOC_CSS + '</style></head><body>'
        + '<a class="skip" href="#main">跳到正文</a><header class="topbar">'
        + '<button id="menu" class="icon-btn menu-btn" type="button" aria-label="打开目录">☰</button>'
        + '<a class="brand" href="#/overview">' + logo + '<span class="brand-text">SuperRAN<small>DEVELOPER GUIDE</small></span></a>'
        + '<div class="search-wrap"><span class="search-icon">⌕</span><input id="search" type="search" autocomplete="off" placeholder="搜索算法、公式、函数、模块…" aria-label="全文搜索"><span class="kbd">Ctrl K</span></div>'
        + '<div class="top-actions"><button id="print" class="icon-btn print-btn" type="button" title="打印全部章节">⎙</button><button id="theme" class="icon-btn" type="button" title="切换主题">◐</button></div>'
        + '<div id="progress" class="progress"></div></header>'
        + '<aside class="sidebar" aria-label="章节目录">' + "".join(nav)
        + f'<div class="side-meta"><b>{len(pages)} 页 · {len(modules)} 模块 · {len(tools)} 工具</b><br>单文件 · 离线公式 · 源码可追溯</div></aside>'
        + '<div id="backdrop" class="backdrop"></div><div id="search-panel" class="search-panel" hidden></div>'
        + '<main id="main" class="main"><h1 id="doc-title" class="sr-only">SuperRAN</h1>' + articles
        + '<footer class="doc-footer">本页由 <code>scripts/make_developer_guide.py</code> 从当前源码、测试、预设与 Skill 自动构建。测试通过不等于现场标定完成。</footer></main>'
        + '<aside class="toc" aria-label="页内目录"><strong>本页目录</strong><nav id="toc-links"></nav></aside>'
        + '<script>window.__DOC_PAGES__=' + page_json + ';window.__DOC_META__=' + meta_json + ';</script>'
        + '<script>' + DOC_JS + '</script>' + kx.upgrade_script() + '</body></html>\n'
    )


def main() -> int:
    check = "--check" in sys.argv[1:]
    output = build()
    if check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != output:
            print(f"developer guide is stale: run {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"OK: {OUT} ({len(output):,} chars)")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(output, encoding="utf-8", newline="\n")
    meta_match = re.search(r"window\.__DOC_META__=(\{.*?\});", output)
    print(f"Wrote {OUT} ({len(output):,} chars) {meta_match.group(1) if meta_match else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
