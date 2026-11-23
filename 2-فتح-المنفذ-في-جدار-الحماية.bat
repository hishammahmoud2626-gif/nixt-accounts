@echo off
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo محتاج صلاحيات مسؤول - بيعيد الفتح كمسؤول...
  powershell -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="Nixt Accounts" >nul 2>&1
netsh advfirewall firewall add rule name="Nixt Accounts" dir=in action=allow protocol=TCP localport=8317 profile=private
echo.
echo تم فتح المنفذ 8317 في جدار الحماية للشبكات الخاصة.
echo لو شبكتك معمولة "عامة" في ويندوز، خليها "خاصة" من إعدادات الشبكة.
pause
