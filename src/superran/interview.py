"""把一句仿真需求问清楚：先读懂原话，再只问会改变结论、而平台又在替人拍的事。

旧流程按关键词套任务模板提问。2026-09-26 用 5 句真实表述做基线，暴露四类错误：

* **不读原话**："跟 Type II 码本比，单小区 64T4R" 说完了，第一轮还问"跟什么比"；
* **只有一种结论形态**：结论模板写死"方法 A 相对基线 B"，于是"只要 20 个 PDP"
  被问"用什么指标判断好坏"，"看站距对干扰的影响"被问码本基线；
* **不看结论取决于什么**："密集城区下行 SINR 分布"被分到通用模板、配了单小区
  ——干扰根本不存在；
* **平台替人做的假设没有台账**：46 dBm、全室外、邻区满载都是悄悄定的。

这里按 grilling（决策树 + 前沿 + 可查事实自己查 + 不留沉默假设）与 superpowers
brainstorming（先复述理解、先给推荐、按任务轻重走不同路径）改写，并利用 SuperRAN
自己就是仿真器这一点：**问什么由目标量的影响因子表决定**（factors.py，可对账），
而不是由任务名决定。

流程产物（都进 ``sr_plan`` 的返回）：

* ``brief``——从原话识别出的条件，每条带原文依据，直接写进草稿，不再重问；
* ``form``——结论形态：交付数据 / 刻画一个量 / 扫一个条件 / 比较方法；
* ``ledger``——决定目标量的每个假设现在取什么值、谁定的；``silently_assumed``
  是影响大却没人确认过的那几项；
* ``frontier``——这一轮该问的 ≤3 个问题：前提已满足、答案会改变结论、且只能由人回答。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import factors as fx

# ---------------------------------------------------------------------------
# 1. 读原话
# ---------------------------------------------------------------------------


@dataclass
class Brief:
    """从原话里识别出的东西。宁可漏认，不许错认：每条都附原文依据。"""

    params: dict[str, Any] = field(default_factory=dict)
    design: dict[str, str] = field(default_factory=dict)
    sweep: dict[str, Any] | None = None
    evidence: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)  # 原话里平台做不到的条件

    def as_dict(self) -> dict[str, Any]:
        return {"params": self.params, "design": self.design, "sweep": self.sweep,
                "evidence": self.evidence, "unsupported": self.unsupported}


_METHOD_WORDS = ("算法", "方案", "码本", "预编码", "调度器", "压缩", "估计器", "估计算法",
                 "我的方法", "新方法", "type i", "type ii", "svd", "mmse", "zf", "pf",
                 "edf", "cort", "网络", "模型训练")
_COMPARE_WORDS = ("比", "对比", "相对", "vs", "提升", "增益", "优于", "基线", "好不好")
_DELIVER_WORDS = ("只要", "只需要", "给我", "交付", "导出", "数据集")
# 交付数据必须真的在要数据：出现这些名词才算（“给我看看站距的影响”不是交付）。
_DATA_NOUNS = ("信道", "数据", "样本", "pdp", "pmi", "csi", "快照", "矩阵")
_ANALYSIS_CUES = ("看看", "分布", "变化", "影响", "评估", "分析", "怎么变", "趋势", "统计")
_CHANGE_WORDS = ("变化", "影响", "改到", "改成", "扫", "随", "不同", "对比", "比较", "差多少",
                 "掉多少")
_CONDITION_KEYS = {
    "站间距": "isd_m", "站距": "isd_m", "isd": "isd_m",
    "速度": "ue_speed_kmh", "车速": "ue_speed_kmh",
    "srs 周期": "srs_period_ms", "srs周期": "srs_period_ms",
    "邻区负载": "neighbor_prb_util", "本小区负载": "target_prb_utilization",
    "服务小区负载": "target_prb_utilization", "负载": "load?",
    "发射功率": "tx_power_dbm", "功率": "tx_power_dbm",
    "带宽": "bandwidth_hz", "站数": "num_sites",
}

# 这些键换一个值就是换一批信道数据：不同取值是不同数据集，目前没有跨数据集的
# 配对判决，只能给逐条件的分布对照。system 层的键可以在同一批数据、同一批随机流
# 上换参数，能做配对统计。**这件事要在开跑前告诉人**，不是跑完才发现。
SWEEP_LAYER: dict[str, str] = {
    "isd_m": "generation", "num_sites": "generation", "tx_power_dbm": "generation",
    "ue_speed_kmh": "generation", "bandwidth_hz": "generation",
    "carrier_freq_hz": "generation", "channel_model": "generation",
    "antenna_preset": "generation", "scenario": "generation",
    "srs_period_ms": "system", "neighbor_prb_util": "system", "scheduler": "system",
    "target_prb_utilization": "system", "load?": "system",
    "mu_enabled": "system", "traffic_model": "system",
}

_METRIC_FAMILY_WORDS = (
    ("dl_experience", ("边缘速率", "体验速率", "吞吐", "速率", "时延", "完成时间", "bler",
                       "边缘用户", "容量")),
    ("dl_interference", ("干扰", "iot", "sinr", "sir", "底噪", "噪声抬升")),
)


def _num_list(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]


_NUM = r"[-−]?\d+(?:\.\d+)?(?:e[+-]?\d+)?"
# 扫描变量的合法范围（换算到配置单位之后）
_SWEEP_RANGE = {
    "isd_m": (10.0, 10000.0), "tx_power_dbm": (-30.0, 60.0), "bandwidth_hz": (1e6, 400e6),
    "neighbor_prb_util": (0.0, 1.0), "target_prb_utilization": (0.0, 1.0),
    "srs_period_ms": (1.0, 640.0), "ue_speed_kmh": (0.0, 600.0),
}


_ANT_TOKEN = r"(\d+)\s*t(?:\s*(\d+)\s*r)?(?![a-z0-9])"
_UNITS = {
    "isd_m": {"m"}, "tx_power_dbm": {"dbm"},
    "bandwidth_hz": {"ghz", "mhz", "khz", "hz"},
    "neighbor_prb_util": {"%"}, "target_prb_utilization": {"%"}, "load?": {"%"},
    "srs_period_ms": {"ms"}, "ue_speed_kmh": {"km/h"},
}
# Read the entire unit before deciding which quantity owns it: MHz must never
# backtrack to an unlabelled number in a power list.
_QUANTITY = re.compile(r"(" + _NUM + r")\s*(km/h|(?!vs(?![a-z]))[a-z]+|%)?(?![\d.a-z%])")


def parse_sweep_values(text: str, key: str) -> tuple[list[Any], list[str]]:
    """扫描取值的唯一解析器：原话、后续回答、执行前检查都用它（审核 R3 要求同一套规则）。

    数值量换算到配置单位（科学计数法、百分比、MHz/GHz/km/h）；阵型保留完整标签
    （“32T4R/64T4R”不拆成数字，审核 R3-4）。返回 (取值, 问题)，问题来自 validate_sweep。
    """
    raw = str(text).lower().replace("−", "-")
    if key == "antenna_preset":
        vals: list[Any] = [f"{m.group(1)}T{m.group(2) or 4}R" for m in re.finditer(_ANT_TOKEN, raw)]
        return vals, validate_sweep(key, vals)
    tokens = [(m.group(1), m.group(2) or "") for m in _QUANTITY.finditer(raw)]
    invalid_units = sorted({unit for _, unit in tokens if unit and unit not in _UNITS.get(key, set())})
    if invalid_units:
        return [], [f"{key} 的单位 {invalid_units} 不匹配；请使用 {sorted(_UNITS.get(key, set()))}"]
    vals = []
    for i, (num, unit) in enumerate(tokens):
        # A trailing unit belongs to the preceding unlabelled list (0.02/0.1 GHz).
        # Explicit units keep their own scale (20 MHz/0.1 GHz).
        if not unit:
            unit = next((u for _, u in tokens[i + 1:] if u), "") or next(
                (u for _, u in reversed(tokens[:i]) if u), "")
        v = float(num)
        if unit == "%" or (key in {"neighbor_prb_util", "target_prb_utilization", "load?"} and v > 1.0):
            v /= 100.0
        elif key == "bandwidth_hz":
            v *= {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1.0}.get(unit, 1e6 if v < 1e4 else 1.0)
        vals.append(v)
    return vals, validate_sweep(key, vals)


def validate_sweep(key: str, values: list[Any]) -> list[str]:
    """扫描取值的唯一校验器：类型、范围、阵型是否支持。原话与修订的值都在执行前再过一遍。"""
    if not values:
        return []
    if key == "antenna_preset":
        from .plan import _ANTENNA_PRESETS  # noqa: PLC0415

        bad = [v for v in values if str(v) not in _ANTENNA_PRESETS]
        return [f"阵型 {bad} 不在支持列表 {sorted(_ANTENNA_PRESETS)}"] if bad else []
    lo, hi = _SWEEP_RANGE.get("neighbor_prb_util" if key == "load?" else key,
                              (float("-inf"), float("inf")))
    try:
        if key == "target_prb_utilization":
            # The serving-cell load calibrator accepts an open (0,1) target;
            # neighbour activity legitimately includes 0 and 1.
            bad = [v for v in values if not (0.0 < float(v) < 1.0)]
        else:
            bad = [v for v in values if not (lo <= float(v) <= hi)]
    except (TypeError, ValueError, OverflowError):
        return [f"{key} 的档位必须是具体数值"]
    interval = "(0, 1)" if key == "target_prb_utilization" else f"[{lo:g}, {hi:g}]"
    return [f"{key} 的取值 {bad} 超出合法范围 {interval}"] if bad else []


# 原话里“条件词 + 两个以上取值”就是扫描。按条件词在原话里出现的位置取最早的那个。
_SWEEP_WORDS: tuple[tuple[str, str], ...] = (
    (r"(?:本小区|服务小区|小区内)\s*负载", "target_prb_utilization"),
    (r"(?:邻区|相邻小区)\s*负载", "neighbor_prb_util"),
    (r"(?<!区)(?<!小区)负载", "load?"),
    (r"站间距|站距|isd", "isd_m"),
    (r"发射功率|功率", "tx_power_dbm"),
    (r"带宽", "bandwidth_hz"),
    (r"srs\s*周期", "srs_period_ms"),
    (r"速度|车速", "ue_speed_kmh"),
)
_SEP = r"\s*(?:/|、|,|，|和|与|到|至|~|vs\.?|及|或|改到|改成|变为|-)\s*"


def _quantity_span(text: str, start: int, key: str) -> tuple[int, list[re.Match]]:
    """Read one quantity list, stopping before another dimension or condition."""
    tokens = []
    end = start
    pos = start
    while m := _QUANTITY.match(text, pos):
        unit = m.group(2)
        if unit and unit not in _UNITS[key]:
            break
        # An unlabelled number followed by a different condition belongs to that
        # condition, even when a comma could otherwise extend the current list.
        following = text[m.end():]
        if any(other != key and re.match(r"\s*(?:" + word + ")", following)
               for word, other in _SWEEP_WORDS):
            break
        tokens.append(m)
        end = m.end()
        sep = re.match(_SEP, text[end:])
        if sep is None:
            break
        pos = end + sep.end()
    return end, tokens


def _quantity_mentions(text: str):
    for word, key in _SWEEP_WORDS:
        for m in re.finditer("(?:" + word + r")\s*(?:(?:从|为|是|取|设为|分别为|分别取)\s*)?", text):
            end, tokens = _quantity_span(text, m.end(), key)
            if tokens:
                yield key, m.start(), end, tokens


def _extract_sweep(text: str) -> tuple[str, list[Any], str] | None:
    best = None
    ant = re.search(r"(\d+\s*t(?:\s*\d+\s*r)?(?:\s*(?:和|与|vs\.?|对比|跟|/|、|,|，)\s*"
                    r"\d+\s*t(?:\s*\d+\s*r)?)+)(?![a-z0-9])", text)
    if ant:
        vals, _ = parse_sweep_values(ant.group(1), "antenna_preset")
        if len(vals) >= 2:
            best = (ant.start(), "antenna_preset", vals, ant.group(0))
    for key, start, end, tokens in _quantity_mentions(text):
        vals, _ = parse_sweep_values(text[tokens[0].start():end], key)
        if len(vals) >= 2 and (best is None or start < best[0]):
            best = (start, key, vals, text[start:end])
    return None if best is None else (best[1], best[2], best[3])


def _negated_before(text: str, idx: int) -> bool:
    """否定必须通过紧邻的谓词修饰该要求，不能跨分句或另一个宾语。"""
    clause = re.split(r"[，,。；;！？!?\n]|但是|而是|但", text[:idx])[-1]
    negation = r"不能|不允许|不可以|不得|不(?:是|用|需(?:要)?|必|要)?|无需|无须|非|忽略|别"
    predicate = r"再|去|来|额外|特意|考虑|包含|采用|使用|实现|引入|带上|的|是|需要|要"
    tail = re.search(r"(?:(?:" + negation + "|" + predicate + r")\s*)+$", clause)
    # “不能忽略” / “不得不考虑” positively require the mechanism. Count the
    # complete adjoining predicate, rather than matching its last negation only.
    return bool(tail and len(re.findall(negation, tail.group(0))) % 2)


def read_brief(intent: str) -> Brief:
    """识别原话里已经给出的条件。只认能明确定位到原文的写法。"""
    raw = intent or ""
    text = raw.lower()
    b = Brief()

    m = re.search(r"(\d+)\s*t\s*(\d+)\s*r", text)
    if m:
        from .plan import _ANTENNA_PRESETS  # noqa: PLC0415

        label = f"{m.group(1)}T{m.group(2)}R"
        # 照原话记下阵型；支持与否每轮按当前阵型重新判断（审核 R2-3），不在这里定死。
        b.params["antenna_preset"] = label
        b.evidence.append(f"「{m.group(0)}」→ 阵型 {label}"
                          + ("" if label in _ANTENNA_PRESETS else "（不支持，已阻断）"))
    m = re.search(r"(?<![a-z])(cdl|tdl)\s*-?\s*([a-e])(?![a-z])", text)
    if m:
        b.params["channel_model"] = f"{m.group(1).upper()}-{m.group(2).upper()}"
        b.evidence.append(f"「{m.group(0)}」→ 信道模型 {b.params['channel_model']}")
    # “20 个 64T4R CDL-C 信道”：数量与“信道/样本”之间允许夹阵型、模型名，但不跨分句。
    m = re.search(r"(\d+)\s*个[^，,。；;]{0,24}?(?:信道|样本|快照|pdp|pmi|csi|srs|数据)",
                  raw, flags=re.I)
    if m:
        b.params["num_samples"] = int(m.group(1))
        b.evidence.append(f"「{m.group(0)}」→ 样本数 {m.group(1)}")
    if re.search(r"单小区|单站", raw):
        b.params["num_sites"] = 1
        b.evidence.append("「单小区」→ 1 站")
    m = re.search(r"(19|7)\s*(?:个)?站", raw)
    if m:
        b.params["num_sites"] = int(m.group(1))
        b.evidence.append(f"「{m.group(0)}」→ {m.group(1)} 站")
    m = re.search(r"(\d+(?:\.\d+)?)\s*km/h", text)
    if m:
        b.params["ue_speed_kmh"] = float(m.group(1))
        b.evidence.append(f"「{m.group(0)}」→ 速度 {m.group(1)} km/h（按全部用户理解；"
                          "若只是一部分高速用户混在低速人群里，要另说）")
    m = re.search(r"([-−]?\d+(?:\.\d+)?)\s*dbm", text)
    if m:
        b.params["tx_power_dbm"] = float(m.group(1).replace("−", "-"))
        b.evidence.append(f"「{m.group(0)}」→ 发射功率 {m.group(1)} dBm")
    # Resolve explicitly named bandwidth first, then hide those spans from the
    # carrier reader. GHz is a unit of both quantities, not a carrier label.
    frequency_text = list(text)
    for key, start, end, tokens in _quantity_mentions(text):
        if key == "bandwidth_hz":
            values, _ = parse_sweep_values(text[tokens[0].start():end], key)
            if len(values) == 1:
                b.params[key] = values[0]
                b.evidence.append(f"「{text[start:end]}」→ 带宽 {values[0]} Hz")
            frequency_text[start:end] = " " * (end - start)
    for m in re.finditer(r"(" + _NUM + r")\s*(ghz|mhz|khz|hz)\s*(?:的)?带宽", text):
        values, _ = parse_sweep_values(m.group(1) + m.group(2), "bandwidth_hz")
        if values:
            b.params["bandwidth_hz"] = values[0]
            b.evidence.append(f"「{m.group(0)}」→ 带宽 {values[0]} Hz")
        frequency_text[m.start():m.end()] = " " * (m.end() - m.start())
    frequency_text = "".join(frequency_text)
    m = re.search(r"(" + _NUM + r")\s*ghz", frequency_text)
    if m:
        b.params["carrier_freq_hz"] = float(m.group(1)) * 1e9
        b.evidence.append(f"「{m.group(0)}」→ 载频 {m.group(1)} GHz")
    bws = re.findall(r"(" + _NUM + r")\s*mhz", frequency_text)
    if len(bws) == 1 and "+" not in text:
        b.params["bandwidth_hz"] = float(bws[0]) * 1e6
        b.evidence.append(f"「{bws[0]} MHz」→ 带宽")

    m = re.search(r"(?<![a-z])(uma|umi|rma|inf)(?:[_\s-]*(nlos|los))?(?![a-z])", text)
    if m:
        base = {"uma": "UMa", "umi": "UMi", "rma": "RMa", "inf": "InF"}[m.group(1)]
        name = base if base == "InF" else base + "_" + (m.group(2) or "nlos").upper()
        b.params["scenario"] = name
        b.evidence.append(f"「{m.group(0)}」→ 场景 {name}")
    # 扫描：条件词 + 两个以上取值，一律走同一个解析器与校验器（审核 R3-2/R3-5）。
    # 原话给了几档就是几档；推荐值不能增删或替换。越界值照原样记下，由执行前检查阻断。
    sw = _extract_sweep(text)
    if sw is not None:
        key, vals, seg = sw
        b.sweep = {"key": key, "values": vals}
        if key in b.params:
            b.params.pop(key)
        if key == "antenna_preset":
            b.params.pop("antenna_preset", None)
        problems = validate_sweep(key, vals)
        what = {"neighbor_prb_util": "邻区负载", "target_prb_utilization": "本小区负载",
                "load?": "负载（归属待确认）"}.get(key, key)
        b.evidence.append(f"「{seg.strip()}」→ 扫{what} {vals}" + (f"（{problems[0]}，已阻断）" if problems else ""))
    if sw is None or sw[0] != "isd_m":
        m = re.search(r"(?:站间距|站距|isd)\s*(\d+(?:\.\d+)?)\s*m|(\d+(?:\.\d+)?)\s*m\s*(?:的)?\s*(?:站间距|站距)", text)
        if m:
            b.params["isd_m"] = float(m.group(1) or m.group(2))
            b.evidence.append(f"「{m.group(0)}」→ 站距 {m.group(1) or m.group(2)} m")

    # 原话里的硬要求：与选项回答走同一套阻断检查，推荐项不能替用户放弃（审核 R2-2）。
    # 否定作用于紧随其后的要求：“不需要考虑室内穿透”是接受平台边界，不是要求 O2I（审核 R3-3）。
    for m in re.finditer(r"室内穿透(?:损耗)?|穿透损耗|o2i|室内用户", text):
        if _negated_before(text, m.start()):
            if "indoor_users" not in b.design:
                b.design["indoor_users"] = "accept_outdoor"
                b.evidence.append("原话不要求室内穿透 → 接受全室外")
        else:
            b.design["indoor_users"] = "need_o2i"
            b.evidence.append("原话要求室内穿透损耗 → 当前平台不支持，已阻断")
    meanings = {"deadline_first" if re.search(r"截止|deadline", m.group(0)) else "drain_first"
                for m in re.finditer(r"最早排空|(?:earliest\s+)?drain(?:\s+first)?|"
                                     r"最早截止(?:时间)?|截止时间|(?:earliest\s+)?deadline(?:\s+first)?", text)
                if not _negated_before(text, m.start())}
    if "drain_first" in meanings:
        b.design["edf_meaning"] = "drain_first"
        b.evidence.append("原话说明 EDF 指最早排空优先（本平台实现）")
    # A positive unsupported requirement cannot be erased by the supported
    # meaning elsewhere; ambiguous conflicting descriptions stay blocked.
    if "deadline_first" in meanings:
        b.design["edf_meaning"] = "deadline_first"
        b.evidence.append("原话指定最早截止时间优先调度 → 本平台只有最早排空优先，已阻断")

    # 基线：比较词后面紧跟的方法名
    # 基线必须是明确的比较句式（“跟 X 比 / 相对 X / 以 X 为基线 / vs X”）；
    # “PF 和 EDF 对比”只说明比哪两个，不说明谁是基线，不能替用户认定。
    _m = r"(type\s*i{1,2}[^\s，,。比]*|svd|mmse|zf|pf|edf|dft)"
    m = (re.search(r"(?:跟|和|与)\s*" + _m + r"\s*(?:码本)?\s*(?:比|相比)", text)
         or re.search(r"(?:相对于?|vs\.?|基线(?:是|为|用)?|以)\s*" + _m, text))
    if m:
        b.design["baseline"] = raw[m.start(1):m.end(1)]
        b.evidence.append(f"「{m.group(0)}」→ 基线 {b.design['baseline']}")
    for word in ("5%", "边缘速率", "边缘用户", "体验速率", "完成时延", "时延", "谱效", "nmse",
                 "iot", "sinr", "sir", "吞吐", "pdp", "pmi"):
        if word in text:
            b.design.setdefault("metric_words", word)
    return b


# ---------------------------------------------------------------------------
# 2. 结论形态
# ---------------------------------------------------------------------------

FORMS: dict[str, dict[str, str]] = {
    "deliver": {
        "label": "交付数据",
        "sentence": "交付【条件】下的【测量量】，体检通过、文件可读。",
        "done_when": "条件与原话一致、门 1 通过、取货代码跑通",
    },
    "characterize": {
        "label": "刻画一个量",
        "sentence": "在【条件与假设】下，【目标量】的分布为【中位 / 分位】。",
        "done_when": "假设台账无沉默项，给出分布与未建模边界",
    },
    "sweep_condition": {
        "label": "扫一个条件",
        "sentence": "固定【其余条件】，【扫描变量】从【取值】变化时，【目标量】如何变化、为什么。",
        "done_when": "各取值的分布与机制解释；配对判决只在同一批数据上可做",
    },
    "compare_methods": {
        "label": "比较方法",
        "sentence": "在【条件】下，【方法】相对【基线】在【指标】上【效应 ± 区间】（n）。",
        "done_when": "预注册主指标、同数据配对、门 3 判决",
    },
}


def classify_form(intent: str, brief: Brief) -> tuple[str | None, str]:
    """判断结论形态；拿不准返回 None（由第一轮问题来定，而不是猜）。"""
    text = (intent or "").lower()
    # 审核 F10：“不做算法对比 / 不比算法”是否定句，不能因为出现“算法”“对比”就判成比较。
    negated = re.search(r"不(?:做|要|需要|用|进行)?\s*(?:算法|方案|方法)?\s*(?:对比|比较|比)",
                        text) is not None
    # 否定只取消“比较方法”，不取消刻画一个量或扫条件（审核 R2-8）。
    has_method = any(w in text for w in _METHOD_WORDS) and not negated
    has_compare = any(w in text for w in _COMPARE_WORDS) and not negated
    wants_data = any(w in text for w in _DELIVER_WORDS) and any(n in text for n in _DATA_NOUNS)
    # 想看某个量怎么分布/怎么变，就是分析任务，不是只交付数据（审核 R3-6：“给我看看…分布”）。
    wants_analysis = any(w in text for w in _ANALYSIS_CUES)
    if wants_data and not has_method and not wants_analysis:
        return "deliver", "原话要数据、不做比较"
    if has_method and (has_compare or "验证" in text):
        return "compare_methods", "原话在比较或验证一个方法/方案"
    if brief.sweep or (
        any(k in text for k in _CONDITION_KEYS) and any(w in text for w in _CHANGE_WORDS)
    ):
        return "sweep_condition", "原话在看某个条件变化时结果怎么变"
    if any(w in text for _, words in _METRIC_FAMILY_WORDS for w in words):
        return "characterize", "原话要看一个量的水平或分布"
    return None, "原话看不出要交付数据、刻画一个量，还是比较方法"


def metric_family(intent: str) -> str | None:
    text = (intent or "").lower()
    for fam, words in _METRIC_FAMILY_WORDS:
        if any(w in text for w in words):
            return fam
    return None


def sweep_key_from_intent(intent: str, brief: Brief) -> str | None:
    if brief.sweep:
        return brief.sweep["key"]
    text = (intent or "").lower()
    for word, key in _CONDITION_KEYS.items():
        if word in text:
            return key
    return None


# ---------------------------------------------------------------------------
# 3. 假设台账
# ---------------------------------------------------------------------------

SOURCE_SAID = "原话"
SOURCE_ANSWERED = "用户确认"
SOURCE_PRESET = "预设/平台默认"
SOURCE_LIMIT = "平台未实现（写进结论边界）"
SOURCE_DERIVED = "由用户选的组合选项带出"


def ledger(family: str | None, params: dict[str, Any], provenance: dict[str, str],
           *, sweep_key: str | None = None, answers: dict[str, str] | None = None) -> dict[str, Any]:
    """决定目标量的每个假设：现在取什么、谁定的。影响大且没人确认的就是沉默假设。"""
    if not family:
        return {"family": None, "items": [], "silently_assumed": []}
    items = []
    for f in sorted(fx.factors_for(family), key=lambda x: x.impact):
        if f.only_for_sweep and f.only_for_sweep != sweep_key:
            continue
        key = f.config_key or f.key
        answers = answers or {}
        if key == sweep_key or f.key == sweep_key:
            source, value = "扫描变量", "见扫描取值"
        elif f.status == fx.NOT_MODELED:
            # 平台没实现：改不了，是结论边界。回答了“怎么解读”也不能把它从边界里移走（审核 F6）。
            source, value = SOURCE_LIMIT, f.platform_default
            if f.key in answers:
                value = f"{f.platform_default}（解读：{answers[f.key]}）"
        elif f.key in provenance or key in provenance:
            source = provenance.get(f.key) or provenance.get(key) or SOURCE_ANSWERED
            value = answers.get(f.key, params.get(key, "已确认"))
        else:
            source = SOURCE_PRESET
            value = params.get(key, f.platform_default) if f.layer == "generation" else f.platform_default
        items.append({
            "key": f.key, "label": f.label, "value": value, "source": source,
            "impact": f.impact, "status": f.status, "layer": f.layer,
        })
    silent = [it for it in items if it["impact"] <= 2 and it["source"] == SOURCE_PRESET]
    interpret = [
        it["key"] for it in items
        if it["source"] == SOURCE_LIMIT and it["impact"] <= 2
        and any(f.key == it["key"] and f.options for f in fx.factors_for(family))
    ]
    return {"family": family, "items": items,
            "silently_assumed": [f"{it['label']} = {it['value']}（{it['source']}）"
                                 for it in silent],
            "conclusion_limits": [f"{it['label']}：{it['value']}"
                                  for it in items if it["source"] == SOURCE_LIMIT],
            "_silent_keys": [it["key"] for it in silent],
            "_interpret_keys": interpret}


# ---------------------------------------------------------------------------
# 4. 这一轮问什么
# ---------------------------------------------------------------------------

MAX_PER_ROUND = 3

# 只能由用户本人给出的内容：按推荐跑时不代答，也不记为“用户确认”。
USER_CONTENT_KEYS = frozenset({"expectation", "effect_size"})

# 系统层键的平台默认（与 sr_system_sim 签名一致，test_e2e 核对）。
_SYSTEM_DEFAULTS: dict[str, Any] = {
    "traffic_model": "ftp3", "neighbor_prb_util": 0.3, "srs_period_adaptive": True,
    "mu_enabled": False, "target_prb_utilization": None,
}

KEY_LABELS = {
    "isd_m": "站间距", "srs_period_ms": "SRS 周期", "ue_speed_kmh": "用户速度",
    "neighbor_prb_util": "邻区负载", "antenna_preset": "天线规模",
    "tx_power_dbm": "发射功率", "num_sites": "站数", "bandwidth_hz": "带宽",
}


@dataclass
class Question:
    key: str
    question: str
    why: str
    options: list[dict[str, Any]]
    layer: str = "design"            # design：记进约定；param：直接改配置
    priority: int = 5
    # 依赖的其他问题：它们这一轮还没答时，本题留到下一轮（grilling 的前沿规则）。
    depends_on: tuple[str, ...] = ()
    # 平台默认会让这次研究失效：用户说“默认”时也不能沿用，改取推荐值并告知。
    blocking_default: Any = None
    default: Any = None               # 参数题的当前默认值
    user_content: bool = False        # 只能由用户本人给出（预期、自定义取值），不按推荐代答
    # 选了某个选项后自动生效的改动：{选项值: {"overrides": {...}, "note": "..."}}
    effects: dict[Any, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = {"key": self.key, "question": self.question, "why": self.why,
               "options": self.options, "layer": self.layer, "priority": self.priority,
               "effects": {str(k): v for k, v in self.effects.items()},
               "depends_on": list(self.depends_on),
               # 与旧提问格式兼容的字段
               "default": self.default, "optional": False, "allow_free": True,
               "examples": [o["label"] for o in self.options]}
        if self.blocking_default is not None:
            out["blocking_default"] = self.blocking_default
        return out


def _opts(pairs: list[tuple[Any, str, str]]) -> list[dict[str, Any]]:
    return [{"value": v, "label": lab, "note": note, "recommended": i == 0}
            for i, (v, lab, note) in enumerate(pairs)]


def _form_question() -> Question:
    return Question(
        key="form",
        question="这次要的是哪一种结果？",
        why="结果形态决定后面问什么：交付数据不需要基线，比较方法需要预注册与配对检验，"
            "扫条件要先确定变量与取值。",
        options=_opts([
            ("characterize", "看一个量在某条件下的水平/分布", "例如某部署下的 SINR 分布"),
            ("sweep_condition", "看某个条件变化时结果怎么变", "例如站距、速度、SRS 周期"),
            ("compare_methods", "比较两个方法/方案谁更好", "需要基线、预注册与配对检验"),
            ("deliver", "只要一批数据", "不做比较"),
        ]),
        priority=0,
    )


_NAMED_METHODS = (
    ("type ii", "Type II 码本"), ("type i", "Type I 码本"), ("svd", "SVD 预编码"),
    ("rzf", "RZF"), ("ezf", "EZF"), ("mmse", "MMSE"), ("zf", "ZF"), ("dft", "DFT 波束"),
    ("pf", "PF 调度"), ("edf", "EDF 调度（本平台：最早排空优先）"), ("cort", "CORT"),
)


def named_methods(intent: str) -> list[str]:
    """原话里点名的方法，按出现顺序。"""
    text = (intent or "").lower()
    hits = []
    for key, label in _NAMED_METHODS:
        m = re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", text)
        if m and label not in [h[1] for h in hits]:
            if key == "type i" and "type ii" in text and text.count("type i") == text.count("type ii"):
                continue
            hits.append((m.start(), label))
    return [label for _, label in sorted(hits)]


def glossary_notes(intent: str) -> list[str]:
    """同缩写不同含义的术语：当场说清，别让人以为平台支持的是另一个东西。"""
    text = (intent or "").lower()
    notes = []
    if re.search(r"(?<![a-z])edf(?![a-z])", text):
        notes.append("本平台的 EDF 是 Earliest Drain First（最早排空优先，看可发送量与队列），"
                     "不是 Earliest Deadline First（最早截止时间优先）。若你要的是截止时间调度，"
                     "需要先确认，当前不能直接声称支持。")
    return notes


def _baseline_question(intent: str = "") -> Question:
    named = named_methods(intent)
    if len(named) >= 2:
        return Question(
            key="baseline",
            question=f"{named[0]} 和 {named[1]} 里，谁作基线、谁是候选？",
            why="基线决定“提升”的方向与预注册；现网已在用的一方通常作基线。",
            options=_opts([
                (named[0], f"以 {named[0]} 为基线", f"{named[1]} 作候选"),
                (named[1], f"以 {named[1]} 为基线", f"{named[0]} 作候选"),
            ]),
            priority=0,
        )
    return Question(
        key="baseline",
        question="你的方法要跟什么比？",
        why="没有基线就没有结论；基线还决定两臂的信道口径要不要一致。",
        options=_opts([
            ("type1_2", "3GPP Type I / Type II 码本", "最常见的基线"),
            ("svd_bound", "理想 CSI 的逐 RB 特征预编码", "乐观参考，不是现网"),
            ("published", "某篇已发表方法", "说出是哪篇，我对齐它的设置"),
        ]),
        priority=1,
    )


def _sweep_question(key: str, family: str | None, params: dict[str, Any] | None = None) -> Question:
    micro = str((params or {}).get("scenario", "")).startswith("UMi")
    values = {
        "isd_m": [("100/150/250", "100 / 150 / 250 m", "街道微站常见站距"),
                  ("100/200/300", "100 / 200 / 300 m", "微站到密集宏站")] if micro else
                 [("200/500/1000", "200 / 500 / 1000 m", "密集城区到郊区"),
                  ("200/300/500", "200 / 300 / 500 m", "集中在城区"),
                  ("500/1000/1732", "500 / 1000 / 1732 m", "城区到农村")],
        "srs_period_ms": [("10/20", "10 ms vs 20 ms", "现网常见两档"),
                          ("10/20/40", "10 / 20 / 40 ms", "看趋势")],
        "ue_speed_kmh": [("3/30/120", "3 / 30 / 120 km/h", "步行到高速"),
                         ("3/60", "3 vs 60 km/h", "两档对照")],
        "tx_power_dbm": [("33/40/46", "33 / 40 / 46 dBm", "微站到宏站"),
                         ("40/46/53", "40 / 46 / 53 dBm", "宏站功率区间")],
        "bandwidth_hz": [("20e6/40e6/100e6", "20 / 40 / 100 MHz", ""),
                         ("20e6/100e6", "20 vs 100 MHz", "两档对照")],
        "neighbor_prb_util": [("0.3/0.6/0.9", "30% / 60% / 90%", "轻载到重载"),
                              ("0.3/0.9", "30% vs 90%", "两档对照")],
        "target_prb_utilization": [("0.3/0.6/0.9", "30% / 60% / 90%", "轻载到重载"),
                                   ("0.3/0.9", "30% vs 90%", "两档对照")],
    }.get(key)
    if values is None:
        # 不认识的扫描变量不给占位选项（审核 F7：“low/high”被当成已答），请用户给数值。
        return Question(
            key="sweep_values",
            question=f"{KEY_LABELS.get(key, key)}扫哪几档？请直接给出具体数值。",
            why="扫描取值决定能看到的是趋势还是一个点；平台对这个变量没有可靠的默认档位。",
            options=_opts([("custom", "我直接给数值", "例如 3 个取值"),
                           ("two_points", "只比两档（请给出两个数值）", "")]),
            priority=1, user_content=True,
        )
    return Question(
        key="sweep_values",
        question=f"{KEY_LABELS.get(key, key)}扫哪几档？",
        why="扫描取值决定能看到的是趋势还是一个点。",
        options=_opts(values),
        priority=1,
    )


def upfront_notices(form: str | None, sweep_key: str | None, intent: str) -> list[str]:
    """开跑前必须说清、但不需要用户选择的事实。"""
    notes = []
    if form == "sweep_condition" and sweep_key and SWEEP_LAYER.get(sweep_key) == "generation":
        notes.append(
            f"{KEY_LABELS.get(sweep_key, sweep_key)}属于信道生成层变量：每一档是一批独立的数据，"
            "目前只能给各档分布对照 + 机制解释，给不了“A 比 B 高 X dB 且显著”这种配对结论"
            "（需要先给平台补跨数据集按位置配对的判决）。")
    if sweep_key == "srs_period_ms":
        notes.append("比较 SRS 周期需要信道随时间演化：平台会按速度生成连续轨迹，每 UE ≥8 个"
                     "时间相关快照；单快照数据上 SRS 周期不起作用。另外当前 SRS 导频干扰是解析"
                     "占位，结论不含上行导频污染的影响。")
    notes.extend(glossary_notes(intent))
    return notes


def _edf_question() -> Question:
    return Question(
        key="edf_meaning",
        question="你说的 EDF 指哪一个？",
        why="本平台实现的是 Earliest Drain First（最早排空优先）；若你指 Earliest Deadline "
            "First（按包时延预算），平台当前没有这个调度器——这决定这道题能不能直接做。",
        options=_opts([
            ("drain_first", "最早排空优先（本平台的 EDF）", "按可发送量与队列排序，可以直接比"),
            ("deadline_first", "最早截止时间优先（按时延预算）", "当前没有，需要先实现或改比其他调度器"),
        ]),
        priority=-1,
    )


def _load_owner_question() -> Question:
    return Question(
        key="load_owner",
        question="你说的负载是本小区的，还是邻区的？",
        why="本小区负载改变排队与资源竞争，邻区负载改变干扰；两者回答的是不同的因果问题。",
        options=_opts([
            ("target_prb_utilization", "本小区负载（资源竞争）", "标定本小区 PRB 利用率"),
            ("neighbor_prb_util", "邻区负载（干扰）", "邻区 PRB 占用率"),
        ]),
        priority=-2,
    )


def _metric_question(family: str | None, intent: str) -> Question | None:
    text = (intent or "").lower()
    if family == "dl_experience":
        opts = [("small_delay_p95", "小包完成时延 P95", "尾部最能拉开调度器差距；需有限到达话务"),
                ("edge_rate_p5", "5% 边缘体验速率", "关心边缘用户"),
                ("cell_tput", "小区吞吐", "关心容量；宜作护栏指标")]
        if not any(w in text for w in ("时延", "小包")):
            opts = [opts[1], opts[0], opts[2]]
    elif family == "dl_interference":
        opts = [("sinr_p5", "SINR 5% 分位", "边缘链路质量"),
                ("sinr_median", "SINR 中位", "整体水平"),
                ("iot_median", "IoT 中位", "干扰相对噪声")]
    else:
        return None
    return Question(
        key="metric",
        question="主判断指标（要预注册）用哪个？其余作护栏。",
        why="比较方法的结论要落在一个事先定下的指标上；看完结果再换指标就成了挑赢的那个。",
        options=_opts(opts),
        priority=0,
    )


def _quantity_question(family: str | None) -> Question | None:
    if family != "dl_interference":
        return None
    return Question(
        key="quantity",
        question="主要看哪个量、为了回答什么？",
        why="SIR 讲干扰几何，IoT 讲干扰比噪声高多少（干扰受限还是噪声受限），SINR 是用户链路"
            "实际拿到的；随站距等条件变化时三者走向可以完全不同。",
        options=_opts([
            ("all", "三个一起看，讲清机制", "例如评估加密站点值不值"),
            ("sinr", "以 SINR 为主", "关心用户体验"),
            ("iot", "以 IoT 为主", "关心干扰受限程度"),
        ]),
        priority=3,
    )


def _expectation_question(family: str | None) -> Question:
    target = {"dl_interference": "IoT / SIR / SINR 中位", "dl_experience": "边缘/体验速率"}.get(
        family or "", "目标量")
    return Question(
        key="expectation",
        question=f"跑之前先写下你的预期：{target}大概是多少？参考来源是什么？",
        why="几十秒的探测出来后逐项对照；差距大先回到假设台账查原因，"
            "不要等正式数据跑完才发现口径不同。",
        options=_opts([
            ("have_number", "我有参考数（直接说数和来源）", "现网统计、论文或以往仿真"),
            ("rough", "只有定性印象", "例如“应该是干扰受限”"),
            ("none", "没有预期，先看平台给什么", "可以，但结论前要逐条核对假设"),
        ]),
        priority=3,
        user_content=True,
    )


def _deployment_question() -> Question:
    """用部署类型一次问清场景、站高、功率——这是用户的语言，不是参数名。"""
    return Question(
        key="deployment",
        question="你对标的是哪种部署？",
        why="部署类型一次决定传播场景、站高和发射功率，三者直接决定 IoT 的绝对值。"
            "只问参数名，用户很难答对“UMi 还是 UMa、33 还是 46 dBm”。",
        options=_opts([
            ("urban_macro", "城区宏站（UMa、25 m、46 dBm）", "站距 300~800 m"),
            ("urban_micro", "街道微站（UMi、10 m、33 dBm）", "站距 100~250 m"),
            ("reduced_macro", "降功率宏站（UMa、25 m、40 dBm）", "站距 200~400 m"),
        ]),
        layer="design",
        priority=1,
        effects={
            "urban_macro": {"overrides": {"scenario": "UMa_NLOS", "tx_power_dbm": 46.0,
                                          "tx_height_m": 25.0}},
            "urban_micro": {"overrides": {"scenario": "UMi_NLOS", "tx_power_dbm": 33.0,
                                          "tx_height_m": 10.0}},
            "reduced_macro": {"overrides": {"scenario": "UMa_NLOS", "tx_power_dbm": 40.0,
                                            "tx_height_m": 25.0}},
        },
    )


def _factor_question(f: fx.Factor) -> Question:
    q = Question(
        key=f.key,
        question=f.ask,
        why=(f.effect_iot or f.effect) + ("。" if (f.effect_iot or f.effect) else "")
        + f"量级：{f.magnitude}。不答则按：{f.platform_default}。",
        options=_opts(list(f.options)),
        layer="param" if (f.status == fx.MODELED and f.layer == "generation") else "design",
        priority=1 + (f.impact - 1),
    )
    if f.key == "neighbor_load":
        q.effects["system_level"] = {"note": "负载改走系统级：sr_system_sim(neighbor_prb_util=现网值)"}
    if f.key == "stat_scope":
        q.effects = {19: {"overrides": {"num_sites": 19}}, 7: {"overrides": {"num_sites": 7}}}
        q.layer = "design"
    return q


def _contextual(q: Question, *, intent: str, params: dict[str, Any]) -> Question:
    """推荐项要看上下文：同一题在不同原话/配置下推荐不同的选项。"""
    text = (intent or "").lower()

    def prefer(value: Any, why: str) -> None:
        opts = sorted(q.options, key=lambda o: str(o["value"]) != str(value))
        for i, o in enumerate(opts):
            o["recommended"] = i == 0
        q.options = opts
        q.why += f" 本题推荐依据：{why}"

    if q.key == "traffic_model":
        if any(w in text for w in ("时延", "小包", "完成时间")):
            prefer("mixed", "原话关心时延，完成时延只有在有限到达（含小包）下才有定义。")
        elif any(w in text for w in ("容量", "满缓冲", "峰值")):
            prefer("full_buffer", "原话关心容量。")
    if q.key == "tx_power_dbm" and str(params.get("scenario", "")).startswith("UMi"):
        prefer(33.0, "场景是 UMi 街道微站，典型功率 33 dBm。")
    return q


def frontier(*, intent: str, form: str | None, family: str | None, brief: Brief,
             answered: set[str], led: dict[str, Any], sweep_key: str | None,
             extra_design: list[dict[str, Any]] | None = None,
             extra_params: list[dict[str, Any]] | None = None,
             limit: int | None = MAX_PER_ROUND,
             params: dict[str, Any] | None = None,
             answers: dict[str, str] | None = None) -> list[Question]:
    """这一轮的问题：前提已满足、会改变结论、只能由人回答，最多 MAX_PER_ROUND 个。

    依赖顺序（grilling 的前沿）：结论形态 → 形态必需项（基线 / 扫描取值）→
    决定目标量的沉默假设（按影响）→ 预期。形态没定时只问形态，别的都依赖它。
    """
    cands: list[Question] = []
    if form is None:
        return [_form_question()] if "form" not in answered else []

    if form == "deliver":
        # 数据任务不问基线、不问指标；原话没给的才补问，其余走默认并在台账里列出。
        return []

    answers = answers or {}
    if sweep_key == "load?" and "load_owner" not in answered:
        cands.append(_load_owner_question())
    if form == "compare_methods" and answers.get("edf_meaning") == "deadline_first":
        # 用户要的调度器平台没有：不再给“最早排空优先”的基线/指标选项（审核 F6），由阻断项处理。
        return []
    if form == "compare_methods":
        if re.search(r"(?<![a-z])edf(?![a-z])", (intent or "").lower()) and "edf_meaning" not in answered:
            cands.append(_edf_question())
        edf_dep = ("edf_meaning",) if re.search(r"(?<![a-z])edf(?![a-z])", (intent or "").lower()) else ()
        if "baseline" not in brief.design and "baseline" not in answered:
            bq = _baseline_question(intent)
            bq.depends_on = edf_dep
            cands.append(bq)
        mq = _metric_question(family, intent)
        if mq is not None and "metric" not in answered:
            mq.depends_on = edf_dep
            cands.append(mq)
    sweep_q = None
    has_values = bool(brief.sweep and brief.sweep.get("values"))
    if form == "sweep_condition" and sweep_key and not has_values and "sweep_values" not in answered:
        sweep_q = _sweep_question(sweep_key, family, params)
        cands.append(sweep_q)
    if form in {"characterize", "sweep_condition"} and "quantity" not in answered:
        qq = _quantity_question(family)
        if qq is not None:
            cands.append(qq)

    silent = set(led.get("_silent_keys", []))
    factors = {f.key: f for f in fx.factors_for(family)} if family else {}
    # 场景与功率都没人定时，用“部署类型”一题替代两个参数题。
    deploy_keys = {"tx_power_dbm", "scenario"} & silent
    if "scenario" in deploy_keys and "deployment" not in answered and "tx_power_dbm" != sweep_key:
        q = _deployment_question()
        q.priority = min(factors[k].impact for k in deploy_keys if k in factors)
        cands.append(q)
        silent -= {"tx_power_dbm", "scenario"}
        # 站距档位依赖部署类型（UMa 与 UMi 的合理站距不同）：部署没定时留到下一轮。
        if sweep_q is not None and sweep_key == "isd_m":
            sweep_q.depends_on = ("deployment",)
    for key in sorted(silent, key=lambda k: factors[k].impact if k in factors else 9):
        f = factors.get(key)
        if f is None or key in answered or not f.options:
            continue
        fq = _contextual(_factor_question(f), intent=intent, params=params or {})
        if f.only_for_sweep and f.only_for_sweep == sweep_key:
            fq.priority -= 1  # 专为这个扫描变量存在的前提（例如 SRS 自适应周期），先问
        compare_kind = "scheduler" if (form == "compare_methods" and any(
            "调度" in m for m in named_methods(intent))) else None
        if sweep_key in f.decisive_for or (compare_kind and compare_kind in f.decisive_for):
            fq.priority = min(fq.priority, 1) - 1  # 决定比较差值的因素，提前
        rec = next((o["value"] for o in fq.options if o["recommended"]), None)
        default = (params or {}).get(f.config_key) if f.layer == "generation" else _SYSTEM_DEFAULTS.get(f.config_key)
        if rec is not None and default is not None and str(rec) != str(default) and (
                (f.key == "srs_period_adaptive" and sweep_key == "srs_period_ms")
                or (f.key == "traffic_model" and any(w in (intent or "") for w in ("时延", "小包")))):
            fq.blocking_default = default
            fq.priority = min(fq.priority, 0) - 1
            fq.why += f" 注意：平台默认 {default} 会让这次研究失效，用户说“默认”时也要改为推荐值。"
        cands.append(fq)
    for key in led.get("_interpret_keys", []):
        f = factors.get(key)
        if f is None or key in answered:
            continue
        q = _factor_question(f)
        q.question = "（平台改不了，只选怎么解读）" + q.question
        q.priority += 1
        cands.append(q)
    # 没有因子表的目标量（例如 CSI 压缩的 NMSE），退回任务模板里的设计题与参数题，
    # 但原话已经给过的一律不问。
    for item in extra_design or []:
        if item["key"] == "metric" and form == "compare_methods" and _metric_question(family, intent):
            continue
        if item["key"] not in answered:
            # 比较方法时“看什么指标”是其余问题的前提，排在最前。
            cands.append(Question(key=item["key"], question=item["question"],
                                  why=item["why"], options=item["options"],
                                  layer="design", priority=int(item.get("priority", 1)) - 1,
                                  # 预期增益是用户的判断，不能按推荐代答（审核 R2-9）
                                  user_content=item["key"] in USER_CONTENT_KEYS))
    if not family:
        for item in extra_params or []:
            # 样本数由试点方差算出来（sr_sample_size），不问用户。
            if item["key"] not in answered and item["key"] != "num_samples":
                cands.append(Question(key=item["key"], question=item["question"],
                                      why=item["why"], options=item["options"],
                                      layer="param", priority=2 + int(item.get("priority", 5)),
                                      default=item.get("default")))
    # 比较方法时“预期增益多大”（effect_size）已经承担了预期的作用。
    if "expectation" not in answered and form != "compare_methods":
        cands.append(_expectation_question(family))

    cands.sort(key=lambda q: q.priority)
    if limit is None:
        return cands
    pending = {q.key for q in cands}
    ready = [q for q in cands if not (set(q.depends_on) & pending - answered)]
    return ready[:limit]


QUESTION_BUILDERS = {
    "form": _form_question, "baseline": _baseline_question,
    "deployment": _deployment_question, "edf_meaning": _edf_question,
    "load_owner": _load_owner_question,
}


def answer_effects(key: str, value: Any, family: str | None) -> dict[str, Any]:
    """用户选了某个选项后该自动生效的改动。选项值按字符串比较。"""
    q: Question | None = None
    if key in QUESTION_BUILDERS:
        q = QUESTION_BUILDERS[key]()
    elif family:
        f = next((x for x in fx.factors_for(family) if x.key == key), None)
        if f is not None:
            q = _factor_question(f)
    if q is None:
        return {}
    for opt_value, eff in q.effects.items():
        if str(opt_value) == str(value):
            return eff
    return {}


# ---------------------------------------------------------------------------
# 5. 汇总给 Agent
# ---------------------------------------------------------------------------


def restatement(form: str | None, family: str | None, brief: Brief, sweep_key: str | None,
                led: dict[str, Any], intent: str = "") -> str:
    """superpowers 的“复述理解”：一句话讲清要回答什么、已知什么、还缺什么。"""
    parts = []
    if form:
        parts.append(f"结果形态：{FORMS[form]['label']}（{FORMS[form]['sentence']}）")
    if sweep_key:
        layer = SWEEP_LAYER.get(sweep_key, "generation")
        parts.append(f"变化的量：{sweep_key}（{'换数据集' if layer == 'generation' else '同数据换参数，可配对'}）")
    if brief.evidence:
        parts.append("原话已定：" + "；".join(brief.evidence))
    if led.get("silently_assumed"):
        parts.append("平台替你假设了：" + "；".join(led["silently_assumed"]))
    parts.extend(upfront_notices(form, sweep_key, intent))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 6. 让仿真器自己说“哪个假设最要紧”
# ---------------------------------------------------------------------------


def _alternatives(cfg: dict[str, Any], sweep_key: str | None) -> list[tuple[str, dict[str, Any], str]]:
    """每个关键假设换一种合理的另一种取值。返回 (因子键, 改动, 人话)。"""
    alts: list[tuple[str, dict[str, Any], str]] = []
    tx = float(cfg.get("tx_power_dbm", 46.0) or 46.0)
    alts.append(("tx_power_dbm", {"tx_power_dbm": 33.0 if tx > 33.0 else 46.0},
                 f"发射功率 {tx:g} → {33.0 if tx > 33.0 else 46.0:g} dBm"))
    scen = str(cfg.get("scenario", "UMa_NLOS"))
    other = "UMi_NLOS" if scen.startswith("UMa") else "UMa_NLOS"
    alts.append(("scenario", {"scenario": other}, f"传播场景 {scen} → {other}"))
    sites = int(cfg.get("num_sites", 7) or 7)
    alts.append(("stat_scope", {"num_sites": 19 if sites < 19 else 7},
                 f"站数 {sites} → {19 if sites < 19 else 7}"))
    if sweep_key != "isd_m" and cfg.get("isd_m"):
        isd = float(cfg["isd_m"])
        alts.append(("isd_m", {"isd_m": isd * 2.0}, f"站距 {isd:g} → {isd * 2:g} m"))
    nf = float(cfg.get("noise_figure_db", 7.0) or 7.0)
    nf_alt = 7.0 if nf != 7.0 else 9.0
    alts.append(("noise_figure_db", {"noise_figure_db": nf_alt}, f"终端噪声系数 {nf:g} → {nf_alt:g} dB"))
    load = float(cfg.get("prb_utilization", 1.0) or 1.0)
    load_alt = 0.5 if load != 0.5 else 1.0
    alts.append(("neighbor_load", {"prb_utilization": load_alt, "pdsch_load": load_alt},
                 f"信道层邻区负载 {load:g} → {load_alt:g}"))
    dist = str(cfg.get("ue_distribution", "uniform"))
    dist_alt = "hotspot" if dist != "hotspot" else "uniform"
    alts.append(("ue_distribution", {"ue_distribution": dist_alt}, f"撒点 {dist} → {dist_alt}"))
    # 每个变体都必须真的改了东西，否则零差值没有意义（审核 F8：NF 已是 9 还“7→9”）。
    return [(k, ch, lab) for k, ch, lab in alts if any(cfg.get(ck) != cv for ck, cv in ch.items())]


def classify_zero(change: dict[str, Any], delta: dict[str, float | None]) -> dict[str, bool]:
    """零差值怎么读：只有键在静态核对过的 INERT_CONFIG_KEYS 里才能说“仿真器不读”；
    否则只能说“这次小样本没观察到变化”。"""
    zero = all(v == 0 for v in delta.values() if v is not None)
    verified = all(k in fx.INERT_CONFIG_KEYS for k in change)
    return {"inert": zero and verified, "no_change_observed": zero and not verified}


def measure_sensitivity(cfg: dict[str, Any], *, sweep_key: str | None = None,
                        keys: list[str] | None = None, num_samples: int = 21) -> dict[str, Any]:
    """在用户自己的配置上实测：每个假设换一种取值，下行 IoT / SIR / SINR 中位变多少。

    用探测模式（与 sr_generate 逐位一致的几何与链路预算）跑同一批 UE 位置，
    所以差值只来自被换的那个假设。Δ 恰好为 0 的说明仿真器根本不读它——不用问，
    但要写进结论边界。
    """
    import time as _time  # noqa: PLC0415

    from . import scenario as sc  # noqa: PLC0415

    def med(r: dict[str, Any]) -> dict[str, float | None]:
        dl = (r.get("interference") or {}).get("dl_iot") or {}
        return {"iot_dl_db": dl.get("median_db"),
                "sir_db": r["link_budget"]["sir_dB"].get("median"),
                "sinr_db": r["link_budget"]["sinr_dB"].get("median")}

    t0 = _time.perf_counter()
    base_cfg = dict(cfg)
    base = med(sc.probe(base_cfg, num_samples=num_samples))
    rows = []
    for key, change, label in _alternatives(base_cfg, sweep_key):
        if keys and key not in keys:
            continue
        got = med(sc.probe({**base_cfg, **change}, num_samples=num_samples))
        delta = {k: (None if got[k] is None or base[k] is None else round(got[k] - base[k], 2))
                 for k in base}
        rows.append({"factor": key, "change": label, "delta": delta,
                     **classify_zero(change, delta)})
    rows.sort(key=lambda r: -abs(r["delta"].get("iot_dl_db") or 0.0))
    return {
        "base": base,
        "num_samples": num_samples,
        "rows": rows,
        "elapsed_s": round(_time.perf_counter() - t0, 1),
        "how_to_use": (
            "按 |ΔIoT|（或 |ΔSINR|）从大到小决定先问谁：变化大的假设必须和用户对齐，"
            "变化小的可以用默认值并在台账里写明；inert=true 表示该键经源码核对不被读取，"
            "no_change_observed=true 只表示这次小样本没看到变化，不能据此说“未实现”。"
            "这是小样本探测的中位差，只用于排序提问，不是结论。"
        ),
    }
