from __future__ import annotations

import uvicorn

from config import get_settings
from main import app


def main() -> None:
    settings = get_settings()
    host = settings.api_host
    if settings.is_internet_reachable_env and host == "127.0.0.1":
        host = "0.0.0.0"
    uvicorn.run("run_api:app", host=host, port=settings.api_port)


if __name__ == "__main__":
    main()
