"""Browser tests for the pages, in Playwright's Chromium like dev/screenshot.py. Run with:  pytest dev
Skipped when Playwright or its Chromium isn't installed."""
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def preview():
    """The mock server on a free port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, str(HERE / "mock_server.py"), "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("the preview server didn't start; try: python dev/mock_server.py")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--no-proxy-server"])
        except Exception as exc:   # the browser is a separate download
            pytest.skip(f"Playwright's Chromium isn't installed ({exc.__class__.__name__}); run: playwright install chromium")
        yield b
        b.close()


def test_settings_scrolls_under_a_mouse_drag(preview, browser):
    """The Pi's desktop turns touches into mouse events, so a finger drag arrives as a mouse drag:
    it must scroll the list, and must not press the control it started on."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(f"{preview}/settings")
    page.wait_for_selector("#main .toggle")
    assert page.evaluate("(() => { const m = document.querySelector('#main'); return m.scrollHeight > m.clientHeight; })()")
    toggle = page.locator("#main .toggle").first
    before = toggle.get_attribute("aria-checked")
    box = toggle.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 300, steps=12)
    page.mouse.up()
    page.wait_for_timeout(600)   # let the glide settle
    assert page.evaluate("document.querySelector('#main').scrollTop") > 150
    assert page.locator("#main .toggle").first.get_attribute("aria-checked") == before
    assert page.evaluate("dirty") is False
    # a plain tap still presses the control
    page.locator("#main .toggle").first.click()
    assert page.locator("#main .toggle").first.get_attribute("aria-checked") != before
    assert page.evaluate("dirty") is True
    page.close()


def test_mailbox_names_filter_the_mail_panel(preview, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(f"{preview}/")
    page.wait_for_selector(".mail-boxes button")
    assert page.locator(".mail-count").inner_text() == "8"
    page.locator(".mail-boxes button", has_text="Work").click()
    page.wait_for_function("document.querySelector('.mail-count').textContent === '2'")
    assert page.locator(".mail-list li").count() == 2                                   # only Work's two messages
    assert page.locator(".mail-boxes button.on").inner_text().startswith("Work")
    assert page.locator(".mail-boxes button.all").inner_text() == "All"
    page.locator(".mail-boxes button", has_text="Personal").click()                   # add a second mailbox
    page.wait_for_function("document.querySelector('.mail-count').textContent === '7'")
    assert page.locator(".mail-boxes button.on").count() == 2
    page.locator(".mail-boxes button", has_text="uic.edu").click()                    # all three chosen: still a choice, All still offered
    page.wait_for_function("document.querySelectorAll('.mail-boxes button.on').length === 3")
    assert page.locator(".mail-count").inner_text() == "8" and page.locator(".mail-boxes button.all").count() == 1
    page.goto(f"{preview}/")                                                         # the choice survives a reload
    page.wait_for_function("document.querySelectorAll('.mail-boxes button.on').length === 3")
    page.locator(".mail-boxes button.all").click()
    page.wait_for_function("document.querySelectorAll('.mail-boxes button.on').length === 0")
    assert page.locator(".mail-boxes button.all").count() == 0 and page.locator(".mail-count").inner_text() == "8"
    page.close()


def test_settings_leaves_real_touch_to_the_browser(preview, browser):
    """With mouse emulation off, the browser scrolls touch itself; the page must not scroll it twice."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(f"{preview}/settings")
    page.wait_for_selector("#main .toggle")
    moved = page.evaluate("""() => {
      const m = document.querySelector('#main');
      const ev = (type, y) => m.dispatchEvent(new PointerEvent(type, {pointerId: 7, pointerType: 'touch', clientX: 400, clientY: y, bubbles: true, isPrimary: true}));
      ev('pointerdown', 500); ev('pointermove', 400); ev('pointermove', 300); ev('pointerup', 300);
      return m.scrollTop;
    }""")
    assert moved == 0
    page.close()


# ---------- the browser extension's readers, against saved shapes of the webmail pages ----------
MOCK = HERE / "mock_mail"
READERS_JS = HERE.parent / "pc" / "extension" / "readers.js"


def read_mock(browser, html_name, expression):
    page = browser.new_page()
    page.set_content((MOCK / html_name).read_text(encoding="utf-8"))
    page.add_script_tag(path=str(READERS_JS))
    result = page.evaluate(expression)
    page.close()
    return result


def test_outlook_reader_takes_the_unread_rows(browser):
    r = read_mock(browser, "outlook.html", "PiClockReaders.pick('outlook.office.com').read(document, {hostname: 'outlook.office.com', hash: ''})")
    assert r["provider"] == "Outlook" and r["account"] == "outlook.office.com" and r["unread"] == 2
    assert [m["id"] for m in r["messages"]] == ["AAQkADc1", "AAQkADc3"]     # unread by label, unread by bold; the read rows skipped, even the bold one that offers "Mark as unread"
    first = r["messages"][0]
    assert first["from"] == "Priya Raman" and first["subject"] == "Re: Saturday hike, still on?"
    assert first["when"] == "9:12 AM" and isinstance(first["ts"], int)       # the tooltip's full date
    assert r["messages"][1]["when"] == "Mon 10/19"


def test_outlook_reader_on_the_2026_row_structure(browser):
    """Icon glyphs, action buttons, the avatar and the preview are not the subject; a read row is skipped."""
    r = read_mock(browser, "outlook_2026.html", "PiClockReaders.pick('outlook.cloud.microsoft').read(document, {hostname: 'outlook.cloud.microsoft', hash: ''})")
    assert r["provider"] == "Outlook" and r["unread"] == 1
    assert [(m["id"], m["from"], m["subject"], m["when"]) for m in r["messages"]] == [("AAQkADd0", "Priya Raman", "Re: Saturday hike, still on?", "9:12 AM")]
    assert isinstance(r["messages"][0]["ts"], int)


def test_outlook_reader_copes_without_tooltips_or_ids(browser):
    r = read_mock(browser, "outlook_plain.html", "PiClockReaders.pick('outlook.office.com').read(document, {hostname: 'outlook.office.com', hash: ''})")
    assert [(m["from"], m["subject"], m["when"]) for m in r["messages"]] == [("Priya Raman", "Re: Saturday hike, still on?", "9:12 AM"), ("Facilities", "Parking garage closed Monday", "Mon 10/19")]
    assert [m["id"] for m in r["messages"]] == ["0", "2"] and r["unread"] == 2
    sample = read_mock(browser, "outlook_plain.html", "document.querySelector(PiClockReaders.ROW_SELECTOR).getAttribute('data-list-index')")
    assert sample == "0"
    d = read_mock(browser, "gmail.html", "PiClockReaders.describe(document)")
    assert d["roles"] == {} and d["frames"] == [] and d["list"] == ""


def test_gmail_reader_uses_the_title_count_and_thread_ids(browser):
    r = read_mock(browser, "gmail.html", "PiClockReaders.pick('mail.google.com').read(document, {hostname: 'mail.google.com', hash: '#inbox'})")
    assert r["provider"] == "Gmail" and r["account"] == "pouyan@gmail.com" and r["unread"] == 2
    assert [(m["id"], m["from"], m["subject"]) for m in r["messages"]] == [("18f1", "Mom", "Call me when you get a minute"), ("18ef", "Ravi", "Photos from the lake")]
    assert isinstance(r["messages"][0]["ts"], int)
    other = read_mock(browser, "gmail.html", "PiClockReaders.pick('mail.google.com').read(document, {hostname: 'mail.google.com', hash: '#label/Receipts'})")
    assert other is None                                                      # another label isn't the inbox


def test_generic_reader_and_sign_in_pages(browser):
    page = browser.new_page()
    page.set_content("<title>(7) Inbox - Proton Mail</title><p>hello</p>")
    page.add_script_tag(path=str(READERS_JS))
    r = page.evaluate("PiClockReaders.pick('mail.proton.me').read(document, {hostname: 'mail.proton.me', hash: ''})")
    assert r == {"provider": "proton.me", "account": "mail.proton.me", "unread": 7, "messages": []}
    assert page.evaluate("PiClockReaders.pick('outlook.live.com').name") == "Outlook"
    assert page.evaluate("PiClockReaders.pick('login.microsoftonline.com').read(document, {hostname: 'login.microsoftonline.com', hash: ''})") == {"provider": "Outlook", "needs_login": True}
    assert page.evaluate("PiClockReaders.pick('outlook.office.com').read(document, {hostname: 'outlook.office.com', hash: ''})") is None   # no list on this page
    page.close()
