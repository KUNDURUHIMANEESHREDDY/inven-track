@echo off
setlocal
cd /d "%~dp0"
echo Starting InvenTrack Python backend...
py -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo Installing backend dependencies...
    py -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
)
echo Open http://127.0.0.1:8000 in your browser.
py -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
pause
