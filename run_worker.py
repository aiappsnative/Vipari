from __future__ import annotations

import asyncio

from services.cloud_worker import run_worker


if __name__ == "__main__":
    asyncio.run(run_worker())
