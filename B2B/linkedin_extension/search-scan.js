// LinkedIn search-results DOM scanner.
// Runs on: linkedin.com/search/results/content/*
//
// Passive — scans what's rendered. Triggered by SCAN_SEARCH_PAGE from the
// side panel. Does NOT push to backend directly; returns leads to the
// caller which then forwards to background.js for the /ingest POST.

(function () {
  if (window.__bcLinkedInScanLoaded) return;
  window.__bcLinkedInScanLoaded = true;

  const EMAIL_RE = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g;
  const PHONE_RE = /(?:\+\d[\d\s().-]{7,18}\d)|(?:\b\d[\d\s().-]{7,18}\d\b)/g;
  const HIRING_RE = /\b(hiring|we'?re hiring|we are hiring|looking for|looking to hire|seeking|recruit(ing|er)?|job opening|open (role|position|to)|apply (to|via|here)|send (your )?(cv|resume)|dm me|reach out|contact us|email (me|us|at)|drop (your )?(cv|resume)|interested candidates|candidates can apply|please share)\b/i;
  const TRUNC_RE = /(…|\.\.\.)\s*see more|\s{0,3}show more\s*$|…$|\.\.\.$/i;
  const AUTHOR_BLOCKLIST = new Set([
    "linkedin", "linkedin news", "linkedin learning", "linkedin corporation",
  ]);
  const WARNING_RE = /(restricted your (account|access)|temporarily (limited|restricted)|unusual activity|automated activity|verify your identity|confirm you'?re not a robot)/i;

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.type !== "SCAN_SEARCH_PAGE") return;
    try {
      const result = scan();
      sendResponse({
        ok: true,
        leads: result.leads,
        stats: result.stats,
        selectorUsed: result.selectorUsed,
        scannedAt: new Date().toISOString(),
        url: location.href,
      });
    } catch (err) {
      sendResponse({ ok: false, error: err.message || String(err) });
    }
    return true;
  });

  // Passive warning-phrase check — one-shot at load.
  try {
    const body = (document.body.innerText || "").slice(0, 10000);
    const m = body.match(WARNING_RE);
    if (m) {
      chrome.runtime.sendMessage({
        type: "ACCOUNT_WARNING",
        phrase: m[0],
        url: location.href,
      });
    }
  } catch (_) {}

  function scan() {
    const { containers, selectorUsed } = findPostContainers();
    const leads = [];
    const seen = new Set();
    const stats = { containers: containers.length, kept: 0, dupe: 0, skipped: 0 };

    for (const el of containers) {
      const lead = extractLead(el);
      if (!lead) { stats.skipped++; continue; }
      const key = lead.urn || lead.post_url || lead.post_text.slice(0, 80);
      if (seen.has(key)) { stats.dupe++; continue; }
      seen.add(key);
      leads.push(lead);
      stats.kept++;
    }

    return { leads, stats, selectorUsed };
  }

  function findPostContainers() {
    const attempts = [
      'div[data-urn^="urn:li:activity:"]',
      '[data-urn^="urn:li:activity:"]',
      '[data-id^="urn:li:activity:"]',
      ".feed-shared-update-v2",
      ".update-components-update-v2",
      "li.reusable-search__result-container",
      '[data-urn*="activity"]',
      '[data-urn*="ugcPost"]',
      "[data-urn]",
    ];
    for (const sel of attempts) {
      try {
        const nodes = Array.from(document.querySelectorAll(sel));
        if (nodes.length) {
          const canon = canonicalize(nodes);
          if (canon.length) return { containers: canon, selectorUsed: sel };
        }
      } catch (_) {}
    }
    return { containers: [], selectorUsed: "none" };
  }

  function canonicalize(nodes) {
    const seen = new Set();
    const out = [];
    for (const n of nodes) {
      let el = n;
      let best = n;
      for (let i = 0; i < 6 && el; i++) {
        const hasAuthor = !!el.querySelector('a[href*="/in/"], a[href*="/company/"]');
        const len = (el.innerText || "").length;
        if (hasAuthor && len >= 60) { best = el; break; }
        el = el.parentElement;
      }
      if (!seen.has(best)) {
        seen.add(best);
        out.push(best);
      }
    }
    return out;
  }

  function extractLead(el) {
    let urn = el.getAttribute("data-urn") || el.getAttribute("data-id") || "";
    if (!urn) {
      const inner = el.querySelector(
        '[data-urn^="urn:li:activity:"], [data-id^="urn:li:activity:"]',
      );
      if (inner) urn = inner.getAttribute("data-urn") || inner.getAttribute("data-id") || "";
    }

    const text = extractText(el);
    if (!text || text.length < 20) return null;

    const author = extractAuthor(el);
    if (author && AUTHOR_BLOCKLIST.has(author.toLowerCase())) return null;

    const emails = uniqueMatches(text, EMAIL_RE).filter(plausibleEmail);
    const phones = uniqueMatches(text, PHONE_RE).map(normPhone).filter(Boolean);
    const hiring = HIRING_RE.test(text);
    if (!emails.length && !phones.length && !hiring) return null;

    const full = text.slice(0, 4000);
    const post_url = urn
      ? `https://www.linkedin.com/feed/update/${urn}/`
      : location.href;

    // Shape matches backend IngestPost Pydantic model.
    return {
      post_url,
      posted_by: author || null,
      company: null,
      role: null,
      tech_stack: null,
      rate: null,
      location: null,
      tags: hiring ? "hiring" : null,
      post_text: full,
      email: emails[0] || null,
      phone: phones[0] || null,
      // extras kept for debug only
      urn,
      truncated: TRUNC_RE.test(full.slice(-40)),
    };
  }

  function extractText(el) {
    const selectors = [
      ".feed-shared-inline-show-more-text",
      ".feed-shared-inline-show-more-text--minimal-padding",
      ".update-components-text",
      ".feed-shared-update-v2__commentary",
      ".update-components-update-v2__commentary",
      ".feed-shared-text",
      ".break-words",
    ];
    const candidates = [];
    for (const sel of selectors) {
      for (const n of el.querySelectorAll(sel)) {
        const tc = norm(n.textContent || "");
        const it = norm(n.innerText || "");
        if (tc) candidates.push(tc);
        if (it && it !== tc) candidates.push(it);
      }
    }
    const fallback = norm(el.textContent || "");
    if (fallback) candidates.push(fallback);
    if (!candidates.length) return "";
    candidates.sort((a, b) => b.length - a.length);
    return candidates[0].replace(/\s*…?\s*(see more|show more)\s*$/i, "").trim();
  }

  function extractAuthor(el) {
    const selectors = [
      '.update-components-actor__name span[aria-hidden="true"]',
      '.update-components-actor__title span[aria-hidden="true"]',
      ".update-components-actor__name",
      ".update-components-actor__title",
    ];
    for (const sel of selectors) {
      const node = el.querySelector(sel);
      if (node?.innerText?.trim()) {
        return node.innerText.trim().split("\n")[0].trim();
      }
    }
    return "";
  }

  function uniqueMatches(text, re, cap = 5) {
    const seen = new Set();
    const out = [];
    const regex = new RegExp(re.source, re.flags);
    let m;
    while ((m = regex.exec(text)) !== null) {
      const v = m[0].trim();
      const key = v.toLowerCase();
      if (!seen.has(key)) { seen.add(key); out.push(v); }
      if (out.length >= cap) break;
    }
    return out;
  }

  function plausibleEmail(e) {
    const lower = e.toLowerCase();
    if (lower.length > 100) return false;
    if (/\.(png|jpg|gif)$/.test(lower)) return false;
    if (/@(sentry|linkedin|licdn|media-exp|example|test)\./.test(lower)) return false;
    return true;
  }

  function normPhone(raw) {
    const c = String(raw || "").replace(/\s+/g, " ").trim();
    const d = c.replace(/\D/g, "");
    return d.length >= 9 && d.length <= 15 ? c : "";
  }

  function norm(s) {
    return String(s || "").replace(/\s+/g, " ").replace(/\s*\n\s*/g, "\n").trim();
  }
})();
