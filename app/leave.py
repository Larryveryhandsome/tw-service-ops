"""夥伴請假＋派員審核＋案場異動通知。

請假：夥伴在 LINE 點「臨時請假」→ 選原因 → 系統先把他從這場拿掉（重新派工不會再問他）→ 派單管理群收到卡片：
  ① 判斷：初次不記點／通融不記點／記點（半年內滿 3 點 → 自動停派半年）
  ② 處理：讓業主選（等原人員／改期／另外派人）、通知業主並另外派工、只另外派工（不通知業主）
派員審核：夥伴接單人到齊 → 先給派單管理群看「是誰、經歷、請假紀錄」→ 按「人員 OK」才通知業主（後台直接指派的視為已審核）。
案場異動：地址、進場方式、注意事項改了 → 自動通知接下來有排這個案場的夥伴。
"""
import datetime as dt
import core as c
import integrations as ix

POINT_LIMIT = 3          # 半年內記點滿幾點停派
POINT_DAYS = 183
REASONS = ['生病', '家裡有事', '交通問題', '其他']
KIND = {'first': '初次・不記點', 'excuse': '通融・不記點', 'point': '記點', 'ours': '我方調整・不算請假', 'withdrawn': '已撤回（重新排回）'}
NOT_LEAVE = ('ours', 'withdrawn')        # 不算請假次數（我們改時間／合併工單、或後來又排回去）
HOW = {'ask': '讓業主選擇', 'notify': '通知業主＋另外派工', 'redispatch': '只另外派工', 'self': '管理層自己找人（先不要自動派）'}

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS worker_leaves(id TEXT PRIMARY KEY, created_at TEXT, worker_id TEXT, order_id TEXT, reason TEXT, "
                "status TEXT DEFAULT 'pending', decided_by TEXT DEFAULT '', decided_at TEXT DEFAULT '', handling TEXT DEFAULT '', handled_by TEXT DEFAULT '', late INTEGER DEFAULT 0)")
    cols = {r[1] for r in con.execute('PRAGMA table_info(orders)')}
    if 'go_partial' not in cols:       # 兩人以上的單有人請假：留下的人照常出發（負責人 ）；有值＝退回待確認補人中，但已接的人照常
        con.execute("ALTER TABLE orders ADD COLUMN go_partial TEXT DEFAULT ''")
    if 'crew_ok' not in cols:          # 派員審核：管理群按「人員 OK」的人與時間；空白＝還沒審
        con.execute("ALTER TABLE orders ADD COLUMN crew_ok TEXT DEFAULT ''")
        con.execute("""UPDATE orders SET crew_ok='既有（上線前已安排）' WHERE status IN ('review','draft','confirmed')
                       AND (SELECT COUNT(*) FROM assignments a WHERE a.order_id=orders.id AND a.status='accepted') >= COALESCE(required_workers,1)""")

def history(con, wid):
    ensure(con)
    since = (dt.datetime.now(c.TZ) - dt.timedelta(days=POINT_DAYS)).isoformat()
    n = con.execute("SELECT COUNT(*) FROM worker_leaves WHERE worker_id=? AND created_at>=? AND status NOT IN ('ours','withdrawn')", (wid, since)).fetchone()[0]
    p = con.execute("SELECT COUNT(*) FROM worker_leaves WHERE worker_id=? AND status='point' AND decided_at>=?", (wid, since)).fetchone()[0]
    return n, p

def reopen(con, oid):
    """已排定的單少了人（請假、換人、10 分鐘內撤回）：退回待確認才能另外派工（稽核：以前留在 confirmed，另外派工會報錯、整筆卡死）。
    時間沒變 → 業主的確認保留；新的人接單、管理群審核過、款項沒問題 → 自動再排定（_auto_confirm）。"""
    o = c.get(con, 'orders', oid)
    if o['status'] == 'confirmed':
        import board; board.set_go_partial(con, oid)          # 每日檢查：換人、撤回、請假都一樣 → 還在的人照常出發
        con.execute("UPDATE orders SET status='review',confirmed_at='',version=version+1 WHERE id=?", (oid,))
        c.audit(con, 'crew_change_reopen', 'orders', oid, o, c.get(con, 'orders', oid))
        return True
    return False

# ───────────── 夥伴請假 ─────────────
def request(con, wid, oid, reason):
    import board, line_bot as lb
    ensure(con)
    a = con.execute("SELECT * FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone()
    if not a:
        raise ValueError('這場你目前沒有排到，不用請假喔 🙏')
    o = c.get(con, 'orders', oid); w = c.get(con, 'workers', wid)
    if (o['review_note'] or '') == '示範工單':          # 稽核 B1：示範工作請假會重新派工、發卡給真的夥伴
        raise ValueError('這是示範工作：真的工作按「臨時請假」會通知管理層、另外找人。示範時不會真的請假 🙏')
    if o['status'] in ('completed', 'cancelled', 'archived'):          # 稽核：舊卡片按到 → 不能把已完工的人拿掉（薪資會歸零）
        raise ValueError('這場已經結束或取消了，不用請假 🙏')
    if con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!='' AND COALESCE(left_at,'')=''", (a['id'],)).fetchone():
        raise ValueError('你已經在現場了，不能請假 🙏 有狀況請按「現場回報」，管理層會處理')
    start = dt.datetime.fromisoformat(f"{o['date']}T{o['start'] or '09:00'}:00").replace(tzinfo=c.TZ)
    late = int(start - dt.datetime.now(c.TZ) < dt.timedelta(hours=24))
    con.execute("UPDATE assignments SET status='declined',notes=COALESCE(notes,'')||?,version=version+1 WHERE id=?", (f'（請假：{reason}）', a['id']))
    con.execute("UPDATE orders SET crew_ok='',dispatch=CASE WHEN dispatch='filled' THEN 'open' ELSE dispatch END,version=version+1 WHERE id=?", (oid,))
    rest = [r[0] for r in con.execute("SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,))]
    if o['status'] == 'confirmed' and rest:          # 負責人 兩人同場一人請假 → 另一位照常出發，系統只補缺的人（名單在 reopen 設）
        for x in rest:
            lb.to_worker(con, x, 'crew_left', '同組夥伴請假，你照常出發', lb._txt(f"{lb.when(dict(o))} 同組的 {w['name']} 請假了，你照常出發 🙏 系統正在補人，有新的夥伴加入會再告訴你。"), oid)
    reopen(con, oid)
    lid = c.uid('lv')
    con.execute('INSERT INTO worker_leaves(id,created_at,worker_id,order_id,reason,late) VALUES(?,?,?,?,?,?)', (lid, c.now(), wid, oid, reason[:200], late))
    board.log(con, oid, 'leave', f"{w['name']}：{reason}")
    c.audit(con, 'worker_leave', 'assignments', a['id'], after={'reason': reason, 'late': late})
    g = lb.admin_group(con)
    if g:
        m = card_admin(con, lid)
        head = [lb.ping(con, f"⚠️ {lb.when(dict(o))} 臨時請假（24 小時內），請管理層盡快選怎麼處理；30 分鐘沒人選，系統會自動通知業主並另外派工 👇")] if late else []
        lb.queue(con, lb.WORK_ACCOUNT, g, 'leave', m[0]['altText'], head + m, oid)          # 稽核：24 小時內的 @管理層
    return lid

def auto_handle(con, minutes=30):
    """稽核：24 小時內的臨時請假，管理層 30 分鐘沒選怎麼處理 → 自動「通知業主＋另外派工」（不然自動派工被擋住，這場就沒人）。"""
    import line_bot as lb
    ensure(con)
    cut = (dt.datetime.now(c.TZ) - dt.timedelta(minutes=minutes)).isoformat(timespec='seconds'); n = 0
    soon = (dt.datetime.now(c.TZ) + dt.timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M')
    for r in c.rows(con, "SELECT l.* FROM worker_leaves l JOIN orders o ON o.id=l.order_id WHERE COALESCE(l.handling,'')='' AND l.status!='withdrawn' "
                         "AND (l.late=1 OR o.date||'T'||COALESCE(NULLIF(o.start,''),'09:00')<=?) "        # 稽核 B3：看現在離開工是否 24 小時內；我方調整的也要有出路
                         "AND o.status IN ('review','draft','confirmed') AND l.created_at<?", (soon, cut)):
        import dispatch
        if dispatch.held(con, r['order_id']):
            continue                                  # 管理層擋住／找人中
        od = con.execute('SELECT dispatch_only FROM orders WHERE id=?', (r['order_id'],)).fetchone()
        mode = ix.setting(con, 'leave_auto_mode') or 'auto'          # 決定（請假後沒選補人方式）
        if (od and od['dispatch_only']) or mode == 'never' or (mode == 'pressed' and r['status'] != 'pending'):
            k = f"lvrem:{r['id']}"; last = ix.setting(con, k) or ''     # 指定邀請的單／管理層已經在處理 → 不自動廣發，提醒選②（30 分鐘一次，最多 4 次）
            cnt = int(last.split('|')[0] or 0) if last else 0
            if cnt < 4 and (not last or last.split('|')[-1] < cut):
                ix.set_setting(con, k, f"{cnt + 1}|{c.now()}")
                why = '（指定邀請的單，系統不會自動發給別人）' if od and od['dispatch_only'] else ''
                head = [lb.ping(con, f"⏱ {lb.when(dict(c.get(con, 'orders', r['order_id'])))} 請假後還沒選「② 這場怎麼處理」{why} 👇")]
                lb.queue(con, lb.WORK_ACCOUNT, lb.admin_group(con) or lb.OWNER_UID, 'leave', '請假還沒選怎麼處理', head + card_admin(con, r['id']), r['order_id'])
            continue
        msg = handle(con, r['id'], 'notify', f'系統（{minutes} 分鐘沒人選）')
        lb.admin_notify(con, '⏱ 臨時請假沒人處理，系統已自動處理', [('做了什麼', msg), ('要改', '中控台「派工」可以再改')], '/#dispatch', '#C47F00', oid=r['order_id'])
        n += 1
    return n

def card_admin(con, lid):
    import line_bot as lb, automation
    r = con.execute('SELECT * FROM worker_leaves WHERE id=?', (lid,)).fetchone()
    o, s, cu, crew, area = lb.order_ctx(con, r['order_id'])
    w = c.get(con, 'workers', r['worker_id'])
    n, p = history(con, w['id'])
    e = automation.experience(con, w['id'])
    ours = any(k in (r['reason'] or '') for k in ('時間異動', '前一場加時'))      # 我們改時間造成的 → 建議不算請假
    rows = [lb._row('夥伴', w['name']), lb._row('工作', lb.when(dict(o))), lb._row('地點', s.get('name') or area or '—'), lb._row('原因', r['reason']),
            lb._row('半年內', f'請假 {n} 次（含這次）・記點 {p} 點'), lb._row('經歷', f"服務 {e['jobs']} 場")]
    if r['late']:
        rows.append(lb._t('⚠️ 服務前 24 小時內才請假', color='#C62828', weight='bold', size='sm', margin='md'))
    judge = lb._bubble(lb._header('🙋 夥伴請假・① 判斷', w['name'], '#C47F00'), rows,
                       ([lb._btn('我方調整・不算請假', data=f'a=lvd&l={lid}&k=ours', primary=True)] if ours else []) +
                       [lb._btn('初次・不記點', data=f'a=lvd&l={lid}&k=first', primary=not ours), lb._btn('通融・不記點', data=f'a=lvd&l={lid}&k=excuse'),
                        lb._btn('記點', data=f'a=lvd&l={lid}&k=point')] + ([] if ours else [lb._btn('我方調整・不算請假', data=f'a=lvd&l={lid}&k=ours')])
                       + [lb._btn('打開工單 ›', uri=f"{lb.BASE_URL}/#o/{r['order_id']}"), lb._btn('看這位夥伴 ›', uri=f"{lb.BASE_URL}/#w/{w['id']}")],      # 點了直接到那一筆
                       f'記點：半年內滿 {POINT_LIMIT} 點自動停派半年。「我方調整」＝我們改時間、合併工單造成，不算請假。系統已先把他從這場拿掉；要排回他，中控台「直接排入」即可（請假自動撤回，已記的點也撤銷）。')
    has_line = bool(lb.customer_target(con, o['customer_id'])[1])
    how = lb._bubble(lb._header('🙋 夥伴請假・② 這場怎麼處理', lb.when(dict(o)), '#C47F00'),
                     [lb._t('讓業主選：業主收到卡片，可選「等原本的人員／改期／請我們另外派人」。', wrap=True, size='sm'),
                      lb._t('通知業主＋另外派工：業主只收到通知，系統馬上問其他夥伴。', wrap=True, size='sm', margin='md'),
                      lb._t('只另外派工：不打擾業主，找到人後照常請管理群審核。', wrap=True, size='sm', margin='md')] +
                     ([] if has_line else [lb._t('⚠️ 業主沒綁 LINE，前兩個選項請打電話告知業主', color='#C62828', size='sm', wrap=True, margin='md')]),
                     [lb._btn('讓業主選擇', data=f'a=lvh&l={lid}&h=ask', primary=True), lb._btn('通知業主＋另外派工', data=f'a=lvh&l={lid}&h=notify'),
                      lb._btn('只另外派工', data=f'a=lvh&l={lid}&h=redispatch'), lb._btn('我在找人（先不要自動派）', data=f'a=lvh&l={lid}&h=self')])
    return [{'type': 'flex', 'altText': f"🙋 {w['name']} 請假：{lb.when(dict(o))}（{r['reason']}）", 'contents': {'type': 'carousel', 'contents': [judge, how]}}]

def decide(con, lid, kind, by):
    """管理群判斷：初次／通融不記點，或記點（滿 3 點自動停派半年）。回傳給按的人看的文字。"""
    import line_bot as lb, automation
    ensure(con)
    r = con.execute('SELECT * FROM worker_leaves WHERE id=?', (lid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆請假')
    if r['status'] != 'pending':
        return f"這筆已經判斷過了：{KIND.get(r['status'], r['status'])}（{r['decided_by']}）"
    con.execute('UPDATE worker_leaves SET status=?,decided_by=?,decided_at=? WHERE id=?', (kind, by, c.now(), lid))
    w = c.get(con, 'workers', r['worker_id'])
    n, p = history(con, w['id'])
    msg = f"✅ {w['name']} 這次請假：{KIND[kind]}（{by}）"
    if not r['handling']:                             # 只按了①，②沒選 → 30 分鐘後系統就自動廣發了
        msg += '\n👉 還要選「② 這場怎麼處理」（卡片往右滑）：讓業主選／另外派工／我在找人（先不要自動派）'
    if kind == 'ours':
        lb.to_worker(con, w['id'], 'leave_result', '請假結果', lb._txt('這次是我們調整時間造成的，不算請假，謝謝你配合 🙏'), r['order_id'])
        return msg
    if kind == 'point':
        tell = f'這次請假記 1 點（半年內第 {p} 點；滿 {POINT_LIMIT} 點會停派半年）。有特殊狀況請直接跟示範服務店說 🙏'
        if p >= POINT_LIMIT:
            until = (dt.datetime.now(c.TZ).date() + dt.timedelta(days=POINT_DAYS)).isoformat()
            automation.set_blacklist(con, w['id'], True, '系統（請假記點）', f'半年內請假記點滿 {POINT_LIMIT} 點', until=until)
            msg += f'\n🚫 半年內已滿 {POINT_LIMIT} 點：自動停派到 {until}（管理選單可以提早解除）'
            tell = f'半年內請假記點已滿 {POINT_LIMIT} 點，依規定暫停派工到 {lb.md(until)}。有疑問請直接跟示範服務店聯絡 🙏'
    else:
        tell = '這次請假不記點，謝謝你事先告訴我們 🙏'
    lb.to_worker(con, w['id'], 'leave_result', '請假結果', lb._txt(tell), r['order_id'])
    return msg

def handle(con, lid, how, by):
    """管理群決定這場怎麼處理。"""
    import line_bot as lb, board
    ensure(con)
    r = con.execute('SELECT * FROM worker_leaves WHERE id=?', (lid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆請假')
    if r['handling']:
        return f"這場已經處理了：{ {**HOW, 'reduced': '減少人數，不補人'}.get(r['handling'], r['handling'])}（{r['handled_by']}）"
    oid = r['order_id']; o = c.get(con, 'orders', oid)
    if o['status'] in ('cancelled', 'archived', 'completed'):          # 稽核：工單已取消還按處理 → 業主會收到「請您選擇」
        return f"這場已經{ {'cancelled': '取消', 'archived': '封存', 'completed': '完工'}[o['status']]}了，不用處理"
    if r['status'] == 'withdrawn':
        return '他已經被排回這場了，不用另外處理'
    if how not in HOW:
        raise ValueError('不認識的處理方式')
    con.execute('UPDATE worker_leaves SET handling=?,handled_by=? WHERE id=?', (how, by, lid))
    board.log(con, oid, 'leave_handle', f'{HOW[how]}（{by}）')
    import dispatch
    if how == 'self':                                 # 管理層自己問人 → 系統不自動派
        return dispatch.hold(con, oid, by, True)
    if how in ('notify', 'redispatch') and dispatch.held(con, oid):
        dispatch.hold(con, oid, by, False)            # 明確選了「另外派工」＝讓系統問
    acc, uid = lb.customer_target(con, o['customer_id'])
    if how == 'ask':
        if uid:
            m = lb.flex('原本安排的服務人員臨時請假，想請您選擇 🙏', lb._bubble(lb._header('人員異動・請您選擇', lb.when(dict(o)), '#C47F00', brand=acc),
                [lb._t('很抱歉，原本安排的服務人員臨時有事無法前往 🙏 您希望我們怎麼安排？', wrap=True)],
                [lb._btn('請另外派人（時間不變）', data=f'a=cw&o={oid}&v=other', primary=True), lb._btn('改期', data=f'a=cw&o={oid}&v=resch'),
                 lb._btn('等原本的人員（另約時間）', data=f'a=cw&o={oid}&v=wait')], '選好後專人會在這裡跟您確認。'))
            lb.queue(con, acc, uid, 'leave_ask', m[0]['altText'], m, oid)
            return '✅ 已傳卡片請業主選擇（另外派人／改期／等原人員），業主選了會通知這裡'
        return '⚠️ 業主沒綁 LINE：請打電話問業主要「另外派人／改期／等原人員」，再到中控台處理'
    if how == 'notify' and uid:
        m = lb.card_text('人員異動通知', f"{lb.when(dict(o))} 原本安排的服務人員臨時有事無法前往，很抱歉 🙏 我們正在為您另外安排人員，確定後會再通知您；時間不變。", '#C47F00', brand=acc)
        lb.queue(con, acc, uid, 'leave_notice', m[0]['altText'], m, oid)
    n = reask(con, oid)
    return f"✅ 已{'通知業主並' if how == 'notify' and uid else ''}另外派工：問了 {n} 位夥伴" + ('（業主沒綁 LINE，請打電話告知）' if how == 'notify' and not uid else '')

def reask(con, oid):
    """另外派工：問所有可派、綁了 LINE、不在黑名單、沒請假／婉拒過這場的夥伴。"""
    import dispatch, automation, board
    if dispatch.held(con, oid):                       # 管理層擋住／找人中 → 不自動問（業主選另外派人也一樣，交給管理層）
        board.log(con, oid, 'auto_ask', '另外派工：找人中，系統沒有自動問')
        return 0
    import tiers
    if tiers.batch_size(con):                          # 不再一次問全部（那次問了 27 位）
        n = tiers.reask(con, c.get(con, 'orders', oid))
        board.log(con, oid, 'reask', f'另外派工（照分級分批）：先問 {n} 位')
        return n
    ids = [x['worker_id'] for x in dispatch.candidates(con, oid) if not x['blocked'] and automation.reachable(con, x['worker_id'])]
    if ids:
        c.CTX.actor = getattr(c.CTX, 'actor', '') or '系統另外派工'
        dispatch.auto_dispatch(con, {'order_id': oid, 'worker_ids': ids, 'exclusive': True, 'batch': len(ids)})
    board.log(con, oid, 'auto_ask', f'另外派工：問了 {len(ids)} 位')
    return len(ids)

def customer_choice(con, oid, v, who):
    import line_bot as lb, board
    o = c.get(con, 'orders', oid)
    board.log(con, oid, 'customer_leave_choice', v)
    if v == 'other':
        n = reask(con, oid)
        lb.admin_notify(con, '業主選：請另外派人', [('工作', lb.when(dict(o))), ('派工', f'系統已問 {n} 位夥伴')], '/#dispatch', '#2E7D32', oid=oid)
        return '好的！我們馬上為您另外安排人員，時間不變，確定後會通知您 🙏'
    lb.admin_notify(con, '業主選：等原本的人員' if v == 'wait' else '業主選：改期', [('工作', lb.when(dict(o))), ('處理', '請跟夥伴、業主協調新時間後在中控台改期')], '/#dispatch', '#C47F00', oid=oid)
    return '好的，專人會跟您約新的時間 🙏（確認之前原本的預約先保留）'

# ───────────── 派員審核：管理群看過才通知業主 ─────────────
def crew_review(con, oid):
    import line_bot as lb, automation
    ensure(con)
    o, s, cu, crew, area = lb.order_ctx(con, oid)
    g = lb.admin_group(con)
    if not g:
        return crew_approve(con, oid, '系統（沒有管理群）')
    rows = [lb._row('工作', lb.when(dict(o))), lb._row('地點', s.get('name') or area or '—')]
    btns = [lb._btn('✅ 人員沒問題，通知業主', data=f'a=crew_ok&o={oid}', primary=True)]
    for a in crew:
        e = automation.experience(con, a['wid']); n, p = history(con, a['wid'])
        avg = con.execute("SELECT AVG(o.rating),COUNT(o.rating) FROM assignments x JOIN orders o ON o.id=x.order_id WHERE x.worker_id=? AND x.status='accepted' AND o.rating IS NOT NULL", (a['wid'],)).fetchone()
        rows.append(lb._row(a['name'], f"服務 {e['jobs']} 場" + (f"・{e['years']} 年" if e['years'] != '' else '') + (f"・評分 {avg[0]:.1f}（{avg[1]}）" if avg[1] else '')
                            + (f"・半年請假 {n} 次／記點 {p}" if n else '')))
        if len(btns) < 4:
            btns.append(lb._btn(f"換掉 {a['name']}"[:20], data=f"a=crew_swap&o={oid}&w={a['wid']}"))
    if (o['required_workers'] or 1) > 1:             # 減少人數（拿掉的不補人）
        btns.append(lb._btn(f"減少人數（改成 {(o['required_workers'] or 1) - 1} 位）"[:20], data=f'a=crew_cutm&o={oid}'))
    m = lb.flex(f'👥 人員到齊，請審核：{lb.when(dict(o))}', lb._bubble(lb._header('👥 人員到齊・請審核', lb.when(dict(o)), '#0A84FF'), rows, btns,
                '按「沒問題」才會通知業主是怎樣的人員（業主看不到姓名，只看經歷和作品集）；換掉的人系統會自動另外派工。'))
    lb.queue(con, lb.WORK_ACCOUNT, g, 'crew_review', m[0]['altText'], m, oid)
    return '待審核'

def crew_approve(con, oid, by):
    import line_bot as lb, notify
    ensure(con)
    o = c.get(con, 'orders', oid)
    if o['crew_ok']:
        return f"這場的人員已經審核過了（{o['crew_ok']}）"
    import integrations as ix
    hold = ix.setting(con, f'dispatch_hold:{oid}')
    if hold:                                       # 管理層先擋住這張單（例：已另外派給指定夥伴）
        return f"這場先擋住了，先不要審核：{hold}"
    crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (oid,)).fetchone()[0]
    if crew < (o['required_workers'] or 1):
        return '人數還沒到齊，到齊後會再請你們審核'
    con.execute('UPDATE orders SET crew_ok=? WHERE id=?', (f"{by} {c.now()[5:16].replace('T', ' ')}", oid))
    lb.on_filled(con, oid)                         # 客戶收到「已安排好服務人員」卡（匿名＋作品集）
    note = ''
    if o['customer_confirmed']:
        note = lb._auto_confirm(con, oid)
        if note == '已排定':
            notify.staff(con, '已自動排定', f"{o['date']} {o['start']} {o['title']}：人員已審核、客戶已確認", '/#schedule')
            lb.admin_notify(con, '✅ 已自動排定', [('說明', '人員已審核、客戶已確認，地址已傳給夥伴')], '/#schedule', '#2E7D32', oid=oid)
    acc_, uid_ = lb.customer_target(con, o['customer_id'])
    if not uid_ or lb.mode(con, acc_) != 'on':      # 稽核：業主收不到就照實說
        return '✅ 人員審核通過，但業主沒綁 LINE（或官方 LINE 還在測試），系統傳不到：請打電話告訴業主人員已安排好、請他確認時間' + (f'（{note}）' if note else '')
    return '✅ 已通知業主' + (f'（{note}）' if note else '，等業主確認時間')

def crew_swap(con, oid, wid, by):
    import line_bot as lb, board
    a = con.execute("SELECT id FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone()
    if not a:
        return '這位已經不在這場了'
    w = c.get(con, 'workers', wid); o = c.get(con, 'orders', oid)
    con.execute("UPDATE assignments SET status='declined',notes=COALESCE(notes,'')||'（管理群換人）',version=version+1 WHERE id=?", (a[0],))
    con.execute("UPDATE orders SET crew_ok='',dispatch=CASE WHEN dispatch='filled' THEN 'open' ELSE dispatch END,version=version+1 WHERE id=?", (oid,))
    reopen(con, oid)
    board.log(con, oid, 'crew_swap', f"{w['name']}（{by}）")
    m = lb.card_text('這筆工作改由其他人負責', f"{lb.when(dict(o))} 這筆工作示範服務店已調整人力，請不要前往，謝謝你 🙏", '#C62828')
    lb.to_worker(con, wid, 'cancel', m[0]['altText'], m, oid)
    n = reask(con, oid)
    return f"✅ 已換掉 {w['name']}，系統另外問了 {n} 位夥伴；到齊後會再請你們審核"

def set_people(con, oid, n, by, drop=(), boss=None, approved=False):
    """工單減少人數。n＝新的人數；已接的人比 n 多 → drop 要列出拿掉誰（拿掉的不另外補人）。
    到齊了：其他邀請改「已額滿」；還沒審核人員的照常請管理群審核（已審核過的不用再審）。回傳一句話。"""
    import line_bot as lb, board, dispatch
    o = c.get(con, 'orders', oid)
    if o['status'] not in ('review', 'draft', 'confirmed'):
        raise ValueError('這場已經完工或取消了，不能改人數')
    old = o['required_workers'] or 1
    n = int(n)
    if n < 1:
        raise ValueError('至少要 1 位（整場不要了請取消工單）')
    if n >= old:
        raise ValueError(f'現在是 {old} 位；要加人請用「挑人派工／換人加人」')
    import crewguard                                 # 比報價少 → 先問店長（中控台「減少人數」、管理群「拿掉 X」都一樣）
    if not approved:
        ask_, qp_, _ = crewguard.needs_ask(con, oid, n, {'role': 'admin' if boss else '', 'name': by if boss is None else ''})
        if ask_:
            return crewguard.ask(con, oid, n, qp_, old, by, list(drop or []))
    crew = c.rows(con, "SELECT a.id,a.worker_id,w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (oid,))
    drop = [w for w in (drop or []) if w]
    k = len([x for x in crew if x['worker_id'] in drop])
    if len(crew) - k > n:
        raise ValueError(f"已經有 {len(crew)} 位接了，改成 {n} 位要先選拿掉誰")
    if k > max(0, len(crew) - n):                   # 稽核：拿掉太多會變成人不夠、卻沒人在找
        raise ValueError(f"改成 {n} 位只要拿掉 {max(0, len(crew) - n)} 位；要換人請用「換掉」")
    gone = []
    for x in crew:
        if x['worker_id'] not in drop:
            continue
        con.execute("UPDATE assignments SET status='declined',notes=COALESCE(notes,'')||'（減少人數）',version=version+1 WHERE id=?", (x['id'],))
        m = lb.card_text('這筆工作人數調整', f"{lb.when(dict(o))} 這筆工作人數調整，這次不用前往了，謝謝你 🙏（不影響之後派工）", '#C47F00')
        lb.to_worker(con, x['worker_id'], 'cancel', m[0]['altText'], m, oid)
        gone.append(x['name'])
    con.execute('UPDATE orders SET required_workers=?,version=version+1 WHERE id=?', (n, oid))
    ensure(con)                                     # 稽核：這場還沒決定怎麼處理的請假 → 減少人數就是處理方式（不再自動通知業主「另外安排人員」）
    con.execute("UPDATE worker_leaves SET handling='reduced',handled_by=? WHERE order_id=? AND COALESCE(handling,'')='' AND status!='withdrawn'", (by, oid))
    c.audit(con, 'people_reduce', 'orders', oid, before={'required_workers': old}, after={'required_workers': n, 'drop': gone, 'by': by})
    board.log(con, oid, 'people_reduce', f"{old}→{n} 位" + (f"，拿掉 {'、'.join(gone)}" if gone else '') + f"（{by}）")
    left = len(crew) - len(gone)
    if left >= n:                                   # 到齊：收單、其他邀請改已額滿
        con.execute("UPDATE assignments SET status='cancelled',notes=COALESCE(notes,'')||'（已額滿）',version=version+1 WHERE order_id=? AND status IN ('invited','applied')", (oid,))
        con.execute("UPDATE orders SET dispatch='filled' WHERE id=? AND dispatch='open'", (oid,))
        o2 = c.get(con, 'orders', oid)
        if not o2['crew_ok'] and o2['status'] != 'confirmed':
            crew_review(con, oid)                    # 人員還沒審核：照常請管理群審核（舊卡上被拿掉的人按了也沒用）
            nxt = '剩下的人到齊了，管理群會收到「人員到齊・請審核」卡'
        else:
            nxt = '剩下的人照原本安排'
    else:
        nxt = f'還差 {n - left} 位，系統照常找人'
    return (f"✅ 這場改成 {n} 位" + (f"，已通知 {'、'.join(gone)} 不用前往" if gone else '') + f"；{nxt}。\n"
            f"⚠️ 報價／收款是照 {old} 位算的，金額要調整請到報價或結帳改；要跟業主說明請在官方 LINE 回覆。")

def crew_reduce(con, oid, wid, by, expect=None, boss=None):
    """人員審核卡「拿掉 X（改成 N−1 位）」：拿掉這位、人數少一位、不另外補人。expect＝按鈕上寫的目標人數（人數已經改過就不再減）。"""
    o = c.get(con, 'orders', oid)
    if not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone():
        return '這位已經不在這場了'
    if (o['required_workers'] or 1) <= 1:
        return '這場只剩 1 位，不能再減（整場不要了請取消工單；要換人請按「換掉」）'
    if expect and str(expect).isdigit() and int(expect) != (o['required_workers'] or 1) - 1:
        return f"人數已經改過了（現在 {o['required_workers']} 位），要再減請到中控台工單「減少人數」"
    try:
        return set_people(con, oid, (o['required_workers'] or 1) - 1, by, [wid], boss=boss)
    except ValueError as e:
        return str(e)

# ───────────── 夥伴暫停／停派：手上的單 ─────────────
def worker_unavailable(con, wid, why):
    """夥伴被暫停或列黑名單：還沒排定的單 → 拿掉並另外派工；已排定（地址已給）的單 → 請管理群決定要不要換人。"""
    import line_bot as lb
    w = c.get(con, 'workers', wid)
    today = dt.datetime.now(c.TZ).date().isoformat()
    moved, kept = [], []
    con.execute("UPDATE assignments SET status='cancelled',notes=COALESCE(notes,'')||?,version=version+1 WHERE worker_id=? AND status IN ('invited','applied')",
                (f'（{why}，收回邀請）', wid))      # 每日檢查：以前只有黑名單會收回，暫停的人的邀請佔住名額到逾時
    for r in c.rows(con, "SELECT a.id aid,o.* FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.date>=? "
                         "AND o.status IN ('review','draft','confirmed') ORDER BY o.date", (wid, today)):
        if r['status'] == 'confirmed':
            kept.append(r)
            continue
        con.execute("UPDATE assignments SET status='declined',notes=COALESCE(notes,'')||?,version=version+1 WHERE id=?", (f'（{why}）', r['aid']))
        con.execute("UPDATE orders SET crew_ok='',dispatch=CASE WHEN dispatch='filled' THEN 'open' ELSE dispatch END,version=version+1 WHERE id=?", (r['id'],))
        reask(con, r['id']); moved.append(r)
    g = lb.admin_group(con)
    if g and (moved or kept):
        rows = [lb._row('已另外派工', '、'.join(lb.when(x) for x in moved) or '—'), lb._row('已排定', '、'.join(lb.when(x) for x in kept) or '—')]
        btns = [lb._btn(f"換掉：{lb.md(x['date'])} {x['start']}"[:20], data=f"a=crew_swap&o={x['id']}&w={wid}") for x in kept[:3]]
        m = lb.flex(f"{w['name']} {why}：手上的工作", lb._bubble(lb._header(f'⏸ {why}', w['name'], '#C47F00'), rows, btns,
                    '還沒排定的單已拿掉並自動另外派工；已排定（地址已傳給他）的請決定要不要換人。'))
        lb.queue(con, lb.WORK_ACCOUNT, g, 'worker_paused', m[0]['altText'], m)
    return len(moved), len(kept)

# ───────────── 案場異動：通知接下來有排的夥伴 ─────────────
WATCH = {'address': '地址', 'entry_mode': '開門方式', 'entry_notes': '進場方式', 'navigation': '導航', 'checklist': '服務重點'}      # 案場備註夥伴看不到，改了不通知

def site_changed(con, before, after):
    import line_bot as lb
    if not before:
        return 0
    diff = [n for k, n in WATCH.items() if (before.get(k) or '') != (after.get(k) or '')]
    if not diff:
        return 0
    today = dt.datetime.now(c.TZ).date().isoformat(); n = 0
    for o in c.rows(con, "SELECT * FROM orders WHERE site_id=? AND date>=? AND status IN ('review','draft','confirmed') ORDER BY date LIMIT 10", (after['id'], today)):
        for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)):
            if o['status'] == 'confirmed':        # 已排定才看得到地址：傳更新後的工作卡
                m = lb.card_worker_remind(con, o['id'], f"📍 案場資料更新：{'、'.join(diff)}", '#C47F00', a['worker_id'])
            else:
                m = lb.card_text('案場資料更新', f"{lb.when(dict(o))} 這場的{'、'.join(diff)}有更新，打開主控板就看得到最新地址；進場方式排定後傳給你。", '#C47F00')
            lb.to_worker(con, a['worker_id'], 'site_change', m[0]['altText'], m, o['id']); n += 1
    return n
