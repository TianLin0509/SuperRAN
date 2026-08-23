"""发射权的总功率/每天线功率归一化。

本项目统一使用 ``Q[frequency, antenna, stream]``。因此每天线发射功率是
``diag(Q Q^H)``，也就是第 2 维（antenna）的**行范数平方**；外部文档若把
预编码矩阵写成 ``[stream, antenna]``，它所说的“列归一”在这里就是行归一。

三个工程名称沿用用户现场口径：

``EBF``
    总功率约束。正交方向的各流等分总功率。
``PEBF``
    从 EBF 权出发做一个全局缩放，使最大发射天线恰好满足 ``P/M``。
    保持列间几何关系，但一般用不满总功率。
``NEBF``
    每根非零天线分别缩放到 ``P/M``。功率用满，但会改变列间内积；在 MU
    ZF/RZF 中它可能破坏零陷。

所有公开函数同时返回物理发射矩阵与诊断量，避免“满足了约束”只靠命名断言。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

PowerConstraint = Literal["ebf", "pebf", "nebf"]

_EPS = 1e-30


@dataclass(frozen=True)
class PowerDiagnostics:
    """逐频点功率与正交性诊断。"""

    mode: str
    per_antenna_power: np.ndarray       # [F, M]
    total_power_used: np.ndarray        # [F]
    total_power_limit: float
    per_antenna_limit: float
    max_per_antenna_violation: float
    utilization: np.ndarray             # [F] = used / P
    orthogonality_error: np.ndarray     # [F]，归一化 Gram 非对角能量
    zero_antenna_rows: np.ndarray       # [F]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "matrix_convention": "Q[frequency, antenna, stream]",
            "per_antenna_axis": 1,
            "total_power_limit": float(self.total_power_limit),
            "per_antenna_limit": float(self.per_antenna_limit),
            "total_power_used_mean": float(np.mean(self.total_power_used)),
            "total_power_used_min": float(np.min(self.total_power_used)),
            "total_power_used_max": float(np.max(self.total_power_used)),
            "utilization_mean": float(np.mean(self.utilization)),
            "utilization_min": float(np.min(self.utilization)),
            "max_per_antenna_power": float(np.max(self.per_antenna_power)),
            "max_per_antenna_violation": float(self.max_per_antenna_violation),
            "orthogonality_error_mean": float(np.mean(self.orthogonality_error)),
            "orthogonality_error_max": float(np.max(self.orthogonality_error)),
            "zero_antenna_rows": int(np.sum(self.zero_antenna_rows)),
        }


def _validate_mode(mode: str) -> PowerConstraint:
    m = str(mode).strip().lower()
    if m not in ("ebf", "pebf", "nebf"):
        raise ValueError(f"power_constraint 只支持 ebf / pebf / nebf，收到 {mode!r}")
    return m  # type: ignore[return-value]


def _as_frequency_matrix(w: np.ndarray) -> tuple[np.ndarray, bool]:
    a = np.asarray(w)
    squeezed = a.ndim == 2
    if squeezed:
        a = a[None]
    if a.ndim != 3 or a.shape[1] < 1 or a.shape[2] < 1:
        raise ValueError(f"预编码权应为 [F,M,L] 或 [M,L]，收到 {np.asarray(w).shape}")
    if not np.all(np.isfinite(a)):
        raise ValueError("预编码权包含 NaN/Inf")
    return np.asarray(a, dtype=np.complex128), squeezed


def _orthogonality_error(q: np.ndarray) -> np.ndarray:
    gram = np.conj(np.transpose(q, (0, 2, 1))) @ q
    diag = np.zeros_like(gram)
    idx = np.arange(gram.shape[-1])
    diag[:, idx, idx] = gram[:, idx, idx]
    return np.linalg.norm(gram - diag, axis=(1, 2)) / np.maximum(
        np.linalg.norm(diag, axis=(1, 2)), _EPS)


def _diagnostics(q: np.ndarray, mode: str, total_power: float) -> PowerDiagnostics:
    per_ant = np.sum(np.abs(q) ** 2, axis=2).real
    used = np.sum(per_ant, axis=1)
    m_ant = q.shape[1]
    cap = float(total_power) / m_ant
    tol = 128.0 * np.finfo(float).eps * max(float(total_power), 1.0)
    return PowerDiagnostics(
        mode=mode,
        per_antenna_power=per_ant,
        total_power_used=used,
        total_power_limit=float(total_power),
        per_antenna_limit=cap,
        max_per_antenna_violation=float(max(0.0, np.max(per_ant) - cap - tol)),
        utilization=used / max(float(total_power), _EPS),
        orthogonality_error=_orthogonality_error(q),
        zero_antenna_rows=np.sum(per_ant <= _EPS, axis=1),
    )


def constrain_physical_matrix(
    q_ebf: np.ndarray,
    *,
    mode: PowerConstraint | str = "ebf",
    total_power: float = 1.0,
) -> tuple[np.ndarray, PowerDiagnostics]:
    """把已满足总功率约束的物理矩阵 ``q_ebf`` 转成 EBF/PEBF/NEBF。

    ``q_ebf`` 的列已含幅度，输入流默认单位方差，即发射协方差为
    ``q_ebf q_ebf^H``。EBF 路径原样返回，以保证默认总功率实现不发生隐藏的
    二次归一化；调用方有责任用 :func:`equal_power_weights` 或
    :func:`allocated_power_weights` 构造它。
    """
    m = _validate_mode(str(mode))
    q, squeezed = _as_frequency_matrix(q_ebf)
    p = float(total_power)
    if not np.isfinite(p) or p <= 0:
        raise ValueError(f"total_power 必须是有限正数，收到 {total_power}")

    out = np.array(q, copy=True)
    per_ant = np.sum(np.abs(out) ** 2, axis=2).real
    cap = p / out.shape[1]
    if m == "ebf":
        # 本函数的输入契约是“已经满足总功率约束”。违反时硬失败，不能因为模式
        # 名字叫 EBF 就把超功率矩阵原样放行。complex64 单位化允许 1e-6 舍入差。
        used = np.sum(per_ant, axis=1)
        if np.any(used > p * (1.0 + 1e-6)):
            raise ValueError(
                f"EBF 输入总功率越界：max={float(np.max(used)):.9g} > P={p:.9g}")
        # 欠功率也不能静默放行：列范数非单位的输入（总功率明显小于 P）
        # 多半是上游方向矩阵没归一，放行会把功率缩水藏进吞吐里。
        if np.any(used < p * (1.0 - 1e-3)):
            raise ValueError(
                f"EBF 输入欠功率：min={float(np.min(used)):.9g} < P={p:.9g}；"
                "预编码列应单位范数（总功率恰为 P），请检查上游方向矩阵")
    elif m == "pebf":
        max_ant = np.max(per_ant, axis=1)
        scale = np.minimum(1.0, np.sqrt(cap / np.maximum(max_ant, _EPS)))
        out *= scale[:, None, None]
    elif m == "nebf":
        row_norm = np.sqrt(per_ant)
        if np.any(row_norm <= _EPS):
            raise ValueError(
                "NEBF 无法把零天线行归一到 P/M；请检查预编码方向/端口映射")
        scale = np.zeros_like(row_norm)
        nz = row_norm > _EPS
        scale[nz] = np.sqrt(cap) / row_norm[nz]
        out *= scale[:, :, None]

    diag = _diagnostics(out, m, p)
    return (out[0] if squeezed else out), diag


def physical_matrix_diagnostics(
    q_physical: np.ndarray,
    *,
    mode: PowerConstraint | str = "ebf",
    total_power: float = 1.0,
) -> PowerDiagnostics:
    """只体检已经归一化的物理矩阵，不再施加第二次缩放。

    这个入口用于 ``W + stream_power`` 已经被分解回物理 ``Q`` 的调用链。
    再调用 :func:`constrain_physical_matrix` 会让 PEBF/NEBF 被归一两次，诊断量
    与实际用于 SINR 的矩阵不再是同一个对象。
    """
    m = _validate_mode(str(mode))
    q, _ = _as_frequency_matrix(q_physical)
    p = float(total_power)
    if not np.isfinite(p) or p <= 0:
        raise ValueError(f"total_power 必须是有限正数，收到 {total_power}")
    diag = _diagnostics(q, m, p)
    tol = 1e-6 * max(p, 1.0)
    if float(np.max(diag.total_power_used)) > p + tol:
        raise ValueError("物理预编码矩阵超过总功率约束")
    if m in ("pebf", "nebf") and diag.max_per_antenna_violation > tol:
        raise ValueError("物理预编码矩阵超过每天线功率约束")
    return diag


def equal_power_weights(
    directions: np.ndarray,
    *,
    mode: PowerConstraint | str = "ebf",
    total_power: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, PowerDiagnostics]:
    """等流功率下返回 ``(物理 Q, 兼容旧 SINR 的 W_model, diagnostics)``。

    ``directions`` 的每列应为单位范数。旧链路公式显式乘 ``P/L``；因此
    ``W_model = Q / sqrt(P/L)`` 能让同一个公式表达三种功率约束。
    EBF 时 ``W_model`` 直接复用原数组，保证默认路径与改动前逐位退化。
    """
    m = _validate_mode(str(mode))
    raw = np.asarray(directions)
    raw_f = raw[None] if raw.ndim == 2 else raw
    w, squeezed = _as_frequency_matrix(raw)
    p = float(total_power)
    l_stream = w.shape[2]
    amp = np.sqrt(p / l_stream)
    q0 = w * amp
    q, diag = constrain_physical_matrix(q0, mode=m, total_power=p)
    qf = q[None] if np.asarray(q).ndim == 2 else np.asarray(q)
    # EBF 是项目历史默认值。这里刻意把调用者原数组（含 dtype）原样作为
    # W_model 返回，避免一次无意义的 complex64 -> complex128 转换让默认路径
    # 无法做逐位回归；另外两种模式才需要新矩阵。
    model = raw_f if m == "ebf" else qf / amp
    if squeezed:
        return qf[0], model[0], diag
    return qf, model, diag


def allocated_power_weights(
    directions: np.ndarray,
    stream_power: np.ndarray,
    *,
    mode: PowerConstraint | str = "ebf",
    total_power: float = 1.0,
) -> tuple[np.ndarray, PowerDiagnostics]:
    """已有逐流功率时返回物理发射矩阵 ``Q = W diag(sqrt(p))``。"""
    w, squeezed = _as_frequency_matrix(directions)
    pw = np.asarray(stream_power, dtype=float)
    if pw.ndim == 1:
        pw = pw[None]
    if pw.shape != (w.shape[0], w.shape[2]):
        raise ValueError(f"stream_power 应为 {(w.shape[0], w.shape[2])}，收到 {pw.shape}")
    if np.any(~np.isfinite(pw)) or np.any(pw < 0):
        raise ValueError("stream_power 必须是有限非负数")
    q0 = w * np.sqrt(pw)[:, None, :]
    q, diag = constrain_physical_matrix(q0, mode=mode, total_power=total_power)
    qf = q[None] if np.asarray(q).ndim == 2 else np.asarray(q)
    return (qf[0] if squeezed else qf), diag
