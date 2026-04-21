// ============================================================
// Pradip AI — content script
// Runs on: linkedin.com/messaging/*
// Responsibilities:
//   - Read the open conversation (READ_CONVERSATION)
//   - Paste a generated reply into LinkedIn's reply box (PASTE_REPLY)
//   - Passive account-warning detector (reports WARNING_DETECTED)
// ============================================================

const CONTENT_CHALLENGE = {
  url: ["checkpoint", "authwall", "challenge", "uas/login", "/login", "unavailable"],
  title: [
    "security verification",
    "sign in",
    "unusual activity",
    "let's do a quick security check",
    "linkedin: log in",
  ],
};

const CONTENT_WARNING_PHRASES = [
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

// Guard against double injection (manifest auto + programmatic fallback).
if (!window.__pradipAiContentLoaded) {
  window.__pradipAiContentLoaded = true;

  // PASSIVE warning detector — fires once shortly after injection.
  setTimeout(checkForAccountWarningBanner, 1500);

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.type === "READ_CONVERSATION") {
      (async () => {
        try {
          if (isChallengePage()) {
            sendResponse({
              ok: false,
              error: "Login/challenge page detected",
            });
            return;
          }
          const conversation = await readActiveConversation();
          sendResponse({ ok: true, conversation });
        } catch (err) {
          sendResponse({ ok: false, error: err.message });
        }
      })();
      return true;
    }

    if (msg && msg.type === "PASTE_REPLY") {
      try {
        pasteIntoReplyBox(msg.text || "");
        sendResponse({ ok: true });
      } catch (err) {
        sendResponse({ ok: false, error: err.message });
      }
      return true;
    }
  });
}

// ============================================================
// CHALLENGE / WARNING DETECTION
// ============================================================

function isChallengePage() {
  const url = (location.href || "").toLowerCase();
  const title = (document.title || "").toLowerCase();
  if (CONTENT_CHALLENGE.url.some((f) => url.includes(f))) return true;
  if (CONTENT_CHALLENGE.title.some((f) => title.includes(f))) return true;
  return false;
}

function checkForAccountWarningBanner() {
  try {
    const bodyText = (document.body && document.body.innerText) || "";
    if (!bodyText) return;
    const lower = bodyText.toLowerCase();
    for (const phrase of CONTENT_WARNING_PHRASES) {
      if (lower.includes(phrase)) {
        chrome.runtime.sendMessage({ type: "WARNING_DETECTED", phrase });
        break;
      }
    }
  } catch (_) {}
}

// ============================================================
// MESSAGING — READ CONVERSATION
// ============================================================

function findMessageEvents() {
  const strategies = [
    () =>
      document.querySelectorAll(
        "li.msg-s-message-list__event, li.msg-s-event-listitem"
      ),
    () =>
      document.querySelectorAll(
        ".msg-s-message-list__event, .msg-s-event-listitem"
      ),
    () => document.querySelectorAll("ul.msg-s-message-list__list > li"),
    () =>
      document.querySelectorAll(
        ".msg-s-message-list-container li[class*='event']"
      ),
    () => document.querySelectorAll("[data-event-urn]"),
    () =>
      document.querySelectorAll(
        "[data-urn*='urn:li:messagingMessage'], [data-urn*='urn:li:message'], [data-urn*='urn:li:fsd_message']"
      ),
    () => document.querySelectorAll("li[class*='msg-s-event']"),
    () => document.querySelectorAll("[class*='message-list'] li"),
    () => document.querySelectorAll("[class*='thread'] [class*='event']"),
    // Walk upward from the reply box
    () => {
      const reply = findReplyBox();
      if (!reply) return [];
      let el = reply.parentElement;
      while (el && el !== document.body) {
        const uls = el.querySelectorAll("ul");
        for (const ul of uls) {
          if (ul.children.length >= 1) return ul.querySelectorAll("li");
        }
        el = el.parentElement;
      }
      return [];
    },
    () => {
      const list = document.querySelector(
        "[class*='msg-s-message-list'], [class*='msg-thread'] ul, [class*='conversation'] ul"
      );
      return list ? list.querySelectorAll("li") : [];
    },
  ];

  for (const strat of strategies) {
    try {
      const nodes = strat();
      if (nodes && nodes.length) return Array.from(nodes);
    } catch (_) {}
  }
  return [];
}

function findReplyBox() {
  const selectors = [
    ".msg-form__contenteditable",
    "[contenteditable='true']",
    "[contenteditable='plaintext-only']",
    "[contenteditable]",
    "[role='textbox']",
    ".ql-editor",
    "div[data-placeholder*='message' i]",
    "div[aria-label*='message' i][contenteditable]",
  ];
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) return el;
  }
  return null;
}

function dumpMessagingDiagnostics() {
  const d = {};
  d.url = location.href;
  d.inIframe = window.self !== window.top;
  d.title = document.title;
  d.bodyClasses = (document.body && document.body.className) || "";

  const msgEls = document.querySelectorAll("[class*='msg']");
  d.msgClassCount = msgEls.length;
  d.msgSampleClasses = Array.from(msgEls)
    .slice(0, 10)
    .map((el) => (el.className || "").split(" ").slice(0, 3).join(" "))
    .filter(Boolean);

  d.replyBox = !!findReplyBox();
  d.contentEditableCount = document.querySelectorAll("[contenteditable]").length;
  d.textboxRoleCount = document.querySelectorAll("[role='textbox']").length;
  d.dataUrnCount = document.querySelectorAll("[data-urn]").length;
  d.iframeCount = document.querySelectorAll("iframe").length;

  return d;
}

async function waitForMessageEvents(maxMs = 4000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const events = findMessageEvents();
    if (events.length) return events;
    await sleep(300);
  }
  return [];
}

async function readActiveConversation() {
  if (!/linkedin\.com\/messaging/.test(location.href)) {
    throw new Error(
      "You're not on the Messaging page. Open linkedin.com/messaging first."
    );
  }

  const events = await waitForMessageEvents(4000);

  if (!events.length) {
    const d = dumpMessagingDiagnostics();
    console.group("[Pradip AI] DOM diagnostics");
    console.log(d);
    console.log("Sample 'msg' classes:", d.msgSampleClasses);
    console.groupEnd();
    throw new Error(
      `No messages found. iframe=${d.inIframe} msgEls=${d.msgClassCount} ` +
        `ce=${d.contentEditableCount} urn=${d.dataUrnCount} ifr=${d.iframeCount}. ` +
        `Check LinkedIn tab DevTools Console for full dump.`
    );
  }

  // Participant / thread title
  const participantName =
    text(document.querySelector(".msg-entity-lockup__entity-title")) ||
    text(document.querySelector(".msg-thread__link-to-profile")) ||
    text(document.querySelector(".msg-overlay-bubble-header__title")) ||
    text(document.querySelector("h2.msg-overlay-bubble-header__title")) ||
    text(document.querySelector(".msg-thread-actions__title")) ||
    text(
      document.querySelector(
        ".msg-convo-wrapper .artdeco-entity-lockup__title"
      )
    ) ||
    text(document.querySelector("[class*='msg-thread'] a[href*='/in/']")) ||
    "";

  const messages = [];
  let currentSender = null;

  events.forEach((ev) => {
    const nameEl =
      ev.querySelector(".msg-s-message-group__name") ||
      ev.querySelector(".msg-s-message-group__profile-link") ||
      ev.querySelector("[class*='message-group__name']") ||
      ev.querySelector("a[href*='/in/'] span[dir='ltr']");
    if (nameEl) {
      const n = text(nameEl);
      if (n) currentSender = n;
    }

    const bodyEl =
      ev.querySelector(".msg-s-event-listitem__body") ||
      ev.querySelector(".msg-s-event__content") ||
      ev.querySelector(".msg-s-event-listitem__message-bubble") ||
      ev.querySelector("[class*='event-listitem__body']") ||
      ev.querySelector("[class*='event__content']") ||
      ev.querySelector(".msg-s-event-listitem__message-bubble p") ||
      ev.querySelector("p.msg-s-event-listitem__body");

    if (!bodyEl) return;

    const body = text(bodyEl);
    if (!body) return;

    messages.push({
      sender: currentSender || "Unknown",
      text: cleanSpaces(body),
    });
  });

  if (!messages.length) {
    throw new Error(
      `Found ${events.length} event containers but no readable message bodies. ` +
        `LinkedIn DOM may have changed — please share a screenshot.`
    );
  }

  return {
    participantName: cleanSpaces(participantName),
    messages,
  };
}

// ============================================================
// MESSAGING — PASTE REPLY
// ============================================================

function pasteIntoReplyBox(replyText) {
  if (!replyText) throw new Error("Empty reply text");

  const box = findReplyBox();
  if (!box) {
    throw new Error(
      "Reply box not found. Make sure a conversation is open and the reply box is visible."
    );
  }

  box.focus();

  const range = document.createRange();
  range.selectNodeContents(box);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);

  const inserted = document.execCommand("insertText", false, replyText);

  if (!inserted) {
    box.innerHTML = "";
    replyText.split("\n").forEach((line) => {
      const p = document.createElement("p");
      p.textContent = line || "\u00A0";
      box.appendChild(p);
    });
    box.dispatchEvent(new Event("input", { bubbles: true }));
  }

  if (box.classList && box.classList.contains("msg-form__placeholder")) {
    box.classList.remove("msg-form__placeholder");
  }

  box.dispatchEvent(new InputEvent("input", { bubbles: true }));
}

// ============================================================
// HELPERS
// ============================================================

function text(el) {
  if (!el) return "";
  return (el.innerText || el.textContent || "").trim();
}

function cleanSpaces(s) {
  return (s || "").replace(/\s+/g, " ").trim();
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
