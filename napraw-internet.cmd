@echo off
chcp 65001 >nul
title Cisza - naprawa internetu (reguly zapory)
cd /d "%~dp0"

echo ============================================================
echo  Naprawa internetu po sesji Ciszy
echo ============================================================
echo.
echo Ten skrypt usuwa reguly zapory "CiszaBlock-*", ktore potrafia
echo zablokowac przegladarki (np. Edge) na portach 80/443.
echo.
echo WYMAGA uprawnien administratora. Jesli okno UAC poprosi o haslo,
echo wybierz konto "admin" (albo inne konto administratora) i podaj haslo.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Nie znaleziono python.exe w PATH. Zainstaluj Pythona albo dodaj go do PATH.
    pause
    exit /b 1
)

python tools\restore.py --panic
set CODE=%ERRORLEVEL%

echo.
echo --- kontrola: czy reguly zniknely ---
powershell -NoProfile -Command "$r = Get-NetFirewallRule -DisplayName 'CiszaBlock*' -ErrorAction SilentlyContinue; if ($r) { 'pozostalo regul: ' + ($r | Measure-Object).Count } else { 'OK - brak regul CiszaBlock' }"

echo.
if "%CODE%"=="0" (echo Gotowe. Sprawdz, czy przegladarka ma internet.) else (echo Skrypt zwrocil kod %CODE% - jesli reguly zostaly, uruchom jako administrator.)
pause
