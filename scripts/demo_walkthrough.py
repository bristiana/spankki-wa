"""Drive the web chat through every flow in a phone-sized browser. Doubles as an end-to-end test and as the
recorder for the demo video.

Usage:
  python scripts/demo_walkthrough.py --url http://localhost:8000            # test only
  python scripts/demo_walkthrough.py --url ... --record out/video_raw       # also record a .webm per run
Needs: pip install playwright; uses the installed Microsoft Edge (channel="msedge").
"""
import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# (caption shown on screen, action, argument)
SCENES = [
    ("A rent reminder arrives before payday", "chip", "⏰ Rent reminder"),
    ("One tap: pay rent", "quick", "Pay rent now"),
    ("Confirm with your bank PIN", "button", None),
    ("Plain questions, real numbers", "chip", "What is safe for me to spend this week?"),
    ("Where does my money go?", "chip", "What kind of spender am I?"),
    ("Regular payments, from the real data", "chip", "What are my regular payments?"),
    ("A bill alert: a utility bill above the usual range", "chip", "🧾 Bill alert"),
    ("No subscriptions to stop, a bill to watch", "quick", "OK, thanks"),
    ("What if I cut dining out?", "chip", "How much could I save if I cut dining out by 20%?"),
    ("Set a savings goal", "chip", "I want to save 1500 euros for a trip to Lapland by next summer"),
    ("Salary lands: pay yourself first", "chip", "💰 Salary landed"),
    ("Save to the goal", "quick", "Save"),
    ("Confirm with PIN", "button", None),
    ("Investing, explained not pushed", "type", "Invest 50 euros once in the balanced fund"),
    ("Confirm the investment", "button", None),
]


def caption(page, text):
    page.evaluate("""t => {
      let c = document.getElementById('cap');
      if (!c) { c = document.createElement('div'); c.id = 'cap';
        c.style.cssText = 'position:fixed;left:12px;right:12px;top:64px;z-index:20;background:rgba(0,40,25,.88);color:#fff;'
          + 'font:600 15px system-ui;padding:10px 14px;border-radius:12px;text-align:center;transition:opacity .3s';
        document.body.appendChild(c); }
      c.textContent = t; c.style.opacity = 1;
      clearTimeout(window._capT); window._capT = setTimeout(() => c.style.opacity = 0, 3500);
    }""", text)


def wait_reply(page, before, timeout=90):
    """Wait until a new bot message appears and typing is gone."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = page.locator("#log .msg.bot").count()
        if n > before and page.locator("#log .typing").count() == 0:
            return page.locator("#log .msg.bot").nth(n - 1).inner_text()
        time.sleep(0.4)
    raise TimeoutError("no reply")


def run(url, record_dir=None, slow=1.0):
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        ctx_args = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        if record_dir:
            Path(record_dir).mkdir(parents=True, exist_ok=True)
            ctx_args["record_video_dir"] = record_dir
            ctx_args["record_video_size"] = {"width": 390, "height": 844}
        ctx = browser.new_context(**ctx_args)
        page = ctx.new_page()
        page.goto(url.rstrip("/") + "/chat")
        page.wait_for_selector("#chips")
        time.sleep(1.5 * slow)
        for cap, action, arg in SCENES:
            caption(page, cap)
            time.sleep(1.2 * slow)
            before = page.locator("#log .msg.bot").count()
            t0 = time.time()
            if action == "chip":
                chip = page.locator("#chips button", has_text=arg).first
                if not chip.is_visible():  # behind the "More ideas" chip
                    page.locator("#chips [data-more]").click()
                chip.click()
                text = wait_reply(page, before)
            elif action == "type":
                page.fill("#q", arg)
                time.sleep(0.6 * slow)
                page.press("#q", "Enter")
                text = wait_reply(page, before)
            elif action == "quick":
                page.locator("#log .msg.bot button.ghost", has_text=arg).last.click()
                text = wait_reply(page, before)
            elif action == "button":
                page.locator("#log .msg.bot button.btn:not(.ghost)").last.click()
                fr = page.frame_locator("#frame")
                fr.locator("input[name=pin]").wait_for(timeout=20000)
                time.sleep(0.8 * slow)
                fr.locator("input[name=pin]").type("1234", delay=180)
                time.sleep(0.5 * slow)
                fr.locator("button[type=submit]").click()
                fr.locator(".ok").wait_for(timeout=20000)
                text = wait_reply(page, before, timeout=30)  # confirmation arrives through the inbox
            results.append({"scene": cap, "seconds": round(time.time() - t0, 1), "reply": text[:400]})
            print(f"[{results[-1]['seconds']:>5}s] {cap}\n    {text[:160]!r}", flush=True)
            time.sleep(2.5 * slow)
        time.sleep(2 * slow)
        video = page.video.path() if record_dir else None
        ctx.close()
        browser.close()
    return results, video


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--record")
    ap.add_argument("--slow", type=float, default=1.0)
    a = ap.parse_args()
    res, video = run(a.url, a.record, a.slow)
    out = Path(a.record or ".") / "walkthrough.json"
    out.write_text(json.dumps({"video": video, "scenes": res}, indent=2, ensure_ascii=False), encoding="utf-8")
    print("video:", video)
