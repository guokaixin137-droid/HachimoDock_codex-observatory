@echo off
setlocal DisableDelayedExpansion
if not exist "%~dp0runtime\python.exe" (
    echo Extract the entire Windows release ZIP first.
    exit /b 1
)
"%~dp0runtime\python.exe" -I -B -X utf8 "%~dp0manage.py" %*
exit /b %errorlevel%
