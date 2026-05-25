from __future__ import annotations

import asyncio

from services.cloud_worker import run_worker
from services.observability import configure_logging


if __name__ == "__main__":
    logger = configure_logging("worker")
    try:
        asyncio.run(run_worker())
    except Exception:
        logger.exception("Worker process exited due to fatal exception")
        raise
