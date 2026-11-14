"""State mirror: a fresh container (empty disk) picks up the state the last one saved."""
import time
from datetime import date

from app import store
from app.engine import Engine


class FakeRemote:
    def __init__(self):
        self.data = {}

    def get(self, url, params=None, timeout=None):
        name = url.rsplit("/", 1)[-1]
        ok = name in self.data
        return type("R", (), {"status_code": 200 if ok else 404, "text": self.data.get(name, "")})()

    def put(self, url, params=None, content=b"", timeout=None):
        self.data[url.rsplit("/", 1)[-1]] = content.decode()
        return type("R", (), {"status_code": 200})()


def test_off_without_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("STATE_REMOTE", raising=False)
    assert store.load("state.json") is None
    store.save("state.json", "{}")  # no thread, no network


def test_goal_survives_a_new_container(monkeypatch, tmp_path):
    remote = FakeRemote()
    monkeypatch.setenv("STATE_REMOTE", "1")
    monkeypatch.setenv("PUBLIC_URL", "https://example.test")
    monkeypatch.setenv("WA_VERIFY_TOKEN", "k")
    monkeypatch.setattr(store.httpx, "get", remote.get)
    monkeypatch.setattr(store.httpx, "put", remote.put)
    e1 = Engine(today=date(2026, 10, 6), state_path=tmp_path / "old" / "state.json")
    e1.create_goal("Trip", 2000)
    for _ in range(50):
        if "state.json" in remote.data and "Trip" in remote.data["state.json"]:
            break
        time.sleep(0.05)
    e2 = Engine(today=date(2026, 10, 6), state_path=tmp_path / "new" / "state.json")  # empty disk
    assert list(e2.state["goals"]) == list(e1.state["goals"]) and e2.state["goals"]


def test_names_are_safe():
    assert store.safe_name("state/CUST00613.json") == "state_CUST00613.json"
    assert store.safe_name("../../etc") == ".._.._etc"
