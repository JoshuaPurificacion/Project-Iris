@echo off
REM Project Iris - Arduin-o-vation Booth Launcher
REM Run this script to start Iris with proper environment configuration

REM Audio configuration - adjust these if needed for the exhibit floor
set MIC_INDEX=
set AEC_MULTIPLIER=2.0

echo ========================================
echo   Project Iris - Arduin-o-vation
echo ========================================
echo.
echo Configuration:
echo   MIC_INDEX:      %MIC_INDEX% (OS Default if blank)
echo   AEC_MULTIPLIER: %AEC_MULTIPLIER%
echo.

REM Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    echo Please install Python and ensure it's in your PATH
    pause
    exit /b 1
)

echo Starting Iris...
echo.

python wake_up.py