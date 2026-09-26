@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Richman - City of Light
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" goto no_venv

".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto failed
goto end

:no_venv
echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
echo.
echo Please run the following commands in this folder first:
echo.
echo     py -3.10 -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo This launcher does not download anything by itself.
pause
exit /b 1

:failed
echo.
echo [ERROR] The game exited with a non-zero code. See the message above.
pause
exit /b 1

:end
endlocal
