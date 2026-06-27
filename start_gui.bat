@echo off
:: Check if pythonw is in PATH
where pythonw >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" pythonw gui.py
    exit
)

:: Check standard LocalAppData installation paths
for /d %%d in ("%USERPROFILE%\AppData\Local\Programs\Python\Python3*") do (
    if exist "%%d\pythonw.exe" (
        start "" "%%d\pythonw.exe" gui.py
        exit
    )
)

:: Check registry/system Python launcher
pyw -0 >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" pyw -3 gui.py
    exit
)

echo Error: Python was not found in PATH or standard installation directory.
echo Please run install_dependencies.bat first.
pause
