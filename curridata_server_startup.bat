@echo off
setlocal
chcp 65001 > nul

set SERVER_PORT=8000
set APP_MODULE=main:app

echo ============================================
echo   正在執行環境檢查與清理...
echo ============================================

:: 1. 強制關閉所有可能殘留的 uvicorn 和 python 進程
echo [步驟 1] 清理舊的 Python/Uvicorn 進程...
taskkill /f /im uvicorn.exe >nul 2>&1
taskkill /f /im python.exe >nul 2>&1

:: 2. 針對 Port 8000 進行深度清理 (預防萬一)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :%SERVER_PORT% ^| findstr LISTENING') do (
    echo 偵測到埠口 %SERVER_PORT% 被 PID %%a 佔用，正在終止...
    taskkill /F /PID %%a >nul 2>&1
)

:: 3. 啟動 FastAPI (使用完整路徑指令)
echo [步驟 2] 正在啟動 FastAPI 伺服器...
cd /d "%~dp0"
:: 建議在啟動時加上 --reload 方便開發，並確保 host 為 127.0.0.1
start "FastAPI_Server" uvicorn %APP_MODULE% --host 127.0.0.1 --port %SERVER_PORT% --reload

:: 4. 等待並驗證
echo 正在等待伺服器就緒 (5秒)...
timeout /t 5 /nobreak > nul

:: 5. 啟動 Cloudflare Tunnel
echo [步驟 3] 正在連接 Cloudflare Tunnel...
echo --------------------------------------------
echo 💡 若看到 502 錯誤，請檢查 [FastAPI_Server] 視窗是否有噴紅字
echo --------------------------------------------

cloudflared tunnel --url http://127.0.0.1:%SERVER_PORT%

pause