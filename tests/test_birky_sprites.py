import json
from pathlib import Path

from scripts import make_birky_sprites as gen

STATIC = Path(__file__).resolve().parent.parent / "app" / "static" / "birky"


def test_committed_sprite_matches_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "OUT", tmp_path)
    gen.build()
    for name in ["sprite.svg", "sprite.json", *(f"{m}.svg" for m in gen.MOODS)]:
        assert (tmp_path / name).read_text(encoding="utf-8") == (STATIC / name).read_text(encoding="utf-8"), \
            f"{name} is stale: run python scripts/make_birky_sprites.py --png"


def test_css_rows_match_penny_atlas():
    atlas = json.loads((STATIC / "penny.json").read_text(encoding="utf-8"))
    css = (STATIC / "birky.css").read_text(encoding="utf-8")
    assert atlas["cols"] == 8 and atlas["rows"] == 11 and "800% 1100%" in css and "penny.webp" in css
    for mood, spec in atlas["moods"].items():
        assert f".birky.{mood}{{--row:{spec['row']};--n:{spec['frames']};" in css
        assert 1 <= spec["frames"] <= atlas["cols"]


def test_penny_assets_exist_and_guide_knows_the_moods():
    from PIL import Image
    atlas = json.loads((STATIC / "penny.json").read_text(encoding="utf-8"))
    w, h = atlas["cell"]
    assert Image.open(STATIC / "penny.webp").size == (w * atlas["cols"], h * atlas["rows"])
    assert Image.open(STATIC / "penny.png").size == (256, 256)
    guide = (STATIC / "guide.js").read_text(encoding="utf-8")
    for mood in atlas["moods"]:
        assert f'"{mood}"' in guide
