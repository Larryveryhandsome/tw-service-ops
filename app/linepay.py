"""LINE Pay 線上付款（v3 Online API）：客戶在報價單／付款通知按「LINE Pay 付款」→ LINE Pay 付款頁 → 回來自動確認 → 收款完成、自動排定。

- 品牌分開：每個品牌可以各自設一組金鑰（LINE Pay 付款頁顯示該品牌的商店名稱，不會混到別的品牌）；沒設的品牌用「共用」那組。
- 金鑰（Channel ID／Channel Secret）由負責人在中控台「設定 › LINE Pay」自己貼；系統不經手、不顯示。
- 模式：sandbox（測試，不會真的扣款）／production（正式）。沒設定金鑰時，客戶看不到 LINE Pay 按鈕，照常匯款。
- 簽章：X-LINE-Authorization = Base64(HMAC-SHA256(ChannelSecret, ChannelSecret + URI + RequestBody + Nonce))
"""
import base64, hashlib, hmac, json, uuid, urllib.request, urllib.error
import core as c
import integrations as ix

HOST = {'sandbox': 'https://sandbox-api-pay.line.me', 'production': 'https://api-pay.line.me'}

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS linepay_tx(order_key TEXT PRIMARY KEY, pid TEXT, token TEXT, transaction_id TEXT DEFAULT '', amount INTEGER, "
                "status TEXT DEFAULT 'requested', created_at TEXT, confirmed_at TEXT DEFAULT '', mode TEXT DEFAULT '', result TEXT DEFAULT '', brand TEXT DEFAULT '')")
    if 'brand' not in {r[1] for r in con.execute('PRAGMA table_info(linepay_tx)')}:
        con.execute("ALTER TABLE linepay_tx ADD COLUMN brand TEXT DEFAULT ''")

def mode(con):
    return 'production' if ix.setting(con, 'linepay_mode') == 'production' else 'sandbox'

def keys(con, brand=''):
    """這個品牌自己的金鑰；沒有就用共用那組。"""
    for suf in ([f'_{brand}'] if brand else []) + ['']:
        cid, sec = ix.secret(con, 'linepay_channel_id' + suf), ix.secret(con, 'linepay_channel_secret' + suf)
        if cid and sec:
            return cid, sec, suf.strip('_') or '共用'
    return None, None, ''

DEMO_KEY = 'linepay_demo_quotes'          # 系統建的 LINE Pay 示範報價單（客戶填的資料改不到這裡）

def is_demo(con, q):
    try:
        return bool(q) and q['id'] in json.loads(ix.setting(con, DEMO_KEY) or '[]')
    except ValueError:
        return False

def demo_ok(con, q):
    """LINE Pay 審核示範單：測試模式＋有金鑰就給 LINE Pay 按鈕（真的客戶照舊看不到）。"""
    return is_demo(con, q) and mode(con) == 'sandbox' and ready(con, q['account'] or 'demoshop')

def demo_quote(con, cid, oid, site):
    """建一張示範報價單（已簽收、今天服務、完工當日付款 NT$ 1,800）給審核截圖用。回傳 (報價單, 收款單)。"""
    import booking, contract, payments
    import datetime as _dt, secrets
    payments.ensure(con); demo_cleanup(con, everything=True)
    day = _dt.datetime.now(c.TZ).date()
    n = con.execute('SELECT COUNT(*) FROM quotes WHERE id LIKE ?', (f"DEMO{day:%Y%m%d}%",)).fetchone()[0] + 1
    qid, token = f"DEMO{day:%Y%m%d}{n:02d}", secrets.token_urlsafe(18)
    terms = contract.terms(booking.company(con, 'demoshop').get('name'), '', 'demoshop')
    form = {'name': '王小姐（示範）', 'phone': '0900-000-000', 'address': site['address'], 'area': site['area'], 'service_type': '一般到府服務', 'source': 'LINE Pay 示範',
            'signed_terms': {'version': contract.VERSION, 'digest': contract.digest(terms), 'agreed_at': c.now(), 'ip': '示範'}}
    items = [{'title': '一般到府服務（示範）', 'sub': '1 人 × 3 小時', 'scope': ['浴室', '廚房', '客廳地板'], 'amount': 1800}]
    con.execute('INSERT INTO quotes(id,token,created_at,account,customer_id,order_id,form,estimate,total,date,start,end,status,signer_name,signed_at,ip,ua,stage,items,taxed,people,hours,scope,sent_at) '
                "VALUES(?,?,?,'demoshop',?,?,?,'{}',?,?,'08:00','20:00','signed',?,?,'','','signed',?,0,1,3,?,?)",
                (qid, token, c.now(), cid, oid, json.dumps(form, ensure_ascii=False), 180000, day.isoformat(), '王小姐（示範）', c.now(),
                 json.dumps(items, ensure_ascii=False), '浴室\n廚房\n客廳地板', c.now()))
    con.execute('INSERT INTO payment_requests(id,created_at,quote_id,order_id,customer_id,kind,label,amount,due,note) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (c.uid('pay'), c.now(), qid, oid, cid, 'balance', '服務費（完工當日）', 180000, day.isoformat(), 'LINE Pay 示範'))
    ids = [x for x in json.loads(ix.setting(con, DEMO_KEY) or '[]') if x != qid][-19:] + [qid]
    ix.set_setting(con, DEMO_KEY, json.dumps(ids))
    c.audit(con, 'linepay_demo', 'quotes', qid, after={'order_id': oid})
    return booking._q(con, qid=qid), payments.rows_for(con, quote_id=qid)

PUBLIC_DEMO = 'linepay_public_demo'      # /pay/demo 固定網址目前導去的示範報價單

def public_demo(con):
    """LINE Pay 審核用的固定網址：導到今天的示範報價單付款區（還沒付的沿用；付過、取消或隔天就建新的，一天最多 20 張）。
    只在測試模式（sandbox）＋有金鑰；正式模式關閉（不能讓外人用正式金流付示範款）。回傳要導去的路徑。"""
    import booking, line_bot as lb
    import datetime as _dt
    if mode(con) == 'production' or not ready(con, 'demoshop'):
        raise PermissionError('付款示範目前關閉（LINE Pay 已正式開通，請直接在官方 LINE 付款）')
    try:
        cur = json.loads(ix.setting(con, PUBLIC_DEMO) or '{}')
    except ValueError:
        cur = {}
    day = _dt.datetime.now(c.TZ).date().isoformat()
    if cur.get('qid') and cur.get('day') == day:
        q = con.execute('SELECT * FROM quotes WHERE id=?', (cur['qid'],)).fetchone()
        if q and q['status'] == 'signed' and con.execute("SELECT 1 FROM payment_requests WHERE quote_id=? AND status='pending'", (q['id'],)).fetchone():
            return f"/q/{q['token']}#pay"
    n = (cur.get('n', 0) if cur.get('day') == day else 0)
    if n >= 20:
        raise PermissionError('今天的付款示範次數已滿，請明天再試，或聯絡我們 🙏')
    cid, sid, wid = lb._demo_ids(con)
    oid = lb._demo_job(con, cid, sid, wid)
    q, rows = demo_quote(con, cid, oid, c.get(con, 'sites', sid))
    ix.set_setting(con, PUBLIC_DEMO, json.dumps({'qid': q['id'], 'day': day, 'n': n + 1}))
    return f"/q/{q['token']}#pay"

def demo_page(con):
    """/pay/demo 說明頁（給 LINE Pay 審核人員）：客戶怎麼付款、商店資訊、開始示範。"""
    import booking, brands, html as _h
    co = booking.company(con, 'demoshop'); name = _h.escape(brands.name('demoshop'))
    on = mode(con) != 'production' and ready(con, 'demoshop')
    steps = [('在官方 LINE 預約、確認報價', '客戶在示範服務店官方 LINE 估價、預約；報價單在線上查看並簽收。'),
             ('服務當天完工後收到「付款通知」', '服務完成後，客戶在 LINE 收到付款通知卡，點開就是報價單的付款區。'),
             ('選擇付款方式', '「用 LINE Pay 付款」或銀行匯款（回報末五碼）。LINE Pay 只用在服務當天或完工後的款項（銀貨兩訖）。'),
             ('LINE Pay 付款完成', '付款完成自動記成已收款，客戶在 LINE 收到「已收到您的款項」。需要退款由管理人員在後台處理（依商店資訊的退款政策）。')]
    li = ''.join(f'<li><b>{_h.escape(a)}</b><span>{_h.escape(b)}</span></li>' for a, b in steps)
    btn = ('<a class="go" href="/pay/demo/go">開始示範：打開付款頁</a><p class="note">本示範使用 LINE Pay 測試環境（Sandbox），不會實際扣款；畫面上的客戶、地址為示範資料。</p>'
           if on else '<p class="note">LINE Pay 已正式開通，付款示範已關閉；客戶請直接在官方 LINE 付款。</p>')
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{name}｜線上付款示範</title><meta name="robots" content="noindex">
<style>
:root{{--ink:#1d1d1f;--sub:#6e6e73;--line:#06C755;--bg:#faf9f6;--card:#fff;--bd:#e5e5ea}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif;padding:max(28px,env(safe-area-inset-top)) 18px 48px}}
main{{max-width:720px;margin:0 auto}}.k{{font-size:13px;font-weight:600;color:var(--sub);letter-spacing:.04em}}
h1{{font-size:clamp(28px,6vw,40px);line-height:1.15;margin:6px 0 8px}}.lede{{color:var(--sub);margin:0 0 22px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:16px;padding:20px 22px;margin:0 0 16px}}
ol{{margin:0;padding-left:22px}}ol li{{margin:0 0 12px}}ol li b{{display:block}}ol li span{{color:var(--sub);font-size:15px}}
.go{{display:inline-block;background:var(--line);color:#fff;text-decoration:none;font-weight:700;border-radius:999px;padding:14px 26px;font-size:17px}}
.note{{color:var(--sub);font-size:14px}}.links a{{color:var(--ink);margin-right:16px;white-space:nowrap}}
</style></head><body><main>
<div class="k">{name}・LINE Pay 線上付款</div><h1>客戶怎麼用 LINE Pay 付款</h1>
<p class="lede">{name}的預約、報價、付款都在官方 LINE 完成；客戶點 LINE 裡的付款通知，會打開下面示範的付款頁。</p>
<div class="card"><ol>{li}</ol></div>
<div class="card">{btn}</div>
<div class="card links"><b style="display:block;margin-bottom:6px">商店資訊</b>
<a href="/store?b=demoshop">公司資訊、聯絡方式、退款政策</a><a href="/privacy?b=demoshop">隱私權政策</a><a href="/home?b=demoshop">官方網站</a><a href="/mall">示範商城（耗材）</a>
<p class="note" style="margin:10px 0 0">{_h.escape(co.get('address') or '')}</p></div>
</main></body></html>'''

def demo_cleanup(con, everything=False):
    """示範款項不算收款、不催款：打「回管理」（everything）或隔天自動取消，示範報價單一起收掉。"""
    import datetime as _dt
    day = _dt.datetime.now(c.TZ).date().isoformat()
    con.execute("UPDATE orders SET status='cancelled' WHERE COALESCE(review_note,'')='示範工單' AND date<? AND status IN ('review','draft','confirmed')", (day,))
    old = (_dt.datetime.now(c.TZ) - _dt.timedelta(days=2)).isoformat()          # 測試視角送的示範估價：2 天後連款項一起取消
    has_pay = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='payment_requests'").fetchone()
    for (qid,) in con.execute("SELECT id FROM quotes WHERE json_extract(form,'$.demo')=1 AND created_at<? AND status!='cancelled'", (old,)).fetchall():
        if has_pay:
            con.execute("UPDATE payment_requests SET status='cancelled' WHERE quote_id=? AND status!='cancelled'", (qid,))
        con.execute("UPDATE quotes SET status='cancelled' WHERE id=?", (qid,))      # 稽核 A3：過期的示範單一起收掉
    try:
        ids = json.loads(ix.setting(con, DEMO_KEY) or '[]')
    except ValueError:
        return 0
    if not ids:
        return 0
    marks = ','.join('?' * len(ids))
    cond, args = ('', ids) if everything else (' AND created_at<?', ids + [day])
    n = con.execute(f"UPDATE payment_requests SET status='cancelled' WHERE quote_id IN ({marks}) AND status!='cancelled'" + cond, args).rowcount
    con.execute(f"UPDATE quotes SET status='cancelled' WHERE id IN ({marks}) AND status!='cancelled'" + cond, args)
    return n

def ready(con, brand=''):
    return bool(keys(con, brand)[0])

def status(con, brand=''):
    which = {b: keys(con, b)[2] for b in ('demoshop', 'bizline', 'homeline')}
    return {'ready': ready(con, brand) if brand else any(which.values()), 'mode': mode(con), 'keys': which,
            'enabled': (ready(con, brand) if brand else any(which.values())) and ix.setting(con, 'linepay_enabled') == '1'}

def save_keys(con, p, by):
    """負責人自己貼金鑰（中控台設定頁）。空白＝不改；mode／enabled 可單獨切換。"""
    suf = f"_{p['brand']}" if p.get('brand') in ('demoshop', 'bizline', 'homeline') else ''
    import re
    cid_in, sec_in = str(p.get('channel_id', '')).strip(), str(p.get('channel_secret', '')).strip()
    if cid_in and not re.fullmatch(r'\d{8,12}', cid_in):          # 瀏覽器把存過的帳號密碼自動填進 Channel ID → 連線失敗
        raise ValueError('Channel ID 應該是 10 位數字（LINE Pay 商家後台的「通路 ID」）。可能是瀏覽器自動填入了別的字，請清掉重貼。')
    if sec_in and (len(sec_in) < 8 or re.search(r'\s', sec_in)):
        raise ValueError('Channel Secret Key 看起來不對（商家後台的「通路密鑰」，32 個英數字），請重貼。')
    if str(p.get('channel_id', '')).strip():
        ix.put_secret(con, 'linepay_channel_id' + suf, str(p['channel_id']).strip()[:40])
    if str(p.get('channel_secret', '')).strip():
        ix.put_secret(con, 'linepay_channel_secret' + suf, str(p['channel_secret']).strip()[:80])
    if p.get('mode') in ('sandbox', 'production'):
        ix.set_setting(con, 'linepay_mode', p['mode'])
    if 'enabled' in p:
        ix.set_setting(con, 'linepay_enabled', '1' if p.get('enabled') else '')
    c.audit(con, 'linepay_settings', 'settings', 'linepay', after={'mode': mode(con), 'enabled': ix.setting(con, 'linepay_enabled'), 'by': by, 'keys_changed': bool(p.get('channel_id') or p.get('channel_secret'))})
    return status(con)

def _call(con, path, body=None, method='POST', brand=''):
    cid, sec, _ = keys(con, brand)
    if not (cid and sec):
        raise ValueError('LINE Pay 還沒設定金鑰')
    nonce = str(uuid.uuid4())
    raw = json.dumps(body, ensure_ascii=False, separators=(',', ':')) if body is not None else ''
    sig = base64.b64encode(hmac.new(sec.encode(), (sec + path + raw + nonce).encode(), hashlib.sha256).digest()).decode()
    req = urllib.request.Request(HOST[mode(con)] + path, data=raw.encode() if body is not None else None, method=method,
                                 headers={'Content-Type': 'application/json', 'X-LINE-ChannelId': cid, 'X-LINE-Authorization-Nonce': nonce, 'X-LINE-Authorization': sig})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
    except urllib.error.HTTPError as e:
        raise ValueError(f'LINE Pay 回應 {e.code}：{e.read()[:200].decode("utf-8", "replace")}') from None
    except OSError as e:
        raise ValueError(f'連線 LINE Pay 失敗：{e}') from None
    # transactionId 是 19 位數字：用字串保留，避免精度問題
    import re
    text = re.sub(r'"transactionId"\s*:\s*(\d+)', r'"transactionId":"\1"', text)
    out = json.loads(text)
    if out.get('returnCode') != '0000':
        raise ValueError(f"LINE Pay：{out.get('returnMessage') or ''}（{out.get('returnCode')}）")      # 帶代碼（sweep 要看 0110／0121…）
    return out.get('info') or {}

def request(con, token, pid, base_url, test=False):
    """客戶按「LINE Pay 付款」：建立付款請求，回傳要導去的 LINE Pay 付款網址。
    測試模式（sandbox）客戶看不到按鈕；只有報價單網址加 ?lptest=1 才能試（測試付款不會記成已收款）。"""
    import booking, brands
    ensure(con)
    q = booking._q(con, token)
    st = status(con, q['account'] or 'demoshop')
    if not demo_ok(con, q) and (not st['enabled'] or (st['mode'] != 'production' and not test)):      # 示範單（審核截圖）：測試模式也能付
        raise ValueError('目前請用匯款付款 🙏')
    r = con.execute('SELECT * FROM payment_requests WHERE id=? AND quote_id=?', (pid, q['id'])).fetchone()
    if not r:
        raise ValueError('找不到這筆款項')
    if r['status'] == 'confirmed':
        raise ValueError('這筆已經付過了，謝謝您！')
    if r['status'] == 'cancelled':
        raise ValueError('這筆款項已取消（工單取消），不用付款')
    if not allowed(con, r):
        raise ValueError('服務前的款項（訂金／全額）請用匯款付款 🙏 LINE Pay 用在服務當天或完工後的款項')
    amount = int(r['amount']) // 100
    if amount <= 0:
        raise ValueError('這筆金額為 0，不用付款')
    import secrets
    key = f"{q['id']}-{r['kind']}-{secrets.token_urlsafe(12)}"[:100]      # 資安：猜不到（以前只有 6 碼 hex）
    bname = brands.name(q['account'] or 'demoshop')
    body = {'amount': amount, 'currency': 'TWD', 'orderId': key,
            'packages': [{'id': r['kind'], 'amount': amount, 'name': bname[:100], 'products': [{'name': f"{bname} {r['label']}（報價單 {q['id']}）"[:200], 'quantity': 1, 'price': amount}]}],
            'redirectUrls': {'confirmUrl': f'{base_url}/linepay/confirm?k={key}', 'cancelUrl': f'{base_url}/q/{token}#pay'}}
    info = _call(con, '/v3/payments/request', body, brand=q['account'] or 'demoshop')
    con.execute('INSERT INTO linepay_tx(order_key,pid,token,transaction_id,amount,created_at,mode,brand) VALUES(?,?,?,?,?,?,?,?)',
                (key, pid, token, str(info.get('transactionId', '')), amount, c.now(), mode(con), q['account'] or 'demoshop'))
    return {'url': (info.get('paymentUrl') or {}).get('web', '')}

PREPAID = ('deposit', 'full', 'month')        # 服務前收的（訂金、單次全額、月方案）：LINE Pay 申請是「銀貨兩訖」，這些只收匯款

def allowed(con, r):
    """這筆收款單能不能用 LINE Pay：尾款、當日結算、月結（服務後）可以；訂金／全額／月方案要等到服務當天（含）以後。
    LINE Pay 商店申請選「銀貨兩訖」，所以不能用 LINE Pay 收預付款（否則要附履保證明）。"""
    o = con.execute('SELECT date,status FROM orders WHERE id=?', (r['order_id'],)).fetchone() if r['order_id'] else None
    import datetime as _dt
    if r['kind'] in PREPAID:                 # 負責人：新開通的商店確定可以收預付款 → 報價單簽收完就能用 LINE Pay 付訂金／全額／月費
        return bool(not o or o[1] not in ('cancelled', 'archived'))
    return bool(o and ((o[0] and o[0] <= _dt.datetime.now(c.TZ).date().isoformat()) or o[1] == 'completed'))     # 稽核：尾款、當日結算也要到服務當天

def live(con, brand='demoshop'):
    """給客戶／夥伴看的 LINE Pay 按鈕：只有正式模式＋已開啟才出現（測試模式只在報價單網址加 ?lptest=1 才有）。"""
    st = status(con, brand)
    return st['enabled'] and st['mode'] == 'production'

def sign(con, kind, ref):
    """/pay/<kind>?i=<ref>&s=<簽章>：付款連結不能被猜或竄改。"""
    import worker_portal, hmac as _h, hashlib as _hl
    k = worker_portal._key(con)
    return _h.new(k if isinstance(k, bytes) else str(k).encode(), f'{kind}:{ref}'.encode(), _hl.sha256).hexdigest()[:20]

def test_connection(con, brand=''):
    """中控台「測試連線」：用 1 元建立一筆付款請求（不會扣款、20 分鐘後自動失效），確認金鑰、簽章、伺服器 IP 白名單都通。"""
    import secrets
    cid, sec, which = keys(con, brand)
    if not cid:
        raise ValueError('還沒貼金鑰')
    body = {'amount': 1, 'currency': 'TWD', 'orderId': 'TEST-' + secrets.token_hex(8),
            'packages': [{'id': 'test', 'amount': 1, 'name': '連線測試', 'products': [{'name': '連線測試（不會扣款）', 'quantity': 1, 'price': 1}]}],
            'redirectUrls': {'confirmUrl': 'https://ops.example.com/pay/done', 'cancelUrl': 'https://ops.example.com/pay/done?c=1'}}
    info = _call(con, '/v3/payments/request', body, brand=brand)
    return f"✅ LINE Pay 連線正常（{'正式' if mode(con) == 'production' else '測試 sandbox'}・金鑰：{which}）。剛剛建立的 1 元測試請求不會扣款，20 分鐘後自動失效。" if info.get('transactionId') else '⚠️ LINE Pay 有回應但沒有交易編號，請截圖給 Claude 看'

def request_fee(con, pid, amount, name, base_url, test=False):
    """非報價單的收款（例：良民證代辦規費 pid='police:<面試編號>'）：只在正式模式（或 test）開放，付完導回 /pay/done。"""
    import secrets
    ensure(con)
    st = status(con, 'demoshop')
    if not st['enabled'] or (st['mode'] != 'production' and not test):
        raise ValueError('LINE Pay 還沒開通，請用其他方式付款（現場付或匯款）')
    key = f"{pid.replace(':', '-')}-{secrets.token_urlsafe(12)}"[:100]
    body = {'amount': int(amount), 'currency': 'TWD', 'orderId': key,
            'packages': [{'id': 'fee', 'amount': int(amount), 'name': '示範服務店', 'products': [{'name': name[:200], 'quantity': 1, 'price': int(amount)}]}],
            'redirectUrls': {'confirmUrl': f'{base_url}/linepay/confirm?k={key}', 'cancelUrl': f'{base_url}/pay/done?c=1'}}
    info = _call(con, '/v3/payments/request', body, brand='demoshop')
    con.execute('INSERT INTO linepay_tx(order_key,pid,token,transaction_id,amount,created_at,mode,brand) VALUES(?,?,?,?,?,?,?,?)',
                (key, pid, '', str(info.get('transactionId', '')), int(amount), c.now(), mode(con), 'demoshop'))
    return {'url': (info.get('paymentUrl') or {}).get('web', '')}

def confirm(con, key, transaction_id):
    """LINE Pay 付款完導回來：向 LINE Pay 確認扣款 → 記成已收款（不用店長手動核對）→ 收齊就自動排定。回傳要導回的頁面。"""
    import payments, line_bot
    ensure(con)
    t = con.execute('SELECT * FROM linepay_tx WHERE order_key=?', (key,)).fetchone()
    if not t:
        raise ValueError('找不到這筆 LINE Pay 付款')
    if (t['pid'] or '').startswith(('police:', 'shop:')):     # 良民證代辦費、商城訂單
        so = con.execute("SELECT status FROM shop_orders WHERE id=?", (t['pid'][5:],)).fetchone() if t['pid'].startswith('shop:') else None
        if so and so[0] == 'cancelled' and t['status'] != 'confirmed':          # 稽核 F8：訂單已取消（例：24 小時沒連 LINE）→ 不請款
            con.execute("UPDATE linepay_tx SET status='void',result='訂單已取消，未請款' WHERE order_key=?", (key,))
            line_bot.admin_notify(con, '⚠️ 商城 LINE Pay 沒有請款', [('訂單', t['pid'][5:]), ('原因', '訂單已取消'), ('處理', '客戶沒有被扣款；如有疑問請說明')], '/#shop', '#C47F00')
            return '/pay/done?c=1'
        if t['status'] != 'confirmed':
            _call(con, f"/v3/payments/{t['transaction_id'] or transaction_id}/confirm", {'amount': t['amount'], 'currency': 'TWD'}, brand='demoshop')
            con.execute("UPDATE linepay_tx SET status='confirmed',confirmed_at=? WHERE order_key=?", (c.now(), key))
            if t['mode'] == 'production':
                kind, ref = t['pid'].split(':', 1)
                if kind == 'police':
                    import recruit; recruit.police_paid_linepay(con, ref)
                else:
                    import shop; shop.paid(con, ref, 'LINE Pay')
        return '/pay/done'
    if t['status'] == 'confirmed':
        return f"/q/{t['token']}#pay"
    if transaction_id and t['transaction_id'] and str(transaction_id) != t['transaction_id']:
        raise ValueError('交易編號不符')
    pr = con.execute('SELECT status,label,order_id FROM payment_requests WHERE id=?', (t['pid'],)).fetchone()
    if pr and pr['status'] in ('confirmed', 'cancelled'):        # 稽核：付款途中被確認／工單取消 → 不向 LINE Pay 請款（不扣款），通知管理層
        con.execute("UPDATE linepay_tx SET status='void',result=? WHERE order_key=?", (f"款項已{'確認' if pr['status'] == 'confirmed' else '取消'}，未請款", key))
        line_bot.admin_notify(con, '⚠️ LINE Pay 付款沒有請款', [('款項', pr['label']), ('原因', '這筆已經確認收款' if pr['status'] == 'confirmed' else '工單已取消'), ('處理', '客戶沒有被扣款；如客戶有疑問請說明')], '/#home', '#C47F00', oid=pr['order_id'] or None)
        return f"/q/{t['token']}#pay"
    try:
        _call(con, f"/v3/payments/{t['transaction_id'] or transaction_id}/confirm", {'amount': t['amount'], 'currency': 'TWD'}, brand=t['brand'] or 'demoshop')
    except ValueError as e:
        con.execute("UPDATE linepay_tx SET status='failed',result=? WHERE order_key=?", (str(e)[:300], key))
        line_bot.admin_notify(con, '⚠️ LINE Pay 付款確認失敗', [('款項', t['pid']), ('原因', str(e)[:80]), ('處理', '請到 LINE Pay 商家後台查這筆交易，必要時聯絡客戶')], '/#home', '#C62828', oid=(pr['order_id'] if pr else None) or None)
        raise
    con.execute("UPDATE linepay_tx SET status='confirmed',confirmed_at=? WHERE order_key=?", (c.now(), key))
    qd = con.execute('SELECT id FROM quotes WHERE token=?', (t['token'],)).fetchone() if t['token'] else None
    if t['mode'] == 'sandbox' and qd and is_demo(con, qd):        # LINE Pay 審核示範：示範資料走完整流程（記成已收、客戶收到「已收到款項」），回管理／隔天自動取消
        c.CTX.actor = 'LINE Pay（示範）'
        payments.confirm(con, t['pid'], 'LINE Pay', t['amount'] * 100, f"LINE Pay 示範交易 {t['transaction_id']}（sandbox，沒有真的扣款）")
        line_bot.after_payment_confirmed(con, t['pid'])
        line_bot.admin_notify(con, f"🧪 LINE Pay 示範付款成功 NT$ {t['amount']:,}", [('款項', '示範報價單 ' + qd[0]), ('說明', '測試模式（sandbox），沒有真的扣款；示範款項打「回管理」或隔天自動取消')], '', '#6E6E73')
        return f"/q/{t['token']}#paid"
    if t['mode'] == 'sandbox':          # 測試付款：沒有真的扣款 → 絕不記成已收款、不觸發排定（加的保險）
        line_bot.admin_notify(con, f"🧪 LINE Pay 測試付款成功 NT$ {t['amount']:,}", [('款項', t['pid']), ('說明', '測試模式（sandbox），沒有真的扣款，不會記成已收款')], '/#home', '#6E6E73')
        return f"/q/{t['token']}#pay"
    c.CTX.actor = 'LINE Pay（自動入帳）'
    payments.confirm(con, t['pid'], 'LINE Pay', t['amount'] * 100, f"LINE Pay 交易 {t['transaction_id']}（{t['mode']}）")
    line_bot.after_payment_confirmed(con, t['pid'])
    r = con.execute('SELECT p.label,p.order_id,cu.name FROM payment_requests p LEFT JOIN customers cu ON cu.id=p.customer_id WHERE p.id=?', (t['pid'],)).fetchone()
    import brands                       # LINE Pay 有收到都要在管理群回報（客人、品牌、哪筆款）
    line_bot.admin_notify(con, f"💚 LINE Pay 已收款 NT$ {t['amount']:,}", [('客人', (r['name'] if r else '') or '—'), ('品牌', brands.name(t['brand'] or 'demoshop')),
                          ('款項', r['label'] if r else t['pid']), ('說明', '已自動記成收款，款項收齊會自動排定；全部收款在中控台「收款紀錄」，或在群組打「收款紀錄」')], '/#payhist', '#2E7D32')
    return f"/q/{t['token']}#paid"

def sweep(con):
    """「LINE PAY 有付款成功都要馬上通知管理群，之前沒有發的補發」：
    客戶在 LINE Pay 付完、沒回到我們的頁面（關掉、網路斷）→ 系統不知道 → 不會記收款也不會通知。
    每分鐘查一次還在「付款中」的正式交易（3 分鐘～3 天內）：LINE Pay 說已授權（0110）就照常確認收款（記收款＋通知管理群＋業主收到收據）；
    已取消／逾時的標起來不再查。回傳補確認了幾筆。"""
    import datetime as dt
    ensure(con)
    now = dt.datetime.now(c.TZ)
    lo, hi = (now - dt.timedelta(days=3)).isoformat(timespec='seconds'), (now - dt.timedelta(minutes=3)).isoformat(timespec='seconds')
    n = 0
    for t in c.rows(con, "SELECT * FROM linepay_tx WHERE status='requested' AND mode='production' AND transaction_id!='' AND created_at BETWEEN ? AND ? ORDER BY created_at DESC LIMIT 5", (lo, hi)):      # 每次最多 5 筆（每分鐘跑）
        try:                                       # 查付款狀態：0000＝還在付款畫面（等）；0110＝已授權要確認；0121／0122／0123＝取消、逾時、失敗
            _call(con, f"/v3/payments/requests/{t['transaction_id']}/check", None, 'GET', brand=t['brand'] or 'demoshop')
            continue
        except ValueError as e:
            msg = str(e)
        if '0123' in msg:                          # 已付款完成（可能請款成功但存檔時中斷）→ 不能再請款，交給管理群確認
            con.execute("UPDATE linepay_tx SET status='check',result=? WHERE order_key=?", ('LINE Pay 顯示已付款完成，系統沒有記到，請管理層確認', t['order_key']))
            import line_bot as _lb
            _lb.admin_notify(con, f"💚 LINE Pay 已付款 NT$ {t['amount']:,}（系統漏記，請確認）", [('交易', t['transaction_id']), ('處理', '請到 LINE Pay 商家後台確認這筆，再到中控台「收款紀錄」或首頁按確認收款')], '/#payhist', '#C47F00')
            continue
        if '0110' not in msg:
            if any(k in msg for k in ('0121', '0122')):
                con.execute("UPDATE linepay_tx SET status='void',result=? WHERE order_key=?", ('客戶沒有完成付款：' + msg[:120], t['order_key']))
            continue
        if True:
            try:
                confirm(con, t['order_key'], t['transaction_id']); n += 1
                con.execute("UPDATE linepay_tx SET result=? WHERE order_key=?", ('系統補確認（客戶付完沒回到頁面）', t['order_key']))
            except Exception as e:
                con.execute("UPDATE linepay_tx SET result=? WHERE order_key=?", (('補確認失敗：' + str(e))[:200], t['order_key']))
    return n

def refund(con, key, amount=None, by=''):
    """退款（全額或部分，元）。只有看得到金額的人可以用。"""
    ensure(con)
    t = con.execute("SELECT * FROM linepay_tx WHERE order_key=? AND status='confirmed'", (key,)).fetchone()
    if not t:
        raise ValueError('找不到已付款的這筆交易')
    body = {'refundAmount': int(amount)} if amount else {}
    info = _call(con, f"/v3/payments/{t['transaction_id']}/refund", body, brand=t['brand'] or 'demoshop')
    con.execute("UPDATE linepay_tx SET status=?,result=? WHERE order_key=?", ('refunded' if not amount or int(amount) >= t['amount'] else 'partial_refund', json.dumps(info)[:300], key))
    c.audit(con, 'linepay_refund', 'linepay_tx', key, after={'amount': amount or t['amount'], 'by': by})
    amt = int(amount) if amount else t['amount']          # 每日檢查 #4：退款同步到收款單（收款紀錄、記帳不再當成收到全額）
    if t['pid'] and not str(t['pid']).startswith(('shop:', 'police:')):
        full = amt >= t['amount']
        con.execute("UPDATE payment_requests SET status=CASE WHEN ? THEN 'refunded' ELSE status END,amount=CASE WHEN ? THEN amount ELSE MAX(0,amount-?) END,"
                    "note=TRIM(COALESCE(note,'') || ?) WHERE id=?", (full, full, amt * 100, f'｜LINE Pay 退款 {amt:,} 元（{by}）', t['pid']))
    return {'refunded': amt}
