"""Logging rapi: console + file berputar (rotating) agar disk VPS tidak penuh."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_LOGGER_NAME = "gladius"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logger(level: str = "INFO", log_dir: str | Path = "logs") -> logging.Logger:
    """Konfigurasi logger induk ``gladius``. Aman dipanggil berulang kali."""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    if logger.handlers:  # sudah dikonfigurasi
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    try:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path / "gladius.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:  # hosting read-only? tetap jalan dengan console saja
        logger.warning("Tidak bisa menulis file log (%s). Memakai console saja.", exc)

    # Kurangi kebisingan library pihak ketiga
    for noisy in ("httpx", "httpcore", "urllib3", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    """Ambil logger anak di bawah ``gladius`` (pakai ``get_logger(__name__)``)."""
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
