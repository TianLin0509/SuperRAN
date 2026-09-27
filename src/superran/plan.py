"""仿真计划：意图 → 提案 → 定稿。

一份计划书同时是给人看的实验记录和给机器执行的指令。draft 落盘保存，
所以协商可以跨会话继续，也便于事后复现。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import decisions as dec
from .paths import drafts_dir, presets_file

_HANDLE_RE = re.compile(r"[A-Za-z0-9_-]+")

# ---------------------------------------------------------------------------
# 抽象参数 → ChannelHub 实参 的翻译
# ---------------------------------------------------------------------------
# 决策点用的是人话（"64T4R"），ChannelHub 要的是具体键。这一层负责翻译，
# 也负责把 ChannelHub 根本不支持的参数（如 snr_range_dB）挑出来另作处理。

_ANTENNA_PRESETS: dict[str, dict[str, int]] = {
    "256T4R": {"num_bs_tx_ant": 256, "num_bs_rx_ant": 256, "num_ue_tx_ant": 4, "num_ue_rx_ant": 4},
    "64T4R": {"num_bs_tx_ant": 64, "num_bs_rx_ant": 64, "num_ue_tx_ant": 4, "num_ue_rx_ant": 4},
    "32T4R": {"num_bs_tx_ant": 32, "num_bs_rx_ant": 32, "num_ue_tx_ant": 4, "num_ue_rx_ant": 4},
    "16T2R": {"num_bs_tx_ant": 16, "num_bs_rx_ant": 16, "num_ue_tx_ant": 2, "num_ue_rx_ant": 2},
    "4T4R": {"num_bs_tx_ant": 4, "num_bs_rx_ant": 4, "num_ue_tx_ant": 4, "num_ue_rx_ant": 4},
}

# ChannelHub 不认识、由 superran 自己消化的键
# scene 会展开成 scenario / osm_path / 站点布局（见 scenes.resolve_scene_config）
#
# 注意 antenna_preset 这个名字：它是"64T4R"这类简写标签，展开成 num_bs_tx_ant 等。
# **不能叫 bs_antenna** —— ChannelHub 自己有一个 bs_antenna 配置块（嵌套 dict，
# 含 port_order / element_pattern / fixed_vertical_subarray），是描述 1驱3 子阵这类
# 阵列细节用的。两者重名会让阵列配置被静默吞掉。
_SUPERRAN_ONLY = {
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
    """拆成 (ChannelHub 实参, superran 自用参数)。

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
        elif k in _SUPERRAN_ONLY:
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
        # **scene 名字本身也要进引擎配置。** 只展开成 scenario/osm_path 是不够的：
        # 射线追踪引擎要靠这个名字选 Sionna 自带场景（etoile / florence /
        # san_francisco …），拿不到就只能退默认。以前它停在 own 里，于是
        # 任何非 munich 的请求都被静默跑成 munich，而结果仍标 sionna_rt。
        ch["scene"] = str(scene)
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
        cfg = body.get("config", {}) or {}
        item = {
            "preset": name,
            "group": body.get("group", "其他"),
            "label": body.get("label", name),
            "summary": body.get("summary", ""),
            "typical_for": body.get("typical_for", []),
            "num_sites": cfg.get("num_sites"),
            "num_cells": (
                (cfg.get("num_sites") or 1) * (cfg.get("sectors_per_site") or 1)
                if cfg.get("num_sites") else None
            ),
            "isd_m": cfg.get("isd_m"),
            "link": cfg.get("link", "DL"),
        }
        # 只有真跑过、把实测值写回 preset 的场景才有 expect；没有就不给，
        # 不用"设计意图"冒充实测。
        for key in ("expect", "verify", "caveat"):
            if body.get(key):
                item[key] = body[key]
        # 预设里写了、仿真器却不读的键：机器可见地列出来，而不只写在 YAML 注释里。
        from . import factors as fx  # noqa: PLC0415

        inert = fx.inert_keys_in(cfg)
        if inert:
            item["not_effective"] = inert
        out.append(item)
    return out


def preset_groups() -> dict[str, list[str]]:
    """按 group 归类的预设名清单。"""
    out: dict[str, list[str]] = {}
    for name, body in load_presets().items():
        out.setdefault(body.get("group", "其他"), []).append(name)
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
        return "company_64t4r_multicell"
    if any(w in text for w in ("最小", "冒烟", "快速", "先跑通", "smoke")):
        return "single_cell_4t4r"
    # 兜底走**本地默认配置**：真实 AAU（1 驱 3 / 192 阵子 / 0.5λ 水平 0.67λ 垂直）
    # + n41 2.6 GHz / 30 kHz / 272 RB / 4R 下行。
    # 旧的 single_cell_64t4r 是 3.5 GHz + legacy 独立阵元模型，留着做对照，
    # 但不该再当默认。2026-07-31 旧内核消融曾测到吞吐 +27%、边缘用户
    # +61%；它只证明阵列模型影响很大，不能作为当前版本的通用百分比。
    return "company_64t4r"


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
    round_no: int = 1  # 当前问到第几轮
    created_at: float = field(default_factory=time.time)
    history: list[str] = field(default_factory=list)
    # --- 访谈状态（interview.py）；旧草稿没有这些键时取默认值 ---
    form: str | None = None            # 结论形态
    family: str | None = None          # 目标量属于哪张因子表
    sweep: dict[str, Any] | None = None  # 扫描变量与取值
    sweep_error: str | None = None  # 无效回答持久阻断，必须由用户重新给合法档位解除
    provenance: dict[str, str] = field(default_factory=dict)  # 键 → 谁定的
    brief_evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)  # 原话里平台做不到的条件

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
            "round_no": self.round_no,
            "created_at": self.created_at,
            "history": self.history,
            "form": self.form,
            "family": self.family,
            "sweep": self.sweep,
            "sweep_error": self.sweep_error,
            "provenance": self.provenance,
            "brief_evidence": self.brief_evidence,
            "blockers": self.blockers,
        }


def _draft_path(draft_id: str) -> Path:
    # 与 dataset_id 同一纪律：越界读/写必须拦在拼路径之前。
    if not _HANDLE_RE.fullmatch(str(draft_id)):
        raise ValueError(f"非法 draft_id {draft_id!r}：只允许 [A-Za-z0-9_-]")
    return drafts_dir() / f"{draft_id}.json"


_CARRIER_GEOMETRY_KEYS = frozenset({
    "bandwidth_hz", "subcarrier_spacing", "num_rb", "bwp_start_rb",
    "rbg_size_config",
})
_NUM_RB_DERIVATION_KEYS = frozenset({"bandwidth_hz", "subcarrier_spacing"})
_SRS_BANDWIDTH_KEYS = (
    "srs_c_srs", "srs_b_srs", "srs_b_hop", "srs_n_rrc",
)


def _apply_dependent_overrides(
    params: dict[str, Any], overrides: dict[str, Any]
) -> list[str]:
    """应用 override，并清掉已经失效的载波/SRS 派生量。

    典型陷阱是从 100 MHz 预置 preset 起步，只改 ``bandwidth_hz=20e6``，
    却把 preset 里的 ``num_rb=272`` 一起带过去。ChannelHub 会忠实生成 272 RB，
    所以带宽字段看着是 20 MHz，实际系统仍按 100 MHz 跑，而且不会报错。

    同一轮显式给出的值视为用户有意自定义，不替用户删除。
    """
    updates = dict(overrides)
    changed_geometry = {
        key for key in _CARRIER_GEOMETRY_KEYS
        if key in updates and params.get(key) != updates[key]
    }
    notes: list[str] = []
    changed_num_rb_inputs = changed_geometry & _NUM_RB_DERIVATION_KEYS
    if changed_num_rb_inputs and "num_rb" not in updates and "num_rb" in params:
        old = params.pop("num_rb")
        notes.append(
            f"自动清除 num_rb={old!r}：带宽/SCS 已改，重新按标准表推导"
        )
    if changed_geometry:
        for key in _SRS_BANDWIDTH_KEYS:
            if key not in updates and key in params:
                old = params.pop(key)
                notes.append(
                    f"自动清除 {key}={old!r}：载波几何已改，交给 SRS 资源选择器重算"
                )
    if "subcarrier_spacing" in changed_geometry:
        for key, label in (
            ("srs_periodicity", "SRS"),
            ("csirs_periodicity", "CSI-RS"),
        ):
            if key in params and key not in updates:
                notes.append(
                    f"保留 {key}={params[key]!r} 个 slot：{label} 周期的 slot 数未改，"
                    "但 SCS 改变后对应的毫秒数会变化；如需保持物理周期请显式覆盖"
                )
    params.update(updates)
    return notes


def save_draft(d: Draft) -> None:
    p = _draft_path(d.draft_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子替换：进程中断不会留下半截 JSON
    import tempfile  # noqa: PLC0415

    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(d.as_dict(), ensure_ascii=False, indent=2))
        os.replace(tmp, p)
    except BaseException:
        os.unlink(tmp)
        raise


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
    """从自然语言意图建一份提案。原话里已经给出的条件直接写进草稿，不再重问。"""
    from . import interview as iv  # noqa: PLC0415

    profile = dec.classify_intent(intent)
    brief = iv.read_brief(intent)
    form, _ = iv.classify_form(intent, brief)
    family = iv.metric_family(intent)
    preset_name = preset or _guess_preset(intent, profile)
    # 目标量落在干扰/速率上却挑了单小区骨架：没有邻区就没有干扰。原话明说单小区除外。
    if (preset is None and family and brief.params.get("num_sites") != 1
            and int(load_presets().get(preset_name, {}).get("config", {}).get("num_sites", 1) or 1) <= 1):
        preset_name = "company_64t4r_multicell"
    presets = load_presets()
    if preset is not None and preset_name not in presets:
        raise ValueError(
            f"未知预设 {preset!r}；可用：{sorted(presets)}。"
            "不给 preset 时会按意图自动挑选")
    if preset_name not in presets:
        preset_name = "single_cell_64t4r"

    params: dict[str, Any] = dict(presets.get(preset_name, {}).get("config", {}))
    if preset is None:
        # 自动选骨架时，任务分类器可以把通用 preset 收敛到该任务需要的
        # 方向/测量量。用户显式点名 preset 时则相反：preset 是已经作出的
        # 配置选择，classifier hint 只能补缺，不能把 company_64t4r 的 BOTH
        # 悄悄改成 DL，导致“上行 SRS 估计”实际变成下行 CSI-RS 估计。
        params.update(profile.config_hints)
    else:
        for key, value in profile.config_hints.items():
            params.setdefault(key, value)

    # preset 若已给具体天线数，就按它反推标签，不要让默认标签把 preset 冲掉
    inferred = antenna_label(params)
    if inferred:
        params["antenna_preset"] = inferred

    # 决策点的默认值补进来（preset 已给的不覆盖）
    for d in dec.decisions_for(profile, limit=99):
        translated, _ = translate({d.key: d.default})
        for k, v in translated.items():
            params.setdefault(k, v)
        if d.key in _SUPERRAN_ONLY:
            params.setdefault(d.key, d.default)

    params.setdefault("num_samples", 200)
    params.setdefault("seed", 42)

    provenance: dict[str, str] = {}
    dependency_notes: list[str] = []
    if brief.params:
        dependency_notes += _apply_dependent_overrides(params, dict(brief.params))
        provenance.update({k: iv.SOURCE_SAID for k in brief.params})
    user_set: list[str] = sorted(brief.params)
    if overrides:
        dependency_notes += _apply_dependent_overrides(params, overrides)
        user_set = sorted(set(user_set) | set(overrides))
        provenance.update({k: iv.SOURCE_SAID for k in overrides})
    design = dict(brief.design)
    provenance.update({k: iv.SOURCE_SAID for k in brief.design})

    d = Draft(
        draft_id="d_" + uuid.uuid4().hex[:8],
        intent=intent,
        task=profile.task,
        task_label=profile.label,
        preset=preset_name,
        params=params,
        user_set=user_set,
        design=design,
        history=[f"由意图创建，场景骨架 {preset_name}", *dependency_notes],
        form=form,
        family=family,
        sweep=brief.sweep,
        provenance=provenance,
        brief_evidence=list(brief.evidence),
        blockers=list(brief.unsupported),
    )
    _scenario_height(d)
    save_draft(d)
    return d, profile


def _scenario_height(d: Draft) -> None:
    """原话直接指定场景也落实站高；显式用户站高始终保留。"""
    from . import interview as iv  # noqa: PLC0415

    scenario = str(d.params.get("scenario", ""))
    height = 10.0 if scenario.startswith("UMi") else 25.0 if scenario.startswith("UMa") else None
    if height is not None and ("tx_height_m" not in d.params
                               or d.provenance.get("tx_height_m") == iv.SOURCE_DERIVED):
        d.params["tx_height_m"] = height
        d.provenance["tx_height_m"] = iv.SOURCE_DERIVED


def _apply_answer(d: Draft, key: str, value: Any, changes: list[str], *, recommended: bool = False) -> None:
    """把一个设计层回答落到草稿：记录、生效选项改动、更新扫描计划。所有入口共用这一条路径。

    审核 F3/F4/F7 的教训：回答只记进 design 而不进执行配置，历史与实际就会分叉。
    """
    from . import interview as iv  # noqa: PLC0415

    if value is None or value == "":  # False / 0 是合法回答（例如“关掉自适应”）
        return
    if key == "sweep_values":
        if recommended and d.sweep_error:
            return  # 推荐不能替用户纠正有歧义或量纲错误的输入。
        skey = (d.sweep or {}).get("key") or iv.sweep_key_from_intent(d.intent, iv.Brief()) or "?"
        nums, problems = iv.parse_sweep_values(str(value), skey)
        if not nums or problems:
            d.sweep_error = (f"扫描取值「{value}」" + ("；".join(problems) if problems else "里没有具体数值")
                             + "，未记为已答；请给出合法数值")
            changes.append(d.sweep_error)
            return
        d.sweep_error = None
        old = (d.sweep or {}).get("values")
        d.sweep = {"key": skey, "values": nums}
        changes.append(f"扫描 {skey}: {old} → {nums}")
    if key == "load_owner":
        # 先答归属、后给档位也要成立（审核 R2-6）：没有取值时先把扫描变量存下来。
        d.sweep = {**(d.sweep or {"values": []}), "key": str(value)}
        changes.append(f"负载归属确认为 {value}，扫描变量改为 {value}")
    d.design[key] = str(value)
    d.provenance[key] = iv.SOURCE_ANSWERED
    changes.append(f"实验设计 {key}: {str(value)[:40]}")
    if key == "form" and str(value) in iv.FORMS:
        d.form = str(value)
    eff = iv.answer_effects(key, value, d.family)
    if eff.get("overrides"):
        apply: dict[str, Any] = {}
        for kk, vv in eff["overrides"].items():
            # 原话或用户直接给过的值不被组合推荐覆盖（审核 F3：53 dBm 被部署推荐改成 46）。
            # 只有原话或用户直接给的值才锁定；由上一个组合选项带出的值可以随改选更新（审核 R2-5）。
            if d.provenance.get(kk) in {iv.SOURCE_SAID} or (
                    kk in d.user_set and d.provenance.get(kk) == iv.SOURCE_ANSWERED
                    and kk != key):
                if d.params.get(kk) != vv:
                    changes.append(f"保留用户给定的 {kk}={d.params.get(kk)!r}；"
                                   f"与 {key}={value} 的推荐 {vv!r} 冲突，未覆盖")
                continue
            apply[kk] = vv
        before = {kk: d.params.get(kk) for kk in apply}
        changes.extend(_apply_dependent_overrides(d.params, apply))
        for kk, vv in apply.items():
            if before.get(kk) != vv:
                changes.append(f"{kk}: {before.get(kk)!r} → {vv!r}（由 {key}={value} 带出）")
            d.provenance[kk] = iv.SOURCE_DERIVED
            if kk not in d.user_set:
                d.user_set.append(kk)
    if eff.get("note"):
        changes.append(eff["note"])


def revise_draft(
    draft_id: str,
    overrides: dict[str, Any] | None = None,
    design: dict[str, str] | None = None,
    *,
    accept_recommended: bool = False,
) -> tuple[Draft, dec.TaskProfile, list[str]]:
    """差分修正：只说改什么，不用重述整个需求。

    ``design`` 记录实验设计层的回答，选项自带的配置改动当场生效。
    ``accept_recommended=True`` 对应用户说“按推荐跑”：逐层展开依赖，每一层的回答
    先生效再算下一层推荐（审核 F4：选了微站，站距档位也要随之改）；原话给定的值
    不被覆盖；只能由用户本人给的内容（预期、预期增益、自定义扫描取值）不代答；
    时间轴等必需的配置调整一并落实。
    """
    from . import interview as iv  # noqa: PLC0415

    d = load_draft(draft_id)
    profile = next((p for p in dec.TASK_PROFILES if p.task == d.task), dec.TASK_PROFILES[-1])
    changes: list[str] = []

    raw_overrides = dict(overrides or {})
    before = dict(d.params)
    changes.extend(_apply_dependent_overrides(d.params, raw_overrides))
    for k, v in raw_overrides.items():
        if before.get(k) != v:
            changes.append(f"{k}: {before.get(k)!r} → {v!r}")
        if k not in d.user_set:
            d.user_set.append(k)
        d.provenance[k] = iv.SOURCE_ANSWERED
    for k, v in (design or {}).items():
        _apply_answer(d, k, v, changes)

    if accept_recommended:
        for _ in range(8):  # 依赖链逐层展开：答完一层，下一层才进入前沿
            st = interview_state(d, profile)
            todo = [q for q in st["pending"]
                    if not q.user_content and q.key not in iv.USER_CONTENT_KEYS]
            if not todo:
                break
            progressed = False
            for q in todo:
                rec = next((o["value"] for o in q.options if o.get("recommended")), None)
                if rec is None:
                    continue
                progressed = True
                if q.layer == "param":
                    old = d.params.get(q.key)
                    changes.extend(_apply_dependent_overrides(d.params, {q.key: rec}))
                    if old != rec:
                        changes.append(f"{q.key}: {old!r} → {rec!r}（按推荐）")
                    if q.key not in d.user_set:
                        d.user_set.append(q.key)
                    d.provenance[q.key] = iv.SOURCE_ANSWERED
                else:
                    _apply_answer(d, q.key, rec, changes, recommended=True)
            if not progressed:
                break
        for adj in required_adjustments(d):
            old = d.params.get(adj["key"])
            d.params[adj["key"]] = adj["to"]
            changes.append(f"{adj['key']}: {old!r} → {adj['to']!r}（{adj['why']}）")

    _scenario_height(d)
    # 用户回应过一轮就推进轮次；即使只是“确认默认值”也要推进，否则会重复问。
    if overrides or design or accept_recommended:
        d.round_no += 1
        d.history.append(
            f"第 {d.round_no - 1} 轮：" + ("；".join(changes) if changes else "确认默认值")
        )
    save_draft(d)
    return d, profile, changes


def _typed(value: str) -> Any:
    """design 里的回答按字符串存；给系统仿真时还原成布尔/数值。"""
    low = str(value).strip().lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        f = float(low)
        return int(f) if f.is_integer() and "." not in low else f
    except ValueError:
        return value


# 系统仿真每 UE 至少要 8 个时间相关快照（可由多个单时隙样本组成）；否则 CSI 老化恒为 0、
# PF 分集被低估（skills/channel-sim/references/system-sim.md）。
MIN_SNAPSHOTS_PER_UE = 8


def snapshots_per_ue(params: dict[str, Any], num_samples: int | None = None) -> float:
    n = int(num_samples if num_samples is not None else params.get("num_samples", 200) or 0)
    ues = max(int(params.get("num_ues", 1) or 1), 1)
    slots = max(int(params.get("num_slots_per_sample", 1) or 1), 1)
    return n * slots / ues


def needs_time_axis(d: Draft) -> bool:
    return d.family == "dl_experience"


def required_adjustments(d: Draft) -> list[dict[str, Any]]:
    """为满足物理前提必须做、且用户没有限定预算时才可以自动做的配置调整。"""
    from . import interview as iv  # noqa: PLC0415

    out = []
    if needs_time_axis(d) and snapshots_per_ue(d.params) < MIN_SNAPSHOTS_PER_UE:
        if d.provenance.get("num_samples") not in {iv.SOURCE_SAID, iv.SOURCE_ANSWERED}:
            ues = max(int(d.params.get("num_ues", 1) or 1), 1)
            slots = max(int(d.params.get("num_slots_per_sample", 1) or 1), 1)
            need = -(-MIN_SNAPSHOTS_PER_UE * ues // slots)
            out.append({"key": "num_samples", "from": d.params.get("num_samples"), "to": need,
                        "why": f"系统仿真每 UE 需 ≥{MIN_SNAPSHOTS_PER_UE} 个时间相关快照"
                               f"（{ues} UE × {slots} 时隙/样本）"})
    return out


def interview_blockers(d: Draft, num_samples: int | None = None) -> list[dict[str, str]]:
    """访谈层发现、会让这次实验答非所问的阻断项。提案与 sr_generate 共用。"""
    out: list[dict[str, str]] = []
    if d.sweep_error:
        out.append({"severity": "block", "key": "sweep_values", "message": d.sweep_error,
                    "suggestion": "请通过 sr_revise 的 sweep_values 重新给出合法档位；按推荐跑不会替换这次无效回答"})
    for msg in d.blockers:
        out.append({"severity": "block", "key": "request", "message": msg,
                    "suggestion": "改成支持的条件，或确认放弃这一项"})
    # 阵型按当前状态判断：改成不支持的要阻断，改回支持的就解锁（审核 R2-3）。
    requested = [str(d.params["antenna_preset"])] if d.params.get("antenna_preset") else []
    if d.sweep and d.sweep.get("key") == "antenna_preset":
        requested += [str(v) for v in d.sweep.get("values", [])]
    bad = sorted({a for a in requested if a not in _ANTENNA_PRESETS})
    if bad:
        out.append({"severity": "block", "key": "antenna_preset",
                    "message": f"阵型 {bad} 不在支持列表 {sorted(_ANTENNA_PRESETS)}；"
                               "不能静默换成预设阵型生成",
                    "suggestion": "改成支持的阵型"})
    if d.design.get("indoor_users") == "need_o2i":
        out.append({"severity": "block", "key": "indoor_users",
                    "message": "你要求必须有室内穿透损耗（O2I），平台当前没有这个机制；"
                               "用现有模型跑会得到全室外的结果，答非所问。",
                    "suggestion": "先实现 O2I，或改为“只比较 SIR 与相对趋势”"})
    if d.design.get("edf_meaning") == "deadline_first":
        out.append({"severity": "block", "key": "edf_meaning",
                    "message": "你要比的是最早截止时间优先（Earliest Deadline First），"
                               "本平台只有最早排空优先（Earliest Drain First）。",
                    "suggestion": "先实现截止时间调度器，或改比平台已有的调度器"})
    from . import interview as _iv  # noqa: PLC0415

    _skey = (d.sweep or {}).get("key") or _iv.sweep_key_from_intent(d.intent, _iv.Brief())
    if d.sweep:
        for problem in _iv.validate_sweep(_skey, d.sweep.get("values", [])):
            out.append({"severity": "block", "key": "sweep_values", "message": problem,
                        "suggestion": "通过 sweep_values 给出合法档位；原话中的越界值不会被推荐覆盖"})
    if _skey == "load?" and d.form == "sweep_condition":
        out.append({"severity": "block", "key": "load_owner",
                    "message": "原话里的“负载”没说是本小区还是邻区：前者改变排队竞争，后者改变干扰。",
                    "suggestion": "回答 load_owner（本小区 / 邻区）"})
    if needs_time_axis(d):
        spu = snapshots_per_ue(d.params, num_samples)
        if spu < MIN_SNAPSHOTS_PER_UE:
            from . import interview as iv  # noqa: PLC0415

            fixed = d.provenance.get("num_samples") in {iv.SOURCE_SAID, iv.SOURCE_ANSWERED} \
                or num_samples is not None
            out.append({
                "severity": "block", "key": "num_samples",
                "message": (f"每 UE 只有 {spu:.2f} 个快照，系统仿真需要 ≥{MIN_SNAPSHOTS_PER_UE} 个"
                            "时间相关快照；否则 CSI 老化恒为 0、PF 分集被低估。"
                            + ("你给定的样本预算与这一前提冲突。" if fixed else "")),
                "suggestion": ("增加样本数或减少 UE 数；或确认接受单快照（不能用于老化/调度比较）"
                               if fixed else "sr_revise(accept_recommended=True) 会按需调整样本数"),
            })
    return out


def draft_issues(d: Draft, profile: dec.TaskProfile, num_samples: int | None = None) -> list[dict[str, str]]:
    """物理组合检查 + 访谈阻断项。"""
    return dec.check_guards(profile, d.params) + interview_blockers(d, num_samples)


def system_params(d: Draft) -> dict[str, Any]:
    """已确认的系统层回答 → 直接传给 sr_system_sim 的参数，避免 Agent 转述时丢失。"""
    from . import factors as fx  # noqa: PLC0415

    if not d.family:
        return {}
    out: dict[str, Any] = {}
    sweep_key = (d.sweep or {}).get("key")
    for f in fx.factors_for(d.family):
        if f.config_key == sweep_key:
            continue  # 扫描变量按各臂取值传，不固定成一个值（审核 F2）
        if f.layer == "system" and f.config_key and f.key in d.design:
            out[f.config_key] = _typed(d.design[f.key])
    if d.family == "dl_experience" and int(d.params.get("num_sites", 1) or 1) > 1:
        # 多小区数据只调度一个服务小区：由 sr_system_sim 按实际撒点在中心站扇区里挑
        # UE 最多（且 ≥2 个）的小区，不盲填常量（审核 R2-7：小区 1 可能没有 UE）。
        out["serving_cell"] = "auto"
    return out


def interview_state(d: Draft, profile: dec.TaskProfile) -> dict[str, Any]:
    """访谈的当前状态：台账、这一轮的问题、全部待问问题（按依赖与影响排好）。"""
    from . import interview as iv  # noqa: PLC0415

    ch_cfg, _ = resolved_config(d)
    answered = set(d.design) | set(d.user_set) | set(d.provenance)
    sweep_key = (d.sweep or {}).get("key") if d.sweep else iv.sweep_key_from_intent(
        d.intent, iv.Brief())
    led = iv.ledger(d.family, ch_cfg, d.provenance, sweep_key=sweep_key, answers=d.design)
    extra_design = []
    if d.form == "compare_methods":
        for key, prio in (("metric", 1), ("effect_size", 3)):
            q = dec._DESIGN.get(key)
            if q is not None and not (key == "metric" and "metric_words" in d.design):
                extra_design.append({**q.as_dict(), "priority": prio})
    extra_params = [x.as_dict() for x in dec.decisions_for(profile, limit=99)]
    common = dict(intent=d.intent, form=d.form, family=d.family,
                  brief=iv.Brief(params={}, design=dict(d.design), sweep=d.sweep),
                  answered=answered, led=led, sweep_key=sweep_key,
                  extra_design=extra_design, extra_params=extra_params, params=ch_cfg,
                  answers=dict(d.design))
    return {
        "sweep_key": sweep_key, "ledger": led, "config": ch_cfg,
        "this_round": iv.frontier(**common),
        "pending": iv.frontier(**common, limit=None),
    }


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
    from . import interview as iv  # noqa: PLC0415

    ch_cfg, own = resolved_config(d)

    # 本轮问什么：由结论形态与目标量的影响因子表决定（interview.py），
    # 不再由任务模板决定。原话、用户回答过的都不问。
    st = interview_state(d, profile)
    sweep_key, led = st["sweep_key"], st["ledger"]
    this_round, pending = st["this_round"], st["pending"]
    blocking = [
        {"key": q.key, "question": q.question, "default": q.blocking_default,
         "recommended": next((o["value"] for o in q.options if o.get("recommended")), None)}
        for q in pending if q.blocking_default is not None
    ]

    round_q = []
    for q in this_round:
        item = q.as_dict()
        if q.layer == "param":
            item["current"] = d.params.get(q.key)
            item["user_specified"] = q.key in d.user_set
        else:
            item["answered"] = d.design.get(q.key)
        round_q.append(item)
    design = [q for q in round_q if q["layer"] == "design"]
    questions = [q for q in round_q if q["layer"] == "param"]
    rnd = {
        "round": d.round_no,
        "focus": "前沿问题" if round_q else "已问完",
        "rationale": (
            "只问前提已满足、会改变结论、且只能由人回答的问题；可查的事实平台自己查，"
            "其余假设列在 assumption_ledger 里，用户可随时改。"
            if round_q else "影响结论的假设都已确认或已列明，可以生成。"
        ),
        "has_more": len(pending) > len(this_round),
        "remaining_count": max(len(pending) - len(this_round), 0),
        "target_rounds": f"每轮最多 {iv.MAX_PER_ROUND} 问，通常 1~2 轮",
        "remaining_all_optional": len(pending) <= len(this_round),
        "stop_hint": (
            "用户说「按推荐跑」→ sr_revise(draft_id, accept_recommended=True)，所有待问问题取推荐项；"
            "用户说「默认就行」→ 停止提问，但 blocking_defaults 里的项默认会让研究失效，"
            "必须改用推荐值并告诉用户，其余沉默假设照 assumption_ledger 复述，不当作已确认。"
        ),
    }

    issues = draft_issues(d, profile)
    presets = load_presets()

    factor_block = None
    if profile.factor_metric:
        from . import factors as fx  # noqa: PLC0415

        factor_block = fx.checklist(profile.factor_metric, cfg=ch_cfg)

    return {
        "draft_id": d.draft_id,
        "task": d.task,
        "task_label": d.task_label,
        "preset": d.preset,
        "preset_label": presets.get(d.preset, {}).get("label", d.preset),
        "preset_summary": presets.get(d.preset, {}).get("summary", ""),
        # --- 读懂了什么 ---
        "form": d.form,
        "form_label": iv.FORMS[d.form]["label"] if d.form else None,
        "conclusion_sentence": iv.FORMS[d.form]["sentence"] if d.form else None,
        "brief": {"evidence": d.brief_evidence, "sweep": d.sweep},
        "restatement": iv.restatement(d.form, d.family, iv.Brief(evidence=d.brief_evidence),
                                      sweep_key, led, d.intent),
        "glossary_notes": iv.glossary_notes(d.intent),
        # 开跑前必须说清、但不需要用户选的事实（例如生成层变量没有配对判决）。
        "upfront_notices": iv.upfront_notices(d.form, sweep_key, d.intent),
        "assumption_ledger": {k: v for k, v in led.items() if not k.startswith("_")},
        # 平台默认会让这次研究失效的项：用户说“默认”时也不能沿用。
        "blocking_defaults": blocking,
        # 已确认的系统层回答，原样传给 sr_system_sim（信道生成参数在 resolved_config）。
        "system_params": system_params(d),
        "needs_user_content": sorted({q.key for q in pending if q.user_content}),
        # 为满足物理前提需要做的配置调整（例如每 UE ≥8 快照）；按推荐跑时自动落实。
        "required_adjustments": required_adjustments(d),
        # --- 本轮提问 ---
        "round": rnd["round"],
        "round_focus": rnd["focus"],
        "round_rationale": rnd["rationale"],
        # round_questions 是本轮全部问题的合并视图（设计层在前），
        # 调用方直接照着问即可，不必自己拼两个列表。
        # 第 1 轮通常只有设计层问题，questions 会是空的，这是正常的。
        "round_questions": [{**q, "layer": "design"} for q in design]
        + [{**q, "layer": "param"} for q in questions],
        "design_questions": design,
        "questions": questions,
        "has_more_rounds": rnd["has_more"],
        "remaining_count": rnd["remaining_count"],
        "target_rounds": rnd["target_rounds"],
        "remaining_all_optional": rnd["remaining_all_optional"],
        "stop_hint": rnd["stop_hint"],
        # --- 参考信息 ---
        "also_configurable": dec.also_configurable(profile),
        "suggested_sweeps": dec.sweep_suggestions(profile),
        "pitfalls": list(profile.pitfalls),
        # 结论落在哪个物理量上，就把决定它的因素按影响排好给出来：已建模的问
        # 取值，未建模的必须告诉用户会偏向哪边。这是提问的知识来源，不是问卷。
        "factor_checklist": factor_block,
        "issues": issues,
        "ready_to_go": not any(i["severity"] == "block" for i in issues),
        "can_generate_now": True,
        "resolved_config": ch_cfg,
        "superran_params": own,
        "user_specified": d.user_set,
        "answered_design": dict(d.design),
        "hint": (
            "**目标 2 轮问完，最多 3 轮。** 这一轮只问 round_questions 里的这几个，"
            "别把 also_configurable 里的也问了。设计层和参数层互不依赖，"
            "已经合并在同一轮，照着列表一次性问出来即可。"
            "每个问题都带 options，把选项编号列出来并标明推荐项（recommended=true），"
            "最后留一句「或者你直接说」。"
            "用户答完后再调 sr_revise 拿下一轮；has_more_rounds 为 false "
            "或用户说「随便」就直接生成。"
            "remaining_all_optional 为 true 时，下一轮请包装成一句可跳过的话"
            "（「剩下这些都有合理默认值，要不要直接跑？」），不要再摆一屏选项。"
            + (
                "**有 factor_checklist 时**：用户没提到的 impact=1 因素要主动说出平台"
                "的取值；must_disclose 里的未建模项必须告诉用户会让结果偏向哪边。"
                "先让用户写下预期，再跑 sr_probe_scenario 对照，偏差大先回到清单"
                "查假设，对齐后再正式生成。"
                if factor_block else ""
            )
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
        "以下由 superran 执行：",
        "",
        "```yaml",
        yaml.safe_dump(ch_cfg, allow_unicode=True, sort_keys=True).rstrip(),
        "```",
    ]
    if own:
        lines += ["", "superran 自用参数：", "", "```yaml",
                  yaml.safe_dump(own, allow_unicode=True, sort_keys=True).rstrip(), "```"]
    if d.history:
        lines += ["", "## 修改记录", *[f"- {h}" for h in d.history]]
    return "\n".join(lines)
