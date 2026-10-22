"""Screenshot the clock page with sample data at the screen sizes the Pi uses.

    python dev/screenshot.py          writes PNGs to dev/screenshots/

First time only:  pip install -r dev/requirements-dev.txt  and  playwright install chromium
Look at the images after any change to static/index.html.
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "screenshots"
SIZES = [(800, 480), (1024, 600), (1920, 1080), (720, 1280)]  # 7" touchscreen, 7" HDMI, TV/monitor, portrait


def start_preview(port, *flags):
    proc = subprocess.Popen([sys.executable, str(HERE / "mock_server.py"), "--port", str(port), *flags],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    sys.exit("The preview server didn't start. Try: python dev/mock_server.py")


def main():
    OUT.mkdir(exist_ok=True)
    written = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])

        def shoot(port, name, size, after_load=None):
            page = browser.new_page(viewport={"width": size[0], "height": size[1]})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_timeout(1200)
            if after_load:
                after_load(page)
            path = OUT / f"{name}.png"
            page.screenshot(path=str(path))
            page.close()
            written.append(path.name)
            if errors:
                print(f"  JavaScript errors in {name}: {errors}")

        def trigger_new_mail(page):
            page.evaluate("fetch('/new-mail').then(() => poll())")
            page.wait_for_timeout(1500)

        def tap(selector):   # a touch on the Pi arrives as a click
            def do(page):
                page.click(selector)
                page.wait_for_timeout(400)
            return do

        scenarios = [
            ([], lambda port: [shoot(port, f"normal_{w}x{h}", (w, h)) for w, h in SIZES]
                              + [shoot(port, "new_mail_800x480", (800, 480), trigger_new_mail),
                                 shoot(port, "tap_mail_800x480", (800, 480), tap("#mail")),
                                 shoot(port, "tap_agenda_800x480", (800, 480), tap("#agenda")),
                                 shoot(port, "tap_hide_800x480", (800, 480), tap("#hide")),
                                 shoot(port, "tap_light_800x480", (800, 480), tap("#light")),
                                 shoot(port, "tap_mailbox_800x480", (800, 480), tap(".mail-boxes button:nth-child(2)"))]),
            (["--no-claude"], lambda port: shoot(port, "no_claude_800x480", (800, 480))),
            (["--light", "red"], lambda port: [shoot(port, "settings_800x480", (800, 480), lambda page: page.goto(f"http://127.0.0.1:{port}/settings") and page.wait_for_timeout(800)),
                                               shoot(port, "settings_keyboard_800x480", (800, 480), lambda page: (page.goto(f"http://127.0.0.1:{port}/settings"), page.wait_for_timeout(800), page.click('[data-edit="weather.location_name"]'), page.wait_for_timeout(400))),
                                               shoot(port, "settings_1280x800", (1280, 800), lambda page: page.goto(f"http://127.0.0.1:{port}/settings") and page.wait_for_timeout(800))]),
            (["--24h"], lambda port: shoot(port, "24h_800x480", (800, 480))),
            (["--empty"], lambda port: shoot(port, "not_set_up_800x480", (800, 480))),
            (["--errors"], lambda port: shoot(port, "errors_800x480", (800, 480))),
        ]
        for i, (flags, run) in enumerate(scenarios):
            port = 8110 + i
            server = start_preview(port, *flags)
            try:
                run(port)
            finally:
                server.terminate()
        browser.close()
    print("Wrote:", ", ".join(written))
    print(f"Folder: {OUT}")


if __name__ == "__main__":
    main()
