"""How Birky sounds on a phone: a reaction on the person's message, then one to three short bubbles.

The model opens a reply with "[react]<emoji>" and splits bubbles with a line that is only "---". split_reaction() takes
the reaction out of the text; pace() decides whether it reaches the person, so about two replies in three carry one,
never the same emoji twice in a row. A reply that is only a reaction ("thanks" -> 🙏) always goes.

The reaction is the only emoji Birky sends: strip_emoji() takes every emoji out of the words that go out, on WhatsApp
and on the web, and the web chat shows no reaction at all.
"""
from __future__ import annotations

import random
import re

REACT = re.compile(r"\[react\]\s*([^\s\[\]]*)\s*", re.I)
SEPARATOR = re.compile(r"^\s*---+\s*$", re.M)
REACT_RATE = 0.67  # about two replies in three carry a reaction
PAST = 6  # replies the pace is judged over
# when a reaction-only reply would repeat the last emoji: a close neighbour instead
NEIGHBOUR = {"🙏": "🥰", "🥰": "🙏", "👍": "🙌", "🙌": "👍", "✅": "👏", "👏": "✅", "🎉": "🥳", "🥳": "🎉",
             "👀": "🤔", "🤔": "👀", "❤️": "🫶", "🫶": "❤️", "💪": "🔥", "🔥": "💪", "😂": "😄", "😄": "😂"}


# One emoji "piece": pictographs, symbols and dingbats, plus the glue of emoji sequences (VS15/VS16, ZWJ, keycap,
# tag characters; skin tones and regional-indicator flags sit inside U+1F000-1FAFF). An arrow or a letterlike symbol
# only counts when a VS16 makes it an emoji, so plain typography (arrows, "›", "€", "•") is left alone.
_EMOJI = (r"(?:[℀-⇿⬀-⯿]️|[\U0001F000-\U0001FAFF⌀-⏿☀-➿"
          r"⬅-⬇⬛⬜⭐⭕‼⁉〰〽㊗㊙"
          r"︎️‍⃣\U000E0020-\U000E007F])+")
_RUN = re.compile(r"[ \t]*" + _EMOJI + r"[ \t]*")
GAP = "\x00"


def strip_emoji(text: str | None) -> str:
    """The text with every emoji taken out, and the gaps it leaves tidied: "Done ✅ Paid" -> "Done Paid",
    "*Birky* 🐿️, hi" -> "*Birky*, hi", "🧾 Heads-up" -> "Heads-up". Deterministic, no model involved."""
    if not text:
        return text or ""
    s = _RUN.sub(GAP, text)
    if GAP not in s:
        return text
    s = re.sub(r"(^|\n)\x00+", r"\1", s)            # at the start of a line
    s = re.sub(r"\x00+(?=$|\n|[.,!?:;)\]])", "", s)  # at the end of a line, or before punctuation
    s = re.sub(r"(?<=[(\[])\x00+", "", s)           # right after an opening bracket
    return s.replace(GAP, " ").strip()


def single_emoji(e: str) -> bool:
    from .whatsapp import single_emoji as ok  # Meta's rule for a reaction, one place
    return bool(e) and ok(e)


def split_reaction(text: str) -> tuple[str | None, str]:
    """('🏠', 'the reply without it') from '[react]🏠\\n---\\nYes, rent is covered.'. A second [react] or one that is
    not a single emoji is dropped; separators left empty at the start or end go too."""
    emoji, text = None, text or ""
    m = REACT.search(text)
    if m:  # "[react]🏠Yes" with no space: the longest emoji at the start
        word = m.group(1)
        k = next((k for k in range(min(len(word), 10), 0, -1) if single_emoji(word[:k])), 0)
        emoji = word[:k] or None
        if k:
            text = text[:m.start()] + (word[k:] + " " if word[k:] else "") + text[m.end():]
        else:  # no emoji after it: only the tag goes
            text = re.sub(r"\[react\]\s*", "", text, count=1, flags=re.I)
    rest = REACT.sub("", text)
    parts = [p.strip() for p in SEPARATOR.split(rest)]
    return emoji, "\n---\n".join(p for p in parts if p)


def quick_reaction(text: str | None) -> str | None:
    """A fallback when the model picked none: 👀 question, 🙏 thanks, 💰 money news, 👋 hello."""
    t = (text or "").lower()
    if re.search(r"\b(thanks|thank you|thx|kiitos|mersi|tack)\b", t):
        return "🙏"
    if re.search(r"\b(salary|paid|bonus|raise|palkka)\b", t):
        return "💰"
    if re.match(r"^\s*(hi|hey|hello|moi|hei|terve|hej|salut)\b", t) and len(t) < 25:
        return "👋"
    if "?" in t or re.match(r"^\s*(how|what|why|where|when|can|should|is|am|do)\b", t):
        return "👀"
    return None


def pace(emoji: str | None, past: list, has_words: bool, roll: float | None = None) -> str | None:
    """The reaction that goes out, or None. `past`: earlier replies' reactions (None = none), oldest first.

    Never the one just used. A reply with no words is only the reaction, so it always goes (a neighbour emoji when
    it would repeat). Otherwise about REACT_RATE of replies: once 3 replies are known, a share under 60% forces one
    and over 70% skips one; in between it's the roll."""
    if not emoji:
        return None
    last = next((p for p in reversed(past) if p), None)
    if not has_words:
        return NEIGHBOUR.get(emoji, "👍" if emoji != "👍" else "🙌") if emoji == last else emoji
    if emoji == last:
        return None
    recent = past[-PAST:]
    share = sum(1 for p in recent if p) / len(recent) if recent else 0.0
    known = len(recent) >= 3
    roll = random.random() if roll is None else roll
    keep = (known and share < 0.6) or (not (known and share > 0.7) and roll < REACT_RATE)
    return emoji if keep else None
