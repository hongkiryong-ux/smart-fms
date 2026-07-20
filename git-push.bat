@echo off
cd /d "%~dp0"
set GIT="C:\Program Files\Git\bin\git.exe"
if not exist %GIT% set GIT=git

echo Smart FMS - GitHub Push
%GIT% add -A
%GIT% status
set /p MSG="커밋 메시지 (Enter=Update): "
if "%MSG%"=="" set MSG=Update Smart FMS
%GIT% commit -m "%MSG%"
%GIT% push origin main
pause
