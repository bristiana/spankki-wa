import json
from pathlib import Path

from PIL import Image

STATIC = Path(__file__).resolve().parent.parent / "app" / "static" / "oiva"


def test_css_rows_match_the_atlas():
    atlas = json.loads((STATIC / "oiva.json").read_text(encoding="utf-8"))
    css = (STATIC / "oiva.css").read_text(encoding="utf-8")
    assert atlas["cols"] == 8 and atlas["rows"] == 11 and "800% 1100%" in css and "oiva.webp" in css
    for mood, spec in atlas["moods"].items():
        assert f".oiva.{mood}{{--row:{spec['row']};--n:{spec['frames']};" in css
        assert 1 <= spec["frames"] <= atlas["cols"]


def test_assets_exist_and_the_guide_knows_the_moods():
    atlas = json.loads((STATIC / "oiva.json").read_text(encoding="utf-8"))
    w, h = atlas["cell"]
    assert Image.open(STATIC / "oiva.webp").size == (w * atlas["cols"], h * atlas["rows"])
    assert Image.open(STATIC / "oiva.png").size == (256, 256)
    guide = (STATIC / "guide.js").read_text(encoding="utf-8")
    for mood in atlas["moods"]:
        assert f'"{mood}"' in guide


def test_no_other_name_for_the_squirrel():
    root = STATIC.parent.parent
    for f in [*root.rglob("*.py"), *root.rglob("*.html"), *root.rglob("*.js"), *root.rglob("*.css")]:
        text = f.read_text(encoding="utf-8").lower()
        assert "birky" not in text and "penny" not in text, f
