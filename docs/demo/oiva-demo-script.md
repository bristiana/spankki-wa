# Oiva demo script (about 2 minutes)

The spec's demo voice flow ("how am I doing" → "is that a lot for someone like me" → "set my restaurant budget to
what's typical" → a teaching question), shown in the S-mobiili concept on a phone. Spec:
`docs/superpowers/specs/2026-11-14-oiva-wealth-and-voice-design.md` (Tonight, The demo voice flow).

**Before going on stage**

- Phone page: `/s-mobiili/?persona=CUST01656` (Antti, 38, Helsinki). Restart the server with a fresh
  `data/state.json` and `data/state/` so the rent is unpaid and no budgets are set.
- Turn the on-screen guide off once (tap Skip), so it doesn't talk over the presenter.
- Until Christiana's `voice.js` lands, the call is the scripted demo playback, labelled "Demo playback · voice not
  connected". Say so out loud; don't pretend it is live. With `voice.js`, the same call is live and the lines below are
  what the presenter says to Oiva.
- Backup: the recorded video of the voice flow (Christiana, Sat 01:00–02:00) and the text chat.

The criteria in brackets are the five the jury scores, 20 % each: **[Lit]** financial literacy, **[AI]** originality
of the AI, **[UX]** clarity of the mobile UX, **[Feas]** feasibility, **[Int]** S-mobiili integration.

| Time | On the phone | Presenter says |
|---|---|---|
| 0:00–0:15 | S-mobiili home: Accounts, then "Where your money goes" with the three baskets. | "This is S-mobiili as Antti knows it: same tabs, same cards. We added one thing: Oiva, a coach that knows his own money. Here are his three baskets; only the last one, what slips through your fingers, can change today." **[Int] [Lit]** |
| 0:15–0:20 | Scroll to "Your money with Oiva", tap **Talk to Oiva**. The call panel opens over the lower half; the timer shows 0:00 / 5:00 and the ElevenLabs line. | "He just talks to it." **[UX]** |
| 0:20–0:35 | Oiva: "Shall we look at October together?" You: "Where is my money going?" The screen behind switches to **Spending**. Oiva asks for a guess first; "Maybe three hundred?"; "Close! 412 euros 30." | "Oiva doesn't just read numbers out. It asks him to guess first, because a guess you check sticks." **[Lit] [AI]** |
| 0:35–0:55 | "Is that a lot for someone like me?" The screen switches to **People like me**: 761 people aged 35–44 in Helsinki. Oiva: they typically keep 7 % of what comes in; Antti spent 4 % more than came in. | "No Finnish bank lets you chat with your own data yet, and none tells you what's normal for people like you. Every number Oiva says comes from a tool call on his data, and a guard flags any number it can't back." **[AI] [Lit] [Feas]** |
| 0:55–1:10 | "Can I set a restaurant budget?" The screen switches to **Budget**; Oiva: people like you typically spend 296 euros on restaurants. Then **Bills**: rent 1 065.17 € on the 3rd. The call ends with a summary card. | "It ends with what he can do, and a written summary of the call." **[Lit] [UX]** |
| 1:10–1:25 | Close the call. On **Budget**, tap **Restaurants** → **Like people near you** (296 €). The toast says it's set, with **Undo**. Tap Undo. | "Every change is one tap to set and one tap to undo." **[UX] [Feas]** |
| 1:25–1:45 | From the chat, tap **Pay rent**. The S-mobiili payment screen: Rent 1 065.17 €, recipient, IBAN, due date. Tap **Confirm with Face ID** (simulated) → "Payment sent". | "Oiva never moves money by itself. It opens the bank's own confirm screen, and Antti confirms with Face ID, just like any payment in S-mobiili." **[Int] [Feas]** |
| 1:45–2:00 | Back on home. | "Built on S-Pankki's synthetic data for 5 100 customers: the bank's own transactions and cohorts, a voice agent and four screens that drop into S-mobiili's Bank tab. Oiva: your money, explained by someone who knows it." **[Feas] [Int]** |

## Mapping to the judging criteria

| Criterion | Where it shows |
|---|---|
| Financial literacy | Three baskets; the guess-first question; "people like me" with the median and the middle half; a budget from peers; the bills calendar. |
| Originality of the AI | Voice over your own data; the screen follows the conversation; the cohort comparison; numbers checked against tool results. |
| Clarity of the mobile UX | S-mobiili's own layout and tabs; one-tap budget with Undo; a call that leaves the screen visible above it; captions; 44 px targets; light and dark. |
| Feasibility | Synthetic S-Pankki data and cohorts; the API contracts in `tests/contracts.py`; confirmation stays in the bank's Face ID flow; sample data is labelled. |
| S-mobiili integration | Lives under the Bank tab as cards like Accounts and Säästäjä; the chat bubble top right; payments go through S-mobiili's confirm screen. |

## Dry run on a phone-size page (14 Nov, 390×844, touch)

Every beat above was walked with a script (Playwright, phone size, touch), in light mode, on the branch
`smobiili-oiva-screens`. The call plays at real speed and the screen switches at 13 s (Spending), 25 s
(People like me), 35 s (Budget) and 40 s (Bills), with the summary at 42 s; the whole path, including Budget with
Undo and paying rent with Face ID, took 49 s of clicking, so 2 minutes leaves room to talk. No page errors, no
horizontal scroll, no "undefined" or "NaN" on any screen.

Fixed after the run: the call panel covered most of the screen Oiva switches to; on phone heights Oiva's picture in
the call is now smaller, so more of the screen behind shows.

**Known gaps to say or fix before the demo**

- The screens use labelled sample data until Bogdan's `/api/spend`, `/api/budget`, `/api/compare` and `/api/recurring`
  are on main (not there on 14 Nov, 20:40).
- The call is demo playback until Christiana's `window.OivaVoice` (`app/static/oiva/voice.js`) is on main.
- The mock still says "Ask Oiva" on the chat bubble and on the baskets card; the Oiva → Oiva rename PR hasn't
  landed.
- Amounts show a decimal point (`412.30 €`); S-mobiili writes a decimal comma (`412,30 €`).
