# POSCO WIDE Smart FMS

통합 시설관리 플랫폼 (Facility Management System)

## 주요 기능

- **Dashboard** — KPI, 작업/점검 현황
- **시설 계층** — 사업장 → 건물 → 층 → 구역 → 설비
- **설비관리** — 템플릿 기반 30초 등록, QR/NFC 모바일 조회
- **예방정비(PM)** — 설비별 점검 스케줄
- **CMMS** — 작업 접수/배정/완료/검수
- **D-1 작업** — JSA, TBM, 작업허가 워크플로
- **재고/협력사** — 재고관리, 협력사 Portal

## 로컬 개발

```powershell
setup.bat          # 최초 1회
run-dev.bat        # 서버 시작
```

| URL | 설명 |
|-----|------|
| http://127.0.0.1:8000/admin/login | 관리자 로그인 |
| http://127.0.0.1:8000/admin/dashboard | Dashboard |
| http://127.0.0.1:8000/eq/AHU-001 | QR 설비 조회 |

**기본 계정:** `admin` / `password123`

## Render 배포

1. GitHub 저장소 연결
2. **New Web Service** → Python
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Health Check: `/health`
6. PostgreSQL 추가 후 `DATABASE_URL` 연결
7. 환경변수: `APP_SECRET_KEY`, `ADMIN_PW`, `RENDER=true`, `COOKIE_HTTPS_ONLY=true`

또는 `render.yaml` Blueprint로 일괄 생성.

## QR 코드 생성

```powershell
venv\Scripts\python.exe qr_generate.py
```

## 기술 스택

- FastAPI + SQLAlchemy (async)
- PostgreSQL (운영) / SQLite (로컬)
- Jinja2 + PWA
- Render
