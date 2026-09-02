@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "LAUNCHER=%PROJECT_ROOT%scripts\start_seamtech_search.ps1"
echo Starting SEAMTECH Search...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo SEAMTECH Search failed with exit code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%