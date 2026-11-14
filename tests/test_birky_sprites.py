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


def test_css_rows_match_atlas():
    atlas = json.loads((STATIC / "sprite.json").read_text(encoding="utf-8"))
    css = (STATIC / "birky.css").read_text(encoding="utf-8")
    assert atlas["cols"] == 6 and "steps(6)" in css and "600% 600%" in css
    for mood, spec in atlas["moods"].items():
        assert f".birky.{mood}{{--row:{spec['row']};" in css
