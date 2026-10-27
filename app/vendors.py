"""配合廠商（窗簾清洗、高空作業、垃圾清運⋯）：外包成本記在工單上、廠商應付款、登記已付。

只有看得到金額的人（負責人、店長）可以看、可以改。金額單位：分（跟其他帳務一致）。
"""
import core as c

CATEGORIES = ['窗簾清洗', '高空作業', '垃圾清運', '冷氣清洗', '除蟲消毒', '水電修繕', '其他']

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS vendors(id TEXT PRIMARY KEY, name TEXT, category TEXT DEFAULT '', phone TEXT DEFAULT '', bank TEXT DEFAULT '', "
                "notes TEXT DEFAULT '', status TEXT DEFAULT 'active', created_at TEXT, version INTEGER DEFAULT 1)")
    con.execute("CREATE TABLE IF NOT EXISTS vendor_costs(id TEXT PRIMARY KEY, order_id TEXT, vendor_id TEXT, item TEXT, amount INTEGER, status TEXT DEFAULT 'pending', "
                "paid_at TEXT DEFAULT '', paid_ref TEXT DEFAULT '', created_at TEXT, created_by TEXT DEFAULT '')")

def save_vendor(con, p, by):
    ensure(con)
    name = str(p.get('name', '')).strip()[:60]
    if not name:
        raise ValueError('請填廠商名稱')
    f = {'name': name, 'category': str(p.get('category', ''))[:20], 'phone': str(p.get('phone', ''))[:30], 'bank': str(p.get('bank', ''))[:120],
         'notes': str(p.get('notes', ''))[:300], 'status': 'paused' if p.get('status') == 'paused' else 'active'}
    if p.get('id'):
        con.execute('UPDATE vendors SET name=?,category=?,phone=?,bank=?,notes=?,status=?,version=version+1 WHERE id=?', (*f.values(), p['id']))
        vid = p['id']
    else:
        vid = c.uid('vnd')
        con.execute('INSERT INTO vendors(id,name,category,phone,bank,notes,status,created_at) VALUES(?,?,?,?,?,?,?,?)', (vid, *f.values(), c.now()))
    c.audit(con, 'vendor_save', 'vendors', vid, after={**f, 'by': by})
    return dict(con.execute('SELECT * FROM vendors WHERE id=?', (vid,)).fetchone())

def save_cost(con, p, by):
    ensure(con)
    c.get(con, 'orders', p['order_id'])
    if not con.execute('SELECT 1 FROM vendors WHERE id=?', (p.get('vendor_id', ''),)).fetchone():
        raise ValueError('請選廠商')
    amount = c.integer(p.get('amount'), '金額')
    if amount <= 0:
        raise ValueError('金額要大於 0')
    item = str(p.get('item', '')).strip()[:80] or '外包'
    if p.get('id'):
        r = con.execute('SELECT status FROM vendor_costs WHERE id=?', (p['id'],)).fetchone()
        if not r:
            raise ValueError('找不到這筆')
        if r[0] == 'paid':
            raise ValueError('已付款的不能改，請先跟會計確認')
        con.execute('UPDATE vendor_costs SET vendor_id=?,item=?,amount=? WHERE id=?', (p['vendor_id'], item, amount, p['id'])); cid = p['id']
    else:
        cid = c.uid('vc')
        con.execute('INSERT INTO vendor_costs(id,order_id,vendor_id,item,amount,created_at,created_by) VALUES(?,?,?,?,?,?,?)', (cid, p['order_id'], p['vendor_id'], item, amount, c.now(), by))
    c.audit(con, 'vendor_cost', 'orders', p['order_id'], after={'vendor': p['vendor_id'], 'item': item, 'amount': amount, 'by': by})
    return {'id': cid}

def delete_cost(con, cid, by):
    ensure(con)
    r = con.execute('SELECT * FROM vendor_costs WHERE id=?', (cid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆')
    if r['status'] == 'paid':
        raise ValueError('已付款的不能刪')
    con.execute('DELETE FROM vendor_costs WHERE id=?', (cid,))
    c.audit(con, 'vendor_cost_delete', 'orders', r['order_id'], before=dict(r), after={'by': by})
    return {'deleted': True}

def mark_paid(con, ids, ref, by):
    ensure(con)
    n = 0
    for cid in ids[:200]:
        n += con.execute("UPDATE vendor_costs SET status='paid',paid_at=?,paid_ref=? WHERE id=? AND status='pending'", (c.now(), str(ref)[:120], cid)).rowcount
    c.audit(con, 'vendor_paid', 'vendor_costs', ','.join(ids[:20]), after={'ref': ref, 'by': by, 'count': n})
    return {'paid': n}

def state(con):
    ensure(con)
    costs = c.rows(con, "SELECT vc.*,o.date,o.title,v.name vendor FROM vendor_costs vc LEFT JOIN orders o ON o.id=vc.order_id LEFT JOIN vendors v ON v.id=vc.vendor_id ORDER BY o.date DESC LIMIT 500")
    return {'vendors': c.rows(con, 'SELECT * FROM vendors ORDER BY status,category,name'), 'vendor_costs': costs, 'vendor_categories': CATEGORIES}
