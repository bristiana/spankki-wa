# Conversation-quality pass

Birky on WhatsApp felt poor in real use. The obvious bugs (confused answers taken as skips, double replies, a broken
button) were already fixed, so this pass looks at the conversation itself: 31 multi-turn chats, every reply read the
way WhatsApp shows it (split into bubbles by `whatsapp.bubbles`), scored, root causes fixed, then the same chats run
again.

## Method

- `scripts/convo_eval.py` runs 31 scenarios (78 messages, 51 of them answered by the AI) through `/api/chat` on a
  local server (it refuses any host but 127.0.0.1 and calls `/demo/reset` first). The 27 other messages are the
  interview, the basket game and button taps, which use no AI.
- Covered: the four challenge questions, follow-ups that need context ("and the month before?", "why?", "what about
  groceries"), confusion ("i dont understand", "what does that mean", "explain like im 5"), typos and lowercase,
  very short messages ("ok", "?", "hmm", "thx"), a money question in the middle of the interview and of the game,
  things it must not do (pay my rent, bitcoin, pick a fund), "im broke and stressed", Finnish and Swedish, ETF / ASP /
  compound interest, subscriptions and "deactivate all my subscriptions", "save 50 for my trip", first home, all my
  banks, charts, another customer through the "Viewing as" cookie (`CUST00613`), "who are you / what can you do".
- Every AI reply gets automatic phone checks: words, number of WhatsApp bubbles, the longest bubble, question marks,
  markdown WhatsApp can't show, and the number guard's verdict. A human read then scores each reply from 1 to 5 on seven
  criteria: **correct** (numbers match the tools, nothing invented), **answers** the question, **clarity** for a
  20-30-year-old, **phone** (length and bubbles: ideally 1-3 short ones), **tone** (warm, never shaming, informs
  rather than advises), **pedagogy** (three baskets used naturally) and **next step** (one, not a menu).
- Transcripts: `docs/convo-eval/before.json`, `docs/convo-eval/after.json`. Scores, with a note per reply:
  `docs/convo-eval/scores-before.json`, `docs/convo-eval/scores-after.json`. Averages:
  `python scripts/convo_eval.py --report before after`.
- Real model calls: 51 AI turns for the baseline, 51 for the re-test, and 6 for a spot check after the last guard
  change (108 in total).

## Results

| criterion | before | after |
|---|---|---|
| correct | 4.55 | 4.88 |
| answers the question | 4.43 | 4.67 |
| clarity | 3.73 | 4.59 |
| phone fit | 2.96 | 3.90 |
| tone | 4.47 | 4.94 |
| pedagogy (baskets) | 3.04 | 3.41 |
| next step | 3.49 | 3.90 |
| **overall** | **3.81** | **4.33** |

| automatic check (51 AI replies) | before | after |
|---|---|---|
| average words | 88.1 | 69.1 |
| replies over 120 words | 6 | 1 |
| average longest bubble (chars) | 263 | 207 |
| replies with a bubble over 300 chars | 15 | 8 |
| replies ending with two questions | 4 | 3* |
| re-introductions in the middle of a task ("Hi Aino, I'm Birky") | 2 | 0 |
| ISO dates in the reply ("2026-10-15") | 4 | 0 |
| markdown WhatsApp can't show | 0 | 0 |
| average seconds per AI reply | 4.4 | 4.0 |

\* After the fix, the two-question cases are a quoted example question plus an offer ("you could ask 'What's safe to
spend?' ... Want me to check?"); before, they were an answer's own question followed by the interview's question.

## Worst replies before the fix

**A side question in the interview got two competing questions and the wrong topic** (`onb_confused#3`). The
interview had just asked about prudent / balanced / growth; the coach did not know that and answered with fund
scenario percentages, then the interview asked again under it. 134 words:

> *Prudent* just means "careful". It's the most cautious of the three demo funds.
> • *Demo Fund Prudent* (risk level 2, low): puts money in the euro money market [...] The scenario growth is 2.0 % a year.
> • *Balanced* (risk level 4) [...] • *Growth* (risk level 6) [...]
> These growth figures are only scenarios, not promises. Want to know how the risk levels compare?
>
> In plain words: *prudent* means your money barely moves [...] Which feels closest to you?

**A mislabelled figure** (`bitcoin#0`): 497.84 € is the monthly *investing* amount, not spending plus investing.

> Over the last 3 months, spending plus investing (*497.84 €* a month) came to *323.62 €* more each month than you earned

**An incoherent trend** (`trend#0`): March is before June, and October had started.

> Since then every month has cost more, from 1 827.97 € in June up to the March high. October hasn't started yet, so it shows 0 €.

**A wall of numbers** (`home#0`, 165 words, 13 figures, a 414-character last bubble), and `who#1`: one 574-character
bubble with seven bullets, a menu rather than an answer.

**Re-introducing itself to a stressed person** (`stressed#0`):

> Hey Aino, I'm Birky 🐿️ That sounds stressful. Here's the good news: [...]

**Advice instead of information** (`stressed#1`): "Stick to about 7.62 € a day [...] you can lower [your investment]".

## Root causes

1. **The style rules were one line at the bottom of a long list of tool-routing rules** ("usually under 80 words").
   Each answer became a mini report: breakdowns, comparisons and fund scenarios nobody asked for. Average 88 words.
2. **Nothing on how to hold a conversation**: no rules for follow-ups, confusion, "ok" / "hmm" / "?", feelings, or
   requests it can't do. "Introduce yourself only when greeting" made every first message of a chat a greeting.
3. **The coach was blind to the interview and the game.** Their messages never reached its history, and it was not
   told that a question was waiting, so a side answer ended with its own question and the interview's came right
   after; "what does prudent mean" had no context.
4. **Bubble splitting put everything after the second paragraph into the last bubble**, and could cut a "Where it
   went:" line off from its list. Every long reply ended in a wall.
5. **Numbers without meaning.** The prompt demanded tool numbers but not that each figure keep its field's meaning,
   and the trend data gave no lowest value and no warning that the last month was still running. The guard also let
   any whole number up to 12 through, even as a percentage or an amount ("4% of that").
6. **Formatting leaks**: ISO dates straight from the tools ("next charge 2026-10-20"), "68.6 €" next to "68.60 €".
7. **"Deactivate all my subscriptions"** depended on the model calling the stop tool once per subscription.

## What changed

| # | Change | Where |
|---|---|---|
| 1 | System prompt rebuilt in sections (how you write, conversation, numbers, the three baskets, inform never advise, tools): answer first with the one number in bold, 40-70 words and never over 100, at most 3 bubbles, exactly one next step or none, dates as people say them, introduce yourself only when greeted or asked | `app/agent.py` |
| 2 | Conversation rules: follow-ups keep the topic and change only what changed; confusion re-explains the last message more simply with one comparison and nothing new; "ok" / "thx" get one line, "hmm" / "?" get one line or a question; typos read as the likely meaning; feelings get warmth, one steadying fact and one optional step; can't-do requests get one kind line and the closest thing it can do | `app/agent.py` |
| 3 | The coach knows what's waiting: `flow_context()` tells it the interview question or the paused game question that follows its reply, so it answers only the side question and ends without one. Interview and game turns go into its history (`Coach.remember`), and their engine-made numbers count as sources for the guard | `app/main.py`, `app/agent.py`, `app/quiz.py` |
| 4 | Bubbles: a line ending in ":" stays with its list; extra paragraphs are joined where the two neighbours are shortest, so length is shared out instead of piled into the last bubble. Extra URL buttons on WhatsApp say "2/4", "3/4" instead of "One more:" | `app/whatsapp.py`, `app/main.py` |
| 5 | Correctness: each figure keeps its field's meaning; trends name highest, lowest and latest only; `explore_my_data` returns `lowest` and flags a period still running; the guard now checks every percentage and euro amount, however small | `app/agent.py`, `app/engine.py`, `app/guard.py` |
| 6 | Output style in code, not just in the prompt: ISO dates become "Thu 15 Oct" (with the year if it isn't this year), one-decimal amounts become two ("68.6 €" -> "68.60 €") | `app/agent.py` (`date_style`, `euro_style`) |
| 7 | `create_stop_subscription_link` takes `name: "all"`: one stop link per active subscription in one call, each still confirmed with the PIN | `app/tools.py` |

Tests: `tests/test_conversation.py` (19 tests, fake model client, no real API) covers the bubbles, date and money
style, the prompt's rules, context reaching the system prompt, remembered flow turns as history and number sources,
stop-all, the running month in explore, the context mid-interview and mid-game through the web chat, and numbered
WhatsApp buttons. Existing fakes now accept `context`.

## Before and after

**Side question during the interview** (`onb_confused#3`)

Before: fund scenario percentages, "Want to know how the risk levels compare?" and then the interview's own question
(134 words, two questions).

After (one question, the interview's):

> *Prudent* means playing it safe: your money barely moves up or down, so it also grows slowly.
>
> Think of a squirrel burying nuts close to home. It won't find a big new stash, but it rarely loses the ones it has. [...] It suits money you may need soon, like a buffer for surprises.
>
> In plain words: *prudent* means your money barely moves [...] Which feels closest to you?

**"explain like im 5"** (`safe_week#1`), 96 words -> 56 words:

> Think of your money as a jar with *1 001.94 €* in it. Tomorrow, rent takes out the biggest handful. Then a little goes aside for food, bus rides and surprises.
>
> What's left is your "fun" money until payday in 9 days: *53.36 €* for this week, or about *7.62 €* a day.
>
> Does that help?

**"How much could I save if I reduced dining out by 20%?"** (`dining_cut#0`), 116 words with three fund scenarios ->
78 words:

> If you cut eating out by 20%, you'd save about *97.18 €* a month, or *1 166.16 €* a year.
>
> • You spend about 485.90 € a month on restaurants now (average of the last 3 full months). After the cut it would be 388.72 €.
> • Eating out sits in your *slips through your fingers* basket. That's the easiest basket to change.
>
> Want to see what 97.18 € a month could grow to if you invested it?

**"i'm broke and stressed"** (`stressed#0`), no self-introduction, no "good news":

> That sounds stressful, Aino. Here's what I can see: your rent of *771.82 €* due tomorrow is covered, and so are your usual groceries, transport and a small buffer for surprises.
>
> After all that, *68.60 €* is safe to spend until payday, which is in 9 days. [...]

## Still weak

- **Fund comparisons, ASP and the first-home answer stay long** (115-125 words): they carry three options or several
  required caveats. A future step could split them into an answer plus a "Show the details" button.
- **"save 50 for my trip" with no trip goal** still takes two turns: Birky asks for the total and "ok" does not move
  it on. A quick-reply with suggested totals, or a goal with no target yet, would fix it.
- **"and the month before?"** after an answer that already gave both months repeats the earlier month instead of
  going one month further back.
- **Pedagogy is the lowest criterion (3.41)**: questions about identity, banks or subscriptions have no natural basket
  moment, and the rubric scores those low.
- One reply in the re-test said "4% of that", a figure no tool gave; the guard let small percentages through. The
  guard now checks them; the spot check after that change (6 turns) had no ungrounded number.
- Scores are one careful human read, not a panel; transcripts and per-reply notes are committed so they can be
  re-read.

## Re-run

```bash
WA_TOKEN= WA_PHONE_NUMBER_ID= DEMO_PHONE= PUBLIC_URL=http://127.0.0.1:8801 \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8801
python scripts/convo_eval.py --label after                 # all scenarios (about 51 AI turns)
python scripts/convo_eval.py --label after --only home,fund  # a few
python scripts/convo_eval.py --report before after
```
