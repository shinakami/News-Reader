@echo off
setlocal
cd /d "%~dp0"

set "NEWS_READER_VENV=%~dp0.venv"
set "NEWS_READER_PYTHON=%NEWS_READER_VENV%\Scripts\python.exe"

if not exist "%NEWS_READER_PYTHON%" (
    echo [Setup] Virtual environment not found. Creating .venv...

    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv "%NEWS_READER_VENV%"
    ) else (
        where python >nul 2>&1
        if errorlevel 1 (
            echo [Error] Python 3 was not found. Please install Python and try again.
            exit /b 1
        )
        python -m venv "%NEWS_READER_VENV%"
    )

    if errorlevel 1 (
        echo [Error] Failed to create the virtual environment.
        exit /b 1
    )
)

if not exist "%NEWS_READER_PYTHON%" (
    echo [Error] Virtual environment Python was not created correctly.
    exit /b 1
)

"%NEWS_READER_PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [Setup] pip not found. Installing pip...
    "%NEWS_READER_PYTHON%" -m ensurepip --upgrade
    if errorlevel 1 (
        echo [Error] Failed to install pip in the virtual environment.
        exit /b 1
    )
)

if exist "%~dp0requirements.txt" (
    echo [Setup] Checking required packages...
    "%NEWS_READER_PYTHON%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo [Error] Failed to install the required packages.
        exit /b 1
    )
)

echo [Setup] Virtual environment is ready.
exit /b 0
