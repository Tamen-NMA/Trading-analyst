"""
candle_intel.py
Ported from frontend/index.html analyzeCandleIntel() — pure Python.
Detects candlestick patterns, signal direction, and buyer/seller control.
"""

from __future__ import annotations


from typing import Optional


def analyze_candle_intel(candles: list[dict], idx: int = -1) -> dict:
    """
    Analyse a single candle in context of surrounding candles.

    Args:
        candles: list of {open, high, low, close, volume} dicts (oldest → newest)
        idx:     index of the candle to analyse (-1 = last/most recent)

    Returns:
        {
            "pattern":  str,   # e.g. "hammer", "bullish_engulfing", "doji"
            "signal":   str,   # "bullish" | "bearish" | "neutral"
            "control":  str,   # "buyers" | "sellers" | "contested"
            "message":  str,   # human-readable description
        }
    """
    if not candles or len(candles) < 2:
        return _neutral("Not enough candle data")

    if idx == -1:
        idx = len(candles) - 1

    c = candles[idx]
    o, h, l, close = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
    prev = candles[idx - 1] if idx > 0 else None

    body      = abs(close - o)
    full_range = h - l
    if full_range == 0:
        return _neutral("No price movement")

    upper_wick = h - max(o, close)
    lower_wick = min(o, close) - l
    body_pct   = body / full_range
    is_bullish_candle = close >= o

    # ── Doji ──────────────────────────────────────────────────────────────────
    if body_pct < 0.1:
        return {
            "pattern": "doji",
            "signal":  "neutral",
            "control": "contested",
            "message": "Doji — indecision between buyers and sellers. Watch next candle for direction.",
        }

    # ── Hammer (bullish reversal after downtrend) ──────────────────────────
    if (
        lower_wick >= body * 2
        and upper_wick <= body * 0.5
        and is_bullish_candle
        and prev and float(prev["close"]) < float(prev["open"])  # prior candle bearish
    ):
        return {
            "pattern": "hammer",
            "signal":  "bullish",
            "control": "buyers",
            "message": "Hammer — buyers rejected lower prices. Strong bullish reversal signal.",
        }

    # ── Shooting Star (bearish reversal after uptrend) ─────────────────────
    if (
        upper_wick >= body * 2
        and lower_wick <= body * 0.5
        and not is_bullish_candle
        and prev and float(prev["close"]) > float(prev["open"])  # prior candle bullish
    ):
        return {
            "pattern": "shooting_star",
            "signal":  "bearish",
            "control": "sellers",
            "message": "Shooting Star — sellers drove price back down. Bearish reversal warning.",
        }

    # ── Bullish Engulfing ──────────────────────────────────────────────────
    if prev:
        po, pc = float(prev["open"]), float(prev["close"])
        if (
            is_bullish_candle
            and pc < po          # prev bearish
            and o < pc           # opens below prev close
            and close > po       # closes above prev open
        ):
            return {
                "pattern": "bullish_engulfing",
                "signal":  "bullish",
                "control": "buyers",
                "message": "Bullish Engulfing — buyers overwhelmed sellers. Strong entry signal.",
            }

    # ── Bearish Engulfing ──────────────────────────────────────────────────
    if prev:
        po, pc = float(prev["open"]), float(prev["close"])
        if (
            not is_bullish_candle
            and pc > po          # prev bullish
            and o > pc           # opens above prev close
            and close < po       # closes below prev open
        ):
            return {
                "pattern": "bearish_engulfing",
                "signal":  "bearish",
                "control": "sellers",
                "message": "Bearish Engulfing — sellers overwhelmed buyers. Exit or short signal.",
            }

    # ── Hanging Man (bearish at top) ───────────────────────────────────────
    if (
        lower_wick >= body * 2
        and upper_wick <= body * 0.5
        and not is_bullish_candle
        and prev and float(prev["close"]) > float(prev["open"])
    ):
        return {
            "pattern": "hanging_man",
            "signal":  "bearish",
            "control": "sellers",
            "message": "Hanging Man — warning sign at top. Sellers testing lower prices.",
        }

    # ── Inverted Hammer (bullish at bottom) ────────────────────────────────
    if (
        upper_wick >= body * 2
        and lower_wick <= body * 0.5
        and is_bullish_candle
        and prev and float(prev["close"]) < float(prev["open"])
    ):
        return {
            "pattern": "inverted_hammer",
            "signal":  "bullish",
            "control": "buyers",
            "message": "Inverted Hammer — buyers attempting reversal. Needs confirmation next candle.",
        }

    # ── Strong bullish candle (large body, buyers in control) ─────────────
    if is_bullish_candle and body_pct > 0.6 and close > o:
        return {
            "pattern": "strong_bull_candle",
            "signal":  "bullish",
            "control": "buyers",
            "message": "Strong bullish candle — buyers firmly in control.",
        }

    # ── Strong bearish candle ──────────────────────────────────────────────
    if not is_bullish_candle and body_pct > 0.6:
        return {
            "pattern": "strong_bear_candle",
            "signal":  "bearish",
            "control": "sellers",
            "message": "Strong bearish candle — sellers firmly in control.",
        }

    # ── Default: read body direction ───────────────────────────────────────
    if is_bullish_candle:
        return {
            "pattern": "bullish_candle",
            "signal":  "bullish",
            "control": "buyers",
            "message": "Bullish close — buyers have slight edge.",
        }
    else:
        return {
            "pattern": "bearish_candle",
            "signal":  "bearish",
            "control": "sellers",
            "message": "Bearish close — sellers have slight edge.",
        }


def is_entry_signal(intel: dict) -> bool:
    """Return True if the candle intel qualifies as a buy entry signal."""
    bullish_patterns = {
        "hammer",
        "bullish_engulfing",
        "inverted_hammer",
        "strong_bull_candle",
        "bullish_candle",
    }
    return (
        intel["signal"] == "bullish"
        and intel["control"] == "buyers"
        and intel["pattern"] in bullish_patterns
    )


def _neutral(message: str) -> dict:
    return {"pattern": "unknown", "signal": "neutral", "control": "contested", "message": message}
