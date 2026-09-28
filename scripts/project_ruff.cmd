@echo off
set "RUFF_EXE=%~dp0..\.venv\Scripts\ruff.exe"
if not exist "%RUFF_EXE%" set "RUFF_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Scripts\ruff.exe"
if not exist "%RUFF_EXE%" (
  echo Ruff executable not found. Install the project's dev dependency or set up .venv. 1>&2
  exit /b 2
)
set "RUFF_ARGS=%*"
if /I "%~1"=="check" set "RUFF_ARGS=%RUFF_ARGS:~6%"
"%RUFF_EXE%" check --no-cache %RUFF_ARGS%
exit /b %ERRORLEVEL%
