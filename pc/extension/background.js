// The extension's worker: takes the reports the tabs read and passes them to the helper on
// this PC (pc/mail_host.py, started by Chrome through native messaging), which sends each one
// to the Pi with "ssh pi@raspberrypi.local piclock mail". Keeps a status per mailbox for the popup.
const HOST = "com.piclock.mail";
const SETUP = "Run on the PC: py pc\\setup_extension.py";
let port = null;
const waiting = new Map();
let nextId = 1;

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

async function labelFor(account) {
  const { labels = {} } = await chrome.storage.sync.get("labels");
  return labels[account] || "";
}

async function handleReport(report, sender) {
  if (!report || typeof report !== "object") return { ok: false, detail: "empty report" };
  if (report.needs_login) {
    // A sign-in page doesn't say which account it is for: flag the mailboxes already known for that provider.
    const all = await chrome.storage.local.get(null);
    const known = Object.values(all).filter((b) => b && b.account && b.provider === report.provider);
    if (!known.length) return { ok: true, detail: "a sign-in page, and no mailbox known yet" };
    let last = { ok: true, detail: "" };
    for (const b of known) {
      last = await ask({ cmd: "send", report: { account: b.account, provider: b.provider, needs_login: true, seq: Date.now() / 1000 } });
      await chrome.storage.local.set({ ["box:" + b.account]: { ...b, needs_login: true, read_at: Date.now(), sent_ok: !!last.ok, detail: last.detail || "" } });
    }
    return { ok: !!last.ok, detail: last.detail || "" };
  }
  const account = report.account || report.provider || "mail";
  const label = await labelFor(account);
  const out = { ...report, account, seq: Date.now() / 1000 };
  if (label) out.label = label;
  const answer = await ask({ cmd: "send", report: out });
  const ok = !!answer.ok;
  const status = {
    account, provider: report.provider || "", label, needs_login: !!report.needs_login,
    unread: report.needs_login ? null : report.unread, count: (report.messages || []).length,
    read_at: Date.now(), sent_ok: ok, detail: answer.detail || "", site: sender && sender.url ? new URL(sender.url).hostname : "",
  };
  await chrome.storage.local.set({ ["box:" + account]: status });
  return { ok, detail: answer.detail || "" };
}

async function forget(account) {
  const answer = await ask({ cmd: "send", report: { account, state: "removed", seq: Date.now() / 1000 } });
  await chrome.storage.local.remove("box:" + account);
  return answer;
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
    forget: () => forget(msg.account),
    ping: () => ask({ cmd: "ping" }),
    watch: () => watch(msg.origin),
  };
  const job = jobs[msg && msg.type];
  if (!job) return false;
  job().then(respond, (err) => respond({ ok: false, detail: String(err && err.message || err) }));
  return true;
});
