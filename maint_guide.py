"""정비관리 사용 설명서 — 화면에서 수정한 본문 저장/불러오기."""
from __future__ import annotations

import re
from datetime import datetime
from html import escape
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AppSetting

KEY_HTML = "maint_guide.html"
KEY_UPDATED = "maint_guide.updated"
MAX_HTML_LEN = 200_000

# 저장된 HTML은 |safe로 렌더링되므로 태그·속성을 허용 목록으로만 통과시킨다.
ALLOWED_TAGS = frozenset({
    "header", "footer", "section", "div", "p", "h1", "h2", "h3", "h4",
    "ul", "ol", "li", "b", "strong", "i", "em", "u", "small", "span", "br",
})
VOID_TAGS = frozenset({"br"})
DROP_CONTENT_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "template", "noscript"})
_CLASS_RE = re.compile(r"[^A-Za-z0-9_\- ]")


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS:
            return
        parts = [tag]
        for name, value in attrs:
            name = (name or "").lower()
            if name == "class" and value:
                cls = _CLASS_RE.sub("", value).strip()
                if cls:
                    parts.append(f'class="{cls}"')
            elif name == "aria-label" and value:
                parts.append(f'aria-label="{escape(value, quote=True)}"')
        self.out.append("<" + " ".join(parts) + ">")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        tag = tag.lower()
        if tag not in VOID_TAGS and self.open and self.open[-1] == tag:
            self.open.pop()
            self.out.append(f"</{tag}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag not in self.open:
            return
        while self.open:
            t = self.open.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def sanitize_html(raw: str) -> str:
    p = _Sanitizer()
    p.feed(raw or "")
    p.close()
    return p.result().strip()


async def _row(session: AsyncSession, key: str) -> AppSetting | None:
    return (
        await session.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()


async def load(session: AsyncSession) -> tuple[str, str]:
    """(저장된 본문 HTML 또는 "", "이름|ISO시각" 또는 "")."""
    html_row = await _row(session, KEY_HTML)
    upd_row = await _row(session, KEY_UPDATED)
    return (
        (html_row.value or "") if html_row else "",
        (upd_row.value or "") if upd_row else "",
    )


async def _set(session: AsyncSession, key: str, value: str) -> None:
    row = await _row(session, key)
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=key, value=value))


async def save(session: AsyncSession, html: str, editor: str) -> None:
    await _set(session, KEY_HTML, html)
    await _set(session, KEY_UPDATED, f"{editor}|{datetime.utcnow().isoformat()}")


async def reset(session: AsyncSession, editor: str) -> None:
    row = await _row(session, KEY_HTML)
    if row:
        await session.delete(row)
    await _set(session, KEY_UPDATED, f"{editor}|{datetime.utcnow().isoformat()}")
