import Foundation

// MARK: - Price

struct Candle: Codable, Identifiable, Hashable {
    var id: String { date }
    let date: String
    let open, high, low, close: Double
    let volume: Int
}

struct PriceData: Codable {
    let ticker: String
    let current_price: Double?
    let daily_change_pct: Double?
    let week52_high: Double?
    let week52_low: Double?
    let ma20, ma50, ma200: Double?
    let price_vs_ma200: String?
    let volume_ratio: String?
    let last_30_candles: [Candle]?

    enum CodingKeys: String, CodingKey {
        case ticker, current_price, daily_change_pct
        case week52_high = "52w_high"
        case week52_low  = "52w_low"
        case ma20, ma50, ma200, price_vs_ma200, volume_ratio, last_30_candles
    }
}

// MARK: - History

struct AnalysisRow: Codable, Identifiable {
    let id: Int
    let ticker: String
    let searched_at: String
    let price: Double?
    let daily_change: Double?
    let verdict: String?
    var analysis_text: String?   // present only on detail fetch
}

// MARK: - Watchlist

struct WatchItem: Codable, Identifiable {
    let id: Int
    let ticker: String
    let entry_price: Double?
    let stop_loss: Double?
    let target: Double?
    let rr_ratio: String?
    let verdict: String?
    let analysis_id: Int?
    let created_at: String?

    /// distance to entry, filled client-side after fetching live price
    var distancePct: Double? = nil
    var livePrice: Double?   = nil

    enum CodingKeys: String, CodingKey {
        case id, ticker, entry_price, stop_loss, target, rr_ratio, verdict, analysis_id, created_at
    }
}

// MARK: - Trades & P&L

struct Trade: Codable, Identifiable {
    let id: Int
    let ticker: String
    let entry_price: Double
    let exit_price: Double?
    let shares: Double?
    let setup: String?
    let status: String
    let pnl: Double?
    let pnl_pct: Double?
    let opened_at: String?
    let closed_at: String?
}

struct SetupStat: Codable {
    let trades: Int
    let wins: Int
    let avg_pnl_pct: Double
    let win_rate: Double
}

struct PnLSummary: Codable {
    let account_size: Double
    let open_trades: Int
    let closed_trades: Int
    let win_rate: Double?
    let avg_win_pct: Double?
    let avg_loss_pct: Double?
    let total_pnl: Double
    let month_pnl: Double
    let month_pnl_pct_of_account: Double
    let month_goal_pct: Double
    let by_setup: [String: SetupStat]
    let open: [Trade]
    let recent_closed: [Trade]
}

// MARK: - Alerts

struct AlertRow: Codable, Identifiable {
    let id: Int
    let ticker: String
    let price: Double?
    let pattern: String?
    let signal: String?
    let fired_at: String?
}
