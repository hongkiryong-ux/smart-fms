@echo off
cd /d "%~dp0"
echo Python 가상환경 생성 및 패키지 설치 중...
python -m venv venv
if errorlevel 1 (
  echo Python 설치를 확인하세요. https://www.python.org/downloads/
  pause
  exit /b 1
)
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
  echo 패키지 설치 실패
  pause
  exit /b 1
)
echo.
echo 설치 완료. run-dev.bat 으로 개발 서버를 시작하세요.
pause
