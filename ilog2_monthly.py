"""점검일지 월누계 공통 계산.

월누계는 해당 월 1일부터 그날까지의 합계다. 전날 월누계를 이어받는 방식은
전날 일지가 비어 있거나 달이 바뀔 때 값이 끊기거나 넘어가므로,
이달 1일~전날 일지를 모아 합계를 새로 구한다(달이 바뀌면 자동으로 0부터).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def same_month(a: date, b: date) -> bool:
    return a.year == b.year and a.month == b.month


async def month_rows_before(session: AsyncSession, model, building_id: int, log_date: date) -> list:
    """같은 건물의 이달 1일 ~ log_date 전날 일지 (날짜순)."""
    first = log_date.replace(day=1)
    if log_date <= first:
        return []
    return list(
        (
            await session.execute(
                select(model)
                .where(
                    model.building_id == building_id,
                    model.log_date >= first,
                    model.log_date < log_date,
                )
                .order_by(model.log_date)
            )
        ).scalars().all()
    )


def sum_by_key(
    datas: Iterable[dict],
    keys: Iterable[str],
    getter: Callable[[dict, str], Any],
    parse: Callable[[Any], float | None],
) -> dict[str, float]:
    totals: dict[str, float] = {}
    key_list = list(keys)
    for data in datas:
        for key in key_list:
            value = parse(getter(data, key))
            if value is not None:
                totals[key] = totals.get(key, 0.0) + value
    return totals


async def utility_month_base(
    session: AsyncSession,
    model,
    building_id: int,
    log_date: date,
    monthly_ids: Iterable[str],
    recompute: Callable[[dict], dict],
    parse: Callable[[Any], float | None],
    fmt: Callable[[Any], str],
) -> dict[str, str]:
    """유틸리티 monthly_ids별 이달 1일~전날 일사용량 합계 (월누계 = 이 값 + 금일 사용량)."""
    rows = await month_rows_before(session, model, building_id, log_date)
    datas = [(recompute(r.data or {}).get("utility") or {}) for r in rows]
    totals = sum_by_key(
        datas, monthly_ids, lambda util, uid: (util.get(uid) or {}).get("daily"), parse
    )
    return {uid: fmt(round(v, 6)) for uid, v in totals.items()}
