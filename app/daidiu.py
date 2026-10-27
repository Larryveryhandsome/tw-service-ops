"""垃圾代丟（方案、價格：店長 ；運作方式：負責人 ）。

方案（每月以四週計）：輕生活 每週 1 次（每月 4 次）990／服務會員 880、好日子 每週 2 次（每月 8 次）1,780／1,580、
無憂生活 每週 3 次（每月 12 次）2,580／2,280、單次代丟 300／250。服務會員＝有訂閱定期服務的業主（電話對到、有月訂閱或固定排程）。
・業主選固定星期幾，晚上 8 點前把垃圾（正常家庭的量）放門口，夥伴當晚收走、交給配合廠商。給業主的文字不寫「清運」。
・簽約 → 自動排好接下來 4 週，每天往後補到 4 週；業主取消（報價作廢）、月費逾期 10 天沒付 → 停，管理群收到通知。
・夥伴：第一次照分級派；接的人變固定，之後每次直接排給他（他請假那次才另外找人；被暫停、停派就重新找）；每次 100 元固定。
・業主：每次收完只傳一則「已收走」；預約已安排、明天見、到場、完成照片、打星星都不傳（line_bot.queue 擋掉）。
"""
import datetime as dt
import json
import re
import core as c
import integrations as ix

NAME = '垃圾代丟'
PLANS = [('輕生活', 1, 990, 880), ('好日子', 2, 1780, 1580), ('無憂生活', 3, 2580, 2280), ('單次代丟', 0, 300, 250)]   # (名稱, 每週幾次, 一般價, 服務會員價)
START, END = '20:00', '21:00'
WD = '一二三四五六日'
NO = '廚餘、大型家具家電、大型廢棄物、危險物品'
CUSTOMER_OK = ('daidiu', 'change', 'cancel', 'pay', 'resch', 'quote', 'bill', 'invoice', 'sign', 'contract')      # 代丟工單給業主的推播只放行這些開頭的
AHEAD = 28

def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS daidiu_subs(id TEXT PRIMARY KEY, quote_id TEXT, customer_id TEXT, site_id TEXT, plan TEXT, weekdays TEXT,
        per_visit INTEGER, worker_id TEXT DEFAULT '', status TEXT DEFAULT 'active', created_at TEXT, stopped_at TEXT DEFAULT '', stop_reason TEXT DEFAULT '')""")
    cols = {r[1] for r in con.execute('PRAGMA table_info(daidiu_subs)')}
    for col in ('pickup', 'first_order'):              # 每日檢查：收取時間、這個訂閱自己的第一張單（續約／先單次後月方案不會抓錯）
        if col not in cols:
            con.execute(f"ALTER TABLE daidiu_subs ADD COLUMN {col} TEXT DEFAULT ''")

def is_daidiu(o):
    try:
        return str(o['title'] or '').startswith(NAME)
    except (KeyError, IndexError, TypeError):
        return False

def plan_of(name):
    return next((p for p in PLANS if p[0] == str(name or '').split('（')[0]), None)

def label(p):
    return f'{p[0]}（每週 {p[1]} 次・每月 {p[1] * 4} 次）' if p[1] else p[0]

def plans_opt():
    """預約頁用：[{name,label,per_week,price,member}]。"""
    return [{'name': p[0], 'label': label(p), 'per_week': p[1], 'price': p[2], 'member': p[3]} for p in PLANS]

def pay(con):
    """夥伴每次酬勞（分）：店長 定 100 元。"""
    try:
        return int(json.loads(ix.setting(con, 'daidiu_known') or '{}').get('worker_pay') or 100) * 100
    except (ValueError, TypeError):
        return 10000

def wd_text(wds):
    return '、'.join('週' + WD[int(w)] for w in sorted({int(x) for x in wds if str(x).isdigit() and 0 <= int(x) <= 6}))

def parse_wds(v):
    if isinstance(v, str):
        v = re.findall(r'\d', v)
    return sorted({int(x) for x in (v or []) if str(x).isdigit() and 0 <= int(x) <= 6})

def is_member(con, phone='', cid=None):
    """服務會員（有訂閱定期服務）：電話對到的業主有月訂閱（簽了定期方案）或接下來有固定排程的服務。"""
    ensure(con)
    digits = re.sub(r'\D', '', phone or '')[-9:]
    ids = {cid} if cid else set()
    if len(digits) >= 9:
        ids |= {r[0] for r in con.execute('SELECT id,phone FROM customers WHERE phone!=\'\'') if re.sub(r'\D', '', r[1] or '')[-9:] == digits}
    for x in ids - {None, ''}:
        if con.execute("SELECT 1 FROM payment_requests WHERE customer_id=? AND kind='month' AND status!='cancelled' AND created_at>=? AND COALESCE(quote_id,'') NOT IN (SELECT quote_id FROM daidiu_subs)",
                       (x, (dt.datetime.now(c.TZ) - dt.timedelta(days=62)).isoformat())).fetchone() if con.execute("SELECT 1 FROM sqlite_master WHERE name='payment_requests'").fetchone() else None:
            return True
        if con.execute("SELECT COUNT(*) FROM orders WHERE customer_id=? AND date>=? AND status IN ('review','draft','confirmed') AND COALESCE(review_note,'') LIKE '固定排程%' AND title NOT LIKE ?",
                       (x, dt.datetime.now(c.TZ).date().isoformat(), NAME + '%')).fetchone()[0] >= 2:
            return True
    return False

def estimate(con, cfg, f):
    """預約頁初估＝方案價（會員價看電話）。月方案收款走月訂閱（每月 5 號前付當月）。"""
    p = plan_of(f.get('service') or f.get('plan')) or PLANS[0]
    member = is_member(con, f.get('phone', ''))
    price = p[3] if member else p[2]
    out = {'mode': 'auto', 'service_type': NAME, 'items': [{'label': f'{NAME}・{label(p)}' + ('（服務會員價）' if member else ''), 'amount': price}],
           'total': price, 'visits': 1, 'hours': 1, 'people': 1, 'low_value': True, 'visit_fee': 0, 'member': member, 'plan': p[0],
           'note': ('每月以四週計，於每月 5 號前付當月費用。' if p[1] else '單次代丟。') + f'晚上 8 點前把垃圾放門口，當晚收走（正常家庭的量；不收{NO}）。'}
    if p[1]:
        out['freq'] = f'每月 {p[1] * 4} 次'
    return out

def check(f):
    """送出時檢查：月方案要選對天數。"""
    p = plan_of(f.get('service') or f.get('plan'))
    if not p:
        raise ValueError('請選方案')
    wds = parse_wds(f.get('weekdays'))
    if p[1] and len(wds) != p[1]:
        raise ValueError(f'{p[0]}要選每週 {p[1]} 天收（現在選了 {len(wds)} 天）')
    return p, wds

TRUCK = ('前一天收', '隔天收', '都可以')

def extra(f):
    """代丟報價單一次問清楚（每次幾袋、幾點收、休息日、沒垃圾車那天怎麼辦）。"""
    pk = str(f.get('pickup') or START)
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', pk):
        raise ValueError('請選幾點來收')
    rest = parse_wds(f.get('rest_days'))
    if set(rest) & set(parse_wds(f.get('weekdays'))):
        raise ValueError('休息日跟要收的日子重複了，請再確認')
    bags = str(f.get('bags') or '1')
    return {'pickup': pk, 'rest_days': rest, 'truck_alt': f.get('truck_alt') if f.get('truck_alt') in TRUCK else '都可以',
            'bags': bags if bags in ('1', '2', '3', '4') else '1'}

def detail(f):
    x = extra(f)
    return {'服務地區': f.get('area') or '—', '每次垃圾量': ('4 袋以上' if x['bags'] == '4' else f"約 {x['bags']} 袋") + '（每袋約 100–120 公升）',
            '收取時間': '晚上 8 點後' if x['pickup'] == START else f"約 {x['pickup']}", '休息日（不收）': wd_text(x['rest_days']) or '無',
            '沒垃圾車那天': x['truck_alt'], '收的日子': wd_text(parse_wds(f.get('weekdays'))) or '單次'}

def price_line():
    return '・'.join(f"{p[0]}{'（每週 ' + str(p[1]) + ' 次）' if p[1] else ''} {p[2]:,}" for p in PLANS) + '（月費；有訂閱定期服務的會員另有優惠）'

def scope_text(f):
    p = plan_of(f.get('service') or f.get('plan'))
    wds = parse_wds(f.get('weekdays'))
    when = f'每{wd_text(wds)}' if wds and p and p[1] else '約好的那天'
    try:
        x = extra(f)
    except ValueError:
        x = {'pickup': START, 'rest_days': [], 'truck_alt': '', 'bags': ''}
    t = f'{when}晚上 8 點前把垃圾放門口，當晚收走' if x['pickup'] == START else f"{when}約 {x['pickup']} 來收（請在這之前放門口）"
    more = [f"每次約 {'4 袋以上' if x['bags'] == '4' else x['bags'] + ' 袋'}" if x['bags'] else '', f"休息日不收：{wd_text(x['rest_days'])}" if x['rest_days'] else '',
            f"剛好沒有垃圾車的日子：{x['truck_alt']}" if x['truck_alt'] else '']
    return '\n'.join([t, f'正常家庭的量（不收{NO}）'] + [m for m in more if m])

def first_date(d, wds):
    """從 d 開始第一個收的日子（d 本身是星期幾之一就是 d）。"""
    if not d or not wds:
        return d
    day = dt.date.fromisoformat(d)
    for i in range(7):
        x = day + dt.timedelta(days=i)
        if x.weekday() in wds:
            return x.isoformat()
    return d

DISPATCH_NOTE = '垃圾代丟：晚上 8 點後到門口收（正常家庭的量），收走交給配合廠商；收完按「離場打卡」，系統會跟業主說已收走'

def _hours(f):
    """業主指定幾點收 → 工單從那時開始一小時（沒指定＝晚上 8–9 點）。"""
    pk = str(f.get('pickup') or START)
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', pk) or pk == START:
        return START, END
    h, m = map(int, pk.split(':'))
    return pk, f'{min(h + 1, 23):02d}:{m if h < 23 else 59:02d}'

def note(pk):
    """派工注意事項：沒指定＝晚上 8 點後；有指定＝約幾點（每一張自動排的單、派工卡都用這個）。"""
    return DISPATCH_NOTE if not pk or pk == START else DISPATCH_NOTE.replace('晚上 8 點後', f'約 {pk}')

def time_text(pk):
    return '晚上 8 點後' if not pk or pk == START else f'約 {pk}'

def on_signed(con, q, f, o):
    """報價成立（線上簽收或管理層代為確認）：第一次的工單改成晚上門口收；月方案建訂閱並排好接下來 4 週。"""
    ensure(con)
    try:
        qs = q['start'] or ''
    except (KeyError, IndexError, TypeError):
        qs = ''
    pk = _hours({'pickup': qs if re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', qs) else f.get('pickup')})[0]      # 每日檢查：管理層在報價改過時間 → 以報價單為準
    con.execute("UPDATE orders SET title=?,start=?,end=?,dispatch_note=?,version=version+1 WHERE id=?", (f"{NAME}・{(plan_of(f.get('service')) or PLANS[0])[0]}", *_hours({'pickup': pk}), note(pk), o['id']))
    p = plan_of(f.get('service') or f.get('plan'))
    wds = parse_wds(f.get('weekdays'))
    if not p or not p[1] or not wds:
        return None
    total = int(q['total'] or 0)
    sid = c.uid('dd')
    con.execute('INSERT INTO daidiu_subs(id,quote_id,customer_id,site_id,plan,weekdays,per_visit,created_at,pickup,first_order) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (sid, q['id'], o['customer_id'], o['site_id'], p[0], ','.join(map(str, wds)), total // (p[1] * 4), c.now(), pk, o['id']))
    con.execute('UPDATE orders SET approved_amount=? WHERE id=?', (total // (p[1] * 4), o['id']))
    extend(con, sub(con, sid))
    return sid

def sub(con, sid):
    ensure(con)
    r = con.execute('SELECT * FROM daidiu_subs WHERE id=?', (sid,)).fetchone()
    return dict(r) if r else None

def orders_of(con, s, frm=None):
    return c.rows(con, "SELECT * FROM orders WHERE site_id=? AND title LIKE ? AND date>=? AND status NOT IN ('cancelled','archived') ORDER BY date",
                  (s['site_id'], NAME + '%', frm or s['created_at'][:10]))

def _worker_ok(con, wid):
    import automation
    w = con.execute('SELECT status FROM workers WHERE id=?', (wid or '',)).fetchone()
    return bool(w and w[0] == 'active' and not automation.is_blacklisted(con, wid))

HOLD = '垃圾代丟：等第一次的人接，之後固定排給他'

def _free(con, wid, oid):
    """每日檢查：固定夥伴那晚有別場、或回報那天不能排 → 那一次走一般派工。"""
    import dispatch
    o = c.get(con, 'orders', oid)
    if dispatch._clash(con, wid, o):
        return False
    return not any(a['kind'] == 'busy' and (not a['start'] or (a['start'] < o['end'] and a['end'] > o['start']))
                   for a in c.rows(con, 'SELECT * FROM availability WHERE worker_id=? AND date=?', (wid, o['date'])))

def assign(con, s, oid):
    """排給固定夥伴：直接排入、排定（不發排定卡；前一天照常提醒他）。"""
    if not s.get('worker_id') or not _worker_ok(con, s['worker_id']):
        return False
    if con.execute("SELECT 1 FROM assignments WHERE order_id=? AND status='accepted'", (oid,)).fetchone():
        return False
    old = con.execute('SELECT id,status FROM assignments WHERE order_id=? AND worker_id=?', (oid, s['worker_id'])).fetchone()
    if (old and old['status'] in ('declined',)) or not _free(con, s['worker_id'], oid):
        ix.set_setting(con, f'dispatch_hold:{oid}', '') if (ix.setting(con, f'dispatch_hold:{oid}') or '') == HOLD else None
        return False                                   # 他這次請假／不行／那晚有別場 → 照一般找人
    if old:
        con.execute("UPDATE assignments SET status='accepted',version=version+1 WHERE id=?", (old['id'],))
    else:
        con.execute("INSERT INTO assignments(id,order_id,worker_id,status,pay_mode,rate,notes) VALUES(?,?,?,?,?,?,?)",
                    (c.uid('ass'), oid, s['worker_id'], 'accepted', 'hourly', None, '垃圾代丟固定夥伴'))
    con.execute("UPDATE assignments SET status='cancelled',notes=COALESCE(notes,'')||'（已排固定夥伴）' WHERE order_id=? AND status IN ('invited','applied')", (oid,))
    con.execute("UPDATE orders SET status='confirmed',confirmed_at=?,crew_ok='垃圾代丟固定夥伴',dispatch='filled',customer_confirmed=1,version=version+1 WHERE id=? AND status IN ('review','draft')",
                (c.now(), oid))
    if (ix.setting(con, f'dispatch_hold:{oid}') or '') == HOLD:
        ix.set_setting(con, f'dispatch_hold:{oid}', '')
    return True

def extend(con, s, today=None):
    """往後補到 4 週：每個收的日子一張工單（晚上 8–9 點）；有固定夥伴就直接排給他。回傳新排幾張。"""
    today = today or dt.datetime.now(c.TZ).date()
    wds = parse_wds(s['weekdays']); n = 0
    r0 = [s['first_order']] if s.get('first_order') and con.execute('SELECT 1 FROM orders WHERE id=?', (s['first_order'],)).fetchone() else \
        con.execute("SELECT id FROM orders WHERE site_id=? AND title LIKE ? AND status!='cancelled' AND date>=? ORDER BY date LIMIT 1",
                    (s['site_id'], NAME + '%', (s.get('created_at') or '')[:10])).fetchone()      # 每日檢查：以這個訂閱自己的第一張為準（先單次後月方案、停了再簽不會抓到別張）
    if not r0:
        return 0                                       # 每日檢查：找不到第一張（地點被合併等）不要整個背景報錯
    first = c.get(con, 'orders', r0[0])
    last = ix.setting(con, f"dd_last:{s['id']}") or (con.execute("SELECT MAX(date) FROM orders WHERE site_id=? AND title LIKE ? AND status!='cancelled' AND date>=?",
                                                               (s['site_id'], NAME + '%', first['date'])).fetchone()[0] or '')
    pk = s.get('pickup') or ''
    st, en = _hours({'pickup': pk}) if pk else (first['start'] or START, first['end'] or END)
    for i in range(1, AHEAD + 1):
        day = today + dt.timedelta(days=i)
        if day.weekday() not in wds or day.isoformat() <= last or day.isoformat() < first['date']:
            continue                                   # 每日檢查：只往後補「沒排過的日子」；取消、改期掉的那天不會被補回來
        o = c.save(con, 'orders', {'title': first['title'], 'site_id': s['site_id'], 'date': day.isoformat(), 'start': st, 'end': en, 'required_workers': 1,
                                   'billing_mode': 'fixed', 'approved_amount': s['per_visit'], 'customer_confirmed': 1, 'dispatch_note': note(pk or st),
                                   'scope': first['scope'] or '', 'review_note': f"垃圾代丟月方案自動排（{s['plan']}）"})
        ix.set_setting(con, f"dd_last:{s['id']}", day.isoformat())
        if not assign(con, s, o['id']) and not s.get('worker_id') and day > today + dt.timedelta(days=2):
            ix.set_setting(con, f"dispatch_hold:{o['id']}", HOLD)      # 還沒有固定夥伴：先只派第一張，後面的等第一次的人接了直接排給他
        n += 1
    return n

def stop(con, sid, by, reason):
    """停掉：之後還沒做的代丟取消（不通知業主；固定夥伴收到一則「不用再去」），管理群通知。"""
    import line_bot as lb, board
    s = sub(con, sid)
    if not s or s['status'] != 'active':
        return '這筆垃圾代丟已經停了'
    con.execute("UPDATE daidiu_subs SET status='stopped',stopped_at=?,stop_reason=? WHERE id=?", (c.now(), f'{reason}（{by}）'[:200], sid))
    tmr = (dt.datetime.now(c.TZ).date() + dt.timedelta(days=1)).isoformat(); n = 0
    for o in orders_of(con, s, tmr):
        if o['status'] in ('review', 'draft', 'confirmed'):
            con.execute("UPDATE orders SET status='cancelled',review_note=COALESCE(review_note,'')||?,version=version+1 WHERE id=?", (f'｜垃圾代丟停止：{reason}', o['id']))
            board.log(con, o['id'], 'daidiu_stop', reason); n += 1
    cu = c.get(con, 'customers', s['customer_id']) if s['customer_id'] else {}
    if s['worker_id']:
        lb.to_worker(con, s['worker_id'], 'cancel', '垃圾代丟停了', lb.card_text('垃圾代丟停了', f"{(cu or {}).get('name') or '這位業主'}的垃圾代丟停了，之後不用再去收，謝謝你 🙏", '#C47F00'))
    lb.admin_notify(con, f"🗑️ 垃圾代丟停了：{(cu or {}).get('name') or ''}", [('原因', reason), ('取消', f'之後 {n} 次'), ('誰', by)], f"/#c/{s['customer_id']}", '#C47F00')
    c.audit(con, 'daidiu_stop', 'daidiu_subs', sid, after={'reason': reason, 'by': by, 'cancelled': n})
    return f'已停止，之後 {n} 次取消了'

def tick(con, now=None):
    """背景：①第一次有人接 → 變固定夥伴、之後的都排給他 ②報價作廢／月費逾期 10 天 → 停 ③每天往後補到 4 週。"""
    import line_bot as lb
    ensure(con)
    now = now or dt.datetime.now(c.TZ); today = now.date(); n = 0
    for s in c.rows(con, "SELECT * FROM daidiu_subs WHERE status='active'"):
        try:
            con.execute('SAVEPOINT dd')
            n += _tick_one(con, s, now, today, lb)
            con.execute('RELEASE dd')
        except Exception as e:                          # 每日檢查：一筆壞掉不影響其他業主
            con.execute('ROLLBACK TO dd'); con.execute('RELEASE dd')
            import sys; print('daidiu tick', s['id'], repr(e), file=sys.stderr)
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'dispatch_hold:%' AND value=?", (HOLD,)).fetchall():
        o = con.execute('SELECT date FROM orders WHERE id=?', (k[14:],)).fetchone()
        if not o or o[0] <= (today + dt.timedelta(days=2)).isoformat():
            ix.set_setting(con, k, '')                  # 第一次還沒人接、這次快到了 → 放開照一般找人
    return n

def _tick_one(con, s, now, today, lb):
    n = 0
    if True:
        if not s['worker_id'] or not _worker_ok(con, s['worker_id']):
            r = con.execute("SELECT a.worker_id FROM assignments a JOIN orders o ON o.id=a.order_id WHERE o.site_id=? AND o.title LIKE ? AND a.status='accepted' "
                            "AND o.status!='cancelled' AND o.date>=? AND COALESCE(a.notes,'') NOT LIKE '%垃圾代丟固定夥伴%' ORDER BY o.date ASC LIMIT 1",
                            (s['site_id'], NAME + '%', (s.get('created_at') or '')[:10])).fetchone()      # 每日檢查：舊約的夥伴不會變成新約的固定夥伴
            if r and r[0] != s['worker_id'] and _worker_ok(con, r[0]):
                con.execute('UPDATE daidiu_subs SET worker_id=? WHERE id=?', (r[0], s['id'])); s = sub(con, s['id'])
                k = sum(assign(con, s, o['id']) for o in orders_of(con, s, (today + dt.timedelta(days=1)).isoformat()))
                site = con.execute('SELECT address,area FROM sites WHERE id=?', (s['site_id'],)).fetchone()
                lb.to_worker(con, r[0], 'daidiu_fixed', '垃圾代丟固定排給你', lb.flex('垃圾代丟固定排給你', lb._bubble(lb._header('🗑️ 垃圾代丟・固定排給你', f"每{wd_text(parse_wds(s['weekdays']))}", '#2E7D32'),
                    [lb._row('地址', (site[0] if site else '') or '請看工作頁'), lb._row('時間', f"{time_text(s.get('pickup'))}到門口收（正常家庭的量）"), lb._row('酬勞', f'{pay(con) // 100} 元／次（固定）'),
                     lb._row('已排', f'接下來 {k} 次，已放進你的班表')], [], '收完按「離場打卡」，系統會跟業主說已收走。哪一次不行就按「臨時請假」，那次會另外找人。')))
                n += 1
        q = con.execute('SELECT status FROM quotes WHERE id=?', (s['quote_id'],)).fetchone()
        if q and q[0] == 'cancelled':
            stop(con, s['id'], '系統', '報價作廢（業主取消）'); return n + 1
        late = con.execute("SELECT label FROM payment_requests WHERE quote_id=? AND kind='month' AND status NOT IN ('confirmed','cancelled') AND due!='' AND due<?",
                           (s['quote_id'], (today - dt.timedelta(days=10)).isoformat())).fetchone() if con.execute("SELECT 1 FROM sqlite_master WHERE name='payment_requests'").fetchone() else None
        if late:
            stop(con, s['id'], '系統', f'{late[0]} 逾期 10 天沒付'); return n + 1
        if ix.setting(con, f"dd_ext:{s['id']}") != today.isoformat() and now.strftime('%H:%M') >= '09:00':
            ix.set_setting(con, f"dd_ext:{s['id']}", today.isoformat())
            n += extend(con, s, today)
    return n

def customer_blocked(con, acc, to, kind, order_id):
    """代丟工單給業主只傳「已收走」（和改期、取消、帳單）；其他（預約已安排、明天見、到場、完成照片、打星星）都不傳。"""
    if not order_id or str(kind or '').startswith(CUSTOMER_OK):
        return False
    o = con.execute('SELECT title FROM orders WHERE id=?', (order_id,)).fetchone()
    if not o or not is_daidiu(o):
        return False
    r = con.execute('SELECT customer_id,worker_id FROM line_users WHERE account=? AND user_id=?', (acc, to)).fetchone()
    return bool(r and r['customer_id'] and not r['worker_id'])

def done_message(con, o, at=''):
    """夥伴離場打卡 → 業主一則「已收走」。"""
    import line_bot as lb
    if not o['customer_id']:
        return
    acc, uid = lb.customer_target(con, o['customer_id'])
    if uid:
        t = (at or c.now())[11:16]
        lb.queue(con, acc, uid, 'daidiu_done', '今晚的垃圾已經收走了', lb._txt(f"🗑️ 今晚的垃圾已經收走了 ✅\n{lb.md(o['date'])} {t}"), o['id'])

def stop_api(con, oid, by, reason='管理層停止'):
    """中控台工單「停止垃圾代丟」。"""
    o = c.get(con, 'orders', oid)
    ensure(con)
    r = con.execute("SELECT id FROM daidiu_subs WHERE site_id=? AND status='active' ORDER BY created_at DESC LIMIT 1", (o['site_id'],)).fetchone()
    if not r:
        raise ValueError('這位業主沒有進行中的垃圾代丟月方案')
    return stop(con, r[0], by, reason)
