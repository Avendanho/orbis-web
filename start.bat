@echo off
rem ORBIS - dois cliques para subir a interface e o motor no Windows.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 start.py %*) else (python start.py %*)
pause
