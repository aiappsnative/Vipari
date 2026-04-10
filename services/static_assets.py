from __future__ import annotations

from fastapi.staticfiles import StaticFiles


class FingerprintedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        query_string = scope.get("query_string", b"")
        query_text = query_string.decode("utf-8", errors="ignore")
        if "v=" in query_text:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
        return response