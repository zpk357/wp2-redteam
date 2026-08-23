@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE="

if defined TRACE_G_PYTHON if exist "%TRACE_G_PYTHON%" (
    set "PYTHON_EXE=%TRACE_G_PYTHON%"
)
if not defined PYTHON_EXE if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
)
if not defined PYTHON_EXE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

if not defined PYTHON_EXE (
    echo No supported project Python was found. 1>&2
    echo Set TRACE_G_PYTHON to a Python 3.11-3.14 executable. 1>&2
    exit /b 2
)

"%PYTHON_EXE%" -c "import sys; raise SystemExit(sys.version_info[:2] not in [(3, minor) for minor in range(11, 15)])"
if errorlevel 1 (
    echo Project Python must be version 3.11 through 3.14: %PYTHON_EXE% 1>&2
    exit /b 2
)

set "PYTHONPATH=%PROJECT_ROOT%\.deps;%PROJECT_ROOT%\.deps\win32;%PROJECT_ROOT%\.deps\win32\lib;%PROJECT_ROOT%\src;%PROJECT_ROOT%\agent_image;%PYTHONPATH%"
set "PATH=%PROJECT_ROOT%\.deps\pywin32_system32;%PATH%"
"%PYTHON_EXE%" %*
exit /b %ERRORLEVEL%
