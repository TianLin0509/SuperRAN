"""Compatibility facade for SuperRAN's first-party physical core.

The module name remains for API compatibility with existing callers.  It no
longer locates, imports, or mutates a ChannelHub/MSG-Platform source tree.  All
required statistical-channel and PHY behavior comes from :mod:`superran.native`;
optional third-party engines are reported independently.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def channelhub_root() -> Path:
    """Return the SuperRAN root for legacy diagnostics.

    Historically this returned an external checkout.  Returning the current
    project makes the changed ownership explicit while preserving callers that
    display the implementation root.  ``SUPERRAN_CHANNELHUB`` is intentionally
    ignored and therefore cannot alter runtime behavior.
    """
    return project_root()


def channelhub_resource_roots() -> list[Path]:
    """Local roots that may carry optional scene assets."""
    return [project_root()]


def _ensure_path() -> None:
    """Legacy no-op: the first-party core is installed with SuperRAN."""
    return None


@dataclass
class Capability:
    name: str
    available: bool
    detail: str = ""
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class SourceContractReport:
    compatible: bool
    checks: dict[str, dict[str, Any]]
    blockers: tuple[str, ...]
    contract_id: str = "superran-native-source-contract-v2"

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "compatible": self.compatible,
            "checks": self.checks,
            "blockers": list(self.blockers),
        }


@lru_cache(maxsize=1)
def probe_source_contract() -> SourceContractReport:
    """Validate the local narrow waist without importing another repository."""
    from . import native

    checks: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks[name] = {"passed": bool(passed), "detail": str(detail)}
        if not passed:
            blockers.append(name)

    record(
        "lmmse_frequency_interpolate",
        callable(native.lmmse_frequency_interpolate),
        "SuperRAN-owned arbitrary pilot-to-target LMMSE",
    )
    record(
        "srs_bandwidth_selector",
        callable(auto_select_c_srs),
        "SuperRAN-owned B_SRS/target_rb selector boundary",
    )
    record(
        "array_port_order",
        all(
            hasattr(native.PortIndex(1, 1), name)
            for name in ("permute_from_layout", "type1_to_canonical")
        ),
        "pol_h_v/top_to_bottom and explicit legacy permutation",
    )
    required = {"h_ul_true", "h_ul_est", "h_dl_true", "h_dl_est"}
    fields = set(native.ChannelSample.model_fields)
    record(
        "paired_channel_roles",
        required.issubset(fields),
        f"required={sorted(required)}; present={sorted(required & fields)}",
    )
    record(
        "source_registry",
        "internal_sim" in native.SOURCE_REGISTRY,
        f"first_party={sorted(native.SOURCE_REGISTRY)}; optional engines are separate capabilities",
    )
    record(
        "external_source_independence",
        "msg_embedding" not in globals(),
        "no external path discovery/import; SUPERRAN_CHANNELHUB cannot select implementation",
    )
    table_state = ensure_spec_tables()
    record(
        "cdl_tables",
        bool(table_state.get("applied")) and not table_state.get("error"),
        str(table_state),
    )
    digests = native.standard_table_digests()
    record(
        "tdl_srs_tables",
        digests.get("tdl") == native.TDL_TABLES_SHA256
        and digests.get("srs") == native.SRS_BW_TABLE_SHA256,
        str(digests),
    )
    return SourceContractReport(not blockers, checks, tuple(blockers))


def _probe_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(str(name).split(".")[0]) is not None
    except (ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def probe_capabilities() -> tuple[Capability, ...]:
    """Report every engine SuperRAN can generate channels with.

    The list length must not depend on what is installed on this machine —
    only ``available`` and ``missing`` may change.  Callers index it by name,
    and an engine that disappears when its runtime is absent turns a missing
    optional dependency into a ``KeyError`` that looks like a broken tool.
    """
    internal_missing = [
        module
        for module in ("numpy", "scipy", "yaml", "filelock", "mcp")
        if not _probe_module(module)
    ]
    contract = probe_source_contract()
    internal_missing.extend(f"source-contract:{name}" for name in contract.blockers)
    from .sionna_rt import adapter_missing as _rt_missing  # noqa: PLC0415

    # 只探顶层包名。探 ``sionna.rt`` 会为了拿父包 __path__ 真的 import sionna，
    # 连带拉起 mitsuba / drjit / matplotlib（历史实测 +455 MB）。
    rt_missing = _rt_missing()
    return (
        Capability(
            "internal_sim",
            not internal_missing,
            (
                "SuperRAN first-party 38.901 statistical channel source"
                if not internal_missing
                else "SuperRAN local source dependencies or contract are incomplete"
            ),
            internal_missing,
        ),
        Capability(
            "sionna_rt",
            not rt_missing,
            (
                "SuperRAN first-party direct Sionna RT adapter: ray-traced multipath "
                "on the shared effective-subarray array model"
                if not rt_missing
                else "Optional direct Sionna RT adapter needs sionna-rt; no fallback engine is used"
            ),
            rt_missing,
        ),
    )


def warmup() -> dict[str, Any]:
    """Preload numeric extensions on the main thread and validate the core."""
    import time

    started = time.perf_counter()
    info: dict[str, Any] = {}
    try:
        import numpy
        import scipy.interpolate  # noqa: F401
        import scipy.io  # noqa: F401
        import scipy.linalg
        import scipy.spatial  # noqa: F401
        import scipy.special  # noqa: F401
        import scipy.stats  # noqa: F401

        contract = probe_source_contract()
        info["source_contract"] = contract.as_dict()
        if not contract.compatible:
            raise RuntimeError("source contract mismatch: " + ", ".join(contract.blockers))
        rng = numpy.random.default_rng(0)
        matrix = rng.standard_normal((16, 8)) + 1j * rng.standard_normal((16, 8))
        scipy.linalg.svd(matrix, full_matrices=False)
        numpy.linalg.eigh(matrix @ matrix.conj().T)
        numpy.fft.ifft(matrix, axis=0)
        info.update(
            {
                "sources": ["internal_sim"],
                "implementation_root": str(project_root()),
                "external_source_tree": None,
                "cdl_spec_tables": ensure_spec_tables(),
                "ok": True,
            }
        )
    except Exception as exc:  # noqa: BLE001
        info.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    info["elapsed_s"] = round(time.perf_counter() - started, 2)
    return info


def ensure_spec_tables() -> dict[str, Any]:
    """Validate and report the immutable local CDL table source."""
    from .spec38901 import apply_spec_tables

    return apply_spec_tables()


def _engine_registry() -> dict[str, Any]:
    """First-party statistical core plus any optional engine that is installed.

    Optional engines live in their own module so that a missing third-party
    runtime can never affect ``internal_sim``; they are only imported when the
    top-level dependency probe already succeeded.
    """
    from .native import SOURCE_REGISTRY

    registry: dict[str, Any] = dict(SOURCE_REGISTRY)
    from .sionna_rt import OPTIONAL_SOURCE_REGISTRY, adapter_missing  # noqa: PLC0415

    if not adapter_missing():
        registry.update(OPTIONAL_SOURCE_REGISTRY)
    return registry


def require_source(name: str) -> Any:
    registry = _engine_registry()
    cap = {item.name: item for item in probe_capabilities()}.get(str(name))
    if cap is None:
        raise RuntimeError(f"unregistered engine {name!r}; available={sorted(registry)}")
    if not cap.available:
        raise RuntimeError(
            f"仿真引擎 {name!r} 在本机不可用：{cap.detail}"
            + (f"（缺 {', '.join(cap.missing)}）" if cap.missing else "")
        )
    if str(name) not in registry:
        raise RuntimeError(
            f"引擎 {name!r} 报告可用但没有注册实现；这是适配层 bug，不做静默回退"
        )
    return registry[str(name)]


def cdl_profile(name: str) -> Any:
    from .native import get_channel_profile

    return get_channel_profile(name)


def list_channel_models() -> dict[str, list[str]]:
    from .native import list_channel_models as _list

    return _list()


def iter_samples(source_name: str, cfg: dict[str, Any]) -> Iterator[Any]:
    source = require_source(source_name)(dict(cfg))
    for sample in source.iter_samples():
        if source_name in ("internal_sim", "sionna_rt"):
            _validate_internal_site_state_contract(sample, cfg)
        yield sample


def describe(source_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    return require_source(source_name)(dict(cfg)).describe()


def auto_select_c_srs(num_rb: int, *, B_SRS: int = 0, target_rb: int | None = None) -> int:
    """Choose a deterministic SRS bandwidth row for a carrier.

    The validated 272-RB/B_SRS=1/16-RB profile is C_SRS=63.  Other carriers
    use a conservative local row solely for non-hopping/probe setup; product
    hopping remains hard-gated in :func:`superran.physical.srs_config`.
    """
    n = int(num_rb)
    b = int(B_SRS)
    if n == 272 and b == 1 and (target_rb is None or int(target_rb) == 16):
        return 63
    if n < 1 or b not in (0, 1, 2, 3):
        raise ValueError("invalid SRS carrier or B_SRS")
    from .native import SRS_BW_TABLE

    desired = n if target_rb is None else min(n, max(int(target_rb), 4))
    candidates = [
        row
        for row in SRS_BW_TABLE
        if row.m_srs[0] <= n and row.m_srs[b] <= desired
    ]
    if not candidates:
        raise ValueError(
            f"no SRS bandwidth row fits carrier={n}, B_SRS={b}, target_rb={desired}"
        )
    return max(candidates, key=lambda row: (row.m_srs[b], row.m_srs[0], row.c_srs)).c_srs


def _validate_internal_site_state_contract(sample: Any, cfg: dict[str, Any]) -> None:
    n_sites = int(cfg.get("num_sites", 1) or 1)
    sectors = int(cfg.get("sectors_per_site", 1) or 1)
    if n_sites <= 1 and sectors <= 1:
        return
    meta = getattr(sample, "meta", {}) or {}
    expected = "same_site_shared_cross_site_independent_v1"
    if meta.get("site_state_policy") != expected:
        raise RuntimeError(
            "SuperRAN internal_sim 不满足多站传播状态契约："
            f"site_state_policy={meta.get('site_state_policy')!r}，要求 {expected!r}"
        )
    group_ids = list(meta.get("physical_site_group_ids") or [])
    is_los_all = list(meta.get("is_los_all") or [])
    ds_all = list(meta.get("sample_tau_rms_all_ns") or [])
    sf_all = list(meta.get("shadow_fading_all_db") or [])
    if not group_ids or not (len(group_ids) == len(is_los_all) == len(ds_all) == len(sf_all)):
        raise RuntimeError(
            "SuperRAN 站点传播元数据不完整：需要等长的 physical_site_group_ids/"
            "is_los_all/sample_tau_rms_all_ns/shadow_fading_all_db"
        )
    shared: dict[int, tuple[bool, float, float]] = {}
    for group, los, delay, shadow in zip(group_ids, is_los_all, ds_all, sf_all, strict=True):
        state = (bool(los), float(delay), float(shadow))
        key = int(group)
        if key in shared and shared[key] != state:
            raise RuntimeError(f"同一物理站 group={key} 的扇区没有共享 LOS/DS/SF")
        shared[key] = state


SUPERRAN_LEGACY_RECIPROCITY_CONTRACT = "superran-tdd-bs-ue-canonical-v1"
SUPERRAN_RECIPROCITY_CONTRACT = "superran-tdd-transpose-canonical-v2"
SUPERRAN_CANONICAL_CHANNEL_AXES = ("time", "rb", "bs_port", "ue_port")


def ul_estimate_to_dl_precoding_csi(
    h_ul_est: Any,
    *,
    expected_shape: tuple[int, ...] | None = None,
    contract_version: str | None = None,
) -> Any:
    """Map canonical UL SRS CSI to the downlink precoding convention.

    New first-party data uses physical ``H_UL = H_DL.T``.  Since both links are
    stored on canonical ``[BS,UE]`` axes, the stored zero-calibration tensors
    are equal and no conjugation is applied.  Historical v1 datasets used a
    Hermitian convention and retain the old conjugation through their explicit
    contract version.
    """
    import numpy as np

    arr = np.asarray(h_ul_est)
    if arr.ndim != 4:
        raise RuntimeError(
            "SuperRAN TDD 互易合同要求 h_ul_est 为 [time,rb,bs_port,ue_port] 四维张量；"
            f"实得 {arr.shape}"
        )
    if expected_shape is not None and arr.shape != tuple(expected_shape):
        raise RuntimeError(
            "SuperRAN TDD 互易合同要求 UL 估计与 DL 真值使用同一 canonical 轴；"
            f"实得 {arr.shape} vs {tuple(expected_shape)}"
        )
    if not np.isfinite(arr).all():
        raise RuntimeError("h_ul_est 含 NaN 或 Inf，无法构造下行预编码 CSI")
    version = contract_version or SUPERRAN_RECIPROCITY_CONTRACT
    if version == SUPERRAN_LEGACY_RECIPROCITY_CONTRACT:
        return np.conj(arr)
    if version != SUPERRAN_RECIPROCITY_CONTRACT:
        raise RuntimeError(f"unknown reciprocity contract {version!r}")
    return arr.copy()


def serving_channel(sample: Any, *, estimated: bool = False) -> Any:
    attrs = ("h_serving_est", "h_dl_est", "h_ul_est") if estimated else (
        "h_serving_true", "h_dl_true", "h_ul_true"
    )
    for attr in attrs:
        value = getattr(sample, attr, None)
        if value is not None:
            return value
    return None


def downlink_and_precoding_channels(sample: Any) -> tuple[Any, Any, Any, str]:
    h_dl_true = getattr(sample, "h_dl_true", None)
    h_ul_est = getattr(sample, "h_ul_est", None)
    h_dl_est = getattr(sample, "h_dl_est", None)
    if h_dl_true is not None:
        if h_ul_est is None:
            raise RuntimeError("paired/BOTH 样本有 h_dl_true 但没有 h_ul_est")
        meta = getattr(sample, "meta", {}) or {}
        contract = dict(meta.get("channel_contract") or {})
        # Missing provenance means a pre-v2 sample.  Fail-safe compatibility
        # keeps the historical Hermitian mapping instead of silently changing
        # old bytes to the new transpose-only convention.
        version = contract.get(
            "reciprocity_contract_version", SUPERRAN_LEGACY_RECIPROCITY_CONTRACT
        )
        mapped = ul_estimate_to_dl_precoding_csi(
            h_ul_est,
            expected_shape=tuple(getattr(h_dl_true, "shape", ())),
            contract_version=str(version),
        )
        return h_dl_true, mapped, h_dl_est, "ul_srs_estimate"
    h_true = getattr(sample, "h_serving_true", None)
    h_est = getattr(sample, "h_serving_est", None)
    link = str(getattr(sample, "link", "DL") or "DL").upper()
    source = "ul_srs_estimate" if link == "UL" else "dl_csirs_estimate"
    return h_true, h_est, h_dl_est, source
