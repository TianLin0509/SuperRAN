"""Pytest 会话级数值库预热。

本仓库仍有一批历史测试在模块导入阶段直接执行矩阵仿真。Windows 上若第一次
``numpy.linalg.pinv/SVD`` 恰好发生在 pytest 的收集路径，OpenBLAS/LAPACK 曾出现
access violation。生产入口已经调用 :func:`superran.channelhub.warmup`；测试会话
也必须在收集任何模块前遵守同一启动合同。
"""
from __future__ import annotations

from superran import channelhub


def pytest_sessionstart(session: object) -> None:
    """在测试模块收集前，于主线程初始化 SciPy/NumPy 数值后端。"""
    del session
    info = channelhub.warmup()
    if not info.get("ok"):
        raise RuntimeError(f"SuperRAN 测试预热失败：{info}")
