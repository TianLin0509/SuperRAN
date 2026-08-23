"""sr_system_sim 的行为级测试：直接调工具函数，不走 stdio 全链路。

覆盖正常 capacity/experience 返回结构，以及三条硬失败路径（非固定格栅、
SRS 来源不符、ue_id 与轮转布局错位）。数据集是合成的小型 NPZ，秒级完成。
"""
from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from superran import provenance
from superran import server as srv
from superran.paths import datasets_dir

_DS = "ds_sys_sim_tool_test"


def _write_dataset(*, num_rb: int = 272, n_samples: int = 8, n_ues: int = 2,
                   csi_source: str = "ul_srs_estimate",
                   ue_ids_ok: bool = True) -> None:
    rng = np.random.default_rng(20260817)
    shape = (n_samples, 1, num_rb, 64, 2)
    h = ((rng.standard_normal(shape) + 1j * rng.standard_normal(shape))
         / np.sqrt(2)).astype(np.complex64)
    noise = (0.03 * ((rng.standard_normal(shape)
                      + 1j * rng.standard_normal(shape)) / np.sqrt(2))
             ).astype(np.complex64)
    d = datasets_dir() / _DS
    d.mkdir(parents=True, exist_ok=True)
    rotation = np.arange(n_samples) % n_ues
    ue_ids = rotation if ue_ids_ok else np.roll(rotation, 1)
    np.savez(
        d / "channels.npz",
        h_true=h,
        h_est=h + noise,
        ue_position=rng.standard_normal((n_samples, 3)) * 50,
        scalar__sinr_dB=np.full(n_samples, 18.0),
        scalar__sir_dB=np.full(n_samples, 30.0),
        scalar__snr_dB=np.full(n_samples, 20.0),
        meta__ue_id=ue_ids.astype(float),
        metastr__precoding_csi_source=np.asarray([csi_source] * n_samples),
    )
    (d / "summary.json").write_text(json.dumps({
        "dataset_id": _DS,
        "source": "internal_sim",
        "shape": {"N": n_samples, "T": 1, "RB": num_rb,
                  "BS_ant": 64, "UE_ant": 2},
        "num_samples": n_samples,
        "cells_configured": 1,
        "antenna_model": {"mode": "effective_subarray",
                          "port_order": "pol_h_v",
                          "vertical_index_order": "top_to_bottom"},
        "config": {
            "bandwidth_hz": 100e6, "subcarrier_spacing": 30000.0,
            "num_rb": num_rb, "num_ues": n_ues,
            "srs_periodicity": 20, "csirs_periodicity": 20,
            "mobility_mode": "static", "scenario": "UMa_NLOS",
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
    out = _run(evaluation_mode="experience", traffic_model="ftp3")
    assert "error" not in out, out.get("error")
    assert "cell_experienced_mbps" in out["cell"]
    assert out["kpi_format"]


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
