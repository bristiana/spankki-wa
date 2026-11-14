# Birky inside S-mobiili

How S-Pankki's mobile app could carry Birky without rebuilding it. Concept page: `/mobile-demo` (a generic banking-app mock, not the real S-mobiili).

There are three parts, and each one works on its own:

| Part | What S-mobiili does | Endpoint |
|---|---|---|
| Native cards | Draws small cards on the home screen with its own UI kit. No AI call, cacheable | `GET /api/widgets` |
| Chat | Opens the web chat in a WebView (a bottom sheet or a full screen) | `/chat?embed=1` |
| Deep links | A card tap opens the chat with a question already asked | `/chat?embed=1&q=<question>` |

The chat runs on the same coach backend as WhatsApp (`app/agent.py` over `app/engine.py`), so the answers, the PIN confirm pages and the proactive messages match on both channels.

## `GET /api/widgets`

Every number comes from the deterministic engine (`safe_to_spend`, `upcoming_bills`, `baskets`, `commitments`, `goals`), never from the model. Responses carry `Cache-Control: private, max-age=300`.

```json
{
  "version": 1,
  "as_of": "2026-10-06",
  "max_age_s": 300,
  "widgets": [
    {"id": "safe_to_spend", "title": "Safe to spend", "value": "68.60 €",
     "subtitle": "until payday Thu 15 Oct · 7.62 € a day",
     "amount_eur": 68.6, "per_day_eur": 7.62, "payday": "2026-10-15", "days_to_payday": 9,
     "cycle_budget_eur": 288.82, "reserve_eur": 0.0,
     "deep_link": "/chat?q=What+is+safe+for+me+to+spend+until+payday%3F"},
    {"id": "next_bill", "title": "Next bill", "value": "771.82 €", "subtitle": "Rent · Wed 07 Oct (tomorrow)",
     "name": "Rent", "amount_eur": 771.82, "due_date": "2026-10-07", "days_away": 1,
     "deep_link": "/chat?q=How+does+my+rent+fit+before+payday%3F",
     "actions": [{"label": "Pay", "deep_link": "/chat?q=__btn__%3Apay%3Ahousing_payment-2026-10-07"}]},
    {"id": "baskets", "title": "Your three baskets", "value": "2 119.58 €", "subtitle": "spent last month",
     "period": "last_month", "from": "2026-09-01", "to": "2026-09-30", "total_eur": 2119.58,
     "baskets": [{"key": "regular", "label": "Mandatory & regular", "share_pct": 56, "total_eur": 1185.85},
                 {"key": "irregular", "label": "Mandatory & irregular", "share_pct": 0, "total_eur": 0.0},
                 {"key": "everyday", "label": "Slips through your fingers", "share_pct": 44, "total_eur": 933.73}],
     "deep_link": "/chat?q=Where+did+my+money+go+last+month%2C+basket+by+basket%3F"},
    {"id": "regular_payments", "kind": "unusual_bill", "title": "A bigger utility bill", "value": "75.00 €",
     "subtitle": "Fri 28 Aug · usually 19.94 € to 60.09 €", "name": "Utility bills",
     "amount_eur": 75.0, "date": "2026-08-28", "usual_low_eur": 19.94, "usual_high_eur": 60.09,
     "deep_link": "/chat?q=What+are+my+regular+payments%3F"},
    {"id": "goal", "title": "Lapland trip", "value": "0%", "subtitle": "0.00 € of 1 500.00 €",
     "goal_id": "laplandtrip", "progress_pct": 0, "saved_eur": 0.0, "target_eur": 1500.0, "deadline": "2027-06-30",
     "deep_link": "/chat?q=How+is+my+Lapland+trip+goal+going%3F"}
  ]
}
```

Every card has `id`, `title`, `value`, `subtitle` and `deep_link`, so a client can draw any card with one generic layout and add extras by `id`: the basket shares as a stacked bar, the goal as a progress bar, `actions` as buttons. The other fields hold the raw numbers for the app's own formatting and screen readers.

| `id` | When it is there | Source |
|---|---|---|
| `safe_to_spend` | always; `cycle_budget_eur` is what the usual pay cycle leaves for flexible spending, `reserve_eur` the money on the account beyond it (likely savings, never counted as safe to spend) | `Engine.safe_to_spend()` |
| `next_bill` | a known bill within 45 days (the regular investment deposit is not counted as a bill) | `Engine.upcoming_bills(45)` |
| `baskets` | always; this month, or last month while this month has no spending yet (`period` says which) | `Engine.baskets()` |
| `regular_payments` | a regular payment from the customer's own data that went up over the last 3 months (`kind: increase`), otherwise a utility bill well above the usual range (`kind: unusual_bill`), otherwise the monthly total (`kind: summary`) | `Engine.commitments()` |
| `goal` | the customer has a savings goal | `Engine.goals()` |

## Deep links

- `/chat?q=<question>` asks the question once when the page loads, then removes `q` from the address so a reload does not ask again. `q` is JSON-encoded into the page, never written out as HTML.
- A `q` that starts with `__` is a direct action that needs no AI: `__btn__:pay:<bill_id>` prepares the payment and shows the *Pay* button. The money still only moves after the customer confirms on the PIN page.
- `/chat?embed=1` hides the site header, nav and footer, removes outer margins and pads for the safe-area insets (`viewport-fit=cover` is already set). Combine both: `/chat?embed=1&q=...`.

## Embedding

1. Home screen: fetch `/api/widgets` when the screen opens, keep it for `max_age_s`, and draw the cards natively.
2. Card tap: open a WebView sheet at `deep_link` with `&embed=1` added.
3. Chat button: open the same WebView at `/chat?embed=1`.
4. PIN pages (`/pay`, `/save`, `/stop` for pausing the monthly investing, `/invest`) open inside the chat and tell it when they are done (`postMessage("done")`). In production S-mobiili would swap them for its own strong customer authentication.

## What changes for production

- **Auth.** The demo has one synthetic customer and no login. Inside S-mobiili the WebView and `/api/widgets` would use the bank session (SSO or a short-lived token the app passes in a header), and the chat session id would come from that session, not from `sessionStorage`.
- **Data.** The engine reads a CSV; in production it would read S-Pankki's account and card APIs.
- **One backend, two channels.** WhatsApp (`/webhook`) and the embedded chat (`/api/chat`) call the same coach and engine. A customer can use either, or both.
