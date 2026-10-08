import json
import logging
import sys

from app.core.config import get_settings
from app.core.runcontext import current_request_id, current_run_id


class JsonFormatter(logging.Formatter):
    """One valid JSON object per log record (BACKEND_SPEC §Reliability).

    `record.getMessage()` resolves %-style arguments first, so the emitted
    message is always a single JSON-safe string. request_id/run_id are read
    from ContextVars (app/core/runcontext.py) and are empty strings when no
    request/run is in scope.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": current_request_id(),
            "run_id": current_run_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=settings.log_level.upper(), handlers=[handler])
    # Provider credentials travel in query strings (SerpApi `api_key=...`).
    # Never let HTTP client INFO logs print request URLs into the logs.
    for noisy in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
