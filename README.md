# S-Pankki Chat · meet Birky, your money coach where you already chat

Junction 2026 · S-Pankki challenge "Personal Economic Trainer".

**Live:** https://spankki-wa.bogdan-c-2206.workers.dev · web chat [/chat](https://spankki-wa.bogdan-c-2206.workers.dev/chat) · dashboard [/app](https://spankki-wa.bogdan-c-2206.workers.dev/app) · WhatsApp (test number, on request)

<img src="app/static/birky.svg" width="120" align="right" alt="Birky the squirrel">

> **S-Pankki doesn't need another app. It needs a friend in WhatsApp.**
> In a banking app you are a "customer": menus, forms, the feeling of being judged. WhatsApp is where you talk to your friends, so that's where you ask, without embarrassment, "I'm out of money, what do I do?"

**Birky**, the S-Pankki savings squirrel, is that friend. It writes like one: short messages, a reaction to yours, your own words back. It answers with your real numbers, nudges you to finish what you started, and moves money only when you confirm with your PIN. The same Birky also lives inside S-mobiili (web chat, home-screen cards), on one backend.

## What it does

| Moment | What the customer sees |
|---|---|
| Opt-in | "Connect S-Pankki": choose what Birky may see, with a clear note that WhatsApp messages pass through Meta. Then Birky gives a three-basket overview and a short interview, one question per message with reply buttons: what you're saving for, how much and by when, how you feel about risk (only used to label investment info), weekly check-in yes/no. It ends with the goal plan from the engine. Skip any question, say "later" to pause, ask a money question in between and it resumes (`app/onboarding.py`, no AI) |
| Basket game | "Play the basket game" (chip, or write *game*): five of your own payments, one at a time ("Restaurant #1 · fast food and cafés · 117.32 € · Sat 26 Sep. Which basket?") with three reply buttons. Each answer gets ✅ or a kind 🤔 and the reason with your own numbers ("449.87 € of yours last month"); the end shows your score, your real basket split for last month and what −20% on the third basket frees in a year. *stop* or any other question pauses it, *game* picks it up (`app/quiz.py`, no AI) |
| Learn in 2 minutes | "📚 Learn in 2 minutes" (chip, or write *learn* / *teach me*): five 30-second lessons on your own numbers. The three baskets (your split), safe to spend vs. your balance ("Your balance is 1 001.94 €, but rent of 771.82 € is due tomorrow…"), the buffer (your irregular basket per month, with S-Pankki's rule of thumb and its source), small things add up (your top "slips through" place × 12), pay yourself first (a realistic monthly saving over a labelled 10-year scenario). Each lesson has *Next lesson*, *Ask Birky* and one action: the basket game, My money, a buffer goal or "Save 34 € now" with PIN. *stop* pauses, *learn* resumes (`app/learn.py`, no AI) |
| Why it matters | When a reply gives your numbers, Birky adds at most one line on the idea behind them, never the same one twice: "Subscriptions are basket 3 on autopilot: 4 small charges add up to 858.55 € a year without a single new decision". The line comes from code (`app/lessons.py`, `lesson_for`) with the tool's own figures, so the number guard checks it like everything else |
| Rent reminder | "Your rent of 771.82 € is due tomorrow. After rent and your usual bills you have 68.60 € safe to spend until payday, about 7.62 € a day." [Pay rent now] [Remind me later] [How do I stretch it?] |
| One-tap pay | Button → S-Pankki confirm page → 4-digit PIN → "✅ Rent paid, balance now …" back in the chat |
| Ask anything | "How much did I spend on taxis last month?" · "What kind of spender am I?" (vs 535 S-Pankki peers aged 20-29 **and** Statistics Finland households aged 17-24) · "What is safe to spend this week?" |
| Can I afford it? | "Can I afford a 120€ jacket?" → fits your usual fun budget, but it is 51.40 € more than safe to spend until payday; what is left per day, and how many days it pushes back your goal |
| First home? | "Could I afford my first home?" → from your salary and 200 S-Pankki mortgage applications: the loan and price range people with that income carried, the deposit plus transfer tax and fees, months to it at several saving paces, ASP, and what banks look at (collateral, income, equity, debts). Educational estimate, never a loan offer; offers a "First home deposit" goal |
| People like you | "How much does someone like me need a month here?" · "Is 400 € on food normal for my area?" · "What does a first flat in Espoo cost?" → a typical month (middle half and median per basket and category) for S-Pankki customers of your postal area and age, and where yours sits: "in line" on most things, "a bit above" or "below" said gently, only to reassure or help a choice. Only averages of at least 30 customers; a smaller group falls back to a wider age band, then the whole capital region. A card on `/app` draws it as range bars (`app/peers.py`, `get_people_like_me`) |
| Subscriptions | Active, inactive and price rises ("StreamPlus went up from 11.99 € to 13.99 €"), renewal alert the day before, stop future card payments with PIN |
| Safe to spend is not your balance | Safe to spend is the smaller of what the balance leaves after bills, usual needs and a buffer, and what the usual pay cycle sustains (average income minus usual mandatory costs, for the days until payday). Money beyond that is the *reserve*: "You have 82 799.61 € on your account, but your usual cycle leaves about 1 313.89 € for flexible spending; the other 81 025.00 € looks like savings" |
| Safe to save | "How much can I safely save?": what can move now (the reserve plus what is left of safe to spend) without touching bills before payday or the irregular buffer, a sustainable monthly amount from the real surplus, and, when there is none, which cut would free money; one tap to the savings pocket with PIN |
| All my banks | "Add another bank" on the Connect page: a mock PSD2 consent links a synthetic **Demo Bank** (savings + credit card). Birky then shows total balances, net position after card debt, how many months those savings cover the irregular basket, and counts the card bill in safe to spend |
| Save | Goals with deadlines ("1,500 € for Lapland by June"), honest monthly options, salary-day nudge "pay yourself first", savings pocket with PIN |
| Invest | What-if cuts ("dining out −20% = 97.18 €/month") checked against **real ETF prices** over the last 2 years; your real deposits valued in each fund; one-off investments can place a real order on a Trading 212 Practice account |
| My money | Dashboard: balance trend, safe to spend, three baskets, weekly in/out, bills, subscriptions, you vs Finland, goals, investing, latest transactions |
| Inside S-mobiili | Concept page [/mobile-demo](app/templates/mobile_demo.html): a generic banking-app home screen with Birky cards drawn natively from `/api/widgets` (engine numbers, no AI, cacheable), and an "Ask Birky" bar that slides up the chat as a WebView (`/chat?embed=1`). A card tap opens the chat with its question (`/chat?q=...`). See [docs/integration.md](docs/integration.md) |
| MyData | **Explore** (`/app/explore`): build your own view of your transactions (spending or income, one category, basket or kind of place, grouped by month, week, weekday, category, basket or place; euros, count or average) and download exactly that view as CSV. Ask Birky "When do I spend most on restaurants?" and the chart is drawn in the web chat, or comes as an *Open chart* button on WhatsApp. "Download my data" (CSV/JSON) on the dashboard or by writing *my data*; *STOP* switches Birky off and deletes the chat history |
| Feels like a person | Instant reaction on your message (👀 🙏 💰 👋), read receipt + typing, replies in short bubbles; card numbers, IBANs and Finnish personal IDs are removed before the AI sees them |

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
| S-Pankki mortgage applications (challenge dataset, 200 applications + negotiation history) | First-home estimate: income-to-loan rule, equity share, one-off costs, rejection reasons (`scripts/build_mortgage_benchmark.py` → `data/mortgage_benchmark.json`) |
| S-Pankki transactions of all 5,100 customers (challenge dataset) | "People like you": p25 / median / p75 of monthly income and spending by postal area (00 Helsinki, 01 Vantaa area, 02 Espoo area) × age band, last 12 full months, groups of ≥ 30 customers only (`scripts/build_peer_benchmark.py` → `data/peer_benchmark.json`) |
| Real monthly ETF prices (VWCE, XEON; Yahoo Finance) | Backtests of the customer's real deposits and of monthly savings (`scripts/fetch_market.py`) |

**Simulated, and labelled as such:** the PIN pages (a stand-in for PSD2 payment initiation), the demo customer's subscriptions (the dataset has none, so a few are added outside the balance), the "Demo Fund" names, and the linked "Demo Bank" (a made-up bank: its savings, card and card purchases are generated).

## Trust by design
- **Numbers come from code, not the model.** Claude only calls tools over a deterministic, tested engine (`app/engine.py`); the system prompt forbids any figure that is not in a tool result. A number guard checks the reply: a figure that is in no tool result gets one rewrite.
- **Show the math.** Under each web chat answer, "🔍 How I worked this out" opens the steps Birky took, the key figures each one returned, the data sources (your transactions, Statistics Finland, Yahoo Finance, the mortgage data, official guides) and the guard's verdict, e.g. "✓ 6 of 6 figures checked against the calculations" (`app/explain.py`, `app/static/trace.js`). WhatsApp stays short: no extra messages.
- **The AI never moves money.** It prepares a payment, saving, investment or subscription stop; the customer confirms with their PIN. Double confirms and overdrafts are refused.
- **Informs, never advises.** Birky explains risk and options; the customer chooses the fund.
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

- `app/engine.py`: three baskets, spending by merchant type (ISO 18245 MCC: taxis, fast food, parking…), recurring payments (rent day 7 and salary day 15 found from history), safe to spend, subscriptions, goals, what-if, real-price backtests, benchmarks.
- `app/agent.py` + `app/tools.py`: Birky's persona and 21 tools, incl. `can_i_afford`, `get_safe_to_save`, `get_full_picture`, `get_home_readiness`, `explore_my_data` (only enum parameters, no code or SQL from the model) and `get_money_guide` over `content/finland-money-guide.md` (checked facts with official sources: Kela, vero.fi, ASP, deposit guarantee, KKV); `app/guard.py` checks every number in a reply against the tool results and asks for one rewrite if one is not there; Claude Opus 5.5 at low effort for fast chat replies.
- `app/main.py`: WhatsApp webhook (signature checked), web chat API, confirm pages, dashboard, demo triggers.
- `worker/index.ts` + `Dockerfile`: the same Python app as a Cloudflare Container behind a Worker.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # ANTHROPIC_API_KEY, optional WhatsApp and Trading 212 keys
uvicorn app.main:app --port 8000
```

- Landing http://127.0.0.1:8000 · chat `/chat` · dashboard `/app` · explore `/app/explore` · inside a banking app `/mobile-demo` · cards `/api/widgets`
- From the terminal: `python -m app.agent "What is safe for me to spend this week?"`
- End to end, every flow in a phone-sized browser: `python scripts/demo_walkthrough.py --url http://127.0.0.1:8000`
- Other customers: the "Viewing as" menu on `/chat`, `/app` and `/app/explore` (or `?as=<CUSTID>`) switches the web app to one of four more real customers picked by rule from the dataset (student-age, high earner, irregular income, older homeowner; `data/personas/index.json` says why each). Each has its own engine, state file (`data/state/<CUSTID>.json`) and chat history; WhatsApp stays on Aino. Rebuild with `python scripts/extract_personas.py --data-dir <dataset>`
- Tests: `python -m pytest` (391). Conversation quality: `python scripts/convo_eval.py` (37 chats, read as WhatsApp bubbles) → [docs/convo-eval.md](docs/convo-eval.md). Fleet check, every customer in the dataset: `python scripts/fleet_check.py --data-dir <dataset>` → `docs/fleet-check.md`
- Refresh real data: `python scripts/fetch_market.py`, `python scripts/fetch_statfin.py`; rebuild the persona with `python scripts/extract_persona.py --data-dir <dataset>`, the mortgage benchmark with `python scripts/build_mortgage_benchmark.py --data-dir <dataset>` and the peer benchmark with `python scripts/build_peer_benchmark.py --data-dir <dataset>`
- WhatsApp: set `<url>/webhook` and `WA_VERIFY_TOKEN` in the Meta app, subscribe to `messages`. Proactive messages: `POST /demo/remind`, `POST /demo/alert?kind=salary|unusual|renewal`
- Proactive check: `POST /cron/wake?key=<WA_VERIFY_TOKEN>` (also on a Worker cron at 07, 12 and 17 UTC) picks the most urgent signal per person from `app/rules.py` and sends it inside WhatsApp's 24-hour window (no templates: after 24 h of silence Birky waits for the person to write). It sends nothing unless `DEMO_PROACTIVE=1`; add `&dry_run=1` to only see what would go out
- Deploy: `wrangler deploy` (bump `INSTANCE` in `wrangler.jsonc` for a fresh container)

## Honest limits
- One demo customer on synthetic data; merchants are anonymous in the dataset ("Restaurant #22").
- Payments and savings are simulated; investing uses demo fund names over real ETF prices; Trading 212 runs only on a Practice (virtual money) account.
- Production needs S-Pankki's own APIs, a verified WhatsApp Business account (and, if Birky should ever write first after 24 h of silence, approved template messages), and real PIN/strong customer authentication.

Inspired by [Moni](https://github.com/Bogdan-ca/Moni) (NEXXT 2026); all code here was written from scratch for this challenge.
