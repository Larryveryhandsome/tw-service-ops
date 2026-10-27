"""品牌（官方 LINE 帳號）→ 底下的業務線（服務、陪玩陪讀、私人管家…）。新業務上線只要在後台「品牌與業務」該品牌下新增一條業務線。
每個品牌：名稱、隸屬公司、客戶來源標籤、品牌色、官方 LINE（webhook＝/line/webhook/<id>）、業務型態
  - cleaning：服務（規模＋現況照片 → 系統初估 → 主管審核 → 報價單）
  - hourly：時數制（陪玩陪讀、私人管家…：日期＋時數＋人數 → 每小時價格初估 → 主管審核 → 報價單）
TITLES／NAMES 是記憶體快取（卡片抬頭、訊息用），新增或修改品牌時 reload()。"""
import json
import re
import core as c

TITLES = {}      # id → 卡片抬頭（例：DEMO 示範服務店）
NAMES = {}       # id → 對客戶的名稱（例：示範服務店）
CHANNELS = {}    # 來源管道 → 品牌 id（例：企業線 → bizline）
SEED = [
    {'id': 'demoshop', 'name': '示範服務店', 'title': 'DEMO 示範服務店', 'company': '示範服務股份有限公司', 'channel': 'DEMO', 'color': '#6B5A4E', 'kind': 'cleaning', 'sort': 1,
     'services': ['一般到府服務', '年度保養／細清', '進階專案', '搬遷前後服務', '店家服務', '店面／辦公室服務', '大型案場服務']},
    {'id': 'bizline', 'name': '企業服務線', 'title': '企業服務線', 'company': '示範服務股份有限公司', 'channel': '企業線', 'color': '#4E7D5A', 'kind': 'cleaning', 'sort': 2,
     'services': ['店家服務', '年度保養／細清', '一般到府服務']},
    {'id': 'homeline', 'name': '生活小幫手', 'title': '生活小幫手', 'company': '示範服務股份有限公司', 'channel': '生活線', 'color': '#B5677A', 'kind': 'cleaning', 'sort': 3,
     'services': ['一般到府服務', '年度保養／細清']},
]
FIELDS = ('name', 'title', 'company', 'channel', 'color', 'kind', 'services', 'hourly', 'active', 'sort', 'intro')

def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS brands (id TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT DEFAULT '', company TEXT DEFAULT '',
                   channel TEXT DEFAULT '', color TEXT DEFAULT '#6B5A4E', kind TEXT DEFAULT 'cleaning', services TEXT DEFAULT '[]', hourly TEXT DEFAULT '{}',
                   intro TEXT DEFAULT '', active INTEGER DEFAULT 1, sort INTEGER DEFAULT 9, created_at TEXT DEFAULT '')''')
    for b in SEED:
        if not con.execute('SELECT 1 FROM brands WHERE id=?', (b['id'],)).fetchone():
            con.execute('INSERT INTO brands(id,name,title,company,channel,color,kind,services,sort,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (b['id'], b['name'], b['title'], b['company'], b['channel'], b['color'], b['kind'], json.dumps(b['services'], ensure_ascii=False), b['sort'], c.now()))
        if not con.execute('SELECT 1 FROM line_accounts WHERE id=?', (b['id'],)).fetchone():
            con.execute('INSERT INTO line_accounts(id,name,basic_id,mode) VALUES(?,?,?,?)', (b['id'], b['name'], '', 'read_only'))
    con.execute('''CREATE TABLE IF NOT EXISTS business_lines (id TEXT PRIMARY KEY, brand_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT DEFAULT 'cleaning',
                   services TEXT DEFAULT '[]', hourly TEXT DEFAULT '{}', intro TEXT DEFAULT '', active INTEGER DEFAULT 1, sort INTEGER DEFAULT 9, created_at TEXT DEFAULT '')''')
    for b in all_(con, active_only=False):          # 每個品牌至少一條「服務」業務線（舊資料搬過來）
        if not con.execute('SELECT 1 FROM business_lines WHERE brand_id=?', (b['id'],)).fetchone():
            con.execute('INSERT INTO business_lines(id,brand_id,name,kind,services,sort,created_at) VALUES(?,?,?,?,?,?,?)',
                        (f"{b['id']}-clean", b['id'], '服務', 'cleaning', json.dumps(b['services'], ensure_ascii=False), 1, c.now()))
            if b['id'] == 'demoshop':    # 示範服務店旗下原本就有的服務；價格還沒定，先停用，店長設好每小時價格再啟用
                for i, (lid, nm, svc) in enumerate((('demoshop-play', '陪玩陪讀', ['陪玩', '陪讀', '課後陪伴']), ('demoshop-butler', '私人管家', ['私人管家', '生活代辦'])), 2):
                    con.execute('INSERT INTO business_lines(id,brand_id,name,kind,services,hourly,active,sort,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                                (lid, 'demoshop', nm, 'hourly', json.dumps(svc, ensure_ascii=False), json.dumps({'rate': 0, 'min_hours': 2, 'unit': '位'}), 0, i, c.now()))
    for bid in ('demoshop', 'homeline'):         # 新增垃圾清收（只建一次；先停用＝待上線，店長核准公告發出時才同步上線，garbage／broadcast launch）
        if con.execute('SELECT 1 FROM brands WHERE id=?', (bid,)).fetchone():
            con.execute('INSERT OR IGNORE INTO business_lines(id,brand_id,name,kind,services,intro,active,sort,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                        (f'{bid}-trash', bid, '垃圾代丟', 'trash', json.dumps(['垃圾代丟'], ensure_ascii=False), '每週固定幾天，晚上 8 點前把垃圾放門口，當晚收走（月費方案；正常家庭的量）。', 0, 2, c.now()))
    reload(con)

def lines(con, bid, active_only=True):
    return [_row(r) for r in con.execute('SELECT * FROM business_lines WHERE brand_id=?' + (' AND active=1' if active_only else '') + ' ORDER BY sort,created_at', (bid,))]

def line(con, lid):
    r = con.execute('SELECT * FROM business_lines WHERE id=?', (lid or '',)).fetchone()
    return _row(r) if r else None

def line_for(con, bid, lid=None):
    """預約用的業務線：指定的（要屬於這個品牌且啟用），否則這個品牌第一條。"""
    ln = line(con, lid)
    if ln and ln['brand_id'] == bid and ln['active']:
        return ln
    ls = lines(con, bid)
    return ls[0] if ls else {'id': '', 'brand_id': bid, 'name': '服務', 'kind': 'cleaning', 'services': [], 'hourly': {}, 'intro': ''}

def save_line(con, p, actor):
    """新增或修改業務線（屬於某個品牌）：名稱、型態（服務／時數制）、服務項目、介紹、時數制價格。"""
    bid = p.get('brand_id')
    if not get(con, bid):
        raise ValueError('請選品牌')
    name = str(p.get('name', '')).strip()[:20]
    if not name:
        raise ValueError('請填業務名稱')
    kind = p.get('kind') if p.get('kind') in ('cleaning', 'hourly') else 'cleaning'
    _old = line(con, p.get('id')) if p.get('id') else None
    if _old and _old['kind'] in ('bnb', 'homeline', 'trash') and kind != 'hourly':
        kind = _old['kind']                  # 企業線房務、生活線、垃圾清收是專屬估價，中控台改名稱／介紹不會變成一般服務
    services = [str(s).strip()[:20] for s in (p.get('services') or []) if str(s).strip()][:20]
    hourly = {}
    if kind == 'hourly':
        h = p.get('hourly') or {}
        hourly = {'rate': max(0, int(float(h.get('rate') or 0))), 'min_hours': max(0.5, float(h.get('min_hours') or 2)),
                  'unit': str(h.get('unit') or '位')[:6], 'people_label': str(h.get('people_label') or '人數')[:12],
                  'note_label': str(h.get('note_label') or '需求說明（年齡、內容、注意事項）')[:40], 'photos': bool(h.get('photos'))}
    active = 1 if p.get('active', True) else 0
    if kind == 'hourly' and active and not hourly['rate']:
        raise ValueError('時數制要先填每小時價格才能啟用')
    row = {'brand_id': bid, 'name': name, 'kind': kind, 'services': json.dumps(services, ensure_ascii=False), 'hourly': json.dumps(hourly, ensure_ascii=False),
           'intro': str(p.get('intro', '')).strip()[:300], 'active': active, 'sort': int(p.get('sort') or 9)}
    lid = p.get('id')
    old = line(con, lid) if lid else None
    if old:
        con.execute('UPDATE business_lines SET ' + ','.join(f'{k}=?' for k in row) + ' WHERE id=?', (*row.values(), lid))
    else:
        lid = f"{bid}-{c.uid('ln')[-6:]}"
        con.execute('INSERT INTO business_lines(id,' + ','.join(row) + ',created_at) VALUES(?,' + ','.join('?' * len(row)) + ',?)', (lid, *row.values(), c.now()))
    c.audit(con, 'line_save' if old else 'line_create', 'business_lines', lid, before=old, after={**row, 'by': actor})
    return line(con, lid)

def _row(r):
    d = dict(r)
    for k, dflt in (('services', []), ('hourly', {})):
        try:
            d[k] = json.loads(d[k] or '')
        except ValueError:
            d[k] = dflt
    return d

def reload(con):
    TITLES.clear(); NAMES.clear(); CHANNELS.clear()
    for b in all_(con, active_only=False):
        TITLES[b['id']] = b['title'] or b['name']; NAMES[b['id']] = b['name']
        if b.get('channel'):
            CHANNELS[b['channel']] = b['id']

def of_customer(cu):
    """客戶屬於哪個品牌：綁過的 LINE 帳號優先，其次看來源管道（企業線／生活線），其餘算示範服務店。"""
    _ensure()
    la = (cu or {}).get('line_account') or ''
    return la if la in NAMES else CHANNELS.get((cu or {}).get('channel') or '', 'demoshop')

def cust_sql(alias='customers'):
    """SQL 版的 of_customer：WHERE {cust_sql()}=? 只找同品牌的客戶（品牌資料不混用）。"""
    return (f"COALESCE(NULLIF({alias}.line_account,''), (SELECT b.id FROM brands b WHERE b.channel={alias}.channel AND b.channel!=''), 'demoshop')")

def all_(con, active_only=True):
    return [_row(r) for r in con.execute('SELECT * FROM brands' + (' WHERE active=1' if active_only else '') + ' ORDER BY sort,created_at')]

def get(con, bid):
    r = con.execute('SELECT * FROM brands WHERE id=?', (bid,)).fetchone()
    return _row(r) if r else None

def valid(bid):
    _ensure()
    return bid in NAMES

def pick(bid, default='demoshop'):
    _ensure()                  # 獨立腳本也要先讀品牌，不然全部會被當成示範服務店
    return bid if bid in NAMES else default

def _ensure():
    if not NAMES:              # 還沒載入（例如獨立腳本）就自己讀一次，避免品牌名稱跑成示範服務店
        import contextlib
        try:
            with contextlib.closing(c.connect()) as con:
                reload(con)
        except Exception:
            pass

def name(bid):
    _ensure()
    return NAMES.get(bid) or NAMES.get('demoshop') or '示範服務店'

def of_channel(con, channel):
    r = con.execute('SELECT id FROM brands WHERE channel=? AND channel!=""', (channel or '',)).fetchone()
    return r[0] if r else 'demoshop'

def channel(con, bid):
    r = con.execute('SELECT channel FROM brands WHERE id=?', (bid,)).fetchone()
    return (r[0] if r else '') or 'DEMO'

def save(con, p, actor):
    """新增或修改品牌（管理員）。id 只能英文小寫＋數字，建立後不能改（LINE webhook 網址用它）。"""
    bid = str(p.get('id', '')).strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9]{1,15}', bid):
        raise ValueError('品牌代號請用 2–16 個英文小寫或數字（例：play、butler），建立後不能改')
    name = str(p.get('name', '')).strip()[:30]
    if not name:
        raise ValueError('請填品牌名稱')
    old = get(con, bid)
    row = {'name': name, 'title': str(p.get('title') or name).strip()[:30], 'channel': str(p.get('channel') or name[:4]).strip()[:10],
           'intro': str(p.get('intro', '')).strip()[:300], 'active': 1 if p.get('active', True) else 0, 'sort': int(p.get('sort') or 9)}
    if not old:
        row.update(company='示範服務股份有限公司', color='#6B5A4E', kind='cleaning')
    if old:
        con.execute('UPDATE brands SET ' + ','.join(f'{k}=?' for k in row) + ' WHERE id=?', (*row.values(), bid))
    else:
        con.execute('INSERT INTO brands(id,' + ','.join(row) + ',created_at) VALUES(?,' + ','.join('?' * len(row)) + ',?)', (bid, *row.values(), c.now()))
        con.execute('INSERT INTO business_lines(id,brand_id,name,kind,services,sort,created_at) VALUES(?,?,?,?,?,?,?)', (f'{bid}-clean', bid, '服務', 'cleaning', '[]', 1, c.now()))
        if not con.execute('SELECT 1 FROM line_accounts WHERE id=?', (bid,)).fetchone():
            con.execute('INSERT INTO line_accounts(id,name,basic_id,mode) VALUES(?,?,?,?)', (bid, name, '', 'read_only'))
    con.execute('UPDATE line_accounts SET name=? WHERE id=?', (name, bid))
    c.audit(con, 'brand_save' if old else 'brand_create', 'brands', bid, before=old, after={**row, 'by': actor})
    reload(con)
    return get(con, bid)

def overview(con):
    """後台「品牌與業務」：每個品牌＋LINE 接線狀態＋客戶數。"""
    import integrations as ix
    out = []
    for b in all_(con, active_only=False):
        la = con.execute('SELECT basic_id FROM line_accounts WHERE id=?', (b['id'],)).fetchone()
        out.append({**b, 'basic_id': la[0] if la else '', 'line_ready': bool(ix.secret(con, 'line_token_' + b['id']) and ix.secret(con, 'line_secret_' + b['id'])),
                    'webhook': f"https://ops.example.com/line/webhook/{b['id']}",
                    'customers': con.execute("SELECT COUNT(*) FROM customers WHERE channel=? AND status IN ('active','paused')", (b['channel'],)).fetchone()[0],
                    'menu': ix.setting(con, f"customer_richmenu_{b['id']}"), 'lines': lines(con, b['id'], active_only=False)})
    return out

def connect_line(con, bid, secret, token, actor):
    """管理員貼上 Channel secret／Channel access token → 加密保存 → 用 LINE API 驗證並設定 webhook。"""
    import integrations as ix, line_bot
    if not get(con, bid):
        raise ValueError('找不到這個品牌')
    secret, token = str(secret or '').strip(), str(token or '').strip()
    if not re.fullmatch(r'[0-9a-f]{32}', secret):
        raise ValueError('Channel secret 應該是 32 個英數字')
    if len(token) < 100:
        raise ValueError('Channel access token 長度不對（請按 LINE Developers 的 Issue 取得長期 token）')
    ix.put_secret(con, 'line_secret_' + bid, secret); ix.put_secret(con, 'line_token_' + bid, token)
    info = line_bot.api(con, '/v2/bot/info', acc=bid)
    url = f"https://ops.example.com/line/webhook/{bid}"
    line_bot.api(con, '/v2/bot/channel/webhook/endpoint', {'endpoint': url}, method='PUT', acc=bid)
    con.execute('UPDATE line_accounts SET basic_id=?,verified_at=?,webhook=? WHERE id=?', (info.get('basicId', ''), c.now(), url, bid))
    c.audit(con, 'brand_line', 'brands', bid, after={'basic_id': info.get('basicId'), 'by': actor})
    return {'basic_id': info.get('basicId'), 'name': info.get('displayName'), 'webhook': url,
            'note': '已設定 webhook 網址；請到 LINE Developers 該頻道的 Messaging API 分頁把「Use webhook」打開。'}
