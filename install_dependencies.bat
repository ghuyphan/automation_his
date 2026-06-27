@echo off
echo ==================================================
echo HIS Automator - Dependency Installer
echo ==================================================
echo.

:: Check if Python is installed
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Python is not in system PATH. Checking standard installations...
    
    set "PYTHON_EXE="
    for /d %%d in ("%USERPROFILE%\AppData\Local\Programs\Python\Python3*") do (
        if exist "%%d\python.exe" (
            set "PYTHON_EXE=%%d\python.exe"
        )
    )
    
    if defined PYTHON_EXE (
        echo Found Python at "%PYTHON_EXE%"
        goto install_deps
    )
    
    echo Python was not found on this system.
    echo Attempting to install Python 3.12 via winget...
    winget install Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    
    if %ERRORLEVEL% neq 0 (
        echo.
        echo Error: Could not install Python automatically.
        echo Please download and install Python 3.12 manually from:
        echo https://www.python.org/downloads/
        echo (Make sure to check "Add Python to PATH" during installation)
        echo.
        pause
        exit /b 1
    )
    
    echo.
    echo Python installation complete! Please restart your terminal/command prompt
    echo and run this file again to finish installing dependencies.
    echo.
    pause
    exit /b 0
)

:install_deps
echo Installing required Python packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if %ERRORLEVEL% equ 0 (
    echo.
    echo ==================================================
    echo Installation completed successfully!
    echo You can now run the app using start_gui.bat
    echo ==================================================
    echo.
) else (
    echo.
    echo Error: Failed to install some Python packages.
    echo Please make sure you have an active internet connection.
    echo.
)
pause
