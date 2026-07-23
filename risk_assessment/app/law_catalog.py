"""현행 law.go.kr 조문명·URL — 표시문구와 링크 내용 일치"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from app.law_common import RULE_ID, RULE_NAME, format_law, article_url, LAW_BASE
from app.runtime_paths import DATA_DIR

CATALOG_PATH = DATA_DIR / "law_article_catalog.json"

# 위험성평가 매칭용 — law_key → 현행 조문번호 (law.go.kr 검증)
TOPIC_ARTICLES: dict[str, str] = {
    "rule_default": "4",
    "rule_3": "3",
    "rule_4": "4",
    "rule_4_2": "4조의2",
    "rule_5": "5",
    "rule_13": "13",
    "rule_17": "301",
    "rule_19": "318",
    "rule_20": "20",
    "rule_22": "22",
    "rule_24": "24",
    "rule_32": "32",
    "rule_36": "36",
    "rule_38": "93",
    "rule_41": "42",
    "rule_42": "43",
    "rule_44": "32",
    "rule_63": "24",
    "rule_64": "241",
    "rule_72": "72",
    "rule_76": "171",
    "rule_86": "663",
    "rule_86_ride": "86",
    "rule_92": "92",
    "rule_93": "93",
    "rule_94": "163",
    "rule_97": "560",
    "rule_98": "98",
    "rule_99": "99",
    "rule_103": "103",
    "rule_170": "72",
    "rule_171": "171",
    "rule_179": "179",
    "rule_183": "183",
    "rule_236": "236",
    "rule_301": "301",
    "rule_302": "302",
    "rule_304": "304",
    "rule_321": "321",
    "rule_410": "449",
    "rule_436": "619",
    "rule_449": "449",
    "rule_454": "449",
    "rule_514": "514",
    "rule_516": "516",
    "rule_519": "519",
    "rule_560": "560",
    "rule_619": "619",
    "rule_628": "628",
    "rule_661": "661",
    "rule_663": "663",
    "osh_act_36": "36",
    "osh_act_41": "41",
    "serious_4": "4",
    "fire_fac_10": "10",
}

ARTICLE_NO_RE = re.compile(r"제(\d+)조(?:의(\d+))?")

# 구법 조문번호 → 현행 (대개정 2017~ 이후) — 표시·URL 불일치 보정
_LEGACY_DEFAULT: dict[str, str] = {
    "17": "301",
    "38": "93",
    "41": "42",
    "44": "32",
    "97": "560",
    "170": "72",
    "321": "302",
    "436": "619",
    "454": "449",
}

_LEGACY_KEYWORD_REMAP: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("86", "663", ("중량", "운반", "근골", "하역", "인양", "25kg", "20kg", "과중", "취급")),
    ("38", "42", ("추락", "낙하", "안전난간", "고소", "개구부", "비계", "안전대")),
)

# law.go.kr 동기화 실패 시 사용 — 현행 조문명 (검증됨)
KNOWN_ARTICLE_TITLES: dict[str, str] = {
    "3": "전도의 방지",
    "4": "사업주의 의무",
    "5": "작업장의 정리·정돈 및 통로",
    "13": "작업장의 청결",
    "20": "작업장의 청결",
    "22": "통로의 설치",
    "24": "통로의 설치",
    "32": "보호구의 지급 등",
    "36": "작업장의 청결",
    "42": "추락의 방지",
    "43": "추락의 방지",
    "72": "분진",
    "76": "건설기계 등의 방호장치",
    "86": "탑승의 제한",
    "92": "정비 등의 작업 시의 운전정지 등",
    "93": "방호장치",
    "94": "달기·운반기계",
    "98": "제한속도의 지정 등",
    "99": "운전위치 이탈 시의 조치",
    "103": "방호장치",
    "163": "달기·운반기계",
    "171": "건설기계 등의 방호장치",
    "179": "전조등 등의 설치",
    "183": "좌석 안전띠의 착용 등",
    "236": "밀폐공간 작업 프로그램의 수립·시행",
    "241": "화재·폭발 위험 작업",
    "301": "전기 기계·기구 등의 충전부 방호",
    "302": "전기 기계·기구의 접지",
    "304": "누전차단기에 의한 감전방지",
    "318": "전기작업자의 제한",
    "321": "충전전로에서의 전기작업",
    "410": "유해·위험물질의 분류 등",
    "449": "유해성 등의 주지",
    "514": "소음수준의 주지 등",
    "516": "청력보호구의 지급 등",
    "519": "유해성 등의 주지",
    "560": "이상온도",
    "619": "밀폐공간 작업 프로그램의 수립·시행",
    "628": "이산화탄소를 사용하는 소화기에 대한 조치",
    "661": "유해성 등의 주지",
    "663": "중량물의 제한",
}


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    if not CATALOG_PATH.exists():
        return {}
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        return data.get("articles", {})
    except (json.JSONDecodeError, OSError):
        return {}


def catalog_ready() -> bool:
    return bool(_catalog())


def article_title(no: str) -> str | None:
    return _catalog().get(no)


def parse_article_no(text: str) -> str | None:
    if not text:
        return None
    m = ARTICLE_NO_RE.search(text.replace(" ", ""))
    if not m:
        return None
    num, sub = m.group(1), m.group(2)
    return f"{num}조의{sub}" if sub else num


def resolve_article_no(no: str, law: str = "", url: str = "") -> str:
    """구법 조문번호를 현행 번호로 보정"""
    if not no or "조의" in no:
        return no
    blob = f"{law} {url}".lower()
    for old, new, kws in _LEGACY_KEYWORD_REMAP:
        if no == old and any(k.lower() in blob for k in kws):
            return new
    return _LEGACY_DEFAULT.get(no, no)


def law_ref(article_no: str, *, law_name: str = RULE_NAME, law_id: str = RULE_ID) -> tuple[str, str]:
    """조문번호 → (표시문구, law.go.kr URL) — catalog 제목 우선"""
    title = article_title(article_no)
    if not title:
        title = _fallback_title(article_no)
    slug = re.sub(r"\s+", "", title)[:40] if title else ""
    url = article_url(law_id, article_no, slug)
    display = format_law(law_name, article_no, title or "관련 조항")
    return display, url


def law_ref_by_key(law_key: str) -> tuple[str, str]:
    no = TOPIC_ARTICLES.get(law_key)
    if not no:
        return law_ref(TOPIC_ARTICLES["rule_default"])
    if law_key.startswith("osh_act") or law_key == "serious_4":
        from app.law_common import OSH_ACT_ID, OSH_ACT_NAME, FIRE_ID, FIRE_NAME

        if law_key == "osh_act_36":
            return law_ref("36", law_name=OSH_ACT_NAME, law_id=OSH_ACT_ID)
        if law_key == "osh_act_41":
            return law_ref("41", law_name=OSH_ACT_NAME, law_id=OSH_ACT_ID)
        if law_key == "serious_4":
            return (
                "중대재해처벌법 제4조 (안전·보건 확보의무)",
                "https://www.law.go.kr/법령/중대재해처벌법/제4조",
            )
    if law_key == "fire_fac_10":
        return (
            "소방시설 설치 및 관리에 관한 법률 제10조 (소방시설의 유지·관리)",
            "https://www.law.go.kr/법령/소방시설설치및관리에관한법률/제10조",
        )
    return law_ref(no)


def normalize_law(law: str, url: str = "") -> tuple[str, str]:
    """표시·URL을 현행 조문명으로 보정"""
    no = parse_article_no(law) or parse_article_no(url or "")
    if not no:
        return law, url

    base_no = no.split("조의")[0] if "조의" in no else no
    remapped = resolve_article_no(base_no, law, url)
    if "조의" in no:
        sub = no.split("조의", 1)[1]
        no = f"{remapped}조의{sub}" if remapped != base_no else no
    else:
        no = remapped

    if "산업안전보건법" in law and "산안" not in law:
        from app.law_common import OSH_ACT_ID, OSH_ACT_NAME
        return law_ref(no, law_name=OSH_ACT_NAME, law_id=OSH_ACT_ID)
    if "중대재해" in law:
        return (
            f"중대재해처벌법 제{no}조 ({article_title(no) or '관련 조항'})",
            f"https://www.law.go.kr/법령/중대재해처벌법/제{no}조",
        )
    if "소방시설" in law:
        return (
            f"소방시설 설치 및 관리에 관한 법률 제{no}조 ({article_title(no) or '소방시설의 유지·관리'})",
            f"https://www.law.go.kr/법령/소방시설설치및관리에관한법률/제{no}조",
        )
    return law_ref(no)


def build_laws_dict() -> dict[str, tuple[str, str]]:
    """law_lookup.LAWS 용 — topic key → (표시, url)"""
    out: dict[str, tuple[str, str]] = {}
    for key in TOPIC_ARTICLES:
        if key in ("osh_act_36", "osh_act_41", "serious_4", "fire_fac_10"):
            out[key] = law_ref_by_key(key)
        else:
            out[key] = law_ref_by_key(key)
    return out


def _fallback_title(no: str) -> str:
    return KNOWN_ARTICLE_TITLES.get(no, "관련 조항")


def export_catalog() -> dict:
    """sync_law_catalog — 내장 조문명 JSON 생성"""
    from datetime import datetime

    articles = dict(KNOWN_ARTICLE_TITLES)
    cat = _catalog()
    if cat:
        articles.update(cat)
    return {
        "version": 1,
        "law_id": RULE_ID,
        "law_name": RULE_NAME,
        "source_url": f"{LAW_BASE}/{RULE_ID}",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "article_count": len(articles),
        "articles": dict(sorted(articles.items(), key=lambda x: int(re.match(r"\d+", x[0]).group()))),
    }
