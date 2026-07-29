"""用户侧加载库——取货代码里 import 的就是这个。

设计目标：让 Agent 拿到数据后**不必知道任何 ChannelHub 的坑**。
诸如"single 模式下 h_dl_true 是 None、数据其实在 h_serving_true"这类
知识固化在生成侧，这里看到的永远是干净的数组。

所有测量量惰性计算：要什么算什么，不要的不算。
"""
from __future__ import annotations

import json
from functools import cached_property
from typing import Any

import numpy as np

from . import measure
from .paths import dataset_dir


class Dataset:
    """一批信道样本 + 按需计算的测量量。

    数组约定
    --------
    h_true / h_est : [N, T, RB, BS_ant, UE_ant] complex64
    ue_position    : [N, 3] 米
    sinr_dB 等标量 : [N]
    """

    def __init__(self, dataset_id: str) -> None:
        self.dataset_id = dataset_id
        self.dir = dataset_dir(dataset_id)
        if not self.dir.is_dir():
            raise FileNotFoundError(f"找不到数据集 {dataset_id!r}（{self.dir}）")
        self.summary: dict[str, Any] = json.loads(
            (self.dir / "summary.json").read_text(encoding="utf-8")
        )
        self._npz = np.load(self.dir / "channels.npz", allow_pickle=False)

    # ---- 基本信息 ----
    def __repr__(self) -> str:
        s = self.summary.get("shape", {})
        return (
            f"<Dataset {self.dataset_id} N={s.get('N')} "
            f"T={s.get('T')} RB={s.get('RB')} BS={s.get('BS_ant')} UE={s.get('UE_ant')} "
            f"model={self.summary.get('channel_model')}>"
        )

    @property
    def n(self) -> int:
        return int(self.summary["shape"]["N"])

    @property
    def channel_model(self) -> str:
        return str(self.summary.get("channel_model") or "")

    @property
    def has_angles(self) -> bool:
        """CDL 模型才有每条径的角度；TDL 没有。"""
        return bool(self.summary.get("is_cdl"))

    @property
    def config(self) -> dict[str, Any]:
        return dict(self.summary.get("config", {}))

    def keys(self) -> list[str]:
        return sorted(self._npz.files)

    # ---- 信道 ----
    @cached_property
    def h_true(self) -> np.ndarray:
        """理想信道 [N, T, RB, BS_ant, UE_ant]。"""
        return self._npz["h_true"]

    @cached_property
    def h_est(self) -> np.ndarray:
        """带导频与噪声的估计信道，与 h_true 同形。"""
        return self._npz["h_est"]

    @cached_property
    def h_interferers(self) -> np.ndarray | None:
        """干扰小区信道 [N, K-1, T, RB, BS, UE]；单小区场景为 None。"""
        return self._npz["h_interferers"] if "h_interferers" in self._npz.files else None

    @cached_property
    def w_dl(self) -> np.ndarray | None:
        """下行预编码矩阵 [N, RB, BS_ant, rank]。"""
        return self._npz["w_dl"] if "w_dl" in self._npz.files else None

    @cached_property
    def ue_position(self) -> np.ndarray:
        """终端位置 [N, 3]，米。"""
        return self._npz["ue_position"]

    def estimation_error_nmse_db(self) -> np.ndarray:
        """逐样本的信道估计归一化均方误差，dB。理想信道模式下会是 -inf。"""
        err = self.h_est - self.h_true
        num = (np.abs(err) ** 2).sum(axis=(1, 2, 3, 4))
        den = (np.abs(self.h_true) ** 2).sum(axis=(1, 2, 3, 4))
        return 10.0 * np.log10(np.maximum(num / np.maximum(den, 1e-30), 1e-30))

    # ---- 标量 ----
    def scalar(self, name: str) -> np.ndarray:
        for prefix in ("scalar__", "meta__"):
            key = prefix + name
            if key in self._npz.files:
                return self._npz[key]
        raise KeyError(f"没有标量 {name!r}；可用：{[k for k in self.keys() if '__' in k]}")

    @property
    def sinr_dB(self) -> np.ndarray:
        return self.scalar("sinr_dB")

    @property
    def snr_dB(self) -> np.ndarray:
        return self.scalar("snr_dB")

    @cached_property
    def geometry(self) -> dict[str, np.ndarray]:
        """几何与大尺度量：路损、距离、视距判定、多普勒、时延扩展。

        这些是仿真过程中算出来的物理量，ChannelHub 原本会在存盘时丢弃。
        """
        out: dict[str, np.ndarray] = {}
        for name in ("pathloss_dB", "distance_3d_m", "is_los", "los_probability",
                     "rx_power_serving_dbm", "doppler_hz", "sample_tau_rms_ns",
                     "antenna_gain_serving_db", "noise_power_dbm"):
            key = "meta__" + name
            if key in self._npz.files:
                out[name] = self._npz[key]
        out["ue_position"] = self.ue_position
        return out

    @cached_property
    def ssb(self) -> dict[str, np.ndarray]:
        """多小区 SSB 测量：每小区 RSRP / SINR，[N, K]。"""
        out = {}
        for k in ("ssb_rsrp_dBm", "ssb_sinr_dB"):
            if k in self._npz.files:
                out[k] = self._npz[k]
        return out

    # ---- 测量量（惰性，全部物理量）----
    def pdp(self, index: int | None = None, *, per_antenna: bool = False) -> Any:
        """时延功率谱：未归一化功率 + 真实时延轴。

        index 为 None 时返回全部样本的列表；给定 index 只算那一个。
        """
        scs = float(self.config.get("subcarrier_spacing", 30000) or 30000)
        if index is not None:
            return measure.power_delay_profile(
                self.h_true[index], subcarrier_spacing_hz=scs, per_antenna=per_antenna
            )
        return [
            measure.power_delay_profile(h, subcarrier_spacing_hz=scs, per_antenna=per_antenna)
            for h in self.h_true
        ]

    def srs(self, index: int | None = None) -> Any:
        """SRS 侧空间特征：完整协方差、全部特征值、每天线增益、波束域 RSRP。"""
        if index is not None:
            return measure.srs_features(self.h_true[index])
        return [measure.srs_features(h) for h in self.h_true]

    def pmi(self, index: int | None = None, *, max_rank: int = 4) -> Any:
        """38.214 Type I 码本索引 + 预编码矩阵 + 秩（不是 MAE token）。"""
        if index is not None:
            return measure.pmi_type_i(self.h_true[index], max_rank=max_rank)
        return [measure.pmi_type_i(h, max_rank=max_rank) for h in self.h_true]

    def rsrp(self, index: int | None = None) -> Any:
        """每天线信道增益 dB（不做区间截断）。"""
        if index is not None:
            return measure.channel_gain_db(self.h_true[index])
        return np.stack([measure.channel_gain_db(h) for h in self.h_true])

    @property
    def is_ray_traced(self) -> bool:
        """是否由射线追踪生成（而非统计信道模型）。"""
        meta = self.summary.get("sample_meta", {}) or {}
        return meta.get("channel_generation_mode") == "sionna_rt"

    def paths(self) -> measure.PathStructure:
        """每条径/簇的时延、功率、角度。CDL 才有角度，TDL 会是 None。

        **仅适用于统计信道模型（CDL/TDL）。** 这些值来自 38.901 的标准剖面，
        时延按实际时延扩展缩放——它描述的是该模型族的多径结构。

        射线追踪数据集调用此方法会直接报错：那里的多径来自真实建筑几何，
        每个位置各不相同，套用 CDL 剖面会得到一组与数据无关的假角度。
        ChannelHub 目前没有把 Sionna 的逐径几何导出到 ChannelSample，
        所以这个量在射线追踪路径上暂时拿不到。
        """
        if self.is_ray_traced:
            raise NotImplementedError(
                f"数据集 {self.dataset_id} 由射线追踪生成，多径结构取决于真实建筑几何，"
                "不能用 CDL/TDL 的标准剖面代替——那样得到的角度与本数据无关。\n"
                "需要每条径的角度时，改用统计信道模型（CDL-A~E）重新生成；"
                "射线追踪侧的逐径几何需要 ChannelHub 先把 Sionna 的 Paths 对象导出，"
                "当前版本尚未支持。\n"
                "本数据集仍可正常使用：信道矩阵、PDP、协方差、PMI、几何量都不受影响。"
            )
        tau = self.summary.get("tau_rms_ns")
        tau_s = float(tau) * 1e-9 if tau else 300e-9
        return measure.path_structure(self.channel_model or "CDL-C", tau_s)

    def capacity(self, index: int | None = None) -> Any:
        """MIMO 容量 bit/s/Hz，按各样本自身的信噪比算。"""
        if index is not None:
            return measure.channel_capacity_bps_hz(self.h_true[index], float(self.sinr_dB[index]))
        return np.asarray(
            [
                measure.channel_capacity_bps_hz(h, float(s))
                for h, s in zip(self.h_true, self.sinr_dB)
            ]
        )

    def condition_number(self, index: int | None = None) -> Any:
        if index is not None:
            return measure.condition_number(self.h_true[index])
        return np.asarray([measure.condition_number(h) for h in self.h_true])

    # ---- 链路性能：预编码 → SINR → 谱效 ----
    def link(self, index: int = 0, **kw: Any) -> Any:
        """单样本的链路性能。见 :func:`superwireless.linklevel.link_performance`。

        常用：``ds.link(0, snr_db=20, method="svd")``。
        传 ``h_for_precoding=ds.h_est[0]`` 可评估"用有误差的 CSI 做预编码"的代价。
        """
        from . import linklevel as ll

        kw.setdefault("snr_db", float(self.sinr_dB[index]))
        if self.h_interferers is not None:
            kw.setdefault("h_interferers", self.h_interferers[index])
        return ll.link_performance(self.h_true[index], **kw)

    def monte_carlo(self, **kw: Any) -> Any:
        """整批样本的蒙特卡洛统计，含 95% 置信区间与收敛判断。

        不指定 ``snr_db`` 时用各样本自身的信干噪比反推噪声——这更贴近真实
        分布，因为近点和边缘用户的工况本就不同。
        """
        from . import linklevel as ll

        if "snr_db" not in kw and "noise_powers" not in kw:
            sig = np.mean(np.abs(self.h_true) ** 2, axis=(1, 2, 3, 4))
            kw["noise_powers"] = sig / np.maximum(10.0 ** (self.sinr_dB / 10.0), 1e-30)
        if self.h_interferers is not None:
            kw.setdefault("interferers", self.h_interferers)
        return ll.monte_carlo(self.h_true, **kw)

    def compare_precoders(self, **kw: Any) -> dict[str, Any]:
        """同一批信道上横向对比 SVD / 宽带 SVD / Type I 码本 / DFT 波束。"""
        from . import linklevel as ll

        kw.setdefault("snr_db", float(np.median(self.sinr_dB)))
        return ll.compare_precoders(self.h_true, **kw)

    def validate(self, **kw: Any) -> Any:
        """可信度体检：对标 38.901、对标物理定律、检查蒙特卡洛收敛。

        生成数据后建议先跑一次——结论建立在信道之上，信道不可信则结论不可信。
        """
        from . import validate as va

        return va.full_report(self, **kw)


def load(dataset_id: str) -> Dataset:
    """按句柄取数据集。这是取货代码的入口。"""
    return Dataset(dataset_id)
