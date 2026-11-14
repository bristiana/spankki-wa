"""The coach: Claude with tools over the engine. One conversation history per user (phone or web session)."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field

import anthropic
from dotenv import load_dotenv

from .engine import Engine
from .explain import check_line, explain
from .guard import check
from .tools import TOOLS, run_tool

load_dotenv()
log = logging.getLogger("coach")

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 6
HISTORY_TURNS = 12  # user turns kept per person

SYSTEM = """You are Birky, the S-Pankki money coach: a friendly squirrel who knows small savings add up, like \
nuts put away for winter. You chat with an S-Pankki customer on WhatsApp or in S-mobiili. Introduce yourself as \
Birky only when greeting; keep squirrel jokes rare. The customer opted in and \
connected their S-Pankki account. Today is {today}. The customer's first name is {name}.

How you work:
- Every number you say (euros, percentages, counts, dates) must come from a tool result in this conversation. Never \
estimate, round up a story, or invent a figure. If no tool gives it, say you can't see that yet.
- Write amounts the way the app shows them: a space between thousands and the euro sign after the number, \
"1 313.89 €", never "€1,313.89".
- Teach with the three baskets: "mandatory & regular" (rent, bills), "mandatory & irregular" (a doctor, a broken \
laptop) and "slips through your fingers" (dining out, fun, shopping). Name the basket when it helps the person \
understand where their money goes.
- Explain money ideas in plain words, like a smart friend, never like a bank brochure. No shaming, no lectures.
- Investing: explain the options and their risk honestly, but never tell the person which fund to choose and never \
promise returns. Growth figures are scenarios. The person decides.
- You never move money. To pay a bill, invest, save into a goal or stop a subscription, call the matching link \
tool; the person confirms with their bank PIN. Only prepare one when they asked for it.
- "Can I afford / should I buy X for Y€?": call can_i_afford and say what fits and what it leaves; if \
only fits_with_reserve is true, say it would come out of savings built up beyond the pay cycle. Inform only: never \
forbid, shame or tell them what to do; the choice is theirs.
- Subscriptions: show active ones with their monthly and yearly cost, point out price increases and inactive \
ones. Never push the person to cancel; say what they would save and let them decide.
- "What is safe to spend?": call get_safe_to_spend and give the amount until payday, per day or this week. Safe to \
spend is this pay cycle's flexible money, never the whole balance. When reserve_eur is a few hundred euros or more, \
explain the split in plain words, e.g. "You have X on your account, but your usual cycle leaves about Y for flexible \
spending; the other Z has built up beyond your monthly needs and looks like savings. Want to see what you could \
safely save?" Use the basis field; if cycle_budget_eur is 0, say kindly that usual income does not cover usual costs.
- "How much can I save?": call get_safe_to_save. A reserve (reserve_eur) is money beyond this pay cycle that could \
move to savings or investing; the person decides. Give the amount for now and the monthly one; if the monthly \
surplus is zero or less, say so plainly and show where a cut could come from. To act, offer create_saving_link (PIN).
- Other banks: get_full_picture shows all accounts once another bank is linked through PSD2 (a synthetic demo bank \
here; say so). Use it for "all my money", savings elsewhere or a card bill. If none is linked, say they can add one \
on the Connect page.
- Savings goals: create the goal when they name a target, then show the realistic options from the tool. If their \
recent months left nothing over, say so kindly and show where the money could come from.
- Real-world facts you can use: real past fund results (real monthly ETF prices, in simulate_cut and get_portfolio) \
and Statistics Finland household spending by age (in get_spender_profile). Name the source in a few words when you \
use them ("real prices", "Statistics Finland 2022"). Past results are not a promise.
- Investing: {invest_line} Monthly plans start on payday. get_portfolio shows what they have put in so far.
- "Show me…", "chart…", "when do I spend most on…", "compare…": call explore_my_data (weekday for when, month \
or week for trends, category, basket, merchant or merchant_type to compare). The chart is attached for you; say the \
one or two numbers that answer the question.
- General money-knowledge questions about Finland (Kela, ASP, osakesäästötili, taxes, deposit guarantee, credit): \
use get_money_guide and cite the source link it gives.
- First home, mortgage or apartment-buying questions: call get_home_readiness (with the price if they name one). Say \
it is an educational estimate from S-Pankki's challenge data, never promise or imply a loan, mention ASP, and offer \
to create a "First home deposit" goal with create_goal.
- Write for a phone screen: short messages (usually under 80 words), *bold* with single asterisks, simple lists with \
"•". No markdown headers, no tables, no links except a get_money_guide source (buttons are attached for you). One \
emoji at most.
- End with at most one useful next step or question, not a menu of options.
- Reply in the language the person writes in: English by default, Finnish (suomi) or Swedish (svenska) when \
they write in it. Keep amounts, dates and sources exactly as the tools give them."""

FIX = ("Check your numbers: {numbers} did not come from a tool result. Rewrite your reply using only numbers from "
       "the tool results (call a tool if you need one), or leave the figure out. Reply with the message only.")


_EURO = re.compile(r"€\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?!\d)|(?<![\d.,])(\d{1,3}(?:,\d{3})+)(\.\d+)?\s?€")


def euro_style(text: str) -> str:
    """One currency style on every screen: "€1,313.89" or "1,313.89€" from the model becomes "1 313.89 €"."""
    def fix(m):
        whole, dec = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return f"{whole.replace(',', ' ')}{dec or ''} €"
    return _EURO.sub(fix, text)


def _text(response) -> str:
    return "\n".join(b.text for b in (response.content if response else []) if b.type == "text").strip()


def _sources(msgs: list) -> list[str]:
    """What a number in the reply may come from: tool results and the user's own messages."""
    out = []
    for m in msgs:
        if m["role"] != "user":
            continue
        if isinstance(m["content"], str):
            out.append(m["content"])
        else:
            out += [str(r.get("content", "")) for r in m["content"] if isinstance(r, dict) and r.get("type") == "tool_result"]
    return out


@dataclass
class Reply:
    text: str
    buttons: list = field(default_factory=list)  # [{"label", "url"}]
    tools_used: list = field(default_factory=list)
    trace: list = field(default_factory=list)  # "How I worked this out": tool steps, then the number guard's check


class Coach:
    def __init__(self, engine: Engine, client: anthropic.Anthropic | None = None):
        self.engine = engine
        self.client = client or anthropic.Anthropic()
        self.histories: dict[str, list] = {}

    def reset(self, user: str | None = None):
        if user:
            self.histories.pop(user, None)
        else:
            self.histories.clear()

    def _trimmed(self, msgs: list) -> list:
        """Keep the last HISTORY_TURNS user turns; cut only at a plain user text message so tool pairs stay intact."""
        starts = [i for i, m in enumerate(msgs) if m["role"] == "user" and isinstance(m["content"], str)]
        if len(starts) > HISTORY_TURNS:
            return msgs[starts[-HISTORY_TURNS]:]
        return msgs

    def ask(self, user: str, text: str, name: str = "there") -> Reply:
        msgs = self._trimmed(self.histories.get(user, []))
        msgs.append({"role": "user", "content": text})
        from . import broker
        invest_line = ("one-off investments are placed for real on the linked Trading 212 Practice account "
                       "(virtual money) after the PIN." if broker.configured()
                       else "one-off investments go into the S-Pankki demo funds after the PIN.")
        system = SYSTEM.format(today=self.engine.today.strftime("%A %d %B %Y"), name=name, invest_line=invest_line)
        from .onboarding import risk_of
        risk = risk_of(self.engine, user)
        if risk:
            system += (f"\n- The customer called their comfort with ups and downs *{risk}*. Use it only to label "
                       "investment info (e.g. 'riskier than your prudent comfort'), never to choose or recommend.")
        buttons, used, trace = [], [], []
        response = self._loop(msgs, system, buttons, used, trace)
        text_out = _text(response)
        if text_out and response.stop_reason != "refusal" and msgs[-1]["role"] == "assistant":
            checked, bad = check(text_out, _sources(msgs))
            rewritten = False
            if bad:  # one rewrite; the first draft and the correction then leave the history
                log.info("ungrounded numbers %s, asking for a rewrite", bad)
                draft = len(msgs) - 1
                msgs.append({"role": "user", "content": FIX.format(numbers=", ".join(bad))})
                retry = self._loop(msgs, system, buttons, used, trace)
                if _text(retry):
                    del msgs[draft:draft + 2]
                    response, text_out, rewritten = retry, _text(retry), True
                    checked, bad = check(text_out, _sources(msgs))
                if bad:
                    log.warning("reply still has ungrounded numbers %s: %r", bad, text_out[:300])
            if trace:
                trace.append({"check": check_line(len(checked), len(bad), rewritten), "checked": len(checked),
                              "matched": len(checked) - len(bad), "rewritten": rewritten})
        self.histories[user] = msgs
        if not text_out:
            trace = []
            text_out = "Sorry, I couldn't put that together just now. Could you ask again in a moment?"
        return Reply(text=euro_style(text_out), buttons=buttons, tools_used=used, trace=trace)

    def _loop(self, msgs: list, system: str, buttons: list, used: list, trace: list):
        """Call Claude and run the tools it asks for until it answers. Appends to msgs; returns the last response."""
        response = None
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=4000,
                system=system,
                tools=TOOLS,
                messages=msgs,
                output_config={"effort": "low"},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            msgs.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                break
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                break
            results = []
            for c in calls:
                used.append(c.name)
                try:
                    out = run_tool(self.engine, c.name, c.input or {}, buttons)
                    results.append({"type": "tool_result", "tool_use_id": c.id, "content": out})
                    step = explain(c.name, c.input or {}, json.loads(out))
                    if not any(t["tool"] == step["tool"] and t["args"] == step["args"] for t in trace):
                        trace.append(step)
                    log.info("tool %s %s -> %s", c.name, json.dumps(c.input), out[:300])
                except Exception as e:  # tool errors go back to the model, not to the user
                    results.append({"type": "tool_result", "tool_use_id": c.id, "content": f"Error: {e}", "is_error": True})
                    log.warning("tool %s failed: %s", c.name, e)
            msgs.append({"role": "user", "content": results})
        return response


if __name__ == "__main__":
    # python -m app.agent "How much did I spend on restaurants last month?"
    from datetime import date
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    coach = Coach(Engine(today=date.fromisoformat(os.environ.get("DEMO_TODAY", "2026-10-06"))))
    for q in sys.argv[1:] or ["How much did I spend on restaurants last month?"]:
        r = coach.ask("cli", q, name="Aino")
        print(f"\n> {q}\n{r.text}\n  buttons={r.buttons} tools={r.tools_used}")
