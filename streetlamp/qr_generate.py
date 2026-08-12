# qr_generate.py — 가로등 QR PNG / ZIP (PUBLIC_BASE_URL 기준)
from __future__ import annotations

import csv
import hashlib
import os
import re
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import qrcode

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "streetlamp" / "lamp_codes.csv"
OUTPUT_DIR = "qr_codes"
CACHE_DIR = Path(os.environ.get("STREETLAMP_QR_CACHE_DIR", "") or tempfile.gettempdir()) / "smart_fms_streetlamp_qr"


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
    """작게·빠르게 생성 (대량 ZIP용)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=3,
        border=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _unique_name(code: str, used: set[str]) -> str:
    fname = safe_qr_filename(code)
    base, ext = fname.rsplit(".", 1) if "." in fname else (fname, "png")
    candidate = fname
    n = 2
    while candidate.lower() in used:
        candidate = f"{base}_{n}.{ext}"
        n += 1
    used.add(candidate.lower())
    return candidate


def build_qr_zip_to_path(codes: list[str], out_path: Path, request=None) -> int:
    """가로등 코드 → ZIP 파일. 반환: 포함된 PNG 개수."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    used: set[str] = set()
    count = 0
    # STORED: 압축보다 생성 속도·CPU 우선 (PNG는 이미 압축됨)
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for code in codes:
            code = (code or "").strip()
            if not code:
                continue
            name = _unique_name(code, used)
            zf.writestr(name, qr_png_bytes(lamp_qr_url(code, request)))
            count += 1
    if count <= 0:
        tmp_path.unlink(missing_ok=True)
        raise ValueError("QR로 만들 가로등 코드가 없습니다.")
    os.replace(tmp_path, out_path)
    return count


def build_qr_zip_bytes(codes: list[str], request=None) -> bytes:
    buf = BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for code in codes:
            code = (code or "").strip()
            if not code:
                continue
            name = _unique_name(code, used)
            zf.writestr(name, qr_png_bytes(lamp_qr_url(code, request)))
    if not used:
        raise ValueError("QR로 만들 가로등 코드가 없습니다.")
    return buf.getvalue()


def cache_key(codes: list[str], request=None, prefix: str = "") -> str:
    base = public_base_url(request)
    digest = hashlib.sha1()
    digest.update(base.encode("utf-8"))
    digest.update(b"|")
    digest.update(prefix.encode("utf-8"))
    digest.update(b"|")
    digest.update(str(len(codes)).encode("utf-8"))
    digest.update(b"|")
    if codes:
        digest.update(codes[0].encode("utf-8"))
        digest.update(codes[-1].encode("utf-8"))
        # 중간 샘플로 변경 감지
        mid = codes[len(codes) // 2]
        digest.update(mid.encode("utf-8"))
    return digest.hexdigest()[:20]


def get_or_build_qr_zip(
    codes: list[str],
    request=None,
    *,
    prefix: str = "",
) -> Path:
    """디스크 캐시된 ZIP 경로 반환 (없으면 생성)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = cache_key(codes, request, prefix=prefix)
    label = "all" if not prefix else re.sub(r"[^\w가-힣.\-]+", "_", prefix)[:40]
    out = CACHE_DIR / f"streetlamp_QR_{label}_{key}.zip"
    if out.is_file() and out.stat().st_size > 0:
        return out
    build_qr_zip_to_path(codes, out, request)
    return out


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
