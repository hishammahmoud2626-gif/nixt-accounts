#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nixt Accounts - offline desktop accounts system (Arabic UI).
Single-file stdlib server: http.server + sqlite3. No internet needed.
Data lives in data/nixt.db next to this file. Backups in backups/.
"""
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_VERSION = "1.0.0"

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


def fetch_update_info():
    """Fetch version.json from the configured URL. Returns dict or raises."""
    url = read_update_url()
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "NixtAccounts/" + APP_VERSION})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

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
  kind TEXT NOT NULL DEFAULT 'sale',      -- 'sale' or 'return'
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
"""


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
    conn.commit()
    conn.close()


def do_backup(manual=True):
    """Copy the DB to backups/ with a timestamp. Returns the backup path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, "nixt-%s.db" % stamp)
    # Use sqlite backup API for a consistent copy even while open.
    src = get_db()
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.close()
    src.close()
    # keep last 60 backups
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".db"))
    for old in files[:-60]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return dest


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def invoice_net(inv_id, conn):
    items = conn.execute(
        "SELECT COALESCE(SUM(pairs*price),0) AS t FROM invoice_items WHERE invoice_id=?",
        (inv_id,),
    ).fetchone()["t"]
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    return items + (inv["shipping"] or 0) + (inv["other_charges"] or 0) - (inv["discount"] or 0)


class API:
    """Route handlers. Each returns (status, obj)."""

    # ---------- customers ----------
    def list_customers(q):
        conn = get_db()
        out = rows(conn.execute("""
          SELECT c.*,
            COALESCE((SELECT SUM(ii.pairs*ii.price) FROM invoice_items ii
                      JOIN invoices i ON i.id=ii.invoice_id
                      WHERE i.customer_id=c.id AND i.kind='sale'),0) AS items_sales,
            COALESCE((SELECT SUM(CASE WHEN i.kind='sale'
                THEN (SELECT COALESCE(SUM(ii.pairs*ii.price),0) FROM invoice_items ii WHERE ii.invoice_id=i.id)
                     + i.shipping + i.other_charges - i.discount ELSE 0 END)
                FROM invoices i WHERE i.customer_id=c.id),0) AS sales_net,
            COALESCE((SELECT SUM(CASE WHEN i.kind='return'
                THEN (SELECT COALESCE(SUM(ii.pairs*ii.price),0) FROM invoice_items ii WHERE ii.invoice_id=i.id)
                     + i.shipping + i.other_charges - i.discount ELSE 0 END)
                FROM invoices i WHERE i.customer_id=c.id),0) AS returns_net,
            COALESCE((SELECT SUM(cash+card+transfer) FROM payments p WHERE p.customer_id=c.id),0) AS paid
          FROM customers c ORDER BY c.name"""))
        conn.close()
        for c in out:
            c["balance"] = round(c["sales_net"] - c["returns_net"] - c["paid"], 2)
        return 200, out

    def create_customer(body):
        name = (body.get("name") or "").strip()
        if not name:
            return 400, {"error": "name required"}
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO customers (name, phone, group_name, discount_pct, notes, created_at) VALUES (?,?,?,?,?,?)",
            (name, body.get("phone", ""), body.get("group_name", ""),
             float(body.get("discount_pct") or 0), body.get("notes", ""), now_str()))
        conn.commit()
        cid = cur.lastrowid
        conn.close()
        return 200, {"id": cid}

    def update_customer(cid, body):
        conn = get_db()
        conn.execute(
            "UPDATE customers SET name=?, phone=?, group_name=?, discount_pct=?, notes=? WHERE id=?",
            (body.get("name", ""), body.get("phone", ""), body.get("group_name", ""),
             float(body.get("discount_pct") or 0), body.get("notes", ""), cid))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    def delete_customer(cid):
        conn = get_db()
        used = conn.execute("SELECT COUNT(*) c FROM invoices WHERE customer_id=?", (cid,)).fetchone()["c"]
        used += conn.execute("SELECT COUNT(*) c FROM payments WHERE customer_id=?", (cid,)).fetchone()["c"]
        if used:
            conn.close()
            return 400, {"error": "customer has transactions"}
        conn.execute("DELETE FROM customers WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        return 200, {"ok": True}

    # ---------- invoices ----------
    def list_invoices(q):
        cid = q.get("customer_id", [""])[0]
        conn = get_db()
        sql = "SELECT * FROM invoices"
        args = []
        if cid:
            sql += " WHERE customer_id=?"
            args.append(cid)
        sql += " ORDER BY date DESC, id DESC"
        invs = rows(conn.execute(sql, args))
        for inv in invs:
            items = rows(conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (inv["id"],)))
            inv["items"] = items
            inv["items_total"] = round(sum(i["pairs"] * i["price"] for i in items), 2)
            inv["net"] = round(invoice_net(inv["id"], conn), 2)
            cust = conn.execute("SELECT name FROM customers WHERE id=?", (inv["customer_id"],)).fetchone()
            inv["customer_name"] = cust["name"] if cust else "?"
            inv["pairs"] = sum(i["pairs"] for i in items)
        conn.close()
        return 200, invs

    def create_invoice(body):
        cid = body.get("customer_id")
        if not cid:
            return 400, {"error": "customer_id required"}
        kind = body.get("kind", "sale")
        if kind not in ("sale", "return"):
            return 400, {"error": "bad kind"}
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO invoices (customer_id, date, kind, shipping, other_charges, discount, notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
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
        conn.close()
        return 200, {"id": iid, "net": invoice_net(iid, get_db())}

    def get_invoice(iid):
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
    def list_payments(q):
        cid = q.get("customer_id", [""])[0]
        conn = get_db()
        sql = "SELECT p.*, c.name AS customer_name FROM payments p JOIN customers c ON c.id=p.customer_id"
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

    def create_payment(body):
        cid = body.get("customer_id")
        if not cid:
            return 400, {"error": "customer_id required"}
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO payments (customer_id, date, cash, card, transfer, notes, created_at) VALUES (?,?,?,?,?,?,?)",
            (cid, body.get("date") or datetime.now().strftime("%Y-%m-%d"),
             float(body.get("cash") or 0), float(body.get("card") or 0),
             float(body.get("transfer") or 0), body.get("notes", ""), now_str()))
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
    def statement(q):
        cid = q.get("customer_id", [""])[0]
        if not cid:
            return 400, {"error": "customer_id required"}
        conn = get_db()
        lines = []
        for inv in conn.execute("SELECT * FROM invoices WHERE customer_id=?", (cid,)):
            net = invoice_net(inv["id"], conn)
            sign = 1 if inv["kind"] == "sale" else -1
            lines.append({
                "date": inv["date"], "sort": inv["created_at"], "type": inv["kind"],
                "ref": inv["id"],
                "desc": ("فاتورة #%s" % inv["id"]) if inv["kind"] == "sale" else ("مرتجع #%s" % inv["id"]),
                "debit": round(net, 2) if sign > 0 else 0,
                "credit": round(net, 2) if sign < 0 else 0,
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
            lines.append({
                "date": p["date"], "sort": p["created_at"], "type": "payment", "ref": p["id"],
                "desc": "دفعة (%s)" % "/".join(parts) if parts else "دفعة",
                "debit": 0, "credit": round(total, 2),
            })
        lines.sort(key=lambda l: (l["date"], l["sort"]))
        bal = 0.0
        for l in lines:
            bal += l["debit"] - l["credit"]
            l["balance"] = round(bal, 2)

        sales = returns = 0.0
        pairs = 0
        for inv in conn.execute("SELECT * FROM invoices WHERE customer_id=?", (cid,)):
            net = invoice_net(inv["id"], conn)
            if inv["kind"] == "sale":
                sales += net
                pairs += conn.execute("SELECT COALESCE(SUM(pairs),0) s FROM invoice_items WHERE invoice_id=?",
                                      (inv["id"],)).fetchone()["s"]
            else:
                returns += net
        paid = conn.execute("SELECT COALESCE(SUM(cash+card+transfer),0) s FROM payments WHERE customer_id=?",
                            (cid,)).fetchone()["s"]
        remaining = round(sales - returns - paid, 2)
        pct = round(100.0 * paid / sales, 1) if sales > 0 else 0
        cust = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        conn.close()
        return 200, {
            "customer": dict(cust) if cust else None,
            "lines": lines,
            "summary": {
                "sales": round(sales, 2), "returns": round(returns, 2),
                "paid": round(paid, 2), "remaining": remaining,
                "pairs": pairs, "payback_pct": pct,
            },
        }

    # ---------- dashboard ----------
    def dashboard(q):
        conn = get_db()
        sales = returns = 0.0
        pairs = 0
        for inv in conn.execute("SELECT * FROM invoices"):
            net = invoice_net(inv["id"], conn)
            if inv["kind"] == "sale":
                sales += net
                pairs += conn.execute("SELECT COALESCE(SUM(pairs),0) s FROM invoice_items WHERE invoice_id=?",
                                      (inv["id"],)).fetchone()["s"]
            else:
                returns += net
        paid = conn.execute("SELECT COALESCE(SUM(cash+card+transfer),0) s FROM payments").fetchone()["s"]
        ncustomers = conn.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
        ninvoices = conn.execute("SELECT COUNT(*) c FROM invoices WHERE kind='sale'").fetchone()["c"]
        top = rows(conn.execute("""
          SELECT c.id, c.name FROM customers c"""))
        balances = {}
        for c in conn.execute("SELECT id FROM customers"):
            balances[c["id"]] = 0.0
        for inv in conn.execute("SELECT * FROM invoices"):
            net = invoice_net(inv["id"], conn)
            balances[inv["customer_id"]] = balances.get(inv["customer_id"], 0) + (net if inv["kind"] == "sale" else -net)
        for p in conn.execute("SELECT * FROM payments"):
            balances[p["customer_id"]] = balances.get(p["customer_id"], 0) - (p["cash"] + p["card"] + p["transfer"])
        names = {c["id"]: c["name"] for c in conn.execute("SELECT id, name FROM customers")}
        debtors = sorted(
            ({"id": k, "name": names.get(k, "?"), "balance": round(v, 2)} for k, v in balances.items() if v > 0.005),
            key=lambda d: -d["balance"])[:10]
        conn.close()
        return 200, {
            "sales": round(sales, 2), "returns": round(returns, 2),
            "paid": round(paid, 2), "remaining": round(sales - returns - paid, 2),
            "pairs": pairs, "customers": ncustomers, "invoices": ninvoices,
            "debtors": debtors,
        }

    def backup(q):
        dest = do_backup()
        return 200, {"ok": True, "file": dest}

    # ---------- updates ----------
    def version(q):
        return 200, {"version": APP_VERSION, "update_configured": bool(read_update_url())}

    def check_update(q):
        if not read_update_url():
            return 200, {"configured": False}
        try:
            info = fetch_update_info()
        except Exception as e:
            return 200, {"configured": True, "online": False, "error": str(e),
                         "current": APP_VERSION}
        latest = str(info.get("version", ""))
        return 200, {
            "configured": True, "online": True,
            "current": APP_VERSION, "latest": latest,
            "update_available": version_tuple(latest) > version_tuple(APP_VERSION),
            "notes": info.get("notes", ""),
            "has_zip": bool(info.get("zip_url")),
        }

    def do_update(body):
        """Download the new portable zip, unpack it next to this folder as
        NixtAccounts-<version>, copy data/ and backups/ over, then hand off to
        a launcher that starts the new copy after this server exits."""
        info = fetch_update_info()
        latest = str(info.get("version", ""))
        if not (version_tuple(latest) > version_tuple(APP_VERSION)):
            return 400, {"error": "no newer version"}
        zip_url = info.get("zip_url")
        if not zip_url:
            return 400, {"error": "no zip url"}
        os.makedirs(UPDATES_DIR, exist_ok=True)
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
            # zip may contain a single top folder; strip it
            names = z.namelist()
            top = names[0].split("/")[0] if names else ""
            strip = top + "/" if all(n.startswith(top + "/") or n == top for n in names) else ""
            for n in names:
                rel = n[len(strip):] if strip and n.startswith(strip) else n
                if not rel or rel.endswith("/"):
                    continue
                dest = os.path.join(target, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(n) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        # carry over user data (never overwritten by the zip)
        for folder in ("data", "backups"):
            src_d = os.path.join(BASE_DIR, folder)
            dst_d = os.path.join(target, folder)
            if os.path.isdir(src_d):
                shutil.copytree(src_d, dst_d, dirs_exist_ok=True)
        # hand off: wait for this server to exit, then start the new copy
        bat = os.path.join(UPDATES_DIR, "run-new.bat")
        new_start = os.path.join(target, "START.bat")
        with open(bat, "w") as f:
            f.write('@echo off\r\nping -n 4 127.0.0.1 >nul\r\nstart "" "%s"\r\n' % new_start)
        def _handoff():
            try:
                subprocess.Popen(["cmd", "/c", bat], cwd=UPDATES_DIR)
            except Exception:
                pass
            os._exit(0)
        threading.Timer(1.0, _handoff).start()
        return 200, {"ok": True, "new_folder": target, "version": latest}


ROUTES_POST = {
    "/api/customers": lambda b: API.create_customer(b),
    "/api/invoices": lambda b: API.create_invoice(b),
    "/api/payments": lambda b: API.create_payment(b),
    "/api/backup": lambda b: API.backup({}),
    "/api/do-update": lambda b: API.do_update(b),
}
ROUTES_GET = {
    "/api/customers": API.list_customers,
    "/api/invoices": API.list_invoices,
    "/api/payments": API.list_payments,
    "/api/statement": API.statement,
    "/api/dashboard": API.dashboard,
    "/api/version": API.version,
    "/api/check-update": API.check_update,
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, ctype):
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        try:
            if path == "/" or path == "/index.html":
                return self._send_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
            if path in ROUTES_GET:
                return self._send(*ROUTES_GET[path](q))
            if path.startswith("/api/invoices/"):
                return self._send(*API.get_invoice(int(path.rsplit("/", 1)[1])))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        try:
            if u.path in ROUTES_POST:
                return self._send(*ROUTES_POST[u.path](body))
            if u.path.startswith("/api/customers/"):
                return self._send(*API.update_customer(int(u.path.rsplit("/", 1)[1]), body))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_DELETE(self):
        u = urlparse(self.path)
        try:
            parts = u.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api":
                if parts[1] == "invoices":
                    return self._send(*API.delete_invoice(int(parts[2])))
                if parts[1] == "payments":
                    return self._send(*API.delete_payment(int(parts[2])))
                if parts[1] == "customers":
                    return self._send(*API.delete_customer(int(parts[2])))
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


def open_browser():
    time.sleep(1.0)
    try:
        webbrowser.open("http://%s:%d/" % (HOST, PORT))
    except Exception:
        pass


def main():
    init_db()
    if port_busy():
        # Already running: just open the browser and exit.
        webbrowser.open("http://%s:%d/" % (HOST, PORT))
        return
    # automatic daily backup on startup (once per day)
    try:
        today = datetime.now().strftime("%Y%m%d")
        existing = [f for f in os.listdir(BACKUP_DIR) if f.startswith("nixt-%s" % today)]
        if not existing and os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
            do_backup(manual=False)
    except Exception:
        pass
    threading.Thread(target=open_browser, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
