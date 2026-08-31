import os
from pathlib import Path
from dotenv import load_dotenv

SYNC_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SYNC_DIR.parent
DATA_DIR = SYNC_DIR / "data"
CONFIG_DIR = SYNC_DIR / "config"

load_dotenv(REPO_ROOT / ".env")


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Settings:
    def __init__(self) -> None:
        self.educamadrid_username = _require("EDUCAMADRID_USERNAME")
        self.educamadrid_password = _require("EDUCAMADRID_PASSWORD")
        self.survey_url = _require("EDUCAMADRID_SURVEY_URL")
        self.survey_id = _require("EDUCAMADRID_SURVEY_ID")
        self.app_api_url = os.getenv("APP_API_URL", "http://localhost:8000").rstrip("/")
        self.admin_email = os.getenv("ADMIN_EMAIL", "admin@school.local")
        self.admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        self.headless = os.getenv("EDUCAMADRID_HEADLESS", "true").strip().lower() not in {"0", "false", "no"}

        self.data_dir = DATA_DIR
        self.pending_responses_path = DATA_DIR / "pending_responses.json"
        self.imported_ids_path = DATA_DIR / "imported_ids.json"
        self.log_path = DATA_DIR / "sync.log"

        mapping_path = CONFIG_DIR / "field_mapping.json"
        if not mapping_path.exists():
            raise RuntimeError(
                f"Missing {mapping_path}. Copy config/field_mapping.example.json to "
                "config/field_mapping.json and fill in the real question codes first."
            )
        self.field_mapping_path = mapping_path

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
