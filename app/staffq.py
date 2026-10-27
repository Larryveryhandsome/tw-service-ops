"""負責人：「LINE 的東西都要讓管理群能夠直接在中控台處理」「也要讓店長可以在中控台直接處理」。

派單管理群裡要人按的卡片 → 中控台「更多 › 管理群待處理」也列出來，按鈕呼叫跟 LINE 同一個後端函式（哪邊按都一樣，另一邊再按會說已處理過）：
現場回報（site_report.act）、客戶留言還沒回（line_bot.cmsg_done）、人員到齊待審核（leave.crew_approve／crew_swap）、請假判斷／怎麼處理（leave.decide／handle）、
開門方式待補（door.fill_skip／staff_ok；設定用中控台的「🚪 開門方式」）、照片不齊照常發薪（line_bot.photo_release）、夥伴用品申請（supplies.decide）、
審核（reviews.decide，看得到金額的人）、報價用其他方式給客戶的原因（quickquote.manual_reason，看得到金額的人）。
"""
import json
import datetime as dt
import core as c
import integrations as ix

def _when(con, oid):
    import line_bot as lb
    o = con.execute('SELECT * FROM orders WHERE id=?', (oid or '',)).fetchone()
    if not o:
        return '', {}
    s = con.execute('SELECT name FROM sites WHERE id=?', (o['site_id'] or '',)).fetchone()
    return f"{lb.when(dict(o))}{'・' + s[0] if s and s[0] else ''}", dict(o)

def items(con, money=False):
    """回傳 [{kind, id, group, title, sub, oid, acts: [[v, label, style]]}]。"""
    import line_bot as lb, site_report, leave, door, supplies, board
    out = []
    site_report.ensure(con)
    for r in c.rows(con, "SELECT r.* FROM site_reports r JOIN orders o ON o.id=r.order_id WHERE r.status='open' AND o.status NOT IN ('cancelled','archived') ORDER BY r.created_at"):
        w = con.execute('SELECT name FROM workers WHERE id=?', (r['worker_id'],)).fetchone()
        when, _ = _when(con, r['order_id'])
        out.append({'kind': 'srp', 'id': r['id'], 'group': '現場回報', 'title': f"{w[0] if w else '夥伴'}：{site_report.summary(con, r)}",
                    'sub': f"{r['created_at'][5:16].replace('T', ' ')} 回報・{when}", 'oid': r['order_id'], 'acts': site_report.actions(con, r)})
    import reschedule                              # 業主自己申請改時間
    for r in reschedule.pending_items(con):
        when, _ = _when(con, r['order_id'])
        out.append({'kind': 'rs', 'id': r['id'], 'group': '業主申請改時間', 'title': f"{when} → {r['new_date'][5:].replace('-', '/')} {r['new_start']}–{r['new_end']}",
                    'sub': '；'.join(f'{n}：{t}' for n, t in reschedule.crew_check(con, c.get(con, 'orders', r['order_id']), r['new_date'], r['new_start'], r['new_end'])) + (f"\n業主說：{r['note']}" if r['note'] else ''),
                    'oid': r['order_id'], 'acts': [['ok', '改到新時間', 'primary'], ['no', '這時間不行（寫給業主的話）', 'gray']]})
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'cmsg_open:%' AND value!=''").fetchall():
        try:
            d = json.loads(v)
        except ValueError:
            continue
        out.append({'kind': 'cmsg', 'id': k[10:], 'group': '客戶留言還沒回', 'title': f"{d.get('customer') or '客戶'}（{d.get('brand') or ''}）：{d.get('text') or '（圖片或貼圖）'}",
                    'sub': f"{(d.get('at') or '')[5:16].replace('T', ' ')} 留言・請到官方 LINE 聊天室回覆，回完按「已回覆」", 'oid': '', 'acts': [['ok', '已回覆', 'primary']]})
    leave.ensure(con)
    today = dt.datetime.now(c.TZ).date().isoformat()
    for o in c.rows(con, "SELECT o.* FROM orders o WHERE o.status IN ('review','draft') AND COALESCE(o.crew_ok,'')='' AND o.date>=? AND COALESCE(o.review_note,'')!='示範工單' "
                         "AND (SELECT COUNT(*) FROM assignments a WHERE a.order_id=o.id AND a.status='accepted')>=COALESCE(o.required_workers,1) ORDER BY o.date,o.start", (today,)):
        crew = c.rows(con, "SELECT a.worker_id,w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (o['id'],))
        when, _ = _when(con, o['id'])
        hold = ix.setting(con, f"dispatch_hold:{o['id']}")
        out.append({'kind': 'crew', 'id': o['id'], 'group': '人員到齊・待審核', 'title': f"{when}：{'、'.join(x['name'] for x in crew)}",
                    'sub': f'先擋住了：{hold}' if hold else '沒問題就按「人員 OK」：系統通知業主（業主已確認時間的直接排定）；不合適就換掉某位，系統自動另外問人',
                    'oid': o['id'], 'acts': [['ok', '人員 OK，通知業主', 'primary']] + [[f"swap:{x['worker_id']}", f"換掉 {x['name']}", 'gray'] for x in crew]
                    + ([[f"cut:{x['worker_id']}", f"拿掉 {x['name']}（改 {(o['required_workers'] or 1) - 1} 位）", 'gray'] for x in crew] if (o['required_workers'] or 1) > 1 else [])})
    for r in c.rows(con, "SELECT l.*,w.name FROM worker_leaves l JOIN workers w ON w.id=l.worker_id JOIN orders o ON o.id=l.order_id "
                         "WHERE (l.status='pending' OR (COALESCE(l.handling,'')='' AND l.status!='withdrawn')) AND o.status NOT IN ('cancelled','archived','completed') ORDER BY l.created_at"):
        when, _ = _when(con, r['order_id'])
        if r['status'] == 'pending':
            n, p = leave.history(con, r['worker_id'])
            out.append({'kind': 'lvd', 'id': r['id'], 'group': '夥伴請假', 'title': f"{r['name']} 請假：{when}", 'sub': f"原因：{r['reason']}・半年內請假 {n} 次、記點 {p} 點" + ('・⚠️ 24 小時內才請' if r['late'] else '') + '\n① 這次算不算請假（記點用）',
                        'oid': r['order_id'], 'acts': [['first', '初次・不記點', 'primary'], ['excuse', '通融・不記點', 'sec'], ['point', '記點', 'gray'], ['ours', '我方調整・不算請假', 'sec']]})
        if not r['handling']:
            out.append({'kind': 'lvh', 'id': r['id'], 'group': '夥伴請假', 'title': f"{r['name']} 請假：{when}", 'sub': '② 這場怎麼補人（沒選的話系統會照設定提醒或自動處理）',
                        'oid': r['order_id'], 'acts': [['ask', '讓業主選擇', 'primary'], ['notify', '通知業主＋另外派工', 'sec'], ['redispatch', '只另外派工', 'sec'], ['self', '我在找人（先不要自動派）', 'gray']]})
    for o in door.fill_due(con):
        s = con.execute('SELECT name,address,entry_mode FROM sites WHERE id=?', (o['site_id'],)).fetchone()
        cu = con.execute('SELECT name,phone FROM customers WHERE id=?', (o['customer_id'] or '',)).fetchone()
        out.append({'kind': 'ent', 'id': o['site_id'], 'group': '開門方式待補', 'title': f"{cu['name'] if cu else ''}｜{s['name'] if s else ''}", 'oid': o['id'],
                    'sub': f"下次服務 {lb.when(dict(o))}・業主電話 {(cu['phone'] if cu else '') or '—'}・" + ('業主還沒綁 LINE，系統問不到' if not o.get('line_bound') else '系統問了業主，還沒回'),
                    'acts': [['set', '🚪 設定開門方式', 'primary'], ['skip', '打電話問了再補（明天再問）', 'gray']] + ([['ok', '目前寫的沒錯', 'sec']] if s and s['entry_mode'] else [])})
    start = ix.setting(con, 'photo_pay_from') or '2026-10-14'
    since = max(start, (dt.date.today() - dt.timedelta(days=30)).isoformat())
    for o in c.rows(con, "SELECT o.* FROM orders o LEFT JOIN sites s ON s.id=o.site_id WHERE o.status='completed' AND o.date>=? AND COALESCE(s.no_photo,0)=0 AND COALESCE(o.review_note,'')!='示範工單' ORDER BY o.date", (since,)):      # 稽核 A3
        if o['date'] >= dt.datetime.now(c.TZ).date().isoformat() and (o['end'] or '23:59') > (dt.datetime.now(c.TZ) - dt.timedelta(hours=2)).strftime('%H:%M'):
            continue                                   # 結束 2 小時內不列（夥伴可以完工時才交）
        if ix.setting(con, f"photo_ok:{o['id']}") or ix.setting(con, f"nophoto_job:{o['id']}"):      # 稽核 F2
            continue
        a = con.execute("SELECT a.*,o.date,o.status ostatus FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.order_id=? AND a.status='accepted' LIMIT 1", (o['id'],)).fetchone()
        hold = board.photo_hold(con, dict(a)) if a else ''
        if hold:
            when, _ = _when(con, o['id'])
            out.append({'kind': 'photo', 'id': o['id'], 'group': '照片不齊・薪資暫不發', 'title': when, 'sub': hold, 'oid': o['id'], 'acts': [['ok', '確認，照常發薪', 'primary']]})
    if money:                       # 稽核 A1：收款單金額跟工單不一樣（一張工單只有一張收款單；月訂閱除外）
        out += pay_mismatch(con)
    supplies.ensure(con)
    for r in c.rows(con, "SELECT r.*,w.name FROM supply_requests r JOIN workers w ON w.id=r.worker_id WHERE r.status='pending' ORDER BY r.created_at"):
        when, _ = _when(con, r['order_id'])
        out.append({'kind': 'sup', 'id': r['id'], 'group': '夥伴用品申請', 'title': f"{r['name']} 要買：{r['item']}", 'sub': f'給 {when} 用・同意後夥伴買好傳發票＋金額，記成代墊併入薪資',
                    'oid': r['order_id'], 'acts': [['ok', '同意，買好拍發票', 'primary'], ['no', '這次不用買', 'gray']]})
    import dispatch
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'gapask:%'").fetchall():
        if ix.setting(con, 'gapans:' + k[7:]):
            continue
        try:
            g = json.loads(v)
        except ValueError:
            continue                      # 舊格式（只有時間）：不知道哪一場是新接的，只能在 LINE 按
        an = con.execute("SELECT a.*,w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.id=?", (g.get('n', ''),)).fetchone()
        ax = con.execute('SELECT order_id FROM assignments WHERE id=?', (g.get('x', ''),)).fetchone()
        if not an or not ax or an['status'] != 'accepted':
            continue
        w1, _ = _when(con, an['order_id']); w2, _ = _when(con, ax['order_id'])
        out.append({'kind': 'gap', 'id': f"{g['n']}|{g['x']}", 'group': '同一天兩場：趕得上嗎', 'title': f"{an['name']}：{w2} → {w1}", 'oid': an['order_id'],
                    'sub': f"中間只隔 {g.get('gap', '?')} 分鐘。回答會記住（同兩地、這個間隔以上之後不再問）；趕不上 → 這場自動換人",
                    'acts': [['ok', '趕得上', 'primary'], ['no', '趕不上（這場換人）', 'gray']]})
    if (ix.setting(con, 'prep_mode') or 'off') != 'off' and ix.setting(con, 'prep_day') == dt.datetime.now(c.TZ).date().isoformat():
        import prep
        prep.ensure(con)
        tmr = (dt.datetime.now(c.TZ).date() + dt.timedelta(days=1)).isoformat()
        for o in c.rows(con, "SELECT * FROM orders WHERE date=? AND status IN ('review','draft','confirmed') ORDER BY start", (tmr,)):
            if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action IN ('prep_ok','worker_note')", (o['id'],)).fetchone():
                continue
            if ix.setting(con, 'prep_mode') == 'new' and not prep.first_visit(con, o):
                continue
            when, _ = _when(con, o['id'])
            out.append({'kind': 'prep', 'id': o['id'], 'group': '明天的工作：要補充提醒夥伴嗎', 'title': when, 'oid': o['id'],
                        'sub': '有沒寫到的（例：現場沒有工具、垃圾怎麼處理、老闆幾點到）按「補充提醒」打字，會跟明日工作卡一起給夥伴',
                        'acts': [['ok', '沒有要補充 ✅', 'primary'], ['add', '補充提醒（打字）', 'sec']]})
    for p in lb.worker_dup_pairs(con):
        k, d = p[0]['id'], p[1]['id']
        wk_ = con.execute('SELECT name,phone FROM workers WHERE id=?', (k,)).fetchone(); wd_ = con.execute('SELECT name,phone FROM workers WHERE id=?', (d,)).fetchone()
        if not wk_ or not wd_:
            continue
        out.append({'kind': 'wdup', 'id': f'{k}|{d}', 'group': '可能重複的夥伴', 'title': f"{wk_['name']}（{wk_['phone'] or '沒電話'}）／{wd_['name']}（{wd_['phone'] or '沒電話'}）", 'oid': '',
                    'sub': f'{p[2]}。是同一人 → 合併（工作、薪資、LINE 都併到第一位）；不是 → 之後不再問',
                    'acts': [['same', '是同一人，合併', 'primary'], ['diff', '不是同一人', 'gray']]})
    for g in c.rows(con, "SELECT group_id,name,role FROM line_groups WHERE role LIKE 'pending_%' AND active=1"):
        out.append({'kind': 'grp', 'id': g['group_id'], 'group': '新的 LINE 群組待確認', 'title': g['name'] or '（沒有名稱）', 'oid': '',
                    'sub': f"系統猜是：{ {'pending_admin': '派單管理群', 'pending_worker': '夥伴個人群組', 'pending_job': '工作群組'}.get(g['role'], g['role'])}。不是我們的群組就按「不是」，系統不會傳任何派工資料過去。群組要用自己的 LINE 搜尋群組名；官方帳號後台的聊天列表不會顯示群組（LINE 的限制）",
                    'acts': [['ok', '是，照這樣分類', 'primary'], ['no', '不是我們的群組', 'gray']]})
    if not ix.setting(con, 'guide_sent_at') and con.execute("SELECT 1 FROM line_outbox WHERE payload LIKE '%a=g_ok%'").fetchone():
        out.append({'kind': 'guide', 'id': 'g', 'group': '夥伴系統介紹', 'title': f'發給 {len(lb.guide_targets(con))} 位已綁官方 LINE 的夥伴', 'oid': '',
                    'sub': '內容在派單管理群那張「示範服務店夥伴系統介紹」；要修改請在 LINE 按「要修改」打字', 'acts': [['ok', '✅ OK，發給所有夥伴', 'primary']]})
    import quotelink                              # 報價成立、原本已有排好人的工單 → 沿用嗎
    for k_, v_ in con.execute("SELECT key,value FROM settings WHERE key LIKE 'dispatch_hold:%' AND value=?", (quotelink.HOLD,)).fetchall():
        oid_ = k_[14:]; o_ = con.execute('SELECT * FROM orders WHERE id=?', (oid_,)).fetchone()
        old_ = quotelink.old_order(con, dict(o_)) if o_ else None
        if old_:
            when, _ = _when(con, oid_); w2, _ = _when(con, old_['id'])
            out.append({'kind': 'qlink', 'id': f"{oid_}|{old_['id']}", 'group': '報價成立：要沿用原本的人員嗎', 'title': when, 'sub': f'原本的工單：{w2}', 'oid': oid_,
                        'acts': [['move', '沿用原本的人員', 'primary'], ['both', '兩張都要', 'gray']]})
    import departure                              # 夥伴離開（刪官方 LINE、退群）還有工作
    for k_, v_ in con.execute("SELECT key,value FROM settings WHERE key LIKE 'wgone:%' AND value!=''").fetchall():
        wid_ = k_[6:]; w_ = con.execute("SELECT name FROM workers WHERE id=? AND status='active'", (wid_,)).fetchone()
        js_ = departure.future_jobs(con, wid_) if w_ else []
        if js_:
            out.append({'kind': 'wgone', 'id': wid_, 'group': '夥伴可能離開了', 'title': f"{w_[0]}：之後還有 {len(js_)} 場", 'oid': js_[0]['id'],
                        'sub': '、'.join(f"{j['date'][5:].replace('-', '/')} {j['site'] or ''}" for j in js_[:5]), 'acts': [['redo', '暫停他，另外找人', 'primary'], ['keep', '先不用，我會聯絡', 'gray']]})
    import prepq                                  # 舊客戶的工具、垃圾 → 管理層補（不問業主）
    for s_, cname_, o_, miss_ in prepq.staff_due(con):
        when, _ = _when(con, o_['id'])
        acts_ = ([['t:full', '工具：業主齊全', 'sec'], ['t:basic', '工具：只有基本', 'sec'], ['t:none', '工具：全部自備', 'sec']] if 'tools' in miss_ else []) + \
                ([['g:pack', '垃圾：打包放門口', 'sec'], ['g:bin', '垃圾：拿到集中處', 'sec'], ['g:none', '垃圾：沒有要處理', 'sec']] if 'trash' in miss_ else [])
        out.append({'kind': 'stq', 'id': s_['id'], 'group': '舊客戶工具／垃圾（管理層補，不問業主）', 'title': f"{cname_}・{s_['name']}", 'oid': o_['id'],
                    'sub': f"最近一場 {when}；還不知道：{'、'.join({'tools': '工具', 'trash': '垃圾怎麼處理'}[x] for x in miss_)}（夥伴卡會照這裡寫）", 'acts': acts_})
    import crewcheck, aibrief                     # 出勤確認逾期、工具不夠、AI 整理的業主交代
    for a in crewcheck.anomalies(con):
        when, _ = _when(con, a['oid'])
        out.append({'kind': 'att', 'id': a['id'], 'group': '出勤異常', 'title': a['title'], 'sub': when, 'oid': a['oid'],
                    'acts': [['ok', '工具準備好了' if a['reason'] == 'lack' else '已電話確認', 'primary']]})
    for oid, st in aibrief.pending(con):
        when, _ = _when(con, oid)
        out.append({'kind': 'aib', 'id': oid, 'group': 'AI 整理的業主交代（請核對）', 'title': when, 'sub': '\n'.join('・' + p for p in st.get('points', [])), 'oid': oid,
                    'acts': [['ok', '同步到派工卡', 'primary'], ['no', '不用', 'gray']]})
    st_ = supplies.stock_check(con)                 # 新進的單要用我們的耗材 → 確認備品夠／已到貨
    if st_:
        out.append({'kind': 'stock', 'id': 'stock', 'group': '新進工單：確認耗材夠、已到貨', 'title': f'{len(st_)} 場要用我們的耗材（14 天內開工）', 'oid': '',
                    'sub': '、'.join(f"{o_['date'][5:].replace('-', '/')} {o_['title'] or ''}" for o_ in st_[:8]) + ('…' if len(st_) > 8 else '')
                           + '\n耗材（尤其水垢、油污用的）夠不夠？網購的到貨了沒？不夠現在就下訂或多屯一點；來不及的請夥伴按「要買耗材」先買。',
                    'acts': [['ok', '備品夠／已到貨', 'primary']]})
    import siteroutine                             # 客戶01：定期特殊服務過了工單日期還沒回報做完
    for o_, rs_ in siteroutine.open_items(con):
        when, _ = _when(con, o_['id'])
        out.append({'kind': 'rtn', 'id': o_['id'], 'group': '定期特殊服務還沒回報做完', 'title': when, 'oid': o_['id'],
                    'sub': '\n'.join('・' + siteroutine._line(r) for r in rs_) + '\n問一下夥伴有沒有做；沒做的話下一場會照樣帶這些項目',
                    'acts': [['done', '已確認做了', 'primary'], ['skip', '這次沒做，下次再做', 'gray']]})
    import materials
    for k, it in materials.pending(con).items():
        out.append({'kind': 'mat', 'id': k, 'group': '素材待準備', 'title': f"{it['label']}（已收 {it.get('got', 0)}／要 {it['need']} 張）", 'oid': '',
                    'sub': (it.get('desc') or '') + '\n照片請避開門牌、人臉和客人的個資。', 'acts': [['upload', '📷 上傳照片（可一次選多張）', 'primary']]})
    if money:
        import reviews, quickquote, privacy
        privacy.ensure(con)
        for r in c.rows(con, "SELECT p.*,cu.name FROM privacy_requests p LEFT JOIN customers cu ON cu.id=p.customer_id WHERE p.status='pending' ORDER BY p.at"):
            out.append({'kind': 'pd', 'id': r['id'], 'group': '客戶申請刪除個資', 'title': r['name'] or '客戶', 'oid': '',
                        'sub': '核准後客戶資料去識別化（不能復原）並通知客戶；還有預約或款項沒結清的請先不要刪',
                        'acts': [['ok', '核准刪除', 'primary'], ['no', '先不要（我會聯絡客戶）', 'gray']]})
        for r in reviews.pending(con):
            bc = r['category'] in reviews.BROADCAST
            try:
                pv = (json.loads(r['payload'] or '{}') or {}).get('preview') if bc else None
            except ValueError:
                pv = None
            out.append({'kind': 'rv', 'id': r['id'], 'group': '要審核（群發、公告、文案）', 'title': f"【{r['category']}】{r['title']}", 'oid': '', 'broadcast': bc,
                        'sub': (r['body'] or '')[:1500] + (('\n\n📱 對方會收到的卡片內容：\n' + preview_text(pv)) if pv else ''),
                        'acts': [['ok', '核准' + ('（立刻群發）' if bc else ''), 'primary'], ['changes', '要修改（寫意見）', 'gray']]})
        sincq = (dt.datetime.now(c.TZ) - dt.timedelta(days=14)).strftime('%Y-%m-%d')
        for (qid,) in con.execute("SELECT DISTINCT entity_id FROM audit WHERE action='quote_sent_manual' AND at>=? ORDER BY id", (sincq,)).fetchall():
            q = con.execute('SELECT form,total FROM quotes WHERE id=?', (qid,)).fetchone()
            if q and not json.loads(q['form'] or '{}').get('manual_reason'):
                f = json.loads(q['form'] or '{}')
                out.append({'kind': 'qreason', 'id': qid, 'group': '報價用其他方式給客戶：為什麼', 'title': f"{f.get('company') or f.get('name') or qid}・NT$ {(q['total'] or 0) // 100:,}",
                            'sub': '說原因 → 系統每天晚上檢查能不能讓這種情況下次全自動', 'oid': '', 'acts': [[o, o, 'sec'] for o in quickquote.MANUAL_WHY] + [['write', '✏️ 自己寫', 'gray']]})
    return out

def preview_text(msgs):
    """群發審核：把要發的 LINE 卡片攤成文字（標題、內文、按鈕），中控台看得到實際內容。"""
    out = []
    def walk(x):
        if isinstance(x, dict):
            if x.get('type') == 'text' and x.get('text'):
                out.append(str(x['text']))
            if x.get('type') == 'button' and (x.get('action') or {}).get('label'):
                out.append(f"［按鈕］{x['action']['label']}")
            for k, v in x.items():
                if k != 'action':
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(msgs or [])
    return '\n'.join(dict.fromkeys(out))[:1500]

def pay_mismatch(con):
    """收款單金額 ≠ 工單核定金額（例：Lin 收款單 6,000、工單改成 1,500）。"""
    out = []
    try:
        rows = c.rows(con, "SELECT p.*,o.approved_amount oamt,o.customer_id ocid FROM payment_requests p JOIN orders o ON o.id=p.order_id WHERE p.status='pending' "
                           "AND p.kind NOT IN ('month','subscription') AND o.approved_amount IS NOT NULL AND o.status NOT IN ('cancelled','archived') AND COALESCE(o.review_note,'')!='示範工單'")
    except Exception:
        return out
    for r in rows:
        if r['amount'] == r['oamt'] or ix.setting(con, f"pamt_ok:{r['id']}"):
            continue
        if con.execute("SELECT COUNT(*) FROM payment_requests WHERE order_id=? AND status!='cancelled'", (r['order_id'],)).fetchone()[0] != 1:
            continue
        when, _ = _when(con, r['order_id'])
        cu = con.execute('SELECT name FROM customers WHERE id=?', (r['ocid'] or '',)).fetchone()
        out.append({'kind': 'pamt', 'id': r['id'], 'group': '收款單金額跟工單不一樣', 'title': f"{cu[0] if cu else ''}｜{when}", 'oid': r['order_id'],
                    'sub': f"收款單 NT$ {r['amount'] // 100:,}（{r['label']}，期限 {r['due'] or '—'}）・工單 NT$ {r['oamt'] // 100:,}：客戶付款、提醒都照收款單",
                    'acts': [['order', f"收款單改成 NT$ {r['oamt'] // 100:,}", 'primary'], ['ok', '金額本來就不同（不用改）', 'gray']]})
    return out

def act(con, kind, iid, v, by, note='', money=False):
    """中控台按下去：呼叫跟 LINE 同一個後端函式。回傳給操作者看的一句話。"""
    import line_bot as lb
    if kind == 'srp':
        import site_report; return site_report.act(con, iid, v, by)
    if kind == 'stq' and v[:2] in ('t:', 'g:'):
        if v[:2] == 't:':
            import sitetools; sitetools.set_tools(con, iid, v[2:], '', by); return '已記下工具（夥伴卡會照這個寫）'
        import prepq
        if v[2:] not in prepq.TRASH:
            raise ValueError('選項不對')
        con.execute('UPDATE sites SET trash_mode=?,version=version+1 WHERE id=?', (v[2:], iid))
        c.audit(con, 'site_trash_mode', 'sites', iid, after={'trash_mode': v[2:], 'by': by}); return '已記下垃圾怎麼處理'
    if kind == 'qlink' and v in ('move', 'both'):
        import quotelink; n_, o_ = iid.split('|', 1); return quotelink.decide(con, n_, o_, v, by)
    if kind == 'wgone' and v in ('redo', 'keep'):
        import departure
        r_ = departure.decide(con, iid, v, by)
        if v == 'keep':
            ix.set_setting(con, f'wgone:{iid}', '')
        return r_
    if kind == 'stock' and v == 'ok':
        import supplies; return supplies.stock_ack(con, by)
    if kind == 'rtn' and v in ('done', 'skip'):
        import siteroutine
        return siteroutine.skip(con, iid, by) if v == 'skip' else f'已記下：定期項目做完了（{siteroutine.done(con, iid, by)} 項）'
    if kind == 'att' and v == 'ok':
        import crewcheck; return crewcheck.resolve(con, iid, by)
    if kind == 'aib' and v in ('ok', 'no'):
        import aibrief; return aibrief.decide(con, iid, v, by)
    if kind == 'pamt':                # 稽核 A1
        if not money:
            raise PermissionError('收款金額只有看得到金額的人可以改')
        it = next((x for x in pay_mismatch(con) if x['id'] == iid), None)
        if not it:
            return '這筆已經處理過了'
        if v == 'ok':
            ix.set_setting(con, f'pamt_ok:{iid}', json.dumps({'by': by, 'at': c.now()}, ensure_ascii=False)); return '✅ 已標記金額本來就不同'
        import payments
        oamt = con.execute('SELECT o.approved_amount FROM payment_requests p JOIN orders o ON o.id=p.order_id WHERE p.id=?', (iid,)).fetchone()[0]
        payments.edit(con, iid, by, amount=oamt, note=f'照工單金額改（{by}）')
        return f'✅ 收款單已改成 NT$ {oamt // 100:,}'
    if kind == 'rs':                                   # 業主申請改時間
        import reschedule
        return reschedule.approve(con, iid, by) if v == 'ok' else reschedule.reject(con, iid, by, note)
    if kind == 'cmsg':
        acc, _, uid = iid.partition(':')
        return lb.cmsg_done(con, acc, uid, by)
    if kind == 'crew':
        import leave
        if v == 'ok':
            return leave.crew_approve(con, iid, by)
        if v.startswith('swap:'):
            return leave.crew_swap(con, iid, v[5:], by)
        if v.startswith('cut:'):                     # 減少人數
            return leave.crew_reduce(con, iid, v[4:], by)
    if kind == 'lvd':
        import leave
        if v in leave.KIND and v != 'withdrawn':
            return leave.decide(con, iid, v, by)
    if kind == 'lvh':
        import leave
        if v in leave.HOW:
            return leave.handle(con, iid, v, by)
    if kind == 'ent':
        import door
        if v == 'skip':
            return door.fill_skip(con, iid, by)
        if v == 'ok':
            return door.staff_ok(con, iid, by)
    if kind == 'gap' and v in ('ok', 'no'):
        import dispatch
        n, _, x = iid.partition('|')
        return dispatch.gap_answer(con, n, x, v == 'ok', by)
    if kind == 'prep':
        import prep, board
        if v == 'ok':
            board.log(con, iid, 'prep_ok', by); return '👍 好的，這場照常'
        if v == 'add':
            if not (note or '').strip():
                raise ValueError('請寫要提醒夥伴的事')
            prep.add_note(con, iid, note, by); return '✅ 已加進給夥伴的提醒'
    if kind == 'wdup' and v in ('same', 'diff'):
        k, _, d = iid.partition('|')
        return lb.worker_dup_decide(con, k, d, v == 'same', by)
    if kind == 'grp' and v in ('ok', 'no'):
        return lb.group_decide(con, iid, v == 'ok')
    if kind == 'guide' and v == 'ok':
        c.CTX.actor = by
        return lb.send_guide(con, by)
    if kind == 'pd' and v in ('ok', 'no') and money:
        return lb.privacy_decide(con, iid, v == 'ok', by)
    if kind == 'photo' and v == 'ok':
        return lb.photo_release(con, iid, by)
    if kind == 'sup' and v in ('ok', 'no'):
        import supplies; return supplies.decide(con, iid, v == 'ok', by)
    if kind == 'rv' and money:
        import reviews
        row, msg = reviews.decide(con, iid, 'approved' if v == 'ok' else 'changes', by, note if v == 'changes' else '')
        if msg:
            return msg
        if lb.OWNER_UID and '負責人' not in by:
            lb.queue(con, lb.WORK_ACCOUNT, lb.OWNER_UID, 'admin', f"{by} {'核准' if v == 'ok' else '要修改'}：{row['title']}",
                     lb._txt(f"{'✅' if v == 'ok' else '✏️'} {by}（中控台）{'核准了' if v == 'ok' else '要修改'}【{row['category']}】{row['title']}" + (f'\n意見：{note}' if v == 'changes' and note else '')))
        return '✅ 已核准' if v == 'ok' else '✏️ 已記下修改意見'
    if kind == 'qreason' and money:
        import quickquote
        ans = (note or '').strip() if v == 'write' else v
        if not ans:
            raise ValueError('請寫下原因')
        return quickquote.manual_reason(con, iid, ans[:200], by)
    raise ValueError('這個按鈕不適用')
