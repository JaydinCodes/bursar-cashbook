"""Application locations shared by development and the packaged Windows app.

Mutable accounting records never belong beside the executable. Set
``CASHBOOK_APP_DATA_DIR`` to isolate a developer or test run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "BursarCashbook"


def _default_app_data_dir() -> Path:
    override = os.getenv("CASHBOOK_APP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    # ``python -m unittest discover`` must never create a real user's AppData
    # files merely because FastAPI's startup hook is exercised by TestClient.
    if "unittest" in sys.modules:
        import tempfile
        return Path(tempfile.gettempdir()) / f"BursarCashbook-tests-{os.getpid()}"
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


APP_DATA_DIR = _default_app_data_dir().resolve()
DATA_DIR = APP_DATA_DIR / "data"
DATABASE_PATH = DATA_DIR / "cashbook.db"
CONFIG_DIR = Path(os.getenv("CASHBOOK_CONFIG_DIR", APP_DATA_DIR / "config")).expanduser().resolve()
CASHBOOK_DIR = Path(os.getenv("CASHBOOK_LIVE_DIR", APP_DATA_DIR / "cashbooks")).expanduser().resolve()
CASHBOOK_BACKUP_DIR = Path(os.getenv("CASHBOOK_FILE_BACKUP_DIR", APP_DATA_DIR / "cashbook_backups")).expanduser().resolve()
DATABASE_BACKUP_DIR = Path(os.getenv("CASHBOOK_BACKUP_DIR", APP_DATA_DIR / "database_backups")).expanduser().resolve()
LOG_DIR = Path(os.getenv("CASHBOOK_LOG_DIR", APP_DATA_DIR / "logs")).expanduser().resolve()
DEFAULT_ACTIVE_CASHBOOK = CASHBOOK_DIR / "active-cashbook.xls"

# Kept only so older installations fail gracefully instead of losing the
# previously configured file. Phase 6 no longer generates a workbook from a
# blank template; the registered live cashbook is the destination itself.
DEFAULT_WCED_TEMPLATE = CONFIG_DIR / "wced-template.xls"


def ensure_application_directories() -> None:
    """Create persistent folders without creating or reseeding data files."""
    for path in (APP_DATA_DIR, DATA_DIR, CONFIG_DIR, CASHBOOK_DIR,
                 CASHBOOK_BACKUP_DIR, DATABASE_BACKUP_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def resource_path(*parts: str) -> Path:
    """Find read-only resources in a PyInstaller bundle or source checkout."""
    return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).joinpath(*parts)


def get_wced_template_path() -> Path | None:
    configured = os.getenv("WCED_TEMPLATE_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_WCED_TEMPLATE if DEFAULT_WCED_TEMPLATE.is_file() else None


def is_wced_template_ready() -> bool:
    path = get_wced_template_path()
    return path is not None and path.is_file()


def get_wced_template_source() -> str:
    if os.getenv("WCED_TEMPLATE_PATH"):
        return "environment"
    if DEFAULT_WCED_TEMPLATE.is_file():
        return "local_config"
    return "missing"


def ensure_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".cashbook-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False
