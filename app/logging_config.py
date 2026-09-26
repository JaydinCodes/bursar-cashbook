import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from .config import LOG_DIR as CONFIG_LOG_DIR

LOG_DIR = CONFIG_LOG_DIR
LOG_FILE = LOG_DIR / "cashbook.log"


class JsonFormatter(logging.Formatter):
    _standard_attrs = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName", "processName",
        "process", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in self._standard_attrs:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("cashbook")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(getattr(handler, "_cashbook_handler", False) for handler in logger.handlers):
        formatter = JsonFormatter()

        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler._cashbook_handler = True
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler._cashbook_handler = True
        logger.addHandler(console_handler)

    return logger


logger = configure_logging()
