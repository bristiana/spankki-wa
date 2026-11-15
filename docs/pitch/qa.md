# Judge Q&A: the 15 likeliest tough questions

Answers fit in about 20 seconds each. The source for each claim is in brackets.

**1. How do I know the AI didn't make up a number?**
The model doesn't compute anything. It calls 24 tools over a deterministic, tested engine (`app/tools.py`, `app/engine.py`, 815 tests). The prompt forbids any figure that isn't in a tool result (`app/agent.py`). A number guard checks every figure in the reply against the tool results. On a mismatch it forces one rewrite (`app/guard.py`). "How I worked this out" shows the steps, the sources and the verdict, e.g. "6 of 6 figures checked" (`app/explain.py`).
*If pressed:* the guard doesn't block. If the rewrite still misses, the reply goes out with "Only M of N figures matched" in the trace and a warning in the log. In production we'd swap in a safe fallback reply.

**2. Can Oiva move my money? What if it's prompt-injected?**
No. Its tools only *prepare* a payment, saving, investment or pause (`create_*_link` in `app/tools.py`). The money moves only on a confirm page with your PIN or Face ID (`/pay`, `/save`, `/invest`, `/stop` in `app/main.py`). Text in tool results that looks like an instruction is defanged (`app/guardrails.py` `defang`). A reply that claims "I paid" is rewritten (`MONEY_MOVED`). Double confirms and overdrafts are refused (`app/engine.py` `confirm`).

**3. Is this financial advice? FIN-FSA would care.**
It's information on your own numbers, not advice. The terms say so (`app/main.py` `TERMS`). Fund names are "Demo Funds" over real ETF prices, and you choose. The risk profile only labels the information. Code rewrites urgency and promised returns (`app/guardrails.py`). Where the legal line sits is a question for S-Pankki compliance before a pilot.

**4. GDPR: where does my data go?**
To Anthropic to generate the answer, and through Meta on WhatsApp. The consent screen and `/privacy` say this (`app/templates/connect.html`, `app/main.py` `PRIVACY`). PINs, CVVs, card numbers and codes are caught first: the secret itself never reaches the model, and Oiva asks you to delete it. IBANs and personal IDs are redacted (`app/guardrails.py`, `app/main.py`). "my data" exports everything. STOP switches Oiva off and deletes the chat history. Production needs DPAs, a DPIA and a retention policy. We haven't done those.

**5. Why WhatsApp? Banks avoid it.**
It's where people already talk, and talking is where shame drops (`docs/core-idea.md`). The channel is opt-in with explicit consent, and messages stay inside Meta's 24-hour rule with no templates (`docs/aprobare-whatsapp.md`). The same coach also runs inside S-mobiili, so WhatsApp is a choice, not a requirement (`docs/integration.md`).

**6. Is the data real?**
- The S-Pankki challenge dataset: synthetic transactions for 5,100 customers.
- Statistics Finland Household Budget Survey 2022 (`scripts/fetch_statfin.py`).
- Real monthly VWCE and XEON prices (`data/market.json`).
- Live PSD2 data from your other banks through Enable Banking (`app/enable_banking.py`).

Simulated, and labelled as such: the PIN and Face ID, the payments, the Demo Bank and the fund names (`README.md`, "Simulated").

**7. Is the PSD2 link real or a mock?**
Real account information through Enable Banking, a licensed PSD2 provider. It's read only, lasts 90 days, and you approve it in your own bank (`app/enable_banking.py`). The Enable Banking app isn't fully activated yet, so only accounts we registered can link today (`README.md`, Honest limits). The demo account shows a synthetic Demo Bank.

**8. What does it cost per user?**
About $1.65–2.85 a month for an active user on Opus 5.5, voice included, at list prices. On Sonnet 5.5 it's roughly half (`docs/pitch/feasibility.md`). Learn, the basket game and onboarding run without the AI. It's an estimate from prompt and tool sizes. We don't log tokens yet.

**9. How is "people like me" not a privacy leak?**
"People like me" shows only p25, median and p75 for groups of at least 30 customers, by postal area and age band. A smaller group falls back to a wider one (`app/peers.py` `MIN_GROUP = 30`, `scripts/build_peer_benchmark.py`). The spender profile uses one fixed benchmark of 535 customers aged 20–29 (`data/benchmark.json`). Nothing about an individual is stored in the benchmark.

**10. Why three baskets and not a budget app?**
They're the challenge's own model: mandatory and regular, mandatory and irregular, and "slips through your fingers". We call them Bills & basics, Occasional costs and Fun & extras (`app/engine.py` `BASKETS`). They teach one idea: only the third basket is yours to change today, and the second needs a buffer. You learn them through the 5-lesson course and the basket game on your own payments (`app/learn.py`, `app/quiz.py`), and through one "why it matters" line in each answer (`app/lessons.py`).

**11. Your hero is 38. The challenge is about young adults.**
Antti, 38, was picked by rule near the dataset's median customer age (`data/personas/index.json`, `docs/SUBMISSION.md`). Aino, 24, on her first salary, is one of six real customers you can switch to (`?as=CUST04831`). Her spender profile compares her with customers aged 20–29 (`data/benchmark.json`). "People like me" uses customers aged 18–24 in her area (`app/peers.py`). *Open decision: which one leads the demo.*

**12. How would S-mobiili actually integrate this?**
In three pieces, each on its own:
- Native home cards from `/api/widgets` (engine numbers, no AI, cached for 5 minutes)
- The chat in a WebView at `/chat?embed=1`
- Deep links with `&q=`

The confirm pages already report back with `postMessage("done")`, so S-mobiili's own strong customer authentication drops in (`docs/integration.md`).

**13. What breaks at scale? It's one container.**
This is a demo deployment. A Worker sits in front of one container, with state in a Durable Object (`worker/index.ts`, `wrangler.jsonc`). Today it is one container by design (`main-${INSTANCE}`). Sessions and the chat mirror are held in memory (`app/mirror.py`). The engine is per customer and deterministic, so it can be sharded per customer once those maps move to a shared store. In production it would run on S-Pankki's own infrastructure and APIs, not on a CSV.

**14. Latency? Bank customers won't wait.**
In our 31-chat evaluation (51 AI replies, local server) an AI reply averaged 4.0 seconds (`docs/convo-eval.md`), with low effort and prompt caching (`app/agent.py`). WhatsApp shows the read receipt and the typing indicator meanwhile. Home cards, Learn and the game need no AI.

**15. What's actually new here vs. a chatbot on an FAQ?**
Three things:
- It answers on *your* transactions, with every figure checked by code and the check shown to you.
- It writes first at the right moment: rent tomorrow, then what's really safe to spend (`app/rules.py`).
- It's a squirrel with 16 poses chosen from what the answer did (the tools it used, a payment ready, a lesson), not from keywords (`app/mood.py`), and it talks (ElevenLabs, `app/tts.py`).

S-mobiili today has a virtual assistant, but no spending-by-category view and no mascot.

## Do not claim
- That the guard "guarantees" or "blocks" wrong numbers. It rewrites once and shows the verdict.
- A "Viewing as" menu. It's gone: switch customers with `?as=` or Minä → "Switch customer".
- That WhatsApp voice notes use ElevenLabs. They use Whisper on Workers AI (`app/voice.py`). ElevenLabs is the web voice and mic.
- Finnish in the number check. "How I worked this out" is English only (`app/static/trace.js`).
- A "✅ Rent paid" message or a basket-game chip. The reply is "*Rent paid*: …", and the game starts when you type *basket game*.
- Any statement about the real S-mobiili without checking it on a real phone first (checklist).
