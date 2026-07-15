@echo off
title process_designer
REM process_designer - quick launcher (venv must already exist; run setup_env.bat first)
set "PYEXE=%LOCALAPPDATA%\venvs\process_designer\Scripts\python.exe"
if not exist "%PYEXE%" (
    echo [ERROR] local venv not found: %PYEXE%
    echo Run setup_env.bat first (creates venv + installs deps).
    pause
    exit /b 1
)
echo Starting process_designer ... http://localhost:8540
"%PYEXE%" -m streamlit run "%~dp0app.py" --server.port 8540 --browser.gatherUsageStats false
