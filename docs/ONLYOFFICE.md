# OnlyOffice Docs Community 연동 (점검일지)

서버에 파일을 두고, OnlyOffice Document Server(무료 Community)로 열어 편집·저장합니다.

## 구성

```
[브라우저] → FMS (편집 페이지)
           → OnlyOffice Document Server (에디터 UI/수식)
OnlyOffice → FMS `/oo/.../content`  (파일 다운로드)
OnlyOffice → FMS `/oo/.../callback` (저장)
```

## 로컬 실행

### 1) Document Server 기동

Docker Desktop 필요 (메모리 권장 4GB+).

```powershell
docker compose -f docker-compose.onlyoffice.yml up -d
```

첫 기동은 1~3분 걸릴 수 있습니다.  
확인: http://localhost:8080

### 2) FMS 환경변수

`.env` 또는 셸에:

```env
ONLYOFFICE_URL=http://localhost:8080
ONLYOFFICE_JWT_SECRET=smart-fms-onlyoffice-jwt-secret-change-me
ONLYOFFICE_JWT_ENABLED=true
PUBLIC_BASE_URL=http://127.0.0.1:8000
ONLYOFFICE_INTERNAL_FILE_URL=http://host.docker.internal:8000
```

`ONLYOFFICE_JWT_SECRET` 은 `docker-compose.onlyoffice.yml` 의 `JWT_SECRET` 과 **반드시 동일**해야 합니다.

### 3) FMS 실행 후 점검일지 열기

```powershell
run-dev.bat
```

점검일지 → 파일 **열기** → OnlyOffice 편집기  
- 저장: 에디터에서 저장/강제저장 또는 문서 닫기 시 콜백으로 DB에 반영  
- 간단 편집기: `?editor=legacy` 또는 화면의 **간단 편집기** 링크

## Render / 운영

Render 무료/스타터 웹 서비스만으로는 Document Server(리소스 큼)를 같이 올리기 어렵습니다.

권장:
1. FMS는 기존처럼 Render에 배포
2. OnlyOffice Document Server는 별도 VM/VPS (Docker)에 Community 설치
3. 환경변수:
   - `ONLYOFFICE_URL=https://oo.your-domain.com`
   - `PUBLIC_BASE_URL=https://smart-fms.onrender.com`
   - `ONLYOFFICE_INTERNAL_FILE_URL=https://smart-fms.onrender.com`
   - `ONLYOFFICE_JWT_SECRET=...`

방화벽: Document Server → FMS HTTPS 아웃바운드 허용.

## 라이선스

- **Community Edition**: 무료 (AGPL v3), 동시 연결 약 20개
- 상용 기능/대규모 동시접속: Enterprise 유료

## 문제 해결

| 증상 | 확인 |
|------|------|
| API 로드 실패 | `ONLYOFFICE_URL` 브라우저에서 열리는지 |
| 문서 열기 실패 | `ONLYOFFICE_INTERNAL_FILE_URL` 이 Document Server에서 FMS로 도달 가능한지 |
| 저장 안 됨 | JWT 시크릿 일치, 콜백 URL 도달, FMS 로그 `[onlyoffice] saved` |
| JWT 오류 | `ONLYOFFICE_JWT_ENABLED` / `JWT_SECRET` 일치 |

미설정 시(`ONLYOFFICE_URL` 없음) 기존 간단 편집기가 그대로 사용됩니다.
