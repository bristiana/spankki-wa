# S-mobiili screens for Oiva: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spend, Budget, Compare and Bills screens and a "Talk to Oiva" call view inside the `/s-mobiili/` mock,
working on sample data now and on Bogdan's and Christiana's APIs as soon as they exist.

**Architecture:**
- **Pages:** a small router serves `/s-mobiili/{spend|budget|compare|bills}` with the existing `smobiili.html`.
- **Drawing the screens:** `screens.js` draws each screen from JSON, fetched by `data.js`. It tries the live
  `/api/...` route first and falls back to a fixture in `app/static/fixtures/`, telling the screen which one it got.
- **Voice:** the call view talks only to `window.OivaVoice` (Christiana's contract). Until her `voice.js` exists, a
  labelled demo stub plays a scripted call.

**Tech Stack:** FastAPI and Jinja (existing), plain JavaScript with no build step, `kchart.js` (existing charts),
`oiva.js` (mascot from `design/mascot`), pytest with TestClient, and Playwright for the browser checks.

**Spec:** `docs/superpowers/specs/2026-11-14-oiva-wealth-and-voice-design.md`

## Global Constraints

- **Concept label:** every screen keeps "Concept prototype · not the real S-mobiili".
- **No logos:** no S-Pankki logos or images; our own icons only, drawn in the `icon()` macro's style.
- **Amounts:** in the app's style, `1 234.56 €`, and never broken across lines (`nb`, or ` ` in JS).
- **Accessibility:** tap targets at least 44 px; WCAG AA text contrast in light and dark mode; respect reduced
  motion.
- **Sample data is labelled:** "Sample data until the live service is connected".
- **API contracts:** exactly the shapes in the spec's "API contracts" section. Amounts in euros; every response has
  `as_of` and `source`.
- **Who and how many:** every comparison names the cohort and its size n.
- **No judging:** no grades, red marks or "should" on restaurants and shopping.
- **Budgets:** a budget change shows Undo for 10 seconds.
- **Voice:** maximum 5 minutes per call. A first-call line names ElevenLabs as the voice provider; S-Pankki stores
  no audio.
- **Files:** only this plan's files, plus the single `include_router` line in `app/main.py`.

## Review Focus

1. **The live API answers with an error, or a field is missing:** the screen shows a short "couldn't load" line and
   the rest still works. Never a blank screen or `undefined`. Pinned in Task 1 (data layer) and Task 4 (renders a
   partial payload).
2. **A negative savings rate (10 % of people), no budget set, or a zero budget:** shown as "−13 %", "No budget
   yet" and "0 €", with no `NaN` and no divide-by-zero bar. Pinned in Tasks 4–6.
3. **A widened cohort** (fewer than 30 people): the source line says it was widened. Pinned in Task 6.
4. **Microphone denied, or voice not connected:** the call view says so and offers the text chat; no endless
   "connecting". Pinned in Task 8.
5. **Dark mode and long amounts** (`12 345.67 €`) in narrow rows: no overflow at 390 px. Checked in Task 9.

---

### Task 1: Contracts, fixtures and the data layer

**Files:**
- Create:
  - `app/static/fixtures/spend.json`, `budget.json`, `compare.json` and `recurring.json`: Antti-like sample values
    with `"fixture": true`. `recurring.json` is a
    real `Engine.commitments()` output for Antti, saved as JSON;
  - `app/static/oiva/data.js`;
  - `tests/contracts.py` (shape checkers, reusable on the live API);
  - `tests/test_sm_contracts.py`.

**Interfaces:**
- **Produces:** `tests/contracts.py` with these functions, each raising `AssertionError` with the missing key's
  name:
  - `check_spend(d: dict)`;
  - `check_budget(d: dict)`;
  - `check_compare(d: dict)`;
  - `check_recurring(d: dict)`.
- **Produces:** `window.OivaData` with:
  - `get(name, params?) -> Promise<{data, live: bool, error?: string}>`, where `name` is `spend`, `budget`,
    `compare` or `recurring`, and `params` becomes the query string (for example `{month: "2026-09"}`);
  - `setBudget({category, mode, amount_eur?}) -> Promise<{data, live}>`.

- [ ] **Step 1: Write the failing tests.**
  - `test_fixtures_match_contracts`: loads each fixture and runs its checker.
  - `test_compare_has_the_five_figures`: the ids are exactly `{income, rent_share, savings_rate, restaurants,
    shopping, buffer_months}` and every figure has `you, median, p25, p75, percentile, unit, better, sentence`.
  - `test_checker_rejects_missing_field`: `check_spend({})` raises an error naming `month`.
- [ ] **Step 2: Run** `python -m pytest tests/test_sm_contracts.py -q`. Expected: FAIL, because there are no
  fixtures or checkers yet.
- [ ] **Step 3: Write the checkers and the four fixtures.**
  - The checkers mirror the spec's JSON key for key.
  - The fixtures use plausible numbers, labelled as samples.
  - The compare fixture's cohort is `35-44 / Helsinki / n 761`, with one figure at a negative savings rate.
  - The budget fixture has one category with no budget.
- [ ] **Step 4: Write `data.js`.**
  - `get` fetches `/api/{name}`. If it isn't ok or the JSON is malformed, it fetches
    `/static/fixtures/{name}.json` and returns `live: false` with the reason.
  - `setBudget` POSTs JSON to `/api/budget`. If that route doesn't exist, it applies the change to an in-memory
    copy of the fixture and returns `live: false`.
- [ ] **Step 5: Run the tests.** Expected: PASS.
- [ ] **Step 6: Commit** `feat(s-mobiili): API contracts, sample fixtures and data layer`.

### Task 2: Routes and the screen shell

**Files:**
- Create: `app/smobiili_screens.py` (`router = APIRouter()`).
- Create: `tests/test_sm_screens.py`.
- Modify: `app/main.py` (one `include_router` line next to the S-mobiili routes).
- Modify: `app/templates/smobiili.html`:
  - a `screen in SCREENS` branch with `<main id="screen" data-screen="{{ screen }}">` and a loading line;
  - a back arrow;
  - home tiles linking to the new screens;
  - a "Talk to Oiva" button;
  - the script tags for `data.js`, `screens.js`, `kchart.js`, `oiva.js` and `voice.js` (only if it exists), then
    `voice-stub.js`.

**Interfaces:**
- **Consumes:** `sm_persona`, `sm_ctx` and `templates` from `app.main`. Import them inside the handler, so
  `main.py` keeps a single include line.
- **Produces:**
  - `GET /s-mobiili/{screen}` for `screen` in `SCREENS = ("spend", "budget", "compare", "bills")`, rendering
    `smobiili.html` with `screen=…` and the titles `Spending · Kulutus`, `Budget · Budjetti`, `People like me ·
    Vertailu` and `Bills · Laskut`. Anything else returns 404.
  - Home tiles with ids `tile-spend`, `tile-budget`, `tile-compare` and `tile-bills`.
  - A call button with id `talk-oiva`.

- [ ] **Step 1: Write the failing tests.**
  - `test_screen_routes_render`: each of the 4 routes returns 200, contains `data-screen="{name}"` and the concept
    label.
  - `test_unknown_screen_is_404`: `/s-mobiili/nope` returns 404.
  - `test_home_links_to_screens_and_talk_button`: the home page has the 4 tile ids, links to the routes, and has
    `id="talk-oiva"`.
  - `test_existing_flow_still_works`: one call into the payment flow from `tests/test_smobiili_mock.py` still
    passes.
- [ ] **Step 2: Run** `python -m pytest tests/test_sm_screens.py -q`. Expected: FAIL (404).
- [ ] **Step 3: Write the router and the template branches.** The persona query (`?persona=`) carries through all
  links via `home_url`.
- [ ] **Step 4: Run** `tests/test_sm_screens.py tests/test_smobiili_mock.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(s-mobiili): screen routes, home tiles and Talk to Oiva button`.

### Task 3: Charts — donut and pace line

**Files:**
- Modify: `app/static/kchart.js` (two new `spec.type` branches; existing types unchanged).

**Interfaces:**
- **Produces:**
  - `birkyChart(host, {type: "donut", title, subtitle, labels, values, unit: "€"})`: a ring with a centre total,
    and a legend that has values.
  - `birkyChart(host, {type: "pace", title, subtitle, labels: ["1", …], values: [cumulative…], budget: number,
    today: index})`: the spend line, a dashed even-pace line from 0 to the budget, a marker for today, and the line
    colour turning amber when over pace.

- [ ] **Step 1: Write the failing check.** A Playwright script in the scratchpad loads a test page with each spec
  and asserts the donut has `labels.length` arcs and the pace chart has 2 paths and 1 marker.
- [ ] **Step 2: Run it.** Expected: FAIL (the types fall through to the existing columns).
- [ ] **Step 3: Write `donut(svg, spec, W)` and `pace(svg, spec, W)`.** Use the existing `el` and `num` helpers,
  the colour tokens from `base.html`, and `aria-label` text listing the values.
- [ ] **Step 4: Run the check, and the chat chart tests** (`python -m pytest -q -k chart`). Expected: PASS.
- [ ] **Step 5: Commit** `feat(charts): donut and pace line`.

### Task 4: Spend screen

**Files:**
- Create: `app/static/oiva/screens.js` (with `renderSpend(main, d, live)`).
- Create: `app/static/oiva/screens.css`.

**Interfaces:**
- **Consumes:** `OivaData.get("spend")` from Task 1; the `donut` and `pace` charts from Task 3.
- **Produces:** `OivaScreens.render(name)`, called by the template on load, and `OivaScreens.show(name)`, which the
  voice "screen" event uses in Task 8.
- **What the screen shows:**
  - a month header with the total and the change from last month;
  - the pace chart against the budget total;
  - a donut of the top categories;
  - category rows (spent, a budget bar or "No budget yet", the change from last month, a basket dot);
  - a bills strip;
  - the "Sample data…" note when `live` is false.
- **A month switcher:** ‹ and › buttons that call `OivaData.get("spend", {month})`, from 12 months back up to the
  current month.
- **Tapping a category** opens the chat with "Show my {category} spending by month". The existing Explore tool
  draws the trend, so no new API field is needed.

- [ ] **Step 1: Write the failing check.** A Playwright check on `/s-mobiili/spend` (served by TestClient routing,
  as before) asserts:
  - the total text and the category rows are present;
  - "No budget yet" shows for the fixture category with no budget;
  - with `/api/spend` returning `{}` (mocked through route interception), the page shows "Couldn't load your
    spending" and no `undefined` or `NaN` anywhere in the body text.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Write `renderSpend`.** Escape all text. Format amounts like the chat's `nb()`. Guard every
  division.
- [ ] **Step 4: Run the check.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(s-mobiili): Spend screen`.

### Task 5: Budget screen

**Files:**
- Modify: `screens.js` (`renderBudget`) and `screens.css`.

**Interfaces:**
- **Consumes:** `OivaData.get("budget")` and `OivaData.setBudget()`.
- **What the screen shows:** one row per category with spent, limit, left, per day and status. Tap a row to open a
  sheet with 4 choices, each showing its amount and where it comes from:
  - "Your own" (number input);
  - "People like me" (cohort and n);
  - "Helsinki area";
  - "Finland" ("per person" or "per household" from `per_person`).
- **After saving:** the row updates, a toast with **Undo** shows for 10 s, and Undo calls `setBudget` with the
  previous value. "Set all to people like me" at the top.

- [ ] **Step 1: Write the failing check.** Playwright:
  - choosing "People like me" for Restaurants sets the row's limit to the fixture's peers value and shows Undo;
  - Undo restores "No budget yet";
  - a typed own amount of 0 shows "0 €" and no bar overflow.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Write `renderBudget` and the choice sheet.** Focus moves into the sheet and back; Esc closes it.
- [ ] **Step 4: Run the check.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(s-mobiili): Budget screen with people-like-me and regional presets`.

### Task 6: People-like-me screen

**Files:**
- Modify: `screens.js` (`renderCompare`) and `screens.css`.

**What the screen shows:**
- **Top:** "Compared with people aged {age_band} in {city} ({n} people, {from}–{to})".
- **A card per figure:**
  - your value and the typical value;
  - a band bar showing the 25th–75th range, the median tick and a "You" dot, clamped to the bar;
  - the `sentence`;
  - for `better: "lower"` or `"higher"`, a neutral "common target" line only where the spec allows.
- **No colour judging** on restaurants and shopping.
- **A widened cohort** (`cohort.widened: true`) gets a line saying so.

- [ ] **Step 1: Write the failing check.** Playwright:
  - 6 cards render;
  - the savings-rate card shows "−13 %" for the fixture;
  - the "You" dot stays inside the bar even when `you` is below `p25`;
  - with `widened: true` added through interception, the widened line shows.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Write `renderCompare`.** Format `pct` as a whole percent with a real minus sign, `months` with one
  decimal and `eur` with `nb`.
- [ ] **Step 4: Run the check.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(s-mobiili): People like me screen`.

### Task 7: Bills screen

**Files:**
- Modify: `screens.js` (`renderBills`) and `screens.css`.

**What the screen shows,** from `Engine.commitments()` (Cristiana's #64):
- **A header:** bills per month, and outgoing per month against income.
- **A row per regular payment:** name, the monthly amount, and either "next {date}" or the `note` for utilities.
- **Chips:**
  - the trend's own `direction` text when it isn't "about the same";
  - "Unusual bill" when `unusual_bill` is set;
  - a "Stopped" or "Paused" chip from `status`.
- **A footer line:** "We checked your card payments: no subscriptions found" when `subscriptions_found` is false.

- [ ] **Step 1: Write the failing check.** Playwright:
  - a row shows for each fixture regular payment (rent, utilities, investing, salary);
  - the utilities row shows its `note` rather than a date;
  - with `unusual_bill` set through interception, an "Unusual bill" chip shows;
  - the no-subscriptions line shows.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Write `renderBills`.**
- [ ] **Step 4: Run the check.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(s-mobiili): Bills screen from Engine.commitments()`.

### Task 8: Talk to Oiva — the call view

**Files:**
- Create: `app/static/oiva/oiva.js`, copied unchanged from `design/mascot/oiva.js` on `origin/design/mascot`.
- Create: `app/static/oiva/call.js` and `app/static/oiva/voice-stub.js`.
- Modify: `smobiili.html` (the call overlay markup, inside `.phone`).

**Interfaces:**
- **Consumes:**
  - `window.OivaVoice` per the spec: `start()`, `stop()`, and `on(event, fn)` for the events `state`, `caption`,
    `screen`, `mood` and `summary`;
  - `Oiva.create(el, {palette: "red", state: "idle"})` and `.setState(name)`;
  - `OivaScreens.show(name)` from Task 4.
- **Produces:**
  - **Call states → Oiva's animation:** `connecting`→`thinking`, `listening`→`listening`, `thinking`→`thinking`,
    `speaking`→`teaching`, `ended`→`idle`. A `mood` event calls `setState(mood)` when the mood is one of Oiva's
    states.
  - **Captions:** the last 2 lines, `aria-live="polite"`.
  - **Buttons:** Mute and End, at least 44 px.
  - **Timer:** stops the call at 5:00.
  - **First call:** the provider line, shown once per session (`sessionStorage`).
  - **The stub** (loaded only if `window.OivaVoice` is undefined): a scripted demo call of the spec's demo flow,
    with captions and screen switches, labelled "Demo playback · voice not connected". Its `start()` resolves.
  - **Errors:** a real `OivaVoice` whose `start()` rejects shows "Can't start the call. You can still type to
    Oiva", with a link to the chat.

- [ ] **Step 1: Write the failing checks.** Playwright:
  - tapping `talk-oiva` opens the overlay with the provider line;
  - the stub's `speaking` state puts Oiva in `teaching` (read through a `data-state` attribute on the mascot host);
  - a `screen: "compare"` event shows the Compare screen behind the call;
  - End closes the overlay;
  - with an injected `OivaVoice` whose `start()` rejects, the error line and the chat link show.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Write `call.js` and `voice-stub.js`, and add the overlay markup.**
- [ ] **Step 4: Run the checks.** Expected: PASS. Then run `python -m pytest -q`; everything passes.
- [ ] **Step 5: Commit** `feat(s-mobiili): Talk to Oiva call view with a demo stub until voice is connected`.

### Task 9: Check it on screen, then push

- [ ] **Step 1: Walk through all screens and the call** at 390×844, in light and dark mode, through Playwright and
  in your browser pane. Check:
  - no console errors;
  - no tap target under 44 px;
  - no text below AA contrast;
  - no horizontal overflow;
  - a `12 345.67 €` amount fits.
- [ ] **Step 2: Fix what fails, and rerun** `python -m pytest -q`.
- [ ] **Step 3: Commit, push the branch, and open a draft PR.** The PR lists:
  - the fixtures-to-API switch;
  - Bogdan's routes, which the screens expect at `/api/spend`, `/api/budget`, `/api/compare` and `/api/recurring`;
  - Christiana's `window.OivaVoice` contract.
