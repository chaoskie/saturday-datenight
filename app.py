"""Saturday Datenight - a self-hosted surprise date-night picker.

Public view: spin a wheel over eligible date ideas; the result is shown
REDACTED (dress code, duration, dinner, travel time) so it stays a surprise.
Planner mode (passcode) manages ideas, pins picks, books and reveals them.
"""
import json
import os
import random
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, g, jsonify, redirect, render_template,
                   request, session, url_for)

BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("DATENIGHT_DB", BASE_DIR / "data" / "datenight.db"))
PASSCODE = os.environ.get("PLANNER_PASSCODE", "changeme")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
# Hours before the booked date at which the redacted pick auto-reveals (0 = only on the day itself)
AUTO_REVEAL_HOURS = int(os.environ.get("AUTO_REVEAL_HOURS", "24"))
# Deployed version, overridable at build/run time (e.g. a git short SHA) for deploy verification
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
# Days before limited_until at which an idea counts as "expiring soon".
# Shared by the wheel-weight boost and the expiry ping so they stay in step.
EXPIRY_WARNING_DAYS = int(os.environ.get("EXPIRY_WARNING_DAYS", "14"))
# ntfy push channel for expiring limited-time ideas. Both must be set or the
# expiry ping stays disabled (no external calls, no errors).
NTFY_URL = os.environ.get("NTFY_URL", "").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

DRESS_CODES = ["casual", "smart casual", "fancy", "sporty", "swimwear", "outdoor/weatherproof"]

app = Flask(__name__)
app.secret_key = SECRET_KEY

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    url TEXT DEFAULT '',
    location TEXT DEFAULT '',
    price_eur REAL,
    travel_minutes INTEGER,
    duration_hours REAL,
    dress_code TEXT DEFAULT 'casual',
    dinner_included INTEGER DEFAULT 0,        -- 0 no, 1 dinner is part of it, 2 eat beforehand
    limited_until TEXT,                       -- ISO date; NULL = evergreen
    available_dates TEXT DEFAULT '',          -- planner notes: candidate days / opening info
    category TEXT DEFAULT 'going-out',        -- at-home | going-out | daytrip
    banned INTEGER DEFAULT 0,
    rating INTEGER,                           -- 1-5 after doing it
    times_done INTEGER DEFAULT 0,
    last_picked_at TEXT,
    rigged_next INTEGER DEFAULT 0,            -- planner-only: force the next spin onto this idea (one-shot)
    expiry_notified_at TEXT,                  -- ISO datetime of the expiry ping; NULL = not yet sent
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    picked_at TEXT DEFAULT (datetime('now')),
    pinned INTEGER DEFAULT 0,
    booked_date TEXT,                         -- ISO date once reservation is made
    revealed INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0
);
"""


def get_db():
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# Idempotent column adds for databases created before these columns existed.
MIGRATIONS = [
    ("ideas", "rigged_next", "INTEGER DEFAULT 0"),
    ("ideas", "expiry_notified_at", "TEXT"),
]


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def planner_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("planner"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# ---------- eligibility + wheel weighting ----------

def eligible_ideas(db):
    today = date.today().isoformat()
    return db.execute(
        """SELECT * FROM ideas
           WHERE banned = 0
             AND (limited_until IS NULL OR limited_until = '' OR limited_until >= ?)
             AND id NOT IN (SELECT idea_id FROM picks WHERE pinned = 1 AND done = 0)
           ORDER BY id""",
        (today,),
    ).fetchall()


def idea_weight(idea):
    """Limited-time ideas expiring soon get boosted; recently picked ones damped."""
    w = 1.0
    if idea["limited_until"]:
        try:
            days_left = (date.fromisoformat(idea["limited_until"]) - date.today()).days
            if days_left <= EXPIRY_WARNING_DAYS:
                w *= 3.0
            elif days_left <= 45:
                w *= 1.8
        except ValueError:
            pass
    if idea["last_picked_at"]:
        try:
            picked = datetime.fromisoformat(idea["last_picked_at"])
            days_ago = (datetime.now() - picked).days
            if days_ago < 30:
                w *= 0.3
            elif days_ago < 90:
                w *= 0.7
        except ValueError:
            pass
    return w


def maybe_auto_reveal(db, pick):
    """Reveal automatically when the booked date is close enough."""
    if pick and pick["booked_date"] and not pick["revealed"]:
        try:
            booked = datetime.fromisoformat(pick["booked_date"])
            if datetime.now() >= booked - timedelta(hours=AUTO_REVEAL_HOURS):
                db.execute("UPDATE picks SET revealed = 1 WHERE id = ?", (pick["id"],))
                db.commit()
                return True
        except ValueError:
            pass
    return bool(pick and pick["revealed"])


def redacted(idea):
    """The only fields the partner may see before the reveal."""
    dinner = {0: "no, eat beforehand", 1: "dinner is included", 2: "small bites, eat something first"}
    return {
        "dress_code": idea["dress_code"] or "casual",
        "duration_hours": idea["duration_hours"],
        "travel_minutes": idea["travel_minutes"],
        "dinner": dinner.get(idea["dinner_included"], "no, eat beforehand"),
        "category": idea["category"],
    }


# ---------- expiry ping (ntfy) ----------

def ideas_expiring_soon(db, days=None):
    """Un-banned, not-yet-notified limited-time ideas expiring within `days`."""
    if days is None:
        days = EXPIRY_WARNING_DAYS
    return db.execute(
        """SELECT * FROM ideas
           WHERE banned = 0
             AND limited_until IS NOT NULL AND limited_until != ''
             AND limited_until >= date('now')
             AND julianday(limited_until) - julianday('now') <= ?
             AND expiry_notified_at IS NULL""",
        (days,),
    ).fetchall()


def notify_expiring(idea, days_left):
    """Send one ntfy push for an idea about to expire. Returns True on success."""
    if not (NTFY_URL and NTFY_TOPIC):
        return False
    plural = "s" if days_left != 1 else ""
    body = (f"'{idea['title']}' expires {idea['limited_until']}"
            f" ({days_left} day{plural} left). Book it or lose it.")
    req = urllib.request.Request(
        f"{NTFY_URL}/{NTFY_TOPIC}",
        data=body.encode("utf-8"),
        headers={"Title": "Date idea expiring soon", "Tags": "hourglass"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def check_expiry_notifications(db):
    """On-request expiry check (no cron): ping once per idea per limited_until.

    Marks expiry_notified_at only after a successful send, so failures retry
    on the next request. update_idea() resets the mark when limited_until
    changes, re-arming the ping for the new date.
    """
    if not (NTFY_URL and NTFY_TOPIC):
        return
    for idea in ideas_expiring_soon(db):
        try:
            days_left = (date.fromisoformat(idea["limited_until"]) - date.today()).days
        except ValueError:
            continue
        if notify_expiring(idea, days_left):
            db.execute("UPDATE ideas SET expiry_notified_at = datetime('now') WHERE id = ?",
                       (idea["id"],))
            db.commit()


# ---------- public routes ----------

@app.get("/healthz")
def healthz():
    """Liveness/readiness probe. Confirms the SQLite volume is reachable and
    reports the running version so deploys can verify what is actually live."""
    try:
        get_db().execute("SELECT 1")
    except sqlite3.Error as exc:
        return jsonify({"status": "error", "version": APP_VERSION, "db": str(exc)}), 503
    return jsonify({"status": "ok", "version": APP_VERSION})


@app.route("/")
def index():
    db = get_db()
    check_expiry_notifications(db)
    ideas = eligible_ideas(db)
    pick = db.execute(
        "SELECT p.*, i.title, i.description, i.url, i.location, i.price_eur, i.travel_minutes,"
        " i.duration_hours, i.dress_code, i.dinner_included, i.category"
        " FROM picks p JOIN ideas i ON i.id = p.idea_id"
        " WHERE p.done = 0 ORDER BY p.picked_at DESC LIMIT 1"
    ).fetchone()
    revealed = maybe_auto_reveal(db, pick)
    return render_template(
        "index.html",
        segment_count=len(ideas),
        pick=pick,
        revealed=revealed,
        redacted=redacted(pick) if pick else None,
        is_planner=bool(session.get("planner")),
    )


@app.post("/spin")
def spin():
    db = get_db()
    ideas = eligible_ideas(db)
    if not ideas:
        return jsonify({"error": "No eligible date ideas. Add some in planner mode!"}), 400
    rigged = next((i for i in ideas if i["rigged_next"]), None)
    # A rig is one-shot: clear it unconditionally so it never survives past one
    # spin, even if the rigged idea became ineligible (banned/expired/pinned)
    # in the meantime. Committed by _record_pick() below.
    db.execute("UPDATE ideas SET rigged_next = 0 WHERE rigged_next != 0")
    if rigged is not None:
        # eligible_ideas() already filtered out banned/expired/pinned ideas,
        # so an ineligible rig falls back to the fair path automatically.
        chosen_index = ideas.index(rigged)
    else:
        weights = [idea_weight(i) for i in ideas]
        chosen_index = random.choices(range(len(ideas)), weights=weights, k=1)[0]
    idea = ideas[chosen_index]
    _record_pick(db, idea["id"])
    return jsonify({"segment": chosen_index, "count": len(ideas), "redacted": redacted(idea)})


def _record_pick(db, idea_id):
    """Replace any unpinned live spin with a new pick for idea_id."""
    db.execute("DELETE FROM picks WHERE pinned = 0 AND done = 0")
    db.execute("INSERT INTO picks (idea_id) VALUES (?)", (idea_id,))
    db.execute("UPDATE ideas SET last_picked_at = datetime('now') WHERE id = ?", (idea_id,))
    db.commit()


# ---------- planner auth ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("passcode") == PASSCODE:
            session["planner"] = True
            return redirect(request.args.get("next") or url_for("planner"))
        error = "Wrong passcode."
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.pop("planner", None)
    return redirect(url_for("index"))


# ---------- planner routes ----------

@app.get("/planner")
@planner_required
def planner():
    db = get_db()
    check_expiry_notifications(db)
    ideas = db.execute("SELECT * FROM ideas ORDER BY banned, limited_until IS NULL, limited_until, title").fetchall()
    picks = db.execute(
        "SELECT p.*, i.title FROM picks p JOIN ideas i ON i.id = p.idea_id"
        " WHERE p.done = 0 ORDER BY p.picked_at DESC"
    ).fetchall()
    today = date.today().isoformat()
    return render_template("planner.html", ideas=ideas, picks=picks, today=today,
                           dress_codes=DRESS_CODES, eligible=eligible_ideas(db))


def idea_from_form(form):
    def num(key, cast):
        v = form.get(key, "").strip()
        try:
            return cast(v) if v else None
        except ValueError:
            return None
    return {
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
        "url": form.get("url", "").strip(),
        "location": form.get("location", "").strip(),
        "price_eur": num("price_eur", float),
        "travel_minutes": num("travel_minutes", int),
        "duration_hours": num("duration_hours", float),
        "dress_code": form.get("dress_code", "casual"),
        "dinner_included": int(form.get("dinner_included", 0)),
        "limited_until": form.get("limited_until", "").strip() or None,
        "available_dates": form.get("available_dates", "").strip(),
        "category": form.get("category", "going-out"),
    }


@app.post("/planner/ideas")
@planner_required
def create_idea():
    d = idea_from_form(request.form)
    if not d["title"]:
        # quick-add by URL: use the URL as a provisional title
        d["title"] = d["url"] or "Untitled idea"
    db = get_db()
    db.execute(
        """INSERT INTO ideas (title, description, url, location, price_eur, travel_minutes,
           duration_hours, dress_code, dinner_included, limited_until, available_dates, category)
           VALUES (:title,:description,:url,:location,:price_eur,:travel_minutes,
                   :duration_hours,:dress_code,:dinner_included,:limited_until,:available_dates,:category)""",
        d,
    )
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/ideas/<int:idea_id>")
@planner_required
def update_idea(idea_id):
    d = idea_from_form(request.form)
    d["id"] = idea_id
    db = get_db()
    old = db.execute("SELECT limited_until FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    db.execute(
        """UPDATE ideas SET title=:title, description=:description, url=:url, location=:location,
           price_eur=:price_eur, travel_minutes=:travel_minutes, duration_hours=:duration_hours,
           dress_code=:dress_code, dinner_included=:dinner_included, limited_until=:limited_until,
           available_dates=:available_dates, category=:category WHERE id=:id""",
        d,
    )
    # A changed limited_until makes any earlier expiry ping stale: re-arm it.
    if old is not None and (old["limited_until"] or None) != d["limited_until"]:
        db.execute("UPDATE ideas SET expiry_notified_at = NULL WHERE id = ?", (idea_id,))
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/ideas/<int:idea_id>/ban")
@planner_required
def toggle_ban(idea_id):
    db = get_db()
    db.execute("UPDATE ideas SET banned = 1 - banned WHERE id = ?", (idea_id,))
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/ideas/<int:idea_id>/rig")
@planner_required
def rig_idea(idea_id):
    """Toggle the one-shot rig; at most one idea is rigged at a time."""
    db = get_db()
    row = db.execute("SELECT rigged_next FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if row is None:
        abort(404)
    db.execute("UPDATE ideas SET rigged_next = 0 WHERE rigged_next != 0")
    if not row["rigged_next"]:
        db.execute("UPDATE ideas SET rigged_next = 1 WHERE id = ?", (idea_id,))
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/ideas/<int:idea_id>/delete")
@planner_required
def delete_idea(idea_id):
    db = get_db()
    db.execute("DELETE FROM ideas WHERE id = ?", (idea_id,))
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/pick")
@planner_required
def manual_pick():
    idea_id = request.form.get("idea_id", "").strip()
    if not idea_id.isdigit():
        return redirect(url_for("planner"))
    db = get_db()
    idea = db.execute("SELECT id FROM ideas WHERE id = ?", (int(idea_id),)).fetchone()
    if idea:
        _record_pick(db, idea["id"])
    return redirect(url_for("planner"))


@app.post("/planner/picks/<int:pick_id>/pin")
@planner_required
def pin_pick(pick_id):
    db = get_db()
    db.execute("UPDATE picks SET pinned = 1 WHERE id = ?", (pick_id,))
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/picks/<int:pick_id>/book")
@planner_required
def book_pick(pick_id):
    booked = request.form.get("booked_date", "").strip()
    db = get_db()
    db.execute("UPDATE picks SET pinned = 1, booked_date = ? WHERE id = ?", (booked or None, pick_id))
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/picks/<int:pick_id>/reveal")
@planner_required
def reveal_pick(pick_id):
    db = get_db()
    db.execute("UPDATE picks SET revealed = 1 WHERE id = ?", (pick_id,))
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/picks/<int:pick_id>/done")
@planner_required
def complete_pick(pick_id):
    rating = request.form.get("rating", "").strip()
    db = get_db()
    pick = db.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone()
    if not pick:
        abort(404)
    db.execute("UPDATE picks SET done = 1, revealed = 1 WHERE id = ?", (pick_id,))
    db.execute("UPDATE ideas SET times_done = times_done + 1 WHERE id = ?", (pick["idea_id"],))
    if rating.isdigit():
        db.execute("UPDATE ideas SET rating = ? WHERE id = ?", (int(rating), pick["idea_id"]))
    db.commit()
    return redirect(url_for("planner"))


@app.post("/planner/picks/<int:pick_id>/discard")
@planner_required
def discard_pick(pick_id):
    db = get_db()
    db.execute("DELETE FROM picks WHERE id = ?", (pick_id,))
    db.commit()
    return redirect(url_for("planner"))


# ---------- import / export ----------

IDEA_FIELDS = ["title", "description", "url", "location", "price_eur", "travel_minutes",
               "duration_hours", "dress_code", "dinner_included", "limited_until",
               "available_dates", "category"]


@app.get("/planner/export")
@planner_required
def export_ideas():
    db = get_db()
    rows = db.execute("SELECT * FROM ideas").fetchall()
    data = [{k: r[k] for k in IDEA_FIELDS} for r in rows]
    return jsonify(data)


@app.post("/planner/import")
@planner_required
def import_ideas():
    file = request.files.get("file")
    if not file:
        return redirect(url_for("planner"))
    try:
        data = json.load(file)
    except json.JSONDecodeError:
        return "Invalid JSON", 400
    db = get_db()
    existing = {r["title"] for r in db.execute("SELECT title FROM ideas").fetchall()}
    added = 0
    for item in data:
        if not isinstance(item, dict) or not item.get("title") or item["title"] in existing:
            continue
        row = {k: item.get(k) for k in IDEA_FIELDS}
        row["dress_code"] = row["dress_code"] or "casual"
        row["dinner_included"] = int(row["dinner_included"] or 0)
        row["category"] = row["category"] or "going-out"
        db.execute(
            """INSERT INTO ideas (title, description, url, location, price_eur, travel_minutes,
               duration_hours, dress_code, dinner_included, limited_until, available_dates, category)
               VALUES (:title,:description,:url,:location,:price_eur,:travel_minutes,
                       :duration_hours,:dress_code,:dinner_included,:limited_until,:available_dates,:category)""",
            row,
        )
        added += 1
    db.commit()
    return redirect(url_for("planner"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("FLASK_DEBUG") == "1")
