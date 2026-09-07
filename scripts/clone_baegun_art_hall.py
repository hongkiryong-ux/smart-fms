# -*- coding: utf-8 -*-
"""Clone human_center → baegun_art_hall scaffolding."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REPLACEMENTS = [
    ("휴먼센터 운영일보[냉,난방]", "백운아트홀 운영일보[냉,난방]"),
    ("휴먼센터", "백운아트홀"),
    ("human_center", "baegun_art_hall"),
    ("HumanCenter", "BaegunArtHall"),
    ("hcenter", "bahall"),
    ("HCENTER", "BAHALL"),
    ("is_human_center_building", "is_baegun_art_hall_building"),
    ("human_center_daily_qr_url", "baegun_art_hall_daily_qr_url"),
]


def transform(text: str) -> str:
    for a, b in REPLACEMENTS:
        text = text.replace(a, b)
    return text


def transform_frontend(text: str) -> str:
    text = transform(text)
    pairs = [
        ("hc-", "bah-"),
        ("#hc-", "#bah-"),
        (".hc-", ".bah-"),
        ("'hc-", "'bah-"),
        ('"hc-', '"bah-'),
        ("X-HCENTER", "X-BAHALL"),
        ("getElementById(\"hc-", "getElementById(\"bah-"),
        ("getElementById('hc-", "getElementById('bah-"),
    ]
    for a, b in pairs:
        text = text.replace(a, b)
    return text


def main() -> None:
    py = transform((ROOT / "human_center.py").read_text(encoding="utf-8"))
    (ROOT / "baegun_art_hall.py").write_text(py, encoding="utf-8")

    css = transform_frontend((ROOT / "static/css/human_center.css").read_text(encoding="utf-8"))
    (ROOT / "static/css/baegun_art_hall.css").write_text(css, encoding="utf-8")

    js = transform_frontend((ROOT / "static/js/human_center.js").read_text(encoding="utf-8"))
    (ROOT / "static/js/baegun_art_hall.js").write_text(js, encoding="utf-8")

    # template: only rename scaffolding strings; daily body will be rewritten
    html = transform_frontend((ROOT / "templates/human_center.html").read_text(encoding="utf-8"))
    html = html.replace("/human-center", "/baegun-art-hall")
    html = html.replace("human_center.css", "baegun_art_hall.css")
    html = html.replace("human_center.js", "baegun_art_hall.js")
    (ROOT / "templates/baegun_art_hall.html").write_text(html, encoding="utf-8")
    print("ok")


if __name__ == "__main__":
    main()
