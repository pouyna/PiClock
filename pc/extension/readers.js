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
  const INITIALS = /^[A-Z]{1,3}$/;   // an avatar's initials, not a line of mail
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
  function leaves(row) {   // the row's visible text, one entry per text node, with the element that holds it
    const out = [];
    const walker = row.ownerDocument.createTreeWalker(row, 4 /* NodeFilter.SHOW_TEXT */);
    let node;
    while ((node = walker.nextNode())) {
      const t = tidy(node.nodeValue);
      const el = node.parentElement;
      if (!t || !/[\p{L}\p{N}]/u.test(t)) continue;   // Outlook's icons are font glyphs: text without a letter or digit
      if (el && el.closest('[aria-hidden="true"], button, input, label, [role="checkbox"], [role="img"]')) continue;   // icons, actions, the avatar
      out.push({ text: t, el });
    }
    return out;
  }

  // Outlook on the web: outlook.office.com (work and school, such as UIC), outlook.live.com
  // (personal), outlook.cloud.microsoft (the newer address). Each conversation is a row with
  // role=option (older builds carry data-convid). The sender, subject and time are usually
  // spans with tooltips; when they aren't, the row's text lines are taken in order. Unread
  // rows say "unread" in their accessible name, or are set in bold.
  const OUTLOOK_ROWS = ['[role="option"][data-convid]', '[data-convid]', '[role="listbox"] [role="option"]', '[role="option"][aria-label]'];
  function outlookRows(doc) {
    for (const s of OUTLOOK_ROWS) {
      const rows = Array.from(doc.querySelectorAll(s));
      if (rows.length) return rows;
    }
    // A list named "Message list" (or similar) whatever role it uses, and whatever its rows are called.
    const lists = Array.from(doc.querySelectorAll("[aria-label]")).filter((el) => /message list|messages|inbox/i.test(attr(el, "aria-label")));
    for (const list of lists) {
      const rows = Array.from(list.querySelectorAll('[role="option"], [role="row"], [role="listitem"], [role="treeitem"], [role="gridcell"]'));
      if (rows.length) return rows;
    }
    return [];
  }
  function outlook(doc, loc) {
    if (/^login\./.test(loc.hostname)) return { provider: "Outlook", needs_login: true };
    const rows = outlookRows(doc);
    if (!rows.length) return null;
    const parsed = [];
    for (const row of rows.slice(0, 60)) {
      // The sender is the span whose tooltip holds an address. Everything else comes from the row's
      // text lines in page order: sender, subject, preview, time. The avatar's initials ("TG") and
      // the words "Unread"/"Read" are not lines of mail.
      const spans = Array.from(row.querySelectorAll("span[title], span[aria-label]"));
      const senderEl = spans.find((sp) => EMAIL.test(attr(sp, "title")) || EMAIL.test(attr(sp, "aria-label"))) || null;
      const lines = leaves(row).filter((l) => !/^(unread|read)$/i.test(l.text) && !INITIALS.test(l.text)
        && !l.el.closest('[class*="ersona"], [class*="vatar"], [class*="Avatar"]'));
      const timeLine = lines.slice().reverse().find((l) => TIME_TEXT.test(l.text)) || null;
      let from = text(senderEl) || attr(senderEl, "title");
      const rest = lines.filter((l) => l !== timeLine && !(senderEl && senderEl.contains(l.el)) && (!from || l.text !== from));
      if (!from && rest.length) from = rest.shift().text;
      const subjectLine = rest[0] || null;
      const subject = subjectLine ? subjectLine.text : "";
      const subjectEl = subjectLine ? subjectLine.el : null;
      const timeEl = timeLine ? (timeLine.el.closest("[title], time") || timeLine.el) : null;
      const label = attr(row, "aria-label");
      if (!from) from = tidy(label.split(",")[0]);
      // The cues for unread. "Mark as unread" is the action a READ row offers, so it must not count.
      const saysUnread = /\bunread\b/i.test(label) || !!row.querySelector('[aria-label="Unread" i], [title="Unread" i]');
      const offersMarkRead = !!row.querySelector('[title="Mark as read" i], [aria-label="Mark as read" i]');
      const offersMarkUnread = !!row.querySelector('[title="Mark as unread" i], [aria-label="Mark as unread" i]');
      parsed.push({ row, from, subject, timeEl, saysUnread, offersMarkRead, offersMarkUnread, bold: bold(senderEl) || bold(subjectEl) });
    }
    // Bold only means unread when the list also has rows that aren't bold.
    const boldTells = parsed.some((r) => r.bold) && parsed.some((r) => !r.bold);
    const messages = [];
    for (const r of parsed) {
      if (messages.length >= MAX) break;
      const unread = !r.offersMarkUnread && (r.saysUnread || r.offersMarkRead || (boldTells && r.bold));
      if (!unread) continue;
      messages.push(message(attr(r.row, "data-convid") || attr(r.row, "data-item-id") || r.row.id || attr(r.row, "data-list-index"), r.from, r.subject, r.timeEl));
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

  // What a page looks like, for the popup's troubleshooting sample when a reader finds nothing.
  function describe(doc) {
    const roles = {};
    doc.querySelectorAll("[role]").forEach((el) => { const r = attr(el, "role"); roles[r] = (roles[r] || 0) + 1; });
    const labelled = Array.from(doc.querySelectorAll("[aria-label]"))
      .filter((el) => /message|inbox|list|mail/i.test(attr(el, "aria-label"))).slice(0, 20)
      .map((el) => `${el.tagName.toLowerCase()} role=${attr(el, "role") || "-"} aria-label="${attr(el, "aria-label").slice(0, 70)}" children=${el.children.length}`);
    const frames = Array.from(doc.querySelectorAll("iframe")).map((f) => (f.src || "").slice(0, 100));
    const list = Array.from(doc.querySelectorAll("[aria-label]")).find((el) => /message list/i.test(attr(el, "aria-label")));
    return { roles, labelled, frames, list: list ? list.outerHTML.slice(0, 5000) : "" };
  }

  const READERS = [
    { name: "Outlook", test: (h) => /(^|\.)outlook\.(office|office365|live)\.com$/.test(h) || /^outlook\.cloud\.microsoft$/.test(h) || /^login\.(microsoftonline|live)\.com$/.test(h), read: outlook },
    { name: "Gmail", test: (h) => h === "mail.google.com", read: gmail },
    { name: "Yahoo", test: (h) => /(^|\.)mail\.yahoo\.com$/.test(h), read: yahoo },
    { name: "iCloud", test: (h) => /(^|\.)icloud\.com$/.test(h), read: (d, l) => generic(d, l, "iCloud") },
  ];
  const GENERIC = { name: "Generic", test: () => true, read: (d, l) => generic(d, l) };
  const ROW_SELECTOR = OUTLOOK_ROWS.join(", ") + ', tr.zA, [data-test-id="message-list-item"]';

  function pick(hostname) { return READERS.find((r) => r.test(hostname)) || GENERIC; }
  function read(doc, loc) { return pick(loc.hostname).read(doc, loc); }
  return { pick, read, readers: READERS, describe, ROW_SELECTOR };
})();
if (typeof module !== "undefined") module.exports = PiClockReaders;
