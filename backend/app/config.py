from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mongodb_uri: str
    mongodb_db_name: str = "analyticsagent"

    pghost: str = "localhost"
    pgport: int = 5432
    pguser: str = "postgres"
    pgpassword: str = ""
    pgdatabase: str = "interview_prep"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    frontend_url: str = ""

    @property
    def cors_origins(self) -> list[str]:
        origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://elegant-rolypoly-d5e460.netlify.app",
        ]
        if self.frontend_url and self.frontend_url not in origins:
            origins.append(self.frontend_url.rstrip("/"))
        return origins

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.pguser}:{self.pgpassword}"
            f"@{self.pghost}:{self.pgport}/{self.pgdatabase}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
