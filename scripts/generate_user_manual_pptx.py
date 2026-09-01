#!/usr/bin/env python3
"""Smart FMS 사용자 매뉴얼 PPT 생성 스크립트 (고밀도·소형 폰트)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "Smart_FMS_사용매뉴얼.pptx"

COLOR_PRIMARY = RGBColor(0x00, 0x3D, 0x7A)
COLOR_ACCENT = RGBColor(0x00, 0x7A, 0xCC)
COLOR_TEXT = RGBColor(0x22, 0x22, 0x22)
COLOR_MUTED = RGBColor(0x55, 0x55, 0x55)
COLOR_SECTION = RGBColor(0x00, 0x3D, 0x7A)
FONT = "맑은 고딕"

# 폰트 크기 (페이지당 정보 밀도 ↑)
SZ_COVER_TITLE = 28
SZ_COVER_SUB = 11
SZ_TITLE = 17
SZ_SECTION = 9.5
SZ_BODY = 8.5
SZ_FOOTER = 7.5


@dataclass
class SlideSpec:
    title: str
    left: list[str] = field(default_factory=list)
    right: list[str] = field(default_factory=list)
    cover: bool = False


# 32장 → 10장: 주제별 통합, 항목은 최대한 상세 기술
SLIDES: list[SlideSpec] = [
    SlideSpec(
        "Smart FMS  사용자 매뉴얼",
        left=[
            "POSCO WIDE 스마트 시설관리 플랫폼 (Facility Management System)",
            "예방점검(PM) · 정비(CMMS) · 점검일지(엑셀·웹) · QR 현장운영 · AI 분석 · 위험성평가",
            "배포: Render(main 자동) · DB: PostgreSQL/SQLite · PC 브라우저 + 모바일 QR",
            "POSCO WIDE Smart FMS  ·  2026.09",
        ],
        cover=True,
    ),
    SlideSpec(
        "01  시스템 개요 · 접속 · 역할 · 메뉴",
        left=[
            "【시스템】 사업장·건물·설비 기준정보, PM, 점검일지1(엑셀)·2(웹), 정비의뢰→D-1→작업허가, 자재·협력사, 위험성평가, AI, 가로등 QR",
            "【접속】 로그인 /admin/login · 가입 /admin/signup(관리자 승인) · 내 계정 /admin/account(항상 접근, API키·비번)",
            "【초기관리자】 환경변수 ADMIN_ID/ADMIN_PW (배포 시 반드시 변경)",
            "【역할】 시스템관리자·사업장관리자·그룹장·파트장·시설담당자·협력사·외부업체·조회전용",
            "【CRUD】 can_create/edit/delete — 역할 기본값 + 계정별 조정",
            "【전용메뉴】 서버관리·계정관리 = system_admin만",
            "【협력사/외부】 설비·PM·점검일지·점검일지2·시설섹션·가로등 기본 차단, 수정(E)만",
            "【조회전용】 읽기만, C/E/D 불가",
        ],
        right=[
            "【메뉴 — PC 사이드바】",
            "Dashboard /admin/dashboard",
            "주요설비 일정 /admin/schedules",
            "공지사항 /admin/notices",
            "사업장·건물 /admin/sites",
            "설비관리 /admin/equipment",
            "점검(PM) /admin/pm",
            "점검일지 /admin/inspection-logs",
            "점검일지2 /admin/inspection-logs2",
            "정비접수/승인 /admin/work-orders",
            "정비 List(D-1) /admin/d1",
            "작업허가/승인 /admin/facility-section",
            "가로등 /admin/streetlamp/requests",
            "AI 분석 /admin/ai-analysis",
            "서버관리 /admin/server",
            "위험성평가 /admin/risk-assessment",
            "자재관리 /admin/materials?popup=1",
            "협력사 /admin/partners",
            "계정관리 /admin/users",
            "※ menu_access JSON으로 계정별 메뉴 개별 허용·차단",
        ],
    ),
    SlideSpec(
        "02  Dashboard · 일정 · 공지",
        left=[
            "【Dashboard /admin/dashboard】",
            "KPI: 사업장·건물·설비 수, PM 지연, 정비의뢰, 오늘/어제 점검 등 (30초 TTL 캐시)",
            "위젯: 정비현황·사업장현황·에너지·오늘 일정·공지 — 순서·표시·레이아웃(gallery/bento) 사용자 설정",
            "서버 상태 위젯: Render 배포·DB·디스크·메모리 (관리자 화면과 연동)",
            "【주요설비 일정 /admin/schedules】",
            "캘린더 등록·삭제 · 카테고리: 긴급/점검/작업/검수",
            "Dashboard 「오늘 일정」 위젯과 연동",
        ],
        right=[
            "【공지사항 /admin/notices】",
            "등록·삭제 · 카테고리: 긴급/안전/일반",
            "Dashboard 공지 위젯 표시",
            "【운영 팁】",
            "· 사이드바 메뉴는 역할·menu_access 미허용 시 /admin/account?error=no_menu",
            "· 정비 3메뉴(정비섹션·D-1·시설섹션)에 신규 건수 배지 표시",
            "· 내 계정에서 OpenAI API 키·모델(gpt-4o-mini 기본) 설정 → AI·위험성평가 공용",
        ],
    ),
    SlideSpec(
        "03  사업장 · 건물 · 설비 · QR",
        left=[
            "【사업장/건물 /admin/sites】",
            "사업장·건물·층·구역 CRUD · 도면·표준서 파일 업로드",
            "【설비관리 /admin/equipment】",
            "설비 등록·수정·삭제 · 사업장·건물별 트리 · 상세 스펙·정비·PM 이력",
            "엑셀 Import/Export(전체·건물별) · UI export와 동일 형식",
            "QR: 개별 PNG /admin/equipment/{id}/qr.png",
            "건물 일괄 ZIP /admin/equipment/building/{building_id}/qr-zip",
            "설비 화면에서 정비의뢰·PM·점검일지 바로 연계",
        ],
        right=[
            "【설비 QR · 현장(로그인 불필요)】",
            "설비 상세: {BASE}/eq/{설비코드}",
            "  → 모바일: 설비정보·정비의뢰·PM점검·일지작성",
            "점검일지 작성: {BASE}/eq/{코드}/log",
            "  → save/cursor/unlock/heartbeat API 지원",
            "PM 현장점검: POST /eq/{코드}/pm-inspect (고장→정비의뢰)",
            "【환경설정】 PUBLIC_BASE_URL = QR 도메인 고정(배포 URL과 일치 권장)",
            "【백업 포함】 설비현황·설비상세 엑셀은 서버 ZIP 백업 excel/ 폴더에 포함",
        ],
    ),
    SlideSpec(
        "04  점검(PM) · 점검일지1",
        left=[
            "【점검(PM) /admin/pm】",
            "일정 등록: 매일/매주/매월/분기/반기/연간/사용자정의",
            "점검 실행 POST .../schedules/{id}/inspect → 정상/주의/고장",
            "지연·기한 필터 · 엑셀 Export /admin/pm/export",
            "QR 현장: /eq/{코드} → PM 점검, 고장 시 정비의뢰 자동 연계 가능",
            "설비 상세·PM 메뉴 양쪽에서 일정 등록 가능",
        ],
        right=[
            "【점검일지1 — 엑셀 /admin/inspection-logs】",
            "건물별 점검일지 등록 → 엑셀 업로드",
            "OnlyOffice 또는 레거시 편집기 웹 편집",
            "QR 일지: {BASE}/eq/{코드}/log (설비 QR와 동일 경로)",
            "건물 add/remove 시 invalidate_nav_cache → 사이드바 즉시 반영",
            "【점검일지1 vs 2】",
            "1=건물별 엑셀 파일·OnlyOffice / 2=건물명 유형별 웹폼·자동집계·QR 1일입력",
        ],
    ),
    SlideSpec(
        "05  점검일지2 — 웹 운영일보 (4유형)",
        left=[
            "【공통 /admin/inspection-logs2】",
            "건물 등록 POST .../buildings → 건물명으로 유형 자동 판별·전용 화면",
            "1일 입력·저장 · 일 마감 close-day(엑셀 아카이브) · 월보/년보 Export",
            "QR 1일 입력(로그인 불필요) · QR PNG 각 화면에서 다운로드",
            "리다이렉트 우선: 주택변전소→제철소본부→중앙관제실(설비)→중앙관제실",
            "【주택변전소】명에 '주택변전소' 포함",
            "  관리 .../housing · QR {BASE}/hs/{code}/daily",
            "  전력·운전 자동집계 · 월보/년보 kWh · 아카이브 download",
            "【중앙관제실】명 '중앙관제실'(설비 제외)",
            "  관리 .../central-control-room · QR {BASE}/ccr/{code}/daily",
            "  일·월·년 Export · 아카이브",
        ],
        right=[
            "【중앙관제실(설비)】명 '중앙관제실(설비)'",
            "  관리 .../ccr-facility · QR {BASE}/ccrf/{code}/daily",
            "  설비별 운전·점검 항목 · daily Export",
            "【제철소본부】명 '제철소본부'",
            "  관리 .../steelworks-hq · QR {BASE}/swhq/{code}/daily",
            "  NO.1/NO.2 수전 배율 12000 · 일사용량 kWh 자동계산",
            "  전일 월누계 기반 일일·월누계 재계산(recompute_daily/finalize)",
            "【미매칭】 inspection_log2_detail.html 일반 상세",
            "【AI 연동】 주택변전소 월별 전력 _gather_housing_monthly_reports",
        ],
    ),
    SlideSpec(
        "06  정비 프로세스 (정비섹션 · D-1 · 시설섹션)",
        left=[
            "【흐름】",
            "설비QR·관리화면·수동 → 정비의뢰(received) → 배정(assigned)",
            "→ D-1승인 → D-1보드 → 작업허가요청 → 시설승인(permit)",
            "→ in_progress → completed → verified → closed",
            "【정비접수/승인 /admin/work-orders】",
            "목록·상태필터·상세 · D-1승인·일괄D-1승인(approve-d1)",
            "작업허가 요청 request-approval → 시설섹션 전달 · 엑셀 Export",
        ],
        right=[
            "【정비 List(D-1) /admin/d1】",
            "D-1 계획 보드 · 협력사별 필터(partner_id)",
            "단계: draft→review→approved→JSA→TBM→permit→진행→완료 (advance)",
            "【작업허가/승인 /admin/facility-section】",
            "보드: day_before(전일)·today·scheduled·all",
            "작업허가·일괄승인 · 협력사별 위험등급·기본값",
            "【배지】 maint_nav_badges — 3메뉴 신규 건수",
            "【협력사 연계】 /admin/partners 코드·담당자·계약종료",
            "  partner/external 계정: partner_id·company_name",
        ],
    ),
    SlideSpec(
        "07  자재 · 협력사 · 위험성평가 · 가로등",
        left=[
            "【자재 /admin/materials?popup=1】",
            "품목·입고(stock-in)·출고(stock-out)·그룹·재고로그",
            "Import/Export · reset · 설비화면 팝업 연동",
            "【협력사 /admin/partners】",
            "코드·담당자·계약종료 · D-1·시설·정비 필터",
            "【위험성평가 /admin/risk-assessment】",
            "작업명+4M+1E(인·기계·재료·방법·환경) · 대분류·프리셋",
            "AI 보조 assess(use_ai) · 문서 학습 learn · HTML/Excel export",
        ],
        right=[
            "【가로등】",
            "공개: {BASE}/lamp/{코드} → 유형선택·접수 · /status 조회",
            "관리: /admin/streetlamp/requests 목록·상태·삭제·Export",
            "QR ZIP /admin/streetlamp/qr-zip · 설정 SMS/메일",
            "CSV import · cron /cron/streetlamp/daily-report",
            "【권한 요약】",
            "system_admin: 전메뉴+서버+계정",
            "site_admin~facility_manager: users/server 제외 C/E/D",
            "partner/external: 정비·D-1·자재·협력사·AI·위험성 등, E만",
            "viewer: 조회만",
        ],
    ),
    SlideSpec(
        "08  AI 분석 · GPT 대화",
        left=[
            "【AI 분석 /admin/ai-analysis】",
            "질의 POST /admin/ai-analysis/ask",
            "  · 집계모드: DB스냅샷 즉시답(API키 불필요)",
            "  · GPT상세(mode=detail): 전체컨텍스트+GPT",
            "【데이터범위 gather_context】",
            "사업장·건물·설비·정비·PM·D-1·협력사·점검일지1/2",
            "자재·공지·일정·가로등·주택변전소 월보전력 등",
            "의도분류: 설비·정비·PM·가로등·D-1·협력사·점검일지·자재·전체현황",
        ],
        right=[
            "【GPT 대화 /admin/ai-analysis/chat】",
            "세션 최대 20턴 · 의도별 DB섹션 강화조회",
            "GPT응답 + evidence(집계근거) 병행",
            "API키 미설정: 버튼활성+안내박스(비활성 아님)",
            "【설정 POST /admin/ai-analysis/ai-settings】",
            "openai_api_key · openai_model(기본 gpt-4o-mini)",
            "내 계정 /admin/account 에서도 동일 키 설정",
            "【위험성평가 AI】 동일 API키 공유 · assess·learn 별도 메뉴",
        ],
    ),
    SlideSpec(
        "09  서버관리 · 계정 · QR URL 부록",
        left=[
            "【서버관리 /admin/server — system_admin】",
            "상태 30초 갱신: Render·DB·디스크·메모리·CPU",
            "GET /admin/server/status (JSON 폴링)",
            "백업 ZIP /admin/server/backup.zip",
            "  files/: 일지·도면·표준서·업로드",
            "  excel/: 업무데이터·설비현황·설비상세 + README.txt",
            "매뉴얼 /admin/server/manual.pptx",
            "발표자료 /admin/server/presentation.pptx",
            "【계정 /admin/users】",
            "가입승인 · 역할 · CRUD · menu_access · partner 연결",
        ],
        right=[
            "【QR·URL 부록 — {BASE}=PUBLIC_BASE_URL】",
            "설비 {BASE}/eq/{code} · 일지 .../eq/{code}/log",
            "주택 {BASE}/hs/{code}/daily",
            "CCR {BASE}/ccr/{code}/daily",
            "CCR설비 {BASE}/ccrf/{code}/daily",
            "제철소 {BASE}/swhq/{code}/daily",
            "가로등 {BASE}/lamp/{code}",
            "【다운로드】 서버관리 → 사용 매뉴얼 PPT 받기",
            "문의: 시스템관리자 · POSCO WIDE Smart FMS",
        ],
    ),
]


def _font(run, *, size: float, bold: bool = False, color: RGBColor = COLOR_TEXT):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def _para(tf, idx: int):
    return tf.paragraphs[0] if idx == 0 else tf.add_paragraph()


def _write_lines(box, lines: list[str], *, cover: bool = False):
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Pt(2)
    tf.margin_top = tf.margin_bottom = Pt(1)
    for i, raw in enumerate(lines):
        text = raw.strip()
        if not text:
            continue
        p = _para(tf, i)
        p.alignment = PP_ALIGN.CENTER if cover else PP_ALIGN.LEFT
        p.space_before = Pt(0)
        p.space_after = Pt(1 if not cover else 3)
        p.line_spacing = 1.0

        is_section = text.startswith("【")
        run = p.add_run()
        run.text = text
        if cover:
            _font(run, size=SZ_COVER_SUB, bold=(i == 0), color=COLOR_MUTED if i else COLOR_TEXT)
        elif is_section:
            _font(run, size=SZ_SECTION, bold=True, color=COLOR_SECTION)
        else:
            _font(run, size=SZ_BODY, color=COLOR_TEXT)


def _add_title(slide, text: str, *, cover: bool = False):
    top = Inches(1.8) if cover else Inches(0.28)
    h = Inches(1.6) if cover else Inches(0.55)
    box = slide.shapes.add_textbox(Inches(0.45), top, Inches(12.4), h)
    tf = box.text_frame
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER if cover else PP_ALIGN.LEFT
    run = p.add_run()
    run.text = text.replace("\n", " ")
    _font(run, size=SZ_COVER_TITLE if cover else SZ_TITLE, bold=True, color=COLOR_PRIMARY)


def _add_footer(slide, page: int, total: int):
    box = slide.shapes.add_textbox(Inches(11.0), Inches(7.15), Inches(2.0), Inches(0.25))
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    run = p.add_run()
    run.text = f"{page} / {total}"
    _font(run, size=SZ_FOOTER, color=COLOR_MUTED)


def build_presentation() -> Presentation:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    total = len(SLIDES)
    content_top = Inches(0.88)
    content_h = Inches(6.15)

    for idx, spec in enumerate(SLIDES, start=1):
        slide = prs.slides.add_slide(blank)
        bar = slide.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width, Inches(0.08))
        bar.fill.solid()
        bar.fill.fore_color.rgb = COLOR_ACCENT
        bar.line.fill.background()

        _add_title(slide, spec.title, cover=spec.cover)

        if spec.cover:
            box = slide.shapes.add_textbox(Inches(1.0), Inches(3.0), Inches(11.3), Inches(3.5))
            _write_lines(box, spec.left, cover=True)
        elif spec.right:
            half = Inches(6.15)
            gap = Inches(0.35)
            left_box = slide.shapes.add_textbox(Inches(0.45), content_top, half, content_h)
            right_box = slide.shapes.add_textbox(
                Inches(0.45) + half + gap, content_top, half, content_h
            )
            _write_lines(left_box, spec.left)
            _write_lines(right_box, spec.right)
        else:
            box = slide.shapes.add_textbox(Inches(0.45), content_top, Inches(12.4), content_h)
            _write_lines(box, spec.left)

        if not spec.cover:
            _add_footer(slide, idx, total)

    return prs


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    prs = build_presentation()
    prs.save(str(OUTPUT))
    print(f"Wrote {OUTPUT} ({len(SLIDES)} slides)")


if __name__ == "__main__":
    main()
