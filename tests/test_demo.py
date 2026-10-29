"""示範版端到端測試：seed、server、主要頁面、免登入、停止開關、對外連線封鎖、預約成立報價、權限。
執行：python -m unittest discover -s tests -v"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _demo import APP, NO_WINDOW, Demo, env, seed  # noqa: E402

DEMO = None


def setUpModule():
    global DEMO
    DEMO = Demo()


def tearDownModule():
    if DEMO:
        DEMO.stop()


class SeedTest(unittest.TestCase):
    def test_seed_ok(self):
        self.assertEqual(DEMO.seed_rc, 0, DEMO.seed_out)
        line = next(x for x in DEMO.seed_out.splitlines() if '示範資料建立完成' in x)
        n = json.loads(line.split('：', 1)[1])
        self.assertEqual((n['customers'], n['workers'], n['users']), (15, 6, 4))
        self.assertGreater(n['orders'], 0)
        self.assertTrue((DEMO.data / 'operations.db').exists())

    def test_seed_data_is_fictional(self):
        con = sqlite3.connect(DEMO.data / 'operations.db')
        phones = [r[0] for r in con.execute("SELECT phone FROM workers UNION ALL SELECT phone FROM customers") if r[0]]
        con.close()
        self.assertTrue(phones)
        self.assertTrue(all(p.startswith('0900-000-') for p in phones), phones)

    def test_reseed_keeps_old_db(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(seed(d)[0], 0)
            self.assertEqual(seed(d)[0], 0)
            self.assertTrue(any(p.name.startswith('_舊示範資料_') for p in Path(d).iterdir()))


class PagesTest(unittest.TestCase):
    def test_main_pages_200(self):
        op = DEMO.opener()
        for path in ('/', '/intro', '/book', '/join', '/mall', '/privacy', '/home', '/store', '/hi', '/health',
                     '/style.css', '/app.js', '/demo-banner.js', '/manifest.webmanifest'):
            code, body, _ = DEMO.get(op, path)
            self.assertEqual(code, 200, path)
            self.assertTrue(body, path)

    def test_first_visit_shows_intro_and_ad(self):
        code, body, _ = DEMO.get(DEMO.opener(), '/')
        html = body.decode('utf-8')
        self.assertEqual(code, 200)
        self.assertIn('傳統做法', html)
        self.assertIn('0928-880-392', html)

    def test_ad_bar_kept(self):
        _, body, _ = DEMO.get(DEMO.opener(), '/demo-banner.js')
        self.assertIn('需要客製化？加 LINE 林其衞（0928-880-392）', body.decode('utf-8'))

    def test_admin_pages_after_login(self):
        op = DEMO.opener('admin')
        code, body, _ = DEMO.get(op, '/')
        self.assertEqual(code, 200)
        self.assertNotIn('傳統做法', body.decode('utf-8'))      # 登入後是系統畫面，不是說明頁
        for path in ('/api/me', '/api/state', '/api/quotes', '/api/billing'):
            self.assertEqual(DEMO.get(op, path)[0], 200, path)

    def test_api_requires_login(self):
        op = DEMO.opener()
        for path in ('/api/state', '/api/quotes', '/api/billing', '/api/users'):
            self.assertEqual(DEMO.get(op, path)[0], 401, path)


class EnterTest(unittest.TestCase):
    def loc(self, q):
        code, _, h = DEMO.get(DEMO.opener(), '/enter' + q)
        self.assertEqual(code, 302, q)
        return h.get('Location'), h.get('Set-Cookie') or ''

    def test_enter_each_role(self):
        for who, want in (('admin', '/'), ('staff', '/'), ('finance', '/'), ('worker', '/wp/demo-portal-worker-01')):
            loc, cookie = self.loc('?as=' + who)
            self.assertEqual(loc, want, who)
            self.assertIn('HttpOnly', cookie)

    def test_enter_unknown_role_falls_back_to_admin(self):
        self.assertEqual(self.loc('?as=root')[0], '/')
        self.assertEqual(self.loc('')[0], '/')

    def test_enter_go_param(self):
        self.assertEqual(self.loc('?as=admin&go=quotes')[0], '/#quotes')
        self.assertEqual(self.loc('?as=staff&go=board')[0], '/#board')

    def test_enter_go_rejects_unsafe(self):
        for bad in ('..%2Fevil', 'https%3A%2F%2Fevil.example', '%3Cscript%3E', 'a' * 21, '%E5%A0%B1%E5%83%B9'):
            self.assertEqual(self.loc('?as=admin&go=' + bad)[0], '/', bad)
        self.assertEqual(self.loc('?as=worker&go=quotes')[0], '/wp/demo-portal-worker-01')   # 服務人員不吃 go


class StopTest(unittest.TestCase):
    def test_stop_rejects_proxied_or_foreign_host(self):
        op = DEMO.opener()
        for h in ({'X-Forwarded-For': '203.0.113.9'}, {'Cf-Connecting-Ip': '203.0.113.9'}, {'Host': 'demo.example.com'},
                  {'Forwarded': 'for=203.0.113.9'}):
            self.assertEqual(DEMO.get(op, '/__demo_stop', h)[0], 404, h)
        self.assertNotEqual(DEMO.get(op, '/__demo_stopx')[0], 200)
        self.assertEqual(DEMO.get(op, '/health')[0], 200)        # 還活著

    def test_stop_local_works(self):
        d = Demo()                                            # 另開一個，避免影響其他測試
        try:
            code, body, _ = d.get(d.opener(), '/__demo_stop')
            self.assertEqual(code, 200)
            self.assertIn('已停止', body.decode('utf-8'))
            d.proc.wait(10)
            self.assertIsNotNone(d.proc.poll())
        finally:
            d.stop()


class OutboundBlockedTest(unittest.TestCase):
    CODE = r'''
import json, socket, sys, urllib.request
sys.path.insert(0, sys.argv[1]); import demo_server
out = {}
for name, fn in (('dns', lambda: socket.getaddrinfo('api.line.me', 443)),
                 ('ip', lambda: socket.create_connection(('1.1.1.1', 443), timeout=3)),
                 ('https', lambda: urllib.request.urlopen('https://api.line.me/v2/bot/info', timeout=3)),
                 ('other_local_port', lambda: socket.create_connection(('127.0.0.1', 1), timeout=3))):
    try:
        fn(); out[name] = 'connected'
    except Exception as e:
        out[name] = type(e).__name__ + ':' + str(e)[:80]
print(json.dumps(out, ensure_ascii=False))
'''

    def test_outbound_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, '-c', self.CODE, str(APP)], env=env(d, 5999), capture_output=True, timeout=60,
                               creationflags=NO_WINDOW)
        out = json.loads(r.stdout.decode('utf-8').strip().splitlines()[-1])
        self.assertEqual(set(out), {'dns', 'ip', 'https', 'other_local_port'})
        for k, v in out.items():
            self.assertNotEqual(v, 'connected', k)
        for k in ('dns', 'ip', 'https'):                   # 對外：被示範版的防護擋下（不是剛好網路不通）
            self.assertIn('示範版已關閉對外連線', out[k], k)


class BookTest(unittest.TestCase):
    def form(self, **kw):
        op = DEMO.opener()
        o = json.loads(DEMO.get(op, '/api/public/options?b=demoshop')[1])
        f = {'account': 'demoshop', 'svc_items': [{'code': o['generic'][1]['code'], 'qty': 2}], 'date': '2030-01-15', 'slot': o['slots'][0],
             'area': '臺北市大安區', 'address': '臺北市大安區示範路 1 號', 'name': '測試客人', 'phone': '0900-000-999', 'notes': '',
             'website': '', 'photo_via': 'upload', 'consent': True}
        f.update(kw)
        return op, o, f

    def test_estimate(self):
        op, o, f = self.form()
        code, e = DEMO.post(op, '/api/public/estimate', f)
        self.assertEqual(code, 200)
        self.assertEqual(e['total'], o['generic'][1]['price'] * 2)
        self.assertFalse(any(k.startswith('internal_') for k in e))

    def test_book_creates_quote_visible_to_admin(self):
        op, o, f = self.form(name='預約測試甲')
        code, q = DEMO.post(op, '/api/public/quote', f)
        self.assertEqual(code, 200, q)
        self.assertTrue(q['token'] and q['no'])
        code, r = DEMO.post(op, f"/api/public/quote/{q['token']}/submit", {})
        self.assertEqual((code, r.get('stage')), (200, 'review'))
        qs = json.loads(DEMO.get(DEMO.opener('admin'), '/api/quotes')[1])['quotes']
        mine = [x for x in qs if x['token'] == q['token']]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['total'], o['generic'][1]['price'] * 2)
        self.assertEqual(mine[0]['stage'], 'review')
        self.assertEqual(DEMO.get(op, '/q/' + q['token'])[0], 200)       # 客人看得到自己的報價單

    def test_book_requires_consent(self):
        op, _, f = self.form(consent=False)
        code, r = DEMO.post(op, '/api/public/quote', f)
        self.assertGreaterEqual(code, 400)
        self.assertIn('同意', json.dumps(r, ensure_ascii=False))

    def test_book_requires_same_origin(self):
        op, _, f = self.form()
        self.assertGreaterEqual(DEMO.post(op, '/api/public/quote', f, origin=False)[0], 400)

    def test_book_rejects_garbage(self):
        op, _, f = self.form(phone='12', name='')
        self.assertGreaterEqual(DEMO.post(op, '/api/public/quote', f)[0], 400)


class PermissionTest(unittest.TestCase):
    OTHERS = ('陳美玉', '黃雅芬', '張淑華', '李佩君', '吳家豪', '0900-000-102', '0900-000-103')

    def test_worker_sees_only_own_data(self):
        op = DEMO.opener('worker')
        code, body, _ = DEMO.get(op, '/api/state')
        self.assertEqual(code, 200)
        st = json.loads(body)
        self.assertEqual(st['role'], 'cleaner')
        txt = body.decode('utf-8')
        for n in self.OTHERS:
            self.assertNotIn(n, txt, n)
        self.assertTrue(all(s.get('worker_id') in (None, 'wor-demo01') for s in st.get('settlements') or []))

    def test_worker_blocked_from_admin_apis(self):
        op = DEMO.opener('worker')
        for path in ('/api/users', '/api/billing', '/api/ledger', '/api/settlements', '/api/quotes', '/api/customers/unbound', '/api/payhist'):
            self.assertEqual(DEMO.get(op, path)[0], 403, path)

    def test_staff_blocked_from_money(self):
        op = DEMO.opener('staff')
        for path in ('/api/billing', '/api/ledger', '/api/payhist'):
            self.assertEqual(DEMO.get(op, path)[0], 403, path)

    def test_staff_cannot_see_or_change_worker_bank(self):
        """沒有 money 權限的客服：看不到服務人員的銀行帳戶與領薪週期，用 /api/save 也改不了。"""
        bank = ('bank_name', 'bank_account', 'bank_holder', 'payout_cycle')
        adm = DEMO.opener('admin')
        w_admin = next(w for w in json.loads(DEMO.get(adm, '/api/state')[1])['workers'] if w['id'] == 'wor-demo03')
        self.assertTrue(w_admin['bank_account'])                       # 管理員看得到（對照組）
        op = DEMO.opener('staff')
        st = json.loads(DEMO.get(op, '/api/state')[1])
        self.assertTrue(st['workers'])
        for w in st['workers']:
            for k in bank:
                self.assertIsNone(w.get(k), (w['id'], k))
        self.assertNotIn(w_admin['bank_account'], json.dumps(st, ensure_ascii=False))
        w = next(x for x in st['workers'] if x['id'] == 'wor-demo03')
        code, r = DEMO.post(op, '/api/save/workers', {'id': w['id'], 'version': w['version'], 'notes': '客服改備註',
                                                      'bank_name': '壞人銀行', 'bank_account': '9999-999-999999', 'bank_holder': '壞人', 'payout_cycle': 'week'},
                            csrf=st['csrf'])
        self.assertEqual(code, 200, r)                                  # 其他欄位照常能改
        con = sqlite3.connect(DEMO.data / 'operations.db')
        try:
            row = con.execute('SELECT notes,bank_name,bank_account,bank_holder,payout_cycle FROM workers WHERE id=?', ('wor-demo03',)).fetchone()
        finally:
            con.close()
        self.assertEqual(row[0], '客服改備註')
        self.assertEqual(list(row[1:]), [w_admin[k] for k in bank])     # 銀行欄位一個都沒變

    def test_worker_portal_needs_valid_token(self):
        op = DEMO.opener()
        self.assertEqual(DEMO.get(op, '/api/public/portal/demo-portal-worker-01')[0], 200)
        self.assertGreaterEqual(DEMO.get(op, '/api/public/portal/not-a-real-token')[0], 400)
        a = DEMO.get(op, '/api/public/portal/demo-portal-worker-01')[1].decode('utf-8')
        self.assertNotIn('陳美玉', a)                                 # 自己的入口也看不到別人

    def test_post_needs_csrf(self):
        op = DEMO.opener('admin')
        code, _ = DEMO.post(op, '/api/settings', {'key': 'x', 'value': 'y'})
        self.assertEqual(code, 403)

    def test_reapply_with_known_phone_cannot_impersonate(self):
        """陌生 LINE 帳號＋既有服務人員的電話重新應徵：綁定核准前不審資料、不掛照片。"""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(seed(d)[0], 0)
            r = subprocess.run([sys.executable, str(Path(__file__).with_name('_reapply_case.py')), d], env=env(d),
                               capture_output=True, timeout=120, creationflags=NO_WINDOW)
            out = json.loads(r.stdout.decode('utf-8').strip().splitlines()[-1])
        self.assertNotIn('rescreen', out['result'])
        self.assertEqual(out['screening_before_bind'], 0)
        self.assertIsNone(out['file_owner_before_bind'])
        self.assertIsNone(out['line_worker_before_bind'])
        self.assertTrue(out['bind_request'])                    # 改成請管理層確認是不是本人
        self.assertEqual(out['screening_after_bind'], 1)        # 管理層確認後才審資料
        self.assertEqual(out['file_owner_after_bind'], 'wor-demo02')


class BackupRestoreTest(unittest.TestCase):
    def test_backup_then_restore(self):
        op = DEMO.opener('admin')
        csrf = json.loads(DEMO.get(op, '/api/me')[1])['csrf']
        _, o = DEMO.get(op, '/api/public/options?b=demoshop')[:2]
        o = json.loads(o)
        f = {'account': 'demoshop', 'svc_items': [{'code': o['generic'][0]['code'], 'qty': 1}], 'date': '2030-02-01', 'slot': o['slots'][0],
             'area': '臺北市大安區', 'address': '臺北市大安區示範路 9 號', 'name': '備份測試', 'phone': '0900-000-998', 'website': '', 'consent': True}
        code, q = DEMO.post(DEMO.opener(), '/api/public/quote', f)
        self.assertEqual(code, 200, q)
        code, r = DEMO.post(op, '/api/backup', {}, csrf=csrf)
        self.assertEqual(code, 200, r)
        bk = DEMO.data / 'backups' / r['file']
        self.assertTrue(bk.exists())
        live, copy = sqlite3.connect(DEMO.data / 'operations.db'), sqlite3.connect(bk)
        try:
            self.assertEqual(copy.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            for t in ('customers', 'workers', 'orders', 'quotes', 'invoices', 'users'):
                self.assertEqual(copy.execute(f'SELECT COUNT(*) FROM {t}').fetchone(), live.execute(f'SELECT COUNT(*) FROM {t}').fetchone(), t)
        finally:
            live.close(); copy.close()
        d = Demo(restore_from=bk)                           # 真的拿備份起一個 server
        try:
            qs = json.loads(d.get(d.opener('admin'), '/api/quotes')[1])['quotes']
            self.assertTrue(any(x['token'] == q['token'] for x in qs))
        finally:
            d.stop()

    def test_backup_needs_csrf_and_admin(self):
        self.assertEqual(DEMO.post(DEMO.opener('admin'), '/api/backup', {})[0], 403)
        wk = DEMO.opener('worker')
        csrf = json.loads(DEMO.get(wk, '/api/state')[1])['csrf']
        self.assertEqual(DEMO.post(wk, '/api/backup', {}, csrf=csrf)[0], 403)


class SecretsTest(unittest.TestCase):
    def test_secret_reads_env_first_and_defaults_empty(self):
        code = r'''
import os, sqlite3, sys
sys.path.insert(0, sys.argv[1]); import integrations as ix
con = sqlite3.connect(':memory:'); con.execute('CREATE TABLE secrets(key TEXT PRIMARY KEY, value TEXT)')
a = ix.secret(con, 'line_token_demoshop')
os.environ[ix.env_secret_name('line_token_demoshop')] = 'from-env'
print(repr(a), ix.env_secret_name('line_token_demoshop'), ix.secret(con, 'line_token_demoshop'))
'''
        with tempfile.TemporaryDirectory() as d:
            e = env(d)
            e.pop('OPS_SECRET_LINE_TOKEN_DEMOSHOP', None)
            r = subprocess.run([sys.executable, '-c', code, str(APP)], env=e, capture_output=True, timeout=60, creationflags=NO_WINDOW)
        self.assertEqual(r.stdout.decode().split(), ['None', 'OPS_SECRET_LINE_TOKEN_DEMOSHOP', 'from-env'], r.stderr.decode('utf-8', 'replace'))

    def test_no_secrets_in_repo(self):
        import re
        root = APP.parent
        pat = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-ant-[A-Za-z0-9_-]{20,}|\bAIza[0-9A-Za-z_-]{30,}|\bU[0-9a-f]{32}\b')
        hits = []
        files = [p for d in ('app', 'docs', '.github') for p in (root / d).rglob('*')] + list(root.glob('*'))   # 只掃 repo 內容（不掃 .venv、data）
        for p in files:
            if p.is_file() and p.suffix in ('.py', '.js', '.html', '.md', '.sql', '.json', '.yml') and '__pycache__' not in p.parts:
                if pat.search(p.read_text('utf-8', 'ignore')):
                    hits.append(str(p))
        self.assertEqual(hits, [])


if __name__ == '__main__':
    unittest.main()
