"""보안 응답 헤더 + 다른 사이트에서 보낸 상태 변경 요청(CSRF) 차단 — 순수 ASGI 미들웨어."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 서버 간 호출(OnlyOffice 문서 서버 콜백·크론)은 브라우저 Origin이 없거나 다름
_CSRF_EXEMPT_PREFIXES = ("/oo/", "/cron/")


def _https_only() -> bool:
    return os.environ.get("RENDER", "").lower() in ("true", "1", "yes") or os.environ.get(
        "COOKIE_HTTPS_ONLY", ""
    ).lower() in ("1", "true", "yes")


def _base_headers() -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"SAMEORIGIN"),
        (b"referrer-policy", b"same-origin"),
        (b"content-security-policy", b"frame-ancestors 'self'; object-src 'none'; base-uri 'self'"),
        (b"permissions-policy", b"camera=(self), microphone=(), geolocation=(self)"),
    ]
    if _https_only():
        headers.append((b"strict-transport-security", b"max-age=31536000"))
    return headers


def _allowed_hosts(host_header: str) -> set[str]:
    hosts = {host_header.lower()} if host_header else set()
    base = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if base:
        hosts.add(urlsplit(base).netloc.lower())
    return hosts


def _source_host(headers: dict[bytes, bytes]) -> str | None:
    """요청을 보낸 페이지의 host. 확인할 정보가 없으면 None."""
    origin = headers.get(b"origin", b"").decode("latin-1").strip()
    if origin:
        return "" if origin == "null" else urlsplit(origin).netloc.lower()
    referer = headers.get(b"referer", b"").decode("latin-1").strip()
    if referer:
        return urlsplit(referer).netloc.lower()
    return None


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app
        self.extra_headers = _base_headers()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "") or ""
        if method in _UNSAFE_METHODS and not path.startswith(_CSRF_EXEMPT_PREFIXES):
            headers = dict(scope.get("headers") or [])
            src = _source_host(headers)
            if src is not None:
                host = headers.get(b"host", b"").decode("latin-1").strip()
                if src not in _allowed_hosts(host):
                    await self._reject(send)
                    return

        extra = self.extra_headers

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (k, v) for k, v in extra if k not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    async def _reject(self, send):
        body = "다른 사이트에서 보낸 요청은 처리할 수 없습니다.".encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    *self.extra_headers,
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
