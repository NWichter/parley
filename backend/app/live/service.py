"""Live demo: a negotiation streamed turn by turn (server-sent events), voiced as each turn arrives.

Engines: "replay" streams a recorded session at speaking pace, so the page, the stream and the voices can be
shown without touching the arena. Bidders that need the arena or a model ("claude", "custom") are accepted
and described, but only run once an engine for them is enabled.
"""

import json
import threading
import time
import uuid
from collections.abc import Iterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.replays import core

router = APIRouter(prefix="/live", tags=["live"])

CLAUDE_BIDDERS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]
PRESET_BIDDERS = [
    "mirror",
    "holder",
    "floor_stop",
    "short_fuse",
    "auction",
    "index_lover",
    "cash_constrained",
    "dup_punisher",
    "announced_steps",
]
WORDS_PER_SECOND = 2.6
MAX_SESSIONS = 4
_sessions: dict[str, dict] = {}
_lock = threading.Lock()
_tts_used = {"chars": 0}


class Bidder(BaseModel):
    kind: str = Field("preset", pattern="^(preset|claude|custom|replay)$")
    preset: str | None = None
    model: str | None = None
    name: str | None = Field(None, max_length=60)
    role: str | None = Field(None, max_length=40)
    style: str | None = Field(None, max_length=400)
    firmness: float = Field(0.5, ge=0, le=1)
    patience: int = Field(6, ge=1, le=10)


class LiveRequest(BaseModel):
    case_id: str = Field("case:D02", max_length=40)
    bidders: list[Bidder] = Field(default_factory=lambda: [Bidder()], max_length=3)
    replay: str | None = Field(None, max_length=40)


class SpeakRequest(BaseModel):
    text: str = Field(..., max_length=900)
    voice_id: str = Field(..., max_length=40)


@router.get("/config")
def config() -> dict:
    return {
        "enabled": settings.live_enabled,
        "tts": bool(settings.live_tts and settings.elevenlabs_api_key),
        "engines": ["replay"],
        "bidders": {"preset": PRESET_BIDDERS, "claude": CLAUDE_BIDDERS, "custom": True},
    }


def _recorded(sid: str) -> dict:
    path = settings.replay_dir / sid / "replay.json"
    if not path.exists():
        raise HTTPException(404, f"no recorded replay {sid}")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/sessions")
def start(req: LiveRequest) -> dict:
    if not settings.live_enabled:
        raise HTTPException(503, "the live demo is switched off")
    engines = {b.kind for b in req.bidders}
    if engines - {"replay"} and not req.replay:
        raise HTTPException(501, "live bidders need the arena engine, which is not enabled yet; pick a recorded run")
    with _lock:
        if len(_sessions) >= MAX_SESSIONS:
            raise HTTPException(429, "too many live sessions, try again in a minute")
        sid = uuid.uuid4().hex[:12]
        _sessions[sid] = {"replay": _recorded(req.replay or ""), "request": req.model_dump(), "at": time.time()}
    return {"id": sid, "engine": "replay"}


def _events(sid: str) -> Iterator[str]:
    state = _sessions.get(sid)
    if not state:
        yield f"event: error\ndata: {json.dumps({'detail': 'unknown session'})}\n\n"
        return
    rep = state["replay"]
    speakers = {k: dict(v) for k, v in rep.get("speakers", {}).items()}
    voices = core.load_assets().get("voices", {})
    for key in ("buyer", "narrator"):
        if key in speakers and not speakers[key].get("voice_id"):
            speakers[key]["voice_id"] = (voices.get(key) or {}).get("id")
    try:
        yield f"event: start\ndata: {json.dumps({'speakers': speakers, 'title': rep.get('title'), 'case_id': rep.get('case_id')})}\n\n"
        for i, turn in enumerate(rep.get("turns", [])):
            yield f"event: turn\ndata: {json.dumps({'index': i, **turn})}\n\n"
            words = len((turn.get("text") or "").split())
            time.sleep(min(12.0, max(1.5, words / WORDS_PER_SECOND)))
        yield f"event: result\ndata: {json.dumps(rep.get('result', {}))}\n\n"
    finally:
        with _lock:
            _sessions.pop(sid, None)


@router.get("/sessions/{sid}/events")
def events(sid: str) -> StreamingResponse:
    return StreamingResponse(_events(sid), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/speak")
def speak(req: SpeakRequest) -> StreamingResponse:
    """One turn as speech (ElevenLabs streaming TTS), only when switched on and within the character budget."""
    if not (settings.live_tts and settings.elevenlabs_api_key):
        raise HTTPException(403, "live voices are switched off")
    voices = {v.get("id") for v in core.all_voices(core.load_assets())} if hasattr(core, "all_voices") else None
    if voices is not None and req.voice_id not in voices:
        raise HTTPException(400, "unknown voice")
    with _lock:
        if settings.live_tts_chars and _tts_used["chars"] + len(req.text) > settings.live_tts_chars:
            raise HTTPException(429, "the voice budget is used up")
        _tts_used["chars"] += len(req.text)

    def stream() -> Iterator[bytes]:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{req.voice_id}/stream?output_format=mp3_44100_64"
        body = {"text": req.text, "model_id": settings.live_tts_model}
        with httpx.stream("POST", url, json=body, headers={"xi-api-key": settings.elevenlabs_api_key}, timeout=60) as r:
            r.raise_for_status()
            yield from r.iter_bytes()

    return StreamingResponse(stream(), media_type="audio/mpeg")
