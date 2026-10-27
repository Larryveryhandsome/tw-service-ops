"""收款規則與收款單（第二批：確認）。

規則：
- 年度保養類（年度保養／細清、進階專案、搬家前後、啟用前服務）：開工前 7 天收 30% 訂金；尾款完工後 3 天內。
- 單次服務：開工前 7 天收全額。
- 居家訂閱（每月固定次數）：每月 5 號收當月費用；第一個月開工前 7 天收。
- 簽約時離服務日已不足 7 天：當天就要付（期限＝簽約日）。
- 簽約後就開始找人；**店長確認收款後才排定**（把地址給夥伴）→ cleared_for_schedule()。
- LINE Pay 串好以後：付款成功直接 confirm(by='LINE Pay')。

金額單位：分（和系統其他地方一樣）。
"""
import datetime as dt
import json
import secrets
import core as c

DEPOSIT_PCT = 30
DUE_BEFORE_DAYS = 7
BALANCE_AFTER_DAYS = 3
SUBSCRIPTION_DAY = 5
DEEP = ('年度保養', '細清', '施工', '搬家', '啟用前', '交屋', '空屋')
STATUS = {'pending': '待付款', 'reported': '客戶回報已匯款', 'confirmed': '已收款', 'cancelled': '已取消', 'refunded': '已退款'}

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS payment_requests(id TEXT PRIMARY KEY, created_at TEXT, quote_id TEXT, order_id TEXT, customer_id TEXT, "
                "kind TEXT, label TEXT, amount INTEGER, due TEXT, status TEXT DEFAULT 'pending', ref TEXT DEFAULT '', reported_at TEXT DEFAULT '', "
                "confirmed_by TEXT DEFAULT '', confirmed_at TEXT DEFAULT '', note TEXT DEFAULT '', reminded TEXT DEFAULT '')")
    cols = {r[1] for r in con.execute('PRAGMA table_info(payment_requests)')}
    if 'reminded' not in cols:
        con.execute("ALTER TABLE payment_requests ADD COLUMN reminded TEXT DEFAULT ''")

def mark(con, pid, tag):
    """記下已發過的提醒（同一筆同一種只發一次）。回傳 True＝這次第一次。"""
    r = con.execute('SELECT reminded FROM payment_requests WHERE id=?', (pid,)).fetchone()
    tags = set(filter(None, (r[0] if r else '').split(',')))
    if tag in tags:
        return False
    tags.add(tag); con.execute('UPDATE payment_requests SET reminded=? WHERE id=?', (','.join(sorted(tags)), pid)); return True

STAFF_PAY = {'訂金尾款': 'deep', '全額預付': 'single', '完工後付款': 'after', '每月月結': 'monthly', '當日結算': 'onsite', '月訂閱': 'subscription'}
PAY_LABEL = {'': '依服務類型（標準）', '訂金尾款': '訂金 30%＋尾款（完工後 3 天）', '全額預付': '全額預付（服務日 7 天前）', '完工後付款': '完工後付款（3 天內，不收訂金）',
             '每月月結': '每月月結（次月 5 號前）', '當日結算': '當日結算（完工當天）', '月訂閱': '月訂閱（每月 5 號前付當月）'}

_AS = {'v': None, 't': 0.0}

def after_scope():
    """管理群決定哪些改「完工後付款」（回報、管理群回答）：all＝全部（月訂閱照舊）／single＝單次居家／''＝維持。30 秒快取。"""
    import time, integrations as ix
    if _AS['v'] is None or time.time() - _AS['t'] > 30:
        try:
            con = c.connect()
            try:
                _AS['v'] = ix.setting(con, 'pay_after_scope') or ''
            finally:
                con.close()
        except Exception:
            _AS['v'] = _AS['v'] or ''
        _AS['t'] = time.time()
    return _AS['v']

def kind_of(service_type, freq='', brand='', pay_method=''):
    """依品牌、服務類型與頻率決定收款方式：deep（訂金＋尾款）／subscription（月訂閱）／single（單次全額）；
    企業線日常房務依業主選的付款方式：onsite（當日結算）／monthly（月結）／single（預付）。
    管理層在報價單指定的付款方式（已跟客戶談好）優先：after（完工後付款）、monthly（每月月結）…"""
    if pay_method in STAFF_PAY:
        return STAFF_PAY[pay_method]
    st = service_type or ''
    if brand == 'bizline' and '年度保養' not in st:
        return {'當日結算': 'onsite', '月結': 'monthly'}.get(pay_method, 'single')
    deep = any(k in st for k in DEEP); sub = bool(freq and freq not in ('單次', '先試掃一次'))
    sc = after_scope()
    if not sub and (sc == 'all' or (sc == 'single' and not deep)):
        return 'after'                       # 服務後結帳
    if deep:
        return 'deep'
    if sub:
        return 'subscription'
    return 'single'

def _yuan_up(cents):
    return -(-int(cents) // 100) * 100          # 無條件進位到元

AMOUNT_LINE = 1000000                    # 一萬元（分）

def plan(total_cents, service_type, service_date, signed_on, freq='', brand='', pay_method='', company=False):
    """回傳收款項目 [{kind,label,amount,due}]。total_cents＝含稅總額（訂閱則為每月金額）。"""
    sd = dt.date.fromisoformat(service_date) if service_date else None
    today = dt.date.fromisoformat(signed_on)
    before = max(today, sd - dt.timedelta(days=DUE_BEFORE_DAYS)) if sd else today
    k = kind_of(service_type, freq, brand, pay_method)
    if not pay_method and k in ('single', 'deep', 'after') and after_scope() == 'amount':      # 依金額
        k = 'deep' if int(total_cents) >= AMOUNT_LINE else 'single'     # 一萬以上先收 30%；一萬以下全額預付（改成事前收款，公司行號也一樣；報價單另外指定付款方式的照指定）
    if k == 'after':                     # 完工後付款（已跟客戶談好；不收訂金、不擋排定）→ 完工後 3 天內
        return [{'kind': 'after', 'label': '服務費（完工後付款）', 'amount': int(total_cents), 'due': '', 'note': f'完工後 {BALANCE_AFTER_DAYS} 天內'}]
    if k == 'onsite':                    # 企業線：當天清完請款（不擋排定）
        return [{'kind': 'onsite', 'label': '服務費（當日結算）', 'amount': int(total_cents), 'due': (sd or today).isoformat()}]
    if k == 'monthly':                   # 企業線長期配合：月底統計、次月 5 號前付（不擋排定）
        m = (sd or today).replace(day=28) + dt.timedelta(days=4)
        return [{'kind': 'monthly', 'label': '服務費（月結）', 'amount': int(total_cents), 'due': m.replace(day=SUBSCRIPTION_DAY).isoformat()}]
    if k == 'deep':
        dep = _yuan_up(total_cents * DEPOSIT_PCT / 100)
        return [{'kind': 'deposit', 'label': f'訂金 {DEPOSIT_PCT}%', 'amount': dep, 'due': before.isoformat()},
                {'kind': 'balance', 'label': '尾款', 'amount': int(total_cents) - dep, 'due': '', 'note': f'完工後 {BALANCE_AFTER_DAYS} 天內'}]
    if k == 'subscription':
        return [{'kind': 'month', 'label': f"{(sd or today).strftime('%Y-%m')} 月訂閱費", 'amount': int(total_cents), 'due': before.isoformat()}]
    return [{'kind': 'full', 'label': '服務費（全額）', 'amount': int(total_cents), 'due': before.isoformat()}]

def create_for(con, quote_id, order_id, customer_id, total_cents, service_type, service_date, signed_on, freq='', brand='', pay_method='', company=False):
    """簽約後建立收款單（重複呼叫不會重建）。"""
    ensure(con)
    if con.execute("SELECT 1 FROM payment_requests WHERE quote_id=? AND status!='cancelled'", (quote_id,)).fetchone():
        return rows_for(con, quote_id=quote_id)
    if not total_cents:            # 只有單價／另計項目：簽約時沒有金額，完工依實際數量由店長開帳，不建 0 元收款單（也不擋排定）
        return []
    for p in plan(total_cents, service_type, service_date, signed_on, freq, brand, pay_method, company):
        con.execute('INSERT INTO payment_requests(id,created_at,quote_id,order_id,customer_id,kind,label,amount,due,note) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (c.uid('pay'), c.now(), quote_id, order_id, customer_id, p['kind'], p['label'], p['amount'], p['due'], p.get('note', '')))
    return rows_for(con, quote_id=quote_id)

def rows_for(con, quote_id=None, order_id=None, customer_id=None):
    ensure(con)
    col, val = ('quote_id', quote_id) if quote_id else ('order_id', order_id) if order_id else ('customer_id', customer_id)
    return c.rows(con, f"SELECT * FROM payment_requests WHERE {col}=? AND status!='cancelled' ORDER BY CASE kind WHEN 'balance' THEN 1 ELSE 0 END, due", (val,))

def report(con, pid, ref, by):
    """客戶在 LINE 按「我已匯款」＋末五碼 → 等店長確認。"""
    ensure(con)
    r = con.execute('SELECT * FROM payment_requests WHERE id=?', (pid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆款項')
    if r['status'] == 'confirmed':
        return dict(r)
    if r['status'] == 'cancelled':
        raise ValueError('這筆款項已取消（工單取消），不用付款；有問題請在官方 LINE 跟我們說')
    ref = ''.join(ch for ch in str(ref) if ch.isdigit())[-5:]
    if len(ref) != 5:
        raise ValueError('請填匯款帳號末五碼')
    con.execute("UPDATE payment_requests SET status='reported',ref=?,reported_at=? WHERE id=?", (ref, c.now(), pid))
    if r['customer_id']:                                     # 記住業主匯款末五碼，下次自動帶入
        con.execute('UPDATE customers SET remit_last5=? WHERE id=?', (ref, r['customer_id']))
    c.audit(con, 'payment_reported', 'payment_requests', pid, after={'ref': ref, 'by': by})
    return dict(con.execute('SELECT * FROM payment_requests WHERE id=?', (pid,)).fetchone())

def confirm(con, pid, by, amount=None, note=''):
    """店長確認收款（金額可改：特殊情況以她為準，留紀錄）。"""
    ensure(con)
    r = con.execute('SELECT * FROM payment_requests WHERE id=?', (pid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆款項')
    amt = int(amount) if amount not in (None, '') else r['amount']
    con.execute("UPDATE payment_requests SET status='confirmed',amount=?,confirmed_by=?,confirmed_at=?,note=CASE WHEN ?='' THEN note ELSE ? END WHERE id=?",
                (amt, by, c.now(), note, note, pid))
    c.audit(con, 'payment_confirmed', 'payment_requests', pid, before={'amount': r['amount'], 'status': r['status']}, after={'amount': amt, 'by': by, 'note': note})
    return dict(con.execute('SELECT * FROM payment_requests WHERE id=?', (pid,)).fetchone())

def edit(con, pid, by, amount=None, due=None, note=''):
    """店長手動調整（金額、期限、分期說明），以她改的為準。"""
    ensure(con)
    r = con.execute('SELECT * FROM payment_requests WHERE id=?', (pid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆款項')
    con.execute('UPDATE payment_requests SET amount=?,due=?,note=? WHERE id=?',
                (int(amount) if amount not in (None, '') else r['amount'], due if due is not None else r['due'], note or r['note'], pid))
    c.audit(con, 'payment_edit', 'payment_requests', pid, before=dict(r), after={'amount': amount, 'due': due, 'note': note, 'by': by})

def cleared_for_schedule(con, order_id):
    """開工前要收的款（訂金／全額／當月訂閱）都確認了沒？沒有收款單的舊工單視為可排定。"""
    rows = rows_for(con, order_id=order_id)
    need = [r for r in rows if r['kind'] in ('deposit', 'full', 'month')]
    return all(r['status'] == 'confirmed' for r in need)

def balance_due_on_complete(con, order_id, completed_on):
    """完工後：尾款期限＝完工日＋3 天。"""
    ensure(con)
    due = (dt.date.fromisoformat(completed_on) + dt.timedelta(days=BALANCE_AFTER_DAYS)).isoformat()
    con.execute("UPDATE payment_requests SET due=? WHERE order_id=? AND kind IN ('balance','after') AND status='pending' AND due=''", (due, order_id))

def on_order_change(con, before, after, actor=''):
    """工單取消／封存／改期 → 收款單跟著變（稽核：以前取消後客戶還每天收付款提醒、管理群收逾期卡）。
    取消／封存：未付的作廢（status=cancelled，不再催）；已付或客戶回報已匯的 → 請店長決定退款、抵用或依合約不退。
    改期：未付款項的期限跟著服務日移動（不早於今天）。"""
    ensure(con)
    if before and after['status'] in ('cancelled', 'archived') and before['status'] not in ('cancelled', 'archived'):
        import decisions; decisions.month_on_cancel(con, after)      # 訂閱月費照管理群決定的規則（照收／照次數扣／每次問）
    rows = c.rows(con, 'SELECT * FROM payment_requests WHERE order_id=?', (after['id'],))
    if not rows or not before:
        return
    import line_bot as lb
    if after['status'] in ('cancelled', 'archived') and before['status'] != after['status']:
        for r in rows:
            if r['status'] == 'pending' and r['kind'] != 'month':        # 訂閱月費（整個月的）不跟單一場次作廢，交給店長
                con.execute("UPDATE payment_requests SET status='cancelled',note=TRIM(COALESCE(note,'')||' '||?) WHERE id=?", (f"工單取消 {c.now()[:10]}", r['id']))
        paid = [r for r in rows if r['status'] in ('confirmed', 'reported') and r['kind'] != 'month']      # 月費（整個月的）交給 decisions.month_on_cancel
        if paid:
            lb.admin_notify(con, '💰 取消的工單有已付款項', [('款項', '、'.join(f"{r['label']} NT$ {r['amount'] // 100:,}（{'已收款' if r['status'] == 'confirmed' else '客戶回報已匯款'}）" for r in paid)),
                                                       ('處理', '請店長決定：退款、抵用下次服務，或依合約不退')], '', '#C62828', oid=after['id'], tag='取消的工單有已付款項')
        c.audit(con, 'payments_void', 'orders', after['id'], after={'voided': [r['id'] for r in rows if r['status'] == 'pending'], 'by': actor})
    elif before.get('date') and after.get('date') and before['date'] != after['date'] and after['status'] not in ('cancelled', 'archived'):
        delta = dt.date.fromisoformat(after['date']) - dt.date.fromisoformat(before['date'])
        today = dt.datetime.now(c.TZ).date()
        for r in rows:
            if r['status'] == 'pending' and r['due'] and r['kind'] in ('deposit', 'full', 'balance', 'onsite', 'after'):       # onsite＝企業線當日收
                nd = max(dt.date.fromisoformat(r['due']) + delta, today).isoformat()
                con.execute('UPDATE payment_requests SET due=?,reminded=? WHERE id=?', (nd, '', r['id']))     # 新期限重新提醒
        c.audit(con, 'payments_shift', 'orders', after['id'], after={'days': delta.days, 'by': actor})

def reminders(con, today):
    """每天跑一次：期限前 3 天、前 1 天提醒客戶；過期未付通知派單群。回傳 [(row, 'remind_3'|'remind_1'|'overdue')]。"""
    ensure(con)
    t = dt.date.fromisoformat(today); out = []
    for r in c.rows(con, "SELECT p.* FROM payment_requests p LEFT JOIN orders o ON o.id=p.order_id WHERE p.status='pending' AND p.due!='' "
                         "AND COALESCE(o.status,'') NOT IN ('cancelled','archived') AND (COALESCE(p.order_id,'')='' OR o.id IS NOT NULL)"):        # 工單取消／刪除了就不催
        d = (dt.date.fromisoformat(r['due']) - t).days
        if d == 3:
            out.append((r, 'remind_3'))
        elif d == 1:
            out.append((r, 'remind_1'))
        elif d < 0:
            out.append((r, 'overdue'))
    return out

def subscriptions(con, month):
    """每月：已簽約的訂閱方案，這個月有排服務 → 建當月收款單（期限 5 號）。這個月沒排（停掉／暫停）→ 不收。重複呼叫不重建。
    同一位客戶同一品牌只算最新簽的那張；第一個月已在簽約時收。回傳新建的收款單 id。"""
    ensure(con)
    made, seen = [], set()
    for q in c.rows(con, "SELECT id,account,customer_id,form FROM quotes WHERE status='signed' AND COALESCE(customer_id,'')!='' ORDER BY signed_at DESC"):
        try:
            f = json.loads(q['form'] or '{}')
        except ValueError:
            continue
        if kind_of(f.get('service_type', ''), f.get('freq') or '', q['account'], f.get('pay_method') or '') != 'subscription' or (q['customer_id'], q['account'], f.get('service_type') == '垃圾代丟') in seen:
            continue
        seen.add((q['customer_id'], q['account'], f.get('service_type') == '垃圾代丟'))      # 每日檢查：代丟月費和服務月費各自開（以前互相吃掉）
        first = con.execute("SELECT amount,label FROM payment_requests WHERE quote_id=? AND kind='month' ORDER BY created_at LIMIT 1", (q['id'],)).fetchone()     # 每日檢查：首期被取消也照原金額（以前從此不再開單）
        if not first or first['label'][:7] >= month:
            continue
        if con.execute("SELECT 1 FROM payment_requests WHERE quote_id=? AND kind='month' AND label LIKE ?", (q['id'], month + '%')).fetchone():
            continue
        if not con.execute("SELECT 1 FROM orders WHERE customer_id=? AND date LIKE ? AND status NOT IN ('cancelled','archived') AND (title LIKE '垃圾代丟%')=? AND COALESCE(parent_order_id,'')='' AND title NOT LIKE '補清%'",
                           (q['customer_id'], month + '%', int(f.get('service_type') == '垃圾代丟'))).fetchone():      # 每日檢查：只算同一種服務、不算補服務（服務停了、代丟還在跑，不會再開服務月費）
            continue
        pid = c.uid('pay')
        con.execute("INSERT INTO payment_requests(id,created_at,quote_id,customer_id,kind,label,amount,due,note) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pid, c.now(), q['id'], q['customer_id'], 'month', f'{month} 月訂閱費', first['amount'], f'{month}-{SUBSCRIPTION_DAY:02d}', '每月自動建立'))
        made.append(pid)
    return made

def prepaid(con, o):
    """這筆服務已經用收款單收（線上簽約的訂金／尾款／全額，或訂閱當月費）→ 不用再月結開帳。回傳說明或空字串。"""
    ensure(con)
    if con.execute("SELECT 1 FROM payment_requests WHERE order_id=? AND status!='cancelled' AND kind IN ('deposit','balance','full','month','onsite','monthly','after')", (o['id'],)).fetchone():      # 稽核：企業線當日結算／月結也算（不然第一場請款兩次）
        return '線上簽約收款單'
    if o.get('customer_id') and o.get('date') and con.execute("SELECT 1 FROM payment_requests WHERE customer_id=? AND kind='month' AND status!='cancelled' AND label LIKE ?",
                                                               (o['customer_id'], o['date'][:7] + '%')).fetchone():
        return '當月訂閱費'
    return ''

def monthly_subscriptions(con, month, plans):
    """每月 1 號：為每個訂閱方案建當月收款單（期限 5 號）。plans＝[{customer_id, amount, label}]；重複呼叫不重建。"""
    ensure(con)
    due = f'{month}-{SUBSCRIPTION_DAY:02d}'; made = 0
    for p in plans:
        key = f"sub:{p['customer_id']}:{month}"
        if con.execute('SELECT 1 FROM payment_requests WHERE quote_id=?', (key,)).fetchone():
            continue
        con.execute("INSERT INTO payment_requests(id,created_at,quote_id,customer_id,kind,label,amount,due) VALUES(?,?,?,?,?,?,?,?)",
                    (c.uid('pay'), c.now(), key, p['customer_id'], 'month', p.get('label') or f'{month} 月訂閱費', int(p['amount']), due))
        made += 1
    return made
