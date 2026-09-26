@echo off
setlocal
cd /d "%~dp0"
py -3.11 -c "import winfspy" >nul 2>nul
if errorlevel 1 (
  echo UMDI Windows integration is not installed correctly.
  echo Run INSTALL_WINDOWS.bat first.
  pause
  exit /b 1
)
py -3.11 umdi_windows_drive.py
if errorlevel 1 (
    echo.
    echo UMDI could not mount BioSSD.
    echo Check the terminal output above for the cause.
    pause
    exit /b 1
)

endlocal