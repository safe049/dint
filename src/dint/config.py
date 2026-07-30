"""Application configuration loaded from environment variables / .env file."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/dint/config.py -> project root is three levels up.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings for dint.

    Values are read from environment variables, with a local ``.env`` file at
    the project root taking precedence over the process environment.
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM -------------------------------------------------------------
    openai_api_key: str = "sk-replace-me"
    openai_base_url: str = "https://api.openai.com/v1"
    dint_model: str = "gpt-4o-mini"
    reflect_model: str = ""

    # --- Storage ---------------------------------------------------------
    database_url: str = "dint.db"

    # --- Behaviour -------------------------------------------------------
    max_tool_rounds: int = 8
    max_tool_calls_per_turn: int = 4      # ← 新增：单轮对话总工具调用上限
    max_reflect_updates: int = 4          # ← 新增：reflection 每类最多更新几条    
    web_search_results: int = 5
    dint_temperature: float = 0.7

    @property
    def db_path(self) -> Path:
        """Resolve the SQLite path relative to the project root."""
        p = Path(self.database_url)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def effective_reflect_model(self) -> str:
        return self.reflect_model or self.dint_model


@lru_cache
def get_settings() -> Settings:
    return Settings()