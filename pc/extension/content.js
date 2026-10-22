// Runs inside each webmail tab. Reads the inbox the page shows (readers.js) and hands a
// report to background.js whenever it changes, and every ten minutes anyway so the Pi
// knows the tab is still there. Reads only; the page is never changed.
(() => {
  if (window.__piclockMail) return;
  window.__piclockMail = true;
  const reader = PiClockReaders.pick(location.hostname);
  let lastKey = "", lastSent = 0, timer = null, last = null;

  function snapshot() {
    try { return reader.read(document, location); } catch (err) { return null; }
  }
  function keyOf(r) {
    return JSON.stringify([r.account, r.unread, !!r.needs_login, (r.messages || []).map((m) => m.id)]);
  }
  async function tick(force) {
    const r = snapshot();
    if (!r) return;   // this frame isn't showing an inbox right now
    last = r;
    const k = keyOf(r), now = Date.now();
    if (!force && k === lastKey && now - lastSent < 10 * 60 * 1000) return;
    lastKey = k;
    lastSent = now;
    try {
      await chrome.runtime.sendMessage({ type: "report", report: r, reader: reader.name });
    } catch (err) {
      // The extension was reloaded or its worker is asleep; the next change tries again.
    }
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => tick(false), 2000);
  }
  // Watching the whole page would make a busy webmail page lag: only the message list is
  // observed, for rows coming and going, and everything else is caught by a check every 15 s.
  let watched = null;
  const observer = new MutationObserver(schedule);
  function watchList() {
    const row = document.querySelector(PiClockReaders.ROW_SELECTOR);
    const list = row ? (row.closest('[role="listbox"], [role="list"], [role="grid"], [role="region"]') || row.parentElement) : null;
    if (list && list !== watched) {
      observer.disconnect();
      observer.observe(list, { childList: true, subtree: true });
      watched = list;
    }
  }
  setInterval(() => { watchList(); tick(false); }, 15 * 1000);
  setTimeout(() => { watchList(); tick(true); }, 3000);

  // The popup asks for a fresh read, or for a sample of the page to send to whoever maintains the reader.
  chrome.runtime.onMessage.addListener((msg, sender, respond) => {
    // The popup's question reaches every frame of the tab; a side frame without a mail list stays quiet.
    if (msg.type === "read-now") {
      if (window !== window.top && !snapshot()) return false;
      tick(true).then(() => respond({ reader: reader.name, report: last }));
      return true;
    }
    if (msg.type === "sample") {
      const row = document.querySelector(PiClockReaders.ROW_SELECTOR);
      if (window !== window.top && !row) return false;
      respond({ reader: reader.name, title: document.title, url: location.href, frame: window === window.top ? "top" : "child frame",
                sample: row ? row.outerHTML.slice(0, 6000) : "(no mail row found in this frame)", page: row ? null : PiClockReaders.describe(document) });
      return false;
    }
    return false;
  });
})();
