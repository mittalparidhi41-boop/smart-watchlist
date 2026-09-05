# Smart Market Watchlist

A submission for **Code, by Groww** — a watchlist that doesn't just show
prices, but tells you what actually deserves your attention.

## Setup

1. `python -m venv venv`
2. Activate it:
   - Windows: `venv\Scripts\activate`
   - Mac/Linux: `source venv/bin/activate`
3. `pip install -r requirements.txt`
4. `python app.py`
5. Open `http://127.0.0.1:5000` — register an account, then start adding
   stocks.

**Symbol format**: NSE stocks need a `.NS` suffix (e.g. `TCS.NS`,
`INFY.NS`, `RELIANCE.NS`). US stocks just use the ticker (e.g. `AAPL`).
This follows Yahoo Finance's convention, which is the data source used.

## What it does

- Create and manage a personal watchlist (multi-user, with accounts)
- See live prices, refreshed automatically every 2 minutes or on demand
- On every visit, see exactly what changed **since you personally last
  checked** — not since some arbitrary refresh timer
- Stocks that moved unusually are surfaced first and flagged, instead of
  being buried in an alphabetical list

## The core idea: what counts as "meaningful"

Most watchlists either show every price tick (noisy) or use one flat
threshold for every stock (blunt — 2% is huge for a sleepy stock and
nothing for a volatile one).

This app calculates, per stock, its own recent average price movement,
and flags a change as meaningful only when it's **at least 1.5x that
stock's own normal behavior** (with a small floor so very quiet stocks
don't flag on noise). A stock's "meaningful" threshold is different from
every other stock's, and adjusts automatically as its behavior changes.

## Other key design decisions

**"Change since last check" is personal, not global.** Each user has
their own record of the price they last saw for each stock. Reloading
the page twice in a row shows 0% (correctly — nothing changed between
those two moments). Coming back after a real gap shows the real change
since *you* looked.

**Price data is fetched once per symbol, globally — not once per user.**
If 100 users all watch RELIANCE.NS, the app still only fetches its price
once per refresh cycle. A user's watchlist is just a membership link to
a globally-tracked symbol. This is the answer to "how does this scale
to more users": the expensive part (external API calls) scales with the
number of distinct stocks tracked, not the number of users.

**Stale and unreliable data is handled explicitly, not ignored.** If a
price fetch fails, the last known price is kept and shown, marked as
stale. A symbol that fails 3+ times in a row is flagged as likely
invalid/mistyped, distinct from a temporary glitch.

**SQLite over Postgres, deliberately.** Zero setup, and enough for this
scale. At real production scale with many concurrent writers, I'd
migrate to Postgres — the schema and queries would carry over with
minimal change.

## Try it yourself

- Add a few stocks, click Refresh Prices, reload the page — see "you
  last checked at ₹X" and a real % change appear
- Open an incognito window, register a second account, add different
  stocks — confirm the two accounts have separate watchlists
- Add an invalid symbol (e.g. `FAKESTOCK123`) — after 3 failed refresh
  attempts, it's flagged as likely invalid instead of failing silently
  forever
- Remove your whole watchlist — see the empty state instead of a blank
  page

## Known simplifications (honest tradeoffs, not oversights)

- Session secret key is hardcoded for demo purposes — a real deployment
  would use an environment variable
- No password reset flow
- No rate limiting on login attempts
- Background refresh runs in a single thread on one process — fine for
  this scale, but a real deployment with multiple server instances would
  need a proper job scheduler (e.g. Celery + a message queue) instead

## 100-Word Pitch

Most watchlists flag changes using one flat threshold for every stock. I
built one that learns each stock's own normal behavior, flagging a move
as meaningful only when it's unusually large for that specific stock,
not just large in absolute terms. Each user's "change since last check"
is measured against the price they personally last saw, not a generic
refresh timer. Price data is fetched once per symbol globally, not per
user, so the app scales with distinct stocks tracked, not user count.
SQLite was a deliberate choice for this scale, with a clear migration
path to Postgres in production.