"""ChannelHub 适配层。

只把 ChannelHub 当普通 Python 库用：注入 src 路径、驱动 DataSource、
把产出的 ChannelSample 转成 SuperRAN 自己的数据结构。

不碰 platform/（后端、前端、任务队列），不走 data/bridge.py（那条路的输出
是为 MAE token 裁剪过的，见设计文档 v1 第三节）。
"""
from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

# ChannelHub 源码位置。按以下顺序查找，都找不到时报错并给出指引。
#   1. 环境变量 SUPERRAN_CHANNELHUB
#   2. 本项目的兄弟目录 / 上级目录下的常见位置
#   3. 若干本机习惯路径
_ENV_KEY = "SUPERRAN_CHANNELHUB"


def _candidate_roots() -> list[Path]:
    here = Path(__file__).resolve()
    project = here.parents[2]  # src/superran/channelhub.py -> 项目根
    # ``MSG-Platform`` is the current first-party checkout name; the three
    # historical names remain for existing developer machines.
    names = ("MSG-Platform", "ChannelHub_main", "ChannelHub", "channelhub")
    cands: list[Path] = []
    for base in (project.parent, project.parent.parent, project):
        cands.extend(base / n for n in names)
    cands.append(Path(r"C:\Vibe\AI\ChannelHub_main"))
    return cands


def _looks_like_channelhub(p: Path) -> bool:
    return (p / "src" / "msg_embedding" / "data" / "contract.py").is_file()


def channelhub_root() -> Path:
    """定位 ChannelHub 源码树。

    环境变量优先；否则在本项目周边找。找不到时返回第一个候选，
    由 :func:`probe_capabilities` 给出可读的错误说明。
    """
    env = os.environ.get(_ENV_KEY)
    if env:
        return Path(env)
    for c in _candidate_roots():
        if _looks_like_channelhub(c):
            return c
    return _candidate_roots()[0]


def channelhub_resource_roots() -> list[Path]:
    """Return ordered roots that may carry ChannelHub-side data assets.

    The active Python implementation and the optional ray-tracing scene
    catalogue need not live in the same checkout: the current MSG-Platform
    repository can provide ``src/msg_embedding`` while a legacy/full
    ChannelHub checkout provides ``configs/scenes``.  The active code root is
    always first; duplicates are removed without requiring paths to exist.
    """
    ordered = [channelhub_root(), *_candidate_roots()]
    unique: list[Path] = []
    seen: set[str] = set()
    for root in ordered:
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


_spec_tables_state: dict[str, Any] = {}


def _ensure_path() -> None:
    """把 ChannelHub 的 src 加进 sys.path（幂等），并确保标准 CDL 表已就位。"""
    src = channelhub_root() / "src"
    s = str(src)
    if s not in sys.path:
        sys.path.insert(0, s)
    state = ensure_spec_tables()
    if state.get("error"):
        raise RuntimeError(
            "ChannelHub CDL 标准表校准失败；拒绝继续生成可能伪装成 38.901 的信道："
            f"{state['error']}"
        )


def ensure_spec_tables() -> dict[str, Any]:
    """确保 ChannelHub 的 CDL 剖面表已被替换为逐字核对过的 38.901 查表值。

    **挂在 ``_ensure_path`` 上而不是只挂在 ``warmup`` 上是有意的。** 任何取
    ChannelHub 东西的路径都会先过 ``_ensure_path``——直接调 ``cdl_profile``、
    跑测试、在 REPL 里试都算。如果只在 ``warmup`` 里做，就会出现"跑 MCP 时
    信道是标准的、跑测试时不是"这种最难查的不一致。

    幂等：只在第一次真正执行替换。
    """
    if _spec_tables_state:
        return _spec_tables_state
    _spec_tables_state["pending"] = True  # 占位，防止替换过程里的重入
    try:
        from .spec38901 import apply_spec_tables  # noqa: PLC0415

        result = apply_spec_tables()
    except Exception as exc:  # noqa: BLE001
        result = {"applied": False, "error": f"{type(exc).__name__}: {exc}"}
    _spec_tables_state.clear()
    _spec_tables_state.update(result)
    return _spec_tables_state


@dataclass
class Capability:
    """某个仿真引擎在本机的可用性。"""

    name: str
    available: bool
    detail: str = ""
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "missing": self.missing,
        }


@dataclass(frozen=True)
class SourceContractReport:
    """Structural handshake between a source checkout and SuperRAN's adapter."""

    compatible: bool
    checks: dict[str, dict[str, Any]]
    blockers: tuple[str, ...]
    contract_id: str = "superran-source-contract-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "compatible": self.compatible,
            "checks": self.checks,
            "blockers": list(self.blockers),
        }


@lru_cache(maxsize=1)
def probe_source_contract() -> SourceContractReport:
    """Fail closed when imports exist but the adapter's physical API does not."""
    checks: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []

    def _record(name: str, passed: bool, detail: str) -> None:
        checks[name] = {"passed": bool(passed), "detail": str(detail)}
        if not passed:
            blockers.append(name)

    try:
        _ensure_path()
        from msg_embedding.channel_est import (  # noqa: PLC0415
            lmmse_frequency_interpolate,
        )

        _record(
            "lmmse_frequency_interpolate",
            callable(lmmse_frequency_interpolate),
            "pilot-to-full-RB LMMSE public export",
        )
    except Exception as exc:  # noqa: BLE001
        _record(
            "lmmse_frequency_interpolate",
            False,
            f"{type(exc).__name__}: {exc}",
        )

    try:
        from msg_embedding.ref_signals.srs import auto_select_c_srs  # noqa: PLC0415

        parameters = inspect.signature(auto_select_c_srs).parameters
        passed = {"B_SRS", "target_rb"}.issubset(parameters)
        _record(
            "srs_bandwidth_selector",
            passed,
            f"signature={inspect.signature(auto_select_c_srs)}; requires B_SRS/target_rb",
        )
    except Exception as exc:  # noqa: BLE001
        _record("srs_bandwidth_selector", False, f"{type(exc).__name__}: {exc}")

    try:
        from msg_embedding.phy_sim.effective_array import EffectiveArray  # noqa: PLC0415

        parameters = inspect.signature(EffectiveArray).parameters
        passed = {"port_order", "vertical_index_order"}.issubset(parameters)
        _record(
            "array_port_order",
            passed,
            f"signature={inspect.signature(EffectiveArray)}; requires canonical port-order controls",
        )
    except Exception as exc:  # noqa: BLE001
        _record("array_port_order", False, f"{type(exc).__name__}: {exc}")

    try:
        from msg_embedding.data.contract import ChannelSample  # noqa: PLC0415

        fields = set(getattr(ChannelSample, "model_fields", {}))
        if not fields:
            fields = set(getattr(ChannelSample, "__fields__", {}))
        required = {"h_ul_true", "h_ul_est", "h_dl_true", "h_dl_est"}
        _record(
            "paired_channel_roles",
            required.issubset(fields),
            f"required={sorted(required)}; present={sorted(required & fields)}",
        )
    except Exception as exc:  # noqa: BLE001
        _record("paired_channel_roles", False, f"{type(exc).__name__}: {exc}")

    try:
        from msg_embedding.data.sources import SOURCE_REGISTRY  # noqa: PLC0415

        required_sources = {"internal_sim", "sionna_rt", "quadriga_real"}
        present = set(SOURCE_REGISTRY)
        _record(
            "source_registry",
            required_sources.issubset(present),
            f"required={sorted(required_sources)}; present={sorted(present)}",
        )
    except Exception as exc:  # noqa: BLE001
        _record("source_registry", False, f"{type(exc).__name__}: {exc}")

    return SourceContractReport(
        compatible=not blockers,
        checks=checks,
        blockers=tuple(blockers),
    )


def _probe_module(mod: str) -> bool:
    """判断某个模块装没装。**只按顶层包名判断，不执行任何模块。**

    ⚠ 2026-08-29 的坑：`find_spec("sionna.rt")` 为了拿父包的 ``__path__``
    会**真的 import sionna**，而 sionna 连带拉起 mitsuba / drjit / matplotlib /
    IPython / pythreejs 一整套可视化栈（实测 +455 MB）。MCP 服务端是每个 CLI
    会话一个进程，探一下"装没装"就付这个价钱完全不划算；更糟的是这次 import
    发生在事件循环里（sr_capabilities 工具内），首次载入重 C 扩展有死锁风险。

    顶层名字的 find_spec 不触发任何 import，所以这里把带点的名字截到顶层。
    代价是分辨不出"装了 sionna 但缺 sionna.rt 子模块"这种破损安装 —— 真用的时候
    照样会报错，而且报得更具体，不值得为它每次付 455 MB。
    """
    import importlib.util

    top = mod.split(".")[0]
    try:
        return importlib.util.find_spec(top) is not None
    except (ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def probe_capabilities() -> tuple[Capability, ...]:
    """启动时探测各引擎依赖，缺什么如实报出来，不等到调用时才崩。"""
    root = channelhub_root()
    if not _looks_like_channelhub(root):
        # **三个引擎都要报，不能只报 internal_sim。** 早先这里只返回一个条目，
        # 于是没装 ChannelHub 时 sionna_rt / quadriga_real 从清单里整个消失——
        # 调用方写 engines["sionna_rt"] 会 KeyError，看起来像工具坏了。
        # 清单的长度不该随环境变化，变的只是 available 与 missing。
        why = (
            f"未找到 ChannelHub 源码（试过 {root}）。"
            "请先 clone https://github.com/wangxz0803-lab/ChannelHub_main ，"
            f"放在本项目同级或上级目录，或设环境变量 {_ENV_KEY} 指向它。"
            "判据是该目录下存在 src/msg_embedding/data/contract.py。"
        )
        rt_missing = [m for m in ("sionna.rt", "mitsuba", "drjit") if not _probe_module(m)]
        return (
            Capability("internal_sim", False, why, ["ChannelHub"]),
            Capability(
                "sionna_rt", False,
                why if not rt_missing else why + f" 另外还缺 {', '.join(rt_missing)}。",
                ["ChannelHub", *rt_missing],
            ),
            Capability(
                "quadriga_real", False,
                "需要 MATLAB 或 Octave 运行时，本方案未纳入；且同样依赖 ChannelHub。",
                ["ChannelHub", "octave"],
            ),
        )

    try:
        _ensure_path()
    except Exception as exc:  # noqa: BLE001
        why = (
            "源码存在但初始化/标准表合同失败；拒绝继续探测："
            f"{type(exc).__name__}: {exc}"
        )
        return (
            Capability("internal_sim", False, why, ["source-initialization"]),
            Capability("sionna_rt", False, why, ["source-initialization"]),
            Capability(
                "quadriga_real", False,
                why + "；另需MATLAB或Octave运行时",
                ["source-initialization", "octave"],
            ),
        )
    caps: list[Capability] = []
    contract = probe_source_contract()
    contract_missing = [f"source-contract:{name}" for name in contract.blockers]

    # internal_sim：纯 numpy 统计仿真，Phase 0 主力
    missing = [m for m in ("numpy", "scipy", "pydantic", "structlog") if not _probe_module(m)]
    missing.extend(contract_missing)
    caps.append(
        Capability(
            "internal_sim",
            not missing,
            (
                "3GPP 38.901 统计信道仿真（CDL/TDL），源契约已握手"
                if not missing else
                "依赖或SuperRAN源契约不满足；拒绝把可import误报成可生成"
            ),
            missing,
        )
    )

    # sionna_rt：射线追踪，Phase 2 才需要
    rt_missing = [m for m in ("sionna.rt", "mitsuba", "drjit") if not _probe_module(m)]
    rt_missing.extend(contract_missing)
    caps.append(
        Capability(
            "sionna_rt",
            not rt_missing,
            (
                "射线追踪（真实城市地图），源契约已握手"
                if not rt_missing else
                "缺射线追踪依赖或SuperRAN源契约不满足"
            ),
            rt_missing,
        )
    )

    # quadriga_real：需要 MATLAB/Octave
    caps.append(
        Capability(
            "quadriga_real",
            False,
            "需要 MATLAB 或 Octave 运行时，本方案未纳入",
            ["octave"],
        )
    )
    return tuple(caps)


def warmup() -> dict[str, Any]:
    """启动时预热：把 ChannelHub 及其重依赖 import 好。

    有两个理由：一是依赖缺失应当在启动时暴露，而不是等第一次调用才崩；
    二是 numpy/scipy 这类库首次 import 有明显开销，放在请求处理路径上
    会让第一次调用异常缓慢。
    """
    import time

    t0 = time.perf_counter()
    _ensure_path()
    info: dict[str, Any] = {}
    try:
        contract = probe_source_contract()
        info["source_contract"] = contract.as_dict()
        if not contract.compatible:
            raise RuntimeError(
                "source contract mismatch: " + ", ".join(contract.blockers)
            )
        import msg_embedding.channel_est.interpolate  # noqa: F401,PLC0415
        import msg_embedding.data.sources._interference_estimation  # noqa: F401,PLC0415
        import msg_embedding.data.sources.internal_sim  # noqa: F401,PLC0415
        import msg_embedding.phy_sim.precoding  # noqa: F401,PLC0415
        import numpy  # noqa: PLC0415
        import scipy.interpolate  # noqa: F401,PLC0415  channel_est/interpolate.py
        import scipy.io  # noqa: F401,PLC0415            sources/quadriga_real.py

        # scipy 的这几个子模块 ChannelHub 是**惰性 import** 的（用到才导）。
        # 必须在主线程提前导完：MCP 把工具调用派到工作线程执行，而在工作线程里
        # 首次加载 scipy 的 C 扩展会撞上 import 死锁——表现为请求永久无响应、
        # 无异常、无日志。这一条是实测 faulthandler 抓栈定位到的，别删。
        import scipy.linalg  # noqa: PLC0415
        import scipy.spatial  # noqa: F401,PLC0415       eval/channel_charting.py
        import scipy.special  # noqa: F401,PLC0415       channel_models/tdl.py
        import scipy.stats  # noqa: F401,PLC0415
        from msg_embedding.channel_models.cdl import get_cdl_profile  # noqa: F401,PLC0415
        from msg_embedding.channel_models.tdl import get_tdl_profile  # noqa: F401,PLC0415
        from msg_embedding.data.contract import ChannelSample  # noqa: F401,PLC0415
        from msg_embedding.data.sources import SOURCE_REGISTRY  # noqa: PLC0415

        info["sources"] = sorted(SOURCE_REGISTRY)

        # 关键：在主线程真跑一次小计算，把 BLAS / FFT 的线程池初始化掉。
        # 否则首次数值计算若发生在工作线程（MCP 把工具调用派到线程池），
        # OpenBLAS 的线程池初始化可能死锁——表现为请求无响应且无异常。
        rng = numpy.random.default_rng(0)
        a = (rng.standard_normal((16, 8)) + 1j * rng.standard_normal((16, 8))).astype(
            numpy.complex128
        )
        scipy.linalg.svd(a, full_matrices=False)
        numpy.linalg.eigh(a @ a.conj().T)
        numpy.fft.ifft(a, axis=0)

        # 标准 CDL 表已由 _ensure_path → ensure_spec_tables 灌好，这里只是报状态。
        info["cdl_spec_tables"] = ensure_spec_tables()
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001
        info["ok"] = False
        info["error"] = f"{type(exc).__name__}: {exc}"
    info["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return info


def require_source(name: str) -> Any:
    """按名字取出 DataSource 类；不可用时抛出可读的错误。"""
    caps = {c.name: c for c in probe_capabilities()}
    cap = caps.get(name)
    if cap is not None and not cap.available:
        raise RuntimeError(
            f"仿真引擎 {name!r} 在本机不可用：{cap.detail}"
            + (f"（缺 {', '.join(cap.missing)}）" if cap.missing else "")
        )

    _ensure_path()
    import msg_embedding.data.sources.internal_sim  # noqa: F401,PLC0415
    from msg_embedding.data.sources import SOURCE_REGISTRY  # noqa: PLC0415

    cls = SOURCE_REGISTRY.get(name)
    if cls is None:
        raise RuntimeError(
            f"未注册的引擎 {name!r}；已注册：{sorted(SOURCE_REGISTRY)}"
        )
    return cls


def cdl_profile(name: str) -> Any:
    """取 CDL/TDL 剖面对象（含每簇角度、时延、功率）。"""
    _ensure_path()
    key = name.upper().replace("_", "-")
    if key.startswith("CDL-"):
        from msg_embedding.channel_models.cdl import get_cdl_profile  # noqa: PLC0415

        return get_cdl_profile(key)
    from msg_embedding.channel_models.tdl import get_tdl_profile  # noqa: PLC0415

    return get_tdl_profile(key)


def list_channel_models() -> dict[str, list[str]]:
    """可用的信道模型清单。CDL 有每径角度，TDL 没有——这个差别很要紧。"""
    _ensure_path()
    from msg_embedding.channel_models.cdl import list_cdl_profiles  # noqa: PLC0415
    from msg_embedding.channel_models.tdl import list_tdl_profiles  # noqa: PLC0415

    return {"cdl": list(list_cdl_profiles()), "tdl": list(list_tdl_profiles())}


def iter_samples(source_name: str, cfg: dict[str, Any]) -> Iterator[Any]:
    """实例化引擎并逐个产出 ChannelSample。"""
    cls = require_source(source_name)
    src = cls(dict(cfg))
    for sample in src.iter_samples():
        if source_name == "internal_sim":
            _validate_internal_site_state_contract(sample, cfg)
        yield sample


def _validate_internal_site_state_contract(sample: Any, cfg: dict[str, Any]) -> None:
    """拒绝旧版 ChannelHub 的跨站复制传播状态。

    多小区结果若没有明确的“同站共享、跨站独立”元数据，不能靠数值碰巧不同来
    猜测实现正确。与其静默生成一批物理口径错误的数据，不如在窄腰入口硬失败。
    """
    n_sites = int(cfg.get("num_sites", 1) or 1)
    sectors = int(cfg.get("sectors_per_site", 1) or 1)
    if n_sites <= 1 and sectors <= 1:
        return

    meta = getattr(sample, "meta", {}) or {}
    policy = meta.get("site_state_policy")
    expected = "same_site_shared_cross_site_independent_v1"
    if policy != expected:
        raise RuntimeError(
            "ChannelHub internal_sim 不满足多站传播状态契约："
            f"site_state_policy={policy!r}，要求 {expected!r}。"
            "必须使用支持‘同站扇区共享 LOS/DS/SF、不同物理站独立’的版本，"
            "禁止把一个 UE 级状态复制给全网小区。"
        )

    group_ids = list(meta.get("physical_site_group_ids") or [])
    is_los_all = list(meta.get("is_los_all") or [])
    ds_all = list(meta.get("sample_tau_rms_all_ns") or [])
    sf_all = list(meta.get("shadow_fading_all_db") or [])
    if not group_ids or not (
        len(group_ids) == len(is_los_all) == len(ds_all) == len(sf_all)
    ):
        raise RuntimeError(
            "ChannelHub 的站点传播元数据不完整：需要等长的 "
            "physical_site_group_ids/is_los_all/sample_tau_rms_all_ns/"
            "shadow_fading_all_db。"
        )

    shared: dict[int, tuple[Any, float, float]] = {}
    for group, los, ds, sf in zip(
        group_ids, is_los_all, ds_all, sf_all, strict=True
    ):
        state = (bool(los), float(ds), float(sf))
        group = int(group)
        if group in shared and shared[group] != state:
            raise RuntimeError(
                f"同一物理站 group={group} 的扇区没有共享 LOS/DS/SF："
                f"{shared[group]!r} != {state!r}"
            )
        shared[group] = state


def describe(source_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """不生成数据，只问引擎"这个配置会产出什么"。用于预览，代价极低。"""
    cls = require_source(source_name)
    return cls(dict(cfg)).describe()


# ---------------------------------------------------------------------------
# ChannelSample 取值助手
# ---------------------------------------------------------------------------
# 坑：link_pairing == "single" 时（默认），h_dl_* / h_ul_* 全是 None，
# 数据实际在 h_serving_*。这类知识固化在这里，用户侧不必知道。

# 这是 SuperRAN 自己的数据合同，不跟随任何外部源的 ``w_dl``
# helper 演进。外部源只交付信道；预编码权由 SuperRAN 从归一化后
# 的 h_est 重算。如果以后更改轴序或数值约定，必须升这个版本。
SUPERRAN_RECIPROCITY_CONTRACT = "superran-tdd-bs-ue-canonical-v1"
SUPERRAN_CANONICAL_CHANNEL_AXES = ("time", "rb", "bs_port", "ue_port")


def ul_estimate_to_dl_precoding_csi(
    h_ul_est: Any,
    *,
    expected_shape: tuple[int, ...] | None = None,
) -> Any:
    """Map canonical UL SRS estimate to SuperRAN's DL precoding convention.

    Physical reciprocity uses ``H_UL = H_DL^H``.  SuperRAN stores both links
    on ``[time, rb, bs_port, ue_port]`` axes, so transposing the physical UL
    matrix back to those canonical axes leaves ``conj(H_DL)``.  Consequently
    the DL-precoding view is exactly ``conj(h_ul_est)``.

    This pure mapping is owned and versioned by SuperRAN.  Source-provided
    precoders are deliberately irrelevant to it.
    """
    import numpy as np  # noqa: PLC0415

    arr = np.asarray(h_ul_est)
    if arr.ndim != 4:
        raise RuntimeError(
            "SuperRAN TDD 互易合同要求 h_ul_est 为 "
            "[time,rb,bs_port,ue_port] 四维张量；"
            f"实得 {arr.shape}"
        )
    if expected_shape is not None and arr.shape != tuple(expected_shape):
        raise RuntimeError(
            "SuperRAN TDD 互易合同要求 UL 估计与 DL 真值使用"
            f"同一 canonical 轴；实得 {arr.shape} vs {tuple(expected_shape)}"
        )
    if not np.isfinite(arr).all():
        raise RuntimeError("h_ul_est 含 NaN 或 Inf，无法构造下行预编码 CSI")
    return np.conj(arr)


def serving_channel(sample: Any, *, estimated: bool = False) -> Any:
    """取服务小区信道 [T, RB, BS_ant, UE_ant]，自动处理 paired/single 差异。"""
    if estimated:
        for attr in ("h_serving_est", "h_dl_est", "h_ul_est"):
            v = getattr(sample, attr, None)
            if v is not None:
                return v
    else:
        for attr in ("h_serving_true", "h_dl_true", "h_ul_true"):
            v = getattr(sample, attr, None)
            if v is not None:
                return v
    return None


def downlink_and_precoding_channels(sample: Any) -> tuple[Any, Any, Any, str]:
    """Return ``(h_dl_true, h_precoding_est, h_dl_est, source)``.

    The distinction is essential for TDD system simulation:

    * ``h_dl_true`` is the channel on which PDSCH is physically evaluated;
    * ``h_precoding_est`` is what the gNB knew when it designed the weight;
    * ``h_dl_est`` is the UE-side CSI-RS estimate, retained for diagnostics but
      **must not silently replace SRS** in the precoding path.

    A paired source may store all three explicitly.  SuperRAN normalizes them
    to its own versioned contract here; it never delegates the complex mapping
    or the transmit weight to a source-provided ``w_dl`` helper.  Legacy/single
    samples only carry ``h_serving_*``; those remain supported and are labelled
    by direction instead of being misreported as SRS.
    """
    h_dl_true = getattr(sample, "h_dl_true", None)
    h_ul_est = getattr(sample, "h_ul_est", None)
    h_dl_est = getattr(sample, "h_dl_est", None)
    if h_dl_true is not None:
        if h_ul_est is None:
            raise RuntimeError(
                "paired/BOTH 样本有 h_dl_true 但没有 h_ul_est；"
                "无法用 SRS 估计设计下行预编码"
            )
        # 不信任数据源的 w_dl 或 helper；轴序、共轭和版本均由
        # SuperRAN 的纯函数合同决定。
        h_precoding_est = ul_estimate_to_dl_precoding_csi(
            h_ul_est,
            expected_shape=tuple(getattr(h_dl_true, "shape", ())),
        )
        return h_dl_true, h_precoding_est, h_dl_est, "ul_srs_estimate"

    h_true = getattr(sample, "h_serving_true", None)
    h_est = getattr(sample, "h_serving_est", None)
    link = str(getattr(sample, "link", "DL") or "DL").upper()
    source = "ul_srs_estimate" if link == "UL" else "dl_csirs_estimate"
    return h_true, h_est, h_dl_est, source
