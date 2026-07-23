"""법적근거 공통 — 키워드·조문 파싱·점수 (인덱스/웹검색 공용)"""

from __future__ import annotations

import re

LAW_BASE = "https://www.law.go.kr/법령"
RULE_ID = "산업안전보건기준에관한규칙"
RULE_NAME = "산업안전보건기준에 관한 규칙"
FIRE_ID = "소방시설설치및관리에관한법률"
FIRE_NAME = "소방시설 설치 및 관리에 관한 법률"
OSH_ACT_ID = "산업안전보건법"
OSH_ACT_NAME = "산업안전보건법"

ARTICLE_RE = re.compile(r"제(\d+)조\s*\(([^)]+)\)")

HAZARD_KWS = (
    "감전", "충전", "전기", "전압", "추락", "낙하", "실족", "미끄", "넘어짐", "전도",
    "소음", "청각", "화상", "고온", "화재", "폭발", "끼임", "협착", "밀폐", "질식",
    "접지", "loto", "운전정지", "중량물", "근골격", "분진", "유해", "화학", "소방",
    "감지", "소화", "크레인", "지게차", "보호구", "정리", "통로", "오조작", "누전",
)

SKIP_HREF = re.compile(r"lsInfoP|lsLink|lsBdyPrint|lsSideInfo|admRul|wikipedia", re.I)


def contains_keyword(blob: str, kw: str) -> bool:
    if kw == "전기":
        return bool(
            re.search(r"(?<!발)전기", blob)
            or any(x in blob for x in ("감전", "전압", "충전", "분전", "판넬", "접지", "ups"))
        )
    return kw in blob


def extract_keywords(*texts: str) -> list[str]:
    blob = " ".join(t for t in texts if t).lower()
    found = [kw for kw in HAZARD_KWS if contains_keyword(blob, kw)]
    for part in re.split(r"[,，·/]", blob):
        w = part.strip()
        if 2 <= len(w) <= 8 and w not in found:
            found.append(w)
    return found[:6]


def article_url(law_id: str, num: str, slug: str = "") -> str:
    num = (num or "").strip()
    if re.match(r"^\d+조의\d+$", num):
        segment = f"제{num}"
    elif re.match(r"^\d+$", num):
        segment = f"제{num}조"
    elif num.startswith("제"):
        segment = num
    else:
        segment = f"제{num}조"
    if slug:
        slug = re.sub(r"\s+", "", slug)[:40]
        return f"{LAW_BASE}/{law_id}/{segment}({slug})"
    return f"{LAW_BASE}/{law_id}/{segment}"


def format_law(law_name: str, num: str, title: str) -> str:
    num = (num or "").strip()
    if re.match(r"^\d+조의\d+$", num):
        label = f"제{num}"
    elif re.match(r"^\d+$", num):
        label = f"제{num}조"
    elif num.startswith("제"):
        label = num
    else:
        label = f"제{num}조"
    return f"{law_name} {label} ({title.strip()})"


def score_article(title: str, keywords: list[str], hazard: str) -> int:
    t = title.lower()
    h = hazard.lower()
    score = 0
    for kw in keywords:
        if len(kw) > 10:
            continue
        if kw in t:
            score += 12
        if kw in h and kw in t:
            score += 8
    pairs = (
        (("감전", "전기", "충전"), ("감전", "전기", "누전", "전기작업", "충전")),
        (("추락", "낙하", "실족"), ("추락", "낙하", "안전난간", "개구부", "사다리")),
        (("소음", "청각"), ("소음", "청각")),
        (("미끄", "넘어짐", "전도"), ("미끄", "통로", "정리", "정돈")),
        (("화상", "고온"), ("화상", "고온", "이상온도")),
        (("끼임", "협착"), ("끼임", "협착", "방호")),
        (("밀폐", "질식"), ("밀폐", "질식")),
        (("접지",), ("접지",)),
        (("소방", "감지", "소화"), ("소방", "소화", "감지", "유지")),
    )
    for hazard_kws, title_kws in pairs:
        if any(k in h or k in keywords for k in hazard_kws):
            if any(k in t for k in title_kws):
                score += 18
    return score


def confidence(law: str, keywords: list[str], hazard: str) -> int:
    m = ARTICLE_RE.search(law)
    if not m:
        return 0
    return score_article(m.group(2), keywords, hazard)
