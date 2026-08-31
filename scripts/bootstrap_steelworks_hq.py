# -*- coding: utf-8 -*-
"""제철소본부 일지 스키마 생성."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ELEC_TIMES = ["06:00", "10:00", "14:00", "18:00", "22:00"]
HV_TIMES = ["10:00", "14:00"]


def _meter_group(name: str, cols: list[tuple[str, str, str, str, int | None]]) -> dict:
    """cols: (col, metric, unit, range, multiplier|None for cumulative)"""
    columns = []
    meter = None
    for col, metric, unit, rng, mult in cols:
        is_cum = mult is not None and metric == "적산량"
        entry = {"col": col, "metric": metric, "unit": unit, "range": rng}
        if is_cum:
            entry["is_cumulative"] = True
            entry["multiplier"] = mult
            meter = {"id": f"m_{col}", "name": name, "reading_col": col, "multiplier": mult}
        columns.append(entry)
    g = {"name": name, "columns": columns}
    if meter:
        g["meter"] = meter
    return g


def build_schema() -> dict:
    incoming = {
        "id": "incoming",
        "title": "수전",
        "times": ELEC_TIMES,
        "groups": [
            _meter_group(
                "NO.1 Incomming",
                [
                    ("C", "전압", "V", "6171~6765", None),
                    ("E", "전류", "A", "0~1000", None),
                    ("F", "유효", "kW", "0~1200", None),
                    ("G", "무효", "kVar", "57~61", None),
                    ("H", "주파수", "Hz", "57~61", None),
                    ("I", "역률", "%", "0.8~1.0", None),
                    ("J", "적산량", "Mwh", "", 1000),
                ],
            ),
            _meter_group(
                "NO.2 Incomming",
                [
                    ("L", "전압", "V", "6171~6765", None),
                    ("N", "전류", "A", "0~1000", None),
                    ("O", "유효", "kW", "0~1200", None),
                    ("P", "무효", "kVar", "57~61", None),
                    ("Q", "주파수", "Hz", "57~61", None),
                    ("R", "역률", "%", "0.8~1.0", None),
                    ("S", "적산량", "Mwh", "", 1000),
                ],
            ),
        ],
    }

    def turbo_block(bid: str, title: str, groups_spec: list) -> dict:
        groups = []
        meters = []
        for name, cols in groups_spec:
            g = _meter_group(name, cols)
            groups.append(g)
            if g.get("meter"):
                meters.append(g["meter"])
        return {"id": bid, "title": title, "times": ELEC_TIMES, "groups": groups, "meters": meters}

    turbo = turbo_block(
        "turbo",
        "터보·TR",
        [
            ("NO.1 Turbo", [("C", "유효", "kW", "0~360", None), ("D", "전류", "A", "0~30", None), ("E", "적산량", "", "×360", 360)]),
            ("NO.2 Turbo", [("F", "유효", "kW", "0~360", None), ("G", "전류", "A", "0~30", None), ("H", "적산량", "", "×360", 360)]),
            ("NO.1 300kVA TR", [("I", "유효", "kW", "0~480", None), ("J", "전류", "A", "0~40", None), ("K", "적산량", "", "×480", 480)]),
            ("백운대", [("L", "유효", "kW", "0~1200", None), ("M", "전류", "A", "0~100", None), ("N", "적산량", "", "×900", 900)]),
            ("NO.3 500kVA TR", [("O", "유효", "kW", "0~900", None), ("P", "전류", "A", "0~75", None), ("Q", "적산량", "", "×900", 900)]),
            ("NO.6 300kVA TR", [("R", "유효", "kW", "0~1200", None), ("S", "전류", "A", "0~100", None), ("T", "적산량", "", "×1200", 1200)]),
        ],
    )

    room = turbo_block(
        "room",
        "전기실·TR",
        [
            ("3동 전기실", [("C", "유효", "kW", "0~1800", None), ("D", "전류", "A", "0~150", None), ("E", "적산량", "", "×1800", 1800)]),
            ("전용구장", [("F", "유효", "kW", "0~4800", None), ("G", "전류", "A", "0~400", None), ("H", "적산량", "", "×4800", 4800)]),
            ("NO.2 300kVA TR", [("I", "유효", "kW", "0~480", None), ("J", "전류", "A", "0~40", None), ("K", "적산량", "", "×480", 480)]),
            ("Spare", [("L", "유효", "kW", "0~7200", None), ("M", "전류", "A", "0~600", None), ("N", "적산량", "", "×7200", 7200)]),
            ("NO.4 300kVA TR", [("O", "유효", "kW", "0~480", None), ("P", "전류", "A", "0~40", None), ("Q", "적산량", "", "×480", 480)]),
            ("NO.5 400kVA TR", [("R", "유효", "kW", "0~600", None), ("S", "전류", "A", "0~50", None), ("T", "적산량", "", "×600", 600)]),
        ],
    )

    hv = {
        "id": "hv",
        "title": "HV·TR온도·특이사항",
        "times": HV_TIMES,
        "groups": [
            {
                "name": "HV (상) 400kVA",
                "columns": [
                    {"col": "C", "metric": "유효", "unit": "kW", "range": "0~1800"},
                    {"col": "D", "metric": "전류", "unit": "A", "range": "0~150"},
                    {"col": "E", "metric": "TR온도", "unit": "℃", "range": ""},
                ],
            },
            {
                "name": "HV 2(하) 450kVA",
                "columns": [
                    {"col": "F", "metric": "유효", "unit": "kW", "range": "0~4800"},
                    {"col": "G", "metric": "전류", "unit": "A", "range": "0~400"},
                    {"col": "H", "metric": "TR온도", "unit": "℃", "range": ""},
                ],
            },
            {
                "name": "HV 3(상) 500kVA",
                "columns": [
                    {"col": "I", "metric": "유효", "unit": "kW", "range": "0~480"},
                    {"col": "J", "metric": "전류", "unit": "A", "range": "0~40"},
                    {"col": "K", "metric": "TR온도", "unit": "℃", "range": ""},
                ],
            },
        ],
        "tr_temps": [
            {"col": "L", "name": "NO.1", "range": "300kVA"},
            {"col": "M", "name": "NO.2", "range": "300kVA"},
            {"col": "N", "name": "NO.3", "range": "500kVA"},
            {"col": "O", "name": "NO.4", "range": "300kVA"},
            {"col": "P", "name": "NO.5", "range": "400kVA"},
            {"col": "Q", "name": "NO.6", "range": "300kVA"},
        ],
        "notes_col": "R",
    }

    facility_utility = {
        "title": "1. Utility 사용량",
        "rows": [
            {"id": "pwr1", "label": "전력 NO.1 수전", "unit": "Mwh"},
            {"id": "pwr2", "label": "전력 NO.2 수전", "unit": "Mwh"},
            {"id": "heat", "label": "중온수 열량", "unit": "Mwh"},
            {"id": "flow", "label": "중온수 유량", "unit": "㎥"},
            {"id": "water1", "label": "급수 1동 고가수조", "unit": "㎥"},
            {"id": "water_main", "label": "급수 Main", "unit": "㎥"},
        ],
    }

    return {
        "building_name": "제철소본부",
        "title": "제철소본부 일지",
        "electrical": {
            "sheet_title": "제철소본부 전기설비 점검일지",
            "blocks": [incoming, turbo, room, hv],
        },
        "facility": {
            "sheet_title": "제철소본부 운영일보",
            "utility": facility_utility,
            "heating": {
                "title": "2. Utility 점검 — 난방(동절기)",
                "rows": [
                    {"id": "b1", "label": "1동"},
                    {"id": "b3", "label": "3동"},
                ],
            },
            "fire": {"title": "소화전", "rows": [{"id": "b1", "label": "1동"}, {"id": "b3", "label": "3동"}]},
            "ahu": {
                "title": "공조온도(S/R)",
                "times": ["09:50", "13:30"],
                "units": ["AHU-1", "AHU-2", "AHU-3", "AHU-4", "AHU-5", "AHU-6"],
            },
            "outdoor": {
                "title": "외기온도",
                "times": ["09:50", "13:30"],
                "groups": [
                    {"id": "g1", "label": "1차", "fields": ["제철소장실", "행정부소장실", "안전환경부소장실", "환경기획실장실"]},
                    {"id": "g2", "label": "2차", "fields": ["회장실", "본부장실", "대응접실", "영상회의실"]},
                ],
            },
            "chiller": {
                "title": "3. 냉동기(하절기)",
                "units": [
                    {"id": "c260_1", "label": "260RT #1", "times": 2},
                    {"id": "c260_2", "label": "260RT #2", "times": 2},
                    {"id": "c80", "label": "80RT", "times": ["09:50", "13:30"]},
                    {"id": "c300", "label": "300RT", "times": ["09:00", "13:10"]},
                ],
                "fields_260": ["전압kV", "전류A", "유압", "Oil℃", "Vane%", "응축압력", "응축입구", "응축출구", "냉매%", "증발압력", "증발입구", "증발출구"],
                "fields_300": ["전류A", "IGV%", "D.P%", "입력KW", "압력비", "응축압력", "응축입구", "응축출구", "냉매%", "증발압력", "증발입구", "증발출구"],
            },
        },
    }


if __name__ == "__main__":
    schema = build_schema()
    out = ROOT / "resources" / "steelworks_hq_schema.json"
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", out)
