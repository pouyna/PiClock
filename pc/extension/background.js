// The extension's worker. The rule is simple: a webmail tab that is open is a mailbox on the
// clock, and nothing else is. Content scripts in the tabs read the inbox and report here; this
// worker passes each report to the helper on the PC (pc/mail_host.py, started by Chrome through
// native messaging), which sends it to the Pi with "ssh pi@raspberrypi.local piclock mail".
// When a tab closes, or goes quiet, its mailbox is removed from the Pi at once.
const HOST = "com.piclock.mail";
const SETUP = "Run on the PC: py pc\\setup_extension.py";
const QUIET_MS = 3 * 60 * 1000;   // a tab that hasn't said anything for this long is gone (closed, navigated away, crashed)
let port = null;
const waiting = new Map();
let nextId = 1;

// ---------- the helper on the PC ----------
function connect() {
  if (port) return port;
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (err) {
    return null;
  }
  port.onMessage.addListener((m) => {
    const resolve = waiting.get(m.id);
    if (resolve) { waiting.delete(m.id); resolve(m); }
  });
  port.onDisconnect.addListener(() => {
    const why = (chrome.runtime.lastError && chrome.runtime.lastError.message) || "the helper closed";
    port = null;
    for (const [id, resolve] of waiting) resolve({ id, ok: false, detail: `No helper on this PC (${why}). ${SETUP}` });
    waiting.clear();
  });
  return port;
}

function ask(msg) {
  return new Promise((resolve) => {
    const p = connect();
    if (!p) { resolve({ ok: false, detail: `Couldn't start the helper on this PC. ${SETUP}` }); return; }
    const id = nextId++;
    waiting.set(id, resolve);
    try {
      p.postMessage({ id, ...msg });
    } catch (err) {
      waiting.delete(id);
      resolve({ ok: false, detail: `Couldn't talk to the helper (${err.message}). ${SETUP}` });
      return;
    }
    setTimeout(() => {
      if (waiting.has(id)) { waiting.delete(id); resolve({ id, ok: false, detail: "The helper didn't answer in 40 seconds." }); }
    }, 40000);
  });
}

// ---------- which tab holds which mailbox (session storage: empty again when Chrome restarts) ----------
async function tabs() {
  const { tabs = {} } = await chrome.storage.session.get("tabs");
  return tabs;   // tabId -> {account, provider, seen}
}
async function saveTabs(t) {
  await chrome.storage.session.set({ tabs: t });
}
async function labelFor(account) {
  const { labels = {} } = await chrome.storage.sync.get("labels");
  return labels[account] || "";
}

async function handleReport(report, sender) {
  if (!report || typeof report !== "object" || !sender || !sender.tab) return { ok: false, detail: "empty report" };
  const tabId = sender.tab.id;
  const t = await tabs();
  if (report.needs_login) {
    // A sign-in page can't say which account it is for: flag the mailboxes of that provider that are open.
    const accounts = [...new Set(Object.values(t).filter((x) => x.provider === report.provider).map((x) => x.account))];
    if (!accounts.length) return { ok: true, detail: "a sign-in page, and no mailbox open yet" };
    let last = { ok: true, detail: "" };
    for (const account of accounts) {
      last = await ask({ cmd: "send", report: { account, provider: report.provider, needs_login: true, seq: Date.now() / 1000 } });
      await setStatus(account, { needs_login: true, read_at: Date.now(), sent_ok: !!last.ok, detail: last.detail || "" });
    }
    return last;
  }
  const account = report.account || report.provider || "mail";
  const before = t[tabId];
  t[tabId] = { account, provider: report.provider || "", seen: Date.now() };
  await saveTabs(t);
  if (before && before.account !== account) await mailboxGone(before.account, t);   // the tab now shows another account
  const label = await labelFor(account);
  const out = { ...report, account, seq: Date.now() / 1000 };
  if (label) out.label = label;
  const answer = await ask({ cmd: "send", report: out });
  await setStatus(account, {
    provider: report.provider || "", label, needs_login: false, unread: report.unread, count: (report.messages || []).length,
    read_at: Date.now(), sent_ok: !!answer.ok, detail: answer.detail || "", site: sender.url ? new URL(sender.url).hostname : "",
  });
  return { ok: !!answer.ok, detail: answer.detail || "" };
}

async function setStatus(account, fields) {
  const key = "box:" + account;
  const { [key]: old = {} } = await chrome.storage.local.get(key);
  await chrome.storage.local.set({ [key]: { ...old, ...fields, account } });
}

// A tab stopped being a mailbox. If no other tab shows the same account, the Pi forgets it.
async function tabGone(tabId) {
  const t = await tabs();
  const entry = t[tabId];
  if (!entry) return;
  delete t[tabId];
  await saveTabs(t);
  await mailboxGone(entry.account, t);
}
async function mailboxGone(account, t) {
  if (Object.values(t).some((x) => x.account === account)) return;   // still open elsewhere
  await ask({ cmd: "send", report: { account, state: "removed", seq: Date.now() / 1000 } });
  await chrome.storage.local.remove("box:" + account);
}

chrome.tabs.onRemoved.addListener((tabId) => { tabGone(tabId); });

// Every minute: tabs that went quiet (navigated away, crashed, or Chrome missed the close) are gone too.
chrome.alarms.create("sweep", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "sweep") return;
  const t = await tabs();
  const now = Date.now();
  for (const [tabId, entry] of Object.entries(t)) {
    let alive = now - entry.seen < QUIET_MS;
    if (alive) {
      try { await chrome.tabs.get(Number(tabId)); } catch (err) { alive = false; }
    }
    if (!alive) await tabGone(Number(tabId));
  }
});

async function alive(sender) {
  if (!sender || !sender.tab) return { ok: false };
  const t = await tabs();
  if (t[sender.tab.id]) { t[sender.tab.id].seen = Date.now(); await saveTabs(t); }
  return { ok: true };
}

async function watch(origin) {
  // Any other webmail: the popup asked Chrome for that site (the click has to happen there);
  // here the readers are registered for it. The generic reader takes the unread count from the tab title.
  const id = "piclock-" + new URL(origin).hostname;
  const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [id] }).catch(() => []);
  if (!existing.length) {
    await chrome.scripting.registerContentScripts([{ id, matches: [origin + "/*"], js: ["readers.js", "content.js"], runAt: "document_idle", allFrames: true, persistAcrossSessions: true }]);
  }
  const { watched = [] } = await chrome.storage.sync.get("watched");
  if (!watched.includes(origin)) await chrome.storage.sync.set({ watched: [...watched, origin] });
  return { ok: true, detail: "Watching " + origin + ". Reload that tab once." };
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  const jobs = {
    report: () => handleReport(msg.report, sender),
    alive: () => alive(sender),
    ping: () => ask({ cmd: "ping" }),
    watch: () => watch(msg.origin),
    "sample-file": () => ask({ cmd: "sample", name: String(msg.name || "page"), html: String(msg.html || "") }),
  };
  const job = jobs[msg && msg.type];
  if (!job) return false;
  job().then(respond, (err) => respond({ ok: false, detail: String(err && err.message || err) }));
  return true;
});
