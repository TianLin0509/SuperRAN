"""sr_system_sim 的行为级测试：直接调工具函数，不走 stdio 全链路。

覆盖正常 capacity/experience 返回结构，以及三条硬失败路径（非固定格栅、
SRS 来源不符、ue_id 与轮转布局错位）。数据集是合成的小型 NPZ，秒级完成。
"""
from __future__ import annotations

import inspect
import json
import shutil
from dataclasses import replace
from functools import wraps
from pathlib import Path

import numpy as np
import pytest

from superran import amc_policy as ap
from superran import provenance
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
                   sir_db: np.ndarray | None = None,
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
        scalar__sir_dB=(np.full(n_samples, 30.0) if sir_db is None
                        else np.asarray(sir_db, dtype=float)),
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


def test_harq_process_default_is_identical_across_all_entry_points() -> None:
    signature_default = inspect.signature(srv.sr_system_sim).parameters[
        "harq_max_processes"].default
    control = next(row for row in specm._EDITABLE if row[0] == "harq_max_processes")
    assert sysm.SystemConfig().harq_max_processes == 8
    assert signature_default == 8
    assert specm._SIM_DEFAULTS["harq_max_processes"] == 8
    assert control[2] == "number" and control[3] == (1, 16, 1)


def test_runtime_cqi_switch_is_exposed_and_can_restore_offline_behavior() -> None:
    signature_default = inspect.signature(srv.sr_system_sim).parameters[
        "runtime_cqi_enabled"].default
    control = next(row for row in specm._EDITABLE if row[0] == "runtime_cqi_enabled")
    assert sysm.SystemConfig().cqi_report.enabled is True
    assert signature_default is True
    assert specm._SIM_DEFAULTS["runtime_cqi_enabled"] == "on"
    assert control[3] == ["on", "off"]

    _write_dataset()
    out = _run(traffic_model="full_buffer", runtime_cqi_enabled=False)
    assert "error" not in out, out.get("error")
    assert out["config"]["system"]["cqi_report"]["enabled"] is False
    assert "cqi_update_count_mean" not in out["cell"]
    assert "cqi_age_tti_max" not in out["cell"]


def test_capacity_ok() -> None:
    """"容量仿真"就是 full_buffer 话务，不是另一条路径。"""
    _write_dataset()
    out = _run(traffic_model="full_buffer")
    assert "error" not in out, out.get("error")
    served = out["cell"]["cell_served_mbps"]
    assert isinstance(served, dict) and served["mean"] > 0  # KpiStat 结构
    assert len(out["users"]) == 2
    assert out["notes"]
    assert out["provenance"]["compatibility"]["status"] == "unknown"
    assert any("provenance" in note for note in out["notes"])
    # TS 128 552 V19.5.0 p54：样本只在 buffer emptied 事件上形成。full buffer 下
    # 没有该事件 ⇒ **标准 KPI 无样本**，重复实验只汇总数值型 KPI，因此这些键
    # 不出现在 cell 里；为什么不出现由 notes 明说，不是静默消失。
    assert "drb_throughput_rel19_mbps" not in out["cell"]
    assert "cell_experienced_mbps" not in out["cell"]
    # **两个工程字段照常有值**，用户要数拿得到，只是不借标准的名。
    assert out["cell"]["active_window_goodput_mbps"]["mean"] > 0
    assert out["cell"]["ue_served_p5_mbps"]["mean"] > 0
    assert out["cell"]["drb_throughput_inflight_share"]["mean"] == 1.0
    assert out["cell"]["drb_throughput_completed_bursts"]["mean"] == 0
    assert any("full buffer" in note for note in out["notes"])
    # 容量口径的指标照常在。**别断言 occupancy==1**：HARQ 反馈时序、进程池与
    # 时隙类型约束仍可能留下空闲机会，那是物理不是缺陷。
    # "满缓冲退化成全带宽"这条不变量在 test_physics_invariants 里用 DDDD 守。
    assert out["cell"]["serving_cell_prb_utilization"]["mean"] > 0.0
    assert out["cell"]["cell_served_mbps"]["mean"] > 0.0
    assert out["config"]["system"]["model_version"] == "experience_v2"
    assert "evaluation_mode" not in out["config"]["system"]


def test_experience_ok() -> None:
    _write_dataset()
    out = _run(
        traffic_model="ftp3",
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
        traffic_model="ftp3",
        scheduler="pf", algorithm_label="PF 基线",
        tti_trace_max_points=24)
    candidate = _run(
        traffic_model="ftp3",
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
        duration_s=0.5,
        num_replications=4, replication_workers=1)
    parallel = _run(
        duration_s=0.5,
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


def test_serving_cell_selection_picks_one_cell_and_keeps_ue_rotation() -> None:
    """多小区数据集里挑一个小区做单小区调度。

    3GPP TR 36.814 的标准撒点密度是每扇区 10 个 UE，21 扇区要撒 210 个；
    仿真器一次只调度一个小区，所以必须能挑出属于某个小区的那批。
    **最容易错的地方是轮转不变式**：样本 i 必须属于 UE i % num_ues，
    下游 group_samples_by_ue 依赖它；筛完不重排就会静默把不同 UE 的快照混掉。
    """
    # 8 个样本 / 4 个 UE：UE0,UE2 在小区 0，UE1,UE3 在小区 1（按轮转布局）
    _write_dataset(n_samples=8, n_ues=4,
                   serving_cell_indices=np.asarray([0, 1, 0, 1] * 2),
                   cells_configured=2)
    out = _run(serving_cell=0)
    assert "error" not in out, out.get("error")
    sel = out["serving_cell_selection"]
    assert sel["requested"] == 0
    assert sel["ues_in_cell"] == 2 and sel["ues_in_dataset"] == 4
    assert sel["ue_count_by_cell"] == {"0": 2, "1": 2}
    # 只剩被选中那两个 UE，且样本数按快照数 x 选中 UE 数收缩
    assert len(out["users"]) == 2
    assert out["num_samples"] == 4


def test_serving_cell_selection_reports_real_interference_profile() -> None:
    """**选哪个小区是物理选择，工具必须把判断依据一起给出来。**

    依据是选中小区那批 UE 的几何 SIR 与由它推出的 IoT
    （``IoT = SIR/(SIR-SINR)``，与仿真里 ``cell["iot_db_median"]`` 同一个公式）。
    没有 wrap-around 的撒点里，边缘小区邻区不完整 ⇒ SIR 偏高、IoT 偏低，
    选错会系统性低估干扰。

    **棘轮**：早先这里去查数据集的 ``iot_dl_dB`` 标量——多小区数据集根本没有那个
    measurement，裸 ``except`` 把它吞成恒 None，看起来像"没有干扰信息"。
    把它改回去，下面三条断言全红。
    """
    # UE0/UE2 在小区 0（SIR 20 dB，被包围得紧），UE1/UE3 在小区 1（SIR 34 dB，边缘）。
    # SIR 必须 > SINR(=18 dB)，否则物理上不成立——见下面第二段。
    _write_dataset(n_samples=8, n_ues=4,
                   serving_cell_indices=np.asarray([0, 1, 0, 1] * 2),
                   sir_db=np.asarray([20.0, 34.0, 20.0, 34.0] * 2),
                   cells_configured=2)
    inner = _run(serving_cell=0)["serving_cell_selection"]
    edge = _run(serving_cell=1)["serving_cell_selection"]
    assert inner["selected_interference_note"] is None
    assert edge["selected_interference_note"] is None
    # 几何 SIR 如实回报，不是 None 也不是全数据集的中位
    assert abs(inner["selected_geometric_sir_db_median"] - 20.0) < 1e-6
    assert abs(edge["selected_geometric_sir_db_median"] - 34.0) < 1e-6
    # IoT 由同一对 SINR/SIR 推出：SIR 越低（被包围越完整）IoT 越高
    def _iot(sir_db_val: float, sinr_db_val: float = 18.0) -> float:
        sir_lin = 10.0 ** (sir_db_val / 10.0)
        sinr_lin = 10.0 ** (sinr_db_val / 10.0)
        return 10.0 * np.log10(sir_lin / (sir_lin - sinr_lin))
    assert abs(inner["selected_iot_db_median"] - _iot(20.0)) < 1e-2
    assert abs(edge["selected_iot_db_median"] - _iot(34.0)) < 1e-2
    assert inner["selected_iot_db_median"] > edge["selected_iot_db_median"]
    assert "under-estimate" in inner["selection_criterion"]

    # **拿不到就明说拿不到，不留哑 None。** SIR <= SINR 物理上不成立
    # （夹逼或口径错配才会出现），此时 IoT 没有有限值，必须给出原因。
    _write_dataset(n_samples=8, n_ues=4,
                   serving_cell_indices=np.asarray([0, 1, 0, 1] * 2),
                   sir_db=np.full(8, 6.0),   # < SINR 18 dB
                   cells_configured=2)
    bad = _run(serving_cell=0)["serving_cell_selection"]
    assert bad["selected_iot_db_median"] is None
    assert "SIR<=SINR" in bad["selected_interference_note"]
    # 几何 SIR 本身仍然如实给出——它不依赖 IoT 能不能算
    assert abs(bad["selected_geometric_sir_db_median"] - 6.0) < 1e-6


def test_serving_cell_selection_rejects_empty_and_single_ue_cells() -> None:
    _write_dataset(n_samples=8, n_ues=4,
                   serving_cell_indices=np.asarray([0, 1, 0, 1] * 2),
                   cells_configured=2)
    # 不存在的小区：报错里要把可选小区和各自 UE 数给出来
    out = _run(serving_cell=7)
    assert "error" in out and "没有任何 UE" in out["error"]
    assert "'0': 2" in out["error"] or "0: 2" in out["error"]
    # 只有 1 个 UE 的小区：调度是多用户取舍，单用户测不出调度器
    _write_dataset(n_samples=8, n_ues=4,
                   serving_cell_indices=np.asarray([0, 1, 1, 1] * 2),
                   cells_configured=2)
    out = _run(serving_cell=0)
    assert "error" in out and "单用户小区测不出调度器" in out["error"]


def test_serving_cell_selection_refuses_to_mix_with_rb_power_control() -> None:
    """逐 RB 功控的几何量直接来自数据集、不随样本筛选走，混用是半对半错。"""
    _write_dataset(n_samples=8, n_ues=4,
                   serving_cell_indices=np.asarray([0, 1, 0, 1] * 2),
                   cells_configured=2)
    out = _run(serving_cell=0, rb_power_control_enabled=True)
    assert "error" in out and "不能与 rb_power_control_enabled 同开" in out["error"]


def test_multi_serving_cell_error_points_at_the_selection_parameter() -> None:
    _write_dataset(
        serving_cell_indices=np.asarray([0, 1] * 4),
        cells_configured=2)
    out = _run(rb_power_control_enabled=False)
    assert "error" in out and "serving_cell=<小区编号>" in out["error"]


def test_multicell_dataset_missing_serving_identity_hard_fails() -> None:
    _write_dataset(
        include_serving_cell_indices=False,
        cells_configured=2)
    out = _run(rb_power_control_enabled=False)
    assert "error" in out and "serving_cell_index" in out["error"]


def test_zero_speed_is_not_defaulted_to_three_kmh() -> None:
    _write_dataset(ue_speed_kmh=0.0)
    out = _run()
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
    out = _run(srs_period_ms=10.0)
    assert "error" not in out, out.get("error")
    aging = out["csi_aging"]
    assert aging["requested_config"]["srs_period_ms"] == 10.0
    assert aging["effective_config"]["srs_period_ms"] == 20.0
    assert aging["config"]["full_sweep_ms"] == 340.0
    assert any("生效全局周期 20 ms" in note for note in out["notes"])


def test_srs_capacity_error_returns_structured_tool_error() -> None:
    _write_dataset(n_samples=5, n_ues=5)
    out = _run(
        srs_hopping=False,
        srs_period_adaptive=False)
    assert "error" in out
    assert "srs_hopping=false" in out["error"]
    assert "17 frequency-resource" in out["error"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
