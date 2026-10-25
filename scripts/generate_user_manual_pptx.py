#!/usr/bin/env python3
"""시설관리실 DX 시스템(Smart FMS) 사용자 매뉴얼 PPT 생성 스크립트 (고밀도·소형 폰트)."""
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


# 주제별 통합, 항목은 최대한 상세 기술 (2026.10 기준 시스템)
SLIDES: list[SlideSpec] = [
    SlideSpec(
        "시설관리실 DX 시스템  사용자 매뉴얼",
        left=[
            "POSCO WIDE 광양운영그룹 스마트 시설관리 플랫폼 (Smart FMS)",
            "대시보드 · 사업장/설비 · 점검(PM) · 점검일지(17개 건물 웹 운영일보) · 정비관리(접수→D-1→작업허가→최종승인)",
            "자재 · 협력사 · 위험성평가(AI) · 자료실 · AI 분석(GPT 대화) · 계정/서버관리",
            "배포: Render(main 자동) · DB: PostgreSQL · PC 브라우저 + 모바일 QR(로그인 필요)",
            "POSCO WIDE  ·  2026.10 개정",
        ],
        cover=True,
    ),
    SlideSpec(
        "01  시스템 개요 · 접속 · 역할 · 메뉴",
        left=[
            "【시스템】 사업장·건물·설비 기준정보 → PM 점검 → 점검일지(웹) → 정비의뢰·D-1·작업허가·최종승인 → 정비실적, 자재·협력사·위험성평가·자료실·AI 분석",
            "【접속】 로그인 /admin/login (「자동 로그인」 체크 시 다음 접속부터 자동) · 가입 /admin/signup(관리자 승인 후 사용)",
            "【보안】 QR 포함 모든 업무 화면은 로그인한 사용자에게만 제공 — 미로그인 시 로그인 후 원래 화면으로 복귀",
            "【내 계정 /admin/account】 이름·연락처·비밀번호 변경, OpenAI API 키·모델, 비밀번호 확인 후 탈퇴(비활성화)",
            "【역할】 시스템관리자 · 사업장관리자 · 그룹장 · 파트장 · 시설담당자 · 정비섹션 · 운영지원섹션 · 금호시설섹션 · 백운시설섹션 · SDC섹션 · 해수담수섹션 · 협력사 · 외부업체 · 조회전용 (+ 관리자 추가 역할)",
            "【CRUD】 등록(C)·수정(E)·삭제(D) — 역할 기본값 + 계정별 조정",
            "【관리자 전용】 서버관리·계정관리 = 시스템관리자만 · 주요설비 일정·공지사항은 사이드바에서 관리자에게만 표시",
            "【협력사/외부업체】 설비·PM·점검일지·작업허가·정비실적·가로등·자료실 기본 차단, 지정 협력사 건만 처리",
            "【조회전용】 읽기만 가능, 등록·수정·삭제 불가",
            "【내 건물 · 나의 협력사】 계정별 담당 건물·협력사를 지정하면 설비·PM·점검일지·정비관리 화면이 그 범위로 표시",
        ],
        right=[
            "【메뉴 — PC 사이드바 (상단 명칭: 시설관리실 DX 시스템)】",
            "대시보드 /admin/dashboard",
            "주요설비 일정 /admin/schedules · 공지사항 /admin/notices (관리자)",
            "사업장/건물 /admin/sites (사업장별 펼침)",
            "설비관리 /admin/equipment (사업장 → 건물 중첩 펼침)",
            "점검(PM) /admin/pm",
            "점검일지 /admin/inspection-logs2 (구 점검일지2, 구 점검일지1 메뉴는 제거)",
            "정비관리 /admin/maintenance/dashboard — 누르면 대시보드 + 하위메뉴 펼침",
            "  정비접수/승인 /admin/work-orders",
            "  정비 List(D-1) /admin/d1 (협력사별 하위메뉴)",
            "  작업허가/승인 /admin/facility-section",
            "  정비완료 최종승인 /admin/maintenance-final-approvals",
            "  정비실적(변동비) /admin/maintenance-performance",
            "  가로등 /admin/streetlamp/requests",
            "AI 분석 /admin/ai-analysis",
            "위험성평가 /admin/risk-assessment",
            "자재관리 /admin/materials",
            "자료실 /admin/library",
            "협력사 /admin/partners",
            "서버관리 /admin/server · 계정관리 /admin/users (관리자)",
            "※ 메뉴 접근은 역할 기본값 + 계정별 메뉴 권한(개별·일괄 변경)",
            "※ 정비관리 메뉴에 신규 건수 배지 — 새로고침 없이 자동 갱신",
        ],
    ),
    SlideSpec(
        "02  대시보드 · 일정 · 공지",
        left=[
            "【광양운영그룹 대시보드 /admin/dashboard】",
            "PC 모니터 한 화면 구성 · 위젯별 ON/OFF · 순서 조정",
            "KPI: 사업장·건물·설비 수, PM 지연, 정비의뢰·긴급정비·오늘작업 (숫자 = 클릭 시 목록과 동일 기준)",
            "사업장 현황: 등록된 전체 사업장 카드(사진·요약) · 표시 항목(시설/설비/정비의뢰/미해결/완료) 설정 · 사업장명 클릭 → 안내도",
            "설비 상태 현황: 점검(PM) 결과로 정상·주의·고장·정비중 집계 → 클릭 시 해당 설비 목록 /admin/dashboard/equipment-health",
            "전력사용량(탭 전환형): 주택변전소 TR 3대(kWh·A·부하율) · 제철소본부 수전 · 중앙관제실 전일 전력 — 기준일·추세 기간 제목 표시",
            "실시간 접속 현황: 현재 접속 중인 사용자",
            "정비의뢰 현황 · 오늘 일정 · 공지(본문 바로 보기, 카드에서 바로 수정)",
            "【대시보드 설정 — 시스템관리자만】",
            "화면 구성(새 대시보드 / 기존 운영 보드) 선택 · 위젯 표시 · 사업장 순서·아이콘 사진 개별 변경",
        ],
        right=[
            "【주요설비 일정 /admin/schedules】",
            "월 캘린더 등록·삭제 · 이전/다음 달 이동 · 카테고리: 긴급/점검/작업/검수",
            "대시보드 「오늘 일정」 위젯과 연동",
            "【공지사항 /admin/notices】",
            "등록·삭제·수정(삭제 없이 바로 정정) · 카테고리: 긴급/안전/일반",
            "대시보드 공지 카드에서 본문 확인·현재 위치에서 수정",
            "【운영 팁】",
            "· 메뉴 권한이 없으면 /admin/account?error=no_menu 로 안내",
            "· 사이드바 펼침 메뉴는 하나만 열림(다른 메뉴를 열면 기존 펼침이 닫힘)",
            "· 내 계정에서 OpenAI API 키·모델 설정 → AI 분석·위험성평가 공용",
        ],
    ),
    SlideSpec(
        "03  사업장/건물 · 설비관리",
        left=[
            "【사업장/건물 /admin/sites】",
            "메인: 사업장 탭 선택형 사진 그리드 — 마우스 올림만으로 사업장 사진 ↔ 안내도 전환",
            "안내도: 이미지·PDF 업로드, 건물 바로가기(핫스팟) 위치 수정·추가 → 클릭 시 건물 현황",
            "바로가기에 마우스 올리면 건물 첨부 사진 미리보기 · 사진·안내도는 DB 저장(재배포 후 유지)",
            "사이드바: 사업장 펼침 → 건물 목록(가나다·숫자·영문 순) · 메뉴 클릭 시 항상 첫 화면",
            "건물 상세: 대표 사진 · 층·구역 · 도면·표준서 파일 · 「설비관리로 이동」 버튼",
            "공공시설물 상세 현황: 원본 이미지 + 엑셀 일괄 갱신",
        ],
        right=[
            "【설비관리 /admin/equipment】",
            "첫 화면: 실데이터 대시보드(건물별 설비·정비·점검) · 내 건물 범위(접힘형 표시줄)",
            "사이드바: 사업장 → 건물 중첩 펼침, 선택한 사업장·건물만 표시",
            "설비 등록·수정·삭제 · 상세(엑셀 양식) 우측에 설비 사진 등록·표시 · 정비·PM 이력",
            "엑셀 Import/Export(전체·건물별): 시트 코드 열 사용, 건물명 접두로 최종 코드 생성",
            "엑셀 버튼 옆 「마지막 import 일시 · 실행자」 표시 — 누가 설비현황을 덮어썼는지 추적",
            "QR: 개별 PNG /admin/equipment/{id}/qr.png · 건물 일괄 ZIP .../building/{id}/qr-zip",
            "【설비 → 정비의뢰】",
            "내용·우선순위(긴급 우선 정렬)·사진 첨부 · 협력사 지정 필수(미지정 시 접수 불가)",
            "협력사 선택 시 D-1 잠재위험·안전대책을 접수 전에 확인·수정",
            "【설비 QR 현장 /eq/{설비코드}】 (로그인 필요)",
            "설비정보 · 정비의뢰 · PM 점검(고장→정비의뢰) · 점검일지 작성(건물 유형별 1일 입력으로 연결)",
            "PUBLIC_BASE_URL = QR 도메인 고정(배포 URL과 일치 권장)",
        ],
    ),
    SlideSpec(
        "04  점검(PM)",
        left=[
            "【점검(PM) /admin/pm】",
            "첫 화면: 대시보드 — 오늘 점검 현황, 기한 도래·주의·고장 현황을 한눈에",
            "내 건물 설정: 담당 건물만 보이도록 계정별 범위 지정(접힘형 표시줄)",
            "일정 등록 주기: 매일/매주/매월/분기/반기/연간/사용자정의",
            "점검 실행 → 결과 정상/주의/고장 기록 · 고장 시 정비의뢰 연계",
            "지연·기한 필터 · 엑셀 Export /admin/pm/export",
            "설비 상세 · PM 메뉴 양쪽에서 일정 등록 가능",
        ],
        right=[
            "【현장 QR 점검】",
            "/eq/{코드} → PM 점검 → POST /eq/{코드}/pm-inspect",
            "고장 판정 시 정비의뢰 자동 연계 가능",
            "【대시보드 연동】",
            "PM 점검 결과가 메인 대시보드 「설비 상태 현황」(정상·주의·고장·정비중) 집계 기준",
            "정비의뢰 삭제 시 PM '점검진행중'이 남지 않도록 정리",
            "【운영 팁】",
            "· 주의·고장은 다음 점검 전까지 정비의뢰로 처리 상황을 추적",
            "· 내 건물이 비어 있으면 전체 건물 표시 — 새 건물은 내 건물에 추가해야 보임",
        ],
    ),
    SlideSpec(
        "05  점검일지 — 공통 기능",
        left=[
            "【점검일지 /admin/inspection-logs2】 (구 점검일지2 → 점검일지로 통합)",
            "첫 화면: 대시보드형 — 계정별 내 건물만 표시, 건물별 작성 현황",
            "건물 등록 시 건물명으로 일지 유형 자동 판별 → 전용 웹 운영일보 화면",
            "1일 입력·저장 · 일 마감(엑셀 아카이브) · 일/월/년 엑셀 Export",
            "1일 엑셀 다운로드 = 화면 양식 그대로 저장",
            "작성자와 관계없이 권한 있는 사용자 누구나 수정 가능",
            "【엑셀처럼 입력 — 1일 입력 화면】",
            "드래그로 여러 칸 선택 · Ctrl+C 복사 · 엑셀에서 복사한 표 Ctrl+V 붙여넣기",
            "선택 후 Delete/Backspace 일괄 비우기 · 입력·붙여넣기·삭제 후 자동 저장",
            "전일지침: 금일지침 → 다음날 전일지침 자동 이월 + 전일지침 수기 수정 가능",
            "월누계: 매달 1일부터 그날까지 합계(달이 바뀌면 다시 누계) · 입력 즉시 재계산",
            "계량 배율 자동 반영(예: 제철소본부 수전 ×12000, 중앙관제실 INCOMING ×9600)",
        ],
        right=[
            "【고정 문구 수정】 문구 수정 모드에서 화면의 고정 글씨를 직접 클릭해 수정·저장",
            "【과거 일지 자료】 건물별 기존 일지 파일 보관 · 선택/전체 ZIP 다운로드",
            "【특이사항】 하루 한 행 목록(일지 줄바꿈 그대로) · 검색 · 엑셀 출력",
            "【QR 1일 입력】 각 일지 화면에서 QR PNG 다운로드 → 현장 모바일 입력(로그인 필요)",
            "  설비 QR /eq/{코드}/log 도 해당 건물 일지로 연결",
            "【전용 양식이 없는 건물】 일반 상세 화면 + 일/월/년 엑셀 다운로드",
            "【AI 연동】 점검일지 월보(중앙관제실·주택변전소 등) 계산 결과를 AI 분석 근거로 사용",
            "【운영 팁】",
            "· 붙여넣기는 선택 시작 칸(또는 포커스 칸)부터 채워짐",
            "· 연동 항목(예: 전기실 지침 → 전력 금일지침)은 수기 수정값을 덮어쓰지 않음",
        ],
    ),
    SlideSpec(
        "06  점검일지 — 건물 유형 · QR 경로",
        left=[
            "【건물명 → 전용 일지 (관리 화면 /admin/inspection-logs2/{건물ID}/…)】",
            "주택변전소 — 전력·운전일지·변압기온도 · 월보/년보 kWh · QR /hs/{code}/daily",
            "중앙관제실 — 전기 일보(압연/수전 INCOMING) · QR /ccr/{code}/daily",
            "중앙관제실(설비) — 중앙관제실 하위로 통합, 공조·보일러·냉방 가동시간·열량·유량 · QR /ccrf/{code}/daily",
            "제철소본부 — 현장 엑셀 양식 동일 운영일보, NO.1·2 수전 ×12000 · QR /swhq/{code}/daily",
            "제철회관 — 유틸리티·난방 · QR /swhall/{code}/daily",
            "휴먼센터 — 냉·난방, 전력 배율 전 날짜 공통, 전기실 지침 자동 입력 · QR /hcenter/{code}/daily",
            "어울림체육관 — 전기설비 시간별 G/S 2줄 · QR /egym/{code}/daily",
            "백운생활관 — QR /bdorm/{code}/daily",
        ],
        right=[
            "기가타운 — 기계설비·급탕/소화설비 원본 양식, 공조 급기/환기온도 · QR /gtown/{code}/daily",
            "PARK1538 — 전기실 Main VCB(HV-2) 점검표 · QR /p1538/{code}/daily",
            "53서브(53Sub-station) — QR /s53/{code}/daily",
            "백운대 — 전력 일사용량 =(금일-전일)×900 · QR /bdae/{code}/daily",
            "백운쇼핑센터 — 전력 중간+최대+경부하 합산, 중온수 급탕/냉동기 · QR /bshop/{code}/daily",
            "백운아트홀 — QR /bahall/{code}/daily",
            "RIST — 연구동 전력(금일지침 → 연구동 1행 자동기록), 실험동 TR 온도 #1~#3 · QR /rist/{code}/daily",
            "GROUND광양 — QR /ggy/{code}/daily",
            "【QR 공통】 {BASE}=PUBLIC_BASE_URL · {code}=건물 일지 코드",
            "모든 QR 화면은 로그인 필요 — 현장 모바일도 계정으로 로그인 후 입력",
        ],
    ),
    SlideSpec(
        "07  정비관리 — 업무 흐름 · 역할 · 마감시각",
        left=[
            "【흐름】",
            "① 정비의뢰(설비관리·정비접수 「＋ 정비의뢰」·설비 QR) — 협력사 지정 필수",
            "② 정비섹션: 업체 지정 확인 · D-1 승인 (매일 16:00 이전까지) · 반려 시 의뢰자에게 알림",
            "③ 협력사: 예정일 입력 · 작업허가 요청 (16:30 이전까지)",
            "④ 시설섹션 관리감독자: 작업허가 승인 (18:00 이전까지) → 진행중",
            "⑤ 작업완료 요청: 협력사 완료 → 완료확인 대기 / 시설섹션 직원 완료처리 → 즉시 완료",
            "⑥ 최종승인: 협력사 완료 건 확정 → 완료 + 정비이력 기록 (반려 시 진행중으로 복귀)",
            "【상태】 접수 → 배정 → D-1 승인 → 작업허가 요청 → 진행중 → 완료확인 대기 → 완료",
            "【정비의뢰 상세】 작업정보 · 위험등급·잠재위험·안전대책 · 첨부 사진",
            "【사진 첨부】 의뢰자·지정 협력사 계정이 첨부·삭제 · 자동 축소(약 500KB) · 1년 보관, 필요 시 영구보관",
        ],
        right=[
            "【역할별 1페이지 사용 설명서 /admin/maintenance/guide】",
            "정비관리 대시보드 「사용 설명서」 버튼 · 각 화면 도움말의 「전체 사용 설명서 보기」",
            "의뢰자·정비섹션·협력사·시설섹션·최종승인·관리자/조회자 역할별 할 일 + 마감시각",
            "인쇄(A4 1장) 지원 · 권한자는 「수정」으로 화면에서 직접 고친 뒤 저장(기본 설명서로 복원 가능)",
            "【권한 요약】",
            "정비섹션: 정비접수/승인·D-1 승인·반려",
            "협력사: 지정된 건만 — 예정일·작업허가 요청·완료 요청·사진",
            "시설섹션: 작업허가 승인(관리감독자) · 직원 완료처리",
            "최종승인: 협력사·외부업체 계정은 불가",
            "조회전용: 모든 화면 조회만",
        ],
    ),
    SlideSpec(
        "08  정비관리 — 화면별 사용법",
        left=[
            "【정비관리 대시보드 /admin/maintenance/dashboard】 정비 현황 한눈에 · 우측 상단 설정·사용 설명서",
            "【정비접수/승인 /admin/work-orders】",
            "상단 현황 카드(접수·반려·승인대기 등) 클릭 → 해당 목록 · 검색 필터(협력사 '전체' 포함) · 표",
            "「＋ 정비의뢰」(협력사 필수, 지정 시 D-1 안전내용 표시·수정) · D-1 승인/일괄 승인 · 반려 · 엑셀",
            "【정비 List(D-1) /admin/d1】",
            "보드: 전체 · 정비접수 · 오늘 · 내일 · 정비 예정 · 정비완료 (20건 페이지 넘김)",
            "협력사별 사이드바 하위메뉴(포스코와이드 숨김) · 협력사 미선택 화면에서도 건수 클릭 → 목록",
            "【작업허가/승인 /admin/facility-section】",
            "보드: 하루전 · 당일 · 예정 · 전체 · 작업허가/일괄 승인 · 협력사별 위험등급 기본값 · 내 건물",
            "【정비완료 최종승인 /admin/maintenance-final-approvals】",
            "건별 최종승인 · 「선택 항목 일괄 최종승인」 · 반려(진행중 복귀) · 엑셀",
        ],
        right=[
            "【정비실적(변동비) /admin/maintenance-performance】 월별 실적 파일 업로드·다운로드·ZIP",
            "【가로등 /admin/streetlamp/requests】 접수 목록·상태·삭제·Export · QR ZIP · SMS/메일 설정",
            "  QR {BASE}/lamp/{코드} → 유형선택·접수(로그인 필요) · 일일 보고 cron(보안키)",
            "【정비관리 설정 /admin/maintenance/settings】",
            "협력사 접수 정비의뢰 일일 메일: 설정 시각 기준 집계 구간 · 본문 표 · 안내 문구 · 미리보기·테스트 발송",
            "다음날 작업 예정 메일: 하루 전 자동 발송 · 미리보기·테스트 발송",
            "공식 발신 계정 연결(SMTP) · 발송 테스트",
            "【표시 범위】 계정별 나의 협력사 · 내 건물 적용",
            "【화면 전환】 「기존 화면으로」 버튼은 시스템관리자에게만 표시",
            "【운영 팁】",
            "· 긴급 건이 목록 상단에 먼저 표시",
            "· 협력사 미지정 기존 건은 그대로 유지 — 신규 의뢰부터 필수",
        ],
    ),
    SlideSpec(
        "09  자재 · 협력사 · 위험성평가 · 자료실",
        left=[
            "【자재관리 /admin/materials】 (팝업 대신 본 화면, 모니터 한 화면 레이아웃)",
            "품목 · 입고 · 출고 · 그룹 · 재고 로그 · Import/Export",
            "【협력사 /admin/partners】",
            "코드 · 담당자 · 계약종료 · 정비접수·D-1·작업허가 필터 기준",
            "협력사/외부업체 계정은 협력사와 연결 → 지정된 건만 처리",
            "【자료실 /admin/library】 (윈도우 탐색기형)",
            "도면·매뉴얼 등 공용 자료 · 폴더 만들기(상단) · 폴더·파일 한 줄씩 표시",
            "하위메뉴 관리: 우측 상단 설정 버튼(팝업)",
        ],
        right=[
            "【위험성평가 /admin/risk-assessment】",
            "입력 단계: 작업선택 → 평가정보 → 5M1E(인·기계·재료·방법·환경·관리)",
            "AI 모드: 작업명만 입력해도 AI 작업분석으로 공종·작업방법·순서·안전조치를 5M1E에 자동 채움(수정 가능)",
            "AI 평가는 백그라운드 처리 + 진행 확인(긴 응답에도 화면 멈춤 없음)",
            "키 설정·문서학습은 대화상자 · 학습 결과(소분류)는 DB 영구 저장(재배포 후 유지)",
            "결과: 등급 색상 · 요약 KPI · HTML/Excel Export",
            "OpenAI API 키는 내 계정·AI 분석과 공유",
        ],
    ),
    SlideSpec(
        "10  AI 분석 · GPT 대화",
        left=[
            "【AI 분석 /admin/ai-analysis】 채팅형 화면 — 왼쪽 대화 목록 · 오른쪽 대화",
            "GPT 대화 전용(일반질문 집계 모드 삭제) · 대화 계정별 저장 · 다시보기 · 이어서 대화",
            "모델 선택: 최신 GPT-6 포함(추론 모델 호환) · 기본 gpt-4o-mini",
            "답변 중 '데이터 분석 중' 회전 표시",
            "【데이터 범위】",
            "7개 메뉴 DB 원본을 질문 기준으로 추출(사업장·설비·정비·PM·점검일지·자재·가로등 등)",
            "일지·정비·점검 본문(특이사항 등) · 점검일지 월보 계산 결과(중앙관제실·주택변전소 등)",
            "후속 질문은 이번 질문을 우선해 조회 범위 해석",
        ],
        right=[
            "【답변 형식】",
            "표 렌더링 · 그래프 생성(ChatGPT식 데이터 분석)",
            "엑셀로 정리·다운로드(예: 정비완료 상세 목록)",
            "【공유 학습 노트】 계정 간 공유되는 업무 기준·용어 메모 → 답변에 반영",
            "【설정】 API 키·모델: AI 분석 설정 또는 내 계정 /admin/account",
            "API 키 미설정 시 안내 박스 표시",
            "【활용 예】",
            "· 지난달 중앙관제실 전력 사용량 추이를 그래프로",
            "· 이번 주 협력사별 정비완료 건을 엑셀로",
            "· 주의·고장 설비와 최근 점검일지 특이사항 요약",
        ],
    ),
    SlideSpec(
        "11  계정관리 · 서버관리 · 부록",
        left=[
            "【계정관리 /admin/users — 시스템관리자】",
            "탭으로 업무 구분(가입승인·계정 목록 등) · 사이드바에 승인 대기 건수 배지",
            "계정 검색 · 단계적 목록(페이지) · 수정 후 작업하던 페이지 유지",
            "계정 수정과 메뉴 권한 설정 분리 · 메뉴 접근 일괄 변경",
            "역할 직접 추가·삭제(가입 선택 허용 여부 포함) · 섹션 역할(금호·백운·SDC·해수담수·운영지원)",
            "협력사 연결(partner/외부업체) · 계정 삭제 · 가입 계정 초기 비밀번호 보안 강화",
            "엑셀 내보내기: 권한·메뉴 상세 포함",
        ],
        right=[
            "【서버관리 /admin/server — 시스템관리자】",
            "상태 30초 갱신: Render · DB · 디스크 · 메모리 · CPU",
            "백업 ZIP /admin/server/backup.zip (files/ 일지·도면·업로드, excel/ 업무데이터·설비현황)",
            "DB 원본 백업 /admin/server/database-backup.dump — 외부 보관용",
            "접속·활동 로그 검색 /admin/server/access-logs · 검색 조건 기준 엑셀 다운로드",
            "사용 매뉴얼 /admin/server/manual.pptx · 발표자료 /admin/server/presentation.pptx",
            "【QR·URL 부록 — {BASE}=PUBLIC_BASE_URL, 모두 로그인 필요】",
            "설비 /eq/{code} · 설비 일지 /eq/{code}/log · 가로등 /lamp/{code}",
            "점검일지 /{유형}/{code}/daily — hs·ccr·ccrf·swhq·swhall·hcenter·egym·bdorm·gtown·p1538·s53·bdae·bshop·bahall·rist·ggy",
            "【다운로드】 서버관리 → 사용 매뉴얼 PPT 받기 · 문의: 시스템관리자",
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
