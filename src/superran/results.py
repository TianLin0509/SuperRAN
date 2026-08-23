"""外部算法的结果契约 —— 让自研算法进得了门 2 / 门 3。

``compare_arms`` 只认内置的六种预编码（svd / svd_wideband / type1 / dft /
mrt / identity）。可这个项目的核心用途是"我提一个无线算法优化思路，然后在
可信的信道上跑蒙特卡洛"——**那个思路的代码在用户手里，不在这里**。
CSI 压缩、信道估计、波束管理、定位、调度，一个都进不来。

这个模块补的就是那一层：用户在自己的脚本里跑自己的算法，把逐样本指标值
按标准格式注册回来，之后照样过门 2 / 门 3、照样拿到可直接引用的结论句。

## 边界

**MCP 绝不执行用户代码。** 用户代码在取货脚本里跑（那本来就是他们自己的
进程），只把标准化的 ``ResultArtifact`` 注册回来。这条边界不能松：
一旦 MCP 开始 exec 用户传进来的字符串，它就从"数据供应站"变成了任意代码执行面。

**逐样本数值不进 MCP JSON。** 值落 ``.npz``，MCP 只回句柄与统计摘要。
和信道数据同一条规矩。

## 为什么要校验这么多

配对比较的全部有效性建立在"两臂的第 i 个数对应同一个信道实例"上。
一旦顺序错位、样本数不同、或者两组结果其实来自不同数据集，配对检验的
p 值就是废的——**而它照样会算出一个看起来很显著的数**。所以注册时锁死：

* ``dataset_digest`` —— 内容摘要。数据重新生成过就对不上
* ``sample_ids``     —— 逐个且按序比对，不只比长度
* ``metric``         —— 指标名与单位必须一致，谱效不能拿去跟 NMSE 比
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .paths import dataset_dir, results_dir


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: Any, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


_SEMANTIC_SUMMARY_KEYS = (
    "source", "num_samples", "cells_configured", "cells_actual",
    "bs_panel", "ue_panel", "antenna_model", "parallel_dropped_fields",
    "interference_modeled", "shape", "channel_contract",
    "configured_channel_model", "effective_channel_model",
    "effective_channel_model_counts", "scenario", "config", "sample_meta",
    "provenance",
)


def _semantic_summary_sha256(summary: dict[str, Any]) -> str:
    """只哈希会改变数据物理语义的 summary 字段，排除耗时/路径/说明书。"""
    semantic = {key: summary.get(key) for key in _SEMANTIC_SUMMARY_KEYS}
    encoded = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=True,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def dataset_digest(dataset_id: str, *, refresh: bool = False) -> str:
    """数据集内容摘要（NPZ + 物理语义 summary 的版本化 SHA-256）。

    首次算完写进 ``summary.json`` 缓存——几百 MB 的文件每次都哈希太慢，
    而它一旦生成就不会再变（重新生成会得到新的 dataset_id）。
    """
    d = dataset_dir(dataset_id)
    sp = d / "summary.json"
    summary = json.loads(sp.read_text(encoding="utf-8"))
    npz = d / "channels.npz"
    st = npz.stat()
    cached = summary.get("dataset_digest")
    sidecar = summary.get("dataset_digest_source") or {}
    semantic_sha256 = _semantic_summary_sha256(summary)
    npz_unchanged = (sidecar.get("npz_mtime_ns") == st.st_mtime_ns
                     and sidecar.get("npz_size") == st.st_size
                     and bool(sidecar.get("npz_sha256")))
    fresh = (npz_unchanged
             and sidecar.get("semantic_sha256") == semantic_sha256
             and sidecar.get("digest_version") == "npz+semantic-v2")
    if cached and not refresh and fresh:
        return str(cached)
    # 无侧车（旧缓存）或 npz 被原地替换过时，必须重算——否则两臂注册会
    # 读到同一份陈旧摘要，"数据集内容一致"的检查被混过去。
    # 只改 summary 语义时复用已验证的 NPZ 摘要；数百 MB 数据不必重新哈希。
    npz_sha256 = str(sidecar["npz_sha256"]) if npz_unchanged else _sha256_file(npz)
    digest = _sha256_bytes(
        f"npz+semantic-v2\n{npz_sha256}\n{semantic_sha256}".encode("ascii"))
    summary["dataset_digest"] = digest
    summary["dataset_digest_source"] = {
        "digest_version": "npz+semantic-v2",
        "npz_mtime_ns": st.st_mtime_ns,
        "npz_size": st.st_size,
        "npz_sha256": npz_sha256,
        "semantic_sha256": semantic_sha256,
    }
    sp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return digest


def sample_ids(dataset_id: str, n: int) -> list[str]:
    """逐样本标识。

    形式是 ``<dataset_id>#<序号>``，简单但够用：序号就是数组下标，
    两臂只要都用 ``ds.sample_ids()`` 取，顺序天然一致。
    带上 dataset_id 是为了跨数据集也能一眼看出错配。
    """
    return [f"{dataset_id}#{i}" for i in range(int(n))]


@dataclass
class ResultArtifact:
    """一个臂的逐样本结果。数值在 ``values_path`` 指的 .npz 里，不在这个对象里。"""

    result_id: str
    dataset_id: str
    dataset_digest: str
    arm_name: str
    metric: str
    metric_unit: str
    n: int
    values_path: str
    values_sha256: str
    sample_ids_sha256: str
    method_metadata: dict[str, Any] = field(default_factory=dict)
    code_sha256: str | None = None
    prereg_id: str | None = None
    prereg_digest: str | None = None
    created_at: float = 0.0

    # ---- 统计摘要（这些可以进 JSON，逐样本值不行）----
    mean: float = float("nan")
    std: float = float("nan")
    p5: float = float("nan")
    p50: float = float("nan")
    p95: float = float("nan")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def values(self) -> np.ndarray:
        with np.load(self.values_path, allow_pickle=False) as z:
            return np.asarray(z["values"], dtype=float)

    def ids(self) -> list[str]:
        with np.load(self.values_path, allow_pickle=False) as z:
            return [str(x) for x in z["sample_ids"]]

    def text(self) -> str:
        return (
            f"结果 {self.result_id}「{self.arm_name}」\n"
            f"  数据集   {self.dataset_id}（摘要 {self.dataset_digest[:12]}…）\n"
            f"  指标     {self.metric} [{self.metric_unit}]  n={self.n}\n"
            f"  分布     均值 {self.mean:.4f}  标准差 {self.std:.4f}  "
            f"p5/p50/p95 {self.p5:.4f}/{self.p50:.4f}/{self.p95:.4f}\n"
            f"  方法信息 {self.method_metadata or '（未填）'}"
        )


def register(
    dataset_id: str,
    arm_name: str,
    values: Any,
    *,
    metric: str = "spectral_efficiency",
    metric_unit: str | None = None,
    ids: list[str] | None = None,
    method_metadata: dict[str, Any] | None = None,
    code_path: str | None = None,
    prereg_id: str | None = None,
) -> ResultArtifact:
    """把一个臂的逐样本结果注册进来。

    ``values`` 是长度 N 的一维数组，第 i 个对应数据集第 i 个样本。
    ``ids`` 不给时按 ``sample_ids(dataset_id, N)`` 自动生成——两臂都用默认
    就一定对齐；只有在你**跳过了部分样本**时才需要显式传。

    ``code_path`` 指向跑出这个结果的脚本，会记它的 SHA-256。这不是为了防谁，
    是为了三个月后还能确认"当时那版算法"到底是哪一版。
    """
    from . import analysis as an

    v = np.asarray(values, dtype=float).ravel()
    if v.size == 0:
        raise ValueError("values 是空的")

    ds_digest = dataset_digest(dataset_id)
    summary = json.loads((dataset_dir(dataset_id) / "summary.json").read_text(encoding="utf-8"))
    n_ds = int(summary.get("num_samples") or summary.get("shape", {}).get("N") or 0)

    sids = list(ids) if ids is not None else sample_ids(dataset_id, v.size)
    if len(sids) != v.size:
        raise ValueError(f"sample_ids 与 values 长度不符：{len(sids)} vs {v.size}")
    if len(set(sids)) != len(sids):
        raise ValueError(
            "sample_ids 含重复 ID：同一样本注册两次会伪造独立样本量，"
            "区间偏窄、p 值偏小，统计层面不可观测")
    if ids is None and n_ds and v.size != n_ds:
        raise ValueError(
            f"values 长度 {v.size} 与数据集样本数 {n_ds} 不符。"
            f"只算了一部分样本时请显式传 ids=，否则两臂会错位对齐"
        )

    n_bad = int((~np.isfinite(v)).sum())
    if n_bad:
        raise ValueError(
            f"values 里有 {n_bad} 个非有限值（nan/inf）。"
            f"配对检验会把它们整行丢掉，导致两臂样本数悄悄变少——请先处理"
        )

    unit = metric_unit or an.KNOWN_METRICS.get(metric, "")
    rid = "res_" + uuid.uuid4().hex[:8]
    out = results_dir() / f"{rid}.npz"
    np.savez_compressed(out, values=v, sample_ids=np.asarray(sids, dtype=object).astype(str))

    pr_digest = None
    if prereg_id:
        pr_digest = an.load(prereg_id).digest
    elif summary.get("prereg", {}).get("prereg_id"):
        # 数据集生成时绑过预注册，直接继承——用户不必重复传
        prereg_id = summary["prereg"]["prereg_id"]
        pr_digest = summary["prereg"].get("digest")

    art = ResultArtifact(
        result_id=rid,
        dataset_id=dataset_id,
        dataset_digest=ds_digest,
        arm_name=str(arm_name),
        metric=str(metric),
        metric_unit=unit,
        n=int(v.size),
        values_path=str(out),
        values_sha256=_sha256_file(out),
        sample_ids_sha256=_sha256_bytes(
            json.dumps(sids, ensure_ascii=False).encode("utf-8")),
        method_metadata=dict(method_metadata or {}),
        code_sha256=(_sha256_file(code_path) if code_path else None),
        prereg_id=prereg_id,
        prereg_digest=pr_digest,
        created_at=time.time(),
        mean=float(v.mean()),
        std=float(v.std(ddof=1)) if v.size > 1 else 0.0,
        p5=float(np.percentile(v, 5)),
        p50=float(np.percentile(v, 50)),
        p95=float(np.percentile(v, 95)),
    )
    (results_dir() / f"{rid}.json").write_text(
        json.dumps(art.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return art


def load(result_id: str) -> ResultArtifact:
    p = results_dir() / f"{result_id}.json"
    if not p.is_file():
        raise FileNotFoundError(f"找不到结果 {result_id!r}（{p}）")
    return ResultArtifact(**json.loads(p.read_text(encoding="utf-8")))


def list_results(dataset_id: str | None = None) -> list[dict[str, Any]]:
    out = []
    for p in sorted(results_dir().glob("res_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if dataset_id and d["dataset_id"] != dataset_id:
            continue
        out.append(
            {
                "result_id": d["result_id"],
                "arm_name": d["arm_name"],
                "dataset_id": d["dataset_id"],
                "metric": d["metric"],
                "n": d["n"],
                "mean": round(d["mean"], 4),
                "created_at": d["created_at"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# 配对前的一致性校验
# ---------------------------------------------------------------------------


def check_pairable(a: ResultArtifact, b: ResultArtifact) -> list[dict[str, Any]]:
    """两个结果能不能做配对比较。返回拦截项列表，空列表表示可以。

    每一条都是**硬拦截**，不是告警：不成立时配对检验算出来的 p 值没有意义，
    而它照样会算出一个数。
    """
    issues: list[dict[str, Any]] = []

    for r in (a, b):
        if _sha256_file(r.values_path) != r.values_sha256:
            issues.append({
                "check": "结果文件未被改动",
                "detail": (f"{r.arm_name} 的 values npz 与注册时的 SHA-256 不符——"
                           "注册后被替换或截断，统计摘要与真实数据已脱钩"),
                "fix": "用同一份结果重新 register",
            })
    if issues:
        return issues

    if a.dataset_id != b.dataset_id:
        issues.append(
            {
                "check": "同一数据集",
                "detail": f"{a.arm_name} 用 {a.dataset_id}，{b.arm_name} 用 {b.dataset_id}",
                "fix": "两个方案必须跑在同一批信道上，配对才有意义",
            }
        )
    elif a.dataset_digest != b.dataset_digest:
        issues.append(
            {
                "check": "数据集内容一致",
                "detail": (
                    f"dataset_id 相同但内容摘要不同"
                    f"（{a.dataset_digest[:12]}… vs {b.dataset_digest[:12]}…）"
                ),
                "fix": "数据集被重新生成过。两臂都要用同一份数据重跑",
            }
        )

    if a.n != b.n:
        issues.append(
            {
                "check": "样本数一致",
                "detail": f"{a.arm_name} n={a.n}，{b.arm_name} n={b.n}",
                "fix": "两臂必须覆盖同一批样本",
            }
        )
    elif a.sample_ids_sha256 != b.sample_ids_sha256:
        # 长度一样但 ID 序列不同 —— 最隐蔽的一种错配
        ida, idb = a.ids(), b.ids()
        first = next((i for i, (x, y) in enumerate(zip(ida, idb, strict=True)) if x != y), None)
        same_set = set(ida) == set(idb)
        issues.append(
            {
                "check": "样本顺序一致",
                "detail": (
                    f"样本数相同但 ID 序列不同，首个差异在第 {first} 位"
                    f"（{ida[first] if first is not None else '?'} vs "
                    f"{idb[first] if first is not None else '?'}）"
                    + ("；集合相同，只是顺序被打乱了" if same_set else "；集合也不同")
                ),
                "fix": (
                    "配对要求第 i 个数对应同一个信道实例。两臂都用 ds.sample_ids() "
                    "的默认顺序，不要自己排序或筛选"
                ),
            }
        )

    if a.metric != b.metric:
        issues.append(
            {
                "check": "指标一致",
                "detail": f"{a.metric} vs {b.metric}",
                "fix": "不同指标之间没有可比性",
            }
        )
    elif a.metric_unit != b.metric_unit:
        issues.append(
            {
                "check": "单位一致",
                "detail": f"{a.metric_unit!r} vs {b.metric_unit!r}",
                "fix": "同名指标但单位不同，先统一单位",
            }
        )

    return issues


# ---------------------------------------------------------------------------
# 取货脚本模板
# ---------------------------------------------------------------------------

_TEMPLATE = '''"""外部算法评测脚本 —— 数据集 {dataset_id}

由 sr_export_eval_template 生成。**把 my_algorithm 换成你自己的算法**，
其余不用动。跑完会把两个臂的结果注册回 superran，
之后用 sr_compare_results 过门 2 / 门 3。

指标：{metric} [{unit}]{prereg_line}
"""
import sys
sys.path.insert(0, r"{src}")

import numpy as np
from superran import load, results

ds = load("{dataset_id}")
print(ds)

# 逐样本标识。**两个臂都用这一份，顺序不要改** —— 配对比较的全部有效性
# 建立在"第 i 个数对应同一个信道实例"上。
IDS = results.sample_ids("{dataset_id}", ds.n)


# ===========================================================================
#  你的算法写在这里
# ===========================================================================
def my_algorithm(h_est, h_true, operating_point):
    """对单个样本算出一个指标值。

    参数
    ----
    h_est   : [T, RB, BS_ant, UE_ant] 估计信道（带导频与噪声，贴近实际系统）
    h_true  : 同形，理想信道。**只用来评估性能，不要拿它算预编码**
              —— 那等于让自己的方法提前知道答案，门 2 会拦
    operating_point : 该样本经标定的几何工作点。first-party ``sinr_db`` 是
              预数字波束、每 RB 口径；这里使用由它和 E[|H|²] 反标的
              noise_power / interference_cov。数字 BF 增益由 H 与预编码器贡献一次。

    返回
    ----
    一个 float。这里给的示例是"用估计信道做逐 RB SVD 预编码"，
    换成你自己的算法即可。
    """
    from superran.linklevel import link_performance

    r = link_performance(
        h_true,
        noise_power=operating_point.noise_power,
        interference_cov=operating_point.interference_cov,
        operating_point=operating_point.as_dict(),
        method="svd",
        h_for_precoding=h_est,          # 预编码只看估计信道
    )
    return float(r.spectral_efficiency)


def baseline(h_est, h_true, operating_point):
    """基线。这里用 Type-I-style 单面板列码本近似，**同样只看估计信道**。"""
    from superran.linklevel import link_performance

    r = link_performance(
        h_true,
        noise_power=operating_point.noise_power,
        interference_cov=operating_point.interference_cov,
        operating_point=operating_point.as_dict(),
        method="type1",
        h_for_precoding=h_est,
    )
    return float(r.spectral_efficiency)


# ===========================================================================
#  跑两个臂
# ===========================================================================
h_true, h_est = ds.h_true, ds.h_est

mine, base = [], []
for i in range(ds.n):
    op = ds.geometric_impairment(i)
    mine.append(my_algorithm(h_est[i], h_true[i], op))
    base.append(baseline(h_est[i], h_true[i], op))
    if (i + 1) % 50 == 0:
        print(f"  {{i + 1}}/{{ds.n}}")

# ===========================================================================
#  注册回 superran
# ===========================================================================
art_a = results.register(
    "{dataset_id}", "我的方法", mine,
    metric="{metric}", ids=IDS, code_path=__file__,
    method_metadata={{"note": "换成你的算法描述：超参、训练集、版本号等"}},
)
art_b = results.register(
    "{dataset_id}", "基线", base,
    metric="{metric}", ids=IDS, code_path=__file__,
    method_metadata={{"method": "type1", "csi": "estimated"}},
)

print()
print(art_a.text())
print()
print(art_b.text())
print()
print("下一步，把这两个句柄交给 MCP 判决：")
print(f'  sr_compare_results("{{art_a.result_id}}", "{{art_b.result_id}}")')
'''


def eval_template(dataset_id: str, *, metric: str = "spectral_efficiency") -> dict[str, Any]:
    """生成一份可直接运行的评测脚本骨架。

    用户只需要替换 ``my_algorithm`` 的函数体。模板里预填的示例本身就是一个
    合法的对比（估计 CSI 下的 SVD vs Type I），所以**不改也能跑通全链路**——
    先确认管道通了再换自己的算法，比一上来就全套自己写省事。
    """
    from . import analysis as an
    from .paths import project_root

    summary = json.loads((dataset_dir(dataset_id) / "summary.json").read_text(encoding="utf-8"))
    pr = summary.get("prereg") or {}
    prereg_line = ""
    if pr.get("prereg_id"):
        prereg_line = (
            f"\n预注册：{pr['prereg_id']}（主指标 {pr.get('primary_metric')}，"
            f"基线 {pr.get('baseline') or '未填'}）"
        )
        metric = metric or pr.get("primary_metric") or metric

    code = _TEMPLATE.format(
        dataset_id=dataset_id,
        metric=metric,
        unit=an.KNOWN_METRICS.get(metric, ""),
        src=str(project_root() / "src"),
        prereg_line=prereg_line,
    )
    return {
        "dataset_id": dataset_id,
        "metric": metric,
        "n_samples": int(summary.get("num_samples", 0)),
        "prereg_id": pr.get("prereg_id"),
        "code": code,
        "how_to_use": [
            "把 code 写进一个 .py 文件",
            "替换 my_algorithm 的函数体（不改也能跑，示例是 SVD vs TypeI）",
            "运行它，会打印两个 result_id",
            "把两个 result_id 交给 sr_compare_results，过门 2 与门 3",
        ],
        "guardrails": [
            "两个臂都用同一份 IDS，顺序不要改 —— 配对的有效性全靠它",
            "预编码只看 h_est，h_true 只用于评估。混用会被门 2 拦",
            "返回值必须是有限数，nan/inf 会在注册时报错而不是悄悄丢样本",
        ],
    }
