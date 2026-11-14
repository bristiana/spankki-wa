# Junction submission text (copy into the form)

**Project name (max 30):** Birky · S-Pankki Chat

**Punchline (max 200):** S-Pankki doesn't need another app. It needs a friend in WhatsApp: Birky answers with your real numbers, teaches the three baskets and saves with one PIN tap.

**Challenge:** Personal Economic Trainer (S-Pankki)

**Project demo:** https://spankki-wa.bogdan-c-2206.workers.dev

**Source code:** https://github.com/bristiana/spankki-wa

## Description (markdown)

**S-Pankki doesn't need another app. It needs a friend in WhatsApp.** In a banking app you are a "customer": menus, forms, the feeling of being judged. WhatsApp is where people talk to their friends, so that's where they ask, without embarrassment, "I'm out of money, what do I do?". **Birky**, the S-Pankki savings squirrel, is that friend: short, warm messages with your real numbers, and the same Birky inside S-mobiili, on one backend.

Birky is for everyday money at any age, and especially helpful for young adults with a first salary. The demo opens on Antti, 38: the median S-Pankki customer is 44, so we picked, by rule from the data, a customer around that age with a typical salary, rent, bills, monthly investing and the everyday spending most of us have.

**What Birky does**
- **Answers the three questions that matter most, with real numbers.** "What kind of spender am I?" (an experience spender: 31% slips through his fingers), "What can I safely spend this week?" (183.31 €, after bills, usual needs and a buffer) and "How could I save more?" (20% less on shopping = 1 224.50 € a year; 6 314.43 € has built up beyond this month's needs and could become an emergency buffer; utility bills, 2 476.44 € a year, worth a review). Every figure is computed by a tested engine; the AI only explains.
- **Teaches the three baskets.** Mandatory & regular, mandatory & irregular, and money that slips through your fingers, in every answer and on the My money dashboard.
- **Warns before bills.** The day before rent: what is due, and what is still safe to spend until payday. Pay with one tap and your PIN.
- **Knows your regular payments, from your own data.** Rent, about 3 utility bills a month and the monthly investing: what each costs a month and a year, when it's due, and when one goes up. Rent can be paid with PIN, the monthly investing can be paused with PIN. Ask it to cancel your subscriptions and it says honestly that there are none.
- **Turns savings into goals and investments.** An emergency buffer, a home renovation, a trip: Birky works out the monthly amount, checks a cut against real ETF prices for the last 2 years, nudges "pay yourself first" on payday, and moves money only after your PIN. It informs and never advises.

**Real data**
- The S-Pankki transaction dataset: Antti (CUST01656) compared with 228 S-Pankki customers aged 30-39 in his area, and five more real customers to switch to, from 18 to 60 (picked by rule, each with the reason).
- Statistics Finland Household Budget Survey 2022: you vs Finnish households.
- Real monthly ETF prices (VWCE, XEON): backtests of the customer's real deposits.

Simulated, and labelled as such: the PIN pages and the payments, savings and pauses they confirm (a stand-in for PSD2 payment initiation), the "Demo Fund" names over real ETF prices, and the optional linked "Demo Bank" (a made-up second bank for the multi-bank view). Every balance, bill and regular payment comes from the S-Pankki dataset.

**Built with:** Claude (tool use over a deterministic pandas engine), FastAPI, WhatsApp Cloud API, Cloudflare Containers, Statistics Finland PxWeb API. 547 automated tests, plus an end-to-end browser walkthrough of every flow.

**S-mobiili fit:** the same API powers an in-app chat. Next steps are PSD2 payment initiation, a real multi-bank view and card data, where the same detector would also find card subscriptions.
