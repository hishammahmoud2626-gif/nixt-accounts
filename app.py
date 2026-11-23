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
import math
import os
import re
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

try:
    import drive_sync
except Exception:
    drive_sync = None

APP_VERSION = "2.9.4"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def default_data_root():
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "NixtAccounts")
    return os.path.join(os.path.expanduser("~"), ".nixt-accounts")


DATA_ROOT = os.environ.get("NIXT_DATA_DIR", default_data_root())
DATA_DIR = os.path.join(DATA_ROOT, "data")
BACKUP_DIR = os.path.join(DATA_ROOT, "backups")


def _nixt_log_path():
    return os.path.join(DATA_ROOT, "logs", "nixt.log")


def _nixt_log(msg):
    """Append one timestamped line to the diagnostic log. Never raises."""
    try:
        d = os.path.join(DATA_ROOT, "logs")
        os.makedirs(d, exist_ok=True)
        p = _nixt_log_path()
        if os.path.exists(p) and os.path.getsize(p) > 400000:
            os.remove(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " [v" + APP_VERSION + "] " + msg + "\n")
    except Exception:
        pass
DB_PATH = os.path.join(DATA_DIR, "nixt.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
HOST = "127.0.0.1"
PORT = 8317
MOBILE_BOUND = False
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
CREATE TABLE IF NOT EXISTS stock_movements (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_name TEXT NOT NULL, material_type TEXT NOT NULL,
 color TEXT NOT NULL DEFAULT '', size TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL,
 quantity REAL NOT NULL, kind TEXT NOT NULL, date TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', invoice_id INTEGER,
 reverses_id INTEGER UNIQUE, created_at TEXT NOT NULL, username TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS stock_key ON stock_movements(item_name,material_type,color,size,unit);
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
  cost REAL DEFAULT 0,
  active INTEGER DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  date TEXT NOT NULL,
  notes TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  invoice_id INTEGER,
  number TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotation_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
  item_name TEXT NOT NULL,
  color TEXT DEFAULT '',
  pairs REAL DEFAULT 0,
  price REAL DEFAULT 0
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
    ("invoices", "stock_posted", "ALTER TABLE invoices ADD COLUMN stock_posted INTEGER DEFAULT 0"),
    ("invoice_items", "material_type", "ALTER TABLE invoice_items ADD COLUMN material_type TEXT DEFAULT ''"),
    ("invoice_items", "unit", "ALTER TABLE invoice_items ADD COLUMN unit TEXT DEFAULT 'pair'"),
    ("invoice_items", "size", "ALTER TABLE invoice_items ADD COLUMN size TEXT DEFAULT ''"),
    ("customers", "kind", "ALTER TABLE customers ADD COLUMN kind TEXT NOT NULL DEFAULT 'customer'"),
    ("customers", "code", "ALTER TABLE customers ADD COLUMN code TEXT DEFAULT ''"),
    ("customers", "address", "ALTER TABLE customers ADD COLUMN address TEXT DEFAULT ''"),
    ("customers", "credit_limit", "ALTER TABLE customers ADD COLUMN credit_limit REAL DEFAULT 0"),
    ("customers", "opening", "ALTER TABLE customers ADD COLUMN opening REAL DEFAULT 0"),
    ("customers", "opening_date", "ALTER TABLE customers ADD COLUMN opening_date TEXT DEFAULT ''"),
    ("payments", "direction", "ALTER TABLE payments ADD COLUMN direction TEXT NOT NULL DEFAULT 'in'"),
    ("payments", "cheque", "ALTER TABLE payments ADD COLUMN cheque REAL DEFAULT 0"),
    ("invoices", "reason", "ALTER TABLE invoices ADD COLUMN reason TEXT DEFAULT ''"),
    ("invoices", "number", "ALTER TABLE invoices ADD COLUMN number TEXT DEFAULT ''"),
    ("invoice_items", "retail_price", "ALTER TABLE invoice_items ADD COLUMN retail_price REAL"),
    ("items", "cost", "ALTER TABLE items ADD COLUMN cost REAL DEFAULT 0"),
    ("cheques", "endorsed_to", "ALTER TABLE cheques ADD COLUMN endorsed_to INTEGER"),
    ("cheques", "endorsed_date", "ALTER TABLE cheques ADD COLUMN endorsed_date TEXT DEFAULT ''"),
    ("cheques", "endorsed_note", "ALTER TABLE cheques ADD COLUMN endorsed_note TEXT DEFAULT ''"),
    ("cheques", "endorsement_payment_id", "ALTER TABLE cheques ADD COLUMN endorsement_payment_id INTEGER"),
    ("invoices", "return_of", "ALTER TABLE invoices ADD COLUMN return_of INTEGER"),
    ("payments", "vcash", "ALTER TABLE payments ADD COLUMN vcash REAL DEFAULT 0"),
    ("treasury", "method", "ALTER TABLE treasury ADD COLUMN method TEXT DEFAULT 'cash'"),
    ("users", "pin_hash", "ALTER TABLE users ADD COLUMN pin_hash TEXT DEFAULT ''"),
    ("users", "locked_until", "ALTER TABLE users ADD COLUMN locked_until TEXT DEFAULT ''"),
    ("users", "must_change", "ALTER TABLE users ADD COLUMN must_change INTEGER DEFAULT 0"),
    ("users", "disabled", "ALTER TABLE users ADD COLUMN disabled INTEGER DEFAULT 0"),
    ("users", "last_login", "ALTER TABLE users ADD COLUMN last_login TEXT DEFAULT ''"),
    ("users", "perms", "ALTER TABLE users ADD COLUMN perms TEXT DEFAULT ''"),
]

DEFAULT_SETTINGS = {
    "company_name": "NIXT FOR LEATHER",
    "company_phone": "",
    "company_address": "",
    "alert_days": "30",
    "backup_dir": "",
    "login_show_users": "1",
    "sync_dir": "",
    "device_name": "",
    "wholesale_divisor": "1.22",
    "idle_lock_minutes": "5",
    "window_size": "",
    "window_max": "",
    "shortcut_dismissed": "",
    "approval_discount_limit": "200",
    "colors": "أسود,بني,هافان,أبيض,كحلي,رمادي,تانجو",
}

INVOICE_KINDS = ("sale", "return", "purchase", "purchase_return")
INV_NUMBER_PREFIX = {"sale": "S", "return": "SR", "purchase": "P", "purchase_return": "PR"}
KIND_LABELS = {"sale": "فاتورة بيع", "return": "مرتجع بيع",
               "purchase": "فاتورة شراء", "purchase_return": "مرتجع شراء"}
PARTY_KIND_FOR_INVOICE = {"sale": "customer", "return": "customer",
                          "purchase": "supplier", "purchase_return": "supplier"}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class _NixtConn(sqlite3.Connection):
    """Main-DB connection: every commit flags the Drive snapshot as stale."""
    def commit(self):
        changed = self.total_changes > getattr(self, "_backed_changes", 0)
        super().commit()
        self._backed_changes = self.total_changes
        if changed and "continuous_backup_dirty" in globals():
            continuous_backup_dirty()
        if drive_sync is not None:
            try:
                drive_sync.mark_dirty()
            except Exception:
                pass


def get_db():
    conn = sqlite3.connect(DB_PATH, factory=_NixtConn)
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
    # 1.9.0: company name is printed in English everywhere
    conn.execute("UPDATE settings SET value='NIXT FOR LEATHER' WHERE key='company_name'"
                 " AND value IN ('نيكست للجلديات', 'نيكست للجلديات - NIXT FOR LEATHER')")
    sync_schema_upgrade(conn)
    # backfill invoice numbers (added in 1.6.0)
    counts = {}
    for r in conn.execute("SELECT kind, COUNT(*) c FROM invoices WHERE number != '' GROUP BY kind"):
        counts[r["kind"]] = r["c"]
    for r in conn.execute("SELECT id, kind FROM invoices WHERE number = '' ORDER BY id"):
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        conn.execute("UPDATE invoices SET number=? WHERE id=?",
                     (INV_NUMBER_PREFIX.get(r["kind"], "X") + "-%04d" % counts[r["kind"]], r["id"]))
    conn.commit()
    conn.close()


def get_setting(conn, key, default=""):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def mobile_key_get():
    conn = get_db()
    key = get_setting(conn, "mobile_key", "")
    if not key:
        key = secrets.token_urlsafe(18)
        set_setting(conn, "mobile_key", key)
        conn.commit()
    conn.close()
    return key


def lan_ips():
    ips = []
    try:
        for _fam, _typ, _pr, _cn, addr in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET):
            ip = addr[0]
            if ip.startswith("127.") or ip.startswith("169.254.") or ip in ips:
                continue
            ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.append(ip)
        except Exception:
            pass

    def _rank(ip):
        if is_tailscale_ip(ip):
            return 2
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            try:
                if 16 <= int(ip.split(".")[1]) <= 31:
                    return 1
            except (IndexError, ValueError):
                pass
        return 3

    ips.sort(key=_rank)
    return ips


def is_tailscale_ip(ip):
    try:
        parts = [int(x) for x in ip.split(".")]
        return parts[0] == 100 and 64 <= parts[1] <= 127
    except Exception:
        return False


def mobile_urls(key):
    out = []
    recommended_set = False
    for ip in lan_ips():
        ts = is_tailscale_ip(ip)
        rec = False
        if not ts and not recommended_set:
            rec = True
            recommended_set = True
        out.append({"ip": ip, "tailscale": ts, "recommended": rec,
                    "url": "http://%s:%d/m?k=%s" % (ip, PORT, key)})
    return out


def mobile_config_payload():
    conn = get_db()
    enabled = get_setting(conn, "mobile_enabled", "0") == "1"
    conn.close()
    key = mobile_key_get()
    return {"enabled": enabled, "active": MOBILE_BOUND, "port": PORT,
            "key": key, "urls": mobile_urls(key), "version": APP_VERSION}


def closed_months_list(conn):
    try:
        return json.loads(get_setting(conn, "closed_months", "[]")) or []
    except Exception:
        return []


def month_is_closed(conn, date_str):
    m = (date_str or "")[:7]
    return bool(m) and m in closed_months_list(conn)


def set_closed_months(conn, lst):
    conn.execute("INSERT INTO settings (key, value) VALUES ('closed_months', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (json.dumps(lst, ensure_ascii=False),))


# ---------------- auth ----------------
PBKDF2_ROUNDS = 120000
SESSIONS = {}  # token -> {uid, username, role, exp, sid, created, last, ua, ip}
SESSION_HOURS = 12
REQ = threading.local()  # per-request context: ua, ip

APPROVALS = {}  # token -> {"by": manager username, "exp": ts}
APPROVAL_MINUTES = 5


def approval_check(token):
    if not token:
        return None
    ap = APPROVALS.get(token)
    if not ap:
        return None
    if ap["exp"] < time.time():
        APPROVALS.pop(token, None)
        return None
    return ap["by"]


def device_short(ua):
    ua = (ua or "").lower()
    if any(k in ua for k in ("mobile", "android", "iphone", "ipad")):
        return "موبايل"
    if ua:
        return "كمبيوتر"
    return ""


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
    now = time.time()
    SESSIONS[token] = {"uid": uid, "username": username, "role": role,
                       "exp": now + SESSION_HOURS * 3600,
                       "sid": secrets.token_hex(4),
                       "created": now, "last": now,
                       "ua": getattr(REQ, "ua", "") or "",
                       "ip": getattr(REQ, "ip", "") or ""}
    return token


def get_session(token):
    s = SESSIONS.get(token)
    if not s:
        return None
    if s["exp"] < time.time():
        SESSIONS.pop(token, None)
        return None
    s["last"] = time.time()
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


def next_party_code(conn, kind):
    prefix = "C" if kind == "customer" else "P"
    best = 0
    for r in conn.execute("SELECT code FROM customers WHERE kind=? AND code LIKE ?",
                          (kind, prefix + "-%")):
        try:
            best = max(best, int(str(r["code"] or "").split("-", 1)[1]))
        except (IndexError, ValueError):
            pass
    return "%s-%04d" % (prefix, best + 1)


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
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = os.path.join(backup_dir_effective(), "nixt-%s.db" % stamp)
    temp=dest+".tmp"
    src = get_db()
    dst = sqlite3.connect(temp)
    try:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
    finally:
        dst.close()
        src.close()
    os.replace(temp,dest)
    for folder in {BACKUP_DIR, os.path.dirname(dest)}:
        try:
            files = sorted(f for f in os.listdir(folder)
                           if f.startswith("nixt-") and f.endswith(".db"))
            for old in files[:-60]:
                os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return dest


BACKUP_EVENT = threading.Event()
BACKUP_WORKER_LOCK = threading.Lock()
BACKUP_STATUS = {"pending": False, "last_success": "", "last_error": "", "path": ""}
CLOUD_BACKUP_STATUS = {"pending":False,"last_success":"","last_error":"اربط حساب hishammahmoud707@gmail.com لتفعيل نسخة قاعدة البيانات على Drive"}
CLOUD_BACKUP_GENERATION = [0]
BACKUP_GENERATION = [0]
BACKUP_FIRST_DIRTY = [0.0]
BACKUP_LAST_DIRTY = [0.0]


def continuous_backup_dirty():
    now = time.time()
    if not BACKUP_STATUS["pending"]:
        BACKUP_FIRST_DIRTY[0] = now
    BACKUP_GENERATION[0] += 1
    BACKUP_LAST_DIRTY[0] = now
    BACKUP_STATUS["pending"] = True
    BACKUP_EVENT.set()


def continuous_backup_flush():
    with BACKUP_WORKER_LOCK:
        if not BACKUP_STATUS["pending"]:
            return True
        generation = BACKUP_GENERATION[0]
        try:
            dest = do_backup()
            BACKUP_STATUS.update(last_success=now_str(),last_error="",path=dest)
            CLOUD_BACKUP_STATUS["pending"]=True
            CLOUD_BACKUP_GENERATION[0]+=1
            if generation == BACKUP_GENERATION[0]:
                BACKUP_STATUS["pending"] = False
            _nixt_log("continuous local backup completed")
            return True
        except Exception as e:
            BACKUP_STATUS["last_error"] = str(e)
            _nixt_log("continuous backup FAILED: " + str(e))
            return False


def continuous_backup_worker():
    while True:
        BACKUP_EVENT.wait()
        BACKUP_EVENT.clear()
        while BACKUP_STATUS["pending"]:
            now = time.time()
            if now-BACKUP_LAST_DIRTY[0]<1 and now-BACKUP_FIRST_DIRTY[0]<5:
                time.sleep(.1)
                continue
            if not continuous_backup_flush():
                time.sleep(10)
            elif BACKUP_STATUS["pending"]:
                BACKUP_FIRST_DIRTY[0] = time.time()


def cloud_backup_worker():
    last_attempt=0.0
    while True:
        time.sleep(2)
        if not CLOUD_BACKUP_STATUS["pending"]:
            continue
        if drive_sync is None or not drive_sync.full_backup_ready():
            CLOUD_BACKUP_STATUS["last_error"]="اربط حساب hishammahmoud707@gmail.com لتفعيل نسخة قاعدة البيانات على Drive"
            continue
        if time.monotonic()-last_attempt<180:
            continue
        last_attempt=time.monotonic()
        generation=CLOUD_BACKUP_GENERATION[0]
        stable=os.path.join(BACKUP_DIR,"cloud-upload.tmp")
        try:
            with BACKUP_WORKER_LOCK:
                path=BACKUP_STATUS["path"]
                if not path or not os.path.isfile(path):
                    continue
                shutil.copyfile(path,stable)
            drive_sync.upload_full_backup(stable)
            CLOUD_BACKUP_STATUS.update(last_success=now_str(),last_error="")
            if generation==CLOUD_BACKUP_GENERATION[0]:
                CLOUD_BACKUP_STATUS["pending"]=False
            _nixt_log("full private Drive database backup completed")
        except Exception as e:
            CLOUD_BACKUP_STATUS["last_error"]=str(e)
            _nixt_log("full Drive backup FAILED: "+str(e))
        finally:
            try: os.remove(stable)
            except OSError: pass


DB_MUTATION_LOCK = threading.RLock()
UPDATE_LOCK = threading.Lock()

UPDATE_STATUS = {"stage":"idle","error":"","downloaded":0,"total":0}


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
    return login_throttle_wait(uname) > 0


def login_throttle_wait(uname):
    with LOGIN_LOCK:
        fails, locked_until = LOGIN_FAILS.get(uname.lower(), (0, 0.0))
        return max(0, int(locked_until - time.time()))


LOGIN_LOCK_MINUTES = 5


def login_throttle_fail(uname):
    with LOGIN_LOCK:
        fails, _ = LOGIN_FAILS.get(uname.lower(), (0, 0.0))
        fails += 1
        locked_until = time.time() + LOGIN_LOCK_MINUTES * 60 if fails >= 5 else 0.0
        if fails >= 5:
            fails = 0
        LOGIN_FAILS[uname.lower()] = (fails, locked_until)


def login_throttle_clear(uname):
    with LOGIN_LOCK:
        LOGIN_FAILS.pop(uname.lower(), None)



# ---------------- sync v2: drive lock + record-level merge ----------------
SYNC_LOCK_STALE_SEC = 900
SYNC_STATE = {"locked_by": None, "own_lock": False}

SYNC_MERGE_TABLES = ["customers", "items", "employees", "imports", "activity_log",
                     "invoices", "invoice_items", "payments", "ledger_entries", "treasury",
                     "cheques", "cheque_events", "customer_prices",
                     "quotations", "quotation_items", "users"]

SYNC_FKS = {
    "invoices": {"customer_id": "customers"},
    "invoice_items": {"invoice_id": "invoices"},
    "payments": {"customer_id": "customers"},
    "ledger_entries": {"customer_id": "customers"},
    "cheques": {"customer_id": "customers", "payment_id": "payments", "endorsed_to": "customers"},
    "cheque_events": {"cheque_id": "cheques"},
    "customer_prices": {"customer_id": "customers"},
    "quotations": {"customer_id": "customers", "invoice_id": "invoices"},
    "quotation_items": {"quotation_id": "quotations"},
}

SYNC_TS = "strftime('%Y-%m-%d %H:%M:%f','now','localtime')"
SYNC_DEV = "(SELECT value FROM settings WHERE key='device_name')"


def sync_schema_upgrade(conn):
    """Add uuid/updated_at/device columns, the tombstones table and change
    triggers to every syncable table. Idempotent."""
    conn.execute("""CREATE TABLE IF NOT EXISTS sync_deletions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tbl TEXT NOT NULL, uuid TEXT NOT NULL,
        deleted_at TEXT NOT NULL, device TEXT DEFAULT '',
        UNIQUE(tbl, uuid))""")
    for t in SYNC_MERGE_TABLES:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(%s)" % t)]
        for col in ("uuid", "updated_at", "device"):
            if col not in cols:
                conn.execute("ALTER TABLE %s ADD COLUMN %s TEXT DEFAULT ''" % (t, col))
                cols.append(col)
        conn.execute("UPDATE %s SET uuid=lower(hex(randomblob(16))) WHERE uuid IS NULL OR uuid=''" % t)
        base_ts = "created_at" if "created_at" in cols else ("ts" if "ts" in cols else None)
        if base_ts:
            conn.execute("UPDATE %s SET updated_at=%s WHERE updated_at IS NULL OR updated_at=''" % (t, base_ts))
        conn.execute("UPDATE %s SET updated_at=? WHERE updated_at IS NULL OR updated_at=''" % t, (now_str(),))
        conn.executescript(
            "CREATE TRIGGER IF NOT EXISTS sync_ai_%s AFTER INSERT ON %s BEGIN\n"
            "  UPDATE %s SET uuid=lower(hex(randomblob(16))) WHERE id=NEW.id AND (uuid IS NULL OR uuid='');\n"
            "  UPDATE %s SET updated_at=%s, device=%s WHERE id=NEW.id;\n"
            "END;\n"
            "CREATE TRIGGER IF NOT EXISTS sync_au_%s AFTER UPDATE ON %s BEGIN\n"
            "  UPDATE %s SET updated_at=%s, device=%s WHERE id=NEW.id;\n"
            "END;\n"
            "CREATE TRIGGER IF NOT EXISTS sync_ad_%s AFTER DELETE ON %s BEGIN\n"
            "  INSERT INTO sync_deletions (tbl, uuid, deleted_at, device)\n"
            "  SELECT '%s', OLD.uuid, %s, %s\n"
            "  WHERE OLD.uuid IS NOT NULL AND OLD.uuid != '';\n"
            "END;\n"
            % (t, t, t, t, SYNC_TS, SYNC_DEV,
               t, t, t, SYNC_TS, SYNC_DEV,
               t, t, t, SYNC_TS, SYNC_DEV))
    conn.commit()


def sync_lock_path(sdir):
    return os.path.join(sdir, "nixt-sync-lock.json")


def sync_read_lock(sdir):
    try:
        with open(sync_lock_path(sdir), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def sync_write_lock(sdir):
    lock = {"device": device_name(), "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "epoch": time.time(), "app_version": APP_VERSION}
    try:
        with open(sync_lock_path(sdir), "w", encoding="utf-8") as f:
            json.dump(lock, f, ensure_ascii=False)
        return True
    except OSError:
        return False


def sync_release_lock():
    sdir = sync_dir()
    SYNC_STATE["own_lock"] = False
    if not sdir:
        return
    lock = sync_read_lock(sdir)
    if lock and lock.get("device") == device_name():
        try:
            os.remove(sync_lock_path(sdir))
        except OSError:
            pass


def sync_lock_heartbeat():
    while SYNC_STATE.get("own_lock"):
        time.sleep(120)
        if not SYNC_STATE.get("own_lock"):
            break
        sdir = sync_dir()
        if not sdir:
            continue
        lock = sync_read_lock(sdir)
        # keep the lock while it is still ours (or gone); never steal another device's fresh lock
        if not lock or lock.get("device") == device_name():
            sync_write_lock(sdir)


def sync_acquire_lock(force=False):
    """Take the drive lock. Returns None on success, or the other device's
    lock dict when it is held fresh by someone else."""
    sdir = sync_dir()
    if not sdir:
        return None
    try:
        os.makedirs(sdir, exist_ok=True)
    except OSError:
        return None
    lock = sync_read_lock(sdir)
    if lock and lock.get("device") != device_name() and not force:
        if time.time() - float(lock.get("epoch") or 0) < SYNC_LOCK_STALE_SEC:
            return lock
    if sync_write_lock(sdir):
        SYNC_STATE["own_lock"] = True
        SYNC_STATE["locked_by"] = None
    return None


def sync_on_startup():
    """Merge in a newer remote snapshot, then take the drive lock. Runs once at boot."""
    sdir = sync_dir()
    if not sdir:
        return
    if not os.path.isdir(sdir):
        try:
            os.makedirs(sdir, exist_ok=True)
        except OSError:
            return
    try:
        remote = sync_remote_meta(sdir)
        conn = get_db()
        last = get_setting(conn, "sync_last", "")
        conn.close()
        if remote and remote.get("device") != device_name() and remote.get("ts", "") > last:
            srcp = os.path.join(sdir, remote.get("file", ""))
            if os.path.isfile(srcp):
                stats = merge_remote_db(srcp, remote.get("device", ""))
                conn = get_db()
                conn.execute("INSERT INTO settings (key, value) VALUES ('sync_last', ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (remote["ts"],))
                log_action(conn, {"username": "system"}, "مزامنة تلقائية عند الفتح",
                           "دمج نسخة %s: %d جديد، %d تحديث، %d حذف"
                           % (remote.get("device", ""), stats["inserted"], stats["updated"], stats["deleted"]))
                conn.commit()
                conn.close()
    except Exception:
        pass
    other = sync_acquire_lock(force=False)
    if other:
        SYNC_STATE["locked_by"] = other
        SYNC_STATE["own_lock"] = False
    else:
        threading.Thread(target=sync_lock_heartbeat, daemon=True).start()


def sync_on_shutdown():
    """Auto-upload a snapshot on exit, then release the lock."""
    if not SYNC_STATE.get("own_lock"):
        return
    try:
        do_sync_push()
    except Exception:
        pass
    sync_release_lock()


def _tbl_cols(conn, t, schema="main"):
    return [r["name"] for r in conn.execute("PRAGMA %s.table_info(%s)" % (schema, t))]


def merge_remote_db(remote_path, remote_device=""):
    """Record-level merge of a remote snapshot DB into the local DB.
    Identity is the record uuid; the newer updated_at wins; deletions win via
    tombstones. A safety backup is always taken first."""
    check=get_db()
    has_stock=check.execute("SELECT 1 FROM stock_movements LIMIT 1").fetchone()
    check.close()
    remote=sqlite3.connect(remote_path)
    try:
        remote_stock=remote.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
    except sqlite3.Error:
        remote_stock=0
    finally:
        remote.close()
    if has_stock or remote_stock:
        raise RuntimeError("دمج جهازين غير متاح مع سجل المخزن. استخدم جهاز واحد أو استعادة نسخة كاملة بعد المراجعة")
    do_backup()
    stats = {"inserted": 0, "updated": 0, "deleted": 0, "renumbered": 0}
    pending_return = []
    conn = get_db()
    conn.isolation_level = None
    try:
        conn.execute("ATTACH DATABASE ? AS rem", (remote_path,))
        # merge writes carry their own uuid/updated_at; suppress stamping and
        # tombstone recording by removing the sync triggers for the duration
        # (recreated right after, and on every app start via init_db)
        for tr in [r[0] for r in conn.execute(
                "SELECT name FROM main.sqlite_master WHERE type='trigger' AND name LIKE 'sync_%'")]:
            conn.execute("DROP TRIGGER %s" % tr)
        conn.execute("BEGIN IMMEDIATE")
        # union of tombstones from both sides, newest wins
        tombs = {}
        for schema in ("main", "rem"):
            try:
                trows = conn.execute("SELECT tbl, uuid, deleted_at, device FROM %s.sync_deletions" % schema).fetchall()
            except sqlite3.Error:
                trows = []
            for r in trows:
                k = (r["tbl"], r["uuid"])
                if k not in tombs or r["deleted_at"] > tombs[k][0]:
                    tombs[k] = (r["deleted_at"], r["device"])
        id_maps = {}
        for t in SYNC_MERGE_TABLES:
            id_maps[t] = merge_one_table(conn, t, tombs, id_maps, stats, remote_device, pending_return)
        # deferred self-FK fix: invoices.return_of
        inv_map = id_maps.get("invoices", {})
        for lid, orig in pending_return:
            conn.execute("UPDATE invoices SET return_of=? WHERE id=?", (inv_map.get(orig), lid))
        # store merged tombstones locally
        for (tbl, uuid), (deleted_at, device) in tombs.items():
            conn.execute("INSERT INTO sync_deletions (tbl, uuid, deleted_at, device) VALUES (?,?,?,?) "
                         "ON CONFLICT(tbl, uuid) DO UPDATE SET deleted_at=excluded.deleted_at, device=excluded.device "
                         "WHERE excluded.deleted_at > sync_deletions.deleted_at",
                         (tbl, uuid, deleted_at, device))
        # apply tombstones: a row older than its tombstone is deleted
        for (tbl, uuid), (deleted_at, device) in tombs.items():
            if tbl not in SYNC_MERGE_TABLES:
                continue
            row = conn.execute("SELECT id, updated_at FROM %s WHERE uuid=?" % tbl, (uuid,)).fetchone()
            if row and (row["updated_at"] or "") < deleted_at:
                conn.execute("DELETE FROM %s WHERE id=?" % tbl, (row["id"],))
                stats["deleted"] += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        try:
            sync_schema_upgrade(conn)  # recreate triggers + commit
        except sqlite3.Error:
            pass
        try:
            conn.execute("DETACH DATABASE rem")
        except sqlite3.Error:
            pass
        conn.isolation_level = ""
        conn.close()
    return stats


def _remap_treasury_source(d, id_maps):
    src = d.get("source") or ""
    ref = {"payment": "payments", "invoice": "invoices", "cheque": "cheques"}.get(src)
    if ref:
        d["source_id"] = id_maps.get(ref, {}).get(d.get("source_id"))
    else:
        d["source_id"] = None


def merge_one_table(conn, t, tombs, id_maps, stats, remote_device, pending_return):
    try:
        rem_cols = _tbl_cols(conn, t, "rem")
    except sqlite3.Error:
        return {}
    if not rem_cols:
        return {}
    local_cols = _tbl_cols(conn, t)
    merge_cols = [c for c in rem_cols if c in local_cols and c != "id"]
    by_uuid = {r["uuid"]: (r["id"], r["updated_at"] or "")
               for r in conn.execute("SELECT id, uuid, updated_at FROM %s" % t) if r["uuid"]}
    by_uname = {}
    if t == "users":
        by_uname = {r["username"]: (r["id"], r["updated_at"] or "")
                    for r in conn.execute("SELECT id, username, updated_at FROM users")}
    fks = SYNC_FKS.get(t, {})
    remap = {}
    for r in conn.execute("SELECT * FROM rem.%s" % t):
        d = dict(r)
        ruuid = d.get("uuid") or ""
        if not ruuid:
            ruuid = "leg-%s-%s-%s" % (t, remote_device or "x", d["id"])
        rut = d.get("updated_at") or d.get("created_at") or d.get("ts") or ""
        tb = tombs.get((t, ruuid))
        if tb and rut < tb[0]:
            continue
        orig_return = None
        for col, ref in fks.items():
            if col in d and d[col] is not None:
                d[col] = id_maps.get(ref, {}).get(d[col])
        if t == "invoices" and "return_of" in d:
            orig_return = d["return_of"]
            d["return_of"] = id_maps.get("invoices", {}).get(orig_return)
        if t == "treasury":
            _remap_treasury_source(d, id_maps)
        if t == "users":
            hit = by_uname.get(d.get("username"))
        else:
            hit = by_uuid.get(ruuid)
        if hit:
            lid, lut = hit
            if rut > lut:
                sets = [c for c in merge_cols if c != "uuid"]
                vals = [d.get(c) for c in sets] + [lid]
                conn.execute("UPDATE %s SET %s WHERE id=?"
                             % (t, ",".join("%s=?" % c for c in sets)), vals)
                stats["updated"] += 1
            remap[d["id"]] = lid
            if t == "invoices" and orig_return is not None:
                pending_return.append((lid, orig_return))
        else:
            if t in ("invoices", "quotations") and d.get("number"):
                clash = conn.execute("SELECT id FROM %s WHERE number=? AND uuid<>?" % t,
                                     (d["number"], ruuid)).fetchone()
                if clash:
                    prefix = INV_NUMBER_PREFIX.get(d.get("kind") or "sale", "X") if t == "invoices" else "Q"
                    seq = 1
                    for nrow in conn.execute("SELECT number FROM %s WHERE number LIKE ?" % t, (prefix + "-%",)):
                        try:
                            seq = max(seq, int((nrow["number"] or "").split("-")[1]) + 1)
                        except (IndexError, ValueError):
                            pass
                    d["number"] = "%s-%04d" % (prefix, seq)
                    stats["renumbered"] += 1
            ins_cols = [c for c in merge_cols if c != "uuid"] + ["uuid"]
            vals = [d.get(c) for c in merge_cols if c != "uuid"] + [ruuid]
            if "updated_at" in ins_cols:
                vals[ins_cols.index("updated_at")] = rut or now_str()
            conn.execute("INSERT INTO %s (%s) VALUES (%s)"
                         % (t, ",".join(ins_cols), ",".join("?" for _ in ins_cols)), vals)
            lid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            remap[d["id"]] = lid
            by_uuid[ruuid] = (lid, rut)
            stats["inserted"] += 1
            if t == "invoices" and orig_return is not None:
                pending_return.append((lid, orig_return))
    return remap


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
    lock = sync_read_lock(sdir) if sdir else None
    out = {"sync_dir": sdir, "device": dev, "last_sync": last,
           "configured": bool(sdir), "data_dir": DATA_DIR,
           "migrated_from": migrated,
           "lock": lock or {},
           "own_lock": SYNC_STATE["own_lock"],
           "locked_by": (SYNC_STATE["locked_by"] or {}).get("device", "")}
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
    stats = merge_remote_db(tmp, meta.get("device", ""))
    try:
        os.remove(tmp)
    except OSError:
        pass
    conn = get_db()
    conn.execute("INSERT INTO settings (key, value) VALUES ('sync_last', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (meta["ts"],))
    log_action(conn, user={"username": "system"}, action="مزامنة من درايف",
               details="دمج نسخة %s: %d جديد، %d تحديث، %d حذف"
               % (meta.get("device", ""), stats["inserted"], stats["updated"], stats["deleted"]))
    conn.commit()
    conn.close()
    meta = dict(meta)
    meta["merge"] = stats
    return meta, None


# ---------------- API ----------------
def stock_key(item):
    return tuple(str(item.get(k) or "").strip() for k in ("item_name", "material_type", "color", "size", "unit"))


def stock_balance(conn, key):
    return float(conn.execute("SELECT COALESCE(SUM(quantity),0) FROM stock_movements WHERE item_name=? AND material_type=? AND color=? AND size=? AND unit=?", key).fetchone()[0])


def validate_material_item(item):
    for field in ("item_name","material_type","color","size","unit"):
        item[field]=str(item.get(field) or "").strip()
    kind = item.get("material_type") or ""
    if kind not in ("leather", "sole", "other"):
        raise ValueError("اختار نوع الخامة لكل سطر شراء")
    if not str(item.get("item_name") or "").strip():
        raise ValueError("اكتب اسم الخامة")
    q = float(item.get("pairs") or 0)
    price = float(item.get("price") or 0)
    if not math.isfinite(q) or q <= 0 or not math.isfinite(price) or price < 0:
        raise ValueError("راجع الكمية والسعر، لازم أرقام موجبة")
    item["unit"] = "sqft" if kind == "leather" else "pair" if kind == "sole" else str(item.get("unit") or "").strip()
    if not item["unit"]:
        raise ValueError("اكتب وحدة الخامة")
    if kind == "sole" and (not str(item.get("size") or "").strip() or not str(item.get("color") or "").strip() or not q.is_integer()):
        raise ValueError("النعل لازم مقاس ولون وعدد أزواج صحيح")
    if kind != "sole":
        item["size"] = ""
    return q


def stock_post(conn, item, quantity, kind, date, reason, user, invoice_id=None, reverses_id=None):
    key = stock_key(item)
    if stock_balance(conn, key) + quantity < -0.0000001:
        raise ValueError("الرصيد مش كفاية للصنف: " + key[0])
    conn.execute("INSERT INTO stock_movements(item_name,material_type,color,size,unit,quantity,kind,date,reason,username,invoice_id,reverses_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", key + (quantity,kind,date,reason,(user or {}).get("username", ""),invoice_id,reverses_id,now_str()))


class API:
    # ---------- auth ----------
    def me(q, user):
        locked = SYNC_STATE["locked_by"] or {}
        extra = {}
        if user:
            conn = get_db()
            extra["idle_lock_minutes"] = int(get_setting(conn, "idle_lock_minutes", "5") or "5")
            if user.get("uid"):
                r = conn.execute("SELECT must_change, pin_hash, perms FROM users WHERE id=?",
                                 (user["uid"],)).fetchone()
                if r:
                    extra["must_change"] = bool(r["must_change"])
                    extra["has_pin"] = bool(r["pin_hash"])
                    try:
                        extra["perms"] = json.loads(r["perms"]) if r["perms"] else None
                    except Exception:
                        extra["perms"] = None
            conn.close()
        return 200, {
            "logged_in": bool(user),
            "needs_setup": users_count() == 0,
            "username": user["username"] if user else "",
            "role": user["role"] if user else "",
            "version": APP_VERSION,
            **extra,
            "locked_by": locked.get("device", ""),
            "locked_ts": locked.get("ts", ""),
            "own_lock": SYNC_STATE["own_lock"],
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
        pin = (body.get("pin") or "").strip()
        wait = login_throttle_wait(uname)
        if wait > 0:
            return 429, {"error": "الحساب ده متقفل مؤقتًا بعد محاولات غلط كتير",
                         "locked": True, "wait": wait}
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (uname,)).fetchone()
        if u and u["disabled"]:
            conn.close()
            return 403, {"error": "الحساب ده موقوف - كلم المدير"}
        if u and u["locked_until"] and u["locked_until"] > now_str():
            remaining = int((datetime.strptime(u["locked_until"], "%Y-%m-%d %H:%M:%S") - datetime.now()).total_seconds())
            conn.close()
            return 429, {"error": "الحساب ده متقفل مؤقتًا بعد محاولات غلط كتير",
                         "locked": True, "wait": max(1, remaining)}
        ok = False
        if u:
            if pin:
                ok = bool(u["pin_hash"]) and check_password(pin, u["pin_hash"])
            else:
                ok = check_password(pw, u["pass_hash"])
        if not ok:
            login_throttle_fail(uname)
            wait = login_throttle_wait(uname)
            log_action(conn, {"username": uname}, "دخول فاشل",
                       ("بالرقم السري" if pin else "بكلمة السر") + " - " +
                       (device_short(getattr(REQ, "ua", "")) or "جهاز غير معروف"))
            conn.commit()
            if wait > 0 and u:
                exp = (datetime.now() + timedelta(seconds=wait)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("UPDATE users SET locked_until=? WHERE id=?", (exp, u["id"]))
                conn.commit()
            conn.close()
            time.sleep(0.5)
            if wait > 0:
                return 429, {"error": "اتقفل مؤقتًا %d دقايق بعد 5 محاولات غلط" % LOGIN_LOCK_MINUTES,
                             "locked": True, "wait": wait}
            return 401, {"error": "بيانات الدخول غير صحيحة"}
        login_throttle_clear(uname)
        conn.execute("UPDATE users SET last_login=?, locked_until='' WHERE id=?",
                     (now_str(), u["id"]))
        log_action(conn, {"username": u["username"]}, "تسجيل دخول",
                   ("بالرقم السري" if pin else "بكلمة السر") + " - " +
                   (device_short(getattr(REQ, "ua", "")) or "جهاز غير معروف"))
        conn.commit()
        conn.close()
        return 200, {"ok": True, "token": new_session(u["id"], u["username"], u["role"]),
                     "username": u["username"], "role": u["role"],
                     "must_change": bool(u["must_change"]), "has_pin": bool(u["pin_hash"])}

    def login_users(q, user):
        conn = get_db()
        show = get_setting(conn, "login_show_users", "1") == "1"
        out = []
        if show and users_count() > 0:
            for r in conn.execute("SELECT username, role, last_login FROM users WHERE disabled=0 ORDER BY username"):
                name = r["username"]
                initials = "".join(w[0] for w in name.split()[:2]).upper() or name[:1].upper()
                out.append({"username": name, "role": r["role"], "initials": initials,
                            "last_login": r["last_login"] or ""})
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
        conn.execute("UPDATE users SET pass_hash=?, must_change=0 WHERE id=?", (hash_password(new), user["uid"]))
        log_action(conn, user, "تغيير كلمة السر", "")
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- users (admin) ----------
    def list_users(q, user):
        conn = get_db()
        out = rows(conn.execute(
            "SELECT id, username, role, created_at, disabled, locked_until, last_login,"
            " must_change, perms, CASE WHEN pin_hash<>'' THEN 1 ELSE 0 END AS has_pin,"
            " (SELECT action || CASE WHEN details<>'' THEN ' - ' || details ELSE '' END"
            "  FROM activity_log a WHERE a.username=u.username ORDER BY a.id DESC LIMIT 1) AS last_action,"
            " (SELECT ts FROM activity_log a WHERE a.username=u.username ORDER BY a.id DESC LIMIT 1) AS last_action_ts"
            " FROM users u ORDER BY username"))
        now = now_str()
        for r in out:
            r["locked"] = bool(r["locked_until"] and r["locked_until"] > now)
            try:
                r["perms"] = json.loads(r["perms"]) if r["perms"] else None
            except Exception:
                r["perms"] = None
        conn.close()
        return 200, out

    def list_sessions(q, user):
        now = time.time()
        out = []
        for tok, s in list(SESSIONS.items()):
            if s["exp"] < now:
                SESSIONS.pop(tok, None)
                continue
            out.append({"sid": s.get("sid", ""), "username": s.get("username", ""),
                        "role": s.get("role", ""),
                        "created": datetime.fromtimestamp(s.get("created", now)).strftime("%Y-%m-%d %H:%M:%S"),
                        "last": datetime.fromtimestamp(s.get("last", now)).strftime("%Y-%m-%d %H:%M:%S"),
                        "device": device_short(s.get("ua", "")),
                        "current": s is user})
        out.sort(key=lambda r: r["last"], reverse=True)
        return 200, out

    def kick_session(body, user):
        sid = (body or {}).get("sid", "")
        if sid and sid == user.get("sid"):
            return 400, {"error": "دي الجلسة الحالية - اعمل خروج عادي بدلها"}
        for tok, s in list(SESSIONS.items()):
            if s.get("sid") == sid:
                SESSIONS.pop(tok, None)
                conn = get_db()
                log_action(conn, user, "إنهاء جلسة", "%s (%s)" % (s.get("username", ""), s.get("role", "")))
                conn.commit()
                conn.close()
                return 200, {"ok": True}
        return 404, {"error": "الجلسة مش موجودة (يمكن خلصت لوحدها)"}

    def approve(body, user):
        pin = (body.get("pin") or "").strip()
        if not pin:
            return 400, {"error": "اكتب الرقم السري بتاع المدير"}
        key = "approve:" + (user or {}).get("username", "?")
        wait = login_throttle_wait(key)
        if wait > 0:
            return 429, {"error": "محاولات كتير غلط - استنى شوية", "locked": True, "wait": wait}
        conn = get_db()
        admins = conn.execute("SELECT * FROM users WHERE role='admin' AND pin_hash<>'' AND disabled=0").fetchall()
        mgr = None
        for a in admins:
            if check_password(pin, a["pin_hash"]):
                mgr = a
                break
        if not mgr:
            conn.close()
            login_throttle_fail(key)
            time.sleep(0.5)
            return 401, {"error": "الرقم السري بتاع المدير غلط"}
        login_throttle_clear(key)
        tok = secrets.token_urlsafe(16)
        APPROVALS[tok] = {"by": mgr["username"], "exp": time.time() + APPROVAL_MINUTES * 60}
        log_action(conn, user, "طلب موافقة مدير",
                   "وافق %s - صالحة %d دقايق" % (mgr["username"], APPROVAL_MINUTES))
        conn.commit()
        conn.close()
        return 200, {"ok": True, "approval": tok, "by": mgr["username"]}

    def create_user(body, user):
        uname = norm_username(body.get("username"))
        pw = body.get("password") or ""
        pin = (body.get("pin") or "").strip()
        role = body.get("role") if body.get("role") in ("admin", "user", "accountant", "warehouse") else "user"
        if len(uname) < 3 or len(pw) < 4:
            return 400, {"error": "اسم المستخدم 3 أحرف على الأقل وكلمة السر 4 على الأقل"}
        if pin and (not pin.isdigit() or len(pin) < 4 or len(pin) > 8):
            return 400, {"error": "الرقم السري من 4 لـ 8 أرقام"}
        conn = get_db()
        try:
            conn.execute("INSERT INTO users (username, pass_hash, role, created_at, pin_hash) VALUES (?,?,?,?,?)",
                         (uname, hash_password(pw), role, now_str(), hash_password(pin) if pin else ""))
            log_action(conn, user, "مستخدم جديد", "%s (%s)" % (uname, role))
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
        conn.execute("UPDATE users SET pass_hash=?, must_change=1 WHERE id=?", (hash_password(pw), uid))
        log_action(conn, user, "إعادة تعيين كلمة سر", "مستخدم #%d" % uid)
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def set_pin(uid, body, user):
        if user["role"] != "admin" and user.get("uid") != uid:
            return 403, {"error": "admin_required"}
        pin = (body.get("pin") or "").strip()
        if pin and (not pin.isdigit() or len(pin) < 4 or len(pin) > 8):
            return 400, {"error": "الرقم السري من 4 لـ 8 أرقام"}
        conn = get_db()
        if not conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone():
            conn.close()
            return 404, {"error": "المستخدم مش موجود"}
        conn.execute("UPDATE users SET pin_hash=? WHERE id=?", (hash_password(pin) if pin else "", uid))
        log_action(conn, user, "تعيين رقم سري" if pin else "مسح الرقم السري", "مستخدم #%d" % uid)
        conn.commit()
        conn.close()
        return 200, {"ok": True, "has_pin": bool(pin)}

    def set_status(uid, body, user):
        disabled = 1 if body.get("disabled") else 0
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            conn.close()
            return 404, {"error": "المستخدم مش موجود"}
        if disabled and u["role"] == "admin":
            n = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND disabled=0").fetchone()["c"]
            if u["disabled"] == 0 and n <= 1:
                conn.close()
                return 400, {"error": "لا يمكن إيقاف آخر مدير"}
        if disabled and user.get("uid") == uid:
            conn.close()
            return 400, {"error": "مينفعش توقف حسابك بنفسك"}
        conn.execute("UPDATE users SET disabled=? WHERE id=?", (disabled, uid))
        log_action(conn, user, "إيقاف مستخدم" if disabled else "تفعيل مستخدم", u["username"])
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def unlock_user(uid, user):
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            conn.close()
            return 404, {"error": "المستخدم مش موجود"}
        conn.execute("UPDATE users SET locked_until='' WHERE id=?", (uid,))
        log_action(conn, user, "فك قفل مستخدم", u["username"])
        conn.commit()
        conn.close()
        with LOGIN_LOCK:
            LOGIN_FAILS.pop(u["username"].lower(), None)
            LOGIN_FAILS.pop("pin:mobile", None)
        return 200, {"ok": True}

    def set_role(uid, body, user):
        role = body.get("role")
        if role not in ("admin", "user", "accountant", "warehouse"):
            return 400, {"error": "دور غير معروف"}
        conn = get_db()
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            conn.close()
            return 404, {"error": "المستخدم مش موجود"}
        if u["role"] == "admin" and role != "admin":
            n = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND disabled=0").fetchone()["c"]
            if n <= 1:
                conn.close()
                return 400, {"error": "لازم يفضل مدير واحد على الأقل"}
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
        log_action(conn, user, "تغيير دور مستخدم", u["username"] + " -> " + role)
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def set_perms(uid, body, user):
        conn = get_db()
        u = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            conn.close()
            return 404, {"error": "المستخدم مش موجود"}
        perms = body.get("perms")
        if perms is None:
            val = ""
        else:
            secs = [s for s in (perms.get("sections") or []) if s in KNOWN_SECTIONS]
            ops = [o for o in (perms.get("ops") or []) if o in KNOWN_OPS]
            val = json.dumps({"sections": secs, "ops": ops}, ensure_ascii=False)
        conn.execute("UPDATE users SET perms=? WHERE id=?", (val, uid))
        log_action(conn, user, "تعديل صلاحيات", "مستخدم #%d" % uid)
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def unlock_session(body, user):
        pw = body.get("password") or ""
        pin = (body.get("pin") or "").strip()
        conn = get_db()
        if user.get("uid"):
            u = conn.execute("SELECT * FROM users WHERE id=?", (user["uid"],),).fetchone()
        else:
            u = conn.execute("SELECT * FROM users WHERE username=? AND pin_hash<>''",
                             (user.get("pin_user") or "",)).fetchone()
        conn.close()
        ok = False
        if u:
            if pin:
                ok = bool(u["pin_hash"]) and check_password(pin, u["pin_hash"])
            else:
                ok = check_password(pw, u["pass_hash"])
        if not ok:
            time.sleep(0.5)
            return 401, {"error": "غلط - جرب تاني"}
        return 200, {"ok": True}

    def mobile_pin_status(q, user):
        conn = get_db()
        us = rows(conn.execute("SELECT username, role FROM users WHERE pin_hash<>'' AND disabled=0 ORDER BY username"))
        since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        recent = rows(conn.execute(
            "SELECT username, action, details, ts FROM activity_log"
            " WHERE ts>=? AND action IN ('تسجيل دخول','دخول بالرقم السري','دخول فاشل')"
            " ORDER BY id DESC LIMIT 20", (since,)))
        conn.close()
        out = []
        for r in us:
            name = r["username"]
            initials = "".join(w[0] for w in name.split()[:2]).upper() or name[:1].upper()
            out.append({"username": name, "role": r["role"], "initials": initials})
        return 200, {"required": len(us) > 0, "ok": bool(user.get("pin_ok")),
                     "users": out, "user": user.get("pin_user", ""),
                     "recent_logins": recent}

    def mobile_pin_login(body, user):
        pin = (body.get("pin") or "").strip()
        if not pin:
            return 400, {"error": "اكتب الرقم السري"}
        wait = login_throttle_wait("pin:mobile")
        if wait > 0:
            return 429, {"error": "محاولات كتير غلط - استنى شوية", "locked": True, "wait": wait}
        conn = get_db()
        us = conn.execute("SELECT * FROM users WHERE pin_hash<>'' AND disabled=0").fetchall()
        conn.close()
        for u in us:
            if check_password(pin, u["pin_hash"]):
                login_throttle_clear("pin:mobile")
                user["pin_ok"] = True
                user["pin_user"] = u["username"]
                conn = get_db()
                conn.execute("UPDATE users SET last_login=? WHERE id=?", (now_str(), u["id"]))
                log_action(conn, {"username": u["username"]}, "دخول بالرقم السري", "موبايل")
                conn.commit()
                conn.close()
                return 200, {"ok": True, "username": u["username"], "role": u["role"]}
        conn = get_db()
        log_action(conn, {"username": "؟"}, "دخول فاشل", "رقم سري - موبايل")
        conn.commit()
        conn.close()
        login_throttle_fail("pin:mobile")
        time.sleep(0.5)
        wait = login_throttle_wait("pin:mobile")
        if wait > 0:
            return 429, {"error": "اتقفل مؤقتًا بعد 5 محاولات غلط", "locked": True, "wait": wait}
        return 401, {"error": "الرقم السري غلط"}

    def mobile_lock(body, user):
        user["pin_ok"] = False
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
        log_action(conn, user, "تعديل إعدادات", ", ".join(sorted(body.keys())))
        conn.commit()
        conn.close()
        if ("sync_dir" in body or "device_name" in body) and not SYNC_STATE["own_lock"]:
            other = sync_acquire_lock(force=False)
            if other:
                SYNC_STATE["locked_by"] = other
            else:
                threading.Thread(target=sync_lock_heartbeat, daemon=True).start()
        return 200, {"ok": True}

    def window_size(body, user):
        try:
            w = int(body.get("w") or 0)
            h = int(body.get("h") or 0)
        except (TypeError, ValueError):
            return 400, {"error": "مقاس غير صحيح"}
        if body.get("max"):
            val, mx = "", "1"
        elif 400 <= w <= 7680 and 400 <= h <= 4320:
            val, mx = "%dx%d" % (w, h), "0"
        else:
            return 400, {"error": "مقاس غير صحيح"}
        conn = get_db()
        conn.execute("INSERT INTO settings (key, value) VALUES ('window_size', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (val,))
        conn.execute("INSERT INTO settings (key, value) VALUES ('window_max', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mx,))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def shortcut_status(q, user):
        lnk = os.path.join(desktop_path(), "Nixt.lnk")
        return 200, {"windows": os.name == "nt",
                     "exists": os.path.isfile(lnk), "path": lnk}

    def create_shortcut(body, user):
        if os.name != "nt":
            return 400, {"error": "الميزة دي شغالة على ويندوز بس"}
        lnk = os.path.join(desktop_path(), "Nixt.lnk")
        ps1 = os.path.join(DATA_DIR, "create_shortcut.ps1")
        try:
            with open(ps1, "wb") as f:
                f.write(SHORTCUT_PS1.encode("ascii"))
            env = os.environ.copy()
            env["NIXT_SC_TARGET"] = os.path.join(BASE_DIR, "START.bat")
            env["NIXT_SC_DIR"] = BASE_DIR
            env["NIXT_SC_ICON"] = os.path.join(BASE_DIR, "logo.ico")
            subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy",
                            "Bypass", "-WindowStyle", "Hidden", "-File", ps1],
                           env=env, capture_output=True, timeout=30)
        except Exception as e:
            return 500, {"error": "فشل إنشاء الاختصار: %s" % e}
        if os.path.isfile(lnk):
            conn = get_db()
            log_action(conn, user, "إنشاء اختصار سطح المكتب", lnk)
            conn.close()
            return 200, {"ok": True, "path": lnk}
        return 500, {"error": "فشل إنشاء الاختصار - جرّب ملف إنشاء-اختصار-سطح-المكتب.bat جنب البرنامج"}

    def item_monthly(q, user):
        name = (q.get("name", [""])[0] or "").strip()
        out = {"matched": [], "rows": []}
        if not name:
            return 200, out
        conn = get_db()
        names = [r[0] for r in conn.execute(
            "SELECT DISTINCT item_name FROM invoice_items WHERE item_name LIKE ? "
            "ORDER BY item_name LIMIT 10", ("%" + name + "%",))]
        out["matched"] = names
        exact = name if name in names else (names[0] if len(names) == 1 else None)
        if exact:
            sign = {"sale": 1.0, "return": -1.0}
            rows = {}
            for r in conn.execute(
                    "SELECT substr(i.date,1,7) AS m, i.kind, it.pairs, it.price "
                    "FROM invoice_items it JOIN invoices i ON i.id=it.invoice_id "
                    "WHERE it.item_name=? AND i.kind IN ('sale','return')", (exact,)):
                s = sign.get(r["kind"], 1.0)
                a = rows.setdefault(r["m"], {"month": r["m"], "pairs": 0.0, "total": 0.0})
                a["pairs"] += s * (r["pairs"] or 0)
                a["total"] += s * (r["pairs"] or 0) * (r["price"] or 0)
            out["rows"] = sorted(rows.values(), key=lambda x: x["month"], reverse=True)
            out["item"] = exact
        conn.close()
        return 200, out

    # ---------- mobile ----------
    def mobile_bootstrap(q, user):
        conn = get_db()
        name = get_setting(conn, "company_name", "NIXT FOR LEATHER")
        conn.close()
        drv = bool(drive_sync is not None and drive_sync.connected())
        return 200, {"company_name": name, "version": APP_VERSION,
                     "view_only": (user or {}).get("role") == "view",
                     "quick_add": (user or {}).get("role") == "mobile",
                     "drive_connected": drv}

    def mobile_today_summary(q, user):
        conn = get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        chqs = rows(conn.execute(
            "SELECT c.id, c.due_date, c.amount, c.status, c.cheque_number, c.bank,"
            " cu.name AS customer_name, cu.kind AS party_kind"
            " FROM cheques c JOIN customers cu ON cu.id=c.customer_id"
            " WHERE c.status IN ('pending','endorsed') AND c.due_date<=?"
            " ORDER BY c.due_date, c.id LIMIT 50", (week,)))
        for c in chqs:
            c["overdue"] = c["due_date"] < today
            c["due_today"] = c["due_date"] == today
        conn.close()
        _st, ag = API.aging({}, user)
        debtors = [c for c in ag.get("customers", [])
                   if (c.get("d60") or 0) + (c.get("d90") or 0) > 0.005][:5]
        return 200, {"date": today, "cheques": chqs, "debtors": debtors}

    def item_last_price(q, user):
        cid = q.get("customer_id", [""])[0]
        name = (q.get("item_name", [""])[0] or "").strip()
        if not name:
            return 400, {"error": "item_name required"}
        conn = get_db()
        row = None
        if cid:
            row = conn.execute(
                "SELECT ii.price, i.date FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id"
                " WHERE ii.item_name=? AND i.customer_id=? AND i.kind='sale'"
                " ORDER BY i.date DESC, i.id DESC LIMIT 1", (name, cid)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT ii.price, i.date FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id"
                " WHERE ii.item_name=? AND i.kind='sale'"
                " ORDER BY i.date DESC, i.id DESC LIMIT 1", (name,)).fetchone()
        conn.close()
        return 200, {"price": row["price"] if row else None,
                     "date": row["date"] if row else ""}

    def mobile_config(q, user):
        return 200, mobile_config_payload()

    def mobile_config_save(body, user):
        conn = get_db()
        changed = []
        if "enabled" in body:
            v = "1" if body.get("enabled") else "0"
            set_setting(conn, "mobile_enabled", v)
            changed.append("تفعيل=" + v)
        if body.get("regenerate"):
            set_setting(conn, "mobile_key", secrets.token_urlsafe(18))
            changed.append("تجديد المفتاح")
        log_action(conn, user, "إعدادات الموبايل", ", ".join(changed) or "بدون تغيير")
        conn.commit()
        conn.close()
        return 200, mobile_config_payload()

    # ---------- drive sync ----------
    def drive_status(q, user):
        if drive_sync is None:
            return 200, {"configured": False, "connected": False}
        return 200, drive_sync.status()

    def drive_connect(body, user):
        if drive_sync is None or not drive_sync.configured():
            return 400, {"error": "الربط غير معد في هذي النسخة"}
        url, _st = drive_sync.begin_auth(drive_redirect_uri())
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return 200, {"ok": True, "url": url}

    def drive_disconnect(body, user):
        if drive_sync is not None:
            drive_sync.disconnect()
        return 200, {"ok": True}

    def drive_upload_now(body, user):
        if drive_sync is None or not drive_sync.connected():
            return 400, {"error": "جوجل درايف غير مربوط"}
        ok = drive_sync.upload_now(build_snapshot_bytes())
        return (200 if ok else 500), {"ok": ok, "status": drive_sync.status()}

    # ---------- parties (customers & suppliers) ----------
    def party_next_code(q, user):
        kind = q.get("kind", ["customer"])[0]
        if kind not in ("customer", "supplier"):
            kind = "customer"
        conn = get_db()
        code = next_party_code(conn, kind)
        conn.close()
        return 200, {"code": code}

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
        code = (body.get("code") or "").strip() or next_party_code(conn, kind)
        cur = conn.execute(
            "INSERT INTO customers (name, phone, group_name, discount_pct, notes, kind, code, address,"
            " credit_limit, opening, opening_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, body.get("phone", ""), body.get("group_name", ""),
             float(body.get("discount_pct") or 0), body.get("notes", ""), kind,
             code, body.get("address", ""),
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
        log_action(conn, user, "تعديل بيانات طرف", body.get("name", ""))
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

    def stock_list(q, user):
        if user.get("role") not in ("admin","warehouse","user"):
            return 403,{"error":"صلاحية المخزن مطلوبة"}
        perms=user.get("perms") or {}
        if user.get("role") != "admin" and perms.get("sections") is not None and "inventory" not in perms["sections"]:
            return 403,{"error":"صلاحية المخزن مطلوبة"}
        conn = get_db()
        balances = rows(conn.execute("SELECT item_name,material_type,color,size,unit,ROUND(SUM(quantity),6) quantity FROM stock_movements GROUP BY item_name,material_type,color,size,unit ORDER BY material_type,item_name,color,size"))
        movements = rows(conn.execute("SELECT s.*,EXISTS(SELECT 1 FROM stock_movements r WHERE r.reverses_id=s.id) reversed FROM stock_movements s ORDER BY id DESC LIMIT 300"))
        conn.close()
        return 200, {"balances": balances, "movements": movements}

    def stock_move(body, user):
        if user.get("role") not in ("admin", "warehouse", "user"):
            return 403,{"error":"صلاحية المخزن مطلوبة"}
        perms=user.get("perms") or {}
        if user.get("role") != "admin" and perms.get("sections") is not None and "inventory" not in perms["sections"]:
            return 403,{"error":"صلاحية المخزن مطلوبة"}
        conn = get_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            kind = body.get("kind")
            reason = str(body.get("reason") or "").strip()
            if not reason:
                raise ValueError("اكتب سبب الحركة")
            date = body.get("date") or datetime.now().strftime("%Y-%m-%d")
            if kind == "reverse":
                original = conn.execute("SELECT * FROM stock_movements WHERE id=?", (int(body.get("movement_id") or 0),)).fetchone()
                if not original or original["invoice_id"] is not None or original["kind"] == "reverse":
                    raise ValueError("حركات الفواتير تتصحح بمرتجع مرتبط، مش بإلغاء حركة المخزن")
                if conn.execute("SELECT 1 FROM stock_movements WHERE reverses_id=?", (original["id"],)).fetchone():
                    raise ValueError("الحركة دي متصححة قبل كده")
                stock_post(conn, dict(original), -original["quantity"], kind, date, reason, user, reverses_id=original["id"])
            elif kind in ("opening", "issue"):
                item = dict(body)
                q0 = validate_material_item(item)
                if kind == "opening" and conn.execute("SELECT 1 FROM stock_movements WHERE item_name=? AND material_type=? AND color=? AND size=? AND unit=?",stock_key(item)).fetchone():
                    raise ValueError("رصيد البداية للصنف ده اتسجل أو عليه حركات بالفعل")
                stock_post(conn,item,q0 if kind=="opening" else -q0,kind,date,reason,user)
            else:
                raise ValueError("نوع حركة غير صحيح")
            log_action(conn,user,"حركة مخزن",reason)
            conn.commit()
            return 200,{"ok":True}
        except (ValueError,TypeError) as e:
            conn.rollback()
            return 400,{"error":str(e)}
        finally:
            conn.close()

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
        if kind in ("purchase", "purchase_return"):
            try:
                if not body.get("items"):
                    raise ValueError("أضف خامة واحدة على الأقل")
                for item in body["items"]:
                    validate_material_item(item)
            except (ValueError,TypeError) as e:
                return 400,{"error":str(e)}
        idate = body.get("date") or datetime.now().strftime("%Y-%m-%d")
        conn = get_db()
        conn.execute("BEGIN IMMEDIATE")
        if kind == "purchase_return":
            needed = {}
            for item in body["items"]:
                key = stock_key(item)
                needed[key] = needed.get(key,0) + float(item["pairs"])
            original_id = int(body.get("return_of") or 0)
            original = conn.execute("SELECT customer_id,kind,stock_posted FROM invoices WHERE id=?",(original_id,)).fetchone()
            if not original or original["kind"] != "purchase" or original["customer_id"] != int(cid) or not original["stock_posted"]:
                conn.rollback();conn.close()
                return 400,{"error":"مرتجع المخزن لازم يرتبط بفاتورة شراء جديدة لنفس المورد"}
            for key, quantity in needed.items():
                received = float(conn.execute("SELECT COALESCE(SUM(quantity),0) FROM stock_movements WHERE invoice_id=? AND item_name=? AND material_type=? AND color=? AND size=? AND unit=?",(original_id,)+key).fetchone()[0])
                returned = float(conn.execute("SELECT COALESCE(-SUM(s.quantity),0) FROM stock_movements s JOIN invoices i ON i.id=s.invoice_id WHERE i.return_of=? AND s.item_name=? AND s.material_type=? AND s.color=? AND s.size=? AND s.unit=?",(original_id,)+key).fetchone()[0])
                if quantity > received - returned + 0.0000001:
                    conn.rollback();conn.close()
                    return 400,{"error":"كمية المرتجع أكبر من المتبقي في فاتورة الشراء: " + key[0]}
                if stock_balance(conn,key) < quantity - 0.0000001:
                    conn.rollback();conn.close()
                    return 400,{"error":"الرصيد مش كفاية لمرتجع الصنف: " + key[0]}
        if month_is_closed(conn, idate):
            conn.close()
            return 400, {"error": "شهر " + idate[:7] + " متقفل - افتحه من تاب التقارير لو محتاج تسجل فيه"}
        disc = float(body.get("discount") or 0)
        try:
            lim = float(get_setting(conn, "approval_discount_limit", "200") or 0)
        except (TypeError, ValueError):
            lim = 0.0
        if user.get("role") != "admin" and lim > 0 and disc > lim:
            approver = approval_check(body.get("approval") or "")
            if not approver:
                conn.close()
                return 403, {"error": "الخصم ده أكبر من المسموح (%s ج) - محتاج موافقة المدير بالرقم السري" % round(lim, 2),
                             "need_approval": True, "limit": lim}
            log_action(conn, user, "خصم كبير بموافقة مدير",
                       "خصم %s - وافق %s" % (round(disc, 2), approver))
        last = conn.execute("SELECT number FROM invoices WHERE kind=? AND number != '' "
                            "ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        seq = 1
        if last and last["number"]:
            try:
                seq = int(str(last["number"]).split("-")[1]) + 1
            except (IndexError, ValueError):
                seq = conn.execute("SELECT COUNT(*) c FROM invoices WHERE kind=?", (kind,)).fetchone()["c"] + 1
        number = INV_NUMBER_PREFIX.get(kind, "X") + "-%04d" % seq
        cur = conn.execute(
            "INSERT INTO invoices (customer_id, date, kind, shipping, other_charges, discount, notes, number, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, idate, kind,
             float(body.get("shipping") or 0), float(body.get("other_charges") or 0),
             float(body.get("discount") or 0), body.get("notes", ""), number, now_str()))
        iid = cur.lastrowid
        if kind in ("return", "purchase_return"):
            if body.get("reason"):
                conn.execute("UPDATE invoices SET reason=? WHERE id=?", (body.get("reason", ""), iid))
            if body.get("return_of"):
                conn.execute("UPDATE invoices SET return_of=? WHERE id=?", (int(body["return_of"]), iid))
        # 2.7.0: for customer sales/returns the typed price is the retail (consumer)
        # price; wholesale-after-discount is derived server-side so desktop and
        # mobile agree. Supplier invoices and old clients keep price as sent.
        party_row = conn.execute("SELECT kind, discount_pct FROM customers WHERE id=?", (cid,)).fetchone()
        is_customer_party = (party_row is None) or ((party_row["kind"] or "customer") == "customer")
        retail_mode = kind in ("sale", "return") and is_customer_party
        try:
            divisor = float(get_setting(conn, "wholesale_divisor", "1.22") or 1.22) or 1.22
        except (TypeError, ValueError):
            divisor = 1.22
        cust_disc_pct = float(party_row["discount_pct"] or 0) if (retail_mode and party_row) else 0.0
        for it in body.get("items", []):
            if not (it.get("item_name") or "").strip():
                continue
            rv = it.get("retail_price")
            try:
                rv = float(rv) if rv not in (None, "") else None
            except (TypeError, ValueError):
                rv = None
            if retail_mode and rv and rv > 0:
                price = round(rv / divisor * (1 - cust_disc_pct / 100.0), 2)
                retail = rv
            else:
                price = float(it.get("price") or 0)
                retail = None
            conn.execute(
                "INSERT INTO invoice_items (invoice_id, item_name, color, pairs, price, retail_price, material_type, unit, size) VALUES (?,?,?,?,?,?,?,?,?)",
                (iid, it["item_name"].strip(), it.get("color", ""),
                 float(it.get("pairs") or 0), price, retail,
                 (it.get("material_type") or "") if kind in ("purchase", "purchase_return") else "",
                 it.get("unit", "pair") if kind in ("purchase", "purchase_return") else "pair",
                 str(it.get("size") or "") if kind in ("purchase", "purchase_return") and it.get("material_type") == "sole" else ""))
        if kind in ("purchase", "purchase_return"):
            for item in body["items"]:
                quantity = float(item["pairs"]) * (1 if kind == "purchase" else -1)
                stock_post(conn,item,quantity,kind,idate,body.get("reason") or number,user,invoice_id=iid)
            conn.execute("UPDATE invoices SET stock_posted=1 WHERE id=?",(iid,))
        net0 = invoice_net(iid, conn)
        log_action(conn, user, "فاتورة جديدة" if kind in ("sale", "purchase") else "مرتجع جديد",
                   "رقم %s - صافي %s" % (number, round(net0, 2)))
        conn.commit()
        net = invoice_net(iid, conn)
        conn.close()
        return 200, {"id": iid, "number": number, "net": round(net, 2)}

    def get_invoice(iid, user):
        conn = get_db()
        inv = conn.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
        if not inv:
            conn.close()
            return 404, {"error": "not found"}
        inv = dict(inv)
        inv["items"] = rows(conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)))
        for it in inv["items"]:
            if it.get("retail_price") is None:
                r = conn.execute("SELECT retail_price FROM items WHERE name=?", (it["item_name"],)).fetchone()
                it["retail_price"] = r["retail_price"] if r else None
        inv["items_total"] = round(sum(i["pairs"] * i["price"] for i in inv["items"]), 2)
        inv["net"] = round(invoice_net(iid, conn), 2)
        cust = conn.execute("SELECT * FROM customers WHERE id=?", (inv["customer_id"],)).fetchone()
        inv["customer"] = dict(cust) if cust else None
        inv["party_balance"] = round(party_balance(conn, inv["customer_id"]), 2)
        st = {k: get_setting(conn, k, v) for k, v in DEFAULT_SETTINGS.items()}
        inv["company"] = st
        conn.close()
        return 200, inv

    def reprice_invoice_retail(iid, body, user):
        """One-click fix for invoices saved when the typed price was treated as
        wholesale: reinterpret each line price as the retail (consumer) price and
        derive wholesale = retail / divisor * (1 - customer discount). Balances are
        computed live, so they update automatically. Refuses already-fixed invoices."""
        conn = get_db()
        inv = conn.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
        if not inv:
            conn.close()
            return 404, {"error": "الفاتورة مش موجودة"}
        cust = conn.execute("SELECT * FROM customers WHERE id=?", (inv["customer_id"],)).fetchone()
        if not cust or (cust["kind"] or "customer") != "customer" or inv["kind"] not in ("sale", "return"):
            conn.close()
            return 400, {"error": "التصحيح ده للفواتير والمرتجعات بتاعة العملاء بس"}
        items = rows(conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (iid,)))
        if not items:
            conn.close()
            return 400, {"error": "الفاتورة مفيهاش أصناف"}
        if any(it["retail_price"] is not None for it in items):
            conn.close()
            return 400, {"error": "الفاتورة دي متسجلة أصلاً بسعر المستهلك - مش محتاجة تصحيح"}
        try:
            divisor = float(get_setting(conn, "wholesale_divisor", "1.22") or 1.22) or 1.22
        except (TypeError, ValueError):
            divisor = 1.22
        disc = float(cust["discount_pct"] or 0)
        old_net = invoice_net(iid, conn)
        for it in items:
            retail = float(it["price"] or 0)
            newp = round(retail / divisor * (1 - disc / 100.0), 2)
            conn.execute("UPDATE invoice_items SET retail_price=?, price=? WHERE id=?",
                         (retail, newp, it["id"]))
        new_net = invoice_net(iid, conn)
        log_action(conn, user, "تصحيح تفسير سعر فاتورة",
                   "رقم %s - الصافي من %s لـ %s" % (inv["number"] or iid, round(old_net, 2), round(new_net, 2)))
        conn.commit()
        conn.close()
        return 200, {"old_net": round(old_net, 2), "new_net": round(new_net, 2)}

    def delete_invoice(iid, user=None, approver=""):
        conn = get_db()
        inv = conn.execute("SELECT number,stock_posted FROM invoices WHERE id=?", (iid,)).fetchone()
        if inv and inv["stock_posted"]:
            conn.close()
            return 400,{"error":"الفاتورة مرتبطة بحركات مخزن، صححها بمرتجع مرتبط للحفاظ على السجل"}
        conn.execute("DELETE FROM invoice_items WHERE invoice_id=?", (iid,))
        conn.execute("DELETE FROM invoices WHERE id=?", (iid,))
        note = (inv["number"] or ("#%d" % iid)) if inv else ("#%d" % iid)
        if approver:
            note += " - بموافقة المدير " + approver
        log_action(conn, user, "حذف فاتورة", note)
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

    def get_payment(pid, user):
        conn = get_db()
        r = conn.execute("SELECT p.*, c.name AS customer_name, c.kind AS party_kind"
                         " FROM payments p JOIN customers c ON c.id=p.customer_id"
                         " WHERE p.id=?", (pid,)).fetchone()
        conn.close()
        if not r:
            return 404, {"error": "الدفعة مش موجودة"}
        p = dict(r)
        p["total"] = round((p["cash"] or 0) + (p["card"] or 0) + (p["transfer"] or 0) + (p["cheque"] or 0) + (p["vcash"] or 0), 2)
        return 200, p

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
        if month_is_closed(conn, pdate):
            conn.close()
            return 400, {"error": "شهر " + pdate[:7] + " متقفل - افتحه من تاب التقارير لو محتاج تسجل فيه"}
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

    def delete_payment(pid, user=None, approver=""):
        conn = get_db()
        pm = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        conn.execute("DELETE FROM payments WHERE id=?", (pid,))
        conn.execute("DELETE FROM cheques WHERE payment_id=?", (pid,))
        conn.execute("DELETE FROM treasury WHERE source='payment' AND source_id=?", (pid,))
        if pm:
            cust = conn.execute("SELECT name FROM customers WHERE id=?", (pm["customer_id"],)).fetchone()
            det = "%s - %s" % (cust["name"] if cust else pm["customer_id"], pm["date"])
            if approver:
                det += " - بموافقة المدير " + approver
            log_action(conn, user, "حذف دفعة", det)
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
        # charts: monthly sales for the last 6 months, top models, deltas
        month_keys = []
        d0 = today_d.replace(day=1)
        for _ in range(6):
            month_keys.append(d0.strftime("%Y-%m"))
            d0 = (d0 - timedelta(days=1)).replace(day=1)
        month_keys.reverse()
        mon_sales = {k: 0.0 for k in month_keys}
        for inv in conn.execute("SELECT id, date FROM invoices WHERE kind='sale'"):
            k = (inv["date"] or "")[:7]
            if k in mon_sales:
                mon_sales[k] += invoice_net(inv["id"], conn)
        out["monthly_sales"] = [{"month": k, "total": round(mon_sales[k], 2)} for k in month_keys]
        top = conn.execute(
            "SELECT ii.item_name n, SUM(ii.pairs) p FROM invoice_items ii"
            " JOIN invoices i ON i.id=ii.invoice_id WHERE i.kind='sale'"
            " GROUP BY ii.item_name ORDER BY p DESC LIMIT 5").fetchall()
        out["top_models"] = [{"name": r["n"], "pairs": round(r["p"] or 0, 2)} for r in top]
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
        if not UPDATE_LOCK.acquire(blocking=False):
            return 409,{"error":"تحديث شغال بالفعل"}
        accepted=False
        try:
            result=API._do_update(body,user)
            accepted=result[0]==200
            return result
        except Exception as e:
            UPDATE_STATUS.update(stage="error",error=str(e))
            _nixt_log("update failed: "+str(e))
            return 500,{"error":"فشل تجهيز التحديث: "+str(e)}
        finally:
            if not accepted:
                UPDATE_LOCK.release()

    def _do_update(body, user):
        UPDATE_STATUS.update(stage="checking",error="")
        _nixt_log("update: checking release")
        info, kind, detail = fetch_update_info()
        if info is None:
            return 400, {"error": UPDATE_ERROR_KIND.get(kind, "تعذر الاتصال بخادم التحديثات")}
        latest = str(info.get("version", ""))
        if not (version_tuple(latest) > version_tuple(APP_VERSION)):
            return 400, {"error": "no newer version"}
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}",latest):
            return 400,{"error":"رقم إصدار غير صحيح"}
        zip_url = info.get("zip_url")
        if not zip_url:
            return 400, {"error": "no zip url"}
        # Never apply an update without a verified safety backup.
        try:
            UPDATE_STATUS["stage"]="backup"
            do_backup()
        except Exception as e:
            UPDATE_STATUS.update(stage="error",error=str(e))
            _nixt_log("update backup failed: " + str(e))
            return 500,{"error":"فشل النسخ الاحتياطي قبل التحديث: " + str(e)}
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
        UPDATE_STATUS.update(stage="download",downloaded=0)
        _nixt_log("update: downloading " + latest)
        started=time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=20) as r, open(zip_path,"wb") as f:
                UPDATE_STATUS["total"]=int(r.headers.get("Content-Length") or 0)
                while True:
                    chunk=r.read(65536)
                    if not chunk: break
                    f.write(chunk)
                    UPDATE_STATUS["downloaded"]+=len(chunk)
                    if time.monotonic()-started>180:
                        raise TimeoutError("download exceeded 3 minutes")
        except Exception as e:
            UPDATE_STATUS.update(stage="error",error=str(e))
            _nixt_log("update download failed: " + str(e))
            return 500,{"error":"تحميل التحديث فشل: " + str(e)}
        expected_hash=str(info.get("sha256") or "").lower()
        if expected_hash:
            with open(zip_path,"rb") as package:
                actual_hash=hashlib.sha256(package.read()).hexdigest()
            if not hmac.compare_digest(expected_hash,actual_hash):
                raise ValueError("بصمة ملف التحديث غير مطابقة")
        UPDATE_STATUS["stage"]="extract"
        _nixt_log("update: extracting package")
        staging = os.path.join(UPDATES_DIR, "extract-%s" % latest)
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)
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
                dest = os.path.abspath(os.path.join(staging,rel.replace("/",os.sep)))
                if os.path.commonpath([os.path.abspath(staging),dest]) != os.path.abspath(staging):
                    raise ValueError("unsafe update archive path")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(n) as srcf, open(dest, "wb") as outf:
                    shutil.copyfileobj(srcf, outf)
        # handoff: stop this server, copy the new files over THIS folder
        # (the launch path never changes, so shortcuts keep working), restart.
        # Paths travel via environment variables so the bat itself stays pure
        # ASCII - Arabic/space paths in a .bat get mangled by cmd's codepage
        # (root cause of the silent 2.5.0->2.6.0 failure).
        old_dir = BASE_DIR.rstrip(os.sep)
        log_path = os.path.join(os.path.dirname(DATA_DIR), "update-log.txt")
        bat = build_update_bat(old_dir)

        new_start = os.path.join(old_dir,"START.bat")
        UPDATE_STATUS["stage"]="handoff"
        _nixt_log("update: package ready; scheduling Windows handoff")
        def _handoff():
            try:
                if not continuous_backup_flush():
                    raise RuntimeError("فشل حفظ نسخة أمان قبل إعادة التشغيل")
                env = dict(os.environ)
                env.update(NIXT_STAGING=staging,NIXT_OLD=old_dir,NIXT_START=new_start,
                           NIXT_LOG=log_path,NIXT_UPDIR=UPDATES_DIR,NIXT_PID=str(os.getpid()))
                subprocess.Popen(["cmd.exe","/d","/c",bat],cwd=os.environ.get("TEMP") or BASE_DIR,
                                 env=env,creationflags=getattr(subprocess,"CREATE_NEW_CONSOLE",0))
                _nixt_log("update: Windows handoff process started; exiting app")
            except Exception as e:
                import traceback
                UPDATE_STATUS.update(stage="error",error=str(e))
                _nixt_log("update handoff FAILED:\n"+traceback.format_exc())
                show_message("Update could not start. The app is still running.\n"+str(e))
                if UPDATE_LOCK.locked():
                    UPDATE_LOCK.release()
                return
            os._exit(0)

        threading.Timer(1.0, _handoff).start()
        return 200, {"ok": True, "new_folder": BASE_DIR, "version": latest}

    def update_log(q, user):
        path = os.path.join(os.path.dirname(DATA_DIR), "update-log.txt")
        try:
            with io.open(path, encoding="utf-8", errors="replace") as f:
                txt = f.read()[-6000:]
        except OSError:
            txt = ""
        return 200, {"log": txt}

    def wipe(body, user):
        check=get_db()
        has_stock=check.execute("SELECT 1 FROM stock_movements LIMIT 1").fetchone()
        check.close()
        if has_stock:
            return 400,{"error":"فيه سجل مخزن مرتبط بالبيانات. التصفير غير متاح للحفاظ على تطابق المخزن والحسابات"}
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

    def sync_force_lock(body, user):
        other = sync_acquire_lock(force=True)
        if other:
            return 423, {"error": "locked", "locked_by": other.get("device", "")}
        SYNC_STATE["locked_by"] = None
        return 200, {"ok": True, "device": device_name()}

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
        tdate = body.get("date") or datetime.now().strftime("%Y-%m-%d")
        if month_is_closed(conn, tdate):
            conn.close()
            return 400, {"error": "شهر " + tdate[:7] + " متقفل - افتحه من تاب التقارير لو محتاج تسجل فيه"}
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
        d = (q.get("date", [""])[0] or "").strip()
        un = (q.get("user", [""])[0] or "").strip()
        sql = "SELECT * FROM activity_log"
        conds, args = [], []
        if d:
            conds.append("ts LIKE ?")
            args.append(d + "%")
        if un:
            conds.append("username=?")
            args.append(un)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT 500"
        conn = get_db()
        out = rows(conn.execute(sql, args))
        conn.close()
        return 200, out


    # ---------- 1.7.0: today panel, model profit, quotations, restore ----------
    def today(q, user):
        conn = get_db()
        t = datetime.now().strftime("%Y-%m-%d")
        alert_days = int(float(get_setting(conn, "alert_days", "30") or 30))
        cheque_days = int(float(get_setting(conn, "cheque_alert_days", "2") or 2))
        def dplus(n):
            return (datetime.now() + timedelta(days=n)).strftime("%Y-%m-%d")
        tin = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM treasury WHERE date=? AND kind='in'", (t,)).fetchone()["s"]
        tout = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM treasury WHERE date=? AND kind='out'", (t,)).fetchone()["s"]
        qcheq = ("SELECT c.*, cu.name AS customer_name, cu.kind AS party_kind FROM cheques c"
                 " JOIN customers cu ON cu.id=c.customer_id"
                 " WHERE c.status IN ('pending','endorsed') AND c.due_date<=? ORDER BY c.due_date")
        week = rows(conn.execute(qcheq, (dplus(7),)))
        for c in week:
            c["overdue"] = c["due_date"] < t
            c["due_today"] = c["due_date"] == t
            c["due_soon"] = (not c["overdue"]) and (not c["due_today"]) and c["due_date"] <= dplus(cheque_days)
        overdue = [c for c in week if c["overdue"]]
        out = {"date": t,
               "treasury_in": round(tin, 2), "treasury_out": round(tout, 2),
               "cheques_week": week,
               "cheques_overdue_count": len(overdue),
               "cheques_overdue_sum": round(sum(c["amount"] for c in overdue), 2),
               "cheques_due_today": [c for c in week if c["due_today"]],
               "cheques_due_soon": [c for c in week if c["due_soon"]],
               "cheque_alert_days": cheque_days}
        # overdue customers (same aging basis as collections)
        overdue_cust = []
        for p in conn.execute("SELECT * FROM customers WHERE kind='customer'"):
            bal = party_balance(conn, p["id"])
            if bal <= 0.005:
                continue
            oldest = conn.execute(
                "SELECT MIN(date) d FROM invoices WHERE customer_id=? AND kind='sale'", (p["id"],)).fetchone()["d"]
            if not oldest:
                continue
            try:
                days = (datetime.now().date() - datetime.strptime(oldest, "%Y-%m-%d").date()).days
            except ValueError:
                days = 0
            if days > alert_days:
                overdue_cust.append({"id": p["id"], "name": p["name"], "balance": round(bal, 2), "days": days})
        overdue_cust.sort(key=lambda x: -x["balance"])
        out["overdue_customers"] = overdue_cust[:10]
        out["overdue_customers_count"] = len(overdue_cust)
        conn.close()
        return 200, out

    def model_profit(q, user):
        dfrom = q.get("from", [""])[0]
        dto = q.get("to", [""])[0]
        cond = "i.kind IN ('sale','return')"
        args = []
        if dfrom:
            cond += " AND i.date>=?"
            args.append(dfrom)
        if dto:
            cond += " AND i.date<=?"
            args.append(dto)
        conn = get_db()
        agg = {}
        for r in conn.execute(
                "SELECT ii.item_name, ii.pairs, ii.price, i.kind FROM invoice_items ii"
                " JOIN invoices i ON i.id=ii.invoice_id WHERE " + cond, args):
            a = agg.setdefault(r["item_name"], {"pairs": 0.0, "revenue": 0.0})
            sign = 1 if r["kind"] == "sale" else -1
            a["pairs"] += sign * (r["pairs"] or 0)
            a["revenue"] += sign * (r["pairs"] or 0) * (r["price"] or 0)
        costs = {r["name"]: (r["cost"] or 0) for r in conn.execute("SELECT name, cost FROM items")}
        out = []
        for name, a in agg.items():
            cost = costs.get(name, 0.0)
            profit = a["revenue"] - cost * a["pairs"]
            out.append({"item_name": name, "pairs": round(a["pairs"], 2),
                        "revenue": round(a["revenue"], 2), "unit_cost": round(cost, 2),
                        "has_cost": name in costs and cost > 0,
                        "profit": round(profit, 2),
                        "margin": round(profit / a["revenue"] * 100, 1) if a["revenue"] > 0 else 0})
        out.sort(key=lambda x: -x["profit"])
        conn.close()
        return 200, {"items": out,
                     "total_profit": round(sum(x["profit"] for x in out), 2),
                     "total_revenue": round(sum(x["revenue"] for x in out), 2)}

    # ---------- quotations ----------
    def list_quotations(q, user):
        conn = get_db()
        out = rows(conn.execute(
            "SELECT q.*, c.name AS customer_name FROM quotations q"
            " JOIN customers c ON c.id=q.customer_id ORDER BY q.date DESC, q.id DESC"))
        for qt in out:
            its = rows(conn.execute("SELECT * FROM quotation_items WHERE quotation_id=?", (qt["id"],)))
            qt["items"] = its
            qt["pairs"] = round(sum(i["pairs"] or 0 for i in its), 2)
            qt["total"] = round(sum((i["pairs"] or 0) * (i["price"] or 0) for i in its), 2)
        conn.close()
        return 200, out

    def create_quote(body, user):
        cid = body.get("customer_id")
        if not cid:
            return 400, {"error": "اختر العميل أولاً"}
        items = body.get("items") or []
        if not items:
            return 400, {"error": "أضف صنفاً واحداً على الأقل"}
        conn = get_db()
        last = conn.execute("SELECT number FROM quotations WHERE number != ''"
                            " ORDER BY CAST(SUBSTR(number,3) AS INTEGER) DESC LIMIT 1").fetchone()
        seq = 1
        if last and last["number"]:
            try:
                seq = int(str(last["number"]).split("-")[1]) + 1
            except (IndexError, ValueError):
                pass
        number = "Q-%04d" % seq
        cur = conn.execute(
            "INSERT INTO quotations (customer_id, date, notes, status, number, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (cid, body.get("date") or datetime.now().strftime("%Y-%m-%d"),
             body.get("notes", ""), "open", number, now_str()))
        qid = cur.lastrowid
        for it in items:
            conn.execute(
                "INSERT INTO quotation_items (quotation_id, item_name, color, pairs, price)"
                " VALUES (?,?,?,?,?)",
                (qid, it["item_name"].strip(), it.get("color", ""),
                 float(it.get("pairs") or 0), float(it.get("price") or 0)))
        log_action(conn, user, "عرض سعر جديد", "رقم %s" % number)
        conn.commit()
        conn.close()
        return 200, {"id": qid, "number": number}

    def get_quote(qid, user):
        conn = get_db()
        qt = conn.execute("SELECT q.*, c.name AS customer_name, c.phone AS customer_phone"
                          " FROM quotations q JOIN customers c ON c.id=q.customer_id"
                          " WHERE q.id=?", (qid,)).fetchone()
        if not qt:
            conn.close()
            return 404, {"error": "عرض السعر مش موجود"}
        qt = dict(qt)
        qt["items"] = rows(conn.execute("SELECT * FROM quotation_items WHERE quotation_id=?", (qid,)))
        qt["pairs"] = round(sum(i["pairs"] or 0 for i in qt["items"]), 2)
        qt["total"] = round(sum((i["pairs"] or 0) * (i["price"] or 0) for i in qt["items"]), 2)
        qt["company"] = {"company_name": get_setting(conn, "company_name", ""),
                         "company_phone": get_setting(conn, "company_phone", ""),
                         "company_address": get_setting(conn, "company_address", "")}
        conn.close()
        return 200, qt

    def convert_quote(qid, user):
        conn = get_db()
        qt = conn.execute("SELECT * FROM quotations WHERE id=?", (qid,)).fetchone()
        if not qt:
            conn.close()
            return 404, {"error": "عرض السعر مش موجود"}
        if qt["status"] == "converted":
            conn.close()
            return 400, {"error": "عرض السعر ده اتحوّل لفاتورة قبل كده"}
        its = rows(conn.execute("SELECT * FROM quotation_items WHERE quotation_id=?", (qid,)))
        conn.close()
        body = {"customer_id": qt["customer_id"], "kind": "sale",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "notes": ("عرض سعر " + qt["number"]) if qt["number"] else "",
                "items": [{"item_name": i["item_name"], "color": i["color"],
                           "pairs": i["pairs"], "price": i["price"]} for i in its]}
        status, res = API.create_invoice(body, user)
        if status != 200:
            return status, res
        conn = get_db()
        conn.execute("UPDATE quotations SET status='converted', invoice_id=? WHERE id=?",
                     (res["id"], qid))
        log_action(conn, user, "تحويل عرض سعر لفاتورة",
                   "%s -> %s" % (qt["number"], res.get("number", res["id"])))
        conn.commit()
        conn.close()
        return 200, {"ok": True, "invoice_id": res["id"], "invoice_number": res.get("number", "")}

    def cancel_quote(qid, user):
        conn = get_db()
        conn.execute("UPDATE quotations SET status='canceled' WHERE id=? AND status='open'", (qid,))
        log_action(conn, user, "إلغاء عرض سعر", "رقم %s" % qid)
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- backups & restore ----------
    def list_backups(q, user):
        seen = {}
        for folder in {BACKUP_DIR, backup_dir_effective()}:
            try:
                for f in os.listdir(folder):
                    if f.startswith("nixt-") and f.endswith(".db"):
                        p = os.path.join(folder, f)
                        try:
                            st = os.stat(p)
                        except OSError:
                            continue
                        seen[f] = {"file": f, "size": st.st_size,
                                   "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                                   "folder": folder}
            except OSError:
                pass
        out = sorted(seen.values(), key=lambda x: x["file"], reverse=True)
        return 200, {"backups": out[:30], "dir": backup_dir_effective()}

    def restore_cloud_backup(body,user):
        if drive_sync is None:
            return 400,{"error":"Google Drive غير متاح"}
        name="nixt-cloud-download-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".db"
        path=os.path.join(BACKUP_DIR,name)
        try:
            drive_sync.download_full_backup(str(body.get("file_id") or ""),path)
            return API.restore_backup({"file":name},user)
        except Exception as e:
            return 400,{"error":str(e)}

    def restore_backup(body, user):
        fname = os.path.basename(body.get("file") or "")
        if not fname.startswith("nixt-") or not fname.endswith(".db"):
            return 400, {"error": "اسم ملف غير صحيح"}
        src_path = None
        for folder in {BACKUP_DIR, backup_dir_effective()}:
            p = os.path.join(folder, fname)
            if os.path.isfile(p):
                src_path = p
                break
        if not src_path:
            return 404, {"error": "النسخة مش موجودة"}
        try:
            check = sqlite3.connect(src_path)
            check.execute("SELECT COUNT(*) FROM customers").fetchone()
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("integrity check failed")
            check.close()
        except sqlite3.Error:
            return 400, {"error": "ملف النسخة تالف"}
        # Serialize app writes and backup rotation while replacing the DB.
        # Stage the selected snapshot first: retention must not remove it.
        import tempfile
        fd,staged=tempfile.mkstemp(prefix="nixt-restore-",suffix=".db")
        os.close(fd)
        try:
            with DB_MUTATION_LOCK, BACKUP_WORKER_LOCK:
                shutil.copyfile(src_path,staged)
                safety=do_backup()
                src_conn=sqlite3.connect(staged)
                dst_conn=get_db()
                try:
                    src_conn.backup(dst_conn)
                    log_action(dst_conn,user,"استعادة نسخة احتياطية",fname)
                    dst_conn.commit()
                finally:
                    src_conn.close()
                    dst_conn.close()
                init_db()
        finally:
            try: os.remove(staged)
            except OSError: pass
        return 200, {"ok": True, "restored": fname, "safety_backup": os.path.basename(safety)}

    # ---------- price lists ----------
    def top_items(q, user):
        dfrom = q.get("from", [""])[0]
        dto = q.get("to", [""])[0]
        cond = "i.kind IN ('sale','return')"
        args = []
        if dfrom:
            cond += " AND i.date>=?"
            args.append(dfrom)
        if dto:
            cond += " AND i.date<=?"
            args.append(dto)
        conn = get_db()
        by_item, by_color = {}, {}
        sign = {"sale": 1.0, "return": -1.0}
        for r in conn.execute(
                "SELECT i.kind, it.item_name, it.color, it.pairs, it.price FROM invoice_items it "
                "JOIN invoices i ON i.id=it.invoice_id WHERE " + cond, args):
            s = sign.get(r["kind"], 1.0)
            name = (r["item_name"] or "").strip()
            if not name:
                continue
            color = (r["color"] or "").strip() or "بدون لون"
            pairs = s * (r["pairs"] or 0)
            total = s * (r["pairs"] or 0) * (r["price"] or 0)
            a = by_item.setdefault(name, {"item_name": name, "pairs": 0.0, "total": 0.0})
            a["pairs"] += pairs
            a["total"] += total
            b = by_color.setdefault(color, {"color": color, "pairs": 0.0, "total": 0.0})
            b["pairs"] += pairs
            b["total"] += total
        conn.close()
        items = sorted(by_item.values(), key=lambda x: -x["pairs"])[:30]
        colors = sorted(by_color.values(), key=lambda x: -x["pairs"])[:30]
        for arr in (items, colors):
            for x in arr:
                x["pairs"] = round(x["pairs"], 2)
                x["total"] = round(x["total"], 2)
        return 200, {"items": items, "colors": colors}

    def collections(q, user):
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
            paid = -credit if credit < 0 else 0.0
            total_remaining = credit if credit > 0 else 0.0
            oldest = ""
            for inv in invs:
                net = invoice_net(inv["id"], conn)
                remaining = net
                if paid > 0:
                    use = min(paid, remaining)
                    remaining -= use
                    paid -= use
                if remaining > 0.005:
                    total_remaining += remaining
                    if not oldest:
                        oldest = inv["date"]
            if total_remaining <= 0.005:
                continue
            lp = conn.execute("SELECT MAX(date) d FROM payments WHERE customer_id=?",
                              (p["id"],)).fetchone()["d"] or ""
            days = 0
            if oldest:
                try:
                    days = (today - datetime.strptime(oldest, "%Y-%m-%d").date()).days
                except ValueError:
                    days = 0
            out.append({"id": p["id"], "name": p["name"], "phone": p["phone"] or "",
                        "balance": round(total_remaining, 2), "oldest_date": oldest,
                        "days": days, "last_payment": lp})
        conn.close()
        out.sort(key=lambda x: -x["balance"])
        return 200, out

    def monthly_report(q, user):
        month = q.get("month", [""])[0]
        if len(month) != 7:
            return 400, {"error": "اختر الشهر"}
        code, prof = API.profit({"month": [month]}, user)
        if code != 200:
            return code, prof
        like = month + "%"
        conn = get_db()
        purchases = purchase_returns = 0.0
        for r in conn.execute("SELECT id, kind FROM invoices WHERE date LIKE ?", (like,)):
            net = invoice_net(r["id"], conn)
            if r["kind"] == "purchase":
                purchases += net
            elif r["kind"] == "purchase_return":
                purchase_returns += net
        closed = month in closed_months_list(conn)
        inv_count = conn.execute("SELECT COUNT(*) c FROM invoices WHERE date LIKE ?", (like,)).fetchone()["c"]
        conn.close()
        prof.update({"month": month, "closed": closed, "invoice_count": inv_count,
                     "purchases": round(purchases, 2),
                     "purchase_returns": round(purchase_returns, 2),
                     "net_purchases": round(purchases - purchase_returns, 2)})
        return 200, prof

    def monthly_close(body, user):
        m = (body.get("month") or "").strip()
        if len(m) != 7:
            return 400, {"error": "اختر الشهر"}
        conn = get_db()
        lst = closed_months_list(conn)
        if m not in lst:
            lst.append(m)
            set_closed_months(conn, lst)
            log_action(conn, user, "تقفيل شهر", m)
            conn.commit()
        conn.close()
        return 200, {"ok": True}

    def monthly_open(body, user):
        m = (body.get("month") or "").strip()
        conn = get_db()
        lst = closed_months_list(conn)
        if m in lst:
            lst.remove(m)
            set_closed_months(conn, lst)
            log_action(conn, user, "فتح شهر", m)
            conn.commit()
        conn.close()
        return 200, {"ok": True}

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
        try:
            cost = float(body.get("cost") or 0)
        except (TypeError, ValueError):
            return 400, {"error": "التكلفة غير صحيحة"}
        conn = get_db()
        conn.execute(
            "INSERT INTO items (name, code, retail_price, cost, active, created_at) VALUES (?,?,?,?,1,?) "
            "ON CONFLICT(name) DO UPDATE SET code=excluded.code, retail_price=excluded.retail_price,"
            " cost=excluded.cost",
            (name, body.get("code", "") or "", retail, cost, now_str()))
        log_action(conn, user, "حفظ صنف", "%s - مستهلك %s - تكلفة %s" % (name, retail, cost))
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
        log_action(conn, user, "سعر خاص", "%s - %s = %s" % (cid, name, price))
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


# ---------------- drive snapshot (phase 1: PC -> Drive view-only) ----------------

def build_snapshot_bytes():
    fake_admin = {"role": "admin", "name": ""}
    parts = {}
    for key, fn in (("bootstrap", API.mobile_bootstrap),
                    ("dashboard", API.dashboard),
                    ("stock", API.stock_list),
                    ("customers", API.list_customers),
                    ("invoices", API.list_invoices),
                    ("today", API.today),
                    ("today_summary", API.mobile_today_summary)):
        try:
            _st, obj = fn({}, fake_admin)
            parts[key] = obj
        except Exception as e:
            parts[key] = {"error": str(e)[:120]}
    parts["snapshot_ts"] = now_str()
    parts["version"] = APP_VERSION
    return json.dumps(parts, ensure_ascii=False, default=str).encode("utf-8")


def drive_redirect_uri():
    return "http://127.0.0.1:%d/api/drive/callback" % PORT


def drive_callback_html(q):
    err = (q.get("error") or [""])[0]
    code = (q.get("code") or [""])[0]
    state = (q.get("state") or [""])[0]
    if drive_sync is None:
        ok, msg = False, "الوحدة غير متاحة"
    elif err:
        ok, msg = False, "اترفض الإذن من جوجل"
    elif not code:
        ok, msg = False, "لم يصل رمز التفويض"
    else:
        ok, msg = drive_sync.finish_auth(code, state, drive_redirect_uri())
        if ok:
            msg = "تم ربط جوجل درايف بنجاح. اقفل الصفحة دي وارجع للبرنامج."
        else:
            msg = "فشل الربط: " + msg
    color = "#166534" if ok else "#b91c1c"
    body = "<div style='font-family:sans-serif;direction:rtl;text-align:center;margin-top:80px'>"
    body += "<h2 style='color:%s'>%s</h2></div>" % (color, msg)
    return ("<!DOCTYPE html><html lang=ar dir=rtl><head><meta charset=utf-8>"
            "<title>ربط جوجل درايف</title></head><body>" + body + "</body></html>")


# ---------------- routes ----------------
# path -> (handler, needs_admin)
ROUTES_POST = {
    "/api/stock/move": (lambda b,u: API.stock_move(b,u),False),
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
    "/api/window-size": (lambda b, u: API.window_size(b, u), False),
    "/api/create-shortcut": (lambda b, u: API.create_shortcut(b, u), False),
    "/api/users": (lambda b, u: API.create_user(b, u), True),
    "/api/import/parties": (lambda b, u: api_import_parties(b, u), True),
    "/api/do-update": (lambda b, u: API.do_update(b, u), True),
    "/api/wipe": (lambda b, u: API.wipe(b, u), True),
    "/api/sync/push": (lambda b, u: API.sync_push(b, u), True),
    "/api/drive/connect": (lambda b, u: API.drive_connect(b, u), True),
    "/api/drive/disconnect": (lambda b, u: API.drive_disconnect(b, u), True),
    "/api/drive/upload-now": (lambda b, u: API.drive_upload_now(b, u), True),
    "/api/treasury": (lambda b, u: API.create_treasury(b, u), False),
    "/api/prices": (lambda b, u: API.save_price(b, u), False),
    "/api/items": (lambda b, u: API.save_item(b, u), False),
    "/api/monthly-close": (lambda b, u: API.monthly_close(b, u), True),
    "/api/quotations": (lambda b, u: API.create_quote(b, u), False),
    "/api/restore": (lambda b, u: API.restore_backup(b, u), True),
    "/api/cloud-restore": (lambda b,u:API.restore_cloud_backup(b,u),True),
    "/api/monthly-open": (lambda b, u: API.monthly_open(b, u), True),
    "/api/sync/pull": (lambda b, u: API.sync_pull(b, u), True),
    "/api/mobile/config": (lambda b, u: API.mobile_config_save(b, u), True),
    "/api/sync/force-lock": (lambda b, u: API.sync_force_lock(b, u), True),
    "/api/unlock": (lambda b, u: API.unlock_session(b, u), False),
    "/api/approve": (lambda b, u: API.approve(b, u), False),
    "/api/sessions/kick": (lambda b, u: API.kick_session(b, u), True),
    "/api/mobile/pin-login": (lambda b, u: API.mobile_pin_login(b, u), False),
    "/api/mobile/lock": (lambda b, u: API.mobile_lock(b, u), False),
}
ROUTES_GET = {
    "/api/stock": (API.stock_list,False),
    "/api/backup-status": (lambda q,u: (200,dict(BACKUP_STATUS,cloud=dict(CLOUD_BACKUP_STATUS))),False),
    "/api/cloud-backups": (lambda q,u: (200,{"backups":drive_sync.list_full_backups() if drive_sync else []}),True),
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
    "/api/shortcut-status": (API.shortcut_status, False),
    "/api/sync/status": (API.sync_status, True),
    "/api/treasury": (API.list_treasury, False),
    "/api/cheques": (API.list_cheques, False),
    "/api/aging": (API.aging, False),
    "/api/activity": (API.list_activity, True),
    "/api/prices": (API.list_prices, False),
    "/api/item-names": (API.list_item_names, False),
    "/api/top-items": (API.top_items, False),
    "/api/item-monthly": (API.item_monthly, False),
    "/api/today": (API.today, False),
    "/api/model-profit": (API.model_profit, True),
    "/api/quotations": (API.list_quotations, False),
    "/api/mobile/bootstrap": (API.mobile_bootstrap, False),
    "/api/mobile/today-summary": (API.mobile_today_summary, False),
    "/api/item-last-price": (API.item_last_price, False),
    "/api/mobile/config": (API.mobile_config, True),
    "/api/parties/next-code": (API.party_next_code, True),
    "/api/update-log": (API.update_log, True),
    "/api/update-progress": (lambda q,u:(200,dict(UPDATE_STATUS)),True),
    "/api/drive/status": (API.drive_status, True),
    "/api/backups": (API.list_backups, True),
    "/api/collections": (API.collections, False),
    "/api/monthly-report": (API.monthly_report, True),
    "/api/items": (API.list_items, False),
    "/api/profit": (API.profit, True),
    "/api/users": (API.list_users, True),
    "/api/sessions": (API.list_sessions, True),
    "/api/check-update": (API.check_update, False),
    "/api/mobile/pin-status": (API.mobile_pin_status, False),
}

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png",
                ".jpg": "image/jpeg", ".ico": "image/x-icon", ".css": "text/css",
                ".js": "application/javascript"}
OPEN_PATHS = {"/api/login", "/api/setup", "/api/me", "/api/version", "/api/login_users", "/api/drive/callback"}
VIEW_GET_PATHS = {"/api/me", "/api/dashboard", "/api/customers", "/api/invoices",
                  "/api/today", "/api/version", "/api/mobile/bootstrap"}


def view_get_allowed(path):
    if path in VIEW_GET_PATHS:
        return True
    if path.startswith("/api/invoices/"):
        return path.rsplit("/", 1)[1].isdigit()
    return False


MOBILE_GET_EXTRA = {"/api/cheques", "/api/aging", "/api/statement", "/api/item-names",
                    "/api/mobile/today-summary", "/api/item-last-price", "/api/mobile/pin-status"}
MOBILE_POST_PATHS = {"/api/payments", "/api/invoices", "/api/customers",
                     "/api/mobile/pin-login", "/api/mobile/lock"}

KNOWN_SECTIONS = {"accounts", "inventory", "production", "settings"}
KNOWN_OPS = {"delete_docs", "monthly_close", "backup", "users_admin"}

MOBILE_PIN_OPEN = {"/api/mobile/pin-status", "/api/mobile/pin-login", "/api/mobile/lock",
                   "/api/me", "/api/version"}


def mobile_pin_blocked(user, path):
    if user.get("pin_ok") or path in MOBILE_PIN_OPEN:
        return False
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) c FROM users WHERE pin_hash<>'' AND disabled=0").fetchone()["c"]
    conn.close()
    return n > 0


def mobile_get_allowed(path):
    if path in MOBILE_GET_EXTRA:
        return True
    return view_get_allowed(path)


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

    def _send_html(self, html):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
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

    def _redirect(self, loc, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", loc)
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
        REQ.ua = self.headers.get("User-Agent") or ""
        REQ.ip = self.client_address[0] if self.client_address else ""
        path, q = u.path, parse_qs(u.query)
        if path in ("/", "/index.html", "/api/version", "/api/whoami"):
            _nixt_log("REQ GET " + path)
        try:
            if path == "/" or path == "/index.html":
                u0 = self._user()
                if u0 and u0["role"] in ("view", "mobile"):
                    return self._redirect("/mobile")
                return self._send_file(os.path.join(STATIC_DIR, "index.html"),
                                       "text/html; charset=utf-8")
            if path == "/m":
                conn = get_db()
                en = get_setting(conn, "mobile_enabled", "0") == "1"
                conn.close()
                key = (q.get("k") or [""])[0]
                if en and key and key == mobile_key_get():
                    token = new_session(None, "موبايل", "mobile")
                    extra = [pn + "=" + (q.get(pn) or [""])[0]
                             for pn in ("s", "inv", "sheet") if (q.get(pn) or [""])[0]]
                    loc = "/mobile" + ("?" + "&".join(extra) if extra else "")
                    return self._redirect(loc, self._set_session(token))
                return self._send(403, {"error": "mobile_link_invalid"})
            if path in ("/mobile", "/mobile.html"):
                return self._send_file(os.path.join(STATIC_DIR, "mobile.html"),
                                       "text/html; charset=utf-8")
            if path == "/manifest.json":
                return self._send_file(os.path.join(STATIC_DIR, "manifest.json"),
                                       "application/manifest+json; charset=utf-8")
            if path == "/favicon.ico":
                return self._send_file(os.path.join(STATIC_DIR, "favicon.ico"),
                                       "image/x-icon")
            if path == "/sw.js":
                return self._send_file(os.path.join(STATIC_DIR, "sw.js"),
                                       "application/javascript")
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
            if path == "/api/drive/callback":
                return self._send_html(drive_callback_html(q))
            if path in OPEN_PATHS:
                if path in ROUTES_GET:
                    fn, _adm = ROUTES_GET[path]
                    return self._send(*fn(q, user))
            if not user:
                return self._send(401, {"error": "login_required"})
            if user["role"] == "view" and not view_get_allowed(path):
                return self._send(403, {"error": "view_only"})
            if user["role"] == "mobile" and not mobile_get_allowed(path):
                return self._send(403, {"error": "view_only"})
            if user["role"] == "mobile" and mobile_pin_blocked(user, path):
                return self._send(403, {"error": "pin_required"})
            if path in ROUTES_GET:
                fn, adm = ROUTES_GET[path]
                if adm and user["role"] != "admin":
                    return self._send(403, {"error": "admin_required"})
                return self._send(*fn(q, user))
            if path.startswith("/api/cheques/") and path.endswith("/history"):
                return self._send(*API.cheque_history(int(path.split("/")[3]), user))
            if path.startswith("/api/invoices/"):
                return self._send(*API.get_invoice(int(path.rsplit("/", 1)[1]), user))
            if path.startswith("/api/payments/"):
                return self._send(*API.get_payment(int(path.rsplit("/", 1)[1]), user))
            if path.startswith("/api/quotations/"):
                return self._send(*API.get_quote(int(path.rsplit("/", 1)[1]), user))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        with DB_MUTATION_LOCK:
            self._do_POST()

    def _do_POST(self):
        u = urlparse(self.path)
        REQ.ua = self.headers.get("User-Agent") or ""
        REQ.ip = self.client_address[0] if self.client_address else ""
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
            if user["role"] == "view":
                return self._send(403, {"error": "view_only"})
            if user["role"] == "mobile" and path not in MOBILE_POST_PATHS:
                return self._send(403, {"error": "view_only"})
            if user["role"] == "mobile" and mobile_pin_blocked(user, path):
                return self._send(403, {"error": "pin_required"})
            if SYNC_STATE["locked_by"] and path not in ("/api/sync/pull", "/api/sync/force-lock", "/api/sync/status"):
                lk = SYNC_STATE["locked_by"]
                return self._send(423, {"error": "device_locked",
                                        "locked_by": lk.get("device", ""),
                                        "locked_ts": lk.get("ts", "")})
            if path in ROUTES_POST:
                fn, adm = ROUTES_POST[path]
                if adm and user["role"] != "admin":
                    return self._send(403, {"error": "admin_required"})
                return self._send(*fn(body, user))
            if path.startswith("/api/invoices/") and path.endswith("/reprice-retail"):
                return self._send(*API.reprice_invoice_retail(int(path.split("/")[3]), body, user))
            if path.startswith("/api/employees/") and path.endswith("/toggle"):
                return self._send(*API.toggle_employee(int(path.split("/")[3]), user))
            if path.startswith("/api/cheques/") and path.endswith("/status"):
                return self._send(*API.set_cheque_status(int(path.split("/")[3]), body, user))
            if path.startswith("/api/cheques/") and path.endswith("/endorse"):
                return self._send(*API.endorse_cheque(int(path.split("/")[3]), body, user))
            if path.startswith("/api/customers/"):
                return self._send(*API.update_customer(int(path.rsplit("/", 1)[1]), body, user))
            if path.startswith("/api/quotations/") and path.endswith("/convert"):
                return self._send(*API.convert_quote(int(path.strip("/").split("/")[2]), user))
            if path.startswith("/api/quotations/") and path.endswith("/cancel"):
                return self._send(*API.cancel_quote(int(path.strip("/").split("/")[2]), user))
            if path.startswith("/api/users/"):
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[2].isdigit():
                    uid = int(parts[2])
                    sub = parts[3]
                    if sub == "reset":
                        if user["role"] != "admin":
                            return self._send(403, {"error": "admin_required"})
                        return self._send(*API.reset_user(uid, body, user))
                    if sub == "pin":
                        return self._send(*API.set_pin(uid, body, user))
                    if sub == "role":
                        if user["role"] != "admin":
                            return self._send(403, {"error": "admin_required"})
                        return self._send(*API.set_role(uid, body, user))
                    if sub == "status":
                        if user["role"] != "admin":
                            return self._send(403, {"error": "admin_required"})
                        return self._send(*API.set_status(uid, body, user))
                    if sub == "unlock":
                        if user["role"] != "admin":
                            return self._send(403, {"error": "admin_required"})
                        return self._send(*API.unlock_user(uid, user))
                    if sub == "perms":
                        if user["role"] != "admin":
                            return self._send(403, {"error": "admin_required"})
                        return self._send(*API.set_perms(uid, body, user))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_DELETE(self):
        with DB_MUTATION_LOCK:
            self._do_DELETE()

    def _do_DELETE(self):
        u = urlparse(self.path)
        REQ.ua = self.headers.get("User-Agent") or ""
        REQ.ip = self.client_address[0] if self.client_address else ""
        try:
            user = self._user()
            if not user:
                return self._send(401, {"error": "login_required"})
            approver = ""
            if user["role"] != "admin":
                _dparts = u.path.strip("/").split("/")
                if len(_dparts) == 3 and _dparts[0] == "api" and _dparts[1] in ("invoices", "payments"):
                    approver = approval_check(self.headers.get("X-Approval") or "")
                    if not approver:
                        return self._send(403, {"error": "محتاج موافقة المدير بالرقم السري",
                                                "need_approval": True})
                else:
                    return self._send(403, {"error": "admin_required"})
            if SYNC_STATE["locked_by"]:
                lk = SYNC_STATE["locked_by"]
                return self._send(423, {"error": "device_locked",
                                        "locked_by": lk.get("device", ""),
                                        "locked_ts": lk.get("ts", "")})
            parts = u.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api":
                if parts[1] == "invoices":
                    return self._send(*API.delete_invoice(int(parts[2]), user, approver))
                if parts[1] == "payments":
                    return self._send(*API.delete_payment(int(parts[2]), user, approver))
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


def build_update_bat(old_dir):
    """Write the apply-update bat next to the program and return its path.
    All dynamic values travel via environment variables (%NIXT_*%): the bat
    body stays pure ASCII/literal, and nothing here may use Python % string
    formatting (a literal %VAR% in the template crashes formatting - this
    exact bug broke 2.7.0's updater)."""
    bat = os.path.join(old_dir, "nixt-apply-update.bat")
    kill = 'taskkill /F /PID %NIXT_PID% >>"%NIXT_LOG%" 2>&1'
    bat_lines = [
        '@echo off',
        'title Nixt Update',
        'cd /d "%TEMP%"',
        'echo === update start %date% %time% ===>>"%NIXT_LOG%"',
        'echo Waiting for the app to close...',
        'ping -n 4 127.0.0.1 >nul',
        kill,
        'ping -n 2 127.0.0.1 >nul',
        'echo Copying new files...',
        'robocopy "%NIXT_STAGING%" "%NIXT_OLD%" /E /IS /NFL /NDL /NJH /R:2 /W:1 >>"%NIXT_LOG%" 2>&1',
        'if errorlevel 8 goto failed',
        'echo Restarting...',
        'start "" /D "%NIXT_OLD%" "%NIXT_OLD%\\python\\pythonw.exe" "%NIXT_OLD%\\app.py"',
        'if errorlevel 1 goto failed',
        'ping -n 3 127.0.0.1 >nul',
        'rmdir /s /q "%NIXT_STAGING%" >>"%NIXT_LOG%" 2>&1',
        'rmdir /s /q "%NIXT_UPDIR%" >>"%NIXT_LOG%" 2>&1',
        'echo === update done %date% %time% ===>>"%NIXT_LOG%"',
        '(goto) 2>nul & del "%~f0"',
        ':failed',
        'echo Update failed. Files preserved for recovery.>>"%NIXT_LOG%"',
        'echo Update failed. Run the latest NixtSetup installer. See update-log.txt in your Nixt data folder.',
        'pause',
        'exit /b 1',
    ]
    with open(bat, "w", newline="") as f:
        f.write("\r\n".join(bat_lines) + "\r\n")
    return bat


def desktop_path():
    """Real Desktop folder (handles OneDrive redirection)."""
    if os.name == "nt":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            v, _ = winreg.QueryValueEx(k, "Desktop")
            winreg.CloseKey(k)
            return os.path.expandvars(v)
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


SHORTCUT_PS1 = (
    "$ErrorActionPreference = 'Stop'\r\n"
    "$ws = New-Object -ComObject WScript.Shell\r\n"
    "$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nixt.lnk'\r\n"
    "$sc = $ws.CreateShortcut($lnk)\r\n"
    "$sc.TargetPath = $env:NIXT_SC_TARGET\r\n"
    "$sc.WorkingDirectory = $env:NIXT_SC_DIR\r\n"
    "$sc.IconLocation = $env:NIXT_SC_ICON + ',0'\r\n"
    "$sc.Description = 'Nixt Accounts'\r\n"
    "$sc.Save()\r\n"
)


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
    args = [candidates[0], "--app=" + url, "--disable-features=Translate"]
    # Always maximize. Saved browser geometry and old window_size settings
    # must not override the owner's always-maximized preference.
    args.append("--start-maximized")
    try:
        process = subprocess.Popen(args)
        threading.Thread(target=maximize_nixt_window, daemon=True).start()
        return process
    except Exception:
        return None


def maximize_nixt_window():
    """Edge can reuse an existing process and ignore its launch flag.
    Maximize only a Chromium window with this app's exact document title.
    Bounded retry accommodates the time it takes to create that window.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.IsZoomed.argtypes = [wintypes.HWND]
        expected_title = "نيكست للحسابات - NIXT"
        for attempt in range(24):
            found = []
            @callback_type
            def visit(hwnd, unused):
                if not user32.IsWindowVisible(hwnd):
                    return True
                title = ctypes.create_unicode_buffer(512)
                klass = ctypes.create_unicode_buffer(128)
                user32.GetWindowTextW(hwnd, title, len(title))
                user32.GetClassNameW(hwnd, klass, len(klass))
                # Chromium may append its browser name to the document title.
                if (title.value == expected_title or title.value.startswith(expected_title + " - ")) and klass.value == "Chrome_WidgetWin_1":
                    found.append(hwnd)
                return True
            user32.EnumWindows(visit, 0)
            if found:
                for hwnd in found:
                    user32.ShowWindowAsync(hwnd, 3)  # SW_MAXIMIZE, not fullscreen
                _nixt_log("maximize requested for Nixt window")
                return
            time.sleep(0.25)
        _nixt_log("maximize: no matching Nixt window found within 6 seconds")
    except Exception:
        import traceback
        _nixt_log("maximize failed (launch continues):\n" + traceback.format_exc())


def open_browser():
    try:
        return bool(webbrowser.open("http://%s:%d/" % (HOST, PORT)))
    except Exception:
        return False


def launch_ui():
    """Try the standalone window, then the default browser without leaking errors."""
    try:
        if open_app_window() is not None:
            _nixt_log("window launched: app-window")
            return True
    except Exception:
        import traceback
        _nixt_log("app-window failed; trying browser:\n" + traceback.format_exc())
    if open_browser():
        _nixt_log("window launched: fallback-browser")
        return True
    _nixt_log("both window launch methods failed")
    show_message("Nixt could not open a window. See log:\n" + _nixt_log_path())
    return False


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
    _nixt_log("MESSAGE SHOWN: " + text.split("\n")[0][:160])
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


def cleanup_old_installs():
    """Delete sibling folders left by pre-1.7.1 updaters (NixtAccounts-x.y.z,
    _old-NixtAccounts*) and the stale handoff script. Keeps this folder and
    the newest other copy as a fallback, and never touches a folder that
    still holds a database."""
    import re
    try:
        parent = os.path.dirname(BASE_DIR.rstrip(os.sep)) or BASE_DIR
        me = os.path.normpath(BASE_DIR)
        cands = []
        for name in os.listdir(parent):
            low = name.lower()
            if not (re.fullmatch(r"nixtaccounts-\d+\.\d+\.\d+", low)
                    or low.startswith("_old-nixt")):
                continue
            p = os.path.normpath(os.path.join(parent, name))
            if p == me or not os.path.isdir(p):
                continue
            if os.path.isfile(os.path.join(p, "data", "nixt.db")):
                continue
            cands.append(p)

        def ver_key(p):
            m = re.search(r"(\d+)\.(\d+)\.(\d+)", os.path.basename(p))
            return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)

        cands.sort(key=ver_key, reverse=True)
        for p in cands[1:]:
            shutil.rmtree(p, ignore_errors=True)
        stale = os.path.join(parent, "nixt-run-new.bat")
        if os.path.isfile(stale):
            try:
                os.remove(stale)
            except OSError:
                pass
    except Exception:
        pass


def _port_check_and_resolve():
    """If the port is taken by another Nixt copy, open its window or kill it.
    Returns True when THIS instance should stop (another copy owns the port)."""
    if not port_busy():
        return False
    ver = running_server_version()
    _nixt_log("port busy; running version: %r" % (ver,))
    if ver == APP_VERSION and server_serves_index():
        launch_ui()
        return True
    if ver and version_tuple(ver) > version_tuple(APP_VERSION):
        # a NEWER copy is already running: just open its window
        launch_ui()
        return True
    if ver and version_tuple(ver) < version_tuple(APP_VERSION):
        # an older copy is stuck in the background: stop it and continue
        if not kill_stale_server():
            show_message(
                "فيه نسخة أقدم من البرنامج (إصدار " + ver + ") معلّقة ومش قادر أقفلها.\n\n"
                + "اعمل Restart للجهاز وبعدين شغّل البرنامج تاني.")
            return True
        return False
    show_message(
        "فيه برنامج تاني مسك منفذ التشغيل.\n\n"
        + "اعمل Restart للجهاز وبعدين شغّل البرنامج تاني.")
    return True


def _main():
    _nixt_log("=== app start ===")
    try:
        init_db()
        _nixt_log("init_db ok")
        threading.Thread(target=continuous_backup_worker,daemon=True).start()
        threading.Thread(target=cloud_backup_worker,daemon=True).start()
    except Exception:
        import traceback as _tb
        _nixt_log("init_db FAILED:\n" + _tb.format_exc())
        show_message("حصل خطأ في قاعدة البيانات أول التشغيل.\n\nابعت الملف ده لدعم نيكست:\n" + _nixt_log_path())
        return
    try:
        cleanup_old_installs()
        _nixt_log("cleanup ok")
    except Exception:
        import traceback as _tb
        _nixt_log("cleanup_old_installs FAILED (continuing):\n" + _tb.format_exc())
    try:
        if _port_check_and_resolve():
            return
    except Exception:
        import traceback as _tb
        _nixt_log("port check FAILED (continuing to bind):\n" + _tb.format_exc())
    _nixt_log("port free, continuing startup")
    try:
        auto_backup_tick()
    except Exception:
        import traceback as _tb
        _nixt_log("auto_backup FAILED (continuing):\n" + _tb.format_exc())
    threading.Thread(target=auto_backup_loop, daemon=True).start()
    if drive_sync is not None:
        try:
            drive_sync.init(DATA_DIR)
            drive_sync.start_worker(build_snapshot_bytes)
        except Exception:
            pass
    try:
        sync_on_startup()
    except Exception:
        import traceback as _tb
        _nixt_log("sync_on_startup FAILED (continuing):\n" + _tb.format_exc())
    global MOBILE_BOUND
    try:
        _c = get_db()
        MOBILE_BOUND = get_setting(_c, "mobile_enabled", "0") == "1"
        _c.close()
    except Exception:
        import traceback as _tb
        _nixt_log("mobile flag read FAILED (default off):\n" + _tb.format_exc())
        MOBILE_BOUND = False
    try:
        server = ThreadingHTTPServer(("0.0.0.0" if MOBILE_BOUND else HOST, PORT), Handler)
        _nixt_log("server bound (mobile_bound=%s)" % MOBILE_BOUND)
    except OSError:
        _nixt_log("server bind FAILED", )
        show_message(
            "البرنامج مش قادر يفتح منفذ التشغيل.\n\n"
            + "اعمل Restart للجهاز وبعدين شغّل البرنامج تاني.")
        return

    def launch_window():
        time.sleep(1.0)
        if not launch_ui():
            server.shutdown()

    def self_check():
        time.sleep(2.0)
        try:
            req = urllib.request.Request(
                "http://%s:%d/api/version" % (HOST, PORT),
                headers={"User-Agent": "NixtAccounts/" + APP_VERSION})
            with urllib.request.urlopen(req, timeout=3) as r:
                body = r.read().decode("utf-8", "ignore")
            _nixt_log("self-check ok: " + body[:120])
        except Exception as e:
            _nixt_log("self-check FAILED: %r" % (e,))

    LAST_ACTIVITY[0] = time.time()
    threading.Thread(target=launch_window, daemon=True).start()
    threading.Thread(target=self_check, daemon=True).start()
    threading.Thread(target=watchdog_loop, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        continuous_backup_flush()
        sync_on_shutdown()


def main():
    try:
        _main()
        _nixt_log("server loop exited normally")
    except Exception:
        import traceback as _tb
        _nixt_log("FATAL:\n" + _tb.format_exc())
        show_message("حصل خطأ وتعذّر تشغيل البرنامج.\n\nابعت الملف ده لدعم نيكست:\n" + _nixt_log_path())


if __name__ == "__main__":
    main()
