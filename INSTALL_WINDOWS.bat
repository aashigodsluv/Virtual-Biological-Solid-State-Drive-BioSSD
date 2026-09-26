@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo UMDI Software Stack - Windows Host Integration Installer
echo ============================================================
echo.

set "WINFSP_DIR="
set "WINFSP_DLL="

call :detect_winfsp
if defined WINFSP_DLL goto winfsp_ok

echo [1/3] WinFsp was not detected. Installing WinFsp...
where winget >nul 2>nul
if errorlevel 1 goto no_winget
winget install --id WinFsp.WinFsp -e --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto winfsp_install_failed

rem Detect again from registry and all standard locations. No reboot loop.
call :detect_winfsp
if defined WINFSP_DLL goto winfsp_ok

echo.
echo WinFsp installer returned successfully, but UMDI still cannot locate winfsp-x64.dll.
echo The installer will NOT ask you to reboot repeatedly.
echo.
echo Checked the WinFsp registry keys and standard installation folders.
echo Please open Windows Settings ^> Apps and confirm that WinFsp is listed.
echo Then report the output of this command:
echo   reg query "HKLM\SOFTWARE\WOW6432Node\WinFsp" /v InstallDir
echo.
pause
exit /b 1

:winfsp_ok
echo [1/3] WinFsp: OK
echo       Directory: %WINFSP_DIR%
echo       DLL:       %WINFSP_DLL%

rem WinFSPy source/build helpers understand this variable if it is ever needed.
set "WINFSP_LIBRARY_PATH=%WINFSP_DIR%"

echo.
echo [2/3] Checking Python 3.11 x64...
py -3.11 -c "import sys; assert sys.maxsize ^> 2**32" >nul 2>nul
if not errorlevel 1 goto py311_ok
where winget >nul 2>nul
if errorlevel 1 goto no_py311
echo Installing Python 3.11 x64 for the Virtual BioSSD Windows integration...
winget install --id Python.Python.3.11 -e --architecture x64 --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto no_py311
py -3.11 -c "import sys; assert sys.maxsize ^> 2**32" >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 was installed, but the Python launcher has not refreshed yet.
  echo Close this window and run INSTALL_WINDOWS.bat once more.
  pause
  exit /b 1
)

:py311_ok
echo Python 3.11 x64: OK

echo.
echo [3/3] Installing WinFSPy 0.8.4 for Python 3.11...
py -3.11 -m pip install --upgrade pip
py -3.11 -m pip install --only-binary=:all: winfspy==0.8.4
if errorlevel 1 (
  echo.
  echo The prebuilt WinFSPy wheel could not be installed.
  echo Your existing Python installations were not replaced.
  pause
  exit /b 1
)
py -3.11 -c "import winfspy; print('WinFSPy import: OK')"
if errorlevel 1 (
  echo WinFSPy installed but failed its import check.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo UMDI Windows host integration is ready.
echo Run START_BIOSSD.bat to mount BioSSD using an available drive letter.
echo ============================================================
pause
exit /b 0

:detect_winfsp
set "WINFSP_DIR="
set "WINFSP_DLL="

rem On x64 Windows WinFsp normally records InstallDir under WOW6432Node.
for %%K in ("HKLM\SOFTWARE\WOW6432Node\WinFsp" "HKLM\SOFTWARE\WinFsp") do (
  for /f "tokens=2,*" %%A in ('reg query %%K /v InstallDir 2^>nul ^| find /i "InstallDir"') do (
    set "CANDIDATE=%%B"
    if defined CANDIDATE (
      if exist "!CANDIDATE!\bin\winfsp-x64.dll" (
        set "WINFSP_DIR=!CANDIDATE!"
        set "WINFSP_DLL=!CANDIDATE!\bin\winfsp-x64.dll"
        goto :eof
      )
    )
  )
)

rem Standard locations. Current x64 WinFsp commonly installs under Program Files (x86).
if defined ProgramFiles(x86) if exist "%ProgramFiles(x86)%\WinFsp\bin\winfsp-x64.dll" (
  set "WINFSP_DIR=%ProgramFiles(x86)%\WinFsp"
  set "WINFSP_DLL=%ProgramFiles(x86)%\WinFsp\bin\winfsp-x64.dll"
  goto :eof
)
if defined ProgramFiles if exist "%ProgramFiles%\WinFsp\bin\winfsp-x64.dll" (
  set "WINFSP_DIR=%ProgramFiles%\WinFsp"
  set "WINFSP_DLL=%ProgramFiles%\WinFsp\bin\winfsp-x64.dll"
  goto :eof
)
if exist "C:\Program Files (x86)\WinFsp\bin\winfsp-x64.dll" (
  set "WINFSP_DIR=C:\Program Files (x86)\WinFsp"
  set "WINFSP_DLL=C:\Program Files (x86)\WinFsp\bin\winfsp-x64.dll"
  goto :eof
)
if exist "C:\Program Files\WinFsp\bin\winfsp-x64.dll" (
  set "WINFSP_DIR=C:\Program Files\WinFsp"
  set "WINFSP_DLL=C:\Program Files\WinFsp\bin\winfsp-x64.dll"
)
goto :eof

:no_winget
echo WinFsp is required and Windows Package Manager was not found.
echo Install WinFsp from its official installer, then rerun this file.
pause
exit /b 1

:winfsp_install_failed
echo WinFsp installation failed. UMDI made no further changes.
pause
exit /b 1

:no_py311
echo Python 3.11 x64 is required for the current Windows drive adapter.
echo Automatic installation was unavailable.
pause
exit /b 1
