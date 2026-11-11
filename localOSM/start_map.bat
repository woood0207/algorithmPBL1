@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0server.py" %*
    goto done
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0server.py" %*
    goto done
)
echo Python 3.10 or newer is required. Install Python and run this file again.
:done
pause
