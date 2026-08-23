@echo off
call "%~dp0project_python.cmd" -m ruff check --no-cache %*
exit /b %ERRORLEVEL%
