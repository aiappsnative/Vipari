from __future__ import annotations

import uvicorn

from config import get_settings
from services.webhook_service import create_webhook_app


app = create_webhook_app()


def main() -> None:
    settings = get_settings()
    host = settings.webhook_host
    if settings.is_internet_reachable_env and host == "127.0.0.1":
        host = "0.0.0.0"
    uvicorn.run("run_webhook:app", host=host, port=settings.webhook_port)


if __name__ == "__main__":
    main()
