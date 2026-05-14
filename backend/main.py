import os
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
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

DAILY_LIMIT = 2

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def count_today_analyses(ip: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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

def save_analysis(ticker: str, text: str, price_data: dict, user_ip: str = ""):
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
        "last_30_candles": candles,
    }


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
                    model="claude-opus-4-7",
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
    return {"status": "ok", "model": "claude-opus-4-7"}


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
        model="claude-opus-4-7",
        max_tokens=200,
        system=(
            "You are a friendly trading teacher explaining concepts to a complete beginner. "
            "Use plain English, no jargon. Be concise: 2-3 sentences max. "
            "Use a simple analogy if it helps. Never say 'in simple terms' or 'basically'."
        ),
        messages=[{"role": "user", "content": f"Explain this: {text}"}],
    )
    return {"explanation": msg.content[0].text}


# Serve frontend — must be LAST so API routes take precedence
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
