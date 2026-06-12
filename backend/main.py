import os
import json
import re
import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import yfinance as yf
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="McAllen Trading Analyst")

# ── Voice event broadcast (SSE) ───────────────────────────
_voice_listeners: list = []
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async_client = anthropic.AsyncAnthropic()

DB_PATH = Path(__file__).parent / "history.db"
DATABASE_URL = os.environ.get("DATABASE_URL")

# ── Database ──────────────────────────────────────────────
def _pg():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)

def _sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS alerts_log (
                        id            SERIAL PRIMARY KEY,
                        ticker        TEXT NOT NULL,
                        price         REAL,
                        entry_price   REAL,
                        stop_loss     REAL,
                        target        REAL,
                        rr_ratio      TEXT,
                        pattern       TEXT,
                        signal        TEXT,
                        fired_at      TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id           SERIAL PRIMARY KEY,
                        ticker       TEXT NOT NULL,
                        entry_price  REAL NOT NULL,
                        exit_price   REAL,
                        shares       REAL,
                        stop_loss    REAL,
                        target       REAL,
                        setup        TEXT,
                        notes        TEXT,
                        status       TEXT DEFAULT 'open',
                        pnl          REAL,
                        pnl_pct      REAL,
                        opened_at    TIMESTAMPTZ DEFAULT NOW(),
                        closed_at    TIMESTAMPTZ
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS analyses (
                        id            SERIAL PRIMARY KEY,
                        ticker        TEXT NOT NULL,
                        searched_at   TEXT NOT NULL,
                        analysis_text TEXT NOT NULL,
                        price         REAL,
                        daily_change  REAL,
                        verdict       TEXT,
                        user_ip       TEXT
                    )
                """)
                cur.execute("ALTER TABLE analyses ADD COLUMN IF NOT EXISTS user_ip TEXT")
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts_log (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker       TEXT NOT NULL,
                    price        REAL,
                    entry_price  REAL,
                    stop_loss    REAL,
                    target       REAL,
                    rr_ratio     TEXT,
                    pattern      TEXT,
                    signal       TEXT,
                    fired_at     TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker       TEXT NOT NULL,
                    entry_price  REAL NOT NULL,
                    exit_price   REAL,
                    shares       REAL,
                    stop_loss    REAL,
                    target       REAL,
                    setup        TEXT,
                    notes        TEXT,
                    status       TEXT DEFAULT 'open',
                    pnl          REAL,
                    pnl_pct      REAL,
                    opened_at    TEXT DEFAULT (datetime('now')),
                    closed_at    TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analyses (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker        TEXT NOT NULL,
                    searched_at   TEXT NOT NULL,
                    analysis_text TEXT NOT NULL,
                    price         REAL,
                    daily_change  REAL,
                    verdict       TEXT,
                    user_ip       TEXT
                )
            """)
            try:
                conn.execute("ALTER TABLE analyses ADD COLUMN user_ip TEXT")
            except Exception:
                pass  # column already exists

@app.on_event("startup")
async def startup():
    init_db()

DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "2"))

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def count_today_analyses(ip: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        if DATABASE_URL:
            with _pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM analyses WHERE user_ip = %s AND searched_at LIKE %s",
                        (ip, f"{today}%"),
                    )
                    return cur.fetchone()[0]
        else:
            with _sqlite() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM analyses WHERE user_ip = ? AND searched_at LIKE ?",
                    (ip, f"{today}%"),
                ).fetchone()
                return row[0] if row else 0
    except Exception as e:
        print(f"[rate-limit] DB error, allowing request: {e}")
        return 0  # fail open — don't block users due to DB issues

def save_analysis(ticker: str, text: str, price_data: dict, user_ip: str = ""):
    try:
        verdict_match = re.search(r"\*\*VERDICT:\*\*\s*(BULLISH|BEARISH|NEUTRAL)", text, re.I)
        verdict = verdict_match.group(1).upper() if verdict_match else None
        searched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        vals = (ticker, searched_at, text, price_data.get("current_price"), price_data.get("daily_change_pct"), verdict, user_ip)
        if DATABASE_URL:
            with _pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO analyses (ticker, searched_at, analysis_text, price, daily_change, verdict, user_ip) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        vals,
                    )
                conn.commit()
        else:
            with _sqlite() as conn:
                conn.execute(
                    "INSERT INTO analyses (ticker, searched_at, analysis_text, price, daily_change, verdict, user_ip) VALUES (?,?,?,?,?,?,?)",
                    vals,
                )
    except Exception as e:
        print(f"[save-analysis] DB error (analysis still delivered): {e}")

def _history_rows(rows_raw, pg: bool) -> list:
    if pg:
        return [dict(r) for r in rows_raw]
    return [dict(r) for r in rows_raw]

# ── History endpoints ──────────────────────────────────────
@app.get("/history")
async def get_history():
    if DATABASE_URL:
        import psycopg2.extras
        with _pg() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, ticker, searched_at, price, daily_change, verdict FROM analyses ORDER BY id DESC LIMIT 200"
                )
                return [dict(r) for r in cur.fetchall()]
    else:
        with _sqlite() as conn:
            rows = conn.execute(
                "SELECT id, ticker, searched_at, price, daily_change, verdict FROM analyses ORDER BY id DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]

@app.get("/history/{analysis_id}")
async def get_analysis(analysis_id: int):
    if DATABASE_URL:
        import psycopg2.extras
        with _pg() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM analyses WHERE id = %s", (analysis_id,))
                row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        return dict(row)
    else:
        with _sqlite() as conn:
            row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        return dict(row)

@app.delete("/history/{analysis_id}")
async def delete_analysis(analysis_id: int):
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM analyses WHERE id = %s", (analysis_id,))
            conn.commit()
    else:
        with _sqlite() as conn:
            conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
    return {"deleted": analysis_id}

# ── System prompt ──────────────────────────────────────────
SYSTEM_PROMPT = """You are a professional trading analyst using Fred McAllen's *Charting and Technical Analysis* methodology. You combine technical analysis with fundamental research and macro awareness.

## Core McAllen Principles
- Market timing is everything — buying at the wrong time causes loss
- Protect capital first — always define stop loss before entry
- "When in doubt, stay out" — never force a trade on unclear signals
- Always start with the big picture (multi-year chart) before drilling into daily candles
- Never fight the primary trend; volume confirms price

## Analysis Framework

**1. PRIMARY TREND (Dow Theory)**
- Primary (Secular): Bull = higher highs/lows; Bear = lower highs/lows
- Three phases: Accumulation → Public Participation → Distribution (bull)
- Never buy during Distribution phase of a bull market

**2. KEY PRICE LEVELS**
- Support: Prior lows, gap bottoms, round numbers where buyers step in
- Resistance: Prior highs, gap tops where sellers dominate
- Role reversal: Broken support → new resistance; broken resistance → new support
- Red flag: Price fails to reach prior high. Major red flag: price breaks below support.

**3. TREND LINES & CHANNELS**
- Draw using 2+ confirmed swing lows (uptrend) or swing highs (downtrend)
- Channel line touch + bearish candle = shorting opportunity
- Advance steeper than 45° is unsustainable — watch for exhaustion

**4. CANDLESTICK SIGNALS**
Bullish: Hammer (long lower wick after decline), Bullish Engulfing, Bullish Harami, Three White Soldiers, Morning Star
Bearish: Shooting Star (long upper wick after advance), Hanging Man, Bearish Engulfing, Three Black Crows, Spinning Top at top
Rules: Always require confirmation from the NEXT candle. Doji = earliest warning (1-5 days before reversal). Multiple warnings = exit signal.

**5. CHART PATTERNS**
Reversal: Head & Shoulders (neckline break = confirmed sell), Inverted H&S (breakout = buy), Double Top/Bottom, Saucers, V-Reversals
Continuation: Bull/Bear Flags, Pennants, Triangles — volume decreases during formation, surges on breakout

**6. PRICE GAPS**
Breakaway = new trend starts (rarely fills quickly); Runaway/Measuring = mid-trend acceleration; Exhaustion = trend ending; Common = fills quickly
Key rule: Gaps usually get filled. Unfilled gap above = overhead resistance; below = support.

**7. PERCENTAGE RETRACEMENTS**
33% = minor correction (trend very strong); 50% = normal healthy correction (most common); 66% = deep correction (trend weakening)
>66% likely = full trend reversal, not a correction. Use 50% as prime buy zone in uptrend.

**8. MOVING AVERAGES**
200 DMA: Primary trend direction (price above = bull, below = bear). Most reliable.
50 DMA: Intermediate trend; common support/resistance in uptrends
20 DMA: Short-term; swing trader entries
Golden Cross (50 crosses above 200) = long-term bull signal; Death Cross = long-term bear signal
Warning: In sideways market, MAs generate false signals (whipsaws) — reduce position size or stand aside

**9. STOP LOSSES (Non-Negotiable)**
Entry stop: Just below support that justified entry (long) or above resistance (short)
Trailing stop: Move up as price advances to lock in gains. NEVER move a stop lower.
Rule: If stock breaks major support, exit immediately — do not hope

**10. VOLUME**
High volume breakout = conviction; Low volume breakout = suspect
Volume should increase in direction of trend and decrease on pullbacks
High volume reversal candle = significant warning

## Structured Output Format
Always format your final analysis EXACTLY as follows:

---
**PRIMARY TREND:** [Secular Bull / Secular Bear / Sideways] — [brief evidence from price action]

**KEY LEVELS:**
- Resistance: [specific price levels]
- Support: [specific price levels]

**PATTERN:** [specific pattern name or "No clear pattern forming"]

**CANDLESTICK SIGNALS:** [recent significant signals with dates if available]

**MOVING AVERAGES:** [price vs 50/200 DMA, any crossovers or crossover warnings]

**VOLUME:** [Confirming / Diverging / Neutral] — [explanation]

**FUNDAMENTALS:** [earnings trend, revenue growth, debt, P/E, analyst ratings — from web research]

**MACRO CONTEXT:** [interest rates, sector trend, regulatory risk — from web research]

**TRADE SETUP:**
- Bias: [Long / Short / No Trade — "when in doubt, stay out"]
- Entry trigger: [specific condition to enter]
- Stop loss: [$level — reason]
- Target: [$level — reason]
- Risk/Reward: [X:1 ratio]

**VERDICT:** [BULLISH / BEARISH / NEUTRAL] — [one-sentence summary]
---

*Based on McAllen's Charting and Technical Analysis. Educational only — not financial advice.*"""


# ── Price data ─────────────────────────────────────────────
def get_price_data(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if hist.empty:
        raise HTTPException(status_code=404, detail=f"No price data found for {ticker}")

    hist["MA20"] = hist["Close"].rolling(20).mean()
    hist["MA50"] = hist["Close"].rolling(50).mean()
    hist["MA200"] = hist["Close"].rolling(200).mean()

    cur = hist.iloc[-1]
    prev = hist.iloc[-2]

    def safe_round(val, n=2):
        return round(float(val), n) if pd.notna(val) else None

    avg_vol = float(hist["Volume"].tail(20).mean())
    vol_ratio = float(cur["Volume"]) / avg_vol if avg_vol > 0 else 1.0

    candles = []
    for dt, row in hist.tail(30).iterrows():
        candles.append({
            "date": str(dt.date()),
            "open": safe_round(row["Open"]),
            "high": safe_round(row["High"]),
            "low": safe_round(row["Low"]),
            "close": safe_round(row["Close"]),
            "volume": int(row["Volume"]),
        })

    # Float shares and 3-month avg volume from stock.info (best-effort)
    float_shares = None
    avg_3m_volume = None
    try:
        info = stock.info
        float_shares = info.get("floatShares")
        avg_3m_volume = info.get("averageDailyVolume3Month") or info.get("averageVolume")
    except Exception:
        pass

    return {
        "ticker": ticker,
        "current_price": safe_round(cur["Close"]),
        "daily_change_pct": safe_round(((cur["Close"] - prev["Close"]) / prev["Close"]) * 100),
        "52w_high": safe_round(hist["High"].max()),
        "52w_low": safe_round(hist["Low"].min()),
        "pct_below_52w_high": safe_round(((hist["High"].max() - cur["Close"]) / hist["High"].max()) * 100, 1),
        "ma20": safe_round(hist["MA20"].iloc[-1]),
        "ma50": safe_round(hist["MA50"].iloc[-1]),
        "ma200": safe_round(hist["MA200"].iloc[-1]),
        "price_vs_ma20": "above" if cur["Close"] > hist["MA20"].iloc[-1] else "below",
        "price_vs_ma50": "above" if cur["Close"] > hist["MA50"].iloc[-1] else "below",
        "price_vs_ma200": "above" if cur["Close"] > hist["MA200"].iloc[-1] else "below",
        "today_volume": int(cur["Volume"]),
        "avg_20d_volume": int(avg_vol),
        "volume_ratio": f"{vol_ratio:.1f}x avg",
        "float_shares": int(float_shares) if float_shares else None,
        "avg_3m_volume": int(avg_3m_volume) if avg_3m_volume else None,
        "last_30_candles": candles,
    }


# ── Rate limit pre-flight ─────────────────────────────────
@app.get("/can-analyze")
async def can_analyze(request: Request):
    ip = get_client_ip(request)
    used = count_today_analyses(ip)
    remaining = max(0, DAILY_LIMIT - used)
    return {"allowed": remaining > 0, "used": used, "limit": DAILY_LIMIT, "remaining": remaining}


# ── Analysis endpoint ──────────────────────────────────────
@app.get("/analyze/{ticker}")
async def analyze(ticker: str, request: Request):
    ip = get_client_ip(request)
    ticker = ticker.upper().strip()

    try:
        price_data = get_price_data(ticker)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    async def stream():
        used = count_today_analyses(ip)
        if used >= DAILY_LIMIT:
            remaining = 0
            yield f"data: {json.dumps({'type': 'error', 'content': f'You have reached your daily limit of {DAILY_LIMIT} analyses. Come back tomorrow.'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'meta', 'data': price_data})}\n\n"

        accumulated = []
        messages = [
            {
                "role": "user",
                "content": (
                    f"Perform a complete McAllen trading analysis for **{ticker}**.\n\n"
                    f"## Live Price Data (yfinance)\n```json\n{json.dumps(price_data, indent=2)}\n```\n\n"
                    f"## Step 1 — Web research (use web_search for each)\n"
                    f'1. Search: "{ticker} latest earnings revenue growth 2025 2026"\n'
                    f'2. Search: "{ticker} debt ratio P/E analyst rating price target"\n'
                    f'3. Search: "Federal Reserve interest rate outlook 2025 2026"\n'
                    f'4. Search: "{ticker} sector performance trend 2025"\n\n'
                    f"## Step 2 — Apply the full McAllen framework to the price data above.\n\n"
                    f"## Step 3 — Output the structured analysis in the exact format from your system prompt."
                ),
            }
        ]

        cached_system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

        while True:
            try:
                async with async_client.messages.stream(
                    model="claude-opus-4-8",
                    max_tokens=8192,
                    system=cached_system,
                    tools=[{"type": "web_search_20260209", "name": "web_search"}],
                    messages=messages,
                ) as stream_ctx:
                    async for event in stream_ctx:
                        if (
                            event.type == "content_block_delta"
                            and hasattr(event.delta, "type")
                            and event.delta.type == "text_delta"
                        ):
                            accumulated.append(event.delta.text)
                            yield f"data: {json.dumps({'type': 'text', 'content': event.delta.text})}\n\n"

                    final = await stream_ctx.get_final_message()

                    if final.stop_reason != "pause_turn":
                        break

                    # Serialize content blocks and cache the last one so turn-2
                    # re-sends turn-1 search results at ~10% of normal input cost
                    blocks = [
                        b.model_dump() if hasattr(b, "model_dump") else dict(b)
                        for b in final.content
                    ]
                    if blocks:
                        blocks[-1]["cache_control"] = {"type": "ephemeral"}
                    messages.append({"role": "assistant", "content": blocks})

            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
                return

        # Persist to history
        full_text = "".join(accumulated)
        if full_text.strip():
            save_analysis(ticker, full_text, price_data, user_ip=ip)

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/price/{ticker}")
async def price(ticker: str):
    ticker = ticker.upper().strip()
    try:
        return get_price_data(ticker)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "model": "claude-opus-4-8"}


# ── Watchlist endpoints ────────────────────────────────────────────────────────

class WatchlistItem(BaseModel):
    ticker:       str
    entry_price:  Optional[float] = None
    stop_loss:    Optional[float] = None
    target:       Optional[float] = None
    rr_ratio:     Optional[str]   = None
    verdict:      Optional[str]   = None
    analysis_id:  Optional[int]   = None

def _parse_trade_setup(analysis_text: str) -> dict:
    """Extract entry, stop, target and R/R from a saved analysis text."""
    result = {}
    entry_match = re.search(r"Entry trigger[:\s]+\$?([\d,.]+)", analysis_text, re.I)
    stop_match  = re.search(r"Stop loss[:\s]+\$?([\d,.]+)", analysis_text, re.I)
    target_match= re.search(r"Target[:\s]+\$?([\d,.]+)", analysis_text, re.I)
    rr_match    = re.search(r"Risk/Reward[:\s]+([\d.]+):1", analysis_text, re.I)
    if entry_match:  result["entry_price"] = float(entry_match.group(1).replace(",", ""))
    if stop_match:   result["stop_loss"]   = float(stop_match.group(1).replace(",", ""))
    if target_match: result["target"]      = float(target_match.group(1).replace(",", ""))
    if rr_match:     result["rr_ratio"]    = f"{rr_match.group(1)}:1"
    return result

@app.post("/watchlist")
async def add_to_watchlist(item: WatchlistItem):
    ticker = item.ticker.upper().strip()
    entry  = item.entry_price
    stop   = item.stop_loss
    target = item.target
    rr     = item.rr_ratio
    verdict= item.verdict

    # If analysis_id provided, auto-parse trade setup from analysis text
    if item.analysis_id and not entry:
        try:
            if DATABASE_URL:
                import psycopg2.extras
                with _pg() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT analysis_text, verdict FROM analyses WHERE id = %s", (item.analysis_id,))
                        row = cur.fetchone()
            else:
                with _sqlite() as conn:
                    row = conn.execute("SELECT analysis_text, verdict FROM analyses WHERE id = ?", (item.analysis_id,)).fetchone()
            if row:
                parsed = _parse_trade_setup(row["analysis_text"])
                entry  = parsed.get("entry_price", entry)
                stop   = parsed.get("stop_loss", stop)
                target = parsed.get("target", target)
                rr     = parsed.get("rr_ratio", rr)
                verdict= verdict or row["verdict"]
        except Exception as e:
            print(f"[watchlist] Could not parse analysis {item.analysis_id}: {e}")

    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO watchlist (ticker, entry_price, stop_loss, target, rr_ratio, verdict, analysis_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (ticker, entry, stop, target, rr, verdict, item.analysis_id)
                )
                new_id = cur.fetchone()[0]
            conn.commit()
    else:
        with _sqlite() as conn:
            cur = conn.execute(
                """INSERT INTO watchlist (ticker, entry_price, stop_loss, target, rr_ratio, verdict, analysis_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (ticker, entry, stop, target, rr, verdict, item.analysis_id)
            )
            new_id = cur.lastrowid
    return {"id": new_id, "ticker": ticker, "entry_price": entry, "stop_loss": stop, "target": target, "rr_ratio": rr}

@app.get("/watchlist")
async def get_watchlist():
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

@app.delete("/watchlist/{watchlist_id}")
async def remove_from_watchlist(watchlist_id: int):
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE watchlist SET active = false WHERE id = %s", (watchlist_id,))
            conn.commit()
    else:
        with _sqlite() as conn:
            conn.execute("UPDATE watchlist SET active = 0 WHERE id = ?", (watchlist_id,))
    return {"removed": watchlist_id}

# ── Trades & P&L ───────────────────────────────────────────────────────────────

ACCOUNT_SIZE = float(os.environ.get("ACCOUNT_SIZE", "25000"))
RISK_PCT     = float(os.environ.get("RISK_PCT", "1.0"))

class TradeOpen(BaseModel):
    ticker:      str
    entry_price: float
    shares:      Optional[float] = None
    stop_loss:   Optional[float] = None
    target:      Optional[float] = None
    setup:       Optional[str] = None
    notes:       Optional[str] = None

class TradeClose(BaseModel):
    exit_price: float
    notes:      Optional[str] = None

@app.post("/trades")
async def open_trade(t: TradeOpen):
    ticker = t.ticker.upper().strip()
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO trades (ticker, entry_price, shares, stop_loss, target, setup, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (ticker, t.entry_price, t.shares, t.stop_loss, t.target, t.setup, t.notes))
                new_id = cur.fetchone()[0]
            conn.commit()
    else:
        with _sqlite() as conn:
            cur = conn.execute(
                """INSERT INTO trades (ticker, entry_price, shares, stop_loss, target, setup, notes)
                   VALUES (?,?,?,?,?,?,?)""",
                (ticker, t.entry_price, t.shares, t.stop_loss, t.target, t.setup, t.notes))
            new_id = cur.lastrowid
    return {"id": new_id, "ticker": ticker, "status": "open"}

@app.put("/trades/{trade_id}/close")
async def close_trade(trade_id: int, body: TradeClose):
    now = datetime.now(timezone.utc).isoformat()
    # fetch the open trade
    if DATABASE_URL:
        import psycopg2.extras
        with _pg() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM trades WHERE id = %s", (trade_id,))
                row = cur.fetchone()
    else:
        with _sqlite() as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")
    row = dict(row)
    shares = row.get("shares") or 0
    pnl = (body.exit_price - row["entry_price"]) * shares if shares else None
    pnl_pct = ((body.exit_price - row["entry_price"]) / row["entry_price"]) * 100
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE trades SET exit_price=%s, status='closed', pnl=%s, pnl_pct=%s, closed_at=%s, notes=COALESCE(%s, notes) WHERE id=%s",
                    (body.exit_price, pnl, pnl_pct, now, body.notes, trade_id))
            conn.commit()
    else:
        with _sqlite() as conn:
            conn.execute(
                "UPDATE trades SET exit_price=?, status='closed', pnl=?, pnl_pct=?, closed_at=?, notes=COALESCE(?, notes) WHERE id=?",
                (body.exit_price, pnl, pnl_pct, now, body.notes, trade_id))
    return {"id": trade_id, "status": "closed", "pnl": pnl, "pnl_pct": round(pnl_pct, 2)}

@app.get("/trades")
async def list_trades(status: Optional[str] = None, limit: int = 100):
    q = "SELECT * FROM trades"
    args: tuple = ()
    if status in ("open", "closed"):
        q += " WHERE status = %s" if DATABASE_URL else " WHERE status = ?"
        args = (status,)
    q += " ORDER BY opened_at DESC LIMIT " + str(int(limit))
    if DATABASE_URL:
        import psycopg2.extras
        with _pg() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(q, args)
                return [dict(r) for r in cur.fetchall()]
    else:
        with _sqlite() as conn:
            rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

@app.get("/pnl")
async def pnl_summary():
    trades = await list_trades(status="closed", limit=500)
    open_trades = await list_trades(status="open", limit=100)
    closed = [t for t in trades if t.get("pnl_pct") is not None]
    wins   = [t for t in closed if t["pnl_pct"] > 0]
    losses = [t for t in closed if t["pnl_pct"] <= 0]
    total_pnl = sum(t["pnl"] for t in closed if t.get("pnl") is not None)

    # this month
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    month_closed = [t for t in closed if str(t.get("closed_at", "")).startswith(month_prefix)]
    month_pnl = sum(t["pnl"] for t in month_closed if t.get("pnl") is not None)

    # per-setup breakdown
    by_setup: dict = {}
    for t in closed:
        s = t.get("setup") or "unspecified"
        d = by_setup.setdefault(s, {"trades": 0, "wins": 0, "avg_pnl_pct": 0.0, "_sum": 0.0})
        d["trades"] += 1
        d["_sum"] += t["pnl_pct"]
        if t["pnl_pct"] > 0: d["wins"] += 1
    for s, d in by_setup.items():
        d["avg_pnl_pct"] = round(d.pop("_sum") / d["trades"], 2)
        d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1)

    return {
        "account_size": ACCOUNT_SIZE,
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_win_pct":  round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else None,
        "total_pnl": round(total_pnl, 2),
        "month_pnl": round(month_pnl, 2),
        "month_pnl_pct_of_account": round(month_pnl / ACCOUNT_SIZE * 100, 2),
        "month_goal_pct": 40.0,
        "by_setup": by_setup,
        "open": open_trades,
        "recent_closed": closed[:20],
    }

@app.get("/alerts")
async def get_alerts(limit: int = 20):
    if DATABASE_URL:
        import psycopg2.extras
        with _pg() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM alerts_log ORDER BY fired_at DESC LIMIT %s", (limit,)
                )
                return [dict(r) for r in cur.fetchall()]
    else:
        with _sqlite() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts_log ORDER BY fired_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


class ExplainRequest(BaseModel):
    text: str

@app.post("/explain")
async def explain(body: ExplainRequest):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Selection too long")

    msg = await async_client.messages.create(
        model="claude-opus-4-8",
        max_tokens=200,
        system=(
            "You are a friendly trading teacher explaining concepts to a complete beginner. "
            "Use plain English, no jargon. Be concise: 2-3 sentences max. "
            "Use a simple analogy if it helps. Never say 'in simple terms' or 'basically'."
        ),
        messages=[{"role": "user", "content": f"Explain this: {text}"}],
    )
    return {"explanation": msg.content[0].text}


# ── Voice UI endpoints ────────────────────────────────────
@app.post("/voice/event")
async def voice_event(request: Request):
    """Voice assistant POSTs state/transcript events here."""
    data = await request.json()
    for q in _voice_listeners[:]:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass
    return {"ok": True}

@app.get("/voice/stream")
async def voice_stream(request: Request):
    """Browser subscribes here via SSE to receive live voice events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=60)
    _voice_listeners.append(q)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            try:
                _voice_listeners.remove(q)
            except ValueError:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Serve frontend — must be LAST so API routes take precedence
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
