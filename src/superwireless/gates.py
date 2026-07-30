"""三道评审门 —— 让站不住的结论出不去。

体检（``validate``）管的是"这批信道对不对"，但**信道对了结论照样可以是错的**。
真正让仿真结论翻车的，多数不是信道，是下面这几类：

* 两组配置除了被测变量之外还有别的不同，增益来自那个"别的"；
* 一边用理想信道预编码、另一边用估计信道，等于让自己的方法偷看答案；
* 样本量不足，置信区间比效应本身还宽，正负全凭抽样运气；
* 只比了均值，没做配对检验，把噪声当成了提升。

所以把门分成三道，各自守一个阶段：

===========  =================  ==========================================
门            什么时候过         过不去意味着
===========  =================  ==========================================
门 1 信道     生成之后           这批信道不可信，后面全白做
门 2 比较     跑对比之前         比出来的差异不能归因到被测变量
门 3 结论     写结论之前         数字也许对，但支撑不了要下的结论
===========  =================  ==========================================

门 2 里的样本量不靠人拍脑袋：给定期望效应量，样本量是**算出来的**
（``required_samples``）。反过来，若样本量已定，也能算出这个实验最小能
检出多大的效应（``detectable_effect``）——如果它比期望效应还大，这个实验
无论结果如何都不值得跑。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

_EPS = 1e-30

# 双侧 α=0.05 与功效 80% 对应的正态分位数。样本量公式里就这两个常数。
_Z_ALPHA = 1.959963985
_Z_POWER = 0.8416212336


@dataclass
class GateItem:
    """一条门禁判据。"""

    name: str
    passed: bool
    detail: str
    severity: str = "block"  # block / warn / info
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "passed": self.passed, "detail": self.detail,
            "severity": self.severity, "fix": self.fix,
        }


@dataclass
class GateResult:
    gate: str
    items: list[GateItem] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(i.passed for i in self.items if i.severity == "block")

    @property
    def blockers(self) -> list[GateItem]:
        return [i for i in self.items if i.severity == "block" and not i.passed]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "passed": self.passed,
            "context": self.context,
            "n_items": len(self.items),
            "blockers": [i.as_dict() for i in self.blockers],
            "items": [i.as_dict() for i in self.items],
            "verdict": (
                "通过，可以进入下一步"
                if self.passed
                else f"未通过：{len(self.blockers)} 项拦截。修完再跑，不要带着拦截项下结论"
            ),
        }

    def text(self) -> str:
        lines = [f"【{self.gate}】"]
        for i in self.items:
            mark = "PASS" if i.passed else ("WARN" if i.severity != "block" else "BLOCK")
            lines.append(f"  [{mark}] {i.name}")
            lines.append(f"         {i.detail}")
            if i.fix and not i.passed:
                lines.append(f"         → {i.fix}")
        lines.append("  " + self.as_dict()["verdict"])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 统计工具：功效分析与配对检验
# ---------------------------------------------------------------------------


def required_samples(
    std_diff: float, effect: float, *, alpha: float = 0.05, power: float = 0.80
) -> int:
    """检出给定效应量所需的样本数（配对设计）。

    ::

        N ≥ ( (z_{α/2} + z_β) · σ_d / Δ )^2

    ``std_diff`` 是**逐样本差值**的标准差，不是任一方的标准差。配对设计里
    两组共用同一批信道，共同的场景起伏会被差分抵消，σ_d 通常远小于单组标准差
    ——这就是配对比非配对省样本的原因，常常省一个数量级。

    ``effect`` 是想检出的最小差值，与 σ_d 同单位。
    """
    if not np.isfinite(std_diff) or not np.isfinite(effect) or abs(effect) < _EPS:
        return -1
    z = _Z_ALPHA if alpha == 0.05 else abs(_ndtri(1 - alpha / 2))
    zb = _Z_POWER if power == 0.80 else abs(_ndtri(power))
    return int(math.ceil(((z + zb) * float(std_diff) / abs(float(effect))) ** 2))


def detectable_effect(
    std_diff: float, n: int, *, alpha: float = 0.05, power: float = 0.80
) -> float:
    """给定样本数，这个实验最小能可靠检出多大的效应。``required_samples`` 的反解。

    这个数比样本量更值得先看一眼：如果它比你期望的增益还大，那这次实验
    **无论跑出什么结果都不足以支撑结论**，该做的是加样本或换更敏感的指标，
    而不是跑完再解释。
    """
    if n <= 0 or not np.isfinite(std_diff):
        return float("nan")
    z = _Z_ALPHA if alpha == 0.05 else abs(_ndtri(1 - alpha / 2))
    zb = _Z_POWER if power == 0.80 else abs(_ndtri(power))
    return float((z + zb) * float(std_diff) / math.sqrt(n))


def _ndtri(p: float) -> float:
    """标准正态分位数。只在非默认 α/功效 时才走这条路。"""
    from scipy.special import ndtri  # noqa: PLC0415

    return float(ndtri(p))


@dataclass
class PairedResult:
    """配对比较的结果。"""

    n: int
    mean_a: float
    mean_b: float
    mean_diff: float
    std_diff: float
    ci_low: float
    ci_high: float
    t_stat: float
    p_value: float
    wilcoxon_p: float
    win_rate: float
    max_single_contribution: float

    # ---- 两个检验各自的结论 --------------------------------------------
    @property
    def t_significant(self) -> bool:
        return bool(np.isfinite(self.p_value) and self.p_value < 0.05)

    @property
    def wilcoxon_significant(self) -> bool:
        return bool(np.isfinite(self.wilcoxon_p) and self.wilcoxon_p < 0.05)

    @property
    def tests_agree(self) -> bool:
        """两个检验是否给出同一结论。Wilcoxon 不可用时视为一致（无从冲突）。"""
        if not np.isfinite(self.wilcoxon_p):
            return True
        return self.t_significant == self.wilcoxon_significant

    # ---- 最终判决 --------------------------------------------------------
    # **判决以 Wilcoxon 为准**，这不是随便定的：
    #   · 谱效的逐样本差值分布常是偏的（少数用户位置贡献了大部分差异），
    #     t 检验的正态假设不成立，小样本下它会偏乐观；
    #   · 符号秩检验只用秩，对偏态与离群点稳健。
    # 只有 Wilcoxon 算不出来时（全零差值等退化情形）才退回配对 t。
    #
    # 这里曾经有个真漏洞：文档写着"以 Wilcoxon 为准"，但门 3 用的是只看
    # t 检验的 significant 属性。n=8、t p=0.044、Wilcoxon p=0.109 的样本
    # 会被直接放行——文档承诺的判据和代码实际用的判据是两回事。
    # 所以 significant 这个含混的名字被删掉了，改为必须显式说清用的是哪个检验。
    @property
    def decision_test(self) -> str:
        return "wilcoxon" if np.isfinite(self.wilcoxon_p) else "paired_t"

    @property
    def decision_p_value(self) -> float:
        return self.wilcoxon_p if self.decision_test == "wilcoxon" else self.p_value

    @property
    def decision_significant(self) -> bool:
        p = self.decision_p_value
        return bool(np.isfinite(p) and p < 0.05)

    @property
    def ci_excludes_zero(self) -> bool:
        return bool(self.ci_low > 0 or self.ci_high < 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "mean_a": round(self.mean_a, 4),
            "mean_b": round(self.mean_b, 4),
            "mean_diff": round(self.mean_diff, 4),
            "relative_gain": (
                round(self.mean_diff / self.mean_b, 4) if abs(self.mean_b) > _EPS else None
            ),
            "std_diff": round(self.std_diff, 4),
            "ci95": [round(self.ci_low, 4), round(self.ci_high, 4)],
            "ci_excludes_zero": self.ci_excludes_zero,
            "t_stat": (round(self.t_stat, 3) if np.isfinite(self.t_stat) else None),
            "p_value": float(f"{self.p_value:.3g}") if np.isfinite(self.p_value) else None,
            "wilcoxon_p": (
                float(f"{self.wilcoxon_p:.3g}") if np.isfinite(self.wilcoxon_p) else None
            ),
            "t_significant": self.t_significant,
            "wilcoxon_significant": self.wilcoxon_significant,
            "tests_agree": self.tests_agree,
            "decision_test": self.decision_test,
            "decision_p_value": (
                float(f"{self.decision_p_value:.3g}")
                if np.isfinite(self.decision_p_value)
                else None
            ),
            "decision_significant": self.decision_significant,
            "win_rate": round(self.win_rate, 3),
            "max_single_contribution": round(self.max_single_contribution, 3),
        }


def paired_compare(a: np.ndarray, b: np.ndarray) -> PairedResult:
    """逐样本配对比较 a 相对 b。

    同时给参数（配对 t）与非参数（Wilcoxon 符号秩）两个 p 值。谱效的逐样本
    差值分布往往是偏的，两个 p 值差很多就说明正态假设不成立，该信 Wilcoxon。

    ``win_rate`` 是 a 胜出的样本比例，``max_single_contribution`` 是贡献最大的
    单个样本占总差值的比例——后者用来抓"整体提升其实来自一两个极端样本"这种情况。
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.shape != b.shape:
        raise ValueError(f"配对比较要求形状一致，收到 {a.shape} 与 {b.shape}")
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    n = int(a.size)
    d = a - b
    if n < 2:
        nan = float("nan")
        return PairedResult(n, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan)

    mean_d = float(d.mean())
    std_d = float(d.std(ddof=1))
    se = std_d / math.sqrt(n)

    from scipy import stats  # noqa: PLC0415

    tcrit = float(stats.t.ppf(0.975, n - 1))

    # 零方差要分两种情况，不能一律当成"无穷显著"。
    #   · 差值恒为 0（两臂完全相同）→ 没有差异，p = 1；
    #   · 差值恒为同一个非零常数 → 方向确定，p = 0。
    # 早先这里一律写 p = 0.0，于是"自己跟自己比"会得到 p=0（最显著），
    # 只是碰巧被"置信区间不跨零"那条拦住——靠运气拦住的不算拦住。
    # 另外 float("inf") * np.sign(0) = nan，还会抛一个 RuntimeWarning。
    if se > _EPS:
        t_stat = mean_d / se
        p = float(2 * stats.t.sf(abs(t_stat), n - 1))
    elif abs(mean_d) <= _EPS:
        t_stat, p = 0.0, 1.0
    else:
        t_stat, p = math.copysign(float("inf"), mean_d), 0.0

    try:
        # scipy 在全零差值上会报错或给出无意义结果，显式短路成 p = 1。
        wp = float(stats.wilcoxon(d).pvalue) if np.any(d != 0) else 1.0
    except ValueError:
        wp = float("nan")

    total = float(np.abs(d).sum())
    return PairedResult(
        n=n,
        mean_a=float(a.mean()),
        mean_b=float(b.mean()),
        mean_diff=mean_d,
        std_diff=std_d,
        ci_low=mean_d - tcrit * se,
        ci_high=mean_d + tcrit * se,
        t_stat=float(t_stat),
        p_value=p,
        wilcoxon_p=wp,
        win_rate=float((d > 0).mean()),
        max_single_contribution=float(np.abs(d).max() / total) if total > _EPS else 0.0,
    )


# ---------------------------------------------------------------------------
# 配置差分
# ---------------------------------------------------------------------------

# 这些键不影响物理，差异不算"配置漂移"
_IGNORE_KEYS = frozenset(
    {"num_samples", "seed", "data_dir", "dataset_id", "draft_id", "source"}
)


def config_diff(cfg_a: dict, cfg_b: dict, *, ignore: frozenset = _IGNORE_KEYS) -> dict:
    """两份配置的差异。用来验证"除了被测变量，其余完全一致"。"""
    keys = (set(cfg_a) | set(cfg_b)) - set(ignore)
    out: dict[str, Any] = {}
    for k in sorted(keys):
        va, vb = cfg_a.get(k, "<缺失>"), cfg_b.get(k, "<缺失>")
        if isinstance(va, float) and isinstance(vb, float):
            if math.isclose(va, vb, rel_tol=1e-9):
                continue
        elif va == vb:
            continue
        out[k] = {"a": va, "b": vb}
    return out


# ---------------------------------------------------------------------------
# 门 1：信道可信
# ---------------------------------------------------------------------------


def gate_channel(ds: Any, *, snr_db: float = 20.0) -> GateResult:
    """门 1 —— 这批信道能不能拿来下结论。

    把 ``validate.full_report`` 的硬性检查搬成门禁语言：error 级不通过的项
    直接拦截，warn 级放行但记账。
    """
    from .validate import full_report

    rep = full_report(ds, snr_db=snr_db)
    items = [
        GateItem(
            name=c.name,
            passed=c.passed,
            detail=c.detail,
            severity={"error": "block", "warn": "warn"}.get(c.severity, "info"),
            fix=("按 detail 里写的原因修配置后重新生成" if not c.passed else ""),
        )
        for c in rep.checks
    ]
    return GateResult(
        gate="门 1 · 信道可信",
        items=items,
        context={
            "dataset_id": getattr(ds, "dataset_id", ""),
            "n": int(getattr(ds, "n", 0)),
            "scenario": ds.config.get("scenario"),
            "channel_model": getattr(ds, "channel_model", None),
        },
    )


# ---------------------------------------------------------------------------
# 门 2：比较公平
# ---------------------------------------------------------------------------


def gate_comparison(
    arm_a: dict[str, Any],
    arm_b: dict[str, Any],
    *,
    expected_effect: float | None = None,
    pilot_std_diff: float | None = None,
    n_samples: int | None = None,
) -> GateResult:
    """门 2 —— 这个对比公不公平、有没有把握。

    ``arm_a`` / ``arm_b`` 是两个"臂"的描述，至少含::

        {"name": "我的方法", "dataset_id": "ds_xxx", "config": {...},
         "csi": "ideal" | "estimated", "method": "svd", "receiver": "mmse"}

    检查四件事：

    1. **同一批信道** —— 两臂必须跑在同一个数据集上。不同数据集意味着信道
       实例不同，差值里混进了信道抽样噪声，配对检验的前提直接没了。
    2. **配置无漂移** —— 除被测变量外配置必须一致。
    3. **CSI 口径一致** —— 一边理想一边估计就是让自己的方法偷看答案。
       这是无线论文评审最常抓的一条。
    4. **样本量够不够** —— 有期望效应量时做功效分析；给不出效应量时，
       至少把"这个实验能检出多大效应"算出来摆在台面上。
    """
    items: list[GateItem] = []

    same_ds = arm_a.get("dataset_id") and arm_a.get("dataset_id") == arm_b.get("dataset_id")
    items.append(
        GateItem(
            "两臂跑在同一批信道上",
            bool(same_ds),
            (
                f"同为 {arm_a.get('dataset_id')}，可做配对比较"
                if same_ds
                else f"分别为 {arm_a.get('dataset_id')} 与 {arm_b.get('dataset_id')}"
            ),
            fix="把两种方法跑在同一个 dataset 上。配对比较能消掉共同的场景起伏，样本量常省一个数量级",
        )
    )

    diff = config_diff(arm_a.get("config") or {}, arm_b.get("config") or {})
    declared = set(arm_a.get("varies") or arm_b.get("varies") or [])
    drift = {k: v for k, v in diff.items() if k not in declared}
    items.append(
        GateItem(
            "配置无意外漂移",
            not drift,
            (
                f"两臂配置一致（声明的被测变量：{sorted(declared) or '无'}）"
                if not drift
                else f"除被测变量外还有 {len(drift)} 处不同：{drift}"
            ),
            fix="把这些差异消掉，或在 varies 里声明它们确实是被测变量",
        )
    )

    csi_a, csi_b = arm_a.get("csi", "?"), arm_b.get("csi", "?")
    # CSI 口径本身可以是被测变量——"CSI 误差的代价有多大"就是这么测的。
    # 门要拦的是**没声明**的不一致，不是不一致本身。声明方式与配置漂移相同：
    # 在任一臂里写 varies=["csi"]。
    csi_declared = "csi" in declared
    csi_ok = csi_a == csi_b or csi_declared
    items.append(
        GateItem(
            "CSI 口径一致",
            bool(csi_ok),
            (
                f"两臂都用 {csi_a} CSI"
                if csi_a == csi_b
                else (
                    f"CSI 口径是本次被测变量（{csi_a} vs {csi_b}）——已声明，放行。"
                    f"这测的是 CSI 误差本身的代价，不是算法优劣"
                    if csi_declared
                    else f"「{arm_a.get('name','A')}」用 {csi_a}，"
                    f"「{arm_b.get('name','B')}」用 {csi_b}"
                    "——用理想 CSI 的那一边等于提前知道了答案"
                )
            ),
            fix=(
                "两臂统一用 h_est（贴近实际）或统一用 h_true（上界对比）；"
                "若你就是要测 CSI 误差的代价，在臂里加 varies=[\"csi\"] 声明"
            ),
        )
    )

    if pilot_std_diff is not None and expected_effect:
        need = required_samples(pilot_std_diff, expected_effect)
        have = int(n_samples or 0)
        ok = have >= need > 0
        items.append(
            GateItem(
                "样本量足以检出期望效应",
                ok,
                (
                    f"期望效应 {expected_effect:g}，差值标准差 {pilot_std_diff:.3f}，"
                    f"需 {need} 个样本（α=0.05，功效 80%），现有 {have} 个"
                ),
                fix=f"把样本数加到 {need} 个" if not ok else "",
            )
        )
    elif pilot_std_diff is not None and n_samples:
        mde = detectable_effect(pilot_std_diff, int(n_samples))
        items.append(
            GateItem(
                "最小可检出效应",
                True,
                f"{n_samples} 个样本、差值标准差 {pilot_std_diff:.3f} 时，"
                f"最小可检出效应 {mde:.3f}。比这更小的差异，本实验分辨不出来",
                severity="warn",
            )
        )
    else:
        items.append(
            GateItem(
                "样本量论证",
                True,
                "未给出期望效应量，跳过功效分析。建议先跑 20 个样本做试点，"
                "拿差值标准差再算需要多少",
                severity="warn",
            )
        )

    return GateResult(
        gate="门 2 · 比较公平",
        items=items,
        context={"arm_a": arm_a.get("name"), "arm_b": arm_b.get("name")},
    )


# ---------------------------------------------------------------------------
# 门 3：结论站得住
# ---------------------------------------------------------------------------


def gate_conclusion(
    paired: PairedResult,
    *,
    claimed_gain: float | None = None,
    outlier_share: float = 0.5,
) -> GateResult:
    """门 3 —— 这个数字支不支撑要下的结论。

    四条判据：

    1. **置信区间不跨零** —— 跨零就是"可能更好也可能更差"，不能说提升。
    2. **配对检验显著** —— 同时看 t 与 Wilcoxon，两者结论不一致时以 Wilcoxon 为准
       并明说（差值分布偏态时 t 检验不可信）。
    3. **不被单个样本主导** —— 一个样本贡献了过半差值，说明这是个案不是规律。
    4. **声称的增益在区间内** —— 报"提升 12%"但区间是 [2%, 30%] 时，
       该报的是区间，不是点估计。
    """
    items: list[GateItem] = []

    items.append(
        GateItem(
            "95% 置信区间不跨零",
            paired.ci_excludes_zero,
            f"差值均值 {paired.mean_diff:+.4f}，95% CI [{paired.ci_low:+.4f}, {paired.ci_high:+.4f}]"
            + ("" if paired.ci_excludes_zero else " —— 跨零，方向都不能确定"),
            fix=f"加样本。当前 n={paired.n}，"
            f"要让区间不跨零至少需要 "
            f"{required_samples(paired.std_diff, abs(paired.mean_diff)) if paired.mean_diff else -1} 个",
        )
    )

    # 判决用 paired.decision_significant（以 Wilcoxon 为准，不可用时退 t），
    # 不是只看 t 检验的 t_significant——两者不一致时后者会放行不该放行的结论。
    detail = (
        f"判决检验 {paired.decision_test}（p={paired.decision_p_value:.3g}）；"
        f"配对 t p={paired.p_value:.3g}，Wilcoxon p={paired.wilcoxon_p:.3g}，"
        f"胜率 {paired.win_rate:.0%}"
    )
    if not paired.tests_agree:
        detail += (
            f" —— **两个检验结论冲突**（t {'显著' if paired.t_significant else '不显著'}、"
            f"Wilcoxon {'显著' if paired.wilcoxon_significant else '不显著'}）。"
            f"差值分布偏态时 t 检验的正态假设不成立，以 Wilcoxon 为准"
        )
    items.append(
        GateItem(
            "配对检验显著",
            paired.decision_significant,
            detail,
            fix=(
                "加样本，或换一个方差更小的指标"
                if not paired.tests_agree
                else "加样本，或换一个方差更小的指标"
            ),
        )
    )

    dominated = paired.max_single_contribution > outlier_share
    items.append(
        GateItem(
            "不被单个样本主导",
            not dominated,
            f"贡献最大的单个样本占总差值的 {paired.max_single_contribution:.0%}"
            + ("" if not dominated else " —— 这更像个案，不是规律"),
            fix="检查那个样本是不是异常工况；扩大撒点范围重跑",
        )
    )

    if claimed_gain is not None:
        inside = paired.ci_low <= claimed_gain <= paired.ci_high
        items.append(
            GateItem(
                "声称的增益落在区间内",
                inside,
                f"声称 {claimed_gain:+.4f}，实测 95% CI [{paired.ci_low:+.4f}, {paired.ci_high:+.4f}]"
                + ("" if inside else " —— 声称值超出区间"),
                fix="改成报区间而不是点估计",
            )
        )

    return GateResult(
        gate="门 3 · 结论站得住",
        items=items,
        context=paired.as_dict(),
    )


# ---------------------------------------------------------------------------
# 一步到位：同一批信道上跑两个方案并全程过门
# ---------------------------------------------------------------------------


@dataclass
class ComparisonResult:
    arm_a: str
    arm_b: str
    se_a: np.ndarray
    se_b: np.ndarray
    paired: PairedResult
    gate2: GateResult
    gate3: GateResult

    @property
    def passed(self) -> bool:
        return self.gate2.passed and self.gate3.passed

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm_a": self.arm_a,
            "arm_b": self.arm_b,
            "paired": self.paired.as_dict(),
            "gate_comparison": self.gate2.as_dict(),
            "gate_conclusion": self.gate3.as_dict(),
            "passed": self.passed,
            "statement": self.statement(),
        }

    def statement(self) -> str:
        """一句可以直接写进报告的结论——**过不了门时它会明说不能下结论**。

        必须写清最终用的是哪个检验。只写"p=..."而不说是哪个检验，读者会默认是
        t 检验；两个检验冲突时这就是误导。
        """
        p = self.paired
        rel = p.mean_diff / p.mean_b if abs(p.mean_b) > _EPS else float("nan")
        test_name = {"wilcoxon": "Wilcoxon 符号秩检验", "paired_t": "配对 t 检验"}[
            p.decision_test
        ]
        base = (
            f"{self.arm_a} 相对 {self.arm_b}：谱效 {p.mean_a:.3f} vs {p.mean_b:.3f} bit/s/Hz，"
            f"差值 {p.mean_diff:+.3f}（{rel:+.1%}），95% CI "
            f"[{p.ci_low:+.3f}, {p.ci_high:+.3f}]，n={p.n}，"
            f"{test_name} p={p.decision_p_value:.3g}"
        )
        if not p.tests_agree:
            base += (
                f"（配对 t p={p.p_value:.3g} 与之结论冲突，差值分布偏态，"
                f"以 Wilcoxon 为准）"
            )
        if self.passed:
            return base + "。结论成立。"
        blockers = [i.name for i in (self.gate2.blockers + self.gate3.blockers)]
        return base + f"。**结论不成立**，未过门：{'、'.join(blockers)}。"

    def text(self) -> str:
        return "\n".join([self.gate2.text(), "", self.gate3.text(), "", self.statement()])


def compare_arms(
    ds: Any,
    arm_a: dict[str, Any],
    arm_b: dict[str, Any],
    *,
    snr_db: float | None = None,
    max_samples: int = 500,
) -> ComparisonResult:
    """在**同一批信道**上跑两个方案，做配对比较，并连过门 2、门 3。

    每个臂的键：``name``、``method``（预编码）、``receiver``、
    ``csi``（``ideal`` 用 h_true 预编码，``estimated`` 用 h_est）。

    因为两臂共用同一批信道实例，差值天然是配对的——共同的路损、撒点、
    衰落起伏被差分抵消掉，剩下的才是方案本身的差别。
    """
    from .linklevel import link_performance

    h_true = np.asarray(ds.h_true)
    h_est = np.asarray(ds.h_est)
    n = min(int(h_true.shape[0]), int(max_samples))
    intf = ds.h_interferers

    # 不指定信噪比时用数据集自身逐样本的 SINR——那是这批信道真实的工作点。
    # 强行给一个统一的 snr_db 会把不同位置的用户拉到同一工作点，
    # 抹掉场景本身的差异，也让"边缘用户"这类结论无从谈起。
    sinr_per_sample = (
        None if snr_db is not None else np.asarray(ds.sinr_dB, dtype=float)
    )

    def run(arm: dict[str, Any]) -> np.ndarray:
        use_est = str(arm.get("csi", "ideal")) == "estimated"
        out = np.empty(n, dtype=float)
        for i in range(n):
            hi = h_true[i]
            s = snr_db if snr_db is not None else float(sinr_per_sample[i])
            out[i] = link_performance(
                hi,
                snr_db=s,
                method=arm.get("method", "svd"),
                receiver=arm.get("receiver", "mmse"),
                h_for_precoding=(h_est[i] if use_est else None),
                h_interferers=(intf[i] if intf is not None else None),
            ).spectral_efficiency
        return out

    se_a, se_b = run(arm_a), run(arm_b)
    paired = paired_compare(se_a, se_b)

    cfg = dict(ds.config)
    # 两臂 CSI 不同又没人声明时，多半是忘了；但如果调用方本来就在测 CSI 误差的
    # 代价，那它会自己写 varies。这里不替调用方声明——**自动放行等于门形同虚设**。
    a2 = {**arm_a, "dataset_id": ds.dataset_id, "config": cfg}
    b2 = {**arm_b, "dataset_id": ds.dataset_id, "config": cfg}
    g2 = gate_comparison(a2, b2, pilot_std_diff=paired.std_diff, n_samples=paired.n)
    g3 = gate_conclusion(paired)

    return ComparisonResult(
        arm_a=str(arm_a.get("name", "A")),
        arm_b=str(arm_b.get("name", "B")),
        se_a=se_a, se_b=se_b, paired=paired, gate2=g2, gate3=g3,
    )
