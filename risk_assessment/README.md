# P-WIDE 위험성평가 (웹)

원본 위치: `C:\Users\USER\Desktop\1. p-wide-risk-assessment`

원본 `app/` 패키지(LocalAssessmentEngine, WorkTypeLookup, risk_form, report_exporter 등)를
그대로 사용해 Smart FMS `/admin/risk-assessment`에서 동작합니다.

- 로컬 모드: API 없이 JSA·법령 인덱스 평가 (원본과 동일)
- AI 모드: `OPENAI_API_KEY` 설정 시 (웹 로그인/Playwright 제외)
- `LAW_WEB_SEARCH` 기본값 `0` (Render 속도·안정성)
