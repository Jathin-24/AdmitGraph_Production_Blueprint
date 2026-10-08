import logging
import sys

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        stream=sys.stdout,
        format='{"level":"%(levelname)s","logger":"%(name)s","message":%(message)s}',
    )
    # Provider credentials travel in query strings (SerpApi `api_key=...`).
    # Never let HTTP client INFO logs print request URLs into the logs.
    for noisy in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
