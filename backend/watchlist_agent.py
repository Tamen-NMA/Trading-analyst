"""
watchlist_agent.py
Monitors the watchlist every 5 minutes during market hours.
Fires a Slack alert when price reaches the entry zone AND candles confirm buyers are in control.

Usage:
    python backend/watchlist_agent.py
"""

from __future__ import annotations


import os
import time
import json
import sqlite3
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from candle_intel import analyze_candle_intel, is_entry_signal
from slack_alerts import send_entry_alert, send_stop_breach_alert

try:
    from push_notifications import send_push
except Exception:
    def send_push(*a, **k):  # graceful no-op if module unavailable
        return 0

BACKEND_URL   = os.environ.get("BACKEND_URL", "http://localhost:8000")
DATABASE_URL  = os.environ.get("DATABASE_URL")
DB_PATH       = Path(__file__).parent / "history.db"
CHECK_INTERVAL_SECS = 300   # 5 minutes
ENTRY_ZONE_PCT      = 0.02  # alert when price is within 2% of entry
ALERT_COOLDOWN_HRS  = 4     # don't re-alert same ticker within 4 hours

ET = ZoneInfo("America/New_York")

http = httpx.Client(timeout=20)


# ── Database helpers ──────────────────────────────────────────────────────────

def _pg():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)

def _sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_watchlist_table():
    """Create watchlist table if it doesn't exist."""
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS watchlist (
                        id           SERIAL PRIMARY KEY,
                        ticker       TEXT NOT NULL,
                        entry_price  REAL,
                        stop_loss    REAL,
                        target       REAL,
                        rr_ratio     TEXT,
                        verdict      TEXT,
                        analysis_id  INTEGER,
                        active       BOOLEAN DEFAULT true,
                        alerted_at   TIMESTAMPTZ,
                        created_at   TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
            conn.commit()
    else:
        with _sqlite() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker       TEXT NOT NULL,
                    entry_price  REAL,
                    stop_loss    REAL,
                    target       REAL,
                    rr_ratio     TEXT,
                    verdict      TEXT,
                    analysis_id  INTEGER,
                    active       INTEGER DEFAULT 1,
                    alerted_at   TEXT,
                    created_at   TEXT DEFAULT (datetime('now'))
                )
            """)


def get_active_watchlist() -> list[dict]:
    if DATABASE_URL:
        import psycopg2.extras
        with _pg() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM watchlist WHERE active = true ORDER BY created_at DESC")
                return [dict(r) for r in cur.fetchall()]
    else:
        with _sqlite() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE active = 1 ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def update_alerted_at(watchlist_id: int):
    now = datetime.now(timezone.utc).isoformat()
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE watchlist SET alerted_at = %s WHERE id = %s", (now, watchlist_id))
            conn.commit()
    else:
        with _sqlite() as conn:
            conn.execute("UPDATE watchlist SET alerted_at = ? WHERE id = ?", (now, watchlist_id))


def log_alert(ticker: str, price: float, entry_price: float, stop_loss: float,
               target: float, rr_ratio: str, pattern: str, signal: str):
    """Persist every fired alert to alerts_log so the voice assistant can read it."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        if DATABASE_URL:
            with _pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO alerts_log
                           (ticker, price, entry_price, stop_loss, target, rr_ratio, pattern, signal, fired_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (ticker, price, entry_price, stop_loss, target, rr_ratio, pattern, signal, now)
                    )
                conn.commit()
        else:
            with _sqlite() as conn:
                conn.execute(
                    """INSERT INTO alerts_log
                       (ticker, price, entry_price, stop_loss, target, rr_ratio, pattern, signal, fired_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (ticker, price, entry_price, stop_loss, target, rr_ratio, pattern, signal, now)
                )
        print(f"  [alerts_log] Saved alert for {ticker}")
    except Exception as e:
        print(f"  [alerts_log] Could not save: {e}")


# ── Market hours ──────────────────────────────────────────────────────────────

def market_is_open() -> bool:
    """Returns True if US market is currently open (9:30am–4:00pm ET, Mon–Fri)."""
    now = datetime.now(ET)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now <= market_close


def cooldown_passed(alerted_at_str: str | None) -> bool:
    """Returns True if enough time has passed since the last alert."""
    if not alerted_at_str:
        return True
    try:
        alerted_at = datetime.fromisoformat(str(alerted_at_str))
        if alerted_at.tzinfo is None:
            alerted_at = alerted_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - alerted_at > timedelta(hours=ALERT_COOLDOWN_HRS)
    except Exception:
        return True


# ── Price check ───────────────────────────────────────────────────────────────

def get_price_data(ticker: str) -> dict | None:
    try:
        r = http.get(f"{BACKEND_URL}/price/{ticker.upper()}")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [price] Failed to fetch {ticker}: {e}")
        return None


def price_in_entry_zone(current_price: float, entry_price: float) -> bool:
    """True if current price is within ENTRY_ZONE_PCT above the entry price."""
    if not entry_price:
        return False
    lower = entry_price
    upper = entry_price * (1 + ENTRY_ZONE_PCT)
    return lower <= current_price <= upper


# ── Main check loop ───────────────────────────────────────────────────────────

def check_watchlist():
    items = get_active_watchlist()
    if not items:
        print("  [watchlist] No active items.")
        return

    print(f"  [watchlist] Checking {len(items)} ticker(s)…")

    for item in items:
        ticker      = item["ticker"]
        entry_price = item.get("entry_price")
        stop_loss   = item.get("stop_loss") or 0.0
        target      = item.get("target") or 0.0
        rr_ratio    = item.get("rr_ratio") or "N/A"
        analysis_id = item.get("analysis_id")
        alerted_at  = item.get("alerted_at")

        if not entry_price:
            print(f"  [{ticker}] No entry price set — skipping")
            continue

        if not cooldown_passed(alerted_at):
            print(f"  [{ticker}] Cooldown active — skipping")
            continue

        price_data = get_price_data(ticker)
        if not price_data:
            continue

        current_price = price_data.get("current_price", 0)
        candles       = price_data.get("last_30_candles", [])

        # Stop-loss breach check — fires regardless of entry zone
        if stop_loss and current_price < stop_loss and cooldown_passed(alerted_at):
            print(f"  [{ticker}] 🔴 STOP BREACHED — ${current_price} < ${stop_loss}")
            if send_stop_breach_alert(ticker, current_price, stop_loss, entry_price):
                update_alerted_at(item["id"])
                log_alert(ticker, current_price, entry_price, stop_loss, target,
                          rr_ratio, "stop_breach", "bearish")
                send_push(
                    title=f"🔴 {ticker} — Stop Loss Breached",
                    body=f"${current_price:.2f} is below your ${stop_loss:.2f} stop. McAllen: exit, don't hope.",
                    data={"ticker": ticker, "kind": "stop_breach"},
                )
            continue

        if not price_in_entry_zone(current_price, entry_price):
            print(f"  [{ticker}] ${current_price} not in entry zone (${entry_price:.2f} ± {ENTRY_ZONE_PCT*100:.0f}%)")
            continue

        # Check last 2 candles for confirmation
        intel = analyze_candle_intel(candles, idx=-1)
        print(f"  [{ticker}] ${current_price} in entry zone | {intel['pattern']} | {intel['signal']}")

        if not is_entry_signal(intel):
            print(f"  [{ticker}] No bullish confirmation — holding")
            continue

        # Get analysis date for the Slack message
        analysis_date = item.get("created_at", "")
        if analysis_date:
            try:
                dt = datetime.fromisoformat(str(analysis_date))
                analysis_date = dt.strftime("%b %d, %Y")
            except Exception:
                pass

        print(f"  [{ticker}] 🚨 ALERT TRIGGERED — sending Slack message")
        sent = send_entry_alert(
            ticker        = ticker,
            current_price = current_price,
            entry_price   = entry_price,
            stop_loss     = stop_loss,
            target        = target,
            rr_ratio      = rr_ratio,
            pattern       = intel["pattern"],
            signal_message= intel["message"],
            analysis_date = analysis_date,
            backend_url   = BACKEND_URL,
        )

        if sent:
            update_alerted_at(item["id"])
            log_alert(
                ticker      = ticker,
                price       = current_price,
                entry_price = entry_price,
                stop_loss   = stop_loss,
                target      = target,
                rr_ratio    = rr_ratio,
                pattern     = intel["pattern"],
                signal      = intel["signal"],
            )
            send_push(
                title=f"🟢 {ticker} — Entry Alert",
                body=f"${current_price:.2f} in entry zone · {intel['pattern'].replace('_',' ')}. Stop ${stop_loss:.2f}, target ${target:.2f}.",
                data={"ticker": ticker, "kind": "entry"},
            )


def main():
    print("\n" + "=" * 60)
    print("  McAllen Watchlist Agent")
    print(f"  Checking every {CHECK_INTERVAL_SECS // 60} minutes during market hours")
    print("  Ctrl+C to stop")
    print("=" * 60 + "\n")

    init_watchlist_table()

    # Send test message to confirm Slack is wired up
    from slack_alerts import send_test_alert
    send_test_alert()

    while True:
        now_str = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
        if market_is_open():
            print(f"\n[{now_str}] Market open — running check…")
            try:
                check_watchlist()
            except Exception as e:
                print(f"[agent] Error during check: {e}")
        else:
            print(f"[{now_str}] Market closed — sleeping…")

        time.sleep(CHECK_INTERVAL_SECS)


if __name__ == "__main__":
    main()
