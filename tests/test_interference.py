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

# 高铁场景必须走 linear 拓扑 + 移动模型，否则 train_* 参数根本不生效
for name in groups.get("高铁", []):
    c = presets[name]["config"]
    check(c.get("topology_layout") == "linear" and
          c.get("mobility_mode") in ("linear", "track"),
          f"{name} 是 linear 拓扑 + 移动模型（否则进不了高铁模式）")
    check(float(c.get("train_penetration_loss_db", 0)) > 0,
          f"{name} 设了车体穿透损耗")

# expect 里的实测值必须自洽：iot_dl_db 与它标注的等级对得上
for name, body in presets.items():
    e = body.get("expect") or {}
    if e.get("iot_dl_db") is None:
        continue
    want = itf.classify_iot(float(e["iot_dl_db"]))["band"]
    check(want == (e.get("iot_dl_band") or want),
          f"{name} 的 expect.iot_dl_db 与标注等级一致")

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
sect("8.5  探测模式：几何量必须与全量逐位相同")

from superwireless import scenario as sc  # noqa: E402

# 探测模式压 num_rb 和 num_ofdm_symbols 换速度，前提是几何量一个不差。
# **这一节比对的是实际发货的那组参数**，不是外推——num_ofdm_symbols 在 1 处
# 有一道悬崖（实测 sir_dB 偏 16.1 dB），所以只能逐个验证、不能"2 行那 4 也行"。
probe_cfg = dict(pl.load_presets()["multicell_7site"]["config"])
probe_cfg["num_ues"] = 7
probe_cfg["seed"] = 4242
# **参照组也必须带 bs_panel。** 缺 panel 时 ChannelHub 建不出 DFT 码本，
# 几何 SINR 整条路径被跳过、sinr_dB 退化成含 -10log10(RB) 的 snr_dB，
# 于是压 num_rb 会看到 10.56 dB 的"偏差"——那是配置缺陷，不是探测模式的问题。
# probe_config 现在自己补 panel，这里对照组也补，两边才可比。
gen._ensure_bs_panel(probe_cfg)


def _geom(**over):
    cfg = dict(probe_cfg, **over, num_samples=7)
    cfg.pop("source", None)
    itf.install_geometry_capture()
    out = []
    n = 0
    for smp in ch.iter_samples("internal_sim", cfg):
        mm = smp.meta if isinstance(smp.meta, dict) else {}
        out.append((
            float(smp.sinr_dB), float(smp.sir_dB or np.nan),
            float(mm.get("pathloss_dB", np.nan)),
            float(mm.get("distance_3d_m", np.nan)),
            float(mm.get("doppler_hz", np.nan)),
            itf.take_ul_geometry_sir(smp),
        ))
        n += 1
        if n >= 7:
            break
    return np.asarray(out)


ref_geom = _geom()
shipped, _rb, _rbf = sc.probe_config(dict(probe_cfg))
cut_geom = _geom(num_rb=shipped["num_rb"],
                 num_ofdm_symbols=shipped["num_ofdm_symbols"])
both = np.isfinite(ref_geom) & np.isfinite(cut_geom)
worst_geom = float(np.max(np.abs(ref_geom[both] - cut_geom[both])))
print(f"  发货参数 num_rb={shipped['num_rb']} "
      f"num_ofdm_symbols={shipped['num_ofdm_symbols']}，"
      f"几何量最大偏差 {worst_geom:.3e}")
check(worst_geom == 0.0, "探测模式的几何量与全量逐位相同（不是近似）")

# 缺 bs_panel 时探测会失真——probe_config 必须自己把它补上
_no_panel = dict(pl.load_presets()["multicell_7site"]["config"])
_no_panel.pop("bs_panel", None)
check("bs_panel" not in _no_panel, "预设本身不带 bs_panel（所以补齐这步不能省）")
check("bs_panel" in sc.probe_config(_no_panel)[0],
      "probe_config 自动补 bs_panel（否则 sinr_dB 会退化成 RB 相关的 snr_dB）")

# 悬崖回归：符号数降到 1 会让几何量失真，PROBE_NUM_SYM 绝不能滑到这里
cliff_geom = _geom(num_rb=shipped["num_rb"], num_ofdm_symbols=1)
both_c = np.isfinite(ref_geom) & np.isfinite(cliff_geom)
worst_cliff = float(np.max(np.abs(ref_geom[both_c] - cliff_geom[both_c])))
print(f"  num_ofdm_symbols=1 时几何量最大偏差 {worst_cliff:.2f} dB")
check(worst_cliff > 1.0,
      "num_ofdm_symbols=1 确实会破坏几何量（所以 PROBE_NUM_SYM 不能取 1）")
check(sc.PROBE_NUM_SYM > sc.PROBE_NUM_SYM_CLIFF,
      "PROBE_NUM_SYM 在悬崖之上")

# 移动场景每个 UE 至少要 2 个样本，否则多普勒恒为 0
_hst = sc.probe(dict(pl.load_presets()["hst_350kmh"]["config"]), num_samples=21)
print(f"  hst 探测 21 样本 -> 实跑 {_hst['num_samples']} 个"
      f"（每 UE {_hst['samples_per_ue']} 个），"
      f"多普勒中位 {_hst['geometry']['doppler_hz']['median']} Hz")
check(_hst["samples_per_ue"] >= 2, "移动场景自动把样本数补到每 UE >= 2")
check((_hst["geometry"]["doppler_hz"]["median"] or 0) > 100,
      "补够之后多普勒不再是 0（350 km/h @ 2.6 GHz 应有几百 Hz）")
check("num_samples_note" in _hst, "补样本这件事写进了报告，不是静默发生")

_static = sc.probe(dict(probe_cfg), num_samples=21)
check("num_samples_note" not in _static, "静止场景不做补样本（不白花时间）")

# 探测模式不支持射线追踪，必须直说而不是给一份假的探测报告
try:
    sc.probe({"source": "sionna_rt", "scene": "munich"}, num_samples=1)
    check(False, "射线追踪配置应当被拒绝")
except ValueError as exc:
    check("internal_sim" in str(exc), "射线追踪配置被明确拒绝并说明原因")

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
sect("9.5  本地默认硬件：64T 1驱3 / 192 阵子 / 0.67λ")

from superwireless import hardware as hw  # noqa: E402

check(hw.COMPANY_RF_PANEL == [8, 4, 2], "RF 面板是 8H x 4V x 2pol")
check(hw.COMPANY_NUM_PORTS == 64, "RF 端口 64")
check(hw.COMPANY_ELEMENTS_PER_PORT == 3, "1 驱 3")
check(hw.COMPANY_NUM_ELEMENTS == 192, "物理阵子 192")
check(hw.COMPANY_H_SPACING_LAMBDA == 0.5, "水平间距 0.5λ")
# 这一条是实测纠正过的硬件值，写错会让全盘产物失真（见记忆 reconfig_mimo_sim）
check(hw.COMPANY_V_SPACING_LAMBDA == 0.67, "垂直间距 0.67λ（**不是 0.5**）")
check(abs(hw.COMPANY_ELEMENTS_PER_PORT * hw.COMPANY_V_SPACING_LAMBDA - 2.01) < 1e-9,
      "RF 端口垂直相位中心间距 2.01λ")
check(hw.COMPANY_CARRIER_HZ == 2.6e9, "载波 2.6 GHz (n41)")
check(hw.COMPANY_SCS_HZ == 30000, "子载波间隔 30 kHz")
check(hw.COMPANY_NUM_RB == 272 == hw.COMPANY_NUM_RBG * hw.COMPANY_RB_PER_RBG,
      "272 RB = 17 RBG x 16 RB")
check(hw.NR_TABLE_NUM_RB_100M_30K == 273,
      "同时记住 38.104 标准表是 273（口径不同，不是笔误）")
check(hw.COMPANY_UE_RX_ANT == 4 and hw.COMPANY_LINK == "DL", "默认 4R 下行")

# 自动挂载规则：只对 64T 面板生效，显式指定一律尊重
c1 = {"bs_panel": [8, 4, 2]}
hw.apply_array_defaults(c1)
check(hw.strip_markers(c1) == "company_1to3_192ae", "64T 面板自动切真实阵列")
check(c1["antenna_model_mode"] == "effective_subarray", "模式为 effective_subarray")
check(c1["bs_antenna"]["fixed_vertical_subarray"]["ae_vertical_spacing_lambda"] == 0.67,
      "挂上去的垂直间距是 0.67λ")

c2 = {"bs_panel": [16, 8, 2]}
hw.apply_array_defaults(c2)
check(hw.strip_markers(c2) == "skipped_non_64t", "非 64T 面板不套 1 驱 3（它是这款硬件的事实，不是通用规律）")
check("antenna_model_mode" not in c2, "非 64T 面板保持 ChannelHub 默认")

c3 = {"bs_panel": [8, 4, 2], "antenna_model_mode": "legacy_64"}
hw.apply_array_defaults(c3)
check(hw.strip_markers(c3) == "explicit" and c3["antenna_model_mode"] == "legacy_64",
      "显式指定 legacy_64 时不被覆盖（对照实验要用）")

# 预设：默认组必须真的走真实阵列
_pg = pl.preset_groups()
check("本地默认" in _pg, "有「本地默认」分组")
for name in _pg.get("本地默认", []):
    c = dict(pl.load_presets()[name]["config"])
    check(int(c.get("num_rb", 0)) == 272, f"{name} 用 272 RB")
    check(float(c.get("carrier_freq_hz", 0)) == 2.6e9, f"{name} 用 2.6 GHz")
    check(int(c.get("num_ue_rx_ant", 0)) == 4, f"{name} 默认 4R 接收")
    # **不在 preset 里写死 bs_panel**：写死会让 4T4R 这类天线覆盖失效
    # （num_bs_tx_ant 改了、panel 还是 64 口，两者矛盾）。让它由
    # _ensure_bs_panel 从 num_bs_tx_ant 推导，64 -> [8,4,2] 正是要的。
    check("bs_panel" not in c, f"{name} 不写死 bs_panel（由端口数推导）")

# sw_plan 的兜底预设应当是本地默认配置
_d, _prof = pl.create_draft("验证一个 CSI 压缩的想法")
check(_d.preset == "company_64t4r", f"通用意图默认挑 company_64t4r（实得 {_d.preset}）")

# 端到端：summary 必须带阵列口径
_cfg = dict(pl.load_presets()["company_64t4r"]["config"])
_cfg["num_rb"] = 24
_cfg["num_ues"] = 4
_s = gen.generate(_cfg, num_samples=8, workers=1)
_am = _s.get("antenna_model") or {}
print(f"  summary.antenna_model: mode={_am.get('antenna_model_mode')} "
      f"AE={_am.get('physical_elements')} dv={_am.get('ae_vertical_spacing_lambda')}")
check(_am.get("antenna_model_mode") == "effective_subarray", "summary 记录了真实阵列模式")
check(_am.get("physical_elements") == 192, "summary 记录了 192 物理阵子")
check(_am.get("element_pattern_is_measured") is False,
      "明示阵元方向图不是实测的（是 3GPP 式参数化模型）")
check("几何 SINR / IoT 不受它影响" in (_am.get("note") or ""),
      "明示阵列模型不影响几何 SINR/IoT")

# ---------------------------------------------------------------------------
sect("9.8  仿真说明书")

import re as _re  # noqa: E402
import xml.etree.ElementTree as _ET  # noqa: E402

from superwireless import spec as sp  # noqa: E402


def _svgs(path):
    h = Path(path).read_text(encoding="utf-8")
    return h, _re.findall(r"<svg.*?</svg>", h, _re.S)


# 多小区（六边形栅格）
_r1 = sp.write_spec(dict(pl.load_presets()["company_64t4r_multicell"]["config"]),
                    num_samples=100, title="test-hex")
_h1, _s1 = _svgs(_r1["html_path"])
print(f"  hex: {_r1['headline'][:60]}")
check(len(_s1) == 5, f"五张图都在（阵列/拓扑/频域/TDD/剖面），实得 {len(_s1)}")
for _i, _sv in enumerate(_s1):
    try:
        _ET.fromstring(_sv)
    except _ET.ParseError as _e:
        check(False, f"svg{_i} XML 格式正确（{_e}）")
check(_h1.rstrip().endswith("</html>"), "HTML 完整闭合")

_lay = [x for x in _s1 if "网络拓扑" in x][0]
_sites = len(_re.findall(r'<circle class="st"', _lay)) - 1   # 减去图例那个
check(_sites == 7, f"六边形 7 站都画出来了（实得 {_sites}）")
check(len(_re.findall(r'<line class="bs"', _lay)) - 1 == 21, "21 个扇区指向都画出来了")

# 线性拓扑（高铁）：站点沿轨道两侧交错，不能画成一个点
_r2 = sp.write_spec(dict(pl.load_presets()["hst_350kmh"]["config"]), title="test-linear")
_h2, _s2 = _svgs(_r2["html_path"])
_lay2 = [x for x in _s2 if "网络拓扑" in x][0]
_cx = [float(x) for x in _re.findall(r'<circle class="st" cx="([-\d.]+)"', _lay2)][:-1]
print(f"  linear: {len(_cx)} 站，横坐标跨度 {max(_cx) - min(_cx):.0f}")
check(len(_cx) == 7, f"线性拓扑 7 站（实得 {len(_cx)}）")
check(max(_cx) - min(_cx) > 200, "线性拓扑真的铺开了，不是挤成一个点")

# **同一秒内连出两份不能互相覆盖。** 秒级时间戳做文件名时踩过：
# 后一份直接盖掉前一份且不报错，用户拿到的路径指向的是别人的图。
_a = sp.write_spec(dict(pl.load_presets()["company_64t4r"]["config"]), title="a")
_b = sp.write_spec(dict(pl.load_presets()["hst_350kmh"]["config"]), title="b")
check(_a["html_path"] != _b["html_path"], "同一秒生成的两份说明书文件名不冲突")
_na = len(_re.findall(r'<circle class="st"',
                      [x for x in _svgs(_a["html_path"])[1] if "网络拓扑" in x][0]))
_nb = len(_re.findall(r'<circle class="st"',
                      [x for x in _svgs(_b["html_path"])[1] if "网络拓扑" in x][0]))
check(_na != _nb, "两份内容各自独立（没被对方覆盖）")

# 阵列图必须如实反映实际用的模型
check("1 驱 3" in _h1 and "192" in _h1, "64T 说明书画的是 1 驱 3 / 192 阵子")
check("0.67" in _h1, "标注了 0.67λ 垂直间距")
_r3 = sp.write_spec(dict(pl.load_presets()["company_64t4r_legacy_array"]["config"]),
                    title="test-legacy")
_h3 = Path(_r3["html_path"]).read_text(encoding="utf-8")
check("legacy" in _h3, "legacy 配置画的是独立阵元，不冒充 1 驱 3")
check(any("legacy" in n for n in _r3["notes"]),
      "64T 却走 legacy 时在 notes 里明确警告")

# 参数来源要分得清
_r4 = sp.write_spec(dict(pl.load_presets()["company_64t4r"]["config"]),
                    user_set=["scenario", "num_ues"], title="test-user")
check(_r4["num_user_set"] == 2, f"用户指定项计数正确（实得 {_r4['num_user_set']}）")
check(_r4["num_params"] > _r4["num_user_set"], "其余标为默认值")

# 生成时自动带一份
_cfgs = dict(pl.load_presets()["company_64t4r"]["config"])
_cfgs["num_rb"] = 24
_cfgs["num_ues"] = 4
_ss = gen.generate(_cfgs, num_samples=8, workers=1)
_sheet = _ss.get("spec_sheet") or {}
check("html_path" in _sheet, "sw_generate 自动产出说明书")
check(Path(_sheet.get("html_path", "")).is_file(), "说明书文件真的落盘了")
check("UE" in Path(_sheet["html_path"]).read_text(encoding="utf-8"),
      "生成后的说明书带真实撒点")

# 分级呈现：拓扑图打头，其余折进 tab
check(_h1.count('name="tb"') == 5, "五个 tab 都在")
check(_h1.count('<section id="pn') == 5, "五个面板都在")
check('id="tb1" checked' in _h1, "默认停在总览")
# tab 用纯 CSS 实现，**离线双击打开必须能用**，不许依赖 JS
check("<script" not in _h1, "不依赖 JS（离线 file:// 打开也能切页签）")
# input 必须是 .tabs 的直接子元素且与 .panels 同级，~ 选择器才成立。
# 早先套了一层 .tabbar，页面上直接露出原生 radio、一个面板都不显示——
# 光看代码看不出来，是在浏览器里看出来的。
check('<div class="tabs">\n<input' in _h1 or '<div class="tabs">\r\n<input' in _h1,
      "input 是 .tabs 的直接子元素（否则 CSS 兄弟选择器失效、tab 变成裸 radio）")
check("#tb1:checked~.panels>#pn1" in _h1, "选择器按 id 一一对应")

# 拓扑图在 tab 之外（首屏就能看到），不是折进某个页签
_hero_at = _h1.index('class="hero"')
_tabs_at = _h1.index('<div class="tabs">')
check(_hero_at < _tabs_at, "拓扑图在 tab 之前 —— 打开就看得见，不用点")
check(_h1.index('class="facts"') < _tabs_at, "关键信息卡也在首屏")

# highlight：对话里点过名的参数顶到最前并高亮
_r5 = sp.write_spec(dict(pl.load_presets()["company_64t4r_multicell"]["config"]),
                    title="test-hl", highlight=["isd_m"])
_h5 = Path(_r5["html_path"]).read_text(encoding="utf-8")
# 注意别拿 '<div class="fact' 去找第一张卡片——它会先命中容器
# '<div class="facts">'。从容器结束的位置往后找。
_box = _h5.index('<div class="facts">') + len('<div class="facts">')
check(_h5[_box:_box + 40].startswith('<div class="fact hi"'),
      "被 highlight 的信息卡排在第一个并高亮")
_r6 = sp.write_spec(dict(pl.load_presets()["company_64t4r_multicell"]["config"]),
                    title="test-nohl")
_h6 = Path(_r6["html_path"]).read_text(encoding="utf-8")
check('class="fact hi"' not in _h6, "没传 highlight 时不乱高亮")

# 说明文字里的 ** 要变成 <b>，不能在页面上露出星号
check("**" not in _h1.split("<footer>")[0], "页面上没有裸露的 markdown 星号")

# 画布随规模自适应：单站不该用多小区那么大的画布
_r7 = sp.write_spec(dict(pl.load_presets()["company_64t4r"]["config"]), title="test-1cell")
_h7 = Path(_r7["html_path"]).read_text(encoding="utf-8")
_lay7 = [x for x in _re.findall(r"<svg.*?</svg>", _h7, _re.S) if "网络拓扑" in x][0]
_lay1 = [x for x in _s1 if "网络拓扑" in x][0]
_w7 = int(_re.search(r'viewBox="0 0 (\d+)', _lay7).group(1))
_w1 = int(_re.search(r'viewBox="0 0 (\d+)', _lay1).group(1))
print(f"  画布：单站 {_w7} / 多小区 {_w1}")
check(_w7 < _w1, "单站用更小的画布（大画布只会得到一片空白加中间一个点）")

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
