@echo off
setlocal EnableExtensions EnableDelayedExpansion

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage

set "REPO_ID=%~1"
set "PAYLOAD_DIR=%~2"
set "IDENTITY=%~3"
set "MODE=%~4"

if "%IDENTITY%"=="" set "IDENTITY=user"
if /I "%MODE%"=="--dry-run" (
  set "DRY_RUN=--dry-run"
) else (
  set "DRY_RUN="
)

if "%LARK_CLI_EXE%"=="" (
  set "LARK_CLI_EXE=%APPDATA%\npm\lark-cli.cmd"
)

if not exist "%LARK_CLI_EXE%" (
  echo lark-cli not found: "%LARK_CLI_EXE%"
  exit /b 1
)

if not exist "%PAYLOAD_DIR%" (
  echo Payload directory not found: "%PAYLOAD_DIR%"
  exit /b 1
)

for %%F in ("%PAYLOAD_DIR%\*.json") do (
  set "JSON="
  set /p JSON=<"%%~fF"
  echo === Posting %%~nxF ===
  call "%LARK_CLI_EXE%" api POST "/open-apis/lingo/v1/drafts?repo_id=%REPO_ID%" --as %IDENTITY% %DRY_RUN% --data "!JSON!"
  if errorlevel 1 (
    echo Failed on %%~nxF
    exit /b 1
  )
)

echo All draft requests completed.
exit /b 0

:usage
echo Usage: post_lingo_drafts.cmd ^<repo_id^> ^<payload_dir^> [user^|bot] [--dry-run]
exit /b 1
