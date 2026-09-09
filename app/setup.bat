@echo off
rem One-time setup for a new device: installs Python, Ollama, the Gemma
rem model and PyMuPDF as needed. Safe to re-run. Then use run_app.bat.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "SMARTDOC_SETUP_EXIT=%ERRORLEVEL%"
if not "%SMARTDOC_SETUP_EXIT%"=="0" echo Setup did not finish. Check the error above before opening SmartDoc.
pause
exit /b %SMARTDOC_SETUP_EXIT%
