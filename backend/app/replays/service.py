import hmac
import json
import queue
import threading
import time
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.replays import core



class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


router = APIRouter(prefix="/replays", tags=["replays"], default_response_class=UTF8JSONResponse)

_assets: dict | None = None
_state: dict[str, dict] = {}
_jobs: queue.Queue[str] = queue.Queue()
_worker: threading.Thread | None = None
_start = threading.Lock()
_budget = threading.Lock()
_listing = {"at": 0.0, "rows": []}
LIST_TTL = 60


def assets() -> dict:
    global _assets
    if _assets is None:
        _assets = core.load_assets()
    return _assets


def folder(sid: str):
    return settings.replay_dir / sid


def ready(sid: str) -> bool:
    return (folder(sid) / "replay.json").exists() and (folder(sid) / "audio.mp3").exists()


def is_admin(request: Request) -> bool:
    token = settings.replay_admin_token
    if not token:
        return False
    given = request.headers.get("x-admin-token") or request.headers.get("authorization", "").removeprefix("Bearer ")
    return hmac.compare_digest(given.strip().encode(), token.encode())


def may_voice(request: Request) -> bool:
    return settings.replay_voice_public or is_admin(request)


def used_today() -> int:
    f = settings.replay_dir / "_budget.json"
    try:
        b = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return b.get("chars", 0) if b.get("date") == date.today().isoformat() else 0


def spend(n: int) -> None:
    f = settings.replay_dir / "_budget.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"date": date.today().isoformat(), "chars": used_today() + n}), encoding="utf-8")


def check_sid(sid: str) -> None:
    if not core.SID.match(sid):
        raise HTTPException(404, "unknown session")


def sessions(refresh: bool = False) -> list[dict]:
    if not settings.arena_api_key:
        return []
    if refresh or time.time() - _listing["at"] > LIST_TTL:
        try:
            rows = core.list_sessions(settings.arena_api_key, base=settings.arena_url)
        except Exception:
            rows = None
        if rows is not None:
            _listing.update(at=time.time(), rows=rows)
    return _listing["rows"]


def left_today() -> int | None:
    if settings.replay_daily_chars <= 0:
        return None
    return max(0, settings.replay_daily_chars - used_today())


def generation_info() -> dict:
    return {
        "enabled": bool(settings.arena_api_key and settings.elevenlabs_api_key),
        "public": settings.replay_voice_public,
        "left_today": left_today(),
        "session_chars": settings.replay_session_chars or None,
    }


def best_per_case(rows: list[dict], exclude=frozenset()) -> list[dict]:
    """Highest-scoring ended session per (set, case); ties go to a deal, then the newer session.
    Sessions in `exclude` (finished by hand) count as runs but are never picked as the best."""
    best: dict[tuple, dict] = {}
    runs: dict[tuple, int] = {}
    for s in rows:
        if s.get("status") != "ended":
            continue
        k = (s.get("set"), s.get("case_id"))
        runs[k] = runs.get(k, 0) + 1
        if s.get("session_id") in exclude:
            continue
        rank = (s.get("score") or 0, bool(s.get("deal")), s.get("created_at") or "")
        cur = best.get(k)
        if cur is None or rank > (cur.get("score") or 0, bool(cur.get("deal")), cur.get("created_at") or ""):
            best[k] = s
    return [{**s, "runs": runs[k]} for k, s in sorted(best.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))]


def work() -> None:
    while True:
        sid = _jobs.get()
        try:
            data = core.fetch(sid, settings.arena_api_key, settings.arena_url)
            script = core.build(data, assets(), settings.replay_session_chars)
            n = core.characters(script)
            with _budget:
                left = left_today()
                if left is not None and n > left:
                    raise RuntimeError("daily voice budget reached, try again tomorrow")
                audio, replay = core.synthesize(script, settings.elevenlabs_api_key)
                spend(n)
            out = folder(sid)
            out.mkdir(parents=True, exist_ok=True)
            (out / "audio.mp3.tmp").write_bytes(audio)
            (out / "audio.mp3.tmp").replace(out / "audio.mp3")
            (out / "replay.json.tmp").write_text(json.dumps(replay, ensure_ascii=False), encoding="utf-8")
            (out / "replay.json.tmp").replace(out / "replay.json")
            _state.pop(sid, None)
        except core.ArenaError as e:
            msg = "replay not available yet" if e.status in (403, 409) else f"arena error {e.status}"
            _state[sid] = {"status": "failed", "error": msg}
        except Exception as e:
            _state[sid] = {"status": "failed", "error": str(e)[:200]}
        finally:
            _jobs.task_done()


def ensure_worker() -> None:
    global _worker
    with _start:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=work, name="replay-worker", daemon=True)
            _worker.start()


def with_audio(replay: dict) -> dict:
    return {**replay, "audio": f"/api/replays/{replay['session_id']}/audio"}


@router.get("")
def list_replays(request: Request, all: bool = False) -> dict:
    if all and not is_admin(request):
        raise HTTPException(403, "the full session list is internal")
    cat = assets().get("catalog", {})
    manual = frozenset(cat.get("exclude_best", []))
    rows = []
    seen = set()
    ended = [s for s in sessions() if s.get("status") == "ended"]
    for s in ended if all else best_per_case(ended, manual):
        sid = s["session_id"]
        seen.add(sid)
        case = cat.get("cases", {}).get(s.get("case_id"), {})
        rows.append(
            {
                "session_id": sid,
                "case_id": s.get("case_id"),
                "set": s.get("set"),
                "title": case.get("title"),
                "type": case.get("type"),
                "suppliers": case.get("suppliers") or [],
                "rounds": s.get("rounds"),
                "end_reason": s.get("end_reason"),
                "deal": bool(s.get("deal")),
                "score": s.get("score"),
                "house_score": case.get("house"),
                "agent": cat.get("sessions", {}).get(sid, {}).get("agent"),
                "created_at": s.get("created_at"),
                "runs": s.get("runs"),
                "status": "ready" if ready(sid) else _state.get(sid, {}).get("status", "none"),
            }
        )
    if settings.replay_dir.exists():
        cached = []
        for f in settings.replay_dir.glob("*/replay.json"):
            if f.parent.name not in seen:
                try:
                    cached.append({**core.index_entry(json.loads(f.read_text(encoding="utf-8"))), "status": "ready"})
                except (OSError, ValueError, KeyError):
                    pass
        cases = {(r["set"], r["case_id"]) for r in rows}
        if not all:
            cached = [{**c, "status": "ended"} for c in cached if (c.get("set"), c.get("case_id")) not in cases]
            cached = [{**c, "status": "ready"} for c in best_per_case(cached, manual)]
        rows += cached
    return {"sessions": rows, "arena": bool(_listing["rows"]), "generation": generation_info()}


@router.get("/{sid}")
def get_replay(sid: str) -> dict:
    check_sid(sid)
    if ready(sid):
        return {"status": "ready", "replay": with_audio(json.loads((folder(sid) / "replay.json").read_text(encoding="utf-8")))}
    return {**_state.get(sid, {"status": "none"}), "generation": generation_info()}


@router.post("/{sid}", status_code=202)
def generate(sid: str, request: Request) -> dict:
    check_sid(sid)
    if not may_voice(request):
        raise HTTPException(403, "voicing is not open to visitors")
    if ready(sid):
        return {"status": "ready"}
    if _state.get(sid, {}).get("status") == "generating":
        return {"status": "generating"}
    if not generation_info()["enabled"]:
        raise HTTPException(503, "voice generation is not configured")
    if left_today() == 0:
        raise HTTPException(429, "daily voice budget reached, try again tomorrow")
    ours = {s["session_id"]: s for s in sessions()}
    if sid not in ours:
        ours = {s["session_id"]: s for s in sessions(refresh=True)}
    if sid not in ours:
        raise HTTPException(404, "unknown session")
    if ours[sid].get("status") != "ended":
        raise HTTPException(409, "session has not ended")
    _state[sid] = {"status": "generating"}
    ensure_worker()
    _jobs.put(sid)
    return {"status": "generating"}


@router.get("/{sid}/audio")
def audio(sid: str) -> FileResponse:
    check_sid(sid)
    f = folder(sid) / "audio.mp3"
    if not f.exists():
        raise HTTPException(404, "no audio yet")
    return FileResponse(f, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})
