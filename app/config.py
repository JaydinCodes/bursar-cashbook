import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path(os.getenv("CASHBOOK_CONFIG_DIR", PROJECT_ROOT / "config"))
DEFAULT_WCED_TEMPLATE = CONFIG_DIR / "wced-template.xls"


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
