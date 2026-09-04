"""superran MCP 服务端。

工具设计的两条铁律：
1. **不传数据**。信道矩阵落盘，这里只回句柄、摘要和取货代码。
2. **主动交给用户决策**。sr_plan 返回的是提案 + 该问什么 + 为什么值得问，
   不是替用户做决定，也不是把几十个参数一股脑甩出来。
"""
from __future__ import annotations

import functools
import inspect
import math
import os
import sys
from dataclasses import replace
from typing import Any

import anyio

from ._lazy import lazy_module

# 这里的 ``np`` 是占位模块：只是让 ``import superran.server`` 本身保持便宜
# （49 MB 而不是 1323 MB），方便测试、工具链和 --help 之类的场景。
#
# ⚠ 但**服务端运行时 numpy 仍然是预载的**，见 main() 里的 _preload_native_stack()：
# 事件循环起来之后再第一次载入 numpy 的 C 扩展会死锁（2026-08-29 实测，栈见那个
# 函数的注释）。所以这个占位模块在服务端进程里其实一启动就被解析掉了，
# 它省的是"不跑服务端时"的开销。真正省服务端内存的是 BLAS 线程上限。
#
# 用代理对象而不是 PEP 562 的模块 __getattr__，是因为本文件有 40 多个工具、
# 上千处 ``np.xxx`` / ``ch.xxx`` 调用，走的是**模块内的全局名字查找**，
# 那条路径不经过 PEP 562。用占位模块则一个字都不用改函数体。
np = lazy_module("numpy")

# mcp 1.x 与 2.x 的服务端类改了名字和位置：
#   1.x  mcp.server.fastmcp.FastMCP
#   2.x  mcp.server.mcpserver.MCPServer   （fastmcp 子模块整个删掉了）
# 两者的 .tool() 装饰器与 .run(transport=...) 签名一致，所以只需换个导入。
# 不做这个兼容的话，今天新装的用户 `pip install mcp` 拿到 2.x，
# 服务端会在 import 阶段就 ModuleNotFoundError——而且报的是 mcp 的错，
# 看起来像用户环境问题，很难联想到是版本不兼容。
try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _ServerClass

    MCP_MAJOR = 2
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _ServerClass

    MCP_MAJOR = 1

# 同上：这几个子模块几乎都在顶层 import numpy，eager import 会把上面省下的
# 又全部拉回来。名字和用法与之前逐字一致，只是推迟到第一次访问属性时才真正加载。
carrier_grid = lazy_module(".carrier", __package__)
ch = lazy_module(".channelhub", __package__)
dec = lazy_module(".decisions", __package__)
dlv = lazy_module(".deliver", __package__)
gen = lazy_module(".generate", __package__)
pl = lazy_module(".plan", __package__)
provenance = lazy_module(".provenance", __package__)

mcp = _ServerClass("superran")

_DEBUG = bool(os.environ.get("SUPERRAN_DEBUG"))

# 注：这里**刻意没有**「跳过 warmup」的开关。warmup 是正确性依赖不是性能优化
# （见 _resolve_lazy_modules），给一个能关掉它的旋钮只会制造一个必然踩中的坑。
#
# BLAS 线程池上限。设成 auto / off / 0 就完全不干预，交给 OpenBLAS 按核数决定。
#
# 默认取 1，不是保守，是实测下来最快的一档 —— 见 _apply_blas_thread_cap 的数据。
# SuperRAN 的矩阵都很小（逐 RB 的 4×64 / 4×256 SVD、64×64 eigh），多线程 BLAS
# 只剩线程调度开销；真正的并行度来自 generate 的多进程分块，而那些 worker
# 本来就各自把线程数压成 1（见 CLAUDE.md「多进程必须先压 BLAS 线程数」）。
_BLAS_THREADS = os.environ.get("SUPERRAN_BLAS_THREADS", "1").strip()

_BLAS_ENV_VARS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def _apply_blas_thread_cap() -> str | None:
    """在 numpy 第一次被 import **之前**限制 BLAS 线程池大小。

    2026-08-29 在 20 逻辑核的机器上实测，服务端空转的提交内存::

        1 线程 172 MB    2 线程 236 MB    4 线程 365 MB    不限(auto) 2718 MB

    差额**全部**落在 numpy 与 scipy.special 的 import 上（逐段量过，其余每一步
    逐字节相同），也就是 OpenBLAS 按核数预留的线程 arena —— 跟 superran 的代码量
    毫无关系，而且实占从来没涨（提交了却没碰过）。MCP 服务端是每个 CLI 会话一个
    进程，这笔钱要乘以会话数。

    **默认取 1 不是保守，是实测最快的一档。** 按 SuperRAN 真实矩阵尺寸测：

        场景                          1线程    2线程    4线程   auto(20)
        273×(4×64) SVD 逐 RB 预编码   23.3ms  16.7ms  20.0ms   16.2ms
        273×(4×256) SVD 256 端口面板  47.4    37.1    50.2     38.4
        64×(64×64) eigh 协方差         102.5   110.4   122.1    117.3
        (2048×64) ifft 时延域          314.5   332.6   382.4    325.9
        (2000×2000) GEMM（人造对照）    2897    1579     894      481

    只有那个人造的大 GEMM 吃多线程，而 SuperRAN 根本不做那种运算。真实负载复核
    （脚本模式跑 tests/test_gates.py，全部通过）：1 线程 87.5s / 2 线程 96.2s /
    4 线程 88.1s —— 差异都在噪声内。

    真正的并行度来自 generate 的多进程分块，而那些 worker 本来就各自把线程数压成 1
    （见 CLAUDE.md「多进程必须先压 BLAS 线程数」）。

    这是**性能取舍不是精度取舍**：数值结果逐位不变。确实要放开就设
    SUPERRAN_BLAS_THREADS=auto。用户自己显式设过这些环境变量的话一律尊重，
    这里只补没设的。
    """
    if _BLAS_THREADS.lower() in {"", "0", "auto", "off", "none"}:
        return None
    for var in _BLAS_ENV_VARS:
        os.environ.setdefault(var, _BLAS_THREADS)
    return _BLAS_THREADS


def _resolve_lazy_modules() -> None:
    """在主线程把本模块的占位模块全部解析掉（必须早于 mcp.run）。

    ⚠⚠ 别把这一步挪到"第一次调用工具时"，也别以为限制线程数能绕开 ——
    2026-08-29 完整踩过一遍：

    把 numpy 也做成懒加载之后，服务端能正常 initialize、能列出全部 35 个工具，
    但**第一次调用任何工具就永久挂死**，无异常无日志。抓到的主线程栈是::

        mcp_server.py <module> → server.main → mcp.run → ... → sr_mcs_info
          → superran/linkadapt.py <module>
            → numpy/_core/multiarray.py <module>
              → importlib._bootstrap_external.create_module   ← 卡死在这里

    改成只在这里预载 numpy/scipy 之后，卡点前移到了下一个"首次载入的 scipy 子模块"：

        sr_capabilities → channelhub.probe_source_contract
          → msg_embedding/channel_est/interpolate.py <module>（import scipy.interpolate）
            ← 换个地方卡死

    边界在哪（2026-08-29 补测，别把结论用过头）：危险的是 **numpy / scipy 及其
    子模块**的首次加载。同一个进程里，事件循环 + anyio 线程池都跑起来之后再
    ``import torch`` / ``import sionna.rt``，实测**不会**死锁（分别 1.4s / 1.0s
    正常导完）—— 前提是 numpy/scipy 已经在主线程预热过。
    这与 channelhub.warmup() 里那段标了"别删"的注释完全一致：它预热的就是
    numpy 和那一串 scipy 子模块。所以 main() 里它必须无条件跑；
    而 sionna/torch 这类只能由未来 direct adapter 在事件循环启动后按需加载；
    当前 first-party source 不导入它们。

    省内存要靠 _apply_blas_thread_cap()（那才是 1.3 GB 的真正来源），
    不能靠推迟 import。文件头部的占位模块只用来让"不跑服务端"的场景
    （测试、工具链）保持便宜，服务端进程里它们在这里就被解析掉了。
    """
    for placeholder in (carrier_grid, ch, dec, dlv, gen, pl, provenance):
        placeholder._load()  # noqa: SLF001  占位模块自己的接口


# ---------------------------------------------------------------------------
# 说明书回传的"送达感知"
# ---------------------------------------------------------------------------
# **MCP 是纯拉取的：服务端没法往对话里推消息。** 用户在说明书页面上点了
# 「应用到仿真」，如果 agent 此刻没有正好阻塞在 sr_await_config 上，
# 那份改动就只是静静躺在收件箱里——CLI 上不会有任何动静，用户会以为没生效。
#
# 唯一能用的通道是**工具返回值**。所以把"有未处理的回传"挂到每一个工具的
# 返回上：agent 下一次做任何事，都会在结果里看到它，然后立刻告诉用户。
# 这不是推送，但把"用户感知不到"的窗口从"直到 agent 恰好来等"
# 压到了"直到 agent 下一次调用任何工具"。
def _with_pending(result: Any) -> Any:
    """给返回值挂上未处理的配置回传通知。非 dict 的返回原样放行。"""
    if not isinstance(result, dict):
        return result
    try:
        from . import bridge as _br  # noqa: PLC0415

        n = _br.pending_count()
        if n:
            result = dict(result)
            result["pending_config_changes"] = {
                "count": n,
                "notice": f"用户在说明书页面上提交了 {n} 项配置改动，还没处理。",
                "action": "**先停下手头的事**，调 sr_await_config(timeout_s=1) 取回来，"
                          "向用户复述改了什么，再问是否照做。他点了按钮却没等到回应，"
                          "多半正以为没生效。",
            }
    except Exception as exc:  # noqa: BLE001
        _dbg(f"pending 检查失败（不影响工具本身）：{exc}")
    return result


def tool(*d_args: Any, **d_kwargs: Any):
    """``@tool()`` 的包装，统一挂上回传通知。

    **必须分同步/异步两条路。** 有些工具（``sr_generate``）是 ``async def``，
    用同步包装器去调它只会拿到一个从没被 await 的 coroutine 对象，
    然后 ``_with_pending`` 看它不是 dict 就原样放行——整个返回值静默变成垃圾。
    实测症状是调用方拿到的结果里没有 ``summary`` 键，报 KeyError，
    完全看不出跟装饰器有关。
    """
    inner = mcp.tool(*d_args, **d_kwargs)

    def deco(fn):
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                return _with_pending(await fn(*args, **kwargs))

            return inner(awrapper)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return _with_pending(fn(*args, **kwargs))

        return inner(wrapper)

    return deco


def _dbg(msg: str) -> None:
    """调试打点。只能写 stderr —— stdio 传输下 stdout 是 JSON-RPC 通道。"""
    if _DEBUG:
        print(f"[sw] {msg}", file=sys.stderr, flush=True)


def _jsonable(obj: Any) -> Any:
    """把 inf/nan 之类 JSON 装不下的值清理掉。"""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float):
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        if math.isnan(obj):
            return None
    return obj


def _carrier_grid(config: dict[str, Any], *, num_rb: int) -> dict[str, Any]:
    """校验数据集是否符合 SuperRAN 固定 TDD 系统载波。

    ``num_rb`` 来自信道张量本身（``h.shape[-3]``），不是配置里的声明值——
    用它抓住“配置写 100 MHz，张量却不是 272 RB”这类静默错配。
当前产品口径固定为 100 MHz @ 30 kHz、272 RB = 17 RBG × 16 RB；
链路级可以生成其他带宽，但不能送入这个 TDD 系统入口。
    """
    return carrier_grid.CarrierGrid.company_tdd(config, num_rb=num_rb).as_dict()


def _system_adaptation_contract(
    *,
    target_bler: float,
    olla_step_up_db: float,
    olla_step_down_db: float | None,
    resolved_su_down: float,
    mu_olla_step_up_db: float,
    mu_olla_step_down_db: float | None,
    resolved_mu_down: float,
) -> dict[str, Any]:
    """Build auditable OLLA/MCS metadata for ``sr_system_sim`` only."""
    from . import linkadapt as la  # noqa: PLC0415
    from . import system as sysm  # noqa: PLC0415

    return {
        "olla_configuration": {
            "target_bler": float(target_bler),
            "domain": "continuous_mcs_index",
            "application_order": "SINR-to-MCS, then OLLA, then floor and clip",
            "legacy_parameter_names": "*_db names retained for API compatibility",
            "su": {
                "step_up_mcs": float(olla_step_up_db),
                "step_down_mcs": float(resolved_su_down),
                "step_up_db": float(olla_step_up_db),
                "step_down_db": float(resolved_su_down),
                "step_down_source": (
                    "auto_from_target_bler"
                    if olla_step_down_db is None else "explicit_user_override"
                ),
            },
            "mu": {
                "step_up_mcs": float(mu_olla_step_up_db),
                "step_down_mcs": float(resolved_mu_down),
                "step_up_db": float(mu_olla_step_up_db),
                "step_down_db": float(resolved_mu_down),
                "step_down_source": (
                    "auto_from_target_bler"
                    if mu_olla_step_down_db is None else "explicit_user_override"
                ),
            },
            "formula": "step_down = step_up * (1 - target_bler) / target_bler",
        },

        "mcs_profile": {
            # 与 build_link_tables 的默认值同源，不在这里抄第二份——
            # 抄了就会漂，漂了这段元数据就在静默说谎。
            "table": int(inspect.signature(
                sysm.build_link_tables).parameters["table"].default),
            "profile": "preset_20b_256qam",
            "cqi_profile": la.INTERNAL_CQI_PROFILE_ID,
            "cqi_to_mcs": list(la.INTERNAL_CQI_TO_MCS),
            "cqi_numbering": {
                "legacy_internal_row": "0..14; row 0 maps to MCS0",
                "reported_4bit": "0..15; codepoint 0 is out-of-range; 1..15 map to rows 0..14",
            },
            "reported_cqi_zero_semantics": "out_of_range",
            "top_mapping_limit": (
                "internal row14 / reported CQI15 requests MCS28; current MCS0..27 "
                "curve profile clips to 27"
            ),
            "scope": "experience_v2 fixed preset table",
            "bler_abstraction": (
                "one user grant per TTI is one independent single-codeword TB; "
                "codeword SINR is averaged in dB across RBGs and rank streams; "
                "preset curves are universal across TBS/RE/rank/scenario"
            ),
            "extensibility": (
                "table/profile remains an explicit internal contract; unsupported "
                "tables hard-fail until their BLER/TBS metadata is implemented"
            ),
        },
    }


# ---------------------------------------------------------------------------
# 能力发现
# ---------------------------------------------------------------------------


@tool()
def sr_capabilities() -> dict[str, Any]:
    """查看本机可用的仿真引擎，以及不可用的引擎缺什么。

    引擎不可用时不会报错，而是如实标注——避免用户以为是自己用错了。
    """
    caps = [c.as_dict() for c in ch.probe_capabilities()]
    try:
        models = ch.list_channel_models()
    except Exception as exc:  # noqa: BLE001
        models = {"error": str(exc)}
    from . import hardware as hw

    return {
        "physical_core": "superran-first-party",
        "physical_core_root": str(ch.project_root()),
        "external_source_tree": None,
        # Deprecated display alias retained for clients that parsed it before
        # the first-party-core migration.
        "channelhub_root": str(ch.channelhub_root()),
        "source_contract": ch.probe_source_contract().as_dict(),
        "engines": caps,
        "channel_models": models,
        # 本地默认硬件与载波。**面板是 8x4x2 时自动生效**，不需要调用方写。
        "default_hardware": hw.describe(),
        "note": (
            "CDL 系列含每条径的角度（AoD/AoA/ZoD/ZoA），TDL 系列没有。"
            "凡是依赖角度的课题（波束管理、定位）必须用 CDL。"
        ),
    }


@tool()
def sr_system_scene(name: str | None = None) -> dict[str, Any]:
    """**系统级场景预设：把一整套仿真条件打包成一个名字。**

    不给 ``name`` 就列全部；给了就返回这个场景该怎么跑
    （``generate`` 段喂 ``sr_generate``、``system`` 段喂 ``sr_system_sim``）。

    信道侧有 20+ 个预设，一句 ``company_64t4r_multicell`` 就够了；系统级却要
    手工填 ``duration_s`` / ``traffic_model`` / ``arrival_rate_hz`` /
    ``neighbor_prb_util`` / ``csi_aging`` / ``srs_period_ms`` 八九个参数。
    **后果不只是麻烦——每次跑都在拍参数，不同次之间参数不一致，结果没法横向比。**

    比"一组默认值"多两样东西：

    * ``expect`` **实测锚点**。``measured: false`` 时**不许有数值**——
      preset 里的 label 是设计意图，写着"高干扰"实际只有 2 dB 的事发生过。
    * ``pair_with`` **受控对照**。除了 ``pair_varies`` 列出的那一项，
      两个场景其余参数**逐字相同**，否则差值归因不到任何一项上。
      同时差三四个参数的两个场景不是对照组，只是两个场景。

    **成对场景要用公共随机数跑**（两臂同一批 replication 流），
    实测 95% 区间能窄 3.92 倍。
    """
    from . import sysscenes as ss  # noqa: PLC0415

    if name is None:
        bad = ss.check_pairs()
        return _jsonable({
            "scenes": ss.list_system_presets(),
            "pair_check": "全部成对场景都是受控对比" if not bad else bad,
            "hint": ("挑一个名字再调一次拿到完整参数。"
                     "**成对的那些要两个都跑**——很多系统级结论必须靠 A/B 才立得住，"
                     "而且两臂必须用同一批随机流（CRN）。"),
        })
    try:
        sc = ss.resolve(name)
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}
    exp = sc.get("expect") or {}
    return _jsonable({
        "name": name, "label": sc.get("label"), "summary": sc.get("summary"),
        "answers": sc.get("answers"),
        "generate": sc.generate_kwargs, "system": sc.sim_kwargs,
        "expect": exp, "pair_with": sc.get("pair_with"),
        "pair_varies": sc.get("pair_varies"),
        "hint": (("**这个场景的 expect 还没实测**，别把 summary 里的定性说法"
                  "当成实测值转述给用户。") if not exp.get("measured") else
                 "expect 是实测锚点，跑出来差太多就说明配置没对上，要停下来查。"),
    })


@tool()
def sr_list_presets(group: str | None = None) -> dict[str, Any]:
    """列出场景预设。预设只提供场景骨架，具体参数由 sr_plan 协商决定。

    参数
    ----
    group : 只看某一组（干扰场景 / 测量干扰 / 大站间距 / 移动性 / 传播条件 /
            多小区干扰 / 基线 / 射线追踪 / 室内与专网）。不给则全给。
    """
    items = pl.preset_summaries()
    if group:
        items = [x for x in items if x.get("group") == group]
    return {
        "groups": pl.preset_groups(),
        "presets": items,
        "tasks": [
            {"task": p.task, "label": p.label, "asks": list(p.decision_keys)}
            for p in dec.TASK_PROFILES
        ],
        "note": (
            "干扰类场景的 IoT 必须生成后用 sr_interference_report 复核 —— "
            "preset 里写的是设计意图，不是保证达标的实测值。"
        ),
    }


# ---------------------------------------------------------------------------
# 协商
# ---------------------------------------------------------------------------


@tool()
def sr_plan(
    intent: str,
    preset: str | None = None,
    overrides: dict[str, Any] | None = None,
    max_questions: int = 5,
) -> dict[str, Any]:
    """把用户的仿真意图变成一份配置提案，并给出该和用户确认哪几件事。

    参数
    ----
    intent : 用户的原话，例如"验证一个 CSI 压缩的想法，单小区 64T4R"
    preset : 场景骨架名；不给则按意图自动挑
    overrides : 用户已经明确表态的参数
    max_questions : 最多提几个问题（建议 3~6）

    返回里最重要的是 questions —— 每条都带 why，说明这个选择为什么会改变
    结论。请把 why 转述给用户，不要只列选项值。

    另外 also_configurable 只给参数名不展开，用来告诉用户"还能调这些"。

    用户若无明显偏好，直接用默认值调 sr_generate 即可，不必逐条确认。
    """
    try:
        draft, profile = pl.create_draft(intent, preset=preset, overrides=overrides)
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}
    proposal = pl.build_proposal(draft, profile, max_questions=max(1, min(max_questions, 8)))

    cfg, own = pl.resolved_config(draft)
    proposal["estimated"] = {
        "size_mb": round(gen.estimate_size_mb(cfg, int(draft.params.get("num_samples", 200))), 1),
        "note": "耗时随天线数与样本数增长；先跑 20 个样本验证流程更稳妥",
    }
    proposal["next"] = (
        "和用户对齐后调 sr_generate(draft_id=...)；"
        "用户改主意则调 sr_revise(draft_id=..., overrides={...})"
    )
    return _jsonable(proposal)


@tool()
def sr_revise(
    draft_id: str,
    overrides: dict[str, Any] | None = None,
    design: dict[str, str] | None = None,
) -> dict[str, Any]:
    """差分修正一份提案——用户只说改什么，不必重述整个需求。

    overrides 改仿真参数，例如用户说"信噪比降到 5 dB"：
        sr_revise(draft_id, overrides={"snr_range_dB": [0, 5]})

    design 记录实验设计层的回答，例如用户说"跟 Type II 码本比，看 NMSE"：
        sr_revise(draft_id, design={"baseline": "3GPP Type II 码本",
                                     "metric": "NMSE 与频谱效率损失"})

    design 不影响任何仿真参数，但会写进计划书——三个月后回看时，
    这部分比参数值有用得多。
    """
    try:
        draft, profile, changes = pl.revise_draft(draft_id, overrides, design)
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}
    proposal = pl.build_proposal(draft, profile, max_questions=5)
    proposal["changes"] = changes
    proposal["next"] = "确认无误后调 sr_generate(draft_id=...)"
    return _jsonable(proposal)


@tool()
def sr_list_scenes() -> dict[str, Any]:
    """列出射线追踪可用的场景（真实建筑几何）。

    内置场景（慕尼黑、巴黎凯旋门、佛罗伦萨、旧金山）开箱即用；
    中国城市场景（北京中关村、上海陆家嘴、深圳福田、广州天河、杭州钱江、
    重庆解放碑）首次使用时会自动准备资产，需要几秒。
    """
    from . import scenes as sc

    caps = {c.name: c for c in ch.probe_capabilities()}
    rt = caps.get("sionna_rt")
    return _jsonable(
        {
            "ray_tracing_available": bool(rt and rt.available),
            "unavailable_reason": None if (rt and rt.available) else (rt.detail if rt else None),
            "scenes": [s.as_dict() for s in sc.list_scenes()],
            "hint": (
                "在 sr_plan 的 overrides 里写 {\"scene\": \"shenzhen_futian\"} 即可切换场景；"
                "也可以直接用 rt_munich / rt_shanghai_lujiazui / rt_shenzhen_futian 这几个预设。"
                "射线追踪比统计信道慢一个量级（约 3~5 秒/样本），先小批量验证。"
            ),
        }
    )


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


@tool()
async def sr_generate(
    draft_id: str | None = None,
    intent: str | None = None,
    preset: str | None = None,
    overrides: dict[str, Any] | None = None,
    num_samples: int | None = None,
    prereg_id: str | None = None,
    workers: int | str = "auto",
    collect_ssb: bool | None = None,
) -> dict[str, Any]:
    """生成信道数据集，返回句柄与统计摘要（不返回数据本身）。

    两种用法：
    * 协商过：只传 draft_id
    * 用户说"随便，默认就行"：直接传 intent，跳过协商

    返回里的 auto_decided 列出了替用户做的决定，请转述给用户，
    这样他事后想改也知道改什么。

    prereg_id 是 sr_lock_analysis 返回的预注册句柄。传了它，主指标与基线会
    随数据存档，之后 sr_compare_results 能判断用的指标是不是事先定的那个。
    **只能在生成前绑定**——事后补绑没有意义。

    workers 默认 "auto"：按版本化的粗略工作量决定要不要起多进程；它只是
    调度启发式，不是 ETA。static internal_sim 以同 seed + 全局 sample index
    分块，worker 数变化时逐样本逐位一致。移动轨迹、拒绝采样或未支持全局
    index 的外部源会带原因回退串行。

    collect_ssb=False 关掉每小区 SSB RSRP/SINR 的计算，会减少工作量，但加速比
    依内核与配置而变；旧 30% 标定不再作为 20-ray 版本承诺。代价是 Dataset.ssb
    为空——小区选择、切换、波束管理类课题需要它，别乱关。默认 None = 保留，
    不静默减少数据；实际耗时看返回的 elapsed_s。
    """
    # 仿真是 CPU 密集的，丢到工作线程，别把 MCP 事件循环堵死
    _dbg("sr_generate: 进入，准备切工作线程")
    out = await anyio.to_thread.run_sync(
        functools.partial(
            _generate_sync,
            draft_id=draft_id,
            intent=intent,
            preset=preset,
            overrides=overrides,
            prereg_id=prereg_id,
            workers=workers,
            num_samples=num_samples,
            collect_ssb=collect_ssb,
        )
    )
    _dbg("sr_generate: 工作线程返回，准备序列化响应")
    return out


def _generate_sync(
    *,
    draft_id: str | None,
    intent: str | None,
    preset: str | None,
    overrides: dict[str, Any] | None,
    num_samples: int | None,
    prereg_id: str | None = None,
    workers: int | str = "auto",
    collect_ssb: bool | None = None,
) -> dict[str, Any]:
    if draft_id:
        try:
            draft = pl.load_draft(draft_id)
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}
        profile = next(
            (p for p in dec.TASK_PROFILES if p.task == draft.task), dec.TASK_PROFILES[-1]
        )
        if overrides:
            draft, profile, _ = pl.revise_draft(draft_id, overrides=overrides)
    elif intent:
        draft, profile = pl.create_draft(intent, preset=preset, overrides=overrides)
    else:
        raise ValueError("需要 draft_id 或 intent 其中之一")

    issues = dec.check_guards(profile, draft.params)
    blockers = [i for i in issues if i["severity"] == "block"]
    if blockers:
        return _jsonable(
            {
                "status": "blocked",
                "draft_id": draft.draft_id,
                "issues": issues,
                "message": (
                    "这个参数组合跑得出结果，但结果没有物理意义。"
                    "请按 suggestion 调整后重试，或显式确认要继续。"
                ),
            }
        )

    cfg, own = pl.resolved_config(draft)
    n = int(num_samples or draft.params.get("num_samples", 200))
    cfg.pop("num_samples", None)

    wanted = own.get("measurements_wanted") or ["channel"]
    plan_md = pl.render_plan_markdown(draft, profile, wanted)
    _dbg(f"_generate_sync: 配置就绪 n={n} snr={own.get('snr_range_dB')}，开始生成")

    summary = gen.generate(
        cfg,
        num_samples=n,
        snr_range_dB=own.get("snr_range_dB"),
        plan_markdown=plan_md,
        draft_id=draft.draft_id,
        prereg_id=prereg_id or "",
        workers=workers,
        collect_ssb=collect_ssb,
    )
    _dbg(f"_generate_sync: 生成完成 {summary['dataset_id']} {summary['elapsed_s']}s")

    auto = [
        f"{d.question.rstrip('？')} = {draft.params.get(d.key, d.default)}"
        for d in dec.decisions_for(profile, limit=99)
        if d.key not in draft.user_set
    ]

    out = {
        "status": "ok",
        "dataset_id": summary["dataset_id"],
        "draft_id": draft.draft_id,
        "summary": summary,
        "auto_decided": auto,
        "warnings": [i for i in issues if i["severity"] != "block"],
        "plan_markdown": plan_md,
        "next": (
            f'调 sr_deliver(dataset_id="{summary["dataset_id"]}", want="信道") 拿取货代码；'
            "want 可写自然语言，例如「信道 + PMI + SRS」"
        ),
    }
    if summary["snr_filter"]["enabled"]:
        out["snr_filter_note"] = (
            f"信噪比筛选接受率 {summary['snr_filter']['accept_rate']:.0%}"
            f"（尝试 {summary['snr_filter']['attempted']} 个，接受 {summary['num_samples']} 个）。"
            "信噪比由路损、发射功率和噪声共同决定，无法直接设定，只能生成后筛选。"
        )
    return _jsonable(out)


# ---------------------------------------------------------------------------
# 取货
# ---------------------------------------------------------------------------


@tool()
def sr_deliver(dataset_id: str, want: str | None = None) -> dict[str, Any]:
    """按需生成取货代码——返回可直接运行的 Python，不是数据。

    want 可以写自然语言："信道"、"信道 + PMI + SRS RSRP"、"我还想看时延功率谱"。
    不写则只给信道。

    同一个数据集可以反复取货要不同的测量量，**不必重跑仿真**。
    """
    try:
        return _jsonable(dlv.build_code(dataset_id, want))
    except (KeyError, FileNotFoundError, ValueError) as exc:
        return {"error": f"加载数据集失败：{exc}"}


@tool()
def sr_describe_dataset(dataset_id: str) -> dict[str, Any]:
    """查看已生成数据集的维度、统计分布和可用字段。"""
    try:
        s = gen.load_summary(dataset_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        return {"error": f"加载数据集失败：{exc}"}
    return _jsonable(
        {
            "dataset_id": dataset_id,
            "shape": s.get("shape"),
            "num_samples": s.get("num_samples"),
            "channel_model": s.get("channel_model"),
            "scenario": s.get("scenario"),
            "has_angles": s.get("is_cdl"),
            "sinr_dB": s.get("sinr_dB"),
            "pathloss_dB": s.get("pathloss_dB"),
            "distance_3d_m": s.get("distance_3d_m"),
            "los_ratio": s.get("los_ratio"),
            "size_mb": s.get("size_mb"),
            "elapsed_s": s.get("elapsed_s"),
            "path": s.get("path"),
            "available_measurements": list(dlv.measure.MEASUREMENT_CATALOG),
        }
    )


@tool()
def sr_list_datasets() -> dict[str, Any]:
    """列出本机已生成的数据集。"""
    return _jsonable({"datasets": gen.list_datasets()})


@tool()
async def sr_validate(dataset_id: str) -> dict[str, Any]:
    """可信度体检：这批信道能不能拿来下结论。

    三类检查：对标 3GPP 38.901 的路损与时延扩展；对标物理定律（时频能量守恒、
    谱效不超容量上界、预编码方案的性能排序、SISO 退化到香农公式）；
    统计层面（蒙特卡洛是否收敛、信噪比分布是否够宽）。

    **蒙特卡洛仿真前建议先跑一次。** 结论建立在信道之上，
    ``passed`` 为 false 时先修配置再做实验。
    """
    return await anyio.to_thread.run_sync(functools.partial(_validate_sync, dataset_id))


def _validate_sync(dataset_id: str) -> dict[str, Any]:
    from . import loader as ld
    from . import validate as va

    ds = ld.load(dataset_id)
    rep = va.full_report(ds)
    out = rep.as_dict()
    out["dataset_id"] = dataset_id
    out["text"] = rep.text()
    return _jsonable(out)


@tool()
async def sr_link_performance(
    dataset_id: str,
    snr_db: float | None = None,
    methods: list[str] | None = None,
    receiver: str = "mmse",
    use_estimated_csi: bool = False,
    power_constraint: str = "nebf",
) -> dict[str, Any]:
    """算谱效：预编码 → 逐层 SINR → 频谱效率，并横向对比多种预编码方案。

    这是蒙特卡洛仿真最常用的评价链路。返回各方案的谱效均值、95% 置信区间
    和收敛判断——**不收敛时方案间的差异可能只是噪声**，会明确标出。

    参数
    ----
    methods : 默认对比 ``["svd", "svd_wideband", "type1", "dft"]``。
        ``svd`` 是逐 RB 协方差特征波束（单快照时等价瞬时 SVD）；
        ``type1`` 是 Type-I-style 单面板列码本子集近似；DFT 是单层波束。
        用户自研方案应当和这几个在同一批信道上比。
    use_estimated_csi : True 时用估计信道计算预编码、用理想信道评估性能，
        得到的是"CSI 有误差时的实际代价"——CSI 反馈类课题的核心对比。
    receiver : ``mmse``（默认）/ ``zf`` / ``mrc``。
    power_constraint : ``nebf``（默认）/ ``ebf`` / ``pebf``。矩阵约定是
        ``Q[frequency, antenna, stream]``，所以每天线功率对应 antenna 行范数。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _link_perf_sync,
            dataset_id=dataset_id, snr_db=snr_db, methods=methods,
            receiver=receiver, use_estimated_csi=use_estimated_csi,
            power_constraint=power_constraint,
        )
    )


def _link_perf_sync(
    *, dataset_id: str, snr_db: float | None, methods: list[str] | None,
    receiver: str, use_estimated_csi: bool, power_constraint: str,
) -> dict[str, Any]:
    import numpy as np

    from . import loader as ld

    ds = ld.load(dataset_id)
    ms = tuple(methods or ("svd", "svd_wideband", "type1", "dft"))
    explicit_snr = None if snr_db is None else float(snr_db)
    kw: dict[str, Any] = {
        "snr_db": explicit_snr, "receiver": receiver,
        "power_constraint": power_constraint}
    if use_estimated_csi:
        kw["channels_for_precoding"] = ds.h_est

    cmp = ds.compare_precoders(methods=ms, **kw)
    best = max(cmp.items(), key=lambda kv: kv[1]["se_mean"])
    unconverged = [m for m, v in cmp.items() if not v["converged"]]

    return _jsonable(
        {
            "dataset_id": dataset_id,
            "n_samples": int(ds.n),
            "snr_db": None if explicit_snr is None else round(explicit_snr, 2),
            "power_constraint": str(power_constraint).lower(),
            "dataset_sinr_db": (
                {
                    "p5": round(float(np.percentile(ds.sinr_dB, 5)), 2),
                    "median": round(float(np.median(ds.sinr_dB)), 2),
                    "p95": round(float(np.percentile(ds.sinr_dB, 95)), 2),
                }
                if explicit_snr is None else None
            ),
            "operating_point": (
                {
                    "mode": "dataset_geometric_sinr_per_sample",
                    "anchor": "prebeam_mean_coefficient_power",
                    "interference": (
                        "SIR 标量 + 已标定空间协方差（条件满足时）"
                        if ds.h_interferers is not None
                        else "总损伤 I+N 作为各向同性噪声"
                    ),
                }
                if explicit_snr is None
                else {"mode": "synthetic_prebeam_snr", "snr_db": explicit_snr}
            ),
            "receiver": receiver,
            "csi_for_precoding": "estimated" if use_estimated_csi else "ideal",
            "results": cmp,
            "best_method": best[0],
            "note": (
                "谱效口径：SE = mean_rb Σ_layer log2(1 + 后处理SINR)。"
                "vs_svd_pct 是相对逐 RB 协方差特征波束的百分比；真正的容量上界"
                "由 water-filling capacity 单独给出，不能把 svd 曲线冒充容量上界。"
            ),
            "warning": (
                f"这些方案的置信区间还没收敛到 5%：{unconverged}。"
                f"样本量不足时方案间差异可能只是随机波动，建议加大 num_samples。"
                if unconverged
                else None
            ),
        }
    )


@tool()
async def sr_calibrate(dataset_id: str) -> dict[str, Any]:
    """按 3GPP TR 38.901 §7.8 的口径算校准量。

    这是业界判断"信道生成得对不对"的标准做法：不看曲线好不好看，而是把标准
    规定的几个统计量按规定口径算出来，跟各参与方提交给 3GPP 的参考曲线对。

    出的量（括号内是标准里的条款与指标号）：

    * 耦合损耗 CDF（§7.8.1 指标1）—— 串联检验路损模型 + 天线方向图 + 小区选择
    * 几何量 CDF，含噪与不含噪两条（§7.8.1 指标2 / §7.8.2 指标2）
    * 时延扩展与角度扩展 ASD/ASA/ZSD/ZSA（§7.8.2 指标3，Annex A.1 圆周定义）
    * PRB 奇异值：最大、次大、比值三条 CDF，10log10 尺度（§7.8.2 指标4）

    参考曲线在 3GPP 文稿 R1-165974（大尺度）、R1-165975（全校准）、
    R1-1909704（InF）里。**本工具只出数不判决**，判决在 ``sr_gate``。
    不适用的项会说明原因（例如 CDL 的时延角度是查表固定值，CDF 是退化的）。
    """
    return await anyio.to_thread.run_sync(functools.partial(_calibrate_sync, dataset_id))


def _calibrate_sync(dataset_id: str) -> dict[str, Any]:
    from . import calibration as cal
    from . import loader as ld

    rep = cal.calibration_report(ld.load(dataset_id))
    out = rep.as_dict()
    out["text"] = rep.text()
    return _jsonable(out)


@tool()
async def sr_gate(
    dataset_id: str,
    stage: str = "channel",
    expected_precoding_csi_source: str | None = None,
) -> dict[str, Any]:
    """评审门：拦住站不住的结论。

    ``stage="channel"``（门 1）—— 生成之后、做实验之前跑。把可信度体检的
    结果翻译成门禁语言：硬性检查不通过就是拦截项，不修不许往下走。

    门 2（比较公平）与门 3（结论站得住）在 ``sr_compare_arms`` 里一次跑完，
    因为它们需要两个方案的逐样本结果。
    """
    if stage != "channel":
        return {
            "error": f"stage={stage!r} 不支持",
            "hint": "门 2 与门 3 请用 sr_compare_arms，它需要两个方案的逐样本结果",
        }
    return await anyio.to_thread.run_sync(
        functools.partial(
            _gate_sync,
            dataset_id,
            expected_precoding_csi_source=expected_precoding_csi_source,
        )
    )


def _gate_sync(
    dataset_id: str,
    *,
    expected_precoding_csi_source: str | None = None,
) -> dict[str, Any]:
    from . import gates as g
    from . import loader as ld

    res = g.gate_channel(
        ld.load(dataset_id),
        expected_precoding_csi_source=expected_precoding_csi_source,
    )
    out = res.as_dict()
    out["text"] = res.text()
    return _jsonable(out)


@tool()
async def sr_compare_arms(
    dataset_id: str,
    method_a: str,
    method_b: str,
    name_a: str = "方案A",
    name_b: str = "方案B",
    csi_a: str = "ideal",
    csi_b: str = "ideal",
    receiver: str = "mmse",
    snr_db: float | None = None,
    max_samples: int = 500,
    varies: list[str] | None = None,
) -> dict[str, Any]:
    """在**同一批信道**上跑两个方案，做配对比较，并连过门 2、门 3。

    这是下结论前的最后一道关。它做四件普通的"比均值"做不到的事：

    1. **配对** —— 两臂共用同一批信道实例，共同的路损/撒点/衰落起伏被差分
       抵消，剩下的才是方案本身的差别。配对设计所需样本数常比非配对少一个数量级。
    2. **公平性检查（门 2）** —— 配置漂移、CSI 口径不一致（一边理想一边估计
       就是让自己的方法偷看答案）会被直接拦截。
    3. **统计检验（门 3）** —— 配对 t 检验 + Wilcoxon 符号秩双保险，95% 置信
       区间跨零就拦，单个样本贡献过半也拦。
    4. **一句可直接写进报告的结论** —— 过不了门时它会明说结论不成立及原因。

    ``method_*``：``svd`` / ``svd_wideband`` / ``type1`` / ``dft`` / ``mrt`` / ``identity``。
    ``csi_*``：``ideal`` 用理想信道；``estimated`` 用数据集主估计；
    ``srs`` 用 gNB 的 UL SRS 估计；``csirs`` 用 UE 的 DL CSI-RS 估计。
    SRS-vs-PMI 端到端方案比较应写 ``srs`` vs ``csirs``，并用
    ``varies=["csi"]`` 明确这是方案链差异；只测码本量化则两臂都写 ``srs``。
    ``snr_db`` 不给时用数据集逐样本自身的 SINR（各用户真实工作点）。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _compare_arms_sync,
            dataset_id=dataset_id, method_a=method_a, method_b=method_b,
            name_a=name_a, name_b=name_b, csi_a=csi_a, csi_b=csi_b,
            receiver=receiver, snr_db=snr_db, max_samples=max_samples,
            varies=varies,
        )
    )


def _compare_arms_sync(
    *, dataset_id: str, method_a: str, method_b: str, name_a: str, name_b: str,
    csi_a: str, csi_b: str, receiver: str, snr_db: float | None, max_samples: int,
    varies: list[str] | None = None,
) -> dict[str, Any]:
    from . import loader as ld

    ds = ld.load(dataset_id)
    res = ds.compare_arms(
        {
            "name": name_a, "method": method_a, "csi": csi_a,
            "receiver": receiver, "varies": list(varies or []),
        },
        {
            "name": name_b, "method": method_b, "csi": csi_b,
            "receiver": receiver, "varies": list(varies or []),
        },
        snr_db=snr_db, max_samples=max_samples,
    )
    out = res.as_dict()
    out["dataset_id"] = dataset_id
    out["text"] = res.text()
    return _jsonable(out)


@tool()
def sr_sample_size(
    std_diff: float | None = None,
    expected_effect: float | None = None,
    n_current: int | None = None,
) -> dict[str, Any]:
    """样本数该定多少 —— **算出来的，不是问用户的**。

    蒙特卡洛跑多少次，取决于想检出多大的效应和逐样本差值有多离散::

        N ≥ ( (1.96 + 0.84) · σ_d / Δ )^2

    三种用法：

    * 给 ``std_diff`` 和 ``expected_effect`` → 返回需要的样本数；
    * 给 ``std_diff`` 和 ``n_current`` → 返回这个实验最小能检出多大效应。
      **这个数比样本数更该先看**：它比期望增益还大时，实验无论跑出什么结果
      都不足以下结论；
    * 什么都不给 → 返回试点流程（先跑 20 个样本量方差）。

    ``std_diff`` 从 ``sr_compare_arms`` 的 ``paired.std_diff`` 取。
    """
    from . import decisions as dec

    return _jsonable(
        dec.sample_size_advice(
            std_diff=std_diff, expected_effect=expected_effect, n_current=n_current
        )
    )


@tool()
def sr_missing_slots(
    answered_design: list[str] | None = None,
    answered_params: list[str] | None = None,
) -> dict[str, Any]:
    """结论模板里还空着哪些槽 —— 决定该主动问用户什么。

    一次蒙特卡洛仿真的产出说到底就是一句话::

        在【场景】下，【方法】相对【基线】在【指标】上【效应 ± 置信区间】（n 样本），
        该结论在【扫描维度】上成立。

    每个方括号是一个必须填的槽。**空着的槽就是该问的问题**，按"空着的代价"
    从大到小排序返回，每个槽带 3~4 个选项。

    注意样本数不在槽里——它是由效应量和试点方差**算出来**的（``sr_sample_size``），
    把它当问题抛回给用户是把该自己做的功课推回去。
    """
    from . import decisions as dec

    slots = dec.missing_slots(set(answered_design or []), set(answered_params or []))
    return _jsonable(
        {
            "conclusion_template": dec.CONCLUSION_TEMPLATE,
            "n_missing": len(slots),
            "slots": slots,
            "how_to_ask": (
                "一轮问 2~4 个，每题 3~4 个选项，推荐项放第一位并说明理由。"
                "允许用户自由作答。用户说「随便 / 默认就行」时立刻停止提问。"
            ),
        }
    )


@tool()
def sr_lock_analysis(
    primary_metric: str = "spectral_efficiency",
    baseline: str = "",
    draft_id: str = "",
    csi_basis: str = "ideal",
    expected_effect: float | None = None,
    metric_unit: str | None = None,
    higher_is_better: bool = True,
    secondary_metrics: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """**在生成数据之前**把主指标与基线定下来（预注册）。

    三道门证明不了一件事：主指标是看数据之前定的，还是跑完之后挑出来的
    那个最好看的。真实过程往往是——跑完发现平均谱效没提升，顺手换成 5%
    边缘用户谱效，有提升就报了这个。每一步都合理，合起来是在多个指标里
    挑赢的那个，假阳性率远高于 5%。

    做法很轻：写一个 JSON、算个 SHA-256、不可原地改。把返回的 ``prereg_id``
    传给 ``sr_generate``，之后 ``sr_compare_results`` 会判断用的指标是不是
    当初定的：一致 → ``primary``；不一致 → ``exploratory``，**结论句里会明说
    这不是预注册主结论**。

    改主意就再调一次，会得到新 ``prereg_id``，旧的不动——"改过口径"这件事
    本身留了痕。
    """
    from . import analysis as an

    pr = an.lock(
        draft_id=draft_id, primary_metric=primary_metric, baseline=baseline,
        csi_basis=csi_basis, expected_effect=expected_effect, metric_unit=metric_unit,
        higher_is_better=higher_is_better, secondary_metrics=secondary_metrics, note=note,
    )
    d = pr.as_dict()
    d["text"] = pr.text()
    d["next"] = f'把 prereg_id 传给 sr_generate：sr_generate(..., prereg_id="{pr.prereg_id}")'
    return _jsonable(d)


@tool()
def sr_export_eval_template(
    dataset_id: str,
    metric: str = "spectral_efficiency",
) -> dict[str, Any]:
    """给**自研算法**导出一份评测脚本骨架，让它能进门 2 / 门 3。

    内置的 `sr_compare_arms` 只认六种预编码，自研的 CSI 压缩、信道估计、
    波束管理、调度算法进不来。这个工具补那一层：

    1. 拿到 `code`，写进 .py 文件；
    2. 把 `my_algorithm` 的函数体换成你的算法（**不改也能跑**，
       预填的示例是估计 CSI 下的 SVD vs Type I，先确认管道通再换）；
    3. 运行它，会注册两个臂并打印 `result_id`；
    4. 把两个 id 交给 `sr_compare_results` 判决。

    **MCP 不执行用户代码**，脚本在用户自己的进程里跑，只把标准化的逐样本
    结果注册回来。逐样本数值落 .npz，不进 MCP JSON。
    """
    from . import results as rs

    return _jsonable(rs.eval_template(dataset_id, metric=metric))


@tool()
async def sr_compare_results(
    result_id_a: str,
    result_id_b: str,
    claimed_gain: float | None = None,
) -> dict[str, Any]:
    """判决两个**外部算法结果**，连过门 2、门 3。

    与 `sr_compare_arms` 用的是**同一套统计与门控实现**，判决标准完全一致
    ——自研算法不走宽松通道。区别只在数值从哪来：那个现场跑内置预编码，
    这个读已注册的结果。

    注册时锁死三件事，任一不成立就拦：数据集内容摘要一致、样本 ID 逐个按序
    一致、指标与单位一致。因为配对检验的全部有效性建立在"第 i 个数对应同一个
    信道实例"上，**错配时它照样会算出一个看起来很显著的 p 值**。

    返回的 `statement` 会写清用的哪个检验、指标是什么，以及这是预注册主结论
    还是探索性分析。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _compare_results_sync,
            result_id_a=result_id_a, result_id_b=result_id_b, claimed_gain=claimed_gain,
        )
    )


def _compare_results_sync(
    *, result_id_a: str, result_id_b: str, claimed_gain: float | None
) -> dict[str, Any]:
    from . import gates as g

    res = g.compare_results(result_id_a, result_id_b, claimed_gain=claimed_gain)
    out = res.as_dict()
    out["text"] = res.text()
    return _jsonable(out)


@tool()
def sr_list_results(dataset_id: str | None = None) -> dict[str, Any]:
    """列出已注册的外部算法结果。不给 dataset_id 就列全部。"""
    from . import analysis as an
    from . import results as rs

    return _jsonable(
        {
            "results": rs.list_results(dataset_id),
            "pregs": an.list_pregs(),
        }
    )


@tool()
async def sr_throughput(
    dataset_id: str,
    mcs_table: int = 3,
    target_bler: float = 0.1,
    max_samples: int = 200,
    method: str = "svd",
) -> dict[str, Any]:
    """算**真实吞吐**（Mbps）与 3GPP 口径的边缘用户指标，不是香农上界。

    `sr_link_performance` 给的是 `SE = Σ log2(1+SINR)`——香农谱效，是个
    任何真实系统都达不到的上界。这个工具走业界做系统级仿真的标准路径
    （链路到系统映射），把三项真实损失算进来：

    1. **调制受限** —— 显式选表1时，20 dB下64QAM会早于256QAM封顶
    2. **码率离散** —— MCS 只有 29 档
    3. **有限码长 + 实现损失** —— LDPC 距容量 1~2 dB

    返回吞吐的均值/中位/**5% 边缘用户**/95% 峰值、谱效、MCS 分布、平均 BLER。
    边缘用户吞吐是 3GPP 评估里的公平性指标，比均值更能说明问题。

    `mcs_table`默认为3：预置256QAM MCS + NewTx/ReTx解调曲线。1 = 最高64QAM
    （38.214 Table 5.1.3.1-1），2 = 含256QAM的标准表（Table 5.1.3.1-2）；
    两者均为显式可选分支，64QAM不会被默认路径触发。

    表 1/2 的 BLER 是有限码长分析模型，不是实测。表 3 的 BLER 来自预置的
    解调曲线，也不是 3GPP 标准曲线；源标签 Es/No 表示经典 MMSE 接收机 SINR。
    表 3 的 HARQ 只允许一次重传（**用户 2026-09-02 确认的显式决定**，不是待补
    项；现场规格的 16 进程 / 最多 3 次重传当前不做）：默认 IR 用半谱效等效 MCS，
    可选 CC 用原 MCS 的码字 SINR +3.0103 dB；两者都只查询 NewTx 曲线，空口 MCS
    不会被等效档改写。
    表 3 使用版本化256QAM映射。历史内部表行0..14对应上报4-bit CQI1..15；
    上报CQI0是out-of-range，不调度。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _throughput_sync, dataset_id=dataset_id, mcs_table=mcs_table,
            target_bler=target_bler, max_samples=max_samples, method=method,
        )
    )


def _throughput_sync(*, dataset_id: str, mcs_table: int, target_bler: float,
                     max_samples: int, method: str) -> dict[str, Any]:
    from . import linkadapt as la
    from . import loader as ld

    ds = ld.load(dataset_id)
    throughput_kw: dict[str, Any] = {
        "mcs_table": mcs_table, "target_bler": target_bler, "method": method,
    }
    if mcs_table != 3:
        throughput_kw["cqi_table"] = min(mcs_table, 2)
    st = ds.throughput(max_samples=max_samples, **throughput_kw)
    out = st.as_dict()
    out["dataset_id"] = dataset_id
    out["mcs_table"] = mcs_table
    out["mcs_source"] = la.MCS_TABLE_SOURCES[mcs_table]
    out["text"] = st.text()
    top = max(st.mcs_distribution) if st.mcs_distribution else 0
    capped = st.mcs_distribution.get(top, 0) / max(st.n, 1)
    if capped > 0.25 and mcs_table == 1:
        out["hint"] = (
            f"{capped:.0%} 的样本压在最高档 MCS {top} —— 限制来自 MCS 表而不是信道。"
            f"试 mcs_table=2（含 256QAM）。"
        )
    return _jsonable(out)


@tool()
def sr_mcs_info(
    table: int = 3,
    show_bler_anchors: bool = False,
) -> dict[str, Any]:
    """查 MCS / CQI 表，以及分析模型或表驱动 BLER 的门限。

    `show_bler_anchors=true` 时给出各 MCS 达到 10% BLER 所需的有效 SINR，
    以及它距同频谱效率的香农极限有多远。**这是模型预测，摆出来供人工对照
    公开的 NR 链路级曲线**——常见量级是 MCS0 约 -5~-7 dB、MCS28 约 20~23 dB。

    `table=1/2` 是逐字录入的 38.214 标准值，BLER 是分析模型。
    `table=3` 是内置的 20B 256QAM MCS 与 NewTx/ReTx 预置曲线：返回两套码率、
    10% BLER 门限和数据哈希自检。它不是 3GPP 标准表；源标签 Es/No 表示
    经典 MMSE 的单码字有效 SINR。系统按已确认合同只用 MCS+SINR 查询通用
    NewTx 曲线；TBS/RE/rank/场景不作为 BLER 查询轴，ReTx 行仅保留审计。
    其 `cqi_table` 同时给历史表行0..14和上报4-bit CQI1..15；上报CQI0是
    out-of-range。表行14/上报CQI15的 `requested_mcs=28` 在当前MCS0..27
    profile上会显式标记钳位。
    """
    from . import linkadapt as la

    if table not in la.MCS_TABLES:
        raise ValueError(f"table 应为 {sorted(la.MCS_TABLES)}，收到 {table}")

    mcs_rows = (
        la.bc.mcs_profile_rows() if table == 3
        else [m.as_dict() for m in la.MCS_TABLES[table]]
    )
    cqi_rows = (
        la.internal_cqi_mapping_rows(mcs_table=3)
        if table == 3 else [
            {"index": c.index, "modulation": la._MOD_NAME[c.q_m],
             "code_rate": round(c.r_1024 / 1024, 4), "se": c.se}
            for c in la.CQI_TABLES[table]
        ]
    )
    out: dict[str, Any] = {
        "table": int(table),
        "mcs_table": mcs_rows,
        "cqi_table": cqi_rows,
        "verify": la.bc.verify_curves() if table == 3 else la.verify_tables(),
        "source": la.MCS_TABLE_SOURCES[table],
        "cqi_source": la.INTERNAL_CQI_SOURCE if table == 3 else "same table family",
    }
    if show_bler_anchors:
        out["bler_anchors"] = (
            la.curve_anchor_check() if table == 3
            else la.DEFAULT_BLER.anchor_check(table=table)
        )
    return _jsonable(out)


@tool()
def sr_bler_curve(
    mcs: int,
    tx_mode: str = "newtx",
    sinr_db_list: list[float] | None = None,
    target_bler: float = 0.1,
) -> dict[str, Any]:
    """查预置表中的单档 BLER 曲线，并可在任意 SINR 点插值。

    `mcs` 为 0..27；`tx_mode` 为 `newtx` 或 `retx`。默认返回完整原始点、码率、
    10% BLER 门限和来源口径。`retx` 仅用于查看/审计原始资产；当前系统 HARQ
    的 IR/CC 都从 NewTx 曲线推导。传 `sinr_db_list` 时额外返回查询点的 BLER。

    插值在 log10(BLER) 域线性完成；低于曲线范围钳到 1，高于范围钳到最后一个
    实测点，绝不外推一条看似精确的尾巴。源脚本标签 Es/No 已确认表示经典
    MMSE 的单码字有效 SINR；返回值同时保留原始标签和物理口径。
    """
    from . import linkadapt as la

    return _jsonable(la.bler_curve(
        mcs=mcs, tx_mode=tx_mode, target_bler=target_bler,
        sinr_db=sinr_db_list,
    ))


@tool()
async def sr_tdd_mcs(
    dataset_id: str,
    cqi: int,
    cqi_numbering: str = "internal_row",
    sample_index: int = 0,
    olla_mcs_offset: float = 0.0,
    target_bler: float = 0.1,
    max_rank: int = 4,
    use_estimated_csi: bool = True,
    feedback_ack: bool | None = None,
    olla_ack_step_mcs: float = 0.01,
    power_constraint: str = "nebf",
) -> dict[str, Any]:
    """TDD 下按 CQI、SVD-vs-PMI BF Gain 和 OLLA 选择最终 MCS。

    真实调用链是：256QAM CQI → 显式离散表映射初始 MCS → 该 MCS 的 NewTx 目标
    BLER SINR 门限 → 在同一信道/CSI/rank/功率/干扰/MMSE 接收机下逐 RB、逐流计算
    ``SINR_SVD - SINR_PMI`` → 在 dB 域对全部 RB×流求算术平均 → 按表 3 重映射
    MCS → 加连续的 ``olla_mcs_offset`` → ``floor`` → 钳位到 0..27。

    ``olla_mcs_offset`` 的单位是连续 MCS 档位。``sr_system_sim`` /
    ``experience_v2`` 也遵循同一顺序：先由 SINR 反折无 OLLA MCS，再加
    用户级 MCS-domain OLLA、floor 并钳位。系统 API 的 ``*_db`` 参数名
    仅为历史兼容保留，值不再解释为 dB。

    ``cqi_numbering="internal_row"`` 保留历史脚本的 0..14 数组行编号，其中 row 0
    对应上报 4-bit CQI 1；``reported_4bit`` 接受标准 codepoint 0..15，其中 0 明确
    表示 out-of-range、不调度。返回会同时给出 ``cqi_row`` 与
    ``reported_cqi_codepoint``，避免再把数组第 0 行误称为上报 CQI 0。

    `feedback_ack` 可选：给出时按目标首传 BLER 更新下一时刻的 OLLA；10% 默认对应
    ACK +0.01、NACK -0.09 MCS。当前时刻
    使用传入的 OLLA，反馈只影响返回的 `olla_next_offset_mcs`。

    默认 ``power_constraint=nebf``。返回的 gNB 预测量与真实接收量分开：
    ``SINR_NEBF/PEBF/EBF`` 和 ``SINR_PMI`` 在 gNB CSI 上形成 BF Gain；最终
    BLER 只使用同一物理权打到 ``h_true`` 后的实际 post-MMSE SINR 查表。
    只有 CQI/BF/OLLA 标量而没有实际接收 SINR 时，BLER 必须返回未知。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _tdd_mcs_sync,
            dataset_id=dataset_id,
            cqi=cqi,
            cqi_numbering=cqi_numbering,
            sample_index=sample_index,
            olla_mcs_offset=olla_mcs_offset,
            target_bler=target_bler,
            max_rank=max_rank,
            use_estimated_csi=use_estimated_csi,
            feedback_ack=feedback_ack,
            olla_ack_step_mcs=olla_ack_step_mcs,
            power_constraint=power_constraint,
        )
    )


def _tdd_mcs_sync(
    *,
    dataset_id: str,
    cqi: int,
    cqi_numbering: str,
    sample_index: int,
    olla_mcs_offset: float,
    target_bler: float,
    max_rank: int,
    use_estimated_csi: bool,
    feedback_ack: bool | None,
    olla_ack_step_mcs: float,
    power_constraint: str,
) -> dict[str, Any]:
    from . import linkadapt as la
    from . import loader as ld

    numbering = str(cqi_numbering).strip().lower()
    if numbering not in ("internal_row", "reported_4bit"):
        return {"error": "cqi_numbering 只支持 internal_row / reported_4bit"}
    # Validate the dataset/sample identity even when reported CQI0 causes an early
    # no-schedule return.  Otherwise a typo in dataset_id would look like a valid
    # out-of-range decision and silently bypass the normal Dataset.tdd_mcs checks.
    ds = ld.load(dataset_id)
    sample = int(sample_index)
    if sample < 0 or sample >= ds.n:
        raise IndexError(
            f"sample index must be 0..{ds.n - 1}, got {sample_index}")
    if numbering == "reported_4bit":
        mapping = la.reported_cqi_to_mcs(cqi, mcs_table=3)
        if not mapping["scheduled"]:
            return _jsonable({
                **mapping,
                "dataset_id": dataset_id,
                "sample_index": sample,
                "cqi_input": int(cqi),
                "cqi_input_numbering": numbering,
                "reason": "reported_cqi_out_of_range",
                "actual_bler_available": False,
                "bler_status": "not_scheduled_out_of_range",
            })
        cqi_row = int(mapping["cqi_row"])
    else:
        mapping = la.internal_cqi_to_mcs(cqi, mcs_table=3)
        cqi_row = int(mapping["cqi_row"])
    result = ds.tdd_mcs(
        sample,
        cqi_index=cqi_row,
        olla_mcs_offset=olla_mcs_offset,
        target_bler=target_bler,
        max_rank=max_rank,
        use_estimated_csi=use_estimated_csi,
        feedback_ack=feedback_ack,
        olla_ack_step_mcs=olla_ack_step_mcs,
        power_constraint=power_constraint,
    )
    result.update({
        "cqi_input": int(cqi),
        "cqi_input_numbering": numbering,
    })
    return _jsonable(result)


@tool()
async def sr_sweep_snr(
    dataset_id: str,
    snr_db_list: list[float] | None = None,
    mcs_table: int = 3,
    max_samples: int = 60,
) -> dict[str, Any]:
    """扫信噪比，出**谱效/吞吐 vs SNR 曲线** —— 无线论文里最标准的那张图。

    对同一批信道，把工作点信噪比设成一组值，逐点给出香农谱效、实际谱效、
    吞吐、选中的 MCS。**同一批信道**意味着各点之间是配对的，曲线不会被
    信道抽样噪声搅乱。

    默认用预置256QAM profile扫 -5 ~ 35 dB。表1的64QAM只在显式指定时使用。
    返回里 `efficiency_vs_shannon` 的走势最有信息量：
    低信噪比处接近 1（受噪声限），高信噪比处掉下来（受 MCS 表封顶限）。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _sweep_sync, dataset_id=dataset_id, snr_db_list=snr_db_list,
            mcs_table=mcs_table, max_samples=max_samples,
        )
    )


def _sweep_sync(*, dataset_id: str, snr_db_list: list[float] | None,
                mcs_table: int, max_samples: int) -> dict[str, Any]:
    import numpy as np

    from . import linkadapt as la
    from . import loader as ld

    ds = ld.load(dataset_id)
    grid = snr_db_list or [-5, 0, 5, 10, 15, 20, 25, 30, 35]
    n = min(int(ds.n), int(max_samples))
    n_prb = int(ds.h_true.shape[2])
    rows = []
    for snr in grid:
        link_kw: dict[str, Any] = {"mcs_table": mcs_table}
        if mcs_table != 3:
            link_kw["cqi_table"] = min(mcs_table, 2)
        res = [ds.link_adaptation(
            i, snr_db=float(snr), n_prb=n_prb, **link_kw)
            for i in range(n)]
        st = la.throughput_stats(res)
        rows.append({
            "snr_db": float(snr),
            "se_shannon": round(float(np.mean([r.se_shannon for r in res])), 3),
            "se_achieved": round(st.mean_se, 3),
            "efficiency_vs_shannon": round(
                float(np.mean([r.efficiency_vs_shannon for r in res])), 3),
            "throughput_mbps": round(st.mean_mbps, 2),
            "cell_edge_mbps": round(st.cell_edge_mbps, 2),
            "mcs_median": int(np.median([r.mcs_index for r in res])),
            "mean_bler": round(st.mean_bler, 4),
            "mean_retx_bler": (
                None if st.mean_retx_bler is None else round(st.mean_retx_bler, 4)
            ),
        })
    return _jsonable({
        "dataset_id": dataset_id, "n_samples": n, "mcs_table": mcs_table,
        "mcs_source": la.MCS_TABLE_SOURCES[mcs_table],
        "bler_source": st.bler_source,
        "harq_model": st.harq_model,
        "curve": rows,
        "note": (
            "各点跑在同一批信道上，彼此配对，曲线不含信道抽样噪声。"
            "efficiency_vs_shannon 在高信噪比处下滑通常是 MCS 表封顶所致，"
            "不是信道或算法的问题。"
        ),
    })


# ---------------------------------------------------------------------------
# 干扰强度
# ---------------------------------------------------------------------------


@tool()
def sr_interference_report(dataset_id: str) -> dict[str, Any]:
    """一个数据集的干扰画像：业务域 IoT + 测量域导频 SIR。只读已落盘的标量。

    **业务域和测量域是两回事**，报告分开给：

    * ``traffic_domain``——PDSCH/PUSCH 受到的干扰，用 IoT（噪声抬升 (I+N)/N）
      刻画。20 dB 以上算高干扰，同时给出等效小区负载。
    * ``measurement_domain``——SRS / CSI-RS 导频受到的干扰，决定信道估计精度。
      给出估计 NMSE 的下限。这两列只在 ``link="BOTH"`` 生成的数据里有。

    IoT 由几何 SIR 与 SINR 推出（``IoT = SIR/(SIR-SINR)``，线性域）。
    当前 first-party ``snr_dB`` / ``sinr_dB`` 共享预数字波束、每 RB 参考，
    所以两者之差是等价旁证；主公式仍只依赖 SIR+SINR，以兼容外部/旧数据源。

    贴在 ±50 dB 契约边界上的样本、以及没有干扰源的哨兵样本会单独计数而不是
    混进统计，``notes`` 里会说明。
    """
    from . import interference as itf

    return _jsonable(itf.interference_report(dataset_id))


@tool()
def sr_iot_convert(
    iot_db: float | None = None,
    load: float | None = None,
    sinr_db: float | None = None,
    sir_db: float | None = None,
) -> dict[str, Any]:
    """IoT 相关的换算与分级。三种用法，给哪组参数就算哪个。

    * 给 ``sinr_db`` + ``sir_db``：算这一点的 IoT（两者必须来自同一几何预算）。
    * 给 ``iot_db``：分级 + 换成等效小区负载。
    * 给 ``load``：由等效负载反推 IoT。

    等效负载用的是上行极点容量关系 ``IoT = 1/(1-load)``，是**解释性**换算，
    帮助把 "IoT 20 dB" 读成 "等效 99% 负载"，不代表仿真真按这个负载调度。
    """
    from . import interference as itf

    out: dict[str, Any] = {}
    if sinr_db is not None and sir_db is not None:
        v = float(itf.iot_db(sinr_db, sir_db))
        out["from_sinr_sir"] = {"sinr_db": sinr_db, "sir_db": sir_db, **itf.classify_iot(v)}
        if iot_db is None:
            iot_db = v
    if iot_db is not None:
        out["classification"] = itf.classify_iot(float(iot_db))
    if load is not None:
        out["from_load"] = {
            "load": load,
            **itf.classify_iot(itf.iot_from_load(float(load))),
        }
    if not out:
        out["error"] = "至少要给 iot_db、load，或者 sinr_db + sir_db 这一对。"
    out["bands"] = [
        {"upper_db": (None if h == float("inf") else h), "band": b, "meaning": w}
        for h, b, w in itf.IOT_BANDS
    ]
    return _jsonable(out)


@tool()
def sr_design_interference(target_iot_db: float = 20.0) -> dict[str, Any]:
    """要构造某个干扰强度的场景，该动哪些旋钮。

    **不返回保证达标的配置。** IoT 由几何、负载、功率共同决定，唯一可靠的
    确认方式是生成一批再用 ``sr_interference_report`` 复核。这里给的是方向与
    量级，以及各旋钮在 ChannelHub 几何模型里的**实际**作用——有几个和教科书
    直觉不一样，写在每条的 note 里。
    """
    from . import interference as itf

    return _jsonable(itf.design_hint(target_iot_db))


# ---------------------------------------------------------------------------
# 场景探测
# ---------------------------------------------------------------------------


@tool()
async def sr_probe_scenario(
    preset: str | None = None,
    config: dict[str, Any] | None = None,
    num_samples: int = 30,
) -> dict[str, Any]:
    """花几十秒看清一个场景长什么样，再决定要不要花几十分钟正式跑。

    **下单之前先看货。** 把 ``num_rb`` 压到 24、关掉 SSB 测量，传播状态、SIR、
    路损、距离、视距、多普勒与 UE 位置保持不变。总载波功率挤进更少 RB 会使 raw
    SNR 人工升高，raw SINR 在含噪场景也随之变化；工具会先把 SNR 还原到全带口径，
    再与不变 SIR 重算 SINR/IoT。20-ray 内核的已测基准约 1.80×，不是固定 SLA。

    回的是：干扰画像（IoT，多小区才有）、链路预算（SNR/SINR/SIR 分布）、
    几何量（路损、距离、视距比例、多普勒）、测量域导频 SIR（link=BOTH 才有）。

    ``not_available`` 里明确列出探测模式**给不了**的量——谱效、吞吐、时延扩展
    估计、宽带预编码。这些必须跑正式生成，别拿探测结果替代。

    参数
    ----
    preset : 预设名（sr_list_presets 查）。与 config 二选一。
    config : 直接给配置。给了 preset 时作为覆盖项。
    num_samples : 探测样本数。30 看中位数够用，看 5% 分位建议 100 以上。
    """
    from . import scenario as sc

    cfg: dict[str, Any] = {}
    if preset:
        presets = pl.load_presets()
        if preset not in presets:
            return {"error": f"未知预设 {preset!r}", "available": sorted(presets)}
        cfg = dict(presets[preset]["config"])
    dep_notes: list[str] = []
    if config:
        dep_notes = pl._apply_dependent_overrides(cfg, config)
    if not cfg:
        return {"error": "preset 与 config 至少给一个。"}

    out = await anyio.to_thread.run_sync(
        functools.partial(sc.probe, cfg, num_samples=num_samples))
    out["preset"] = preset
    if dep_notes:
        out["dependent_override_notes"] = dep_notes
    return _jsonable(out)


@tool()
async def sr_compare_scenarios(
    presets: list[str],
    num_samples: int = 30,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """并排探测几个场景，回一张对照表。用来在候选场景里选。

    每个场景各跑一次探测（见 ``sr_probe_scenario`` 的口径说明），
    表里给 IoT 中位数与等级、SINR/SNR 中位数、路损中位数、视距比例、单样本耗时。

    典型用法：确认"高干扰"预设确实比"低干扰"对照高出足够的 IoT，
    再拿这两个去跑正式对比——**别在没验证过干扰水平的两批数据上做消融**。
    """
    from . import scenario as sc

    all_presets = pl.load_presets()
    named: dict[str, dict[str, Any]] = {}
    unknown = []
    dep_notes: list[str] = []
    for name in presets:
        if name not in all_presets:
            unknown.append(name)
            continue
        cfg = dict(all_presets[name]["config"])
        if overrides:
            dep_notes.extend(
                f"{name}: {note}"
                for note in pl._apply_dependent_overrides(cfg, overrides))
        named[name] = cfg
    if unknown:
        return {"error": f"未知预设：{unknown}", "available": sorted(all_presets)}
    if not named:
        return {"error": "presets 不能为空。"}

    out = _jsonable(await anyio.to_thread.run_sync(
        functools.partial(sc.compare_probes, named, num_samples=num_samples)))
    if dep_notes:
        out["dependent_override_notes"] = dep_notes
    return out


# ---------------------------------------------------------------------------
# 仿真说明书
# ---------------------------------------------------------------------------


@tool()
async def sr_spec_sheet(
    draft_id: str | None = None,
    dataset_id: str | None = None,
    preset: str | None = None,
    config: dict[str, Any] | None = None,
    title: str = "",
    highlight: list[str] | None = None,
    open_browser: bool = False,
) -> dict[str, Any]:
    """把敲定的仿真配置画成一份说明书（含示意图、可调参），返回可点开的地址。

    **配置定下来之后、正式跑之前调一次**，让用户看清楚这次到底在仿什么：

    * 基站阵列——RF 端口怎么排、每端口驱动几个物理阵子、间距多少、有没有栅瓣
    * 站点拓扑——站点位置、扇区指向、UE 撒点、站间距
    * 频域——多少 RB、怎么分 RBG、子载波间隔与实际占用带宽
    * 时域——TDD 时隙图案
    * 信道剖面——CDL/TDL 的时延功率谱
    * 参数全表——**逐项标注是用户指定的还是系统默认补的**

    画的是**将要跑的那个仿真**，不是配置意图：站数被六边形栅格吸附
    （配 2 站实际 7 站）、阵列走了 legacy 而非本地 1 驱 3 硬件，
    这些差异都按实际画并写进 ``notes``。

    ``sr_generate`` 会自动生成一份（带真实撒点），句柄在 ``summary.spec_sheet``；
    这个工具用于**生成之前**先看一眼。

    **默认不替用户弹窗**，只把 ``url`` 给他，他自己在浏览器或 AI HUB 里点开。
    页面带一个调参面板：改完点「应用到仿真」，改动**直接回到这个 MCP 进程**，
    你随后调 ``sr_await_config`` 就能拿到——不用他复制粘贴。

    所以敲定配置那一步的标准动作是：

        sr_spec_sheet(...)  →  把 url 发给用户，说"点开看一眼；要改就在上面改，
                               改完点应用"  →  sr_await_config()

    ``writeback`` 字段告诉你这次是哪条路：``post`` 表示回传通道通了、
    可以去 ``sr_await_config`` 等；``clipboard`` 表示服务没起来（原因在
    ``serve_error``），得让用户复制粘贴。

    **返回的是地址和摘要，不要把 HTML 内容贴回对话。**
    把 ``headline`` 和 ``notes`` 转述给用户，并把 ``url`` 发给他。

    参数
    ----
    draft_id : sr_plan / sr_revise 的草稿句柄（最常用）
    dataset_id : 已生成的数据集，用它的配置与真实撒点
    preset : 预设名
    config : 直接给配置。与上面几个同时给时，config 作为覆盖项。
    highlight : **本次对话里用户专门提过的参数名**，如
        ``["isd_m", "num_interfering_ues"]``。它们会被顶到首屏关键信息卡最前面
        并高亮。首屏因此既覆盖"做仿真通常最关心的"，也覆盖"这次特别在意的"——
        用户点名过什么就把什么传进来。
    open_browser : 默认 **False**——给地址，不替他弹窗。只有用户明确说
        "帮我打开"时才传 True。
    """
    from . import spec as sp

    cfg: dict[str, Any] = {}
    user_set: list[str] = []
    ue_xy = None

    if dataset_id:
        from . import load as _load

        try:
            ds = _load(dataset_id)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            return {"error": f"加载数据集失败：{exc}"}
        cfg = dict(ds.config)
        try:
            pos = ds.ue_position
            ue_xy = [(float(r[0]), float(r[1])) for r in pos[:400]]
        except Exception:  # noqa: BLE001
            ue_xy = None
    elif draft_id:
        try:
            draft = pl.load_draft(draft_id)
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}
        cfg, _own = pl.resolved_config(draft)
        user_set = list(draft.user_set)
    elif preset:
        presets = pl.load_presets()
        if preset not in presets:
            return {"error": f"未知预设 {preset!r}", "available": sorted(presets)}
        cfg = dict(presets[preset]["config"])
    dep_notes: list[str] = []
    if config:
        dep_notes = pl._apply_dependent_overrides(cfg, config)
        user_set = sorted(set(user_set) | set(config))
    if not cfg:
        return {"error": "需要 draft_id / dataset_id / preset / config 其中之一。"}

    out = await anyio.to_thread.run_sync(functools.partial(
        sp.write_spec,
        cfg, user_set=user_set, dataset_id=dataset_id,
        title=title or "仿真说明书", ue_xy=ue_xy, highlight=highlight,
        open_browser=open_browser,
    ))
    if dep_notes:
        out["dependent_override_notes"] = dep_notes
    if out.get("writeback") == "post":
        out["hint"] = (
            "把 url 发给用户让他自己点开（别替他弹窗），转述 headline 和 notes，"
            "然后告诉他「要调参就在页面上改，改完点『应用到仿真』」，"
            "接着调 sr_await_config 等他。**不要把 HTML 内容或图贴回对话。**"
        )
    else:
        out["hint"] = (
            "把 headline 和 notes 转述给用户，并给出 html_path 让他自己打开；"
            f"回传只能走复制粘贴（{out.get('serve_error')}）。"
            "**不要把 HTML 内容或图贴回对话。**"
        )
    return _jsonable(out)


@tool()
async def sr_await_config(timeout_s: float = 90.0, spec_id: str | None = None) -> dict[str, Any]:
    """等用户在说明书页面上点「应用到仿真」，把他改的参数取回来。

    **紧跟在 ``sr_spec_sheet`` 之后调**（前提是它返回 ``writeback="post"``）。
    用户在页面上拖几下滑块、点一下按钮，改动就到这里了——省掉"复制 → 切窗口
    → 粘贴"三步。

    返回 ``got=0`` **不是错误**，只是这段时间里用户没点。两种处理：

    * 用户还在看 → 再调一次接着等；
    * 用户已经在对话里说话了 → 别再等，按他说的做。

    ``overrides`` 可以直接喂给 ``sr_revise(draft_id, **overrides)`` 或
    ``sr_generate(config=...)``。**拿到后先复述一遍改了什么再动手**，
    用户点错了得有机会喊停。

    参数
    ----
    timeout_s : 最多等多久，默认 90 秒（上限 240）。别设太长，
        MCP 客户端那边也有超时，卡住比等不到更难解释。
    spec_id : 只收某一份说明书的回传。不传就收全部。
    """
    from . import bridge as br

    # threading.Event.wait 是同步阻塞；放线程里等，别把事件循环冻住
    subs = await anyio.to_thread.run_sync(
        functools.partial(br.await_submission, min(float(timeout_s), 240.0), spec_id))
    if not subs:
        return {
            "got": 0,
            "waited_s": round(min(float(timeout_s), 240.0), 1),
            "note": "这段时间里用户没点「应用到仿真」。不是错误——"
                    "他要么还在看，要么已经改用对话说了。",
            "bridge": br.status(),
        }
    # 多次点击以**最后一次**为准：用户改了又改，最后那下才是他的意思。
    last = subs[-1]
    return {
        "got": len(subs),
        "overrides": last.overrides,
        "spec_id": last.spec_id,
        "title": last.title,
        "text": last.text,
        "superseded": [s.overrides for s in subs[:-1]] or None,
        "hint": "先向用户复述这几项改动，再调 sr_revise / sr_generate 落实。"
                "改完记得重新出一份说明书。",
    }


# ---------------------------------------------------------------------------
# 系统级仿真
# ---------------------------------------------------------------------------


@tool()
def sr_system_sim(
    dataset_id: str,
    duration_s: float = 5.0,
    traffic_model: str = "ftp3",
    file_bytes: int = 500_000,
    arrival_rate_hz: float = 2.0,
    small_ue_share: float = 0.5,
    small_file_bytes: int = 1_500,
    small_arrival_rate_hz: float = 20.0,
    small_pdb_ms: float = 20.0,
    large_pdb_ms: float = 300.0,
    packet_size_cdf: str | None = None,
    interarrival_cdf: str | None = None,
    packet_size_scale: float = 1.0,
    interarrival_scale: float = 1.0,
    interarrival_cdf_unit: str = "ms",
    traffic_profiles: list[dict[str, Any]] | None = None,
    target_prb_utilization: float | None = None,
    load_calibration_axis: str = "interarrival",
    load_calibration_tolerance: float = 0.02,
    load_calibration_max_iterations: int = 6,
    load_calibration_replications: int = 2,
    load_calibration_formal_refinements: int = 2,
    scheduler: str = "pf",
    pf_window_tti: int = 100,
    pf_accounting: str = "auto",
    frequency_selective: str = "auto",
    max_layers_per_rbg: int = 4,
    max_logical_prb_per_tti: int | None = None,
    target_bler: float = 0.1,
    harq_combining: str = "ir",
    harq_feedback_delay: bool | str = True,
    rank_mode: str = "fixed",
    fixed_rank: int = 2,
    rank_adaptation_period_tti: int = 1000,
    rank_gain_factor_raise: float = 1.1,
    rank_gain_factor_reduce: float = 1.1,
    rank_switch_rule: str = "unified_ratio",
    rank_se_filter_beta: float = 0.1,
    rank_se_sample_scope: str = "snapshot",
    rank_min_filter_samples: int = 3,
    rank_min_mcs_threshold: int = 9,
    rank_quick_fallback_nack_thld: int = 90,
    rank_quick_fallback_ibler_thld: float = 0.3,
    rank_quick_fallback_se_ratio_thld: float = 1.0,
    rank_max_backoff_times: int = 4,
    rank_probe_enabled: bool = False,
    olla_step_up_db: float = 0.01,
    olla_step_down_db: float | None = None,
    qos_avg_rate_exponent: float = 1.0,
    qos_instant_rate_exponent: float = 1.0,
    qos_delay_exponent: float = 0.0,
    qos_priority_weighting: str = "none",
    edf_mixed_weight: float = 0.5,
    edf_mixed_epf_scale: float = 1.0,
    srb_priority_boost: float = 5000.0,
    edf_starvation_hol_ms: float | None = None,
    mu_enabled: bool = False,
    mu_accounting: str = "pair_table",
    mu_precoder: str = "zf",
    mu_csi_error_variance: float = 0.0,
    mu_corr_threshold: float = 0.7,
    mu_olla_step_up_db: float = 0.01,
    mu_olla_step_down_db: float | None = None,
    small_burst_policy: str = "fractional_slot",
    tdd_pattern: str = "DDDSU",
    neighbor_prb_util: float = 0.3,
    neighbor_load_jitter: float = 0.05,
    csi_aging: bool | str = True,
    srs_period_ms: float = 10.0,
    srs_hopping: bool | str = True,
    srs_resource_allocation: bool | str = True,
    srs_period_adaptive: bool | str = True,
    srs_pci_mod3: int = 0,
    csi_processing_delay_ms: float = 2.0,
    csi_report_period_ms: float = 20.0,
    cqi_filter_lambda: float = 0.25,
    cqi_filter_domain: str = "cqi_index",
    warmup_s: float = 1.0,
    olla_speedup: float = 1.0,
    olla_warmup_speedup: float = 1.0,
    precoder: str = "svd",
    power_constraint: str = "nebf",
    rb_power_control_enabled: bool | str = False,
    rb_power_overrides: list[dict[str, Any]] | str | None = "",
    seed: int = 0,
    num_replications: int = 8,
    replication_workers: int | str = "auto",
    algorithm_label: str = "",
    tti_trace_mode: str = "sampled",
    tti_trace_max_points: int = 256,
    kpi_focus: list[str] | None = None,
    kpi_intent: str = "",
) -> dict[str, Any]:
    """**系统级仿真：连续几秒钟的 TTI，出体验速率等现网 KPI，全部带置信区间。**

    这是和链路级完全不同的一层。链路级问"这个信道能跑多快"，
    系统级问"**这个小区里的用户实际体验到多快**"——把话务到达与结束、
    调度器的多用户取舍与缓冲区排空全算进去。一个用户在一个 TTI 的 grant 视为
    一个独立单码字 TB；最多一次重传，发送 MCS/RBG/rank/TBS 不变。
    默认 IR 以“原 MCS 半谱效对应的等效 MCS”查 NewTx 曲线；CC 以原 MCS、SINR
    +10log10(2) dB 查同一 NewTx 曲线。

    **只有一条评估路径**（``experience_v2``），没有模式开关。KPI 用 28.552
    Rel-19 DRB busy-period：起点是首传、排队等待另报、末段 ACK piece 排除，
    并为单时隙小 burst 提供 TBVol/PaddingVol 的 fractional-slot 口径；另报
    “首包时延”（每个 arrival object 从生成到首次调度）与“含头速率”（相同
    payload/去尾规则，只把首包时延加回分母）。

    **“容量仿真”是这条路径的一个话务配置**：``traffic_model="full_buffer"``。
    缓冲区永不空 ⇒ 按需 RBG 反查恒等于全带宽、每 TTI 一个 SU（或一对 MU），
    这就是容量口径。调度、AMC、HARQ、解调 SINR 聚合全部照体验模式的定义走，
    没有任何为它开的特例。代价是 busy period 永不结束，**28.552 的体验速率在
    这个配置下无边界可用、如实报 ``None``**。**用户体验速率仍然有定义**，走
    ITU-R M.2412 / TR 38.913 口径：``ue_served_p5_mbps`` 是 cell-edge user
    throughput（每 UE 已服务净荷 ÷ 观测窗长的 5% 分位）。小区总吞吐看
    ``cell_served_mbps`` 与 ``serving_cell_prb_utilization``。

    **``mu_accounting`` 决定 MU 的代价怎么记账**：``pair_table``（唯一支持的
    口径）MCS 从 pair 表的 ``CorrLoss + powerLoss`` 平移出来、TBS 按该 MCS 全带
    算、**误块抽签用 pair 的真实 SINR**（ZF 权按基站可能已老化的 CSI 打，但打在
    双方 ``h_true`` 上，对方的流进干扰协方差）。历史的 ``se_ratio_legacy``
    （只把 TBS 乘一个标量、误块抽签仍按 SU）已随 legacy 容量路径一起下线。

    ``mu_precoder`` 可选 ``zf`` 或 ``rzf``。RZF 的
    ``mu_csi_error_variance`` 是每个复信道系数的估计误差方差，加载项为
    ``N_BS·sigma_e²``；它应来自估计器协方差或离线标定，不能在运行时逐快照
    偷看 ``h_true``。默认 ``zf`` / ``0.0`` 保持旧结果。

    **Rank 是显式策略，默认固定 rank2。** ``rank_mode='fixed'``（默认，配
    ``fixed_rank``）是现网基线；链路表里的逐快照 ``best_rank`` 是瞬时谱效最优
    值，每 5 ms 就可能变，直接拿它发送会让链路自适应收敛不了。
    ``rank_mode='adaptive'`` 按用户 2026-09-02 给的现场规格实现，常数不再是
    工程猜测：每 ``rank_adaptation_period_tti``（默认 1000）判决一次，且要先
    攒够 ``rank_min_filter_samples``（默认 3）个谱效滤波样本；**升 rank 要求
    最优 rank 的滤波谱效超过当前 rank 的 ``rank_gain_factor_raise`` 倍**
    （默认 1.1，即高 10%）。谱效滤波是 ``beta`` 一阶 IIR
    （``rank_se_filter_beta`` 默认 0.1）；预估 MCS 低于
    ``rank_min_mcs_threshold``（默认 9）的 rank 谱效直接置 0；各 rank 再乘一个
    DMRS 开销系数。

    **默认升/降 rank 使用同一条对称判据**（负责人 2026-09-03 裁决）：
    ``rank_switch_rule='unified_ratio'``，且两个 gain factor 都是 1.1；只有最优
    rank 的滤波谱效严格超过当前 rank 10% 才切换。``spec_asymmetric`` 只保留作
    显式反向对照，不再是 MCP 或页面默认。

    **升 rank 之后进入快速回退监测**，窗内实时判：新增 NACK 超过
    ``rank_quick_fallback_nack_thld``（默认 90）立即回退；窗口结束时初传 BLER
    ≥ ``rank_quick_fallback_ibler_thld``（默认 0.3）或新旧 rank 实测谱效比低于
    ``rank_quick_fallback_se_ratio_thld``（默认 1.0）也回退。回退会把 rank 与
    OLLA 偏置一起退回，并让判决周期指数退避 ``×2^n``（n 上限
    ``rank_max_backoff_times``，默认 4 → 最长 16000 TTI）。

    ``rank_se_sample_scope`` 是 SuperRAN 侧显式的口径选择：现场每 TTI 累积一个
    谱效样本，而这里的 AMC 坐标在一个信道快照内是常数，逐 TTI 采样会让
    ``beta=0.1`` 的平滑在快照之间完全失效。默认 ``snapshot``（一次新观测算一个
    样本），设成 ``tti`` 复现现场节拍。**两者不等价，随结果一起报。**

    ``rank_mode='link_table'`` 是逐快照跟随的历史行为，**只作反向对照**。

    **ACK/NACK 要等上行时隙。** ``harq_feedback_delay=True``（默认）下，TB 在
    D/S 发出、反馈搭其后第一个 U 回传，OLLA 更新与重传资格从该 U 之后第一个
    D/S 起生效；``DDDSU`` 在 30 kHz 下逐相位偏移 5/4/3/2 个 TTI。**重传还要
    额外等到同类型时隙**（S 上发的 TB 要等下一个 S），两个约束取交集。等待期间
    首传 ACK 与 NACK 都占住该 UE 的单 HARQ 进程：反馈前不能更新 OLLA/rank，也不能
    发新 TB（计入 ``harq_feedback_wait_skips``）。设成
    False 是零时延反向对照；图案里没有 U 时自动退化并写进 ``notes``。
    k1/k2、PUCCH 资源与并行 HARQ 进程都不建模。

    **CQI 的长期滤波是一阶 IIR**：``s <- s + λ(x - s)``。``cqi_filter_lambda``
    默认 0.25，**已由负责人确认为当前工程默认，但尚未经现场测量/设备数据
    标定**；``cqi_filter_domain`` 默认在量化后的 CQI 档上。两者都随结果上报；
    ``λ=1`` 关闭滤波可作反向对照。不得把工程默认表述成现场等价。

    ``target_bler`` 可配，但它在**开环上大部分抵消**（同一个目标同时出现在
    CQI→门限 与 门限→MCS 两侧）：实测 384 个样本里 92% 选出完全相同的 MCS。
    真正吃到它的是 OLLA 闭环——10%→30% 实测稳态偏置 1.65→1.85 档、首传 BLER
    0.067→0.178。取值必须落在预置曲线的共同实测区间 [0.001, 0.998]。

    返回里 **cell 是小区级、users 是用户级**，两级都有：平均调度 MCS、
    平均 rank、首传 BLER、残留 BLER、体验速率、含头速率与首包时延。``avg_mcs`` 的分母含重传（重传重放冻结的旧档），要看链路自适应视角用 ``avg_mcs_first_tx``。cell 还给
    本小区 PRB 利用率、0..17 RBG 的逐 TTI 占用分布和 MU 配对 PRB 比例。

    **每个 KPI 都是 ``{mean, std, ci95, n_rep, cv, rel_half_width}`` 而不是一个裸数**
    ——同一批信道、同一套配置只改种子，实测 ``cell_experienced_mbps`` 的变异系数
    有 **11.4%**（``measurements/seed_variance.json``），单次运行报出来的
    "142.3 Mbps" 小数点后那位是假的。念数字前先看 ``rel_half_width``：
    比它小的差异这次实验分辨不出来。

    ``notes`` 会主动报出让结论不成立的情况：队列积压未收敛、burst 样本太少、
    信道快照不足（时间起伏被低估，PF 拿不到多用户分集）、字节对不上账、
    置信区间过宽。**这些必须转述给用户，别只报好看的数字。**

    参数
    ----
    dataset_id : 已生成的数据集。每个 UE 建议至少 8 个时间快照。
        在 ChannelHub 修复多时隙 SIR/SINR 聚合前，优先用
        ``num_slots_per_sample=1`` 且 ``num_samples/num_ues>=8``，既保留时间序列，
        又避免门 1 的 IoT 自洽性失败。
    duration_s : 仿真时长，3~20 秒。逐 TTI 的 FIFO 与 RBG 分配是主要开销。
    traffic_model : ``ftp3``（3GPP FTP Model 3，评价体验速率的标准话务）/
        ``cdf``（两份 value,cdf 文件驱动包大小与包间隔 renewal process）/
        ``mixed``（推荐：大小 UE 混跑，包长与到达率外生定义）/
        ``full_buffer``（**这就是"容量仿真"**：话务开到最大、缓冲区永不空，
        按需 RBG 退化成全带宽。28.552 的 busy-period 吞吐报 ``None``，
        用户体验速率走 ITU 口径 ``ue_served_p5_mbps``）/ ``cbr``
    arrival_rate_hz : 每用户每秒到达几个文件。控制负载——太高会积压，
        ``notes`` 会拦。
    packet_size_cdf / interarrival_cdf : UTF-8 两列经验 CDF，cdf 支持 0..1 或
        0..100；相对路径固定从项目根解析。包间隔存在 CDF 时，它取代
        ``arrival_rate_hz`` 作为该 profile 的到达时钟。
    packet_size_scale / interarrival_scale : 全局双标量，并与 profile 局部标量
        相乘。业务量一阶近似正比于 size/interval；二者都必须为正数。
    traffic_profiles : 多业务模型数组。每项使用 TrafficClassConfig 字段，可通过
        ``ue_ids`` 显式绑定视频/XR 用户；未绑定 UE 按 ``ue_share`` 分配。profile
        自己的 CDF 为空时继承全局 CDF。
    target_prb_utilization : 可选的本小区实测 PRB 利用率目标，例如 0.30。
        它触发话务校准，不直接改结果；最终仍另跑正式重复实验并报告实测值/区间。
    load_calibration_axis : 默认 ``interarrival``，只调包间隔、保留包长分布；
        也可选 ``packet_size`` 或 ``balanced``。
    load_calibration_formal_refinements : probe 后首轮正式均值仍未达标时，最多再用
        正式 ``num_replications`` 反馈校正几轮；默认 2，完整轨迹会返回。
    pf_accounting : ``auto`` 解析成实际 scheduled TBS。``acked_goodput`` 与
        ``legacy_fullband`` 只供研究，不是默认 PF 口径。
    frequency_selective : ``auto`` 在逐 RBG 字段完整时启用，``on`` 缺字段硬失败，
        ``off`` 是宽带/顺序 RBG 基线。它与 RB 功控开关相互独立。
    max_layers_per_rbg / max_logical_prb_per_tti : P0 资源账本的空间层和逻辑
        layer-PRB 预算。默认每 RBG 4 层、逻辑预算取 272×4；PDCCH/CCE 暂不建模。
    target_bler : MCS 选择与 SU/MU OLLA 共用的目标 IBLER。未显式给 down 步长时，
        按 ``s_down=s_up*(1-target)/target`` 自动反解，防止链路表按 20% 选 MCS、
        主循环却仍按 10% OLLA 记账。
    harq_combining : ``ir``（默认，增量冗余的半谱效等效 MCS）或 ``cc``
        （追逐合并，码字 SINR +3.0103 dB）。每个 TB 最多重传一次；等效 MCS
        只用于 BLER 查表，实际重传 MCS 与 RBG 数保持和初传一致。
    scheduler : ``pf``（默认）/ ``qos_pf`` / ``rr`` / ``max_ci`` / ``edf`` /
        ``qos_pf_edf``。后两个是包长感知：``edf`` 用 ``TBS/Buffer``，优先调度
        最快能传完的用户，**牺牲长期公平性换小包时延**；``qos_pf_edf`` 是它与
        ``qos_pf`` 的 蓝本原式加权混合。两者都需要有限队列，
        搭 ``full_buffer`` 会硬失败——容量口径请用 ``pf`` / ``max_ci``。
    edf_mixed_weight : ``qos_pf_edf`` 里 EDF 的权重 w ∈ [0,1]。0 严格退化成
        ``qos_pf``，1 严格退化成 ``edf``。
    edf_mixed_epf_scale : 蓝本的 ``thp_filter`` 配平系数，默认 1.0。两个
        分量不同量纲，它没标定时中间的 w 会被量级差吞掉；结果里的
        ``cell.scheduler_mixed_component_scale`` 报出实测量级与实际占比。
    edf_starvation_hol_ms : EDF / 混合模式的**时延兜底**门限（ms）。队首等待达到
        它的用户无条件排到最前，组内按等待降序。默认 ``None``（关闭）：EDF 的
        分母是积压，越饿分母越大、优先级越低，与 PF 的 ``r_avg`` 越饿越小刚好
        相反，所以纯 EDF 在饱和下必然饿死一部分大包用户，靠算法自身不会恢复。
    srb_priority_boost : SRB 绝对优先加值，默认 5000。**SuperRAN 不建模逻辑
        信道**，只有显式声明 ``resource_type="signalling"`` 的业务类才触发；
        不声明就永远不触发，不会凭空造出信令话务。
    qos_* : ``qos_pf`` 的显式参数化形式
        ``w(priority) * R_inst^beta / R_avg^alpha * delay^gamma``。默认
        alpha=beta=1、gamma=0、w=1，严格退化成经典 PF；它不是未确认定义的 EPF。
    small_burst_policy : experience_v2 默认 ``fractional_slot``，按 28.552 Rel-19
        的 TB volume / padding volume 折算单时隙小 burst；``exclude`` 保留旧式盲区。
    mu_enabled : 是否允许 MU 配对。默认关，先看清 SU 基线。
    mu_corr_threshold : MU SUS 配对的归一化相关性上限，默认 0.7。
    olla_step_up_db / olla_step_down_db : 历史参数名；值是连续 MCS 档位步长，
        在 SINR 反折 MCS 之后叠加。down 省略时按 target_bler 反解。
    mu_olla_step_up_db / mu_olla_step_down_db : MU 专属用户级 MCS-domain OLLA；
        历史 ``*_db`` 名仅为兼容保留，状态按用户维护，不按配对关系拆分。
    neighbor_prb_util : **邻区 PRB 利用率**，默认 0.3。ChannelHub 的几何 SINR
        是按所有邻区都在发算的（等于 100%），真实网络 5G 典型是 10%/30%/50%。
        按 full buffer 算会把干扰放大到不真实的程度。1.0 退化成原行为。
        **当前只支持全网统一值**——几何 SIR 是聚合量，拿不到逐邻区贡献。
        它是干扰侧的输入，不是结果里的 ``serving_cell_prb_utilization``；后者由本小区
        话务、撒点、调度与链路共同形成。要做 10%/30%/50% 负载场景应校准话务使实测值
        落入目标附近，不能把目标数直接回填成结果。
    neighbor_load_jitter : 实际生效负载在配置值 ±这个比例内逐快照波动，默认 0.05。
        恒定负载会让所有快照的干扰完全一样，结果比现网干净。
    csi_aging : **是否建模 CSI 反馈时延与老化**，默认开。关掉退化成零时延完美 CSI
        ——那是个上界不是现网，MU 增益会被系统性高估。
    srs_period_ms : 固定 100 MHz 系统的最短候选 SRS 周期，资源分配开启时只接受
        10 / 20 / 40 ms。默认从10 ms开始；资源不足时全局升到20/40 ms。
        5 ms仅保留在不启用该资源表的链路级老化接口中。
    srs_hopping : SRS 跳频。默认开，对应 38.211 Table 6.4.1.4.3-1 的 C_SRS=63
        （每跳 16 RB = 1 个 RBG，**17 跳**扫完 272 RB）。
        **这是老化的主导项**：10 ms 周期下全带扫一遍要 170 ms。
    srs_resource_allocation : 为每个2T4R UE分配相邻两个SRS机会、4-CS中的2-CS块
        与17个频域相位；BBL叶子排除且只能使用本PCI模3颜色。默认开。
    srs_period_adaptive : 默认开，从 ``srs_period_ms`` 起选择全局最短可容纳周期；
        关闭后资源不足直接失败，不跨颜色借资源。
    srs_pci_mod3 : 当前服务小区的 PCI 模 3 颜色，取 0/1/2；系统结果仍是单小区。
    csi_processing_delay_ms : 信道估计 + 预编码计算 + 调度下发的固定时延。
    csi_report_period_ms : 宽带 CQI/PMI 报告周期，默认 20 ms。它与 5 ms 的信道
        快照间隔、SRS 周期是三个不同量；38.331 按 slot 配置，并未规定 PMI 固定 5 ms。
    warmup_s : 预启动时长，默认 1 s。PF/OLLA/SRS 继续演进，体验与 BLER/资源 KPI
        从该时刻后才统计；5 s 仿真默认统计后 4 s。
    olla_speedup : OLLA 两个步长的**等比**放大系数，默认 1.0；目标为 10% 且
        up=0.01 时，自动反解的基础 down=0.09（现网口头 +0.01/−0.1 对应 9.09%）。
        稳态 BLER = up/(up+down) 与它无关，放大只加快收敛、加大稳态抖动。
        短仿真里基线常常压不动一档 MCS，可临时设 10；**出正式结论设回 1.0**。
        非 1.0 时结果里会带一条显式告警。
    olla_warmup_speedup : 只在 ``warmup_s`` 预启动窗口内生效的等比放大系数；
        进入 KPI 窗口后恢复 ``olla_speedup``。短仿真需要预收敛时优先用它，
        且结果会显式标注，不能冒充全程使用现场步长。
    precoder : **实际发射权**。``svd`` 在系统建表的单快照上逐 RBG 做 SVD
        （默认；不是跨时隙 Shannon 容量上界）；``type1`` 用 Type-I-style
        单面板宽带列码本近似当发射权，多层采用增量贪心而非完整矩阵码本枚举。
        码本自由度少，**在 CSI 老化下反而可能更耐受**——能算错的地方也少。
        ``type1`` 时 BF Gain 恒为 0（发射权就是 CQI 的参照权）。
    power_constraint : ``nebf``（默认）逐天线强制 P/M、用满总功率但可能破坏 MU
        零陷；``ebf`` 是总功率约束；``pebf`` 对 EBF 权做全局缩放、受最大发射
        天线限制而满足每天线 P/M。
    rb_power_control_enabled : 默认关闭，关闭时每个 RB 都是 1x。开启后按
        ``rb_power_overrides`` 连续调节，并对每个小区强制 ``sum(q)=N_RB``。
    rb_power_overrides : JSON/对象数组。每项用 ``cell_index``（整数或 ``all``）、
        ``rb`` 或闭区间 ``rb_start/rb_end``、``multiplier``；最终倍率必须在
        0.1x..4x。未指定 RB 自动等功率补偿；若补偿不可行则硬失败。功控使用
        ChannelHub 保存的逐 slot/逐小区干扰项重算
        ``q_serving*S/(N+sum(q_interferer*I_cell))``，不会缩放聚合 SINR 冒充。
    seed : 实验批次的**主种子**（对应 ns-3 的 ``RngSeed``）。重复实验**不要**
        靠改它——改它等于换一整个宇宙，两批之间没有任何"流不重叠"的保证。
    num_replications : 独立重复次数（对应 ns-3 的 ``RngRun``），默认 **8**。
        **这个默认值是算出来的，不是拍的**：

        * n ≤ 5 时判决检验（Wilcoxon 符号秩）最小可达 p 是 ``2/2^n`` > 0.05，
          **无论数据多干净都不可能宣告显著**——而它照样会算出漂亮的百分比。
          n=6 是硬下界（p_min=0.031），8 留了余量（p_min=0.0078）。
        * 代价不大：``build_link_tables`` 与随机种子无关，**只建一次表**，
          重复的只是 TTI 主循环。代价比例是
          ``(n−1)·T_loop / (T_build + T_loop)``，**随数据集大小变**——
          实测建表 5.1 s / 主循环 1.0 s 时 8 次是 +113%，
          建表 10.5 s / 主循环 1.1 s 时是 +67%。
        * **收窄比 1/√n 快。** 区间半宽 = ``t(0.975,n−1)·σ/√n``，
          t 因子本身也在缩（n=4 时 3.18、n=16 时 2.13），所以从 n=4 到 n=16
          实测是 **0.36 倍**而不是 1/√n 的 0.5 倍。
          写成"按 1/√n"会低估多跑几次的收益。
          实测半宽/均值（从 64 次重复里重抽，见 ``measurements/rng_replication.json``）：
          n=4 时 13.6%、**n=8 时 7.5%**、n=16 时 4.9%、n=32 时 3.4%。
          8 是拐点——再翻倍只多拿 2.6 个百分点，却要多一倍墙钟。

        设成 1 会退回"单次运行、无区间"，并在 ``notes`` 里明确告警。
    replication_workers : 重复实验的进程数，默认 ``auto``。短任务保持串行；
        工作量足以覆盖 Windows spawn 与链路表序列化成本时自动用最多 4 个进程。
        也可显式设 1/2/4/8；结果 ``parallel`` 会返回实际进程数、阈值与降级原因。
        线程后端不提供，因为 TTI Python 事件循环实测 4 线程反而更慢。
    algorithm_label : 本次算法臂的可读名称。多算法 KPI 工作台用它固定颜色、图例与
        基线差值；为空时从 scheduler/MU/precoder 自动生成，不影响仿真数值。
    tti_trace_mode : ``sampled``（默认，等间隔锚点 + MU/NACK/重传等关键事件）、
        ``full``（测量窗全部 DL TTI）或 ``off``。单 TTI 轨迹只用于解释机制，
        不能替代跨 replication 的 Gate 3 判决。
    tti_trace_max_points : sampled 模式的每次重复轨迹上限，默认 256；约一半保留给
        跨算法严格对齐的均匀锚点，另一半保留关键事件。
    kpi_focus : 调用本工具的 Agent/LLM 依据用户问题显式传入的 KPI key 或关注词。
        页面会保存选择来源和理由、优先展示相关 KPI，其余折叠；不在仿真库内部
        暗调另一个模型。
    kpi_intent : 用户问题的短文本摘要；未传 kpi_focus 时用于可审计的关键词推断。
    """
    # 局部 import：本函数用得到 time / rng，但顶层已经很挤，
    # 而且这个模块里就是这么写的（见下面 `_load` / `sysm`）。
    import time  # noqa: PLC0415

    from . import load as _load  # noqa: PLC0415
    from . import power_control as pc  # noqa: PLC0415
    from . import rng  # noqa: PLC0415
    from . import system as sysm  # noqa: PLC0415

    ds = _load(dataset_id)
    if traffic_profiles is not None and not isinstance(traffic_profiles, list):
        return {"error": "traffic_profiles 必须是对象数组"}
    if traffic_profiles and str(traffic_model) not in ("mixed", "cdf"):
        return {"error": "traffic_profiles 只允许与 traffic_model='mixed'/'cdf' 搭配"}
    if kpi_focus is not None and (
            not isinstance(kpi_focus, list)
            or not all(isinstance(item, str) and item.strip() for item in kpi_focus)):
        return {"error": "kpi_focus 必须是非空字符串数组"}
    try:
        h = ds.h_true
        h_est = ds.h_est
        sinr = np.asarray(ds.scalar("sinr_dB"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"取不到信道或 sinr_dB：{exc}"}

    h_users = [np.asarray(h[i]) for i in range(h.shape[0])]
    h_est_users = [np.asarray(h_est[i]) for i in range(h_est.shape[0])]
    # **样本数不是用户数。** 数据集里 num_samples 个样本分布在 num_ues 个
    # UE 位置上；不按 UE 合并的话小区里会多出好几倍的人，
    # 每用户谱效被摊薄（实测 40 样本/10 UE 时从 0.32 掉到 0.08）。
    n_ue = int(ds.config.get("num_ues") or 0) or None
    # 样本→UE 的轮转布局必须与落盘的 ue_id 一致。SINR 拒绝采样等路径会破坏
    # 它（ue_id 按 attempted_index 合成，接受后的序号不再轮转），继续按
    # 轮转分组会把不同 UE 混进同一"用户"、把同一 UE 拆成多个——静默无报错。
    if n_ue is not None:
        try:
            _ue_raw = np.asarray(ds.scalar("ue_id")).ravel()
        except (KeyError, OSError, ValueError) as exc:
            if int(n_ue) > 1:
                return {"error": (
                    "多 UE 系统仿真缺少可核验的 ue_id；不能静默假设样本按 UE 轮转。"
                    f"请用当前生成器重建数据集，或补齐逐样本 ue_id（{type(exc).__name__}）。")}
            _ue_raw = np.zeros(int(h.shape[0]), dtype=int)
        if (_ue_raw.size != int(h.shape[0])
                or not np.issubdtype(_ue_raw.dtype, np.number)
                or not np.all(np.isfinite(_ue_raw.astype(float)))
                or not np.allclose(
                    _ue_raw.astype(float), np.round(_ue_raw.astype(float)),
                    rtol=0.0, atol=1e-9)):
            return {"error": (
                "逐样本 ue_id 必须与信道样本等长，并且全部是有限整数；"
                f"收到 {int(_ue_raw.size)} 个标签、{int(h.shape[0])} 个样本。")}
        _ue_ids = np.round(_ue_raw.astype(float)).astype(int)
        _rotation = np.arange(int(h.shape[0])) % int(n_ue)
        if not np.array_equal(_ue_ids, _rotation):
            return {"error": (
                "数据集的 ue_id 与样本轮转布局不一致（常见于 SINR 拒绝采样："
                "ue_id 按 attempted_index 合成，接受后序号不再轮转）。"
                "按轮转分组会静默混 UE；请关闭筛选重新生成，"
                "或先按 ue_id 归并样本。")}
    configured_cells = int(
        ds.summary.get("cells_configured")
        or (int(ds.config.get("num_sites", 1) or 1)
            * int(ds.config.get("sectors_per_site", 1) or 1)))
    serving_identity_inferred = False
    try:
        _serving_raw = np.asarray(ds.scalar("serving_cell_index")).ravel()
    except (KeyError, OSError, ValueError) as exc:
        if configured_cells > 1:
            return {"error": (
                "当前 SystemResult 是单小区资源池，但多小区数据集缺少逐样本 "
                "serving_cell_index；不能静默把不同小区 UE 合并。"
                f"请用当前生成器重建数据集（{type(exc).__name__}）。")}
        _serving_raw = np.zeros(int(h.shape[0]), dtype=int)
        serving_identity_inferred = True
    if (_serving_raw.size != int(h.shape[0])
            or not np.issubdtype(_serving_raw.dtype, np.number)
            or not np.all(np.isfinite(_serving_raw.astype(float)))
            or not np.allclose(
                _serving_raw.astype(float), np.round(_serving_raw.astype(float)),
                rtol=0.0, atol=1e-9)):
        return {"error": (
            "逐样本 serving_cell_index 必须与信道样本等长，并且全部是有限整数；"
            f"收到 {int(_serving_raw.size)} 个标签、{int(h.shape[0])} 个样本。")}
    _serving_ids = np.round(_serving_raw.astype(float)).astype(int)
    serving_groups = sysm.group_samples_by_ue(
        int(h.shape[0]), int(n_ue) if n_ue is not None else int(h.shape[0]))
    serving_cell_ids_by_ue: list[int] = []
    for ue, indices in enumerate(serving_groups):
        cells = sorted({int(_serving_ids[index]) for index in indices})
        if len(cells) != 1:
            return {"error": (
                f"UE {ue} 的时间快照跨越多个 serving cell {cells}；"
                "当前系统仿真没有实现切换，拒绝混表。")}
        serving_cell_ids_by_ue.append(int(cells[0]))
    distinct_serving_cells = sorted(set(serving_cell_ids_by_ue))
    if len(distinct_serving_cells) != 1:
        return {"error": (
            "当前 SystemResult 是单小区调度结果，不能把不同 serving cell 的 UE "
            f"放进同一 272-RB 资源池（实得 {distinct_serving_cells}）。"
            "请按 serving cell 筛选后分别运行；联合调度属于下一阶段。")}
    try:
        sir = [float(x) for x in np.asarray(ds.scalar("sir_dB"))]
    except Exception:  # noqa: BLE001
        sir = None
    # **快照间隔由配置算出来，不能拍脑袋。** ChannelHub 的多时隙输出是连续的
    # SRS/CSI-RS 机会（默认 5 ms），不是连续 TTI——当成 TTI 会让所有时间相关的
    # 结论差 10 倍，见 CLAUDE.md「多时隙的快照间隔是 5 ms」。
    try:
        snap_ms = sysm.snapshot_interval_ms(ds.config)
    except (TypeError, ValueError) as exc:
        return {"error": f"快照间隔解析失败（旧数据集字段可能不规范）：{exc}"}
    try:
        _speed_raw = ds.config.get("ue_speed_kmh", 3.0)
        ue_speed_kmh = 3.0 if _speed_raw is None else float(_speed_raw)
    except (TypeError, ValueError) as exc:
        return {"error": f"ue_speed_kmh 必须是有限非负数：{exc}"}
    if not np.isfinite(ue_speed_kmh) or ue_speed_kmh < 0:
        return {"error": "ue_speed_kmh 必须是有限非负数"}
    # **TDD 系统载波是产品合同，不是调参项。** 信道张量必须实际为
    # 272 RB，配置标签也必须是 100 MHz / 30 kHz。不符时拒绝运行，既不把
    # 51 RB 假当 272 RB，也不在系统层临时发明 7-RBG 口径。
    try:
        carrier = _carrier_grid(ds.config, num_rb=int(h.shape[2]))
    except ValueError as exc:
        return {"error": f"TDD 系统载波不符合固定口径：{exc}"}
    # 说明书页面上的开关是 select，回传的是 "on"/"off" 字符串；
    # 直接 bool() 的话 "off" 是**真值**，开关会失灵而且完全无声。
    def _flag(v: Any) -> bool:
        return v.strip().lower() not in ("off", "false", "0", "no", "") \
            if isinstance(v, str) else bool(v)

    if (isinstance(srs_pci_mod3, bool)
            or not isinstance(srs_pci_mod3, (int, np.integer))
            or int(srs_pci_mod3) not in (0, 1, 2)):
        return {"error": "srs_pci_mod3 必须是整数 0 / 1 / 2"}

    try:
        power_cfg = pc.RbPowerControlConfig.from_raw(
            enabled=rb_power_control_enabled, num_rb=int(h.shape[2]),
            overrides=rb_power_overrides)
        power_geometry = pc.geometry_from_dataset(ds) if power_cfg.enabled else None
        resolved_power_profiles = (
            power_cfg.resolve_profiles(power_geometry.num_cells)
            if power_geometry is not None else None)
    except ValueError as exc:
        return {"error": str(exc)}
    if power_geometry is not None:
        _serving_cells = sorted({
            int(x) for x in np.asarray(power_geometry.serving_cell_index).ravel()
        })
        if len(_serving_cells) != 1:
            return {"error": (
                "RB 功控的当前 SystemResult 是单小区调度结果，数据集却包含多个 "
                f"serving cell {_serving_cells}。不能把独立小区的 RBG 当成一个互斥"
                "资源池；请生成/筛选同一服务小区的 UE。跨小区联合调度属于下一阶段。")}
    try:
        csi_cfg = sysm.ca.CsiConfig(
            enabled=_flag(csi_aging), srs_period_ms=float(srs_period_ms),
            hopping=_flag(srs_hopping),
            srs_resource_allocation=_flag(srs_resource_allocation),
            srs_period_adaptive=_flag(srs_period_adaptive),
            processing_delay_ms=float(csi_processing_delay_ms),
            csi_report_period_ms=float(csi_report_period_ms),
            cqi_filter_lambda=float(cqi_filter_lambda),
            cqi_filter_domain=str(cqi_filter_domain),
            periodic_trace_history=float(warmup_s) > 0)
    except ValueError as exc:
        return {"error": str(exc)}
    # **h_est 的物理来源必须与 SRS 语义一致。** 系统仿真把 h_est 当基站侧
    # SRS 预编码 CSI（CSI 老化模型的物理语义就是"SRS 探到的信道"）。
    # 比较门已对 csi='srs' 硬校验 provenance，这条更常用的主链路同等对待：
    # 来源不是 ul_srs_estimate 时，开老化硬失败、不开老化显式告警进 notes。
    _pre_notes: list[str] = []
    if serving_identity_inferred:
        _pre_notes.append(
            "**serving-cell 身份由单小区配置推断**：数据集未落逐样本 "
            "serving_cell_index，但配置只含一个小区，按 cell 0 处理。")
    _csi_src = [str(x) for x in ds.precoding_csi_sources]
    if _csi_src and any(x != "ul_srs_estimate" for x in _csi_src):
        _src_kinds = sorted(set(_csi_src))
        _src_msg = (
            f"数据集的预编码 CSI 来源是 {_src_kinds}，不是 ul_srs_estimate；"
            "系统仿真把 h_est 当 SRS 用，来源不符时结果在物理上不可解释。")
        if csi_cfg.enabled:
            return {"error": _src_msg + (
                " CSI 老化已开启，禁止继续；请用 SRS 来源的数据集重新生成，"
                "或明确关闭 csi_aging（结果会带来源告警）。")}
        _pre_notes.append("**预编码 CSI 来源不符**：" + _src_msg)
    # **邻区负载抖动走它自己的随机流**，不是 `seed + 909`。
    # `master + 常数` 正是 NumPy 并行随机数文档点名的反模式（"UNSAFE! Do not do
    # this!"）：换一次 master 就可能和别的流撞上，而撞上之后两条流是**逐位相同**
    # 的，不是"相关"——这种复用在结果里完全看不出来。见 rng.py 的模块文档。
    # 两个消费者各拿同一命名流的全新 generator，才能逐快照对齐；把建表已经
    # 消费过的同一个对象继续传给 MU 增益，会让第二段从随机序列中途开始。
    try:
        load_book = rng.RngBook(master_seed=seed)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        resolved_su_down = (
            sysm.olla_step_down_for(float(target_bler), float(olla_step_up_db))
            if olla_step_down_db is None else float(olla_step_down_db)
        )
        resolved_mu_down = (
            sysm.olla_step_down_for(float(target_bler), float(mu_olla_step_up_db))
            if mu_olla_step_down_db is None else float(mu_olla_step_down_db)
        )
        profile_cfg = tuple(
            sysm.TrafficClassConfig.from_dict(item)
            for item in (traffic_profiles or []))
        traffic_cfg = sysm.TrafficConfig(
            model=str(traffic_model), file_bytes=file_bytes,
            arrival_rate_hz=float(arrival_rate_hz),
            small_ue_share=float(small_ue_share),
            small_file_bytes=small_file_bytes,
            small_arrival_rate_hz=float(small_arrival_rate_hz),
            small_pdb_ms=float(small_pdb_ms), large_pdb_ms=float(large_pdb_ms),
            packet_size_cdf=packet_size_cdf,
            interarrival_cdf=interarrival_cdf,
            packet_size_scale=float(packet_size_scale),
            interarrival_scale=float(interarrival_scale),
            interarrival_cdf_unit=str(interarrival_cdf_unit),
            classes=profile_cfg)
        rank_cfg = sysm.ap.RankConfig(
            mode=str(rank_mode), fixed_rank=int(fixed_rank),
            period_tti=int(rank_adaptation_period_tti),
            min_filter_samples=int(rank_min_filter_samples),
            gain_factor_raise=float(rank_gain_factor_raise),
            gain_factor_reduce=float(rank_gain_factor_reduce),
            switch_rule=str(rank_switch_rule),
            se_filter_beta=float(rank_se_filter_beta),
            se_sample_scope=str(rank_se_sample_scope),
            min_mcs_threshold=int(rank_min_mcs_threshold),
            quick_fallback_nack_thld=int(rank_quick_fallback_nack_thld),
            quick_fallback_ibler_thld=float(rank_quick_fallback_ibler_thld),
            quick_fallback_se_ratio_thld=float(
                rank_quick_fallback_se_ratio_thld),
            max_backoff_times=int(rank_max_backoff_times),
            probe_enabled=_flag(rank_probe_enabled))
        system_cfg = sysm.SystemConfig(
            duration_s=float(duration_s),
            tdd_pattern=tdd_pattern, harq_combining=str(harq_combining),
            harq_feedback_delay=_flag(harq_feedback_delay),
            seed=seed, snapshot_update_ms=snap_ms,
            power_constraint=str(power_constraint), rb_power_control=power_cfg,
            scs_khz=carrier["scs_khz"],
            num_rbg=carrier["num_rbg"], rb_per_rbg=carrier["rb_per_rbg"],
            rbg_prb_sizes=tuple(int(x) for x in carrier["rbg_prb_sizes"]))
        scheduler_cfg = sysm.SchedulerConfig(
            algorithm=scheduler, pf_window_tti=pf_window_tti,
            rank=rank_cfg,
            pf_accounting=pf_accounting,
            frequency_selective=str(frequency_selective),
            max_layers_per_rbg=max_layers_per_rbg,
            max_logical_prb_per_tti=max_logical_prb_per_tti,
            olla_step_up_db=float(olla_step_up_db),
            olla_step_down_db=resolved_su_down,
            qos_avg_rate_exponent=float(qos_avg_rate_exponent),
            qos_instant_rate_exponent=float(qos_instant_rate_exponent),
            qos_delay_exponent=float(qos_delay_exponent),
            qos_priority_weighting=str(qos_priority_weighting),
            edf_mixed_weight=float(edf_mixed_weight),
            edf_mixed_epf_scale=float(edf_mixed_epf_scale),
            srb_priority_boost=float(srb_priority_boost),
            edf_starvation_hol_ms=(
                None if edf_starvation_hol_ms is None
                else float(edf_starvation_hol_ms)),
            mu_enabled=_flag(mu_enabled),
            mu_accounting=str(mu_accounting),
            mu_precoder=str(mu_precoder),
            mu_csi_error_variance=float(mu_csi_error_variance),
            mu_corr_threshold=float(mu_corr_threshold),
            mu_olla_step_up_db=float(mu_olla_step_up_db),
            mu_olla_step_down_db=resolved_mu_down,
            olla_speedup=float(olla_speedup),
            olla_warmup_speedup=float(olla_warmup_speedup))
        kpi_cfg = sysm.KpiConfig(
            small_burst_policy=small_burst_policy,
            warmup_s=float(warmup_s),
            tti_trace_mode=str(tti_trace_mode),
            tti_trace_max_points=tti_trace_max_points)
    except (TypeError, ValueError) as exc:
        return {"error": str(exc)}
    load_rng = load_book.generator("neighbor_load")
    _t_build = time.perf_counter()
    try:
        _array_md = ds.summary.get("antenna_model", {}) or {}
        _port_order = _array_md.get("port_order")
        _v_order = _array_md.get("vertical_index_order")
        if (_port_order is None
                and ds.summary.get("source") in ("internal_sim", "sionna_rt")):
            # 与 loader.pmi 同一回退合同：没有天线元数据的旧样本按历史
            # h_v_pol + bottom_to_top 读，并披露——不静默按 canonical 错配。
            _port_order = "h_v_pol"
            _v_order = _v_order or "bottom_to_top"
            _pre_notes.append(
                "**天线端口序回退**：数据集没有 antenna_model 元数据，PMI/Type-I "
                "权按历史 h_v_pol + bottom_to_top 端口序读取（与 loader.pmi 同一合同）。")
        elif _port_order is not None and _v_order is None:
            _v_order = ("bottom_to_top" if str(_port_order) == "h_v_pol"
                        else "top_to_bottom")
        tables = sysm.build_link_tables(
            h_users, [float(x) for x in sinr], num_ues=n_ue, geo_sir_db=sir,
            h_for_precoding_users=h_est_users,
            target_bler=float(target_bler),
            neighbor_load=float(neighbor_prb_util), csi=csi_cfg, snapshot_ms=snap_ms,
            rb_per_rbg=carrier["rb_per_rbg"],
            rbg_boundaries=tuple(
                (int(pair[0]), int(pair[1])) for pair in carrier["rbg_boundaries"]),
            load_jitter_rng=(load_rng if float(neighbor_load_jitter) > 0 else None),
            neighbor_load_jitter=float(neighbor_load_jitter),
            precoder=str(precoder), power_constraint=str(power_constraint),
            # capacity 现在也读 pair 表（mu_accounting='pair_table'）：
            # MU 的代价必须同时进 MCS 决策与误块抽签，标量比值做不到。
            mu_enabled=(_flag(mu_enabled)
                        and str(mu_accounting) == "pair_table"),
            mu_rank_per_user=2, mu_precoder=str(mu_precoder),
            mu_csi_error_variance=float(mu_csi_error_variance),
            rb_power_control=power_cfg, power_geometry=power_geometry,
            bs_panel=ds.config.get("bs_panel"),
            srs_cell_ids=serving_cell_ids_by_ue,
            srs_pci_mod3=int(srs_pci_mod3),
            port_order=_port_order,
            vertical_index_order=_v_order)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}
    effective_csi_cfg = csi_cfg
    if csi_cfg.enabled and csi_cfg.srs_resource_allocation:
        assignments = [table.srs_resource_assignment for table in tables]
        if any(assignment is None for assignment in assignments):
            return {"error": "SRS 资源分配已开启，但链路表缺少逐 UE assignment"}
        effective_periods = {
            float(assignment.period_ms)
            for assignment in assignments
            if assignment is not None
        }
        if len(effective_periods) != 1:
            return {"error": (
                "SRS allocator 未形成唯一全局生效周期："
                f"{sorted(effective_periods)}")}
        effective_csi_cfg = replace(
            csi_cfg, srs_period_ms=float(next(iter(effective_periods))))
    # 标量 MU 增益随 se_ratio_legacy 一起下线：MU 的代价直接进 MCS 与误块抽签。
    # `measure_mu_gain` 仍是独立的测量原语，只是不再喂给主循环。
    mu_gain = {
        "ratio": 1.0, "measured": False,
        "note": ("逐 pair 查表口径（mu_accounting='pair_table'）不使用标量比值；"
                 "MU 代价直接进 MCS 与误块抽签"
                 if _flag(mu_enabled) else "未开 MU")}
    build_s = time.perf_counter() - _t_build

    # **建表只做一次，重复的只是 TTI 主循环。** build_link_tables 与随机种子
    # 完全无关（SVD、码本搜索、MCS 查表都是确定性的），所以 n 次重复的边际成本
    # 只有主循环那一份——这是置信区间能默认开着的唯一原因。
    calibration = None
    try:
        if target_prb_utilization is not None:
            calibration = sysm.calibrate_traffic_to_prb(
                tables,
                target_prb_utilization=float(target_prb_utilization),
                axis=str(load_calibration_axis),
                tolerance=float(load_calibration_tolerance),
                max_iterations=load_calibration_max_iterations,
                probe_replications=load_calibration_replications,
                formal_refinements=load_calibration_formal_refinements,
                num_replications=num_replications, master_seed=seed,
                sys_cfg=system_cfg, traffic=traffic_cfg, sched=scheduler_cfg,
                kpi=kpi_cfg,
                build_elapsed_s=build_s,
                replication_workers=replication_workers)
            res = calibration.result
        else:
            res = sysm.simulate_replications(
                tables, num_replications=num_replications, master_seed=seed,
                sys_cfg=system_cfg, traffic=traffic_cfg, sched=scheduler_cfg,
                kpi=kpi_cfg,
                build_elapsed_s=build_s,
                replication_workers=replication_workers)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}
    out = res.as_dict()
    out.update(_system_adaptation_contract(
        target_bler=float(target_bler),
        olla_step_up_db=float(olla_step_up_db),
        olla_step_down_db=olla_step_down_db,
        resolved_su_down=resolved_su_down,
        mu_olla_step_up_db=float(mu_olla_step_up_db),
        mu_olla_step_down_db=mu_olla_step_down_db,
        resolved_mu_down=resolved_mu_down,
    ))
    if calibration is not None:
        out["traffic_calibration"] = calibration.as_dict()
    out["dataset_id"] = dataset_id
    out["analysis_identity"] = ds.summary.get("prereg")
    resolved_algorithm_label = str(algorithm_label).strip() or (
        f"{str(scheduler).upper()}"
        + (f" + MU-{str(mu_precoder).upper()}" if _flag(mu_enabled) else " + SU")
        + f" · {str(precoder).upper()}/{str(power_constraint).upper()}"
    )
    out["algorithm"] = {
        "label": resolved_algorithm_label,
        "scheduler": str(scheduler),
        "mu_enabled": _flag(mu_enabled),
        "mu_precoder": str(mu_precoder),
        "precoder": str(precoder),
        "power_constraint": str(power_constraint),
    }
    out["num_ues"] = len(tables)
    out["kpi_format"] = {
        "shape": "cell / users 里的每个 KPI 都是 {mean, std, ci95, n_rep, cv, "
                 "rel_half_width, min, max}，不是一个裸数",
        "ci95": "95% 置信区间，t 分布（n 小时 t 比 z 宽 20%，用 z 会把区间报窄）",
        "rel_half_width": "区间半宽 / 均值。**比它小的差异这次实验分辨不出来**",
        "distribution": ("tti_occupied_rbg_distribution 是嵌套分布；每个 0..17 桶的 "
                         "tti_share/tti_count 各自带同样的统计结构"),
        "why": ("同一批信道、同一套配置只改种子，实测 cell_experienced_mbps 的"
                "变异系数 11.4%（measurements/seed_variance.json）。"
                "上一轮就发生过把 11.4% 的噪声报成「+14% 提升」的事故。"),
    }
    out["rng"] = {
        **rng.RngBook(master_seed=int(seed)).as_dict(),
        "num_replications": int(num_replications),
        "stream_purposes": dict(rng.STREAMS),
        "covered_by_ci": ["traffic", "harq", "scheduler"],
        "not_covered_by_ci": ["channel", "neighbor_load"],
        "ci_scope": ("各次重复共用同一批信道与同一张链路表（建表与种子无关，"
                     "只建一次），所以区间覆盖的是话务到达、HARQ 误码、调度决胜"
                     "这三条流的抽样噪声。冻结邻区负载抖动**实测没有可分辨地"
                     "把离散度报小**（64 次 replication vs 32 次 master seed 扫描，"
                     "五个 KPI 里四个的变异系数区间重叠，见 "
                     "measurements/rng_replication.json）。"
                     "**信道实现本身的不确定度是另一个、更大的方差分量**，"
                     "要覆盖它得用不同 seed 重新 sr_generate 再比。"),
    }
    out["mu_gain"] = mu_gain
    aging = sysm.ca.aging_summary(
        effective_csi_cfg, num_rbg=carrier["num_rbg"], snapshot_ms=snap_ms,
        rb_per_rbg=carrier["rb_per_rbg"],
        speed_kmh=ue_speed_kmh)
    aging["requested_config"] = csi_cfg.as_dict()
    aging["effective_config"] = effective_csi_cfg.as_dict()
    out["csi_aging"] = aging
    out["carrier"] = carrier
    out["precoder"] = {
        "transmit_weight": str(precoder),
        "note": ("SVD 逐 RBG 特征波束" if precoder == "svd"
                 else "Type-I-style 宽带列码本近似；BF Gain 恒为 0（发射权即参照权）"),
        "cqi_reference_weight": "type1_wideband",
    }
    out["neighbor_load"] = sysm.NeighborLoadConfig(
        prb_utilization=float(neighbor_prb_util),
        jitter=float(neighbor_load_jitter), seed=int(seed)).as_dict()
    out["rb_power_control"] = {
        **power_cfg.as_dict(),
        "profiles": (pc.profile_summary(resolved_power_profiles)
                     if resolved_power_profiles is not None else None),
        "coupling": [dict(t.rb_power_coupling_diagnostics or {}) for t in tables]
        if power_cfg.enabled else [],
        "interference_source": (
            "ChannelHub exact [sample,slot,cell] denominator contributions"
            if power_cfg.enabled else "not used; uniform 1x baseline"),
        "spatial_constraint": str(power_constraint).lower(),
        "spatial_frequency_composition": (
            "RB multiplier scales the already EBF/PEBF/NEBF-normalized matrix; "
            "frequency sum stays constant and PEBF/NEBF per-antenna bounds scale with q"),
    }

    # **让结论不成立的条件必须进 notes**，不能只躺在子字段里等人翻。
    notes = _pre_notes + list(out.get("notes") or [])
    notes.extend(aging.get("warnings") or [])
    notes.append(
        f"TDD 载波使用固定 profile {carrier['profile_id']}："
        f"{carrier['num_rb_in_channel']} RB @ {carrier['scs_khz']} kHz "
        f"（TTI {carrier['tti_ms']:g} ms），{carrier['num_rbg']} 个 RBG × "
        f"{carrier['rb_per_rbg']} RB。标准表的 273 RB 在信道生成前按项目"
        "口径简化为 272，该格栅不向用户开放修改。")
    if carrier["partial_rbg_indices"]:
        notes.append(
            "载波含 partial RBG（索引 "
            f"{carrier['partial_rbg_indices']}）。TBS、RB 功控和 PRB 利用率已按每组"
            "真实 PRB 数记账；RBG 占用直方图仍以组数为横轴。")
    if power_cfg.enabled:
        if not power_cfg.overrides:
            notes.append(
                "RB 功控已开启但没有 override：最终仍是全带 1x，作为接口/守恒基线。")
        notes.append(
            "RB 功控按逐小区绝对干扰功率耦合：服务小区倍率改变期望信号，"
            "每个邻区自己的倍率只改变它对该 UE 的干扰项；每小区 RB 倍率均值严格为 1。")
        notes.append(
            "RB→RBG 的信号/干扰功率耦合是逐项精确重算；跨多个已分配 RBG 的单码字"
            "有效 SINR 当前沿用项目既有的 dB 算术平均，尚未用链路级曲线标定 EESM/MIESM。"
            "因此可审计每个 RB/RBG 的因果方向，但不要把跨频深衰落下的 TB BLER 称为链路级精确值。")
    _n_snap = int(tables[0].sinr_db.shape[0]) if tables else 0
    if not csi_cfg.enabled:
        notes.append("**CSI 老化已关闭**：预编码用的是零时延完美信道，"
                     "这是上界不是现网——MU 增益会被系统性高估。")
    elif _n_snap <= 1:
        notes.append(
            "**CSI 老化开着但测不出来**：这个数据集每个 UE 只有 1 个信道快照，"
            "「陈旧信道」和「当前信道」是同一个矩阵，老化的效果恒为 0。"
            "要看老化就得让每个 UE 有多个时间相关的快照——"
            "生成时把 num_slots_per_sample 调到 8 以上，或让 num_samples 是 num_ues 的倍数。")
    else:
        notes.append(
            f"CSI 老化已开：SRS请求下限 {csi_cfg.srs_period_ms:g} ms、"
            f"生效全局周期 {effective_csi_cfg.srs_period_ms:g} ms"
            f"{'、全局周期自适应' if csi_cfg.srs_period_adaptive else '、周期固定'}"
            f"{f'、{effective_csi_cfg.hop_factor} 倍跳频' if effective_csi_cfg.hopping else '、不跳频'}，"
            f"全带扫一遍 {effective_csi_cfg.full_sweep_ms:g} ms，平均 CSI 陈旧时长 "
            f"{effective_csi_cfg.mean_csi_staleness_ms:.0f} ms；完整4端口需"
            f"{effective_csi_cfg.srs_transmissions_per_full_sweep}次2-port SRS发送。")
        notes.append(
            "SRS基础资源分配已开启：BBL排除、4 CS、17频域相位；每个2T4R UE"
            "用相邻两个SRS机会发送两组2T，两个offset分别进入端口组CSI老化；"
            "只使用本PCI模3颜色。独立srs_waveform后端已支持显式UL cross-link"
            "驱动的RE级污染、TA/CFO、解扩和UL IoT证据；但本次系统主循环没有"
            "自动生成邻区UE→受害gNB cross-link，也未把波形H-hat注入调度。"
            "因此当前结果仍只覆盖固定载波普通H资源的时序/老化，不能据此声称"
            "PCI模3带来BLER或吞吐收益。"
            if csi_cfg.srs_resource_allocation else
            "SRS 资源分配已显式关闭：所有 UE 复现旧的 offset=0 上界。")
    if float(olla_speedup) != 1.0:
        notes.append(sysm.SchedulerConfig(
            olla_speedup=float(olla_speedup)).as_dict()["olla_speedup_warning"])
    if float(olla_warmup_speedup) != float(olla_speedup):
        notes.append(
            f"预启动期 OLLA 步长放大 {float(olla_warmup_speedup):g} 倍，"
            f"KPI 窗口恢复 {float(olla_speedup):g} 倍；目标 BLER 不变。")
    if calibration is not None:
        cal_dict = calibration.as_dict()
        if cal_dict["status"] != "target_met":
            notes.append(
                "**话务校准的正式结果未落入目标容差**："
                f"target={cal_dict['target_prb_utilization']:.1%}，"
                f"measured={float(cal_dict['achieved_prb_utilization']):.1%}，"
                f"tolerance=±{cal_dict['tolerance_absolute']:.1%}。"
                "结果按实测值保留，未回填目标数。")
    out["notes"] = notes
    runtime_provenance = provenance.snapshot(source="system_sim")
    dataset_provenance = ds.summary.get("provenance")
    provenance_check = provenance.compare(dataset_provenance, runtime_provenance)
    out["provenance"] = {
        "dataset": dataset_provenance,
        "runtime": runtime_provenance,
        "compatibility": provenance_check,
    }
    if provenance_check["status"] == "mismatch":
        out["notes"].append(
            "**数据集与当前运行代码的血缘不一致**："
            + "；".join(provenance_check["mismatches"])
            + "。结果仍可用于历史复现，但做当前版本正式结论前应重新生成信道。"
        )
    elif provenance_check["status"] == "unknown":
        out["notes"].append(
            "**数据集缺少完整 provenance（旧产物）**：无法证明当前系统代码与"
            "生成信道时的版本一致；正式结论建议用当前版本重新生成。"
        )
    out["num_samples"] = len(h_users)
    out["summary"] = res.text()
    out["timing"] = {
        "build_tables_s": round(build_s, 3),
        "tti_loops_s": round(res.elapsed_s, 3),
        "per_replication_s": round(res.elapsed_s / max(res.n_rep, 1), 3),
        "total_s": round(build_s + res.elapsed_s, 3),
        "overhead_vs_single_run": (
            round((build_s + res.elapsed_s)
                  / max(build_s + res.elapsed_s / max(res.n_rep, 1), 1e-9) - 1.0, 3)),
        "note": ("建表与随机种子无关，只做一次；重复的只有 TTI 主循环。"
                 "overhead_vs_single_run 就是"
                 "「跑 n 次比跑 1 次多花的比例」。"),
    }
    out["hint"] = ("先把 summary 念给用户（**带上方括号里的置信区间**），"
                   "再把 notes 里的每一条都说出来——那些是让这组数字不成立的条件。"
                   "**报差异之前先比一比 rel_half_width**：效应比区间半宽还小时"
                   "只能说「分辨不出来」，不能报百分比。"
                   "两组配置的正式对比用 rng.compare_replications()，"
                   "它复用 gates.py 的配对检验并给出明确判决。"
                   "用户级明细在 users 里。")
    serializable = _jsonable(out)
    try:
        from . import kpi_view as _kpi_view  # noqa: PLC0415

        serializable["kpi_view"] = _kpi_view.write_kpi_report(
            serializable, dataset_id=dataset_id,
            kpi_focus=kpi_focus, kpi_intent=str(kpi_intent))
    except Exception as exc:  # noqa: BLE001
        # 页面是呈现层；失败不能吞掉已经完成的仿真，但降级必须显式可见。
        serializable["kpi_view"] = {
            "error": f"KPI 页面生成失败：{exc}",
            "notice": "仿真数值仍完整返回；请先修复页面生成错误再交付结果。",
        }
    return serializable


@tool()
def sr_compare_system_results(
    result_ids: list[str],
    baseline_result_id: str | None = None,
    primary_kpi: str = "cell_experienced_mbps",
    title: str = "",
) -> dict[str, Any]:
    """生成 2..5 个系统算法臂的交互式 KPI 对比工作台。

    每个输入 ID 来自一次 ``sr_system_sim(..., algorithm_label=...)`` 返回的
    ``kpi_view.result_id``。工作台把算法保持为同屏固定颜色系列，顶层按“总览、
    KPI 矩阵、用户分布、TTI 趋势、单 TTI、统计门禁”分 Tab；不会把每个算法藏在
    单独 Tab 里。

    生成前硬校验同一 dataset、体验模式、时长、载波、TDD、话务、KPI 口径和逐位
    一致的 ``(master_seed, replication)``。主 KPI 复用 ``rng.compare_replications``
    与 Gate 3；同时比较多个候选时再做 Holm step-down，只会收紧判决。逐 TTI 轨迹
    只解释调度、RBG、MCS/Rank、SINR、BLER/ACK、OLLA 与 PF 状态差异，不能替代
    跨 replication 结论。
    """
    if not isinstance(result_ids, list):
        return {"error": "result_ids 必须是 2..5 个 KPI result_id 组成的数组"}
    try:
        from . import kpi_compare as _kpi_compare  # noqa: PLC0415

        return _kpi_compare.write_comparison_report(
            result_ids,
            baseline_result_id=baseline_result_id,
            primary_kpi=str(primary_kpi),
            title=str(title),
        )
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return {"error": str(exc)}


def main() -> None:
    # 注意只能写 stderr —— stdio 传输下 stdout 是 JSON-RPC 通道。
    if _DEBUG:
        import faulthandler

        faulthandler.enable(file=sys.stderr)
        faulthandler.dump_traceback_later(25, repeat=True, file=sys.stderr, exit=False)

    # ① BLAS 线程池上限必须**早于任何 numpy import**，晚一步就不生效了。
    #    这是本次省内存的唯一有效杠杆：1323 MB → 295 MB（4 线程档）。
    cap = _apply_blas_thread_cap()

    # ② 占位模块在主线程解析掉，③ warmup 把整张重依赖图导完。两步都不能省，
    #    也都不能推迟到请求处理路径上 —— 见 _resolve_lazy_modules 的注释与
    #    channelhub.warmup 里那段标了「别删」的说明。
    _resolve_lazy_modules()
    info = ch.warmup()
    print(
        f"[superran] warmup {'ok' if info.get('ok') else 'FAILED'} "
        f"{info.get('elapsed_s')}s {info.get('error', '')} | BLAS 线程上限 "
        + (f"{cap}（SUPERRAN_BLAS_THREADS=auto 放开）" if cap else "不限（auto）"),
        file=sys.stderr,
        flush=True,
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
