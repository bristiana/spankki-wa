# Junction submission text (copy into the form)

**Project name (max 30):** Oiva · S-Pankki Chat

**Punchline (max 200):** S-Pankki doesn't need another app. It needs a friend in WhatsApp: Oiva answers with your real numbers, teaches the three baskets and saves with one PIN tap.

**Challenge:** Personal Economic Trainer (S-Pankki)

**Project demo:** https://spankki-wa.bogdan-c-2206.workers.dev (no WhatsApp? open /login and tap "Use the demo account")

**Source code:** https://github.com/bristiana/spankki-wa

## Description (markdown)

**S-Pankki doesn't need another app. It needs a friend in WhatsApp.** In a banking app you are a "customer": menus, forms, the feeling of being judged. WhatsApp is where people talk to their friends, so that's where they ask, without embarrassment, "I'm out of money, what do I do?". **Oiva**, the S-Pankki savings squirrel, is that friend: short, warm messages with your real numbers, and the same Oiva inside S-mobiili, on one backend.

Oiva is for everyday money at any age, and especially helpful for young adults with a first salary. The demo opens on Antti, 38: the median S-Pankki customer is 44, so we picked, by rule from the data, a customer around that age with a typical salary, rent, bills, monthly investing and the everyday spending most of us have.

**What Oiva does**
- **Answers the three questions that matter most, with real numbers.** "What kind of spender am I?" (an experience spender: 31% slips through his fingers), "What can I safely spend this week?" (183.31 €, after bills, usual needs and a buffer) and "How could I save more?" (20% less on shopping = 1 224.50 € a year; 6 314.43 € has built up beyond this month's needs and could become an emergency buffer; utility bills, 2 476.44 € a year, worth a review). Every figure is computed by a tested engine; the AI only explains.
- **Teaches the three baskets.** Mandatory & regular, mandatory & irregular, and money that slips through your fingers, in every answer and on the My money dashboard.
- **Warns before bills.** The day before rent: what is due, and what is still safe to spend until payday. Pay with one tap and your PIN.
- **Knows your regular payments, from your own data.** Rent, about 3 utility bills a month and the monthly investing: what each costs a month and a year, when it's due, and when one goes up. Rent can be paid with PIN, the monthly investing can be paused with PIN. Ask it to cancel your subscriptions and it says honestly that there are none.
- **Sees your other banks, for real (MyData).** S-Pankki stays your main bank; write "add bank" and Oiva links your real Revolut or any Finnish bank through Enable Banking, a licensed PSD2 provider: read only, 90 days, approved in your own bank. RON, USD and other accounts are converted to EUR, moves between your own accounts are left out, and transfers to people are kept apart from card spending. Only you see them, on the site you log in to with a link Oiva sends you on WhatsApp.
- **Turns savings into goals and investments.** An emergency buffer, a home renovation, a trip: Oiva works out the monthly amount, checks a cut against real ETF prices for the last 2 years, nudges "pay yourself first" on payday, and moves money only after your PIN. It informs and never advises.

**Real data**
- The S-Pankki transaction dataset: Antti (CUST01656) compared with 228 S-Pankki customers aged 30-39 in his area, and five more real customers to switch to, from 18 to 60 (picked by rule, each with the reason).
- Statistics Finland Household Budget Survey 2022: you vs Finnish households.
- Real monthly ETF prices (VWCE, XEON): backtests of the customer's real deposits.
- Your own other banks, live through Enable Banking (PSD2 account information), with ECB exchange rates.

Simulated, and labelled as such: the PIN pages and the payments, savings and pauses they confirm (a stand-in for PSD2 payment initiation), the "Demo Fund" names over real ETF prices, and the "Demo Bank" on the demo account (a made-up second bank, so the jury sees the multi-bank view without linking a real one). Every balance, bill and regular payment comes from the S-Pankki dataset.

**Built with:** Claude (tool use over a deterministic pandas engine), FastAPI, WhatsApp Cloud API, Cloudflare Containers, Statistics Finland PxWeb API, Enable Banking. 653 automated tests, plus an end-to-end browser walkthrough of every flow.

**S-mobiili fit:** the same API powers an in-app chat. The multi-bank view already runs on real PSD2 account data. Next steps are PSD2 payment initiation and card data, where the same detector would also find card subscriptions.
