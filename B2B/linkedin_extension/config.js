// Shared config + storage helpers for the BitCoding LinkedIn extension.
// Phase 1: dashboard URL + API-key plumbing only. Scan/send logic arrives
// in Phase 2.

const DASHBOARD_BASE = "http://localhost:8900";
const API = {
  overview: `${DASHBOARD_BASE}/api/linkedin/overview`,
  safety: `${DASHBOARD_BASE}/api/linkedin/safety`,
  ingest: `${DASHBOARD_BASE}/api/linkedin/ingest`,
  warning: `${DASHBOARD_BASE}/api/linkedin/account-warning`,
};

const STORAGE_KEYS = {
  apiKey: "bc_linkedin_api_key",
  lastStatus: "bc_linkedin_last_status",
};

async function getApiKey() {
  const { [STORAGE_KEYS.apiKey]: k } = await chrome.storage.local.get(
    STORAGE_KEYS.apiKey,
  );
  return k || "";
}

async function setApiKey(key) {
  await chrome.storage.local.set({ [STORAGE_KEYS.apiKey]: key });
}

async function apiFetch(path, init = {}) {
  const key = await getApiKey();
  const res = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Ext-Key": key,
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? ` — ${text}` : ""}`);
  }
  return res.json();
}

if (typeof self !== "undefined") {
  self.DASHBOARD_BASE = DASHBOARD_BASE;
  self.API = API;
  self.STORAGE_KEYS = STORAGE_KEYS;
  self.getApiKey = getApiKey;
  self.setApiKey = setApiKey;
  self.apiFetch = apiFetch;
}
