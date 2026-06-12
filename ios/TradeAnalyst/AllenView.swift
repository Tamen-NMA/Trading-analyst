import SwiftUI

/// Allen's states drive both her pose frame and her status line.
enum AllenState: String {
    case idle, listening, thinking, speaking, alert
    var label: String {
        switch self {
        case .idle:      return "Resting among the flowers…"
        case .listening: return "I’m all ears! 🌸"
        case .thinking:  return "Hmm, let me check the charts…"
        case .speaking:  return "Here’s what I found!"
        case .alert:     return "Heads up — this needs you!"
        }
    }
}

@MainActor
final class AllenVM: ObservableObject {
    @Published var state: AllenState = .idle
    @Published var pose: String = "pose-neutral"
    private var task: Task<Void, Never>?

    func set(_ s: AllenState) {
        state = s
        startLoop()
    }

    /// Drives blink / talk / ponder frame swapping, mirroring the web pose engine.
    private func startLoop() {
        task?.cancel()
        let s = state
        task = Task { [weak self] in
            guard let self else { return }
            switch s {
            case .idle, .listening:
                let base = s == .idle ? "pose-neutral" : "pose-hug"
                await MainActor.run { self.pose = base }
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(Double.random(in: 3...6)))
                    if Task.isCancelled { break }
                    await MainActor.run { self.pose = "pose-blink" }
                    try? await Task.sleep(for: .milliseconds(160))
                    await MainActor.run { self.pose = base }
                }
            case .thinking:
                var n = 0
                while !Task.isCancelled {
                    await MainActor.run { self.pose = n % 2 == 0 ? "pose-think" : "pose-scratch" }
                    n += 1
                    try? await Task.sleep(for: .seconds(3.5))
                }
            case .speaking:
                var open = true
                while !Task.isCancelled {
                    await MainActor.run { self.pose = open ? "pose-talk" : "pose-smile" }
                    open.toggle()
                    try? await Task.sleep(for: .milliseconds(Int.random(in: 220...360)))
                }
            case .alert:
                await MainActor.run { self.pose = "pose-angry" }
            }
        }
    }
}

struct AllenView: View {
    @StateObject private var vm = AllenVM()
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            ZStack {
                LinearGradient(colors: [Color(hex: 0xFBF6E8), Theme.paper, Color(hex: 0xF1E6CB)],
                               startPoint: .top, endPoint: .bottom).ignoresSafeArea()
                VStack(spacing: 18) {
                    Spacer()
                    ZStack {
                        Circle().fill(Theme.blossom.opacity(0.25))
                            .frame(width: 260, height: 260).blur(radius: 30)
                        Image(vm.pose)
                            .resizable().scaledToFit()
                            .frame(height: 320)
                            .shadow(color: .black.opacity(0.18), radius: 12, y: 8)
                            .animation(.easeOut(duration: 0.12), value: vm.pose)
                    }
                    Text(vm.state.label)
                        .font(.system(size: 16)).italic().foregroundStyle(Theme.inkSoft)
                        .padding(.vertical, 8).padding(.horizontal, 20).card()
                    Spacer()
                    // demo controls — in production these are driven by the voice agent / push
                    HStack(spacing: 8) {
                        ForEach([AllenState.idle, .listening, .thinking, .speaking, .alert], id: \.rawValue) { s in
                            Button(s.rawValue) { vm.set(s) }
                                .font(.caption2.weight(.semibold))
                                .padding(.vertical, 6).padding(.horizontal, 10)
                                .background(vm.state == s ? Theme.terra.opacity(0.18) : Theme.card)
                                .clipShape(Capsule())
                        }
                    }.padding(.bottom, 8)
                }.padding(16)
            }
            .navigationTitle("Allen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                }
            }
            .sheet(isPresented: $showSettings) { SettingsView() }
        }
    }
}

struct SettingsView: View {
    @AppStorage("backendURL") private var backendURL = "http://localhost:8000"
    @AppStorage("accountSize") private var accountSize: Double = 25000
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Backend") {
                    TextField("http://192.168.1.x:8000", text: $backendURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Text("Use your Mac’s LAN IP so the phone can reach the backend on the same Wi-Fi.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Risk") {
                    HStack {
                        Text("Account size")
                        Spacer()
                        TextField("25000", value: $accountSize, format: .number)
                            .keyboardType(.decimalPad).multilineTextAlignment(.trailing)
                    }
                    Text("Position sizing risks 1% of this per trade.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }
}
