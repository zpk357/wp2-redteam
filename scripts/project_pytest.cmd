@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
if not defined PYTEST_BASETEMP set "PYTEST_BASETEMP=%PROJECT_ROOT%\.tmp\pytest-%RANDOM%-%RANDOM%-%RANDOM%"
if not exist "%PYTEST_BASETEMP%" mkdir "%PYTEST_BASETEMP%"
call "%~dp0project_python.cmd" -m pytest -p no:cacheprovider --basetemp "%PYTEST_BASETEMP%" %*
exit /b %ERRORLEVEL%
