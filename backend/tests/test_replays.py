import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.replays import core, service

SID = "0123456789abcdef"


def session() -> dict:
    sup = "sup:70110"
    body0 = (
        "Dear customer,\n\nThank you for your enquiry. Please find our Angebot below.\n\n"
        "We are pleased to submit our offer at a price of �6.05 per unit, with 30 days net.\n\n"
        "Mit freundlichen Gr��en\nThomas Brandlhuber\nOwner, Gie�erei Brandlhuber GmbH & Co. KG"
    )
    buyer = "Dear Thomas Brandlhuber,\n\nWe commit to 8150 pieces a year (fc:310010@2026-09).\n\nKind regards\nHollerau"
    return {
        "session": {
            "session_id": SID,
            "case_id": "case:P01",
            "set": "practice",
            "status": "ended",
            "end_reason": "accepted",
            "rounds": 1,
            "threads": {sup: {}},
            "result": {"deal": True, "supplier_id": sup, "score": 0.9, "terms": {"unit_price": 5.9}},
        },
        "threads": {
            sup: {
                "messages": [
                    {
                        "from": "sales@70110",
                        "body": body0,
                        "status": "open",
                        "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 6.05, "payment_days": 30}}],
                    },
                    {"from": "buyer", "body": buyer, "term_sheets": [{"terms": {"unit_price": 5.5}}]},
                    {"from": "buyer", "body": "", "action": {"type": "accept", "term_sheet_id": "offer:1"}},
                ]
            }
        },
    }


def test_condense_keeps_numbers_and_drops_boilerplate():
    text = core.condense(core.repair(session()["threads"]["sup:70110"]["messages"][0]["body"], []), 280)
    assert "€6.05 per unit" in text
    assert "enquiry" not in text and "Brandlhuber" not in text


def test_build_casts_voices_and_term_sheets():
    a = core.load_assets()
    script = core.build(session(), a, 2000)
    sup = script["speakers"]["sup:70110"]
    assert sup["contact"] == "Thomas Brandlhuber"
    assert sup["voice_id"] not in (a["voices"]["buyer"]["id"], a["voices"]["narrator"]["id"])
    accept = [t for t in script["turns"] if t.get("action") == "accept"][0]
    assert accept["terms"][0]["terms"]["unit_price"] == 6.05
    buyer = [t for t in script["turns"] if t["speaker"] == "buyer"][0]
    assert "fc:" not in buyer["say"] and "8150" in buyer["say"]
    assert core.characters(script) <= 2000


def test_unlimited_voices_every_turn_in_full():
    a = core.load_assets()
    script = core.build(session(), a)
    turns = [t for t in script["turns"] if t["kind"] == "turn"]
    assert all(t["spoken"] for t in turns)
    assert "30 days net" in turns[0]["text"] and "enquiry" not in turns[0]["text"]


def test_long_turns_split_into_small_requests():
    a = core.load_assets()
    data = session()
    long = " ".join(f"Item {i} costs €{i}.50 per unit at 30 days." for i in range(400))
    data["threads"]["sup:70110"]["messages"][0]["body"] = long
    script = core.build(data, a)
    assert core.characters(script) > core.CHUNK_CHARS * 3
    chunks = core.chunks_of(script)
    assert len(chunks) > 3
    assert all(sum(len(x["text"]) for _, x in c) <= core.CHUNK_CHARS for c in chunks)
    voiced = {i for c in chunks for i, _ in c}
    assert voiced == {i for i, t in enumerate(script["turns"]) if t.get("say")}


def test_best_per_case():
    rows = [
        {"session_id": "a" * 16, "set": "practice", "case_id": "case:P01", "status": "ended", "score": 0.5},
        {"session_id": "b" * 16, "set": "practice", "case_id": "case:P01", "status": "ended", "score": 0.8},
        {"session_id": "c" * 16, "set": "practice", "case_id": "case:P01", "status": "running", "score": 0.9},
        {"session_id": "d" * 16, "set": "sandbox", "case_id": "case:D01", "status": "ended", "score": None},
    ]
    best = service.best_per_case(rows)
    assert [(b["session_id"], b["runs"]) for b in best] == [("b" * 16, 2), ("d" * 16, 1)]
    best = service.best_per_case(rows, exclude={"b" * 16})
    assert [(b["session_id"], b["runs"]) for b in best] == [("a" * 16, 2), ("d" * 16, 1)]


def test_distinct_voices_in_one_rfq():
    a = core.load_assets()
    sups = ["sup:70210", "sup:70240", "sup:70250"]
    cast = core.cast(sups, {}, a["voices"])
    assert len({c["voice"]["id"] for c in cast.values()}) == 3


def test_endpoints_without_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "replay_dir", tmp_path)
    monkeypatch.setattr(settings, "arena_api_key", "")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "replay_voice_public", False)
    monkeypatch.setattr(settings, "replay_admin_token", "")
    with TestClient(app) as client:
        assert client.get("/replays/not-a-session").status_code == 404
        assert client.post(f"/replays/{SID}").status_code == 403
        assert client.get("/replays?all=1").status_code == 403
        listing = client.get("/replays")
        assert listing.headers["content-type"] == "application/json; charset=utf-8"
        monkeypatch.setattr(settings, "replay_admin_token", "secret")
        assert client.post(f"/replays/{SID}", headers={"X-Admin-Token": "wrong"}).status_code == 403
        assert client.post(f"/replays/{SID}", headers={"X-Admin-Token": "secret"}).status_code == 503
        monkeypatch.setattr(settings, "replay_voice_public", True)
        assert client.post(f"/replays/{SID}").status_code == 503
        assert client.get(f"/replays/{SID}").json()["status"] == "none"
        (tmp_path / SID).mkdir()
        (tmp_path / SID / "audio.mp3").write_bytes(b"ID3")
        replay = core.finish(core.build(session(), core.load_assets(), 2000), "audio.mp3", 1.0)
        (tmp_path / SID / "replay.json").write_text(json.dumps(replay), encoding="utf-8")
        r = client.get(f"/replays/{SID}").json()
        assert r["status"] == "ready" and r["replay"]["audio"] == f"/api/replays/{SID}/audio"
        assert client.get(f"/replays/{SID}/audio").status_code == 200
        listing = client.get("/replays").json()
        assert any(s["session_id"] == SID for s in listing["sessions"])
        assert listing["generation"]["left_today"] is None and listing["generation"]["session_chars"] is None
        for _ in range(300):
            assert client.get(f"/replays/{SID}/audio").status_code == 200
