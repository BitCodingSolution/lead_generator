# Email authentication DNS (SPF / DKIM / DMARC)

This doc captures the deliverability-critical DNS records for
`bitcodingsolutions.com` — the domain the B2B outreach path sends from
(`jaydip@bitcodingsolutions.com` via Microsoft 365 / Outlook SMTP).

**DNS provider**: Cloudflare (`dina.ns.cloudflare.com`, `johnathan.ns.cloudflare.com`). Registrar is separate — don't edit records at the registrar, edit at Cloudflare.

**Mail provider**: Microsoft 365 (Exchange Online Protection).

---

## Why this matters

The LinkedIn side sends from `@gmail.com` addresses, so Google handles
SPF/DKIM/DMARC for us automatically. The B2B side is different —
`bitcodingsolutions.com` is our own domain, so authentication is our
responsibility. Missing any of the three records causes cold mails to
silently fold into Junk on the recipient side, quietly burning the
domain's reputation.

| Record | Role |
|---|---|
| SPF | Declares which servers are allowed to send mail for this domain. |
| DKIM | Attaches a cryptographic signature to every outbound mail so recipients can verify it really came from us and wasn't tampered with. |
| DMARC | Tells recipients what to do when SPF/DKIM fail, and where to send failure reports. |

---

## Active records (as of 2026-04-24)

### SPF — TXT at apex

```
Name:    bitcodingsolutions.com
Type:    TXT
Content: v=spf1 include:spf.protection.outlook.com -all
```

`-all` = hard fail (any server not in the include list is rejected).

### DKIM — two CNAMEs

Microsoft 365 signs outbound mail with a rotating key pair. The actual
keys live in Microsoft's tenant; we publish CNAMEs pointing at them so
DNS lookups always resolve to the current key.

```
Name:   selector1._domainkey.bitcodingsolutions.com
Type:   CNAME
Target: selector1-bitcodingsolutions-com._domainkey.bitcodingsolutions.n-v1.dkim.mail.microsoft
Proxy:  DNS only (grey cloud)  — CRITICAL

Name:   selector2._domainkey.bitcodingsolutions.com
Type:   CNAME
Target: selector2-bitcodingsolutions-com._domainkey.bitcodingsolutions.n-v1.dkim.mail.microsoft
Proxy:  DNS only (grey cloud)  — CRITICAL
```

Proxy MUST be "DNS only". If Cloudflare proxies (orange cloud) the
record, it rewrites the CNAME target and DKIM validation fails on the
recipient side.

After publishing the CNAMEs, the toggle still needs to be flipped ON in
the Microsoft Defender admin panel:
`https://security.microsoft.com/dkimv2` → `bitcodingsolutions.com` row
→ "Sign messages for this domain with DKIM signatures".

Microsoft's internal DNS cache can lag 15 min – 4 hrs even after the
record is globally propagated; expect a "CnameMissing" error during
that window.

### DMARC — TXT

```
Name:    _dmarc.bitcodingsolutions.com
Type:    TXT
Content: v=DMARC1; p=none; rua=mailto:jaydip@bitcodingsolutions.com; fo=1
```

`p=none` is monitor-only — nothing is blocked, but weekly aggregate
reports land at `jaydip@bitcodingsolutions.com` showing who is
claiming to send as us (legitimate and spoofed alike).

---

## Hardening roadmap

`p=none` is step 1. Once we've watched two weeks of reports and they're
all clean (SPF + DKIM passing on every legit send):

1. `v=DMARC1; p=quarantine; pct=25; rua=...` — quarantine a quarter of failures.
2. `v=DMARC1; p=quarantine; pct=100; rua=...` — quarantine all failures.
3. `v=DMARC1; p=reject; pct=100; rua=...` — reject outright (strictest; optional).

Do NOT jump straight to `p=reject` or `p=quarantine` — if DKIM isn't
signing yet or forwarders are mangling the SPF path, legitimate mail
gets blocked.

---

## Verify

From any machine with PowerShell:

```powershell
Resolve-DnsName bitcodingsolutions.com -Type TXT -Server 8.8.8.8 | Select Strings
Resolve-DnsName selector1._domainkey.bitcodingsolutions.com -Type CNAME -Server 8.8.8.8 | Select NameHost
Resolve-DnsName selector2._domainkey.bitcodingsolutions.com -Type CNAME -Server 8.8.8.8 | Select NameHost
Resolve-DnsName _dmarc.bitcodingsolutions.com -Type TXT -Server 8.8.8.8 | Select Strings
```

From unix / WSL:

```bash
dig +short TXT   bitcodingsolutions.com
dig +short CNAME selector1._domainkey.bitcodingsolutions.com
dig +short CNAME selector2._domainkey.bitcodingsolutions.com
dig +short TXT   _dmarc.bitcodingsolutions.com
```

End-to-end proof: send a test mail from `jaydip@bitcodingsolutions.com`
to a personal Gmail. Open the mail → 3-dot menu → **"Show original"**.
All three lines should read PASS:

```
SPF:   PASS  with IP ...
DKIM:  PASS  with domain bitcodingsolutions.com
DMARC: PASS
```

Any FAIL means something in DNS drifted — re-run the resolve commands
above and reconcile against the values in this doc.

---

## Adjacent LinkedIn-side note

The LinkedIn outreach path uses `@gmail.com` sender addresses — SPF /
DKIM / DMARC on those are handled by Google and need no action on our
side. The rails that protect that path live in code, not DNS:

- Per-account 5-min cooldown (`MIN_ACCOUNT_GAP_S` in `dashboard/backend/linkedin_gmail.py`)
- Warmup curve + effective daily cap
- Soft opt-out P.S. in every draft (baked into the Claude system prompt)
- Auto-blocklist when a reply classifies as `not_interested`
- Business-hours-only toggle in the safety card
