"""守住 MCP 服务端的 import 策略与内存上限。

背景（2026-08-29 实测，20 逻辑核 / 32 GB 的机器）
------------------------------------------------

superran 的 MCP 服务端是**每个 CLI 会话各起一个进程**。改动之前，一个进程恒定
提交 2.72 GB 内存。当时并存 13 个 Claude 会话，单是 superran 就占掉 34.6 GB 提交
内存（系统总额度的三分之一），最终表现成一个看起来毫不相干的故障：AI Hub 的
文件预览打不开（提交内存见底，Chromium 起不了新的渲染进程）。

分级测量（纯 import，未 warmup）::

    ① 裸 python                8 MB
    ② + numpy                658 MB     ← OpenBLAS 按核数预留线程 arena
    ③ + scipy               1288 MB
    ④ + superran.server     1323 MB     ← superran 自己只占 35 MB

**最初的方案（把 numpy 也懒加载）行不通**，两次死锁的栈见
test_warmup_stays_unconditional_and_has_no_skip_switch 的说明：numpy/scipy 及其
子模块的首次加载必须发生在主线程。所以服务端仍然在主线程预载它们。

服务端真正的省法是另外两条（都不动 import 时机）：
  1. 限制 BLAS 线程池（SUPERRAN_BLAS_THREADS，默认 4）；
  2. 不再为一个从不使用的 PyTorch 买单 —— sionna/torch 由 MSG-Platform 侧改成
     按需加载，能力探测改用 find_spec 只探顶层包名。
合起来：服务端空转 2718 MB → 365 MB。

懒加载保留下来的价值是「不跑服务端」的场景：``import superran`` 从 658 MB 降到
10 MB，``import superran.server`` 从 1323 MB 降到 49 MB —— 测试、工具链、
以及只想 ``from superran import load`` 取数据的脚本都受益。

这些测试用**子进程**跑：必须是干净解释器才测得准"谁把 numpy 拉进来了"。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _run(snippet: str) -> dict:
    """在干净子进程里跑一段代码，要求它 print 一行 JSON。"""
    code = f"import sys; sys.path.insert(0, {str(SRC)!r})\n{snippet}"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, f"子进程失败：\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_import_superran_does_not_pull_numpy():
    """``import superran`` 不得拉起 numpy。

    ``loader`` 顶层 import numpy，而包入口以前直接 ``from .loader import load``，
    于是只要碰一下这个包就付掉 658 MB。
    """
    out = _run(
        "import superran, json\n"
        "print(json.dumps({'numpy': 'numpy' in sys.modules, 'scipy': 'scipy' in sys.modules}))"
    )
    assert out["numpy"] is False, "import superran 不该拉起 numpy"
    assert out["scipy"] is False


def test_import_server_registers_tools_without_numpy():
    """服务端 import 完必须已经注册好工具，但仍然没加载 numpy。

    这两件事要同时成立才有意义：只要工具清单在，agent 就能看到能力；
    真正的计算推迟到第一次调用。
    """
    out = _run(
        "from superran.server import mcp\n"
        "import json\n"
        "tm = getattr(mcp, '_tool_manager', None)\n"
        "n = len(tm._tools) if tm is not None and hasattr(tm, '_tools') else -1\n"
        "print(json.dumps({'numpy': 'numpy' in sys.modules, 'tools': n}))"
    )
    assert out["numpy"] is False, "import superran.server 不该拉起 numpy"
    assert out["tools"] >= 30, f"工具没注册全，只有 {out['tools']} 个"


def test_lazy_proxies_resolve_on_first_use():
    """占位模块被访问属性时要真正加载，且用法与原来逐字一致。"""
    out = _run(
        "from superran import server as S\n"
        "import json\n"
        "before = {'np': S.np.lazy_is_loaded, 'ch': S.ch.lazy_is_loaded}\n"
        "value = float(S.np.pi)\n"
        "zeros = S.np.zeros(3).tolist()\n"
        "print(json.dumps({'before': before, 'numpy_now': 'numpy' in sys.modules,"
        " 'pi': value, 'zeros': zeros, 'np_loaded': S.np.lazy_is_loaded}))"
    )
    assert out["before"] == {"np": False, "ch": False}
    assert out["numpy_now"] is True
    assert out["np_loaded"] is True
    assert abs(out["pi"] - 3.14159265) < 1e-6
    assert out["zeros"] == [0.0, 0.0, 0.0]


def test_warmup_stays_unconditional_and_has_no_skip_switch():
    """``main()`` 里的 warmup 必须无条件跑，且不许提供"跳过"开关。

    ⚠ 这条是 2026-08-29 用两次死锁换来的，别再把它改回"懒加载"：

    把 warmup 改成可选之后，服务端能 initialize、能列出 35 个工具，但第一次调用
    工具就永久挂死（无异常无日志）。faulthandler 抓到的主线程栈依次是::

        sr_mcs_info → superran/linkadapt.py → numpy/_core/multiarray.py
          → importlib create_module            ← 卡死
        # 只预载 numpy/scipy 之后，卡点换了个地方：
        sr_capabilities → channelhub.probe_source_contract
          → msg_embedding/channel_est/interpolate.py   ← 卡死

    边界（别把结论用过头）：危险的是 **numpy / scipy 及其子模块**的首次加载，
    限制 BLAS 线程数也绕不开（实测照样挂）。而 torch / sionna.rt 在事件循环里
    首次 import 实测**不会**挂（1.4s / 1.0s），前提是 numpy/scipy 已主线程预热 ——
    所以它们可以、也确实是按需加载的。channelhub.warmup() 的注释里早就写了
    这一点并标了「别删」：它是正确性依赖，不是性能优化。
    省内存请调 SUPERRAN_BLAS_THREADS。
    """
    source = (SRC / "superran" / "server.py").read_text(encoding="utf-8")
    assert "info = ch.warmup()" in source, "main() 里必须仍然调用 ch.warmup()"
    assert "if _EAGER_WARMUP" not in source, "不许再给 warmup 加开关"
    assert "SUPERRAN_EAGER_WARMUP" not in source
    # 只在 main() 函数体里比较先后，别匹配到上面的函数定义。
    body = source[source.index("def main() -> None:"):]
    resolve = body.index("_resolve_lazy_modules()")
    warm = body.index("info = ch.warmup()")
    run = body.index('mcp.run(transport="stdio")')
    assert resolve < warm < run, "顺序必须是 解析占位模块 → warmup → mcp.run"


def test_blas_thread_cap_is_applied_before_any_numpy_import():
    """BLAS 线程上限必须早于 numpy 第一次 import，否则不生效。

    实测（20 逻辑核，服务端空转提交内存；已含 torch 不再被拉起的收益）::

        auto（不限） 2718 MB   4 线程 365 MB   2 线程 236 MB   1 线程 172 MB

    默认取 1：按 SuperRAN 真实矩阵尺寸（逐 RB 的 4×64 / 4×256 SVD、64×64 eigh、
    2048×64 ifft）实测，多线程 BLAS 没有可测收益甚至更慢；脚本模式跑
    tests/test_gates.py 也是 1 线程 87.5s vs 4 线程 88.1s，差异在噪声内。

    这是**性能取舍不是精度取舍**：只影响大矩阵运算的墙钟时间，数值逐位不变。
    """
    source = (SRC / "superran" / "server.py").read_text(encoding="utf-8")
    # 只在 main() 函数体里比较先后，别匹配到上面的函数定义。
    body = source[source.index("def main() -> None:"):]
    cap = body.index("cap = _apply_blas_thread_cap()")
    resolve = body.index("_resolve_lazy_modules()")
    assert cap < resolve, "线程上限必须在解析占位模块（会 import numpy）之前设好"
    assert "SUPERRAN_BLAS_THREADS" in source

    # 用户显式设过就不覆盖
    out = _run(
        "import os\n"
        "os.environ['OPENBLAS_NUM_THREADS'] = '9'\n"
        "from superran.server import _apply_blas_thread_cap\n"
        "import json\n"
        "_apply_blas_thread_cap()\n"
        "print(json.dumps({'openblas': os.environ['OPENBLAS_NUM_THREADS'],"
        " 'omp': os.environ.get('OMP_NUM_THREADS')}))"
    )
    assert out["openblas"] == "9", "用户显式设的线程数不得被覆盖"
    assert out["omp"] is not None, "没设过的那些要补上默认值"

    # auto / off 表示完全不干预
    out = _run(
        "import os\n"
        "os.environ['SUPERRAN_BLAS_THREADS'] = 'auto'\n"
        "import importlib, superran.server as S, json\n"
        "importlib.reload(S)\n"
        "print(json.dumps({'cap': S._apply_blas_thread_cap(),"
        " 'openblas': os.environ.get('OPENBLAS_NUM_THREADS')}))"
    )
    assert out["cap"] is None
    assert out["openblas"] is None, "auto 档不该往环境里写任何线程数"


def test_lazy_module_does_not_leak_through_dunder():
    """dunder 属性不得代理 —— 否则 copy/pickle/inspect 的探测会把目标模块拖进来。"""
    out = _run(
        "from superran._lazy import lazy_module\n"
        "import json\n"
        "m = lazy_module('numpy')\n"
        "missing = False\n"
        "try:\n"
        "    m.__wrapped__\n"
        "except AttributeError:\n"
        "    missing = True\n"
        "print(json.dumps({'raised': missing, 'numpy': 'numpy' in sys.modules,"
        " 'loaded': m.lazy_is_loaded}))"
    )
    assert out["raised"] is True
    assert out["numpy"] is False, "探测 dunder 不该触发加载"
    assert out["loaded"] is False


if __name__ == "__main__":
    # run_test_matrix.py 用 `python tests/<file>.py` 跑每个文件。没有这个入口，
    # pytest 式文件会「什么都不做地退出 0」，在矩阵里表现为假通过。
    # 见 .agents/TESTING.md 的坑 2。
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
