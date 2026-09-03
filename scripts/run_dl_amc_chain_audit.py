"""下行 AMC 链改动的物理反向对照。

每一项都跑**同数据集、同随机流**的成对实验，只改一个开关，用来回答两个问题：
这次改动到底改了什么量、以及关掉它能不能逐位回到旧行为。

**这不是性能结论。** 它只证明机制接通、方向可解释、反向对照成立；任何百分比
都要按 `skills/channel-sim` 的门 3 重新走统计判决才能对外说。

    python scripts/run_dl_amc_chain_audit.py
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import amc_policy as ap  # noqa: E402
from superran import csi_aging as ca  # noqa: E402
from superran import experience as ex  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import rng as rg  # noqa: E402
from superran import scheduler_mu as smu  # noqa: E402
from superran import system as sy  # noqa: E402

OUT = ROOT / "artifacts" / "results" / "dl_amc_chain_audit.json"


def _channels(n_ue: int = 4, n_snap: int = 8, seed: int = 20260902):
    rng = np.random.default_rng(seed)
    return [
        ((rng.standard_normal((n_snap, 24, 16, 4))
          + 1j * rng.standard_normal((n_snap, 24, 16, 4))) / np.sqrt(2))
        for _ in range(n_ue)
    ]


def _tables(csi: ca.CsiConfig | None = None, **kwargs):
    return sy.build_link_tables(
        _channels(), [12.0, 7.0, 2.0, -3.0], csi=csi, **kwargs)


def _run(tables, *, sched: sy.SchedulerConfig, sys_cfg: sy.SystemConfig,
         seed: int = 4242):
    return sy.simulate(
        tables, sys_cfg=sys_cfg,
        traffic=sy.TrafficConfig(model="full_buffer"),
        sched=sched, kpi=sy.KpiConfig(warmup_tti=0, tti_trace_mode="off"),
        rng=rg.RngBook(seed, 0))


def _cell(run) -> dict[str, float]:
    c = run.cell
    return {
        "avg_mcs": round(float(c["avg_mcs"]), 4),
        "avg_mcs_first_tx": round(float(c["avg_mcs_first_tx"]), 4),
        "avg_rank": round(float(c["avg_rank"]), 4),
        "bler_first_tx": round(float(c["bler_first_tx"]), 5),
        "olla_mcs_mean": round(float(c["olla_mcs_mean"]), 4),
        "cell_served_mbps": round(float(c["cell_served_mbps"]), 3),
    }


def experiment_rank_stability() -> dict:
    """固定 rank2 与逐快照跟随 best_rank 的对照。"""
    tables = _tables()
    cfg = sy.SystemConfig(evaluation_mode="experience", duration_s=1.0,
                          tdd_pattern="DDDSU", seed=77)
    arms = {}
    for name, rank_cfg in (
        ("fixed_rank2", ap.RankConfig(mode="fixed", fixed_rank=2)),
        ("legacy_per_snapshot", ap.RankConfig(mode="link_table")),
    ):
        run = _run(tables, sched=sy.SchedulerConfig(
            mu_enabled=False, rank=rank_cfg), sys_cfg=cfg)
        arms[name] = _cell(run)
    ranks = [int(t.best_rank[s]) for t in tables for s in range(t.best_rank.size)]
    switches = sum(
        int(t.best_rank[s] != t.best_rank[s - 1])
        for t in tables for s in range(1, t.best_rank.size))
    assert arms["fixed_rank2"]["avg_rank"] == 2.0
    assert arms["legacy_per_snapshot"]["avg_rank"] != 2.0
    return {
        "question": "固定 rank 与逐快照跟随 best_rank 各自给出什么",
        "arms": arms,
        "link_table_best_rank_values": sorted(set(ranks)),
        "link_table_rank_changes_between_snapshots": switches,
        "interpretation": (
            "历史模式下 best_rank 在快照之间确实会变；固定模式把这个自由度关掉。"
            "两条轨迹的 avg_rank 不同，因此它们不是同一个实验，"
            "**不能拿其中一条的历史数字解释另一条**。"),
        "not_a_conclusion": (
            "两臂吞吐差不是 rank 策略的收益：本审计只跑一次重复、没有配对检验。"),
    }


def _expected_base_tx(table, alloc) -> float:
    """这条 grant 的决策坐标应当是什么：频选路径取被授 RBG 的子集均值。

    容差按 ``Allocation.as_dict`` 的 4 位小数序列化取 5e-4，不是数值容差。
    """
    snap, rank = int(alloc["snapshot"]), int(alloc["rank"])
    indices = [int(x) for x in alloc["rbg_indices"]]
    rows = table.sinr_tx_rbg_db
    if rows is not None and indices and max(indices) < int(rows.shape[-1]):
        return float(np.mean(np.asarray(rows[snap, rank - 1])[indices]))
    return float(table.sinr_tx_db[snap, rank - 1])


def experiment_olla_coordinate() -> dict:
    """关掉 OLLA 时决策坐标是否仍是 CQI+BF。"""
    tables = _tables()
    cfg = sy.SystemConfig(evaluation_mode="experience", duration_s=0.5,
                          tdd_pattern="DDDSU", seed=31)
    rows = {}
    for name, olla in (("olla_on", True), ("olla_off", False)):
        run = _run(tables, sched=sy.SchedulerConfig(
            mu_enabled=False, olla_enabled=olla), sys_cfg=cfg)
        sample = run.diagnostics["allocation_sample"]
        newtx = [a for a in sample if a["harq_tx_mode"] == "newtx"]
        rows[name] = {
            "cell": _cell(run),
            "base_tx_sinr_db_head": [a["base_tx_sinr_db"] for a in newtx[:4]],
            "matches_link_table_sinr_tx": all(
                abs(a["base_tx_sinr_db"]
                    - _expected_base_tx(tables[a["ue"]], a)) < 5e-4
                for a in newtx),
            "equals_true_rx_sinr": all(
                abs(a["base_tx_sinr_db"] - a["sinr_db"]) < 5e-4
                for a in newtx),
        }
    assert rows["olla_off"]["matches_link_table_sinr_tx"]
    assert not rows["olla_off"]["equals_true_rx_sinr"]
    return {
        "question": "olla_enabled=False 时发送决策看的是预测坐标还是真值",
        "arms": rows,
        "interpretation": (
            "两臂的 base_tx_sinr_db 都逐条等于链路表的 sinr_tx_db，"
            "且都不等于真实接收 SINR。关掉 OLLA 只去掉偏置叠加这一步。"),
        "regression_guard": (
            "改回旧行为时 olla_off 臂的 equals_true_rx_sinr 会变成 true，"
            "同时 bler_first_tx 会被构造到目标值附近。"),
    }


def experiment_feedback_delay() -> dict:
    """反馈时延开/关的对照，以及零时延反向对照。"""
    tables = _tables()
    arms = {}
    for name, delay in (("delay_on", True), ("delay_off_control", False)):
        cfg = sy.SystemConfig(
            evaluation_mode="experience", duration_s=1.0, tdd_pattern="DDDSU",
            seed=88, harq_feedback_delay=delay)
        run = _run(tables, sched=sy.SchedulerConfig(mu_enabled=False),
                   sys_cfg=cfg)
        arms[name] = {
            "cell": _cell(run),
            "harq_feedback_wait_skips": int(
                run.cell["harq_feedback_wait_skips"]),
            "offsets_tti": run.diagnostics["harq_feedback"][
                "effective_offsets_tti"],
            "delay_modelled": run.diagnostics["harq_feedback"][
                "delay_modelled"],
        }
    assert arms["delay_on"]["offsets_tti"] == [5, 4, 3, 2, 6]
    assert arms["delay_off_control"]["offsets_tti"] == [1, 1, 1, 1, 1]
    assert arms["delay_on"]["harq_feedback_wait_skips"] > 0
    assert arms["delay_off_control"]["harq_feedback_wait_skips"] == 0

    # Strong ACK counterexample: with one UE and forced ACK, old code scheduled
    # all eight D/S opportunities because it created pending state only on NACK.
    # The single-process contract permits only t0 and t5 in ten DDDSU TTIs.
    one = _tables()[:1]
    old_system_bler, old_experience_bler = sy._bler_lookup, ex._bler_lookup
    ack_runs = {}
    try:
        sy._bler_lookup = lambda _mcs, _sinr: 0.0
        ex._bler_lookup = lambda _mcs, _sinr: 0.0
        for mode in ("capacity", "experience"):
            result = _run(
                one, sched=sy.SchedulerConfig(mu_enabled=False),
                sys_cfg=sy.SystemConfig(
                    evaluation_mode=mode, duration_s=0.005,
                    tdd_pattern="DDDSU", seed=650))
            ack_runs[mode] = {
                "scheduled_tti": int(result.cell["scheduled_tti"]),
                "feedback_wait_skips": int(
                    result.cell["harq_feedback_wait_skips"]),
            }
    finally:
        sy._bler_lookup, ex._bler_lookup = old_system_bler, old_experience_bler
    assert all(row == {"scheduled_tti": 2, "feedback_wait_skips": 6}
               for row in ack_runs.values())

    # Terminal retransmission ACK/NACK also stays in flight until feedback.
    # It only releases the process: no second OLLA/rank vote and no third TX.
    terminal_runs = {}
    old_system_bler, old_experience_bler = sy._bler_lookup, ex._bler_lookup
    old_retx_bler = la.harq_retransmission_bler
    try:
        sy._bler_lookup = lambda _mcs, _sinr: 1.0
        ex._bler_lookup = lambda _mcs, _sinr: 1.0
        for terminal_ack in (True, False):
            terminal_bler = 0.0 if terminal_ack else 1.0

            def terminal_retx(mcs, sinr, *, combining="ir", table=3,
                              _bler=terminal_bler):
                return {
                    "bler": float(_bler), "lookup_mcs": int(mcs),
                    "lookup_sinr_db": float(sinr),
                    "combining": str(combining), "table": int(table),
                }

            la.harq_retransmission_bler = terminal_retx
            for mode in ("capacity", "experience"):
                result = _run(
                    one, sched=sy.SchedulerConfig(mu_enabled=False),
                    sys_cfg=sy.SystemConfig(
                        evaluation_mode=mode, duration_s=0.006,
                        tdd_pattern="DDDSU", seed=651))
                key = f"{mode}/retx_{'ack' if terminal_ack else 'nack'}"
                terminal_runs[key] = {
                    "scheduled_tti": int(result.cell["scheduled_tti"]),
                    "feedback_wait_skips": int(
                        result.cell["harq_feedback_wait_skips"]),
                    "olla_mcs_mean": round(
                        float(result.cell["olla_mcs_mean"]), 6),
                    "experience_timeline": (
                        [(row["tti"], row["harq_tx_mode"])
                         for row in result.diagnostics["allocation_sample"]]
                        if mode == "experience" else None),
                }
    finally:
        sy._bler_lookup, ex._bler_lookup = old_system_bler, old_experience_bler
        la.harq_retransmission_bler = old_retx_bler
    assert all(row["scheduled_tti"] == 3
               and row["feedback_wait_skips"] == 7
               and abs(row["olla_mcs_mean"] + 0.09) < 1e-12
               for row in terminal_runs.values())
    assert all(
        row["experience_timeline"] in (
            None, [(0, "newtx"), (5, "retx"), (10, "newtx")])
        for row in terminal_runs.values())

    # Delayed NACK must not trip the rank monitor at send time.
    ctl = ap.RankController(
        ap.RankConfig(
            mode="adaptive", fixed_rank=1, period_tti=30,
            min_filter_samples=1, se_filter_beta=1.0,
            min_mcs_threshold=0, resource_cost_ratio=(1.0, 1.0),
            gain_factor_raise=1.1, fallback_enabled=True,
            quick_fallback_nack_thld=1, quick_fallback_window_tti=20,
            quick_fallback_min_sched=1),
        1, tti_ms=0.5, max_rank_available=2)
    ctl.observe_link(0, 0, [1.0, 2.0], [20, 20])
    ctl._last_judge_tti[0] = -29
    olla = np.zeros(1)
    mu_olla = np.zeros(1)
    ctl.step(1, olla_by_ue=olla)
    delayed = [
        ap.FirstTxFeedback(0, False, 20, 2, 0.0, 1, 5, False, -0.09),
        ap.FirstTxFeedback(0, False, 20, 2, 0.0, 6, 10, False, -0.09),
    ]
    ranks_before_feedback = []
    for tti in range(2, 5):
        ctl.step(tti, olla_by_ue=olla)
        ranks_before_feedback.append(ctl.rank_of(0))
    delayed[0].apply(
        rank_controller=ctl, su_olla=olla, mu_olla=mu_olla,
        olla_min=-20.0, olla_max=3.0)
    ctl.step(5, olla_by_ue=olla)
    for tti in range(6, 10):
        ctl.step(tti, olla_by_ue=olla)
        ranks_before_feedback.append(ctl.rank_of(0))
    delayed[1].apply(
        rank_controller=ctl, su_olla=olla, mu_olla=mu_olla,
        olla_min=-20.0, olla_max=3.0)
    ctl.step(10, olla_by_ue=olla)
    assert ranks_before_feedback and set(ranks_before_feedback) == {2}
    assert ctl.rank_of(0) == 1
    assert ctl.diagnostics()["events"][-1]["tti"] == 10
    return {
        "question": "ACK/NACK 等上行时隙这件事有没有真的进入调度与 OLLA",
        "arms": arms,
        "forced_ack_single_process": ack_runs,
        "terminal_retransmission_feedback": terminal_runs,
        "rank_nack_feedback_counterexample": {
            "rank_before_feedback": ranks_before_feedback,
            "fallback_tti": 10,
            "final_rank": ctl.rank_of(0),
        },
        "interpretation": (
            "开启时 DDDSU 逐相位偏移 5/4/3/2 个 TTI，并确实产生等待反馈而"
            "不参与调度的 TTI；首传 ACK 与 NACK 都占住单进程，只有反馈到达时"
            "才交给 OLLA/rank。终次重传 ACK/NACK 也等反馈后才释放进程，但不再"
            "进入 OLLA/rank、也不触发更多重传。关闭时偏移全为 1、等待计数为 0。"),
        "not_modelled": "k1/k2 取值、PUCCH 资源、并行 HARQ 进程。",
    }


def experiment_cqi_filter() -> dict:
    """CQI 一阶 IIR 对阶跃的响应，以及 lambda=1 的反向对照。"""
    step_one = np.zeros((1, 17, 4, 2), dtype=complex)
    step_one[0, :, 0, 0] = 1.0
    step_one[0, :, 1, 1] = 0.7
    channels = [step_one.copy() for _ in range(8)]
    geo = [6.0] * 4 + [24.0] * 4
    out = {}
    for name, lam in (("lambda_1_no_filter", 1.0), ("lambda_0p25", 0.25)):
        table = sy.build_link_tables(
            channels, geo, num_ues=1, snapshot_ms=5.0, max_rank=2,
            rb_per_rbg=1, power_constraint="ebf",
            csi=ca.CsiConfig(enabled=False, csi_report_period_ms=5.0,
                             cqi_filter_lambda=lam))[0]
        out[name] = [int(x) for x in
                     table.reported_cqi_codepoint_per_snapshot[:, 0]]
    assert out["lambda_1_no_filter"][4] == out["lambda_1_no_filter"][7]
    assert out["lambda_0p25"][4] < out["lambda_1_no_filter"][4]
    return {
        "question": "把累计平均换成一阶 IIR 之后，CQI 对阶跃的跟踪快多少",
        "scenario": "几何 SINR 前 4 个快照 6 dB、后 4 个 24 dB，CSI 上报每快照更新",
        "reported_cqi_codepoint_by_snapshot": out,
        "interpretation": (
            "lambda=1 一步到位，是无滤波上界；lambda=0.25 逐步逼近，"
            "时间常数约 4 个上报周期。旧的 expanding mean 记忆无限长，"
            "阶跃后要很多个周期才追上，且越晚发生的阶跃追得越慢。"),
        "calibration_status": (
            "lambda=0.25 已由负责人确认为当前工程默认，但尚未经现场测量/设备"
            "数据标定；不得表述成现场等价。"),
    }


def experiment_grant_decode_sinr() -> dict:
    """部分授权时解码 SINR 取全带均值 vs 取被授 RBG 的差别。"""
    strong = np.full(8, 26.0)
    weak = np.full(9, -4.0)
    per_rbg = np.concatenate([strong, weak])
    wideband = float(np.mean(per_rbg))
    mcs = 15
    curve_wideband = float(
        la.bler_curve(mcs, sinr_db=wideband)["query"]["bler"][0])
    rows = []
    for count in (1, 2, 4, 8, 17):
        granted = per_rbg[:count]
        value = float(np.mean(granted))
        rows.append({
            "granted_rbg": count,
            "granted_sinr_db": round(value, 4),
            "wideband_sinr_db": round(wideband, 4),
            "bler_on_granted": round(
                float(la.bler_curve(mcs, sinr_db=value)["query"]["bler"][0]), 6),
            "bler_on_wideband": round(curve_wideband, 6),
        })
    assert rows[0]["bler_on_granted"] < rows[0]["bler_on_wideband"]
    assert rows[-1]["granted_sinr_db"] == round(wideband, 4)
    return {
        "question": "小包按全带均值判误块会错多少",
        "mcs": mcs,
        "scenario": "前 8 个 RBG 26 dB、后 9 个 -4 dB 的强频选信道",
        "rows": rows,
        "interpretation": (
            "只授前几个强 RBG 时，真实误块概率远低于按全带均值算出来的值；"
            "全带授权时两者按定义相同。方向相反的情形（只授弱 RBG）同理会被低估。"),
    }


def experiment_table3_migration() -> dict:
    """Breaking migration: system link tables reject Table 1/2 at the boundary."""
    h = np.ones((1, 4, 2, 2), dtype=complex)
    rejected = {}
    for table in (1, 2):
        try:
            sy.build_link_tables([h], [10.0], table=table)
        except ValueError as exc:
            rejected[str(table)] = str(exc)
    accepted = sy.build_link_tables([h], [10.0], table=3)
    assert set(rejected) == {"1", "2"}
    assert int(accepted[0].mcs_table) == 3
    return {
        "question": "旧 build_link_tables(table=1/2) 调用怎样迁移",
        "rejected": rejected,
        "accepted_table": int(accepted[0].mcs_table),
        "migration": (
            "系统/体验调用改用 table=3；Table 1/2 只保留给显式链路级分析。"
            "失败前移到建表入口，避免先产生一张看似可用的错口径系统表。"),
    }


def _mu_tables(corr: float, n_snap: int = 6, seed: int = 20260902):
    """两个 UE，空间相关系数可控；corr→1 时 ZF 没有零陷空间。"""
    rng = np.random.default_rng(seed)
    a = ((rng.standard_normal((n_snap, 272, 16, 4))
          + 1j * rng.standard_normal((n_snap, 272, 16, 4))) / np.sqrt(2))
    e = ((rng.standard_normal((n_snap, 272, 16, 4))
          + 1j * rng.standard_normal((n_snap, 272, 16, 4))) / np.sqrt(2))
    b = corr * a + np.sqrt(max(1.0 - corr ** 2, 0.0)) * e
    return sy.build_link_tables(
        [a, b], [14.0, 12.0], max_rank=2, rb_per_rbg=16, mu_enabled=True,
        csi=ca.CsiConfig(enabled=False))


def experiment_capacity_mu_accounting() -> dict:
    """capacity 开 MU 时，配对的代价进了哪几处。"""
    cfg = sy.SystemConfig(evaluation_mode="capacity", duration_s=0.6,
                          tdd_pattern="DDDSU", seed=4242)

    def run(tables, *, mu_on, accounting, ratio=1.0):
        return sy.simulate(
            tables, sys_cfg=cfg,
            traffic=sy.TrafficConfig(model="full_buffer"),
            sched=sy.SchedulerConfig(mu_enabled=mu_on,
                                     mu_accounting=accounting),
            kpi=sy.KpiConfig(warmup_tti=0, tti_trace_mode="off"),
            mu_se_ratio=ratio, rng=rg.RngBook(4242, 0))

    indep = _mu_tables(0.0)
    pair_graph = smu.validate_pair_graph(indep)
    su = run(indep, mu_on=False, accounting="pair_table")
    pair = run(indep, mu_on=True, accounting="pair_table")
    corr = run(_mu_tables(0.999), mu_on=True, accounting="pair_table")
    # 历史标量口径要用不含 pair 数据的表，与它当年的输入一致
    legacy_tabs = sy.build_link_tables(
        [indep[0].h_true_rbg, indep[1].h_true_rbg], [14.0, 12.0],
        max_rank=2, rb_per_rbg=16, mu_enabled=False,
        csi=ca.CsiConfig(enabled=False))
    legacy = run(legacy_tabs, mu_on=True,
                 accounting="se_ratio_legacy", ratio=1.4)
    legacy_su = run(legacy_tabs, mu_on=False, accounting="pair_table")

    link = indep[0].mu_links[1]
    delta_db = float(np.mean(link.true_sinr_db
                             - np.column_stack((indep[0].sinr_db[:, 1],
                                                indep[1].sinr_db[:, 1]))))
    shift = link.corr_loss_tx_db + link.power_loss_db
    identity_ok = bool(np.allclose(
        shift,
        link.predicted_sinr_db
        - (link.predicted_sinr_db - link.corr_loss_tx_db - link.power_loss_db),
        atol=1e-9))

    # Three-UE complete-graph negative control.  Each UE still has a neighbour
    # after removing 1<->2, so the old len(mu_links)>=1 check would pass.
    graph_rng = np.random.default_rng(20260903)
    graph_h = [((graph_rng.standard_normal((2, 32, 8, 2))
                + 1j * graph_rng.standard_normal((2, 32, 8, 2))) / np.sqrt(2))
               for _ in range(3)]
    graph_tables = sy.build_link_tables(
        graph_h, [12.0, 11.0, 10.0], max_rank=2, rb_per_rbg=16,
        mu_enabled=True, csi=ca.CsiConfig(enabled=False))
    broken = deepcopy(graph_tables)
    del broken[1].mu_links[2]
    del broken[2].mu_links[1]
    graph_rejection = ""
    try:
        smu.validate_pair_graph(broken)
    except ValueError as exc:
        graph_rejection = str(exc)
    assert "UE 1 缺边 [2]" in graph_rejection

    # Positive OLLA counterexample: the pre-OLLA MCS passes a step BLER model,
    # then one MU ACK adds +3 MCS.  Admission must use that final sending MCS and
    # reject the next pair when predicted BLER crosses 0.5.
    pair01 = indep[0].mu_links[1]
    base_mcs = []
    for user in (0, 1):
        side = pair01.side(user)
        predicted = (float(indep[user].sinr_tx_db[0, 1])
                     + float(pair01.corr_loss_tx_db[0, side])
                     + float(pair01.power_loss_db))
        base_mcs.append(int(la.select_mcs(
            predicted, table=3, target_bler=0.1).index))
    bler_step_mcs = max(base_mcs) + 1
    assert bler_step_mcs <= 27
    old_lookup = sy._bler_lookup
    try:
        sy._bler_lookup = lambda mcs, _sinr: (
            0.9 if int(mcs) >= bler_step_mcs else 0.0)
        olla_admission = sy.simulate(
            indep,
            sys_cfg=sy.SystemConfig(
                evaluation_mode="capacity", duration_s=0.01,
                tdd_pattern="DDDSU", seed=313),
            traffic=sy.TrafficConfig(model="full_buffer"),
            sched=sy.SchedulerConfig(
                mu_enabled=True, mu_accounting="pair_table",
                mu_corr_threshold=1.0, mu_olla_step_up_db=3.0,
                olla_max_db=6.0),
            rng=rg.RngBook(313, 0))
    finally:
        sy._bler_lookup = old_lookup
    assert olla_admission.cell["mu_share"] > 0
    assert olla_admission.cell["mu_pair_rejects"] > 0
    assert pair.cell["avg_mcs_first_tx"] < su.cell["avg_mcs_first_tx"]
    assert abs(legacy.cell["avg_mcs_first_tx"]
               - legacy_su.cell["avg_mcs_first_tx"]) < 0.5
    assert corr.cell["mu_share"] < 0.05
    return {
        "question": "capacity 开 MU 之后，配对的代价体现在哪几处",
        "arms": {
            "SU_only": _cell(su),
            "MU_pair_table": _cell(pair),
            "MU_se_ratio_legacy": _cell(legacy),
            "SU_baseline_for_legacy": _cell(legacy_su),
            "MU_pair_table_corr0.999": _cell(corr),
        },
        "pair_true_minus_su_true_db": round(delta_db, 3),
        "power_loss_db": round(float(link.power_loss_db), 4),
        "mcs_shift_identity_holds": identity_ok,
        "pair_graph": pair_graph,
        "missing_1_2_edge_rejection": graph_rejection,
        "positive_olla_admission_counterexample": {
            "pre_olla_mcs": base_mcs,
            "step_bler_reject_mcs": bler_step_mcs,
            "mu_share": round(float(olla_admission.cell["mu_share"]), 4),
            "mu_pair_rejects": int(olla_admission.cell["mu_pair_rejects"]),
        },
        "mu_share_independent": round(float(pair.cell["mu_share"]), 4),
        "mu_share_corr0.999": round(float(corr.cell["mu_share"]), 4),
        "mu_su_wins_corr0.999": int(corr.cell["mu_su_wins"]),
        "interpretation": (
            "pair 表口径下配对的代价同时进 MCS 决策与误块抽签：首传平均 MCS 明显"
            "下降，而历史标量口径下它几乎不动（代价只体现在 TB 变小）。"
            "pair 真值系统性低于 SU 真值，其中 -3.01 dB 是等功率分摊、其余是相关性"
            "损失；决策平移量 CorrLoss+PowerLoss 恒等于 pred_MU-pred_SU，"
            "所以那个常数在决策里精确抵消。相关系数拉到 0.999 时配对率坍塌，"
            "说明 SU/MU 自适应真的在判而不是无条件配对。完整 pair graph 在调度前"
            "硬校验；准入使用叠加 SU+MU OLLA 后的实际发送 MCS。"),
        "not_a_conclusion": (
            "两种口径的吞吐差不是 MU 的收益或损失：它们是两个不同的物理模型，"
            "单次重复、未做配对检验，且历史口径本身已知系统性乐观。"),
    }


def experiment_rank_decision_machine() -> dict:
    """rank 判决状态机：升档迟滞、降档两种写法、指数退避。"""
    def ctl(**kw):
        base = dict(mode="adaptive", fixed_rank=1, period_tti=100,
                    min_filter_samples=1, se_filter_beta=1.0,
                    min_mcs_threshold=0,
                    resource_cost_ratio=(1.0, 1.0, 1.0, 1.0),
                    fallback_enabled=False)
        base.update(kw)
        return ap.RankController(ap.RankConfig(**base), 1, tti_ms=0.5,
                                 snapshot_ms=5.0, max_rank_available=4)

    def drive(controller, se, mcs=(20, 20, 20, 20), n_tti=401, start=None,
              olla=None):
        if start is not None:
            controller._rank[0] = int(start)
        for tti in range(n_tti):
            controller.observe_link(0, tti, list(se), list(mcs))
            controller.step(tti, olla_by_ue=olla)
        return controller

    raise_scan = {
        f"ratio_{r:.2f}": drive(ctl(), [1.0, r, 0.0, 0.0]).rank_of(0)
        for r in (1.05, 1.10, 1.11, 1.50)
    }
    narrow = [5.20, 4.0, 5.00, 3.0]      # 当前 rank3，最优 rank1 只高 4%
    reduce_matrix = {
        f"{rule}/G_down={gd:.1f}": drive(
            ctl(switch_rule=rule, gain_factor_reduce=gd), narrow,
            start=3).rank_of(0)
        for rule in ("spec_asymmetric", "unified_ratio")
        for gd in (1.1, 0.9)
    }
    gate = drive(ctl(min_mcs_threshold=9), [1.0, 2.0, 0.0, 0.0],
                 mcs=(20, 8, 8, 8), n_tti=301).rank_of(0)
    cost = ctl(resource_cost_ratio=ap.RankConfig().resource_cost_ratio)
    cost.observe_link(0, 0, [1.0, 1.0, 1.0, 1.0], [20, 20, 20, 20])

    backoff = ctl(fallback_enabled=True, quick_fallback_nack_thld=5,
                  max_backoff_times=4)
    olla = np.zeros(1)
    periods = set()
    for tti in range(40000):
        backoff.observe_link(0, tti, [1.0, 5.0, 0.0, 0.0], [20, 20, 20, 20])
        backoff.step(tti, olla_by_ue=olla)
        if backoff.rank_of(0) > 1:
            backoff.record_first_tx(0, ack=False, mcs=20, realized_se=0.0)
        periods.add(backoff.judge_period_tti(0))

    assert raise_scan["ratio_1.10"] == 1 and raise_scan["ratio_1.11"] == 2
    assert raise_scan["ratio_1.05"] == 1 and raise_scan["ratio_1.50"] == 2
    assert (reduce_matrix["spec_asymmetric/G_down=1.1"]
            != reduce_matrix["unified_ratio/G_down=1.1"])
    assert gate == 1
    return {
        "question": "rank 判决的每一道门各自挡住了什么",
        "raise_hysteresis_final_rank_by_se_ratio": raise_scan,
        "reduce_rule_matrix_final_rank": reduce_matrix,
        "reduce_matrix_setup": (
            "当前 rank3，滤波谱效 [5.20, 4.0, 5.00, 3.0]，最优 rank1 只高 4%"),
        "min_mcs_gate_final_rank": gate,
        "min_mcs_gate_setup": "rank2 估计谱效高一倍，但预估 MCS 8 低于闸门 9",
        "resource_cost_filtered_se": [
            round(float(x), 4) for x in cost._se_filt[0]],
        "backoff_period_values_tti": sorted(periods),
        "backoff_final_times": int(backoff.diagnostics()
                                   ["backoff_times_by_ue"][0]),
        "se_filter_memory_ms": {
            scope: ap.RankController(
                ap.RankConfig(mode="adaptive", se_sample_scope=scope), 1,
                tti_ms=0.5, snapshot_ms=5.0, max_rank_available=4
            ).diagnostics()["se_filter_memory_ms"]
            for scope in ("snapshot", "tti")},
        "interpretation": (
            "默认判据是对称 10% 迟滞（unified_ratio + 1.1）：按滤波谱效最大化选 "
            "rank，但任何方向都要超过 10% 才切。升档门限是严格大于：比值 1.10 "
            "不动、1.11 才升。降档的两种写法让同一个常数含义相反——默认这条是降"
            "也要 10% 余量，spec_asymmetric+1.1 则是降立即生效；差距超过 10% 时"
            "两者一致，所以差异只在临界带里。最小 MCS 闸门把一个发不出去的高 rank "
            "直接从候选里删掉，并且当前 rank 被闸门判死时不讲迟滞、直接降。"
            "判决周期按 2^n 退避到封顶。"),
        "not_a_conclusion": (
            "对称 10% 是负责人 2026-09-03 裁决的当前统一默认；这不等于已经用"
            "现场设备数据验证。旧 spec_asymmetric 只保留作迁移反向对照；"
            "se_sample_scope 的两个记忆长度差 10 倍，也不是等价实现。"),
    }


def main() -> int:
    report = {
        "title": "下行 AMC 链改动的物理反向对照",
        "scope": (
            "机制审计，不是性能结论。每项只跑一次重复、不做配对检验，"
            "任何百分比都必须重新走门 3。"),
        "rank_stability": experiment_rank_stability(),
        "olla_decision_coordinate": experiment_olla_coordinate(),
        "harq_feedback_delay": experiment_feedback_delay(),
        "cqi_filter": experiment_cqi_filter(),
        "grant_decode_sinr": experiment_grant_decode_sinr(),
        "table3_breaking_migration": experiment_table3_migration(),
        "capacity_mu_accounting": experiment_capacity_mu_accounting(),
        "rank_decision_machine": experiment_rank_decision_machine(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"OUTPUT={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
