# Junction submission text (copy into the form)

**Project name (max 30):** Birky · S-Pankki Chat

**Punchline (max 200):** S-Pankki doesn't need another app. It needs a friend in WhatsApp: Birky answers with your real numbers, teaches the three baskets and saves with one PIN tap.

**Challenge:** Personal Economic Trainer (S-Pankki)

**Project demo:** https://spankki-wa.bogdan-c-2206.workers.dev

**Source code:** https://github.com/bristiana/spankki-wa

## Description (markdown)

**S-Pankki doesn't need another app. It needs a friend in WhatsApp.** In a banking app you are a "customer": menus, forms, the feeling of being judged. WhatsApp is where people talk to their friends, so that's where they ask, without embarrassment, "I'm out of money, what do I do?". **Birky**, the S-Pankki savings squirrel, is that friend: short, warm messages with your real numbers, and the same Birky inside S-mobiili, on one backend.

**What Birky does**
- **Answers with real numbers.** "How much did I spend on taxis last month?", "What kind of spender am I?", "What is safe to spend this week?". Every figure is computed by a tested engine; the AI only explains.
- **Teaches the three baskets.** Mandatory & regular, mandatory & irregular, and money that slips through your fingers, in every answer and on the My money dashboard.
- **Warns before bills.** "Your rent of 771.82 € is due tomorrow. After it you have 68.60 € safe to spend until payday." Pay with one tap and your PIN.
- **Knows your regular payments, from your own data.** Rent, about 3 utility bills a month and the monthly investing: what each costs a month and a year, when it's due, and when one goes up ("a 75.00 € utility bill on 28 Aug; one bill is usually 19.94 € to 60.09 €"). Rent can be paid with PIN, the monthly investing can be paused with PIN. Ask it to cancel your subscriptions and it says honestly that there are none.
- **Turns savings into goals and investments.** "Cut dining out 20% = 97.18 €/month". Birky checks this against real ETF prices for the last 2 years, sets a Lapland goal, nudges "pay yourself first" on payday, and moves money only after your PIN. It informs and never advises.

**Real data**
- The S-Pankki transaction dataset: demo customer CUST04831, compared with 535 peers aged 20-29.
- Statistics Finland Household Budget Survey 2022: you vs Finnish households of your age group.
- Real monthly ETF prices (VWCE, XEON): backtests of the customer's real deposits.

Simulated, and labelled as such: the PIN pages and the payments, savings and pauses they confirm (a stand-in for PSD2 payment initiation), the "Demo Fund" names over real ETF prices, and the optional linked "Demo Bank" (a made-up second bank for the multi-bank view). Every balance, bill and regular payment comes from the S-Pankki dataset.

**Built with:** Claude (tool use over a deterministic pandas engine), FastAPI, WhatsApp Cloud API, Cloudflare Containers, Statistics Finland PxWeb API. 44 automated tests, plus an end-to-end browser walkthrough of every flow.

**S-mobiili fit:** the same API powers an in-app chat. Next steps are PSD2 payment initiation, a real multi-bank view and card data, where the same detector would also find card subscriptions.
