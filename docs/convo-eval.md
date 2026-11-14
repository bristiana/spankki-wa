# Conversation-quality pass

Oiva on WhatsApp felt poor in real use. The obvious bugs (confused answers taken as skips, double replies, a broken
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
| re-introductions in the middle of a task ("Hi Aino, I'm Oiva") | 2 | 0 |
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

> Hey Aino, I'm Oiva 🐿️ That sounds stressful. Here's the good news: [...]

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
- **"save 50 for my trip" with no trip goal** still takes two turns: Oiva asks for the total and "ok" does not move
  it on. A quick-reply with suggested totals, or a goal with no target yet, would fix it.
- **"and the month before?"** after an answer that already gave both months repeats the earlier month instead of
  going one month further back.
- **Pedagogy is the lowest criterion (3.41)**: questions about identity, banks or subscriptions have no natural basket
  moment, and the rubric scores those low. Follow-up pass below.
- One reply in the re-test said "4% of that", a figure no tool gave; the guard let small percentages through. The
  guard now checks them; the spot check after that change (6 turns) had no ungrounded number.
- Scores are one careful human read, not a panel; transcripts and per-reply notes are committed so they can be
  re-read.

## Pedagogy pass: why the numbers matter

Pedagogy (3.41) was the weakest criterion. Replies gave the right numbers but seldom said what they mean. Two changes:

1. **A "why it matters" line.** `app/lessons.py` has `lesson_for(tool, result)`, a deterministic function that
   returns one teaching line built from the tool's own figures. `run_tool` adds it to the result as `"lesson"`, for
   example: *"Small amounts × 365: 3.19 € a day is 97.18 € a month and 1 166.16 € a year"*, *"Your balance is not
   your budget: of 1 001.94 €, 68.60 € is free…"*, *"Subscriptions are basket 3 on autopilot: 4 small charges add up
   to 858.55 € a year"*. Any figure the line derives (the per-day amount, a share in %) goes into the result too,
   so the number guard accepts it. The prompt gets one short rule: when a reply gives the person's numbers, add at
   most one such line, tied to a basket or a habit, never the same idea twice in a conversation, never preachy, and
   none for small talk.
2. **Learn in 2 minutes** (`app/learn.py`, no AI): five 30-second lessons on the person's own engine numbers, with
   reply buttons that work on WhatsApp and the web: the three baskets, safe to spend vs. the balance, the buffer
   (with the money guide's S-Pankki source), the top "slips through" place × 12, and pay yourself first over a
   labelled 10-year scenario. Each lesson's action reuses an existing flow: the basket game, My money, a buffer or
   pay-yourself-first goal with the PIN savings flow, or the coach.

**Re-run:** only the pedagogy subset (`--only pedagogy`, 20 AI turns, `docs/convo-eval/pedagogy.json`, scores
in `docs/convo-eval/scores-pedagogy.json`). It has 11 earlier chats (taxi, safe_week, typo_safe, afford,
subs_then_all) and 6 new scenarios: the course with a question in the middle of a lesson, "why does that matter?",
"do I have an emergency fund?", "what does pay yourself first mean", "is a 5 € coffee a day a big deal", and three
questions in a row to check that the line is not repeated.

| criterion | before (all 51) | before (same 11) | after (same 11) | after (9 new) | after (all 20) |
|---|---|---|---|---|---|
| correct | 4.88 | 4.91 | 4.91 | 4.89 | 4.90 |
| answers | 4.67 | 4.73 | 4.73 | 4.89 | 4.80 |
| clarity | 4.59 | 4.73 | 4.91 | 4.56 | 4.75 |
| phone fit | 3.90 | 4.00 | 4.00 | 3.11 | 3.60 |
| tone | 4.94 | 5.00 | 5.00 | 4.89 | 4.95 |
| **pedagogy** | **3.41** | **3.36** | **3.91** | **4.67** | **4.25** |
| next step | 3.90 | 3.91 | 3.91 | 4.11 | 4.00 |
| overall | 4.33 | 4.38 | 4.48 | 4.44 | 4.46 |

Average words: 59.4 → 64.1 on the same 11 chats. The 9 new replies average 97.7 words, because they are explaining
questions. No reply ended with two questions and none went over 120 words. The guard checked every figure; one reply
(`coffee#0`) needed its one rewrite.

**Before and after, the same question:**

`typo_safe#0` "wat is safe to spnd". Before (pedagogy 2): *"• Your account balance is the limit this time. Your
usual pay cycle would leave 288.82 € for flexible spending."* After (5):

> You can safely spend *68.60 €* until payday on Thu 15 Oct, in 9 days. [...]
>
> Your balance isn't your budget: of the 1 001.94 € in your account, only 68.60 € is free to spend.

`afford#1` "and if its 300". Before (4): *"It would still fit your usual monthly budget for shopping and fun, with
495.92 € left after it."* After (5):

> Over a whole month it still fits, but it would take *37%* of your usual 809.91 € for wants. That would leave
> 495.92 € for everything else in that basket.

**Still weak after this pass**

- **Explaining replies run long.** Phone fit dropped to 3.11 on the new scenarios (`coffee#0` 117 words,
  `no_repeat#0` 112, `buffer#0` 105). The why-line adds about 15 words. A tighter word cap for concept answers is
  the next step.
- `pay_first#0` explains the idea well but uses none of her numbers. The Learn lesson 5 does.
- `learn_course#3` guessed what happens without a buffer ("out of next month's rent money or on credit").
- The same 11 chats give the fairest comparison (3.36 → 3.91). The 9 new scenarios have no "before" because they
  were written for this pass. As before, the scores are one careful human read.

## Instinct pass: like messaging a friend on WhatsApp

The goal: "copy the Instinct type of messaging; our scope is to make people finish learning, but like messaging with
a friend on WhatsApp". Moni, the reference, reacted to messages and had guardrails. Replies above were correct and
kind, but they still read like small reports: three bubbles, bullets, five figures, an offer tacked onto a plain fact.

**What changed**

| # | Change | Where |
|---|---|---|
| 1 | Prompt in XML sections (`role`, `never`, `style`, `how_you_help`, `examples` with 7 short chats): 1-2 bubbles split by a `---` line, 1-2 sentences each, the answer first; a quick human reaction first when they share news or a worry; one question at most, never a menu; a quick fact gets the number and stops; at most two numbers unless asked; mirror their tone; never mention tools; a bare "ok" after a small offer means yes. Kept: numbers only from tools, inform never advise, the why-it-matters line (now used sparingly), their language | `app/agent.py` |
| 2 | Reactions: the model opens with `[react]<emoji>`, picked for that message (🏠 rent, 🫂 stress, 🚕 taxis, 🙏 thanks...). It is taken out of the text and sent as a real WhatsApp reaction on their message, paced so about 2 in 3 replies carry one, never the same twice in a row; "thx" gets only the reaction. The regex reaction is just the fallback. The web chat shows a badge on their bubble | `app/reactions.py`, `app/main.py`, `app/templates/chat.html` |
| 3 | Bubbles split on `---` first, then on blank lines as before, on WhatsApp and in the web chat (one bubble per part) | `app/whatsapp.py`, `app/main.py` |
| 4 | Follow-ups that make people finish: `OPEN_THREAD` comes back once, the day after, inside the 24-hour window, with one button: the Learn course mid-way, the interview or the basket game paused, a goal with nothing in it, a PIN link not confirmed, a "maybe later". Learn shows "2 of 5 done 🌰" and celebrates the end | `app/rules.py`, `app/learn.py`, `app/main.py` |
| 5 | Guardrails in code: PINs, codes, CVVs and passwords are redacted and get "please delete that message" plus the scam sign; tool text that poses as instructions is defanged; a reply that claims it moved money, pushes with urgency or promises returns gets one rewrite; fraud, disputes and debt add a hand-off to S-Pankki customer service in S-mobiili; "/new" gives "Hi! Ask me anything about your money." | `app/guardrails.py`, `app/agent.py`, `app/main.py` |
| 6 | "hello" at the goal question is a hello, not a goal ("Saving for hello"): small talk gets a warm re-ask | `app/onboarding.py` |

**Re-run.** 17 scenarios, 30 AI turns (`--only instinct`, `docs/convo-eval/instinct-after.json`). The "before" is
the same scenarios from the latest stored transcripts (`--compose instinct-before`: the pedagogy run where it has the
scenario, else the conversation-quality "after" run), so no extra model calls; three scenarios are new (news, fraud,
a PIN in the message) and have no before. For "before", the reaction is the instant regex one WhatsApp got; the web
chat showed none. Scores: `scores-instinct-before.json`, `scores-instinct-after.json`; the seven older criteria for
"before" are the stored scores, the five Instinct criteria a new human read of the same replies.

| criterion | before (26) | after (30) |
|---|---|---|
| correct | 4.96 | 4.93 |
| answers | 4.50 | 4.70 |
| clarity | 4.65 | 4.90 |
| phone fit | 3.96 | 4.50 |
| tone | 4.96 | 4.97 |
| pedagogy | 3.50 | 3.33 |
| next step | 3.88 | 3.93 |
| overall (7) | 4.34 | 4.47 |
| **reacted fittingly** | 2.96 | 3.87 |
| **bubble count and length** | 3.04 | 4.57 |
| **mirrors their tone** | 3.54 | 4.20 |
| **one question max** | 3.88 | 4.97 |
| **friend-like, not a report** | 3.04 | 4.53 |
| **Instinct overall (5)** | **3.29** | **4.43** |

| automatic check (AI replies) | before | after |
|---|---|---|
| average words | 62.1 | 37.8 |
| average bubbles | 2.54 | 1.80 |
| replies in 1-2 bubbles | 35% | 93% |
| average longest bubble (chars) | 181 | 124 |
| replies with more than 2 figures | 12 | 6 |
| replies with bullets | 11 | 1 |
| replies with 2+ questions | 3 | 0 |
| replies with a reaction | 54% | 67% |
| same reaction twice in a row (within a chat) | 3 | 0 |

**Three chats as WhatsApp shows them** (a reaction sits on Aino's message; each line is its own bubble)

"im broke and stressed" (`stressed#0`)

Before (no reaction, 3 bubbles, 77 words):

> **Aino:** im broke and stressed
>
> **Oiva:** That sounds stressful, Aino. Here's what I can see: your rent of *771.82 €* due tomorrow is covered, and so are your usual groceries, transport and a small buffer for surprises.
>
> **Oiva:** After all that, *68.60 €* is safe to spend until payday, which is in 9 days. That's about *7.62 €* a day. Payday is Thu 15 Oct, when about 2 313.39 € is expected.
>
> **Oiva:** Want me to check your subscriptions for anything you no longer use?

After:

> **Aino:** im broke and stressed  ‹🫂›
>
> **Oiva:** That's a heavy feeling, and I'm here with you. Tomorrow's rent is covered, groceries are covered too, and you still have *68.60 €* free until payday in 9 days.
>
> **Oiva:** That works out to about 7.62 € a day. Want me to look at where a bit more room could come from?

"wat is safe to spnd", then "thx" (`typo_safe`)

Before (no reaction, then 🙏):

> **Aino:** wat is safe to spnd
>
> **Oiva:** You can safely spend *68.60 €* until payday on Thu 15 Oct, in 9 days. That works out to about 7.62 € a day, or 53.36 € this week.
>
> **Oiva:** Rent of 771.82 € goes out tomorrow. Your usual bills, food, transport and a small buffer for surprises are already set aside.
>
> **Oiva:** Your balance isn't your budget: of the 1 001.94 € in your account, only 68.60 € is free to spend. Want me to check a purchase against it?
>
> **Aino:** thx  ‹🙏›
>
> **Oiva:** You're welcome, Aino! I'm here whenever you need me. 🐿️

After:

> **Aino:** wat is safe to spnd  ‹👀›
>
> **Oiva:** You've got *68.60 €* free until payday on Thu 15 Oct, so about 7.62 € a day.
>
> **Oiva:** Rent (771.82 €) goes out tomorrow and is already set aside, along with your usual groceries and a small buffer for surprises.
>
> **Aino:** thx  ‹🙏›
>
> *(no message: the reaction is the reply)*

"How much did I spend on taxis last month?", then "and the month before?" (`taxi`)

Before (👀 on both):

> **Aino:** How much did I spend on taxis last month?  ‹👀›
>
> **Oiva:** In September you spent *57.73 €* on taxis, all on one ride.
>
> **Oiva:** • In August you spent nothing on taxis.  
> • Parking was your biggest transport cost last month, at 112.56 €.
>
> **Oiva:** A one-off taxi can feel small, but if rides become a habit they add up fast. Want me to chart your transport spending over the past 12 months?
>
> **Aino:** and the month before?  ‹👀›
>
> **Oiva:** In August you spent *0.00 €* on taxis, so September's 57.73 € ride was the only one in those two months.
>
> **Oiva:** Want me to check further back, for example the last 12 months?

After:

> **Aino:** How much did I spend on taxis last month?  ‹🚕›
>
> **Oiva:** You spent *57.73 €* on taxis in September, all on one ride.
>
> **Aino:** and the month before?  ‹👀›
>
> **Oiva:** Nothing in August, *0 €* on taxis.

New in this pass, no before: "got a raise today!! 🎉" gets ‹💰› and *"Whoa, congrats Aino! That's well deserved 🥳"*,
then one offer to put part of it aside; "someone used my card, I didn't make this payment" gets ‹😬›, *"freeze your
card in S-mobiili"* and *"contact S-Pankki customer service in S-mobiili"*, with no phone number; "my pin is 4821, why
is my safe to spend so low?" gets ‹🔒›, the delete-it and scam-sign bubbles, and the answer without the PIN ever
reaching the AI.

**Still weak after this pass**

- **Pedagogy dipped (3.50 → 3.33)**: by design a quick fact now gets the number and stops, so the why-line shows up
  less often (`safe_week#0` lost "your balance isn't your budget"). The Learn course and its daily follow-up carry
  more of the teaching now.
- **Three figures still slip in** on 6 of 30 replies (`safe_week#0`, `typo_safe#0`, `coffee#0`), against the "at
  most two" rule.
- **"save 50 for my trip" + "ok"** still needs a total before it can move on, as before.
- **"which fund should I buy"** still answers with three bulleted funds; fair for the question, but not chatty.
- `stressed#1` suggests pausing a subscription ("totally your call"), a slight lean the old reply also had.
- The secret path shows four bubbles (two from the guardrail, two from the answer).
- Reactions are paced at random, so one run's 67% will move a little between runs; repeats inside a chat are
  blocked in code.
- Scores are one careful human read, as before; transcripts and per-reply notes are committed.

## Re-run

```bash
WA_TOKEN= WA_PHONE_NUMBER_ID= DEMO_PHONE= PUBLIC_URL=http://127.0.0.1:8801 \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8801
python scripts/convo_eval.py --label after                 # all scenarios (about 60 AI turns)
python scripts/convo_eval.py --label after --only home,fund  # a few
python scripts/convo_eval.py --label pedagogy --only pedagogy --url http://127.0.0.1:8802  # 20 AI turns
python scripts/convo_eval.py --report before after pedagogy
# the Instinct pass: about 30 AI turns, then its "before" from the stored transcripts (no model calls)
python scripts/convo_eval.py --label instinct-after --only instinct --url http://127.0.0.1:8803 --max-ai 30
python scripts/convo_eval.py --compose instinct-before
python scripts/convo_eval.py --report instinct-before instinct-after
```
