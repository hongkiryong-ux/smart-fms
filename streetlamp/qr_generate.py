# qr_generate.py — 가로등 QR PNG / ZIP (PUBLIC_BASE_URL 기준)
from __future__ import annotations

import csv
import os
import re
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import qrcode

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "streetlamp" / "lamp_codes.csv"
OUTPUT_DIR = "qr_codes"


def public_base_url(request=None) -> str:
    env = (
        os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    )
    if env:
        return env
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://localhost:8000"


def lamp_qr_url(code: str, request=None) -> str:
    base = public_base_url(request)
    return f"{base}/lamp/{quote(str(code), safe='')}"


def safe_qr_filename(code: str) -> str:
    safe = re.sub(r"[^\w가-힣.\-]+", "_", (code or "").strip()) or "lamp"
    return f"{safe}.png"


def qr_png_bytes(url: str) -> bytes:
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_qr_zip_bytes(codes: list[str], request=None) -> bytes:
    """가로등 코드 목록 → PNG ZIP 바이트."""
    buf = BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for code in codes:
            code = (code or "").strip()
            if not code:
                continue
            fname = safe_qr_filename(code)
            base, ext = fname.rsplit(".", 1) if "." in fname else (fname, "png")
            candidate = fname
            n = 2
            while candidate.lower() in used:
                candidate = f"{base}_{n}.{ext}"
                n += 1
            used.add(candidate.lower())
            zf.writestr(candidate, qr_png_bytes(lamp_qr_url(code, request)))
    if not used:
        raise ValueError("QR로 만들 가로등 코드가 없습니다.")
    return buf.getvalue()


def load_codes_from_csv() -> list[str]:
    if not CSV_PATH.is_file():
        raise FileNotFoundError(
            f"{CSV_PATH} 없음. scripts/build_lamp_codes_csv.py 를 먼저 실행하세요."
        )
    codes: list[str] = []
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if code:
                codes.append(code)
    return codes


def generate_qr_for_code(code: str) -> str:
    """CLI용: 디스크에 PNG 저장."""
    url = lamp_qr_url(code)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, safe_qr_filename(code))
    with open(path, "wb") as f:
        f.write(qr_png_bytes(url))
    return url


if __name__ == "__main__":
    codes = load_codes_from_csv()
    print(f"QR 생성 시작: {len(codes)}개 → {OUTPUT_DIR}/")
    for i, code in enumerate(codes, 1):
        url = generate_qr_for_code(code)
        if i <= 5 or i == len(codes):
            print(f"  [{i}/{len(codes)}] {code} → {url}")
        elif i == 6:
            print("  ...")
    print(f"\n완료. {len(codes)}개 PNG → {OUTPUT_DIR}/")
