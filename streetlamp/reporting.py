# reporting.py — 최근 48시간 접수 엑셀 + SMTP 메일
from __future__ import annotations

import asyncio
import base64
import os
import smtplib
import socket
import ssl
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from zoneinfo import ZoneInfo

import httpx
import openpyxl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from streetlamp.models import MaintenanceRequest, RequestType, RequestStatus
from streetlamp.settings_service import get_setting


class SmtpNotConfiguredError(Exception):
    """SMTP_HOST 가 비어 있을 때 (Resend 미사용)."""


class ResendApiError(Exception):
    """Resend HTTP API 오류."""


RequestTypeLabel = {
    RequestType.outage.value: "불점등",
    RequestType.globe_broken.value: "글로브 파손",
    RequestType.fall_risk.value: "전도 위험",
    RequestType.low_brightness.value: "밝기 불량",
    RequestType.other.value: "기타",
}
RequestStatusLabel = {
    RequestStatus.received.value: "접수",
    RequestStatus.in_progress.value: "처리중",
    RequestStatus.done.value: "완료",
}


def _kst_dt_str(dt: datetime | None) -> str:
    if not isinstance(dt, datetime):
        return ""
    d = dt
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")


async def fetch_requests_last_48h(session: AsyncSession) -> list[MaintenanceRequest]:
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=48)
    cutoff_naive = cutoff.replace(tzinfo=None)
    result = await session.execute(
        select(MaintenanceRequest)
        .where(MaintenanceRequest.created_at >= cutoff_naive)
        .order_by(MaintenanceRequest.created_at.desc())
    )
    return list(result.scalars().all())


def build_xlsx_bytes(rows: list[MaintenanceRequest]) -> tuple[bytes, str]:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "48h"
    ws.append(
        [
            "접수번호",
            "가로등 No",
            "접수일시(KST)",
            "완료일시(KST)",
            "이름",
            "전화번호",
            "정비유형",
            "내용",
            "작업비고",
            "상태",
        ]
    )
    for r in rows:
        created_str = _kst_dt_str(r.created_at)
        completed_str = _kst_dt_str(r.completed_at) if r.completed_at else ""
        ws.append(
            [
                r.id,
                r.lamp_id,
                created_str,
                completed_str,
                r.name,
                r.phone,
                RequestTypeLabel.get(r.request_type.value, r.request_type.value),
                r.content or "",
                r.work_memo or "",
                RequestStatusLabel.get(r.status.value, r.status.value),
            ]
        )
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M")
    filename = f"streetlamp_48h_{ts}.xlsx"
    return bio.read(), filename


def _smtp_log(line: str) -> None:
    """Render 로그에는 보통 print 가 확실히 잡힘(logging.INFO 는 레벨 때문에 안 보일 수 있음)."""
    print(line, flush=True)


def _send_via_resend_sync(
    to_addr: str,
    subject: str,
    body: str,
    attachment: bytes,
    attach_name: str,
) -> None:
    """Render 등에서 SMTP(587/465)가 막힐 때 — HTTPS api.resend.com 사용."""
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if not key:
        raise ResendApiError("RESEND_API_KEY 가 비어 있습니다.")
    mail_from = (os.environ.get("RESEND_FROM", "") or "").strip()
    # Resend는 @gmail.com 등 임의 발신 불가(도메인 검증 필요). SMTP_FROM 을 자동 사용하면 403 남.
    if not mail_from and os.environ.get("RESEND_USE_SMTP_FROM", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        mail_from = (os.environ.get("SMTP_FROM", "") or "").strip()
    if not mail_from:
        mail_from = "onboarding@resend.dev"
    _smtp_log(f"[resend] POST /emails to={to_addr!r} from={mail_from!r} attach={attach_name!r}")
    payload = {
        "from": mail_from,
        "to": [to_addr],
        "subject": subject,
        "text": body,
        "attachments": [
            {
                "filename": attach_name,
                "content": base64.b64encode(attachment).decode("ascii"),
            }
        ],
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.RequestError as e:
        raise ResendApiError(f"Resend 연결 실패: {e}") from e
    if r.status_code >= 400:
        raise ResendApiError(f"HTTP {r.status_code}: {r.text[:800]}")
    try:
        jid = r.json().get("id", "")
    except Exception:
        jid = ""
    _smtp_log(f"[resend] ok id={jid!r}")


def _tcp_connect_ipv4(host: str, port: int, timeout: float) -> socket.socket:
    """smtp.gmail.com 등에 IPv4로만 TCP 연결."""
    last_exc: OSError | None = None
    for res in socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM):
        af, socktype, proto, _canon, sockaddr = res
        sock: socket.socket | None = None
        try:
            sock = socket.socket(af, socktype, proto)
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            _smtp_log(f"[smtp] tcp open {sockaddr[0]}:{sockaddr[1]}")
            return sock
        except OSError as e:
            last_exc = e
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    if last_exc is not None:
        raise last_exc
    raise OSError(f"IPv4 연결 실패: {host!r}:{port}")


class _SMTPForceIPv4(smtplib.SMTP):
    """IPv6 경로가 없어 `Network is unreachable`(errno 101) 나는 환경(Render 등)용 — IPv4만 연결."""

    def _get_socket(self, host, port, timeout):
        self.timeout = timeout
        return _tcp_connect_ipv4(host, int(port), float(timeout))


class _SMTPSSLForceIPv4(smtplib.SMTP_SSL):
    """Gmail 465(SMTPS) 등: IPv4 TCP 후 즉시 TLS(암시적 SSL)."""

    def _get_socket(self, host, port, timeout):
        self.timeout = timeout
        plain = _tcp_connect_ipv4(host, int(port), float(timeout))
        return self.context.wrap_socket(plain, server_hostname=host)


def _send_email_sync(
    to_addr: str,
    subject: str,
    body: str,
    attachment: bytes,
    attach_name: str,
) -> None:
    if os.environ.get("RESEND_API_KEY", "").strip():
        _send_via_resend_sync(to_addr, subject, body, attachment, attach_name)
        return

    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        raise SmtpNotConfiguredError(
            "SMTP_HOST 가 없습니다. Render에서 Gmail SMTP 대신 "
            "RESEND_API_KEY(및 선택 RESEND_FROM)를 설정해 Resend로 보내거나, SMTP_HOST 를 채우세요."
        )

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    # Gmail 앱 비밀번호는 화면에 "abcd efgh ..." 로 보이므로 붙여넣 시 공백 제거
    password = "".join((os.environ.get("SMTP_PASSWORD", "") or "").split())
    mail_from = os.environ.get("SMTP_FROM", user).strip()
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
    try:
        timeout = float(os.environ.get("SMTP_TIMEOUT", "120"))
    except ValueError:
        timeout = 120.0
    use_implicit_ssl = os.environ.get("SMTP_USE_SSL", "").lower() in (
        "1",
        "true",
        "yes",
    ) or int(port) == 465

    _smtp_log(
        f"[smtp] start host={host!r} port={port} timeout={timeout}s "
        f"mode={'SMTPS(465)' if use_implicit_ssl else 'STARTTLS'} tls_starttls={use_tls} "
        f"user_set={bool(user)} pass_len={len(password)} from={mail_from!r} to={to_addr!r}"
    )

    msg = MIMEMultipart()
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = mail_from
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain", "utf-8"))
    part = MIMEApplication(attachment, _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    part.add_header("Content-Disposition", "attachment", filename=attach_name)
    msg.attach(part)

    try:
        if use_implicit_ssl:
            ctx = ssl.create_default_context()
            with _SMTPSSLForceIPv4(host, port, timeout=timeout, context=ctx) as smtp:
                _smtp_log(f"[smtp] connected (implicit TLS, port {port})")
                if user and password:
                    smtp.login(user, password)
                    _smtp_log("[smtp] login ok")
                _smtp_log("[smtp] sending...")
                refused = smtp.sendmail(mail_from, [to_addr], msg.as_string())
                if refused:
                    raise RuntimeError(f"SMTP가 수신 거부: {refused}")
        else:
            with _SMTPForceIPv4(host, port, timeout=timeout) as smtp:
                _smtp_log("[smtp] connected (plain)")
                if use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    _smtp_log("[smtp] starttls ok")
                if user and password:
                    smtp.login(user, password)
                    _smtp_log("[smtp] login ok")
                _smtp_log("[smtp] sending...")
                refused = smtp.sendmail(mail_from, [to_addr], msg.as_string())
                if refused:
                    raise RuntimeError(f"SMTP가 수신 거부: {refused}")
        _smtp_log(f"[smtp] sendmail ok from={mail_from!r} to={to_addr!r}")
    except Exception as e:
        _smtp_log(f"[smtp] FAILED {type(e).__name__}: {e}")
        raise


async def send_daily_report_email(session: AsyncSession, to_email: str) -> str:
    if not (to_email or "").strip():
        raise ValueError("받는 메일 주소가 비어 있습니다.")

    rows = await fetch_requests_last_48h(session)
    data, fname = build_xlsx_bytes(rows)
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    subject = f"[가로등 정비] 최근 48시간 접수 요약 ({now_kst} KST)"
    body = (
        f"최근 48시간 접수 건수: {len(rows)}건\n"
        f"발송 시각(KST): {now_kst}\n\n"
        "첨부 엑셀을 확인하세요.\n"
    )
    await asyncio.to_thread(
        _send_email_sync, to_email, subject, body, data, fname
    )
    via = "Resend(HTTPS)" if os.environ.get("RESEND_API_KEY", "").strip() else "SMTP"
    return (
        f"메일 발송 완료 ({via}) — 수신 주소: {to_email} (설정의 「받는 메일 주소」로 발송됨), 건수 {len(rows)}건. "
        "수신함·스팸함·프로모션함을 확인하세요. 다른 주소로 받으려면 설정에서 받는 메일을 바꾼 뒤 저장하세요."
    )


async def run_daily_report_pipeline(session: AsyncSession, to_email: str) -> str:
    """Resend·SMTP 미설정·발송 실패·DB 오류 시에도 문자열로 안내 (관리자 화면 500 방지)."""
    addr = (to_email or "").strip()
    if not addr:
        _smtp_log("[smtp] pipeline skip: empty report_email")
        return "받는 메일 주소가 비어 있습니다. 설정에서 받는 메일 주소를 입력·저장한 뒤 다시 시도하세요."

    try:
        out = await send_daily_report_email(session, addr)
        _smtp_log("[email] pipeline ok (see settings notice for recipient)")
        return out
    except SmtpNotConfiguredError as e:
        try:
            rows = await fetch_requests_last_48h(session)
            _, fname = build_xlsx_bytes(rows)
        except Exception as inner:
            msg = (
                f"메일 설정 오류: {e} | 접수 조회 오류: {type(inner).__name__}: {inner}"
            )
            _smtp_log(f"[email] pipeline fail: {msg}")
            return msg
        msg = (
            f"{e} "
            f"(대상 건수 {len(rows)}, 파일명 예: {fname})"
        )
        _smtp_log(f"[email] pipeline fail: {msg}")
        return msg
    except ResendApiError as e:
        _smtp_log(f"[resend] pipeline fail: {e}")
        return f"Resend 발송 실패: {e}"
    except RuntimeError as e:
        try:
            rows = await fetch_requests_last_48h(session)
            n = len(rows)
        except Exception:
            n = "?"
        msg = f"SMTP 오류: {e} (접수 {n}건)"
        _smtp_log(f"[email] pipeline fail: {msg}")
        return msg
    except ValueError as e:
        _smtp_log(f"[smtp] pipeline skip: {e}")
        return str(e)
    except Exception as e:
        msg = (
            f"메일 발송에 실패했습니다 ({type(e).__name__}): {e}\n\n"
            "Render 무료 등에서 **아웃바운드 SMTP(587/465)가 막혀 Timeout** 나는 경우가 많습니다. "
            "이때는 Gmail SMTP 대신 **Resend**(HTTPS)를 쓰세요: resend.com 가입 → API 키 발급 → "
            "Render Environment에 `RESEND_API_KEY=re_...` 추가(선택 `RESEND_FROM`, 미설정 시 onboarding@resend.dev). "
            "도메인 없이 테스트하면 Resend 정책상 수신 주소가 제한될 수 있습니다.\n\n"
            "SMTP를 계속 쓸 경우: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD(앱 비밀번호), SMTP_FROM, "
            "SMTP_USE_TLS · `SMTP_PORT=465`+`SMTP_USE_SSL=true` · `SMTP_TIMEOUT`(초). "
            "`TimeoutError`는 재배포 직후 재시도. DB 오류는 Render Logs SQL 메시지 확인."
        )
        _smtp_log(f"[smtp] pipeline fail: {type(e).__name__}: {e}")
        return msg
