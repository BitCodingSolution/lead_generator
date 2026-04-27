// ============================================================
// Pradip AI — Lead Tracker (side panel)
// Tabs: Bulk · Reply · Settings
// (the legacy "Post" paste-and-extract tab was removed — DOM scanning
// in the Bulk tab fully covers that workflow with auto-fill + email gen.)
// ============================================================

// --- DOM refs ------------------------------------------------

const bulkBridgeHealth = document.getElementById("bulkBridgeHealth");
const settingsBridgeHealth = document.getElementById("settingsBridgeHealth");
const postsBridgeHealth = document.getElementById("postsBridgeHealth");

// Reply tab
const generateReplyBtn = document.getElementById("generateReplyBtn");
const bridgeHealth = document.getElementById("bridgeHealth");
const replyStatus = document.getElementById("replyStatus");
const replyPreview = document.getElementById("replyPreview");
const replyText = document.getElementById("replyText");
const pasteReplyBtn = document.getElementById("pasteReplyBtn");
const copyReplyBtn = document.getElementById("copyReplyBtn");
const regenerateBtn = document.getElementById("regenerateBtn");
const replyPasteStatus = document.getElementById("replyPasteStatus");
const replyInstruction = document.getElementById("replyInstruction");

// Settings tab
const apiKeyInput = document.getElementById("apiKeyInput");
const apiKeyGroup = document.getElementById("apiKeyGroup");
const saveKeyBtn = document.getElementById("saveKeyBtn");
const clearKeyBtn = document.getElementById("clearKeyBtn");
const keyStatus = document.getElementById("keyStatus");
const bridgeUrlInput = document.getElementById("bridgeUrlInput");
const saveBackendBtn = document.getElementById("saveBackendBtn");
const testBridgeBtn = document.getElementById("testBridgeBtn");
const backendStatus = document.getElementById("backendStatus");
const signOffInput = document.getElementById("signOffInput");
const toneSelect = document.getElementById("toneSelect");
const maxLinesSelect = document.getElementById("maxLinesSelect");
const saveStyleBtn = document.getElementById("saveStyleBtn");
const styleStatus = document.getElementById("styleStatus");
const safetyInfoText = document.getElementById("safetyInfoText");
const modeStatus = document.getElementById("modeStatus");
const warningCard = document.getElementById("warningCard");
const sheetWebhookInput = document.getElementById("sheetWebhookInput");
const saveSheetBtn = document.getElementById("saveSheetBtn");
const testSheetBtn = document.getElementById("testSheetBtn");
const sheetStatus = document.getElementById("sheetStatus");
const sheetViewUrlInput = document.getElementById("sheetViewUrlInput");
const dashboardBaseUrlInput = document.getElementById("dashboardBaseUrlInput");
const dashboardBaseBadge = document.getElementById("dashboardBaseBadge");
// saveSheetViewBtn was removed — the unified "Save config" button now
// handles both endpoint and open-URL in one save.
const sheetViewStatus = document.getElementById("sheetViewStatus");
const themeToggleBtn = document.getElementById("themeToggleBtn");

let statsTimer = null;
let bridgeHealthTimer = null;

// --- Tab switching -------------------------------------------

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.getAttribute("data-tab");
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${tab}`).classList.add("active");
  });
});

// --- Initial load --------------------------------------------

// Apply theme ASAP (before the main load) to avoid a white flash
chrome.storage.local.get(["darkMode"], (d) => applyTheme(!!d.darkMode));

chrome.storage.local.get(
  [
    "claudeApiKey",
    "replyStyle",
    "safetyMode",
    "claudeBackend",
    "bridgeUrl",
    "sheetWebhookUrl",
    "sheetViewUrl",
    "dashboardBaseUrl",
    "darkMode",
  ],
  (data) => {
    if (data.claudeApiKey) {
      apiKeyInput.value = maskSecret(data.claudeApiKey);
      apiKeyInput.dataset.masked = "1";
      keyStatus.textContent = "Key saved (hidden).";
    }
    if (data.sheetWebhookUrl) {
      sheetWebhookInput.value = maskSecret(data.sheetWebhookUrl);
      sheetWebhookInput.dataset.masked = "1";
      sheetStatus.textContent = "URL saved (hidden).";
    }
    if (data.sheetViewUrl) {
      sheetViewUrlInput.value = data.sheetViewUrl;
      sheetViewStatus.textContent = "Saved.";
    }
    if (dashboardBaseUrlInput && data.dashboardBaseUrl) {
      dashboardBaseUrlInput.value = data.dashboardBaseUrl;
      if (dashboardBaseBadge) {
        dashboardBaseBadge.textContent = "custom";
        dashboardBaseBadge.classList.remove("sheet-badge-muted");
      }
    }

    applyTheme(!!data.darkMode);

    const style = data.replyStyle || {};
    if (style.signOff) signOffInput.value = style.signOff;
    if (style.tone) toneSelect.value = style.tone;
    if (style.maxLines) maxLinesSelect.value = style.maxLines;
    const contactPhoneInput = document.getElementById("contactPhoneInput");
    if (contactPhoneInput && style.phone) contactPhoneInput.value = style.phone;

    const mode = data.safetyMode || "max";
    document.querySelectorAll('input[name="safetyMode"]').forEach((r) => {
      r.checked = r.value === mode;
    });

    const backend = data.claudeBackend || "bridge";
    document.querySelectorAll('input[name="claudeBackend"]').forEach((r) => {
      r.checked = r.value === backend;
    });
    if (data.bridgeUrl) bridgeUrlInput.value = data.bridgeUrl;
    updateApiKeyVisibility(backend);
  }
);

refreshStats();
statsTimer = setInterval(refreshStats, 1500);

refreshBridgeHealth();
bridgeHealthTimer = setInterval(refreshBridgeHealth, 15000);

window.addEventListener("unload", () => {
  if (statsTimer) clearInterval(statsTimer);
  if (bridgeHealthTimer) clearInterval(bridgeHealthTimer);
  // Tell any in-flight auto-scroll loop to stop before its next await
  // resumes. Chrome tears down the popup's JS context on close so pending
  // promises die anyway, but this flag also prevents sending one last
  // SCROLL_PAGE / scanRunNow round-trip during teardown.
  autoScrollAbort = true;
});

// --- Safety bar / stats --------------------------------------

async function refreshStats() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: "GET_STATS" });
    if (!resp || !resp.ok) return;
    updateSafetyInfo(resp.stats);
    updateWarningCard(resp.stats);
  } catch (_) {}
}

function updateSafetyInfo(stats) {
  if (!safetyInfoText || !stats) return;
  const cooldownMin = Math.round(stats.cooldownTotalMs / 1000);
  safetyInfoText.innerHTML = `
    <div>🔁 Mode: <strong>${stats.modeLabel || stats.mode}</strong></div>
    <div>💬 Replies today: <strong>${stats.dailyReplyCount ?? 0} / ${stats.dailyReplyCap ?? 0}</strong> <span class="hint-inline">(capped — touches LinkedIn)</span></div>
    <div>📋 Extracts today: <strong>${stats.dailyExtractCount ?? 0}</strong> <span class="hint-inline">(uncapped — no LinkedIn contact)</span></div>
    <div>⏳ Reply cooldown: <strong>${cooldownMin}s</strong></div>
    <div>🌙 Quiet hours: <strong>${stats.quietStart}:00 – ${stats.quietEnd}:00</strong> ${stats.quietHour ? "(active now)" : ""} <span class="hint-inline">(Reply only)</span></div>
    <div>🛡 Failure pause: after 3 errors → 10 min <span class="hint-inline">(Reply only)</span></div>
    <div>⚠ Warning pause: 7 days if LinkedIn flags the account</div>
  `;
}

function updateWarningCard(stats) {
  if (!warningCard) return;
  if (stats.warningPauseRemainMs > 0) {
    const hours = Math.ceil(stats.warningPauseRemainMs / (60 * 60 * 1000));
    warningCard.style.display = "block";
    warningCard.innerHTML = `
      <strong>⚠ Account warning detected</strong>
      Phrase: "${escapeHtml(stats.warningPhrase || "unknown")}"<br/>
      Extension paused for ${hours}h. Use LinkedIn manually only.
    `;
  } else {
    warningCard.style.display = "none";
  }
}

// --- Bridge health -------------------------------------------

async function refreshBridgeHealth() {
  const nodes = [bridgeHealth, bulkBridgeHealth, settingsBridgeHealth, postsBridgeHealth].filter(Boolean);
  if (!nodes.length) return;

  try {
    const settings = await chrome.storage.local.get([
      "claudeBackend",
      "bridgeUrl",
    ]);
    const backend = settings.claudeBackend || "bridge";

    if (backend === "direct") {
      setBridgeHealth("direct", "🔑 Direct API mode (no bridge needed)");
      return;
    }

    const url =
      (settings.bridgeUrl || "http://127.0.0.1:8765").replace(/\/$/, "") +
      "/health";

    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 2500);

    let ok = false;
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      ok = res.ok;
    } catch (_) {
      ok = false;
    } finally {
      clearTimeout(timeout);
    }

    if (ok) {
      setBridgeHealth("ok", "🌉 Bridge running · Claude Max subscription");
    } else {
      setBridgeHealth(
        "down",
        "🌉 Bridge OFFLINE — run Bridge/start-silent.vbs"
      );
    }
  } catch (_) {
    setBridgeHealth("unknown", "Checking bridge…");
  }
}

function setBridgeHealth(state, msg) {
  [bridgeHealth, bulkBridgeHealth, settingsBridgeHealth, postsBridgeHealth].forEach((node) => {
    if (!node) return;
    node.className = `bridge-health bh-${state}`;
    const textEl = node.querySelector(".bh-text");
    if (textEl) textEl.textContent = msg;
  });
}

// ============================================================
// REPLY TAB
// ============================================================

generateReplyBtn.addEventListener("click", () => generateReply(false));
regenerateBtn.addEventListener("click", () => generateReply(true));

// Persist the custom instruction across side-panel reopens so it isn't
// lost on accidental close. Cleared explicitly only on tab switch or
// after a successful Paste.
if (replyInstruction) {
  chrome.storage.local.get(["replyInstructionDraft"], (d) => {
    if (d.replyInstructionDraft) replyInstruction.value = d.replyInstructionDraft;
  });
  replyInstruction.addEventListener("input", () => {
    chrome.storage.local.set({ replyInstructionDraft: replyInstruction.value });
  });
}
pasteReplyBtn.addEventListener("click", pasteReplyIntoLinkedIn);

copyReplyBtn.addEventListener("click", () => {
  const txt = replyText.value;
  if (!txt) return;
  navigator.clipboard.writeText(txt).then(() => {
    const old = copyReplyBtn.textContent;
    copyReplyBtn.textContent = "✓ Copied";
    setTimeout(() => (copyReplyBtn.textContent = old), 1500);
  });
});

document.querySelectorAll(".refine-btn").forEach((btn) => {
  btn.addEventListener("click", () => refineReply(btn.dataset.refine));
});

async function generateReply(isRegenerate) {
  setReplyStatus(isRegenerate ? "Regenerating…" : "Reading conversation…");
  generateReplyBtn.disabled = true;
  if (!isRegenerate) replyPreview.style.display = "none";

  const userInstruction = (replyInstruction?.value || "").trim();

  try {
    const resp = await chrome.runtime.sendMessage({
      type: "GENERATE_REPLY",
      userInstruction,
    });
    if (!resp || !resp.ok) {
      setReplyStatus(`Error: ${(resp && resp.error) || "unknown"}`, true);
      return;
    }
    const reply = resp.reply || "";
    replyText.value = reply;
    replyPreview.style.display = "flex";

    const copied = await autoCopyToClipboard(reply);
    const who = resp.participantName || "conversation";
    setReplyStatus(
      copied
        ? `✅ Reply for "${who}" ready & COPIED to clipboard. Just paste in LinkedIn.`
        : `Reply for "${who}" ready. (clipboard copy failed — use 📋 Copy button)`
    );
    replyPasteStatus.textContent = "";
  } catch (err) {
    setReplyStatus(`Error: ${err.message}`, true);
  } finally {
    generateReplyBtn.disabled = false;
  }
}

async function autoCopyToClipboard(text) {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.top = "-9999px";
      ta.setAttribute("readonly", "");
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (_) {
      return false;
    }
  }
}

async function refineReply(refineType) {
  const currentText = (replyText.value || "").trim();
  if (!currentText) {
    setReplyStatus("Generate a reply first, then refine.", true);
    return;
  }

  setReplyStatus(`Refining (${refineType})…`);
  document.querySelectorAll(".refine-btn").forEach((b) => (b.disabled = true));
  generateReplyBtn.disabled = true;
  regenerateBtn.disabled = true;

  try {
    const resp = await chrome.runtime.sendMessage({
      type: "REFINE_REPLY",
      refineType,
      currentReply: currentText,
      userInstruction: (replyInstruction?.value || "").trim(),
    });

    if (!resp || !resp.ok) {
      setReplyStatus(`Error: ${(resp && resp.error) || "unknown"}`, true);
      return;
    }

    const refined = resp.reply || "";
    replyText.value = refined;
    const copied = await autoCopyToClipboard(refined);
    setReplyStatus(
      copied
        ? `✅ Refined (${refineType}) & COPIED to clipboard. Just paste in LinkedIn.`
        : `Refined (${refineType}). (clipboard copy failed — use 📋 Copy)`
    );
    replyPasteStatus.textContent = "";
  } catch (err) {
    setReplyStatus(`Error: ${err.message}`, true);
  } finally {
    document
      .querySelectorAll(".refine-btn")
      .forEach((b) => (b.disabled = false));
    generateReplyBtn.disabled = false;
    regenerateBtn.disabled = false;
  }
}

async function pasteReplyIntoLinkedIn() {
  const text = (replyText.value || "").trim();
  if (!text) {
    replyPasteStatus.textContent = "Nothing to paste.";
    replyPasteStatus.classList.add("error");
    return;
  }
  pasteReplyBtn.disabled = true;
  replyPasteStatus.textContent = "Pasting into LinkedIn…";
  replyPasteStatus.classList.remove("error");

  try {
    const resp = await chrome.runtime.sendMessage({
      type: "PASTE_REPLY",
      text,
    });
    if (resp && resp.ok) {
      replyPasteStatus.textContent = "✓ Pasted. Review and click Send in LinkedIn.";
      if (replyInstruction) {
        replyInstruction.value = "";
        chrome.storage.local.remove("replyInstructionDraft");
      }
    } else {
      replyPasteStatus.textContent = `Error: ${(resp && resp.error) || "unknown"}`;
      replyPasteStatus.classList.add("error");
    }
  } catch (err) {
    replyPasteStatus.textContent = `Error: ${err.message}`;
    replyPasteStatus.classList.add("error");
  } finally {
    pasteReplyBtn.disabled = false;
  }
}

function setReplyStatus(text, isError = false) {
  replyStatus.textContent = text;
  replyStatus.classList.toggle("error", isError);
}

// ============================================================
// SETTINGS TAB
// ============================================================

// --- Safety mode ---------------------------------------------

document.querySelectorAll('input[name="safetyMode"]').forEach((radio) => {
  radio.addEventListener("change", async () => {
    if (!radio.checked) return;
    await chrome.storage.local.set({ safetyMode: radio.value });
    modeStatus.textContent = `✓ Switched to ${radio.value === "max" ? "Maximum Safety" : "Normal"} mode.`;
    refreshStats();
  });
});

// --- Claude backend ------------------------------------------

function updateApiKeyVisibility(backend) {
  // API key card is ONLY relevant for Direct API mode
  if (apiKeyGroup) {
    apiKeyGroup.style.display = backend === "direct" ? "" : "none";
  }

  // Bridge URL input + test button + bridge health pill are ONLY relevant
  // for Local Bridge mode. In Direct API mode, hide them entirely so the
  // user isn't staring at irrelevant fields.
  const bridgeSection = document.getElementById("bridgeSection");
  const isBridge = backend !== "direct";
  if (bridgeSection) bridgeSection.style.display = isBridge ? "" : "none";
  if (testBridgeBtn)  testBridgeBtn.style.display  = isBridge ? "" : "none";
  if (settingsBridgeHealth) settingsBridgeHealth.style.display = isBridge ? "" : "none";
}

document.querySelectorAll('input[name="claudeBackend"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    if (!radio.checked) return;
    updateApiKeyVisibility(radio.value);
  });
});

saveBackendBtn.addEventListener("click", async () => {
  const backend =
    document.querySelector('input[name="claudeBackend"]:checked')?.value ||
    "bridge";
  const url = (bridgeUrlInput.value || "").trim() || "http://127.0.0.1:8765";
  await chrome.storage.local.set({ claudeBackend: backend, bridgeUrl: url });
  backendStatus.textContent = `✓ Backend set to: ${backend === "bridge" ? "Local Bridge" : "Direct API"}`;
  backendStatus.classList.remove("error");
  updateApiKeyVisibility(backend);
  refreshBridgeHealth();
});

testBridgeBtn.addEventListener("click", async () => {
  const url = (bridgeUrlInput.value || "").trim() || "http://127.0.0.1:8765";
  backendStatus.textContent = "Testing bridge…";
  backendStatus.classList.remove("error");
  try {
    const res = await fetch(`${url.replace(/\/$/, "")}/health`, {
      method: "GET",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data && data.ok) {
      backendStatus.textContent = `✓ Bridge alive: ${data.service || "OK"} v${data.version || ""}`;
    } else {
      backendStatus.textContent = "Bridge responded but not OK.";
      backendStatus.classList.add("error");
    }
  } catch (err) {
    backendStatus.textContent = `✗ ${err.message}. Is the server running?`;
    backendStatus.classList.add("error");
  }
});

// --- Claude API key ------------------------------------------

apiKeyInput.addEventListener("focus", () => {
  if (apiKeyInput.dataset.masked === "1") {
    apiKeyInput.value = "";
    apiKeyInput.dataset.masked = "0";
  }
});

saveKeyBtn.addEventListener("click", async () => {
  const key = apiKeyInput.value.trim();
  if (!key) {
    keyStatus.textContent = "Please paste a key first.";
    keyStatus.classList.add("error");
    return;
  }
  if (!key.startsWith("sk-ant-")) {
    keyStatus.textContent = "Doesn't look like an Anthropic key (should start with sk-ant-).";
    keyStatus.classList.add("error");
    return;
  }
  await chrome.storage.local.set({ claudeApiKey: key });
  apiKeyInput.value = maskSecret(key);
  apiKeyInput.dataset.masked = "1";
  keyStatus.textContent = "✓ Key saved locally.";
  keyStatus.classList.remove("error");
});

clearKeyBtn.addEventListener("click", async () => {
  await chrome.storage.local.remove("claudeApiKey");
  apiKeyInput.value = "";
  apiKeyInput.dataset.masked = "0";
  keyStatus.textContent = "Key cleared.";
  keyStatus.classList.remove("error");
});

// --- Google Sheet webhook ------------------------------------

// Only clear the masked value when the user ACTUALLY starts editing
// (typing / pasting / deleting) — not on a plain focus/click. Otherwise
// a stray click wipes the saved URL from view, which is confusing.
sheetWebhookInput.addEventListener("beforeinput", () => {
  if (sheetWebhookInput.dataset.masked === "1") {
    sheetWebhookInput.value = "";
    sheetWebhookInput.dataset.masked = "0";
  }
});
sheetWebhookInput.addEventListener("paste", () => {
  if (sheetWebhookInput.dataset.masked === "1") {
    sheetWebhookInput.value = "";
    sheetWebhookInput.dataset.masked = "0";
  }
});

// Unified save — writes both the Apps Script endpoint URL AND the sheet
// view URL in one click. Either field can be blank; we only validate
// fields the user actually typed into.
saveSheetBtn.addEventListener("click", async () => {
  const endpointRaw = sheetWebhookInput.value.trim();
  const viewRaw = (sheetViewUrlInput.value || "").trim();

  // Validate API key if user typed something non-masked. Dashboard keys
  // start with `li_` and are 30+ chars (matches linkedin_api.py's
  // secrets.token_urlsafe(24) format).
  let endpointToSave = null;
  const isMasked = sheetWebhookInput.dataset.masked === "1" && /•/.test(endpointRaw);
  if (!isMasked && endpointRaw) {
    if (!/^li_[A-Za-z0-9_-]{20,}$/.test(endpointRaw)) {
      setSheetStatus(
        "API key should start with 'li_' and be at least 24 chars. Issue one at /linkedin/settings.",
        true
      );
      return;
    }
    endpointToSave = endpointRaw;
  }

  // Validate dashboard URL if user typed something
  let viewToSave = null;
  let clearView = false;
  if (viewRaw) {
    if (!/^https?:\/\//.test(viewRaw)) {
      setSheetStatus(
        "Dashboard URL should start with http:// or https://",
        true
      );
      return;
    }
    viewToSave = viewRaw;
  } else {
    // Blank view input → clear stored view URL
    clearView = true;
  }

  // Backend API base (optional)
  const baseRaw = (dashboardBaseUrlInput?.value || "").trim().replace(/\/$/, "");
  let baseToSave = null;
  let clearBase = false;
  if (baseRaw) {
    if (!/^https?:\/\//.test(baseRaw)) {
      setSheetStatus("Backend API base should start with http:// or https://", true);
      return;
    }
    baseToSave = baseRaw;
  } else {
    clearBase = true;
  }

  // Write to storage
  const updates = {};
  if (endpointToSave !== null) updates.sheetWebhookUrl = endpointToSave;
  if (viewToSave !== null) updates.sheetViewUrl = viewToSave;
  if (baseToSave !== null) updates.dashboardBaseUrl = baseToSave;
  if (Object.keys(updates).length) await chrome.storage.local.set(updates);
  if (clearView && !viewToSave) await chrome.storage.local.remove("sheetViewUrl");
  if (clearBase && !baseToSave) await chrome.storage.local.remove("dashboardBaseUrl");
  if (dashboardBaseBadge) {
    dashboardBaseBadge.textContent = baseToSave ? "custom" : "default";
    dashboardBaseBadge.classList.toggle("sheet-badge-muted", !baseToSave);
  }

  // Re-mask the endpoint field after save
  if (endpointToSave) {
    sheetWebhookInput.value = maskSecret(endpointToSave);
    sheetWebhookInput.dataset.masked = "1";
  }

  // Reflect status + badges
  const parts = [];
  if (endpointToSave) parts.push("endpoint");
  if (viewToSave) parts.push("open URL");
  if (clearView && !viewToSave) parts.push("open URL cleared");
  setSheetStatus(parts.length ? `✓ Saved (${parts.join(", ")}).` : "Nothing to save.", false);
  refreshSheetBadges();
});

const openSheetFromSettingsBtn = document.getElementById("openSheetFromSettingsBtn");
if (openSheetFromSettingsBtn) {
  openSheetFromSettingsBtn.addEventListener("click", async () => {
    const { sheetViewUrl } = await chrome.storage.local.get(["sheetViewUrl"]);
    if (!sheetViewUrl) {
      setSheetStatus("No Open URL saved yet — paste your sheet view URL above first.", true);
      return;
    }
    chrome.tabs.create({ url: sheetViewUrl });
  });
}

function setSheetStatus(text, isErr) {
  if (!sheetStatus) return;
  sheetStatus.textContent = text || "";
  sheetStatus.className = "status" + (isErr ? " error" : "");
}

// Update the inline "not set / saved" badges next to each field.
async function refreshSheetBadges() {
  try {
    const { sheetWebhookUrl, sheetViewUrl } = await chrome.storage.local.get([
      "sheetWebhookUrl",
      "sheetViewUrl",
    ]);
    const endpointBadge = document.getElementById("sheetEndpointBadge");
    const viewBadge = document.getElementById("sheetViewBadge");
    if (endpointBadge) {
      if (sheetWebhookUrl) {
        endpointBadge.textContent = "saved";
        endpointBadge.className = "sheet-badge sheet-badge-ok";
      } else {
        endpointBadge.textContent = "not set";
        endpointBadge.className = "sheet-badge sheet-badge-muted";
      }
    }
    if (viewBadge) {
      if (sheetViewUrl) {
        viewBadge.textContent = "saved";
        viewBadge.className = "sheet-badge sheet-badge-ok";
      } else {
        viewBadge.textContent = "not set";
        viewBadge.className = "sheet-badge sheet-badge-muted";
      }
    }
  } catch (_) {}
}
// Refresh badges on load once storage has been read.
refreshSheetBadges();

// --- Dark mode -----------------------------------------------

themeToggleBtn.addEventListener("click", async () => {
  const isDark = document.body.classList.contains("dark-theme");
  const next = !isDark;
  await chrome.storage.local.set({ darkMode: next });
  applyTheme(next);
});

function applyTheme(dark) {
  if (dark) {
    document.body.classList.add("dark-theme");
    if (themeToggleBtn) {
      themeToggleBtn.textContent = "🌙";
      themeToggleBtn.title = "Switch to light mode";
    }
  } else {
    document.body.classList.remove("dark-theme");
    if (themeToggleBtn) {
      themeToggleBtn.textContent = "☀";
      themeToggleBtn.title = "Switch to dark mode";
    }
  }
}

testSheetBtn.addEventListener("click", async () => {
  sheetStatus.textContent = "Testing sheet webhook…";
  sheetStatus.classList.remove("error");
  try {
    const resp = await chrome.runtime.sendMessage({ type: "TEST_SHEET" });
    if (resp && resp.ok) {
      sheetStatus.textContent = `✓ Sheet alive: ${resp.service || "OK"} v${resp.version || ""}`;
    } else {
      sheetStatus.textContent = `✗ ${(resp && resp.error) || "unknown error"}`;
      sheetStatus.classList.add("error");
    }
  } catch (err) {
    sheetStatus.textContent = `✗ ${err.message}`;
    sheetStatus.classList.add("error");
  }
});

// --- Reply style ---------------------------------------------

saveStyleBtn.addEventListener("click", async () => {
  const contactPhoneInput = document.getElementById("contactPhoneInput");
  const phone = (contactPhoneInput && contactPhoneInput.value.trim()) || "";
  const style = {
    signOff: signOffInput.value.trim() || "Best, Jaydip",
    tone: toneSelect.value,
    maxLines: maxLinesSelect.value,
    phone, // empty string means: never share a number in replies
  };
  await chrome.storage.local.set({ replyStyle: style });
  styleStatus.textContent = "✓ Style saved.";
  styleStatus.classList.remove("error");
});

// --- Helpers -------------------------------------------------

function maskSecret(s) {
  if (!s || s.length < 16) return "••••••";
  return s.slice(0, 8) + "•".repeat(12) + s.slice(-4);
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

// ============================================================
// BULK SEARCH TAB — query × location URL factory
// ============================================================

const BULK_DEFAULT_QUERIES = [
  "python developer hiring",
  "hiring python",
  "FastAPI developer",
  "Django dev hiring",
  "AI engineer hiring",
  "LangChain developer",
  "LangGraph engineer",
  "ML engineer hiring",
  "RAG engineer",
  "LLM developer",
  "GenAI engineer",
  "web scraping developer",
  "automation engineer",
  "MLOps engineer",
  "agentic AI developer",
];

const BULK_DEFAULT_LOCATIONS = [
  "global",
  "United States",
  "United Kingdom",
  "Germany",
  "Netherlands",
  "Canada",
  "Australia",
  "UAE",
  "Singapore",
];

// DOM refs — bulk tab
const bulkQueries          = document.getElementById("bulkQueries");
const bulkLocationsGrid    = document.getElementById("bulkLocationsGrid");
const bulkFilterRemote     = document.getElementById("bulkFilterRemote");
const bulkFilterContract   = document.getElementById("bulkFilterContract");
const bulkSaveBtn          = document.getElementById("bulkSaveBtn");
const bulkResetDefaultsBtn = document.getElementById("bulkResetDefaultsBtn");
const bulkOpenNextBtn      = document.getElementById("bulkOpenNextBtn");
const bulkStatus           = document.getElementById("bulkStatus");
const bulkProgressPill     = document.getElementById("bulkProgressPill");
const bulkUrlList          = document.getElementById("bulkUrlList");

// In-memory state
let bulkUrls = [];         // array of { query, location, url }
let bulkCursor = 0;        // index of the NEXT URL to open via "Open next"
let bulkOpenedSet = new Set(); // URLs already opened (for strikethrough)
let bulkRegenDebounce = null;

function bulkBuildSearchUrl(query, location, filters) {
  // LinkedIn content search (posts) — sorted by date_posted (Latest).
  // Location is appended to keywords since the content-search page doesn't
  // expose a standalone location facet. Remote / contract filters append
  // their keywords so location-anchored searches (Singapore, Germany, UAE)
  // don't surface local-only full-time roles.
  const parts = [String(query || "").trim()];
  const loc = String(location || "").trim();
  if (loc && loc.toLowerCase() !== "global") parts.push(loc);

  const joinedLower = () => parts.join(" ").toLowerCase();
  if (filters && filters.remote   && !/\bremote\b/.test(joinedLower()))   parts.push("remote");
  if (filters && filters.contract && !/\bcontract\b/.test(joinedLower())) parts.push("contract");

  const keywords = parts.join(" ");
  return (
    "https://www.linkedin.com/search/results/content/" +
    "?keywords=" + encodeURIComponent(keywords) +
    "&origin=FACETED_SEARCH" +
    '&sortBy=%22date_posted%22'
  );
}

// Reads the current filter checkboxes into a plain object.
function bulkReadFilters() {
  return {
    remote:   !!(bulkFilterRemote   && bulkFilterRemote.checked),
    contract: !!(bulkFilterContract && bulkFilterContract.checked),
  };
}

// Renders the locations checkbox grid. Each location gets a <label>+checkbox.
// Checked state is driven by the `checkedSet` argument (Set of lowercase names).
function bulkRenderLocations(allLocations, checkedSet) {
  if (!bulkLocationsGrid) return;
  bulkLocationsGrid.innerHTML = "";
  allLocations.forEach((loc) => {
    const id = "bulkLoc_" + loc.replace(/[^a-z0-9]/gi, "_");
    const wrap = document.createElement("label");
    wrap.className = "bulk-loc-chip";
    wrap.htmlFor = id;

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = id;
    cb.value = loc;
    cb.checked = checkedSet.has(loc.toLowerCase());
    cb.addEventListener("change", () => bulkOnInput());

    const lbl = document.createElement("span");
    lbl.textContent = loc;

    wrap.appendChild(cb);
    wrap.appendChild(lbl);
    bulkLocationsGrid.appendChild(wrap);
  });
}

// Reads the currently-checked locations from the grid.
function bulkReadCheckedLocations() {
  if (!bulkLocationsGrid) return [];
  const boxes = bulkLocationsGrid.querySelectorAll('input[type="checkbox"]');
  const out = [];
  boxes.forEach((b) => { if (b.checked) out.push(b.value); });
  return out;
}

function bulkParseList(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0 && !s.startsWith("#"));
}

async function bulkLoad() {
  const data = await chrome.storage.local.get([
    "bulkQueries",
    "bulkLocations",            // full known list (what shows up as checkboxes)
    "bulkCheckedLocations",     // which of those are currently checked
    "bulkOpenedUrls",
    "bulkCursor",
    "bulkFilterRemote",
    "bulkFilterContract",
  ]);

  const queries = (Array.isArray(data.bulkQueries) && data.bulkQueries.length)
    ? data.bulkQueries
    : BULK_DEFAULT_QUERIES.slice();
  const allLocations = (Array.isArray(data.bulkLocations) && data.bulkLocations.length)
    ? data.bulkLocations
    : BULK_DEFAULT_LOCATIONS.slice();
  // Default to ALL locations checked on first run (matches previous behavior
  // before the textarea → checkbox migration).
  const checkedLocs = (Array.isArray(data.bulkCheckedLocations))
    ? data.bulkCheckedLocations
    : allLocations.slice();
  const checkedSet = new Set(checkedLocs.map((s) => String(s).toLowerCase()));

  bulkQueries.value = queries.join("\n");
  bulkRenderLocations(allLocations, checkedSet);

  // Filters default to on; only turn off if explicitly saved false.
  if (bulkFilterRemote)   bulkFilterRemote.checked   = data.bulkFilterRemote   !== false;
  if (bulkFilterContract) bulkFilterContract.checked = data.bulkFilterContract !== false;

  bulkCursor = Math.max(0, Number(data.bulkCursor || 0));
  bulkOpenedSet = new Set(Array.isArray(data.bulkOpenedUrls) ? data.bulkOpenedUrls : []);

  bulkRegenerate();
}

// Pure regenerate — no storage writes. Safe to call on every keystroke /
// checkbox flip (debounced).
function bulkRegenerate() {
  const queries = bulkParseList(bulkQueries.value);
  const locations = bulkReadCheckedLocations();
  const filters = bulkReadFilters();

  bulkUrls = [];
  for (let i = 0; i < queries.length; i++) {
    for (let j = 0; j < locations.length; j++) {
      bulkUrls.push({
        query: queries[i],
        location: locations[j],
        url: bulkBuildSearchUrl(queries[i], locations[j], filters),
      });
    }
  }

  if (bulkCursor > bulkUrls.length) bulkCursor = bulkUrls.length;

  bulkRender();
}

function bulkRender() {
  const total = bulkUrls.length;
  bulkProgressPill.textContent = `${bulkCursor} / ${total}`;

  // Virtualise-lite: only mount rows if the <details> is open OR total <= 200.
  // For large lists kept collapsed, we still render so <ol> counter shows — but
  // keep styling minimal.
  bulkUrlList.innerHTML = "";
  for (let i = 0; i < bulkUrls.length; i++) {
    const item = bulkUrls[i];
    const li = document.createElement("li");

    if (bulkOpenedSet.has(item.url)) li.classList.add("bulk-opened");
    if (i === bulkCursor) li.classList.add("bulk-current");

    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = `${item.query} · ${item.location}`;
    link.addEventListener("click", (ev) => {
      ev.preventDefault();
      // Direct link click: open this specific URL WITHOUT advancing the "next"
      // cursor. This way Jaydip can jump around without skipping earlier URLs.
      // reuseTab=true → replaces existing LinkedIn tab instead of spawning new.
      bulkOpenAt(i, /*advanceCursor=*/ false, /*foreground=*/ true, /*reuseTab=*/ true);
    });
    li.appendChild(link);

    bulkUrlList.appendChild(li);
  }
}

// Find a reusable LinkedIn tab — prefer one already on a search-results page,
// otherwise any linkedin.com tab. Returns null if none open.
async function bulkFindLinkedInTab() {
  try {
    const searchTabs = await chrome.tabs.query({
      url: "*://www.linkedin.com/search/results/content/*",
    });
    if (searchTabs && searchTabs.length) {
      // Prefer the most recently accessed / last in the list
      return searchTabs[searchTabs.length - 1];
    }
    const anyLinkedIn = await chrome.tabs.query({
      url: "*://www.linkedin.com/*",
    });
    if (anyLinkedIn && anyLinkedIn.length) return anyLinkedIn[anyLinkedIn.length - 1];
  } catch (_) {}
  return null;
}

// Core opener — opens a URL at index, marks it as opened, optionally
// advances the cursor (only when "Open next"/"Open next 5" fired).
// foreground=true → user jumps to the new/updated tab; false → background.
// reuseTab=true → update an existing LinkedIn tab instead of spawning new.
async function bulkOpenAt(index, advanceCursor, foreground, reuseTab) {
  if (index < 0 || index >= bulkUrls.length) return null;
  const item = bulkUrls[index];

  try {
    let reused = false;
    if (reuseTab) {
      const existing = await bulkFindLinkedInTab();
      if (existing && existing.id != null) {
        await chrome.tabs.update(existing.id, {
          url: item.url,
          active: !!foreground,
        });
        if (foreground && existing.windowId != null) {
          try { await chrome.windows.update(existing.windowId, { focused: true }); } catch (_) {}
        }
        reused = true;
      }
    }
    if (!reused) {
      await chrome.tabs.create({ url: item.url, active: !!foreground });
    }
  } catch (err) {
    setBulkStatus("Failed to open tab: " + err.message, true);
    return null;
  }

  bulkOpenedSet.add(item.url);
  if (advanceCursor) bulkCursor = Math.max(bulkCursor, index + 1);

  await chrome.storage.local.set({
    bulkOpenedUrls: Array.from(bulkOpenedSet),
    bulkCursor: bulkCursor,
  });

  bulkRender();
  return item;
}

async function bulkOpenNext() {
  bulkRegenerate(); // ensure URLs reflect current textarea contents
  if (!bulkUrls.length) {
    setBulkStatus("No URLs generated. Check your query + location lists.", true);
    return;
  }
  if (bulkCursor >= bulkUrls.length) {
    setBulkStatus("You've finished all URLs. Click ↺ Reset cursor to restart.", true);
    return;
  }
  // Single "Open next" reuses the existing LinkedIn tab instead of spawning
  // new tabs on every click — avoids a pile-up of abandoned tabs.
  const item = await bulkOpenAt(bulkCursor, /*advanceCursor=*/ true, /*foreground=*/ true, /*reuseTab=*/ true);
  if (item) {
    setBulkStatus(
      `▶ Opened #${bulkCursor}/${bulkUrls.length}: ${item.query} · ${item.location}`
    );
  }
}

async function bulkSave() {
  const queries = bulkParseList(bulkQueries.value);
  const checkedLocations = bulkReadCheckedLocations();

  if (!queries.length) {
    setBulkStatus("Add at least one query before saving.", true);
    return;
  }
  if (!checkedLocations.length) {
    setBulkStatus('Tick at least one location (use "global" for worldwide).', true);
    return;
  }

  const filters = bulkReadFilters();

  await chrome.storage.local.set({
    bulkQueries: queries,
    bulkCheckedLocations: checkedLocations,
    bulkFilterRemote:   filters.remote,
    bulkFilterContract: filters.contract,
  });
  bulkRegenerate();
  const filterBits = [];
  if (filters.remote)   filterBits.push("remote");
  if (filters.contract) filterBits.push("contract");
  const filterNote = filterBits.length ? ` (+${filterBits.join(", ")})` : "";
  setBulkStatus(
    `💾 Saved ${queries.length} queries × ${checkedLocations.length} locations = ${bulkUrls.length} URLs${filterNote}.`
  );
}

async function bulkResetDefaults() {
  if (!confirm("Reset query + location lists to factory defaults?\n\nYour custom edits will be lost.")) return;
  bulkQueries.value = BULK_DEFAULT_QUERIES.join("\n");
  const checkedSet = new Set(BULK_DEFAULT_LOCATIONS.map((s) => s.toLowerCase()));
  bulkRenderLocations(BULK_DEFAULT_LOCATIONS.slice(), checkedSet);
  if (bulkFilterRemote)   bulkFilterRemote.checked   = true;
  if (bulkFilterContract) bulkFilterContract.checked = true;
  await chrome.storage.local.set({
    bulkQueries: BULK_DEFAULT_QUERIES.slice(),
    bulkLocations: BULK_DEFAULT_LOCATIONS.slice(),
    bulkCheckedLocations: BULK_DEFAULT_LOCATIONS.slice(),
    bulkFilterRemote: true,
    bulkFilterContract: true,
  });
  bulkRegenerate();
  setBulkStatus(
    `↺ Restored ${BULK_DEFAULT_QUERIES.length} default queries × ${BULK_DEFAULT_LOCATIONS.length} default locations.`
  );
}

function setBulkStatus(text, isErr) {
  bulkStatus.textContent = text || "";
  bulkStatus.className = "status" + (isErr ? " error" : "");
}

// Live preview — regenerate URLs as user types, debounced.
function bulkOnInput() {
  if (bulkRegenDebounce) clearTimeout(bulkRegenDebounce);
  bulkRegenDebounce = setTimeout(() => bulkRegenerate(), 200);
}

if (bulkQueries)          bulkQueries.addEventListener("input", bulkOnInput);
if (bulkFilterRemote)     bulkFilterRemote.addEventListener("change", () => bulkRegenerate());
if (bulkFilterContract)   bulkFilterContract.addEventListener("change", () => bulkRegenerate());
if (bulkSaveBtn)          bulkSaveBtn.addEventListener("click", bulkSave);
if (bulkResetDefaultsBtn) bulkResetDefaultsBtn.addEventListener("click", bulkResetDefaults);
if (bulkOpenNextBtn)      bulkOpenNextBtn.addEventListener("click", bulkOpenNext);

// Load on startup
if (bulkQueries && bulkLocationsGrid) {
  bulkLoad().catch((err) => console.warn("bulkLoad failed:", err));
}

// ============================================================
// LIVE SCAN — DOM-grab leads from the active LinkedIn search tab
// ============================================================

const scanPanel         = document.getElementById("scanPanel");
const scanNowBtn        = document.getElementById("scanNowBtn");
const autoScrollBtn     = document.getElementById("autoScrollBtn");
const autoScrollDuration = document.getElementById("autoScrollDuration");
const autoScrollProgress = document.getElementById("autoScrollProgress");
const autoScrollProgressLabel = document.getElementById("autoScrollProgressLabel");
const autoScrollProgressFill = document.getElementById("autoScrollProgressFill");
const scanSaveAllBtn    = document.getElementById("scanSaveAllBtn");
const scanProgress      = document.getElementById("scanProgress");
const scanProgressLabel = document.getElementById("scanProgressLabel");
const scanProgressEta   = document.getElementById("scanProgressEta");
const scanProgressFill  = document.getElementById("scanProgressFill");
const scanClearBtn      = document.getElementById("scanClearBtn");
const scanAutoGenEmail  = document.getElementById("scanAutoGenEmail");
const scanHint          = document.getElementById("scanHint");
const scanStatus        = document.getElementById("scanStatus");
const scanLeadsWrap     = document.getElementById("scanLeadsWrap");

let scanCurrentLeads = [];     // accumulated results across multiple scans
let scanActiveTabId  = null;   // detected search-results tab id
// Saved leads tracked by STABLE KEY, not index. Indices shift when a
// re-scan merges new leads in, so a key-based set survives merges.
let scanSavedKeys    = new Set();
// Map stable-lead-key -> dashboard lead id. Populated once the save
// response comes back; used to fire call-status patches post-save.
let scanSavedLeadIds = new Map();
// Map stable-lead-key -> current local call_status pick (green|yellow|red|null).
// Mirrored in the UI button state and submitted as part of the save payload
// on first save, or patched via API after save.
let scanCallStatus   = new Map();

// Stable identity for a lead — survives DOM virtualization + page scrolling.
// Priority: postUrl > sorted emails > sorted phones > snippet hash. Used
// both for merging across re-scans (skip dupes) and for tracking which
// leads are already saved (so re-scan doesn't reset their visual state).
function scanLeadKey(lead) {
  if (!lead) return '';
  if (lead.postUrl) return 'url:' + lead.postUrl;
  const emails = (lead.emails || []).map((e) => String(e).toLowerCase()).sort().join(',');
  if (emails) return 'em:' + emails;
  const phones = (lead.phones || []).map((p) => String(p).replace(/\D/g, '')).sort().join(',');
  if (phones) return 'ph:' + phones;
  return 'tx:' + (lead.snippet || lead.text || '').slice(0, 80).toLowerCase();
}

// Persist the auto-gen toggle so it survives side panel reloads.
(async function loadScanPrefs() {
  try {
    const { scanAutoGenEmail: genPref } =
      await chrome.storage.local.get(["scanAutoGenEmail"]);
    if (scanAutoGenEmail && genPref === false) scanAutoGenEmail.checked = false;
  } catch (_) {}
})();
if (scanAutoGenEmail) {
  scanAutoGenEmail.addEventListener("change", () => {
    chrome.storage.local.set({ scanAutoGenEmail: !!scanAutoGenEmail.checked });
  });
}

// Rotate the email's structural angle per call so bulk saves don't all
// come out with the same "Saw your post..." opener. Claude is stateless,
// so we inject one of these hints and background.js routes it into the
// user prompt. Distribution is random, so over 20 leads Jaydip gets
// roughly 4 of each style.
const SCAN_STYLE_ANGLES = ["tech_match", "recent_project", "availability", "question", "direct"];

function scanPickStyleAngle() {
  return SCAN_STYLE_ANGLES[Math.floor(Math.random() * SCAN_STYLE_ANGLES.length)];
}

// Ask Claude to extract structured fields + compose email subject/body for
// a scanned lead. Returns a payload-ready object, or null on failure.
// EXTRACT_POST returns: email_mode, email_subject, email_body, posted_by,
// company, role, tech_stack, etc. — the shape Apps Script expects.
async function scanGenerateEmailForLead(lead) {
  const text = lead.text || lead.snippet || "";
  if (!text || text.length < 30) return null;

  const resp = await chrome.runtime.sendMessage({
    type: "EXTRACT_POST",
    text,
    url: lead.postUrl || "",
    styleAngle: scanPickStyleAngle(),
  });

  if (!resp || !resp.ok) {
    throw new Error((resp && resp.error) || "Claude extraction failed");
  }
  return resp.data || null;
}

// Build the Apps Script payload for a scanned lead, optionally merging
// Claude-generated fields (email_subject/body/mode/etc.) over the raw
// DOM-scraped values.
function scanBuildPayload(lead, genFields) {
  // Scan-side saves land in the main LinkedIn sheet via the same doPost
  // pipeline as any other webhook write. We tag them "bulk-scan" in
  // column J so these rows can be
  // filtered / distinguished from manually-pasted leads. Status defaults
  // to 'New' so they're immediately eligible for batch send (subject to
  // Apps Script's phrase + job-post-signal filters).
  const cardKey = scanLeadKey(lead);
  const preTagged = scanCallStatus.get(cardKey) || "";
  const base = {
    post_url: lead.postUrl || "",
    posted_by: lead.author || "",
    email: (lead.emails && lead.emails[0]) || "",
    phone: (lead.phones && lead.phones[0]) || "",
    post_text: lead.text || "",
    notes: lead.snippet || "",
    tags: lead.hiringSignal ? "bulk-scan, hiring-signal" : "bulk-scan",
    // 🟢/🟡/🔴 the user marked before hitting Save gets persisted at
    // insert — no race with a follow-up PATCH_LEAD call.
    call_status: preTagged || undefined,
  };

  if (!genFields) return base;

  // Trust Claude's extraction over the DOM scraper where it has a value —
  // the scraper grabs first email/phone raw, Claude picks the best match
  // and often pulls the right posted_by / company / role.
  return {
    ...base,
    posted_by:   genFields.posted_by   || base.posted_by,
    company:     genFields.company     || "",
    role:        genFields.role        || "",
    tech_stack:  genFields.tech_stack  || "",
    rate_budget: genFields.rate_budget || "",
    location:    genFields.location    || "",
    email:       genFields.email       || base.email,
    phone:       genFields.phone       || base.phone,
    tags:        genFields.tags        || base.tags,
    notes:       genFields.notes       || base.notes,
    email_mode:    genFields.email_mode    || "individual",
    email_subject: genFields.email_subject || "",
    email_body:    genFields.email_body    || "",
    // Claude's skip decision — Apps Script doPost trusts this over the
    // phrase-blocklist fallback. Normalize string "true"/"false" just in
    // case Claude serialized the bool as a string.
    should_skip:   genFields.should_skip === true || genFields.should_skip === "true",
    skip_reason:   String(genFields.skip_reason || "").trim(),
  };
}

async function scanDetectActiveTab() {
  // Prefer an active LinkedIn search-results tab in the last-focused window;
  // fall back to any such tab. We do NOT switch focus — just detect.
  try {
    const active = await chrome.tabs.query({
      active: true,
      lastFocusedWindow: true,
      url: "*://www.linkedin.com/search/results/content/*",
    });
    if (active && active.length) return active[0];

    const any = await chrome.tabs.query({
      url: "*://www.linkedin.com/search/results/content/*",
    });
    if (any && any.length) return any[any.length - 1];
  } catch (_) {}
  return null;
}

async function scanRefreshAvailability() {
  const tab = await scanDetectActiveTab();
  if (tab) {
    scanActiveTabId = tab.id;
    const q = extractSearchKeywords(tab.url);
    scanHint.textContent = q ? `target: "${q}"` : "target: LinkedIn search page";
    if (scanNowBtn) scanNowBtn.disabled = false;
    if (autoScrollBtn && !autoScrollRunning) autoScrollBtn.disabled = false;
  } else {
    scanActiveTabId = null;
    scanHint.textContent = "open a LinkedIn search-results tab to scan";
    if (scanNowBtn) scanNowBtn.disabled = true;
    if (autoScrollBtn && !autoScrollRunning) autoScrollBtn.disabled = true;
  }
}

function extractSearchKeywords(url) {
  try {
    const u = new URL(url);
    return u.searchParams.get("keywords") || "";
  } catch (_) {
    return "";
  }
}

async function scanRunNow() {
  await scanRefreshAvailability();
  if (!scanActiveTabId) {
    setScanStatus("No LinkedIn search tab detected. Open one from the Bulk list first.", true);
    return;
  }

  setScanStatusLoading("Scanning page…");
  scanNowBtn.disabled = true;

  let resp;
  try {
    resp = await chrome.tabs.sendMessage(scanActiveTabId, { type: "SCAN_SEARCH_PAGE" });
  } catch (err) {
    setScanStatus(
      "Couldn't reach the search page. Reload the LinkedIn tab and try again. (" + err.message + ")",
      true
    );
    if (scanNowBtn) scanNowBtn.disabled = false;
    return;
  }

  scanNowBtn.disabled = false;

  if (!resp || !resp.ok) {
    setScanStatus("Scan failed: " + ((resp && resp.error) || "no response"), true);
    return;
  }

  const leads = Array.isArray(resp.leads) ? resp.leads : [];
  const stats = resp.stats || {};
  const selUsed = resp.selectorUsed || "none";

  // MERGE new leads into the existing list rather than replacing it.
  // LinkedIn virtualizes search-result DOM as the user scrolls — a fresh
  // scan only sees the currently-rendered window. Without merging, the
  // user would lose all previously-scanned leads (and their saved state)
  // every time they scroll + Scan again. Dedup by stable key so the same
  // post showing up in two consecutive scans isn't doubled.
  const isFirstScan = scanCurrentLeads.length === 0;
  let addedCount = 0;
  let dupedCount = 0;
  if (isFirstScan) {
    scanCurrentLeads = leads.slice();
    addedCount = leads.length;
    // First-scan also resets saved-state — the previous run was already
    // cleared via scanClear or never existed, so nothing to preserve.
    scanSavedKeys = new Set();
  } else {
    const existingKeys = new Set(scanCurrentLeads.map(scanLeadKey));
    for (const l of leads) {
      const k = scanLeadKey(l);
      if (existingKeys.has(k)) { dupedCount++; continue; }
      existingKeys.add(k);
      scanCurrentLeads.push(l);
      addedCount++;
    }
  }

  scanClearBtn.disabled = scanCurrentLeads.length === 0;
  scanSaveAllBtn.disabled = scanCurrentLeads.length === 0;

  if (!leads.length) {
    // Zero-lead diagnostic so we can tell WHY it was empty.
    if (stats.containers === 0) {
      const diag = resp.domDiag || {};
      const urnHint = (diag.urnSamples && diag.urnSamples.length)
        ? ` (saw ${diag.urnSamples.length} URN nodes — DOM present but selectors didn't match)`
        : " (no URN nodes found — page may not have finished loading)";
      setScanStatus(
        `0 post containers matched${urnHint}. LinkedIn DOM likely changed. Open DevTools → Console on the LinkedIn tab → share the [Pradip AI scan] log.`,
        true
      );
    } else {
      const breakdown = [];
      if (stats.noText) breakdown.push(`${stats.noText} empty/short`);
      if (stats.blockedAuthor) breakdown.push(`${stats.blockedAuthor} blocked author`);
      if (stats.noContactNoHiring) breakdown.push(`${stats.noContactNoHiring} no email/phone/hiring-signal`);
      if (stats.dupe) breakdown.push(`${stats.dupe} duplicate`);
      setScanStatus(
        `Saw ${stats.containers} post(s) via \`${selUsed}\`, kept 0 (${breakdown.join(", ") || "all filtered"}). Try scrolling to load more posts.`
      );
    }
    scanLeadsWrap.innerHTML = "";
    return;
  }

  const withEmail = scanCurrentLeads.filter((l) => l.hasEmail).length;
  const withPhone = scanCurrentLeads.filter((l) => l.hasPhone).length;
  const fallbackNote = stats.fallbackUsed ? " [fallback mode — per-post URLs unavailable]" : "";
  const skipNote = !stats.fallbackUsed && stats.containers > leads.length
    ? ` (this scan: ${stats.containers}, filtered ${stats.containers - leads.length})`
    : "";

  let mergeNote = "";
  if (!isFirstScan) {
    const parts = [];
    if (addedCount) parts.push(`+${addedCount} new`);
    if (dupedCount) parts.push(`${dupedCount} already in list`);
    mergeNote = ` · ${parts.join(", ") || "no new leads"} after scroll`;
  }

  setScanStatus(
    `${scanCurrentLeads.length} lead${scanCurrentLeads.length > 1 ? "s" : ""} total — ` +
    `${withEmail} with email, ${withPhone} with phone${skipNote}${fallbackNote}${mergeNote}. ` +
    (isFirstScan ? `Scroll the LinkedIn page + click Scan again to add more.` : "")
  );
  scanRenderLeads(scanCurrentLeads);
  refreshSaveAllState();
}

function scanRenderLeads(leads) {
  scanLeadsWrap.innerHTML = "";
  for (let i = 0; i < leads.length; i++) {
    const lead = leads[i];
    const isAlreadySaved = scanSavedKeys.has(scanLeadKey(lead));
    const card = document.createElement("div");
    card.className = "scan-lead" + (isAlreadySaved ? " saved" : "");
    card.dataset.idx = String(i);
    card.dataset.key = scanLeadKey(lead);

    // Header: author + badges
    const header = document.createElement("div");
    header.className = "scan-lead-header";

    const author = document.createElement("span");
    author.className = "scan-lead-author";
    author.textContent = lead.author || "(unknown author)";
    header.appendChild(author);

    const badges = document.createElement("span");
    badges.className = "scan-lead-badges";
    if (lead.hasEmail) badges.appendChild(mkBadge("📧", "b-email"));
    if (lead.hasPhone) badges.appendChild(mkBadge("📞", "b-phone"));
    if (lead.hiringSignal) badges.appendChild(mkBadge("🔥 hiring", "b-hiring"));
    if (lead.truncated) {
      const tb = mkBadge("⚠ truncated", "b-truncated");
      tb.title = "LinkedIn clamped this post with \"...see more\". Open the post on LinkedIn, click See more, then rescan — otherwise the generated email will be based on partial context.";
      badges.appendChild(tb);
    }
    header.appendChild(badges);
    card.appendChild(header);

    // Snippet
    const snippet = document.createElement("div");
    snippet.className = "scan-lead-snippet";
    snippet.textContent = lead.snippet || lead.text || "";
    card.appendChild(snippet);

    // Contacts
    if ((lead.emails && lead.emails.length) || (lead.phones && lead.phones.length)) {
      const contacts = document.createElement("div");
      contacts.className = "scan-lead-contacts";
      (lead.emails || []).forEach((e) => {
        const pill = document.createElement("span");
        pill.className = "scan-contact-pill";
        pill.textContent = e;
        contacts.appendChild(pill);
      });
      (lead.phones || []).forEach((p) => {
        const pill = document.createElement("span");
        pill.className = "scan-contact-pill";
        pill.textContent = p;
        contacts.appendChild(pill);
      });
      card.appendChild(contacts);
    }

    // Actions
    const actions = document.createElement("div");
    actions.className = "scan-lead-actions";

    const saveBtn = document.createElement("button");
    saveBtn.className = "primary-btn";
    if (isAlreadySaved) {
      saveBtn.textContent = "✓ Saved";
      saveBtn.disabled = true;
    } else {
      saveBtn.textContent = "💾 Save";
    }
    saveBtn.addEventListener("click", () => scanSaveLead(i, saveBtn, card));
    actions.appendChild(saveBtn);

    if (lead.postUrl) {
      const openBtn = document.createElement("button");
      openBtn.textContent = "🔗 Open";
      openBtn.addEventListener("click", () => chrome.tabs.create({ url: lead.postUrl, active: true }));
      actions.appendChild(openBtn);
    }

    // Call-status quick-mark row. Works both pre-save (buffers the
    // choice; applied on first save) and post-save (patches lead by id).
    const tagRow = document.createElement("div");
    tagRow.className = "scan-lead-tags";
    const cardKey = scanLeadKey(lead);
    const current = scanCallStatus.get(cardKey) || "";
    const defs = [
      { v: "green",  label: "🟢", title: "Interested" },
      { v: "yellow", label: "🟡", title: "Maybe" },
      { v: "red",    label: "🔴", title: "Not a fit" },
    ];
    defs.forEach((d) => {
      const b = document.createElement("button");
      b.className = "scan-tag-btn" + (current === d.v ? " active" : "");
      b.textContent = d.label;
      b.title = d.title;
      b.dataset.value = d.v;
      b.addEventListener("click", () => onCallStatusClick(cardKey, d.v, tagRow));
      tagRow.appendChild(b);
    });
    actions.appendChild(tagRow);

    card.appendChild(actions);
    scanLeadsWrap.appendChild(card);
  }
}

function mkBadge(text, cls) {
  const b = document.createElement("span");
  b.className = "scan-badge " + cls;
  b.textContent = text;
  return b;
}

async function onCallStatusClick(cardKey, value, tagRow) {
  // Toggle: clicking the already-active one clears.
  const prev = scanCallStatus.get(cardKey) || "";
  const next = prev === value ? "" : value;
  if (next) {
    scanCallStatus.set(cardKey, next);
  } else {
    scanCallStatus.delete(cardKey);
  }
  // Repaint just this row's buttons.
  tagRow.querySelectorAll(".scan-tag-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.value === next);
  });

  // If the lead is already saved, patch the dashboard now.
  const leadId = scanSavedLeadIds.get(cardKey);
  if (leadId) {
    try {
      const resp = await chrome.runtime.sendMessage({
        type: "PATCH_LEAD",
        leadId,
        updates: { call_status: next },
      });
      if (resp && resp.ok) {
        setScanStatus(next ? `Tagged ${labelFor(next)}` : "Tag cleared.");
      } else {
        setScanStatus(`Tag failed: ${(resp && resp.error) || "unknown"}`, true);
      }
    } catch (err) {
      setScanStatus(`Tag failed: ${err.message}`, true);
    }
  }
}

function labelFor(v) {
  if (v === "green")  return "🟢 Interested";
  if (v === "yellow") return "🟡 Maybe";
  if (v === "red")    return "🔴 Not a fit";
  return v;
}


async function scanSaveLead(idx, btn, card) {
  const lead = scanCurrentLeads[idx];
  if (!lead) return;

  let autoGen = false; // Drafts now generate on the dashboard — extension is scrape-only.

  // If the post is truncated (LinkedIn "...see more" clamp), Claude only
  // gets partial context and emails come out generic. Ask the user before
  // burning a Claude call on it.
  if (autoGen && lead.truncated) {
    const choice = confirm(
      "⚠ This post is truncated (LinkedIn '...see more' clamp).\n\n" +
      "The generated email will be based on partial context and likely " +
      "come out generic.\n\n" +
      "OK = save WITHOUT auto-generated email (columns R/S stay blank — " +
      "you can generate later after opening the post manually).\n\n" +
      "Cancel = generate anyway (at your own risk)."
    );
    // OK → skip generation; Cancel → proceed with generation as normal.
    if (choice) autoGen = false;
  }

  btn.disabled = true;
  btn.textContent = autoGen ? "⏳ Generating email…" : "⏳ Saving…";

  let genFields = null;
  if (autoGen) {
    try {
      genFields = await scanGenerateEmailForLead(lead);
    } catch (err) {
      // Don't block the save if Claude fails — save raw fields and let
      // Jaydip generate manually later.
      setScanStatus(`⚠ Email generation failed (${err.message}) — saving without email draft.`, true);
    }
  }

  btn.textContent = "⏳ Saving…";
  const payload = scanBuildPayload(lead, genFields);

  try {
    const resp = await chrome.runtime.sendMessage({
      type: "SAVE_TO_SHEET",
      payload,
      force: false,
    });

    if (!resp || !resp.ok) {
      btn.disabled = false;
      btn.textContent = "💾 Save";
      setScanStatus("Save failed: " + ((resp && resp.error) || "unknown"), true);
      return;
    }

    if (resp.duplicate) {
      // Don't show row number — top-insert makes it shift on every save,
      // so the number is misleading.
      const ok = confirm(
        `⚠ Already in the sheet.\n\n${resp.message || ""}\n\nSave again anyway?`
      );
      if (!ok) {
        btn.disabled = false;
        btn.textContent = "💾 Save";
        return;
      }
      // Retry with force
      const forced = await chrome.runtime.sendMessage({
        type: "SAVE_TO_SHEET",
        payload,
        force: true,
      });
      if (!forced || !forced.ok) {
        btn.disabled = false;
        btn.textContent = "💾 Save";
        setScanStatus("Forced save failed: " + ((forced && forced.error) || "unknown"), true);
        return;
      }
      markCardSaved(card, btn, forced.row, forced.autoSkipped, forced.autoSkipReason, forced.leadId);
      return;
    }

    markCardSaved(card, btn, resp.row, resp.autoSkipped, resp.autoSkipReason, resp.leadId);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "💾 Save";
    setScanStatus("Save error: " + err.message, true);
  }
}

function markCardSaved(card, btn, row, autoSkipped, autoSkipReason, leadId) {
  card.classList.add("saved");
  if (autoSkipped) {
    // Apps Script auto-classified this row as blocked (full-time / onsite /
    // etc.) so it saved with Status="Skipped: X" and won't ever email.
    card.classList.add("skipped");
    btn.textContent = "⊘ Auto-skipped";
    btn.title = autoSkipReason
      ? `Matched blocked phrase: "${autoSkipReason}". Row saved but will not email.`
      : "Row saved but flagged as skipped — will not email.";
  } else {
    btn.textContent = "✓ Saved";
    btn.title = "";
  }
  btn.disabled = true;
  // Track saved-state by stable lead key (not index) so re-scan merges
  // don't lose it. Card carries its key in data-key; fall back to lead lookup.
  const key = card.dataset.key
    || scanLeadKey(scanCurrentLeads[Number(card.dataset.idx)] || null);
  if (key) scanSavedKeys.add(key);
  if (key && leadId) scanSavedLeadIds.set(key, leadId);
  setScanStatus(
    autoSkipped
      ? `⊘ Saved as Skipped — matched "${autoSkipReason}" (won't email).`
      : "✓ Lead saved to the LinkedIn sheet."
  );
  refreshSaveAllState();
}

function refreshSaveAllState() {
  if (!scanSaveAllBtn) return;
  // Count by key, not by saved-set size, in case some current leads share
  // a key with previously-saved ones (post-rescan dedup).
  let savedNow = 0;
  for (const l of scanCurrentLeads) {
    if (scanSavedKeys.has(scanLeadKey(l))) savedNow++;
  }
  const remaining = scanCurrentLeads.length - savedNow;
  scanSaveAllBtn.disabled = remaining <= 0;
  scanSaveAllBtn.textContent = remaining > 0
    ? `💾 Save all (${remaining})`
    : "✓ All saved";
}

// Format ms into "12s", "1m 5s", "2m" — short and human.
function scanFmtEta(ms) {
  if (!ms || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return sec ? (m + "m " + sec + "s") : (m + "m");
}

// Smooth-progress state — leads complete in chunks (~10s each with Claude
// in the loop) but the bar should creep forward continuously between
// completions so the user can SEE work happening. We snap to the real
// fraction every time a lead actually finishes, then interpolate within
// the current lead based on elapsed time vs. nominal per-lead duration.
let scanProgressState    = null; // { total, autoGen, perLeadMs, done, lastDoneAt }
let scanProgressInterval = null;

function scanProgressStart(total, autoGen) {
  if (!scanProgress) return;
  scanProgress.style.display = "block";
  scanProgressState = {
    total,
    autoGen,
    // Claude email gen dominates wall time when auto-gen is on (~10s),
    // plus ~1s sheet write. Without gen, just the sheet write.
    perLeadMs: autoGen ? 11_000 : 1_000,
    done: 0,
    lastDoneAt: Date.now(),
  };
  if (scanProgressInterval) clearInterval(scanProgressInterval);
  scanProgressInterval = setInterval(scanProgressTick, 250);
  scanProgressTick();
}

// Called every time a real lead finishes (saved/skipped/failed). Snaps the
// "done" counter to truth and resets the lastDoneAt clock so the next
// in-flight lead starts interpolating from 0.
function scanProgressUpdate(done) {
  if (!scanProgressState) return;
  scanProgressState.done = done;
  scanProgressState.lastDoneAt = Date.now();
  scanProgressTick();
}

function scanProgressTick() {
  if (!scanProgressState || !scanProgressFill) return;
  const { total, autoGen, perLeadMs, done, lastDoneAt } = scanProgressState;

  // Interpolate the in-flight lead — cap at 0.95 so we never visually
  // claim a lead is done before the server confirms. Once done >= total
  // we hold at exactly 100%.
  let smoothDone = done;
  if (done < total) {
    const elapsedInLead = Date.now() - lastDoneAt;
    const fraction = Math.min(0.95, elapsedInLead / perLeadMs);
    smoothDone = done + fraction;
  }
  const pct = total > 0 ? Math.min(100, (smoothDone / total) * 100) : 0;

  scanProgressFill.style.width = pct.toFixed(1) + "%";
  scanProgressFill.classList.toggle("complete", done >= total && total > 0);

  if (scanProgressLabel) {
    const verb = autoGen ? "Gen+Save" : "Save";
    scanProgressLabel.textContent =
      verb + " " + done + " / " + total + "  ·  " + Math.round(pct) + "%";
  }
  if (scanProgressEta) {
    const remaining = Math.max(0, total - done);
    if (remaining === 0) {
      scanProgressEta.textContent = "✓ done";
    } else {
      // Subtract time already burned on the in-flight lead so ETA
      // counts down smoothly instead of jumping.
      const elapsedInLead = Date.now() - lastDoneAt;
      const remainingMs = Math.max(0, remaining * perLeadMs - elapsedInLead);
      scanProgressEta.textContent = "~" + scanFmtEta(remainingMs) + " left";
    }
  }
}

function scanProgressStop() {
  if (scanProgressInterval) {
    clearInterval(scanProgressInterval);
    scanProgressInterval = null;
  }
}

// Hide the progress bar — called on Clear. Stops the interval too.
function scanProgressHide() {
  scanProgressStop();
  scanProgressState = null;
  if (scanProgress) scanProgress.style.display = "none";
  if (scanProgressFill) {
    scanProgressFill.style.width = "0%";
    scanProgressFill.classList.remove("complete");
  }
}

// Bulk save — iterates unsaved leads with a short delay between POSTs.
// Duplicates are SILENTLY skipped (no per-lead confirm prompt) since the
// user explicitly chose "Save all" and interrupting with modals would
// defeat the purpose.
async function scanSaveAll() {
  if (!scanCurrentLeads.length) return;

  const toSave = [];
  for (let i = 0; i < scanCurrentLeads.length; i++) {
    const k = scanLeadKey(scanCurrentLeads[i]);
    if (!scanSavedKeys.has(k)) toSave.push(i);
  }
  if (!toSave.length) {
    setScanStatus("All leads are already saved.");
    return;
  }

  const total = toSave.length;
  const autoGen = false; // Extension is scrape-only; dashboard handles Claude drafting.
  let saved = 0;
  let skippedDupe = 0;
  let autoSkipped = 0;   // Apps Script auto-classified as blocked phrase
  let failed = 0;
  let genFailed = 0;     // Claude call failed — saved without email draft
  let genSkippedTrunc = 0; // auto-skipped Claude for truncated posts

  scanSaveAllBtn.disabled = true;
  scanSaveAllBtn.textContent = `⏳ ${autoGen ? "Generating+Saving" : "Saving"} 0/${total}…`;
  scanProgressStart(total, autoGen);

  // Disable individual save buttons while the bulk run is in progress
  const cards = Array.from(scanLeadsWrap.querySelectorAll(".scan-lead"));
  cards.forEach((c) => {
    const btn = c.querySelector(".scan-lead-actions button.primary-btn");
    if (btn && !c.classList.contains("saved")) btn.disabled = true;
  });

  for (const idx of toSave) {
    const lead = scanCurrentLeads[idx];
    const card = cards[idx];
    const btn = card ? card.querySelector(".scan-lead-actions button.primary-btn") : null;

    let genFields = null;
    let genAttempted = false;
    if (autoGen) {
      // Truncated posts → auto-skip Claude in bulk mode. Prompting per-lead
      // would defeat "Save all". The row still saves, just without R/S cols.
      if (lead.truncated) {
        genSkippedTrunc++;
      } else {
        genAttempted = true;
        if (btn) btn.textContent = "⏳ Generating…";
        try {
          genFields = await scanGenerateEmailForLead(lead);
        } catch (err) {
          // Continue with raw fields — don't halt the bulk run for one gen fail
          genFailed++;
          console.warn("[Pradip AI] gen fail for lead", idx, err.message);
        }
      }
    }

    if (btn) btn.textContent = "⏳ Saving…";
    const payload = scanBuildPayload(lead, genFields);

    try {
      const resp = await chrome.runtime.sendMessage({
        type: "SAVE_TO_SHEET",
        payload,
        force: false,
      });

      if (resp && resp.ok && !resp.duplicate) {
        saved++;
        if (resp.autoSkipped) autoSkipped++;
        if (card && btn) markCardSaved(card, btn, resp.row, resp.autoSkipped, resp.autoSkipReason, resp.leadId);
      } else if (resp && resp.duplicate) {
        skippedDupe++;
        // Mark as "already saved" — same visual state as a fresh save so
        // it won't appear in a future Save-all run.
        if (card) card.classList.add("saved");
        if (btn) {
          btn.textContent = "↪ Duplicate — skipped";
          btn.disabled = true;
        }
        scanSavedKeys.add(scanLeadKey(lead));
      } else {
        failed++;
        const errText = (resp && resp.error) || "no response";
        console.warn("[Save-all] lead failed:", errText, { lead, payload });
        if (btn) {
          btn.disabled = false;
          btn.textContent = "⚠ Failed";
          btn.title = String(errText).slice(0, 200);
        }
      }
    } catch (err) {
      failed++;
      console.warn("[Save-all] exception:", err.message, { lead, payload });
      if (btn) {
        btn.disabled = false;
        btn.textContent = "⚠ Error";
        btn.title = String(err.message || "").slice(0, 200);
      }
    }

    const processed = saved + skippedDupe + failed;
    scanSaveAllBtn.textContent = `⏳ ${autoGen ? "Gen+Save" : "Save"} ${processed}/${total}…`;
    scanProgressUpdate(processed);

    // Pacing — with Claude in the loop we already have natural spacing,
    // so just a short breather. Without auto-gen, keep the 250ms stagger
    // so Apps Script doesn't throttle.
    await new Promise((r) => setTimeout(r, autoGen ? 500 : 250));
  }

  // Snap progress to final state (bar to 100% + green) and stop the
  // interpolation timer. Bar stays visible so the user sees the result.
  scanProgressUpdate(total);
  scanProgressStop();

  const parts = [];
  if (saved) parts.push(`${saved} saved`);
  if (autoSkipped) parts.push(`${autoSkipped} auto-skipped (full-time/onsite/etc.)`);
  if (skippedDupe) parts.push(`${skippedDupe} duplicate(s) skipped`);
  if (genSkippedTrunc) parts.push(`${genSkippedTrunc} saved without email-gen (truncated posts)`);
  if (genFailed) parts.push(`${genFailed} saved with Claude gen failure`);
  if (failed) parts.push(`${failed} failed`);
  setScanStatus(`Save all complete — ${parts.join(", ")}.`, failed > 0 || genFailed > 0);

  refreshSaveAllState();
}

function scanClear() {
  scanCurrentLeads = [];
  scanSavedKeys = new Set();
  scanLeadsWrap.innerHTML = "";
  scanClearBtn.disabled = true;
  if (scanSaveAllBtn) {
    scanSaveAllBtn.disabled = true;
    scanSaveAllBtn.textContent = "💾 Save all";
  }
  scanProgressHide();
  setScanStatus("");
}

function setScanStatus(text, isErr) {
  if (!scanStatus) return;
  scanStatus.textContent = text || "";
  scanStatus.className = "status" + (isErr ? " error" : "");
}

function setScanStatusLoading(text) {
  if (!scanStatus) return;
  scanStatus.textContent = text || "Working…";
  scanStatus.className = "status loading";
}

if (scanNowBtn)     scanNowBtn.addEventListener("click", scanRunNow);
if (scanSaveAllBtn) scanSaveAllBtn.addEventListener("click", scanSaveAll);
if (scanClearBtn)   scanClearBtn.addEventListener("click", scanClear);
if (autoScrollBtn)  autoScrollBtn.addEventListener("click", autoScrollToggle);

// ------------------------------------------------------------
// Auto-scroll: runs for a user-selected duration (or indefinitely),
// scrolling the active LinkedIn tab with small random hops so posts
// animate past the viewport. Stops only on timer expiry or toggle.
//
// LinkedIn's feed is infinite — when we "reach the bottom" it's only
// transient while more posts stream in. So we don't early-stop on
// atBottom; instead we pause briefly (letting the feed load) then
// keep going.
// ------------------------------------------------------------
let autoScrollRunning = false;
let autoScrollAbort   = false;

function autoScrollSetLabel(running, remainingLabel) {
  if (!autoScrollBtn) return;
  autoScrollBtn.textContent = running
    ? (remainingLabel ? `⏸ Stop (${remainingLabel})` : "⏸ Stop auto-scroll")
    : "🖱 Auto-scroll";
  autoScrollBtn.classList.toggle("auto-running", running);
}

function autoScrollRandomWait() {
  // 1.2 – 2.6s between hops — enough for the smooth animation to
  // finish, short enough that scrolling feels continuous.
  return 1200 + Math.round(Math.random() * 1400);
}

function formatDuration(sec) {
  if (sec <= 0) return "";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m && s) return `${m}m ${s}s`;
  if (m) return `${m}m`;
  return `${s}s`;
}

async function autoScrollToggle() {
  if (autoScrollRunning) {
    autoScrollAbort = true;
    return;
  }
  await scanRefreshAvailability();
  if (!scanActiveTabId) {
    setScanStatus("No LinkedIn search tab detected. Open one first.", true);
    return;
  }

  const durationSec = Number(autoScrollDuration?.value || 0); // 0 = infinite
  const deadline = durationSec > 0 ? Date.now() + durationSec * 1000 : 0;

  autoScrollRunning = true;
  autoScrollAbort = false;
  autoScrollSetLabel(true, durationSec > 0 ? formatDuration(durationSec) : "∞");
  if (autoScrollProgress) autoScrollProgress.style.display = "";
  setScanStatus(
    durationSec > 0
      ? `Auto-scroll running for ${formatDuration(durationSec)} — switch to the LinkedIn tab to watch.`
      : "Auto-scroll running — click again to stop.",
  );

  // Initial scan BEFORE we scroll so the Save-all count starts populating
  // from the posts already on screen. Otherwise the user has to wait a
  // full hop cycle to see anything in the queue.
  try { await scanRunNow(); } catch (_) {}
  if (autoScrollAbort) {
    autoScrollRunning = false;
    autoScrollSetLabel(false);
    if (autoScrollProgress) autoScrollProgress.style.display = "none";
    scanRefreshAvailability();
    return;
  }

  let hops = 0;
  let atBottomStreak = 0;
  let lastScrollY = 0;
  let lastMaxScroll = 0;
  try {
    while (!autoScrollAbort) {
      // Deadline check
      if (deadline && Date.now() >= deadline) {
        setScanStatus(`Auto-scroll done — ran for ${formatDuration(durationSec)} (${hops} hops).`);
        break;
      }

      let atBottom = false;
      let resp = null;
      try {
        resp = await chrome.tabs.sendMessage(scanActiveTabId, { type: "SCROLL_PAGE" });
        atBottom = !!(resp && resp.atBottom);
        hops++;
        if (resp && typeof resp.scrollY === 'number') lastScrollY = resp.scrollY;
        if (resp && typeof resp.maxScroll === 'number') lastMaxScroll = resp.maxScroll;
      } catch (err) {
        setScanStatus("Auto-scroll lost the tab: " + (err.message || err), true);
        break;
      }

      // Let LinkedIn render newly-streamed posts before we re-scan.
      await new Promise((r) => setTimeout(r, 800 + Math.round(Math.random() * 400)));
      if (autoScrollAbort) break;

      // Passive scan. Swallow errors so one failed scan doesn't kill
      // the loop. scanRunNow internally enables Save all/Clear when it
      // finds new leads.
      const leadsBefore = scanCurrentLeads.length;
      try {
        await scanRunNow();
      } catch (_) {}
      if (autoScrollAbort) break;
      const leadsAdded = scanCurrentLeads.length - leadsBefore;

      // At the bottom — LinkedIn streams more posts on demand. Give it a
      // longer pause so more content loads, then keep going. Don't stop.
      let wait = autoScrollRandomWait();
      if (atBottom) {
        atBottomStreak++;
        wait = Math.min(5000, 2500 + atBottomStreak * 300);
      } else {
        atBottomStreak = 0;
      }

      // Live UI update: button countdown + progress bar + label
      const remainingSec = deadline
        ? Math.max(0, Math.round((deadline - Date.now()) / 1000))
        : null;
      autoScrollSetLabel(true, remainingSec !== null ? formatDuration(remainingSec) : "∞");

      const pct = resp && typeof resp.progressPct === 'number' ? resp.progressPct : 0;
      if (autoScrollProgressFill) autoScrollProgressFill.style.width = pct + "%";
      if (autoScrollProgressLabel) {
        const tail = atBottom ? " · 🔄 loading more" : "";
        autoScrollProgressLabel.textContent =
          `Hop ${hops} · ${pct}% · ${scanCurrentLeads.length} leads (+${leadsAdded})` +
          (remainingSec !== null ? ` · ${formatDuration(remainingSec)} left` : "") +
          tail;
      }

      await new Promise((r) => setTimeout(r, wait));
    }
    if (autoScrollAbort) setScanStatus(`Auto-scroll stopped after ${hops} hops.`);
  } finally {
    autoScrollRunning = false;
    autoScrollAbort = false;
    autoScrollSetLabel(false);
    if (autoScrollProgress) autoScrollProgress.style.display = "none";
    if (autoScrollProgressFill) autoScrollProgressFill.style.width = "0%";
    scanRefreshAvailability();
  }
}

// Initial availability check + refresh when the user switches tabs in
// Chrome (so the button enables as soon as they navigate to a search page).
if (scanPanel) {
  scanRefreshAvailability();
  try {
    chrome.tabs.onActivated.addListener(() => scanRefreshAvailability());
    chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => {
      if (changeInfo.url || changeInfo.status === "complete") scanRefreshAvailability();
    });
  } catch (_) {}
}

// ============================================================
// POSTS TAB — LinkedIn post draft generator + manager
// ============================================================

const postsGenerateBtn      = document.getElementById("postsGenerateBtn");
const postsStatus           = document.getElementById("postsStatus");
const postsDraftsWrap       = document.getElementById("postsDraftsWrap");
const postsAutoDraftToggle  = document.getElementById("postsAutoDraftToggle");
const postsAutoDraftHour    = document.getElementById("postsAutoDraftHour");

// Recent-themes log bounds — keep the last 7 so the rotation picks
// something Jaydip hasn't posted this week.
const POSTS_RECENT_THEMES_MAX = 7;
// Cap stored drafts so chrome.storage doesn't bloat (LinkedIn posts rarely
// benefit from deep history; posted ones are the only ones worth keeping
// anyway, and we trim to 50 including discarded).
const POSTS_DRAFTS_MAX = 50;

function getCheckedRadio(name) {
  const el = document.querySelector('input[name="' + name + '"]:checked');
  return el ? el.value : null;
}

function setPostsStatus(text, isError) {
  if (!postsStatus) return;
  postsStatus.textContent = text || "";
  postsStatus.classList.toggle("error", !!isError);
  postsStatus.classList.remove("loading");
}

function setPostsStatusLoading(text) {
  if (!postsStatus) return;
  postsStatus.textContent = text || "";
  postsStatus.classList.remove("error");
  postsStatus.classList.add("loading");
}

async function loadPostDrafts() {
  const { postDrafts } = await chrome.storage.local.get(["postDrafts"]);
  return Array.isArray(postDrafts) ? postDrafts : [];
}

async function savePostDrafts(drafts) {
  const trimmed = drafts.slice(0, POSTS_DRAFTS_MAX);
  await chrome.storage.local.set({ postDrafts: trimmed });
}

async function loadRecentThemes() {
  const { postRecentThemes } = await chrome.storage.local.get(["postRecentThemes"]);
  return Array.isArray(postRecentThemes) ? postRecentThemes : [];
}

async function pushRecentTheme(theme) {
  if (!theme) return;
  const recent = await loadRecentThemes();
  // Remove any existing entry for this theme and unshift fresh — most
  // recently-posted theme sits at index 0, oldest drops off at the cap.
  const filtered = recent.filter((t) => t !== theme);
  filtered.unshift(theme);
  await chrome.storage.local.set({
    postRecentThemes: filtered.slice(0, POSTS_RECENT_THEMES_MAX),
  });
}

async function handleGeneratePost() {
  if (!postsGenerateBtn) return;
  const length = getCheckedRadio("postsLength") || "medium";
  const tone = getCheckedRadio("postsTone") || "casual";

  postsGenerateBtn.disabled = true;
  setPostsStatusLoading("Generating 3 variants via Claude — ~20-30 sec…");

  const recentThemes = await loadRecentThemes();

  let resp;
  try {
    resp = await chrome.runtime.sendMessage({
      type: "GENERATE_POST",
      options: { length, tone, recentThemes },
    });
  } catch (err) {
    postsGenerateBtn.disabled = false;
    setPostsStatus("Could not reach Claude: " + (err.message || err), true);
    return;
  }
  postsGenerateBtn.disabled = false;

  if (!resp || !resp.ok) {
    setPostsStatus("Generation failed: " + ((resp && resp.error) || "no response"), true);
    return;
  }

  const newDrafts = (resp.drafts || []).map((d) => ({ ...d, source: "manual" }));
  if (!newDrafts.length) {
    setPostsStatus("Claude returned no variants. Try again.", true);
    return;
  }

  const existing = await loadPostDrafts();
  const merged = [...newDrafts, ...existing];
  await savePostDrafts(merged);

  setPostsStatus(
    "✅ 3 variants on theme: " + resp.theme + " (" + resp.length + ", " + resp.tone + "). Pick one, edit if needed, then Open + Paste."
  );
  renderPostDrafts(merged);
}

function renderPostDrafts(drafts) {
  if (!postsDraftsWrap) return;
  postsDraftsWrap.innerHTML = "";
  if (!drafts || !drafts.length) return;

  drafts.forEach((d) => {
    const card = document.createElement("div");
    card.className = "posts-draft";
    if (d.status === "posted") card.classList.add("posted");
    if (d.status === "discarded") card.classList.add("discarded");
    card.dataset.id = d.id;

    const head = document.createElement("div");
    head.className = "posts-draft-head";
    const meta = document.createElement("div");
    meta.className = "posts-draft-meta";
    meta.append(
      makePostBadge(d.theme || "—", "b-theme"),
      makePostBadge(d.tone || "—", "b-tone"),
      makePostBadge((d.length || "—") + (d.variant ? " v" + d.variant : ""), "b-length"),
      makePostBadge((d.charCount || (d.text || "").length) + " chars", "b-chars")
    );
    if (d.source === "auto") meta.appendChild(makePostBadge("auto", "b-auto"));
    if (d.status === "posted") meta.appendChild(makePostBadge("posted", "b-auto"));
    head.appendChild(meta);
    card.appendChild(head);

    const body = document.createElement("div");
    body.className = "posts-draft-text";
    body.textContent = d.text || "";
    card.appendChild(body);

    const actions = document.createElement("div");
    actions.className = "posts-draft-actions";

    const editBtn = document.createElement("button");
    editBtn.textContent = "✏ Edit";
    editBtn.addEventListener("click", async () => {
      const editing = body.contentEditable === "true";
      if (editing) {
        body.contentEditable = "false";
        editBtn.textContent = "✏ Edit";
        await persistDraftText(d.id, body.innerText);
        // Re-render so the "X chars" badge reflects the edited length
        // (and any other derived metadata stays in sync).
        const refreshed = await loadPostDrafts();
        renderPostDrafts(refreshed);
      } else {
        body.contentEditable = "true";
        body.focus();
        editBtn.textContent = "💾 Save";
      }
    });
    actions.appendChild(editBtn);

    const copyBtn = document.createElement("button");
    copyBtn.textContent = "📋 Copy";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(body.innerText);
        copyBtn.textContent = "✓ Copied";
        setTimeout(() => { copyBtn.textContent = "📋 Copy"; }, 1500);
      } catch (_) {
        setPostsStatus("Clipboard blocked. Select and Ctrl+C manually.", true);
      }
    });
    actions.appendChild(copyBtn);

    const openBtn = document.createElement("button");
    openBtn.className = "primary-btn";
    openBtn.textContent = "🚀 Open + Paste";
    openBtn.addEventListener("click", async () => {
      openBtn.disabled = true;
      setPostsStatusLoading("Opening LinkedIn feed + pasting…");
      try {
        const r = await chrome.runtime.sendMessage({
          type: "PASTE_POST_TO_FEED",
          text: body.innerText,
        });
        if (r && r.ok) {
          setPostsStatus("✅ Pasted into composer. Review and click Post in LinkedIn.");
        } else {
          setPostsStatus("Paste failed: " + ((r && r.error) || "no response"), true);
        }
      } catch (err) {
        setPostsStatus("Paste error: " + (err.message || err), true);
      } finally {
        openBtn.disabled = false;
      }
    });
    actions.appendChild(openBtn);

    const postedBtn = document.createElement("button");
    postedBtn.textContent = d.status === "posted" ? "✓ Posted" : "Mark posted";
    postedBtn.disabled = d.status === "posted";
    postedBtn.addEventListener("click", async () => {
      await markDraftPosted(d.id, d.theme);
      const refreshed = await loadPostDrafts();
      renderPostDrafts(refreshed);
    });
    actions.appendChild(postedBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.textContent = "🗑";
    deleteBtn.title = "Delete draft";
    deleteBtn.addEventListener("click", async () => {
      const all = await loadPostDrafts();
      await savePostDrafts(all.filter((x) => x.id !== d.id));
      const refreshed = await loadPostDrafts();
      renderPostDrafts(refreshed);
    });
    actions.appendChild(deleteBtn);

    card.appendChild(actions);
    postsDraftsWrap.appendChild(card);
  });
}

function makePostBadge(text, cls) {
  const s = document.createElement("span");
  s.className = "posts-draft-badge " + (cls || "");
  s.textContent = text;
  return s;
}

async function persistDraftText(id, text) {
  const drafts = await loadPostDrafts();
  const updated = drafts.map((d) =>
    d.id === id ? { ...d, text, charCount: text.length } : d
  );
  await savePostDrafts(updated);
}

async function markDraftPosted(id, theme) {
  const drafts = await loadPostDrafts();
  const updated = drafts.map((d) =>
    d.id === id ? { ...d, status: "posted", postedAt: Date.now() } : d
  );
  await savePostDrafts(updated);
  if (theme) await pushRecentTheme(theme);
}

async function initPostsAutoDraft() {
  const { postAutoDraftEnabled, postAutoDraftHour: hourStored } =
    await chrome.storage.local.get(["postAutoDraftEnabled", "postAutoDraftHour"]);
  if (postsAutoDraftToggle) postsAutoDraftToggle.checked = !!postAutoDraftEnabled;
  if (postsAutoDraftHour && Number.isFinite(Number(hourStored))) {
    postsAutoDraftHour.value = String(Number(hourStored));
  }
}

async function onPostsAutoDraftChange() {
  const enabled = !!(postsAutoDraftToggle && postsAutoDraftToggle.checked);
  let hour = Number(postsAutoDraftHour && postsAutoDraftHour.value);
  if (!Number.isFinite(hour) || hour < 0 || hour > 23) hour = 9;
  await chrome.storage.local.set({
    postAutoDraftEnabled: enabled,
    postAutoDraftHour: hour,
  });
  // Background rebuilds the alarm from these values on startup/install,
  // but we also ping it so the new schedule takes effect immediately.
  try {
    await chrome.runtime.sendMessage({ type: "RESCHEDULE_POST_ALARM" });
  } catch (_) {}
}

if (postsGenerateBtn) postsGenerateBtn.addEventListener("click", handleGeneratePost);
if (postsAutoDraftToggle) postsAutoDraftToggle.addEventListener("change", onPostsAutoDraftChange);
if (postsAutoDraftHour) postsAutoDraftHour.addEventListener("change", onPostsAutoDraftChange);

(async function initPostsTab() {
  if (!postsDraftsWrap) return;
  await initPostsAutoDraft();
  const drafts = await loadPostDrafts();
  renderPostDrafts(drafts);
})();
