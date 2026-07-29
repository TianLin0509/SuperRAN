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
from mcp.server.fastmcp import FastMCP

from . import channelhub as ch
from . import decisions as dec
from . import deliver as dlv
from . import generate as gen
from . import plan as pl

mcp = FastMCP("superwireless")

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
    return {
        "channelhub_root": str(ch.channelhub_root()),
        "engines": caps,
        "channel_models": models,
        "note": (
            "CDL 系列含每条径的角度（AoD/AoA/ZoD/ZoA），TDL 系列没有。"
            "凡是依赖角度的课题（波束管理、定位）必须用 CDL。"
        ),
    }


@mcp.tool()
def sw_list_presets() -> dict[str, Any]:
    """列出场景预设。预设只提供场景骨架，具体参数由 sw_plan 协商决定。"""
    return {
        "presets": pl.preset_summaries(),
        "tasks": [
            {"task": p.task, "label": p.label, "asks": list(p.decision_keys)}
            for p in dec.TASK_PROFILES
        ],
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
) -> dict[str, Any]:
    """生成信道数据集，返回句柄与统计摘要（不返回数据本身）。

    两种用法：
    * 协商过：只传 draft_id
    * 用户说"随便，默认就行"：直接传 intent，跳过协商

    返回里的 auto_decided 列出了替用户做的决定，请转述给用户，
    这样他事后想改也知道改什么。
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
            num_samples=num_samples,
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
