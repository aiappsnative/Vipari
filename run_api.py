from __future__ import annotations

import uvicorn

from config import get_settings
from main import app


def main() -> None:
    settings = get_settings()
    uvicorn.run("run_api:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
