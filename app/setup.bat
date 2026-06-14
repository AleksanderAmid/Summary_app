@echo off
rem One-time setup for a new device: installs Python, Ollama, the Gemma
rem model and PyMuPDF as needed. Safe to re-run. Then use run_app.bat.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
