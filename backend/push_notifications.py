"""
push_notifications.py
Native iOS push via APNs (token-based .p8 auth). Degrades gracefully:
if APNs env vars aren't set, registration still works and sends are skipped,
so Slack remains the fallback channel.

Required env (all optional — missing any disables sending):
    APNS_KEY_ID       e.g. ABC123DEFG
    APNS_TEAM_ID      e.g. DEF456GHIJ
    APNS_BUNDLE_ID    e.g. com.mcallen.tradeanalyst
    APNS_KEY_PATH     path to AuthKey_XXXX.p8
    APNS_USE_SANDBOX  "1" for dev builds (default), "0" for App Store/TestFlight
"""

from __future__ import annotations

import os
import json
import time
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

DB_PATH      = Path(__file__).parent / "history.db"
DATABASE_URL = os.environ.get("DATABASE_URL")

APNS_KEY_ID      = os.environ.get("APNS_KEY_ID", "")
APNS_TEAM_ID     = os.environ.get("APNS_TEAM_ID", "")
APNS_BUNDLE_ID   = os.environ.get("APNS_BUNDLE_ID", "com.mcallen.tradeanalyst")
APNS_KEY_PATH    = os.environ.get("APNS_KEY_PATH", "")
APNS_USE_SANDBOX = os.environ.get("APNS_USE_SANDBOX", "1") == "1"

APNS_HOST = "https://api.sandbox.push.apple.com" if APNS_USE_SANDBOX else "https://api.push.apple.com"


def _configured() -> bool:
    return all([APNS_KEY_ID, APNS_TEAM_ID, APNS_KEY_PATH]) and Path(APNS_KEY_PATH).exists()


# ── token storage ─────────────────────────────────────────────────────────────

def _pg():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)

def _sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_push_table():
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS device_tokens (
                        token       TEXT PRIMARY KEY,
                        platform    TEXT,
                        created_at  TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
            conn.commit()
    else:
        with _sqlite() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_tokens (
                    token       TEXT PRIMARY KEY,
                    platform    TEXT,
                    created_at  TEXT DEFAULT (datetime('now'))
                )
            """)

def save_token(token: str, platform: str = "ios"):
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO device_tokens (token, platform) VALUES (%s,%s) ON CONFLICT (token) DO NOTHING",
                    (token, platform))
            conn.commit()
    else:
        with _sqlite() as conn:
            conn.execute("INSERT OR IGNORE INTO device_tokens (token, platform) VALUES (?,?)", (token, platform))

def _all_tokens() -> list[str]:
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT token FROM device_tokens")
                return [r[0] for r in cur.fetchall()]
    else:
        with _sqlite() as conn:
            return [r[0] for r in conn.execute("SELECT token FROM device_tokens").fetchall()]

def _delete_token(token: str):
    if DATABASE_URL:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM device_tokens WHERE token = %s", (token,))
            conn.commit()
    else:
        with _sqlite() as conn:
            conn.execute("DELETE FROM device_tokens WHERE token = ?", (token,))


# ── APNs JWT (ES256) — cached for ~50 min ─────────────────────────────────────

_jwt_cache: dict = {"token": None, "ts": 0}

def _provider_token() -> str | None:
    if not _configured():
        return None
    now = time.time()
    if _jwt_cache["token"] and now - _jwt_cache["ts"] < 3000:
        return _jwt_cache["token"]
    try:
        import jwt  # PyJWT
        key = Path(APNS_KEY_PATH).read_text()
        token = jwt.encode(
            {"iss": APNS_TEAM_ID, "iat": int(now)},
            key,
            algorithm="ES256",
            headers={"kid": APNS_KEY_ID},
        )
        _jwt_cache.update(token=token, ts=now)
        return token
    except Exception as e:
        print(f"[apns] JWT error: {e}")
        return None


# ── send ──────────────────────────────────────────────────────────────────────

def send_push(title: str, body: str, data: dict | None = None) -> int:
    """
    Send a push to every registered device. Returns number delivered.
    No-op (returns 0) if APNs isn't configured.
    """
    if not _configured():
        print("[apns] not configured — skipping push")
        return 0

    jwt_token = _provider_token()
    if not jwt_token:
        return 0

    tokens = _all_tokens()
    if not tokens:
        return 0

    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    if data:
        payload.update(data)
    body_bytes = json.dumps(payload).encode()

    delivered = 0
    try:
        import httpx
        # APNs requires HTTP/2
        with httpx.Client(http2=True, timeout=10) as client:
            for tok in tokens:
                headers = {
                    "authorization": f"bearer {jwt_token}",
                    "apns-topic": APNS_BUNDLE_ID,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                }
                try:
                    r = client.post(f"{APNS_HOST}/3/device/{tok}", content=body_bytes, headers=headers)
                    if r.status_code == 200:
                        delivered += 1
                    elif r.status_code == 410:
                        _delete_token(tok)  # token no longer valid
                    else:
                        print(f"[apns] {r.status_code} for {tok[:8]}…: {r.text}")
                except Exception as e:
                    print(f"[apns] send error: {e}")
    except Exception as e:
        print(f"[apns] client error: {e}")

    print(f"[apns] delivered {delivered}/{len(tokens)}")
    return delivered
