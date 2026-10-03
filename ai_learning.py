"""AI 공유 학습 — 계정과 무관하게 답변 문제 사례에서 지침을 배워 모든 질문에 반영.

흐름: 👎 피드백 또는 '그게 아니라' 같은 정정 질문 → GPT가 일반화된 지침·용어 별칭(JSON)으로 정리
→ ai_lessons 저장 → 이후 누구의 질문이든 관련 지침·별칭·좋은 평가 사례를 GPT 프롬프트에 포함.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_context_full import question_keywords
from models import AiFeedback, AiLesson

MAX_LESSONS_IN_PROMPT = 12
MAX_EXAMPLES_IN_PROMPT = 2

_CORRECTION_RE = re.compile(
    r"그게\s*아니|그거\s*말고|아니\s*(야|요|라|고|지)|틀렸|틀린|틀려|잘못|엉뚱|"
    r"왜\s*없|없다고|있는데|있잖|빠졌|빠져|누락|안\s*나와|안\s*나오|이상해|맞지\s*않|"
    r"다시\s*(확인|봐|찾아|해|알려)|그런\s*뜻|그런\s*의미|말한\s*건|물어본\s*건|의도"
)


def is_correction(question: str) -> bool:
    return bool(_CORRECTION_RE.search(question or ""))


def _loads(text: str | None, default: Any) -> Any:
    try:
        v = json.loads(text or "")
        return v if isinstance(v, type(default)) else default
    except (TypeError, ValueError):
        return default


def lesson_dict(row: AiLesson) -> dict[str, Any]:
    return {
        "id": row.id,
        "rule": row.rule,
        "keywords": _loads(row.keywords, []),
        "synonyms": _loads(row.synonyms, {}),
        "is_active": bool(row.is_active),
        "hit_count": int(row.hit_count or 0),
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
        "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M") if row.updated_at else "",
    }


def _clip(text: Any, n: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def record_feedback(
    db: AsyncSession,
    *,
    user_id: int,
    conversation_id: int | None,
    message_index: int | None,
    rating: int,
    question: str,
    answer: str,
    comment: str = "",
    source: str = "button",
) -> AiFeedback:
    row = AiFeedback(
        user_id=user_id,
        conversation_id=conversation_id,
        message_index=message_index,
        rating=1 if rating > 0 else -1,
        source=source,
        question=_clip(question, 4000),
        answer=_clip(answer, 8000),
        comment=_clip(comment, 2000),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


_LESSON_PROMPT = (
    "당신은 Smart FMS(시설관리) AI 답변 품질 개선 담당입니다. 아래는 사용자가 AI 답변에 문제가 있다고 "
    "지적한 사례입니다. 다른 사용자가 비슷한 질문을 할 때 같은 문제가 반복되지 않도록, "
    "일반화된 답변 지침을 JSON으로만 출력하세요.\n"
    '형식: {"rule": "지침 1~3문장(한국어, 명령형)", "keywords": ["이 지침이 적용될 질문 키워드", ...], '
    '"synonyms": {"사용자가 쓴 별칭": "FMS 정식 명칭", ...}}\n'
    "규칙: 사람 이름·연락처·API키 등 개인정보는 넣지 마세요. 특정 날짜의 일회성 수치가 아니라 "
    "질문 의도 해석·용어·데이터 위치·답변 형식에 관한 재사용 가능한 지침으로 쓰세요. "
    "keywords는 건물명·설비명·메뉴명 등 2~6개. synonyms는 확실한 별칭만(없으면 {}). "
    "일반화할 수 없거나 단순 불만이면 rule을 빈 문자열로 두세요."
)


def _parse_lesson_json(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _generate_lesson_sync(api_key: str, model: str, question: str, answer: str, comment: str) -> dict | None:
    from ai_analysis import _openai_chat_completion

    user = (
        f"[사용자 질문]\n{_clip(question, 1500)}\n\n[AI 답변]\n{_clip(answer, 3500)}\n\n"
        f"[사용자 지적/정정]\n{_clip(comment, 1500) or '(내용 없음 — 답변이 질문 의도와 달랐음)'}"
    )
    text = _openai_chat_completion(
        api_key=api_key,
        model=model,
        messages=[{"role": "system", "content": _LESSON_PROMPT}, {"role": "user", "content": user}],
    )
    return _parse_lesson_json(text)


async def create_lesson_from_feedback(
    db: AsyncSession,
    fb: AiFeedback,
    *,
    api_key: str = "",
    model: str = "",
) -> AiLesson | None:
    """문제 피드백 → 학습 노트. GPT 정리가 실패하면 사용자 지적 원문으로 지침을 만든다."""
    import asyncio

    if fb.rating > 0:
        return None
    data: dict[str, Any] | None = None
    if api_key:
        try:
            data = await asyncio.to_thread(
                _generate_lesson_sync, api_key, model or "gpt-4o-mini", fb.question, fb.answer, fb.comment
            )
        except Exception as e:  # noqa: BLE001
            print(f"[ai_learning] lesson GPT 실패: {e}", flush=True)
    if data is not None and not str(data.get("rule") or "").strip():
        return None
    if data is None:
        if not fb.comment.strip():
            return None
        data = {
            "rule": f"'{_clip(fb.question, 120)}' 같은 질문에서 사용자 지적: {_clip(fb.comment, 400)} "
            "— 이 지적을 반영해 답할 것.",
            "keywords": question_keywords(fb.question)[:6],
            "synonyms": {},
        }
    rule = _clip(data.get("rule"), 800)
    keywords = [str(k).strip().lower() for k in (data.get("keywords") or []) if str(k).strip()][:8]
    synonyms = {
        str(k).strip(): str(v).strip()
        for k, v in (data.get("synonyms") or {}).items()
        if str(k).strip() and str(v).strip() and str(k).strip() != str(v).strip()
    }
    dup = (
        await db.execute(select(AiLesson).where(AiLesson.rule == rule, AiLesson.is_active == True))  # noqa: E712
    ).scalar_one_or_none()
    if dup:
        fb.lesson_id = dup.id
        return dup
    now = datetime.utcnow()
    row = AiLesson(
        rule=rule,
        keywords=json.dumps(keywords, ensure_ascii=False),
        synonyms=json.dumps(synonyms, ensure_ascii=False),
        source_feedback_id=fb.id,
        created_by=fb.user_id,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    fb.lesson_id = row.id
    return row


async def learn_from_correction_background(
    *,
    user_id: int,
    conversation_id: int | None,
    message_index: int | None,
    question: str,
    answer: str,
    correction: str,
    api_key: str,
    model: str,
) -> None:
    """채팅 응답 후 백그라운드 — '그게 아니라' 같은 정정 질문을 문제 사례로 학습."""
    from database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            fb = await record_feedback(
                db,
                user_id=user_id,
                conversation_id=conversation_id,
                message_index=message_index,
                rating=-1,
                question=question,
                answer=answer,
                comment=correction,
                source="auto",
            )
            await create_lesson_from_feedback(db, fb, api_key=api_key, model=model)
            await db.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[ai_learning] 자동 학습 실패: {e}", flush=True)


async def relevant_lessons(db: AsyncSession, question: str) -> list[AiLesson]:
    q = (question or "").lower()
    rows = (
        await db.execute(
            select(AiLesson)
            .where(AiLesson.is_active == True)  # noqa: E712
            .order_by(AiLesson.updated_at.desc())
            .limit(500)
        )
    ).scalars().all()
    scored: list[tuple[float, AiLesson]] = []
    for row in rows:
        kws = _loads(row.keywords, [])
        syn = _loads(row.synonyms, {})
        score = sum(1.0 for k in kws if k and str(k).lower() in q)
        score += sum(2.0 for alias in syn if alias and alias.lower() in q)
        if not kws and not syn:
            score = 0.5
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda t: -t[0])
    return [r for _, r in scored[:MAX_LESSONS_IN_PROMPT]]


def apply_synonyms(question: str, lessons: list[AiLesson]) -> str:
    q = question or ""
    extra: list[str] = []
    for row in lessons:
        for alias, canonical in _loads(row.synonyms, {}).items():
            if alias and canonical and alias.lower() in q.lower() and canonical not in q and canonical not in extra:
                extra.append(canonical)
    return f"{q} {' '.join(extra)}" if extra else q


async def good_examples(db: AsyncSession, question: str) -> list[dict[str, str]]:
    kws = question_keywords(question)
    if not kws:
        return []
    rows = (
        await db.execute(
            select(AiFeedback)
            .where(AiFeedback.rating > 0)
            .order_by(AiFeedback.created_at.desc())
            .limit(300)
        )
    ).scalars().all()
    scored = []
    for r in rows:
        hit = sum(1 for k in kws if k in (r.question or "").lower())
        if hit:
            scored.append((hit, r))
    scored.sort(key=lambda t: -t[0])
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, r in scored:
        if r.question in seen:
            continue
        seen.add(r.question)
        out.append({"question": _clip(r.question, 300), "answer": _clip(r.answer, 900)})
        if len(out) >= MAX_EXAMPLES_IN_PROMPT:
            break
    return out


async def learned_context(db: AsyncSession, question: str) -> tuple[dict[str, Any], str]:
    """(프롬프트용 학습 정보, 별칭이 보강된 질문). 사용된 지침은 hit_count 증가."""
    lessons = await relevant_lessons(db, question)
    effective = apply_synonyms(question, lessons)
    examples = await good_examples(db, effective)
    for row in lessons:
        row.hit_count = int(row.hit_count or 0) + 1
    info: dict[str, Any] = {}
    if lessons:
        info["rules"] = [row.rule for row in lessons]
        syn: dict[str, str] = {}
        for row in lessons:
            syn.update(_loads(row.synonyms, {}))
        if syn:
            info["synonyms"] = syn
    if examples:
        info["good_examples"] = examples
    return info, effective
