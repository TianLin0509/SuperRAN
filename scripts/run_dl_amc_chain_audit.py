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
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import amc_policy as ap  # noqa: E402
from superran import csi_aging as ca  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import rng as rg  # noqa: E402
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
    return {
        "question": "ACK/NACK 等上行时隙这件事有没有真的进入调度与 OLLA",
        "arms": arms,
        "interpretation": (
            "开启时 DDDSU 逐相位偏移 5/4/3/2 个 TTI，并确实产生等待反馈而"
            "不参与调度的 TTI；关闭时偏移全为 1、等待计数为 0，回到旧行为。"),
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
        "calibration_status": "lambda 默认 0.25 是工程默认，尚未按现场标定。",
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
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"OUTPUT={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
