# Deployment — Cloudflare Tunnel + Vercel (bitcodingsolutions.com)

Goal: run the app from anywhere. PC stays the server (SQLite DB + Claude
Bridge stay local). Cost: $0 after existing domain.

## Target URLs

- `b2b.bitcodingsolutions.com`  → Vercel-hosted Next.js frontend
- `api.bitcodingsolutions.com`  → Cloudflare Tunnel → `localhost:8900` (backend)

## Phase 1 — Move domain DNS to Cloudflare (one-time)

1. Sign in at https://dash.cloudflare.com (create free account).
2. **Add a Site** → enter `bitcodingsolutions.com` → pick **Free** plan.
3. Cloudflare scans existing DNS. Review records (keep MX for email,
   keep any A/CNAME for existing pages). Continue.
4. Cloudflare gives you **2 nameservers** like `xyz.ns.cloudflare.com`.
5. Go to your domain registrar (wherever you bought bitcodingsolutions.com)
   → DNS / nameservers → replace existing nameservers with the 2 from
   Cloudflare → save.
6. Wait 5 min – 2 hours for propagation. Cloudflare dashboard shows
   **Active** when done.

## Phase 2 — Cloudflare Tunnel for backend

### 2a. Install `cloudflared` on Windows

```powershell
winget install --id Cloudflare.cloudflared
# or download MSI from https://github.com/cloudflare/cloudflared/releases/latest
```

Verify:
```powershell
cloudflared --version
```

### 2b. Log in and create tunnel

```powershell
cloudflared login
```
Opens browser → pick bitcodingsolutions.com. Cert saved to
`%USERPROFILE%\.cloudflared\cert.pem`.

```powershell
cloudflared tunnel create b2b-backend
```
This prints a tunnel ID and writes credentials JSON to
`%USERPROFILE%\.cloudflared\<tunnel-id>.json`. Note the tunnel ID.

### 2c. Write tunnel config

Create `%USERPROFILE%\.cloudflared\config.yml`:

```yaml
tunnel: b2b-backend
credentials-file: C:\Users\Pradip Kachhadiya\.cloudflared\<TUNNEL-ID>.json

ingress:
  - hostname: api.bitcodingsolutions.com
    service: http://localhost:8900
  - service: http_status:404
```

### 2d. Route the subdomain

```powershell
cloudflared tunnel route dns b2b-backend api.bitcodingsolutions.com
```

This adds a CNAME in Cloudflare DNS pointing `api` → the tunnel.

### 2e. Run as a Windows service (starts on boot)

```powershell
cloudflared service install
```

Tunnel now runs in the background. Verify:
```powershell
curl https://api.bitcodingsolutions.com/api/health
```
Should return `{"ok":true,...}` (assumes backend is running locally).

## Phase 3 — Update code configs

### 3a. Backend env var (persists tracking pixel URL)

Create `H:\Lead Generator\B2B\dashboard\backend\.env.local.ps1`:

```powershell
$env:LINKEDIN_TRACKING_BASE_URL = "https://api.bitcodingsolutions.com"
python -m uvicorn main:app --host 127.0.0.1 --port 8900
```

Run this instead of plain `uvicorn` from now on — the tracking pixel URL
injected into every new outgoing email will point to the public endpoint.

### 3b. Chrome extension

In the extension popup → ⚙ Settings → "🌐 Backend API base" →
`https://api.bitcodingsolutions.com` → Save config.

### 3c. Frontend — deploy to Vercel

1. https://vercel.com/new → Import from GitHub →
   `pradipkachhadiya123/b2b_leads_generator`.
2. **Root Directory**: `dashboard/frontend`
3. **Framework Preset**: Next.js (auto-detected)
4. **Build Command**: leave default (`next build`)
5. **Environment Variables**:
   - `NEXT_PUBLIC_API_URL` = `https://api.bitcodingsolutions.com`
6. Deploy. Vercel gives you a `*.vercel.app` URL.

### 3d. Map custom domain to Vercel

1. Vercel project → Settings → Domains → Add →
   `b2b.bitcodingsolutions.com`.
2. Vercel shows a CNAME record to add. In Cloudflare DNS:
   - Type: CNAME
   - Name: `b2b`
   - Target: `cname.vercel-dns.com`
   - Proxy status: **DNS only** (grey cloud, not orange — Vercel handles
     its own TLS).
3. Wait 1-2 min. Vercel verifies → https://b2b.bitcodingsolutions.com
   live.

## Phase 4 — Smoke test

1. Open `https://b2b.bitcodingsolutions.com` on your phone → dashboard loads.
2. `/linkedin/leads` → table shows today's data.
3. Extension scans a LinkedIn page → Save all → leads appear in the
   dashboard (going through Cloudflare Tunnel back to your PC).
4. Send one email → check `first_opened_at` in drawer once recipient
   opens. (May take a few minutes for Gmail proxy to pre-fetch.)

## Things to keep running on your PC

- `python -m uvicorn main:app` (backend) — use the `.env.local.ps1`
  wrapper so TRACKING_BASE_URL is set.
- `python bridge/server.py` (Claude Bridge at :8765) — already runs.
- `npm run dev` (frontend local, optional — Vercel already hosts it).
- `cloudflared` — runs as a Windows service, no action needed.

## Cost & limits

- Cloudflare Tunnel: free, unlimited traffic.
- Vercel Hobby: free, 100GB bandwidth/month (way above any use here).
- Domain: already owned.
- SQLite DB: local disk, no cost.
- Claude Bridge: Claude Max subscription (already paid).
- Gmail SMTP/IMAP: free up to Gmail's per-account limits (enforced by
  our per-account daily cap + warmup).

## If something breaks

- Tunnel down: `cloudflared service uninstall` then reinstall. Check
  `Get-Service cloudflared`.
- DNS not resolving: check Cloudflare DNS tab for the CNAME; propagation
  takes up to 5 min for CNAMEs.
- 502 on api.bitcodingsolutions.com: backend not running locally. Start
  it. Tunnel will immediately start routing once backend responds.
- Extension still hitting localhost: popup → Save config, make sure the
  "Backend API base" badge shows **custom**.
