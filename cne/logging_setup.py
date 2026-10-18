"""One-shot logging configuration."""
import logging
import sys
from cne.config import settings

_configured = False


def configure() -> None:
    global _configured
    if _configured:
        return
    fmt = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handlers = [logging.StreamHandler(sys.stderr)]
    if settings.log_file:
        handlers.append(logging.FileHandler(settings.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format=fmt, datefmt=datefmt, handlers=handlers, force=True,
    )
    for noisy in ("urllib3", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure()
    return logging.getLogger(name)