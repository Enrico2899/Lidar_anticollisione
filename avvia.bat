@echo off
rem Avvio dell'applicazione anticollisione con doppio click.
rem Usa il Python del venv se esiste, apre la pagina di diagnostica e lascia
rem la finestra aperta a fine esecuzione per leggere eventuali errori.
cd /d "%~dp0"
if exist venv\Scripts\python.exe (set PY=venv\Scripts\python.exe) else (set PY=python)
start "" http://127.0.0.1:8080
%PY% main.py
echo.
echo Applicazione terminata. Premi un tasto per chiudere.
pause >nul
