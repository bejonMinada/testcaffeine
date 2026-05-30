import logging
from logging.handlers import RotatingFileHandler

from .config import get_app_data_dir


def build_logger() -> logging.Logger:
    logger = logging.getLogger("test_caffeine")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    log_file = get_app_data_dir() / "test_caffeine.log"
    handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.propagate = False
    return logger
