@echo off
setlocal
set "BRAIN_CLI_VERSION=3.2.1"
set "BRAIN_INSTALL_REF=v0.67.2"

set "SELF_DIR=%~dp0"
set "SELF_PATH=%~f0"
set "DISTRIBUTION_ROOT=%SELF_DIR%..\lib\brain-cli\%BRAIN_CLI_VERSION%"
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
%PYTHON_COMMAND% -m _local_cli.main %*
exit /b %ERRORLEVEL%
