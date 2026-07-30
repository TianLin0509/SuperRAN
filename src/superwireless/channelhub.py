"""ChannelHub 适配层。

只把 ChannelHub 当普通 Python 库用：注入 src 路径、驱动 DataSource、
把产出的 ChannelSample 转成 superwireless 自己的数据结构。

不碰 platform/（后端、前端、任务队列），不走 data/bridge.py（那条路的输出
是为 MAE token 裁剪过的，见设计文档 v1 第三节）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

# ChannelHub 源码位置。按以下顺序查找，都找不到时报错并给出指引。
#   1. 环境变量 SUPERWIRELESS_CHANNELHUB
#   2. 本项目的兄弟目录 / 上级目录下的常见位置
#   3. 若干本机习惯路径
_ENV_KEY = "SUPERWIRELESS_CHANNELHUB"


def _candidate_roots() -> list[Path]:
    here = Path(__file__).resolve()
    project = here.parents[2]  # src/superwireless/channelhub.py -> 项目根
    names = ("ChannelHub_main", "ChannelHub", "channelhub")
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


_spec_tables_state: dict[str, Any] = {}


def _ensure_path() -> None:
    """把 ChannelHub 的 src 加进 sys.path（幂等），并确保标准 CDL 表已就位。"""
    src = channelhub_root() / "src"
    s = str(src)
    if s not in sys.path:
        sys.path.insert(0, s)
    ensure_spec_tables()


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


def _probe_module(mod: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(mod) is not None
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

    _ensure_path()
    caps: list[Capability] = []

    # internal_sim：纯 numpy 统计仿真，Phase 0 主力
    missing = [m for m in ("numpy", "scipy", "pydantic", "structlog") if not _probe_module(m)]
    caps.append(
        Capability(
            "internal_sim",
            not missing,
            "3GPP 38.901 统计信道仿真（CDL/TDL），纯 numpy，秒级" if not missing else "缺依赖",
            missing,
        )
    )

    # sionna_rt：射线追踪，Phase 2 才需要
    rt_missing = [m for m in ("sionna.rt", "mitsuba", "drjit") if not _probe_module(m)]
    caps.append(
        Capability(
            "sionna_rt",
            not rt_missing,
            "射线追踪（真实城市地图）" if not rt_missing else "Phase 2 才需要；补装 sionna-rt 可启用",
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
        import numpy  # noqa: PLC0415

        # scipy 的这几个子模块 ChannelHub 是**惰性 import** 的（用到才导）。
        # 必须在主线程提前导完：MCP 把工具调用派到工作线程执行，而在工作线程里
        # 首次加载 scipy 的 C 扩展会撞上 import 死锁——表现为请求永久无响应、
        # 无异常、无日志。这一条是实测 faulthandler 抓栈定位到的，别删。
        import scipy.linalg  # noqa: PLC0415
        import scipy.interpolate  # noqa: F401,PLC0415  channel_est/interpolate.py
        import scipy.special  # noqa: F401,PLC0415       channel_models/tdl.py
        import scipy.io  # noqa: F401,PLC0415            sources/quadriga_real.py
        import scipy.spatial  # noqa: F401,PLC0415       eval/channel_charting.py
        import scipy.stats  # noqa: F401,PLC0415

        from msg_embedding.data.contract import ChannelSample  # noqa: F401,PLC0415
        from msg_embedding.data.sources import SOURCE_REGISTRY  # noqa: PLC0415
        import msg_embedding.data.sources.internal_sim  # noqa: F401,PLC0415
        import msg_embedding.data.sources._interference_estimation  # noqa: F401,PLC0415
        import msg_embedding.channel_est.interpolate  # noqa: F401,PLC0415
        import msg_embedding.phy_sim.precoding  # noqa: F401,PLC0415
        from msg_embedding.channel_models.cdl import get_cdl_profile  # noqa: F401,PLC0415
        from msg_embedding.channel_models.tdl import get_tdl_profile  # noqa: F401,PLC0415

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
    from msg_embedding.data.sources import SOURCE_REGISTRY  # noqa: PLC0415
    import msg_embedding.data.sources.internal_sim  # noqa: F401,PLC0415

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
    yield from src.iter_samples()


def describe(source_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """不生成数据，只问引擎"这个配置会产出什么"。用于预览，代价极低。"""
    cls = require_source(source_name)
    return cls(dict(cfg)).describe()


# ---------------------------------------------------------------------------
# ChannelSample 取值助手
# ---------------------------------------------------------------------------
# 坑：link_pairing == "single" 时（默认），h_dl_* / h_ul_* 全是 None，
# 数据实际在 h_serving_*。这类知识固化在这里，用户侧不必知道。


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
