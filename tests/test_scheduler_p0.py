"""Unit and stress tests for the P0 scheduling contracts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import amc_policy as ap  # noqa: E402
from superran import experience as exp_mod  # noqa: E402
from superran.experience import TbsLookup  # noqa: E402
from superran.scheduler_finalize import (  # noqa: E402
    CandidateGrant,
    finalize_candidate_grant,
)
from superran.scheduler_frequency import select_frequency_subset  # noqa: E402
from superran.scheduler_mu import (  # noqa: E402
    MuCandidateEvaluation,
    choose_mu_candidate,
)
from superran.scheduler_resource import (  # noqa: E402
    ResourceBudget,
    ResourceLedger,
    ResourceLimitError,
)


def test_resource_ledger_counts_mu_physical_once_and_layers_four() -> None:
    budget = ResourceBudget(17, (16,) * 17, max_layers_per_rbg=4)
    ledger = ResourceLedger(budget, tti=9)
    reservation = ledger.reserve(
        grant_index=0, mode="MU", users=(1, 2), ranks=(2, 2),
        rbg_indices=(3, 4, 5))
    ledger.commit(reservation.reservation_id)
    snapshot = ledger.snapshot()
    assert snapshot.used_physical_prb == 3 * 16
    assert snapshot.used_logical_prb == 3 * 16 * 4
    assert snapshot.max_layers_used == 4
    assert snapshot.used_rbg_indices == (3, 4, 5)


def test_resource_ledger_reserve_rollback_and_hard_overlap() -> None:
    ledger = ResourceLedger(ResourceBudget(4, (16,) * 4), tti=1)
    first = ledger.reserve(
        grant_index=0, mode="SU", users=(0,), ranks=(2,), rbg_indices=(0, 1))
    assert ledger.snapshot().reservation_count == 1
    ledger.rollback(first.reservation_id)
    assert ledger.snapshot().reservation_count == 0
    ledger.commit(ledger.reserve(
        grant_index=0, mode="SU", users=(0,), ranks=(2,),
        rbg_indices=(0,)).reservation_id)
    with pytest.raises(ResourceLimitError, match="physical_rbg_overlap"):
        ledger.reserve(
            grant_index=1, mode="SU", users=(1,), ranks=(1,), rbg_indices=(0,))


def test_resource_ledger_can_block_on_explicit_logical_budget() -> None:
    budget = ResourceBudget(
        4, (16,) * 4, max_layers_per_rbg=4, max_logical_prb=64)
    ledger = ResourceLedger(budget)
    grants = [
        SimpleNamespace(mode="SU", users=(0,), ranks=(2,), rbg_indices=(0, 1)),
        SimpleNamespace(mode="SU", users=(1,), ranks=(2,), rbg_indices=(2, 3)),
    ]
    admission = ledger.admit_grants(grants)
    assert admission.accepted_grant_indices == (0,)
    assert admission.rejections[0].reason == "logical_prb_budget"


def test_resource_ledger_rejects_unknown_mode_and_duplicate_user_in_one_tti() -> None:
    ledger = ResourceLedger(ResourceBudget(4, (16,) * 4))
    with pytest.raises(ResourceLimitError, match="unknown_mode"):
        ledger.reserve(
            grant_index=0, mode="mystery", users=(0,), ranks=(1,),
            rbg_indices=(0,))
    ledger.commit(ledger.reserve(
        grant_index=0, mode="SU", users=(0,), ranks=(1,),
        rbg_indices=(0,)).reservation_id)
    with pytest.raises(ResourceLimitError, match="duplicate_user_in_tti"):
        ledger.reserve(
            grant_index=1, mode="SU", users=(0,), ranks=(1,),
            rbg_indices=(1,))


def _candidate(**overrides) -> CandidateGrant:
    base = dict(
        mode="SU", users=(0,), rbg_indices=(0, 1), ranks=(1,),
        base_predicted_sinr_db=(10.0,), receive_sinr_db=(9.0,),
        corr_loss_db=(0.0,), power_loss_db=0.0, olla_mcs=(1.0,),
        queue_bytes=(10_000,), required_rbg=(2,), fits_in_fullband=(True,),
        potential_fullband_bytes=(20_000,),
    )
    base.update(overrides)
    return CandidateGrant(**base)


def test_finalizer_is_single_source_for_mcs_tbs_and_useful_bytes() -> None:
    lookup = TbsLookup.build(17, 16, mcs_table=3, target_bler=0.1)
    final = finalize_candidate_grant(
        _candidate(), lookup=lookup, slot="D", olla_enabled=True)
    assert final.mcs[0] >= final.mcs_without_olla[0]
    assert final.tbs_bytes[0] == lookup.tbs_bytes_for_indices(
        "D", final.mcs[0], 1, (0, 1))
    assert final.useful_bytes[0] == min(10_000, final.tbs_bytes[0])
    assert final.one_codeword_per_user


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"mode": "mystery"}, "mode must be SU or MU"),
        ({"mode": "MU", "users": (0,), "ranks": (1,),
          "base_predicted_sinr_db": (10.0,), "receive_sinr_db": (9.0,),
          "corr_loss_db": (0.0,), "olla_mcs": (0.0,),
          "queue_bytes": (1,), "required_rbg": (1,),
          "fits_in_fullband": (True,),
          "potential_fullband_bytes": (1,)}, "mode and user count"),
        ({"base_predicted_sinr_db": (float("nan"),)}, "finite"),
        ({"frequency_incremental_useful_bytes": -1}, "non-negative"),
    ],
)
def test_candidate_grant_rejects_structurally_invalid_inputs(
    override: dict, message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _candidate(**override)


def test_finalizer_mu_formula_and_harq_identity() -> None:
    lookup = TbsLookup.build(17, 16, mcs_table=3, target_bler=0.1)
    mu = _candidate(
        mode="MU", users=(0, 1), ranks=(2, 2),
        base_predicted_sinr_db=(16.0, 15.0), receive_sinr_db=(12.0, 11.0),
        corr_loss_db=(-2.0, -3.0), power_loss_db=-3.0103,
        olla_mcs=(0.0, 0.0), queue_bytes=(20_000, 20_000),
        required_rbg=(2, 2), fits_in_fullband=(True, True),
        potential_fullband_bytes=(20_000, 20_000))
    final_mu = finalize_candidate_grant(mu, lookup=lookup, slot="D")
    assert final_mu.mcs_input_sinr_db[0] == pytest.approx(16.0 - 2.0 - 3.0103)
    assert final_mu.mcs_input_sinr_db[1] == pytest.approx(15.0 - 3.0 - 3.0103)

    newtx = finalize_candidate_grant(_candidate(olla_mcs=(0.0,)), lookup=lookup, slot="D")
    retx = finalize_candidate_grant(_candidate(
        queue_bytes=(newtx.useful_bytes[0],),
        frozen_mcs=(newtx.mcs[0],),
        frozen_tbs_bytes=(newtx.tbs_bytes[0],),
        frozen_payload_bytes=(newtx.useful_bytes[0],)),
        lookup=lookup, slot="D")
    assert retx.mcs == newtx.mcs and retx.tbs_bytes == newtx.tbs_bytes
    assert retx.is_retransmission == (True,)


def test_frequency_selection_uses_fewer_rbg_than_sequential_counterfactual() -> None:
    scores = [0.0, 10.0, 9.0, 1.0]
    target = 1_500

    def evaluate(indices: tuple[int, ...]):
        raw = int(sum(scores[index] for index in indices) * 100)
        return SimpleNamespace(useful_bytes=(min(target, raw),))

    selected = select_frequency_subset(
        (0, 1, 2, 3), scores, cursor=0, evaluate=evaluate,
        sufficient=lambda grant: grant.useful_bytes[0] >= target)
    assert set(selected.selected_indices) == {1, 2}
    assert len(selected.selected_indices) == 2
    assert len(selected.baseline_indices) == 3
    assert selected.selected_useful_bytes >= selected.baseline_useful_bytes


def test_frequency_selection_randomized_safety_net_never_loses_useful_bytes() -> None:
    rng = np.random.default_rng(20260827)
    for case in range(300):
        scores = rng.normal(3.0, 5.0, size=17)
        keep = np.sort(rng.choice(17, size=int(rng.integers(1, 18)), replace=False))
        target = int(rng.integers(200, 20_000))

        def evaluate(
            indices: tuple[int, ...],
            *,
            _scores: np.ndarray = scores,
            _target: int = target,
        ):
            # Positive but nonlinear toy TBS: each extra RBG contributes a
            # quality-dependent amount, then integer quantization and queue cap.
            raw = int(sum(max(float(_scores[index]) + 8.0, 0.1) ** 1.3
                          for index in indices) * 37)
            return SimpleNamespace(useful_bytes=(min(_target, raw),))

        selected = select_frequency_subset(
            tuple(int(x) for x in keep), scores, cursor=case % 17,
            evaluate=evaluate,
            sufficient=lambda grant, _target=target: grant.useful_bytes[0] >= _target)
        assert selected.incremental_useful_bytes >= 0
        assert selected.selected_useful_bytes >= selected.baseline_useful_bytes
        assert set(selected.selected_indices).issubset(set(int(x) for x in keep))
        assert len(selected.selected_indices) == len(set(selected.selected_indices))


def test_mu_scorer_selects_later_better_partner_not_first_feasible() -> None:
    first_grant = SimpleNamespace(useful_bytes=(400, 400), n_rbg=4)
    better_grant = SimpleNamespace(useful_bytes=(700, 700), n_rbg=4)
    decision = choose_mu_candidate(0, [
        MuCandidateEvaluation(
            anchor_ue=0, partner_ue=1, pf_order=0, feasible=True,
            rejection_reason=None, correlation=0.4, predicted_bler_max=0.1,
            useful_bytes=800, used_rbg=4, useful_bytes_per_rbg=200.0,
            final_mcs=(10, 10), grant=first_grant),
        MuCandidateEvaluation(
            anchor_ue=0, partner_ue=2, pf_order=1, feasible=True,
            rejection_reason=None, correlation=0.2, predicted_bler_max=0.1,
            useful_bytes=1400, used_rbg=4, useful_bytes_per_rbg=350.0,
            final_mcs=(15, 15), grant=better_grant),
    ])
    assert decision.selected_partner_ue == 2
    assert decision.selected_grant is better_grant
    assert decision.feasible_count == 2


def test_end_to_end_directional_validation_experiments() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_scheduler_p0_validation.py")],
        cwd=ROOT, check=False, capture_output=True, text=True, encoding="utf-8",
        timeout=60)
    assert completed.returncode == 0, completed.stderr
    report = json.loads((
        ROOT / "artifacts" / "results" / "scheduler_p0_validation.json"
    ).read_text(encoding="utf-8"))
    assert report["frequency_selection"]["throughput_ratio_on_over_off"] > 1.2
    assert report["srs"]["pci_mod3_staggered"]["colliding_pair_count"] == 0
    decision = report["mu_candidate_scoring"]["first_tti_decision"]
    assert decision["selected_partner_ue"] == 2
    densities = {
        row["partner_ue"]: row["useful_bytes_per_rbg"]
        for row in decision["evaluations"]}
    assert densities[2] > densities[1]


# ---------------------------------------------------------------------------
# HARQ 重传的资源身份：冻结的是 PRB 数，不是 RBG 个数
# ---------------------------------------------------------------------------
# 51 RB 的 Type-0 分组是 (8,8,8,8,8,8,3)，尾组只有 3 PRB。以前 _HarqTb 只冻结
# RBG **个数**，重传按优先级顺序取前 n 个 RBG，于是首传落在 3 PRB 尾组、重传
# 落到 8 PRB 普通组时，同 MCS/rank/个数 算出来的 TBS 是原来的 2.7 倍——一个
# TB 在重传时变了大小。等长分组（绝大多数配置）看不到这个 bug。
_UNEVEN_SIZES = (8, 8, 8, 8, 8, 8, 3)


def _uneven_lookup() -> TbsLookup:
    return TbsLookup.build(len(_UNEVEN_SIZES), 8, 0.7,
                           rbg_prb_sizes=_UNEVEN_SIZES)


def test_uneven_rbg_makes_group_count_a_useless_tbs_identity():
    """先证明这个坑真的存在：同 MCS/rank、同样 1 个 RBG，TBS 差 2 倍以上。"""
    lookup = _uneven_lookup()
    tail = int(lookup.tbs_bytes_for_indices("D", 23, 2, (6,)))    # 3 PRB
    head = int(lookup.tbs_bytes_for_indices("D", 23, 2, (0,)))    # 8 PRB
    assert head > 2 * tail, (tail, head)


def test_retx_indices_matches_the_frozen_prb_count_not_the_group_count():
    lookup = _uneven_lookup()
    # 优先级顺序把 8 PRB 的组排在前面，尾组排最后。
    ordered = (0, 1, 2, 3, 4, 5, 6)
    # 冻结 3 PRB（首传用了尾组）：必须挑回尾组，而不是顺手拿第一个 8 PRB 组。
    assert exp_mod._retx_indices(ordered, lookup, 3) == (6,)
    # 冻结 11 PRB = 8 + 3：允许跨组，但 PRB 总数必须精确命中。
    assert exp_mod._retx_indices(ordered, lookup, 11) == (0, 6)
    # 凑不出精确的 PRB 数就返回 None，让调用方把重传推迟，而不是换个大小发。
    assert exp_mod._retx_indices((0, 1, 2), lookup, 3) is None
    assert exp_mod._retx_indices(ordered, lookup, 0) is None


def test_retx_indices_is_bit_identical_to_head_slice_when_groups_are_equal():
    """等长分组下必须与旧的 ``ordered[:n]`` 逐位一致，不能顺带改了主流配置。"""
    lookup = TbsLookup.build(17, 16, 0.7)
    ordered = tuple(range(17))
    for n in range(1, 18):
        assert exp_mod._retx_indices(ordered, lookup, 16 * n) == ordered[:n]


def _retx_su_plan(*, frequency_aware: bool, cursor: int):
    """构造一个"首传占了 3 PRB 尾组、重传时优先级把 8 PRB 组排前面"的 TTI。"""
    lookup = _uneven_lookup()
    n_rbg = len(_UNEVEN_SIZES)
    frozen_tbs = int(lookup.tbs_bytes_for_indices("D", 23, 2, (6,)))
    pending = exp_mod._HarqTb(
        mcs=23, rank=2, n_rbg=1, n_prb=3,
        tb_bytes=frozen_tbs, payload_bytes=frozen_tbs,
        slot="D", first_tti=0, first_mode="SU",
        feedback=ap.FirstTxFeedback(
            ue=0, ack=False, mcs=23, rank=2, realized_se=0.0,
            tx_tti=0, effective_tti=1, use_mu_olla=False),
        state="retx_ready")
    # 逐 RBG SINR 让 8 PRB 的组看起来更好，频选一定先拿它们。
    per_rbg = np.array([30.0, 29.0, 28.0, 27.0, 26.0, 25.0, 5.0])
    table = SimpleNamespace(
        sinr_db=np.full((1, 4), 25.0),
        sinr_tx_db=np.full((1, 4), 25.0),
        sinr_rbg_db=np.tile(per_rbg, (1, 4, 1)),
        sinr_tx_rbg_db=np.tile(per_rbg, (1, 4, 1)),
        outage=None)
    return exp_mod._build_su_plan(
        [0], queue_bytes={0: frozen_tbs}, lookup=lookup, slot="D",
        num_rbg=n_rbg, rank_of={0: 2}, mcs_of={0: 23},
        base_tx_sinr_of={0: 25.0}, mcs_without_olla_of={0: 23},
        true_sinr_of={0: 25.0}, potential_of={0: frozen_tbs},
        blocked_data=False, cursor=cursor, tables=[table], snap=0,
        su_olla_db=np.zeros(1), olla_enabled=False,
        frequency_aware=frequency_aware, harq_pending={0: pending})


@pytest.mark.parametrize(
    ("frequency_aware", "cursor"), [(False, 0), (True, 0)])
def test_retransmission_reproduces_the_frozen_tbs_on_an_uneven_carrier(
        frequency_aware, cursor):
    """棘轮：把 _retx_indices 换回 ordered[:n] 会让这里抛
    「HARQ 重传的同 MCS/RBG/rank 未复现原 TBS」。"""
    lookup = _uneven_lookup()
    plan = _retx_su_plan(frequency_aware=frequency_aware, cursor=cursor)
    assert len(plan.grants) == 1
    grant = plan.grants[0]
    prb = sum(_UNEVEN_SIZES[i] for i in grant.rbg_indices)
    assert prb == 3, (grant.rbg_indices, prb)
    assert int(grant.tbs_bytes[0]) == int(
        lookup.tbs_bytes_for_indices("D", 23, 2, (6,)))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
