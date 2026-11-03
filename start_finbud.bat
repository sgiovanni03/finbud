@echo off
cd /d "%~dp0"
echo Starting FinBud...
start "" "http://127.0.0.1:8000"
".\.venv\Scripts\python.exe" run.py
pause

