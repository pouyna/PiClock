// Readers for webmail pages: each turns the inbox a page shows into a report for the Pi.
//
// A report is {provider, account, unread, messages: [{id, from, subject, ts, when}]}, with ts
// in seconds or null when the page shows a time only as text (then "when" carries that text);
// {provider, needs_login: true} on a sign-in page; or null when this frame isn't showing an
// inbox. Only unread mail is read, only what is on the page, and nothing here changes the page.
// The same file runs in the tests (dev/test_pages.py) against saved copies of the pages.
const PiClockReaders = (() => {
  const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;
  const TIME_TEXT = /^(\d{1,2}:\d{2}|\d{1,2}\/\d{1,2}|(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Yesterday|Today)\b)/i;
  const MAX = 20;
  const tidy = (s) => (s || "").replace(/\s+/g, " ").trim();
  const text = (el) => tidy(el && el.textContent);
  const attr = (el, name) => (el && el.getAttribute(name)) || "";

  function unreadFromTitle(doc) {   // "(12) Inbox - ..." is how most webmail titles count unread mail
    const m = /\((\d{1,5})\)/.exec(doc.title || "");
    return m ? Number(m[1]) : null;
  }
  function when(el) {   // the time an element shows, with its full-date tooltip when there is one
    if (!el) return { ts: null, when: "" };
    const full = attr(el, "title") || attr(el, "aria-label") || attr(el, "datetime");
    const parsed = Date.parse(full);
    return { ts: Number.isFinite(parsed) ? Math.round(parsed / 1000) : null, when: text(el) };
  }
  function bold(el) {
    if (!el || typeof getComputedStyle !== "function") return false;
    const w = getComputedStyle(el).fontWeight;
    return w === "bold" || Number(w) >= 600;
  }
  function accountFrom(...candidates) {
    for (const c of candidates) {
      const m = EMAIL.exec(c || "");
      if (m) return m[0].toLowerCase();
    }
    return "";
  }
  function message(id, from, subject, timeEl) {
    const t = when(timeEl);
    return { id: tidy(id), from: tidy(from), subject: tidy(subject), ts: t.ts, when: t.when };
  }

  // Outlook on the web: outlook.office.com (work and school), outlook.live.com (personal).
  // Each conversation is a [role=option] row; the sender, subject and time are spans with
  // tooltips (title). Unread rows say "unread" in their accessible name or are set in bold.
  function outlook(doc, loc) {
    if (/^login\./.test(loc.hostname)) return { provider: "Outlook", needs_login: true };
    let rows = Array.from(doc.querySelectorAll('[role="option"][data-convid], [role="option"][data-item-id]'));
    if (!rows.length) rows = Array.from(doc.querySelectorAll('[role="listbox"] [role="option"]'));
    if (!rows.length) return null;
    const messages = [];
    for (const row of rows) {
      if (messages.length >= MAX) break;
      const spans = Array.from(row.querySelectorAll("span[title], span[aria-label]"));
      const senderEl = spans.find((s) => EMAIL.test(attr(s, "title")) || EMAIL.test(attr(s, "aria-label"))) || null;
      const timeEl = spans.find((s) => s !== senderEl && TIME_TEXT.test(text(s))) || row.querySelector("time") || null;
      const subjectEl = spans.find((s) => s !== senderEl && s !== timeEl && text(s).length > 0) || null;
      const label = attr(row, "aria-label");
      const unread = /\bunread\b/i.test(label) || bold(senderEl) || bold(subjectEl) || !!row.querySelector('[title="Mark as read"], [aria-label="Mark as read"]');
      if (!unread) continue;
      const from = text(senderEl) || attr(senderEl, "title") || tidy(label.split(",")[0]);
      const subject = text(subjectEl) || attr(subjectEl, "title");
      messages.push(message(attr(row, "data-convid") || attr(row, "data-item-id") || row.id, from, subject, timeEl));
    }
    const me = doc.querySelector('#O365_MainLink_Me, [id$="MainLink_Me"], button[aria-label*="Account manager"]');
    const account = accountFrom(doc.title, attr(me, "aria-label"), attr(me, "title"));
    const unread = unreadFromTitle(doc);
    return { provider: "Outlook", account: account || loc.hostname, unread: unread === null ? messages.length : unread, messages };
  }

  // Gmail: rows are tr.zA, unread ones also zE; the sender span carries name and email
  // attributes, the subject sits in .bog, the time cell's tooltip has the full date.
  function gmail(doc, loc) {
    const hash = loc.hash || "";
    if (hash && !/^#inbox(\/|$)/.test(hash)) return null;   // another label, or a search
    const rows = Array.from(doc.querySelectorAll("tr.zA"));
    if (!rows.length) return null;
    const messages = rows.filter((r) => r.classList.contains("zE")).slice(0, MAX).map((r) => {
      const s = r.querySelector("span[email]");
      const threadEl = r.querySelector("[data-thread-id]");
      const id = attr(r, "data-legacy-thread-id") || (threadEl ? attr(threadEl, "data-thread-id") : "") || r.id;
      return message(id, s ? (attr(s, "name") || text(s)) : text(r.querySelector(".yX")), text(r.querySelector(".bog")), r.querySelector("td.xW span[title]"));
    });
    const account = accountFrom(doc.title, attr(doc.querySelector('a[aria-label*="Google Account"]'), "aria-label"));
    const unread = unreadFromTitle(doc);
    return { provider: "Gmail", account: account || "gmail", unread: unread === null ? messages.length : unread, messages };
  }

  // Yahoo Mail: the list items and their parts carry data-test-id attributes.
  function yahoo(doc, loc) {
    const rows = Array.from(doc.querySelectorAll('[data-test-id="message-list-item"]'));
    if (!rows.length) return null;
    const messages = [];
    for (const r of rows) {
      if (messages.length >= MAX) break;
      const sender = r.querySelector('[data-test-id="senders"]');
      const unread = !!r.querySelector('[data-test-id="icon-unread"], [data-test-id="unread"]') || /\bunread\b/i.test(attr(r, "aria-label")) || bold(sender);
      if (!unread) continue;
      messages.push(message(attr(r, "data-test-message-id") || attr(r, "id"), text(sender), text(r.querySelector('[data-test-id="message-subject"]')),
                            r.querySelector('[data-test-id="email-date"], time')));
    }
    const account = accountFrom(doc.title);
    const unread = unreadFromTitle(doc);
    return { provider: "Yahoo", account: account || loc.hostname, unread: unread === null ? messages.length : unread, messages };
  }

  // Any other webmail: the unread count from the tab title, which is what most of them show.
  function generic(doc, loc, provider) {
    const unread = unreadFromTitle(doc);
    if (unread === null) return null;
    return { provider: provider || loc.hostname.replace(/^(www|mail|app)\./, ""), account: loc.hostname, unread, messages: [] };
  }

  const READERS = [
    { name: "Outlook", test: (h) => /(^|\.)outlook\.(office|office365|live)\.com$/.test(h) || /^login\.(microsoftonline|live)\.com$/.test(h), read: outlook },
    { name: "Gmail", test: (h) => h === "mail.google.com", read: gmail },
    { name: "Yahoo", test: (h) => /(^|\.)mail\.yahoo\.com$/.test(h), read: yahoo },
    { name: "iCloud", test: (h) => /(^|\.)icloud\.com$/.test(h), read: (d, l) => generic(d, l, "iCloud") },
  ];
  const GENERIC = { name: "Generic", test: () => true, read: (d, l) => generic(d, l) };

  function pick(hostname) { return READERS.find((r) => r.test(hostname)) || GENERIC; }
  function read(doc, loc) { return pick(loc.hostname).read(doc, loc); }
  return { pick, read, readers: READERS };
})();
if (typeof module !== "undefined") module.exports = PiClockReaders;
