"""服務前確認一次問（開門方式、丟垃圾方式、工具都要在確認工單後同步問）。

以前分三次：工具在派工前問、開門在業主確認時間後／前一天問、垃圾沒問。現在第一個要問的時間點（派工前問工具、業主確認時間、前一天提醒）
就用同一張卡（左右滑）把「還不知道的」一起問；答過的同一個地點之後沿用，不再問。
"""
import core as c
import integrations as ix

TRASH = {'pack': '打包好放門口，我們自己丟', 'bin': '幫忙拿到社區／大樓垃圾集中處', 'none': '沒有要處理的垃圾'}

def trash_text(s):
    v = (s or {}).get('trash_mode') or ''
    return TRASH.get(v, v)

def old_customer(con, cid):
    """舊客戶（以前做過）→ 工具、垃圾不問業主（問舊業主很不專業），改問管理層。"""
    import datetime as dt
    return bool(cid) and bool(con.execute("SELECT 1 FROM orders WHERE customer_id=? AND status IN ('completed','archived') AND date<?",
                                          (cid, dt.datetime.now(c.TZ).date().isoformat())).fetchone())

def staff_due(con, days=14):
    """管理層要補的：舊客戶 days 天內有工作的案場，還不知道工具或垃圾怎麼處理。[(案場, 客戶名, 最近一場, 缺什麼)]"""
    import datetime as dt, sitetools
    t = dt.datetime.now(c.TZ).date()
    out, seen = [], set()
    for o in c.rows(con, "SELECT o.*,cu.name cname FROM orders o JOIN customers cu ON cu.id=o.customer_id WHERE o.date BETWEEN ? AND ? "
                         "AND o.status IN ('review','draft','confirmed') AND COALESCE(o.review_note,'')!='示範工單' ORDER BY o.date,o.start",
                    (t.isoformat(), (t + dt.timedelta(days=days)).isoformat())):
        if o['site_id'] in seen or not old_customer(con, o['customer_id']):
            continue
        s = sitetools.site_of(con, o)
        if not s:
            continue
        miss = (['tools'] if not s.get('tools') and sitetools.order_needs(con, o) else []) + (['trash'] if _trash_needed(o, s) and sitetools.order_needs(con, o) else [])
        if miss:
            seen.add(o['site_id']); out.append((s, o['cname'], o, miss))
    return out

def _recent(con, key, days=7):
    import datetime as dt
    v = ix.setting(con, key) or ''
    return bool(v) and v[:19] >= (dt.datetime.now(c.TZ) - dt.timedelta(days=days)).isoformat()[:19]

def _trash_needed(o, s):
    t = str(o.get('title') or '')
    return bool(s) and not (s.get('trash_mode') or '').strip() and not t.startswith(('垃圾代丟', '垃圾清收'))

def trash_bubble(con, o, s):
    import line_bot as lb
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o.get('customer_id') else {}
    return lb._bubble(lb._header('服務完的垃圾怎麼處理？🗑️', lb.when(o), brand=lb.brand_of(cu)),
        [lb._t('服務人員打包好之後，請問要怎麼處理？點一個就好；之後同一個地方會沿用，有變動再跟我們說。', wrap=True)],
        [lb._btn(TRASH['pack'], data=f"a=gb&s={s['id']}&v=pack", primary=True), lb._btn(TRASH['bin'], data=f"a=gb&s={s['id']}&v=bin"),
         lb._btn(TRASH['none'], data=f"a=gb&s={s['id']}&v=none")])

def message(con, oid):
    """還不知道的項目組成一則左右滑的卡：([訊息], [項目])。沒有要問的回 ([], [])。"""
    import door, sitetools
    o = c.get(con, 'orders', oid); s = sitetools.site_of(con, o)
    parts, bubbles = [], []
    if door.need_ask(con, oid):
        bubbles.append(door.card_ask(con, oid, mark=False)[0]['contents']); parts.append('entry')
    old = old_customer(con, o['customer_id'])          # 舊客戶的工具、垃圾問管理層
    if s and not old and not s.get('tools') and sitetools.order_needs(con, o) and not _recent(con, f"tools_ask:{s['id']}"):
        bubbles.append(sitetools.tools_bubble(con, o, s)); parts.append('tools')
    if not old and _trash_needed(o, s) and sitetools.order_needs(con, o) and not _recent(con, f"trash_ask:{s['id']}"):      # 每日檢查：問過 7 天內不重問；時數制服務不問垃圾
        bubbles.append(trash_bubble(con, o, s)); parts.append('trash')
    if not bubbles:
        return [], []
    alt = '服務前想先確認：' + '、'.join({'entry': '怎麼進門', 'tools': '現場工具', 'trash': '垃圾怎麼處理'}[p] for p in parts) + ' 🙏'
    msg = [{'type': 'flex', 'altText': alt, 'contents': bubbles[0] if len(bubbles) == 1 else {'type': 'carousel', 'contents': bubbles}}]
    return msg, parts

def mark(con, oid, parts):
    import sitetools
    o = c.get(con, 'orders', oid); s = sitetools.site_of(con, o)
    if s and 'tools' in parts:
        ix.set_setting(con, f"tools_ask:{s['id']}", c.now())
    if s and 'trash' in parts:
        ix.set_setting(con, f"trash_ask:{s['id']}", c.now())
    if 'entry' in parts and o['customer_id']:
        import line_bot as lb                          # 每日檢查：真的送出才記「問過開門」（以前組卡就記，代丟擋掉沒送也算問過）
        cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {})
        if cu and lb.mode(con, lb.brand_of(cu)) == 'on':
            ix.set_setting(con, f"ent_asked:{cu['id']}", c.now())

def send(con, oid, acc, uid, kind='prep_ask'):
    """推播一次（還不知道的一起問）。回傳問了幾項（0＝沒有要問或沒送出）。"""
    import line_bot as lb
    msg, parts = message(con, oid)
    if not msg or lb.queue(con, acc, uid, kind, msg[0]['altText'], msg, oid) != 'queued':
        return 0
    mark(con, oid, parts)
    return len(parts)

def answer_trash(con, acc, uid, lu, sid, v):
    """業主點「垃圾怎麼處理」→ 記在案場（之後沿用；夥伴的工作卡、工作頁看得到）。"""
    import line_bot as lb, board
    s = con.execute('SELECT * FROM sites WHERE id=?', (sid or '',)).fetchone()
    if not s or v not in TRASH:
        return '收到，專人會跟您確認 🙏'
    if not (lu or {}).get('customer_id') or (lu or {}).get('customer_id') != s['customer_id']:
        if uid not in lb.test_users(con):
            return '收到，專人會跟您確認 🙏'
    con.execute('UPDATE sites SET trash_mode=?,version=version+1 WHERE id=?', (v, sid))
    c.audit(con, 'site_trash_mode', 'sites', sid, after={'trash_mode': v, 'by': '業主（LINE）'})
    return f'好的 👍 服務完的垃圾：{TRASH[v]}。之後同一個地方都照這樣，有變動直接在這裡跟我們說。'
