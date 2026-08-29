"""Multi-algorithm system-KPI comparison workbench.

The workbench compares 2--5 saved :func:`sr_system_sim` results.  Algorithms are
global colour-coded series; tabs represent questions (overview, KPI matrix, user
distribution, TTI trend, one-TTI evidence, statistical gates) rather than hiding
each algorithm behind a separate tab.

Scalar conclusions always use paired per-replication evidence and the existing
``rng.compare_replications``/Gate-3 implementation.  Single-TTI rows are diagnostic:
they explain *why* two algorithms diverged but never manufacture a performance claim.
"""
from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import time
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from . import bridge as br
from . import kpi_view, webui
from . import rng as rg
from .paths import artifacts_root

_ARM_COLOURS = ("#1769aa", "#cf6b35", "#209567", "#8f5bb7", "#c49717")
_RESULT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PRIMARY_DEFAULT = "cell_experienced_mbps"

# A direction is only used after Gate 3 and family-wise correction pass.  ``target``
# and ``diagnostic`` metrics never receive an automatic "winner" label.
_GOAL: dict[str, str] = {
    "cell_experienced_mbps": "higher",
    "cell_head_inclusive_experienced_mbps": "higher",
    "ue_experienced_p5_mbps": "higher",
    "cell_served_mbps": "higher",
    "first_packet_delay_ms_mean": "lower",
    "first_packet_delay_ms_p95": "lower",
    "first_packet_delay_observed_share": "higher",
    "small_completion_delay_ms_p95": "lower",
    "small_pdb_miss_ratio": "lower",
    "bler_first_tx": "lower",
    "retx_bler": "lower",
    "residual_bler": "lower",
    "retx_attempts": "lower",
    "pending_harq_tb_at_end": "lower",
    "padding_ratio": "lower",
    "backlog_bytes": "lower",
    "payload_fill_ratio": "higher",
    "serving_cell_prb_utilization": "target",
    "mu_paired_prb_share_of_used": "diagnostic",
    "mu_paired_prb_utilization": "diagnostic",
    "allocated_prb_equivalent": "diagnostic",
    "offered_mbps": "diagnostic",
    "avg_mcs": "diagnostic",
    "avg_rank": "diagnostic",
    "su_bler_first_tx": "lower",
    "mu_bler_first_tx": "lower",
}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_json_ready(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _json_ready(value.item())
    if hasattr(value, "item"):
        try:
            return _json_ready(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(_json_ready(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _stat(container: dict[str, Any], key: str) -> tuple[float | None, list[float] | None]:
    value = container.get(key)
    if isinstance(value, dict):
        mean = value.get("mean")
        raw_ci = value.get("ci95")
        ci = (
            [float(raw_ci[0]), float(raw_ci[1])]
            if isinstance(raw_ci, list)
            and len(raw_ci) == 2
            and all(isinstance(item, (int, float)) and math.isfinite(float(item))
                    for item in raw_ci)
            else None
        )
        if isinstance(mean, (int, float)) and math.isfinite(float(mean)):
            return float(mean), ci
    elif isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value), None
    return None, None


def _books(result: dict[str, Any]) -> list[rg.RngBook]:
    evidence = result.get("comparison_evidence")
    raw = evidence.get("rng_books") if isinstance(evidence, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("结果缺少 comparison_evidence.rng_books，无法证明 CRN 配对")
    books: list[rg.RngBook] = []
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("rng_books 必须是对象数组")
        books.append(rg.RngBook(
            master_seed=row.get("master_seed"), replication=row.get("replication")))
    return books


def _samples(result: dict[str, Any], metric: str) -> list[float]:
    evidence = result.get("comparison_evidence")
    values = (evidence.get("cell_samples_by_replication", {}).get(metric)
              if isinstance(evidence, dict) else None)
    if not isinstance(values, list) or not values:
        raise ValueError(f"结果缺少 {metric!r} 的逐 replication 样本，不能做配对比较")
    parsed = [float(value) for value in values]
    if not all(math.isfinite(value) for value in parsed):
        raise ValueError(f"{metric!r} 的逐 replication 样本包含 NaN/Inf")
    return parsed


def _result_path(result_id: str) -> Any:
    text = str(result_id).strip()
    if not _RESULT_ID.fullmatch(text):
        raise ValueError("result_id 只允许字母、数字、点、下划线与短横线，禁止路径")
    return artifacts_root() / "kpi" / f"{text}.result.json"


def load_saved_result(result_id: str) -> dict[str, Any]:
    path = _result_path(result_id)
    if not path.is_file():
        raise FileNotFoundError(f"找不到系统仿真 KPI 结果 {result_id!r}（{path}）")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or not isinstance(result.get("cell"), dict):
        raise ValueError(f"{result_id!r} 不是 SuperRAN 系统 KPI 结果")
    return result


def _arm_label(result_id: str, result: dict[str, Any]) -> str:
    algorithm = result.get("algorithm")
    label = algorithm.get("label") if isinstance(algorithm, dict) else None
    return str(label or result_id).strip()


def _fairness_value(result: dict[str, Any], path: Sequence[str]) -> Any:
    value: Any = result
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


_FAIRNESS_PATHS: tuple[tuple[str, ...], ...] = (
    ("dataset_id",),
    ("analysis_identity",),
    ("config", "system", "model_version"),
    ("config", "system", "evaluation_mode"),
    ("config", "system", "duration_s"),
    ("config", "system", "tti_ms"),
    ("config", "system", "tdd_pattern"),
    ("config", "system", "num_rb"),
    ("config", "system", "num_rbg"),
    ("config", "system", "rb_per_rbg"),
    ("config", "traffic"),
    ("config", "kpi"),
    ("n_rep",),
    ("replications",),
)


def _diff_tree(a: Any, b: Any, prefix: str = "") -> list[dict[str, Any]]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[dict[str, Any]] = []
        for key in sorted(set(a) | set(b)):
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_diff_tree(a.get(key), b.get(key), path))
        return out
    if _canonical(a) == _canonical(b):
        return []
    return [{"path": prefix, "baseline": _json_ready(a), "candidate": _json_ready(b)}]


def _holm_rejections(p_values: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down correction; it can only tighten existing Gate-3 decisions."""
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    decisions = {name: False for name in p_values}
    keep_rejecting = True
    total = len(ordered)
    for index, (name, p_value) in enumerate(ordered):
        threshold = float(alpha) / (total - index)
        keep_rejecting = keep_rejecting and float(p_value) <= threshold
        decisions[name] = bool(keep_rejecting)
    return decisions


def build_comparison(
    result_ids: Sequence[str], *, baseline_result_id: str | None = None,
    primary_kpi: str = _PRIMARY_DEFAULT,
) -> dict[str, Any]:
    """Validate and compare 2--5 saved system-simulation algorithm arms."""
    ids = [str(result_id).strip() for result_id in result_ids]
    if not 2 <= len(ids) <= 5:
        raise ValueError("多算法工作台一次只支持 2..5 个结果")
    if len(set(ids)) != len(ids):
        raise ValueError("result_ids 不能重复")
    baseline_id = str(baseline_result_id or ids[0]).strip()
    if baseline_id not in ids:
        raise ValueError("baseline_result_id 必须位于 result_ids 中")

    loaded = {result_id: load_saved_result(result_id) for result_id in ids}
    labels = {result_id: _arm_label(result_id, loaded[result_id]) for result_id in ids}
    if len(set(labels.values())) != len(labels):
        raise ValueError("算法标签必须唯一；请在 sr_system_sim 里设置不同 algorithm_label")
    baseline = loaded[baseline_id]
    baseline_books = _books(baseline)
    prereg = baseline.get("analysis_identity")
    prereg_same = (
        isinstance(prereg, dict)
        and all(_canonical(result.get("analysis_identity")) == _canonical(prereg)
                for result in loaded.values())
    )
    prereg_metric = str(prereg.get("primary_metric", "")) if isinstance(prereg, dict) else ""
    prereg_baseline = str(prereg.get("baseline", "")).strip() if isinstance(prereg, dict) else ""
    prereg_verified = bool(
        prereg_same
        and prereg_metric == primary_kpi
        and prereg_baseline
        and prereg_baseline.casefold() == labels[baseline_id].casefold()
    )
    primary_lock = {
        "status": "verified" if prereg_verified else "exploratory_unregistered",
        "prereg_id": prereg.get("prereg_id") if isinstance(prereg, dict) else None,
        "digest": prereg.get("digest") if isinstance(prereg, dict) else None,
        "declared_primary_kpi": prereg_metric or None,
        "declared_baseline": prereg_baseline or None,
        "requested_primary_kpi": primary_kpi,
        "selected_baseline": labels[baseline_id],
        "publishable_gate_enabled": prereg_verified,
        "reason": (
            "dataset preregistration matches the primary KPI and baseline label"
            if prereg_verified
            else "primary KPI/baseline was not verifiably locked before dataset generation; "
                 "all comparisons remain exploratory even if paired statistics pass"
        ),
    }

    blockers: list[dict[str, Any]] = []
    config_differences: dict[str, list[dict[str, Any]]] = {}
    for result_id in ids:
        if result_id == baseline_id:
            continue
        candidate = loaded[result_id]
        for path in _FAIRNESS_PATHS:
            left, right = _fairness_value(baseline, path), _fairness_value(candidate, path)
            if _canonical(left) != _canonical(right):
                blockers.append({
                    "arm": labels[result_id],
                    "path": ".".join(path),
                    "baseline": _json_ready(left),
                    "candidate": _json_ready(right),
                })
        issues = rg.check_pairable(baseline_books, _books(candidate))
        blockers.extend({"arm": labels[result_id], **issue} for issue in issues)
        config_differences[result_id] = [
            row for row in _diff_tree(baseline.get("config", {}), candidate.get("config", {}))
            if not row["path"].startswith("execution")
            and not row["path"].startswith("rng")
        ][:80]
    if blockers:
        first = blockers[0]
        raise ValueError(
            "多算法结果不可公平比较：" + str(first.get("detail") or first.get("path")))

    cell_specs = {spec.key: spec for spec in kpi_view.CELL_KPIS}
    user_specs = {spec.key: spec for spec in kpi_view.USER_KPIS}
    common_cell = [
        key for key in cell_specs
        if all(_stat(result.get("cell", {}), key)[0] is not None
               for result in loaded.values())
    ]
    common_user = [
        key for key in user_specs
        if all(any(_stat(row, key)[0] is not None
                   for row in result.get("users", []) if isinstance(row, dict))
               for result in loaded.values())
    ]
    if primary_kpi not in common_cell:
        raise ValueError(f"主 KPI {primary_kpi!r} 不是所有算法共有的小区级 KPI")

    comparisons: dict[str, dict[str, Any]] = {}
    for metric in common_cell:
        spec = cell_specs[metric]
        by_candidate: dict[str, Any] = {}
        for result_id in ids:
            if result_id == baseline_id:
                continue
            by_candidate[result_id] = rg.compare_replications(
                _samples(loaded[result_id], metric), _samples(baseline, metric),
                metric=metric,
                unit=("ratio" if spec.percent else spec.unit),
                arm_a=labels[result_id], arm_b=labels[baseline_id],
                books_a=_books(loaded[result_id]), books_b=baseline_books,
            )
        comparisons[metric] = by_candidate

    primary_tests = comparisons[primary_kpi]
    p_values = {
        result_id: float(row.get("paired", {}).get("decision_p_value", 1.0))
        for result_id, row in primary_tests.items()
        if row.get("verdict") != "not_pairable"
    }
    holm = _holm_rejections(p_values)
    goal = _GOAL.get(primary_kpi, "diagnostic")
    for result_id, row in primary_tests.items():
        individual = row.get("verdict") == "significant"
        family_pass = bool(individual and holm.get(result_id, False))
        effect = float(row.get("effect", 0.0))
        beneficial = ((goal == "higher" and effect > 0)
                      or (goal == "lower" and effect < 0))
        row["holm_family_size"] = len(primary_tests)
        row["holm_reject"] = bool(holm.get(result_id, False))
        row["family_verdict"] = (
            "exploratory_unregistered" if not prereg_verified
            else "significant_beneficial" if family_pass and beneficial
            else "significant_adverse" if family_pass and not beneficial
            else "inconclusive"
        )
        row["publishable_winner"] = bool(prereg_verified and family_pass and beneficial
                                         and goal in ("higher", "lower"))
        row["display_verdict_text"] = (
            row.get("verdict_text", "")
            if prereg_verified
            else "未验证生成前预注册的主 KPI 与基线身份；该比较只作探索，"
                 "不能发布算法胜负或提升百分比。"
        )

    arms = []
    for index, result_id in enumerate(ids):
        result = loaded[result_id]
        arms.append({
            "result_id": result_id,
            "label": labels[result_id],
            "colour": _ARM_COLOURS[index],
            "baseline": result_id == baseline_id,
            "algorithm": result.get("algorithm", {}),
            "cell": result.get("cell", {}),
            "users": result.get("users", []),
            "trace": result.get("tti_trace", {}),
            "notes": result.get("notes", []),
            "config_differences": config_differences.get(result_id, []),
        })
    return {
        "schema": "superran_kpi_comparison_v1",
        "dataset_id": baseline.get("dataset_id", ""),
        "baseline_result_id": baseline_id,
        "primary_kpi": primary_kpi,
        "primary_goal": goal,
        "primary_lock": primary_lock,
        "arms": arms,
        "common_cell_kpis": common_cell,
        "common_user_kpis": common_user,
        "cell_kpi_meta": {
            key: {
                "label": cell_specs[key].label,
                "unit": "%" if cell_specs[key].percent else cell_specs[key].unit,
                "percent": cell_specs[key].percent,
                "digits": cell_specs[key].digits,
                "goal": _GOAL.get(key, "diagnostic"),
            }
            for key in common_cell
        },
        "user_kpi_meta": {
            key: {
                "label": user_specs[key].label,
                "unit": "%" if user_specs[key].percent else user_specs[key].unit,
                "percent": user_specs[key].percent,
                "digits": user_specs[key].digits,
            }
            for key in common_user
        },
        "comparisons": comparisons,
        "fairness": {
            "pairable": True,
            "pairing_key": "(master_seed, replication)",
            "n_rep": len(baseline_books),
            "blockers": [],
            "config_differences_are_disclosed_not_hidden": True,
        },
        "interpretation_contract": {
            "single_tti": "diagnostic only; never a Gate-3 performance claim",
            "secondary_kpis": "exploratory unless preregistered",
            "primary_family": "candidate-vs-baseline p-values use Holm step-down correction",
        },
    }


def _metric_options(keys: Iterable[str], specs: dict[str, kpi_view.KpiSpec]) -> str:
    return "".join(
        f'<option value="{_esc(key)}">{_esc(specs[key].label)}</option>'
        for key in keys
    )


def _display_stat(container: dict[str, Any], key: str,
                  spec: kpi_view.KpiSpec) -> str:
    mean, ci = _stat(container, key)
    if mean is None:
        return "n/a"
    if spec.percent:
        value = f"{mean:.2%}"
        return value if ci is None else f"{value} [{ci[0]:.2%}, {ci[1]:.2%}]"
    value = f"{mean:.{spec.digits}f}"
    unit = f" {spec.unit}" if spec.unit else ""
    return value + unit if ci is None else (
        f"{value} [{ci[0]:.{spec.digits}f}, {ci[1]:.{spec.digits}f}]{unit}")


def _matrix_cell(comparison: dict[str, Any], arm: dict[str, Any], key: str,
                 spec: kpi_view.KpiSpec) -> str:
    value = _display_stat(arm["cell"], key, spec)
    if arm["baseline"]:
        return '<span class="matrix-value">' + _esc(value) + '</span><small>基线</small>'
    row = comparison["comparisons"].get(key, {}).get(arm["result_id"], {})
    effect = row.get("effect")
    if not isinstance(effect, (int, float)) or not math.isfinite(float(effect)):
        delta = "Δ n/a"
    elif spec.percent:
        delta = f"Δ {float(effect) * 100:+.{spec.digits}f} pp"
    else:
        unit = f" {spec.unit}" if spec.unit else ""
        delta = f"Δ {float(effect):+.{spec.digits}f}{unit}"
    css = "delta-pos" if float(effect or 0.0) > 0 else "delta-neg"
    return (
        '<span class="matrix-value">' + _esc(value) + '</span>'
        + f'<small class="matrix-delta {css}" title="配对差值；非主 KPI 默认只作探索">'
        + _esc(delta) + "</small>"
    )


def _summary_csv(comparison: dict[str, Any]) -> str:
    cell_specs = {spec.key: spec for spec in kpi_view.CELL_KPIS}
    baseline_id = comparison["baseline_result_id"]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("result_id", "algorithm", "baseline", "metric", "label", "mean",
                     "ci95_low", "ci95_high", "effect_vs_baseline", "p_value",
                     "individual_verdict", "family_verdict", "unit"))
    for arm in comparison["arms"]:
        for metric in comparison["common_cell_kpis"]:
            spec = cell_specs[metric]
            mean, ci = _stat(arm["cell"], metric)
            row = comparison["comparisons"].get(metric, {}).get(arm["result_id"], {})
            writer.writerow((
                arm["result_id"], arm["label"], int(arm["result_id"] == baseline_id),
                metric, spec.label, mean,
                "" if ci is None else ci[0], "" if ci is None else ci[1],
                row.get("effect", ""), row.get("paired", {}).get("decision_p_value", ""),
                row.get("verdict", "baseline"), row.get("family_verdict", "baseline"),
                "%" if spec.percent else spec.unit,
            ))
    return "\ufeff" + stream.getvalue()


def _trace_csv(comparison: dict[str, Any]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("result_id", "algorithm", "tti", "time_ms", "slot", "snapshot",
                     "sample_reasons", "occupied_rbg", "scheduled_user_count", "has_mu",
                     "newtx_count", "retx_count", "nack_count", "scheduled_bytes",
                     "acked_bytes", "backlog_bytes_after", "grants_json"))
    for arm in comparison["arms"]:
        trace = arm.get("trace") if isinstance(arm.get("trace"), dict) else {}
        for row in trace.get("rows", []) if isinstance(trace.get("rows"), list) else []:
            writer.writerow((
                arm["result_id"], arm["label"], row.get("tti"), row.get("time_ms"),
                row.get("slot"), row.get("snapshot"), "|".join(row.get("sample_reasons", [])),
                row.get("occupied_rbg"), row.get("scheduled_user_count"), row.get("has_mu"),
                row.get("newtx_count"), row.get("retx_count"), row.get("nack_count"),
                row.get("scheduled_bytes"), row.get("acked_bytes"),
                row.get("backlog_bytes_after"),
                json.dumps(row.get("grants", []), ensure_ascii=False, separators=(",", ":")),
            ))
    return "\ufeff" + stream.getvalue()


def _gate_rows(comparison: dict[str, Any]) -> str:
    primary = comparison["primary_kpi"]
    baseline = comparison["baseline_result_id"]
    rows = []
    for arm in comparison["arms"]:
        if arm["result_id"] == baseline:
            continue
        result = comparison["comparisons"][primary][arm["result_id"]]
        ci = result.get("ci95_of_effect") or [None, None]
        p_value = result.get("paired", {}).get("decision_p_value")
        rows.append(
            "<tr><th>" + _esc(arm["label"]) + "</th>"
            + f'<td>{float(result.get("effect", 0.0)):+.4g}</td>'
            + "<td>" + _esc(f"[{ci[0]:+.4g}, {ci[1]:+.4g}]" if None not in ci else "n/a") + "</td>"
            + "<td>" + _esc(f"{float(p_value):.4g}" if p_value is not None else "n/a") + "</td>"
            + "<td>" + _esc(result.get("verdict", "-")) + "</td>"
            + "<td>" + ("通过" if result.get("holm_reject") else "未通过") + "</td>"
            + '<td class="verdict ' + _esc(result.get("family_verdict", "inconclusive")) + '">'
            + _esc(result.get("family_verdict", "inconclusive")) + "</td></tr>"
        )
    return "".join(rows)


def render_html(comparison: dict[str, Any], *, title: str = "") -> str:
    """Render a self-contained interactive comparison workbench."""
    cell_specs = {spec.key: spec for spec in kpi_view.CELL_KPIS}
    user_specs = {spec.key: spec for spec in kpi_view.USER_KPIS}
    primary = comparison["primary_kpi"]
    primary_spec = cell_specs[primary]
    headline = str(title).strip() or "多算法 KPI 对比工作台"
    payload_json = json.dumps(_json_ready(comparison), ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    export_json = json.dumps(_json_ready(comparison), ensure_ascii=False,
                             indent=2, allow_nan=False)
    baseline = next(arm for arm in comparison["arms"] if arm["baseline"])
    summary_lines = [
        "SuperRAN 多算法 KPI 对比",
        f"数据集: {comparison.get('dataset_id') or '-'}",
        f"基线: {baseline['label']}",
        f"主 KPI: {primary_spec.label}",
    ]
    for arm in comparison["arms"]:
        if arm["baseline"]:
            continue
        row = comparison["comparisons"][primary][arm["result_id"]]
        summary_lines.append(str(row.get("display_verdict_text", row.get("verdict_text", ""))))
        if row.get("verdict") == "significant" and not row.get("holm_reject"):
            summary_lines.append(
                f"{arm['label']} 未通过 {row.get('holm_family_size')} 路 Holm 校正，不能发布胜负结论。")
    actions = webui.render_actions(
        title=headline,
        context=f"COMPARE · {len(comparison['arms'])} ALGORITHMS · CRN PAIRED",
        summary_text="\n".join(summary_lines),
        root_selector="#share-surface",
        base_filename="superran-kpi-comparison",
        downloads={
            "comparison.json": ("完整对比 JSON", "application/json;charset=utf-8", export_json),
            "summary.csv": ("算法 × KPI 汇总 CSV", "text/csv;charset=utf-8",
                            _summary_csv(comparison)),
            "tti-trace.csv": ("TTI 抽样与 grant CSV", "text/csv;charset=utf-8",
                              _trace_csv(comparison)),
        },
    )
    algorithm_controls = "".join(
        '<label class="arm-toggle"><input type="checkbox" data-arm-toggle="'
        + _esc(arm["result_id"]) + '" checked '
        + ("disabled" if arm["baseline"] else "") + '><i style="--arm:'
        + _esc(arm["colour"]) + '"></i><span>' + _esc(arm["label"]) + "</span>"
        + ("<b>BASELINE</b>" if arm["baseline"] else "") + "</label>"
        for arm in comparison["arms"]
    )
    gate_rows = _gate_rows(comparison)
    cell_options = _metric_options(comparison["common_cell_kpis"], cell_specs)
    user_options = _metric_options(comparison["common_user_kpis"], user_specs)
    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    css = """
:root{--ink:#172536;--muted:#66768a;--bg:#f2f5f9;--card:#fff;--line:#d9e2ec;
--blue:#1769aa;--cyan:#39a7c7;--green:#209567;--amber:#c88916;--red:#c84e4e;
--panel:#fff;--tab:#e8eef5;--grid:#e4eaf1;--soft:#f8fafc;--th:#edf3f8}
@media(prefers-color-scheme:dark){:root{--ink:#edf2f7;--muted:#a8b4c3;--bg:#12171d;
--card:#1c232b;--line:#34404c;--blue:#63b3ed;--cyan:#4cc0dd;--green:#55ce94;
--amber:#e6b85b;--red:#ef8585;--panel:#1c232b;--tab:#27313b;--grid:#303b47;
--soft:#171e25;--th:#242e38}}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);
font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif}.wrap{max-width:1540px;margin:auto;padding:22px 26px 50px}
.hero{background:linear-gradient(128deg,#0b3f6a,#087d8c);color:#fff;padding:26px 28px;border-radius:18px;
display:flex;justify-content:space-between;gap:24px;align-items:flex-end}.eyebrow{font-size:11px;letter-spacing:.15em;
font-weight:850;color:#bdebf4}.hero h1{margin:5px 0 7px;font-size:31px}.hero p{margin:0;opacity:.85}.hero-badges{display:flex;
gap:7px;flex-wrap:wrap;justify-content:flex-end}.hero-badges span{border:1px solid #ffffff45;background:#ffffff18;
padding:6px 9px;border-radius:999px;font-size:11px;white-space:nowrap}.arm-bar{position:sticky;top:8px;z-index:30;
display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:color-mix(in srgb,var(--card) 94%,transparent);
backdrop-filter:blur(10px);padding:10px 12px;margin:12px 0;border:1px solid var(--line);border-radius:12px;
box-shadow:0 5px 18px #001b3812}.arm-bar>strong{margin-right:5px}.arm-toggle{display:flex;align-items:center;gap:6px;
padding:6px 9px;border:1px solid var(--line);border-radius:999px;cursor:pointer;font-size:12px}.arm-toggle i{width:11px;
height:11px;border-radius:3px;background:var(--arm)}.arm-toggle b{font-size:8px;color:var(--blue);letter-spacing:.08em}.arm-toggle:has(input:not(:checked)){opacity:.45}
.work-tabs{display:flex;gap:4px;overflow:auto;border-bottom:1px solid var(--line);margin-top:12px}.work-tabs button{border:1px solid var(--line);
border-bottom:0;background:var(--tab);color:var(--ink);padding:11px 17px;font-weight:750;cursor:pointer;white-space:nowrap;
border-radius:9px 9px 0 0}.work-tabs button[aria-selected=true]{background:var(--panel);color:var(--blue)}.work-tabs button:focus-visible,
button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid var(--cyan);outline-offset:2px}.panel{display:none;background:var(--panel);
border:1px solid var(--line);border-top:0;padding:22px;border-radius:0 0 14px 14px}.panel.active{display:block}.panel-head{display:flex;
justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}.panel-head h2{margin:0}.controls{display:flex;gap:8px;
align-items:center;flex-wrap:wrap}select,button{font:inherit}select,.controls button{background:var(--card);color:var(--ink);border:1px solid var(--line);
border-radius:8px;padding:8px 10px}.controls button{cursor:pointer}.callout{border-left:4px solid var(--amber);padding:12px 14px;
background:color-mix(in srgb,var(--amber) 10%,var(--card));margin:14px 0}.chart{min-height:330px;border:1px solid var(--line);
border-radius:12px;background:var(--soft);padding:8px;overflow:auto}.chart svg{display:block;min-width:700px;width:100%;height:330px}.axis{stroke:var(--ink);
stroke-width:1.2}.gridline{stroke:var(--grid);stroke-width:1}.chart text{fill:var(--muted);font-size:11px}.chart .axis-title{fill:var(--ink);
font-weight:750}.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px;margin:16px 0}.arm-card{border:1px solid var(--line);
border-top:5px solid var(--arm);border-radius:11px;padding:13px;min-width:0}.arm-card h3{margin:0 0 9px;font-size:15px}.arm-card strong{font-size:24px;
display:block}.arm-card small{color:var(--muted)}.table-scroll{overflow:auto;max-width:100%}table{border-collapse:collapse;min-width:100%;font-size:12px}
th,td{border:1px solid var(--line);padding:8px 9px;text-align:right;white-space:nowrap}th{background:var(--th);text-align:left;position:sticky;top:0}
.matrix-value,.matrix-delta{display:block}.matrix-delta{margin-top:4px}.delta-pos{color:var(--green)}.delta-neg{color:var(--red)}.verdict{font-weight:800}.significant_beneficial{color:var(--green)}
.significant_adverse{color:var(--red)}.inconclusive,.exploratory_unregistered{color:var(--amber)}.trace-note{font-size:12px;color:var(--muted)}.tti-grid{display:grid;
grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}.tti-card{border:1px solid var(--line);border-top:5px solid var(--arm);
border-radius:12px;padding:14px;min-width:0}.tti-card h3{margin:0 0 8px}.tti-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}
.tti-summary div{background:var(--soft);padding:8px;border-radius:7px}.tti-summary small,.tti-summary b{display:block}.tti-summary small{color:var(--muted)}
.grant-table{margin-top:10px}.empty{padding:30px;color:var(--muted);text-align:center}.stats-grid{display:grid;grid-template-columns:1.3fr 1fr;gap:16px}
details{border:1px solid var(--line);border-radius:10px;padding:12px;margin:12px 0}summary{font-weight:750;cursor:pointer}.source-note{color:var(--muted);
font-size:12px}.stats-grid>*,.panel,.panel>*{min-width:0}.table-scroll{width:100%;min-width:0}
pre{max-width:100%;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.hidden-arm{display:none!important}
@media(max-width:1150px){.cards{grid-template-columns:repeat(2,1fr)}.stats-grid{grid-template-columns:1fr}}
@media(max-width:760px){.wrap{padding:9px}.hero{display:block;padding:21px}.hero h1{font-size:24px}.hero-badges{justify-content:flex-start;margin-top:13px}
.panel{padding:12px}.cards,.tti-grid{grid-template-columns:1fr}.tti-summary{grid-template-columns:repeat(2,1fr)}.arm-bar{top:3px}.work-tabs button{padding:9px 12px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
""" + webui.action_css()
    script = r"""
const DATA=JSON.parse(document.getElementById('comparison-data').textContent);
const armMap=Object.fromEntries(DATA.arms.map(a=>[a.result_id,a]));
let visible=new Set(DATA.arms.map(a=>a.result_id));
let selectedTti=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const meanOf=(container,key)=>{const v=container?.[key];return v&&typeof v==='object'?v.mean:(typeof v==='number'?v:null)};
const ciOf=(container,key)=>{const v=container?.[key];return v&&Array.isArray(v.ci95)?v.ci95:null};
const metaOf=(scope,key)=>(scope==='cell'?DATA.cell_kpi_meta:DATA.user_kpi_meta)?.[key]||{};
const scaleOf=(scope,key,value)=>(value===null||value===undefined)?Number.NaN:(metaOf(scope,key).percent?Number(value)*100:Number(value));
const fmtOf=(scope,key,value)=>{if(!Number.isFinite(Number(value)))return'n/a';const m=metaOf(scope,key),v=scaleOf(scope,key,value),d=Number.isInteger(m.digits)?m.digits:2;return `${v.toFixed(d)}${m.unit?` ${m.unit}`:''}`;};
const activeArms=()=>DATA.arms.filter(a=>visible.has(a.result_id));
function setTab(name){document.querySelectorAll('.work-tabs button').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===name)));
document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.dataset.panel===name));}
document.querySelectorAll('.work-tabs button').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));
document.querySelectorAll('[data-arm-toggle]').forEach(input=>input.addEventListener('change',()=>{input.checked?visible.add(input.dataset.armToggle):visible.delete(input.dataset.armToggle);
document.querySelectorAll(`[data-arm-id="${CSS.escape(input.dataset.armToggle)}"]`).forEach(el=>el.classList.toggle('hidden-arm',!input.checked));renderAll();}));
function extent(values){let lo=Math.min(...values),hi=Math.max(...values);if(!Number.isFinite(lo)||!Number.isFinite(hi))return[0,1];
if(Math.abs(hi-lo)<1e-12){const p=Math.max(Math.abs(lo)*.08,1);lo-=p;hi+=p;}return[lo,hi];}
function drawBar(){const key=document.getElementById('cell-metric').value,arms=activeArms(),box=document.getElementById('cell-chart');
const rows=arms.map(a=>{const raw=meanOf(a.cell,key),ci=ciOf(a.cell,key);return{a,raw,v:scaleOf('cell',key,raw),ci:ci?.map(v=>scaleOf('cell',key,v))};}).filter(x=>Number.isFinite(x.v));if(!rows.length){box.innerHTML='<div class="empty">无共同 KPI 数据</div>';return;}
const vals=rows.flatMap(r=>r.ci||[r.v]),[lo0,hi0]=extent(vals),lo=Math.min(0,lo0),hi=hi0,w=900,h=320,L=78,R=24,T=24,B=62,axisUnit=metaOf('cell',key).unit||'';
const y=v=>T+(hi-v)/(hi-lo)*(h-T-B),base=y(0),bw=Math.min(110,(w-L-R)/rows.length*.55);
let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="算法 KPI 柱形图">`;
for(let i=0;i<5;i++){const v=lo+(hi-lo)*i/4,yy=y(v);svg+=`<line class="gridline" x1="${L}" y1="${yy}" x2="${w-R}" y2="${yy}"/><text x="${L-8}" y="${yy+4}" text-anchor="end">${v.toPrecision(3)}${axisUnit==='%'?'%':''}</text>`;}
svg+=`<line class="axis" x1="${L}" y1="${h-B}" x2="${w-R}" y2="${h-B}"/>`;
rows.forEach((r,i)=>{const x=L+(i+.5)*(w-L-R)/rows.length,yy=y(r.v),rh=Math.max(1,Math.abs(base-yy));svg+=`<rect x="${x-bw/2}" y="${Math.min(base,yy)}" width="${bw}" height="${rh}" rx="5" fill="${r.a.colour}"><title>${esc(r.a.label)}: ${fmtOf('cell',key,r.raw)}</title></rect>`;
if(r.ci){const y1=y(r.ci[0]),y2=y(r.ci[1]);svg+=`<line x1="${x}" y1="${y2}" x2="${x}" y2="${y1}" stroke="${r.a.colour}" stroke-width="2"/><line x1="${x-8}" y1="${y1}" x2="${x+8}" y2="${y1}" stroke="${r.a.colour}"/><line x1="${x-8}" y1="${y2}" x2="${x+8}" y2="${y2}" stroke="${r.a.colour}"/>`;}
svg+=`<text x="${x}" y="${h-B+20}" text-anchor="middle">${esc(r.a.label).slice(0,16)}</text><text x="${x}" y="${yy-8}" text-anchor="middle" class="axis-title">${fmtOf('cell',key,r.raw)}</text>`;});
box.innerHTML=svg+'</svg>';renderHeadline(key);}
function renderHeadline(key){const host=document.getElementById('headline-cards');host.innerHTML=activeArms().map(a=>{const v=meanOf(a.cell,key),ci=ciOf(a.cell,key);return `<article class="arm-card" data-arm-id="${esc(a.result_id)}" style="--arm:${a.colour}"><h3>${esc(a.label)}${a.baseline?' · 基线':''}</h3><strong>${fmtOf('cell',key,v)}</strong><small>${ci?`95% CI [${fmtOf('cell',key,ci[0])}, ${fmtOf('cell',key,ci[1])}]`:'无区间'}</small></article>`;}).join('');}
function drawUserCdf(){const key=document.getElementById('user-metric').value,arms=activeArms(),box=document.getElementById('user-chart');let series=arms.map(a=>({a,vals:a.users.map(u=>meanOf(u,key)).filter(Number.isFinite).map(v=>scaleOf('user',key,v)).sort((x,y)=>x-y)})).filter(s=>s.vals.length);
if(!series.length){box.innerHTML='<div class="empty">无共同用户级数据</div>';return;}const all=series.flatMap(s=>s.vals),[lo,hi]=extent(all),w=900,h=320,L=72,R=25,T=20,B=55,x=v=>L+(v-lo)/(hi-lo)*(w-L-R),y=p=>h-B-p*(h-B-T);let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="跨算法用户 CDF">`;
for(let i=0;i<5;i++){const p=i/4,yy=y(p);svg+=`<line class="gridline" x1="${L}" y1="${yy}" x2="${w-R}" y2="${yy}"/><text x="${L-8}" y="${yy+4}" text-anchor="end">${Math.round(p*100)}%</text>`;}svg+=`<line class="axis" x1="${L}" y1="${h-B}" x2="${w-R}" y2="${h-B}"/>`;
series.forEach(s=>{const pts=s.vals.map((v,i)=>`${x(v)},${y((i+1)/s.vals.length)}`).join(' ');svg+=`<polyline points="${pts}" fill="none" stroke="${s.a.colour}" stroke-width="3"><title>${esc(s.a.label)}</title></polyline>`;});box.innerHTML=svg+'</svg>';}
const traceRows=a=>Array.isArray(a.trace?.rows)?a.trace.rows:[];
function filteredTtis(){const filter=document.getElementById('event-filter').value,all=new Set();activeArms().forEach(a=>traceRows(a).forEach(r=>{if(filter==='all'||(r.sample_reasons||[]).includes(filter))all.add(r.tti);}));return [...all].sort((a,b)=>a-b);}
function drawTrace(){const key=document.getElementById('trace-metric').value,arms=activeArms(),box=document.getElementById('trace-chart'),series=arms.map(a=>({a,rows:traceRows(a).filter(r=>Number.isFinite(Number(r[key])))})).filter(s=>s.rows.length);
if(!series.length){box.innerHTML='<div class="empty">没有启用 TTI trace；请用 sampled 或 full</div>';return;}const allRows=series.flatMap(s=>s.rows),xs=allRows.map(r=>r.tti),ys=allRows.map(r=>Number(r[key])),[x0,x1]=extent(xs),[y0a,y1]=extent(ys),y0=Math.min(0,y0a),w=980,h=330,L=72,R=24,T=22,B=52,x=v=>L+(v-x0)/(x1-x0)*(w-L-R),y=v=>T+(y1-v)/(y1-y0)*(h-T-B);let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="逐 TTI 多算法趋势">`;
for(let i=0;i<5;i++){const v=y0+(y1-y0)*i/4,yy=y(v);svg+=`<line class="gridline" x1="${L}" y1="${yy}" x2="${w-R}" y2="${yy}"/><text x="${L-8}" y="${yy+4}" text-anchor="end">${v.toPrecision(3)}</text>`;}svg+=`<line class="axis" x1="${L}" y1="${h-B}" x2="${w-R}" y2="${h-B}"/>`;
series.forEach((s,seriesIndex)=>{const offset=(seriesIndex-(series.length-1)/2)*7,pts=s.rows.map(r=>`${x(r.tti)+offset},${y(Number(r[key]))}`).join(' ');svg+=`<polyline points="${pts}" fill="none" stroke="${s.a.colour}" stroke-width="2" opacity=".78"/>`;s.rows.forEach(r=>{const event=(r.sample_reasons||[]).some(v=>v!=='uniform'&&v!=='full');svg+=`<circle class="trace-point" data-tti="${r.tti}" data-has-grants="${Boolean(r.grants?.length)}" cx="${x(r.tti)+offset}" cy="${y(Number(r[key]))}" r="${event?5:3}" fill="${s.a.colour}" tabindex="0"><title>${esc(s.a.label)} · TTI ${r.tti} · ${key}=${r[key]} · ${(r.sample_reasons||[]).join('/')}</title></circle>`;});});box.innerHTML=svg+'</svg>';box.querySelectorAll('.trace-point').forEach(p=>{const open=()=>{selectedTti=Number(p.dataset.tti);setTab('tti');renderTti();};p.addEventListener('click',open);p.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}});});updateTtiOptions();}
function updateTtiOptions(){const ttis=filteredTtis(),sel=document.getElementById('tti-select'),old=selectedTti;sel.innerHTML=ttis.map(t=>`<option value="${t}">TTI ${t}</option>`).join('');if(old!=null&&ttis.includes(old))sel.value=String(old);else selectedTti=ttis[0]??null;if(selectedTti!=null)sel.value=String(selectedTti);}
function grantTable(grants){if(!grants?.length)return '<div class="empty">该算法在此采样 TTI 没有 grant（这是真 idle，不是缺样本）。</div>';return `<div class="table-scroll grant-table"><table><thead><tr><th>UE</th><th>模式</th><th>RBG</th><th>MCS/rank</th><th>队列前</th><th>TBS/payload/ACK</th><th>SINR pred → RX</th><th>BLER / draw</th><th>OLLA 前→后</th><th>PF metric</th><th>选择原因</th></tr></thead><tbody>${grants.map(g=>`<tr><th>UE ${g.ue}</th><td>${esc(g.transmission_mode)} / ${esc(g.harq_tx_mode)}</td><td>${esc((g.rbg_indices||[]).join(','))}</td><td>${g.mcs} / ${g.rank}</td><td>${g.queue_bytes_before}</td><td>${g.scheduled_bytes} / ${g.payload_bytes} / ${g.acked_bytes}</td><td>${g.mcs_input_sinr_db} → ${g.sinr_db}</td><td>${g.bler} / ${g.harq_random_draw}</td><td>${g.su_olla_before_mcs} → ${g.su_olla_after_mcs}</td><td>${g.scheduler_metric}</td><td>${esc(g.plan_selected_reason)}</td></tr>`).join('')}</tbody></table></div>`;}
function renderTti(){updateTtiOptions();const host=document.getElementById('tti-details');if(selectedTti==null){host.innerHTML='<div class="empty">无可用 TTI 样本</div>';return;}document.getElementById('tti-select').value=String(selectedTti);host.innerHTML=activeArms().map(a=>{const r=traceRows(a).find(x=>Number(x.tti)===selectedTti);if(!r)return `<article class="tti-card" data-arm-id="${esc(a.result_id)}" style="--arm:${a.colour}"><h3>${esc(a.label)}</h3><div class="empty">此 TTI 未被该算法的 sampled 轨迹采集；不能解释为 idle。使用 full 可消除该缺口。</div></article>`;return `<article class="tti-card" data-arm-id="${esc(a.result_id)}" style="--arm:${a.colour}"><h3>${esc(a.label)} · TTI ${r.tti} · ${esc((r.sample_reasons||[]).join(' / '))}</h3><div class="tti-summary"><div><small>slot / snapshot</small><b>${esc(r.slot)} / ${r.snapshot}</b></div><div><small>占用 RBG</small><b>${r.occupied_rbg}</b></div><div><small>调度 UE</small><b>${r.scheduled_user_count}</b></div><div><small>ACK bytes</small><b>${r.acked_bytes}</b></div><div><small>NACK / ReTx</small><b>${r.nack_count} / ${r.retx_count}</b></div><div><small>队列 after</small><b>${r.backlog_bytes_after}</b></div><div><small>模式</small><b>${esc((r.transmission_modes||[]).join('+')||'idle')}</b></div><div><small>候选 UE</small><b>${esc((r.candidate_ues||[]).join(',')||'-')}</b></div></div>${grantTable(r.grants)}</article>`;}).join('');}
function renderAll(){drawBar();drawUserCdf();drawTrace();renderTti();}
document.getElementById('cell-metric').value=DATA.primary_kpi;document.getElementById('cell-metric').addEventListener('change',drawBar);
document.getElementById('user-metric').addEventListener('change',drawUserCdf);document.getElementById('trace-metric').addEventListener('change',drawTrace);
document.getElementById('event-filter').addEventListener('change',()=>{updateTtiOptions();renderTti();});document.getElementById('tti-select').addEventListener('change',e=>{selectedTti=Number(e.target.value);renderTti();});
document.getElementById('tti-prev').addEventListener('click',()=>{const a=filteredTtis(),i=a.indexOf(selectedTti);selectedTti=a[Math.max(0,i-1)]??selectedTti;renderTti();});
document.getElementById('tti-next').addEventListener('click',()=>{const a=filteredTtis(),i=a.indexOf(selectedTti);selectedTti=a[Math.min(a.length-1,i+1)]??selectedTti;renderTti();});
renderAll();
"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>{_esc(headline)}</title><style>{css}</style></head><body><div class="wrap" id="share-surface">
<header class="hero"><div><span class="eyebrow">SUPERRAN · MULTI-ALGORITHM EVIDENCE</span><h1>{_esc(headline)}</h1>
<p>数据集 {_esc(comparison.get('dataset_id') or '-')} · {generated}</p></div><div class="hero-badges"><span>{len(comparison['arms'])} 个算法</span><span>基线 {_esc(baseline['label'])}</span><span>{comparison['fairness']['n_rep']} 次 CRN 重复</span><span>主 KPI {_esc(primary_spec.label)}</span><span>预注册 {_esc(comparison['primary_lock']['status'])}</span></div></header>
{actions}<div class="arm-bar"><strong>算法系列</strong>{algorithm_controls}</div>
<nav class="work-tabs" aria-label="对比分析视图"><button data-tab="overview" aria-selected="true">总览</button><button data-tab="matrix" aria-selected="false">KPI 矩阵</button><button data-tab="users" aria-selected="false">用户分布</button><button data-tab="trend" aria-selected="false">TTI 趋势</button><button data-tab="tti" aria-selected="false">单 TTI</button><button data-tab="gates" aria-selected="false">统计门禁</button></nav>
<main><section class="panel active" data-panel="overview"><div class="panel-head"><div><h2>主结果与算法绝对值</h2><p class="source-note">算法保持同屏；Tab 按问题切换，不按算法切换。</p></div><div class="controls"><label>KPI <select id="cell-metric">{cell_options}</select></label></div></div><div id="headline-cards" class="cards"></div><div id="cell-chart" class="chart"></div><div class="callout"><strong>判读纪律：</strong>柱高是绝对 KPI，误差棒是单臂 95% CI；算法胜负必须看“统计门禁”中的配对差值区间、Wilcoxon 与多候选 Holm 校正，不能用两根柱子肉眼相减。</div></section>
<section class="panel" data-panel="matrix"><div class="panel-head"><div><h2>算法 × KPI 矩阵</h2><p class="source-note">每个候选同时显示绝对值与相对基线的配对 Δ；用于发现容量、体验、时延、资源和可靠性取舍，非预注册 KPI 只作探索。</p></div></div><div class="table-scroll"><table id="kpi-matrix"><thead><tr><th>KPI</th>{''.join('<th data-arm-id="'+_esc(arm['result_id'])+'">'+_esc(arm['label'])+'</th>' for arm in comparison['arms'])}</tr></thead><tbody>{''.join('<tr><th>'+_esc(cell_specs[key].label)+'<small style="display:block;color:var(--muted)">'+_esc(key)+'</small></th>'+''.join('<td data-arm-id="'+_esc(arm['result_id'])+'">'+_matrix_cell(comparison,arm,key,cell_specs[key])+'</td>' for arm in comparison['arms'])+'</tr>' for key in comparison['common_cell_kpis'])}</tbody></table></div></section>
<section class="panel" data-panel="users"><div class="panel-head"><div><h2>跨算法用户分布</h2><p class="source-note">每条线的样本是各 UE 在 replication 间的均值；用于看边缘用户和公平性，不是包级 CDF。</p></div><div class="controls"><label>用户 KPI <select id="user-metric">{user_options}</select></label></div></div><div id="user-chart" class="chart"></div></section>
<section class="panel" data-panel="trend"><div class="panel-head"><div><h2>逐 TTI 抽样趋势</h2><p class="source-note">均匀锚点负责同一 TTI 对齐；较大的事件点来自 MU/NACK/重传/多 UE/outage。点击点进入单 TTI。</p></div><div class="controls"><label>轨迹量 <select id="trace-metric"><option value="occupied_rbg">占用 RBG</option><option value="acked_bytes">ACK bytes</option><option value="scheduled_bytes">TBS bytes</option><option value="nack_count">NACK 数</option><option value="backlog_bytes_after">队列 bytes</option><option value="scheduled_user_count">调度 UE 数</option></select></label></div></div><div id="trace-chart" class="chart"></div></section>
<section class="panel" data-panel="tti"><div class="panel-head"><div><h2>同一 TTI 并排复盘</h2><p class="source-note">“未采样”与“采样后 idle”严格区分；单 TTI 只解释机制，不产生收益结论。</p></div><div class="controls"><label>事件 <select id="event-filter"><option value="all">全部采样</option><option value="mu">MU</option><option value="nack">NACK</option><option value="retx">重传</option><option value="multi_ue">多 UE</option><option value="outage">outage</option><option value="uniform">均匀锚点</option></select></label><button id="tti-prev" type="button">上一条</button><select id="tti-select" aria-label="选择 TTI"></select><button id="tti-next" type="button">下一条</button></div></div><div id="tti-details" class="tti-grid"></div></section>
<section class="panel" data-panel="gates"><div class="panel-head"><div><h2>统计门禁与配置差异</h2><p class="source-note">主 KPI 候选均与同一基线配对；多候选使用 Holm step-down，只会收紧 Gate 3。</p></div></div><div class="callout"><strong>预注册身份：{_esc(comparison['primary_lock']['status'])}</strong> · {_esc(comparison['primary_lock']['reason'])}。只有 status=verified 时，统计显著且方向有利的候选才允许标为可发布胜者。</div><div class="stats-grid"><div><h3>主 KPI：{_esc(primary_spec.label)}</h3><div class="table-scroll"><table><thead><tr><th>候选</th><th>配对差值</th><th>95% CI</th><th>判决 p</th><th>单对判决</th><th>Holm</th><th>家族判决</th></tr></thead><tbody>{gate_rows}</tbody></table></div></div><div><div class="callout"><strong>公平性已硬校验：</strong>同一 dataset、模式、时长、载波、TDD、话务、KPI 口径和逐位一致的 (master_seed, replication)。任一不一致时页面拒绝生成。</div><details open><summary>算法配置差异</summary>{''.join('<h4 style="color:'+_esc(arm['colour'])+'">'+_esc(arm['label'])+'</h4><pre>'+_esc(json.dumps(arm.get('config_differences',[]),ensure_ascii=False,indent=2))+'</pre>' for arm in comparison['arms'] if not arm['baseline'])}</details></div></div><details><summary>所有算法告警</summary>{''.join('<h4>'+_esc(arm['label'])+'</h4><ul>'+''.join('<li>'+_esc(note)+'</li>' for note in arm.get('notes',[]))+'</ul>' for arm in comparison['arms'])}</details></section></main>
<script type="application/json" id="comparison-data">{payload_json}</script><script>{script}</script></div></body></html>"""


def write_comparison_report(
    result_ids: Sequence[str], *, baseline_result_id: str | None = None,
    primary_kpi: str = _PRIMARY_DEFAULT, title: str = "", serve: bool = True,
) -> dict[str, Any]:
    comparison = build_comparison(
        result_ids, baseline_result_id=baseline_result_id, primary_kpi=primary_kpi)
    html_text = render_html(comparison, title=title)
    out_dir = artifacts_root() / "kpi"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"kpi-compare-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    path = out_dir / f"{stem}.html"
    json_path = out_dir / f"{stem}.json"
    path.write_text(html_text, encoding="utf-8")
    json_path.write_text(
        json.dumps(_json_ready(comparison), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    url = None
    serve_error = None
    if serve:
        if not br.enabled():
            serve_error = "SUPERRAN_NO_SERVE 已关闭环回服务"
        else:
            url = br.serve(stem, html_text, title="多算法 KPI 对比", allowed=set())
            if url is None:
                serve_error = "环回服务启动失败；请直接打开 html_path"
    return {
        "comparison_id": stem,
        "html_path": str(path),
        "json_path": str(json_path),
        "url": url,
        "serve_error": serve_error,
        "tabs": ["总览", "KPI 矩阵", "用户分布", "TTI 趋势", "单 TTI", "统计门禁"],
        "algorithm_count": len(comparison["arms"]),
        "baseline_result_id": comparison["baseline_result_id"],
        "primary_kpi": comparison["primary_kpi"],
        "primary_lock": comparison["primary_lock"],
        "primary_comparisons": comparison["comparisons"][comparison["primary_kpi"]],
        "fairness": comparison["fairness"],
        "actions": {
            "download": ["完整对比 JSON", "算法 × KPI 汇总 CSV", "TTI 抽样与 grant CSV"],
            "copy_summary": True,
            "screenshot": "PNG when canvas is origin-clean; full-page SVG fallback",
            "web_share_with_copy_fallback": True,
            "print_pdf": True,
            "offline_safe": True,
        },
    }
