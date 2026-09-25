"""Isolated real-network and real-browser acceptance, never writes to weekly/prod."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from playwright.sync_api import sync_playwright, expect

from admin import issue
from report import Reporter
from server import Store

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parents[1] / "output" / "playwright" / "team-site-implementation"


def run():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    checks = []
    with tempfile.TemporaryDirectory(prefix="superran-site-browser-") as directory:
        directory = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        site = origin + "/superran"
        configs = []
        store = Store(directory / "data")
        for name, role in (("负责人（演示）", "admin"), ("成员甲（演示）", "member"), ("成员乙（演示）", "member")):
            path = directory / (str(uuid4())+".json")
            issue(store, name, role, path, site)
            configs.append(json.loads(path.read_text(encoding="utf-8")))
        process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--data", str(directory / "data"), "--origin", origin, "--port", str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            reporters = [Reporter(c, directory / f"queue-{i}.sqlite") for i, c in enumerate(configs)]
            for _ in range(100):
                try:
                    reporters[0].request("state")
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("isolated server did not start")
            seeds = [
                (1, "核对 CSI 反馈时序", "进行中", "已复核反馈采样时刻与调度使用时刻。\n正在补充边界场景，尚未得出性能结论。"),
                (2, "补充调度回归场景", "受阻", "场景已整理，等待确认业务到达假设后继续验证。"),
                (1, "整理信道实验使用说明", "已完成", "说明已整理并本地验证，交付文档供小组使用。此项记录不表示代码已合并。"),
            ]
            ids = []
            for owner, title, status, progress in seeds:
                wid = reporters[owner].enqueue(str(uuid4()), dict(title=title, status=status, progress=progress), source="codex" if owner == 1 else "company-agent", new=True)
                assert reporters[owner].flush()["ok"]
                ids.append(wid)
            checks.append("Two member agents report over real HTTP into the same persistent database")
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                contexts = [browser.new_context(viewport={"width": 1440, "height": 960}) for _ in range(2)]
                pages = [c.new_page() for c in contexts]
                errors = []
                for page, config in zip(pages, configs[:2]):
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(site+"/")
                    page.locator("#token").fill(config["browser_token"])
                    page.locator("#loginForm button").click()
                    expect(page.locator(".work-row")).to_have_count(3)
                leader, member = pages
                checks.append("Two authenticated browser sessions see all three work records")
                leader.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
                member.get_by_role("button", name=seeds[0][1], exact=True).click()
                member.locator("#field-progress").fill("人工修正：测试仍在进行，不能宣称已通过。")
                member.locator("#save").click()
                expect(member.locator("#detail")).not_to_be_visible()
                expect(leader.locator("#rows")).to_contain_text("人工修正", timeout=10000)
                checks.append("Human edit synchronizes to a second browser without reload")
                reporters[1].enqueue(ids[0], {"progress": "agent 尝试覆盖", "status": "受阻"})
                assert reporters[1].flush()["reason"] == "conflict"
                latest = reporters[1].request("works/"+ids[0])["work"]
                reporters[1].resolve(ids[0], latest["revision"])
                receipt = reporters[1].flush()
                assert receipt["receipts"][0]["protected"] == ["progress"]
                expect(leader.locator("#rows")).to_contain_text("人工修正", timeout=10000)
                member.get_by_role("button", name=seeds[0][1], exact=True).click()
                expect(member.locator("#field-status")).to_have_value("受阻")
                member.locator("#history summary").click()
                expect(member.locator("#historyRows")).to_contain_text("人工保护，未采用")
                member.screenshot(path=str(OUTPUT / "manual-protection.png"), full_page=True)
                checks.append("Agent conflict is explicit; reviewed retry updates status, preserves human text and records blocked text")
                member.locator('[data-release="progress"]').check()
                member.locator("#save").click()
                reporters[1].attach(ids[0])
                reporters[1].enqueue(ids[0], {"progress": "人工交回后，agent 可以继续更新。"})
                assert reporters[1].flush()["ok"]
                expect(leader.locator("#rows")).to_contain_text("人工交回后", timeout=10000)
                checks.append("Explicit human release permits later agent updates")
                member.get_by_role("button", name=seeds[1][1], exact=True).click()
                expect(member.locator("#save")).not_to_be_visible()
                expect(member.locator("#field-progress")).to_be_disabled()
                member.locator("#closeDetail").click()
                checks.append("Colleagues can read but cannot edit another member's work")
                member.locator("#newWork").click()
                member.locator("#field-title").fill("刷新后恢复的新工作草稿")
                member.locator("#field-progress").fill("尚未提交")
                member.reload()
                expect(member.locator("#workspace")).to_be_visible()
                member.locator("#newWork").click()
                expect(member.locator("#field-title")).to_have_value("刷新后恢复的新工作草稿")
                member.locator("#discardDraft").click()
                checks.append("Unsubmitted new-record draft survives a full page reload")
                # Same record, two browser edits: keep losing browser's draft for review.
                for page in (leader, member):
                    page.get_by_role("button", name=seeds[0][1], exact=True).click()
                leader.locator("#field-progress").fill("负责人先保存的版本")
                leader.locator("#save").click()
                member.locator("#field-progress").fill("成员尚未合并的草稿")
                member.locator("#save").click()
                expect(member.locator("#editError")).to_contain_text("记录已更新")
                expect(member.locator("#field-progress")).to_have_value("成员尚未合并的草稿")
                member.locator("#reloadDetail").click()
                expect(member.locator("#historyRows")).to_contain_text("负责人先保存的版本")
                expect(member.locator("#field-progress")).to_have_value("成员尚未合并的草稿")
                member.locator("#save").click()
                expect(member.locator("#detail")).not_to_be_visible()
                checks.append("Concurrent browser save retains draft and offers current history before retry")
                leader.locator("#statusFilter").select_option("受阻")
                expect(leader.locator(".work-row")).to_have_count(2)
                leader.locator("#search").fill("调度")
                expect(leader.locator(".work-row")).to_have_count(1)
                leader.locator("#search").fill("")
                leader.locator("#statusFilter").select_option("")
                checks.append("Search and status filters use the shared records")
                # Model-provided content is plain text, not markup.
                reporters[2].enqueue(ids[1], {"progress": '<img src=x onerror="window.injected=1"> 仅显示文字'})
                assert reporters[2].flush()["ok"]
                expect(leader.locator("#rows")).to_contain_text("仅显示文字", timeout=10000)
                assert leader.evaluate("window.injected===undefined")
                assert leader.locator("#rows img").count() == 0
                checks.append("Agent HTML is escaped instead of executed")
                for width in (375, 768, 1024, 1440):
                    leader.set_viewport_size({"width": width, "height": 950})
                    assert leader.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
                    if width == 375:
                        leader.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
                checks.append("No horizontal overflow at 375/768/1024/1440 pixels")
                leader.locator("#connect").click()
                expect(leader.locator("#connectDialog")).to_be_visible()
                leader.locator("#closeConnect").click()
                member.locator("#logout").click()
                expect(member.locator("#loginPanel")).to_be_visible()
                expect(member.locator("#workspace")).not_to_be_visible()
                checks.append("Agent guidance and logout work")
                assert not errors, errors
                checks.append("No browser JavaScript errors")
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    (OUTPUT / "checks.json").write_text(json.dumps({"checks": checks, "count": len(checks), "scope": "isolated local HTTP + Edge; not cloud/company network"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    run()
