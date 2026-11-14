"""Oiva's screens in the S-mobiili concept mock: Spend, Budget, People like me and Bills.

The page is the mock's own template (smobiili.html) with screen=<name>; the screen draws itself in the browser from
/api/<name> (Bogdan's routes) through /static/oiva/data.js, which falls back to labelled sample data until those
exist. Plan: docs/superpowers/plans/2026-11-14-smobiili-oiva-screens.md
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

SCREENS = ("spend", "budget", "compare", "bills")
TITLES = {"spend": ("Spending", "Kulutus"), "budget": ("Budget", "Budjetti"),
          "compare": ("People like me", "Vertailu"), "bills": ("Bills", "Laskut")}

router = APIRouter()
VOICE_JS = Path(__file__).resolve().parent / "static" / "oiva" / "voice.js"  # Christiana's window.OivaVoice


@router.get("/s-mobiili/voice-status")
def voice_status():
    """Tells the call view whether the real voice script exists yet; until it does, the labelled demo plays."""
    return {"voice_js": VOICE_JS.exists()}


@router.get("/s-mobiili/{screen}", response_class=HTMLResponse)
def smobiili_screen(request: Request, screen: str):
    if screen not in SCREENS:
        raise HTTPException(404)
    from .main import sm_ctx, sm_persona, templates  # here, not at import: main includes this router
    title, fi = TITLES[screen]
    return templates.TemplateResponse(request, "smobiili.html", sm_ctx(
        request, sm_persona(request), screen=screen, f={"title": title, "fi": fi}, steps=[]))
