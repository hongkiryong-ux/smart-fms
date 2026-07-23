"""프롬프트 및 템플릿 관리"""

from __future__ import annotations

import json
from pathlib import Path

from app.runtime_paths import APP_ROOT, DATA_DIR, PROMPTS_DIR

BASE_DIR = APP_ROOT

WELCOME_TEMPLATE = """▣ 안녕하세요 P-WIDE 위험성평가 도우미(V1)입니다.

::::::::::::::::::::위험성평가의 사전조사 과정에 사용하는 프로그램 입니다. ::::::::::::::::::::

------------------------------------------------------------------------------------

위험성평가를 진행하고자 한다면 『①』 대분류, 소분류로 선택
 『②』 5M1E 자동채우기된 내용수정

--------------------------------------------------------------------------------------------------------------

**① 작업명에 대한 위험성평가를 진행** 다만 작업표준명만 입력해도 위험성평가 가 진행되게 해줘, 작업또는 설비가 확정되면 해당 내용에 대한 점검, 정비 , 돌발 에 대한 JSA 평가를 진행
평가가 완료되면 추가입력조건을 계속 출력



  ============【 5M 1E 입력 】============

  1.Man(작업자 능력·행동) : 작업자의 자격, 숙련도, 작업행동, 인원구성 등
  2.Machine(설비·장비 상태) : 기계·설비·공구의 구조, 성능, 운전상태 등
  3.Material(자재·물질 상태) : 취급·발생 물질, 에너지, 가스, 유체 등
  4.Method(작업방법·절차) : 작업 수행 절차, 작업방식, 작업공법 등
  5.Management(점검·관리 상태) : 점검, 검사, 작업통제, 유지관리 등
  6.Environment(작업환경 조건) : 작업장 위치, 주변환경, 기후·공간조건 등

**완료된 평가의 추가 정보가 필요하다면 아래 명령 (1~7)**

   다른 위험성 평가를 하려면 『다른위험성평가』 를 입력하세요

--------------------------------------------------------------------------------------------------------------

1. 작업표준생성

2. 기술자료·기술표준

3. 전국 사고사례 (안전·뉴스)

4. 정부 위험성평가에 따른 진행단계별 고려사항

5. 산안법 및 안전보건기준에 관한규칙 이행조항

6. 고용노동부 등 정부 안전 및 교육자료

7. 기타 편안하게 질문해 주세요

---"""

ADDITIONAL_COMMANDS = {
    1: "작업표준생성",
    2: "기술자료·기술표준",
    3: "전국 사고사례 (안전·뉴스)",
    4: "위험성평가 진행단계별 고려사항",
    5: "산안법·안전보건기준 이행조항",
    6: "정부 안전·교육자료",
    7: "기타 질문",
}

# 추가 명령 1~6 — 작업명과 함께 검색할 주제 (버튼 표시 내용 기준)
COMMAND_SEARCH_TOPIC = {
    1: "작업표준",
    2: "기술자료 기술표준",
    3: "안전사고 산재",
    4: "위험성평가 진행단계",
    5: "산안법 안전보건기준",
    6: "안전 교육자료",
}

HAZARD_CHECKLIST = [
    "끼임", "떨어짐", "넘어짐", "감전", "부딪힘", "맞음", "폭발·파열", "깔림",
    "화재", "불균형 및 무리한 동작", "이상온도 물체접촉", "화학물질누출 접촉",
    "업무상질병", "교통", "산소결핍", "절단", "베임", "찔림", "무너짐",
    "동물상해", "빠짐익사", "체육 행사등의 사고", "폭력행위",
]

SAFETY_SOURCES = [
    ("고용노동부", "https://www.moel.go.kr/index.do"),
    ("안전보건공단", "https://smartsearch.kosha.or.kr"),
    ("법무부 안전관련법규", "https://www.law.go.kr"),
    ("중대재해처벌법 관련", "https://www.koshahub.or.kr"),
    ("위험성평가(KRAS)", "https://kras.kosha.or.kr/kras24"),
    ("중대재해알림e", "https://labor.moel.go.kr/sasttc/main/main.do"),
    ("산업안전보건기준에 관한 규칙", "https://www.law.go.kr/법령/산업안전보건기준에%20관한%20규칙"),
]

# 안전자료 수집 — 사이트별 검색 URL ({q} = URL-encoded 키워드)
SAFETY_SOURCE_SEARCH = [
    ("고용노동부", "https://www.moel.go.kr/info/lawinfo/law/LawSearch.do?searchKeyword={q}"),
    ("안전보건공단", "https://smartsearch.kosha.or.kr/?searchValue={q}"),
    ("법무부 안전관련법규", "https://www.law.go.kr/lsSc.do?menuId=1&subMenuId=15&tabMenuId=81&query={q}"),
    ("중대재해처벌법 관련", "https://www.koshahub.or.kr/?is_keyword={q}"),
    ("위험성평가(KRAS)", "https://kras.kosha.or.kr/kras24"),
    ("중대재해알림e", "https://labor.moel.go.kr/sasttc/main/main.do"),
    ("산업안전보건기준에 관한 규칙", "https://www.law.go.kr/법령/산업안전보건기준에%20관한%20규칙"),
]


def load_system_prompt() -> str:
    path = PROMPTS_DIR / "condition1_system.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_web_system_prompt() -> str:
    path = PROMPTS_DIR / "condition1_web_assessment.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "산업안전 JSA 위험성평가 전문가. 인사 없이 즉시 평가표 작성. "
        "운전/정비/돌발 × 작업전/중/후, 빈도·강도·등급, 법적근거 URL 포함."
    )


def load_presets() -> list[dict]:
    for name in ("work_types.json", "presets.json"):
        path = DATA_DIR / name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("presets", [])
    return []


def format_five_m_one_e(data: dict[str, str]) -> str:
    labels = {
        "Man": "1.Man(작업자 능력·행동)",
        "Machine": "2.Machine(설비·장비 상태)",
        "Material": "3.Material(자재·물질 상태)",
        "Method": "4.Method(작업방법·절차)",
        "Management": "5.Management(점검·관리 상태)",
        "Environment": "6.Environment(작업환경 조건)",
    }
    lines = []
    for key, label in labels.items():
        value = data.get(key, "").strip()
        if value:
            lines.append(f"  {label} : {value}")
    return "\n".join(lines)


def build_assessment_user_message(
    job_name: str,
    five_m_one_e: dict[str, str],
    extra_info: str = "",
    safety_context: str = "",
    *,
    compact: bool = False,
) -> str:
    safety_max = 1500 if compact else 6000
    parts = [
        "아래 5M 1E 정보를 바탕으로 JSA 기반 위험성평가를 즉시 수행해 주세요.",
        "",
        f"【작업/설비명】 {job_name or '미지정'}",
        "",
        "【5M 1E 정보】",
        format_five_m_one_e(five_m_one_e),
    ]
    if extra_info.strip():
        parts.extend(["", "【추가입력】", extra_info.strip()[:2000 if compact else 4000]])
    if safety_context.strip():
        parts.extend(["", "【참고 자료】", safety_context.strip()[:safety_max]])
    if compact:
        parts.extend([
            "",
            "【요청】 JSA 표 12~20행 이내, 핵심만. 즉시 표부터 출력.",
        ])
    else:
        parts.extend([
            "",
            "【필수 검토 위험점】",
            ", ".join(HAZARD_CHECKLIST),
            "",
            "운전작업, 정비작업, 돌발대응 각각에 대해 작업 전/중/후 단계별 평가표를 작성하세요.",
            "모든 항목에 빈도-강도-등급을 한글로 표기하고, 법적 근거 하이퍼링크를 포함하세요.",
        ])
    return "\n".join(parts)


def build_additional_command_message(
    command_num: int,
    job_name: str,
    context: str,
    *,
    five_m: dict[str, str] | None = None,
    rows: list | None = None,
    user_question: str = "",
) -> str:
    cmd = ADDITIONAL_COMMANDS.get(command_num, "기타")
    extra = ""
    if command_num == 1:
        extra = (
            "위험성평가표의 단위작업·JSA 단계(작업 전/중/후) 순서에 맞춰 "
            "작업표준(SOP)을 작성하세요. 각 단계별 유해·위험·개선대책·법적근거를 반영하세요.\n"
        )
    elif command_num == 2:
        extra = (
            f"작업명 『{job_name}』의 기술자료·기술표준(MSDS·설비사양·작업표준 등)을 검색·정리하세요.\n"
        )
    elif command_num == 3:
        extra = (
            f"작업명 『{job_name}』 관련 전국 사고사례를 "
            "KOSHA·고용노동부·중대재해알림e 및 뉴스·언론 보도를 검색해 정리하세요.\n"
        )
    elif command_num == 7 and user_question.strip():
        extra = (
            f"사용자 질문: 『{user_question.strip()}』\n"
            f"작업명 『{job_name}』과 위 질문에 맞게 관련 자료를 검색·정리하여 답변하세요. "
            "KOSHA·고용노동부·law.go.kr 등 공식 출처 링크를 포함하세요.\n"
        )
    elif command_num in range(4, 8):
        extra = (
            f"작업명 『{job_name}』에 맞게 관련 자료를 검색·정리하여 작성하세요. "
            "KOSHA·고용노동부·law.go.kr 등 공식 출처 링크를 포함하세요.\n"
        )
    fm_block = ""
    if five_m and any(five_m.values()):
        fm_block = f"\n【5M 1E】\n{format_five_m_one_e(five_m)}\n"
    row_hint = ""
    if rows:
        row_hint = f"\n【평가표】 {len(rows)}건 — 단위작업·단계 순서를 우선 따르세요.\n"
    return (
        f"완료된 위험성평가(작업: {job_name})에 대해 다음 추가 요청을 처리해 주세요.\n"
        f"요청: {command_num}. {cmd}\n\n"
        f"{extra}{fm_block}{row_hint}\n"
        f"【기존 평가 요약/맥락】\n{context[:8000]}"
    )
