# Demo script: 3 min 40 s, in English

Follows the demo path in the judging audit. Two screens: a phone with WhatsApp (mirrored), and a laptop or second phone with S-mobiili at 360 px.

- **P** = presenter (speaks)
- **O** = operator (taps the /demo panel off-screen)

Expected figures come from the engine on the demo date, 6 Oct 2026 (`wrangler.jsonc` `DEMO_TODAY`). Read the number off the screen, never from this page: the engine is the source.

## Open decisions (do not settle them on stage)
- **Hero customer:** Antti, 38 (the default, `data/personas/index.json`) or Aino, 24.
  - Rahasi ("Your money") is built only for Antti: `MONEY_PERSONA` in `app/main.py`, from `app/static/mockups/charts/data/spend.json`.
  - With Aino, step 6 ends on the Koti cards instead.
- **The Kurre video** (`docs/demo/Kurre-demo.mp4`) still shows "Kurre" and old pages. Do not play it as the backup until it is re-recorded or dropped.
- **The Minä profile name:** it does not match "Hei Antti". Avoid opening Minä on stage until this is decided.

## Script

| Time | Screen | Exact tap or question | What P says |
|---|---|---|---|
| 0:00–0:15 | Phone, WhatsApp chat with Oiva | none | "S-Pankki doesn't need another app. It needs a friend in WhatsApp. In a banking app you're a customer. In WhatsApp you ask a friend, without embarrassment, 'I'm out of money, what do I do?'" (`docs/core-idea.md`) |
| 0:15–0:30 | Phone | O taps **Rent reminder + pay** on `/demo?key=…`. Oiva's message arrives: "your rent of … is due tomorrow … safe to spend until payday …" | "Oiva writes first, at the right moment: rent is due tomorrow, and this is what is really safe to spend, not just my balance." |
| 0:30–0:55 | Phone, then S-mobiili | Tap **Pay rent now** → tap **Open in S-mobiili** → the payment opens filled in → tap **Confirm with Face ID** (simulated) → back in WhatsApp: "✅ Rent paid" | "Oiva prepared it. I confirmed it. The AI never moves money: every payment goes through the bank's own confirm step." |
| 0:55–1:25 | Phone | Type **Where does my money go?** | "Three baskets, the challenge's model: Bills & basics run on autopilot, Occasional costs need a buffer, and only Fun & extras is what I can change today." |
| 1:25–1:35 | S-mobiili | Tap the **FI \| EN** switch once, then back to EN | "Finnish first, English for you today." |
| 1:35–2:10 | S-mobiili, Oiva chat | Type **What if I ate out 20% less?** | "That's the yearly figure, checked against real ETF prices over the last two years. Information, not advice: I choose." |
| 2:10–2:35 | S-mobiili, same answer | Tap **How I worked this out** under the answer | "Every number comes from a tested engine. The model may only quote tool results, and a number guard checks each figure. Here: N of N figures checked." |
| 2:35–3:05 | S-mobiili chat | Tap the **basket game** chip → answer one round with a basket button | "My own payment: which basket? It teaches by doing, on my own money." |
| 3:05–3:30 | S-mobiili Koti, then Pankki → Rahasi | Show the Oiva entry on Koti → tap **Pankki** → tap the **Rahasi** tile | "The same Oiva, inside S-mobiili: one backend, two front doors. S-mobiili has a virtual assistant, but no spending-by-category view and no mascot. Oiva fills that gap." |
| 3:30–3:40 | Back to WhatsApp | none | "Oiva: a friend in WhatsApp, a bank underneath. Thank you." |

Why the order: the number check ("How I worked this out") exists only in the web chat (`app/static/trace.js`). That's why the what-if and the check run in S-mobiili, not in WhatsApp.

## If something fails

| Failure | Do this | Line to say |
|---|---|---|
| The WhatsApp message doesn't arrive in 10 s | Skip to the S-mobiili chat. Type the same questions there; it runs on the same coach (`docs/integration.md`) | "Same Oiva, same backend: here it is inside the app." |
| Meta rejects the reminder (the 24 h window is closed) | Avoid it in warm-up: write "hi" to the bot within 24 h of stage time (`docs/aprobare-whatsapp.md` §4). On stage, use the web chat | as above |
| An AI reply takes more than 10 s | Keep talking over the pitch line. If it takes over 20 s, tap the basket game chip, which needs no AI (`app/quiz.py`) | "While Oiva thinks: this part runs without the AI at all." |
| Voice out (no sound, no ElevenLabs key) | Carry on muted: the page falls back to the browser voice (`app/tts.py`), or show text only | "Oiva also talks; today we'll read." |
| Mic fails | Type the question. The mic is optional (`app/templates/chat.html` `#mic`) | none |
| Venue network down | Phone hotspot → live site. If that also fails, use the local server on the laptop (checklist §3) with the web chat. WhatsApp can't run offline: show the screenshot from warm-up | "WhatsApp needs Meta's servers; here is the same flow in the app." |
| Everything down | Play the backup recording made during warm-up (checklist §5) | none |
