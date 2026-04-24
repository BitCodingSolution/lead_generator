// ============================================================
// Pradip AI — feed composer paste
// Runs on: linkedin.com/feed/*
// Responsibilities:
//   - On PASTE_POST_COMPOSER request from the side panel, open the
//     "Start a post" modal and paste the provided text into its editor.
//   - Does NOT submit the post. Jaydip reviews + clicks "Post" himself —
//     this keeps us on the right side of LinkedIn's TOS (human-in-loop).
// ============================================================

(function () {
  if (window.__pradipAiFeedComposeLoaded) return;
  window.__pradipAiFeedComposeLoaded = true;

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || msg.type !== "PASTE_POST_COMPOSER") return;
    (async () => {
      try {
        await pasteIntoComposer(String(msg.text || ""));
        sendResponse({ ok: true });
      } catch (err) {
        sendResponse({ ok: false, error: err.message || String(err) });
      }
    })();
    return true;
  });

  async function pasteIntoComposer(text) {
    if (!text.trim()) throw new Error("Empty post text");

    // If the composer modal is already open, skip the open step
    let editor = findComposerEditor();
    if (!editor) {
      openComposer();
      editor = await waitFor(findComposerEditor, 5000);
      if (!editor) {
        throw new Error(
          "Could not open the 'Start a post' composer. Click 'Start a post' manually, then try again."
        );
      }
    }

    editor.focus();

    // LinkedIn uses a contentEditable with Quill/Draft-like behavior.
    // insertText via execCommand keeps undo-stack intact and mirrors what
    // a real paste would do. Fallback manually builds paragraph elements.
    const inserted = document.execCommand("insertText", false, text);
    if (!inserted) {
      editor.innerHTML = "";
      text.split("\n").forEach((line) => {
        const p = document.createElement("p");
        p.textContent = line || "\u00A0";
        editor.appendChild(p);
      });
    }

    // Strip the placeholder class if it's still on the node
    if (editor.classList) {
      editor.classList.remove("ql-blank");
    }

    // Fire input events so LinkedIn's React/Ember state picks up the change
    // (otherwise the "Post" button stays disabled).
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    editor.dispatchEvent(new InputEvent("input", { bubbles: true }));
  }

  // Try to find the composer editor. LinkedIn's DOM shifts periodically so
  // we probe a few selectors in order of specificity.
  function findComposerEditor() {
    const selectors = [
      "div.ql-editor[contenteditable='true']",
      ".share-box__editor div[contenteditable='true']",
      ".share-creation-state__text-editor div[contenteditable='true']",
      "[role='dialog'] div.ql-editor",
      "[role='dialog'] div[contenteditable='true'][role='textbox']",
      "[aria-label*='text editor' i][contenteditable='true']",
      "div[data-placeholder*='What do you want to talk about' i]",
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  // Click the "Start a post" entry-point button. Try a few selectors because
  // LinkedIn rotates copy occasionally (Start a post / Create a post).
  function openComposer() {
    const candidates = [
      "button.share-box-feed-entry__trigger",
      "button[aria-label*='Start a post' i]",
      "button[aria-label*='Create a post' i]",
      "button[aria-label*='share a post' i]",
      "[class*='share-box-feed-entry'] button",
    ];
    for (const sel of candidates) {
      const btn = document.querySelector(sel);
      if (btn) {
        btn.click();
        return true;
      }
    }
    // Text-match fallback — walk buttons looking for the label text.
    const btns = document.querySelectorAll("button");
    for (const b of btns) {
      const t = (b.innerText || "").trim().toLowerCase();
      if (t === "start a post" || t === "create a post") {
        b.click();
        return true;
      }
    }
    return false;
  }

  function waitFor(fn, maxMs) {
    return new Promise((resolve) => {
      const start = Date.now();
      const poll = () => {
        const v = fn();
        if (v) return resolve(v);
        if (Date.now() - start > maxMs) return resolve(null);
        setTimeout(poll, 200);
      };
      poll();
    });
  }
})();
