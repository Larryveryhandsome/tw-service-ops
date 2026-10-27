"""v3 營運功能：派工大廳、夥伴手機端、月結帳板、固定排程、日曆供稿、提醒與關心。
原則：系統只整理與提示，接單、報價、確認、收款仍由人決定；LINE 只產生文字讓人複製。"""
import datetime as dt
import decimal
import hashlib
import hmac
import json
import re
import secrets
import core as c
import integrations as ix

AREA = re.compile(r'(大安區|板橋區|蘇澳鎮|東區|中壢區|壯圍鄉|東區|三民區|西屯區|三星鄉|大同鄉|南澳鄉|'
                  r'[一-鿿]{1,3}[市縣][一-鿿]{1,3}[區鄉鎮市])')
OPEN_STATUSES = ('review', 'draft')
# 兩人以上的單有人離開、退回待確認補人：orders.go_partial＝「照常出發的夥伴名單」（,wid1,wid2,）。新補進來的人不在名單裡（管理群審核後重新排定才給地址）
GO_SQL = "(o.status='confirmed' OR (o.status IN ('review','draft') AND instr(COALESCE(o.go_partial,''), ','||a.worker_id||',')>0))"      # 需要 assignments a
GO_ORDER_SQL = "(o.status='confirmed' OR (o.status IN ('review','draft') AND COALESCE(o.go_partial,'')!=''))"

def goes(o, wid=None):
    """這場（對這位夥伴）是不是照常出發：已排定；或補人中而且他在照常名單裡（wid 不給＝名單有人就算）。"""
    gp = (o.get('go_partial') if hasattr(o, 'get') else o['go_partial']) or ''
    return o['status'] == 'confirmed' or (o['status'] in OPEN_STATUSES and bool(gp) and (wid is None or f',{wid},' in gp))

def set_go_partial(con, oid, exclude=None):
    """已排定的單少人／換人要退回待確認：還在的人（不含剛加進來的 exclude）照常出發。回傳名單。"""
    rest = [r[0] for r in con.execute("SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)) if r[0] != exclude]
    con.execute('UPDATE orders SET go_partial=? WHERE id=?', ((',' + ','.join(rest) + ',') if rest else '', oid))
    return rest
LIVE = ('review', 'draft', 'confirmed', 'completed')

NUM_CUT = re.compile(r'(?:\d+|[０-９]+)\s*(?:[-－之]\s*\d+\s*)?(?:弄|號|樓|[Ff](?![A-Za-z]))|(?:\d+|[０-９]+)\s*[-－]\s*\d+')

def street_of(site):
    """接單前給夥伴看的地點：縣市＋鄉鎮＋路＋巷，不含弄、號、樓；接單排定後才看得到完整地址。"""
    site = site or {}
    a = re.sub(r'\s+', '', site.get('address') or '')
    a = re.sub(r'^\d{3,6}', '', a)                          # 郵遞區號
    a = re.sub(r'[（(].*?[)）]', '', a)                      # 括號說明
    m = NUM_CUT.search(a)
    a = a[:m.start()] if m else re.sub(r'[\d０-９].*$', '', a)      # 沒寫「號」：第一個數字以後都不給
    a = re.sub(r'([一-鿿]{2}[市鎮鄉區])\1', r'\1', a).rstrip('、，,-－ ')      # 「大安區大安區」重複
    p = c.place(area_of(site))
    if a and not re.match(r'^.{2}[縣市]', a):
        a = (p[:-3] + a) if p.endswith(a[:3]) else (p + a if p != '臺北' else a)
    return a if len(a) > len(p) else p

def merge_sites(con, keep, drop, by=''):
    """同一位業主重複的地點 → 工單、別名移到正確的地點（空白的地址、進場方式等補過去），重複的放回收桶。"""
    import trash
    if keep == drop:
        raise ValueError('請選另一個地點')
    k, d = c.get(con, 'sites', keep), c.get(con, 'sites', drop)
    if k['customer_id'] != d['customer_id']:
        raise ValueError('只能合併同一位業主的地點')
    n = con.execute('UPDATE orders SET site_id=?,version=version+1 WHERE site_id=?', (keep, drop)).rowcount
    con.execute('UPDATE customer_aliases SET site_id=? WHERE site_id=?', (keep, drop))
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='daidiu_subs'").fetchone():
        con.execute('UPDATE daidiu_subs SET site_id=? WHERE site_id=?', (keep, drop))      # 每日檢查：以前合併後代丟背景每分鐘報錯
    for p_ in ('tools_ask:', 'trash_ask:'):
        v_ = ix.setting(con, p_ + drop)
        if v_ and not ix.setting(con, p_ + keep):
            ix.set_setting(con, p_ + keep, v_)
    fill = {f: d[f] for f in ('address', 'entry_notes', 'navigation', 'checklist', 'area', 'entry_mode') if not (k.get(f) or '').strip() and (d.get(f) or '').strip()}
    if fill:
        c.save(con, 'sites', {'id': keep, 'version': k['version'], **fill})
    trash.to_trash(con, 'sites', drop, f"合併到「{k['name']}」（{by}）")
    c.audit(con, 'site_merge', 'sites', keep, after={'from': drop, 'orders': n, 'filled': list(fill), 'by': by})
    return f"已合併：{n} 張工單移到「{k['name']}」，「{d['name']}」放回收桶（可以還原）"

def area_of(site):
    if not site:
        return ''
    if site.get('area'):
        return site['area']
    m = AREA.search(site.get('address') or '')
    return m.group(1)[-3:] if m else ''

def mins(a, b, pause=0):
    try:
        return c.minutes(a, b, pause)
    except Exception:
        return None

def log(con, order_id, action, detail=''):
    con.execute('INSERT INTO dispatch_log(order_id,actor,action,detail) VALUES(?,?,?,?)',
                (order_id, getattr(c.CTX, 'actor', '系統'), action, detail))

def order_minutes(con, o):
    """實際工時：逐人實績加總；沒有就用工單實際起訖（多人時乘人數）。"""
    ts = con.execute("SELECT COALESCE(SUM(t.minutes),0),COUNT(*) FROM timesheets t JOIN assignments a ON a.id=t.assignment_id "
                     "WHERE a.order_id=? AND a.status='accepted'", (o['id'],)).fetchone()
    if ts[1]:
        return ts[0]
    m = mins(o['actual_start'], o['actual_end'], o['break_minutes'] or 0) if o['actual_start'] and o['actual_end'] else None
    return m * max(1, o['required_workers'] or 1) if m else None

def suggested_charge(con, o, site=None):
    """建議應收（分）。順序：已核定 → 案場每次價 → 費率 × 時數。只是建議，開帳前人工確認。"""
    site = site or (dict(con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'],)).fetchone() or {}) if o['site_id'] else {})
    extra = (o['expense'] or 0) + (o['adjustment'] or 0)
    if o['approved_amount'] is not None:
        return o['approved_amount'] + extra, '已核定'
    if site.get('visit_price') is not None:
        return site['visit_price'] + extra, '案場每次價'
    rate = o['rate'] if o['rate'] is not None else site.get('rate')
    if rate is None:
        return None, '缺費率'
    if o['billable_hours'] is not None:
        hours = decimal.Decimal(o['billable_hours']); basis = '核定時數'
    else:
        m = order_minutes(con, o); basis = '實際時數'
        if m is None:
            m = mins(o['start'], o['end']); basis = '預排時數'
            if m is None:
                return None, '缺時數'
            m *= max(1, o['required_workers'] or 1)
        hours = decimal.Decimal(m) / 60
    return c.cents(decimal.Decimal(rate) * hours) + extra, basis

def is_redo(o):
    """補服務工單（有原本那一場，或標題開頭是「補清」）。中控台 isRedo 同一個判斷。"""
    o = dict(o) if o is not None else {}
    return bool(o.get('parent_order_id') or str(o.get('title') or '').startswith('補清'))

def redo_unpaid(o):
    """補服務而且主管判斷不支薪（建立時寫在工單備註「補掃：不支薪」）。"""
    return is_redo(o) and '補掃：不支薪' in str(dict(o).get('notes') or '')

def worker_pay(con, a, o=None):
    """夥伴應付（分）：核定金額 → 時薪 × 實績分鐘 → 時薪 × 預排；再加代墊用品（reimburse，supplies.py）。"""
    re_ = (a['reimburse'] if 'reimburse' in a.keys() else 0) or 0
    if a['amount'] is not None:
        return a['amount'] + re_
    import garbage                     # 垃圾清收：單獨清收是固定酬勞（不看時數）；服務加購照店長定的每袋另加（預設 0）
    try:
        o = o if o is not None and 'title' in o.keys() else None
    except AttributeError:
        o = None
    o = o or c.get(con, 'orders', a['order_id'])
    if redo_unpaid(o):
        return 0 + re_                 # 每日檢查：補服務「不支薪」不管誰派、怎麼派到都是 0（以前只有建立當下指定的人才是 0）
    fx = garbage.worker_fixed(con, o)
    if fx is not None:
        return fx + re_
    if a['rate'] is None:
        return None
    t = con.execute('SELECT minutes FROM timesheets WHERE assignment_id=?', (a['id'],)).fetchone()
    m = a['approved_minutes'] if a['approved_minutes'] is not None else (t[0] if t else None)
    if m is None:
        m = mins(o['start'], o['end'])
    return None if m is None else c.cents(decimal.Decimal(a['rate']) * decimal.Decimal(m) / 60) + re_ + garbage.addon_pay(con, o)

def est_pay(con, o, wid):
    """夥伴接這場能賺多少（分）＋一句說明。跟月結、夥伴收入、記帳同一套（worker_pay），所以接單看到的＝之後入帳的。
    已經有核定金額就用核定；沒有就 時薪 × 預排時數（＋垃圾清收加購）；垃圾清收單獨是固定酬勞。不含代墊用品。看不出來回 (None, '')。"""
    import garbage
    o = dict(o)
    w = con.execute('SELECT rate FROM workers WHERE id=?', (wid or '',)).fetchone()
    r = con.execute("SELECT * FROM assignments WHERE order_id=? AND worker_id=? AND status NOT IN ('cancelled','declined') ORDER BY rowid DESC LIMIT 1", (o['id'], wid or '')).fetchone()
    a = dict(r) if r else {'id': '', 'order_id': o['id'], 'amount': None, 'rate': None, 'approved_minutes': None}
    if a.get('rate') is None:
        a['rate'] = c.pay_rate(con, o['id'], wid) if w else None          # 案場有設夥伴時薪就用案場的
    a['reimburse'] = 0
    try:
        pay = worker_pay(con, a, o)
    except Exception:
        pay = None
    if pay is None:
        return None, ''
    if garbage.worker_fixed(con, o) is not None and a.get('amount') is None:          # 垃圾清收／垃圾代丟：固定酬勞
        return pay, garbage.pay_text(con, o)
    if (a.get('amount') == 0 and is_redo(o)) or (a.get('amount') is None and redo_unpaid(o)):
        return 0, '不支薪（補服務）'                    # 每日檢查：補服務不支薪，派工卡直接講清楚
    if a.get('amount') is not None:
        return pay, f'NT$ {pay // 100:,}（核定金額）'
    m = mins(o['start'], o['end'])
    hrs = f'{m / 60:g}' if m else '?'
    add = garbage.addon_pay(con, o)
    return pay, f'約 NT$ {pay // 100:,}（時薪 {a["rate"]} × {hrs} 小時' + (f'＋垃圾加購 {add // 100:,}' if add else '') + '）'

def photo_hold(con, a):
    """負責人 前後照片有拍才發放薪資（settings photo_pay_from 起的完工單；服務前、完工都要有）。回傳暫不發放的原因，沒事回 ''。"""
    import integrations as _ix
    start = _ix.setting(con, 'photo_pay_from') or '2026-10-14'
    if a.get('ostatus', a.get('status')) != 'completed' or (a.get('date') or '') < start:
        return ''
    o_ = con.execute('SELECT id,site_id FROM orders WHERE id=?', (a['order_id'],)).fetchone()
    if o_ and no_photo(con, o_):
        return ''                                  # 場內不能拍照的案場，薪資不看照片；稽核 F2：夥伴回報「這裡不能拍照」的這一場也是
    n = {r[0]: r[1] for r in con.execute("SELECT label,COUNT(*) FROM attachments WHERE order_id=? AND label IN ('before','after') GROUP BY label", (a['order_id'],))}
    miss = [t for k, t in (('before', '服務前'), ('after', '完工')) if not n.get(k)]
    if miss and _ix.setting(con, f"photo_ok:{a['order_id']}"):      # 管理層在群組按「確認，照常發薪」→ 照片不齊也照算
        return ''
    if miss == ['服務前'] and _ix.setting(con, f"forgot_before:{a['order_id']}"):
        return '忘了拍服務前照片，等管理層確認後發放'
    return f"缺{'、'.join(miss)}照片，暫不發放（補拍，或管理層在群組按「確認，照常發薪」）" if miss else ''

def pay_lines(con, worker_id, month, today=None):
    """夥伴薪資的唯一算法（月結、薪資明細、結算單、夥伴「我的收入」都用這個）。
    應付＝已完工（依核定金額 → 核定分鐘 → 打卡 → 預排）＋取消後有核定金額（取消補貼）；已排定還沒完工的另列「預估」，不算進應付。"""
    out = []
    for a in c.rows(con, "SELECT a.*,o.date,o.status ostatus,o.start ostart,o.end oend,o.title,o.site_id FROM assignments a JOIN orders o ON o.id=a.order_id "
                         "WHERE a.worker_id=? AND o.date LIKE ? AND ((a.status='accepted' AND o.status IN ('confirmed','completed')) OR (o.status='cancelled' AND a.amount>0)) "
                         "ORDER BY o.date,o.start", (worker_id, month + '%')):
        if today and a['ostatus'] == 'confirmed' and a['date'] > today:
            continue
        t = con.execute('SELECT minutes FROM timesheets WHERE assignment_id=?', (a['id'],)).fetchone()
        if a['amount'] is not None:
            m, basis = None, '核定金額' if a['ostatus'] != 'cancelled' else '取消補貼'
        elif a['approved_minutes'] is not None:
            m, basis = a['approved_minutes'], '核定工時'
        elif t:
            m, basis = t[0], '打卡'
        else:
            m, basis = mins(a['ostart'], a['oend']), '預排時間'
        site = con.execute('SELECT name FROM sites WHERE id=?', (a['site_id'],)).fetchone() if a['site_id'] else None
        if a.get('reimburse'):
            basis += f"＋代墊用品 {a['reimburse'] // 100:,}"
        hold = photo_hold(con, a)
        out.append({'assignment_id': a['id'], 'order_id': a['order_id'], 'date': a['date'], 'site': (site[0] if site else '') or a['title'], 'minutes': m, 'basis': basis,
                    'rate': a['rate'], 'pay': worker_pay(con, a), 'kind': {'completed': '完工', 'cancelled': '取消補貼'}.get(a['ostatus'], '已排定（未完工）'),
                    'counted': a['ostatus'] in ('completed', 'cancelled') and not hold, 'hold': hold,
                    'settled': bool(con.execute('SELECT 1 FROM settlement_lines WHERE assignment_id=?', (a['id'],)).fetchone())})
    return out

# ───────────────────────── 派工大廳 ─────────────────────────

def publish(con, p, open_=True):
    o = c.get(con, 'orders', p['order_id'])
    if open_:
        if o['status'] not in OPEN_STATUSES:
            raise ValueError('只有尚未確認排程的工單可以發布派工')
        if not (o['date'] and o['start'] and o['end'] and o['site_id']):
            raise ValueError('發布前請先填案場、日期、開始與結束時間')
        con.execute("UPDATE orders SET dispatch='open',dispatch_note=?,version=version+1 WHERE id=?", (p.get('note', o['dispatch_note']) or '', o['id']))
        log(con, o['id'], 'publish', p.get('note', ''))
    else:
        con.execute("UPDATE orders SET dispatch='',version=version+1 WHERE id=?", (o['id'],))
        log(con, o['id'], 'unpublish')
    c.audit(con, 'publish' if open_ else 'unpublish', 'orders', o['id'])
    return c.get(con, 'orders', o['id'])

def _my_assignment(con, user, assignment_id):
    a = c.get(con, 'assignments', assignment_id)
    if not user.get('worker_id') or a['worker_id'] != user['worker_id']:
        raise PermissionError('這不是你的派工')
    return a

def apply(con, user, order_id, withdraw=False):
    if not user.get('worker_id'):
        raise PermissionError('帳號尚未對應服務人員')
    w = c.get(con, 'workers', user['worker_id'])
    o = c.get(con, 'orders', order_id)
    old = con.execute('SELECT * FROM assignments WHERE order_id=? AND worker_id=?', (o['id'], w['id'])).fetchone()
    if withdraw:
        if not old or old['status'] != 'applied':
            raise ValueError('只能撤回尚未被選上的報名')
        con.execute("UPDATE assignments SET status='cancelled',version=version+1 WHERE id=?", (old['id'],))
        log(con, o['id'], 'withdraw', w['name'])
        return {'withdrawn': True}
    if o['dispatch_only'] and not old:
        raise PermissionError('這筆是指定邀請的工作')
    if o['dispatch'] != 'open' or o['status'] not in OPEN_STATUSES or o['date'] < dt.datetime.now(c.TZ).date().isoformat():
        raise ValueError('這筆工作已經截止')
    if w['status'] != 'active':
        raise ValueError('你的夥伴資料還在審核，請聯絡示範服務店')
    if old and old['status'] in ('applied', 'accepted', 'invited'):
        return dict(old)
    if old:
        con.execute("UPDATE assignments SET status='applied',version=version+1 WHERE id=?", (old['id'],)); key = old['id']
    else:
        key = c.uid('ass')
        con.execute("INSERT INTO assignments(id,order_id,worker_id,status,pay_mode,rate,notes) VALUES(?,?,?,?,?,?,?)",
                    (key, o['id'], w['id'], 'applied', 'hourly', c.pay_rate(con, o['id'], w['id']), '夥伴自行報名'))
    log(con, o['id'], 'apply', w['name'])
    c.audit(con, 'apply', 'assignments', key)
    import notify
    notify.staff(con, f"{w['name']} 報名了工作", f"{o['date']} {o['start']} {o['title']}，請到派工確認", '/#dispatch')
    return c.get(con, 'assignments', key)

def respond(con, user, assignment_id, accept):
    """管理員指定邀請後，夥伴自己按接受／婉拒。"""
    a = _my_assignment(con, user, assignment_id)
    if a['status'] != 'invited':
        raise ValueError('這筆邀請已處理')
    o = c.get(con, 'orders', a['order_id'])
    status = 'accepted' if accept else 'declined'
    if accept:
        c.save(con, 'assignments', {'id': a['id'], 'version': a['version'], 'status': 'accepted'})
    else:
        con.execute("UPDATE assignments SET status='declined',version=version+1 WHERE id=?", (a['id'],))
    log(con, o['id'], status, user['name'])
    import dispatch, notify
    if accept:
        dispatch.after_accept(con, o['id'])
    notify.staff(con, f"{user['name']}{'接受' if accept else '婉拒'}了工作", f"{o['date']} {o['start']} {o['title']}", '/#dispatch')
    return c.get(con, 'assignments', a['id'])

def checkin(con, user, p):
    a = _my_assignment(con, user, p['assignment_id'])
    o = c.get(con, 'orders', a['order_id'])
    arrived = con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!='' AND (left_at IS NULL OR left_at='')", (a['id'],)).fetchone()
    if p.get('kind') == 'leave' and arrived and (a['status'] != 'accepted' or o['status'] not in ('confirmed',)):
        # 盤點 S-24：人已經在現場做，後台中途改派／改狀態 → 仍讓他離場打卡（工時是真的），並通知管理群確認
        import line_bot
        line_bot.admin_notify(con, f"⚠️ {user['name']} 工作中被改派，仍已離場打卡", [('處理', '他有到場紀錄，工時照記；請確認這筆的派工與薪資')], '/#dispatch', '#C47F00', oid=o['id'])
        if a['status'] != 'accepted':
            con.execute("UPDATE assignments SET status='accepted',notes=COALESCE(notes,'')||'（改派前已到場，保留工時）',version=version+1 WHERE id=?", (a['id'],))
            a = c.get(con, 'assignments', a['id'])
    elif a['status'] != 'accepted' or not goes(o, a['worker_id']):
        raise ValueError('工單確認排程後才能打卡')
    now = dt.datetime.now(c.TZ)
    lat, lng = p.get('lat'), p.get('lng')
    lat = float(lat) if isinstance(lat, (int, float)) else None; lng = float(lng) if isinstance(lng, (int, float)) else None
    row = con.execute('SELECT * FROM checkins WHERE assignment_id=?', (a['id'],)).fetchone()
    if p.get('kind') == 'arrive':
        if row and row['arrived_at']:
            raise ValueError('已經打過到場卡')
        if o['date'] != now.date().isoformat():
            raise ValueError('只能在服務當天打到場卡')
        con.execute('INSERT INTO checkins(assignment_id,arrived_at,arrive_lat,arrive_lng) VALUES(?,?,?,?) '
                    'ON CONFLICT(assignment_id) DO UPDATE SET arrived_at=excluded.arrived_at,arrive_lat=excluded.arrive_lat,arrive_lng=excluded.arrive_lng',
                    (a['id'], now.isoformat(timespec='seconds'), lat, lng))
        log(con, o['id'], 'arrive', user['name'])
    elif p.get('kind') == 'leave':
        if not row or not row['arrived_at']:
            raise ValueError('請先打到場卡')
        if row['left_at']:
            raise ValueError('已經打過離場卡')
        start = row['arrived_at'][11:16]; end = now.strftime('%H:%M')
        if p.get('end_time'):                  # 補登：忘了離場打卡，隔天填實際離場時間
            end = str(p['end_time'])[:5]
            now = dt.datetime.fromisoformat(f"{row['arrived_at'][:10]}T{end}:00").replace(tzinfo=c.TZ)
        pause = c.integer(p.get('pause', 0) or 0, '休息分鐘')
        if end <= start:
            raise ValueError('離場時間要晚於到場時間')
        con.execute('UPDATE checkins SET left_at=?,leave_lat=?,leave_lng=? WHERE assignment_id=?', (now.isoformat(timespec='seconds'), lat, lng, a['id']))
        import workflow
        old = con.execute('SELECT version FROM timesheets WHERE assignment_id=?', (a['id'],)).fetchone()
        workflow.command(con, 'timesheet', {'assignment_id': a['id'], 'start': start, 'end': end, 'pause': pause,
                                            'notes': '夥伴打卡：' + (p.get('notes') or '依打卡時間'), 'version': old[0] if old else None})
        log(con, o['id'], 'leave', user['name'])
    else:
        raise ValueError('打卡種類不符')
    return dict(con.execute('SELECT * FROM checkins WHERE assignment_id=?', (a['id'],)).fetchone())

def field_report(con, user, p):
    a = _my_assignment(con, user, p['assignment_id'])
    o = c.get(con, 'orders', a['order_id'])
    text = (p.get('report') or '').strip()
    came = con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!=''", (a['id'],)).fetchone()
    if a['status'] != 'accepted' or not (o['status'] == 'confirmed' or (came and o['status'] in OPEN_STATUSES)):      # 已到場、中途工單被退回待確認（加人等）：回報照收
        raise ValueError('只能回報已排程的工作')
    if not text:
        raise ValueError('請寫完工回報')
    report = (o['field_report'] + '\n' if o['field_report'] else '') + f"{user['name']}：{text}"
    con.execute('UPDATE orders SET field_report=?,field_reported_at=? WHERE id=?', (report[:4000], c.now(), o['id']))
    log(con, o['id'], 'field_report', user['name'])
    c.audit(con, 'field_report', 'orders', o['id'], after={'report': text})
    return {'saved': True}

def worker_photo_allowed(con, user, order_id):
    if not user.get('worker_id') or not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'",
                                                   (order_id, user['worker_id'])).fetchone():
        raise PermissionError('只能上傳自己的工作照片')

MONEY = re.compile(r'(NT\$|\$|元|價|費|收款|匯款|帳|發票|統編|折扣|\d{3,}\s*/)')          # 舊規則（保留給其他地方判斷用）
AMOUNT = re.compile(r'(NT\$\s*[\d,]+|\$\s*[\d,]+|[\d,]{2,}\s*元(?:\s*[／/]\s*[^\s，。、]{1,2})?|[\d,]{3,}\s*[/／]\s*(?:時|小時|次|間|單位|人|場|月|台|個)|(?:單價|總價|報價|小計|總計)\s*[:：]?\s*[\d,]+)')
FIN = re.compile(r"(收款|匯款|轉帳|帳號|統編|訂金|尾款|折扣|報價|價格|價錢|費用|發票號碼|本次收|收現|現收|付款|已付|代收|收費|應收)")
def _no_money(text):
    """夥伴看不到金額、帳務：帳務資料（收款、匯款、訂金⋯＋數字）整行拿掉；其他行只拿掉金額本身。
    盤點：舊版看到「發票」就整行刪，連「發票放桌上不要丟」這種作業指示也不見了。"""
    out = []
    for x in (text or '').splitlines():
        if FIN.search(x) and re.search(r'\d', x):
            continue
        y = AMOUNT.sub('', x).strip(' ：:，,、')
        if y and not (y != x.strip() and len(y) <= 3):        # 拿掉金額後只剩「月結」「本次收」這種標籤 → 整行拿掉
            out.append(y)
    return '\n'.join(out)

def worker_state(con, user):
    """夥伴只看得到：開放報名的工作（不含客戶姓名、電話、價格）、自己的派工、自己的薪資。"""
    today = dt.datetime.now(c.TZ).date().isoformat()
    wid = user.get('worker_id')
    me = dict(con.execute('SELECT id,name,status,rate,regions FROM workers WHERE id=?', (wid,)).fetchone() or {}) if wid else {}
    sites = {s['id']: s for s in c.rows(con, 'SELECT * FROM sites')}
    mine = {a['order_id']: a for a in c.rows(con, "SELECT * FROM assignments WHERE worker_id=? AND status!='cancelled'", (wid or '',))}
    jobs = []
    for o in c.rows(con, "SELECT * FROM orders WHERE dispatch='open' AND status IN ('review','draft') AND date>=? ORDER BY date,start", (today,)):
        if o['dispatch_only'] and o['id'] not in mine:
            continue
        s = sites.get(o['site_id']) or {}
        taken = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        jobs.append({'id': o['id'], 'date': o['date'], 'start': o['start'], 'end': o['end'], 'area': area_of(s), 'street': street_of(s),
                     'service_type': s.get('service_type', ''), 'minutes': mins(o['start'], o['end']), 'required_workers': o['required_workers'],
                     'taken': taken, 'note': _no_money(o['dispatch_note']), 'scope': '' if o.get('scope_hold') else _no_money(o['scope']), 'mine': mine.get(o['id'], {}).get('status', '')})
    my = []
    for a in mine.values():
        o = c.get(con, 'orders', a['order_id'])
        if o['status'] == 'archived':
            continue
        s = sites.get(o['site_id']) or {}
        detail = a['status'] == 'accepted' and o['status'] in ('confirmed', 'completed')
        ck = con.execute('SELECT * FROM checkins WHERE assignment_id=?', (a['id'],)).fetchone()
        ts = con.execute('SELECT start,end,pause,minutes FROM timesheets WHERE assignment_id=?', (a['id'],)).fetchone()
        my.append({'assignment_id': a['id'], 'assignment_status': a['status'], 'order_id': o['id'], 'order_status': o['status'],
                   'date': o['date'], 'start': o['start'], 'end': o['end'], 'area': area_of(s), 'street': street_of(s), 'service_type': s.get('service_type', ''),
                   'site_name': c.place(area_of(s)) if detail else '', 'address': s.get('address', '') if a['status'] == 'accepted' else '', 'scope': '' if o.get('scope_hold') else _no_money(o['scope']),
                   'navigation': __import__('sitegeo').nav_url(s) if a['status'] == 'accepted' else '', 'entry_notes': _no_money(s.get('entry_notes', '')) if detail else '',
                   'checklist': _no_money(s.get('checklist', '')) if detail and not o.get('scope_hold') else '', 'site_notes': '', 'order_notes': '',          # 備註是內部用，夥伴看不到
                   'field_report': o['field_report'] if detail else '',
                   'checkin': dict(ck) if ck else None, 'timesheet': dict(ts) if ts else None,
                   'photos': con.execute('SELECT COUNT(*) FROM attachments WHERE order_id=?', (o['id'],)).fetchone()[0] if detail else 0,
                   'pay': worker_pay(con, a, o) if a['status'] == 'accepted' else None})
    my.sort(key=lambda x: (x['date'], x['start']))
    settlements = c.rows(con, 'SELECT s.id,s.period,s.status,s.paid_at,COALESCE(SUM(l.amount),0) total FROM settlements s '
                              'LEFT JOIN settlement_lines l ON l.settlement_id=s.id WHERE s.worker_id=? GROUP BY s.id ORDER BY s.period DESC', (wid or '',))
    av = c.rows(con, 'SELECT date,start,end,kind,note FROM availability WHERE worker_id=? AND date>=?', (wid or '', today[:7] + '-01'))
    return {'role': 'cleaner', 'today': today, 'me': me, 'jobs': jobs, 'mine': my, 'settlements': settlements, 'availability': av}

# ───────────────────────── 月結帳板 ─────────────────────────

def billing_board(con, month):
    c.date(month + '-01')
    today = dt.datetime.now(c.TZ).date().isoformat()
    sites = {s['id']: s for s in c.rows(con, 'SELECT * FROM sites')}
    workers = {w['id']: w['name'] for w in c.rows(con, 'SELECT id,name FROM workers')}
    invoiced = {r[0]: r[1] for r in con.execute('SELECT order_id,invoice_id FROM invoice_lines WHERE order_id IS NOT NULL')}
    customers = {}
    def bucket(cid):
        if cid not in customers:
            cu = c.get(con, 'customers', cid)
            import brands, booking
            b = brands.of_customer(cu); co = booking.company(con, b)       # 盤點：請款訊息依客戶品牌（不寫死示範服務店），匯款帳號用該品牌的公司資料
            bank = f"匯款資訊：{co['bank']}　帳號 {co['account']}　戶名 {co['holder']}" if co.get('account') else ''
            customers[cid] = {'customer_id': cid, 'name': cu['name'], 'billing_rule': cu['billing_rule'], 'phone': cu['phone'],
                              'brand': b, 'brand_name': brands.name(b), 'bank': bank,
                              'orders': [], 'plans': [], 'invoices': [], 'older_balance': 0}
        return customers[cid]
    for o in c.rows(con, "SELECT * FROM orders WHERE date LIKE ? AND status IN ('review','draft','confirmed','completed') AND customer_id IS NOT NULL AND COALESCE(review_note,'')!='示範工單' ORDER BY date,start", (month + '%',)):      # 稽核 A3
        s = sites.get(o['site_id']) or {}
        amount, basis = suggested_charge(con, o, s)
        ws = c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],))
        actual = order_minutes(con, o)
        planned = mins(o['start'], o['end'])
        import payments
        pre = payments.prepaid(con, o)              # 稽核：已用收款單收的（線上簽約、訂閱月費）不列「完工待開帳」，避免重複請款
        bucket(o['customer_id'])['orders'].append({
            'id': o['id'], 'date': o['date'], 'start': o['start'], 'end': o['end'], 'site': s.get('name', ''), 'status': o['status'], 'prepaid': pre or bool(is_redo(o) and (amount == 0 or redo_unpaid(o))),      # 每日檢查：不收費的補服務不算待開帳
            
            'planned_minutes': planned * max(1, o['required_workers'] or 1) if planned else None, 'actual_minutes': actual,
            'workers': [workers.get(w['worker_id'], '') for w in ws], 'billing_mode': o['billing_mode'], 'charge_approved': o['charge_approved'],
            'acceptance': o['acceptance'], 'amount': amount, 'basis': basis, 'invoice_id': invoiced.get(o['id']), 'version': o['version'],
            'plan_id': o['plan_id']})
    first = month + '-01'; last = (dt.date.fromisoformat(first).replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    for p in c.rows(con, "SELECT * FROM plans WHERE status='active' AND valid_from<=? AND valid_to>=?", (last.isoformat(), first)):
        import workflow
        bucket(p['customer_id'])['plans'].append({**p, **workflow.plan_balance(con, p['id'])})
    for i in c.rows(con, 'SELECT * FROM invoices ORDER BY created_at'):
        t = c.invoice_totals(con, i['id'])
        if i['period'] == month:
            bucket(i['customer_id'])['invoices'].append({**i, **t, 'overdue': i['status'] == 'issued' and t['balance'] > 0 and i['due_date'] < today})
        elif i['status'] == 'issued' and t['balance'] > 0 and i['period'] < month:
            bucket(i['customer_id'])['older_balance'] += t['balance']
    totals = {'expected': 0, 'billed': 0, 'paid': 0, 'balance': 0, 'overdue': 0, 'to_bill': 0, 'customers': 0}
    for b in customers.values():
        live = [o for o in b['orders'] if o['status'] != 'cancelled']
        unbilled = [o for o in live if not o['invoice_id'] and not o['prepaid']]
        b['done'] = sum(1 for o in live if o['status'] == 'completed'); b['scheduled'] = len(live)
        b['unbilled_completed'] = [o['id'] for o in unbilled if o['status'] == 'completed']
        b['pending_service'] = sum(1 for o in unbilled if o['status'] != 'completed')
        b['missing_amount'] = sum(1 for o in unbilled if o['amount'] is None)
        b['billed'] = sum(i['total'] for i in b['invoices'] if i['status'] == 'issued')
        b['paid'] = sum(i['paid'] for i in b['invoices'])
        b['balance'] = sum(i['balance'] for i in b['invoices'] if i['status'] == 'issued')
        b['overdue'] = any(i['overdue'] for i in b['invoices'])
        b['draft_invoices'] = sum(1 for i in b['invoices'] if i['status'] == 'draft')
        b['unbilled_amount'] = sum(o['amount'] or 0 for o in unbilled)
        b['expected'] = b['billed'] + b['unbilled_amount']
        if b['overdue']:
            b['state'] = 'overdue'
        elif b['unbilled_completed']:
            b['state'] = 'to_bill'
        elif b['balance'] > 0:
            b['state'] = 'billed'
        elif b['invoices'] and not b['pending_service']:
            b['state'] = 'paid'
        else:
            b['state'] = 'in_service'
        totals['expected'] += b['expected']; totals['billed'] += b['billed']; totals['paid'] += b['paid']
        totals['balance'] += b['balance'] + b['older_balance']; totals['to_bill'] += sum(o['amount'] or 0 for o in unbilled if o['status'] == 'completed')
        totals['overdue'] += sum(i['balance'] for i in b['invoices'] if i['overdue'])
    totals['customers'] = len(customers)
    order = {'overdue': 0, 'to_bill': 1, 'billed': 2, 'in_service': 3, 'paid': 4}
    payroll = {}
    for (wid,) in con.execute("SELECT DISTINCT a.worker_id FROM assignments a JOIN orders o ON o.id=a.order_id WHERE o.date LIKE ? AND COALESCE(o.review_note,'')!='示範工單' "
                              "AND ((a.status='accepted' AND o.status IN ('confirmed','completed')) OR (o.status='cancelled' AND a.amount>0))", (month + '%',)).fetchall():
        lines = pay_lines(con, wid, month)            # 跟薪資明細、結算單、夥伴「我的收入」同一套
        done = [x for x in lines if x['counted']]
        held = [x for x in lines if x.get('hold')]
        payroll[wid] = {'worker_id': wid, 'name': workers.get(wid, ''), 'jobs': len(lines), 'done': len(done), 'held': held,
                        'minutes': sum(x['minutes'] or 0 for x in done), 'pay': sum(x['pay'] or 0 for x in done),
                        'missing': sum(1 for x in done if x['pay'] is None), 'settled': sum(1 for x in done if x['settled']), 'lines': done}
    return {'month': month, 'today': today, 'totals': totals,
            'customers': sorted(customers.values(), key=lambda b: (order[b['state']], b['name'])),
            'payroll': sorted(payroll.values(), key=lambda w: w['name']), 'bank_info': ix.setting(con, 'bank_info')}

def bill_month(con, p):
    """一鍵：核定這些已完工工單的金額 → 建帳單 → 核定。全在同一個交易裡，任一筆失敗全部回復。"""
    cid = p['customer_id']; month = p['month']; c.get(con, 'customers', cid); c.date(month + '-01')
    lines = p.get('lines') or []; manual = [m for m in (p.get('manual_lines') or []) if str(m.get('description', '')).strip()]
    if not lines and not manual:
        raise ValueError('請至少選一筆服務或填方案月費')
    ids = []
    for ln in lines:
        o = c.get(con, 'orders', ln['order_id'])
        if o['customer_id'] != cid or o['status'] != 'completed':
            raise ValueError(f"{o['date']} 的服務還沒完工，不能請款")
        if con.execute('SELECT 1 FROM invoice_lines WHERE order_id=?', (o['id'],)).fetchone():
            raise ValueError(f"{o['date']} 已經開過帳，不能重複請款")
        import payments
        if payments.prepaid(con, o) and not ln.get('extra'):
            raise ValueError(f"{o['date']} 這筆已經用{payments.prepaid(con, o)}收款，不能再開帳（要加收額外費用請在「方案月費／其他」另填一行）")
        if o['acceptance'] == 'disputed' and not o.get('feedback_done'):      # 盤點：業主要求加強還沒處理，不能直接開帳
            raise ValueError(f"{o['date']} 業主要求加強還沒處理完，請先在首頁「客戶回饋待處理」按已處理")
        amount = c.integer(ln.get('amount'), f"{o['date']} 金額")
        changes = {'billing_mode': 'fixed', 'approved_amount': amount - (o['expense'] or 0) - (o['adjustment'] or 0),
                   'charge_approved': 1, 'approval_note': (ln.get('note') or '月結核定').strip(), 'version': o['version'] + 1}
        if changes['approved_amount'] < 0:
            raise ValueError(f"{o['date']} 金額小於額外費用，請改工單的加減價")
        con.execute('UPDATE orders SET ' + ','.join(f'{k}=?' for k in changes) + ' WHERE id=?', (*changes.values(), o['id']))
        c.audit(con, 'approve-charge', 'orders', o['id'], o, changes)
        ids.append(o['id'])
    due = p.get('due_date') or month + '-28'
    inv = c.create_invoice(con, {'customer_id': cid, 'period': month, 'due_date': due, 'order_ids': ids,
                                 'manual_lines': [{'description': m['description'], 'amount': c.integer(m['amount'], '方案金額')} for m in manual],
                                 'notes': p.get('notes', '')})
    if p.get('issue', True):
        c.invoice_action(con, inv['id'], 'issue', {})
    return {**c.get(con, 'invoices', inv['id']), **c.invoice_totals(con, inv['id'])}

# ───────────────────────── 排班工具 ─────────────────────────

def recurring(con, p):
    """固定排程：指定案場、星期、時段、期間，一次產生多張工單（可直接指定夥伴並確認）。"""
    site = c.get(con, 'sites', p['site_id'])
    weekdays = sorted({int(x) for x in p.get('weekdays', [])})
    if not weekdays or any(x < 0 or x > 6 for x in weekdays):
        raise ValueError('請選擇星期')
    start = c.date(p['from']); end = c.date(p['to'])
    if end < start or (end - start).days > 400:
        raise ValueError('期間不符（最多一年）')
    c.minutes(p['start'], p['end'])
    every = c.integer(p.get('every_weeks', 1) or 1, '每幾週', 1)
    created = []; skipped = []
    day = start
    while day <= end:
        weeks = (day - start).days // 7
        if day.weekday() in weekdays and weeks % every == 0:
            iso = day.isoformat()
            if con.execute("SELECT 1 FROM orders WHERE site_id=? AND date=? AND start=? AND status NOT IN ('cancelled','archived')", (site['id'], iso, p['start'])).fetchone():
                skipped.append(iso)
            else:
                o = c.save(con, 'orders', {'title': p.get('title') or site['name'], 'site_id': site['id'], 'date': iso, 'start': p['start'], 'end': p['end'],
                                           'required_workers': p.get('required_workers', 1), 'billing_mode': p.get('billing_mode', 'fixed'),
                                           'rate': p.get('rate') if p.get('rate') not in (None, '') else site['rate'],
                                           'plan_id': p.get('plan_id') or None, 'notes': p.get('notes', ''),
                                           'review_note': '固定排程產生' + ('；客戶已同意固定時段' if p.get('customer_confirmed') else ''),
                                           'customer_confirmed': 1 if p.get('customer_confirmed') else 0})
                if p.get('worker_id'):
                    w = c.get(con, 'workers', p['worker_id'])
                    a = c.save(con, 'assignments', {'order_id': o['id'], 'worker_id': w['id'], 'status': 'accepted', 'pay_mode': 'hourly',
                                                    'rate': p.get('worker_rate') if p.get('worker_rate') not in (None, '') else c.pay_rate(con, o['id'], w['id'])})
                    if p.get('confirm') and p.get('customer_confirmed'):
                        o = c.get(con, 'orders', o['id'])
                        after_transition(con, o, c.transition(con, o['id'], 'confirm', {'version': o['version']}), 'confirm', getattr(c.CTX, 'actor', '系統'))
                    else:
                        import line_bot
                        line_bot.on_assigned_worker(con, a['id'])      # 排進去了就通知那位夥伴（跟後台單筆指派一樣）
                created.append(o['id'])
        day += dt.timedelta(days=1)
    return {'created': len(created), 'skipped': skipped}

def after_transition(con, before, out, action, actor):
    """工單排定／取消之後的通知（單筆、批次、固定排程、待整理區都走這裡，才不會有的有通知、有的沒有）。"""
    import alerts, notify, line_bot
    alerts.change(con, before, out, actor)
    if action in ('confirm', 'cancel'):
        ws = [r[0] for r in con.execute("SELECT worker_id FROM assignments WHERE order_id=? AND status IN ('accepted','cancelled')", (out['id'],))]
        msg = '已排定，地址與注意事項已開放' if action == 'confirm' else '已取消，請不要前往'
        notify.workers(con, ws, '工作' + ('已確認' if action == 'confirm' else '取消'), f"{out['date']} {out['start']}–{out['end']} {msg}", '/#mine')
        if action == 'confirm':
            line_bot.on_confirmed(con, out['id'])       # 人工排定也把地址、進場方式用 LINE 傳給夥伴

def bulk(con, p):
    """批次操作：逐筆用 savepoint，成功的留下、失敗的列原因。"""
    action = p.get('action'); done = 0; errors = []
    for oid in p.get('ids', [])[:500]:
        con.execute('SAVEPOINT b')
        try:
            o = c.get(con, 'orders', oid)
            if action == 'confirm':
                after_transition(con, o, c.transition(con, oid, 'confirm', {'version': o['version']}), 'confirm', getattr(c.CTX, 'actor', '系統'))
            elif action == 'archive':
                if o['status'] not in ('review', 'draft') or con.execute('SELECT 1 FROM invoice_lines WHERE order_id=?', (oid,)).fetchone():
                    raise ValueError('只有待核對工單可以封存')
                con.execute("UPDATE orders SET status='archived',version=version+1 WHERE id=?", (oid,))
                c.audit(con, 'archive', 'orders', oid, o)
                import payments; payments.on_order_change(con, o, c.get(con, 'orders', oid), getattr(c.CTX, 'actor', '系統'))
            elif action == 'customer-confirmed':
                c.save(con, 'orders', {'id': oid, 'version': o['version'], 'customer_confirmed': 1})
            elif action == 'reminded':
                con.execute('UPDATE orders SET reminded_at=? WHERE id=?', (c.now(), oid)); log(con, oid, 'reminded')
            elif action == 'feedback-done':        # 客戶回饋（改時間／要求加強／低分）已處理
                con.execute('UPDATE orders SET feedback_done=? WHERE id=?', (dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), oid)); log(con, oid, 'feedback_done')   # 時間跟 dispatch_log.at 同格式（UTC）
            elif action == 'followed-up':
                con.execute('UPDATE orders SET followed_up_at=? WHERE id=?', (c.now(), oid)); log(con, oid, 'followed_up')
            else:
                raise ValueError('不支援的批次操作')
            con.execute('RELEASE b'); done += 1
        except (ValueError, KeyError) as e:
            con.execute('ROLLBACK TO b'); con.execute('RELEASE b')
            errors.append({'id': oid, 'error': str(e)})
    return {'done': done, 'errors': errors}

def no_photo(con, o):
    """這場可以不拍照：案場設了「場內不能拍照」，或夥伴當場回報「這裡不能拍照」（這一場先放行，管理層再決定要不要設成整個案場）。"""
    if str(o['title'] if 'title' in o.keys() else '').startswith('垃圾代丟'):
        return True                            # 垃圾代丟：不用前後照，收完離場打卡就好
    sid = o['site_id'] if 'site_id' in o.keys() else None
    if sid and con.execute('SELECT 1 FROM sites WHERE id=? AND COALESCE(no_photo,0)=1', (sid,)).fetchone():
        return True
    return bool(ix.setting(con, f"nophoto_job:{o['id']}"))

def set_times(con, aid, arrived, left='', pause=0, note='', by=''):
    """管理層手動改夥伴的到場／離場打卡時間（要寫原因）。改打卡紀錄＋工時（薪資、月結、我的收入都照工時算），不通知業主。"""
    import re as _re
    a = con.execute('SELECT * FROM assignments WHERE id=?', (aid,)).fetchone()
    if not a or a['status'] != 'accepted':
        raise ValueError('找不到這位夥伴的排班')
    o = c.get(con, 'orders', a['order_id'])
    if o['status'] not in ('confirmed', 'completed') and not goes(o, a['worker_id']):
        raise ValueError('工單排定後才能改打卡時間')
    note = (note or '').strip()
    if len(note) < 2:
        raise ValueError('請寫更正原因（例：夥伴忘了打卡、實際 9:30 才到）')
    arrived, left = (arrived or '').strip(), (left or '').strip()
    if not _re.match(r'^\d{2}:\d{2}$', arrived) or (left and not _re.match(r'^\d{2}:\d{2}$', left)):
        raise ValueError('時間格式要像 09:30（到場一定要填）')
    pause = int(pause or 0)
    n = c.minutes(arrived, left, pause) if left else None
    if con.execute('SELECT 1 FROM settlement_lines WHERE assignment_id=?', (aid,)).fetchone():
        raise ValueError('這位夥伴這場的薪資已經結算，不能再改（請找負責人）')
    if con.execute('SELECT 1 FROM invoice_lines WHERE order_id=?', (o['id'],)).fetchone():          # 稽核 F11
        raise ValueError('這張工單已經開帳了，不能再改打卡時間（請找負責人）')
    if f"{o['date']}T{left or arrived}" > dt.datetime.now(c.TZ).isoformat()[:16]:      # 已結算／已開帳先講（不能改的原因比較重要）
        raise ValueError('不能填還沒到的時間')
    ck = con.execute('SELECT * FROM checkins WHERE assignment_id=?', (aid,)).fetchone()
    t = con.execute('SELECT * FROM timesheets WHERE assignment_id=?', (aid,)).fetchone()
    before = {'arrived': ck['arrived_at'] if ck else '', 'left': ck['left_at'] if ck else '', 'timesheet': f"{t['start']}–{t['end']}" if t else ''}
    ta = f"{o['date']}T{arrived}:00+08:00"
    tl = f"{o['date']}T{left}:00+08:00" if left else (ck['left_at'] if ck else '') or ''
    if ck:
        con.execute('UPDATE checkins SET arrived_at=?,left_at=? WHERE assignment_id=?', (ta, tl, aid))
    else:
        con.execute('INSERT INTO checkins(assignment_id,arrived_at,left_at) VALUES(?,?,?)', (aid, ta, tl))
    if left:
        why = f"管理員更正（{by}）：{note[:100]}" + (f"（原 {t['start']}–{t['end']}）" if t else '')
        if t:
            con.execute('UPDATE timesheets SET start=?,end=?,pause=?,minutes=?,notes=?,version=version+1 WHERE assignment_id=?', (arrived, left, pause, n, why, aid))
        else:
            con.execute('INSERT INTO timesheets(id,assignment_id,start,end,pause,minutes,notes) VALUES(?,?,?,?,?,?,?)', (c.uid('ts'), aid, arrived, left, pause, n, why))
        crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        if crew == 1 and o['status'] == 'completed':          # 單人：工單的實際時間跟著改（帳單看這個）
            con.execute('UPDATE orders SET actual_start=?,actual_end=?,break_minutes=?,version=version+1 WHERE id=?', (arrived, left, pause, o['id']))
    wn = (con.execute('SELECT name FROM workers WHERE id=?', (a['worker_id'],)).fetchone() or [''])[0]
    c.audit(con, 'checkin_fix', 'assignments', aid, before, {'arrived': arrived, 'left': left, 'pause': pause, 'note': note, 'by': by})
    log(con, o['id'], 'time_fix', f"{wn} {arrived}–{left or '（還沒離場）'}（{by}：{note[:40]}）")
    msg = f"✅ {wn}：到場 {arrived}" + (f"、離場 {left}（{n // 60} 小時 {n % 60} 分）" if left else '') + '，已改好（不會通知業主）'
    if a['approved_minutes'] is not None:
        msg += '\n⚠️ 這位已經有「核定工時」，薪資以核定工時為準；要改請到「計酬」'
    return {'message': msg}

def quick_complete(con, p):
    """完工登記：沿用預排時間（可改），單人自動帶實績；夥伴已打卡的以打卡為準。"""
    o = c.get(con, 'orders', p['id'])
    if not goes(o):
        raise ValueError('請先確認排程')
    change = {'id': o['id'], 'version': o['version'], 'actual_start': p.get('actual_start') or o['start'],
              'actual_end': p.get('actual_end') or o['end'], 'break_minutes': p.get('break_minutes', 0) or 0}
    o = c.save(con, 'orders', change)
    crew = c.rows(con, "SELECT id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],))
    if len(crew) == 1:              # 單人：打卡是工時正本；同事在這裡改了時間＝更正打卡（帳單、薪資才會一致）
        t = con.execute('SELECT start,end,pause FROM timesheets WHERE assignment_id=?', (crew[0]['id'],)).fetchone()
        if t and (t['start'], t['end'], t['pause'] or 0) != (o['actual_start'], o['actual_end'], o['break_minutes'] or 0):
            n = c.minutes(o['actual_start'], o['actual_end'], o['break_minutes'] or 0)
            con.execute("UPDATE timesheets SET start=?,end=?,pause=?,minutes=?,notes=?,version=version+1 WHERE assignment_id=?",
                        (o['actual_start'], o['actual_end'], o['break_minutes'] or 0, n, f"完工登記更正（原打卡 {t['start']}–{t['end']}）", crew[0]['id']))
            c.audit(con, 'timesheet_fix', 'assignments', crew[0]['id'], dict(t), {'start': o['actual_start'], 'end': o['actual_end'], 'minutes': n})
    elif len(crew) > 1:             # 多人服務有人沒打卡 → 完工登記表每人一列（預設＝工單實際時間），直接記工時，不再擋「請先逐人記錄」
        ct = p.get('crew_times') or {}
        for a in crew:
            if con.execute('SELECT 1 FROM timesheets WHERE assignment_id=?', (a['id'],)).fetchone():
                continue
            st_, en_ = (ct.get(a['id']) or [None, None])[:2]
            st_, en_ = st_ or o['actual_start'], en_ or o['actual_end']
            n = c.minutes(st_, en_, o['break_minutes'] or 0)
            con.execute('INSERT INTO timesheets(id,assignment_id,start,end,pause,minutes,notes) VALUES(?,?,?,?,?,?,?)',
                        (c.uid('ts'), a['id'], st_, en_, o['break_minutes'] or 0, n, f"完工登記代填（{'管理層逐人填' if a['id'] in ct else '依工單實際時間'}）"))
            c.audit(con, 'timesheet_fill', 'assignments', a['id'], None, {'start': st_, 'end': en_, 'minutes': n, 'by': 'quick_complete'})
    report = (p.get('report') or o['field_report'] or '依排班完成').strip()
    acc = p.get('acceptance') or 'pending'
    if acc == 'pending' and o['acceptance'] in ('accepted', 'disputed'):
        acc = o['acceptance']          # 客戶已在 LINE 確認完工／要求加強：表單預設的「待驗收」不能蓋掉
    return c.transition(con, o['id'], 'complete', {'version': o['version'], 'report': report, 'acceptance': acc})

# ───────────────────────── Google 日曆供稿（Apps Script 拉取） ─────────────────────────

def feed_key(con, rotate=False):
    key = ix.setting(con, 'calendar_feed_hash')
    if rotate or not key:
        token = secrets.token_urlsafe(24)
        ix.set_setting(con, 'calendar_feed_hash', hashlib.sha256(token.encode()).hexdigest())
        ix.put_secret(con, 'calendar_feed_token', token)
        return token
    return ix.secret(con, 'calendar_feed_token')

def feed(con, token):
    key = ix.setting(con, 'calendar_feed_hash')
    if not key or not hmac.compare_digest(hashlib.sha256((token or '').encode()).hexdigest(), key):
        raise PermissionError('日曆金鑰不符')
    today = dt.datetime.now(c.TZ).date()
    sites = {s['id']: s for s in c.rows(con, 'SELECT * FROM sites')}
    workers = {w['id']: w['name'] for w in c.rows(con, 'SELECT id,name FROM workers')}
    events = []
    for o in c.rows(con, "SELECT * FROM orders WHERE status IN ('confirmed','completed') AND date BETWEEN ? AND ? AND start!='' AND end!=''",
                    ((today - dt.timedelta(days=45)).isoformat(), (today + dt.timedelta(days=200)).isoformat())):
        s = sites.get(o['site_id']) or {}
        names = [workers.get(a['worker_id'], '') for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],))]
        import decisions                       # 店長決定的日曆隱私（標題不放姓名、地址只到鄉鎮）
        events.append({'id': o['id'], 'title': decisions.calendar_title(con, s, o, names) + ('（已完工）' if o['status'] == 'completed' else ''),
                       'start': f"{o['date']}T{o['start']}:00+08:00", 'end': f"{o['date']}T{o['end']}:00+08:00",
                       'location': decisions.calendar_location(con, s), 'description': f"工單 {o['id']}\n服務：{s.get('service_type', '')}\n人數：{o['required_workers']}\n"
                       f"{('導航：' + s['navigation']) if s.get('navigation') else ''}\n（門禁、價格不放日曆，請看營運中樞）",
                       'version': o['version']})
    ix.set_setting(con, 'calendar_feed_last_pull', c.now())
    return {'generated_at': c.now(), 'window_from': (today - dt.timedelta(days=45)).isoformat(), 'events': events}

def to_json(x):
    return json.dumps(x, ensure_ascii=False, default=str)

# ───────────────────────── 合併重複（客戶、夥伴）─────────────────────────

def brand_known(con, cu):
    """確定的品牌：line_account；沒有就看聯絡管道是不是某個品牌的官方 LINE；都沒有（舊資料、LINE 群組）回空字串。"""
    import brands
    if brands.valid(cu.get('line_account') or ''):
        return cu['line_account']
    ch = cu.get('channel') or ''
    return brands.of_channel(con, ch) if ch and ch in {b['channel'] for b in brands.all_(con) if b['channel']} else ''

def p_force_cross(k, d, con=None):
    """其中一邊還沒有確定的品牌（舊資料）就允許合併。稽核 L9：聯絡管道是官方 LINE 的也算確定。"""
    if con is None:
        return not (k.get('line_account') and d.get('line_account'))
    return not (brand_known(con, k) and brand_known(con, d))

def merge_customers(con, keep, drop):
    """把 drop 客戶的一切（地點、工單、方案、帳單、收款、聯絡人、對話、需求、待辦）移到 keep，drop 的名稱留作別名。"""
    if keep == drop:
        raise ValueError('不能跟自己合併')
    k = c.get(con, 'customers', keep); d = c.get(con, 'customers', drop)
    import brands
    if brands.of_customer(k) != brands.of_customer(d) and not p_force_cross(k, d, con):      # 盤點：不同品牌的客戶不能合併（帳務、LINE、收款都分開）
        raise ValueError(f"「{k['name']}」和「{d['name']}」屬於不同品牌，不能合併；同一人在兩個品牌都有服務是正常的")
    for t in ('sites', 'orders', 'plans', 'invoices', 'receipts', 'contacts', 'chat_threads', 'inquiries', 'tasks', 'service_cases', 'conversation_notes', 'customer_aliases', 'quotes', 'line_users',
              'payment_requests', 'testimonials'):        # 稽核：以前漏收款單 → 被併的客戶有逾期款時，全站付款提醒每天出錯停擺
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            con.execute(f'UPDATE {t} SET customer_id=? WHERE customer_id=?', (keep, drop))
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shop_orders'").fetchone():
        con.execute("UPDATE shop_orders SET ref_id=? WHERE buyer='customer' AND ref_id=?", (keep, drop))
    con.execute("UPDATE line_users SET suggest_cid=? WHERE suggest_cid=?", (keep, drop))
    con.execute("UPDATE bind_requests SET ref_id=? WHERE kind='customer' AND ref_id=?", (keep, drop))
    con.execute("UPDATE line_bind SET ref_id=? WHERE kind='customer' AND ref_id=?", (keep, drop))          # 稽核 M6：合併前發的綁定碼照樣能用
    con.execute("UPDATE line_bind SET ref_id=SUBSTR(ref_id,1,LENGTH(ref_id)-?)||? WHERE kind='cust' AND ref_id LIKE ?", (len(drop), keep, '%|' + drop))
    for k_, v_ in con.execute("SELECT key,value FROM settings WHERE key=? OR key=? OR key LIKE ?", (f'pcf_none:{drop}', f'ent_asked:{drop}', f'hm_ok:{drop}:%')).fetchall():
        con.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (k_.replace(drop, keep, 1), v_))      # 沒加官方 LINE、問過開門、館別確認：不重問
        con.execute('DELETE FROM settings WHERE key=?', (k_,))
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='privacy_requests'").fetchone():
        con.execute('UPDATE privacy_requests SET customer_id=? WHERE customer_id=?', (keep, drop))
    for alias in (d['name'], d['line_name']):
        if alias and alias != k['name']:
            con.execute('INSERT OR IGNORE INTO customer_aliases(alias,customer_id) VALUES(?,?)', (alias, keep))
    merged = {f: k[f] or d[f] for f in ('phone', 'line_name', 'code', 'billing_rule')}
    merged['notes'] = '\n'.join(x for x in (k['notes'], f"【合併自 {d['name']}】{d['notes']}" if d['notes'] else f"【合併自 {d['name']}】") if x)
    if k['status'] != 'active' and d['status'] == 'active':
        merged['status'] = 'active'
    con.execute('UPDATE customers SET ' + ','.join(f'{f}=?' for f in merged) + ',version=version+1 WHERE id=?', (*merged.values(), keep))
    con.execute('DELETE FROM customers WHERE id=?', (drop,))
    c.audit(con, 'merge', 'customers', keep, d, {'merged_into': keep})
    return c.get(con, 'customers', keep)

def merge_workers(con, keep, drop):
    if keep == drop:
        raise ValueError('不能跟自己合併')
    k = c.get(con, 'workers', keep); d = c.get(con, 'workers', drop)
    if con.execute('SELECT 1 FROM users WHERE worker_id=?', (drop,)).fetchone():
        raise ValueError(f"{d['name']} 有 App 帳號，請保留有帳號的那位")
    for a in c.rows(con, 'SELECT * FROM assignments WHERE worker_id=?', (drop,)):
        if con.execute('SELECT 1 FROM assignments WHERE order_id=? AND worker_id=?', (a['order_id'], keep)).fetchone():
            if con.execute('SELECT 1 FROM settlement_lines WHERE assignment_id=?', (a['id'],)).fetchone() or con.execute('SELECT 1 FROM timesheets WHERE assignment_id=?', (a['id'],)).fetchone():
                raise ValueError('兩人在同一張工單都有實績，請人工處理')
            con.execute('DELETE FROM checkins WHERE assignment_id=?', (a['id'],)); con.execute('DELETE FROM assignments WHERE id=?', (a['id'],))
        else:
            con.execute('UPDATE assignments SET worker_id=? WHERE id=?', (keep, a['id']))
    for t in ('settlements', 'availability', 'line_users', 'line_groups',
              'worker_leaves', 'interviews', 'worker_files', 'supply_requests', 'site_reports', 'testimonials'):      # 稽核：以前漏請假記點、面試、作品集、證件
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() and                 'worker_id' in {r[1] for r in con.execute(f'PRAGMA table_info({t})')}:
            con.execute(f'UPDATE OR IGNORE {t} SET worker_id=? WHERE worker_id=?', (keep, drop))
    import automation, integrations as ix2
    bl = json.loads(ix2.setting(con, 'worker_blacklist') or '{}') if ix2.setting(con, 'worker_blacklist') else {}
    if drop in bl:                       # 黑名單／停派跟著人走（以前合併等於解除）
        bl[keep] = {**bl.pop(drop), 'name': k['name']}
        ix2.set_setting(con, 'worker_blacklist', json.dumps(bl, ensure_ascii=False))
    con.execute("UPDATE bind_requests SET ref_id=? WHERE kind='worker' AND ref_id=?", (keep, drop))
    for cid, pw in con.execute("SELECT id,preferred_workers FROM customers WHERE preferred_workers LIKE ?", (f'%{drop}%',)).fetchall():
        ids = list(dict.fromkeys(keep if x == drop else x for x in (pw or '').split(',') if x))
        con.execute('UPDATE customers SET preferred_workers=? WHERE id=?', (','.join(ids), cid))
    con.execute('DELETE FROM availability WHERE worker_id=?', (drop,))
    merged = {f: k[f] or d[f] for f in ('phone', 'regions', 'skills', 'availability', 'rate')}
    merged['notes'] = '；'.join(x for x in (k['notes'], f"合併自「{d['name']}」" + (f"：{d['notes']}" if d['notes'] else '')) if x)
    order = ['active', 'review', 'paused']
    merged['status'] = min(k['status'], d['status'], key=lambda s: order.index(s) if s in order else 9)
    con.execute('UPDATE workers SET ' + ','.join(f'{f}=?' for f in merged) + ',version=version+1 WHERE id=?', (*merged.values(), keep))
    con.execute('DELETE FROM workers WHERE id=?', (drop,))
    c.audit(con, 'merge', 'workers', keep, d, {'merged_into': keep})
    return c.get(con, 'workers', keep)

def duplicate_suggestions(con):
    """可能重複：名稱互相包含、或同長度只差一個字。給人確認後再合併。"""
    import re
    norm = lambda s: re.sub(r'[\s()\-－.、/·]', '', s or '').lower()
    out = []
    for table in ('customers', 'workers'):
        rows = c.rows(con, f'SELECT id,name FROM {table}')
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                x, y = norm(a['name']), norm(b['name'])
                if len(x) < 2 or len(y) < 2 or (x[-2:] in ('小姐', '先生') and y[-2:] in ('小姐', '先生')):
                    continue
                typo = len(x) == len(y) >= 3 and sum(p != q for p, q in zip(x, y)) == 1
                if x in y or y in x or typo:
                    out.append({'table': table, 'a': a, 'b': b, 'why': '只差一個字' if typo else '名稱包含'})
    return out
