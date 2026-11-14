# Junction submission text (copy into the form)

**Project name (max 30):** Birky · S-Pankki Chat

**Punchline (max 200):** S-Pankki doesn't need another app. It needs a friend in WhatsApp: Birky answers with your real numbers, teaches the three baskets and saves with one PIN tap.

**Challenge:** Personal Economic Trainer (S-Pankki)

**Project demo:** https://spankki-wa.bogdan-c-2206.workers.dev

**Source code:** https://github.com/bristiana/spankki-wa

## Description (markdown)

**S-Pankki doesn't need another app. It needs a friend in WhatsApp.** In a banking app you are a "customer": menus, forms, the feeling of being judged. WhatsApp is where people talk to their friends, so that's where they ask, without embarrassment, "I'm out of money, what do I do?". **Birky**, the S-Pankki savings squirrel, is that friend: short, warm messages with your real numbers, and the same Birky inside S-mobiili, on one backend. It answers in English, Finnish or Swedish.

**Financial literacy: it teaches, with your own numbers**
- **The three baskets everywhere.** Mandatory & regular, mandatory & irregular, and money that slips through your fingers: in every answer, on the My money dashboard, and in a basket game played on your own payments.
- **Learn in 2 minutes.** Five 30-second lessons built on your figures: the three baskets, safe to spend vs. your balance, the buffer, small things add up, pay yourself first. Each ends with one action, such as starting a buffer goal.
- **A "why it matters" line under the numbers.** Aino's September restaurants: "Small things add up: 10 payments came to 644.63 € in a month, about 7 735.56 € a year if every month looked like this."

**AI that does the maths in code and explains it in words**
- **Real answers.** "What kind of spender am I?", "What is safe to spend this week?", "Can I afford a 900 € laptop?", "How ready am I to buy a first home?"
- **Every number from a tested engine.** Claude calls 21 tools over a deterministic engine and only explains. A number guard checks every figure in a reply against the tool results, and "How I worked this out" sits under each answer.
- **It speaks first.** The day before rent: "After it you have 68.60 € safe to spend until payday." A renewal alert the day before a subscription renews, and "pay yourself first" on salary day.
- **Voice notes in WhatsApp.** Send a voice message and Birky says back what it heard, then answers as if you had typed it. The audio is not stored.
- **What-ifs.** "Cut dining out 20% = 97.18 € a month", checked against real ETF prices from the last 2 years. Birky informs and never advises.

**Built for a phone**
- Every flow was walked at 390×844 with touch, in light and dark mode: 44 px tap targets, WCAG AA contrast, amounts that never break across lines.
- One tap and your PIN to pay rent, save, invest or stop a subscription. The AI prepares; only your PIN moves money.

**MyData: your data, your questions**
- **Explore** lets you filter your own transactions by category, basket, merchant type and dates, and group them by month, week, weekday or category, instead of charts the bank picked, and export them as CSV or JSON.
- **Benchmarks** compare you with 535 peers aged 20-29 from the S-Pankki dataset and with Finnish households your age (Statistics Finland).
- **Multi-bank view (PSD2):** connect another bank and Birky sees the full picture.

**Real data**
- The S-Pankki transaction dataset: demo customer Aino (CUST04831, 24), plus four more customers aged 18 to 60 to switch between.
- The S-Pankki mortgage data: the first-home estimate is built from the approved applications in it. An educational estimate, never a loan offer.
- Statistics Finland Household Budget Survey 2022 (table 14q4).
- Real monthly ETF prices (VWCE, XEON): backtests of the customer's real deposits.

Simulated, and labelled as such: the PIN pages (a stand-in for PSD2 payment initiation), the other bank's account, the demo customer's subscriptions (the dataset has none) and the demo fund names.

**Feasibility:** Claude (tool use over a deterministic pandas engine), FastAPI, WhatsApp Cloud API, Cloudflare Containers, Statistics Finland PxWeb API. 360 automated tests, an end-to-end browser walkthrough of every flow, and a scored evaluation of real AI conversations.

**S-mobiili fit:** the same API serves the chat embedded in the app (`/chat?embed=1`), with a concept of Birky's cards inside S-mobiili at `/mobile-demo`. Next steps are real PSD2 payment initiation and account access, and subscriptions from S-Pankki card data.
