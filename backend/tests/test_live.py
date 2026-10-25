from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def test_config_lists_bidders():
    r = client.get("/live/config")
    assert r.status_code == 200
    body = r.json()
    assert "claude-sonnet-5-5" in body["bidders"]["claude"]
    assert body["bidders"]["custom"] is True


def test_start_is_off_by_default(monkeypatch):
    monkeypatch.setattr(settings, "live_enabled", False)
    assert client.post("/live/sessions", json={}).status_code == 503


def test_speak_is_off_without_switch(monkeypatch):
    monkeypatch.setattr(settings, "live_tts", False)
    assert client.post("/live/speak", json={"text": "hi", "voice_id": "x"}).status_code == 403


def test_recorded_run_streams(tmp_path, monkeypatch):
    rep = {"speakers": {"buyer": {"label": "Parley", "kind": "buyer", "voice": "Daniel"}}, "turns": [], "result": {"deal": True}}
    (tmp_path / "abc").mkdir()
    (tmp_path / "abc" / "replay.json").write_text(__import__("json").dumps(rep), encoding="utf-8")
    monkeypatch.setattr(settings, "live_enabled", True)
    monkeypatch.setattr(settings, "replay_dir", tmp_path)
    r = client.post("/live/sessions", json={"replay": "abc", "bidders": [{"kind": "claude", "model": "claude-haiku-5-5"}]})
    assert r.status_code == 200
    stream = client.get(f"/live/sessions/{r.json()['id']}/events").text
    assert "event: start" in stream and "event: result" in stream
