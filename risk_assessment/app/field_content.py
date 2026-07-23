"""현장 실무형 개선전(위험)·개선후(대책) 문구 생성"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.local_engine import RiskRow

_IMPROVEMENT_TAG = re.compile(r"^\[(공학적|관리적|보호구|개인\s*보호구)\]\s*")
LAW_DEFAULT = (
    "산업안전보건기준에 관한 규칙 제4조 (사업주의 의무)",
    "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제4조",
)

# 도장·석고·가벽·현장정리 등 건축 공통 (현장 실무 문구)
CIVIL_FIELD_CORE = [
    {
        "work_class": "작업준비",
        "phase": "작업 전",
        "unit_task": "현장이동·자재반입",
        "hazard": "작업 시작 전 안전보호구 미착용 상태에서 작업에 임할 경우 안전사고 발생 우려",
        "injury": "절단, 화학물질 접촉, 부딪힘",
        "current": "보호구 지급은 되어 있으나 착용 여부 확인·관리가 미흡함",
        "improvement": "작업별 적정 보호구(보호장갑·안전화·안전모 등) 착용 확인 후 작업을 시작할 것",
        "freq_before": 4, "sev_before": 2, "freq_after": 2, "sev_after": 2,
        "law": "산업안전보건기준에 관한 규칙 제44조 (보호구의 지급 등)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제44조",
    },
    {
        "work_class": "운전작업",
        "phase": "작업 전",
        "unit_task": "자재·부재 반입·운반",
        "hazard": "자재 반입·운반 시 1인 단독 운반으로 인한 요통·근골격계 질환 발생 우려",
        "injury": "불균형 및 무리한 동작, 넘어짐",
        "current": "적정하중 미준수, 운반동선·협력체계 미확립",
        "improvement": "적정중량(남 25kg·여 20kg 이하) 준수 및 긴 부재·중량물은 2인 1조로 운반할 것",
        "freq_before": 4, "sev_before": 2, "freq_after": 2, "sev_after": 1,
        "law": "산업안전보건기준에 관한 규칙 제86조 (중량물의 취급)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제86조",
    },
    {
        "work_class": "운전작업",
        "phase": "작업 중",
        "unit_task": "비계·고소에서 본작업",
        "hazard": "비계 상부 작업 중 균형 상실에 따른 추락 재해 발생 우려",
        "injury": "추락, 떨어짐",
        "current": "안전대 미체결·이중고리 미확인, 안전난간 미설치 구간 존재",
        "improvement": "강관조립말비계 아우트리거 설치, 안전난간·발판 설치 후 안전대 2중고리 체결 확인할 것",
        "freq_before": 3, "sev_before": 4, "freq_after": 2, "sev_after": 3,
        "law": "산업안전보건기준에 관한 규칙 제38조 (기계·기구의 방호)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제38조",
    },
    {
        "work_class": "운전작업",
        "phase": "작업 중",
        "unit_task": "설치·가설 자재 취급",
        "hazard": "고소 작업 중 자재·공구 낙하로 하부 작업자 피격 우려",
        "injury": "맞음, 떨어짐",
        "current": "자재 결속·낙하방지 조치 미흡, 하부 작업자 혼재",
        "improvement": "작업 구역 출입 통제 및 자재 결속·낙하방지망·공구 고정줄 사용할 것",
        "freq_before": 3, "sev_before": 3, "freq_after": 2, "sev_after": 2,
        "law": "산업안전보건기준에 관한 규칙 제38조 (기계·기구의 방호)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제38조",
    },
    {
        "work_class": "운전작업",
        "phase": "작업 후",
        "unit_task": "현장 정리·정돈",
        "hazard": "작업 종료 후 정리정돈 미실시로 통로 적치·넘어짐 재해 발생 우려",
        "injury": "넘어짐, 부딪힘",
        "current": "공구·자재 방치, 작업통로 미확보",
        "improvement": "작업 중·종료 후 작업구역 정리정돈 실시 및 통로 확보할 것",
        "freq_before": 4, "sev_before": 2, "freq_after": 2, "sev_after": 1,
        "law": "산업안전보건기준에 관한 규칙 제5조 (작업장의 정리·정돈 및 통로)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제5조",
    },
]

WORK_AFTER_DEFAULT = {
    "work_class": "운전작업",
    "phase": "작업 후",
    "unit_task": "작업장 정리·복구",
    "hazard": "작업 종료 후 정리정돈 미실시로 통로 적치·넘어짐 재해 발생 우려",
    "injury": "넘어짐, 부딪힘",
    "current": "공구·자재 방치, 작업통로 미확보",
    "improvement": "작업 종료 후 작업구역 정리정돈 실시, 공구 수거·점검 후 작업완료할 것",
    "freq_before": 3, "sev_before": 2, "freq_after": 2, "sev_after": 1,
    "law": "산업안전보건기준에 관한 규칙 제5조 (작업장의 정리·정돈 및 통로)",
    "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제5조",
}

CIVIL_EXTRA = {
    "도장": [
        {
            "work_class": "운전작업", "phase": "작업 중", "unit_task": "도장·칠 작업",
            "hazard": "도료·유기용제 흡입 및 스프레이 비산으로 호흡기·피부 질환 및 화재 발생 우려",
            "injury": "화학물질 접촉, 화재",
            "current": "환기 불량, MSDS 미비치, 화기·불꽃 작업 혼재",
            "improvement": "국소배기·환기 확보, MSDS 비치, 화기 엄금 및 유기용제용 보호구 착용할 것",
            "freq_before": 3, "sev_before": 3, "freq_after": 2, "sev_after": 2,
            "law": "산업안전보건기준에 관한 규칙 제64조 (화재·폭발 위험 작업)",
            "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제64조",
        },
    ],
    "석고": [
        {
            "work_class": "운전작업", "phase": "작업 중", "unit_task": "석고·미장 작업",
            "hazard": "석고분진 흡입 및 바닥 슬립·미끄럼으로 업무상질환·넘어짐 발생 우려",
            "injury": "업무상질병, 넘어짐",
            "current": "방진마스크 미착용, 바닥 슬립·분진 미정리",
            "improvement": "방진마스크(P2 이상) 착용, 바닥 슬립·분진 수시 제거 및 환기할 것",
            "freq_before": 4, "sev_before": 2, "freq_after": 2, "sev_after": 1,
            "law": "산업안전보건기준에 관한 규칙 제170조 (분진)",
            "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제170조",
        },
    ],
    "가벽": [
        {
            "work_class": "운전작업", "phase": "작업 중", "unit_task": "가벽·칸막이 설치",
            "hazard": "가벽 패널·트랙 인양·고정 작업 중 끼임 및 낙하 재해 발생 우려",
            "injury": "끼임, 맞음",
            "current": "2인 1조 미준수, 임시 지지·고정 미실시",
            "improvement": "패널 인양·고정 2인 1조 작업 및 임시 지지·클램프 고정 후 본체결할 것",
            "freq_before": 3, "sev_before": 2, "freq_after": 2, "sev_after": 1,
            "law": "산업안전보건기준에 관한 규칙 제38조 (기계·기구의 방호)",
            "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제38조",
        },
    ],
    "정리": [
        {
            "work_class": "운전작업", "phase": "작업 후", "unit_task": "현장정리·폐기물 처리",
            "hazard": "현장정리 과정 중 잔재물·공구에 의한 베임·찔림 재해 발생 우려",
            "injury": "베임, 찔림",
            "current": "작업장 혼잡, 공구 수거·분리수거 미흡",
            "improvement": "폐기물 분리수거·지정장소 적재 및 공구 수거·점검 후 작업완료할 것",
            "freq_before": 3, "sev_before": 2, "freq_after": 2, "sev_after": 1,
            "law": "산업안전보건기준에 관한 규칙 제5조 (작업장의 정리·정돈 및 통로)",
            "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제5조",
        },
    ],
}

MECH_FIELD = [
    {
        "keywords": ["펌프", "분해", "정비"],
        "work_class": "정비작업", "phase": "작업 전", "unit_task": "설비 LOTO·에너지차단",
        "hazard": "잔류압·잔류에너지 미해제 상태에서 분해·정비 착수",
        "injury": "끼임, 화학물질 누출, 맞음",
        "current": "LOTO 미실시, 압력게이지 미확인",
        "improvement": "LOTO 6단계 실시, 잔압·잔류에너지 해제 확인 후 작업허가서 발행·작업할 것",
        "freq_before": 3, "sev_before": 4, "freq_after": 2, "sev_after": 2,
        "law": "산업안전보건기준에 관한 규칙 제92조 (정비 등의 작업 시의 운전정지 등)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제92조",
    },
    {
        "keywords": ["펌프", "보일러", "열교환", "배관", "용접"],
        "work_class": "정비작업", "phase": "작업 중", "unit_task": "분해·용접·교체",
        "hazard": "고온·고압 부품 및 용접 불꽃에 의한 화상·화재 발생 우려",
        "injury": "화재, 이상온도, 화학물질 누출",
        "current": "주변 가연물 미제거, 화재감시자 미배치",
        "improvement": "주변 가연물 제거, 화재감시자 배치, 소화기 비치 및 용접면·내열장갑 착용할 것",
        "freq_before": 3, "sev_before": 3, "freq_after": 2, "sev_after": 2,
        "law": "산업안전보건기준에 관한 규칙 제64조 (화재·폭발 위험 작업)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제64조",
    },
    {
        "keywords": ["펌프", "보일러", "열교환", "배관", "용접"],
        "work_class": "정비작업", "phase": "작업 후", "unit_task": "정비 후 원상복구·정리",
        "hazard": "정비 종료 후 작업장 정리 미흡으로 통로 적치·넘어짐 재해 발생 우려",
        "injury": "넘어짐, 부딪힘",
        "current": "공구·부품 방치, 작업통로 미확보",
        "improvement": "정비 종료 후 공구·부품 수거, 작업구역 정리정돈 및 통로 확보할 것",
        "freq_before": 3, "sev_before": 2, "freq_after": 2, "sev_after": 1,
        "law": "산업안전보건기준에 관한 규칙 제5조 (작업장의 정리·정돈 및 통로)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제5조",
    },
]

ELEC_FIELD = [
    {
        "keywords": ["분전", "차단", "수,배전", "접촉기", "케이블"],
        "work_class": "정비작업", "phase": "작업 전", "unit_task": "전원차단·검전·접지",
        "hazard": "활선·잔류전압 접촉으로 감전",
        "injury": "감전",
        "current": "검전·접지 미실시, 충전부 표시 미흡",
        "improvement": "LOTO 후 검전기로 무전압 확인, 접지선 선연결, 충전부 표지·출입통제",
        "freq_before": 3, "sev_before": 4, "freq_after": 2, "sev_after": 3,
        "law": "산업안전보건기준에 관한 규칙 제17조 (전기의 위험성)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제17조",
    },
    {
        "keywords": ["조명", "고소", "열화상", "승강로"],
        "work_class": "운전작업", "phase": "작업 중", "unit_task": "고소·천장 전기작업",
        "hazard": "사다리·고소작업대에서 균형 상실·추락 후 감전 복합재해",
        "injury": "추락, 감전",
        "current": "안전대 미착용, 1인 작업",
        "improvement": "고소작업 허가, 안전대 2중고리 체결, 2인 1조, 절연공구 사용",
        "freq_before": 3, "sev_before": 4, "freq_after": 2, "sev_after": 3,
        "law": "산업안전보건기준에 관한 규칙 제38조 (기계·기구의 방호)",
        "law_url": "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제38조",
    },
]

CIVIL_KEYWORDS = [
    "도장", "석고", "가벽", "미장", "타일", "코킹", "방수", "인테리어",
    "석재", "판넬", "천정", "gutter", "외벽", "청소", "정리", "건축",
    "커튼월", "침하", "철물", "tex", "습식",
]

from app.runtime_paths import DATA_DIR

JSA_PPT_PATH = DATA_DIR / "jsa_ppt_library.json"
JSA_DC_PATH = DATA_DIR / "jsa_datacenter_library.json"
JSA_MAINT_PATH = DATA_DIR / "jsa_maintenance_library.json"
_JSA_PPT_CACHE: dict | None = None

INJURY_MAP = [
    ("감전", "감전"),
    ("화재", "화재"),
    ("추락", "추락, 떨어짐"),
    ("떨어짐", "추락, 떨어짐"),
    ("끼임", "끼임"),
    ("베임", "베임, 찔림"),
    ("절단", "절단, 베임"),
    ("화상", "화상"),
    ("질식", "산소결핍"),
    ("넘어짐", "넘어짐"),
    ("부딪힘", "부딪힘, 맞음"),
    ("낙하", "맞음, 떨어짐"),
    ("근골격", "불균형 및 무리한 동작"),
    ("요통", "불균형 및 무리한 동작"),
]


def _load_jsa_ppt_library() -> dict[str, list[dict]]:
    global _JSA_PPT_CACHE
    if _JSA_PPT_CACHE is not None:
        return _JSA_PPT_CACHE.get("jobs", {})

    merged: dict[str, list[dict]] = {}
    for path in (JSA_PPT_PATH, JSA_DC_PATH, JSA_MAINT_PATH):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, items in data.get("jobs", {}).items():
            merged.setdefault(key, []).extend(items)

    for job, items in merged.items():
        seen: set[str] = set()
        unique: list[dict] = []
        for item in items:
            sig = item["hazard"][:45]
            if sig in seen:
                continue
            seen.add(sig)
            unique.append(item)
        merged[job] = unique

    _JSA_PPT_CACHE = {"jobs": merged}
    return merged


def _norm_job(s: str) -> str:
    s = s.lower().replace("bettery", "battery").replace("밧데리", "battery")
    return re.sub(r"[\s,·/_\-]+", "", s)


def _ppt_aliases_for_job(job_name: str) -> list[str]:
    """work_types preset aliases → PPT JSA 작업명 후보"""
    try:
        from app.work_type_lookup import WorkTypeLookup

        preset = WorkTypeLookup().get_by_name(job_name.strip())
        if preset:
            return list(preset.get("aliases", []))
    except Exception:
        pass
    return []


def match_jsa_ppt_job(job_name: str, context: str = "") -> str | None:
    """PPT JSA 라이브러리에서 작업명 매칭"""
    library = _load_jsa_ppt_library()
    if not library:
        return None
    q = _norm_job(job_name)
    ctx = _norm_job(context)
    if job_name in library:
        return job_name

    # preset 별칭 우선 (Datacenter PPT 작업명)
    for alias in _ppt_aliases_for_job(job_name):
        if alias in library:
            return alias
        a = _norm_job(alias)
        for key in library:
            if a == _norm_job(key):
                return key

    _GENERIC = {"운전", "점검", "작업", "설비", "정비", "및", "및점검"}
    best_key = None
    best_score = 0
    for key in library:
        k = _norm_job(key)
        if q == k:
            return key
        score = 0
        if q and (q in k or k in q):
            score = min(len(q), len(k)) + 10
        q_tokens = {t for t in re.findall(r"[\w가-힣]{2,}", job_name) if t not in _GENERIC}
        k_tokens = {t for t in re.findall(r"[\w가-힣]{2,}", key) if t not in _GENERIC}
        overlap = len(q_tokens & k_tokens)
        if overlap >= 2:
            score = max(score, overlap * 10)
        elif overlap == 1 and len(q_tokens) <= 2:
            score = max(score, overlap * 6)
        if ctx and k in ctx:
            score += 4
        if score > best_score:
            best_score = score
            best_key = key
    return best_key if best_score >= 10 else None


def _guess_injury(hazard: str) -> str:
    for kw, injury in INJURY_MAP:
        if kw in hazard:
            return injury
    return "부딪힘, 기타"


def _guess_current(hazard: str) -> str:
    if "미착용" in hazard:
        return "안전보호구 지급은 되어 있으나 착용 확인·관리 미흡"
    if "미확인" in hazard or "미실시" in hazard:
        return "사전 확인·점검 절차 미이행"
    if "미흡" in hazard or "부족" in hazard:
        return "관련 안전조치·관리적 대책 미흡"
    return "현장 안전조치·작업표준 준수 미흡"


def _default_scores(hazard: str, injury: str = "") -> tuple[int, int, int, int]:
    from app.risk_scoring import is_fatal_risk, normalize_scores

    if is_fatal_risk(hazard, injury):
        return normalize_scores(3, 4, 2, 4, hazard, injury, "", "")
    sev = 2
    if any(k in hazard for k in ("감전", "추락", "화재", "질식", "깔림", "중대")):
        sev = 3
    if any(k in hazard for k in ("사망", "폭발", "화상")):
        sev = 4
    freq = 3 if sev >= 3 else 2
    return normalize_scores(freq, sev, max(1, freq - 1), sev, hazard, injury, "", "")


def _ppt_item_to_row(item: dict) -> "RiskRow":
    from app.local_engine import RiskRow

    hazard = polish_hazard(item["hazard"])
    improvement = polish_improvement(item["improvement"])
    if "freq_before" in item:
        f_b, s_b = item["freq_before"], item["sev_before"]
        f_a, s_a = item["freq_after"], item["sev_after"]
    else:
        f_b, s_b, f_a, s_a = _default_scores(hazard)
    phase = item.get("phase", "작업 중")
    unit = item.get("unit_task", "본작업")
    wc = "정비작업" if any(k in unit for k in ("정비", "분해", "LOTO", "점검")) else "운전작업"
    injury = item.get("injury", "").strip()
    if injury and len(injury) <= 12:
        injury = _guess_injury(hazard + injury)
    else:
        injury = _guess_injury(hazard)
    return RiskRow(
        work_class=wc,
        phase=phase,
        unit_task=unit,
        hazard=hazard,
        injury=injury,
        current=item.get("current") or _guess_current(hazard),
        freq_before=f_b,
        sev_before=s_b,
        improvements=improvement,
        law=LAW_DEFAULT[0],
        law_url=LAW_DEFAULT[1],
        freq_after=f_a,
        sev_after=s_a,
        source="JSA-PPT",
    )


def build_ppt_jsa_rows(job_name: str, context: str = "") -> list[RiskRow]:
    """첨부 JSA PPT 기반 개선전·개선후 문구"""
    key = match_jsa_ppt_job(job_name, context)
    if not key:
        return []
    items = _load_jsa_ppt_library().get(key, [])
    rows = [_ppt_item_to_row(item) for item in items]
    return rows


def _dict_to_row(item: dict) -> RiskRow:
    from app.local_engine import RiskRow

    wc = item.get("work_class", "운전작업")
    if wc in ("작업준비",):
        wc = "운전작업"
    law = item.get("law", LAW_DEFAULT[0])
    law_url = item.get("law_url", LAW_DEFAULT[1])
    from app.law_catalog import normalize_law
    law, law_url = normalize_law(law, law_url)
    return RiskRow(
        work_class=wc if wc in ("운전작업", "정비작업", "돌발대응") else "운전작업",
        phase=item["phase"],
        unit_task=item["unit_task"],
        hazard=item["hazard"],
        injury=item["injury"],
        current=item["current"],
        freq_before=item["freq_before"],
        sev_before=item["sev_before"],
        improvements=item["improvement"],
        law=law,
        law_url=law_url,
        freq_after=item["freq_after"],
        sev_after=item["sev_after"],
    )


def _is_civil_work(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in CIVIL_KEYWORDS)


def build_field_rows(job_name: str, five_m_one_e: dict[str, str], context: str) -> list[RiskRow]:
    """현장 실무형 개선전·개선후 문구로 RiskRow 생성"""
    text = f"{job_name} {' '.join(five_m_one_e.values())} {context}"

    ppt_rows = build_ppt_jsa_rows(job_name, text)
    if ppt_rows:
        return ensure_work_phases(ppt_rows, job_name)

    rows: list[RiskRow] = []
    seen: set[str] = set()

    def add(item: dict):
        key = item["hazard"][:30]
        if key in seen:
            return
        seen.add(key)
        rows.append(_dict_to_row(item))

    if _is_civil_work(text):
        for item in CIVIL_FIELD_CORE:
            add(item)
        for kw, extras in CIVIL_EXTRA.items():
            if kw in text:
                for item in extras:
                    add(item)

    for item in MECH_FIELD:
        if any(k in text for k in item.get("keywords", [])):
            add({k: v for k, v in item.items() if k != "keywords"})

    for item in ELEC_FIELD:
        if any(k in text for k in item.get("keywords", [])):
            add({k: v for k, v in item.items() if k != "keywords"})

    if rows:
        return rows

    for item in CIVIL_FIELD_CORE:
        add(item)
    return rows


def ensure_work_phases(rows: list[RiskRow], job_name: str = "") -> list[RiskRow]:
    """작업 전·중·후 중 누락된 단계(특히 작업 후) 자동 보완"""
    if not rows:
        rows = [_dict_to_row(WORK_AFTER_DEFAULT)]
        return rows

    phases = {r.phase for r in rows}
    out = list(rows)
    if "작업 후" not in phases:
        out.append(_dict_to_row(WORK_AFTER_DEFAULT))
    return out


_HAZARD_ENDINGS = ("우려", "위험", "발생", "가능", "재해", "질환", "손상", "우려함")


def polish_hazard(text: str) -> str:
    t = text.strip()
    if not t:
        return t
    if not t.endswith(_HAZARD_ENDINGS) and "우려" not in t and "위험" not in t:
        t += " 우려"
    return t


_IMPROVEMENT_ENDINGS = (
    "할 것", "해야 함", "실시", "준수", "확보", "유지", "금지", "배치",
    "착용", "통제", "완료", "발행", "비치", "사용", "운영", "시작",
)


def polish_improvement(text: str) -> str:
    lines: list[str] = []
    for line in clean_improvement_text(text).splitlines():
        line = line.strip().rstrip(".")
        if not line:
            continue
        if line.endswith(_IMPROVEMENT_ENDINGS):
            lines.append(line)
            continue
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if len(parts) >= 2 and all(len(p) < 18 for p in parts):
            lines.append(line)
            continue
        if line.endswith("금지"):
            lines.append(line)
            continue
        if any(v in line for v in ("검사", "점검")) and "실시" not in line:
            line += " 실시할 것"
        elif any(v in line for v in ("착용", "설치", "실시", "준수", "배치", "비치", "통제", "확인", "운영", "제거", "사용", "시작", "발행", "수거", "적재")):
            line += "할 것"
        else:
            line += "할 것"
        lines.append(line)
    return "\n".join(lines)


def clean_improvement_text(text: str) -> str:
    """개선후 문구에서 [공학적]/[관리적]/[보호구] 등 분류 태그 제거"""
    if not text:
        return ""
    lines: list[str] = []
    for line in text.splitlines():
        line = _IMPROVEMENT_TAG.sub("", line.strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _numbered_summary_item(index: int, body: str) -> str:
    body = body.strip()
    if not body:
        return f"{index})"
    first, *rest = body.split("\n")
    item = f"{index}) {first}"
    for line in rest:
        item += f"\n   {line}"
    return item


def format_improvement_summary(rows: list[RiskRow]) -> tuple[str, str]:
    """개선전·개선후 번호 목록 (현장 보고용)"""
    before_lines = []
    after_lines = []
    for i, r in enumerate(rows, 1):
        before_lines.append(f"{i}) {polish_hazard(r.hazard)}")
        after_lines.append(_numbered_summary_item(i, polish_improvement(r.improvements)))
    return "\n".join(before_lines), "\n".join(after_lines)
