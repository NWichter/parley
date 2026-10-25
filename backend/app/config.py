from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILES = (Path(__file__).resolve().parents[2] / ".env", Path(__file__).resolve().parents[1] / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore", env_ignore_empty=True)

    anthropic_api_key: str = Field("", validation_alias=AliasChoices("ANTHROPIC_API_KEY", "ANTHROPIC_KEY"))
    plan_model: str = "claude-opus-5-5"
    write_model: str = "claude-sonnet-5-5"

    arena_api_key: str = ""
    arena_url: str = "https://arena.tacto.ai"
    elevenlabs_api_key: str = ""
    replay_dir: Path = Path("/data/replays")
    replay_session_chars: int = 0
    replay_daily_chars: int = 0
    replay_voice_public: bool = False
    replay_admin_token: str = ""
    live_enabled: bool = False
    live_tts: bool = False
    live_tts_model: str = "eleven_flash_v2_5"
    live_tts_chars: int = 0


settings = Settings()
