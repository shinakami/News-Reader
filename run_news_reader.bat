@echo off
cd /d "%~dp0"
set "NEWS_READER_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%NEWS_READER_PYTHON%" set "NEWS_READER_PYTHON=python"
"%NEWS_READER_PYTHON%" main.py news --links
pause
