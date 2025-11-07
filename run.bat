@echo off
echo ========================================
echo Zerodha to 5paisa Copy Trader Launcher
echo ========================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8 or higher
    pause
    exit /b 1
)

REM Check if .venv exists
if not exist ".venv" (
    echo Virtual environment not found. Creating .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
    echo Virtual environment created successfully.
    echo.
) else (
    echo Virtual environment found.
    echo.
)

REM Activate virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)

REM Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip --quiet

REM Install/update requirements
echo Installing/updating requirements...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install requirements
    pause
    exit /b 1
)
echo Requirements installed successfully.
echo.

REM Start the copy trader
echo ========================================
echo Starting Copy Trader...
echo ========================================
echo.
python copy_trader.py

REM If copy_trader.py exits, pause so user can see any error messages
if errorlevel 1 (
    echo.
    echo Copy trader exited with an error.
    pause
)

