"""仿真计划：意图 → 提案 → 定稿。

一份计划书同时是给人看的实验记录和给机器执行的指令。draft 落盘保存，
所以协商可以跨会话继续，也便于事后复现。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import decisions as dec
from .paths import drafts_dir, presets_file

# ---------------------------------------------------------------------------
# 抽象参数 → ChannelHub 实参 的翻译
# ---------------------------------------------------------------------------
# 决策点用的是人话（"64T4R"），ChannelHub 要的是具体键。这一层负责翻译，
# 也负责把 ChannelHub 根本不支持的参数（如 snr_range_dB）挑出来另作处理。

_ANTENNA_PRESETS: dict[str, dict[str, int]] = {
    "64T4R": {"num_bs_tx_ant": 64, "num_bs_rx_ant": 64, "num_ue_tx_ant": 4, "num_ue_rx_ant": 4},
    "32T4R": {"num_bs_tx_ant": 32, "num_bs_rx_ant": 32, "num_ue_tx_ant": 4, "num_ue_rx_ant": 4},
    "16T2R": {"num_bs_tx_ant": 16, "num_bs_rx_ant": 16, "num_ue_tx_ant": 2, "num_ue_rx_ant": 2},
    "4T4R": {"num_bs_tx_ant": 4, "num_bs_rx_ant": 4, "num_ue_tx_ant": 4, "num_ue_rx_ant": 4},
}

# ChannelHub 不认识、由 superwireless 自己消化的键
# scene 会展开成 scenario / osm_path / 站点布局（见 scenes.resolve_scene_config）
#
# 注意 antenna_preset 这个名字：它是"64T4R"这类简写标签，展开成 num_bs_tx_ant 等。
# **不能叫 bs_antenna** —— ChannelHub 自己有一个 bs_antenna 配置块（嵌套 dict，
# 含 port_order / element_pattern / fixed_vertical_subarray），是描述 1驱3 子阵这类
# 阵列细节用的。两者重名会让阵列配置被静默吞掉。
_SUPERWIRELESS_ONLY = {
    "antenna_preset", "snr_range_dB", "measurements_wanted", "scene", "scene_site_preset",
}


def antenna_label(params: dict[str, Any]) -> str | None:
    """从具体天线数反推标签。preset 直接给了 num_bs_* 时用它，避免默认标签把 preset 冲掉。"""
    bs = params.get("num_bs_tx_ant")
    ue = params.get("num_ue_rx_ant")
    if bs is None:
        return None
    for label, spec in _ANTENNA_PRESETS.items():
        if spec["num_bs_tx_ant"] == bs and spec["num_ue_rx_ant"] == ue:
            return label
    return f"{bs}T{ue}R"


def translate(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """拆成 (ChannelHub 实参, superwireless 自用参数)。

    antenna_preset 这类抽象参数最后展开，因为它要覆盖具体的 num_bs_* ——
    能出现在 params 里就说明是被明确选定的阵型。

    ChannelHub 自己的 ``bs_antenna``（嵌套 dict：port_order / element_pattern /
    fixed_vertical_subarray 等，1驱3 子阵就配在这里）原样透传，不做任何解释。
    为兼容早期写法，字符串形式的 bs_antenna 仍按 antenna_preset 处理。
    """
    ch: dict[str, Any] = {}
    own: dict[str, Any] = {}
    antenna: str | None = None

    for k, v in params.items():
        if k == "antenna_preset":
            antenna = str(v)
            own[k] = v
        elif k == "bs_antenna":
            if isinstance(v, str):  # 早期写法：bs_antenna="64T4R"
                antenna = v
                own["antenna_preset"] = v
            else:  # ChannelHub 的阵列配置块，原样透传
                ch[k] = v
        elif k in _SUPERWIRELESS_ONLY:
            own[k] = v
        else:
            ch[k] = v

    if antenna is not None:
        spec = _ANTENNA_PRESETS.get(antenna)
        if spec is not None:
            ch.update(spec)

    # 射线追踪场景展开：scene -> scenario / osm_path / 站点布局。
    # 真实城市场景会在这里顺带完成资产准备（复制到缓存 + 修 PLY 头）。
    scene = own.get("scene")
    if scene:
        from .scenes import resolve_scene_config  # 延迟导入，避免非 RT 路径付出代价

        scene_cfg = resolve_scene_config(str(scene), own.get("scene_site_preset"))
        scene_cfg.pop("source", None)
        for k, v in scene_cfg.items():
            # 用户显式给过的值优先，场景只补没给的
            if k in ("scenario", "osm_path", "scene_preset") or k not in ch:
                ch[k] = v
    return ch, own


# ---------------------------------------------------------------------------
# Preset
# ---------------------------------------------------------------------------


def load_presets() -> dict[str, dict[str, Any]]:
    path = presets_file()
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def preset_summaries() -> list[dict[str, Any]]:
    out = []
    for name, body in load_presets().items():
        out.append(
            {
                "preset": name,
                "label": body.get("label", name),
                "summary": body.get("summary", ""),
                "typical_for": body.get("typical_for", []),
                "num_sites": body.get("config", {}).get("num_sites"),
            }
        )
    return out


_RT_HINTS: dict[str, str] = {
    "慕尼黑": "rt_munich", "munich": "rt_munich",
    "陆家嘴": "rt_shanghai_lujiazui", "上海": "rt_shanghai_lujiazui",
    "福田": "rt_shenzhen_futian", "深圳": "rt_shenzhen_futian",
}


def _guess_preset(intent: str, profile: dec.TaskProfile) -> str:
    """按意图挑一个场景骨架。多小区类任务自动升到 7 站。"""
    text = (intent or "").lower()
    for key in load_presets():
        if key in text:
            return key

    # 提到具体城市或射线追踪，走 RT 路径
    for hint, preset in _RT_HINTS.items():
        if hint in text:
            return preset
    if any(w in text for w in ("射线追踪", "ray tracing", "raytracing", "真实地图", "真实建筑", "osm")):
        return "rt_munich"
    if any(w in text for w in ("19 站", "19站", "19 site", "57")):
        return "multicell_19site"
    if any(w in text for w in ("室内", "工厂", "indoor", "factory")):
        return "indoor_factory"
    if "require_multicell" in profile.guards or any(
        w in text for w in ("多小区", "多站", "multi-cell", "multicell", "邻区", "干扰")
    ):
        return "multicell_7site"
    if any(w in text for w in ("最小", "冒烟", "快速", "先跑通", "smoke")):
        return "single_cell_4t4r"
    return "single_cell_64t4r"


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------


@dataclass
class Draft:
    draft_id: str
    intent: str
    task: str
    task_label: str
    preset: str
    params: dict[str, Any] = field(default_factory=dict)
    user_set: list[str] = field(default_factory=list)  # 用户显式指定过的键
    design: dict[str, str] = field(default_factory=dict)  # 实验设计层的回答
    created_at: float = field(default_factory=time.time)
    history: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "intent": self.intent,
            "task": self.task,
            "task_label": self.task_label,
            "preset": self.preset,
            "params": self.params,
            "user_set": self.user_set,
            "design": self.design,
            "created_at": self.created_at,
            "history": self.history,
        }


def _draft_path(draft_id: str) -> Path:
    return drafts_dir() / f"{draft_id}.json"


def save_draft(d: Draft) -> None:
    p = _draft_path(d.draft_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_draft(draft_id: str) -> Draft:
    p = _draft_path(draft_id)
    if not p.is_file():
        raise KeyError(f"找不到计划 {draft_id!r}；它可能已被清理，重新 plan 一次即可")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return Draft(**raw)


def create_draft(
    intent: str,
    *,
    preset: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[Draft, dec.TaskProfile]:
    """从自然语言意图建一份提案。"""
    profile = dec.classify_intent(intent)
    preset_name = preset or _guess_preset(intent, profile)
    presets = load_presets()
    if preset_name not in presets:
        preset_name = "single_cell_64t4r"

    params: dict[str, Any] = dict(presets.get(preset_name, {}).get("config", {}))
    params.update(profile.config_hints)

    # preset 若已给具体天线数，就按它反推标签，不要让默认标签把 preset 冲掉
    inferred = antenna_label(params)
    if inferred:
        params["antenna_preset"] = inferred

    # 决策点的默认值补进来（preset 已给的不覆盖）
    for d in dec.decisions_for(profile, limit=99):
        translated, _ = translate({d.key: d.default})
        for k, v in translated.items():
            params.setdefault(k, v)
        if d.key in _SUPERWIRELESS_ONLY:
            params.setdefault(d.key, d.default)

    params.setdefault("num_samples", 200)
    params.setdefault("seed", 42)

    user_set: list[str] = []
    if overrides:
        params.update(overrides)
        user_set = sorted(overrides)

    d = Draft(
        draft_id="d_" + uuid.uuid4().hex[:8],
        intent=intent,
        task=profile.task,
        task_label=profile.label,
        preset=preset_name,
        params=params,
        user_set=user_set,
        history=[f"由意图创建，场景骨架 {preset_name}"],
    )
    save_draft(d)
    return d, profile


def revise_draft(
    draft_id: str,
    overrides: dict[str, Any] | None = None,
    design: dict[str, str] | None = None,
) -> tuple[Draft, dec.TaskProfile, list[str]]:
    """差分修正：只说改什么，不用重述整个需求。

    ``design`` 记录实验设计层的回答（基线、指标、推广范围）。它不影响仿真
    参数，但会写进计划书——这是三个月后回看时最有价值的部分。
    """
    d = load_draft(draft_id)
    profile = next((p for p in dec.TASK_PROFILES if p.task == d.task), dec.TASK_PROFILES[-1])

    changes: list[str] = []
    for k, v in (overrides or {}).items():
        old = d.params.get(k)
        if old != v:
            changes.append(f"{k}: {old!r} → {v!r}")
        d.params[k] = v
        if k not in d.user_set:
            d.user_set.append(k)

    for k, v in (design or {}).items():
        if v:
            d.design[k] = str(v)
            changes.append(f"实验设计 {k}: {str(v)[:40]}")

    if changes:
        d.history.append("修改 " + "；".join(changes))
    save_draft(d)
    return d, profile, changes


def resolved_config(d: Draft) -> tuple[dict[str, Any], dict[str, Any]]:
    """定稿：拆出真正交给 ChannelHub 的配置和自用参数。"""
    return translate(d.params)


# ---------------------------------------------------------------------------
# 提案渲染
# ---------------------------------------------------------------------------


def build_proposal(
    d: Draft,
    profile: dec.TaskProfile,
    *,
    max_questions: int = 6,
) -> dict[str, Any]:
    """组装给 Agent 看的提案。

    分两层交给 Agent：

    * ``design_questions`` —— 实验设计层（跟什么比、用什么指标、推广到哪）。
      这层没有默认值，也不影响仿真参数，但决定了这批数据能不能支撑
      用户想要的结论。**应当先问这层**，参数配错重跑就行，实验设计
      错了整个结论作废。
    * ``questions`` —— 仿真参数层，每条都带 why 和默认值。

    另外 ``sweeps`` 给出建议的对比组，``pitfalls`` 是这类课题的常见坑。
    """
    ch_cfg, own = resolved_config(d)
    picked = dec.decisions_for(profile, limit=max_questions)

    questions = []
    for item in picked:
        current = d.params.get(item.key, item.default)
        questions.append(
            {
                **item.as_dict(),
                "current": current,
                "user_specified": item.key in d.user_set,
            }
        )

    design = [
        {**q.as_dict(), "answered": d.design.get(q.key)}
        for q in dec.design_questions_for(profile)
    ]

    issues = dec.check_guards(profile, d.params)
    presets = load_presets()

    return {
        "draft_id": d.draft_id,
        "task": d.task,
        "task_label": d.task_label,
        "preset": d.preset,
        "preset_label": presets.get(d.preset, {}).get("label", d.preset),
        "preset_summary": presets.get(d.preset, {}).get("summary", ""),
        "design_questions": design,
        "questions": questions,
        "also_configurable": dec.also_configurable(profile),
        "suggested_sweeps": dec.sweep_suggestions(profile),
        "pitfalls": list(profile.pitfalls),
        "issues": issues,
        "ready_to_go": not any(i["severity"] == "block" for i in issues),
        "resolved_config": ch_cfg,
        "superwireless_params": own,
        "user_specified": d.user_set,
        "hint": (
            "建议顺序：先用 design_questions 和用户对齐实验设计（尤其是基线和指标），"
            "再问 questions 里的仿真参数。用户没有明确偏好时直接用默认值生成，"
            "不必逐条确认。suggested_sweeps 里的对比组值得主动提一句——"
            "很多结论必须靠 A/B 才立得住。"
        ),
    }


def render_plan_markdown(d: Draft, profile: dec.TaskProfile, wanted: list[str]) -> str:
    """计划书：上半人话，下半配置。可存档、可交给同事复现。"""
    ch_cfg, own = resolved_config(d)
    lines = [
        f"# 仿真计划：{d.task_label}",
        "",
        "## 要验证什么",
        d.intent or "（未说明）",
        "",
    ]

    # 实验设计层：三个月后回看时最有价值的部分
    if d.design:
        labels = {
            "baseline": "对比基线", "metric": "评价指标",
            "scope": "结论适用范围", "hypothesis": "预期结果",
        }
        lines.append("## 实验设计")
        for k, v in d.design.items():
            lines.append(f"- **{labels.get(k, k)}**：{v}")
        lines.append("")

    lines.append("## 关键选择与理由")
    for item in dec.decisions_for(profile, limit=99):
        cur = d.params.get(item.key, item.default)
        mark = "用户指定" if item.key in d.user_set else "默认"
        first_sentence = item.why.split("；")[0].split("。")[0]
        lines.append(f"- **{item.question.rstrip('？')}**：`{cur}`（{mark}）—— {first_sentence}")

    lines += [
        "",
        "## 产出什么",
        "、".join(wanted) + "；其余测量量后续可再取，不必重跑仿真。",
        "",
        "## 场景骨架",
        f"`{d.preset}`",
        "",
        "---",
        "以下由 superwireless 执行：",
        "",
        "```yaml",
        yaml.safe_dump(ch_cfg, allow_unicode=True, sort_keys=True).rstrip(),
        "```",
    ]
    if own:
        lines += ["", "superwireless 自用参数：", "", "```yaml",
                  yaml.safe_dump(own, allow_unicode=True, sort_keys=True).rstrip(), "```"]
    if d.history:
        lines += ["", "## 修改记录", *[f"- {h}" for h in d.history]]
    return "\n".join(lines)
