"""Build Oiva's animated sprite (the "Penny" squirrel pack) for the web.

Source: assets/penny/spritesheet-extended.png, 8 columns x 11 rows of 192x208 cells:
  0 idle(6)  1 running-right(8)  2 running-left(8)  3 waving(4)  4 jumping(5)  5 failed(8)
  6 waiting(6)  7 running(6)  8 review(6)  9-10 look directions 0..337.5 deg, clockwise from up (8 + 8)

Writes into app/static/birky/:
  penny.webp   the same grid scaled to 144x156 cells (used by birky.css)
  penny.json   the atlas: which row is which mood, frames and frame time
  penny.png    one still (idle, first frame) on a square canvas, for avatars and the favicon

Run: python scripts/make_penny_sprite.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "penny" / "spritesheet-extended.png"
OUT = ROOT / "app" / "static" / "birky"
COLS, ROWS, CW, CH = 8, 11, 192, 208
SCALE = 0.75  # 144x156 cells: sharp at 2x for the ~70px guide

# Oiva's moods -> rows of the pack. Durations are per loop, in seconds.
MOODS = {
    "idle":    {"row": 0, "frames": 6, "loop": 1.2},
    "walk-r":  {"row": 1, "frames": 8, "loop": 0.64},
    "walk-l":  {"row": 2, "frames": 8, "loop": 0.64},
    "wave":    {"row": 3, "frames": 4, "loop": 0.6},
    "happy":   {"row": 4, "frames": 5, "loop": 0.7},
    "worried": {"row": 5, "frames": 8, "loop": 1.2},
    "talk":    {"row": 6, "frames": 6, "loop": 0.9},
    "busy":    {"row": 7, "frames": 6, "loop": 0.6},
    "think":   {"row": 8, "frames": 6, "loop": 1.2},
}
LOOK_ROWS = (9, 10)  # 16 directions, 22.5 deg apart, clockwise from straight up


def build() -> dict:
    sheet = Image.open(SRC).convert("RGBA")
    assert sheet.size == (COLS * CW, ROWS * CH), sheet.size
    w, h = round(CW * SCALE), round(CH * SCALE)
    small = sheet.resize((COLS * w, ROWS * h), Image.LANCZOS)
    small.save(OUT / "penny.webp", "WEBP", quality=88, method=6)

    still = sheet.crop((0, 0, CW, CH))
    box = still.getbbox()
    still = still.crop(box)
    side = max(still.size) + 8
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(still, ((side - still.width) // 2, (side - still.height) // 2))
    square.resize((256, 256), Image.LANCZOS).save(OUT / "penny.png", optimize=True)

    atlas = {"image": "penny.webp", "cols": COLS, "rows": ROWS, "cell": [w, h],
             "moods": MOODS, "look": {"rows": list(LOOK_ROWS), "directions": 16, "step_deg": 22.5}}
    (OUT / "penny.json").write_text(json.dumps(atlas, indent=2) + "\n", encoding="utf-8")
    return atlas


if __name__ == "__main__":
    a = build()
    print("wrote", OUT / a["image"], a["cell"], (OUT / a["image"]).stat().st_size // 1024, "KB")
