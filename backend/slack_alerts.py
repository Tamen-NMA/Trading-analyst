"""
slack_alerts.py
Sends formatted trade alert messages to Slack via incoming webhook.
"""

from __future__ import annotations


import os
import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
SLACK_CHANNEL     = os.environ.get("SLACK_CHANNEL", "#trade-alerts")
ACCOUNT_SIZE      = float(os.environ.get("ACCOUNT_SIZE", "25000"))
RISK_PCT          = float(os.environ.get("RISK_PCT", "1.0"))


def position_size(entry: float, stop: float) -> int | None:
    """Shares to buy risking RISK_PCT of ACCOUNT_SIZE between entry and stop."""
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return None
    return int((ACCOUNT_SIZE * RISK_PCT / 100) / risk_per_share)


def send_entry_alert(
    ticker: str,
    current_price: float,
    entry_price: float,
    stop_loss: float,
    target: float,
    rr_ratio: str,
    pattern: str,
    signal_message: str,
    analysis_date: str,
    backend_url: str = "http://localhost:8000",
) -> bool:
    """
    Send a green entry alert to Slack.
    Returns True if delivered successfully.
    """
    if not SLACK_WEBHOOK_URL:
        print("[slack] SLACK_WEBHOOK_URL not set — skipping alert")
        return False

    pct_from_entry = ((current_price - entry_price) / entry_price) * 100
    pct_str = f"+{pct_from_entry:.1f}%" if pct_from_entry >= 0 else f"{pct_from_entry:.1f}%"

    payload = {
        "channel": SLACK_CHANNEL,
        "attachments": [
            {
                "color": "#3fb950",  # McAllen bullish green
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"🟢 {ticker} — Entry Alert",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": f"*Price*\n${current_price:.2f} ({pct_str} from entry)",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Entry was*\n${entry_price:.2f}",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Stop Loss*\n${stop_loss:.2f}",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Target*\n${target:.2f}",
                            },
                        ],
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Signal:* {signal_message}\n*Pattern:* `{pattern.replace('_', ' ').title()}`  |  *R/R:* {rr_ratio}",
                        },
                    },
                    *([{
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Position size:* {position_size(entry_price, stop_loss)} shares  _(risking {RISK_PCT:.0f}% of ${ACCOUNT_SIZE:,.0f} to your stop)_",
                        },
                    }] if stop_loss and position_size(entry_price, stop_loss) else []),
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"Based on your McAllen analysis · {analysis_date}  |  <{backend_url}|Open Trade Analyst>",
                            }
                        ],
                    },
                ],
            }
        ],
    }

    try:
        r = httpx.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[slack] ✅ Alert sent for {ticker}")
        return True
    except Exception as e:
        print(f"[slack] ❌ Failed to send alert for {ticker}: {e}")
        return False


def send_stop_breach_alert(
    ticker: str,
    current_price: float,
    stop_loss: float,
    entry_price: float,
) -> bool:
    """Send a red stop-loss breach alert to Slack. McAllen rule: exit immediately."""
    if not SLACK_WEBHOOK_URL:
        return False
    breach_pct = ((current_price - stop_loss) / stop_loss) * 100
    payload = {
        "channel": SLACK_CHANNEL,
        "attachments": [
            {
                "color": "#f85149",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"🔴 {ticker} — STOP LOSS BREACHED", "emoji": True},
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*Price:* ${current_price:.2f}  ({breach_pct:.1f}% below stop)\n"
                                f"*Your stop:* ${stop_loss:.2f}  |  *Entry was:* ${entry_price:.2f}\n\n"
                                f"_McAllen: if a stock breaks major support, exit immediately — do not hope._"
                            ),
                        },
                    },
                ],
            }
        ],
    }
    try:
        r = httpx.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[slack] 🔴 Stop-breach alert sent for {ticker}")
        return True
    except Exception as e:
        print(f"[slack] ❌ Stop-breach alert failed for {ticker}: {e}")
        return False


def send_test_alert() -> bool:
    """Send a test message to confirm the webhook is working."""
    if not SLACK_WEBHOOK_URL:
        print("[slack] SLACK_WEBHOOK_URL not set")
        return False

    payload = {
        "channel": SLACK_CHANNEL,
        "text": "✅ *McAllen Watchlist Agent connected.* You'll receive entry alerts here when conditions are met.",
    }
    try:
        r = httpx.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print("[slack] ✅ Test alert sent")
        return True
    except Exception as e:
        print(f"[slack] ❌ Test failed: {e}")
        return False


if __name__ == "__main__":
    send_test_alert()
