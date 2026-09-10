from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Engineering Project Platform"
    app_tagline: str = "Engineering a Safer Tomorrow"
    company_name: str = "Al Arabia for Safety & Security LLC"

    database_url: str = "sqlite:///./ep_platform.db"

    secret_key: str = "dev-secret-key-change-me-in-production"
    access_token_expire_minutes: int = 30
    cookie_name: str = "access_token"
    cookie_secure: bool = False

    max_failed_login_attempts: int = 5
    lockout_minutes: int = 15

    default_admin_email: str = "admin@ep-platform.com"
    default_admin_password: str = "ChangeMe123!"

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
