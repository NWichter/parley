from fastapi import FastAPI

from app.config import settings
from app.live.service import router as live
from app.replays.service import router as replays

app = FastAPI(title="Parley API")
app.include_router(replays)
app.include_router(live)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "llm": bool(settings.anthropic_api_key)}
