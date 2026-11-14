# Junction submission text (copy into the form)

**Project name (max 30):** Birky · S-Pankki Chat

**Punchline (max 200):** Birky, the S-Pankki savings squirrel: an AI money coach in WhatsApp and S-mobiili that answers with your real numbers, warns before bills and saves with one PIN tap.

**Challenge:** Personal Economic Trainer (S-Pankki)

**Project demo:** https://spankki-wa.bogdan-c-2206.workers.dev

**Source code:** https://github.com/bristiana/spankki-wa

## Description (markdown)

**Why another app?** One in three Finnish adults opens S-mobiili every week; almost everyone opens a chat app every day. **Birky**, the S-Pankki savings squirrel, lives in both: WhatsApp (Meta Cloud API) and an S-mobiili web chat, on one backend.

**What Birky does**
- **Answers with real numbers.** "How much did I spend on taxis last month?", "What kind of spender am I?", "What is safe to spend this week?". Every figure is computed by a tested engine; the AI only explains.
- **Teaches the three baskets.** Mandatory & regular, mandatory & irregular, and money that slips through your fingers, in every answer and on the My money dashboard.
- **Warns before bills.** "Your rent of 771.82 € is due tomorrow. After it you have 68.60 € safe to spend until payday." Pay with one tap and your PIN.
- **Sorts subscriptions.** Active, inactive and price rises, a renewal alert the day before, and stop future card payments with PIN.
- **Turns savings into goals and investments.** "Cut dining out 20% = 97.18 €/month". Birky checks this against real ETF prices for the last 2 years, sets a Lapland goal, nudges "pay yourself first" on payday, and moves money only after your PIN. It informs and never advises.

**Real data**
- The S-Pankki transaction dataset: demo customer CUST04831, compared with 535 peers aged 20-29.
- Statistics Finland Household Budget Survey 2022: you vs Finnish households of your age group.
- Real monthly ETF prices (VWCE, XEON): backtests of the customer's real deposits.

Simulated, and labelled as such: the PIN pages (a stand-in for PSD2 payment initiation), the demo customer's subscriptions (the dataset has none) and the demo fund names.

**Built with:** Claude (tool use over a deterministic pandas engine), FastAPI, WhatsApp Cloud API, Cloudflare Containers, Statistics Finland PxWeb API. 44 automated tests, plus an end-to-end browser walkthrough of every flow.

**S-mobiili fit:** the same API powers an in-app chat. Next steps are PSD2 payment initiation, a multi-bank view and real card subscriptions.
