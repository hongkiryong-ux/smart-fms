# -*- coding: utf-8 -*-
"""Smart FMS 사용 매뉴얼 PPT 생성."""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "Smart_FMS_사용매뉴얼.pptx"

POSCO_BLUE = RGBColor(0x00, 0x38, 0x76)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DARK = RGBColor(0x33, 0x33, 0x33)
GRAY = RGBColor(0x66, 0x66, 0x66)
LIGHT_BG = RGBColor(0xF0, 0xF4, 0xF8)
ACCENT = RGBColor(0x00, 0x6E, 0xB8)


def _set_slide_bg(slide, color: RGBColor) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_bar(slide, prs) -> None:
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(0),
        Inches(0),
        prs.slide_width,
        Inches(0.45),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = POSCO_BLUE
    shape.line.fill.background()


def _title_slide(prs, title: str, subtitle: str = "") -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide, POSCO_BLUE)
    box = slide.shapes.add_textbox(Inches(0.8), Inches(2.2), Inches(11.5), Inches(1.2))
    tf = box.text_frame
    tf.text = title
    p = tf.paragraphs[0]
    p.font.size = Pt(40)
    p.font.bold = True
    p.font.color.rgb = WHITE
    if subtitle:
        sub = slide.shapes.add_textbox(Inches(0.8), Inches(3.5), Inches(11.5), Inches(1.5))
        stf = sub.text_frame
        stf.text = subtitle
        sp = stf.paragraphs[0]
        sp.font.size = Pt(20)
        sp.font.color.rgb = RGBColor(0xCC, 0xDD, 0xEE)
    foot = slide.shapes.add_textbox(Inches(0.8), Inches(6.5), Inches(11), Inches(0.5))
    foot.text_frame.text = "POSCO WIDE Smart FMS  ·  2026"
    foot.text_frame.paragraphs[0].font.size = Pt(14)
    foot.text_frame.paragraphs[0].font.color.rgb = RGBColor(0xAA, 0xBB, 0xCC)


def _section_slide(prs, title: str, num: str = "") -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide, LIGHT_BG)
    _add_bar(slide, prs)
    if num:
        nbox = slide.shapes.add_textbox(Inches(0.7), Inches(1.0), Inches(2), Inches(0.6))
        nbox.text_frame.text = num
        nbox.text_frame.paragraphs[0].font.size = Pt(18)
        nbox.text_frame.paragraphs[0].font.color.rgb = ACCENT
        nbox.text_frame.paragraphs[0].font.bold = True
    box = slide.shapes.add_textbox(Inches(0.7), Inches(2.0), Inches(11.5), Inches(1.5))
    box.text_frame.text = title
    p = box.text_frame.paragraphs[0]
    p.font.size = Pt(36)
    p.font.bold = True
    p.font.color.rgb = POSCO_BLUE


def _content_slide(prs, title: str, bullets: list[str], note: str = "") -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide, WHITE)
    _add_bar(slide, prs)
    tbox = slide.shapes.add_textbox(Inches(0.6), Inches(0.65), Inches(12), Inches(0.7))
    tbox.text_frame.text = title
    tbox.text_frame.paragraphs[0].font.size = Pt(26)
    tbox.text_frame.paragraphs[0].font.bold = True
    tbox.text_frame.paragraphs[0].font.color.rgb = POSCO_BLUE

    body = slide.shapes.add_textbox(Inches(0.7), Inches(1.45), Inches(12), Inches(5.2))
    tf = body.text_frame
    tf.word_wrap = True
    for i, line in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.font.size = Pt(16)
        p.font.color.rgb = DARK
        p.space_after = Pt(8)
        p.level = 0
        if line.startswith("  ·"):
            p.level = 1
            p.font.size = Pt(14)
            p.font.color.rgb = GRAY

    if note:
        nbox = slide.shapes.add_textbox(Inches(0.7), Inches(6.6), Inches(12), Inches(0.5))
        nbox.text_frame.text = f"※ {note}"
        nbox.text_frame.paragraphs[0].font.size = Pt(12)
        nbox.text_frame.paragraphs[0].font.color.rgb = GRAY


def _two_col_slide(prs, title: str, left_title: str, left: list[str], right_title: str, right: list[str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide, WHITE)
    _add_bar(slide, prs)
    tbox = slide.shapes.add_textbox(Inches(0.6), Inches(0.65), Inches(12), Inches(0.7))
    tbox.text_frame.text = title
    tbox.text_frame.paragraphs[0].font.size = Pt(26)
    tbox.text_frame.paragraphs[0].font.bold = True
    tbox.text_frame.paragraphs[0].font.color.rgb = POSCO_BLUE

    for col_x, col_title, items in [
        (0.6, left_title, left),
        (6.6, right_title, right),
    ]:
        h = slide.shapes.add_textbox(Inches(col_x), Inches(1.35), Inches(5.8), Inches(0.45))
        h.text_frame.text = col_title
        h.text_frame.paragraphs[0].font.size = Pt(18)
        h.text_frame.paragraphs[0].font.bold = True
        h.text_frame.paragraphs[0].font.color.rgb = ACCENT
        body = slide.shapes.add_textbox(Inches(col_x), Inches(1.85), Inches(5.8), Inches(4.8))
        tf = body.text_frame
        tf.word_wrap = True
        for i, line in enumerate(items):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = line
            p.font.size = Pt(14)
            p.font.color.rgb = DARK
            p.space_after = Pt(6)


def _table_slide(prs, title: str, headers: list[str], rows: list[list[str]]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_slide_bg(slide, WHITE)
    _add_bar(slide, prs)
    tbox = slide.shapes.add_textbox(Inches(0.6), Inches(0.65), Inches(12), Inches(0.7))
    tbox.text_frame.text = title
    tbox.text_frame.paragraphs[0].font.size = Pt(26)
    tbox.text_frame.paragraphs[0].font.bold = True
    tbox.text_frame.paragraphs[0].font.color.rgb = POSCO_BLUE

    cols = len(headers)
    table = slide.shapes.add_table(
        len(rows) + 1,
        cols,
        Inches(0.5),
        Inches(1.4),
        Inches(12.3),
        Inches(0.35 * (len(rows) + 2)),
    ).table

    for ci, h in enumerate(headers):
        cell = table.cell(0, ci)
        cell.text = h
        cell.fill.solid()
        cell.fill.fore_color.rgb = POSCO_BLUE
        for p in cell.text_frame.paragraphs:
            p.font.bold = True
            p.font.size = Pt(11)
            p.font.color.rgb = WHITE
            p.alignment = PP_ALIGN.CENTER
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE

    for ri, row in enumerate(rows, start=1):
        for ci, val in enumerate(row):
            cell = table.cell(ri, ci)
            cell.text = val
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(10)
                p.font.color.rgb = DARK
            if ri % 2 == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = LIGHT_BG


def build() -> Path:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # 표지
    _title_slide(
        prs,
        "Smart FMS\n사용 매뉴얼",
        "POSCO WIDE 통합 시설관리 플랫폼\nFacility Management System",
    )

    # 목차
    _content_slide(
        prs,
        "목차",
        [
            "1. 시스템 개요 및 접속",
            "2. 메뉴 구조 (PC / 모바일)",
            "3. 사용자 역할 및 권한",
            "4. Dashboard",
            "5. 사업장·건물·설비 관리",
            "6. 점검(PM) · 점검일지",
            "7. 정비관리 (3단계 워크플로)",
            "8. 가로등 · 위험성평가 · 자재 · 협력사",
            "9. QR 현장 활용",
            "10. 계정관리 · 서버관리 · 백업",
        ],
    )

    # 1. 개요
    _section_slide(prs, "시스템 개요 및 접속", "01")
    _content_slide(
        prs,
        "Smart FMS란?",
        [
            "사업장·건물·설비를 계층적으로 관리하는 통합 시설관리(FMS) 플랫폼입니다.",
            "예방점검(PM), 정비(CMMS), 점검일지, D-1 작업허가, 가로등 QR, 자재·협력사를 하나의 시스템에서 운영합니다.",
            "",
            "접속 주소",
            "  · 관리자 로그인: /admin/login",
            "  · 가입 신청: /admin/signup (관리자 승인 후 사용)",
            "  · 메인 화면: /admin/dashboard",
            "",
            "최초 배포 시 admin 계정이 자동 생성됩니다. 비밀번호는 서버 환경변수(ADMIN_PW)로 설정합니다.",
        ],
    )

    # 2. 메뉴
    _section_slide(prs, "메뉴 구조", "02")
    _table_slide(
        prs,
        "PC 사이드바 메뉴",
        ["메뉴", "경로", "설명"],
        [
            ["Dashboard", "/admin/dashboard", "KPI·운영 현황"],
            ["사업장/건물", "/admin/sites", "사업장·건물·층·구역"],
            ["설비관리", "/admin/equipment", "건물별 설비·엑셀·QR"],
            ["점검(PM)", "/admin/pm", "예방점검 주기·결과"],
            ["점검일지", "/admin/inspection-logs", "건물별 엑셀 점검일지"],
            ["정비접수/승인", "/admin/work-orders", "정비섹션 CMMS"],
            ["정비 List(D-1)", "/admin/d1", "협력사 작업·허가 요청"],
            ["작업허가/승인", "/admin/facility-section", "시설섹션 최종 승인"],
            ["가로등", "/admin/streetlamp/requests", "QR 가로등 정비"],
            ["위험성평가", "/admin/risk-assessment", "JSA·5M1E 평가"],
            ["자재관리", "/admin/materials", "재고·입출고 (팝업)"],
            ["협력사", "/admin/partners", "협력사 마스터"],
            ["계정관리", "/admin/users", "시스템관리자 전용"],
            ["서버관리", "/admin/server", "상태·ZIP 백업"],
        ],
    )
    _two_col_slide(
        prs,
        "PC vs 모바일 내비게이션",
        "PC (데스크톱)",
        [
            "좌측 사이드바에 전체 메뉴 표시",
            "설비관리·점검일지는 건물 목록 펼침",
            "정비관리 그룹: 4개 하위 메뉴",
            "자재관리는 팝업 창으로 열기",
            "엑셀 Import/Export, 대량 QR ZIP",
        ],
        "모바일 (900px 이하)",
        [
            "상단 햄버거 메뉴 + Smart FMS",
            "정비관리 하위 메뉴만 표시",
            "QR 설비·일지작성이 주 사용 환경",
            "PWA: 홈 화면 추가 가능",
            "Dashboard 등은 URL 직접 접근",
        ],
    )

    # 3. 권한
    _section_slide(prs, "사용자 역할 및 권한", "03")
    _table_slide(
        prs,
        "역할별 기본 권한",
        ["역할", "CRUD", "접근 범위"],
        [
            ["시스템관리자", "전체", "모든 메뉴 + 계정·서버"],
            ["사업장관리자", "추가·수정·삭제", "계정·서버 제외"],
            ["그룹장 / 파트장", "추가·수정·삭제", "계정·서버 제외"],
            ["시설담당자", "추가·수정·삭제", "계정·서버 제외"],
            ["협력사 / 외부업체", "수정만", "정비·D-1 중심"],
            ["조회전용", "조회만", "삭제·추가·수정 불가"],
        ],
    )
    _content_slide(
        prs,
        "가입 · 승인 · 권한 설정",
        [
            "1. /admin/signup 에서 아이디·이름·역할·회사·비밀번호 입력",
            "2. 시스템관리자가 계정관리에서 승인/거절",
            "3. 계정별로 CRUD(추가·수정·삭제)와 메뉴 접근을 개별 지정 가능",
            "4. 내 계정(/admin/account)은 모든 로그인 사용자 접근 가능",
            "",
            "계정관리·서버관리는 시스템관리자만 접근할 수 있습니다.",
            "본인 계정의 역할 변경·삭제는 불가합니다.",
        ],
    )

    # 4. Dashboard
    _section_slide(prs, "Dashboard", "04")
    _content_slide(
        prs,
        "Dashboard 주요 기능",
        [
            "사업장·건물·설비 수, PM(금일 계획·완료·지연), 정비(의뢰·진행·완료·긴급)",
            "D-1(금일 계획·완료·미완료) 등 KPI를 한눈에 확인",
            "KPI 카드 클릭 시 해당 모듈로 바로 이동",
            "30초마다 자동 갱신",
            "",
            "레이아웃 3종 (/admin/dashboard/layouts)",
            "  · 운영(ops): KPI 중심",
            "  · 한눈에(bento): 카드형 요약",
            "  · 시설(gallery): 시설 중심 뷰",
        ],
    )

    # 5. 설비
    _section_slide(prs, "사업장 · 건물 · 설비", "05")
    _content_slide(
        prs,
        "시설 계층 구조",
        [
            "사업장 → 건물 → 층 → 구역 → 설비 순으로 계층 관리",
            "",
            "사업장/건물 (/admin/sites)",
            "  · 사업장·건물 등록·수정·삭제",
            "  · 건물 상세: 층·구역 추가, 도면·표준서 첨부 (최대 20MB)",
            "",
            "설비관리 (/admin/equipment)",
            "  · 사이드바 또는 건물 카드에서 건물 선택",
            "  · 분류(시트)별 설비 목록·상세·정비의뢰·변경로그",
            "  · QR PNG/ZIP 다운로드 → 현장 부착",
        ],
    )
    _content_slide(
        prs,
        "설비 엑셀 Import / Export",
        [
            "엑셀 Import (/admin/equipment/import)",
            "  · 건물별 xlsx 업로드 → 시트별 설비 자동 등록",
            "  · 38개 건물 일괄 Import 지원",
            "  · 총괄 시트는 제외, 시트명 = 설비 분류",
            "",
            "엑셀 Export",
            "  · 건물별「엑셀 출력」→ {건물명}_설비현황.xlsx",
            "  · 설비 상세「Excel 내보내기」→ 사양·정비·점검·주기·의뢰",
            "",
            "백업 ZIP에도 동일 형식의 설비현황·설비상세 파일이 포함됩니다.",
        ],
        note="설비 템플릿(/admin/templates)은 URL 직접 접근으로 종류·점검항목 관리",
    )

    # 6. PM / 점검일지
    _section_slide(prs, "점검(PM) · 점검일지", "06")
    _two_col_slide(
        prs,
        "점검(PM) · 점검일지",
        "예방점검 PM (/admin/pm)",
        [
            "탭: 점검목록 / 주기설정",
            "주기: 일·주·월·분기·반기·연·사용자지정",
            "결과: 정상 / 주의 / 고장",
            "이상 시 정비의뢰 연계",
            "Excel 내보내기 지원",
            "QR(/eq/{코드})에서도 PM 등록 가능",
        ],
        "점검일지 (/admin/inspection-logs)",
        [
            "건물 등록 후 엑셀 파일 업로드",
            "「이 파일로 연결」→ QR 일지작성 대상 지정",
            "OnlyOffice 또는 브라우저 간단 편집기",
            "편집 URL: .../files/{id}/edit",
            "마지막 편집 셀 위치 저장",
            "OnlyOffice: docker-compose.onlyoffice.yml",
        ],
    )

    # 7. 정비
    _section_slide(prs, "정비관리 3단계 워크플로", "07")
    _content_slide(
        prs,
        "정비 전체 흐름",
        [
            "① 정비접수/승인 (정비섹션) — /admin/work-orders",
            "  · 정비의뢰 접수 → 정비진행 → 정비완료",
            "  · 협력사 지정 + D-1 일괄/개별 승인",
            "",
            "② 정비 List(D-1)/협력사 — /admin/d1",
            "  · D-1 승인된 건만 표시 · 예정일·조치·진행상태 편집",
            "  · 작업허가 승인요청 (개별·일괄)",
            "",
            "③ 작업허가/승인 (시설섹션) — /admin/facility-section",
            "  · 잠재위험·안전대책 확인 → 작업허가 최종 승인",
            "",
            "완료 시 설비 정비이력에 자동 등록됩니다.",
        ],
    )
    _table_slide(
        prs,
        "정비 상태 · D-1 보드 필터",
        ["단계", "상태/필터", "설명"],
        [
            ["정비섹션", "정비의뢰→진행→완료", "CMMS 접수·승인"],
            ["D-1", "전체/접수/오늘/내일/예정/완료", "협력사 작업 보드"],
            ["시설섹션", "하루전/당일/예정/전체", "작업허가 승인"],
            ["D-1 승인", "d1_approved", "승인 후 D-1 목록 노출"],
        ],
    )

    # 8. 기타 모듈
    _section_slide(prs, "가로등 · 위험성평가 · 자재 · 협력사", "08")
    _two_col_slide(
        prs,
        "가로등 · 위험성평가",
        "가로등 (정비관리 → 가로등)",
        [
            "정비의뢰 목록·상태·메모·Excel",
            "QR ZIP (전체/구역별)",
            "CSV·엑셀 일괄 등록",
            "일일 리포트 메일·SMS 설정",
            "시민 QR: /lamp/{코드} (로그인 불필요)",
            "진행 조회: /status (이름+전화 4자리)",
        ],
        "위험성평가 · 자재 · 협력사",
        [
            "위험성평가: 로컬/AI 모드, HTML·Excel 출력",
            "자재관리: 그룹별 재고, 입출고, Excel",
            "  · 메뉴 클릭 시 팝업 창으로 열림",
            "협력사: 업체·담당·연락·계약만료",
            "  · D-1·작업허가·시설섹션 기본값 연동",
        ],
    )

    # 9. QR
    _section_slide(prs, "QR 현장 활용", "09")
    _table_slide(
        prs,
        "QR · 공개 URL",
        ["URL", "로그인", "기능"],
        [
            ["/eq/{설비코드}", "불필요", "설비 조회·PM·정비이력"],
            ["/eq/{코드}/log", "불필요", "점검일지 엑셀 바로 편집·저장"],
            ["/lamp/{가로등코드}", "불필요", "가로등 정비 의뢰"],
            ["/status", "불필요", "가로등 진행상황 조회"],
        ],
    )
    _content_slide(
        prs,
        "QR 점검일지 작성 절차",
        [
            "1. 점검일지 메뉴에서 건물 등록",
            "2. 엑셀 파일 업로드",
            "3. 해당 파일에「이 파일로 연결」버튼 클릭 (건물당 1개 연결)",
            "4. 현장에서 설비 QR 스캔 →「일지작성」또는 /eq/{코드}/log",
            "5. 연결된 엑셀이 바로 열림 → 편집 후 저장",
            "",
            "로그인 없이 QR에서 작성·저장 가능합니다.",
        ],
    )

    # 10. 관리
    _section_slide(prs, "계정관리 · 서버관리 · 백업", "10")
    _content_slide(
        prs,
        "서버관리 · ZIP 백업",
        [
            "접근: /admin/server (시스템관리자 전용)",
            "",
            "서버 상태: CPU·메모리·디스크·DB 용량 (30초 자동 갱신)",
            "",
            "「ZIP 백업 받기」(/admin/server/backup.zip)",
            "  · files/ — 점검일지·도면·표준서·업로드 원본",
            "  · excel/업무데이터.xlsx — DB 전체 flat export",
            "  · excel/설비현황/ — 건물별 설비현황 xlsx (UI와 동일)",
            "  · excel/설비상세/ — 설비별 상세 xlsx (UI와 동일)",
            "  · README.txt — 생성 시각·건수 요약",
            "",
            "무압축 스트리밍으로 빠르게 다운로드됩니다.",
        ],
    )
    _content_slide(
        prs,
        "URL 빠른 참조",
        [
            "로그인 /admin/login  ·  가입 /admin/signup  ·  Dashboard /admin/dashboard",
            "설비관리 /admin/equipment  ·  Import /admin/equipment/import",
            "점검(PM) /admin/pm  ·  점검일지 /admin/inspection-logs",
            "정비접수 /admin/work-orders  ·  D-1 /admin/d1  ·  시설섹션 /admin/facility-section",
            "가로등 /admin/streetlamp/requests  ·  자재 /admin/materials  ·  협력사 /admin/partners",
            "계정 /admin/users  ·  서버 /admin/server  ·  내 계정 /admin/account",
            "Health Check /health",
        ],
    )

    # 마무리
    _title_slide(
        prs,
        "감사합니다",
        "Smart FMS 사용 중 문의는 시스템관리자에게 연락하세요.",
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(OUT))
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"생성 완료: {path} ({path.stat().st_size // 1024} KB)")
