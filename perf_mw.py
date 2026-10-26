"""응답 속도 관련 순수 ASGI 미들웨어: 텍스트 응답 gzip 압축, 느린 요청 로그."""
from __future__ import annotations

import os
import time

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder, IdentityResponder

_COMPRESSIBLE = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)


class _TextGZipResponder(GZipResponder):
    async def send_with_compression(self, message) -> None:
        if message["type"] == "http.response.start":
            await super().send_with_compression(message)
            ct = Headers(raw=message["headers"]).get("content-type", "").lower()
            if not ct.startswith(_COMPRESSIBLE):
                # 엑셀·zip·사진은 이미 압축된 형식 — CPU만 쓰므로 그대로 보냄
                self.content_type_is_excluded = True
            return
        await super().send_with_compression(message)


class TextGZipMiddleware(GZipMiddleware):
    """HTML·CSS·JS·JSON만 압축 (서버→Cloudflare 구간 전송량 감소)."""

    def __init__(self, app) -> None:
        super().__init__(app, minimum_size=1024, compresslevel=5)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if "gzip" in Headers(scope=scope).get("accept-encoding", ""):
            responder = _TextGZipResponder(self.app, self.minimum_size, compresslevel=self.compresslevel)
        else:
            responder = IdentityResponder(self.app, self.minimum_size)
        await responder(scope, receive, send)


SLOW_REQUEST_MS = int(os.environ.get("SLOW_REQUEST_MS", "1000") or "1000")


class SlowRequestLogMiddleware:
    """처리에 SLOW_REQUEST_MS 이상 걸린 요청을 로그에 남김 (Render 로그에서 [slow] 검색)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or SLOW_REQUEST_MS <= 0:
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = {"code": 0}

        async def _send(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            ms = (time.perf_counter() - start) * 1000
            if ms >= SLOW_REQUEST_MS:
                path = scope.get("path", "")
                print(
                    f"[slow] {scope.get('method', '')} {path} {status['code']} {ms:.0f}ms",
                    flush=True,
                )
