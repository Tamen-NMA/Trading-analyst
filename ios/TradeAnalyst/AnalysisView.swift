import SwiftUI

@MainActor
final class AnalysisVM: ObservableObject {
    @Published var ticker = ""
    @Published var price: PriceData?
    @Published var text = ""
    @Published var streaming = false
    @Published var error: String?

    func run() async {
        let t = ticker.uppercased().trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        text = ""; price = nil; error = nil; streaming = true
        defer { streaming = false }
        do {
            try await APIClient.shared.streamAnalysis(
                ticker: t,
                onMeta: { [weak self] pd in Task { @MainActor in self?.price = pd } },
                onText: { [weak self] chunk in Task { @MainActor in self?.text += chunk } }
            )
        } catch {
            self.error = error.localizedDescription
        }
    }

    func loadPriceOnly() async {
        let t = ticker.uppercased().trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        error = nil
        do { price = try await APIClient.shared.price(t) }
        catch { self.error = error.localizedDescription }
    }
}

struct AnalysisView: View {
    @StateObject private var vm = AnalysisVM()

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.paper.ignoresSafeArea()
                ScrollView {
                    VStack(spacing: 16) {
                        searchBar
                        if let p = vm.price { PriceCard(price: p) }
                        if let p = vm.price, let candles = p.last_30_candles, !candles.isEmpty {
                            CandleChartView(candles: candles, ma50: p.ma50, ma200: p.ma200)
                        }
                        if let e = vm.error { errorBox(e) }
                        if !vm.text.isEmpty { analysisBody }
                        if vm.streaming && vm.text.isEmpty { loading }
                    }
                    .padding(16)
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("McAllen Analyst")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private var searchBar: some View {
        HStack(spacing: 10) {
            TextField("Ticker (e.g. NVDA)", text: $vm.ticker)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                .padding(.vertical, 11).padding(.horizontal, 14)
                .card()
                .onSubmit { Task { await vm.loadPriceOnly() } }

            Button {
                Task { await vm.run() }
            } label: {
                Text(vm.streaming ? "…" : "Analyze")
                    .fontWeight(.semibold)
                    .foregroundStyle(.white)
                    .padding(.vertical, 11).padding(.horizontal, 18)
                    .background(Theme.terra)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(vm.streaming)
        }
    }

    private var analysisBody: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let v = McAllenParser.verdict(vm.text) {
                VerdictBadge(verdict: v)
            }
            let plan = McAllenParser.tradePlan(vm.text)
            if plan.entry != nil || plan.stop != nil {
                TradePlanCard(plan: plan, ticker: vm.ticker.uppercased())
            }
            Text(cleanMarkdown(vm.text))
                .font(.system(size: 15))
                .foregroundStyle(Theme.ink)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
                .card()
        }
    }

    private var loading: some View {
        HStack(spacing: 10) {
            ProgressView()
            Text("Searching web & analysing…").foregroundStyle(Theme.inkSoft).italic()
        }.padding(24)
    }

    private func errorBox(_ e: String) -> some View {
        Text(e).foregroundStyle(Theme.bear).font(.footnote)
            .padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.bear.opacity(0.1)).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func cleanMarkdown(_ s: String) -> String {
        s.replacingOccurrences(of: "**", with: "")
         .replacingOccurrences(of: "##", with: "")
    }
}

struct VerdictBadge: View {
    let verdict: String
    var body: some View {
        Text(verdict)
            .font(.headline.weight(.bold))
            .foregroundStyle(.white)
            .padding(.vertical, 8).padding(.horizontal, 18)
            .frame(maxWidth: .infinity)
            .background(Theme.verdictColor(verdict))
            .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct PriceCard: View {
    let price: PriceData
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(price.ticker).font(.title3.bold())
                Spacer()
                if let p = price.current_price {
                    Text("$\(p, specifier: "%.2f")").font(.title3.weight(.semibold))
                }
                if let c = price.daily_change_pct {
                    Text("\(c >= 0 ? "+" : "")\(c, specifier: "%.2f")%")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(c >= 0 ? Theme.bull : Theme.bear)
                }
            }
            HStack(spacing: 14) {
                stat("52W H", price.week52_high)
                stat("52W L", price.week52_low)
                stat("MA200", price.ma200)
                if let r = price.volume_ratio {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Vol").font(.caption2).foregroundStyle(Theme.inkSoft)
                        Text(r).font(.caption.weight(.semibold))
                    }
                }
            }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading).card()
    }
    private func stat(_ label: String, _ v: Double?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).foregroundStyle(Theme.inkSoft)
            Text(v != nil ? "$\(v!, specifier: "%.0f")" : "—").font(.caption.weight(.semibold))
        }
    }
}

struct TradePlanCard: View {
    let plan: TradePlan
    let ticker: String
    @AppStorage("accountSize") private var accountSize: Double = 25000
    @State private var logged = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("⚡ Trade Plan").font(.subheadline.bold()).foregroundStyle(Theme.terra)
            row("🎯 Entry", plan.entry, Theme.warn)
            row("🛑 Stop",  plan.stop,  Theme.bear)
            row("🎯 Target", plan.target, Theme.bull)
            if let rr = plan.rrRatio { row("⚖️ R/R", rr, Theme.sky) }

            if let shares = McAllenParser.positionSize(entry: plan.entryNum, stop: plan.stopNum, account: accountSize) {
                Divider()
                Text("🧮 \(shares) shares · max loss $\(Int(accountSize * 0.01)) at stop (1% risk)")
                    .font(.caption).foregroundStyle(Theme.inkSoft)
                Button {
                    Task {
                        try? await APIClient.shared.logTrade(
                            ticker: ticker, entry: plan.entryNum ?? 0, shares: Double(shares),
                            stop: plan.stopNum, target: plan.targetNum, setup: "mcallen_analysis")
                        logged = true
                    }
                } label: {
                    Text(logged ? "✓ Trade logged" : "I took this trade")
                        .fontWeight(.semibold).frame(maxWidth: .infinity)
                        .padding(.vertical, 11)
                        .background(Theme.bull.opacity(logged ? 0.25 : 0.14))
                        .foregroundStyle(Theme.bull)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .disabled(logged)
            }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading).card()
    }
    private func row(_ label: String, _ value: String?, _ color: Color) -> some View {
        HStack {
            Text(label).font(.caption.weight(.semibold)).foregroundStyle(color)
            Spacer()
            Text(value ?? "—").font(.caption.weight(.medium)).foregroundStyle(Theme.ink)
        }
    }
}
