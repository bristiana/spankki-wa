# Demo script: 3 min 45 s, in English

Follows the demo path in the judging audit. There are two screens:
- the stage phone with WhatsApp, mirrored to the projector
- a second device showing S-mobiili at 360 px

Roles:
- **P** is the presenter, who speaks.
- **O** is the operator, who taps the /demo panel off-screen.

Figures come from the engine on the demo date, 6 Oct 2026 (`wrangler.jsonc` `DEMO_TODAY`). Read each number off the screen, never from this page.

## Open decisions (do not settle them on stage)
- **Hero customer: Antti, 38, or Aino, 24.**
  - Antti is the default (`data/personas/index.json`, `DEFAULT_PERSONA` in `app/main.py`).
  - With Antti, the WhatsApp reminder says rent is due **in 28 days** (1 065,17 €, 3 Nov). With Aino it says **tomorrow** (771,82 €, 7 Oct), because `reminder_text` takes the next Housing bill.
  - Rahasi ("Your money") is built only for Antti (`MONEY_PERSONA` in `app/main.py`).
  - Making Aino the WhatsApp hero needs `DEMO_PERSONA=CUST04831` on the container. The Worker does not pass it today (`worker/index.ts` `SECRET_KEYS`).
- **The Kurre video** (`docs/demo/Kurre-demo.mp4`) still shows "Kurre" and old pages. Don't use it as the backup.
- **The Minä profile name** doesn't match "Hei Antti". Don't open Minä on stage until this is decided.

The lines below are written for Antti.

## Script

| Time | Screen | Exact tap or question | What P says |
|---|---|---|---|
| 0:00–0:15 | Phone, WhatsApp chat with Oiva | none | "S-Pankki doesn't need another app. It needs a friend in WhatsApp. In a banking app you're a customer. In WhatsApp you ask a friend, without embarrassment, 'I'm out of money, what do I do?'" (`docs/core-idea.md`) |
| 0:15–0:30 | Phone | O taps **Rent reminder + pay** on `/demo?key=…` (`app/templates/demo.html`). Oiva's message arrives: "your rent of … is due in … days … safe to spend until payday …" | "Oiva writes first: rent is coming up, and this is what's really safe to spend until payday, not just my balance." |
| 0:30–1:05 | Phone (the WhatsApp in-app browser) | Tap **Pay rent now** → **Open in S-mobiili** → tap the **Ready for you** card ("Check and confirm") → **Confirm with Face ID** (simulated) → **Back to WhatsApp**. Oiva writes: "**Rent paid**: …, balance now …" | "Oiva prepared it. I checked it and confirmed it. The AI never moves money: every payment goes through the bank's own confirm step." |
| 1:05–1:30 | Phone | Type **Where does my money go?** | "Three baskets, the challenge's own model. Bills & basics run on autopilot. Occasional costs need a buffer. Only Fun & extras is what I can change today." |
| 1:30–1:35 | S-mobiili, Koti | Tap **FI \| EN** to FI, then back to EN, before opening the chat (each tap reloads the page) | "Finnish first, English for you today." |
| 1:35–2:10 | S-mobiili Koti → chat | Tap the **Ask Oiva** row → in the chat type **What if I ate out 20% less and invested it?** | "This is the yearly saving from my own restaurant payments, and what it would have grown to at real ETF prices over the last two years. It's information, not advice: I choose." |
| 2:10–2:35 | Same answer | Tap **How I worked this out** under the answer | "Every number comes from a tested engine. The model may only quote tool results, and a number guard checks each figure against them. Here it says N of N figures checked against the calculations." |
| 2:35–3:05 | Same chat | Type **basket game** → answer one round with a basket button | "My own payment: which basket is it? It teaches by doing, with my own money, and it doesn't use the AI at all." |
| 3:05–3:35 | S-mobiili | Back to Koti → tap **Pankki** → tap the **Your money** (Rahasi) tile | "The same Oiva, inside S-mobiili: one backend, two front doors. S-mobiili has a virtual assistant, but no spending-by-category view and no mascot. Oiva fills that gap." |
| 3:35–3:45 | WhatsApp | none | "Oiva: a friend in WhatsApp, with a bank underneath. Thank you." |

Why this order: "How I worked this out" exists only in the web chat (`app/static/trace.js`, loaded by `app/templates/chat.html`). So the what-if and the number check run in S-mobiili. The basket game starts by typing (`quiz.START` in `app/quiz.py`). There is no chip for it in the chat.

## If something fails

| Failure | Do this | Line to say |
|---|---|---|
| The WhatsApp message doesn't arrive within 10 s | Go to the S-mobiili chat and type the same questions. It's the same coach (`docs/integration.md`) | "Same Oiva, same backend: here it is inside the app." |
| Meta rejects the reminder because the 24 h window is closed | Prevent it in warm-up: write "hi" to the bot within 24 h of stage time (`docs/aprobare-whatsapp.md` §4). On stage, switch to the web chat | as above |
| An AI reply takes more than 10 s | Keep talking. After 20 s, type **basket game**, which needs no AI (`app/quiz.py`) | "While Oiva thinks: this part runs without the AI." |
| No sound, or the ElevenLabs key is out | Carry on. The page falls back to the browser voice (`app/tts.py`), or use the text only | "Oiva also talks; today we'll read." |
| The mic fails | Type the question. The mic is optional (`#mic` in `app/templates/chat.html`) | none |
| The venue network is down | Switch to the phone hotspot and the live site. If that also fails, use the screenshots from warm-up for WhatsApp and the local server for the rest (checklist §3) | "WhatsApp needs Meta's servers; here's the same flow in the app." |
| Everything is down | Play the backup recording made in warm-up (checklist §5) | none |

Don't type **STOP** on stage. Its reply says "Your bank data never left S-Pankki", which contradicts `/privacy`: the numbers go to Anthropic and through Meta (`app/main.py` `switch_off`, `PRIVACY`).
