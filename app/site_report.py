"""現場回報（負責人：夥伴在現場遇到狀況〔掃不完要加時、趕不上下一場、現場跟預期不一樣…〕要有回報系統，讓管理層知道並處理）。

夥伴：LINE 打「回報／加時／晚到／趕不上」、選單「今天的工作」卡上的「現場回報」、工作頁「現場有狀況？」→ 點選種類。
管理層：派單管理群收到可以點的卡（@標記）——
  需要加時    → 同意加時（工單結束時間直接延長，不會跳「新時間不行」）／照原時間收尾／通知業主／已處理
  下一場會晚到 → 通知下一場業主會晚到／今天不去・改天補／走請假流程（另派或通知業主）／已處理
  其他狀況    → 通知業主／已處理
  15 分鐘沒人處理 → 再提醒一次；中控台首頁「今天」那一列會亮紅燈直到處理。
要不要通知業主：店長決定（settings srep_owner，JSON {種類: auto|ask|none}）。還沒決定＝ask＝管理層按了才通知。
"""
import datetime as dt, json
import core as c, integrations as ix

KINDS = {                     # 種類：(圖示, 名稱, 卡片顏色)
    'extra': ('⏱', '需要加時', '#C47F00'),
    'late': ('🚗', '下一場會晚到', '#C47F00'),
    'scope': ('🧹', '現場跟預期不一樣', '#0A84FF'),
    'damage': ('⚠️', '有東西損壞', '#C62828'),
    'other': ('💬', '其他狀況', '#0A84FF'),
    'nophoto': ('📵', '這裡不能拍照', '#0A84FF'),        # 客戶03這類（以後各種狀況都能按）
}
NOTE_KINDS = ('scope', 'damage', 'other')          # 這幾種要夥伴打字說明
OWNER_RULES = {               # 店長的選項 → 每種要不要通知業主
    '全部都自動通知業主': dict.fromkeys(KINDS, 'auto'),
    '管理層看過按了才通知': dict.fromkeys(KINDS, 'ask'),
    '加時、晚到自動；其他看過再通知': {'extra': 'auto', 'late': 'auto', 'scope': 'ask', 'damage': 'ask', 'other': 'ask'},
    '都不要通知業主': dict.fromkeys(KINDS, 'none'),
}
# 店長 要決定的兩條規則（負責人：問店長）。回答記在 settings，現場回報卡上會顯示給管理層照做。
OVERTIME_TOPIC, OVERTIME_Q = '現場加時收費', ('夥伴在現場掃不完要加時（例：10/12 壯圍江小姐加 1～2 小時），工單是固定價，加時要不要另外跟業主收費？\n'
                                           '目前：工單金額不會自動變，要收要手動「調整金額」。夥伴時薪照實際時數算。')
OVERTIME_OPTS = ['照時薪加收（每30分鐘計）', '業主同意才收，金額我定', '不加收，算我們的', '看狀況，每次問我']
CARRY_TOPIC, CARRY_Q = '時數順延', ('夥伴當天沒做完、或前一場加時趕不上下一場（例：10/12 瑞安），沒做到的時數怎麼處理？\n'
                                  '目前：系統沒有時數帳，按「今天不去・改天補」後由管理層手動改期或加一場。')
CARRY_OPTS = ['記時數帳，下次服務自動補上', '管理層另約一場補做', '看客戶，每次問我', '不補，照實際時數算']
ASK_TOPIC = '現場回報通知業主'
ASK_Q = ('夥伴在現場回報狀況時，要不要通知業主？\n'
         '・需要加時：管理層按「同意加時」後，告訴業主延長到幾點\n'
         '・下一場會晚到：告訴下一場的業主大約晚多久\n'
         '・現場跟預期不一樣／有東西損壞／其他：告訴業主「主管會再跟您聯繫」（不講細節）\n'
         '還沒決定前：管理層看過、按「通知業主」才會通知。')

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS site_reports(id TEXT PRIMARY KEY, created_at TEXT, order_id TEXT, worker_id TEXT, kind TEXT, "
                "minutes INTEGER DEFAULT 0, owner_knows TEXT DEFAULT '', next_order_id TEXT DEFAULT '', note TEXT DEFAULT '', status TEXT DEFAULT 'open', "
                "handled_by TEXT DEFAULT '', handled_at TEXT DEFAULT '', resolution TEXT DEFAULT '', owner_notified TEXT DEFAULT '', reminded TEXT DEFAULT '')")

def owner_mode(con, kind):
    try:
        return json.loads(ix.setting(con, 'srep_owner') or '{}').get(kind) or 'ask'
    except ValueError:
        return 'ask'

def _hm(t):
    h, m = map(int, str(t)[:5].split(':')); return h * 60 + m

def _fmt(n):
    n = max(0, min(n, 23 * 60 + 59)); return f'{n // 60:02d}:{n % 60:02d}'

def _now():
    return dt.datetime.now(c.TZ)

def today_jobs(con, wid, day=None):
    day = day or _now().date().isoformat()
    return c.rows(con, "SELECT o.*,a.id aid,COALESCE(k.arrived_at,'') arrived_at,COALESCE(k.left_at,'') left_at FROM assignments a JOIN orders o ON o.id=a.order_id "
                       "LEFT JOIN checkins k ON k.assignment_id=a.id WHERE a.worker_id=? AND a.status='accepted' AND o.date=? AND " + __import__('board').GO_SQL + " ORDER BY o.start", (wid, day))

def current(con, wid):
    """夥伴「現在」在做的那場：已到場還沒離場 → 那場；否則最接近現在的那場（開工前 2 小時～結束後 2 小時）。"""
    jobs = today_jobs(con, wid)
    on = [j for j in jobs if j['arrived_at'] and not j['left_at']]
    if on:
        return on[-1]
    nm = _now().hour * 60 + _now().minute
    near = [j for j in jobs if _hm(j['start']) - 120 <= nm <= _hm(j['end']) + 120 and not j['left_at']]
    return min(near, key=lambda j: abs(_hm(j['start']) - nm)) if near else None

def next_job(con, wid, o):
    return next((j for j in today_jobs(con, wid, o['date']) if j['id'] != o['id'] and j['start'] >= o['start'] and not j['arrived_at']), None)

def _wlabel(con, oid):
    """夥伴看的場次名稱：照報班慣例只有地區＋類型（負責人 不出現人名、聯絡資料）。"""
    import line_bot as lb
    o, s, cu, crew, area = lb.order_ctx(con, oid)
    return f"{c.place(area)}・{s.get('service_type') or o['title'] or '服務'}"

def _on_job(con, oid, wid):
    return con.execute("SELECT 1 FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.order_id=? AND a.worker_id=? AND a.status='accepted' AND " + __import__('board').GO_SQL,
                       (oid, wid)).fetchone()

# ───────────── 夥伴：選種類 ─────────────
def start_card(con, wid, oid=None):
    """「現場回報」卡：這場要回報什麼（點選）。"""
    import line_bot as lb
    o = c.get(con, 'orders', oid) if oid else current(con, wid)
    if not o or not _on_job(con, o['id'], wid):
        return lb._txt('今天現在沒有進行中的工作 🙏 有其他事直接在這裡留言，派單同事會回覆你。')
    btn = lambda k: lb._btn(f'{KINDS[k][0]} {KINDS[k][1]}', data=f"a=srep&o={o['id']}&k={k}", primary=k == 'extra')
    return lb.flex('現場回報', lb._bubble(lb._header('現場回報', f"{lb.when(dict(o))}・{_wlabel(con, o['id'])}", '#C47F00'),
        [lb._t('現場遇到狀況，點一下就會通知管理層，處理好會在這裡告訴你。', wrap=True)], [btn(k) for k in KINDS],
        '要補照片：在工作頁按「拍異常照片」。'))

def options(con, wid, oid, kind, data):
    """點了種類之後的下一步（快速回覆）；回傳 (訊息, 是否已可建立)。"""
    import line_bot as lb
    q = lambda label, d, shown: {'type': 'action', 'action': {'type': 'postback', 'label': label[:20], 'data': d, 'displayText': shown}}
    base = f'a=srep&o={oid}&k={kind}'
    if kind == 'extra' and not data.get('m'):
        return [{'type': 'text', 'text': '大約還需要多久？👇', 'quickReply': {'items': [q(f'+{m} 分', f'{base}&m={m}', f'需要加時約 {m} 分') for m in (30, 60, 90, 120)]}}], False
    if kind == 'extra' and not data.get('own'):
        b2 = f"{base}&m={data['m']}"
        return [{'type': 'text', 'text': '業主知道要加時嗎？👇', 'quickReply': {'items': [q('業主知道、同意', f'{b2}&own=y', '業主知道、同意'), q('業主還不知道', f'{b2}&own=n', '業主還不知道')]}}], False
    if kind == 'late' and not data.get('m'):
        o = c.get(con, 'orders', oid); nx = next_job(con, wid, o)
        if not nx:
            return lb._txt('今天這場之後沒有排其他工作喔 🙏 如果是這場會晚到，請按工作卡上的「會晚到」。'), False
        return [{'type': 'text', 'text': f"下一場是 {nx['start']} {_wlabel(con, nx['id'])}，大約會晚多久？👇",          # 負責人 沒有「今天趕不上」——臨時不去＝臨時缺班
                 'quickReply': {'items': [q(f'晚 {m} 分', f'{base}&m={m}', f'下一場約晚 {m} 分') for m in (15, 30, 60, 90)]}}], False
    if kind in NOTE_KINDS and not data.get('note'):
        return lb._txt(f'請直接打字說明「{KINDS[kind][1]}」的狀況（下一則訊息會傳給管理層）✏️ 有照片可以到工作頁按「拍異常照片」。'), False
    return None, True

# ───────────── 建立回報 → 管理群卡片 ─────────────
def create(con, oid, wid, kind, minutes=0, own='', note='', by=''):
    import line_bot as lb, board
    ensure(con)
    if kind not in KINDS:
        raise ValueError('不認得的回報種類')
    if not _on_job(con, oid, wid):
        raise ValueError('這場你目前沒有排到（或還沒排定）')
    o = c.get(con, 'orders', oid); w = c.get(con, 'workers', wid); minutes = int(minutes or 0)
    since = (_now() - dt.timedelta(minutes=10)).isoformat(timespec='seconds')
    dup = con.execute("SELECT id FROM site_reports WHERE order_id=? AND worker_id=? AND kind=? AND minutes=? AND note=? AND created_at>?",
                      (oid, wid, kind, minutes, note[:500], since)).fetchone()
    if dup:
        return {'id': dup[0], 'message': '剛剛已經回報過了，管理層處理好會告訴你 🙏'}
    if kind == 'late' and minutes <= 0:
        raise ValueError('請選大約晚多久；真的去不了是臨時缺班，請直接打電話給管理層')
    nx = next_job(con, wid, o) if kind == 'late' else None
    if kind == 'late' and not nx:
        raise ValueError('今天這場之後沒有排其他工作')
    if kind == 'nophoto':                         # 這一場先放行（不用拍照、可以直接離場打卡），管理層再決定要不要設成整個案場
        note = note.strip() or '夥伴回報場內不能拍照'
        ix.set_setting(con, f'nophoto_job:{oid}', json.dumps({'by': w['name'], 'at': c.now(), 'note': note[:200]}, ensure_ascii=False))
    rid = c.uid('srp')
    con.execute('INSERT INTO site_reports(id,created_at,order_id,worker_id,kind,minutes,owner_knows,next_order_id,note) VALUES(?,?,?,?,?,?,?,?,?)',
                (rid, c.now(), oid, wid, kind, minutes, own, nx['id'] if nx else '', note[:500]))
    r = get(con, rid)
    board.log(con, oid, 'site_report', f"{w['name']}：{summary(con, r)}")
    if kind == 'late' and minutes > 0:            # 下一場的「到場」進度照晚到時間算（首頁即時狀況、晚到提醒都認得這個格式）
        board.log(con, nx['id'], 'worker_late', f"{w['name']} 約晚 {minutes} 分")
    auto = ''
    if owner_mode(con, kind) == 'auto' and kind not in ('extra', 'nophoto') and not (kind == 'late' and minutes < 0):      # 不能拍照：不用通知業主
        auto = notify_owner(con, rid, '系統（自動）')        # 加時等管理層同意延長後才通知；今天趕不上要管理層決定
    send_card(con, rid, auto)
    c.audit(con, 'site_report', 'orders', oid, after={'kind': kind, 'minutes': minutes, 'by': by or w['name']})
    if kind == 'nophoto':
        return {'id': rid, 'message': '收到 🙏 這一場不用拍照了，做完直接離場打卡就好；已通知管理層。'}
    return {'id': rid, 'message': '收到 🙏 已通知管理層' + ('，也已告訴業主' if auto.startswith('已') else '') + '，處理好會在這裡告訴你。'}

def get(con, rid):
    r = con.execute('SELECT * FROM site_reports WHERE id=?', (rid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆現場回報')
    return dict(r)

def summary(con, r):
    k = r['kind']; m = r['minutes']
    if k == 'extra':
        o = c.get(con, 'orders', r['order_id'])
        return f"需要加時約 {m} 分（到 {_fmt(_hm(o['end']) + m)}），" + ('業主知道、同意' if r['owner_knows'] == 'y' else '業主還不知道')
    if k == 'late':
        nx = c.get(con, 'orders', r['next_order_id'])
        return f"做完趕不上下一場 {nx['start']}" + ('，今天去不了' if m < 0 else f'，約晚 {m} 分')
    return f"{KINDS[k][1]}：{r['note'] or '（沒寫說明）'}"

PHOTO_WORDS = ('拍照', '拍攝', '照相', '拍照片', '不能拍')

def _photo_issue(con, r, o):
    """回報說不能拍照、案場還沒設「場內不能拍照」→ 卡片給「📵 這個案場不能拍照」按鈕。"""
    if not o['site_id'] or not (r['kind'] == 'nophoto' or (r['kind'] == 'other' and any(w in (r['note'] or '') for w in PHOTO_WORDS))):
        return False
    return not (con.execute('SELECT no_photo FROM sites WHERE id=?', (o['site_id'],)).fetchone() or [0])[0]

def _same_issue(kind, a, b):
    """同一件事：加時／晚到同種就算；其他狀況、範圍、損壞要內容一樣，或兩筆都在說不能拍照。"""
    if kind in ('extra', 'late'):
        return True
    a, b = (a or '').strip(), (b or '').strip()
    return a == b or (any(w in a for w in PHOTO_WORDS) and any(w in b for w in PHOTO_WORDS))

def _handled_before(con, r):
    """同一張工單、同一位夥伴、同一種回報，之前已經有人處理過的（最近一筆）。"""
    x = con.execute("SELECT handled_by,handled_at,created_at FROM site_reports WHERE order_id=? AND worker_id=? AND kind=? AND status='handled' AND id!=? AND created_at<? "
                    "ORDER BY handled_at DESC LIMIT 1", (r['order_id'], r['worker_id'], r['kind'], r['id'], r['created_at'])).fetchone()
    return dict(x) if x else None

def actions(con, r):
    """中控台「管理群待處理」的按鈕（跟派單管理群那張卡一樣）。[[v, 顯示, 樣式]]"""
    k, m = r['kind'], r['minutes']; mode = owner_mode(con, k); out = []
    o = c.get(con, 'orders', r['order_id'])
    if k == 'extra':
        out += [['ok', f"同意加時到 {_fmt(_hm(o['end']) + m)}", 'primary'], ['no', '照原時間收尾', 'sec']]
    elif k == 'late':
        if m > 0 and mode != 'none' and not r['owner_notified']:
            out.append(['notify', f'通知下一場業主晚{m}分', 'primary'])
        out.append(['leave', '改派別人（走請假流程）', 'sec'])
    else:
        if _photo_issue(con, r, o):
            out.append(['nophoto', '📵 這個案場不能拍照', 'primary'])
    if mode == 'ask' and not r['owner_notified'] and k != 'nophoto':
        out.append(['own', '通知業主', 'sec'])
    out.append(['done', '已處理', 'gray'])
    return out

def send_card(con, rid, auto=''):
    import line_bot as lb
    r = get(con, rid); o = c.get(con, 'orders', r['order_id']); w = c.get(con, 'workers', r['worker_id'])
    k = r['kind']; ic, name, color = KINDS[k]; mode = owner_mode(con, k); m = r['minutes']
    rows = lb.order_people(con, o['id']) + [('回報', w['name']), ('工作', lb.when(dict(o)))]
    btn = lambda label, v, primary=False: lb._btn(label, data=f'a=srx&r={rid}&v={v}', primary=primary)
    btns = []
    if k == 'extra':
        new_end = _fmt(_hm(o['end']) + m)
        rows += [('需要', f'加時約 {m} 分（{o["end"]} → {new_end}）'), ('業主', '知道、同意' if r['owner_knows'] == 'y' else '還不知道')]
        nx = next_job(con, r['worker_id'], o)
        if nx and nx['start'] < new_end:
            rows.append(('⚠️ 衝突', f"延長會壓到他下一場 {nx['start']}（{lb.order_ctx(con, nx['id'])[1].get('name') or nx['title']}）"))
        rule = ix.setting(con, 'srep_overtime')
        rows.append(('費用', f'店長規則：{rule}' + ('（要收請到工單「調整金額」）' if '不加收' not in rule else '') if rule else '工單金額不會自動變；要加收請到工單「調整金額」'))
        btns = [btn(f'同意加時到 {new_end}', 'ok', True), btn('照原時間收尾', 'no')]
    elif k == 'late':
        nx = c.get(con, 'orders', r['next_order_id']); s2 = lb.order_ctx(con, nx['id'])[1]
        rows += [('下一場', f"{nx['start']} {s2.get('name') or nx['title']}"), ('狀況', '今天趕不上' if m < 0 else f'約晚 {m} 分')]
        if m > 0 and mode != 'none' and not auto:
            btns.append(btn(f'通知下一場業主晚{m}分', 'notify', True))
        btns += [btn('改派別人（走請假流程）', 'leave')]
    else:
        rows.append(('狀況', r['note'] or '（沒寫說明）'))
        if _photo_issue(con, r, o):                   # 客戶03：夥伴說不能拍照 → 一鍵設定（不然照片沒拍不能離場，夥伴會一直回報）
            btns.append(btn('📵 這個案場不能拍照', 'nophoto', True))
        if mode == 'ask' and k != 'nophoto':
            btns.append(btn('通知業主', 'own', not _photo_issue(con, r, o)))
    hb = _handled_before(con, r)
    if hb:                                            # 處理過又回報同一件 → 可能還沒解決
        rows.append(('⚠️ 又回報', f"{hb['created_at'][11:16]} 那筆 {hb['handled_by']} {hb['handled_at'][11:16]} 已處理過，夥伴又回報同一類（可能還沒解決）"))
    if k == 'extra' and mode == 'ask':
        btns.append(btn('通知業主', 'own'))
    btns.append(btn('已處理', 'done'))
    rows.append(('業主通知', auto or {'auto': '管理層處理後自動通知' if k == 'extra' else '自動通知', 'ask': '要通知請按「通知業主」', 'none': '設定為不通知業主'}[mode]))
    m2 = lb.flex(f'{ic} 現場回報：{w["name"]} {name}', lb._bubble(lb._header('現場回報', f'{ic} {name}', color), [lb._row(a, b) for a, b in rows],
                 btns + [lb._btn('打開工單', uri=f"{lb.BASE_URL}/#o/{o['id']}")]))
    g = lb.admin_group(con)
    if g:
        lb.queue(con, lb.WORK_ACCOUNT, g, 'site_report', m2[0]['altText'], [lb.ping(con, '現場回報，請處理 👇')] + m2, o['id'])

# ───────────── 管理層：處理 ─────────────
def act(con, rid, v, by):
    import line_bot as lb, board
    r = get(con, rid); o = c.get(con, 'orders', r['order_id']); w = c.get(con, 'workers', r['worker_id']); k = r['kind']
    if v == 'own':
        return notify_owner(con, rid, by)
    if r['status'] == 'handled' and v != 'nophoto':     # 稽核 M5：📵 已處理的單也能按（同一件先被一起結掉）
        return f"這筆已由 {r['handled_by']} 處理：{r['resolution']}"
    tell = lambda text: lb.to_worker(con, r['worker_id'], 'srep_reply', '管理層回覆你的現場回報', lb._txt(text), o['id'])
    extra = ''; auto_owner = False
    if v == 'ok' and k == 'extra':
        if not board.goes(o):
            raise ValueError('這場已經不是排定狀態，不能延長')
        old, new = o['end'], _fmt(_hm(o['end']) + r['minutes'])
        con.execute('UPDATE orders SET end=?,version=version+1 WHERE id=?', (new, o['id']))      # 不走一般改期通知（避免夥伴收到「新時間不行」）
        board.log(con, o['id'], 'extend', f'{old}→{new}（{by} 同意加時）')
        c.audit(con, 'order_extend', 'orders', o['id'], before={'end': old}, after={'end': new, 'by': by})
        res = f'同意加時，結束時間 {old} → {new}'
        tell(f"管理層同意加時 👍 今天做到 {new}（工單已改）。做完一樣拍完成照片、打離場卡。")
        for x in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted' AND worker_id!=?", (o['id'], r['worker_id'])):     # 同組的人也要知道
            lb.to_worker(con, x['worker_id'], 'srep_reply', '今天的工作延長了', lb._txt(f"管理層同意加時：今天這場做到 {new}（{w['name']} 回報掃不完）。做完一樣拍完成照片、打離場卡 🙏"), o['id'])
        auto_owner = owner_mode(con, k) == 'auto'          # 先結案再通知 → 業主收到的是「延長到幾點」，不是暫定文字
    elif v == 'no' and k == 'extra':
        res = '照原時間收尾'
        if r['owner_notified']:          # 每日檢查：已經告訴業主要加時 → 更正
            acc2, uid2 = lb.customer_target(con, o['customer_id'])
            if uid2:
                lb.queue(con, acc2, uid2, 'srep_c', '服務時間更正', lb._txt(f"您好，更正一下：今天的服務會照原定時間 {o['end']} 完成，不加時了，謝謝您 🙏"), o['id'])
        tell(f"管理層回覆：請照原定時間 {o['end']} 收尾 🙏 沒做完的地方拍照、在工作頁寫一下，管理層會再跟業主安排。")
    elif v == 'notify' and k == 'late':
        res = '已通知下一場業主會晚到'; extra = '；' + notify_owner(con, rid, by)
        tell('管理層已告訴下一場的業主你會晚一點到，路上小心 🙏')
    elif v == 'skip':
        raise ValueError('「今天不去・改天補」已取消（臨時不去＝臨時缺班）：要換人請按「改派別人（走請假流程）」')
    elif v == 'leave' and k == 'late':
        import leave
        leave.request(con, r['worker_id'], r['next_order_id'], '前一場加時，趕不上這場')
        res = '下一場走請假流程（另派／通知業主）'; extra = '；請在請假卡選怎麼處理'
    elif v == 'done':
        res = '已處理'
        tell(f"管理層已處理你回報的「{KINDS[k][1]}」，謝謝你 🙏")
    elif v == 'nophoto':                              # 客戶03：案場設「場內不能拍照」
        if not o['site_id']:
            raise ValueError('這張工單沒有案場')
        s = c.get(con, 'sites', o['site_id'])
        if not s.get('no_photo'):
            c.CTX.actor = f'{by}（現場回報）'
            c.save(con, 'sites', {'id': s['id'], 'version': s['version'], 'no_photo': 1})
        res = '設成場內不能拍照'
        extra = f"（{s['name']}：夥伴不用拍、可以直接離場打卡、薪資照常，業主收沒照片的完工卡）"
        for x in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)):
            lb.to_worker(con, x['worker_id'], 'srep_reply', '這個案場不用拍照', lb._txt(f"管理層已設定「{s['name']}」場內不能拍照 👍 不用拍照片，做完直接在工作頁按「完工・離場打卡」就好（薪資照常）。"), o['id'])
    else:
        raise ValueError('這個按鈕不適用這筆回報')
    con.execute("UPDATE site_reports SET status='handled',handled_by=?,handled_at=?,resolution=? WHERE id=?", (by, c.now(), res, rid))
    if v in ('done', 'nophoto'):                      # 「我明明就有處理」：同一張工單、同一位夥伴、同一種回報還開著的 → 同一件，一起結
        sibs = [x for x in c.rows(con, "SELECT id,note FROM site_reports WHERE order_id=? AND worker_id=? AND kind=? AND status='open' AND id!=?", (r['order_id'], r['worker_id'], k, rid))
                if _same_issue(k, r['note'], x['note'])]          # 稽核 M5：只結同一件（「鑰匙不見」不會跟「不能拍照」一起結）
        for x in sibs:
            con.execute("UPDATE site_reports SET status='handled',handled_by=?,handled_at=?,resolution=? WHERE id=?", (by, c.now(), f'{res}（同一件，一起處理）', x['id']))
        n2 = len(sibs)
        if n2:
            extra += f'（同一件的另外 {n2} 筆也一起結了）'
    board.log(con, o['id'], 'site_report_done', f'{by}：{res}')
    if v == 'ok' and k == 'extra' and auto_owner:
        extra = '；' + notify_owner(con, rid, '系統（自動）')
    return f"✅ {w['name']} 的現場回報：{res}{extra}"

def notify_owner(con, rid, by):
    """通知業主（加時／其他：這場的業主；晚到：下一場的業主）。業主沒綁 LINE → 回傳請打電話。"""
    import line_bot as lb, brands
    r = get(con, rid); k = r['kind']
    if r['owner_notified']:
        return f"業主已經通知過了（{r['owner_notified'][11:16]}）"
    oid = r['next_order_id'] if k == 'late' else r['order_id']
    o = c.get(con, 'orders', oid); cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    acc, uid = lb.customer_target(con, o['customer_id'])
    if not uid:
        return f"業主沒綁 LINE，請打電話告知（{cu.get('name') or '業主'} {cu.get('phone') or '沒有電話'}）"
    bn = brands.name(acc)
    if k == 'extra':
        txt = (f"您好 😊 今天的服務需要多一點時間把細節做好，服務時間延長到 {o['end']}，有任何問題直接在這裡回覆我們 🙏（{bn}）"
               if r['status'] == 'handled' and '同意加時' in r['resolution'] else
               f"您好 😊 今天的服務現場需要多一點時間，夥伴預計加時約 {r['minutes']} 分鐘，主管會再跟您確認，有問題直接在這裡回覆我們 🙏（{bn}）")
    elif k == 'late':
        if r['minutes'] < 0:
            return '這筆是「今天去不了」：請按「改派別人（走請假流程）」，再由管理層跟業主說明'
        txt = f"您好，今天 {o['start']} 的服務人員前一場耽擱，大約會晚 {r['minutes']} 分鐘到，很抱歉 🙏 到場時會再通知您。（{bn}）"
    elif k == 'damage':
        txt = f"您好，今天的服務人員在現場發現有物品損壞的狀況，已回報主管，主管會盡快跟您聯繫說明 🙏（{bn}）"
    elif k == 'scope':
        txt = f"您好 😊 服務人員回報今天現場的狀況跟原本預估的不太一樣，主管會再跟您聯繫確認，謝謝您 🙏（{bn}）"
    else:
        txt = f"您好 😊 服務人員今天在現場回報了一個狀況，主管會再跟您聯繫確認，謝謝您 🙏（{bn}）"
    lb.queue(con, acc, uid, 'srep_c', '服務現場通知', lb._txt(txt), oid)
    con.execute('UPDATE site_reports SET owner_notified=? WHERE id=?', (c.now(), rid))
    import board; board.log(con, oid, 'site_report_owner', f'{by} 通知業主')
    return '已通知業主'

# ───────────── 背景：沒人處理再提醒；首頁即時狀況 ─────────────
def remind(con):
    import line_bot as lb
    ensure(con)
    old = (_now() - dt.timedelta(minutes=15)).isoformat(timespec='seconds')
    for r in c.rows(con, "SELECT r.* FROM site_reports r JOIN orders o ON o.id=r.order_id WHERE r.status='open' AND r.reminded='' AND o.status NOT IN ('cancelled','archived') "
                         "AND r.created_at<? AND r.created_at>?", (old, (_now() - dt.timedelta(hours=12)).isoformat(timespec='seconds'))):
        w = c.get(con, 'workers', r['worker_id'])
        hb = _handled_before(con, r)
        lb.admin_notify(con, f"⏰ 現場回報還沒人處理：{w['name']}", [('回報', f"{r['created_at'][11:16]} {summary(con, r)}")]
                        + ([('說明', f"這是 {r['created_at'][11:16]} 新的一筆；之前 {hb['created_at'][11:16]} 那筆 {hb['handled_by']} 已處理過，夥伴又回報（可能還沒解決）")] if hb else [])
                        + [('處理', f"請按 {r['created_at'][11:16]} 那張「現場回報」卡的按鈕（按一次，同一件的會一起結）")], '', '#C62828', oid=r['order_id'], tag='現場回報 15 分鐘沒人處理')
        con.execute("UPDATE site_reports SET reminded=? WHERE id=?", (c.now(), r['id']))

def for_order(con, oid):
    ensure(con)
    return c.rows(con, 'SELECT * FROM site_reports WHERE order_id=? ORDER BY created_at', (oid,))

def ask_rule(con):
    """請店長決定「現場回報要不要通知業主」（只問一次；回答由 decisions.apply 套用）。"""
    import line_bot as lb
    if ix.setting(con, f'ask:{ASK_TOPIC}') or ix.setting(con, f'answer:{ASK_TOPIC}'):
        return 0
    return lb.ask_staff(con, ASK_TOPIC, ASK_Q, list(OWNER_RULES))

def ask_money(con):
    """請店長決定「加時收費」與「時數順延」（各問一次）。"""
    import line_bot as lb
    n = 0
    for topic, q, opts in ((OVERTIME_TOPIC, OVERTIME_Q, OVERTIME_OPTS), (CARRY_TOPIC, CARRY_Q, CARRY_OPTS)):
        if not (ix.setting(con, f'ask:{topic}') or ix.setting(con, f'answer:{topic}')):
            n += lb.ask_staff(con, topic, q, opts)
    return n

def apply_money(con, topic, ans):
    """店長的回答（點選或自己打字）→ 記下來，現場回報卡顯示給管理層照做。"""
    key = {OVERTIME_TOPIC: 'srep_overtime', CARRY_TOPIC: 'srep_carry'}[topic]
    ix.set_setting(con, key, ans.strip()[:200])
    return f'已記下「{topic}」：{ans.strip()[:200]}（之後的現場回報卡會顯示給管理層照做）'

def apply_rule(con, ans):
    rule = OWNER_RULES.get(ans.strip())
    if not rule:
        return ''
    ix.set_setting(con, 'srep_owner', json.dumps(rule, ensure_ascii=False))
    return f'已套用現場回報通知業主規則：{ans.strip()}'
