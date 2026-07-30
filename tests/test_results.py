"""外部算法结果契约 + 预注册分析口径的测试。

重点是**错配必须被拦住**：配对比较的全部有效性建立在"第 i 个数对应同一个
信道实例"上，错配时它照样会算出一个看起来很显著的 p 值。

直接运行：python tests/test_results.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Windows 中文控制台是 GBK，print 到它可能编不出某些字符；统一 replace 兜底。
sys.stdout.reconfigure(errors="replace")

from superwireless import analysis as an  # noqa: E402
from superwireless import channelhub as ch  # noqa: E402
from superwireless import gates as g  # noqa: E402
from superwireless import generate as gen  # noqa: E402
from superwireless import load  # noqa: E402
from superwireless import plan as pl  # noqa: E402
from superwireless import results as rs  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(t: str) -> None:
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


ch.warmup()

# ---------------------------------------------------------------------------
sect("1  预注册：锁口径")

pr = an.lock(
    draft_id="dr_test", primary_metric="spectral_efficiency", baseline="type1",
    csi_basis="estimated", expected_effect=1.5,
    secondary_metrics=["sinr_db"], note="单测",
)
print(pr.text())
check(pr.prereg_id.startswith("pr_"), "生成了 prereg_id")
check(len(pr.digest) == 64, "摘要是 SHA-256")
check(an.verify(pr), "摘要与内容自校验通过")
check(pr.metric_unit == "bit/s/Hz", "已知指标自动带单位")

# 同样的输入应当给同样的摘要（确定性序列化）
pr2 = an.lock(
    draft_id="dr_test", primary_metric="spectral_efficiency", baseline="type1",
    csi_basis="estimated", expected_effect=1.5,
    secondary_metrics=["sinr_db"], note="单测",
)
check(pr2.digest == pr.digest, "相同口径 → 相同摘要")
check(pr2.prereg_id != pr.prereg_id, "但 ID 不同（改动不覆盖旧文件）")

# 改一个字段摘要就变
pr3 = an.lock(draft_id="dr_test", primary_metric="edge_user_se", baseline="type1")
check(pr3.digest != pr.digest, "改主指标 → 摘要变")

loaded = an.load(pr.prereg_id)
check(loaded.digest == pr.digest, "存盘再读回内容一致")

# 被手改过的文件要能查出来
tampered = an.Prereg(**{**pr.as_dict(), "primary_metric": "偷偷换掉"})
check(not an.verify(tampered), "内容被改但摘要没改 → 校验失败")

# ---------------------------------------------------------------------------
sect("2  指标身份分类")

for metric, want in (
    ("spectral_efficiency", "primary"),
    ("sinr_db", "secondary"),
    ("edge_user_se", "exploratory"),
):
    c = an.classify(pr, metric)
    print(f"  {metric:22s} → {c['status']}")
    check(c["status"] == want, f"{metric} 判为 {want}")
    check(c["primary"] == (want == "primary"), f"{metric} 的 primary 标志正确")

c_none = an.classify(None, "spectral_efficiency")
print(f"  无预注册               → {c_none['status']}")
check(c_none["status"] == "unregistered" and not c_none["primary"],
      "没有预注册时不算 primary（没登记就不能声称事先定过）")
c_bad = an.classify(tampered, "spectral_efficiency")
check(c_bad["status"] == "tampered" and not c_bad["primary"], "被改过的预注册不算 primary")

# ---------------------------------------------------------------------------
sect("3  生成时绑定预注册")

pr_gen = an.lock(primary_metric="spectral_efficiency", baseline="type1",
                 csi_basis="estimated", expected_effect=1.0)
d, _ = pl.create_draft("蒙特卡洛评估谱效", overrides={
    "num_samples": 24, "num_ues": 8, "antenna_preset": "4T4R",
    "bandwidth_hz": 20000000.0,
})
cfg, _ = pl.resolved_config(d)
cfg.pop("num_samples", None)
summ = gen.generate(cfg, num_samples=24, draft_id=d.draft_id, prereg_id=pr_gen.prereg_id)
ds = load(summ["dataset_id"])
print(f"  数据集 {ds.dataset_id}  n={ds.n}")
print(f"  绑定的预注册: {ds.prereg}")
check(ds.prereg is not None, "预注册已随数据存档")
check(ds.prereg["prereg_id"] == pr_gen.prereg_id, "prereg_id 正确")
check(ds.prereg["digest"] == pr_gen.digest, "摘要一并存档")
check(ds.prereg["locked_before_generation"] is True, "标记为生成前锁定")

# 绑一个不存在的预注册要如实记下来，不能静默忽略
summ_bad = gen.generate(cfg, num_samples=8, prereg_id="pr_doesnotexist")
check(load(summ_bad["dataset_id"]).prereg.get("error"), "绑不存在的预注册时如实报错")

# ---------------------------------------------------------------------------
sect("4  数据集摘要与样本 ID")

dg = ds.digest()
print(f"  digest {dg[:24]}…")
check(len(dg) == 64, "摘要是 SHA-256")
check(ds.digest() == dg, "第二次取值一致（走缓存）")
check(ds.summary.get("dataset_digest") == dg, "摘要已缓存进 summary.json")

ids = ds.sample_ids()
print(f"  sample_ids {ids[:2]} … 共 {len(ids)}")
check(len(ids) == ds.n, "ID 数等于样本数")
check(len(set(ids)) == ds.n, "ID 唯一")
check(all(i.startswith(ds.dataset_id + "#") for i in ids), "ID 带 dataset_id 前缀")

# ---------------------------------------------------------------------------
sect("5  注册外部结果")

rng = np.random.default_rng(11)
base_vals = rng.normal(12.0, 3.0, ds.n)
mine_vals = base_vals + rng.normal(1.2, 0.5, ds.n)  # 系统性高 1.2

art_a = ds.register_results(
    "我的方法", mine_vals, metric="spectral_efficiency",
    method_metadata={"csi": "estimated", "note": "单测用的假算法"},
)
art_b = ds.register_results(
    "基线", base_vals, metric="spectral_efficiency",
    method_metadata={"csi": "estimated", "method": "type1"},
)
print(art_a.text())
check(art_a.result_id.startswith("res_"), "生成了 result_id")
check(art_a.n == ds.n, "样本数正确")
check(abs(art_a.mean - float(mine_vals.mean())) < 1e-9, "均值摘要正确")
check(art_a.dataset_digest == dg, "记录了数据集摘要")
check(art_a.prereg_id == pr_gen.prereg_id, "自动继承数据集绑定的预注册")
check(np.allclose(art_a.values(), mine_vals), "逐样本值可原样取回")
check(art_a.ids() == ids, "样本 ID 可原样取回")

# 逐样本值不能进 MCP JSON
d_json = art_a.as_dict()
check("values" not in d_json, "as_dict 不含逐样本值（只回句柄与摘要）")
check("values_path" in d_json and "values_sha256" in d_json, "但给出路径与内容摘要")

reloaded = rs.load(art_a.result_id)
check(reloaded.values_sha256 == art_a.values_sha256, "存盘再读回一致")

# 非有限值必须当场报错，不能悄悄丢样本
bad = mine_vals.copy(); bad[3] = np.nan
try:
    ds.register_results("坏的", bad)
    check(False, "含 nan 的结果应当被拒")
except ValueError as e:
    print(f"  拒绝 nan：{str(e)[:60]}…")
    check(True, "含 nan 的结果被拒（否则配对时两臂样本数会悄悄变少）")

# 长度不对也要拒
try:
    ds.register_results("短的", mine_vals[:5])
    check(False, "长度不符应当被拒")
except ValueError as e:
    print(f"  拒绝长度不符：{str(e)[:60]}…")
    check(True, "长度与数据集不符时被拒")

# 只算部分样本是允许的，但必须显式传 ids
part = ds.register_results("部分", mine_vals[:10], ids=ids[:10])
check(part.n == 10, "显式传 ids 时允许只覆盖部分样本")

# ---------------------------------------------------------------------------
sect("6  错配必须被拦住")

# ① 样本顺序被打乱 —— 最隐蔽的一种
shuffled_ids = list(ids)
shuffled_ids[0], shuffled_ids[1] = shuffled_ids[1], shuffled_ids[0]
art_shuf = ds.register_results("顺序乱了", base_vals, ids=shuffled_ids)
iss = rs.check_pairable(art_a, art_shuf)
print(f"  顺序错位 → 拦截 {[i['check'] for i in iss]}")
check(any(i["check"] == "样本顺序一致" for i in iss), "顺序错位被拦（长度相同也要逐个比）")
check("集合相同，只是顺序被打乱了" in iss[0]["detail"], "指出是顺序问题而非集合不同")

# ② 样本数不同
iss2 = rs.check_pairable(art_a, part)
print(f"  样本数不同 → 拦截 {[i['check'] for i in iss2]}")
check(any(i["check"] == "样本数一致" for i in iss2), "样本数不同被拦")

# ③ 指标不同
art_other = ds.register_results("另一个指标", base_vals, metric="nmse_db")
iss3 = rs.check_pairable(art_a, art_other)
print(f"  指标不同 → 拦截 {[i['check'] for i in iss3]}")
check(any(i["check"] == "指标一致" for i in iss3), "指标不同被拦")

# ④ 不同数据集
ds2 = load(summ_bad["dataset_id"])
art_ds2 = ds2.register_results("别的数据集", rng.normal(12, 3, ds2.n))
iss4 = rs.check_pairable(art_a, art_ds2)
print(f"  不同数据集 → 拦截 {[i['check'] for i in iss4]}")
check(any(i["check"] == "同一数据集" for i in iss4), "不同数据集被拦")

# 正常情况不该有拦截项
check(not rs.check_pairable(art_a, art_b), "两臂对齐时无拦截项")

# ---------------------------------------------------------------------------
sect("7  外部结果过门 2 / 门 3")

r = g.compare_results(art_a.result_id, art_b.result_id)
print(r.text())
check(r.gate2.passed, "门 2 通过")
check(r.gate3.passed, "门 3 通过")
check(r.paired.n == ds.n, "配对样本数正确")
check(r.paired.decision_significant, "真实差异被检出")
check(r.metric == "spectral_efficiency", "结论句带指标名")
check("bit/s/Hz" in r.statement(), "结论句带单位")
check(r.identity["status"] == "primary", "判为预注册主结论")
check(pr_gen.prereg_id in r.statement(), "结论句里写出预注册号")

# 与内置对比走的是同一套统计实现
pc = g.paired_compare(art_a.values(), art_b.values())
check(abs(pc.decision_p_value - r.paired.decision_p_value) < 1e-30,
      "外部结果与内置用同一套统计实现（判决标准一致）")

# ---------------------------------------------------------------------------
sect("8  错配时不做统计，并明说")

r_bad = g.compare_results(art_a.result_id, art_shuf.result_id)
print(f"  门 2: {[i.name for i in r_bad.gate2.blockers]}")
print(f"  门 3: {[i.name for i in r_bad.gate3.blockers]}")
print(f"  {r_bad.statement()}")
check(not r_bad.gate2.passed, "错配时门 2 拦住")
check(not r_bad.gate3.passed, "门 3 也不通过")
check(not np.isfinite(r_bad.paired.mean_diff), "统计已跳过，不给出假的差值")
check("无法比较" in r_bad.statement(), "结论句明说无法比较")
check("p 值没有意义" in r_bad.statement(), "说清为什么不算 p 值")

# ---------------------------------------------------------------------------
sect("9  探索性分析必须被标出来")

# 用一个没预注册的指标下结论
art_x = ds.register_results("我的方法(边缘用户)", mine_vals, metric="edge_user_se",
                            method_metadata={"csi": "estimated"})
art_y = ds.register_results("基线(边缘用户)", base_vals, metric="edge_user_se",
                           method_metadata={"csi": "estimated"})
r_exp = g.compare_results(art_x.result_id, art_y.result_id)
print(f"  身份: {r_exp.identity['status']}")
print(f"  {r_exp.statement()}")
check(r_exp.identity["status"] == "exploratory", "未预注册的指标判为探索性")
check(r_exp.gate3.passed, "统计上仍然成立（探索性≠不成立）")
check("探索性" in r_exp.statement(), "结论句明说这是探索性分析")
check("不是预注册主结论" in r_exp.statement(), "明说不能当主结论")
check(r_exp.identity.get("how_to_claim"), "给出想升为主结论该怎么做")

# ---------------------------------------------------------------------------
sect("10  CSI 口径：外部结果只能靠声明")

art_no_meta = ds.register_results("没写口径", mine_vals)
r_nm = g.compare_results(art_no_meta.result_id, art_b.result_id)
csi_item = next(i for i in r_nm.gate2.items if "CSI" in i.name)
print(f"  [{csi_item.severity}] {csi_item.detail[:70]}…")
check(csi_item.severity == "warn", "没声明 CSI 口径时给告警而非放行沉默")
check("MCP 看不到" in csi_item.detail, "如实说明 MCP 查不了外部代码内部")

art_mix = ds.register_results("理想CSI", mine_vals, method_metadata={"csi": "ideal"})
r_mix = g.compare_results(art_mix.result_id, art_b.result_id)
print(f"  混用口径 → 门 2 {'通过' if r_mix.gate2.passed else '拦截 ' + str([i.name for i in r_mix.gate2.blockers])}")
check(not r_mix.gate2.passed, "两臂声明的 CSI 口径不同时被拦")

art_dec = ds.register_results("理想CSI(已声明)", mine_vals,
                              method_metadata={"csi": "ideal", "varies": ["csi"]})
r_dec = g.compare_results(art_dec.result_id, art_b.result_id)
check(r_dec.gate2.passed, "声明 varies=[csi] 后放行（测 CSI 误差代价是正当实验）")

# ---------------------------------------------------------------------------
sect("11  评测脚本模板")

tpl = ds.eval_template()
code = tpl["code"]
print(f"  代码 {len(code)} 字符，预注册 {tpl['prereg_id']}")
check("def my_algorithm" in code, "模板含待替换的算法入口")
check("results.register" in code, "模板含注册调用")
check("IDS" in code and "sample_ids" in code, "模板用统一的 sample_ids")
check("h_for_precoding=h_est" in code, "示例里预编码只看估计信道")
check(tpl["prereg_id"] == pr_gen.prereg_id, "模板带上数据集绑定的预注册")
check(len(tpl["guardrails"]) >= 3, "给出护栏提示")
compile(code, "template", "exec")  # 语法必须正确
check(True, "模板代码语法正确（可直接运行）")

# ---------------------------------------------------------------------------
sect("12  清单与列表")

lst = rs.list_results(ds.dataset_id)
print(f"  该数据集已注册 {len(lst)} 个结果")
check(len(lst) >= 2, "能列出已注册结果")
check(all("values" not in x for x in lst), "清单里不含逐样本值")
pregs = an.list_pregs()
check(len(pregs) >= 3, "能列出预注册记录")

# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("外部算法结果契约与预注册全部通过。")
