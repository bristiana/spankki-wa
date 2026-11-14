# Demo video

`Kurre-demo.mp4`: 2:50, 1920x1080, 30 fps, H.264, narrated. A real recording of the app running locally on the
challenge's synthetic S-Pankki data; the only edits are that the waits for AI replies play 4x faster.
`voiceover.txt` has the narration lines with their timestamps.

## How it is made

- `app/static/video/stage.html` is the 1920x1080 stage: title, "Meet Kurre", "Why another app?", a split screen
  (headline on the left, a phone on the right with the real `/`, `/chat` and `/app` pages in an iframe),
  manifesto, "Simulated vs Real" and the end card.
- `scripts/record_video.py` drives the stage with Playwright (Microsoft Edge, headless), clicks through the chat
  exactly as a customer would (chips, quick buttons, PIN sheets), and captures frames over CDP
  (`Page.startScreencast`) with their timestamps. It logs every wait for an AI reply, speeds those parts up 4x with
  ffmpeg (trim + setpts + concat), and mixes in one narration line per scene from Windows SAPI
  ("Microsoft Hazel Desktop").

## Re-record

```bash
cp .env.example .env            # ANTHROPIC_API_KEY is required; leave WhatsApp settings empty
rm -f data/state.json
WA_TOKEN= DEMO_PHONE= PUBLIC_URL=http://127.0.0.1:8103 python -m uvicorn app.main:app --port 8103 &
python scripts/record_video.py --url http://127.0.0.1:8103
```

- `--no-voice`: captions only, no narration.
- `--edit-only`: re-edit the last capture (`out/video/recording.json`) without recording again.
- Raw frames, the uncut capture and the WAVs go to `out/video/` (not committed).
- Restart the server with a fresh `data/state.json` before each take, so goals and payments start clean.

Needs Windows (for System.Speech), ffmpeg on PATH and `pip install playwright` with Microsoft Edge installed.
Replies come from the live model, so wording changes between takes; the numbers come from the engine and stay the
same. The captions in `STEPS` (`scripts/record_video.py`) quote those engine numbers.
