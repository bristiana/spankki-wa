"""Generate Birky's sprite sheet from one parameterised drawing.

Writes into app/static/birky/:
  sprite.svg    6 x 6 grid of 240px frames (one mood per row), used by birky.css
  sprite.json   frame atlas (row per mood, frame size, fps)
  <mood>.svg    one still per mood, for templates, slides and the video
  sprite.png    raster of the sheet (2x), only with --png (needs Playwright)

Run: python scripts/make_birky_sprites.py [--png]
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "birky"
CELL = 240
COLS = 6

DEFS = """<defs>
  <radialGradient id="fur" cx="45%" cy="35%" r="70%"><stop offset="0" stop-color="#F6A55B"/><stop offset="0.6" stop-color="#E07A2E"/><stop offset="1" stop-color="#B85A1C"/></radialGradient>
  <radialGradient id="tail" cx="40%" cy="30%" r="75%"><stop offset="0" stop-color="#F7B06A"/><stop offset="0.55" stop-color="#E27F33"/><stop offset="1" stop-color="#A84F17"/></radialGradient>
  <radialGradient id="belly" cx="50%" cy="35%" r="65%"><stop offset="0" stop-color="#FFF6E8"/><stop offset="1" stop-color="#F3D9B8"/></radialGradient>
  <radialGradient id="coin" cx="40%" cy="35%" r="70%"><stop offset="0" stop-color="#FFE38A"/><stop offset="0.7" stop-color="#F2B630"/><stop offset="1" stop-color="#C98A12"/></radialGradient>
  <linearGradient id="scarf" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1F9A63"/><stop offset="1" stop-color="#00663D"/></linearGradient>
</defs>"""

INK = "#1B1B1F"
BROWN = "#5A2C12"


@dataclass
class Pose:
    dy: float = 0          # whole-body hop (negative = up)
    dx: float = 0          # shiver
    tail: float = 0        # tail sway, degrees
    tilt: float = 0        # head tilt, degrees
    eyes: str = "open"     # open | half | closed | happy | up
    mouth: str = "smile"   # smile | open | wide | frown | o | flat
    brows: str = "normal"  # normal | raised | worried
    wave: float | None = None  # left-arm angle when waving
    coin_dy: float = 0     # coin toss
    extras: list[str] = field(default_factory=list)


def eyes(kind: str) -> str:
    if kind == "open":
        return (f'<ellipse cx="90" cy="84" rx="10" ry="12" fill="{INK}"/><ellipse cx="130" cy="84" rx="10" ry="12" fill="{INK}"/>'
                '<circle cx="94" cy="79" r="4" fill="#fff"/><circle cx="134" cy="79" r="4" fill="#fff"/>'
                '<circle cx="87" cy="89" r="1.8" fill="#fff" opacity=".8"/><circle cx="127" cy="89" r="1.8" fill="#fff" opacity=".8"/>')
    if kind == "up":
        return (f'<ellipse cx="90" cy="84" rx="10" ry="12" fill="{INK}"/><ellipse cx="130" cy="84" rx="10" ry="12" fill="{INK}"/>'
                '<circle cx="92" cy="76" r="4.5" fill="#fff"/><circle cx="132" cy="76" r="4.5" fill="#fff"/>')
    if kind == "half":
        return (f'<ellipse cx="90" cy="88" rx="10" ry="6" fill="{INK}"/><ellipse cx="130" cy="88" rx="10" ry="6" fill="{INK}"/>'
                '<circle cx="94" cy="86" r="2.5" fill="#fff"/><circle cx="134" cy="86" r="2.5" fill="#fff"/>'
                '<path d="M79 86h22M119 86h22" stroke="#8E4513" stroke-width="2.5" stroke-linecap="round"/>')
    if kind == "closed":
        return f'<path d="M80 86q10 7 20 0M120 86q10 7 20 0" stroke="{INK}" stroke-width="4" fill="none" stroke-linecap="round"/>'
    if kind == "happy":
        return f'<path d="M80 90q10-13 20 0M120 90q10-13 20 0" stroke="{INK}" stroke-width="4.5" fill="none" stroke-linecap="round"/>'
    raise ValueError(kind)


def brows(kind: str) -> str:
    d = {"normal": "M80 68q10-6 19-1M121 67q9-5 19 1",
         "raised": "M80 62q10-8 19-3M121 59q9-5 19 3",
         "worried": "M80 66q10-1 18-7M122 59q8 6 18 7"}[kind]
    return f'<path d="{d}" stroke="#8E4513" stroke-width="3" fill="none" stroke-linecap="round"/>'


def mouth(kind: str) -> str:
    nose = f'<path d="M104 98q6-5 12 0l-6 6z" fill="{BROWN}"/>'
    s = f'stroke="{BROWN}" stroke-width="3" fill="none" stroke-linecap="round"'
    teeth = '<rect x="105.5" y="{y}" width="9" height="7" rx="2" fill="#fff" stroke="#E2D2C0" stroke-width="1"/>'
    if kind == "smile":
        return nose + f'<path d="M110 104v5M99 110q11 9 22 0" {s}/>' + teeth.format(y=110)
    if kind == "open":
        return nose + f'<path d="M110 104v4" {s}/><ellipse cx="110" cy="114" rx="8" ry="6" fill="{BROWN}"/>' + teeth.format(y=108)
    if kind == "wide":
        return (nose + f'<path d="M110 104v3" {s}/><path d="M98 108q12 0 24 0q-2 16-12 16t-12-16z" fill="{BROWN}"/>'
                '<ellipse cx="110" cy="119" rx="6" ry="3.5" fill="#E86A6A"/>' + teeth.format(y=108))
    if kind == "frown":
        return nose + f'<path d="M110 104v4M100 116q10-8 20 0" {s}/>'
    if kind == "o":
        return nose + f'<path d="M110 104v4" {s}/><ellipse cx="110" cy="114" rx="4" ry="4.5" fill="{BROWN}"/>'
    if kind == "flat":
        return nose + f'<path d="M110 104v5M102 112h16" {s}/>'
    raise ValueError(kind)


def extras(names: list[str]) -> str:
    out = []
    for n in names:
        if n.startswith("spark"):  # spark1..3: more sparkles
            pts = [(40, 60, 1), (190, 40, .8), (200, 130, .7)][: int(n[-1])]
            for x, y, k in pts:
                out.append(f'<path transform="translate({x} {y}) scale({k})" d="M0-14L4-4 14 0 4 4 0 14-4 4-14 0-4-4z" fill="#FFD447" stroke="#E2A40E" stroke-width="1.5"/>')
        elif n.startswith("dots"):  # dots1..3: thinking bubble fills in
            for i, (x, y, r) in enumerate([(166, 54, 5), (182, 36, 7), (204, 16, 10)][: int(n[-1])]):
                out.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#fff" stroke="#9AA8A1" stroke-width="2"/>')
        elif n.startswith("sweat"):  # sweat0..2: drop slides down
            y = 52 + 6 * int(n[-1])
            out.append(f'<path transform="translate(160 {y})" d="M0-10q8 10 6 15a6 6 0 0 1-12 0q-2-5 6-15z" fill="#8FD3FF" stroke="#4BA3D9" stroke-width="1.5"/>')
        elif n == "lines":  # motion lines next to the waving paw
            out.append('<path d="M30 92l-10-5M26 108h-12M30 124l-10 5" stroke="#E2A40E" stroke-width="3" stroke-linecap="round"/>')
    return "".join(out)


def squirrel(p: Pose) -> str:
    """One 240x240 frame. Shapes match app/static/birky.svg so the still and the sprite agree."""
    shadow_rx = 62 + p.dy * 0.8
    shadow = f'<ellipse cx="118" cy="226" rx="{shadow_rx:.1f}" ry="8" fill="#000" opacity=".12"/>'
    tail = (f'<g transform="rotate({p.tail} 150 196)">'
            '<path d="M150 196c38-6 70-40 64-86-5-38-36-60-60-50-20 8-20 34-6 44 12 9 26 2 30-8 5 22-6 44-30 54z" fill="url(#tail)"/>'
            '<path d="M168 76c14 2 26 14 28 30" fill="none" stroke="#FFD6A6" stroke-width="5" stroke-linecap="round" opacity=".55"/></g>')
    body = ('<ellipse cx="112" cy="168" rx="50" ry="54" fill="url(#fur)"/><ellipse cx="110" cy="176" rx="31" ry="38" fill="url(#belly)"/>'
            '<ellipse cx="88" cy="218" rx="17" ry="8" fill="#C9662A"/><ellipse cx="134" cy="218" rx="17" ry="8" fill="#C9662A"/>')
    head = (f'<g transform="rotate({p.tilt} 110 130)">'
            '<path d="M70 56 64 22c0-4 5-6 8-3l20 24z" fill="#D46C25"/><path d="M71 50 68 30l14 16z" fill="#F7C9A0"/>'
            '<path d="M150 56 156 22c0-4-5-6-8-3l-20 24z" fill="#D46C25"/><path d="M149 50 152 30l-14 16z" fill="#F7C9A0"/>'
            '<path d="M64 22l-6-10M66 20l-1-11M156 22l6-10M154 20l1-11" stroke="#B85A1C" stroke-width="3" stroke-linecap="round"/>'
            '<ellipse cx="110" cy="88" rx="52" ry="46" fill="url(#fur)"/><ellipse cx="110" cy="104" rx="30" ry="22" fill="url(#belly)"/>'
            '<ellipse cx="78" cy="104" rx="9" ry="6" fill="#F28C8C" opacity=".55"/><ellipse cx="142" cy="104" rx="9" ry="6" fill="#F28C8C" opacity=".55"/>'
            + eyes(p.eyes) + brows(p.brows) + mouth(p.mouth) + "</g>")
    scarf = ('<path d="M66 126q44 22 90 0l4 12q-48 26-98 0z" fill="url(#scarf)"/><path d="M132 136l12 32-14 2-6-30z" fill="url(#scarf)"/>'
             '<path d="M136 150l9 2M138 158l8 2" stroke="#7CD7A6" stroke-width="2" stroke-linecap="round" opacity=".7"/>')
    right_arm = '<ellipse cx="142" cy="160" rx="12" ry="18" transform="rotate(-30 142 160)" fill="#D97330"/>'
    coin_x = 111 if p.wave is None else 122
    coin = (f'<g transform="translate({coin_x - 111} {p.coin_dy})"><circle cx="111" cy="164" r="25" fill="url(#coin)" stroke="#B57A0E" stroke-width="2.5"/>'
            '<circle cx="111" cy="164" r="18" fill="none" stroke="#FFF1B8" stroke-width="2" opacity=".7"/>'
            '<text x="111" y="173" text-anchor="middle" font-family="system-ui,Segoe UI,Arial" font-weight="800" font-size="25" fill="#8A5A06">€</text></g>')
    right_paw = '<ellipse cx="134" cy="168" rx="9" ry="7" fill="#E88A45"/>'
    if p.wave is None:
        left = ('<ellipse cx="80" cy="160" rx="12" ry="18" transform="rotate(30 80 160)" fill="#D97330"/>',
                '<ellipse cx="88" cy="168" rx="9" ry="7" fill="#E88A45"/>')
        arms_back, arms_front = left[0] + right_arm, left[1] + right_paw
    else:  # left arm swings up from the shoulder, behind nothing
        arm = (f'<g transform="rotate({p.wave} 68 146)"><ellipse cx="68" cy="164" rx="12" ry="20" fill="#D97330"/>'
               '<ellipse cx="68" cy="182" rx="10" ry="8" fill="#E88A45"/></g>')
        arms_back, arms_front = right_arm, right_paw + arm
    fig = tail + body + head + scarf + arms_back + coin + arms_front
    return f'{shadow}<g transform="translate({p.dx} {p.dy})">{fig}</g>{extras(p.extras)}'


def seq(n: int = COLS, **lists) -> list[Pose]:
    return [Pose(**{k: v[i] for k, v in lists.items()}) for i in range(n)]


MOODS: dict[str, list[Pose]] = {
    # breathing bob with a blink on frames 3-4
    "idle": seq(dy=[0, -2, -3, -2, 0, 0], tail=[0, 2, 4, 2, 0, -2],
                eyes=["open", "open", "half", "closed", "half", "open"]),
    "talk": seq(mouth=["smile", "open", "wide", "open", "o", "open"], dy=[0, -1, -2, -1, 0, 0],
                tail=[0, 1, 2, 1, 0, -1], eyes=["open"] * 4 + ["half", "open"]),
    "happy": seq(dy=[0, -10, -18, -10, 0, 2], coin_dy=[0, -6, -16, -6, 0, 0], tail=[0, 6, 10, 6, 0, -3],
                 eyes=["happy"] * 6, mouth=["wide"] * 6, brows=["raised"] * 6,
                 extras=[[], ["spark1"], ["spark3"], ["spark2"], ["spark1"], []]),
    "think": seq(tilt=[0, -4, -7, -7, -7, -4], eyes=["open", "up", "up", "up", "up", "open"],
                 mouth=["smile", "flat", "o", "o", "flat", "flat"], brows=["normal", "raised", "raised", "raised", "raised", "normal"],
                 tail=[0, -2, -3, -3, -2, 0], extras=[[], ["dots1"], ["dots2"], ["dots3"], ["dots3"], ["dots2"]]),
    "worried": seq(dx=[0, -1.5, 1.5, -1.5, 1.5, 0], eyes=["open", "open", "open", "half", "open", "open"],
                   brows=["worried"] * 6, mouth=["frown", "frown", "o", "frown", "frown", "o"],
                   extras=[["sweat0"], ["sweat0"], ["sweat1"], ["sweat1"], ["sweat2"], ["sweat2"]]),
    "wave": seq(wave=[160, 135, 160, 135, 160, 150], tail=[0, 3, 0, 3, 0, 1], dy=[0, -1, 0, -1, 0, 0],
                eyes=["happy", "happy", "open", "open", "happy", "open"], mouth=["wide", "wide", "smile", "smile", "wide", "smile"],
                extras=[["lines"], [], ["lines"], [], ["lines"], []]),
}
# which frame represents each mood as a still
STILL = {"idle": 0, "talk": 2, "happy": 2, "think": 3, "worried": 2, "wave": 0}
FPS = {"idle": 6, "talk": 10, "happy": 10, "think": 6, "worried": 8, "wave": 8}


def svg(w: int, h: int, body: str, label: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{label}">'
            f"{DEFS}{body}</svg>\n")


def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for r, (mood, poses) in enumerate(MOODS.items()):
        assert len(poses) == COLS, mood
        for c, p in enumerate(poses):
            rows.append(f'<g transform="translate({c * CELL} {r * CELL})">{squirrel(p)}</g>')
        (OUT / f"{mood}.svg").write_text(svg(CELL, CELL, squirrel(poses[STILL[mood]]), f"Birky the squirrel, {mood}"), encoding="utf-8")
    w, h = COLS * CELL, len(MOODS) * CELL
    (OUT / "sprite.svg").write_text(svg(w, h, "".join(rows), "Birky sprite sheet"), encoding="utf-8")
    atlas = {"image": "sprite.svg", "frame": CELL, "cols": COLS,
             "moods": {m: {"row": i, "frames": COLS, "fps": FPS[m]} for i, m in enumerate(MOODS)}}
    (OUT / "sprite.json").write_text(json.dumps(atlas, indent=2) + "\n", encoding="utf-8")
    return atlas


def render_png(scale: int = 2) -> None:
    from playwright.sync_api import sync_playwright
    src = (OUT / "sprite.svg").read_text(encoding="utf-8")
    w, h = COLS * CELL, len(MOODS) * CELL
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception:  # no bundled Chromium: fall back to Edge, like the deck builder
            browser = pw.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=scale)
        page.set_content(f'<html><body style="margin:0;background:transparent">{src}</body></html>')
        page.locator("svg").screenshot(path=str(OUT / "sprite.png"), omit_background=True)
        browser.close()


if __name__ == "__main__":
    atlas = build()
    if "--png" in sys.argv:
        render_png()
    print(f"wrote {len(atlas['moods'])} moods x {COLS} frames to {OUT}")
