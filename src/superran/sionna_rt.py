"""SuperRAN 自己的 Sionna RT 直连信道源（可选引擎）。

默认信道仍然是 38.901 统计信道（``internal_sim`` 的 CDL/TDL）。这个模块
提供第二种**生成信道矩阵**的方式：多径来自真实建筑几何的射线追踪，而不是
标准表里的簇。

**只换信道矩阵，别的一律不换。**
站点布局、撒点、LOS 抽样、路损、阴影衰落、服务小区选择、预波束 S/N/I 预算、
估计噪声、SSB、TDD 成对与元数据全部沿用 :class:`superran.native.InternalSimSource`；
本模块只覆写 ``_small_scale_channel`` 这一个接缝。所以 CDL 与 RT 的 KPI 差异
可以归因到信道模型本身，而不是被口径差异污染。

**1 驱 3 / 1 驱 6 架构完全不变。**
BS 侧的 RF 端口阵因子走 :func:`superran.native._spatial_panel_response`
（端口相位中心间距 = ``elements_per_rf_port × ae_vertical_spacing_lambda``），
固定子阵方向图走 :func:`superran.native.fixed_subarray_response` ——
和 CDL 路径用的是同一个函数，不是抄一份。射线追踪只负责给出每条径的
到达/离开角、时延、多普勒和 2x2 极化耦合矩阵。

边界（这些是有意为之，不是遗漏）：

* **大尺度仍走 38.901 公式**。RT 算出的路损与 LOS 判定只写进 meta 作为旁证
  （``rt_pathloss_db`` / ``rt_is_los``），不驱动链路预算。理由是让 CDL↔RT
  的对比只差信道矩阵一项；要让 RT 接管大尺度必须是另一次显式改动。
* **不做任何回退**。Sionna 装不上、场景不认识、服务链路一条径都追不到，
  都直接抛错，绝不悄悄退回 TDL/CDL —— 那正是历史上外部适配层踩过的坑。
* **UE 侧不叠 CDL 路径里那个人造的空间相关矩阵**。RT 的空间相关是真的。
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np

from .native import (
    Cell,
    ChannelSample,
    InternalSimSource,
    PortIndex,
    _panel_shape,
    _spatial_panel_response,
    fixed_subarray_response,
)

SIONNA_RT_CONTRACT_VERSION = "superran-sionna-rt-direct-v1"

#: 与 :data:`superran.scenes.BUILTIN_SCENES` 对齐的 Sionna 自带场景。
BUILTIN_SCENE_NAMES = ("munich", "etoile", "florence", "san_francisco")

_REQUIRED_TOP_LEVEL_MODULES = ("sionna", "mitsuba", "drjit")

_C = 299_792_458.0
_EPS = 1e-30


# ---------------------------------------------------------------------------
# 可用性探测：只探顶层包名，绝不 import sionna
# ---------------------------------------------------------------------------


def _probe_top_level(name: str) -> bool:
    """``find_spec`` 只探顶层名字。

    探 ``sionna.rt`` 会为了拿父包 ``__path__`` 真的 import ``sionna``，
    连带拉起 mitsuba / drjit / matplotlib（历史实测 +455 MB）。MCP 服务端
    每个 CLI 会话一个进程，绝大多数进程一次 RT 都不跑，不该付这笔钱。
    """
    try:
        return importlib.util.find_spec(str(name).split(".")[0]) is not None
    except (ImportError, ValueError):
        return False


def adapter_missing() -> list[str]:
    """返回缺失项；空列表表示这台机器可以跑 RT。"""
    return [name for name in _REQUIRED_TOP_LEVEL_MODULES if not _probe_top_level(name)]


def _ensure_sionna() -> Any:
    """真正要跑 RT 时才 import。缺依赖时给出可执行的修复建议。"""
    missing = adapter_missing()
    if missing:
        raise RuntimeError(
            "Sionna RT 信道源需要 " + ", ".join(missing) + "；"
            "装法：pip install sionna-rt（连带 mitsuba + drjit，约 300 MB）。"
            "不会退回统计信道。"
        )
    import sionna.rt as rt  # noqa: PLC0415

    return rt


# ---------------------------------------------------------------------------
# 引擎无关的径集合：合成信道时只依赖它，所以合成逻辑可以脱离 Sionna 单测
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RayPaths:
    """一条 BS-UE 链路的射线集合，全部为全局球坐标（弧度）与秒。

    ``gains`` 的形状是 ``[n_pol_ue, n_pol_bs, n_paths]``，已按 SuperRAN 的
    极化槽顺序排好（``polarization_slant_angles_deg`` 的第 0 项对应下标 0）。
    """

    gains: np.ndarray
    tau_s: np.ndarray
    theta_t_rad: np.ndarray
    phi_t_rad: np.ndarray
    theta_r_rad: np.ndarray
    phi_r_rad: np.ndarray
    doppler_hz: np.ndarray

    @property
    def num_paths(self) -> int:
        return int(self.tau_s.size)

    def validate(self) -> None:
        n = self.num_paths
        for name in ("tau_s", "theta_t_rad", "phi_t_rad", "theta_r_rad",
                     "phi_r_rad", "doppler_hz"):
            arr = np.asarray(getattr(self, name))
            if arr.shape != (n,):
                raise ValueError(f"RayPaths.{name} 形状应为 ({n},)，实得 {arr.shape}")
            if not np.isfinite(arr).all():
                raise ValueError(f"RayPaths.{name} 含 NaN 或 Inf")
        gains = np.asarray(self.gains)
        if gains.ndim != 3 or gains.shape[-1] != n:
            raise ValueError(
                f"RayPaths.gains 形状应为 [n_pol_ue, n_pol_bs, {n}]，实得 {gains.shape}"
            )
        if not np.isfinite(gains).all():
            raise ValueError("RayPaths.gains 含 NaN 或 Inf")


@dataclass(frozen=True)
class ArraySpec:
    """把 SuperRAN 的阵列合同压成合成信道需要的最小集合。"""

    bs_shape: tuple[int, int, int]
    ue_shape: tuple[int, int, int]
    bs_layout: PortIndex
    ue_layout: PortIndex
    bs_horizontal_spacing_lambda: float
    elements_per_rf_port: int
    ae_vertical_spacing_lambda: float
    fixed_downtilt_deg: float

    @property
    def bs_port_vertical_spacing_lambda(self) -> float:
        """RF 端口相位中心间距：1 驱 M 把 M 个 0.67λ 阵子并成一个端口。"""
        return float(self.elements_per_rf_port) * float(self.ae_vertical_spacing_lambda)


def array_spec_from_config(cfg: dict[str, Any], n_bs: int, n_ue: int) -> ArraySpec:
    """从生成配置里取出与 CDL 路径**完全相同**的阵列参数。"""
    bs_shape = _panel_shape(n_bs, cfg.get("bs_panel"))
    ue_shape = _panel_shape(n_ue, cfg.get("ue_panel"))
    bs_ant = dict(cfg.get("bs_antenna") or {})
    subarray = dict(bs_ant.get("fixed_vertical_subarray") or {})
    return ArraySpec(
        bs_shape=bs_shape,
        ue_shape=ue_shape,
        bs_layout=PortIndex(*bs_shape, "pol_h_v", "top_to_bottom"),
        ue_layout=PortIndex(*ue_shape, "pol_h_v", "top_to_bottom"),
        bs_horizontal_spacing_lambda=float(
            bs_ant.get("horizontal_port_spacing_lambda", 0.5) or 0.5
        ),
        elements_per_rf_port=int(subarray.get("elements_per_rf_port", 1) or 1),
        ae_vertical_spacing_lambda=float(
            subarray.get("ae_vertical_spacing_lambda", 0.67) or 0.67
        ),
        fixed_downtilt_deg=float(subarray.get("fixed_downtilt_deg", 0.0) or 0.0),
    )


# ---------------------------------------------------------------------------
# 合成：径几何 -> [time, rb, bs_port, ue_port]
# ---------------------------------------------------------------------------


def synthesize_channel(
    paths: RayPaths,
    spec: ArraySpec,
    *,
    sector_azimuth_deg: float,
    carrier_freq_hz: float,
    n_time: int,
    n_rb: int,
    subcarrier_spacing_hz: float,
    sample_interval_s: float,
    normalize: bool = True,
) -> np.ndarray:
    """把射线集合合成为 ``[time, rb, bs_port, ue_port]`` 信道张量。

    每条径:

    .. math::
        H \\mathrel{+}= g_{p}[u,b]\\,
            e^{j2\\pi f_{d,p} t}\\,
            e^{-j2\\pi (f_c + f)\\tau_p}\\,
            a^{\\mathrm{BS}}_{p}\\, \\overline{a^{\\mathrm{UE}}_{p}}

    载波项 :math:`e^{-j2\\pi f_c \\tau_p}` 不能省：CDL 的时延是合成的、各簇
    相位另有随机项，而 RT 的径长差是真实的，径间相对相位就来自这一项。
    这与 Sionna 自己的 ``Paths.cfr()`` 口径一致（实测相对误差 4e-4，量级
    等于 Sionna 内部 float32 的相位精度）。

    ``normalize`` 为真时把平均功率归一到 1，与 CDL 路径一致——大尺度链路
    预算由上层的 38.901 路损承担，信道矩阵只携带小尺度结构。
    """
    paths.validate()
    n_bs = int(np.prod(spec.bs_shape))
    n_ue = int(np.prod(spec.ue_shape))
    if paths.gains.shape[:2] != (spec.ue_shape[2], spec.bs_shape[2]):
        raise ValueError(
            "RayPaths.gains 的极化维与阵列合同不符："
            f"{paths.gains.shape[:2]} vs ({spec.ue_shape[2]}, {spec.bs_shape[2]})"
        )

    freq = (np.arange(int(n_rb), dtype=np.float64) - (int(n_rb) - 1.0) / 2.0) * 12.0 * float(
        subcarrier_spacing_hz
    )
    times = np.arange(int(n_time), dtype=np.float64) * float(sample_interval_s)
    absolute_freq = float(carrier_freq_hz) + freq

    h = np.zeros((int(n_time), int(n_rb), n_bs, n_ue), dtype=np.complex128)
    sector = math.radians(float(sector_azimuth_deg))

    for p in range(paths.num_paths):
        # BS 阵因子用**相对扇区法向**的方位角；天顶角本来就是全局量。
        aod_local = (float(paths.phi_t_rad[p]) - sector + np.pi) % (2.0 * np.pi) - np.pi
        zod = float(paths.theta_t_rad[p])
        aoa = float(paths.phi_r_rad[p])
        zoa = float(paths.theta_r_rad[p])

        bs_space = _spatial_panel_response(
            spec.bs_shape[0],
            spec.bs_shape[1],
            aod_local,
            zod,
            horizontal_spacing=spec.bs_horizontal_spacing_lambda,
            vertical_spacing=spec.bs_port_vertical_spacing_lambda,
        ) * fixed_subarray_response(
            zod,
            elements_per_rf_port=spec.elements_per_rf_port,
            ae_vertical_spacing_lambda=spec.ae_vertical_spacing_lambda,
            fixed_downtilt_deg=spec.fixed_downtilt_deg,
        )
        ue_space = _spatial_panel_response(
            spec.ue_shape[0], spec.ue_shape[1], aoa, zoa,
            horizontal_spacing=0.5, vertical_spacing=0.5,
        )

        spatial = np.zeros((n_bs, n_ue), dtype=np.complex128)
        for p_bs in range(spec.bs_shape[2]):
            for p_ue in range(spec.ue_shape[2]):
                coupling = complex(paths.gains[p_ue, p_bs, p])
                for h_bs in range(spec.bs_shape[0]):
                    for v_bs in range(spec.bs_shape[1]):
                        b = spec.bs_layout.flat(h_bs, v_bs, p_bs)
                        b_space = bs_space[h_bs * spec.bs_shape[1] + v_bs]
                        for h_ue in range(spec.ue_shape[0]):
                            for v_ue in range(spec.ue_shape[1]):
                                u = spec.ue_layout.flat(h_ue, v_ue, p_ue)
                                u_space = ue_space[h_ue * spec.ue_shape[1] + v_ue]
                                spatial[b, u] = coupling * b_space * np.conj(u_space)

        delay_phase = np.exp(-2j * np.pi * absolute_freq * float(paths.tau_s[p]))
        time_phase = np.exp(2j * np.pi * float(paths.doppler_hz[p]) * times)
        h += (
            time_phase[:, None, None, None]
            * delay_phase[None, :, None, None]
            * spatial[None, None]
        )

    if normalize:
        power = float(np.mean(np.abs(h) ** 2))
        if power <= 0.0:
            raise ValueError("合成信道全为零，无法归一化；调用方应先处理零径链路")
        h /= math.sqrt(power)
    return h.astype(np.complex64)


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------


class SionnaRTSource(InternalSimSource):
    """把 ``internal_sim`` 的小尺度信道换成 Sionna 射线追踪的结果。

    额外配置键（其余与 ``internal_sim`` 完全一致）::

        scene                  场景名，见 BUILTIN_SCENE_NAMES，或本地资产目录
        rt_max_depth           最大反射阶数，默认 3
        rt_samples_per_src     每个源的采样射线数，默认 1_000_000
        rt_specular_reflection 默认 True
        rt_diffuse_reflection  默认 False（打开会显著变慢）
        rt_refraction          默认 True
        rt_edge_diffraction    默认 False
        rt_seed                求解器种子，默认取 ``seed``
    """

    #: 服务链路一条径都没有时的行为。故意不提供 "fallback"。
    OUTAGE_POLICY = "error"

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        self._scene_name = str(self.cfg.get("scene", "munich"))
        self._rt_cache: dict[tuple[int, ...], list[RayPaths | None]] = {}
        self._scene = None
        self._sites_in_scene: list[Cell] | None = None
        self._pending_diag: list[dict[str, Any]] = []
        self._sample_diag: list[dict[str, Any]] = []
        # 注意不要叫 _offset：父类 InternalSimSource 用 _offset 存
        # sample_index_offset，同名会静默改掉全局样本编号。
        self._scene_offset_m = np.zeros(3, dtype=np.float64)
        self._cell_to_source: list[int] = []

    # -- 场景 ---------------------------------------------------------------

    def _scene_file(self) -> Any:
        rt = _ensure_sionna()
        from sionna.rt import scene as builtin  # noqa: PLC0415

        name = self._scene_name
        if name in BUILTIN_SCENE_NAMES:
            return getattr(builtin, name)
        from .scenes import prepare_scene  # noqa: PLC0415

        prepared = prepare_scene(name)
        path = prepared.get("scene_file") if isinstance(prepared, dict) else None
        if not path:
            raise RuntimeError(
                f"场景 {name!r} 不是 Sionna 自带场景，且本地资产里没有可用的 scene 文件。"
                f"自带场景：{', '.join(BUILTIN_SCENE_NAMES)}；本地资产用 SUPERRAN_SCENES 指向。"
            )
        del rt
        return str(path)

    def _polarization_name(self) -> str:
        """按 SuperRAN 配置的极化槽顺序注册一个 Sionna 极化。

        Sionna 自带的 ``"cross"`` 是 ``[-45°, +45°]``，而 SuperRAN 的
        ``polarization_slant_angles_deg`` 默认是 ``[+45°, -45°]``——顺序相反。
        直接用 ``"cross"`` 会把两个极化端口块整体对调，所以这里按配置注册，
        保证下标 0 永远是配置里的第 0 个倾角。
        """
        from sionna.rt.antenna_pattern import (  # noqa: PLC0415
            polarization_registry,
            register_polarization,
        )

        slants = self._slant_angles_deg()
        key = "superran_" + "_".join(f"{s:+.1f}".replace(".", "p") for s in slants)
        if key not in polarization_registry.list():
            register_polarization(key, [float(math.radians(s)) for s in slants])
        return key

    def _slant_angles_deg(self) -> tuple[float, ...]:
        pattern = dict((self.cfg.get("bs_antenna") or {}).get("element_pattern") or {})
        slants = pattern.get("polarization_slant_angles_deg") or (45.0, -45.0)
        values = tuple(float(v) for v in slants)
        if len(values) not in (1, 2):
            raise ValueError("Sionna RT 适配层只支持 1 或 2 个极化倾角")
        return values

    def _scene_offset(self, scene: Any) -> np.ndarray:
        """把 SuperRAN 的拓扑平移到场景中心（水平方向）。

        SuperRAN 的六边形栅格以坐标原点为中心，而 Sionna 自带场景的原点未必
        在城区里——munich 的包围盒中心是 (-68, -86)，把站点摆在 (0,0) 会落到
        建筑密集区边上，实测 40 个随机 UE 只有 6 个能追到径。按包围盒中心平移
        之后，四个自带场景的覆盖率都回到 10~12/12。

        平移只作用于**送进射线追踪的坐标**。SuperRAN 自己的几何、距离、路损、
        撒点全部仍在原坐标系里，距离是平移不变量，所以链路预算逐位不变。
        配置 ``rt_scene_offset_m`` 可以显式覆盖（写 ``[0, 0]`` 表示不平移）。
        """
        explicit = self.cfg.get("rt_scene_offset_m")
        if explicit is not None:
            values = [float(v) for v in explicit]
            if len(values) == 2:
                values.append(0.0)
            if len(values) != 3:
                raise ValueError("rt_scene_offset_m 需要 2 或 3 个分量")
            return np.asarray(values, dtype=np.float64)
        bbox = scene.mi_scene.bbox()
        return np.asarray(
            [
                (float(bbox.min[0]) + float(bbox.max[0])) / 2.0,
                (float(bbox.min[1]) + float(bbox.max[1])) / 2.0,
                0.0,
            ],
            dtype=np.float64,
        )

    def _build_scene(self, sites: list[Cell]) -> Any:
        rt = _ensure_sionna()
        scene = rt.load_scene(self._scene_file(), merge_shapes=True)
        scene.frequency = float(self.cfg.get("carrier_freq_hz", 3.5e9) or 3.5e9)
        self._scene_offset_m = self._scene_offset(scene)
        pol = self._polarization_name()
        # 阵列建模留在 SuperRAN 侧：这里只要一个单元双极化探针，用来取出
        # 每条径的 2x2 极化耦合。RF 端口阵因子与 1 驱 M 子阵由
        # synthesize_channel 用与 CDL 完全相同的函数合成。
        probe = rt.PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization=pol)
        scene.tx_array = probe
        scene.rx_array = probe
        # 同一物理站的三个扇区共用一个位置，传播环境完全相同——差别只在
        # 天线朝向，而朝向是在 synthesize_channel 里按 sector_azimuth 转的。
        # 所以按位置去重，21 小区只追 7 次，而不是 21 次。
        self._cell_to_source = []
        seen: dict[tuple[int, int, int], int] = {}
        for cell in sites:
            key = tuple(int(round(float(v) * 1000.0)) for v in cell.position)
            if key not in seen:
                seen[key] = len(seen)
                scene.add(
                    rt.Transmitter(
                        name=f"site{seen[key]}",
                        position=[
                            float(v)
                            for v in (np.asarray(cell.position) + self._scene_offset_m)
                        ],
                    )
                )
            self._cell_to_source.append(seen[key])
        return scene

    # -- 求解 ---------------------------------------------------------------

    def _velocity(self) -> list[float]:
        speed = max(float(self.cfg.get("ue_speed_kmh", 3.0) or 0.0), 0.0) / 3.6
        if speed <= 0.0:
            return [0.0, 0.0, 0.0]
        heading = math.radians(
            float(self.cfg.get("ue_heading_deg", self.cfg.get("track_heading_deg", 0.0)) or 0.0)
        )
        return [speed * math.cos(heading), speed * math.sin(heading), 0.0]

    def _solve(self, sites: list[Cell], position: np.ndarray) -> list[RayPaths | None]:
        """对一个 UE 位置一次性追踪**全部**站点，按小区下标返回径集合。

        一次求解拿到所有小区，而不是每条链路解一次——干扰小区的径本来就是
        同一次场景遍历的产物，分开解只是白花时间。
        """
        key = tuple(int(round(float(v) * 1000.0)) for v in position)
        cached = self._rt_cache.get(key)
        if cached is not None:
            return cached

        rt = _ensure_sionna()
        if self._scene is None:
            self._scene = self._build_scene(sites)
        scene = self._scene
        if "ue" in scene.receivers:
            scene.remove("ue")
        scene.add(
            rt.Receiver(
                name="ue",
                position=[float(v) for v in (np.asarray(position) + self._scene_offset_m)],
                velocity=self._velocity(),
            )
        )
        solver = rt.PathSolver()
        solved = solver(
            scene,
            max_depth=int(self.cfg.get("rt_max_depth", 3) or 3),
            samples_per_src=int(self.cfg.get("rt_samples_per_src", 1_000_000) or 1_000_000),
            synthetic_array=True,
            los=True,
            specular_reflection=bool(self.cfg.get("rt_specular_reflection", True)),
            diffuse_reflection=bool(self.cfg.get("rt_diffuse_reflection", False)),
            refraction=bool(self.cfg.get("rt_refraction", True)),
            edge_diffraction=bool(self.cfg.get("rt_edge_diffraction", False)),
            seed=int(self.cfg.get("rt_seed", self._seed) or 0),
        )
        out = self._split_paths(solved, len(set(self._cell_to_source)))
        self._rt_cache[key] = out
        return out

    @staticmethod
    def _split_paths(solved: Any, num_cells: int) -> list[RayPaths | None]:
        """把 Sionna 的 ``[num_rx, num_rx_ant, num_tx, num_tx_ant, num_paths]``
        拆成逐小区的 :class:`RayPaths`，并丢掉无效径。"""
        gains = np.asarray(solved.a[0]) + 1j * np.asarray(solved.a[1])
        tau = np.asarray(solved.tau)
        theta_t = np.asarray(solved.theta_t)
        phi_t = np.asarray(solved.phi_t)
        theta_r = np.asarray(solved.theta_r)
        phi_r = np.asarray(solved.phi_r)
        valid = np.asarray(solved.valid)
        doppler = np.asarray(solved.doppler)

        out: list[RayPaths | None] = []
        for tx in range(num_cells):
            mask = valid[0, tx, :] if valid.size else np.zeros((0,), dtype=bool)
            mask = np.asarray(mask, dtype=bool)
            # Sionna 用 tau < 0 标记无效径；两个判据都要，缺一会混进假径。
            mask &= np.asarray(tau[0, tx, :] >= 0.0, dtype=bool)
            if not mask.any():
                out.append(None)
                continue
            out.append(
                RayPaths(
                    gains=np.ascontiguousarray(gains[0, :, tx, :, :][:, :, mask]),
                    tau_s=np.ascontiguousarray(tau[0, tx, mask]).astype(np.float64),
                    theta_t_rad=np.ascontiguousarray(theta_t[0, tx, mask]).astype(np.float64),
                    phi_t_rad=np.ascontiguousarray(phi_t[0, tx, mask]).astype(np.float64),
                    theta_r_rad=np.ascontiguousarray(theta_r[0, tx, mask]).astype(np.float64),
                    phi_r_rad=np.ascontiguousarray(phi_r[0, tx, mask]).astype(np.float64),
                    doppler_hz=np.ascontiguousarray(doppler[0, tx, mask]).astype(np.float64),
                )
            )
        return out

    # -- 接缝 ---------------------------------------------------------------

    def _small_scale_channel(
        self,
        profile: Any,
        rng: np.random.Generator,
        *,
        n_time: int,
        n_rb: int,
        n_bs: int,
        n_ue: int,
        doppler_hz: float,
        realization_index: int,
        link_aod_rad: float,
        link_aoa_rad: float,
        link_zod_rad: float,
        link_zoa_rad: float,
        cell: Cell,
        ue_position: np.ndarray,
        is_los: bool,
        role: str,
    ) -> np.ndarray:
        # 统计信道的这些入参在 RT 下没有意义：多径角度、时延、多普勒全部
        # 来自几何。显式 del 掉，避免以后有人以为它们参与了计算。
        del profile, rng, doppler_hz, realization_index
        del link_aod_rad, link_aoa_rad, link_zod_rad, link_zoa_rad, is_los

        if role == "serving":
            self._sample_diag = []
        sites = self._sites_in_scene or self._build_sites()
        self._sites_in_scene = sites
        per_cell = self._solve(sites, np.asarray(ue_position, dtype=np.float64))
        index = int(cell.cell_id)
        source_index = (
            self._cell_to_source[index] if index < len(self._cell_to_source) else index
        )
        rays = per_cell[source_index] if source_index < len(per_cell) else None

        spec = array_spec_from_config(self.cfg, n_bs, n_ue)
        if rays is None:
            if role == "serving":
                # 先分清是「这个 UE 真的没覆盖」还是「38.901 选的服务小区
                # 恰好被挡住，而别的小区是通的」——两者的处理方式完全不同，
                # 一句「覆盖空洞」会把后者误导成前者。
                reachable = sum(1 for item in per_cell if item is not None)
                if reachable > 0:
                    cause = (
                        f"该 UE 还有 {reachable} 个物理站可达，只是 38.901 按路损+扇区增益"
                        "选出的这个服务小区在射线追踪下追不到径。这是**大尺度用 38.901、"
                        "小尺度用射线追踪**这个混合口径的固有后果：服务小区的选择不知道"
                        "建筑遮挡。"
                    )
                    hint = (
                        "处理办法：用 custom_ue_positions 挑同时满足两边的位置；"
                        "或减少站数/改 isd_m 让选择与可达一致；"
                        "或把 rt_max_depth 调大、打开 rt_diffuse_reflection / "
                        "rt_edge_diffraction 让被挡的链路也能追到径。"
                    )
                else:
                    cause = "该 UE 对**所有**物理站都追不到径，是真实的覆盖空洞。"
                    hint = (
                        "处理办法：换 UE 位置（custom_ue_positions）、调 tx_height_m / "
                        "rt_scene_offset_m，或打开 rt_diffuse_reflection / rt_edge_diffraction。"
                    )
                raise RuntimeError(
                    "Sionna RT 在服务链路上没有追到任何径："
                    f"scene={self._scene_name!r} cell={index} "
                    f"bs={np.round(np.asarray(cell.position) + self._scene_offset_m, 2).tolist()} "
                    f"ue={np.round(np.asarray(ue_position) + self._scene_offset_m, 2).tolist()}"
                    f"（含场景平移 {np.round(self._scene_offset_m[:2], 2).tolist()}）。"
                    + cause
                    + "不会退回统计信道。"
                    + hint
                )
            # 干扰小区被完全遮挡是正常物理结果：该小区不产生干扰信道。
            self._sample_diag.append(
                {"cell": index, "role": role, "num_paths": 0, "outage": True}
            )
            return np.zeros((int(n_time), int(n_rb), n_bs, n_ue), dtype=np.complex64)

        h = synthesize_channel(
            rays,
            spec,
            sector_azimuth_deg=float(cell.azimuth_deg),
            carrier_freq_hz=float(self.cfg.get("carrier_freq_hz", 3.5e9) or 3.5e9),
            n_time=int(n_time),
            n_rb=int(n_rb),
            subcarrier_spacing_hz=float(
                self.cfg.get("subcarrier_spacing", 30_000.0) or 30_000.0
            ),
            sample_interval_s=float(self.cfg.get("sample_interval_s", 5e-3) or 5e-3),
        )
        gain_sum = float(np.sum(np.abs(rays.gains) ** 2))
        self._sample_diag.append(
            {
                "cell": index,
                "role": role,
                "num_paths": rays.num_paths,
                "outage": False,
                "rt_pathloss_db": (
                    -10.0 * math.log10(max(gain_sum, _EPS)) if gain_sum > 0 else None
                ),
                "rt_min_delay_ns": float(np.min(rays.tau_s)) * 1e9,
                "rt_delay_spread_ns": _delay_spread_ns(rays),
                "rt_doppler_max_hz": float(np.max(np.abs(rays.doppler_hz))),
            }
        )
        return h

    # -- 输出 ---------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        info = super().describe()
        info.update(
            {
                "source": "sionna_rt",
                "implementation": "superran-first-party-adapter+sionna-rt",
                "scene": self._scene_name,
                "channel_generation_mode": "sionna_rt",
                "contract": SIONNA_RT_CONTRACT_VERSION,
            }
        )
        return info

    def iter_samples(self) -> Iterator[ChannelSample]:
        for sample in super().iter_samples():
            sample.source = "sionna_rt"
            diag = list(self._sample_diag)
            serving = next(
                (row for row in diag if row.get("role") == "serving"), {}
            )
            sample.meta.update(
                {
                    "implementation": "superran-first-party-adapter+sionna-rt",
                    "channel_generation_mode": "sionna_rt",
                    "rt_contract": SIONNA_RT_CONTRACT_VERSION,
                    "rt_scene": self._scene_name,
                    "rt_scene_offset_m": [float(v) for v in self._scene_offset_m],
                    "rt_engine": _engine_version(),
                    "rt_max_depth": int(self.cfg.get("rt_max_depth", 3) or 3),
                    "rt_diffuse_reflection": bool(
                        self.cfg.get("rt_diffuse_reflection", False)
                    ),
                    "rt_edge_diffraction": bool(
                        self.cfg.get("rt_edge_diffraction", False)
                    ),
                    "rt_link_diagnostics": diag,
                    "rt_num_paths_serving": serving.get("num_paths"),
                    "rt_pathloss_db": serving.get("rt_pathloss_db"),
                    "rt_delay_spread_ns": serving.get("rt_delay_spread_ns"),
                    # RT 的多径来自真实几何，套 CDL 标准剖面会得到与数据无关的
                    # 假角度。loader.paths() 认这个键并拒绝返回错误结果。
                    "rt_large_scale_source": "3gpp-tr38901-formula-not-ray-traced",
                    "rt_array_model": "superran-effective-subarray-shared-with-cdl",
                    # internal_sim 是逐位可复现的；射线追踪不是。Sionna 的
                    # PathSolver 即使固定 seed、单线程、同一进程内连跑，返回的
                    # 时延与复幅度仍有末位差异（实测 max|Δa|/|a| ~ 1e-5）。
                    # 传到信道矩阵上实测 NMSE −65 ~ −122 dB，物理上可忽略，
                    # 但**字节不同**：RT 数据集不能靠重跑逐位复算。
                    "rt_bit_reproducible": False,
                    "rt_reproducibility_note": (
                        "sionna PathSolver is not bit-deterministic; measured "
                        "run-to-run channel NMSE -65..-122 dB on munich"
                    ),
                    # 阵元方向图只进链路预算（按链路方位的标量），没有逐径加权；
                    # 阵列响应在整个 100 MHz 上是常数（无 beam squint）。
                    # 这两条与 CDL 路径一致，但 RT 的径角度散得更开，误差更值得注意。
                    "rt_element_pattern_per_ray": False,
                    "rt_beam_squint_modeled": False,
                    "polarization_slant_angles_deg": list(self._slant_angles_deg()),
                }
            )
            # 统计信道的簇口径在 RT 下无意义，留着会被误读成真值。
            for key in ("num_taps", "rician_k_db", "sample_tau_rms_ns", "tau_rms_ns"):
                sample.meta.pop(key, None)
            yield sample


def _delay_spread_ns(rays: RayPaths) -> float:
    power = np.sum(np.abs(rays.gains) ** 2, axis=(0, 1))
    total = float(np.sum(power))
    if total <= 0.0:
        return 0.0
    weights = power / total
    tau = np.asarray(rays.tau_s, dtype=np.float64)
    mean = float(np.sum(weights * tau))
    return float(math.sqrt(max(float(np.sum(weights * (tau - mean) ** 2)), 0.0))) * 1e9


def _engine_version() -> str:
    try:
        import sionna.rt as rt  # noqa: PLC0415

        return f"sionna-rt {getattr(rt, '__version__', 'unknown')}"
    except Exception:  # noqa: BLE001
        return "sionna-rt unavailable"


def scene_fingerprint(name: str) -> str:
    """场景标识的稳定摘要，写进 provenance 用。"""
    return hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:16]


OPTIONAL_SOURCE_REGISTRY: dict[str, type[SionnaRTSource]] = {"sionna_rt": SionnaRTSource}
