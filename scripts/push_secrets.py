"""Upload the non-empty secrets from .env to the Cloudflare Worker (encrypted), without printing them.

Usage: python scripts/push_secrets.py
"""
import json
import subprocess
import tempfile
from pathlib import Path

from dotenv import dotenv_values

KEYS = ["ANTHROPIC_API_KEY", "WA_TOKEN", "WA_PHONE_NUMBER_ID", "WA_APP_SECRET", "WA_VERIFY_TOKEN",
        "DEMO_PHONE", "WA_DISPLAY_NUMBER", "EB_APP_ID", "EB_PRIVATE_KEY",
        "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_DAILY_CHARS"]

env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
secrets = {k: env[k] for k in KEYS if env.get(k)}
print("uploading:", ", ".join(secrets) or "nothing (fill .env first)")
if secrets:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(secrets, f)
    try:
        subprocess.run(["wrangler", "secret", "bulk", f.name], check=True, shell=True)
    finally:
        Path(f.name).unlink()
