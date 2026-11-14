"""Record the 1080p demo video: the stage page (app/static/video/stage.html) with the real web chat in the phone.

Steps: narration WAVs (Windows SAPI) -> drive the stage with Playwright and capture it over CDP -> speed up the waits for
AI replies 4x -> mix the narration in -> docs/demo/Kurre-demo.mp4.

Usage (server with WhatsApp disabled, fresh state):
  rm -rf data/state.json data/state
  DEMO_PERSONA=CUST04831 WA_TOKEN= DEMO_PHONE= PUBLIC_URL=http://127.0.0.1:8103 python -m uvicorn app.main:app --port 8103  # captions quote Aino
  python scripts/record_video.py --url http://127.0.0.1:8103
Options: --no-voice (captions only), --work DIR (raw recording, logs, WAVs; default out/video).
Needs: playwright with Microsoft Edge (channel="msedge"), ffmpeg on PATH, Windows (System.Speech) for the voice.
"""
import argparse
import base64
import json
import subprocess
import time
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SPEEDUP = 4.0      # waits for AI replies play this much faster
VOICE = "Microsoft Hazel Desktop"

# One step per scene. kind: card | landing | chip | quick | button | type | dashboard.
# title/sub change the left headline; quote/small show the subtitle bar; say is the narration line.
STEPS = [
    dict(kind="card", arg="title", hold=5,
         say="Kurre. A money coach, where you already chat."),
    dict(kind="card", arg="meet", hold=4.5,
         say="Meet Kurre, the S-Pankki savings squirrel."),
    dict(kind="card", arg="why", hold=5.5,
         say="One in three Finns opens S-mobiili every week. Almost everyone chats every day. Kurre lives in both."),
    dict(kind="landing", hold=7,
         title="Kurre lives where<br>you already chat.",
         sub="Web chat in S-mobiili and WhatsApp, one backend.\nDemo customer CUST04831, 24, synthetic S-Pankki data.",
         say="This is the real app, on synthetic S-Pankki data."),
    dict(kind="chip", arg="⏰ Rent reminder",
         title="A heads-up<br><span class='accent'>before</span> rent.",
         sub="Safe to spend = balance - bills - a buffer\nfor irregular costs, until payday (day 15).",
         quote="Rent 771.82 € due tomorrow · 68.60 € safe to spend until payday", small="about 7.62 € a day",
         say="Before rent, Kurre says what is safe to spend until payday."),
    dict(kind="quick", arg="Pay rent now", title="One tap to pay.",
         sub="Kurre prepares the payment.\nIt cannot send it.",
         say="One tap prepares the payment."),
    dict(kind="button", title="Your PIN moves<br>the money. <span class='accent'>Not the AI.</span>",
         sub="Confirm page with a 4-digit PIN\n(simulated PSD2 payment initiation).",
         say="The PIN confirms it. The AI never moves money."),
    dict(kind="chip", arg="What is safe for me to spend this week?", title="Plain questions,<br>real numbers.",
         sub="Claude calls tools over a deterministic, tested engine.\nNo figure that isn't in a tool result.",
         say="Plain questions. Every number comes from a tested engine, not the model."),
    dict(kind="type", arg="What kind of spender am I, compared with Finland?", title="You vs Finland.",
         sub="535 S-Pankki peers aged 20-29\n+ Statistics Finland Household Budget Survey 2022.",
         quote="Restaurants: 6.0× Finnish households aged 17-24", small="Statistics Finland 2022, table 14q4",
         say="Kurre compares you with Finnish households your age, from Statistics Finland."),
    dict(kind="chip", arg="What are my regular payments?", title="Regular payments,<br>from your data.",
         sub="Rent, utility bills and monthly investing,\nfound in the customer's own transactions.",
         say="It finds your regular payments in your own data: rent, bills, investing."),
    dict(kind="chip", arg="🧾 Bill alert", title="A bill that<br>stands out.",
         sub="Proactive message: one bill well above the usual range.",
         quote="a 75.00 € utility bill on Fri 28 Aug",
         say="And it tells you when a bill stands out."),
    dict(kind="chip", arg="How much could I save if I cut dining out by 20%?", title="What if I cut<br>dining out?",
         sub="Checked against real monthly ETF prices\n(VWCE, XEON) over the last 2 years.",
         quote="Cut dining out by 20% = 97.18 € a month", small="past check on real ETF prices",
         say="Cut dining out by twenty percent? Kurre checks it on real fund prices."),
    dict(kind="chip", arg="I want to save 1500 euros for a trip to Lapland by next summer", title="Save for<br>something real.",
         sub="Goals with deadlines and honest monthly options.",
         say="Set a goal: Lapland, by next summer."),
    dict(kind="chip", arg="💰 Salary landed", title="Salary day:<br><span class='accent'>pay yourself first.</span>",
         sub="Proactive nudge when the salary lands (day 15).",
         say="Salary day. Pay yourself first."),
    dict(kind="quick", arg="Save", title="Into the<br>savings pocket.", sub="One tap, then your PIN.",
         say="Save with one tap, and a PIN."),
    dict(kind="button", title="Into the<br>savings pocket.", sub="Confirmed with PIN, back in the chat."),
    dict(kind="type", arg="Invest 50 euros once in the balanced fund", title="Investing, explained.<br>Never pushed.",
         sub="Kurre explains risk. The customer picks the fund.\nFund names are labelled \"Demo Fund\".",
         say="Investing is explained, never pushed. You pick the fund, and confirm."),
    dict(kind="button", title="Investing, explained.<br>Never pushed.", sub="One-off investment, confirmed with PIN."),
    dict(kind="dashboard", hold=13, title="My money.",
         sub="Balance, safe to spend, three baskets, bills,\nregular payments, you vs Finland, goals, investing.",
         say="And everything in one place: My money."),
    dict(kind="card", arg="manifesto", hold=5.5, say="Kurre informs. It never advises."),
    dict(kind="card", arg="honest", hold=8,
         say="Payments are simulated. The data, Statistics Finland and the fund prices are real."),
    dict(kind="card", arg="end", hold=5, say="Kurre. Small savings, put away."),
]


# ---------- narration ----------

def make_voice(work: Path) -> list[float]:
    """One WAV per step via SAPI; returns durations (0 when a step has no line)."""
    vdir = work / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    jobs = [{"file": str(vdir / f"{i:02d}.wav"), "text": s["say"]} for i, s in enumerate(STEPS) if s.get("say")]
    (vdir / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    ps = vdir / "say.ps1"
    ps.write_text(
        "Add-Type -AssemblyName System.Speech\n"
        f"$jobs = Get-Content -Raw -Encoding UTF8 '{vdir / 'jobs.json'}' | ConvertFrom-Json\n"
        "foreach ($j in $jobs) {\n"
        "  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        f"  $s.SelectVoice('{VOICE}'); $s.Rate = 1\n"
        "  $s.SetOutputToWaveFile($j.file); $s.Speak($j.text); $s.Dispose()\n"
        "}\n", encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)], check=True)
    durs = []
    for i, s in enumerate(STEPS):
        f = vdir / f"{i:02d}.wav"
        if s.get("say") and f.exists():
            with wave.open(str(f)) as w:
                durs.append(w.getnframes() / w.getframerate())
        else:
            durs.append(0.0)
    return durs


# ---------- recording ----------

class Screencast:
    """Full-quality frames over CDP (Page.startScreencast), each with its wall-clock timestamp."""

    def __init__(self, page, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        for f in folder.glob("*.jpg"):
            f.unlink()
        self.frames = []
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "maxWidth": 1920, "maxHeight": 1080})

    def _frame(self, ev):
        f = self.folder / f"{len(self.frames):06d}.jpg"
        f.write_bytes(base64.b64decode(ev["data"]))
        self.frames.append((ev["metadata"]["timestamp"], f))
        try:
            self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:
            pass

    def stop(self):
        self.cdp.send("Page.stopScreencast")

    def to_video(self, out: Path, t0: float, t1: float) -> Path:
        """Constant 30 fps video of [t0, t1] (wall clock): each frame is held until the next one arrives."""
        fr = sorted(self.frames)
        first = max([k for k, (ts, _) in enumerate(fr) if ts <= t0] or [0])
        fr = fr[first:]
        lines = []
        for k, (ts, f) in enumerate(fr):
            a = max(ts, t0)
            b = fr[k + 1][0] if k + 1 < len(fr) else t1
            b = min(b, t1)
            if b <= a:
                continue
            lines += [f"file '{f.as_posix()}'", f"duration {b - a:.4f}"]
        lines.append(lines[-2])  # concat demuxer: repeat the last file so its duration counts
        lst = out.with_suffix(".txt")
        lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", "fps=30,scale=out_range=tv,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", str(out)],
                       check=True)
        print(f"frames: {len(fr)}  raw: {out}", flush=True)
        return out


class Clock:
    """Real time since the recording started, and the matching time in the final (sped-up) video."""

    def __init__(self):
        self.t0 = time.time()
        self.waits = []  # (start, end) in real seconds, played SPEEDUP x faster

    def now(self):
        return time.time() - self.t0

    def out(self, t=None):
        t = self.now() if t is None else t
        saved = sum(max(0.0, min(t, b) - a) for a, b in self.waits) * (1 - 1 / SPEEDUP)
        return t - saved


def run(url, work: Path, durs):
    base = url.rstrip("/")
    log = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1, color_scheme="light")
        page = ctx.new_page()
        clk = Clock()

        def sleep(sec):  # keeps Playwright's event loop running so screencast frames are received
            if sec > 0:
                page.wait_for_timeout(int(sec * 1000))

        page.goto(base + "/static/video/stage.html")
        page.wait_for_function("window.stage")
        page.evaluate("stage.show('title')")
        sleep(1.0)
        frames = Screencast(page, work / "frames")
        sleep(0.5)
        clk.t0 = time.time()
        start_mark = 0.0

        def chat_frame():
            return page.locator("#phone").element_handle().content_frame()

        def bots():
            return chat_frame().locator("#log .msg.bot").count()

        def wait_reply(before, timeout=90):
            fr = chat_frame()
            t = time.time()
            while time.time() - t < timeout:
                n = fr.locator("#log .msg.bot").count()
                if n > before and fr.locator("#log .typing").count() == 0:
                    return fr.locator("#log .msg.bot").nth(n - 1).inner_text()
                sleep(0.2)
            raise TimeoutError("no reply")

        def ai_wait(before):
            a = clk.now()
            text = wait_reply(before)
            clk.waits.append((a, clk.now()))
            return text

        def read_reply(scene_out, say_dur, text):
            """Show the newest reply from its top, then glide to the bottom (buttons), long enough to read it."""
            fr = chat_frame()
            fr.evaluate("""() => { const log = document.getElementById('log');
                const m = [...log.querySelectorAll('.msg.bot')].pop(); const me = [...log.querySelectorAll('.msg.me')].pop();
                const top = (me && me.offsetTop > m.offsetTop - 200 ? me : m).offsetTop - 12;
                log.scrollTop = Math.min(top, log.scrollHeight); }""")
            hold = min(6.0, max(3.2, len(text) / 60))
            hold = max(hold, scene_out + say_dur + 0.5 - clk.out())
            t = time.time()
            sleep(min(1.6, hold / 3))
            fr.evaluate("""ms => { const log = document.getElementById('log'); const from = log.scrollTop;
                const to = log.scrollHeight - log.clientHeight; const t0 = performance.now();
                (function step(now) { const k = Math.min(1, (now - t0) / ms); log.scrollTop = from + (to - from) * k;
                  if (k < 1) requestAnimationFrame(step); })(t0); }""", int(max(600, hold * 1000 * 0.45)))
            sleep(max(0.0, hold - (time.time() - t)))

        for i, s in enumerate(STEPS):
            kind = s["kind"]
            say = durs[i]
            scene_real = clk.now()
            scene_out = clk.out()
            entry = {"i": i, "kind": kind, "arg": s.get("arg"), "real": round(scene_real, 2), "out": round(scene_out, 2)}
            if kind == "card":
                page.evaluate("id => stage.show(id)", s["arg"])
                sleep(max(s["hold"], say + 0.8))
                log.append(entry)
                continue
            if s.get("title"):
                page.evaluate("([t, sub]) => stage.split(t, sub)", [s["title"], s.get("sub", "")])
            page.evaluate("([q, sm]) => stage.quote(q, sm)", [s.get("quote", ""), s.get("small", "")])
            if kind == "landing":
                page.evaluate("u => stage.phone(u)", base + "/")
                page.evaluate("stage.show('split')")
                chat_frame().wait_for_load_state()
                sleep(3)
                chat_frame().evaluate("window.scrollTo({top: 520, behavior: 'smooth'})")
                sleep(max(s["hold"] - 3, say + 0.8 - (clk.out() - scene_out)))
                # open the chat for the next scenes
                page.evaluate("u => stage.phone(u)", base + "/chat")
                sleep(0.3)
                chat_frame().wait_for_selector("#chips")
                sleep(1.0)
                log.append(entry)
                continue
            if kind == "dashboard":
                page.evaluate("u => stage.phone(u)", base + "/app")
                sleep(0.4)
                chat_frame().wait_for_load_state()
                sleep(1.5)
                hold = max(s["hold"], say + 1.5)
                chat_frame().evaluate("""ms => { const el = document.scrollingElement; const to = el.scrollHeight - innerHeight;
                    const t0 = performance.now(); (function step(now) { const k = Math.min(1, (now - t0) / ms);
                    el.scrollTop = to * (k < .5 ? 2*k*k : 1 - Math.pow(-2*k + 2, 2) / 2); if (k < 1) requestAnimationFrame(step); })(t0); }""",
                                      int((hold - 2.5) * 1000))
                sleep(hold - 1.5)
                log.append(entry)
                continue

            fr = chat_frame()
            sleep(0.8)
            before = fr.locator("#log .msg.bot").count()
            if kind == "chip":
                chip = fr.locator("#chips button", has_text=s["arg"]).first
                if not chip.is_visible():  # behind the "More ideas" chip
                    fr.locator("#chips [data-more]").click()
                chip.click()
                text = ai_wait(before)
            elif kind == "type":
                fr.locator("#q").click()
                fr.locator("#q").type(s["arg"], delay=28)
                sleep(0.5)
                fr.locator("#q").press("Enter")
                text = ai_wait(before)
            elif kind == "quick":
                fr.locator("#log .msg.bot button.ghost", has_text=s["arg"]).last.click()
                text = ai_wait(before)
            elif kind == "button":
                fr.locator("#log .msg.bot button.btn:not(.ghost)").last.click()
                pin = fr.frame_locator("#frame")
                pin.locator("input[name=pin]").wait_for(timeout=20000)
                sleep(1.0)
                pin.locator("input[name=pin]").type("1234", delay=180)
                sleep(0.4)
                pin.locator("button[type=submit]").click()
                pin.locator(".ok").wait_for(timeout=20000)
                sleep(0.8)
                text = wait_reply(before, timeout=30)  # confirmation arrives through the inbox
            entry["reply"] = text
            print(f"[{i:02d}] {kind} {s.get('arg') or ''}\n     {text[:220]!r}", flush=True)
            read_reply(scene_out, say, text)
            log.append(entry)

        page.evaluate("stage.quote('')")
        sleep(1.0)
        end_real = clk.now()
        frames.stop()
        ctx.close()
        browser.close()
    video = frames.to_video(work / "raw.mp4", clk.t0, clk.t0 + end_real)
    return {"video": str(video), "start": start_mark, "end": end_real, "waits": clk.waits, "steps": log}


# ---------- edit ----------

def build(rec, durs, work: Path, out: Path, voice=True):
    start, end = rec["start"], rec["end"]
    waits = [(max(a, start), min(b, end)) for a, b in rec["waits"] if b - a > 0.4]
    segs, t = [], start
    for a, b in waits:
        if a > t:
            segs.append((t, a, 1.0))
        segs.append((a, b, SPEEDUP))
        t = b
    segs.append((t, end, 1.0))

    def out_time(x):
        o = 0.0
        for a, b, sp in segs:
            if x >= b:
                o += (b - a) / sp
            elif x > a:
                o += (x - a) / sp
        return o

    total = out_time(end)
    parts, labels = [], []
    for k, (a, b, sp) in enumerate(segs):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{sp},fps=30[v{k}]")
        labels.append(f"[v{k}]")
    fil = ";".join(parts) + f";{''.join(labels)}concat=n={len(segs)}:v=1:a=0,fade=in:st=0:d=0.6,fade=out:st={total - 0.8:.2f}:d=0.8,scale=out_range=tv,format=yuv420p[v]"

    inputs = ["-i", rec["video"]]
    amap, placed = [], {}
    if voice:
        prev_end, n = 0.0, 0
        for st in rec["steps"]:
            i = st["i"]
            f = work / "voice" / f"{i:02d}.wav"
            if not STEPS[i].get("say") or not f.exists():
                continue
            at = max(out_time(st["real"]) + 0.35, prev_end + 0.25)
            prev_end = at + durs[i]
            placed[i] = at
            inputs += ["-i", str(f)]
            n += 1
            fil += f";[{n}:a]aresample=48000,adelay={int(at * 1000)}|{int(at * 1000)}[a{n}]"
        fil += ";" + "".join(f"[a{k}]" for k in range(1, n + 1)) + f"amix=inputs={n}:normalize=0:duration=longest,volume=0.9,apad,atrim=0:{total:.2f}[a]"
        amap = ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    (work / "filter.txt").write_text(fil, encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex_script", str(work / "filter.txt"),
           "-map", "[v]", *amap, "-c:v", "libx264", "-preset", "slow", "-crf", "23", "-pix_fmt", "yuv420p", "-r", "30",
           "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return total, [(round(placed.get(st["i"], out_time(st["real"])), 1), st["kind"], st.get("arg")) for st in rec["steps"]]


def write_voiceover(path: Path, marks):
    lines = ["Kurre · S-Pankki Chat demo: narration (Windows SAPI voice \"Microsoft Hazel Desktop\")", ""]
    for (t, kind, arg), s in zip(marks, STEPS):
        if s.get("say"):
            lines.append(f"[{int(t // 60)}:{t % 60:04.1f}] {s['say']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8103")
    ap.add_argument("--work", default=str(ROOT / "out" / "video"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo" / "Kurre-demo.mp4"))
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--edit-only", action="store_true", help="re-edit the last recording without recording again")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    durs = make_voice(work) if not a.no_voice else [0.0] * len(STEPS)
    if a.edit_only:
        rec = json.loads((work / "recording.json").read_text(encoding="utf-8"))
    else:
        rec = run(a.url, work, durs)
        (work / "recording.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    total, marks = build(rec, durs, work, Path(a.out), voice=not a.no_voice)
    if not a.no_voice:
        write_voiceover(Path(a.out).parent / "voiceover.txt", marks)
    print(f"video: {a.out}  {total:.1f}s")
    for m in marks:
        print("  ", m)
