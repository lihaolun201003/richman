@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   Richman 大富翁 - 启动器
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 .venv\Scripts\python.exe
    echo.
    echo 请先在本目录执行以下命令创建环境并安装依赖：
    echo.
    echo     py -3.10 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    echo 本启动器不会自动下载任何内容。
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py %*
if errorlevel 1 (
    echo.
    echo [提示] 程序以非 0 状态码退出，请查看上方错误信息。
    pause
)
endlocal
