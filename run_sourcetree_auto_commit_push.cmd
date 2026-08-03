@echo off
setlocal
title News-Reader - SourceTree Auto Commit and Push

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\sourcetree-auto-commit-push.ps1" -RepositoryPath "%~dp0." %*
set "HOOK_EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%HOOK_EXIT_CODE%"=="0" (
    echo The action stopped without pushing. Review the message above.
) else (
    echo The SourceTree Git action completed successfully.
)
exit /b %HOOK_EXIT_CODE%
