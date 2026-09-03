"""包长感知（EDF）调度的单元、集成与公平性测试。

分三层，对应规格的 §8.1 / §8.2 / §8.3：

* 单元：``scheduler_edf`` 内核的公式与全部边界，不跑仿真；
* 集成：EDF / 混合模式在真实 ``experience_v2`` 主循环里的退化等价性与配置边界；
* 公平性：在**饱和**工作点上验证 EDF 真的在拿公平性换小包时延。

公平性那几条刻意做成方向性判据，而不是"方差 < 20%"这种既要几千 TTI 才有
意义、又会被 11.4% 种子噪声淹掉的绝对阈值。它们是固定种子的确定性回归，
用来锁住行为方向；**不是**带置信区间的统计结论。

一条实测结论直接写进了测试：**饱和下 EDF 会把大包用户饿死到 0 吞吐**，而 PF
不会。这不是 bug，是 EDF 的定价（规格 §6.4 已写明），所以这里把它钉成断言而
不是掩盖——谁把它"修好"了都必须先解释清楚代价换到了哪里。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import scheduler_edf as sedf  # noqa: E402
from superran import system as sysm  # noqa: E402


# ---------------------------------------------------------------------------
# §8.1 单元测试：内核公式与边界
# ---------------------------------------------------------------------------
def test_zero_buffer_gives_zero_priority() -> None:
    """无数据不发。"""
    assert sedf.edf_priority(1000.0, 0.0) == 0.0


def test_zero_tbs_gives_zero_priority() -> None:
    """MCS=0 或 rank=0 时传不动，不发。"""
    assert sedf.edf_priority(0.0, 1000.0) == 0.0


def test_small_and_large_buffer_ratios() -> None:
    """规格 §8.1 的两个基准值：小包 10，大包 0.01。"""
    assert sedf.edf_priority(1000.0, 100.0) == pytest.approx(10.0)
    assert sedf.edf_priority(1000.0, 100_000.0) == pytest.approx(0.01)


def test_small_packet_outranks_large_packet() -> None:
    assert sedf.edf_priority(1000.0, 100.0) > sedf.edf_priority(1000.0, 100_000.0)


def test_lch_priority_divides() -> None:
    """priority=2 的承载优先级正好减半（1 = 最高，沿用 5QI 方向）。"""
    base = sedf.edf_priority(1000.0, 100.0, 1)
    assert sedf.edf_priority(1000.0, 100.0, 2) == pytest.approx(base / 2.0)


def test_srb_boost_is_additive_and_absolute() -> None:
    """相同 TBS/Buffer 下 SRB 高出恰好 5000，且足以压过任何数据承载。"""
    data = sedf.edf_priority(1000.0, 100.0)
    srb = sedf.edf_priority(1000.0, 100.0, is_srb=True)
    assert srb - data == pytest.approx(sedf.SRB_PRIORITY_BOOST)
    # 最强反例：数据承载把缓冲区压到 1 字节、TBS 拉满，仍然抢不过一个信道极差
    # 且缓冲区巨大的 SRB。
    assert sedf.edf_priority(1.0, 100_000.0, is_srb=True) > \
        sedf.edf_priority(4000.0, 1.0)


def test_retx_boost_is_additive() -> None:
    data = sedf.edf_priority(1000.0, 100.0)
    retx = sedf.edf_priority(1000.0, 100.0, is_retx=True)
    assert retx - data == pytest.approx(sedf.RETX_PRIORITY_BOOST)


def test_boosts_do_not_resurrect_empty_queues() -> None:
    """空队列即使标了 SRB / 重传也必须是 0——否则会凭空造出幽灵 grant。"""
    assert sedf.edf_priority(1000.0, 0.0, is_srb=True) == 0.0
    assert sedf.edf_priority(0.0, 1000.0, is_retx=True) == 0.0


def test_kernel_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        sedf.edf_priority(-1.0, 100.0)
    with pytest.raises(ValueError):
        sedf.edf_priority(1000.0, float("nan"))
    with pytest.raises(ValueError):
        sedf.edf_priority(1000.0, 100.0, 0)          # priority 最小是 1
    with pytest.raises(ValueError):
        sedf.mixed_metric(np.ones(2), np.ones(2), np.ones(2), weight=1.5)


def test_vector_metric_matches_scalar_kernel() -> None:
    """向量化实现与标量参考实现必须逐元素一致（含两条边界）。"""
    tbs = np.array([1000.0, 1000.0, 1000.0, 0.0, 500.0])
    buf = np.array([100.0, 100_000.0, 0.0, 1000.0, 250.0])
    prio = np.array([1.0, 1.0, 1.0, 1.0, 0.5])
    srb = np.array([False, False, True, True, False])
    got = sedf.edf_metric(tbs, buf, prio, srb_mask=srb)
    want = [sedf.edf_priority(t, b, 1.0 / p, is_srb=bool(m))
            for t, b, p, m in zip(tbs, buf, prio, srb, strict=True)]
    assert got == pytest.approx(want)


def test_mixed_metric_degenerates_at_both_ends() -> None:
    """w=0 只剩 EPF，w=1 只剩 EDF——混合模式两端必须严格退化。"""
    epf = np.array([3.0, 7.0, 11.0])
    edf = np.array([0.5, 0.25, 2.0])
    factor = np.array([1.0, 0.5, 1.0])
    assert sedf.mixed_metric(
        epf, edf, factor, weight=0.0) == pytest.approx(epf * factor)
    assert sedf.mixed_metric(
        epf, edf, factor, weight=1.0) == pytest.approx(edf * factor)


def test_mixed_metric_follows_blueprint_form() -> None:
    """照抄 蓝本原式，不做归一化改写。"""
    got = sedf.mixed_metric(np.array([4.0]), np.array([0.5]), np.array([0.5]),
                            weight=0.25, epf_scale=3.0)
    assert got == pytest.approx([(0.75 * 3.0 * 4.0 + 0.25 * 0.5) * 0.5])


# ---------------------------------------------------------------------------
# 仿真夹具
# ---------------------------------------------------------------------------
def _tables(n_ue: int, sinr_hi: float, sinr_lo: float, seed: int = 0):
    rng = np.random.default_rng(seed)
    hs = [((rng.standard_normal((8, 24, 16, 4))
            + 1j * rng.standard_normal((8, 24, 16, 4))) / np.sqrt(2))
          for _ in range(n_ue)]
    return sysm.build_link_tables(
        hs, list(np.linspace(sinr_hi, sinr_lo, n_ue)), power_constraint="nebf")


#: 轻载：8 UE、占用率约 0.83，用于等价性与配置边界（跑得快）。
_LIGHT_TABLES = _tables(8, 25.0, 0.0)
#: 饱和：24 UE、占用率约 1.0，调度器必须做取舍，公平性差异才显现出来。
_SAT_TABLES = _tables(24, 10.0, -3.0)

_SAT_TRAFFIC = dict(model="mixed", small_ue_share=0.5, small_file_bytes=600,
                    small_arrival_rate_hz=300.0, file_bytes=400_000,
                    arrival_rate_hz=30.0)
_LIGHT_TRAFFIC = dict(model="mixed", small_ue_share=0.5, small_file_bytes=600,
                      small_arrival_rate_hz=400.0, file_bytes=400_000,
                      arrival_rate_hz=40.0)

_CACHE: dict[tuple, object] = {}


def _run(algorithm: str, *, saturated: bool = False, traffic=None,
         **sched_kwargs):
    key = (algorithm, saturated, traffic is not None,
           tuple(sorted(sched_kwargs.items())))
    if traffic is None and key in _CACHE:
        return _CACHE[key]
    cfg = sysm.SystemConfig(
        evaluation_mode="experience", duration_s=0.5 if saturated else 0.8,
        seed=41, tdd_pattern="DDDSU")
    run = sysm.simulate(
        _SAT_TABLES if saturated else _LIGHT_TABLES,
        sys_cfg=cfg,
        traffic=traffic or sysm.TrafficConfig(
            **(_SAT_TRAFFIC if saturated else _LIGHT_TRAFFIC)),
        sched=sysm.SchedulerConfig(algorithm=algorithm, mu_enabled=False,
                                   olla_enabled=False, **sched_kwargs),
        kpi=sysm.KpiConfig(warmup_tti=0))
    if traffic is None:
        _CACHE[key] = run
    return run


def _jain(values) -> float:
    v = np.asarray([float(x) for x in values
                    if x is not None and np.isfinite(x)])
    if v.size == 0 or float(np.sum(v ** 2)) <= 0:
        return float("nan")
    return float(np.sum(v) ** 2 / (v.size * np.sum(v ** 2)))


def _served(run) -> list[float]:
    return [float(u["served_mbps"]) for u in run.users]


# ---------------------------------------------------------------------------
# §8.2 集成测试：主循环里的等价性与配置边界
# ---------------------------------------------------------------------------
_IDENTITY_KEYS = ("cell_served_mbps", "cell_experienced_mbps",
                  "ue_experienced_median_mbps", "ue_experienced_p5_mbps",
                  "avg_mcs", "bler_first_tx", "occupancy")


def test_mixed_weight_zero_is_bit_identical_to_qos_pf() -> None:
    """最强反例 ①：w=0 必须与纯 qos_pf 逐位一致，不能只是"接近"。"""
    base, mixed = _run("qos_pf"), _run("qos_pf_edf", edf_mixed_weight=0.0)
    for key in _IDENTITY_KEYS:
        assert mixed.cell[key] == base.cell[key], key


def test_mixed_weight_one_is_bit_identical_to_pure_edf() -> None:
    """最强反例 ②：w=1 必须与纯 edf 逐位一致。"""
    base, mixed = _run("edf"), _run("qos_pf_edf", edf_mixed_weight=1.0)
    for key in _IDENTITY_KEYS:
        assert mixed.cell[key] == base.cell[key], key


def test_edf_actually_changes_the_schedule() -> None:
    """反向保险：EDF 若与 PF 结果完全相同，说明度量根本没接进主循环。"""
    assert _jain(_served(_run("edf", saturated=True))) != \
        _jain(_served(_run("pf", saturated=True)))


def test_result_reports_the_formula_actually_used() -> None:
    identity = _run("edf").cell["scheduler_priority_metric"]
    assert identity["algorithm"] == "edf"
    assert "Buffer" in identity["formula"]
    # SRB 没建模这件事必须写进结果，不能让读者以为 +5000 生效了。
    assert identity["srb_modelled"] is False


def test_srb_boost_never_fires_without_a_signalling_class() -> None:
    """SuperRAN 默认没有 SRB：把加值改成 0 也必须逐位不变。"""
    default, no_boost = _run("edf"), _run("edf", srb_priority_boost=0.0)
    for key in _IDENTITY_KEYS:
        assert no_boost.cell[key] == default.cell[key], key


def test_signalling_class_activates_the_srb_boost() -> None:
    """显式声明 signalling 业务类时 SRB 加值才生效，且不得少拿资源。"""
    traffic = sysm.TrafficConfig(model="mixed", classes=(
        sysm.TrafficClassConfig(
            name="srb", ue_share=0.25, file_bytes=200,
            arrival_rate_hz=200.0, priority=1, resource_type="signalling"),
        sysm.TrafficClassConfig(
            name="data", ue_share=0.75, file_bytes=400_000,
            arrival_rate_hz=40.0, priority=80),
    ))
    boosted = _run("edf", traffic=traffic)
    plain = _run("edf", traffic=traffic, srb_priority_boost=0.0)
    assert boosted.cell["class_acked_bytes"]["srb"] >= \
        plain.cell["class_acked_bytes"]["srb"]


def test_edf_rejects_full_buffer() -> None:
    """full_buffer 把队列钉在 2**50 B，EDF 的分母失去物理含义——硬失败。"""
    with pytest.raises(ValueError, match="有限队列"):
        _run("edf", traffic=sysm.TrafficConfig(model="full_buffer"))


def test_edf_rejects_capacity_mode() -> None:
    """容量口径没有队列可言。"""
    with pytest.raises(ValueError, match="experience"):
        sysm.simulate(
            _LIGHT_TABLES,
            sys_cfg=sysm.SystemConfig(evaluation_mode="capacity",
                                      duration_s=0.2, seed=1),
            traffic=sysm.TrafficConfig(model="ftp3"),
            sched=sysm.SchedulerConfig(algorithm="edf"), kpi=sysm.KpiConfig())


def test_config_validates_edf_parameters() -> None:
    with pytest.raises(ValueError, match="edf_mixed_weight"):
        sysm.SchedulerConfig(edf_mixed_weight=1.5)
    with pytest.raises(ValueError, match="srb_priority_boost"):
        sysm.SchedulerConfig(srb_priority_boost=-1.0)
    with pytest.raises(ValueError, match="未知调度器"):
        sysm.SchedulerConfig(algorithm="edf_typo")


# ---------------------------------------------------------------------------
# §8.3 公平性：EDF 在饱和下用什么换什么（固定种子的方向性回归）
# ---------------------------------------------------------------------------
def test_edf_is_less_fair_than_pf() -> None:
    """判据 (a)：EDF 若不比 PF 更不公平，说明它没有真的按缓冲区排序。

    实测 24 UE 饱和：Jain 0.4707 → 0.3032。
    """
    assert _jain(_served(_run("edf", saturated=True))) < \
        _jain(_served(_run("pf", saturated=True)))


def test_edf_improves_small_packet_immediate_service() -> None:
    """判据 (b)：小包的即时服务比例必须高于 PF——这是 EDF 的全部收益。

    实测 0.7665 → 0.7891。**收益只有 2.3 个百分点**：SuperRAN 的按需 RBG 分配
    早就让小包在 PF 下也基本即时服务，EDF 能额外拿到的并不多。单种子单场景的
    方向性观察，不是带置信区间的结论。
    """
    assert _run("edf", saturated=True).cell["small_immediate_service_ratio"] > \
        _run("pf", saturated=True).cell["small_immediate_service_ratio"]


def test_edf_starves_large_packet_users_under_saturation() -> None:
    """判据 (c)：饱和下 EDF **会**把大包用户饿死到 0，而 PF 不会。

    这是钉住已知代价，不是期望行为。规格 §6.4 写明了饥饿风险，实测 24 UE
    饱和时 2 个 UE 的 served_mbps 恰好为 0。谁要把它改好，必须先说明代价挪到
    了哪里，并同步更新这条断言。缓解手段见
    :func:`test_calibrated_mixed_mode_avoids_starvation`。
    """
    edf_zero = sum(1 for x in _served(_run("edf", saturated=True)) if x == 0.0)
    pf_zero = sum(1 for x in _served(_run("pf", saturated=True)) if x == 0.0)
    assert pf_zero == 0
    assert edf_zero > 0


def test_uncalibrated_mixed_mode_degenerates_and_says_so() -> None:
    """蓝本原式的量纲陷阱必须被检出，而不是让 w 假装生效。

    SuperRAN 的量纲下 EPF 分量中位数约 14.6、EDF 约 0.028，``epf_scale=1.0``
    时名义 w=0.5 的实际 EDF 占比只有 0.002——混合已退化成纯 EPF。
    """
    scale = _run("qos_pf_edf", saturated=True, edf_mixed_weight=0.5).cell[
        "scheduler_mixed_component_scale"]
    assert scale["effective_edf_share"] < 0.01
    assert "warning" in scale


def test_calibrated_mixed_mode_avoids_starvation() -> None:
    """标定 ``epf_scale`` 后，混合模式拿到接近纯 EDF 的取舍但不饿死任何人。

    实测 ``epf_scale=1e-4``：Jain 0.3096（纯 EDF 是 0.3032，纯 PF 是 0.4707），
    饿死用户数 0。这就是规格 §6.4 说的缓解手段，实测有效。
    """
    run = _run("qos_pf_edf", saturated=True, edf_mixed_weight=0.5,
               edf_mixed_epf_scale=1e-4)
    scale = run.cell["scheduler_mixed_component_scale"]
    assert scale["effective_edf_share"] > 0.5      # w 这次真的生效了
    assert all(x > 0.0 for x in _served(run))      # 没有人被饿死
    pf_jain = _jain(_served(_run("pf", saturated=True)))
    edf_jain = _jain(_served(_run("edf", saturated=True)))
    assert edf_jain <= _jain(_served(run)) <= pf_jain


def test_epf_scale_monotonically_shifts_the_effective_share() -> None:
    """标定系数越小，混合越倒向 EDF——旋钮方向不能反。"""
    shares = [
        _run("qos_pf_edf", saturated=True, edf_mixed_weight=0.5,
             edf_mixed_epf_scale=s).cell[
                 "scheduler_mixed_component_scale"]["effective_edf_share"]
        for s in (1.0, 1e-2, 1e-4)
    ]
    assert shares[0] < shares[1] < shares[2]
