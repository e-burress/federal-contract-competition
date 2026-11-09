@echo off
setlocal
cd /d "%~dp0"
echo Federal contract competition: quick rebuild
echo.

where uv >nul 2>nul
if not errorlevel 1 (
  uv run run.py quick
  goto done
)

set "PY="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=python"
  )
)

if not defined PY (
  echo Neither uv nor Python 3.11 or newer was found. Nothing was installed.
  echo.
  echo Option 1, install uv. Open PowerShell and type:
  echo     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo Option 2, install Python 3.11 or newer from https://www.python.org/downloads/
  echo     and tick "Add python.exe to PATH" in the installer.
  echo.
  echo Then close this window and double-click this file again.
  goto done
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment in .venv, first time only...
  %PY% -m venv .venv
  if errorlevel 1 goto failed
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" run.py quick
goto done

:failed
echo.
echo Setup failed. See the messages above. Deleting the .venv folder and trying again often helps.

:done
echo.
pause
