import SwiftUI
import Charts

/// Candlestick chart with MA overlays and tap-to-inspect, built on Swift Charts.
struct CandleChartView: View {
    let candles: [Candle]
    let ma50: Double?
    let ma200: Double?

    @State private var selected: Candle?

    private var priceRange: ClosedRange<Double> {
        let lows  = candles.map(\.low)
        let highs = candles.map(\.high)
        guard let lo = lows.min(), let hi = highs.max() else { return 0...1 }
        let pad = (hi - lo) * 0.06
        return (lo - pad)...(hi + pad)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Last \(candles.count) days").font(.subheadline.bold()).foregroundStyle(Theme.ink)
                Spacer()
                legend
            }

            Chart {
                ForEach(candles) { c in
                    // wick (high–low)
                    RuleMark(
                        x: .value("Date", c.date),
                        yStart: .value("Low", c.low),
                        yEnd: .value("High", c.high)
                    )
                    .foregroundStyle(color(c).opacity(0.55))
                    .lineStyle(StrokeStyle(lineWidth: 1.4))

                    // body (open–close) as a thicker bar
                    RuleMark(
                        x: .value("Date", c.date),
                        yStart: .value("Open", c.open),
                        yEnd: .value("Close", c.close)
                    )
                    .foregroundStyle(color(c))
                    .lineStyle(StrokeStyle(lineWidth: 6, lineCap: .round))
                }

                if let ma50 {
                    RuleMark(y: .value("MA50", ma50))
                        .foregroundStyle(Theme.sky.opacity(0.7))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                        .annotation(position: .top, alignment: .leading) {
                            Text("MA50").font(.system(size: 8)).foregroundStyle(Theme.sky)
                        }
                }
                if let ma200 {
                    RuleMark(y: .value("MA200", ma200))
                        .foregroundStyle(Theme.gold.opacity(0.8))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                        .annotation(position: .bottom, alignment: .leading) {
                            Text("MA200").font(.system(size: 8)).foregroundStyle(Theme.gold)
                        }
                }

                if let sel = selected {
                    RuleMark(x: .value("Sel", sel.date))
                        .foregroundStyle(Theme.ink.opacity(0.15))
                }
            }
            .chartYScale(domain: priceRange)
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 4)) { value in
                    AxisGridLine().foregroundStyle(Theme.line.opacity(0.4))
                    AxisValueLabel {
                        if let d = value.as(String.self) { Text(shortDate(d)).font(.system(size: 9)) }
                    }
                }
            }
            .chartYAxis {
                AxisMarks(position: .trailing) { value in
                    AxisGridLine().foregroundStyle(Theme.line.opacity(0.4))
                    AxisValueLabel { if let p = value.as(Double.self) { Text("\(Int(p))").font(.system(size: 9)) } }
                }
            }
            .chartOverlay { proxy in
                GeometryReader { geo in
                    Rectangle().fill(.clear).contentShape(Rectangle())
                        .gesture(DragGesture(minimumDistance: 0)
                            .onChanged { v in
                                let x = v.location.x - geo[proxy.plotAreaFrame].origin.x
                                if let date: String = proxy.value(atX: x),
                                   let hit = candles.first(where: { $0.date == date }) {
                                    selected = hit
                                }
                            })
                }
            }
            .frame(height: 240)

            if let s = selected { candleIntel(s) }
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading).card()
    }

    private var legend: some View {
        HStack(spacing: 10) {
            Circle().fill(Theme.bull).frame(width: 7, height: 7); Text("up").font(.caption2).foregroundStyle(Theme.inkSoft)
            Circle().fill(Theme.bear).frame(width: 7, height: 7); Text("down").font(.caption2).foregroundStyle(Theme.inkSoft)
        }
    }

    private func candleIntel(_ c: Candle) -> some View {
        let bullish = c.close >= c.open
        return HStack(spacing: 12) {
            Text(shortDate(c.date)).font(.caption.bold())
            Text("O \(c.open, specifier: "%.2f")").font(.caption2).foregroundStyle(Theme.inkSoft)
            Text("H \(c.high, specifier: "%.2f")").font(.caption2).foregroundStyle(Theme.inkSoft)
            Text("L \(c.low, specifier: "%.2f")").font(.caption2).foregroundStyle(Theme.inkSoft)
            Text("C \(c.close, specifier: "%.2f")").font(.caption2.weight(.semibold))
                .foregroundStyle(bullish ? Theme.bull : Theme.bear)
        }
        .padding(8).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.black.opacity(0.03)).clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func color(_ c: Candle) -> Color { c.close >= c.open ? Theme.bull : Theme.bear }
    private func shortDate(_ d: String) -> String {
        // "2026-06-12" → "Jun 12"
        let parts = d.split(separator: "-")
        guard parts.count == 3, let m = Int(parts[1]) else { return d }
        let months = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        return "\(months[m]) \(parts[2])"
    }
}
