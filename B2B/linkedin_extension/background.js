// Service worker — routes side-panel commands and content-script events
// to the dashboard backend. No Claude / no sheet; pure relay.

importScripts("config.js");

chrome.runtime.onInstalled.addListener(() => {
  if (chrome.sidePanel?.setPanelBehavior) {
    chrome.sidePanel
      .setPanelBehavior({ openPanelOnActionClick: true })
      .catch((err) => console.error("sidePanel setup failed:", err));
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg?.type) return;

  if (msg.type === "GET_STATUS") {
    apiFetch(API.overview)
      .then((stats) => sendResponse({ ok: true, stats }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  // Side panel asks background to run a scan on the active LinkedIn tab.
  if (msg.type === "SCAN_ACTIVE_TAB") {
    runScan()
      .then((r) => sendResponse(r))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  // Content script spotted a LinkedIn account-warning banner.
  if (msg.type === "ACCOUNT_WARNING") {
    apiFetch(API.warning, {
      method: "POST",
      body: JSON.stringify({ phrase: msg.phrase, url: msg.url || "" }),
    })
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
});

async function runScan() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url || !/https:\/\/www\.linkedin\.com\/search\/results\/content/.test(tab.url)) {
    return {
      ok: false,
      error: "Open a LinkedIn search → Posts page first.",
    };
  }

  const scan = await chrome.tabs.sendMessage(tab.id, { type: "SCAN_SEARCH_PAGE" });
  if (!scan?.ok) return { ok: false, error: scan?.error || "Scan failed" };
  if (!scan.leads?.length) {
    return { ok: true, scan, ingest: { inserted: 0, updated: 0, total: 0 } };
  }

  // Strip debug-only fields before POST.
  const cleaned = scan.leads.map(({ urn, truncated, ...rest }) => rest);
  const ingest = await apiFetch(API.ingest, {
    method: "POST",
    body: JSON.stringify({ leads: cleaned }),
  });
  return { ok: true, scan, ingest };
}
