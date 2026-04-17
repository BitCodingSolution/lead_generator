# Outlook SMTP Setup — Current Status

**As of handoff**: mid-Phase 0. Authentication still failing.

## What works

1. **Mailbox-level SMTP AUTH** enabled:
   ```powershell
   Set-CASMailbox -Identity jaydip@bitcodingsolutions.com -SmtpClientAuthenticationDisabled $false
   ```
   Verified: `SmtpClientAuthenticationDisabled : False` ✅

2. **Security Defaults disabled** in Microsoft Entra admin center:
   - https://entra.microsoft.com → Bitcoding Solutions tenant → Properties → Manage security defaults
   - Set to "Disabled (not recommended)"
   - Reason selected, Save confirmed ✅

## What fails

PowerShell SMTP test:
```powershell
$cred = Get-Credential   # jaydip@bitcodingsolutions.com + regular password
Send-MailMessage `
  -From 'jaydip@bitcodingsolutions.com' `
  -To 'jaydipnakarani888@gmail.com' `
  -Subject 'SMTP test 2 - after security defaults disabled' `
  -Body 'Should work now' `
  -SmtpServer 'smtp.office365.com' `
  -Port 587 `
  -UseSsl `
  -Credential $cred
```

Error:
```
The SMTP server requires a secure connection or the client was not authenticated.
The server response was: 5.7.57 Client not authenticated to send mail.
Error: 535 5.7.139 Authentication unsuccessful, the request did not meet the
criteria to be authenticated successfully.
```

## Root causes (likely)

1. **Tenant-level SMTP AUTH still disabled** (separate from mailbox-level)
2. **2FA + Basic SMTP AUTH incompatible** — needs App Password, not regular password

## Next actions (resume from here)

### Step 1 — Enable tenant-level SMTP AUTH

In Exchange Online PowerShell (connected session):
```powershell
Set-TransportConfig -SmtpClientAuthenticationDisabled $false

# Verify
Get-TransportConfig | Select-Object SmtpClientAuthenticationDisabled
# Expected: False
```

### Step 2 — Create App Password

1. Open https://mysignins.microsoft.com/security-info
2. Sign in as `jaydip@bitcodingsolutions.com`
3. Click **"Add sign-in method"**
4. Select **"App password"** from dropdown
   - **If option missing**: tenant blocks app passwords; must use OAuth2 instead (alternative path needed)
5. Name: `SMTP Outlook`
6. Click **Create** → copy the 16-character password (shown once)

### Step 3 — Retest

```powershell
$cred = Get-Credential
# Username: jaydip@bitcodingsolutions.com
# Password: <the 16-char app password>

Send-MailMessage `
  -From 'jaydip@bitcodingsolutions.com' `
  -To 'jaydipnakarani888@gmail.com' `
  -Subject 'SMTP test 3 - app password' `
  -Body 'After tenant + app password' `
  -SmtpServer 'smtp.office365.com' `
  -Port 587 `
  -UseSsl `
  -Credential $cred
```

Success = mail arrives in Gmail inbox → move to Phase 1 (Gmail Send-As alias).

## Context

- **Tenant**: Bitcoding Solutions (38 users, 9 admin roles, Entra ID Free)
- **Account**: jaydip@bitcodingsolutions.com
- **2FA**: ON (Microsoft Authenticator, phone +91 78028 30436)
- **Password**: last updated ~10 months ago, strong
