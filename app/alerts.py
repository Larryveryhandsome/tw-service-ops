"""異動與風險通知：同一件事只通知一次（alerts_sent 記錄）。不發客戶。
起一律發派單管理群（LINE）；手機推播預設關閉（notify.staff）。"""
import datetime as dt
import core as c
import notify

def _once(con, key):
    if con.execute('SELECT 1 FROM alerts_sent WHERE key=?', (key,)).fetchone():
        return False
    con.execute('INSERT INTO alerts_sent(key) VALUES(?)', (key,))
    return True

def _staff(con, key, title, body, url):
    if _once(con, key):
        notify.staff(con, title, body, url)
        if key.split(':')[0] == 'morning':              # 早上「今天服務」：每場一行（人員、排定、開門），按鈕開中控台首頁
            import line_bot, live
            line_bot.admin_notify(con, title, live.morning_rows(con) or [('內容', body)], '/#home', '#0A84FF')
        elif key.split(':')[0] in ('late', 'short', 'custconf', 'undone'):
            import line_bot
            kind, ref = key.split(':')[0], (key.split(':') + [''])[1]
            oid = ref if kind in ('short', 'custconf') else (con.execute('SELECT order_id FROM assignments WHERE id=?', (ref,)).fetchone() or [None])[0] if kind == 'late' else None
            line_bot.admin_notify(con, title, [('內容', body)], url, '#C47F00', oid=oid)
        con.execute('INSERT INTO alert_log(key,title,body) VALUES(?,?,?)', (key, title, body))
        return 1
    return 0

def _name(con, o):
    s = con.execute('SELECT name FROM sites WHERE id=?', (o['site_id'],)).fetchone() if o['site_id'] else None
    return (s[0] if s else '') or o['title']

def check(con, now=None):
    """背景每分鐘呼叫。回傳本次送出幾則。"""
    now = now or dt.datetime.now(c.TZ)
    today = now.date().isoformat(); hm = now.strftime('%H:%M'); sent = 0
    live = c.rows(con, "SELECT * FROM orders WHERE status IN ('review','draft','confirmed') AND date BETWEEN ? AND ? AND start!='' AND COALESCE(review_note,'')!='示範工單'",
                  (today, (now.date() + dt.timedelta(days=1)).isoformat()))
    for o in live:
        try:
            start = dt.datetime.fromisoformat(f"{o['date']}T{o['start']}:00+08:00")
        except ValueError:             # 稽核：一張時間格式錯（例 9:30）不能讓全站警報停擺
            continue
        left = (start - now).total_seconds() / 3600
        acc = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        when = f"{o['date'][5:].replace('-', '/')} {o['start']}"
        if acc < o['required_workers'] and 0 < left <= 24:
            tier = '3h' if left <= 3 else '24h'
            sent += _staff(con, f"short:{o['id']}:{tier}", '⚠️ 還沒找到人' + ('（3 小時內）' if tier == '3h' else ''),
                           f"{when} {_name(con, o)} 缺 {o['required_workers'] - acc} 人", '/#dispatch')
        if o['status'] in ('review', 'draft') and not o['customer_confirmed'] and 0 < left <= 24:
            sent += _staff(con, f"custconf:{o['id']}", '客戶還沒確認明天的服務', f"{when} {_name(con, o)}", '/#home')
        import board
        if board.goes(o) and -2 <= left <= -0.25:     # 開工後 15 分鐘～2 小時（補人中照常出發的人也算）
            for a in c.rows(con, "SELECT a.*,w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (o['id'],)):
                if not board.goes(o, a['worker_id']):
                    continue
                has_app = con.execute("SELECT 1 FROM users WHERE worker_id=? AND active=1 UNION SELECT 1 FROM line_users WHERE worker_id=?",
                                      (a['worker_id'], a['worker_id'])).fetchone()      # App 或 LINE 工作頁都能打卡
                arrived = con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!=''", (a['id'],)).fetchone()
                told = con.execute("SELECT detail FROM dispatch_log WHERE order_id=? AND action='worker_late' AND detail LIKE ? ORDER BY id DESC LIMIT 1", (o['id'], a['name'] + ' 約晚%')).fetchone()
                late_m = int((told[0].split(' 約晚 ')[1].split(' ')[0]) if told else 0)
                if has_app and not arrived and -left * 60 >= late_m + 5:          # 說了會晚 N 分：過了 N+5 分才通報
                    sent += _staff(con, f"late:{a['id']}", '⏰ 夥伴還沒打到場卡', f"{when} {_name(con, o)}：{a['name']} 開工 {int(-left * 60)} 分鐘還沒到場" + (f"（說會晚 {late_m} 分）" if late_m else ''), '/#home')
    if hm >= '21:00':
        import board
        undone = c.rows(con, f"SELECT o.* FROM orders o WHERE {board.GO_ORDER_SQL} AND o.date<=? AND o.end!='' AND COALESCE(o.review_note,'')!='示範工單' ORDER BY o.date,o.start", (today,))
        undone = [o for o in undone if o['date'] < today or o['end'] <= hm]
        if undone:
            sent += _staff(con, f"undone:{today}", f'今天還有 {len(undone)} 場沒完工登記',
                           '、'.join(f"{o['date'][5:].replace('-', '/')} {_name(con, o)}" for o in undone[:5]), '/#home')
    if hm >= '08:30':
        t = c.rows(con, "SELECT * FROM orders WHERE date=? AND status IN ('review','draft','confirmed')", (today,))
        short = sum(1 for o in t if con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0] < o['required_workers'])
        if t:
            sent += _staff(con, f"morning:{today}", f'早安，今天 {len(t)} 場服務', (f'其中 {short} 場還缺人。' if short else '人都到齊了。') + '打開「今天」看細節。', '/#home')
    return sent

def change(con, o_before, o_after, actor):
    """工單有異動（改期、取消、人數變動）就通知同事，並用 LINE 通知客戶與夥伴。"""
    if not o_before:
        return
    import line_bot, payments
    payments.on_order_change(con, o_before, o_after, actor)          # 收款單跟著工單（取消作廢、改期移期限）
    if o_before.get('status') == 'archived' and o_after.get('status') == 'archived':
        return                           # 歷史紀錄補客戶／案場（待整理「修改」、工單補資料）不是改期，不能發「預約時間已更新」給業主、也不吵管理群
    if (o_before.get('entry_mode') or '') != (o_after.get('entry_mode') or ''):        # 工單改了開門方式 → 舊的開門確認不算數
        con.execute("UPDATE orders SET door_ack='',door_open='',door_req='' WHERE id=?", (o_after['id'],))
    if o_before.get('date') != o_after.get('date') or o_before.get('start') != o_after.get('start'):       # 稽核：改期 → 舊的「會來開門」確認、照常出發名單都不算數
        con.execute("UPDATE orders SET door_ack='',door_open='',door_req='',go_partial='' WHERE id=?", (o_after['id'],))
        con.execute("DELETE FROM alerts_sent WHERE key LIKE ? OR key=?", (f"short:{o_after['id']}:%", f"custconf:{o_after['id']}"))      # 稽核：改期後缺人、客戶未確認的提醒重新算
    if o_after['status'] == 'completed' and o_before['status'] == 'completed':
        pass                             # 完工後改時間（延時）只是補紀錄，不通知業主、夥伴
    elif (o_after['status'] == 'cancelled' and o_before['status'] != 'cancelled') or any(o_before[k] != o_after[k] for k in ('date', 'start', 'end', 'site_id')):
        line_bot.on_change(con, o_before, o_after)
        import datetime as _dt
        if o_after['status'] in ('review', 'draft', 'confirmed') and (o_before['date'] != o_after['date'] or o_before['start'] != o_after['start']) \
                and o_after['date'] <= (_dt.datetime.now(c.TZ).date() + _dt.timedelta(days=3)).isoformat() and line_bot.admin_group(con) \
                and not getattr(c.CTX, 'no_urgent_card', False) and not __import__('integrations').setting(con, f"urgent:{o_after['id']}"):
            import urgent                    # 業主突然改時間 → 緊急加派或調整人數的選項
            m_ = urgent.staff_card(con, o_after['id'])
            line_bot.queue(con, line_bot.WORK_ACCOUNT, line_bot.admin_group(con), 'urgent_adjust', m_[0]['altText'], m_, o_after['id'])
    elif o_after['status'] == 'confirmed' and any((o_before.get(k) or '') != (o_after.get(k) or '') for k in ('scope', 'worker_note', 'required_workers', 'entry_mode')):      # 備註夥伴看不到，改了不通知
        import board                     # 稽核：已排定的單改了範圍、備註、人數 → 已排的夥伴要知道（以前只看時間、地點）
        what = '、'.join(n for k, n in (('scope', '範圍'), ('notes', '備註'), ('worker_note', '主管提醒'), ('required_workers', '人數'), ('entry_mode', '開門方式')) if (o_before.get(k) or '') != (o_after.get(k) or ''))
        body = board._no_money(o_after.get('scope') or '') if (o_before.get('scope') or '') != (o_after.get('scope') or '') else ''
        for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o_after['id'],)):
            line_bot.to_worker(con, a['worker_id'], 'job_update', f'工作內容更新：{line_bot.when(o_after)}',
                               line_bot._txt(f"📝 {line_bot.when(o_after)} 的工作有更新（{what}）" + (f"：\n{body[:300]}" if body else '，請打開工作頁看最新內容。')), o_after['id'])
    when = f"{o_after['date'][5:].replace('-', '/')} {o_after['start']}"
    if o_after['status'] == 'cancelled' and o_before['status'] != 'cancelled':
        notify.staff(con, '工單取消', f"{when} {_name(con, o_after)}（{actor}）：{o_after['review_note'][-40:]}", '/#home')
        line_bot.admin_notify(con, '🗑 工單取消', [('時間', when), ('誰取消', actor), ('原因', o_after['review_note'][-60:] or '—')], '/#home', '#C62828', oid=o_after['id'])
    elif any(o_before[k] != o_after[k] for k in ('date', 'start', 'end', 'site_id', 'required_workers')):
        notify.staff(con, '工單改期／異動', f"{o_before['date'][5:]} {o_before['start']} → {when} {_name(con, o_after)}（{actor}）", '/#schedule')
        line_bot.admin_notify(con, '📅 工單改期／異動', [('原本', f"{o_before['date'][5:].replace('-', '/')} {o_before['start']}–{o_before['end']}"), ('改成', f"{when}–{o_after['end']}"),
                                                     ('誰改的', actor)], '/#schedule', '#C47F00', oid=o_after['id'])
