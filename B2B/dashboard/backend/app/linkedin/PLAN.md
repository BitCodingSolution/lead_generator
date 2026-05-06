# LinkedIn Integration — Full Plan

**Status:** Phase 1 scaffolding in progress (2026-04-21)
**Goal:** Replace the legacy Chrome extension + Google Sheet + Apps Script pipeline with a native, dashboard-first flow. Existing `H:/Lead Generator/LinkedIn/*` stays untouched as reference.

---

## Scope

- **New sidebar section:** "LinkedIn" (alongside Overview, Sources, Campaigns, Replies, Analytics).
- **New Chrome extension** (`B2B/linkedin_extension/`) — unpacked/local, talks only to `http://localhost:8900`.
- **New SQLite DB:** `Database/LinkedIn Data/leads.db` — fresh start, no migration from the old sheet.
- **Sending:** Gmail API from Jaydip's personal Gmail (`jaydipnakrani888@gmail.com`) via OAuth2.
- **AI:** Reuse dashboard's Claude bridge (`claude-opus-4-6`) — key never touches the browser.

## Locked decisions

| # | Topic | Value |
|---|---|---|
| 1 | Sidebar label | `LinkedIn` (icon: `Linkedin` from lucide) |
| 2 | URL prefix | `/linkedin` |
| 3 | API prefix | `/api/linkedin` |
| 4 | DB file | `Database/LinkedIn Data/leads.db` |
| 5 | Sender | `jaydipnakrani888@gmail.com` — **Gmail SMTP + App Password** (no GCP / OAuth). Replies/bounces via IMAP polling. Credentials encrypted at rest. |
| 6 | Claude model | `claude-opus-4-6` (via backend bridge) |
| 7 | Extension distribution | Unpacked (local dev) |
| 8 | Data migration | None — fresh start |
| 9 | Daily send cap | 20 |
| 10 | Quiet hours | 23:00 – 07:00 local |
| 11 | Jitter | 60–90 s between sends |
| 12 | Warning pause | 7 days on account-warning signal |

## Architecture

```
┌──────────────────────────┐        ┌───────────────────────────────┐
│ LinkedIn Chrome Ext v1.0 │        │  Dashboard Backend  :8900      │
│  • search-scan.js        │──POST──▶  /api/linkedin/ingest          │
│  • content.js (replies)  │        │  /api/linkedin/leads           │
│  • feed-compose.js       │──POST──▶  /api/linkedin/drafts/{id}     │
│  • popup (side panel)    │        │  /api/linkedin/send/{id}       │
│  • API-key auth          │        │  /api/linkedin/safety          │
│  • NO AI calls           │        │  /api/linkedin/gmail/*         │
└──────────────────────────┘        │  /api/linkedin/replies         │
                                    │                                │
                                    │  linkedin_api.py (router)      │
                                    │  linkedin_db.py  (schema)      │
                                    │  linkedin_claude.py (bridge)   │
                                    │  linkedin_gmail.py  (OAuth,    │
                                    │                    send, poll) │
                                    │  linkedin_safety.py (rails)    │
                                    └─────────────┬─────────────────┘
                                                  ▼
                          Database/LinkedIn Data/leads.db   (SQLite)

                                                  ▲
                                                  │
                          ┌───────────────────────┴─────────────┐
                          │  Next.js  /linkedin/*                │
                          │   overview | leads | drafts |        │
                          │   sent     | recyclebin | settings   │
                          └──────────────────────────────────────┘
```

## Database schema

```sql
-- Main active leads
CREATE TABLE leads (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  post_url        TEXT UNIQUE NOT NULL,
  posted_by       TEXT,
  company         TEXT,
  role            TEXT,
  tech_stack      TEXT,
  rate            TEXT,
  location        TEXT,
  tags            TEXT,
  post_text       TEXT,
  email           TEXT,
  phone           TEXT,
  status          TEXT NOT NULL DEFAULT 'New',
                  -- New | Drafted | Queued | Sending | Sent | Replied
                  --   | Bounced | Skipped
  gen_subject     TEXT,
  gen_body        TEXT,
  email_mode      TEXT DEFAULT 'company',   -- company | individual
  cv_cluster      TEXT,                     -- python_ai|fullstack|scraping|n8n
  jaydip_note     TEXT,                     -- non-empty → skip on send
  skip_reason     TEXT,
  skip_source     TEXT,                     -- claude | regex | user
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  queued_at       TEXT,
  sent_at         TEXT,
  replied_at      TEXT,
  bounced_at      TEXT,
  follow_up_at    TEXT,
  needs_attention INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_leads_status       ON leads(status);
CREATE INDEX idx_leads_attention    ON leads(needs_attention);
CREATE INDEX idx_leads_last_seen    ON leads(last_seen_at);

-- Moved out of active view (junk / rejected / bounced / declined)
CREATE TABLE recyclebin (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  original_id     INTEGER,
  post_url        TEXT UNIQUE,
  payload_json    TEXT NOT NULL,           -- frozen snapshot at move time
  reason          TEXT NOT NULL,           -- no_email|auto_skip|bounced|user_note|manual
  moved_at        TEXT NOT NULL
);

-- Safety state — one row singleton
CREATE TABLE safety_state (
  id                          INTEGER PRIMARY KEY CHECK (id = 1),
  daily_sent_count            INTEGER NOT NULL DEFAULT 0,
  daily_sent_date             TEXT,
  last_send_at                TEXT,
  consecutive_failures        INTEGER NOT NULL DEFAULT 0,
  warning_paused_until        TEXT,
  autopilot_enabled           INTEGER NOT NULL DEFAULT 0,
  autopilot_hour              INTEGER NOT NULL DEFAULT 10,
  safety_mode                 TEXT NOT NULL DEFAULT 'max'  -- max | normal
);

-- Replies from Gmail poll
CREATE TABLE replies (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id         INTEGER NOT NULL REFERENCES leads(id),
  gmail_msg_id    TEXT UNIQUE NOT NULL,
  gmail_thread_id TEXT,
  from_email      TEXT,
  subject         TEXT,
  snippet         TEXT,
  received_at     TEXT NOT NULL,
  kind            TEXT NOT NULL             -- reply | bounce | auto_reply
);
CREATE INDEX idx_replies_lead ON replies(lead_id);

-- Gmail OAuth token storage (single user)
CREATE TABLE gmail_auth (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  email           TEXT,
  access_token    TEXT,
  refresh_token   TEXT,
  token_expires_at TEXT,
  history_id      TEXT                      -- last-seen Gmail history cursor
);

-- Extension auth
CREATE TABLE extension_keys (
  key             TEXT PRIMARY KEY,
  label           TEXT,
  created_at      TEXT NOT NULL,
  last_used_at    TEXT
);

-- Audit log (lightweight)
CREATE TABLE events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  at              TEXT NOT NULL,
  kind            TEXT NOT NULL,            -- ingest|draft|send|reply|bounce|pause|warning
  lead_id         INTEGER,
  meta_json       TEXT
);
CREATE INDEX idx_events_at ON events(at);
```

## API contract (v1)

| Method | Path | Purpose | Request → Response |
|---|---|---|---|
| GET  | `/api/linkedin/overview` | KPI card values | `→ {total, new, drafted, queued, sent_today, replied, bounced, quota_used, quota_cap}` |
| GET  | `/api/linkedin/leads` | Paged lead list + filters | query: `status, needs_attention, q, limit, offset` `→ {rows, total}` |
| GET  | `/api/linkedin/leads/{id}` | Detail | `→ lead` |
| POST | `/api/linkedin/leads/{id}` | Edit draft/note/mode | `{gen_subject?, gen_body?, jaydip_note?, email_mode?}` |
| POST | `/api/linkedin/leads/{id}/archive` | Move to Recyclebin | `{reason}` |
| POST | `/api/linkedin/leads/{id}/restore` | Un-archive | — |
| POST | `/api/linkedin/ingest` | Extension pushes raw scraped post | auth: `X-Ext-Key` header. `{post_url, posted_by, ...}` `→ {lead_id, status}` |
| POST | `/api/linkedin/drafts/{id}/generate` | Claude draft | `→ {subject, body, cv_cluster, skip_decision}` |
| POST | `/api/linkedin/send/{id}` | Direct send | `→ {sent_at}` (blocked by safety) |
| POST | `/api/linkedin/send/batch` | Queue N pending | `{count}` `→ {queued}` |
| POST | `/api/linkedin/send/stop` | Stop current batch | `→ {stopped}` |
| GET  | `/api/linkedin/safety` | Current state | `→ safety_state row` |
| POST | `/api/linkedin/safety` | Update mode / autopilot | `{safety_mode?, autopilot_enabled?, autopilot_hour?}` |
| GET  | `/api/linkedin/gmail/status` | Connected?  | `→ {connected, email, expires_at}` |
| GET  | `/api/linkedin/gmail/connect` | OAuth start | redirect |
| GET  | `/api/linkedin/gmail/callback` | OAuth finish | redirect back to /linkedin/settings |
| POST | `/api/linkedin/gmail/disconnect` | Revoke | — |
| POST | `/api/linkedin/replies/poll` | Force reply poll | `→ {new}` |
| GET  | `/api/linkedin/replies` | List | `→ rows` |
| POST | `/api/linkedin/extension/keys` | Issue a key | `{label}` `→ {key}` |
| GET  | `/api/linkedin/extension/keys` | List keys | `→ rows` |
| POST | `/api/linkedin/extension/keys/{key}/revoke` | Revoke | — |
| POST | `/api/linkedin/account-warning` | Extension signals warning | `{phrase}` → triggers 7-day pause |

## Frontend routes

- `/linkedin` — Overview (KPIs, safety status, autopilot toggle, Gmail connection pill)
- `/linkedin/leads` — All rows (filters: status, has email, attention, q). Row click → drawer with full details + draft editor.
- `/linkedin/drafts` — Only `Drafted | Queued`. Inline edit + Send button.
- `/linkedin/sent` — `Sent | Replied | Bounced` with reply thread drawer.
- `/linkedin/recyclebin` — Moved-out rows with Restore button.
- `/linkedin/settings` — Gmail OAuth, extension keys, safety mode, autopilot config, daily cap, quiet hours preview.

Shared components (live under `frontend/components/linkedin/`):

- `linkedin-kpi-row.tsx` — cross-page KPIs
- `linkedin-leads-table.tsx` — reusable table with sort/filter
- `linkedin-draft-editor.tsx` — subject + body textarea + regen
- `linkedin-safety-card.tsx` — autopilot + mode controls
- `linkedin-gmail-connect.tsx` — connect / disconnect UI
- `linkedin-extension-keys.tsx` — key list + copy

## Chrome extension (v1.0) — new folder `B2B/linkedin_extension/`

Files:
- `manifest.json` (MV3)
- `background.js` — relays messages, carries API key header
- `config.js` — dashboard URL, storage helpers
- `content.js` — LinkedIn messaging page (reply capture → POST)
- `search-scan.js` — search results scanner (post extraction → POST `/ingest`)
- `feed-compose.js` — user feed composer aid
- `popup.html` + `popup.js` + `popup.css` — side panel: key setup, stats, safety status

Message protocol (`chrome.runtime.sendMessage` + backend):
| From | Type | To | Payload |
|---|---|---|---|
| search-scan | `INGEST_POST` | background | raw post |
| background | POST `/api/linkedin/ingest` | backend | + `X-Ext-Key` |
| content | `CAPTURE_REPLY` | background | thread snippet |
| popup | `GET_STATUS` | background | — |
| background | GET `/api/linkedin/overview` | backend | — |

**No Claude API calls from extension.** All AI happens backend-side.

## Safety rails (backend re-implementation)

All rails enforced before any `_do_send()` call:
1. `safety_state.safety_mode === 'max'` → block batch, allow single-send only.
2. Quiet hours check (local tz).
3. `daily_sent_count >= 20` → reject.
4. `warning_paused_until > now` → reject.
5. Last send `< 60s` ago → reject (unless `--no-jitter` admin call).
6. After send: increment counter, persist `last_send_at`, roll counter at midnight.

Account-warning ingest from extension:
- Any `WARNING_PHRASES` hit → set `warning_paused_until = now + 7 days`, write `events(kind='warning')`, return 200.
- UI: Settings → big red banner with countdown.

## Phased rollout

### Phase 1 — Foundation (THIS CHANGESET)

- [x] Folder layout created (`Database/LinkedIn Data/`, `linkedin_extension/`, `dashboard/frontend/app/(dash)/linkedin/*`)
- [x] `PLAN.md` (this file)
- [ ] `linkedin_db.py` — schema + migrations + helpers
- [ ] `linkedin_api.py` — router stub with read-only endpoints (`/overview`, `/leads`, `/safety`)
- [ ] Wire router into `main.py`
- [ ] Sidebar entry + 6 empty-but-themed pages
- [ ] Extension manifest stub with placeholder icons + `config.js`

**Acceptance:** Sidebar shows LinkedIn item, clicking each page renders themed empty-state, `/api/linkedin/overview` returns zeros, `leads.db` auto-creates on first request.

### Phase 2 — Extension ingest (~2-3 d)

- Extension key issue/revoke UI + real auth middleware.
- `search-scan.js` ports DOM logic from legacy extension (post extraction, contact heuristics).
- Backend `/ingest` upsert by `post_url`, returns inserted/updated.
- Leads page renders real rows.

### Phase 3 — Claude drafts (~1-2 d)

- `linkedin_claude.py` backend bridge (same prompt + specialty-cluster picker as legacy v3.18).
- Draft editor UI (`linkedin-draft-editor.tsx`) with regenerate button.
- `skip_decision` honoured → auto-move to Recyclebin.

### Phase 4 — Gmail send via SMTP + App Password (~1-2 d)

- User generates Gmail App Password (2FA required) at
  https://myaccount.google.com/apppasswords — one-time, no GCP setup.
- `linkedin_gmail.py` — smtplib SSL send, imaplib reply/bounce poll, Fernet-encrypted credential storage.
- `/gmail/connect` (POST email+app_password, verifies both channels before save), `/gmail/test`, `/gmail/disconnect`.
- Safety-gated `/send/{id}` + `/send/batch`.
- Scheduler tick integrates with existing `main.py` thread.

### Phase 5 — Replies & bounces (~2 d)

- Gmail `users.history.list` delta polling (every 5 min).
- Reply matcher: link by `In-Reply-To` / thread id recorded at send.
- Bounce detector: `mailer-daemon`, DSN header parse.
- `/linkedin/sent` thread drawer.

### Phase 6 — Autopilot & recyclebin polish (~1-2 d)

- Daily autopilot cron (reuses `_scheduler_loop`).
- Manual rejection-note regex → recyclebin auto-move.
- Follow-up scheduler.
- Export button (CSV dump).

## Test matrix (Playwright additions)

- `/linkedin` renders without Gmail connected — shows connect CTA.
- `/api/linkedin/overview` returns zeroed KPIs when DB empty.
- Extension key issue → revoke flow.
- Safety state POST validates autopilot_hour 0–23.
- Batch send rejects when quota exhausted (seed state).

## Style conventions (enforced)

- **No dead fields.** Every column read by ≥1 endpoint OR deleted.
- **Theme match:** same Tailwind tokens (`zinc-*`, `hsl(250 80% 62%)` purple), shadcn primitives, `lucide-react` icons — mirror `components/batches-panel.tsx`, `kpi-card.tsx`.
- **Hooks over effects** for data fetch (SWR), keyed by the endpoint path.
- **Time formats:** ISO 8601 with second precision everywhere.
- **No `any`.** Types in `frontend/lib/types.ts` → `LinkedInLead`, `LinkedInOverview`, `SafetyState`, `GmailStatus`.
- **Errors:** backend raises `HTTPException`; frontend surfaces via toast + inline.

## File inventory (after full build)

```
B2B/
├── Database/LinkedIn Data/
│   ├── PLAN.md
│   ├── GMAIL_OAUTH.md            (Phase 4)
│   └── leads.db                  (runtime)
├── dashboard/
│   ├── backend/
│   │   ├── linkedin_api.py       ← router
│   │   ├── linkedin_db.py        ← schema + helpers
│   │   ├── linkedin_claude.py    (Phase 3)
│   │   ├── linkedin_gmail.py     (Phase 4)
│   │   └── linkedin_safety.py    (Phase 4)
│   └── frontend/
│       ├── app/(dash)/linkedin/
│       │   ├── page.tsx          overview
│       │   ├── leads/page.tsx
│       │   ├── drafts/page.tsx
│       │   ├── sent/page.tsx
│       │   ├── recyclebin/page.tsx
│       │   └── settings/page.tsx
│       └── components/linkedin/
│           ├── linkedin-kpi-row.tsx
│           ├── linkedin-leads-table.tsx
│           ├── linkedin-draft-editor.tsx
│           ├── linkedin-safety-card.tsx
│           ├── linkedin-gmail-connect.tsx
│           └── linkedin-extension-keys.tsx
└── linkedin_extension/
    ├── manifest.json
    ├── background.js
    ├── config.js
    ├── content.js
    ├── search-scan.js
    ├── feed-compose.js
    ├── popup.html | popup.js | popup.css
    └── icons/
```
