import pytest

from app import main


@pytest.fixture(autouse=True)
def persona_isolation(tmp_path, monkeypatch):
    """Extra personas write their state under tmp, and no engine or coach survives from another test."""
    monkeypatch.setattr(main, "STATE_DIR", tmp_path / "state")
    main._engines.clear()
    main._coaches.clear()
    main.session_persona.clear()
    yield
    main._engines.clear()
    main._coaches.clear()


@pytest.fixture(autouse=True)
def chats_isolation(tmp_path, monkeypatch):
    """Coaches keep their conversations under tmp, never in data/."""
    from app import agent
    monkeypatch.setattr(agent, "chats_path", lambda e: tmp_path / "chats" / f"{e.state_path.stem}-chats.json")
