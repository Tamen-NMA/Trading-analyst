import SwiftUI
import UIKit

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        PushManager.shared.requestAuthorization()
        return true
    }
    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        PushManager.shared.didRegister(deviceToken)
    }
}

@main
struct TradeAnalystApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    var body: some Scene {
        WindowGroup {
            RootView()
                .tint(Theme.terra)
                .preferredColorScheme(.light)   // Fable light by default
        }
    }
}

struct RootView: View {
    enum Tab { case analyze, watch, pnl, allen }
    @State private var tab: Tab = .analyze

    var body: some View {
        TabView(selection: $tab) {
            AnalysisView()
                .tabItem { Label("Analyze", systemImage: "chart.line.uptrend.xyaxis") }
                .tag(Tab.analyze)

            WatchlistView()
                .tabItem { Label("Watch", systemImage: "star.fill") }
                .tag(Tab.watch)

            PnLView()
                .tabItem { Label("P&L", systemImage: "dollarsign.circle") }
                .tag(Tab.pnl)

            AllenView()
                .tabItem { Label("Allen", systemImage: "sparkles") }
                .tag(Tab.allen)
        }
    }
}
