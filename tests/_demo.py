"""測試共用：在暫存資料夾建示範資料、在隨機埠啟動示範 server（不連外、不碰正式資料）。"""
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / 'app'
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0        # Windows：背景程序不跳主控台視窗


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def env(data, port=None):
    e = dict(os.environ, OPS_DATA=str(data), PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    if port:
        e['DEMO_PORT'] = str(port)
    return e


def seed(data):
    r = subprocess.run([sys.executable, str(APP / 'seed_demo.py')], env=env(data), capture_output=True, timeout=300,
                       creationflags=NO_WINDOW)
    out = r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')
    return r.returncode, out


class Demo:
    """seed＋啟動；stop() 走 /__demo_stop，萬一沒停就 terminate 這個子行程（只動自己開的那一個）。"""

    def __init__(self, restore_from=None):
        self.tmp = tempfile.TemporaryDirectory(prefix='svcops_')
        self.data = Path(self.tmp.name)
        self.port = free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        if restore_from:                                  # 從備份檔還原：直接把備份當成資料庫啟動
            import shutil
            shutil.copyfile(restore_from, self.data / 'operations.db')
            self.seed_rc, self.seed_out = 0, ''
        else:
            self.seed_rc, self.seed_out = seed(self.data)
        self.log = open(self.data / 'server_test.log', 'wb')
        self.proc = subprocess.Popen([sys.executable, str(APP / 'demo_server.py')], env=env(self.data, self.port),
                                     stdout=self.log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(self.base + '/health', timeout=2).read()
                return
            except Exception:
                time.sleep(0.3)
        self.stop()
        raise RuntimeError('示範 server 沒有起來：\n' + (self.data / 'server_test.log').read_text('utf-8', 'replace'))

    def stop(self):
        if self.proc.poll() is None:
            try:
                urllib.request.urlopen(self.base + '/__demo_stop', timeout=3).read()
            except Exception:
                pass
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(10)
        self.log.close()
        try:
            self.tmp.cleanup()
        except OSError:                 # Windows：SQLite 檔偶爾晚一點才放開
            pass

    # ── HTTP 小工具 ──
    def opener(self, who=None):
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), NoRedirect)
        if who:
            code, _, _ = self.get(op, '/enter?as=' + who)
            assert code == 302, code
        return op

    def get(self, op, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            r = op.open(req, timeout=15)
            return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read(), e.headers

    def post(self, op, path, body, csrf=None, origin=True):
        h = {'Content-Type': 'application/json'}
        if origin:
            h['Origin'] = self.base
        if csrf:
            h['X-CSRF-Token'] = csrf
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=h, method='POST')
        try:
            r = op.open(req, timeout=15)
            return r.status, json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {'raw': raw.decode('utf-8', 'replace')}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

    def http_error_302(self, req, fp, code, msg, headers):
        return fp

    http_error_301 = http_error_303 = http_error_307 = http_error_302
