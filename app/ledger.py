"""記帳（先做示範服務股份有限公司——示範服務店／生活線／企業線；管理帳＋每月給記帳士）。

- 系統裡已經有的錢自動算，不用另外記：完工服務收入、帳單上的方案／其他費用、商城、夥伴承攬報酬、夥伴代墊用品、配合廠商外包。
- 其他收支記在 ledger 表：
  - LINE 一對一（示範服務店官方帳號）打「記帳」→ 30 分鐘內傳的收據／發票照片 → Claude（Haiku）讀日期、店家、統編、發票號碼、金額、類別 → 確認卡
  - 打「記帳 350 加油」＝直接記一筆（沒照片也可以）
  - 中控台「更多 › 記帳」記、上傳憑證、改、作廢
- 讀憑證的金鑰：管理員在中控台「記帳 › 設定」自己貼 Anthropic API 金鑰（Windows 加密存）。只送「憑證照片」去讀，不送客戶資料。沒有金鑰＝照片照存，金額自己填。
- 每月 5 日：上個月的記帳士包（收支總表、收入／支出／夥伴承攬明細 CSV＋憑證照片）自動做好，管理群一張卡。
- 公司欄位先固定示範公司；之後要加合作店家，在 COMPANIES 加一筆＋品牌對應即可（資料不混）。
"""
import base64
import csv
import datetime as dt
import io
import json
import os
import re
import threading
import zipfile
import core as c
import integrations as ix

COMPANIES = {'freeisland': {'name': '示範服務股份有限公司', 'owner': '店長', 'brands': ('demoshop', 'homeline', 'bizline')}}
COMPANY = 'freeisland'
SHORT = {'demoshop': '示範服務店', 'homeline': '生活線', 'bizline': '企業線', '': '共同'}
MODEL = 'claude-haiku-5-5'          # 負責人 選 Haiku：便宜、夠讀收據
KEY = 'anthropic_api_key'
WAIT_MIN = 30
LOCK = threading.RLock()            # server 啟動時換成全系統的 LOCK（ledger.LOCK = LOCK）

# 類別：(代碼, 名稱, 說明)。auto＝系統自動算，不能手動記（避免重複）
EXPENSE = [('supplies', '耗材耗材', '耗材、抹布、垃圾袋、手套'), ('goods', '商品進貨', '商城要賣的耗材進貨'),
           ('equipment', '設備工具', '吸塵器、蒸氣機、梯子（單價高的記帳士可能列資產）'), ('transport', '交通油資', '加油、停車、過路費、計程車'),
           ('vehicle', '車輛維修保養', ''), ('marketing', '廣告行銷', 'FB／IG 廣告、印刷、傳單、名片'), ('software', '軟體網路服務', 'LINE 官方帳號、網域、雲端、AI'),
           ('telecom', '電話網路費', ''), ('rent', '租金', ''), ('utility', '水電瓦斯', ''), ('meal', '餐飲交際', '聚餐、禮品'),
           ('insurance', '保險', '意外險、責任險'), ('outsourcing', '外包勞務', '另外開發票的外包（不是系統裡的夥伴）'), ('fees', '手續費', '匯款、LINE Pay、刷卡手續費'),
           ('tax', '稅捐規費', '印花稅、規費、牌照稅'), ('office', '文具雜支', '文具、郵資、小額雜支'), ('other', '其他支出', '')]
INCOME = [('other_income', '其他收入', '系統外的收入（不是系統訂單、補助、利息）'), ('refund_in', '退款收回', '廠商退款、退費')]
AUTO_IN = [('service', '服務收入（完工）'), ('billed_other', '帳單其他費用（方案月費等）'), ('shop', '商城收入'), ('cancel_kept', '取消・已收不退的款項'), ('discount', '折讓（扣收入）')]
AUTO_TAX = [('output_tax', '銷項稅額（代收，不算收入）')]      # A：照會計原則，稅另列
AUTO_OUT = [('worker', '夥伴承攬報酬'), ('reimburse', '夥伴代墊用品'), ('vendor', '配合廠商外包')]
CAT = {k: n for k, n, _ in EXPENSE + INCOME} | dict(AUTO_IN + AUTO_OUT + AUTO_TAX)
DOC_TYPES = ('統一發票', '電子發票證明聯', '收據', '收銀明細', '其他', '無憑證')
METHODS = ('現金', '轉帳', '信用卡', 'LINE Pay', '電子支付', '其他', '')
WORDS = [('transport', ('加油', '油資', '中油', '台塑', '停車', '高速', 'etag', 'eTag', '計程車', '高鐵', '台鐵', '客運')),
         ('supplies', ('耗材', '抹布', '垃圾袋', '手套', '用品', '耗材', '菜瓜布', '海綿', '漂白', '酒精')),
         ('goods', ('進貨',)), ('equipment', ('吸塵器', '蒸氣', '工具', '梯子', '設備', '刷頭')),
         ('marketing', ('廣告', 'FB', 'IG', '傳單', '名片', '印刷', '貼紙')), ('software', ('LINE官方', '網域', '雲端', '軟體', 'API', 'Claude', 'GPT')),
         ('telecom', ('電話費', '網路費', '手機費', '中華電信', '台灣大', '遠傳')), ('rent', ('房租', '租金')), ('utility', ('水費', '電費', '瓦斯')),
         ('meal', ('餐', '便當', '飲料', '咖啡', '聚餐', '禮盒')), ('insurance', ('保險',)), ('fees', ('手續費',)), ('office', ('文具', '郵資', '郵局', '影印')),
         ('vehicle', ('保養', '輪胎', '修車'))]

RECEIPT = {'type': 'object', 'additionalProperties': False,
          'required': ['is_receipt', 'date', 'seller', 'seller_tax_id', 'buyer_tax_id', 'doc_type', 'doc_no', 'total', 'tax', 'items', 'category', 'payment', 'note'],
          'properties': {
              'is_receipt': {'type': 'boolean', 'description': '照片是不是收據、發票、收銀明細、繳費單之類的付款憑證'},
              'date': {'type': 'string', 'description': '交易日期 YYYY-MM-DD（西元；民國年＋1911）。看不出來給空字串'},
              'seller': {'type': 'string', 'description': '賣方／店家名稱'},
              'seller_tax_id': {'type': 'string', 'description': '賣方統一編號（8 位數字），沒有給空字串'},
              'buyer_tax_id': {'type': 'string', 'description': '買方統一編號（8 位數字），沒有給空字串'},
              'doc_type': {'type': 'string', 'enum': list(DOC_TYPES)},
              'doc_no': {'type': 'string', 'description': '發票號碼（2 個英文字母＋8 位數字），收據編號也可以；沒有給空字串'},
              'total': {'type': 'integer', 'description': '總金額（新台幣元、含稅；看不出來給 0）'},
              'tax': {'type': 'integer', 'description': '營業稅額（元），沒有分開列給 0'},
              'items': {'type': 'array', 'items': {'type': 'string'}, 'description': '品項（最多 8 項，每項「品名 金額」）'},
              'category': {'type': 'string', 'enum': [k for k, _, _ in EXPENSE]},
              'payment': {'type': 'string', 'enum': list(METHODS)},
              'note': {'type': 'string', 'description': '看不清楚、可能讀錯的地方（中文一句話），沒有給空字串'}}}
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['receipts'],
          'properties': {'receipts': {'type': 'array', 'items': RECEIPT, 'description': '照片裡的每一張憑證各一筆（由左到右、由上到下）'}}}
PROMPT = ('這是台灣一家服務公司（示範服務股份有限公司，品牌：示範服務店、生活小幫手、企業服務線）的支出憑證照片'
          '（統一發票、電子發票證明聯、收據、收銀明細、繳費單…）。請照格式讀出欄位。\n'
          '- 一張照片可能排了好幾張收據（例如一頁 A4 掃描好幾張）：每一張各列一筆，由左到右、由上到下；同一張收據的發票證明聯和交易明細算一筆。'
          '被切掉、看不到總金額的也要列，total 給 0、note 寫「被切掉」。照片裡沒有任何付款憑證就給空陣列。\n'
          '- 日期：民國年請換成西元（例如 115/10/19 → 2026-10-19）。\n'
          '- 金額只要數字（元），總計以「總計／合計／應付」為準，不要拿單項。\n'
          '- 統編：賣方統編通常印在店名旁或「賣方」後面；買方統編在「買方」後面。都是 8 位數字。\n'
          '- 類別從清單選最接近的：' + '、'.join(f'{k}={n}' + (f'（{d}）' if d else '') for k, n, d in EXPENSE) + '。\n'
          '- 看不清楚的欄位寫空字串或 0，不要猜；照片不是付款憑證就 is_receipt=false。\n'
          '- note 只寫「看不清楚、可能讀錯」的地方；都清楚就給空字串（不要寫隨機碼、發票期別、稅額為 0 這類說明）。')

# ───────────────────────── 資料表 ─────────────────────────

def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY, company TEXT DEFAULT 'freeisland', brand TEXT DEFAULT '', kind TEXT DEFAULT 'expense',
        category TEXT DEFAULT 'other', date TEXT DEFAULT '', amount INTEGER DEFAULT 0, tax INTEGER DEFAULT 0, counterparty TEXT DEFAULT '', tax_id TEXT DEFAULT '',
        doc_type TEXT DEFAULT '', doc_no TEXT DEFAULT '', method TEXT DEFAULT '', memo TEXT DEFAULT '', source TEXT DEFAULT 'manual', status TEXT DEFAULT 'draft',
        photo TEXT DEFAULT '', ocr TEXT DEFAULT '', ocr_state TEXT DEFAULT '', tries INTEGER DEFAULT 0, reply_token TEXT DEFAULT '', line_uid TEXT DEFAULT '',
        created_by TEXT DEFAULT '', created_at TEXT, confirmed_by TEXT DEFAULT '', confirmed_at TEXT DEFAULT '', version INTEGER DEFAULT 1)""")
    con.execute('CREATE INDEX IF NOT EXISTS ledger_date ON ledger(company,date)')
    if 'batch' not in {r[1] for r in con.execute('PRAGMA table_info(ledger)')}:      # 一張照片多張收據 → 同一批
        con.execute("ALTER TABLE ledger ADD COLUMN batch TEXT DEFAULT ''")

def get(con, eid):
    ensure(con)
    r = con.execute('SELECT * FROM ledger WHERE id=?', (eid or '',)).fetchone()
    if not r:
        raise ValueError('找不到這筆記帳（可能已經刪除）')
    return dict(r)

def brand_ok(b):
    return b if b in COMPANIES[COMPANY]['brands'] else ''

def guess(text):
    for k, ws in WORDS:
        if any(w.lower() in (text or '').lower() for w in ws):
            return k
    return 'other'

def _clean(p, old=None):
    """表單／LINE／讀取結果 → 欄位（金額單位：分）。"""
    old = old or {}
    kind = p.get('kind', old.get('kind', 'expense'))
    if kind not in ('expense', 'income'):
        raise ValueError('收支只能是支出或收入')
    cats = [k for k, _, _ in (EXPENSE if kind == 'expense' else INCOME)]
    cat = p.get('category', old.get('category')) or cats[-1]
    if cat not in cats:
        raise ValueError('類別不在清單裡（系統自動算的服務收入、夥伴報酬不用手動記）')
    d = str(p.get('date', old.get('date')) or '')[:10]
    if d:
        c.date(d)
    amt = p.get('amount', old.get('amount'))
    amt = c.integer(amt, '金額', 0) if amt not in (None, '') else 0
    tax = p.get('tax', old.get('tax'))
    tax = c.integer(tax, '稅額', 0) if tax not in (None, '') else 0
    if amt > 1_000_000_000 or tax > amt:
        raise ValueError('金額或稅額不對')
    tid = re.sub(r'\D', '', str(p.get('tax_id', old.get('tax_id')) or ''))
    if tid and len(tid) != 8:
        raise ValueError('統編要 8 位數字')
    no = re.sub(r'[\s-]', '', str(p.get('doc_no', old.get('doc_no')) or '')).upper()[:30]
    dtp = p.get('doc_type', old.get('doc_type')) or ''
    if dtp and dtp not in DOC_TYPES:
        raise ValueError('憑證類型不在清單裡')
    m = p.get('method', old.get('method')) or ''
    if m not in METHODS:
        raise ValueError('付款方式不在清單裡')
    return {'kind': kind, 'category': cat, 'date': d, 'amount': amt, 'tax': tax, 'tax_id': tid, 'doc_no': no, 'doc_type': dtp, 'method': m,
            'brand': brand_ok(p.get('brand', old.get('brand', ''))), 'counterparty': str(p.get('counterparty', old.get('counterparty')) or '').strip()[:80],
            'memo': str(p.get('memo', old.get('memo')) or '').strip()[:300]}

def save(con, p, who):
    """中控台新增／修改（金額用分）。存了就算確認過（status=ok）。"""
    ensure(con)
    eid = p.get('id') or ''
    old = get(con, eid) if eid else {}
    if old and int(p.get('version') or old['version']) != old['version']:
        raise ValueError('這筆剛剛被別人改過，請重新整理再改')
    f = _clean(p, old)
    if not f['date']:
        raise ValueError('請填日期')
    if not f['amount']:
        raise ValueError('請填金額')
    if old:
        f.update({'status': 'ok' if old['status'] != 'void' else 'void', 'confirmed_by': old['confirmed_by'] or who, 'confirmed_at': old['confirmed_at'] or c.now(), 'version': old['version'] + 1})
        if old['status'] == 'reading':
            f['ocr_state'] = 'skip'
        con.execute('UPDATE ledger SET ' + ','.join(f'{k}=?' for k in f) + ' WHERE id=?', (*f.values(), eid))
        c.audit(con, 'ledger_edit', 'ledger', eid, before={k: old[k] for k in f if k in old}, after=f, actor=who)
        return get(con, eid)
    eid = c.uid('lg')
    con.execute('INSERT INTO ledger(id,company,source,status,created_by,created_at,confirmed_by,confirmed_at,' + ','.join(f) + ') VALUES(' + ','.join('?' * (8 + len(f))) + ')',
                (eid, COMPANY, p.get('source') or 'manual', 'ok', who, c.now(), who, c.now(), *f.values()))
    c.audit(con, 'ledger_add', 'ledger', eid, after=f, actor=who)
    return get(con, eid)

def dup_of(con, e):
    """同一張發票記兩次（稽核：單張照片一筆、PDF 掃描又一筆）：發票號碼末 8 碼相同、沒作廢的另一筆。"""
    no = re.sub(r'\D', '', e.get('doc_no') or '')[-8:]
    if len(no) < 8:
        return None
    for r in con.execute("SELECT * FROM ledger WHERE company=? AND id!=? AND status!='void' AND doc_no LIKE ?", (e.get('company') or COMPANY, e['id'], '%' + no)):
        if re.sub(r'\D', '', r['doc_no'] or '')[-8:] == no:
            return dict(r)
    return None

def confirm(con, eid, who):
    e = get(con, eid)
    d = dup_of(con, e)
    if d and d['status'] == 'ok' and e['status'] != 'ok':
        raise ValueError(f"可能重複：發票 {d['doc_no']} 已經記過一筆（{d['date']} NT$ {d['amount'] // 100:,}）。同一張請按「作廢這筆」；真的是兩張不同的，請到中控台改發票號碼")
    if e['status'] == 'void':
        raise ValueError('這筆已經作廢了')
    if e['status'] == 'reading':
        raise ValueError('照片還在讀，等一下再確認（或直接改金額）')
    if not e['amount'] or not e['date']:
        raise ValueError('還缺金額或日期，請先補（中控台「記帳」點這筆修改）')
    if e['status'] == 'ok':
        return e
    con.execute("UPDATE ledger SET status='ok',confirmed_by=?,confirmed_at=?,version=version+1 WHERE id=?", (who, c.now(), eid))
    c.audit(con, 'ledger_confirm', 'ledger', eid, actor=who)
    return get(con, eid)

def void(con, eid, who):
    e = get(con, eid)
    con.execute("UPDATE ledger SET status='void',ocr_state=CASE WHEN status='reading' THEN 'skip' ELSE ocr_state END,version=version+1 WHERE id=?", (eid,))
    c.audit(con, 'ledger_void', 'ledger', eid, before={'status': e['status']}, actor=who)
    return get(con, eid)

def set_field(con, eid, who, category=None, brand=None):
    """LINE 確認卡：改類別、改品牌。"""
    e = get(con, eid)
    if e['status'] == 'void':
        raise ValueError('這筆已經作廢了')
    if category is not None:
        if category not in [k for k, _, _ in (EXPENSE if e['kind'] == 'expense' else INCOME)]:
            raise ValueError('類別不在清單裡')
        con.execute('UPDATE ledger SET category=?,version=version+1 WHERE id=?', (category, eid))
    if brand is not None:
        con.execute('UPDATE ledger SET brand=?,version=version+1 WHERE id=?', (brand_ok(brand), eid))
    c.audit(con, 'ledger_field', 'ledger', eid, before={'category': e['category'], 'brand': e['brand']}, after={'category': category, 'brand': brand}, actor=who)
    return get(con, eid)

# ───────────────────────── 憑證照片 ─────────────────────────

def _photo_dir(month):
    d = c.DATA / 'ledger' / month
    d.mkdir(parents=True, exist_ok=True)
    return d

def photo_path(e):
    return (c.DATA / 'ledger' / e['photo']) if e.get('photo') else None

def store_jpeg(jpg):
    """已經壓好的 JPEG → data/ledger/<月>/<代碼>.jpg（不公開）。回傳相對路徑。"""
    month = dt.datetime.now(c.TZ).strftime('%Y-%m')
    name = c.uid('rc') + '.jpg'
    (_photo_dir(month) / name).write_bytes(jpg)
    return f'{month}/{name}'

def store_photo(raw):
    """壓成 JPEG（長邊 1600、去掉 EXIF）再存。回傳 (相對路徑, jpeg bytes)。"""
    import recruit
    jpg = recruit.shrink(raw)
    return store_jpeg(jpg), jpg

def pdf_pages(raw, limit=10):
    """PDF（掃描檔）→ 每一頁一張 JPEG（長邊 1600、去掉 EXIF）。純運算，在鎖外呼叫。"""
    import fitz, recruit
    if raw[:5] != b'%PDF-':
        raise ValueError('這不是 PDF 檔')
    out = []
    with fitz.open(stream=raw, filetype='pdf') as d:
        if d.page_count > limit:
            raise ValueError(f'PDF 超過 {limit} 頁，請分開傳')
        for p in d:
            z = 2000 / max(p.rect.width, p.rect.height)
            out.append(recruit.shrink(p.get_pixmap(matrix=fitz.Matrix(z, z)).tobytes('png')))
    return out

def add_photo(con, jpg_rel, who, source='upload', brand='', uid='', token=''):
    """收一張憑證 → 一筆待讀取（有金鑰）／待補金額（沒金鑰）。"""
    ensure(con)
    eid = c.uid('lg')
    state = 'wait' if api_key(con) else 'nokey'
    con.execute("INSERT INTO ledger(id,company,brand,kind,category,status,photo,ocr_state,source,reply_token,line_uid,created_by,created_at,date) "
                "VALUES(?,?,?,'expense','other',?,?,?,?,?,?,?,?,?)",
                (eid, COMPANY, brand_ok(brand), 'reading' if state == 'wait' else 'draft', jpg_rel, state, source, token, uid, who, c.now(), c.now()[:10]))
    c.audit(con, 'ledger_photo', 'ledger', eid, after={'source': source}, actor=who)
    return eid

# ───────────────────────── Claude 讀憑證 ─────────────────────────

def api_key(con):
    try:
        return ix.secret(con, KEY) or ''
    except Exception:
        return ''

def key_status(con):
    tail = ix.setting(con, 'anthropic_key_tail')
    return {'set': bool(con.execute('SELECT 1 FROM secrets WHERE key=?', (KEY,)).fetchone()), 'tail': tail, 'model': MODEL,
            'checked': ix.setting(con, 'anthropic_key_checked')}

def set_tax_id(con, tid, who):
    """示範公司統編：收據上的買方統編跟這個不一樣 → 確認卡提醒「可能是別家公司的」。"""
    tid = re.sub(r'\D', '', tid or '')
    if tid and len(tid) != 8:
        raise ValueError('統編要 8 位數字')
    ix.set_setting(con, 'ledger_tax_id', tid)
    c.audit(con, 'ledger_tax_id', 'settings', 'ledger_tax_id', after={'tax_id': tid}, actor=who)
    return f'✅ 示範公司統編：{tid}（收據上買方統編不同會提醒）' if tid else '已清除示範公司統編'

def set_key(con, key, who):
    key = (key or '').strip()
    if not re.fullmatch(r'sk-ant-[A-Za-z0-9_\-]{20,300}', key):
        raise ValueError('這不像 Anthropic 的 API 金鑰（應該是 sk-ant- 開頭）')
    ix.put_secret(con, KEY, key)
    ix.set_setting(con, 'anthropic_key_tail', key[-4:]); ix.set_setting(con, 'anthropic_key_checked', '')
    c.audit(con, 'anthropic_key_set', 'secrets', KEY, after={'tail': key[-4:]}, actor=who)        # 金鑰本身不進紀錄
    return f'✅ 金鑰已存（結尾 …{key[-4:]}，Windows 加密）。之後傳的憑證照片會自動讀。'

def clear_key(con, who):
    con.execute('DELETE FROM secrets WHERE key=?', (KEY,))
    ix.set_setting(con, 'anthropic_key_tail', ''); ix.set_setting(con, 'anthropic_key_checked', '')
    c.audit(con, 'anthropic_key_clear', 'secrets', KEY, actor=who)
    return '已清除金鑰。之後的憑證照片只存檔，金額要自己填。'

def check_key(key):
    """不花錢的檢查：查一次模型資料。回傳 (ok, 說明)。在鎖外呼叫。"""
    if not key:
        return False, '還沒設定金鑰'
    if os.environ.get('OPS_NO_PUSH'):
        return False, '測試環境不連 Claude'
    import anthropic
    try:
        anthropic.Anthropic(api_key=key, timeout=20.0, max_retries=1).models.retrieve(MODEL)
        return True, f'✅ 金鑰可以用（{MODEL}）'
    except anthropic.AuthenticationError:
        return False, '金鑰無效（請重新複製貼上）'
    except anthropic.PermissionDeniedError:
        return False, '這把金鑰沒有權限用這個模型'
    except anthropic.NotFoundError:
        return False, f'找不到模型 {MODEL}'
    except anthropic.APIStatusError as e:
        return False, f'Claude 回應 {e.status_code}，稍後再試'
    except anthropic.APIConnectionError:
        return False, '連不上 Claude（網路）'

def _media(raw):
    return 'image/png' if raw[:8] == b'\x89PNG\r\n\x1a\n' else 'image/webp' if raw[8:12] == b'WEBP' else 'image/gif' if raw[:4] == b'GIF8' else 'image/jpeg'

def _call(key, raw):
    """真的打 Claude（測試會換掉這個）。回傳 (結果 dict 或 None, 狀態, 說明)；狀態 ok／retry（可以再試）／failed（不用再試）。"""
    import anthropic
    client = anthropic.Anthropic(api_key=key, timeout=45.0, max_retries=1)
    try:
        r = client.messages.create(
            model=MODEL, max_tokens=12000,
            output_config={'effort': 'low', 'format': {'type': 'json_schema', 'schema': SCHEMA}},
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': _media(raw), 'data': base64.standard_b64encode(raw).decode()}},
                {'type': 'text', 'text': PROMPT}]}])
    except anthropic.AuthenticationError:
        return None, 'failed', '讀取金鑰無效（請管理員到中控台「記帳 › 設定」重貼）'
    except anthropic.PermissionDeniedError:
        return None, 'failed', '讀取金鑰沒有權限'
    except anthropic.BadRequestError as e:
        return None, 'failed', f'這張照片讀不了（{str(e.message)[:60]}）'
    except anthropic.RateLimitError:
        return None, 'retry', '讀取太頻繁，等一下自動再試'
    except anthropic.APIStatusError as e:
        return None, 'retry', f'Claude 暫時有問題（{e.status_code}），等一下自動再試'
    except anthropic.APIConnectionError:
        return None, 'retry', '連不上 Claude（網路），等一下自動再試'
    if r.stop_reason == 'refusal':
        return None, 'failed', '這張沒有讀（請自己填金額）'
    if r.stop_reason == 'max_tokens':
        return None, 'failed', '這張內容太多讀不完（請自己填金額）'
    text = next((b.text for b in r.content if b.type == 'text'), '')
    try:
        data = json.loads(text)
    except ValueError:
        return None, 'failed', '讀到的格式不對（請自己填金額）'
    data['_usage'] = {'in': r.usage.input_tokens, 'out': r.usage.output_tokens, 'id': getattr(r, '_request_id', '') or ''}
    return data, 'ok', ''

def read(key, raw):
    """在鎖外讀一張憑證。回傳 (結果, 狀態, 說明)。測試環境（OPS_NO_PUSH）不連線，除非測試換掉 _call。"""
    if not key:
        return None, 'nokey', ''
    if os.environ.get('OPS_NO_PUSH') and _call is _REAL_CALL:
        return None, 'failed', '測試環境不連 Claude'
    try:
        return _call(key, raw)
    except Exception as e:                 # 照片壞掉、SDK 版本問題：當作讀不到，照片照存
        return None, 'failed', f'讀取失敗（{str(e)[:60]}）'

_REAL_CALL = _call

def _fill(r):
    """讀取結果 → 欄位（只收看起來合理的值）。"""
    out = {}
    d = str(r.get('date') or '')
    try:
        if d and dt.date.fromisoformat(d) <= dt.datetime.now(c.TZ).date() + dt.timedelta(days=3):
            out['date'] = d
    except ValueError:
        pass
    tot = r.get('total') or 0
    if isinstance(tot, int) and 0 < tot < 10_000_000:
        out['amount'] = tot * 100
        tax = r.get('tax') or 0
        out['tax'] = tax * 100 if isinstance(tax, int) and 0 <= tax < tot else 0
    tid = re.sub(r'\D', '', str(r.get('seller_tax_id') or ''))
    out['tax_id'] = tid if len(tid) == 8 else ''
    no = re.sub(r'[\s-]', '', str(r.get('doc_no') or '')).upper()
    out['doc_no'] = no[:30]
    out['doc_type'] = r.get('doc_type') if r.get('doc_type') in DOC_TYPES else ''
    out['method'] = r.get('payment') if r.get('payment') in METHODS else ''
    out['counterparty'] = str(r.get('seller') or '')[:80]
    out['category'] = r.get('category') if r.get('category') in [k for k, _, _ in EXPENSE] else 'other'
    items = [str(x)[:40] for x in (r.get('items') or [])][:8]
    note = str(r.get('note') or '')[:120]
    out['memo'] = '、'.join(items)[:240] + (f'（{note}）' if note else '')
    return out

def apply_read(con, eid, res, state, why):
    """把讀取結果寫回這筆（在鎖裡）。回傳更新後的這筆。"""
    e = get(con, eid)
    if e['status'] != 'reading':
        return e                            # 讀的時候被人作廢或手動改了：不蓋掉
    tries = e['tries'] + 1
    if state == 'ok' and res is not None:
        recs = [r for r in (res.get('receipts') if 'receipts' in res else [res]) if isinstance(r, dict)]
        usage = res.get('_usage') or {}
        if len(recs) > 1:                   # 一張照片好幾張收據 → 第 2 張起各建一筆（同一張照片、同一批）
            con.execute("UPDATE ledger SET batch=? WHERE id=?", (eid, eid))
            for r in recs[1:]:
                nid = c.uid('lg')
                con.execute("INSERT INTO ledger(id,company,brand,kind,category,status,photo,ocr_state,source,line_uid,created_by,created_at,date,batch) "
                            "VALUES(?,?,?,'expense','other','reading',?,'wait',?,?,?,?,?,?)",
                            (nid, e['company'], e['brand'], e['photo'], e['source'], e['line_uid'], e['created_by'], e['created_at'], e['date'], eid))
                _fill_row(con, nid, r, '', tries, {})
        _fill_row(con, eid, recs[0] if recs else {'is_receipt': False}, why, tries, usage)
        return get(con, eid)
    elif state == 'retry' and tries < 3:
        f = {'ocr_state': 'retry', 'tries': tries, 'memo': why[:300]}
    else:
        f = {'status': 'draft', 'ocr_state': 'failed', 'tries': tries, 'memo': why[:300]}
    con.execute('UPDATE ledger SET ' + ','.join(f'{k}=?' for k in f) + ',version=version+1 WHERE id=?', (*f.values(), eid))
    return get(con, eid)

def _fill_row(con, eid, r, why, tries, usage):
    f = _fill(r)
    if not r.get('is_receipt'):
        why = why or '這張看起來不是收據或發票'
    elif not f.get('amount'):
        why = why or '看不到總金額（可能被切掉），請補金額'
    buyer = re.sub(r'\D', '', str(r.get('buyer_tax_id') or ''))
    raw = json.dumps({**r, 'buyer_tax_id': buyer, '_usage': usage}, ensure_ascii=False)
    f.update({'status': 'draft', 'ocr': raw[:4000], 'ocr_state': 'ok' if not why else 'odd', 'tries': tries, 'memo': (f['memo'] + (f'｜{why}' if why else ''))[:300]})
    con.execute('UPDATE ledger SET ' + ','.join(f'{k}=?' for k in f) + ',version=version+1 WHERE id=?', (*f.values(), eid))

def siblings(con, e):
    """同一張照片讀出來的幾筆（沒有分批就只有自己）。"""
    if not e.get('batch'):
        return [e]
    return c.rows(con, 'SELECT * FROM ledger WHERE batch=? ORDER BY created_at,rowid', (e['batch'],))

def buyer(e):
    try:
        return (json.loads(e.get('ocr') or '{}') or {}).get('buyer_tax_id') or ''
    except ValueError:
        return ''

def process(con, eid):
    """同一條連線裡讀完（測試、手動補讀用）。正式環境走 kick() 背景讀，不卡全系統的鎖。"""
    e = get(con, eid)
    p = photo_path(e)
    res, state, why = read(api_key(con), p.read_bytes()) if p and p.exists() else (None, 'failed', '照片不見了')
    return apply_read(con, eid, res, state, why)

def reread(con, eid, who):
    """中控台「重新讀取」：讀不清楚、剛設好金鑰的照片再讀一次（背景讀）。"""
    e = get(con, eid)
    if e['status'] not in ('draft', 'reading'):
        raise ValueError('已經確認或作廢的不用再讀')
    if not photo_path(e) or not photo_path(e).exists():
        raise ValueError('這筆沒有照片')
    if not api_key(con):
        raise ValueError('還沒設定讀取金鑰（管理員在這一頁下面的「設定」貼上）')
    con.execute("UPDATE ledger SET status='reading',ocr_state='wait',tries=0,version=version+1 WHERE id=?", (eid,))
    c.audit(con, 'ledger_reread', 'ledger', eid, actor=who)
    kick([eid])
    return get(con, eid)

_RUNNING = set()

def kick(ids=None):
    """背景讀憑證：先等 LINE 那筆交易存好，鎖外打 Claude，再進鎖寫結果、回確認卡。測試環境不開執行緒。"""
    if os.environ.get('OPS_NO_PUSH'):
        return
    threading.Thread(target=_worker, args=(list(ids or []),), daemon=True).start()

def _worker(ids):
    import contextlib
    try:
        with LOCK, contextlib.closing(c.connect()) as con:
            ensure(con)
            rows = [dict(r) for r in con.execute("SELECT * FROM ledger WHERE status='reading' AND ocr_state IN ('wait','retry')" + (f" AND id IN ({','.join('?' * len(ids))})" if ids else ''), ids)]
            key = api_key(con)
        for e in rows:
            if e['id'] in _RUNNING:
                continue
            _RUNNING.add(e['id'])
            try:
                p = photo_path(e)
                res, state, why = read(key, p.read_bytes()) if p and p.exists() else (None, 'failed', '照片不見了')
                with LOCK, contextlib.closing(c.connect()) as con, con:
                    e2 = apply_read(con, e['id'], res, state, why)
                    if e2['status'] == 'draft':
                        _deliver(con, e2)
            finally:
                _RUNNING.discard(e['id'])
        import line_bot
        line_bot.kick()
    except Exception as ex:                # 背景執行緒：出錯記到系統通知，不要整個掛掉
        import traceback
        traceback.print_exc()
        try:
            import notify
            with contextlib.closing(c.connect()) as con2, con2:
                notify.staff(con2, '記帳讀憑證出錯', str(ex)[:120], '/#ledger')
        except Exception:
            pass

def _deliver(con, e):
    """讀完 → 確認卡回給傳照片的人（1 分鐘內用 reply 不花額度，超過就一般傳送）。"""
    import line_bot as lb
    if not e.get('line_uid'):
        return
    msgs = cards(con, siblings(con, e))
    fresh = e.get('reply_token') and (dt.datetime.now(c.TZ) - dt.datetime.fromisoformat(e['created_at'])).total_seconds() < 50
    if fresh:
        lb.reply(con, lb.WORK_ACCOUNT, e['reply_token'], msgs, 'ledger')
        st = con.execute("SELECT status FROM line_outbox WHERE id=last_insert_rowid()").fetchone()
        if st and st[0] == 'sent':
            con.execute("UPDATE ledger SET reply_token='' WHERE id=?", (e['id'],))
            return
    lb.queue(con, lb.WORK_ACCOUNT, e['line_uid'], 'ledger', msgs[0]['altText'], msgs)
    con.execute("UPDATE ledger SET reply_token='' WHERE id=?", (e['id'],))

# ───────────────────────── LINE ─────────────────────────

def who_ok(con, uid):
    import line_bot as lb
    return uid in lb.staff_users(con) or uid in lb.money_users(con) or uid in lb.deciders(con)

def start(con, uid):
    until = (dt.datetime.now(c.TZ) + dt.timedelta(minutes=WAIT_MIN)).isoformat(timespec='seconds')
    ix.set_setting(con, f'lg_wait:{uid}', until)
    k = '' if api_key(con) else '\n（讀取金鑰還沒設定：照片會先存起來，金額請在中控台「記帳」補。）'
    return (f'🧾 好，請直接在這裡傳收據／發票照片或 PDF 掃描檔（{WAIT_MIN} 分鐘內傳的都算；一張照片排好幾張收據也可以，會自動拆開）。\n'
            '我讀完會回一張確認卡：日期、店家、統編、金額、類別，對了按「✅ 正確，記帳」。\n'
            '沒有照片的：打「記帳 350 加油」就好（收入打「記帳 收入 3000 說明」）。' + k)

def waiting(con, uid):
    u = ix.setting(con, f'lg_wait:{uid}')
    return bool(u) and u >= dt.datetime.now(c.TZ).isoformat(timespec='seconds')

def line_file(con, uid, name, raw, token, who, to=None):
    """記帳中傳的檔案：PDF 掃描檔（每頁讀出所有收據）；照片檔照一般照片。"""
    low = (name or '').lower()
    if low.endswith(('.jpg', '.jpeg', '.png', '.heic', '.webp')):
        return line_photo(con, uid, raw, token, who, to)
    if not low.endswith('.pdf'):
        return [{'type': 'text', 'text': '記帳只收照片或 PDF 掃描檔 🙏'}]
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('PDF 超過 10MB，請分開傳')
    pages = pdf_pages(raw)
    ids = [add_photo(con, store_jpeg(j), who, 'line', uid=to or uid, token=token if i == 0 else '') for i, j in enumerate(pages)]
    ix.set_setting(con, f'lg_wait:{uid}', (dt.datetime.now(c.TZ) + dt.timedelta(minutes=WAIT_MIN)).isoformat(timespec='seconds'))
    if get(con, ids[0])['status'] == 'reading':
        kick(ids)
        return None if len(ids) == 1 else [{'type': 'text', 'text': f'📄 收到 PDF {len(ids)} 頁，讀取中（每頁約 15 秒），讀完會一頁一張確認卡；中控台「記帳 › 待確認」也看得到。'}]
    return cards(con, [get(con, i) for i in ids])

def line_photo(con, uid, raw, token, who, to=None):
    """記帳中傳的照片：存檔 → 有金鑰就背景讀（讀完用同一個 reply 回確認卡）；沒金鑰就直接回「待補金額」卡。回傳要立刻回的訊息（None＝等背景讀完再回）。
    to：確認卡送回哪裡（在派單管理群傳的就回群組）。"""
    rel, _ = store_photo(raw)
    eid = add_photo(con, rel, who, 'line', uid=to or uid, token=token)
    until = (dt.datetime.now(c.TZ) + dt.timedelta(minutes=WAIT_MIN)).isoformat(timespec='seconds')
    ix.set_setting(con, f'lg_wait:{uid}', until)          # 連續傳：時間往後延
    e = get(con, eid)
    if e['status'] == 'reading':
        kick([eid])
        return None
    return card(con, e)

def line_text(con, uid, text, who):
    """「記帳 350 加油」「記帳 收入 3000 退款」→ 直接記一筆（自己打的＝確認過）。回傳訊息；格式不對回 None。"""
    m = re.match(r'^記帳\s*(收入)?\s*\$?\s*(\d[\d,]*)\s*(?:元)?\s*(.*)$', text.strip(), re.S)
    if not m:
        return None
    amt = int(m.group(2).replace(',', ''))
    if amt <= 0 or amt > 10_000_000:
        return [{'type': 'text', 'text': '金額看起來不對，請再打一次，例如「記帳 350 加油」'}]
    memo = m.group(3).strip()[:200]
    kind = 'income' if m.group(1) else 'expense'
    e = save(con, {'kind': kind, 'category': 'other_income' if kind == 'income' else guess(memo), 'date': c.now()[:10], 'amount': amt * 100,
                   'memo': memo, 'source': 'line', 'method': ''}, who)
    return card(con, e)

def card(con, e):
    """確認卡（LINE）。"""
    import line_bot as lb
    amt = f"NT$ {e['amount'] // 100:,}" if e['amount'] else '（還沒有金額）'
    if e['status'] == 'ok':
        head, color = '✅ 已記帳', '#2E7D32'
    elif e['ocr_state'] == 'nokey':
        head, color = '🧾 照片已存（待補金額）', '#C47F00'
    elif e['ocr_state'] in ('failed', 'odd'):
        head, color = '🧾 請確認（有地方讀不清楚）', '#C47F00'
    else:
        head, color = '🧾 請確認這筆記帳', '#0A84FF'
    rows = [lb._row('日期', e['date'] or '—'), lb._row('店家／對象', e['counterparty'] or '—'), lb._row('金額', amt + (f"（稅 {e['tax'] // 100:,}）" if e['tax'] else '')),
            lb._row('類別', ('收入・' if e['kind'] == 'income' else '') + CAT.get(e['category'], e['category'])), lb._row('品牌', SHORT.get(e['brand'], '共同'))]
    if e['tax_id'] or e['doc_no']:
        rows.append(lb._row('統編／發票', ' '.join(x for x in (e['tax_id'], e['doc_no']) if x)))
    by, mine = buyer(e), ix.setting(con, 'ledger_tax_id')
    if by:
        rows.append(lb._row('買方統編', by + ('（示範公司）' if mine and by == mine else '　⚠️ 不是示範公司，可能是別家公司的' if mine else '')))
    d = dup_of(con, e)
    if d:
        rows.append(lb._t(f"⚠️ 可能重複：發票 {d['doc_no']} 還有另一筆（{d['date']}，{'NT$ ' + format(d['amount'] // 100, ',') if d['amount'] else '沒有金額'}，{'已記帳' if d['status'] == 'ok' else '待確認'}）。同一張只要留一筆，另一筆按「作廢這筆」。",
                          size='xs', color='#C62828', wrap=True, margin='md'))
    if e['memo']:
        rows.append(lb._t(e['memo'][:200], size='xs', color=lb.SUB, wrap=True, margin='md'))
    if e['ocr_state'] == 'nokey':
        rows.append(lb._t('讀取金鑰還沒設定，請在中控台「記帳」點這筆補金額。', size='xs', color=lb.SUB, wrap=True, margin='md'))
    url = f"{_base(con)}/#ledger/{e['id']}"
    btns = []
    if e['status'] != 'ok' and e['amount']:
        btns.append(lb._btn('✅ 正確，記帳', data=f"a=lg_ok&e={e['id']}", primary=True))
    btns += [lb._btn('改類別', data=f"a=lg_cat&e={e['id']}"), lb._btn('改品牌', data=f"a=lg_br&e={e['id']}"),
             lb._btn('改金額、日期（中控台）', uri=url, primary=not e['amount']), lb._btn('作廢這筆', data=f"a=lg_void&e={e['id']}")]
    return lb.flex(f"記帳：{e['counterparty'] or CAT.get(e['category'], '')} {amt}", lb._bubble(lb._header(head, amt, color), rows, btns))

def cards(con, rows):
    """一筆＝一張確認卡；一張照片好幾筆＝左右滑的輪播（最多 10 張）＋「全部正確」。"""
    import line_bot as lb
    if len(rows) == 1:
        return card(con, rows[0])
    bubbles = [card(con, r)[0]['contents'] for r in rows[:10]]
    ok = [r for r in rows if r['status'] == 'draft' and r['amount'] and r['date']]
    total = sum(r['amount'] for r in rows if r['status'] != 'void') // 100
    lines = [lb._t(f"・{(r['date'] or '')[5:]} {(r['counterparty'] or '（看不清楚）')[:14]} " + (f"{r['amount'] // 100:,}" if r['amount'] else '缺金額'), size='sm', wrap=True) for r in rows[:15]]
    summ = lb.flex(f'這張照片讀到 {len(rows)} 張收據', lb._bubble(lb._header(f'🧾 這張照片讀到 {len(rows)} 張收據', f'合計 NT$ {total:,}', '#0A84FF'), lines,
                   ([lb._btn(f'✅ 全部正確，記帳（{len(ok)} 筆）', data=f"a=lg_okall&b={rows[0]['batch']}", primary=True)] if ok else [])
                   + [lb._btn('到中控台逐筆看', uri=f'{_base(con)}/#ledger')], '上一則左右滑可以逐張改類別、品牌或作廢；缺金額的要到中控台補。'))
    return [{'type': 'flex', 'altText': f'記帳：{len(rows)} 張收據', 'contents': {'type': 'carousel', 'contents': bubbles}}] + summ

def _base(con):
    import line_bot as lb
    return lb.BASE_URL

QUICK = ('supplies', 'transport', 'goods', 'equipment', 'marketing', 'software', 'telecom', 'meal', 'vehicle', 'utility', 'fees', 'office', 'other')      # LINE 快速回覆最多 13 個；租金、保險、外包、稅捐在中控台改

def quick_cats(e):
    cats = [(k, n) for k, n, _ in EXPENSE if k in QUICK] if e['kind'] == 'expense' else [(k, n) for k, n, _ in INCOME]
    items = [{'type': 'action', 'action': {'type': 'postback', 'label': n[:20], 'data': f"a=lg_set&e={e['id']}&c={k}", 'displayText': f'類別：{n}'}} for k, n in cats]
    return [{'type': 'text', 'text': '要改成哪一類？（點下面；租金、保險、外包、稅捐請按「改金額、日期（中控台）」）', 'quickReply': {'items': items[:13]}}]

def quick_brands(e):
    items = [{'type': 'action', 'action': {'type': 'postback', 'label': SHORT[b], 'data': f"a=lg_setb&e={e['id']}&b={b}", 'displayText': f'品牌：{SHORT[b]}'}}
             for b in COMPANIES[COMPANY]['brands'] + ('',)]
    return [{'type': 'text', 'text': '這筆算哪個品牌？（三個品牌一起用的選「共同」）', 'quickReply': {'items': items}}]

def postback(con, act, data, uid, who):
    """LINE 確認卡的按鈕。回傳要回的訊息。"""
    if not who_ok(con, uid):
        return [{'type': 'text', 'text': '記帳只有管理層可以用。'}]
    try:
        e = get(con, data.get('e') or data.get('b') or '')
        if act == 'lg_ok':
            e = confirm(con, e['id'], who)
            return [{'type': 'text', 'text': f"✅ 已記帳：{e['date']} {e['counterparty'] or CAT.get(e['category'], '')} NT$ {e['amount'] // 100:,}（{SHORT.get(e['brand'], '共同')}・{CAT.get(e['category'], '')}）"}]
        if act == 'lg_cat':
            return quick_cats(e)
        if act == 'lg_br':
            return quick_brands(e)
        if act == 'lg_set':
            return card(con, set_field(con, e['id'], who, category=data.get('c', '')))
        if act == 'lg_setb':
            return card(con, set_field(con, e['id'], who, brand=data.get('b', '')))
        if act == 'lg_okall':
            rows = [r for r in siblings(con, e) if r['status'] == 'draft']
            done = []
            for r in rows:
                if r['amount'] and r['date']:
                    try:
                        done.append(confirm(con, r['id'], who))
                    except ValueError:            # 可能重複的跳過，留給人看
                        pass
            left = len(rows) - len(done)
            return [{'type': 'text', 'text': f"✅ 已記帳 {len(done)} 筆，共 NT$ {sum(r['amount'] for r in done) // 100:,}" + (f"\n還有 {left} 筆缺金額或日期，請到中控台「記帳 › 待確認」補。" if left else '')}]
        if act == 'lg_void':
            void(con, e['id'], who)
            return [{'type': 'text', 'text': '已作廢這筆（不會算進帳）。中控台「記帳」還看得到，要恢復可以在那裡改。'}]
    except ValueError as ex:
        return [{'type': 'text', 'text': str(ex)}]
    return None

# ───────────────────────── 月報 ─────────────────────────

def _month_end(month):
    first = dt.date.fromisoformat(month + '-01')
    return ((first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)).isoformat()

def _cust_brands(con):
    import brands
    return {r['id']: brands.of_customer(dict(r)) for r in con.execute('SELECT id,line_account,channel FROM customers')}

def auto_lines(con, month):
    """系統自動算的收支（分）：[{kind, category, brand, date, amount, desc, ref, basis}]。"""
    import board
    cb = _cust_brands(con)
    out = []
    demo = "COALESCE(o.review_note,'')!='示範工單'"
    taxed = {r[0] for r in con.execute("SELECT order_id FROM quotes WHERE taxed=1 AND COALESCE(order_id,'')!=''")}      # 含稅報價成立的工單
    for o in c.rows(con, f"SELECT o.* FROM orders o WHERE o.date LIKE ? AND o.status='completed' AND o.customer_id IS NOT NULL AND {demo} ORDER BY o.date,o.start", (month + '%',)):
        amt, basis = board.suggested_charge(con, o)
        site = con.execute('SELECT name FROM sites WHERE id=?', (o['site_id'],)).fetchone() if o['site_id'] else None
        b, nm = cb.get(o['customer_id'], 'demoshop'), (site[0] if site else '') or o['title']
        tax = (amt or 0) - round((amt or 0) / 1.05) if o['id'] in taxed and amt else 0      # A：含稅金額拆出 5% 銷項稅額，收入只算未稅
        out.append({'kind': 'income', 'category': 'service', 'brand': b, 'date': o['date'], 'amount': (amt or 0) - tax,
                    'desc': nm, 'ref': o['id'], 'basis': basis + ('（未稅）' if tax else ''), 'customer_id': o['customer_id'], 'estimate': basis != '已核定' or amt is None})
        if tax:
            out.append({'kind': 'tax', 'category': 'output_tax', 'brand': b, 'date': o['date'], 'amount': tax, 'desc': f'{nm} 營業稅', 'ref': o['id'], 'basis': '含稅報價拆稅', 'estimate': False})
    for r in c.rows(con, "SELECT l.*,i.customer_id,i.issued_at FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.order_id IS NULL AND i.status='issued' AND i.period=?", (month,)):
        is_tax = bool(re.search('營業稅|稅額', r['description'] or ''))          # A：帳單上的營業稅不算收入
        out.append({'kind': 'tax' if is_tax else 'income', 'category': 'output_tax' if is_tax else 'billed_other', 'brand': cb.get(r['customer_id'], 'demoshop'), 'date': (r['issued_at'] or month + '-01')[:10], 'amount': r['amount'],
                    'desc': r['description'], 'ref': r['invoice_id'], 'basis': '帳單', 'customer_id': r['customer_id'], 'estimate': False})
    for r in c.rows(con, f"SELECT p.*,o.date odate,o.title FROM payment_requests p JOIN orders o ON o.id=p.order_id WHERE o.status='cancelled' AND p.status='confirmed' AND p.kind!='month' AND o.date LIKE ? AND {demo}", (month + '%',)) \
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='payment_requests'").fetchone() else []:
        out.append({'kind': 'income', 'category': 'cancel_kept', 'brand': cb.get(r['customer_id'], 'demoshop'), 'date': r['odate'], 'amount': r['amount'],      # A：取消了、已收沒退的款
                    'desc': f"{r['title']}（取消）{r['label']}", 'ref': r['id'], 'basis': '已收不退', 'customer_id': r['customer_id'], 'estimate': False})
    for r in c.rows(con, "SELECT cr.*,i.customer_id FROM credits cr JOIN invoices i ON i.id=cr.invoice_id WHERE cr.date LIKE ?", (month + '%',)):
        out.append({'kind': 'income', 'category': 'discount', 'brand': cb.get(r['customer_id'], 'demoshop'), 'date': r['date'], 'amount': -abs(r['amount'] or 0),      # A：折讓扣收入
                    'desc': f"折讓：{r['reason'] or ''}", 'ref': r['id'], 'basis': '折讓', 'customer_id': r['customer_id'], 'estimate': False})
    has = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for r in (c.rows(con, "SELECT * FROM shop_orders WHERE status!='cancelled' AND paid_at LIKE ?", (month + '%',)) if 'shop_orders' in has else []):
        out.append({'kind': 'income', 'category': 'shop', 'brand': brand_ok(r['acc'] or 'demoshop') or 'demoshop', 'date': r['paid_at'][:10], 'amount': (r['total'] or 0) * 100,
                    'desc': '商城訂單', 'ref': r['id'], 'basis': r['paid_how'] or '已付款', 'customer_id': '', 'estimate': False})
    for a in c.rows(con, "SELECT a.*,o.date odate,o.customer_id ocust,o.title otitle,o.status ostatus,w.name wname FROM assignments a JOIN orders o ON o.id=a.order_id "
                         f"LEFT JOIN workers w ON w.id=a.worker_id WHERE o.date LIKE ? AND {demo} AND ((a.status='accepted' AND o.status='completed') OR (o.status='cancelled' AND a.amount>0)) "
                         "ORDER BY o.date", (month + '%',)):
        pay = board.worker_pay(con, a)
        re_ = a.get('reimburse') or 0
        b = cb.get(a['ocust'], 'demoshop') if a['ocust'] else 'demoshop'
        if pay is not None and pay - re_:
            out.append({'kind': 'expense', 'category': 'worker', 'brand': b, 'date': a['odate'], 'amount': pay - re_, 'desc': f"{a['wname'] or '夥伴'}・{a['otitle']}",
                        'ref': a['id'], 'basis': '取消補貼' if a['ostatus'] == 'cancelled' else '承攬', 'worker': a['wname'] or '', 'worker_id': a['worker_id'], 'estimate': False})
        if re_:
            out.append({'kind': 'expense', 'category': 'reimburse', 'brand': b, 'date': a['odate'], 'amount': re_, 'desc': f"{a['wname'] or '夥伴'} 代墊用品",
                        'ref': a['id'], 'basis': '代墊', 'worker': a['wname'] or '', 'worker_id': a['worker_id'], 'estimate': False})
    for r in (c.rows(con, "SELECT v.*,o.date odate,o.customer_id ocust,d.name vname FROM vendor_costs v LEFT JOIN orders o ON o.id=v.order_id LEFT JOIN vendors d ON d.id=v.vendor_id "
                          "WHERE COALESCE(v.status,'')!='cancelled' AND COALESCE(o.date,substr(v.created_at,1,10)) LIKE ?", (month + '%',)) if {'vendor_costs', 'vendors'} <= has else []):
        out.append({'kind': 'expense', 'category': 'vendor', 'brand': cb.get(r['ocust'], 'demoshop') if r['ocust'] else '', 'date': r['odate'] or (r['created_at'] or '')[:10],
                    'amount': r['amount'] or 0, 'desc': f"{r['vname'] or '廠商'}・{r['item'] or ''}", 'ref': r['id'], 'basis': '已付' if r['status'] == 'paid' else '未付', 'estimate': False})
    return out

def entries(con, month, status=None):
    ensure(con)
    q = 'SELECT * FROM ledger WHERE company=? AND date LIKE ?' + (' AND status=?' if status else '') + ' ORDER BY date DESC,created_at DESC'
    return c.rows(con, q, (COMPANY, month + '%') + ((status,) if status else ()))

def pending(con):
    """待確認（讀完還沒按確認、沒金鑰待補金額、還在讀）：不分月份。"""
    ensure(con)
    return c.rows(con, "SELECT * FROM ledger WHERE company=? AND status IN ('draft','reading') ORDER BY created_at", (COMPANY,))

def summary(con, month):
    """中控台「記帳」：各品牌收支、待確認、應收應付。"""
    import board
    c.date(month + '-01')
    ensure(con)
    bs = list(COMPANIES[COMPANY]['brands']) + ['']
    table = {}
    def add(kind, cat, b, amt):
        row = table.setdefault((kind, cat), {x: 0 for x in bs})
        row[b if b in row else ''] += amt or 0
    auto = auto_lines(con, month)
    for x in auto:
        add(x['kind'], x['category'], x['brand'], x['amount'])
    mine = entries(con, month)
    for e in mine:
        if e['status'] == 'ok':
            add(e['kind'], e['category'], e['brand'], e['amount'])
    order = [('income', k) for k, _ in AUTO_IN] + [('income', k) for k, _, _ in INCOME] + [('expense', k) for k, _ in AUTO_OUT] + [('expense', k) for k, _, _ in EXPENSE]
    rows = [{'kind': k, 'category': cat, 'name': CAT[cat], 'auto': cat in dict(AUTO_IN + AUTO_OUT), 'by': table[(k, cat)], 'total': sum(table[(k, cat)].values())}
            for k, cat in order if (k, cat) in table and any(table[(k, cat)].values())]
    tot = {b: {'income': sum(r['by'][b] for r in rows if r['kind'] == 'income'), 'expense': sum(r['by'][b] for r in rows if r['kind'] == 'expense')} for b in bs}
    for b in bs:
        tot[b]['net'] = tot[b]['income'] - tot[b]['expense']
    allt = {k: sum(tot[b][k] for b in bs) for k in ('income', 'expense', 'net')}
    bb = board.billing_board(con, month)
    unpaid_workers = sum(x['pay'] or 0 for w in bb['payroll'] for x in w['lines'] if not x['settled'])
    est = [x for x in auto if x.get('estimate')]
    out_tax = {b: sum(x['amount'] for x in auto if x['kind'] == 'tax' and (x['brand'] if x['brand'] in bs else '') == b) for b in bs}
    return {'month': month, 'output_tax': out_tax, 'output_tax_all': sum(out_tax.values()), 'company': COMPANIES[COMPANY]['name'], 'brands': [[b, SHORT[b]] for b in bs], 'rows': rows, 'totals': tot, 'all': allt,
            'estimates': {'n': len(est), 'amount': sum(x['amount'] for x in est)},
            'entries': [e for e in mine if e['status'] in ('ok', 'void')], 'pending': pending(con),
            'ar': {'to_bill': bb['totals']['to_bill'], 'balance': bb['totals']['balance'], 'overdue': bb['totals']['overdue']},
            'ap': {'workers': unpaid_workers, 'vendors': sum(x['amount'] for x in auto if x['category'] == 'vendor' and x['basis'] == '未付')},
            'cats': {'expense': [[k, n, d] for k, n, d in EXPENSE], 'income': [[k, n, d] for k, n, d in INCOME]}, 'doc_types': list(DOC_TYPES), 'methods': [m for m in METHODS if m],
            'key': key_status(con), 'package': package_info(month), 'tax_id': ix.setting(con, 'ledger_tax_id')}

# ───────────────────────── 記帳士包 ─────────────────────────

def _pkg_path(month):
    d = c.DATA / 'ledger' / 'packages'
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{COMPANIES[COMPANY]['name']}_{month}_記帳士包.zip"

def package_info(month):
    p = _pkg_path(month)
    return {'exists': p.exists(), 'at': dt.datetime.fromtimestamp(p.stat().st_mtime, c.TZ).isoformat(timespec='minutes') if p.exists() else ''}

def _csv(rows, head):
    buf = io.StringIO()
    buf.write('﻿')                    # Excel 開中文不亂碼
    w = csv.writer(buf)
    w.writerow(head)
    w.writerows(rows)
    return buf.getvalue().encode('utf-8')

def _yuan(cents):
    return round((cents or 0) / 100)

def package(con, month):
    """做記帳士包（zip bytes），同時存一份在 data/ledger/packages/。"""
    s = summary(con, month)
    auto = auto_lines(con, month)
    names = {r['id']: r['name'] for r in con.execute('SELECT id,name FROM customers')}
    bs = [b for b, _ in s['brands']]
    z = io.BytesIO()
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as f:
        f.writestr('1_收支總表.csv', _csv(
            [[('收入' if r['kind'] == 'income' else '支出'), r['name'], *[_yuan(r['by'][b]) for b in bs], _yuan(r['total']), '系統自動' if r['auto'] else '手動記帳']
             for r in s['rows']] + [['合計', '收入', *[_yuan(s['totals'][b]['income']) for b in bs], _yuan(s['all']['income']), ''],
                                    ['合計', '支出', *[_yuan(s['totals'][b]['expense']) for b in bs], _yuan(s['all']['expense']), ''],
                                    ['合計', '淨額', *[_yuan(s['totals'][b]['net']) for b in bs], _yuan(s['all']['net']), '']],
            ['收支', '類別', *[SHORT[b] for b in bs], '合計（元）', '來源']))
        f.writestr('2_收入明細.csv', _csv(
            [[x['date'], SHORT.get(x['brand'], ''), CAT[x['category']], names.get(x.get('customer_id'), ''), x['desc'], _yuan(x['amount']), x['basis'] + ('（預估）' if x.get('estimate') else ''), x['ref']]
             for x in auto if x['kind'] == 'income'] +
            [[e['date'], SHORT.get(e['brand'], ''), CAT.get(e['category'], ''), e['counterparty'], e['memo'], _yuan(e['amount']), '手動記帳', e['id']]
             for e in s['entries'] if e['status'] == 'ok' and e['kind'] == 'income'],
            ['日期', '品牌', '類別', '客戶／對象', '說明', '金額（元）', '依據', '系統編號']))
        photos = {}
        rows = []
        for e in sorted((e for e in s['entries'] if e['status'] == 'ok' and e['kind'] == 'expense'), key=lambda e: e['date']):
            fn = ''
            p = photo_path(e)
            if p and p.exists():
                fn = f"憑證/{e['date'].replace('-', '')}_{CAT.get(e['category'], '')}_{_yuan(e['amount'])}元_{e['id'][-6:]}.jpg"
                photos[fn] = p
            rows.append([e['date'], SHORT.get(e['brand'], ''), CAT.get(e['category'], ''), e['counterparty'], e['tax_id'], e['doc_type'], e['doc_no'],
                         _yuan(e['amount']), _yuan(e['tax']), e['method'], e['memo'], fn or '（沒有照片）', e['id']])
        for x in auto:
            if x['kind'] == 'expense' and x['category'] in ('reimburse', 'vendor'):
                rows.append([x['date'], SHORT.get(x['brand'], ''), CAT[x['category']], x['desc'], '', '', '', _yuan(x['amount']), 0, '', x['basis'], '（照片在系統工單裡）', x['ref']])
        f.writestr('3_支出明細.csv', _csv(rows, ['日期', '品牌', '類別', '店家／對象', '賣方統編', '憑證類型', '發票號碼', '金額（元）', '稅額（元）', '付款方式', '說明', '憑證檔名', '系統編號']))
        per = {}
        for x in auto:
            if x['category'] in ('worker', 'reimburse'):
                d = per.setdefault(x.get('worker_id') or x.get('worker'), {'name': x.get('worker') or '', 'n': set(), 'pay': 0, 're': 0})
                d['n'].add(x['ref'])
                d['pay' if x['category'] == 'worker' else 're'] += x['amount']
        f.writestr('4_夥伴承攬明細.csv', _csv(
            [[d['name'], len(d['n']), _yuan(d['pay']), _yuan(d['re']), _yuan(d['pay'] + d['re'])] for d in sorted(per.values(), key=lambda d: d['name'])],
            ['夥伴', '場數', '承攬報酬（元）', '代墊用品（元）', '合計（元）']))
        for fn, p in photos.items():
            f.write(p, fn)
        drafts = [e for e in s['pending'] if (e['date'] or '')[:7] <= month]
        f.writestr('0_說明.txt', (
            f"{s['company']}　{month} 記帳士包（{c.now()[:16].replace('T', ' ')} 產生）\n\n"
            '1_收支總表：各品牌（示範服務店／生活線／企業線／共同）收入、支出、淨額。\n'
            '2_收入明細：完工服務（依服務日）、帳單其他費用、商城（依付款日）、手動記的其他收入。\n'
            '3_支出明細：手動記帳（附憑證照片，在「憑證」資料夾）＋夥伴代墊用品、配合廠商外包。\n'
            '4_夥伴承攬明細：服務人員每場計酬（承攬），依完工日彙總。扣繳、二代健保請記帳士依所得類別判斷。\n\n'
            f"注意：\n- 有 {s['estimates']['n']} 場完工還沒核定金額，收入用系統預估（{_yuan(s['estimates']['amount']):,} 元），核定後會變。\n"
            f"- 還有 {len(drafts)} 筆憑證待確認，沒有算進這份。\n"
            '- 收入以「服務完成」認列（管理帳）；實際開立發票／收據的時點請記帳士確認。\n').encode('utf-8'))
    data = z.getvalue()
    _pkg_path(month).write_bytes(data)
    return data

# ───────────────────────── 每天 ─────────────────────────

def tick(con, now=None):
    """自動化：卡在「讀取中」超過 2 分鐘的再讀一次（重開機、Claude 暫時不通）；每月 5 日 10:00 後做上個月的記帳士包＋管理群一張卡。"""
    ensure(con)
    n = 0
    old = (dt.datetime.now(c.TZ) - dt.timedelta(minutes=2)).isoformat(timespec='seconds')
    stuck = [r[0] for r in con.execute("SELECT id FROM ledger WHERE status='reading' AND ocr_state IN ('wait','retry') AND created_at<? AND tries<3", (old,))]
    if stuck:
        kick(stuck); n += len(stuck)
    now = now or dt.datetime.now(c.TZ)
    last = (now.replace(day=1) - dt.timedelta(days=1)).strftime('%Y-%m')
    if now.day >= 5 and now.strftime('%H:%M') >= '10:00' and ix.setting(con, f'ledger_pkg:{last}') != 'done':
        ix.set_setting(con, f'ledger_pkg:{last}', 'done')
        s = summary(con, last)
        ix.set_setting(con, f'ledger_pkg_sum:{last}', json.dumps(s['all']))
        if s['rows'] or s['pending']:
            package(con, last)
            _announce(con, s)
            n += 1
    if now.strftime('%H:%M') >= '10:00' and ix.setting(con, 'ledger_adj_day') != now.date().isoformat():       # A：包交出後又有變動 → 重做＋通知
        ix.set_setting(con, 'ledger_adj_day', now.date().isoformat())
        d1 = now.replace(day=1) - dt.timedelta(days=1)
        for m in (d1.strftime('%Y-%m'), (d1.replace(day=1) - dt.timedelta(days=1)).strftime('%Y-%m')):      # 上個月、上上個月
            old = ix.setting(con, f'ledger_pkg_sum:{m}')
            if ix.setting(con, f'ledger_pkg:{m}') != 'done' or not old:
                continue
            o, s = json.loads(old), summary(con, m)
            if (o.get('income'), o.get('expense')) != (s['all']['income'], s['all']['expense']):
                ix.set_setting(con, f'ledger_pkg_sum:{m}', json.dumps(s['all']))
                package(con, m)
                import line_bot as lb
                di, de = s['all']['income'] - (o.get('income') or 0), s['all']['expense'] - (o.get('expense') or 0)
                lb.admin_notify(con, f"📒 {int(m[5:])} 月記帳士包交出後有變動，已重做", [('收入', f"{'+' if di >= 0 else ''}{di // 100:,} 元"), ('支出', f"{'+' if de >= 0 else ''}{de // 100:,} 元"),
                                ('處理', '中控台「記帳」重新下載這個月的記帳士包，跟記帳士說是上月調整')], '/#ledger', '#C47F00')
                n += 1
    return n

def _announce(con, s):
    import line_bot as lb
    y, m = s['month'].split('-')
    rows = [lb._row(SHORT[b] if b else '共同', f"收 {s['totals'][b]['income'] // 100:,}／支 {s['totals'][b]['expense'] // 100:,}") for b, _ in s['brands'] if s['totals'][b]['income'] or s['totals'][b]['expense']]
    rows.append(lb._row('合計淨額', f"NT$ {s['all']['net'] // 100:,}"))
    if s['estimates']['n']:
        rows.append(lb._t(f"有 {s['estimates']['n']} 場完工還沒核定金額（用預估算），結帳頁核定後重新下載就會更新。", size='xs', color=lb.SUB, wrap=True, margin='md'))
    if s['pending']:
        rows.append(lb._t(f"還有 {len(s['pending'])} 筆憑證待確認，沒有算進去。", size='xs', color=lb.SUB, wrap=True, margin='md'))
    m_ = lb.flex(f'{y}/{int(m)} 記帳士包做好了', lb._bubble(lb._header(s['company'], f'📒 {int(m)} 月記帳士包做好了', '#2E7D32'), rows,
                 [lb._btn('到中控台下載', uri=f"{_base(con)}/#ledger", primary=True)], '收支總表、收入／支出／夥伴承攬明細＋憑證照片；下載後交給記帳士。'))
    lb.to_managers(con, 'ledger_pkg', m_[0]['altText'], m_, tag=f'{int(m)} 月的記帳士包做好了，請下載交給記帳士 📒')

def standing(con):
    """10:30 那一則：待確認的憑證（超過 1 天）。"""
    ensure(con)
    old = (dt.datetime.now(c.TZ) - dt.timedelta(days=1)).isoformat(timespec='seconds')
    n = con.execute("SELECT COUNT(*) FROM ledger WHERE company=? AND status IN ('draft','reading') AND created_at<?", (COMPANY, old)).fetchone()[0]
    return f'{n} 筆（中控台「更多 › 記帳」確認或補金額）' if n else ''
