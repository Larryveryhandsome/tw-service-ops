"""開門流程（今天夥伴到現場進不去 → 打給店長 → 店長打給業主 → 業主才去開門。這要自動化）

① 客戶在 LINE 按「確認這個時間」→ 馬上問「服務當天怎麼進門？」（同一個案場確認過一次就記住，定期客戶不用每次問）
   我在家開門／密碼鎖・鑰匙（打字說明）／管理員・家人開門（打字留稱呼電話）
② 客戶選好 → 店長收到「開門方式確認」→ 確認（或打字修改）
③ 前一天 18:00：業主「明天 X 點服務人員會到，需要您開門」→ 按「我知道了，明天會過去開門 ✅」→ 夥伴同步收到「業主已確認會來開門」
   業主前一天還沒按 → 服務當天開工前 2 小時再提醒一次
④ 服務當天：夥伴到場打卡（或按「請業主開門 🚪」）→ 業主收到「服務人員到門口了」＋「我來開門」→ 夥伴收到「業主說馬上來」
⑤ 業主 5 分鐘沒回應 → 自動通知派單管理群打電話（附業主與現場聯絡人電話），也告訴夥伴「已請派單群打給業主」
"""
import datetime as dt
import re
import core as c
import integrations as ix

MODES = {'home': '業主親自開門', 'bell': '按門鈴／有人在，可直接進入', 'self': '密碼鎖／鑰匙，自行進門', 'contact': '管理室／管理員開門'}
NO_DOOR = ('bell', 'self', 'contact')     # 這幾種到場打卡＝已進入，不發開門訊息給業主、夥伴也不用按「已進門」
WAIT_MIN = 5          # 請業主開門後幾分鐘沒回應 → 管理群打電話

def ensure(con):
    cols = {r[1] for r in con.execute('PRAGMA table_info(sites)')}
    for k in ('entry_mode', 'entry_ok'):
        if k not in cols:
            con.execute(f"ALTER TABLE sites ADD COLUMN {k} TEXT DEFAULT ''")
    cols = {r[1] for r in con.execute('PRAGMA table_info(orders)')}
    for k in ('door_ack', 'door_req', 'door_open', 'entry_mode'):          # entry_mode：這張工單另外設的開門方式（空白＝沿用案場）
        if k not in cols:
            con.execute(f"ALTER TABLE orders ADD COLUMN {k} TEXT DEFAULT ''")

def site_of(con, oid):
    ensure(con)
    o = c.get(con, 'orders', oid)
    s = dict(con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'],)).fetchone() or {}) if o['site_id'] else {}
    return o, s

def mode_of(o, s):
    """這張工單的開門方式：工單有另外設就用工單的，沒有就用案場的。"""
    try:
        om = (o['entry_mode'] or '') if o else ''
    except (KeyError, IndexError):
        om = ''
    return om or ((s or {}).get('entry_mode') or '')

def mode_text(s, o=None):
    return MODES.get(mode_of(o, s), '還沒確認')

# ① 問客戶（客服 同一個業主只要點選一次，不用每次問）
def asked_before(con, cid):
    """這位業主以前被問過開門方式了嗎（任何一個地點、任何一次）。"""
    return bool(cid and (ix.setting(con, f'ent_asked:{cid}') or con.execute(
        "SELECT 1 FROM line_outbox x JOIN orders y ON y.id=x.order_id WHERE y.customer_id=? AND x.kind='entry_ask' AND x.status IN ('queued','sent','held') LIMIT 1", (cid,)).fetchone()))

def sibling(con, cid, sid):
    """同一位業主、可以沿用的其他地點：只有「業主在家開門」（稽核：密碼鎖、管理員開門每一館不一樣，抄過去夥伴會輸錯密碼，也會把別館密碼給不相關的人看）。"""
    if not cid:
        return None
    r = con.execute("SELECT * FROM sites WHERE customer_id=? AND id!=? AND entry_mode='home' AND COALESCE(entry_notes,'') NOT LIKE '%沿用同業主%' "
                    "ORDER BY (COALESCE(entry_ok,'')!='') DESC, version DESC LIMIT 1", (cid, sid)).fetchone()      # 只從業主自己答的地點沿用（不從「沿用來的」再沿用）
    return dict(r) if r else None

def reset_inherited(con, s, m):
    """這個地點的開門方式改了、不再是「在家開門」→ 從它沿用過去的其他地點清掉，重新走問／補（稽核 A2）。"""
    if m == 'home' or not s.get('customer_id'):
        return 0
    n = 0
    for x in c.rows(con, "SELECT * FROM sites WHERE customer_id=? AND id!=? AND entry_mode='home' AND COALESCE(entry_notes,'') LIKE ?", (s['customer_id'], s['id'], f"%沿用同業主「{s.get('name') or '其他地點'}」%")):
        notes = '\n'.join(y for y in (x.get('entry_notes') or '').splitlines() if not y.startswith('沿用同業主'))
        con.execute("UPDATE sites SET entry_mode='',entry_ok='',entry_notes=?,version=version+1 WHERE id=?", (notes, x['id']))
        c.audit(con, 'entry_mode', 'sites', x['id'], after={'mode': '', 'by': f"來源地點改成{MODES.get(m, m)}，取消沿用"}); n += 1
    return n

def inherit(con, s, cid):
    """這個地點還不知道怎麼進門、同業主其他地點答過 → 沿用（方式＋業主說明），不再問業主。回傳 True＝已沿用。"""
    sib = sibling(con, cid, s['id'])
    if not sib:
        return False
    note = f"沿用同業主「{sib.get('name') or '其他地點'}」的開門方式（{MODES.get(sib['entry_mode'], sib['entry_mode'])}）"
    keep = (s.get('entry_notes') or '').strip()
    con.execute("UPDATE sites SET entry_mode=?,entry_notes=?,version=version+1 WHERE id=? AND COALESCE(entry_mode,'')=''",
                (sib['entry_mode'], (keep + '\n' if keep else '') + note[:200], s['id']))
    c.audit(con, 'entry_mode', 'sites', s['id'], after={'mode': sib['entry_mode'], 'by': '沿用同業主其他地點', 'from': sib['id']})
    return True

def inherit_due(con, days=7):
    """每分鐘：接下來 N 天有服務、還不知道怎麼進門、同業主其他地點是「在家開門」→ 直接沿用（業主有沒有綁 LINE 都一樣）。"""
    ensure(con)
    today = dt.datetime.now(c.TZ).date()
    n = 0
    for s in c.rows(con, "SELECT DISTINCT s.* FROM sites s JOIN orders o ON o.site_id=s.id WHERE COALESCE(s.entry_mode,'')='' AND s.customer_id IS NOT NULL "
                         "AND o.status IN ('review','draft','confirmed') AND o.date BETWEEN ? AND ?", (today.isoformat(), (today + dt.timedelta(days=days)).isoformat())):
        n += inherit(con, s, s['customer_id'])
    return n

def fill_push(con):
    """稽核 A5：變成「開門方式待補」、服務在明天以前的案場 → 馬上送一張補開門卡到派單管理群（每個案場只送一次；以前要等隔天 10:30 的待辦卡）。"""
    import line_bot as lb
    now = dt.datetime.now(c.TZ)
    g = lb.admin_group(con)
    if not g or not ('08:00' <= now.strftime('%H:%M') < '22:00') or ix.setting(con, 'standing_push') != 'on':
        return 0                          # 開門方式是常態的事 → 不一張張推，每天 10:30 一則請管理層到中控台補（standing_push=on 才恢復）
    n = 0
    for o in fill_due(con, days=1):
        k = f"fill_pushed:{o['site_id']}:{o['date']}"
        if ix.setting(con, k):
            continue
        ix.set_setting(con, k, c.now())
        m = card_fill(con, o)
        lb.queue(con, lb.WORK_ACCOUNT, g, 'ent_fill', m[0]['altText'], [lb.ping(con, f"🚪 {lb.md(o['date'])} {o['start']} 的案場還不知道怎麼進門（{'業主沒綁 LINE' if not o.get('line_bound') else '問了業主還沒回'}），誰知道就按一下補上，或打電話問業主 🙏")] + m, o['id'])
        n += 1
    return n

def need_ask(con, oid):
    """要不要用 LINE 問業主怎麼進門：這個地點已知 → 不問；同業主其他地點答過 → 沿用、不問；
    這位業主問過了（沒回）→ 不再問，改由管理層補（fill_due／「開門方式待補」）；都沒有才問（每位業主只問一次）。"""
    o, s = site_of(con, oid)
    if not s or s.get('entry_mode') or o.get('entry_mode') or not o['customer_id']:        # 稽核 F10：工單自己設了開門方式就不問
        return False
    if inherit(con, s, o['customer_id']):
        return False
    return not asked_before(con, o['customer_id'])

def card_ask(con, oid, mark=True):
    import line_bot as lb
    o, s = site_of(con, oid)
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    if mark and cu and lb.mode(con, lb.brand_of(cu)) == 'on':      # 記下問過這位業主；LINE 測試／關閉模式沒真的送出，不算問過（稽核 A4）
        ix.set_setting(con, f"ent_asked:{cu['id']}", c.now())
    return lb.flex('服務當天怎麼進門？請點選 🚪', lb._bubble(lb._header('服務當天怎麼進門？🚪', lb.when(dict(o)), brand=lb.brand_of(cu)),
        [lb._t('先跟您確認開門方式，服務人員到了就不用在門口等。只要選一次，之後每次服務都照這個方式；有變動隨時在這裡留言。', wrap=True)],
        [lb._btn('我（或家人）親自開門', data=f'a=ent&o={oid}&m=home', primary=True), lb._btn('店裡／家裡有人，按門鈴就好', data=f'a=ent&o={oid}&m=bell'),
         lb._btn('密碼鎖／鑰匙，自行進門', data=f'a=ent&o={oid}&m=self'), lb._btn('大樓管理室／管理員開門', data=f'a=ent&o={oid}&m=contact')], '選好後主管會再確認；有變動隨時在這裡留言。'))

def customer_pick(con, acc, uid, oid, m):
    """客戶點選開門方式。回傳要回覆的訊息。"""
    import line_bot as lb
    o, s = site_of(con, oid)
    if m not in MODES or not s:
        return lb._txt('收到，專人會跟您確認開門方式 🙏')
    notes = '\n'.join(x for x in (s.get('entry_notes') or '').splitlines() if not x.startswith('沿用同業主'))      # 業主自己選了 → 拿掉「沿用」那行
    con.execute("UPDATE sites SET entry_mode=?,entry_ok='',entry_notes=?,version=version+1 WHERE id=?", (m, notes, s['id']))
    c.audit(con, 'entry_mode', 'sites', s['id'], after={'mode': m, 'by': '客戶 LINE'})
    reset_inherited(con, s, m)
    if s.get('entry_mode') and s.get('entry_mode') != m:
        _tell_workers(con, s, s['id'])          # 稽核：業主改了開門方式 → 已排的夥伴要知道
    if m == 'self':
        ix.set_setting(con, f'ent_wait:{uid}', oid)
        return lb._txt('好的 👍 請直接打字說明怎麼進門（例如：大門密碼、鑰匙放哪裡）。只會給當天接案的服務人員看，用完建議改密碼。')
    if m == 'contact':
        ix.set_setting(con, f'ent_wait:{uid}', oid)
        return lb._txt('好的 👍 請打字留下開門的人的稱呼和電話（例如：管理員王先生 0900-000-000），我們前一天會先聯絡。')
    to_shiqing(con, oid)
    if m == 'bell':
        return lb._txt('好的 👍 服務人員到了會按門鈴，到場打卡就開始工作，不會再另外請您開門。')
    return lb._txt(f"好的 👍 服務前一天會提醒您 {lb.md(o['date'])} {o['start']} 要開門；服務人員到門口時也會在這裡通知您。")

def _tell_workers(con, before, sid):
    """開門方式／進場說明改了 → 通知接下來已排這個案場的夥伴（跟中控台改案場一樣，leave.site_changed）。"""
    import leave
    after = dict(con.execute('SELECT * FROM sites WHERE id=?', (sid,)).fetchone() or {})
    if after:
        leave.site_changed(con, before, after)

def customer_detail(con, uid, oid, text):
    """客戶打字說明（密碼／鑰匙／開門的人）→ 存到案場進場方式（只給接案夥伴看）→ 店長確認。"""
    import line_bot as lb
    o, s = site_of(con, oid)
    note = text.strip()[:200]
    keep = '\n'.join(x for x in (s.get('entry_notes') or '').splitlines() if not x.startswith(('客戶說明（', '沿用同業主')))      # 稽核：新的密碼取代舊的（同事寫的保留）
    con.execute("UPDATE sites SET entry_notes=?,version=version+1 WHERE id=?", ((keep + '\n' if keep else '') + f'客戶說明（{c.now()[5:10]}）：{note}', s['id']))
    _tell_workers(con, s, s['id'])
    ph = re.search(r'0\d[\d\- ]{7,11}\d', note)
    if s.get('entry_mode') == 'contact' and ph and o['customer_id'] and not con.execute('SELECT 1 FROM contacts WHERE customer_id=? AND phone=?', (o['customer_id'], ph.group(0))).fetchone():
        who = note[:ph.start()].strip(' ：:，,') or '開門聯絡人'
        c.save(con, 'contacts', {'customer_id': o['customer_id'], 'name': who[:30], 'role': '大樓管理員' if '管理' in who else '家人', 'phone': ph.group(0), 'notes': '客戶在 LINE 留的開門聯絡人'})
    to_shiqing(con, oid)
    return lb._txt('收到 🙏 已記下，只會給當天接案的服務人員看。主管確認後就完成了。')

# ② 店長確認
def card_confirm(con, oid):
    import line_bot as lb
    o, s = site_of(con, oid)
    cu = dict(con.execute('SELECT name,phone FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    return lb.flex(f"開門方式待確認：{cu.get('name', '')}", lb._bubble(lb._header('🚪 開門方式待確認', cu.get('name') or s.get('name') or '', '#0A84FF'),
        [lb._row('服務', lb.when(dict(o))), lb._row('地點', s.get('name') or '—'), lb._row('方式', mode_text(s)),
         lb._row('說明', (s.get('entry_notes') or '—')[-120:]), lb._row('電話', cu.get('phone') or '—')],
        [lb._btn('確認無誤', data=f"a=ent_ok&s={s.get('id', '')}&o={oid}", primary=True), lb._btn('要修改（打字）', data=f"a=ent_edit&s={s.get('id', '')}&o={oid}")],
        '確認後夥伴的工作卡會顯示開門方式；前一天會自動請業主按「我知道了，會來開門」。'))

def to_shiqing(con, oid):
    import line_bot as lb, reviews
    m = card_confirm(con, oid)
    lb.to_managers(con, 'entry_confirm', m[0]['altText'], m, oid)          # 送管理群 @店長

def staff_ok(con, sid, by):
    ensure(con)
    s = con.execute('SELECT entry_mode,entry_notes FROM sites WHERE id=?', (sid,)).fetchone()
    extra = '（無需說明）' if s and s[0] in ('self', 'contact') and not (s[1] or '').strip() else ''      # 稽核 F9：密碼鎖／管理室沒說明，確認沒問題就不再列待補
    con.execute("UPDATE sites SET entry_ok=?,version=version+1 WHERE id=?", (f"{by} {c.now()[5:16].replace('T', ' ')}{extra}", sid))
    return '✅ 開門方式已確認'

def staff_edit(con, sid, text, by):
    """店長打字修改：覆蓋進場說明（保留在 entry_notes），並視為已確認。文字裡有「在家」→ 業主開門；「密碼／鑰匙」→ 自行進門；「管理員／家人」→ 他人開門。"""
    ensure(con)
    m = 'bell' if re.search('門鈴|有人在|店面|店裡', text) else 'home' if '在家' in text or '業主開' in text or '親自' in text else 'self' if re.search('密碼|鑰匙|自行', text) \
        else 'contact' if re.search('管理室|管理員|代管', text) else 'home' if '家人' in text else None      # 稽核 F9：家人開門＝業主親自開門
    before = dict(con.execute('SELECT * FROM sites WHERE id=?', (sid,)).fetchone() or {})
    con.execute("UPDATE sites SET entry_notes=?,entry_ok=?,entry_mode=COALESCE(?,entry_mode),version=version+1 WHERE id=?",
                (text.strip()[:300], f"{by} {c.now()[5:16].replace('T', ' ')}", m, sid))
    if before:
        _tell_workers(con, before, sid)
    return '✅ 已改好並確認：' + text.strip()[:60]

# ③ 前一天：業主按「我知道了，會來開門」→ 夥伴同步
def customer_ack(con, oid, who='業主'):
    import line_bot as lb, board
    ensure(con)
    o = c.get(con, 'orders', oid)
    if o.get('door_ack'):
        return lb._txt('已經記下了，謝謝您 🙏')
    con.execute('UPDATE orders SET door_ack=? WHERE id=?', (c.now(), oid))
    board.log(con, oid, 'door_ack', who)
    for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)):
        lb.to_worker(con, a['worker_id'], 'door_ack_w', '業主已確認會來開門 👍', lb._txt(f"👍 業主已確認 {lb.md(o['date'])} {o['start']} 會來開門。到了先打到場卡，系統會通知業主。"), oid)
    return lb._txt(f"好的，謝謝您 🙏 {lb.md(o['date'])} {o['start']} 服務人員到門口時，會在這裡通知您開門。")

# ④ 當天：夥伴請業主開門（到場打卡也會自動請）
def request_open(con, oid, wid=None, who='服務人員', auto=False):
    import line_bot as lb, board
    o, s = site_of(con, oid)
    if o.get('door_open') and not auto and mode_of(o, s) in NO_DOOR:      # 稽核 F1：打卡自動記的「已進門」，夥伴說進不去 → 清掉，照常請業主開門
        con.execute("UPDATE orders SET door_open='' WHERE id=?", (oid,)); o['door_open'] = ''
    if o.get('door_open'):
        return '業主已經說會來開門了 👍' if mode_of(o, s) == 'home' else '已經記錄進門了 👍'
    if auto and mode_of(o, s) in NO_DOOR:          # 按門鈴／密碼鎖／管理室 → 打卡＝已進入，不自動請業主開門
        return None
    last = o.get('door_req') or ''
    if auto and last[:10] == c.now()[:10]:              # 檢查：同組第二位夥伴到場打卡 → 業主不再收一次（第一位已經請過）
        return f'業主 {last[11:16]} 已經收到開門通知了，請稍等；還是進不去可以按「到門口了，請業主開門」再通知一次'
    if last and last > (dt.datetime.now(c.TZ) - dt.timedelta(minutes=3)).isoformat():
        return '剛剛已經通知業主了，請稍等；5 分鐘沒回應系統會請派單群打電話 🙏'
    con.execute('UPDATE orders SET door_req=? WHERE id=?', (c.now(), oid))
    board.log(con, oid, 'door_req', f"{who}{'（到場打卡自動）' if auto else ''}")
    acc, uid = lb.customer_target(con, o['customer_id'])
    if uid:
        m = card_at_door(con, oid)
        lb.queue(con, acc, uid, 'door_req_c', m[0]['altText'], m, oid)
        return f'已通知業主開門 🚪 {WAIT_MIN} 分鐘沒回應，系統會請派單群打電話給業主。'
    escalate(con, o, s, '業主沒綁 LINE，收不到開門通知')
    return '業主沒有綁 LINE，已請派單群打電話給業主，請稍等 🙏'

def card_at_door(con, oid):
    import line_bot as lb
    o, s, cu, crew, area = lb.order_ctx(con, oid)
    return lb.flex('服務人員到門口了，麻煩開門 🚪', lb._bubble(lb._header('服務人員到門口了 🚪', f"{dt.datetime.now(c.TZ).strftime('%H:%M')}・{s.get('name') or area}", brand=lb.brand_of(cu)),
        [lb._t('服務人員已經到了，麻煩您（或家人）開門 🙏 按下面讓他知道您馬上來。', wrap=True)],
        [lb._btn('我來開門 👍', data=f'a=door&o={oid}', primary=True), lb._btn('我不在，請照說明的方式進門', data=f'a=door_self&o={oid}')],
        '有其他進門方式要補充，直接在這裡留言。'))

def customer_open(con, oid, self_entry=False):
    """業主按「我來開門」或「請照說明進門」→ 停止催促、通知夥伴。"""
    import line_bot as lb, board
    ensure(con)
    o, s = site_of(con, oid)
    if self_entry and not (s.get('entry_notes') or '').strip():          # 稽核：說「照說明進門」但根本沒說明 → 夥伴進不去，請管理群打電話
        board.log(con, oid, 'customer_door', '照說明進門（但沒有說明）')
        escalate(con, dict(o), s, '業主說不在家、照說明進門，但案場沒有進門說明')
        return lb._txt('好的 🙏 我們這邊還沒有您的進門說明，專人會馬上打給您確認怎麼進門。')
    con.execute('UPDATE orders SET door_open=? WHERE id=?', (c.now(), oid))
    board.log(con, oid, 'customer_door', '照說明進門' if self_entry else '馬上來開門')
    txt = (f"🔑 業主說不在家，請照進場說明進門：{(s.get('entry_notes') or '請看工作頁')[-80:]}" if self_entry else f"🚪 業主說馬上來開門（{o['start']} 的工作）")
    for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)):
        lb.to_worker(con, a['worker_id'], 'door', '業主回覆開門', lb._txt(txt), oid)
    return lb._txt('好的，已經告訴服務人員了 👍')

# ⑤ 業主沒回應 → 管理群打電話
def escalate(con, o, s, why):
    import line_bot as lb, board
    if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='door_escalate'", (o['id'],)).fetchone():
        return False                  # 稽核：每張單只請管理群打一次（以前每 2 小時再發，直到完工）
    board.log(con, o['id'], 'door_escalate', why)
    cu = dict(con.execute('SELECT name,phone FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    cts = [f"{r[0]}（{r[1]}）{r[2]}" for r in con.execute("SELECT name,role,phone FROM contacts WHERE customer_id=? AND role!='請款窗口' AND phone!='' LIMIT 2", (o['customer_id'],))] if o['customer_id'] else []
    lb.admin_notify(con, '📞 服務人員在門口，業主沒回應', [('原因', why), ('業主', f"{cu.get('name', '')} {cu.get('phone') or '（沒電話）'}"), ('現場聯絡', '、'.join(cts) or '—'),
                    ('開門方式', mode_text(s)), ('處理', '請打電話請業主開門')], '/#home', '#C62828', oid=o['id'])
    for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)):
        lb.to_worker(con, a['worker_id'], 'door_wait', '已請派單群打給業主',
                     lb.flex('已請派單群打給業主', lb._bubble(lb._header('業主還沒回應', '已請派單群打電話', '#C47F00'),
                             [lb._t('📞 已請派單群打電話給業主，請在門口稍等一下 🙏 如果已經進去了，按下面讓我們知道。', wrap=True)],
                             [lb._btn('已經進門了 ✅', data=f"a=door_in&o={o['id']}&w={a['worker_id']}", primary=True)])), o['id'])
    return True

def worker_inside(con, oid, who):
    """夥伴按「已經進門了」：停止催業主／管理群；如果已經請管理群打電話，告訴他們不用打了。"""
    import line_bot as lb, board
    o, s = site_of(con, oid)
    if o.get('door_open'):
        return '收到 👍'
    con.execute('UPDATE orders SET door_open=? WHERE id=?', (c.now(), oid))
    board.log(con, oid, 'worker_inside', who)
    if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='door_escalate'", (oid,)).fetchone():
        lb.admin_notify(con, f'✅ {who} 已經進門了', [('處理', '不用再打給業主了')], '', '#2E7D32', oid=oid)
    return '收到 👍 開始工作吧，記得拍服務前照片。'

def watch(con):
    """背景每分鐘：① 請業主開門 5 分鐘沒回應 → 管理群；② 前一天沒按「會來開門」的 → 當天開工前 2 小時再提醒業主一次。"""
    import line_bot as lb
    ensure(con)
    now = dt.datetime.now(c.TZ); today = now.date().isoformat(); n = 0
    import board
    for o in c.rows(con, f"SELECT o.* FROM orders o WHERE o.date=? AND {board.GO_ORDER_SQL} AND COALESCE(o.door_req,'')!='' AND COALESCE(o.door_open,'')=''", (today,)):
        if con.execute("SELECT 1 FROM attachments WHERE order_id=? AND label='before'", (o['id'],)).fetchone():
            continue                  # 已經拍了服務前照＝進得去了，不用請管理群打電話
        if dt.datetime.fromisoformat(o['door_req']) <= now - dt.timedelta(minutes=WAIT_MIN):
            s = dict(con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'],)).fetchone() or {})
            n += escalate(con, o, s, f'請業主開門 {WAIT_MIN} 分鐘沒回應')
    for o in c.rows(con, "SELECT o.* FROM orders o JOIN sites s ON s.id=o.site_id WHERE o.date=? AND o.status='confirmed' AND COALESCE(NULLIF(o.entry_mode,''),s.entry_mode)='home' AND COALESCE(o.door_ack,'')=''", (today,)):
        start = dt.datetime.fromisoformat(f"{o['date']}T{o['start']}:00").replace(tzinfo=c.TZ)
        if dt.timedelta(minutes=90) < start - now <= dt.timedelta(minutes=120) and not con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='door_morning'", (o['id'],)).fetchone():
            acc, uid = lb.customer_target(con, o['customer_id'])
            if uid:
                m = lb.flex(f"今天 {o['start']} 服務人員會到，需要您開門 🚪", lb._bubble(lb._header('今天要開門喔 🚪', lb.when(dict(o)), brand=acc),
                    [lb._t(f"今天 {o['start']} 服務人員會到，需要您（或家人）開門。按一下讓我們知道 🙏", wrap=True)],
                    [lb._btn('我知道了，會去開門 ✅', data=f"a=door_ack&o={o['id']}", primary=True), lb._btn('我不在，需要改方式', data=f"a=ent_again&o={o['id']}")]))
                lb.queue(con, acc, uid, 'door_morning', m[0]['altText'], m, o['id']); n += 1
    return n

# ⑥ 店長補資料（負責人 開門方式每個案場確認一次，之後有變再改；業主沒綁 LINE 或沒辦法確認 → 請店長把資料填一填）
FILL_SKIP = 'ent_fill_skip'           # {site_id: 這天以前先不問}

def fill_due(con, days=7):
    """接下來 N 天有服務、案場還沒有開門方式的：業主沒綁 LINE（系統問不到）、問了 20 小時還沒回、或今天就要去 → 請店長補。"""
    import json, line_bot as lb
    ensure(con)
    now = dt.datetime.now(c.TZ); today = now.date().isoformat()
    try:
        sk = json.loads(ix.setting(con, FILL_SKIP) or '{}')
    except ValueError:
        sk = {}
    out = {}
    for o in c.rows(con, "SELECT o.*, s.entry_mode site_mode FROM orders o JOIN sites s ON s.id=o.site_id WHERE o.date BETWEEN ? AND ? AND o.status IN ('review','draft','confirmed') "
                         "AND COALESCE(o.entry_mode,'')='' AND (COALESCE(s.entry_mode,'')='' OR (s.entry_mode IN ('self','contact') AND COALESCE(s.entry_ok,'')='' AND COALESCE(s.entry_notes,'') NOT LIKE '%客戶說明（%') "
                         "OR (s.entry_mode IN ('self','contact') AND COALESCE(s.entry_notes,'')='' AND COALESCE(s.entry_ok,'') NOT LIKE '%無需說明%' "
                         "AND NOT EXISTS(SELECT 1 FROM contacts ct WHERE ct.customer_id=o.customer_id AND COALESCE(ct.phone,'')!=''))) "
                         "AND COALESCE(o.review_note,'')!='示範工單' ORDER BY o.date, o.start",          # 稽核：選了密碼鎖／管理員卻沒打說明的也要補
                    (today, (now.date() + dt.timedelta(days=days)).isoformat())):
        if o['site_id'] in out or sk.get(o['site_id'], '') > today:
            continue
        if sibling(con, o['customer_id'], o['site_id']):
            continue                      # 同業主其他地點答過 → 確認時間／前一天會自動沿用，不用補
        acc, uid = lb.customer_target(con, o['customer_id'])
        if uid and o['date'] > today and o['site_mode'] not in ('self', 'contact'):        # 稽核 F9：密碼鎖／管理室沒說明 → 系統不會問業主，直接請管理層補
            asked = con.execute("SELECT MIN(x.created_at) FROM line_outbox x JOIN orders y ON y.id=x.order_id WHERE y.customer_id=? AND x.kind='entry_ask' AND x.status='sent'",
                                (o['customer_id'],)).fetchone()[0] or ix.setting(con, f"ent_asked:{o['customer_id']}")      # 每位業主只問一次 → 看這位業主有沒有被問過（不分地點；確認時間後直接回覆的也算）
            if not asked or asked > (now - dt.timedelta(hours=3)).isoformat():
                continue                  # 業主有綁 LINE：前一天 18:00 系統會自動問，問了 3 小時沒回就請店長（稽核：以前 20 小時，等於服務當天才問）
        out[o['site_id']] = dict(o, line_bound=bool(uid))
    return list(out.values())

def card_fill(con, o):
    import line_bot as lb, brands
    s = dict(con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'],)).fetchone() or {})
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    b = brands.of_customer(cu) if cu else 'demoshop'
    left = len(fill_due(con))
    sid = s.get('id', '')
    return lb.flex(f"開門方式待補：{cu.get('name') or s.get('name', '')}", lb._bubble(lb._header('🚪 開門方式待補', cu.get('name') or s.get('name') or '', '#C47F00', brand=b),
        [lb._row('品牌', brands.name(b)), lb._row('下次服務', lb.when(dict(o))), lb._row('地點', f"{s.get('name') or ''} {(s.get('address') or '')[:24]}".strip() or '—'),
         lb._row('業主電話', cu.get('phone') or '—'), lb._row('為什麼問妳', '業主還沒綁 LINE，系統問不到' if not o.get('line_bound') else '系統問了業主，還沒回'),
         lb._t('這個案場確認一次就會記住，之後每次服務都用這個；有變再改。選「密碼鎖」或「管理室」之後，請接著打字補說明。只有「業主親自開門」會通知業主開門，其他到場打卡就算進門。', color=lb.SUB, size='xs', wrap=True, margin='md')],
        [lb._btn('業主（或家人）親自開門', data=f'a=ent_set&s={sid}&m=home', primary=True), lb._btn('按門鈴／有人在，直接進入', data=f'a=ent_set&s={sid}&m=bell'),
         lb._btn('密碼鎖／鑰匙，自行進門', data=f'a=ent_set&s={sid}&m=self'), lb._btn('大樓管理室／管理員開門', data=f'a=ent_set&s={sid}&m=contact'),
         lb._btn('打電話問了再補（明天再問）', data=f'a=ent_skip&s={sid}')]
        + ([lb._btn('打開客戶資料', uri=f"{lb.BASE_URL}/#c/{cu['id']}")] if cu.get('id') else []),
        f'還有 {left} 個案場要補。'))

def next_fill(con, skip_sid=''):
    for o in fill_due(con):
        if o['site_id'] != skip_sid:
            return card_fill(con, o)
    return None

def staff_set(con, sid, m, uid, by):
    """店長點選開門方式（視為已確認）。密碼鎖／管理員 → 接著打字補說明。"""
    if m not in MODES:
        raise ValueError('開門方式不對')
    ensure(con)
    s = con.execute('SELECT id FROM sites WHERE id=?', (sid,)).fetchone()
    if not s:
        raise ValueError('找不到這個案場')
    before = dict(con.execute('SELECT * FROM sites WHERE id=?', (sid,)).fetchone())
    con.execute("UPDATE sites SET entry_mode=?,entry_ok=?,version=version+1 WHERE id=?", (m, f"{by} {c.now()[5:16].replace('T', ' ')}", sid))
    c.audit(con, 'entry_mode', 'sites', sid, after={'mode': m, 'by': by + '（LINE 補資料）'})
    reset_inherited(con, before, m)
    if m in ('home', 'bell'):
        _tell_workers(con, before, sid)
    if m == 'bell':
        return f'✅ 已記下：{MODES[m]}。夥伴到場打卡就算進門，不會再通知業主開門。'
    if m in ('self', 'contact'):
        import line_bot as _lb; _lb._staff_wait(con, uid, 'entedit_wait', sid)
        return f"✅ 已記下：{MODES[m]}。\n請接著打字補說明（{'例如：大門密碼 1234#鑰匙放信箱' if m == 'self' else '例如：管理員王先生 0900-000-000'}），只會給當天接案的夥伴看。"
    return f'✅ 已記下：{MODES[m]}。服務前一天會請業主按「會來開門」（沒綁 LINE 的請打電話提醒）。'

def fill_skip(con, sid, by):
    import json
    try:
        sk = json.loads(ix.setting(con, FILL_SKIP) or '{}')
    except ValueError:
        sk = {}
    sk[sid] = (dt.datetime.now(c.TZ).date() + dt.timedelta(days=1)).isoformat()
    ix.set_setting(con, FILL_SKIP, json.dumps(sk))
    return '好，明天再問妳這一個 👌'
