"""報價成立時，這位業主同一個地點附近日期已經有排好人的工單（例如舊單已排好人，
業主簽了新報價又建一張新工單，系統若馬上廣發派工，就得手動取消舊單、把人搬過去）。

→ 新工單先「擋派工」，派單管理群一張卡：
   ［沿用原本的人員］＝舊單已接的人搬到新單（照常排定流程）、舊單安靜取消（不通知業主、夥伴；工單紀錄寫原因）
   ［兩張都要］＝放開新單照常找人
"""
import datetime as dt
import core as c
import integrations as ix

HOLD = '報價成立：等管理群決定要不要沿用原本工單的人員'

def old_order(con, o):
    if not o.get('customer_id') or not o.get('date'):
        return None
    d = dt.date.fromisoformat(o['date'])
    for r in c.rows(con, "SELECT o.* FROM orders o WHERE o.customer_id=? AND o.site_id=? AND o.id!=? AND o.status IN ('review','draft','confirmed') AND o.date BETWEEN ? AND ? "
                         "AND NOT EXISTS(SELECT 1 FROM quotes q WHERE q.order_id=o.id) ORDER BY ABS(julianday(o.date)-julianday(?))",
                    (o['customer_id'], o['site_id'], o['id'], (d - dt.timedelta(days=14)).isoformat(), (d + dt.timedelta(days=14)).isoformat(), o['date'])):
        if con.execute("SELECT 1 FROM assignments WHERE order_id=? AND status='accepted'", (r['id'],)).fetchone():
            return r
    return None

def check(con, o):
    """報價成立建好工單後呼叫。有舊單 → 擋派工＋管理群卡。回傳舊單 id 或 None。"""
    import line_bot as lb
    o = dict(o); old = old_order(con, o)
    if not old:
        return None
    ix.set_setting(con, f"dispatch_hold:{o['id']}", HOLD)
    crew = [r[0] for r in con.execute("SELECT w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (old['id'],))]
    m = lb.flex('報價成立：要沿用原本工單的人員嗎？', lb._bubble(lb._header('要沿用原本的人員嗎？', lb.when(o), '#C47F00'),
        [lb._row('新工單', lb.when(o) + '（報價成立）'), lb._row('原本的', lb.when(dict(old)) + '（' + '、'.join(crew) + '）'),
         lb._t('新工單先不自動派人，等你們選。', wrap=True, size='sm', color=lb.SUB, margin='md')],
        [lb._btn('沿用原本的人員', data=f"a=qlink&o={o['id']}&x={old['id']}&v=move", primary=True), lb._btn('兩張都要（新單照常找人）', data=f"a=qlink&o={o['id']}&x={old['id']}&v=both"),
         lb._btn('打開新工單', uri=f"{lb.BASE_URL}/#o/{o['id']}")],
        '沿用：原本的人搬到新工單，舊工單安靜取消（不通知業主和夥伴）。'))
    lb.to_managers(con, 'qlink', m[0]['altText'], m, o['id'], tag='報價成立了，這位業主原本已有排好人的工單，要沿用嗎？👇')
    import board; board.log(con, o['id'], 'qlink_ask', f"原本的工單 {old['id']}（{'、'.join(crew)}）")
    return old['id']

def decide(con, oid, old_id, v, by):
    import board, line_bot as lb
    if (ix.setting(con, f'dispatch_hold:{oid}') or '') != HOLD:
        return '這張已經處理過了'
    ix.set_setting(con, f'dispatch_hold:{oid}', '')
    if v != 'move':
        board.log(con, oid, 'qlink_both', by); return '好，新工單照常找人 👍'
    o, old = c.get(con, 'orders', oid), c.get(con, 'orders', old_id)
    moved = []
    for a in c.rows(con, "SELECT * FROM assignments WHERE order_id=? AND status='accepted'", (old_id,)):
        if con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, a['worker_id'])).fetchone():
            continue
        ex = con.execute('SELECT id,version FROM assignments WHERE order_id=? AND worker_id=?', (oid, a['worker_id'])).fetchone()
        if ex:
            con.execute("UPDATE assignments SET status='accepted',notes=COALESCE(notes,'')||'（沿用原本工單）',version=version+1 WHERE id=?", (ex['id'],))
        else:
            con.execute("INSERT INTO assignments(id,order_id,worker_id,status,pay_mode,rate,amount,notes) VALUES(?,?,?,?,?,?,?,?)",
                        (c.uid('ass'), oid, a['worker_id'], 'accepted', a['pay_mode'], a['rate'], a['amount'], '沿用原本工單（' + by + '）'))
        moved.append(a['worker_id'])
    con.execute("UPDATE assignments SET status='cancelled',notes=COALESCE(notes,'')||'（搬到新工單）',version=version+1 WHERE order_id=? AND status IN ('accepted','invited')", (old_id,))
    con.execute("UPDATE orders SET status='cancelled',review_note=COALESCE(review_note,'')||?,version=version+1 WHERE id=?", (f'｜報價成立改用新工單 {oid}（{by}）', old_id))
    con.execute("UPDATE assignments SET status='cancelled',notes=COALESCE(notes,'')||'（人員已沿用，不用回覆）',version=version+1 WHERE order_id=? AND status='invited'", (oid,))
    crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (oid,)).fetchone()[0]
    if crew >= (o['required_workers'] or 1):
        con.execute("UPDATE orders SET dispatch='filled',crew_ok=?,version=version+1 WHERE id=?", (f'沿用原本工單（{by}）', oid))
        note = lb._auto_confirm(con, oid) if hasattr(lb, '_auto_confirm') else ''
    else:
        note = f'還缺 {(o["required_workers"] or 1) - crew} 位，系統照分級找人'
    board.log(con, oid, 'qlink_move', f"{len(moved)} 位（{by}）"); board.log(con, old_id, 'qlink_cancel', f'改用新工單 {oid}')
    c.audit(con, 'qlink_move', 'orders', oid, after={'old': old_id, 'moved': moved, 'by': by})
    return f'✅ 已沿用 {len(moved)} 位，舊工單已取消（沒通知業主、夥伴）' + (f'；{note}' if note else '')
