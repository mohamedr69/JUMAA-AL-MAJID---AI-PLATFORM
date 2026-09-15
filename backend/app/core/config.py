import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def expand_path(value: str | None) -> str | None:
    """`~` and `%USERPROFILE%` / `$HOME` in a configured path, so one .env
    serves every machine the same OneDrive is synced to."""
    if value is None:
        return None
    value = os.path.expandvars(os.path.expanduser(value.strip()))
    return value or None


def find_synced_folder(name: str) -> str | None:
    """Where OneDrive put a synced SharePoint library on this machine.

    A library synced from SharePoint lands as `<Organisation>\\<Library> -
    <Folder>` directly under the user's profile (`C:\\Users\\x\\Juma Al
    Majid\\SSD FIRE ALARM PROJECTS - Fire Alarm 2021 Projects`), and a
    personal or business OneDrive keeps its folders under the path in the
    `OneDrive` / `OneDriveCommercial` variables. The folder is looked for by
    its name in all of those, exact name first, then as a prefix (a library
    renamed "... 2021 Projects (1)" by a second sync still counts)."""
    roots: list[Path] = []
    for candidate in (os.environ.get("OneDriveCommercial"), os.environ.get("OneDrive"), str(Path.home())):
        if candidate and Path(candidate).is_dir() and Path(candidate) not in roots:
            roots.append(Path(candidate))
    for pattern in (name, f"{name}*", f"*{name}*"):
        for root in roots:
            for depth in ("", "*/"):
                try:
                    hits = sorted(p for p in root.glob(f"{depth}{pattern}") if p.is_dir())
                except OSError:
                    hits = []
                if hits:
                    return str(hits[0])
    return None


# backend/, found from this file, so `.env` and the default data folders do not
# depend on the folder the server was started from.
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
# Where Tesseract's Windows installer puts it.
_TESSERACT_CANDIDATES = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8")

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

    # The project archive: the SharePoint library OneDrive syncs to every
    # office machine. Left unset, it is found by name on whichever machine
    # the platform runs (see `find_synced_folder`); set it only for an
    # archive kept somewhere unusual. `~` and %VARS% are expanded.
    # Production should replace this with Microsoft Graph search against
    # SharePoint -- see app/services/ep_resolver.py docstring.
    projects_root: str | None = None
    projects_root_name: str = "SSD FIRE ALARM PROJECTS - Fire Alarm 2021 Projects"
    # Tests turn this off: their archives are the temporary folders they build.
    projects_root_autodetect: bool = True

    @model_validator(mode="after")
    def _resolve_projects_root(self) -> "Settings":
        self.projects_root = expand_path(self.projects_root)
        if not self.projects_root and self.projects_root_autodetect:
            self.projects_root = find_synced_folder(self.projects_root_name)
        return self

    # Where documents uploaded through the platform are kept: a project's
    # own folder under here. Never the archive itself -- the platform reads
    # the archive, it does not write to it.
    uploads_root: str = "uploads"

    # Path to tesseract.exe. Unset: found on the PATH or where the Windows
    # installer puts it, so a new machine needs no setting.
    tesseract_cmd: str | None = None

    @model_validator(mode="after")
    def _find_tesseract(self) -> "Settings":
        self.tesseract_cmd = expand_path(self.tesseract_cmd)
        if not self.tesseract_cmd:
            import shutil

            if not shutil.which("tesseract"):
                found = next((p for p in _TESSERACT_CANDIDATES if p.is_file()), None)
                self.tesseract_cmd = str(found) if found else None
        return self

    # --- The company library ------------------------------------------
    # Everything a submittal needs that is not about a particular project:
    # the company documents, the templates and the manufacturers'
    # datasheets. One local folder with a fixed structure, not the synced
    # archive -- see app/services/company_library.py for the layout and why.
    #
    # Unset means `backend/library`, resolved from the code rather than from
    # the working directory, so it does not matter where uvicorn is started.
    library_root: str | None = None

    # A manufacturer is normally *discovered*: a folder under
    # `library/datasheets/` is a library, named for the brand. This is the
    # override, for a library kept somewhere else -- relative to the library
    # root, or absolute. DATASHEET_LIBRARIES='{"MENVIER": "D:/menvier"}'
    datasheet_libraries: dict[str, str] = {}

    # The submittal builder: the company documents that go into every
    # material submittal (Company Profile, Trade License, ISO and
    # civil-defence certificates, test reports, ...) and the templates the
    # package is built from (cover page, index and dividers). Relative to
    # the library root, or absolute. Read, never written to.
    submittal_library: str = "submittal"

    # Derived data -- the datasheet index, above all. Never inside the
    # library: that folder can be read-only or synced. Unset means
    # `backend/.cache`.
    cache_root: str | None = None

    # How long a library's file listing is trusted before it is walked
    # again. Looking a BOQ's fifty parts up used to re-walk the folder fifty
    # times; over a synced drive that was the whole cost of the page. A
    # datasheet dropped in shows up within this many seconds, and Reindex on
    # the library page is the way to see it at once. 0 disables the throttle.
    library_rescan_seconds: float = 60.0

    # --- Selective AI assistance (app/ai) ---------------------------------
    # Off by default: every deterministic path runs without it. When on, a
    # model is asked only about issues the router marks eligible -- an
    # unreadable quantity cell, an unlabelled design sheet -- with the
    # evidence for that issue alone, and its answer is a proposal an
    # engineer accepts, never a value written by itself.
    ai_enabled: bool = False
    # Which way the model is reached: "claude-code" (Claude through the Claude
    # Code CLI, on the Claude subscription signed in on this server -- no API
    # key), "claude" (Anthropic API) or "openai" (OpenAI API). Each has its
    # own provider class in app/ai/provider.py behind one interface.
    ai_provider: str = "claude-code"
    # Tasks switched off on this server whatever else allows them, comma
    # separated (e.g. "answer_clauses,read_field"): the deployment control
    # for rolling a task back without a code change. See app/ai/evaluation.py.
    ai_disabled_tasks: str = ""
    # The Claude Code program for "claude-code": a name on the PATH or the full
    # path to claude.exe. Sign in once with `claude` as the user the server runs as.
    ai_claude_cli: str = "claude"
    # One Claude Code call, start to finish (it starts a process and may read an image).
    ai_cli_timeout_s: float = 300.0
    # The key, for the API providers only. Put it here (backend/.env is
    # gitignored) or let the vendor SDK read OPENAI_API_KEY or ANTHROPIC_API_KEY.
    ai_api_key: str | None = None
    # Model IDs are configuration, verified against the account's own model
    # list rather than assumed. The small tier reads a single cell; the
    # standard tier is the one escalation for a reply the small one botched.
    # For "claude-code" these are Claude Code model names: sonnet, opus, haiku.
    ai_model_small: str = "sonnet"
    ai_model_standard: str = "opus"
    # Reasoning depth for these short extraction tasks.
    ai_effort: str = "low"
    ai_timeout_s: float = 60.0
    ai_max_concurrency: int = 2
    ai_max_retries: int = 3
    # Budgets. Estimated cost uses the prices below (per million tokens, in
    # the account's billing currency); reconcile against invoices.
    ai_max_input_tokens_per_task: int = 6000
    ai_max_output_tokens_per_task: int = 800
    ai_max_calls_per_document: int = 12
    ai_max_calls_per_project_per_day: int = 60
    ai_max_cost_per_job: float = 0.50
    ai_max_elapsed_s_per_job: float = 120.0
    ai_max_escalations_per_document: int = 2
    # Prices per million tokens, in the account's billing currency. Left at
    # zero on purpose: a made-up price is worse than none. Set them from the
    # vendor's current pricing page and the cost column and the per-job cost
    # cap start working; until then only the call-count and time limits bind.
    ai_price_input_per_million: float = 0.0
    ai_price_output_per_million: float = 0.0
    ai_price_cached_input_per_million: float = 0.0
    # How long a validated result is reused for the same evidence.
    ai_cache_ttl_days: int = 90
    # Field-level OCR confidence below which a DRF value is offered to the
    # model for a second reading (calibrated on the ten reviewed DRFs:
    # correct values read at 77-96, noise at 24-30, EP-31725's plot at 54).
    ai_ocr_review_confidence: float = 60.0

    # --- Compliance statements ------------------------------------------
    # A compliance statement is answered clause by clause. Python reads the
    # clauses, reuses answers from the company's past statements and writes
    # the workbook; the model is asked only about clauses nothing settles,
    # many at a time. These bound that one kind of call.
    ai_compliance_batch_clauses: int = 25
    ai_compliance_max_input_tokens: int = 9000
    ai_compliance_max_output_tokens: int = 3000
    ai_compliance_max_calls_per_statement: int = 12
    ai_compliance_max_elapsed_s: float = 600.0
    # A past answer is reused without asking when its clause reads this much
    # like the new one (0-1, word-level similarity).
    compliance_reuse_similarity: float = 0.86
    # Below this a past clause is not the same clause at all.
    compliance_hint_similarity: float = 0.6

    # --- The compliance knowledge base --------------------------------
    # The source collection: the folder holding the Compliance Response
    # Database workbook (Compliance_Response_Database.xlsx) and its exports.
    # An import source only -- the records are copied into the application
    # database, which is what autofill queries. Unset means no import can
    # run on this machine; the knowledge already imported keeps working.
    compliance_knowledge_source: str | None = None
    # Unset source: use the `data base` folder at the top of the repository
    # when it holds the workbook, so a fresh clone has its knowledge base.
    # Tests turn this off.
    compliance_knowledge_autodetect: bool = True
    # On startup, import the knowledge base in the background when nothing
    # has been imported yet and the source is reachable.
    compliance_knowledge_import_on_start: bool = True

    # --- Fallback: the library as it was filed in the archive -----------
    # Used only for what the local library does not hold, so a machine that
    # has not copied it across yet behaves exactly as before.
    # `scripts/sync_library.py` makes that copy.
    archive_datasheet_libraries: dict[str, str] = {
        "EDWARDS": "Systems/01- FAVE/01- Edwards - UL&EN/01- EST4",
    }
    archive_submittal_library: str = "Systems/01- FAVE/01- Edwards - UL&EN/01- EST4/submittal builder"


@lru_cache
def get_settings() -> Settings:
    return Settings()
