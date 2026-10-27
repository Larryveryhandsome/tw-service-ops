"""今天服務・即時狀況（中控台首頁要看到當下最重要的資訊——人到齊了沒、業主開門了沒、到場、服務前／完成照片、離場、驗收）。
中控台首頁「今天服務」區塊、每天早上派單管理群的「今天服務」卡都用這裡。不含金額。"""
import datetime as dt
import core as c
import integrations as ix

DEMO = '示範工單'

def _hm(iso):
    return (iso or '')[11:16]

def _mins_since(iso, now):
    try:
        return (now - dt.datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return 0

def day(con, date=None, now=None):
    """某一天（預設今天）每一場的狀況。每場回傳 stage（目前在哪一步）與 steps（每一步 ok／wait／bad／na）。"""
    now = now or dt.datetime.now(c.TZ)
    date = date or now.date().isoformat()
    import payments
    out = []
    for o in c.rows(con, "SELECT o.*, s.name sname, s.area sarea, COALESCE(NULLIF(o.entry_mode,''),s.entry_mode) entry_mode_eff FROM orders o LEFT JOIN sites s ON s.id=o.site_id "
                         "WHERE o.date=? AND o.status NOT IN ('cancelled','archived') AND COALESCE(o.review_note,'')!=? ORDER BY o.start, o.id", (date, DEMO)):
        crew = c.rows(con, "SELECT a.id aid, a.worker_id, w.name, COALESCE(ck.arrived_at,'') arrived, COALESCE(ck.left_at,'') gone FROM assignments a "
                           "JOIN workers w ON w.id=a.worker_id LEFT JOIN checkins ck ON ck.assignment_id=a.id WHERE a.order_id=? AND a.status='accepted' ORDER BY w.name", (o['id'],))
        need = o['required_workers'] or 1
        ph = {r[0]: r[1] for r in con.execute('SELECT label, COUNT(*) FROM attachments WHERE order_id=? GROUP BY label', (o['id'],))}
        approved = bool(con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='photo_approved'", (o['id'],)).fetchone())
        sent = bool(con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='finish_c' AND status NOT LIKE 'skipped%'", (o['id'],)).fetchone())
        start = dt.datetime.fromisoformat(f"{o['date']}T{o['start'] or '00:00'}:00").replace(tzinfo=c.TZ)
        started = now >= start
        arrived = [x for x in crew if x['arrived']]
        left = [x for x in crew if x['gone']]
        done = o['status'] == 'completed'
        steps = []
        # ① 派工（誰排了這場；跟下面「到場」分開，負責人 「人員到齊」會跟「到場」搞混）
        steps.append(('派工', 'ok', f"已安排 {'、'.join(x['name'] for x in crew)}" + (f"（{len(crew)} 人）" if need > 1 else '')) if len(crew) >= need else
                     ('派工', 'bad', f"還缺 {need - len(crew)} 人" + (f"（已排 {'、'.join(x['name'] for x in crew)}）" if crew else '')))
        # ② 排定
        if o['status'] in ('confirmed', 'completed'):
            steps.append(('排定', 'ok', '已排定，地址已給夥伴'))
        else:
            why = ('業主還沒確認時間' if not o['customer_confirmed'] else '等派單管理群審核人員' if len(crew) >= need and not o['crew_ok']
                   else '還在找人' if len(crew) < need else '等收款確認' if not payments.cleared_for_schedule(con, o['id']) else '等排定')
            steps.append(('排定', 'bad' if started else 'wait', why))
        # ③ 開門
        em = o['entry_mode_eff'] or ''
        import door
        if em in door.NO_DOOR and not (o['door_req'] and not o['door_open']):          # 按門鈴／密碼鎖／管理室 → 打卡＝已進入（稽核 F1：夥伴說進不去、等業主回覆時照常顯示）
            steps.append(('開門', 'ok', door.MODES[em] + '（打卡＝已進入）'))
        elif o['door_open']:
            steps.append(('開門', 'ok', f"業主回覆會開門 {_hm(o['door_open'])}"))
        elif o['door_req'] and con.execute("SELECT 1 FROM attachments WHERE order_id=? AND label='before'", (o['id'],)).fetchone():
            steps.append(('開門', 'ok', '已進門（有服務前照）'))
        elif o['door_req'] and con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='door_escalate'", (o['id'],)).fetchone():
            steps.append(('開門', 'ok' if arrived and done else 'wait', f"{_hm(o['door_req'])} 請業主開門，已請管理群打電話處理"))      # 已交給管理群：不再一直亮紅燈
        elif o['door_req']:
            m = _mins_since(o['door_req'], now)
            steps.append(('開門', 'bad' if m >= 5 else 'wait', f"{_hm(o['door_req'])} 已請業主開門" + ('，業主還沒回應（已請管理群打電話）' if m >= 5 else '，等業主回覆')))
        elif o['door_ack']:
            steps.append(('開門', 'ok', '業主說會在家開門'))
        else:
            steps.append(('開門', 'wait' if em == 'home' else 'na', '業主還沒按「會來開門」' if em == 'home' else '進門方式還沒確認'))
        # ④ 到場：依時間漸進（開工前 1 小時以上＝還早；30 分鐘內看夥伴有沒有回報出門；開工後才算晚，15 分鐘以上或過了他說的晚到時間＝紅）
        if crew:
            eta = _eta(con, o['id'])
            mins = (now - start).total_seconds() / 60          # 負數＝距開工還有幾分
            parts, worst = [], 'ok'
            rank = {'ok': 0, 'na': 1, 'wait': 2, 'bad': 3}
            for x in crew:
                e = eta.get(x['name'], {})
                if x['arrived']:
                    t, s = f"{x['name']} {_hm(x['arrived'])} 到場", 'ok'
                elif done:
                    t, s = f"{x['name']} 沒打到場卡", 'na'
                elif e.get('late') and mins < e['late'] + 5:
                    t, s = f"{x['name']} 說會晚 {e['late']} 分（{_hm(e['late_at'])}）", 'wait'
                elif mins >= 15 or (e.get('late') and mins >= e['late'] + 5):
                    t, s = f"{x['name']} 開工 {int(mins)} 分還沒到", 'bad'
                elif mins >= 0:
                    t, s = f"{x['name']} 開工 {int(mins)} 分，還沒打到場卡", 'wait'
                elif e.get('out') or e.get('near'):
                    t, s = f"{x['name']} {'快到了' if e.get('near') else '已出門'} {_hm(e.get('near') or e.get('out'))}", 'ok'
                elif mins >= -30:
                    t, s = f"{x['name']} 還沒回報出門（{int(-mins)} 分後開工）", 'wait'
                elif mins >= -60:
                    t, s = f"{x['name']} 準備中（1 小時內開工）", 'na'
                else:
                    t, s = f"{x['name']} {o['start']} 開工", 'na'
                parts.append(t); worst = s if rank[s] > rank[worst] else worst
            steps.append(('到場', worst, '、'.join(parts)))
        else:
            steps.append(('到場', 'na', '—'))
        # ⑤ 服務前照片 ⑥ 完成照片
        b, a = ph.get('before', 0), ph.get('after', 0)
        nophoto = __import__('board').no_photo(con, o)          # 稽核 F2：夥伴回報「這裡不能拍照」的這一場也算
        if nophoto:                       # 場內不能拍照（例：客戶03）→ 不亮紅燈
            steps.append(('服務前照', 'ok', '場內不能拍照')); steps.append(('完成照片', 'ok', '場內不能拍照'))
        elif not b and ix.setting(con, f"forgot_before:{o['id']}"):      # 稽核 M4：夥伴已寫「忘了拍」原因 → 等管理層確認薪資，不亮紅燈
            steps.append(('服務前照', 'ok', '已說明忘了拍（等管理層確認薪資）'))
            steps.append(('完成照片', 'ok' if a else 'wait' if arrived else 'na', f'{a} 張' if a else ('還沒拍' if arrived else '—')))
        else:
            nudged = con.execute("SELECT MIN(created_at) FROM line_outbox WHERE order_id=? AND kind IN ('before_nudge','relay_w:before_nudge')", (o['id'],)).fetchone()[0]
            late = now >= dt.datetime.fromisoformat(f"{o['date']}T{o['end'] or '23:59'}:00").replace(tzinfo=c.TZ) + dt.timedelta(hours=2)      # 前後照可以完工時一起交，結束 2 小時後還沒有才亮紅燈
            steps.append(('服務前照', 'ok' if b else 'bad' if late else 'wait' if arrived else 'na', f'{b} 張' if b else ('結束 2 小時了還沒交' if late else ('還沒交（可以完工時一起交）' + (f'，{_hm(nudged)} 已提醒' if nudged else '')) if arrived else '—')))
            steps.append(('完成照片', 'ok' if a else 'bad' if late else 'wait' if arrived else 'na', f'{a} 張' if a else ('結束 2 小時了還沒交' if late else '還沒交' if arrived else '—')))
        # ⑦ 照片傳業主（免審核，離場打卡自動傳）
        if approved or sent:
            steps.append(('照片傳業主', 'ok' if sent else 'bad', '已自動傳給業主' if sent else '業主沒綁 LINE，綁好自動補傳'))
        else:
            steps.append(('照片傳業主', 'wait' if a else 'na', '離場打卡後自動傳' if a else '—'))
        # ⑧ 離場
        if crew:
            steps.append(('離場', 'ok' if len(left) == len(crew) else 'wait' if arrived else 'na',
                          '、'.join(f"{x['name']} {_hm(x['gone'])}" for x in left) or ('還在現場' if arrived else '—')))
        # ⑩ 現場回報（加時、晚到、狀況）：沒處理亮紅燈
        import site_report
        reps = site_report.for_order(con, o['id'])
        if reps:
            op = [r for r in reps if r['status'] == 'open']
            steps.append(('現場回報', 'bad' if op else 'ok', '、'.join(f"{site_report.KINDS[r['kind']][1]}（還沒處理）" for r in op) if op else
                          '、'.join(r['resolution'] for r in reps[-2:])))
        # ⑨ 業主驗收
        if done:
            acc = o['acceptance'] or 'pending'
            mgr = acc == 'accepted' and (o.get('acceptance_note') or '').startswith('主管')          # 主管代為驗收要顯示「主管驗收」
            steps.append(('主管驗收' if mgr else '業主驗收', {'accepted': 'ok', 'disputed': 'bad'}.get(acc, 'wait'),
                          (o.get('acceptance_note') or '主管驗收') if mgr else {'accepted': '業主已確認', 'disputed': '業主要求加強'}.get(acc, '等業主確認')))
        stage = ('已完工' if done else '已離場' if crew and len(left) == len(crew) else '服務中' if arrived else
                 '開工了・還沒到場' if started and o['status'] == 'confirmed' else '待開工' if o['status'] == 'confirmed' else '還沒排定')
        nxt = next((s for s in steps if s[0] == '到場'), None) if crew and not arrived and not done else None          # 還沒到場：最該看的是「到場」進度
        focus = (next((s for s in steps if s[1] == 'bad'), None) or next((s for s in steps if s[1] == 'wait'), None) or nxt
                 or next((s for s in steps if s[1] == 'na' and s[2] != '—'), None))
        out.append({'id': o['id'], 'start': o['start'], 'end': o['end'], 'title': o['sname'] or o['title'], 'area': o['sarea'] or '',
                    'status': o['status'], 'stage': stage, 'steps': [{'k': k, 's': s, 't': t} for k, s, t in steps],
                    'focus': {'k': focus[0], 's': focus[1], 't': focus[2]} if focus else {'k': '全部完成', 's': 'ok', 't': '這場都處理好了'},
                    'alerts': sum(1 for _, s, _ in steps if s == 'bad')})
    return out

def _eta(con, oid):
    """夥伴在 LINE 回報的路上狀況：已出門／快到了（worker_eta）、會晚到 N 分（worker_late）。{名字: {...}}"""
    out = {}
    for d, a, at in con.execute("SELECT detail, action, at FROM dispatch_log WHERE order_id=? AND action IN ('worker_eta','worker_late') ORDER BY id", (oid,)):
        at_local = (dt.datetime.fromisoformat(at.replace(' ', 'T')).replace(tzinfo=dt.timezone.utc).astimezone(c.TZ).isoformat() if at else '')   # dispatch_log.at 是 UTC
        if a == 'worker_eta':
            name, _, v = (d or '').partition('|')
            out.setdefault(name, {})[v] = at_local
        else:
            name, _, rest = (d or '').partition(' 約晚 ')
            try:
                out.setdefault(name, {}).update(late=int(rest.split(' ')[0]), late_at=at_local)
            except ValueError:
                pass
    return out

def morning_rows(con, date=None):
    """早上派單管理群卡用：每場一行。"""
    rows = []
    for x in day(con, date):
        st = {s['k']: s for s in x['steps']}
        bits = [st['派工']['t'], st['排定']['t'] if st['排定']['s'] != 'ok' else '已排定', st['開門']['t']]
        rows.append((f"{x['start']} {x['title'][:10]}", '・'.join(bits)))
    return rows
