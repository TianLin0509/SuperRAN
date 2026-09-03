"""sr_system_sim 的行为级测试：直接调工具函数，不走 stdio 全链路。

覆盖正常 capacity/experience 返回结构，以及三条硬失败路径（非固定格栅、
SRS 来源不符、ue_id 与轮转布局错位）。数据集是合成的小型 NPZ，秒级完成。
"""
from __future__ import annotations

import json
import shutil
import inspect
from dataclasses import replace
from functools import wraps
from pathlib import Path

import numpy as np
import pytest

from superran import provenance
from superran import amc_policy as ap
from superran import server as srv
from superran import spec as specm
from superran import system as sysm
from superran.paths import datasets_dir

_DS = "ds_sys_sim_tool_test"


def _write_dataset(*, num_rb: int = 272, n_samples: int = 8, n_ues: int = 2,
                   csi_source: str = "ul_srs_estimate",
                   ue_ids_ok: bool = True,
                   include_ue_ids: bool = True,
                   serving_cell_indices: np.ndarray | None = None,
                   include_serving_cell_indices: bool = True,
                   cells_configured: int = 1,
                   ue_speed_kmh: float = 3.0) -> None:
    rng = np.random.default_rng(20260817)
    # Baseline terminal is 2T4R: the channel tensor retains four logical UE
    # antenna ports even though each SRS opportunity transmits only two.
    shape = (n_samples, 1, num_rb, 64, 4)
    h = ((rng.standard_normal(shape) + 1j * rng.standard_normal(shape))
         / np.sqrt(2)).astype(np.complex64)
    noise = (0.03 * ((rng.standard_normal(shape)
                      + 1j * rng.standard_normal(shape)) / np.sqrt(2))
             ).astype(np.complex64)
    d = datasets_dir() / _DS
    d.mkdir(parents=True, exist_ok=True)
    rotation = np.arange(n_samples) % n_ues
    ue_ids = rotation if ue_ids_ok else np.roll(rotation, 1)
    arrays = dict(
        h_true=h,
        h_est=h + noise,
        ue_position=rng.standard_normal((n_samples, 3)) * 50,
        scalar__sinr_dB=np.full(n_samples, 18.0),
        scalar__sir_dB=np.full(n_samples, 30.0),
        scalar__snr_dB=np.full(n_samples, 20.0),
        metastr__precoding_csi_source=np.asarray([csi_source] * n_samples),
    )
    if include_ue_ids:
        arrays["meta__ue_id"] = ue_ids.astype(float)
    if include_serving_cell_indices:
        serving = (
            np.zeros(n_samples, dtype=float)
            if serving_cell_indices is None
            else np.asarray(serving_cell_indices, dtype=float)
        )
        arrays["meta__serving_cell_index"] = serving
    np.savez(d / "channels.npz", **arrays)
    (d / "summary.json").write_text(json.dumps({
        "dataset_id": _DS,
        "source": "internal_sim",
        "shape": {"N": n_samples, "T": 1, "RB": num_rb,
                  "BS_ant": 64, "UE_ant": 4},
        "num_samples": n_samples,
        "cells_configured": int(cells_configured),
        "antenna_model": {"mode": "effective_subarray",
                          "port_order": "pol_h_v",
                          "vertical_index_order": "top_to_bottom"},
        "config": {
            "bandwidth_hz": 100e6, "subcarrier_spacing": 30000.0,
            "num_rb": num_rb, "num_ues": n_ues,
            "srs_periodicity": 20, "csirs_periodicity": 20,
            "mobility_mode": "static", "scenario": "UMa_NLOS",
            "ue_speed_kmh": float(ue_speed_kmh),
        },
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    shutil.rmtree(datasets_dir() / _DS, ignore_errors=True)


def _run(**kw):
    args = dict(dataset_id=_DS, duration_s=0.2, warmup_s=0.0,
                num_replications=2, mu_enabled=False)
    args.update(kw)
    return srv.sr_system_sim(**args)


def test_rank_switch_default_is_identical_across_all_entry_points() -> None:
    signature_default = inspect.signature(srv.sr_system_sim).parameters[
        "rank_switch_rule"].default
    control = next(row for row in specm._EDITABLE if row[0] == "rank_switch_rule")
    assert ap.RankConfig().switch_rule == "unified_ratio"
    assert signature_default == "unified_ratio"
    assert specm._SIM_DEFAULTS["rank_switch_rule"] == "unified_ratio"
    assert control[3][0] == "unified_ratio"
    assert ap.RankConfig().gain_factor_raise == 1.1
    assert ap.RankConfig().gain_factor_reduce == 1.1


def test_capacity_ok() -> None:
    _write_dataset()
    out = _run(evaluation_mode="capacity")
    assert "error" not in out, out.get("error")
    served = out["cell"]["cell_served_mbps"]
    assert isinstance(served, dict) and served["mean"] > 0  # KpiStat 结构
    assert len(out["users"]) == 2
    assert out["notes"]
    assert out["provenance"]["compatibility"]["status"] == "unknown"
    assert any("provenance" in note for note in out["notes"])


def test_experience_ok() -> None:
    _write_dataset()
    out = _run(
        evaluation_mode="experience", traffic_model="ftp3",
        algorithm_label="PF 基线", tti_trace_mode="sampled",
        tti_trace_max_points=32)
    assert "error" not in out, out.get("error")
    assert "cell_experienced_mbps" in out["cell"]
    assert out["kpi_format"]
    view = out["kpi_view"]
    assert view["result_id"] and Path(view["result_json_path"]).is_file()
    assert out["algorithm"]["label"] == "PF 基线"
    assert out["tti_trace"]["mode"] == "sampled"
    assert out["comparison_evidence"]["cell_samples_by_replication"]
    assert view["actions"]["offline_safe"] is True
    assert len(view["actions"]["download"]) == 3
    page = Path(view["html_path"]).read_text(encoding="utf-8")
    assert page.count("data-action=") == 4
    assert page.count("data-download=") == 3
    assert "superran_kpi_export_v1" in page
    assert "为什么优先展示这些 KPI" in page


def test_compare_two_system_results_generates_real_workbench() -> None:
    _write_dataset()
    baseline = _run(
        evaluation_mode="experience", traffic_model="ftp3",
        scheduler="pf", algorithm_label="PF 基线",
        tti_trace_max_points=24)
    candidate = _run(
        evaluation_mode="experience", traffic_model="ftp3",
        scheduler="rr", algorithm_label="RR 候选",
        tti_trace_max_points=24)
    out = srv.sr_compare_system_results(
        [baseline["kpi_view"]["result_id"], candidate["kpi_view"]["result_id"]],
        baseline_result_id=baseline["kpi_view"]["result_id"],
        primary_kpi="cell_experienced_mbps",
    )
    assert "error" not in out, out.get("error")
    assert out["algorithm_count"] == 2 and len(out["tabs"]) == 6
    assert out["primary_lock"]["status"] == "exploratory_unregistered"
    assert all(not row["publishable_winner"]
               for row in out["primary_comparisons"].values())
    page = Path(out["html_path"]).read_text(encoding="utf-8")
    assert "superran_kpi_comparison_v1" in page
    assert "同一 TTI 并排复盘" in page
    assert "Holm" in page


def test_matching_dataset_and_runtime_provenance_is_reported() -> None:
    _write_dataset()
    summary_path = datasets_dir() / _DS / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["provenance"] = provenance.snapshot(source="internal_sim")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8", newline="\n")
    out = _run()
    assert "error" not in out, out.get("error")
    assert out["provenance"]["compatibility"]["status"] == "match"


def test_replication_processes_preserve_results_and_report_actual_backend() -> None:
    _write_dataset()
    serial = _run(
        evaluation_mode="capacity", duration_s=0.5,
        num_replications=4, replication_workers=1)
    parallel = _run(
        evaluation_mode="capacity", duration_s=0.5,
        num_replications=4, replication_workers=2)
    assert "error" not in parallel, parallel.get("error")
    assert parallel["cell"] == serial["cell"]
    assert parallel["parallel"]["backend"] == "process"
    assert parallel["parallel"]["workers"] == 2
    assert parallel["replications"] == serial["replications"]


def test_non_company_grid_hard_fails() -> None:
    _write_dataset(num_rb=51)
    out = _run()
    assert "error" in out and "272" in out["error"]


def test_wrong_csi_provenance_hard_fails_with_aging() -> None:
    _write_dataset(csi_source="dl_csirs_estimate")
    out = _run(csi_aging=True)
    assert "error" in out and "ul_srs_estimate" in out["error"]
    out2 = _run(csi_aging=False)
    assert "error" not in out2
    assert any("来源" in n for n in out2["notes"])


def test_ue_id_misalignment_hard_fails() -> None:
    _write_dataset(ue_ids_ok=False)
    out = _run()
    assert "error" in out and "轮转" in out["error"]


def test_multi_ue_dataset_missing_identity_hard_fails() -> None:
    _write_dataset(include_ue_ids=False)
    out = _run()
    assert "error" in out and "ue_id" in out["error"] and "静默" in out["error"]


def test_multi_serving_cell_hard_fails_even_when_rb_power_control_is_off() -> None:
    _write_dataset(
        serving_cell_indices=np.asarray([0, 1] * 4),
        cells_configured=2)
    out = _run(rb_power_control_enabled=False)
    assert "error" in out and "不同 serving cell" in out["error"]
    assert "272-RB" in out["error"]


def test_multicell_dataset_missing_serving_identity_hard_fails() -> None:
    _write_dataset(
        include_serving_cell_indices=False,
        cells_configured=2)
    out = _run(rb_power_control_enabled=False)
    assert "error" in out and "serving_cell_index" in out["error"]


def test_zero_speed_is_not_defaulted_to_three_kmh() -> None:
    _write_dataset(ue_speed_kmh=0.0)
    out = _run(evaluation_mode="capacity")
    assert "error" not in out, out.get("error")
    assert out["csi_aging"]["speed_kmh"] == 0.0
    assert out["csi_aging"]["doppler_hz"] == 0.0
    assert out["csi_aging"]["coherence_time_ms"] is None


def test_result_reports_effective_allocator_period_not_request(monkeypatch) -> None:
    _write_dataset()
    original = sysm.build_link_tables

    @wraps(original)
    def _with_effective_period(*args, **kwargs):
        tables = original(*args, **kwargs)
        for table in tables:
            assert table.srs_resource_assignment is not None
            table.srs_resource_assignment = replace(
                table.srs_resource_assignment, period_ms=20.0)
        return tables

    monkeypatch.setattr(sysm, "build_link_tables", _with_effective_period)
    out = _run(evaluation_mode="capacity", srs_period_ms=10.0)
    assert "error" not in out, out.get("error")
    aging = out["csi_aging"]
    assert aging["requested_config"]["srs_period_ms"] == 10.0
    assert aging["effective_config"]["srs_period_ms"] == 20.0
    assert aging["config"]["full_sweep_ms"] == 340.0
    assert any("生效全局周期 20 ms" in note for note in out["notes"])


def test_srs_capacity_error_returns_structured_tool_error() -> None:
    _write_dataset(n_samples=5, n_ues=5)
    out = _run(
        evaluation_mode="capacity", srs_hopping=False,
        srs_period_adaptive=False)
    assert "error" in out
    assert "srs_hopping=false" in out["error"]
    assert "17 frequency-resource" in out["error"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
