"""推薦給朋友（做＋有推薦獎勵；金額另外問店長）。

① 每位客戶一組推薦碼（同品牌；不放客戶編號在網址）→「我的預約」按「推薦給朋友」→ LINE 分享（帶推薦碼的線上估價連結＋官方 LINE）
② 朋友從連結線上估價 → 報價記下是誰推薦的（不能推薦自己、品牌要一樣）
③ 朋友的報價成立 → 記成「推薦成交」→ 派單管理群一張卡（照店長定的獎勵規則；還沒定就寫「等店長定」）
獎勵怎麼給（折抵哪一筆、多少）由管理層照規則處理，系統不自動扣款。
"""
import json
import secrets
import urllib.parse
import core as c
import integrations as ix

RULE_TOPIC = '推薦獎勵金額'

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS referrals(id TEXT PRIMARY KEY, brand TEXT, referrer_cid TEXT, quote_id TEXT UNIQUE, friend_cid TEXT DEFAULT '', "
                "status TEXT DEFAULT 'pending', created_at TEXT, converted_at TEXT DEFAULT '', note TEXT DEFAULT '')")

def code(con, cid):
    """這位客戶的推薦碼（固定一組）。"""
    k = f'ref_code:{cid}'
    t = ix.setting(con, k)
    if not t:
        t = secrets.token_urlsafe(6)
        ix.set_setting(con, k, t); ix.set_setting(con, f'ref:{t}', cid)
    return t

def share_url(con, acc, cid):
    """LINE 分享網址（朋友收到：線上估價連結＋官方 LINE）。"""
    import line_bot as lb, brands
    name = brands.name(acc)
    text = (f"我在用「{name}」的服務，推薦給你 😊\n拍照就能線上估價：{lb.BASE_URL}/book?b={acc}&r={code(con, cid)}\n"
            f"也可以加官方 LINE 直接問：https://line.me/R/ti/p/{urllib.parse.quote(lb.basic_id(con, acc))}")
    return 'https://line.me/R/share?text=' + urllib.parse.quote(text)

def on_quote(con, qid, acc, r, cid=None):
    """線上估價帶推薦碼 → 記下推薦人（同品牌、不是自己）。回傳推薦人名字或 ''。"""
    ensure(con)
    ref = ix.setting(con, f'ref:{r}') if r and len(r) <= 20 else ''
    if not ref or ref == cid:
        return ''
    cu = con.execute('SELECT * FROM customers WHERE id=?', (ref,)).fetchone()
    import brands
    if not cu or brands.of_customer(dict(cu)) != acc:
        return ''
    con.execute('INSERT OR IGNORE INTO referrals(id,brand,referrer_cid,quote_id,created_at) VALUES(?,?,?,?,?)', (c.uid('ref'), acc, ref, qid, c.now()))
    q = con.execute('SELECT form FROM quotes WHERE id=?', (qid,)).fetchone()
    f = json.loads(q[0] or '{}'); f['ref_by'] = cu['name']
    con.execute('UPDATE quotes SET form=? WHERE id=?', (json.dumps(f, ensure_ascii=False), qid))
    return cu['name']

def rule(con):
    try:
        return (json.loads(ix.setting(con, f'answer:{RULE_TOPIC}') or '{}') or {}).get('answer', '')
    except ValueError:
        return ''

def on_signed(con, q):
    """朋友的報價成立 → 推薦成交（每張報價一次）→ 派單管理群一張卡。"""
    ensure(con)
    r = con.execute("SELECT * FROM referrals WHERE quote_id=? AND status='pending'", (q['id'],)).fetchone()
    if not r:
        return False
    if q.get('customer_id') == r['referrer_cid']:
        con.execute("UPDATE referrals SET status='void',note='推薦人自己' WHERE id=?", (r['id'],))
        return False
    con.execute("UPDATE referrals SET status='converted',friend_cid=?,converted_at=? WHERE id=?", (q.get('customer_id') or '', c.now(), r['id']))
    import line_bot as lb
    who = (con.execute('SELECT name,phone FROM customers WHERE id=?', (r['referrer_cid'],)).fetchone() or ['', ''])
    f = json.loads(q.get('form') or '{}')
    old = q.get('customer_id') and con.execute("SELECT 1 FROM orders WHERE customer_id=? AND status='completed' AND id!=?", (q['customer_id'], q.get('order_id') or '')).fetchone()
    lb.admin_notify(con, f"🎁 推薦成交：{who[0]} 推薦的朋友成立報價" + ('（老客戶）' if old else ''), [('推薦人', f"{who[0]} {who[1] or ''}".strip()), ('朋友', f.get('company') or f.get('name') or '—'),
                    ('報價', q['id']), ('獎勵', rule(con) or '還沒定（已請店長決定金額），先記錄')] + ([('⚠️ 注意', '這位朋友之前就是我們的客戶，獎勵請斟酌')] if old else []), f"/#q/{q['id']}", '#2E7D32')      # 稽核 L4
    c.audit(con, 'referral_converted', 'quotes', q['id'], after={'referrer': r['referrer_cid']})
    return True

def counts(con):
    """中控台：每位客戶推薦了幾位、成交幾位。"""
    ensure(con)
    out = {}
    for cid, st, n in con.execute('SELECT referrer_cid,status,COUNT(*) FROM referrals GROUP BY referrer_cid,status'):
        d = out.setdefault(cid, {'sent': 0, 'won': 0})
        d['sent'] += n
        if st == 'converted':
            d['won'] += n
    return out
