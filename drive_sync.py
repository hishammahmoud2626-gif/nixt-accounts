# -*- coding: utf-8 -*-
"""
Google Drive snapshot sync for Nixt (phase 1: PC -> Drive, view-only).

After every local change, the app uploads a small JSON snapshot of the
view screens (dashboard / customers / invoices / today) into the hidden
app-data folder of the user's OWN Google Drive. The phone app reads that
copy when the PC is off. The PC database is never uploaded and never read
back from Drive, so sync conflicts are impossible by design.

OAuth: "Desktop app" client, loopback redirect to the app's own server
(http://127.0.0.1:PORT/api/drive/callback). Scope is drive.appdata only:
the app can see just its own hidden folder, nothing else in Drive.
The refresh token lives next to the database in drive_token.json.

CLIENT_ID / CLIENT_SECRET are injected at build time (Google treats
installed-app credentials as non-secret by design); the source repo keeps
placeholders so nothing account-specific is committed.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "__NIXT_GCLIENT_ID__"
CLIENT_SECRET = "__NIXT_GCLIENT_SECRET__"

SCOPE = "https://www.googleapis.com/auth/drive.appdata"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
SNAPSHOT_NAME = "nixt_snapshot.json"

TOKEN_PATH = None
_log = lambda *a: None

_dirty = 0.0          # timestamp of last local change (0 = clean)
_last_try = 0.0       # last upload attempt (for backoff)
_last_error = ""
_last_ok = ""
_pending_state = ""   # OAuth state we are waiting for
_upload_lock = threading.Lock()
_started = False


def init(data_dir, log=None):
    global TOKEN_PATH, _log
    TOKEN_PATH = os.path.join(data_dir, "drive_token.json")
    if log:
        _log = log


def configured():
    return CLIENT_ID.startswith("__") is False and CLIENT_ID != ""


def connected():
    return configured() and TOKEN_PATH and os.path.isfile(TOKEN_PATH)


def status():
    tok = _read_token()
    return {
        "configured": configured(),
        "connected": bool(tok),
        "pending": _dirty > 0,
        "last_ok": _last_ok,
        "last_error": _last_error,
    }


def mark_dirty():
    global _dirty
    _dirty = time.time()


def _read_token():
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_token(tok):
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tok, f)
    os.replace(tmp, TOKEN_PATH)


def disconnect():
    global _dirty, _last_error, _last_ok
    try:
        os.remove(TOKEN_PATH)
    except OSError:
        pass
    _dirty = 0.0
    _last_error = ""
    _last_ok = ""


# ---------------- OAuth ----------------

def begin_auth(redirect_uri):
    """Returns (auth_url, state)."""
    global _pending_state
    import secrets as _sec
    _pending_state = _sec.token_urlsafe(16)
    q = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": _pending_state,
    })
    return AUTH_URI + "?" + q, _pending_state


def finish_auth(code, state, redirect_uri):
    if not state or state != _pending_state:
        return False, "state mismatch"
    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode()
    try:
        data = _post_json(TOKEN_URI, body)
    except Exception as e:
        return False, str(e)
    tok = {
        "refresh_token": data.get("refresh_token", ""),
        "access_token": data.get("access_token", ""),
        "expiry": time.time() + int(data.get("expires_in", 3500)) - 120,
    }
    if not tok["refresh_token"]:
        return False, "no refresh token (approve again with consent prompt)"
    _write_token(tok)
    mark_dirty()  # push a first snapshot right away
    return True, "ok"


def _access_token():
    tok = _read_token()
    if not tok:
        return None
    if tok.get("access_token") and tok.get("expiry", 0) > time.time():
        return tok["access_token"]
    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    data = _post_json(TOKEN_URI, body)
    tok["access_token"] = data.get("access_token", "")
    tok["expiry"] = time.time() + int(data.get("expires_in", 3500)) - 120
    _write_token(tok)
    return tok["access_token"]


# ---------------- HTTP helpers ----------------

def _post_json(url, body, headers=None):
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _api_json(method, url, token, raw=None, ctype=None):
    req = urllib.request.Request(url, data=raw, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = r.read().decode("utf-8")
        return json.loads(txt) if txt.strip() else {}


# ---------------- upload ----------------

def _find_snapshot_id(token):
    q = urllib.parse.urlencode({
        "spaces": "appDataFolder",
        "q": "name='%s'" % SNAPSHOT_NAME,
        "fields": "files(id,name)",
    })
    data = _api_json("GET", API + "?" + q, token)
    files = data.get("files", [])
    return files[0]["id"] if files else None


def upload_now(payload_bytes):
    """Uploads payload_bytes as the single snapshot file. Returns True/False."""
    global _last_error, _last_ok
    with _upload_lock:
        try:
            token = _access_token()
            if not token:
                raise RuntimeError("not connected")
            fid = _find_snapshot_id(token)
            if fid:
                _api_json("PATCH", UPLOAD_API + "/" + fid + "?uploadType=media",
                          token, raw=payload_bytes, ctype="application/json")
            else:
                boundary = "nixt" + str(int(time.time() * 1000))
                meta = json.dumps({"name": SNAPSHOT_NAME,
                                   "parents": ["appDataFolder"]}).encode()
                body = (b"--" + boundary.encode() + b"\r\n"
                        b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                        + meta + b"\r\n"
                        b"--" + boundary.encode() + b"\r\n"
                        b"Content-Type: application/json\r\n\r\n"
                        + payload_bytes + b"\r\n"
                        b"--" + boundary.encode() + b"--")
                _api_json("POST", UPLOAD_API + "?uploadType=multipart",
                          token, raw=body,
                          ctype='multipart/related; boundary=' + boundary)
            _last_ok = time.strftime("%Y-%m-%d %H:%M:%S")
            _last_error = ""
            return True
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")[:200]
            except Exception:
                detail = ""
            _last_error = "HTTP %s %s" % (e.code, detail)
        except Exception as e:
            _last_error = str(e)[:200]
        _log("drive_sync upload failed:", _last_error)
        return False


# ---------------- background worker ----------------

def start_worker(build_snapshot_bytes):
    """build_snapshot_bytes() -> bytes. Called after changes, debounced."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        global _dirty, _last_try
        while True:
            time.sleep(2)
            try:
                if not _dirty or not connected():
                    continue
                idle = time.time() - _dirty
                backoff = time.time() - _last_try
                if idle < 4 or backoff < 25:
                    continue
                _last_try = time.time()
                payload = build_snapshot_bytes()
                if upload_now(payload):
                    _dirty = 0.0
            except Exception as e:
                _log("drive_sync worker error:", e)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
