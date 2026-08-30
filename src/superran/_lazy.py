"""模块级懒加载工具。

为什么需要
==========

superran 的 MCP 服务端是**每个 CLI 会话各起一个进程**。2026-08-29 在一台
20 逻辑核 / 32 GB 的机器上实测：一个 superran MCP 进程**恒定提交 2.66 GB**
内存，而实占只有 20–30 MB —— 也就是说这些内存从启动到进程结束一次都没被碰过。
当时机器上并存 13 个 Claude 会话，单是 superran 就占掉 **34.6 GB 提交内存**
（系统总额度的三分之一），最终表现为一个看似无关的故障：文件预览打不开
（提交内存见底，Chromium 起不了新的渲染进程）。

分级测量（同一台机器，逐步 import）::

    ① 裸 python                    提交    8 MB
    ② + numpy                      提交  658 MB   ← OpenBLAS 按核数预留线程 arena
    ③ + scipy                      提交 1288 MB
    ④ + superran.server            提交 1323 MB   ← superran 自己只占 35 MB

⚠ 但**服务端进程里不能靠它省内存**（2026-08-29 用两次死锁换来的结论）。

一开始的想法是把 numpy 也推迟到第一次调用工具时再导。结果服务端能 initialize、
能列出全部 35 个工具，但第一次调用任何工具就永久挂死，无异常无日志。
faulthandler 抓到的主线程栈依次是::

    sr_mcs_info → superran/linkadapt.py → numpy/_core/multiarray.py
      → importlib create_module              ← 卡死
    # 改成只预载 numpy/scipy 之后，卡点换了个地方：
    sr_capabilities → channelhub.probe_source_contract
      → msg_embedding/channel_est/interpolate.py    ← 卡死

即：事件循环跑起来之后，**首次载入任何 C 扩展都不安全**；限制 OpenBLAS 线程数
也绕不开（实测照样挂）。``channelhub.warmup()`` 的注释里早就写了这一点并标了
「别删」—— 它是正确性依赖，不是性能优化。所以 ``server.main()`` 仍然在主线程把
整张依赖图预载完，服务端省内存改由 ``SUPERRAN_BLAS_THREADS`` 限制线程 arena
（空转 2718 MB → 1677 MB）。

那这个模块还有什么用？**给"不跑服务端"的场景省钱**：

    import superran            658 MB → 10 MB
    import superran.server    1323 MB → 49 MB

测试、工具链、以及只想 ``from superran import load`` 取数据的脚本都因此变轻。
代价可以忽略：实测 ``import numpy, scipy.special`` 只要 0.28 秒，
``superran.channelhub`` 0.01 秒。

为什么用代理对象而不是 PEP 562
==============================

模块级 ``__getattr__``（PEP 562）只在**从外部访问模块属性**时触发；模块内部函数
体里的全局名字查找走的是 globals → builtins，不会经过它。而 server.py 有 40 多个
工具函数、上千处 ``np.xxx`` / ``ch.xxx`` 调用，全靠模块内的全局查找。
所以这里用"占位模块对象"：名字照旧绑在 globals 里，属性访问时才真正 import。
这样 server.py 的函数体一个字都不用改。

包入口 ``__init__.py`` 那种"从外部 ``from superran import load``"的场景，
用 PEP 562 就够了，不需要代理对象。
"""

from __future__ import annotations

import importlib
import types
from typing import Any

__all__ = ["LazyModule", "lazy_module"]

# 这几个属性是代理自身的状态，必须先于 __getattr__ 命中，
# 否则 __init__ 里第一次赋值就会触发无限递归。
_OWN_ATTRS = ("_lazy_target", "_lazy_package", "_lazy_loaded")


class LazyModule(types.ModuleType):
    """占位模块：第一次访问属性时才真正 import 目标模块。"""

    def __init__(self, target: str, package: str | None = None) -> None:
        super().__init__(target.lstrip("."))
        self._lazy_target = target
        self._lazy_package = package
        self._lazy_loaded: types.ModuleType | None = None

    def _load(self) -> types.ModuleType:
        module = self.__dict__.get("_lazy_loaded")
        if module is None:
            module = importlib.import_module(
                self.__dict__["_lazy_target"], self.__dict__["_lazy_package"]
            )
            self.__dict__["_lazy_loaded"] = module
        return module

    @property
    def lazy_is_loaded(self) -> bool:
        """已经真正 import 过了吗（给测试和诊断用，不触发加载）。"""
        return self.__dict__.get("_lazy_loaded") is not None

    def __getattr__(self, item: str) -> Any:
        if item in _OWN_ATTRS or item.startswith("__"):
            # dunder 与自身状态一律不代理：让它按普通模块的方式失败，
            # 否则 copy / pickle / inspect 的探测会把整个目标模块拖进来。
            raise AttributeError(item)
        return getattr(self._load(), item)

    def __dir__(self) -> list[str]:
        return dir(self._load())

    def __repr__(self) -> str:
        state = "loaded" if self.lazy_is_loaded else "pending"
        return f"<lazy module {self.__dict__.get('_lazy_target')!r} ({state})>"


def lazy_module(target: str, package: str | None = None) -> LazyModule:
    """建一个懒加载占位模块。

    ``target`` 可以是绝对名（``"numpy"``）或相对名（``".channelhub"``，
    这时要一并给出 ``package``）。
    """
    return LazyModule(target, package)
