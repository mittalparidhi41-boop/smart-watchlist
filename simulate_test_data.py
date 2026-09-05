"""
simulate_test_data.py — FOR TESTING ONLY, not part of the actual submission.

Since the real market is closed (weekend), real prices won't move, so
there's no way to see the "meaningful change" logic fire naturally. This
script fakes a realistic price history for one symbol you're already
tracking, so you can verify the logic actually works before submission.

Usage:
    python simulate_test_data.py TCS.NS

What it does:
1. Inserts ~8 snapshots with small, "normal" price wiggles (~0.1% each) —
   this becomes the stock's "usual" volatility baseline.
2. Inserts one final snapshot with a BIG jump (3%) — this should get
   flagged as meaningful.
3. Sets every user's "last seen" price for this symbol back to the price
   BEFORE the big jump, so the app shows a real "change since you last
   checked" when you reload the page.

After running this, restart your Flask app if it's not already running,
log in, and reload the page — you should see the ⚠ MEANINGFUL badge on
this stock.

Delete this file before submitting — it's a testing tool, not part of
the product.
"""

import sqlite3
import sys
import random
from datetime import datetime, timezone, timedelta

DB_FILE = "watchlist.db"

if len(sys.argv) != 2:
    print("Usage: python simulate_test_data.py SYMBOL")
    print("Example: python simulate_test_data.py TCS.NS")
    sys.exit(1)

symbol = sys.argv[1].strip().upper()

conn = sqlite3.connect(DB_FILE)
conn.row_factory = sqlite3.Row

tracked = conn.execute("SELECT * FROM tracked_symbols WHERE symbol = ?", (symbol,)).fetchone()
if not tracked:
    print(f"'{symbol}' isn't being tracked by anyone yet. Add it in the app first, then rerun this.")
    sys.exit(1)

# Start from a realistic base price and wiggle it slightly for each
# "normal" snapshot, going backward in time from now.
base_price = 2300.0
now = datetime.now(timezone.utc)

normal_snapshots = []
price = base_price
for i in range(8):
    price = price * (1 + random.uniform(-0.001, 0.001))  # ~0.1% normal wiggle
    timestamp = (now - timedelta(minutes=(8 - i) * 3)).isoformat()
    normal_snapshots.append((round(price, 2), timestamp))

price_before_jump = normal_snapshots[-1][0]

# The "meaningful" jump: 3% higher than the last normal price.
jump_price = round(price_before_jump * 1.03, 2)
jump_timestamp = now.isoformat()

for p, ts in normal_snapshots:
    conn.execute(
        "INSERT INTO snapshots (symbol, price, fetched_at) VALUES (?, ?, ?)",
        (symbol, p, ts)
    )
conn.execute(
    "INSERT INTO snapshots (symbol, price, fetched_at) VALUES (?, ?, ?)",
    (symbol, jump_price, jump_timestamp)
)

conn.execute(
    "UPDATE tracked_symbols SET consecutive_failures = 0, last_refreshed_at = ? WHERE symbol = ?",
    (jump_timestamp, symbol)
)

# Set every user's "last seen" price for this symbol to the price BEFORE
# the jump, so next page load shows the full jump as "change since you
# last checked".
users_watching = conn.execute(
    "SELECT DISTINCT user_id FROM watchlist WHERE symbol = ?", (symbol,)
).fetchall()

for row in users_watching:
    conn.execute("""
        INSERT INTO last_seen (user_id, symbol, price_at_last_seen, seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, symbol) DO UPDATE SET
            price_at_last_seen = excluded.price_at_last_seen,
            seen_at = excluded.seen_at
    """, (row["user_id"], symbol, price_before_jump, (now - timedelta(minutes=5)).isoformat()))

conn.commit()
conn.close()

print(f"Done. {symbol} now has fake history:")
print(f"  8 'normal' snapshots wiggling around {base_price}")
print(f"  1 final snapshot at {jump_price} (a 3% jump from {price_before_jump})")
print(f"  Every user's 'last seen' price reset to {price_before_jump}")
print()
print("Now reload the watchlist page in your browser — this stock should")
print("show a real % change and the MEANINGFUL badge.")