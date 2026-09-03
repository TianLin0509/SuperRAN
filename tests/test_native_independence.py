"""Hard negative controls for first-party source ownership."""
from __future__ import annotations

import ast
import asyncio
import importlib.abc
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import channelhub, generate, load, native, physical, server  # noqa: E402


class _RejectMsgEmbedding(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        del path, target
        if fullname == "msg_embedding" or fullname.startswith("msg_embedding."):
            raise AssertionError(f"external runtime import attempted: {fullname}")
        return None


def _minimal_config() -> dict[str, object]:
    return {
        "num_samples": 2,
        "num_ues": 2,
        "num_rb": 4,
        "num_slots_per_sample": 1,
        "num_bs_tx_ant": 4,
        "num_bs_rx_ant": 4,
        "num_ue_tx_ant": 2,
        "num_ue_rx_ant": 2,
        "scenario": "UMa_NLOS",
        "channel_model": "CDL-C",
        "channel_est_mode": "ideal",
        "link": "BOTH",
        "seed": 903,
        "ue_seed": 904,
        "measurements": {"ssb_rsrp": False},
    }


def test_bogus_external_roots_cannot_change_first_party_bytes(monkeypatch) -> None:
    finder = _RejectMsgEmbedding()
    before = list(sys.path)
    sys.meta_path.insert(0, finder)
    try:
        monkeypatch.setenv("SUPERRAN_CHANNELHUB", str(ROOT / "does-not-exist-a"))
        first = list(channelhub.iter_samples("internal_sim", _minimal_config()))
        monkeypatch.setenv("SUPERRAN_CHANNELHUB", str(ROOT / "does-not-exist-b"))
        second = list(channelhub.iter_samples("internal_sim", _minimal_config()))
        report = channelhub.probe_source_contract()
        warmup = channelhub.warmup()
    finally:
        sys.meta_path.remove(finder)

    assert report.compatible and not report.blockers
    assert warmup["ok"] is True
    assert sys.path == before
    assert channelhub.channelhub_root() == ROOT
    assert len(first) == len(second) == 2
    for left, right in zip(first, second, strict=True):
        np.testing.assert_array_equal(left.h_dl_true, right.h_dl_true)
        np.testing.assert_array_equal(left.h_ul_true, right.h_ul_true)
        np.testing.assert_array_equal(left.h_ul_true, left.h_dl_true)
        assert left.meta["implementation"] == "superran-first-party"
        assert left.meta["channel_contract"]["reciprocity_contract_version"] == (
            channelhub.SUPERRAN_RECIPROCITY_CONTRACT
        )


def test_reciprocity_versions_are_explicit_and_not_shape_heuristics() -> None:
    value = np.asarray([[[[1 + 2j], [3 - 4j]]]], dtype=np.complex64)
    np.testing.assert_array_equal(
        channelhub.ul_estimate_to_dl_precoding_csi(value), value
    )
    np.testing.assert_array_equal(
        channelhub.ul_estimate_to_dl_precoding_csi(
            value, contract_version=channelhub.SUPERRAN_LEGACY_RECIPROCITY_CONTRACT
        ),
        np.conj(value),
    )


def test_mcp_surface_stays_complete_without_external_source_tree() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 35
    capabilities = {item.name: item for item in channelhub.probe_capabilities()}
    assert capabilities["internal_sim"].available
    assert all("ChannelHub" not in item.missing for item in capabilities.values())


def test_repository_runtime_has_no_msg_embedding_import_edge() -> None:
    violations: list[str] = []
    for base in (ROOT / "src" / "superran", ROOT / "scripts", ROOT / "tests"):
        for path in sorted(base.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                if any(name == "msg_embedding" or name.startswith("msg_embedding.") for name in modules):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert violations == []


def test_local_interference_projection_has_explicit_rank_axis() -> None:
    rng = np.random.default_rng(905)
    cross = rng.standard_normal((2, 1, 3, 4, 2)) + 1j * rng.standard_normal(
        (2, 1, 3, 4, 2)
    )
    serving = [
        rng.standard_normal((1, 3, 4, 2)) + 1j * rng.standard_normal((1, 3, 4, 2))
        for _ in range(2)
    ]
    result = physical.project_interference(cross, serving, max_rank=2)
    assert result["projected"].shape == (2, 1, 3, 2, 2)
    assert len(result["ranks"]) == 2
    assert all(1 <= rank <= 2 for rank in result["ranks"])
    assert np.isfinite(result["projected"]).all()


def test_per_ray_jones_xpr_and_los_k_are_not_double_counted() -> None:
    rng = np.random.default_rng(906)
    jones = native.polarization_jones_matrix(10.0, rng)
    np.testing.assert_allclose(np.abs(np.diag(jones)), 1.0, atol=1e-12)
    np.testing.assert_allclose(
        np.abs(jones[[0, 1], [1, 0]]), 10.0 ** (-10.0 / 20.0), atol=1e-12
    )

    cfg = _minimal_config()
    source = native.InternalSimSource(cfg)
    profile = native.get_channel_profile("CDL-D")
    altered_metadata = replace(profile, k_factor_dB=100.0)
    kwargs = {
        "n_time": 1,
        "n_rb": 4,
        "n_bs": 4,
        "n_ue": 2,
        "doppler_hz": 10.0,
        "realization_index": 0,
        "link_aod_rad": 0.2,
        "link_aoa_rad": -2.9,
        "link_zod_rad": 1.6,
        "link_zoa_rad": 1.5,
    }
    first = source._channel(profile, np.random.default_rng(907), **kwargs)  # noqa: SLF001
    second = source._channel(  # noqa: SLF001
        altered_metadata, np.random.default_rng(907), **kwargs
    )
    np.testing.assert_array_equal(first, second)


def test_local_standard_tables_match_frozen_independent_digests() -> None:
    digests = native.standard_table_digests()
    assert digests == {
        "tdl": native.TDL_TABLES_SHA256,
        "srs": native.SRS_BW_TABLE_SHA256,
    }
    assert channelhub.ensure_spec_tables()["sha256"]


def test_first_party_multislot_axis_is_preserved_without_complex_averaging(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SUPERRAN_ARTIFACTS", str(tmp_path / "artifacts"))
    cfg = _minimal_config()
    cfg["num_slots_per_sample"] = 3
    cfg["sample_interval_s"] = 0.005
    summary = generate.generate(cfg, num_samples=2, workers=1, collect_ssb=False)
    dataset = load(summary["dataset_id"])
    assert dataset.h_true.shape[:3] == (2, 3, 4)
    assert not np.array_equal(dataset.h_true[:, 0], dataset.h_true[:, 1])
    assert dataset.channel_contract["ofdm_to_slot_reduction"].startswith(
        "source already provides slot snapshots"
    )


def test_non_uma_umi_pathloss_fallback_is_never_silent() -> None:
    cfg = _minimal_config()
    cfg["scenario"] = "InF_NLOS"
    sample = next(channelhub.iter_samples("internal_sim", cfg))
    assert sample.meta["pathloss_model"] == "log-distance-engineering-fallback-v1"
    assert sample.meta["pathloss_model_approximate"] is True


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
