"""superran MCP 服务端入口（自包含，不需要先安装包）。

注册到 agent 时直接指向这个文件即可：

    claude mcp add superran -- <python> <此文件绝对路径>
    codex  mcp add superran -- <python> <此文件绝对路径>
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from superran.server import main  # noqa: E402

if __name__ == "__main__":
    main()
