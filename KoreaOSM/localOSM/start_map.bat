@echo off
setlocal
title Offline Route Planner
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"
set "LANG=C"
set "LANGUAGE=en"
set "LC_MESSAGES=C"
chcp 65001 >nul 2>nul
pushd "%~dp0" >nul 2>nul
if errorlevel 1 goto :directory_error
echo Offline Route Planner
echo Starting the local map server...
echo.
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto :launch_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto :launch_python
echo ERROR: Python 3.10 or newer was not found.
echo Install Python and enable the Add Python to PATH option.
set "ROUTE_EXIT=1"
goto :finished

:launch_py
py -3 -X utf8 server.py %*
set "ROUTE_EXIT=%errorlevel%"
goto :finished

:launch_python
python -X utf8 server.py %*
set "ROUTE_EXIT=%errorlevel%"
goto :finished

:directory_error
echo ERROR: Could not open the localOSM folder.
echo Move the project to an accessible folder and try again.
set "ROUTE_EXIT=1"
goto :wait_to_close

:finished
popd >nul 2>nul
if "%ROUTE_EXIT%"=="0" exit /b 0
echo.
echo The map server stopped with exit code %ROUTE_EXIT%.
:wait_to_close
echo Press any key to close this window...
pause >nul
exit /b %ROUTE_EXIT%
