@echo off
REM Build HOPE-Labs.exe on a Windows computer.
REM
REM Needs Python 3.10 or newer from python.org (tick "Add python.exe to PATH" when installing).
REM Double-click this file, or run it from a command prompt. It installs what it needs, checks the
REM launcher starts, then builds dist\HOPE-Labs\HOPE-Labs.exe.
setlocal
cd /d "%~dp0.."

set PY=python
where py >nul 2>&1
if %errorlevel%==0 set PY=py -3

echo.
echo === installing what the build needs ===
%PY% -m pip install --upgrade pip paramiko pyinstaller pillow
if errorlevel 1 goto fail

REM The icon ships with this folder; it is redrawn only if it went missing.
if not exist docs\brand\hopelabs.ico %PY% docs\brand\make_icon.py

echo.
echo === checking the launcher's own parts ===
set PYTHONPATH=%CD%
%PY% -m hope_labs --selftest
if errorlevel 1 goto fail

echo.
echo === filling the version resource in from the license ===
%PY% packaging\win_version.py
if errorlevel 1 goto fail

echo.
echo === building the executable ===
REM --onedir, not --onefile: a one-file build unpacks a runtime at startup, which is what antivirus
REM heuristics flag. The folder has to travel whole, so send the folder, or a zip of it.
%PY% -m PyInstaller --noconfirm --onedir --windowed ^
  --name HOPE-Labs --paths . ^
  --icon docs\brand\hopelabs.ico ^
  --version-file packaging\win_version.built.txt ^
  --hidden-import paramiko ^
  --add-data "hope_labs/web;hope_labs/web" ^
  --add-data "hope_labs/setup/bootstrap.sh;hope_labs/setup" ^
  --add-data "LICENSE;." ^
  --add-data "THIRD_PARTY_NOTICES.md;." ^
  packaging\launcher.py
if errorlevel 1 goto fail

echo.
echo === done ===
echo   dist\HOPE-Labs\HOPE-Labs.exe
echo.
echo Send the whole dist\HOPE-Labs folder, or a zip of it. It is unsigned, so the first
echo run shows "Windows protected your PC": More info -^> Run anyway.
pause
exit /b 0

:fail
echo.
echo The build stopped. The lines above say where.
pause
exit /b 1
