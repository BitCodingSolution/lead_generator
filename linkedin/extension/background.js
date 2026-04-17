// ============================================================
// Pradip AI — Lead Tracker (background service worker)
// Handles message types from popup + content scripts:
//   • Reply tab        → GENERATE_REPLY, REFINE_REPLY, PASTE_REPLY
//   • Bulk scan        → EXTRACT_POST (auto-gen email per scanned lead),
//                         SAVE_TO_SHEET (write row via Apps Script webhook)
//   • Settings         → TEST_SHEET, GET_STATS
//   • content.js       → WARNING_DETECTED (account-warning telemetry)
// ============================================================

importScripts("config.js");

chrome.runtime.onInstalled.addListener(() => {
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel
      .setPanelBehavior({ openPanelOnActionClick: true })
      .catch((err) => console.error("sidePanel setPanelBehavior failed:", err));
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "GET_STATS") {
    getStats()
      .then((stats) => sendResponse({ ok: true, stats }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg && msg.type === "GENERATE_REPLY") {
    handleGenerateReply()
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg && msg.type === "REFINE_REPLY") {
    handleRefineReply(msg.refineType, msg.currentReply)
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg && msg.type === "PASTE_REPLY") {
    handlePasteReply(msg.text)
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg && msg.type === "EXTRACT_POST") {
    handleExtractPost(msg.text, msg.url, msg.styleAngle)
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg && msg.type === "SAVE_TO_SHEET") {
    handleSaveToSheet(msg.payload, !!msg.force)
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg && msg.type === "TEST_SHEET") {
    handleTestSheet()
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (msg && msg.type === "WARNING_DETECTED") {
    recordAccountWarning(msg.phrase || "unknown", sender)
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
});

// ============================================================
// SAFETY STATE
// ============================================================

async function loadSafety() {
  const { safetyMode } = await chrome.storage.local.get(["safetyMode"]);
  return SAFETY_FOR(safetyMode || "max");
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

function isQuietHour() {
  const h = new Date().getHours();
  const start = SAFETY_COMMON.QUIET_START_HOUR;
  const end = SAFETY_COMMON.QUIET_END_HOUR;
  if (start < end) return h >= start && h < end;
  return h >= start || h < end;
}

async function getStats() {
  const safety = await loadSafety();
  const today = todayKey();
  const data = await chrome.storage.local.get([
    "dailyReplyStats",
    "dailyExtractStats",
    "lastAiCallTs",
    "failureState",
    "warningState",
  ]);

  let dailyReplyStats = data.dailyReplyStats || { date: today, count: 0 };
  if (dailyReplyStats.date !== today) {
    dailyReplyStats = { date: today, count: 0 };
  }

  let dailyExtractStats = data.dailyExtractStats || { date: today, count: 0 };
  if (dailyExtractStats.date !== today) {
    dailyExtractStats = { date: today, count: 0 };
  }

  const now = Date.now();
  const lastAiCallTs = data.lastAiCallTs || 0;
  const cooldownRemainMs = Math.max(
    0,
    safety.MIN_COOLDOWN_MS - (now - lastAiCallTs)
  );

  const failureState = data.failureState || { count: 0, untilTs: 0 };
  const failureCooldownRemainMs = Math.max(0, failureState.untilTs - now);

  const warningState = data.warningState || { phrase: "", untilTs: 0 };
  const warningPauseRemainMs = Math.max(0, warningState.untilTs - now);

  return {
    mode: safety.mode,
    modeLabel: safety.LABEL,
    dailyReplyCount: dailyReplyStats.count,
    dailyReplyCap: safety.DAILY_REPLY_CAP,
    dailyExtractCount: dailyExtractStats.count,
    cooldownRemainMs,
    cooldownTotalMs: safety.MIN_COOLDOWN_MS,
    failureCount: failureState.count,
    failureCooldownRemainMs,
    warningPhrase: warningState.phrase,
    warningPauseRemainMs,
    quietHour: isQuietHour(),
    quietStart: SAFETY_COMMON.QUIET_START_HOUR,
    quietEnd: SAFETY_COMMON.QUIET_END_HOUR,
  };
}

/**
 * Full safety preflight — used for REPLY generation only.
 * Reply flow reads LinkedIn DOM → needs full account-safety rules.
 */
async function preflightSafety() {
  const stats = await getStats();

  if (stats.warningPauseRemainMs > 0) {
    const hours = Math.ceil(stats.warningPauseRemainMs / (60 * 60 * 1000));
    throw new Error(
      `⚠ Account warning detected earlier. Paused for ${hours}h. Use LinkedIn manually only.`
    );
  }
  if (stats.quietHour) {
    throw new Error(
      `Quiet hours (${stats.quietStart}:00–${stats.quietEnd}:00 local). Extension rests at night — safer for your account.`
    );
  }
  if (stats.failureCooldownRemainMs > 0) {
    const mins = Math.ceil(stats.failureCooldownRemainMs / 60000);
    throw new Error(
      `Paused due to errors. Try again in ${mins} min (safety cool-down).`
    );
  }
  if (stats.dailyReplyCount >= stats.dailyReplyCap) {
    throw new Error(
      `Daily reply cap reached (${stats.dailyReplyCap}). Resets at midnight.`
    );
  }
}

/**
 * Lightweight preflight — used for POST EXTRACTION.
 * Extract never touches LinkedIn (it's just text → Claude → JSON), so the
 * only safety rule we still care about is: if LinkedIn flagged the account,
 * stay paranoid and avoid all AI calls for a bit. No daily cap, no cooldown,
 * no quiet hours — those only exist to protect the LinkedIn session.
 */
async function preflightExtract() {
  const { warningState } = await chrome.storage.local.get(["warningState"]);
  const ws = warningState || { phrase: "", untilTs: 0 };
  const remainMs = Math.max(0, ws.untilTs - Date.now());
  if (remainMs > 0) {
    const hours = Math.ceil(remainMs / (60 * 60 * 1000));
    throw new Error(
      `⚠ Account warning detected earlier. Paused for ${hours}h (extraction disabled as a precaution).`
    );
  }
}

async function incrementDailyReplyCount() {
  const data = await chrome.storage.local.get(["dailyReplyStats"]);
  const today = todayKey();
  let stats = data.dailyReplyStats || { date: today, count: 0 };
  if (stats.date !== today) stats = { date: today, count: 0 };
  stats.count += 1;
  await chrome.storage.local.set({
    dailyReplyStats: stats,
    lastAiCallTs: Date.now(),
  });
}

/**
 * Cost-tracking counter for Extract. Not rate-limited.
 */
async function incrementDailyExtractCount() {
  const data = await chrome.storage.local.get(["dailyExtractStats"]);
  const today = todayKey();
  let stats = data.dailyExtractStats || { date: today, count: 0 };
  if (stats.date !== today) stats = { date: today, count: 0 };
  stats.count += 1;
  await chrome.storage.local.set({ dailyExtractStats: stats });
}

async function recordFailure() {
  const data = await chrome.storage.local.get(["failureState"]);
  let state = data.failureState || { count: 0, untilTs: 0 };
  state.count += 1;
  if (state.count >= SAFETY_COMMON.MAX_CONSECUTIVE_FAILURES) {
    state.untilTs = Date.now() + SAFETY_COMMON.FAILURE_COOLDOWN_MS;
  }
  await chrome.storage.local.set({ failureState: state });
}

async function recordSuccess() {
  await chrome.storage.local.set({
    failureState: { count: 0, untilTs: 0 },
  });
}

async function recordAccountWarning(phrase, sender) {
  const untilTs = Date.now() + SAFETY_COMMON.WARNING_PAUSE_MS;
  await chrome.storage.local.set({
    warningState: {
      phrase: String(phrase).slice(0, 200),
      at: Date.now(),
      untilTs,
      url: (sender && sender.tab && sender.tab.url) || "",
    },
  });
  console.warn("LinkedIn account warning detected:", phrase);
}

// ============================================================
// MESSAGING TAB HELPERS (find LinkedIn tab, inject content script)
// ============================================================

async function findActiveMessagingTab() {
  const [active] = await chrome.tabs.query({
    active: true,
    currentWindow: true,
  });
  if (active && /linkedin\.com\/messaging/.test(active.url || "")) {
    return active;
  }
  const [msgTab] = await chrome.tabs.query({
    url: "https://www.linkedin.com/messaging/*",
  });
  if (msgTab) return msgTab;

  throw new Error(
    "Open a conversation on linkedin.com/messaging first, then try again."
  );
}

async function ensureContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"],
    });
  } catch (e) {
    console.warn("executeScript note:", e.message);
  }
}

async function sendMessageWithRetry(tabId, message, retries = 4) {
  let lastErr;
  for (let i = 0; i < retries; i++) {
    try {
      return await chrome.tabs.sendMessage(tabId, message);
    } catch (err) {
      lastErr = err;
      await sleep(1500 + rand(0, 700));
    }
  }
  throw lastErr || new Error("Content script not reachable");
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function rand(min, max) {
  return Math.floor(min + Math.random() * (max - min + 1));
}

// ============================================================
// REPLY FLOW
// ============================================================

async function handleGenerateReply() {
  await preflightSafety();

  const settings = await chrome.storage.local.get([
    "claudeApiKey",
    "replyStyle",
    "claudeBackend",
    "bridgeUrl",
  ]);
  const backend = settings.claudeBackend || "bridge";
  const style = settings.replyStyle || {
    signOff: "Best, Jaydip",
    tone: "professional-friendly",
    maxLines: "2-4",
  };

  const tab = await findActiveMessagingTab();
  await ensureContentScript(tab.id);
  await sleep(400);

  const readResp = await sendMessageWithRetry(
    tab.id,
    { type: "READ_CONVERSATION" },
    3
  );
  if (!readResp || !readResp.ok) {
    await recordFailure();
    throw new Error(
      (readResp && readResp.error) || "Could not read the conversation."
    );
  }

  const convo = readResp.conversation;
  if (!convo || !convo.messages || !convo.messages.length) {
    await recordFailure();
    throw new Error("No messages found in the open conversation.");
  }

  let reply;
  if (backend === "bridge") {
    const bridgeUrl = settings.bridgeUrl || "http://127.0.0.1:8765";
    reply = await callBridgeRaw(
      bridgeUrl,
      buildReplySystemPrompt(style),
      buildReplyUserPrompt(convo)
    );
  } else {
    const apiKey = settings.claudeApiKey;
    if (!apiKey) {
      throw new Error(
        "Direct API selected but no key set. Go to Settings and save an API key, " +
          "or switch to 'Local Bridge' backend."
      );
    }
    reply = await callClaudeRaw(
      apiKey,
      buildReplySystemPrompt(style),
      buildReplyUserPrompt(convo)
    );
  }

  await incrementDailyReplyCount();
  await recordSuccess();

  const safety = await loadSafety();
  return {
    reply,
    participantName: convo.participantName || "",
    autoPasteAllowed: !!safety.AUTO_PASTE,
    backend,
  };
}

const REFINE_INSTRUCTIONS = {
  shorter:
    "Make the reply SHORTER. Cut to 1–2 lines. Drop everything that isn't essential. Keep the same intent and the sign-off.",
  casual:
    "Make it MORE CASUAL. Looser word choices. Use a contraction or two more. Drop any remaining stiffness.",
  formal:
    "Make it MORE FORMAL but still natural. Slightly more polished, no slang. Don't go corporate or AI-ish.",
  ask_call:
    "Rewrite so the reply ENDS with a clear ask for a quick call (15 mins). Keep it casual and short.",
  ask_budget:
    "Rewrite so the reply asks ONE clarifying question about budget or rate. Stay polite and short.",
  ask_clarify:
    "Rewrite so the reply asks ONE clear clarifying question (about scope, tech stack, or timeline — pick whichever is most missing in their message). Keep it short.",
};

async function handleRefineReply(refineType, currentReply) {
  await preflightSafety();

  if (!currentReply || !currentReply.trim()) {
    throw new Error("No current reply to refine.");
  }

  const instruction = REFINE_INSTRUCTIONS[refineType];
  if (!instruction) {
    throw new Error(`Unknown refine type: ${refineType}`);
  }

  const settings = await chrome.storage.local.get([
    "claudeApiKey",
    "replyStyle",
    "claudeBackend",
    "bridgeUrl",
  ]);
  const backend = settings.claudeBackend || "bridge";
  const style = settings.replyStyle || {
    signOff: "Best, Jaydip",
    tone: "professional-friendly",
    maxLines: "2-4",
  };

  const tab = await findActiveMessagingTab();
  await ensureContentScript(tab.id);
  await sleep(300);

  const readResp = await sendMessageWithRetry(
    tab.id,
    { type: "READ_CONVERSATION" },
    3
  );
  if (!readResp || !readResp.ok) {
    throw new Error(
      (readResp && readResp.error) || "Could not read the conversation."
    );
  }
  const convo = readResp.conversation;

  const userPrompt =
    buildReplyUserPrompt(convo) +
    `\n\n---\n\nMy CURRENT DRAFT reply:\n"""\n${currentReply}\n"""\n\n` +
    `REFINEMENT INSTRUCTION: ${instruction}\n\n` +
    `Output ONLY the refined reply body. Keep the sign-off line exactly: ${style.signOff}`;

  let refined;
  if (backend === "bridge") {
    const bridgeUrl = settings.bridgeUrl || "http://127.0.0.1:8765";
    refined = await callBridgeRaw(
      bridgeUrl,
      buildReplySystemPrompt(style),
      userPrompt
    );
  } else {
    const apiKey = settings.claudeApiKey;
    if (!apiKey) {
      throw new Error("Direct API selected but no key set.");
    }
    refined = await callClaudeRaw(
      apiKey,
      buildReplySystemPrompt(style),
      userPrompt
    );
  }

  await incrementDailyReplyCount();
  return { reply: refined, refineType };
}

async function handlePasteReply(text) {
  if (!text) throw new Error("Empty text");

  const safety = await loadSafety();
  if (!safety.AUTO_PASTE) {
    throw new Error(
      "Auto-paste is disabled in Maximum Safety mode. Use Copy instead and paste manually."
    );
  }

  const tab = await findActiveMessagingTab();
  await ensureContentScript(tab.id);
  await sleep(300);

  const resp = await sendMessageWithRetry(
    tab.id,
    { type: "PASTE_REPLY", text },
    3
  );
  if (!resp || !resp.ok) {
    throw new Error((resp && resp.error) || "Paste failed");
  }
}

// --- Reply prompt builders -----------------------------------

function buildReplySystemPrompt(style) {
  return (
    `You are writing AS Jaydip Nakarani (Senior Python / AI-ML Developer, 8+ years, based in Surat, India). ` +
    `You reply to LinkedIn messages IN JAYDIP'S VOICE. The reply MUST sound like a real busy engineer typed it ` +
    `in 30 seconds — never like an AI wrote it.\n\n` +
    `Jaydip's stack (mention only if directly relevant, don't list everything): Python, Django, FastAPI, ` +
    `LangGraph, OpenAI, Claude, multi-agent systems, RAG, web scraping, automation, YOLOv8, NLP, AWS, Docker. ` +
    `Open to remote contracts.\n\n` +
    `=====================================================\n` +
    `STEP 1 — SILENTLY CLASSIFY the conversation into ONE of these types:\n` +
    `=====================================================\n` +
    `1. COLD_OUTREACH — a recruiter / client is pitching a role or opportunity for the first time\n` +
    `2. FOLLOW_UP_BUMP — Jaydip's last message got no reply; this is a gentle nudge\n` +
    `3. QUESTION — they asked a specific direct question that needs an answer\n` +
    `4. RATE_TALK — contract rate / budget numbers are being discussed\n` +
    `5. SCHEDULING — setting up a call/meeting/interview time\n` +
    `6. REJECTION — they declined, OR Jaydip needs a polite exit\n` +
    `7. GENERAL — none of the above; plain social/intro message\n\n` +
    `=====================================================\n` +
    `STEP 2 — WRITE THE REPLY using the pattern for that type:\n` +
    `=====================================================\n\n` +
    `### COLD_OUTREACH pattern\n` +
    `- 2–3 lines max\n` +
    `- One clause confirming relevant experience (pick only what matches their ask)\n` +
    `- End with EITHER a call request OR ONE clarifying question (tech stack / budget / timeline)\n` +
    `- Example: "Hey Morgan, thanks for the note. I've done a lot of multi-agent / LangGraph work, sounds close to what you need. Got 15 mins this week for a quick call?"\n\n` +
    `### FOLLOW_UP_BUMP pattern\n` +
    `- 1–2 lines ONLY, casual, not pushy\n` +
    `- Don't re-pitch. Just nudge.\n` +
    `- Example: "Hi René, just bumping this up. Still open on my side if the role's active — let me know."\n\n` +
    `### QUESTION pattern\n` +
    `- Direct answer in 1–2 lines\n` +
    `- Don't over-explain. Ask one follow-up only if needed.\n` +
    `- Example: "Yeah, I can start next Monday. Contract or FT — either works."\n\n` +
    `### RATE_TALK pattern\n` +
    `- Acknowledge the number lightly, don't accept or reject in first reply\n` +
    `- Redirect to a call to discuss scope\n` +
    `- Example: "That range works for me to explore. Quick call to align on scope?"\n\n` +
    `### SCHEDULING pattern\n` +
    `- Give 2 concrete time options OR accept one they offered\n` +
    `- Include timezone (IST) if crossing zones\n` +
    `- Example: "Works for me. Tue or Wed, 4–6 PM IST? Or pick whatever suits you."\n\n` +
    `### REJECTION pattern\n` +
    `- Gracious, short, door open for future\n` +
    `- NO defensive talk, NO over-explaining\n` +
    `- Example: "Understood, thanks for circling back. Keep me in mind if something else opens up."\n\n` +
    `### GENERAL pattern\n` +
    `- Match their length + tone\n` +
    `- Stay natural and conversational\n\n` +
    `=====================================================\n` +
    `HARD RULES (all types) — ABSOLUTE, no exceptions:\n` +
    `=====================================================\n` +
    `- Output ONLY the reply body. No preamble, no type label, no quotes, no subject line.\n` +
    `- LENGTH HARD CAP: MAX 4 short lines total. Target 2–3 lines. If in doubt, cut.\n` +
    `  The user-configured maxLines=${style.maxLines} is the upper bound, but 4 is the absolute ceiling.\n` +
    `- Tone: ${style.tone}, but NATURAL + DIRECT always wins.\n` +
    `- Language: English only. NO emojis. NO hashtags.\n` +
    `- End with exactly this on its own line: ${style.signOff}\n\n` +

    `================================================================\n` +
    `MULTI-QUESTION / RECRUITER-INTAKE PATTERN — critical\n` +
    `================================================================\n` +
    `If the incoming message contains 3+ intake-style questions (e.g. "Total Experience, ` +
    `Current CTC, Expected CTC, Notice period, Current Location, Preferred Location"), ` +
    `DO NOT answer them one by one. That ALWAYS reads AI-generated and takes forever.\n\n` +
    `Instead, reply in 2–3 casual lines:\n` +
    `  • One line acknowledging their ask\n` +
    `  • One line offering a quick call OR saying you'll share details 1:1\n` +
    `  • Optional: mention notice or availability in passing if it's the key blocker\n\n` +
    `EXAMPLE (good): "Hey Chetan, LangGraph / multi-agent is my core area — 8+ yrs Python. ` +
    `Happy to share CTC + notice on a quick call. Free this week?"\n\n` +
    `EXAMPLE (BAD — never do this):\n` +
    `  "Total Experience in Python: 8+ years\n` +
    `  Relevant Experience: ...\n` +
    `  Current Location: Surat\n` +
    `  Current CTC: ...\n` +
    `  Notice: ..."\n` +
    `Reason: structured lists + labelled fields in a DM are the #1 AI-generated giveaway.\n\n` +

    `================================================================\n` +
    `FORMATTING BANS (hard fails):\n` +
    `================================================================\n` +
    `- NO numbered lists (1. 2. 3.)\n` +
    `- NO bulleted lists (•, -, *)\n` +
    `- NO label:value pairs ("Current CTC: 20L", "Notice period: 30 days")\n` +
    `- NO "Total / Relevant / Current / Expected" column-style answers\n` +
    `- NO headings or section breaks\n` +
    `- NO em dashes (—). Use a period or a comma.\n` +
    `- NO markdown (no **bold**, no *italic*, no backticks)\n\n` +

    `HUMAN STYLE: USE CONTRACTIONS (I'm, don't, it's). VARY SENTENCE LENGTH. BE DIRECT — ` +
    `skip "I hope you're doing well". Don't acknowledge-then-answer-then-sign-off ceremoniously.\n\n` +

    `BANNED PHRASES (instantly scream AI):\n` +
    `"I hope this message finds you well", "I'd be delighted/love to/be happy to", "That sounds great", ` +
    `"Absolutely!", "Thank you for reaching out", "I look forward to hearing from you", ` +
    `"Please let me know if you have any questions", "At your earliest convenience", ` +
    `"Please find below", "As per your request".\n\n` +

    `FINAL CHECK before emitting: count the lines. If > 4, rewrite shorter. ` +
    `If any numbered/bulleted/label-value pattern appears, rewrite as flowing sentences.\n\n` +
    `Now classify silently, pick the right pattern, output the reply body only.\n`
  );
}

function buildReplyUserPrompt(convo) {
  const conversationText = convo.messages
    .slice(-15)
    .map(
      (m) => `[${m.sender || "Unknown"}]: ${(m.text || "").slice(0, 1200)}`
    )
    .join("\n\n");

  return (
    `Participant: ${convo.participantName || "Unknown"}\n\n` +
    `Conversation so far (oldest to newest):\n\n${conversationText}\n\n` +
    `Write Jaydip's next reply now.`
  );
}

// ============================================================
// POST EXTRACTION FLOW
// ============================================================

async function handleExtractPost(rawText, postUrl, styleAngle) {
  // Extract never touches LinkedIn — only a minimal warning-pause check applies.
  await preflightExtract();

  if (!rawText || !rawText.trim()) {
    throw new Error("Post text is empty.");
  }

  const settings = await chrome.storage.local.get([
    "claudeApiKey",
    "claudeBackend",
    "bridgeUrl",
  ]);
  const backend = settings.claudeBackend || "bridge";

  const systemPrompt = buildExtractSystemPrompt();
  const userPrompt = buildExtractUserPrompt(rawText, postUrl, styleAngle);

  const callOnce = async (sysPrompt, usrPrompt) => {
    if (backend === "bridge") {
      const bridgeUrl = settings.bridgeUrl || "http://127.0.0.1:8765";
      return callBridgeRaw(bridgeUrl, sysPrompt, usrPrompt);
    }
    const apiKey = settings.claudeApiKey;
    if (!apiKey) {
      throw new Error(
        "Direct API selected but no key set. Set one in Settings or switch to Local Bridge."
      );
    }
    return callClaudeRaw(apiKey, sysPrompt, usrPrompt);
  };

  // First attempt
  let raw = await callOnce(systemPrompt, userPrompt);
  let data;
  try {
    data = parseExtractedJson(raw);
  } catch (firstErr) {
    // Retry once with a stricter prompt that shows Claude its bad output
    console.warn("[Pradip AI] First extraction parse failed, retrying:", firstErr.message);
    const retrySystem =
      systemPrompt +
      `\n\nCRITICAL RETRY: Your previous response could not be parsed as JSON. ` +
      `Return ONLY the raw JSON object, starting with { and ending with }. ` +
      `NO code fences, NO markdown, NO explanation, NO "here is the JSON" text. ` +
      `Just the JSON object, nothing before or after.`;
    const retryUser =
      userPrompt +
      `\n\n(Your previous response was invalid JSON. First 200 chars of it: ` +
      `"""${(raw || "").slice(0, 200)}""")\n\nTry again. Output ONLY valid JSON.`;

    raw = await callOnce(retrySystem, retryUser);
    try {
      data = parseExtractedJson(raw);
    } catch (secondErr) {
      // Don't trigger the reply failure cool-down for extract failures —
      // those are about LinkedIn account health, not Claude parse issues.
      throw new Error(
        `Extraction failed twice. First error: ${firstErr.message}. Second error: ${secondErr.message}`
      );
    }
  }

  await incrementDailyExtractCount();
  return { data };
}

function buildExtractSystemPrompt() {
  return (
    `You are an information extractor, email-mode classifier, AND email drafter for ` +
    `a LinkedIn lead tracker used by Jaydip Nakarani.\n\n` +

    `Jaydip has TWO distinct identities for outreach:\n\n` +

    `**INDIVIDUAL mode** (default — 90%+ of LinkedIn posts):\n` +
    `- Senior Python / AI-ML Developer, 8+ years, Surat India, remote contracts\n` +
    `- Applies personally as a solo contractor\n` +
    `- Voice: "I" language\n\n` +

    `**COMPANY mode** (only when post signals collaboration / team work):\n` +
    `- Represents BitCoding Solutions Pvt Ltd (https://bitcodingsolutions.com/)\n` +
    `- Jaydip's position in this mode: Co-Founder & CTO\n` +
    `- Pitches a 30+ engineer team (Python / AI-ML / automation / multi-agent / RAG)\n` +
    `- Voice: "we/our" language\n\n` +

    `Return ONLY a JSON object (no prose, no markdown, no code fences). Schema:\n` +
    `{\n` +
    `  "posted_by":     "person's full name who posted (e.g. Kavita S)",\n` +
    `  "company":       "company name if mentioned",\n` +
    `  "role":          "job title / role",\n` +
    `  "tech_stack":    "comma-separated key technologies",\n` +
    `  "rate_budget":   "rate or budget if mentioned, else ''",\n` +
    `  "location":      "Remote / city / country if mentioned",\n` +
    `  "email":         "first email address in the post",\n` +
    `  "phone":         "first phone number in the post",\n` +
    `  "tags":          "comma-separated short filterable tags, max 8",\n` +
    `  "notes":         "1 short sentence summary",\n` +
    `  "email_mode":    "individual OR company — see classification rules",\n` +
    `  "email_subject": "subject matching chosen mode",\n` +
    `  "email_body":    "body matching chosen mode",\n` +
    `  "should_skip":   "true/false — set true ONLY when this post is fundamentally unfit for Jaydip (see strict rules below). Default false.",\n` +
    `  "skip_reason":   "short phrase (2-6 words) describing why — e.g. 'not a job post', 'onsite only', 'full-time only no contract', 'W2 only', 'internship', 'tech mismatch'. Empty string when should_skip is false."\n` +
    `}\n\n` +

    `================================================================\n` +
    `STEP 0 — SHOULD_SKIP DECISION (do this FIRST, it gates everything)\n` +
    `================================================================\n\n` +

    `Jaydip is a solo senior Python / AI-ML contractor (also runs BitCoding). ` +
    `He needs REMOTE CONTRACT work. Your job is to decide whether outreach on ` +
    `this post would be a waste of his time.\n\n` +

    `DEFAULT: should_skip = false. When in doubt, DO NOT skip. Jaydip would ` +
    `rather review a borderline post himself than have a real lead thrown away.\n\n` +

    `Set should_skip = TRUE only when ONE of these is CLEARLY true:\n\n` +

    `  A) NOT A JOB POST. The post is a candidate "open to work" / referral ` +
    `     ask / visa-seeker / networking / congrats / generic motivational ` +
    `     post. No employer is hiring here.\n` +
    `     skip_reason: "not a job post"\n\n` +

    `  B) STRICTLY ONSITE. Post explicitly says onsite-only / in-office only / ` +
    `     "must relocate" AND makes NO mention of remote, hybrid, or work-from-home ` +
    `     as an option. If the post mentions "hybrid" or "remote optional" or ` +
    `     "flexible" → NOT a skip, Jaydip can negotiate.\n` +
    `     skip_reason: "onsite only"\n\n` +

    `  C) FULL-TIME ONLY, NO CONTRACT. Post explicitly rules out contract / ` +
    `     freelance / C2C — e.g. "full-time employees only, no contractors", ` +
    `     "permanent position only, no C2C". If the post mentions full-time ` +
    `     AS ONE OPTION among others (contract / freelance / C2C also listed, ` +
    `     even as "open to") → NOT a skip.\n` +
    `     skip_reason: "full-time only no contract"\n\n` +

    `  D) W2-ONLY RECRUITER. Post explicitly says "W2 only" / "W2 candidates ` +
    `     only" / "no C2C". Jaydip is India-based so W2 is impossible.\n` +
    `     skip_reason: "W2 only"\n\n` +

    `  E) INTERNSHIP / JUNIOR / UNPAID. Post is for an intern, unpaid role, ` +
    `     or fresher (0-2 yrs). Jaydip is 8+ yrs, huge mismatch.\n` +
    `     skip_reason: "internship" or "too junior"\n\n` +

    `  F) HARD LOCATION LOCK. Post requires physical presence in a specific ` +
    `     country/city AND explicitly rules out remote (e.g. "must be based in ` +
    `     US, no remote", "local candidates only in Berlin, no remote"). ` +
    `     Silence on remote → NOT a skip.\n` +
    `     skip_reason: "location locked"\n\n` +

    `  G) FUNDAMENTAL TECH MISMATCH. Role is clearly in a stack Jaydip does ` +
    `     NOT work with AND there's no Python/AI/ML/automation/scraping/RAG ` +
    `     overlap at all — e.g. pure Salesforce admin, .NET/C# only, pure ` +
    `     mainframe COBOL, pure SAP FICO. Python + anything-else is NOT a skip.\n` +
    `     skip_reason: "tech mismatch"\n\n` +

    `NEGATION-AWARE: If you see a phrase like "full-time" or "onsite" in the ` +
    `post, READ THE SURROUNDING CONTEXT. "Not looking for full-time only ` +
    `candidates" or "no onsite required" means the opposite — DON'T skip.\n\n` +

    `If should_skip is true, still fill in all extraction fields (company, role, ` +
    `etc.) normally. Also still produce a non-empty email_subject and email_body ` +
    `— Jaydip may manually override the skip later.\n\n` +

    `================================================================\n` +
    `STEP 1 — CLASSIFY email_mode (this is the most important step)\n` +
    `================================================================\n\n` +

    `DEFAULT = "individual". LinkedIn is flooded with recruiter posts hiring ONE ` +
    `developer for ONE role. That's always individual mode.\n\n` +

    `Switch to "company" ONLY when ONE OR MORE of these strong signals appear:\n\n` +

    `1. Explicit B2B keywords in the post: "partner", "partnership", "agency", ` +
    `"vendor", "dev shop", "consulting firm", "outsource", "subcontract", ` +
    `"development partner", "service provider", "white-label", "dedicated team", ` +
    `"bring your team", "need a team"\n\n` +

    `2. Post asks for MULTIPLE developers at once: "team of N engineers", ` +
    `"need 3-5 devs", "multiple positions", "build out a team"\n\n` +

    `3. Post is from a CTO / Founder / VP describing a FULL PRODUCT BUILD ` +
    `requiring multiple tech stacks (end-to-end work that clearly needs a team, ` +
    `not a solo dev). Example: "need someone to build our data platform + ML ` +
    `pipelines + frontend dashboard"\n\n` +

    `4. Post explicitly says "looking for agency" / "looking for consultancy" / ` +
    `"looking for a development partner"\n\n` +

    `5. Post describes an ongoing long-term partnership (multi-month/year ` +
    `engagement with a dedicated team)\n\n` +

    `If NONE of the above are clearly present → email_mode = "individual".\n` +
    `If ANY of the above are clearly present → email_mode = "company".\n` +
    `When unsure → "individual" (safer default — recruiters hate agency spam).\n\n` +

    `================================================================\n` +
    `STEP 2 — EXTRACTION RULES\n` +
    `================================================================\n` +
    `- Use "" for fields not clearly stated. Don't invent values.\n` +
    `- Normalize tech_stack: "AI/ML, Python, AWS" not "ai ml python aws".\n` +
    `- For tags, include short lowercase filter tags: seniority, work mode, ` +
    `employment type, urgency, 2-3 key tech tags. Max 8 tags total.\n` +
    `- For notes, be neutral and factual in 1 sentence.\n\n` +

    `================================================================\n` +
    `STEP 3 — EMAIL DRAFT RULES (must sound like a real human typed it)\n` +
    `================================================================\n\n` +

    `#### INDIVIDUAL MODE (email_mode = "individual") ####\n\n` +

    `Positioning: Jaydip personally, solo contractor. "I" language throughout.\n` +
    `Relevant stack to mention ONLY if matching: Python, FastAPI, Django, LangGraph, ` +
    `OpenAI, Claude, multi-agent systems, RAG, web scraping, automation, NLP, ` +
    `YOLOv8, AWS, Docker.\n\n` +

    `email_subject rules (individual):\n` +
    `- Max 65 characters\n` +
    `- CRITICAL: Use the post's EXACT vocabulary. If they wrote "Senior Python Developer", ` +
    `do NOT shorten to "Sr Python Dev" or "Python dev". Copy their exact words.\n` +
    `- Lead with the specific role + dominant tech from the post. Recruiters scan ` +
    `inboxes in 2 seconds — they must see matching keywords immediately.\n` +
    `- Feel post-specific, not template. Read the post, pick the strongest ` +
    `matching angle, use their own language.\n` +
    `- No clickbait, no "!", no ALL CAPS, no emojis\n` +
    `- DO NOT use "Re:" or "re:" prefix. Fresh outreach is NOT a reply.\n` +
    `- Pattern options (adapt to post vocabulary — these are templates, not rules):\n` +
    `    • "<exact role from post> — 8+ yrs <dominant tech from post>"\n` +
    `    • "<dominant tech from post> + Python — <exact role>"\n` +
    `    • "<exact role> — experienced with <specific tech stack>"\n` +
    `    • "8 yrs <tech> — your <exact role> post"\n` +
    `- Good: "Senior Python Developer — 8 yrs FastAPI/LangGraph"\n` +
    `- Good: "Databricks Engineer — 8+ yrs PySpark/AWS"\n` +
    `- Good: "MLOps Engineer — 8 yrs Python/ML pipelines"\n` +
    `- Bad: "Python dev 8+ yrs" (too generic, no post-specific hook)\n` +
    `- Bad: "Re: Senior FastAPI Developer"\n` +
    `- Bad: "URGENT: Amazing Opportunity!!"\n\n` +

    `email_body rules (individual):\n` +
    `- 4–7 short lines INCLUDING sign-off\n` +
    `- Plain text only. No HTML, no markdown.\n` +
    `- Opening greeting (choose the right one):\n` +
    `    • If posted_by has a clear first name → "Hi <FirstName>,"\n` +
    `    • Else → "Hi Hiring Manager,"\n` +
    `    • NEVER use "Hi there," — it's lazy and impersonal\n` +
    `- Line: acknowledge the post naturally — "Saw your <role> post at <company>"\n` +
    `- Line: specific hook matching tech_stack — "The <tech> angle lines up with what I've been doing"\n` +
    `- Optional: 1 brief line of relevant experience\n` +
    `- CTA: 15-min call OR one clarifying question\n` +
    `- MUST end with these two lines:\n` +
    `    Best,\n` +
    `    Jaydip\n\n` +

    `#### COMPANY MODE (email_mode = "company") ####\n\n` +

    `Positioning: Jaydip AS Co-Founder & CTO of BitCoding Solutions Pvt Ltd. ` +
    `"We/our" language. Team of 30+ engineers. Capabilities: Python, AI/ML, ` +
    `automation, RAG, multi-agent systems, FastAPI, LangGraph, AWS, data engineering, ` +
    `end-to-end product builds.\n\n` +

    `email_subject rules (company):\n` +
    `- Max 75 characters\n` +
    `- CRITICAL: Use the post's EXACT vocabulary for what they need. Pull their ` +
    `own words for the project/platform/need and reflect them back.\n` +
    `- Lead with the specific project/need + team capability. Founders scan ` +
    `subjects looking for the exact angle they posted about.\n` +
    `- Feel post-specific, not template.\n` +
    `- No "Re:", no emojis, no ALL CAPS, no "!"\n` +
    `- Pattern options (adapt to post vocabulary):\n` +
    `    • "BitCoding Solutions — Python/AI-ML team for your <exact need>"\n` +
    `    • "<exact project type> build — Python/AI-ML team at BitCoding"\n` +
    `    • "30+ Python/AI-ML devs for your <exact need>"\n` +
    `    • "BitCoding Solutions — <exact tech stack> team for <exact project>"\n` +
    `- Good: "BitCoding Solutions — Python/AI-ML team for your SaaS platform"\n` +
    `- Good: "RAG + multi-agent build — Python team at BitCoding Solutions"\n` +
    `- Good: "30+ Python/AI-ML devs for your data platform scale-up"\n` +
    `- Bad: "BitCoding Solutions — team for you" (vague, no post-specific hook)\n\n` +

    `email_body rules (company):\n` +
    `- 5–8 short lines INCLUDING sign-off\n` +
    `- Plain text only\n` +
    `- Opening greeting (choose the right one):\n` +
    `    • If posted_by has a clear first name → "Hi <FirstName>,"\n` +
    `    • Else → "Hi Hiring Manager,"\n` +
    `    • NEVER use "Hi there," — it's lazy and impersonal\n` +
    `- Line: acknowledge the post + the collaboration/team/scope angle you detected\n` +
    `- Line: intro BitCoding naturally — "At BitCoding Solutions we run a 30+ ` +
    `engineer Python / AI-ML team" or similar\n` +
    `- Line: relevance to their need — pick what matches their tech_stack\n` +
    `- Optional: brief credibility line (recent similar work in generic terms)\n` +
    `- CTA: quick call to align on scope, offer case studies, or one clarifying Q\n` +
    `- MUST end with these two lines:\n` +
    `    Best,\n` +
    `    Jaydip\n\n` +

    `#### SHARED STYLE RULES (both modes) ####\n` +
    `- USE CONTRACTIONS: I'm, don't, it's, that's, we're, we've\n` +
    `- Vary sentence length\n` +
    `- Be direct. Skip "I hope this message finds you well"\n` +
    `- No emojis\n` +
    `- No em dashes (—). Use regular hyphens (-).\n` +
    `- No 3-item lists — feels AI-generated\n\n` +

    `BANNED PHRASES (instant AI giveaway — NEVER use):\n` +
    `"I hope this message finds you well", "I'd be delighted/love to/be happy to", ` +
    `"That sounds great", "Absolutely!", "Thank you for reaching out", ` +
    `"I look forward to hearing from you", "Please let me know if you have any questions", ` +
    `"At your earliest convenience", "I am writing to express my interest", ` +
    `"I am excited about the opportunity", "Game-changer", "Synergy", "Leverage", ` +
    `em dashes (—).\n\n` +

    `If tech_stack is empty, skip the tech hook and mention Python / AI-ML plainly.\n` +
    `If role is empty, use "the role you posted".\n\n` +

    `Output ONLY the raw JSON object starting with { and ending with }. No explanation.`
  );
}

// Style angles for per-email variation — picked by the caller and threaded
// through here so Claude writes a DIFFERENT-shaped email each call. Claude
// is stateless, so without this hint it gravitates to the same default
// opener ("Saw your <role> post...") across every lead.
//
// IMPORTANT: examples below use <placeholders> and generic phrasing so
// Claude adapts them to each post's actual tech/role. Don't hardcode
// specific client names or domains (fintech/healthtech/etc.) — Claude
// has been known to copy those verbatim, which would be false claims.
const STYLE_ANGLE_INSTRUCTIONS = {
  tech_match: (
    `STYLE ANGLE FOR THIS EMAIL: "tech_match"\n` +
    `Open the body with ONE specific tech from their post and why it maps to your ` +
    `actual stack. Do NOT open with "Saw your post..." — dive straight into the ` +
    `tech overlap. Shape: "<their specific tech/stack> is exactly what I've been ` +
    `shipping lately" or similar. Use the post's own vocabulary for the tech.`
  ),
  recent_project: (
    `STYLE ANGLE FOR THIS EMAIL: "recent_project"\n` +
    `Open by referencing a recent similar project in GENERIC terms (no client ` +
    `name, no specific domain like fintech/healthtech — say "a recent client" or ` +
    `"a production build"). Then pivot to their post. No greeting fluff. ` +
    `Shape: "Just wrapped <generic project type matching their stack> — your post ` +
    `looks like the same shape of work."`
  ),
  availability: (
    `STYLE ANGLE FOR THIS EMAIL: "availability"\n` +
    `Lead with immediate availability and relevant seniority. Short, direct, no ` +
    `preamble. Shape: "Available this week for <their role>. 8 yrs <dominant tech ` +
    `from post>." Use the post's vocabulary for role and tech.`
  ),
  question: (
    `STYLE ANGLE FOR THIS EMAIL: "question"\n` +
    `Open with ONE specific clarifying question about the post's scope, then ` +
    `briefly establish fit. Don't pad with "Hi, hope you're well". Question must ` +
    `be specific to something in the post (scope, stack, timeline, or team size). ` +
    `Shape: "Quick question on your <role> post — <specific scope question>? ` +
    `Either way, I've done both."`
  ),
  direct: (
    `STYLE ANGLE FOR THIS EMAIL: "direct"\n` +
    `Skip any acknowledgment of the post. Deliver a 2-3 line pitch. Hook → fit → ` +
    `CTA. Shape: "8 yrs <dominant tech from post>, recent <generic related work>. ` +
    `Your <role> maps cleanly. 15 min to chat this week?" No specific client names.`
  ),
};

function buildExtractUserPrompt(rawText, postUrl, styleAngle) {
  const angleHint = styleAngle && STYLE_ANGLE_INSTRUCTIONS[styleAngle]
    ? `\n\n${STYLE_ANGLE_INSTRUCTIONS[styleAngle]}\n\n` +
      `IMPORTANT: This style angle OVERRIDES the default opener patterns. Still ` +
      `obey the hard rules (sign-off "Best,\\nJaydip", banned phrases, no emojis, ` +
      `plain text, email_subject rules, correct email_mode classification). But ` +
      `structure the email_body around THIS angle, not the default "Saw your post" ` +
      `pattern.`
    : '';

  return (
    `LinkedIn job post URL: ${postUrl || "(not provided)"}\n\n` +
    `Raw post text:\n"""\n${rawText.slice(0, 8000)}\n"""\n\n` +
    angleHint +
    `\n\nExtract the JSON now.`
  );
}

function parseExtractedJson(raw) {
  if (!raw) throw new Error("Empty response from Claude.");
  let s = raw.trim();

  // Strip code fences if present
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
  if (fence) s = fence[1].trim();

  // Find first { and last } to be tolerant of stray text
  const first = s.indexOf("{");
  const last = s.lastIndexOf("}");
  if (first !== -1 && last !== -1) {
    s = s.slice(first, last + 1);
  }

  try {
    return JSON.parse(s);
  } catch (err) {
    throw new Error(
      `Claude didn't return valid JSON: ${err.message}. Raw start: ${raw.slice(0, 120)}`
    );
  }
}

// ============================================================
// GOOGLE SHEET (Apps Script webhook)
// ============================================================

async function handleSaveToSheet(payload, force) {
  if (!payload) throw new Error("No payload to save.");

  const { sheetWebhookUrl } = await chrome.storage.local.get([
    "sheetWebhookUrl",
  ]);
  if (!sheetWebhookUrl) {
    throw new Error(
      "Google Sheet webhook URL not set. Go to Settings → Google Sheet and paste your Apps Script URL."
    );
  }

  const bodyObj = { ...payload, force: !!force };

  let res;
  try {
    // Apps Script web apps are picky about CORS preflight. Using text/plain
    // content type avoids the preflight and still delivers the JSON body.
    res = await fetch(sheetWebhookUrl, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(bodyObj),
      redirect: "follow",
    });
  } catch (err) {
    throw new Error(`Could not reach Apps Script webhook: ${err.message}`);
  }

  const bodyText = await res.text();

  if (!res.ok) {
    throw new Error(
      `Sheet HTTP ${res.status}. Body: ${bodyText.slice(0, 200) || res.statusText}`
    );
  }

  let data;
  try {
    data = JSON.parse(bodyText);
  } catch (_) {
    // Most likely: Apps Script returned an HTML login page because the
    // deployment isn't "Anyone" or "Execute as: Me".
    const snippet = bodyText.replace(/\s+/g, " ").slice(0, 200);
    throw new Error(
      `Response was not JSON. Redeploy the Apps Script with "Execute as: Me" + "Who has access: Anyone". First 200 chars: ${snippet}`
    );
  }

  // Duplicate detected → return special response, DO NOT throw.
  // Popup will ask the user whether to save anyway.
  if (data.duplicate) {
    return {
      duplicate: true,
      existingRow: data.existingRow || null,
      matchType: data.matchType || "unknown",
      matchValue: data.matchValue || "",
      message: data.message || "A similar entry already exists in the sheet.",
    };
  }

  if (!data.ok) {
    throw new Error(`Apps Script: ${data.error || JSON.stringify(data).slice(0, 200)}`);
  }
  return { row: data.row, sheet: data.sheet };
}

async function handleTestSheet() {
  const { sheetWebhookUrl } = await chrome.storage.local.get([
    "sheetWebhookUrl",
  ]);
  if (!sheetWebhookUrl) {
    throw new Error("No webhook URL saved.");
  }
  let res;
  try {
    res = await fetch(sheetWebhookUrl, { method: "GET", redirect: "follow" });
  } catch (err) {
    throw new Error(`Unreachable: ${err.message}`);
  }

  const bodyText = await res.text();

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}. Body: ${bodyText.slice(0, 200)}`);
  }

  let data;
  try {
    data = JSON.parse(bodyText);
  } catch (_) {
    const snippet = bodyText.replace(/\s+/g, " ").slice(0, 200);
    throw new Error(
      `Not JSON (probably a Google login page). Redeploy with "Anyone" access. First 200 chars: ${snippet}`
    );
  }

  if (!data.ok) {
    throw new Error(
      `Webhook JSON missing ok:true. Got: ${JSON.stringify(data).slice(0, 200)}`
    );
  }
  return { service: data.service, version: data.version };
}

// ============================================================
// CLAUDE CALLS (bridge OR direct API)
// ============================================================

async function callBridgeRaw(bridgeUrl, systemPrompt, userMessage) {
  const url = bridgeUrl.replace(/\/$/, "") + "/generate-reply";
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        system_prompt: systemPrompt,
        user_message: userMessage,
        max_turns: 1,
      }),
    });
  } catch (err) {
    throw new Error(
      `Bridge unreachable at ${bridgeUrl}. Is the server running? (${err.message})`
    );
  }
  if (!res.ok) {
    let body = "";
    try {
      body = await res.text();
    } catch (_) {}
    throw new Error(
      `Bridge ${res.status}: ${body.slice(0, 300) || res.statusText}`
    );
  }
  const data = await res.json();
  const reply = (data && data.reply) || "";
  if (!reply.trim()) throw new Error("Bridge returned empty reply.");
  return reply.trim();
}

async function callClaudeRaw(apiKey, systemPrompt, userPrompt) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({
      model: "claude-opus-4-6",
      max_tokens: 800,
      system: [
        {
          type: "text",
          text: systemPrompt,
          cache_control: { type: "ephemeral" },
        },
      ],
      messages: [{ role: "user", content: userPrompt }],
    }),
  });
  if (!res.ok) {
    let errBody = "";
    try {
      errBody = await res.text();
    } catch (_) {}
    throw new Error(
      `Claude API ${res.status}: ${errBody.slice(0, 300) || res.statusText}`
    );
  }
  const data = await res.json();
  const textBlock = (data.content || []).find((c) => c.type === "text");
  const text = (textBlock && textBlock.text) || "";
  if (!text) throw new Error("Claude returned empty response.");
  return text.trim();
}
