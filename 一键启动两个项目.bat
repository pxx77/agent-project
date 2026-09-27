@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\deepseek_launcher.ps1" -Mode Start -Projects DataPilot,CiteGuard
set "launcher_exit=%ERRORLEVEL%"
if not "%launcher_exit%"=="0" (
    echo.
    echo One-click startup failed. See the error above.
    pause
)
endlocal & exit /b %launcher_exit%
