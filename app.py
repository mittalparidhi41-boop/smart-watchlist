"""
Smart Market Watchlist — v3, with multi-user accounts.

Architecture note (explain this in your pitch):
Price data is fetched and stored ONCE PER SYMBOL GLOBALLY, not once per
user. If 100 users all watch RELIANCE.NS, we still only fetch and store
its price once per refresh cycle. A user's "watchlist" is just a
membership link between their account and a symbol. This is the answer
to "how does this scale to more users" — the expensive part (external
API calls) doesn't grow with user count, only with the number of
DISTINCT symbols being tracked.

Run it with:  python app.py
Then open:    http://127.0.0.1:5000
"""

from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import requests
import threading
import time
from datetime import datetime, timezone

app = Flask(__name__)
app.secret_key = "hackathon-dev-secret-change-this-for-real-use"  # fine for a hackathon demo
DB_FILE = "watchlist.db"

SENSITIVITY_MULTIPLIER = 1.5
MIN_CHANGE_FLOOR = 0.5
VOLATILITY_WINDOW = 10
AUTO_REFRESH_SECONDS = 120


def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracked_symbols (
            symbol TEXT PRIMARY KEY,
            consecutive_failures INTEGER DEFAULT 0,
            last_refreshed_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER,
            symbol TEXT,
            added_at TEXT,
            PRIMARY KEY (user_id, symbol)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            price REAL,
            fetched_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS last_seen (
            user_id INTEGER,
            symbol TEXT,
            price_at_last_seen REAL,
            seen_at TEXT,
            PRIMARY KEY (user_id, symbol)
        )
    """)
    conn.commit()
    conn.close()


# ---------- auth helpers ----------

def current_user_id():
    return session.get("user_id")


def login_required(view):
    def wrapped(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            error = "Username and password are both required."
        else:
            conn = get_db()
            existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing:
                error = "That username is already taken."
            else:
                conn.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), datetime.now(timezone.utc).isoformat())
                )
                conn.commit()
                user = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                session["user_id"] = user["id"]
                session["username"] = username
                conn.close()
                return redirect("/")
            conn.close()

    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = username
            return redirect("/")
        error = "Incorrect username or password."

    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- price fetching / analysis ----------

def fetch_price(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        data = resp.json()
        result = data["chart"]["result"][0]
        price = result["meta"]["regularMarketPrice"]
        return float(price)
    except Exception:
        return None


def get_recent_prices(conn, symbol, limit):
    rows = conn.execute(
        "SELECT price FROM snapshots WHERE symbol = ? ORDER BY fetched_at DESC LIMIT ?",
        (symbol, limit)
    ).fetchall()
    return [r["price"] for r in rows]


def compute_average_move_pct(prices):
    if len(prices) < 3:
        return None
    moves = []
    for i in range(len(prices) - 1):
        newer, older = prices[i], prices[i + 1]
        if older != 0:
            moves.append(abs((newer - older) / older * 100))
    if not moves:
        return None
    return sum(moves) / len(moves)


def analyze_symbol(conn, user_id, symbol, consecutive_failures, last_refreshed_at):
    """
    Returns the current state of one symbol FOR THIS SPECIFIC USER:
    - current price
    - the price this user last saw (when they last opened the page)
    - the % change between those two, and whether it's "meaningful"

    This is the literal answer to "return later and see what has changed":
    the comparison is against when THIS user last checked, not against
    the last background refresh cycle.
    """
    prices = get_recent_prices(conn, symbol, VOLATILITY_WINDOW)

    if not prices:
        if consecutive_failures >= 3:
            status = f"unable to fetch after {consecutive_failures} tries — check the symbol is correct"
        else:
            status = "no data yet — click Refresh"
        return {"symbol": symbol, "price": None, "last_seen_price": None, "change_pct": None,
                "meaningful": False, "stale": True, "reason": None,
                "status": status, "last_refreshed_at": last_refreshed_at}

    current_price = prices[0]

    last_seen_row = conn.execute(
        "SELECT price_at_last_seen FROM last_seen WHERE user_id = ? AND symbol = ?",
        (user_id, symbol)
    ).fetchone()
    last_seen_price = last_seen_row["price_at_last_seen"] if last_seen_row else None

    if last_seen_price is None or last_seen_price == 0:
        # First time this user has ever viewed this symbol — nothing to
        # compare against yet.
        return {"symbol": symbol, "price": current_price, "last_seen_price": None, "change_pct": None,
                "meaningful": False, "stale": False, "reason": "first time viewing this stock",
                "status": None, "last_refreshed_at": last_refreshed_at}

    change_pct = round((current_price - last_seen_price) / last_seen_price * 100, 2)

    # "Meaningful" is still calibrated against the stock's own normal
    # volatility (global, shared across users) — just the change itself
    # is now measured against THIS user's personal last-seen price.
    avg_move = compute_average_move_pct(prices)
    if avg_move is None or avg_move == 0:
        threshold = MIN_CHANGE_FLOOR
        reason = f"using default {MIN_CHANGE_FLOOR}% floor (not enough history yet)"
    else:
        threshold = max(avg_move * SENSITIVITY_MULTIPLIER, MIN_CHANGE_FLOOR)
        reason = f"this stock's normal move is ~{round(avg_move, 2)}%, so {round(threshold, 2)}% is the bar"

    meaningful = abs(change_pct) >= threshold
    status = f"currently failing to update ({consecutive_failures} tries)" if consecutive_failures >= 3 else None

    return {"symbol": symbol, "price": current_price, "last_seen_price": last_seen_price,
            "change_pct": change_pct, "meaningful": meaningful,
            "stale": consecutive_failures >= 3, "reason": reason,
            "status": status, "last_refreshed_at": last_refreshed_at}


# ---------- main app routes ----------

@app.route("/")
@login_required
def home():
    conn = get_db()
    user_id = current_user_id()

    rows = conn.execute("""
        SELECT w.symbol, t.consecutive_failures, t.last_refreshed_at
        FROM watchlist w
        JOIN tracked_symbols t ON w.symbol = t.symbol
        WHERE w.user_id = ?
        ORDER BY w.added_at
    """, (user_id,)).fetchall()

    items = [
        analyze_symbol(conn, user_id, row["symbol"], row["consecutive_failures"] or 0, row["last_refreshed_at"])
        for row in rows
    ]

    # Now that we've computed "what changed since you last checked" using
    # the OLD last_seen values, update last_seen to today's price. Next
    # time this user opens the page, the comparison starts fresh from here.
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        if item["price"] is not None:
            conn.execute("""
                INSERT INTO last_seen (user_id, symbol, price_at_last_seen, seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, symbol) DO UPDATE SET
                    price_at_last_seen = excluded.price_at_last_seen,
                    seen_at = excluded.seen_at
            """, (user_id, item["symbol"], item["price"], now))
    conn.commit()
    conn.close()

    def sort_key(item):
        is_meaningful = item["meaningful"]
        magnitude = abs(item["change_pct"]) if item["change_pct"] is not None else -1
        return (not is_meaningful, -magnitude)

    items.sort(key=sort_key)

    last_updated_values = [i["last_refreshed_at"] for i in items if i["last_refreshed_at"]]
    last_updated = max(last_updated_values) if last_updated_values else None

    conn2 = get_db()
    user_row = conn2.execute("SELECT username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    conn2.close()

    if user_row is None:
        # Session points to a user that no longer exists (e.g. the database
        # was reset). Clear the stale session and send them to log in again
        # instead of crashing.
        session.clear()
        return redirect(url_for("login"))

    meaningful_count = sum(1 for i in items if i["meaningful"])
    up_count = sum(1 for i in items if i["change_pct"] is not None and i["change_pct"] > 0)
    down_count = sum(1 for i in items if i["change_pct"] is not None and i["change_pct"] < 0)

    return render_template("index.html", items=items, last_updated=last_updated,
                            auto_refresh_minutes=AUTO_REFRESH_SECONDS // 60,
                            username=user_row["username"], member_since=user_row["created_at"],
                            total_count=len(items), meaningful_count=meaningful_count,
                            up_count=up_count, down_count=down_count)


@app.route("/add", methods=["POST"])
@login_required
def add_symbol():
    symbol = request.form.get("symbol", "").strip().upper()
    is_valid = (
        symbol
        and 1 <= len(symbol) <= 20
        and all(c.isalnum() or c in ".-" for c in symbol)
    )

    if is_valid:
        conn = get_db()
        # Register the symbol globally (if not already tracked by anyone),
        # then link it to this user's personal watchlist.
        conn.execute("INSERT OR IGNORE INTO tracked_symbols (symbol) VALUES (?)", (symbol,))
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, symbol, added_at) VALUES (?, ?, ?)",
            (current_user_id(), symbol, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()

    return redirect("/")


@app.route("/remove", methods=["POST"])
@login_required
def remove_symbol():
    symbol = request.form.get("symbol", "").strip().upper()
    conn = get_db()
    # Only removes this user's membership — the global symbol data stays,
    # since other users might still be tracking the same stock.
    conn.execute("DELETE FROM watchlist WHERE user_id = ? AND symbol = ?", (current_user_id(), symbol))
    conn.commit()
    conn.close()
    return redirect("/")


@app.route("/refresh", methods=["POST"])
@login_required
def refresh_prices():
    refresh_all_symbols()
    return redirect("/")


def refresh_all_symbols():
    """
    Fetches a fresh price for every DISTINCT symbol currently tracked by
    ANY user — once each, regardless of how many users are watching it.
    """
    conn = get_db()
    try:
        symbols = conn.execute("SELECT symbol FROM tracked_symbols").fetchall()
        now = datetime.now(timezone.utc).isoformat()

        for row in symbols:
            symbol = row["symbol"]
            price = fetch_price(symbol)

            if price is not None:
                conn.execute(
                    "INSERT INTO snapshots (symbol, price, fetched_at) VALUES (?, ?, ?)",
                    (symbol, price, now)
                )
                conn.execute(
                    "UPDATE tracked_symbols SET consecutive_failures = 0, last_refreshed_at = ? WHERE symbol = ?",
                    (now, symbol)
                )
            else:
                conn.execute(
                    "UPDATE tracked_symbols SET consecutive_failures = consecutive_failures + 1, last_refreshed_at = ? WHERE symbol = ?",
                    (now, symbol)
                )

        conn.commit()
    finally:
        conn.close()


def start_background_refresher():
    def loop():
        while True:
            time.sleep(AUTO_REFRESH_SECONDS)
            try:
                refresh_all_symbols()
            except Exception as e:
                print(f"[auto-refresh] failed: {e}")

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


if __name__ == "__main__":
    init_db()
    start_background_refresher()
    app.run(debug=True, use_reloader=False)