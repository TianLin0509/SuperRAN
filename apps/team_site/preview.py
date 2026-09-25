"""Start a local-only preview with explicitly fictional data; no production access."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

from admin import issue
from report import Reporter
from server import Store


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--directory", required=True, help="独立的本机预览目录，不能指向正式数据")
    p.add_argument("--port", type=int, default=18770)
    a = p.parse_args()
    directory = Path(a.directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "preview.json"
    origin = f"http://127.0.0.1:{a.port}"
    site = origin + "/superran"
    if marker.exists():
        settings = json.loads(marker.read_text(encoding="utf-8"))
        if settings["site"] != site:
            raise ValueError("该预览目录已绑定另一个端口")
    elif any(directory.iterdir()):
        raise ValueError("初次预览必须使用空目录，不能接管已有数据")
    # Probe only. Never kill a process that owns the desired port.
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", a.port))
        except OSError:
            if marker.exists():
                print("Preview may already be running: " + site + "/")
                return
            raise ValueError("端口已占用，请选择其他端口")
    store = Store(directory / "data")
    configs = []
    for number, (name, role) in enumerate((("负责人（演示）", "admin"), ("成员甲（演示）", "member"), ("成员乙（演示）", "member"))):
        path = directory / f"member-{number}.json"
        if not path.exists():
            issue(store, name, role, path, site)
        configs.append(json.loads(path.read_text(encoding="utf-8")))
    if not marker.exists():
        marker.write_text(json.dumps({"site": site, "seeded": False}), encoding="utf-8")
    with (directory / "server.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("server.py")), "--data", str(directory / "data"), "--origin", origin, "--port", str(a.port)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    reporter = Reporter(configs[0], directory / "leader.outbox.sqlite")
    for _ in range(100):
        try:
            reporter.request("state")
            break
        except OSError:
            time.sleep(.1)
    else:
        raise RuntimeError("预览启动失败，请检查专属 server.log")
    settings = json.loads(marker.read_text(encoding="utf-8"))
    if not settings["seeded"]:
        for i, title, status, progress in (
            (1, "核对 CSI 反馈时序", "进行中", "演示数据：已梳理反馈采样与调度使用的时间关系，正在补充边界场景。"),
            (2, "补充调度回归场景", "受阻", "演示数据：场景已整理，等待确认业务到达假设。"),
            (1, "整理信道实验使用说明", "已完成", "演示数据：说明已整理，供小组讨论；不代表实际代码或实验已完成。"),
        ):
            r = Reporter(configs[i], directory / f"member-{i}.outbox.sqlite")
            r.enqueue(str(uuid4()), dict(title=title, status=status, progress=progress), "company-agent" if i == 2 else "codex", new=True)
            if not r.flush()["ok"]:
                raise RuntimeError("演示记录同步失败")
        settings["seeded"] = True
    settings["pid"] = process.pid
    marker.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(json.dumps({"url": site+"/", "pid": process.pid}, ensure_ascii=False))


if __name__ == "__main__":
    main()
