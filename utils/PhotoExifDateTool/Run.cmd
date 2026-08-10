@echo off
setlocal

title Photo EXIF Date Unifier
set "SCRIPT=%~dp0unify_exif_dates.ps1"
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

echo Photo EXIF Date Unifier
echo.
echo This window is normal. A folder picker should open now.
echo Choose the folder that contains your photos.
echo.

if not exist "%SCRIPT%" (
  echo ERROR: Cannot find:
  echo "%SCRIPT%"
  echo.
  echo Please fully extract the zip file first, then keep Run.cmd and
  echo unify_exif_dates.ps1 in the same folder.
  echo.
  pause
  exit /b 1
)

if not exist "%POWERSHELL%" (
  echo ERROR: Cannot find Windows PowerShell.
  echo "%POWERSHELL%"
  echo.
  pause
  exit /b 1
)

"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "EXITCODE=%ERRORLEVEL%"
echo.
if "%EXITCODE%"=="0" (
  echo Done. You can close this window.
) else (
  echo ERROR: The tool stopped with exit code %EXITCODE%.
  echo Please send me the lines above this message.
)
echo.
pause
endlocal
