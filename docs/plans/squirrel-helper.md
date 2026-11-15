# Plan: Oiva as a squirrel helper that reacts to the conversation

Status: planned 15 Nov 2026, built on branch `squirrel-helper`. Since 15 Nov the Helper is the only chat: the
Chat | Helper | Coin switch, the plain chat view and the coin-squirrel Helper are gone (§6), and every S-mobiili
screen has an "Ask Oiva" row that opens it with a question (§9).

## What we want

Oiva's chat in the S-mobiili mock: a big squirrel that acts out what the conversation is about,
says its answer in a speech bubble (and, if you turn the sound on, out loud), with most of the screen still left for
the conversation. Its most important change: when you compare yourself with other people, it switches to its
*compare* pose. It is the chat: opening Oiva from S-mobiili always gives the Helper.

## 1. The existing chat

- Page: `GET /chat` → `app/templates/chat.html` (S-mobiili's "Viestit" screen; `?embed=1` drops the phone frame,
  FI/EN from `ui_lang`). The S-mobiili mock's chat bubble and "Ask Oiva" card link to it.
- Flow: `send()` in the page posts `{session, text, as, mirror, shown}` to `POST /api/chat` → `chat_turn()` in
  `app/main.py`. Fixed flows answer without AI (`__remind__`, `__alert__`, buttons, onboarding interview, basket game,
  Learn course); everything else goes to `web_answer()` → `ask()` → `Coach.ask()` in `app/agent.py`: Claude with the
  engine's tools on the persona's real data (Antti, `CUST01656`). The JSON back is
  `{text, bubbles[], tools[], trace[], buttons[], charts[], quick[]}`; `web_words()` strips emoji and the reaction.
- The page draws one bubble per part, then charts, PIN/Face ID buttons (open a sheet), quick replies and the
  "How I worked this out" trace. (Before the Helper, a 46 px sprite Oiva in the header acted this out; it is gone.)

## 2. The mascot art

Two squirrels exist:

| Art | Where | States |
|---|---|---|
| Coin squirrel sprite | `app/static/oiva/oiva.webp` (+ `assets/oiva/spritesheet-extended.png`) | idle, wave, happy, worried, talk, busy, think, walk, 16 look directions. No comparing pose. |
| Vault-keeper squirrel "Oiva Orava" | branch `design/mascot`, `design/mascot/oiva.js` (+ PNG/SVG stills) | **16 states**: idle, listening, thinking, working, teaching, checking, needs_approval, success, error, learned, saving, spent, safe_to_spend, caution, what_if, **compare** |

The helper uses the **mascot set's `oiva.js`** (red fur): it is the one with a state for each moment of the
conversation, including a dedicated *compare* pose (bar chart and magnifier), and it is animated, vector and
dependency-free (63 KB), so it stays sharp at 150+ px. Only `oiva.js` is copied into the app
(`app/static/oiva/mascot/oiva.js`); the 35 MB of stills stay out of the deploy. No new character is drawn.
The coin squirrel stays on S-mobiili (the guide and Your money), not in the chat.

## 3. How a state is chosen

**The server tags each reply with a `mood`** (`app/mood.py`, `mood_for()`), from what the reply actually did:
the tools Claude called, the result of `can_i_afford`, the buttons it made, and which fixed flow answered.
The page only adds the moments between replies (listening while you type, thinking while it waits).

Why not keyword matching in the page: "compare", "vertailu", "muihin", "people like me", "normal for my area",
"keskiverto" … a word list misses most phrasings and fires on wrong ones ("compare my rent with last month" is not
a peer comparison). The tools are the ground truth: a reply that compared you with other people called
`get_people_like_me`, `get_spender_profile` or `get_home_readiness`, whatever words you used, in any language.
Why not a new `[mood]` tag in Claude's output: it would change the prompt that WhatsApp shares, cost tokens, and
could drift from what the answer did; the tool list is already there, free, and testable without the AI.

| Signal (first match wins) | State | Speech-bubble tag (EN / FI) |
|---|---|---|
| The AI or the connection failed | `error` | Oops / Hups |
| A PIN / Face ID button (payment, saving, investing, pause): an action waiting for your OK comes first | `needs_approval` | Your OK is needed / Tarvitaan hyväksyntäsi |
| **Peer comparison**: `get_people_like_me`, `get_spender_profile` | **`compare`** | Compared with people like you / Verrattuna kaltaisiisi |
| `can_i_afford` and it does not fit safe to spend | `caution` | Careful / Varovasti |
| `can_i_afford` and it fits | `safe_to_spend` | Safe to spend / Turvallisesti käytettävissä |
| Rent reminder (`__remind__`) / bill alert (`__alert__`) | `checking` / `caution` | Checked for you / Tarkistin · Careful / Varovasti |
| `simulate_cut` | `what_if` | What if… / Entä jos… |
| `get_safe_to_spend` | `safe_to_spend` | Safe to spend / Turvallisesti käytettävissä |
| `get_safe_to_save`, `get_save_more_ideas`, `create_goal`, `get_goals`, `get_home_readiness` | `saving` | Saving / Säästöön |
| `get_money_guide`, Learn lesson, basket-game question | `teaching` | Let me show you / Katso |
| Basket-game answer right / Learn course finished | `learned` | Nice! / Hienoa! |
| `get_spending`, `get_baskets`, `get_regular_payments`, `get_upcoming_bills` | `spent` | Your spending / Kulutuksesi |
| `explore_my_data`, `get_full_picture`, `get_portfolio`, `list_funds` | `working` | Your numbers / Lukusi |
| Nothing above (small talk, thanks, a plain answer) | `success` | — |

Peer comparison vs not is the key split: *"Am I spending more than people my age?"* → `compare`;
*"How much did I spend on restaurants last month?"* → `spent`. Between replies the page shows `listening` while you
type (only the pose changes; the bubble keeps the last sentence) and `thinking` while it waits. After about 12 s on
a reply state Oiva eases back to `idle` and the tag fades.

`get_home_readiness` uses mortgage applications of people with your income, but its answer is about your own
deposit and price range, so it is `saving`, not `compare` (a review found the compare tag over an answer that
mentioned nobody else).

## 4. Layout (360 px)

```
┌ header: back · 🔈 sound · Oiva · FI|EN ─┐
│ ┌────────┐  ┌─ speech bubble ───────┐  │
│ │ squirrel│ ◄ tag: Compared with …   │  │  squirrel: clamp(104px, 16dvh, 132px)
│ │ ~150 px │  one sentence of reply   │  │
│ └────────┘  └───────────────────────┘  │
│ conversation log (same bubbles, charts,│  everything left: about half the screen at 360×760
│ buttons, "How I worked this out")      │
│ [📷] [ Ask about your money… ] [Send]  │
└ S-mobiili tab bar ─────────────────────┘
```

- The squirrel sits left, the speech bubble right of it with a tail pointing at it; the bubble holds the state tag and
  one sentence of the reply (for a comparison, the sentence that compares), not a copy of the log.
- Room is a trade-off between "bigger" and "space for the conversation": with the S-mobiili frame and tab bar the log
  gets about 45-50 % of a 360×760 screen; embedded in the app (no tab bar) more than half.
- The stage shrinks to a strip when the keyboard opens (visual viewport < 560 px: 76 px squirrel, bubble 2 lines).
- Colours, theme and font are the chat's own (chat.html `:root`), light like the chat: no palette or dark mode of
  Helper's own. Caution uses base.html's light warn `#8a5a00`.
- The bubble: the tag (sentence case, green; warn for caution) and one short sentence; long ones keep the part before a
  colon or second clause. For compare, caution/safe to spend, what if and spent, two bars from the reply's own trace
  figures (you vs typical, price vs free now, now vs after, this time vs before), only when the reply states one of
  those amounts. Your bar is warn-coloured when it is the worse one. The greeting lives in the bubble (the log copy is
  only for screen readers); the chat's three dots show in the bubble while Oiva thinks.
- Oiva glances at the input while you type and at the bubble when a reply lands; animation is capped at 30 fps
  (20 at rest).
- Font: the app's `--font`; amounts keep non-breaking spaces (`nb()`).

## 5. Talking

- Speech bubble: always on (above).
- Voice: off by default; a speaker button next to Back in the top bar turns it on (remembered per browser). It reads the
  whole reply (bubbles joined, `*bold*` marks removed) with `speechSynthesis`, voice language `fi-FI` or `en-GB` by the
  page language (not guessed from the text), an exact-language voice first, then any of that language. A new message or turning it off cancels speech. If the browser
  has no `speechSynthesis`, the button is not shown. While speaking Oiva stays in the reply's state.

## 6. One chat, no switch

- `/chat` is always the Helper; `?mode=` is ignored and nothing is remembered per browser. `/chat?embed=1` (S-mobiili's
  WebView) and `/chat?q=` deep links land in it and ask straight away.
- What the plain chat had stays: the log, charts, PIN/Face ID buttons in the sheet, quick replies, bill photos and
  "How I worked this out". Only its little avatars (the coin sprite on each bubble and in the header) are gone: the big
  Oiva is the speaker.

## 7. Out of scope

- No new character art, no new poses; the coin sprite and the vault-keeper are not merged into one.
- No mood on WhatsApp (the reply's `mood` field is web-only, additive; WhatsApp payloads are unchanged).
- No speech recognition (talking *to* Oiva by voice); the existing WhatsApp voice notes are untouched.
- No change to what Oiva says or to the prompt; no new tools.
- The S-mobiili home screen and "Your money" keep their current art.
- Messages that arrive from WhatsApp in the owner's mirrored chat take `success`; only replies typed in the app carry a
  real mood.

## 8. Tests

- `tests/test_mood.py`: the mapping (peer tools → compare; spending → spent; afford fits/doesn't; approval buttons;
  error; small talk) and that `/api/chat` returns `mood` for fixed flows; the chat page is the Helper only in FI and EN.
- Screenshots at 360 px through real conversations with Claude: a peer-comparison question, a plain spending question,
  an affordability question, the embedded chat, and each S-mobiili screen's Ask Oiva row tapped.

## 9. Ask Oiva on S-mobiili

One row look everywhere (Oiva's face, "Ask Oiva" over the question, a chevron), inside a section's card or as a card of
its own, at most one per section. Each opens the Helper asking its question in the page's language (`ask()` in
`sm_ctx`, keeping `?persona=`). The row's face is the Helper's own (`mascot/head.png`, a still of oiva.js). Under
`?web=1` (S-mobiili inside the chat's confirm sheet) no rows are drawn: a chat inside the chat would be a dead end.

| Screen | Where | Asks (EN) |
|---|---|---|
| Koti | Accounts card | How much can I spend until payday? |
| Koti | 3 baskets card (was already there) | Where does my money go? |
| Koti | Savings and investments card | How much could I save a month? |
| Koti | Transactions card | What have I spent the most on lately? |
| Pankki | top | Which bills are coming up? |
| Pankki → Your money | each chart and the floating Oiva (already there) | per chart |
| Kortti | under Transactions | Where did my money go this month? |
| S-Etukortti | under the offers | How much do I spend on groceries a month? |
| S-Etukortti → Receipts | above the list | Is my grocery spending normal for my area? |
| Minä | top | What kind of spender am I? |

