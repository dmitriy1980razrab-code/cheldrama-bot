from __future__ import annotations

from logging import Formatter, Logger, getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path
import hashlib
import time


def create_technical_logger(log_path: str | Path) -> Logger:
    """Create a rotating technical log that contains no dialogue text."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    logger = getLogger(f"theatre_bot.technical.{identity}")
    logger.setLevel("ERROR")
    logger.propagate = False

    if not logger.handlers:
        handler = RotatingFileHandler(
            path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        formatter = Formatter("%(asctime)sZ level=%(levelname)s %(message)s")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def record_error(logger: Logger, location: str, error: BaseException) -> None:
    """Record only controlled metadata, never the exception message or request data."""
    logger.error(
        "event=internal_error location=%s error_type=%s",
        location,
        type(error).__name__,
    )
