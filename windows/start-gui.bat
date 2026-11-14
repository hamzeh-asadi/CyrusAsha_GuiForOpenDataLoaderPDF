@echo off
rem ==========================================================================
rem  CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - start the control center (with console).
rem  Usage:  start-gui.bat [--browser] [--port 8765] [--no-browser] ...
rem  All arguments are passed to src\main.py (see README.md).
rem ==========================================================================
setlocal EnableExtensions
chcp 65001 >nul
title CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - Control Center
pushd "%~dp0.."
if errorlevel 1 goto :bad_dir

echo.
echo  =====================================================
echo    CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - Control Center
echo    Code . Intelligence . Order
echo  =====================================================
echo.

if not exist "src\main.py" goto :bad_layout

call :find_python
if not defined PY goto :no_python

echo  Starting... the control center opens automatically.
echo  Keep this window open while you work. Press Ctrl+C to stop.
echo.
%PY% "src\main.py" %*
set "EXITCODE=%ERRORLEVEL%"
popd
if not "%EXITCODE%"=="0" pause
exit /b %EXITCODE%

:no_python
echo  Python 3.9 or newer was not found on this computer.
echo.
echo  Option 1: run windows\install.bat - it can install Python with winget.
echo  Option 2: install Python 3.11+ from https://www.python.org/downloads/
echo            (tick "Add python.exe to PATH") and run this file again.
echo.
popd
pause
exit /b 1

:bad_layout
echo  This launcher must stay inside the project folder.
echo  Expected file not found:
echo      %CD%\src\main.py
echo.
popd
pause
exit /b 1

:bad_dir
echo  Could not open the project folder: %~dp0..
echo.
pause
exit /b 1

rem --------------------------------------------------------------------------
rem  :find_python - sets PY to a working Python 3.9+ command (py launcher first)
rem --------------------------------------------------------------------------
:find_python
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3" && goto :eof
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python" && goto :eof
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python3" && goto :eof
goto :eof
