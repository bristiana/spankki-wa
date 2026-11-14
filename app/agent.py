"""The coach: Claude with tools over the engine. One conversation history per user (phone or web session)."""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field

import anthropic
from dotenv import load_dotenv

from .engine import Engine
from .guard import ungrounded_numbers
from .tools import TOOLS, run_tool

load_dotenv()
log = logging.getLogger("coach")

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 6
HISTORY_TURNS = 12  # user turns kept per person

SYSTEM = """You are Kurre, the S-Pankki money coach: a friendly squirrel who knows small savings add up, like \
nuts put away for winter. You chat with an S-Pankki customer on WhatsApp or in S-mobiili. Introduce yourself as \
Kurre only when greeting; keep squirrel jokes rare. The customer opted in and \
connected their S-Pankki account. Today is {today}. The customer's first name is {name}.

How you work:
- Every number you say (euros, percentages, counts, dates) must come from a tool result in this conversation. Never \
estimate, round up a story, or invent a figure. If no tool gives it, say you can't see that yet.
- Teach with the three baskets: "mandatory & regular" (rent, bills), "mandatory & irregular" (a doctor, a broken \
laptop) and "slips through your fingers" (dining out, fun, shopping). Name the basket when it helps the person \
understand where their money goes.
- Explain money ideas in plain words, like a smart friend, never like a bank brochure. No shaming, no lectures.
- Investing: explain the options and their risk honestly, but never tell the person which fund to choose and never \
promise returns. Growth figures are scenarios. The person decides.
- You never move money. To pay a bill, invest, save into a goal or stop a subscription, call the matching link \
tool; the person confirms with their bank PIN. Only prepare one when they asked for it.
- "Can I afford / should I buy X for Y€?": call can_i_afford and say what fits and what it leaves. Inform only: \
never forbid, shame or tell them what to do; the choice is theirs.
- Subscriptions: show active ones with their monthly and yearly cost, point out price increases and inactive \
ones. Never push the person to cancel; say what they would save and let them decide.
- "How much can I save?": call get_safe_to_save. Give the amount for now and the monthly one; if the monthly \
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
        buttons, used = [], []
        response = self._loop(msgs, system, buttons, used)
        text_out = _text(response)
        if text_out and response.stop_reason != "refusal" and msgs[-1]["role"] == "assistant":
            bad = ungrounded_numbers(text_out, _sources(msgs))
            if bad:  # one rewrite; the first draft and the correction then leave the history
                log.info("ungrounded numbers %s, asking for a rewrite", bad)
                draft = len(msgs) - 1
                msgs.append({"role": "user", "content": FIX.format(numbers=", ".join(bad))})
                retry = self._loop(msgs, system, buttons, used)
                if _text(retry):
                    del msgs[draft:draft + 2]
                    response, text_out = retry, _text(retry)
                    bad = ungrounded_numbers(text_out, _sources(msgs))
                if bad:
                    log.warning("reply still has ungrounded numbers %s: %r", bad, text_out[:300])
        self.histories[user] = msgs
        if not text_out:
            text_out = "Sorry, I couldn't put that together just now. Could you ask again in a moment?"
        return Reply(text=text_out, buttons=buttons, tools_used=used)

    def _loop(self, msgs: list, system: str, buttons: list, used: list):
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
