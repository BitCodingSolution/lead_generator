// Safety configuration — tuned to stay well below LinkedIn's automation triggers.
// Two modes:
//   - "max"    → MAXIMUM SAFETY: extension is read-only, user does all actions manually
//   - "normal" → existing assisted flow (navigate + scroll + click)
//
// Default is "max" — explicitly opt into "normal" in Settings.

const SAFETY_MODES = {
  // Read-only assistant — user drives, extension only reads
  max: {
    // Only applies to REPLY generation (which reads LinkedIn DOM).
    // Post extraction has its own lightweight counter for cost tracking only
    // and is NOT rate-limited, because extract never touches LinkedIn.
    DAILY_REPLY_CAP: 20,
    MIN_COOLDOWN_MS: 3 * 60 * 1000, // 3 min
    AUTO_NAVIGATE: false, // don't change the tab URL
    AUTO_SCROLL: false, // don't scroll programmatically
    AUTO_EXPAND: false, // don't click "…more"
    AUTO_PASTE: false, // don't touch the reply box — user copies manually
    LABEL: "🛡 Maximum Safety",
    DESCRIPTION:
      "Read-only. You drive, extension only reads visible content and generates AI replies.",
  },

  // Current assisted flow
  normal: {
    DAILY_REPLY_CAP: 30,
    MIN_COOLDOWN_MS: 90 * 1000,
    AUTO_NAVIGATE: true,
    AUTO_SCROLL: true,
    AUTO_EXPAND: true,
    AUTO_PASTE: true,
    LABEL: "⚡ Normal",
    DESCRIPTION:
      "Assisted. Extension navigates, scrolls, expands '…more', and pastes replies.",
  },
};

// Constants shared by both modes
const SAFETY_COMMON = {
  // Failure backoff
  MAX_CONSECUTIVE_FAILURES: 3,
  FAILURE_COOLDOWN_MS: 10 * 60 * 1000, // 10 min

  // Warning pause — if LinkedIn shows an account-warning banner, stop for 7 days
  WARNING_PAUSE_MS: 7 * 24 * 60 * 60 * 1000,

  // Quiet hours (local time) — no searches at night
  QUIET_START_HOUR: 23, // 11 PM
  QUIET_END_HOUR: 7, // 7 AM

  // Scroll / expand pacing (only applies in Normal mode)
  SCROLL_COUNT: 3,
  SCROLL_STEP_MIN: 600,
  SCROLL_STEP_MAX: 1100,
  SCROLL_DELAY_MIN_MS: 1800,
  SCROLL_DELAY_MAX_MS: 3200,
  EXPAND_CLICK_DELAY_MIN_MS: 150,
  EXPAND_CLICK_DELAY_MAX_MS: 400,
  EXPAND_SETTLE_MIN_MS: 1200,
  EXPAND_SETTLE_MAX_MS: 2200,
  POST_LOAD_WAIT_MIN_MS: 2500,
  POST_LOAD_WAIT_MAX_MS: 4500,
  TAB_LOAD_TIMEOUT_MS: 35 * 1000,
};

function SAFETY_FOR(mode) {
  const m = SAFETY_MODES[mode] || SAFETY_MODES.max;
  return { ...SAFETY_COMMON, ...m, mode: SAFETY_MODES[mode] ? mode : "max" };
}

// Account-warning detection — these phrases indicate LinkedIn flagged the account
const WARNING_PHRASES = [
  "we've restricted your account",
  "your account has been restricted",
  "account is temporarily restricted",
  "temporarily limited",
  "we noticed some unusual activity",
  "we've detected unusual activity",
  "we detected automated activity",
  "verify your identity",
  "please confirm you're not a robot",
  "we restricted your access",
  "your linkedin account has been restricted",
];

// Challenge pages (login/checkpoint/authwall)
const CHALLENGE_SIGNATURES = {
  urlFragments: [
    "checkpoint",
    "authwall",
    "challenge",
    "uas/login",
    "/login",
    "unavailable",
  ],
  titleFragments: [
    "security verification",
    "sign in",
    "unusual activity",
    "let's do a quick security check",
    "linkedin: log in",
  ],
};

if (typeof self !== "undefined") {
  self.SAFETY_MODES = SAFETY_MODES;
  self.SAFETY_COMMON = SAFETY_COMMON;
  self.SAFETY_FOR = SAFETY_FOR;
  self.WARNING_PHRASES = WARNING_PHRASES;
  self.CHALLENGE_SIGNATURES = CHALLENGE_SIGNATURES;
}
