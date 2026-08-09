"""取货代码生成。

MCP 返回的不是数据，是一段能直接跑的代码。用户改主意想多要一种测量量时，
重新取货即可，**不必重跑仿真**——测量量都是从信道现算的。
"""
from __future__ import annotations

from typing import Any

from . import measure
from .generate import load_summary
from .paths import project_root

_HEADER = '''"""{title}

数据集 {ds}｜{n} 个样本｜{model}｜{scenario}
由 superwireless 生成，直接运行即可。
"""
import sys
sys.path.insert(0, r"{src}")

from superwireless import load

ds = load("{ds}")
print(ds)
'''

_BLOCKS: dict[str, str] = {
    "channel": '''
# ── 信道矩阵 ────────────────────────────────────────────────
H      = ds.h_true      # [N, T, RB, BS_ant, UE_ant] complex64，理想信道
H_est  = ds.h_est       # 同形，带导频与噪声的估计信道
print("H:", H.shape, H.dtype)

# 估计误差（做信道估计类课题时的基准指标）
# nmse_db = ds.estimation_error_nmse_db()   # [N] dB
''',
    "sinr": '''
# ── 链路标量 ────────────────────────────────────────────────
sinr = ds.sinr_dB       # [N] 信干噪比
snr  = ds.snr_dB        # [N] 信噪比
print("SINR 中位数 %.1f dB" % float(__import__("numpy").median(sinr)))
''',
    "pdp": '''
# ── 时延功率谱（未归一化，带真实时延轴）────────────────────
p = ds.pdp(0)                    # 第 0 个样本；ds.pdp() 返回全部
print("RMS 时延扩展 %.1f ns" % (p.rms_delay_spread_s * 1e9))
# p.power     [RB] 线性功率，未归一化
# p.delays_s  [RB] 对应时延（秒）
# p.power_db  [RB] dB
''',
    "paths": '''
# ── 每条径的几何 ────────────────────────────────────────────
paths = ds.paths()
print("径数 %d，是否含角度：%s" % (paths.num_paths, paths.aoa_rad is not None))
# paths.delays_s      [L] 每径时延
# paths.powers_db     [L] 每径功率
# paths.aoa_rad       [L] 到达角（CDL 才有，TDL 为 None）
# paths.aod_rad       [L] 发射角
# paths.zoa_rad / zod_rad  俯仰角
''',
    "srs": '''
# ── SRS 侧空间特征（完整协方差与全部特征值）────────────────
f = ds.srs(0)
print("主导秩 %d，最大特征值 %.3e" % (f.dominant_rank, f.eigenvalues[0]))
# f.covariance    [BS, BS] 完整空间协方差 R_hh
# f.eigenvalues   [BS] 全部特征值（降序，非只取前 4 个）
# f.eigenvectors  [BS, BS]
# f.beam_rsrp_db  [n_beams] DFT 波束域 RSRP
''',
    "pmi": '''
# ── PMI（Type-I-style 单面板列码本子集近似，非 MAE token）──
w = ds.pmi(0)
print("PMI 索引 %s，秩 %d，码本大小 %d" % (w.indices, w.rank, w.codebook_size))
# w.indices    每层选中的码本列号
# w.precoder   [ports, rank] 预编码矩阵 W
# w.layout     (n_h, n_v) 阵型解读
''',
    "rsrp": '''
# ── 功率类（不做区间截断）──────────────────────────────────
g = ds.rsrp()           # [N, BS_ant] 每天线信道增益 dB
print("每天线增益范围 %.1f ~ %.1f dB" % (g.min(), g.max()))
''',
    "capacity": '''
# ── 容量与条件数 ────────────────────────────────────────────
cap  = ds.capacity()            # [N] bit/s/Hz
cond = ds.condition_number()    # [N] 空间复用难度
print("容量中位数 %.2f bit/s/Hz" % float(__import__("numpy").median(cap)))
''',
    "geometry": '''
# ── 几何与大尺度量 ──────────────────────────────────────────
g = ds.geometry
for k in ("pathloss_dB", "distance_3d_m", "is_los", "doppler_hz"):
    if k in g:
        print("%-18s 均值 %.2f" % (k, float(g[k][~__import__("numpy").isnan(g[k])].mean())))
# g["ue_position"]  [N, 3] 终端坐标
''',
    "topology": '''
# ── 多小区测量 ──────────────────────────────────────────────
if ds.ssb:
    print("SSB RSRP:", ds.ssb["ssb_rsrp_dBm"].shape)   # [N, K] 每小区
if ds.h_interferers is not None:
    print("干扰信道:", ds.h_interferers.shape)          # [N, K-1, T, RB, BS, UE]
else:
    print("单小区场景，无干扰信道")
''',
    "linkperf": '''
# ── 链路性能：预编码 → 逐层 SINR → 谱效 ────────────────────
r = ds.link(0, method="svd", receiver="mmse")
print("谱效 %.2f bit/s/Hz（容量上界 %.2f，达成 %.0f%%）" % (
    r.spectral_efficiency, r.capacity_bound,
    100 * r.spectral_efficiency / r.capacity_bound))
print("逐层 SINR:", [round(x, 1) for x in r.sinr_per_layer_db], "dB   rank =", r.rank)

# 蒙特卡洛：均值 + 95% 置信区间 + 收敛判断
mc = ds.monte_carlo(method="svd")
print("谱效 %.3f ± %.3f，收敛=%s" % (
    mc.se_mean, (mc.se_ci95[1] - mc.se_ci95[0]) / 2, mc.converged))
if not mc.converged:
    print("  样本量不足，两方案的差异可能只是噪声")

# 横向对比：你的方案该跟这些比
for name, v in ds.compare_precoders().items():
    print("  %-14s %6.2f bit/s/Hz  (SVD 的 %.0f%%)" % (name, v["se_mean"], v["vs_svd_pct"]))

# 用估计信道做预编码，评估 CSI 误差的代价
# r_est = ds.link(0, method="svd", h_for_precoding=ds.h_est[0])
''',
    "validate": '''
# ── 可信度体检 ──────────────────────────────────────────────
rep = ds.validate()
print(rep.text())
# rep.passed 为 False 时，结论不可信——先修配置再跑实验
''',
}


def build_code(dataset_id: str, want: str | list[str] | None = None) -> dict[str, Any]:
    """按点单生成取货代码。"""
    summary = load_summary(dataset_id)
    wanted = measure.resolve_measurements(want)

    src = str(project_root() / "src")
    code = _HEADER.format(
        title="superwireless 取货代码",
        ds=dataset_id,
        n=summary.get("num_samples"),
        model=summary.get("channel_model"),
        scenario=summary.get("scenario"),
        src=src,
    )
    for name in wanted:
        block = _BLOCKS.get(name)
        if block:
            code += block

    is_rt = (summary.get("sample_meta", {}) or {}).get("channel_generation_mode") == "sionna_rt"

    notes: list[str] = []
    if "paths" in wanted and is_rt:
        notes.append(
            "该数据集由射线追踪生成，多径来自真实建筑几何。ds.paths() 会报错——"
            "套用 CDL/TDL 标准剖面得到的角度与本数据无关，属于错误结果。"
            "需要每条径的角度请改用 CDL 模型重新生成。"
        )
    elif "paths" in wanted and not summary.get("is_cdl"):
        notes.append(
            "该数据集用的是 TDL 模型，没有每条径的角度信息（角度字段会是 None）。"
            "需要角度请改用 CDL-A~E 重新生成。"
        )
    if is_rt:
        notes.append(
            "射线追踪数据：信道来自真实建筑几何的反射与绕射，"
            "channel_model 字段仅作为 ChannelHub 内部回退标记，不代表实际信道生成方式。"
        )
    if "topology" in wanted and not summary.get("shape", {}).get("N"):
        notes.append("单小区场景没有干扰信道。")

    return {
        "dataset_id": dataset_id,
        "measurements": wanted,
        "code": code,
        "catalog": {k: v for k, v in measure.MEASUREMENT_CATALOG.items()},
        "notes": notes,
        "hint": "想再要别的测量量，用同一个 dataset_id 重新取货即可，不必重跑仿真。",
    }
