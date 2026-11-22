#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nixt Accounts - offline desktop accounts system (Arabic UI).
Single-file stdlib server: http.server + sqlite3. No internet needed.
Data lives in a stable per-user folder (%APPDATA%/NixtAccounts on Windows),
so updates never touch it. Backups live next to the database.
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
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_VERSION = "1.5.1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def default_data_root():
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "NixtAccounts")
    return os.path.join(os.path.expanduser("~"), ".nixt-accounts")


DATA_ROOT = os.environ.get("NIXT_DATA_DIR", default_data_root())
DATA_DIR = os.path.join(DATA_ROOT, "data")
BACKUP_DIR = os.path.join(DATA_ROOT, "backups")
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
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  code TEXT DEFAULT '',
  retail_price REAL DEFAULT 0,
  active INTEGER DEFAULT 1,
  created_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS treasury (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'in',
  category TEXT DEFAULT '',
  amount REAL DEFAULT 0,
  notes TEXT DEFAULT '',
  source TEXT DEFAULT 'manual',
  source_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cheques (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  payment_id INTEGER,
  due_date TEXT NOT NULL,
  amount REAL DEFAULT 0,
  bank TEXT DEFAULT '',
  cheque_number TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customer_prices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  item_name TEXT NOT NULL,
  price REAL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(customer_id, item_name)
);
CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  username TEXT DEFAULT '',
  action TEXT NOT NULL,
  details TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cheque_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cheque_id INTEGER, ts TEXT, event TEXT, detail TEXT
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
    ("payments", "cheque", "ALTER TABLE payments ADD COLUMN cheque REAL DEFAULT 0"),
    ("invoices", "reason", "ALTER TABLE invoices ADD COLUMN reason TEXT DEFAULT ''"),
    ("cheques", "endorsed_to", "ALTER TABLE cheques ADD COLUMN endorsed_to INTEGER"),
    ("cheques", "endorsed_date", "ALTER TABLE cheques ADD COLUMN endorsed_date TEXT DEFAULT ''"),
    ("cheques", "endorsed_note", "ALTER TABLE cheques ADD COLUMN endorsed_note TEXT DEFAULT ''"),
    ("cheques", "endorsement_payment_id", "ALTER TABLE cheques ADD COLUMN endorsement_payment_id INTEGER"),
    ("invoices", "return_of", "ALTER TABLE invoices ADD COLUMN return_of INTEGER"),
    ("payments", "vcash", "ALTER TABLE payments ADD COLUMN vcash REAL DEFAULT 0"),
    ("treasury", "method", "ALTER TABLE treasury ADD COLUMN method TEXT DEFAULT 'cash'"),
]

DEFAULT_SETTINGS = {
    "company_name": "نيكست للجلديات - NIXT FOR LEATHER",
    "company_phone": "",
    "company_address": "",
    "alert_days": "30",
    "backup_dir": "",
    "login_show_users": "1",
    "sync_dir": "",
    "device_name": "",
    "wholesale_divisor": "1.22",
    "colors": "أسود,بني,هافان,أبيض,كحلي,رمادي,تانجو",
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


def legacy_db_candidates():
    """Old installs kept data inside the program folder. Collect every
    data/nixt.db we can still find: this folder, and sibling folders from
    previous versions / retired update folders."""
    cands = []

    def add(p):
        p = os.path.normpath(p)
        if os.path.isfile(p) and p not in cands:
            cands.append(p)

    add(os.path.join(BASE_DIR, "data", "nixt.db"))
    parent = os.path.dirname(BASE_DIR.rstrip(os.sep)) or BASE_DIR
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        names = []
    for name in names:
        low = name.lower()
        if low.startswith("nixtaccounts") or low.startswith("_old-nixt"):
            add(os.path.join(parent, name, "data", "nixt.db"))
    return cands


def db_data_score(path):
    """Higher = more real business data. Used to pick the right legacy db."""
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error:
        return -1
    score = 0
    for t in ("customers", "suppliers", "invoices", "payments",
              "treasury_movements", "cheques", "users"):
        try:
            score += conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except sqlite3.Error:
            pass
    conn.close()
    return score


def migrate_legacy_data():
    """First run from the new stable location: adopt the richest old
    database found next to the program, plus its backups. Never runs when
    a database already exists in the stable location."""
    if os.path.exists(DB_PATH):
        return None
    best, best_key = None, (-1, 0)
    for path in legacy_db_candidates():
        score = db_data_score(path)
        if score < 0:
            continue
        try:
            key = (score, int(os.path.getmtime(path)))
        except OSError:
            continue
        if key > best_key:
            best, best_key = path, key
    if not best:
        return None
    os.makedirs(DATA_DIR, exist_ok=True)
    src = sqlite3.connect(best)
    dst = sqlite3.connect(DB_PATH)
    src.backup(dst)
    dst.close()
    src.close()
    legacy_backups = os.path.join(os.path.dirname(os.path.dirname(best)), "backups")
    if os.path.isdir(legacy_backups):
        try:
            shutil.copytree(legacy_backups, BACKUP_DIR, dirs_exist_ok=True)
        except OSError:
            pass
    return best


MIGRATED_FROM = migrate_legacy_data()


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
    if MIGRATED_FROM:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
                     ("migrated_from", MIGRATED_FROM))
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


def log_action(conn, user, action, details=""):
    try:
        conn.execute("INSERT INTO activity_log (ts, username, action, details) VALUES (?,?,?,?)",
                     (now_str(), (user or {}).get("username", ""), action, details))
    except sqlite3.Error:
        pass


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
        total = (pm["cash"] or 0) + (pm["card"] or 0) + (pm["transfer"] or 0) + (pm["cheque"] or 0) + (pm["vcash"] or 0)
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



# ---------------- sync (via Google Drive desktop folder) ----------------
def device_name(conn=None):
    close = False
    if conn is None:
        conn, close = get_db(), True
    name = get_setting(conn, "device_name", "").strip()
    if close:
        conn.close()
    if name:
        return name
    import platform
    return platform.node() or "جهاز"


def detect_sync_dir():
    up = os.environ.get("USERPROFILE", "")
    home = os.path.expanduser("~")
    cands = [os.path.join(up, "Google Drive", "My Drive"),
             os.path.join(up, "My Drive"),
             "G:\\My Drive",
             os.path.join(home, "Google Drive", "My Drive"),
             os.path.join(home, "My Drive")]
    for base in cands:
        if base and os.path.isdir(base):
            return os.path.join(base, "NixtSync")
    return ""


def sync_dir(conn=None):
    close = False
    if conn is None:
        conn, close = get_db(), True
    d = get_setting(conn, "sync_dir", "").strip() or detect_sync_dir()
    if close:
        conn.close()
    return d


def sync_remote_meta(sdir):
    try:
        with open(os.path.join(sdir, "nixt-sync-meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def db_mtime_iso():
    try:
        return datetime.utcfromtimestamp(os.path.getmtime(DB_PATH)).strftime("%Y-%m-%dT%H:%M:%S")
    except OSError:
        return ""


def sync_snapshot_path(sdir, ts, dev):
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in dev)
    return os.path.join(sdir, "nixt-sync-%s-%s.db" % (ts.replace(":", "").replace("-", ""), safe))


def sync_status_payload():
    conn = get_db()
    sdir = sync_dir(conn)
    last = get_setting(conn, "sync_last", "")
    dev = device_name(conn)
    migrated = get_setting(conn, "migrated_from", "")
    conn.close()
    out = {"sync_dir": sdir, "device": dev, "last_sync": last,
           "configured": bool(sdir), "data_dir": DATA_DIR,
           "migrated_from": migrated}
    if not sdir:
        out["state"] = "not_configured"
        return out
    if not os.path.isdir(sdir):
        try:
            os.makedirs(sdir, exist_ok=True)
        except OSError:
            out["state"] = "dir_missing"
            return out
    remote = sync_remote_meta(sdir)
    local_ts = db_mtime_iso()
    out["local_ts"] = local_ts
    out["remote"] = remote
    if not remote:
        out["state"] = "local_only"
        return out
    rts = remote.get("ts", "")
    if last and rts > last and local_ts > last:
        out["state"] = "conflict"
    elif rts > local_ts and remote.get("device") != dev:
        out["state"] = "remote_newer"
    elif local_ts > rts:
        out["state"] = "local_newer"
    else:
        out["state"] = "up_to_date"
    return out


def do_sync_push():
    sdir = sync_dir()
    if not sdir:
        return None, "حدد فولدر المزامنة من الإعدادات الأول"
    os.makedirs(sdir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    dev = device_name()
    dest = sync_snapshot_path(sdir, ts, dev)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.close()
    src.close()
    import hashlib as _hl
    h = _hl.sha1()
    with open(dest, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    meta = {"ts": ts, "device": dev, "size": os.path.getsize(dest),
            "sha1": h.hexdigest(), "file": os.path.basename(dest),
            "app_version": APP_VERSION}
    with open(os.path.join(sdir, "nixt-sync-meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    # keep the newest 5 snapshots
    snaps = sorted(fn for fn in os.listdir(sdir)
                   if fn.startswith("nixt-sync-") and fn.endswith(".db"))
    for old in snaps[:-5]:
        try:
            os.remove(os.path.join(sdir, old))
        except OSError:
            pass
    conn = get_db()
    conn.execute("INSERT INTO settings (key, value) VALUES ('sync_last', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (ts,))
    conn.commit()
    conn.close()
    return meta, None


def do_sync_pull():
    sdir = sync_dir()
    meta = sync_remote_meta(sdir) if sdir else None
    if not meta:
        return None, "مفيش نسخة مرفوعة في فولدر المزامنة"
    src_path = os.path.join(sdir, meta.get("file", ""))
    if not os.path.isfile(src_path):
        return None, "ملف النسخة مش موجود في الفولدر (يمدّي لسه بيتحمّل من جوجل درايف)"
    tmp = os.path.join(DATA_DIR, "sync-incoming.db")
    shutil.copyfile(src_path, tmp)
    try:
        chk = sqlite3.connect(tmp)
        ok = chk.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        chk.close()
    except sqlite3.Error:
        ok = False
    if not ok:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None, "النسخة الموجودة في الفولدر مش سليمة - جرب تاني بعد ما جوجل درايف يكمّل تحميل"
    do_backup()
    src = sqlite3.connect(tmp)
    dst = sqlite3.connect(DB_PATH)
    src.backup(dst)
    src.close()
    dst.close()
    try:
        os.remove(tmp)
    except OSError:
        pass
    conn = get_db()
    conn.execute("INSERT INTO settings (key, value) VALUES ('sync_last', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (meta["ts"],))
    conn.commit()
    conn.close()
    return meta, None


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
        log_action(conn, user, "طرف جديد",
                   ("%s - %s" % ("عميل" if kind == "customer" else "مورد",
                                 (body.get("name") or "").strip())))
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
        if kind in ("return", "purchase_return"):
            if body.get("reason"):
                conn.execute("UPDATE invoices SET reason=? WHERE id=?", (body.get("reason", ""), iid))
            if body.get("return_of"):
                conn.execute("UPDATE invoices SET return_of=? WHERE id=?", (int(body["return_of"]), iid))
        for it in body.get("items", []):
            if not (it.get("item_name") or "").strip():
                continue
            conn.execute(
                "INSERT INTO invoice_items (invoice_id, item_name, color, pairs, price) VALUES (?,?,?,?,?)",
                (iid, it["item_name"].strip(), it.get("color", ""),
                 float(it.get("pairs") or 0), float(it.get("price") or 0)))
        net0 = invoice_net(iid, conn)
        log_action(conn, user, "فاتورة جديدة" if kind in ("sale", "purchase") else "مرتجع جديد",
                   "رقم %s - صافي %s" % (iid, round(net0, 2)))
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
            p["total"] = round((p["cash"] or 0) + (p["card"] or 0) + (p["transfer"] or 0) + (p["cheque"] or 0) + (p["vcash"] or 0), 2)
        conn.close()
        return 200, out

    def create_payment(body, user):
        cid = body.get("customer_id")
        if not cid:
            return 400, {"error": "اختر الطرف أولاً"}
        conn = get_db()
        party = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        direction = body.get("direction") or ("out" if (party and party["kind"] == "supplier") else "in")
        cash = float(body.get("cash") or 0)
        card = float(body.get("card") or 0)
        transfer = float(body.get("transfer") or 0)
        vcash = float(body.get("vcash") or 0)
        cheque = float(body.get("cheque") or 0)
        pdate = body.get("date") or datetime.now().strftime("%Y-%m-%d")
        cur = conn.execute(
            "INSERT INTO payments (customer_id, date, cash, card, transfer, cheque, vcash, notes, direction, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, pdate, cash, card, transfer, cheque, vcash, body.get("notes", ""), direction, now_str()))
        pid = cur.lastrowid
        if cheque > 0:
            due = (body.get("cheque_due") or "").strip()
            if not due:
                conn.close()
                return 400, {"error": "اكتب تاريخ استحقاق الشيك"}
            conn.execute(
                "INSERT INTO cheques (customer_id, payment_id, due_date, amount, bank, cheque_number, notes, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (cid, pid, due, cheque, body.get("cheque_bank", ""),
                 body.get("cheque_number", ""), body.get("notes", ""), now_str()))
            chk_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ev0 = "إصدار الشيك" if (party and party["kind"] == "supplier") else "استلام الشيك"
            conn.execute("INSERT INTO cheque_events (cheque_id, ts, event, detail) VALUES (?,?,?,?)",
                         (chk_id, now_str(), ev0,
                          "%s - %s - سند رقم %s - الاستحقاق %s" % (ev0, party["name"] if party else "", pid, due)))
        instant = cash + card + transfer + vcash
        base_cat = "تحصيل من عميل" if direction == "in" else "دفع لمورد"
        for m, amt, lbl in (("cash", cash, "كاش"), ("card", card, "شبكة"),
                            ("transfer", transfer, "تحويل بنكي"), ("vcash", vcash, "فودافون كاش")):
            if amt > 0:
                conn.execute(
                    "INSERT INTO treasury (date, kind, category, amount, notes, source, source_id, method, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (pdate, direction, base_cat + " - " + lbl, amt, body.get("notes", ""), "payment", pid, m, now_str()))
        name = party["name"] if party else ""
        log_action(conn, user, "دفعة جديدة",
                   "%s - %s - إجمالي %s" % (name, pdate, round(instant + cheque, 2)))
        conn.commit()
        conn.close()
        return 200, {"id": pid}

    def delete_payment(pid, user=None):
        conn = get_db()
        pm = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        conn.execute("DELETE FROM payments WHERE id=?", (pid,))
        conn.execute("DELETE FROM cheques WHERE payment_id=?", (pid,))
        conn.execute("DELETE FROM treasury WHERE source='payment' AND source_id=?", (pid,))
        if pm:
            cust = conn.execute("SELECT name FROM customers WHERE id=?", (pm["customer_id"],)).fetchone()
            log_action(conn, user, "حذف دفعة",
                       "%s - %s" % (cust["name"] if cust else pm["customer_id"], pm["date"]))
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
            total = (p["cash"] or 0) + (p["card"] or 0) + (p["transfer"] or 0) + (p["cheque"] or 0) + (p["vcash"] or 0)
            parts = []
            if p["cash"]:
                parts.append("كاش")
            if p["card"]:
                parts.append("شبكة")
            if p["transfer"]:
                parts.append("تحويل")
            if p["vcash"]:
                parts.append("فودافون كاش")
            if p["cheque"]:
                parts.append("شيك")
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
        dfrom = q.get("from", [""])[0]
        dto = q.get("to", [""])[0]
        if dfrom or dto:
            within, before = [], []
            for l in lines:
                d = l["date"] or ""
                if l["type"] == "opening" or (dfrom and d < dfrom):
                    before.append(l)
                elif dto and d > dto:
                    continue
                else:
                    within.append(l)
            carried = 0.0
            for l in before:
                carried += (l["debit"] - l["credit"]) if not is_supplier else (l["credit"] - l["debit"])
            if abs(carried) > 0.004:
                within.insert(0, {"date": dfrom or "", "sort": "", "type": "carried", "ref": 0,
                                  "desc": "رصيد مرحّل من الفترة السابقة",
                                  "debit": round(carried, 2) if carried > 0 else 0,
                                  "credit": round(-carried, 2) if carried < 0 else 0})
            lines = within
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
        paid = conn.execute("SELECT COALESCE(SUM(cash+card+transfer+cheque+vcash),0) s FROM payments WHERE customer_id=?",
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
        soon7 = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        out["cheques_due"] = rows(conn.execute(
            "SELECT c.*, cu.name AS customer_name FROM cheques c JOIN customers cu ON cu.id=c.customer_id"
            " WHERE c.status IN ('pending','endorsed') AND c.due_date<=? ORDER BY c.due_date", (soon7,)))
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

    def wipe(body, user):
        if (body.get("confirm") or "").strip() != "تصفير":
            return 400, {"error": "اكتب كلمة تصفير في الخانة الأول"}
        try:
            backup_path = do_backup()
        except Exception as e:
            return 500, {"error": "فشل أخذ نسخة احتياطية قبل التصفير: %s" % e}
        conn = get_db()
        counts = {}
        for table in ("invoice_items", "invoices", "payments", "ledger_entries",
                      "imports", "customers", "employees"):
            counts[table] = conn.execute("SELECT COUNT(*) c FROM %s" % table).fetchone()["c"]
            conn.execute("DELETE FROM %s" % table)
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN "
                         "('invoice_items','invoices','payments','ledger_entries','imports','customers','employees')")
        except sqlite3.Error:
            pass
        conn.commit()
        conn.close()
        return 200, {"ok": True, "backup": backup_path, "removed": counts}

    def sync_status(q, user):
        return 200, sync_status_payload()

    def sync_push(body, user):
        meta, err = do_sync_push()
        if err:
            return 400, {"error": err}
        return 200, {"ok": True, "meta": meta}

    def sync_pull(body, user):
        meta, err = do_sync_pull()
        if err:
            return 400, {"error": err}
        return 200, {"ok": True, "meta": meta}

    # ---------- treasury ----------
    def list_treasury(q, user):
        month = q.get("month", [""])[0]
        conn = get_db()
        if month:
            ent = rows(conn.execute("SELECT * FROM treasury WHERE date LIKE ? ORDER BY date DESC, id DESC", (month + "%",)))
        else:
            ent = rows(conn.execute("SELECT * FROM treasury ORDER BY date DESC, id DESC LIMIT 500"))
        # party name for payment/cheque sourced entries
        pay_party = {}
        for r in conn.execute(
                "SELECT p.id AS pid, c.name AS nm FROM payments p JOIN customers c ON c.id=p.customer_id"):
            pay_party[r["pid"]] = r["nm"]
        chq_party = {}
        for r in conn.execute(
                "SELECT ch.id AS cid, c.name AS nm FROM cheques ch JOIN customers c ON c.id=ch.customer_id"):
            chq_party[r["cid"]] = r["nm"]
        for e in ent:
            e["method"] = e["method"] or "cash"
            if e["source"] == "payment":
                e["party_name"] = pay_party.get(e["source_id"], "")
            elif e["source"] == "cheque":
                e["party_name"] = chq_party.get(e["source_id"], "")
            else:
                e["party_name"] = ""
        tot_in = sum(e["amount"] for e in ent if e["kind"] == "in")
        tot_out = sum(e["amount"] for e in ent if e["kind"] == "out")
        by_method = {}
        for e in ent:
            b = by_method.setdefault(e["method"], {"in": 0.0, "out": 0.0})
            b[e["kind"]] = round(b[e["kind"]] + e["amount"], 2)
        for b in by_method.values():
            b["net"] = round(b["in"] - b["out"], 2)
        conn.close()
        return 200, {"entries": ent, "total_in": round(tot_in, 2),
                     "total_out": round(tot_out, 2), "net": round(tot_in - tot_out, 2),
                     "by_method": by_method}

    def create_treasury(body, user):
        kind = body.get("kind") if body.get("kind") in ("in", "out") else "out"
        amount = float(body.get("amount") or 0)
        if amount <= 0:
            return 400, {"error": "اكتب مبلغ صحيح"}
        conn = get_db()
        method = body.get("method") if body.get("method") in ("cash", "vcash", "transfer", "card") else "cash"
        cur = conn.execute(
            "INSERT INTO treasury (date, kind, category, amount, notes, source, method, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (body.get("date") or datetime.now().strftime("%Y-%m-%d"), kind,
             (body.get("category") or "").strip(), amount, body.get("notes", ""),
             "manual", method, now_str()))
        log_action(conn, user, "قيد خزينة",
                   "%s - %s - %s" % ("قبض" if kind == "in" else "صرف",
                                     body.get("category") or "", round(amount, 2)))
        conn.commit()
        tid = cur.lastrowid
        conn.close()
        return 200, {"id": tid}

    def delete_treasury(tid, user=None):
        conn = get_db()
        e = conn.execute("SELECT * FROM treasury WHERE id=?", (tid,)).fetchone()
        if e and e["source"] != "manual":
            conn.close()
            return 400, {"error": "القيد ده اتولّد من دفعة - احذف الدفعة نفسها"}
        conn.execute("DELETE FROM treasury WHERE id=?", (tid,))
        if e:
            log_action(conn, user, "حذف قيد خزينة", "%s - %s" % (e["category"], e["amount"]))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- cheques ----------
    def list_cheques(q, user):
        status = q.get("status", [""])[0]
        conn = get_db()
        sql = ("SELECT c.*, cu.name AS customer_name, cu.kind AS party_kind FROM cheques c "
               "JOIN customers cu ON cu.id=c.customer_id")
        args = []
        if status:
            sql += " WHERE c.status=?"
            args.append(status)
        sql += " ORDER BY c.due_date, c.id"
        out = rows(conn.execute(sql, args))
        today = datetime.now().strftime("%Y-%m-%d")
        sup_names = {}
        for c in out:
            c["overdue"] = c["status"] in ("pending", "endorsed") and c["due_date"] < today
            if c["endorsed_to"]:
                if c["endorsed_to"] not in sup_names:
                    r = conn.execute("SELECT name FROM customers WHERE id=?", (c["endorsed_to"],)).fetchone()
                    sup_names[c["endorsed_to"]] = r["name"] if r else ""
                c["endorsed_name"] = sup_names[c["endorsed_to"]]
        totals = {"in_hand": 0.0, "endorsed": 0.0, "cashed": 0.0, "bounced": 0.0}
        for c in out:
            key = {"pending": "in_hand"}.get(c["status"], c["status"])
            if key in totals:
                totals[key] = round(totals[key] + c["amount"], 2)
        conn.close()
        return 200, {"cheques": out, "totals": totals}

    def set_cheque_status(cid, body, user):
        st = body.get("status")
        if st not in ("pending", "cashed", "bounced"):
            return 400, {"error": "حالة غير صحيحة"}
        conn = get_db()
        c = conn.execute("SELECT * FROM cheques WHERE id=?", (cid,)).fetchone()
        if not c:
            conn.close()
            return 404, {"error": "not found"}
        old_status = c["status"]
        conn.execute("UPDATE cheques SET status=? WHERE id=?", (st, cid))
        if st == "cashed" and old_status != "cashed" and not c["endorsement_payment_id"]:
            # cash actually enters only when WE collect the paper ourselves
            cust = conn.execute("SELECT * FROM customers WHERE id=?", (c["customer_id"],)).fetchone()
            direction = "out" if (cust and cust["kind"] == "supplier") else "in"
            cat = "صرف شيك لمورد" if direction == "out" else "تحصيل شيك"
            conn.execute(
                "INSERT INTO treasury (date, kind, category, amount, notes, source, source_id, method, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now().strftime("%Y-%m-%d"), direction, cat, c["amount"],
                 "شيك " + (c["cheque_number"] or ""), "cheque", cid, "cheque", now_str()))
        if st == "bounced" and old_status != "bounced":
            # customer debt goes back up
            if c["payment_id"]:
                conn.execute("UPDATE payments SET cheque = MAX(0, cheque - ?) WHERE id=?",
                             (c["amount"], c["payment_id"]))
            # endorsement reversed: supplier was never really paid
            if c["endorsement_payment_id"]:
                conn.execute("DELETE FROM payments WHERE id=?", (c["endorsement_payment_id"],))
                conn.execute("UPDATE cheques SET endorsement_payment_id=NULL, endorsed_to=NULL, endorsed_date='', endorsed_note='' WHERE id=?", (cid,))
            # any treasury movement from this cheque is undone
            conn.execute("DELETE FROM treasury WHERE source='cheque' AND source_id=?", (cid,))
        ev_map = {"pending": "رجع تحت التحصيل", "cashed": "تم الصرف", "bounced": "مرتجع"}
        det = ""
        if st == "cashed" and c["endorsement_payment_id"]:
            det = "اتصرف عند المورد - مفيش حركة نقدية في الخزينة"
        elif st == "bounced":
            det = "الشيك اترجع - المديونية رجعت على العميل"
            if c["endorsement_payment_id"]:
                det += " واتلغى التظهير للمورد"
        conn.execute("INSERT INTO cheque_events (cheque_id, ts, event, detail) VALUES (?,?,?,?)",
                     (cid, now_str(), ev_map[st], det))
        log_action(conn, user, "حالة شيك",
                   "شيك %s - %s" % (c["cheque_number"] or cid, ev_map[st]))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def cheque_history(cid, user):
        conn = get_db()
        out = rows(conn.execute(
            "SELECT ts, event, detail FROM cheque_events WHERE cheque_id=? ORDER BY id", (cid,)))
        conn.close()
        return 200, out

    def endorse_cheque(cid, body, user):
        sid = body.get("supplier_id")
        if not sid:
            return 400, {"error": "اختر المورد"}
        conn = get_db()
        c = conn.execute("SELECT * FROM cheques WHERE id=?", (cid,)).fetchone()
        if not c:
            conn.close()
            return 404, {"error": "not found"}
        if c["status"] != "pending":
            conn.close()
            return 400, {"error": "التظهير متاح للشيكات تحت التحصيل فقط"}
        sup = conn.execute("SELECT * FROM customers WHERE id=? AND kind='supplier'", (sid,)).fetchone()
        if not sup:
            conn.close()
            return 400, {"error": "المورد غير موجود"}
        cust = conn.execute("SELECT * FROM customers WHERE id=?", (c["customer_id"],)).fetchone()
        edate = body.get("date") or datetime.now().strftime("%Y-%m-%d")
        note = "تظهير شيك %s من %s" % (c["cheque_number"] or cid, cust["name"] if cust else "")
        if body.get("notes"):
            note += " - " + body["notes"]
        cur = conn.execute(
            "INSERT INTO payments (customer_id, date, cash, card, transfer, cheque, notes, direction, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, edate, 0, 0, 0, c["amount"], note, "out", now_str()))
        epid = cur.lastrowid
        conn.execute("UPDATE cheques SET status='endorsed', endorsed_to=?, endorsed_date=?,"
                     " endorsed_note=?, endorsement_payment_id=? WHERE id=?",
                     (sid, edate, body.get("notes", ""), epid, cid))
        conn.execute("INSERT INTO cheque_events (cheque_id, ts, event, detail) VALUES (?,?,?,?)",
                     (cid, now_str(), "تظهير", "اتظهّر للمورد: %s - بتاريخ %s" % (sup["name"], edate)))
        log_action(conn, user, "تظهير شيك",
                   "شيك %s (%s) إلى %s" % (c["cheque_number"] or cid, round(c["amount"], 2), sup["name"]))
        conn.commit()
        conn.close()
        return 200, {"ok": True, "payment_id": epid}

    # ---------- aging ----------
    def aging(q, user):
        today = datetime.now().date()
        conn = get_db()
        out = []
        for p in conn.execute("SELECT * FROM customers WHERE kind='customer' ORDER BY name"):
            invs = rows(conn.execute(
                "SELECT * FROM invoices WHERE customer_id=? AND kind='sale' ORDER BY date, id", (p["id"],)))
            credit = (p["opening"] or 0)
            for r in conn.execute("SELECT * FROM invoices WHERE customer_id=? AND kind='return'", (p["id"],)):
                credit -= invoice_net(r["id"], conn)
            for pm in conn.execute("SELECT * FROM payments WHERE customer_id=?", (p["id"],)):
                credit -= (pm["cash"] or 0) + (pm["card"] or 0) + (pm["transfer"] or 0) + (pm["cheque"] or 0) + (pm["vcash"] or 0)
            buckets = {"b0": 0.0, "b30": 0.0, "b60": 0.0, "b90": 0.0}
            paid = -credit if credit < 0 else 0.0
            if credit > 0:
                buckets["b90"] += credit
            for inv in invs:
                net = invoice_net(inv["id"], conn)
                remaining = net
                if paid > 0:
                    use = min(paid, remaining)
                    remaining -= use
                    paid -= use
                if remaining > 0.005:
                    try:
                        age = (today - datetime.strptime(inv["date"], "%Y-%m-%d").date()).days
                    except ValueError:
                        age = 0
                    if age <= 30:
                        buckets["b0"] += remaining
                    elif age <= 60:
                        buckets["b30"] += remaining
                    elif age <= 90:
                        buckets["b60"] += remaining
                    else:
                        buckets["b90"] += remaining
            total = round(sum(buckets.values()), 2)
            if total > 0.005:
                out.append({"id": p["id"], "name": p["name"], "phone": p["phone"],
                            "credit_limit": p["credit_limit"] or 0,
                            "current": round(buckets["b0"], 2), "d30": round(buckets["b30"], 2),
                            "d60": round(buckets["b60"], 2), "d90": round(buckets["b90"], 2),
                            "total": total})
        conn.close()
        out.sort(key=lambda x: -x["d60"] - x["d90"])
        return 200, {"customers": out,
                     "totals": {"current": round(sum(c["current"] for c in out), 2),
                                "d30": round(sum(c["d30"] for c in out), 2),
                                "d60": round(sum(c["d60"] for c in out), 2),
                                "d90": round(sum(c["d90"] for c in out), 2),
                                "total": round(sum(c["total"] for c in out), 2)}}

    # ---------- activity log ----------
    def list_activity(q, user):
        conn = get_db()
        out = rows(conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT 200"))
        conn.close()
        return 200, out

    # ---------- price lists ----------
    def list_prices(q, user):
        cid = q.get("customer_id", [""])[0]
        if not cid:
            return 400, {"error": "customer_id required"}
        conn = get_db()
        out = rows(conn.execute("SELECT * FROM customer_prices WHERE customer_id=? ORDER BY item_name", (cid,)))
        conn.close()
        return 200, out

    def list_items(q, user):
        conn = get_db()
        out = rows(conn.execute("SELECT * FROM items WHERE active=1 ORDER BY name"))
        conn.close()
        return 200, out

    def save_item(body, user):
        name = (body.get("name") or "").strip()
        if not name:
            return 400, {"error": "اسم الصنف مطلوب"}
        try:
            retail = float(body.get("retail_price") or 0)
        except (TypeError, ValueError):
            return 400, {"error": "سعر المستهلك غير صحيح"}
        conn = get_db()
        conn.execute(
            "INSERT INTO items (name, code, retail_price, active, created_at) VALUES (?,?,?,1,?) "
            "ON CONFLICT(name) DO UPDATE SET code=excluded.code, retail_price=excluded.retail_price",
            (name, body.get("code", "") or "", retail, now_str()))
        conn.commit()
        iid = conn.execute("SELECT id FROM items WHERE name=?", (name,)).fetchone()["id"]
        conn.close()
        return 200, {"id": iid}

    def list_item_names(q, user):
        conn = get_db()
        names = set()
        for r in conn.execute("SELECT DISTINCT item_name FROM invoice_items"):
            if r["item_name"]:
                names.add(r["item_name"])
        for r in conn.execute("SELECT DISTINCT item_name FROM customer_prices"):
            if r["item_name"]:
                names.add(r["item_name"])
        for r in conn.execute("SELECT name FROM items WHERE active=1"):
            if r["name"]:
                names.add(r["name"])
        conn.close()
        return 200, sorted(names)

    def save_price(body, user):
        cid = body.get("customer_id")
        name = (body.get("item_name") or "").strip()
        if not cid or not name:
            return 400, {"error": "اختر العميل واكتب اسم الصنف"}
        price = float(body.get("price") or 0)
        conn = get_db()
        conn.execute("INSERT INTO customer_prices (customer_id, item_name, price, created_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(customer_id, item_name) DO UPDATE SET price=excluded.price",
                     (cid, name, price, now_str()))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def delete_price(pid, user=None):
        conn = get_db()
        conn.execute("DELETE FROM customer_prices WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- profit ----------
    def profit(q, user):
        month = q.get("month", [""])[0]
        like = month + "%" if month else "%"
        conn = get_db()
        sales = returns = 0.0
        for r in conn.execute("SELECT id, kind FROM invoices WHERE date LIKE ?", (like,)):
            net = invoice_net(r["id"], conn)
            if r["kind"] == "sale":
                sales += net
            elif r["kind"] == "return":
                returns += net
        tre = rows(conn.execute("SELECT kind, category, amount, source FROM treasury WHERE date LIKE ?", (like,)))
        conn.close()
        collected = sum(e["amount"] for e in tre if e["kind"] == "in")
        paid_suppliers = sum(e["amount"] for e in tre if e["kind"] == "out" and e["source"] in ("payment", "cheque"))
        expenses = {}
        for e in tre:
            if e["kind"] == "out" and e["source"] == "manual":
                expenses[e["category"] or "أخرى"] = expenses.get(e["category"] or "أخرى", 0) + e["amount"]
        exp_total = sum(expenses.values())
        return 200, {
            "sales": round(sales, 2), "returns": round(returns, 2),
            "net_sales": round(sales - returns, 2),
            "collected": round(collected, 2),
            "paid_suppliers": round(paid_suppliers, 2),
            "expenses": {k: round(v, 2) for k, v in sorted(expenses.items(), key=lambda x: -x[1])},
            "expenses_total": round(exp_total, 2),
            "gross": round(sales - returns - exp_total, 2),
        }


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
    "/api/import/parties": (lambda b, u: api_import_parties(b, u), True),
    "/api/do-update": (lambda b, u: API.do_update(b, u), True),
    "/api/wipe": (lambda b, u: API.wipe(b, u), True),
    "/api/sync/push": (lambda b, u: API.sync_push(b, u), True),
    "/api/treasury": (lambda b, u: API.create_treasury(b, u), False),
    "/api/prices": (lambda b, u: API.save_price(b, u), False),
    "/api/items": (lambda b, u: API.save_item(b, u), False),
    "/api/sync/pull": (lambda b, u: API.sync_pull(b, u), True),
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
    "/api/sync/status": (API.sync_status, True),
    "/api/treasury": (API.list_treasury, False),
    "/api/cheques": (API.list_cheques, False),
    "/api/aging": (API.aging, False),
    "/api/activity": (API.list_activity, True),
    "/api/prices": (API.list_prices, False),
    "/api/item-names": (API.list_item_names, False),
    "/api/items": (API.list_items, False),
    "/api/profit": (API.profit, False),
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
            if path.startswith("/api/cheques/") and path.endswith("/history"):
                return self._send(*API.cheque_history(int(path.split("/")[3]), user))
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
            if path.startswith("/api/cheques/") and path.endswith("/status"):
                return self._send(*API.set_cheque_status(int(path.split("/")[3]), body, user))
            if path.startswith("/api/cheques/") and path.endswith("/endorse"):
                return self._send(*API.endorse_cheque(int(path.split("/")[3]), body, user))
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
                    return self._send(*API.delete_payment(int(parts[2]), user))
                if parts[1] == "treasury":
                    return self._send(*API.delete_treasury(int(parts[2]), user))
                if parts[1] == "prices":
                    return self._send(*API.delete_price(int(parts[2]), user))
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


def kill_stale_server():
    """Kill pythonw.exe processes hosting an older Nixt server, excluding self."""
    my_pid = os.getpid()
    try:
        subprocess.run(["taskkill", "/F", "/IM", "pythonw.exe",
                        "/FI", "PID ne %d" % my_pid],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    for _ in range(24):
        if not port_busy():
            return True
        time.sleep(0.25)
    return not port_busy()


def main():
    init_db()
    if port_busy():
        ver = running_server_version()
        if ver == APP_VERSION and server_serves_index():
            if open_app_window() is None:
                open_browser()
            return
        if ver and version_tuple(ver) > version_tuple(APP_VERSION):
            # a NEWER copy is already running: just open its window
            if open_app_window() is None:
                open_browser()
            return
        if ver and version_tuple(ver) < version_tuple(APP_VERSION):
            # an older copy is stuck in the background: stop it and continue
            if not kill_stale_server():
                show_message(
                    "فيه نسخة أقدم من البرنامج (إصدار " + ver + ") معلّقة ومش قادر أقفلها.\n\n"
                    + "اعمل Restart للجهاز وبعدين شغّل البرنامج تاني.")
                return
        else:
            show_message(
                "فيه برنامج تاني مسك منفذ التشغيل.\n\n"
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
