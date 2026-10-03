# -*- coding: utf-8 -*-
"""AI 분석 GPT 대화 계정별 저장 — 목록·다시보기·이어서 대화."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AiChatConversation

MAX_STORED_MESSAGES = 400
MAX_LIST = 200
GPT_HISTORY_MESSAGES = 20
_KST = timezone(timedelta(hours=9))


def _kst_text(value: datetime | None) -> str:
    if not value:
        return ""
    return value.replace(tzinfo=timezone.utc).astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def make_title(question: str) -> str:
    text = " ".join(str(question or "").split())
    return (text[:60] + "…") if len(text) > 60 else (text or "새 대화")


def conversation_messages(row: AiChatConversation | None) -> list[dict[str, str]]:
    if not row or not row.messages:
        return []
    try:
        data = json.loads(row.messages)
    except (TypeError, ValueError):
        return []
    return [
        {"role": str(m.get("role") or ""), "content": str(m.get("content") or "")}
        for m in data
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]


def conversation_summary(row: AiChatConversation) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title or "새 대화",
        "model": row.model or "",
        "count": int(row.message_count or 0),
        "updated_at": _kst_text(row.updated_at),
        "created_at": _kst_text(row.created_at),
    }


async def list_conversations(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(AiChatConversation)
            .where(AiChatConversation.user_id == user_id)
            .order_by(AiChatConversation.updated_at.desc(), AiChatConversation.id.desc())
            .limit(MAX_LIST)
        )
    ).scalars().all()
    return [conversation_summary(r) for r in rows]


async def get_conversation(
    db: AsyncSession, user_id: int, conversation_id: Any
) -> AiChatConversation | None:
    try:
        cid = int(conversation_id)
    except (TypeError, ValueError):
        return None
    row = await db.get(AiChatConversation, cid)
    if not row or row.user_id != user_id:
        return None
    return row


async def create_conversation(
    db: AsyncSession, user_id: int, title: str, model: str = ""
) -> AiChatConversation:
    now = datetime.utcnow()
    row = AiChatConversation(
        user_id=user_id,
        title=make_title(title),
        model=(model or "")[:64],
        messages="[]",
        message_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return row


def append_messages(
    row: AiChatConversation, new_messages: list[dict[str, str]], model: str = ""
) -> None:
    merged = conversation_messages(row) + [
        {"role": m["role"], "content": str(m.get("content") or "")}
        for m in new_messages
        if m.get("role") in ("user", "assistant")
    ]
    merged = merged[-MAX_STORED_MESSAGES:]
    row.messages = json.dumps(merged, ensure_ascii=False)
    row.message_count = len(merged)
    if model:
        row.model = model[:64]
    row.updated_at = datetime.utcnow()
