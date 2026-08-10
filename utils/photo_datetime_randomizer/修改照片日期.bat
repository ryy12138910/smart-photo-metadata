@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul

pushd "%~dp0"
if errorlevel 1 goto directory_error

set "PHOTO_INPUT=%~1"
if defined PHOTO_INPUT goto validate_input
set /p "PHOTO_INPUT=Enter or drag the photo folder here: "

:validate_input
if not defined PHOTO_INPUT goto missing_input
set "PHOTO_INPUT=%PHOTO_INPUT:"=%"
if not exist "%PHOTO_INPUT%\." goto missing_folder
if exist "..\..\.venv\Scripts\python.exe" goto run_venv

py randomize_photo_datetime.py "%PHOTO_INPUT%"
set "PHOTO_DATE_EXIT=%ERRORLEVEL%"
goto finish

:run_venv
"..\..\.venv\Scripts\python.exe" randomize_photo_datetime.py "%PHOTO_INPUT%"
set "PHOTO_DATE_EXIT=%ERRORLEVEL%"
goto finish

:missing_input
echo ERROR: No photo folder was provided.
set "PHOTO_DATE_EXIT=2"
goto finish

:missing_folder
echo ERROR: Folder does not exist: "%PHOTO_INPUT%"
set "PHOTO_DATE_EXIT=2"
goto finish

:directory_error
echo ERROR: Cannot enter the script directory.
set "PHOTO_DATE_EXIT=2"
goto final_pause

:finish
popd

:final_pause
echo.
if not defined PHOTO_DATE_NO_PAUSE pause
exit /b %PHOTO_DATE_EXIT%
