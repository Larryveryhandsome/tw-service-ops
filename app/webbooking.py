"""官網（舊 Base44 站 demoshop-main）與 beauty_ip/booking_webhook.py 送來的預約 → 營運中樞「預約需求」。
雲端搬本機：取代 Google Apps Script「示範服務店網站後端 Web App」（原本寫 Google 試算表＋LINE 通知）。

- 收的欄位：Apps Script／booking_webhook 用的中文欄名（姓名、聯絡電話、需要服務地址…）或英文 key（name、phone、address…）都可以。
- 只建「預約需求」（inquiries），不建報價、不建工單、不回覆客人；店長在中控台／派單管理群照原本流程處理。
- 可重跑不重複：同一支電話＋同樣內容 30 分鐘內再送一次 → 回傳同一筆（官網重試、按兩次都不會多一筆）。
- 跨網域：只有 settings `web_booking_origins`（逗號分隔，預設見 DEFAULT_ORIGINS）列的網址可以從瀏覽器送；本機程式（127.0.0.1）不看來源。
"""
import datetime as dt
import hashlib
import json
import re
import core as c

DEFAULT_ORIGINS = 'https://www.example.com'

ALIASES = {     # 中文欄名（含 Google 表單原欄名去空白後）→ 內部 key
    '姓名': 'name', 'name': 'name', 'client_name': 'name',
    '聯絡電話': 'phone', '電話': 'phone', 'phone': 'phone',
    '電子郵件地址': 'email', '電子郵件': 'email', 'email': 'email', 'Email': 'email',
    '需要服務地址': 'address', '服務地址': 'address', '地址': 'address', 'address': 'address',
    '服務地區': 'area', '地區': 'area', 'area': 'area', 'service_area': 'area',
    '空間型態': 'space', 'housing_type': 'space', 'space': 'space',
    '需求服務規模': 'size', '規模': 'size', 'square_footage': 'size', 'size': 'size',
    '是否有寵物？': 'pets', '是否有寵物': 'pets', '寵物': 'pets', 'pets': 'pets',
    '目前狀態': 'condition', '狀態': 'condition',
    '想要的時長×次數/訂閱制': 'plan', '想要的時長': 'plan', '時長次數': 'plan', '時長': 'plan', '方案': 'plan', 'plan': 'plan',
    '現場工具': 'tools', 'tools': 'tools',
    '您想申請的服務類型': 'service', '服務類型': 'service', 'service_type': 'service', 'service': 'service',
    '加強服務': 'focus',
    '特殊需求/備注': 'notes', '特殊需求': 'notes', '備注': 'notes', '備註': 'notes', 'notes': 'notes',
    '預計開始日期': 'date', '開始日期': 'date', 'scheduled_date': 'date', 'date': 'date',
    '偏好時段': 'slot', '時段': 'slot', 'time_slot': 'slot', 'slot': 'slot',
    '偏好的星期（可複選）': 'pref_days', '偏好的星期': 'pref_days', '星期': 'pref_days',
    '您是從哪裡知道示範服務店？': 'heard', '得知來源': 'heard', '來源': 'heard',
    '若為親友推薦，請留推薦人': 'referrer', '推薦人': 'referrer', '親友推薦': 'referrer',
    '現場照片上傳(非必填，亦可傳送': 'photo', '現場照片': 'photo', '照片': 'photo',
    '我已閱讀並同意以下條款': 'agree', '同意條款': 'agree', '條款': 'agree',
    'website': 'website', 'source': 'source', 'src': 'source',
}
LABELS = [('plan', '方案／時長'), ('space', '空間'), ('size', '規模'), ('pets', '寵物'), ('condition', '目前狀態'), ('tools', '現場工具'),
          ('focus', '加強服務'), ('pref_days', '偏好星期'), ('heard', '得知來源'), ('referrer', '推薦人'), ('email', 'Email'), ('photo', '照片')]

def _key(k):
    k0 = re.sub(r'\s', '', str(k))
    return ALIASES.get(str(k).strip()) or ALIASES.get(k0) or ''

def normalize(p):
    out = {}
    for k, v in (p or {}).items():
        key = _key(k)
        if not key or v in (None, ''):
            continue
        if isinstance(v, (list, tuple)):
            v = '、'.join(str(x) for x in v)
        elif isinstance(v, dict):
            v = json.dumps(v, ensure_ascii=False)
        out.setdefault(key, str(v).strip()[:1000])
    return out

def _service(f):
    import dispatch
    t = (f.get('service', '') + ' ' + f.get('plan', '') + ' ' + f.get('space', ''))
    for kw, st in (('店家', '店家服務'), ('社區', '大型案場服務'), ('店', '店面／辦公室服務'), ('辦公', '店面／辦公室服務'), ('搬', '搬遷前後服務'),
                   ('細清', '年度保養／細清'), ('年度保養', '年度保養／細清'), ('施工', '年度保養／細清'), ('毛坯', '年度保養／細清'), ('居家', '一般到府服務'),
                   ('日常', '一般到府服務'), ('定期', '一般到府服務'), ('訂閱', '一般到府服務'), ('單次', '一般到府服務')):
        if kw in t:
            return st
    return f.get('service') if f.get('service') in dispatch.SERVICE_TYPES else '其他'

def origins(con):
    import integrations as ix
    return {x.strip().rstrip('/') for x in (ix.setting(con, 'web_booking_origins', DEFAULT_ORIGINS) or '').split(',') if x.strip()}

def ingest(con, p, ip):
    """回傳 {ok, id, line, duplicate}；欄位不足丟 ValueError（官網會顯示錯誤，請客人改用官方 LINE）。"""
    import dispatch
    f = normalize(p)
    if f.get('website'):
        raise ValueError('送出失敗')
    src = re.sub(r'[^\w\-.:/一-鿿]', '', f.get('source') or '官網')[:30] or '官網'
    detail = {lab: f[k][:300] for k, lab in LABELS if f.get(k)}
    detail['來源'] = src
    sig = hashlib.sha256(json.dumps([re.sub(r'\D', '', f.get('phone', '')), f.get('address', ''), f.get('date', ''), f.get('notes', ''), detail],
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    detail['送單碼'] = sig
    since = (dt.datetime.now(c.TZ) - dt.timedelta(minutes=30)).isoformat(timespec='seconds')
    hit = con.execute("SELECT id FROM inquiries WHERE created_at>? AND detail LIKE ?", (since, f'%"送單碼": "{sig}"%')).fetchone()
    if hit:
        return {'ok': True, 'id': hit[0], 'duplicate': True}
    pref = ' '.join(x for x in (f.get('date', ''), f.get('slot', '')) if x)
    r = dispatch.create_inquiry(con, {'account': 'demoshop', 'name': f.get('name', ''), 'phone': f.get('phone', ''), 'area': f.get('area', ''),
                                      'address': f.get('address', ''), 'service_type': _service(f), 'detail': detail, 'preferred': pref,
                                      'notes': f.get('notes', ''), 'line_name': ''}, ip)
    c.audit(con, 'web_booking', 'inquiries', r['id'], after={'source': src})
    return {**r, 'duplicate': False}
