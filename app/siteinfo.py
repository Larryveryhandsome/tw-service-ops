"""案場須知＋照片（／／）。

・案場多「給夥伴的現場須知」（sites.worker_info：備品櫃鑰匙放哪、注意事項…）＋須知照片（site_photos），管理層在中控台客戶資料整理（一個地點一份）。
・業主在估價單上傳的現場照片（quote_photos）＝這張工單的「業主傳的現場照片」。
・夥伴看：派工卡（接單前）只給「每次服務重點」＋業主傳的現場照片；接單／排定後（前一天提醒卡、工作頁）才給現場須知＋須知照片（鑰匙位置這類不在接單前給）。
・頁面 /si/<工單>.<等級>.<簽章>：等級 q＝只有業主照片、f＝須知＋全部照片。照片 /si/p/<q|s>/<照片id>.<簽章>。
"""
import hashlib, hmac, html as _h, re
import core as c

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS site_photos(id TEXT PRIMARY KEY, site_id TEXT, caption TEXT DEFAULT '', mime TEXT, bytes INTEGER, data BLOB, created_at TEXT, created_by TEXT DEFAULT '')")

def _sig(con, msg):
    import worker_portal
    return hmac.new(worker_portal._key(con).encode(), msg.encode(), hashlib.sha256).hexdigest()[:24]

def ensure_media(con):
    con.execute("CREATE TABLE IF NOT EXISTS order_media(id TEXT PRIMARY KEY, order_id TEXT, customer_id TEXT, mime TEXT, bytes INTEGER, data BLOB, created_at TEXT, source TEXT DEFAULT 'line')")

def media_ids(con, oid):
    """業主在 LINE 傳的現場照片／影片（接在工單上）：[(id, mime)]。"""
    ensure_media(con)
    return [(r[0], r[1]) for r in con.execute('SELECT id,mime FROM order_media WHERE order_id=? ORDER BY created_at', (oid,))]

def nearest_order(con, cid, days=14):
    import datetime as dt
    t = dt.datetime.now(c.TZ).date()
    r = con.execute("SELECT id FROM orders WHERE customer_id=? AND date BETWEEN ? AND ? AND status IN ('review','draft','confirmed') ORDER BY date,start LIMIT 1",
                    (cid, t.isoformat(), (t + dt.timedelta(days=days)).isoformat())).fetchone()
    return r[0] if r else None

def add_media(con, oid, cid, raw, mime, source='line'):
    ensure_media(con)
    mid = c.uid('omd')
    con.execute('INSERT INTO order_media(id,order_id,customer_id,mime,bytes,data,created_at,source) VALUES(?,?,?,?,?,?,?,?)', (mid, oid, cid, mime, len(raw), raw, c.now(), source))
    import board; board.log(con, oid, 'customer_media', f"業主在 LINE 傳了{'影片' if mime.startswith('video') else '照片'}")
    return mid

def quote_photo_ids(con, oid):
    return [r[0] for r in con.execute("SELECT p.id FROM quote_photos p JOIN quotes q ON q.id=p.quote_id WHERE q.order_id=? ORDER BY p.created_at", (oid,))] \
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='quote_photos'").fetchone() else []

def site_photo_ids(con, sid):
    ensure(con)
    return [(r[0], r[1]) for r in con.execute('SELECT id,caption FROM site_photos WHERE site_id=? ORDER BY created_at', (sid or '',))]

def info(con, o):
    s = con.execute('SELECT worker_info FROM sites WHERE id=?', (o['site_id'] or '',)).fetchone() if o.get('site_id') else None
    return ((s[0] if s else '') or '').strip()

def url(con, oid, level):
    import line_bot as lb
    return f"{lb.BASE_URL}/si/{oid}.{level}.{_sig(con, f'si:{oid}:{level}')}"

def has(con, o, level):
    """這張工單有沒有東西可以看（q：業主照片；f：須知或任何照片）。"""
    if level == 'q':
        return bool(quote_photo_ids(con, o['id']) or media_ids(con, o['id']))
    import siteroutine
    return bool(quote_photo_ids(con, o['id']) or media_ids(con, o['id']) or site_photo_ids(con, o.get('site_id')) or info(con, o) or siteroutine.due(con, o))

def photo_src(con, kind, pid):
    return f"/si/p/{kind}/{pid}.{_sig(con, f'sip:{kind}:{pid}')}"

def photo(con, name):
    m = re.match(r'^(q|s|m)/([\w-]+)\.([0-9a-f]{24})$', name or '')
    if not m or not hmac.compare_digest(_sig(con, f'sip:{m.group(1)}:{m.group(2)}'), m.group(3)):
        raise PermissionError('連結無效')
    ensure(con)
    ensure_media(con)
    r = con.execute({'q': 'SELECT mime,data FROM quote_photos WHERE id=?', 's': 'SELECT mime,data FROM site_photos WHERE id=?', 'm': 'SELECT mime,data FROM order_media WHERE id=?'}[m.group(1)], (m.group(2),)).fetchone()
    if not r:
        raise ValueError('找不到照片')
    return r[0], r[1]

def page(con, name):
    m = re.match(r'^([\w-]+)\.(q|f)\.([0-9a-f]{24})$', name or '')
    if not m or not hmac.compare_digest(_sig(con, f'si:{m.group(1)}:{m.group(2)}'), m.group(3)):
        raise PermissionError('連結無效')
    oid, level = m.group(1), m.group(2)
    o = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
    if not o or o['status'] in ('cancelled', 'archived'):
        raise ValueError('這筆工作已取消')
    o = dict(o)
    s = dict(con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'] or '',)).fetchone() or {})
    import board
    secs = []
    if s.get('checklist'):
        secs.append(('每次服務重點', f'<p class="t">{_h.escape(board._no_money(s["checklist"]))}</p>'))
    import siteroutine
    if siteroutine.text(con, o):                  # 定期特殊服務（這一期還沒做）
        secs.append(('這次要做的定期項目', f'<p class="t">{_h.escape(siteroutine.text(con, o))}</p>'))
    if level == 'f' and info(con, o):
        secs.append(('現場須知', f'<p class="t">{_h.escape(board._no_money(info(con, o)))}</p>'))
    if level == 'f':
        sp = site_photo_ids(con, o['site_id'])
        if sp:
            secs.append(('須知照片', '<div class="g">' + ''.join(f'<figure><a href="{photo_src(con, "s", i)}"><img loading="lazy" src="{photo_src(con, "s", i)}"></a>'
                                                                  f'<figcaption>{_h.escape(cap or "")}</figcaption></figure>' for i, cap in sp) + '</div>'))
    qp = quote_photo_ids(con, oid); md = media_ids(con, oid)
    if qp or md:
        secs.append(('業主傳的現場照片／影片', '<div class="g">' + ''.join(f'<figure><a href="{photo_src(con, "q", i)}"><img loading="lazy" src="{photo_src(con, "q", i)}"></a></figure>' for i in qp)
                     + ''.join(f'<figure><video controls preload="metadata" src="{photo_src(con, "m", i)}" style="width:100%;border-radius:10px"></video></figure>' if mm.startswith('video')
                               else f'<figure><a href="{photo_src(con, "m", i)}"><img loading="lazy" src="{photo_src(con, "m", i)}"></a></figure>' for i, mm in md) + '</div>'))
    body = ''.join(f'<section><h2>{t}</h2>{h}</section>' for t, h in secs) or '<p>這場沒有另外的須知或照片。</p>'
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>現場須知・照片</title><style>
body{{margin:0;background:#FAF9F6;color:#1D1D1F;font:16px/1.6 -apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif;padding:20px 16px 40px}}
main{{max-width:760px;margin:0 auto}}h1{{font-size:24px;margin:0 0 4px}}.sub{{color:#6E6E73;margin:0 0 18px}}h2{{font-size:17px;color:#6B5A4E;margin:22px 0 8px}}
.t{{white-space:pre-wrap;background:#fff;border:1px solid #E5E1DC;border-radius:12px;padding:12px 14px;margin:0}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}figure{{margin:0}}img{{width:100%;aspect-ratio:1;object-fit:cover;border-radius:10px;background:#E5E1DC}}
figcaption{{font-size:13px;color:#6E6E73}}</style></head><body><main><h1>現場須知・照片</h1><p class="sub">{_h.escape(s.get("service_type") or "服務")}｜{_h.escape(board.street_of(s))}{"｜接單後才看得到現場須知" if level == "q" else ""}</p>{body}</main></body></html>'''

def add_photo(con, sid, raw, mime, caption, by):
    ensure(con)
    if not con.execute('SELECT 1 FROM sites WHERE id=?', (sid,)).fetchone():
        raise ValueError('找不到地點')
    if not raw or len(raw) > 8 * 1024 * 1024 or mime not in ('image/jpeg', 'image/png', 'image/webp'):
        raise ValueError('請上傳 8MB 以內的 JPG／PNG 照片')
    pid = c.uid('sph')
    con.execute('INSERT INTO site_photos(id,site_id,caption,mime,bytes,data,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)', (pid, sid, (caption or '')[:60], mime, len(raw), raw, c.now(), by))
    c.audit(con, 'site_photo_add', 'sites', sid, after={'photo': pid, 'by': by})
    return pid

def del_photo(con, pid, by):
    ensure(con)
    r = con.execute('SELECT site_id FROM site_photos WHERE id=?', (pid,)).fetchone()
    if not r:
        raise ValueError('找不到照片')
    con.execute('DELETE FROM site_photos WHERE id=?', (pid,))
    c.audit(con, 'site_photo_del', 'sites', r[0], after={'photo': pid, 'by': by})
