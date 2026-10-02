@echo off
setlocal EnableDelayedExpansion

:: ============================================================================
:: Subtitle Translator - Windows Stop Script
:: ============================================================================

title Subtitle Translator - Stop

set "GREEN=[92m"
set "YELLOW=[93m"
set "RED=[91m"
set "CYAN=[96m"
set "RESET=[0m"

echo.
echo %CYAN%Stopping Subtitle Translator...%RESET%
echo.

:: Kill Python processes running run.py
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST ^| findstr "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /C:"run.py" >nul
    if !ERRORLEVEL! EQU 0 (
        echo Stopping process %%a...
        taskkill /F /IM llama-server.exe >nul 2>&1
taskkill /PID %%a /F >nul 2>&1
    )
)

:: Also check for pythonw.exe
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq pythonw.exe" /FO LIST ^| findstr "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /C:"run.py" >nul
    if !ERRORLEVEL! EQU 0 (
        echo Stopping process %%a...
        taskkill /PID %%a /F >nul 2>&1
    )
)

:: Close any cmd windows titled "Subtitle Translator Backend"
taskkill /FI "WINDOWTITLE eq Subtitle Translator Backend*" /F >nul 2>&1

echo.
echo %GREEN%Subtitle Translator has been stopped.%RESET%
echo.

timeout /t 3 >nul
