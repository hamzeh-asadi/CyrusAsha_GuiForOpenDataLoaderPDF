@echo off
rem ==========================================================================
rem  CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - console installer
rem
rem  Installs into the install folder (the "data" folder of the project unless
rem  you choose another one):
rem    * a private Python virtual environment (venv)
rem    * opendataloader-pdf from PyPI
rem    * a portable Java runtime (java) if no Java 11+ is installed
rem    * optionally the hybrid AI extra (docling, PyTorch, EasyOCR)
rem  It asks for the install folder and for the folder of the AI models that
rem  the hybrid mode downloads; press Enter to keep the folders shown.
rem
rem  Usage:  install.bat                          standard installation
rem          install.bat --hybrid                 also install the hybrid AI extra
rem          install.bat --install-dir D:\ODL     install folder (no questions)
rem          install.bat --models-dir E:\models   models folder (no questions)
rem
rem  The same installation can be done from the control center (Setup page),
rem  which can also MOVE an existing installation to another folder.
rem ==========================================================================
setlocal EnableExtensions
chcp 65001 >nul
title CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - Installer
pushd "%~dp0.."
if errorlevel 1 goto :bad_dir

echo.
echo  =====================================================
echo    CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - Installer
echo    Code . Intelligence . Order
echo  =====================================================
echo.

if not exist "src\main.py" goto :bad_layout

call :find_python
if not defined PY call :offer_python
if not defined PY goto :no_python

for /f "delims=" %%v in ('%PY% -c "import platform; print(platform.python_version())"') do set "PYVER=%%v"
echo  Python found: %PY%  (%PYVER%)
echo.

set "LOCATION_ARGS="
echo %* | find /i "-dir" >nul
if errorlevel 1 call :ask_folders

%PY% "src\main.py" --setup full %LOCATION_ARGS% %*
if errorlevel 1 goto :failed

echo.
echo  Installation finished.
choice /C YN /M " Create a desktop shortcut"
if not errorlevel 2 powershell -NoProfile -ExecutionPolicy Bypass -File "windows\install-shortcut.ps1"
choice /C YN /M " Start the control center now"
if not errorlevel 2 start "" "%CD%\Start.bat"
popd
exit /b 0

:failed
echo.
echo  [!] The installation did not finish. See the messages above, or start
echo      the control center (Start.bat) and open "Setup and updates".
echo.
popd
pause
exit /b 1

:no_python
echo.
echo  [!] Python 3.9+ is required. Install Python 3.11 or newer from
echo      https://www.python.org/downloads/  (tick "Add python.exe to PATH")
echo      and run install.bat again.
echo.
popd
pause
exit /b 1

:bad_layout
echo  This installer must stay inside the project folder.
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
rem  :ask_folders - asks for the install and models folders (Enter = keep)
rem --------------------------------------------------------------------------
:ask_folders
echo  Where should opendataloader-pdf be installed?
%PY% "src\main.py" --show-locations
echo.
echo  The install folder holds the Python environment, Java and the download
echo  cache (several GB with --hybrid). The models folder holds the AI models
echo  that the hybrid mode downloads (several GB). A short folder without
echo  spaces or non-English characters is best, for example D:\ODL
echo  Press Enter to keep a folder.
echo.
set "NEW_INSTALL="
set /p "NEW_INSTALL= Install folder: "
if defined NEW_INSTALL set "NEW_INSTALL=%NEW_INSTALL:"=%"
if defined NEW_INSTALL set LOCATION_ARGS=--install-dir "%NEW_INSTALL%"
set "NEW_MODELS="
set /p "NEW_MODELS= Models folder : "
if defined NEW_MODELS set "NEW_MODELS=%NEW_MODELS:"=%"
if defined NEW_MODELS set LOCATION_ARGS=%LOCATION_ARGS% --models-dir "%NEW_MODELS%"
echo.
goto :eof

rem --------------------------------------------------------------------------
rem  :find_python - sets PY to a working Python 3.9+ command (py launcher first)
rem --------------------------------------------------------------------------
:find_python
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3" && goto :eof
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python" && goto :eof
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python3" && goto :eof
goto :eof

rem --------------------------------------------------------------------------
rem  :offer_python - offers to install Python 3.13 with winget
rem --------------------------------------------------------------------------
:offer_python
where winget >nul 2>nul || goto :eof
echo  Python was not found.
choice /C YN /M " Install Python 3.13 with winget now"
if errorlevel 2 goto :eof
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
rem winget does not refresh PATH for this window - try the default location first
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe"" && goto :eof
call :find_python
goto :eof
