"""
market_scanner.py
Scans the whole US stock market for low-float momentum/breakout candidates
(price $1-$10, float < threshold, RVOL > threshold, volume > threshold,
gap% >= threshold) using Polygon.io's snapshot + grouped-daily endpoints.

Fires two kinds of Slack alerts:
  - "breakout"  — ticker meets ALL configured thresholds right now
  - "building"  — ticker doesn't meet thresholds yet, but RVOL/gap are
                   accelerating fast vs the previous scan cycle

Usage:
    python backend/market_scanner.py
"""

from __future__ import annotations

import os
import time
import json
import sqlite3
import httpx
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import yfinance as yf

from slack_alerts import send_scanner_breakout_alert, send_scanner_warning_alert, send_test_alert

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
POLYGON_BASE    = "https://api.polygon.io"

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH      = Path(__file__).parent / "history.db"

SCANNER_PRICE_MIN   = float(os.environ.get("SCANNER_PRICE_MIN", "1"))
SCANNER_PRICE_MAX   = float(os.environ.get("SCANNER_PRICE_MAX", "10"))
SCANNER_FLOAT_MAX   = int(os.environ.get("SCANNER_FLOAT_MAX", "10000000"))
SCANNER_RVOL_MIN    = float(os.environ.get("SCANNER_RVOL_MIN", "5"))
SCANNER_VOLUME_MIN  = int(os.environ.get("SCANNER_VOLUME_MIN", "500000"))
SCANNER_GAP_MIN_PCT = float(os.environ.get("SCANNER_GAP_MIN_PCT", "10"))

SCANNER_CHECK_INTERVAL_SECS = int(os.environ.get("SCANNER_CHECK_INTERVAL_SECS", "300"))
SCANNER_COOLDOWN_MIN         = int(os.environ.get("SCANNER_COOLDOWN_MIN", "60"))

VOLUME_BASELINE_PATH = Path(__file__).parent / "volume_baseline.json"
FLOAT_CACHE_PATH     = Path(__file__).parent / "float_cache.json"
BASELINE_DAYS        = 20
FLOAT_CACHE_MAX_AGE_DAYS = 7

ET = ZoneInfo("America/New_York")

http = httpx.Client(timeout=30)


# ── Database helpers ──────────────────────────────────────────────────────────

def _pg():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)

def _sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Market hours ──────────────────────────────────────────────────────────────

def scanner_hours_open() -> bool:
    """Returns True Mon-Fri 4:00am-8:00pm ET (extended hours, to catch pre-market gaps)."""
    now = datetime.now(ET)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    end   = now.replace(hour=20, minute=0, second=0, microsecond=0)
    return start <= now <= end


# ── Polygon API ──────────────────────────────────────────────────────────────

def polygon_get(path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["apiKey"] = POLYGON_API_KEY
    r = http.get(f"{POLYGON_BASE}{path}", params=params)
    r.raise_for_status()
    return r.json()


def fetch_snapshot() -> list[dict]:
    """One call: price/volume/gap snapshot for every US ticker."""
    data = polygon_get("/v2/snapshot/locale/us/markets/stocks/tickers")
    return data.get("tickers", [])


def fetch_grouped_daily(day: date) -> list[dict]:
    """All US tickers' OHLCV for a single past trading day."""
    date_str = day.strftime("%Y-%m-%d")
    data = polygon_get(f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}", {"adjusted": "true"})
    return data.get("results", []) or []


# ── Volume baseline (avg daily volume per ticker, refreshed once/day) ─────────

def refresh_volume_baseline() -> dict:
    """Build a {ticker: avg_volume_over_BASELINE_DAYS} map from grouped-daily bars."""
    print(f"  [baseline] Building {BASELINE_DAYS}-day volume baseline…")
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}

    cursor = datetime.now(ET).date() - timedelta(days=1)
    trading_days_seen = 0
    calendar_days_checked = 0

    while trading_days_seen < BASELINE_DAYS and calendar_days_checked < BASELINE_DAYS * 2:
        calendar_days_checked += 1
        if cursor.weekday() < 5:  # skip weekends
            try:
                results = fetch_grouped_daily(cursor)
                if results:
                    for row in results:
                        ticker = row.get("T")
                        vol = row.get("v")
                        if ticker and vol:
                            sums[ticker] = sums.get(ticker, 0) + vol
                            counts[ticker] = counts.get(ticker, 0) + 1
                    trading_days_seen += 1
            except Exception as e:
                print(f"  [baseline] Failed grouped-daily for {cursor}: {e}")
        cursor -= timedelta(days=1)

    baseline = {t: sums[t] / counts[t] for t in sums if counts[t] > 0}
    print(f"  [baseline] Built baseline for {len(baseline)} tickers from {trading_days_seen} trading day(s).")

    VOLUME_BASELINE_PATH.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data": baseline,
    }))
    return baseline


def load_volume_baseline() -> tuple[dict, datetime]:
    """Returns (baseline, updated_at). Refreshes if missing or stale (>24h)."""
    if VOLUME_BASELINE_PATH.exists():
        try:
            cached = json.loads(VOLUME_BASELINE_PATH.read_text())
            updated_at = datetime.fromisoformat(cached["updated_at"])
            if datetime.now(timezone.utc) - updated_at < timedelta(hours=24):
                return cached["data"], updated_at
        except Exception:
            pass
    return refresh_volume_baseline(), datetime.now(timezone.utc)


# ── Float cache (lazy per-ticker lookup via yfinance) ──────────────────────────

def load_float_cache() -> dict:
    if FLOAT_CACHE_PATH.exists():
        try:
            return json.loads(FLOAT_CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def save_float_cache(cache: dict):
    try:
        FLOAT_CACHE_PATH.write_text(json.dumps(cache))
    except Exception as e:
        print(f"  [float] Could not save cache: {e}")


def get_float(ticker: str, cache: dict) -> int | None:
    entry = cache.get(ticker)
    if entry:
        try:
            updated_at = datetime.fromisoformat(entry["updated_at"])
            if datetime.now(timezone.utc) - updated_at < timedelta(days=FLOAT_CACHE_MAX_AGE_DAYS):
                return entry["float"]
        except Exception:
            pass

    float_shares = None
    try:
        info = yf.Ticker(ticker).info
        float_shares = info.get("floatShares")
    except Exception as e:
        print(f"  [float] Failed to fetch float for {ticker}: {e}")

    cache[ticker] = {"float": float_shares, "updated_at": datetime.now(timezone.utc).isoformat()}
    return float_shares


# ── Alert cooldown + logging ───────────────────────────────────────────────────

def cooldown_passed(ticker: str, alert_type: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=SCANNER_COOLDOWN_MIN)
    try:
        if DATABASE_URL:
            with _pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT fired_at FROM scanner_alerts WHERE ticker = %s AND alert_type = %s ORDER BY fired_at DESC LIMIT 1",
                        (ticker, alert_type),
                    )
                    row = cur.fetchone()
        else:
            with _sqlite() as conn:
                row = conn.execute(
                    "SELECT fired_at FROM scanner_alerts WHERE ticker = ? AND alert_type = ? ORDER BY fired_at DESC LIMIT 1",
                    (ticker, alert_type),
                ).fetchone()

        if not row:
            return True
        fired_at = datetime.fromisoformat(str(row[0]))
        if fired_at.tzinfo is None:
            fired_at = fired_at.replace(tzinfo=timezone.utc)
        return fired_at < cutoff
    except Exception:
        return True


def log_scanner_alert(ticker: str, price: float, gap_pct: float, rvol: float,
                       volume: int, float_shares: int | None, alert_type: str):
    now = datetime.now(timezone.utc).isoformat()
    try:
        if DATABASE_URL:
            with _pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO scanner_alerts
                           (ticker, price, gap_pct, rvol, volume, float_shares, alert_type, fired_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (ticker, price, gap_pct, rvol, volume, float_shares, alert_type, now)
                    )
                conn.commit()
        else:
            with _sqlite() as conn:
                conn.execute(
                    """INSERT INTO scanner_alerts
                       (ticker, price, gap_pct, rvol, volume, float_shares, alert_type, fired_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (ticker, price, gap_pct, rvol, volume, float_shares, alert_type, now)
                )
        print(f"  [scanner_alerts] Saved {alert_type} alert for {ticker}")
    except Exception as e:
        print(f"  [scanner_alerts] Could not save: {e}")


# ── Main scan ───────────────────────────────────────────────────────────────────

def scan_once(volume_baseline: dict, float_cache: dict, prev_scan: dict):
    snapshot = fetch_snapshot()
    print(f"  [scan] {len(snapshot)} tickers in snapshot…")

    hits = 0
    for item in snapshot:
        ticker = item.get("ticker")
        if not ticker:
            continue

        day = item.get("day", {}) or {}
        price = day.get("c") or (item.get("min") or {}).get("c") or (item.get("prevDay") or {}).get("c")
        volume = day.get("v") or 0
        gap_pct = item.get("todaysChangePerc")

        if not price or gap_pct is None:
            continue

        # First-pass filter — cheap, no extra calls
        if not (SCANNER_PRICE_MIN <= price <= SCANNER_PRICE_MAX):
            continue
        if volume < SCANNER_VOLUME_MIN:
            continue
        if gap_pct < SCANNER_GAP_MIN_PCT * 0.5:
            continue

        baseline_vol = volume_baseline.get(ticker)
        if not baseline_vol:
            continue
        rvol = volume / baseline_vol
        if rvol < SCANNER_RVOL_MIN * 0.5:
            continue

        float_shares = get_float(ticker, float_cache)

        is_breakout = (
            gap_pct >= SCANNER_GAP_MIN_PCT
            and rvol >= SCANNER_RVOL_MIN
            and float_shares is not None
            and float_shares <= SCANNER_FLOAT_MAX
        )

        is_building = False
        if not is_breakout:
            meets_partial = rvol >= SCANNER_RVOL_MIN * 0.6 or gap_pct >= SCANNER_GAP_MIN_PCT * 0.6
            prev = prev_scan.get(ticker)
            if meets_partial and prev:
                rvol_jumped = rvol >= prev["rvol"] * 1.3
                gap_jumped = (gap_pct - prev["gap_pct"]) >= 2
                is_building = rvol_jumped or gap_jumped

        prev_scan[ticker] = {"rvol": rvol, "gap_pct": gap_pct}

        alert_type = "breakout" if is_breakout else ("building" if is_building else None)
        if not alert_type:
            continue

        if not cooldown_passed(ticker, alert_type):
            continue

        hits += 1
        if alert_type == "breakout":
            print(f"  [{ticker}] 🚀 BREAKOUT — price ${price:.2f}, gap {gap_pct:.1f}%, rvol {rvol:.1f}x, float {float_shares}")
            send_scanner_breakout_alert(ticker, price, gap_pct, rvol, volume, float_shares)
        else:
            print(f"  [{ticker}] ⚡ BUILDING — price ${price:.2f}, gap {gap_pct:.1f}%, rvol {rvol:.1f}x, float {float_shares}")
            send_scanner_warning_alert(ticker, price, gap_pct, rvol, volume, float_shares)

        log_scanner_alert(ticker, price, gap_pct, rvol, volume, float_shares, alert_type)

    if hits == 0:
        print("  [scan] No new alerts this cycle.")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  McAllen Market Scanner")
    print(f"  Checking every {SCANNER_CHECK_INTERVAL_SECS // 60} minutes during 4am-8pm ET")
    print(f"  Price ${SCANNER_PRICE_MIN}-${SCANNER_PRICE_MAX} | Float <= {SCANNER_FLOAT_MAX:,} | "
          f"RVOL >= {SCANNER_RVOL_MIN}x | Volume >= {SCANNER_VOLUME_MIN:,} | Gap >= {SCANNER_GAP_MIN_PCT}%")
    print("  Ctrl+C to stop")
    print("=" * 60 + "\n")

    if not POLYGON_API_KEY:
        print("[scanner] POLYGON_API_KEY not set — exiting.")
        return

    send_test_alert()

    volume_baseline, baseline_updated_at = load_volume_baseline()
    float_cache = load_float_cache()
    prev_scan: dict = {}

    while True:
        now_str = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
        if scanner_hours_open():
            print(f"\n[{now_str}] Scanner hours — running scan…")
            if datetime.now(timezone.utc) - baseline_updated_at >= timedelta(hours=24):
                volume_baseline = refresh_volume_baseline()
                baseline_updated_at = datetime.now(timezone.utc)

            try:
                scan_once(volume_baseline, float_cache, prev_scan)
                save_float_cache(float_cache)
            except Exception as e:
                print(f"[scanner] Error during scan: {e}")
        else:
            print(f"[{now_str}] Outside scanner hours — sleeping…")

        time.sleep(SCANNER_CHECK_INTERVAL_SECS)


if __name__ == "__main__":
    main()
