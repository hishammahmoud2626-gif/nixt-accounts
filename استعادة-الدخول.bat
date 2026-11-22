@echo off
chcp 65001 >nul
title استعادة الدخول - نيكست
echo هذا الملف يمسح كل المستخدمين وكلمات السر.
echo بعد التشغيل افتح البرنامج وهيطلب منك إنشاء حساب مدير جديد.
echo الفواتير والعملاء والبيانات لا تتأثر.
echo.
set /p ok="اكتب نعم للاستمرار: "
if /i not "%ok%"=="نعم" exit /b
"%~dp0python\python.exe" -c "import sqlite3,os; p=os.path.join(os.path.dirname(os.path.abspath(__file__)),'data','nixt.db'); p=os.path.join(r'%~dp0','data','nixt.db'); c=sqlite3.connect(p); c.execute('DELETE FROM users'); c.commit(); c.close(); print('تم مسح المستخدمين. شغل البرنامج الآن.')"
pause
