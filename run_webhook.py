from __future__ import annotations

import uvicorn

from config import get_settings
from services.webhook_service import create_webhook_app


app = create_webhook_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run("run_webhook:app", host=settings.webhook_host, port=settings.webhook_port)


if __name__ == "__main__":
    main()
