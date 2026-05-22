@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Q币充值 - 本地服务
echo ========================================
echo   本地充值网站启动
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未安装 Python，请先安装 Python 3.8 或以上
    pause
    exit /b 1
)

echo 正在启动，请勿关闭本窗口...
echo 启动成功后，浏览器打开：
echo   http://localhost:8765
echo   或 http://127.0.0.1:8765
echo.
echo 关闭本窗口 = 网站停止（会出现连接被拒绝）
echo ========================================
echo.

python server.py
if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请把上面红色报错发出来
)
pause
