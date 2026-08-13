"""预注册分析口径 —— 在看到数据之前把主指标定下来。

三道门管的是"信道对不对、比较公不公平、统计站不站得住"。它们**证明不了**
一件事：主指标和基线是在看数据之前定的，还是跑完之后挑出来的那个最好看的。

这不是学术不端才会犯的错。真实过程往往是：跑完看到平均谱效没提升，
顺手换成 5% 边缘用户谱效，发现有提升，于是就报了这个。每一步都合理，
合起来就是在多个指标里挑赢的那个——而挑选本身会把假阳性率推高到远超 5%。

所以做法很简单：**生成数据之前，把主指标写下来并算个摘要。** 之后
用别的指标下结论也可以，但会被标成 `exploratory`，不能冒充预注册主结论。

刻意做得很轻：一个 JSON、一个 SHA-256、不可原地改。
没有版本树、没有审批流、没有不可变存储——那是临床试验的量级，
个人做算法验证用不上，加上去只会让人绕过它。

用法::

    pr = lock(draft_id="dr_xxx", primary_metric="spectral_efficiency",
              baseline="type1", expected_effect=1.5)
    # → pr.prereg_id = "pr_a1b2c3d4"，把它传给 sr_generate

    classify(pr, "spectral_efficiency")   # → "primary"
    classify(pr, "edge_user_se")          # → "exploratory"
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .paths import prereg_dir

# 已知指标 → 单位。自定义指标也允许，单位由调用方给。
KNOWN_METRICS: dict[str, str] = {
    "spectral_efficiency": "bit/s/Hz",
    "sinr_db": "dB",
    "edge_user_se": "bit/s/Hz",
    "nmse_db": "dB",
    "cosine_similarity": "1",
    "beam_hit_rate": "1",
    "capacity": "bit/s/Hz",
}


@dataclass
class Prereg:
    """一份预注册的分析口径。"""

    prereg_id: str
    draft_id: str
    primary_metric: str
    metric_unit: str
    baseline: str
    csi_basis: str
    expected_effect: float | None
    higher_is_better: bool
    secondary_metrics: list[str] = field(default_factory=list)
    note: str = ""
    created_at: float = 0.0
    digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def text(self) -> str:
        eff = (
            f"{self.expected_effect:g} {self.metric_unit}"
            if self.expected_effect is not None
            else "未给（样本量只能事后反推）"
        )
        return (
            f"预注册 {self.prereg_id}（摘要 {self.digest[:16]}…）\n"
            f"  主指标   {self.primary_metric} [{self.metric_unit}]"
            f"（{'越大越好' if self.higher_is_better else '越小越好'}）\n"
            f"  基线     {self.baseline}\n"
            f"  CSI 口径 {self.csi_basis}\n"
            f"  期望效应 {eff}\n"
            f"  次要指标 {', '.join(self.secondary_metrics) or '无'}"
        )


def _canonical_digest(payload: dict[str, Any]) -> str:
    """确定性序列化后算 SHA-256。

    ``sort_keys`` + 固定分隔符是必须的：字典顺序变一下摘要就变，
    那这个摘要就没法用来判断"是不是同一份口径"。
    """
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def lock(
    *,
    draft_id: str = "",
    primary_metric: str = "spectral_efficiency",
    baseline: str = "",
    csi_basis: str = "ideal",
    expected_effect: float | None = None,
    metric_unit: str | None = None,
    higher_is_better: bool = True,
    secondary_metrics: list[str] | None = None,
    note: str = "",
) -> Prereg:
    """把分析口径写下来。**在生成数据之前调用。**

    返回的 ``prereg_id`` 传给 ``sr_generate``，会一起存进数据集摘要，
    之后 ``sr_compare_results`` / 门 3 就能判断用的指标是不是当初定的那个。

    改主意时**再调一次这个函数**，会得到新的 ``prereg_id``；
    旧文件不动。这样"改过口径"这件事本身留了痕，而不是被覆盖掉。
    """
    metric = str(primary_metric).strip()
    unit = metric_unit or KNOWN_METRICS.get(metric, "")
    payload = {
        "draft_id": str(draft_id),
        "primary_metric": metric,
        "metric_unit": unit,
        "baseline": str(baseline),
        "csi_basis": str(csi_basis),
        "expected_effect": (float(expected_effect) if expected_effect is not None else None),
        "higher_is_better": bool(higher_is_better),
        "secondary_metrics": sorted(secondary_metrics or []),
        "note": str(note),
    }
    digest = _canonical_digest(payload)
    pr = Prereg(
        prereg_id="pr_" + uuid.uuid4().hex[:8],
        created_at=time.time(),
        digest=digest,
        **payload,
    )
    (prereg_dir() / f"{pr.prereg_id}.json").write_text(
        json.dumps(pr.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return pr


def load(prereg_id: str) -> Prereg:
    p = prereg_dir() / f"{prereg_id}.json"
    if not p.is_file():
        raise FileNotFoundError(f"找不到预注册 {prereg_id!r}（{p}）")
    return Prereg(**json.loads(p.read_text(encoding="utf-8")))


def list_pregs() -> list[dict[str, Any]]:
    out = []
    for p in sorted(prereg_dir().glob("pr_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append(
            {
                "prereg_id": d["prereg_id"],
                "primary_metric": d["primary_metric"],
                "baseline": d["baseline"],
                "created_at": d["created_at"],
                "digest": d["digest"][:16],
            }
        )
    return out


def verify(pr: Prereg) -> bool:
    """摘要与内容是否对得上。文件被手改过就会不一致。"""
    payload = {
        k: getattr(pr, k)
        for k in (
            "draft_id", "primary_metric", "metric_unit", "baseline", "csi_basis",
            "expected_effect", "higher_is_better", "secondary_metrics", "note",
        )
    }
    return _canonical_digest(payload) == pr.digest


def classify(pr: Prereg | None, metric: str) -> dict[str, Any]:
    """这个指标算预注册主结论，还是探索性分析。

    没有预注册时一律 ``unregistered``——**不是 primary**。
    没登记过就不能声称"这是我事先定的主指标"。
    """
    m = str(metric).strip()
    if pr is None:
        return {
            "status": "unregistered",
            "primary": False,
            "metric": m,
            "why": "这批数据没有绑定预注册口径，无法证明主指标是事先定的",
            "how_to_claim": "下次生成前先 sr_lock_analysis，把主指标写下来",
        }
    if not verify(pr):
        return {
            "status": "tampered",
            "primary": False,
            "metric": m,
            "why": f"预注册 {pr.prereg_id} 的摘要与内容不符，文件可能被手改过",
        }
    if m == pr.primary_metric:
        return {
            "status": "primary",
            "primary": True,
            "metric": m,
            "prereg_id": pr.prereg_id,
            "why": f"与预注册主指标一致（{pr.prereg_id}）",
        }
    if m in pr.secondary_metrics:
        return {
            "status": "secondary",
            "primary": False,
            "metric": m,
            "prereg_id": pr.prereg_id,
            "why": f"预注册的次要指标之一。可以报，但主结论要用 {pr.primary_metric}",
        }
    return {
        "status": "exploratory",
        "primary": False,
        "metric": m,
        "prereg_id": pr.prereg_id,
        "why": (
            f"预注册的主指标是 {pr.primary_metric}，这里用的是 {m}。"
            f"**只能作为探索性分析报告**，不能当成预注册主结论——"
            f"跑完再换指标等于在多个指标里挑赢的那个，假阳性率远高于 5%"
        ),
        "how_to_claim": (
            f"想把 {m} 作为主结论：新开一次预注册把它定成主指标，"
            f"然后**用新数据**验证。拿同一批数据换指标不算独立验证"
        ),
    }
