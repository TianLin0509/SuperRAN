"""端到端冒烟测试：能力探测 → 提案 → 生成 → 取货 → 真跑取货代码。

直接运行：python tests/test_e2e.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superwireless import decisions as dec  # noqa: E402
from superwireless import deliver as dlv  # noqa: E402
from superwireless import generate as gen  # noqa: E402
from superwireless import plan as pl  # noqa: E402
from superwireless import channelhub as ch  # noqa: E402

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
print(f"  可直接生成   {proposal['ready_to_go']}")
print(f"  问题数       {len(proposal['questions'])}")
print(f"  首个问题     {proposal['questions'][0]['question']}")
print(f"    why       {proposal['questions'][0]['why'][:70]}…")
check(proposal["ready_to_go"], "提案可直接生成（用户不表态也能走）")
check(all("why" in q and q["why"] for q in proposal["questions"]), "每个问题都带 why")
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
print(f"  信道模型     {summary['channel_model']}  含角度={summary['is_cdl']}")
print(f"  SINR 分布    {summary['sinr_dB']}")
print(f"  路损分布     {summary.get('pathloss_dB')}")
print(f"  视距比例     {summary.get('los_ratio')}")
check(summary["num_samples"] == 4, "生成了 4 个样本")
check(summary["shape"]["BS_ant"] == 4, "天线维度正确")
check("pathloss_dB" in summary, "路损等物理量已接出（无需改 ChannelHub）")

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
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    print((proc.stdout or "").strip()[:1400])
    if proc.returncode != 0:
        print("STDERR:\n" + (proc.stderr or "")[-2500:])
    check(proc.returncode == 0, "取货代码可直接运行")

# ---------------------------------------------------------------------------
sect("9  测量量物理正确性抽查")
from superwireless import load  # noqa: E402

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
check(True, "RSRP 可取")

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
print("\n" + "=" * 68)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f_ in FAILED:
        print("  - " + f_)
    sys.exit(1)
print("全部通过。")
