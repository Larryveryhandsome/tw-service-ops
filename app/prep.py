"""案件前提醒事項（店長前一天會傳夥伴「現場沒有工具、老闆會開門、垃圾裝好放一樓」這類提醒 → 系統化）。

做法由店長決定（點選題「夥伴提醒方式」，decisions 自動套用 prep_mode）：
- all：每天 15:00 一張卡（明天每一場一格）問店長要不要補充
- new：只問「第一次去的案場」
- off：不問（她自己傳）
卡片先列出系統已經知道的（範圍、開門方式、進場說明、已有提醒），她只補沒寫到的 → 去重。
她打的字存到工單「給夥伴的提醒」（orders.worker_note），18:00 夥伴的明日工作卡一起帶出；18:00 之後才補的 → 另外推給夥伴。
"""
import datetime as dt
import core as c
import integrations as ix

def ensure(con):
    if 'worker_note' not in {r[1] for r in con.execute('PRAGMA table_info(orders)')}:
        con.execute("ALTER TABLE orders ADD COLUMN worker_note TEXT DEFAULT ''")

def first_visit(con, o):
    return not con.execute("SELECT 1 FROM orders WHERE site_id=? AND status IN ('completed','archived') AND id!=?", (o['site_id'], o['id'])).fetchone()

def digest(con, force=False):
    """每天 15:00：明天的場次問店長要不要補充提醒（依 prep_mode）。"""
    import line_bot as lb, reviews, door
    ensure(con)
    mode = ix.setting(con, 'prep_mode') or 'off'
    now = dt.datetime.now(c.TZ)
    if not force and (mode == 'off' or now.strftime('%H:%M') < '15:00' or ix.setting(con, 'prep_day') == now.date().isoformat()):
        return 0
    ix.set_setting(con, 'prep_day', now.date().isoformat())
    day = (now.date() + dt.timedelta(days=1)).isoformat()
    bs = []
    for o in c.rows(con, "SELECT * FROM orders WHERE date=? AND status IN ('review','draft','confirmed') ORDER BY start", (day,)):
        if mode == 'new' and not first_visit(con, o):
            continue
        o2, s, cu, crew, area = lb.order_ctx(con, o['id'])
        rows = [lb._row('時間', f"{o['start']}–{o['end']}"), lb._row('地點', f"{s.get('name') or ''} {s.get('address') or ''}"[:60]),
                lb._row('夥伴', '、'.join(a['name'] for a in crew) or '還沒排'), lb._row('範圍', lb.scope_text(o, 80) or '—'),
                lb._row('開門', door.mode_text(s, o)), lb._row('進場說明', (s.get('entry_notes') or '—')[-60:])]
        if o.get('worker_note'):
            rows.append(lb._row('已有提醒', o['worker_note'][-80:]))
        if first_visit(con, o):
            rows.insert(0, lb._t('第一次去這個案場', color='#C47F00', weight='bold', size='sm'))
        bs.append(lb._bubble(lb._header('明天的工作・要提醒夥伴嗎？', f"{o['start']} {s.get('name') or o['title']}"[:30], '#0A84FF'), rows,
                             [lb._btn('沒有要補充 ✅', data=f"a=prep_ok&o={o['id']}", primary=True), lb._btn('補充提醒（打字）', data=f"a=prep_add&o={o['id']}")]))
    if not bs:
        return 0
    m = lb._carousel(f'明天 {len(bs)} 場：要提醒夥伴什麼嗎？', bs)
    intro = {'type': 'text', 'text': f'📝 明天 {len(bs)} 場。上面列了系統已知的範圍、開門方式、進場說明；有沒寫到的（例如現場沒有工具、垃圾怎麼處理、老闆幾點到）按「補充提醒」打字，18:00 會跟夥伴的明日工作卡一起發。'}
    lb.to_managers(con, 'prep', m[0]['altText'], [intro] + m)          # 送管理群 @店長
    return len(bs)

def add_note(con, oid, text, by):
    """店長補充的提醒 → 存工單；18:00 的明日工作卡已經發了 → 另外推給夥伴。"""
    import line_bot as lb, board
    ensure(con)
    o = c.get(con, 'orders', oid)
    note = text.strip()[:300]
    con.execute("UPDATE orders SET worker_note=TRIM(COALESCE(worker_note,'') || ? || ?) WHERE id=?", ('\n' if o.get('worker_note') else '', note, oid))
    board.log(con, oid, 'worker_note', by)
    sent = con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind IN ('remind_w','relay_w:remind_w')", (oid,)).fetchone()      # 沒綁的夥伴由管理群轉達
    if sent or o['date'] <= dt.datetime.now(c.TZ).date().isoformat():
        for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)):
            lb.to_worker(con, a['worker_id'], 'worker_note', '主管補充提醒', lb._txt(f"📝 {lb.when(dict(o))} 主管補充提醒：\n{note}"), oid)
        return '✅ 已記下，也已直接傳給夥伴'
    return '✅ 已記下，18:00 會跟夥伴的明日工作卡一起發'
