"""店家營運中樞（示範）伺服器。只綁 127.0.0.1；外網經 Cloudflare Tunnel（HTTPS）進來。
多人帳號（管理員／客服排班／帳務／服務人員），後端逐路由檢查權限；LINE 沒有任何發送端點。"""
import contextlib
import hashlib
import hmac
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
import traceback
import urllib.parse
import core
import integrations as ix
import workflow
import photos
import accounts as acc
import board
import dispatch
import notify
import trash
import alerts
import line_bot
import booking
import pricing
import worker_portal
import automation
import ledger
import brands

PORT = int(os.environ.get('OPS_PORT', '4781'))
HOST = f'127.0.0.1:{PORT}'
ORIGIN = 'http://' + HOST
PUBLIC_HOSTS = {h.strip().lower() for h in os.environ.get('OPS_PUBLIC_HOSTS', 'ops.example.com').split(',') if h.strip()}
SITE_HOSTS = {h.strip().lower() for h in os.environ.get('OPS_SITE_HOSTS', 'www.example.com').split(',') if h.strip()}     # 官網網址：首頁＝官網，只開公開頁
SITE_PATHS = ('/home', '/mall', '/book', '/chg', '/wguide', '/go/book-', '/go/mk-', '/q/', '/store', '/privacy', '/hi', '/apply', '/site', '/pf', '/api/public/', '/p/', '/pg/', '/pay/', '/linepay/', '/si/', '/u/', '/health')
ix.BASE = ORIGIN
LOCK = threading.RLock()
ledger.LOCK = LOCK                 # 記帳：背景讀憑證跟全系統用同一把鎖（鎖外打 Claude，鎖裡寫結果）
__import__('sitegeo').LOCK = LOCK
__import__('aibrief').LOCK = LOCK  # AI 整理客戶對話：鎖外打 Claude，鎖裡寫結果  # 案場精準定位：鎖外查國土測繪，鎖裡寫座標
COOKIE = 'demo_session'
STATIC = re.compile(r'^/([a-z0-9-]+\.(?:js|css|png|jpg|gif|svg|webmanifest))$')      # 加 gif：開發信主視覺動畫
MONEY_TABLES = ('invoices', 'payments', 'invoice_lines', 'settlements', 'receipts', 'credits', 'receipt_refunds', 'audit')

WORKER_BANK = ('bank_name', 'bank_account', 'bank_holder', 'payout_cycle')      # 服務人員的金流欄位：只有 money 權限看得到、改得到

def backup():
    target = core.DATA / 'backups' / ('operations-' + core.now().replace(':', '-') + '-' + secrets.token_hex(3) + '.db')
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        with contextlib.closing(core.connect()) as source, contextlib.closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
    return target.name

def need(user, cap):
    if cap not in acc.CAN.get(user['role'], set()):
        raise PermissionError('你的帳號沒有這個權限')

def order_pay(con):
    import datetime as dt
    """{工單: {mode: before|after, paid, billed}}（近 60 天以後的工單）：有服務前收款單＝先結；沒有收款單看客戶資料的結帳方式。"""
    import payments
    payments.ensure(con)
    since = (dt.datetime.now(core.TZ).date() - dt.timedelta(days=60)).isoformat()
    rows = {}
    for r in con.execute("SELECT p.order_id,p.kind,p.status FROM payment_requests p JOIN orders o ON o.id=p.order_id WHERE o.date>=? AND p.status!='cancelled'", (since,)):
        rows.setdefault(r[0], []).append((r[1], r[2]))
    out = {}
    for oid, cpm in con.execute("SELECT o.id,COALESCE(cu.pay_method,'') FROM orders o LEFT JOIN customers cu ON cu.id=o.customer_id WHERE o.date>=? AND o.status NOT IN ('cancelled','archived')", (since,)):
        ps = rows.get(oid, [])
        pre = [st for k, st in ps if k in ('deposit', 'full', 'month')]
        if pre:
            out[oid] = {'mode': 'before', 'paid': all(st == 'confirmed' for st in pre)}
        elif ps:
            out[oid] = {'mode': 'after', 'billed': True}
        else:
            out[oid] = {'mode': 'before' if cpm in ('全額預付', '訂金尾款') else 'after'}
    return out

def crew_seen(con):
    """（客服：派工不確定夥伴有沒有收到、有沒有在看）：還沒結束的工單，每個被邀請／已接的人 →
    {assignment_id: [這場派工 LINE 的狀態, 送出時間, 他最後一次在 LINE 有動作的時間, 有沒有綁 LINE]}。"""
    import datetime as dt, core as c
    out = {}
    today = dt.datetime.now(c.TZ).date().isoformat()
    for a in c.rows(con, "SELECT a.id,a.order_id,a.worker_id FROM assignments a JOIN orders o ON o.id=a.order_id WHERE o.date>=? AND a.status IN ('invited','applied','accepted')", (today,)):
        us = [r[0] for r in con.execute('SELECT user_id FROM line_users WHERE worker_id=?', (a['worker_id'],))]
        if not us:
            out[a['id']] = ['', '', '', 0]; continue
        m = con.execute(f"SELECT status,COALESCE(sent_at,created_at) FROM line_outbox WHERE order_id=? AND to_id IN ({','.join('?' * len(us))}) ORDER BY id DESC LIMIT 1", (a['order_id'], *us)).fetchone()
        seen = con.execute(f"SELECT MAX(last_seen) FROM line_users WHERE user_id IN ({','.join('?' * len(us))})", us).fetchone()[0] or ''
        out[a['id']] = [m[0] if m else '', (m[1] or '') if m else '', seen, 1]
    return out

def more_counts(con, money=False):
    """「更多」每個入口裡面有東西要處理 → 紅色數字。每項各自 try，一項壞掉不影響其他。"""
    out = {}
    def n(k, f):
        try:
            v = f()
            if v:
                out[k] = int(v)
        except Exception:
            pass
    q1 = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    n('sysreport', lambda: q1("SELECT COUNT(*) FROM sys_reports WHERE status IN ('open','need_decision')"))
    n('pairing', lambda: len(__import__('pairing').todo(con)))
    n('testimonials', lambda: len(__import__('testimonials').pending(con)))
    n('interviews', lambda: q1("SELECT COUNT(*) FROM interviews WHERE status='proposed' OR (status='scheduled' AND slot!='' AND slot<?)", core.now()))
    n('shop', lambda: q1("SELECT COUNT(*) FROM shop_orders WHERE status='new'"))
    n('marketing', lambda: q1("SELECT COUNT(*) FROM staff_reviews WHERE status='pending' AND payload LIKE '%\"mk_%'"))
    if money:
        n('ledger', lambda: len(__import__('ledger').pending(con)))
        n('receipts', lambda: q1("SELECT COUNT(*) FROM payment_requests WHERE status='reported'"))
    return out

def state_for(con, user):
    if user['role'] == 'cleaner':
        return board.worker_state(con, user)
    data = {**core.snapshot(con), 'integrations': ix.status(con)}
    for w in data.get('workers', []): w.pop('portal_token', None)
    data['admin_group_fail'] = ix.setting(con, 'admin_group_fail')          # 派單管理群收不到系統訊息 → 首頁紅條
    lns = {}                                   # 客戶名字旁邊要有 LINE 名稱（綁定的 LINE 顯示名稱＋客戶資料的 LINE 名稱）
    for cid, dn in con.execute("SELECT customer_id,display_name FROM line_users WHERE customer_id IS NOT NULL AND COALESCE(display_name,'')!=''"):
        lns.setdefault(cid, []).append(dn)
    bd = {}                                    # 哪個客戶已經綁定 LINE，列表上一眼看得出來（稽核 B1：同品牌的優先，綁在別品牌要看得出來）
    for cid, acc_, dn, fr, uid_ in con.execute("SELECT customer_id,account,display_name,is_friend,user_id FROM line_users WHERE customer_id IS NOT NULL ORDER BY is_friend DESC, last_seen DESC"):
        bd.setdefault(cid, []).append({'acc': acc_, 'name': dn or '', 'friend': fr or 0, 'uid': uid_})      # uid：品牌不一致時「解除這個 LINE」用
    for cu in data.get('customers', []):
        names = [x for x in [cu.get('line_name') or ''] + lns.get(cu['id'], []) if x and x != cu['name']]
        cu['ln'] = '、'.join(dict.fromkeys(names))
        bs_ = bd.get(cu['id']) or []; b_ = brands.of_customer(cu)
        cu['bound'] = next((x for x in bs_ if x['acc'] == b_ and x['friend']), None) or next((x for x in bs_ if x['acc'] == b_), None) or (bs_[0] if bs_ else None)
    for a in data.get('assignments', []): a.pop('work_token', None)
    if 'money' not in acc.CAN[user['role']]:
        for t in MONEY_TABLES:
            data[t] = []
        for w in data['workers']:
            w['rate'] = None
            for k in WORKER_BANK:                  # 資安：銀行帳戶與領薪週期也是金流資料，沒有 money 權限看不到
                w[k] = None
        for o in data.get('orders', []):
            for k in ('rate', 'approved_amount', 'expense', 'billable_hours', 'hist_income', 'hist_expense', 'hist_salary', 'adjustment', 'original_json'):
                if k in o: o[k] = None
        for t, keys in (('sites', ('rate', 'pricing_note', 'worker_rate')), ('plans', ('price', 'amount')), ('customers', ('billing_rule',))):
            for r in data.get(t, []):
                for k in keys:
                    if k in r: r[k] = None
        for a in data.get('assignments', []):
            a['rate'] = None; a['amount'] = None
            if 'reimburse' in a: a['reimburse'] = None        # 代墊金額也是錢
        for s in data.get('sites', []):
            s['visit_price'] = None
        for i in data.get('inquiries', []):        # 需求明細裡的初估、估價費
            if i.get('detail'): i['detail'] = booking.strip_money_text(i['detail'])
    if user['role'] == 'staff':          # 資安：客服只看最近有往來的客戶；開門說明只給 7 天內已排定的案場
        import datetime as _dt
        d0 = _dt.datetime.now(core.TZ).date()
        since, soon, today_ = (d0 - _dt.timedelta(days=30)).isoformat(), (d0 + _dt.timedelta(days=7)).isoformat(), d0.isoformat()
        keep = {o.get('customer_id') for o in data.get('orders', []) if (o.get('date') or '') >= since}
        keep |= {i.get('customer_id') for i in data.get('inquiries', []) if i.get('status') in ('new', 'contacted', 'quoted')}
        keep |= {cu['id'] for cu in data.get('customers', []) if (cu.get('created_at') or '') >= since}
        for cu in data.get('customers', []):          # 舊客戶照樣查得到名字（客戶來電要找），但電話只露末 3 碼、備註不給，避免整批個資被帶走
            if cu['id'] not in keep:
                ph = ''.join(ch for ch in (cu.get('phone') or '') if ch.isdigit())
                cu['phone'] = ('＊' * max(0, len(ph) - 3) + ph[-3:]) if ph else ''
                cu['notes'] = ''
        data['contacts'] = [x for x in data.get('contacts', []) if x.get('customer_id') in keep]
        live_sites = {o.get('site_id') for o in data.get('orders', []) if o.get('status') == 'confirmed' and today_ <= (o.get('date') or '') <= soon}
        for s in data.get('sites', []):
            if s.get('id') not in live_sites:
                s['entry_notes'] = ''
            if s.get('customer_id') not in keep:
                s['notes'] = ''
        for i in data.get('inquiries', []):
            i.pop('ip', None)
            if i.get('status') not in ('new', 'contacted', 'quoted') and i.get('customer_id') not in keep:
                ph = ''.join(ch for ch in (i.get('phone') or '') if ch.isdigit())
                i['phone'] = ('＊' * max(0, len(ph) - 3) + ph[-3:]) if ph else ''
                i['address'] = ''
    if user['role'] == 'admin':
        data['users'] = acc.list_users(con)
        data['alert_log'] = core.rows(con, 'SELECT * FROM alert_log ORDER BY id DESC LIMIT 30')
        data['settings'] = {'bank_info': ix.setting(con, 'bank_info'), 'calendar_feed_last_pull': ix.setting(con, 'calendar_feed_last_pull'),
                            'calendar_feed_ready': bool(ix.setting(con, 'calendar_feed_hash'))}
    data['dispatch_log'] = core.rows(con, 'SELECT * FROM dispatch_log ORDER BY id DESC LIMIT 300')
    data['triage_open'] = con.execute("SELECT COUNT(*) FROM triage WHERE status='open'").fetchone()[0]
    import live; data['today_live'] = live.day(con)                                 # 今天服務・即時狀況（人員、排定、開門、到場、照片、離場、驗收）
    import testimonials; data['testimonials'] = testimonials.state(con)          # 客戶見證（滿意客戶、服務前後照片）
    import sysreport; data['sys_reports'] = sysreport.listing(con, limit=200)                # 功能回報
    data['sys_report_files'] = sysreport.files_map(con)                                  # 回報附的截圖
    import interviews; data['interviews'] = interviews.state(con)                 # 夥伴面試
    data['iv_slots'] = [t.isoformat()[:16] for t in interviews.slots(con)]       # 面試時間由我們選 → 建議時段
    import shop; data.update(shop.state(con))                                      # 示範商城
    data['line_bound'] = {'customers': [r[0] for r in con.execute('SELECT DISTINCT customer_id FROM line_users WHERE customer_id IS NOT NULL AND is_friend=1')],
                          'workers': [r[0] for r in con.execute('SELECT DISTINCT worker_id FROM line_users WHERE worker_id IS NOT NULL AND is_friend=1')]}
    lu_rows = con.execute("SELECT worker_id,display_name,user_id,account FROM line_users WHERE worker_id IS NOT NULL AND is_friend=1 ORDER BY last_seen DESC").fetchall()
    bots = {a: ix.setting(con, f'line_bot_uid_{a}') for a in {r[3] for r in lu_rows}}
    data['worker_line'] = {}              # 夥伴的 LINE 名稱＋官方帳號後台對話連結（chat.line.biz）
    for wid_, dn, uid_, acc_ in lu_rows:
        if wid_ not in data['worker_line']:
            tpl_ = ix.setting(con, 'line_chat_url_tpl')      # 管理員按也錯 → 舊格式不對；拿到她貼的正確網址格式後才組直達連結
            data['worker_line'][wid_] = {'name': dn or '', 'acc': acc_, 'brand': __import__('brands').name(acc_),
                                         'chat': tpl_.replace('{bot}', bots.get(acc_) or '').replace('{user}', uid_) if tpl_ and bots.get(acc_) else 'https://chat.line.biz/'}
    data['holds'] = {r[0][14:]: r[1] for r in con.execute("SELECT key,value FROM settings WHERE key LIKE 'dispatch_hold:%' AND COALESCE(value,'')!=''")}      # 擋派工／找人中
    data['worker_pf'] = {}                # 應徵時放的作品集照片、好評截圖、作品集連結，錄取後留在夥伴資料（良民證不在這裡）
    try:
        for wid_, fid_, k_ in con.execute("SELECT worker_id,id,kind FROM worker_files WHERE worker_id IS NOT NULL AND kind IN ('portfolio','feedback') ORDER BY created_at"):
            data['worker_pf'].setdefault(wid_, {'files': [], 'link': '', 'text': ''})['files'].append([fid_, k_])
        for wid_, app_ in con.execute("SELECT worker_id,application FROM interviews WHERE COALESCE(application,'')!='' ORDER BY created_at"):
            try:
                ap_ = json.loads(app_ or '{}')
            except ValueError:
                continue
            lk_ = str(ap_.get('portfolio_link') or '').strip(); tx_ = str(ap_.get('portfolio_text') or '').strip()
            if lk_.startswith(('http://', 'https://')) or tx_:
                e_ = data['worker_pf'].setdefault(wid_, {'files': [], 'link': '', 'text': ''})
                e_['link'] = lk_[:300] if lk_.startswith(('http://', 'https://')) else e_['link']; e_['text'] = tx_[:300] or e_['text']
    except Exception:
        pass
    import decisions; data['asks'] = decisions.pending(con)      # 群組題目卡（中控台「管理層要決定」）
    import staffq; data['staffq'] = staffq.items(con, 'money' in acc.CAN[user['role']])      # 管理群卡片在中控台處理
    data['nophoto_jobs'] = [k[12:] for (k,) in con.execute("SELECT key FROM settings WHERE key LIKE 'nophoto_job:%'")]      # 稽核 F2：夥伴回報「這裡不能拍照」的工單
    data['worker_bind_sent'] = {}         # 夥伴綁定訊息誰傳過（中控台「LINE 還沒綁」）
    for k_, v_ in con.execute("SELECT key,value FROM settings WHERE key LIKE 'wbind_sent:%'"):
        try:
            data['worker_bind_sent'][k_[11:]] = json.loads(v_)
        except ValueError:
            pass
    data['finish_sent'] = [r[0] for r in con.execute("SELECT DISTINCT order_id FROM line_outbox WHERE kind='finish_c' AND status NOT LIKE 'skipped%' AND created_at>=date('now','-60 day')")]
    data['brands'] = [{'id': b['id'], 'name': b['name'], 'channel': b['channel']} for b in brands.all_(con)]
    if 'money' in acc.CAN[user['role']]:          # 配合廠商外包成本／應付：只給看得到金額的人
        import vendors; data.update(vendors.state(con))
        import linepay; linepay.ensure(con)
        data['linepay'] = linepay.status(con)
        data['linepay_tx'] = core.rows(con, 'SELECT order_key,pid,amount,status,created_at,confirmed_at,mode FROM linepay_tx ORDER BY created_at DESC LIMIT 50')
    if 'money' in acc.CAN[user['role']]:          # 收款單只給看得到金額的人
        import payments; payments.ensure(con)
        data['payment_requests'] = core.rows(con, "SELECT p.*, cu.name AS customer, q.token FROM payment_requests p LEFT JOIN customers cu ON cu.id=p.customer_id "
                                                  "LEFT JOIN quotes q ON q.id=p.quote_id WHERE p.status IN ('pending','reported') ORDER BY p.due")
    data['bind_requests'] = core.rows(con, "SELECT r.*, COALESCE(cu.name, w.name, '') AS target FROM bind_requests r LEFT JOIN customers cu ON r.kind='customer' AND cu.id=r.ref_id "
                                      "LEFT JOIN workers w ON r.kind='worker' AND w.id=r.ref_id WHERE r.status='open' ORDER BY r.created_at")
    if 'ops' in acc.CAN[user['role']]:          # 配對不推播 → 首頁提示還有幾個聊天室要確認
        import pairing
        data['pairing_todo'] = len(pairing.todo(con))
    data['quotes_pending'] = con.execute("SELECT COUNT(*) FROM quotes WHERE status='draft' AND stage IN ('review','site_visit')").fetchone()[0]
    if 'ops' in acc.CAN[user['role']]:          # 工單進度條依客戶結帳方式（先結：收款在排定前；後結：完工後請款）
        data['order_pay'] = order_pay(con)
    if 'ops' in acc.CAN[user['role']]:          # 案場須知照片
        import siteinfo; siteinfo.ensure(con)
        sp_ = {}
        for sid_, pid_, cap_ in con.execute('SELECT site_id,id,caption FROM site_photos ORDER BY created_at'):
            sp_.setdefault(sid_, []).append([pid_, cap_])
        data['site_photos'] = sp_
        data['crew_seen'] = crew_seen(con)                  # 派工到底有沒有送到、夥伴有沒有在看
        import siteroutine; siteroutine.ensure(con)          # 客戶01：定期特殊服務
        rt_ = {}
        for r_ in core.rows(con, 'SELECT id,site_id,every,title,tasks,last_done FROM site_routines WHERE active=1 ORDER BY every,created_at'):
            rt_.setdefault(r_['site_id'], []).append(r_)
        data['site_routines'] = rt_
        siteinfo.ensure_media(con)
        om_ = {}
        for oid_, mid_, mm_ in con.execute('SELECT order_id,id,mime FROM order_media ORDER BY created_at'):
            om_.setdefault(oid_, []).append([mid_, mm_])
        data['order_media'] = om_
    if 'ops' in acc.CAN[user['role']]:          # 「更多」裡每個有東西要處理的入口都要紅色數字
        data['more_n'] = more_counts(con, 'money' in acc.CAN[user['role']])
    data['worker_blacklist'] = automation.blacklist(con)
    if 'ops' in acc.CAN[user['role']]:          # 派工分級 A／B／C（只有管理層看得到；沒排的算 B）
        import tiers; data['worker_tiers'] = tiers.all_tiers(con)
        data['customer_tiers'] = tiers.all_ctiers(con); data['tier_rules'] = tiers.rules_on(con); data['tier_route'] = ix.setting(con, 'tier_route') == '1'      # 客戶 A／B／C、D 級
    data['role'] = user['role']
    return data

class Handler(BaseHTTPRequestHandler):
    server_version = 'HesonOps'

    def log_message(self, *args):
        pass   # 網址可能含一次性登入碼或日曆金鑰，不寫 log

    # ── 來源與身分 ──
    def host(self):
        h = (self.headers.get('Host') or '').lower()
        if h == HOST or h == f'localhost:{PORT}':
            return 'local'
        if h in PUBLIC_HOSTS:
            return 'public'
        if h in SITE_HOSTS:
            return 'site'
        raise PermissionError('請使用店家營運中樞（示範）的正式網址')

    def origin_ok(self):
        h = (self.headers.get('Host') or '').lower()
        scheme = 'https://' if h in PUBLIC_HOSTS or h in SITE_HOSTS else 'http://'
        return self.headers.get('Origin') == scheme + h

    def ip(self):
        if self.host() in ('public', 'site'):
            return (self.headers.get('Cf-Connecting-Ip') or self.client_address[0])[:64]
        return self.client_address[0]

    def token(self):
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            return None
        m = cookie.get(COOKIE)
        return m.value if m else None

    def user(self, con):
        t = self.token()
        u = acc.session_user(con, t) if t else None
        if not u:
            raise PermissionError('請先登入')
        core.CTX.actor = u['name']
        return u

    def cookie_header(self, token, life):
        secure = '; Secure' if self.host() in ('public', 'site') else ''
        return f'{COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={life}{secure}'

    # ── 回應 ──
    def respond(self, obj, status=200, headers=None):
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_bytes(raw, 'application/json; charset=utf-8', status, headers)

    def send_bytes(self, raw, content_type, status=200, headers=None, cache='no-store'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(self), geolocation=(self), microphone=()')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        try:
            if self.host() in ('public', 'site'):
                self.send_header('Strict-Transport-Security', 'max-age=31536000')
        except PermissionError:
            pass
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        try:
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass                     # 每日回報 瀏覽器已經關掉／重啟中斷線，不是程式錯誤（以前每次重啟都記一筆假警報）

    def fail(self, e):
        if isinstance(e, PermissionError):
            msg = str(e)
            return self.respond({'error': msg, 'login': msg == '請先登入'}, 401 if msg == '請先登入' else 403)
        if isinstance(e, (ValueError, KeyError, sqlite3.IntegrityError)):
            msg = '資料已被使用或欄位關聯不符，請重新整理核對' if isinstance(e, sqlite3.IntegrityError) else (str(e) if not isinstance(e, KeyError) else f'缺少欄位 {e}')
            return self.respond({'error': msg}, 400)
        traceback.print_exc()
        report_error('網址 ' + urllib.parse.urlsplit(self.path).path, e)          # 功能回報：自動記下，每天統一修
        return self.respond({'error': '處理失敗，本次變更已回復；請稍後重試（已自動回報）'}, 500)

    # ── 官網預約（跨網域，雲端搬本機）──
    def _web_cors(self):
        """None＝沒有 Origin（本機程式）；False＝來源不在白名單；dict＝要回的 CORS 標頭。"""
        o = (self.headers.get('Origin') or '').rstrip('/')
        if not o:
            return None
        import webbooking
        with contextlib.closing(core.connect()) as con0:
            ok = o in webbooking.origins(con0)
        return {'Access-Control-Allow-Origin': o, 'Vary': 'Origin', 'Access-Control-Allow-Methods': 'POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '600'} if ok else False

    def do_OPTIONS(self):
        try:
            if urllib.parse.urlsplit(self.path).path != '/api/public/web-booking':
                return self.respond({'error': '找不到頁面'}, 404)
            self.host()
            h = self._web_cors()
            if not h:
                raise PermissionError('來源不符')
            return self.send_bytes(b'', 'text/plain; charset=utf-8', 204, h)
        except Exception as e:
            return self.fail(e)

    def web_booking(self):
        try:
            h = self._web_cors()
            if h is False or (h is None and self.host() != 'local'):
                raise PermissionError('來源不符')
            if self.headers.get('Content-Type', '').split(';')[0] not in ('application/json', 'text/plain'):
                raise ValueError('請使用 JSON 格式')
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 64 * 1024:
                raise ValueError('請求大小不符')
            p = json.loads(self.rfile.read(length))
            if not isinstance(p, dict):
                raise ValueError('資料格式不符')
            import webbooking
            with LOCK, contextlib.closing(core.connect()) as con, con:
                r = webbooking.ingest(con, p, self.ip())
            return self.respond(r, 200, h or None)
        except Exception as e:
            return self.fail(e)

    # ── GET ──
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        path = parsed.path
        try:
            if path in ('/robots.txt', '/sitemap.xml', '/llms.txt', '/', '/home', '/store', '/mall', '/book') or re.match(r'^/[0-9a-f]{32}\.txt$', path):        # SEO 除錯：記一下誰來抓（只記公開網址）；加官網其他頁
                try:
                    with open(core.ROOT / 'logs' / 'seo_access.log', 'a', encoding='utf-8') as f_:
                        f_.write(f"{core.now()} {path} host={self.headers.get('Host')} ip={self.headers.get('Cf-Connecting-Ip') or self.client_address[0]} ua={(self.headers.get('User-Agent') or '')[:120]}\n")
                except OSError:
                    pass
            where = self.host()
            if path.startswith('/u/'):                 # 開發信退訂連結：打開只顯示「確認取消訂閱」按鈕（公司／機關的郵件安全掃描會自動點開連結，不能一點就退）
                import mailunsub
                try:
                    with contextlib.closing(core.connect()) as con_, LOCK:
                        e_ = mailunsub.parse(con_, path[3:])
                    return self.send_bytes(mailunsub.confirm_page(e_, path[3:]).encode('utf-8'), 'text/html; charset=utf-8')
                except PermissionError:
                    return self.send_bytes('<!doctype html><meta charset="utf-8"><p>連結無效，請直接回覆信件「退訂」。</p>'.encode('utf-8'), 'text/html; charset=utf-8', status=400)
            mk_ = re.match(r'^/([0-9a-f]{32})\.txt$', path)
            if mk_ and self.host() == 'site':              # IndexNow 驗證檔
                import seo as _seo
                with contextlib.closing(core.connect()) as con_, LOCK:
                    if mk_.group(1) == _seo.indexnow_key(con_):
                        return self.send_bytes(mk_.group(1).encode(), 'text/plain; charset=utf-8')
            if path in ('/robots.txt', '/sitemap.xml', '/llms.txt'):      # SEO／AEO：官網網址開放；營運網址整個不收錄
                import seo
                if path == '/robots.txt':
                    return self.send_bytes(seo.robots(where).encode('utf-8'), 'text/plain; charset=utf-8', cache='public, max-age=3600')
                if where != 'site':
                    raise PermissionError('請到官網網址')
                if path == '/sitemap.xml':
                    return self.send_bytes(seo.sitemap().encode('utf-8'), 'application/xml; charset=utf-8', cache='public, max-age=3600')
                with contextlib.closing(core.connect()) as con_, LOCK:
                    return self.send_bytes(seo.llms(con_).encode('utf-8'), 'text/plain; charset=utf-8', cache='public, max-age=3600')
            if path in ('/', '/index.html', '/home') and (where == 'site' or path == '/home'):      # SEO：官網伺服器端先畫好（三個品牌各自標題、結構化資料）
                import seo
                with contextlib.closing(core.connect()) as con_, LOCK:
                    page_ = seo.render_home(con_, q.get('b', 'demoshop') if path == '/home' else 'demoshop', where)
                return self.send_bytes(page_.encode('utf-8'), 'text/html; charset=utf-8')
            if where == 'site':                        # 官網網址：首頁＝官網；中控台、後台 API 不開放（請走 demoshop-ops 網址）
                if path in ('/', '/index.html'):
                    return self.send_bytes((core.ROOT / 'web' / 'home.html').read_bytes(), 'text/html; charset=utf-8')
                if not (path.startswith(SITE_PATHS) or (STATIC.match(path) and (core.ROOT / 'web' / STATIC.match(path).group(1)).is_file())):
                    self.send_response(302); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers(); return
            if path == '/health':
                return self.respond({'status': 'ok', 'service': 'demoshop-ops', 'line': 'auto'})
            if path in ('/', '/index.html'):
                return self.send_bytes((core.ROOT / 'web' / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            m = STATIC.match(path)
            if m and (core.ROOT / 'web' / m.group(1)).is_file():
                f = core.ROOT / 'web' / m.group(1)
                ctype = {'.js': 'application/javascript', '.css': 'text/css', '.webmanifest': 'application/manifest+json'}.get(f.suffix) or mimetypes.guess_type(f.name)[0]
                extra = {'Access-Control-Allow-Origin': 'https://manager.line.biz'} if f.name.startswith('richmenu-') else None   # 圖文選單圖片給 LINE 後台讀
                return self.send_bytes(f.read_bytes(), ctype + ('; charset=utf-8' if f.suffix in ('.js', '.css') else ''), headers=extra, cache='no-cache')
            if path.startswith('/go/'):                # 選單網址按鈕點一次記一次（選單點擊統計），再轉到真正的頁面
                import richmenu2
                to = richmenu2.go_target(path[4:])
                if to:
                    try:
                        with contextlib.closing(core.connect()) as con, con:
                            richmenu2.go_log(con, path[4:])
                    except Exception:
                        traceback.print_exc()          # 稽核：記點擊失敗（資料庫忙）也照常轉址，行銷連結不會 500
                    try:                                     # 行銷連結導進官方 LINE（手機直接開 LINE、電腦給 QR）
                        import mklink
                        with contextlib.closing(core.connect()) as con:
                            mk = mklink.target(con, path[4:], self.headers.get('User-Agent', ''))
                        if mk and mk[0] == 'html':
                            return self.send_bytes(mk[1], 'text/html; charset=utf-8')
                        to = mk[1] if mk else to
                    except Exception:
                        traceback.print_exc()                # 出錯就照舊開網頁線上估價
                    self.send_response(302); self.send_header('Location', to); self.send_header('Content-Length', '0'); self.end_headers(); return
            mm = re.match(r'^/matimg/([A-Za-z0-9_-]{20,40})\.jpg$', path)
            if mm:                                     # 素材照片（夥伴拍照標準公告的範例；隨機代碼）
                import materials as _mt
                with contextlib.closing(core.connect()) as con:
                    raw, mime = _mt.image_bytes(con, mm.group(1), preview=q.get('p') == '1')
                if not raw:
                    return self.respond({'error': '找不到'}, 404)
                return self.send_bytes(raw, mime, cache='private, max-age=3600')
            mi = re.match(r'^/mkimg/([A-Za-z0-9_-]{20,40})\.(?:png|jpg)$', path)
            if mi:                                     # 行銷送審圖片（隨機代碼；LINE 圖片訊息要公開網址）
                import mkbridge
                with contextlib.closing(core.connect()) as con:
                    raw, mime = mkbridge.image_bytes(con, mi.group(1), preview=q.get('p') == '1')
                if not raw:
                    return self.respond({'error': '找不到'}, 404)
                return self.send_bytes(raw, mime, cache='private, max-age=3600')
            with contextlib.closing(core.connect()) as con:
                if path == '/login':
                    if where != 'local':
                        raise PermissionError('一次性登入連結只能在本機使用')
                    token = q.get('key', '')
                    with LOCK, con:
                        key = ix.setting(con, 'launch_key_hash'); exp = int(ix.setting(con, 'launch_key_expires', '0'))
                        if not key or time.time() > exp or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), key):
                            raise PermissionError('登入連結已使用或已過期，請重新執行啟動器')
                        ix.set_setting(con, 'launch_key_hash', '')
                        acc.ensure_local(con)
                        session, life = acc.new_session(con, core.get(con, 'users', acc.LOCAL_ID), self.ip())
                    return self.send_bytes(b'', 'text/plain', 302, {'Location': '/', 'Set-Cookie': self.cookie_header(session, life)})
                if path == '/login/line':               # 一次性登入連結（LINE 一對一傳給本人；30 分鐘、只能用一次；進去先設定自己的密碼）
                    with LOCK, con:
                        u = acc.use_magic(con, q.get('t', ''))
                        session, life = acc.new_session(con, u, self.ip())
                        core.audit(con, 'magic_login', 'users', u['id'], after={'ip': self.ip()}, actor=u['name'])
                    return self.send_bytes(b'', 'text/plain', 302, {'Location': '/', 'Set-Cookie': self.cookie_header(session, life)})
                if path == '/pay/shop':                 # 商城訂單：LINE Pay（正式開通後）
                    import shop, linepay
                    try:
                        with LOCK, con:
                            oid = q.get('i', '')
                            if not hmac.compare_digest(linepay.sign(con, 'shop', oid), q.get('s', '')):
                                raise PermissionError('連結無效')
                            shop.ensure(con)
                            o0 = con.execute('SELECT * FROM shop_orders WHERE id=?', (oid,)).fetchone()
                            if not o0 or o0['status'] == 'cancelled':
                                raise ValueError('找不到這筆訂單或已取消')
                            if o0['paid_at']:
                                raise ValueError('這筆已經付過了，謝謝您！')
                            r0 = linepay.request_fee(con, 'shop:' + oid, o0['total'], f"示範商城訂單 {oid}", line_bot.BASE_URL)
                        if r0.get('url'):
                            return self.send_bytes(b'', 'text/plain', 302, {'Location': r0['url']})
                        msg0 = 'LINE Pay 暫時無法使用，請取貨時再付。'
                    except (ValueError, PermissionError) as e0:
                        msg0 = str(e0)
                    html0 = ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                             '<title>示範服務店</title><link rel="stylesheet" href="/style.css"></head><body><main class="main" style="max-width:520px"><div class="card"><h2 style="margin-top:0">'
                             + html_escape(msg0) + '</h2></div></main></body></html>')
                    return self.send_bytes(html0.encode('utf-8'), 'text/html; charset=utf-8')
                if path in ('/pay/demo', '/pay/demo/go'):     # LINE Pay 合作商店審核：固定的示範付款網址（測試環境，不會真的扣款）
                    import linepay
                    if path == '/pay/demo':
                        with LOCK, con:
                            return self.send_bytes(linepay.demo_page(con).encode('utf-8'), 'text/html; charset=utf-8')
                    try:
                        with LOCK, con:
                            to = linepay.public_demo(con)
                        return self.send_bytes(b'', 'text/plain', 302, {'Location': to})
                    except PermissionError as e0:
                        html0 = ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                                 '<title>示範服務店</title><link rel="stylesheet" href="/style.css"></head><body><main class="main" style="max-width:520px"><div class="card"><h2 style="margin-top:0">'
                                 + html_escape(str(e0)) + '</h2></div></main></body></html>')
                        return self.send_bytes(html0.encode('utf-8'), 'text/html; charset=utf-8')
                if path in ('/pay/police', '/pay/done'):     # 良民證代辦規費：LINE Pay（正式開通後）
                    msg = '付款完成，謝謝你！可以關掉這個頁面回到 LINE 🙏' if not q.get('c') else '已取消付款，可以回到 LINE 改用其他方式。'
                    if path == '/pay/police':
                        import recruit, linepay, interviews
                        try:
                            with LOCK, con:
                                iid = q.get('i', '')
                                if not hmac.compare_digest(recruit.sig(con, iid), q.get('s', '')):
                                    raise PermissionError('連結無效')
                                w0 = core.get(con, 'workers', interviews.get(con, iid)['worker_id'])
                                r0 = linepay.request_fee(con, 'police:' + iid, recruit.fee(con), f"良民證代辦規費（{w0['name']}）", line_bot.BASE_URL)
                            if r0.get('url'):
                                return self.send_bytes(b'', 'text/plain', 302, {'Location': r0['url']})
                            msg = 'LINE Pay 暫時無法使用，請回到 LINE 改用其他方式付款。'
                        except (ValueError, PermissionError) as e0:
                            msg = str(e0)
                    html = ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                            '<title>示範服務店</title><link rel="stylesheet" href="/style.css"></head><body><main class="main" style="max-width:520px"><div class="card"><h2 style="margin-top:0">'
                            + html_escape(msg) + '</h2></div></main></body></html>')
                    return self.send_bytes(html.encode('utf-8'), 'text/html; charset=utf-8')
                if path == '/linepay/confirm':          # LINE Pay 付款完導回來 → 確認扣款 → 回報價單頁
                    import linepay
                    with LOCK, con:
                        try:
                            loc = linepay.confirm(con, q.get('k', ''), q.get('transactionId', ''))
                        except (ValueError, PermissionError):
                            t = con.execute('SELECT token FROM linepay_tx WHERE order_key=?', (q.get('k', ''),)).fetchone() if con.execute("SELECT name FROM sqlite_master WHERE name='linepay_tx'").fetchone() else None
                            loc = f"/q/{t[0]}#payfail" if t else '/'
                    return self.send_bytes(b'', 'text/plain', 302, {'Location': loc})
                mqp = re.match(r'^/q/([A-Za-z0-9_-]{10,40})\.pdf$', path)
                if mqp:                    # 客戶要 PDF／紙本／Email → 報價單 A4 PDF（跟中控台下載的同一份；傳出後才開放）
                    import importlib.util, tempfile, pathlib, hashlib as _hl      # 別名：do_GET 前面還有用到 hashlib（直接 import 會變成區域變數）
                    spec = importlib.util.spec_from_file_location('quote_print', str(pathlib.Path(__file__).resolve().parent / 'tools' / 'quote_print.py'))
                    qp = importlib.util.module_from_spec(spec); spec.loader.exec_module(qp)
                    with LOCK, con:
                        qq = con.execute('SELECT id,stage,status FROM quotes WHERE token=?', (mqp.group(1),)).fetchone()
                        if not qq or qq['status'] == 'cancelled' or (qq['stage'] not in ('sent', 'signed') and qq['status'] != 'signed'):
                            raise PermissionError('這張報價單還沒開放下載')
                        h, d = qp.html(con, qq['id'])
                    out = pathlib.Path(tempfile.gettempdir()) / f"qpub_{qq['id']}_{_hl.sha1(h.encode()).hexdigest()[:10]}.pdf"
                    if not out.exists():             # 內容沒變就用上次產生的（不用每次開 Chrome）
                        src = out.with_suffix('.html'); src.write_text(h, encoding='utf-8'); qp.pdf(src, out)
                    from urllib.parse import quote as _qt
                    return self.send_bytes(out.read_bytes(), 'application/pdf', headers={'Content-Disposition': f"inline; filename*=UTF-8''{_qt('報價單_' + qq['id'] + '.pdf')}"})
                if re.match(r'^/q/[A-Za-z0-9_-]{10,40}$', path):
                    return self.send_bytes((core.ROOT / 'web' / 'q.html').read_bytes(), 'text/html; charset=utf-8')
                if path == '/join':
                    return self.send_bytes((core.ROOT / 'web' / 'join.html').read_bytes(), 'text/html; charset=utf-8')
                if path == '/mall' or re.match(r'^/mall/o/[A-Za-z0-9_-]{10,40}$', path):      # 示範商城網站（商品、購物車、免註冊下單、訂單進度）
                    return self.send_bytes((core.ROOT / 'web' / 'mall.html').read_bytes(), 'text/html; charset=utf-8')
                if path == '/qr':                    # 電腦打開客戶頁 → LINE 按鈕改顯示 QR code（只做 line.me 的連結）
                    u = q.get('u', '')
                    if not re.match(r'^https://line\.me/[A-Za-z0-9/@%?=&._~+-]{3,300}$', u):
                        return self.respond({'error': '不支援的連結'}, 400)
                    import io as _io, qrcode as _qr, qrcode.image.svg as _qsvg
                    b_ = _io.BytesIO(); _qr.make(u, image_factory=_qsvg.SvgPathImage, box_size=10, border=2).save(b_)
                    return self.send_bytes(b_.getvalue(), 'image/svg+xml')
                msf = re.match(r'^/api/public/sheetfeed/([A-Za-z0-9_-]{20,60})\.csv$', path)
                if msf:                              # 試算表自動化：=IMPORTDATA 讀這裡（金鑰只放在她的試算表）
                    import sheetfeed as _sf, hmac as _hm
                    with LOCK, con:
                        if not _hm.compare_digest(msf.group(1), _sf.token(con)):
                            return self.respond({'error': '連結無效'}, 404)
                        cyc = q.get('c') or core.now()[:7]
                        if not re.match(r'^\d{4}-\d{2}$', cyc):
                            return self.respond({'error': '期間格式 2026-10'}, 400)
                        return self.send_bytes(_sf.csv_text(con, cyc).encode('utf-8'), 'text/csv; charset=utf-8')
                if path == '/api/public/oa':          # 各品牌官方 LINE 加好友連結（客戶頁結尾的「LINE 問我們」）
                    import brands as _brands, line_bot as _lb      # 別名：do_GET 裡 import brands 會讓整個函式的 brands 變成區域變數
                    with LOCK, con:
                        return self.respond({b['id']: f"https://line.me/R/ti/p/{_lb.basic_id(con, b['id'])}" for b in _brands.all_(con) if _lb.basic_id(con, b['id'])})
                mq = re.match(r'^/mall/qr/([A-Za-z0-9_-]{10,40})$', path)       # 電腦下單 → 用手機 LINE 掃 QR code 連上訂單
                if mq:
                    import shop
                    with LOCK, con:
                        raw = shop.qr_svg(con, mq.group(1))
                    return self.send_bytes(raw, 'image/svg+xml')
                mi = re.match(r'^/mall/img/([a-z0-9-]{3,40})$', path)
                if mi:
                    import shop
                    with LOCK, con:
                        mime, raw = shop.image(con, mi.group(1))
                    return self.send_bytes(raw, mime, cache='public, max-age=86400')
                if path == '/api/public/mall':
                    import shop
                    with LOCK, con:
                        return self.respond(shop.public(con, q.get('t', ''), q.get('w', ''), q.get('l', '')))
                mo = re.match(r'^/api/public/mall/o/([A-Za-z0-9_-]{10,40})$', path)
                if mo:
                    import shop
                    with LOCK, con:
                        return self.respond(shop.order_view(con, mo.group(1)))
                page = re.match(r'^/(w|wp)/[A-Za-z0-9_-]{10,40}$', path)
                if page or path in ('/apply', '/hi', '/site', '/pf', '/privacy', '/store', '/home', '/chg', '/wguide'):      # /wguide＝夥伴使用教學（，?b=homeline）      # /chg＝業主申請改時間      # /home＝官方網站（三個品牌 ?b=）
                    f = {'w': 'w.html', 'wp': 'wp.html'}.get(page.group(1)) if page else path[1:] + '.html'
                    return self.send_bytes((core.ROOT / 'web' / f).read_bytes(), 'text/html; charset=utf-8')
                if path.startswith('/p/'):            # 給 LINE 顯示的照片（簽章網址）
                    with LOCK, con:
                        mime, raw = worker_portal.public_photo(con, path[3:])
                    if q.get('t'):                    # 全部照片頁的縮圖
                        raw, m2 = photos.thumb(raw); mime = m2 or mime
                    return self.send_bytes(raw, mime, cache='private, max-age=86400')      # 資安：不給 CDN 快取，刪掉的照片不會再被看到
                if path.startswith('/si/'):           # 夥伴看的現場須知＋照片（簽章網址）
                    import siteinfo
                    try:
                        with LOCK, con:
                            if path.startswith('/si/p/'):
                                mime, raw = siteinfo.photo(con, path[6:])
                                return self.send_bytes(raw, mime, cache='private, max-age=86400')
                            page = siteinfo.page(con, path[4:])
                    except (PermissionError, ValueError) as e0:
                        page = '<!doctype html><meta charset="utf-8"><p style="font:17px sans-serif;padding:24px">' + html_escape(str(e0)) + '</p>'
                    return self.send_bytes(page.encode('utf-8'), 'text/html; charset=utf-8', cache='private, no-store')
                if path.startswith('/pg/'):           # 一場服務的全部照片（負責人 業主要全部看得到）
                    try:
                        with LOCK, con:
                            page = worker_portal.gallery_page(con, path[4:])
                    except (PermissionError, ValueError) as e0:
                        page = '<!doctype html><meta charset="utf-8"><p style="font:17px sans-serif;padding:24px">' + html_escape(str(e0)) + '</p>'
                    return self.send_bytes(page.encode('utf-8'), 'text/html; charset=utf-8', cache='private, no-store')
                m3 = re.match(r'^/api/public/(work|portal)/([A-Za-z0-9_-]{10,40})$', path)
                if m3:
                    with LOCK, con:
                        return self.respond(worker_portal.work_view(con, m3.group(2)) if m3.group(1) == 'work' else worker_portal.portal(con, m3.group(2)))
                if path == '/api/public/testimonials':   # 官方網站：客戶同意不具名分享的好評（不含姓名、地址）
                    import testimonials
                    with LOCK, con:
                        return self.respond(testimonials.public(con, q.get('b', 'demoshop')))
                if path == '/api/public/store':        # 商店資訊（LINE Pay 審核、客戶查詢）
                    import store
                    return self.respond(store.view(con, q.get('b', 'demoshop')))
                if path == '/api/public/privacy':      # 個資蒐集告知（各品牌名稱）
                    import privacy
                    nt = privacy.notice(brands.name(q.get('b', 'demoshop')) if q.get('b') else '本公司')
                    with LOCK:
                        add = ix.setting(con, 'privacy_recruit') == '1'
                    if add:                                       # 補上應徵的良民證、作品集照片
                        nt['sections'] = [(k, v.replace('夥伴另含可服務區域、技能、可排時間與工時紀錄。', '夥伴另含可服務區域、技能、可排時間與工時紀錄；應徵者另含作品集照片與良民證（警察刑事紀錄證明，只有管理員看得到）。'))
                                          if k == '個資類別' else (k, v) for k, v in nt['sections']]
                    return self.respond(nt)
                if path == '/api/public/chg':          # 業主申請改時間（簽章網址）
                    import reschedule as _rs
                    with LOCK, con:
                        return self.respond(_rs.view(con, q.get('o', ''), q.get('s', '')))
                if path == '/api/public/pf':           # 客戶看的夥伴作品集（匿名、簽章網址）
                    import automation
                    with LOCK, con:
                        return self.respond(automation.portfolio_view(con, q.get('o', ''), q.get('s', '')))
                if path == '/api/public/prefill':
                    return self.respond(worker_portal.prefill(con, q.get('t', '')))
                if path == '/api/public/onboard':
                    return self.respond(worker_portal.onboard_info(con, q.get('kind', ''), q.get('t', '')))
                m2 = re.match(r'^/api/public/quote/([A-Za-z0-9_-]{10,40})$', path)
                if m2:
                    return self.respond(booking.view(con, m2.group(1)))
                if path == '/api/public/join':
                    return self.respond(booking.join_info(con, q.get('t', '')))
                if path == '/api/public/options':
                    bid = brands.pick(q.get('b')); cfg = pricing.config(con, bid); bb = brands.get(con, bid) or {}
                    sp = {k: v for k, v in pricing.SPACES.items() if not cfg.get('spaces') or k in cfg['spaces']}      # 品牌限定空間（生活線只有住家）
                    return self.respond({'generic': pricing.GENERIC_SERVICES, 'addons': cfg['addons'], 'slots': list(booking.SLOTS), 'freq': list(pricing.FREQ), 'spaces': sp, 'quote_only': bool(cfg.get('quote_only')),
                                         'bnb': {'services': pricing.BNB_SERVICES, 'pay_methods': pricing.PAY_METHODS, 'overtime': cfg['bnb_overtime']},
                                         'homeline': {'services': pricing.MAMA_SERVICES, 'rate': cfg.get('homeline_rate', 450), 'min_hours': cfg.get('homeline_min_hours', 3), 'hours_by_size': cfg.get('homeline_hours_by_size', '')},
                                         'conditions': pricing.CONDITIONS, 'site_visit_fee': cfg['site_visit_fee'], 'community_visit_fee': cfg['community_visit_fee'],
                                         'low_value': cfg['low_value'], 'min_photos': booking.MIN_PHOTOS, 'max_photos': booking.MAX_PHOTOS,
                                         'trash': {'bag': cfg.get('trash_bag'), 'more': cfg.get('trash_bag_more') or cfg.get('trash_bag'), 'addon': cfg.get('trash_addon'), 'max': cfg.get('trash_max_bags'),
                                                   'no': __import__('daidiu').NO, 'on': False, 'plans': __import__('daidiu').plans_opt()},      # 垃圾代丟（服務加購垃圾清收不做了：店長 只要代丟）
                                         'brand': {k: bb.get(k) for k in ('id', 'name', 'title', 'color', 'intro')}, 'service_counties': __import__('regions').service_counties(con, bid),
                                         'lines': [{k: ln.get(k) for k in ('id', 'name', 'kind', 'services', 'hourly', 'intro')} for ln in brands.lines(con, bid)]})
                if path in ('/book', '/book/'):
                    return self.send_bytes((core.ROOT / 'web' / 'book.html').read_bytes(), 'text/html; charset=utf-8')
                if path == '/api/push/key':
                    return self.respond({'key': notify.public_key()})
                if path == '/api/calendar/feed':      # 停用：通知一律用 LINE 官方（金鑰已清掉）
                    return self.respond({'error': 'Google 日曆同步已停用'}, 404)
                m4 = re.match(r'^/api/quote-preview/([A-Za-z0-9_-]{10,40})$', path)
                if m4:                     # 內部預覽正式報價單（帶內部簽章網址，或登入的同事）
                    import hmac as _h
                    try:
                        pu = self.user(con)
                    except PermissionError:
                        pu = None
                    if not _h.compare_digest(q.get('sig', ''), booking.preview_sig(con, m4.group(1))):
                        if not pu: raise PermissionError('請先登入')
                        need(pu, 'ops')
                    out = {**booking.view(con, m4.group(1), preview=True), 'preview': True}
                    if pu and 'money' not in acc.CAN.get(pu['role'], set()):      # 客服：看得到內容，看不到金額
                        out = booking.mask_money(out)
                    return self.respond(out)
                user = self.user(con)
                m5 = re.match(r'^/api/quote-pdf/([GM]?\d{10,12})$', path)          # 企業線 G／生活線 M 開頭的報價編號
                if m5:                     # A4 正式報價單 PDF（仿示範服務店樣本，自動分頁）：有金額，只給有財務權限的人
                    need(user, 'money')
                    import importlib.util, tempfile, pathlib
                    spec = importlib.util.spec_from_file_location('quote_print', str(pathlib.Path(__file__).resolve().parent / 'tools' / 'quote_print.py'))
                    qp = importlib.util.module_from_spec(spec); spec.loader.exec_module(qp)
                    h, d = qp.html(con, m5.group(1))
                    tmp = pathlib.Path(tempfile.gettempdir()) / f'qprint_{m5.group(1)}.html'; out = tmp.with_suffix('.pdf')
                    tmp.write_text(h, encoding='utf-8'); qp.pdf(tmp, out)
                    from urllib.parse import quote as _qt
                    fn = f"報價單_{m5.group(1)}.pdf"
                    return self.send_bytes(out.read_bytes(), 'application/pdf', headers={'Content-Disposition': f"inline; filename*=UTF-8''{_qt(fn)}"})
                if path == '/api/me':
                    return self.respond({**acc.public(user), 'csrf': user['csrf'], 'role_name': acc.ROLES[user['role']], 'where': where})
                if user['must_change']:
                    raise PermissionError('請先設定你自己的密碼')
                if path == '/api/state':
                    tours = [r[0] for r in con.execute('SELECT key FROM tours WHERE user_id=?', (user['id'],))]
                    import guide
                    return self.respond({**state_for(con, user), 'csrf': user['csrf'], 'me': acc.public(user), 'tours': tours, 'guide': guide.state(con, user),
                                         'push_devices': con.execute('SELECT COUNT(*) FROM push_subs WHERE user_id=?', (user['id'],)).fetchone()[0]})
                if path.startswith('/api/order-media/'):      # 業主 LINE 傳的現場照片／影片（管理層看）
                    need(user, 'ops'); import siteinfo; siteinfo.ensure_media(con)
                    r_ = con.execute('SELECT mime,data FROM order_media WHERE id=?', (path.rsplit('/', 1)[-1],)).fetchone()
                    if not r_:
                        raise ValueError('找不到')
                    return self.send_bytes(r_[1], r_[0], cache='private, max-age=86400')
                if path.startswith('/api/site-photo/'):       # 案場須知照片（管理層看）
                    need(user, 'ops')
                    r_ = con.execute('SELECT mime,data FROM site_photos WHERE id=?', (path.rsplit('/', 1)[-1],)).fetchone()
                    if not r_:
                        raise ValueError('找不到照片')
                    raw, mime = (photos.thumb(r_[1]) if q.get('t') else (r_[1], r_[0]))
                    return self.send_bytes(raw, mime or r_[0], cache='private, max-age=86400')
                if path.startswith('/api/photo/'):
                    photo = core.get(con, 'attachments', path.rsplit('/', 1)[-1])
                    if user['role'] == 'cleaner':
                        board.worker_photo_allowed(con, user, photo['order_id'])
                    raw = con.execute('SELECT content FROM attachment_blobs WHERE id=?', (photo['id'],)).fetchone()[0]
                    if q.get('t'):                        # 縮圖（工單照片一排左右滑）
                        raw, mime = photos.thumb(raw)
                        return self.send_bytes(raw, mime or photo['mime'], cache='private, max-age=86400')
                    return self.send_bytes(raw, photo['mime'], cache='private, max-age=3600')
                need(user, 'ops')
                if path == '/api/billing':
                    need(user, 'money')
                    return self.respond(board.billing_board(con, q.get('month') or core.now()[:7]))
                if path == '/api/payhist':                # 收款紀錄（管理群點了看得到歷史付款）
                    need(user, 'money')
                    import payhist
                    return self.respond(payhist.view(con, q.get('month', '')))
                if path == '/api/ledger':                 # 記帳：示範公司各品牌收支、待確認、應收應付
                    need(user, 'money')
                    out = ledger.summary(con, q.get('month') or core.now()[:7]); con.commit()
                    return self.respond(out)
                if path == '/api/ledger/entry':
                    need(user, 'money')
                    return self.respond(ledger.get(con, q.get('id', '')))
                if path.startswith('/api/ledger/photo/'):
                    need(user, 'money')
                    lp = ledger.photo_path(ledger.get(con, path.rsplit('/', 1)[-1]))
                    if not lp or not lp.exists():
                        raise ValueError('這筆沒有照片')
                    return self.send_bytes(lp.read_bytes(), 'image/jpeg', cache='private, max-age=3600')
                if path == '/api/ledger/package':         # 記帳士包（zip）
                    need(user, 'money')
                    lm = q.get('month') or core.now()[:7]; core.date(lm + '-01')
                    zbytes = ledger.package(con, lm); con.commit()
                    from urllib.parse import quote as _qt
                    return self.send_bytes(zbytes, 'application/zip', headers={'Content-Disposition': f"attachment; filename*=UTF-8''{_qt(ledger.COMPANIES[ledger.COMPANY]['name'] + '_' + lm + '_記帳士包.zip')}"})
                if path == '/api/candidates':
                    return self.respond(dispatch.candidates(con, q.get('id', '')))
                if path == '/api/review/detail':          # 審核在中控台看全文、圖片、每則平台時間
                    need(user, 'money'); import mkbridge as _mb
                    out = _mb.review_detail(con, q.get('id', '')); con.commit()          # 圖片代碼要存起來，/mkimg 才打得開
                    return self.respond(out)
                if path == '/api/marketing':              # 更多 › 行銷（待審、排程、發布、成效）
                    import mkbridge as _mb, mkpatrol as _mp
                    out = _mb.overview(con); con.commit()
                    out['patrol'] = _mp.pending()          # 海巡留言待審
                    out['tposts'] = _mp.posts()            # Threads 發文（排隊中／已發／失敗）
                    out['tdrafts'] = _mp.drafts()          # 5100 後台不用了，舊產文草稿也在這審
                    out['inbox'] = __import__('mkinbox').listing()      # 給行銷的素材／企劃
                    return self.respond(out)
                if path == '/api/chats':
                    if user['role'] == 'staff':
                        raise PermissionError('舊對話紀錄只有管理員看得到')      # 稽核：客服帳號不能整批搜尋舊對話
                    return self.respond(workflow.chat_search(con, q))
                if path in ('/api/source', '/api/sheets'):
                    need(user, 'money')          # Excel 原表有進帳、支出、夥伴時薪
                if path == '/api/source':
                    offset = max(0, int(q.get('offset', '0')))
                    return self.respond(core.rows(con, 'SELECT sheet,row_no,payload FROM source_rows WHERE sheet=? ORDER BY row_no LIMIT 100 OFFSET ?', (q.get('sheet', ''), offset)))
                if path == '/api/sheets':
                    return self.respond([r[0] for r in con.execute('SELECT DISTINCT sheet FROM source_rows ORDER BY sheet')])
                if path == '/api/quotes':
                    need(user, 'ops')
                    qb = brands.pick(q.get('b'))
                    money = 'money' in acc.CAN.get(user['role'], set())
                    ql = booking.admin_list(con)
                    if not money:
                        for x in ql: x['total'] = None
                    return self.respond({'quotes': ql, 'pricing': pricing.config(con, qb) if money else {}, 'money': money, 'dispatch_mode': ix.setting(con, 'dispatch_mode', 'manual'),
                                         'scopes': pricing.scopes(con, qb), 'company': booking.company(con, qb), 'stamp': bool(booking.stamp(con, qb)), 'rule_brand': qb,
                                         'bind_message': {b['id']: booking.bind_template(con, b['id']) for b in brands.all_(con)},
                                         'brands': [{'id': b['id'], 'name': b['name']} for b in brands.all_(con)]})
                if path == '/api/brands':
                    need(user, 'admin')
                    return self.respond({'brands': brands.overview(con)})
                if path == '/api/customers/unbound-hints':     # 未綁定的為什麼查不到對話
                    need(user, 'ops'); import pairing
                    return self.respond(pairing.unbound_hints(con))
                if path == '/api/customers/unbound':
                    need(user, 'ops')
                    rows_ = core.rows(con, "SELECT c.id,c.name,c.line_name,c.phone,c.status,c.channel FROM customers c WHERE c.status IN ('active','paused') "
                                           "AND NOT EXISTS (SELECT 1 FROM line_users l WHERE l.customer_id=c.id) ORDER BY c.status,c.name")
                    if user['role'] == 'staff':
                        for x in rows_:
                            ph = ''.join(ch for ch in (x.get('phone') or '') if ch.isdigit()); x['phone'] = ('＊' * max(0, len(ph) - 3) + ph[-3:]) if ph else ''
                    return self.respond(rows_)
                if path == '/api/pairing':             # 中控台 LINE 配對（偵測到還沒配對的聊天室＋證據）
                    need(user, 'ops')
                    import pairing
                    with LOCK, con:
                        return self.respond(pairing.overview(con))
                if path == '/api/worker-links':        # 夥伴視角：管理員預覽某位夥伴看到的主控板
                    need(user, 'admin')
                    with LOCK, con:
                        return self.respond({'portal': worker_portal.portal_url(con, q.get('id', ''))})
                if path == '/api/quote':
                    need(user, 'ops')
                    out = booking.admin_get(con, q.get('id', ''))
                    if 'money' not in acc.CAN.get(user['role'], set()):        # 客服看得到內容、照片，看不到金額
                        out = {**out, 'subtotal': None, 'tax': None, 'total': None, 'items': [{**i, 'amount': None} for i in out['items']],
                               'estimate': {'mode': (out.get('estimate') or {}).get('mode')}, 'similar': [{**s, 'subtotal': None} for s in out.get('similar', [])], 'preview_sig': ''}
                    return self.respond(out)
                if path == '/api/worker-file':          # 夥伴檔案：作品集（同事看）／良民證（只有管理員）
                    need(user, 'ops'); import recruit
                    mime, raw = recruit.file_get(con, q.get('id', ''), user)
                    return self.send_bytes(raw, mime, cache='private, no-store')
                if path == '/api/issue/file':           # 功能回報的截圖
                    need(user, 'ops'); import sysreport as issues; issues.ensure_files(con)
                    r_ = con.execute('SELECT mime,data FROM sys_report_files WHERE id=?', (q.get('id', ''),)).fetchone()
                    if not r_:
                        raise ValueError('找不到截圖')
                    return self.send_bytes(r_[1], r_[0], cache='private, max-age=86400')
                if path == '/api/quote-photo':
                    need(user, 'ops')
                    mime, raw = booking.photo(con, q.get('id', ''))
                    return self.send_bytes(raw, mime, cache='private, max-age=86400')
                if path == '/api/triage':
                    with LOCK, con:
                        out = {'items': trash.listing(con), 'trash': trash.trash_list(con), 'history': trash.history(con)}
                    if user['role'] == 'staff':          # 稽核：客服帳號在待整理看不到完整電話（只露末 3 碼）
                        import re as _re
                        mask = lambda s: _re.sub(r'0\d[\d\- ]{6,}(\d{3})', lambda m: '＊' * 6 + m.group(1), str(s))
                        out = json.loads(mask(json.dumps(out, ensure_ascii=False)))
                    return self.respond(out)
                if path == '/api/duplicates':
                    return self.respond(board.duplicate_suggestions(con))
                if path == '/api/line/overview':
                    need(user, 'admin')
                    return self.respond({'quota': line_bot.quota(con), 'mode': line_bot.mode(con),
                                         'modes': {b['id']: line_bot.mode(con, b['id']) for b in brands.all_(con)}, 'brands': [{'id': b['id'], 'name': b['name']} for b in brands.all_(con)], 'test_users': sorted(line_bot.test_users(con)),
                                         'group': ix.setting(con, 'line_dispatch_group'), 'webhook': ix.setting(con, 'line_webhook_note'),
                                         'staff': sorted(line_bot.staff_users(con)), 'admin_group': line_bot.admin_group(con),
                                         'users': core.rows(con, 'SELECT l.*,c.name customer,w.name worker FROM line_users l LEFT JOIN customers c ON c.id=l.customer_id LEFT JOIN workers w ON w.id=l.worker_id ORDER BY l.last_seen DESC LIMIT 300'),
                                         'groups': core.rows(con, 'SELECT * FROM line_groups ORDER BY joined_at DESC'),
                                         'outbox': core.rows(con, 'SELECT id,created_at,to_id,kind,alt,status,error FROM line_outbox ORDER BY id DESC LIMIT 40')})
                if path == '/api/order-log':      # dispatch_log.at 是 UTC，換成台灣時間給畫面
                    rows = core.rows(con, "SELECT *,datetime(at,'+8 hours') at FROM dispatch_log WHERE order_id=? ORDER BY id", (q.get('id', ''),))
                    return self.respond(rows)
                if path == '/oauth/callback':
                    need(user, 'admin')
                    with LOCK, con:
                        ix.oauth_finish(con, q, user['token_hash'])
                    return self.send_bytes(b'', 'text/plain', 302, {'Location': '/#settings'})
                return self.respond({'error': '找不到頁面'}, 404)
        except Exception as e:
            self.fail(e)

    # ── POST ──
    def line_webhook(self, acc='demoshop'):
        """官方 LINE webhook（每個品牌一條：/line/webhook/demoshop|bizline|homeline）：驗簽 → 立刻回 200 → 背景轉給舊系統＋處理事件。"""
        if acc not in line_bot.BRAND:
            return self.respond({'error': 'account'}, 404)
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0 or length > 1024 * 1024:
            return self.respond({'error': 'size'}, 400)
        raw = self.rfile.read(length); sig = self.headers.get('X-Line-Signature', '')
        with contextlib.closing(core.connect()) as con:
            ok = line_bot.verify(con, raw, sig, acc)
        if not ok:
            return self.respond({'error': 'signature'}, 403)
        self.respond({'ok': True})
        def work():
            for ev in json.loads(raw).get('events', []):
                try:
                    with LOCK, contextlib.closing(core.connect()) as con, con:
                        core.CTX.actor = 'LINE'
                        line_bot.handle(con, acc, ev)
                except Exception as e:
                    traceback.print_exc()
                    report_error(f'LINE webhook（{acc}）', e)
                    try:                              # 處理出錯整筆回滾 → 客戶打的字也不見；另外留一筆紀錄（標「處理失敗」）
                        src = ev.get('source') or {}
                        with contextlib.closing(core.connect()) as con2, con2:
                            con2.execute('INSERT INTO line_inbox(at,account,user_id,group_id,type,text,data) VALUES(?,?,?,?,?,?,?)',
                                         (core.now(), acc, src.get('userId'), src.get('groupId') or src.get('roomId'), ev.get('type'),
                                          ((ev.get('message') or {}).get('text') or '')[:500], ('處理失敗 ' + ((ev.get('postback') or {}).get('data') or ''))[:200]))
                    except Exception:
                        pass
            line_bot.kick()
        threading.Thread(target=work, daemon=True).start()

    def do_POST(self):
        wp = urllib.parse.urlsplit(self.path).path
        if wp.startswith('/u/'):                       # 退訂：信箱「取消訂閱」一鍵（RFC 8058）＋確認頁按鈕（表單 confirm=1）
            import mailunsub
            try:
                n_ = int(self.headers.get('Content-Length', '0') or 0)
                body_ = self.rfile.read(min(n_, 2000)).decode('utf-8', 'ignore') if n_ > 0 else ''
                form_ = 'confirm=1' in body_
                if not form_ and 'List-Unsubscribe=One-Click' not in body_:
                    raise ValueError('請在信件裡按「取消訂閱」')      # 每日檢查：空的 POST（掃描器）不算退訂
                with contextlib.closing(core.connect()) as con_, LOCK, con_:
                    e_ = mailunsub.unsub(con_, wp[3:], 'link' if form_ else 'one-click')
                if form_:
                    return self.send_bytes(mailunsub.page(e_).encode('utf-8'), 'text/html; charset=utf-8')
                return self.send_bytes(b'ok', 'text/plain; charset=utf-8')
            except Exception as e:
                return self.fail(e)
        if wp == '/api/public/web-booking':          # 雲端搬本機：官網（舊站）／booking_webhook 送預約（取代 Google Apps Script Web App）
            return self.web_booking()
        if wp == '/line/webhook' or wp.startswith('/line/webhook/'):
            try:
                if self.host() == 'site':
                    raise PermissionError('請使用店家營運中樞（示範）的正式網址')
                return self.line_webhook(wp.rsplit('/', 1)[-1] if wp.startswith('/line/webhook/') else 'demoshop')
            except Exception as e:
                return self.fail(e)
        try:
            if self.host() == 'site' and not wp.startswith('/api/public/'):
                raise PermissionError('請使用店家營運中樞（示範）的正式網址')      # 官網網址只收公開表單
            if not self.origin_ok():
                raise PermissionError('來源不符，請在店家營運中樞（示範）內操作')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('請使用 JSON 格式')
            length = int(self.headers.get('Content-Length', '0'))
            limit = 8 * 1024 * 1024 if self.path in ('/api/photo', '/api/me/photo', '/api/recruit/upload', '/api/shop/image', '/api/issue/report', '/api/materials/upload', '/api/ledger/upload', '/api/mk/upload', '/api/site/photo', '/api/import/preview', '/api/import/commit') else 14 * 1024 * 1024 if re.match(r'^/api/public/work/[\w-]+/photo$', self.path) else 3 * 1024 * 1024 if (re.match(r'^/api/public/quote/[\w-]+/photo$', self.path) or self.path == '/api/public/apply/photo') else 1024 * 1024
            if length <= 0 or length > limit:
                raise ValueError('請求大小不符')
            p = json.loads(self.rfile.read(length))
            if not isinstance(p, dict):
                raise ValueError('資料格式不符')
            route = urllib.parse.urlsplit(self.path).path.strip('/').split('/')
            jpeg = None
            if route in (['api', 'issue', 'report'], ['api', 'materials', 'upload']) and p.get('images'):      # 回報截圖先在鎖外壓好（去掉 EXIF、長邊 1600）；素材上傳一樣
                import recruit, base64
                p['_jpegs'] = [recruit.shrink(base64.b64decode(str(x).split(',', 1)[-1])) for x in list(p.get('images') or [])[:6]]
                p.pop('images', None)
            if route == ['api', 'ledger', 'upload']:              # 記帳：憑證照片先在鎖外壓好（讀取在背景，不卡系統）
                import recruit, base64
                p['_jpegs'] = [recruit.shrink(base64.b64decode(str(x).split(',', 1)[-1])) for x in list(p.get('images') or [])[:10]]
                for x in list(p.get('pdfs') or [])[:3]:              # PDF 掃描檔：每一頁一張（一頁好幾張收據會自動拆開）
                    p['_jpegs'] += ledger.pdf_pages(base64.b64decode(str(x).split(',', 1)[-1]))
                p.pop('images', None); p.pop('pdfs', None)
            if route == ['api', 'ledger', 'key', 'test']:          # 記帳：測試金鑰要連 Claude → 鎖外做
                with contextlib.closing(core.connect()) as con0:
                    need(self.user(con0), 'admin'); key0 = ledger.api_key(con0)
                p['_check'] = list(ledger.check_key(key0))
            if route == ['api', 'public', 'apply', 'photo']:        # 應徵照片先在鎖外壓好（壓縮要幾百毫秒，不讓整個系統跟著卡）
                import recruit, base64
                jpeg = recruit.shrink(base64.b64decode(str(p.get('content', '')).split(',', 1)[-1]))
            with LOCK, contextlib.closing(core.connect()) as con, con:
                pd_form = {('api', 'public', 'quote'): 'book', ('api', 'public', 'join'): 'join', ('api', 'public', 'site'): 'site',
                           ('api', 'public', 'apply'): 'apply', ('api', 'public', 'hello'): 'hi', ('api', 'public', 'mall', 'order'): 'mall'}.get(tuple(route))
                if pd_form:                    # 個資法：蒐集告知要勾選同意，後端檢查並留存（版本、時間、IP）
                    import privacy
                    privacy.require(p)
                    privacy.record(con, p, pd_form, self.ip(), str(p.get('account') or ''), '', str(p.get('t') or '')[:12])
                if route == ['api', 'public', 'apply', 'photo']:      # 應徵表單：作品集照片（最多 8 張，送出應徵後掛到這位夥伴）
                    import recruit, base64
                    t0 = str(p.get('t', ''))
                    worker_portal._bind(con, 'apply', t0)
                    recruit.ensure(con)
                    kind0 = 'feedback' if p.get('kind') == 'feedback' else 'portfolio'          # 作品集（最多 8）／客戶好評截圖（最多 6）
                    if con.execute('SELECT COUNT(*) FROM worker_files WHERE ref=? AND kind=?', ('apply:' + t0, kind0)).fetchone()[0] >= (6 if kind0 == 'feedback' else 8):
                        raise ValueError('好評截圖最多 6 張' if kind0 == 'feedback' else '作品集照片最多 8 張')
                    recruit.save_file(con, kind0, jpeg, None, 'apply:' + t0, '應徵者')
                    return self.respond({'ok': True, 'kind': kind0, 'count': con.execute('SELECT COUNT(*) FROM worker_files WHERE ref=? AND kind=?', ('apply:' + t0, kind0)).fetchone()[0]})
                if route == ['api', 'public', 'jserr']:          # 夥伴工作頁／報價單的程式錯誤 → 功能回報（每個 IP 一小時最多 10 筆）
                    now = time.time(); ip = self.ip(); JSERR[ip] = [t for t in JSERR.get(ip, []) if now - t < 3600][-9:] + [now]
                    if len(JSERR) > 500:
                        for k in [k for k, v in JSERR.items() if not v or now - v[-1] > 3600]: JSERR.pop(k, None)
                    if len(JSERR[ip]) <= 10:          # 資安：公開來源不收任意文字（避免有人把指令塞進每天的修正流程），只留錯誤種類＋檔名行號
                        import sysreport as issues
                        page = {'夥伴工作頁': '夥伴工作頁', '報價單': '報價單'}.get(str(p.get('page', '')), '公開頁')
                        kind = (re.match(r'^[A-Za-z]{0,30}Error', str(p.get('msg', ''))) or [None])[0] or 'Error'
                        frames = '、'.join(f'{a}:{b}' for a, b, _ in re.findall(r'([a-z0-9-]+\.js)[^\s]*?:(\d+):(\d+)', str(p.get('stack', '')))[:6])
                        issues.auto(con, f'{page}（公開）', kind, frames, source='public')
                    return self.respond({'ok': True})
                if route == ['api', 'public', 'chg']:                # 業主送出改時間申請（簽章網址；管理群確認後才改）
                    import reschedule as _rs
                    return self.respond(_rs.submit(con, str(p.get('o', '')), str(p.get('s', '')), p))
                if route == ['api', 'public', 'mall', 'order']:      # 商城網站下單（免註冊；價格、庫存由伺服器算）
                    import shop
                    return self.respond(shop.web_order(con, p, self.ip()))
                if route == ['api', 'public', 'mall', 'ev']:         # 商城：瀏覽／加入購物車次數（只收商品編號，不收個資）
                    import shop
                    pid = str(p.get('pid', ''))[:40]; kind = p.get('kind')
                    if kind in ('view', 'add') and con.execute('SELECT 1 FROM shop_products WHERE id=?', (pid,)).fetchone():
                        shop.bump(con, pid, kind)
                    return self.respond({'ok': True})
                if route == ['api', 'public', 'inquiry']:
                    return self.respond(dispatch.create_inquiry(con, p, self.ip()))
                if route == ['api', 'public', 'estimate']:
                    return self.respond({k: v for k, v in pricing.estimate(con, p).items() if not k.startswith('internal_')})      # 內部初估不給客戶看
                if route == ['api', 'public', 'quote']:
                    return self.respond(booking.create_quote(con, p, self.ip()))
                if route[:3] == ['api', 'public', 'quote'] and len(route) == 5 and route[4] == 'paid':      # 客戶回報已匯款（末五碼）
                    import payments
                    q0 = booking._q(con, route[3]); r0 = con.execute('SELECT * FROM payment_requests WHERE id=? AND quote_id=?', (p.get('pid', ''), q0['id'])).fetchone()
                    if not r0: raise ValueError('找不到這筆款項')
                    row = payments.report(con, r0['id'], p.get('ref', ''), '客戶')
                    f0 = json.loads(q0['form']); line_bot.on_payment_reported(con, row, f0.get('company') or f0.get('name') or '客戶')
                    return self.respond({'ok': True, 'status': row['status']})
                if route[:3] == ['api', 'public', 'quote'] and len(route) == 5 and route[4] == 'linepay':      # 客戶按「LINE Pay 付款」
                    import linepay
                    return self.respond(linepay.request(con, route[3], str(p.get('pid', '')), line_bot.BASE_URL, test=bool(p.get('test'))))
                if route[:3] == ['api', 'public', 'quote'] and len(route) == 5 and route[4] == 'requote':
                    return self.respond(booking.requote(con, route[3], p))
                if route[:3] == ['api', 'public', 'quote'] and len(route) == 5 and route[4] == 'sign':
                    return self.respond(booking.sign(con, route[3], p, self.ip()))
                if route[:3] == ['api', 'public', 'quote'] and len(route) == 5 and route[4] == 'photo':
                    return self.respond(booking.add_photo(con, route[3], p))
                if route[:3] == ['api', 'public', 'quote'] and len(route) == 5 and route[4] == 'submit':
                    return self.respond(booking.submit(con, route[3]))
                if route == ['api', 'public', 'sitevisit']:
                    return self.respond(booking.site_visit(con, p, self.ip()))
                if route == ['api', 'public', 'join']:
                    return self.respond(booking.join(con, str(p.get('t', '')), p))
                if route[:2] == ['api', 'public'] and len(route) == 5 and route[2] in ('work', 'portal'):
                    fn = worker_portal.work_action if route[2] == 'work' else worker_portal.portal_action
                    return self.respond(fn(con, route[3], route[4], p))
                if route == ['api', 'public', 'site']:
                    return self.respond(worker_portal.save_site(con, str(p.get('t', '')), p))
                if route == ['api', 'public', 'apply']:
                    return self.respond(worker_portal.apply(con, str(p.get('t', '')), p))
                if route == ['api', 'public', 'hello']:
                    return self.respond(worker_portal.hello(con, str(p.get('t', '')), p))
                if route == ['api', 'login']:
                    try:
                        u = acc.login(con, p.get('username'), p.get('password'), self.ip())
                    except PermissionError:
                        con.commit()   # 失敗次數要留下來，不能跟著回復
                        raise
                    token, life = acc.new_session(con, u, self.ip())
                    core.CTX.actor = u['name']; core.audit(con, 'login', 'users', u['id'], after={'ip': self.ip()})
                    return self.respond({'ok': True, 'must_change': bool(u['must_change'])}, headers={'Set-Cookie': self.cookie_header(token, life)})
                user = self.user(con)
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), user['csrf']):
                    raise PermissionError('頁面驗證已過期，請重新整理')
                if route == ['api', 'logout']:
                    acc.logout(con, user['token_hash'])
                    return self.respond({'ok': True}, headers={'Set-Cookie': f'{COOKIE}=; Max-Age=0; Path=/'})
                if route == ['api', 'password']:
                    if user['id'] == acc.LOCAL_ID:
                        raise ValueError('本機登入不需要密碼')
                    return self.respond(acc.change_pw(con, user, p.get('old'), p.get('new')))
                if user['must_change']:
                    raise PermissionError('請先設定你自己的密碼')
                if route == ['api', 'push', 'subscribe']:
                    return self.respond(notify.subscribe(con, user, p))
                if route == ['api', 'push', 'unsubscribe']:
                    return self.respond(notify.unsubscribe(con, user, p))
                if route == ['api', 'tour']:
                    if p.get('reset'):
                        con.execute('DELETE FROM tours WHERE user_id=?', (user['id'],))
                    else:
                        con.execute('INSERT OR IGNORE INTO tours(user_id,key) VALUES(?,?)', (user['id'], str(p.get('key', ''))[:60]))
                    return self.respond({'saved': True})
                result = self.route(con, user, route, p)
                if result is None:
                    return self.respond({'error': '找不到操作'}, 404)
            self.respond(result)
            line_bot.kick()            # 交易已提交，叫 LINE 發送佇列馬上送
        except Exception as e:
            self.fail(e)

    def route(self, con, user, route, p):
        r = '/'.join(route)
        # 服務人員（自己的工作）
        if r.startswith('api/me/'):
            if user['role'] != 'cleaner':
                raise PermissionError('這是服務人員專用功能')
            kind = route[2]
            if kind == 'apply': return board.apply(con, user, p['order_id'])
            if kind == 'withdraw': return board.apply(con, user, p['order_id'], withdraw=True)
            if kind == 'respond': return board.respond(con, user, p['assignment_id'], bool(p.get('accept')))
            if kind == 'checkin': return board.checkin(con, user, p)
            if kind == 'report': return board.field_report(con, user, p)
            if kind == 'availability': return dispatch.set_availability(con, user['worker_id'], p)
            if kind == 'line-bind': return line_bot.bind_code(con, 'worker', user['worker_id'])
            if kind == 'photo':
                a = board._my_assignment(con, user, p['assignment_id'])
                board.worker_photo_allowed(con, user, a['order_id'])
                return photos.upload(con, {**p, 'order_id': a['order_id']})
            return None
        need(user, 'ops')
        money_flows = {'receipt', 'receipt-verify', 'allocate', 'credit', 'receipt-refund', 'plan', 'plan-adjust'}
        if route[:2] == ['api', 'save'] and len(route) == 3:
            if 'money' not in acc.CAN.get(user['role'], set()):
                for k in ('approved_amount', 'adjustment', 'rate', 'worker_rate', 'amount', 'expense', 'visit_price', 'billable_hours', 'billing_rule', 'pricing_note',
                          'approved_minutes', 'pay_mode', 'billing_mode', 'plan_id') + WORKER_BANK:
                    p.pop(k, None)      # 看不到的欄位也不能改（表單送空白會洗掉原值）
            if route[2] == 'sites' and p.get('entry_mode'):          # 管理層在中控台設定開門方式＝已確認（不再列待補、不再問業主）
                old_ = core.get(con, 'sites', p['id']) if p.get('id') else {}
                if p['entry_mode'] != (old_.get('entry_mode') or '') or not old_.get('entry_ok'):
                    p['entry_ok'] = f"{user['name']}（中控台） {core.now()[5:16].replace('T', ' ')}"
            if route[2] == 'customers' and p.get('line_account') and not brands.valid(p['line_account']):
                raise ValueError('品牌不對')          # 品牌改成獨立下拉（line_account）
            if route[2] == 'customers' and p.get('id') and 'line_account' in p:
                b0 = con.execute('SELECT account FROM line_users WHERE customer_id=? AND is_friend=1 LIMIT 1', (p['id'],)).fetchone()
                if b0 and p['line_account'] != b0[0]:
                    raise ValueError('這位客戶已經綁定其他品牌的官方 LINE，品牌不能直接改（要換請先到「LINE 配對」解除）')
            if user['role'] == 'staff' and route[2] == 'customers' and '＊' in str(p.get('phone', '')):
                p.pop('phone', None)    # 客服看到的是遮住的電話：沒改就不要蓋掉真的電話
            if user['role'] == 'staff' and route[2] == 'customers' and p.get('id') and not p.get('notes') and 'notes' in p:
                p.pop('notes', None)    # 備註被遮掉：送空白不能洗掉原本的備註
            if user['role'] == 'staff' and route[2] == 'sites' and 'entry_notes' in p and not p.get('entry_notes'):
                p.pop('entry_notes', None)   # 開門說明被遮掉：送空白不能洗掉
            if route[2] == 'assignments' and not p.get('id') and p.get('worker_id') and not p.get('rate'):     # 新指派沒帶時薪 → 案場夥伴時薪／夥伴本人的時薪
                wr = core.pay_rate(con, p.get('order_id'), p['worker_id'])
                if wr: p['rate'] = wr
            before = core.get(con, 'orders', p['id']) if route[2] == 'orders' and p.get('id') else None
            s_before = core.get(con, 'sites', p['id']) if route[2] == 'sites' and p.get('id') else None
            w_before = core.get(con, 'workers', p['id']) if route[2] == 'workers' and p.get('id') else None
            if w_before and p.get('status') == 'active' and w_before['status'] != 'active' and user['role'] != 'admin':
                import recruit
                stg = recruit.open_stage(con, w_before['id'])
                if stg:                       # 稽核：客服帳號不能跳過招募關卡（LINE 那邊本來就擋）
                    raise PermissionError(f"{w_before['name']} 還在招募「{recruit.stage_name(stg)}」關卡，請照招募流程走，或請管理員處理")
            a_before = core.get(con, 'assignments', p['id']) if route[2] == 'assignments' and p.get('id') else None
            if route[2] == 'customers' and 'channel' in p:      # 品牌一定要分清楚：改來源（品牌）時同步 line_account；已綁別品牌官方 LINE 的不能直接改
                cb = core.get(con, 'customers', p['id']) if p.get('id') else {}
                if brands.valid(p.get('line_account') or ''):          # 品牌另外選（選 LINE 群組不能把企業線改成示範服務店）
                    nb = p['line_account']
                elif p['channel'] in {b['channel'] for b in brands.all_(con) if b['channel']}:
                    nb = brands.of_channel(con, p['channel'])
                else:                                                   # 聯絡管道不是官方 LINE（LINE 群組、店長個人 LINE…）：品牌維持原本的
                    nb = brands.of_customer(cb) if cb else 'demoshop'
                if not cb or brands.of_customer(cb) != nb or (p['channel'] or '') != (cb.get('channel') or ''):
                    bound = con.execute('SELECT DISTINCT account FROM line_users WHERE customer_id=? AND is_friend=1', (p.get('id') or '',)).fetchall()
                    if any(r[0] != nb for r in bound):
                        raise ValueError(f"這位客戶已經綁定「{brands.name(bound[0][0])}」的官方 LINE，品牌不能直接改成「{brands.name(nb)}」；請先解除綁定再改。")
                    p['line_account'] = nb
            crew_msg = ''
            if route[2] == 'orders' and before and p.get('required_workers') not in (None, ''):      # 人數比報價少 → 先問店長
                import crewguard
                ask_, qp_, old_ = crewguard.needs_ask(con, before['id'], int(p['required_workers']), user)
                if ask_:
                    crew_msg = crewguard.ask(con, before['id'], int(p.pop('required_workers')), qp_, old_, user['name'])
            out = core.save(con, route[2], p)
            if crew_msg:
                out = {**dict(out), 'crew_ask': crew_msg}
            if route[2] == 'orders':
                alerts.change(con, before, out, user['name'])
                if before and before.get('scope_hold') and 'scope' in p and (p['scope'] or '') != (before['scope'] or ''):
                    import scopereview; scopereview.approve(con, out['id'], user['name'])      # 管理層在中控台改了範圍＝看過了（備註夥伴看不到，改備註不算）
            if route[2] == 'workers' and w_before and w_before['status'] == 'active' and out['status'] != 'active':
                import leave; leave.worker_unavailable(con, out['id'], '已暫停')          # 暫停的夥伴：手上的單另外處理
            if route[2] == 'sites' and s_before:
                import leave; leave.site_changed(con, s_before, out)          # 地址、進場方式改了 → 通知接下來有排的夥伴
            if route[2] == 'assignments' and out['status'] == 'accepted':
                if not a_before or a_before['status'] != 'accepted':
                    line_bot.on_assigned_worker(con, out['id'])      # 後台直接排入／選他 → 通知那位夥伴
                dispatch.after_accept(con, out['order_id'], reviewed=True)      # 後台直接指派＝管理層選的人，視為已審核
            elif route[2] == 'assignments' and a_before and a_before['status'] == 'accepted' and out['status'] in ('cancelled', 'declined'):
                line_bot.on_unassigned(con, out['id'], user['name'])     # 後台取消此人 → 通知夥伴、提醒缺人
            return out
        if route[:2] == ['api', 'workflow'] and len(route) == 3:
            if route[2] in money_flows: need(user, 'money')
            return workflow.command(con, route[2], p)
        if r == 'api/pairing/pair':             # 中控台 LINE 配對
            import pairing; need(user, 'ops'); return {'message': pairing.pair(con, p['acc'], p['uid'], p['kind'], p['id'], user['name'] + '（中控台）')}
        if r == 'api/pairing/skip':
            import pairing; need(user, 'ops'); return {'message': pairing.skip(con, p['acc'], p['uid'], p.get('v', 'no'), user['name'] + '（中控台）')}
        if r == 'api/customer/tier':                # 客戶 A／B／C（店長手動設）
            need(user, 'ops'); import tiers as _tr
            t = str(p.get('tier', '')).upper(); name = _tr.set_cust_tier(con, str(p.get('id', '')), t, user['name'] + '（中控台）')
            return {'message': f"✅ {name} → {t} 客（新單會照客戶等級決定先問哪一級夥伴）"}
        if r == 'api/order/people':                 # 減少人數（拿掉的不補人）
            need(user, 'ops'); import leave as _lv
            return {'message': _lv.set_people(con, str(p.get('id', '')), int(p.get('n') or 0), user['name'] + '（中控台）', [str(x) for x in (p.get('drop') or [])],
                                              boss=user.get('role') == 'admin' or '店長' in (user.get('name') or ''))}
        if r == 'api/order/hold':                   # 找人中，先不要自動派
            need(user, 'ops'); import dispatch as _dp
            return {'message': _dp.hold(con, str(p.get('id', '')), user['name'] + '（中控台）', bool(p.get('on')))}
        if r == 'api/order/urgent-add':             # 緊急加派一位（照分級、先按先得）
            need(user, 'ops'); import urgent as _ug; core.CTX.actor = user['name']
            return {'message': _ug.add_one(con, str(p.get('id', '')), user['name'], str(p.get('why') or '緊急加派'))}
        if r == 'api/daidiu/stop':                  # 垃圾代丟：管理層停止這位業主的月方案
            need(user, 'ops'); import daidiu as _dd; core.CTX.actor = user['name']
            return {'message': _dd.stop_api(con, str(p.get('id', '')), user['name'], str(p.get('reason') or '管理層停止'))}
        if r == 'api/site/delete':                  # 沒有工單的地點 → 回收桶
            need(user, 'ops'); import trash as _tr
            c0 = core.get(con, 'sites', p['id']); core.CTX.actor = user['name']
            _tr.to_trash(con, 'sites', p['id'], f"{user['name']} 在客戶頁刪除"); return {'message': f"已刪除「{c0['name']}」（在回收桶，可以還原）"}
        if r == 'api/site/merge':                   # 有工單的重複地點 → 合併到正確的地點
            need(user, 'ops'); core.CTX.actor = user['name']
            return {'message': board.merge_sites(con, str(p.get('target', '')), str(p.get('id', '')), user['name'])}
        if r == 'api/assignment/uninvite':          # 取消邀請（邀請中／報名中）
            need(user, 'ops'); import dispatch as _dp          # 別名：函式裡 import 同名模組會讓整個 route 的 dispatch 變區域變數（教訓）
            return {'message': _dp.uninvite(con, str(p.get('id', '')), user['name'] + '（中控台）')}
        if r == 'api/worker/tier':                  # 中控台改派工分級（以前只能 LINE 打「派工分級」）
            need(user, 'ops'); import tiers
            name = tiers.set_tier(con, str(p.get('id', '')), str(p.get('tier', '')).upper(), user['name'] + '（中控台）')
            return {'message': f"✅ {name} → {str(p.get('tier', '')).upper()} 級（新單會照這個順序問）"}
        if r.startswith('api/ledger/'):             # 記帳（看得到金額的人；金鑰只有管理員）
            need(user, 'money'); who = user['name'] + '（中控台）'
            if r == 'api/ledger/save':
                e = ledger.save(con, p, who); return {'entry': e, 'message': f"✅ 已記帳 NT$ {e['amount'] // 100:,}"}
            if r == 'api/ledger/confirm':
                e = ledger.confirm(con, str(p.get('id', '')), who); return {'entry': e, 'message': '✅ 已確認記帳'}
            if r == 'api/ledger/void':
                ledger.void(con, str(p.get('id', '')), who); return {'message': '已作廢（不算進帳）'}
            if r == 'api/ledger/taxid':
                return {'message': ledger.set_tax_id(con, str(p.get('tax_id', '')), who)}
            if r == 'api/ledger/reread':
                ledger.reread(con, str(p.get('id', '')), who); return {'message': '重新讀取中，約 10 秒後會更新'}
            if r == 'api/ledger/upload':
                ids = [ledger.add_photo(con, ledger.store_jpeg(bytes(j)), who, 'upload', brand=str(p.get('brand', ''))) for j in (p.get('_jpegs') or [])]
                if not ids:
                    raise ValueError('沒有收到照片')
                reading = [i for i in ids if ledger.get(con, i)['status'] == 'reading']
                ledger.kick(reading)
                return {'ids': ids, 'message': f'收到 {len(ids)} 張，' + ('讀取中（約 10 秒），讀完在「待確認」按確認' if reading else '還沒設定讀取金鑰，請在「待確認」點每一筆補金額')}
            need(user, 'admin')
            if r == 'api/ledger/key':
                return {'message': ledger.set_key(con, str(p.get('key', '')), user['name'])}
            if r == 'api/ledger/key/clear':
                return {'message': ledger.clear_key(con, user['name'])}
            if r == 'api/ledger/key/test':
                ok, msg = (list(p.get('_check') or []) + [False, '沒有測試結果'])[:2]
                if ok:
                    ix.set_setting(con, 'anthropic_key_checked', core.now()[:16])
                return {'ok': bool(ok), 'message': msg}
            raise ValueError('不支援的記帳操作')
        if r == 'api/materials/upload':             # 素材在中控台直接上傳（以前只能 LINE「我要傳」一對一傳）
            need(user, 'ops'); import materials
            msgs = [materials.add(con, str(p.get('key', '')), bytes(j), user['name'] + '（中控台）')[0] for j in (p.get('_jpegs') or [])]
            if not msgs:
                raise ValueError('沒有收到照片')
            return {'message': msgs[-1]}
        if r == 'api/quote/confirm':                # 已跟客戶談好 → 代為確認、直接成立（看得到金額的人）
            need(user, 'money'); return booking.admin_confirm(con, p, user['name'])
        if r == 'api/order/photo-redo':             # 工單「請夥伴補拍」（跟 LINE 同一個後端）
            need(user, 'ops'); return {'message': line_bot.photo_redo(con, str(p.get('id', '')), user['name'] + '（中控台）')}
        if r == 'api/order/worker-note':            # 工單「補充提醒夥伴」（明日工作卡一起給；已經發過就另外推）
            need(user, 'ops'); import prep
            if not str(p.get('text', '')).strip():
                raise ValueError('請寫要提醒夥伴的事')
            prep.add_note(con, str(p.get('id', '')), str(p.get('text', '')), user['name'] + '（中控台）'); return {'message': '✅ 已加進給夥伴的提醒'}
        if r == 'api/interview/reinvite':           # 面試「請他重新選時間」
            need(user, 'ops'); import interviews; return {'message': interviews.reinvite(con, str(p.get('id', '')), user['name'] + '（中控台）')}
        if r == 'api/pairing/none-worker':          # LINE 配對「依人找」夥伴：他沒加官方 LINE（14 天內不再列）
            need(user, 'ops'); import pairing; pairing.mark_worker_none(con, str(p.get('id', '')), user['name'] + '（中控台）'); return {'message': '已記下：14 天內不再列'}
        if r == 'api/patrol/act':                   # 海巡留言全部要審過才發（更多 › 行銷 › 海巡留言待審）
            need(user, 'ops'); import mkpatrol
            n = mkpatrol.act(p.get('ids') or p.get('id'), str(p.get('v', '')), user['name'] + '（中控台）', p.get('text'))
            core.audit(con, 'patrol_' + str(p.get('v', '')), 'outreach_log', str(p.get('ids') or p.get('id'))[:200], actor=user['name'])
            return {'message': (f'✅ 已核准 {n} 則，會分散發出' if p.get('v') == 'ok' else f'已略過 {n} 則')}
        if r == 'api/mk/upload':                     # 行銷素材照＋參考文字／企劃
            need(user, 'ops'); import mkinbox
            return mkinbox.save(con, p, user['name'])
        if r == 'api/tdraft/act':                   # 5100 後台審核全面搬回各品牌中控 → 示範服務店 Threads 草稿在這審
            need(user, 'ops'); import mkpatrol
            m = mkpatrol.draft_act(p.get('id'), str(p.get('v', '')), user['name'] + '（中控台）', p.get('text'))
            core.audit(con, 'tdraft_' + str(p.get('v', '')), 'self_post_draft', str(p.get('id'))[:40], actor=user['name'])
            return {'message': m or '這篇已經處理過了'}
        if r == 'api/staffq/act':                   # 管理群卡片在中控台處理（跟 LINE 同一個後端函式）
            need(user, 'ops'); import staffq
            return {'message': staffq.act(con, str(p.get('kind', '')), str(p.get('id', '')), str(p.get('v', '')), user['name'] + '（中控台）', str(p.get('note', '')), 'money' in acc.CAN[user['role']])}
        if r == 'api/ask/answer':                   # LINE 的東西都要能在中控台處理 → 題目卡在中控台回答
            need(user, 'ops'); import decisions; return {'message': decisions.answer_from_web(con, p.get('topic'), p.get('answer'), user['name']) or '已記錄'}
        if r == 'api/worker/bind-sent':             # 「已傳給他了」→ 每天 10:00 不再列他
            need(user, 'ops'); return {'message': line_bot.mark_wbind_sent(con, str(p.get('id', '')), user['name'] + '（中控台）')}
        if r == 'api/worker/bind-code':             # 中控台拿夥伴綁定訊息（配對不推播後，管理群不再收名單卡）
            need(user, 'ops'); m = line_bot.worker_bind_invite(con, str(p.get('id', '')), str(p.get('tpl', '')))
            return {'text': m[-1]['text'], 'linked': len(m) == 1}
        if r == 'api/pairing/none':                 # 他沒加官方 LINE（記下來，每天 10:00 列進打電話清單；14 天後再列）
            import pairing; need(user, 'ops'); return {'message': pairing.mark_none(con, p['id'], user['name'] + '（中控台）')}
        if r == 'api/pairing/unpair':
            import pairing; need(user, 'ops'); return {'message': pairing.unpair(con, p['acc'], p['uid'], user['name'] + '（中控台）')}
        if r in ('api/import/preview', 'api/import/commit'):      # 試算表／檔案匯入（客戶、夥伴、工單）
            need(user, 'money' if str(p.get('kind', '')) == 'prices' else 'ops'); import bulkimport      # 報價品項價格：要看得到金額的人
            rows_ = bulkimport.from_payload(p)
            if r == 'api/import/preview':
                return {'rows': bulkimport.plan(con, str(p.get('kind', '')), rows_)}
            return bulkimport.run(con, str(p.get('kind', '')), rows_, user['name'])
        if r == 'api/site/tools':               # 管理層直接設定案場工具（舊客戶不問業主）
            need(user, 'ops'); import sitetools
            have = [x for x in (p.get('have') or []) if isinstance(x, str)][:12]
            note = ('現場有：' + '、'.join(have) if have else '') + (('；' if have and str(p.get('note') or '').strip() else '') + str(p.get('note') or '').strip())
            sitetools.set_tools(con, str(p.get('site_id', '')), str(p.get('tools', '')), note, user['name'] + '（中控台）')
            return {'message': '已記下工具（夥伴卡會照這個寫，不會再問業主）'}
        if r == 'api/order/tools-ask':          # 用 LINE 問業主要不要自備工具
            import sitetools
            return {'message': '已用 LINE 問業主，回覆後會記在案場' if sitetools.ask(con, p['id']) else '業主還沒綁 LINE，請打電話問，再到案場資料填「現場工具」'}
        if r == 'api/order/scope-ok':           # 詳細需求審核通過，給夥伴看
            import scopereview; return {'message': scopereview.approve(con, p['id'], user['name'])}
        if r == 'api/photo': return photos.upload(con, p)
        if r == 'api/site/photo':                   # 案場須知照片（鑰匙、備品櫃位置…）
            need(user, 'ops'); import siteinfo, base64 as _b64
            raw_ = _b64.b64decode(str(p.get('content', '')).split(',')[-1] or b'')
            return {'id': siteinfo.add_photo(con, str(p.get('site_id', '')), raw_, 'image/png' if raw_[:4] == b'\x89PNG' else 'image/jpeg', str(p.get('caption', '')), user['name'])}
        if r == 'api/order-media/del':
            need(user, 'ops'); import siteinfo; siteinfo.ensure_media(con)
            con.execute('DELETE FROM order_media WHERE id=?', (str(p.get('id', '')),)); return {'message': '已刪除'}
        if r == 'api/site/routine':             # 客戶01：定期特殊服務（每月／每兩月…）
            need(user, 'ops'); import siteroutine
            siteroutine.save(con, p, user['name']); return {'message': '已儲存（這一期還沒做的，派工卡、提醒卡會帶）'}
        if r == 'api/site/routine-del':
            need(user, 'ops'); import siteroutine
            siteroutine.delete(con, str(p.get('id', '')), user['name']); return {'message': '已刪除'}
        if r == 'api/site/photo-del':
            need(user, 'ops'); import siteinfo
            siteinfo.del_photo(con, str(p.get('id', '')), user['name']); return {'message': '已刪除'}
        if r == 'api/photo/delete':                 # 重複、拍錯的照片要能刪
            need(user, 'admin'); return {'message': photos.delete(con, str(p.get('id', '')), user['name'])}
        if route[:2] == ['api', 'order'] and len(route) == 4:
            if route[3] == 'approve-charge': need(user, 'money')
            before = core.get(con, 'orders', route[2])
            out = core.transition(con, route[2], route[3], p)
            board.after_transition(con, before, out, route[3], user['name'])
            return out
        if r == 'api/customer/bind-generic':   # 通用綁定訊息（一條可以傳給很多客戶；客戶專屬的那條只能給那一位）
            need(user, 'ops'); return booking.generic_bind(con, p.get('brand') if brands.valid(p.get('brand') or '') else 'demoshop')
        if r == 'api/customer/bind-code':      # 客戶專屬 LINE 綁定訊息（店長一對一貼給客戶，不算推播額度）
            cu = core.get(con, 'customers', p['id'])
            a = cu.get('line_account') if brands.valid(cu.get('line_account')) else brands.of_channel(con, cu.get('channel'))
            if not line_bot.basic_id(con, a):      # 品牌分清楚：各品牌的客戶要用自己的官方帳號綁，還沒接上就先不給連結
                raise ValueError(f"{brands.name(a)} 的官方 LINE 還沒接上系統，這位客戶先不能綁定（不會讓他綁到別的品牌）。")
            b = line_bot.bind_code(con, 'customer', cu['id'], a, days=14)
            return {**b, 'linked': bool(con.execute('SELECT 1 FROM line_users WHERE customer_id=?', (cu['id'],)).fetchone()),
                    'brand': a, 'text': booking.bind_message(con, a, cu['name'], b['url'], b['code'], b['add'], p.get('kind') == 'new'),
                    'guide': f'{line_bot.BASE_URL}/bind-guide-{a}.png'}      # 新／舊客戶兩款＋引導圖
        if r.startswith('api/brand/'): need(user, 'admin')        # 品牌、LINE 金鑰、圖文選單：只限管理員
        if r == 'api/brand/save': return brands.save(con, p, user['name'])
        if r == 'api/brand/line-save': return brands.save_line(con, p, user['name'])
        if r == 'api/brand/line': return brands.connect_line(con, p.get('id'), p.get('secret'), p.get('token'), user['name'])
        if r == 'api/brand/menu':
            import tools_richmenu
            return tools_richmenu.cancel_default(con, p.get('id')) if p.get('cancel') else tools_richmenu.create(con, p.get('id'), set_default=bool(p.get('set_default')))
        if r == 'api/bind-message':             # 店長改打招呼訊息（每個品牌一份；空白＝恢復預設）
            need(user, 'admin')
            for k in [b['id'] for b in brands.all_(con, active_only=False)]:
                if k in p:
                    t = str(p[k] or '').strip()[:2000]
                    if t and '{url}' not in t: raise ValueError('訊息裡要保留 {url}（綁定連結會放在那裡）')
                    ix.set_setting(con, f'bind_message_{k}', t)
            core.audit(con, 'bind_message', 'settings', 'bind_message'); return {'saved': True}
        if r == 'api/bind-request/decide':      # 中控台確認／拒絕 LINE 綁定（和派單管理群的按鈕同一份資料）
            need(user, 'ops'); return {'message': line_bot.decide_bind(con, str(p.get('id', '')), bool(p.get('ok')), user['name'])}
        if r == 'api/quote/create' and p.get('customer_id') is not None and not str(p.get('customer_id')).startswith('cus-'):
            p.pop('customer_id', None)
        if r == 'api/quote/create':
            need(user, 'ops')
            if 'money' not in acc.CAN.get(user['role'], set()):        # 客服開單：品項照填，金額留給店長
                p['items'] = [{**it, 'amount': 0} for it in (p.get('items') or [])]
            return booking.admin_create(con, p, user['name'])
        if r in ('api/quote/save', 'api/quote/ask') and 'money' not in acc.CAN.get(user['role'], set()):
            q0 = booking._q(con, qid=p.get('id', '')); old = booking._items(q0)     # 沒有金額權限：品項數、金額、另計、稅都沿用原本的（由店長決定）
            new = p.get('items') or []
            if len(new) != len(old):
                raise PermissionError('新增或刪除品項會影響金額，請交給店長處理（可以在「交代」寫要加減什麼）')
            p['items'] = [{**it, **{k: old[i].get(k) for k in ('amount', 'per_unit', 'separate')}} for i, it in enumerate(new)]
            p['taxed'] = q0['taxed']
        if r == 'api/quote/ask': need(user, 'ops'); booking.admin_save(con, p, user['name']); return booking.ask_review(con, p['id'], user['name'], str(p.get('msg', '')))
        if r == 'api/quote/send': need(user, 'money')      # 確認並傳給客戶：只有店長（與管理員）
        if r == 'api/quote/save': need(user, 'ops'); return booking.admin_save(con, p, user['name'])
        if r == 'api/quote/send': need(user, 'ops'); return booking.admin_send(con, p, user['name'])
        if r == 'api/quote/stage': need(user, 'ops'); return booking.admin_stage(con, p)
        if r == 'api/quote/more': need(user, 'ops'); return booking.admin_more(con, p, user['name'])
        if r == 'api/order/quick-complete': return board.quick_complete(con, p)
        if r == 'api/order/set-times':                 # 管理層手動改到場／離場打卡時間（要寫原因）
            return board.set_times(con, p.get('id', ''), p.get('arrived', ''), p.get('left', ''), p.get('pause') or 0, p.get('note', ''), user['name'] + '（中控台）')
        if r == 'api/dispatch/publish': return board.publish(con, p)
        if r == 'api/dispatch/auto': return dispatch.auto_dispatch(con, {**p, 'unpaid': bool(p.get('unpaid')) and 'money' in acc.CAN.get(user['role'], set())})      # 不支薪只有看得到金額的人能設
        if r == 'api/availability': return dispatch.set_availability(con, p['worker_id'], p)
        if r == 'api/inquiry/convert': return dispatch.convert_inquiry(con, p)
        if r == 'api/inquiry/status': return dispatch.inquiry_status(con, p)
        if r == 'api/dispatch/unpublish': return board.publish(con, p, open_=False)
        if r == 'api/orders/recurring':
            if 'money' not in acc.CAN.get(user['role'], set()):
                for k in ('rate', 'worker_rate', 'billing_mode', 'plan_id'): p.pop(k, None)
            return board.recurring(con, p)
        if r == 'api/orders/bulk':
            return board.bulk(con, p)
        if r == 'api/issue/resolve':
            if not p.get('resolution', '').strip(): raise ValueError('請填核對結果')
            before = core.get(con, 'issues', p['id'])
            con.execute("UPDATE issues SET status='resolved',resolution=? WHERE id=?", (p['resolution'], p['id']))
            core.audit(con, 'resolve', 'issues', p['id'], before, {'resolution': p['resolution']}); return {'saved': True}
        if r == 'api/triage/decide': return trash.decide(con, p)
        if r == 'api/shop/product':                 # 商城：價格、夥伴價、上下架（管理員）
            need(user, 'admin'); import shop
            return shop.set_product(con, p['id'], p, user['name'])
        if r == 'api/shop/stock':                   # 商城庫存：入庫／團隊領用／盤點調整（管理員）
            need(user, 'admin')
            import shop
            kind = p.get('kind')
            if kind not in ('in', 'use', 'adjust'):
                raise ValueError('庫存異動種類不對')
            n = int(p.get('qty') or 0)
            if n == 0 or (kind != 'adjust' and n < 0):
                raise ValueError('請填數量')
            return {'stock': shop.stock_move(con, p.get('id', ''), -n if kind == 'use' else n, kind, str(p.get('ref') or ''), str(p.get('note') or ''), user['name'])}
        if r == 'api/shop/image':                   # 商城商品照片（管理員）
            need(user, 'admin')
            import shop, base64
            head, _, b64 = str(p.get('content', '')).partition(',')
            mime = (re.match(r'data:([\w/+-]+);base64', head) or [None, 'image/jpeg'])[1]
            return shop.set_image(con, p.get('id', ''), base64.b64decode(b64 or head), mime, user['name'])
        if r == 'api/shop/status':                  # 商城訂單：已交付／取消
            import shop
            return {'message': shop.set_status(con, p['id'], p['status'], user['name'])}
        if r == 'api/quote/link-sent':              # 沒綁 LINE 的客戶：同事已經把報價單連結傳給他了
            con.execute("UPDATE quotes SET link_sent=? WHERE id=?", (f"{core.now()[:16]}（{user['name']}）", p['id']))
            core.audit(con, 'quote_link_sent', 'quotes', p['id'], after={'by': user['name']}); return {'ok': True}
        if r == 'api/interview/decide':             # 夥伴面試：評分表＋結果
            import interviews
            return {'result': interviews.decide(con, p['id'], p['result'], user['name'], p.get('scores') or {}, str(p.get('skills', '')), str(p.get('note', '')))}
        if r == 'api/interview/invite':             # 幫舊夥伴／電話應徵的人約面試
            import interviews
            return {'id': interviews.invite(con, p['worker_id'])}
        if r == 'api/recruit/advance':              # 招募關卡：完成這關 → 下一關（實作 → 訓練 → 試派案 → 正式）
            import recruit; return {'message': recruit.advance(con, p['id'], user['name'])}
        if r == 'api/recruit/police':               # 良民證：已收規費／已送件／通過／請重傳
            import recruit; return {'message': recruit.police_mark(con, p['id'], p['what'], user['name'], str(p.get('how', '')))}
        if r == 'api/recruit/upload':               # 代辦拿到的良民證：管理員拍照存進來
            need(user, 'admin'); import recruit, base64, interviews
            iv0 = interviews.get(con, p['id'])
            fid = recruit.save_file(con, 'police', base64.b64decode(str(p.get('content', '')).split(',', 1)[-1]), iv0['worker_id'], p['id'], user['name'], '代辦拿到')
            recruit._set_pol(con, p['id'], file=fid); return {'ok': True}
        if r == 'api/interview/more':               # 應徵資料不夠：請他補填完整應徵表（傳 LINE 連結）
            import interviews
            return {'message': interviews.ask_full_app(con, p.get('id', ''), user['name'])}
        if r == 'api/interview/screen':             # 應徵資料：OK 邀請電話面試／婉拒
            import interviews
            return {'message': interviews.screen_ok(con, p['id'], user['name'], str(p.get('slot') or ''), bool(p.get('self_pick'))) if p.get('ok') else interviews.reject(con, p['id'], user['name'], str(p.get('note', '')))}
        if r == 'api/interview/undo-reject':        # 婉拒按錯要能改回
            need(user, 'ops'); import interviews
            return {'message': interviews.undo_reject(con, p['id'], user['name'])}
        if r == 'api/interview/slot':               # 電話約好的時間，直接填
            import interviews
            return {'message': interviews.confirm(con, p['id'], str(p['slot'])[:16], user['name'])}      # 主管挑時間＝直接排定
        if r == 'api/issue/report':                 # 功能回報：同事回報問題／想要的功能
            import sysreport as issues
            rid = issues.report(con, p.get('title', ''), p.get('detail', ''), user['name'], 'staff', p.get('kind', 'bug'), str(p.get('page', ''))[:80])
            n = issues.add_files(con, rid, p.get('_jpegs'), user['name'])
            return {'id': rid, 'files': n}
        if r == 'api/issue/js':                     # 中控台程式錯誤（自動）
            import sysreport as issues, re as _re
            if _re.search(r'Load failed|Failed to fetch|NetworkError|network', str(p.get('msg', '')), _re.I):
                return {'id': '', 'skipped': '斷線不是程式錯誤'}          # 每日回報 系統重啟、手機沒訊號
            return {'id': issues.auto(con, f"中控台 {str(p.get('page', ''))[:30]}", str(p.get('msg', ''))[:300], str(p.get('stack', ''))[:1500])}
        if r == 'api/issue/status':
            need(user, 'admin'); import sysreport as issues
            issues.set_status(con, p['id'], p['status'], str(p.get('note', ''))); return {'ok': True}
        if r == 'api/testimonial/replied':          # 客戶見證：已到官方 LINE 親自回覆
            import testimonials; return {'message': testimonials.mark_replied(con, p['id'], user['name'])}
        if r == 'api/triage/flag':
            trash.flag(con, p['entity'], p['ref_id'], p.get('grp') or '同事轉交', p.get('reason') or f"{user['name']} 轉交處理", p.get('suggestion', '')); return {'saved': True}
        if r == 'api/payment/confirm':            # 店長在中控台確認收款（金額可改，以她為準）
            need(user, 'money'); import payments
            st = con.execute('SELECT status,confirmed_by FROM payment_requests WHERE id=?', (str(p.get('id', '')),)).fetchone()
            if st and st[0] in ('confirmed', 'cancelled'):            # 稽核：兩人先後按 → 客戶收到兩次「已收到款項」
                raise ValueError(f"這筆已經確認過了（{st[1]}）" if st[0] == 'confirmed' else '這筆款項已取消（工單取消）')
            row = payments.confirm(con, p['id'], user['name'], p.get('amount'), str(p.get('note', ''))); line_bot.after_payment_confirmed(con, p['id']); return row
        if r == 'api/payment/edit':
            need(user, 'money'); import payments
            payments.edit(con, p['id'], user['name'], p.get('amount'), p.get('due'), str(p.get('note', ''))); return {'saved': True}
        if r == 'api/linepay/test':                 # LINE Pay 連線測試（1 元請求，不扣款）
            need(user, 'admin'); import linepay
            return {'message': linepay.test_connection(con, str(p.get('brand', '')))}
        if r == 'api/shop/paid':                    # 商城訂單：現場收款／匯款確認
            need(user, 'money'); import shop
            shop.paid(con, p['id'], str(p.get('how') or '現場／匯款'), user['name']); return {'ok': True}
        if r in ('api/linepay/settings', 'api/linepay/refund'):     # LINE Pay：金鑰由負責人自己貼；退款（只有看得到金額的人）
            need(user, 'money'); import linepay
            if r == 'api/linepay/settings':
                need(user, 'admin'); return linepay.save_keys(con, p, user['name'])
            return linepay.refund(con, p['key'], p.get('amount'), user['name'])
        if r in ('api/vendor/save', 'api/vendor-cost/save', 'api/vendor-cost/delete', 'api/vendor-cost/paid'):     # 配合廠商（只有看得到金額的人）
            need(user, 'money'); import vendors
            if r == 'api/vendor/save': return vendors.save_vendor(con, p, user['name'])
            if r == 'api/vendor-cost/save': return vendors.save_cost(con, p, user['name'])
            if r == 'api/vendor-cost/delete': return vendors.delete_cost(con, p['id'], user['name'])
            return vendors.mark_paid(con, list(p.get('ids') or []), p.get('ref', ''), user['name'])
        if r == 'api/worker/blacklist':          # 黑名單：店長、客服（管理層）都可以設
            automation.set_blacklist(con, p['id'], bool(p.get('on')), user['name'], str(p.get('reason', ''))); return {'saved': True}
        if r == 'api/inquiry/delete':            # 測試／無效的預約需求 → 移到回收桶（可還原）；已轉工單的不能刪
            q = core.get(con, 'inquiries', p['id'])
            if q['order_id']: raise ValueError('這筆已經轉成工單，請改用「未成交」或到工單處理')
            con.execute("UPDATE quotes SET status='cancelled',stage='cancelled' WHERE inquiry_id=? AND status='draft'", (q['id'],))
            return trash.to_trash(con, 'inquiries', q['id'], str(p.get('reason') or '測試／無效需求'))
        if r == 'api/trash/restore': return trash.restore(con, p['id'])
        if r == 'api/trash/purge':
            need(user, 'admin'); return trash.purge(con, p['id'])
        if r == 'api/merge':
            need(user, 'admin')            # 稽核：合併會搬走帳單、刪掉一筆，只有管理員
            if p.get('table') == 'customers':
                r_ = board.merge_customers(con, p['keep'], p['drop']); line_bot.after_customer_linked(con, p['keep']); return r_      # 稽核 A1：合併後補發待付款通知
            if p.get('table') == 'workers': return board.merge_workers(con, p['keep'], p['drop'])
            raise ValueError('合併類型不符')
        if r == 'api/alias':
            core.get(con, 'customers', p['customer_id'])
            if not str(p.get('alias', '')).strip(): raise ValueError('請填別名')
            con.execute('INSERT INTO customer_aliases(alias,customer_id,site_id) VALUES(?,?,?) ON CONFLICT(alias) DO UPDATE SET customer_id=excluded.customer_id,site_id=excluded.site_id',
                        (p['alias'].strip(), p['customer_id'], p.get('site_id') or None))
            core.audit(con, 'alias', 'customers', p['customer_id'], after={'alias': p['alias']}); return {'saved': True}
        # 帳務
        if r in ('api/invoices', 'api/settlements', 'api/billing/bill') or route[:2] in (['api', 'invoice'], ['api', 'settlement']):
            need(user, 'money')
            if r == 'api/invoices': return core.create_invoice(con, p)
            if r == 'api/billing/bill': return board.bill_month(con, p)
            if r == 'api/settlements': return core.create_settlement(con, p)
            if route[:2] == ['api', 'invoice'] and len(route) == 4: return core.invoice_action(con, route[2], route[3], p)
            if route[:2] == ['api', 'settlement'] and len(route) == 4 and route[3] == 'paid':
                import payout
                return payout.mark_paid(con, route[2], p.get('reference', ''), user['name'], bool(p.get('silent')))
            return None
        # 管理員
        need(user, 'admin')
        if r == 'api/users': return acc.save_user(con, p)
        if r == 'api/settings':
            ix.set_setting(con, 'bank_info', str(p.get('bank_info', ''))[:500]); core.audit(con, 'settings', 'settings', 'bank_info'); return {'saved': True}
        if r == 'api/calendar/key':
            raise ValueError('Google 日曆同步已停用（通知一律用 LINE 官方）')
        if r == 'api/google/config':          # 雲端搬本機：載入 Google OAuth 用戶端 JSON（桌面應用程式）
            return ix.configure_google(con, p)
        if r == 'api/google/connect':         # 開 Google 授權頁（只要求「本程式建立的日曆」權限）；要在這台電腦 http://127.0.0.1:4781 按
            return ix.oauth_start(con, user['token_hash'])
        if r == 'api/google/calendar':        # 開／關自動同步（每 10 分鐘，只同步 7 天前以後的已排定／已完工工單，不含姓名、地址、金額）
            on = bool(p.get('on'))
            if on:
                ix.create_calendar(con)
            ix.set_setting(con, 'google_calendar_sync', 'on' if on else 'off'); core.audit(con, 'settings', 'settings', 'google_calendar_sync', after={'on': on})
            return {'saved': True, 'on': on}
        if r == 'api/google/sync':
            return ix.sync_calendar(con, since=ix.sync_since())
        if r == 'api/conversation-note':
            core.get(con, 'line_accounts', p['line_account_id'])
            if not p.get('source') or not p.get('note'): raise ValueError('請填來源與對話摘要')
            key = core.uid('note')
            con.execute('INSERT INTO conversation_notes(id,customer_id,line_account_id,source,note) VALUES(?,?,?,?,?)',
                        (key, p.get('customer_id') or None, p['line_account_id'], p['source'], p['note']))
            core.audit(con, 'create', 'conversation_notes', key); return {'saved': True}
        if r == 'api/line/verify': return ix.verify_line(con, p['account_id'])
        if r == 'api/line/settings':
            if p.get('mode') not in ('off', 'test', 'on'): raise ValueError('模式不符')
            ix.set_setting(con, 'line_mode', p['mode'])
            for bid, m in (p.get('modes') or {}).items():          # 每個品牌各自的模式
                if brands.valid(bid) and m in ('off', 'test', 'on'):
                    ix.set_setting(con, f'line_mode_{bid}', m)
            ix.set_setting(con, 'line_test_users', json.dumps([u for u in p.get('test_users', []) if isinstance(u, str) and u.startswith('U')][:20]))
            ix.set_setting(con, 'line_dispatch_group', p.get('group') or '')
            core.audit(con, 'line_settings', 'settings', 'line', after={'mode': p['mode'], 'group': p.get('group')}); return {'saved': True}
        if r == 'api/line/classify':
            line_bot.classify_all(con); return {'groups': core.rows(con, 'SELECT * FROM line_groups')}
        if r == 'api/line/staff':
            ix.set_setting(con, 'line_staff_users', json.dumps([u for u in p.get('users', []) if isinstance(u, str) and u.startswith('U')][:30]))
            con.execute('UPDATE line_users SET is_staff=0'); line_bot.link_staff_menu(con); return {'saved': True}
        if r == 'api/line/group-role':
            if p.get('role') not in ('admin', 'pool', 'worker', 'job'): raise ValueError('角色不符')
            con.execute('UPDATE line_groups SET role=?,worker_id=? WHERE group_id=?', (p['role'], p.get('worker_id') or None, p['group_id']))
            if p['role'] == 'admin': ix.set_setting(con, 'line_admin_group', p['group_id'])
            elif ix.setting(con, 'line_admin_group') == p['group_id']: ix.set_setting(con, 'line_admin_group', '')
            core.audit(con, 'line_group_role', 'line_groups', p['group_id'], after=p)
            return {'saved': True}
        if r == 'api/dispatch-mode':
            need(user, 'admin')
            if p.get('mode') not in ('manual', 'auto'): raise ValueError('模式不符')
            ix.set_setting(con, 'dispatch_mode', p['mode']); return {'saved': True}
        if r == 'api/pricing':
            cfg = {}
            for k, d in pricing.DEFAULT.items():
                if k not in p: continue
                if isinstance(d, dict):
                    cfg[k] = {str(n).strip()[:20]: int(v) for n, v in (p[k] or {}).items() if str(n).strip() and 0 <= int(v) <= 100000}
                elif isinstance(d, list):
                    cfg[k] = [str(x).strip()[:20] for x in p[k] if str(x).strip()]
                else:
                    v = float(p[k])
                    if v < 0 or v > 1_000_000: raise ValueError(f'{k} 數字不合理')
                    cfg[k] = int(v) if float(d).is_integer() and v.is_integer() else v
            pk = pricing.key('pricing', brands.pick(p.get('brand')))
            ix.set_setting(con, pk, json.dumps(cfg, ensure_ascii=False)); core.audit(con, 'pricing', 'settings', pk, after=cfg); return {'saved': True}
        if r == 'api/scopes':
            sc = {str(k)[:20]: [str(x).strip()[:80] for x in v if str(x).strip()][:12] for k, v in (p.get('scopes') or {}).items()}
            sk = pricing.key('scopes', brands.pick(p.get('brand')))
            ix.set_setting(con, sk, json.dumps(sc, ensure_ascii=False)); core.audit(con, 'scopes', 'settings', sk, after=sc); return {'saved': True}
        if r == 'api/stamp':      # 公司報價章（PNG，透明背景最好）：店長確認後的報價單才會蓋上
            img = str(p.get('image', ''))
            if img and (not img.startswith('data:image/png;base64,') or len(img) > 400_000): raise ValueError('請用 PNG 圖檔（300KB 以內）')
            stk = pricing.key('company_stamp', brands.pick(p.get('brand')))
            ix.set_setting(con, stk, img); core.audit(con, 'stamp', 'settings', stk, after={'bytes': len(img)}); return {'saved': True}
        if r == 'api/company':
            co = {k: str(p.get(k, '')).strip()[:120] for k in booking.COMPANY if k in p}
            ck = pricing.key('company', brands.pick(p.get('brand')))
            ix.set_setting(con, ck, json.dumps(co, ensure_ascii=False)); core.audit(con, 'company', 'settings', ck, after=co); return {'saved': True}
        if r == 'api/line/link':
            accs = [x[0] for x in con.execute('SELECT account FROM line_users WHERE user_id=?', (p['user_id'],))]
            if not accs: raise ValueError('找不到這個 LINE 帳號')
            la = p.get('account') or (accs[0] if len(accs) == 1 else '')      # 同一個人加了多個品牌：只改指定品牌那一列
            if la not in accs: raise ValueError('請指定是哪個品牌的 LINE（同一個人加了好幾個品牌）')
            if p.get('customer_id') is not None:
                if p['customer_id']:
                    cu_ = core.get(con, 'customers', p['customer_id'])
                    if brands.valid(cu_.get('line_account') or '') and cu_['line_account'] != la:      # 稽核 B1：不能對到別品牌的 LINE（訊息會傳不到）
                        raise ValueError(f"{cu_['name']} 是{brands.name(cu_['line_account'])}的客戶，不能對到{brands.name(la)}官方 LINE 的好友")
                con.execute('UPDATE line_users SET customer_id=? WHERE user_id=? AND account=?', (p['customer_id'] or None, p['user_id'], la))
                if p['customer_id']:      # 稽核：綁在哪個品牌就記下來（卡片抬頭、發送帳號一致）
                    con.execute("UPDATE customers SET line_account=? WHERE id=? AND COALESCE(line_account,'')=''", (la, p['customer_id']))
            if p.get('worker_id') is not None:
                if p['worker_id']: core.get(con, 'workers', p['worker_id'])
                con.execute('UPDATE line_users SET worker_id=? WHERE user_id=? AND account=?', (p['worker_id'] or None, p['user_id'], la))
            core.audit(con, 'line_link', 'line_users', p['user_id'], after=p); return {'saved': True}
        if r == 'api/line/demo': return line_bot.demo(con, p)
        if r == 'api/backup': return {'file': backup()}
        return None

def main():
    core.initialize()
    with contextlib.closing(core.connect()) as con, con:
        acc.ensure_local(con)
    backup()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    print(f'Demo Ops ready: {ORIGIN} public={sorted(PUBLIC_HOSTS)} (LINE read-only)', flush=True)
    threading.Thread(target=maintenance, daemon=True).start()
    threading.Thread(target=line_bot.sender_loop, args=(LOCK,), daemon=True).start()
    server.serve_forever()

def remind_tomorrow(con):
    """每天 18:00：推播給明天有工作的夥伴（只發給系統內的夥伴帳號，不發 LINE、不發客戶）。"""
    import datetime as dt
    day = (dt.datetime.now(core.TZ).date() + dt.timedelta(days=1)).isoformat()
    for r in core.rows(con, "SELECT a.worker_id,o.start,o.end,o.title FROM assignments a JOIN orders o ON o.id=a.order_id "
                            "WHERE o.date=? AND o.status='confirmed' AND a.status='accepted' ORDER BY o.start", (day,)):
        notify.workers(con, [r['worker_id']], '明天的工作提醒', f"{r['start']}–{r['end']} 有工作，請準時，到場記得打卡", '/#mine')
    line_bot.on_tomorrow(con, day)          # LINE：客戶與夥伴的前一天提醒卡

def html_escape(t):
    import html
    return html.escape(str(t))

def report_error(where, e):
    """程式錯誤自動記到「功能回報」（同一種錯誤只記一筆、累加次數）；記不進去也不影響主流程。"""
    if isinstance(e, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
        return                       # 對方斷線（重啟、關分頁）不是程式錯誤
    try:
        import sysreport as issues
        detail = traceback.format_exc()
        with LOCK, contextlib.closing(core.connect()) as con, con:
            issues.auto(con, where, e, detail)
    except Exception:
        traceback.print_exc()

JSERR = {}

def maintenance():
    last_day = core.now()[:10]; reminded = ''
    while True:
        time.sleep(60)
        try:
            if core.now()[:10] != last_day:
                with LOCK:
                    backup()
                    try:
                        bdir = core.DATA / 'backups'          # 跟 backup() 同一個資料夾
                        olds = sorted(bdir.glob('operations-*.db'))[:-30] if bdir.exists() else []
                        for f in olds: f.unlink()
                    except Exception:
                        traceback.print_exc()
                last_day = core.now()[:10]
            def tomorrow(con):
                if core.now()[11:16] >= '18:00' and ix.setting(con, 'reminded_day') != core.now()[:10]:   # 存資料庫：重啟不會重發
                    remind_tomorrow(con); ix.set_setting(con, 'reminded_day', core.now()[:10])
                elif '18:15' <= core.now()[11:16] < '21:30' and core.now()[15] in '05':          # 稽核：18:00 之後才排定的明天工作，也補前一天提醒（每張只發一次）
                    import datetime as _d2
                    line_bot.on_tomorrow(con, (_d2.datetime.now(core.TZ).date() + _d2.timedelta(days=1)).isoformat())
                import datetime as _dt
                yday = (_dt.datetime.now(core.TZ).date() - _dt.timedelta(days=1)).isoformat()
                if '06:00' <= core.now()[11:16] < '12:00' and (ix.setting(con, 'reminded_day') or '') < yday and ix.setting(con, 'reminded_catchup') != core.now()[:10]:
                    # 盤點：昨晚 18:00 剛好停機 → 早上補發「今天」的提醒（只補還沒開始的場次）
                    ix.set_setting(con, 'reminded_catchup', core.now()[:10])
                    line_bot.on_tomorrow(con, core.now()[:10], '今天')
                    line_bot.admin_notify(con, '⚠️ 昨晚的前一天提醒沒發（系統停機），今天早上已補發', [('處理', '請留意今天的場次有沒有人沒收到')], '/#schedule', '#C47F00')
            for step in (dispatch.escalate, alerts.check, line_bot.on_worker_approved, tomorrow, line_bot.payments_daily, automation.step, __import__('linepay').sweep):      # LINE Pay 付完沒回頁面也要補確認＋通知
                try:
                    with LOCK, contextlib.closing(core.connect()) as con, con:
                        core.CTX.actor = '自動派工'
                        step(con)
                except Exception as e:
                    traceback.print_exc()
                    report_error(f"背景排程 {getattr(step, '__name__', step)}", e)
        except Exception:
            traceback.print_exc()

if __name__ == '__main__':
    main()
