# B2B Leads Outreach System

Separate initiative from the LinkedIn extension (which lives at `h:/Upwork Agent/LinkedIn/`). Do **not** modify LinkedIn files from this folder.

## Goal

Direct B2B cold outreach to companies sourced from Dealfront (German B2B database). Send branded emails from `jaydip@bitcodingsolutions.com` via Outlook SMTP, managed through a Google Sheet + Apps Script + Claude-powered draft generation.

## Key decisions (locked)

- **Safety**: Ultra-safe — 20 drafts/day cap
- **Send mode**: Manual "Send now" first; auto-send with time-delay queue later
- **Language**: English only
- **Tone**: Casual, business-outcome focused (not Python/tech jargon)
- **Positioning**: Software + AI hybrid, industry-aware pitches
- **Email mode**: Company (BitCoding Solutions, Co-Founder & CTO signature) — not individual freelancer
- **Sender**: `jaydip@bitcodingsolutions.com` (branded), NOT `info@`
- **Stack**: Standalone Apps Script (separate from LinkedIn one) + Gmail "Send-As" alias routing through Outlook SMTP
- **AI**: Direct Anthropic API for draft generation (bridge can't reach localhost from Apps Script)
- **Attachments**: Smart CV/brochure pick per industry
- **Status flow**: New → Drafted → Sent → Replied

## Current status

See [docs/SMTP-SETUP-STATUS.md](docs/SMTP-SETUP-STATUS.md) for where we left off with the Outlook SMTP authentication setup.

See [docs/B2B-PLAN.md](docs/B2B-PLAN.md) for the full phased plan.

See [docs/DEALFRONT-LEADS.md](docs/DEALFRONT-LEADS.md) for sample leads to test with.

## Folder layout

- `docs/` — plan, status, notes, pitch prompts
- `AppsScript/` — the standalone B2B Apps Script (to be created)
