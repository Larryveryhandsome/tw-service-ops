"""示範商城（水垢、油垢、磁磚耗材，前期 500ML；夥伴跟業主都可以選購）。

第二版（負責人：讓他們點選；LINE 可以操作，也另外做商城網站給客戶、夥伴用；中控台只看數據）：
- 商城網站 /mall（官網網址 www.example.com/mall 也可以）：照「服務問題」找商品、商品頁寫清楚適用／不適用／用法／注意事項、
  購物車、免註冊下單（夥伴從 LINE 帶身分連結進來看到夥伴價）、下單前看得到總金額與取貨方式、訂單進度頁。
- 官方 LINE：打「商城」點選購買（照舊），也能一鍵開網站；打「我的訂單」查商城訂單。
- 庫存：下單就扣、取消加回；夥伴帶去案場用的記「領用」，不算賣出（數字分開）。stock 空白＝不控管。
- 中控台：營收、訂單、熱銷、瀏覽→加購→下單、庫存；商品內容／價格／照片／入庫在這裡改。訂單處理在派單管理群卡片點選。
- 商品說明只放供應商標示與店長確認過的文字：沒填的欄位網站上不出現，不自己編「無毒、抗菌」這類宣稱。
- 只開在示範服務店（品牌不混）。價格、夥伴價、取貨付款方式由店長決定（decisions：商城價格／商城取貨付款）；沒定價的商品不上架。
先不做（等真的有量）：宅配金物流、優惠碼、會員點數、定期配送。

上架 6 款（負責人：示範商城資料內化成品牌語言；價格客服給）：水垢 379、廚房油垢 309、磁磚 269、浴室 329、除霉 359、地板傢俱 299（500ML）。
商品說明照廠商的使用步驟、注意事項改寫成示範服務店口吻（不加功效宣稱；成分與警語以瓶身為準）；夥伴價等店長定（decisions：商城夥伴價），沒定之前跟售價一樣。
"""
import datetime as dt
import json
import re
import secrets
import core as c
import integrations as ix

SEED = [('shp-scale', '水垢耗材', '500ML', '浴室、水龍頭、玻璃的白色水垢', 1, '浴室水垢,玻璃鏡面'), ('shp-grease', '廚房油垢耗材', '500ML', '抽油煙機、廚房牆面與檯面油污', 2, '廚房油污'),
        ('shp-tile', '磁磚耗材', '500ML', '陽台、浴室、地面磁磚與縫隙的髒污', 3, '磁磚地面'), ('shp-bath', '浴室耗材', '500ML', '馬桶、洗手台、浴室地板的髒污', 4, '浴室馬桶'),
        ('shp-mold', '除霉萬用劑', '500ML', '牆面、磁磚縫、矽利康的霉斑', 5, '發霉黴斑'), ('shp-floor', '地板傢俱耗材', '500ML', '室內地板、傢俱的日常服務', 6, '地板傢俱')]
PROBLEMS = ['浴室水垢', '浴室馬桶', '廚房油污', '磁磚地面', '發霉黴斑', '地板傢俱', '玻璃鏡面', '工具耗材']
WORKER_KEYS = (('水垢', 'shp-scale'), ('油垢', 'shp-grease'), ('磁磚', 'shp-tile'), ('浴室', 'shp-bath'), ('除霉', 'shp-mold'), ('地板', 'shp-floor'))

def set_worker_prices(con, ans, by):
    """店長回【商城夥伴價】：「8 折」「85 折」→ 每瓶售價 × 折數（四捨五入）；「跟客人一樣」→ 不打折；或逐項「水垢 300、油垢 250」。回傳說明（看不懂回空字串）。"""
    ensure(con)
    ps = {p['id']: p for p in products(con, False)}
    ch = {}
    if '一樣' in ans or '不打折' in ans:
        ch = {pid: None for pid in ps}
    elif re.search(r'(\d{1,2}(?:\.\d)?)\s*折', ans):
        k = float(re.search(r'(\d{1,2}(?:\.\d)?)\s*折', ans).group(1)); k = k / 10 if k < 10 else k / 100
        if not 0.3 <= k < 1:
            return ''
        ch = {pid: int(round(p['price'] * k)) for pid, p in ps.items() if p['price']}
    else:
        for key, pid in WORKER_KEYS:
            m = re.search(key + r'[^\d、，,；;]{0,10}?(\d{2,5})', ans)
            if m and pid in ps:
                ch[pid] = int(m.group(1))
    if not ch:
        return ''
    for pid, v in ch.items():
        con.execute('UPDATE shop_products SET worker_price=? WHERE id=?', (v, pid))
    c.audit(con, 'shop_worker_price', 'shop_products', ','.join(ch), after={'answer': ans[:100], 'by': by})
    return '夥伴價：' + '、'.join(f"{ps[pid]['name']} {v if v else ps[pid]['price']}" for pid, v in ch.items()) + '（夥伴從 LINE 打「商城」就看得到）'
CONTENT = ('solves', 'suitable', 'avoid', 'usage', 'spec', 'warning', 'faq')        # 商品頁欄位：解決什麼／適用／不適用與注意／用法／規格內容／成分警語／常見問題
STATUS = {'new': '已收到訂單', 'ready': '備好了，等您取貨', 'delivered': '已交付', 'cancelled': '已取消'}

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS shop_products(id TEXT PRIMARY KEY, name TEXT, size TEXT DEFAULT '500ML', note TEXT DEFAULT '', price INTEGER, worker_price INTEGER, "
                "active INTEGER DEFAULT 0, sort INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS shop_orders(id TEXT PRIMARY KEY, uid TEXT, acc TEXT DEFAULT 'demoshop', buyer TEXT, ref_id TEXT, name TEXT, items TEXT DEFAULT '[]', "
                "total INTEGER DEFAULT 0, delivery TEXT DEFAULT '', status TEXT DEFAULT 'cart', note TEXT DEFAULT '', handled_by TEXT DEFAULT '', created_at TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS shop_stock(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, pid TEXT, delta INTEGER, kind TEXT, ref TEXT DEFAULT '', note TEXT DEFAULT '', by TEXT DEFAULT '')")
    con.execute("CREATE TABLE IF NOT EXISTS shop_events(day TEXT, pid TEXT, kind TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(day, pid, kind))")
    con.execute("CREATE TABLE IF NOT EXISTS shop_images(id TEXT PRIMARY KEY, mime TEXT, content BLOB, created_at TEXT)")
    cols = {r[1] for r in con.execute('PRAGMA table_info(shop_orders)')}
    for k, d in (('paid_at', "TEXT DEFAULT ''"), ('paid_how', "TEXT DEFAULT ''"), ('phone', "TEXT DEFAULT ''"), ('token', "TEXT DEFAULT ''"), ('channel', "TEXT DEFAULT 'line'"),
                 ('stock_done', 'INTEGER DEFAULT 0'), ('ip', "TEXT DEFAULT ''"), ('announced_at', "TEXT DEFAULT ''")):
        if k not in cols:
            con.execute(f'ALTER TABLE shop_orders ADD COLUMN {k} {d}')
            if k == 'announced_at':              # 之前的訂單都已經通知過派單管理群
                con.execute("UPDATE shop_orders SET announced_at=created_at WHERE status!='cart'")
    cols = {r[1] for r in con.execute('PRAGMA table_info(shop_products)')}
    for k, d in [('stock', 'INTEGER'), ('image_id', "TEXT DEFAULT ''"), ('image_mime', "TEXT DEFAULT ''"), ('problems', "TEXT DEFAULT ''")] + [(k, "TEXT DEFAULT ''") for k in CONTENT]:
        if k not in cols:
            con.execute(f'ALTER TABLE shop_products ADD COLUMN {k} {d}')
    for pid, name, size, note, sort, prob in SEED:
        con.execute('INSERT OR IGNORE INTO shop_products(id,name,size,note,sort) VALUES(?,?,?,?,?)', (pid, name, size, note, sort))
        con.execute("UPDATE shop_products SET problems=? WHERE id=? AND COALESCE(problems,'')=''", (prob, pid))
        con.execute("UPDATE shop_products SET solves=note WHERE id=? AND COALESCE(solves,'')=''", (pid,))

def products(con, only_active=True):
    ensure(con)
    return c.rows(con, 'SELECT * FROM shop_products' + (' WHERE active=1 AND price>0' if only_active else '') + ' ORDER BY sort')

def _buyer(con, lu):
    if lu and lu.get('worker_id'):
        return 'worker', lu['worker_id'], c.get(con, 'workers', lu['worker_id'])['name']
    if lu and lu.get('customer_id'):
        return 'customer', lu['customer_id'], c.get(con, 'customers', lu['customer_id'])['name']
    return None, None, None

def unit_price(p, buyer):
    return (p['worker_price'] or p['price']) if buyer == 'worker' else p['price']

def in_stock(p, qty=1):
    return p.get('stock') is None or (p['stock'] or 0) >= qty

def bump(con, pid, kind, n=1):
    """瀏覽／加入購物車次數（中控台看「瀏覽 → 加購 → 下單」）。"""
    ensure(con)
    con.execute('INSERT INTO shop_events(day,pid,kind,n) VALUES(?,?,?,?) ON CONFLICT(day,pid,kind) DO UPDATE SET n=n+excluded.n', (c.now()[:10], pid or '', kind, n))

# ───────── 身分：LINE 帶進網站的連結碼（客戶 t＝我的預約連結碼；夥伴 w＝主控板連結碼）─────────
UNLINKED_HOURS = 24                 # 網站訂單沒連上官方 LINE、也沒付款：幾小時後自動取消（聯絡不到人＝幽靈單）

L_DAYS = 30                         # 從 LINE 打開商城的代號：30 天有效（稽核 F3）

def line_token(con, uid):
    """從 LINE 打開商城（還沒綁定的人也一樣）網址帶的代號：網站下單直接連到這個 LINE。網址上不放 LINE 的 userId。"""
    if not uid:
        return ''
    t = ix.setting(con, f'shop_l_of:{uid}')
    if not t or (ix.setting(con, f'shop_l_at:{t}') or '') < (dt.datetime.now(c.TZ) - dt.timedelta(days=L_DAYS - 1)).isoformat():      # 稽核 F3：30 天換一組
        t = secrets.token_urlsafe(12)
        ix.set_setting(con, f'shop_l_of:{uid}', t); ix.set_setting(con, f'shop_l:{t}', uid); ix.set_setting(con, f'shop_l_at:{t}', c.now())
    return t

def who(con, t='', w='', l=''):
    """回傳 (kind, ref_id, name, phone, acc, uid)；沒帶或過期＝訪客。l＝從 LINE 打開（還沒綁定的人）：訪客價，但訂單直接連到這個 LINE。"""
    if w:
        r = con.execute("SELECT id,name,phone,status FROM workers WHERE portal_token=?", (w,)).fetchone()
        if r and r['status'] in ('active', 'review'):
            import automation
            if not automation.is_blacklisted(con, r['id']):
                u = con.execute('SELECT account,user_id FROM line_users WHERE worker_id=? AND is_friend=1 ORDER BY last_seen DESC LIMIT 1', (r['id'],)).fetchone()
                return 'worker', r['id'], r['name'], r['phone'] or '', (u[0] if u else 'demoshop'), (u[1] if u else '')
    if t:
        try:
            import worker_portal
            acc, uid, cu = worker_portal._cust(con, t)
            if acc == 'demoshop':
                return 'customer', cu['id'], cu['name'], cu['phone'] or '', acc, uid
        except (PermissionError, ValueError):
            pass
    if l and re.match(r'^[A-Za-z0-9_-]{10,30}$', l):        # 稽核 F3：只當訪客（不升級成業主／夥伴、不帶入名字電話），30 天過期
        uid = ix.setting(con, f'shop_l:{l}')
        fresh = (ix.setting(con, f'shop_l_at:{l}') or '') >= (dt.datetime.now(c.TZ) - dt.timedelta(days=L_DAYS)).isoformat()
        if uid and fresh and con.execute("SELECT 1 FROM line_users WHERE account='demoshop' AND user_id=?", (uid,)).fetchone():
            return 'guest', None, '', '', 'demoshop', uid
    return 'guest', None, '', '', 'demoshop', ''

def link_for(con, lu, acc, uid):
    """LINE 裡給的商城網址（帶身分：夥伴看到夥伴價、客戶自動帶入姓名電話）。"""
    import line_bot as lb, worker_portal
    base = f'{lb.BASE_URL}/mall'
    if lu and lu.get('worker_id'):
        return f"{base}?w={worker_portal.portal_token(con, lu['worker_id'])}"
    if lu and lu.get('customer_id'):
        return f"{base}?t={worker_portal.cust_token(con, acc, uid, lu['customer_id'])}"
    if acc == 'demoshop' and uid:                   # 還沒綁定的也帶代號 → 網站下單直接連到這個 LINE（不會變成聯絡不到人的訂單）
        return f"{base}?l={line_token(con, uid)}"
    return base

def image_url(con, p, rel=False):
    """LINE 卡片要完整網址；商城網站用相對路徑（官網 www.example.com/mall 打開時，別的網域的圖片會被 CSP 擋掉，修）。"""
    import line_bot as lb
    return f"{'' if rel else lb.BASE_URL}/mall/img/{p['id']}?v={(p.get('image_id') or '')[-6:]}" if p.get('image_id') else ''

def catalog(con, kind='guest'):
    """網站用：上架商品（價格依買家身分），只給有填的說明欄位。"""
    out = []
    for p in products(con):
        out.append({'id': p['id'], 'name': p['name'], 'size': p['size'], 'price': unit_price(p, kind), 'list_price': p['price'],
                    'worker_price': kind == 'worker' and bool(p['worker_price']), 'problems': [x for x in (p.get('problems') or '').split(',') if x],
                    'image': image_url(con, p, rel=True), 'stock': None if p.get('stock') is None else max(0, p['stock']),
                    **{k: (p.get(k) or '').strip() for k in CONTENT}})
    return out

def delivery_options(kind):
    return {'worker': [('office', '下次到示範服務店領'), ('job', '派工時帶給我')], 'customer': [('next', '下次服務時帶給我'), ('pickup', '我自己去拿')],
            'guest': [('pickup', '到示範服務店自取（臺北板橋）'), ('contact', '請專人聯絡我安排')]}.get(kind, [])

DELIVERY = {'customer': delivery_options('customer'), 'worker': delivery_options('worker')}

def public(con, t='', w='', l=''):
    kind, ref, name, phone, acc, uid = who(con, t, w, l)
    import linepay, booking
    co = booking.company(con, 'demoshop')
    return {'buyer': {'kind': kind, 'name': name, 'phone': phone, 'line': bool(uid)}, 'products': catalog(con, kind), 'problems': PROBLEMS,
            'delivery': [{'id': k, 'label': v} for k, v in delivery_options(kind)], 'terms': ix.setting(con, 'shop_terms') or '',
            'linepay': linepay.live(con, 'demoshop'), 'line': f"https://line.me/R/ti/p/{__import__('line_bot').basic_id(con, 'demoshop')}", 'brand_name': __import__('brands').name('demoshop'),
            'address': co.get('address') or ''}

# ───────── 庫存 ─────────
def stock_move(con, pid, delta, kind, ref='', note='', by=''):
    """入庫 in／賣出 sale／取消加回 cancel／團隊領用 use／盤點調整 adjust。stock 空白（不控管）的商品只記帳，不算數字。"""
    ensure(con)
    p = con.execute('SELECT * FROM shop_products WHERE id=?', (pid,)).fetchone()
    if not p:
        raise ValueError('找不到商品')
    if kind not in ('in', 'sale', 'cancel', 'use', 'adjust'):
        raise ValueError('庫存異動種類不對')
    delta = int(delta)
    con.execute('INSERT INTO shop_stock(at,pid,delta,kind,ref,note,by) VALUES(?,?,?,?,?,?,?)', (c.now(), pid, delta, kind, ref, note[:200], by))
    if p['stock'] is not None or kind in ('in', 'adjust'):
        con.execute('UPDATE shop_products SET stock=COALESCE(stock,0)+? WHERE id=?', (delta, pid))
    return con.execute('SELECT stock FROM shop_products WHERE id=?', (pid,)).fetchone()[0]

def _reserve(con, o):
    """下單：檢查上架與庫存，扣庫存（每張單只扣一次）。"""
    if o.get('stock_done'):
        return
    items = json.loads(o['items'] or '[]')
    for i in items:
        p = con.execute('SELECT * FROM shop_products WHERE id=?', (i['pid'],)).fetchone()
        if not p or not p['active'] or not p['price']:
            raise ValueError(f"{i['name']} 目前沒有販售，請把它從購物車拿掉 🙏")
        if p['stock'] is not None and p['stock'] < i['qty']:
            raise ValueError(f"{i['name']} 只剩 {max(0, p['stock'])} 瓶，請改數量 🙏")
    for i in items:
        stock_move(con, i['pid'], -int(i['qty']), 'sale', o['id'])
    con.execute('UPDATE shop_orders SET stock_done=1 WHERE id=?', (o['id'],))

def _release(con, o):
    if not o.get('stock_done'):
        return
    for i in json.loads(o['items'] or '[]'):
        stock_move(con, i['pid'], int(i['qty']), 'cancel', o['id'])
    con.execute('UPDATE shop_orders SET stock_done=0 WHERE id=?', (o['id'],))

# ───────── LINE ─────────
def menu(con, acc, lu, uid=''):
    """商品卡（左右滑）：第一張是「到網站逛」；每張商品可直接點選加入購物車。"""
    import line_bot as lb
    if acc != 'demoshop':
        return lb._txt('商城目前只在示範服務店官方帳號開放 🙏')
    buyer, _, _ = _buyer(con, lu)
    ps = products(con)
    if not ps:
        return lb._txt('示範商城準備中，很快就開賣 😊')
    url = link_for(con, lu, acc, uid or (lu or {}).get('user_id', ''))
    if not buyer:                 # 還沒綁定：可以到網站直接買（免註冊），或先綁定
        return lb.flex('示範商城', lb._bubble(lb._header('示範商城 🧴', '服務團隊在用的耗材', '#2E7D32'),
            [lb._t('不用註冊，打開網站就能選購、下單；從這裡打開下單會自動連到您的 LINE，備好了直接在這裡通知您。綁定資料之後在 LINE 也能直接點選購買。', wrap=True)],
            [lb._btn('打開商城網站', uri=url, primary=True), lb._btn('綁定我的資料', data='a=bind')]))
    bs = [lb._bubble(lb._header('示範商城 🧴', '服務團隊在用的耗材', '#2E7D32'),
                     [lb._t(f'共 {len(ps)} 款，都是 500ML。左右滑看商品，點「買 1 瓶」就加入購物車；想看完整說明、用法，打開網站更清楚。', wrap=True)]
                     + ([lb._t('你看到的是夥伴價 👍', color=lb.SUB, size='sm', margin='md')] if buyer == 'worker' else []),
                     [lb._btn('打開商城網站', uri=url, primary=True), lb._btn('看購物車／結帳', data='a=shop_co'), lb._btn('我的訂單', data='a=shop_my')])]
    for p in ps[:9]:
        price = unit_price(p, buyer)
        sold_out = not in_stock(p)
        b = lb._bubble(lb._header('示範商城', f"{p['name']} {p['size']}", '#2E7D32'),
                       [lb._t(p.get('solves') or p['note'] or '', color=lb.SUB, wrap=True), lb._row('價格', f"NT$ {price:,}" + ('（夥伴價）' if buyer == 'worker' and p['worker_price'] else ''))]
                       + ([lb._row('庫存', '暫時缺貨' if sold_out else f"剩 {p['stock']} 瓶")] if p['stock'] is not None and (sold_out or p['stock'] <= 5) else []),
                       ([] if sold_out else [lb._btn('買 1 瓶', data=f"a=shop_add&p={p['id']}&q=1", primary=True), lb._btn('買 2 瓶', data=f"a=shop_add&p={p['id']}&q=2")])
                       + [lb._btn('看說明・用法', uri=f"{url}{'&' if '?' in url else '?'}p={p['id']}")])
        img = image_url(con, p)
        if img:
            b['hero'] = {'type': 'image', 'url': img, 'size': 'full', 'aspectRatio': '1:1', 'aspectMode': 'cover'}
            b.pop('header', None); b.pop('styles', None)        # 商品圖上已經有 示範 和商品名稱，標題不要再寫一次（文字重複）
        bs.append(b)
    return lb._carousel(f'示範商城：{len(ps)} 款耗材 500ML', bs)

def _cart(con, uid, acc, lu, create=True):
    ensure(con)
    r = con.execute("SELECT * FROM shop_orders WHERE uid=? AND acc=? AND status='cart'", (uid, acc)).fetchone()
    if r or not create:
        return dict(r) if r else None
    buyer, ref, name = _buyer(con, lu)
    oid = _new_id()
    con.execute("INSERT INTO shop_orders(id,uid,acc,buyer,ref_id,name,status,created_at,updated_at,channel) VALUES(?,?,?,?,?,?,'cart',?,?,'line')", (oid, uid, acc, buyer, ref, name, c.now(), c.now()))
    return dict(con.execute('SELECT * FROM shop_orders WHERE id=?', (oid,)).fetchone())

def _new_id():
    return 'S' + c.now()[5:10].replace('-', '') + c.uid('')[-4:].upper()

def _summary(o):
    items = json.loads(o['items'] or '[]')
    return '\n'.join(f"・{i['name']} × {i['qty']}　NT$ {i['price'] * i['qty']:,}" for i in items) + f"\n合計 NT$ {sum(i['price'] * i['qty'] for i in items):,}"

def add(con, uid, acc, lu, pid, qty):
    import line_bot as lb
    buyer, _, _ = _buyer(con, lu)
    p = con.execute('SELECT * FROM shop_products WHERE id=? AND active=1 AND price>0', (pid,)).fetchone()
    if not buyer or not p:
        return lb._txt('這個商品目前沒有販售，請重新打「商城」看看 🙏')
    o = _cart(con, uid, acc, lu)
    items = json.loads(o['items'] or '[]'); hit = next((i for i in items if i['pid'] == pid), None)
    want = (hit['qty'] if hit else 0) + int(qty)
    if not in_stock(dict(p), want):
        return lb._txt(f"{p['name']} 目前只剩 {max(0, p['stock'] or 0)} 瓶 🙏")
    if hit:
        hit['qty'] = min(20, want)
    else:
        items.append({'pid': pid, 'name': f"{p['name']} {p['size']}", 'qty': max(1, min(20, int(qty))), 'price': unit_price(p, buyer)})
    total = sum(i['price'] * i['qty'] for i in items)
    con.execute('UPDATE shop_orders SET items=?,total=?,updated_at=? WHERE id=?', (json.dumps(items, ensure_ascii=False), total, c.now(), o['id']))
    bump(con, pid, 'add', int(qty))
    o = dict(con.execute('SELECT * FROM shop_orders WHERE id=?', (o['id'],)).fetchone())
    return [{'type': 'text', 'text': '已加入購物車 🛒\n' + _summary(o), 'quickReply': {'items': [
        {'type': 'action', 'action': {'type': 'postback', 'label': '繼續選購', 'data': 'a=shop', 'displayText': '繼續選購'}},
        {'type': 'action', 'action': {'type': 'postback', 'label': '結帳', 'data': 'a=shop_co', 'displayText': '結帳'}},
        {'type': 'action', 'action': {'type': 'postback', 'label': '清空購物車', 'data': 'a=shop_clear', 'displayText': '清空購物車'}}]}}]

def checkout(con, uid, acc, lu):
    import line_bot as lb
    o = _cart(con, uid, acc, lu, create=False)
    if not o or not json.loads(o['items'] or '[]'):
        return lb._txt('購物車是空的，打「商城」看看商品 😊')
    opts = DELIVERY.get(o['buyer'], DELIVERY['customer'])
    terms = ix.setting(con, 'shop_terms')
    return [{'type': 'text', 'text': '請確認訂單：\n' + _summary(o) + (f'\n\n{terms}' if terms else '') + '\n\n要怎麼拿呢？點下面選一個就送出 👇',
             'quickReply': {'items': [{'type': 'action', 'action': {'type': 'postback', 'label': t, 'data': f'a=shop_dl&v={k}', 'displayText': t}} for k, t in opts]}}]

def _notify_new(con, o):
    """新訂單：派單管理群一張卡（點選處理）；買家 LINE 收到確認（有 LINE 的）。"""
    import line_bot as lb
    con.execute('UPDATE shop_orders SET announced_at=? WHERE id=?', (c.now(), o['id']))
    g = lb.admin_group(con)
    who_ = {'worker': '夥伴', 'customer': '業主', 'guest': '網站訪客'}.get(o['buyer'], '')
    if g:
        rows = [lb._t(_summary(o), wrap=True), lb._row('取貨', o['delivery']), lb._row('編號', o['id']),
                lb._row('從哪裡', ('商城網站（已連 LINE，進度會自動通知他）' if o.get('uid') else '商城網站（⚠️ 沒連 LINE，系統通知不到，請打電話）') if o.get('channel') == 'web' else '官方 LINE')]
        if o.get('phone'):
            rows.append(lb._row('電話', o['phone']))
        if o.get('note'):
            rows.append(lb._row('備註', o['note'][:60]))
        m = lb.flex(f"🛒 商城訂單 {o['id']}：{o['name']} NT$ {o['total']:,}", lb._bubble(lb._header('🛒 商城訂單', f"{o['name'] or '—'}（{who_}）", '#2E7D32'), rows,
            [lb._btn('備好了，請他取貨', data=f"a=shop_st&o={o['id']}&v=ready"), lb._btn('已交付', data=f"a=shop_st&o={o['id']}&v=delivered", primary=True),
             lb._btn('已收款', data=f"a=shop_pay&o={o['id']}"), lb._btn('取消訂單', data=f"a=shop_st&o={o['id']}&v=cancelled")]))
        lb.queue(con, lb.WORK_ACCOUNT, g, 'shop_order', m[0]['altText'], [lb.ping(con, '商城有新訂單 🛒')] + m)

def _order_url(con, o):
    import line_bot as lb
    return f"{lb.BASE_URL}/mall/o/{o['token']}" if o.get('token') else ''

def bank(con):
    """商城匯款資訊（已下單卻沒看到匯款資訊；規則是一律先匯款再給貨）。用示範服務店報價單的匯款資料。"""
    import booking
    co = booking.company(con, 'demoshop')
    return {k: co.get(k) or '' for k in ('bank', 'holder', 'account')} if co.get('account') else {}

def _done_msg(con, o):
    import line_bot as lb, linepay
    url = _order_url(con, o)
    btns = ([lb._btn(f"用 LINE Pay 付 NT$ {o['total']:,}", uri=f"{lb.BASE_URL}/pay/shop?i={o['id']}&s={linepay.sign(con, 'shop', o['id'])}", primary=True)]
            if linepay.live(con, 'demoshop') and o['total'] > 0 and not o.get('paid_at') else []) + ([lb._btn('看訂單進度', uri=url)] if url else [])
    terms = ix.setting(con, 'shop_terms')
    return lb.flex(f"訂單已送出 ✅ {o['id']}", lb._bubble(lb._header('訂單已送出 ✅', o['id'], '#2E7D32'),
        [lb._t(_summary(o), wrap=True), lb._row('取貨', o['delivery'])] + ([lb._t(terms, color=lb.SUB, size='xs', wrap=True, margin='md')] if terms else [])
        + ([lb._t('匯款資訊', weight='bold', size='sm', margin='md'), lb._row('銀行', bk['bank']), lb._row('戶名', bk['holder']), lb._row('帳號', bk['account']),
            lb._t(f"金額 NT$ {o['total']:,}；匯好請在這裡傳「帳號末五碼」，我們確認後就幫您備貨 🙏", color=lb.SUB, size='xs', wrap=True)] if (bk := bank(con)) and not o.get('paid_at') and o['total'] > 0 else []), btns,
        '示範服務店會跟您確認，備好了會在這裡通知您，謝謝您！'))

def place(con, uid, acc, lu, how):
    import line_bot as lb
    o = _cart(con, uid, acc, lu, create=False)
    if not o or not json.loads(o['items'] or '[]'):
        return lb._txt('購物車是空的，打「商城」看看商品 😊')
    label = dict(DELIVERY.get(o['buyer'], DELIVERY['customer'])).get(how, how)
    con.execute('SAVEPOINT sp')
    try:
        _reserve(con, o)
        con.execute('RELEASE sp')
    except ValueError as e:
        con.execute('ROLLBACK TO sp'); con.execute('RELEASE sp')
        return lb._txt(str(e))
    con.execute("UPDATE shop_orders SET status='new',delivery=?,token=?,updated_at=? WHERE id=?", (label, secrets.token_urlsafe(12), c.now(), o['id']))
    o = dict(con.execute('SELECT * FROM shop_orders WHERE id=?', (o['id'],)).fetchone())
    _notify_new(con, o)
    return _done_msg(con, o)

def web_order(con, p, ip=''):
    """商城網站下單（免註冊）：價格、庫存一律由伺服器算；同一個 IP／電話一小時最多 5 張（擋灌單）。"""
    ensure(con)
    kind, ref, name0, phone0, acc, uid = who(con, str(p.get('t') or ''), str(p.get('w') or ''), str(p.get('l') or ''))
    name = (str(p.get('name') or '').strip() or name0)[:30]
    phone = re.sub(r'[^\d\-+ ]', '', str(p.get('phone') or '') or phone0)[:20]
    if not name or len(re.sub(r'\D', '', phone)) < 8:
        raise ValueError('請填稱呼和手機號碼，我們才聯絡得到您')
    since = (dt.datetime.now(c.TZ) - dt.timedelta(hours=1)).isoformat()
    if con.execute("SELECT COUNT(*) FROM shop_orders WHERE channel='web' AND created_at>=? AND (ip=? OR phone=?)", (since, ip, phone)).fetchone()[0] >= 5:
        raise ValueError('下單太頻繁了，請稍後再試，或直接在官方 LINE 留言 🙏')
    opts = dict(delivery_options(kind))
    if p.get('delivery') not in opts:
        raise ValueError('請選取貨方式')
    items, seen = [], set()
    for it in (p.get('items') or [])[:20]:
        pid, qty = str(it.get('pid', '')), int(it.get('qty') or 0)
        if pid in seen or qty <= 0:
            continue
        seen.add(pid)
        pr = con.execute('SELECT * FROM shop_products WHERE id=? AND active=1 AND price>0', (pid,)).fetchone()
        if not pr:
            raise ValueError('購物車裡有商品已下架，請重新整理頁面 🙏')
        items.append({'pid': pid, 'name': f"{pr['name']} {pr['size']}", 'qty': min(20, qty), 'price': unit_price(pr, kind)})
    if not items:
        raise ValueError('購物車是空的')
    total = sum(i['price'] * i['qty'] for i in items)
    oid = _new_id()
    con.execute("INSERT INTO shop_orders(id,uid,acc,buyer,ref_id,name,items,total,delivery,status,note,created_at,updated_at,phone,token,channel,ip) "
                "VALUES(?,?,?,?,?,?,?,?,?,'cart',?,?,?,?,?,'web',?)",
                (oid, uid, 'demoshop', kind, ref, name, json.dumps(items, ensure_ascii=False), total, opts[p['delivery']], str(p.get('note') or '')[:200], c.now(), c.now(),
                 phone, secrets.token_urlsafe(12), ip[:60]))
    o = dict(con.execute('SELECT * FROM shop_orders WHERE id=?', (oid,)).fetchone())
    _reserve(con, o)                       # 庫存不夠會整筆回復（呼叫端在交易裡）
    con.execute("UPDATE shop_orders SET status='new' WHERE id=?", (oid,))
    o = dict(con.execute('SELECT * FROM shop_orders WHERE id=?', (oid,)).fetchone())
    c.audit(con, 'shop_web_order', 'shop_orders', oid, after={'total': total, 'buyer': kind, 'ip': ip[:60]})
    if uid:                                # 有 LINE（從 LINE 打開、業主／夥伴連結）：馬上通知派單管理群＋LINE 確認
        _notify_new(con, o)
        import line_bot as lb
        m = _done_msg(con, o); lb.queue(con, 'demoshop', uid, 'shop_done', m[0]['altText'], m)
    # 沒有 LINE＝備好貨也聯絡不到（幽靈單）→ 先不通知管理群；客戶在訂單頁連上 LINE 才成立，24 小時沒連自動取消（expire_unlinked）
    return {'id': oid, 'token': o['token'], 'total': total, 'line_on': bool(uid)}

def order_view(con, token):
    ensure(con)
    o = con.execute("SELECT * FROM shop_orders WHERE token=? AND token!='' AND status!='cart'", (token or '',)).fetchone()
    if not o:
        raise PermissionError('找不到這筆訂單，請從 LINE 或下單完成頁重新打開')
    import linepay, urllib.parse, line_bot as lb
    name = o['name'] or ''
    wait = not o['uid'] and o['status'] == 'new' and not o['paid_at']          # 還沒連 LINE：連上才成立
    bid = lb.basic_id(con, 'demoshop')
    exp = (dt.datetime.fromisoformat(o['created_at']) + dt.timedelta(hours=UNLINKED_HOURS)).strftime('%m/%d %H:%M') if wait else ''
    return {'line_wait': wait, 'line_code': f"訂單 {o['id']} {o['token'][:6]}" if not o['uid'] else '', 'line_id': bid,
            'line_qr': f"/mall/qr/{o['token']}" if not o['uid'] else '', 'add_friend': f"https://line.me/R/ti/p/{urllib.parse.quote(bid)}", 'expires': exp,
            'auto_cancelled': o['status'] == 'cancelled' and (o['handled_by'] or '').startswith('系統'),
            'id': o['id'], 'status': o['status'], 'status_text': '等您連上官方 LINE' if wait else STATUS.get(o['status'], o['status']), 'items': json.loads(o['items'] or '[]'), 'total': o['total'],
            'delivery': o['delivery'], 'paid': bool(o['paid_at']), 'paid_how': o['paid_how'] or '', 'created_at': o['created_at'][:16].replace('T', ' '),
            'name': name[:1] + '○' * max(0, len(name) - 1), 'terms': ix.setting(con, 'shop_terms') or '',
            'bank': bank(con) if not o['paid_at'] and o['status'] != 'cancelled' and o['total'] > 0 else {}, 'line_link': line_link(con, o), 'line_on': bool(o['uid']),
            'pay_url': f"/pay/shop?i={o['id']}&s={linepay.sign(con, 'shop', o['id'])}" if linepay.live(con, 'demoshop') and not o['paid_at'] and o['status'] not in ('cancelled',) and o['total'] > 0 else ''}

def line_link(con, o):
    """訂單還沒連到 LINE：回傳「一鍵開官方 LINE、訊息已打好」的網址（傳出去就綁定這張訂單）。"""
    import urllib.parse, line_bot as lb
    if o['uid'] or not o['token']:
        return ''
    return f"https://line.me/R/oaMessage/{urllib.parse.quote(lb.basic_id(con, 'demoshop'))}/?{urllib.parse.quote('訂單 ' + o['id'] + ' ' + o['token'][:6])}"

def link_line(con, acc, uid, oid, code):
    """買家在官方 LINE 傳「訂單 S1012ABCD 驗證碼」→ 這張訂單之後的進度都傳到這個 LINE。"""
    import line_bot as lb
    ensure(con)
    if acc != 'demoshop':
        return lb._txt('商城訂單請在示範服務店官方 LINE 傳喔 🙏')
    o = con.execute("SELECT * FROM shop_orders WHERE id=? AND status!='cart'", (oid,)).fetchone()
    same = lambda s: s.translate(str.maketrans('lIoO', '1100'))          # 手打驗證碼 1／l／I、0／O 看錯也認
    if not o or not o['token'] or same(o['token'][:6]) != same(code or ''):
        return lb._txt('找不到這張訂單，請從訂單進度頁再按一次「用 LINE 接收通知」🙏')
    if o['uid'] and o['uid'] != uid:
        return lb._txt('這張訂單已經連到別的 LINE 了，有問題請直接留言 🙏')
    if o['status'] == 'cancelled':
        return lb._txt(f"訂單 {oid} 已經取消了（" + (f'超過 {UNLINKED_HOURS} 小時沒連上 LINE，系統自動取消、庫存已釋出' if (o['handled_by'] or '').startswith('系統') else '有問題請直接留言')
                       + '）。想買的話打「商城」重新下單，從 LINE 打開商城下單會自動連上 😊')
    con.execute("UPDATE shop_orders SET uid=?,acc='demoshop' WHERE id=?", (uid, oid))
    c.audit(con, 'shop_line_link', 'shop_orders', oid, after={'uid': uid[:6] + '…'})
    o = dict(con.execute('SELECT * FROM shop_orders WHERE id=?', (oid,)).fetchone())
    if not o['announced_at']:
        _notify_new(con, o)                # 連上 LINE 才算成立 → 這時才通知派單管理群
    return lb._txt(f"✅ 已連上！訂單 {oid} 成立了，確認取貨、付款、備好了都會在這裡通知您。\n（聊天室上方如果有「加入好友」，請按一下，才收得到通知 🙏）") + my_orders(con, uid, 'demoshop')

def my_orders(con, uid, acc):
    import line_bot as lb
    ensure(con)
    os_ = c.rows(con, "SELECT * FROM shop_orders WHERE uid=? AND acc=? AND status!='cart' ORDER BY created_at DESC LIMIT 5", (uid, acc))
    if not os_:
        return lb._txt('還沒有商城訂單，打「商城」看看商品 😊')
    bs = [lb._bubble(lb._header('我的商城訂單', o['id'], '#2E7D32'),
                     [lb._t(_summary(o), wrap=True), lb._row('狀態', STATUS.get(o['status'], o['status'])), lb._row('付款', '已付款' if o['paid_at'] else '還沒付款'), lb._row('取貨', o['delivery'])],
                     [lb._btn('看訂單進度', uri=_order_url(con, o))] if o['token'] else []) for o in os_]
    return lb._carousel('我的商城訂單', bs)

def paid(con, oid, how, by=''):
    """商城訂單已付款（LINE Pay 自動／同事按）。"""
    import line_bot as lb
    ensure(con)
    o = con.execute('SELECT * FROM shop_orders WHERE id=?', (oid,)).fetchone()
    if not o or o['paid_at']:
        return
    con.execute('UPDATE shop_orders SET paid_at=?,paid_how=? WHERE id=?', (c.now(), how + (f'（{by}）' if by else ''), oid))
    if not o['announced_at']:              # 付了錢但沒連 LINE：照樣通知派單管理群（卡上有電話）
        _notify_new(con, dict(con.execute('SELECT * FROM shop_orders WHERE id=?', (oid,)).fetchone()))
    lb.admin_notify(con, f"💚 商城訂單 {oid} 已付款 NT$ {o['total']:,}", [('買家', o['name'] or '—'), ('方式', how)], '/#shop', '#2E7D32')

def expire_unlinked(con):
    """網站訂單 24 小時沒連上官方 LINE、也沒付款 → 自動取消、庫存加回（聯絡不到人的幽靈單不留給管理層追；）。"""
    ensure(con)
    since = (dt.datetime.now(c.TZ) - dt.timedelta(hours=UNLINKED_HOURS)).isoformat(timespec='seconds')
    n = 0
    for o in c.rows(con, "SELECT * FROM shop_orders WHERE status='new' AND channel='web' AND COALESCE(uid,'')='' AND COALESCE(paid_at,'')='' AND created_at<?", (since,)):
        con.execute("UPDATE shop_orders SET status='cancelled',handled_by=?,updated_at=? WHERE id=?", (f'系統（{UNLINKED_HOURS} 小時沒連 LINE）', c.now(), o['id']))
        _release(con, o)
        c.audit(con, 'shop_unlinked_cancel', 'shop_orders', o['id'], after={'total': o['total']})
        n += 1
    return n

def qr_svg(con, token):
    """電腦下單：訂單頁的 QR code（手機 LINE 掃 → 打開示範服務店官方 LINE、訊息已打好，按傳送就連上）。"""
    ensure(con)
    o = con.execute("SELECT * FROM shop_orders WHERE token=? AND token!='' AND status!='cart'", (token or '',)).fetchone()
    url = line_link(con, o) if o else ''
    if not url:
        raise PermissionError('這張訂單已經連上 LINE 了')
    import io, qrcode, qrcode.image.svg
    b = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2).save(b)
    return b.getvalue()

def clear(con, uid, acc):
    con.execute("DELETE FROM shop_orders WHERE uid=? AND acc=? AND status='cart'", (uid, acc))

def set_status(con, oid, status, by):
    import line_bot as lb
    ensure(con)
    o = con.execute('SELECT * FROM shop_orders WHERE id=?', (oid,)).fetchone()
    if not o:
        return '找不到這筆訂單'
    if status not in ('ready', 'delivered', 'cancelled', 'new'):
        return '狀態不符'
    if o['status'] in ('delivered', 'cancelled'):
        return f"這筆已經是「{STATUS[o['status']]}」了（{o['handled_by']}）"
    if o['status'] == status:
        return f"這筆已經是「{STATUS.get(status, status)}」了"
    con.execute('UPDATE shop_orders SET status=?,handled_by=?,updated_at=? WHERE id=?', (status, by, c.now(), oid))
    if status in ('ready', 'delivered'):     # 稽核 F7：等連 LINE 的單由管理層直接處理 → 算成立（營收、待處理一致）
        con.execute("UPDATE shop_orders SET announced_at=? WHERE id=? AND COALESCE(announced_at,'')=''", (c.now(), oid))
    if status == 'cancelled':
        _release(con, dict(o))            # 取消：庫存加回
    if o['uid']:
        msg = {'cancelled': f"您的商城訂單 {oid} 已取消，有問題請直接留言 🙏", 'ready': f"您的商城訂單 {oid} 備好了 😊 取貨方式：{o['delivery']}",
               'delivered': f"您的商城訂單 {oid} 已交付，謝謝您！用完想再買，打「商城」就可以 🧴"}.get(status)
        if msg:
            lb.queue(con, o['acc'], o['uid'], 'shop_status', '商城訂單更新', lb._txt(msg))
    elif status in ('ready', 'cancelled'):
        return f"✅ {oid}：{STATUS.get(status, status)}（{by}）\n⚠️ 買家沒連 LINE，系統通知不到" + (f"，請打電話 {o['phone']}" if o['phone'] else '')
    return f"✅ {oid}：{STATUS.get(status, status)}（{by}）"

def set_product(con, pid, p, by):
    """管理員改商品：價格、夥伴價、上下架、說明欄位、問題分類。"""
    ensure(con)
    cur = con.execute('SELECT * FROM shop_products WHERE id=?', (pid,)).fetchone()
    if not cur:
        raise ValueError('找不到商品')
    price = int(p['price']) if str(p.get('price', '')).strip() else None
    wp = int(p['worker_price']) if str(p.get('worker_price', '')).strip() else None
    active = 1 if p.get('active') and price else 0
    ch = {'price': price, 'worker_price': wp, 'active': active, 'note': str(p.get('note', cur['note']) or '')[:100]}
    for k in CONTENT + ('problems', 'name', 'size'):
        if k in p:
            ch[k] = str(p.get(k) or '').strip()[:1500]
    con.execute('UPDATE shop_products SET ' + ','.join(f'{k}=?' for k in ch) + ' WHERE id=?', (*ch.values(), pid))
    c.audit(con, 'shop_product', 'shop_products', pid, before=dict(cur), after={**{k: v for k, v in ch.items() if k not in CONTENT}, 'by': by})
    return {'ok': True, 'active': active}

def set_image(con, pid, raw, mime, by):
    ensure(con)
    if not con.execute('SELECT 1 FROM shop_products WHERE id=?', (pid,)).fetchone():
        raise ValueError('找不到商品')
    if mime not in ('image/jpeg', 'image/png', 'image/webp') or len(raw) > 3 * 1024 * 1024:
        raise ValueError('請上傳 3MB 以內的 JPG／PNG 照片')
    aid = c.uid('shimg')                  # 商品照片：存在 shop_images（照片表要掛工單，商品用不到）
    con.execute('INSERT INTO shop_images(id,mime,content,created_at) VALUES(?,?,?,?)', (aid, mime, raw, c.now()))
    old = con.execute('SELECT image_id FROM shop_products WHERE id=?', (pid,)).fetchone()[0]
    con.execute('UPDATE shop_products SET image_id=?,image_mime=? WHERE id=?', (aid, mime, pid))
    if old:
        con.execute('DELETE FROM shop_images WHERE id=?', (old,))
    c.audit(con, 'shop_image', 'shop_products', pid, after={'by': by})
    return {'ok': True}

def image(con, pid):
    r = con.execute("SELECT i.mime,i.content FROM shop_products p JOIN shop_images i ON i.id=p.image_id WHERE p.id=? AND p.active=1", (pid,)).fetchone()
    if not r:
        raise PermissionError('沒有照片')
    return r[0], r[1]

def stats(con):
    """中控台：本月營收、訂單、熱銷、30 天漏斗、低庫存、通路。"""
    ensure(con)
    m0 = c.now()[:7]; d30 = (dt.datetime.now(c.TZ) - dt.timedelta(days=30)).date().isoformat()
    live = c.rows(con, "SELECT * FROM shop_orders WHERE status NOT IN ('cart','cancelled') AND COALESCE(announced_at,'')!='' AND created_at LIKE ?", (m0 + '%',))     # 還沒連 LINE 的不算
    qty, rev = {}, {}
    for o in live:
        for i in json.loads(o['items'] or '[]'):
            qty[i['name']] = qty.get(i['name'], 0) + i['qty']; rev[i['name']] = rev.get(i['name'], 0) + i['qty'] * i['price']
    ev = {k: n for k, n in con.execute('SELECT kind,SUM(n) FROM shop_events WHERE day>=? GROUP BY kind', (d30,))}
    orders30 = con.execute("SELECT COUNT(*) FROM shop_orders WHERE status NOT IN ('cart','cancelled') AND created_at>=?", (d30,)).fetchone()[0]
    use30 = con.execute("SELECT COALESCE(-SUM(delta),0) FROM shop_stock WHERE kind='use' AND at>=?", (d30,)).fetchone()[0]
    return {'month': m0, 'revenue': sum(o['total'] for o in live), 'paid': sum(o['total'] for o in live if o['paid_at']), 'orders': len(live),
            'open': con.execute("SELECT COUNT(*) FROM shop_orders WHERE status IN ('new','ready') AND COALESCE(announced_at,'')!=''").fetchone()[0],
            'wait_line': con.execute("SELECT COUNT(*) FROM shop_orders WHERE status='new' AND COALESCE(announced_at,'')=''").fetchone()[0],
            'by_buyer': {k: sum(1 for o in live if o['buyer'] == k) for k in ('customer', 'worker', 'guest')},
            'by_channel': {k: sum(1 for o in live if (o.get('channel') or 'line') == k) for k in ('line', 'web')},
            'top': sorted([{'name': k, 'qty': v, 'revenue': rev[k]} for k, v in qty.items()], key=lambda x: -x['qty'])[:5],
            'funnel': {'view': ev.get('view', 0), 'add': ev.get('add', 0), 'order': orders30}, 'team_use_30d': use30,
            'low_stock': [{'id': p['id'], 'name': p['name'], 'stock': p['stock']} for p in products(con, False) if p['stock'] is not None and p['stock'] <= 5]}

def state(con):
    ensure(con)
    return {'shop_products': products(con, False), 'shop_orders': c.rows(con, "SELECT * FROM shop_orders WHERE status!='cart' ORDER BY created_at DESC LIMIT 200"),
            'shop_stats': stats(con), 'shop_stock': c.rows(con, 'SELECT * FROM shop_stock ORDER BY id DESC LIMIT 50')}
