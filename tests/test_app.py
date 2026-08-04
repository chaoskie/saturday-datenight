"""Tests for the v2 features: rigged spin and expiry ping."""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as datenight  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(datenight, "DB_PATH", tmp_path / "test.db")
    datenight.init_db()
    datenight.app.config["TESTING"] = True
    with datenight.app.test_client() as c:
        yield c


@pytest.fixture
def planner_client(client):
    with client.session_transaction() as sess:
        sess["planner"] = True
    return client


def add_idea(title, **cols):
    """Insert an idea directly and return its id."""
    cols = {"title": title, **cols}
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    db = sqlite3.connect(datenight.DB_PATH)
    try:
        cur = db.execute(f"INSERT INTO ideas ({keys}) VALUES ({marks})", tuple(cols.values()))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def get_idea(idea_id):
    db = sqlite3.connect(datenight.DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        return db.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    finally:
        db.close()


def days_from_now(n):
    return (date.today() + timedelta(days=n)).isoformat()


# ---------- migration ----------

def test_init_db_adds_columns_to_pre_v2_database(tmp_path, monkeypatch):
    """A database created before v2 gains the new columns idempotently."""
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE ideas (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
    monkeypatch.setattr(datenight, "DB_PATH", db_path)
    datenight.init_db()
    datenight.init_db()  # idempotent second run
    with sqlite3.connect(db_path) as db:
        cols = {r[1] for r in db.execute("PRAGMA table_info(ideas)")}
    assert {"rigged_next", "expiry_notified_at"} <= cols


# ---------- rigged spin ----------

def test_spin_lands_on_rigged_idea_and_clears_flag(client):
    ids = [add_idea(f"idea {n}") for n in range(3)]
    rigged_id = ids[1]
    with sqlite3.connect(datenight.DB_PATH) as db:
        db.execute("UPDATE ideas SET rigged_next = 1 WHERE id = ?", (rigged_id,))

    resp = client.post("/spin")
    data = resp.get_json()
    assert resp.status_code == 200
    assert set(data) == {"segment", "count", "redacted"}  # payload shape unchanged
    assert data["count"] == 3
    assert data["segment"] == 1  # eligible_ideas orders by id; ids[1] is index 1
    assert get_idea(rigged_id)["rigged_next"] == 0  # one-shot: consumed

    # the pick recorded is the rigged idea
    with sqlite3.connect(datenight.DB_PATH) as db:
        (picked,) = db.execute("SELECT idea_id FROM picks ORDER BY id DESC LIMIT 1").fetchone()
    assert picked == rigged_id


def test_rig_falls_back_to_fair_spin_when_rigged_idea_ineligible(client):
    keep = add_idea("kept")
    banned = add_idea("banned later", banned=1, rigged_next=1)

    resp = client.post("/spin")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["count"] == 1 and data["segment"] == 0
    # flag cleared even though the rig was never used
    assert get_idea(banned)["rigged_next"] == 0
    with sqlite3.connect(datenight.DB_PATH) as db:
        (picked,) = db.execute("SELECT idea_id FROM picks ORDER BY id DESC LIMIT 1").fetchone()
    assert picked == keep


def test_rig_is_consumed_exactly_once(client):
    ids = [add_idea(f"idea {n}") for n in range(2)]
    with sqlite3.connect(datenight.DB_PATH) as db:
        db.execute("UPDATE ideas SET rigged_next = 1 WHERE id = ?", (ids[0],))
    assert client.post("/spin").get_json()["segment"] == 0
    # second spin is fair: no idea rigged any more
    with sqlite3.connect(datenight.DB_PATH) as db:
        (n,) = db.execute("SELECT COUNT(*) FROM ideas WHERE rigged_next = 1").fetchone()
    assert n == 0


def test_fair_spin_untouched_without_rig(client):
    for n in range(4):
        add_idea(f"idea {n}")
    data = client.post("/spin").get_json()
    assert data["count"] == 4
    assert 0 <= data["segment"] < 4


def test_rig_route_toggles_and_enforces_single_rig(planner_client):
    a = add_idea("a")
    b = add_idea("b")
    planner_client.post(f"/planner/ideas/{a}/rig")
    assert get_idea(a)["rigged_next"] == 1
    # rigging b clears a
    planner_client.post(f"/planner/ideas/{b}/rig")
    assert get_idea(a)["rigged_next"] == 0
    assert get_idea(b)["rigged_next"] == 1
    # toggling b again un-rigs it
    planner_client.post(f"/planner/ideas/{b}/rig")
    assert get_idea(b)["rigged_next"] == 0


def test_rig_route_requires_planner_login(client):
    a = add_idea("a")
    resp = client.post(f"/planner/ideas/{a}/rig")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_rig_route_404s_on_unknown_idea(planner_client):
    assert planner_client.post("/planner/ideas/9999/rig").status_code == 404


def test_no_rig_marker_on_public_page(planner_client):
    add_idea("secret trip", rigged_next=1)
    html = planner_client.get("/").get_data(as_text=True)
    assert "rigged" not in html.lower()
    assert "🎯" not in html and "🎁" not in html


# ---------- expiry ping ----------

def app_db():
    ctx = datenight.app.app_context()
    ctx.push()
    return datenight.get_db(), ctx


def test_ideas_expiring_soon_window(client):
    soon = add_idea("soon", limited_until=days_from_now(5))
    add_idea("far", limited_until=days_from_now(30))
    add_idea("past", limited_until=days_from_now(-1))
    add_idea("evergreen")
    add_idea("banned", limited_until=days_from_now(5), banned=1)
    add_idea("already pinged", limited_until=days_from_now(5),
             expiry_notified_at="2026-01-01T00:00:00")
    db, ctx = app_db()
    try:
        rows = datenight.ideas_expiring_soon(db, days=14)
        assert [r["id"] for r in rows] == [soon]
    finally:
        ctx.pop()


def test_check_expiry_notifications_marks_only_on_success(client, monkeypatch):
    monkeypatch.setattr(datenight, "NTFY_URL", "https://ntfy.example")
    monkeypatch.setattr(datenight, "NTFY_TOPIC", "datenight-test")
    ok = add_idea("book me", limited_until=days_from_now(3))
    failing = add_idea("flaky", limited_until=days_from_now(4))
    sent = []

    def fake_notify(idea, days_left):
        sent.append((idea["title"], days_left))
        return idea["id"] == ok

    monkeypatch.setattr(datenight, "notify_expiring", fake_notify)
    db, ctx = app_db()
    try:
        datenight.check_expiry_notifications(db)
    finally:
        ctx.pop()
    assert ("book me", 3) in sent and ("flaky", 4) in sent
    assert get_idea(ok)["expiry_notified_at"] is not None
    assert get_idea(failing)["expiry_notified_at"] is None  # failed send retries later

    # second pass: success is one-shot, failure is retried
    sent.clear()
    db, ctx = app_db()
    try:
        datenight.check_expiry_notifications(db)
    finally:
        ctx.pop()
    assert [t for t, _ in sent] == ["flaky"]


def test_expiry_check_disabled_without_ntfy_config(client, monkeypatch):
    monkeypatch.setattr(datenight, "NTFY_URL", "")
    monkeypatch.setattr(datenight, "NTFY_TOPIC", "")
    add_idea("soon", limited_until=days_from_now(2))

    def boom(*a, **k):
        raise AssertionError("must not attempt to notify")

    monkeypatch.setattr(datenight, "notify_expiring", boom)
    db, ctx = app_db()
    try:
        datenight.check_expiry_notifications(db)
    finally:
        ctx.pop()
    assert client.get("/").status_code == 200  # index runs the check safely


def test_notify_expiring_posts_to_ntfy(client, monkeypatch):
    monkeypatch.setattr(datenight, "NTFY_URL", "https://ntfy.example")
    monkeypatch.setattr(datenight, "NTFY_TOPIC", "datenight-test")
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode()
        captured["title"] = req.get_header("Title")
        return FakeResp()

    monkeypatch.setattr(datenight.urllib.request, "urlopen", fake_urlopen)
    idea = {"title": "Pop-up museum", "limited_until": days_from_now(1)}
    assert datenight.notify_expiring(idea, 1) is True
    assert captured["url"] == "https://ntfy.example/datenight-test"
    assert "Pop-up museum" in captured["body"] and "1 day left" in captured["body"]
    assert captured["title"] == "Date idea expiring soon"


def test_update_idea_resets_expiry_notified_when_date_changes(planner_client):
    idea_id = add_idea("stale ping", limited_until=days_from_now(5),
                       expiry_notified_at="2026-01-01T00:00:00")
    form = {"title": "stale ping", "dress_code": "casual", "dinner_included": "0",
            "category": "going-out", "limited_until": days_from_now(20)}
    planner_client.post(f"/planner/ideas/{idea_id}", data=form)
    assert get_idea(idea_id)["expiry_notified_at"] is None  # re-armed

    # saving without changing limited_until keeps the mark
    with sqlite3.connect(datenight.DB_PATH) as db:
        db.execute("UPDATE ideas SET expiry_notified_at = '2026-02-01T00:00:00' WHERE id = ?",
                   (idea_id,))
    planner_client.post(f"/planner/ideas/{idea_id}", data=form)
    assert get_idea(idea_id)["expiry_notified_at"] == "2026-02-01T00:00:00"
