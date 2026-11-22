@echo off
rem Nixt Accounts launcher - runs the local server and opens the app.
cd /d "%~dp0"
start "" "%~dp0python\pythonw.exe" "%~dp0app.py"
