"""superran —— 给 Agent 用的无线仿真信道供应站。

用法（取货代码里就这两行）::

    from superran import load
    ds = load("ds_a3f21c")

MCP 服务端入口在 :mod:`superran.server`。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

from ._compat import migrate_legacy_environment  # noqa: E402

migrate_legacy_environment()

# ``Dataset`` / ``load`` 改成懒暴露：``loader`` 顶层 import numpy，而 numpy 在
# 这台机器上光是 import 就提交 658 MB（OpenBLAS 按 20 个核预留线程 arena）。
# MCP 服务端每个 CLI 会话起一个进程，绝大多数进程一次数据都不加载，
# 不该为此付这笔钱。详见 superran._lazy 的模块注释。
#
# 这里用 PEP 562 就够了：外部写 ``from superran import load`` 是对模块做属性
# 访问，会走 __getattr__。（模块**内部**的全局名字查找不走它，所以 server.py
# 用的是 _lazy.LazyModule 代理对象，两者场景不同。）
if TYPE_CHECKING:  # 只给类型检查器看，运行时不 import
    from .loader import Dataset, load

_LAZY_EXPORTS = {"Dataset", "load"}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        from . import loader

        value = getattr(loader, name)
        globals()[name] = value  # 只解析一次，之后走正常属性查找
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LAZY_EXPORTS)


__all__ = ["Dataset", "load", "__version__"]
