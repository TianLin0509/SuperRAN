import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admin import issue
from report import Reporter
from server import Store, create_app


@pytest.fixture
def site(tmp_path):
    app = create_app(tmp_path / "data", "http://testserver")
    store = app.state.store
    configs = []
    for name, role in (("甲", "admin"), ("乙", "member"), ("丙", "member")):
        path = tmp_path / (name+".json")
        issue(store, name, role, path, "http://127.0.0.1:18770/superran")
        configs.append(json.loads(path.read_text(encoding="utf-8")))
    client = TestClient(app)
    client.headers["X-SuperRAN-Request"] = "1"
    return client, configs, store


def headers(config):
    return {"Authorization": "Bearer "+config["agent_token"]}


def write(client, config, wid=None, revision=0, changes=None, **kwargs):
    wid = wid or str(uuid4())
    body = {"event_id": str(uuid4()), "expected_revision": revision,
            "changes": {"title": "无线验证", "progress": "尚未自测"} if changes is None else changes, "source": "codex", **kwargs}
    return client.put("/superran/api/works/"+wid, json=body, headers=headers(config)), wid, body


def human(client, config):
    result = client.post("/superran/api/login", json={"token": config["browser_token"]})
    assert result.status_code == 200


def test_auth_and_all_members_visibility(site):
    client, configs, _ = site
    assert client.get("/superran/api/state").status_code == 401
    result, wid, _ = write(client, configs[1])
    assert result.status_code == 200
    assert client.get("/superran/api/state", headers=headers(configs[2])).json()["works"][0]["id"] == wid
    assert client.get("/superran/api/works/"+wid, headers=headers(configs[2])).status_code == 200
    for config in (configs[0], configs[2]):  # Even the leader's agent cannot edit colleagues.
        assert write(client, config, wid, 1, {"progress": "越权"})[0].status_code == 403
    human(client, configs[0])
    assert client.put("/superran/api/works/"+wid, json={"event_id": str(uuid4()), "expected_revision": 1, "changes": {"progress": "负责人修订"}}).status_code == 200


def test_idempotency_stale_revision_and_payload_binding(site):
    client, configs, store = site
    result, wid, body = write(client, configs[1])
    assert client.put("/superran/api/works/"+wid, json=body, headers=headers(configs[1])).json() == result.json()
    assert write(client, configs[1], wid, 0, {"progress": "旧版本"})[0].status_code == 409
    body["changes"]["progress"] = "同编号不同内容"
    assert client.put("/superran/api/works/"+wid, json=body, headers=headers(configs[1])).status_code == 409
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_manual_locks_partial_apply_history_release(site):
    client, configs, _ = site
    _, wid, _ = write(client, configs[1])
    human(client, configs[1])
    url = "/superran/api/works/"+wid
    result = client.put(url, json={"event_id": str(uuid4()), "expected_revision": 1, "changes": {"progress": "人工纠正：尚未验证"}})
    assert result.json()["work"]["locks"] == ["progress"]
    result, _, _ = write(client, configs[1], wid, 2, {"progress": "错误地宣称完成", "status": "受阻"})
    assert result.json()["protected"] == ["progress"]
    assert result.json()["work"]["progress"] == "人工纠正：尚未验证"
    assert result.json()["work"]["status"] == "受阻"
    assert client.get(url).json()["history"][0]["request"]["changes"]["progress"] == "错误地宣称完成"
    assert write(client, configs[1], wid, 3, {}, release=["progress"])[0].status_code == 403
    assert client.put(url, json={"event_id": str(uuid4()), "expected_revision": 3, "changes": {}, "release": ["progress"]}).status_code == 200
    result, _, _ = write(client, configs[1], wid, 4, {"progress": "新结果"})
    assert result.json()["work"]["progress"] == "新结果"


@pytest.mark.parametrize("changes", [
    {"title": " "}, {"title": "x"*161}, {"status": "review"}, {"progress": None},
    {"progress": "x"*12001}, {"result_url": "javascript:alert(1)"},
    {"result_url": "https://user:password@example.com"}, {"unknown": "x"},
    {"result_url": "https://example.com/\nattack"}, {},
])
def test_invalid_updates(site, changes):
    client, configs, _ = site
    _, wid, _ = write(client, configs[0])
    assert write(client, configs[0], wid, 1, changes)[0].status_code == 422


def test_origin_headers_and_source(site):
    client, configs, _ = site
    assert client.post("/superran/api/login", json={"token": configs[0]["browser_token"]}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert client.get("/superran/api/state", headers={"Host": "attacker.invalid"}).status_code == 400
    assert client.post("/superran/api/login", json={"token": configs[0]["agent_token"]}).status_code == 401
    assert write(client, configs[0], source="human")[0].status_code == 403
    human(client, configs[0])
    assert client.cookies.get("superran_session")
    assert client.post("/superran/api/logout", json={}).status_code == 200
    assert client.get("/superran/api/state").status_code == 401
    client.headers.pop("X-SuperRAN-Request")
    assert client.post("/superran/api/login", json={"token": configs[0]["browser_token"]}).status_code == 403


def test_restart_persists_and_rotation_revokes(site, tmp_path):
    client, configs, store = site
    result, wid, _ = write(client, configs[1])
    human(client, configs[1])
    app2 = create_app(store.path.parent, "http://testserver")
    assert TestClient(app2).get("/superran/api/works/"+wid, headers=headers(configs[1])).json()["work"] == result.json()["work"]
    issue(store, "乙", "member", tmp_path / "rotated.json", configs[1]["site"], rotate=True)
    assert client.get("/superran/api/state", headers=headers(configs[1])).status_code == 401
    assert client.get("/superran/api/state").status_code == 401


def adapt_reporter(reporter, client, config):
    def request(path, method="GET", body=None):
        response = client.request(method, "/superran/api/"+path, json=body, headers=headers(config))
        if response.status_code >= 400:
            raise HTTPError("http://local/", response.status_code, "rejected", {}, BytesIO(response.content))
        return response.json()
    reporter.request = request
    return request


def test_offline_lost_receipt_restart_no_duplicate(site, tmp_path):
    client, configs, store = site
    outbox = tmp_path / "outbox.sqlite"
    reporter = Reporter(configs[1], outbox)
    wid = reporter.enqueue(str(uuid4()), {"title": "断线恢复", "progress": "第一条"}, new=True)
    def offline(*args):
        raise URLError("offline")
    reporter.request = offline
    assert reporter.flush()["reason"] == "offline"
    reporter = Reporter(configs[1], outbox)
    send = adapt_reporter(reporter, client, configs[1])
    def lose_receipt(*args):
        send(*args)
        raise URLError("response lost after commit")
    reporter.request = lose_receipt
    assert reporter.flush()["ok"] is False
    reporter = Reporter(configs[1], outbox)
    adapt_reporter(reporter, client, configs[1])
    reporter.enqueue(wid, {"progress": "第二条"})
    assert reporter.flush()["ok"] is True
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        assert json.loads(db.execute("SELECT fields FROM works").fetchone()[0])["progress"] == "第二条"


def test_reporter_conflict_and_explicit_resolution(site, tmp_path):
    client, configs, _ = site
    _, wid, _ = write(client, configs[1])
    reporter = Reporter(configs[1], tmp_path / "queue.sqlite")
    adapt_reporter(reporter, client, configs[1])
    reporter.attach(wid)
    write(client, configs[1], wid, 1, {"progress": "另一电脑更新"})
    reporter.enqueue(wid, {"progress": "我的新结果"})
    assert reporter.flush()["reason"] == "conflict"
    assert reporter.flush()["reason"] == "conflict"
    with pytest.raises(ValueError):
        reporter.resolve(wid, 1)
    reporter.resolve(wid, 2)
    assert reporter.flush()["ok"]
    assert client.get("/superran/api/works/"+wid, headers=headers(configs[1])).json()["work"]["progress"] == "我的新结果"


def test_reporter_identity_and_attach_permissions(site, tmp_path):
    client, configs, _ = site
    path = tmp_path / "queue.sqlite"
    reporter = Reporter(configs[1], path)
    with pytest.raises(ValueError):
        Reporter(configs[2], path)
    _, wid, _ = write(client, configs[2])
    adapt_reporter(reporter, client, configs[1])
    with pytest.raises(ValueError):
        reporter.attach(wid)


def test_concurrent_same_revision_one_winner(site):
    from concurrent.futures import ThreadPoolExecutor
    client, configs, store = site
    _, wid, _ = write(client, configs[1])
    def update(value):
        return write(client, configs[1], wid, 1, {"progress": value})[0].status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["电脑甲", "电脑乙"]))
    assert sorted(results) == [200, 409]
    with store.db() as db:
        assert db.execute("SELECT revision FROM works WHERE id=?", (wid,)).fetchone()[0] == 2


def test_browser_cookie_is_http_only_https(tmp_path):
    app = create_app(tmp_path / "data", "https://example.com")
    private = tmp_path / "private.json"
    issue(app.state.store, "组员", "member", private, "https://example.com/superran")
    config = json.loads(private.read_text(encoding="utf-8"))
    client = TestClient(app, base_url="https://example.com")
    response = client.post("/superran/api/login", json={"token": config["browser_token"]}, headers={"X-SuperRAN-Request": "1"})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Path=/superran/" in cookie
    assert client.get("/superran/api/state").status_code == 200


def test_static_prefix_and_no_private_files(site):
    client, _, _ = site
    for path in ("", "web.js", "web.css"):
        assert client.get("/superran/"+path).status_code == 200
    for path in ("server.py", "admin.py", "superran.sqlite", "private.json"):
        assert client.get("/superran/"+path).status_code == 404
    assert "script-src 'self'" in client.get("/superran/").headers["content-security-policy"]


@pytest.mark.parametrize("changes", [{"title": " "}, {"progress": "x"*12001}, {"status": "unknown"}, {"result_url": "javascript:alert(1)"}, {"unknown": "field"}])
def test_reporter_rejects_invalid_content_before_queue(site, tmp_path, changes):
    _, configs, _ = site
    reporter = Reporter(configs[1], tmp_path / "queue.sqlite")
    with pytest.raises(ValueError):
        reporter.enqueue(str(uuid4()), changes, new=True)
    with reporter.db() as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
