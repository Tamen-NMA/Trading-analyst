import SwiftUI

@MainActor
final class PnLVM: ObservableObject {
    @Published var summary: PnLSummary?
    @Published var loading = false

    func load() async {
        loading = true; defer { loading = false }
        summary = try? await APIClient.shared.pnl()
    }
    func close(_ trade: Trade, exit: Double) async {
        try? await APIClient.shared.closeTrade(id: trade.id, exit: exit)
        await load()
    }
}

struct PnLView: View {
    @StateObject private var vm = PnLVM()
    @State private var closing: Trade?
    @State private var exitText = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                if let s = vm.summary, (s.closed_trades > 0 || s.open_trades > 0) {
                    VStack(spacing: 14) {
                        hero(s)
                        statGrid(s)
                        if !s.by_setup.isEmpty { bySetup(s) }
                        if !s.open.isEmpty { openTrades(s) }
                        if !s.recent_closed.isEmpty { recentClosed(s) }
                    }.padding(16)
                } else if !vm.loading {
                    Text("No trades logged yet.\nTap “I took this trade” on an analysis,\nor tell Allen: “I bought NVDA at 892”.")
                        .multilineTextAlignment(.center).foregroundStyle(Theme.inkSoft).italic()
                        .padding(.top, 60)
                }
            }
            .background(Theme.paper.ignoresSafeArea())
            .navigationTitle("P&L")
            .refreshable { await vm.load() }
        }
        .task { await vm.load() }
        .alert("Close trade", isPresented: Binding(get: { closing != nil }, set: { if !$0 { closing = nil } })) {
            TextField("Exit price", text: $exitText).keyboardType(.decimalPad)
            Button("Cancel", role: .cancel) { closing = nil }
            Button("Close") {
                if let t = closing, let v = Double(exitText) { Task { await vm.close(t, exit: v) } }
                closing = nil; exitText = ""
            }
        }
    }

    private func hero(_ s: PnLSummary) -> some View {
        let progress = max(0, min(1, s.month_pnl_pct_of_account / s.month_goal_pct))
        return VStack(spacing: 6) {
            Text("THIS MONTH").font(.caption2).tracking(1).foregroundStyle(Theme.inkSoft)
            Text("\(s.month_pnl >= 0 ? "+" : "")$\(abs(s.month_pnl), specifier: "%.0f")")
                .font(.system(size: 34, weight: .bold))
                .foregroundStyle(s.month_pnl >= 0 ? Theme.bull : Theme.bear)
            Text("\(s.month_pnl_pct_of_account, specifier: "%.1f")% of account · goal +\(Int(s.month_goal_pct))%")
                .font(.caption).foregroundStyle(Theme.inkSoft)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.black.opacity(0.06)).frame(height: 5)
                    Capsule().fill(LinearGradient(colors: [Theme.warn, Theme.bull], startPoint: .leading, endPoint: .trailing))
                        .frame(width: geo.size.width * progress, height: 5)
                }
            }.frame(height: 5).padding(.top, 4)
        }
        .padding(18).frame(maxWidth: .infinity).card()
    }

    private func statGrid(_ s: PnLSummary) -> some View {
        let cols = [GridItem(.flexible()), GridItem(.flexible())]
        return LazyVGrid(columns: cols, spacing: 8) {
            statBox("Win rate", s.win_rate != nil ? String(format: "%.0f%%", s.win_rate!) : "—", Theme.ink)
            statBox("Closed / Open", "\(s.closed_trades) / \(s.open_trades)", Theme.ink)
            statBox("Avg win", s.avg_win_pct != nil ? String(format: "+%.1f%%", s.avg_win_pct!) : "—", Theme.bull)
            statBox("Avg loss", s.avg_loss_pct != nil ? String(format: "%.1f%%", s.avg_loss_pct!) : "—", Theme.bear)
        }
    }
    private func statBox(_ label: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption2).foregroundStyle(Theme.inkSoft)
            Text(value).font(.title3.weight(.bold)).foregroundStyle(color)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(12).card()
    }

    private func bySetup(_ s: PnLSummary) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("BY SETUP — TRADE YOUR WINNERS").font(.caption2.weight(.bold)).foregroundStyle(Theme.sky)
            ForEach(s.by_setup.sorted { $0.value.avg_pnl_pct > $1.value.avg_pnl_pct }, id: \.key) { name, d in
                HStack {
                    Text(name.replacingOccurrences(of: "_", with: " ")).font(.caption.weight(.medium))
                    Spacer()
                    Text("\(d.trades) · \(d.win_rate, specifier: "%.0f")% win").font(.caption2).foregroundStyle(Theme.inkSoft)
                    Text("\(d.avg_pnl_pct >= 0 ? "+" : "")\(d.avg_pnl_pct, specifier: "%.1f")%")
                        .font(.caption.weight(.bold)).foregroundStyle(d.avg_pnl_pct >= 0 ? Theme.bull : Theme.bear)
                }.padding(.vertical, 8).padding(.horizontal, 12).card()
            }
        }
    }

    private func openTrades(_ s: PnLSummary) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("OPEN POSITIONS").font(.caption2.weight(.bold)).foregroundStyle(Theme.sky)
            ForEach(s.open) { t in
                HStack {
                    Text(t.ticker).font(.caption.bold())
                    Text("in @ $\(t.entry_price, specifier: "%.2f")").font(.caption2).foregroundStyle(Theme.inkSoft)
                    Spacer()
                    Button("Close") { closing = t }
                        .font(.caption.weight(.bold)).foregroundStyle(Theme.bear)
                        .padding(.vertical, 4).padding(.horizontal, 10)
                        .background(Theme.bear.opacity(0.12)).clipShape(Capsule())
                }.padding(.vertical, 8).padding(.horizontal, 12).card()
            }
        }
    }

    private func recentClosed(_ s: PnLSummary) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("RECENT CLOSED").font(.caption2.weight(.bold)).foregroundStyle(Theme.sky)
            ForEach(s.recent_closed) { t in
                HStack {
                    Text(t.ticker).font(.caption.bold())
                    Text("$\(t.entry_price, specifier: "%.2f") → $\(t.exit_price ?? 0, specifier: "%.2f")")
                        .font(.caption2).foregroundStyle(Theme.inkSoft)
                    Spacer()
                    if let pct = t.pnl_pct {
                        Text("\(pct >= 0 ? "+" : "")\(pct, specifier: "%.1f")%")
                            .font(.caption.weight(.bold)).foregroundStyle(pct >= 0 ? Theme.bull : Theme.bear)
                    }
                }.padding(.vertical, 8).padding(.horizontal, 12).card()
            }
        }
    }
}
