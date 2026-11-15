@echo off
setlocal
title South Korea Offline Route Planner
if not exist "%~dp0localOSM\start_map.bat" (
  echo ERROR: The map launcher is missing.
  echo Restore the localOSM folder and try again.
  echo Press any key to close this window...
  pause >nul
  exit /b 1
)
call "%~dp0localOSM\start_map.bat" --national-dir "%~dp0localOSM\national_data" %*
exit /b %errorlevel%
