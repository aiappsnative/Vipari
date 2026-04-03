from __future__ import annotations

import uvicorn

from services.webhook_service import create_webhook_app


app = create_webhook_app()


if __name__ == "__main__":
    uvicorn.run("run_webhook:app", host="0.0.0.0", port=8001)
