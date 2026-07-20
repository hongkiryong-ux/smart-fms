@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  echo 가상환경이 없습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
echo POSCO WIDE Smart FMS - 로컬 개발 서버 시작
echo http://127.0.0.1:8000
echo 관리자: http://127.0.0.1:8000/admin/login
echo.
venv\Scripts\uvicorn.exe main:app --reload --host 127.0.0.1 --port 8000
