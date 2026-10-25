@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys; assert sys.version_info >= (3,12)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 "%~dp0view\bootstrap.py"
goto finish
:try_python
where python >nul 2>nul
if errorlevel 1 goto missing
python -c "import sys; assert sys.version_info >= (3,12)" >nul 2>nul
if errorlevel 1 goto missing
python "%~dp0view\bootstrap.py"
goto finish
:missing
powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Python 3.12 or newer was not found. Install Python or start from Anaconda Prompt.','CETUS startup error',0,16)" >nul
exit /b 1
:finish
if not errorlevel 1 exit /b 0
echo CETUS could not start. See the error above.
pause
exit /b 1
