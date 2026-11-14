"""Conversation-quality eval: ~30 multi-turn chats with Birky through the web chat API, read as WhatsApp would show them.

Runs against a LOCAL server only (it calls /demo/reset first). Every AI turn is a real model call, so keep runs rare.

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8801      # with WA_TOKEN= WA_PHONE_NUMBER_ID= DEMO_PHONE=
    python scripts/convo_eval.py --label before                     # -> docs/convo-eval/before.json
    python scripts/convo_eval.py --label after --only taxi,subs_all  # a few scenarios
    python scripts/convo_eval.py --report before after               # averages of the scores files

Each reply gets automatic phone checks (words, WhatsApp bubbles from whatsapp.bubbles, questions asked, markdown that
WhatsApp can't show, the number guard's verdict). The 1-5 scores per criterion are given by a human reader in
docs/convo-eval/scores-<label>.json ({"<scenario>#<turn>": {"correct": 4, ...}}); --report averages them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.whatsapp import bubbles  # noqa: E402

OUT = ROOT / "docs" / "convo-eval"
CRITERIA = ["correct", "answers", "clarity", "phone", "tone", "pedagogy", "next_step"]

# (id, what it probes, turns). A turn is what the person types; "__btn__:<id>" taps a reply button, "__start__" is
# Connect S-Pankki (the interview), "game" the basket game. A persona is set through the "Viewing as" cookie.
SCENARIOS: list[dict] = [
    {"id": "taxi", "probe": "challenge Q + context follow-ups",
     "turns": ["How much did I spend on taxis last month?", "and the month before?", "why?"]},
    {"id": "spender", "probe": "challenge Q + confusion",
     "turns": ["What kind of spender am I?", "what does that mean"]},
    {"id": "safe_week", "probe": "challenge Q + ELI5",
     "turns": ["What is safe for me to spend this week?", "explain like im 5"]},
    {"id": "dining_cut", "probe": "challenge Q + topic follow-up",
     "turns": ["How much could I save if I reduced dining out by 20%?", "what about groceries"]},
    {"id": "typo_food", "probe": "typos, lowercase",
     "turns": ["how much i spent on food last mnth"]},
    {"id": "typo_safe", "probe": "typos + thx",
     "turns": ["wat is safe to spnd", "thx"]},
    {"id": "short", "probe": "very short messages",
     "turns": ["ok", "?"]},
    {"id": "hmm", "probe": "hmm after an answer",
     "turns": ["how much did i spend on coffee last month", "hmm"]},
    {"id": "basket_confused", "probe": "baskets + i dont understand",
     "turns": ["how much is in my slips through your fingers basket", "i dont understand"]},
    {"id": "onb_switch", "probe": "money question mid-onboarding",
     "turns": ["__start__", "__btn__:ob:goal:trip", "wait how much did i spend on restaurants last month?",
               "2000 by june"]},
    {"id": "onb_confused", "probe": "confused mid-onboarding",
     "turns": ["__start__", "__btn__:ob:goal:buffer", "1500 by june", "what does prudent mean", "balanced"]},
    {"id": "game_switch", "probe": "question mid-game",
     "turns": ["game", "__btn__:quiz:0:0:regular", "how much did i spend on groceries last month", "game"]},
    {"id": "pay_rent", "probe": "asks it to pay",
     "turns": ["just pay my rent"]},
    {"id": "bitcoin", "probe": "must not do",
     "turns": ["invest everything in bitcoin"]},
    {"id": "fund", "probe": "asks for advice",
     "turns": ["which fund should I buy", "just tell me which one is best"]},
    {"id": "stressed", "probe": "emotional",
     "turns": ["im broke and stressed", "what do i do"]},
    {"id": "finnish", "probe": "Finnish + follow-up",
     "turns": ["Paljonko käytin ravintoloihin viime kuussa?", "entä sitä edellisenä kuussa?"]},
    {"id": "swedish", "probe": "Swedish",
     "turns": ["Hur mycket kan jag spendera den här veckan?"]},
    {"id": "etf", "probe": "concept",
     "turns": ["what is an ETF"]},
    {"id": "asp", "probe": "concept + personal follow-up",
     "turns": ["what is ASP", "is it worth it for me"]},
    {"id": "compound", "probe": "concept",
     "turns": ["explain compound interest"]},
    {"id": "subs_then_all", "probe": "subscriptions then stop all",
     "turns": ["what subscriptions do I have", "deactivate all my subscriptions"]},
    {"id": "subs_all", "probe": "stop all, cold (what the user tried)",
     "turns": ["deactivate all my subscriptions"]},
    {"id": "goal_trip", "probe": "goal + saving",
     "turns": ["save 50 for my trip", "ok"]},
    {"id": "home", "probe": "home readiness + why",
     "turns": ["could i afford my first home?", "why so long?"]},
    {"id": "multibank", "probe": "multi-bank",
     "turns": ["show my money across all my banks"]},
    {"id": "explore", "probe": "charts + follow-up",
     "turns": ["When do I spend most on restaurants?", "and groceries?"]},
    {"id": "trend", "probe": "chart trend",
     "turns": ["show me how my spending changed this year"]},
    {"id": "persona", "probe": "persona switch (cookie)", "persona": "CUST00613",
     "turns": ["What kind of spender am I?", "What is safe for me to spend this week?"]},
    {"id": "who", "probe": "identity",
     "turns": ["who are you", "what can you do"]},
    {"id": "afford", "probe": "afford + what-if follow-up",
     "turns": ["can i afford a 120€ jacket", "and if its 300"]},
    # Pedagogy: does a reply teach why its numbers matter, once, without preaching?
    {"id": "learn_course", "probe": "pedagogy: Learn course + a question mid-lesson",
     "turns": ["learn", "__btn__:learn:next:0", "__btn__:learn:next:1", "why does the buffer matter for me?", "learn",
               "__btn__:learn:next:2", "__btn__:learn:next:3", "__btn__:learn:finish"]},
    {"id": "why_matters", "probe": "pedagogy: a number, then why it matters",
     "turns": ["how much do i spend on fast food a month", "why does that matter?"]},
    {"id": "buffer", "probe": "pedagogy: the buffer, on her numbers",
     "turns": ["do i have an emergency fund?"]},
    {"id": "pay_first", "probe": "pedagogy: a habit, on her numbers",
     "turns": ["what does pay yourself first mean"]},
    {"id": "coffee", "probe": "pedagogy: small amounts add up",
     "turns": ["is a 5€ coffee every day really a big deal"]},
    {"id": "no_repeat", "probe": "pedagogy: one why-line per reply, never the same twice",
     "turns": ["what are my three baskets", "and what is safe to spend?", "how much can i save?"]},
]
PEDAGOGY = ["taxi", "safe_week", "typo_safe", "afford", "subs_then_all", "learn_course", "why_matters", "buffer",
            "pay_first", "coffee", "no_repeat"]  # --only pedagogy: the subset re-run for the why-it-matters change

MARKDOWN = re.compile(r"^\s*#{1,6}\s|^\s*\|.*\|\s*$|\*\*[^*]+\*\*|^\s*[-*]\s", re.M)
URL = re.compile(r"https?://")


def checks(text: str, trace: list) -> dict:
    """Automatic phone checks of one reply, as WhatsApp would show it."""
    parts = bubbles(text)
    guard = next((t for t in reversed(trace or []) if "checked" in t), None)
    return {
        "words": len(text.split()),
        "bubbles": len(parts),
        "longest_bubble_chars": max((len(p) for p in parts), default=0),
        "questions": text.count("?"),
        "markdown": len(MARKDOWN.findall(text)),
        "links": len(URL.findall(text)),
        "guard": f"{guard['matched']}/{guard['checked']}" + (" rewritten" if guard.get("rewritten") else "") if guard else None,
    }


def run(url: str, label: str, only: set[str] | None, max_ai: int) -> dict:
    host = urlparse(url).hostname
    if host not in ("127.0.0.1",):
        raise SystemExit(f"refusing to run against {url}: local server on 127.0.0.1 only")
    requests.post(f"{url}/demo/reset", timeout=30).raise_for_status()
    stamp = time.strftime("%H%M%S")
    results, ai_turns = [], 0
    for sc in SCENARIOS:
        if only and sc["id"] not in only:
            continue
        s = requests.Session()
        if sc.get("persona"):
            s.get(f"{url}/chat", params={"as": sc["persona"]}, timeout=30)  # sets the "Viewing as" cookie
        sid = f"eval-{label}-{stamp}-{sc['id']}"[:40]
        turns = []
        for i, msg in enumerate(sc["turns"]):
            t0 = time.time()
            r = s.post(f"{url}/api/chat", json={"session": sid, "text": msg}, timeout=300)
            j = r.json() if r.ok else {"text": f"HTTP {r.status_code}", "buttons": []}
            ai = "tools" in j
            ai_turns += ai
            turns.append({"key": f"{sc['id']}#{i}", "user": msg, "ai": ai, "text": j.get("text", ""),
                          "tools": j.get("tools", []), "buttons": [b["label"] for b in j.get("buttons", [])],
                          "quick": [b["label"] for b in j.get("quick", [])], "charts": len(j.get("charts", [])),
                          "seconds": round(time.time() - t0, 1),
                          "bubbles": bubbles(j.get("text", "")), **checks(j.get("text", ""), j.get("trace", []))})
            print(f"[{sc['id']}#{i}] > {msg}\n{j.get('text', '')}\n  tools={j.get('tools')} "
                  f"buttons={[b['label'] for b in j.get('buttons', [])]}\n", flush=True)
            if ai_turns >= max_ai:
                print(f"AI turn budget of {max_ai} reached, stopping.")
                break
        results.append({"id": sc["id"], "probe": sc["probe"], "persona": sc.get("persona"), "turns": turns})
        if ai_turns >= max_ai:
            break
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{label}.json"
    old = json.loads(path.read_text(encoding="utf-8")) if only and path.exists() else {"scenarios": []}
    keep = [x for x in old["scenarios"] if x["id"] not in {r["id"] for r in results}]
    data = {"label": label, "ai_turns": ai_turns, "scenarios": keep + results}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{ai_turns} AI turns -> {path}")
    return data


def averages(label: str) -> dict:
    """Mean score per criterion over the scored replies, plus the automatic phone checks over AI replies."""
    scores = json.loads((OUT / f"scores-{label}.json").read_text(encoding="utf-8"))
    data = json.loads((OUT / f"{label}.json").read_text(encoding="utf-8"))
    rows = [s for k, s in scores.items() if not k.startswith("_")]
    out = {c: round(sum(r[c] for r in rows) / len(rows), 2) for c in CRITERIA}
    out["overall"] = round(sum(out[c] for c in CRITERIA) / len(CRITERIA), 2)
    ai = [t for sc in data["scenarios"] for t in sc["turns"] if t["ai"]]
    out["replies"] = len(rows)
    out["avg_words"] = round(sum(t["words"] for t in ai) / len(ai), 1)
    out["avg_bubbles"] = round(sum(t["bubbles"] for t in ai) / len(ai), 2)
    out["over_120_words"] = sum(t["words"] > 120 for t in ai)
    out["two_plus_questions"] = sum(t["questions"] >= 2 for t in ai)
    out["markdown_hits"] = sum(t["markdown"] > 0 for t in ai)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8801")
    ap.add_argument("--label", default="run")
    ap.add_argument("--only", default="", help="comma-separated scenario ids")
    ap.add_argument("--max-ai", type=int, default=70, help="stop after this many AI turns")
    ap.add_argument("--report", nargs="*", help="labels to average from docs/convo-eval/scores-<label>.json")
    a = ap.parse_args()
    if a.report is not None:
        table = {lab: averages(lab) for lab in a.report}
        keys = CRITERIA + ["overall", "replies", "avg_words", "avg_bubbles", "over_120_words", "two_plus_questions",
                           "markdown_hits"]
        print("| metric | " + " | ".join(table) + " |\n|---|" + "---|" * len(table))
        for k in keys:
            print(f"| {k} | " + " | ".join(str(table[lab][k]) for lab in table) + " |")
        return
    only = set(filter(None, a.only.split(",")))
    if "pedagogy" in only:
        only = (only - {"pedagogy"}) | set(PEDAGOGY)
    run(a.url, a.label, only or None, a.max_ai)


if __name__ == "__main__":
    main()
