@echo off
rem ==========================================================================
rem  CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - start without a console window.
rem  Used by the desktop shortcut. Uses pythonw / pyw and falls back to
rem  start-gui.bat when they are not available. --exit-when-idle stops the
rem  hidden server a few minutes after the last window was closed (unless a
rem  job or the installer is still running). Log: data\logs\app.log
rem ==========================================================================
setlocal EnableExtensions
pushd "%~dp0.."
if errorlevel 1 exit /b 1

pyw -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && (
    start "" pyw -3 "src\main.py" --exit-when-idle %*
    popd
    exit /b 0
)
pythonw -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && (
    start "" pythonw "src\main.py" --exit-when-idle %*
    popd
    exit /b 0
)
popd
call "%~dp0start-gui.bat" %*
