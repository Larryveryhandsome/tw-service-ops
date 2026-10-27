"""夥伴買耗材（wei 耗材快用完，在群組問店長能不能買 → 店長說可以 → 他拍發票）。

流程：夥伴在工作卡按「要買耗材」（或在 LINE 打「要買耗材」）→ 打字：買什麼、大約多少
     → 店長收到卡片：同意／這次不用買 → 通知夥伴
     → 同意：夥伴買好直接傳發票照片 → 打金額 → 記成「代墊」，掛在那一場的派工上（assignments.reimburse），
       中控台月結、薪資明細、結算單、夥伴「我的收入」都會一起算進去；店長收到「發票已登記」。
"""
import base64, re
import core as c
import integrations as ix

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS supply_requests(id TEXT PRIMARY KEY, created_at TEXT, worker_id TEXT, order_id TEXT, assignment_id TEXT, item TEXT, "
                "status TEXT DEFAULT 'pending', decided_by TEXT DEFAULT '', decided_at TEXT DEFAULT '', amount INTEGER DEFAULT 0, attachment_id TEXT DEFAULT '', note TEXT DEFAULT '')")
    if 'reimburse' not in {r[1] for r in con.execute('PRAGMA table_info(assignments)')}:
        con.execute('ALTER TABLE assignments ADD COLUMN reimburse INTEGER DEFAULT 0')
    # 稽核 #6：代墊掛的那一場被拿掉／取消（沒有取消補貼）→ 代墊移到他另一場還有效的工作，月結、記帳才算得到（每條路都算：資料庫觸發）
    con.execute("""CREATE TRIGGER IF NOT EXISTS reimburse_keep AFTER UPDATE OF status ON assignments
        WHEN OLD.status='accepted' AND NEW.status IN ('declined','cancelled') AND COALESCE(NEW.reimburse,0)>0 AND COALESCE(NEW.amount,0)=0
          AND EXISTS (SELECT 1 FROM assignments x JOIN orders o ON o.id=x.order_id WHERE x.worker_id=NEW.worker_id AND x.status='accepted' AND x.id!=NEW.id AND o.status NOT IN ('cancelled','archived'))
        BEGIN
          UPDATE assignments SET reimburse=COALESCE(reimburse,0)+NEW.reimburse WHERE id=(SELECT x.id FROM assignments x JOIN orders o ON o.id=x.order_id
            WHERE x.worker_id=NEW.worker_id AND x.status='accepted' AND x.id!=NEW.id AND o.status NOT IN ('cancelled','archived') ORDER BY abs(julianday(o.date)-julianday('now')) LIMIT 1);
          UPDATE assignments SET reimburse=0 WHERE id=NEW.id;
        END""")

def job_for(con, wid, oid=''):
    """用品是給哪一場用的：指定的那場，沒指定就找這位夥伴今天以後最近的一場（找不到就最近做過的）。"""
    import datetime as dt
    if oid and con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone():
        return oid
    today = dt.datetime.now(c.TZ).date().isoformat()
    r = con.execute("SELECT o.id FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.date>=? "
                    "AND o.status NOT IN ('cancelled','archived') ORDER BY o.date,o.start LIMIT 1", (wid, today)).fetchone() or \
        con.execute("SELECT o.id FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' ORDER BY o.date DESC LIMIT 1", (wid,)).fetchone()
    return r[0] if r else ''

def request(con, wid, oid, text):
    import line_bot as lb, reviews
    ensure(con)
    oid = job_for(con, wid, oid)
    if not oid:
        raise ValueError('找不到你接下來的工作，請直接留言給派單同事 🙏')
    a = con.execute("SELECT id FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone()
    rid = c.uid('sup')
    con.execute('INSERT INTO supply_requests(id,created_at,worker_id,order_id,assignment_id,item) VALUES(?,?,?,?,?,?)', (rid, c.now(), wid, oid, a[0] if a else '', text.strip()[:200]))
    w = c.get(con, 'workers', wid); o = c.get(con, 'orders', oid)
    m = lb.flex(f"{w['name']} 要買耗材：{text[:30]}", lb._bubble(lb._header('🧴 夥伴要買耗材', w['name'], '#0A84FF'),
        [lb._row('要買', text.strip()[:120]), lb._row('給哪場用', lb.when(dict(o))), lb._row('地點', lb.order_ctx(con, oid)[4] or '—')],
        [lb._btn('同意，買好拍發票', data=f'a=sup_ok&r={rid}', primary=True), lb._btn('這次不用買', data=f'a=sup_no&r={rid}')],
        '同意後夥伴買好會傳發票照片＋金額，系統自動記成代墊、併入月底薪資。'))
    lb.to_managers(con, 'supply', m[0]['altText'], m, oid)          # 送管理群 @店長
    return rid

def decide(con, rid, ok, by):
    import line_bot as lb
    ensure(con)
    r = con.execute('SELECT * FROM supply_requests WHERE id=?', (rid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆')
    if r['status'] != 'pending':
        return f"這筆已經處理過了（{r['decided_by']}）"
    con.execute('UPDATE supply_requests SET status=?,decided_by=?,decided_at=? WHERE id=?', ('approved' if ok else 'declined', by, c.now(), rid))
    u = con.execute('SELECT user_id FROM line_users WHERE worker_id=? AND is_friend=1 ORDER BY last_seen DESC LIMIT 1', (r['worker_id'],)).fetchone()
    if ok:
        if u:
            ix.set_setting(con, f'supr_wait:{u[0]}', rid)
        lb.to_worker(con, r['worker_id'], 'supply_ok', '經理同意買耗材了', lb._txt(f"✅ 經理同意了：{r['item'][:60]}\n買好請直接在這裡傳發票照片，再打金額，會記成代墊、併入月底結算一起給你 🙏"), r['order_id'])
        return f'✅ 已同意，已通知夥伴（{by}）'
    lb.to_worker(con, r['worker_id'], 'supply_no', '這次先不用買', lb._txt(f"經理說這次先不用買：{r['item'][:60]}\n有問題直接在這裡留言 🙏"), r['order_id'])
    return f'已回覆夥伴：這次不用買（{by}）'

def receipt_photo(con, uid, raw):
    """夥伴傳發票照片（同意後）。回傳回覆文字。"""
    import photos
    ensure(con)
    rid = ix.setting(con, f'supr_wait:{uid}')
    r = con.execute('SELECT * FROM supply_requests WHERE id=?', (rid,)).fetchone()
    if not r:
        return None
    o = c.get(con, 'orders', r['order_id'])
    att = photos.upload(con, {'order_id': r['order_id'], 'label': 'receipt', 'service_date': o['date'] or c.now()[:10], 'name': '用品發票', 'content': base64.b64encode(raw).decode()})
    con.execute('UPDATE supply_requests SET attachment_id=? WHERE id=?', (att['id'], rid))
    ix.set_setting(con, f'supr_wait:{uid}', ''); ix.set_setting(con, f'supamt_wait:{uid}', rid)
    return '收到發票 🧾 請打發票上的總金額（只打數字，例如 99）'

def receipt_amount(con, uid, text):
    import line_bot as lb, worker_portal, reviews
    rid = ix.setting(con, f'supamt_wait:{uid}')
    m = re.search(r'\d[\d,]*', text or '')
    if not m:
        return '請只打數字金額（例如 99）🙏'
    amt = int(m.group(0).replace(',', ''))
    if amt <= 0 or amt > 20000:
        return '金額看起來不對，請再打一次（只打數字）'
    ix.set_setting(con, f'supamt_wait:{uid}', '')
    r = con.execute('SELECT * FROM supply_requests WHERE id=?', (rid,)).fetchone()
    con.execute("UPDATE supply_requests SET amount=?,status='receipt' WHERE id=?", (amt * 100, rid))
    if r['assignment_id']:
        con.execute('UPDATE assignments SET reimburse=COALESCE(reimburse,0)+?,version=version+1 WHERE id=?', (amt * 100, r['assignment_id']))
    c.audit(con, 'supply_receipt', 'assignments', r['assignment_id'], after={'amount': amt, 'item': r['item']})
    w = c.get(con, 'workers', r['worker_id'])
    url = worker_portal.photo_url(con, r['attachment_id']) if r['attachment_id'] else ''
    msg = lb.flex(f"{w['name']} 用品發票已登記 NT$ {amt:,}", lb._bubble(lb._header('🧾 用品發票已登記（代墊）', f"NT$ {amt:,}", '#2E7D32'),
        [lb._row('夥伴', w['name']), lb._row('買了', r['item'][:80]), lb._row('處理', '已併入這位夥伴本月結算')], [lb._btn('看發票照片', uri=url, primary=True)] if url else []))
    lb.to_managers(con, 'supply_receipt', msg[0]['altText'], msg, r['order_id'])          # 送管理群 @店長
    return f'✅ 已登記代墊 NT$ {amt:,}，會併入月底結算一起給你，辛苦了 🙏'

def stock_check(con, days=14):
    """（耗材沒到貨、臨時買的服務力不夠）：新進的單要用我們的耗材 → 一筆「確認備品夠／已到貨」（彙整，不一單一則）。
    回傳 [(order)]：上次確認之後新建、14 天內開工、業主沒有齊全工具（我們自備）的工單。"""
    import json as _j
    acked = set(_j.loads(ix.setting(con, 'stock_acked') or '[]'))       # 每日檢查：改記「確認過的工單」（時間比對 UTC／台灣格式不同會漏；固定排程進到 14 天內也要列）
    t = c.now()[:10]
    return [o for o in c.rows(con, "SELECT o.* FROM orders o JOIN sites s ON s.id=o.site_id WHERE o.date BETWEEN ? AND date(?, '+%d days') "
                                   "AND o.status IN ('draft','review','confirmed') AND COALESCE(s.tools,'')!='full' ORDER BY o.date" % int(days), (t, t)) if o['id'] not in acked]

def stock_ack(con, by):
    import json as _j
    t = c.now()[:10]
    keep = {r[0] for r in con.execute("SELECT id FROM orders WHERE date>=?", (t,))}
    acked = (set(_j.loads(ix.setting(con, 'stock_acked') or '[]')) & keep) | {o['id'] for o in stock_check(con)}
    ix.set_setting(con, 'stock_acked', _j.dumps(sorted(acked)))
    ix.set_setting(con, 'stock_ack', c.now())
    c.audit(con, 'stock_ack', 'settings', 'stock_ack', after={'by': by})
    return '已記下：備品確認過了（之後新進的單會再提醒）'
