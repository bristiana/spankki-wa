# S-Pankki Chat · meet Kurre, your money coach where you already chat

Junction 2026 · S-Pankki challenge "Personal Economic Trainer".

**Live:** https://spankki-wa.bogdan-c-2206.workers.dev · web chat [/chat](https://spankki-wa.bogdan-c-2206.workers.dev/chat) · dashboard [/app](https://spankki-wa.bogdan-c-2206.workers.dev/app) · WhatsApp (test number, on request)

<img src="app/static/kurre.svg" width="120" align="right" alt="Kurre the squirrel">

**Why another app?** One in three Finnish adults opens S-mobiili every week, and almost everyone opens a chat app every day. Kurre, the S-Pankki savings squirrel, lives in both: **WhatsApp** and an **S-mobiili web chat** run on the same backend. You ask about your own money in plain words, get a heads-up before rent with what is *safe to spend* until payday, catch forgotten subscriptions, and put small savings away with one tap and your PIN.

## What it does

| Moment | What the customer sees |
|---|---|
| Opt-in | "Connect S-Pankki": choose what Kurre may see, with a clear note that WhatsApp messages pass through Meta. Then Kurre gives a three-basket overview and asks what you're saving for |
| Rent reminder | "Your rent of 771.82 € is due tomorrow. After rent and your usual bills you have 68.60 € safe to spend until payday, about 7.62 € a day." [Pay rent now] [Remind me later] [How do I stretch it?] |
| One-tap pay | Button → S-Pankki confirm page → 4-digit PIN → "✅ Rent paid, balance now …" back in the chat |
| Ask anything | "How much did I spend on taxis last month?" · "What kind of spender am I?" (vs 535 S-Pankki peers aged 20-29 **and** Statistics Finland households aged 17-24) · "What is safe to spend this week?" |
| Subscriptions | Active, inactive and price rises ("StreamPlus went up from 11.99 € to 13.99 €"), renewal alert the day before, stop future card payments with PIN |
| Save | Goals with deadlines ("1,500 € for Lapland by June"), honest monthly options, salary-day nudge "pay yourself first", savings pocket with PIN |
| Invest | What-if cuts ("dining out −20% = 97.18 €/month") checked against **real ETF prices** over the last 2 years; your real deposits valued in each fund; one-off investments can place a real order on a Trading 212 Practice account |
| My money | Dashboard: balance trend, safe to spend, three baskets, weekly in/out, bills, subscriptions, you vs Finland, goals, investing, latest transactions |

### The three baskets
Every answer uses the challenge's teaching model:
- **Mandatory & regular**: housing, utilities, groceries, transport
- **Mandatory & irregular**: health, home & tech, travel (a buffer is kept for these in safe-to-spend)
- **Slips through your fingers**: restaurants, entertainment, shopping, subscriptions

## Real data, not made up

| Source | Used for |
|---|---|
| S-Pankki synthetic transactions (challenge dataset) | Demo customer `CUST04831` (24 y): every balance, bill, basket, salary and deposit; peer group of 535 customers aged 20-29 |
| [Statistics Finland, Household Budget Survey 2022, table 14q4](https://pxdata.stat.fi/PxWeb/pxweb/en/StatFin/StatFin__ktutk/statfin_ktutk_pxt_14q4.px/) | "You vs Finland": monthly spending of households by age of the reference person (`scripts/fetch_statfin.py`) |
| Real monthly ETF prices (VWCE, XEON; Yahoo Finance) | Backtests of the customer's real deposits and of monthly savings (`scripts/fetch_market.py`) |

**Simulated, and labelled as such:** the PIN pages (a stand-in for PSD2 payment initiation), the demo customer's subscriptions (the dataset has none, so a few are added outside the balance), and the "Demo Fund" names.

## Trust by design
- **Numbers come from code, not the model.** Claude only calls tools over a deterministic, tested engine (`app/engine.py`); the system prompt forbids any figure that is not in a tool result.
- **The AI never moves money.** It prepares a payment, saving, investment or subscription stop; the customer confirms with their PIN. Double confirms and overdrafts are refused.
- **Informs, never advises.** Kurre explains risk and options; the customer chooses the fund.
- **Opt-in channel.** WhatsApp is enabled per customer with explicit consent; the web chat keeps everything inside S-mobiili.

## How it works

```
WhatsApp (Meta Cloud API) ─webhook─┐
S-mobiili web chat (/chat) ────────┼─> FastAPI (Cloudflare Containers) ─> Claude tool use ─> engine (pandas, deterministic)
                                   │      ├── /pay /invest /save /stop   PIN confirm pages
                                   │      ├── /app                       My money dashboard
                                   │      └── /demo/remind, /demo/alert  proactive messages (rent, salary, unusual spend, renewal)
Trading 212 Practice API <─────────┘      (one-off investments, only after PIN, when keys are set)
```

- `app/engine.py`: three baskets, recurring payments (rent day 7 and salary day 15 found from history), safe to spend, subscriptions, goals, what-if, real-price backtests, benchmarks.
- `app/agent.py` + `app/tools.py`: Kurre's persona and 15 tools; Claude Opus 5.5 at low effort for fast chat replies.
- `app/main.py`: WhatsApp webhook (signature checked), web chat API, confirm pages, dashboard, demo triggers.
- `worker/index.ts` + `Dockerfile`: the same Python app as a Cloudflare Container behind a Worker.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # ANTHROPIC_API_KEY, optional WhatsApp and Trading 212 keys
uvicorn app.main:app --port 8000
```

- Landing http://127.0.0.1:8000 · chat `/chat` · dashboard `/app`
- From the terminal: `python -m app.agent "What is safe for me to spend this week?"`
- End to end, every flow in a phone-sized browser: `python scripts/demo_walkthrough.py --url http://127.0.0.1:8000`
- Tests: `python -m pytest` (38)
- Refresh real data: `python scripts/fetch_market.py`, `python scripts/fetch_statfin.py`; rebuild the persona with `python scripts/extract_persona.py --data-dir <dataset>`
- WhatsApp: set `<url>/webhook` and `WA_VERIFY_TOKEN` in the Meta app, subscribe to `messages`. Proactive messages: `POST /demo/remind`, `POST /demo/alert?kind=salary|unusual|renewal`
- Deploy: `wrangler deploy` (bump `INSTANCE` in `wrangler.jsonc` for a fresh container)

## Honest limits
- One demo customer on synthetic data; merchants are anonymous in the dataset ("Restaurant #22").
- Payments and savings are simulated; investing uses demo fund names over real ETF prices; Trading 212 runs only on a Practice (virtual money) account.
- Production needs S-Pankki's own APIs, a verified WhatsApp Business account, template messages for reminders outside the 24-hour window, and real PIN/strong customer authentication.

Inspired by [Moni](https://github.com/Bogdan-ca/Moni) (NEXXT 2026); all code here was written from scratch for this challenge.
