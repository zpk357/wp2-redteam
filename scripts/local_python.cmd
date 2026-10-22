@echo off
rem Compatibility alias. project_python.cmd is the single interpreter/dependency entry.
call "%~dp0project_python.cmd" %*
exit /b %ERRORLEVEL%
