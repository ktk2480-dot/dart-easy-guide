@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import PySide6, requests, matplotlib" 2>nul
if errorlevel 1 (
    echo 필요한 프로그램이 없습니다. 아래 명령을 한 번 실행해 주세요.
    echo python -m pip install -r requirements.txt
    pause
    exit /b 1
)
start "" pythonw -m dart_guide
