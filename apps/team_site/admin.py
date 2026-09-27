"""Provision credentials into a private file, never into logs or the repository."""
import argparse
import json
import secrets
import sqlite3
from pathlib import Path
from uuid import uuid4

from server import Store, digest


def issue(store, name, role, output, site, rotate=False):
    if not name.strip() or len(name) > 80 or role not in ("admin", "member"):
        raise ValueError("成员姓名应为1到80字，角色须为 admin/member")
    browser, agent = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids destroying somebody's credential file.
    with output.open("x", encoding="utf-8") as stream:
        with store.db(True) as db:
            row = db.execute("SELECT id FROM members WHERE name=?", (name,)).fetchone()
            if row and not rotate:
                raise ValueError("成员已存在；轮换凭证请显式使用 --rotate")
            mid = row[0] if row else str(uuid4())
            if row:
                db.execute("UPDATE members SET role=?,browser_hash=?,agent_hash=? WHERE id=?", (role, digest(browser), digest(agent), mid))
                db.execute("DELETE FROM sessions WHERE member=?", (mid,))
            else:
                db.execute("INSERT INTO members VALUES(?,?,?,?,?)", (mid, name, role, digest(browser), digest(agent)))
            json.dump({"site": site.rstrip("/"), "member": mid, "name": name, "agent_token": agent}, stream, ensure_ascii=False, indent=2)
    return mid


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    sub = p.add_subparsers(dest="command", required=True)
    member = sub.add_parser("member")
    member.add_argument("--name", required=True)
    member.add_argument("--role", choices=["admin", "member"], default="member")
    member.add_argument("--output", required=True)
    member.add_argument("--site", required=True)
    member.add_argument("--rotate", action="store_true")
    backup = sub.add_parser("backup")
    backup.add_argument("--output", required=True)
    a = p.parse_args()
    store = Store(a.data)
    if a.command == "member":
        issue(store, a.name.strip(), a.role, a.output, a.site, a.rotate)
        print("Credentials written to the specified private file.")
    else:
        target = Path(a.output)
        with target.open("xb"):
            pass
        with store.db() as db, sqlite3.connect(target) as dst:
            db.backup(dst)
        print("Consistent SQLite backup complete.")
