@echo off
rem Usage:
rem   shutdown.bat                    -> stop user-service, ai-service, app-service, chat-service, api-gateway
rem   shutdown.bat user ai app chat   -> stop selected services only
rem Service port mapping:
rem   user    -> 50051
rem   ai      -> 8002
rem   app     -> 8004
rem   chat    -> 8005
rem   gateway -> 8456
setlocal EnableDelayedExpansion

set "RUN_USER=0"
set "RUN_AI=0"
set "RUN_APP=0"
set "RUN_CHAT=0"
set "RUN_GATEWAY=0"

if "%~1"=="" (
    set "RUN_USER=1"
    set "RUN_AI=1"
    set "RUN_APP=1"
    set "RUN_CHAT=1"
    set "RUN_GATEWAY=1"
) else (
    call :parse_args %*
    if errorlevel 1 exit /b 1
)

echo Stopping selected services...

if "%RUN_USER%"=="1" call :stop_service "user-service" 50051
if "%RUN_AI%"=="1" call :stop_service "ai-service" 8002
if "%RUN_APP%"=="1" call :stop_service "app-service" 8004
if "%RUN_CHAT%"=="1" call :stop_service "chat-service" 8005
if "%RUN_GATEWAY%"=="1" call :stop_service "api-gateway" 8456

echo Shutdown check completed.
endlocal
exit /b 0

:parse_args
if "%~1"=="" exit /b 0

if /I "%~1"=="user" (
    set "RUN_USER=1"
) else if /I "%~1"=="ai" (
    set "RUN_AI=1"
) else if /I "%~1"=="app" (
    set "RUN_APP=1"
) else if /I "%~1"=="chat" (
    set "RUN_CHAT=1"
) else if /I "%~1"=="gateway" (
    set "RUN_GATEWAY=1"
) else (
    echo Unknown service argument: %~1
    echo Usage: shutdown.bat [user] [ai] [app] [chat] [gateway]
    exit /b 1
)

shift
goto parse_args

:stop_service
set "SERVICE_NAME=%~1"
set "SERVICE_PORT=%~2"
powershell -NoProfile -Command ^
  "$serviceName = '%SERVICE_NAME%';" ^
  "$servicePort = %SERVICE_PORT%;" ^
  "$procIds = @(Get-NetTCPConnection -LocalPort $servicePort -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique);" ^
  "if (-not $procIds) { Write-Output ('[SKIP] ' + $serviceName + ' is not listening on port ' + $servicePort + '.'); exit 0 }" ^
  "foreach ($procId in $procIds) { try { Stop-Process -Id $procId -Force -ErrorAction Stop; Write-Output ('[STOP] ' + $serviceName + ' stopped by killing process ' + $procId + ' on port ' + $servicePort + '.'); } catch { Write-Output ('[FAIL] ' + $serviceName + ' could not stop process ' + $procId + ' on port ' + $servicePort + '.'); exit 1 } }"
exit /b 0
