@echo off
cd /d "%~dp0"
call "%~dp0setup_environment.bat"
if errorlevel 1 (
    echo Dashboard startup cancelled because environment setup failed.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" main.py dashboard --open
pause
