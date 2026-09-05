#!/usr/bin/env python3
"""把退役的 ``bimodal`` 话务旋钮机械转写成两份经验 CDF。

**这不是新的现网数据。** 它只是把 2026-08-02 那套「按占用 RBG 数分布」的旋钮
（``p_small_rbg=0.30`` / ``p_full_rbg=0.30`` / 中间 2..N-1 均匀）在合并容量与
体验模式时原样搬进合法的 CDF 输入域，好让 ``sys_field_bimodal_traffic``
这个场景不随 legacy 路径一起消失。

两处必须说清的口径变化：

1. **RBG 数 → 字节的折算只做一次，且只在这里。** legacy 的 ``_Traffic`` 每次
   抽到 ``n_rbg`` 就按写死的 3.0 bit/s/Hz 折成字节；本脚本把同一个折算固化
   成包长分布写进文件。仿真里**一次传输实际占几个 RBG 不再由它决定**，而是由
   该用户当时的 MCS/rank 经 TBS 反查决定——这正是体验模式的定义。
2. **``p_idle_tti`` 没有对应物，也不该有。** 它在 legacy 里就不驱动仿真，只进
   一个解析式。空闲 TTI 由到达率与信道决定，如实测出来。

    python scripts/make_field_bimodal_cdf.py
"""
from __future__ import annotations

import math
from pathlib import Path

# legacy TrafficConfig 的三个旋钮与载波口径（company_64t4r_multicell）
P_SMALL_RBG = 0.30
P_FULL_RBG = 0.30
NUM_RBG = 17
RB_PER_RBG = 16
# legacy `_Traffic._per_rbg_bytes`：RB × 12 子载波 × 12 数据符号 × 3.0 bit/s/Hz / 8
PER_RBG_BYTES = max(50, int(RB_PER_RBG * 12 * 12 * 3.0 / 8))
# legacy `_Traffic.step`：n_bytes = max(200, per_rbg_bytes * n_rbg)
MIN_BYTES = 200
# legacy 默认 arrival_rate_hz=2.0 的泊松过程 ⇒ 指数包间隔，均值 500 ms
MEAN_INTERARRIVAL_MS = 1000.0 / 2.0
INTERARRIVAL_POINTS = 64
TAIL_QUANTILE = 0.999

OUT_DIR = Path(__file__).resolve().parents[1] / "presets" / "traffic_cdf"


def packet_size_rows() -> list[tuple[int, float]]:
    """RBG 数分布 → 包长 CDF。中间段 2..NUM_RBG-1 均匀，与 legacy 的抽样一致。"""
    mid = list(range(2, max(3, NUM_RBG)))          # legacy: rng.integers(2, max(3, N))
    p_mid_total = max(0.0, 1.0 - P_SMALL_RBG - P_FULL_RBG)
    pmf: dict[int, float] = {}
    pmf[1] = pmf.get(1, 0.0) + P_SMALL_RBG
    pmf[NUM_RBG] = pmf.get(NUM_RBG, 0.0) + P_FULL_RBG
    for n in mid:
        pmf[n] = pmf.get(n, 0.0) + p_mid_total / len(mid)
    rows: list[tuple[int, float]] = []
    acc = 0.0
    for n in sorted(pmf):
        acc += pmf[n]
        rows.append((max(MIN_BYTES, PER_RBG_BYTES * n), acc))
    rows[-1] = (rows[-1][0], 1.0)
    return rows


def interarrival_rows() -> list[tuple[float, float]]:
    """指数分布的离散化：等概率分位点，末点取 0.999 分位再收敛到 1。"""
    rows: list[tuple[float, float]] = []
    for i in range(1, INTERARRIVAL_POINTS + 1):
        q = TAIL_QUANTILE * i / INTERARRIVAL_POINTS
        value = -MEAN_INTERARRIVAL_MS * math.log(1.0 - q)
        rows.append((round(max(value, 1e-3), 4), round(q, 6)))
    rows.append((rows[-1][0] * 2.0, 1.0))
    return rows


def write(path: Path, header: str, unit: str, rows) -> None:
    lines = [f"# {header}", "# 由 scripts/make_field_bimodal_cdf.py 生成，勿手改",
             f"value_{unit},cdf"]
    lines += [f"{v},{c}" for v, c in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{path}  ({len(rows)} 点)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write(OUT_DIR / "field_bimodal_packet_size.csv",
          "现网两头高中间低画像的包长分布（由退役的 bimodal RBG 数分布机械转写）",
          "bytes", packet_size_rows())
    write(OUT_DIR / "field_bimodal_interarrival.csv",
          "指数包间隔，均值 500 ms（等价于 legacy 的 arrival_rate_hz=2.0 泊松到达）",
          "ms", interarrival_rows())


if __name__ == "__main__":
    main()
