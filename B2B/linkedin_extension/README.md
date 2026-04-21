# BitCoding — LinkedIn Extension (v1.0, Phase 1)

Unpacked Chrome extension that pairs with the BitCoding dashboard at
`http://localhost:8900`. Replaces the legacy Google Sheet + Apps Script flow.

## Install (local dev)

1. Open `chrome://extensions`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked**
4. Select this folder (`B2B/linkedin_extension/`)
5. Pin the extension and open its side panel
6. Paste an API key from the dashboard's **LinkedIn → Settings** page
7. Click **Test** — should show `Connected · N leads`

## Phase status

- Phase 1 (now) — manifest, side panel, API-key plumbing, connection check
- Phase 2 — actual post scanning on LinkedIn search pages
- Phase 3 — reply capture in messaging view
- Phase 4 — send + Gmail OAuth (dashboard-side)
- Phase 5 — reply/bounce polling

See `Database/LinkedIn Data/PLAN.md` for the full roadmap.

## Files

| File | Purpose |
|---|---|
| `manifest.json` | MV3 manifest, host permissions locked to linkedin.com + localhost |
| `config.js` | `DASHBOARD_BASE`, `apiFetch` helper, storage keys |
| `background.js` | Service worker, message routing |
| `search-scan.js` | Content script on `/search/results/content/*` (Phase 2) |
| `content.js` | Content script on `/messaging/*` (Phase 5) |
| `feed-compose.js` | Content script on `/feed/*` (Phase 6) |
| `popup.html/js/css` | Side panel |
| `icons/` | 16/48/128 PNGs |
