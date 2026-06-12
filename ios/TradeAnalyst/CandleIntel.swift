import SwiftUI

/// Swift port of backend/candle_intel.py — pattern, signal, who's in control.
struct CandleIntel {
    enum Signal { case bullish, bearish, neutral
        var color: Color {
            switch self { case .bullish: return Theme.bull; case .bearish: return Theme.bear; case .neutral: return Theme.neutral }
        }
        var label: String {
            switch self { case .bullish: return "BULLISH"; case .bearish: return "BEARISH"; case .neutral: return "NEUTRAL" }
        }
    }
    let pattern: String
    let signal: Signal
    let control: String   // "buyers" | "sellers" | "contested"
    let message: String

    var patternLabel: String { pattern.replacingOccurrences(of: "_", with: " ").capitalized }

    /// Analyse the candle at `idx` in context of the previous one.
    static func analyze(_ candles: [Candle], at idx: Int) -> CandleIntel {
        guard candles.count >= 2, idx >= 0, idx < candles.count else {
            return CandleIntel(pattern: "unknown", signal: .neutral, control: "contested", message: "Not enough data")
        }
        let c = candles[idx]
        let prev: Candle? = idx > 0 ? candles[idx - 1] : nil
        let body = abs(c.close - c.open)
        let range = c.high - c.low
        guard range > 0 else {
            return CandleIntel(pattern: "flat", signal: .neutral, control: "contested", message: "No price movement")
        }
        let upper = c.high - max(c.open, c.close)
        let lower = min(c.open, c.close) - c.low
        let bodyPct = body / range
        let bull = c.close >= c.open
        let prevBear = prev.map { $0.close < $0.open } ?? false
        let prevBull = prev.map { $0.close > $0.open } ?? false

        if bodyPct < 0.1 {
            return CandleIntel(pattern: "doji", signal: .neutral, control: "contested",
                message: "Doji — indecision. Watch the next candle for direction.")
        }
        if lower >= body * 2, upper <= body * 0.5, bull, prevBear {
            return CandleIntel(pattern: "hammer", signal: .bullish, control: "buyers",
                message: "Hammer — buyers rejected lower prices. Strong bullish reversal.")
        }
        if upper >= body * 2, lower <= body * 0.5, !bull, prevBull {
            return CandleIntel(pattern: "shooting_star", signal: .bearish, control: "sellers",
                message: "Shooting Star — sellers drove price back down. Bearish warning.")
        }
        if let p = prev, bull, p.close < p.open, c.open < p.close, c.close > p.open {
            return CandleIntel(pattern: "bullish_engulfing", signal: .bullish, control: "buyers",
                message: "Bullish Engulfing — buyers overwhelmed sellers. Strong entry signal.")
        }
        if let p = prev, !bull, p.close > p.open, c.open > p.close, c.close < p.open {
            return CandleIntel(pattern: "bearish_engulfing", signal: .bearish, control: "sellers",
                message: "Bearish Engulfing — sellers overwhelmed buyers. Exit or short signal.")
        }
        if lower >= body * 2, upper <= body * 0.5, !bull, prevBull {
            return CandleIntel(pattern: "hanging_man", signal: .bearish, control: "sellers",
                message: "Hanging Man — warning at top. Sellers testing lower prices.")
        }
        if upper >= body * 2, lower <= body * 0.5, bull, prevBear {
            return CandleIntel(pattern: "inverted_hammer", signal: .bullish, control: "buyers",
                message: "Inverted Hammer — buyers attempting reversal. Needs confirmation.")
        }
        if bull, bodyPct > 0.6 {
            return CandleIntel(pattern: "strong_bull_candle", signal: .bullish, control: "buyers",
                message: "Strong bullish candle — buyers firmly in control.")
        }
        if !bull, bodyPct > 0.6 {
            return CandleIntel(pattern: "strong_bear_candle", signal: .bearish, control: "sellers",
                message: "Strong bearish candle — sellers firmly in control.")
        }
        return bull
            ? CandleIntel(pattern: "bullish_candle", signal: .bullish, control: "buyers", message: "Bullish close — buyers have a slight edge.")
            : CandleIntel(pattern: "bearish_candle", signal: .bearish, control: "sellers", message: "Bearish close — sellers have a slight edge.")
    }
}
