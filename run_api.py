from __future__ import annotations

import uvicorn

from main import app



if __name__ == "__main__":
    uvicorn.run("run_api:app", host="0.0.0.0", port=8002)
