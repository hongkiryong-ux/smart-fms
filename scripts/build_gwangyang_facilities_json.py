# -*- coding: utf-8 -*-
"""광양 주택단지 공공시설물 현황 xls → UTF-8 JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path

import xlrd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "gwangyang_facilities_250121.xls"
OUT = ROOT / "resources" / "gwangyang_public_facilities.json"


def cell(sh, r: int, c: int):
    if r >= sh.nrows or c >= sh.ncols:
        return ""
    v = sh.cell_value(r, c)
    if v == "" or v is None:
        return ""
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return int(round(v))
        return v
    return str(v).strip()


def fmt_num(v, decimals=None):
    if v == "" or v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if decimals is not None:
        f = round(f, decimals)
        if decimals == 0:
            return f"{int(f):,}"
        return f"{f:,.{decimals}f}".rstrip("0").rstrip(".")
    if abs(f - round(f)) < 1e-6:
        return f"{int(round(f)):,}"
    return f"{f:,.2f}".rstrip("0").rstrip(".")


def fmt_date(v):
    if v == "" or v is None:
        return ""
    if isinstance(v, (int, float)):
        if isinstance(v, float) and abs(v - round(v)) > 1e-9:
            return str(v).rstrip("0").rstrip(".")
        return str(int(v)) if float(v) == int(float(v)) else str(v)
    return str(v).strip()


def tidy_spaced_label(s: str) -> str:
    """엑셀 '기 타', '소 계', '합 계(...)' 같은 자간 띄어쓰기를 복원."""
    s = str(s or "").strip()
    if not s:
        return ""
    m = re.match(r"^((?:[^\s()]\s)+[^\s()])(\s*\(.*\))?$", s)
    if m:
        return m.group(1).replace(" ", "") + (m.group(2) or "")
    return s


def row_side(sh, r: int, base: int):
    category = tidy_spaced_label(cell(sh, r, base + 0))
    name = tidy_spaced_label(cell(sh, r, base + 1))
    date = fmt_date(cell(sh, r, base + 2))
    cost = cell(sh, r, base + 3)
    area = cell(sh, r, base + 4)
    pyeong = cell(sh, r, base + 5)
    note = cell(sh, r, base + 6)
    if not any([category, name, date, cost != "", area != "", pyeong != "", note]):
        return None
    kind = "data"
    nm = str(name).replace(" ", "")
    cat = str(category).replace(" ", "")
    if nm == "소계":
        kind = "subtotal"
    elif "합계" in cat or "합계" in nm:
        kind = "total"
        if category and not name:
            name = category
            category = ""
    return {
        "category": category or "",
        "name": name or "",
        "completed": date,
        "cost": fmt_num(cost, 0) if cost != "" else "",
        "area": fmt_num(area, 2) if area != "" else "",
        "pyeong": fmt_num(pyeong, 0) if pyeong != "" else "",
        "note": note or "",
        "kind": kind,
    }


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"missing source: {SRC}")
    wb = xlrd.open_workbook(str(SRC), formatting_info=True)
    sh = wb.sheet_by_index(0)

    left_rows: list = []
    right_rows: list = []
    for r in range(6, sh.nrows):
        left = row_side(sh, r, 0)
        right = row_side(sh, r, 8)
        # skip repeated header row on right panel
        if right and right.get("name") == "건물명":
            right = None
        if left and left.get("name") == "건물명":
            left = None
        left_rows.append(left)
        right_rows.append(right)

    while left_rows and right_rows and left_rows[-1] is None and right_rows[-1] is None:
        left_rows.pop()
        right_rows.pop()

    title = re.sub(r"\s+", " ", str(cell(sh, 1, 0))).strip()
    as_of = str(cell(sh, 3, 14)).replace("(", "").replace(")", "").replace("'", "").strip()

    payload = {
        "title": title or "광양 주택지역 공공시설물 현황",
        "as_of": as_of or "2025.01 기준",
        "source": "광양 주택단지 공공시설물 관리 현황250121.xls",
        "headers": ["구분", "건물명", "준공일자", "취득액(천원)", "연면적(㎡)", "坪", "비고"],
        "left": left_rows,
        "right": right_rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(left_rows)} pairs)")
    print("title:", payload["title"])
    print("as_of:", payload["as_of"])
    for i in (0, 5, 6, 37, 61, 72):
        L, R = left_rows[i], right_rows[i]
        print(i, (L or {}).get("category") or (L or {}).get("name"), "|", (R or {}).get("category") or (R or {}).get("name"))


if __name__ == "__main__":
    main()
