#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression test for the 2.7.0 updater crash
('not enough arguments for format string').

Runs the real do_update path end to end with a file:// version.json and zip:
fetch info -> download -> extract -> build the apply-update bat. The OS
handoff timer is stubbed so the test process survives (the real handoff
calls os._exit). Pre-fix this test fails while the bat lines are built.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_app(tmp):
    os.environ["NIXT_DATA_DIR"] = os.path.join(tmp, "data-root")
    spec = importlib.util.spec_from_file_location("nixt_app", os.path.join(ROOT, "app.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    tmp = tempfile.mkdtemp(prefix="nixt-test-update-")
    try:
        app = load_app(tmp)
        old_dir = os.path.join(tmp, "NixtAccounts")
        os.makedirs(old_dir)
        # Redirect the folders do_update touches: BASE_DIR is where the bat
        # lands and where robocopy would copy over - never the real folder.
        app.BASE_DIR = old_dir
        app.UPDATES_DIR = os.path.join(old_dir, "updates")

        # Fake "new version" package served over file://
        pkg = os.path.join(tmp, "pkg")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "app.py"), "w") as f:
            f.write("# new version\n")
        with open(os.path.join(pkg, "START.bat"), "w") as f:
            f.write("@echo off\r\n")
        zip_path = os.path.join(tmp, "new.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.write(os.path.join(pkg, "app.py"), "app.py")
            z.write(os.path.join(pkg, "START.bat"), "START.bat")
        app.fetch_update_info = lambda: ({"version": "99.0.0",
                                          "zip_url": "file://" + zip_path}, None, "")

        # Stub the handoff timer so the process is not killed.
        fired = []

        class FakeTimer:
            def __init__(self, delay, fn):
                fired.append((delay, fn))

            def start(self):
                pass

        real_timer = app.threading.Timer
        app.threading.Timer = FakeTimer
        try:
            code, out = app.API.do_update({}, {"username": "tester", "role": "admin"})
        finally:
            app.threading.Timer = real_timer

        assert code == 200, (code, out)
        assert fired, "handoff timer was never scheduled"

        # The bat: written, CRLF, port baked in, loop var intact, env paths.
        bat = os.path.join(old_dir, "nixt-apply-update.bat")
        assert os.path.isfile(bat), "bat missing"
        raw = open(bat, "rb").read()
        assert raw.replace(b"\r\n", b"").find(b"\n") == -1, "line endings not CRLF"
        text = raw.decode("ascii")
        assert 'findstr /C:":%d "' % app.PORT in text, "port missing from kill line"
        assert "%%a" in text, "for-loop variable mangled"
        assert '"%NIXT_STAGING%"' in text and '"%NIXT_OLD%"' in text
        assert 'robocopy "%NIXT_STAGING%" "%NIXT_OLD%"' in text

        # Download + extract landed where do_update put them.
        staging = os.path.join(app.UPDATES_DIR, "extract-99.0.0")
        assert os.path.isfile(os.path.join(staging, "app.py")), "staging extract failed"
        assert os.path.isfile(os.path.join(app.UPDATES_DIR, "nixt-update-99.0.0.zip"))

        print("PASS: do_update full path OK - bat has port %d, CRLF, loop var intact" % app.PORT)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
