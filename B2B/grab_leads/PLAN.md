# Grab Leads — Master Planning Document

**Status:** Planning only (no code yet)
**Last updated:** 2026-04-20
**Owner:** Jaydip N. / Pradip K. (BitCoding Solutions)

---

## 1. Purpose

Build a free-tools lead sourcing pipeline that feeds the existing B2B outreach infrastructure (`H:\Lead Generator\B2B\`). Target: high-ticket US decision makers for AI-first backend services.

**Non-goals:**
- No paid lead databases (ZoomInfo, Apollo paid tier, etc.)
- No generic scraping of shops / local businesses
- No mass blast — quality > quantity

---

## 2. Ideal Customer Profile (ICP)

| Dimension | Target |
|-----------|--------|
| Geography | United States (primary), English-speaking markets (secondary) |
| Company size | 20-500 employees (SMB / mid-market sweet spot) |
| Deal size | $5K minimum, ideal range $10K–$50K project or $10K–$30K/mo retainer |
| Persona | CEO, CTO, COO, Founder, VP Engineering, Head of AI/Data |
| Company type | Funded AI/SaaS startups, SaaS companies adding AI features, enterprises with legacy data wanting internal RAG, agencies needing AI subcontractor |
| Signal of intent | Recent funding, hiring AI/ML engineers, launching AI features, modernizing tech stack |

**Positioning line (from Jaydip's Upwork):** *"I do not do generic web apps — I do complex, AI-first backend systems."* Lead targeting must reflect this.

---

## 3. Signal Sources (priority order)

### Tier 1 — Build first
| # | Source | Why | Signal type | Auth needed |
|---|--------|-----|-------------|-------------|
| 1 | **Wellfound (AngelList)** | US startups actively hiring AI eng = budget + need | hiring_ai | No (public job listings) |
| 2 | **Y Combinator / WorkAtAStartup** | All YC portfolio companies public, high-quality funded startups | yc_batch | No for directory |
| 3 | **Crunchbase (free tier)** | Recently funded companies = cash-rich, spending | recent_funding | Free account |

### Tier 2 — Add after Tier 1 validated
| # | Source | Why | Signal type |
|---|--------|-----|-------------|
| 4 | LinkedIn Google dorks | Decision-maker discovery when company known | person_discovery |
| 5 | Company /team /about scraping | Founder/CTO often listed with email | person_enrichment |
| 6 | BuiltWith reverse lookup | Companies using specific tech stacks (Shopify Plus, HubSpot Enterprise) | tech_stack_match |
| 7 | Product Hunt makers | Recent launchers = founders actively building | product_launch |
| 8 | GitHub org contributors | CTO/tech-lead discovery, sometimes public email | technical_leader |

### Tier 3 — Optional / experimental
- Twitter/X bio scraping (founders often list "CEO @ XYZ")
- SEC EDGAR (larger public companies)
- Wellfound investor filters

---

## 4. Folder Architecture

```
H:\Lead Generator\Grab Leads\
│
├── PLAN.md                           ← this document
├── README.md                         ← ops guide (how to run, add sources)
│
├── sources/                          ← one folder per source
│   ├── wellfound/
│   │   ├── scraper.py                ← standalone, CLI-runnable
│   │   ├── schema.json               ← fields this source provides
│   │   ├── data.db                   ← source-specific SQLite
│   │   ├── raw/                      ← raw JSON dumps (audit trail)
│   │   ├── processed/                ← normalized output
│   │   └── README.md                 ← quirks, rate limits, selectors
│   │
│   ├── ycombinator/
│   ├── crunchbase/
│   ├── linkedin_dork/
│   ├── company_about_pages/
│   └── builtwith/
│
├── common/                           ← shared code across sources
│   ├── base_scraper.py               ← abstract class all scrapers inherit
│   ├── validators.py                 ← email format, domain reachable, dedup
│   ├── email_pattern_gen.py          ← firstname@, f.lastname@ etc.
│   ├── smtp_verify.py                ← thin wrapper over B2B's verify_emails_free.py
│   └── decision_maker_finder.py      ← given domain → CEO/CTO names
│
├── unified/                          ← cross-source views
│   ├── pool.db                       ← deduplicated, normalized leads pool
│   ├── merge.py                      ← pull from each source.db → pool.db
│   ├── dedup.py                      ← person appearing across sources
│   └── view_cli.py                   ← CLI to filter & preview leads
│
├── api/                              ← dashboard integration
│   ├── scraper_runner.py             ← FastAPI endpoints dashboard hits
│   └── routes.md                     ← endpoint contract
│
├── mailer/                           ← Outlook bridge
│   ├── template_manager.py           ← English templates per signal type
│   ├── templates/
│   │   ├── en_hiring_ai.md
│   │   ├── en_funded_recent.md
│   │   ├── en_yc_portfolio.md
│   │   └── en_tech_stack.md
│   ├── to_b2b_batch.py               ← export selected leads → Excel batch for B2B pipeline
│   └── README.md                     ← how this connects to B2B/scripts/write_to_outlook.py
│
└── logs/                             ← all scraper runs + errors
```

---

## 5. Scraper Interface Contract

Every source's `scraper.py` MUST implement the following CLI and output contract:

### CLI
```bash
python scraper.py --query "AI engineer" --location "San Francisco" --limit 100
python scraper.py --resume                     # continue from last checkpoint
python scraper.py --dry-run                    # preview without writing
python scraper.py --since 2026-04-01           # incremental scrape
```

### Output schema (minimum common fields)
Every source writes to its own `data.db` with this table:

```sql
CREATE TABLE leads (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  source             TEXT NOT NULL,           -- 'wellfound' | 'yc' | 'crunchbase'
  source_url         TEXT NOT NULL,           -- exact page scraped
  company_name       TEXT NOT NULL,
  company_domain     TEXT,                    -- extracted/inferred
  company_size       TEXT,                    -- employee range if available
  location           TEXT,
  signal_type        TEXT NOT NULL,           -- 'hiring_ai' | 'recent_funding' | ...
  signal_detail      TEXT,                    -- "Hiring Senior ML Engineer" / "Raised $5M Series A"
  signal_date        DATE,                    -- when the signal happened
  person_name        TEXT,                    -- optional at scrape time
  person_title       TEXT,                    -- optional
  person_linkedin    TEXT,                    -- optional
  person_email       TEXT,                    -- filled in enrichment phase
  email_verified     INTEGER DEFAULT 0,       -- 0 | 1
  extra_data         TEXT,                    -- JSON blob for source-specific fields
  scraped_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source, source_url)
);
```

Source-specific richness (e.g., Crunchbase's `funding_amount`, YC's `batch`) lives inside `extra_data` JSON — never flattened, never lost.

### Validation checklist (before scraper is "done")
- [ ] Handles pagination correctly
- [ ] Respects rate limits (sleep between requests)
- [ ] Graceful failure on single-record errors (log, continue)
- [ ] Resumable via `--resume` flag (checkpoint file)
- [ ] Dry-run mode produces preview without writes
- [ ] `--since` flag for incremental scraping
- [ ] Logs structured (JSON) to `logs/<source>_<timestamp>.log`
- [ ] Unit test on 1 known-good URL → fixed expected fields

---

## 6. Enrichment Pipeline

Scraped lead often has company but no person/email. Enrichment fills the gaps:

```
Raw scraped lead (company only)
         │
         ▼
[decision_maker_finder.py]
   ├─ Scrape company website /team, /about
   ├─ Google dork: site:linkedin.com/in "CEO" "<company>"
   └─ Output: person_name, person_title, person_linkedin
         │
         ▼
[email_pattern_gen.py]
   ├─ Generate candidates: firstname@, f.last@, firstname.last@ etc.
   └─ Output: [email1, email2, email3]
         │
         ▼
[smtp_verify.py] (wraps B2B's verify_emails_free.py)
   ├─ SMTP check each candidate
   └─ Output: best verified email or NULL
         │
         ▼
Updated lead in source.db (person_email, email_verified=1)
```

---

## 7. Unified Pool (`unified/pool.db`)

### Why a separate pool
- Each source has different fields → keeping raw data clean per source is important
- But for mailing, we need ONE deduplicated view
- Pool is the "merge target" — not a replacement, a projection

### Pool schema
Same as source schema, PLUS:
```sql
  first_seen_source  TEXT,           -- which source found this person first
  seen_in_sources    TEXT,           -- JSON array: ['wellfound','yc']
  dedup_key          TEXT,           -- normalized email or company+person hash
  in_b2b_db          INTEGER,        -- already exists in B2B/leads.db? (avoid re-mailing)
  pool_status        TEXT            -- 'new' | 'enriched' | 'sent_to_b2b' | 'mailed'
```

### Dedup strategy
- Primary key: verified email (lowercase)
- Secondary: normalized `company_domain + person_name`
- When same person found in multiple sources → keep richest record, append `seen_in_sources`

---

## 8. Dashboard Integration

### Existing dashboard
- Backend: [B2B/dashboard/backend/main.py](H:/Lead Generator/B2B/dashboard/backend/main.py) (FastAPI)
- Frontend: [B2B/dashboard/frontend/](H:/Lead Generator/B2B/dashboard/frontend/) (Next.js + TS)

### New dashboard tabs / sections
1. **Sources tab**
   - Cards: Wellfound, YC, Crunchbase, ...
   - Shows: last scrape time, lead count, last run status
   - Button: **[Run Fresh Scrape]** → triggers FastAPI endpoint

2. **Leads Pool tab**
   - Table of unified pool
   - Filters: source (multi-select), signal type, verified email only, not-yet-mailed
   - Actions: bulk-select → **[Push to Outlook Batch]**

3. **Scrape Config tab**
   - Per-source config: queries, locations, keywords, rate limits
   - Saved presets (e.g., "AI startups SF", "YC S24 cohort")

4. **Run History tab**
   - Log of all scrape runs, leads added, errors

### API endpoints (FastAPI)
```
POST   /grab/sources/{source}/scrape        → trigger scrape (async, returns job_id)
GET    /grab/sources/{source}/status        → lead count, last run
GET    /grab/jobs/{job_id}                  → scrape job progress
GET    /grab/pool                           → paginated leads with filters
POST   /grab/pool/push-to-b2b               → export selected → B2B Excel batch
GET    /grab/pool/stats                     → counts per source, per signal, per status
```

---

## 9. Outlook Sending — English Template Migration

### Existing (B2B)
- Sender: `pradip@bitcodingsolutions.com`
- Signature: **German** ("Mit freundlichen Grüßen") — [write_to_outlook.py:27-38](H:/Lead Generator/B2B/scripts/write_to_outlook.py#L27-L38)
- Flow: `write_to_outlook.py` (drafts) → `send_drafts.py` (jitter-send)

### Changes needed for US
1. **English signature variant** — add `SIGNATURE_HTML_EN` constant, pick based on lead's region
2. **New templates folder** — `Grab Leads/mailer/templates/en_*.md` with US-tone copy
3. **Per-signal personalization** — template picked by `signal_type`:
   - `en_hiring_ai.md` — "Noticed you're hiring for [ROLE]. We ship AI agents in production..."
   - `en_funded_recent.md` — "Congrats on the [ROUND]. Many post-funding teams need to ship AI features fast..."
   - `en_yc_portfolio.md` — "As a [BATCH] company, your speed-to-ship matters..."
4. **From-address decision** — stay on `pradip@bitcodingsolutions.com` for now, or register US-friendly domain later
5. **Volume**: Outlook Desktop COM ≈ 100-300 sends/day safe. For scale, migrate to Graph API later.

### Send flow for Grab Leads
```
unified/pool.db (filtered selection)
        │
        ▼
mailer/to_b2b_batch.py
   ├─ Export to Excel in B2B format
   ├─ Fill draft_subject, draft_body using template + signal data
   └─ Place in B2B's batches folder
        │
        ▼
[reuse existing B2B pipeline]
B2B/scripts/write_to_outlook.py  → creates drafts
B2B/scripts/send_drafts.py       → sends with jitter
```

---

## 10. Phase-wise Timeline

### Phase 1 — First source end-to-end (week 1)
- [ ] `common/base_scraper.py` abstract class
- [ ] `sources/wellfound/scraper.py` (first source)
- [ ] Output to `wellfound/data.db`
- [ ] Manual validation — 100 scraped records look right

### Phase 2 — Enrichment (week 2)
- [ ] `common/decision_maker_finder.py`
- [ ] `common/email_pattern_gen.py`
- [ ] `common/smtp_verify.py` (wrap B2B verifier)
- [ ] Run end-to-end on Wellfound data → 50 verified decision-maker emails

### Phase 3 — Unified pool (week 2-3)
- [ ] `unified/pool.db` schema
- [ ] `unified/merge.py`, `unified/dedup.py`
- [ ] `unified/view_cli.py` for quick inspection

### Phase 4 — Second & third source (week 3)
- [ ] `sources/ycombinator/scraper.py`
- [ ] `sources/crunchbase/scraper.py`
- [ ] Merge into unified pool

### Phase 5 — Dashboard integration (week 4)
- [ ] New FastAPI routes under `/grab/*`
- [ ] Frontend tabs: Sources, Leads Pool, Scrape Config, Run History
- [ ] "Run Fresh Scrape" button working

### Phase 6 — Outlook US-template bridge (week 5)
- [ ] English signature + templates
- [ ] `mailer/to_b2b_batch.py` exporter
- [ ] Dry-run: pool → Excel batch → B2B write_to_outlook.py → Outlook Drafts
- [ ] First live send: 10 hyper-targeted leads

### Phase 7 — Tier 2 sources + polish
- [ ] LinkedIn Google dork scraper
- [ ] Company /about scraper
- [ ] BuiltWith tech-stack reverse lookup

---

## 11. Open Decisions (revisit before coding)

| # | Decision | Options | Default |
|---|----------|---------|---------|
| D1 | First source to build | Wellfound / YC / Crunchbase | **Wellfound** (public, clear signal, US-heavy) |
| D2 | Scraping library | Playwright / Selenium / requests+BS4 / httpx | **Playwright** (most anti-bot resistant, JS-rendered sites) |
| D3 | US-domain email sender | Keep `pradip@bitcodingsolutions.com` / register US domain | Keep for now, decide at Phase 6 |
| D4 | Dashboard vs standalone CLI first | Dashboard first / CLI-only validation first | **CLI first, dashboard at Phase 5** |
| D5 | Rate-limit / proxy strategy | Residential proxies / rotating user agents / just go slow | **Go slow + rotating UA** for Phase 1; proxies only if blocked |
| D6 | Outreach volume target | 50/week hyper / 200/week targeted / 500/week broad | **50-100/week hyper-targeted** to start |
| D7 | Email finder — Apollo free vs pure SMTP guess | Apollo (50/mo free) / pure pattern+verify | **Pattern+verify first**, Apollo fallback |

---

## 12. Success Metrics (Phase 6 checkpoint)

- ≥ 70% scrape completeness (rows with company + signal filled)
- ≥ 40% enrichment rate (leads with verified person email)
- ≥ 10% reply rate on first cold email (US benchmark for hyper-targeted is 5-15%)
- ≥ 1 qualified meeting per 50 sent
- ≥ 1 closed deal ($5K+) per 200 sent

---

## 13. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Source website blocks scraper (Cloudflare, rate limits) | High | Medium | Playwright + slow pace + rotate UA; fallback to manual CSV import |
| Outlook account flagged for spam | Medium | High | Keep per-day volume ≤ 100, maintain jitter, warm-up, quality templates |
| Email verification false positives (catch-all domains) | Medium | Medium | Mark catch-alls separately; lower confidence score |
| Legal/ToS issues (LinkedIn, Crunchbase) | Low | High | Scrape public data only, no login-walled content, respect robots.txt |
| LLM-generated templates feel generic → low reply rate | High | High | Per-signal templates with real signal reference ("saw you're hiring X") |
| Duplicate outreach to same person | Medium | Medium | `in_b2b_db` check in pool before export |

---

## 14. What's NOT in this plan (yet)

- Phase 2 (LinkedIn outreach) — will be separate plan after cold email works
- Paid API integrations (Apollo paid, Clay, Clearbit)
- Automated follow-up cadences beyond B2B's existing `queue_followups.py`
- CRM integration (HubSpot, Pipedrive)
- Multi-sender rotation (different From accounts)
- A/B testing framework for templates
