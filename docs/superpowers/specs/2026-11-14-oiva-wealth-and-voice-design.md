# Oiva: monthly spend, budgets, "people like me" and a voice coach in S-mobiili

**Status:** draft for the team to approve, Sat 14 Nov 2026, about 19:45. Submission is Sun 10:00.
**Who builds what:** Bogdan (backend, WhatsApp login), Christiana (ElevenLabs voice, WhatsApp), and the S-mobiili
app owner (screens and design). Details in [Who owns what](#who-owns-what).

## Why

The S-Pankki brief scores five things equally: financial literacy, originality of the AI, the mobile experience,
feasibility, and fit with S-mobiili. The app already answers questions with real numbers. This adds what a customer
can *see and set* (Revolut Wealth-style monthly spend and budgets) and a way to *talk* to the coach. Oiva is a
character you have a conversation with, like Meta's friendly AI companion, who teaches with your own numbers.

## Decided by the team

- **The coach is Oiva** (Oiva Orava), the vault-keeper squirrel from `design/mascot`, in red fur.
- **Budgets and monthly spend follow Revolut Wealth.** Screenshots are coming; the screens below are refined with
  them.
- **A budget can be set to the typical spend of people in a region:** the Helsinki area from S-Pankki's data, or
  all of Finland from Statistics Finland.
- **"People like me" is the same city plus the same age band.** The cities are Helsinki, Espoo and Vantaa.
- **Five comparison figures:** income, rent as a share of income, savings rate, restaurant and shopping spend, and
  buffer in months.
- **Voice lives in the S-mobiili concept**, through ElevenLabs. Christiana owns it.
- **Best practice wherever the team didn't decide.** Those calls are marked *(my call)*.

## Goals

1. **Spend:** see this month's spending against last month and against a budget, by category.
2. **Budget:** set a budget per category in one tap, from your own amount, "people like me" or a regional typical.
3. **Compare:** see the five figures against people your age in your city, in plain words.
4. **Bills:** see recurring bills, when they come, and when one goes up or looks unusual.
5. **Talk to Oiva:** a voice call inside S-mobiili about all of the above, where Oiva asks you questions, explains
   and teaches.

## Not doing tonight

- Real PSD2 or account data.
- Real card subscriptions. The dataset has none: no customer has a fixed monthly card charge. The demo
  subscriptions stay simulated and labelled.
- Finnish-language voice (stretch goal).
- Voice on WhatsApp.

## Failures to avoid, even if it "works"

- Oiva says a number no tool gave it.
- A comparison rests on a handful of people, or doesn't say which people.
- Oiva gives advice ("you should buy…"). Oiva informs and teaches; you decide.
- Voice is the only way to do something. Every voice action also exists on screen.
- The demo breaks because the venue Wi-Fi or the microphone fails. Keep a recorded backup.

## Constraints and facts from the data (checked 14 Nov)

| Fact | Value |
|---|---|
| Customers | 5,100, all in three postcode zones: `00` Helsinki (2,557), `01` Vantaa (1,116), `02` Espoo (1,427) |
| Dataset dates | 1 Oct 2024 – 30 Sep 2026. The demo's "today" is 6 Oct 2026, so the window is **Apr–Sep 2026** |
| Coverage of the five figures | 5,099 customers have a regular salary; 4,867 pay rent |
| Cohorts (age band × city) | 18 cohorts, smallest 58 (65+ in Vantaa), largest 938 (45–54 in Helsinki) |
| Typical values (all customers, my check over Mar–Aug 2026; the builder reruns on Apr–Sep) | Income 3,186 €/month; rent share 33 %; savings rate 6 %; restaurants 296 €/month; shopping 265 €/month; buffer 3.0 months |
| Recurring payments in the data | Rent, salary and monthly investing: one a month. Utilities: about 3 a month, 20–75 €, no merchant name |
| Statistics Finland | Table 14q4 (by age) is already used. 14q7 has urban vs rural. **Nothing by city** |
| Default demo customer | **Antti, 38** (#71), so cohort 35–44 in his city |
| Recurring payments | `Engine.commitments()` (#64) already finds them from the real data |
| S-mobiili mock | **`/s-mobiili/`** (`app/templates/smobiili.html`, #69). Build there; `/mobile-demo` is the older page |

## Approaches for voice (decided: A)

| | How | Good | Bad |
|---|---|---|---|
| **A. ElevenLabs Agent with our tools (chosen)** | ElevenLabs runs speech-to-text, the model, turn-taking and the voice; it calls our API as "server tools" for every fact | Fastest; natural conversation and interruptions out of the box | The number guard can't stop a sentence before it's spoken. Mitigated below |
| B. ElevenLabs with our own model endpoint | Our Claude agent behind an OpenAI-compatible endpoint | The guard stays in the loop | Adapter and streaming work; slower to respond |
| C. Our own pipeline | Whisper, our agent, ElevenLabs voice only | Full control | Turn-taking and interruptions are hard; push-to-talk feel |

## The features

### 1. Monthly spend (like Revolut Wealth's spending view)

- **Month switcher:** the last 12 months, defaulting to the current one.
- **Header:** total spent, and the change from last month ("+82 € vs August"). Income for the month underneath.
- **Pace line:** spending so far this month against an even pace towards the budget, with a marker for today. The
  line goes over budget → amber; over by month end → red.
- **Categories, largest first:** each row shows spent, budget (if set), a progress bar and the change from last
  month. Tap a category → its places (merchants) and a 6-month trend.
- **Bills strip:** this month's recurring bills on a small calendar (from feature 4).
- **The three baskets stay visible:** each category row shows a small basket dot, so the lesson keeps working.

### 2. Budgets

- **One budget per everyday category,** plus an optional total. Categories: Groceries, Restaurants, Transportation,
  Entertainment, Health, Home & Tech, Shopping (the data's `Lifestyle`) and Travel. Rent and utilities are bills,
  shown as fixed rather than budgeted *(my call)*.
- **Set a budget from:**
  - **Your own amount.**
  - **People like me:** the median for your age band in your city, from S-Pankki's data, with who and how many:
    "People aged 35–44 in Helsinki typically spend X € a month on restaurants (761 people)."
  - **Region typical:** Helsinki area (all three cities, all ages, S-Pankki data) or Finland (Statistics Finland,
    your age group).
- **Median, labelled "typical"** *(my call)*. The team said "average"; the median isn't pulled up by a few big
  spenders. The screen says "typical (median)".
- **Finland is per person:** 14q4 gives euros per household. Bogdan checks whether 14q9 has household size by age
  and divides by it. If not, the screen says "per household" and doesn't pretend otherwise.
- **During the month:** "on track", "X € left, Y € a day" or "over by X €". Alerts at 80 % and 100 % in the app,
  and on WhatsApp inside the 24-hour window.
- **Saving a budget needs no PIN** (it's a setting, not a money move), but the screen shows **Undo** for 10 seconds
  *(my call)*.

### 3. "People like me"

**Cohort:** your age band (18–24, 25–34, 35–44, 45–54, 55–64, 65+) × your city (postcode `00` Helsinki, `01`
Vantaa, `02` Espoo). Leave the customer out of their own cohort. If a cohort ever has fewer than 30 people, widen
to the age band across all three cities, and say so.

**The five figures,** each a monthly average over the 6 full months:

| Figure | Definition | Better when |
|---|---|---|
| Income | Salary credits | (neutral, context only) |
| Rent share | Rent (`housing_payment`) ÷ income | Lower |
| Savings rate | (income − all money out except investment deposits) ÷ income. Investing counts as saving | Higher |
| Restaurants | Spending in `Restaurants` | (neutral: shown, not judged) |
| Shopping | Spending in `Lifestyle`, labelled "Shopping" | (neutral) |
| Buffer | Balance today ÷ monthly spending in baskets 1 and 2 (`engine.BASKETS` regular + irregular) | Higher |

- **Shown as:** your value, the cohort median, the 25th–75th percentile band, and **your percentile** in words
  ("more than 7 in 10 people your age in Helsinki").
- **No grades or red marks** on restaurants or shopping. They're choices, and Oiva teaches rather than judges.
  Rent share, savings rate and buffer get a gentle "this is a common target" line from `content/finland-money-guide.md`
  where one exists.
- **Every figure says where it comes from:** "S-Pankki customers aged 35–44 in Helsinki, Apr–Sep 2026, n = 761".

### 4. Recurring bills

**Already built by Cristiana in #64:** `Engine.commitments()` finds, in each customer's own data:
- rent;
- utilities (about 3 bills a month with no merchant name, treated as one monthly total with the usual range of one
  bill);
- the monthly investment deposit;
- salary.

For each it gives the monthly and yearly cost, the usual day, the next expected date, the status, a trend (the last
3 months against the 3 before, counted only beyond the usual swing) and unusual bills. The made-up subscriptions are
gone; the card-subscription check finds none. **The Bills screen shows exactly this**, in Cristiana's wording, and
`/api/recurring` returns `Engine.commitments()` unchanged.

### 5. Talking to Oiva (voice, in S-mobiili)

**Who Oiva is:** a friendly, slightly playful vault-keeper squirrel. Short sentences, one question at a time. Oiva
reacts to what you say, uses your own numbers to teach, and asks you to guess before telling you ("Guess how much
went on restaurants in September?"). Oiva informs and never advises, never says a number a tool didn't give, and
can't move money; for that, Oiva shows the right screen and you confirm there. English first.

**In the app:**
- A **"Talk to Oiva"** button on the S-mobiili home screen and in the chat composer.
- Tapping it opens a **call view**: the animated Oiva big in the middle, live captions, a mute button and an end
  button.
- **Oiva's animation follows the call:** listening → `listening`, thinking → `thinking`, speaking → `teaching`. A
  tool can set a mood like `caution`, `saving` or `learned` (see `design/mascot/oiva.js`).
- **While talking, Oiva can switch the screen behind the call** to Spend, Budget, Compare or Bills (a client tool),
  so you see what you hear.
- **First call:** one line explaining that audio goes to ElevenLabs, our voice provider, and isn't stored by
  S-Pankki. Then the call starts.
- **Maximum 5 minutes per call.** When the call ends, a short summary card appears in the chat.

**Tools Oiva can call** (server tools → our API; few, fast and well described):

| Tool | Does |
|---|---|
| `get_month_summary(month?)` | Feature 1 header and top categories |
| `get_budget()` / `set_budget(category, mode, amount?)` | Feature 2. `set_budget` changes the setting and the screen shows Undo |
| `compare_to_peers(figure?)` | Feature 3, one figure or all five |
| `get_recurring_bills()` | Feature 4 |
| `get_safe_to_spend()` | Already exists |
| `next_lesson()` / `answer_lesson(answer)` | The existing 5-lesson course, by voice; progress shared with text |
| `show_screen(name)` (client tool) | Switches the S-mobiili screen behind the call |

Every tool result has a `speech` field: a ready-to-say sentence with the figures written the way they should be
spoken. Oiva repeats those figures and no others.

**Keeping numbers honest with option A:**
1. The prompt rule: never say a number that isn't in a tool result this call.
2. Tools return `speech` lines, so the easy path is to quote them.
3. Our server logs every tool result per call. After the call, the post-call webhook runs the existing number
   guard (`app/guard.py`) over the transcript against those logs. Any unbacked number is flagged and shown on the
   summary card. This also proves the point to judges.

## Architecture

The flow:
- **App chat / S-mobiili screens:** browser → Cloudflare Worker → FastAPI (unchanged).
- **Voice:** browser ↔ ElevenLabs (WebRTC audio). ElevenLabs → our API for tools, and → our webhook after the call.
- **New data:** `data/cohorts.json` (built offline), read into memory at startup.

**New and changed parts:**

| Part | Where | Owner |
|---|---|---|
| Cohort builder | `scripts/build_cohorts.py` → `data/cohorts.json` (medians and quartiles per cohort × figure and per category; n per cohort) | Bogdan |
| Insights logic | `app/insights.py`: spend, budgets, compare, recurring | Bogdan |
| Insights API | `app/insights_api.py`, a FastAPI router included with one line in `app/main.py` | Bogdan |
| Chat tools | `app/tools.py`: wrappers so the text chat and WhatsApp get the same features | Bogdan |
| Voice API | `app/voice_agent.py`, its own router: session, tools, webhook | Christiana |
| Agent setup | ElevenLabs agent (prompt, voice, tools, limits), exported to `docs/voice/agent.json` for review | Christiana |
| Voice in the page | `app/static/oiva/voice.js`: the ElevenLabs web SDK, exposing `window.OivaVoice` (contract below) | Christiana |
| Screens | `app/templates/smobiili.html` (and partials) | S-mobiili app owner |
| Oiva animated | `app/static/oiva/oiva.js`, copied from `design/mascot` | S-mobiili app owner |
| Charts | `app/static/kchart.js`: donut and pace line added | S-mobiili app owner |
| Fixtures | `app/static/fixtures/*.json`: fake responses matching the contracts, so screens can be built before the API exists | S-mobiili app owner |

**Secrets** (Cloudflare Worker and container env; never in the repo or the page):
- `ELEVENLABS_API_KEY`
- `ELEVENLABS_AGENT_ID`
- `ELEVENLABS_WEBHOOK_SECRET`

**Security and privacy:**
- **The browser never sees the ElevenLabs key.** `POST /api/voice/session` asks ElevenLabs for a short-lived
  conversation token or signed URL.
- **Each call gets our own session token,** tied to the persona. It's passed to the agent as a *secret* dynamic
  variable, sent in tool-call headers and never shown to the model. Tool endpoints reject calls without a valid one.
- **ElevenLabs only receives tool results** (aggregated figures and sentences), never raw transactions.
- **The webhook** checks ElevenLabs' signature, then stores only the summary and the guard result. No audio is kept.
- **Limits:** session creation is rate-limited per IP, with one live call per session.
- **The microphone:**
  - Any iframe hosting the call needs `allow="microphone"`.
  - The page must be HTTPS (production) or `localhost` (development).
  - In real S-mobiili, the native app grants microphone access to the WebView: a feasibility note for the deck.

**ElevenLabs details to verify first (Christiana, in the first 30 minutes):**
- conversation tokens or signed URLs for web calls;
- secret dynamic variables in tool headers;
- the post-call webhook's signature scheme and payload;
- client tools in the web SDK;
- maximum call duration;
- English voice choice;
- Finnish support;
- data residency and retention options;
- price per minute.

## API contracts

Amounts are in euros as numbers. Every response also carries `as_of` (the demo date) and `source`. **The values in
these examples are made up; only the field names and shapes are the contract.** (The cohort size 761 is real:
35–44-year-olds in Helsinki.)

`GET /api/spend?month=2026-09`
```json
{"month": "2026-09", "total_eur": 2143.80, "prev_total_eur": 2061.55, "income_eur": 3420.00,
 "budget_total_eur": 1900.00,
 "pace": {"expected_by_today_eur": 1520.00, "spent_by_today_eur": 1610.30, "status": "over"},
 "categories": [{"category": "Restaurants", "label": "Restaurants", "basket": "everyday",
                 "spent_eur": 412.10, "prev_eur": 380.00, "budget_eur": 300.00, "pct_of_budget": 137}],
 "daily": [{"day": 1, "cumulative_eur": 54.20}],
 "bills": [{"name": "Rent", "date": "2026-09-05", "amount_eur": 950.00}]}
```

`GET /api/budget` and `POST /api/budget {"category": "Restaurants" | "all", "mode": "own" | "peers" | "helsinki" | "finland", "amount_eur": 250}`
```json
{"month": "2026-10",
 "items": [{"category": "Restaurants", "limit_eur": 296.00, "source": "peers", "spent_eur": 61.40,
            "left_eur": 234.60, "left_per_day_eur": 9.38, "status": "on_track"}],
 "presets": {"peers": {"cohort": {"age_band": "35-44", "city": "Helsinki", "n": 761},
                        "per_category": {"Restaurants": 296.00}},
             "helsinki": {"n": 5100, "per_category": {"Restaurants": 280.00}},
             "finland": {"year": 2022, "per_person": true, "per_category": {"Restaurants": 64.00}}},
 "undo": {"category": "Restaurants", "previous": null}}
```

`GET /api/compare`
```json
{"cohort": {"age_band": "35-44", "city": "Helsinki", "n": 761, "from": "2026-04-01", "to": "2026-09-30"},
 "figures": [{"id": "rent_share", "label": "Rent as share of income", "unit": "pct",
              "you": 0.28, "median": 0.33, "p25": 0.27, "p75": 0.40, "percentile": 31, "better": "lower",
              "sentence": "Your rent takes 28 % of your income. For people aged 35–44 in Helsinki it's typically 33 %."}]}
```
The `id` values are `income`, `rent_share`, `savings_rate`, `restaurants`, `shopping` and `buffer_months`. The
`unit` values are `eur`, `pct` and `months`.

`GET /api/recurring` returns `Engine.commitments()` unchanged:
- `regular_payments` is a list of `{id, name, what, direction, status, basket, per_month_eur, per_year_eur,
  usual_day, next_expected, days_to_next, last_payment, trend: {direction, change_pct, …}, actions, note?,
  unusual_bill?}`;
- plus `outgoing_per_month_eur`, `bills_per_month_eur`, `income_per_month_eur`, `subscriptions_found` and
  `card_subscriptions`.

**Voice:**
- **`POST /api/voice/session {"as": "<persona id>"}`** returns `{"agent_id", "conversation_token" (or "signed_url"),
  "expires_at", "dynamic_variables": {"user_name", "coach_name": "Oiva"}}`. The secret session variable isn't in
  the response body that page scripts can read; the server attaches it when starting the session.
- **`POST /api/voice/tools/{name}`** with header `X-Oiva-Session` returns `{"speech": "...", "data": {...},
  "ui": {"screen": "budget", "mood": "saving"}}`. `ui` is optional.
- **`POST /api/voice/webhook`** has its signature verified. It stores `{conversation_id, summary,
  unbacked_numbers: [...]}`.

**`window.OivaVoice`** (between Christiana's `voice.js` and the screens):
- `OivaVoice.start()` and `OivaVoice.stop()`.
- `OivaVoice.on("state", s)`, where `s` is `connecting`, `listening`, `thinking`, `speaking` or `ended`.
- `OivaVoice.on("caption", {who, text})`.
- `OivaVoice.on("screen", name)` and `OivaVoice.on("mood", mood)`.
- `OivaVoice.on("summary", card)`.

## Who owns what

To avoid merge conflicts, each person mostly touches only their own files. `app/main.py` gets **one
`include_router` line per new router**, added by its owner.

- **Bogdan:**
  - `scripts/build_cohorts.py` and `data/cohorts.json`;
  - `/api/recurring`, which returns `Engine.commitments()`;
  - `app/insights.py` and `app/insights_api.py`;
  - the tool wrappers in `app/tools.py`;
  - tests in `tests/test_insights.py`;
  - the Statistics Finland per-person check;
  - the WhatsApp login.
- **Christiana:**
  - the ElevenLabs agent and `docs/voice/agent.json`;
  - `app/voice_agent.py` and `app/static/oiva/voice.js`;
  - tests in `tests/test_voice_agent.py`;
  - budget alerts and the new features on WhatsApp.
- **S-mobiili app owner:**
  - Spend, Budget, Compare and Bills screens and the call view in `smobiili.html`;
  - `oiva.js` wiring and the `kchart.js` additions;
  - the fixtures;
  - the demo script and the deck.

**Rename Birky → Oiva everywhere users see it:** one PR, done first, by whoever is free. It covers the agent
prompt, the pages, WhatsApp texts and the docs. Meta template identifiers (`kurre_*`) stay. PR #60 (submission
text) and PR #70 (the older `/mobile-demo` restyle, now in conflict with #69) get folded into or redone after this
rename.

## Tonight

| When | Bogdan | Christiana | S-mobiili app owner |
|---|---|---|---|
| 20:00–20:30 | Read this; agree on the contracts | Read this; ElevenLabs hello-world agent and the checks above | Read this; send the Revolut screenshots |
| 20:30–23:00 | Cohorts, `/api/compare`, `/api/budget`, `/api/spend` | `/api/voice/session`, tool endpoints (stubs first), `voice.js` | Spend, Budget and Compare screens on fixtures; call view |
| 23:00–01:00 | `/api/recurring`; tool wrappers; tests | Tools call the real API; webhook and guard; one full voice flow | Switch fixtures to the real API; Bills screen; polish |
| 01:00–02:00 | Fixes; deploy | Fixes; record the backup video of the voice flow | Run the demo script twice; fix what breaks |
| Sun morning | — | — | Deck and video with Oiva; submit by 10:00 |

**If behind, cut in this order:** the Bills screen, the Finland per-person preset, the donut chart, WhatsApp
alerts. **Keep at all costs:** Compare, a peers budget, and one working voice flow.

**The demo voice flow:** "Hi Oiva, how am I doing this month?" → spend summary and the screen switches → "Is that a
lot for someone like me?" → comparison → "Set my restaurant budget to what's typical" → budget set, Undo shown →
Oiva asks a teaching question about the three baskets.

## Risks

| Risk | Mitigation |
|---|---|
| ElevenLabs features differ from what's assumed here | Christiana checks them first; option B is the fallback for the tools |
| Oiva says an unbacked number | `speech` lines, the prompt rule, the guard on the transcript, the flag on the summary |
| Microphone or Wi-Fi fails at the demo | Recorded backup; text chat always works |
| Merge conflicts | One owner per file; routers; the rename PR goes first |
| Running out of time | The cut order above; freeze at 02:00 |

## Calls I made (check these)

- Median, labelled "typical", rather than mean.
- A 6-month window (Apr–Sep 2026).
- Rent and utilities are shown as fixed bills, not budgeted.
- `Lifestyle` is shown as "Shopping".
- The buffer counts baskets 1 and 2.
- The savings rate counts investing as saving.
- Leave the customer out of their own cohort; widen it below 30 people.
- No grades on restaurants and shopping.
- A budget change needs no PIN, but shows Undo.
- English voice first.
- Maximum 5-minute calls.

## Still open

- The Revolut Wealth screenshots: adjust the Spend and Budget layouts to them.
- "Like Meta's AI character": I read this as a warm, playful character that reacts and talks with you. Confirm, or
  name what to copy.
- Statistics Finland per person: depends on table 14q9 (Bogdan).
