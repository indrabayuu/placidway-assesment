from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), extra="ignore")
    database_url: str = Field(default="postgresql://placidway:placidway@localhost:5432/placidway", repr=False)
    openai_base_url: str = ""
    openai_model: str = ""
    openai_api_key: str = Field(default="", repr=False)
    openai_reasoning_effort: str = "low"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "inception/mercury-decide:free"
    openrouter_api_key: str = Field(default="", repr=False)
    auth_owner_secret: str = Field(default="", repr=False)
    auth_client_secret: str = Field(default="", repr=False)
    cookie_secure: bool = False
    session_days: int = 7
    data_dir: Path = Path("../../data")
    reports_dir: Path = Path("../../reports")
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_mode: Literal["dense", "bm25", "hybrid"] = "hybrid"
    refresh_interval_seconds: int = 86400
    crawl_min_delay: float = 1.0
    provider_timeout: float = 90.0
    api_concurrency: int = Field(default=2, ge=1, le=8)
    evaluation_concurrency: int = Field(default=2, ge=1, le=8)
    judge_concurrency: int = Field(default=1, ge=1, le=4)
    eval_max_improvement_rounds: int = Field(default=10, ge=1, le=10)

    @field_validator("openrouter_base_url", mode="before")
    @classmethod
    def default_judge_url(cls, value):
        return value or "https://openrouter.ai/api/v1"

    @property
    def generation_ready(self) -> bool:
        return bool(self.openai_base_url and self.openai_model and self.openai_api_key)

    @property
    def judge_url(self) -> str:
        base = self.openrouter_base_url.rstrip("/")
        return base if base.endswith("/systemone") else base + "/systemone"


@lru_cache
def settings() -> Settings:
    return Settings()
