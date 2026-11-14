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
