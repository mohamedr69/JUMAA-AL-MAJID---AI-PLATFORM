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

    # Local-filesystem dev/test shim for the project archive (synced OneDrive
    # tree). Production should replace this with Microsoft Graph search
    # against SharePoint -- see app/services/ep_resolver.py docstring.
    projects_root: str | None = None

    # Where documents uploaded through the platform are kept: a project's
    # own folder under here. Never the archive itself -- the platform reads
    # the archive, it does not write to it.
    uploads_root: str = "uploads"

    # Path to tesseract.exe. Only needed if it's not already on PATH.
    tesseract_cmd: str | None = None

    # Each manufacturer's datasheet library: the folder its datasheet PDFs
    # live in, relative to PROJECTS_ROOT (or absolute). Keyed by the brand as
    # the DRF and the BOQ spell it. The Edwards entry is the folder the
    # platform owner designated as the permanent reference for Edwards
    # datasheets; override or extend with DATASHEET_LIBRARIES='{"...": "..."}'.
    datasheet_libraries: dict[str, str] = {
        "EDWARDS": "Systems/01- FAVE/01- Edwards - UL&EN/01- EST4",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
