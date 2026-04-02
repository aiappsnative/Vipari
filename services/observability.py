from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from prometheus_fastapi_instrumentator import Instrumentator


class JSONFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "service": self.service,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("delivery_id", "installation_id", "repo", "pr_number", "head_sha", "job_id", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload)


def configure_logging(service: str) -> logging.Logger:
    logger = logging.getLogger(service)
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(service))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def instrument_fastapi(app) -> None:
    Instrumentator().instrument(app).expose(app, include_in_schema=False, should_gzip=True)
