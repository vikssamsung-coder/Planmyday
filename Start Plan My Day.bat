@echo off
REM ============================================================
REM  Plan My Day - desktop launcher (Windows)
REM  Installs dependencies only when requirements.txt has changed
REM  (e.g. after an in-app update), otherwise launches fast.
REM ============================================================
cd /d "%~dp0"

REM Reinstall only if requirements.txt changed since last successful install.
py -c "import hashlib,os,sys;h=hashlib.sha256(open('requirements.txt','rb').read()).hexdigest();s=(open('.req_installed.sha').read().strip() if os.path.exists('.req_installed.sha') else '');sys.exit(0 if h==s else 1)"
if errorlevel 1 (
  echo Requirements changed - installing/updating dependencies...
  py -m pip install -r requirements.txt
  if not errorlevel 1 py -c "import hashlib;open('.req_installed.sha','w').write(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())"
) else (
  echo Dependencies up to date.
)

start "" /b cmd /c "timeout /t 4 >nul & start http://localhost:8501"
py -m streamlit run app.py --server.port 8501
pause
