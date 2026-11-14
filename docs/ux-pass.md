# Mobile UX pass

Walked every page and flow as a first-time user at 390×844 with touch, light and dark (Playwright on Edge):
landing, Connect + consent, Add another bank, web chat (every chip, "More ideas", onboarding, basket game, rent
reminder → Pay → PIN → done → back in chat, the three alerts), invest/save/stop confirm pages, My money, Explore
(every dropdown), the S-mobiili concept page with its sheet, "Viewing as", `/chat?embed=1`. Dashboard, Explore and
chat checked for Aino and Helena (`?as=CUST00613`). Six live AI questions in total.

Each page was measured for horizontal overflow, tap targets under 44 px, amounts split across lines, WCAG text
contrast and console errors, before and after.

## Findings → fixes

| # | Finding | Fix |
|---|---|---|
| 1 | An unknown or used confirm link (`/pay/<id>`, `/save/…`, `/invest/…`, `/stop/…`) showed raw JSON `{"detail":"This link has expired."}` at desktop width | Friendly page in the app's style ("This link has expired… Nothing was paid or moved"), with Back to the chat / My money. Any other 404, 4xx or 500 a browser hits gets the same kind of page; API and webhook callers still get JSON. A 500 never shows a trace |
| 2 | The PIN sheet in the chat loaded the confirm page with the full site header, nav and footer inside it: two S-Pankki bars, one over the other | Pages opened in the sheet (`?web=1`) drop the site chrome. The sheet's own top bar is now a light "🔒 S-Pankki" header with a 44 px close button. Tapping outside the panel or pressing Esc also closes it |
| 3 | Confirm pages had no way out but the browser's back button | A Cancel button under the confirm button. Inside the sheet it closes the sheet; on its own it goes back to the chat or to WhatsApp |
| 4 | The done page in the web chat said "You can go back to the chat." with no button (a dead end when opened on its own) | Always a Back to the chat (or Back to WhatsApp) button, plus Open My money outside the sheet. The sheet now stays open 2.2 s instead of 1.4 s so the receipt can be read |
| 5 | Amounts broke across lines ("1 771.69 /€", "13.99 /€") in chat bubbles, the dashboard and the S-mobiili cards | Every amount shown on a web page keeps its spaces non-breaking: `money_nb()` and the `nb` filter in templates, `nb()` in the chat and card JavaScript. WhatsApp text is unchanged |
| 6 | AI replies mixed currency styles: "€379,899.51" next to the app's "379 899.51 €" | The prompt asks for the app's style, and `agent.euro_style()` rewrites any "€1,234.56" / "1,234.56€" in a reply to "1 234.56 €" before it is sent (web chat and WhatsApp) |
| 7 | A long AI answer scrolled to its last line, so the reader landed in the middle of it | A reply taller than the chat view scrolls its first line into view; short replies still go to the bottom |
| 8 | "More ideas" added more chips to the same horizontal row off-screen, so a tap looked like nothing happened | Expanded ideas wrap into a scrollable block over the input; picking one folds it back. The collapsed row fades at the right edge to show that it scrolls |
| 9 | Tap targets under 44 px: nav links (32 px), "Viewing as" (31 px), chat chips (37 px), in-bubble buttons (42 px), card Pay button (29 px), Custom dates, sheet close (40 px) | All 44 px or more; the audit finds none left |
| 10 | Dark mode: negative amounts and "6.0×" in `#c0392b` on the dark card (contrast 3.06); landing links in browser-default blue (1.97) | A `--neg` colour token (light `#b3261e`, dark `#ff8a80`); links use the brand colour. No text under WCAG AA left, apart from disabled buttons (exempt) |
| 11 | Quick-reply buttons in old messages stayed active: tapping "Pay rent now" twice made two payment links | After one is tapped, that message's buttons are disabled and the chosen one stays marked |
| 12 | The typing state was a plain "Kurre is typing…" line, not lined up with the bubbles; a non-200 reply showed "Connection problem" | Three animated dots next to Kurre's avatar, announced to screen readers (`role=status`) and static under reduced motion. A server error shows a clear sorry line |
| 13 | The on-screen keyboard could cover the input on browsers that don't resize the layout | `interactive-widget=resizes-content` in the viewport tag, and the chat follows `visualViewport` height and keeps the newest message in view |
| 14 | `/favicon.ico` 404 on every page (the only console error) | Kurre SVG favicon and `theme-color`. The console is clean apart from the intended 404 status of the error page |
| 15 | No visible focus style on the green header; current page not marked in the nav | A 3 px `:focus-visible` ring everywhere (light on the header); `aria-current` and a soft highlight on the current tab |
| 16 | `prefers-reduced-motion` covered only the landing squirrel and the S-mobiili sheet | One global rule also covers the typing dots and every transition |
| 17 | Landing on a phone: a 270 px squirrel under the text pushed every feature card below the fold, and the two secondary links were 17 px tall blue text | Smaller squirrel above the headline; the links are 44 px brand-coloured rows |
| 18 | Mixed per-month wording on My money: "€/mo", " / month", "a month" | "a month" everywhere; the basket amount and its share sit on two lines, so the label column keeps its width |
| 19 | Unclear PIN copy: "Please enter 4 digits.", a visible PIN, no hint that it's a demo | "The PIN is 4 digits, like 1234. Please try again." (linked with `aria-describedby`), a masked numeric field, and "Demo: any 4 digits work. No real money moves." |
| 20 | `/stop/<id>` returned a 500 if the bank refused (for example, a subscription already stopped) | Same "Not done" page as pay, save and invest |
| 21 | S-mobiili concept page: the "Kurre is offline" text when the cards fail didn't say what to do | "Kurre's cards could not load. Tap Ask below to chat instead."; Esc closes the sheet |

Tests: `tests/test_ux.py` covers the friendly pages (unknown link on all four confirm routes, POST to an unknown link,
unknown page, 500 without a trace, JSON kept for API callers), the bad PIN copy, the sheet without site chrome, Cancel
and Back, non-breaking amounts, the stop refusal, the favicon and current tab, and `euro_style`. 211 tests pass.

## Left as is, and why

- **Chips scroll sideways when collapsed.** One row keeps the conversation tall on a phone. The fade and the
  wrapped "More ideas" view make the rest findable.
- **"Viewing as" takes one row above the chat.** Judges need it to switch customers. It is hidden in `embed=1`,
  which is the real S-mobiili case.
- **Any 4-digit PIN is accepted.** It's a demo with no real money, and the page now says so.
- **Negative "Left over" on My money for some months.** That is the real data (investing plus spending above income
  over 3 months). The red colour is the signal.
- **Pages opened from chat buttons (My money, Explore) in the sheet keep their own links.** A link to the chat from
  inside the sheet opens a second chat there. Rare in the demo flows, and fixing it needs a navigation model the
  prototype doesn't have.
- **Charts are not rewritten.** The kchart labels use the short "1.9k" style for axes on purpose, and the
  dark-green "Mandatory & regular" bar in dark mode is a fill, not text.
