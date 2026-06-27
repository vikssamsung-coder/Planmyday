@echo off
REM ============================================================
REM  Plan My Day - desktop launcher (Windows)
REM  Double-click this file to start the app.
REM  On first run it creates  D:\Sarthi - Plan My Day\
REM ============================================================

cd /d "%~dp0"

REM install/update dependencies (quiet); needs Python 3.10+ on PATH
py -m pip install -q -r requirements.txt

REM open the browser after a short delay
start "" /b cmd /c "timeout /t 4 >nul & start http://localhost:8501"

py -m streamlit run app.py --server.port 8501

pause
