// Side-panel UI — API-key setup + connection check + stats preview.

const $ = (id) => document.getElementById(id);

async function refreshKeyField() {
  const k = await getApiKey();
  $("api-key").value = k;
}

async function checkConnection() {
  const el = $("conn-status");
  try {
    const stats = await apiFetch(API.overview);
    el.textContent = `Connected · ${stats.total} leads`;
    el.className = "status ok";
    renderStats(stats);
  } catch (err) {
    el.textContent = `Offline — ${err.message}`;
    el.className = "status err";
  }
}

function renderStats(s) {
  const host = $("stats");
  const rows = [
    ["Total", s.total],
    ["Drafted", s.drafted],
    ["Sent today", `${s.sent_today} / ${s.quota_cap}`],
    ["Replied", s.replied],
    ["Safety", s.safety_mode === "max" ? "Maximum" : "Normal"],
  ];
  host.innerHTML = rows
    .map(
      ([k, v]) =>
        `<div class="stat-row"><span>${k}</span><span>${v}</span></div>`,
    )
    .join("");
  host.classList.remove("muted");
}

$("save-key").addEventListener("click", async () => {
  await setApiKey($("api-key").value.trim());
  $("key-msg").textContent = "Saved.";
  $("key-msg").className = "status ok";
  checkConnection();
});

$("test-key").addEventListener("click", checkConnection);

$("scan").addEventListener("click", async () => {
  const btn = $("scan");
  const msg = $("scan-msg");
  btn.disabled = true;
  msg.textContent = "Scanning…";
  msg.className = "status muted";
  try {
    const res = await chrome.runtime.sendMessage({ type: "SCAN_ACTIVE_TAB" });
    if (!res?.ok) {
      msg.textContent = res?.error || "Scan failed";
      msg.className = "status err";
      return;
    }
    const { scan, ingest } = res;
    msg.textContent = `Scanned ${scan.stats.containers} posts · kept ${scan.stats.kept} · ingest +${ingest.inserted} / ~${ingest.updated}`;
    msg.className = "status ok";
    checkConnection();
  } catch (err) {
    msg.textContent = err.message;
    msg.className = "status err";
  } finally {
    btn.disabled = false;
  }
});

refreshKeyField().then(checkConnection);
