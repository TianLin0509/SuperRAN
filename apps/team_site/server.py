"""Small shared work log. Run separately from the simulation and weekly site."""
import argparse
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parent
FIELDS = {"title", "status", "progress", "result_url"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Store:
    def __init__(self, directory):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "superran.sqlite"
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS members(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL, browser_hash TEXT NOT NULL UNIQUE,
                    agent_hash TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS sessions(
                    hash TEXT PRIMARY KEY, member TEXT NOT NULL REFERENCES members(id), expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS works(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES members(id),
                    revision INTEGER NOT NULL, fields TEXT NOT NULL, locks TEXT NOT NULL,
                    updated REAL NOT NULL, source TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    member TEXT NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    work TEXT NOT NULL REFERENCES works(id), at REAL NOT NULL,
                    source TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY(member,id));
                CREATE INDEX IF NOT EXISTS work_events ON events(work,at);
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Body):
    token: str = Field(min_length=32, max_length=200)


class Changes(Body):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["进行中", "受阻", "已完成"] | None = None
    progress: str | None = Field(default=None, max_length=12000)
    result_url: str | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def title_nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("标题不能为空")
        return value

    @field_validator("result_url")
    @classmethod
    def safe_url(cls, value):
        if value:
            url = urlsplit(value)
            if url.scheme not in ("https", "http") or not url.hostname or url.username or any(ord(c) < 33 for c in value):
                raise ValueError("成果链接须为 HTTP(S) 地址")
        return value


class Update(Body):
    event_id: UUID
    expected_revision: int = Field(ge=0)
    changes: Changes
    release: list[Literal["title", "status", "progress", "result_url"]] = Field(default_factory=list, max_length=4)
    source: Literal["codex", "company-agent", "agent", "human"] = "agent"


def public_work(row):
    return {"id": row["id"], "owner": row["owner"], "revision": row["revision"],
            **json.loads(row["fields"]), "locks": json.loads(row["locks"]),
            "updated": row["updated"], "source": row["source"]}


def create_app(directory, origin="http://127.0.0.1:18770", base_path="/superran"):
    origin = origin.rstrip("/")
    parts = urlsplit(origin)
    if not parts.netloc or parts.path or parts.query or parts.fragment or parts.username or parts.scheme not in ("http", "https"):
        raise ValueError("origin must be a bare HTTP(S) origin")
    if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost", "testserver"):
        raise ValueError("HTTPS required outside localhost")
    if base_path and (not base_path.startswith("/") or base_path.endswith("/") or ".." in base_path):
        raise ValueError("base_path must be /superran or empty")
    store = Store(directory)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    prefix = base_path
    cookie_path = prefix + "/"

    @app.middleware("http")
    async def protect(request, call_next):
        if request.headers.get("host") != parts.netloc:
            return JSONResponse({"detail": "访问域名不匹配"}, status_code=400)
        if request.method not in ("GET", "HEAD"):
            if request.headers.get("origin", origin) != origin or request.headers.get("x-superran-request") != "1":
                return JSONResponse({"detail": "请求来源不正确"}, status_code=403)
            if len(await request.body()) > 100000:
                return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    def user(request):
        bearer = request.headers.get("authorization", "")
        with store.db() as db:
            if bearer:
                row = db.execute("SELECT * FROM members WHERE agent_hash=?", (digest(bearer.removeprefix("Bearer ")),)).fetchone() if bearer.startswith("Bearer ") else None
                kind = "agent"
            else:
                row = db.execute("SELECT m.* FROM sessions s JOIN members m ON m.id=s.member WHERE s.hash=? AND s.expires>?",
                                 (digest(request.cookies.get("superran_session", "")), time.time())).fetchone()
                kind = "human"
        if not row:
            raise HTTPException(401, "请使用个人访问码登录")
        return dict(row), kind

    @app.get(prefix + "/")
    def page():
        return FileResponse(ROOT / "web.html")

    @app.get(prefix + "/web.js")
    def script():
        return FileResponse(ROOT / "web.js", media_type="text/javascript")

    @app.get(prefix + "/web.css")
    def style():
        return FileResponse(ROOT / "web.css", media_type="text/css")

    @app.get(prefix + "/api/health")
    def health():
        with store.db() as db:
            db.execute("SELECT 1 FROM works LIMIT 1").fetchone()
        return {"ok": True}

    @app.post(prefix + "/api/login")
    def login(body: Login, response: Response):
        with store.db(True) as db:
            row = db.execute("SELECT id FROM members WHERE browser_hash=?", (digest(body.token),)).fetchone()
            if not row:
                raise HTTPException(401, "访问码不正确")
            token = secrets.token_urlsafe(32)
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), row[0], time.time()+86400*7))
        response.set_cookie("superran_session", token, max_age=86400*7, httponly=True, secure=parts.scheme == "https", samesite="strict", path=cookie_path)
        return {"ok": True}

    @app.post(prefix + "/api/logout")
    def logout(request: Request, response: Response):
        with store.db(True) as db:
            db.execute("DELETE FROM sessions WHERE hash=?", (digest(request.cookies.get("superran_session", "")),))
        response.delete_cookie("superran_session", path=cookie_path)
        return {"ok": True}

    @app.get(prefix + "/api/state")
    def state(request: Request):
        me, _ = user(request)
        with store.db() as db:
            return {"me": {k: me[k] for k in ("id", "name", "role")},
                    "members": [dict(r) for r in db.execute("SELECT id,name FROM members ORDER BY name")],
                    "works": [public_work(r) for r in db.execute("SELECT * FROM works ORDER BY updated DESC")], "at": time.time()}

    @app.get(prefix + "/api/works/{wid}")
    def detail(wid: UUID, request: Request):
        user(request)
        with store.db() as db:
            row = db.execute("SELECT * FROM works WHERE id=?", (str(wid),)).fetchone()
            if not row:
                raise HTTPException(404, "记录不存在")
            history = []
            for event in db.execute("SELECT e.*,m.name FROM events e JOIN members m ON e.member=m.id WHERE work=? ORDER BY at DESC LIMIT 100", (str(wid),)):
                history.append({"at": event["at"], "source": event["source"], "name": event["name"],
                                "request": json.loads(event["request"]), "result": json.loads(event["response"])})
            return {"work": public_work(row), "history": history}

    @app.put(prefix + "/api/works/{wid}")
    def update(wid: UUID, body: Update, request: Request):
        me, kind = user(request)
        wid = str(wid)
        raw = body.model_dump(mode="json")
        fingerprint = digest(packed({"work": wid, "kind": kind, "body": raw}))
        changes = body.changes.model_dump(exclude_unset=True)
        if any(v is None for v in changes.values()) or (not changes and not body.release):
            raise HTTPException(422, "请提供修改内容")
        if set(body.release) & set(changes):
            raise HTTPException(422, "同一字段不能同时修改和交回 agent")
        if kind == "agent" and (body.release or body.source == "human"):
            raise HTTPException(403, "agent 不能解除人工保护或冒充人工修改")
        source = "human" if kind == "human" else body.source
        with store.db(True) as db:
            row = db.execute("SELECT * FROM works WHERE id=?", (wid,)).fetchone()
            if row and row["owner"] != me["id"] and not (kind == "human" and me["role"] == "admin"):
                raise HTTPException(403, "只能修改自己的记录")
            seen = db.execute("SELECT * FROM events WHERE member=? AND id=?", (me["id"], str(body.event_id))).fetchone()
            if seen:
                if seen["fingerprint"] != fingerprint:
                    raise HTTPException(409, "该请求编号已用于不同内容")
                return json.loads(seen["response"])
            current = row["revision"] if row else 0
            if current != body.expected_revision:
                raise HTTPException(409, {"message": "记录已更新，请先核对最新内容；草稿已保留", "revision": current})
            if not row and (not changes.get("title") or body.release):
                raise HTTPException(422, "新记录需要标题")
            values = json.loads(row["fields"]) if row else {"title": "", "status": "进行中", "progress": "", "result_url": ""}
            locks = set(json.loads(row["locks"])) if row else set()
            protected = sorted(set(changes) & locks) if kind == "agent" else []
            applied = sorted(set(changes) - set(protected))
            for key in applied:
                values[key] = changes[key]
                if kind == "human":
                    locks.add(key)
            locks.difference_update(body.release)
            now = time.time()
            db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,fields=excluded.fields,locks=excluded.locks,updated=excluded.updated,source=excluded.source",
                       (wid, row["owner"] if row else me["id"], current+1, packed(values), packed(sorted(locks)), now, source))
            fresh = db.execute("SELECT * FROM works WHERE id=?", (wid,)).fetchone()
            result = {"work": public_work(fresh), "applied": applied, "protected": protected, "released": body.release}
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (me["id"], str(body.event_id), fingerprint, wid, now, source, packed(raw), packed(result)))
            return result

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--origin", default="http://127.0.0.1:18770")
    parser.add_argument("--base-path", default="/superran")
    parser.add_argument("--port", type=int, default=18770)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.data, args.origin, args.base_path), host="127.0.0.1", port=args.port, access_log=False)
