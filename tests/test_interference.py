"""干扰强度量化的测试：IoT 推导、分级、测量域、场景预设。

直接运行：python tests/test_interference.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Windows 中文控制台是 GBK，统一兜底（见 test_gates.py 同样的处理）。
sys.stdout.reconfigure(errors="replace")

from superwireless import channelhub as ch  # noqa: E402
from superwireless import generate as gen  # noqa: E402
from superwireless import interference as itf  # noqa: E402
from superwireless import load  # noqa: E402
from superwireless import plan as pl  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(t: str) -> None:
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


# ---------------------------------------------------------------------------
sect("1  IoT 推导的解析正确性")

# 直接构造 S/I/N，三个量都算得出来，检查 iot_db 与解析值逐位相符。
cases = [
    (1.0, 10.0, 1.0), (1.0, 0.01, 1.0), (1.0, 1.0, 1.0),
    (10.0, 3.0, 0.5), (100.0, 0.001, 1.0), (1.0, 1000.0, 1.0),
]
worst = 0.0
for S, I_pow, N in cases:
    sinr = 10 * math.log10(S / (I_pow + N))
    sir = 10 * math.log10(S / I_pow)
    want = 10 * math.log10((I_pow + N) / N)
    got = float(itf.iot_db(sinr, sir))
    worst = max(worst, abs(want - got))
print(f"  {len(cases)} 组 (S,I,N) 的最大偏差 {worst:.2e} dB")
check(worst < 1e-9, "IoT = SIR/(SIR-SINR) 与 (I+N)/N 解析一致")

# 这条是本模块存在的理由：**不能**用 snr - sinr。
# 构造一个 ChannelHub 口径的例子：snr 不含阵列增益且多减了 10log10(RB)。
RB, N_ant = 273, 64
S, I_pow, N = 1.0, 10.0, 1.0
sinr_ch = 10 * math.log10(S * N_ant / (I_pow * N_ant + N))   # 几何 SINR：含阵列增益
snr_ch = 10 * math.log10(S / N) - 10 * math.log10(RB)     # ChannelHub 的 snr_dB
naive = snr_ch - sinr_ch
true_iot = 10 * math.log10((I_pow * N_ant + N) / N)
print(f"  naive(snr-sinr) = {naive:.2f} dB，真值 = {true_iot:.2f} dB，"
      f"差 {abs(naive - true_iot):.1f} dB")
check(abs(naive - true_iot) > 20.0,
      "snr_dB - sinr_dB 与真 IoT 相差 20 dB 以上（所以模块里禁用这个式子）")

# 向量化与广播
v = itf.iot_db(np.array([-5.0, 0.0, 5.0]), np.array([0.0, 5.0, 10.0]))
check(v.shape == (3,) and np.all(np.isfinite(v)), "支持数组输入")
check(np.isnan(itf.iot_db(np.nan, 10.0)), "输入非有限值时回 nan 而不是瞎算")
check(math.isinf(float(itf.iot_db(10.0, 10.0))),
      "SIR == SINR（噪声为零）时回 inf，不是回 0")
check(math.isinf(float(itf.iot_db(10.0, 5.0))),
      "SIR < SINR（物理不可能）时回 inf 而不是负数")

# ---------------------------------------------------------------------------
sect("2  负载换算与分级")

for load_v, want_db in ((0.5, 3.01), (0.75, 6.02), (0.9, 10.0), (0.99, 20.0)):
    got = itf.iot_from_load(load_v)
    check(abs(got - want_db) < 0.02, f"负载 {load_v} -> IoT {want_db} dB（得 {got:.2f}）")

rt = float(itf.load_factor_from_iot(itf.iot_from_load(0.87)))
check(abs(rt - 0.87) < 1e-9, "负载 <-> IoT 往返一致")

check(itf.classify_iot(22.0)["high_interference"] is True, "22 dB 判为高干扰")
check(itf.classify_iot(19.9)["high_interference"] is False, "19.9 dB 不判为高干扰")
check(itf.classify_iot(20.0)["high_interference"] is True, "门限 20 dB 取闭区间")
check(itf.classify_iot(float("nan"))["band"] == "未定义", "非有限值不硬套等级")
bands = [itf.classify_iot(x)["band"] for x in (1, 5, 10, 17, 25)]
print("  1/5/10/17/25 dB -> " + " ".join(bands))
check(len(set(bands)) == 5, "五个档位互不重叠")

# ---------------------------------------------------------------------------
sect("3  IoT 统计：不可信样本必须单独计数")

# 哨兵值：没有干扰源时 ChannelHub 填 sir_dB = 49.9
st = itf.iot_stats(np.array([20.0, 21.0, 22.0]), np.array([49.9, 49.9, 49.9]))
check(st.n_no_interferer == 3 and st.n_valid == 0,
      "sir=49.9 哨兵全部计入 n_no_interferer，不进 IoT 统计")

# 贴边：SINR 顶到 +50 dB
st = itf.iot_stats(np.array([50.0, 10.0]), np.array([55.0, 20.0]))
check(st.n_clamped == 1 and st.n_valid == 1, "贴 ±50 dB 边界的样本单独计数")

# 正常样本
sinr = np.array([0.0, 1.0, 2.0, 3.0])
sir = sinr + 0.5
st = itf.iot_stats(sinr, sir)
check(st.n_valid == 4 and st.median_db > 0, "正常样本全部有效")
check(sum(st.bands.values()) == st.n_valid, "分档计数之和等于有效样本数")

nan_st = itf.iot_stats(np.array([np.nan, np.nan]), np.array([np.nan, np.nan]))
check(nan_st.n_valid == 0 and nan_st.as_dict()["median_db"] is None,
      "全 nan 时不抛异常、中位数回 None")

# ---------------------------------------------------------------------------
sect("4  测量域：估计 NMSE 下限")

# 纯干扰受限：NMSE 底 = 1/SIR
floor = float(itf.estimation_nmse_floor_db(20.0))
check(abs(floor + 20.0) < 1e-9, "SIR 20 dB -> NMSE 底 -20 dB")
# 干扰 + 噪声：两项功率相加
floor2 = float(itf.estimation_nmse_floor_db(20.0, 20.0))
check(abs(floor2 - (-20.0 + 10 * math.log10(2))) < 1e-9,
      "同量级噪声让 NMSE 底抬高 3 dB")
check(float(itf.estimation_nmse_floor_db(5.0)) > float(itf.estimation_nmse_floor_db(25.0)),
      "SIR 越低 NMSE 底越高")

m = itf.classify_measurement_sir(3.0)
check(m["band"] == "测量严重受损", "3 dB 测量 SIR 判为严重受损")
check(itf.classify_measurement_sir(-2.0)["band"] == "测量已失效",
      "负 SIR 判为测量已失效")

# ---------------------------------------------------------------------------
sect("5  几何采集钩子")

ok = itf.install_geometry_capture()
check(ok, "钩子挂载成功")
check(itf.install_geometry_capture(), "重复挂载幂等")


class _FakeSample:
    def __init__(self, sinr, sir):
        self.sinr_dB = sinr
        self.sir_dB = sir


# 暂存为空时必须回 nan，不能回上一次的值
itf._capture.clear()
check(math.isnan(itf.take_ul_geometry_sir(_FakeSample(1.0, 2.0))),
      "暂存为空时回 nan")

# 自检：暂存的下行量与 sample 对得上才给上行值
itf._capture.update({"dl_sinr_avg": 12.0, "sir_dl_db": 18.0,
                     "ul_sinr_avg": 5.0, "sir_ul_db": 9.0})
check(abs(itf.take_ul_geometry_sir(_FakeSample(12.0, 18.0)) - 9.0) < 1e-9,
      "下行量对得上时给出上行 SIR")

itf._capture.update({"dl_sinr_avg": 12.0, "sir_dl_db": 18.0,
                     "ul_sinr_avg": 5.0, "sir_ul_db": 9.0})
check(math.isnan(itf.take_ul_geometry_sir(_FakeSample(3.0, 18.0))),
      "下行量对不上时回 nan —— 宁可没有，也不给错的上行 IoT")

# 取过一次就清空，防止串到下一个样本
itf._capture.update({"dl_sinr_avg": 1.0, "sir_dl_db": 2.0,
                     "ul_sinr_avg": 3.0, "sir_ul_db": 4.0})
itf.take_ul_geometry_sir(_FakeSample(1.0, 2.0))
check(math.isnan(itf.take_ul_geometry_sir(_FakeSample(1.0, 2.0))),
      "取走后暂存清空，同一份不会被第二个样本重复领走")

# ---------------------------------------------------------------------------
sect("6  场景预设的完整性")

presets = pl.load_presets()
print(f"  共 {len(presets)} 个预设")
check(len(presets) >= 20, "预设数量 >= 20")

groups = pl.preset_groups()
print("  分组：" + "  ".join(f"{g}({len(v)})" for g, v in groups.items()))
check("干扰场景" in groups and "测量干扰" in groups and "大站间距" in groups,
      "干扰 / 测量干扰 / 大站间距 三组都在")

for name, body in presets.items():
    check(bool(body.get("group")), f"{name} 有 group")
    check(bool(body.get("label")) and bool(body.get("summary")), f"{name} 有 label/summary")

# 测量域的量只在 paired 模式下产生，声称测量干扰的场景必须 link=BOTH
for name in groups.get("测量干扰", []):
    link = presets[name]["config"].get("link")
    check(link == "BOTH", f"{name} 的 link 是 BOTH（否则拿不到测量域 SIR）")

# 大站间距场景必须带 caveat：ChannelHub 没有 RMa 路损公式
for name in groups.get("大站间距", []):
    check("RMa" in (presets[name].get("caveat") or ""),
          f"{name} 注明了用的不是 RMa 路损公式")

summaries = pl.preset_summaries()
check(all("group" in s for s in summaries), "preset_summaries 带 group")
check(all(("expect" not in s) or s["expect"] for s in summaries),
      "expect 字段要么没有、要么非空（不放空壳）")

# ---------------------------------------------------------------------------
sect("7  设计提示")

hint = itf.design_hint(20.0)
check(hint["band"] == "高干扰", "目标 20 dB 归入高干扰档")
check(abs(hint["equivalent_load"] - 0.99) < 1e-3, "20 dB 对应等效负载 0.99")
check(len(hint["levers"]) >= 5, "至少列出 5 个旋钮")
check(all({"key", "direction", "why", "note"} <= set(x) for x in hint["levers"]),
      "每个旋钮都说清方向、原因与注意事项")
check("复核" in hint["verification"], "明确要求生成后复核，不拿估算下结论")

# ---------------------------------------------------------------------------
sect("8  端到端：多小区数据集的 IoT 与报告")

ch.warmup()
cfg = dict(pl.load_presets()["multicell_7site"]["config"])
cfg["num_rb"] = 24          # 只减计算量；几何 IoT 与 num_rb 无关
cfg["num_ues"] = 7
cfg["measurements"] = {"ssb_rsrp": False}
summ = gen.generate(cfg, num_samples=7, workers=1)
ds = load(summ["dataset_id"])

check(summ.get("interference_modeled") is True, "多小区场景的干扰确实进了 SINR")
iot_block = summ.get("iot")
check(isinstance(iot_block, dict) and "dl" in iot_block, "summary 里有 iot 块")
dl = iot_block["dl"]
print(f"  下行 IoT 中位数 {dl['median_db']} dB，{dl['classification']['band']}，"
      f"等效负载 {dl['classification']['equivalent_load']}")
check(dl["n_valid"] > 0, "有有效 IoT 样本")
check(dl["median_db"] is not None and dl["median_db"] > 0,
      "多小区场景的 IoT 大于 0 dB（干扰确实存在）")

# 逐样本复核：报告里的 IoT 必须能由落盘的 sinr/sir 重算出来
sinr = ds.scalar("sinr_dB")
sir = ds.scalar("sir_dB")
recomputed = itf.iot_stats(sinr, sir)
check(abs(recomputed.median_db - dl["median_db"]) < 0.01,
      "summary 的 IoT 可由落盘标量原样重算（不是另存的快照）")

rep = itf.interference_report(summ["dataset_id"])
check(rep["traffic_domain"]["dl"]["iot"]["n_valid"] > 0, "报告里有业务域 IoT")
check(rep["iot_exact"] is True, "num_slots_per_sample=1 时 IoT 标为精确")
check(isinstance(rep["notes"], list), "报告带 notes")

# 新增的测量域列即使在 DL-only 场景下也要存在（值为 nan），
# 否则并行合并时两块的字段集会不一致。
for name in ("ul_sir_dB", "dl_sir_dB", "num_interfering_ues", "ul_sir_geo_dB"):
    try:
        arr = ds.scalar(name)
        check(arr.shape[0] == summ["num_samples"], f"{name} 每样本一个值")
    except KeyError:
        check(False, f"{name} 落盘了")

# ---------------------------------------------------------------------------
sect("9  端到端：paired 模式下的测量域 SIR")

cfg2 = dict(pl.load_presets()["srs_congested"]["config"])
cfg2["num_rb"] = 24
cfg2["num_ues"] = 7
cfg2["num_interfering_ues"] = 12
cfg2["measurements"] = {"ssb_rsrp": False}
summ2 = gen.generate(cfg2, num_samples=7, workers=1)
ds2 = load(summ2["dataset_id"])
rep2 = itf.interference_report(summ2["dataset_id"])

md = rep2.get("measurement_domain", {})
print("  测量域：" + ", ".join(md) if md else "  测量域：空")
check("ul_srs" in md, "paired 模式下拿到了 SRS 测量域 SIR")
if "ul_srs" in md:
    srs = md["ul_srs"]
    print(f"  SRS 测量 SIR 中位数 {srs['sir_dB']['median']} dB -> "
          f"{srs['classification']['band']}；NMSE 底 {srs['nmse_floor_db']} dB")
    check(srs["sir_dB"]["n"] > 0, "SRS 测量 SIR 有有效样本")
    check(srs["nmse_floor_db"] is not None, "给出了估计 NMSE 下限")

# 业务域与测量域是两个独立的量，不该恰好相等
if "ul_srs" in md and rep2["traffic_domain"].get("dl"):
    a = md["ul_srs"]["sir_dB"]["median"]
    b = rep2["traffic_domain"]["dl"]["sir_dB"]["median"]
    check(a is not None and b is not None and abs(a - b) > 0.01,
          f"测量域 SIR({a}) 与业务域 SIR({b}) 是不同的量")

# ---------------------------------------------------------------------------
sect("10  文档里的数字必须和代码对得上")

# "19 项体检"这句话在 README / SKILL.md / 两份 HTML 里写了八处，而 full_report
# 从第一版（f44b46a）起就只有 16 项 —— 数字是凭印象写的，从没对过账。
# 这一节让文档里的计数与代码绑死，省得下次再漂。
import re  # noqa: E402

from superwireless import server as _srv  # noqa: E402
from superwireless import validate as _val  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
n_checks = len(_val.full_report(ds).checks)
print(f"  full_report 实有 {n_checks} 项检查")

_pat = re.compile(r"(\d+)\s*项(?:体检|可信度检查|检查)")
for name in ("README.md", "skills/channel-sim/SKILL.md",
             "CAPABILITIES.html", "SETUP.html"):
    path = ROOT / name
    if not path.is_file():
        continue
    claims = {int(m) for m in _pat.findall(path.read_text(encoding="utf-8"))}
    check(all(c == n_checks for c in claims),
          f"{name} 声称的体检项数都等于 {n_checks}（文中出现 {sorted(claims)}）")

n_tools = len([n for n in vars(_srv) if n.startswith("sw_")])
_m = re.search(r"MCP 工具（(\d+) 个）", (ROOT / "README.md").read_text(encoding="utf-8"))
print(f"  server 实有 {n_tools} 个 sw_ 工具，README 写 {_m.group(1) if _m else '未写'}")
check(bool(_m) and int(_m.group(1)) == n_tools, f"README 声称的 MCP 工具数等于 {n_tools}")

# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
if FAILED:
    print(f"FAILED {len(FAILED)} 项：")
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("干扰量化、场景预设全部通过。")
