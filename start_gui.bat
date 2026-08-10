@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 goto PATH_ERROR

if not exist ".venv\Scripts\pythonw.exe" goto VENV_ERROR
if not exist "photo_metadata_gui.py" goto SCRIPT_ERROR

start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0photo_metadata_gui.py"
popd
exit /b 0

:PATH_ERROR
echo ERROR: Cannot open the project folder.
pause
exit /b 1

:VENV_ERROR
echo ERROR: Python environment was not found.
echo Run these commands in the project folder:
echo   py -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
popd
pause
exit /b 1

:SCRIPT_ERROR
echo ERROR: photo_metadata_gui.py was not found.
popd
pause
exit /b 1
