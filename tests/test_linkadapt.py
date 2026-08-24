"""链路自适应（链路到系统映射）与并行生成的测试。

直接运行：python tests/test_linkadapt.py
"""
from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(errors="replace")

from superran import channelhub as ch  # noqa: E402
from superran import experience as ex  # noqa: E402
from superran import generate as gen  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import load  # noqa: E402
from superran import loader as ld  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def sect(t: str) -> None:
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def _memory_dataset(h_true: np.ndarray, h_est: np.ndarray) -> ld.Dataset:
    """Small in-memory Dataset shell for a causal BF-gain regression."""
    ds = object.__new__(ld.Dataset)
    ds.dataset_id = "memory_bf_gain"
    ds.summary = {"shape": {"N": 1}, "config": {}, "source": "internal_sim"}
    ds.__dict__["h_true"] = np.asarray(h_true)[None]
    ds.__dict__["h_est"] = np.asarray(h_est)[None]
    ds.__dict__["h_interferers"] = None
    return ds


def test_tdd_bf_gain_uses_gnb_csi_not_true_channel() -> None:
    """Changing h_true alone must not change the BF gain used for MCS."""
    rng = np.random.default_rng(20260823)
    shape = (1, 8, 4, 2)
    h_est = (rng.normal(size=shape) + 1j * rng.normal(size=shape)) / np.sqrt(2)
    h_true_a = h_est.copy()
    h_true_b = (rng.normal(size=shape) + 1j * rng.normal(size=shape)) / np.sqrt(2)
    # Change only the spatial realization, not the pre-beam power/noise operating
    # point.  Otherwise snr_db intentionally re-anchors N0 and this is no longer a
    # pure h_true-causality test.
    h_true_b *= np.sqrt(
        np.mean(np.abs(h_true_a) ** 2) / np.mean(np.abs(h_true_b) ** 2))
    common = dict(
        cqi_index=5, use_estimated_csi=True, snr_db=10.0, max_rank=2)
    arm_a = _memory_dataset(h_true_a, h_est).tdd_mcs(0, **common)
    arm_b = _memory_dataset(h_true_b, h_est).tdd_mcs(0, **common)
    assert arm_a["bf_gain_csi_view"] == "gnb_precoding_csi"
    assert arm_a["bf_gain_user_db"] == arm_b["bf_gain_user_db"]
    assert arm_a["gnb_predicted_amc_sinr_db"] == arm_b["gnb_predicted_amc_sinr_db"]
    assert arm_a["final_mcs"] == arm_b["final_mcs"]
    assert arm_a["pmi_stream_sinr_db"] == arm_b["pmi_stream_sinr_db"]
    assert arm_a["svd_stream_sinr_db"] == arm_b["svd_stream_sinr_db"]
    assert arm_a["bf_gain_true_user_db"] != arm_b["bf_gain_true_user_db"]
    assert arm_a["actual_receive_sinr_db"] != arm_b["actual_receive_sinr_db"]
    for arm in (arm_a, arm_b):
        expected_bler = float(la.bc.get_curve(
            arm["final_mcs"], "newtx").evaluate(arm["actual_receive_sinr_db"])[0])
        assert abs(arm["final_mcs_newtx_bler"] - expected_bler) < 1e-6
        assert arm["actual_bler_available"] is True
        assert arm["physical_tx_sinr_label"] == "SINR_NEBF"
        assert arm["sinr_views"]["bler_observation"]["db"] == arm["actual_receive_sinr_db"]
    assert arm_b["true_channel_bf_audit_enters_mcs"] is False


def test_preset_curve_contract_is_universal_single_codeword_tb() -> None:
    contract = la.bc.verify_curves()
    assert contract["consistent"]
    assert "single-codeword" in contract["error_event"]
    assert contract["preset_lookup_inputs"] == [
        "codeword_effective_sinr_db", "mcs"]
    assert "not modeled by product decision" in contract["tb_size_axis_status"]
    assert "exact code rates" in contract["extra_code_rate_rows_status"]

    # 预置表已明确：1/17 RBG 的 TBS 不同，但只要 MCS+码字 SINR 相同就查同一条
    # 通用曲线。CB 数仍可被表 1/2 分析模型计算，却不得进入预置表 3 路径。
    lookup = ex.TbsLookup.build(17, 16)
    short_tbs = lookup.tbs_bytes("D", 12, 2, 1)
    long_tbs = lookup.tbs_bytes("D", 12, 2, 17)
    assert (short_tbs, long_tbs) == (1_729, 29_722)
    short_cb = la.code_blocks(short_tbs * 8, la.MCS_TABLE_3[12].rate)[0]
    long_cb = la.code_blocks(long_tbs * 8, la.MCS_TABLE_3[12].rate)[0]
    assert (short_cb, long_cb) == (2, 29)
    assert list(inspect.signature(ex._bler_lookup).parameters) == ["mcs", "sinr_db"]

    model = la.CurveBlerModel("newtx")
    mcs = la.MCS_TABLE_3[12]
    short_n_coded = 16 * 12 * 12 * mcs.q_m * 2
    long_n_coded = 17 * short_n_coded
    short_tb = model.bler(
        14.0, mcs, n_coded_bits=short_n_coded, n_code_blocks=short_cb
    )
    long_tb = model.bler(
        14.0, mcs, n_coded_bits=long_n_coded, n_code_blocks=long_cb
    )
    assert np.array_equal(short_tb, long_tb)
    for bad_mcs in (True, 12.5):
        try:
            la.harq_retransmission_bler(bad_mcs, 10.0)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法重传 MCS 未被拒绝：{bad_mcs!r}")
    for bad_max_tx in (3, 1.5, True):
        try:
            la.link_adaptation(
                np.full(16, 10.0), n_prb=16, max_harq_tx=bad_max_tx)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"超过一次重传/非整数 max_harq_tx 未被拒绝：{bad_max_tx!r}")


def test_preset_one_retransmission_cc_and_ir() -> None:
    cc = la.harq_retransmission_bler(20, 16.0, combining="cc")
    ir = la.harq_retransmission_bler(20, 16.0, combining="ir")
    assert cc["transmitted_mcs"] == cc["lookup_mcs"] == 20
    assert abs(cc["lookup_sinr_db"] - (16.0 + 10 * np.log10(2))) < 1e-12
    assert ir["transmitted_mcs"] == 20 and ir["lookup_mcs"] == 10
    assert ir["curve_tx_mode"] == cc["curve_tx_mode"] == "newtx"
    assert ir["bler"] < cc["bler"] < float(
        la.bc.get_curve(20, "newtx").evaluate(16.0)[0])
    for mcs in range(28):
        row = la.harq_retransmission_bler(mcs, 10.0, combining="ir")
        assert 0 <= row["lookup_mcs"] <= mcs
        assert row["equivalent_spectral_efficiency"] == la.MCS_TABLE_3[mcs].se / 2


def main() -> None:
    ch.warmup()

    # -----------------------------------------------------------------------
    sect("1  QAM 约束容量（精确计算，可自检）")

    snr_db = np.array([-20., -10., 0., 10., 20., 30., 40.])
    shannon = np.log2(1 + 10 ** (snr_db / 10))
    print(f"  {'SNR':>5} {'香农':>8} " + " ".join(f"{m:>8}" for m in (4, 16, 64, 256)))
    for i, s in enumerate(snr_db):
        vals = [la.qam_mi(m, s)[0] for m in (4, 16, 64, 256)]
        print(f"  {s:>5.0f} {shannon[i]:>8.3f} " + " ".join(f"{v:>8.3f}" for v in vals))

    for m in (4, 16, 64, 256):
        mi = la.qam_mi(m, snr_db)
        check(bool(np.all(mi <= shannon + 1e-6)), f"{m}QAM 互信息恒 ≤ 香农")
        check(bool(np.all(np.diff(mi) >= -1e-9)), f"{m}QAM 互信息随信噪比单调")
        check(abs(mi[-1] - np.log2(m)) < 0.02, f"{m}QAM 高信噪比饱和到 log2(M)")

    # 低信噪比处应与香农重合 —— 这条能抓住 sigma 定义差 3 dB 那类错误
    lo = la.qam_mi(4, -25.0)[0]
    check(abs(lo - np.log2(1 + 10 ** (-2.5))) < 1e-3, "低信噪比处与香农重合（口径正确）")

    # 反解要能还原
    for m in (4, 64):
        g0 = 12.0
        back = la.qam_mi_inverse(m, la.qam_mi(m, g0)[0])[0]
        check(abs(back - g0) < 0.3, f"{m}QAM 互信息反解可还原信噪比")

    # -----------------------------------------------------------------------
    sect("2  38.214 表格自检")

    v = la.verify_tables()
    print(f"  核对 {v['n_checked']} 行：SE == q_m·R/1024")
    check(v["consistent"], "MCS/CQI 表内蕴一致（抄错一个数就会不一致）")
    check(len(la.MCS_TABLE_1) == 29, "MCS 表 1 共 29 档")
    check(len(la.MCS_TABLE_2) == 28, "MCS 表 2 共 28 档")
    check(len(la.CQI_TABLE_1) == 15, "CQI 表 1 共 15 档")
    check(max(m.q_m for m in la.MCS_TABLE_1) == 6, "表 1 最高 64QAM")
    check(max(m.q_m for m in la.MCS_TABLE_2) == 8, "表 2 含 256QAM")

    # 标准表在调制切换点上 SE 故意重叠，这不是抄错
    check(la.MCS_TABLE_1[9].se < la.MCS_TABLE_1[10].se, "MCS9→10 SE 重叠（标准如此）")
    check(la.MCS_TABLE_1[16].se > la.MCS_TABLE_1[17].se, "MCS16→17 SE 回落（标准如此）")

    # -----------------------------------------------------------------------
    sect("3  传输块大小（38.214 §5.1.3.2 两支都要覆盖）")

    n_re = la.re_per_slot(273, n_symbols=12, n_dmrs_per_prb=12)
    print(f"  273 PRB → n_re={n_re}")
    check(n_re == 273 * 132, "RE 数 = PRB × (12·符号 − DMRS)")
    check(la.re_per_slot(273, n_symbols=14, n_dmrs_per_prb=0) == 273 * 156,
          "每 PRB 的 RE 数封顶 156（标准明写）")

    small = la.transport_block_size(50, 0.234, 2, 1)
    check(small in la._TBS_SMALL, "小包走查表分支，落在 Table 5.1.3.2-1 上")

    big = la.transport_block_size(n_re, 0.926, 6, 4)
    check(big > 3824, "大包走量化分支")
    check(big % 8 == 0, "大包 TBS 是 8 的倍数")
    print(f"  273PRB MCS28 4层 → TBS {big} bit → {big/0.5e-3/1e6:.0f} Mbps 峰值")
    check(1_200 < big / 0.5e-3 / 1e6 < 2_200, "100MHz 4 层峰值吞吐在 1.2~2.2 Gbps")

    # TBS 应随各因素单调
    check(la.transport_block_size(n_re, 0.5, 6, 2) > la.transport_block_size(n_re, 0.5, 6, 1),
          "层数翻倍 TBS 变大")
    check(la.transport_block_size(n_re, 0.9, 6, 1) > la.transport_block_size(n_re, 0.3, 6, 1),
          "码率越高 TBS 越大")

    # -----------------------------------------------------------------------
    sect("4  码块分段")

    for tbs, r, want_ge in ((200, 0.117, 1), (8448, 0.5, 2), (200808, 0.926, 20)):
        c, k = la.code_blocks(tbs, r)
        print(f"  TBS {tbs:7d} R={r:.3f} → {c:3d} 块 × {k} bit")
        check(c >= want_ge, f"TBS {tbs} 至少分成 {want_ge} 块")
        check(k <= 8448 + 24, "每块不超过 BG1 的 K_cb")
    check(la.code_blocks(200, 0.117)[0] == 1, "小 TB 不分段")

    # -----------------------------------------------------------------------
    sect("5  有效 SINR（链路到系统映射）")

    flat = np.full(273, 15.0)
    for meth in ("miesm", "eesm"):
        e = la.effective_sinr(flat, method=meth, m_order=64)
        check(abs(e - 15.0) < 0.15, f"{meth}：平坦信道下等于原值")

    rng = np.random.default_rng(0)
    prev = None
    for spread in (2.0, 6.0, 12.0):
        g = 15.0 + rng.normal(0, spread, 273)
        e = la.effective_sinr(g, method="miesm", m_order=64)
        lin = 10 * np.log10(np.mean(10 ** (g / 10)))
        print(f"  起伏 σ={spread:4.1f} dB → 有效 {e:6.2f} dB，线性均值 {lin:6.2f} dB")
        check(e < lin, "有效 SINR 低于线性均值（好 RE 补不了坏 RE）")
        if prev is not None:
            check(e < prev, "频选越严重有效 SINR 越低")
        prev = e

    # -----------------------------------------------------------------------
    sect("6  BLER 模型的门限锚点")

    a = la.DEFAULT_BLER.anchor_check(table=1, n_coded_bits=20000)
    lo_db, hi_db = a["span_db"]
    print(f"  MCS0 需要 {lo_db:.2f} dB，MCS28 需要 {hi_db:.2f} dB")
    print(f"  调制切换点回落：{a['modulation_switch_drops']}")
    check(a["monotonic_within_modulation"], "同一调制内门限单调上升")
    check(a["above_shannon_limit"], "每档门限都高于其香农极限（不可能优于容量）")
    check(-8 <= lo_db <= -3, "MCS0 门限落在公开曲线的常见区间 −5~−7 dB 附近")
    check(18 <= hi_db <= 24, "MCS28 门限落在公开曲线的常见区间 20~23 dB 附近")
    check(all(0 < d["drop_db"] < 1.0 for d in a["modulation_switch_drops"]),
          "切换点回落幅度很小（标准表设计使然，非缺陷）")
    check("不是实测" in a["caveat"], "如实标注 BLER 是模型不是实测")

    # BLER 必须随信噪比单调下降
    m28 = la.MCS_TABLE_1[28]
    b = la.DEFAULT_BLER.bler(np.array([10., 15., 20., 25., 30.]), m28, 20000, 3)
    check(bool(np.all(np.diff(b) <= 1e-12)), "BLER 随信噪比单调下降")
    check(b[0] > 0.9 and b[-1] < 1e-3, "低信噪比几乎必错、高信噪比几乎必对")

    # 分段越多 TB 越容易错
    b1 = float(la.DEFAULT_BLER.bler(19.0, m28, 20000, 1)[0])
    b24 = float(la.DEFAULT_BLER.bler(19.0, m28, 20000, 24)[0])
    print(f"  同信噪比下 1 块 BLER {b1:.4f}，24 块 {b24:.4f}")
    check(b24 > b1, "码块越多 TB 级 BLER 越高（任一块错则整块错）")

    # -----------------------------------------------------------------------
    sect("6.5  预置单码字 TB-BLER 曲线与一次 HARQ 工程抽象")

    cv = la.bc.verify_curves()
    print(f"  {cv['n_mcs']} 个 MCS / {cv['n_curves']} 条曲线 / {cv['n_points']} 个点")
    check(cv["consistent"], "曲线哈希、覆盖、单调性和 10% 门限全部自洽")
    check(cv["hash_matches"], "曲线数据与导入时 SHA-256 一致")
    check("single-codeword" in cv["error_event"]
          and "CB is not exposed" in cv["error_event"],
          "预置误块事件明确是一用户 grant/TTI 的单码字 TB，不单列 CB")
    check(cv["preset_lookup_inputs"] == [
              "codeword_effective_sinr_db", "mcs"],
          "预置通用曲线只以单码字有效 SINR + MCS 查 BLER")
    check("not modeled by product decision" in cv["tb_size_axis_status"],
          "TBS/RE/rank/场景是明确忽略的维度，不再误报成待补查询轴")
    check("exact code rates" in cv["extra_code_rate_rows_status"],
          "额外未映射码率行的具体数值/语义未被原始导入保留，不能凭空解释")
    check(len(la.MCS_TABLE_3) == 28, "表 3 共 28 档，覆盖 MCS 0..27")
    check(max(m.q_m for m in la.MCS_TABLE_3) == 8, "表 3 含 256QAM")

    c15n = la.bc.get_curve(15, "newtx")
    c15r = la.bc.get_curve(15, "retx")
    check(c15n.q_m == 6 and abs(c15n.code_rate - 0.650) < 1e-12,
          "MCS15 NewTx 映射到 64QAM R=0.650")
    check(abs(c15r.code_rate - 0.333) < 1e-12,
          "MCS15 ReTx 映射到 R=0.333")
    check(abs(float(c15n.evaluate(14.00)[0]) - 0.132) < 1e-12 and
          abs(float(c15n.evaluate(14.05)[0]) - 0.0949) < 1e-12,
          "MCS15 NewTx 在原始网格点逐值还原")
    check(abs(c15n.required_sinr_db(0.1) - 14.0421) < 1e-3,
          "MCS15 NewTx 10% BLER 门限为 14.042 dB")
    check(abs(c15r.required_sinr_db(0.1) - 7.7429) < 1e-3,
          "MCS15 ReTx 10% BLER 门限为 7.743 dB")
    c15_contract = c15n.as_dict(include_points=False)
    check(c15_contract["error_event"] == cv["error_event"] and
          c15_contract["tb_size_axis_status"] == cv["tb_size_axis_status"],
          "单曲线查询携带同一单码字 TTI/TB 与通用曲线合同")
    current_model = la.CurveBlerModel("newtx")
    mcs12 = la.MCS_TABLE_3[12]
    short_n_coded = 16 * 12 * 12 * mcs12.q_m * 2
    short_tb_bler = current_model.bler(
        14.0, mcs12, n_coded_bits=short_n_coded, n_code_blocks=2
    )
    long_tb_bler = current_model.bler(
        14.0, mcs12, n_coded_bits=17 * short_n_coded, n_code_blocks=29
    )
    check(np.array_equal(short_tb_bler, long_tb_bler),
          "反例锁定：当前预置曲线不随当次 coded bits/TBS/CB 数变化")
    check(float(c15n.evaluate(0.0)[0]) == 1.0 and
          abs(float(c15n.evaluate(30.0)[0]) - c15n.bler_points[-1]) < 1e-12,
          "曲线范围外保守钳位，不伪造外推尾部")

    _orig_code_blocks = la.code_blocks
    la.code_blocks = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("预置表 3 不应进入 CB 分段"))
    try:
        _single_codeword_probe = la.link_adaptation(
            np.full(16, 14.2), n_prb=16, layers=1, mcs_table=3,
            max_harq_tx=1)
    finally:
        la.code_blocks = _orig_code_blocks
    check(_single_codeword_probe.bler >= 0.0,
          "预置表 3 的端到端路径不调用 CB 分段/CBLER→TBLER 合成")

    cc = la.harq_retransmission_bler(20, 16.0, combining="cc")
    ir = la.harq_retransmission_bler(20, 16.0, combining="ir")
    check(abs(cc["lookup_sinr_db"] - (16.0 + 10 * np.log10(2))) < 1e-12
          and cc["lookup_mcs"] == 20,
          "CC 保持 MCS20 曲线并把码字 SINR 精确抬升 10log10(2) dB")
    check(ir["transmitted_mcs"] == 20 and ir["lookup_mcs"] == 10
          and abs(ir["equivalent_spectral_efficiency"]
                  - la.MCS_TABLE_3[20].se / 2) < 1e-12,
          "IR 保持空口 MCS20，但半谱效按真实表反查到等效 MCS10")
    check(ir["bler"] < cc["bler"]
          < float(la.bc.get_curve(20, "newtx").evaluate(16.0)[0]),
          "高阶 MCS 工作点上 IR 重传 BLER < CC < 初传 BLER")

    tab = la.link_adaptation(np.full(273, 14.2), n_prb=273, layers=1,
                             mcs_table=3, harq_combining="ir")
    check(tab.mcs_index == 15, "表 3 在 14.2 dB 选择 MCS15")
    check(tab.bler_source == "preset_20b_256qam", "结果显式标出预置表 BLER 来源")
    check(0 <= tab.cqi <= 13 and tab.cqi_source == la.INTERNAL_CQI_SOURCE,
          "预置表端到端返回内部 CQI，不混用 38.214 CQI 编号")
    check(tab.retx_bler is not None and tab.retx_bler < tab.bler,
          "HARQ 首传后按半谱效 IR 抽象从 NewTx 曲线推导重传 BLER")
    check(tab.harq_model == "one_retransmission_ir_derived_from_newtx",
          "结果显式标出只允许一次 IR 重传且只用 NewTx 曲线")
    check("SINR" in tab.bler_axis_source and "MMSE" in tab.bler_axis_source,
          "结果明确曲线横轴为经典 MMSE 接收机 SINR")

    # -----------------------------------------------------------------------
    sect("6.6  TDD CQI → BF Gain → MCS → OLLA")

    expected_internal = (0, 1, 3, 5, 7, 9, 12, 14, 16, 19, 21, 23, 25, 27, 28)
    mapped_internal = tuple(
        la.internal_cqi_to_mcs(i, mcs_table=1)["mcs"]
        for i in range(len(expected_internal))
    )
    check(mapped_internal == expected_internal,
          "内部 CQI0..14 逐项映射到已确认的 MCS 离散表")

    cqi0 = la.internal_cqi_to_mcs(0)
    check(cqi0["scheduled"] is True and cqi0["mcs"] == 0,
          "内部 CQI0 是最低可用档并映射 MCS0")

    cqi9 = la.internal_cqi_to_mcs(9)
    check(cqi9["mcs"] == 19 and cqi9["mcs_clipped_to_profile"] is False,
          "内部 CQI9 查离散表得到 MCS19")

    cqi14 = la.internal_cqi_to_mcs(14)
    check(cqi14["requested_mcs"] == 28 and cqi14["mcs"] == 27
          and cqi14["mcs_clipped_to_profile"] is True,
          "CQI14 保留 MCS28 合同，当前 0..27 曲线 profile 显式钳位")
    for bad_cqi in (15, -1, 2.5, True):
        try:
            la.internal_cqi_to_mcs(bad_cqi)  # type: ignore[arg-type]
            rejected = False
        except ValueError:
            rejected = True
        check(rejected, f"内部 CQI 拒绝越界/非整数输入 {bad_cqi!r}")

    tdd = la.tdd_mcs_adaptation(
        9,
        [[13.0, 10.0], [15.0, 12.0]],
        [[10.0, 8.0], [12.0, 10.0]],
        olla_mcs_offset=-0.2,
        feedback_ack=False,
    )
    check(tdd["scheduled"] is True and tdd["rank"] == 2 and tdd["n_rb"] == 2,
          "TDD 决策保留逐 RB、逐流维度")
    check(abs(tdd["cqi_mcs_sinr_db"] - 17.6419) < 1e-3,
          "初始 MCS19 转成 NewTx 10% BLER SINR 门限")
    check(tdd["bf_gain_per_stream_db"] == [3.0, 2.0],
          "BF Gain 逐流等于 SVD post-MMSE SINR 减 PMI post-MMSE SINR")
    check(abs(tdd["bf_gain_user_db"] - 2.5) < 1e-12,
          "用户 BF Gain 在所有 RB×流上做 dB 域算术平均")
    check(abs(tdd["user_sinr_db"] - 20.1419) < 1e-3,
          "用户 SINR 等于初始门限叠加逐 RB/流 BF Gain 后的 dB 域平均")
    check("dB domain" in tdd["sinr_aggregation"],
          "结果显式声明 dB 域平均口径")
    check(tdd["mcs_after_bf"] == 21,
          "叠加 BF Gain 后按 NewTx 门限重映射到 MCS21")
    check(abs(tdd["mcs_before_floor"] - 20.8) < 1e-12 and
          tdd["mcs_after_floor"] == 20 and tdd["final_mcs"] == 20,
          "OLLA 在 MCS 域相加后严格向下取整并钳位")
    check(tdd["final_mcs_newtx_bler"] is None
          and tdd["actual_bler_available"] is False
          and tdd["bler_status"] == "unknown_without_true_receive_sinr",
          "只有 CQI/BF/OLLA 标量时不拿 AMC 预测坐标冒充真实 BLER")
    check("predicted_final_mcs_newtx_bler" not in tdd,
          "结果不再输出容易被误读为真实误块率的预测 BLER")
    check(abs(tdd["sinr_svd_gnb_db"] - tdd["sinr_pmi_gnb_db"]
              - tdd["bf_gain_user_db"]) < 2e-4,
          "gNB 物理 SVD/PMI SINR 之差逐值闭合到 BF Gain")
    check(tdd["receiver"] == "classic MMSE" and
          "only precoding weight changes" in tdd["fairness_contract"],
          "结果钉住经典 MMSE 与只改变预编码权的公平对照")
    check(abs(tdd["olla_update"]["delta_mcs"] + 0.9) < 1e-12 and
          abs(tdd["olla_next_offset_mcs"] + 1.1) < 1e-12,
          "10% 目标下 NACK 令下一时刻 OLLA 减 0.9 MCS")

    ack = la.update_olla_mcs(0.3, True)
    check(abs(ack["next_offset_mcs"] - 0.4) < 1e-12,
          "ACK 令下一时刻 OLLA 加 0.1 MCS")

    floor_edge = la.tdd_mcs_adaptation(
        9, [[14.0]], [[14.0]], olla_mcs_offset=-0.01,
    )
    check(floor_edge["mcs_after_bf"] == 19 and floor_edge["final_mcs"] == 18,
          "极小负 OLLA 也按数学 floor 降一档，不做截零取整")

    try:
        la.tdd_mcs_adaptation(9, [[1.0, 2.0]], [[1.0]])
        shape_rejected = False
    except ValueError:
        shape_rejected = True
    check(shape_rejected, "SVD/PMI 的 RB×流形状不一致时拒绝计算")

    try:
        la.tdd_mcs_adaptation(9, [[float("nan")]], [[1.0]])
        nan_rejected = False
    except ValueError:
        nan_rejected = True
    check(nan_rejected, "非有限 SINR 不进入 BF Gain 与 MCS 决策")

    one_rbg = la.codeword_sinr_db([[20.0], [-20.0]], rb_per_rbg=2)
    two_rbg = la.codeword_sinr_db([[20.0], [-20.0]], rb_per_rbg=1)
    check(abs(one_rbg["user_db"] - 10.0 * np.log10(50.005)) < 1e-12,
          "RBG 内 RB SINR 先在线性功率域平均")
    check(abs(two_rbg["user_db"]) < 1e-12,
          "跨 RBG 在 dB 域算术平均，+20/−20 dB 得 0 dB")

    test_tdd_bf_gain_uses_gnb_csi_not_true_channel()
    check(True, "sr_tdd_mcs 只用 gNB 可见 CSI 计算进入 MCS 的 BF Gain，h_true 只作事后审计")

    # -----------------------------------------------------------------------
    sect("7  链路自适应端到端")

    rng = np.random.default_rng(3)
    prev_tp = None
    for mean_db in (-5, 5, 15, 25):
        g = mean_db + rng.normal(0, 3, 273)
        r = la.link_adaptation(g, n_prb=273, layers=2)
        print(f"  SINR≈{mean_db:3d} dB → MCS {r.mcs_index:2d} CQI {r.cqi:2d} "
              f"{r.modulation:<6} 吞吐 {r.throughput_bps/1e6:7.1f} Mbps "
              f"达成 {r.efficiency_vs_shannon:5.1%}")
        check(0 <= r.mcs_index <= 28, "MCS 索引合法")
        check(0 <= r.cqi <= 15, "CQI 合法")
        check(r.bler <= 0.1 + 1e-9 or r.mcs_index == 0, "选中的 MCS 满足目标 BLER")
        check(r.se_achieved <= r.se_shannon, "实际谱效不超香农上界")
        check(r.throughput_bps <= r.throughput_ideal_bps + 1e-6, "有效吞吐不超名义吞吐")
        if prev_tp is not None:
            check(r.throughput_bps >= prev_tp, "信噪比越高吞吐越高")
        prev_tp = r.throughput_bps

    # 目标 BLER 越严，选的 MCS 越保守
    g = np.full(273, 15.0)
    strict = la.link_adaptation(g, n_prb=273, target_bler=0.01)
    loose = la.link_adaptation(g, n_prb=273, target_bler=0.1)
    print(f"  目标 BLER 1% → MCS {strict.mcs_index}，10% → MCS {loose.mcs_index}")
    check(strict.mcs_index <= loose.mcs_index, "目标 BLER 越严 MCS 越保守")

    # 256QAM 表在高信噪比下更强
    hi = np.full(273, 28.0)
    t1 = la.link_adaptation(hi, n_prb=273, mcs_table=1)
    t2 = la.link_adaptation(hi, n_prb=273, mcs_table=2)
    print(f"  28 dB：表1 {t1.throughput_bps/1e6:.0f} Mbps，表2 {t2.throughput_bps/1e6:.0f} Mbps")
    check(t2.throughput_bps > t1.throughput_bps, "高信噪比下 256QAM 表吞吐更高")

    # -----------------------------------------------------------------------
    sect("8  吞吐统计与边缘用户")

    rng = np.random.default_rng(5)
    res = [la.link_adaptation(rng.normal(12, 8) + rng.normal(0, 3, 273), n_prb=273)
           for _ in range(40)]
    st = la.throughput_stats(res)
    print(st.text())
    check(st.n == 40, "样本数正确")
    check(st.cell_edge_mbps <= st.median_mbps <= st.peak_mbps, "5% ≤ 中位 ≤ 95%")
    check(sum(st.mcs_distribution.values()) == 40, "MCS 分布计数完整")
    check("边缘用户" in st.as_dict()["note"], "说明 5% 分位的含义")

    # -----------------------------------------------------------------------
    sect("9  RB 表口径与调度估计的版本化锚点")

    check(gen._rb_from_bandwidth({"bandwidth_hz": 20e6,
                                  "subcarrier_spacing": 30_000}) == 51,
          "带宽反查复用标准表：20 MHz @ 30 kHz = 51 RB，不再近似成 52")
    check(gen._rb_from_bandwidth({"bandwidth_hz": 100e6,
                                  "subcarrier_spacing": 30_000}) == 273,
          "带宽反查复用标准表：100 MHz @ 30 kHz = 273 RB")
    try:
        gen._rb_from_bandwidth({"bandwidth_hz": 17e6,
                                "subcarrier_spacing": 30_000})
        nonstandard_rejected = False
    except (KeyError, ValueError):
        nonstandard_rejected = True
    check(nonstandard_rejected,
          "非标准带宽不再静默做 0.95 除法近似；synthetic grid 要显式给 num_rb")

    # 这些是 2026-08-11、20-ray 内核、热进程的历史基准锚点，不是本测试
    # 现场测出来的值。普通 CI 不跑 timing，避免把宿主负载变成随机正确性门。
    pts = [
        ("1c/32T/20M", dict(num_sites=1, sectors_per_site=1,
                            num_bs_tx_ant=32, num_rb=51), 0.158),
        ("1c/64T/100M", dict(num_sites=1, sectors_per_site=1,
                             num_bs_tx_ant=64, num_rb=273), 1.074),
        ("21c/16T/20M", dict(num_sites=7, sectors_per_site=3,
                             num_bs_tx_ant=16, num_rb=51), 7.479),
    ]
    for name, cfg_anchor, recorded_s in pts:
        estimate_s = gen.estimate_seconds(cfg_anchor, 1)
        err = estimate_s / recorded_s - 1
        print(f"  历史锚点 {name:12s} {recorded_s:6.3f}s / "
              f"调度估计 {estimate_s:6.3f}s  偏差 {err:+.0%}")
        check(abs(err) < 0.35,
              f"20ray-2026-08-11 锚点 {name} 的调度估计偏差在 35% 内")

    check(gen.estimate_seconds({}, 0) == 0.0, "零样本估时为零")

    light = dict(num_sites=1, sectors_per_site=1, num_bs_tx_ant=32, num_rb=51)
    heavy = dict(num_sites=7, sectors_per_site=3, num_bs_tx_ant=64, num_rb=273)
    check(gen._resolve_workers("auto", 8, light) == 1,
          "小批轻配置仍不起进程（启动成本不划算）")
    check(gen._resolve_workers("auto", 200, light) > 1,
          "20-ray 下大批轻配置也会并行，不再套用旧 24 ms 假设")
    check(gen._resolve_workers("auto", 200, heavy) > 4, "重配置自动多进程")
    check(gen._resolve_workers("auto", 2, heavy) <= 2, "进程数不超过样本数")
    check(gen._resolve_workers(1, 200, heavy) == 1, "显式 workers=1 强制串行")
    heavy_20ue = dict(heavy, num_ues=20)
    check(gen._requested_worker_count("auto", 20, heavy_20ue) > 1,
          "重配置原始工作量启发式会请求并行")
    check(gen._resolve_workers("auto", 20, heavy_20ue) == 1,
          "20 样本/20 UE 只启一个 worker，避免重复构造 20 个 UE batch")
    check(gen._resolve_workers(20, 80, heavy_20ue) == 4,
          "80 样本/20 UE 的显式并行度收口到四个 UE batch")

    # -----------------------------------------------------------------------
    sect("10  并行生成与串行等价")

    cfg = dict(scenario="UMa_NLOS", channel_model="CDL-C", num_sites=7,
               sectors_per_site=3, isd_m=500.0, num_ues=6, num_bs_tx_ant=16,
               num_ue_rx_ant=2, num_ue_tx_ant=2, bandwidth_hz=20e6,
               subcarrier_spacing=30000, carrier_freq_hz=3.5e9, link="DL", seed=42)
    N = 24
    t0 = time.perf_counter()
    s1 = gen.generate(dict(cfg), num_samples=N, workers=1)
    t_ser = time.perf_counter() - t0
    t0 = time.perf_counter()
    sp = gen.generate(dict(cfg), num_samples=N, workers=4)
    t_par = time.perf_counter() - t0
    d1, dp = load(s1["dataset_id"]), load(sp["dataset_id"])
    print(f"  串行 {t_ser:.1f}s / 并行(4) {t_par:.1f}s")
    print(f"  并行摘要 {sp['parallel']}")

    check(s1["num_samples"] == sp["num_samples"] == N, "两种路径样本数一致")
    check(set(d1.keys()) == set(dp.keys()), "字段集一致")
    check(d1.h_true.shape[1:] == dp.h_true.shape[1:], "样本形状一致")
    check(sp["parallel"]["workers"] == 4, "摘要记录了进程数")
    check("逐样本、逐位一致" in (sp["parallel"]["note"] or ""), "摘要声明 worker-count invariant")
    check(sp["parallel"]["fallback_reason"] is None, "并行未降级")

    check(np.array_equal(d1.sinr_dB, dp.sinr_dB),
          "串行与并行的 SINR 逐样本相同（强于分布 KS）")
    check(np.array_equal(d1.h_true, dp.h_true),
          "串行与并行的复信道逐位相同")

    # 同 seed 同 workers 必须可复现
    sp2 = gen.generate(dict(cfg), num_samples=N, workers=4)
    check(np.allclose(dp.sinr_dB, load(sp2["dataset_id"]).sinr_dB),
          "同 seed 同 workers 可复现")

    # -----------------------------------------------------------------------
    sect("11  数据集级链路自适应")

    r = d1.link_adaptation(0)
    print(f"  {r.text().splitlines()[0]}")
    check(r.n_re > 0 and r.tbs_bits > 0, "单样本链路自适应可用")
    st = d1.throughput(max_samples=12)
    print(f"  {st.text().splitlines()[0]}")
    check(st.n == 12, "整批吞吐统计可用")
    check(st.mean_mbps > 0, "吞吐为正")

    # -----------------------------------------------------------------------
    sect("12  门限查表必须与逐档求解逐点等价")
    # select_mcs / select_cqi 的默认路径改成缓存门限表（逐档求 BLER 实测
    # 0.8 ms/次，查表 2 µs）。**加速只有在能证明等价时才成立**，所以这里在
    # 密网格上逐点比对，并单独测门限点本身（二分解落在边界上时最容易分叉）。
    _grid = np.concatenate([np.arange(-30.0, 45.0, 0.01),
                            [float("nan"), float("inf"), float("-inf")]])
    for _tbl in (1, 2, 3):
        _slow = la.CurveBlerModel("newtx") if _tbl == 3 else la.BlerModel()
        _bad = [float(s) for s in _grid
                if la.select_mcs(float(s), table=_tbl, target_bler=0.1).index
                != la.select_mcs(float(s), table=_tbl, target_bler=0.1,
                                 model=_slow).index]
        check(not _bad,
              f"select_mcs 表 {_tbl}：{_grid.size} 点（含 nan/±inf）逐点一致")
        _thr, _ = la._mcs_thresholds(_tbl, 0.1, 20000, 1)
        _bad_thr = [t for t in _thr
                    if la.select_mcs(t, table=_tbl).index
                    != la.select_mcs(t, table=_tbl, model=_slow).index]
        check(not _bad_thr, f"select_mcs 表 {_tbl}：{len(_thr)} 个门限点也一致")
    _slow_cqi = la.BlerModel()
    for _tbl in (1, 2):
        _bad = [float(s) for s in _grid
                if la.select_cqi(float(s), table=_tbl, target_bler=0.1)
                != la.select_cqi(float(s), table=_tbl, target_bler=0.1,
                                 model=_slow_cqi)]
        check(not _bad, f"select_cqi 表 {_tbl}：{_grid.size} 点逐点一致")
    # 自定义 model 必须绕过快路径，否则"换个 BLER 后端"会静默无效。
    class _AlwaysBad:
        def bler(self, s, m, n, c=1):
            return np.asarray([1.0])

    check(la.select_mcs(40.0, table=3, model=_AlwaysBad()).index == 0,
          "传入自定义 model 时不走缓存门限，仍按该 model 判决")

    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    if FAILED:
        print(f"FAILED {len(FAILED)} 项：")
        for f in FAILED:
            print("  -", f)
        sys.exit(1)
    print("链路自适应与并行生成全部通过。")


def test_main_script():
    """pytest 入口：脚本主体全部检查在此执行（失败时 sys.exit(1)）。

    只跑脚本不跑 pytest（或反过来）都会漏掉另一半——两种执行模型必须看到
    同一个真理，这条薄壳就是为此存在的。
    """
    main()


if __name__ == "__main__":
    main()
