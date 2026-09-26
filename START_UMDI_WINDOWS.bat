@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 umdi_app.py
  goto :eof
)
where python >nul 2>nul
if %errorlevel%==0 (
  python umdi_app.py
  goto :eof
)
echo Python 3 was not found. Install Python 3, then run this launcher again.
pause
