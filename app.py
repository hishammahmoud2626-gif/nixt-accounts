#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nixt Accounts - offline desktop accounts system (Arabic UI).
Single-file stdlib server: http.server + sqlite3. No internet needed.
Data lives in data/nixt.db next to this file. Backups in backups/.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import webbrowser
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_VERSION = "1.1.3"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
DB_PATH = os.path.join(DATA_DIR, "nixt.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
HOST = "127.0.0.1"
PORT = 8317
UPDATE_CONFIG = os.path.join(BASE_DIR, "update_url.txt")
UPDATES_DIR = os.path.join(BASE_DIR, "updates")


def read_update_url():
    try:
        with open(UPDATE_CONFIG, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0,)


UPDATE_FALLBACK_URL = "https://api.github.com/repos/hishammahmoud2626-gif/nixt-accounts/releases/latest"

UPDATE_ERROR_KIND = {"noinet": "لا يوجد اتصال بالإنترنت حالياً",
                     "cert": "مشكلة في شهادات الأمان (SSL) على الجهاز",
                     "http": "خادم التحديثات رد برد غير متوقع",
                     "bad": "رد غير مفهوم من خادم التحديثات"}


def classify_net_error(e):
    import ssl as _ssl
    s = str(e)
    if isinstance(e, _ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in s.upper():
        return "cert"
    if isinstance(e, urllib.error.HTTPError):
        return "http"
    return "noinet"


def _fetch_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "NixtAccounts/" + APP_VERSION,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_update_info():
    """Returns (info, error_kind, error_text). info is None on failure.
    Tries the configured version.json URL first, then the GitHub latest-release API."""
    url = read_update_url()
    errors = []
    if url:
        try:
            info = _fetch_json(url)
            if info.get("version"):
                return info, None, ""
            errors.append(("bad", "version.json missing version field"))
        except Exception as e:
            errors.append((classify_net_error(e), "%s: %s" % (type(e).__name__, e)))
    try:
        rel = _fetch_json(UPDATE_FALLBACK_URL)
        zip_url = ""
        for a in rel.get("assets", []):
            if a.get("name", "").endswith("Portable.zip"):
                zip_url = a.get("browser_download_url", "")
                break
        info = {"version": str(rel.get("tag_name", "")).lstrip("vV"),
                "zip_url": zip_url, "notes": (rel.get("body") or "")[:300]}
        if info["version"]:
            return info, None, ""
        errors.append(("bad", "github latest release missing tag_name"))
    except Exception as e:
        errors.append((classify_net_error(e), "%s: %s" % (type(e).__name__, e)))
    kind = errors[0][0] if errors else "noinet"
    detail = " | ".join("%s [%s]" % (t, k) for k, t in errors)[:300]
    return None, kind, detail


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT DEFAULT '',
  group_name TEXT DEFAULT '',
  discount_pct REAL DEFAULT 0,
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  date TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'sale',
  shipping REAL DEFAULT 0,
  other_charges REAL DEFAULT 0,
  discount REAL DEFAULT 0,
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoice_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  item_name TEXT NOT NULL,
  color TEXT DEFAULT '',
  pairs REAL DEFAULT 0,
  price REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  date TEXT NOT NULL,
  cash REAL DEFAULT 0,
  card REAL DEFAULT 0,
  transfer REAL DEFAULT 0,
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  role TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  start_date TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  pass_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ledger_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  type TEXT DEFAULT '',
  desc TEXT DEFAULT '',
  product TEXT DEFAULT '',
  pairs REAL DEFAULT 0,
  debit REAL DEFAULT 0,
  credit REAL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  filename TEXT DEFAULT '',
  summary TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
"""

# columns added after 1.0.0: (table, column, ddl)
MIGRATIONS = [
    ("customers", "kind", "ALTER TABLE customers ADD COLUMN kind TEXT NOT NULL DEFAULT 'customer'"),
    ("customers", "code", "ALTER TABLE customers ADD COLUMN code TEXT DEFAULT ''"),
    ("customers", "address", "ALTER TABLE customers ADD COLUMN address TEXT DEFAULT ''"),
    ("customers", "credit_limit", "ALTER TABLE customers ADD COLUMN credit_limit REAL DEFAULT 0"),
    ("customers", "opening", "ALTER TABLE customers ADD COLUMN opening REAL DEFAULT 0"),
    ("customers", "opening_date", "ALTER TABLE customers ADD COLUMN opening_date TEXT DEFAULT ''"),
    ("payments", "direction", "ALTER TABLE payments ADD COLUMN direction TEXT NOT NULL DEFAULT 'in'"),
]

DEFAULT_SETTINGS = {
    "company_name": "نيكست للجلديات - NIXT FOR LEATHER",
    "company_phone": "",
    "company_address": "",
    "alert_days": "30",
    "backup_dir": "",
    "login_show_users": "1",
}

INVOICE_KINDS = ("sale", "return", "purchase", "purchase_return")
KIND_LABELS = {"sale": "فاتورة بيع", "return": "مرتجع بيع",
               "purchase": "فاتورة شراء", "purchase_return": "مرتجع شراء"}
PARTY_KIND_FOR_INVOICE = {"sale": "customer", "return": "customer",
                          "purchase": "supplier", "purchase_return": "supplier"}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA)
    for table, col, ddl in MIGRATIONS:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)]
        if col not in cols:
            conn.execute(ddl)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()


def get_setting(conn, key, default=""):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


# ---------------- auth ----------------
PBKDF2_ROUNDS = 120000
SESSIONS = {}  # token -> {uid, username, role, exp}
SESSION_HOURS = 12


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                            bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
    return "%d$%s$%s" % (PBKDF2_ROUNDS, salt, h)


def check_password(password, stored):
    try:
        rounds, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                   bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def users_count():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    conn.close()
    return n


def new_session(uid, username, role):
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"uid": uid, "username": username, "role": role,
                       "exp": time.time() + SESSION_HOURS * 3600}
    return token


def get_session(token):
    s = SESSIONS.get(token)
    if not s:
        return None
    if s["exp"] < time.time():
        SESSIONS.pop(token, None)
        return None
    return s


# ---------------- helpers ----------------
def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def invoice_net(inv_id, conn):
    items = conn.execute(
        "SELECT COALESCE(SUM(pairs*price),0) AS t FROM invoice_items WHERE invoice_id=?",
        (inv_id,)).fetchone()["t"]
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    return items + (inv["shipping"] or 0) + (inv["other_charges"] or 0) - (inv["discount"] or 0)


def party_balance(conn, pid):
    """Positive = customer owes us / we owe supplier."""
    p = conn.execute("SELECT * FROM customers WHERE id=?", (pid,)).fetchone()
    if not p:
        return 0.0
    kind = p["kind"] or "customer"
    sale_kind, ret_kind = ("sale", "return") if kind == "customer" else ("purchase", "purchase_return")
    bal = p["opening"] or 0.0
    for inv in conn.execute("SELECT * FROM invoices WHERE customer_id=?", (pid,)):
        net = invoice_net(inv["id"], conn)
        bal += net if inv["kind"] == sale_kind else -net
    for pm in conn.execute("SELECT * FROM payments WHERE customer_id=?", (pid,)):
        total = (pm["cash"] or 0) + (pm["card"] or 0) + (pm["transfer"] or 0)
        bal -= total  # 'in' from customer or 'out' to supplier both reduce balance
    le = conn.execute("SELECT COALESCE(SUM(debit-credit),0) s FROM ledger_entries WHERE customer_id=?",
                      (pid,)).fetchone()["s"]
    bal += le if kind == "customer" else -le
    return round(bal, 2)


def backup_dir_effective(conn=None):
    own = conn is None
    conn = conn or get_db()
    d = get_setting(conn, "backup_dir", "").strip()
    if own:
        conn.close()
    return d if d and os.path.isdir(d) else BACKUP_DIR


def do_backup():
    """Copy the DB to the backup folder with a timestamp. Returns the path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir_effective(), "nixt-%s.db" % stamp)
    src = get_db()
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.close()
    src.close()
    for folder in {BACKUP_DIR, os.path.dirname(dest)}:
        try:
            files = sorted(f for f in os.listdir(folder)
                           if f.startswith("nixt-") and f.endswith(".db"))
            for old in files[:-60]:
                os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return dest


AUTO_BACKUP_STATE = {"date": ""}


def auto_backup_tick():
    """Once per calendar day while the app is running."""
    today = datetime.now().strftime("%Y%m%d")
    if AUTO_BACKUP_STATE["date"] == today:
        return
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        return
    try:
        do_backup()
        AUTO_BACKUP_STATE["date"] = today
    except Exception:
        pass



def norm_username(s):
    return unicodedata.normalize("NFC", (s or "").strip())


# failed-login protection: per-username counters, lock after 5 fails for 60s
LOGIN_FAILS = {}
LOGIN_LOCK = threading.Lock()


def login_throttle_check(uname):
    with LOGIN_LOCK:
        fails, locked_until = LOGIN_FAILS.get(uname.lower(), (0, 0.0))
        return time.time() < locked_until


def login_throttle_fail(uname):
    with LOGIN_LOCK:
        fails, _ = LOGIN_FAILS.get(uname.lower(), (0, 0.0))
        fails += 1
        locked_until = time.time() + 60 if fails >= 5 else 0.0
        if fails >= 5:
            fails = 0
        LOGIN_FAILS[uname.lower()] = (fails, locked_until)


def login_throttle_clear(uname):
    with LOGIN_LOCK:
        LOGIN_FAILS.pop(uname.lower(), None)


# ---------------- API ----------------
class API:
    # ---------- auth ----------
    def me(q, user):
        return 200, {
            "logged_in": bool(user),
            "needs_setup": users_count() == 0,
            "username": user["username"] if user else "",
            "role": user["role"] if user else "",
            "version": APP_VERSION,
        }

    def setup(body, user):
        if users_count() > 0:
            return 400, {"error": "already setup"}
        uname = norm_username(body.get("username"))
        pw = body.get("password") or ""
        if len(uname) < 3 or len(pw) < 4:
            return 400, {"error": "اسم المستخدم 3 أحرف على الأقل وكلمة السر 4 على الأقل"}
        conn = get_db()
        conn.execute("INSERT INTO users (username, pass_hash, role, created_at) VALUES (?,?,?,?)",
                     (uname, hash_password(pw), "admin", now_str()))
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()["id"]
        conn.close()
        return 200, {"ok": True, "token": new_session(uid, uname, "admin")}

    def login(body, user):
        uname = norm_username(body.get("username"))
        pw = body.get("password") or ""
        if login_throttle_check(uname):
            return 429, {"error": "محاولات كتير غلط. استنى دقيقة وجرب تاني"}
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (uname,)).fetchone()
        conn.close()
        if not u or not check_password(pw, u["pass_hash"]):
            login_throttle_fail(uname)
            time.sleep(0.5)
            return 401, {"error": "بيانات الدخول غير صحيحة"}
        login_throttle_clear(uname)
        return 200, {"ok": True, "token": new_session(u["id"], u["username"], u["role"]),
                     "username": u["username"], "role": u["role"]}

    def login_users(q, user):
        conn = get_db()
        show = get_setting(conn, "login_show_users", "1") == "1"
        out = []
        if show and users_count() > 0:
            for r in conn.execute("SELECT username, role FROM users ORDER BY username"):
                name = r["username"]
                initials = "".join(w[0] for w in name.split()[:2]).upper() or name[:1].upper()
                out.append({"username": name, "role": r["role"], "initials": initials})
        conn.close()
        return 200, out

    def logout(body, user):
        return 200, {"ok": True}

    def change_password(body, user):
        old, new = body.get("old") or "", body.get("new") or ""
        if len(new) < 4:
            return 400, {"error": "كلمة السر الجديدة 4 أحرف على الأقل"}
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE id=?", (user["uid"],)).fetchone()
        if not u or not check_password(old, u["pass_hash"]):
            conn.close()
            return 400, {"error": "كلمة السر الحالية غير صحيحة"}
        conn.execute("UPDATE users SET pass_hash=? WHERE id=?", (hash_password(new), user["uid"]))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- users (admin) ----------
    def list_users(q, user):
        conn = get_db()
        out = rows(conn.execute("SELECT id, username, role, created_at FROM users ORDER BY username"))
        conn.close()
        return 200, out

    def create_user(body, user):
        uname = norm_username(body.get("username"))
        pw = body.get("password") or ""
        role = body.get("role") if body.get("role") in ("admin", "user") else "user"
        if len(uname) < 3 or len(pw) < 4:
            return 400, {"error": "اسم المستخدم 3 أحرف على الأقل وكلمة السر 4 على الأقل"}
        conn = get_db()
        try:
            conn.execute("INSERT INTO users (username, pass_hash, role, created_at) VALUES (?,?,?,?)",
                         (uname, hash_password(pw), role, now_str()))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return 400, {"error": "اسم المستخدم موجود بالفعل"}
        conn.close()
        return 200, {"ok": True}

    def reset_user(uid, body, user):
        pw = body.get("password") or ""
        if len(pw) < 4:
            return 400, {"error": "كلمة السر 4 أحرف على الأقل"}
        conn = get_db()
        conn.execute("UPDATE users SET pass_hash=? WHERE id=?", (hash_password(pw), uid))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def delete_user(uid):
        conn = get_db()
        n = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if u and u["role"] == "admin" and n <= 1:
            conn.close()
            return 400, {"error": "لا يمكن حذف آخر مدير"}
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- settings ----------
    def get_settings(q, user):
        conn = get_db()
        out = {k: get_setting(conn, k, v) for k, v in DEFAULT_SETTINGS.items()}
        out["backup_dir_effective"] = backup_dir_effective(conn)
        conn.close()
        return 200, out

    def save_settings(body, user):
        conn = get_db()
        for k in DEFAULT_SETTINGS:
            if k in body:
                conn.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (k, str(body.get(k) or "")))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- parties (customers & suppliers) ----------
    def list_customers(q, user):
        kind = q.get("kind", [""])[0]
        conn = get_db()
        sql = "SELECT * FROM customers"
        args = []
        if kind in ("customer", "supplier"):
            sql += " WHERE kind=?"
            args.append(kind)
        sql += " ORDER BY name"
        out = rows(conn.execute(sql, args))
        for c in out:
            c["balance"] = party_balance(conn, c["id"])
        conn.close()
        return 200, out

    def create_customer(body, user):
        name = (body.get("name") or "").strip()
        if not name:
            return 400, {"error": "الاسم مطلوب"}
        kind = body.get("kind") if body.get("kind") in ("customer", "supplier") else "customer"
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO customers (name, phone, group_name, discount_pct, notes, kind, code, address,"
            " credit_limit, opening, opening_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, body.get("phone", ""), body.get("group_name", ""),
             float(body.get("discount_pct") or 0), body.get("notes", ""), kind,
             body.get("code", ""), body.get("address", ""),
             float(body.get("credit_limit") or 0), float(body.get("opening") or 0),
             body.get("opening_date", ""), now_str()))
        conn.commit()
        cid = cur.lastrowid
        conn.close()
        return 200, {"id": cid}

    def list_employees(q, user):
        conn = get_db()
        out = rows(conn.execute("SELECT * FROM employees ORDER BY name"))
        conn.close()
        return 200, out

    def create_employee(body, user):
        name = (body.get("name") or "").strip()
        if not name:
            return 400, {"error": "الاسم مطلوب"}
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO employees (name, role, phone, start_date, notes, active, created_at)"
            " VALUES (?,?,?,?,?,1,?)",
            (name, body.get("role", ""), body.get("phone", ""),
             body.get("start_date", ""), body.get("notes", ""), now_str()))
        conn.commit()
        eid = cur.lastrowid
        conn.close()
        return 200, {"id": eid}

    def toggle_employee(eid, user):
        conn = get_db()
        conn.execute("UPDATE employees SET active = 1 - active WHERE id=?", (eid,))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def update_customer(cid, body, user):
        conn = get_db()
        conn.execute(
            "UPDATE customers SET name=?, phone=?, group_name=?, discount_pct=?, notes=?,"
            " code=?, address=?, credit_limit=?, opening=?, opening_date=? WHERE id=?",
            (body.get("name", ""), body.get("phone", ""), body.get("group_name", ""),
             float(body.get("discount_pct") or 0), body.get("notes", ""),
             body.get("code", ""), body.get("address", ""),
             float(body.get("credit_limit") or 0), float(body.get("opening") or 0),
             body.get("opening_date", ""), cid))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def delete_customer(cid):
        conn = get_db()
        used = conn.execute("SELECT COUNT(*) c FROM invoices WHERE customer_id=?", (cid,)).fetchone()["c"]
        used += conn.execute("SELECT COUNT(*) c FROM payments WHERE customer_id=?", (cid,)).fetchone()["c"]
        if used:
            conn.close()
            return 400, {"error": "له فواتير أو دفعات مسجلة"}
        conn.execute("DELETE FROM customers WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- invoices ----------
    def list_invoices(q, user):
        cid = q.get("customer_id", [""])[0]
        kind = q.get("kind", [""])[0]
        conn = get_db()
        sql = "SELECT * FROM invoices"
        conds, args = [], []
        if cid:
            conds.append("customer_id=?")
            args.append(cid)
        if kind in INVOICE_KINDS:
            conds.append("kind=?")
            args.append(kind)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY date DESC, id DESC"
        invs = rows(conn.execute(sql, args))
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM customers")}
        for inv in invs:
            items = rows(conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (inv["id"],)))
            inv["items"] = items
            inv["items_total"] = round(sum(i["pairs"] * i["price"] for i in items), 2)
            inv["net"] = round(invoice_net(inv["id"], conn), 2)
            inv["customer_name"] = names.get(inv["customer_id"], "?")
            inv["pairs"] = sum(i["pairs"] for i in items)
        conn.close()
        return 200, invs

    def create_invoice(body, user):
        cid = body.get("customer_id")
        if not cid:
            return 400, {"error": "اختر الطرف أولاً"}
        kind = body.get("kind", "sale")
        if kind not in INVOICE_KINDS:
            return 400, {"error": "نوع غير صحيح"}
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO invoices (customer_id, date, kind, shipping, other_charges, discount, notes, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (cid, body.get("date") or datetime.now().strftime("%Y-%m-%d"), kind,
             float(body.get("shipping") or 0), float(body.get("other_charges") or 0),
             float(body.get("discount") or 0), body.get("notes", ""), now_str()))
        iid = cur.lastrowid
        for it in body.get("items", []):
            if not (it.get("item_name") or "").strip():
                continue
            conn.execute(
                "INSERT INTO invoice_items (invoice_id, item_name, color, pairs, price) VALUES (?,?,?,?,?)",
                (iid, it["item_name"].strip(), it.get("color", ""),
                 float(it.get("pairs") or 0), float(it.get("price") or 0)))
        conn.commit()
        net = invoice_net(iid, conn)
        conn.close()
        return 200, {"id": iid, "net": round(net, 2)}

    def get_invoice(iid, user):
        conn = get_db()
        inv = conn.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
        if not inv:
            conn.close()
            return 404, {"error": "not found"}
        inv = dict(inv)
        inv["items"] = rows(conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)))
        inv["items_total"] = round(sum(i["pairs"] * i["price"] for i in inv["items"]), 2)
        inv["net"] = round(invoice_net(iid, conn), 2)
        cust = conn.execute("SELECT * FROM customers WHERE id=?", (inv["customer_id"],)).fetchone()
        inv["customer"] = dict(cust) if cust else None
        st = {k: get_setting(conn, k, v) for k, v in DEFAULT_SETTINGS.items()}
        inv["company"] = st
        conn.close()
        return 200, inv

    def delete_invoice(iid):
        conn = get_db()
        conn.execute("DELETE FROM invoice_items WHERE invoice_id=?", (iid,))
        conn.execute("DELETE FROM invoices WHERE id=?", (iid,))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- payments ----------
    def list_payments(q, user):
        cid = q.get("customer_id", [""])[0]
        conn = get_db()
        sql = "SELECT p.*, c.name AS customer_name, c.kind AS party_kind FROM payments p" \
              " JOIN customers c ON c.id=p.customer_id"
        args = []
        if cid:
            sql += " WHERE p.customer_id=?"
            args.append(cid)
        sql += " ORDER BY p.date DESC, p.id DESC"
        out = rows(conn.execute(sql, args))
        for p in out:
            p["total"] = round((p["cash"] or 0) + (p["card"] or 0) + (p["transfer"] or 0), 2)
        conn.close()
        return 200, out

    def create_payment(body, user):
        cid = body.get("customer_id")
        if not cid:
            return 400, {"error": "اختر الطرف أولاً"}
        conn = get_db()
        party = conn.execute("SELECT kind FROM customers WHERE id=?", (cid,)).fetchone()
        direction = body.get("direction") or ("out" if (party and party["kind"] == "supplier") else "in")
        cur = conn.execute(
            "INSERT INTO payments (customer_id, date, cash, card, transfer, notes, direction, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (cid, body.get("date") or datetime.now().strftime("%Y-%m-%d"),
             float(body.get("cash") or 0), float(body.get("card") or 0),
             float(body.get("transfer") or 0), body.get("notes", ""), direction, now_str()))
        conn.commit()
        pid = cur.lastrowid
        conn.close()
        return 200, {"id": pid}

    def delete_payment(pid):
        conn = get_db()
        conn.execute("DELETE FROM payments WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- statement ----------
    def statement(q, user):
        cid = q.get("customer_id", [""])[0]
        if not cid:
            return 400, {"error": "customer_id required"}
        conn = get_db()
        cust = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not cust:
            conn.close()
            return 404, {"error": "not found"}
        cust = dict(cust)
        is_supplier = cust.get("kind") == "supplier"
        sale_kind, ret_kind = ("purchase", "purchase_return") if is_supplier else ("sale", "return")
        lines = []
        opening = cust.get("opening") or 0
        if abs(opening) > 0.004:
            if is_supplier:
                ln = {"debit": 0, "credit": round(opening, 2)} if opening > 0 else {"debit": round(-opening, 2), "credit": 0}
            else:
                ln = {"debit": round(opening, 2), "credit": 0} if opening > 0 else {"debit": 0, "credit": round(-opening, 2)}
            ln.update({"date": cust.get("opening_date") or "", "sort": "", "type": "opening",
                       "ref": 0, "desc": "رصيد افتتاحي (من النظام السابق)"})
            lines.append(ln)
        for inv in conn.execute("SELECT * FROM invoices WHERE customer_id=?", (cid,)):
            net = invoice_net(inv["id"], conn)
            is_sale = inv["kind"] == sale_kind
            # customer: sale=debit, return=credit. supplier: purchase=credit, return=debit
            if is_supplier:
                debit, credit = (0, round(net, 2)) if is_sale else (round(net, 2), 0)
            else:
                debit, credit = (round(net, 2), 0) if is_sale else (0, round(net, 2))
            lines.append({
                "date": inv["date"], "sort": inv["created_at"], "type": inv["kind"], "ref": inv["id"],
                "desc": "%s #%s" % (KIND_LABELS.get(inv["kind"], inv["kind"]), inv["id"]),
                "debit": debit, "credit": credit,
            })
        for p in conn.execute("SELECT * FROM payments WHERE customer_id=?", (cid,)):
            total = (p["cash"] or 0) + (p["card"] or 0) + (p["transfer"] or 0)
            parts = []
            if p["cash"]:
                parts.append("كاش")
            if p["card"]:
                parts.append("شبكة")
            if p["transfer"]:
                parts.append("تحويل")
            label = "دفعة للمورد" if is_supplier else "دفعة"
            lines.append({
                "date": p["date"], "sort": p["created_at"], "type": "payment", "ref": p["id"],
                "desc": "%s (%s)" % (label, "/".join(parts)) if parts else label,
                "debit": round(total, 2) if is_supplier else 0,
                "credit": 0 if is_supplier else round(total, 2),
            })
        for e in conn.execute("SELECT * FROM ledger_entries WHERE customer_id=?", (cid,)):
            lines.append({
                "date": e["date"], "sort": e["created_at"] or e["date"], "type": "ledger",
                "ref": e["id"],
                "desc": e["desc"] or e["type"],
                "debit": round(e["debit"] or 0, 2), "credit": round(e["credit"] or 0, 2),
            })
        lines.sort(key=lambda l: (0 if l["type"] == "opening" else 1, l["date"] or "0000", l["sort"]))
        bal = 0.0
        for l in lines:
            bal += (l["debit"] - l["credit"]) if not is_supplier else (l["credit"] - l["debit"])
            l["balance"] = round(bal, 2)
        sales = returns = pairs = 0.0
        for inv in conn.execute("SELECT * FROM invoices WHERE customer_id=?", (cid,)):
            net = invoice_net(inv["id"], conn)
            if inv["kind"] == sale_kind:
                sales += net
                pairs += conn.execute("SELECT COALESCE(SUM(pairs),0) s FROM invoice_items WHERE invoice_id=?",
                                      (inv["id"],)).fetchone()["s"]
            elif inv["kind"] == ret_kind:
                returns += net
        paid = conn.execute("SELECT COALESCE(SUM(cash+card+transfer),0) s FROM payments WHERE customer_id=?",
                            (cid,)).fetchone()["s"]
        led = conn.execute("SELECT COALESCE(SUM(debit-credit),0) s FROM ledger_entries WHERE customer_id=?",
                           (cid,)).fetchone()["s"]
        remaining = round((cust.get("opening") or 0) + sales - returns - paid
                          + (led if not is_supplier else -led), 2)
        pct = round(100.0 * paid / sales, 1) if sales > 0 else 0
        st = {k: get_setting(conn, k, v) for k, v in DEFAULT_SETTINGS.items()}
        conn.close()
        return 200, {
            "customer": cust,
            "lines": lines,
            "company": st,
            "summary": {"sales": round(sales, 2), "returns": round(returns, 2),
                        "paid": round(paid, 2), "remaining": remaining,
                        "pairs": pairs, "payback_pct": pct},
        }

    # ---------- dashboard ----------
    def dashboard(q, user):
        conn = get_db()
        out = {}
        for kind, sale_k, ret_k in (("customer", "sale", "return"), ("supplier", "purchase", "purchase_return")):
            ids = [r["id"] for r in conn.execute("SELECT id FROM customers WHERE kind=?", (kind,))]
            idlist = ",".join(str(i) for i in ids) or "-1"
            sales = returns = 0.0
            for inv in conn.execute("SELECT * FROM invoices WHERE customer_id IN (%s)" % idlist):
                net = invoice_net(inv["id"], conn)
                if inv["kind"] == sale_k:
                    sales += net
                elif inv["kind"] == ret_k:
                    returns += net
            paid = conn.execute("SELECT COALESCE(SUM(cash+card+transfer),0) s FROM payments"
                                " WHERE customer_id IN (%s)" % idlist).fetchone()["s"]
            opening = conn.execute("SELECT COALESCE(SUM(opening),0) s FROM customers WHERE kind=?",
                                   (kind,)).fetchone()["s"]
            out[kind] = {"sales": round(sales, 2), "returns": round(returns, 2),
                         "paid": round(paid, 2),
                         "remaining": round(opening + sales - returns - paid, 2),
                         "count": len(ids)}
        pairs = 0.0
        for r in conn.execute("SELECT ii.pairs FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id"
                              " WHERE i.kind='sale'"):
            pairs += r["pairs"] or 0
        out["pairs"] = pairs
        out["invoices"] = conn.execute("SELECT COUNT(*) c FROM invoices WHERE kind='sale'").fetchone()["c"]
        # debtors & creditor lists + alerts
        debtors, creditors, over_limit, stale = [], [], [], []
        alert_days = int(float(get_setting(conn, "alert_days", "30") or 30))
        today_d = datetime.now().date()
        for p in conn.execute("SELECT * FROM customers"):
            bal = party_balance(conn, p["id"])
            if p["kind"] == "customer":
                if bal > 0.005:
                    entry = {"id": p["id"], "name": p["name"], "balance": bal}
                    debtors.append(entry)
                    if (p["credit_limit"] or 0) > 0 and bal > p["credit_limit"]:
                        over_limit.append({**entry, "limit": p["credit_limit"]})
                    last = conn.execute(
                        "SELECT MAX(d) md FROM (SELECT date d FROM invoices WHERE customer_id=?"
                        " UNION ALL SELECT date FROM payments WHERE customer_id=?)",
                        (p["id"], p["id"])).fetchone()["md"]
                    if last:
                        try:
                            age = (today_d - datetime.strptime(last, "%Y-%m-%d").date()).days
                        except ValueError:
                            age = 0
                        if age > alert_days:
                            stale.append({**entry, "days": age, "last": last})
            else:
                if bal > 0.005:
                    creditors.append({"id": p["id"], "name": p["name"], "balance": bal})
        debtors.sort(key=lambda d: -d["balance"])
        creditors.sort(key=lambda d: -d["balance"])
        stale.sort(key=lambda d: -d["days"])
        out["debtors"] = debtors[:10]
        out["creditors"] = creditors[:10]
        out["alerts"] = {"over_limit": over_limit, "stale": stale[:10]}
        conn.close()
        return 200, out

    def backup(q, user):
        dest = do_backup()
        return 200, {"ok": True, "file": dest}

    # ---------- updates ----------
    def version(q):
        return 200, {"version": APP_VERSION, "update_configured": bool(read_update_url())}

    def check_update(q, user):
        if not read_update_url():
            return 200, {"configured": False}
        info, kind, detail = fetch_update_info()
        if info is None:
            return 200, {"configured": True, "online": False, "error": detail,
                         "message": UPDATE_ERROR_KIND.get(kind, UPDATE_ERROR_KIND["noinet"]),
                         "current": APP_VERSION}
        latest = str(info.get("version", ""))
        return 200, {"configured": True, "online": True, "current": APP_VERSION, "latest": latest,
                     "update_available": version_tuple(latest) > version_tuple(APP_VERSION),
                     "notes": info.get("notes", ""), "has_zip": bool(info.get("zip_url"))}

    def do_update(body, user):
        info, kind, detail = fetch_update_info()
        if info is None:
            return 400, {"error": UPDATE_ERROR_KIND.get(kind, "تعذر الاتصال بخادم التحديثات")}
        latest = str(info.get("version", ""))
        if not (version_tuple(latest) > version_tuple(APP_VERSION)):
            return 400, {"error": "no newer version"}
        zip_url = info.get("zip_url")
        if not zip_url:
            return 400, {"error": "no zip url"}
        # safety: backup the database before touching anything
        try:
            do_backup()
        except Exception:
            pass
        os.makedirs(UPDATES_DIR, exist_ok=True)
        # clean old update packages
        for fn in os.listdir(UPDATES_DIR):
            if fn.startswith("nixt-update-") and fn.endswith(".zip"):
                try:
                    os.remove(os.path.join(UPDATES_DIR, fn))
                except OSError:
                    pass
        zip_path = os.path.join(UPDATES_DIR, "nixt-update-%s.zip" % latest)
        req = urllib.request.Request(zip_url, headers={"User-Agent": "NixtAccounts/" + APP_VERSION})
        with urllib.request.urlopen(req, timeout=120) as r, open(zip_path, "wb") as f:
            shutil.copyfileobj(r, f)
        parent = os.path.dirname(BASE_DIR.rstrip(os.sep)) or BASE_DIR
        target = os.path.join(parent, "NixtAccounts-%s" % latest)
        if os.path.exists(target):
            shutil.rmtree(target, ignore_errors=True)
        os.makedirs(target, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            top = names[0].split("/")[0] if names else ""
            strip = top + "/" if all(n.startswith(top + "/") or n == top for n in names) else ""
            for n in names:
                rel = n[len(strip):] if strip and n.startswith(strip) else n
                if not rel or rel.endswith("/"):
                    continue
                low = rel.replace("\\", "/").lower()
                if low.startswith("data/") or low.startswith("backups/") or low == "data" or low == "backups":
                    continue
                dest = os.path.join(target, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(n) as srcf, open(dest, "wb") as outf:
                    shutil.copyfileobj(srcf, outf)
        for folder in ("data", "backups"):
            src_d = os.path.join(BASE_DIR, folder)
            dst_d = os.path.join(target, folder)
            if os.path.isdir(src_d):
                shutil.copytree(src_d, dst_d, dirs_exist_ok=True)
        # handoff: start the new copy, clean stale pythonw, then retire this folder
        old_dir = BASE_DIR.rstrip(os.sep)
        retired = os.path.join(parent, "_old-" + os.path.basename(old_dir))
        bat = os.path.join(parent, "nixt-run-new.bat")
        new_start = os.path.join(target, "START.bat")
        with open(bat, "w") as f:
            f.write('@echo off\r\n'
                    'cd /d "%TEMP%"\r\n'
                    'ping -n 5 127.0.0.1 >nul\r\n'
                    'taskkill /F /IM pythonw.exe >nul 2>&1\r\n'
                    'start "" "%s"\r\n'
                    'ping -n 6 127.0.0.1 >nul\r\n'
                    'if exist "%s" rmdir /s /q "%s" >nul 2>&1\r\n'
                    'ren "%s" "%s" >nul 2>&1\r\n'
                    'del "%%~f0" >nul 2>&1\r\n'
                    % (new_start, retired, retired, old_dir, os.path.basename(retired)))

        def _handoff():
            try:
                subprocess.Popen(["cmd", "/c", bat], cwd=os.environ.get("TEMP", parent))
            except Exception:
                pass
            os._exit(0)

        threading.Timer(1.0, _handoff).start()
        return 200, {"ok": True, "new_folder": target, "version": latest}


# ---------------- import ----------------
def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def medo_parties(data):
    """Extract parties from a MeDo FULL_SNAPSHOT_JSON export."""
    d = data.get("data", data)
    clients, suppliers = [], []
    for c in d.get("accounts_clients", []):
        clients.append({
            "name": (c.get("name") or "").strip(), "code": c.get("code") or "",
            "phone": c.get("phone") or "", "address": c.get("address") or "",
            "discount_pct": _f(c.get("discount_percent")),
            "credit_limit": _f(c.get("debt_limit")),
        })
    for s in d.get("suppliers", []):
        suppliers.append({
            "name": (s.get("name") or "").strip(), "code": "",
            "phone": s.get("phone") or "", "address": s.get("address") or "",
            "group_name": s.get("category") or "",
            "opening": _f(s.get("current_balance")),
        })
    return clients, suppliers


def api_import_medo(body, user):
    mode = body.get("mode", "preview")
    data = body.get("data")
    if not isinstance(data, dict):
        return 400, {"error": "ملف غير صالح"}
    clients, suppliers = medo_parties(data)
    clients = [c for c in clients if c["name"]]
    suppliers = [s for s in suppliers if s["name"]]
    conn = get_db()
    existing = {r["name"].strip(): r["id"] for r in conn.execute("SELECT id, name FROM customers")}
    if mode == "preview":
        dup_c = sum(1 for c in clients if c["name"] in existing)
        dup_s = sum(1 for s in suppliers if s["name"] in existing)
        conn.close()
        return 200, {
            "customers": len(clients), "suppliers": len(suppliers),
            "customers_dupes": dup_c, "suppliers_dupes": dup_s,
            "suppliers_balance_total": round(sum(s["opening"] for s in suppliers), 2),
            "note": "أرصدة العملاء الافتتاحية تُدخل لاحقًا من قائمة أرصدة MeDo (Excel/CSV). "
                    "التصدير لا يحمل أرصدة العملاء.",
        }
    ins_c = ins_s = skip = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for c in clients:
        if c["name"] in existing:
            skip += 1
            continue
        conn.execute(
            "INSERT INTO customers (name, phone, group_name, discount_pct, notes, kind, code, address,"
            " credit_limit, opening, opening_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (c["name"], c["phone"], "", c["discount_pct"], "مستورد من MeDo", "customer",
             c["code"], c["address"], c["credit_limit"], 0, "", now_str()))
        ins_c += 1
    for s in suppliers:
        if s["name"] in existing:
            skip += 1
            continue
        conn.execute(
            "INSERT INTO customers (name, phone, group_name, discount_pct, notes, kind, code, address,"
            " credit_limit, opening, opening_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["name"], s["phone"], s["group_name"], 0, "مستورد من MeDo", "supplier",
             "", s["address"], 0, s["opening"], today, now_str()))
        ins_s += 1
    conn.execute("INSERT INTO imports (kind, filename, summary, created_at) VALUES (?,?,?,?)",
                 ("medo", body.get("filename", ""),
                  "عملاء جدد: %d، موردون جدد: %d، تم تخطي (موجودون): %d" % (ins_c, ins_s, skip),
                  now_str()))
    conn.commit()
    conn.close()
    return 200, {"ok": True, "customers_added": ins_c, "suppliers_added": ins_s, "skipped": skip}


def parse_csv_text(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows_out = []
    for r in csv.reader(io.StringIO(text), dialect):
        rows_out.append([c.strip() for c in r])
    return [r for r in rows_out if any(r)]


def parse_xlsx_b64(b64):
    """Minimal xlsx reader (first sheet)."""
    raw = base64.b64decode(b64)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(ns + "si"):
                shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in z.namelist():
            sheet_name = sorted(n for n in z.namelist()
                                if n.startswith("xl/worksheets/") and n.endswith(".xml"))[0]
        root = ET.fromstring(z.read(sheet_name))
        out = []
        for row in root.iter(ns + "row"):
            vals = []
            for c in row.findall(ns + "c"):
                v = c.find(ns + "v")
                val = v.text if v is not None else ""
                if c.get("t") == "s" and val != "":
                    val = shared[int(val)]
                elif c.get("t") == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(ns + "t"))
                vals.append((val or "").strip())
            out.append(vals)
        return [r for r in out if any(r)]


PARTY_FIELDS = ["name", "phone", "code", "group_name", "address", "discount_pct",
                "credit_limit", "opening"]
PARTY_FIELD_LABELS = {"name": "الاسم", "phone": "التليفون", "code": "الكود",
                      "group_name": "المجموعة/الفئة", "address": "العنوان",
                      "discount_pct": "خصم %", "credit_limit": "حد الائتمان",
                      "opening": "الرصيد الافتتاحي"}


def api_import_parties(body, user):
    mode = body.get("mode", "preview")
    kind = body.get("kind") if body.get("kind") in ("customer", "supplier") else "customer"
    if body.get("b64"):
        table = parse_xlsx_b64(body["b64"])
    else:
        table = parse_csv_text(body.get("text") or "")
    if not table:
        return 400, {"error": "لم يتم العثور على صفوف في الملف"}
    headers = table[0]
    data_rows = table[1:]
    if mode == "preview":
        # suggested mapping by header similarity
        hints = {"name": ["اسم", "name", "client", "عميل", "مورد"],
                 "opening": ["رصيد", "balance", "مديونية", "متبقي"],
                 "phone": ["تليفون", "هاتف", "phone", "موبايل"],
                 "discount_pct": ["خصم", "discount"],
                 "credit_limit": ["حد", "limit", "ائتمان"],
                 "code": ["كود", "code"]}
        suggested = {}
        for f in PARTY_FIELDS:
            for i, h in enumerate(headers):
                hl = (h or "").lower()
                if any(k in hl for k in hints.get(f, [])):
                    suggested[f] = i
                    break
        return 200, {"headers": headers, "sample": data_rows[:5],
                     "rows": len(data_rows), "suggested": suggested,
                     "fields": PARTY_FIELD_LABELS}
    # commit
    mapping = body.get("mapping") or {}
    if mapping.get("name") is None:
        return 400, {"error": "حدد عمود الاسم"}
    conn = get_db()
    existing = {r["name"].strip(): r["id"]
                for r in conn.execute("SELECT id, name FROM customers WHERE kind=?", (kind,))}
    ins = upd = skip = 0
    today = datetime.now().strftime("%Y-%m-%d")
    report_rows = []
    for r in data_rows:
        def col(f):
            i = mapping.get(f)
            return r[i] if (i is not None and i < len(r)) else ""
        name = col("name").strip()
        if not name:
            skip += 1
            continue
        opening = _f(col("opening"))
        if name in existing:
            # existing party: update opening balance only (balance-entry use case)
            if mapping.get("opening") is not None:
                conn.execute("UPDATE customers SET opening=?, opening_date=? WHERE id=?",
                             (opening, today, existing[name]))
                upd += 1
                report_rows.append("تحديث رصيد: " + name)
            else:
                skip += 1
            continue
        conn.execute(
            "INSERT INTO customers (name, phone, group_name, discount_pct, notes, kind, code, address,"
            " credit_limit, opening, opening_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, col("phone"), col("group_name"), _f(col("discount_pct")), "مستورد من ملف",
             kind, col("code"), col("address"), _f(col("credit_limit")), opening, today, now_str()))
        ins += 1
    conn.execute("INSERT INTO imports (kind, filename, summary, created_at) VALUES (?,?,?,?)",
                 ("parties-" + kind, body.get("filename", ""),
                  "جديد: %d، تحديث رصيد: %d، تم تخطي: %d" % (ins, upd, skip), now_str()))
    conn.commit()
    conn.close()
    return 200, {"ok": True, "added": ins, "updated": upd, "skipped": skip}


def _num_ar(s):
    s = (s or "").strip().replace(",", "").replace("٬", "")
    if s in ("", "-", "لا يوجد"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _date_ar(s):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


def _find_col(headers, *names):
    for i, h in enumerate(headers):
        for n in names:
            if n in (h or ""):
                return i
    return None


def api_import_medo_full(body, user):
    """Import official MeDo balances + line-by-line statements (customer side)."""
    mode = body.get("mode", "preview")
    bal_text = body.get("balances_text") or ""
    st_text = body.get("statements_text") or ""
    bal_rows = parse_csv_text(bal_text) if bal_text.strip() else []
    st_rows = parse_csv_text(st_text) if st_text.strip() else []
    balances = {}
    if bal_rows:
        h = bal_rows[0]
        ci = {"code": _find_col(h, "الكود"), "name": _find_col(h, "اسم العميل", "الاسم"),
              "phone": _find_col(h, "التليفون", "التليفون"), "address": _find_col(h, "العنوان"),
              "disc": _find_col(h, "نسبة الخصم", "الخصم"), "limit": _find_col(h, "حد المديونية", "حد الائتمان"),
              "bal": _find_col(h, "الرصيد المستحق", "الرصيد")}
        for r in bal_rows[1:]:
            def g(k):
                i = ci[k]
                return r[i] if (i is not None and i < len(r)) else ""
            name = g("name").strip()
            if not name:
                continue
            balances[g("code").strip() or name] = {
                "code": g("code").strip(), "name": name, "phone": g("phone").strip(),
                "address": g("address").strip(), "discount_pct": _num_ar(g("disc")),
                "credit_limit": _num_ar(g("limit")), "balance": _num_ar(g("bal"))}
    st_by_code = {}
    if st_rows:
        h2 = st_rows[0]
        si = {"code": _find_col(h2, "كود العميل", "الكود"), "name": _find_col(h2, "اسم العميل"),
              "date": _find_col(h2, "التاريخ"), "type": _find_col(h2, "النوع", "نوع الحركة"),
              "product": _find_col(h2, "المنتج", "البيان"), "pairs": _find_col(h2, "أزواج"),
              "debit": _find_col(h2, "عليه", "مدين"), "credit": _find_col(h2, "له", "دائن")}
        for r in st_rows[1:]:
            def g2(k):
                i = si[k]
                return r[i] if (i is not None and i < len(r)) else ""
            code = g2("code").strip()
            if not code:
                continue
            st_by_code.setdefault(code, {"name": g2("name").strip(), "lines": []})
            d, c = _num_ar(g2("debit")), _num_ar(g2("credit"))
            typ = g2("type").strip()
            prod = g2("product").strip()
            pairs = _num_ar(g2("pairs"))
            desc = typ + ((" - " + prod) if prod and prod != "-" else "") + \
                ((" ×%g" % pairs) if pairs else "")
            st_by_code[code]["lines"].append({
                "date": _date_ar(g2("date")), "type": typ, "desc": desc,
                "product": prod if prod != "-" else "", "pairs": pairs,
                "debit": d, "credit": c})
    # analysis
    analysis = []
    for code, b in balances.items():
        lines = st_by_code.get(code, {}).get("lines", [])
        hist = round(sum(l["debit"] - l["credit"] for l in lines), 2)
        correction = round(b["balance"] - hist, 2)
        analysis.append({"code": b["code"], "name": b["name"], "official": b["balance"],
                         "history_net": hist, "correction": correction,
                         "lines": len(lines)})
    mismatched = [a for a in analysis if abs(a["correction"]) > 0.01]
    if mode == "preview":
        return 200, {
            "customers_in_balances": len(balances),
            "customers_in_statements": len(st_by_code),
            "statement_lines": sum(len(s["lines"]) for s in st_by_code.values()),
            "total_official": round(sum(b["balance"] for b in balances.values()), 2),
            "matched": len(analysis) - len(mismatched), "mismatched": len(mismatched),
            "mismatch_list": mismatched[:30],
        }
    # commit
    conn = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    by_code = {r["code"]: r["id"] for r in conn.execute(
        "SELECT id, code FROM customers WHERE kind='customer' AND code != ''")}
    by_name = {r["name"].strip(): r["id"] for r in conn.execute(
        "SELECT id, name FROM customers WHERE kind='customer'")}
    ins = upd = 0
    for code, b in balances.items():
        pid = by_code.get(b["code"]) or by_name.get(b["name"])
        lines = st_by_code.get(code, {}).get("lines", [])
        hist = round(sum(l["debit"] - l["credit"] for l in lines), 2)
        opening = round(b["balance"] - hist, 2)
        if pid:
            conn.execute(
                "UPDATE customers SET phone=?, address=?, discount_pct=?, credit_limit=?,"
                " opening=?, opening_date=?, code=? WHERE id=?",
                (b["phone"], b["address"], b["discount_pct"], b["credit_limit"],
                 opening, today, b["code"], pid))
            upd += 1
        else:
            cur = conn.execute(
                "INSERT INTO customers (name, phone, group_name, discount_pct, notes, kind, code, address,"
                " credit_limit, opening, opening_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (b["name"], b["phone"], "", b["discount_pct"], "مستورد من MeDo", "customer",
                 b["code"], b["address"], b["credit_limit"], opening, today, now_str()))
            pid = cur.lastrowid
            ins += 1
        conn.execute("DELETE FROM ledger_entries WHERE customer_id=?", (pid,))
        for l in sorted(lines, key=lambda x: x["date"]):
            conn.execute(
                "INSERT INTO ledger_entries (customer_id, date, type, desc, product, pairs, debit, credit, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, l["date"], l["type"], l["desc"], l["product"], l["pairs"],
                 l["debit"], l["credit"], now_str()))
    conn.execute("INSERT INTO imports (kind, filename, summary, created_at) VALUES (?,?,?,?)",
                 ("medo-full", "balances+statements",
                  "عملاء: %d جديد / %d محدث، كشوف: %d سطر، غير متطابقين: %d"
                  % (ins, upd, sum(len(s["lines"]) for s in st_by_code.values()), len(mismatched)),
                  now_str()))
    conn.commit()
    conn.close()
    return 200, {"ok": True, "added": ins, "updated": upd,
                 "lines": sum(len(s["lines"]) for s in st_by_code.values()),
                 "mismatched": len(mismatched), "mismatch_list": mismatched[:30]}


# ---------------- routes ----------------
# path -> (handler, needs_admin)
ROUTES_POST = {
    "/api/setup": (lambda b, u: API.setup(b, u), False),
    "/api/login": (lambda b, u: API.login(b, u), False),
    "/api/logout": (lambda b, u: API.logout(b, u), False),
    "/api/change-password": (lambda b, u: API.change_password(b, u), False),
    "/api/customers": (lambda b, u: API.create_customer(b, u), False),
    "/api/employees": (lambda b, u: API.create_employee(b, u), False),
    "/api/invoices": (lambda b, u: API.create_invoice(b, u), False),
    "/api/payments": (lambda b, u: API.create_payment(b, u), False),
    "/api/backup": (lambda b, u: API.backup({}, u), False),
    "/api/settings": (lambda b, u: API.save_settings(b, u), True),
    "/api/users": (lambda b, u: API.create_user(b, u), True),
    "/api/import/medo": (lambda b, u: api_import_medo(b, u), True),
    "/api/import/medo-full": (lambda b, u: api_import_medo_full(b, u), True),
    "/api/import/parties": (lambda b, u: api_import_parties(b, u), True),
    "/api/do-update": (lambda b, u: API.do_update(b, u), True),
}
ROUTES_GET = {
    "/api/me": (API.me, False),
    "/api/login_users": (API.login_users, False),
    "/api/version": (lambda q, u: API.version(q), False),
    "/api/customers": (API.list_customers, False),
    "/api/employees": (API.list_employees, False),
    "/api/invoices": (API.list_invoices, False),
    "/api/payments": (API.list_payments, False),
    "/api/statement": (API.statement, False),
    "/api/dashboard": (API.dashboard, False),
    "/api/settings": (API.get_settings, False),
    "/api/users": (API.list_users, True),
    "/api/check-update": (API.check_update, False),
}

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png",
                ".jpg": "image/jpeg", ".ico": "image/x-icon", ".css": "text/css",
                ".js": "application/javascript"}
OPEN_PATHS = {"/api/login", "/api/setup", "/api/me", "/api/version", "/api/login_users"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, status, obj, extra_headers=None):
        LAST_ACTIVITY[0] = time.time()
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, ctype):
        LAST_ACTIVITY[0] = time.time()
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _user(self):
        cookie = self.headers.get("Cookie") or ""
        token = ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("nixt_session="):
                token = part.split("=", 1)[1]
        return get_session(token) if token else None

    def _set_session(self, token):
        return {"Set-Cookie": "nixt_session=%s; HttpOnly; SameSite=Strict; Path=/" % token}

    def _clear_session(self):
        return {"Set-Cookie": "nixt_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"}

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        try:
            if path == "/" or path == "/index.html":
                return self._send_file(os.path.join(STATIC_DIR, "index.html"),
                                       "text/html; charset=utf-8")
            if path.startswith("/static/"):
                rel = path[len("/static/"):]
                if "/" in rel or ".." in rel:
                    return self._send(404, {"error": "not found"})
                fp = os.path.join(STATIC_DIR, rel)
                ext = os.path.splitext(rel)[1].lower()
                if os.path.isfile(fp) and ext in STATIC_TYPES:
                    return self._send_file(fp, STATIC_TYPES[ext])
                return self._send(404, {"error": "not found"})
            user = self._user()
            if path in OPEN_PATHS:
                if path in ROUTES_GET:
                    fn, _adm = ROUTES_GET[path]
                    return self._send(*fn(q, user))
            if not user:
                return self._send(401, {"error": "login_required"})
            if path in ROUTES_GET:
                fn, adm = ROUTES_GET[path]
                if adm and user["role"] != "admin":
                    return self._send(403, {"error": "admin_required"})
                return self._send(*fn(q, user))
            if path.startswith("/api/invoices/"):
                return self._send(*API.get_invoice(int(path.rsplit("/", 1)[1]), user))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        try:
            user = self._user()
            if path == "/api/logout":
                cookie = self.headers.get("Cookie") or ""
                for part in cookie.split(";"):
                    part = part.strip()
                    if part.startswith("nixt_session="):
                        SESSIONS.pop(part.split("=", 1)[1], None)
                return self._send(200, {"ok": True}, self._clear_session())
            if path in OPEN_PATHS and path in ROUTES_POST:
                fn, _adm = ROUTES_POST[path]
                status, obj = fn(body, user)
                hdrs = self._set_session(obj["token"]) if status == 200 and obj.get("token") else None
                obj = {k: v for k, v in obj.items() if k != "token"}
                return self._send(status, obj, hdrs)
            if not user:
                return self._send(401, {"error": "login_required"})
            if path in ROUTES_POST:
                fn, adm = ROUTES_POST[path]
                if adm and user["role"] != "admin":
                    return self._send(403, {"error": "admin_required"})
                return self._send(*fn(body, user))
            if path.startswith("/api/employees/") and path.endswith("/toggle"):
                return self._send(*API.toggle_employee(int(path.split("/")[3]), user))
            if path.startswith("/api/customers/"):
                return self._send(*API.update_customer(int(path.rsplit("/", 1)[1]), body, user))
            if path.startswith("/api/users/") and path.endswith("/reset"):
                if user["role"] != "admin":
                    return self._send(403, {"error": "admin_required"})
                uid = int(path.strip("/").split("/")[2])
                return self._send(*API.reset_user(uid, body, user))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_DELETE(self):
        u = urlparse(self.path)
        try:
            user = self._user()
            if not user:
                return self._send(401, {"error": "login_required"})
            if user["role"] != "admin":
                return self._send(403, {"error": "admin_required"})
            parts = u.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api":
                if parts[1] == "invoices":
                    return self._send(*API.delete_invoice(int(parts[2])))
                if parts[1] == "payments":
                    return self._send(*API.delete_payment(int(parts[2])))
                if parts[1] == "customers":
                    return self._send(*API.delete_customer(int(parts[2])))
                if parts[1] == "users":
                    return self._send(*API.delete_user(int(parts[2])))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})


def port_busy():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((HOST, PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


APP_WINDOW_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

LAST_ACTIVITY = [0.0]


def open_app_window():
    """Open the app in a standalone browser window (no tabs/address bar).
    Returns the process handle, or None to fall back to the default browser."""
    url = "http://%s:%d/" % (HOST, PORT)
    candidates = [c for c in APP_WINDOW_BROWSERS if os.path.exists(c)]
    if not candidates:
        for name in ("msedge.exe", "chrome.exe"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
                break
    if not candidates:
        return None
    try:
        return subprocess.Popen([candidates[0], "--app=" + url])
    except Exception:
        return None


def open_browser():
    try:
        webbrowser.open("http://%s:%d/" % (HOST, PORT))
    except Exception:
        pass


def watchdog_loop(server):
    """Stop the server shortly after the last open window stops talking to us,
    so closing the app window never leaves a zombie server behind."""
    while True:
        time.sleep(15)
        last = LAST_ACTIVITY[0]
        if last and time.time() - last > 180:
            try:
                server.shutdown()
            except Exception:
                pass
            return


def auto_backup_loop():
    while True:
        time.sleep(3600)
        auto_backup_tick()


def running_server_version():
    try:
        req = urllib.request.Request(
            "http://%s:%d/api/version" % (HOST, PORT),
            headers={"User-Agent": "NixtAccounts/" + APP_VERSION})
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("version") or ""
    except Exception:
        return None


def server_serves_index():
    try:
        with urllib.request.urlopen("http://%s:%d/" % (HOST, PORT), timeout=2) as r:
            html = r.read().decode("utf-8", "ignore")
        return "نيكست" in html or "nixt" in html.lower()
    except Exception:
        return False


def show_message(text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "نظام نيكست للحسابات", 0x40)
    except Exception:
        pass


def main():
    init_db()
    if port_busy():
        ver = running_server_version()
        if ver == APP_VERSION and server_serves_index():
            if open_app_window() is None:
                open_browser()
        elif ver and ver != APP_VERSION:
            show_message(
                "فيه نسخة أقدم من البرنامج (إصدار " + ver + ") لسه شغالة في الخلفية.\n\n"
                + "افتح Task Manager بـ Ctrl+Shift+Esc واعمل End task لـ pythonw.exe، "
                + "أو اعمل Restart للجهاز، وبعدين شغّل البرنامج تاني.")
        else:
            show_message(
                "فيه نسخة قديمة من البرنامج معلّقة أو برنامج تاني مسك منفذ التشغيل.\n\n"
                + "اعمل Restart للجهاز وبعدين شغّل البرنامج تاني.")
        return
    auto_backup_tick()
    threading.Thread(target=auto_backup_loop, daemon=True).start()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        show_message(
            "البرنامج مش قادر يفتح منفذ التشغيل.\n\n"
            + "اعمل Restart للجهاز وبعدين شغّل البرنامج تاني.")
        return

    def launch_window():
        time.sleep(1.0)
        if open_app_window() is None:
            open_browser()

    threading.Thread(target=launch_window, daemon=True).start()
    threading.Thread(target=watchdog_loop, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
