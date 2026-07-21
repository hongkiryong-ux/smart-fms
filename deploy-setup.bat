@echo off
cd /d "%~dp0"
set PATH=C:\Program Files\Git\bin;%PATH%
where gh >nul 2>&1 || set PATH=%PATH%;C:\Program Files\GitHub CLI\

echo === Smart FMS GitHub + Render 배포 ===
echo.

gh auth status >nul 2>&1
if errorlevel 1 (
  echo [1/3] GitHub 로그인 필요
  echo 브라우저에서 https://github.com/login/device 열고 아래 코드 입력:
  gh auth login -h github.com -p https -w
  pause
)

echo [2/3] GitHub 저장소 생성 및 push...
gh repo create smart-fms --public --source=. --remote=origin --push
if errorlevel 1 (
  echo 저장소가 이미 있으면 push만 시도합니다...
  git remote remove origin 2>nul
  git remote add origin https://github.com/hongkiryong-ux/smart-fms.git
  git push -u origin main
)

echo.
echo [3/3] Render Blueprint 배포
echo 1. https://dashboard.render.com/blueprints 접속
echo 2. New Blueprint Instance
echo 3. GitHub smart-fms 저장소 선택
echo 4. render.yaml 적용 후 Deploy
echo.
echo 완료 후 PUBLIC_BASE_URL, ADMIN_PW 환경변수 설정하세요.
pause
