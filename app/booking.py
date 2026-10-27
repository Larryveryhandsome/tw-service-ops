"""客戶估價需求 → 系統初估（照片＋規模）→ 店長審核修改 → 正式報價單傳給客戶 → 線上簽收 → 建客戶／案場／工單 → 店長挑人派工。
另：現場估價（社區、大案場，或客戶自選付費現場估價）、夥伴報到（自動建立／對應夥伴資料）。"""
import base64
import datetime as dt
import io
import json
import re
import secrets
import time
import warnings
import core as c
import integrations as ix
import pricing
import brands

SLOTS = {'09:00–13:00': '09:00', '13:00–17:00': '13:00', '08:00–12:00': '08:00', '14:00–18:00': '14:00'}
COMPANY = {'review_url': '', 'name': '示範服務店', 'tagline': 'BOOK · SERVE · DONE', 'slogan': '專業服務 · 每一次都準時、清楚、安心', 'address': '○○縣○○市示範路 1 號',
           'bank': '示範銀行 (000)', 'holder': '示範服務股份有限公司', 'account': '000000000000'}
# 舊客戶綁定訊息（店長一對一貼給客戶）。{name}＝客戶稱呼、{url}＝綁定連結。店長可在「報價 › 報價規則與資料 › 給客戶的打招呼訊息」修改。
BIND_MSG = {
    'demoshop': '''{name} 您好 😊 我是示範服務店！
謝謝您一直以來的支持，跟您分享一個好消息：我們的新系統上線了 🎉

之後的預約、服務人員安排、服務前一天提醒、完成照片，都會直接在這個 LINE 通知您，不用再來回確認。
👉 麻煩點下面連結、按「傳送」就完成（14 天內有效）：
{url}

另外，示範服務店也整合了許多長期配合的專業廠商，除了一般到府服務，像是：
・窗簾拆洗、送洗
・高空清洗（外牆、高處玻璃）
以及其他各式各樣的工程，都可以一起幫您安排 🙌

最近家裡或店裡有沒有想整理、想處理的地方呢？直接在這裡跟我們說就好，我們幫您評估、報價（原則上以照片或現場確認為準）。''',
    'bizline': '''{name} 您好 😊 我是企業服務線！
謝謝您一直以來的支持，跟您分享一個好消息：我們的新系統上線了 🎉

之後的房務預約、服務人員安排、前一天提醒、服務前後照片，都會直接在這個 LINE 通知您，不用再來回確認。
👉 麻煩點下面連結、按「傳送」就完成（14 天內有效）：
{url}

另外，我們也整合了許多長期配合的專業廠商，除了定期駐點服務，像是：
・窗簾拆洗、布品送洗
・高空清洗（外牆、高處玻璃）
以及其他各式各樣的工程，都可以一起幫您安排 🙌

最近店家有沒有想整理、想處理的地方，或是旺季、連假的排班需求呢？直接在這裡跟我們說就好，我們幫您評估、報價（原則上以照片或現場確認為準）。''',
    'homeline': '''{name} 您好 😊 我是生活小幫手！
謝謝您一直以來的支持，跟您分享一個好消息：我們的新系統上線了 🎉

之後的預約、服務人員安排、前一天提醒、完成照片，都會直接在這個 LINE 通知您，不用再來回確認。
👉 麻煩點下面連結、按「傳送」就完成（14 天內有效）：
{url}

另外，我們也整合了許多長期配合的專業廠商，像是：
・窗簾拆洗、送洗
・高空清洗（外牆、高處玻璃）
以及其他各式各樣的工程，都可以一起幫您安排 🙌

最近家裡有沒有想整理、想處理的地方呢？直接在這裡跟我們說就好，我們幫您評估、報價（原則上以照片或現場確認為準）。'''}

GENERIC_BIND = '''{name} 您好 😊 我是{brand}！
我們的新系統上線了 🎉 之後的預約、人員安排、前一天提醒，都會直接在這個 LINE 通知您。
👉 麻煩點下面連結、按「傳送」就完成（14 天內有效）：
{url}

最近有什麼需求，也歡迎直接在這裡留言告訴我們 🙌'''

def bind_template(con, brand):
    return ix.setting(con, f'bind_message_{brand}') or BIND_MSG.get(brand) or GENERIC_BIND.replace('{brand}', brands.name(brand))

GENERIC_TEXT = '我是老客戶，要綁定 LINE'

def generic_bind(con, brand):
    """一條連結傳給很多客戶 → 通用綁定訊息（不帶姓名、不帶綁定碼；客戶送出後用 LINE 名稱／電話找舊資料）。"""
    import urllib.parse, line_bot
    bid = line_bot.basic_id(con, brand)
    if not bid:
        raise ValueError(f'{brands.name(brand)} 的官方 LINE 還沒接上系統')
    url = f'https://line.me/R/oaMessage/{bid}/?' + urllib.parse.quote(GENERIC_TEXT)
    t = bind_template(con, brand).replace('{name} ', '').replace('{name}', '').replace('（14 天內有效）', '')
    t = t.replace('按「傳送」就完成', '按「傳送」，系統會幫您找到您的資料（找不到會請您留電話）')
    return {'url': url, 'text': t.replace('{url}', url).strip()}

NEW_BIND = '''{name} 您好 😊 我是{brand}！
之後的預約、服務前一天提醒、完成照片，都會在官方 LINE 通知您。
👉 兩步驟完成綁定（14 天內有效）：
① 點連結加入好友：{add}
② 在聊天室傳送：綁定 {code}
完成後就會收到「綁定完成」🙌'''

STEPS_OLD = '👉 麻煩點下面連結、按「傳送」就完成（14 天內有效）：\n{url}'

def bind_message(con, brand, name, url, code='', add='', new=False):
    """新客戶短版；老客戶照原本那則，連結那段改成「加好友＋傳綁定碼」。"""
    if code and add:
        steps = f'👉 兩步驟完成綁定（14 天內有效）：\n① 點連結加入好友：{add}\n② 在聊天室傳送：綁定 {code}'
        if new:
            return NEW_BIND.replace('{brand}', brands.name(brand)).replace('{name}', name or '').replace('{add}', add).replace('{code}', code).replace('{name} 您好', '您好').strip()
        t = bind_template(con, brand)
        return (t.replace(STEPS_OLD, steps) if STEPS_OLD in t else t.replace('{url}', steps)).replace('{name}', name or '')
    return bind_template(con, brand).replace('{name}', name or '').replace('{url}', url)

def company(con, brand='demoshop'):
    """報價單抬頭、地址、匯款資訊：每個品牌各自一份（示範服務店沿用原本的設定）。"""
    base = {**COMPANY, 'name': brands.name(brand)} if brand == 'demoshop' else \
        {'name': brands.name(brand), 'tagline': '', 'address': '', 'bank': '', 'holder': '', 'account': ''}    # 其他品牌沒設定就不印示範服務店的匯款資料
    try:
        return {**base, **json.loads(ix.setting(con, pricing.key('company', brand), '{}'))}
    except ValueError:
        return base

VALID_DAYS = 5                    # 報價期限預設＝傳出日＋5 天（負責人確認，不改）

def valid_until(q):
    """報價期限（PDF、線上報價、LINE 報價卡、簽收、過期追蹤都用這個）：
    管理層在中控台指定了（quotes.valid_until）就用指定的；沒指定＝傳出日＋VALID_DAYS（還沒傳＝今天＋VALID_DAYS）。"""
    v = q['valid_until'] if 'valid_until' in q.keys() else ''
    if v:
        return v
    return (dt.date.fromisoformat((q['sent_at'] or c.now())[:10]) + dt.timedelta(days=VALID_DAYS)).isoformat()
MIN_PHOTOS = 3
MAX_PHOTOS = 20
STAGE = {'drafting': '草稿（還沒交給店長）', 'photos': '等客戶傳照片', 'review': '待主管確認', 'sent': '已傳給客戶', 'site_visit': '現場估價', 'signed': '已簽收', 'cancelled': '已取消'}

def full_address(f):
    """鄉鎮＋路名（客戶表單分兩欄填：area＝臺中市西屯區、address＝○○縣○○市示範路 1 號）。報價單、案場地址（夥伴導航）都用完整地址。"""
    area, addr = str((f or {}).get('area') or '').strip(), str((f or {}).get('address') or '').strip()
    if not area:
        return addr
    n = lambda s: s.replace('台', '臺').replace(' ', '')
    if n(addr).startswith(n(area)) or (len(area) >= 3 and n(area)[-3:] in n(addr)[:10]):
        return addr
    return area + addr

def _sent_label(con, q, linked):
    """「已傳給客戶」只在真的傳到時才這樣寫（岳勳：沒綁 LINE 卻顯示已傳）。"""
    if q['stage'] != 'sent' or q['status'] in ('signed', 'cancelled'):
        return None
    if linked:
        return '已傳給客戶（LINE）'
    return '已傳給客戶（連結）' if q.get('link_sent') else '已確認・還沒傳給客戶（沒綁 LINE）'

def _rate(con, ip, key):
    hour_ago = (dt.datetime.now(c.TZ) - dt.timedelta(hours=1)).isoformat(timespec='seconds')
    if con.execute(f"SELECT COUNT(*) FROM {key} WHERE ip=? AND created_at>?", (ip, hour_ago)).fetchone()[0] >= 8:
        raise PermissionError('送出太多次，請直接在官方 LINE 留言')

def _clean(p):
    name = str(p.get('name', '')).strip()[:60]; phone = re.sub(r'[^\d+\-]', '', str(p.get('phone', '')))[:20]
    if p.get('website'):
        raise ValueError('送出失敗')
    if not name or len(re.sub(r'\D', '', phone)) < 8:
        raise ValueError('請留下稱呼與手機')
    return name, phone

PREFIX = {'bizline': 'G', 'homeline': 'M'}          # 盤點：三品牌各自流水號（示範服務店沿用純數字，企業線 G、生活線 M 開頭）

def quote_no(con, brand='demoshop'):
    pre = PREFIX.get(brand, ''); day = dt.datetime.now(c.TZ).strftime('%Y%m%d')
    last = (con.execute("SELECT id FROM quotes WHERE id LIKE ? ORDER BY length(id) DESC, id DESC LIMIT 1", (pre + day + '%',)).fetchone() or [None])[0]
    n = int(last[len(pre) + 8:]) + 1 if last else 1
    return f'{pre}{day}{n:02d}'

def _end(start, hours):
    h, m = map(int, start.split(':')); t = min(h * 60 + m + int(round(float(hours or 3) * 60)), 23 * 60 + 59)
    return f'{t // 60:02d}:{t % 60:02d}'

def _q(con, token=None, qid=None):
    q = con.execute('SELECT * FROM quotes WHERE ' + ('token=?' if token else 'id=?'), (token or qid,)).fetchone()
    if not q:
        raise ValueError('找不到這張報價單')
    return dict(q)

def _items(q):
    try:
        if q.get('items'):
            return json.loads(q['items'])
        est = json.loads(q.get('estimate') or '{}')        # 舊格式報價（改版前）：用系統估價品項
        return [{'title': i['label'], 'sub': '', 'scope': [], 'amount': i['amount']} for i in est.get('items', []) if i.get('amount') is not None]
    except ValueError:
        return []

def _scope_from_items(items):
    """報價單沒寫工作範圍 → 用品項（含另計、單價項目）組成工單的工作範圍，夥伴才知道要做什麼。"""
    out = []
    for i in items:
        t = i.get('title', '') + (f"（{i['sub']}）" if i.get('sub') else '')
        tag = '［另計］' if i.get('separate') else '［單價，依實際數量］' if i.get('per_unit') else ''
        sc = '、'.join(i.get('scope') or [])
        out.append(f"{tag}{t}" + (f"：{sc}" if sc else ''))
    return chr(10).join(out)[:2000]

def _worker_scope(scope, f):
    """備註改成內部用、夥伴看不到 → 客人寫的交代、樓層／電梯／寵物要寫進服務範圍（範圍審核後夥伴才看得到）。"""
    extra = []
    site = '、'.join(x for x in (f"{f.get('floors')} 樓" if f.get('floors') else '', {'yes': '有電梯', 'no': '沒電梯'}.get(str(f.get('lift') or ''), ''),
                                 '有寵物' if f.get('pets') else '') if x)
    if site:
        extra.append(f'現場：{site}')
    if (f.get('notes') or '').strip():
        extra.append(f"客戶交代：{str(f['notes']).strip()[:300]}")
    import cleanlevel
    if cleanlevel.worker_line(f):                 # 業主確認過的服務等級、髒污程度
        extra.append(cleanlevel.worker_line(f))
    return '\n'.join(x for x in [(scope or '').rstrip()] + [e for e in extra if e not in (scope or '')] if x)

def _extras_note(items):
    """另計／單價項目：簽收時不計入總額，完工要依實際數量加收，寫進工單備註提醒結帳。"""
    ex = [f"{i.get('title', '')}{('（' + i['sub'] + '）') if i.get('sub') else ''}" for i in items if i.get('per_unit') or i.get('separate')]      # 稽核：不寫單價（工單備註夥伴看得到）
    return ('另計項目（完工依實際數量另計，價格看報價單）：' + '；'.join(ex)) if ex else ''

def _totals(items, taxed):
    sub = sum(int(i.get('amount') or 0) for i in items if not i.get('per_unit') and not i.get('separate'))      # 單價項目（每間、每單位）不計入小計
    tax = round(sub * 0.05) if taxed else 0
    return sub, tax, sub + tax

def _photo_count(con, qid):
    return con.execute('SELECT COUNT(*) FROM quote_photos WHERE quote_id=?', (qid,)).fetchone()[0]

HEARD = ('Facebook／IG／Threads', 'LINE 官方', 'Google 搜尋', '親友推薦', '社區／管委會', '其他')
WEEKDAYS = ('一', '二', '三', '四', '五', '六', '日')

def _more(p):
    """選填欄位整理（不合格的直接丟掉，不擋送出）。"""
    out = {}
    e = str(p.get('email') or '').strip()[:120]
    if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', e):
        out['email'] = e
    d = [x for x in WEEKDAYS if x in [str(v) for v in (p.get('pref_days') or [])]] if isinstance(p.get('pref_days'), list) else []
    if d:
        out['pref_days'] = d
    if p.get('heard') in HEARD:
        out['heard'] = p['heard']
    r = re.sub(r'[\r\n]', ' ', str(p.get('referrer') or '')).strip()[:40]
    if r:
        out['referrer'] = r
    return out

def create_quote(con, p, ip):
    """客戶送出估價需求：系統依空間＋狀況分類並初估（偏高），產生待審報價（客戶還看不到正式報價）。
    mode=quote：照片線上報價；mode=site_visit：現場估價（社區、超大規模，或客戶自選付費現場估價）。"""
    _rate(con, ip, 'quotes')
    if con.execute('SELECT COUNT(*) FROM quotes WHERE ip=? AND created_at>?', (ip, (dt.datetime.now(c.TZ) - dt.timedelta(days=1)).isoformat())).fetchone()[0] >= 20:
        raise PermissionError('今天送出太多次，請直接在官方 LINE 留言')
    name, phone = _clean(p)
    if not str(p.get('address', '')).strip():
        raise ValueError('請填地址')
    m_ = re.search(r'([一-鿿]{2}[縣市])([一-鿿]{1,3}[鄉鎮市區])', re.sub(r'\s', '', str(p.get('address') or '')))
    if m_ and m_.group(1) + m_.group(2) != str(p.get('area') or ''):      # 地址寫了縣市鄉鎮就以地址為準（客人常選錯下拉）
        p = {**p, 'area': m_.group(1) + m_.group(2)}
    est = pricing.estimate(con, p)
    mode = 'site_visit' if est['mode'] == 'site_visit' else 'quote'      # 單次超過門檻、社區、大規模 → 現場估價
    dd_ = est.get('service_type') == '垃圾代丟'
    if dd_:                                   # 垃圾代丟：月方案要選對天數；第一次收＝開始日之後第一個選的星期；晚上 8 點
        import daidiu
        _p, _wds = daidiu.check(p)
        _x = daidiu.extra(p)
        p = {**p, 'start_time': daidiu._hours(_x)[0], 'weekdays': _wds, **_x}      # 每日檢查：報價單時間＝業主選的收取時間（以前固定 20:00）
        if p.get('date') and _wds:
            p['date'] = daidiu.first_date(p['date'], _wds)
        elif p.get('date') and _x['rest_days'] and dt.date.fromisoformat(p['date']).weekday() in _x['rest_days']:
            raise ValueError('選的日期剛好是休息日（不收），請換一天')
    d = p.get('date', '')
    if d:
        c.date(d)
        if d <= dt.datetime.now(c.TZ).date().isoformat():
            raise ValueError('請選明天以後的日期')
    start = p.get('start_time') if re.fullmatch(r'\d\d:\d\d', str(p.get('start_time') or '')) else SLOTS.get(p.get('slot'), '09:00')
    acc = brands.pick(p.get('account'))
    via = p.get('photo_via') if p.get('photo_via') in ('line', 'human') else 'upload'      # human＝不方便拍照，轉專人
    qid = quote_no(con, acc); token = secrets.token_urlsafe(18)
    st = est['service_type']
    form = {k: p.get(k) for k in ('name', 'phone', 'line_name', 'area', 'address', 'company', 'tax_id', 'invoice', 'space', 'condition', 'freq', 'size', 'rooms', 'baths',
                                  'floors', 'lift', 'kind', 'pets', 'ladder', 'addons', 'notes', 'slot', 'date', 'hours', 'people', 'start_time', 'line',
                                  'bnb_name', 'entry', 'service', 'bags', 'trash_bags', 'weekdays', 'pickup', 'rest_days', 'truck_alt', 'svc_items')}
    form.update(_more(p))                # 雲端搬本機：Google 表單原有的 Email／偏好星期／得知來源／推薦人      # 付款方式客戶不能自己選（pay_method 只收管理層設定的）
    if re.match(r'^mk-[a-z0-9-]{1,60}$', str(p.get('src') or '')):      # 行銷：從哪一篇貼文的追蹤連結來的
        form['campaign'] = str(p['src'])
    import sitetools                    # 開單前要知道需不需要自備工具（時數制服務不用）
    if not p.get('svc_items') and sitetools.needs_line(con, p.get('line'), acc):
        if p.get('tools') not in sitetools.KINDS:
            raise ValueError('請選「現場工具」：服務當天需要我們自備工具嗎？')
        form['tools'] = p['tools']; form['tools_note'] = str(p.get('tools_note') or '').strip()[:200]
    form['service_type'] = st
    if est.get('freq'):
        form['freq'] = est['freq']          # 生活線月方案：每月 N 次（收款走月訂閱）
    import regions as rg
    form['outside'] = rg.outside(con, acc, p.get('area'))       # 不在品牌直接服務的縣市：照收，幫客戶找配合的夥伴
    sc = pricing.scopes(con, acc)
    items = pricing.draft_items(form, est, sc)          # 現場估價也先備好系統初估品項，店長改回線上報價時直接可用
    people, hours = est.get('people', 1), est.get('hours', 0)
    fee = est['visit_fee'] if mode == 'site_visit' else 0
    total = est.get('total') or est.get('internal_total') or 0
    con.execute('INSERT INTO quotes(id,token,created_at,account,form,estimate,total,date,start,end,ip,ua,stage,items,taxed,people,hours,scope,photo_via,mode,visit_fee) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (qid, token, c.now(), acc, json.dumps(form, ensure_ascii=False), json.dumps(est, ensure_ascii=False), total * 100, d, start, _end(start, hours) if hours else '',
                 ip, '', 'photos', json.dumps(items, ensure_ascii=False), 1 if p.get('invoice') else 0, people, hours,
                 __import__('daidiu').scope_text(p) if dd_ else '\n'.join(sc.get(st, [])), via, mode, fee))
    import dispatch
    detail = {'報價單': qid, '類型': st, '初估（內部）': f"NT$ {total:,}（未稅）" if total else '—', '規模': p.get('size', ''), '頻率': p.get('freq', ''),
              '加購': '、'.join(list(p.get('addons') or []) + ([f"垃圾清收 {pricing.trash_bags(p.get('trash_bags'), 10)} 袋"] if pricing.trash_bags(p.get('trash_bags'), 10) else [])),
              '照片': '用 LINE 傳' if via == 'line' else '表單上傳'}
    if st == pricing.TRASH:
        detail['袋數'] = f"{est.get('bags') or 1} 袋（每袋約 100–120 公升）"; detail.pop('規模', None); detail.pop('頻率', None)
    if mode == 'site_visit':
        detail['現場估價費'] = f'NT$ {fee:,}' if fee else '免費'
    if dd_:                                   # 代丟要問的都在報價單上，不用再手動問客人
        detail.update(daidiu.detail(p)); detail.pop('規模', None)
    for k_, lab_ in (('pref_days', '偏好星期'), ('heard', '得知來源'), ('referrer', '推薦人'), ('email', 'Email')):
        if form.get(k_):
            detail[lab_] = '、'.join(form[k_]) if isinstance(form[k_], list) else form[k_]
    if form['outside']:
        detail['服務區域'] = f"{p.get('area')}（不在直接服務範圍，需找配合夥伴）"
    inq = dispatch.create_inquiry(con, {**p, 'service_type': st, 'preferred': f"{d} {p.get('slot', '')}".strip(), 'detail': detail}, ip, notify_admin=False)
    con.execute("UPDATE inquiries SET kind=?,status='quoted' WHERE id=?", ('site_visit' if mode == 'site_visit' else 'quote', inq['id']))
    if p.get('t'):                     # 從 LINE「現在預約（資料已帶入）」來的：直接對應這位客戶，不用再傳預約編號
        try:
            import worker_portal
            bacc, buid, bcu = worker_portal._cust(con, str(p['t']))
            if bacc != acc:
                raise PermissionError('品牌不同')            # 每日檢查：別的品牌的連結碼不帶入（以前估價會掛到別的品牌）
            con.execute('UPDATE inquiries SET line_user_id=?,customer_id=? WHERE id=?', (buid, bcu['id'], inq['id']))
            con.execute('UPDATE quotes SET customer_id=? WHERE id=?', (bcu['id'], qid)); inq['line'] = None
        except PermissionError:
            pass
    if not con.execute('SELECT customer_id FROM quotes WHERE id=?', (qid,)).fetchone()[0]:      # 電話跟既有客戶一樣 → 直接對上（同品牌、只有一位才對）
        dg = re.sub(r'\D', '', phone or '')
        hit = [x['id'] for x in c.rows(con, f"SELECT id,phone FROM customers WHERE phone!='' AND COALESCE(status,'') NOT IN ('merged','deleted') AND {brands.cust_sql()}=?", (acc,))
               if len(dg) >= 9 and re.sub(r'\D', '', x['phone'])[-9:] == dg[-9:]]
        if len(hit) == 1:
            con.execute('UPDATE quotes SET customer_id=? WHERE id=?', (hit[0], qid))
            con.execute('UPDATE inquiries SET customer_id=? WHERE id=? AND customer_id IS NULL', (hit[0], inq['id']))
            c.audit(con, 'quote_customer_match', 'quotes', qid, after={'customer_id': hit[0], 'by': '電話相同'})
    con.execute('UPDATE quotes SET inquiry_id=? WHERE id=?', (inq['id'], qid))
    if p.get('r'):                       # 推薦給朋友：帶推薦碼的線上估價 → 記下推薦人
        import referral
        referral.on_quote(con, qid, acc, str(p['r'])[:20], con.execute('SELECT customer_id FROM quotes WHERE id=?', (qid,)).fetchone()[0])
    return {'token': token, 'no': qid, 'mode': mode, 'service_type': st, 'visit_fee': fee, 'line': inq.get('line'), 'outside': form['outside'],
            'min_photos': MIN_PHOTOS if via == 'upload' else 0}

def add_photo(con, token, p, source='upload'):
    """現況照片：只接受有效 JPEG／PNG，每張 ≤ 2 MB，每張報價最多 20 張。"""
    q = _q(con, token)
    if q['status'] != 'draft' or q['stage'] not in ('photos', 'review', 'site_visit'):
        raise ValueError('這張報價已經處理完，不能再加照片')
    if _photo_count(con, q['id']) >= MAX_PHOTOS:
        raise ValueError(f'照片最多 {MAX_PHOTOS} 張')
    try:
        raw = p if isinstance(p, bytes) else base64.b64decode(str(p.get('content', '')).split(',', 1)[-1], validate=True)
        if not raw or len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        from PIL import Image
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as im:
                if im.format not in ('PNG', 'JPEG') or im.width * im.height > 30_000_000:
                    raise ValueError()
                mime = 'image/png' if im.format == 'PNG' else 'image/jpeg'; im.verify()
    except Exception:
        raise ValueError('照片格式不支援，請改用一般相片（JPG／PNG）') from None
    pid = c.uid('qp')
    con.execute('INSERT INTO quote_photos(id,quote_id,created_at,source,mime,bytes,data) VALUES(?,?,?,?,?,?,?)', (pid, q['id'], c.now(), source, mime, len(raw), raw))
    return {'id': pid, 'count': _photo_count(con, q['id'])}

def submit(con, token):
    """客戶按「送出」：照片夠了 → 待主管確認並通知派單管理群；選用 LINE 傳照片 → 等照片（收齊自動轉待確認）。"""
    q = _q(con, token)
    if q['stage'] != 'photos':
        return {'stage': q['stage']}
    n = _photo_count(con, q['id'])
    if q['photo_via'] == 'human':             # 客人不方便拍照 → 不擋，轉專人（管理群打電話）
        con.execute("UPDATE quotes SET stage='review' WHERE id=?", (q['id'],)); _review_now(con, q['id'])
        f_ = json.loads(q['form'] or '{}')
        import line_bot
        line_bot.admin_notify(con, '📞 客人現在不方便拍照，請專人聯絡', [('客人', f"{f_.get('name', '')}（{f_.get('phone', '')}）"), ('需求', f_.get('service_type', '') or '—'),
                              ('處理', '打電話問現場狀況，或約時間現場估價；報價單在「報價」')], f"/#q/{q['id']}", '#C47F00')
        return {'stage': 'review'}
    bb = brands.line_for(con, q['account'], json.loads(q['form']).get('line'))
    fq = json.loads(q['form'] or '{}').get('service') or ''
    need_photos = not json.loads(q['form'] or '{}').get('svc_items') and not ((bb.get('kind') == 'hourly' and not (bb.get('hourly') or {}).get('photos')) or bb.get('kind') in ('bnb', 'trash')   # 定期駐點服務：照片選填（擺放照片、須知）；垃圾清收照片選填
                       or (bb.get('kind') == 'homeline' and (fq.startswith('日常') or fq.startswith('月方案'))))                   # 生活線：日常、月方案照片選填
    if bb.get('kind') == 'trash' and q['photo_via'] == 'upload' and n < 1:      # 代丟要看一次的垃圾量
        raise ValueError('請拍一張平常一次的垃圾量（看得出幾袋、多大）')
    if need_photos and n < MIN_PHOTOS:
        if q['photo_via'] == 'upload':
            raise ValueError(f'請上傳至少 {MIN_PHOTOS} 張現況照片（每個要服務的區域都要拍）')
        if not con.execute("SELECT 1 FROM audit WHERE action='quote_waiting' AND entity_id=?", (q['id'],)).fetchone():
            c.audit(con, 'quote_waiting', 'quotes', q['id']); _notify_new(con, q, waiting=True)
        return {'stage': 'photos'}
    stage = 'site_visit' if q['mode'] == 'site_visit' else 'review'
    con.execute('UPDATE quotes SET stage=? WHERE id=?', (stage, q['id'])); _review_now(con, q['id'])
    _notify_new(con, {**q, 'stage': stage})
    if stage == 'review':
        import quickquote; quickquote.notify(con, q['id'])          # 店長在 LINE 一鍵確認報價
    return {'stage': stage}

def _notify_new(con, q, waiting=False):
    import line_bot, notify
    f = json.loads(q['form']); n = _photo_count(con, q['id'])
    who = f"{f.get('company') or f['name']}（{f['phone']}）"
    if f.get('outside'):
        title_prefix = '🌏 外縣市・'
    else:
        title_prefix = ''
    if q['mode'] == 'site_visit':
        title, color, last = '📍 新的現場估價需求', '#C47F00', ('現場估價費', f"NT$ {q['visit_fee']:,}" if q['visit_fee'] else '免費')
    elif waiting:
        title, color, last = '📷 新估價（等客戶用 LINE 傳照片）', '#8E8E93', ('報價單', q['id'])
    else:
        title, color, last = f"🧮 新報價待確認・初估 NT$ {q['total'] // 100:,}", '#0A84FF', ('報價單', q['id'])
    line_bot.admin_notify(con, title_prefix + title, [('客戶', who), ('類型', f.get('service_type', '')), ('地點', f"{f.get('area', '')} {f.get('address', '')}"[:40]),
                                       ('希望', f"{q['date']} {f.get('slot') or ''}".strip() or '—'), ('照片', f'{n} 張'), last], f"/#q/{q['id']}", color)
    notify.staff(con, title.split('・初估')[0], f"{who} {f.get('service_type', '')}", f"/#q/{q['id']}")      # 手機推播客服也會收到：不放金額

def photos_from_line(con, uid, raw):
    """客戶在官方 LINE 傳照片：附到這個人最近一張還在審的報價。'ready'＝剛湊滿照片（要回覆客戶），'added'＝已附上，None＝沒有待審報價。"""
    q = con.execute("SELECT q.* FROM quotes q JOIN inquiries i ON i.id=q.inquiry_id WHERE i.line_user_id=? AND q.status='draft' "
                    "AND q.stage IN ('photos','review','site_visit') AND q.created_at>? ORDER BY q.created_at DESC LIMIT 1",
                    (uid, (dt.datetime.now(c.TZ) - dt.timedelta(days=7)).isoformat())).fetchone()
    if not q:
        return None
    r = add_photo(con, q['token'], raw, 'line')
    if q['stage'] == 'photos' and r['count'] >= MIN_PHOTOS:
        stage = 'site_visit' if q['mode'] == 'site_visit' else 'review'
        con.execute('UPDATE quotes SET stage=? WHERE id=?', (stage, q['id'])); _review_now(con, q['id']); _notify_new(con, {**dict(q), 'stage': stage})
        if stage == 'review':
            import quickquote; quickquote.notify(con, q['id'])          # 照片在 LINE 收齊 → 店長在 LINE 一鍵確認報價
        return 'ready'
    return f"added:{r['count']}"      # 收到但還沒湊滿（或已送審後補傳）：回報張數

# ───────────── 內部：店長審核 ─────────────
def admin_list(con):
    out = []
    for q in c.rows(con, 'SELECT id,token,created_at,stage,status,mode,form,items,estimate,taxed,total,date,start,visit_fee,sent_at,reviewed_by,order_id,account,account AS acct,customer_id,inquiry_id,link_sent FROM quotes ORDER BY created_at DESC LIMIT 300'):
        f = json.loads(q.pop('form')); grand = _totals(_items(q), q['taxed'])[2] if (q['items'] or '[]') != '[]' else q['total'] // 100
        for k in ('items', 'estimate', 'taxed'): q.pop(k)
        key = 'signed' if q['status'] == 'signed' else 'cancelled' if q['status'] == 'cancelled' else q['stage']
        inq = con.execute('SELECT line_code,line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone() if q['inquiry_id'] else None
        lname = (con.execute('SELECT display_name FROM line_users WHERE user_id=? LIMIT 1', (inq['line_user_id'],)).fetchone() or [''])[0] if inq and inq['line_user_id'] else ''
        q.update(code=(inq['line_code'] if inq else '') or '', line_name=lname or f.get('line_name') or '')      # 預約編號、LINE 名稱
        q.update(brand_name=brands.name(q.pop('account', 'demoshop')), name=('🧪示範｜' if f.get('demo') else '') + str(f.get('company') or f.get('name') or ''), demo=bool(f.get('demo')), phone=f.get('phone'), service_type=f.get('service_type'), area=f.get('area'), size=f.get('size'),
                 photos=_photo_count(con, q['id']), stage_key=key, stage_name=STAGE.get(key, key), total=grand)
        if key == 'sent':
            inq = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone() if q['inquiry_id'] else None
            linked = (bool(inq and inq['line_user_id']) or _cust_line(con, {'customer_id': q['customer_id'], 'account': q['acct'] or 'demoshop'})) and _line_on(con, q['acct'])
            q['stage_name'] = _sent_label(con, q, linked) or q['stage_name']
        out.append(q)
    return out

def maybe_customers(con, q, f):
    """Lin（21稻香店家）：報價還沒對到客戶 → 同品牌裡可能是同一位的既有客戶（電話後 9 碼、名稱／LINE 名稱／別名、案場名稱有聯絡人名字）。
    只給管理層看、自己選（身分只依證據，不自動對）。回傳 [{id, name, why}]。"""
    if q['customer_id']:
        return []
    acc = q['account'] or 'demoshop'
    generic = re.compile(r'^.{1,2}(小姐|先生|太太|女士|老師|阿姨|媽媽|姐|哥)$')       # 「王小姐」這種稱呼太常見，不能當證據（電話一樣照樣比）
    keys = {str(x).strip() for x in (f.get('name'), f.get('company'), f.get('line_name'), f.get('bnb_name')) if x and str(x).strip() not in ('None', '') and len(str(x).strip()) >= 2
            and not generic.match(str(x).strip())}
    digits = re.sub(r'\D', '', str(f.get('phone') or ''))[-9:]
    out = []
    for cu in c.rows(con, f"SELECT id,name,phone,line_name FROM customers WHERE {brands.cust_sql()}=?", (acc,)):
        why = []
        if len(digits) == 9 and re.sub(r'\D', '', cu['phone'] or '')[-9:] == digits:
            why.append('電話一樣')
        names = {n for n in [cu['name'], cu['line_name']] + [r[0] for r in con.execute('SELECT alias FROM customer_aliases WHERE customer_id=?', (cu['id'],))] if n}
        for k in keys:
            if any(k == n or (len(k) >= 3 and k in n) or (len(n) >= 3 and n in k) for n in names):
                why.append(f'名稱「{k}」')
            for (sn,) in con.execute('SELECT name FROM sites WHERE customer_id=?', (cu['id'],)):
                if sn and k in sn and k not in names:
                    why.append(f'案場「{sn}」')
        if why:
            out.append({'id': cu['id'], 'name': cu['name'], 'why': '、'.join(dict.fromkeys(why))})
    return out[:5]

def scale_warn(f, q):
    """報價人力×時數 ≥ 客戶原本填的 2 倍（例：客戶填 1 人 4 小時，報價 4 人 6 小時）→ 客戶容易被金額嚇到。"""
    try:
        cp, ch, qp, qh = (float(x or 0) for x in (f.get('people'), f.get('hours'), q['people'], q['hours']))
    except (TypeError, ValueError):
        return ''
    if min(cp, ch, qp, qh) <= 0 or qp * qh < 2 * cp * ch:
        return ''
    return (f"客戶原本填 {cp:g} 人 × {ch:g} 小時，這張報價 {qp:g} 人 × {qh:g} 小時（約 {qp * qh / (cp * ch):.0f} 倍工時），客戶容易被金額嚇到："
            "建議報價單顯示估算人力、備註寫可以分次做或先做重點區域，或先在 LINE 跟客戶說明再傳。")

def admin_get(con, qid):
    q = _q(con, qid=qid); f = json.loads(q['form']); est = json.loads(q['estimate'])
    inq = con.execute('SELECT line_user_id,line_code FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone()
    photos = [r[0] for r in con.execute('SELECT id FROM quote_photos WHERE quote_id=? ORDER BY created_at', (q['id'],))]
    # 一致化：同類型、規模相近、已傳出或已簽收的報價，給店長對照
    size = pricing._num(f.get('size')); similar = []
    for r in c.rows(con, "SELECT id,form,items,status,created_at FROM quotes WHERE id!=? AND (status='signed' OR stage='sent') ORDER BY created_at DESC LIMIT 200", (q['id'],)):
        g = json.loads(r['form'])
        if g.get('service_type') == f.get('service_type') and (not size or abs(pricing._num(g.get('size')) - size) <= max(5, size * 0.3)):
            similar.append({'id': r['id'], 'size': g.get('size'), 'subtotal': _totals(_items(r), 0)[0], 'status': '已簽收' if r['status'] == 'signed' else '已報價',
                            'date': r['created_at'][:10]})
    sub, tax, total = _totals(_items(q), q['taxed'])
    return {**{k: q[k] for k in ('id', 'account', 'token', 'created_at', 'stage', 'status', 'mode', 'date', 'start', 'end', 'taxed', 'people', 'hours', 'scope', 'note',
                                 'photo_via', 'visit_fee', 'reviewed_by', 'sent_at', 'order_id')},
            'form': f, 'estimate': est, 'items': _items(q),
            'clean_level': __import__('cleanlevel').quoted(f) if __import__('cleanlevel').applies(f) else None,      # 
            'scale_levels': {k: __import__('cleanlevel').scale_level(f, k, _items(q)) for k in __import__('cleanlevel').SCALES},      # 清運／整理等級（0＝不適用）
            'subtotal': sub, 'tax': tax, 'total': total, 'photos': photos, 'similar': similar[:8],
            'preview_sig': preview_sig(con, q['token']), 'line_linked': (bool(inq and inq['line_user_id']) or _cust_line(con, q)) and _line_on(con, q['account']),
            'line_mode': _line_mode(con, q['account']), 'scopes': pricing.scopes(con, q['account']), 'stypes': STYPES, 'brand': q['account'], 'brand_name': brands.name(q['account']), 'stage_name': _sent_label(con, dict(q), bool(inq and inq['line_user_id']) or _cust_line(con, q)) or STAGE.get(q['stage'], q['stage']),
            'link_sent': q['link_sent'] if 'link_sent' in q.keys() else '', 'public_url': f"{__import__('line_bot').BASE_URL}/q/{q['token']}", 'customer_id': q['customer_id'],
            'customer_name': (con.execute('SELECT name FROM customers WHERE id=?', (q['customer_id'],)).fetchone() or [''])[0] if q['customer_id'] else '',
            'customer_match': bool(q['customer_id'] and con.execute("SELECT 1 FROM audit WHERE action='quote_customer_match' AND entity_id=?", (q['id'],)).fetchone()),
            'valid_until': q['valid_until'], 'valid_effective': valid_until(q), 'valid_days': VALID_DAYS,
            'pay_method': f.get('pay_method') or '', 'pay_kind': pay_kind(q, f), 'pay_options': [[k, v] for k, v in __import__('payments').PAY_LABEL.items()],
            'pay_terms': next((x['items'] for x in _terms(con, q, f) if '付款' in x['title']), []), 'scale_warn': scale_warn(f, q), 'maybe_customers': maybe_customers(con, q, f)}

def requote(con, token, p=None):
    """客戶按「請重新報價」（報價過期或日期已過；也有「選的服務等級比報價高」）：通知派單管理群與店長，一天只通知一次。"""
    import line_bot, notify
    q = _q(con, token)
    if q['status'] == 'signed':
        raise ValueError('這張報價已經簽收了')
    since_ = max((dt.datetime.now(c.TZ) - dt.timedelta(days=1)).isoformat(), q['sent_at'] or '')      # 重傳之後再按（又選了更高的級別）要照樣通知
    if con.execute("SELECT 1 FROM audit WHERE action='quote_requote' AND entity_id=? AND at>?", (q['id'], since_)).fetchone():
        return {'ok': True, 'already': True}
    f = json.loads(q['form'])
    import cleanlevel
    why = cleanlevel.requote_reason(p or {})
    if why:                                       # 業主在報價單選的級別／髒污／頻率記下來，重新報價時看得到
        f['requote_want'] = why; con.execute('UPDATE quotes SET form=? WHERE id=?', (json.dumps(f, ensure_ascii=False), q['id']))
    c.audit(con, 'quote_requote', 'quotes', q['id'], after={'why': why} if why else None)
    line_bot.admin_notify(con, '🔁 客戶請我們重新報價', [('報價單', q['id']), ('客戶', f"{f.get('company') or f.get('name')}（{f.get('phone', '')}）"), ('類型', f.get('service_type', '')),
                                                  *([('業主要的', why), ('原本報價', cleanlevel.label(cleanlevel.quoted(f)))] if why else []),
                                                  ('處理', '打開報價 → 改日期／金額 → 確認並傳給客戶')], f'/#q/{q["id"]}', '#C47F00')
    notify.staff(con, '客戶請重新報價', f"{f.get('company') or f.get('name')} {q['id']}", f'/#q/{q["id"]}')
    if q['status'] == 'cancelled':                 # 稽核：過期自動結案後客戶要重新報價 → 重新打開成待確認（以前打開是唯讀、沒人能處理）
        con.execute("UPDATE quotes SET status='draft',stage='review' WHERE id=?", (q['id'],)); _review_now(con, q['id'])
        c.audit(con, 'quote_reopen', 'quotes', q['id'], after={'by': '客戶按請重新報價'})
    elif q['stage'] == 'sent':
        con.execute("UPDATE quotes SET stage='review' WHERE id=?", (q['id'],)); _review_now(con, q['id'])      # 回到待確認清單
    return {'ok': True}

def _line_mode(con, brand):
    import line_bot
    return line_bot.mode(con, brand or 'demoshop')

def _line_on(con, brand):
    """這個品牌官方 LINE 是正式發送（test／off 時客戶收不到，岳勳 就是這樣）。"""
    return _line_mode(con, brand) == 'on'

def _cust_line(con, q):
    """報價單的客戶有沒有綁同品牌的 LINE（有的話確認後自動傳報價卡）。"""
    import line_bot
    if not q['customer_id']:
        return False
    acc, uid = line_bot.customer_target(con, q['customer_id'])
    return bool(uid) and acc == (q['account'] or 'demoshop')

def admin_save(con, p, actor):
    q = _q(con, qid=p['id'])
    if q['status'] != 'draft':
        raise ValueError('已簽收或取消的報價不能再改')
    f0 = json.loads(q['form']); f1 = dict(f0)       # 讀一次、全部改完再存一次（以前每段讀舊的，同時改服務項目和「只給總價」會互相蓋掉）
    if 'total_only' in p: f1['total_only'] = bool(p.get('total_only'))      # 只給業主看總價（負責人）
    if 'show_crew' in p: f1['hide_crew'] = not p.get('show_crew')           # 報價單標示人力與時數（客服，預設顯示）
    if 'special' in p:                       # 特別約定（店長打字）：印在合約與報價單上
        f1['special'] = str(p.get('special') or '')[:600]
    if p.get('service_type'):                # 服務項目改下拉（舊的值可以保留）
        f1['service_type'] = _stype(p['service_type'], keep=f0.get('service_type') or '')
    if 'level' in p:                         # 這份報價是服務 Level 幾（業主簽收前要選，比報價高就不能簽）
        import cleanlevel
        lv = int(p.get('level') or 0)
        if lv and lv not in cleanlevel.LEVELS:
            raise ValueError('服務等級要是 1、2、3')
        f1['level'] = lv or None
    for k_ in ('trash', 'tidy'):              # 清運等級、整理等級（0＝這份報價不適用）
        if f'{k_}_level' in p:
            v_ = int(p.get(f'{k_}_level') or 0)
            if v_ not in (0, 1, 2, 3):
                raise ValueError('等級要是 0（不適用）或 1、2、3')
            f1[f'{k_}_level'] = v_
    if 'new_ok' in p:                        # 稽核 F5：管理層確認「確定是新客戶」（不是系統提示的既有客戶）
        f1['new_ok'] = bool(p.get('new_ok'))
    if 'pay_method' in p:                    # 已跟客戶談好的付款方式（不再自動塞訂金、月訂閱）
        import payments
        pm = str(p.get('pay_method') or '')
        if pm and pm not in payments.STAFF_PAY and pm not in ('月結', '預付'):
            raise ValueError('付款方式不對')
        f1['pay_method'] = pm
    if f1 != f0:
        con.execute('UPDATE quotes SET form=? WHERE id=?', (json.dumps(f1, ensure_ascii=False), q['id']))
    if p.get('customer_id') and not q['customer_id']:          # 還沒對到客戶的報價 → 管理層選既有客戶（成立時不會另建一位）
        k0 = con.execute('SELECT * FROM customers WHERE id=?', (p['customer_id'],)).fetchone()
        if not k0:
            raise ValueError('找不到這位客戶')
        if brands.of_customer(dict(k0)) != q['account']:
            raise ValueError('這位客戶是別的品牌的，不能對到這張報價')
        con.execute('UPDATE quotes SET customer_id=? WHERE id=?', (k0['id'], q['id']))
        c.audit(con, 'quote_customer_link', 'quotes', q['id'], after={'customer_id': k0['id'], 'by': actor})
        f2 = json.loads(con.execute('SELECT form FROM quotes WHERE id=?', (q['id'],)).fetchone()[0] or '{}')     # 空白的電話、地址、公司、統編從客戶資料帶入
        s0 = con.execute("SELECT address FROM sites WHERE customer_id=? AND COALESCE(address,'')!='' ORDER BY rowid LIMIT 1", (k0['id'],)).fetchone()
        for k_, v_ in (('phone', k0['phone']), ('address', s0[0] if s0 else ''), ('name', k0['name'])):
            if v_ and not str(f2.get(k_) or '').strip():
                f2[k_] = v_
        con.execute('UPDATE quotes SET form=? WHERE id=?', (json.dumps(f2, ensure_ascii=False), q['id']))
    items = []
    for it in p.get('items') or []:
        title = str(it.get('title', '')).strip()[:60]
        if not title:
            continue
        amt = int(round(float(it.get('amount') or 0)))
        if (amt < 0 and title != '現場估價費折抵') or amt > 2_000_000:          # 折抵是唯一允許的負數
            raise ValueError('金額不合理')
        items.append({'title': title, 'sub': str(it.get('sub', '')).strip()[:60], 'amount': amt, 'unit': str(it.get('unit', '')).strip()[:30], 'per_unit': bool(it.get('per_unit')), 'separate': bool(it.get('separate')),
                      'scope': [str(s).strip()[:80] for s in (it.get('scope') or []) if str(s).strip()][:12]})
    if not items:
        raise ValueError('至少要有一個項目')
    d = p.get('date', q['date']) or ''; start = p.get('start') or q['start'] or '09:00'
    if d:
        c.date(d)
    people = max(1, min(10, int(p.get('people') or q['people'] or 1))); hours = max(0.5, min(12, float(p.get('hours') or q['hours'] or 3)))
    end = _end(start, hours); c.minutes(start, end)
    sub = _totals(items, 0)[0]
    taxed = (1 if p.get('taxed') else 0) if 'taxed' in p else q['taxed']     # 稽核：沒帶 taxed（同事開單、工具）＝不改，不要洗成未稅
    con.execute('UPDATE quotes SET items=?,note=?,taxed=?,date=?,start=?,end=?,people=?,hours=?,scope=?,total=?,reviewed_by=? WHERE id=?',
                (json.dumps(items, ensure_ascii=False), str(p['note'])[:500] if 'note' in p else (q['note'] or ''), taxed, d, start, end, people, hours,      # 稽核：沒帶備註（LINE 確認報價）＝保留
                 str(p.get('scope', ''))[:600], sub * 100, actor, q['id']))
    if 'valid_until' in p:                   # 報價期限：空白＝預設（傳出日＋5 天）；指定日期＝管理層決定（例：大客戶給到月底）
        v = str(p.get('valid_until') or '').strip()
        old = q['valid_until'] if 'valid_until' in q.keys() else ''
        if v != old:
            if v:
                c.date(v); today = dt.datetime.now(c.TZ).date()
                if v < today.isoformat():
                    raise ValueError('報價期限不能早於今天')
                if v > (today + dt.timedelta(days=90)).isoformat():
                    raise ValueError('報價期限最多 90 天內')
            con.execute('UPDATE quotes SET valid_until=? WHERE id=?', (v, q['id']))
            c.audit(con, 'quote_valid', 'quotes', q['id'], before={'valid_until': old or f'預設（傳出日＋{VALID_DAYS} 天）'},
                    after={'valid_until': v or f'預設（傳出日＋{VALID_DAYS} 天）', 'by': actor})
    money = lambda its: [(str(i.get('title') or ''), int(i.get('amount') or 0), bool(i.get('per_unit')), bool(i.get('separate')), str(i.get('unit') or '')) for i in its]
    if q['stage'] == 'sent' and (money(items) != money(_items(q)) or (d, start, taxed) != (q['date'], q['start'], q['taxed'])):
        con.execute("UPDATE quotes SET stage='review' WHERE id=?", (q['id'],)); _review_now(con, q['id'])     # 改到金額或日期：收回，主管重新確認後再傳（人力、範圍、備註直接更新，不收回）
    c.audit(con, 'quote_save', 'quotes', q['id'], after={'subtotal': sub, 'people': people, 'hours': hours})
    return admin_get(con, q['id'])

def admin_send(con, p, actor):
    """店長確認 → 報價單開放給客戶看並簽收；客戶有綁 LINE 就自動傳報價卡，沒有就給連結自己傳。"""
    out = admin_save(con, p, actor)
    if not out['date']:
        raise ValueError('請填服務日期')
    if out['date'] <= dt.datetime.now(c.TZ).date().isoformat():          # 稽核：傳出去客戶也簽不了（服務日已過）
        raise ValueError('服務日期是今天或已經過了，客戶沒辦法簽約；請改日期再傳')
    q0 = _q(con, qid=out['id'])
    if (json.loads(q0['form'] or '{}').get('service_type') or '') in ('', '我不確定', '服務'):      # 稽核：服務項目要先選（不然報價卡、工單、收款規則都會錯）
        raise ValueError('服務項目還沒選：請在中控台報價單「服務項目」下拉選一個再傳')
    if q0['valid_until'] and q0['valid_until'] < dt.datetime.now(c.TZ).date().isoformat():
        raise ValueError('這張指定的報價期限已經過了，請改報價期限，或清空（清空＝傳出日＋5 天）')
    f0_ = json.loads(q0['form'] or '{}')
    mc = maybe_customers(con, q0, f0_)
    if mc and not f0_.get('new_ok') and not p.get('new_ok') and not out.get('line_linked'):      # 稽核 F5：客戶還沒綁 LINE、又可能是既有客戶 → 先對到（不然簽約會另建一位、收不到 LINE）
        raise ValueError('這張報價可能是既有客戶：' + '；'.join(f"{x['name']}（{x['why']}）" for x in mc)
                         + '。請在中控台報價按「就是他」；確定是新客戶，請在報價上勾「確定是新客戶」再傳。')
    if q0['account'] != 'demoshop' and not company(con, q0['account']).get('bank'):
        raise ValueError(f"{brands.name(q0['account'])}還沒設定公司與匯款資料（品牌設定 › 報價單抬頭），設定好才能傳報價單")
    import decisions; decisions.visit_fee_on_send(con, q0)          # 現場估價費：成交折抵（店長定的規則）
    con.execute("UPDATE quotes SET stage='sent',sent_at=? WHERE id=?", (c.now(), out['id']))
    ix.set_setting(con, f"qreview_at:{out['id']}", ''); ix.set_setting(con, f"qrv1:{out['id']}", '')      # 之後收回待確認 → 重新計時
    q = _q(con, qid=out['id'])
    import line_bot
    sent = line_bot.on_quote_sent(con, q)
    c.audit(con, 'quote_sent', 'quotes', q['id'], after={'by': actor, 'line': sent})
    return {'url': f"{line_bot.BASE_URL}/q/{q['token']}", 'line_sent': sent, 'total': out['total']}

def admin_stage(con, p):
    q = _q(con, qid=p['id'])
    if q['status'] != 'draft':
        raise ValueError('已簽收或取消的報價不能再改')
    if p.get('stage') == 'cancelled':
        con.execute("UPDATE quotes SET status='cancelled',stage='cancelled' WHERE id=?", (q['id'],))
        import line_bot                    # 取消自動通知客戶「已經幫您取消」
        knew = bool(q['inquiry_id'] or q['sent_at'])           # 客戶自己申請過、或收過這張報價才通知（同事開了還沒傳的，客戶不知道）
        sent = line_bot.on_quote_cancelled(con, _q(con, qid=q['id'])) if knew else False
        if knew and not sent:
            f0 = json.loads(q['form'] or '{}')
            line_bot.admin_notify(con, '📞 報價已取消，客戶收不到 LINE 通知', [('報價', f"{q['id']} {f0.get('name') or ''}"), ('電話', f0.get('phone') or '—'),
                                  ('處理', '客戶沒綁 LINE：請打電話或用其他方式告知已取消')], f"/#q/{q['id']}", '#C47F00')
        c.audit(con, 'quote_stage', 'quotes', q['id'], after={**p, 'notified': sent})
        return {'saved': True, 'notified': sent, 'knew': knew}
    elif p.get('stage') in ('review', 'site_visit'):
        fee = q['visit_fee']
        if p['stage'] == 'site_visit' and not fee:
            cfg = pricing.config(con, q['account']); st = json.loads(q['form']).get('service_type')
            fee = cfg.get('community_visit_fee', 0) if st == '大型案場服務' else cfg.get('site_visit_fee', 500)
        con.execute('UPDATE quotes SET stage=?,mode=?,visit_fee=? WHERE id=?', (p['stage'], 'site_visit' if p['stage'] == 'site_visit' else 'quote', fee or 0, q['id']))
        if p['stage'] == 'review':
            _review_now(con, q['id'])
        if p['stage'] == 'site_visit':
            import decisions; decisions.visit_fee_on_stage(con, _q(con, qid=q['id']))
    else:
        raise ValueError('狀態不符')
    c.audit(con, 'quote_stage', 'quotes', q['id'], after=p)
    return {'saved': True}

def admin_more(con, p, actor):
    """店長：照片不夠或看不清楚 → 請客戶補拍（退回「等照片」），用 LINE 告訴客戶要補什麼。"""
    q = _q(con, qid=p['id'])
    if q['status'] != 'draft':
        raise ValueError('已簽收或取消的報價不能再改')
    why = str(p.get('reason', '')).strip()[:200]
    if not why:
        raise ValueError('請寫要客戶補什麼')
    con.execute("UPDATE quotes SET stage='photos',note=CASE WHEN note='' THEN ? ELSE note END WHERE id=?", ('', q['id']))
    ix.set_setting(con, f"qmore:{q['id']}", json.dumps({'at': c.now(), 'why': why}, ensure_ascii=False))      # 稽核：追蹤從補照片這天重算、提醒寫要補什麼
    ix.set_setting(con, f"qreview_at:{q['id']}", ''); ix.set_setting(con, f"qrv1:{q['id']}", '')
    import line_bot
    sent = line_bot.on_quote_more(con, q, why)
    c.audit(con, 'quote_more', 'quotes', q['id'], after={'by': actor, 'reason': why, 'line': sent})
    return {'line_sent': sent, 'url': f"{line_bot.BASE_URL}/q/{q['token']}"}

def _cust_pay(con, cid):
    """客戶資料的結帳方式（空白＝預設先結：依金額）；報價單上管理層指定的優先。"""
    r = con.execute('SELECT pay_method FROM customers WHERE id=?', (cid or '',)).fetchone()
    v = (r[0] if r else '') or ''
    return v if v in __import__('payments').STAFF_PAY else ''

def stamp(con, brand='demoshop'):
    return ix.setting(con, pricing.key('company_stamp', brand))

def photo(con, pid):
    r = con.execute('SELECT mime,data FROM quote_photos WHERE id=?', (pid,)).fetchone()
    if not r:
        raise ValueError('找不到照片')
    return r[0], r[1]

# ───────────── 客戶：看報價單、簽收 ─────────────
STYPES = [('一般到府服務', '一般到府服務'), ('店面／辦公室服務', '店面服務'), ('大型案場服務', '大型案場服務'), ('店家服務', '店家服務'), ('年度保養／細清', '年度保養'), ('進階專案', '進階專案'), ('垃圾清收', '垃圾清收'), ('我不確定', '我不確定')]      # 加店家服務   # 客服 服務項目下拉

def _stype(v, keep=''):
    """服務項目只收下拉選單的值（舊資料原本的值可以保留）。"""
    v = str(v or '').strip() or '我不確定'               # 沒選（工具、舊程式）＝我不確定，報價審核會提醒要選
    v = {t: k for k, t in STYPES}.get(v, v)              # 也收畫面上的名稱（例：年度保養 → 年度保養／細清）
    if v in dict(STYPES) or (keep and v == keep):
        return v
    raise ValueError('請從下拉選單選服務項目（一般到府服務、店面服務、大型案場服務、年度保養、進階專案、我不確定）')

def _review_now(con, qid):
    """報價轉成「待主管確認」的時間（稽核：追蹤從這時算，不從建立時間算）；之前追過的也重新計時。"""
    ix.set_setting(con, f'qreview_at:{qid}', c.now()); ix.set_setting(con, f'qrv1:{qid}', '')

def _last_quote_form(con, cid):
    r = con.execute("SELECT form FROM quotes WHERE customer_id=? ORDER BY created_at DESC LIMIT 1", (cid,)).fetchone()
    try:
        return json.loads(r[0] or '{}') if r else {}
    except ValueError:
        return {}

def admin_create(con, p, actor):
    """同事直接開報價單（現場估價後、老客戶、大型案場）：填客戶資料與品項，存成「待主管確認」，確認後才傳給客戶。"""
    acc = brands.pick(p.get('brand'))
    cu = dict(p.get('customer') or {})
    known = None
    if p.get('customer_id'):                 # 選了既有客戶 → 報價直接對到他；空白的資料自動帶入（例：Lin 開新的店家案場，電話不同也對得上）
        known = con.execute('SELECT * FROM customers WHERE id=?', (p['customer_id'],)).fetchone()
        if not known:
            raise ValueError('找不到這位客戶')
        if brands.of_customer(dict(known)) != acc:
            raise ValueError(f"這位客戶是{brands.name(brands.of_customer(dict(known)))}的，請把品牌選成一樣的")
        site0 = con.execute('SELECT address,area FROM sites WHERE customer_id=? ORDER BY rowid LIMIT 1', (known['id'],)).fetchone()
        cu.setdefault('name', ''); cu['name'] = cu['name'] or known['name']; cu['phone'] = cu.get('phone') or known['phone'] or ''
        cu['address'] = cu.get('address') or (site0['address'] if site0 else '')
        for k_, v_ in _last_quote_form(con, known['id']).items():        # 公司、統編沿用這位客戶上一張報價
            if k_ in ('company', 'tax_id') and v_ and not cu.get(k_):
                cu[k_] = v_
    name = str(cu.get('name') or cu.get('company') or '').strip()[:60]
    if not name:
        raise ValueError('請填客戶名稱')
    form = {'name': str(cu.get('name') or name)[:60], 'company': str(cu.get('company', ''))[:60], 'phone': str(cu.get('phone', ''))[:20],
            'address': str(cu.get('address', ''))[:200], 'tax_id': str(cu.get('tax_id', ''))[:10], 'area': str(cu.get('area', ''))[:20],
            'service_type': _stype(p.get('service_type')), 'source': '同事開單', 'pay_method': str(p.get('pay_method') or '') if str(p.get('pay_method') or '') in __import__('payments').STAFF_PAY else ''}
    qid = quote_no(con, acc); token = secrets.token_urlsafe(18)
    digits = re.sub(r'\D', '', form['phone'])
    cid = known['id'] if known else next((x['id'] for x in c.rows(con, f"SELECT id,phone FROM customers WHERE phone!='' AND {brands.cust_sql()}=?", (acc,)) if len(digits) >= 9 and re.sub(r'\D', '', x['phone'])[-9:] == digits[-9:]), None)
    con.execute('INSERT INTO quotes(id,token,created_at,account,customer_id,form,estimate,total,date,start,end,ip,ua,stage,items,taxed,people,hours,scope,photo_via,mode,visit_fee) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (qid, token, c.now(), acc, cid, json.dumps(form, ensure_ascii=False), '{}', 0, '', '09:00', '', '', '', 'drafting', '[]', 1 if form['tax_id'] or form['company'] else 0,
                 1, 4, '', 'upload', 'quote', 0))
    c.audit(con, 'quote_create', 'quotes', qid, after={'by': actor})
    out = admin_save(con, {'id': qid, **{k: p.get(k) for k in ('items', 'note', 'taxed', 'date', 'start', 'people', 'hours', 'scope') if k in p}}, actor)
    return out          # 開單先存草稿，不通知任何人；填好價格按「交給店長確認」才通知（ask_review）

def ask_review(con, qid, actor, msg=''):
    """同事開好／改好報價 → 請店長確認金額（派單管理群卡片＋桌機通知，按了直接打開這張報價）。"""
    import line_bot, notify
    q = _q(con, qid=qid); f = json.loads(q['form'])
    if q['stage'] == 'drafting':                   # 草稿 → 待主管確認（從這時開始追蹤）
        con.execute("UPDATE quotes SET stage='review' WHERE id=?", (qid,)); _review_now(con, qid)
    total = q['total'] // 100
    line_bot.admin_notify(con, f"🧾 {actor} 請店長確認報價", [('報價單', qid), ('客戶', f.get('company') or f.get('name') or '—'), ('類型', f.get('service_type', '')),
                                                    ('目前小計', f'NT$ {total:,}' if total else '還沒填金額'), ('交代', msg[:80] or '—')], f'/#q/{qid}', '#0A84FF')
    notify.staff(con, '報價請你確認', f"{actor}：{f.get('company') or f.get('name')} {qid}", f'/#q/{qid}')
    c.audit(con, 'quote_ask', 'quotes', qid, after={'by': actor, 'msg': msg[:200]})
    return {'asked': True}

def mask_money(v):
    """沒有金額權限的人（客服）看報價單：品項、範圍照給，金額全部拿掉。"""
    return {**v, 'subtotal': None, 'tax': None, 'total': None, 'visit_fee': None, 'items': [{**i, 'amount': None} for i in v.get('items') or []]}

def strip_money_text(detail):
    """需求明細（JSON 文字或 dict）拿掉含金額的欄位（初估、估價費）。"""
    try:
        d = json.loads(detail) if isinstance(detail, str) else dict(detail)
    except ValueError:
        return '\n'.join(x for x in str(detail).splitlines() if 'NT$' not in x and '初估' not in x)
    if not isinstance(d, dict):
        return detail
    d = {k: v for k, v in d.items() if '初估' not in k and '估價費' not in k and 'NT$' not in str(v)}
    return json.dumps(d, ensure_ascii=False) if isinstance(detail, str) else d

def preview_sig(con, token):
    """內部預覽網址的簽章（同事之間傳報價單草稿用，客戶拿不到）。"""
    import hashlib, hmac, worker_portal
    return hmac.new(worker_portal._key(con).encode(), ('qp:' + token).encode(), hashlib.sha256).hexdigest()[:20]

def expect_text(q, f=None):
    """客人線上估價填的期望日期／時段（寫在報價單上，簽約後工單照這個時間；以夥伴確認為準）。"""
    f = f if f is not None else json.loads(q['form'] or '{}')
    d = q['date'] or ''
    try:
        wd = '一二三四五六日'[dt.date.fromisoformat(d).weekday()]
    except ValueError:
        return ''
    slot = str(f.get('slot') or '')
    t = slot if re.search(r'\d', slot) else (f"{q['start']}–{q['end']}" if q['start'] and q['end'] else slot)
    return f"{int(d[5:7])}/{int(d[8:10])}（{wd}）{t}".strip()

def view(con, token, preview=False):
    """preview=True：內部預覽（還沒傳給客戶也看得到正式報價單的樣子）。"""
    q = _q(con, token)
    f = json.loads(q['form'])
    base = {'no': q['id'], 'status': q['status'], 'stage': q['stage'], 'mode': q['mode'], 'company': company(con, q['account']), 'brand': q['account'], 'name': f.get('name'),
            'photos': _photo_count(con, q['id']), 'min_photos': MIN_PHOTOS, 'photo_via': q['photo_via'], 'visit_fee': q['visit_fee'],
            'service_type': f.get('service_type'),
            'ask_tools': not f.get('tools') and q['status'] != 'signed' and __import__('sitetools').needs_line(con, f.get('line'), q['account'])}
    if q['status'] == 'cancelled' and q['stage'] != 'sent' and not preview:      # 稽核：已取消的舊連結不要顯示「報價確認中」
        return {**base, 'pending': True, 'cancelled': True}
    if q['status'] != 'signed' and q['stage'] != 'sent' and not preview:
        more = con.execute("SELECT after_json FROM audit WHERE action='quote_more' AND entity_id=? ORDER BY id DESC LIMIT 1", (q['id'],)).fetchone()
        return {**base, 'pending': True, 'more': json.loads(more[0]).get('reason') if more and q['stage'] == 'photos' else '', 'line': _line(con, con.execute('SELECT * FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone(), q['account'])}
    sent = dt.date.fromisoformat((q['sent_at'] or c.now())[:10])
    items = _items(q); sub, tax, total = _totals(items, q['taxed'])
    return {**base, 'pending': False, 'date': sent.isoformat(), 'valid_until': valid_until(q),
            'customer': {**{k: f.get(k, '') for k in ('name', 'company', 'phone', 'tax_id')}, 'address': full_address(f)}, 'service_date': q['date'], 'start': q['start'], 'end': q['end'], 'expect': expect_text(q, f),
            'items': [{**{k: i.get(k) for k in ('title', 'sub', 'scope', 'amount', 'unit', 'per_unit', 'separate')},
                       **({'amount': None} if f.get('total_only') and not (i.get('per_unit') or i.get('separate')) else {})} for i in items],      # 只給業主看總價
            'subtotal': sub, 'tax': tax, 'total': total, 'total_only': bool(f.get('total_only')),
            'crew': None if f.get('hide_crew') else {'people': q['people'] or 1, 'hours': q['hours'] or 0},      # 寫明幾位人力、約幾小時
            'clean': __import__('cleanlevel').view_all(con, f, q['status'] == 'signed', items),      # 服務等級＋髒污程度
            'note': q['note'], 'freq': f.get('freq'), 'signed_at': q['signed_at'], 'signer': q['signer_name'], 'stamp': stamp(con, q['account']),
            'line': _line(con, con.execute('SELECT * FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone(), q['account']) if q['status'] == 'signed' else None,
            'chg_url': __import__('reschedule').url(con, q['order_id']) if q['status'] == 'signed' and q['order_id'] else '',      # 簽收完成頁直接能申請改時間
            **_contract_view(con, q, f)}

def pay_kind(q, f):
    """這張報價的收款方式（管理層指定的付款方式優先，沒指定就依服務類型）。"""
    import payments
    return payments.kind_of(f.get('service_type', ''), f.get('freq') or '', q['account'], f.get('pay_method') or '')

def _terms(con, q, f):
    """客戶看到、簽收比對的合約：只列這張報價的付款方式。"""
    import contract
    return contract.terms(company(con, q['account']).get('name'), f.get('special', ''), q['account'], pay=pay_kind(q, f))

def admin_confirm(con, p, by):
    """已經電話／當面跟客戶談好（價格、付款方式）→ 不用客戶再線上簽收，管理層代為確認、直接成立（建工單、收款單、開始派工）。"""
    q = _q(con, qid=p['id'])
    if q['status'] == 'signed':
        return {'signed': True, 'already': True, 'order': q['order_id']}
    if q['status'] != 'draft':
        raise ValueError('這張報價已經取消了')
    if not q['date']:
        raise ValueError('請先填服務日期再成立（工單要有日期）')
    if q['date'] < dt.datetime.now(c.TZ).date().isoformat():
        raise ValueError('服務日期已經過了，請先改日期')
    if q['date'] == dt.datetime.now(c.TZ).date().isoformat() and (q['start'] or '00:00') <= dt.datetime.now(c.TZ).strftime('%H:%M'):      # 稽核 A2
        raise ValueError('開工時間已經過了，請先改日期或時間再成立（不然系統會一次問所有夥伴）')
    how_ = str(p.get('how') or '電話').strip()[:10]
    f = json.loads(q['form']); items = _items(q); sub, tax, total = _totals(items, q['taxed'])
    mc = maybe_customers(con, q, f)
    if mc and not p.get('new_ok') and not f.get('new_ok'):          # Lin：沒對到客戶就成立 → 系統另建一位、收不到 LINE
        raise ValueError('這張報價可能是既有客戶：' + '；'.join(f"{x['name']}（{x['why']}）" for x in mc)
                         + '。請先按報價上的「就是他」對到既有客戶；確定是新客戶，請勾「確定是新客戶」再成立。')
    if not items:
        raise ValueError('至少要有一個項目')
    signer = f"業主{how_}同意（{by} 代為確認）"
    con.execute("UPDATE quotes SET reviewed_by=?,sent_at=COALESCE(NULLIF(sent_at,''),?) WHERE id=?", (by, c.now(), q['id']))
    q = _q(con, qid=q['id'])
    r = _finalize(con, q, f, items, total, signer, '', f'staff:{by}', _terms(con, q, f), f"{by}（業主{how_}同意，代為確認）",
                  f"業主{how_}同意報價單 {q['id']}（{by} 代為確認）", f'🧾 報價已成立（業主{how_}同意）')
    c.audit(con, 'quote_confirm_verbal', 'quotes', q['id'], after={'by': by, 'how': how_})
    return r

def _contract_view(con, q, f):
    """合約條款（簽名前逐段勾選）；簽完：簽名、同意的條款版本、收款進度。"""
    import contract, payments
    t = _terms(con, q, f)
    import linepay
    out = {'terms': t, 'terms_version': contract.VERSION, 'terms_digest': contract.digest(t), 'special': f.get('special', ''), 'linepay': linepay.status(con, q['account'] or 'demoshop')['enabled'], 'linepay_mode': linepay.mode(con),
           'linepay_demo': linepay.demo_ok(con, q)}
    if q['status'] == 'signed':
        st = f.get('signed_terms') or {}
        out.update({'signature': q['signature'], 'signed_terms': st,
                    'payments': [{**{k: r[k] for k in ('id', 'kind', 'label', 'amount', 'due', 'status', 'note')}, 'lp': linepay.allowed(con, r)} for r in payments.rows_for(con, quote_id=q['id'])],
                    'last5': (con.execute('SELECT remit_last5 FROM customers WHERE id=?', (q['customer_id'] or '',)).fetchone() or [''])[0] or ''})      # 上次的末五碼自動帶入
    return out

def sign(con, token, p, ip):
    """簽收 → 建客戶、案場、工單（客戶已確認）→ 通知派單管理群請店長挑人派工（設定為自動時才自動派）。"""
    q = _q(con, token)
    if q['status'] == 'signed':
        return {'signed': True, 'already': True}
    if q['status'] != 'draft' or q['stage'] != 'sent':
        raise ValueError('這張報價還在主管確認中，確認後會傳給您')
    if q['date'] and q['date'] <= dt.datetime.now(c.TZ).date().isoformat():
        raise ValueError('報價單上的服務日期已經過了，請在官方 LINE 跟我們聯絡，重新約時間 🙏')
    if valid_until(q) < dt.datetime.now(c.TZ).date().isoformat():
        raise ValueError('報價單已過期，請在官方 LINE 跟我們聯絡，會重新幫您報價')
    signer = str(p.get('name', '')).strip()[:40]; sig = str(p.get('signature', ''))
    if not signer or not p.get('agree'):
        raise ValueError('請填簽收人並勾選同意')
    import contract
    f0 = json.loads(q['form']); terms = _terms(con, q, f0)
    if p.get('terms_digest') != contract.digest(terms):          # 條款有更新（或沒逐段同意）→ 請重新整理再簽
        raise ValueError('合約條款有更新，請重新整理頁面，逐段閱讀並勾選後再簽名')
    if not sig.startswith('data:image/png;base64,') or len(sig) > 300_000 or len(sig) < 800:
        raise ValueError('請在簽名框簽名')
    base64.b64decode(sig.split(',', 1)[1], validate=True)
    f = json.loads(q['form']); items = _items(q); sub, tax, total = _totals(items, q['taxed'])
    import cleanlevel
    if cleanlevel.need_any(f, items):            # 簽收前選服務等級、髒污程度、希望頻率與時長；晚上加清運／整理等級（比報價高 → 請主管重新報價）
        f.update(cleanlevel.check_all(f, p, items))
    import sitetools
    if not f.get('tools') and sitetools.needs_line(con, f.get('line'), q['account']):     # 同事代開的報價單 → 簽收時先回答工具
        if p.get('tools') not in sitetools.KINDS:
            raise ValueError('請先回答「現場工具」：服務當天需要我們自備工具嗎？')
        f['tools'] = p['tools']; f['tools_note'] = str(p.get('tools_note') or '').strip()[:200]
    return _finalize(con, q, f, items, total, signer, sig, ip, terms, f"客戶 {signer}（線上簽收）", f"客戶線上簽收報價單 {q['id']}（{signer}）", '🧾 報價單已簽收')

def _finalize(con, q, f, items, total, signer, sig, ip, terms, actor, how, title):
    """報價成立（客戶線上簽收、管理層代為確認共用）：建客戶、案場、工單（客戶已確認）→ 收款單 → 通知派單管理群、客戶付款通知。"""
    import contract, sitetools
    d9 = re.sub(r'\D', '', f['phone'])[-9:]
    inq = con.execute('SELECT * FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone()
    cu = {'id': q['customer_id']} if q['customer_id'] else None
    dup = None
    if not cu and len(d9) == 9:
        cu = next((x for x in c.rows(con, f"SELECT id,name,phone FROM customers WHERE phone!='' AND {brands.cust_sql()}=?", (q['account'],))
                   if re.sub(r'\D', '', x['phone'])[-9:] == d9), None)
        if cu and inq and inq['line_user_id'] and con.execute('SELECT 1 FROM line_users WHERE customer_id=? AND account=? AND user_id!=?',
                                                                (cu['id'], q['account'], inq['line_user_id'])).fetchone():
            dup, cu = cu, None
    if not cu and inq and inq['line_user_id']:
        r = con.execute('SELECT customer_id FROM line_users WHERE user_id=? AND account=? AND customer_id IS NOT NULL', (inq['line_user_id'], q['account'])).fetchone()
        cu = {'id': r[0]} if r else None
    c.CTX.actor = actor
    cid = cu['id'] if cu else c.save(con, 'customers', {'name': f.get('company') or f['name'], 'line_name': f.get('line_name', ''), 'phone': f['phone'],
                                                        'line_account': q['account'], 'status': 'active', 'channel': brands.channel(con, q['account']),
                                                        'billing_rule': ('需開發票；統編 ' + f['tax_id']) if f.get('tax_id') else '線上報價簽收',
                                                        'notes': f"線上報價單 {q['id']} 簽收建立"})['id']
    if dup:
        import trash
        trash.flag(con, 'customers', cid, '6. 可能重複客戶', f"線上簽收 {q['id']} 填的電話和「{dup['name']}」相同，但那位客戶已綁別的 LINE", '確認是否同一人；是的話合併')
    con.execute("UPDATE customers SET status='active',version=version+1 WHERE id=? AND status IN ('lead','ended')", (cid,))     # 簽了就是服務中客戶
    visits = pricing.FREQ.get(f.get('freq') or '單次') or int((re.search(r'(\d+)', f.get('freq') or '') or [0, 1])[1])     # 生活線「每月 12 次」也算得出每次價
    addr = re.sub(r'\s', '', full_address(f) or f.get('address', '')); street = re.sub(r'\s', '', f.get('address', ''))
    old_site = next((s for s in c.rows(con, 'SELECT id,address,entry_notes FROM sites WHERE customer_id=?', (cid,))       # 稽核：以前拿路名比含縣市的地址，永遠對不上 → 每次都多一個案場
                     if addr and (re.sub(r'\s', '', s['address'] or '') == addr or (street and re.sub(r'\s', '', s['address'] or '').endswith(street)))), None)
    if old_site and f.get('entry') and not (old_site['entry_notes'] or '').strip():
        con.execute('UPDATE sites SET entry_notes=?,version=version+1 WHERE id=?', (str(f['entry'])[:300], old_site['id']))
    per_visit = total * 100 // visits if visits > 1 else None          # 定期方案才有「每次價」；單次的金額只記在工單
    if old_site and per_visit is not None:
        con.execute('UPDATE sites SET visit_price=?,version=version+1 WHERE id=?', (per_visit, old_site['id']))
    site = old_site or c.save(con, 'sites', {'customer_id': cid, 'name': (f.get('bnb_name') or f"{f['name']} {f.get('area', '')}").strip()[:60], 'address': full_address(f), 'service_type': f.get('service_type', ''),
                                 'entry_notes': str(f.get('entry') or '')[:300],      # 企業線表單的進場方式（以前只留在報價裡）
                                 'area': f.get('area', ''), 'visit_price': per_visit, 'checklist': (q['scope'] or '')[:500],      # 每次價跟工單 approved_amount 同一個稅基
                                 'notes': '；'.join(x for x in (f"規模 {f.get('size')}" if f.get('size') else '', f"{f.get('floors')} 樓" if f.get('floors') else '',
                                                               {'yes': '有電梯', 'no': '沒電梯'}.get(str(f.get('lift') or ''), ''), '有寵物' if f.get('pets') else '', f.get('notes', '')) if x)})
    if f.get('tools') in sitetools.KINDS:            # 工具記在案場，之後同一個地方沿用
        sitetools.set_tools(con, site['id'], f['tools'], f.get('tools_note', ''), signer)
    o = c.save(con, 'orders', {'title': f.get('service_type') or '服務', 'site_id': site['id'], 'date': q['date'], 'start': q['start'], 'end': q['end'],
                               'required_workers': q['people'] or 1, 'billing_mode': 'fixed',
                               'approved_amount': total * 100 // visits if total else None,      # 含稅（有開發票時）；只有單價／另計項目 → 不寫 0，完工依實際數量核定
                               'customer_confirmed': 1, 'review_note': '示範工單' if f.get('demo') else how + ('；金額依實際數量完工後核定' if not total else '')
                                              + (f"；特別約定：{f['special']}" if f.get('special') else ''),          # 稽核：特別約定進工單（內部看，不給夥伴）
                               'scope': _worker_scope(q['scope'] or _scope_from_items(items), f),        # 客戶交代、樓層／電梯／寵物放服務範圍（審核後夥伴看得到；備註夥伴看不到）
                               'notes': chr(10).join(x for x in (f"客戶交代：{f['notes']}" if f.get('notes') else '', _extras_note(items), f"報價備註：{q['note']}" if q['note'] else '') if x)})
    f['signed_terms'] = {'version': contract.VERSION, 'digest': contract.digest(terms), 'agreed_at': c.now(), 'ip': ip}
    con.execute("UPDATE quotes SET status='signed',stage='signed',signer_name=?,signature=?,signed_at=?,customer_id=?,order_id=?,form=? WHERE id=?",
                (signer, sig, c.now(), cid, o['id'], json.dumps(f, ensure_ascii=False), q['id']))
    import payments                            # 收款單：年度保養 30% 訂金＋尾款／單次全額／月訂閱；開工前 7 天收齊
    pays = payments.create_for(con, q['id'], o['id'], cid, total * 100, f.get('service_type', ''), q['date'], dt.datetime.now(c.TZ).date().isoformat(), f.get('freq') or '',
                               brand=q['account'], pay_method=f.get('pay_method') or _cust_pay(con, cid), company=bool(f.get('company') or f.get('tax_id')))      # 公司行號一萬以下完工後付
    if inq:
        con.execute("UPDATE inquiries SET status='booked',customer_id=?,order_id=? WHERE id=?", (cid, o['id'], inq['id']))
        if inq['line_user_id'] and not cu:          # 新客戶才自動綁 LINE；電話對到舊客戶的不綁（避免冒用），由店長確認
            con.execute('UPDATE line_users SET customer_id=COALESCE(customer_id,?) WHERE user_id=? AND account=?', (cid, inq['line_user_id'], q['account']))
        elif inq['line_user_id'] and not con.execute('SELECT 1 FROM line_users WHERE user_id=? AND account=? AND customer_id IS NOT NULL', (inq['line_user_id'], q['account'])).fetchone():
            import line_bot as _lb       # 稽核：老客戶簽收 → 送管理群確認綁定（以前什麼都沒做，之後提醒、開門、完成照片都送不到）
            _lb.request_bind(con, q['account'], inq['line_user_id'], 'customer', cid, f.get('name', ''), f.get('phone', ''), f.get('line_name', ''))
    c.audit(con, 'quote_signed', 'quotes', q['id'], after={'order_id': o['id'], 'ip': ip})
    if (f.get('service_type') or '') == '垃圾代丟':           # 晚上門口收；月方案自動排 4 週、第一次接的人變固定
        import daidiu; daidiu.on_signed(con, q, f, o)
    import quotelink; quotelink.check(con, o)             # 這位業主附近日期已有排好人的工單 → 先擋派工，問管理群要不要沿用
    import referral; referral.on_signed(con, _q(con, qid=q['id']))          # 推薦成交 → 群組卡
    import scopereview; scopereview.hold(con, o['id'])        # 客人打的服務需求要管理層審核才給夥伴看
    import dispatch, line_bot, notify
    if ix.setting(con, 'dispatch_mode', 'manual') == 'auto':
        msg = dispatch.auto_dispatch(con, {'order_id': o['id']})['message']
    elif (ix.setting(con, 'auto_ask', 'on') or 'on') != 'off':
        msg = '系統 1 分鐘內自動詢問可派的夥伴（業主指定／常配合的先問）；要自己挑人請到「派工」'
    else:
        msg = '請到「派工」勾選要發給哪幾位夥伴'
    if visits > 1:
        msg += f'；每月 {visits} 次方案，這張只建了第一次，請用「固定排程」建其餘場次'
    line_bot.admin_notify(con, f"{title} NT$ {total:,}", [('客戶', f"{f.get('company') or f['name']}（{f['phone']}）"),
                                                             ('服務', f"{f.get('service_type')}・{q['date']} {q['start']}–{q['end']}"),
                                                             ('人力', f"{q['people']} 人 × 約 {q['hours']:g} 小時"), ('派工', msg),
                                                             *([('級別', __import__('cleanlevel').worker_line(f).replace(chr(10), '；') + (f"；希望 {f.get('want_freq')}" if f.get('want_freq') else '') + (f"、每次約 {f['want_hours']} 小時" if f.get('want_hours') else ''))] if __import__('cleanlevel').worker_line(f) else []),
                                                             ('收款', ('、'.join(f"{r['label']} {r['amount'] // 100}（{r['due'] or r['note']}）" for r in pays) + ('；店長確認收款後才排定' if any(r['kind'] in ('deposit', 'full', 'month') for r in pays) else '；當日結算／月結，不用等收款就能排定')) if pays else '只有單價／另計項目，完工後依實際數量開帳')],
                          '/#dispatch', '#2E7D32', oid=o['id'])
    line_bot.on_payment_notice(con, q['account'], inq['line_user_id'] if inq and inq['line_user_id'] else None, cid, q['token'], pays)
    notify.staff(con, '報價單已簽收，請派工', f"{f['name']} {q['date']} {q['start']}", '/#dispatch')
    return {'signed': True, 'order': o['id'], 'line': _line(con, inq, q['account'])}

def _line(con, inq, acc):
    """客戶還沒用 LINE 對應過 → 給「用 LINE 傳送編號」連結；已對應就不用。"""
    if not inq or inq['line_user_id'] or not inq['line_code']:
        return None
    import line_bot, urllib.request
    return {'code': inq['line_code'], 'url': f'https://line.me/R/oaMessage/{line_bot.basic_id(con, acc)}/?' + urllib.request.quote(f"預約編號 {inq['line_code']}")}

def site_visit(con, p, ip):
    """舊表單相容：需要現場估價 → 建需求、通知管理群。"""
    import dispatch, line_bot
    r = dispatch.create_inquiry(con, {**p, 'detail': {**(p.get('detail') or {}), '需求': '現場估價'}}, ip, notify_admin=False)
    con.execute("UPDATE inquiries SET kind='site_visit' WHERE id=?", (r['id'],))
    line_bot.admin_notify(con, '📍 有人要現場估價', [('客戶', f"{p.get('name')}（{p.get('phone')}）"), ('服務', p.get('service_type', '')), ('地區', p.get('area', '')),
                                                  ('希望', p.get('preferred', ''))], f"/#inq/{r['id']}", '#C47F00')
    return r

# ───────────── 夥伴報到 ─────────────
def join_info(con, t):
    b = con.execute("SELECT * FROM line_bind WHERE code=? AND kind='join' AND expires>?", ('J' + t, int(time.time()))).fetchone()
    if not b:
        raise PermissionError('報到連結已過期，請在群組重新按「報到」')
    lu = con.execute('SELECT display_name,worker_id FROM line_users WHERE account=? AND user_id=?', (b['account'], b['ref_id'])).fetchone()
    ws = []           # 稽核：不列夥伴名單（客戶點到報到也看不到誰在做）；老夥伴選「用手機號碼找我」，送出時用手機比對
    import brands, worker_portal
    return {'display_name': lu['display_name'] if lu else '', 'bound': bool(lu and lu['worker_id']), 'workers': ws, 'brand': b['account'], 'brand_name': brands.name(b['account']),
            'apply_url': worker_portal.onboard_token(con, b['account'], b['ref_id'], 'apply')}       # 新夥伴：填完整應徵表

def join(con, t, p):
    b = con.execute("SELECT * FROM line_bind WHERE code=? AND kind='join' AND expires>?", ('J' + t, int(time.time()))).fetchone()
    if not b:
        raise PermissionError('報到連結已過期，請在群組重新按「報到」')
    phone = re.sub(r'[^\d\-]', '', str(p.get('phone', '')))
    if len(re.sub(r'\D', '', phone)) < 9:
        raise ValueError('請填手機號碼')
    fields = {'regions': str(p.get('regions', ''))[:400], 'skills': str(p.get('skills', ''))[:200], 'availability': str(p.get('availability', ''))[:100]}
    c.CTX.actor = 'LINE 夥伴報到'
    if p.get('worker_id') == 'phone':                  # 老夥伴：用手機號碼找自己（只找得到電話完全一樣的那一位）
        d9 = re.sub(r'\D', '', phone)[-9:]
        hit = [w for w in c.rows(con, "SELECT id,phone FROM workers WHERE name NOT LIKE '（示範）%'") if re.sub(r'\D', '', w['phone'] or '')[-9:] == d9]
        if len(hit) != 1:
            raise ValueError('名單裡找不到這支手機（或有重複），請確認號碼；第一次來的請選「我是新夥伴」')
        p = {**p, 'worker_id': hit[0]['id']}
    if p.get('worker_id'):
        w = c.get(con, 'workers', p['worker_id'])
        if not w['phone'] or re.sub(r'\D', '', w['phone'])[-9:] != re.sub(r'\D', '', phone)[-9:]:
            n = con.execute("SELECT COUNT(*) FROM audit WHERE action='join_fail' AND entity_id=?", (b['code'],)).fetchone()[0]
            c.audit(con, 'join_fail', 'line_bind', b['code'])
            if n + 1 >= 3:
                con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],)); con.commit()
                raise PermissionError('錯誤次數太多，這個報到連結已失效，請聯絡示範服務店')
            con.commit()
            raise ValueError('手機號碼跟示範服務店的紀錄不一樣（或示範服務店還沒有你的電話），請確認或請示範服務店幫你更新')
        import line_bot
        if not con.execute('SELECT 1 FROM line_users WHERE account=? AND user_id=? AND worker_id=?', (b['account'], b['ref_id'], w['id'])).fetchone():
            lu = con.execute('SELECT display_name FROM line_users WHERE account=? AND user_id=?', (b['account'], b['ref_id'])).fetchone()
            line_bot.request_bind(con, b['account'], b['ref_id'], 'worker', w['id'], w['name'], phone, lu[0] if lu else '')
            con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
            return {'ok': True, 'name': w['name'], 'new': False, 'pending': True}      # 只憑電話不能換掉夥伴的 LINE：等管理群確認
        upd = {k: v for k, v in fields.items() if v}
        if upd:
            c.save(con, 'workers', {'id': w['id'], 'version': w['version'], **upd})
        wid = w['id']; new = False
    else:
        raise ValueError('第一次來的新夥伴，請按「填應徵表」填完整資料（示範服務店看過資料才會約面試）🙏')
        name = str(p.get('name', '')).strip()[:30]
        if not name:
            raise ValueError('請填名字')
        wid = c.save(con, 'workers', {'name': name, 'phone': phone, 'status': 'review', 'notes': 'LINE 報到新夥伴', **fields})['id']; new = True   # 新的人先給管理層看資料、面試
        import interviews; interviews.screen(con, wid, b['ref_id'], b['account'], {k: str(v)[:200] for k, v in fields.items() if v})   # 帶 LINE：邀請面試才傳得到
        import trash
        trash.flag(con, 'workers', wid, '4. 新報到的夥伴', f'{name}（{phone}）從 LINE 報到，應徵資料已送派單管理群審核（待核對，還不能派工）', '到「夥伴招募」看資料：OK 邀請面試、不適合婉拒')
    con.execute('UPDATE line_users SET worker_id=?,onboard=CASE WHEN ? THEN ? ELSE onboard END WHERE account=? AND user_id=?', (wid, 1 if new else 0, 'applicant', b['account'], b['ref_id']))
    import line_bot
    line_bot.worker_menu_link(con, b['ref_id'], b['account'])
    con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
    import notify, line_bot
    name = c.get(con, 'workers', wid)['name']
    notify.staff(con, '夥伴 LINE 報到', f"{name}{'（新夥伴，待確認）' if new else ''}", f'/#w/{wid}')
    if new:
        line_bot.admin_notify(con, '🙋 新夥伴報到', [('名字', name), ('手機', phone), ('區域', fields['regions'])], f'/#w/{wid}')
    return {'ok': True, 'name': name, 'new': new}
