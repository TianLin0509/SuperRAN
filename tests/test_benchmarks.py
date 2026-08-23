"""Classic benchmark manifest and fast deterministic cases."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from superran import benchmarks


def test_manifest_is_locked_and_matches_runners() -> None:
    spec = benchmarks.load_spec()
    assert spec["locked_before_first_run"] is True
    assert len(spec["cases"]) == 10


def test_fast_classic_cases_pass() -> None:
    result = benchmarks.run_suite(case_ids=[
        "B01_awgn_shannon_siso",
        "B07_tdd_srs_reciprocity",
        "B08_jakes_doppler_time_scale",
        "B09_nr_tbs_rbg_monotonicity",
    ])
    assert result["overall_status"] == "pass", result["results"]
    assert result["provenance"]["spec_sha256"]
    assert result["provenance"]["git_diff_sha256"]
    assert result["provenance"]["source_tree_sha256"]
    assert result["provenance"]["git_capture_complete"] is True


def test_test_matrix_lists_every_test_file_exactly_once() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "run_test_matrix.py"
    spec = importlib.util.spec_from_file_location("_test_matrix", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    listed = list(module.QUICK + module.PHYSICS)
    discovered = sorted(p.name for p in (root / "tests").glob("test_*.py"))
    assert len(listed) == len(set(listed))
    assert sorted(listed) == discovered


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
