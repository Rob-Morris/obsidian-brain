@echo off
setlocal
set "BRAIN_CLI_VERSION=4.0.5"
set "BRAIN_INSTALL_REF=v0.70.6"

set "SELF_DIR=%~dp0"
set "SELF_PATH=%~f0"
set "DISTRIBUTION_ROOT=%SELF_DIR%..\lib\brain-cli\%BRAIN_CLI_VERSION%"
if "%~1"=="mcp" if "%~2"=="serve" goto mcp_serve
if defined BRAIN_CLI_BUNDLE (
    set "DISTRIBUTION_ROOT=%BRAIN_CLI_BUNDLE%"
) else if exist "%SELF_DIR%_local_cli\main.py" (
    if exist "%SELF_DIR%..\src\brain-core\VERSION" set "DISTRIBUTION_ROOT=%SELF_DIR%.."
)
for %%I in ("%DISTRIBUTION_ROOT%") do set "DISTRIBUTION_ROOT=%%~fI"

if not exist "%DISTRIBUTION_ROOT%\cli\_local_cli\main.py" (
    >&2 echo brain: CLI distribution %DISTRIBUTION_ROOT% is missing or incomplete
    exit /b 4
)
if not exist "%DISTRIBUTION_ROOT%\src\brain-core\VERSION" (
    >&2 echo brain: CLI distribution %DISTRIBUTION_ROOT% is missing or incomplete
    exit /b 4
)

set "PYTHON_COMMAND="
where python3.12.exe >nul 2>nul && python3.12.exe -c "import sys; raise SystemExit(sys.version_info < (3, 12))" >nul 2>nul && set "PYTHON_COMMAND=python3.12.exe"
if not defined PYTHON_COMMAND where python.exe >nul 2>nul && python.exe -c "import sys; raise SystemExit(sys.version_info < (3, 12))" >nul 2>nul && set "PYTHON_COMMAND=python.exe"
if not defined PYTHON_COMMAND where py.exe >nul 2>nul && py.exe -3.12 -c "import sys; raise SystemExit(sys.version_info < (3, 12))" >nul 2>nul && set "PYTHON_COMMAND=py.exe -3.12"
if not defined PYTHON_COMMAND (
    >&2 echo brain: Python 3.12 or newer is required
    exit /b 4
)

set "BRAIN_CLI_BINARY=%SELF_PATH%"
set "BRAIN_CLI_DISTRIBUTION_ROOT=%DISTRIBUTION_ROOT%"
if defined PYTHONPATH (
    set "PYTHONPATH=%DISTRIBUTION_ROOT%\cli;%DISTRIBUTION_ROOT%\src\brain-core\scripts;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%DISTRIBUTION_ROOT%\cli;%DISTRIBUTION_ROOT%\src\brain-core\scripts"
)
%PYTHON_COMMAND% -B -m _local_cli.main %*
exit /b %ERRORLEVEL%

:mcp_serve
if not exist "%DISTRIBUTION_ROOT%\.bootstrap-python" (
    >&2 echo brain: installed MCP bootstrap is missing; reinstall the CLI with --bootstrap-python
    exit /b 4
)
set /p BOOTSTRAP_PYTHON=<"%DISTRIBUTION_ROOT%\.bootstrap-python"
if not exist "%BOOTSTRAP_PYTHON%" (
    >&2 echo brain: recorded bootstrap Python is unavailable; reinstall the CLI with --bootstrap-python
    exit /b 4
)
"%BOOTSTRAP_PYTHON%" -I -B "%DISTRIBUTION_ROOT%\cli\_mcp_stdio.py" %*
exit /b %ERRORLEVEL%
