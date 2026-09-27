"""Agent-side reporter: Python stdlib only; durable ordered outbox, no inbound port."""
import argparse
import json
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import UUID, uuid4


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never send a credential to a redirect target.


class Reporter:
    def __init__(self, config, outbox):
        self.config = config
        site = config["site"].rstrip("/")
        url = urlsplit(site)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost")):
            raise ValueError("接入地址需要 HTTPS；仅本机测试可用 HTTP")
        if not url.hostname or url.username or url.query or url.fragment:
            raise ValueError("接入地址不正确")
        self.site = site
        self.path = Path(outbox).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS identity(site TEXT,member TEXT);
                CREATE TABLE IF NOT EXISTS work(id TEXT PRIMARY KEY,revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT,work TEXT NOT NULL,
                    event TEXT NOT NULL,changes TEXT NOT NULL,source TEXT NOT NULL,
                    body TEXT,state TEXT NOT NULL DEFAULT 'pending',error TEXT);
            """)
            identity = db.execute("SELECT * FROM identity").fetchone()
            if identity and (identity[0] != site or identity[1] != config["member"]):
                raise ValueError("该待传队列属于另一站点或成员，不能混用")
            if not identity:
                db.execute("INSERT INTO identity VALUES(?,?)", (site, config["member"]))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=35)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def db(self):
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = Request(self.site + "/api/" + path, data=data, method=method,
                      headers={"Authorization": "Bearer " + self.config["agent_token"],
                               "Content-Type": "application/json", "X-SuperRAN-Request": "1"})
        with build_opener(NoRedirect()).open(req, timeout=15) as response:
            return json.load(response)

    def attach(self, wid):
        wid = str(UUID(wid))
        current = self.request("works/" + wid)["work"]
        if current["owner"] != self.config["member"]:
            raise ValueError("只能接续自己的工作记录")
        with self.db() as db:
            if db.execute("SELECT 1 FROM outbox WHERE work=? AND state!='sent'", (wid,)).fetchone():
                raise ValueError("该记录有待传内容，请先补传或核对冲突")
            db.execute("INSERT INTO work VALUES(?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision", (wid, current["revision"]))
        return current

    def enqueue(self, wid, changes, source="codex", new=False):
        wid = str(UUID(wid))
        limits = {"title": 160, "progress": 12000, "result_url": 2000, "status": 8}
        if not changes or not set(changes) <= set(limits) or source not in ("codex", "company-agent", "agent"):
            raise ValueError("上报字段或来源不正确")
        if any(not isinstance(v, str) or len(v) > limits[k] for k, v in changes.items()):
            raise ValueError("上报内容必须为文本且不能超过字段长度限制")
        if "title" in changes and not changes["title"].strip():
            raise ValueError("标题不能为空")
        if new and not changes.get("title"):
            raise ValueError("新记录需要标题")
        if "status" in changes and changes["status"] not in ("进行中", "受阻", "已完成"):
            raise ValueError("状态不正确")
        if changes.get("result_url"):
            url = urlsplit(changes["result_url"])
            if url.scheme not in ("http", "https") or not url.hostname or url.username or any(ord(c) < 33 for c in changes["result_url"]):
                raise ValueError("成果链接须为 HTTP(S) 地址")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if new:
                db.execute("INSERT INTO work VALUES(?,0)", (wid,))
            if not db.execute("SELECT 1 FROM work WHERE id=?", (wid,)).fetchone():
                raise ValueError("未绑定工作编号；先 attach 原编号，勿另建重复记录")
            db.execute("INSERT INTO outbox(work,event,changes,source) VALUES(?,?,?,?)",
                       (wid, str(uuid4()), json.dumps(changes, ensure_ascii=False), source))
        return wid

    def flush(self):
        receipts = []
        # A write lock serializes processes on this outbox. Each wire body is
        # committed BEFORE sending so a crash/lost response retries exactly it.
        while True:
            db = self.connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM outbox WHERE state!='sent' ORDER BY seq LIMIT 1").fetchone()
                if not row:
                    db.commit()
                    return {"ok": True, "receipts": receipts}
                if row["state"] == "conflict":
                    return {"ok": False, "reason": "conflict", "work_id": row["work"], "detail": row["error"], "receipts": receipts}
                if not row["body"]:
                    revision = db.execute("SELECT revision FROM work WHERE id=?", (row["work"],)).fetchone()[0]
                    body = {"event_id": row["event"], "expected_revision": revision,
                            "changes": json.loads(row["changes"]), "source": row["source"]}
                    db.execute("UPDATE outbox SET body=? WHERE seq=?", (json.dumps(body, ensure_ascii=False), row["seq"]))
                    db.commit()
                    continue
                try:
                    result = self.request("works/"+row["work"], "PUT", json.loads(row["body"]))
                except HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")[:2000]
                    if exc.code == 409:
                        db.execute("UPDATE outbox SET state='conflict',error=? WHERE seq=?", (detail, row["seq"]))
                    else:
                        db.execute("UPDATE outbox SET error=? WHERE seq=?", ("HTTP "+str(exc.code), row["seq"]))
                    db.commit()
                    return {"ok": False, "reason": "conflict" if exc.code == 409 else "http_error", "status": exc.code, "detail": detail, "work_id": row["work"], "receipts": receipts}
                except (URLError, TimeoutError, OSError) as exc:
                    db.commit()
                    return {"ok": False, "reason": "offline", "detail": type(exc).__name__, "work_id": row["work"], "receipts": receipts}
                db.execute("UPDATE work SET revision=? WHERE id=?", (result["work"]["revision"], row["work"]))
                db.execute("UPDATE outbox SET state='sent',error=NULL WHERE seq=?", (row["seq"],))
                db.commit()
                receipts.append({"work_id": row["work"], "revision": result["work"]["revision"], "applied": result["applied"], "protected": result["protected"]})
            finally:
                db.close()

    def resolve(self, wid, reviewed_revision):
        """Explicitly retry a rejected update after inspecting the current record."""
        current = self.request("works/"+str(UUID(wid)))["work"]
        if current["revision"] != reviewed_revision:
            raise ValueError("记录再次变化，请先 show 核对最新版本")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outbox WHERE work=? AND state='conflict' ORDER BY seq LIMIT 1", (wid,)).fetchone()
            if not row:
                raise ValueError("没有已确认的版本冲突；不能重写提交不明的请求")
            db.execute("UPDATE work SET revision=? WHERE id=?", (reviewed_revision, wid))
            db.execute("UPDATE outbox SET state='pending',body=NULL,event=?,error=NULL WHERE seq=?", (str(uuid4()), row["seq"]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, help="私有接入配置路径，勿提交到仓库")
    p.add_argument("--outbox", help="默认存放在配置旁；同一成员每台电脑共用一个队列")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("start", "update"):
        c = sub.add_parser(command)
        c.add_argument("--work-id", required=command == "update")
        c.add_argument("--title", required=command == "start")
        c.add_argument("--progress")
        c.add_argument("--progress-file", help="UTF-8 摘要文本；与 --progress 二选一")
        c.add_argument("--status", choices=["进行中", "受阻", "已完成"])
        c.add_argument("--result-url")
        c.add_argument("--source", choices=["codex", "company-agent", "agent"], default="codex")
    for command in ("attach", "show"):
        sub.add_parser(command).add_argument("--work-id", required=True)
    sub.add_parser("flush")
    sub.add_parser("pending")
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=int, default=30)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--work-id", required=True)
    resolve.add_argument("--reviewed-revision", type=int, required=True)
    a = p.parse_args()
    config_path = Path(a.config)
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    reporter = Reporter(config, a.outbox or config_path.with_suffix(".outbox.sqlite"))
    if a.command in ("start", "update"):
        if a.progress is not None and a.progress_file:
            p.error("--progress 与 --progress-file 二选一")
        if a.progress_file:
            a.progress = Path(a.progress_file).read_text(encoding="utf-8-sig")
        changes = {k: getattr(a,k) for k in ("title", "progress", "status", "result_url") if getattr(a,k) is not None}
        if not changes:
            p.error("至少提供一项更新")
        # Caller may preallocate an ID to persist a handoff before any network call.
        wid = a.work_id or str(uuid4())
        reporter.enqueue(wid, changes, a.source, new=a.command == "start")
        result = reporter.flush()
        result["work_id"] = wid
    elif a.command == "attach":
        result = reporter.attach(a.work_id)
    elif a.command == "show":
        result = reporter.request("works/"+str(UUID(a.work_id)))
    elif a.command == "pending":
        with reporter.db() as db:
            result = [dict(r) for r in db.execute("SELECT work,changes,state,error FROM outbox WHERE state!='sent' ORDER BY seq")]
    elif a.command == "resolve":
        reporter.resolve(a.work_id, a.reviewed_revision)
        result = reporter.flush()
    elif a.command == "watch":
        while True:
            result = reporter.flush()
            print(json.dumps(result, ensure_ascii=False), flush=True)
            time.sleep(max(5, a.interval))
    else:
        result = reporter.flush()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if isinstance(result, dict) and result.get("ok") is False else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
