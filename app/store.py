"""Demo state that survives a redeploy.

Every deploy (or restart) starts a fresh container with an empty disk, which used to forget goals, the WhatsApp
connection and which messages were already answered (Meta then redelivered them, so replies came twice). When the
Worker sets STATE_REMOTE=1, each state file is also mirrored to a Durable Object behind <PUBLIC_URL>/__state/<name>
(protected by WA_VERIFY_TOKEN) and read back on first use. Without it (tests, local runs) nothing leaves the disk.
"""
from __future__ import annotations

import logging
import os
import re
import threading

import httpx

log = logging.getLogger("store")
_pending: dict[str, str] = {}
_cv = threading.Condition()
_worker: threading.Thread | None = None


def _target() -> tuple[str, str] | None:
    if os.environ.get("STATE_REMOTE") != "1":
        return None
    url, key = os.environ.get("PUBLIC_URL", ""), os.environ.get("WA_VERIFY_TOKEN", "")
    return (url.rstrip("/") + "/__state/", key) if url and key else None


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:80]


def load(name: str) -> str | None:
    """The mirrored copy of a state file, or None (no mirror, nothing stored yet, or unreachable)."""
    t = _target()
    if not t:
        return None
    try:
        r = httpx.get(t[0] + safe_name(name), params={"key": t[1]}, timeout=8)
        return r.text if r.status_code == 200 else None
    except httpx.HTTPError as e:
        log.warning("state load failed for %s: %s", name, e)
        return None


def save(name: str, text: str) -> None:
    """Mirror a state file in the background; only the latest version of each file is sent."""
    global _worker
    if not _target():
        return
    with _cv:
        _pending[safe_name(name)] = text
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, daemon=True, name="state-mirror")
            _worker.start()
        _cv.notify()


def _run() -> None:
    while True:
        with _cv:
            while not _pending:
                _cv.wait()
            batch = dict(_pending)
            _pending.clear()
        t = _target()
        if not t:
            continue
        for name, text in batch.items():
            try:
                r = httpx.put(t[0] + name, params={"key": t[1]}, content=text.encode(), timeout=8)
                if r.status_code >= 300:
                    log.warning("state save for %s returned %s", name, r.status_code)
            except httpx.HTTPError as e:
                log.warning("state save failed for %s: %s", name, e)
