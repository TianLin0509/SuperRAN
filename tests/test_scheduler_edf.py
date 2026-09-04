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


def test_srb_boost_is_additive_not_mathematically_absolute() -> None:
    """相同 TBS/Buffer 下 SRB 高出恰好 5000——但那是**加性**，不是绝对优先。

    反例：``TBS/Buffer`` 只要超过加值，数据承载就压过 SRB。当前包长下够不到
    （实测逐 TTI EDF 度量上界饱和 24UE 约 143、轻载 8UE 约 239），所以运行上
    无缺陷；但"无论 Buffer/TBS 如何都排在前面"这句话是错的，要真正的绝对优先
    必须改成两级排序，调大常数只是把门槛推远。
    """
    data = sedf.edf_priority(1000.0, 100.0)
    srb = sedf.edf_priority(1000.0, 100.0, is_srb=True)
    assert srb - data == pytest.approx(sedf.SRB_PRIORITY_BOOST)
    # 常规包长下 SRB 稳压数据承载
    assert sedf.edf_priority(1.0, 100_000.0, is_srb=True) > \
        sedf.edf_priority(4000.0, 1.0)
    # 但 TBS/Buffer 超过加值时就压不住了——这是加性的必然结果
    assert sedf.edf_priority(6000.0, 1.0) > \
        sedf.edf_priority(1.0, 100_000.0, is_srb=True)


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
#: 30% PRB 利用率：真实网络的常见负载，调度器基本没得选。
# arrival_rate_hz 于 2026-09-03 由 4.0 重新标定为 3.5：下行 AMC 链修正
# （HARQ 进程占用与 rank 反馈时序）下修了可达吞吐，同样的到达率现在要占 40.7%
# 的 PRB。要保持"30% 工作点"这个场景名副其实，必须降到达率而不是放宽断言。
# 2026-09-04 再由 3.5 标定为 3.0：TBS 扣掉 DM-RS+PDCCH 开销、CQI 改成运行时
# 上报（含 1.5 dB UE 实现损失）之后，同样的到达率占到 40.0% 的 PRB。
# 同样是降到达率，不是放宽断言。
_PRB30_TRAFFIC = dict(model="mixed", small_ue_share=0.5, small_file_bytes=600,
                      small_arrival_rate_hz=100.0, file_bytes=400_000,
                      arrival_rate_hz=3.0)

_CACHE: dict[tuple, object] = {}


_SCENARIOS = {
    "light": (lambda: _LIGHT_TABLES, _LIGHT_TRAFFIC, 0.8),
    "saturated": (lambda: _SAT_TABLES, _SAT_TRAFFIC, 0.5),
    "prb30": (lambda: _SAT_TABLES, _PRB30_TRAFFIC, 0.5),
}


def _run(algorithm: str, *, saturated: bool = False, scenario: str | None = None,
         traffic=None, **sched_kwargs):
    scenario = scenario or ("saturated" if saturated else "light")
    key = (algorithm, scenario, traffic is not None,
           tuple(sorted(sched_kwargs.items())))
    if traffic is None and key in _CACHE:
        return _CACHE[key]
    tables_of, scen_traffic, duration = _SCENARIOS[scenario]
    # **HARQ 进程数在这整套对照里必须钉死。** 本文件比的是调度算法之间的差异，
    # 进程数是另一个自变量：单进程时"UE 在等自己反馈就不参与调度"本身提供了一层
    # 隐式公平——强用户被迫轮空，弱用户才排得上。放开进程数后这层公平消失，
    # 各条阈值（尤其是饿死兜底的 edf_starvation_hol_ms）都要重新标定。
    # 那条交互单独钉在 test_multiprocess_harq_weakens_the_starvation_guard 里，
    # 不在这里混着测。
    cfg = sysm.SystemConfig(
        duration_s=duration, seed=41, tdd_pattern="DDDSU",
        harq_max_processes=1)
    run = sysm.simulate(
        tables_of(),
        sys_cfg=cfg,
        traffic=traffic or sysm.TrafficConfig(**scen_traffic),
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
    """最强反例 ①：w=0 与纯 qos_pf 逐位一致。

    适用范围有两条，都由下面两个测试各自钉住：**没有 signalling 业务类**，
    且 ``qos_priority_weighting="none"``（此时 priority_factor 精确等于 1.0，
    两条表达式的浮点重结合恰好无损）。
    """
    base, mixed = _run("qos_pf"), _run("qos_pf_edf", edf_mixed_weight=0.0)
    for key in _IDENTITY_KEYS:
        assert mixed.cell[key] == base.cell[key], key


def test_mixed_weight_zero_is_not_qos_pf_when_signalling_exists() -> None:
    """SRB 加值与 w 无关，所以有 signalling 业务类时 w=0 **不**等于 qos_pf。

    这是刻意的取舍：信令的优先级不该随混合权重漂移。把加值置 0 即可恢复逐位
    一致——这条断言同时证明差异确实来自加值，而不是别的地方漏了。
    """
    traffic = sysm.TrafficConfig(model="mixed", classes=(
        # arrival_rate_hz 于 2026-09-03 由 200 重新标定为 400：AMC 链修正之后，
        # 200 Hz 这个工作点上加值带来的重排不再改变任何可观测量（小区级全部
        # 数值 KPI 与逐 UE served_mbps 都逐位相同），断言会失去分辨力。
        # 已实测确认加值本身仍然有效（400 Hz、srb_bytes=2000、data=4 Hz 三个
        # 工作点都能看到差异），所以要改的是场景而不是被断言的机制。
        sysm.TrafficClassConfig(
            name="srb", ue_share=0.25, file_bytes=200,
            arrival_rate_hz=400.0, priority=1, resource_type="signalling"),
        sysm.TrafficClassConfig(
            name="data", ue_share=0.75, file_bytes=400_000,
            arrival_rate_hz=40.0, priority=80),
    ))
    qos_pf = _run("qos_pf", scenario="saturated", traffic=traffic)
    with_boost = _run("qos_pf_edf", scenario="saturated", traffic=traffic,
                      edf_mixed_weight=0.0)
    without = _run("qos_pf_edf", scenario="saturated", traffic=traffic,
                   edf_mixed_weight=0.0, srb_priority_boost=0.0)
    assert with_boost.cell["cell_served_mbps"] != qos_pf.cell["cell_served_mbps"]
    for key in _IDENTITY_KEYS:
        assert without.cell[key] == qos_pf.cell[key], key


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


def test_srb_modelled_flips_true_when_the_boost_actually_fires() -> None:
    """声明 signalling 类后加值真的生效，结果就不能再报 srb_modelled=false。"""
    traffic = sysm.TrafficConfig(model="mixed", classes=(
        sysm.TrafficClassConfig(
            name="srb", ue_share=0.25, file_bytes=200,
            arrival_rate_hz=200.0, priority=1, resource_type="signalling"),
        sysm.TrafficClassConfig(
            name="data", ue_share=0.75, file_bytes=400_000,
            arrival_rate_hz=40.0, priority=80),
    ))
    identity = _run("edf", traffic=traffic).cell["scheduler_priority_metric"]
    assert identity["srb_modelled"] is True


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
    assert boosted.cell["scheduler_priority_metric"]["srb_modelled"] is True


def test_edf_rejects_full_buffer() -> None:
    """full_buffer 把队列钉在 2**50 B，EDF 的分母失去物理含义——硬失败。"""
    with pytest.raises(ValueError, match="有限队列"):
        _run("edf", traffic=sysm.TrafficConfig(model="full_buffer"))


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

    实测 24 UE 饱和：Jain 0.4764 → 0.2708（2026-09-03 在 AMC 链修正后的基线上
    重测；旧基线上是 0.4707 → 0.3032，修正后公平性代价更大）。
    """
    assert _jain(_served(_run("edf", saturated=True))) < \
        _jain(_served(_run("pf", saturated=True)))


def test_edf_improves_small_packet_immediate_service() -> None:
    """判据 (b)：小包的即时服务比例必须高于 PF——这是 EDF 的全部收益。

    实测 0.5235 → 0.5280。**收益只有 0.45 个百分点**：SuperRAN 的按需 RBG 分配
    早就让小包在 PF 下也基本即时服务，EDF 能额外拿到的并不多。
    2026-09-03 在 AMC 链修正后的基线上重测；旧基线上是 0.7665 → 0.7891（+2.3 pp）。
    修正之后收益缩到五分之一，而公平性代价反而更大——「纯 edf 不划算」这个结论
    在新基线上比原来更成立。单种子单场景的方向性观察，不是带置信区间的结论。
    """
    assert _run("edf", saturated=True).cell["small_immediate_service_ratio"] > \
        _run("pf", saturated=True).cell["small_immediate_service_ratio"]


def test_edf_starves_edge_users_with_large_backlog_under_saturation() -> None:
    """判据 (c)：饱和下 EDF **会**把用户饿死到 0，而 PF 不会。

    这是钉住已知代价，不是期望行为。规格 §6.4 写明了饥饿风险，实测 24 UE
    饱和时 1 个 UE 的 served_mbps 恰好为 0（2026-09-03 在 AMC 链修正后的基线上
    重测；旧基线上是 2 个）。

    **受害者不是"大包用户"，是"大缓冲 + 边缘信道"**：分子是信道相关的 TBS，
    所以 EDF 对坏信道和大积压是乘性双重惩罚，且没有 PF 的 1/R_avg 补偿。实测
    被饿死的正是全小区最差的链路，而其它同为 large 类的 UE 活得好好的
    ——下面直接断言这个因果，防止把结论误传成"大包必饿"。
    缓解手段见 :func:`test_calibrated_mixed_mode_avoids_starvation`。
    """
    edf = _run("edf", saturated=True)
    starved = [u for u in edf.users if float(u["served_mbps"]) == 0.0]
    assert starved
    # 必须是**真饥饿**：有积压却一次都没被调度过。只看 served==0 会把"根本没有
    # 业务到达"的 UE 也算进来（bursts==0 两种情况都会出现，不足以区分）。
    for u in starved:
        assert int(u["queued_bytes"]) > 0, u["ue"]
        assert int(u["sched_tti"]) == 0, u["ue"]
    # PF 在同一场景下一个都不饿死
    assert all(x > 0.0 for x in _served(_run("pf", saturated=True)))

    # 因果：受害者是**同业务类内信道最差**的那几个，不是"所有大包用户"。
    # 全网第二差的链路属小包类，活得好好的——证明惩罚需要大积压与坏信道同时成立。
    victim_class = starved[0]["traffic_class"]
    assert all(u["traffic_class"] == victim_class for u in starved)
    same_class = sorted(float(u["geo_sinr_db"]) for u in edf.users
                         if u["traffic_class"] == victim_class)
    worst_in_class = set(same_class[:len(starved)])
    assert all(float(u["geo_sinr_db"]) in worst_in_class for u in starved)
    assert any(u["traffic_class"] == victim_class
               and float(u["served_mbps"]) > 0.0 for u in edf.users),         "同类用户全灭说明是按业务类饿死，不是按信道"
    # 全网第二差的链路不属于受害者集合（它是另一个业务类）
    all_sinr = sorted(float(u["geo_sinr_db"]) for u in edf.users)
    assert all_sinr[1] not in {float(u["geo_sinr_db"]) for u in starved}


def test_uncalibrated_mixed_mode_degenerates_and_says_so() -> None:
    """蓝本原式的量纲陷阱必须被检出，而不是让 w 假装生效。

    SuperRAN 的量纲下 EPF 分量中位数约 14.6、EDF 约 0.028，``epf_scale=1.0``
    时名义 w=0.5 的实际 EDF 占比只有 0.002——混合已退化成纯 EPF。
    """
    scale = _run("qos_pf_edf", saturated=True, edf_mixed_weight=0.5).cell[
        "scheduler_mixed_component_scale"]
    assert scale["effective_edf_share"] < 0.01
    assert "warning" in scale
    # 告警不得把"数值占比小"说成"对排序毫无影响"——实测占比 0.002 时公平度
    # 仍走了纯 EPF→纯 EDF 全程的约 2.4%。
    assert "不等于它对排序毫无影响" in scale["warning"]
    assert scale["caveats"]


def test_uncalibrated_mixed_mode_still_shifts_the_schedule() -> None:
    """占比 0.002 不代表零影响：公平度确实偏离了纯 qos_pf。"""
    mixed = _run("qos_pf_edf", saturated=True, edf_mixed_weight=0.5)
    pure = _run("qos_pf", saturated=True)
    assert _jain(_served(mixed)) != _jain(_served(pure))


def test_epf_scale_is_per_operating_point_not_a_constant() -> None:
    """同一个 epf_scale 在轻载与饱和下相差一个数量级以上——不能当常数搬走。

    2026-09-03 重新标定。原断言写的是 ``sat_1 < 0.01 < 0.3 < light_1``，
    附注"轻载下却接近平衡"。下行 AMC 链修正之后**这句话不再成立**：轻载实测
    只有 0.0246，离"平衡"很远。被证伪的是那句措辞，不是本测试要守的结论——
    "epf_scale 不是常数、必须按工作点标定"仍然成立，而且差距足有 20 倍。
    """
    sat_1 = _run("qos_pf_edf", saturated=True, edf_mixed_weight=0.5).cell[
        "scheduler_mixed_component_scale"]["effective_edf_share"]
    light_1 = _run("qos_pf_edf", edf_mixed_weight=0.5).cell[
        "scheduler_mixed_component_scale"]["effective_edf_share"]
    # s=1.0 实测：饱和 0.001195（几乎全 EPF），轻载 0.024647（仍以 EPF 为主，
    # 但 EDF 分量的占比高了一个数量级以上）。断言锚在"数量级差异"而不是绝对值，
    # 这样它守的是机制而不是某一次仿真的小数点。
    assert sat_1 < 0.01, sat_1
    assert light_1 > 10.0 * sat_1, (sat_1, light_1)


def test_calibrated_mixed_mode_avoids_starvation() -> None:
    """标定 ``epf_scale`` 后，混合模式拿到接近纯 EDF 的取舍但不饿死任何人。

    实测 ``epf_scale=1e-4``：Jain 0.2788（纯 EDF 是 0.2708，纯 PF 是 0.4764），
    饿死用户数 0。这就是规格 §6.4 说的缓解手段，实测有效。
    2026-09-03 在 AMC 链修正后的基线上重测；旧基线上依次是 0.3096 / 0.3032 / 0.4707。
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

# ---------------------------------------------------------------------------
# 饥饿保护：硬时延兜底 vs 软 EPF 分量
# ---------------------------------------------------------------------------
def test_starvation_guard_lifts_only_starved_and_orders_by_wait() -> None:
    """内核语义：饥饿者整体抬到未饥饿者之上，组内按等待降序。"""
    metric = np.array([9.0, 0.1, 0.2, 5.0])
    hol = np.array([1.0, 300.0, 500.0, 2.0])
    out = sedf.apply_starvation_guard(metric, hol, threshold_ms=100.0)
    assert out[0] == 9.0 and out[3] == 5.0          # 未饥饿者原值不动
    assert out[1] > 9.0 and out[2] > out[1]          # 饥饿者抬升且等待久的更高
    assert np.argmax(out) == 2                       # 等待最久的排第一


def test_starvation_guard_is_a_noop_when_nobody_waits_too_long() -> None:
    metric = np.array([3.0, 1.0])
    out = sedf.apply_starvation_guard(
        metric, np.array([5.0, 9.0]), threshold_ms=100.0)
    assert out == pytest.approx(metric)


def test_starvation_guard_handles_all_starved() -> None:
    """全员饥饿时基准取 0，仍然按等待降序。"""
    out = sedf.apply_starvation_guard(
        np.array([1.0, 2.0]), np.array([300.0, 900.0]), threshold_ms=100.0)
    assert out[1] > out[0]


def test_starvation_guard_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        sedf.apply_starvation_guard(np.ones(2), np.ones(3), threshold_ms=10.0)
    with pytest.raises(ValueError):
        sedf.apply_starvation_guard(np.ones(2), np.ones(2), threshold_ms=0.0)


def test_starvation_guard_off_by_default_is_bit_identical() -> None:
    """默认关闭必须与不带兜底的实现逐位相同。"""
    default = _run("edf", saturated=True)
    explicit_off = _run("edf", saturated=True, edf_starvation_hol_ms=None)
    for key in _IDENTITY_KEYS:
        assert explicit_off.cell[key] == default.cell[key], key
    assert "scheduler_starvation_lifts" not in default.cell


def test_multiprocess_harq_weakens_the_starvation_guard() -> None:
    """**单进程 HARQ 在做一件调度器没在做的事：强制轮空。**

    单进程下 UE 发完一个 TB 就要等反馈，这期间不参与调度——等于给了弱用户
    一个天然的插空机会。放开到 8 进程后强用户不再轮空，24 UE 饱和小区里
    最弱那个（geo SINR −0.74 dB）即使被饿死兜底抬进候选集 5000 多次，
    仍然抢不到足够的 RBG，实测 served = 0。

    **这不是 bug，是把调度器的真实公平性暴露出来了**：以前的"不饿死"有一部分
    是 HARQ 阻塞白送的。阈值 200 ms 是在旧行为上标定的，需要维护者重新决定。
    这条用例把这个交互钉住，免得以后当成回归去修。
    """
    def _run_mp(procs: int):
        tables_of, scen_traffic, duration = _SCENARIOS["saturated"]
        return sysm.simulate(
            tables_of(),
            sys_cfg=sysm.SystemConfig(
                duration_s=duration, seed=41,
                tdd_pattern="DDDSU", harq_max_processes=procs),
            traffic=sysm.TrafficConfig(**scen_traffic),
            sched=sysm.SchedulerConfig(algorithm="edf", mu_enabled=False,
                                       olla_enabled=False,
                                       edf_starvation_hol_ms=200.0),
            kpi=sysm.KpiConfig(warmup_tti=0))

    single = _run_mp(1)
    multi = _run_mp(8)
    starved_single = sum(1 for x in _served(single) if x <= 0.0)
    starved_multi = sum(1 for x in _served(multi) if x <= 0.0)
    assert starved_single == 0, starved_single
    assert starved_multi > 0, starved_multi
    # 兜底确实在动，不是没触发
    assert multi.cell["scheduler_starvation_lifts"]["lifted_candidate_ttis"] > 0


def test_hard_starvation_guard_eliminates_starvation() -> None:
    """兜底确实消灭饥饿：24 UE 饱和下饿死用户 2 → 0。"""
    guarded = _run("edf", saturated=True, edf_starvation_hol_ms=200.0)
    assert all(x > 0.0 for x in _served(guarded))
    assert guarded.cell["scheduler_starvation_lifts"][
        "lifted_candidate_ttis"] > 0


def test_hard_starvation_guard_costs_throughput_and_small_packet_service() -> None:
    """兜底的代价必须被钉住，不能只宣传它消灭了饥饿。

    实测：小区吞吐 423.1 → 322.3 Mbps（−23.8%），小包即时服务 0.5280 → 0.5161，
    **比 PF 基线的 0.5235 还低**。原因是硬兜底是字典序绝对优先，比 PF 连续的
    1/r_avg 加权更钝，而被抬升的正是信道差、传同样字节要占更多 RBG 的用户。
    2026-09-03 在下行 AMC 链修正后的基线上重测；旧基线上是 559.0 → 401.5 Mbps
    （−28%）、0.7891 → 0.6632、PF 基线 0.7665。方向与结论不变。
    """
    plain = _run("edf", saturated=True)
    guarded = _run("edf", saturated=True, edf_starvation_hol_ms=200.0)
    pf = _run("pf", saturated=True)
    assert guarded.cell["cell_served_mbps"] < plain.cell["cell_served_mbps"]
    assert guarded.cell["small_immediate_service_ratio"] < \
        plain.cell["small_immediate_service_ratio"]
    assert guarded.cell["small_immediate_service_ratio"] < \
        pf.cell["small_immediate_service_ratio"]


def test_calibrated_mixed_mode_dominates_the_hard_guard() -> None:
    """关键结论：抗饥饿该用 EPF 分量，不该外挂时延门限。

    EPF 的 1/r_avg 是**连续自纠正**负反馈——越饿 r_avg 越小、优先级越高，
    没有悬崖；EDF 的 1/Buffer 是反纠正——越饿积压越大、优先级越低。所以标定后的
    混合模式在**每一个轴上**都优于硬兜底：零饿死、吞吐 429.2 vs 322.3 Mbps、
    小包即时 0.5229 vs 0.5161。
    2026-09-03 在下行 AMC 链修正后的基线上重测；旧基线上是 579.7 vs 401.5 Mbps、
    0.7807 vs 0.6632。方向与结论不变。
    """
    soft = _run("qos_pf_edf", saturated=True, edf_mixed_weight=0.5,
                edf_mixed_epf_scale=1e-4)
    hard = _run("edf", saturated=True, edf_starvation_hol_ms=200.0)
    assert all(x > 0.0 for x in _served(soft))                    # 同样零饿死
    assert soft.cell["cell_served_mbps"] > hard.cell["cell_served_mbps"]
    assert soft.cell["small_immediate_service_ratio"] > \
        hard.cell["small_immediate_service_ratio"]


# ---------------------------------------------------------------------------
# 30% PRB 利用率：真实网络常见负载下调度器还剩多少差异
# ---------------------------------------------------------------------------
def test_thirty_percent_prb_operating_point_is_actually_thirty_percent() -> None:
    """先证明这个工作点确实在 30% 附近，否则下面的结论不成立。"""
    util = _run("pf", scenario="prb30").cell["serving_cell_prb_utilization"]
    assert 0.25 <= util <= 0.35


def test_at_thirty_percent_prb_all_schedulers_deliver_the_same_throughput() -> None:
    """30% PRB 下没有竞争，调度器选谁都一样——小区吞吐完全相同。

    这是判断"要不要上 EDF"的关键：它只在拥塞时才有意义。
    """
    served = {
        alg: _run(alg, scenario="prb30").cell["cell_served_mbps"]
        for alg in ("pf", "qos_pf", "edf", "max_ci")
    }
    assert len(set(served.values())) == 1, served


def test_at_thirty_percent_prb_edf_still_helps_small_packets_slightly() -> None:
    """吞吐一样，但小包即时服务仍有可测的方向性差异（0.7243 → 0.7380）。

    2026-09-03 随 prb30 话务重新标定一并重测；旧基线上是 0.8101 → 0.8303。
    """
    assert _run("edf", scenario="prb30").cell["small_immediate_service_ratio"] > \
        _run("pf", scenario="prb30").cell["small_immediate_service_ratio"]


def test_at_thirty_percent_prb_nobody_is_starved_by_the_scheduler() -> None:
    """30% PRB 下 EDF 不饿死任何人；零吞吐的 UE 必须是零到达，不是被饿死。"""
    run = _run("edf", scenario="prb30")
    for user, served in zip(run.users, _served(run), strict=True):
        if served > 0.0:
            continue
        # bursts==0 不足以判"没有业务"——真被饿死的 UE 同样是 bursts==0
        # （饱和场景实测：积压 4 MB、sched_tti=0、bursts=0）。必须看积压。
        assert int(user["queued_bytes"]) == 0, user["ue"]

# ---------------------------------------------------------------------------
# 交付契约：结果里必须留下"这次到底用了什么参数"
#
# 上面的集成测试断言的都是 sysm.simulate(...) 返回的 SystemResult，而 MCP 的
# sr_system_sim 返回的是 ReplicationResult.as_dict()。两者不是同一个契约：
# summarize_runs 只保留数值型 KPI，结构化诊断会被整条丢掉。这一节专门盯真正
# 交付出去的那个对象，避免"测试全绿但交付缺字段"。
# ---------------------------------------------------------------------------
def test_scheduler_config_echo_carries_the_edf_knobs() -> None:
    """改了会让数字变的参数必须跟着结果一起走。

    少了它们，kpi_compare 会把 w=0（纯 qos_pf）和 w=1（纯 edf）这两个调度行为
    完全不同的臂报成"配置无差异"。
    """
    left = sysm.SchedulerConfig(
        algorithm="qos_pf_edf", edf_mixed_weight=0.0,
        edf_mixed_epf_scale=1.0, srb_priority_boost=5000.0).as_dict()
    right = sysm.SchedulerConfig(
        algorithm="qos_pf_edf", edf_mixed_weight=1.0,
        edf_mixed_epf_scale=1e-4, srb_priority_boost=0.0,
        edf_starvation_hol_ms=200.0).as_dict()
    for key in ("edf_mixed_weight", "edf_mixed_epf_scale",
                "srb_priority_boost", "edf_starvation_hol_ms"):
        assert key in left, key
        assert left[key] != right[key], key


def test_replication_result_delivers_the_scheduler_identity() -> None:
    """MCP 实际返回的对象里必须能读到调度器身份与混合分量量级。

    ``experience.py`` 的 notes 明确指着 ``scheduler_mixed_component_scale``；
    如果它到不了 ``as_dict()``，那就是个悬空指针。
    """
    res = sysm.simulate_replications(
        _SAT_TABLES,
        sys_cfg=sysm.SystemConfig(duration_s=0.5,
                                  seed=41, tdd_pattern="DDDSU"),
        traffic=sysm.TrafficConfig(**_SAT_TRAFFIC),
        sched=sysm.SchedulerConfig(algorithm="qos_pf_edf", mu_enabled=False,
                                   olla_enabled=False, edf_mixed_weight=0.5),
        kpi=sysm.KpiConfig(warmup_tti=0), num_replications=2)
    out = res.as_dict()
    sched = out.get("scheduler") or {}
    assert sched.get("scheduler_priority_metric", {}).get("algorithm") == \
        "qos_pf_edf"
    scale = sched.get("scheduler_mixed_component_scale") or {}
    assert scale.get("effective_edf_share") is not None
    # 配置回显也要在同一个交付对象里
    assert out["config"]["scheduler"]["edf_mixed_weight"] == 0.5


if __name__ == "__main__":
    # run_test_matrix.py 用 `python tests/<file>.py` 跑每个文件。没有这个入口，
    # pytest 式文件会「什么都不做地退出 0」，在矩阵里表现为假通过。
    # 见 .agents/TESTING.md 的坑 2。
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
