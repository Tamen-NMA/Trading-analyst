import Foundation
import UserNotifications
import UIKit

/// Registers for APNs so the watchlist agent can push native entry/stop alerts.
/// Backend wiring (sending the push) is a follow-up; this is the device side.
final class PushManager: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    static let shared = PushManager()

    func requestAuthorization() {
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted else { return }
            DispatchQueue.main.async { UIApplication.shared.registerForRemoteNotifications() }
        }
    }

    /// Called from AppDelegate; send this token to your backend to target this device.
    func didRegister(_ deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { try? await registerToken(token) }
    }

    private func registerToken(_ token: String) async throws {
        guard let url = URL(string: APIClient.baseURL + "/push/register") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["token": token, "platform": "ios"])
        _ = try? await URLSession.shared.data(for: req)
    }

    // show alerts even while the app is foregrounded
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions { [.banner, .sound] }
}
