"""射线追踪与深化决策层测试。

射线追踪比统计信道慢一个量级，所以这里只跑极小配置。
直接运行：python tests/test_raytracing.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superwireless import channelhub as ch  # noqa: E402
from superwireless import decisions as dec  # noqa: E402
from superwireless import generate as gen  # noqa: E402
from superwireless import plan as pl  # noqa: E402
from superwireless import scenes as sc  # noqa: E402
from superwireless import load  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(t: str) -> None:
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


# ---------------------------------------------------------------------------
sect("1  射线追踪引擎可用性")
caps = {c.name: c for c in ch.probe_capabilities()}
rt = caps["sionna_rt"]
print(f"  sionna_rt: {'可用' if rt.available else '不可用'}  {rt.detail}")
check(rt.available, "sionna_rt 报告可用")

# ---------------------------------------------------------------------------
sect("2  场景清单")
all_scenes = sc.list_scenes()
for s in all_scenes:
    tag = "内置" if s.builtin else "真实OSM"
    print(f"  {s.scene_id:<22} {tag:<8} {s.display_name[:26]:<28} presets={list(s.presets)}")
check(len(all_scenes) >= 10, f"至少 10 个场景（实际 {len(all_scenes)}）")
check(sum(1 for s in all_scenes if s.builtin) == 4, "4 个 Sionna 内置场景")
check(sum(1 for s in all_scenes if s.needs_preparation) >= 6, "6 个中国城市场景带本地资产")

# ---------------------------------------------------------------------------
sect("3  场景资产准备（修 PLY 头，不改 ChannelHub）")
t0 = time.perf_counter()
prep = sc.prepare_scene("shenzhen_futian", force=True)
print(f"  准备耗时 {time.perf_counter()-t0:.1f}s")
print(f"  PLY 总数 {prep['ply_total']}，修复 {prep['ply_fixed']} 个")
print(f"  缓存位置 {prep['osm_path']}")
check(prep["prepared"], "场景准备完成")
check(prep["ply_fixed"] >= 1, "确实修复了带 obj_info 的 PLY")

orig = sc.scenes_dir() / "shenzhen_futian" / "mesh" / "ground.ply"
if orig.is_file():
    check(b"obj_info" in orig.read_bytes()[:400], "ChannelHub 原文件未被修改")

cached = sc.prepare_scene("shenzhen_futian")
check(cached.get("cached") is True, "第二次调用命中缓存")

# ---------------------------------------------------------------------------
sect("4  内置场景射线追踪（慕尼黑）")
d, p = pl.create_draft(
    "在慕尼黑真实地图上验证覆盖",
    overrides={"num_ues": 1, "num_samples": 1, "bs_antenna": "4T4R", "bandwidth_hz": 20000000.0},
)
print(f"  自动选中预设: {d.preset}")
check(d.preset.startswith("rt_"), "意图含城市名时自动走射线追踪预设")

cfg, own = pl.resolved_config(d)
cfg.pop("num_samples", None)
print(f"  scenario={cfg.get('scenario')}  device={cfg.get('device')}")
t0 = time.perf_counter()
s1 = gen.generate(cfg, num_samples=1)
dt = time.perf_counter() - t0
print(f"  生成 {dt:.1f}s  形状 {s1['shape']}")
print(f"  SINR {s1['sinr_dB']['median']} dB  路损 {s1.get('pathloss_dB', {}).get('median')} dB")
check(s1["num_samples"] == 1, "慕尼黑场景生成成功")

ds1 = load(s1["dataset_id"])
mode = ds1.summary.get("sample_meta", {}).get("channel_generation_mode")
print(f"  channel_generation_mode = {mode}")
check(mode == "sionna_rt", "确认走的是真射线追踪，不是 TDL 回退")

# ---------------------------------------------------------------------------
sect("5  真实城市射线追踪（深圳福田）")
d2, p2 = pl.create_draft(
    "深圳福田密集城区覆盖分析",
    overrides={"num_ues": 1, "num_samples": 1, "bs_antenna": "4T4R", "bandwidth_hz": 20000000.0},
)
print(f"  预设 {d2.preset}")
cfg2, own2 = pl.resolved_config(d2)
cfg2.pop("num_samples", None)
print(f"  scenario={cfg2.get('scenario')}  站点={cfg2.get('num_sites')}x{cfg2.get('sectors_per_site')}")
print(f"  osm_path={str(cfg2.get('osm_path'))[-46:]}")
check(cfg2.get("scenario") == "custom_osm", "真实城市走 custom_osm")
check("artifacts" in str(cfg2.get("osm_path", "")), "osm_path 指向准备好的缓存副本")

t0 = time.perf_counter()
s2 = gen.generate(cfg2, num_samples=1)
print(f"  生成 {time.perf_counter()-t0:.1f}s  形状 {s2['shape']}")
print(f"  SINR {s2['sinr_dB']['median']} dB  视距比例 {s2.get('los_ratio')}")
ds2 = load(s2["dataset_id"])
mode2 = ds2.summary.get("sample_meta", {}).get("channel_generation_mode")
print(f"  channel_generation_mode = {mode2}  小区数 {ds2.summary['sample_meta'].get('num_cells')}")
check(mode2 == "sionna_rt", "深圳福田走真射线追踪")
check(bool(ds2.ssb), "多小区 SSB 测量可用")

print("\n  正确性护栏：射线追踪数据不得套用 CDL 剖面的假角度")
check(ds2.is_ray_traced, "数据集自报为射线追踪")
try:
    ds2.paths()
    check(False, "paths() 在射线追踪数据上应当报错")
except NotImplementedError as e:
    print(f"    已拦截：{str(e).splitlines()[0][:70]}…")
    check(True, "paths() 在射线追踪数据上正确报错")

from superwireless import deliver as dlv  # noqa: E402

res_rt = dlv.build_code(s2["dataset_id"], "信道 + 角度")
print(f"    取货提示 {len(res_rt['notes'])} 条")
check(any("射线追踪" in n for n in res_rt["notes"]), "取货代码给出射线追踪说明")

# 常规量在射线追踪数据上照常可用
p_rt = ds2.pdp(0)
srs_rt = ds2.srs(0)
print(f"    PDP RMS 时延扩展 {p_rt.rms_delay_spread_s*1e9:.1f} ns | 主导秩 {srs_rt.dominant_rank}")
check(p_rt.rms_delay_spread_s > 0, "射线追踪数据的 PDP 仍可用")

# ---------------------------------------------------------------------------
sect("6  实验设计层（superpowers 式头脑风暴）")
d3, p3 = pl.create_draft("验证一个 CSI 压缩的想法")
prop = pl.build_proposal(d3, p3)
print(f"  任务 {prop['task_label']}")
print("\n  实验设计问题（先问这层）：")
for q in prop["design_questions"]:
    mark = "可选" if q["optional"] else "建议问"
    print(f"    [{mark}] {q['question']}")
    print(f"           why: {q['why'][:66]}…")
    print(f"           例:  {' / '.join(q['examples'][:3])}")
check(len(prop["design_questions"]) >= 2, "提供了实验设计层问题")
check(all(q["why"] and q["examples"] for q in prop["design_questions"]), "设计问题都带 why 和示例")

print("\n  建议的对比组：")
for s in prop["suggested_sweeps"]:
    print(f"    · {s['label']}: {s['key']} = {s['values']}")
    print(f"      why: {s['why'][:66]}…")
check(len(prop["suggested_sweeps"]) >= 1, "给出对比组建议")

print("\n  常见陷阱：")
for pf in prop["pitfalls"]:
    print(f"    · {pf[:76]}")
check(len(prop["pitfalls"]) >= 2, "给出常见陷阱提示")

# ---------------------------------------------------------------------------
sect("7  实验设计写进计划书")
d4, p4, changes = pl.revise_draft(
    d3.draft_id,
    design={"baseline": "3GPP Type II 码本", "metric": "NMSE 与频谱效率损失"},
)
md = pl.render_plan_markdown(d4, p4, ["channel", "pmi"])
print(md[: md.find("## 关键选择")])
check("## 实验设计" in md, "计划书含实验设计章节")
check("Type II" in md, "基线写进了计划书")

# ---------------------------------------------------------------------------
sect("8  新增任务类型与拦截")
cases = [
    ("做信道预测，用 LSTM 外推未来时隙", "channel_prediction"),
    ("CQI 上报和 MCS 选择的自适应算法", "link_adaptation"),
    ("信道表征学习，做对比学习预训练", "channel_charting"),
]
for text, expect in cases:
    prof = dec.classify_intent(text)
    ok = prof.task == expect
    print(f"  {'OK ' if ok else 'ERR'} {text[:30]:<32} → {prof.task}")
    check(ok, f"新任务类型识别：{expect}")

prof_pred = dec.classify_intent("信道预测")
issues = dec.check_guards(prof_pred, {"num_slots_per_sample": 1, "ue_speed_kmh": 3.0})
for i in issues:
    print(f"  [{i['severity']}] {i['key']}: {i['message'][:62]}…")
check(any(i["severity"] == "block" for i in issues), "信道预测 + 单时隙被拦截")

# ---------------------------------------------------------------------------
print("\n" + "=" * 68)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("射线追踪与决策层全部通过。")
