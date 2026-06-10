@echo off
rem Medical Summary app launcher — uses the project's virtual environment.
cd /d "%~dp0\.."
".venv\Scripts\python.exe" -m app.backend.server
pause
