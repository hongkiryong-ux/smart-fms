"""서버 측 쓰기 권한 공통 검사 (화면에서 버튼을 숨기는 것만으로는 막히지 않는 요청 차단)."""
from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from auth import can_access_menu, can_create, can_delete, can_edit
from models import User, UserRole

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_QR_PREFIXES = (
    "hs", "ccr", "ccrf", "swhq", "swhall", "hcenter", "egym", "bdorm",
    "gtown", "p1538", "bahall", "s53", "bdae", "bshop", "rist", "ggy",
)
_QR_LOG_WRITE_RE = re.compile(r"^/(?:%s)/[^/]+/daily/.+$" % "|".join(_QR_PREFIXES))
_EQ_LOG_SAVE_RE = re.compile(r"^/eq/[^/]+/log/save$")
_EQ_PM_RE = re.compile(r"^/eq/[^/]+/pm-inspect$")
_ILOG_ADMIN_PREFIXES = ("/admin/inspection-logs2/", "/admin/inspection-logs/")
# 편집 세션 유지용 신호 — 화면을 열어 둔 조회자도 보낼 수 있음
_EDIT_SESSION_PING_RE = re.compile(r"/(?:cursor|unlock|heartbeat)$")

# 등록·수정·삭제 권한이 하나도 없는 조회 전용 계정이 보낼 수 있는 요청 (개인 설정·조회성 작업)
_READONLY_ALLOWED = tuple(
    re.compile(p)
    for p in (
        r"^/admin/account/",
        r"^/admin/notifications/",
        r"^/admin/presence/heartbeat$",
        r"^/admin/work-orders/my-partners$",
        r"^/admin/pm/my-buildings$",
        r"^/admin/ai-analysis/(chat|feedback|ai-settings|conversations/\d+/(rename|delete))$",
        r"^/admin/risk-assessment/(suggest-5m|assess|command|ai-settings|export-html|export-excel)$",
        r"^/admin/ilog2-page-excel$",
        r"/export(/|$)",
        r"^/lamp/",
        r"^/status$",
    )
)

# 협력사·외부업체는 기준정보·관리 화면을 바꿀 수 없음 (정비 처리·자재 등 업무 화면만)
_PARTNER_WRITE_DENIED = (
    "/admin/sites", "/admin/buildings", "/admin/floors", "/admin/zones",
    "/admin/partners", "/admin/dashboard", "/admin/schedules", "/admin/notices",
    "/admin/templates", "/admin/equipment", "/admin/equipment-types",
    "/admin/facility-section", "/admin/maintenance", "/admin/inspection-logs",
    "/admin/inspection-logs2", "/admin/streetlamp", "/admin/library",
)
# 협력사·외부업체에 열지 않는 화면 — AI는 전 업체 DB를 근거로 답하므로 업체 범위를 지킬 수 없음
_PARTNER_DENIED_ANY = ("/admin/ai-analysis",)

_PARTNER_ROLES = (UserRole.partner, UserRole.external)


def is_partner_role(user: User | None) -> bool:
    return user is not None and user.role in _PARTNER_ROLES


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _deny(request: Request, msg: str):
    accept = (request.headers.get("accept") or "").lower()
    ctype = (request.headers.get("content-type") or "").lower()
    wants_json = (
        "application/json" in accept
        or "application/json" in ctype
        or request.headers.get("x-requested-with")
        or request.url.path.endswith(("/save", "/heartbeat"))
    )
    if wants_json:
        return JSONResponse({"ok": False, "detail": "forbidden", "message": msg}, status_code=403)
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>권한 없음</title>"
        f"<p style='font-family:sans-serif;padding:2rem'>{msg} "
        "<a href='javascript:history.back()'>돌아가기</a></p>",
        status_code=403,
    )


_WO_ITEM_RE = re.compile(r"^/admin/work-orders/(\d+)(?:/|$)")
_WO_PARTNER_DENIED_ACTION_RE = re.compile(
    r"^/admin/work-orders/(?:\d+/(?:approve-d1|reject|unapprove-d1)|approve-d1-bulk)$"
)


async def check_partner_scope(request: Request, user: User):
    """협력사·외부업체: 다른 업체 정비건 접근 차단, D-1 승인·반려는 정비섹션 전용."""
    if not is_partner_role(user):
        return None
    path = request.url.path or ""
    if _WO_PARTNER_DENIED_ACTION_RE.match(path):
        return _deny(request, "D-1 승인·반려는 정비섹션에서 처리합니다.")
    m = _WO_ITEM_RE.match(path)
    if not m:
        return None
    from auth import user_own_partner_id
    from database import AsyncSessionLocal
    from models import WorkOrder

    async with AsyncSessionLocal() as session:
        wo = await session.get(WorkOrder, int(m.group(1)))
        if wo is None:
            return None
        if wo.requester_user_id and wo.requester_user_id == user.id:
            return None
        own = await user_own_partner_id(session, user)
    if own and int(wo.partner_id or 0) == int(own):
        return None
    return _deny(request, "본인 협력사에 지정된 정비 항목만 볼 수 있습니다.")


def check_request(request: Request, user: User):
    """허용되지 않는 요청이면 403 응답, 통과면 None."""
    path = request.url.path or ""
    method = (request.method or "GET").upper()

    if is_partner_role(user) and _under(path, _PARTNER_DENIED_ANY):
        return _deny(request, "협력사·외부업체 계정은 이 메뉴를 사용할 수 없습니다.")

    if method not in _UNSAFE:
        return None

    if user.role == UserRole.system_admin:
        return None

    if not (can_create(user) or can_edit(user) or can_delete(user)):
        if not any(rx.search(path) for rx in _READONLY_ALLOWED):
            return _deny(request, "조회 전용 계정은 저장·변경할 수 없습니다.")

    if is_partner_role(user) and _under(path, _PARTNER_WRITE_DENIED):
        return _deny(request, "협력사·외부업체 계정은 이 항목을 변경할 수 없습니다.")

    is_log_write = bool(_QR_LOG_WRITE_RE.match(path) or _EQ_LOG_SAVE_RE.match(path)) or (
        path.startswith(_ILOG_ADMIN_PREFIXES) and not _EDIT_SESSION_PING_RE.search(path)
    )
    if is_log_write:
        if not can_edit(user) or not can_access_menu(user, "inspection_logs2"):
            return _deny(request, "점검일지 수정 권한이 없습니다.")
    elif _EQ_PM_RE.match(path):
        if not can_edit(user) or not can_access_menu(user, "pm"):
            return _deny(request, "점검(PM) 수정 권한이 없습니다.")
    return None
