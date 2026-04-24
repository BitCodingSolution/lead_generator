# B2B Leads Outreach — Phased Plan

## Phase 0 — Outlook SMTP auth (IN PROGRESS)

Goal: enable `jaydip@bitcodingsolutions.com` to authenticate against `smtp.office365.com:587` so Gmail "Send-As" alias can relay through it.

Done so far:
- ✅ Enabled SMTP AUTH at mailbox level: `Set-CASMailbox -Identity jaydip@bitcodingsolutions.com -SmtpClientAuthenticationDisabled $false`
- ✅ Disabled Security Defaults in Microsoft Entra admin center

Blocker:
- ❌ PowerShell `Send-MailMessage` test fails with **535 5.7.139 Authentication unsuccessful**

Next:
1. Enable at tenant level too: `Set-TransportConfig -SmtpClientAuthenticationDisabled $false`
2. Create App Password at https://mysignins.microsoft.com/security-info (2FA is ON, so regular password won't work for basic SMTP AUTH)
3. Retest PowerShell with app password
4. Screenshot result

See [SMTP-SETUP-STATUS.md](SMTP-SETUP-STATUS.md) for detailed state.

## Phase 1 — Gmail Send-As alias

Once SMTP works from PowerShell:
1. Gmail → Settings → Accounts → "Send mail as" → Add another email address
2. Enter `jaydip@bitcodingsolutions.com`, uncheck "Treat as an alias"
3. SMTP: `smtp.office365.com:587`, STARTTLS, app password from Phase 0
4. Verify via confirmation email
5. Test: send a mail from Gmail UI using the alias → confirm recipient sees `jaydip@bitcodingsolutions.com`

## Phase 2 — Standalone B2B Apps Script

Brand new script (do NOT touch LinkedIn Apps Script).

Sheet schema (tab: `B2B Leads`):
- Company | Industry | Contact name | Role | Email | Phone | Country | Source | Pitch hook | Status | Draft subject | Draft body | Attachment file | Last action | Notes | Sent at | Replied at | Error

Config rows: daily cap (20), paused flag, default attachment folder ID.

Features:
- Checkbox column "Generate draft" → calls Claude API → fills Draft subject/body/attachment
- Checkbox column "Send now" → `GmailApp.sendEmail({from: 'jaydip@bitcodingsolutions.com', ...})` using alias
- Daily cap guard
- Status auto-update New → Drafted → Sent
- Industry-aware pitch prompt (Energy → grid analytics, Consulting → reporting automation, etc.)

## Phase 3 — Extension support (optional)

Extension button: "Generate B2B batch" → pulls rows from sheet → calls bridge → writes back drafts. Useful while bridge is free (via Max subscription). For production, direct Anthropic API in Apps Script is the plan.

## Phase 4 — Auto-send queue

Time-based trigger (every 30 min) reads "Ready to send + scheduled time < now" rows, sends with jitter, respects daily cap.

## Phase 5 — Reply tracking

Gmail label/search on sent thread → detect inbound reply → update status to Replied + copy snippet.

## Phase 6 — Brochure

Separate Claude session will generate a 1-page PDF brochure for attachment. Prompt already drafted — lives in `docs/BROCHURE-PROMPT.md` (to be added).
