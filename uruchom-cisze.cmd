@echo off
rem Uruchamia Cisze ze zrodel (bez SmartScreen i bez rozpakowywania do %TEMP%).
rem Dwuklik na tym pliku = to samo co "pythonw run.pyw" w tym katalogu.
setlocal
cd /d "%~dp0"

where pythonw.exe >nul 2>nul
if errorlevel 1 (
    echo Nie znaleziono pythonw.exe w PATH.
    echo Uruchom recznie:  python run.pyw
    pause
    exit /b 1
)

start "Cisza" pythonw.exe "%~dp0run.pyw"
endlocal
