@echo off
call "%~dp0project_python.cmd" -m pytest -p no:cacheprovider %*
exit /b %ERRORLEVEL%
