@echo off
rem Usage:
rem   startup.bat                     -> start user-service, ai-service, app-service, api-gateway
rem   startup.bat user ai app         -> start selected services only
rem Service port mapping:
rem   user    -> 50051
rem   ai      -> 8002
rem   app     -> 8004  (includes chat endpoints)
rem   gateway -> 8456
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "BACKEND=%ROOT%\backend"
set "PYTHON=%BACKEND%\.venv\Scripts\python.exe"
set "RUN_USER=0"
set "RUN_AI=0"
set "RUN_APP=0"
set "RUN_GATEWAY=0"

if not exist "%PYTHON%" (
    echo Backend Python not found: %PYTHON%
    exit /b 1
)

if "%~1"=="" (
    set "RUN_USER=1"
    set "RUN_AI=1"
    set "RUN_APP=1"
    set "RUN_GATEWAY=1"
) else (
    call :parse_args %*
    if errorlevel 1 exit /b 1
)

echo Starting selected services...

if "%RUN_USER%"=="1" call :start_user_service
if "%RUN_AI%"=="1" call :start_ai_service
if "%RUN_APP%"=="1" call :start_app_service
if "%RUN_GATEWAY%"=="1" call :start_gateway_service

echo Startup check completed.
echo Logs are printed in the service windows that were opened.
echo This launcher window will stay open. Close it manually when you are done.

:hold
timeout /t -1 >nul
goto hold

:parse_args
if "%~1"=="" exit /b 0

if /I "%~1"=="user" (
    set "RUN_USER=1"
) else if /I "%~1"=="ai" (
    set "RUN_AI=1"
) else if /I "%~1"=="app" (
    set "RUN_APP=1"
) else if /I "%~1"=="gateway" (
    set "RUN_GATEWAY=1"
) else (
    echo Unknown service argument: %~1
    echo Usage: startup.bat [user] [ai] [app] [gateway]
    exit /b 1
)

shift
goto parse_args

:is_port_listening
powershell -NoProfile -Command "$conn = Get-NetTCPConnection -LocalPort %~1 -State Listen -ErrorAction SilentlyContinue; if ($conn) { exit 0 } else { exit 1 }"
exit /b %ERRORLEVEL%

:start_user_service
call :is_port_listening 50051
if %ERRORLEVEL% EQU 0 (
    echo [SKIP] user-service is already listening on port 50051.
    exit /b 0
)

start "user-service" powershell -NoExit -ExecutionPolicy Bypass -Command "$Host.UI.RawUI.WindowTitle = 'user-service'; $env:PYTHONPATH='%BACKEND%'; Set-Location '%BACKEND%\services\user-service'; & '%PYTHON%' grpc_server.py"
echo [START] user-service launch command sent on port 50051.
exit /b 0

:start_ai_service
call :is_port_listening 8002
if %ERRORLEVEL% EQU 0 (
    echo [SKIP] ai-service is already listening on port 8002.
    exit /b 0
)

start "ai-service" powershell -NoExit -ExecutionPolicy Bypass -Command "$Host.UI.RawUI.WindowTitle = 'ai-service'; $env:PYTHONPATH='%BACKEND%'; Set-Location '%BACKEND%\services\ai-service'; & '%PYTHON%' -m uvicorn app:app --host 0.0.0.0 --port 8002"
echo [START] ai-service launch command sent on port 8002.
exit /b 0

:start_app_service
call :is_port_listening 8004
if %ERRORLEVEL% EQU 0 (
    echo [SKIP] app-service is already listening on port 8004.
    exit /b 0
)

start "app-service" powershell -NoExit -ExecutionPolicy Bypass -Command "$Host.UI.RawUI.WindowTitle = 'app-service'; $env:PYTHONPATH='%BACKEND%'; Set-Location '%BACKEND%\services\app-service'; & '%PYTHON%' -m uvicorn app:app --host 0.0.0.0 --port 8004"
echo [START] app-service launch command sent on port 8004.
exit /b 0

:start_gateway_service
call :is_port_listening 8456
if %ERRORLEVEL% EQU 0 (
    echo [SKIP] api-gateway is already listening on port 8456.
    exit /b 0
)

start "api-gateway" powershell -NoExit -ExecutionPolicy Bypass -Command "$Host.UI.RawUI.WindowTitle = 'api-gateway'; $env:PYTHONPATH='%BACKEND%'; Set-Location '%BACKEND%\api-gateway'; & '%PYTHON%' run.py --env local --host 0.0.0.0 --port 8456"
echo [START] api-gateway launch command sent on port 8456.
exit /b 0
