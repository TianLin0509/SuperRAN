"""经验 CDF 话务分布的读取、校验与逆变换采样。

CDF 文件是仿真输入，不是结果拟合参数。默认格式为两列 ``value,cdf``：包大小
的 value 单位是 byte，包间隔默认是 ms；cdf 可写 0..1 或 0..100。相对路径固定
从项目根解析，避免 MCP 进程 cwd 改变后同一配置读到另一份文件。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np

from .paths import project_root

_SPLIT = re.compile(r"[,;\t ]+")
_CDF_HEADERS = {"cdf", "prob", "probability", "percentile", "cumulative"}
_VALUE_HEADERS = {
    "value", "size", "bytes", "packet_size", "interval", "interval_ms", "ms",
}


def resolve_cdf_path(path: str | Path) -> Path:
    """把 CDF 路径解析为稳定绝对路径；相对路径以项目根为基准。"""
    raw = Path(path).expanduser()
    resolved = raw if raw.is_absolute() else project_root() / raw
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise ValueError(f"CDF 文件不存在：{resolved}")
    return resolved


def _tokens(line: str) -> list[str]:
    return [x for x in _SPLIT.split(line.strip()) if x]


def _number(text: str) -> float:
    return float(text.strip().replace("%", ""))


@dataclass(frozen=True)
class EmpiricalCdf:
    """已校验的离散经验 CDF；通过 ``searchsorted`` 做逆变换采样。"""

    values: tuple[float, ...]
    cdf: tuple[float, ...]
    source_path: str
    sha256: str
    kind: Literal["packet_size", "interarrival"]
    value_unit: str
    probability_input_scale: Literal["fraction", "percent"]
    tail_normalized: bool

    @property
    def mean(self) -> float:
        probs = np.diff(np.r_[0.0, np.asarray(self.cdf, dtype=float)])
        return float(np.sum(np.asarray(self.values, dtype=float) * probs))

    def quantile(self, q: float) -> float:
        if not np.isfinite(q) or not 0.0 <= float(q) <= 1.0:
            raise ValueError(f"CDF quantile 必须在 [0,1]，收到 {q}")
        idx = int(np.searchsorted(self.cdf, float(q), side="left"))
        return float(self.values[min(idx, len(self.values) - 1)])

    def sample(self, rng: np.random.Generator, size: int | None = None) -> np.ndarray:
        if size is not None and (
                isinstance(size, (bool, np.bool_)) or not isinstance(size, (int, np.integer))
                or int(size) < 0):
            raise ValueError(f"CDF sample size 必须是非负整数，收到 {size}")
        u = rng.random(None if size is None else int(size))
        idx = np.searchsorted(np.asarray(self.cdf), u, side="left")
        return np.asarray(self.values, dtype=float)[idx]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "sha256": self.sha256,
            "kind": self.kind,
            "value_unit": self.value_unit,
            "points": len(self.values),
            "min": float(self.values[0]),
            "p50": self.quantile(0.5),
            "p95": self.quantile(0.95),
            "max": float(self.values[-1]),
            "mean": self.mean,
            "probability_input_scale": self.probability_input_scale,
            "tail_normalized": self.tail_normalized,
        }


def load_empirical_cdf(
    path: str | Path,
    *,
    kind: Literal["packet_size", "interarrival"],
    value_unit: str,
) -> EmpiricalCdf:
    """读取两列经验 CDF，并按文件 mtime/size 缓存解析结果。"""
    resolved = resolve_cdf_path(path)
    stat = resolved.stat()
    return _load_cached(
        str(resolved), int(stat.st_mtime_ns), int(stat.st_size), kind, str(value_unit))


@lru_cache(maxsize=64)
def _load_cached(
    path: str,
    _mtime_ns: int,
    _size: int,
    kind: Literal["packet_size", "interarrival"],
    value_unit: str,
) -> EmpiricalCdf:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"CDF 文件必须是 UTF-8/UTF-8-BOM：{path}") from exc
    lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        raise ValueError(f"CDF 文件为空：{path}")

    first = _tokens(lines[0])
    if len(first) < 2:
        raise ValueError(f"CDF 每行至少要有 value,cdf 两列：{path}")
    value_idx, cdf_idx, start = 0, 1, 0
    try:
        _number(first[0])
        _number(first[1])
    except ValueError:
        header = [x.strip().lower() for x in first]
        cdf_candidates = [i for i, name in enumerate(header) if name in _CDF_HEADERS]
        value_candidates = [i for i, name in enumerate(header) if name in _VALUE_HEADERS]
        if not cdf_candidates:
            raise ValueError(
                f"CDF 表头找不到 cdf/probability 列：{path}") from None
        cdf_idx = cdf_candidates[0]
        value_idx = value_candidates[0] if value_candidates else next(
            (i for i in range(len(header)) if i != cdf_idx), -1)
        if value_idx < 0:
            raise ValueError(f"CDF 表头找不到 value 列：{path}") from None
        start = 1

    values: list[float] = []
    probs: list[float] = []
    required = max(value_idx, cdf_idx)
    for line_no, line in enumerate(lines[start:], start=start + 1):
        row = _tokens(line)
        if len(row) <= required:
            raise ValueError(f"CDF 第 {line_no} 行列数不足：{path}")
        try:
            value, prob = _number(row[value_idx]), _number(row[cdf_idx])
        except ValueError as exc:
            raise ValueError(f"CDF 第 {line_no} 行不是数值：{path}") from exc
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"CDF 第 {line_no} 行 value 必须是有限正数：{path}")
        if not np.isfinite(prob):
            raise ValueError(f"CDF 第 {line_no} 行 cdf 必须有限：{path}")
        values.append(float(value))
        probs.append(float(prob))
    if not values:
        raise ValueError(f"CDF 没有数据行：{path}")
    if any(b <= a for a, b in zip(values, values[1:], strict=False)):
        raise ValueError(f"CDF value 必须严格递增且不可重复：{path}")

    input_scale: Literal["fraction", "percent"] = "fraction"
    max_prob = max(probs)
    if max_prob > 1.0 + 1e-9:
        if max_prob > 100.0 + 1e-6:
            raise ValueError(f"CDF 概率既不是 0..1 也不是 0..100：{path}")
        probs = [x / 100.0 for x in probs]
        input_scale = "percent"
    if probs[0] < -1e-12 or any(
            b + 1e-12 < a for a, b in zip(probs, probs[1:], strict=False)):
        raise ValueError(f"CDF 概率必须在 [0,1] 内单调不减：{path}")
    if probs[-1] < 0.999 or probs[-1] > 1.001:
        raise ValueError(f"CDF 最后一项必须收敛到 1（或 100%），实得 {probs[-1]}：{path}")
    if any(x < -1e-12 or x > 1.0 + 1e-9 for x in probs):
        raise ValueError(f"CDF 概率越界：{path}")
    tail_normalized = probs[-1] != 1.0
    probs[-1] = 1.0
    return EmpiricalCdf(
        values=tuple(values), cdf=tuple(probs), source_path=path,
        sha256=hashlib.sha256(raw).hexdigest(), kind=kind, value_unit=value_unit,
        probability_input_scale=input_scale, tail_normalized=tail_normalized,
    )
