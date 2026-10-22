// The popup: one card per mailbox the tabs have read, the helper's state, and the buttons.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ago = (t) => { const m = Math.round((Date.now() - t) / 60000); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.floor(m / 60)} h ago`; };
const say = (t) => { $("#msg").textContent = t; };

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab || null;
}

async function drawBoxes() {
  const all = await chrome.storage.local.get(null);
  const boxes = Object.keys(all).filter((k) => k.startsWith("box:")).map((k) => all[k]).sort((a, b) => a.account.localeCompare(b.account));
  if (!boxes.length) {
    $("#boxes").innerHTML = `<div class="box"><div class="line">No mailbox read yet. Open your webmail (Outlook, Gmail, Yahoo, iCloud) in a tab and keep it open; it shows up here within a minute.</div></div>`;
    return;
  }
  $("#boxes").innerHTML = boxes.map((b) => `
    <div class="box" data-account="${esc(b.account)}">
      <div class="name">${esc(b.label || b.provider || b.account)} <span class="line">${esc(b.account !== b.label ? b.account : "")}</span></div>
      <div class="line">${b.needs_login ? "The page is asking for a sign-in." : `${b.unread ?? "?"} unread, ${b.count} shown, read ${ago(b.read_at)}`}</div>
      <div class="line ${b.sent_ok ? "ok" : "bad"}">${b.sent_ok ? "Sent to the Pi." : "Not sent: " + esc(b.detail)}</div>
      <div class="row"><input placeholder="Name on the clock, e.g. UIC" value="${esc(b.label || "")}" aria-label="Name on the clock"><button data-save>Save</button><button class="plain" data-forget>Forget</button></div>
    </div>`).join("");
  $("#boxes").querySelectorAll("[data-save]").forEach((btn) => btn.addEventListener("click", async () => {
    const card = btn.closest(".box"), account = card.dataset.account, label = card.querySelector("input").value.trim();
    const { labels = {} } = await chrome.storage.sync.get("labels");
    if (label) labels[account] = label; else delete labels[account];
    await chrome.storage.sync.set({ labels });
    say("Saved. The clock uses the new name on the next report.");
    readNow();
  }));
  $("#boxes").querySelectorAll("[data-forget]").forEach((btn) => btn.addEventListener("click", async () => {
    const account = btn.closest(".box").dataset.account;
    const r = await chrome.runtime.sendMessage({ type: "forget", account });
    say(r && r.ok ? "Removed from the clock. It comes back if the tab reports again." : "Couldn't tell the Pi: " + (r && r.detail));
    drawBoxes();
  }));
}

async function readNow() {
  const tab = await activeTab();
  if (!tab) return;
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: "read-now" });
    say(r && r.report ? `Read ${r.report.needs_login ? "a sign-in page" : (r.report.messages || []).length + " unread message(s)"} with the ${r.reader} reader.` : `The ${r ? r.reader : ""} reader found no inbox in this tab.`);
  } catch (err) {
    say("This tab isn't a webmail page the extension watches.");
  }
  setTimeout(drawBoxes, 1500);
}

async function copySample() {
  const tab = await activeTab();
  if (!tab) return;
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: "sample" });
    await navigator.clipboard.writeText(`PiClock mail sample\nreader: ${r.reader}\ntitle: ${r.title}\nurl: ${r.url}\n\n${r.sample}`);
    say("Copied a sample of the mail list. Paste it to whoever maintains the reader; it contains your mail text.");
  } catch (err) {
    say("Couldn't read this tab.");
  }
}

async function drawSite() {
  const tab = await activeTab();
  const url = tab && tab.url ? new URL(tab.url) : null;
  const el = $("#site");
  if (!url || url.protocol !== "https:") { el.innerHTML = ""; return; }
  const known = ["outlook.office.com", "outlook.office365.com", "outlook.live.com", "mail.google.com", "mail.yahoo.com", "www.icloud.com"].includes(url.hostname);
  const { watched = [] } = await chrome.storage.sync.get("watched");
  const isWatched = known || watched.includes(url.origin);
  el.innerHTML = isWatched
    ? `<div class="row"><button id="read">Read this tab now</button><button class="plain" id="sample">Copy a sample for troubleshooting</button></div>`
    : `<div class="row"><button id="watch">Watch ${esc(url.hostname)} too</button></div><div class="line">For webmail that isn't built in: the unread count from the tab's title.</div>`;
  if (isWatched) { $("#read").addEventListener("click", readNow); $("#sample").addEventListener("click", copySample); }
  else $("#watch").addEventListener("click", async () => {
    const granted = await chrome.permissions.request({ origins: [url.origin + "/*"] });   // asked here: Chrome wants the click
    if (!granted) { say("Chrome didn't grant access to that site."); return; }
    const r = await chrome.runtime.sendMessage({ type: "watch", origin: url.origin });
    say(r.detail);
    drawSite();
  });
}

async function drawHelper() {
  const r = await chrome.runtime.sendMessage({ type: "ping" });
  $("#helper").textContent = r && r.ok ? `Helper on this PC: ${r.detail}` : `Helper on this PC: ${r ? r.detail : "no answer"}`;
}

drawBoxes();
drawSite();
drawHelper();
