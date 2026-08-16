"""Agent 自适应的系统级 KPI 离线结果页。

页面只重排已经算出的证据，不修改仿真配置或 KPI 数值。调用本工具的 LLM/Agent
可显式传 ``kpi_focus``；库内不暗调另一个模型。没有显式关注点时，使用可审计的
意图关键词与场景配置兜底，并把选择来源、标签和完整排序一起返回。
"""
from __future__ import annotations

import html
import math
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import bridge as br
from .paths import artifacts_root


@dataclass(frozen=True)
class KpiSpec:
    key: str
    label: str
    unit: str = ""
    percent: bool = False
    digits: int = 2
    tags: tuple[str, ...] = ()


CELL_KPIS = (
    KpiSpec("cell_experienced_mbps", "掐头去尾体验速率", "Mbps",
            tags=("experience", "fairness")),
    KpiSpec("cell_head_inclusive_experienced_mbps", "含头体验速率", "Mbps",
            tags=("experience", "latency")),
    KpiSpec("ue_experienced_p5_mbps", "5% 边缘用户体验速率", "Mbps",
            tags=("experience", "fairness")),
    KpiSpec("cell_served_mbps", "小区 ACK 吞吐", "Mbps",
            tags=("experience", "capacity")),
    KpiSpec("first_packet_delay_ms_mean", "首包时延均值", "ms",
            tags=("latency", "traffic")),
    KpiSpec("first_packet_delay_ms_p95", "首包时延 P95", "ms",
            tags=("latency", "traffic")),
    KpiSpec("first_packet_delay_observed_share", "首包时延观测覆盖", percent=True,
            tags=("latency", "reliability")),
    KpiSpec("small_completion_delay_ms_p95", "小包完成时延 P95", "ms",
            tags=("latency", "traffic")),
    KpiSpec("small_pdb_miss_ratio", "小包 PDB miss", percent=True,
            tags=("latency", "reliability")),
    KpiSpec("serving_cell_prb_utilization", "本小区 PRB 利用率", percent=True,
            tags=("resource", "traffic")),
    KpiSpec("mu_paired_prb_share_of_used", "MU 配对占已用 PRB", percent=True,
            tags=("resource", "mu")),
    KpiSpec("mu_paired_prb_utilization", "MU 配对占全部 PRB", percent=True,
            tags=("resource", "mu")),
    KpiSpec("allocated_prb_equivalent", "已用 PRB-equivalent", "PRB",  digits=0,
            tags=("resource",)),
    KpiSpec("offered_mbps", "测量窗 offered load", "Mbps",
            tags=("traffic", "capacity")),
    KpiSpec("avg_mcs", "平均 MCS", tags=("link",)),
    KpiSpec("avg_rank", "平均 Rank", tags=("link", "mu")),
    KpiSpec("bler_first_tx", "首传 BLER", percent=True,
            tags=("link", "reliability")),
    KpiSpec("su_bler_first_tx", "SU 首传 BLER", percent=True,
            tags=("link", "reliability")),
    KpiSpec("mu_bler_first_tx", "MU 首传 BLER", percent=True,
            tags=("link", "mu", "reliability")),
    KpiSpec("payload_fill_ratio", "Payload fill", percent=True,
            tags=("resource", "traffic")),
    KpiSpec("padding_ratio", "Padding 比例", percent=True,
            tags=("resource", "traffic")),
    KpiSpec("backlog_bytes", "窗口末积压", "bytes", digits=0,
            tags=("traffic", "latency")),
)

USER_KPIS = (
    KpiSpec("experienced_mbps", "用户掐头去尾体验速率", "Mbps",
            tags=("experience", "fairness")),
    KpiSpec("head_inclusive_experienced_mbps", "用户含头体验速率", "Mbps",
            tags=("experience", "latency", "fairness")),
    KpiSpec("served_mbps", "用户 ACK 吞吐", "Mbps",
            tags=("experience", "capacity")),
    KpiSpec("first_packet_delay_ms_mean", "用户首包时延均值", "ms",
            tags=("latency", "traffic")),
    KpiSpec("first_packet_delay_ms_p95", "用户首包时延 P95", "ms",
            tags=("latency", "traffic")),
    KpiSpec("first_packet_delay_observed_share", "首包时延观测覆盖", percent=True,
            tags=("latency", "reliability")),
    KpiSpec("arrival_completion_delay_p95_ms", "用户包完成时延 P95", "ms",
            tags=("latency", "traffic")),
    KpiSpec("arrival_pdb_miss_ratio", "用户 PDB miss", percent=True,
            tags=("latency", "reliability")),
    KpiSpec("avg_mcs", "用户平均 MCS", tags=("link",)),
    KpiSpec("avg_rank", "用户平均 Rank", tags=("link", "mu")),
    KpiSpec("bler_first_tx", "用户首传 BLER", percent=True,
            tags=("link", "reliability")),
    KpiSpec("sched_tti", "用户调度次数", "TTI", digits=0,
            tags=("resource", "fairness")),
    KpiSpec("queued_bytes", "用户窗口末积压", "bytes", digits=0,
            tags=("traffic", "latency")),
    KpiSpec("grant_prb_equivalent", "用户 grant PRB exposure", "PRB", digits=0,
            tags=("resource",)),
    KpiSpec("allocated_prb_equivalent_attributed", "用户归因 PRB", "PRB", digits=0,
            tags=("resource", "fairness")),
    KpiSpec("cell_used_prb_attribution_share", "用户占小区已用 PRB", percent=True,
            tags=("resource", "fairness")),
    KpiSpec("mu_paired_prb_share_of_user_used", "用户 MU 配对 PRB 比例", percent=True,
            tags=("resource", "mu")),
    KpiSpec("mu_tx_share", "用户 MU grant 比例", percent=True,
            tags=("mu", "resource")),
    KpiSpec("geo_sinr_db", "用户几何 SINR", "dB", tags=("link", "fairness")),
    KpiSpec("iot_db", "用户 IoT", "dB", tags=("link", "fairness")),
)

_KEYWORDS = {
    "experience": ("体验", "速率", "吞吐", "throughput", "experience", "视频", "xr"),
    "latency": ("时延", "等待", "首包", "pdb", "latency", "delay", "xr"),
    "resource": ("prb", "rbg", "资源", "利用率", "负载", "load", "occupancy"),
    "traffic": ("话务", "包长", "包间隔", "cdf", "traffic", "arrival", "视频", "xr"),
    "mu": ("mu", "配对", "相关性", "corr", "multi-user"),
    "link": ("mcs", "bler", "sinr", "cqi", "olla", "bf", "链路", "误块"),
    "fairness": ("用户", "边缘", "公平", "cdf", "p5", "fairness"),
    "reliability": ("可靠", "bler", "pdb", "误块", "reliability"),
    "capacity": ("容量", "capacity", "throughput", "吞吐"),
}

_PALETTE = ("#1769aa", "#cf6b35", "#209567", "#8f5bb7", "#c49717",
            "#317f88", "#be4e72", "#65758b")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _strong(text: str) -> str:
    escaped = _esc(text)
    parts = escaped.split("**")
    return "".join(
        f"<strong>{part}</strong>" if i % 2 else part
        for i, part in enumerate(parts)
    )


def _stat(container: dict[str, Any], key: str) -> tuple[
        float | None, list[float] | None, int | None]:
    value = container.get(key)
    if isinstance(value, dict) and "mean" in value:
        mean = value.get("mean")
        raw_ci = value.get("ci95")
        ci = ([float(x) for x in raw_ci]
              if isinstance(raw_ci, list) and len(raw_ci) == 2
              and all(isinstance(x, (int, float)) and math.isfinite(float(x))
                      for x in raw_ci) else None)
        return (
            (float(mean) if isinstance(mean, (int, float))
             and math.isfinite(float(mean)) else None),
            ci,
            int(value["n_rep"]) if isinstance(value.get("n_rep"), int) else None,
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        return (parsed, None, 1) if math.isfinite(parsed) else (None, None, None)
    return None, None, None


def _fmt(value: float | None, spec: KpiSpec, *, include_unit: bool = False) -> str:
    if value is None:
        return "n/a"
    shown = value * 100.0 if spec.percent else value
    if spec.percent:
        shown = min(100.0, max(0.0, shown))
    elif spec.key not in {"geo_sinr_db", "iot_db"}:
        shown = max(0.0, shown)
    suffix = "%" if spec.percent else (f" {spec.unit}" if include_unit and spec.unit else "")
    return f"{shown:.{spec.digits}f}{suffix}"


def _available_cell(cell: dict[str, Any], spec: KpiSpec) -> bool:
    return _stat(cell, spec.key)[0] is not None


def _available_user(users: list[dict[str, Any]], spec: KpiSpec) -> bool:
    return any(_stat(row, spec.key)[0] is not None for row in users)


def _infer_tags(text: str) -> set[str]:
    lowered = text.casefold()
    return {
        tag for tag, words in _KEYWORDS.items()
        if any(word.casefold() in lowered for word in words)
    }


def _scenario_tags(result: dict[str, Any]) -> tuple[set[str], list[str]]:
    tags = {"experience"}
    reasons = ["experience_v2 默认关注体验速率"]
    config = result.get("config") if isinstance(result.get("config"), dict) else {}
    traffic = config.get("traffic") if isinstance(config.get("traffic"), dict) else {}
    scheduler = config.get("scheduler") if isinstance(config.get("scheduler"), dict) else {}
    model = str(traffic.get("model", ""))
    if model in ("mixed", "cdf", "ftp3"):
        tags.update(("traffic", "latency"))
        reasons.append(f"话务模型 {model or 'unknown'} 需要同时检查到达与时延")
    if bool(scheduler.get("mu_enabled")):
        tags.update(("mu", "resource"))
        reasons.append("MU 已启用，优先检查配对资源与 MU 链路质量")
    if isinstance(result.get("traffic_calibration"), dict):
        tags.update(("resource", "traffic"))
        reasons.append("本次含目标 PRB 校准，优先核对实测利用率与话务")
    tags.add("fairness")
    reasons.append("用户级页面默认保留边缘与公平性视角")
    return tags, reasons


def _rank_specs(specs: Iterable[KpiSpec], available: set[str], focus: list[str],
                tags: set[str]) -> list[KpiSpec]:
    focus_norm = [item.casefold().strip() for item in focus]
    indexed = list(specs)

    def score(item: tuple[int, KpiSpec]) -> tuple[float, int]:
        idx, spec = item
        haystack = f"{spec.key} {spec.label}".casefold()
        value = float(len(indexed) - idx) / 1000.0
        for word in focus_norm:
            if word == spec.key.casefold():
                value += 1000.0
            elif word in haystack:
                value += 400.0
        value += 80.0 * len(set(spec.tags) & tags)
        return value, -idx

    ranked = sorted(
        ((idx, spec) for idx, spec in enumerate(indexed) if spec.key in available),
        key=score, reverse=True)
    return [spec for _, spec in ranked]


def select_kpis(result: dict[str, Any], *, kpi_focus: list[str] | None = None,
                kpi_intent: str = "") -> dict[str, Any]:
    """生成可审计的 KPI 排序；不调用隐藏模型，也不改变任何数值。"""
    cell = result.get("cell") if isinstance(result.get("cell"), dict) else {}
    users = result.get("users") if isinstance(result.get("users"), list) else []
    explicit = [str(item).strip() for item in (kpi_focus or []) if str(item).strip()]
    scenario, scenario_reasons = _scenario_tags(result)
    if explicit:
        source = "llm_agent_explicit"
        inferred = _infer_tags(" ".join(explicit))
        tags = scenario | inferred
        reasons = [f"调用 Agent 显式关注：{', '.join(explicit)}", *scenario_reasons]
    elif str(kpi_intent).strip():
        source = "intent_inference"
        inferred = _infer_tags(str(kpi_intent))
        tags = scenario | inferred
        reasons = [f"从用户意图摘要推断：{str(kpi_intent).strip()}", *scenario_reasons]
    else:
        source = "scenario_fallback"
        tags = scenario
        reasons = scenario_reasons
    available_cell = {spec.key for spec in CELL_KPIS if _available_cell(cell, spec)}
    available_user = {spec.key for spec in USER_KPIS if _available_user(users, spec)}
    ranked_cell = _rank_specs(CELL_KPIS, available_cell, explicit, tags)
    ranked_user = _rank_specs(USER_KPIS, available_user, explicit, tags)
    return {
        "source": source,
        "focus": explicit,
        "intent": str(kpi_intent).strip(),
        "resolved_tags": sorted(tags),
        "reasons": reasons,
        "cell": {
            "prioritized": [spec.key for spec in ranked_cell[:8]],
            "collapsed": [spec.key for spec in ranked_cell[8:]],
            "ranked": [spec.key for spec in ranked_cell],
        },
        "user": {
            "prioritized": [spec.key for spec in ranked_user[:6]],
            "collapsed": [spec.key for spec in ranked_user[6:]],
            "ranked": [spec.key for spec in ranked_user],
        },
    }


def _spec_map(specs: Iterable[KpiSpec]) -> dict[str, KpiSpec]:
    return {spec.key: spec for spec in specs}


def _card(cell: dict[str, Any], spec: KpiSpec) -> str:
    mean, ci, n_rep = _stat(cell, spec.key)
    ci_text = ""
    if ci is not None:
        lo = _fmt(ci[0], spec)
        hi = _fmt(ci[1], spec)
        ci_text = f'<span class="ci">95% CI [{lo}, {hi}] · n={n_rep}</span>'
    unit = "" if spec.percent else spec.unit
    return (
        '<article class="kpi"><span class="label">' + _esc(spec.label) + "</span>"
        + "<strong>" + _fmt(mean, spec) + "</strong>"
        + '<span class="unit">' + _esc(unit) + "</span>" + ci_text
        + '<small class="key">' + _esc(spec.key) + "</small></article>"
    )


def distribution_title(cell: dict[str, Any]) -> str:
    """按实际载波给出分布图标题。

    标题早先写死"0..17"，那是 100 MHz / 272 RB 的桶数。载波栅格改成跟着数据集走
    之后，20 MHz 的小区只有 0..3 个 RBG，写死的标题会直接说错。
    """
    dist = cell.get("tti_occupied_rbg_distribution")
    top = None
    if isinstance(dist, dict):
        bins = dist.get("bins")
        if isinstance(bins, list) and bins:
            top = len(bins) - 1
        elif isinstance(dist.get("num_rbg"), (int, float)):
            top = int(dist["num_rbg"])
    return f"逐 TTI 0..{top} RBG 占比分布" if top is not None else "逐 TTI RBG 占比分布"


def _distribution(cell: dict[str, Any]) -> str:
    dist = cell.get("tti_occupied_rbg_distribution")
    if not isinstance(dist, dict) or not isinstance(dist.get("bins"), list):
        return '<div class="empty">本次结果没有逐 TTI RBG 分布。</div>'
    rows: list[tuple[int, float, list[float] | None]] = []
    for row in dist["bins"]:
        if not isinstance(row, dict):
            continue
        mean, ci, _ = _stat(row, "tti_share")
        if mean is not None:
            rows.append((int(row.get("occupied_rbg", len(rows))), mean, ci))
    if not rows:
        return '<div class="empty">逐 TTI RBG 分布为空。</div>'
    max_share = max(share for _, share, _ in rows)
    bars: list[str] = []
    for occupied, share, ci in rows:
        height = 4.0 if max_share <= 0 else max(4.0, 210.0 * share / max_share)
        tip = f"{occupied} RBG：{share:.2%}"
        if ci:
            tip += f"；95% CI [{max(0.0, ci[0]):.2%}, {min(1.0, ci[1]):.2%}]"
        bars.append(
            '<div class="bar-col" title="' + _esc(tip) + '">'
            + f'<span class="bar-value">{share:.1%}</span>'
            + f'<div class="bar" style="height:{height:.1f}px"></div>'
            + f"<b>{occupied}</b></div>"
        )
    # **列数必须跟着桶数走。** CSS 里写死 repeat(18,...) 是按 272 RB / 17 RBG 定的；
    # 载波栅格改成跟数据集推导之后，20 MHz 小区只有 4 个桶，柱子会被挤在左边
    # 五分之一处、右边一大片空白——图没坏，但读者会以为右边的桶全是 0。
    return (
        f'<div class="rbg-chart" role="img" aria-label="逐 TTI 占用 RBG 分布" '
        f'style="grid-template-columns:repeat({len(rows)},minmax(24px,1fr))">'
        + "".join(bars) + "</div>"
        + '<p class="axis">横轴：测量窗每个可用 DL TTI 的占用 RBG 数（共 '
        + f"{len(rows)} 个桶，0..{len(rows) - 1}）；"
        "纵轴：TTI 占比。0 桶明确包含 idle TTI。</p>"
    )


def _load_gauge(cell: dict[str, Any]) -> str:
    value, _, _ = _stat(cell, "serving_cell_prb_utilization")
    pct = max(0.0, min(100.0, (value or 0.0) * 100.0))
    return (
        '<div class="gauge"><div class="track"><div class="fill" '
        + f'style="width:{pct:.3f}%"></div>'
        + '<span class="mark m10">10%</span><span class="mark m30">30%</span>'
        + '<span class="mark m50">50%</span><span class="needle" '
        + f'style="left:{pct:.3f}%"></span></div>'
        + f"<p>正式仿真实测 <strong>{pct:.2f}%</strong>；参考线不是硬编码结果。</p></div>"
    )


def _profile_colours(users: list[dict[str, Any]]) -> dict[str, str]:
    names = sorted({str(row.get("traffic_class", "unknown")) for row in users})
    return {name: _PALETTE[i % len(_PALETTE)] for i, name in enumerate(names)}


def _scaled_value(value: float, spec: KpiSpec) -> float:
    scaled = value * 100.0 if spec.percent else value
    if spec.percent:
        return min(100.0, max(0.0, scaled))
    if spec.key not in {"geo_sinr_db", "iot_db"}:
        return max(0.0, scaled)
    return scaled


def _user_bar_svg(users: list[dict[str, Any]], spec: KpiSpec,
                  colours: dict[str, str]) -> str:
    points: list[tuple[int, float, list[float] | None, str]] = []
    for row in users:
        mean, ci, _ = _stat(row, spec.key)
        if mean is None:
            continue
        scaled_ci = ([_scaled_value(ci[0], spec), _scaled_value(ci[1], spec)]
                     if ci else None)
        points.append((int(row.get("ue", len(points))), _scaled_value(mean, spec),
                       scaled_ci, str(row.get("traffic_class", "unknown"))))
    if not points:
        return '<div class="empty">无用户级数据。</div>'
    width = max(720, 94 + 52 * len(points))
    height, left, top, bottom = 270, 70, 20, 220
    max_value = max([max(0.0, value) for _, value, _, _ in points]
                    + [max(0.0, ci[1]) for _, _, ci, _ in points if ci])
    min_value = min([min(0.0, value) for _, value, _, _ in points]
                    + [min(0.0, ci[0]) for _, _, ci, _ in points if ci])
    if abs(max_value - min_value) < 1e-12:
        max_value = min_value + 1.0
    chart_h = bottom - top

    def y(value: float) -> float:
        return top + (max_value - value) / (max_value - min_value) * chart_h

    baseline = y(0.0) if min_value < 0 else bottom
    elements = [
        f'<line x1="{left}" y1="{bottom}" x2="{width - 12}" y2="{bottom}" class="axisline"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axisline"/>',
    ]
    for tick in range(5):
        value = min_value + (max_value - min_value) * tick / 4.0
        yy = y(value)
        elements.append(
            f'<line x1="{left}" y1="{yy:.2f}" x2="{width - 12}" y2="{yy:.2f}" '
            'class="gridline"/>'
            f'<text x="{left - 8}" y="{yy + 4:.2f}" text-anchor="end">{value:.2g}</text>')
    for index, (ue, value, ci, profile) in enumerate(points):
        x = left + 18 + index * 52
        yy = y(value)
        rect_y = min(yy, baseline)
        rect_h = max(1.0, abs(baseline - yy))
        colour = colours.get(profile, _PALETTE[0])
        tip = f"UE {ue} · {profile} · {_fmt(value / (100.0 if spec.percent else 1.0), spec)}"
        elements.append(
            f'<rect x="{x}" y="{rect_y:.2f}" width="28" height="{rect_h:.2f}" '
            f'fill="{colour}" rx="3"><title>{_esc(tip)}</title></rect>')
        if ci:
            y_lo, y_hi = y(ci[0]), y(ci[1])
            elements.append(
                f'<line x1="{x + 14}" y1="{y_hi:.2f}" x2="{x + 14}" '
                f'y2="{y_lo:.2f}" class="whisker"/>'
                f'<line x1="{x + 8}" y1="{y_hi:.2f}" x2="{x + 20}" '
                f'y2="{y_hi:.2f}" class="whisker"/>'
                f'<line x1="{x + 8}" y1="{y_lo:.2f}" x2="{x + 20}" '
                f'y2="{y_lo:.2f}" class="whisker"/>')
        elements.append(
            f'<text x="{x + 14}" y="{bottom + 18}" text-anchor="middle">{ue}</text>')
    unit = "%" if spec.percent else spec.unit
    elements.append(
        f'<text x="{left}" y="12" class="axislabel">{_esc(unit or spec.label)}</text>')
    return (
        '<div class="svg-scroll"><svg class="user-svg" role="img" '
        f'aria-label="{_esc(spec.label)} 按用户柱状图" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">' + "".join(elements) + "</svg></div>"
    )


def _user_cdf_svg(users: list[dict[str, Any]], spec: KpiSpec,
                  colours: dict[str, str]) -> str:
    points: list[tuple[float, int, str]] = []
    for row in users:
        mean, _, _ = _stat(row, spec.key)
        if mean is not None:
            points.append((_scaled_value(mean, spec), int(row.get("ue", 0)),
                           str(row.get("traffic_class", "unknown"))))
    points.sort(key=lambda item: (item[0], item[1]))
    if not points:
        return '<div class="empty">无用户级 CDF 数据。</div>'
    width, height, left, right, top, bottom = 720, 270, 72, 24, 20, 220
    low, high = points[0][0], points[-1][0]
    if abs(high - low) < 1e-12:
        pad = max(abs(low) * 0.05, 1.0)
        low, high = low - pad, high + pad

    def x(value: float) -> float:
        return left + (value - low) / (high - low) * (width - left - right)

    def y(probability: float) -> float:
        return bottom - probability * (bottom - top)

    coords: list[str] = []
    dots: list[str] = []
    for index, (value, ue, profile) in enumerate(points, start=1):
        probability = index / len(points)
        xx, yy = x(value), y(probability)
        coords.append(f"{xx:.2f},{yy:.2f}")
        colour = colours.get(profile, _PALETTE[0])
        dots.append(
            f'<circle cx="{xx:.2f}" cy="{yy:.2f}" r="4" fill="{colour}">'
            f'<title>UE {ue} · {profile} · CDF {probability:.1%}</title></circle>')
    elements = [
        f'<line x1="{left}" y1="{bottom}" x2="{width-right}" y2="{bottom}" class="axisline"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axisline"/>',
    ]
    for tick in range(5):
        probability = tick / 4.0
        yy = y(probability)
        value = low + (high - low) * probability
        xx = x(value)
        elements.append(
            f'<line x1="{left}" y1="{yy:.2f}" x2="{width-right}" y2="{yy:.2f}" '
            'class="gridline"/>'
            f'<text x="{left-8}" y="{yy+4:.2f}" text-anchor="end">{probability:.0%}</text>'
            f'<text x="{xx:.2f}" y="{bottom+18}" text-anchor="middle">{value:.2g}</text>')
    elements.append('<polyline points="' + " ".join(coords)
                    + '" class="cdfline"/>' + "".join(dots))
    unit = "%" if spec.percent else spec.unit
    elements.append(
        f'<text x="{width/2:.0f}" y="258" text-anchor="middle" class="axislabel">'
        f'{_esc(spec.label)} {_esc(unit)}</text>')
    return (
        '<div class="svg-scroll"><svg class="user-svg" role="img" '
        f'aria-label="{_esc(spec.label)} 跨用户经验 CDF" viewBox="0 0 {width} {height}">'
        + "".join(elements) + "</svg></div>"
    )


def _metric_panel(users: list[dict[str, Any]], spec: KpiSpec,
                  colours: dict[str, str]) -> str:
    return (
        '<article class="metric-panel"><h3>' + _esc(spec.label) + "</h3>"
        + '<p class="metric-key">' + _esc(spec.key) + "</p>"
        + '<div class="plot-grid"><div><h4>按 UE</h4>'
        + _user_bar_svg(users, spec, colours)
        + '</div><div><h4>跨 UE 经验 CDF</h4>'
        + _user_cdf_svg(users, spec, colours)
        + '</div></div><p class="axis">CDF 的样本是每个 UE 在 replication 间的均值，'
        + "不是包级 CDF；误差棒是该 UE 的 95% 重复实验区间。</p></article>"
    )


def _legend(colours: dict[str, str]) -> str:
    return '<div class="legend">' + "".join(
        '<span><i style="background:' + colour + '"></i>' + _esc(name) + "</span>"
        for name, colour in colours.items()) + "</div>"


def _user_table(users: list[dict[str, Any]], specs: list[KpiSpec]) -> str:
    heads = "".join(f"<th>{_esc(spec.label)}</th>" for spec in specs)
    rows: list[str] = []
    for row in users:
        cells: list[str] = []
        for spec in specs:
            mean, ci, _ = _stat(row, spec.key)
            text = _fmt(mean, spec)
            if ci:
                text += f" [{_fmt(ci[0], spec)}, {_fmt(ci[1], spec)}]"
            cells.append(f'<td title="{_esc(spec.key)}">{_esc(text)}</td>')
        rows.append(
            f'<tr><th scope="row">UE {_esc(row.get("ue", "-"))}</th>'
            f'<td>{_esc(row.get("traffic_class", "unknown"))}</td>'
            + "".join(cells) + "</tr>")
    return (
        '<div class="table-scroll"><table><thead><tr><th>用户</th><th>话务 profile</th>'
        + heads + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _traffic_profiles(result: dict[str, Any]) -> str:
    profiles = result.get("traffic_profiles")
    if not isinstance(profiles, list) or not profiles:
        return '<div class="empty">本次结果没有话务 profile 元数据。</div>'
    rows: list[str] = []
    for row in profiles:
        if not isinstance(row, dict):
            continue
        packet = row.get("packet_size_cdf")
        interval = row.get("interarrival_cdf")
        packet_source = packet.get("source_path") if isinstance(packet, dict) else "fixed"
        interval_source = interval.get("source_path") if isinstance(interval, dict) else "Poisson"
        ue_ids = row.get("assigned_ue_ids", [])
        rows.append(
            "<tr><th>" + _esc(row.get("name", "-")) + "</th>"
            + "<td>" + _esc(", ".join(str(x) for x in ue_ids)) + "</td>"
            + f'<td>{float(row.get("estimated_mean_packet_bytes", 0)):.1f}</td>'
            + f'<td>{float(row.get("estimated_mean_interarrival_ms", 0)):.2f}</td>'
            + f'<td>{float(row.get("estimated_offered_mbps_per_ue", 0)):.3f}</td>'
            + "<td>" + _esc(packet_source) + "</td><td>" + _esc(interval_source) + "</td></tr>"
        )
    return (
        '<div class="table-scroll"><table><thead><tr><th>profile</th><th>UE IDs</th>'
        '<th>均值包长 B</th><th>均值包间隔 ms</th><th>估算 Mbps/UE</th>'
        '<th>包长输入</th><th>包间隔输入</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></div>"
    )


def _calibration(result: dict[str, Any]) -> str:
    cal = result.get("traffic_calibration")
    if not isinstance(cal, dict):
        return ""
    history = cal.get("history") if isinstance(cal.get("history"), list) else []
    formal = (cal.get("formal_history")
              if isinstance(cal.get("formal_history"), list) else [])
    rows = "".join(
        "<tr>"
        + '<td>probe</td>'
        + f'<td>{int(row.get("iteration", 0))}</td>'
        + f'<td>{float(row.get("offered_load_factor_vs_input", 0)):.4g}</td>'
        + f'<td>{float(row.get("packet_size_scale", 0)):.4g}</td>'
        + f'<td>{float(row.get("interarrival_scale", 0)):.4g}</td>'
        + f'<td>{float(row.get("measured_prb_utilization", 0)):.2%}</td>'
        + f'<td>{float(row.get("absolute_error", 0)):.2%}</td></tr>'
        for row in history if isinstance(row, dict)
    )
    rows += "".join(
        "<tr><td>formal</td>"
        + f'<td>{int(row.get("formal_run", 0))}</td>'
        + f'<td>{float(row.get("offered_load_factor_vs_input", 0)):.4g}</td>'
        + f'<td>{float(row.get("packet_size_scale", 0)):.4g}</td>'
        + f'<td>{float(row.get("interarrival_scale", 0)):.4g}</td>'
        + f'<td>{float(row.get("measured_prb_utilization", 0)):.2%}</td>'
        + f'<td>{float(row.get("absolute_error", 0)):.2%}</td></tr>'
        for row in formal if isinstance(row, dict)
    )
    achieved = cal.get("achieved_prb_utilization")
    achieved_text = f"{float(achieved):.2%}" if isinstance(achieved, (int, float)) else "n/a"
    return (
        '<div class="callout"><strong>话务校准：</strong>状态 '
        + _esc(cal.get("status", "unknown"))
        + f'；目标 {float(cal.get("target_prb_utilization", 0)):.1%}；正式实测 '
        + achieved_text + "。probe 与正式反馈轮使用互不重叠且各自固定的 replication，"
        "结果没有回填目标值。</div>"
        + '<div class="table-scroll"><table><thead><tr><th>阶段</th><th>轮次</th><th>负载倍率</th>'
        '<th>size scale</th><th>interval scale</th><th>实测 PRB</th><th>绝对误差</th>'
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
    )


def render_html(result: dict[str, Any], *, dataset_id: str = "",
                kpi_focus: list[str] | None = None, kpi_intent: str = "",
                selection: dict[str, Any] | None = None) -> str:
    """把 ``ReplicationResult.as_dict()`` 渲染成自包含双层 KPI HTML。"""
    cell = result.get("cell")
    users = result.get("users")
    if not isinstance(cell, dict):
        raise ValueError("KPI 页面需要 result['cell'] 字典")
    if not isinstance(users, list):
        users = []
    chosen = selection or select_kpis(
        result, kpi_focus=kpi_focus, kpi_intent=kpi_intent)
    cell_map, user_map = _spec_map(CELL_KPIS), _spec_map(USER_KPIS)
    cell_primary = [cell_map[key] for key in chosen["cell"]["prioritized"]]
    cell_more = [cell_map[key] for key in chosen["cell"]["collapsed"]]
    user_primary = [user_map[key] for key in chosen["user"]["prioritized"]]
    user_more = [user_map[key] for key in chosen["user"]["collapsed"]]
    colours = _profile_colours(users)
    config = result.get("config") if isinstance(result.get("config"), dict) else {}
    system_cfg = config.get("system") if isinstance(config.get("system"), dict) else {}
    model = system_cfg.get("model_version", "unknown")
    definitions = result.get("kpi_definitions")
    definitions = definitions if isinstance(definitions, dict) else {}
    notes = result.get("notes") if isinstance(result.get("notes"), list) else []
    note_html = "".join(f"<li>{_strong(str(note))}</li>" for note in notes)
    definition_html = "".join(
        '<details><summary>' + _esc(name) + '</summary><dl>'
        + "".join(f'<dt>{_esc(key)}</dt><dd>{_esc(value)}</dd>'
                  for key, value in row.items()) + "</dl></details>"
        for name, row in definitions.items() if isinstance(row, dict)
    )
    reason_html = "".join(f"<li>{_esc(reason)}</li>" for reason in chosen["reasons"])
    tags = " · ".join(chosen["resolved_tags"])
    resource_open = " open" if {"resource", "mu"} & set(chosen["resolved_tags"]) else ""
    traffic_open = " open" if "traffic" in chosen["resolved_tags"] else ""
    primary_user_panels = "".join(
        _metric_panel(users, spec, colours) for spec in user_primary)
    more_user_panels = "".join(
        _metric_panel(users, spec, colours) for spec in user_more)
    css = """
:root{--ink:#162437;--muted:#65758b;--bg:#f3f6fa;--card:#fff;--line:#dce4ee;
--blue:#1769aa;--cyan:#39a7c7;--green:#209567;--amber:#c88916;--red:#c84e4e;
/* 浅底块一律成对定义底色与字色。只翻底色不翻字色，暗色下就是浅底浅字。 */
--panel:#fff;--tab:#e7edf4;--tint:#eaf6f7;--tint-ink:#12333c;
--warn:#fff8e7;--warn-ink:#5a3d00;--th:#eef3f8;--svg-bg:#fbfcfe;
--grid:#e5eaf0;--whisker:#172536}
@media(prefers-color-scheme:dark){:root{
--ink:#e9edf3;--muted:#a3b0c0;--bg:#14181d;--card:#1e242b;--line:#333c47;
--blue:#59aee8;--cyan:#4cc0dd;--green:#4cc98d;--amber:#e2ac4a;--red:#e87b7b;
--panel:#1e242b;--tab:#272f38;--tint:#0f2833;--tint-ink:#a9e2f0;
--warn:#2e2410;--warn-ink:#f3d79a;--th:#222932;--svg-bg:#181e24;
--grid:#2c353f;--whisker:#c9d4e0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif}.wrap{max-width:1460px;margin:auto;padding:28px}
header{background:linear-gradient(125deg,#0c3e67,#087f91);color:white;padding:30px;border-radius:18px}
h1{margin:0 0 8px;font-size:30px}.meta{opacity:.84;margin:0}.priority{background:var(--tint);color:var(--tint-ink);border-left:5px solid var(--cyan);padding:14px 18px;margin:20px 0;border-radius:8px}.priority ul{margin-bottom:4px}
.scope-tabs>input{position:absolute;opacity:0;width:1px;height:1px}.scope-tabs>label{display:inline-block;padding:12px 24px;background:var(--tab);color:var(--ink);border:1px solid var(--line);cursor:pointer;font-weight:750;font-size:16px}.scope-tabs>input:checked+label{background:var(--panel);color:var(--blue);border-bottom-color:var(--panel)}.scope-tabs>input:focus-visible+label{outline:3px solid var(--cyan);outline-offset:2px}.scope-panels>section{display:none;background:var(--panel);border:1px solid var(--line);padding:24px;border-radius:0 14px 14px 14px}#scope-cell:checked~.scope-panels>#cell-panel,#scope-user:checked~.scope-panels>#user-panel{display:block}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.kpi{border:1px solid var(--line);border-radius:12px;padding:16px;min-height:138px}.kpi .label,.kpi .ci,.kpi .key{display:block}.kpi .label{font-weight:700}.kpi strong{font-size:27px;display:inline-block;margin:13px 4px 7px 0}.kpi .unit,.kpi .ci,.kpi .key,.axis,.metric-key{color:var(--muted)}.kpi .key,.metric-key{font-family:Consolas,monospace;font-size:11px;margin-top:8px}
details{border:1px solid var(--line);border-radius:10px;padding:13px;margin:15px 0}summary{font-weight:750;cursor:pointer}h2{margin-top:28px}.rbg-chart{height:290px;display:grid;grid-template-columns:repeat(18,minmax(24px,1fr));align-items:end;gap:7px;border-bottom:2px solid var(--ink);padding:26px 8px 0;margin-top:18px;overflow-x:auto}
/* 载波栅格由数据集推出，桶数不再恒为 18；实际列数由内联 style 覆盖。 */.bar-col{text-align:center;min-width:25px}.bar{background:linear-gradient(#39a7c7,#1769aa);border-radius:5px 5px 0 0}.bar-value{font-size:10px;color:var(--muted);display:block;white-space:nowrap}.bar-col b{font-size:12px}.gauge{margin:8px 0 24px}.track{height:22px;background:var(--tab);border-radius:11px;position:relative;margin:36px 0 10px}.fill{height:100%;border-radius:11px;background:linear-gradient(90deg,var(--green),var(--amber),var(--red))}.mark{position:absolute;top:-26px;font-size:12px;color:var(--muted);transform:translateX(-50%)}.m10{left:10%}.m30{left:30%}.m50{left:50%}.needle{position:absolute;top:-7px;width:3px;height:36px;background:var(--ink);transform:translateX(-1px)}
.callout{padding:14px 16px;border-left:4px solid var(--amber);background:var(--warn);color:var(--warn-ink);margin:14px 0}.legend{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px}.scope-panels,.scope-panels>section,.metric-panel,.plot-grid,.plot-grid>*{min-width:0}.metric-panel{border:1px solid var(--line);border-radius:14px;padding:18px;margin:18px 0}.metric-panel h3{margin:0}.metric-panel h4{margin:8px 0}.plot-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.svg-scroll,.table-scroll{width:100%;max-width:100%;min-width:0;overflow:auto}.user-svg{min-width:640px;background:var(--svg-bg);border:1px solid var(--line);border-radius:8px}.user-svg text{font-size:11px;fill:var(--muted)}.axisline{stroke:var(--ink);stroke-width:1.5}.gridline{stroke:var(--grid);stroke-width:1}.whisker{stroke:var(--whisker);stroke-width:1.5}.cdfline{fill:none;stroke:var(--blue);stroke-width:2.5}.axislabel{font-weight:700;fill:var(--ink)!important}table{border-collapse:collapse;min-width:100%;font-size:13px}th,td{border:1px solid var(--line);padding:8px 10px;text-align:right;white-space:nowrap}th{background:var(--th);text-align:left;position:sticky;top:0}dl{display:grid;grid-template-columns:220px 1fr;gap:7px}dt{font-weight:650}dd{margin:0;color:var(--muted)}li{margin:8px 0;line-height:1.5}.empty{padding:28px;color:var(--muted)}
@media(max-width:1050px){.grid{grid-template-columns:repeat(2,1fr)}.plot-grid{grid-template-columns:1fr}}
@media(max-width:560px){.wrap{padding:10px}.grid{grid-template-columns:1fr}header{padding:22px}h1{font-size:24px}.scope-panels>section{padding:13px}.scope-tabs>label{padding:10px 16px}.priority{padding:12px}.user-svg{min-width:600px}}
"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>系统仿真 KPI</title><style>{css}</style></head><body><div class="wrap">
<header><h1>系统仿真 KPI</h1><p class="meta">数据集 {_esc(dataset_id or result.get('dataset_id','-'))} · {_esc(model)} · {_esc(result.get('n_rep',1))} 次重复 · {time.strftime('%Y-%m-%d %H:%M:%S')}</p></header>
<div class="priority"><strong>Agent KPI 编排：{_esc(chosen['source'])}</strong><p>关注标签：{_esc(tags)}</p><ul>{reason_html}</ul><small>优先级只影响展示顺序；所有可用 KPI 仍保留在折叠区与结果 JSON。</small></div>
<div class="scope-tabs"><input id="scope-cell" name="scope" type="radio" checked><label for="scope-cell">小区级</label><input id="scope-user" name="scope" type="radio"><label for="scope-user">用户级</label><div class="scope-panels">
<section id="cell-panel"><div class="callout"><strong>不要混淆：</strong>neighbor_prb_util 是邻区干扰输入；serving_cell_prb_utilization 是本小区正式仿真的实测结果。10%/30%/50% 只能通过话务校准接近。</div><h2>优先 KPI</h2><div class="grid">{''.join(_card(cell,spec) for spec in cell_primary)}</div>
<details><summary>更多小区 KPI（{len(cell_more)}）</summary><div class="grid">{''.join(_card(cell,spec) for spec in cell_more)}</div></details>
<details{resource_open}><summary>资源占用与 MU 画像</summary>{_load_gauge(cell)}<h3>{_esc(distribution_title(cell))}</h3>{_distribution(cell)}</details>
<details{traffic_open}><summary>话务 profile 与 CDF 输入</summary>{_traffic_profiles(result)}</details>
{_calibration(result)}
<details><summary>KPI 定义与统计口径</summary>{definition_html or '<p class="empty">无定义元数据。</p>'}</details>
<details><summary>必须随结果转述的告警（{len(notes)}）</summary><ul>{note_html or '<li>无告警。</li>'}</ul></details></section>
<section id="user-panel"><h2>用户级优先分析</h2><p>每个指标同时给按 UE 图与跨 UE 经验 CDF；颜色表示话务 profile。</p>{_legend(colours)}{primary_user_panels}
<details><summary>更多用户级图与 CDF（{len(user_more)}）</summary>{more_user_panels or '<p class="empty">没有更多可用指标。</p>'}</details>
<details><summary>用户级全量明细表</summary>{_user_table(users, user_primary + user_more)}</details>
<div class="callout"><strong>资源归因：</strong>grant PRB exposure 在共享 MU PRB 上对每个配对 UE 都计一次，不能跨 UE 相加；attributed PRB 将共享资源等分，跨 UE 求和严格等于小区已用 PRB。</div></section>
</div></div></div></body></html>"""


def write_kpi_report(result: dict[str, Any], *, dataset_id: str = "",
                     serve: bool = True, kpi_focus: list[str] | None = None,
                     kpi_intent: str = "") -> dict[str, Any]:
    """写入 ``artifacts/kpi/``，返回页面与完整的 Agent KPI 排序证据。"""
    selection = select_kpis(result, kpi_focus=kpi_focus, kpi_intent=kpi_intent)
    html_text = render_html(
        result, dataset_id=dataset_id, selection=selection)
    out_dir = artifacts_root() / "kpi"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"kpi-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    path = out_dir / f"{stem}.html"
    path.write_text(html_text, encoding="utf-8")
    url = None
    serve_error = None
    if serve:
        if not br.enabled():
            serve_error = "SUPERRAN_NO_SERVE 已关闭环回服务"
        else:
            url = br.serve(stem, html_text, title="系统仿真 KPI", allowed=set())
            if url is None:
                serve_error = "环回服务启动失败；请直接打开 html_path"
    return {
        "html_path": str(path),
        "url": url,
        "serve_error": serve_error,
        "tabs": ["小区级", "用户级"],
        "kpi_selection": selection,
        "user_plot_contract": "per-UE plot + empirical CDF over UE replication means",
        "supported_cell_kpis": [spec.key for spec in CELL_KPIS],
        "supported_user_kpis": [spec.key for spec in USER_KPIS],
    }
