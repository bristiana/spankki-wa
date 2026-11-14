# Fleet check: the engine on all 5,100 customers

The demo runs on one persona (CUST04831). `scripts/fleet_check.py` runs the same engine on every customer in the
challenge dataset (3,626,755 transactions), on the demo date 2026-10-06, and calls every read-only method the tools
and the dashboard use: spending (all, by category, by merchant type), baskets, upcoming bills, next payday, safe to
spend, safe to save, can I afford, simulate cut, spender profile, subscriptions, monthly flow, full picture,
portfolio, first-home readiness, salary nudge, unusual-spend alert and the whole dashboard overview. `explore` is
called too once it exists. Each engine gets a throwaway state file; the run checks that none was written.

```
python scripts/fleet_check.py --data-dir <dataset> [--sample N] [--jobs 6]
```

Full results: [`fleet-check.json`](fleet-check.json) (counts, failure classes with example customers, distributions,
timing). About 7 minutes on 6 processes; median 0.43 s per customer for all 20 calls.

## Before and after

| | Before | After |
|---|---|---|
| Customers with an exception | 481 (9.4%) | **0** |
| Exceptions | 1,578 in 11 classes | **0** |
| Customers with an impossible number | 0 | 0 |

## What broke, and the fix

| Failure class | Customers | Cause | Fix |
|---|---|---|---|
| `simulate_cut`, `salary_nudge`, `home_readiness`: ZeroDivisionError | 444 | No restaurant spending in the last 3 months, so the cut saves 0 € and the real-price backtest divided by 0 paid in | Backtests return nothing for 0 paid in; saving paces of 0 € are left out |
| `next_payday`, `safe_to_spend`, `safe_to_save`, `afford`, `overview`, `salary_nudge`: AttributeError | 35 | No regular salary: fewer than 4 salary months in the last 6 (17) or amounts that vary more than 25% (20), some both | `next_payday` returns `date: null` with `not_enough_data`; safe to spend covers the next 30 days and says so (`horizon_days`, `not_enough_data`); afford gives `days_to_payday: null` |
| `home_readiness`: ValueError "no regular salary" | 35 | Same customers; the tool returned an error | Returns `not_enough_data` and no loan range instead of an error |
| `home_readiness`: ValueError "year must be in 1..9999" | 3 | A restaurant cut of a few cents a month made the deposit take 10,000+ years, past the last valid date | `reached_by` is null beyond 50 years (also in goal plans) |

Silent errors found by reading the engine against the fleet data (no exception, but a wrong or misleading number):

- **Month-end paydays moved to the 28th.** About 1,000 customers are paid on the 28th-31st; the engine capped every
  due day at 28, so payday came up to 3 days early and the daily safe amount was overstated. Due days now use the real
  last day of the month.
- **Short history diluted averages.** Monthly averages always divided by 3 or 6 months, even when the history was
  shorter. They now divide by the months the history covers, and `monthly_flow` says when it had fewer.
- **Savings rate with no income.** With no income in the window the rate divided by 1 €, giving rates in the thousands of percent. It is
  now `null` and the "steady saver" trait is not given.
- **Peers from another age band.** The peer benchmark is built for ages 20-29 (535 customers), and the Statistics
  Finland comparison used the persona's age for everyone. With `Engine(age=...)` a customer outside the band gets no
  peer comparison, and Finland's figures use their own age.
- **Negative "covered" share.** With an overdrawn account the first-home deposit showed a negative percentage covered.
  It is clamped to 0-100.

Each class has a regression test built from a small synthetic history in `tests/test_fleet.py`. The demo persona's
outputs are identical to before, except one added field (`horizon_days: 9` in safe to spend).

## What the fleet looks like

| | Customers |
|---|---|
| Regular payday found | 5,065 (35 get "not enough data") |
| Rent found as a recurring payment | 4,772 (218 have no housing payments at all) |
| Negative balance today | 37 |
| Negative safe to spend (shown as negative, 0 € a day) | 136 |
| Spent and invested more than came in, last 3 months | 1,759 |

| | p5 | median | p95 |
|---|---|---|---|
| Safe to spend until payday (€) | 317 | 5,590 | 29,106 |
| Safe per day (€) | 33 | 581 | 3,119 |
| Days to payday | 9 | 9 | 16 |
| Mandatory & regular share of spending | 44% | 68% | 91% |
| Mandatory & irregular share | 1% | 13% | 30% |
| Slips through your fingers share | 4% | 18% | 37% |

## Honest limits

- **Zero exceptions is not proof the numbers are right.** The checks catch crashes, NaN/inf, absurd sizes and
  internal contradictions (a negative total shown as a positive daily amount, bills in the past, basket shares not
  adding to 100%, "fits" when it does not, saving more than is safe to spend). They cannot tell whether a correct-looking
  number is the right one for that person.
- **Safe to spend is large for most customers.** The synthetic balances grow over the two years (median 6,160 €), and
  the engine treats the whole current account as available after bills, needs and the buffer. For the median customer
  that makes "safe to spend" mostly savings. A real deployment should separate a savings buffer first.
- **Only read-only calls are checked across the fleet.** Payments, savings, goals and subscription stops are covered by
  the existing tests on the persona and the new synthetic tests, not run per customer.
- **Subscriptions are the demo's synthetic ones for every customer** (the dataset has none), so that part of each
  result is the same for all.
- **Peers exist only for ages 20-29.** Everyone else gets no peer comparison until the benchmark is built per age band.
