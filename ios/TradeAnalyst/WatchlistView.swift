import SwiftUI

@MainActor
final class WatchlistVM: ObservableObject {
    @Published var items: [WatchItem] = []
    @Published var loading = false

    func load() async {
        loading = true; defer { loading = false }
        do {
            var list = try await APIClient.shared.watchlist()
            // fetch live prices, compute distance to entry (parallel, deduped)
            let tickers = Set(list.map { $0.ticker })
            var prices: [String: Double] = [:]
            await withTaskGroup(of: (String, Double?).self) { group in
                for t in tickers {
                    group.addTask { (t, try? await APIClient.shared.price(t).current_price) }
                }
                for await (t, p) in group { prices[t] = p }
            }
            for i in list.indices {
                let px = prices[list[i].ticker] ?? nil
                list[i].livePrice = px
                if let px, let e = list[i].entry_price, e > 0 {
                    list[i].distancePct = (px - e) / e * 100
                }
            }
            // nearest-to-entry first
            items = list.sorted { abs($0.distancePct ?? 999) < abs($1.distancePct ?? 999) }
        } catch { items = [] }
    }

    func remove(_ id: Int) async {
        try? await APIClient.shared.removeFromWatchlist(id: id)
        await load()
    }
}

struct WatchlistView: View {
    @StateObject private var vm = WatchlistVM()

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 10) {
                    if vm.items.isEmpty && !vm.loading {
                        Text("Nothing on watch yet.\nStar an analysis to add one.")
                            .multilineTextAlignment(.center)
                            .foregroundStyle(Theme.inkSoft).italic()
                            .padding(.top, 40)
                    }
                    ForEach(vm.items) { item in
                        WatchCard(item: item) { Task { await vm.remove(item.id) } }
                    }
                }
                .padding(16)
            }
            .background(Theme.paper.ignoresSafeArea())
            .navigationTitle("⭐ Watchlist")
            .refreshable { await vm.load() }
        }
        .task { await vm.load() }
    }
}

struct WatchCard: View {
    let item: WatchItem
    let onRemove: () -> Void

    private var inZone: Bool { if let d = item.distancePct { return d >= 0 && d <= 2 }; return false }
    private var closeness: Double {
        guard let d = item.distancePct else { return 0 }
        return max(0, min(1, 1 - abs(d) / 10))
    }
    private var distText: String {
        guard let d = item.distancePct else { return "—" }
        if inZone { return "IN ENTRY ZONE" }
        return "\(d > 0 ? "+" : "")\(String(format: "%.1f", d))% to entry"
    }
    private var distColor: Color {
        if inZone { return Theme.bull }
        if let d = item.distancePct, abs(d) <= 5 { return Theme.warn }
        return Theme.inkSoft
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(item.ticker).font(.headline.bold())
                if let px = item.livePrice { Text("$\(px, specifier: "%.2f")").font(.caption).foregroundStyle(Theme.inkSoft) }
                Spacer()
                Text(distText).font(.caption.weight(.bold)).foregroundStyle(distColor)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.black.opacity(0.06)).frame(height: 4)
                    Capsule().fill(inZone ? Theme.bull : Theme.warn)
                        .frame(width: geo.size.width * closeness, height: 4)
                }
            }.frame(height: 4)
            HStack(spacing: 6) {
                chip("Entry", item.entry_price, Theme.warn)
                chip("Stop",  item.stop_loss,  Theme.bear)
                chip("Target", item.target,    Theme.bull)
            }
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading).card()
        .swipeActions { Button("Remove", role: .destructive, action: onRemove) }
        .contextMenu { Button("Remove", role: .destructive, action: onRemove) }
    }
    private func chip(_ label: String, _ v: Double?, _ color: Color) -> some View {
        VStack(spacing: 2) {
            Text(label).font(.caption2).foregroundStyle(Theme.inkSoft)
            Text(v != nil ? "$\(v!, specifier: "%.0f")" : "—").font(.caption.weight(.semibold)).foregroundStyle(color)
        }.frame(maxWidth: .infinity).padding(.vertical, 5).background(Color.black.opacity(0.03)).clipShape(RoundedRectangle(cornerRadius: 6))
    }
}
