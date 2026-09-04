"""Sionna RT 可选信道源的合同测试。

分两类：

* **合成层**（不需要装 sionna）——射线几何到 ``[time, rb, bs, ue]`` 的那一段。
  用构造好的 :class:`RayPaths` 直接驱动，所以每条断言都是确定性的，
  也能在没有 sionna-rt 的机器上跑。
* **端到端**（需要 sionna-rt）——真的追一次 munich，验证阵列合同、
  元数据口径和「不静默回退」。装不上时 skip，不会假装通过。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import channelhub as ch  # noqa: E402
from superran import hardware as hw  # noqa: E402
from superran import native  # noqa: E402
from superran import sionna_rt as srt  # noqa: E402

_HAS_SIONNA = not srt.adapter_missing()
requires_sionna = pytest.mark.skipif(
    not _HAS_SIONNA, reason=f"缺少 {srt.adapter_missing()}；不做替代实现"
)

CARRIER_HZ = 2.6e9
SCS_HZ = 30_000.0

_THETA_T = math.radians(95.0)
_PHI_T = math.radians(20.0)
_THETA_R = math.radians(85.0)
_PHI_R = math.radians(-150.0)


def _company_cfg(**overrides):
    cfg = {
        "bs_panel": [8, 4, 2],
        "ue_panel": [2, 1, 2],
        "carrier_freq_hz": CARRIER_HZ,
        "subcarrier_spacing": SCS_HZ,
    }
    hw.apply_array_defaults(cfg)
    hw.strip_markers(cfg)
    cfg.update(overrides)
    return cfg


def _scalar_cfg():
    """单 RF 端口、单极化、无馈电网络——把阵列因子约掉，只留时频相位。"""
    cfg = _company_cfg(bs_panel=[1, 1, 1], ue_panel=[1, 1, 1])
    cfg["bs_antenna"]["element_pattern"]["polarization_slant_angles_deg"] = [0.0]
    cfg["bs_antenna"]["fixed_vertical_subarray"]["elements_per_rf_port"] = 1
    return cfg


def _one_ray(
    *,
    n_pol_ue=2,
    n_pol_bs=2,
    tau_s=1e-7,
    theta_t=_THETA_T,
    phi_t=_PHI_T,
    theta_r=_THETA_R,
    phi_r=_PHI_R,
    doppler_hz=0.0,
    gain=1.0 + 0.0j,
):
    gains = np.zeros((n_pol_ue, n_pol_bs, 1), dtype=np.complex128)
    gains[:, :, 0] = gain
    return srt.RayPaths(
        gains=gains,
        tau_s=np.asarray([tau_s], dtype=np.float64),
        theta_t_rad=np.asarray([theta_t], dtype=np.float64),
        phi_t_rad=np.asarray([phi_t], dtype=np.float64),
        theta_r_rad=np.asarray([theta_r], dtype=np.float64),
        phi_r_rad=np.asarray([phi_r], dtype=np.float64),
        doppler_hz=np.asarray([doppler_hz], dtype=np.float64),
    )


def _synth(paths, spec, **kw):
    params = {
        "sector_azimuth_deg": 0.0,
        "carrier_freq_hz": CARRIER_HZ,
        "n_time": 1,
        "n_rb": 4,
        "subcarrier_spacing_hz": SCS_HZ,
        "sample_interval_s": 5e-3,
        "normalize": False,
    }
    params.update(kw)
    return srt.synthesize_channel(paths, spec, **params)


# ---------------------------------------------------------------------------
# 合成层：阵列合同
# ---------------------------------------------------------------------------


def test_bs_port_response_is_the_same_array_model_as_the_cdl_path() -> None:
    """RT 必须复用 CDL 那套 1 驱 M 有效子阵，而不是另写一份。

    把修复 revert 成「RT 自己算一个 0.5λ 独立阵元阵」这条断言就会红：
    端口相位中心间距 3x0.67λ 与固定下倾子阵方向图都会消失。
    """
    cfg = _company_cfg()
    spec = srt.array_spec_from_config(cfg, 64, 4)
    assert spec.elements_per_rf_port == 3
    assert spec.bs_port_vertical_spacing_lambda == pytest.approx(3 * 0.67)

    zod = math.radians(95.0)
    aod = math.radians(20.0)
    rays = _one_ray(theta_t=zod, phi_t=aod)
    h = _synth(rays, spec)

    expected_space = native._spatial_panel_response(  # noqa: SLF001
        8, 4, aod, zod,
        horizontal_spacing=spec.bs_horizontal_spacing_lambda,
        vertical_spacing=spec.bs_port_vertical_spacing_lambda,
    ) * native.fixed_subarray_response(
        zod,
        elements_per_rf_port=3,
        ae_vertical_spacing_lambda=0.67,
        fixed_downtilt_deg=spec.fixed_downtilt_deg,
    )
    # 沿 BS 端口轴取一列（固定一个 UE 端口），除掉与 BS 无关的公共因子
    column = h[0, 0, :, 0]
    reference = np.zeros(64, dtype=np.complex128)
    for h_bs in range(8):
        for v_bs in range(4):
            for p_bs in range(2):
                reference[spec.bs_layout.flat(h_bs, v_bs, p_bs)] = expected_space[
                    h_bs * 4 + v_bs
                ]
    ratio = column / reference
    # 合成结果是 complex64，比值的残差量级就是 float32 精度
    assert np.allclose(ratio, ratio[0], rtol=1e-6, atol=1e-7)
    assert np.abs(ratio[0]) > 0


def test_fixed_downtilt_actually_changes_the_ray_weighting() -> None:
    """1 驱 3 的固定下倾必须影响 RT 信道，否则馈电网络等于没接上。"""
    base = _company_cfg()
    tilted = _company_cfg()
    tilted["bs_antenna"]["fixed_vertical_subarray"]["fixed_downtilt_deg"] = 0.0
    spec_a = srt.array_spec_from_config(base, 64, 4)
    spec_b = srt.array_spec_from_config(tilted, 64, 4)
    assert spec_a.fixed_downtilt_deg == 6.0
    assert spec_b.fixed_downtilt_deg == 0.0
    rays = _one_ray(theta_t=math.radians(75.0))
    h_a = _synth(rays, spec_a)
    h_b = _synth(rays, spec_b)
    assert not np.allclose(h_a, h_b)


def test_delay_becomes_a_linear_phase_slope_across_rb() -> None:
    """时延必须变成频域线性相位；斜率就是 -2 pi tau。"""
    spec = srt.array_spec_from_config(_scalar_cfg(), 1, 1)
    tau = 3.7e-7
    rays = _one_ray(n_pol_ue=1, n_pol_bs=1, tau_s=tau)
    h = _synth(rays, spec, n_rb=8)[0, :, 0, 0]
    df = 12.0 * SCS_HZ
    measured = np.angle(h[1:] * np.conj(h[:-1]))
    expected = ((-2.0 * np.pi * tau * df) + np.pi) % (2.0 * np.pi) - np.pi
    assert np.allclose(measured, expected, atol=1e-5)


def test_carrier_phase_term_is_present() -> None:
    """径间相对相位来自载波项；漏了它 RT 的多径叠加就是错的。"""
    spec = srt.array_spec_from_config(_scalar_cfg(), 1, 1)
    tau = 1.0 / CARRIER_HZ / 4.0  # 恰好四分之一个载波周期
    rays = _one_ray(n_pol_ue=1, n_pol_bs=1, tau_s=tau)
    h = _synth(rays, spec, n_rb=1)[0, 0, 0, 0]
    baseband_only = np.exp(-2j * np.pi * 0.0 * tau)
    assert not np.isclose(h, baseband_only)
    assert np.isclose(h, np.exp(-2j * np.pi * CARRIER_HZ * tau), atol=1e-6)


def test_doppler_becomes_a_time_phase_ramp() -> None:
    spec = srt.array_spec_from_config(_scalar_cfg(), 1, 1)
    fd, dt = 37.0, 5e-3
    rays = _one_ray(n_pol_ue=1, n_pol_bs=1, tau_s=0.0, doppler_hz=fd)
    h = _synth(rays, spec, n_rb=1, n_time=3, sample_interval_s=dt)[:, 0, 0, 0]
    step = np.angle(h[1] * np.conj(h[0]))
    assert step == pytest.approx(((2 * np.pi * fd * dt) + np.pi) % (2 * np.pi) - np.pi, abs=1e-6)


def test_polarization_slot_order_follows_the_superran_contract() -> None:
    """gains 的第 0 个极化下标必须落在配置里第 0 个倾角的端口块上。

    Sionna 自带的 "cross" 是 [-45, +45]，SuperRAN 配置是 [+45, -45]。
    把 _polarization_name() 换回 "cross" 这条断言就会红。
    """
    cfg = _company_cfg()
    spec = srt.array_spec_from_config(cfg, 64, 4)
    gains = np.zeros((2, 2, 1), dtype=np.complex128)
    gains[0, 0, 0] = 1.0  # 只有 (UE 极化 0, BS 极化 0) 有能量
    rays = srt.RayPaths(
        gains=gains,
        tau_s=np.asarray([0.0]),
        theta_t_rad=np.asarray([math.radians(95.0)]),
        phi_t_rad=np.asarray([0.0]),
        theta_r_rad=np.asarray([math.radians(85.0)]),
        phi_r_rad=np.asarray([0.0]),
        doppler_hz=np.asarray([0.0]),
    )
    h = _synth(rays, spec)[0, 0]
    pol0_bs = [spec.bs_layout.flat(i, j, 0) for i in range(8) for j in range(4)]
    pol1_bs = [spec.bs_layout.flat(i, j, 1) for i in range(8) for j in range(4)]
    pol0_ue = [spec.ue_layout.flat(i, 0, 0) for i in range(2)]
    pol1_ue = [spec.ue_layout.flat(i, 0, 1) for i in range(2)]
    assert np.abs(h[np.ix_(pol0_bs, pol0_ue)]).max() > 0
    assert np.abs(h[np.ix_(pol1_bs, pol1_ue)]).max() == 0
    assert np.abs(h[np.ix_(pol0_bs, pol1_ue)]).max() == 0


def test_sector_azimuth_rotates_only_the_bs_array() -> None:
    cfg = _company_cfg()
    spec = srt.array_spec_from_config(cfg, 64, 4)
    rays = _one_ray(phi_t=math.radians(30.0))
    a = _synth(rays, spec, sector_azimuth_deg=0.0)
    b = _synth(rays, spec, sector_azimuth_deg=30.0)
    c = _synth(
        _one_ray(phi_t=0.0), spec, sector_azimuth_deg=0.0
    )
    assert not np.allclose(a, b)
    # 把扇区法向转到与径方位角一致，等价于径本来就在法向上
    assert np.allclose(b, c, atol=1e-9)


def test_gain_shape_mismatch_is_rejected_not_broadcast() -> None:
    cfg = _company_cfg()
    spec = srt.array_spec_from_config(cfg, 64, 4)
    rays = _one_ray(n_pol_ue=1, n_pol_bs=1)
    with pytest.raises(ValueError, match="极化维"):
        _synth(rays, spec)


def test_all_zero_channel_is_reported_not_silently_normalized() -> None:
    cfg = _company_cfg()
    spec = srt.array_spec_from_config(cfg, 64, 4)
    rays = _one_ray(gain=0.0 + 0.0j)
    with pytest.raises(ValueError, match="全为零"):
        _synth(rays, spec, normalize=True)


# ---------------------------------------------------------------------------
# 不静默回退
# ---------------------------------------------------------------------------


def test_missing_sionna_raises_and_never_returns_a_statistical_source(monkeypatch) -> None:
    monkeypatch.setattr(srt, "adapter_missing", lambda: ["sionna", "mitsuba"])
    ch.probe_capabilities.cache_clear()
    try:
        caps = {c.name: c for c in ch.probe_capabilities()}
        assert caps["sionna_rt"].available is False
        assert caps["sionna_rt"].missing == ["sionna", "mitsuba"]
        assert caps["internal_sim"].available is True
        with pytest.raises(RuntimeError, match="sionna_rt"):
            ch.require_source("sionna_rt")
        with pytest.raises(RuntimeError, match="sionna"):
            srt._ensure_sionna()  # noqa: SLF001
    finally:
        ch.probe_capabilities.cache_clear()


def test_engine_list_is_always_three_entries() -> None:
    ch.probe_capabilities.cache_clear()
    names = [c.name for c in ch.probe_capabilities()]
    assert names == ["internal_sim", "sionna_rt", "quadriga_real"]


def test_default_source_is_still_the_statistical_channel() -> None:
    """默认信道必须还是 CDL；RT 装上了也不能改变默认档。"""
    assert "internal_sim" in native.SOURCE_REGISTRY
    assert "sionna_rt" not in native.SOURCE_REGISTRY
    assert native.InternalSimSource({}).describe()["source"] == "internal_sim"


def test_serving_outage_raises_while_interferer_outage_is_a_zero_channel(monkeypatch) -> None:
    """服务链路全遮挡是必须报出来的覆盖空洞，干扰小区全遮挡只是没有干扰。"""
    cfg = _company_cfg(
        num_ues=1, num_samples=1, num_sites=1, sectors_per_site=3, scene="munich",
        num_bs_tx_ant=64, num_bs_rx_ant=64, num_ue_tx_ant=4, num_ue_rx_ant=4,
    )
    source = srt.SionnaRTSource(cfg)
    monkeypatch.setattr(source, "_solve", lambda sites, position: [None])
    monkeypatch.setattr(source, "_cell_to_source", [0, 0, 0])
    cell = native.Cell(np.asarray([0.0, 0.0, 25.0]), 0.0, 0, 0)
    kwargs = dict(
        n_time=1, n_rb=4, n_bs=64, n_ue=4, doppler_hz=0.0, realization_index=0,
        link_aod_rad=0.0, link_aoa_rad=0.0, link_zod_rad=1.5, link_zoa_rad=1.5,
        cell=cell, ue_position=np.asarray([10.0, 0.0, 1.5]), is_los=False,
    )
    with pytest.raises(RuntimeError, match="没有追到任何径"):
        source._small_scale_channel(None, None, role="serving", **kwargs)  # noqa: SLF001
    zeros = source._small_scale_channel(None, None, role="interferer", **kwargs)  # noqa: SLF001
    assert zeros.shape == (1, 4, 64, 4)
    assert not zeros.any()


# ---------------------------------------------------------------------------
# 端到端（需要 sionna-rt）
# ---------------------------------------------------------------------------

# munich 场景里实测有覆盖的三个位置（SuperRAN 局部坐标，场景平移由适配层加）。
# 这三个点是扫描出来的，不是猜的；换场景要重新扫。
MUNICH_COVERED_UES = [
    [44.29, 53.47, 1.5],
    [-58.96, -120.07, 1.5],
    [-1.96, 114.2, 1.5],
]


def _rt_cfg(**overrides):
    cfg = _company_cfg(
        source="sionna_rt", scene="munich",
        num_samples=2, num_ues=2, num_rb=8, num_slots_per_sample=2,
        num_bs_tx_ant=64, num_bs_rx_ant=64, num_ue_tx_ant=4, num_ue_rx_ant=4,
        scenario="UMa_NLOS", channel_est_mode="ls_linear", link="BOTH",
        seed=903, ue_seed=904, num_sites=1, sectors_per_site=3,
        isd_m=200.0, tx_height_m=45.0, ue_height_m=1.5,
        custom_ue_positions=MUNICH_COVERED_UES[:2],
        rt_max_depth=4, rt_samples_per_src=1_000_000,
        measurements={"ssb_rsrp": True, "interferer_channels": True},
    )
    cfg.update(overrides)
    return cfg


@requires_sionna
def test_real_ray_tracing_produces_a_contract_conformant_sample() -> None:
    samples = list(ch.iter_samples("sionna_rt", _rt_cfg()))
    assert len(samples) == 2
    for s in samples:
        assert s.source == "sionna_rt"
        assert s.meta["channel_generation_mode"] == "sionna_rt"
        assert s.meta["implementation"] == "superran-first-party-adapter+sionna-rt"
        assert s.meta["rt_num_paths_serving"] >= 1
        assert s.meta["rt_array_model"] == "superran-effective-subarray-shared-with-cdl"
        assert s.meta["antenna_profile"] == "fixed_1to3_vertical_subarray_64T"
        # 统计信道的簇口径在 RT 下没有意义，必须被摘掉而不是留个假值
        for key in ("num_taps", "rician_k_db", "tau_rms_ns"):
            assert key not in s.meta
        h = s.h_dl_true
        assert h.shape == (2, 8, 64, 4)
        assert np.isfinite(h).all()
        assert float(np.mean(np.abs(h) ** 2)) == pytest.approx(1.0, rel=1e-5)
        assert not np.array_equal(h[0], h[1])  # 多普勒让时隙之间不一样
        assert not np.array_equal(s.h_dl_true, s.h_dl_est)


@requires_sionna
def test_ray_traced_channel_differs_from_the_cdl_channel_on_the_same_geometry() -> None:
    """同一套几何、同一套阵列，只换生成引擎——信道必须不同，其余口径必须相同。"""
    rt_cfg = _rt_cfg()
    cdl_cfg = dict(rt_cfg)
    cdl_cfg.pop("source", None)
    cdl_cfg.pop("scene", None)
    cdl_cfg["channel_model"] = "CDL-C"
    rt_samples = list(ch.iter_samples("sionna_rt", rt_cfg))
    cdl_samples = list(ch.iter_samples("internal_sim", cdl_cfg))
    for a, b in zip(rt_samples, cdl_samples, strict=True):
        assert a.h_dl_true.shape == b.h_dl_true.shape
        assert not np.allclose(a.h_dl_true, b.h_dl_true)
        # 接缝以上的量必须逐位相同，否则 CDL 与 RT 的对比不可归因
        assert a.meta["pathloss_dB"] == b.meta["pathloss_dB"]
        assert a.snr_dB == b.snr_dB
        assert a.sir_dB == b.sir_dB
        assert a.sinr_dB == b.sinr_dB
        assert a.serving_cell_id == b.serving_cell_id
        np.testing.assert_array_equal(a.ue_position, b.ue_position)


@requires_sionna
def test_synthesis_matches_sionna_own_frequency_response() -> None:
    """单端口单极化下，我们的合成必须等于 Sionna 自己的 ``Paths.cfr()``。

    这是整条链路的物理锚点：时延、载波相位、多普勒三个约定只要有一个错，
    误差就会跳到 O(1)。容差取 2e-3 是 Sionna 内部 float32 相位精度的量级
    （载波项 2 pi f_c tau 在 f_c=2.6 GHz、tau~1 us 时是 1e4 量级的角度）。
    """
    import sionna.rt as rt
    from sionna.rt import scene as builtin

    scene = rt.load_scene(builtin.munich, merge_shapes=True)
    scene.frequency = CARRIER_HZ
    probe = rt.PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    scene.tx_array = probe
    scene.rx_array = probe
    bbox = scene.mi_scene.bbox()
    off = np.asarray(
        [(bbox.min[0] + bbox.max[0]) / 2.0, (bbox.min[1] + bbox.max[1]) / 2.0, 0.0]
    )
    scene.add(rt.Transmitter(name="bs", position=[float(off[0]), float(off[1]), 45.0]))
    ue = off + np.asarray(MUNICH_COVERED_UES[0])
    scene.add(
        rt.Receiver(name="ue", position=[float(ue[0]), float(ue[1]), 1.5],
                    velocity=[8.0, 0.0, 0.0])
    )
    solved = rt.PathSolver()(
        scene, max_depth=4, samples_per_src=1_000_000, synthetic_array=True,
        los=True, specular_reflection=True, diffuse_reflection=False,
        refraction=True, seed=41,
    )
    per_cell = srt.SionnaRTSource._split_paths(solved, 1)  # noqa: SLF001
    rays = per_cell[0]
    assert rays is not None and rays.num_paths >= 1

    spec = srt.array_spec_from_config(_scalar_cfg(), 1, 1)

    n_rb, n_time, dt = 8, 3, 5e-3
    mine = srt.synthesize_channel(
        rays, spec, sector_azimuth_deg=0.0, carrier_freq_hz=CARRIER_HZ,
        n_time=n_time, n_rb=n_rb, subcarrier_spacing_hz=SCS_HZ,
        sample_interval_s=dt, normalize=False,
    )[:, :, 0, 0]
    freqs = (np.arange(n_rb) - (n_rb - 1.0) / 2.0) * 12.0 * SCS_HZ
    reference = np.asarray(
        solved.cfr(frequencies=freqs, sampling_frequency=1.0 / dt,
                   num_time_steps=n_time, normalize_delays=False,
                   normalize=False, out_type="numpy")
    )[0, 0, 0, 0]
    scale = np.abs(reference).max()
    assert scale > 0
    assert np.abs(mine - reference).max() / scale < 2e-3


@requires_sionna
def test_cosited_sectors_share_one_ray_trace() -> None:
    """同站三扇区共用一次追踪：传播环境相同，差别只在天线朝向。"""
    samples = list(ch.iter_samples("sionna_rt", _rt_cfg(num_samples=1, num_ues=1)))
    diag = samples[0].meta["rt_link_diagnostics"]
    assert len(diag) == 3
    assert len({row["num_paths"] for row in diag}) == 1
    assert len({round(row["rt_pathloss_db"], 9) for row in diag}) == 1
