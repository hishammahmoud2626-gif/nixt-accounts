@echo off
chcp 65001 >nul
title Nixt - Desktop Shortcut
set "NIXT_SC_TARGET=%~dp0START.bat"
set "NIXT_SC_DIR=%~dp0"
set "NIXT_SC_ICON=%~dp0logo.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $sc=$ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nixt.lnk')); $sc.TargetPath=$env:NIXT_SC_TARGET; $sc.WorkingDirectory=$env:NIXT_SC_DIR; $sc.IconLocation=$env:NIXT_SC_ICON + ',0'; $sc.Description='Nixt Accounts'; $sc.Save()"
echo.
echo تم - لو ظهر اختصار Nixt على سطح المكتب يبقى تمام. لو مفيش، اقفل أي برنامج حماية وجرّب تاني.
pause
