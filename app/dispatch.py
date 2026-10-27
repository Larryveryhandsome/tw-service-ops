"""自動派工：媒合人選、批次邀請、額滿收單、逾時遞補；夥伴可排時間；客戶預約表單。
只用客觀條件（區域、可排時間、服務過、撞期、負荷）排序，不用年齡性別評分；最後人選仍可由人改。"""
import datetime as dt
import json
import re
import core as c
import integrations as ix
from board import OPEN_STATUSES, area_of, log, publish

DISPATCH_WAIT_MIN = 30      # 邀請多久沒回就換下一批
SERVICE_TYPES = ['一般到府服務', '店家服務', '年度保養／細清', '大型案場服務', '店面／辦公室服務', '搬遷前後服務', '其他']

def _plus(t, m):
    h, mi = map(int, t.split(':')); v = min(h * 60 + mi + m, 1439)
    return f'{v // 60:02d}:{v % 60:02d}'

def _same_day(con, worker_id, o):
    return c.rows(con, "SELECT o.start,o.end,o.title,s.area,s.address,s.name site_name,s.service_type FROM assignments a JOIN orders o ON o.id=a.order_id LEFT JOIN sites s ON s.id=o.site_id "
                       "WHERE a.worker_id=? AND a.status='accepted' AND o.date=? AND o.id!=? AND o.status IN ('review','draft','confirmed','completed') ORDER BY o.start",
                  (worker_id, o['date'], o['id']))

def _clash(con, worker_id, o):
    """時間真的重疊才算撞期（同一天多個案場可以排）；店長設了同天兩場最少間隔（decisions.gap_rule）→ 間隔不夠也算。"""
    import decisions
    me = _area(con, o)
    for x in _same_day(con, worker_id, o):
        if not (x['start'] and x['end']):
            continue
        if x['start'] < o['end'] and x['end'] > o['start']:
            return x['title']
        gap = _gap(o, x)
        if decisions.gap_verdict(con, me, area_of(x), gap) == 'no':
            return f"{x['title']}（只隔 {gap} 分，店長說過這樣趕不上）"
    return None

def _area(con, o):
    s = con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'],)).fetchone() if o.get('site_id') else None
    return area_of(dict(s)) if s else ''

def _gap(o, x):
    m = lambda t: int(t[:2]) * 60 + int(t[3:5])
    return m(o['start']) - m(x['end']) if x['end'] <= o['start'] else m(x['start']) - m(o['end']) if o['end'] <= x['start'] else 0

def _gap_note(con, worker_id, o):
    """同一天其他案場：列出來給店長判斷交通趕不趕得上（間隔 < 30 分鐘特別標示）。"""
    notes = []
    for x in _same_day(con, worker_id, o):
        if not (x['start'] and x['end']):
            continue
        gap = _gap(o, x)
        where = x['area'] or (x['address'] or '')[:6]
        import decisions
        if decisions.gap_verdict(con, _area(con, o), area_of(x), gap) == 'ask':
            notes.append(f"同天 {x['start']}–{x['end']} {where}（間隔 {gap} 分，排了會問店長趕不趕得上）")
            continue
        notes.append(f"同天 {x['start']}–{x['end']} {where}" + (f"（間隔只有 {gap} 分）" if gap < 30 else f"（間隔 {gap} 分）"))
    return notes

def candidates(con, order_id):
    o = c.get(con, 'orders', order_id)
    s = dict(con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'],)).fetchone() or {}) if o['site_id'] else {}
    area = area_of(s); stype = s.get('service_type', '')
    taken = {r[0]: ('self_cancel' if r[1] == 'cancelled' and '自行取消' in (r[2] or '') else r[1]) for r in con.execute('SELECT worker_id,status,notes FROM assignments WHERE order_id=?', (o['id'],))}
    week0 = ''
    if o['date']:
        d = dt.date.fromisoformat(o['date']); week0 = (d - dt.timedelta(days=d.weekday())).isoformat()
    out = []
    import automation, tiers
    bl = automation.blacklist(con); tcache = tiers.all_tiers(con)
    for w in c.rows(con, "SELECT * FROM workers WHERE status='active'"):
        if w['id'] in bl:                  # 黑名單（店長、客服設）：不推薦、不自動詢問
            continue
        if taken.get(w['id']) in ('accepted', 'invited', 'applied'):
            continue
        if w['name'].startswith('（示範）') != (o['review_note'] == '示範工單'):     # 示範夥伴不參加真實派工；稽核 B1：示範單也只派給示範夥伴
            continue
        why = []; score = 0; block = {'declined': '已婉拒這筆', 'self_cancel': '已自行取消這筆'}.get(taken.get(w['id']), '')
        if '外包廠商' in (w['roles'] or ''):           # 外縣市服務廠商 → 不自動邀請，要用再手動排入（案件計酬）
            block = block or '外包廠商：要用再手動排入（案件計酬）'
        if o['date'] and o['start'] and o['end']:
            clash = _clash(con, w['id'], o)
            if clash:
                block = f'同時段已排 {clash}（實際錯開可按「直接排入」）'
            else:
                why += _gap_note(con, w['id'], o)
            av = c.rows(con, 'SELECT * FROM availability WHERE worker_id=? AND date=?', (w['id'], o['date']))
            if any(a['kind'] == 'busy' and (not a['start'] or (a['start'] < o['end'] and a['end'] > o['start'])) for a in av):
                block = block or '當天回報不能排'
            elif any(a['kind'] == 'free' and (not a['start'] or (a['start'] <= o['start'] and a['end'] >= o['end'])) for a in av):
                score += 25; why.append('有回報可排')
            elif av and all(a['kind'] == 'free' and a['start'] for a in av):        # 只回報上午（或下午）可以 → 另一個時段不邀請
                block = block or ('只有' + '、'.join(av_label(a) for a in av) + '可以')
            else:                                  # 當天沒回報 → 看夥伴資料的「平常可約時間」
                fit, why_ = weekly_fit(w['availability'] or '', o['date'], o['start'], o['end'])
                if fit is False:
                    block = block or why_
                elif fit:
                    score += 10; why.append('平常可約時段內')
            if '13:00' in (w['availability'] or '') + (w['notes'] or '') and o['start'] < '13:00':
                score -= 15; why.append('平常 13:00 後才可')
        served = con.execute("SELECT COUNT(*) FROM assignments a JOIN orders x ON x.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' "
                             "AND x.site_id=? AND x.status IN ('completed','confirmed','archived')", (w['id'], o['site_id'])).fetchone()[0] if o['site_id'] else 0
        if served:
            score += min(40, 20 + served * 5); why.append(f'服務過這裡 {served} 次')
        import regions as rg
        regs = (w['regions'] or '') + ' ' + (w['notes'] or '')
        specific = re.sub(r'[^、,，;；\s]*全區|全台[^、,，;；\s]*|都可[^、,，;；\s]*|遠近[^、,，;；\s]*', '', w['regions'] or '')   # 只看有指名的鄉鎮
        if area and rg.covers(specific, area):
            score += 20; why.append(f'區域含{area}')
        elif area and rg.covers(regs, area):
            score += 8; why.append('服務範圍內（全區）')
        if stype and stype[:2] in (w['skills'] or ''):
            score += 8; why.append(f'做過{stype[:2]}')
        if week0:
            load = con.execute("SELECT COUNT(*) FROM assignments a JOIN orders x ON x.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' "
                               "AND x.date BETWEEN ? AND date(?,'+6 day') AND x.status NOT IN ('cancelled','archived')", (w['id'], week0, week0)).fetchone()[0]
            score -= load * 2
            if load:
                why.append(f'本週已排 {load} 場')
        import crewcheck                 # 離案場近、自備工具齊的優先
        sc_, why_ = crewcheck.rank(con, o, s, w); score += sc_; why += why_
        tr = tcache.get(w['id'], '')
        if tr in tiers.TIERS:            # 店長排的派工等級
            score += {'A': 15, 'B': 0, 'C': -15}[tr]; why.insert(0, f'{tr} 級')
        out.append({'worker_id': w['id'], 'name': w['name'], 'score': score, 'why': why, 'blocked': block, 'tier': tr or 'B'})
    out.sort(key=lambda x: (bool(x['blocked']), -x['score'], x['name']))
    return out

def preferred_workers(con, o):
    """業主指定（客戶資料「指定夥伴」）＋歷史常配合（同一客戶服務 2 次以上）。只回可派、沒撞期的人。"""
    if not o['customer_id']:
        return []
    cu = c.get(con, 'customers', o['customer_id'])
    ids = [x for x in (cu.get('preferred_workers') or '').split(',') if x]
    for r in con.execute("SELECT a.worker_id,COUNT(*) n FROM assignments a JOIN orders x ON x.id=a.order_id WHERE x.customer_id=? AND a.status='accepted' "
                         "AND x.status IN ('archived','completed','confirmed') GROUP BY a.worker_id HAVING n>=2 ORDER BY n DESC LIMIT 2", (o['customer_id'],)):
        if r[0] not in ids:
            ids.append(r[0])
    ok = {x['worker_id']: x for x in candidates(con, o['id']) if not x['blocked']}
    return [w for w in ids if w in ok]

def auto_dispatch(con, p):
    """發布到接單大廳，並邀請前幾名（預設需求人數 × 2）。夥伴在手機按接受；額滿自動收單。"""
    o = c.get(con, 'orders', p['order_id'])
    if o['status'] not in OPEN_STATUSES:
        raise ValueError('只有尚未確認排程的工單可以派工')
    if o['dispatch'] != 'open':
        publish(con, {'order_id': o['id'], 'note': p.get('note', o['dispatch_note'])})
    accepted = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
    if accepted >= o['required_workers']:
        return {'invited': [], 'message': '人數已滿'}
    pending = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='invited'", (o['id'],)).fetchone()[0]
    room = max(0, int(p.get('batch') or (o['required_workers'] - accepted) * 2) - pending)
    cands = candidates(con, o['id'])
    pref = [] if p.get('worker_ids') or (o['dispatch_stage'] or 0) > 0 else preferred_workers(con, o)
    if pref:      # 第一階段：只邀請業主指定／常配合的夥伴（時間可協調），60 分鐘沒回才擴大
        picked = [x for x in cands if x['worker_id'] in pref]; stage = 1
        for x in picked:
            x['why'] = ['業主指定／常配合'] + x['why']
    else:
        picked = [x for x in cands if not x['blocked']][:room]; stage = (o['dispatch_stage'] or 0) + 1
    if p.get('worker_ids'):
        picked = [x for x in cands if x['worker_id'] in p['worker_ids']]
    only = 1 if p.get('exclusive') else (o['dispatch_only'] or 0) if p.get('worker_ids') else 0      # 勾選名單＝只有他們看得到；系統自動派＝開放；中控台多邀一位＝維持原本設定
    con.execute('UPDATE orders SET dispatch_only=? WHERE id=?', (only, o['id']))
    con.execute('UPDATE orders SET dispatch_stage=? WHERE id=?', (stage, o['id']))
    invited = []
    import board as _bd
    unpaid = (bool(p.get('unpaid')) and _bd.is_redo(o)) or _bd.redo_unpaid(o)      # 每日檢查：補服務不支薪 → 發卡前就是 0 元（以前卡片先寫時薪收入，事後才改 0）
    for cand in picked:
        w = c.get(con, 'workers', cand['worker_id'])
        old = con.execute('SELECT id FROM assignments WHERE order_id=? AND worker_id=?', (o['id'], w['id'])).fetchone()
        if old:
            con.execute("UPDATE assignments SET status='invited',version=version+1 WHERE id=?", (old[0],))
        else:
            con.execute('INSERT INTO assignments(id,order_id,worker_id,status,pay_mode,rate,notes) VALUES(?,?,?,?,?,?,?)',
                        (c.uid('ass'), o['id'], w['id'], 'invited', 'hourly', c.pay_rate(con, o['id'], w['id']), '系統邀請：' + '、'.join(cand['why'])))
        if unpaid:
            con.execute("UPDATE assignments SET pay_mode='fixed',amount=0,notes='補掃不支薪（主管判斷）' WHERE order_id=? AND worker_id=?", (o['id'], w['id']))
        invited.append(w['name']); log(con, o['id'], 'invite', w['name'] + ('（' + '、'.join(cand['why']) + '）' if cand['why'] else ''))
    if invited:
        import line_bot
        line_bot.on_dispatch(con, o['id'], [x['worker_id'] for x in picked], preferred=bool(pref))   # LINE 派工卡：一人一單發到夥伴個人群組
        import notify
        notify.workers(con, [x['worker_id'] for x in picked], '示範服務店有新的工作邀請',
                       f"{o['date'][5:].replace('-', '/')} {o['start']}–{o['end']}，打開看詳情並按接受", '/#jobs')
    import line_bot
    unreachable = [c.get(con, 'workers', x['worker_id'])['name'] for x in picked if not line_bot.worker_target(con, x['worker_id'])[1]]
    msg = f'已邀請 {len(invited)} 位：' + '、'.join(invited) if invited else '沒有符合條件的人選，請人工聯絡'
    if unreachable:         # 沒綁官方 LINE：卡片已轉到派單管理群，請管理層打電話問
        msg += f"｜📵 {'、'.join(unreachable)} 沒綁官方 LINE，派工卡已轉到派單管理群，請打電話問他們"
    return {'invited': invited, 'unreachable': unreachable, 'message': msg}

def gap_watch(con, order_id):
    """有人接下這筆：他同一天另一場跟這場間隔短（不重疊）、又沒問過 → 卡片問店長「趕得上嗎？」。
    她的回答記住（decisions.gap_learn），同兩地、這個間隔以上之後不再問；趕不上 → 自動換人。"""
    import decisions, integrations as ix, line_bot as lb
    o = c.get(con, 'orders', order_id)
    if not (o['date'] and o['start'] and o['end']):
        return 0
    me = _area(con, o); n = 0
    for a in c.rows(con, "SELECT id,worker_id FROM assignments WHERE order_id=? AND status='accepted'", (order_id,)):
        for x in c.rows(con, "SELECT a.id aid,o.start,o.end,o.title,s.area,s.address FROM assignments a JOIN orders o ON o.id=a.order_id LEFT JOIN sites s ON s.id=o.site_id "
                             "WHERE a.worker_id=? AND a.status='accepted' AND o.date=? AND o.id!=? AND o.status IN ('review','draft','confirmed')", (a['worker_id'], o['date'], o['id'])):
            if not (x['start'] and x['end']) or (x['start'] < o['end'] and x['end'] > o['start']):
                continue                    # 時間重疊＝撞期，另外處理（中控台確認錯開才排得進來）
            gap = _gap(o, x)
            if decisions.gap_verdict(con, me, area_of(x), gap) != 'ask':
                continue
            key = 'gapask:' + ':'.join(sorted([a['id'], x['aid']]))
            if ix.setting(con, key):
                continue
            ix.set_setting(con, key, json.dumps({'at': c.now(), 'n': a['id'], 'x': x['aid'], 'gap': gap}))      # 中控台「管理群待處理」也列得出來
            lb.gap_card(con, a['id'], x['aid'], gap); n += 1
    return n

def gap_answer(con, aid_new, aid_other, ok, who):
    """店長回答「趕得上嗎？」：記住（兩地＋間隔）；趕不上 → 這場換人（leave.crew_swap 自動另外派工）。"""
    import decisions, integrations as ix, leave
    key = 'gapans:' + ':'.join(sorted([aid_new, aid_other]))
    if ix.setting(con, key):
        return f'這題已經回答過了（{ix.setting(con, key)}）'
    an, ax = c.get(con, 'assignments', aid_new), c.get(con, 'assignments', aid_other)
    on, ox = c.get(con, 'orders', an['order_id']), c.get(con, 'orders', ax['order_id'])
    gap = _gap(on, ox)
    where = decisions.gap_learn(con, _area(con, on), _area(con, ox), gap, ok).replace('|', '↔')
    ix.set_setting(con, key, f"{who}：{'趕得上' if ok else '趕不上'}")
    c.audit(con, 'gap_answer', 'assignments', aid_new, after={'ok': ok, 'gap': gap, 'pair': where, 'by': who})
    if ok:
        return f'✅ 記住了：{where} 隔 {gap} 分以上趕得上，之後同樣情況不再問妳'
    if an['status'] != 'accepted':
        return f'記住了：{where} 只隔 {gap} 分趕不上（這位已經不在這場了）'
    return f'記住了：{where} 只隔 {gap} 分趕不上，之後不會這樣排。\n' + leave.crew_swap(con, an['order_id'], an['worker_id'], who + '（趕不上）')

def after_accept(con, order_id, reviewed=False):
    """有人接受後：額滿就收單、其他邀請與報名改為已額滿。"""
    gap_watch(con, order_id)           # 同天另一場間隔短 → 問店長趕不趕得上
    o = c.get(con, 'orders', order_id)
    n = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
    if n >= o['required_workers']:
        con.execute("UPDATE assignments SET status='cancelled',notes=notes||'（已額滿）',version=version+1 WHERE order_id=? AND status IN ('invited','applied')", (o['id'],))
        if o['dispatch'] == 'open':
            con.execute("UPDATE orders SET dispatch='filled' WHERE id=?", (o['id'],))
        log(con, o['id'], 'filled', f"{n}/{o['required_workers']}")
        import line_bot, leave, notify
        leave.ensure(con)
        if not reviewed and not c.get(con, 'orders', o['id']).get('crew_ok'):      # 負責人 每次派員都先讓管理群審核，才通知業主
            leave.crew_review(con, o['id'])
            notify.staff(con, '人員到齊，請審核', f"{o['date']} {o['start']} {o['title']}：在派單管理群按「人員沒問題」才會通知業主", '/#dispatch')
            return
        if reviewed:
            con.execute("UPDATE orders SET crew_ok=? WHERE id=? AND COALESCE(crew_ok,'')=''", (f"{getattr(c.CTX, 'actor', '後台')}（後台指派）", o['id']))
        line_bot.on_filled(con, o['id'])          # 客戶收到「已安排好服務人員」卡
        if o['customer_confirmed'] and line_bot._auto_confirm(con, o['id']) == '已排定':     # 客戶已簽收報價／已確認 → 人到齊自動排定（App 或 LINE 接單都適用）
            notify.staff(con, '已自動排定', f"{o['date']} {o['start']} {o['title']}：客戶已確認、夥伴到齊", '/#schedule')
            line_bot.admin_notify(con, '✅ 已自動排定', [('說明', '客戶已確認、夥伴到齊，地址已傳給夥伴')], '/#schedule', '#2E7D32', oid=o['id'])
        else:
            notify.staff(con, '派工已滿編', f"{o['date']} {o['title']} 已有 {n} 位接單，請確認客戶後排定", '/#dispatch')

def escalate(con):
    """背景每分鐘：邀請超過 30 分鐘沒回 → 記為逾時未回，改邀下一批。安靜時段（22:00–08:00）暫停，不然 08:00 會一次湧出好幾批。"""
    import line_bot
    quiet = line_bot.quiet()
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=DISPATCH_WAIT_MIN)).strftime('%Y-%m-%d %H:%M:%S')   # dispatch_log.at 是 UTC
    today = dt.datetime.now(c.TZ).date().isoformat()
    moved = 0
    cutoff_pref = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=DISPATCH_WAIT_MIN * 2)).strftime('%Y-%m-%d %H:%M:%S')
    for o in c.rows(con, "SELECT * FROM orders WHERE dispatch='open' AND status IN ('review','draft') AND date>=? AND COALESCE(review_note,'')!='示範工單' "
                         "AND NOT (date=? AND COALESCE(start,'')<=?)", (today, today, dt.datetime.now(c.TZ).strftime('%H:%M'))):      # 稽核 A2、B1：開工已過、示範單不換批
        if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='tier_ask'", (o['id'],)).fetchone() and not con.execute(
                "SELECT 1 FROM dispatch_log WHERE order_id=? AND action IN ('auto_ask','auto_ask2')", (o['id'],)).fetchone():
            continue                       # 稽核 M4：分級派工還沒問完 → 由 tiers 決定時間，這裡不標逾時、不換批
        if (quiet and not line_bot.urgent_order(o)) or line_bot.held(con, o['id']):
            continue                       # 每日檢查：安靜時段只處理明早開工的；卡片還在等 08:00 送 → 還沒開始等，不算逾時
        last = con.execute("SELECT MAX(at) FROM dispatch_log WHERE order_id=? AND action IN ('invite','publish','escalate')", (o['id'],)).fetchone()[0]
        if not last or last > (cutoff_pref if (o['dispatch_stage'] or 0) == 1 else cutoff):   # 指定夥伴多等一倍時間
            continue
        stale = con.execute("SELECT id FROM assignments WHERE order_id=? AND status='invited'", (o['id'],)).fetchall()
        if not stale:
            continue
        if o['dispatch_only']:
            if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND (action='stale_notice' AND at>? OR action IN ('auto_ask','auto_ask2','tier_ask'))", (o['id'], last)).fetchone():
                continue                   # 系統自動詢問發的：能問的都問了，由 ask_shortage 第二輪追，不再叫管理群「改發給其他夥伴」
            log(con, o['id'], 'stale_notice', f'{len(stale)} 位未回')
            import line_bot
            line_bot.admin_notify(con, '⏰ 指定派工還沒人回', [('工作', f"{o['date'][5:].replace('-', '/')} {o['start']}–{o['end']}"),
                                                          ('未回', f'{len(stale)} 位'), ('處理', '請到派工改發給其他夥伴')], '/#dispatch', '#C47F00', oid=o['id'])
            moved += 1
            continue
        for (aid,) in stale:
            con.execute("UPDATE assignments SET status='declined',notes=notes||'（逾時未回）',version=version+1 WHERE id=?", (aid,))
        r = auto_dispatch(con, {'order_id': o['id']})
        log(con, o['id'], 'escalate', r['message']); moved += 1
        import notify
        notify.staff(con, '派工邀請逾時沒回', f"{o['date'][5:]} {o['start']} {o['title']}：已改邀下一批（{r['message']}）", '/#dispatch')
    return moved

# ── 夥伴可排時間（每月回報；取代「10 月請問有可安排的時間嗎」一個一個問）──
AV_SLOTS = {'am': ('08:00', '13:00'), 'pm': ('13:00', '18:00')}       # 一天分上午／下午（預約時段 09–13、13–17、14–18 都整段落在其中一個）

def av_label(a):
    """可排時間的顯示：整天／上午／下午／自訂時間。"""
    if not a.get('start'):
        return '整天'
    return {v: k for k, v in {'上午': AV_SLOTS['am'], '下午': AV_SLOTS['pm']}.items()}.get((a['start'], a['end']), f"{a['start']}–{a['end']}")

# ── 夥伴資料的「平常可約時間」文字 → 派工自己判斷。當天有回報可排／不能排的，以回報為準 ──
_PERIODS = {'am': ('00:00', '13:00'), 'pm': ('13:00', '18:00'), 'eve': ('18:00', '24:00')}       # 上午／下午跟 AV_SLOTS 同一條線（13:00）
_PNAME = {'am': '上午', 'pm': '下午', 'eve': '晚上'}
_WD = '一二三四五六日'

def _hm(t):
    h, m = (t or '0:0').split(':')[:2]
    return int(h) * 60 + int(m)

def weekly_rules(text):
    """「平日上午、假日都可」「平日(週三不行)」「13:00過後」「不接」→ (可以, 不行, 全部不接)。每項 (星期集合／None, 時段集合／None, 最早開始分, 最晚結束分)。
    看不懂的字（「彈性」「有正職，先問」）不限制；有「都可以」就只看「哪天不行」。"""
    t = (text or '').replace('周', '週').replace('星期', '週').replace('禮拜', '週').replace('週末', '假日').replace('：', ':')
    if re.fullmatch(r'\s*(不接|不接案|暫停)\s*', t):
        return [], [], True
    allow, deny, anyok = [], [], False
    for cl in re.split(r'[，,、；;。/／()\s]+', t):
        if not cl:
            continue
        days = set()
        if '平日' in cl:
            days |= {0, 1, 2, 3, 4}
        if '假日' in cl:
            days |= {5, 6}
        for m in re.finditer(r'週([一二三四五六日天]+)', cl):
            days |= {_WD.index('日' if ch == '天' else ch) for ch in m.group(1)}
        per = set()
        if re.search('上午|早上', cl):
            per.add('am')
        if '下午' in cl:
            per.add('pm')
        if re.search('晚上|晚間', cl):
            per.add('eve')
        if '白天' in cl:
            per |= {'am', 'pm'}
        aft = re.search(r'(\d{1,2})(?::(\d{2}))?點?(?:過後|以後|之後|後)', cl)
        bef = re.search(r'(\d{1,2})(?::(\d{2}))?點?(?:以前|之前|前)', cl)
        lo = int(aft.group(1)) * 60 + int(aft.group(2) or 0) if aft else None
        hi = int(bef.group(1)) * 60 + int(bef.group(2) or 0) if bef else None
        if per & {'pm', 'eve'}:                      # 「下午 3 點後」＝15:00
            lo = lo + 720 if lo is not None and lo < 720 else lo
            hi = hi + 720 if hi is not None and hi < 720 else hi
        neg = re.search('不行|不可|不能|沒辦法|無法|沒空', cl)
        if not (days or per or lo is not None or hi is not None):
            if not neg and re.search('都可|彈性|隨時|皆可', cl):
                anyok = True
            continue
        (deny if neg else allow).append((days or None, per or None, lo, hi))
    return ([] if anyok else allow), deny, False

def weekly_fit(text, date, start, end):
    """這場在不在夥伴平常可約時間內：(True／False／None＝沒寫或看不懂, 說明)。"""
    if not (text or '').strip() or not (date and start and end):
        return None, ''
    if '寒暑假' in text and date[5:7] in ('01', '02', '07', '08'):      # 「寒暑假 假日」：寒暑假期間每天都可以
        return None, ''
    allow, deny, none_ = weekly_rules(text)
    short = text.strip()[:24]
    if none_:
        return False, f'平常可約：{short}'
    d = dt.date.fromisoformat(date).weekday(); s, e = _hm(start), _hm(end)
    ov = {k: min(e, _hm(b)) - max(s, _hm(a)) for k, (a, b) in _PERIODS.items()}
    need = {k for k, v in ov.items() if v > 60} or {max(ov, key=ov.get)}       # 跨到別的時段 1 小時內不算（例 09–13 算上午）
    why = f"平常可約：{short}（這場是週{_WD[d]}{'、'.join(_PNAME[k] for k in _PERIODS if k in need)}）"
    for days, per, lo, hi in deny:
        if (days is None or d in days) and (per is None or per & need):
            return False, why
    if not allow:
        return None, ''
    ok_per = set()
    for days, per, lo, hi in allow:
        if days is not None and d not in days:
            continue
        if lo is None and hi is None:
            ok_per |= (per or set(_PERIODS))
        elif (lo is None or s >= lo) and (hi is None or e <= hi) and (per is None or need <= per):
            return True, ''
    return (True, '') if need <= ok_per else (False, why)

def set_availability(con, worker_id, p):
    c.get(con, 'workers', worker_id)
    month = p['month']; c.date(month + '-01')
    con.execute('DELETE FROM availability WHERE worker_id=? AND date LIKE ?', (worker_id, month + '%'))
    for d in (p.get('days') or [])[:62]:
        c.date(d['date'])
        if not d['date'].startswith(month):
            raise ValueError('日期不在這個月')
        kind = d.get('kind', 'free')
        if kind not in ('free', 'busy', 'unsure'):          # 不確定
            raise ValueError('種類不符')
        start, end = AV_SLOTS.get(d.get('slot')) or (d.get('start', '') or '', d.get('end', '') or '')
        if start or end:
            c.minutes(start, end)
        con.execute('INSERT OR REPLACE INTO availability(id,worker_id,date,start,end,kind,note) VALUES(?,?,?,?,?,?,?)',
                    (c.uid('av'), worker_id, d['date'], start, end, kind, str(d.get('note', ''))[:200]))
    if 'note' in p:                          # 填空式說明（例：11/13 之後每週五校舍路不接）→ 管理群看得到
        note = str(p.get('note') or '').strip()[:200]
        w = c.get(con, 'workers', worker_id)
        if note != (w.get('av_note') or '').strip():
            con.execute('UPDATE workers SET av_note=? WHERE id=?', (note, worker_id))
            if note:
                import line_bot as lb
                lb.admin_notify(con, f"📝 {w['name']} 寫了可排時間說明", [('說明', note[:120]), ('處理', '有影響固定班（例：某天不接某個案場）請記得換人')], f'/#w/{worker_id}', '#0A84FF')
    c.audit(con, 'availability', 'workers', worker_id, after={'month': month, 'days': len(p.get('days') or []), 'note': 'note' in p})
    return {'saved': True}

def av_remind(con, now=None):
    """夥伴沒填當月可排時間 → LINE 提醒（10:00–20:00；同一個月每 3 天最多一次、最多 3 次）；25 號起也提醒下個月。"""
    import line_bot as lb, worker_portal
    now = now or dt.datetime.now(c.TZ)
    if ix.setting(con, 'av_remind') == 'off' or not ('10:00' <= now.strftime('%H:%M') < '20:00'):
        return 0
    months = [now.strftime('%Y-%m')] + ([(now.replace(day=1) + dt.timedelta(days=32)).strftime('%Y-%m')] if now.day >= 25 else [])
    today, n = now.date(), 0
    for w in c.rows(con, "SELECT id,name,roles FROM workers WHERE status='active' AND name NOT LIKE '（示範）%'"):
        if '外包廠商' in (w['roles'] or '') or __import__('automation').is_blacklisted(con, w['id']):      # 每日檢查：停派的不催
            continue
        acc, uid = lb.worker_target(con, w['id'])
        if not uid or uid in lb.staff_users(con):
            continue
        for m in months:
            if con.execute('SELECT 1 FROM availability WHERE worker_id=? AND date LIKE ?', (w['id'], m + '%')).fetchone():
                continue
            st = json.loads(ix.setting(con, f"av_rem:{w['id']}:{m}") or '{}')
            if st.get('n', 0) >= 3 or (st.get('last') or '') > (today - dt.timedelta(days=3)).isoformat():
                continue
            mm = int(m[5:])
            card = lb.flex(f'請回報 {mm} 月可排時間', lb._bubble(lb._header('📅 可排時間', f'請回報 {mm} 月哪幾天可以'), [
                lb._t(f"{w['name']} 你好，{mm} 月的可排時間還沒填 🙏", wrap=True),
                lb._t('點日期回報哪天可以；整個月都可以就按「整個月都可以」，都還不確定就按「整個月都不確定」。', size='sm', color=lb.SUB, wrap=True, margin='md')],
                [lb._btn('回報可排時間', uri=worker_portal.portal_url(con, w['id']) + '#avail', primary=True)], '派工會優先找有回報可以的人。'))
            lb.to_worker(con, w['id'], 'av_remind', card[0]['altText'], card)
            ix.set_setting(con, f"av_rem:{w['id']}:{m}", json.dumps({'n': st.get('n', 0) + 1, 'last': today.isoformat()}))
            n += 1
            break                                 # 一次只提醒一個月
    return n

# ── 客戶預約表單（官方 LINE 圖文選單連進來；只建立需求，不回覆、不報價）──
HOLD = 'dispatch_hold:'          # 「擋派工」：缺人自動詢問、人員自動通過都會跳過
HOLD_TAG = '找人中'

def held(con, oid):
    return bool(ix.setting(con, HOLD + (oid or '')))

def hold(con, oid, by, on=True):
    """管理層自己在問人 → 系統不要自動廣發（缺人詢問、分級、請假自動處理、另外派工都暫停）。"""
    import board
    c.get(con, 'orders', oid)
    if on:
        ix.set_setting(con, HOLD + oid, f'{HOLD_TAG}（{by}）：先不要自動派')
        board.log(con, oid, 'hold', by); c.audit(con, 'dispatch_hold', 'orders', oid, after={'by': by})
        return '⏸ 已設成「找人中」：系統先不自動問夥伴。找到人請在中控台這張工單「挑人派工 › 直接排入」；要讓系統幫忙問，按「恢復自動派」。開工前 12 小時還缺人會提醒一次。'
    ix.set_setting(con, HOLD + oid, ''); ix.set_setting(con, f'dhold_rem:{oid}', '')
    board.log(con, oid, 'unhold', by); c.audit(con, 'dispatch_unhold', 'orders', oid, after={'by': by})
    return '▶️ 已恢復自動派：系統會照規則問夥伴。'

def hold_tick(con):
    """每分鐘：「找人中」的單人到齊 → 自動解除；開工前 12 小時還缺人 → 提醒管理群一次。"""
    import line_bot as lb
    now = dt.datetime.now(c.TZ); n = 0
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'dispatch_hold:%' AND COALESCE(value,'')!=''").fetchall():
        oid = k[len(HOLD):]
        o = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not o or not str(v).startswith(HOLD_TAG):
            continue                                       # 那種手動擋的不動
        if o['status'] in ('cancelled', 'archived', 'completed'):
            ix.set_setting(con, k, ''); continue
        crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (oid,)).fetchone()[0]
        if crew >= (o['required_workers'] or 1):
            ix.set_setting(con, k, ''); continue
        try:
            start = dt.datetime.fromisoformat(f"{o['date']}T{o['start'] or '09:00'}").replace(tzinfo=c.TZ)
        except ValueError:
            continue
        if start - now <= dt.timedelta(hours=12) and not ix.setting(con, f'dhold_rem:{oid}'):
            ix.set_setting(con, f'dhold_rem:{oid}', c.now())
            lb.admin_notify(con, '⏸ 找人中的工作還缺人', [('工作', lb.when(dict(o))), ('還缺', f"{(o['required_workers'] or 1) - crew} 位"),
                                                    ('處理', '找到人請在中控台「直接排入」；要讓系統幫忙問，打開工單按「恢復自動派」')], '/#dispatch', '#C47F00', oid=oid)
            n += 1
    return n

def uninvite(con, aid, by):
    """取消邀請：邀請中／報名中 → 取消，夥伴收到「這場已經安排好了，不用回覆」；之後用舊卡按接單會擋。"""
    import line_bot as lb, board
    a = con.execute('SELECT * FROM assignments WHERE id=?', (aid,)).fetchone()
    if not a or a['status'] not in ('invited', 'applied'):
        raise ValueError('這筆不是邀請中／報名中，不能取消邀請')
    con.execute("UPDATE assignments SET status='cancelled',notes=TRIM(COALESCE(notes,'') || ' （' || ? || ' 取消邀請）'),version=version+1 WHERE id=?", (by, aid))
    o = c.get(con, 'orders', a['order_id']); w = c.get(con, 'workers', a['worker_id'])
    board.log(con, o['id'], 'uninvite', f"{w['name']}（{by}）")
    c.audit(con, 'uninvite', 'assignments', aid, after={'by': by})
    to = con.execute('SELECT account,user_id FROM line_users WHERE worker_id=? AND is_friend=1 ORDER BY account=? DESC LIMIT 1', (w['id'], lb.WORK_ACCOUNT)).fetchone()
    if to and o['review_note'] != '示範工單':
        lb.queue(con, to[0], to[1], 'uninvite', '這場已經安排好了', lb._txt(f"📅 {lb.when(dict(o))} 這場已經安排好了，邀請先取消，不用回覆，謝謝你 🙏\n有適合你的工作會再通知你。"), o['id'])
    return f"已取消邀請 {w['name']}" + ('，已在 LINE 告訴他' if to else '（他沒綁 LINE，沒有通知）')

def create_inquiry(con, p, ip, notify_admin=True):
    if p.get('website'):              # 機器人陷阱欄位
        raise ValueError('送出失敗')
    hour_ago = (dt.datetime.now(c.TZ) - dt.timedelta(hours=1)).isoformat(timespec='seconds')
    if con.execute("SELECT COUNT(*) FROM inquiries WHERE ip=? AND created_at>?", (ip, hour_ago)).fetchone()[0] >= 8:
        raise PermissionError('送出太多次，請直接在官方 LINE 留言')
    name = str(p.get('name', '')).strip()[:60]; phone = re.sub(r'[^\d+\-]', '', str(p.get('phone', '')))[:20]
    if not name or len(re.sub(r'\D', '', phone)) < 8:
        raise ValueError('請留下稱呼與手機')
    stype = p.get('service_type') if p.get('service_type') in SERVICE_TYPES else '其他'
    detail = {str(k)[:40]: str(v)[:300] for k, v in (p.get('detail') or {}).items()} if isinstance(p.get('detail'), dict) else {}
    import brands
    key = c.uid('inq'); acc = brands.pick(p.get('account'))
    con.execute('INSERT INTO inquiries(id,created_at,account,name,phone,line_name,area,address,service_type,detail,preferred,notes,ip) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key, c.now(), acc, name, phone, str(p.get('line_name', ''))[:60], str(p.get('area', ''))[:20], str(p.get('address', ''))[:200], stype,
                 json.dumps(detail, ensure_ascii=False), str(p.get('preferred', ''))[:300], str(p.get('notes', ''))[:1000], ip))
    import notify
    notify.staff(con, '新的預約需求', f"{name}｜{stype}｜{p.get('area', '')}", f'/#inq/{key}')
    import line_bot
    if notify_admin:     # 第一時間推到「派單管理」群
        line_bot.admin_notify(con, '📥 新的預約需求', [('客戶', f"{name}（{phone}）"), ('服務', stype), ('地區', p.get('area', '')), ('希望', p.get('preferred', ''))], f'/#inq/{key}')
    return {'ok': True, 'id': key, 'line': line_bot.inquiry_code(con, key, acc)}

def convert_inquiry(con, p):
    """需求 → 客戶＋案場＋待核對工單（不會自動確認、不會回覆客戶）。"""
    q = c.get(con, 'inquiries', p['id'])
    if q['order_id']:
        raise ValueError('這筆需求已經建立工單')
    qt = con.execute("SELECT id FROM quotes WHERE inquiry_id=? AND status='draft'", (q['id'],)).fetchone()
    if qt:          # 線上估價的需求：正本是報價單，簽收時會自動建客戶／案場／工單；這裡再轉會重複建一套
        raise ValueError(f"這筆是線上估價（報價單 {qt[0]}），請到「報價」確認並傳給客戶，客戶簽收後會自動建工單；不要的話請先把報價單取消")
    cid = p.get('customer_id') or c.save(con, 'customers', {'name': q['name'], 'line_name': q['line_name'], 'phone': q['phone'],
                                                            'line_account': q['account'] or 'demoshop', 'status': 'active', 'notes': '由預約表單建立'})['id']
    site = p.get('site_id') or c.save(con, 'sites', {'customer_id': cid, 'name': (q['name'] + ' ' + (q['area'] or '')).strip(), 'address': q['address'],
                                                     'service_type': q['service_type'], 'area': q['area'], 'notes': q['notes']})['id']
    d = json.loads(q['detail'] or '{}')
    o = c.save(con, 'orders', {'title': q['service_type'], 'site_id': site, 'date': p.get('date', ''), 'start': p.get('start', ''), 'end': p.get('end', ''),
                               'required_workers': p.get('required_workers', 1) or 1, 'billing_mode': 'fixed',
                               'notes': '預約表單：' + '；'.join(f'{k}：{v}' for k, v in d.items() if v) + (f"\n希望時段：{q['preferred']}" if q['preferred'] else ''),
                               'review_note': '由預約表單轉入，待報價與客戶確認'})
    con.execute("UPDATE inquiries SET status='booked',customer_id=?,order_id=?,version=version+1 WHERE id=?", (cid, o['id'], q['id']))
    if q['line_user_id']:     # 客戶用 LINE 傳過預約編號 → 之後的承接卡、提醒都直接發給他
        con.execute('UPDATE line_users SET customer_id=COALESCE(customer_id,?) WHERE user_id=? AND account=?', (cid, q['line_user_id'], q['account'] or 'demoshop'))
    c.audit(con, 'convert', 'inquiries', q['id'], after={'order_id': o['id']})
    import integrations as ix
    if o['date'] and o['start'] and o['end'] and p.get('auto_dispatch', ix.setting(con, 'dispatch_mode', 'manual') == 'auto'):
        auto_dispatch(con, {'order_id': o['id']})          # 時間定了就自動派工（LINE 派工卡）
    return c.get(con, 'orders', o['id'])

def inquiry_status(con, p):
    q = c.get(con, 'inquiries', p['id'])
    if p.get('status') not in ('new', 'contacted', 'quoted', 'booked', 'lost', 'spam'):
        raise ValueError('狀態不符')
    con.execute('UPDATE inquiries SET status=?,handled_note=?,version=version+1 WHERE id=?', (p['status'], str(p.get('note', ''))[:500], q['id']))
    return c.get(con, 'inquiries', q['id'])
