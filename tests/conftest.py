"""Pytest 会话级数值库预热。

本仓库仍有一批历史测试在模块导入阶段直接执行矩阵仿真。Windows 上若第一次
``numpy.linalg.pinv/SVD`` 恰好发生在 pytest 的收集路径，OpenBLAS/LAPACK 曾出现
access violation。生产入口已经调用 :func:`superran.channelhub.warmup`；测试会话
也必须在收集任何模块前遵守同一启动合同。
"""
from __future__ import annotations

import os

from superran import channelhub


def pytest_sessionstart(session: object) -> None:
    """在测试模块收集前，于主线程初始化 SciPy/NumPy 数值后端。"""
    del session
    # 测试会话不弹浏览器：这个 env 之前只靠个别测试模块 import 时顺带设置，
    # 单跑 pytest 原生文件时没人兜底。回传服务不在此列——test_interference
    # 第 9 节测的就是它，全局关掉会削弱覆盖。
    os.environ.setdefault("SUPERRAN_NO_BROWSER", "1")
    info = channelhub.warmup()
    if not info.get("ok"):
        raise RuntimeError(f"SuperRAN 测试预热失败：{info}")
