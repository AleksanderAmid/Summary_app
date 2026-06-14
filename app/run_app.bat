@echo off
rem Medical Summary app launcher — self-contained, runs from any location.
rem Needs: Python 3.10+ on this machine, and Ollama with the model pulled
rem (ollama pull gemma3:12b-it-q4_K_M). PDF support needs PyMuPDF (optional).
cd /d "%~dp0"

rem Pick a Python: py launcher if present, otherwise python from PATH.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
    echo and check "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

rem Offer PDF support (PyMuPDF) if missing — the app runs without it.
%PY% -c "import fitz" >nul 2>nul
if errorlevel 1 (
    echo PyMuPDF not found - installing it to enable PDF files...
    %PY% -m pip install --quiet pymupdf
)

%PY% backend\server.py
pause
