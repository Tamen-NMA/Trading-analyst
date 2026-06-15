# App Store Deployment — Trade Analyst

## Step 0 — App must launch cleanly first

App Review runs the app on a device. If it crashes on launch (the SIGKILL you saw),
it gets rejected. Fix that before submitting (see "Fixing the launch crash" at the bottom).

## Prerequisites

- **Paid Apple Developer Program** membership ($99/yr). The free tier can run on your
  own device but **cannot** submit to the App Store. (You already have a paid account —
  the APNs key requires it.)
- Backend live and public: ✅ `https://allentrade.com`

## 1. Set version & build number

In Xcode → target **TradeAnalyst** → **General**:
- **Version**: `1.0` (the public version users see)
- **Build**: `1` (increment every upload)

## 2. Create the app record in App Store Connect

1. Go to [appstoreconnect.apple.com](https://appstoreconnect.apple.com) → **My Apps** → **+** → **New App**
2. Platform: iOS · Name: **Trade Analyst** (must be unique across the App Store)
3. Bundle ID: select `com.mcallen.tradeanalyst`
4. SKU: any unique string, e.g. `tradeanalyst-001`
5. Create

## 3. Archive the build

1. In Xcode, set the run destination to **Any iOS Device (arm64)** — NOT a simulator
   (you can't archive for the App Store from a simulator target)
2. **Product → Archive**
3. When it finishes, the **Organizer** window opens with your archive

## 4. Upload to App Store Connect

1. In Organizer, select the archive → **Distribute App**
2. Choose **App Store Connect** → **Upload**
3. Keep the defaults (automatic signing, include symbols) → **Upload**
4. Wait — it processes for 5–15 min, then appears under TestFlight in App Store Connect

## 5. TestFlight (test the real build before going public)

1. App Store Connect → your app → **TestFlight**
2. Add yourself as an internal tester
3. Install via the TestFlight app on your iPhone
4. **This is where you flip push to production:** set `APNS_USE_SANDBOX=0` on Railway,
   because TestFlight/App Store builds use the production APNs servers

## 6. Fill in the App Store listing

App Store Connect → your app → the version → complete:
- **Screenshots** — required sizes: 6.9" (iPhone 16 Pro Max) and 6.5". Take them on a
  simulator: run the app, ⌘S in the simulator saves a screenshot.
- **Description**, keywords, support URL, marketing URL
- **App Privacy** — declare what you collect (you collect ticker searches + trade logs;
  no third-party tracking). Fill the privacy questionnaire honestly.
- **Category**: Finance
- **Age rating** questionnaire
- ⚠️ **Financial disclaimer**: Apple scrutinises finance/trading apps. In the description
  and ideally in-app, state clearly: "For educational/informational purposes only. Not
  financial advice." The app already frames outputs as analysis — keep that.

## 7. Submit for review

1. Attach the TestFlight build to the version
2. **Submit for Review**
3. Review takes ~24–48h typically. They may reject with questions — common ones for
   finance apps: prove you're authorised to show this data, add a disclaimer, clarify
   what the AI does. Respond in Resolution Center and resubmit.

## 8. Release

Once approved → **Release** (manually or auto). Live on the App Store within a few hours.

---

## Fixing the launch crash (SIGKILL on launch)

SIGKILL before your code runs = signing/entitlement mismatch. In order:

1. **Signing & Capabilities** (target → tab): "Automatically manage signing" ON,
   **Team** set, no red errors.
2. If **Push Notifications** capability shows a red error: the provisioning profile
   doesn't include it. Toggle auto-signing off then on to regenerate, or remove the
   capability, run, then re-add.
3. **Product → Clean Build Folder** (⌘⇧K), then run again.
4. Read the actual reason: **⌘⇧Y** opens the console; the last red line names the cause.

If push signing keeps blocking launch during development, temporarily remove the Push
Notifications capability to confirm the rest of the app launches — push can be re-added
right before the TestFlight build (it only matters for production push anyway).
