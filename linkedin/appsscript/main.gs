// ============================================================
// Pradip AI — Lead Tracker + Direct Email Sender
//
// CURRENT VERSION: v3.15
//
// v3.15 changes:
//   • Cleanup pass — pruned stale changelog (v3.5 → v3.13) referencing
//     the long-removed Search_Extracts staging tab and the (now removed)
//     Chrome-extension Post tab. Fixed harmless extra args on hasUserNote
//     call inside sendEmailForRow.
// v3.14:
//   • Live queue sidebar (QueueStatus.html) auto-opens when "Send all
//     pending" starts: progress bar, sent/skipped/pending counts, elapsed
//     + ETA, currently-sending row & recipient, Stop button. 3-sec server
//     poll + 250ms client smoothing so the bar creeps continuously.
//     "A batch send is already running" alert shows live done/total + ETA
//     instead of a generic "wait" message.
//   • PROP_QUEUE_* extended: SENT, SKIPPED, STARTED_AT, CURRENT_ROW,
//     CURRENT_EMAIL, LAST_TICK — all written by processQueue, read by the
//     sidebar via getQueueProgress().
//   • Menu: 📊 Queue status (live sidebar) entry added.
//
// CORE FEATURES (cumulative, no longer worth listing per-version):
//   • Single sheet target: 'LinkedIn'. New rows insert at TOP.
//   • Send checkbox → DIRECT SEND (not draft); checkbox locks after send.
//   • Batch send: menu → "Send all pending", 60-90s jitter, daily cap 20,
//     quiet hours 11pm-7am, stop anytime.
//   • Phrase blocklist (full-time/onsite/visa/intern/etc.) auto-skips on
//     save AND defends at send time. Positive "is a job post" filter.
//   • Company / email-domain blocklist (BLOCKED_COMPANIES, BLOCKED_EMAIL_DOMAINS).
//   • Manual notes in column U/V/W… mean "leave alone" — skipped from send,
//     counted as manual replies in the dashboard.
//   • Specialty-cluster CV picker (python_ai / fullstack / scraping / n8n).
//   • Menu: Check replies (Gmail poll), Check bounces, Send follow-ups,
//     Refresh dashboard, Stop queue.
// ============================================================

const SHEET_NAME = 'LinkedIn';

// Drive folder containing CV PDFs (smart-picked per lead)
const DRIVE_CV_FOLDER_ID = '1g_RYUTByWXDZb-Tt8h5fHF4ZwCMDUxCP';

// ──────────── INDIVIDUAL mode signatures ────────────
const EMAIL_SIGNATURE_INDIVIDUAL_TEXT =
`Best,
Jaydip Nakarani
Senior Python Developer
+91 78028 30436`;

const EMAIL_SIGNATURE_INDIVIDUAL_HTML =
`<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;line-height:1.6;margin-top:20px;">
  <div>Best,</div>
  <div>Jaydip Nakarani</div>
  <div>Senior Python Developer</div>
  <div>+91 78028 30436</div>
</div>`;

// ──────────── COMPANY mode signatures ────────────
const EMAIL_SIGNATURE_COMPANY_TEXT =
`Best,
Jaydip Nakarani
Co-Founder & CTO · BitCoding Solutions Pvt Ltd
jaydip@bitcodingsolutions.com
+91 78028 30436
https://bitcodingsolutions.com/`;

const EMAIL_SIGNATURE_COMPANY_HTML =
`<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;line-height:1.6;margin-top:20px;">
  <div>Best,</div>
  <div><b>Jaydip Nakarani</b></div>
  <div>Co-Founder &amp; CTO &middot; BitCoding Solutions Pvt Ltd</div>
  <div><a href="mailto:jaydip@bitcodingsolutions.com" style="color:#0a66c2;text-decoration:none;">jaydip@bitcodingsolutions.com</a></div>
  <div>+91 78028 30436</div>
  <div><a href="https://bitcodingsolutions.com/" target="_blank" rel="noopener" style="color:#0a66c2;text-decoration:none;">bitcodingsolutions.com</a></div>
</div>`;

// ──────────── Sheet schema ────────────
const HEADERS = [
  '📧 Send',         // A (checkbox)
  'Date Added',      // B
  'Post URL',        // C
  'Posted By',       // D
  'Company',         // E
  'Role',            // F
  'Tech Stack',      // G
  'Rate/Budget',     // H
  'Location',        // I
  'Tags',            // J
  'Status',          // K
  'Post Text',       // L
  'Notes',           // M
  'Email',           // N
  'Phone',           // O
  'Email Sent At',   // P
  'Follow-up Date',  // Q
  'Gen Subject',     // R
  'Gen Body',        // S
  'Email Mode',      // T
  'Jaydip Note'      // U  ← any non-empty value skips the row from send (single + batch)
];

const COL = {
  SEND: 0, DATE: 1, POST_URL: 2, POSTED_BY: 3, COMPANY: 4, ROLE: 5,
  TECH: 6, RATE: 7, LOCATION: 8, TAGS: 9, STATUS: 10,
  POST_TEXT: 11, NOTES: 12, EMAIL: 13, PHONE: 14,
  SENT_AT: 15, FOLLOW: 16, GEN_SUBJECT: 17, GEN_BODY: 18,
  EMAIL_MODE: 19,
  JAYDIP_NOTE: 20
};

// The exact string written to Status column when send succeeds. Used as lock key.
const SENT_STATUS = 'Sent';
const QUEUED_STATUS = 'Queued';
const SENDING_STATUS = '⏳ Sending…';
const BOUNCED_STATUS = 'Bounced';
const REPLIED_STATUS = 'Replied';

// ──────────── Batch-send config ────────────
const BATCH_DAILY_CAP        = 20;          // Hard cap per calendar day (total sent)
const BATCH_PER_RUN_CAP      = 20;          // Max rows queued in a single click
const BATCH_TRIGGER_EVERY_MIN = 1;          // Base trigger interval (Apps Script minimum)
const BATCH_JITTER_MIN_MS    = 0;           // Extra pre-send sleep jitter — lower bound
const BATCH_JITTER_MAX_MS    = 30 * 1000;   // Extra pre-send sleep jitter — upper bound
// Effective inter-send gap = BATCH_TRIGGER_EVERY_MIN (60s) + random(0..30s) = 60–90s

// Quiet hours — no batch sends at night (local time of the account running the script)
const BATCH_QUIET_START_HOUR = 23;  // 11 PM
const BATCH_QUIET_END_HOUR   = 7;   // 7 AM

// ──────────── Follow-up config ────────────
const FOLLOWUP_MIN_AGE_DAYS = 3;    // Only follow up if initial send is >= 3 days old
const FOLLOWUP_MAX_AGE_DAYS = 21;   // Don't follow up on leads older than 3 weeks
const FOLLOWUP_PER_RUN_CAP  = 10;   // Max follow-ups queued in one click
// Shares BATCH_DAILY_CAP — follow-ups + first-sends together can't exceed 20/day.

// PropertiesService keys
const PROP_QUEUE              = 'batch.queue';        // JSON array of row numbers
const PROP_QUEUE_TOTAL        = 'batch.total';        // initial queue size (for progress)
const PROP_QUEUE_DONE         = 'batch.done';         // number processed so far
const PROP_QUEUE_SENT         = 'batch.sent';         // successful sends
const PROP_QUEUE_SKIPPED      = 'batch.skipped';      // mid-queue skips (failed / blocked)
const PROP_QUEUE_STARTED_AT   = 'batch.startedAt';    // ms epoch — when the queue was kicked off
const PROP_QUEUE_CURRENT_ROW  = 'batch.currentRow';   // row being processed right now
const PROP_QUEUE_CURRENT_EMAIL = 'batch.currentEmail'; // recipient being processed right now
const PROP_QUEUE_LAST_TICK    = 'batch.lastTick';     // ms epoch — last processQueue tick

// ──────────── Blocklist (never email these) ────────────
// Matched case-insensitive as a substring against the Company column.
// Add / remove freely — applies to both single-send and batch-send paths.
const BLOCKED_COMPANIES = [
  'infosys',
  'tcs',
  'tata consultancy',
];

// Matched case-insensitive against the email domain.
const BLOCKED_EMAIL_DOMAINS = [
  'infosys.com',
  'tcs.com',
];

// ──────────── Phrase blocklist (never email these) ────────────
// Matched case-insensitive against post_text / role / tags / notes using a
// word-boundary regex (so "onsite" won't trip on "monsite"). When any
// phrase matches on save, the row's Status is auto-set to "Skipped: {phrase}"
// which excludes it from both the batch-send sweep and single-send path.
//
// Rationale: Jaydip is a solo contractor targeting remote Python / AI-ML
// work. Full-time onsite roles, relocation demands, W2-only recruiters, and
// internships are all mismatches and shouldn't burn a Claude call + Gmail
// send quota. Edit freely.
const BLOCKED_PHRASES = [
  // ── Not-a-job-post signals (candidate / visa seeker / referral-ask) ──
  // These come FIRST because they're the most common noise in LinkedIn
  // search results. A "hiring python" search surfaces lots of candidate
  // posts too ("open to work, Python dev, please refer").
  'open to work',
  'open for opportunities', 'open for new opportunities',
  'open for new roles', 'open for new role',
  'looking for new opportunities', 'looking for new opportunity',
  'looking for a new role', 'looking for new role',
  'looking for my next', 'looking for next role',
  'actively seeking', 'actively looking',
  'seeking new opportunity', 'seeking new role',
  'seeking opportunities', 'in search of opportunities',
  'please refer', 'referral appreciated', 'referrals appreciated',
  'any referral', 'any referrals', 'any leads appreciated',
  'pls refer', 'kindly refer', 'kindly share any',
  'resume attached', 'resume for reference',
  // Visa / sponsorship seekers (candidate-side, not employer-side)
  'need visa sponsorship', 'need h1b', 'need h-1b',
  'need green card', 'need greencard',
  'looking for sponsorship', 'looking for h1b', 'looking for h-1b',
  'looking for visa sponsorship', 'seeking sponsorship',
  'sponsorship needed', 'visa sponsorship needed',
  'on h1b transfer', 'h1b transfer available',

  // ── Employment type — we want contract / freelance only ──
  'full-time', 'full time', 'fulltime',
  'permanent employment', 'permanent role', 'permanent position',
  'w2 only', 'w-2 only', 'w2 employee', 'w2 candidates',
  'no c2c', 'no corp-to-corp', 'no corp to corp',

  // ── Work mode — we want remote ──
  'on-site', 'onsite', 'on site only',
  'in-office', 'in office', 'in the office',
  'must relocate', 'relocate to', 'relocation required',
  '5 days from office', 'days in office',

  // ── Levels we don't pursue ──
  'internship', 'intern position', 'unpaid intern',
];

// Positive signal — a row is only considered a real job post if it matches
// at least one of these employer-side "we're hiring" phrases. Posts with
// no such signal AND no blocked phrase (networking / congratulations /
// resume shares / generic "I'm looking") end up as "Skipped: not a job
// post" and are never emailed. Phase 2 (Claude classification) will
// improve on this; the phrase list covers the obvious high-volume cases.
const JOB_POST_SIGNALS = [
  // "we are hiring" variants
  'hiring', 'we are hiring', "we're hiring", 'we hiring',
  'now hiring', 'urgently hiring',
  // Position-open variants
  'job opening', 'job openings', 'position open', 'positions open',
  'open role', 'open roles', 'open position', 'open positions',
  'position available', 'positions available',
  'current opening', 'current openings', 'current vacancy', 'current vacancies',
  'new opening', 'new openings',
  // Explicit application CTAs
  'apply now', 'apply here', 'apply via', 'apply at', 'apply to',
  'please apply', 'pls apply', 'kindly apply',
  'send your cv', 'send your resume',
  'send cv to', 'send resume to',
  'email your cv', 'email your resume',
  'email me your', 'dm me your',
  'drop your cv', 'drop your resume',
  'interested candidates can', 'interested candidates may', 'interested candidates apply',
  // Role-wanted phrasing
  'needed', 'required for', 'wanted for',
  'developer needed', 'engineer needed',
  // Employer-side "seeking" / "looking for" (candidate-side variants are
  // blocked above as "seeking new role" / "looking for opportunities"
  // which match FIRST — so bare 'seeking' / 'looking for' only end up here
  // for genuine employer posts).
  'seeking a', 'seeking an', 'seeking senior', 'seeking junior',
  'seeking experienced', 'seeking qualified',
  'looking for a', 'looking for an',
  'looking for senior', 'looking for experienced', 'looking for qualified',
  // Team growth
  'join our team', 'join us',
  "we're looking to hire", 'we are looking to hire',
  "we're looking for a", 'we are looking for a',
  "we're looking for an", 'we are looking for an',
];

// True if any job-post signal phrase appears in post_text / role / tags.
function hasJobPostSignal_(row) {
  const haystack = [
    String(row[COL.POST_TEXT] || ''),
    String(row[COL.ROLE]      || ''),
    String(row[COL.TAGS]      || ''),
  ].join('\n').toLowerCase();
  if (!haystack) return false;
  for (let i = 0; i < JOB_POST_SIGNALS.length; i++) {
    const phrase = JOB_POST_SIGNALS[i].toLowerCase();
    if (!phrase) continue;
    const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp('\\b' + escaped + '\\b', 'i');
    if (re.test(haystack)) return true;
  }
  return false;
}

// Payload variant for save-time gating.
function hasJobPostSignalInPayload_(payload) {
  const haystack = [
    String(payload.post_text || ''),
    String(payload.role      || ''),
    String(payload.tags      || ''),
  ].join('\n').toLowerCase();
  if (!haystack) return false;
  for (let i = 0; i < JOB_POST_SIGNALS.length; i++) {
    const phrase = JOB_POST_SIGNALS[i].toLowerCase();
    if (!phrase) continue;
    const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp('\\b' + escaped + '\\b', 'i');
    if (re.test(haystack)) return true;
  }
  return false;
}

// ============================================================
// WEBHOOK ENDPOINTS (extension → sheet)
// ============================================================

function doGet(e) {
  return jsonOut({
    ok: true,
    service: 'Pradip AI Lead Tracker',
    version: '3.13',
    sheets: { main: SHEET_NAME },
    time: new Date().toISOString()
  });
}

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents || '{}');

    // All saves land in the main LinkedIn sheet. Scan-side leads carry
    // tags="bulk-scan" so they can be filtered later, but routing is
    // identical to any other webhook save.

    const sheet = getOrCreateSheet();

    const force   = !!payload.force;
    const postUrl = String(payload.post_url || '').trim();
    const company = String(payload.company || '').trim();
    const role    = String(payload.role || '').trim();
    const emailForDedup = String(payload.email || '').trim();

    if (!force) {
      const dup = findDuplicate(sheet, postUrl, company, role, emailForDedup);
      if (dup) {
        return jsonOut({
          ok: false,
          duplicate: true,
          existingRow: dup.row,
          matchType: dup.matchType,
          matchValue: dup.matchValue,
          message: 'Found an existing row that looks like the same lead.'
        });
      }
    }

    const phoneSafe = safeTextValue(payload.phone);
    const emailSafe = safeTextValue(payload.email);

    // Normalize email_mode — default to individual
    const emailMode = (String(payload.email_mode || 'individual').toLowerCase() === 'company')
      ? 'company'
      : 'individual';

    // Auto-skip decision — TWO gates:
    //   (a) Blocked phrase match → "Skipped: <phrase>"
    //   (b) No job-post signal at all (likely not a real hiring post —
    //       could be networking / visa-seeker / congratulations) →
    //       "Skipped: not a job post"
    // Only applied when caller didn't pass an explicit override status.
    const defaultStatus = payload.status || 'New';
    let blockedPhrase = null;
    let notJobPost = false;
    if (defaultStatus === 'New') {
      blockedPhrase = findBlockedPhraseInPayload_(payload);
      if (!blockedPhrase) {
        notJobPost = !hasJobPostSignalInPayload_(payload);
      }
    }
    const effectiveStatus = blockedPhrase
      ? ('Skipped: ' + blockedPhrase)
      : (notJobPost ? 'Skipped: not a job post' : defaultStatus);

    const rowData = [
      false,                                                        // A 📧 Send
      payload.date_added || new Date().toISOString().slice(0, 10),  // B
      postUrl,                                                      // C
      payload.posted_by || '',                                      // D
      company,                                                      // E
      role,                                                         // F
      payload.tech_stack || '',                                     // G
      payload.rate_budget || '',                                    // H
      payload.location || '',                                       // I
      payload.tags || '',                                           // J
      effectiveStatus,                                              // K
      payload.post_text || '',                                      // L
      payload.notes || '',                                          // M
      emailSafe,                                                    // N
      phoneSafe,                                                    // O
      '',                                                           // P
      '',                                                           // Q
      payload.email_subject || '',                                  // R
      payload.email_body || '',                                     // S
      emailMode,                                                    // T
      ''                                                            // U Jaydip Note (user-filled later)
    ];

    // ── NEW: insert at TOP (row 2, right after the frozen header row) ──
    sheet.insertRowBefore(2);
    const newRow = 2;

    // Write values
    sheet.getRange(newRow, 1, 1, rowData.length).setValues([rowData]);

    // Ensure the checkbox is present on the Send column of the new row
    // (insertRowBefore does not always carry data-validation forward)
    sheet.getRange(newRow, COL.SEND + 1).insertCheckboxes();

    // Enforce text format on email/phone cells (preserves leading +)
    sheet.getRange(newRow, COL.EMAIL + 1).setNumberFormat('@');
    sheet.getRange(newRow, COL.PHONE + 1).setNumberFormat('@');

    return jsonOut({
      ok: true,
      row: newRow,
      sheet: SHEET_NAME,
      forced: force,
      autoSkipped: !!(blockedPhrase || notJobPost),
      autoSkipReason: blockedPhrase || (notJobPost ? 'not a job post' : null)
    });
  } catch (err) {
    return jsonOut({ ok: false, error: String(err) });
  }
}

// ============================================================
// SHEET SETUP HELPERS
// ============================================================

function safeTextValue(v) {
  const s = String(v || '').trim();
  if (!s) return '';
  if (/^[=+\-@]/.test(s)) return "'" + s;
  return s;
}

function getOrCreateSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.insertSheet(SHEET_NAME);

  const lastCol = Math.max(sheet.getLastColumn(), 1);
  const currentHeaders = sheet.getRange(1, 1, 1, lastCol).getValues()[0];

  if (currentHeaders.join('|') !== HEADERS.join('|')) {
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
    sheet.getRange(1, 1, 1, HEADERS.length)
      .setFontWeight('bold').setBackground('#4285F4').setFontColor('#ffffff');
    sheet.setFrozenRows(1);
    sheet.setFrozenColumns(1);
  }

  sheet.getRange(1, COL.EMAIL + 1, sheet.getMaxRows(), 1).setNumberFormat('@');
  sheet.getRange(1, COL.PHONE + 1, sheet.getMaxRows(), 1).setNumberFormat('@');

  const lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.getRange(2, 1, lastRow - 1, 1).insertCheckboxes();
  }

  return sheet;
}

function findDuplicate(sheet, postUrl, company, role, email) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return null;

  const data = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  const urlLower = postUrl.toLowerCase();
  const companyLower = company.toLowerCase();
  const roleLower = role.toLowerCase();
  const emailLower = (email || '').toLowerCase();

  for (let i = 0; i < data.length; i++) {
    const r = data[i];
    const rowPostUrl = String(r[COL.POST_URL] || '').toLowerCase();
    const rowCompany = String(r[COL.COMPANY]  || '').toLowerCase();
    const rowRole    = String(r[COL.ROLE]     || '').toLowerCase();
    const rowEmail   = String(r[COL.EMAIL]    || '').toLowerCase();

    if (urlLower && rowPostUrl && urlLower === rowPostUrl) {
      return { row: i + 2, matchType: 'post_url', matchValue: postUrl };
    }
    if (emailLower && rowEmail && emailLower === rowEmail) {
      return { row: i + 2, matchType: 'email', matchValue: email };
    }
    if (companyLower && roleLower && rowCompany === companyLower && rowRole === roleLower) {
      return { row: i + 2, matchType: 'company + role', matchValue: `${company} / ${role}` };
    }
  }
  return null;
}

function jsonOut(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ============================================================
// SEND EMAIL — onEdit handler
// ============================================================

function onEditInstallable(e) {
  try {
    if (!e || !e.range) return;
    const range = e.range;
    const sheet = range.getSheet();
    // Send checkbox fires only from the main LinkedIn sheet. Dashboard /
    // B2B Leads / any other tab — ignore.
    if (sheet.getName() !== SHEET_NAME) return;
    if (range.getColumn() !== COL.SEND + 1) return;

    const rowNumber = range.getRow();
    if (rowNumber === 1) return;

    const statusCell = sheet.getRange(rowNumber, COL.STATUS + 1);
    const currentStatus = String(statusCell.getValue() || '').trim();
    const alreadySent = (currentStatus === SENT_STATUS);

    // ── LOCK: if this row is already Sent, force checkbox to stay TRUE. ──
    //    No matter what the user does, it cannot be re-sent.
    if (alreadySent) {
      if (e.value !== 'TRUE') {
        range.setValue(true);
        SpreadsheetApp.getActive().toast(
          'This row has already been sent. Checkbox is locked.',
          '🔒 Locked',
          4
        );
      }
      return;
    }

    // Not yet sent — only fire on an actual tick (ignore unticks)
    if (e.value !== 'TRUE') return;

    handleSendCheckbox(sheet, rowNumber);
  } catch (err) {
    SpreadsheetApp.getActive().toast('Error: ' + err.message, '❌ Pradip AI', 10);
  }
}

function handleSendCheckbox(sheet, rowNumber) {
  const ss = SpreadsheetApp.getActive();

  // Daily cap guard for single sends too
  const sentToday = countSentToday(sheet);
  if (sentToday >= BATCH_DAILY_CAP) {
    ss.toast(
      'Daily cap of ' + BATCH_DAILY_CAP + ' emails already reached. Try tomorrow.',
      '🛑 Daily cap', 8
    );
    sheet.getRange(rowNumber, COL.SEND + 1).setValue(false);
    return;
  }

  // Loading toast (only for interactive single sends)
  ss.toast('Preparing with CV…', '📤 Sending email…', -1);

  const result = sendEmailForRow(sheet, rowNumber);

  if (!result.ok) {
    ss.toast(result.error, '❌ Send failed', 10);
    // Checkbox already unticked inside sendEmailForRow on failure
    return;
  }

  ss.toast(
    (result.emailMode === 'company' ? 'BitCoding Solutions pitch' : 'Individual pitch') +
      ' → ' + result.recipient,
    '✅ Email sent',
    5
  );

  showSentModal(result.recipient, result.cvName, result.emailMode, result.subject);
}

/**
 * Pure send logic — shared by handleSendCheckbox (manual) and processQueue (batch).
 * Returns { ok, recipient, subject, emailMode, cvName, error }.
 * No toast / modal / dialog calls here — caller decides UI.
 */
function sendEmailForRow(sheet, rowNumber) {
  // Read full row width (incl. user-added cols V, W… past HEADERS) so the
  // manual-note check below sees them.
  const fullCols = Math.max(sheet.getLastColumn(), HEADERS.length);
  const row = sheet.getRange(rowNumber, 1, 1, fullCols).getValues()[0];

  // Validate email FIRST
  const recipient = String(row[COL.EMAIL] || '').trim();
  if (!recipient || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient)) {
    // No email → mark error and untick
    sheet.getRange(rowNumber, COL.STATUS + 1).setValue('Error: no email');
    sheet.getRange(rowNumber, COL.SEND + 1).setValue(false);
    return { ok: false, error: 'Email column is empty or invalid.' };
  }

  // Blocklist check (company name or email domain)
  const block = blockReasonFor(row);
  if (block) {
    sheet.getRange(rowNumber, COL.STATUS + 1).setValue('Skipped: blocked (' + block + ')');
    sheet.getRange(rowNumber, COL.SEND + 1).setValue(false);
    return { ok: false, error: 'Blocked company/domain — not sent (' + block + ').' };
  }

  // Phrase blocklist — defense in depth for manually-edited rows. Most
  // rows hit this at save time, but if someone flipped Status back to
  // 'New' on a full-time/onsite post, we still refuse to email it.
  const phrase = findBlockedPhraseInRow_(row);
  if (phrase) {
    sheet.getRange(rowNumber, COL.STATUS + 1).setValue('Skipped: ' + phrase);
    sheet.getRange(rowNumber, COL.SEND + 1).setValue(false);
    return { ok: false, error: 'Post matches blocked phrase "' + phrase + '" — not sent.' };
  }

  // Positive "is a job post" check — refuse if the row has no clear
  // employer-side hiring signal. Catches manually-forced rows on
  // networking / visa-seeker / congratulations posts.
  if (!hasJobPostSignal_(row)) {
    sheet.getRange(rowNumber, COL.STATUS + 1).setValue('Skipped: not a job post');
    sheet.getRange(rowNumber, COL.SEND + 1).setValue(false);
    return { ok: false, error: 'No job-post signal detected — not sent.' };
  }

  // Manual notes (call done / rejected / blacklisted etc.) in column U or any
  // column to its right mean "leave this lead alone — it's been handled offline".
  if (hasUserNote(row)) {
    sheet.getRange(rowNumber, COL.STATUS + 1).setValue('Skipped: manual note');
    sheet.getRange(rowNumber, COL.SEND + 1).setValue(false);
    return { ok: false, error: 'Row has a manual note (U/V/W…) — not sent.' };
  }

  // Detect email mode
  const emailMode = (String(row[COL.EMAIL_MODE] || 'individual').toLowerCase() === 'company')
    ? 'company'
    : 'individual';

  // Status → Sending
  const statusCell = sheet.getRange(rowNumber, COL.STATUS + 1);
  const previousStatus = statusCell.getValue();
  statusCell.setValue(SENDING_STATUS);
  SpreadsheetApp.flush();

  // Build subject + body
  let subject = String(row[COL.GEN_SUBJECT] || '').trim();
  let body    = String(row[COL.GEN_BODY]    || '').trim();
  if (!subject || !body) {
    const tpl = buildTemplateEmail(row, emailMode);
    if (!subject) subject = tpl.subject;
    if (!body)    body    = tpl.body;
  }

  const plainBody = buildPlainBody(body, emailMode);
  const htmlBody  = buildHtmlBody(body, emailMode);

  // Pick CV
  let attachments = [];
  let cvName = '';
  try {
    const cv = pickCVForRow(row);
    if (cv) {
      attachments.push(cv.getBlob());
      cvName = cv.getName();
    }
  } catch (e) {
    console.warn('CV attach failed: ' + e.message);
  }

  // SEND
  try {
    const opts = { htmlBody: htmlBody, name: 'Jaydip Nakarani' };
    if (attachments.length) opts.attachments = attachments;
    GmailApp.sendEmail(recipient, subject, plainBody, opts);
  } catch (err) {
    statusCell.setValue('Error: ' + err.message);
    sheet.getRange(rowNumber, COL.SEND + 1).setValue(false);
    return { ok: false, error: 'Gmail error: ' + err.message, recipient: recipient };
  }

  // Stamp success
  sheet.getRange(rowNumber, COL.SENT_AT + 1).setValue(new Date());
  statusCell.setValue(SENT_STATUS);
  sheet.getRange(rowNumber, COL.SEND + 1).setValue(true);

  return {
    ok: true,
    recipient: recipient,
    subject: subject,
    emailMode: emailMode,
    cvName: cvName
  };
}

/**
 * Send a follow-up bump on a row that was previously Sent but has no reply.
 * Keeps Status as 'Sent', stamps Follow-up Date, adds a Notes entry.
 */
function sendFollowupForRow(sheet, rowNumber) {
  const row = sheet.getRange(rowNumber, 1, 1, HEADERS.length).getValues()[0];

  const recipient = String(row[COL.EMAIL] || '').trim();
  if (!recipient || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient)) {
    return { ok: false, error: 'Follow-up: email column empty or invalid.' };
  }

  const originalSubject = String(row[COL.GEN_SUBJECT] || '').trim() || 'Following up';
  const subject = /^re:/i.test(originalSubject) ? originalSubject : ('Re: ' + originalSubject);
  const firstName = String(row[COL.POSTED_BY] || '').trim().split(/\s+/)[0] || 'there';
  const emailMode = (String(row[COL.EMAIL_MODE] || 'individual').toLowerCase() === 'company')
    ? 'company'
    : 'individual';

  const bumpBody = buildFollowupBody(firstName, emailMode);
  const plainBody = buildPlainBody(bumpBody, emailMode);
  const htmlBody  = buildHtmlBody(bumpBody, emailMode);

  // Reuse the same CV
  let attachments = [];
  try {
    const cv = pickCVForRow(row);
    if (cv) attachments.push(cv.getBlob());
  } catch (e) {
    console.warn('CV attach failed on follow-up: ' + e.message);
  }

  try {
    const opts = { htmlBody: htmlBody, name: 'Jaydip Nakarani' };
    if (attachments.length) opts.attachments = attachments;
    GmailApp.sendEmail(recipient, subject, plainBody, opts);
  } catch (err) {
    return { ok: false, error: 'Follow-up Gmail error: ' + err.message, recipient: recipient };
  }

  // Stamp Follow-up Date + prepend Notes entry
  sheet.getRange(rowNumber, COL.FOLLOW + 1).setValue(new Date());
  const notesCell = sheet.getRange(rowNumber, COL.NOTES + 1);
  const prev = String(notesCell.getValue() || '').trim();
  const stamp = Utilities.formatDate(
    new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm'
  );
  const newNote = '[Follow-up sent ' + stamp + ']';
  notesCell.setValue(prev ? (newNote + '\n\n' + prev) : newNote);

  return { ok: true, recipient: recipient, subject: subject, emailMode: emailMode };
}

function buildFollowupBody(firstName, mode) {
  // Short, casual 2-liner — no re-pitch, no rehash
  if (mode === 'company') {
    return (
`Hi ${firstName},

Just bumping my earlier note in case it got buried. Still happy to share relevant case studies and set up a quick call if the timing works on your end.

Best,
Jaydip`);
  }
  return (
`Hi ${firstName},

Just bumping my earlier note in case it got buried. Happy to jump on a quick 15-min call if it's still active on your end.

Best,
Jaydip`);
}

function findFollowupCandidates(sheet) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];

  const fullCols = Math.max(sheet.getLastColumn(), HEADERS.length);
  const data = sheet.getRange(2, 1, lastRow - 1, fullCols).getValues();
  const now = Date.now();
  const minAgeMs = FOLLOWUP_MIN_AGE_DAYS * 24 * 60 * 60 * 1000;
  const maxAgeMs = FOLLOWUP_MAX_AGE_DAYS * 24 * 60 * 60 * 1000;

  const candidates = [];
  for (let i = 0; i < data.length; i++) {
    const row = data[i];
    const status = String(row[COL.STATUS] || '').trim();
    const email  = String(row[COL.EMAIL]  || '').trim();
    const sentAt = row[COL.SENT_AT];
    const follow = row[COL.FOLLOW];

    if (status !== SENT_STATUS) continue;      // skip Replied / Bounced / New
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) continue;
    if (!(sentAt instanceof Date)) continue;
    if (follow instanceof Date) continue;      // already followed up
    if (hasUserNote(row)) continue;            // manual reply / skip
    if (blockReasonFor(row)) continue;

    const age = now - sentAt.getTime();
    if (age < minAgeMs) continue;
    if (age > maxAgeMs) continue;

    candidates.push(i + 2);
  }
  return candidates;
}

function sendAllFollowups() {
  const ui = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  if (!sheet) { ui.alert('Sheet "' + SHEET_NAME + '" not found.'); return; }

  if (isBatchRunning()) {
    ui.alert('⚠ A batch queue is already running. Wait or Stop it first.');
    return;
  }

  if (isQuietHours()) {
    ui.alert('🌙 Quiet hours active. Try again after ' + BATCH_QUIET_END_HOUR + ':00.');
    return;
  }

  const candidates = findFollowupCandidates(sheet);
  if (candidates.length === 0) {
    ui.alert('No follow-up candidates.\n\n' +
             'Criteria: Sent ' + FOLLOWUP_MIN_AGE_DAYS + '–' + FOLLOWUP_MAX_AGE_DAYS +
             ' days ago, no reply, no existing follow-up, no Jaydip note.');
    return;
  }

  const sentToday = countSentToday(sheet);
  const remaining = BATCH_DAILY_CAP - sentToday;
  if (remaining <= 0) {
    ui.alert('🛑 Daily cap reached (' + sentToday + ' / ' + BATCH_DAILY_CAP + '). Try tomorrow.');
    return;
  }

  const batchSize = Math.min(candidates.length, FOLLOWUP_PER_RUN_CAP, remaining);
  const chosen = candidates.slice(0, batchSize);
  const estMinutes = Math.ceil(batchSize * 1.5);

  const resp = ui.alert(
    '📮 Send ' + batchSize + ' follow-ups?',
    'Found ' + candidates.length + ' candidates (sent ' + FOLLOWUP_MIN_AGE_DAYS + '+ days ago, no reply).\n' +
    'Will queue ' + batchSize + ' with 60–90s delay.\n' +
    'Est time: ~' + estMinutes + ' min\n\n' +
    'Today total (incl. follow-ups): ' + sentToday + ' → ' + (sentToday + batchSize) + ' / ' + BATCH_DAILY_CAP + '\n\n' +
    'OK to start, Cancel to abort.',
    ui.ButtonSet.OK_CANCEL
  );
  if (resp !== ui.Button.OK) return;

  // Status stays 'Sent' (don't overwrite) — visual cue comes from Follow-up Date.
  // Follow-ups always operate on the main LinkedIn sheet (extracts are
  // first-touch only).
  saveQueueState(
    chosen.map(function (r) { return { r: r, k: 'followup' }; }),
    batchSize, 0
  );

  ScriptApp.newTrigger('processQueue')
    .timeBased()
    .everyMinutes(BATCH_TRIGGER_EVERY_MIN)
    .create();

  SpreadsheetApp.getActive().toast(
    'Queued ' + batchSize + ' follow-ups. First send in under 1 minute.',
    '📮 Follow-ups started', 8
  );
}

// ============================================================
// BATCH SEND — queue + 1-min time trigger with 60–90s jitter
// ============================================================

/**
 * Menu handler: scan for pending rows, confirm with user, start the queue.
 */
function sendAllPending() {
  const ui = SpreadsheetApp.getUi();
  const ss = SpreadsheetApp.getActive();
  const targetName = SHEET_NAME;
  const sheet = ss.getSheetByName(targetName);
  if (!sheet) {
    ui.alert('Sheet "' + targetName + '" not found.');
    return;
  }

  // Reject if a queue is already running — show LIVE progress so the user
  // can decide whether to wait or stop. Also auto-opens the sidebar so
  // they don't have to dig through the menu.
  if (isBatchRunning()) {
    const p = getQueueProgress();
    const lines = [
      '⚠ A batch send is already running.',
      '',
      '📊 Progress: ' + p.done + ' / ' + p.total + ' (' + p.percent + '%)',
      '   ✅ Sent: ' + p.sent + '   ⏭ Skipped: ' + p.skipped,
      '   ⏳ Pending: ' + p.pending,
      ''
    ];
    if (p.elapsedMs)  lines.push('⏱ Elapsed: ' + formatDurationShort_(p.elapsedMs));
    if (p.etaMs)      lines.push('⏰ ETA: ~' + formatDurationShort_(p.etaMs));
    if (p.currentEmail) lines.push('📨 Now sending: ' + p.currentEmail);
    lines.push('');
    lines.push('Opening live status sidebar — wait, or click "Stop queue" there.');
    ui.alert(lines.join('\n'));
    showQueueStatusSidebar();
    return;
  }

  // Quiet-hour block
  if (isQuietHours()) {
    ui.alert(
      '🌙 Quiet hours active (' + BATCH_QUIET_START_HOUR + ':00–' + BATCH_QUIET_END_HOUR + ':00).\n\n' +
      'Batch sending is paused until ' + BATCH_QUIET_END_HOUR + ':00. Try again later.'
    );
    return;
  }

  // Find pending rows
  const pending = findPendingRows(sheet);
  if (pending.length === 0) {
    ui.alert('No pending rows found.\n\nNothing to send.');
    return;
  }

  // Daily cap enforcement
  const sentToday = countSentToday(sheet);
  const remainingSlots = BATCH_DAILY_CAP - sentToday;
  if (remainingSlots <= 0) {
    ui.alert(
      '🛑 Daily cap reached.\n\n' +
      'Already sent today: ' + sentToday + ' / ' + BATCH_DAILY_CAP + '\n\n' +
      'Try again tomorrow.'
    );
    return;
  }

  // Trim to per-run cap and daily remaining
  const batchSize = Math.min(pending.length, BATCH_PER_RUN_CAP, remainingSlots);
  const queue = pending.slice(0, batchSize);
  const estMinutes = Math.ceil(batchSize * 1.5); // ~60–90s per email

  // Confirmation dialog
  const resp = ui.alert(
    '🚀 Send ' + batchSize + ' pending emails?',
    'Found ' + pending.length + ' pending rows. Will queue ' + batchSize + ' of them.\n\n' +
    'Delay: 60–90 seconds between sends (randomised)\n' +
    'Estimated time: ~' + estMinutes + ' minutes\n\n' +
    'Already sent today: ' + sentToday + ' / ' + BATCH_DAILY_CAP + '\n' +
    'This batch will bring total to: ' + (sentToday + batchSize) + ' / ' + BATCH_DAILY_CAP + '\n\n' +
    'Click OK to start the queue, Cancel to abort.',
    ui.ButtonSet.OK_CANCEL
  );
  if (resp !== ui.Button.OK) return;

  // Mark all queued rows visibly + persist queue
  queue.forEach(function (rowNumber) {
    sheet.getRange(rowNumber, COL.STATUS + 1).setValue(QUEUED_STATUS);
  });
  SpreadsheetApp.flush();

  // Reset all counters and stamp startedAt so the sidebar can compute ETA
  const props = PropertiesService.getDocumentProperties();
  props.setProperty(PROP_QUEUE_STARTED_AT, String(Date.now()));
  props.setProperty(PROP_QUEUE_SENT, '0');
  props.setProperty(PROP_QUEUE_SKIPPED, '0');

  saveQueueState(
    queue.map(function (r) { return { r: r, k: 'send' }; }),
    batchSize, 0
  );

  // Create the 1-min time trigger
  ScriptApp.newTrigger('processQueue')
    .timeBased()
    .everyMinutes(BATCH_TRIGGER_EVERY_MIN)
    .create();

  SpreadsheetApp.getActive().toast(
    'Queued ' + batchSize + ' emails. First send in under 1 minute.',
    '🚀 Batch started', 8
  );

  // Auto-open the live status sidebar — Jaydip can watch progress without
  // hunting in the menu. The sidebar self-refreshes every 3 seconds.
  showQueueStatusSidebar();
}

/**
 * Menu handler: stop the batch queue immediately.
 */
function stopQueue() {
  const ui = SpreadsheetApp.getUi();
  if (!isBatchRunning()) {
    ui.alert('No batch queue is currently running.');
    return;
  }

  deleteBatchTrigger();

  // Reset any rows that were still Queued/Sending back to 'New'.
  // Queue entries are {r: rowNumber, k: 'send' | 'followup'} objects —
  // unpack the row number before touching the sheet.
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  if (sheet) {
    const state = loadQueueState();
    if (state.queue && state.queue.length) {
      state.queue.forEach(function (entry) {
        const rowNumber = (typeof entry === 'object' && entry) ? entry.r : entry;
        if (!rowNumber) return;
        const statusCell = sheet.getRange(rowNumber, COL.STATUS + 1);
        const cur = String(statusCell.getValue() || '').trim();
        if (cur === QUEUED_STATUS || cur === SENDING_STATUS) {
          statusCell.setValue('New');
        }
      });
    }
  }

  clearQueueState();
  SpreadsheetApp.getActive().toast('Batch queue stopped.', '⏹ Stopped', 5);
}

/**
 * Time-trigger handler: process ONE row from the queue.
 */
function processQueue() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  if (!sheet) {
    deleteBatchTrigger();
    clearQueueState();
    return;
  }
  const state = loadQueueState();

  // Quiet hours → skip this tick, keep queue intact, trigger will try again next minute
  if (isQuietHours()) return;

  // Daily cap check
  const sentToday = countSentToday(sheet);
  if (sentToday >= BATCH_DAILY_CAP) {
    SpreadsheetApp.getActive().toast(
      'Daily cap reached mid-queue. Remaining rows will be reset.',
      '🛑 Cap hit', 10
    );
    stopQueue();
    return;
  }

  const queue = state.queue || [];

  // Queue empty → we're done
  if (queue.length === 0) {
    deleteBatchTrigger();
    clearQueueState();
    SpreadsheetApp.getActive().toast(
      'Processed ' + (state.done || 0) + ' / ' + (state.total || 0) + ' emails.',
      '✅ Batch complete', 10
    );
    return;
  }

  // Pop next queue entry. Supports both legacy number format and new
  // typed format {r: rowNumber, k: 'send' | 'followup'}.
  const raw = queue.shift();
  const rowNumber = (typeof raw === 'object' && raw) ? raw.r : raw;
  const kind      = (typeof raw === 'object' && raw && raw.k) ? raw.k : 'send';

  // Surface the row being processed to the live sidebar
  const recipientPreview = String(sheet.getRange(rowNumber, COL.EMAIL + 1).getValue() || '');
  setQueueCurrent_(rowNumber, recipientPreview);

  // Re-verify eligibility per kind
  const currentStatus = String(sheet.getRange(rowNumber, COL.STATUS + 1).getValue() || '').trim();
  if (kind === 'send') {
    if (currentStatus === SENT_STATUS) {
      // Already sent by someone — skip silently
      saveQueueState(queue, state.total, (state.done || 0) + 1);
      incrementQueueCounter_(PROP_QUEUE_SKIPPED);
      clearQueueCurrent_();
      return;
    }
  } else if (kind === 'followup') {
    // Only valid if row is still Sent (not Replied / Bounced) and Follow-up Date not yet set
    const followCell = sheet.getRange(rowNumber, COL.FOLLOW + 1).getValue();
    if (currentStatus !== SENT_STATUS || followCell instanceof Date) {
      saveQueueState(queue, state.total, (state.done || 0) + 1);
      incrementQueueCounter_(PROP_QUEUE_SKIPPED);
      clearQueueCurrent_();
      return;
    }
  }

  // Random pre-send jitter (0–30s) on top of the 60s trigger interval
  const jitter = Math.floor(
    Math.random() * (BATCH_JITTER_MAX_MS - BATCH_JITTER_MIN_MS + 1)
  ) + BATCH_JITTER_MIN_MS;
  if (jitter > 0) Utilities.sleep(jitter);

  // Dispatch based on kind
  const result = (kind === 'followup')
    ? sendFollowupForRow(sheet, rowNumber)
    : sendEmailForRow(sheet, rowNumber);

  // Save updated queue + progress
  saveQueueState(queue, state.total, (state.done || 0) + 1);
  if (result.ok) {
    incrementQueueCounter_(PROP_QUEUE_SENT);
  } else {
    incrementQueueCounter_(PROP_QUEUE_SKIPPED);
  }
  clearQueueCurrent_();

  // Progress toast
  const done = (state.done || 0) + 1;
  const total = state.total || done;
  const label = (kind === 'followup') ? '📮 Follow-up' : '📤 Send';
  if (result.ok) {
    SpreadsheetApp.getActive().toast(
      label + ' ' + done + ' / ' + total + ' → ' + result.recipient,
      '📤 Batch progress', 5
    );
  } else {
    SpreadsheetApp.getActive().toast(
      'Row ' + rowNumber + ' failed: ' + result.error,
      '⚠ Skipped', 6
    );
  }

  // If that was the last one, wrap up right now instead of waiting another minute
  if (queue.length === 0) {
    deleteBatchTrigger();
    const finalSent = Number(PropertiesService.getDocumentProperties()
      .getProperty(PROP_QUEUE_SENT) || 0);
    const finalSkipped = Number(PropertiesService.getDocumentProperties()
      .getProperty(PROP_QUEUE_SKIPPED) || 0);
    clearQueueState();
    SpreadsheetApp.getActive().toast(
      'Done — sent ' + finalSent + ', skipped ' + finalSkipped + ' (' + total + ' total).',
      '✅ Batch complete', 10
    );
  }
}

// Format milliseconds as a short human string: "2m 14s", "47s", "1h 5m".
function formatDurationShort_(ms) {
  if (!ms || ms < 0) return '0s';
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return totalSec + 's';
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) {
    return sec ? (min + 'm ' + sec + 's') : (min + 'm');
  }
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return remMin ? (hr + 'h ' + remMin + 'm') : (hr + 'h');
}

// ──────────── Batch helpers ────────────

function findPendingRows(sheet) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];

  // Read full sheet width so we see user-added cols (V, W…) for note check.
  const fullCols = Math.max(sheet.getLastColumn(), HEADERS.length);
  const data = sheet.getRange(2, 1, lastRow - 1, fullCols).getValues();
  const pending = [];

  for (let i = 0; i < data.length; i++) {
    const row = data[i];
    const status = String(row[COL.STATUS] || '').trim();
    const email = String(row[COL.EMAIL] || '').trim();
    const checkbox = row[COL.SEND];

    // Pending criteria: valid email, checkbox unchecked, not blocked, no
    // manual note, status is 'New'. Skipped / Sent / Replied / Bounced /
    // Error rows are excluded by the status check.
    if (status !== 'New') continue;
    if (checkbox === true) continue;
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) continue;
    if (blockReasonFor(row)) continue;
    if (findBlockedPhraseInRow_(row)) continue;  // phrase blocklist (full-time / onsite / etc.)
    if (!hasJobPostSignal_(row)) continue;       // must look like a real hiring post
    if (hasUserNote(row)) continue;

    pending.push(i + 2); // actual row number in sheet
  }

  return pending;
}

/**
 * Returns true if the row carries a manual note in any column from U onwards.
 * Used as both the "skip from send" guard and the "manual reply" signal in
 * the dashboard. Treat U/V/W… as a free-form notes zone Jaydip can extend.
 */
function hasUserNote(row) {
  for (let i = COL.JAYDIP_NOTE; i < row.length; i++) {
    if (String(row[i] || '').trim()) return true;
  }
  return false;
}

/**
 * Returns the first BLOCKED_PHRASES entry found in the row's post_text /
 * role / tags / notes, or null. Used at SEND time as defense in depth —
 * new saves auto-skip via findBlockedPhraseInPayload_, but if a user
 * manually edits a row back to Status='New', we still block on send.
 */
function findBlockedPhraseInRow_(row) {
  const haystack = [
    String(row[COL.POST_TEXT] || ''),
    String(row[COL.ROLE]      || ''),
    String(row[COL.TAGS]      || ''),
    String(row[COL.NOTES]     || ''),
  ].join('\n').toLowerCase();
  return matchBlockedPhrase_(haystack);
}

/**
 * Same check but against the inbound webhook payload — used during
 * doPost so blocked posts land as "Skipped" instead of "New" from the start.
 */
function findBlockedPhraseInPayload_(payload) {
  const haystack = [
    String(payload.post_text || ''),
    String(payload.role      || ''),
    String(payload.tags      || ''),
    String(payload.notes     || ''),
  ].join('\n').toLowerCase();
  return matchBlockedPhrase_(haystack);
}

function matchBlockedPhrase_(haystackLower) {
  if (!haystackLower) return null;
  for (let i = 0; i < BLOCKED_PHRASES.length; i++) {
    const phrase = BLOCKED_PHRASES[i].toLowerCase();
    if (!phrase) continue;
    // Word-boundary regex so "in office" doesn't match "in officer".
    // Escape regex chars in the phrase, then wrap with \b on both ends.
    const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp('\\b' + escaped + '\\b', 'i');
    if (re.test(haystackLower)) return BLOCKED_PHRASES[i];
  }
  return null;
}

/**
 * Returns a reason string if the row should be skipped (blocked company/domain),
 * or null if the row is OK to email.
 */
function blockReasonFor(row) {
  const company = String(row[COL.COMPANY] || '').toLowerCase();
  const email   = String(row[COL.EMAIL]   || '').toLowerCase();

  for (let i = 0; i < BLOCKED_COMPANIES.length; i++) {
    const needle = BLOCKED_COMPANIES[i].toLowerCase();
    if (needle && company.indexOf(needle) !== -1) {
      return BLOCKED_COMPANIES[i];
    }
  }

  const atIdx = email.lastIndexOf('@');
  if (atIdx !== -1) {
    const domain = email.slice(atIdx + 1);
    for (let j = 0; j < BLOCKED_EMAIL_DOMAINS.length; j++) {
      const d = BLOCKED_EMAIL_DOMAINS[j].toLowerCase();
      if (d && (domain === d || domain.endsWith('.' + d))) {
        return BLOCKED_EMAIL_DOMAINS[j];
      }
    }
  }

  return null;
}

function countSentToday(sheet) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return 0;

  const data = sheet.getRange(2, COL.SENT_AT + 1, lastRow - 1, 1).getValues();
  const tz = Session.getScriptTimeZone();
  const todayKey = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');

  let count = 0;
  for (let i = 0; i < data.length; i++) {
    const v = data[i][0];
    if (v instanceof Date) {
      const k = Utilities.formatDate(v, tz, 'yyyy-MM-dd');
      if (k === todayKey) count++;
    }
  }
  return count;
}

function isQuietHours() {
  const h = Number(Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'H'));
  if (BATCH_QUIET_START_HOUR > BATCH_QUIET_END_HOUR) {
    // Wraps midnight (e.g. 23–7)
    return (h >= BATCH_QUIET_START_HOUR) || (h < BATCH_QUIET_END_HOUR);
  }
  return (h >= BATCH_QUIET_START_HOUR) && (h < BATCH_QUIET_END_HOUR);
}

function isBatchRunning() {
  const triggers = ScriptApp.getProjectTriggers();
  for (let i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'processQueue') return true;
  }
  const state = loadQueueState();
  return !!(state && state.queue && state.queue.length);
}

function deleteBatchTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'processQueue') ScriptApp.deleteTrigger(t);
  });
}

function saveQueueState(queue, total, done) {
  const props = PropertiesService.getDocumentProperties();
  props.setProperty(PROP_QUEUE, JSON.stringify(queue || []));
  props.setProperty(PROP_QUEUE_TOTAL, String(total || 0));
  props.setProperty(PROP_QUEUE_DONE, String(done || 0));
  props.setProperty(PROP_QUEUE_LAST_TICK, String(Date.now()));
}

function loadQueueState() {
  const props = PropertiesService.getDocumentProperties();
  const q = props.getProperty(PROP_QUEUE);
  return {
    queue:        q ? JSON.parse(q) : [],
    total:        Number(props.getProperty(PROP_QUEUE_TOTAL)        || 0),
    done:         Number(props.getProperty(PROP_QUEUE_DONE)         || 0),
    sent:         Number(props.getProperty(PROP_QUEUE_SENT)         || 0),
    skipped:      Number(props.getProperty(PROP_QUEUE_SKIPPED)      || 0),
    startedAt:    Number(props.getProperty(PROP_QUEUE_STARTED_AT)   || 0),
    currentRow:   Number(props.getProperty(PROP_QUEUE_CURRENT_ROW)  || 0),
    currentEmail: String(props.getProperty(PROP_QUEUE_CURRENT_EMAIL) || ''),
    lastTick:     Number(props.getProperty(PROP_QUEUE_LAST_TICK)    || 0)
  };
}

function clearQueueState() {
  const props = PropertiesService.getDocumentProperties();
  props.deleteProperty(PROP_QUEUE);
  props.deleteProperty(PROP_QUEUE_TOTAL);
  props.deleteProperty(PROP_QUEUE_DONE);
  props.deleteProperty(PROP_QUEUE_SENT);
  props.deleteProperty(PROP_QUEUE_SKIPPED);
  props.deleteProperty(PROP_QUEUE_STARTED_AT);
  props.deleteProperty(PROP_QUEUE_CURRENT_ROW);
  props.deleteProperty(PROP_QUEUE_CURRENT_EMAIL);
  props.deleteProperty(PROP_QUEUE_LAST_TICK);
}

// Increment a single counter property without disturbing the rest of state.
function incrementQueueCounter_(propName) {
  const props = PropertiesService.getDocumentProperties();
  const cur = Number(props.getProperty(propName) || 0);
  props.setProperty(propName, String(cur + 1));
}

function setQueueCurrent_(rowNumber, email) {
  const props = PropertiesService.getDocumentProperties();
  props.setProperty(PROP_QUEUE_CURRENT_ROW, String(rowNumber || 0));
  props.setProperty(PROP_QUEUE_CURRENT_EMAIL, String(email || ''));
}

function clearQueueCurrent_() {
  const props = PropertiesService.getDocumentProperties();
  props.deleteProperty(PROP_QUEUE_CURRENT_ROW);
  props.deleteProperty(PROP_QUEUE_CURRENT_EMAIL);
}

/**
 * Public function — called by the sidebar HTML via google.script.run.
 * Returns a JSON-friendly snapshot of queue state plus derived stats
 * (elapsed, ETA, pending, percent). Sidebar polls this every 3 sec.
 */
function getQueueProgress() {
  const state = loadQueueState();
  // isBatchRunning() needs script.scriptapp OAuth scope. The sidebar may
  // load before that scope was authorized this session — if it throws,
  // fall back to inferring "running" from queue-state alone (still
  // accurate since processQueue persists state on every tick).
  let running = false;
  try {
    running = isBatchRunning();
  } catch (e) {
    running = !!(state.queue && state.queue.length);
  }
  const total = state.total || 0;
  const done = state.done || 0;
  const sent = state.sent || 0;
  const skipped = state.skipped || 0;
  const pending = Math.max(0, total - done);
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;

  let elapsedMs = 0;
  let etaMs = 0;
  if (state.startedAt) {
    elapsedMs = Date.now() - state.startedAt;
    if (done > 0 && pending > 0) {
      const perRow = elapsedMs / done;
      etaMs = Math.round(perRow * pending);
    } else if (pending > 0) {
      // No completions yet — fall back to nominal 75s/row (mid of 60-90s)
      etaMs = pending * 75 * 1000;
    }
  }

  return {
    running: running,
    total: total,
    done: done,
    sent: sent,
    skipped: skipped,
    pending: pending,
    percent: percent,
    elapsedMs: elapsedMs,
    etaMs: etaMs,
    currentRow: state.currentRow || 0,
    currentEmail: state.currentEmail || '',
    quietHours: isQuietHours(),
    sentToday: countSentTodayCached_(),
    dailyCap: BATCH_DAILY_CAP
  };
}

// Cached countSentToday for the sidebar — sheet read is expensive to do
// every 3 sec. Cache for 30 sec.
let __sentTodayCache = { value: 0, at: 0 };
function countSentTodayCached_() {
  const now = Date.now();
  if (now - __sentTodayCache.at < 30000) return __sentTodayCache.value;
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  const v = sheet ? countSentToday(sheet) : 0;
  __sentTodayCache = { value: v, at: now };
  return v;
}

/**
 * Menu handler + auto-opener — shows the live queue sidebar.
 */
function showQueueStatusSidebar() {
  const html = HtmlService.createHtmlOutputFromFile('QueueStatus')
    .setTitle('📬 Email Queue')
    .setWidth(320);
  SpreadsheetApp.getUi().showSidebar(html);
}

/**
 * Public function — called by the sidebar's Stop button. Wraps stopQueue
 * in a guard that doesn't throw when no queue is running (sidebar may
 * still be open after the batch completed).
 */
function sidebarStopQueue() {
  if (!isBatchRunning()) {
    return { ok: false, message: 'No queue running.' };
  }
  // Inlined version of stopQueue() WITHOUT the ui.alert (sidebar handles UI)
  deleteBatchTrigger();
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  if (sheet) {
    const state = loadQueueState();
    if (state.queue && state.queue.length) {
      state.queue.forEach(function (entry) {
        const rowNumber = (typeof entry === 'object' && entry) ? entry.r : entry;
        if (!rowNumber) return;
        const statusCell = sheet.getRange(rowNumber, COL.STATUS + 1);
        const cur = String(statusCell.getValue() || '').trim();
        if (cur === QUEUED_STATUS || cur === SENDING_STATUS) {
          statusCell.setValue('New');
        }
      });
    }
  }
  clearQueueState();
  SpreadsheetApp.getActive().toast('Batch queue stopped from sidebar.', '⏹ Stopped', 5);
  return { ok: true };
}

// ============================================================
// REPLY TRACKING — scan Gmail inbox for incoming mail from recipients
// of Sent rows, flip status to Replied + store snippet.
// ============================================================

function checkReplies() {
  const ui = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  if (!sheet) {
    ui.alert('Sheet "' + SHEET_NAME + '" not found.');
    return;
  }

  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    ui.alert('No data rows to scan.');
    return;
  }

  const data = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();

  // Build lookup of sent rows that don't already have a reply or bounce
  const sentRows = [];
  for (let i = 0; i < data.length; i++) {
    const status = String(data[i][COL.STATUS] || '').trim();
    const email  = String(data[i][COL.EMAIL]  || '').trim().toLowerCase();
    const sentAt = data[i][COL.SENT_AT];
    if (status !== SENT_STATUS) continue;   // skip Replied / Bounced / New / Error
    if (!email) continue;
    if (!(sentAt instanceof Date)) continue;
    sentRows.push({ rowNumber: i + 2, email: email, sentAt: sentAt });
  }

  if (sentRows.length === 0) {
    ui.alert('No Sent rows to check.\n\n' +
             '(Rows already marked Replied, Bounced, etc. are skipped.)');
    return;
  }

  const tz = Session.getScriptTimeZone();
  let updated = 0;
  const sampleHits = [];

  sentRows.forEach(function (entry) {
    // Gmail query: messages FROM this address, received AFTER the send date.
    // Using from: (bare address) matches both exact and alias routing.
    const sinceStr = Utilities.formatDate(entry.sentAt, tz, 'yyyy/MM/dd');
    const q = 'from:' + entry.email + ' after:' + sinceStr + ' -in:sent -in:drafts';

    let threads = [];
    try {
      threads = GmailApp.search(q, 0, 5);
    } catch (err) {
      console.warn('Reply search failed for ' + entry.email + ': ' + err.message);
      return;
    }
    if (!threads.length) return;

    // Pick the latest inbound message after the send timestamp
    let latest = null;
    threads.forEach(function (thr) {
      thr.getMessages().forEach(function (msg) {
        if (msg.getFrom().toLowerCase().indexOf(entry.email) === -1) return;
        const d = msg.getDate();
        if (d < entry.sentAt) return;
        if (!latest || d > latest.getDate()) latest = msg;
      });
    });
    if (!latest) return;

    // Flip status + stamp snippet into Notes
    sheet.getRange(entry.rowNumber, COL.STATUS + 1).setValue(REPLIED_STATUS);

    const notesCell = sheet.getRange(entry.rowNumber, COL.NOTES + 1);
    const prevNotes = String(notesCell.getValue() || '').trim();
    const snippet   = (latest.getPlainBody() || '').replace(/\s+/g, ' ').trim().slice(0, 180);
    const stamp     = Utilities.formatDate(latest.getDate(), tz, 'yyyy-MM-dd HH:mm');
    const newNote   = '[Replied ' + stamp + '] ' + snippet;
    notesCell.setValue(prevNotes ? (newNote + '\n\n' + prevNotes) : newNote);

    updated++;
    if (sampleHits.length < 10) {
      sampleHits.push('  Row ' + entry.rowNumber + ' — ' + entry.email);
    }
  });

  ui.alert(
    '📨 Reply scan complete.\n\n' +
    'Sent rows checked: ' + sentRows.length + '\n' +
    'New replies found: ' + updated + '\n\n' +
    (updated
      ? 'Affected rows:\n' + sampleHits.join('\n') +
        (updated > sampleHits.length ? '\n  …and ' + (updated - sampleHits.length) + ' more' : '')
      : 'No new replies since last check.')
  );
}

// ============================================================
// BOUNCE DETECTION — scan Gmail for delivery-failure notices,
// flip matching Sent rows to Bounced.
// ============================================================

function checkBounces() {
  const ui = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  if (!sheet) {
    ui.alert('Sheet "' + SHEET_NAME + '" not found.');
    return;
  }

  // Only scan the last 14 days of bounces (keeps the Gmail search cheap)
  const since = new Date(Date.now() - 14 * 24 * 60 * 60 * 1000);
  const sinceStr = Utilities.formatDate(since, Session.getScriptTimeZone(), 'yyyy/MM/dd');

  const query =
    '(from:mailer-daemon OR from:postmaster OR ' +
    'subject:"Delivery Status Notification" OR ' +
    'subject:"Undelivered Mail Returned" OR ' +
    'subject:"Address not found") after:' + sinceStr;

  let threads;
  try {
    threads = GmailApp.search(query, 0, 50);
  } catch (err) {
    ui.alert('Gmail search failed: ' + err.message);
    return;
  }

  if (!threads.length) {
    ui.alert('No bounce notifications found in the last 14 days. 🎉');
    return;
  }

  // Collect bounced email addresses from each message body
  const bouncedEmails = new Set();
  const emailRe = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g;

  // Best-effort skip-self filter. getActiveUser needs userinfo.email scope
  // which isn't always granted; fall back to "" if unavailable.
  let myEmail = '';
  try { myEmail = (Session.getActiveUser().getEmail() || '').toLowerCase(); } catch (_) {}

  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (msg) {
      const from = String(msg.getFrom() || '').toLowerCase();
      if (from.indexOf('mailer-daemon') === -1 &&
          from.indexOf('postmaster')    === -1 &&
          !/delivery|undeliver|not found/i.test(msg.getSubject() || '')) {
        return;
      }
      const body = msg.getPlainBody() || '';
      const hits = body.match(emailRe) || [];
      hits.forEach(function (addr) {
        const a = addr.toLowerCase();
        if (a === myEmail) return;                                // skip your own address
        if (a.indexOf('mailer-daemon') !== -1) return;
        if (a.indexOf('postmaster')    !== -1) return;
        if (a.indexOf('no-reply')      !== -1) return;
        bouncedEmails.add(a);
      });
    });
  });

  if (bouncedEmails.size === 0) {
    ui.alert('Found ' + threads.length + ' bounce message(s), but could not extract ' +
             'any recipient addresses.');
    return;
  }

  // Walk sheet rows, flip Sent rows whose email matches
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    ui.alert('No data rows to update.');
    return;
  }

  const data = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  const matchedRows = [];

  for (let i = 0; i < data.length; i++) {
    const rowEmail = String(data[i][COL.EMAIL] || '').trim().toLowerCase();
    const status   = String(data[i][COL.STATUS] || '').trim();
    if (!rowEmail) continue;
    if (!bouncedEmails.has(rowEmail)) continue;
    if (status === BOUNCED_STATUS) continue; // already marked

    const rowNumber = i + 2;
    sheet.getRange(rowNumber, COL.STATUS + 1).setValue(BOUNCED_STATUS);
    matchedRows.push({ row: rowNumber, email: rowEmail });
  }

  ui.alert(
    '🔍 Bounce scan complete.\n\n' +
    'Bounce messages scanned: ' + threads.length + '\n' +
    'Unique bounced addresses: ' + bouncedEmails.size + '\n' +
    'Sheet rows marked Bounced: ' + matchedRows.length + '\n\n' +
    (matchedRows.length
      ? 'Affected rows:\n' + matchedRows.slice(0, 10).map(function (m) {
          return '  Row ' + m.row + ' — ' + m.email;
        }).join('\n') + (matchedRows.length > 10 ? '\n  …and ' + (matchedRows.length - 10) + ' more' : '')
      : 'None of the bounced addresses are in your sheet.')
  );
}

/**
 * Shows the "Email sent" confirmation modal.
 */
function showSentModal(recipient, cvName, emailMode, subject) {
  const cvLine = cvName
    ? '📎 CV attached: <b>' + escapeHtml(cvName) + '</b>'
    : '⚠ No CV attached (folder empty or not accessible)';

  const modeLabel = (emailMode === 'company')
    ? '🏢 <b>Company pitch</b> (BitCoding Solutions)'
    : '👤 <b>Individual pitch</b> (Jaydip personal)';

  const html = HtmlService.createHtmlOutput(
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.6;padding:4px 6px;">' +
      '<h2 style="margin:0 0 12px 0;color:#0f9d58;font-size:18px;">✅ Email sent</h2>' +
      '<p style="margin:6px 0;"><b>To:</b> ' + escapeHtml(recipient) + '</p>' +
      '<p style="margin:6px 0;"><b>Subject:</b> ' + escapeHtml(subject) + '</p>' +
      '<p style="margin:6px 0;">' + modeLabel + '</p>' +
      '<p style="margin:6px 0;">' + cvLine + '</p>' +
      '<p style="margin:14px 0 0 0;padding:8px 10px;background:#e8f5e9;border-left:3px solid #0f9d58;color:#444;font-size:12px;">' +
      'Status set to <b>Sent</b>. Checkbox is now <b>locked</b> — this row cannot be re-sent.</p>' +
      '<br>' +
      '<button onclick="google.script.host.close()" ' +
        'style="padding:8px 20px;background:#0f9d58;color:#fff;border:0;border-radius:4px;' +
        'cursor:pointer;font-size:13px;font-weight:500;">Close</button>' +
    '</div>'
  ).setWidth(460).setHeight(340);

  SpreadsheetApp.getUi().showModalDialog(html, '✅ Pradip AI — Email sent');
}

// ============================================================
// HELPERS — templating, HTML escape, CV picker, signature builders
// ============================================================

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function buildTemplateEmail(row, mode) {
  const firstName = String(row[COL.POSTED_BY] || '').trim().split(/\s+/)[0] || 'Hiring Manager';
  const company   = String(row[COL.COMPANY]   || '').trim();
  const role      = String(row[COL.ROLE]      || '').trim() || 'the role you posted';
  const tech      = String(row[COL.TECH]      || '').trim();

  if (mode === 'company') {
    let subject;
    if (company && role) subject = `BitCoding Solutions - Python/AI-ML team for your ${role}`;
    else                 subject = `BitCoding Solutions - Python/AI-ML team for your project`;
    if (subject.length > 80) subject = subject.slice(0, 77) + '...';

    const techHook = tech
      ? `${tech.split(',')[0].trim()} work is one of our core areas.`
      : `Python / AI-ML builds are our core focus.`;

    const body =
`Hi ${firstName},

Saw your post${company ? ' at ' + company : ''} - looks like a clean fit for how we work.

At BitCoding Solutions we run a 30+ engineer Python / AI-ML team. ${techHook} We handle end-to-end builds from data pipelines to deployment.

Happy to share relevant case studies and chat about scope. Got time for a quick call this week?

Best,
Jaydip`;

    return { subject, body };
  }

  // Individual mode (default)
  let subject;
  if (company && role) subject = `${role} - 8+ yrs Python/AI-ML dev`;
  else if (role)       subject = `Python dev 8+ yrs for your ${role} role`;
  else                 subject = `Python / AI-ML dev - interested in your post`;
  if (subject.length > 72) subject = subject.slice(0, 69) + '...';

  const techHook = tech
    ? `The ${tech.split(',')[0].trim()} angle lines up with what I've been doing.`
    : `The tech stack lines up with what I've been doing.`;

  const body =
`Hi ${firstName},

Saw your ${role} post${company ? ' at ' + company : ''}. ${techHook}

Quick background:
- Python / AI-ML, 8+ yrs, based in Surat India, remote contracts
- FastAPI, LangGraph, RAG, multi-agent systems, AWS

Got 15 mins this week for a quick call to see if it's a fit?

Best,
Jaydip`;

  return { subject, body };
}

/**
 * Specialty clusters for CV matching. Keys are internal labels; values list
 * lowercase keywords that appear in either the filename or the haystack
 * (role + tech + tags). If the haystack's top-scoring cluster matches the CV
 * file's top-scoring cluster, that CV gets a big boost — preventing e.g. a
 * Python-only role from accidentally picking the Full-Stack or n8n CV.
 */
const CV_SPECIALTY_PROFILES = {
  python_ai: [
    'python', 'django', 'drf', 'fastapi', 'flask',
    'ai', 'ml', 'machine learning', 'llm', 'gpt', 'openai', 'anthropic', 'claude',
    'langchain', 'langgraph', 'agent', 'agents', 'rag', 'nlp', 'chatbot',
    'huggingface', 'tensorflow', 'pytorch', 'yolo', 'computer vision', 'mlops'
  ],
  fullstack: [
    'full stack', 'fullstack', 'full-stack', 'react', 'nextjs', 'next.js',
    'node', 'nodejs', 'express', 'typescript', 'frontend', 'backend',
    'web app', 'web application', 'mern', 'pern'
  ],
  scraping: [
    'scraping', 'scraper', 'scrapy', 'selenium', 'puppeteer', 'playwright',
    'beautifulsoup', 'lxml', 'xpath', 'data extraction', 'crawler', 'crawling',
    'web scraping', 'data mining'
  ],
  n8n: [
    'n8n', 'zapier', 'make.com', 'integromat', 'workflow automation',
    'low-code', 'no-code', 'integration workflow'
  ]
};

function classifySpecialty(text) {
  const t = String(text || '').toLowerCase();
  if (!t) return { label: null, score: 0 };

  let bestLabel = null;
  let bestScore = 0;

  Object.keys(CV_SPECIALTY_PROFILES).forEach(function (label) {
    const keywords = CV_SPECIALTY_PROFILES[label];
    let score = 0;
    for (let i = 0; i < keywords.length; i++) {
      const k = keywords[i];
      if (t.indexOf(k) !== -1) score++;
    }
    if (score > bestScore) {
      bestScore = score;
      bestLabel = label;
    }
  });

  return { label: bestLabel, score: bestScore };
}

function pickCVForRow(row) {
  let folder;
  try {
    folder = DriveApp.getFolderById(DRIVE_CV_FOLDER_ID);
  } catch (e) {
    console.warn('CV folder not accessible: ' + e.message);
    return null;
  }

  const cvs = [];
  const it = folder.getFiles();
  while (it.hasNext()) {
    const f = it.next();
    const name = f.getName();
    if (/\.(pdf|docx?|doc)$/i.test(name)) cvs.push(f);
  }

  if (cvs.length === 0) return null;
  if (cvs.length === 1) return cvs[0];

  // Haystack = the row's role + tech + tags (what the lead is asking for)
  const haystack = (
    String(row[COL.ROLE] || '') + ' ' +
    String(row[COL.TECH] || '') + ' ' +
    String(row[COL.TAGS] || '') + ' ' +
    String(row[COL.POST_TEXT] || '')
  ).toLowerCase();

  const roleSpecialty = classifySpecialty(haystack);

  const stopWords = new Set([
    'jaydip', 'nakarani', 'cv', 'resume', 'final', 'updated', 'copy',
    'new', 'latest', 'v1', 'v2', 'v3', 'doc', 'pdf', 'docx', 'and', 'for'
  ]);

  let best = null;
  let bestScore = -1;

  for (let i = 0; i < cvs.length; i++) {
    const f = cvs[i];
    const cleanName = f.getName().toLowerCase()
      .replace(/\.(pdf|docx?|doc)$/i, '')
      .replace(/[._\-]+/g, ' ')
      .replace(/[()]/g, ' ');

    let score = 0;

    // (1) Specialty match — big jump if CV's specialty equals role's specialty.
    const cvSpecialty = classifySpecialty(cleanName);
    if (cvSpecialty.label && roleSpecialty.label &&
        cvSpecialty.label === roleSpecialty.label &&
        roleSpecialty.score > 0) {
      score += 100;
    }

    // (2) Token overlap — tie-breaker for same-specialty CVs or for rows with
    //     no clear specialty classification.
    const tokens = cleanName.split(/\s+/)
      .filter(function (t) { return t.length >= 3 && !stopWords.has(t); });
    for (let j = 0; j < tokens.length; j++) {
      if (haystack.indexOf(tokens[j]) !== -1) score += 5;
    }

    // (3) Default fallback — a file called "*default*" or "*general*" is
    //     preferred when no specialty matched at all.
    if (roleSpecialty.score === 0 && /\b(default|general)\b/.test(cleanName)) {
      score += 20;
    }

    if (score > bestScore) {
      bestScore = score;
      best = f;
    }
  }

  return best || cvs[0];
}

function stripClaudeSignOff(body) {
  return String(body || '')
    .replace(/[\r\n]+\s*best[\s,.]*[\r\n]+\s*jaydip[\s\S]*$/i, '')
    .trim();
}

function buildPlainBody(rawBody, mode) {
  const sig = (mode === 'company')
    ? EMAIL_SIGNATURE_COMPANY_TEXT
    : EMAIL_SIGNATURE_INDIVIDUAL_TEXT;
  return stripClaudeSignOff(rawBody) + '\n\n' + sig;
}

function buildHtmlBody(rawBody, mode) {
  const cleaned = stripClaudeSignOff(rawBody);

  const htmlParas = cleaned
    .split(/\n\n+/)
    .map(function (para) {
      return '<p style="margin:0 0 14px 0;font-family:Arial,Helvetica,sans-serif;' +
             'font-size:14px;line-height:1.6;color:#222;">' +
             escapeHtml(para).replace(/\n/g, '<br>') +
             '</p>';
    })
    .join('');

  const sig = (mode === 'company')
    ? EMAIL_SIGNATURE_COMPANY_HTML
    : EMAIL_SIGNATURE_INDIVIDUAL_HTML;

  return '<div style="max-width:600px;padding:4px 0;">' + htmlParas + sig + '</div>';
}

// ============================================================
// MENU + ONE-TIME SETUP
// ============================================================

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('📧 Pradip AI')
    .addItem('1. Setup triggers (run once)', 'setupTriggers')
    .addSeparator()
    .addItem('🚀 Send all pending (60–90s delay)', 'sendAllPending')
    .addItem('📮 Send follow-ups (3+ days no reply)', 'sendAllFollowups')
    .addItem('📊 Queue status (live sidebar)', 'showQueueStatusSidebar')
    .addItem('⏹ Stop batch queue', 'stopQueue')
    .addSeparator()
    .addItem('📨 Check replies', 'checkReplies')
    .addItem('🔍 Check bounced emails', 'checkBounces')
    .addSeparator()
    .addItem('📊 Refresh dashboard', 'buildDashboard')
    .addItem('Rebuild headers + checkboxes', 'rebuildSheet')
    .addToUi();
}

function setupTriggers() {
  // Force Apps Script to grant BOTH compose + send scopes.
  // createDraft grants gmail.compose; sendEmail in handleSendCheckbox
  // causes Apps Script to also request gmail.send during auth.
  try {
    const d = GmailApp.createDraft(
      'auth-test@example.com',
      'Pradip AI auth test',
      'You can delete this draft.'
    );
    d.deleteDraft();
  } catch (e) {
    throw new Error(
      'Gmail permission denied. Rerun Setup triggers and click Allow on ALL permissions, ' +
      'especially "Send email on your behalf" and "Read, compose, send and delete email".'
    );
  }

  // Remove old triggers to prevent duplicates
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'onEditInstallable') {
      ScriptApp.deleteTrigger(t);
    }
  });

  ScriptApp.newTrigger('onEditInstallable')
    .forSpreadsheet(SpreadsheetApp.getActive())
    .onEdit()
    .create();

  SpreadsheetApp.getUi().alert(
    '✅ Setup done + Gmail permissions granted.\n\n' +
    'Now check the 📧 Send box on any row — the email will be SENT immediately ' +
    '(no draft). Once sent, that row\'s checkbox will be locked to prevent re-sends.'
  );
}

function rebuildSheet() {
  getOrCreateSheet();
  SpreadsheetApp.getUi().alert(
    '✅ Headers + checkboxes rebuilt.\n\n' +
    'Note: v3.3 sends email directly (no draft). New rows appear at the top.'
  );
}

// ============================================================
// DASHBOARD — summary stats + top subjects
// ============================================================

const DASHBOARD_SHEET = 'Dashboard';

function buildDashboard() {
  const ss = SpreadsheetApp.getActive();
  const src = ss.getSheetByName(SHEET_NAME);
  if (!src) {
    SpreadsheetApp.getUi().alert('Source sheet "' + SHEET_NAME + '" not found.');
    return;
  }

  const lastRow = src.getLastRow();
  const fullCols = Math.max(src.getLastColumn(), HEADERS.length);
  const data = (lastRow >= 2)
    ? src.getRange(2, 1, lastRow - 1, fullCols).getValues()
    : [];

  // Aggregate
  const tz = Session.getScriptTimeZone();
  const now = new Date();
  const todayKey = Utilities.formatDate(now, tz, 'yyyy-MM-dd');
  const weekStart = new Date(now);
  weekStart.setDate(weekStart.getDate() - 6);
  const monthStart = new Date(now);
  monthStart.setDate(monthStart.getDate() - 29);

  let total = data.length, sent = 0, emailReplied = 0, manualReplied = 0;
  let bounced = 0, newCount = 0;
  let modeIndividual = 0, modeCompany = 0;
  let sentToday = 0, sentThisWeek = 0, sentThisMonth = 0;
  let followedUp = 0;
  const subjectReplyCounts = {}; // subject → { sent, replied }

  for (let i = 0; i < data.length; i++) {
    const r = data[i];
    const status = String(r[COL.STATUS] || '').trim();
    const mode   = String(r[COL.EMAIL_MODE] || '').trim().toLowerCase();
    const sentAt = r[COL.SENT_AT];
    const follow = r[COL.FOLLOW];
    const subject = String(r[COL.GEN_SUBJECT] || '').trim();
    const userNote = hasUserNote(r);

    if (status === SENT_STATUS || status === REPLIED_STATUS || status === BOUNCED_STATUS) sent++;
    if (status === REPLIED_STATUS) emailReplied++;
    // Manual reply / interaction — ANY non-email-replied, non-bounced row that
    // carries a manual note in U/V/W… counts. This includes rows that are
    // technically still "New" or "Skipped" — the note itself proves there was
    // some out-of-band interaction (call, in-person, blacklist decision etc.).
    else if (status !== BOUNCED_STATUS && userNote) manualReplied++;
    if (status === BOUNCED_STATUS) bounced++;
    if (status === 'New') newCount++;
    if (mode === 'company') modeCompany++;
    else modeIndividual++;

    if (sentAt instanceof Date) {
      const k = Utilities.formatDate(sentAt, tz, 'yyyy-MM-dd');
      if (k === todayKey) sentToday++;
      if (sentAt >= weekStart) sentThisWeek++;
      if (sentAt >= monthStart) sentThisMonth++;

      if (subject) {
        if (!subjectReplyCounts[subject]) subjectReplyCounts[subject] = { sent: 0, replied: 0 };
        subjectReplyCounts[subject].sent++;
        if (status === REPLIED_STATUS || (status !== BOUNCED_STATUS && userNote)) {
          subjectReplyCounts[subject].replied++;
        }
      }
    }

    if (follow instanceof Date) followedUp++;
  }

  const replied = emailReplied + manualReplied;
  // Reply-rate denominator: we want a meaningful rate even when manual replies
  // came on rows that were never marked Sent (e.g. status still "New"). Use
  // max(sent, replied) so the rate never exceeds 100%.
  const denom = Math.max(sent, replied);
  const replyRate  = denom > 0 ? (replied / denom * 100) : 0;
  const bounceRate = sent > 0 ? (bounced / sent * 100) : 0;

  // Top subjects by reply count (then by reply rate, tie-broken by sent volume)
  const subjects = Object.keys(subjectReplyCounts).map(function (s) {
    return { subject: s, sent: subjectReplyCounts[s].sent, replied: subjectReplyCounts[s].replied };
  });
  subjects.sort(function (a, b) {
    if (b.replied !== a.replied) return b.replied - a.replied;
    const ra = a.sent ? a.replied / a.sent : 0;
    const rb = b.sent ? b.replied / b.sent : 0;
    if (rb !== ra) return rb - ra;
    return b.sent - a.sent;
  });
  const topSubjects = subjects.slice(0, 10);

  // ── Render the dashboard ──
  let dash = ss.getSheetByName(DASHBOARD_SHEET);
  if (!dash) dash = ss.insertSheet(DASHBOARD_SHEET);
  dash.clear();
  // Wipe any previous merges / formatting so re-renders are clean
  dash.getRange(1, 1, dash.getMaxRows(), dash.getMaxColumns()).breakApart();
  dash.setHiddenGridlines(true);

  const FONT = 'Roboto';
  const COLOR = {
    titleBg: '#1a237e',          // deep indigo
    titleFg: '#ffffff',
    cardBlue:   { bg: '#e3f2fd', fg: '#0d47a1' },
    cardGreen:  { bg: '#e8f5e9', fg: '#1b5e20' },
    cardPurple: { bg: '#f3e5f5', fg: '#4a148c' },
    cardRed:    { bg: '#ffebee', fg: '#b71c1c' },
    cardAmber:  { bg: '#fff8e1', fg: '#e65100' },
    cardTeal:   { bg: '#e0f2f1', fg: '#004d40' },
    sectionBar: '#3949ab',
    sectionFg:  '#ffffff',
    tableHdrBg: '#eceff1',
    tableAltBg: '#fafafa',
    border:     '#cfd8dc',
    muted:      '#9e9e9e'
  };

  // Column widths — 12 cols total grid for cards
  const N_COLS = 12;
  for (let c = 1; c <= N_COLS; c++) dash.setColumnWidth(c, 95);

  // ── Title bar (rows 1) ──
  dash.setRowHeight(1, 56);
  const titleRange = dash.getRange(1, 1, 1, N_COLS).merge();
  titleRange
    .setValue('📊  LinkedIn Lead Dashboard   ·   ' + Utilities.formatDate(now, tz, 'EEEE, dd MMM yyyy · HH:mm'))
    .setBackground(COLOR.titleBg)
    .setFontColor(COLOR.titleFg)
    .setFontFamily(FONT)
    .setFontSize(16)
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle');

  dash.setRowHeight(2, 12); // spacer

  // ── KPI cards (rows 3-5) — 4 cards × 3 cols each ──
  const kpis = [
    { label: 'TOTAL SENT',   value: sent,    color: COLOR.cardBlue },
    { label: 'TOTAL REPLIES (EMAIL + CALL)', value: replied, color: COLOR.cardGreen },
    { label: 'REPLY RATE',   value: replyRate.toFixed(1) + '%',
      color: replyRate >= 5 ? COLOR.cardGreen : COLOR.cardPurple },
    { label: 'BOUNCE RATE',  value: bounceRate.toFixed(1) + '%',
      color: bounceRate >= 5 ? COLOR.cardRed : COLOR.cardTeal }
  ];
  drawCardRow_(dash, 3, kpis, FONT, COLOR.border);

  dash.setRowHeight(6, 12);

  // ── Activity cards (rows 7-9) — 3 cards × 4 cols each ──
  const activity = [
    { label: 'SENT TODAY',      value: sentToday,     color: COLOR.cardAmber },
    { label: 'LAST 7 DAYS',     value: sentThisWeek,  color: COLOR.cardBlue },
    { label: 'LAST 30 DAYS',    value: sentThisMonth, color: COLOR.cardTeal }
  ];
  drawCardRow_(dash, 7, activity, FONT, COLOR.border, 4); // 4 cols per card

  dash.setRowHeight(10, 12);

  // ── Pipeline strip (row 11-12) — single row of mini stats ──
  const pipeline = [
    { label: 'TOTAL LEADS',   value: total,         color: COLOR.cardPurple },
    { label: 'NEW',           value: newCount,      color: COLOR.cardAmber },
    { label: 'EMAIL REPLY',   value: emailReplied,  color: COLOR.cardGreen },
    { label: 'CALL / MANUAL', value: manualReplied, color: COLOR.cardTeal },
    { label: 'BOUNCED',       value: bounced,       color: COLOR.cardRed },
    { label: 'FOLLOWED UP',   value: followedUp,    color: COLOR.cardBlue }
  ];
  drawCardRow_(dash, 11, pipeline, FONT, COLOR.border, 2, true); // 2 cols per card, compact

  dash.setRowHeight(13, 16);

  // ── Top subjects section ──
  const sectionRow = 14;
  dash.setRowHeight(sectionRow, 32);
  const sectionRange = dash.getRange(sectionRow, 1, 1, N_COLS).merge();
  sectionRange
    .setValue('   🏆  TOP SUBJECTS BY REPLIES')
    .setBackground(COLOR.sectionBar)
    .setFontColor(COLOR.sectionFg)
    .setFontFamily(FONT)
    .setFontSize(12)
    .setFontWeight('bold')
    .setHorizontalAlignment('left')
    .setVerticalAlignment('middle');

  const headerRow = sectionRow + 1;
  dash.setRowHeight(headerRow, 28);
  // 4-col table: Subject (8 cols) | Sent (1) | Replied (1) | Reply % (2)
  const tHdrSubject = dash.getRange(headerRow, 1, 1, 8).merge();
  tHdrSubject.setValue('Subject').setBackground(COLOR.tableHdrBg)
    .setFontFamily(FONT).setFontSize(11).setFontWeight('bold')
    .setHorizontalAlignment('left').setVerticalAlignment('middle')
    .setBorder(true, true, true, true, false, false, COLOR.border, SpreadsheetApp.BorderStyle.SOLID);
  dash.getRange(headerRow, 9).setValue('Sent');
  dash.getRange(headerRow, 10).setValue('Replied');
  dash.getRange(headerRow, 11, 1, 2).merge().setValue('Reply %');
  dash.getRange(headerRow, 9, 1, 4)
    .setBackground(COLOR.tableHdrBg)
    .setFontFamily(FONT).setFontSize(11).setFontWeight('bold')
    .setHorizontalAlignment('center').setVerticalAlignment('middle')
    .setBorder(true, true, true, true, false, false, COLOR.border, SpreadsheetApp.BorderStyle.SOLID);

  if (topSubjects.length) {
    for (let i = 0; i < topSubjects.length; i++) {
      const s = topSubjects[i];
      const r = headerRow + 1 + i;
      dash.setRowHeight(r, 26);

      const subjCell = dash.getRange(r, 1, 1, 8).merge();
      subjCell.setValue(s.subject)
        .setFontFamily(FONT).setFontSize(11)
        .setHorizontalAlignment('left').setVerticalAlignment('middle')
        .setWrap(true);

      dash.getRange(r, 9).setValue(s.sent);
      dash.getRange(r, 10).setValue(s.replied);
      const rate = s.sent ? (s.replied / s.sent * 100) : 0;
      const rateCell = dash.getRange(r, 11, 1, 2).merge();
      rateCell.setValue(rate.toFixed(1) + ' %');

      // Conditional rate colour
      let rateBg = '#fafafa', rateFg = '#37474f';
      if (rate >= 20)      { rateBg = COLOR.cardGreen.bg; rateFg = COLOR.cardGreen.fg; }
      else if (rate >= 5)  { rateBg = COLOR.cardBlue.bg;  rateFg = COLOR.cardBlue.fg;  }
      rateCell.setBackground(rateBg).setFontColor(rateFg).setFontWeight('bold');

      const rowBg = (i % 2 === 0) ? '#ffffff' : COLOR.tableAltBg;
      dash.getRange(r, 1, 1, 10).setBackground(rowBg);
      dash.getRange(r, 9, 1, 4)
        .setFontFamily(FONT).setFontSize(11)
        .setHorizontalAlignment('center').setVerticalAlignment('middle');
      dash.getRange(r, 1, 1, N_COLS)
        .setBorder(false, false, true, false, false, false, COLOR.border, SpreadsheetApp.BorderStyle.SOLID_THIN);
    }
  } else {
    const r = headerRow + 1;
    dash.setRowHeight(r, 32);
    dash.getRange(r, 1, 1, N_COLS).merge()
      .setValue('No sent rows with subjects yet — start sending to populate this table.')
      .setFontColor(COLOR.muted)
      .setFontFamily(FONT).setFontSize(11).setFontStyle('italic')
      .setHorizontalAlignment('center').setVerticalAlignment('middle');
  }

  dash.setFrozenRows(1);

  ss.setActiveSheet(dash);
  SpreadsheetApp.getActive().toast(
    total + ' leads · ' + sent + ' sent · ' +
    replied + ' replied (' + emailReplied + ' email + ' + manualReplied + ' call) · ' +
    replyRate.toFixed(1) + '% reply rate',
    '📊 Dashboard refreshed', 7
  );
}

/**
 * Render a horizontal row of KPI cards on the dashboard.
 * Each card spans `colsPerCard` columns (default 3), 2 rows for the big number
 * and 1 row for the label. `compact = true` uses smaller fonts + 1+1 layout.
 */
function drawCardRow_(dash, startRow, cards, font, borderColor, colsPerCard, compact) {
  const cw = colsPerCard || 3;
  const numberRowSpan = compact ? 1 : 2;
  const labelRow = startRow + numberRowSpan;

  // Heights
  if (compact) {
    dash.setRowHeight(startRow, 32);
    dash.setRowHeight(labelRow, 22);
  } else {
    dash.setRowHeight(startRow, 38);
    dash.setRowHeight(startRow + 1, 38);
    dash.setRowHeight(labelRow, 24);
  }

  cards.forEach(function (card, idx) {
    const colStart = 1 + idx * cw;

    // Big number (merged across cw cols × numberRowSpan rows)
    const numRange = dash.getRange(startRow, colStart, numberRowSpan, cw).merge();
    numRange
      .setValue(card.value)
      .setBackground(card.color.bg)
      .setFontColor(card.color.fg)
      .setFontFamily(font)
      .setFontSize(compact ? 18 : 28)
      .setFontWeight('bold')
      .setHorizontalAlignment('center')
      .setVerticalAlignment('middle')
      .setBorder(true, true, false, true, false, false, borderColor, SpreadsheetApp.BorderStyle.SOLID);

    // Label (1 row × cw cols)
    const lblRange = dash.getRange(labelRow, colStart, 1, cw).merge();
    lblRange
      .setValue(card.label)
      .setBackground(card.color.bg)
      .setFontColor(card.color.fg)
      .setFontFamily(font)
      .setFontSize(compact ? 9 : 10)
      .setFontWeight('bold')
      .setHorizontalAlignment('center')
      .setVerticalAlignment('middle')
      .setBorder(false, true, true, true, false, false, borderColor, SpreadsheetApp.BorderStyle.SOLID);
  });
}
