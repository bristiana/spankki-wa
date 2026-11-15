# Plan: Oiva as a squirrel helper that reacts to the conversation

Status: planned 15 Nov 2026, built on branch `squirrel-helper`.

## What we want

A second way to talk to Oiva in the S-mobiili mock: a big squirrel that acts out what the conversation is about,
says its answer in a speech bubble (and, if you turn the sound on, out loud), with most of the screen still left for
the conversation. Its most important change: when you compare yourself with other people, it switches to its
*compare* pose. A switch at the top of the chat picks the mode. The current chat stays the default and is unchanged.

## 1. The existing chat

- Page: `GET /chat` → `app/templates/chat.html` (S-mobiili's "Viestit" screen; `?embed=1` drops the phone frame,
  FI/EN from `ui_lang`). The S-mobiili mock's chat bubble and "Ask Oiva" card link to it.
- Flow: `send()` in the page posts `{session, text, as, mirror, shown}` to `POST /api/chat` → `chat_turn()` in
  `app/main.py`. Fixed flows answer without AI (`__remind__`, `__alert__`, buttons, onboarding interview, basket game,
  Learn course); everything else goes to `web_answer()` → `ask()` → `Coach.ask()` in `app/agent.py`: Claude with the
  engine's tools on the persona's real data (Antti, `CUST01656`). The JSON back is
  `{text, bubbles[], tools[], trace[], buttons[], charts[], quick[]}`; `web_words()` strips emoji and the reaction.
- The page draws one bubble per part, then charts, PIN/Face ID buttons (open a sheet), quick replies and the
  "How I worked this out" trace. The 46 px sprite Oiva in the header thinks/talks/waves (`act()`).

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
The chat mode keeps the coin squirrel as it is.

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
| **Peer comparison**: `get_people_like_me`, `get_spender_profile`, `get_home_readiness` | **`compare`** | Compared with people like you / Verrattuna kaltaisiisi |
| A PIN / Face ID button (payment, saving, investing, pause) | `needs_approval` | Your OK is needed / Tarvitaan hyväksyntäsi |
| `can_i_afford` and it does not fit safe to spend | `caution` | Careful / Varovasti |
| `can_i_afford` and it fits | `safe_to_spend` | That fits / Mahtuu |
| `simulate_cut` | `what_if` | What if… / Entä jos… |
| `get_safe_to_spend` | `safe_to_spend` | Safe to spend / Turvallisesti käytettävissä |
| `get_safe_to_save`, `get_save_more_ideas`, `create_goal`, `get_goals` | `saving` | Saving / Säästöön |
| `get_money_guide`, Learn lesson, basket-game question | `teaching` | Let me show you / Katso |
| Basket-game answer right / Learn course finished | `learned` | Nice! / Hienoa! |
| `get_spending`, `get_baskets`, `get_regular_payments`, `get_upcoming_bills` | `spent` | Your spending / Kulutuksesi |
| `explore_my_data`, `get_full_picture`, `get_portfolio`, `list_funds` | `working` | Your numbers / Lukusi |
| Nothing above (small talk, thanks, a plain answer) | `success` | — |

Peer comparison vs not is the key split: *"Am I spending more than people my age?"* → `compare`;
*"How much did I spend on restaurants last month?"* → `spent`. Between replies the page shows `listening` while you
type and `thinking` while it waits. After about 12 s on a reply state Oiva eases back to `idle`.

## 4. Layout (360 px, light and dark)

```
┌ header: back · Oiva · FI|EN ───────────┐
│ [ Chat | Helper ]  switch      [ 🔈 ]  │  ~40 px
│ ┌────────┐  ┌─ speech bubble ───────┐  │
│ │ squirrel│ ◄ tag: Compared with …   │  │  stage: clamp(150px, 26dvh, 210px)
│ │ ~140 px │  first line of the reply │  │
│ └────────┘  └───────────────────────┘  │
│ conversation log (same bubbles, charts,│  everything left: ~55-60 % of the screen
│ buttons, "How I worked this out")      │
│ [📷] [ Ask about your money… ] [Send]  │
└ S-mobiili tab bar ─────────────────────┘
```

- The squirrel sits left, the speech bubble right of it with a tail pointing at it; the bubble holds the state tag and
  the reply's first bubble (cut at ~160 characters with "…"; the full reply is in the log below).
- The stage shrinks when the keyboard opens (visual viewport < 560 px: 110 px squirrel, bubble 3 lines) so the log
  keeps room.
- Light uses the chat's S-mobiili tokens. Dark (`prefers-color-scheme: dark`) is scoped to helper mode so chat mode
  stays exactly as it is: dark green-black surfaces from `base.html`'s dark tokens, a soft spotlight behind Oiva.
- Font: the app's `--font`; amounts keep non-breaking spaces (`nb()`).

## 5. Talking

- Speech bubble: always on (above).
- Voice: off by default; a speaker button next to the switch turns it on (remembered per browser). It reads the
  whole reply (bubbles joined, `*bold*` marks removed) with `speechSynthesis`, voice language `fi-FI` or `en-GB` by the
  page language, picking a matching installed voice. A new message or turning it off cancels speech. If the browser
  has no `speechSynthesis`, the button is not shown. While speaking Oiva stays in the reply's state.

## 6. The switch

- A two-option segmented switch under the header: **Chat** · **Helper** (FI: *Chat* · *Apuri*), `aria-pressed`.
- Default: Chat. The choice is kept per browser (`localStorage`, guarded) and in the address (`?mode=helper`,
  works for deep links and the demo). Switching keeps the conversation (same log, same session); it only shows or
  hides the stage and changes the bubble spacing.
- Chat mode is the page as it was: same markup, same `send()`; the only visible addition is the switch row.

## 7. Out of scope

- No new character art, no new poses; the coin sprite and the vault-keeper are not merged into one.
- No mood on WhatsApp (the reply's `mood` field is web-only, additive; WhatsApp payloads are unchanged).
- No speech recognition (talking *to* Oiva by voice); the existing WhatsApp voice notes are untouched.
- No change to what Oiva says or to the prompt; no new tools.
- The S-mobiili home screen and "Your money" keep their current art.

## 8. Tests

- `tests/test_mood.py`: the mapping (peer tools → compare; spending → spent; afford fits/doesn't; approval buttons;
  error; small talk) and that `/api/chat` returns `mood` for fixed flows; chat page renders the switch in FI and EN.
- Screenshots at 360 px through real conversations with Claude: a peer-comparison question, a plain spending question,
  an affordability question, light and dark, the switch, and chat mode still working.
