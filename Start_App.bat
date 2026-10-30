@echo off
title NIFTY Option Chain Recorder
cd /d "%~dp0"
echo [1/2] Installing requirements...
pip install -q flask requests pandas openpyxl apscheduler pytz
echo [2/2] Starting server...
start "" python app.py
timeout /t 3 /nobreak >nul
start http://127.0.0.1:5000
exit
