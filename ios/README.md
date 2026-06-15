# Trade Analyst — iOS (SwiftUI)

Native iOS client for the McAllen Trade Analyst backend. The FastAPI backend is unchanged —
this app talks to the same endpoints (`/analyze`, `/price`, `/history`, `/watchlist`, `/trades`, `/pnl`).

## Prerequisites

1. **Xcode 16+** — install from the Mac App Store (~7 GB). Required to build any iOS app.
2. **Apple Developer account** — free tier works for running on your own iPhone;
   $99/yr needed for the App Store + push notifications.

## Run it

1. Open `TradeAnalyst.xcodeproj` in Xcode.
2. Pick a simulator (e.g. iPhone 16) or your connected iPhone.
3. Press ⌘R.

The project uses Xcode 16 **synchronized folders** — every file in `TradeAnalyst/` is
included automatically, no manual target membership needed.

## Backend

- **Production (default):** `https://allentrade.com` — the Railway deployment.
  The app uses this out of the box; nothing to configure.
- **Local dev override:** Allen tab → ⚙️ Settings → Backend → enter
  `http://192.168.1.x:8000` (your Mac's LAN IP, `ipconfig getifaddr en0`) to hit a
  local backend instead. Clear the field to fall back to production.

## Screens

| Tab | What it does |
|---|---|
| **Analyze** | Ticker search → live price card → streaming McAllen analysis → verdict, trade plan, 1% position size, "I took this trade" |
| **Watch** | Watchlist sorted nearest-to-entry, live distance bars, entry/stop/target chips |
| **P&L** | Monthly P&L vs the +40% goal, win rate, by-setup ranking, open/closed trades |
| **Allen** | Animated companion (8 hand-drawn poses: blink, talk, think, alert) |

## Still to wire (backend side)

- **Push**: `PushManager` registers the device and POSTs the token to `/push/register`
  (endpoint not yet built). The watchlist agent would send an APNs push instead of
  (or alongside) Slack. Needs the paid developer account + an APNs key.
- **Charts**: the analysis screen shows price stats; a candlestick chart view (Swift Charts)
  is the natural next addition.

## Regenerating Allen's poses

Pose images live in `TradeAnalyst/Assets.xcassets/pose-*.imageset/`. They're generated from
`frontend/pose-*.webp` via the project's `scripts/cutout.swift` + a PIL resize. Re-run that
pipeline if you update her artwork.
