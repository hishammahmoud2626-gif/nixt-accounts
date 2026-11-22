# Nixt Accounts — نظام حسابات نيكست

Offline Windows desktop accounts system for a leather-footwear business. Local SQLite database, Arabic RTL interface, no internet required.

برنامج حسابات بيعمل أوفلاين على ويندوز: فواتير بيع وشراء، مرتجعات، عملاء وموردين، دفعات مقسطة، كشوف حساب، طباعة PDF، ونسخ احتياطي. كل البيانات على الجهاز نفسه.

## Features

- Sales and purchase invoices with returns and split payments
- Customer and supplier accounts with statements, credit limits and overdue alerts
- Customer opening balances and statement history import (MeDo / Excel / CSV)
- User management (admin/user roles, first-run setup, login recovery)
- Printable invoices and statements with company logo and details
- Quick search across invoices and parties
- Daily automatic backup to a configurable folder
- In-app update checker (reads `version.json` from this repo)

## Run (portable)

Download `NixtAccounts-x.y.z-Portable.zip` from the latest Release, unzip, and double-click `START.bat`. A bundled Python is included; no installation needed.

## Data safety

All business data stays in a local SQLite file inside the app folder. This repository contains program code only — no customer or business data.
