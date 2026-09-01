# access_log.py — FMS 활동(접속·조회·등록·수정·삭제) 감사 이력
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from database import AsyncSessionLocal
from models import AccessLog, User

KST = ZoneInfo("Asia/Seoul")

EVENT_LABELS: dict[str, str] = {
    "login_success": "로그인 성공",
    "login_fail": "로그인 실패",
    "logout": "로그아웃",
    "page_view": "화면 조회",
    "create": "등록",
    "update": "수정",
    "delete": "삭제",
    "export": "내보내기",
    "action": "작업",
}

DETAIL_LABELS: dict[str, str] = {
    "invalid_credentials": "아이디/비밀번호 오류",
    "inactive": "비활성 계정",
    "pending_approval": "승인 대기",
}

# 긴 경로 우선
RESOURCE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/admin/inspection-logs2", "점검일지2"),
    ("/admin/inspection-logs", "점검일지"),
    ("/admin/work-orders", "정비접수/승인"),
    ("/admin/facility-section", "작업허가/승인"),
    ("/admin/risk-assessment", "위험성평가"),
    ("/admin/ai-analysis", "AI 분석"),
    ("/admin/streetlamp", "가로등"),
    ("/admin/equipment", "설비관리"),
    ("/admin/buildings", "사업장/건물"),
    ("/admin/sites", "사업장/건물"),
    ("/admin/schedules", "주요설비 일정"),
    ("/admin/notices", "공지사항"),
    ("/admin/dashboard", "Dashboard"),
    ("/admin/users", "계정관리"),
    ("/admin/server", "서버관리"),
    ("/admin/materials", "자재관리"),
    ("/admin/partners", "협력사"),
    ("/admin/pm", "점검(PM)"),
    ("/admin/d1", "정비 List(D-1)"),
    ("/admin/account", "내 계정"),
    ("/admin/login", "로그인"),
    ("/admin/signup", "회원가입"),
    ("/admin/logout", "로그아웃"),
    ("/swhq/", "점검일지2·제철소본부(QR)"),
    ("/ccrf/", "점검일지2·중앙관제실(설비)(QR)"),
    ("/ccr/", "점검일지2·중앙관제실(QR)"),
    ("/hs/", "점검일지2·주택변전소(QR)"),
    ("/eq/", "설비(QR)"),
    ("/lamp/", "가로등(QR)"),
    ("/admin/", "관리자"),
)

_SKIP_AUDIT_EXACT: frozenset[str] = frozenset(
    {
        "/admin/login",
        "/admin/logout",
        "/admin/server/access-logs",
        "/admin/server/status",
        "/admin/dashboard/kpi",
        "/admin/dashboard/server-status",
    }
)

_SKIP_AUDIT_PREFIXES: tuple[str, ...] = (
    "/static/",
    "/favicon",
)

_SKIP_AUDIT_SUFFIXES: tuple[str, ...] = (
    "/cursor",
    "/unlock",
    "/heartbeat",
)


def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return ""


def client_user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:500]


def resource_label(path: str) -> str:
    p = path or ""
    for prefix, label in RESOURCE_PREFIXES:
        if p.startswith(prefix):
            return label
    return "기타"


def classify_event(method: str, path: str) -> str:
    m = (method or "GET").upper()
    p = (path or "").lower()
    if m == "GET":
        if any(
            x in p
            for x in (
                "export",
                "download",
                "backup.zip",
                "manual.pptx",
                "presentation.pptx",
                ".xlsx",
                ".xls",
                ".zip",
                ".pptx",
                "qr-zip",
            )
        ):
            return "export"
        return "page_view"
    if m == "DELETE" or "/delete" in p or "/remove" in p:
        return "delete"
    if any(
        x in p
        for x in (
            "/add",
            "/create",
            "/new",
            "/signup",
            "/approve",
            "/register",
            "/stock-in",
            "/buildings",
        )
    ) and "/save" not in p:
        if "/delete" not in p and "/remove" not in p:
            return "create"
    if any(
        x in p
        for x in (
            "/save",
            "/update",
            "/edit",
            "/import",
            "/upload",
            "/close-day",
            "/inspect",
            "/advance",
            "/stock-out",
            "/status",
            "/permit",
            "/approve-d1",
            "/request-approval",
            "/assess",
            "/learn",
            "/ask",
            "/chat",
            "/ai-settings",
            "/reset",
        )
    ):
        return "update"
    if m in ("POST", "PUT", "PATCH"):
        return "action"
    return "page_view"


def build_summary(method: str, path: str, resource: str, event_type: str) -> str:
    m = (method or "GET").upper()
    label = EVENT_LABELS.get(event_type, event_type)
    if event_type == "page_view":
        return f"{resource} 화면 조회"
    if event_type == "export":
        return f"{resource} — 파일 내보내기/다운로드"
    if event_type == "login_success":
        return "로그인"
    if event_type == "login_fail":
        return "로그인 실패"
    if event_type == "logout":
        return "로그아웃"
    tail = path if len(path) <= 120 else path[:117] + "..."
    return f"{resource} — {label} ({m} {tail})"


def should_skip_audit(path: str, method: str) -> bool:
    p = path or ""
    m = (method or "GET").upper()
    if any(p.startswith(prefix) for prefix in _SKIP_AUDIT_PREFIXES):
        return True
    if p in _SKIP_AUDIT_EXACT:
        return True
    if any(p.endswith(suffix) for suffix in _SKIP_AUDIT_SUFFIXES):
        return True
    if p == "/admin/login" and m == "POST":
        return True
    if p == "/admin/logout" and m == "GET":
        return True
    return False


def should_audit(path: str, method: str, content_type: str | None) -> bool:
    if should_skip_audit(path, method):
        return False
    p = path or ""
    m = (method or "GET").upper()
    ct = (content_type or "").lower()

    if p.startswith("/admin"):
        if m != "GET":
            return True
        if classify_event(m, p) == "export":
            return True
        if "text/html" in ct:
            return True
        return False

    if m in ("POST", "PUT", "PATCH", "DELETE"):
        if p.startswith(("/hs/", "/ccr/", "/ccrf/", "/swhq/", "/eq/", "/lamp/")):
            return True
    return False


async def record_access_log(
    db: AsyncSession,
    *,
    event_type: str,
    username: str,
    request: Request | None = None,
    success: bool = False,
    user_id: int | None = None,
    display_name: str | None = None,
    detail: str | None = None,
    http_method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    resource: str | None = None,
    summary: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    req_path = path or (request.url.path if request else "") or ""
    req_method = (http_method or (request.method if request else "") or "GET").upper()
    res = resource or resource_label(req_path)
    ev = event_type or classify_event(req_method, req_path)
    summ = summary or build_summary(req_method, req_path, res, ev)
    row = AccessLog(
        event_type=ev[:32],
        username=(username or "").strip()[:64] or "-",
        user_id=user_id,
        display_name=(display_name or "")[:100] or None,
        ip_address=(ip_address or (client_ip(request) if request else "") or None),
        user_agent=(user_agent or (client_user_agent(request) if request else "") or None),
        success=success,
        detail=(detail or "")[:200] or None,
        http_method=req_method[:10] if req_method else None,
        path=req_path[:500] if req_path else None,
        status_code=status_code,
        resource=res[:100] if res else None,
        summary=summ[:500] if summ else None,
    )
    db.add(row)
    await db.commit()


async def write_audit_from_request(request: Request, response: Response) -> None:
    path = request.url.path or ""
    method = (request.method or "GET").upper()
    content_type = response.headers.get("content-type")
    if not should_audit(path, method, content_type):
        return

    user: User | None = getattr(request.state, "current_user", None)
    event_type = classify_event(method, path)
    resource = resource_label(path)
    summary = build_summary(method, path, resource, event_type)
    status = response.status_code
    success = status < 400

    if user:
        username = user.username
        user_id = user.id
        display_name = user.name
    else:
        username = "(비로그인)"
        user_id = None
        display_name = None

    async with AsyncSessionLocal() as db:
        await record_access_log(
            db,
            event_type=event_type,
            username=username,
            request=request,
            success=success,
            user_id=user_id,
            display_name=display_name,
            http_method=method,
            path=path,
            status_code=status,
            resource=resource,
            summary=summary,
        )


class AuditLogMiddleware(BaseHTTPMiddleware):
    """FMS /admin 및 QR 저장 요청 활동을 응답 후 DB에 기록."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            await write_audit_from_request(request, response)
        except Exception as e:
            print(f"[audit] skip: {e}", flush=True)
        return response


def _kst_day_bounds(date_from: str | None, date_to: str | None) -> tuple[datetime | None, datetime | None]:
    start = end = None
    if date_from:
        try:
            d = date.fromisoformat(date_from.strip())
            start = (
                datetime(d.year, d.month, d.day, tzinfo=KST)
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
        except ValueError:
            pass
    if date_to:
        try:
            d = date.fromisoformat(date_to.strip())
            end = (
                datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=KST)
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
        except ValueError:
            pass
    return start, end


def _fmt_kst(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


async def query_access_logs(
    db: AsyncSession,
    *,
    q: str = "",
    event: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    per_page = max(10, min(per_page, 200))
    page = max(1, page)
    stmt = select(AccessLog)
    count_stmt = select(func.count()).select_from(AccessLog)

    event_key = (event or "").strip()
    if event_key and event_key in EVENT_LABELS:
        stmt = stmt.where(AccessLog.event_type == event_key)
        count_stmt = count_stmt.where(AccessLog.event_type == event_key)

    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        cond = or_(
            AccessLog.username.ilike(like),
            AccessLog.display_name.ilike(like),
            AccessLog.ip_address.ilike(like),
            AccessLog.user_agent.ilike(like),
            AccessLog.detail.ilike(like),
            AccessLog.path.ilike(like),
            AccessLog.resource.ilike(like),
            AccessLog.summary.ilike(like),
            AccessLog.http_method.ilike(like),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    start, end = _kst_day_bounds(date_from, date_to)
    if start is not None:
        stmt = stmt.where(AccessLog.created_at >= start)
        count_stmt = count_stmt.where(AccessLog.created_at >= start)
    if end is not None:
        stmt = stmt.where(AccessLog.created_at <= end)
        count_stmt = count_stmt.where(AccessLog.created_at <= end)

    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages

    rows = (
        await db.execute(
            stmt.order_by(AccessLog.created_at.desc(), AccessLog.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()

    items = []
    for row in rows:
        detail = row.detail or ""
        summary = row.summary or detail or row.path or ""
        items.append(
            {
                "id": row.id,
                "event_type": row.event_type,
                "event_label": EVENT_LABELS.get(row.event_type, row.event_type),
                "username": row.username,
                "display_name": row.display_name or "",
                "ip_address": row.ip_address or "",
                "user_agent": row.user_agent or "",
                "success": bool(row.success),
                "detail": detail,
                "detail_label": DETAIL_LABELS.get(detail, detail),
                "http_method": row.http_method or "",
                "path": row.path or "",
                "status_code": row.status_code,
                "resource": row.resource or "",
                "summary": summary,
                "created_at": _fmt_kst(row.created_at),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }
