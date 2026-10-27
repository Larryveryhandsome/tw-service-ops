"""試算表／檔案匯入（「希望有檔案或試算表直接代入資料」，要匯入客戶、夥伴、工單、報價品項「都有」）。

中控台「更多 › 資料匯入」：選類型 → 上傳 Excel（.xlsx）或 CSV → 先預覽（每一列：新增／更新／跳過＋原因）→ 按「確認匯入」才寫入。
・欄位用表頭認（中文常見寫法都認得，例：姓名／名稱／客戶、電話／手機…），多的欄位不管。
・客戶、夥伴：電話（後 9 碼）對到既有的 → 只補空白欄位，不蓋掉原本的；夥伴新加的先「待核對」（不會被派工）。
・工單：客戶用電話或名稱對；地點用地址對（沒有就建）；匯入的工單先「擋派工」，確認後在工單按「恢復自動派」。
"""
import base64, csv, datetime as dt, io, re
import core as c

ALIAS = {
    'customers': {'name': ('姓名', '名稱', '客戶', '客戶名稱', '公司', '業主'), 'phone': ('電話', '手機', '聯絡電話', '電話號碼'), 'line_name': ('line名稱', 'line', 'line暱稱'),
                  'brand': ('品牌',), 'address': ('地址', '服務地址', '案場地址'), 'area': ('地區', '鄉鎮', '區域', '縣市鄉鎮'), 'notes': ('備註', '說明', '重要提醒')},
    'workers': {'name': ('姓名', '名稱', '夥伴', '服務人員'), 'phone': ('電話', '手機', '聯絡電話'), 'regions': ('服務區域', '區域', '地區', '可服務區域'),
                'skills': ('技能', '項目', '會做的項目', '服務類型'), 'availability': ('可排時間', '可工作時間', '時間'), 'rate': ('時薪',), 'notes': ('備註', '說明'),
                'status': ('狀態',)},
    'prices': {'item': ('項目', '品項', '名稱', '價目'), 'price': ('價格', '金額', '單價', '數值'), 'brand': ('品牌',)},
    'orders': {'date': ('日期', '服務日期'), 'start': ('開始', '開始時間', '時間'), 'end': ('結束', '結束時間'), 'customer': ('客戶', '業主', '姓名', '客戶名稱'),
               'phone': ('電話', '客戶電話', '手機'), 'address': ('地址', '地點', '案場', '服務地址'), 'people': ('人數', '需要人數'), 'title': ('類型', '服務', '服務類型', '標題'),
               'scope': ('範圍', '服務範圍', '重點'), 'notes': ('備註', '說明')},
}
KINDS = {'customers': '客戶', 'workers': '夥伴', 'orders': '工單', 'prices': '報價品項價格'}
PRICE_KEYS = {      # 報價品項價格匯入（項目名稱 → pricing 設定）
    '居家單次時薪': 'rate_single', '居家時薪': 'rate_single', '單次時薪': 'rate_single', '定期方案時薪': 'rate_plan', '定期時薪': 'rate_plan',
    '年度保養時薪': 'rate_deep', '細清時薪': 'rate_deep', '搬家時薪': 'rate_deep', '進階專案時薪': 'rate_reno', '店面時薪': 'rate_shop', '辦公室時薪': 'rate_shop', '店面辦公室時薪': 'rate_shop',
    '店家每間房': 'bnb_per_room', '居家最低': 'min_single', '單次最低': 'min_single', '年度保養最低': 'min_deep', '細清最低': 'min_deep', '進階專案最低': 'min_reno', '店家最低': 'min_bnb',
    '寵物加成': 'pet_pct', '寵物加成%': 'pet_pct', '梯子': 'ladder', '高處梯子': 'ladder', '無電梯每層': 'no_lift_per_floor', '透天加成': 'townhouse_pct', '透天加成%': 'townhouse_pct',
    '現場估價費': 'site_visit_fee', '社區估價費': 'community_visit_fee', '社區現場估價費': 'community_visit_fee', '線上報價門檻': 'low_value',
    '垃圾每袋': 'trash_bag', '垃圾清收每袋': 'trash_bag', '服務加購垃圾每袋': 'trash_addon', '生活線時薪': 'homeline_rate', '生活線最少時數': 'homeline_min_hours',
    '店家淡季首間': 'bnb_off_1', '店家淡季每間': 'bnb_off_n', '店家旺季首間': 'bnb_peak_1', '店家旺季每間': 'bnb_peak_n', '店家連假首間': 'bnb_hol_1', '店家連假每間': 'bnb_hol_n',
    '店家加時': 'bnb_overtime', '店家年度保養': 'bnb_deep'}

def _norm(h):
    return re.sub(r'[\s　:：()*＊]', '', str(h or '')).lower()

def parse(raw, filename):
    """回傳 [{表頭: 值}]。"""
    name = (filename or '').lower()
    if name.endswith(('.xlsx', '.xlsm')):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [[('' if v is None else v) for v in r] for r in ws.iter_rows(values_only=True)]
    else:
        for enc in ('utf-8-sig', 'big5', 'cp950'):
            try:
                text = raw.decode(enc); break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError('檔案編碼看不懂，請另存成 UTF-8 的 CSV 或 Excel（.xlsx）')
        rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(str(x).strip() for x in r)]
    if len(rows) < 2:
        raise ValueError('檔案裡沒有資料（第一列要是表頭，第二列開始是資料）')
    head = [str(h).strip() for h in rows[0]]
    return [{head[i]: r[i] for i in range(min(len(head), len(r)))} for r in rows[1:1001]]

def _map(kind, row):
    al = ALIAS[kind]; out = {}
    nk = {_norm(k): v for k, v in row.items()}
    for f, names in al.items():
        for n in names:
            if _norm(n) in nk and str(nk[_norm(n)]).strip():
                out[f] = nk[_norm(n)]; break
    return out

def _digits(v):
    return re.sub(r'\D', '', str(v or ''))[-9:]

def _time(v):
    if isinstance(v, dt.time):
        return v.strftime('%H:%M')
    if isinstance(v, dt.datetime):
        return v.strftime('%H:%M')
    m = re.search(r'(\d{1,2})[:：點](\d{2})?', str(v or ''))
    return f'{int(m.group(1)):02d}:{m.group(2) or "00"}' if m else ''

def _date(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return (v.date() if isinstance(v, dt.datetime) else v).isoformat()
    s = str(v or '').strip().replace('/', '-').replace('.', '-')
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})', s) or re.match(r'^(\d{1,2})-(\d{1,2})$', s)
    if not m:
        return ''
    if len(m.groups()) == 2:
        y = dt.date.today().year; return f'{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}'
    return f'{int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'

def _find_customer(con, name, phone):
    d = _digits(phone)
    if len(d) >= 9:
        for r in con.execute("SELECT * FROM customers WHERE phone!=''"):
            if _digits(r['phone']) == d:
                return dict(r)
    if name:
        r = con.execute('SELECT * FROM customers WHERE name=?', (str(name).strip(),)).fetchone()
        return dict(r) if r else None
    return None

def _price_target(con, item, brand):
    """項目 → ('key', 名稱) 或 ('addon', 名稱) 或 None。"""
    import pricing
    it = re.sub(r'[\s()：:]', '', str(item or ''))
    for k, v in PRICE_KEYS.items():
        if re.sub(r'[\s()：:]', '', k) == it:
            return ('key', v)
    name = re.sub(r'^加購', '', it)
    cfg = pricing.config(con, brand)
    if str(item or '').strip().startswith('加購') or name in (cfg.get('addons') or {}):
        return ('addon', name[:20])
    return None

def _price_plan(con, rows):
    import pricing
    out = []
    for i, row in enumerate(rows, 2):
        d = _map('prices', row); b = _brand(d.get('brand'))
        num = re.sub(r'[^0-9.]', '', str(d.get('price') or ''))
        tg = _price_target(con, d.get('item'), b)
        cfg = pricing.config(con, b)
        if not d.get('item'):
            out.append({'i': i, 'action': 'skip', 'why': '沒有項目名稱', 'data': d}); continue
        if not num:
            out.append({'i': i, 'action': 'skip', 'why': '價格不是數字', 'data': {k: str(v) for k, v in d.items()}}); continue
        if not tg:
            out.append({'i': i, 'action': 'skip', 'why': '看不懂這個項目（加購項目請寫「加購：名稱」）', 'data': {k: str(v) for k, v in d.items()}}); continue
        old = cfg.get(tg[1]) if tg[0] == 'key' else (cfg.get('addons') or {}).get(tg[1])
        new = float(num); new = int(new) if new.is_integer() else new
        act = 'skip' if old == new else ('update' if old is not None else 'add')
        out.append({'i': i, 'action': act, 'why': f"{ {'demoshop': '示範服務店', 'homeline': '生活線', 'bizline': '企業線'}[b]}：{old if old is not None else '（沒有）'} → {new}" + ('（一樣，不用改）' if act == 'skip' else ''),
                    'data': {k: str(v) for k, v in d.items()}, 'target': [b, tg[0], tg[1], new]})
    return out

def _price_run(con, rows, by, res):
    import pricing, integrations as ix, json as _j
    for p in _price_plan(con, rows):
        if p['action'] == 'skip':
            res['skipped'] += 1; continue
        b, typ, k, v = p['target']
        pk = pricing.key('pricing', b)
        try:
            cur = _j.loads(ix.setting(con, pk) or '{}')
        except ValueError:
            cur = {}
        if typ == 'key':
            cur[k] = v
        else:
            ad = dict(cur.get('addons') or pricing.config(con, b).get('addons') or {}); ad[k] = int(v); cur['addons'] = ad
        ix.set_setting(con, pk, _j.dumps(cur, ensure_ascii=False))
        res['updated' if p['action'] == 'update' else 'added'] += 1
    c.audit(con, 'pricing_import', 'settings', 'pricing', after={'by': by, 'added': res['added'], 'updated': res['updated']})
    return res

def plan(con, kind, rows):
    """預覽：[{i, action, why, data}]，不寫入。"""
    if kind not in KINDS:
        raise ValueError('請選要匯入的類型')
    if kind == 'prices':
        return _price_plan(con, rows)
    out = []
    for i, row in enumerate(rows, 2):
        d = _map(kind, row); act, why = 'add', ''
        if kind == 'customers':
            if not d.get('name'):
                act, why = 'skip', '沒有姓名／名稱'
            else:
                ex = _find_customer(con, d.get('name'), d.get('phone')) if d.get('phone') else None
                if ex:
                    act, why = 'update', f"電話對到既有客戶「{ex['name']}」：只補空白欄位"
        elif kind == 'workers':
            if not d.get('name'):
                act, why = 'skip', '沒有姓名'
            else:
                dg = _digits(d.get('phone'))
                ex = next((r for r in con.execute("SELECT * FROM workers WHERE phone!=''") if len(dg) >= 9 and _digits(r['phone']) == dg), None)
                if ex:
                    act, why = 'update', f"電話對到既有夥伴「{ex['name']}」：只補空白欄位"
        else:
            if not _date(d.get('date')):
                act, why = 'skip', '日期看不懂（例：2026-10-30 或 10/30）'
            elif not _time(d.get('start')):
                act, why = 'skip', '開始時間看不懂（例：09:00）'
            elif not (d.get('customer') or d.get('phone')):
                act, why = 'skip', '沒有客戶（姓名或電話）'
            else:
                cu = _find_customer(con, d.get('customer'), d.get('phone'))
                why = f"客戶：{cu['name']}（既有）" if cu else '新客戶（會一起建立）'
        out.append({'i': i, 'action': act, 'why': why, 'data': {k: str(v) for k, v in d.items()}})
    return out

def run(con, kind, rows, by):
    """真的匯入。回傳 {added, updated, skipped, errors}。"""
    import brands
    res = {'added': 0, 'updated': 0, 'skipped': 0, 'errors': []}
    c.CTX.actor = f'{by}（資料匯入）'
    if kind == 'prices':
        return _price_run(con, rows, by, res)
    for p in plan(con, kind, rows):
        d = p['data']
        if p['action'] == 'skip':
            res['skipped'] += 1; continue
        try:
            con.execute('SAVEPOINT imp')
            if kind == 'customers':
                _cust(con, d, by, res)
            elif kind == 'workers':
                _worker(con, d, res)
            else:
                _order(con, d, by, res)
            con.execute('RELEASE imp')
        except Exception as e:
            con.execute('ROLLBACK TO imp'); con.execute('RELEASE imp')
            res['errors'].append(f"第 {p['i']} 列：{str(e)[:80]}")
    c.audit(con, 'bulk_import', kind, '', after={**{k: v for k, v in res.items() if k != 'errors'}, 'errors': len(res['errors']), 'by': by})
    return res

def _brand(v):
    s = str(v or '')
    return 'homeline' if '生活線' in s else 'bizline' if '企業線' in s else 'demoshop'

def _cust(con, d, by, res):
    ex = _find_customer(con, d.get('name'), d.get('phone')) if d.get('phone') else None
    if ex:
        ch = {k: d[k] for k in ('phone', 'line_name', 'notes') if d.get(k) and not (ex.get(k) or '').strip()}
        if ch:
            c.save(con, 'customers', {'id': ex['id'], 'version': ex['version'], **ch})
        cid = ex['id']; res['updated'] += 1
    else:
        cid = c.save(con, 'customers', {'name': d['name'][:60], 'phone': d.get('phone', '')[:20], 'line_name': d.get('line_name', '')[:60], 'notes': d.get('notes', '')[:500],
                                         'line_account': _brand(d.get('brand')), 'status': 'active'})['id']
        res['added'] += 1
    if d.get('address') and not con.execute('SELECT 1 FROM sites WHERE customer_id=? AND address=?', (cid, d['address'].strip())).fetchone():
        c.save(con, 'sites', {'customer_id': cid, 'name': d.get('name', '')[:40] or '服務地點', 'address': d['address'].strip()[:200], 'area': d.get('area', '')[:20]})
    return cid

def _worker(con, d, res):
    dg = _digits(d.get('phone'))
    ex = next((dict(r) for r in con.execute("SELECT * FROM workers WHERE phone!=''") if len(dg) >= 9 and _digits(r['phone']) == dg), None)
    if ex:
        ch = {k: d[k] for k in ('regions', 'skills', 'availability', 'notes') if d.get(k) and not (ex.get(k) or '').strip()}
        if ch:
            c.save(con, 'workers', {'id': ex['id'], 'version': ex['version'], **ch})
        res['updated'] += 1; return
    st = 'active' if '可派' in str(d.get('status') or '') else 'review'
    rate = re.sub(r'\D', '', str(d.get('rate') or ''))
    c.save(con, 'workers', {'name': d['name'][:40], 'phone': d.get('phone', '')[:20], 'regions': d.get('regions', '')[:400], 'skills': d.get('skills', '')[:200],
                            'availability': d.get('availability', '')[:100], 'notes': d.get('notes', '')[:500], 'status': st, **({'rate': rate} if rate else {})})
    res['added'] += 1

def _order(con, d, by, res):
    import integrations as ix
    cu = _find_customer(con, d.get('customer'), d.get('phone'))
    cid = cu['id'] if cu else _cust(con, {'name': d.get('customer') or d.get('phone'), 'phone': d.get('phone', ''), 'address': d.get('address', '')}, by, {'added': 0, 'updated': 0})
    addr = (d.get('address') or '').strip()
    s = con.execute('SELECT id FROM sites WHERE customer_id=? AND (address=? OR ?="") ORDER BY rowid LIMIT 1', (cid, addr, addr)).fetchone()
    sid = s[0] if s else c.save(con, 'sites', {'customer_id': cid, 'name': (cu or {}).get('name') or d.get('customer') or '服務地點', 'address': addr})['id']
    start = _time(d.get('start')); end = _time(d.get('end')) or (dt.datetime.strptime(start, '%H:%M') + dt.timedelta(hours=4)).strftime('%H:%M')
    o = c.save(con, 'orders', {'title': (d.get('title') or '服務')[:60], 'site_id': sid, 'customer_id': cid, 'date': _date(d.get('date')), 'start': start, 'end': end,
                               'required_workers': int(re.sub(r'\D', '', str(d.get('people') or '')) or 1), 'scope': d.get('scope', '')[:1000],
                               'notes': d.get('notes', '')[:500], 'review_note': f'資料匯入（{by}）'})
    ix.set_setting(con, f"dispatch_hold:{o['id']}", f'資料匯入（{by}）：先不自動派，確認後按「恢復自動派」')
    res['added'] += 1

def from_payload(p):
    raw = base64.b64decode(str(p.get('content', '')).split(',')[-1] or b'')
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise ValueError('請上傳 5MB 以內的 Excel 或 CSV')
    return parse(raw, str(p.get('filename', '')))
