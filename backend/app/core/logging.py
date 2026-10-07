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
