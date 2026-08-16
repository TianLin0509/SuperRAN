"""3GPP 校准量、标准查表值、三道评审门的测试。

直接运行：python tests/test_gates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Windows 中文控制台是 GBK：print 含 U+2212/U+FFFD 等字符时会炸 UnicodeEncodeError，
# 把测试输出吓成"失败"。统一 reconfigure，本文件的 print 全部 replace 兜底。
sys.stdout.reconfigure(errors="replace")

from superran import calibration as cal  # noqa: E402
from superran import channelhub as ch  # noqa: E402
from superran import decisions as dec  # noqa: E402
from superran import gates as g  # noqa: E402
from superran import generate as gen  # noqa: E402
from superran import load  # noqa: E402
from superran import plan as pl  # noqa: E402
from superran import spec38901 as spec  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(t: str) -> None:
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def make(n: int = 30, **ov):
    base = {"num_samples": n, "num_ues": max(n // 3, 4), "antenna_preset": "32T4R",
            "bandwidth_hz": 20000000.0}
    base.update(ov)
    d, _ = pl.create_draft("蒙特卡洛评估谱效", overrides=base)
    cfg, _ = pl.resolved_config(d)
    cfg.pop("num_samples", None)
    s = gen.generate(cfg, num_samples=n)
    return load(s["dataset_id"]), s


# ---------------------------------------------------------------------------
sect("1  38.901 标准查表值")

info = ch.warmup()
print(f"  预热：{info.get('cdl_spec_tables')}")
check(bool(info.get("cdl_spec_tables", {}).get("applied")), "标准 CDL 表已灌入")

for name in spec.COVERED:
    s = spec.as_arrays(name)
    p = ch.cdl_profile(name)
    same = all(
        np.allclose(s[f], np.asarray(getattr(p, f), dtype=float), atol=1e-9)
        for f in ("delays_norm", "powers_dB", "aod_deg", "aoa_deg", "zod_deg", "zoa_deg")
    )
    check(same, f"{name} 与 {spec.CDL_TABLES[name]['table']} 逐簇一致")

# 灌表必须幂等：重复调用不改变结果
before = ch.cdl_profile("CDL-C").aoa_deg.copy()
ch.ensure_spec_tables()
check(np.allclose(before, ch.cdl_profile("CDL-C").aoa_deg), "重复灌表幂等")

# 差异报告本身要能跑（灌表后应当无差异）
d = spec.diff_against_channelhub()
check(
    all(v["n_mismatched_clusters"] == 0 for v in d.values()),
    "灌表后差异报告显示零差异",
)

# ---------------------------------------------------------------------------
sect("2  Annex A.1 圆周角度扩展")

# 全部功率集中在一个角度 → 角度扩展为 0。
# 容差取 1e-6 rad 而非 0：sqrt(−2·ln r) 在 r→1 处把浮点误差开方放大，
# 数学上的 0 在浮点里是 ~1e-8 rad（约 6e-7 度），远小于任何物理意义上的角度。
check(
    abs(cal.circular_angular_spread_rad(np.array([1.234]), np.array([1.0]))) < 1e-6,
    "单一角度的角度扩展为 0",
)
# 绕回免疫：0° 与 360° 是同一个方向，普通标准差会算出 180°，圆周定义应当给 0
a = np.radians(np.array([0.0, 360.0]))
check(
    cal.circular_angular_spread_rad(a, np.ones(2)) < 1e-6,
    "0° 与 360° 视为同一方向（普通标准差会误判）",
)
# 均匀铺满一圈 → 矢量和趋近 0 → 角度扩展趋近无穷大
wide = cal.circular_angular_spread_rad(
    np.linspace(0, 2 * np.pi, 64, endpoint=False), np.ones(64)
)
check(wide > 3.0, "角度均布一圈时角度扩展很大")

# 与标准表算出的值应稳定可复现
asa = np.degrees(
    cal.circular_angular_spread_rad(
        np.radians(spec.as_arrays("CDL-C")["aoa_deg"]),
        10 ** (spec.as_arrays("CDL-C")["powers_dB"] / 10),
    )
)
print(f"  CDL-C 标准表 ASA = {asa:.2f}°")
check(60.0 < asa < 80.0, "CDL-C 的 ASA 在合理量级")

# ---------------------------------------------------------------------------
sect("3  38.901 §7.8 校准量")

ds, summ = make(30)
print(f"  数据集 {summ['dataset_id']}，{summ['shape']}")
rep = cal.calibration_report(ds)
print(rep.text())

names = {m.name: m for m in rep.metrics}
check("耦合损耗（服务小区）" in names, "出了耦合损耗")
cl = names["耦合损耗（服务小区）"]
check(cl.n == ds.n, "耦合损耗逐样本都有")
check(50 < cl.percentiles["p50"] < 180, "耦合损耗量级合理")

check(rep.singular_values is not None, "出了 PRB 奇异值")
sv = rep.singular_values
check(
    bool(np.all(sv.largest_db >= sv.smallest_db - 1e-6)),
    "最大奇异值不小于次大奇异值",
)
check(bool(np.all(sv.ratio_db >= -1e-6)), "奇异值比值非负")

# 角度扩展四项都应出数
for k in ("ASD", "ASA", "ZSD", "ZSA"):
    m = cal.angular_spread_deg(ds, k)
    check(m.values is not None and np.isfinite(m.values[0]), f"角度扩展 {k} 出数")

# ---------------------------------------------------------------------------
sect("4  跨引擎 KS 比较")

x = np.random.default_rng(0).normal(0, 1, 500)
y = np.random.default_rng(1).normal(0, 1, 500)
z = np.random.default_rng(2).normal(3, 1, 500)
d_same = cal.ks_statistic(x, y)
d_diff = cal.ks_statistic(x, z)
crit = cal.ks_critical(500, 500)
print(f"  同分布 D={d_same:.3f}，异分布 D={d_diff:.3f}，临界值 {crit:.3f}")
check(d_same < crit, "同分布判为一致")
check(d_diff > crit, "异分布判为不一致")

cmpres = cal.cross_engine_compare(ds, ds)
check(
    cmpres["metrics"]["coupling_loss"]["same_distribution"],
    "同一数据集与自己比必然一致",
)

# ---------------------------------------------------------------------------
sect("5  功效分析")

# 两个方向必须互为反解
std, eff = 0.83, 0.30
n = g.required_samples(std, eff)
mde = g.detectable_effect(std, n)
print(f"  σ_d={std}，Δ={eff} → 需要 {n} 个样本；n={n} 时最小可检出 {mde:.4f}")
check(n > 0, "样本数为正")
check(abs(mde - eff) / eff < 0.05, "样本数与可检出效应互为反解")

# 效应减半，样本数应当约为四倍
check(
    abs(g.required_samples(std, eff / 2) / (4 * n) - 1) < 0.02,
    "效应减半时样本数变四倍（平方关系）",
)
# 方差为零或效应为零时不给出荒谬的数
check(g.required_samples(std, 0.0) == -1, "效应为 0 时拒绝给样本数")

adv = dec.sample_size_advice()
check(adv["mode"] == "先做试点", "无输入时给试点流程")
adv2 = dec.sample_size_advice(std_diff=std, expected_effect=eff)
check(adv2["required_n"] == n, "决策层与门控层样本数一致")

# ---------------------------------------------------------------------------
sect("6  配对比较")

rng = np.random.default_rng(7)
base = rng.normal(20, 5, 200)  # 共同的场景起伏
a = base + rng.normal(0.5, 0.4, 200)  # A 系统性高 0.5
b = base + rng.normal(0.0, 0.4, 200)
pr = g.paired_compare(a, b)
print(f"  配对：差值 {pr.mean_diff:+.3f}，CI [{pr.ci_low:+.3f}, {pr.ci_high:+.3f}]，p={pr.p_value:.2e}")
check(pr.decision_significant, "真实差异被检出")
check(pr.ci_excludes_zero, "置信区间不跨零")
check(pr.std_diff < base.std(), "配对后的差值标准差远小于单组标准差")

# 无差异时不该报显著
c = base + rng.normal(0.0, 0.4, 200)
pr0 = g.paired_compare(c, b)
print(f"  无差异：差值 {pr0.mean_diff:+.3f}，p={pr0.p_value:.3f}")
check(not pr0.decision_significant, "无差异时不报显著")

# 单个极端样本主导要被识别出来
d = b.copy()
d[0] += 500.0
prx = g.paired_compare(d, b)
check(prx.max_single_contribution > 0.5, "识别出单样本主导")

# 同一位置的多次衰落是重复测量，不能把 2 个位置 × 2 次衰落冒充 n=4。
ca, cb, cluster_order = g.paired_cluster_means(
    np.array([3.0, 5.0, 12.0, 16.0]),
    np.array([1.0, 3.0, 8.0, 12.0]),
    np.array(["position-0", "position-0", "position-1", "position-1"]),
)
pcluster = g.paired_compare(ca, cb)
check(pcluster.n == 2 and cluster_order == ["position-0", "position-1"],
      "重复衰落先按独立位置聚类，统计 n=2 而不是伪 n=4")
check(np.allclose(ca, [4.0, 14.0]) and np.allclose(cb, [2.0, 10.0]),
      "每个位置内 A/B 各自取均值后再做配对")

# ---------------------------------------------------------------------------
sect("6.5  统计判决：t 与 Wilcoxon 冲突、零方差退化")

# 这一节全是回归测试。曾经有个真漏洞：文档写着"两个检验冲突时以 Wilcoxon 为准"，
# 但门 3 用的是只看 t 检验的属性，于是 t 显著、Wilcoxon 不显著的样本被直接放行。
# 承诺的判据与代码实际用的判据是两回事，这种不一致比判据本身宽松更危险。

# ① t 显著 / Wilcoxon 不显著 —— 必须按 Wilcoxon 判为不显著并拦截
d_conflict = np.array([-0.0811, 1.5561, 0.5308, 1.9896, 3.2605, -0.1125, 1.6908, -0.2045])
pc = g.paired_compare(d_conflict, np.zeros_like(d_conflict))
print(f"  n={pc.n}  t p={pc.p_value:.5f}  Wilcoxon p={pc.wilcoxon_p:.5f}")
print(f"  判决检验={pc.decision_test}  显著={pc.decision_significant}")
check(pc.t_significant, "该样本 t 检验确实显著（构造正确）")
check(not pc.wilcoxon_significant, "该样本 Wilcoxon 不显著（构造正确）")
check(not pc.tests_agree, "识别出两检验冲突")
check(pc.decision_test == "wilcoxon", "冲突时判决用 Wilcoxon")
check(not pc.decision_significant, "按 Wilcoxon 判为不显著")
gc_ = g.gate_conclusion(pc)
check(not gc_.passed, "门 3 拦住 t/Wilcoxon 冲突样本（回归：曾被错误放行）")
check(any("检验" in i.name for i in gc_.blockers), "拦截原因指向检验项")

# ② 反向冲突：t 不显著 / Wilcoxon 显著 —— 行为要与文档一致（以 Wilcoxon 为准）
d_rev = np.array([0.5, 0.6, 0.4, 0.7, 0.55, 0.45, 0.65, 0.5, 0.6, 0.5, 0.55, -40.0])
pr_rev = g.paired_compare(d_rev, np.zeros_like(d_rev))
print(f"  反向：t p={pr_rev.p_value:.4f}  Wilcoxon p={pr_rev.wilcoxon_p:.4f}"
      f"  判决={pr_rev.decision_test}")
check(not pr_rev.tests_agree, "反向冲突也被识别")
check(pr_rev.decision_significant == pr_rev.wilcoxon_significant, "反向冲突同样以 Wilcoxon 为准")

# ③ 全零差值：不能报成"无穷显著"，也不能抛 RuntimeWarning
import warnings  # noqa: E402

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    pz = g.paired_compare(np.full(20, 5.0), np.full(20, 5.0))
    n_rw = sum(1 for w in caught if issubclass(w.category, RuntimeWarning))
print(f"  全零：t={pz.t_stat}  p={pz.p_value}  wilcoxon={pz.wilcoxon_p}  RuntimeWarning={n_rw}")
check(pz.p_value == 1.0, "全零差值 t 检验 p=1（回归：曾为 0）")
check(pz.wilcoxon_p == 1.0, "全零差值 Wilcoxon p=1")
check(pz.t_stat == 0.0, "全零差值 t 统计量为 0（回归：曾为 nan）")
check(not pz.decision_significant, "全零差值判为不显著")
check((pz.ci_low, pz.ci_high) == (0.0, 0.0), "全零差值置信区间为 [0, 0]")
check(n_rw == 0, "全零差值不产生 RuntimeWarning（回归）")
gz = g.gate_conclusion(pz)
check(not gz.passed, "全零差值不过门 3")
check(all("-1 个" not in i.fix for i in gz.items),
      "全零差值不输出荒谬的负样本数，改按不变量解释")

# ④ 恒定非零差值：方向确定，应当显著
pk = g.paired_compare(np.full(20, 5.5), np.full(20, 5.0))
print(f"  恒定 +0.5：t={pk.t_stat}  p={pk.p_value}  显著={pk.decision_significant}")
check(pk.decision_significant, "恒定非零差值判为显著")
check(g.gate_conclusion(pk, expected_direction="positive").passed,
      "正向预注册接受显著正差")
_wrong_direction = g.gate_conclusion(pk, expected_direction="negative")
check(not _wrong_direction.passed
      and any(i.name == "差异方向符合预注册" for i in _wrong_direction.blockers),
      "负向预注册会拦住统计显著但方向相反的劣化")

# ⑤ statement 与 gate3.passed 不得矛盾
for name, pp in (("冲突", pc), ("全零", pz)):
    res_dict = g.gate_conclusion(pp).as_dict()
    check(not res_dict["passed"], f"{name}样本门 3 结论为不通过")
check(
    all(k in pc.as_dict() for k in
        ("decision_test", "decision_p_value", "decision_significant", "tests_agree")),
    "as_dict 导出判决字段（供 MCP 与报告引用）",
)

# ---------------------------------------------------------------------------
sect("7  配置差分")

cfg1 = {"scenario": "UMa_NLOS", "isd_m": 500.0, "seed": 1, "num_samples": 10}
cfg2 = {"scenario": "UMa_NLOS", "isd_m": 800.0, "seed": 99, "num_samples": 200}
d = g.config_diff(cfg1, cfg2)
print(f"  差异：{d}")
check(set(d) == {"isd_m"}, "只报物理差异，忽略 seed / num_samples")

# ---------------------------------------------------------------------------
sect("8  门 1 · 信道可信")

r1 = ds.gate()
print(r1.text())
check(isinstance(r1.passed, bool), "门 1 给出明确结论")
check(len(r1.items) >= 10, "门 1 覆盖足够多的判据")
check(
    all(i.severity in ("block", "warn", "info") for i in r1.items),
    "每项都有明确严重度",
)

# ---------------------------------------------------------------------------
sect("9  门 2 · 比较公平")

fair = g.gate_comparison(
    {"name": "A", "dataset_id": "ds_x", "config": {"isd_m": 500.0}, "csi": "ideal"},
    {"name": "B", "dataset_id": "ds_x", "config": {"isd_m": 500.0}, "csi": "ideal"},
)
check(fair.passed, "同数据集同口径放行")

peek = g.gate_comparison(
    {"name": "我的", "dataset_id": "ds_x", "config": {}, "csi": "ideal"},
    {"name": "基线", "dataset_id": "ds_x", "config": {}, "csi": "estimated"},
)
print(f"  偷看理想信道 → 拦截项 {[i.name for i in peek.blockers]}")
check(not peek.passed, "一边理想一边估计被拦")
check(any("CSI" in i.name for i in peek.blockers), "拦截原因指向 CSI 口径")

# 但 CSI 口径本身可以是被测变量 —— 声明了就该放行
declared = g.gate_comparison(
    {"name": "理想CSI", "dataset_id": "ds_x", "config": {}, "csi": "ideal",
     "varies": ["csi"]},
    {"name": "估计CSI", "dataset_id": "ds_x", "config": {}, "csi": "estimated"},
)
check(declared.passed, "声明 varies=[csi] 后放行（测 CSI 误差的代价是正当实验）")

drift = g.gate_comparison(
    {"name": "A", "dataset_id": "ds_x", "config": {"isd_m": 500.0, "speed_kmh": 3},
     "csi": "ideal"},
    {"name": "B", "dataset_id": "ds_x", "config": {"isd_m": 800.0, "speed_kmh": 30},
     "csi": "ideal", "varies": ["isd_m"]},
)
print(f"  配置漂移 → 拦截项 {[i.name for i in drift.blockers]}")
check(not drift.passed, "未声明的配置差异被拦")

split = g.gate_comparison(
    {"name": "A", "dataset_id": "ds_1", "config": {}, "csi": "ideal"},
    {"name": "B", "dataset_id": "ds_2", "config": {}, "csi": "ideal"},
)
check(not split.passed, "两臂用不同数据集被拦")

# ---------------------------------------------------------------------------
sect("10  门 3 · 结论站得住")

ok3 = g.gate_conclusion(pr)
check(ok3.passed, "真实显著差异过门")

bad3 = g.gate_conclusion(pr0)
print(f"  无差异 → 拦截项 {[i.name for i in bad3.blockers]}")
check(not bad3.passed, "不显著的差异被拦")

dom3 = g.gate_conclusion(prx)
check(
    any("单个样本" in i.name for i in dom3.blockers) or not dom3.passed,
    "单样本主导被拦",
)

# ---------------------------------------------------------------------------
sect("11  端到端：同批信道跑两个方案")

res = ds.compare_arms(
    {"name": "SVD", "method": "svd", "csi": "ideal"},
    {"name": "DFT波束", "method": "dft", "csi": "ideal"},
    max_samples=30,
)
print(res.text())
expected_independent_positions = min(int(ds.config["num_ues"]), ds.n, 30)
check(res.paired.n == expected_independent_positions, "配对样本数按独立 UE 位置聚类")
check(res.paired.n < min(ds.n, 30), "重复快照不会冒充独立统计样本")
check(res.paired.mean_a > res.paired.mean_b, "SVD 优于 DFT 单波束")
check(res.gate2.passed, "公平性门通过")
check("结论" in res.statement(), "给出可直接引用的结论句")
_inf = res.as_dict()["inference_unit"]
check(_inf["clustered_by"] == "ue_position"
      and _inf["raw_observations"] == min(ds.n, 30)
      and _inf["independent_pairs"] == res.paired.n
      and _inf["fallback_reason"] is None,
      "推断单位（原始观测 / 独立对 / 聚类依据）随结果一起报出")

# --- 聚不了类时必须出声，不能静默按逐样本推断 ---------------------------------
# 聚类失败的方向是**把区间报窄、把 p 值报小**，正好是危险的那一侧。
# 另外 Dataset.ue_position 是直接索引 NPZ 的 cached_property，缺键时抛 KeyError，
# 而 getattr(..., None) 只兜 AttributeError——老数据集会直接崩在这里。
_se_a = np.arange(6, dtype=float) + 1.0
_se_b = _se_a * 0.9


class _NoPosDs:
    @property
    def ue_position(self):
        raise KeyError("ue_position")


for _mode, _obj, _needle in (
    ("缺键", _NoPosDs(), "取不到"),
    ("全 NaN", type("_D", (), {"ue_position": np.full((6, 3), np.nan)})(), "非有限"),
    ("形状不对", type("_D", (), {"ue_position": np.zeros(6)})(), "形状"),
):
    _a, _b, _ids, _why = g._position_clusters(_obj, 6, _se_a, _se_b)
    check(_ids is None and _needle in _why and np.array_equal(_a, _se_a),
          f"{_mode}时退回逐样本推断并给出原因（{_why}）")

_fallback_item = g.GateItem(
    "重复快照按 UE 位置聚类", False, "x", severity="warn")
check(_fallback_item.severity != "block",
      "聚类失败只告警不拦截——这是聚类上线前的历史行为，但不能装作验证过独立性")

# 过不了门时必须明说结论不成立
tie = ds.compare_arms(
    {"name": "SVD-1", "method": "svd", "csi": "ideal"},
    {"name": "SVD-2", "method": "svd", "csi": "ideal"},
    max_samples=30,
)
print(f"  自己跟自己比：{tie.statement()}")
check(not tie.passed, "零差异对比不通过门 3")
check("不成立" in tie.statement(), "结论句明说不成立")

# ---------------------------------------------------------------------------
sect("12  结论模板空槽")

allslots = dec.missing_slots(set(), set())
print(f"  全空时 {len(allslots)} 个槽：{[s['slot'] for s in allslots]}")
check(len(allslots) >= 5, "空槽数量合理")
check(all(s["options"] for s in allslots), "每个槽都带选项")
check(
    all(2 <= len(s["options"]) <= 5 for s in allslots),
    "每题选项数在 2~5 之间（superpowers 的做法是别给太多）",
)
check(
    any(o.get("recommended") for s in allslots for o in s["options"]),
    "有推荐项",
)

part = dec.missing_slots({"baseline", "metric"}, {"scenario"})
print(f"  答了 3 项后剩 {[s['slot'] for s in part]}")
check(len(part) == len(allslots) - 3, "答过的槽不再问")
check(all(s["slot"] not in ("baseline", "metric", "scenario") for s in part),
      "已答的槽被排除")

# ---------------------------------------------------------------------------
sect("13  干扰建模的自动推导与检出")

print(f"  bs_panel={summ.get('bs_panel')}（推导={summ.get('bs_panel_derived')}）")
check(bool(summ.get("bs_panel")), "生成时自动推导出 bs_panel")

multi, msum = make(12, num_sites=7, sectors_per_site=3, isd_m=200.0)
print(f"  多小区：{msum['cells_actual']} 小区，干扰建模={msum['interference_modeled']}")
check(bool(msum["interference_modeled"]), "多小区场景下干扰确实进了 SINR")
sinr = np.asarray(multi.sinr_dB)
snr = np.asarray(multi.snr_dB)
check(not np.allclose(sinr, snr), "SINR 不等于纯热噪声 SNR")

from superran import validate as va  # noqa: E402

c = va.check_interference_modeled(multi)
print(f"  检查：{c.detail}")
check(c.passed, "干扰检查放行正确配置")

# ---------------------------------------------------------------------------
sect("14  引擎清单的稳定性")

# 清单长度不该随环境变化，变的只是 available 与 missing。
# 早先没装 ChannelHub 时只返回 internal_sim 一条，调用方写
# engines["sionna_rt"] 会 KeyError，看起来像工具坏了。
caps = {x.name: x for x in ch.probe_capabilities()}
print(f"  引擎 {len(caps)} 个：" + "  ".join(
    f"{k}={'可用' if v.available else '不可用'}" for k, v in caps.items()))
check(set(caps) == {"internal_sim", "sionna_rt", "quadriga_real"},
      "三个引擎恒在清单中（不随 ChannelHub / sionna-rt 是否存在而消失）")
check(all(v.available or v.missing for v in caps.values()),
      "不可用的引擎必须列出缺失项，不能只说不可用")
check(all(v.detail for v in caps.values()), "每个引擎都有可读说明")

# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("校准、标准表、三道门全部通过。")
