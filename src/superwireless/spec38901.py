"""3GPP TR 38.901 的标准查表值，逐字录入，用于对标而非仿真。

这个模块**只放标准原文的数**，不放任何计算逻辑。存在的理由是：仿真器内部
也有一份同样的表，两份独立录入的表对不上，就说明其中一份抄错了——而抄错的
剖面表不会报错，只会安静地产出物理上说得通、但不是标准剖面的信道。

来源：TR 38.901 V17.0.0 (2022-03)

* CDL-A  Table 7.7.1-1（23 簇，NLOS）
* CDL-B  Table 7.7.1-2（23 簇，NLOS）
* CDL-C  Table 7.7.1-3（24 簇，NLOS）

CDL-D / CDL-E（Table 7.7.1-4 / -5）多一列 ``Cluster PAS``，且首簇被拆成
镜面分量与 Laplacian 分量两行，录入口径与前三张表不同——**没有逐字核对过的
表就不放进来**，宁可少一项检查，也不放一份可能自己就抄错了的"标准值"。

每张表的字段顺序与标准一致：归一化时延、功率(dB)、AOD、AOA、ZOD、ZOA，角度单位度。
"""
from __future__ import annotations

from typing import Any

import numpy as np

# Table 7.7.1-1. CDL-A
CDL_A = {
    "delays_norm": [
        0.0000, 0.3819, 0.4025, 0.5868, 0.4610, 0.5375, 0.6708, 0.5750,
        0.7618, 1.5375, 1.8978, 2.2242, 2.1718, 2.4942, 2.5119, 3.0582,
        4.0810, 4.4579, 4.5695, 4.7966, 5.0066, 5.3043, 9.6586,
    ],
    "powers_dB": [
        -13.4, 0.0, -2.2, -4.0, -6.0, -8.2, -9.9, -10.5, -7.5, -15.9, -6.6,
        -16.7, -12.4, -15.2, -10.8, -11.3, -12.7, -16.2, -18.3, -18.9,
        -16.6, -19.9, -29.7,
    ],
    "aod_deg": [
        -178.1, -4.2, -4.2, -4.2, 90.2, 90.2, 90.2, 121.5, -81.7, 158.4,
        -83.0, 134.8, -153.0, -172.0, -129.9, -136.0, 165.4, 148.4, 132.7,
        -118.6, -154.1, 126.5, -56.2,
    ],
    "aoa_deg": [
        51.3, -152.7, -152.7, -152.7, 76.6, 76.6, 76.6, -1.8, -41.9, 94.2,
        51.9, -115.9, 26.6, 76.6, -7.0, -23.0, -47.2, 110.4, 144.5, 155.3,
        102.0, -151.8, 55.2,
    ],
    "zod_deg": [
        50.2, 93.2, 93.2, 93.2, 122.0, 122.0, 122.0, 150.2, 55.2, 26.4,
        126.4, 171.6, 151.4, 157.2, 47.2, 40.4, 43.3, 161.8, 10.8, 16.7,
        171.7, 22.7, 144.9,
    ],
    "zoa_deg": [
        125.4, 91.3, 91.3, 91.3, 94.0, 94.0, 94.0, 47.1, 56.0, 30.1, 58.8,
        26.0, 49.2, 143.1, 117.4, 122.7, 123.2, 32.6, 27.2, 15.2, 146.0,
        150.7, 156.1,
    ],
    "per_cluster": {"cASD": 5, "cASA": 11, "cZSD": 3, "cZSA": 3, "XPR": 10},
    "table": "38.901 Table 7.7.1-1",
}

# Table 7.7.1-2. CDL-B
CDL_B = {
    "delays_norm": [
        0.0000, 0.1072, 0.2155, 0.2095, 0.2870, 0.2986, 0.3752, 0.5055,
        0.3681, 0.3697, 0.5700, 0.5283, 1.1021, 1.2756, 1.5474, 1.7842,
        2.0169, 2.8294, 3.0219, 3.6187, 4.1067, 4.2790, 4.7834,
    ],
    "powers_dB": [
        0.0, -2.2, -4.0, -3.2, -9.8, -1.2, -3.4, -5.2, -7.6, -3.0, -8.9,
        -9.0, -4.8, -5.7, -7.5, -1.9, -7.6, -12.2, -9.8, -11.4, -14.9,
        -9.2, -11.3,
    ],
    "aod_deg": [
        9.3, 9.3, 9.3, -34.1, -65.4, -11.4, -11.4, -11.4, -67.2, 52.5,
        -72.0, 74.3, -52.2, -50.5, 61.4, 30.6, -72.5, -90.6, -77.6, -82.6,
        -103.6, 75.6, -77.6,
    ],
    "aoa_deg": [
        -173.3, -173.3, -173.3, 125.5, -88.0, 155.1, 155.1, 155.1, -89.8,
        132.1, -83.6, 95.3, 103.7, -87.8, -92.5, -139.1, -90.6, 58.6, -79.0,
        65.8, 52.7, 88.7, -60.4,
    ],
    "zod_deg": [
        105.8, 105.8, 105.8, 115.3, 119.3, 103.2, 103.2, 103.2, 118.2,
        102.0, 100.4, 98.3, 103.4, 102.5, 101.4, 103.0, 100.0, 115.2,
        100.5, 119.6, 118.7, 117.8, 115.7,
    ],
    "zoa_deg": [
        78.9, 78.9, 78.9, 63.3, 59.9, 67.5, 67.5, 67.5, 82.6, 66.3, 61.6,
        58.0, 78.2, 82.0, 62.4, 78.0, 60.9, 82.9, 60.8, 57.3, 59.9, 60.1,
        62.3,
    ],
    "per_cluster": {"cASD": 10, "cASA": 22, "cZSD": 3, "cZSA": 7, "XPR": 8},
    "table": "38.901 Table 7.7.1-2",
}

# Table 7.7.1-3. CDL-C
CDL_C = {
    "delays_norm": [
        0.0000, 0.2099, 0.2219, 0.2329, 0.2176, 0.6366, 0.6448, 0.6560,
        0.6584, 0.7935, 0.8213, 0.9336, 1.2285, 1.3083, 2.1704, 2.7105,
        4.2589, 4.6003, 5.4902, 5.6077, 6.3065, 6.6374, 7.0427, 8.6523,
    ],
    "powers_dB": [
        -4.4, -1.2, -3.5, -5.2, -2.5, 0.0, -2.2, -3.9, -7.4, -7.1, -10.7,
        -11.1, -5.1, -6.8, -8.7, -13.2, -13.9, -13.9, -15.8, -17.1, -16.0,
        -15.7, -21.6, -22.8,
    ],
    "aod_deg": [
        -46.6, -22.8, -22.8, -22.8, -40.7, 0.3, 0.3, 0.3, 73.1, -64.5,
        80.2, -97.1, -55.3, -64.3, -78.5, 102.7, 99.2, 88.8, -101.9, 92.2,
        93.3, 106.6, 119.5, -123.8,
    ],
    "aoa_deg": [
        -101.0, 120.0, 120.0, 120.0, -127.5, 170.4, 170.4, 170.4, 55.4,
        66.5, -48.1, 46.9, 68.1, -68.7, 81.5, 30.7, -16.4, 3.8, -13.7, 9.7,
        5.6, 0.7, -21.9, 33.6,
    ],
    "zod_deg": [
        97.2, 98.6, 98.6, 98.6, 100.6, 99.2, 99.2, 99.2, 105.2, 95.3,
        106.1, 93.5, 103.7, 104.2, 93.0, 104.2, 94.9, 93.1, 92.2, 106.7,
        93.0, 92.9, 105.2, 107.8,
    ],
    "zoa_deg": [
        87.6, 72.1, 72.1, 72.1, 70.1, 75.3, 75.3, 75.3, 67.4, 63.8, 71.4,
        60.5, 90.6, 60.1, 61.0, 100.7, 62.3, 66.7, 52.9, 61.8, 51.9, 61.7,
        58.0, 57.0,
    ],
    "per_cluster": {"cASD": 2, "cASA": 15, "cZSD": 3, "cZSA": 7, "XPR": 7},
    "table": "38.901 Table 7.7.1-3",
}

CDL_TABLES: dict[str, dict] = {"CDL-A": CDL_A, "CDL-B": CDL_B, "CDL-C": CDL_C}

# 有标准表可对的剖面。CDL-D/E 未录入，见模块开头说明。
COVERED = tuple(CDL_TABLES)


def as_arrays(name: str) -> dict[str, np.ndarray]:
    """取某个剖面的标准表，字段转成 ndarray。"""
    t = CDL_TABLES[name.upper()]
    return {
        k: np.asarray(v, dtype=float)
        for k, v in t.items()
        if k not in ("per_cluster", "table")
    }


# ---------------------------------------------------------------------------
# 把标准表灌回仿真器
# ---------------------------------------------------------------------------


def diff_against_channelhub() -> dict[str, dict]:
    """逐簇比对 ChannelHub 内置的 CDL 表与标准表，返回差异明细。"""
    from .channelhub import cdl_profile

    out: dict[str, dict] = {}
    for name in COVERED:
        spec = as_arrays(name)
        prof = cdl_profile(name)
        fields: dict[str, Any] = {}
        bad = np.zeros(len(spec["powers_dB"]), dtype=bool)
        for f in ("delays_norm", "powers_dB", "aod_deg", "aoa_deg", "zod_deg", "zoa_deg"):
            impl = np.asarray(getattr(prof, f), dtype=float)
            if impl.shape != spec[f].shape:
                fields[f] = {"n_mismatch": -1, "max_abs_diff": float("nan")}
                continue
            d = np.abs(spec[f] - impl)
            hit = d > 0.05
            bad |= hit
            if hit.any():
                fields[f] = {
                    "n_mismatch": int(hit.sum()),
                    "max_abs_diff": round(float(d.max()), 2),
                    "first_mismatch_cluster": int(np.argmax(hit)) + 1,
                }
        pw = 10.0 ** (spec["powers_dB"] / 10.0)
        pw = pw / pw.sum()
        out[name] = {
            "table": CDL_TABLES[name]["table"],
            "n_clusters": int(len(pw)),
            "n_mismatched_clusters": int(bad.sum()),
            "power_share_mismatched": round(float(pw[bad].sum()), 4),
            "fields": fields,
        }
    return out


def apply_spec_tables() -> dict[str, Any]:
    """把逐字核对过的 38.901 查表值灌回 ChannelHub 的剖面注册表。

    **为什么要做这件事**：ChannelHub 的 ``CDL_A/B/C`` 三张表标着
    "TS 38.901 Table 7.7.1-x"，时延与功率大体对得上，但**角度列从中段起
    与标准不符**（CDL-C 有 23/24 簇有出入，占总功率 93.8%）。角度错了不会
    报错，只会让空间相关性、波束赋形增益、到达角估计这类结论安静地偏掉——
    而这正是谱效仿真最依赖的一部分。

    做法是替换 ``msg_embedding.channel_models.cdl._PROFILES`` 里对应的条目。
    仿真器所有取剖面的路径都走 ``get_cdl_profile``，它只读这个字典，所以
    换掉条目就够了，**不改 ChannelHub 任何一行源码**。

    设 ``SUPERWIRELESS_CDL_SPEC=0`` 可关闭，用来复现未修正前的结果。
    CDL-D/E 不在覆盖范围内（表结构不同、未逐字核对过），保持原样。
    """
    import dataclasses
    import os

    if os.environ.get("SUPERWIRELESS_CDL_SPEC", "1") == "0":
        return {"applied": False, "reason": "SUPERWIRELESS_CDL_SPEC=0，按要求跳过"}

    # 注意这里直接拼 sys.path，不走 channelhub._ensure_path —— 后者会回调本函数。
    import sys

    from .channelhub import channelhub_root

    src = str(channelhub_root() / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from msg_embedding.channel_models import cdl as _cdl  # noqa: PLC0415

    patched: list[str] = []
    for name in COVERED:
        prof = _cdl._PROFILES.get(name)
        if prof is None:
            continue
        spec = as_arrays(name)
        if len(np.asarray(prof.powers_dB)) != len(spec["powers_dB"]):
            continue  # 簇数都不同，不敢就地替换
        _cdl._PROFILES[name] = dataclasses.replace(
            prof,
            delays_norm=spec["delays_norm"].copy(),
            powers_dB=spec["powers_dB"].copy(),
            aod_deg=spec["aod_deg"].copy(),
            aoa_deg=spec["aoa_deg"].copy(),
            zod_deg=spec["zod_deg"].copy(),
            zoa_deg=spec["zoa_deg"].copy(),
        )
        patched.append(name)
    return {
        "applied": bool(patched),
        "profiles": patched,
        "source": "TR 38.901 V17.0.0 Table 7.7.1-1/-2/-3",
        "note": "CDL-D/E 未覆盖（表结构含 Cluster PAS 列，未逐字核对），保持 ChannelHub 原值",
    }
