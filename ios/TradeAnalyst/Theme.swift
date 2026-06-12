import SwiftUI

/// Fable × McAllen palette — mirrors the web frontend so the brand is consistent.
enum Theme {
    // light (Fable parchment garden)
    static let paper      = Color(hex: 0xF6EFDD)
    static let paperDeep  = Color(hex: 0xEFE5CC)
    static let ink        = Color(hex: 0x2D2A24)
    static let inkSoft    = Color(hex: 0x6B6354)
    static let terra      = Color(hex: 0xC8553D)
    static let terraSoft  = Color(hex: 0xE07856)
    static let gold       = Color(hex: 0xD9A441)
    static let leaf       = Color(hex: 0x4A7045)
    static let leafSoft   = Color(hex: 0x7FA86B)
    static let sky        = Color(hex: 0x5E8FB5)
    static let blossom    = Color(hex: 0xE8889B)
    static let card       = Color(hex: 0xFDF9EE)
    static let line       = Color(hex: 0xD8CBAB)

    // verdict / signal colours (shared with charts)
    static let bull    = Color(hex: 0x3FB950)
    static let bear    = Color(hex: 0xF85149)
    static let neutral = Color(hex: 0x8B949E)
    static let warn    = Color(hex: 0xD29922)

    static func verdictColor(_ v: String?) -> Color {
        switch (v ?? "").uppercased() {
        case "BULLISH": return bull
        case "BEARISH": return bear
        default:        return neutral
        }
    }
}

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(
            .sRGB,
            red:   Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue:  Double(hex & 0xFF) / 255,
            opacity: alpha
        )
    }
}

/// Reusable card surface matching the web `.card` look.
struct CardBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .background(Theme.card)
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.line, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}

extension View {
    func card() -> some View { modifier(CardBackground()) }
}
