import Foundation

/// Talks to the existing FastAPI backend. No backend changes required.
actor APIClient {
    static let shared = APIClient()

    /// Set this to your deployed backend, or a LAN IP while developing.
    /// e.g. "http://192.168.1.20:8000" so the phone can reach your Mac.
    nonisolated static var baseURL: String {
        UserDefaults.standard.string(forKey: "backendURL") ?? "http://localhost:8000"
    }

    private var base: URL { URL(string: APIClient.baseURL)! }
    private let session = URLSession(configuration: .default)
    private let decoder = JSONDecoder()

    // MARK: GET helpers

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let (data, resp) = try await session.data(from: base.appendingPathComponent(path))
        try Self.check(resp, data)
        return try decoder.decode(T.self, from: data)
    }

    private static func check(_ resp: URLResponse, _ data: Data) throws {
        guard let http = resp as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"
            throw APIError.server(http.statusCode, msg)
        }
    }

    // MARK: Endpoints

    func price(_ ticker: String) async throws -> PriceData {
        try await get("price/\(ticker.uppercased())")
    }

    func history() async throws -> [AnalysisRow] {
        try await get("history")
    }

    func analysis(id: Int) async throws -> AnalysisRow {
        try await get("history/\(id)")
    }

    func watchlist() async throws -> [WatchItem] {
        try await get("watchlist")
    }

    func pnl() async throws -> PnLSummary {
        try await get("pnl")
    }

    func alerts(limit: Int = 20) async throws -> [AlertRow] {
        try await get("alerts?limit=\(limit)")
    }

    // MARK: Mutations

    func addToWatchlist(ticker: String, analysisId: Int?) async throws {
        try await post("watchlist", body: [
            "ticker": ticker,
            "analysis_id": analysisId as Any
        ])
    }

    func removeFromWatchlist(id: Int) async throws {
        var req = URLRequest(url: base.appendingPathComponent("watchlist/\(id)"))
        req.httpMethod = "DELETE"
        let (data, resp) = try await session.data(for: req)
        try Self.check(resp, data)
    }

    func logTrade(ticker: String, entry: Double, shares: Double?, stop: Double?, target: Double?, setup: String?) async throws {
        try await post("trades", body: [
            "ticker": ticker, "entry_price": entry,
            "shares": shares as Any, "stop_loss": stop as Any,
            "target": target as Any, "setup": setup as Any
        ])
    }

    func closeTrade(id: Int, exit: Double) async throws {
        try await put("trades/\(id)/close", body: ["exit_price": exit])
    }

    func explain(_ term: String) async throws -> String {
        struct R: Decodable { let explanation: String }
        let r: R = try await postDecode("explain", body: ["text": term])
        return r.explanation
    }

    // MARK: Streaming analysis (SSE)

    /// Streams the McAllen analysis. `onMeta` fires once with price data,
    /// `onText` fires repeatedly with markdown chunks.
    func streamAnalysis(
        ticker: String,
        onMeta: @escaping (PriceData) -> Void,
        onText: @escaping (String) -> Void
    ) async throws {
        let url = base.appendingPathComponent("analyze/\(ticker.uppercased())")
        let (bytes, resp) = try await session.bytes(from: url)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw APIError.server((resp as? HTTPURLResponse)?.statusCode ?? -1, "stream failed")
        }
        for try await line in bytes.lines {
            guard line.hasPrefix("data:") else { continue }
            let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
            guard let data = payload.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let type = obj["type"] as? String else { continue }
            switch type {
            case "meta":
                if let metaData = obj["data"],
                   let raw = try? JSONSerialization.data(withJSONObject: metaData),
                   let pd = try? decoder.decode(PriceData.self, from: raw) {
                    onMeta(pd)
                }
            case "text":
                if let c = obj["content"] as? String { onText(c) }
            case "error":
                throw APIError.server(-1, (obj["content"] as? String) ?? "analysis error")
            default: break
            }
        }
    }

    // MARK: private POST/PUT

    private func post(_ path: String, body: [String: Any]) async throws {
        _ = try await rawSend(path, method: "POST", body: body)
    }
    private func put(_ path: String, body: [String: Any]) async throws {
        _ = try await rawSend(path, method: "PUT", body: body)
    }
    private func postDecode<T: Decodable>(_ path: String, body: [String: Any]) async throws -> T {
        let data = try await rawSend(path, method: "POST", body: body)
        return try decoder.decode(T.self, from: data)
    }
    private func rawSend(_ path: String, method: String, body: [String: Any]) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body.compactMapValues { ($0 is NSNull) ? nil : $0 })
        let (data, resp) = try await session.data(for: req)
        try Self.check(resp, data)
        return data
    }
}

enum APIError: LocalizedError {
    case server(Int, String)
    var errorDescription: String? {
        switch self {
        case .server(let code, let msg): return "Server \(code): \(msg)"
        }
    }
}
