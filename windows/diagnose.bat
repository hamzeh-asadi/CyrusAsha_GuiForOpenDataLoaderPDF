@echo off
rem ==========================================================================
rem  CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - print an environment report
rem  (Python, Java, virtual environment, installed packages, folders).
rem  Attach the output when you ask for help.
rem ==========================================================================
setlocal EnableExtensions
chcp 65001 >nul
title CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - Diagnose
pushd "%~dp0.."
if errorlevel 1 exit /b 1

call :find_python
if not defined PY (
    echo  Python 3.9 or newer was not found. Run windows\install.bat first.
    popd
    pause
    exit /b 1
)
echo  Python command: %PY%
echo.
%PY% "src\main.py" --diagnose
set "EXITCODE=%ERRORLEVEL%"
echo.
popd
pause
exit /b %EXITCODE%

rem --------------------------------------------------------------------------
rem  :find_python - sets PY to a working Python 3.9+ command (py launcher first)
rem --------------------------------------------------------------------------
:find_python
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3" && goto :eof
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python" && goto :eof
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python3" && goto :eof
goto :eof
