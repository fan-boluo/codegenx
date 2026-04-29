@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: 循环 1 到 100
for /l %%i in (1,1,100) do (
    echo 第 %%i 次测试
    curl -X POST http://localhost:8456/api/v1/chat/completions ^
    -H "Authorization: Bearer sk-111111111111111111111" ^
    -H "Content-Type: application/json" ^
    -d "{\"model\":\"qwen-plus\",\"messages\":[{\"role\":\"user\",\"content\":\"测试%%i\"}],\"stream\":false}"

    echo.
)

pause