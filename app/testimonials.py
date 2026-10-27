"""客戶見證。

服務完 → 客戶確認完工、給 4–5 星（滿意）→ 派單管理群收到「客戶很滿意」卡（附服務前／完成照片）
→ 我們到官方 LINE 親自回覆謝謝，按「已親自回覆」記錄（誰回、幾點）。
客戶願意寫幾句話 → 問可不可以不具名公開（官網、社群）→ 中控台「客戶見證」頁整理，可以拿來當行銷素材。
"""
import datetime as dt
import core as c

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS testimonials(id TEXT PRIMARY KEY, order_id TEXT UNIQUE, customer_id TEXT, brand TEXT DEFAULT 'demoshop', stars INTEGER, "
                "text TEXT DEFAULT '', consent TEXT DEFAULT '', status TEXT DEFAULT 'new', created_at TEXT, replied_by TEXT DEFAULT '', replied_at TEXT DEFAULT '')")

def photo_ids(con, oid):
    """服務前最多 4 張、完工最多 5 張（一張 LINE 卡最多 10 頁，第一頁是說明）。"""
    ids = lambda label, n, newest=False: [r[0] for r in con.execute(f"SELECT id FROM attachments WHERE order_id=? AND label=? ORDER BY created_at {'DESC' if newest else ''} LIMIT ?", (oid, label, n))]
    return ids('before', 4), ids('after', 5, True)[::-1]          # 稽核：完成照片取最新的（補拍的才會傳給業主）

def on_rating(con, oid, stars, brand):
    """客戶給 4–5 星：建一筆見證、送派單管理群等人親自回覆。同一筆工單只建一次。回傳見證編號。"""
    ensure(con)
    old = con.execute('SELECT id FROM testimonials WHERE order_id=?', (oid,)).fetchone()
    if old:
        return old[0]
    o = c.get(con, 'orders', oid)
    tid = c.uid('tm')
    con.execute('INSERT INTO testimonials(id,order_id,customer_id,brand,stars,created_at) VALUES(?,?,?,?,?,?)', (tid, oid, o['customer_id'], brand, stars, c.now()))
    card(con, tid, '🌟 客戶很滿意')
    return tid

def add_text(con, tid, text):
    ensure(con)
    con.execute('UPDATE testimonials SET text=? WHERE id=?', (text.strip()[:500], tid))
    card(con, tid, '💬 客戶寫了見證')

def set_consent(con, tid, ok):
    ensure(con)
    con.execute('UPDATE testimonials SET consent=? WHERE id=?', ('public' if ok else 'private', tid))
    c.audit(con, 'testimonial_consent', 'testimonials', tid, after={'consent': 'public' if ok else 'private'})

def mark_replied(con, tid, who):
    ensure(con)
    t = con.execute('SELECT * FROM testimonials WHERE id=?', (tid,)).fetchone()
    if not t:
        return '找不到這則見證'
    if t['status'] == 'replied':
        return f"{t['replied_by']} 已經回覆過了（{t['replied_at'][5:16].replace('T', ' ')}）"
    con.execute("UPDATE testimonials SET status='replied',replied_by=?,replied_at=? WHERE id=?", (who, c.now(), tid))
    return f'✅ 已記錄：{who} 親自回覆謝謝'

def card(con, tid, kicker):
    import line_bot as lb, worker_portal, brands
    g = lb.admin_group(con)
    t = con.execute('SELECT * FROM testimonials WHERE id=?', (tid,)).fetchone()
    if not g or not t:
        return
    o = c.get(con, 'orders', t['order_id'])
    who = con.execute('SELECT display_name FROM line_users WHERE customer_id=? ORDER BY last_seen DESC LIMIT 1', (t['customer_id'],)).fetchone()
    before, after = photo_ids(con, o['id'])
    urls = [worker_portal.photo_url(con, i) for i in before + after]
    labels = ['服務前'] * len(before) + ['完工'] * len(after)
    rows = lb.order_people(con, o['id']) + [('品牌', brands.name(t['brand'])), ('LINE 名稱', who[0] if who else '—'), ('工作', lb.when(dict(o))), ('評分', '⭐' * (t['stars'] or 0))]
    if t['text']:
        rows.append(('客戶說', t['text'][:200]))
    rows.append(('照片', f'服務前 {len(before)} 張・完工 {len(after)} 張' + ('' if before else '（沒有服務前照片）')))
    m = lb.card_photos(f"{kicker}：{lb.order_people(con, o['id'])[0][1]}", kicker, f"{lb.md(o['date'])} {o['title'][:14]}", urls,
                       [lb._row(k, v) for k, v in rows] + [lb._t('請到官方 LINE 親自回覆謝謝關心，回完按下面的按鈕記錄（避免重複回）。', color=lb.SUB, size='xs', wrap=True, margin='md')],
                       [lb._btn('已親自回覆 ✅', data=f'a=tm_done&t={tid}', primary=True), lb._btn('打開工單', uri=f"{lb.BASE_URL}/#o/{o['id']}"), lb._btn('看客戶見證（中控台）', uri=f'{lb.BASE_URL}/#testimonials')], '#2E7D32', labels=labels)
    lb.queue(con, lb.WORK_ACCOUNT, g, 'testimonial', m[0]['altText'], [lb.ping(con, '客戶很滿意，請到官方 LINE 親自回覆謝謝 👇')] + m, o['id'])

def state(con):
    ensure(con)
    out = []
    for t in c.rows(con, "SELECT t.*, cu.name customer, o.date, o.title FROM testimonials t LEFT JOIN customers cu ON cu.id=t.customer_id LEFT JOIN orders o ON o.id=t.order_id "
                         "ORDER BY t.created_at DESC LIMIT 200"):
        b, a = photo_ids(con, t['order_id'])
        out.append({**t, 'before': b, 'after': a})
    return out

def public(con, brand):
    """官方網站用：只給客戶同意不具名分享（consent=public）、有寫字、4–5 星的；不含姓名、地址、照片。"""
    ensure(con)
    out = []
    for t in c.rows(con, "SELECT t.text, t.stars, t.created_at, s.service_type FROM testimonials t LEFT JOIN orders o ON o.id=t.order_id LEFT JOIN sites s ON s.id=o.site_id "
                         "WHERE t.consent='public' AND t.brand=? AND t.text!='' AND t.stars>=4 ORDER BY t.created_at DESC LIMIT 12", (brand,)):
        out.append({'text': t['text'][:200], 'stars': t['stars'], 'month': (t['created_at'] or '')[:7].replace('-', ' 年 ') + ' 月' if t['created_at'] else '', 'service': t['service_type'] or ''})
    return out

def pending(con):
    """還沒有人親自回覆的滿意客戶（給每日追蹤）。"""
    ensure(con)
    return c.rows(con, "SELECT t.*, cu.name customer FROM testimonials t LEFT JOIN customers cu ON cu.id=t.customer_id WHERE t.status='new' AND t.created_at<=? ORDER BY t.created_at",
                  ((dt.datetime.now(c.TZ) - dt.timedelta(hours=6)).isoformat(),))
