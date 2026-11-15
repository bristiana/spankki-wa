# Feasibility: one page

## Architecture
```
WhatsApp (Meta Cloud API) ──/webhook──┐
S-mobiili WebView (/chat?embed=1) ────┼─> FastAPI in a Cloudflare Container ─> Claude tool use (24 tools) ─> deterministic engine (pandas)
S-mobiili home cards (/api/widgets) ──┘        └ number guard + guardrails             └ S-Pankki data · Statistics Finland · ETF prices · Enable Banking (PSD2)
```

| Part | Fact | Source |
|---|---|---|
| Engine | Baskets, safe to spend and save, bills, what-if, backtests, peers. Deterministic, 815 tests passing | `app/engine.py`, `tests/` |
| AI | `claude-opus-5-5` at low effort, max 6 tool rounds, usually 2 calls per answer, prompt caching on | `app/agent.py` (`MODEL`, `MAX_TOOL_ROUNDS`, `cache_control`) |
| Tools | 24, all over the engine; `explore_my_data` takes enum parameters only, so no code or SQL comes from the model | `app/tools.py`, `app/bills.py` (`TOOL`) |
| Number guard | Every figure in a reply is matched to tool results or the user's words. On a mismatch: one rewrite, and the result is shown in "How I worked this out" | `app/guard.py`, `app/agent.py`, `app/explain.py` |
| Hosting | Cloudflare Worker in front of a Python container. State lives in a Durable Object, so it survives a redeploy. Cron at 07, 12 and 17 UTC | `worker/index.ts`, `wrangler.jsonc`, `Dockerfile` |
| Voice | Web: ElevenLabs `eleven_multilingual_v2` for speech, Scribe for the mic. WhatsApp voice notes: Whisper on Workers AI | `app/tts.py`, `app/stt.py`, `app/voice.py` |

## Data flow
1. A message arrives through the WhatsApp webhook (HMAC `X-Hub-Signature-256` checked) or the web chat.
2. PINs, CVVs, card numbers, codes and passwords are detected before the AI is called. A message that has one never reaches the model, and Oiva asks the person to delete it. IBANs and personal IDs are redacted (`app/guardrails.py` `secrets`, `redact`; `app/main.py`).
3. Claude calls tools. Tool text that reads like an instruction is defanged before it reaches the model (`guardrails.defang`).
4. The engine computes. The model writes. The guard checks the figures. Guardrails rewrite once any "I paid it", urgency or return promise (`app/guardrails.py` `reply_problems`).
5. Money moves only on a confirm page (`/pay`, `/save`, `/invest`, `/stop`, the S-mobiili Face ID mock). Double confirms and overdrafts are refused (`app/engine.py` `confirm`).

## Privacy, GDPR and WhatsApp
- **Consent:**
  - The connect screen says WhatsApp messages pass through Meta's servers (`app/templates/connect.html`).
  - `/privacy` names Anthropic as the AI and Meta for WhatsApp (`app/main.py` `PRIVACY`).
- **Your rights:**
  - Write **my data** for a JSON export (`/app/export.json`).
  - Write **STOP** to switch Oiva off and delete the chat history (`switch_off` in `app/main.py`).
  - Linked banks: read only, 90 days, removable on `/bank` (`app/enable_banking.py`).
- **Minimisation:**
  - The peer benchmark keeps only p25, median and p75 for groups of 30 or more customers (`app/peers.py` `MIN_GROUP`, `data/peer_benchmark.json`).
  - Bill photos are kept as fields only, for 24 h (`app/bills.py`).
- **WhatsApp rules:**
  - Free-form messages only within 24 h of the person's last message. No templates.
  - Proactive sends are off unless `DEMO_PROACTIVE=1` (`app/main.py` `wake_one`, `docs/aprobare-whatsapp.md`).
- **Needed for production, not built:**
  - A verified WhatsApp Business number and Meta business verification
  - DPAs with Anthropic and Meta, and a DPIA
  - A retention policy (STOP deletes the chat, not every stored marker)
  - Bank SSO instead of the demo login

## Advice vs information
- Oiva informs and the customer chooses. The terms page says "not financial advice, not a loan offer" (`app/main.py` `TERMS`).
- Fund names are "Demo Funds" over real VWCE and XEON prices. The risk profile only labels the information (`app/engine.py`, `README.md`).
- Code rewrites urgency ("act now") and promised returns (`app/guardrails.py`).
- Fraud, disputes and debt go to S-Pankki customer service, and Oiva never makes up a phone number.
- Where exactly the advice line sits under Finnish law needs S-Pankki compliance. We designed for the information side.

## Cost per active user per month (estimate)
Assumptions:
- 30 AI answers a month, 2 calls each.
- About 8.2k fixed tokens per call: a 13.7k-character system prompt plus 19.4k characters of tool schemas (`app/agent.py`, `app/tools.py`).
- About 4k tokens of context, history and tool results.
- About 300 output tokens per call.
- 10 spoken replies of 300 characters, and 5 ten-second mic inputs.

| Item | Price used (read 15 Nov 2026) | Per user per month |
|---|---|---|
| Claude Opus 5.5 | $4 / M input, $20 / M output, $0.20 / M cache read, $5 / M 5-min cache write ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)) | **≈ $1.20 – 2.40** (warm cache to cold: ≈ $0.04 – 0.08 per answer) |
| Same on Sonnet 5.5 | $2 / $10, cache read $0.10 | ≈ $0.60 – 1.20 |
| ElevenLabs speech (`eleven_multilingual_v2`) | $0.08 / 1K characters ([API pricing](https://elevenlabs.io/pricing/api)) | ≈ $0.24 |
| ElevenLabs Scribe (mic) | $0.20 / hour | < $0.01 |
| WhatsApp service messages | Free for the first 1,000 a month per business number, charged after that since 1 Oct 2026 ([Meta](https://developers.facebook.com/docs/whatsapp/pricing)) | ≈ $0 at pilot scale; the rate at scale is on Meta's rate card, not checked |
| Cloudflare | Workers Paid $5 a month base, plus container time ([pricing](https://developers.cloudflare.com/containers/pricing/)) | shared, small |

**About $1.45–2.65 per active user a month on Opus.** Learn, the basket game and onboarding need no AI (`app/learn.py`, `app/quiz.py`, `app/onboarding.py`), so they cost nothing.

This is an estimate: the app does not log token usage yet. That's the first thing to add for a pilot.

## Path into S-mobiili
1. **Cards:** S-mobiili draws `/api/widgets` natively. These are engine numbers with no AI, cached for 5 minutes.
2. **Chat:** a WebView at `/chat?embed=1`. A card tap deep-links with `&q=` (`docs/integration.md`).
3. **Confirm:** swap the demo PIN and Face ID pages for S-mobiili's own strong customer authentication. They already report back with `postMessage("done")`.
4. **Data:**
   - Today the engine reads a CSV.
   - In production it reads S-Pankki's account and card APIs, with the bank session in place of the demo login.
   - The multi-bank view already runs on real PSD2 data through Enable Banking.
5. **WhatsApp:** a verified S-Pankki business number. The same backend serves both channels.
