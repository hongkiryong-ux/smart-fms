# sms_notify.py — 신규 접수 시 Solapi SMS 알림
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone

import httpx

from streetlamp.models import RequestType
from streetlamp.settings_service import get_setting

REQUEST_TYPE_LABEL = {
    RequestType.outage.value: "불점등",
    RequestType.globe_broken.value: "글로브 파손",
    RequestType.fall_risk.value: "전도 위험",
    RequestType.low_brightness.value: "밝기 불량",
    RequestType.other.value: "기타",
}

SOLAPI_SEND_URL = "https://api.solapi.com/messages/v4/send-many/detail"


class SolapiNotConfiguredError(Exception):
    """SOLAPI_API_KEY / SOLAPI_API_SECRET / SOLAPI_SENDER 미설정."""


class SolapiApiError(Exception):
    """Solapi HTTP API 오류."""


def _sms_log(msg: str) -> None:
    print(msg, flush=True)


def normalize_phone_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


def parse_phone_list(raw: str) -> list[str]:
    phones: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").replace(";", ",").split(","):
        d = normalize_phone_digits(part.strip())
        if len(d) >= 10 and d not in seen:
            seen.add(d)
            phones.append(d)
    return phones


def _solapi_authorization(api_key: str, api_secret: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    salt = secrets.token_hex(8)
    signature = hmac.new(
        api_secret.encode("utf-8"),
        (date + salt).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"HMAC-SHA256 apiKey={api_key}, date={date}, salt={salt}, signature={signature}"


def build_new_request_sms_text(
    *,
    req_id: int,
    lamp_code: str,
    name: str,
    phone: str,
    request_type: RequestType,
    content: str,
) -> str:
    label = REQUEST_TYPE_LABEL.get(request_type.value, request_type.value)
    lines = [
        f"[가로등정비] 신규접수 #{req_id}",
        f"No.{lamp_code} {name.strip()}",
        f"유형:{label}",
        f"연락:{phone.strip()}",
    ]
    body = (content or "").strip()
    if body:
        snippet = body if len(body) <= 60 else body[:60] + "…"
        lines.append(f"내용:{snippet}")
    return "\n".join(lines)


def _send_sms_sync(to_list: list[str], text: str) -> None:
    api_key = os.environ.get("SOLAPI_API_KEY", "").strip()
    api_secret = os.environ.get("SOLAPI_API_SECRET", "").strip()
    sender = normalize_phone_digits(os.environ.get("SOLAPI_SENDER", "").strip())

    if not api_key or not api_secret:
        raise SolapiNotConfiguredError(
            "SOLAPI_API_KEY / SOLAPI_API_SECRET 가 Render Environment 에 없습니다."
        )
    if not sender:
        raise SolapiNotConfiguredError(
            "SOLAPI_SENDER(발신번호, 하이픈 없이) 가 Render Environment 에 없습니다."
        )
    if not to_list:
        raise ValueError("알림 받을 전화번호가 없습니다.")

    messages = [{"to": to, "from": sender, "text": text} for to in to_list]
    headers = {
        "Authorization": _solapi_authorization(api_key, api_secret),
        "Content-Type": "application/json",
    }
    _sms_log(f"[solapi] POST send-many to={to_list!r} from={sender!r}")
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(SOLAPI_SEND_URL, headers=headers, json={"messages": messages})
    except httpx.RequestError as e:
        raise SolapiApiError(f"Solapi 연결 실패: {e}") from e
    if r.status_code >= 400:
        raise SolapiApiError(f"HTTP {r.status_code}: {r.text[:800]}")
    _sms_log(f"[solapi] ok status={r.status_code}")


async def send_new_request_sms_alert(
    session,
    *,
    req_id: int,
    lamp_id: int,
    lamp_code: str | None = None,
    name: str,
    phone: str,
    request_type: RequestType,
    content: str,
) -> None:
    """접수 저장 후 호출. 실패해도 예외를 밖으로 던지지 않음."""
    try:
        enabled = (await get_setting(session, "alert_sms_enabled", "1")).strip().lower()
        if enabled not in ("1", "true", "on", "yes"):
            _sms_log("[solapi] skip: alert_sms_enabled off")
            return

        phones_raw = await get_setting(session, "alert_sms_phones", "")
        to_list = parse_phone_list(phones_raw)
        if not to_list:
            _sms_log("[solapi] skip: alert_sms_phones empty")
            return

        text = build_new_request_sms_text(
            req_id=req_id,
            lamp_code=lamp_code or str(lamp_id),
            name=name,
            phone=phone,
            request_type=request_type,
            content=content,
        )
        await asyncio.to_thread(_send_sms_sync, to_list, text)
    except Exception as e:
        _sms_log(f"[solapi] new-request alert fail: {type(e).__name__}: {e}")


async def run_test_sms_pipeline(session) -> str:
    """관리자 설정 화면 테스트 발송."""
    phones_raw = await get_setting(session, "alert_sms_phones", "")
    to_list = parse_phone_list(phones_raw)
    if not to_list:
        return "담당자 전화번호가 비어 있습니다. 설정에서 번호를 입력·저장한 뒤 다시 시도하세요."

    text = (
        "[가로등정비] SMS 테스트\n"
        "신규 접수 알림이 이 번호로 발송됩니다."
    )
    try:
        await asyncio.to_thread(_send_sms_sync, to_list, text)
        return (
            f"SMS 테스트 발송 완료 — 수신: {', '.join(to_list)}. "
            "Solapi 콘솔·휴대폰 문자함을 확인하세요."
        )
    except SolapiNotConfiguredError as e:
        return (
            f"{e}\n\n"
            "Render → Environment 에 아래 3개를 추가하세요.\n"
            "SOLAPI_API_KEY= (console.solapi.com → API Key)\n"
            "SOLAPI_API_SECRET=\n"
            "SOLAPI_SENDER=01071704563 (Solapi에 등록한 발신번호, 하이픈 없이)"
        )
    except SolapiApiError as e:
        return f"Solapi 발송 실패: {e}"
    except Exception as e:
        return f"SMS 테스트 중 오류 ({type(e).__name__}): {e}"
