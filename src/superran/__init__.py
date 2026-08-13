"""superran —— 给 Agent 用的无线仿真信道供应站。

用法（取货代码里就这两行）::

    from superran import load
    ds = load("ds_a3f21c")

MCP 服务端入口在 :mod:`superran.server`。
"""
from __future__ import annotations

__version__ = "0.1.0"

from ._compat import migrate_legacy_environment  # noqa: E402

migrate_legacy_environment()

from .loader import Dataset, load  # noqa: E402

__all__ = ["Dataset", "load", "__version__"]
