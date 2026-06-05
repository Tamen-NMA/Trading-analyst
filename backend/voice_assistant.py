"""
McAllen Voice Assistant
Whisper (STT) → Claude (reasoning + tools) → ElevenLabs (TTS)

Usage:
    python backend/voice_assistant.py

Controls:
    SPACE  — hold to record, release to send
    q      — quit
"""

import os
import sys
import json
import re
import queue
import threading
import tempfile
import subprocess
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import httpx
import numpy as np
import sounddevice as sd
import soundfile as sf
import whisper
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
VOICE_ID           = os.environ.get("ELEVENLABS_VOICE_ID", "dI6Ldou06iqSFGEJjKW0")
BACKEND_URL        = os.environ.get("BACKEND_URL", "http://localhost:8000")
SAMPLE_RATE        = 16_000   # Hz — Whisper native
MAX_RECORD_SECS    = 30
MODEL              = "claude-opus-4-8"
WHISPER_MODEL      = "base"   # tiny/base/small/medium — base is fast and accurate enough

# ── Clients ───────────────────────────────────────────────────────────────────
claude  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
http    = httpx.Client(timeout=30)

def emit(event_type: str, **kwargs):
    """Push a state/transcript event to the web UI (fire-and-forget)."""
    try:
        http.post(f"{BACKEND_URL}/voice/event",
                  json={"type": event_type, **kwargs}, timeout=2)
    except Exception:
        pass

# ── Load Whisper once at startup ───────────────────────────────────────────────
print("[voice] Loading Whisper model…")
stt_model = whisper.load_model(WHISPER_MODEL)
print("[voice] Whisper ready.")

# ── System prompt ─────────────────────────────────────────────────────────────
VOICE_SYSTEM = """You are the McAllen Voice Assistant — a sharp, concise finance analyst partner.

## Your tools
- get_price: fetch live price, MAs, and candles for a ticker
- run_analysis: run a full McAllen technical + fundamental analysis for a ticker
- get_history: retrieve the user's past analyses (optional: filter by ticker)
- explain_term: explain a finance term in plain English
- search_web: search the web for earnings, news, filings, or macro data

## Rules
- Answer first, details on request. Keep spoken responses under 4 sentences unless asked for more.
- Always use get_price or get_history before discussing a specific ticker.
- Format numbers for speech: say "two hundred and twelve dollars" not "$212.00".
- Say "BULLISH", "BEARISH", or "NEUTRAL" clearly when stating a verdict.
- Never read out markdown formatting — no asterisks, hashes, or backticks.
- If the user asks to run analysis, call run_analysis and summarise: verdict, entry trigger, stop loss, risk/reward.
- You are not a licensed financial adviser. Frame outputs as analysis, not advice."""

# ── Tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "get_price",
        "description": "Get live price, 52W high/low, MA20/50/200, volume ratio, and last 30 candles for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock symbol, e.g. AAPL"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "run_analysis",
        "description": "Run a full McAllen technical + fundamental analysis for a ticker. Returns the full analysis text and verdict.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock symbol, e.g. NVDA"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_history",
        "description": "Get the user's past analyses from the database. Optionally filter by ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Optional: filter by stock symbol"}
            },
        },
    },
    {
        "name": "explain_term",
        "description": "Explain a finance or trading term in plain English.",
        "input_schema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "The term or phrase to explain"}
            },
            "required": ["term"],
        },
    },
    {
        "name": "search_web",
        "description": "Search the web for earnings data, news, SEC filings, macro trends, or any finance research.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"],
        },
    },
]

# ── Tool execution ─────────────────────────────────────────────────────────────
def run_tool(name: str, inputs: dict) -> str:
    try:
        if name == "get_price":
            r = http.get(f"{BACKEND_URL}/price/{inputs['ticker'].upper()}")
            r.raise_for_status()
            d = r.json()
            return json.dumps(d)

        elif name == "run_analysis":
            ticker = inputs["ticker"].upper()
            # Collect the full SSE stream
            text_chunks = []
            with http.stream("GET", f"{BACKEND_URL}/analyze/{ticker}") as r:
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = json.loads(line[5:].strip())
                    if payload.get("type") == "text":
                        text_chunks.append(payload["content"])
                    elif payload.get("type") == "error":
                        return f"Analysis error: {payload['content']}"
            full = "".join(text_chunks)
            # Return a compressed version for the voice response
            verdict_match = re.search(r"\*\*VERDICT:\*\*\s*(BULLISH|BEARISH|NEUTRAL)[^\n]*", full, re.I)
            setup_match  = re.search(r"\*\*TRADE SETUP:\*\*(.*?)(?=\n\n|\*\*VERDICT|\Z)", full, re.S)
            verdict = verdict_match.group(0).replace("**", "") if verdict_match else ""
            setup   = setup_match.group(1).strip().replace("**", "").replace("- ", "") if setup_match else ""
            return f"{verdict}\n\nTRADE SETUP:\n{setup}\n\n[Full analysis saved to history]"

        elif name == "get_history":
            r = http.get(f"{BACKEND_URL}/history")
            r.raise_for_status()
            rows = r.json()
            ticker = inputs.get("ticker", "").upper()
            if ticker:
                rows = [x for x in rows if x["ticker"] == ticker]
            # Return the 5 most recent
            rows = rows[:5]
            if not rows:
                return "No analyses found" + (f" for {ticker}" if ticker else "") + "."
            lines = []
            for row in rows:
                lines.append(
                    f"{row['ticker']} — {row['searched_at']} — {row.get('verdict','N/A')} — ${row.get('price','?')}"
                )
            return "\n".join(lines)

        elif name == "explain_term":
            r = http.post(f"{BACKEND_URL}/explain", json={"text": inputs["term"]})
            r.raise_for_status()
            return r.json()["explanation"]

        elif name == "search_web":
            # Use Claude's built-in web search via a one-shot sub-call
            resp = claude.messages.create(
                model=MODEL,
                max_tokens=512,
                tools=[{"type": "web_search_20260209", "name": "web_search"}],
                messages=[{"role": "user", "content": inputs["query"]}],
            )
            for block in resp.content:
                if hasattr(block, "text"):
                    return block.text
            return "No results found."

    except Exception as e:
        return f"Tool error ({name}): {e}"

    return "Unknown tool."


# ── Claude reasoning loop ─────────────────────────────────────────────────────
def think(conversation: list) -> str:
    """Run Claude with tool use until a final text response is produced."""
    messages = conversation.copy()
    cached_system = [{"type": "text", "text": VOICE_SYSTEM, "cache_control": {"type": "ephemeral"}}]

    while True:
        response = claude.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=cached_system,
            tools=TOOLS,
            messages=messages,
        )

        # Collect any text from this turn
        text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]

        if response.stop_reason == "end_turn":
            return " ".join(text_parts) if text_parts else "I didn't catch that. Can you rephrase?"

        if response.stop_reason == "tool_use":
            # Add assistant message with all content blocks
            messages.append({
                "role": "assistant",
                "content": [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in response.content],
            })
            # Execute each tool call and build tool_result blocks
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [tool] {block.name}({json.dumps(block.input)})")
                    result = run_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Fallback: return whatever text we have
        return " ".join(text_parts) if text_parts else "Done."


# ── ElevenLabs TTS ────────────────────────────────────────────────────────────
def speak(text: str):
    """Fetch MP3 from ElevenLabs and play via macOS afplay (no format issues)."""
    clean = re.sub(r"[*#`_~]", "", text)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": clean,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        "output_format": "mp3_44100_128",
    }
    try:
        with httpx.stream("POST", url, headers=headers, json=payload, timeout=30) as r:
            r.raise_for_status()
            audio_bytes = b"".join(r.iter_bytes())

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        subprocess.run(["afplay", tmp_path], check=True)
        os.unlink(tmp_path)
    except Exception as e:
        print(f"[tts] error: {e}")
        print(f"[Allen] {clean}")


# ── Whisper STT ───────────────────────────────────────────────────────────────
# ── Wake word config ──────────────────────────────────────────────────────────
WAKE_PHRASES    = ["hello allen", "hey allen", "hello alan", "hey alan",
                   "hello allen let's make money moves", "allen"]
ENERGY_THRESHOLD = 0.008   # RMS below this = silence
WAKE_CHUNK_SECS  = 2.0     # seconds per wake-word audio window
SILENCE_STOP_SECS = 1.8    # seconds of silence before auto-stopping command recording


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0


def record_chunk(secs: float) -> np.ndarray:
    """Record a fixed-length audio chunk synchronously."""
    frames = int(SAMPLE_RATE * secs)
    data = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return data.flatten()


def transcribe(audio: np.ndarray) -> str:
    if len(audio) < SAMPLE_RATE * 0.3:
        return ""
    result = stt_model.transcribe(audio, fp16=False, language="en")
    return result["text"].strip()


def listen_for_wake_word():
    """Block until the wake phrase is detected. Energy-gated so Whisper only
    runs when there is actual speech — keeps CPU usage low in standby."""
    print("\n💤  Standby — say  \"Hello Allen\"  to activate …\n")
    while True:
        chunk = record_chunk(WAKE_CHUNK_SECS)
        if rms(chunk) < ENERGY_THRESHOLD:
            continue                      # skip silence — don't bother transcribing
        text = transcribe(chunk).lower().strip()
        if any(phrase in text for phrase in WAKE_PHRASES):
            return


def record_command() -> np.ndarray:
    """Record the user's command, auto-stopping after SILENCE_STOP_SECS of silence."""
    print("  🎙️  Listening …")
    chunks: list = []
    silent_blocks = 0
    block_secs    = 0.1
    block_frames  = int(SAMPLE_RATE * block_secs)
    silence_limit = int(SILENCE_STOP_SECS / block_secs)

    def _cb(indata, frames, time, status):
        chunks.append(indata.copy())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="float32", blocksize=block_frames,
                            callback=_cb)
    stream.start()

    import time as _time
    deadline = _time.time() + MAX_RECORD_SECS
    while _time.time() < deadline:
        _time.sleep(block_secs)
        if chunks:
            level = rms(chunks[-1].flatten())
            silent_blocks = silent_blocks + 1 if level < ENERGY_THRESHOLD else 0
            if silent_blocks >= silence_limit:
                break

    stream.stop()
    stream.close()
    return np.concatenate(chunks, axis=0).flatten() if chunks else np.array([], dtype=np.float32)


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 60)
    print("  McAllen Voice Assistant  —  powered by Allen")
    print("  Wake word: \"Hello Allen\"")
    print("  Ctrl-C to quit.")
    print("=" * 60)

    emit("status", state="standby")
    speak("Allen is online. Say Hello Allen whenever you're ready.")

    conversation: list = []

    while True:
        try:
            emit("status", state="standby")
            listen_for_wake_word()
            emit("status", state="listening")

            print("\n✅  Activated — go ahead …")
            speak("Yeah?")

            audio = record_command()
            transcript = transcribe(audio)

            if not transcript:
                speak("I didn't catch that. Say Hello Allen to try again.")
                emit("status", state="standby")
                continue

            print(f"\n[you]  {transcript}")
            emit("transcript", role="you", text=transcript)
            conversation.append({"role": "user", "content": transcript})

            emit("status", state="thinking")
            print("[thinking…]")
            reply = think(conversation)
            conversation.append({"role": "assistant", "content": reply})

            emit("transcript", role="allen", text=reply)
            emit("status", state="speaking")
            print(f"[Allen]  {reply}\n")
            speak(reply)
            emit("status", state="standby")

            # Keep last 20 turns to stay within token limits
            if len(conversation) > 40:
                conversation = conversation[-40:]

        except KeyboardInterrupt:
            print("\n")
            emit("status", state="standby")
            speak("Peace out.")
            break


if __name__ == "__main__":
    main()
