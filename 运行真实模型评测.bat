@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\deepseek_launcher.ps1" -Mode Eval -Projects DataPilot,CiteGuard
set "launcher_exit=%ERRORLEVEL%"
echo.
if not "%launcher_exit%"=="0" echo Live evaluation failed. See the error above.
if "%launcher_exit%"=="0" echo Live evaluation finished. Reports were written to evals\report.live.json inside each project.
pause
endlocal & exit /b %launcher_exit%
