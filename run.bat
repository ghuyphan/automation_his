@echo off
echo Starting HIS Automation Tool...

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python automate.py
    pause
    exit
)

for /d %%d in ("%USERPROFILE%\AppData\Local\Programs\Python\Python3*") do (
    if exist "%%d\python.exe" (
        "%%d\python.exe" automate.py
        pause
        exit
    )
)

py -0 >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -3 automate.py
    pause
    exit
)

echo Error: Python was not found in PATH or standard installation directory.
echo Please run install_dependencies.bat first.
pause
