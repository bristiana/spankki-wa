# Pre-stage checklist

## 1. Keys and config (the day before)
- [ ] The `ANTHROPIC_API_KEY` secret is set on the Worker. Test it: ask any question in `/chat` and get an answer.
- [ ] `ELEVENLABS_API_KEY` is set.
  - Test it: tap play on an answer and hear the voice.
  - Watch the cap of 20,000 characters a day (`app/tts.py` `DAILY_CHARS`). Don't burn it in rehearsals.
- [ ] `WA_TOKEN` is valid (the system user token expires 13 Jan 2027).
- [ ] `WA_APP_SECRET` is set. Without it the webhook signature check is skipped (`app/whatsapp.py` `verify_signature`).
- [ ] `WA_DISPLAY_NUMBER` is set, so S-mobiili shows **Back to WhatsApp** after Face ID.
- [ ] `DEMO_PHONE` is the stage phone, and `/demo?key=<WA_VERIFY_TOKEN>` opens.
- [ ] The live site serves the latest build. Bump `INSTANCE` in `wrangler.jsonc` only if a fresh container is needed (`README.md`, Deploy).

## 2. Reset and warm-up (30 to 10 minutes before)
- [ ] From the stage phone, write "hi" to the Oiva bot.
  - This opens the 24-hour window.
  - `/demo/remind` doesn't check the window, and Meta rejects the message if it's closed (`app/main.py` `demo_remind`).
- [ ] Open `/demo/reset` and tap **Palauta demo / Reset the demo**. This resets the balance, goals, games and chats for every customer, then asks the stage questions once in the background (warm-up, about 40 s; progress at `/demo/warm`).
- [ ] Check that the WhatsApp connection is still there after the reset (`docs/aprobare-whatsapp.md` §6).
- [ ] A container restart also wipes the in-memory app-chat mirror (`app/mirror.py`). Don't redeploy after warm-up.
- [ ] Wake the container: open `/s-mobiili/` and `/chat` once. It stays warm for 1 hour after the last request (`worker/index.ts`).
- [ ] Run every scripted question once, in order (`docs/pitch/demo-script.md`).
  - Note the figures shown.
  - Check that the answers don't trail "The basket game is paused …" (audit item 5).
  - Fix with a reset if they do.
- [ ] Take a screenshot of the WhatsApp rent reminder and the "Rent paid: …" message, for the no-network fallback.
- [ ] **Reset demo again**, then send only "hi" so the window stays open. Don't run the rent reminder yet.

## 3. Network
- [ ] The venue Wi-Fi works on both devices. A phone hotspot is ready as the second route.
- [ ] Local fallback runs on the laptop:
  `uvicorn app.main:app --port 8000` with `.env`, then open `http://127.0.0.1:8000/s-mobiili/` (`README.md`, Run it). It still needs internet for Claude and ElevenLabs. Offline it only shows the AI-free parts (cards, Learn, the game).
- [ ] Phone mirroring (or a camera) has been tested on the projector.

## 4. Devices
- [ ] S-mobiili at 360 px, language EN, customer as decided (Antti is the default; see the open decision in the script).
- [ ] Do not open the Minä tab (the profile name doesn't match yet).
- [ ] Volume up, silent mode off, Do Not Disturb on for other apps. Browser notifications are off.
- [ ] The mic permission was granted in warm-up, so no permission prompt appears on stage.

## 5. Backup video
- [ ] Record a fresh screen capture of the full script during warm-up, under 4 minutes, and keep it on the laptop desktop.
- [ ] Don't use `docs/demo/Kurre-demo.mp4`: it shows "Kurre", Aino and old pages. Dropping or re-recording it is an open decision.

## 6. Claims to check before saying them
- [ ] "S-mobiili has no spending-by-category view and no mascot, only a virtual assistant." This comes from the audit's brand research and isn't backed by anything in the repo. Check it on a real S-mobiili before stage.
- [ ] Test count (815) is still current: run `python -m pytest -q` on the branch that is deployed.
