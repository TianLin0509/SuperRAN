"""superwireless MCP 服务端。

工具设计的两条铁律：
1. **不传数据**。信道矩阵落盘，这里只回句柄、摘要和取货代码。
2. **主动交给用户决策**。sw_plan 返回的是提案 + 该问什么 + 为什么值得问，
   不是替用户做决定，也不是把几十个参数一股脑甩出来。
"""
from __future__ import annotations

import functools
import math
import os
import sys
from typing import Any

import anyio

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

from . import channelhub as ch
from . import decisions as dec
from . import deliver as dlv
from . import generate as gen
from . import plan as pl

mcp = _ServerClass("superwireless")

_DEBUG = bool(os.environ.get("SUPERWIRELESS_DEBUG"))


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


# ---------------------------------------------------------------------------
# 能力发现
# ---------------------------------------------------------------------------


@mcp.tool()
def sw_capabilities() -> dict[str, Any]:
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
        "channelhub_root": str(ch.channelhub_root()),
        "engines": caps,
        "channel_models": models,
        # 本地默认硬件与载波。**面板是 8x4x2 时自动生效**，不需要调用方写。
        "default_hardware": hw.describe(),
        "note": (
            "CDL 系列含每条径的角度（AoD/AoA/ZoD/ZoA），TDL 系列没有。"
            "凡是依赖角度的课题（波束管理、定位）必须用 CDL。"
        ),
    }


@mcp.tool()
def sw_list_presets(group: str | None = None) -> dict[str, Any]:
    """列出场景预设。预设只提供场景骨架，具体参数由 sw_plan 协商决定。

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
            "干扰类场景的 IoT 必须生成后用 sw_interference_report 复核 —— "
            "preset 里写的是设计意图，不是保证达标的实测值。"
        ),
    }


# ---------------------------------------------------------------------------
# 协商
# ---------------------------------------------------------------------------


@mcp.tool()
def sw_plan(
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

    用户若无明显偏好，直接用默认值调 sw_generate 即可，不必逐条确认。
    """
    draft, profile = pl.create_draft(intent, preset=preset, overrides=overrides)
    proposal = pl.build_proposal(draft, profile, max_questions=max(1, min(max_questions, 8)))

    cfg, own = pl.resolved_config(draft)
    proposal["estimated"] = {
        "size_mb": round(gen.estimate_size_mb(cfg, int(draft.params.get("num_samples", 200))), 1),
        "note": "耗时随天线数与样本数增长；先跑 20 个样本验证流程更稳妥",
    }
    proposal["next"] = (
        "和用户对齐后调 sw_generate(draft_id=...)；"
        "用户改主意则调 sw_revise(draft_id=..., overrides={...})"
    )
    return _jsonable(proposal)


@mcp.tool()
def sw_revise(
    draft_id: str,
    overrides: dict[str, Any] | None = None,
    design: dict[str, str] | None = None,
) -> dict[str, Any]:
    """差分修正一份提案——用户只说改什么，不必重述整个需求。

    overrides 改仿真参数，例如用户说"信噪比降到 5 dB"：
        sw_revise(draft_id, overrides={"snr_range_dB": [0, 5]})

    design 记录实验设计层的回答，例如用户说"跟 Type II 码本比，看 NMSE"：
        sw_revise(draft_id, design={"baseline": "3GPP Type II 码本",
                                     "metric": "NMSE 与频谱效率损失"})

    design 不影响任何仿真参数，但会写进计划书——三个月后回看时，
    这部分比参数值有用得多。
    """
    draft, profile, changes = pl.revise_draft(draft_id, overrides, design)
    proposal = pl.build_proposal(draft, profile, max_questions=5)
    proposal["changes"] = changes
    proposal["next"] = "确认无误后调 sw_generate(draft_id=...)"
    return _jsonable(proposal)


@mcp.tool()
def sw_list_scenes() -> dict[str, Any]:
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
                "在 sw_plan 的 overrides 里写 {\"scene\": \"shenzhen_futian\"} 即可切换场景；"
                "也可以直接用 rt_munich / rt_shanghai_lujiazui / rt_shenzhen_futian 这几个预设。"
                "射线追踪比统计信道慢一个量级（约 3~5 秒/样本），先小批量验证。"
            ),
        }
    )


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


@mcp.tool()
async def sw_generate(
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

    prereg_id 是 sw_lock_analysis 返回的预注册句柄。传了它，主指标与基线会
    随数据存档，之后 sw_compare_results 能判断用的指标是不是事先定的那个。
    **只能在生成前绑定**——事后补绑没有意义。

    workers 默认 "auto"：按配置预估耗时决定要不要起多进程。多小区大带宽的
    配置能快 3 倍以上；轻配置起进程反而更慢，会自动走串行。
    并行时各块用不同 seed，结果与串行统计等价但逐样本不同（摘要里会写明）。

    collect_ssb=False 关掉每小区 SSB RSRP/SINR 的计算，**多小区场景省约 30%**
    （交错重测中位数 3456 -> 2475 ms/样本，基准自身轮间波动 11.9%）。
    代价是 Dataset.ssb 为空——小区选择、切换、波束管理类课题需要它，别乱关。
    默认 None = 保留，不静默减少数据。
    """
    # 仿真是 CPU 密集的，丢到工作线程，别把 MCP 事件循环堵死
    _dbg("sw_generate: 进入，准备切工作线程")
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
    _dbg("sw_generate: 工作线程返回，准备序列化响应")
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
        draft = pl.load_draft(draft_id)
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
            f'调 sw_deliver(dataset_id="{summary["dataset_id"]}", want="信道") 拿取货代码；'
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


@mcp.tool()
def sw_deliver(dataset_id: str, want: str | None = None) -> dict[str, Any]:
    """按需生成取货代码——返回可直接运行的 Python，不是数据。

    want 可以写自然语言："信道"、"信道 + PMI + SRS RSRP"、"我还想看时延功率谱"。
    不写则只给信道。

    同一个数据集可以反复取货要不同的测量量，**不必重跑仿真**。
    """
    return _jsonable(dlv.build_code(dataset_id, want))


@mcp.tool()
def sw_describe_dataset(dataset_id: str) -> dict[str, Any]:
    """查看已生成数据集的维度、统计分布和可用字段。"""
    s = gen.load_summary(dataset_id)
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


@mcp.tool()
def sw_list_datasets() -> dict[str, Any]:
    """列出本机已生成的数据集。"""
    return _jsonable({"datasets": gen.list_datasets()})


@mcp.tool()
async def sw_validate(dataset_id: str) -> dict[str, Any]:
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


@mcp.tool()
async def sw_link_performance(
    dataset_id: str,
    snr_db: float | None = None,
    methods: list[str] | None = None,
    receiver: str = "mmse",
    use_estimated_csi: bool = False,
) -> dict[str, Any]:
    """算谱效：预编码 → 逐层 SINR → 频谱效率，并横向对比多种预编码方案。

    这是蒙特卡洛仿真最常用的评价链路。返回各方案的谱效均值、95% 置信区间
    和收敛判断——**不收敛时方案间的差异可能只是噪声**，会明确标出。

    参数
    ----
    methods : 默认对比 ``["svd", "svd_wideband", "type1", "dft"]``。
        SVD 是理论上界，Type I 是 3GPP 码本，DFT 是单层波束。
        用户自研方案应当和这几个在同一批信道上比。
    use_estimated_csi : True 时用估计信道计算预编码、用理想信道评估性能，
        得到的是"CSI 有误差时的实际代价"——CSI 反馈类课题的核心对比。
    receiver : ``mmse``（默认）/ ``zf`` / ``mrc``。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _link_perf_sync,
            dataset_id=dataset_id, snr_db=snr_db, methods=methods,
            receiver=receiver, use_estimated_csi=use_estimated_csi,
        )
    )


def _link_perf_sync(
    *, dataset_id: str, snr_db: float | None, methods: list[str] | None,
    receiver: str, use_estimated_csi: bool,
) -> dict[str, Any]:
    import numpy as np

    from . import linklevel as ll
    from . import loader as ld

    ds = ld.load(dataset_id)
    ms = tuple(methods or ("svd", "svd_wideband", "type1", "dft"))
    snr = float(snr_db) if snr_db is not None else float(np.median(ds.sinr_dB))

    kw: dict[str, Any] = {"snr_db": snr, "receiver": receiver}
    if use_estimated_csi:
        kw["channels_for_precoding"] = ds.h_est

    cmp = ll.compare_precoders(ds.h_true, methods=ms, **kw)
    best = max(cmp.items(), key=lambda kv: kv[1]["se_mean"])
    unconverged = [m for m, v in cmp.items() if not v["converged"]]

    return _jsonable(
        {
            "dataset_id": dataset_id,
            "n_samples": int(ds.n),
            "snr_db": round(snr, 2),
            "receiver": receiver,
            "csi_for_precoding": "estimated" if use_estimated_csi else "ideal",
            "results": cmp,
            "best_method": best[0],
            "note": (
                "谱效口径：SE = mean_rb Σ_layer log2(1 + 后处理SINR)。"
                "SVD 为理论上界，vs_svd_pct 是相对它的百分比。"
            ),
            "warning": (
                f"这些方案的置信区间还没收敛到 5%：{unconverged}。"
                f"样本量不足时方案间差异可能只是随机波动，建议加大 num_samples。"
                if unconverged
                else None
            ),
        }
    )


@mcp.tool()
async def sw_calibrate(dataset_id: str) -> dict[str, Any]:
    """按 3GPP TR 38.901 §7.8 的口径算校准量。

    这是业界判断"信道生成得对不对"的标准做法：不看曲线好不好看，而是把标准
    规定的几个统计量按规定口径算出来，跟各公司提交给 3GPP 的参考曲线对。

    出的量（括号内是标准里的条款与指标号）：

    * 耦合损耗 CDF（§7.8.1 指标1）—— 串联检验路损模型 + 天线方向图 + 小区选择
    * 几何量 CDF，含噪与不含噪两条（§7.8.1 指标2 / §7.8.2 指标2）
    * 时延扩展与角度扩展 ASD/ASA/ZSD/ZSA（§7.8.2 指标3，Annex A.1 圆周定义）
    * PRB 奇异值：最大、次大、比值三条 CDF，10log10 尺度（§7.8.2 指标4）

    参考曲线在 3GPP 文稿 R1-165974（大尺度）、R1-165975（全校准）、
    R1-1909704（InF）里。**本工具只出数不判决**，判决在 ``sw_gate``。
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


@mcp.tool()
async def sw_gate(
    dataset_id: str,
    stage: str = "channel",
) -> dict[str, Any]:
    """评审门：拦住站不住的结论。

    ``stage="channel"``（门 1）—— 生成之后、做实验之前跑。把可信度体检的
    结果翻译成门禁语言：硬性检查不通过就是拦截项，不修不许往下走。

    门 2（比较公平）与门 3（结论站得住）在 ``sw_compare_arms`` 里一次跑完，
    因为它们需要两个方案的逐样本结果。
    """
    if stage != "channel":
        return {
            "error": f"stage={stage!r} 不支持",
            "hint": "门 2 与门 3 请用 sw_compare_arms，它需要两个方案的逐样本结果",
        }
    return await anyio.to_thread.run_sync(functools.partial(_gate_sync, dataset_id))


def _gate_sync(dataset_id: str) -> dict[str, Any]:
    from . import gates as g
    from . import loader as ld

    res = g.gate_channel(ld.load(dataset_id))
    out = res.as_dict()
    out["text"] = res.text()
    return _jsonable(out)


@mcp.tool()
async def sw_compare_arms(
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
    ``csi_*``：``ideal`` 用理想信道预编码，``estimated`` 用估计信道。
    ``snr_db`` 不给时用数据集逐样本自身的 SINR（各用户真实工作点）。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _compare_arms_sync,
            dataset_id=dataset_id, method_a=method_a, method_b=method_b,
            name_a=name_a, name_b=name_b, csi_a=csi_a, csi_b=csi_b,
            receiver=receiver, snr_db=snr_db, max_samples=max_samples,
        )
    )


def _compare_arms_sync(
    *, dataset_id: str, method_a: str, method_b: str, name_a: str, name_b: str,
    csi_a: str, csi_b: str, receiver: str, snr_db: float | None, max_samples: int,
) -> dict[str, Any]:
    from . import loader as ld

    ds = ld.load(dataset_id)
    res = ds.compare_arms(
        {"name": name_a, "method": method_a, "csi": csi_a, "receiver": receiver},
        {"name": name_b, "method": method_b, "csi": csi_b, "receiver": receiver},
        snr_db=snr_db, max_samples=max_samples,
    )
    out = res.as_dict()
    out["dataset_id"] = dataset_id
    out["text"] = res.text()
    return _jsonable(out)


@mcp.tool()
def sw_sample_size(
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

    ``std_diff`` 从 ``sw_compare_arms`` 的 ``paired.std_diff`` 取。
    """
    from . import decisions as dec

    return _jsonable(
        dec.sample_size_advice(
            std_diff=std_diff, expected_effect=expected_effect, n_current=n_current
        )
    )


@mcp.tool()
def sw_missing_slots(
    answered_design: list[str] | None = None,
    answered_params: list[str] | None = None,
) -> dict[str, Any]:
    """结论模板里还空着哪些槽 —— 决定该主动问用户什么。

    一次蒙特卡洛仿真的产出说到底就是一句话::

        在【场景】下，【方法】相对【基线】在【指标】上【效应 ± 置信区间】（n 样本），
        该结论在【扫描维度】上成立。

    每个方括号是一个必须填的槽。**空着的槽就是该问的问题**，按"空着的代价"
    从大到小排序返回，每个槽带 3~4 个选项。

    注意样本数不在槽里——它是由效应量和试点方差**算出来**的（``sw_sample_size``），
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


@mcp.tool()
def sw_lock_analysis(
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
    传给 ``sw_generate``，之后 ``sw_compare_results`` 会判断用的指标是不是
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
    d["next"] = f'把 prereg_id 传给 sw_generate：sw_generate(..., prereg_id="{pr.prereg_id}")'
    return _jsonable(d)


@mcp.tool()
def sw_export_eval_template(
    dataset_id: str,
    metric: str = "spectral_efficiency",
) -> dict[str, Any]:
    """给**自研算法**导出一份评测脚本骨架，让它能进门 2 / 门 3。

    内置的 `sw_compare_arms` 只认六种预编码，自研的 CSI 压缩、信道估计、
    波束管理、调度算法进不来。这个工具补那一层：

    1. 拿到 `code`，写进 .py 文件；
    2. 把 `my_algorithm` 的函数体换成你的算法（**不改也能跑**，
       预填的示例是估计 CSI 下的 SVD vs Type I，先确认管道通再换）；
    3. 运行它，会注册两个臂并打印 `result_id`；
    4. 把两个 id 交给 `sw_compare_results` 判决。

    **MCP 不执行用户代码**，脚本在用户自己的进程里跑，只把标准化的逐样本
    结果注册回来。逐样本数值落 .npz，不进 MCP JSON。
    """
    from . import results as rs

    return _jsonable(rs.eval_template(dataset_id, metric=metric))


@mcp.tool()
async def sw_compare_results(
    result_id_a: str,
    result_id_b: str,
    claimed_gain: float | None = None,
) -> dict[str, Any]:
    """判决两个**外部算法结果**，连过门 2、门 3。

    与 `sw_compare_arms` 用的是**同一套统计与门控实现**，判决标准完全一致
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


@mcp.tool()
def sw_list_results(dataset_id: str | None = None) -> dict[str, Any]:
    """列出已注册的外部算法结果。不给 dataset_id 就列全部。"""
    from . import analysis as an
    from . import results as rs

    return _jsonable(
        {
            "results": rs.list_results(dataset_id),
            "pregs": an.list_pregs(),
        }
    )


@mcp.tool()
async def sw_throughput(
    dataset_id: str,
    mcs_table: int = 1,
    target_bler: float = 0.1,
    max_samples: int = 200,
    method: str = "svd",
) -> dict[str, Any]:
    """算**真实吞吐**（Mbps）与 3GPP 口径的边缘用户指标，不是香农上界。

    `sw_link_performance` 给的是 `SE = Σ log2(1+SINR)`——香农谱效，是个
    任何真实系统都达不到的上界。这个工具走业界做系统级仿真的标准路径
    （链路到系统映射），把三项真实损失算进来：

    1. **调制受限** —— 20 dB 时香农说 6.66 bit/s/Hz，64QAM 最多给 5.80
    2. **码率离散** —— MCS 只有 29 档
    3. **有限码长 + 实现损失** —— LDPC 距容量 1~2 dB

    返回吞吐的均值/中位/**5% 边缘用户**/95% 峰值、谱效、MCS 分布、平均 BLER。
    边缘用户吞吐是 3GPP 评估里的公平性指标，比均值更能说明问题。

    `mcs_table`：1 = 最高 64QAM（38.214 Table 5.1.3.1-1），
    2 = 含 256QAM（Table 5.1.3.1-2），3 = 用户提供的 20B 256QAM MCS +
    NewTx/ReTx 解调曲线。**MCS 分布里大量样本压在最高档时，
    说明限制来自 MCS 表而不是信道**，换表 2 通常能明显提升。

    表 1/2 的 BLER 是有限码长分析模型，不是实测。表 3 的 BLER 来自用户提供的
    解调曲线，也不是 3GPP 标准曲线；源标签 Es/No 表示经典 MMSE 接收机 SINR。
    表 3 的 HARQ 首传用 NewTx、后续用 ReTx 曲线；多次重传复用同一 ReTx 曲线。
    表 3 没有 CQI 曲线，CQI 仍用 38.214 Table 2 + 分析 BLER，并在结果中标明来源。
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
    st = ds.throughput(max_samples=max_samples, mcs_table=mcs_table,
                       cqi_table=min(mcs_table, 2), target_bler=target_bler,
                       method=method)
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


@mcp.tool()
def sw_mcs_info(
    table: int = 1,
    show_bler_anchors: bool = False,
) -> dict[str, Any]:
    """查 MCS / CQI 表，以及分析模型或表驱动 BLER 的门限。

    `show_bler_anchors=true` 时给出各 MCS 达到 10% BLER 所需的有效 SINR，
    以及它距同频谱效率的香农极限有多远。**这是模型预测，摆出来供人工对照
    公开的 NR 链路级曲线**——常见量级是 MCS0 约 -5~-7 dB、MCS28 约 20~23 dB。

    `table=1/2` 是逐字录入的 38.214 标准值，BLER 是分析模型。
    `table=3` 是用户提供的 20B 256QAM MCS 与 NewTx/ReTx 曲线：返回两套码率、
    10% BLER 门限和数据哈希自检。它不是 3GPP 标准表；接收机为经典 MMSE，
    源标签 Es/No 表示 SINR，其他链路维度暂不参数化。
    """
    from . import linkadapt as la

    if table not in la.MCS_TABLES:
        raise ValueError(f"table 应为 {sorted(la.MCS_TABLES)}，收到 {table}")

    mcs_rows = (
        la.bc.mcs_profile_rows() if table == 3
        else [m.as_dict() for m in la.MCS_TABLES[table]]
    )
    out: dict[str, Any] = {
        "mcs_table": mcs_rows,
        "cqi_table": [
            {"index": c.index, "modulation": la._MOD_NAME[c.q_m],
             "code_rate": round(c.r_1024 / 1024, 4), "se": c.se}
            for c in la.CQI_TABLES[min(table, 2)]
        ],
        "verify": la.bc.verify_curves() if table == 3 else la.verify_tables(),
        "source": la.MCS_TABLE_SOURCES[table],
        "cqi_source": "3GPP TS 38.214 CQI Table 2" if table == 3 else "same table family",
    }
    if show_bler_anchors:
        out["bler_anchors"] = (
            la.curve_anchor_check() if table == 3
            else la.DEFAULT_BLER.anchor_check(table=table)
        )
    return _jsonable(out)


@mcp.tool()
def sw_bler_curve(
    mcs: int,
    tx_mode: str = "newtx",
    sinr_db_list: list[float] | None = None,
    target_bler: float = 0.1,
) -> dict[str, Any]:
    """查用户提供的单档 BLER 曲线，并可在任意 SINR 点插值。

    `mcs` 为 0..27；`tx_mode` 为 `newtx` 或 `retx`。默认返回完整原始点、码率、
    10% BLER 门限和来源口径。传 `sinr_db_list` 时额外返回查询点的 BLER。

    插值在 log10(BLER) 域线性完成；低于曲线范围钳到 1，高于范围钳到最后一个
    实测点，绝不外推一条看似精确的尾巴。源脚本标签 Es/No 已确认表示 SINR，
    接收机为经典 MMSE；返回值同时保留原始标签和物理口径。
    """
    from . import linkadapt as la

    return _jsonable(la.bler_curve(
        mcs=mcs, tx_mode=tx_mode, target_bler=target_bler,
        sinr_db=sinr_db_list,
    ))


@mcp.tool()
async def sw_tdd_mcs(
    dataset_id: str,
    cqi: int,
    sample_index: int = 0,
    olla_mcs_offset: float = 0.0,
    target_bler: float = 0.1,
    max_rank: int = 4,
    use_estimated_csi: bool = True,
    feedback_ack: bool | None = None,
    olla_ack_step_mcs: float = 0.1,
) -> dict[str, Any]:
    """TDD 下按 CQI、SVD-vs-PMI BF Gain 和 OLLA 选择最终 MCS。

    真实调用链是：CQI → 按频谱效率映射表 3 初始 MCS → 该 MCS 的 NewTx 目标
    BLER SINR 门限 → 在同一信道/CSI/rank/功率/干扰/MMSE 接收机下逐 RB、逐流计算
    ``SINR_SVD - SINR_PMI`` → 在 dB 域对全部 RB×流求算术平均 → 按表 3 重映射
    MCS → 加连续的 ``olla_mcs_offset`` → ``floor`` → 钳位到 0..27。

    `CQI=0` 表示 out-of-range，不调度。`feedback_ack` 可选：给出时按目标首传
    BLER 更新下一时刻的 OLLA；10% 默认对应 ACK +0.1、NACK -0.9 MCS。当前时刻
    使用传入的 OLLA，反馈只影响返回的 `olla_next_offset_mcs`。

    返回每个中间量，包括初始 MCS/门限、逐流 PMI/SVD SINR、BF Gain、用户 SINR、
    BF 后 MCS、OLLA 取整前后值和最终 BLER，便于 Agent 逐步审计。
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _tdd_mcs_sync,
            dataset_id=dataset_id,
            cqi=cqi,
            sample_index=sample_index,
            olla_mcs_offset=olla_mcs_offset,
            target_bler=target_bler,
            max_rank=max_rank,
            use_estimated_csi=use_estimated_csi,
            feedback_ack=feedback_ack,
            olla_ack_step_mcs=olla_ack_step_mcs,
        )
    )


def _tdd_mcs_sync(
    *,
    dataset_id: str,
    cqi: int,
    sample_index: int,
    olla_mcs_offset: float,
    target_bler: float,
    max_rank: int,
    use_estimated_csi: bool,
    feedback_ack: bool | None,
    olla_ack_step_mcs: float,
) -> dict[str, Any]:
    from . import loader as ld

    ds = ld.load(dataset_id)
    return _jsonable(ds.tdd_mcs(
        sample_index,
        cqi_index=cqi,
        olla_mcs_offset=olla_mcs_offset,
        target_bler=target_bler,
        max_rank=max_rank,
        use_estimated_csi=use_estimated_csi,
        feedback_ack=feedback_ack,
        olla_ack_step_mcs=olla_ack_step_mcs,
    ))


@mcp.tool()
async def sw_sweep_snr(
    dataset_id: str,
    snr_db_list: list[float] | None = None,
    mcs_table: int = 1,
    max_samples: int = 60,
) -> dict[str, Any]:
    """扫信噪比，出**谱效/吞吐 vs SNR 曲线** —— 无线论文里最标准的那张图。

    对同一批信道，把工作点信噪比设成一组值，逐点给出香农谱效、实际谱效、
    吞吐、选中的 MCS。**同一批信道**意味着各点之间是配对的，曲线不会被
    信道抽样噪声搅乱。

    默认扫 -5 ~ 35 dB。返回里 `efficiency_vs_shannon` 的走势最有信息量：
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
        res = [ds.link_adaptation(i, snr_db=float(snr), n_prb=n_prb,
                                  mcs_table=mcs_table, cqi_table=min(mcs_table, 2))
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


@mcp.tool()
def sw_interference_report(dataset_id: str) -> dict[str, Any]:
    """一个数据集的干扰画像：业务域 IoT + 测量域导频 SIR。只读已落盘的标量。

    **业务域和测量域是两回事**，报告分开给：

    * ``traffic_domain``——PDSCH/PUSCH 受到的干扰，用 IoT（噪声抬升 (I+N)/N）
      刻画。20 dB 以上算高干扰，同时给出等效小区负载。
    * ``measurement_domain``——SRS / CSI-RS 导频受到的干扰，决定信道估计精度。
      给出估计 NMSE 的下限。这两列只在 ``link="BOTH"`` 生成的数据里有。

    IoT 由几何 SIR 与 SINR 精确推出（``IoT = SIR/(SIR-SINR)``，线性域），
    **不是 snr_dB 减 sinr_dB**——那两个字段口径不同，相减会差几十 dB。

    贴在 ±50 dB 契约边界上的样本、以及没有干扰源的哨兵样本会单独计数而不是
    混进统计，``notes`` 里会说明。
    """
    from . import interference as itf

    return _jsonable(itf.interference_report(dataset_id))


@mcp.tool()
def sw_iot_convert(
    iot_db: float | None = None,
    load: float | None = None,
    sinr_db: float | None = None,
    sir_db: float | None = None,
) -> dict[str, Any]:
    """IoT 相关的换算与分级。三种用法，给哪组参数就算哪个。

    * 给 ``sinr_db`` + ``sir_db``：算这一点的 IoT（两者必须同口径，
      即都来自几何 SINR 计算，不能拿 snr_dB 凑）。
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


@mcp.tool()
def sw_design_interference(target_iot_db: float = 20.0) -> dict[str, Any]:
    """要构造某个干扰强度的场景，该动哪些旋钮。

    **不返回保证达标的配置。** IoT 由几何、负载、功率共同决定，唯一可靠的
    确认方式是生成一批再用 ``sw_interference_report`` 复核。这里给的是方向与
    量级，以及各旋钮在 ChannelHub 几何模型里的**实际**作用——有几个和教科书
    直觉不一样，写在每条的 note 里。
    """
    from . import interference as itf

    return _jsonable(itf.design_hint(target_iot_db))


# ---------------------------------------------------------------------------
# 场景探测
# ---------------------------------------------------------------------------


@mcp.tool()
def sw_probe_scenario(
    preset: str | None = None,
    config: dict[str, Any] | None = None,
    num_samples: int = 30,
) -> dict[str, Any]:
    """花几十秒看清一个场景长什么样，再决定要不要花几十分钟正式跑。

    **下单之前先看货。** 把 ``num_rb`` 压到 24、关掉 SSB 测量，几何量与全带宽
    **逐位相同**（实测 273 / 24 / 12 三档，sinr / sir / 路损 / 距离 / 视距 /
    多普勒 / UE 位置全部零差异），唯一变的 ``snr_dB`` 有解析修正且已修正。
    耗时降到约 1/8。

    回的是：干扰画像（IoT，多小区才有）、链路预算（SNR/SINR/SIR 分布）、
    几何量（路损、距离、视距比例、多普勒）、测量域导频 SIR（link=BOTH 才有）。

    ``not_available`` 里明确列出探测模式**给不了**的量——谱效、吞吐、时延扩展
    估计、宽带预编码。这些必须跑正式生成，别拿探测结果替代。

    参数
    ----
    preset : 预设名（sw_list_presets 查）。与 config 二选一。
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
    if config:
        cfg.update(config)
    if not cfg:
        return {"error": "preset 与 config 至少给一个。"}

    out = sc.probe(cfg, num_samples=num_samples)
    out["preset"] = preset
    return _jsonable(out)


@mcp.tool()
def sw_compare_scenarios(
    presets: list[str],
    num_samples: int = 30,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """并排探测几个场景，回一张对照表。用来在候选场景里选。

    每个场景各跑一次探测（见 ``sw_probe_scenario`` 的口径说明），
    表里给 IoT 中位数与等级、SINR/SNR 中位数、路损中位数、视距比例、单样本耗时。

    典型用法：确认"高干扰"预设确实比"低干扰"对照高出足够的 IoT，
    再拿这两个去跑正式对比——**别在没验证过干扰水平的两批数据上做消融**。
    """
    from . import scenario as sc

    all_presets = pl.load_presets()
    named: dict[str, dict[str, Any]] = {}
    unknown = []
    for name in presets:
        if name not in all_presets:
            unknown.append(name)
            continue
        cfg = dict(all_presets[name]["config"])
        if overrides:
            cfg.update(overrides)
        named[name] = cfg
    if unknown:
        return {"error": f"未知预设：{unknown}", "available": sorted(all_presets)}
    if not named:
        return {"error": "presets 不能为空。"}

    return _jsonable(sc.compare_probes(named, num_samples=num_samples))


# ---------------------------------------------------------------------------
# 仿真说明书
# ---------------------------------------------------------------------------


@mcp.tool()
def sw_spec_sheet(
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

    ``sw_generate`` 会自动生成一份（带真实撒点），句柄在 ``summary.spec_sheet``；
    这个工具用于**生成之前**先看一眼。

    **默认不替用户弹窗**，只把 ``url`` 给他，他自己在浏览器或 AI HUB 里点开。
    页面带一个调参面板：改完点「应用到仿真」，改动**直接回到这个 MCP 进程**，
    你随后调 ``sw_await_config`` 就能拿到——不用他复制粘贴。

    所以敲定配置那一步的标准动作是：

        sw_spec_sheet(...)  →  把 url 发给用户，说"点开看一眼；要改就在上面改，
                               改完点应用"  →  sw_await_config()

    ``writeback`` 字段告诉你这次是哪条路：``post`` 表示回传通道通了、
    可以去 ``sw_await_config`` 等；``clipboard`` 表示服务没起来（原因在
    ``serve_error``），得让用户复制粘贴。

    **返回的是地址和摘要，不要把 HTML 内容贴回对话。**
    把 ``headline`` 和 ``notes`` 转述给用户，并把 ``url`` 发给他。

    参数
    ----
    draft_id : sw_plan / sw_revise 的草稿句柄（最常用）
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

        ds = _load(dataset_id)
        cfg = dict(ds.config)
        try:
            pos = ds.ue_position
            ue_xy = [(float(r[0]), float(r[1])) for r in pos[:400]]
        except Exception:  # noqa: BLE001
            ue_xy = None
    elif draft_id:
        draft = pl.load_draft(draft_id)
        cfg, _own = pl.resolved_config(draft)
        user_set = list(draft.user_set)
    elif preset:
        presets = pl.load_presets()
        if preset not in presets:
            return {"error": f"未知预设 {preset!r}", "available": sorted(presets)}
        cfg = dict(presets[preset]["config"])
    if config:
        cfg.update(config)
        user_set = sorted(set(user_set) | set(config))
    if not cfg:
        return {"error": "需要 draft_id / dataset_id / preset / config 其中之一。"}

    out = sp.write_spec(
        cfg, user_set=user_set, dataset_id=dataset_id,
        title=title or "仿真说明书", ue_xy=ue_xy, highlight=highlight,
        open_browser=open_browser,
    )
    if out.get("writeback") == "post":
        out["hint"] = (
            "把 url 发给用户让他自己点开（别替他弹窗），转述 headline 和 notes，"
            "然后告诉他「要调参就在页面上改，改完点『应用到仿真』」，"
            "接着调 sw_await_config 等他。**不要把 HTML 内容或图贴回对话。**"
        )
    else:
        out["hint"] = (
            "把 headline 和 notes 转述给用户，并给出 html_path 让他自己打开；"
            f"回传只能走复制粘贴（{out.get('serve_error')}）。"
            "**不要把 HTML 内容或图贴回对话。**"
        )
    return _jsonable(out)


@mcp.tool()
def sw_await_config(timeout_s: float = 90.0, spec_id: str | None = None) -> dict[str, Any]:
    """等用户在说明书页面上点「应用到仿真」，把他改的参数取回来。

    **紧跟在 ``sw_spec_sheet`` 之后调**（前提是它返回 ``writeback="post"``）。
    用户在页面上拖几下滑块、点一下按钮，改动就到这里了——省掉"复制 → 切窗口
    → 粘贴"三步。

    返回 ``got=0`` **不是错误**，只是这段时间里用户没点。两种处理：

    * 用户还在看 → 再调一次接着等；
    * 用户已经在对话里说话了 → 别再等，按他说的做。

    ``overrides`` 可以直接喂给 ``sw_revise(draft_id, **overrides)`` 或
    ``sw_generate(config=...)``。**拿到后先复述一遍改了什么再动手**，
    用户点错了得有机会喊停。

    参数
    ----
    timeout_s : 最多等多久，默认 90 秒（上限 240）。别设太长，
        MCP 客户端那边也有超时，卡住比等不到更难解释。
    spec_id : 只收某一份说明书的回传。不传就收全部。
    """
    from . import bridge as br

    subs = br.await_submission(min(float(timeout_s), 240.0), spec_id)
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
        "hint": "先向用户复述这几项改动，再调 sw_revise / sw_generate 落实。"
                "改完记得重新出一份说明书。",
    }


def main() -> None:
    # 启动即预热：依赖问题在这里暴露，且不拖慢第一次调用。
    # 注意只能写 stderr —— stdio 传输下 stdout 是 JSON-RPC 通道。
    if _DEBUG:
        import faulthandler

        faulthandler.enable(file=sys.stderr)
        faulthandler.dump_traceback_later(25, repeat=True, file=sys.stderr, exit=False)

    info = ch.warmup()
    print(
        f"[superwireless] warmup {'ok' if info.get('ok') else 'FAILED'} "
        f"{info.get('elapsed_s')}s {info.get('error', '')}",
        file=sys.stderr,
        flush=True,
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
