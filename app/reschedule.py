"""業主自己申請改時間（臨時改期、改時間或要調整，請業主自己上去申請、送回來給我們確認；不要跟業主說「審核」）。

- 業主：LINE「改期・取消」／確認卡「需要改時間」→ 簽章網址 /chg?o=<工單>&s=<簽章>（手機、電腦都能開）→ 選新日期、開始時間（時數照原本）、要調整的事 → 送出。
  送出後 LINE 收到「已收到您的改時間申請」圖卡；確認之前原本的預約仍有效。
- 管理群：一張卡（原本 → 想改成、離服務日、改期規則、已排夥伴新時段有沒有空）→「✅ 改到新時間」或「這時間不行（打字回業主）」。
  中控台「管理群待處理」也能按（同一個後端）。
- 改到新時間：工單改日期時間 → 原本的改期流程通知業主（預約時間已更新）、夥伴（收到／新時間不行）、邀請中的重發；
  業主自己選的時間＝已確認，人員也審核過的直接排定。
- 這時間不行：管理層打的話＋「再選一個時間」按鈕傳給業主（圖卡）。
"""
import datetime as dt
import hashlib
import hmac
import core as c

TIMES = [f'{h:02d}:{m:02d}' for h in range(7, 20) for m in (0, 30)]

def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS change_requests(id TEXT PRIMARY KEY, order_id TEXT, customer_id TEXT, old_date TEXT, old_start TEXT, old_end TEXT,
        new_date TEXT, new_start TEXT, new_end TEXT, note TEXT DEFAULT '', status TEXT DEFAULT 'pending', created_at TEXT, decided_by TEXT DEFAULT '',
        decided_at TEXT DEFAULT '', reply TEXT DEFAULT '')""")

def sig(con, oid):
    import worker_portal
    return hmac.new(worker_portal._key(con).encode(), ('chg:' + (oid or '')).encode(), hashlib.sha256).hexdigest()[:20]

def url(con, oid):
    import line_bot as lb
    return f'{lb.BASE_URL}/chg?o={oid}&s={sig(con, oid)}'

def days_before(o):
    return (dt.date.fromisoformat(o['date']) - dt.datetime.now(c.TZ).date()).days if o['date'] else 99

def rule(days):
    return ('離服務日還有 7 天以上：可以免費改期一次。' if days >= 7 else
            '離服務日 4～7 天：改期送出後，我們會跟您確認訂金怎麼處理。' if days >= 4 else
            '離服務日 3 天內：我們會盡量幫您協調服務人員的時間。')

def _order(con, oid, s):
    if not hmac.compare_digest(str(s or ''), sig(con, oid)):
        raise PermissionError('連結不正確，請從 LINE 重新打開')
    o = con.execute('SELECT * FROM orders WHERE id=?', (oid or '',)).fetchone()
    if not o or o['status'] not in ('review', 'draft', 'confirmed') or (o['date'] or '') < dt.datetime.now(c.TZ).date().isoformat():
        raise ValueError('這筆預約已經不能線上改了，請直接在 LINE 告訴我們 🙏')
    return dict(o)

def pending_of(con, oid):
    ensure(con)
    r = con.execute("SELECT * FROM change_requests WHERE order_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (oid,)).fetchone()
    return dict(r) if r else None

def view(con, oid, s):
    """業主看的頁面資料（不含價格、夥伴姓名）。"""
    import line_bot as lb, brands
    o = _order(con, oid, s)
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    b = lb.brand_of(cu)
    p = pending_of(con, oid)
    today = dt.datetime.now(c.TZ).date()
    return {'brand': b, 'brand_name': brands.name(b), 'when': lb.when(o), 'date': o['date'], 'start': o['start'], 'end': o['end'],
            'minutes': c.minutes(o['start'], o['end']) if o['start'] and o['end'] else 0, 'rule': rule(days_before(o)),
            'min_date': (today + dt.timedelta(days=1)).isoformat(), 'max_date': (today + dt.timedelta(days=120)).isoformat(), 'times': fit_times(o),
            'pending': {'date': p['new_date'], 'start': p['new_start'], 'end': p['new_end'], 'note': p['note']} if p else None}

LAST_END = 22 * 60

def fit_times(o):
    """時數做得完的開始時間（稽核：以前選太晚會被截到 22:00，時數、夥伴收入都變少）。"""
    m = c.minutes(o['start'], o['end']) if o['start'] and o['end'] else 120
    return [t for t in TIMES if int(t[:2]) * 60 + int(t[3:]) + m <= LAST_END]

def _end(start, minutes):
    h, m = map(int, start.split(':'))
    t = h * 60 + m + max(30, minutes or 120)
    return f'{min(t, 22 * 60) // 60:02d}:{min(t, 22 * 60) % 60:02d}'

def submit(con, oid, s, p):
    """業主送出改時間申請。回傳 {'message'}。"""
    import line_bot as lb, board
    ensure(con)
    o = _order(con, oid, s)
    nd, ns, note = str(p.get('date') or ''), str(p.get('start') or ''), str(p.get('note') or '').strip()[:300]
    try:
        d = dt.date.fromisoformat(nd)
    except ValueError:
        raise ValueError('請選新的日期')
    today = dt.datetime.now(c.TZ).date()
    if d <= today or d > today + dt.timedelta(days=120):
        raise ValueError('新的日期請選明天以後、4 個月內（今天要改請直接在 LINE 告訴我們）')
    if ns not in TIMES:
        raise ValueError('請選開始時間')
    if ns not in fit_times(o):
        raise ValueError('這個開始時間做不完（時數跟原本一樣），請選早一點的時間')
    p0 = pending_of(con, oid)
    if p0 and (p0['new_date'], p0['new_start'], p0['note']) == (nd, ns, note):
        return {'ok': True, 'message': '已經收到這個時間了 ✅ 我們確認後會在 LINE 通知您。'}       # 稽核：重複送出不再推播
    if con.execute("SELECT COUNT(*) FROM change_requests WHERE order_id=? AND created_at>=?", (oid, today.isoformat())).fetchone()[0] >= 3:
        raise ValueError('今天已經送了 3 次，請直接在 LINE 告訴我們想改的時間 🙏')
    ne = _end(ns, c.minutes(o['start'], o['end']) if o['start'] and o['end'] else 120)
    if nd == o['date'] and ns == o['start'] and not note:
        raise ValueError('跟原本的時間一樣，不用改喔')
    con.execute("UPDATE change_requests SET status='replaced' WHERE order_id=? AND status='pending'", (oid,))
    rid = c.uid('chg')
    con.execute('INSERT INTO change_requests(id,order_id,customer_id,old_date,old_start,old_end,new_date,new_start,new_end,note,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (rid, oid, o['customer_id'], o['date'], o['start'], o['end'], nd, ns, ne, note, 'pending', c.now()))
    what = f'申請改到 {lb.md(nd)} {ns}–{ne}'
    board.log(con, oid, 'customer_change', what)
    c.audit(con, 'customer_change_request', 'orders', oid, after={'request': what, 'note': note, 'days_before': days_before(o)}, actor='業主（線上）')
    r = dict(con.execute('SELECT * FROM change_requests WHERE id=?', (rid,)).fetchone())
    m = card_admin(con, r, o)
    lb.to_managers(con, 'resch', m[0]['altText'], m, oid, tag='📅 業主自己申請改時間，看一下夥伴新時段有沒有空再按 👇')
    acc, uid = lb.customer_target(con, o['customer_id'])
    if uid:
        mc = card_received(con, r, o)
        lb.queue(con, acc, uid, 'resch_got', mc[0]['altText'], mc, oid)
    return {'ok': True, 'message': '已收到您的申請 ✅ 我們確認服務人員的時間後，會在 LINE 通知您（確認之前原本的預約仍有效）。'}

def crew_check(con, o, nd, ns, ne):
    """已排的夥伴新時段有沒有空：[(姓名, 說明)]。"""
    import dispatch
    fake = {**o, 'date': nd, 'start': ns, 'end': ne}
    out = []
    for w in c.rows(con, "SELECT w.id,w.name,w.availability FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (o['id'],)):
        clash = dispatch._clash(con, w['id'], fake)
        fit, why_ = dispatch.weekly_fit(w['availability'] or '', nd, ns, ne)        # 也看平常可約時間
        av = c.rows(con, 'SELECT * FROM availability WHERE worker_id=? AND date=?', (w['id'], nd))
        busy = any(a['kind'] == 'busy' and (not a['start'] or (a['start'] < ne and a['end'] > ns)) for a in av)
        free = any(a['kind'] == 'free' and (not a['start'] or (a['start'] <= ns and a['end'] >= ne)) for a in av)
        out.append((w['name'], f'⚠️ 那時段有別場（{clash}）' if clash else '⚠️ 回報那天不能排' if busy else '✅ 回報可以' if free else f'⚠️ {why_}' if fit is False else '沒有撞期（沒填可排時間）'))
    return out

def card_admin(con, r, o):
    import line_bot as lb
    days = days_before(o)
    rows = [lb._row('原本', f"{lb.md(r['old_date'])} {r['old_start']}–{r['old_end']}"), lb._row('想改成', f"{lb.md(r['new_date'])} {r['new_start']}–{r['new_end']}"),
            lb._row('離服務日', f'{days} 天'), lb._row('規則', '7 天前免費改期；4～7 天訂金由店長確認；3 天內盡量協調')]
    crew = crew_check(con, o, r['new_date'], r['new_start'], r['new_end'])
    rows += [lb._row(n, t) for n, t in crew] or [lb._row('夥伴', '還沒有人接，改了之後照新時間找人')]
    if r['note']:
        rows.append(lb._row('業主說', r['note'][:200]))
    return lb.flex(f"📅 業主申請改時間：{lb.md(r['new_date'])} {r['new_start']}", lb._bubble(lb._header('📅 業主申請改時間', lb.when(o), '#C47F00'),
        [lb._row(k, v) for k, v in lb.order_people(con, o['id'])][:2] + rows,
        [lb._btn('✅ 改到新時間', data=f"a=rs_ok&r={r['id']}", primary=True), lb._btn('改時間＋緊急加派一位', data=f"a=rs_ok&r={r['id']}&add=1"),
         lb._btn('這時間不行（打字回業主）', data=f"a=rs_no&r={r['id']}"),
         lb._btn('打開工單', uri=f"{lb.BASE_URL}/#o/{o['id']}")],
        '按「改到新時間」：工單改好、業主和夥伴都會收到新時間（夥伴不行會按「新時間不行」）。'))

def card_offer(con, o, acc=None):
    """給業主：改期／取消（圖卡）。改時間 → 線上選日期時段（手機、電腦都能開）；取消 → 原本的「確定申請取消」。"""
    import line_bot as lb
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    p = pending_of(con, o['id'])
    rows = [lb._row('目前預約', lb.when(o)), lb._t(rule(days_before(o)), wrap=True, size='sm', margin='md')]
    if p:
        rows.append(lb._row('已送出', f"想改到 {lb.md(p['new_date'])} {p['new_start']}（確認中）"))
    return lb.flex('改期或取消', lb._bubble(lb._header('改期／取消', lb.when(o), '#C47F00', brand=acc or lb.brand_of(cu)), rows,
                   [lb._btn('申請改時間（選日期、時段）', uri=url(con, o['id']), primary=True), lb._btn('申請取消', data=f"a=c_cancel&o={o['id']}")],
                   '改時間送出後，我們確認服務人員的時間會在這裡通知您；確認之前原本的預約仍有效。'))

def card_received(con, r, o):
    """給業主：已收到申請（圖卡，不提審核）。"""
    import line_bot as lb
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    rows = [lb._row('原本', f"{lb.md(r['old_date'])} {r['old_start']}–{r['old_end']}"), lb._row('想改成', f"{lb.md(r['new_date'])} {r['new_start']}–{r['new_end']}")]
    if r['note']:
        rows.append(lb._row('您的備註', r['note'][:120]))
    return lb.flex('已收到您的改時間申請', lb._bubble(lb._header('已收到您的改時間申請 📅', f"{lb.md(r['new_date'])} {r['new_start']}", '#0A84FF', brand=lb.brand_of(cu)), rows,
                   [lb._btn('改成別的時間', uri=url(con, o['id']))], '我們確認服務人員的時間後，會在這裡通知您；確認之前原本的預約仍有效。'))

def _get(con, rid):
    ensure(con)
    r = con.execute('SELECT * FROM change_requests WHERE id=?', (rid or '',)).fetchone()
    if not r:
        raise ValueError('找不到這筆改時間申請')
    return dict(r)

ST_TEXT = {'approved': '已改到新時間', 'declined': '已回覆業主這時間不行', 'replaced': '業主後來又改了一次，請看最新那張卡', 'stale': '申請已作廢'}

def _stale(con, r, o):
    """申請送出後情況變了（稽核）：回傳原因；還能用回傳 ''。"""
    import line_bot as lb
    now = dt.datetime.now(c.TZ)
    if o['status'] not in ('review', 'draft', 'confirmed'):
        return '這張工單已經完工或取消了'
    if (o['date'], o['start']) != (r['old_date'], r['old_start']):
        return f"工單時間後來改過了（現在是 {lb.when(o)}），請業主重新選"
    if r['new_date'] < now.date().isoformat() or (r['new_date'] == now.date().isoformat() and r['new_start'] <= now.strftime('%H:%M')):
        return '業主選的新時間已經過了，請業主重新選'
    if con.execute("SELECT 1 FROM checkins k JOIN assignments a ON a.id=k.assignment_id WHERE a.order_id=? AND COALESCE(k.arrived_at,'')!=''", (o['id'],)).fetchone():
        return '夥伴已經到場打卡了'
    return ''

def _done(r):
    if r['status'] == 'replaced':
        return ST_TEXT['replaced']
    return f"這筆已經處理過了：{ST_TEXT.get(r['status'], r['status'])}" + (f"（{r['decided_by']}）" if r['decided_by'] else '') \
        + (f"：{r['reply']}" if r['status'] == 'stale' and r['reply'] else '')

def approve(con, rid, by):
    """管理層：改到新時間（LINE、中控台共用）。"""
    import alerts, line_bot as lb
    r = _get(con, rid)
    if r['status'] != 'pending':
        return _done(r)
    before = c.get(con, 'orders', r['order_id'])
    why = _stale(con, r, before)
    if why:                                      # 稽核：不照舊申請改（以前會改到過去的日期、蓋掉電話協調好的時間）
        con.execute("UPDATE change_requests SET status='stale',decided_by=?,decided_at=?,reply=? WHERE id=?", (by, c.now(), why, rid))
        return f'這張申請不能用了：{why}'
    hard = [n for n, t in crew_check(con, before, r['new_date'], r['new_start'], r['new_end']) if t.startswith(('⚠️ 那時段有別場', '⚠️ 回報那天不能排'))]
    back = before['status'] == 'confirmed' and bool(hard)      # 稽核：已排定的單、已排的人新時段有別場 → 退回待確認（不維持已排定、不重複排同一個人）
    con.execute('UPDATE orders SET date=?,start=?,end=?,version=version+1' + (",status='review',confirmed_at=''" if back else '') + ' WHERE id=?',
                (r['new_date'], r['new_start'], r['new_end'], r['order_id']))
    after = c.get(con, 'orders', r['order_id'])
    c.CTX.actor = by
    alerts.change(con, before, after, by)          # 原本的改期流程：業主「預約時間已更新」、夥伴「收到／新時間不行」、邀請中重發、收款期限跟著移
    con.execute('UPDATE orders SET customer_confirmed=1,version=version+1 WHERE id=?', (r['order_id'],))      # 業主自己選的時間＝已確認
    note = ''
    o2 = c.get(con, 'orders', r['order_id'])
    if o2['status'] in ('review', 'draft') and o2['crew_ok']:
        note = lb._auto_confirm(con, r['order_id'])
    con.execute("UPDATE change_requests SET status='approved',decided_by=?,decided_at=? WHERE id=?", (by, c.now(), rid))
    import board
    board.log(con, r['order_id'], 'customer_change_ok', f"{r['new_date']} {r['new_start']}（{by}）")
    crew = crew_check(con, o2, r['new_date'], r['new_start'], r['new_end'])
    warn = [n for n, t in crew if t.startswith('⚠️')]
    if back:
        note = f"已退回待確認：{'、'.join(hard)} 新時段沒辦法，請換人後再排定"
    return (f"✅ 已改到 {lb.md(r['new_date'])} {r['new_start']}–{r['new_end']}，業主和夥伴都收到新時間了" + (f'（{note}）' if note else '')
            + (f"\n⚠️ {'、'.join(warn)} 新時段可能不行，等他回覆；按「新時間不行」會再通知你們" if warn else ''))

def reject(con, rid, by, text):
    """管理層：這時間不行 → 把打的話＋「再選一個時間」傳給業主（圖卡）。"""
    import line_bot as lb, board
    r = _get(con, rid)
    if r['status'] != 'pending':
        return _done(r)
    o0 = c.get(con, 'orders', r['order_id'])
    if o0['status'] not in ('review', 'draft', 'confirmed') or (o0['date'], o0['start']) != (r['old_date'], r['old_start']):
        con.execute("UPDATE change_requests SET status='stale',decided_by=?,decided_at=? WHERE id=?", (by, c.now(), rid))
        return '這張申請已經不能用了（工單後來改過或取消了），不用回覆業主'
    text = (text or '').strip()[:300]
    if not text:
        return '請打一句要跟業主說的話（例如哪些時段可以）'
    o = c.get(con, 'orders', r['order_id'])
    con.execute("UPDATE change_requests SET status='declined',decided_by=?,decided_at=?,reply=? WHERE id=?", (by, c.now(), text, rid))
    board.log(con, r['order_id'], 'customer_change_no', f'{text[:60]}（{by}）')
    acc, uid = lb.customer_target(con, o['customer_id'])
    if not uid:
        return '已記錄，但業主沒綁 LINE（或官方 LINE 在測試），請打電話告訴業主'
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {})
    m = lb.flex('想改的時間要再調整一下', lb._bubble(lb._header('想改的時間要再調整一下 🙏', f"{lb.md(r['new_date'])} {r['new_start']}", '#C47F00', brand=lb.brand_of(cu)),
                [lb._row('原本', f"{lb.when(o)}（仍有效）"), lb._t(text, wrap=True, margin='md')],
                [lb._btn('再選一個時間', uri=url(con, o['id']), primary=True)], '沒有要改的話不用做任何事，原本的預約照常。'))
    lb.queue(con, acc, uid, 'resch_no', m[0]['altText'], m, o['id'])
    return '已把你打的話傳給業主（附「再選一個時間」）'

def urgent_tick(con):
    """稽核 優化：業主想改到明天、還沒人處理 → 派單管理群提醒一次（不等 10:30）。"""
    import line_bot as lb, integrations as ix
    ensure(con)
    tmr = (dt.datetime.now(c.TZ).date() + dt.timedelta(days=1)).isoformat(); n = 0
    for r in pending_items(con):
        if r['new_date'] <= tmr and not ix.setting(con, f"rs_urgent:{r['id']}"):
            ix.set_setting(con, f"rs_urgent:{r['id']}", c.now())
            o = c.get(con, 'orders', r['order_id'])
            lb.admin_notify(con, '⏰ 業主想改到明天，還沒處理', [('工作', lb.when(o)), ('想改成', f"{lb.md(r['new_date'])} {r['new_start']}–{r['new_end']}"),
                            ('處理', '看群組那張卡或中控台「管理群待處理」：改到新時間／這時間不行')], '/#asks', '#C62828', oid=o['id'])
            n += 1
    return n

def pending_items(con):
    """還能處理的申請（稽核：工單取消、時間後來改過、新時間已過的不列；按了會作廢）。"""
    ensure(con)
    return [r for r in c.rows(con, "SELECT r.*,o.title FROM change_requests r JOIN orders o ON o.id=r.order_id WHERE r.status='pending' ORDER BY r.created_at")
            if not _stale(con, r, c.get(con, 'orders', r['order_id']))]
