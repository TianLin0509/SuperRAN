"""端到端冒烟测试：能力探测 → 提案 → 生成 → 取货 → 真跑取货代码。

直接运行：python tests/test_e2e.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Windows 中文控制台是 GBK：print 含 U+FFFD 等字符时会炸 UnicodeEncodeError，
# 把测试输出吓成"失败"。统一 reconfigure，本文件的 print 全部 replace 兜底。
sys.stdout.reconfigure(errors="replace")

from superran import channelhub as ch  # noqa: E402
from superran import decisions as dec  # noqa: E402
from superran import deliver as dlv  # noqa: E402
from superran import generate as gen  # noqa: E402
from superran import plan as pl  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(title: str) -> None:
    print("\n" + "=" * 68 + f"\n{title}\n" + "=" * 68)


# ---------------------------------------------------------------------------
sect("1  能力探测")
caps = {c.name: c for c in ch.probe_capabilities()}
for c in caps.values():
    print(f"  {c.name:<16} {'可用' if c.available else '不可用':<6} {c.detail}")
check(caps["internal_sim"].available, "internal_sim 可用")
models = ch.list_channel_models()
print(f"  CDL: {models['cdl']}")
print(f"  TDL: {models['tdl']}")
check(len(models["cdl"]) == 5, "5 个 CDL 剖面可用")

# ---------------------------------------------------------------------------
sect("2  意图识别与决策点")
cases = [
    ("验证一个 CSI 压缩的想法，单小区 64T4R", "csi_compression"),
    ("我想做基于到达角的波束搜索", "beam_management"),
    ("多小区干扰协调算法验证", "interference"),
    ("SRS 信道老化对互易性的影响", "reciprocity"),
    ("随便给我点信道数据", "generic"),
]
for text, expect in cases:
    prof = dec.classify_intent(text)
    ok = prof.task == expect
    print(f"  {'OK ' if ok else 'ERR'} {text[:28]:<30} → {prof.task}")
    check(ok, f"意图识别：{expect}")

prof = dec.classify_intent("验证 CSI 压缩")
picked = dec.decisions_for(prof, limit=5)
print(f"\n  CSI 压缩任务会问 {len(picked)} 个问题：")
for d in picked:
    print(f"    - {d.question}  默认 {d.default}")
check(3 <= len(picked) <= 6, "问题数量在 3~6 之间")
extra = dec.also_configurable(prof)
print(f"  另有 {len(extra)} 个可配项（只给名字）：{'、'.join(extra[:8])}…")
check(len(extra) > 5, "提供了 also_configurable 关键词列表")

# ---------------------------------------------------------------------------
sect("3  体检拦截：波束搜索 + TDL 应被拦下")
prof_beam = dec.classify_intent("波束搜索算法")
issues = dec.check_guards(prof_beam, {"channel_model": "TDL-C", "num_sites": 1})
for i in issues:
    print(f"  [{i['severity']}] {i['key']}: {i['message'][:60]}…")
check(any(i["severity"] == "block" for i in issues), "TDL + 波束任务被拦截")

ok_issues = dec.check_guards(prof_beam, {"channel_model": "CDL-C", "num_sites": 1})
check(not [i for i in ok_issues if i["severity"] == "block"], "CDL + 波束任务放行")

# ---------------------------------------------------------------------------
sect("4  提案生成")
draft, prof = pl.create_draft("验证 CSI 压缩，用最小配置先跑通流程")
proposal = pl.build_proposal(draft, prof, max_questions=5)
print(f"  draft_id     {proposal['draft_id']}")
print(f"  任务类型     {proposal['task_label']}")
print(f"  场景骨架     {proposal['preset']}  ({proposal['preset_label']})")
rq = proposal["round_questions"]
print(f"  可直接生成   {proposal['ready_to_go']}")
print(f"  第 {proposal['round']} 轮 · {proposal['round_focus']}：{len(rq)} 问")
print(f"  首个问题     {rq[0]['question']}  [{rq[0]['layer']}]")
print(f"    why       {rq[0]['why'][:70]}…")
check(proposal["ready_to_go"], "提案可直接生成（用户不表态也能走）")
check(1 <= len(rq) <= 3, f"一轮 1~3 问（实际 {len(rq)}；只问前沿问题）")
check(all(sum(o["recommended"] for o in q["options"]) == 1 for q in rq), "每题恰好一个推荐项")
check(all(q.get("why") for q in rq), "每个问题都带 why")
check(all(q.get("options") for q in rq), "每个问题都带选项")
check("num_bs_tx_ant" in proposal["resolved_config"], "抽象参数已翻译成 ChannelHub 实参")

# ---------------------------------------------------------------------------
sect("5  差分修正")
d2, p2, changes = pl.revise_draft(draft.draft_id, {"channel_model": "CDL-D", "num_samples": 4})
print(f"  改动：{changes}")
check(len(changes) == 2, "修正记录了 2 处改动")
check(d2.params["channel_model"] == "CDL-D", "参数已更新")

# ---------------------------------------------------------------------------
sect("6  生成（4 个样本，最小配置）")
cfg, own = pl.resolved_config(d2)
cfg.pop("num_samples", None)
print(f"  预估体积 {gen.estimate_size_mb(cfg, 4):.1f} MB")
summary = gen.generate(cfg, num_samples=4, plan_markdown="# 测试计划", draft_id=d2.draft_id)
print(f"  dataset_id   {summary['dataset_id']}")
print(f"  形状         {summary['shape']}")
print(f"  耗时         {summary['elapsed_s']}s  ({summary['seconds_per_sample']}s/样本)")
print(f"  体积         {summary['size_mb']} MB")
print(
    f"  信道模型     configured={summary['configured_channel_model']}  "
    f"effective={summary['effective_channel_model_counts']}  含角度={summary['is_cdl']}"
)
check(bool(summary["effective_channel_model_counts"]), "摘要显式区分配置剖面与逐链路实际剖面")
print(f"  SINR 分布    {summary['sinr_dB']}")
print(f"  路损分布     {summary.get('pathloss_dB')}")
print(f"  视距比例     {summary.get('los_ratio')}")
check(summary["num_samples"] == 4, "生成了 4 个样本")
check(summary["shape"]["BS_ant"] == 4, "天线维度正确")
check("pathloss_dB" in summary, "路损等物理量已接出（无需改 ChannelHub）")
check(summary["config"]["sample_interval_s"] == 0.005,
      "新数据显式冻结5 ms快照时钟，不继承外部源的隐式默认")
check(summary["channel_contract"]["sample_interval_s"] == 0.005,
      "快照时钟进入不可变channel contract")

ds_id = summary["dataset_id"]

# ---------------------------------------------------------------------------
sect("7  取货代码生成")
res = dlv.build_code(ds_id, "信道 + PMI + SRS + 时延功率谱 + 几何")
print(f"  解析出的测量量：{res['measurements']}")
check("pmi" in res["measurements"], "自然语言 'PMI' 解析成功")
check("srs" in res["measurements"], "自然语言 'SRS' 解析成功")
check("pdp" in res["measurements"], "自然语言 '时延功率谱' 解析成功")
check("geometry" in res["measurements"], "自然语言 '几何' 解析成功")
check(res["measurements"][0] == "channel", "信道永远排第一（是其他量的原料）")

# ---------------------------------------------------------------------------
sect("8  真的把取货代码跑一遍")
with tempfile.TemporaryDirectory() as td:
    script = Path(td) / "fetch.py"
    script.write_text(res["code"], encoding="utf-8")
    # 子进程的编码要**显式统一成 UTF-8**，不能只在父进程按 UTF-8 解码。
    # Windows 默认 GBK：子进程按 GBK 输出中文，父进程按 UTF-8 解码 → 乱码 →
    # `errors="replace"` 把乱码换成 U+FFFD → 测试照样"通过"，
    # 然后父进程再把 U+FFFD 打到 GBK 控制台时才炸，而且报的是父进程的错。
    # PYTHONIOENCODING 管子进程的 stdout/stderr 编码，PYTHONUTF8 管其内部默认编码。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",  # 出乱码就抛，不许静默替换
        timeout=300,
        env=env,
    )
    out = (proc.stdout or "").strip()
    print(out[:1400])
    if proc.returncode != 0:
        print("STDERR:\n" + (proc.stderr or "")[-2500:])
    check(proc.returncode == 0, "取货代码可直接运行")
    # 光看返回码抓不到编码问题，必须断言输出内容。
    # 上面已断言 measurements 含 pdp，取货代码里 PDP 那段会打印"RMS 时延扩展 … ns"。
    check("�" not in out, "子进程输出无替换字符 U+FFFD（编码链路正确）")
    check("时延扩展" in out, "子进程输出的中文正确传回")

# ---------------------------------------------------------------------------
sect("9  测量量物理正确性抽查")
from superran import load  # noqa: E402

ds = load(ds_id)
p = ds.pdp(0)
print(f"  PDP 峰值 {p.power.max():.3e}（未归一化，不是 1.0）")
print(f"  RMS 时延扩展 {p.rms_delay_spread_s * 1e9:.1f} ns")
check(abs(p.power.max() - 1.0) > 1e-9, "PDP 未被归一化到 1（与 bridge 的关键差别）")
check(p.delays_s[1] > 0, "PDP 带真实时延轴")

f = ds.srs(0)
print(f"  协方差 {f.covariance.shape}，特征值 {len(f.eigenvalues)} 个（非只取 4 个）")
check(len(f.eigenvalues) == ds.h_true.shape[3], "返回全部特征值")

w = ds.pmi(0)
print(f"  PMI 索引 {w.indices}，秩 {w.rank}，码本 {w.codebook_size} 列，阵型 {w.layout}")
check(len(w.indices) >= 1 and w.codebook_size > 1, "PMI 返回码本索引")

paths = ds.paths()
print(f"  径数 {paths.num_paths}，含角度 {paths.aoa_rad is not None}")
check(paths.aoa_rad is not None, "CDL 模型带每径角度")
check(paths.delays_s.max() > 0, "每径时延非零")

g = ds.rsrp(0)
print(f"  每天线增益 {g.min():.1f} ~ {g.max():.1f} dB")
check(bool(np.isfinite(g).all()) and g.size > 0, "RSRP 可取且全部有限")

geo = ds.geometry
print(f"  几何字段：{sorted(geo)}")
check("pathloss_dB" in geo and "is_los" in geo, "几何量含路损与视距判定")

# ---------------------------------------------------------------------------
sect("10  TDL 模型应当没有角度")
d3, p3 = pl.create_draft("随便给点信道", preset="single_cell_4t4r",
                         overrides={"channel_model": "TDL-C", "num_samples": 2})
cfg3, _ = pl.resolved_config(d3)
cfg3.pop("num_samples", None)
s3 = gen.generate(cfg3, num_samples=2)
ds3 = load(s3["dataset_id"])
paths3 = ds3.paths()
print(f"  TDL-C 径数 {paths3.num_paths}，含角度 {paths3.aoa_rad is not None}")
check(paths3.aoa_rad is None, "TDL 确实没有角度（与 CDL 形成对照）")
res3 = dlv.build_code(s3["dataset_id"], "角度")
print(f"  取货提示：{res3['notes']}")
check(bool(res3["notes"]), "TDL 要角度时给出了警告")

# ---------------------------------------------------------------------------
sect("9  访谈：先读原话，只问会改变结论、而平台在替人拍的事")
# 这 5 句是 2026-09-26 的基线案例：旧流程在每一句上都问错了（见 interview.py 文档）。
import inspect  # noqa: E402

from superran import factors as fx  # noqa: E402
from superran import interview as iv  # noqa: E402
from superran import server as srv  # noqa: E402


def _round1(text):
    d, p = pl.create_draft(text)
    pr = pl.build_proposal(d, p)
    return d, p, pr, [q["key"] for q in pr["round_questions"]]


d_isd, p_isd, pr_isd, k_isd = _round1("我想用superRAN来做一个无线仿真，来对比下站间距下的干扰变化情况")
print(f"  站距 → {d_isd.form} {k_isd}")
check(d_isd.form == "sweep_condition", "站距-干扰识别为“扫一个条件”")
check("deployment" in k_isd and "sweep_values" not in k_isd and "baseline" not in k_isd,
      "站距案例第一轮问部署；站距档位依赖部署，留到下一轮；不问码本基线")
check(any("生成层变量" in n for n in pr_isd["upfront_notices"]),
      "生成层变量没有配对判决作为开跑前声明给出，不占提问名额")
check(any("室内" in x for x in pr_isd["assumption_ledger"]["conclusion_limits"])
      and not any("室内" in x for x in pr_isd["assumption_ledger"]["silently_assumed"]),
      "未建模项一律进结论边界，不混在沉默假设里")
check(any("发射功率" in x for x in pr_isd["assumption_ledger"]["silently_assumed"]),
      "发射功率 46 dBm 列为沉默假设")
check(any("撒点" in x for x in pr_isd["assumption_ledger"]["conclusion_limits"]),
      "未实现且无选项的撒点列为结论边界而不是待答问题")

d_sinr, _, pr_sinr, k_sinr = _round1("评估一下密集城区下行 SINR 分布")
print(f"  SINR 分布 → {d_sinr.form} {d_sinr.preset} {k_sinr}")
check(d_sinr.preset == "company_64t4r_multicell", "干扰/SINR 类目标量不会落到单小区骨架")
check("deployment" in k_sinr and not {"channel_model", "snr_range_dB"} & set(k_sinr),
      "SINR 分布先问部署，不问信道模型和信噪比范围")

d_csi, _, _, k_csi = _round1("验证一个 CSI 压缩的想法，单小区 64T4R，跟 Type II 码本比")
print(f"  CSI → {d_csi.form} {k_csi} baseline={d_csi.design.get('baseline')}")
check(d_csi.form == "compare_methods" and "baseline" not in k_csi,
      "原话已给基线（Type II）就不再问")
check(d_csi.params.get("antenna_preset") == "64T4R" and d_csi.params.get("num_sites") == 1,
      "原话里的阵型与单小区直接写进草稿")

d_pdp, _, pr_pdp, k_pdp = _round1("只要 20 个 64T4R CDL-C 信道，给我 PDP，不比算法")
print(f"  PDP → {d_pdp.form} {k_pdp}")
check(d_pdp.form == "deliver" and not k_pdp, "数据交付不提问（不问指标、不问基线）")
check(d_pdp.params.get("num_samples") == 20 and d_pdp.params.get("channel_model") == "CDL-C",
      "数量与信道模型取自原话")

d_srs, _, _, k_srs = _round1("SRS 周期从 10 ms 改到 20 ms，看 120 km/h 用户的边缘速率")
print(f"  SRS → {d_srs.form} {d_srs.sweep} {k_srs}")
check(d_srs.sweep == {"key": "srs_period_ms", "values": [10.0, 20.0]}
      and d_srs.params.get("ue_speed_kmh") == 120.0, "扫描变量与速度取自原话")
check(k_srs and k_srs[0] == "srs_period_adaptive" and "baseline" not in k_srs,
      "比较 SRS 周期第一题就问要不要关自适应周期")

# 设计时没见过的 4 句（泛化检查，防止只对上面 5 句过拟合）
d_ant, _, _, k_ant = _round1("64T 和 32T 在 500 m 站距下的下行边缘速率差多少")
check(d_ant.sweep and d_ant.sweep["key"] == "antenna_preset",
      "“64T 和 32T 差多少”识别为扫天线规模")
d_load, _, _, _ = _round1("我想知道邻区负载从 30% 到 90% 时用户体验速率掉多少")
check(d_load.sweep == {"key": "neighbor_prb_util", "values": [0.3, 0.9]},
      "“负载从 30% 到 90%”识别为扫系统级邻区负载")
d_pe, _, pr_pe, _ = _round1("PF 和 EDF 调度对小包时延的影响对比")
_pe2d, _pe2p, _ = pl.revise_draft(d_pe.draft_id, design={"edf_meaning": "drain_first"})
_pend2 = pl.interview_state(_pe2d, _pe2p)["pending"]
bq = next(q.as_dict() for q in _pend2 if q.key == "baseline")
tq = next((q for q in pr_pe["round_questions"] if q["key"] == "traffic_model"), None)
check("baseline" not in d_pe.design and "PF" in bq["options"][0]["label"]
      and "码本" not in bq["question"], "“PF 和 EDF”不替用户认定基线，基线题在两者之间选")
k_pe = [q["key"] for q in pr_pe["round_questions"]]
check(k_pe[0] == "edf_meaning" and "baseline" not in k_pe,
      "先确认 EDF 指哪个；基线依赖它，留到下一轮")
check("traffic_model" in k_pe and any(b["key"] == "traffic_model" and b["recommended"] == "mixed"
                                      for b in pr_pe["blocking_defaults"]),
      "看小包时延时默认 FTP3 会让研究失效：列入 blocking_defaults，第一轮就问")
mq = next(q.as_dict() for q in _pend2 if q.key == "metric")
check(next(o for o in mq["options"] if o["recommended"])["value"] == "small_delay_p95",
      "比较调度器的主指标题推荐小包完成时延 P95")
check(tq is None or next(o for o in tq["options"] if o["recommended"])["value"] == "mixed",
      "关心小包时延时推荐大小包混合话务")
check(any("Earliest Drain First" in x for x in pr_pe["glossary_notes"]),
      "原话提到 EDF 时说明本平台 EDF 的含义")
d_umi, _, pr_umi, k_umi = _round1("帮我看下 UMi 场景的 SIR 分布，站距 200 m")
check(d_umi.params.get("scenario") == "UMi_NLOS" and "deployment" not in k_umi,
      "原话已给 UMi 就不再问部署类型")
txq = next((q for q in pr_umi["round_questions"] if q["key"] == "tx_power_dbm"), None)
check(txq is None or next(o for o in txq["options"] if o["recommended"])["value"] == 33.0,
      "UMi 场景下功率推荐 33 dBm")

# ---- 审核 20260926-codex1 的 11 个反例：检查回答之后的最终执行配置，而不只是首轮问法 ----
from superran import generate as _gen  # noqa: E402
from superran import hardware as _hw  # noqa: E402


def _final(text, **revise):
    d, p = pl.create_draft(text)
    if revise:
        d, p, ch = pl.revise_draft(d.draft_id, **revise)
    else:
        ch = []
    return d, p, pl.resolved_config(d)[0], ch


def _blocks(d, p):
    return [i["key"] for i in pl.draft_issues(d, p) if i["severity"] == "block"]


# F1 256T 请求必须生成 256T；不支持的阵型阻断，不静默沿用预设
d1, p1, c1, _ = _final("给我2个256T4R信道，不比算法")
_c1 = dict(c1)
_gen._ensure_bs_panel(_c1)
_hw.apply_array_defaults(_c1)
check(c1.get("num_bs_tx_ant") == 256 and _c1["bs_panel"] == [16, 8, 2]
      and _c1["_array_defaults_applied"] == "company_256t_1to6_1536ae" and c1.get("num_samples") == 2,
      "F1：256T4R 请求落到 256 端口、16×8×2、1 驱 6，数量 2")
d1b, p1b, _, _ = _final("给我2个128T4R信道，不比算法")
check("antenna_preset" in _blocks(d1b, p1b), "F1：不支持的 128T4R 阻断生成")
# F2 本小区负载与邻区负载分开；归属不明先问并阻断
d2, p2, _, _ = _final("对比本小区负载30%到90%时的边缘速率", accept_recommended=True)
check(d2.sweep["key"] == "target_prb_utilization" and "target_prb_utilization" not in pl.system_params(d2),
      "F2：扫本小区负载，且不被推荐值固定")
d2b, p2b, _, _ = _final("对比负载30%到90%时的边缘速率")
check(d2b.sweep["key"] == "load?" and "load_owner" in _blocks(d2b, p2b),
      "F2：负载归属不明时先问、阻断生成")
# F3 原话给的功率不被组合推荐覆盖，冲突要写明
d3, p3, c3, ch3 = _final("发射功率53 dBm，对比站距200/500/1000 m的下行干扰", accept_recommended=True)
check(c3.get("tx_power_dbm") == 53.0 and any("冲突" in x for x in ch3),
      "F3：53 dBm 保留，并写明与推荐的冲突")
# F4 选微站：站高 10 m 真正写进配置；下游站距档位随之重算
d4, p4, _, _ = _final("对比下站间距下的干扰变化情况", design={"deployment": "urban_micro"})
d4, p4, ch4 = pl.revise_draft(d4.draft_id, accept_recommended=True)
c4 = pl.resolved_config(d4)[0]
check(c4.get("tx_height_m") == 10.0 and c4.get("scenario") == "UMi_NLOS" and c4.get("tx_power_dbm") == 33.0
      and d4.sweep["values"] == [100.0, 150.0, 250.0],
      "F4：微站落实 10 m 站高、UMi、33 dBm，站距档位改为 100/150/250 m")
# F5 系统级实验必须有时间轴；预算冲突阻断
d5, p5, _, _ = _final("SRS 周期从 10 ms 改到 20 ms，看 120 km/h 用户的边缘速率", accept_recommended=True)
check(pl.snapshots_per_ue(d5.params) >= 8 and pl.system_params(d5).get("serving_cell") == "auto"
      and not _blocks(d5, p5), "F5：按推荐后每 UE ≥8 快照并指定服务小区")
d5b, p5b, _, _ = _final("SRS 周期从 10 ms 改到 20 ms，看 120 km/h 用户的边缘速率，给我 50 个样本",
                        accept_recommended=True)
check(d5b.params.get("num_samples") == 50 and "num_samples" in _blocks(d5b, p5b),
      "F5：用户限定的样本预算不被擅改，与时间轴冲突时阻断")
# F6 不支持的回答阻断，且未建模事实不从结论边界消失
d6, p6, _, _ = _final("评估一下密集城区下行 SINR 分布", design={"indoor_users": "need_o2i"})
pr6 = pl.build_proposal(d6, p6)
check(not pr6["ready_to_go"] and any("室内" in x for x in pr6["assumption_ledger"]["conclusion_limits"]),
      "F6：要求 O2I 时阻断，室内仍在结论边界")
d6b, p6b, _, _ = _final("PF 和 EDF 调度对小包时延的影响对比", design={"edf_meaning": "deadline_first"})
pr6b = pl.build_proposal(d6b, p6b)
check(not pr6b["ready_to_go"] and not pr6b["round_questions"],
      "F6：要截止时间调度时阻断，不再给排空优先的基线选项")
# F7 修改扫描取值替换执行计划；占位选项不算已答
d7, p7, _, _ = _final("对比下站间距200/500/1000 m下的干扰变化情况", design={"sweep_values": "100/200/300"})
check(d7.sweep == {"key": "isd_m", "values": [100.0, 200.0, 300.0]}, "F7：新扫描取值替换执行计划")
d7b, p7b, _, _ = _final("对比发射功率对SINR的影响", accept_recommended=True)
check(d7b.sweep and d7b.sweep["key"] == "tx_power_dbm" and len(d7b.sweep["values"]) >= 2,
      "F7：按推荐后扫描列表是具体数值")
# F8 敏感度变体必须不同于基准；零差值只在静态核对过时才算“不读”
check([lab for k, _, lab in iv._alternatives({"noise_figure_db": 9.0}, None) if k == "noise_figure_db"]
      == ["终端噪声系数 9 → 7 dB"], "F8：NF 已是 9 dB 时变体为 7 dB")
check(iv.classify_zero({"noise_figure_db": 7.0}, {"iot_dl_db": 0.0}) == {"inert": False, "no_change_observed": True},
      "F8：未经静态核对的零差值不判为仿真器不读")
# F9 / F10 数据交付
d9, p9, _, _ = _final("只要20个PDP，不比算法")
check(d9.form == "deliver" and d9.params.get("num_samples") == 20, "F9：只要 20 个 PDP 就生成 20 个")
d10, p10, _, _ = _final("给我20个64T4R CDL-C信道，不做算法对比")
check(d10.form == "deliver" and not pl.build_proposal(d10, p10)["round_questions"],
      "F10：“不做算法对比”识别为交付数据")
check(iv.read_brief("发射功率 -10 dBm").params.get("tx_power_dbm") == -10.0
      and iv.read_brief("看UMi场景的SIR").params.get("scenario") == "UMi_NLOS",
      "原话解析：负功率与紧贴中文的 UMi")

# ---- 审核第二轮（codex1 R2）：改口、分轮补答、单位换算、原话硬要求 ----
for _power_text in ("对比发射功率33和53 dBm下的SINR", "比较功率33 dBm/53 dBm的SINR"):
    _pd, _pp, _, _ = _final(_power_text, accept_recommended=True)
    check(_pd.sweep == {"key": "tx_power_dbm", "values": [33.0, 53.0]},
          "R4：原话功率扫描保留 33 与 53 dBm 两档")
for _bad_text in ("对比本小区负载30%到150%时的边缘速率", "对比站距0/500/1000m下的干扰"):
    _bd, _bp, _, _ = _final(_bad_text, accept_recommended=True)
    check("sweep_values" in _blocks(_bd, _bp), "R4：原话越界档位在按推荐后仍阻断")
    _bd, _bp, _ = pl.revise_draft(_bd.draft_id, design={"sweep_values": "0.3/0.9"
                                  if "负载" in _bad_text else "200/500/1000"})
    check("sweep_values" not in _blocks(_bd, _bp), "R4：修正档位后解除对应阻断")
_ad, _ap, _, _ = _final("对比64T和256T下的SINR", design={"sweep_values": "4T4R/64T4R"})
check(_ad.sweep["values"] == ["4T4R", "64T4R"], "R4：阵型档位按完整标签保存")
for _scenario in ("UMa_LOS", "UMa_NLOS", "UMi_LOS", "UMi_NLOS", "RMa_LOS", "RMa_NLOS"):
    _sd, _sp, _sc, _ = _final(f"给我2个4T4R {_scenario} 信道，不做算法对比")
    check(_sc["scenario"] == _scenario, f"R4：原话 {_scenario} 的 LOS/NLOS 限定进入执行配置")
_hd, _hp, _hc, _ = _final("帮我看下 UMi 场景的 SIR 分布，站距200m", accept_recommended=True)
check(_hc["tx_height_m"] == 10.0, "R4：原话 UMi 的推荐落实 10m 站高")
_hd, _, _ = pl.revise_draft(_hd.draft_id, overrides={"tx_height_m": 15.0, "scenario": "UMa_LOS"})
check(pl.resolved_config(_hd)[0]["tx_height_m"] == 15.0, "R4：场景切换保留显式站高")

def _rv(did, **kw):
    return srv.sr_revise(did, **kw)


_p = srv.sr_plan("对比带宽对SINR的影响")
check(_rv(_p["draft_id"], accept_recommended=True)["brief"]["sweep"]["values"] == [20e6, 40e6, 100e6],
      "R2-4：带宽档位保留 MHz 量纲（20/40/100 MHz → Hz）")
_p = srv.sr_plan("对比本小区负载30%到90%时的边缘速率")
check(_rv(_p["draft_id"], design={"sweep_values": "20%/80%"})["brief"]["sweep"]["values"] == [0.2, 0.8],
      "R2-4：百分比档位换算为 0.2/0.8")
_p = srv.sr_plan("对比站距下的干扰变化")
_rv(_p["draft_id"], design={"deployment": "urban_macro"})
_c = _rv(_p["draft_id"], design={"deployment": "urban_micro"}, accept_recommended=True)["resolved_config"]
check((_c["scenario"], _c["tx_height_m"], _c["tx_power_dbm"]) == ("UMi_NLOS", 10.0, 33.0),
      "R2-5：宏站改选微站后执行配置随之更新")
_p = srv.sr_plan("对比负载对边缘速率的影响")
_rv(_p["draft_id"], design={"load_owner": "target_prb_utilization"})
check(_rv(_p["draft_id"], design={"sweep_values": "0.3/0.9"})["brief"]["sweep"]["key"]
      == "target_prb_utilization", "R2-6：先答负载归属、后给档位，归属不丢")
_p = srv.sr_plan("CSI压缩，跟Type II比，64T4R")
check("effect_size" not in _rv(_p["draft_id"], accept_recommended=True)["answered_design"],
      "R2-9：按推荐跑不代答预期增益")
check(srv.sr_plan("不比算法，只想看密集城区下行SINR分布")["form"] == "characterize"
      and srv.sr_plan("对比站距200/500/1000 m下的干扰，不做算法对比")["form"] == "sweep_condition",
      "R2-8：否定只取消方法比较，保留刻画与扫描")
_p = srv.sr_plan("只要20个64T4R信道")
check(any(x["severity"] == "block"
          for x in _rv(_p["draft_id"], overrides={"antenna_preset": "128T4R"})["issues"]),
      "R2-3：改成不支持的阵型要阻断")
_p = srv.sr_plan("只要20个128T4R信道")
check(not any(x["severity"] == "block"
              for x in _rv(_p["draft_id"], overrides={"antenna_preset": "64T4R"})["issues"]),
      "R2-3：改回支持的阵型后解锁")
_p = srv.sr_plan("评估密集城区下行SINR分布，必须考虑室内穿透损耗")
check(any(x["severity"] == "block" for x in _rv(_p["draft_id"], accept_recommended=True)["issues"]),
      "R2-2：原话要求 O2I，按推荐跑也不能消掉阻断")
_p = srv.sr_plan("比较PF和EDF最早截止时间优先调度的小包时延")
check(any(x["severity"] == "block" for x in _rv(_p["draft_id"], accept_recommended=True)["issues"]),
      "R2-2：原话指定截止时间调度，按推荐跑也不能换成排空优先")
# R2-7：服务小区按实际撒点挑，中心站优先、至少 2 个 UE
check(srv._auto_serving_cell([0, 0, 3, 7, 7, 7], 3)[0] == 0,
      "R2-7：中心站扇区有 ≥2 个 UE 时选它")
check(srv._auto_serving_cell([1, 3, 7, 7, 7], 3)[0] == 7,
      "R2-7：中心站扇区都不足 2 个 UE 时退选 UE 最多的小区并说明")
check(srv._auto_serving_cell([0, 1, 2], 3)[0] is None, "R2-7：没有小区 ≥2 个 UE 时报错而不是硬选")
for _layout in ("linear", "custom"):
    check(srv._auto_serving_cell([0, 0, 9, 9, 9], 3, _layout)[0] is None,
          f"R4：{_layout} 拓扑自动选中心站必须拒绝并要求显式小区")

# “按推荐跑”：所有待问问题取推荐项；False 也是合法回答；预期只能由用户本人给
d_acc, _, _ = pl.revise_draft(d_srs.draft_id, accept_recommended=True)
pr_acc = pl.build_proposal(d_acc, dec.classify_intent(d_acc.intent))
check(pr_acc["system_params"].get("srs_period_adaptive") is False,
      "按推荐跑后 SRS 自适应确实被关掉（False 不被当成空值丢掉）")
check("expectation" not in d_acc.design and pr_acc["needs_user_content"] == ["expectation"],
      "预期不被推荐项代答，仍列为需要用户本人给出")

# 选项自带改动：选“街道微站”当场把场景与功率改掉，并记下是谁定的
d2, _, ch2 = pl.revise_draft(d_isd.draft_id, design={"deployment": "urban_micro"})
check(d2.params.get("scenario") == "UMi_NLOS" and d2.params.get("tx_power_dbm") == 33.0,
      "选“街道微站”后场景与功率随之改为 UMi / 33 dBm")
# R2-5：组合选项带出的值记为“选项带出”，不是用户独立锁定，改选部署时可以更新
check(d2.provenance.get("tx_power_dbm") == iv.SOURCE_DERIVED, "改动来源记为由部署选项带出")
pr2 = pl.build_proposal(d2, p_isd)
check(not any("发射功率" in x for x in pr2["assumption_ledger"]["silently_assumed"]),
      "确认后发射功率不再是沉默假设")

# 预期对照：方向对、量级够的假设才列为候选；当前已经是 33 dBm 时不再拿功率解释
g46 = fx.explain_gap("iot_dl_db", 10.0, 28.5, cfg={"tx_power_dbm": 46.0})
g33 = fx.explain_gap("iot_dl_db", 10.0, 28.5, cfg={"tx_power_dbm": 33.0})
check(any("功率" in c["factor"] for c in g46["candidates"])
      and not any("功率" in c["factor"] for c in g33["candidates"]),
      "预期偏差解释参考当前配置")
check(fx.explain_gap("iot_dl_db", 27.0, 28.5)["candidates"] == [], "差距在阈值内视为一致")

# 体验因子表里写的系统级默认值必须与 sr_system_sim 的签名一致
_sig = inspect.signature(srv.sr_system_sim).parameters
for f in fx.DL_EXPERIENCE:
    if f.layer == "system" and f.config_key in _sig:
        default = _sig[f.config_key].default
        check(str(default).lower() in f.platform_default.lower()
              or str(default) in f.platform_default,
              f"{f.label} 的平台默认（{f.platform_default}）与 sr_system_sim 默认 {default!r} 一致")

# ---------------------------------------------------------------------------
print("\n" + "=" * 68)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f_ in FAILED:
        print("  - " + f_)
    sys.exit(1)
print("全部通过。")
