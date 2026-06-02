@echo off
chcp 65001 >nul 2>&1
title report-skill setup
cd /d "%~dp0"

echo.
echo ===============================================
echo   report-skill - one-shot installer
echo ===============================================
echo.
echo   You will be asked 3 things (Korean prompts):
echo     1) Server URL  (e.g. http://10.0.5.42:3000/api)
echo     2) Email       (default: bot@reportskill.app)
echo     3) Password
echo.
echo   What this does:
echo     - copies report-skill.exe to %%LOCALAPPDATA%%\report-skill\bin\
echo     - adds it to your user PATH
echo     - copies Claude Code slash commands to %%USERPROFILE%%\.claude\skills\
echo     - writes .env and runs 'report-skill ping'
echo.
echo ===============================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-standalone.ps1" %*
set EC=%ERRORLEVEL%

echo.
if "%EC%"=="0" (
    echo ===============================================
    echo   DONE. Open a new terminal and try:
    echo       report-skill ping
    echo   Or in Claude Code:  /report-write
    echo ===============================================
) else (
    echo ===============================================
    echo   FAILED  (exit %EC%)
    echo   Read the messages above and retry.
    echo ===============================================
)
echo.
pause
