"""AI 분석용 메뉴별 DB 추출 — 사업장/건물·설비·PM·점검일지·정비·위험성평가·자재.

질문의 기간·건물·키워드를 해석해 각 메뉴의 원본 데이터를 표(columns/rows) 형태로 모은다.
GPT 컨텍스트 한도는 fit_json()이 큰 목록부터 줄이며 맞추고, 잘린 목록은 `<key>_total`로 전체 건수를 남긴다.
"""
from __future__ import annotations

import importlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from models import (
    Building,
    Consumable,
    D1Plan,
    Equipment,
    Floor,
    MaintenanceRecord,
    MaterialItem,
    MaterialLog,
    Partner,
    PMInspection,
    PMSchedule,
    WorkOrder,
    Zone,
)

DEFAULT_WORK_DAYS = 180
DEFAULT_PM_DAYS = 90
DEFAULT_MATERIAL_LOG_DAYS = 90
DEFAULT_LOG_DAYS = 7
FOCUSED_LOG_DAYS = 31
MAX_LOG_DAYS_PER_BUILDING = 62
MAX_ROWS = 3000

_LOG_DAILY_MODELS: dict[str, str] = {
    "housing_substation": "HousingSubstationDaily",
    "central_control_room": "CentralControlRoomDaily",
    "ccr_facility": "CcrFacilityDaily",
    "steelworks_hq": "SteelworksHqDaily",
    "steelworks_hall": "SteelworksHallDaily",
    "human_center": "HumanCenterDaily",
    "eoulrim_gym": "EoulrimGymDaily",
    "baegun_dorm": "BaegunDormDaily",
    "giga_town": "GigaTownDaily",
    "park1538": "Park1538Daily",
    "baegun_art_hall": "BaegunArtHallDaily",
    "sub53": "Sub53Daily",
    "baegundae": "BaegundaeDaily",
    "baegun_shopping": "BaegunShoppingDaily",
    "rist": "RistDaily",
    "ground_gwangyang": "GroundGwangyangDaily",
}

_PARTICLES = (
    "에서는", "에서", "으로", "까지", "부터", "에게", "이랑", "하고", "보다",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "들",
)
_STOPWORDS = {
    "알려줘", "알려주세요", "알려", "보여줘", "보여주세요", "정리", "정리해줘", "분석", "분석해줘",
    "데이터", "현황", "목록", "리스트", "내용", "최근", "전체", "모든", "모두", "해줘", "주세요",
    "얼마", "얼마나", "몇", "몇건", "건수", "어떤", "무엇", "뭐야", "언제", "어디", "엑셀", "추출",
    "검색", "관련", "대한", "대해", "대해서", "있는", "있어", "있나", "있는지", "없는", "오늘", "어제",
    "이번", "지난", "이번달", "지난달", "이번주", "지난주", "올해", "작년", "기간", "동안", "기준",
    "결과", "사항", "항목", "표로", "비교", "요약", "상태", "건물", "사업장", "설비", "점검", "정비",
    "pm", "일지", "자재", "위험성평가", "위험성", "관리", "이력", "내역", "해당", "그리고", "또는",
    "가장", "많은", "적은", "높은", "낮은", "개수", "합계", "평균", "gpt", "ai", "운영일보",
    "점검일지", "설비관리", "정비관리", "자재관리", "정비의뢰", "작성", "등록", "확인", "어떻게",
    "전부", "다", "각", "별", "및", "중", "총", "수", "정비이력", "점검결과", "점검내용", "정비내용",
    "조치내용", "특이사항", "특이", "재고", "작업", "위험요인", "위험", "유해위험요인", "안전대책", "사용량", "소모품", "입출고", "일별", "월별", "건물별", "설비별",
}


def _clip(text: Any, n: int = 200) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _enum_val(v: Any) -> str:
    return v.value if hasattr(v, "value") else str(v or "")


def _d(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)


def question_keywords(question: str) -> list[str]:
    text = re.sub(r"[^\w가-힣\-\.]+", " ", question or "")
    out: list[str] = []
    for tok in text.split():
        t = tok.strip(".-_").lower()
        if t in _STOPWORDS:
            continue
        for p in _PARTICLES:
            if len(t) > len(p) + 1 and t.endswith(p):
                t = t[: -len(p)]
                break
        if len(t) < 2 or t in _STOPWORDS:
            continue
        if re.fullmatch(r"[\d\.\-]+(년|월|일|주|개월|시|건|개)?", t):
            continue
        if t not in out:
            out.append(t)
    return out[:8]


def _month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    nxt = date(year + (month // 12), month % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def question_date_range(question: str, today: date) -> tuple[date, date] | None:
    """질문의 기간 표현을 (시작일, 종료일)로. 기간 언급이 없으면 None."""
    q = question or ""
    qn = q.replace(" ", "")
    dates: list[date] = []
    for m in re.finditer(
        r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일|(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", q
    ):
        y, mo, dd = (m.group(1), m.group(2), m.group(3)) if m.group(1) else m.group(4, 5, 6)
        try:
            dates.append(date(int(y), int(mo), int(dd)))
        except ValueError:
            pass
    if not dates:
        ym = re.search(r"(20\d{2})\s*년", q)
        year = int(ym.group(1)) if ym else today.year
        for m in re.finditer(
            r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일|(?<![\d/.])(\d{1,2})/(\d{1,2})(?![\d/])", q
        ):
            mo, dd = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            try:
                dates.append(date(year, int(mo), int(dd)))
            except ValueError:
                pass
    if dates:
        return min(dates), max(dates)

    m = re.search(r"최근\s*(\d{1,3})\s*(일|주|개월|달|년)", q)
    if m:
        n = int(m.group(1))
        days = n * {"일": 1, "주": 7, "개월": 30, "달": 30, "년": 365}[m.group(2)]
        return today - timedelta(days=max(days, 1) - 1), today

    monday = today - timedelta(days=today.weekday())
    if "오늘" in qn:
        return today, today
    if "그제" in qn or "그저께" in qn:
        d = today - timedelta(days=2)
        return d, d
    if "어제" in qn:
        d = today - timedelta(days=1)
        return d, d
    if "이번주" in qn or "금주" in qn:
        return monday, today
    if "지난주" in qn or "저번주" in qn:
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if "이번달" in qn or "금월" in qn or "당월" in qn:
        return today.replace(day=1), today
    if "지난달" in qn or "저번달" in qn or "전월" in qn:
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if "올해" in qn or "금년" in qn:
        return date(today.year, 1, 1), today
    if "작년" in qn or "전년" in qn or "지난해" in qn:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)

    ym = re.search(r"(20\d{2})\s*년\s*(\d{1,2})\s*월", q)
    if ym and 1 <= int(ym.group(2)) <= 12:
        return _month_range(int(ym.group(1)), int(ym.group(2)))
    mm = re.search(r"(?<!\d)(\d{1,2})\s*월", q)
    if mm and 1 <= int(mm.group(1)) <= 12:
        month = int(mm.group(1))
        year = today.year if month <= today.month else today.year - 1
        return _month_range(year, month)
    yy = re.search(r"(20\d{2})\s*년", q)
    if yy:
        year = int(yy.group(1))
        return date(year, 1, 1), min(date(year, 12, 31), today) if year == today.year else date(year, 12, 31)
    return None


def _json_s(v: Any, n: int) -> str:
    if not v:
        return ""
    return _clip(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str), n)


def _table(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columns": columns, "rows": [["" if v is None else v for v in r] for r in rows]}


def _kw_filter(keywords: list[str], *cols):
    return or_(*[c.ilike(f"%{k}%") for k in keywords for c in cols])


def _text_hit(keywords: list[str], *values: Any) -> bool:
    if not keywords:
        return False
    blob = " ".join(str(v or "") for v in values).lower()
    return any(k in blob for k in keywords)


# ── 점검일지(운영일보) ──────────────────────────────────────────

def _flatten(obj: Any, prefix: str, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            if ks == "prev_manual" or ks.startswith("_"):
                continue
            _flatten(v, f"{prefix}.{ks}" if prefix else ks, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten(v, f"{prefix}[{i}]", out)
    else:
        if obj is None or obj is False:
            return
        s = "Y" if obj is True else str(obj).strip()
        if s:
            out.append(f"{prefix}={_clip(s, 300)}")


def _schema_glossary(schema: Any) -> dict[str, str]:
    out: dict[str, str] = {}

    def label_of(node: dict) -> str:
        parts: list[str] = []
        for k in ("building", "group", "item", "name", "label", "metric", "sub_label"):
            v = node.get(k)
            if isinstance(v, (str, int, float)) and str(v).strip() and str(v).strip() not in parts:
                parts.append(str(v).strip())
        if not parts and isinstance(node.get("title"), str):
            parts.append(node["title"].strip())
        s = " ".join(parts)
        unit = node.get("unit")
        if s and isinstance(unit, str) and unit.strip():
            s += f"({unit.strip()})"
        return s

    def walk(node: Any, block_id: str) -> None:
        if isinstance(node, dict):
            nid = node.get("id") if isinstance(node.get("id"), str) else ""
            lab = label_of(node)
            if nid and lab:
                out.setdefault(nid, lab)
            col = node.get("col")
            if isinstance(col, str) and block_id and lab:
                out.setdefault(f"{block_id}_{col}", lab)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, nid or block_id)
        elif isinstance(node, list):
            for v in node:
                walk(v, block_id)

    walk(schema, "")
    return out


def _path_segments(pairs: list[str]) -> set[str]:
    segs: set[str] = set()
    for p in pairs:
        path = p.split("=", 1)[0]
        for s in re.split(r"[.\[\]]", path):
            if s:
                segs.add(s)
    return segs


def _question_log_modules(question: str) -> set[str]:
    """질문에 이름이 나온 일지 모듈. 중앙관제실은 전기·설비 일지가 같은 건물이라 함께 본다."""
    from ai_analysis import _LOG_NOTES_MODULES

    q = (question or "").lower()
    mods = {mod for _label, keys, mod, _fn in _LOG_NOTES_MODULES if any(k.lower() in q for k in keys)}
    if "중앙관제" in q:
        mods |= {"central_control_room", "ccr_facility"}
    return mods


async def _gather_daily_logs(
    db: AsyncSession,
    question: str,
    building_rows: list[Building],
    matched_ids: set[int],
    rng: tuple[date, date] | None,
    today: date,
) -> list[dict[str, Any]]:
    from ai_analysis import _LOG_NOTES_MODULES

    specific = _question_log_modules(question)
    focused = bool(specific or matched_ids)
    if rng:
        d_from, d_to = rng
    else:
        days = FOCUSED_LOG_DAYS if focused else DEFAULT_LOG_DAYS
        d_from, d_to = today - timedelta(days=days - 1), today

    out: list[dict[str, Any]] = []
    for label, _keys, module_name, checker_name in _LOG_NOTES_MODULES:
        if specific and module_name not in specific:
            continue
        model = getattr(models, _LOG_DAILY_MODELS.get(module_name, ""), None)
        if model is None:
            continue
        try:
            module = importlib.import_module(module_name)
            is_building = getattr(module, checker_name)
            schema = module.load_schema() if hasattr(module, "load_schema") else {}
        except Exception:
            continue
        glossary = _schema_glossary(schema)
        for b in building_rows:
            if matched_ids and not specific and b.id not in matched_ids:
                continue
            try:
                if not is_building(b):
                    continue
            except Exception:
                continue
            total = int(
                (
                    await db.execute(
                        select(func.count(model.id)).where(
                            model.building_id == b.id,
                            model.log_date >= d_from,
                            model.log_date <= d_to,
                        )
                    )
                ).scalar()
                or 0
            )
            rows = (
                await db.execute(
                    select(model)
                    .where(
                        model.building_id == b.id,
                        model.log_date >= d_from,
                        model.log_date <= d_to,
                    )
                    .order_by(model.log_date.desc())
                    .limit(MAX_LOG_DAYS_PER_BUILDING)
                )
            ).scalars().all()
            days_out: list[dict[str, str]] = []
            all_pairs: list[str] = []
            for row in rows:
                pairs: list[str] = []
                _flatten(row.data or {}, "", pairs)
                if not pairs:
                    continue
                all_pairs.extend(pairs)
                days_out.append(
                    {"date": row.log_date.isoformat(), "values": _clip("; ".join(pairs), 15000)}
                )
            segs = _path_segments(all_pairs)
            item: dict[str, Any] = {
                "log": label,
                "building": b.name,
                "period": f"{d_from.isoformat()}~{d_to.isoformat()}",
                "days_in_period": total,
                "glossary": [f"{k}={v}" for k, v in glossary.items() if k in segs],
                "days": days_out,
            }
            if total > len(rows):
                item["days_total"] = total
            out.append(item)
    return out


# ── 점검일지 월보 ────────────────────────────────────────────────

_MONTHLY_KEYWORDS = ("월보", "월간", "월별", "월 사용량", "월사용량", "사용량", "누계", "월합계", "월 합계")
_MONTHLY_DEF_KEYS = ("schema", "breakers", "meters", "fields", "prev_day")
_MONTHLY_SKIP_LEAF = ("multiplier", "reading", "meter_id", "id", "name", "unit")


def monthly_report_requested(question: str) -> bool:
    q = question or ""
    return any(k in q for k in _MONTHLY_KEYWORDS)


def _report_months(rng: tuple[date, date] | None, today: date) -> list[tuple[int, int]]:
    if not rng:
        prev = today.replace(day=1) - timedelta(days=1)
        return [(today.year, today.month), (prev.year, prev.month)]
    months: list[tuple[int, int]] = []
    y, m = rng[1].year, rng[1].month
    while (y, m) >= (rng[0].year, rng[0].month) and len(months) < 3:
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return months


def _report_glossary(report: Any) -> dict[str, str]:
    out: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            key = node.get("meter_id") or node.get("id") or node.get("col")
            name = node.get("name") or node.get("label") or node.get("metric") or node.get("item")
            if isinstance(key, str) and isinstance(name, str) and name.strip():
                unit = node.get("unit")
                out.setdefault(key, name.strip() + (f"({unit})" if isinstance(unit, str) and unit else ""))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(report)
    return out


def _flatten_report(obj: Any, prefix: str, out: list[str], gloss: dict[str, str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            if ks in _MONTHLY_SKIP_LEAF or ks.startswith("_"):
                continue
            label = gloss.get(ks, ks)
            _flatten_report(v, f"{prefix}.{label}" if prefix else label, out, gloss)
    elif isinstance(obj, list):
        parent = prefix.rsplit(".", 1)[0] if "." in prefix else ""
        for i, v in enumerate(obj):
            key = v.get("meter_id") or v.get("id") if isinstance(v, dict) else None
            name = (v.get("name") or gloss.get(str(key), key)) if isinstance(v, dict) else None
            if name:
                _flatten_report(v, f"{parent}.{name}" if parent else str(name), out, gloss)
            else:
                _flatten_report(v, f"{prefix}[{i}]", out, gloss)
    else:
        if obj is None or obj is False:
            return
        s = str(obj).strip()
        if s:
            out.append(f"{prefix}={_clip(s, 120)}")


def _compact_report(node: Any, gloss: dict[str, str]) -> Any:
    """월보 dict → 정의 목록 제거, days는 '날짜: 항목=값; …' 한 줄씩."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in _MONTHLY_DEF_KEYS:
                continue
            if k == "days" and isinstance(v, list):
                lines = []
                for d in v:
                    if not isinstance(d, dict):
                        continue
                    pairs: list[str] = []
                    _flatten_report({kk: vv for kk, vv in d.items() if kk not in ("day", "date")}, "", pairs, gloss)
                    if pairs:
                        lines.append(f"{d.get('date') or d.get('day')}: " + "; ".join(pairs))
                out["days"] = lines
            elif k in ("totals", "monthly_totals", "summary"):
                pairs = []
                _flatten_report(v, "", pairs, gloss)
                if pairs:
                    out[k] = "; ".join(pairs)
            elif isinstance(v, (dict, list)):
                r = _compact_report(v, gloss)
                if r:
                    out[k] = r
            elif v not in (None, ""):
                out[k] = v
        return out
    if isinstance(node, list):
        return [r for r in (_compact_report(x, gloss) for x in node) if r]
    return node


async def gather_monthly_reports(
    db: AsyncSession,
    question: str,
    building_rows: list[Building],
    matched_ids: set[int],
    today: date,
) -> list[dict[str, Any]]:
    """점검일지 월보(화면의 월보 탭과 같은 계산) — 질문에 월보·월·사용량이 있을 때."""
    import calendar
    import inspect

    from ai_analysis import _LOG_NOTES_MODULES

    rng = question_date_range(question, today)
    specific = _question_log_modules(question)
    if not (monthly_report_requested(question) or (rng and (specific or matched_ids))):
        return []
    months = _report_months(rng, today)

    out: list[dict[str, Any]] = []
    for label, _keys, module_name, checker_name in _LOG_NOTES_MODULES:
        if module_name == "housing_substation":
            continue  # housing_monthly_reports 섹션에서 별도 제공
        if specific and module_name not in specific:
            continue
        model = getattr(models, _LOG_DAILY_MODELS.get(module_name, ""), None)
        try:
            module = importlib.import_module(module_name)
            is_building = getattr(module, checker_name)
            compute = getattr(module, "compute_monthly_report")
        except Exception:
            continue
        if model is None:
            continue
        params = inspect.signature(compute).parameters
        extra = getattr(module, "compute_transformer_monthly_report", None)
        for b in building_rows:
            if matched_ids and not specific and b.id not in matched_ids:
                continue
            try:
                if not is_building(b):
                    continue
            except Exception:
                continue
            for year, month in months:
                d_from = date(year, month, 1)
                d_to = date(year, month, calendar.monthrange(year, month)[1])
                rows = (
                    await db.execute(
                        select(model).where(
                            model.building_id == b.id,
                            model.log_date >= d_from,
                            model.log_date <= d_to,
                        )
                    )
                ).scalars().all()
                item: dict[str, Any] = {
                    "log": label,
                    "building": b.name,
                    "year": year,
                    "month": month,
                    "daily_rows_in_month": len(rows),
                }
                if not rows:
                    out.append(item)
                    continue
                prev_row = (
                    await db.execute(
                        select(model).where(
                            model.building_id == b.id, model.log_date == d_from - timedelta(days=1)
                        )
                    )
                ).scalar_one_or_none()
                args = {
                    "building_id": b.id,
                    "year": year,
                    "month": month,
                    "daily_rows": list(rows),
                    "prev_month_last_row": prev_row,
                }
                try:
                    report = compute(**{k: v for k, v in args.items() if k in params})
                    if callable(extra):
                        report["transformer"] = extra(year, month, list(rows))
                except Exception as e:  # noqa: BLE001
                    item["error"] = f"월보 계산 오류: {_clip(e, 200)}"
                    out.append(item)
                    continue
                gloss = _report_glossary(report)
                item["report"] = _compact_report(
                    {k: v for k, v in report.items() if k not in ("year", "month")}, gloss
                )
                out.append(item)
    return out


# ── 위험성평가 ──────────────────────────────────────────────────

def _risk_assessment_presets(keywords: list[str], question: str) -> dict[str, Any]:
    try:
        from risk_assessment import get_preset, list_majors, list_presets
    except Exception as e:  # pragma: no cover
        return {"error": f"위험성평가 모듈 로드 실패: {e}"}
    try:
        majors = list_majors()
        presets = list_presets()
    except Exception as e:
        return {"error": f"위험성평가 프리셋 조회 실패: {e}"}
    major_name = {m.get("id"): m.get("name") for m in majors}
    q = (question or "").lower()
    matched: list[dict[str, Any]] = []
    for p in presets:
        name = str(p.get("name") or "")
        compact = name.replace(" ", "").lower()
        hit = (compact and compact in q.replace(" ", "")) or _text_hit(
            keywords, name, p.get("sub_category"), p.get("description")
        )
        if hit and len(matched) < 6:
            full = get_preset(preset_id=str(p.get("id") or "")) or p
            rows = []
            for r in (full.get("ai_rows") or [])[:40]:
                rows.append(
                    [
                        r.get("work_class", ""),
                        r.get("phase", ""),
                        _clip(r.get("unit_task"), 80),
                        _clip(r.get("hazard"), 160),
                        r.get("injury", ""),
                        _clip(r.get("current"), 120),
                        r.get("freq_before", ""),
                        r.get("sev_before", ""),
                        _clip(r.get("improvements"), 220),
                        r.get("freq_after", ""),
                        r.get("sev_after", ""),
                        _clip(r.get("law"), 120),
                    ]
                )
            matched.append(
                {
                    "name": name,
                    "major": major_name.get(p.get("major_category"), p.get("major_category")),
                    "sub": p.get("sub_category") or "",
                    "five_m_one_e": full.get("five_m_one_e") or {},
                    "assessment_rows": _table(
                        [
                            "작업구분", "단계", "단위작업", "유해위험요인", "재해형태", "현재안전조치",
                            "빈도(전)", "강도(전)", "개선대책", "빈도(후)", "강도(후)", "관련법령",
                        ],
                        rows,
                    ),
                }
            )
    return {
        "majors": [m.get("name") for m in majors],
        "presets": _table(
            ["대분류", "중분류", "작업명", "출처"],
            [
                [
                    major_name.get(p.get("major_category"), p.get("major_category")),
                    p.get("sub_category") or "",
                    p.get("name") or "",
                    p.get("source") or "",
                ]
                for p in presets
            ],
        ),
        "matched_presets": matched,
    }


# ── 메인 ────────────────────────────────────────────────────────

async def gather_focus_data(
    db: AsyncSession,
    question: str,
    building_rows: list[Building],
    matched_building_ids: set[int] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """질문 기준 메뉴별 원본 데이터 (GPT 우선 근거)."""
    today = today or date.today()
    keywords = question_keywords(question)
    rng = question_date_range(question, today)
    matched_ids = set(matched_building_ids or ())

    def window(default_days: int) -> tuple[date, date]:
        return rng if rng else (today - timedelta(days=default_days - 1), today)

    # 위치 맵: zone → (건물, 층, 구역)
    bname = {b.id: b.name for b in building_rows}
    site_of = {b.id: (b.site.name if getattr(b, "site", None) else "") for b in building_rows}
    floors = (await db.execute(select(Floor).where(Floor.is_active == True))).scalars().all()  # noqa: E712
    zones = (await db.execute(select(Zone).where(Zone.is_active == True))).scalars().all()  # noqa: E712
    floor_map = {f.id: f for f in floors}
    zone_loc: dict[int, tuple[int | None, str, str, str]] = {}
    for z in zones:
        f = floor_map.get(z.floor_id)
        bid = f.building_id if f else None
        zone_loc[z.id] = (bid, bname.get(bid, ""), f.name if f else "", z.name or "")

    equipment = (await db.execute(select(Equipment))).scalars().all()
    eq_map = {e.id: e for e in equipment}

    def eq_bid(eid: int | None) -> int | None:
        e = eq_map.get(eid) if eid else None
        return zone_loc.get(e.zone_id, (None,))[0] if e else None

    def eq_label(eid: int | None) -> str:
        e = eq_map.get(eid) if eid else None
        if not e:
            return ""
        return f"{e.name}({e.code})" if e.code else e.name

    def in_scope(bid: int | None) -> bool:
        return not matched_ids or bid in matched_ids

    partners = (await db.execute(select(Partner).order_by(Partner.name))).scalars().all()
    partner_name = {p.id: p.name for p in partners}

    focus: dict[str, Any] = {
        "meta": {
            "today": today.isoformat(),
            "question_period": f"{rng[0].isoformat()}~{rng[1].isoformat()}" if rng else "",
            "default_periods": (
                f"기간 미지정 시 정비의뢰·정비이력 최근 {DEFAULT_WORK_DAYS}일, PM점검·자재입출고 "
                f"최근 {DEFAULT_PM_DAYS}일, 점검일지 최근 {DEFAULT_LOG_DAYS}일(특정 일지·건물 질문은 "
                f"{FOCUSED_LOG_DAYS}일)"
            ),
            "keywords": keywords,
            "building_filter": [bname.get(i, str(i)) for i in matched_ids],
        }
    }

    # 1) 사업장/건물 구조
    eq_by_zone: dict[int, int] = {}
    for e in equipment:
        if e.is_active and e.zone_id:
            eq_by_zone[e.zone_id] = eq_by_zone.get(e.zone_id, 0) + 1
    floors_by_b: dict[int, list[Floor]] = {}
    for f in floors:
        floors_by_b.setdefault(f.building_id, []).append(f)
    zones_by_f: dict[int, list[Zone]] = {}
    for z in zones:
        zones_by_f.setdefault(z.floor_id, []).append(z)
    building_table = []
    structure = []
    for b in building_rows:
        bf = sorted(floors_by_b.get(b.id, []), key=lambda f: (f.level or 0, f.name or ""))
        bz = [z for f in bf for z in zones_by_f.get(f.id, [])]
        n_eq = sum(eq_by_zone.get(z.id, 0) for z in bz)
        building_table.append(
            [b.id, site_of.get(b.id, ""), b.name, b.code or "", b.manager_name or "", len(bf), len(bz), n_eq]
        )
        if in_scope(b.id):
            structure.append(
                {
                    "building": b.name,
                    "floors": [
                        f"{f.name}: "
                        + ", ".join(
                            f"{z.name}({eq_by_zone.get(z.id, 0)})" for z in zones_by_f.get(f.id, [])
                        )
                        for f in bf
                    ],
                }
            )
    focus["sites_buildings"] = {
        "buildings": _table(
            ["id", "사업장", "건물", "코드", "담당자", "층수", "구역수", "설비수"], building_table
        ),
        "structure_floor_zone_equipment_count": structure,
    }

    # 2) 설비관리
    eq_rows = []
    hits: list[Equipment] = []
    for e in equipment:
        if not e.is_active:
            continue
        bid, b_name, f_name, z_name = zone_loc.get(e.zone_id, (None, "", "", ""))
        hit = _text_hit(keywords, e.name, e.code, e.category, e.manufacturer, e.model, e.description)
        if hit:
            hits.append(e)
        if not in_scope(bid) and not hit:
            continue
        eq_rows.append(
            (
                0 if hit else 1,
                [
                    e.id, e.code or "", e.name, _enum_val(e.category), b_name, f_name, z_name,
                    e.status or "", e.manufacturer or "", e.model or "", _d(e.installed_at),
                    e.running_hours if e.running_hours is not None else "", e.manager_name or "",
                ],
            )
        )
    eq_rows.sort(key=lambda t: (t[0], str(t[1][4]), str(t[1][2])))
    focus["equipment"] = {
        "active_total": sum(1 for e in equipment if e.is_active),
        "list": _table(
            ["id", "코드", "설비명", "분류", "건물", "층", "구역", "상태", "제조사", "모델", "설치일",
             "가동시간", "담당자"],
            [r for _, r in eq_rows[:MAX_ROWS]],
        ),
    }
    if len(eq_rows) > MAX_ROWS:
        focus["equipment"]["list_total"] = len(eq_rows)
    if hits:
        hit_ids = [e.id for e in hits[:30]]
        pm_s = (
            await db.execute(select(PMSchedule).where(PMSchedule.equipment_id.in_(hit_ids)))
        ).scalars().all()
        cons = (
            await db.execute(select(Consumable).where(Consumable.equipment_id.in_(hit_ids)))
        ).scalars().all()
        maint = (
            await db.execute(
                select(MaintenanceRecord)
                .where(MaintenanceRecord.equipment_id.in_(hit_ids))
                .order_by(MaintenanceRecord.work_date.desc(), MaintenanceRecord.id.desc())
                .limit(300)
            )
        ).scalars().all()
        wos = (
            await db.execute(
                select(WorkOrder)
                .where(WorkOrder.equipment_id.in_(hit_ids), WorkOrder.is_active == True)  # noqa: E712
                .order_by(WorkOrder.id.desc())
                .limit(300)
            )
        ).scalars().all()
        details = []
        for e in hits[:30]:
            _bid, b_name, f_name, z_name = zone_loc.get(e.zone_id, (None, "", "", ""))
            extra = e.extra_data
            if isinstance(extra, str):
                extra_s = extra
            else:
                extra_s = json.dumps(extra, ensure_ascii=False, default=str) if extra else ""
            details.append(
                {
                    "equipment": eq_label(e.id),
                    "location": " / ".join(x for x in (b_name, f_name, z_name) if x),
                    "category": _enum_val(e.category),
                    "status": e.status or "",
                    "manufacturer": e.manufacturer or "",
                    "model": e.model or "",
                    "serial_no": e.serial_no or "",
                    "installed_at": _d(e.installed_at),
                    "running_hours": e.running_hours,
                    "description": _clip(e.description, 500),
                    "extra_data": _clip(extra_s, 800),
                    "pm_schedules": [
                        f"{s.title} | 주기 {_enum_val(s.frequency)} | 다음 {_d(s.next_due)} | 최근 {_d(s.last_done)}"
                        for s in pm_s
                        if s.equipment_id == e.id
                    ],
                    "consumables": [
                        f"{c.name} | 재고 {c.stock_qty}/{c.safety_stock} | 최근교체 {_d(c.last_replaced)} | "
                        f"다음교체 {_d(c.next_replace)}"
                        for c in cons
                        if c.equipment_id == e.id
                    ],
                    "maintenance_history": [
                        f"{_d(m.work_date)} {m.title} | 원인 {_clip(m.cause, 120)} | 조치 {_clip(m.action, 200)}"
                        for m in maint
                        if m.equipment_id == e.id
                    ][:20],
                    "work_orders": [
                        f"#{w.id} {_d(w.created_at)} [{_enum_val(w.status)}] {w.title} | 조치 {_clip(w.action, 160)}"
                        for w in wos
                        if w.equipment_id == e.id
                    ][:20],
                }
            )
        focus["equipment"]["keyword_matches"] = details

    # 3) 정비관리 — 정비의뢰
    w_from, w_to = window(DEFAULT_WORK_DAYS)
    dt_from = datetime.combine(w_from, datetime.min.time())
    dt_to = datetime.combine(w_to, datetime.max.time())
    wo_period = (
        await db.execute(
            select(WorkOrder)
            .where(
                WorkOrder.is_active == True,  # noqa: E712
                or_(
                    WorkOrder.created_at.between(dt_from, dt_to),
                    WorkOrder.completed_at.between(dt_from, dt_to),
                    WorkOrder.scheduled_date.between(w_from, w_to),
                ),
            )
            .order_by(WorkOrder.id.desc())
        )
    ).scalars().all()
    wo_kw: list[WorkOrder] = []
    if keywords:
        wo_kw = (
            await db.execute(
                select(WorkOrder)
                .where(
                    WorkOrder.is_active == True,  # noqa: E712
                    _kw_filter(
                        keywords, WorkOrder.title, WorkOrder.description, WorkOrder.cause,
                        WorkOrder.action, WorkOrder.parts_used, WorkOrder.work_type,
                    ),
                )
                .order_by(WorkOrder.id.desc())
                .limit(300)
            )
        ).scalars().all()

    def wo_row(w: WorkOrder) -> list[Any]:
        bid = eq_bid(w.equipment_id)
        return [
            w.id, _d(w.created_at), bname.get(bid, "") if bid else "", eq_label(w.equipment_id),
            _clip(w.title, 120), _enum_val(w.status), w.priority or "", w.work_type or "",
            w.requester_name or "", w.assignee_name or "", partner_name.get(w.partner_id, ""),
            _d(w.scheduled_date), _d(w.completed_at), _clip(w.description, 300), _clip(w.cause, 200),
            _clip(w.action, 300), _clip(w.parts_used, 150), w.cost if w.cost is not None else "",
            w.work_hours if w.work_hours is not None else "", w.risk_grade or "",
        ]

    wo_cols = [
        "id", "접수일", "건물", "설비", "제목", "상태", "우선순위", "작업유형", "요청자", "담당자", "협력사",
        "예정일", "완료일", "의뢰내용", "원인", "조치내용", "사용부품", "비용", "작업시간", "위험등급",
    ]
    period_rows = [w for w in wo_period if in_scope(eq_bid(w.equipment_id)) or not w.equipment_id]
    status_count: dict[str, int] = {}
    for w in period_rows:
        k = _enum_val(w.status)
        status_count[k] = status_count.get(k, 0) + 1
    seen = {w.id for w in period_rows}
    focus["work_orders"] = {
        "period": f"{w_from.isoformat()}~{w_to.isoformat()}",
        "period_count": len(period_rows),
        "period_by_status": status_count,
        "period_list": _table(wo_cols, [wo_row(w) for w in period_rows[:MAX_ROWS]]),
        "keyword_hits_all_time": _table(wo_cols, [wo_row(w) for w in wo_kw if w.id not in seen]),
    }

    # 3-1) 정비이력
    maint_period = (
        await db.execute(
            select(MaintenanceRecord)
            .where(
                or_(
                    MaintenanceRecord.work_date.between(w_from, w_to),
                    MaintenanceRecord.created_at.between(dt_from, dt_to),
                )
            )
            .order_by(MaintenanceRecord.work_date.desc(), MaintenanceRecord.id.desc())
        )
    ).scalars().all()
    maint_rows = [
        [
            m.id, _d(m.work_date), bname.get(eq_bid(m.equipment_id), ""), eq_label(m.equipment_id),
            _clip(m.title, 120), m.worker_name or "", _clip(m.cause, 200), _clip(m.action, 300),
            _clip(m.parts_used, 150), m.work_hours if m.work_hours is not None else "",
            m.cost if m.cost is not None else "", m.work_order_id or "", _clip(m.note, 200),
        ]
        for m in maint_period
        if in_scope(eq_bid(m.equipment_id))
    ]
    focus["maintenance_records"] = {
        "period": f"{w_from.isoformat()}~{w_to.isoformat()}",
        "period_count": len(maint_rows),
        "list": _table(
            ["id", "작업일", "건물", "설비", "제목", "작업자", "원인", "조치", "사용부품", "작업시간", "비용",
             "정비의뢰id", "비고"],
            maint_rows[:MAX_ROWS],
        ),
    }

    # 4) 점검(PM)
    schedules = (
        await db.execute(
            select(PMSchedule).where(PMSchedule.is_active == True).order_by(PMSchedule.next_due)  # noqa: E712
        )
    ).scalars().all()
    sched_title = {s.id: s.title for s in schedules}
    sched_rows = []
    overdue = 0
    for s in schedules:
        bid = eq_bid(s.equipment_id)
        if not in_scope(bid):
            continue
        late = bool(s.next_due and s.next_due < today)
        overdue += int(late)
        sched_rows.append(
            [
                s.id, bname.get(bid, ""), eq_label(s.equipment_id), s.title, _enum_val(s.frequency),
                _d(s.next_due), _d(s.last_done), s.assignee_name or "", "Y" if late else "",
                _json_s(s.checklist, 200),
            ]
        )
    p_from, p_to = window(DEFAULT_PM_DAYS)
    inspections = (
        await db.execute(
            select(PMInspection)
            .where(
                PMInspection.inspected_at.between(
                    datetime.combine(p_from, datetime.min.time()),
                    datetime.combine(p_to, datetime.max.time()),
                )
            )
            .order_by(PMInspection.inspected_at.desc())
        )
    ).scalars().all()
    insp_rows = []
    result_count: dict[str, int] = {}
    for p in inspections:
        bid = eq_bid(p.equipment_id)
        if not in_scope(bid):
            continue
        r = _enum_val(p.result)
        result_count[r] = result_count.get(r, 0) + 1
        insp_rows.append(
            [
                p.id, _d(p.inspected_at), bname.get(bid, ""), eq_label(p.equipment_id),
                sched_title.get(p.schedule_id, ""), r, p.inspector_name or "", _clip(p.note, 300),
                p.work_order_id or "",
            ]
        )
    focus["pm"] = {
        "active_schedules": len(sched_rows),
        "overdue_schedules": overdue,
        "schedules": _table(
            ["id", "건물", "설비", "점검명", "주기", "다음예정", "최근실시", "담당자", "지연", "체크리스트"],
            sched_rows[:MAX_ROWS],
        ),
        "inspection_period": f"{p_from.isoformat()}~{p_to.isoformat()}",
        "inspection_results": result_count,
        "inspections": _table(
            ["id", "점검일시", "건물", "설비", "점검명", "결과", "점검자", "점검내용", "정비의뢰id"],
            insp_rows[:MAX_ROWS],
        ),
    }

    # 5) 점검일지(운영일보 16종)
    focus["inspection_logs"] = await _gather_daily_logs(
        db, question, building_rows, matched_ids, rng, today
    )

    # 6) 위험성평가
    risk = _risk_assessment_presets(keywords, question)
    risk["partner_risk"] = _table(
        ["협력사", "코드", "위험등급", "유해위험", "안전대책", "계약만료"],
        [
            [p.name, p.code or "", p.risk_grade or "", _clip(p.hazard_content, 200),
             _clip(p.safety_measures, 200), _d(p.contract_end)]
            for p in partners
            if p.is_active
        ],
    )
    risk["work_order_risk"] = _table(
        ["id", "접수일", "제목", "협력사", "위험등급", "유해위험내용", "안전조치"],
        [
            [w.id, _d(w.created_at), _clip(w.title, 100), partner_name.get(w.partner_id, ""),
             w.risk_grade or "", _clip(w.hazard_content, 250), _clip(w.safety_measures, 250)]
            for w in period_rows
            if (w.hazard_content or w.safety_measures or w.risk_grade)
        ],
    )
    d_from, d_to = window(DEFAULT_PM_DAYS)
    d1_rows = (
        await db.execute(
            select(D1Plan)
            .where(D1Plan.work_date.between(d_from, d_to + timedelta(days=0 if rng else 30)))
            .order_by(D1Plan.work_date.desc(), D1Plan.id.desc())
        )
    ).scalars().all()
    d1_scoped = [p for p in d1_rows if in_scope(p.building_id) or not p.building_id]

    risk["d1_jsa_permit"] = _table(
        ["id", "작업일", "제목", "JSA(위험성평가)", "작업허가", "허가번호"],
        [
            [p.id, _d(p.work_date), _clip(p.title, 100), _json_s(p.jsa_data, 600),
             _json_s(p.permit_data, 300), p.permit_no or ""]
            for p in d1_scoped
            if p.jsa_data or p.permit_data
        ],
    )
    focus["risk_assessment"] = risk

    # 7) D-1 작업계획
    focus["d1_plans"] = {
        "period": f"{d_from.isoformat()}~{d_to.isoformat()}" + ("" if rng else " (+향후 30일)"),
        "list": _table(
            ["id", "작업일", "건물", "설비", "제목", "작업내용", "작업시간", "협력사", "인원", "긴급", "상태",
             "TBM"],
            [
                [
                    p.id, _d(p.work_date), bname.get(p.building_id, ""), eq_label(p.equipment_id),
                    _clip(p.title, 100), _clip(p.work_content, 250), p.work_time or "",
                    partner_name.get(p.partner_id, ""), p.worker_count or "", "Y" if p.is_urgent else "",
                    _enum_val(p.status), _json_s(p.tbm_data, 200),
                ]
                for p in d1_scoped
            ],
        ),
    }

    # 8) 자재관리
    items = (await db.execute(select(MaterialItem).order_by(MaterialItem.name))).scalars().all()
    item_rows = [
        (
            0 if _text_hit(keywords, m.name, m.spec, m.group_name, m.remarks) else 1,
            [m.name, int(m.quantity or 0), _clip(m.spec, 80), m.group_name or "", m.location or "",
             _clip(m.remarks, 120), _d(m.updated_at)],
        )
        for m in items
    ]
    item_rows.sort(key=lambda t: t[0])
    l_from, l_to = window(DEFAULT_MATERIAL_LOG_DAYS)
    logs = (
        await db.execute(
            select(MaterialLog)
            .where(
                MaterialLog.created_at.between(
                    datetime.combine(l_from, datetime.min.time()),
                    datetime.combine(l_to, datetime.max.time()),
                )
            )
            .order_by(MaterialLog.created_at.desc())
        )
    ).scalars().all()
    consumables = (await db.execute(select(Consumable).order_by(Consumable.next_replace))).scalars().all()
    focus["materials"] = {
        "item_count": len(items),
        "items": _table(
            ["자재명", "수량", "규격", "분류", "위치", "비고", "수정일"], [r for _, r in item_rows[:MAX_ROWS]]
        ),
        "low_stock_le5": [m.name for m in items if int(m.quantity or 0) <= 5],
        "log_period": f"{l_from.isoformat()}~{l_to.isoformat()}",
        "logs": _table(
            ["일시", "구분", "자재명", "수량", "사유"],
            [[_d(x.created_at), x.action or "", x.name, x.quantity, _clip(x.reason, 150)] for x in logs[:MAX_ROWS]],
        ),
        "consumables": _table(
            ["설비", "소모품", "교체기준", "최근교체", "다음교체", "재고", "안전재고", "재고부족"],
            [
                [eq_label(c.equipment_id), c.name, _clip(c.replace_criteria, 80), _d(c.last_replaced),
                 _d(c.next_replace), c.stock_qty, c.safety_stock,
                 "Y" if (c.stock_qty or 0) < (c.safety_stock or 0) else ""]
                for c in consumables
                if in_scope(eq_bid(c.equipment_id))
            ],
        ),
    }

    focus["partners"] = _table(
        ["id", "협력사", "코드", "담당자", "계약만료", "활성"],
        [[p.id, p.name, p.code or "", p.contact_name or "", _d(p.contract_end), "Y" if p.is_active else ""]
         for p in partners],
    )
    return focus


# ── 컨텍스트 한도 맞춤 ──────────────────────────────────────────

def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=str, separators=(",", ":"))


def _collect_trimmable(node: Any, out: list[tuple[int, dict, str]]) -> bool:
    """잘라낼 수 있는 '말단' 목록(dict 값, 길이>1, 하위에 잘라낼 목록 없음)을 모은다."""
    has = False
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, list):
                child = False
                for e in v:
                    child = _collect_trimmable(e, out) or child
                if len(v) > 1 and not child and k != "columns":
                    out.append((len(_dump(v)), node, k))
                has = has or child or (len(v) > 1 and k != "columns")
            elif isinstance(v, dict):
                has = _collect_trimmable(v, out) or has
    elif isinstance(node, list):
        for e in node:
            has = _collect_trimmable(e, out) or has
    return has


def fit_json(obj: Any, budget: int) -> str:
    """JSON 문자열이 budget 이하가 되도록 큰 목록부터 뒤쪽 항목을 줄인다(`<key>_total`에 원래 건수)."""
    text = _dump(obj)
    if len(text) <= budget:
        return text
    data = json.loads(text)
    for _ in range(600):
        if len(text) <= budget:
            return text
        cands: list[tuple[int, dict, str]] = []
        _collect_trimmable(data, cands)
        if not cands:
            break
        size, parent, key = max(cands, key=lambda c: c[0])
        lst = parent[key]
        over = len(text) - budget
        keep = int(len(lst) * (1 - over / max(size, 1)))
        keep = max(1, min(len(lst) - 1, max(keep, len(lst) // 2)))
        parent.setdefault(f"{key}_total", len(lst))
        parent[key] = lst[:keep]
        text = _dump(data)
    return text if len(text) <= budget else text[:budget] + "..."
