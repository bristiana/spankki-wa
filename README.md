# S-Pankki Chat: your money coach on WhatsApp

Junction 2026 · S-Pankki challenge "Personal Economic Trainer".

**Why another app?** One in three Finnish adults already opens S-mobiili every week, and almost everyone opens WhatsApp every day. S-Pankki Chat brings an AI money coach into the chat app people already use: ask about your own spending, get a heads-up before rent with what is *safe to spend* until payday, and turn a saving into an investment with one tap. The same coach runs as a web chat that can be embedded in S-mobiili.

## What it does

| Moment | What the customer sees |
|---|---|
| Opt-in | "Connect S-Pankki" page: choose what the coach may see, clear note that WhatsApp messages pass through Meta |
| Proactive reminder | "Your rent of 771.82 € is due tomorrow. After it you have 68.60 € safe to spend until payday, about 7.62 € a day." [Pay rent now] [Remind me later] [How do I stretch it?] |
| One-tap pay | Button → S-Pankki confirm page → 4-digit PIN → "✅ Rent paid, balance now …" back in WhatsApp |
| Free questions | "How much did I spend on restaurants last month?" · "What kind of spender am I?" · "What is safe to spend this week?" |
| What-if → invest | "What if I cut dining out by 20%?" → 97 €/month, 1,166 €/year, and what it could grow to in three demo funds (scenario, not a promise). The customer picks the fund, confirms with PIN, saving starts on payday |

### The three baskets
Every answer is framed with the challenge's teaching model:
- **Mandatory & regular**: housing, utilities, groceries, transport
- **Mandatory & irregular**: health, home & tech, travel (a buffer is kept for these)
- **Slips through your fingers**: restaurants, entertainment, lifestyle

### Trust by design
- **Numbers come from code, not the model.** Claude only calls tools over a deterministic engine (`app/engine.py`); the system prompt forbids any figure that is not in a tool result.
- **The AI never moves money.** It can only prepare a payment or investment; the customer confirms with their PIN on a bank page.
- **Explains, doesn't advise.** For investing the coach describes risk and options; the customer chooses.
- **Opt-in channel.** WhatsApp is enabled per customer with explicit consent and can be switched off; the web chat keeps everything inside S-mobiili.

## How it works

```
WhatsApp (Meta Cloud API) ─webhook─> FastAPI ─> Claude (tool use) ─> engine (pandas, deterministic)
                                        ├── /connect   consent + connect account
                                        ├── /pay/{id}  PIN confirm (stands in for PSD2 payment initiation)
                                        ├── /invest/{id}
                                        ├── /chat      same coach as a web chat (embeddable)
                                        └── /demo/remind  sends the proactive reminder
```

- **Engine:** recurring payment detection (rent day 7, salary day 15 found from history), safe-to-spend until payday (balance − bills due − usual utilities, groceries and transport − irregular buffer), what-if simulations, and a peer benchmark against the 535 S-Pankki customers aged 20-29 in the dataset.
- **Data:** the synthetic S-Pankki transaction dataset. The demo uses customer `CUST04831` (24 years old); "today" is 6 Oct 2026, a week after the dataset ends.
- **Model:** Claude Opus 5.5 via the Anthropic API, low effort for fast chat replies.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in the keys
# optional: re-extract the persona from the full dataset
python scripts/extract_persona.py --data-dir path/to/Junction-2026-S-pankki --persona CUST04831
uvicorn app.main:app --port 8000
```

- Web chat: http://localhost:8000/chat
- Ask from the terminal: `python -m app.agent "What is safe for me to spend this week?"`
- WhatsApp: expose the server (e.g. `cloudflared tunnel --url http://localhost:8000`), set `<url>/webhook` and `WA_VERIFY_TOKEN` in the Meta app, subscribe to `messages`.
- Send the reminder: `curl -X POST <url>/demo/remind`
- Tests: `python -m pytest`

## Honest limits
- Synthetic data, one demo customer; merchants are anonymous in the dataset, so they appear as "Restaurant #22".
- Payments and investments are simulated; funds are labelled demo funds, not real S-Pankki products.
- A production version would use the bank's own APIs, a WhatsApp Business account verified by S-Pankki, and template messages for reminders outside the 24-hour window.
