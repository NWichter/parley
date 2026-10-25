from anthropic import Anthropic

from app.config import settings

_client: Anthropic | None = None


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key, timeout=120)
    return _client


def ask(prompt: str, system: str | None = None, model: str | None = None, max_tokens: int = 1024) -> str:
    msg = client().messages.create(
        model=model or settings.write_model,
        max_tokens=max_tokens,
        system=system or "",
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")
