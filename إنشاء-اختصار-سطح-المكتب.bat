@echo off
chcp 65001 >nul
title اختصار سطح المكتب - نيكست
"%~dp0python\python.exe" -c "import os,winshell" 2>nul
powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $sc=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\نيكست - الحسابات.lnk'); $sc.TargetPath='%~dp0START.bat'; $sc.WorkingDirectory='%~dp0'; $sc.IconLocation='%~dp0logo.ico'; $sc.Save()"
echo تم إنشاء الاختصار على سطح المكتب.
pause
