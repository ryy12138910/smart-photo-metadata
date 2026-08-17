@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"

if exist "PhotoMetadataTool.exe" (
    start "" /D "%~dp0" "%~dp0PhotoMetadataTool.exe"
    popd
    exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "exit_code=%errorlevel%"
popd
if not "%exit_code%"=="0" pause
exit /b %exit_code%
