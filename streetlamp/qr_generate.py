# qr_generate.py
# PUBLIC_BASE_URL의 /lamp/ 주소 + CSV 기준 QR PNG 생성
import csv
import os
import re
from pathlib import Path
from urllib.parse import quote

import qrcode

PUBLIC_BASE_URL = (
    os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    or os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    or "http://localhost:8000"
)
BASE_URL = f"{PUBLIC_BASE_URL}/lamp/"
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "streetlamp" / "lamp_codes.csv"
OUTPUT_DIR = "qr_codes"


def _safe_filename(code: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', "_", code)
    return f"lamp_{safe}.png"


def load_codes() -> list[str]:
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
    url = f"{BASE_URL}{quote(code, safe='')}"
    img = qrcode.make(url)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = os.path.join(OUTPUT_DIR, _safe_filename(code))
    img.save(filename)
    return url


if __name__ == "__main__":
    codes = load_codes()
    print(f"QR 생성 시작: {len(codes)}개 → {OUTPUT_DIR}/")
    for i, code in enumerate(codes, 1):
        url = generate_qr_for_code(code)
        if i <= 5 or i == len(codes):
            print(f"  [{i}/{len(codes)}] {code} → {url}")
        elif i == 6:
            print("  ...")
    print(f"\n완료. {len(codes)}개 PNG → {OUTPUT_DIR}/")
