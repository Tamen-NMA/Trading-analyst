import Foundation

/// Parses the structured McAllen analysis text into typed fields,
/// mirroring the regex logic in the web frontend.
struct TradePlan {
    var entry: String?
    var stop: String?
    var target: String?
    var rrRatio: String?
    var entryNum: Double?
    var stopNum: Double?
    var targetNum: Double?
}

enum McAllenParser {

    static func verdict(_ text: String) -> String? {
        match(text, #"\*\*VERDICT:\*\*\s*(BULLISH|BEARISH|NEUTRAL)"#)?.uppercased()
    }

    static func tradePlan(_ text: String) -> TradePlan {
        guard let setup = section(text, "TRADE SETUP") else { return TradePlan() }
        var p = TradePlan()
        p.entry   = lineValue(setup, "Entry trigger")
        p.stop    = lineValue(setup, "Stop loss")
        p.target  = lineValue(setup, "Target")
        p.rrRatio = match(setup, #"Risk[\s/\-]*Reward[^:]*:\s*([^\n]+)"#)?
            .replacingOccurrences(of: "*", with: "").trimmingCharacters(in: .whitespaces)
        p.entryNum  = firstPrice(p.entry)
        p.stopNum   = firstPrice(p.stop)
        p.targetNum = firstPrice(p.target)
        return p
    }

    /// 1%-risk position size given account size.
    static func positionSize(entry: Double?, stop: Double?, account: Double, riskPct: Double = 1) -> Int? {
        guard let e = entry, let s = stop, e > s else { return nil }
        return Int((account * riskPct / 100) / (e - s))
    }

    // MARK: helpers

    static func firstPrice(_ s: String?) -> Double? {
        guard let s, let m = match(s, #"\$?([\d,]+\.?\d*)"#) else { return nil }
        return Double(m.replacingOccurrences(of: ",", with: ""))
    }

    private static func lineValue(_ block: String, _ label: String) -> String? {
        match(block, label + #"[:\s]+\$?([^\n]+)"#)?
            .replacingOccurrences(of: "*", with: "")
            .trimmingCharacters(in: .whitespaces)
    }

    private static func section(_ text: String, _ name: String) -> String? {
        // grab from "**NAME:**" up to the next blank line or VERDICT
        guard let r = text.range(of: #"\*\*"# + name + #":\*\*"#, options: .regularExpression) else { return nil }
        let tail = String(text[r.upperBound...])
        if let stop = tail.range(of: #"\n\n|\*\*VERDICT"#, options: .regularExpression) {
            return String(tail[..<stop.lowerBound])
        }
        return tail
    }

    private static func match(_ text: String, _ pattern: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]),
              let m = re.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
              m.numberOfRanges > 1,
              let r = Range(m.range(at: 1), in: text) else { return nil }
        return String(text[r])
    }
}
