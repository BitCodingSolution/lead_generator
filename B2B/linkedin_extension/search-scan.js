// ============================================================
// Pradip AI — search-results DOM scanner
// Runs on: linkedin.com/search/results/content/*
// Responsibilities:
//   - On SCAN_SEARCH_PAGE request from the side panel, read all currently
//     rendered posts, extract {postUrl, author, text, emails[], phones[]},
//     and return them as a leads array.
//   - Passive only — no scrolling, no "see more" clicks. Operates on what
//     the page has already rendered for Jaydip.
// ============================================================

(function () {
  if (window.__pradipAiSearchScanLoaded) return;
  window.__pradipAiSearchScanLoaded = true;

  // ---- Scroll helpers ----
  // LinkedIn doesn't always scroll the window; often a nested div with
  // overflow:auto is the real scroll container. Pick the element with
  // the largest overflow delta (scrollHeight - clientHeight).
  function findScrollTarget() {
    const candidates = [];
    // Prefer the document root first — on many pages this IS the scroller.
    const rootDelta = (document.documentElement.scrollHeight || 0) -
                      (document.documentElement.clientHeight || 0);
    if (rootDelta > 50) {
      return {
        el: null, // sentinel for window-level scroll
        scrollHeight: document.documentElement.scrollHeight,
        clientHeight: window.innerHeight,
      };
    }
    // Otherwise find the biggest overflow-y element.
    const all = document.querySelectorAll('div, main, section, ul');
    for (const el of all) {
      const style = getComputedStyle(el);
      if (!/(auto|scroll)/.test(style.overflowY)) continue;
      const delta = el.scrollHeight - el.clientHeight;
      if (delta > 50) candidates.push({ el, delta });
    }
    candidates.sort((a, b) => b.delta - a.delta);
    const best = candidates[0];
    if (best) {
      return {
        el: best.el,
        scrollHeight: best.el.scrollHeight,
        clientHeight: best.el.clientHeight,
      };
    }
    return {
      el: null,
      scrollHeight: document.documentElement.scrollHeight || 0,
      clientHeight: window.innerHeight || 800,
    };
  }

  // Visible smooth scroll via rAF over `durationMs`. Works on both
  // window-level and element-level scroll targets.
  function smoothScrollStep(target, delta, durationMs) {
    const start = target.el ? target.el.scrollTop : window.scrollY;
    const end = start + delta;
    const t0 = performance.now();
    function tick(now) {
      const p = Math.min(1, (now - t0) / durationMs);
      // ease-out cubic for a natural deceleration
      const eased = 1 - Math.pow(1 - p, 3);
      const y = start + (end - start) * eased;
      if (target.el) target.el.scrollTop = y;
      else window.scrollTo({ top: y, behavior: 'auto' });
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  // Find and click LinkedIn's "Load more" button. Returns { clicked, via }.
  // Why text-match fallback: LinkedIn's class names are obfuscated and
  // change frequently, but the button label is stable. Strict equality on
  // a tiny set of phrases keeps us from misclicking unrelated buttons.
  function clickLoadMoreIfVisible() {
    const semanticSelectors = [
      'button.scaffold-finite-scroll__load-button',
      'button[aria-label*="Load more" i]',
      'button[aria-label*="Show more results" i]',
    ];
    for (const sel of semanticSelectors) {
      try {
        const btns = document.querySelectorAll(sel);
        for (const b of btns) {
          if (b.offsetParent !== null && !b.disabled) {
            b.scrollIntoView({ block: 'center', behavior: 'instant' });
            b.click();
            return { clicked: true, via: sel };
          }
        }
      } catch (_) {}
    }
    const labels = new Set([
      'load more',
      'load more results',
      'show more',
      'show more results',
    ]);
    try {
      const all = document.querySelectorAll('button');
      for (const b of all) {
        if (!b.offsetParent || b.disabled) continue;
        const text = (b.innerText || b.textContent || '').trim().toLowerCase();
        if (labels.has(text)) {
          b.scrollIntoView({ block: 'center', behavior: 'instant' });
          b.click();
          return { clicked: true, via: 'text:' + text };
        }
      }
    } catch (_) {}
    return { clicked: false };
  }

  // Email regex — deliberately conservative. LinkedIn scrubs common patterns
  // like "dev [at] company [dot] com" so we don't try to unscramble those
  // in v1; only pick up literal email strings. False positives are worse
  // than missed ones at this stage.
  const EMAIL_RE = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g;

  // Phone regex — +country-prefixed or 10+ digit runs with optional
  // separators. Tight enough to avoid matching years / IDs / LinkedIn
  // numeric fragments. Requires a + or 9+ digits in a row (spacing/dashes
  // allowed) so "2024" and "1,500/month" don't match.
  const PHONE_RE = /(?:\+\d[\d\s().-]{7,18}\d)|(?:\b\d[\d\s().-]{7,18}\d\b)/g;

  // Authors that are LinkedIn itself or clearly non-lead noise
  const AUTHOR_BLOCKLIST = new Set([
    'linkedin', 'linkedin news', 'linkedin learning', 'linkedin corporation'
  ]);

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg) return;
    if (msg.type === 'SCAN_SEARCH_PAGE') {
      try {
        const result = scanSearchResultsPage();
        sendResponse({
          ok: true,
          leads: result.leads,
          stats: result.stats,
          selectorUsed: result.selectorUsed,
          scannedAt: new Date().toISOString(),
          url: location.href
        });
      } catch (err) {
        sendResponse({ ok: false, error: err.message || String(err) });
      }
      return true;
    }
    if (msg.type === 'SCROLL_PAGE') {
      // LinkedIn's scaffold puts the scroll on the main content region,
      // not on window. window.scrollBy silently no-ops on those layouts.
      // Strategy: find the element that's actually scrollable (largest
      // scrollHeight - clientHeight), animate .scrollTop on it, and
      // dispatch a real wheel event to trigger LinkedIn's infinite
      // loader. Fall back to window scroll if nothing better exists.
      try {
        const viewport = window.innerHeight || 800;
        const direction = Math.random() < 0.08 ? -1 : 1;
        const jitter = 0.5 + Math.random() * 0.5;
        const delta = direction * Math.max(300, Math.round(viewport * jitter));

        const target = findScrollTarget();
        const before = target.el ? target.el.scrollTop : window.scrollY;

        // Animate scrollTop in small steps for a visible smooth motion.
        // Using scrollTo({ behavior: 'smooth' }) is brittle across
        // LinkedIn's custom scroll containers; explicit rAF loop is more
        // reliable and visibly scrolls at ~60fps.
        smoothScrollStep(target, delta, 600);

        // Real wheel event near the bottom so LinkedIn requests more posts.
        // Fire every hop so the feed keeps streaming — belt-and-suspenders.
        try {
          const wheelEvt = new WheelEvent('wheel', {
            deltaY: Math.sign(delta) * 400,
            bubbles: true,
            cancelable: true,
          });
          (target.el || document.scrollingElement || document.body).dispatchEvent(wheelEvt);
        } catch (_) {}

        // Poll briefly so the response reflects the actual new position
        // after the animation is underway (not before it starts).
        setTimeout(() => {
          const after = target.el ? target.el.scrollTop : window.scrollY;
          const maxScroll = Math.max(0, target.scrollHeight - target.clientHeight);
          const atBottom = after + target.clientHeight + 4 >= target.scrollHeight;
          // LinkedIn's newer search-results layout ends with a discrete
          // "Load more" button instead of pure infinite scroll. Wheel
          // events alone won't fire it, so on every hop we look for the
          // button and click it if visible. Safe even mid-feed: button
          // only renders once LinkedIn has streamed everything it can.
          const loadMore = clickLoadMoreIfVisible();
          sendResponse({
            ok: true,
            delta,
            scrollY: after,
            previousScrollY: before,
            maxScroll,
            scrollHeight: target.scrollHeight,
            viewport: target.clientHeight,
            progressPct: maxScroll > 0
              ? Math.min(100, Math.round((after / maxScroll) * 100))
              : 0,
            atBottom,
            loadMoreClicked: !!loadMore.clicked,
            loadMoreVia: loadMore.via || null,
            targetTag: target.el ? target.el.tagName : 'window',
            targetClass: target.el && target.el.className ? String(target.el.className).slice(0, 60) : '',
            url: location.href,
          });
        }, 650);
      } catch (err) {
        sendResponse({ ok: false, error: err.message || String(err) });
      }
      return true;
    }
  });

  function scanSearchResultsPage() {
    const { containers, selectorUsed } = findPostContainers();
    const leads = [];
    const seenUrns = new Set();
    const stats = {
      containers: containers.length,
      noText: 0,
      blockedAuthor: 0,
      noContactNoHiring: 0,
      kept: 0,
      dupe: 0,
      fallbackUsed: false
    };

    for (const el of containers) {
      try {
        const { lead, reason } = extractLeadFromContainer(el);
        if (!lead) {
          if (reason === 'no_text') stats.noText++;
          else if (reason === 'blocked_author') stats.blockedAuthor++;
          else if (reason === 'no_contact_no_hiring') stats.noContactNoHiring++;
          continue;
        }
        const key = lead.urn || lead.postUrl || lead.text.slice(0, 80);
        if (seenUrns.has(key)) { stats.dupe++; continue; }
        seenUrns.add(key);
        leads.push(lead);
        stats.kept++;
      } catch (_) {
        // One bad post shouldn't abort the whole scan
      }
    }

    // Whole-page fallback — if per-post scan found nothing, pull all emails
    // and phones out of the main content area's innerText. Saves are less
    // useful (no per-post URL) but still surface contact info.
    if (!leads.length) {
      const fallbackLeads = wholePageFallback();
      if (fallbackLeads.length) {
        stats.fallbackUsed = true;
        fallbackLeads.forEach((l) => leads.push(l));
      }
    }

    // Diagnostic snapshot so we can tell WHY zero containers matched.
    let domDiag = null;
    if (!stats.containers) {
      domDiag = collectDomDiagnostic();
    }

    try {
      console.log('[Pradip AI scan]', { selectorUsed, stats, domDiag, sampleLead: leads[0] });
    } catch (_) {}

    return { leads, stats, selectorUsed, domDiag };
  }

  // When container detection fails entirely, scrape emails/phones from the
  // main content area and group them into ONE lead per distinct "post-like"
  // text region. We can't perfectly attribute each contact to its post
  // (that's why container detection is preferred), but clustering contacts
  // by their text proximity gives a much cleaner review UI than the old
  // "one lead per email" approach.
  function wholePageFallback() {
    const main = document.querySelector('main') || document.body;
    if (!main) return [];
    const text = normalizeWhitespace(main.innerText || '');
    if (!text) return [];

    // Whole-page sweep — pull ALL emails/phones from main.innerText, not
    // just the top 5 (which is the sensible cap when scanning a single
    // post). 200 is a generous ceiling that still prevents pathological
    // pages with thousands of matches from blowing up memory.
    const emails = uniqueMatches(text, EMAIL_RE, 200).filter(isPlausibleEmail);
    const phones = uniqueMatches(text, PHONE_RE, 200).map(normalizePhone).filter(Boolean);

    if (!emails.length && !phones.length) return [];

    // Build contact objects with their text positions so we can cluster
    // contacts that appear near each other into a single "lead".
    const contacts = [];
    emails.forEach((em) => {
      const idx = text.toLowerCase().indexOf(em.toLowerCase());
      if (idx >= 0) contacts.push({ type: 'email', value: em, pos: idx });
    });
    phones.forEach((ph) => {
      const idx = text.indexOf(ph);
      if (idx >= 0) contacts.push({ type: 'phone', value: ph, pos: idx });
    });
    contacts.sort((a, b) => a.pos - b.pos);

    // Cluster contacts that fall within ~600 chars of each other — that's
    // roughly a full LinkedIn post including its caption + author line.
    const CLUSTER_WINDOW = 600;
    const clusters = [];
    let cur = null;
    for (const c of contacts) {
      if (!cur || c.pos - cur.lastPos > CLUSTER_WINDOW) {
        cur = { start: c.pos, lastPos: c.pos, emails: [], phones: [] };
        clusters.push(cur);
      }
      cur.lastPos = c.pos;
      if (c.type === 'email') cur.emails.push(c.value);
      else cur.phones.push(c.value);
    }

    return clusters.map((cl) => {
      // Expand window so snippet includes context before/after the cluster
      const sStart = Math.max(0, cl.start - 120);
      const sEnd = Math.min(text.length, cl.lastPos + 200);
      const snippet = text.slice(sStart, sEnd).trim();
      return {
        urn: '',
        // Blank postUrl so URL-based sheet dedup doesn't collapse all
        // fallback leads onto the first saved row. Email dedup handles
        // true duplicates.
        postUrl: '',
        author: '(whole-page fallback)',
        text: snippet,
        snippet: snippet.slice(0, 300),
        emails: cl.emails,
        phones: cl.phones,
        hasEmail: cl.emails.length > 0,
        hasPhone: cl.phones.length > 0,
        hiringSignal: /\b(hiring|looking for|apply|dm me|reach out)\b/i.test(snippet),
        fallback: true,
      };
    });
  }

  // Reports up to 10 most-common class name fragments + a sample of URN
  // attribute values seen on the page, so we can update selectors if
  // LinkedIn changes its DOM again.
  function collectDomDiagnostic() {
    try {
      const urnNodes = Array.from(document.querySelectorAll('[data-urn], [data-id]')).slice(0, 8);
      const urnSamples = urnNodes.map((n) => ({
        tag: n.tagName,
        urn: n.getAttribute('data-urn') || n.getAttribute('data-id'),
        classes: (n.className && typeof n.className === 'string') ? n.className.slice(0, 80) : ''
      }));

      const classCounts = {};
      const all = document.querySelectorAll('main *[class]');
      const cap = Math.min(all.length, 2000); // don't burn too much CPU
      for (let i = 0; i < cap; i++) {
        const cls = all[i].className;
        if (typeof cls !== 'string') continue;
        cls.split(/\s+/).forEach((c) => {
          if (!c) return;
          // Only count class fragments related to feed / update / post / search
          if (/feed|update|post|search|result|shared|actor|commentary/i.test(c)) {
            classCounts[c] = (classCounts[c] || 0) + 1;
          }
        });
      }
      const topClasses = Object.entries(classCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10);

      return {
        urnSamples,
        topClasses,
        totalMainChildren: all.length,
      };
    } catch (err) {
      return { error: String(err) };
    }
  }

  function findPostContainers() {
    // LinkedIn search-results page DOM has shifted multiple times. Try the
    // most likely wrappers in order; first non-empty wins. Exposed as
    // selectorUsed in the response so we can tell which strategy hit.
    const attempts = [
      'div[data-urn^="urn:li:activity:"]',
      '[data-urn^="urn:li:activity:"]',
      '[data-id^="urn:li:activity:"]',
      '.feed-shared-update-v2',
      '.update-components-update-v2',
      'li.reusable-search__result-container',
      'div.search-results-container [data-urn]',
      // Newer LinkedIn layouts wrap each post in a generic div with the URN
      // on an inner element. Walk up from those.
      '[data-urn*="activity"]',
      '[data-urn*="ugcPost"]',
      '[data-urn*="share"]',
      // Last-ditch: any URN-bearing node at all (we filter per-post text
      // downstream, so over-matching here is safe).
      '[data-urn]',
    ];
    for (const sel of attempts) {
      try {
        const nodes = Array.from(document.querySelectorAll(sel));
        if (nodes.length) {
          // For the broadest selectors, walk up to the nearest "post-shaped"
          // ancestor (has both an author link and meaningful text block) so
          // we don't duplicate on nested URN nodes.
          const canonical = canonicalizePostNodes(nodes);
          if (canonical.length) return { containers: canonical, selectorUsed: sel };
        }
      } catch (_) {}
    }
    return { containers: [], selectorUsed: 'none' };
  }

  function canonicalizePostNodes(nodes) {
    const seen = new Set();
    const out = [];
    for (const n of nodes) {
      // Walk up at most 6 levels looking for a container that has both
      // an actor/author link and a reasonable amount of text.
      let el = n;
      let best = n;
      for (let i = 0; i < 6 && el; i++) {
        const hasAuthor = !!el.querySelector('a[href*="/in/"], a[href*="/company/"]');
        const textLen = (el.innerText || '').length;
        if (hasAuthor && textLen >= 60) { best = el; break; }
        el = el.parentElement;
      }
      if (!seen.has(best)) {
        seen.add(best);
        out.push(best);
      }
    }
    return out;
  }

  function extractLeadFromContainer(el) {
    // Some wrappers (like li.reusable-search__result-container) don't carry
    // the URN themselves — try to find it on a descendant.
    let urn = el.getAttribute('data-urn') || el.getAttribute('data-id') || '';
    if (!urn) {
      const inner = el.querySelector('[data-urn^="urn:li:activity:"], [data-id^="urn:li:activity:"]');
      if (inner) urn = inner.getAttribute('data-urn') || inner.getAttribute('data-id') || '';
    }

    const text = extractPostText(el);
    if (!text || text.length < 20) return { lead: null, reason: 'no_text' };

    const author = extractAuthor(el);
    if (author && AUTHOR_BLOCKLIST.has(author.toLowerCase())) {
      return { lead: null, reason: 'blocked_author' };
    }

    const postUrl = buildPostUrl(urn);

    const emails = uniqueMatches(text, EMAIL_RE).filter(isPlausibleEmail);
    const phones = uniqueMatches(text, PHONE_RE).map(normalizePhone).filter(Boolean);

    // Broader hiring-signal detection — covers the phrasing styles Jaydip
    // sees in practice (recruiter posts, founder posts, agency posts).
    const hiringSignal = /\b(hiring|we'?re hiring|we are hiring|looking for|looking to hire|seeking|recruit(ing|er)?|job opening|open (role|position|to)|apply (to|via|here)|send (your )?(cv|resume)|dm me|reach out|contact us|email (me|us|at)|drop (your )?(cv|resume)|interested candidates|candidates can apply|please share)\b/i.test(text);
    if (!emails.length && !phones.length && !hiringSignal) {
      return { lead: null, reason: 'no_contact_no_hiring' };
    }

    // Cap at 4000 chars — fits LinkedIn's ~3000 char post limit + slack,
    // well under chrome.runtime.sendMessage's practical ceiling. Gives
    // Claude enough context to write a specific, non-generic email.
    const fullText = text.slice(0, 4000);
    const truncated = detectTruncation(text);

    return { lead: {
      urn,
      postUrl,
      author: author || '',
      text: fullText,
      snippet: fullText.slice(0, 220),
      textLength: fullText.length,
      truncated,
      emails,
      phones,
      hasEmail: emails.length > 0,
      hasPhone: phones.length > 0,
      hiringSignal
    }, reason: 'ok' };
  }

  function extractPostText(el) {
    // LinkedIn swaps between a few commentary containers and truncates the
    // visible text with a "...see more" clamp. The full text is often still
    // in the DOM (just CSS-clipped), so prefer textContent over innerText
    // where it gives us MORE. Try multiple sources and pick the longest.
    const selectors = [
      '.feed-shared-inline-show-more-text',        // newer wrapper, holds full expanded text
      '.feed-shared-inline-show-more-text--minimal-padding',
      '.update-components-text',
      '.feed-shared-update-v2__commentary',
      '.update-components-update-v2__commentary',
      '.feed-shared-text',
      '.break-words',                              // newer LinkedIn commentary class
    ];

    const candidates = [];
    for (const sel of selectors) {
      const nodes = el.querySelectorAll(sel);
      for (const n of nodes) {
        // textContent returns even CSS-clipped text; innerText respects
        // visibility. Record both so we can pick the longer one.
        const tc = normalizeWhitespace(n.textContent || '');
        const it = normalizeWhitespace(n.innerText || '');
        if (tc) candidates.push(tc);
        if (it && it !== tc) candidates.push(it);
      }
    }

    // Fallback: whole container's textContent (catches anything above missed)
    const fallback = normalizeWhitespace(el.textContent || '');
    if (fallback) candidates.push(fallback);

    if (!candidates.length) return '';
    // Pick the LONGEST candidate — "see more" clamping means different
    // selectors yield different lengths, and we want the fullest text.
    candidates.sort((a, b) => b.length - a.length);
    let best = candidates[0];

    // Strip the "see more"/"show more" literal if LinkedIn inlined the
    // control text into the content. Case-insensitive, trim trailing.
    best = best.replace(/\s*…?\s*(see more|show more)\s*$/i, '').trim();

    return best;
  }

  function detectTruncation(text) {
    if (!text) return false;
    return /(…|\.\.\.)\s*see more|\s{0,3}show more\s*$|…$|\.\.\.$/i.test(text.slice(-40));
  }

  function extractAuthor(el) {
    const selectors = [
      '.update-components-actor__name span[aria-hidden="true"]',
      '.update-components-actor__title span[aria-hidden="true"]',
      '.update-components-actor__name',
      '.update-components-actor__title',
    ];
    for (const sel of selectors) {
      const node = el.querySelector(sel);
      if (node && node.innerText && node.innerText.trim()) {
        return node.innerText.trim().split('\n')[0].trim();
      }
    }
    return '';
  }

  function buildPostUrl(urn) {
    if (!urn) return '';
    // urn:li:activity:7123456789 → shareable feed URL
    return 'https://www.linkedin.com/feed/update/' + urn + '/';
  }

  function uniqueMatches(text, re, maxResults) {
    // Default cap 5 for per-post extraction (a single LinkedIn post shouldn't
    // have more than a handful of distinct emails/phones — anything higher
    // is usually noise). Whole-page fallback passes a much larger cap so we
    // surface ALL contacts on the page, not just the topmost few.
    const cap = typeof maxResults === 'number' ? maxResults : 5;
    const seen = new Set();
    const out = [];
    let m;
    const regex = new RegExp(re.source, re.flags); // fresh stateful copy
    while ((m = regex.exec(text)) !== null) {
      const v = m[0].trim();
      const key = v.toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        out.push(v);
      }
      if (out.length >= cap) break;
    }
    return out;
  }

  function isPlausibleEmail(e) {
    // Filter out obvious LinkedIn system noise and bad matches
    const lower = e.toLowerCase();
    if (lower.length > 100) return false;
    if (lower.endsWith('.png') || lower.endsWith('.jpg') || lower.endsWith('.gif')) return false;
    if (/@(sentry|linkedin|licdn|media-exp|example|test)\./.test(lower)) return false;
    return true;
  }

  function normalizePhone(raw) {
    const cleaned = String(raw || '').replace(/\s+/g, ' ').trim();
    // Must contain at least 9 digits total to count (filters out random
    // number strings like "2024-2025" or "page 123")
    const digits = cleaned.replace(/\D/g, '');
    if (digits.length < 9 || digits.length > 15) return '';
    return cleaned;
  }

  function normalizeWhitespace(s) {
    return String(s || '').replace(/\s+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
  }
})();
