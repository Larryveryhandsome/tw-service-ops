"""B 系列自動化：背景每分鐘跑，全部走官方 LINE 一對一、點選操作。

1. 缺人自動詢問：14 天內缺人的工單，一次發給所有「可派工＋綁了 LINE＋沒被列黑名單」的夥伴（先按先得）。
   黑名單：店長、客服（管理選單）都能設。設定 auto_ask=off 可整個關掉。
2. 等客戶確認：人到齊時已傳確認卡；服務前一天還沒確認 → 再傳一次（line_bot.on_tomorrow）。
3. 客戶端匿名：客戶看不到夥伴姓名，改看「夥伴經歷＋作品集」（夥伴自己在主控板建立）。
4. 自動完工：所有夥伴都離場打卡 → 以打卡時間自動完工登記；服務日過了還沒打卡 → 隔天早上問夥伴「照排班時間完成？」。
5. 完工關心：完工後第 3 天自動傳關心卡（同一客戶 14 天內只傳一次）。
"""
import datetime as dt
import hashlib
import hmac
import json
import core as c
import integrations as ix

ASK_DAYS = 14            # 幾天內的工單缺人就自動問
CARE_AFTER = 3           # 完工後第幾天關心
CARE_GAP = 14            # 同一客戶多久內只關心一次
DEMO = '示範工單'          # 管理層切換夥伴視角用的示範工作（line_bot._demo_job）：不跑真的自動化（稽核）

def _today():
    return dt.datetime.now(c.TZ).date()

# ───────────── 黑名單 ─────────────
def blacklist(con):
    """目前有效的黑名單（有 until 的到期自動失效，例如請假記點停派半年）。"""
    try:
        bl = json.loads(ix.setting(con, 'worker_blacklist') or '{}')
    except ValueError:
        return {}
    today = _today().isoformat()
    return {k: v for k, v in bl.items() if not v.get('until') or v['until'] >= today}

def is_blacklisted(con, wid):
    return wid in blacklist(con)

def set_blacklist(con, wid, on, by, reason='', until=''):
    w = c.get(con, 'workers', wid)
    bl = blacklist(con); old = set(bl)
    if on and wid in bl and by == '系統（請假記點）' and bl[wid].get('by') != '系統（請假記點）':
        pass                             # 稽核 M2：管理層手動停派的不被「請假記點」的半年停派蓋掉（排回撤銷記點也不會解除）
    elif on:
        bl[wid] = {'name': w['name'], 'by': by, 'at': c.now(), 'reason': reason[:200], **({'until': until} if until else {})}
        # 已邀請但還沒回的：收回邀請；已接的：沒排定的拿掉另外派工、已排定的請管理群決定
        con.execute("UPDATE assignments SET status='cancelled',notes=notes||'（列入黑名單，收回邀請）',version=version+1 WHERE worker_id=? AND status IN ('invited','applied')", (wid,))
        if wid not in old:
            import leave; leave.worker_unavailable(con, wid, '停派（黑名單）')
    else:
        bl.pop(wid, None)
    ix.set_setting(con, 'worker_blacklist', json.dumps(bl, ensure_ascii=False))
    c.audit(con, 'worker_blacklist', 'workers', wid, after={'on': bool(on), 'by': by, 'reason': reason})
    return bl

# ───────────── 1. 缺人自動詢問 ─────────────
def reachable(con, wid):
    """一對一綁定官方 LINE 的夥伴才問得到（不再用群組）。負責人、店長（老闆）有夥伴身分也不自動問。"""
    import line_bot
    boss = line_bot.bosses(con)
    uids = [r[0] for r in con.execute('SELECT user_id FROM line_users WHERE worker_id=? AND is_friend=1', (wid,))]
    return bool(uids) and not any(u in boss for u in uids)

def ask_shortage(con):
    if (ix.setting(con, 'auto_ask', 'on') or 'on') == 'off':
        return 0
    import line_bot as _lb
    quiet = _lb.quiet()                # 安靜時段：只問明早 10:00 前開工的（其他等 08:00）
    import dispatch, line_bot, leave
    from board import log
    leave.ensure(con)
    t = _today()
    n = 0
    for o in c.rows(con, "SELECT * FROM orders WHERE status IN ('review','draft') AND date BETWEEN ? AND ? AND start!='' AND end!='' AND COALESCE(site_id,'')!='' "
                         "AND (COALESCE(dispatch_only,0)=0 OR EXISTS(SELECT 1 FROM dispatch_log d WHERE d.order_id=orders.id AND d.action IN ('auto_ask','tier_ask')) OR date<=?) "
                         "AND COALESCE(review_note,'')!='示範工單'", (t.isoformat(), (t + dt.timedelta(days=ASK_DAYS)).isoformat(), (t + dt.timedelta(days=3)).isoformat())):
        crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        if crew >= (o['required_workers'] or 1):
            continue
        if o['date'] == t.isoformat() and o['start'] <= dt.datetime.now(c.TZ).strftime('%H:%M'):
            continue                     # 稽核 A2：開工時間已經過了 → 不再廣發（以前一次問 23 位）
        if (quiet and not _lb.urgent_order(o)) or _lb.held(con, o['id']):
            continue                     # 每日檢查：第一輪卡片還在等 08:00 送 → 第二輪的 6 小時從真的送出後才算
        if con.execute("SELECT 1 FROM worker_leaves WHERE order_id=? AND COALESCE(handling,'')=''", (o['id'],)).fetchone():
            continue                     # 有人請假、管理群還沒選怎麼處理（讓業主選／等原人員）→ 先不要自己廣發
        if con.execute("SELECT 1 FROM worker_leaves WHERE order_id=? AND handling='ask'", (o['id'],)).fetchone():      # 稽核：選了「讓業主選擇」→ 等業主選
            ch = con.execute("SELECT detail FROM dispatch_log WHERE order_id=? AND action='customer_leave_choice' ORDER BY id DESC LIMIT 1", (o['id'],)).fetchone()
            if ch and ch[0] in ('wait', 'resch'):
                continue                 # 業主選等原人員／改期：由管理層改期
            if not ch:
                at = con.execute("SELECT MAX(at) FROM dispatch_log WHERE order_id=? AND action='leave_handle'", (o['id'],)).fetchone()[0] or ''
                try:
                    waited = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(at.replace(' ', 'T')).replace(tzinfo=dt.timezone.utc)
                except ValueError:
                    waited = dt.timedelta(hours=99)
                if waited < dt.timedelta(hours=6) and o['date'] > (t + dt.timedelta(days=1)).isoformat():
                    continue             # 等業主回（最多 6 小時；服務在 24 小時內不等）
                import leave             # 自動出路：業主沒回 → 照「另外派人」處理，通知業主和群
                c.CTX.actor = '系統（業主沒回，自動另外派人）'
                leave.customer_choice(con, o['id'], 'other', '系統')
                acc_, uid_ = _lb.customer_target(con, o['customer_id'])
                if uid_:
                    m_ = _lb.card_text('人員異動通知', f"{_lb.when(dict(o))} 我們先為您另外安排服務人員，時間不變；確定後會再通知您 🙏 想改期或等原本的人員，直接在這裡告訴我們。", '#C47F00', brand=acc_)
                    _lb.queue(con, acc_, uid_, 'leave_notice', m_[0]['altText'], m_, o['id'])
                continue
        asked = con.execute("SELECT MIN(at) FROM dispatch_log WHERE order_id=? AND action='auto_ask'", (o['id'],)).fetchone()[0]
        tiered = con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='tier_ask'", (o['id'],)).fetchone()     # 分級派工進行中：由 tiers.step 接著問
        if not asked and not tiered and o['date'] <= (t + dt.timedelta(days=3)).isoformat() and con.execute("SELECT 1 FROM assignments WHERE order_id=? AND status='invited'", (o['id'],)).fetchone():
            asked = con.execute("SELECT MAX(at) FROM dispatch_log WHERE order_id=? AND action IN ('invite','publish')", (o['id'],)).fetchone()[0] or '2000-01-01'   # 店長手動邀請過、3 天內還缺人 → 一樣再問一次
        if asked:
            if asked > (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S'):
                continue                 # 第一輪問完至少 6 小時才再問（dispatch_log.at 是 UTC）                        # 第二輪：服務前 3 天內還缺人 → 沒回的再問一次、新可派的也問；明確婉拒的不再問
            if o['date'] > (t + dt.timedelta(days=3)).isoformat() or con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='auto_ask2'", (o['id'],)).fetchone():
                continue
            again = [r[0] for r in con.execute("SELECT a.worker_id FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND w.status='active' "
                                               "AND (a.status='invited' OR (a.status='declined' AND a.notes LIKE '%逾時未回%'))", (o['id'],))      # 稽核 M3：暫停的不再邀請
                     if reachable(con, r[0]) and not is_blacklisted(con, r[0])]          # 逾時沒回≠婉拒：一樣再問
            new = [x['worker_id'] for x in dispatch.candidates(con, o['id']) if not x['blocked'] and reachable(con, x['worker_id'])]
            import tiers as _tr
            if _tr.batch_size(con):                    # 不要一次發太多人 → 照分級順序、案件數多的先，只問一批
                tc = _tr.all_tiers(con); order_ = {t: i for i, t in enumerate(_tr.route(con, o) + ('D',))}
                new = sorted(new, key=lambda w: (order_.get(_tr.tier(con, w, tc), 9), -_tr.jobs(con, w)))
                new = [w for w in new if _tr.tier(con, w, tc) != 'D' or _tr.d_ok(con, o)][:_tr.batch_size(con)]
            if new:
                dispatch.auto_dispatch(con, {'order_id': o['id'], 'worker_ids': new, 'exclusive': True, 'batch': len(new)})
            if again:
                for wid_ in again:            # 稽核 M3：逾時未回的改回邀請中（以前補傳卡片但按了接不到）
                    con.execute("UPDATE assignments SET status='invited',version=version+1 WHERE order_id=? AND worker_id=? AND status='declined' AND notes LIKE '%逾時未回%'", (o['id'], wid_))
                line_bot.on_dispatch(con, o['id'], again)
            log(con, o['id'], 'auto_ask2', f'3 天內還缺人：再問 {len(again)} 位、新問 {len(new)} 位')
            line_bot.admin_notify(con, '⏰ 3 天內還缺人，已再問一次', [('工作', line_bot.when(dict(o))), ('再問', f'沒回的 {len(again)} 位、新的 {len(new)} 位'),
                                  ('處理', '還是沒人的話請打電話找人，或跟客戶改期')], '/#dispatch', '#C62828', oid=o['id'])
            n += 1
            continue
        if o['dispatch_only'] and not con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='tier_ask'", (o['id'],)).fetchone() \
                and con.execute("SELECT 1 FROM assignments WHERE order_id=? AND status='invited'", (o['id'],)).fetchone():
            continue                     # 店長在中控台勾選名單指定邀請：不自動發給別人（服務前 3 天才由第二輪接手）
        import sitetools                 # 開單（派工）之前先請業主回覆要不要自備工具
        if not con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='tier_ask'", (o['id'],)).fetchone() and sitetools.gate(con, o):
            continue
        import tiers                     # 分級派工（業主指定／常配合 → A → B → C，依開單到開工時間決定每級等多久）
        r = tiers.step(con, o)
        if r == 'done' and not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND status IN ('invited','accepted')", (o['id'],)).fetchone():
            line_bot.admin_notify(con, '⚠️ 缺人，但沒有可以自動詢問的夥伴', [('工作', line_bot.when(dict(o))), ('處理', '請到派工挑人或打電話')], '/#dispatch', '#C62828', oid=o['id'])
        n += r != 'wait'
    return n

# ───────────── 3. 夥伴作品集（匿名） ─────────────
def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS worker_profiles(worker_id TEXT PRIMARY KEY, intro TEXT DEFAULT '', since TEXT DEFAULT '', updated_at TEXT DEFAULT '')")
    con.execute("CREATE TABLE IF NOT EXISTS portfolio(worker_id TEXT, attachment_id TEXT, created_at TEXT, PRIMARY KEY(worker_id, attachment_id))")

# 資安：作品集照片只能來自「客戶在見證同意不具名分享」的工單（testimonials.consent='public'）
CONSENTED = ("SELECT at.id FROM attachments at WHERE at.label IN ('before','after') AND at.order_id IN "
             "(SELECT order_id FROM testimonials WHERE consent='public')")

def experience(con, wid):
    import testimonials; testimonials.ensure(con)
    ensure(con)
    p = dict(con.execute('SELECT * FROM worker_profiles WHERE worker_id=?', (wid,)).fetchone() or {})
    jobs = con.execute("SELECT COUNT(*) FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.status IN ('completed','archived')", (wid,)).fetchone()[0]
    years = ''
    if (p.get('since') or '').isdigit():
        years = max(0, _today().year - int(p['since']))
    photos = [r[0] for r in con.execute(CONSENTED + ' AND at.id IN (SELECT attachment_id FROM portfolio WHERE worker_id=?) ORDER BY at.created_at DESC LIMIT 12', (wid,))]
    return {'intro': p.get('intro', ''), 'since': p.get('since', ''), 'years': years, 'jobs': jobs, 'photos': photos}

def crew_summary(con, crew):
    """給客戶看的匿名經歷：「2 位服務人員・合計服務 35 場」。"""
    if not crew:
        return '服務人員'
    jobs = sum(experience(con, a['wid'])['jobs'] for a in crew)
    yrs = [experience(con, a['wid'])['years'] for a in crew]
    yrs = [y for y in yrs if y != '']
    s = f'{len(crew)} 位服務人員' if len(crew) > 1 else '專業服務人員'
    if jobs:
        s += f'・在本公司服務過 {jobs} 場'
    if yrs:
        s += f'・最資深 {max(yrs)} 年經驗' if len(crew) > 1 else f'・{yrs[0]} 年經驗'
    return s

def _sig(con, oid):
    import worker_portal
    return hmac.new(worker_portal._key(con).encode(), ('pf:' + oid).encode(), hashlib.sha256).hexdigest()[:20]

def portfolio_url(con, oid):
    import line_bot
    return f'{line_bot.BASE_URL}/pf?o={oid}&s={_sig(con, oid)}'

def portfolio_view(con, oid, sig):
    """客戶看的作品集頁（匿名）：只有夥伴代號、介紹、年資、服務場數、他們自己挑的公開照片。"""
    import worker_portal, line_bot
    if not oid or not hmac.compare_digest(_sig(con, oid), str(sig or '')):
        raise PermissionError('連結無效')
    o, s, cu, crew, area = line_bot.order_ctx(con, oid)
    out = []
    for i, a in enumerate(crew):
        e = experience(con, a['wid'])
        out.append({'code': f'夥伴 {chr(65 + i)}', 'intro': e['intro'], 'years': e['years'], 'jobs': e['jobs'],
                    'photos': [worker_portal.photo_url(con, x) for x in e['photos']]})
    import brands
    return {'brand': brands.name(line_bot.brand_of(cu)), 'date': o['date'], 'crew': out}

def my_portfolio(con, wid):
    """夥伴主控板：自己的介紹＋可選的照片（自己有接的工單拍的服務前／完成照片）。"""
    import worker_portal
    ensure(con)
    e = experience(con, wid)
    picked = set(e['photos'])
    pics = []
    for r in con.execute("SELECT at.id,at.label,at.service_date FROM attachments at JOIN assignments a ON a.order_id=at.order_id AND a.worker_id=? AND a.status='accepted' "
                         "WHERE at.label IN ('before','after') AND at.order_id IN (SELECT order_id FROM testimonials WHERE consent='public') ORDER BY at.created_at DESC LIMIT 60", (wid,)):
        pics.append({'id': r[0], 'label': '服務前' if r[1] == 'before' else '完工', 'date': r[2] or '', 'url': worker_portal.photo_url(con, r[0]), 'public': r[0] in picked})
    return {'intro': e['intro'], 'since': e['since'], 'jobs': e['jobs'], 'photos': pics}

def save_portfolio(con, wid, p):
    ensure(con)
    intro = str(p.get('intro', ''))[:300]
    since = str(p.get('since', '')).strip()[:4]
    if since and not (since.isdigit() and 1970 <= int(since) <= _today().year):
        raise ValueError('入行年份請填西元年，例如 2018')
    con.execute('INSERT INTO worker_profiles(worker_id,intro,since,updated_at) VALUES(?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET intro=excluded.intro,since=excluded.since,updated_at=excluded.updated_at',
                (wid, intro, since, c.now()))
    if 'photos' in p:
        mine = {x['id'] for x in my_portfolio(con, wid)['photos']}
        want = [x for x in (p.get('photos') or []) if x in mine][:12]
        con.execute('DELETE FROM portfolio WHERE worker_id=?', (wid,))
        for x in want:
            con.execute('INSERT INTO portfolio(worker_id,attachment_id,created_at) VALUES(?,?,?)', (wid, x, c.now()))
    c.audit(con, 'portfolio', 'workers', wid, after={'intro': intro, 'since': since})
    return my_portfolio(con, wid)

# ───────────── 4. 自動完工 ─────────────
def auto_complete(con):
    import board, line_bot
    n = 0
    today = _today().isoformat()
    for o in c.rows(con, f"SELECT o.* FROM orders o WHERE {board.GO_ORDER_SQL} AND o.date<=? AND COALESCE(o.review_note,'')!=?", (today, DEMO)):
        crew = [x for x in c.rows(con, "SELECT a.id,a.worker_id FROM assignments a WHERE a.order_id=? AND a.status='accepted'", (o['id'],)) if board.goes(o, x['worker_id'])]
        if not crew:
            continue
        ts = [con.execute('SELECT start,end,pause FROM timesheets WHERE assignment_id=?', (a['id'],)).fetchone() for a in crew]
        if all(ts):                       # 全員離場打卡 → 以打卡時間完工登記
            c.CTX.actor = '系統（夥伴已離場打卡）'
            con.execute('SAVEPOINT ac')
            try:
                board.quick_complete(con, {'id': o['id'], 'actual_start': min(t['start'] for t in ts), 'actual_end': max(t['end'] for t in ts),
                                           'break_minutes': max((t['pause'] or 0) for t in ts), 'report': o['field_report'] or '依夥伴打卡完成'})
                con.execute('RELEASE ac'); n += 1
                if o['status'] != 'confirmed':        # 每日檢查：補人中、沒補到人 → 照實際到場的人完工；照管理群決定的規則（decisions.short_crew）
                    import decisions; decisions.short_crew(con, o, len(crew))
            except ValueError as e:
                con.execute('ROLLBACK TO ac'); con.execute('RELEASE ac')
                if not ix.setting(con, f"ac_fail:{o['id']}"):         # 稽核 M7：以前每分鐘重試、沒人知道（例：補的人還沒審核、沒去）
                    ix.set_setting(con, f"ac_fail:{o['id']}", c.now())
                    line_bot.admin_notify(con, '⚠️ 夥伴都離場了，但系統沒辦法自動完工', [('原因', str(e)[:80]), ('處理', '請到中控台這張工單按「完工登記」（沒去的人先移除）')],
                                          '', '#C62828', oid=o['id'], tag='自動完工失敗，請處理')
            continue
        nowhm = dt.datetime.now(c.TZ).strftime('%H:%M')
        if (o['date'] < today and nowhm >= '09:00') or (o['date'] == today and o['end'] and nowhm >= _hm_plus(o['end'], 30)):   # 服務結束 30 分還沒打卡（隔天早上再問一次的舊規則照舊）
            for a in crew:
                n += ask_done(con, o, a['id'], a['worker_id'])
    return n

def _hm_plus(t, m):
    v = min(int(t[:2]) * 60 + int(t[3:5]) + m, 23 * 60 + 59)
    return f'{v // 60:02d}:{v % 60:02d}'

def ask_done(con, o, aid, wid):
    """問夥伴：這筆做完了嗎？（沒打卡）照排班時間完成／時間不一樣就打開工作頁補登實際時間。每筆每人只問一次。"""
    import line_bot
    if con.execute('SELECT 1 FROM timesheets WHERE assignment_id=?', (aid,)).fetchone():
        return 0
    if con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!=''", (aid,)).fetchone() and o['date'] == _today().isoformat():
        return 0                           # 今天有到場、還沒離場 → 可能還在做，不問
    asked = [r[0] for r in con.execute("SELECT created_at FROM line_outbox WHERE order_id=? AND kind IN ('done_ask','relay_w:done_ask') AND payload LIKE ? ORDER BY id", (o['id'], f"%w={wid}%"))]
    if asked:                          # 負責人 不要叫我們提醒 → 每天早上再問一次，問 3 次沒回才請派單管理群打電話
        if asked[-1] > (dt.datetime.now(c.TZ) - dt.timedelta(hours=20)).isoformat():
            return 0                       # 稽核：第 3 次問完也要給他一天回（以前 1 分鐘後就請管理群打電話）
        if len(asked) >= 3:
            return _done_escalate(con, o, wid)
    came = con.execute("SELECT arrived_at FROM checkins WHERE assignment_id=? AND arrived_at!=''", (aid,)).fetchone()
    m = line_bot.flex(f'{line_bot.md(o["date"])} {o["start"]} 那場做完了嗎？', line_bot._bubble(line_bot._header('完工登記', line_bot.when(dict(o)), '#C47F00'),
        [line_bot._t((f"這筆有到場紀錄（{came[0][11:16]}），還沒離場打卡。" if came else '這筆沒有打卡紀錄。') +
                     '照排班時間做完的話，按第一顆就完成登記；時間不一樣（例如早上就做完），按「我要補登」填實際時間，人不在現場也可以。', wrap=True)],
        [line_bot._btn(f"照排班時間完成（{o['start']}–{o['end']}）", data=f"a=wdone&o={o['id']}&w={wid}", primary=True),
         line_bot._btn('時間不一樣，我要補登', uri=line_bot._work_link(con, o['id'], line_bot.order_ctx(con, o['id'])[3], wid))],
        '工時是月底結算的依據，請照實際時間登記 🙏'))
    line_bot.to_worker(con, wid, 'done_ask', m[0]['altText'], m, o['id'])      # 發不到 → 管理群代問、代按
    return 1

def _done_escalate(con, o, wid):
    import line_bot as lb
    g = lb.admin_group(con)
    if not g or con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='done_esc' AND payload LIKE ?", (o['id'], f"%w={wid}%")).fetchone():
        return 0
    w = c.get(con, 'workers', wid)
    m = lb.flex(f"📞 {w['name']} 問了 3 次還沒登記完工", lb._bubble(lb._header('📞 請打電話問完工時間', f"{w['name']}・{lb.md(o['date'])}", '#C62828'),
        [lb._row(k, v) for k, v in lb.order_people(con, o['id'])] + [lb._row('排班', f"{o['start']}–{o['end']}"),
         lb._t('系統問了 3 次都沒回。打電話問實際幾點做完：照排班時間就按下面；時間不一樣請到中控台「完工登記」填實際時間。', color=lb.SUB, size='xs', wrap=True, margin='md')],
        [lb._btn(f"照排班時間完成（{o['start']}–{o['end']}）", data=f"a=wdone&o={o['id']}&w={wid}", primary=True), lb._btn('打開工單', uri=f"{lb.BASE_URL}/#o/{o['id']}")]))
    lb.queue(con, lb.WORK_ACCOUNT, g, 'done_esc', m[0]['altText'], [lb.ping(con, f"{w['name']} 問了 3 次還沒登記完工，請打電話問 👇")] + m, o['id'])
    return 1

def accept_chase(con, anytime=False):
    """業主收到完成照片還沒按「確認完工」：隔天自動提醒一次；店長定的天數（auto_accept_days）到了視為驗收（沒定就只提醒）。"""
    import line_bot as lb, board
    if not anytime and dt.datetime.now(c.TZ).strftime('%H:%M') < '10:00':
        return 0
    n = 0; now = dt.datetime.now(c.TZ)
    days = int(ix.setting(con, 'auto_accept_days') or 3)          # 負責人 全部自動化 → 店長沒定之前預設 3 天
    for o in c.rows(con, "SELECT o.*, (SELECT MIN(x.created_at) FROM line_outbox x WHERE x.order_id=o.id AND x.kind='finish_c' AND x.status='sent') sent_at FROM orders o "
                         "WHERE o.status='completed' AND COALESCE(o.acceptance,'') IN ('','pending') AND o.date>=? AND COALESCE(o.review_note,'')!=?", ((now.date() - dt.timedelta(days=40)).isoformat(), DEMO)):
        since = dt.datetime.fromisoformat(o['sent_at']) if o['sent_at'] else None
        old = o['date'] <= (now.date() - dt.timedelta(days=days)).isoformat()
        no_photo = not con.execute("SELECT 1 FROM attachments WHERE order_id=? AND label='after'", (o['id'],)).fetchone()
        unreach = not since and not lb.customer_target(con, o['customer_id'])[1]
        if days and ((since and now - since >= dt.timedelta(days=days)) or (not since and old and (no_photo or unreach))):
            why = (f"完成照片傳給業主 {days} 天沒回覆" if since else f"完工 {days} 天、沒有完成照片可給業主確認" if no_photo
                   else f"完工 {days} 天、業主沒對上 LINE（照片傳不到）、也沒反映")
            con.execute("UPDATE orders SET acceptance='accepted',acceptance_note=? WHERE id=?", (f"{why}，" + ('依店長定的規則' if ix.setting(con, 'auto_accept_days') else '依自動化預設（店長可改天數）') + f"視為驗收（{c.now()[:16]}）", o['id']))
            board.log(con, o['id'], 'auto_accept', f'{days} 天'); n += 1
            continue
        if since and now - since >= dt.timedelta(hours=24) and not con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='accept_remind'", (o['id'],)).fetchone():
            acc, uid = lb.customer_target(con, o['customer_id'])
            if uid:
                m = lb.flex('上次的服務還沒幫我們確認喔 🙏', lb._bubble(lb._header('完工確認', lb.when(dict(o)), brand=acc),
                    [lb._t('上次的服務前後照片已經傳給您了，方便的時候幫我們按一下「確認完工」；有地方需要加強也請告訴我們 🙏', wrap=True)],
                    [lb._btn('確認完工 ✅', data=f"a=acc_ok&o={o['id']}", primary=True), lb._btn('有地方需要加強', data=f"a=acc_fix&o={o['id']}")]))
                lb.queue(con, acc, uid, 'accept_remind', m[0]['altText'], m, o['id']); n += 1
    return n

def ask_done_earlier(con, wid, o_now):
    """夥伴在下一場到場打卡 → 同一天更早的場次還沒打卡／沒工時，馬上問他補登（不用等到結束後 30 分）。"""
    n = 0
    for r in c.rows(con, "SELECT a.id aid,o.* FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.date=? "
                         "AND o.id!=? AND o.status='confirmed' AND o.start<?", (wid, o_now['date'], o_now['id'], o_now['start'])):
        if not con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!=''", (r['aid'],)).fetchone():
            n += ask_done(con, r, r['aid'], wid)
    return n

def worker_done(con, oid, wid, who):
    """夥伴按「照排班時間完成」：補一筆排班時間的工時；全員都有工時 → 自動完工登記。"""
    import workflow, board
    a = con.execute("SELECT id FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone()
    o = c.get(con, 'orders', oid)
    if not a or not board.goes(o, wid):
        return '這筆已經登記過了，謝謝你 🙏'
    if not con.execute('SELECT 1 FROM timesheets WHERE assignment_id=?', (a[0],)).fetchone():
        c.CTX.actor = who + '（LINE 照排班完成）'
        came = con.execute("SELECT arrived_at FROM checkins WHERE assignment_id=? AND arrived_at!=''", (a[0],)).fetchone()
        st = came[0][11:16] if came and came[0][:10] == o['date'] and came[0][11:16] < o['end'] else o['start']      # 有到場打卡：從實際到場算
        workflow.command(con, 'timesheet', {'assignment_id': a[0], 'start': st, 'end': o['end'], 'pause': 0,
                                            'notes': '夥伴在 LINE 按「照排班時間完成」' + (f'（到場 {st}）' if st != o['start'] else '')})
        board.log(con, oid, 'leave', who + '（照排班時間）')
    auto_complete(con)
    import line_bot
    return (f"已登記 {line_bot.md(o['date'])} {o['start']}–{o['end']} 完成，辛苦了 🙏\n"
            "（實際時間不一樣的話，直接在這裡打字告訴我們幾點到幾點，派單同事會幫你改，不用再按「我要補登」）")

# ───────────── 5. 完工關心 ─────────────
def care(con):
    import line_bot, brands, worker_portal
    if dt.datetime.now(c.TZ).strftime('%H:%M') < '10:00':
        return 0
    t = _today()
    n = 0
    for o in c.rows(con, "SELECT * FROM orders WHERE status IN ('completed','archived') AND (followed_up_at IS NULL OR followed_up_at='') AND date BETWEEN ? AND ?",
                    ((t - dt.timedelta(days=7)).isoformat(), (t - dt.timedelta(days=CARE_AFTER)).isoformat())):
        if o['acceptance'] == 'disputed' or (o['rating'] and o['rating'] <= 3) or o['review_note'] == DEMO:
            con.execute("UPDATE orders SET followed_up_at=? WHERE id=?", (c.now(), o['id']))      # 有客訴／低分：由管理層親自關心（客戶回饋待處理），不發制式關心卡
            continue
        acc, uid = line_bot.customer_target(con, o['customer_id'])
        if not uid:
            continue
        recent = con.execute("SELECT 1 FROM line_outbox WHERE to_id=? AND kind='care' AND created_at>=?", (uid, (t - dt.timedelta(days=CARE_GAP)).isoformat())).fetchone()
        if not recent:
            bn = brands.name(acc)
            book = f"{line_bot.BASE_URL}/book?b={acc}&t={worker_portal.cust_token(con, acc, uid, o['customer_id'])}"
            m = line_bot.flex(f'{bn}關心您：{line_bot.md(o["date"])} 的服務還滿意嗎？', line_bot._bubble(line_bot._header(f'{bn} 關心您 😊', f"{line_bot.md(o['date'])} 的服務", brand=acc),
                [line_bot._t('想關心一下上次的服務您還滿意嗎？有任何地方需要加強，按下面告訴我們，我們會持續調整。', wrap=True)],
                [line_bot._btn('很滿意 😊', data=f"a=care&o={o['id']}&v=good", primary=True), line_bot._btn('有地方想建議', data=f"a=care&o={o['id']}&v=fix"),
                 line_bot._btn('再預約一次', uri=book)]))
            line_bot.queue(con, acc, uid, 'care', m[0]['altText'], m, o['id']); n += 1
        con.execute("UPDATE orders SET followed_up_at=? WHERE id=?", (c.now(), o['id']))
    return n

def quota_watch(con):
    """每天早上一次：三個官方帳號本月推播用量 ≥ 80% 就提醒（企業線／生活線是 200 則的免費方案）。"""
    import line_bot, brands
    if dt.datetime.now(c.TZ).strftime('%H:%M') < '09:00' or ix.setting(con, 'quota_watch_day') == c.now()[:10]:
        return
    ix.set_setting(con, 'quota_watch_day', c.now()[:10])
    for acc in ('demoshop', 'bizline', 'homeline'):
        if not ix.secret(con, 'line_token_' + acc):
            continue
        q = line_bot.quota(con, acc)
        if q.get('type') == 'limited' and q.get('limit') and (q.get('used') or 0) >= q['limit'] * 0.8:
            line_bot.admin_notify(con, f"⚠️ {brands.name(acc)} LINE 額度快用完", [('本月', f"已用 {q['used']}／{q['limit']} 則"),
                                  ('影響', '用完後推播（派工、提醒、關心）都送不出去'), ('處理', '到官方帳號後台升級方案，或請店長決定')], '/#line', '#C47F00')

def triage_auto(con):
    """規則已經決定的待整理直接結案：系統應徵／報到的夥伴＝可派工（負責人 「系統有來的都可以」）。"""
    for t in c.rows(con, "SELECT t.id FROM triage t JOIN workers w ON w.id=t.ref_id WHERE t.status='open' AND t.entity='workers' "
                         "AND t.grp IN ('5. 新應徵者','4. 新報到的夥伴') AND w.status='active'"):
        con.execute("UPDATE triage SET status='done',decided_at=?,decided_by='系統規則',decision='系統應徵自動可派工' WHERE id=?", (c.now(), t['id']))

def triage_digest(con):
    """每天 10:00 後：待整理有新的（或滿 3 天沒提醒）→ LINE 傳一張卡給店長，點了逐筆決定。"""
    import line_bot, reviews
    if dt.datetime.now(c.TZ).strftime('%H:%M') < '10:00' or ix.setting(con, 'triage_digest_day') == c.now()[:10]:
        return
    items = line_bot.triage_items(con)
    dups = line_bot.worker_dup_pairs(con)
    utc = lambda d=0: (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=d)).strftime('%Y-%m-%d %H:%M:%S')     # triage.created_at 是 UTC
    last = ix.setting(con, 'triage_digest_at') or ''
    if not (items or dups) or (not any((t['created_at'] or '') > last for t in items) and last > utc(3)):
        return
    ix.set_setting(con, 'triage_digest_day', c.now()[:10]); ix.set_setting(con, 'triage_digest_at', utc())
    groups = {}
    for t in items:
        groups[t['grp']] = groups.get(t['grp'], 0) + 1
    m = line_bot.flex(f'待整理 {len(items)} 筆，請妳決定', line_bot._bubble(line_bot._header('待整理・交給妳決定', f'{len(items)} 筆', '#C47F00'),
        [line_bot._t(f'{g}：{n} 筆', size='sm', wrap=True) for g, n in sorted(groups.items())][:12],
        [line_bot._btn('開始逐筆點選', data='a=m_tri', primary=True)] + ([line_bot._btn(f'夥伴可能重複 {len(dups)} 組', data='a=m_dups')] if dups else []),
        '系統拿不準的資料。每筆可以保留、移到正確分類或刪除（回收桶可還原）。'))
    line_bot.to_managers(con, 'admin_triage', m[0]['altText'], m)          # 送管理群 @店長

def chase(con):
    """每天 10:00 後一次：首頁紅字的待辦自動追。
    - 等客戶確認：客戶有綁 LINE → 確認卡已自動傳（人到齊、前一天各一次）；沒綁 → 請管理群打電話。
    - 等業主驗收：完成照片已傳業主 2 天還沒按 → 再傳一次；照片還沒審核／業主沒綁 LINE → 請管理群處理。"""
    import line_bot
    if dt.datetime.now(c.TZ).strftime('%H:%M') < '10:00' or ix.setting(con, 'chase_day') == c.now()[:10]:
        return 0
    ix.set_setting(con, 'chase_day', c.now()[:10])
    t = _today(); todo = []
    for o in c.rows(con, "SELECT * FROM orders WHERE status IN ('review','draft') AND COALESCE(customer_confirmed,0)=0 AND date BETWEEN ? AND ? AND COALESCE(review_note,'')!='示範工單'",
                    (t.isoformat(), (t + dt.timedelta(days=7)).isoformat())):
        crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        s = dict(con.execute('SELECT entry_mode,entry_ok FROM sites WHERE id=?', (o['site_id'],)).fetchone() or {}) if o['site_id'] else {}
        import pairing
        no_line = not line_bot.customer_target(con, o['customer_id'])[1] and (bool(ix.setting(con, f"pcf_none:{o['customer_id']}")) or not pairing.push_on(con))
        # ↑ 只剩店長確認「沒加官方 LINE」的才要打電話；配對不推播後（沒有配對卡可以按）→ 沒綁的一律列進打電話清單（稽核 C1）
        if o['date'] <= (t + dt.timedelta(days=2)).isoformat() and not s.get('entry_mode') and not o.get('entry_mode') and no_line:
            todo.append(f"🚪 開門方式｜{line_bot.md(o['date'])} {o['start']} {o['title']}（業主沒加官方 LINE，請打電話問怎麼進門）")
        if crew >= (o['required_workers'] or 1) and o.get('crew_ok') and no_line:
            todo.append(f"📞 客戶確認｜{line_bot.md(o['date'])} {o['start']} {o['title']}（業主沒加官方 LINE，請打電話）")
    for o in c.rows(con, "SELECT * FROM orders WHERE status='completed' AND COALESCE(acceptance,'pending')='pending' AND date BETWEEN ? AND ?",
                    ((t - dt.timedelta(days=14)).isoformat(), (t - dt.timedelta(days=1)).isoformat())):
        sent = con.execute("SELECT MIN(created_at) FROM line_outbox WHERE order_id=? AND kind='finish_c' AND status='sent'", (o['id'],)).fetchone()[0]
        if sent:
            if sent[:10] <= (t - dt.timedelta(days=2)).isoformat() and not con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='accept_remind'", (o['id'],)).fetchone():
                acc, uid = line_bot.customer_target(con, o['customer_id'])
                urls = line_bot._finish_urls(con, o['id'])
                if uid and urls:
                    m = line_bot.card_finish_customer(con, o['id'], urls)
                    line_bot.queue(con, acc, uid, 'accept_remind', '再請您確認一下服務成果 🙏', m, o['id'])
            continue
        continue                         # 負責人 驗收全部自動（照片 2 小時沒人審自動通過、3 天沒回／傳不到視為驗收），不再列人工追
    try:
        todo += json.loads(ix.setting(con, 'followup_todo') or '[]')        # followup.py：報價過期沒簽、照片沒傳齊
    except ValueError:
        pass
    if todo:
        line_bot.admin_notify(con, f'📋 今天要人工追的 {len(todo)} 件', [('・', x) for x in todo[:15]], '/#asks', '#C47F00')      # 按鈕直接到「管理群待處理」
    return len(todo)

# ───────────── 現場提醒（負責人 到現場不打卡、業主不知道要確認，都要自動提醒） ─────────────
def _sent(con, oid, kind, wid=''):
    """發過沒（夥伴沒綁時轉到管理群的 relay_w:<kind> 也算，稽核：以前每分鐘重發到管理群）。"""
    return con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind IN (?,?) AND (?='' OR payload LIKE ?)", (oid, kind, 'relay_w:' + kind, wid, f'%{wid}%')).fetchone()

def onsite(con):
    """每分鐘：
    ① 開工前 1 小時：提醒夥伴（導航、到場打卡）
    ② 開工後 5 分鐘還沒到場打卡：提醒夥伴打卡，可按「會晚到」（同時告訴業主）；15 分鐘還沒到 → 管理群（alerts.py）
    ③ 預定結束後 20 分鐘還沒離場打卡：提醒夥伴拍完成照片、離場打卡
    ④ 服務前 2 天業主還沒確認時間：再請業主確認一次（前一天還會再一次；24 小時內還沒確認 → 管理群打電話）"""
    import line_bot as lb, worker_portal
    now = dt.datetime.now(c.TZ); today = now.date().isoformat(); n = 0
    import board
    for o in c.rows(con, f"SELECT o.* FROM orders o WHERE {board.GO_ORDER_SQL} AND o.date=? AND o.start!='' AND o.end!='' AND COALESCE(o.review_note,'')!=?", (today, DEMO)):
        start = dt.datetime.fromisoformat(f"{o['date']}T{o['start']}:00").replace(tzinfo=c.TZ)
        end = dt.datetime.fromisoformat(f"{o['date']}T{o['end']}:00").replace(tzinfo=c.TZ)
        for a in c.rows(con, "SELECT * FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)):
            if not board.goes(o, a['worker_id']):
                continue                   # 補人中新加進來、還沒審核的人：還沒給地址，不提醒出門
            ck = con.execute('SELECT arrived_at,left_at FROM checkins WHERE assignment_id=?', (a['id'],)).fetchone()
            arrived, left = bool(ck and ck[0]), bool(ck and ck[1])
            url = worker_portal.work_url(con, a['id'])
            mark = f"w={a['worker_id']}"; key = url.rsplit('/', 1)[-1]          # 工作頁連結每人每場不同 → 用來判斷提醒發過沒
            if not arrived and dt.timedelta(minutes=45) < start - now <= dt.timedelta(minutes=60) and not _sent(con, o['id'], 'prestart', key):
                m = lb.flex(f"1 小時後開工：{o['start']}", lb._bubble(lb._header('1 小時後開工 🧹', lb.when(dict(o))),
                    [lb._t('到了先打開工作頁按「到場打卡」，拍服務前照片。路上有狀況請按下面告訴我們。', wrap=True)],
                    [lb._btn('打開工作頁（導航・到場打卡）', uri=url, primary=True), lb._btn('已出門 🚗', data=f"a=weta&o={o['id']}&v=out&{mark}"),
                     lb._btn('會晚到', data=f"a=wlate&o={o['id']}&m=15&{mark}"), lb._btn('臨時無法前往', data=f"a=lv&o={o['id']}")]))
                lb.to_worker(con, a['worker_id'], 'prestart', m[0]['altText'], m, o['id']); n += 1
            told = con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action IN ('worker_eta','worker_late') AND (detail LIKE ? OR detail LIKE ?)",
                               (o['id'], a_name(con, a['worker_id']) + '|%', a_name(con, a['worker_id']) + ' 約晚%')).fetchone()
            if not arrived and not told and dt.timedelta(minutes=15) < start - now <= dt.timedelta(minutes=30) and not _sent(con, o['id'], 'eta_ask', key):
                m = lb.flex(f"出門了嗎？{o['start']} 開工", lb._bubble(lb._header('出門了嗎？🚗', lb.when(dict(o)), '#0A84FF'),
                    [lb._t('再 30 分鐘開工，點一下讓我們知道你在哪（業主在家開門的話，按「快到了」會先通知業主準備開門）。', wrap=True)],
                    [lb._btn('已出門 🚗', data=f"a=weta&o={o['id']}&v=out&{mark}", primary=True), lb._btn('快到了（10 分鐘內）', data=f"a=weta&o={o['id']}&v=near&{mark}"),
                     lb._btn('會晚到 15 分', data=f"a=wlate&o={o['id']}&m=15&{mark}"), lb._btn('會晚到 30 分', data=f"a=wlate&o={o['id']}&m=30&{mark}"),
                     lb._btn('打開工作頁（導航）', uri=url)]))
                lb.to_worker(con, a['worker_id'], 'eta_ask', m[0]['altText'], m, o['id']); n += 1
            if not arrived and dt.timedelta(minutes=5) <= now - start <= dt.timedelta(minutes=60) and not _sent(con, o['id'], 'arrive_nudge', key):
                import door
                em = door.mode_of(dict(o), dict(con.execute('SELECT entry_mode FROM sites WHERE id=?', (o['site_id'] or '',)).fetchone() or {}))
                m = lb.flex('到了嗎？記得打到場卡', lb._bubble(lb._header('到了嗎？📍', lb.when(dict(o)), '#C47F00'),
                    [lb._t('已經開工 5 分鐘，系統還沒收到你的到場打卡。到了請按下面打卡；還在路上請按「會晚到」，我們會告訴業主。', wrap=True)],
                    [lb._btn('到場打卡', uri=url, primary=True)] + ([] if em in door.NO_DOOR else [lb._btn('到門口了，請業主開門 🚪', data=f"a=door_req&o={o['id']}")]) + [lb._btn('會晚到 15 分', data=f"a=wlate&o={o['id']}&m=15&{mark}"),
                     lb._btn('會晚到 30 分', data=f"a=wlate&o={o['id']}&m=30&{mark}")]))
                lb.to_worker(con, a['worker_id'], 'arrive_nudge', m[0]['altText'], m, o['id']); n += 1
            if arrived and not left and not con.execute("SELECT 1 FROM attachments WHERE order_id=? AND label='before'", (o['id'],)).fetchone() \
                    and not con.execute('SELECT 1 FROM sites WHERE id=? AND COALESCE(no_photo,0)=1', (o['site_id'] or '',)).fetchone() and not ix.setting(con, f"nophoto_job:{o['id']}") \
                    and not ix.setting(con, f"forgot_before:{o['id']}"):      # 不能拍照的案場不催；稽核 M4：已寫「忘了拍」原因的也不催
                try:
                    since = now - dt.datetime.fromisoformat(ck[0])
                except (TypeError, ValueError):
                    since = dt.timedelta(0)
                if since >= dt.timedelta(minutes=10) and not _sent(con, o['id'], 'before_nudge', key):       # 負責人 服務前照片要請夥伴拍或上傳
                    m = lb.flex('記得拍服務前照片 📷', lb._bubble(lb._header('記得拍服務前照片 📷', lb.when(dict(o)), '#C47F00'),
                        [lb._t('開始清之前，先拍還沒清的樣子（每個區域一張）。可以先存在手機，完工時跟完成照片一起上傳（先傳服務前、再傳完工）。', wrap=True),      # 前後照可以完工時一起交
                         lb._t('離場打卡前要傳齊服務前、完成照片；有服務前、完成照片才會發放薪資。', wrap=True, color=lb.SUB, size='sm', margin='md')],
                        [lb._btn('打開工作頁・上傳照片', uri=url, primary=True)]))
                    lb.to_worker(con, a['worker_id'], 'before_nudge', m[0]['altText'], m, o['id']); n += 1
                elif False and since >= dt.timedelta(minutes=60) and not ix.setting(con, f"before_esc:{a['id']}") and (con.execute(
                        "SELECT MIN(created_at) FROM line_outbox WHERE order_id=? AND kind IN ('before_nudge','relay_w:before_nudge') AND payload LIKE ?", (o['id'], f'%{key}%')).fetchone()[0] or c.now()) <= (now - dt.timedelta(minutes=30)).isoformat():     # 提醒過 30 分鐘還沒有才找管理群
                    ix.set_setting(con, f"before_esc:{a['id']}", c.now())
                    w_ = con.execute('SELECT name,phone FROM workers WHERE id=?', (a['worker_id'],)).fetchone()
                    lb.admin_notify(con, f"📷 {w_[0] if w_ else '夥伴'} 到場 1 小時還沒有服務前照片", [('已提醒', '系統 10 分鐘時已請他拍照上傳'), ('電話', (w_[1] if w_ else '') or '—'),
                                    ('處理', '請打電話提醒他上傳；已經清完、補不了的，請在中控台幫他完工登記')], '/#home', '#C47F00', oid=o['id'])
                    n += 1
            if arrived and not left and dt.timedelta(minutes=20) <= now - end <= dt.timedelta(hours=4) and not _sent(con, o['id'], 'leave_nudge', key):
                m = lb.flex('做完了嗎？記得拍完成照片、離場打卡', lb._bubble(lb._header('做完了嗎？✨', lb.when(dict(o)), '#2E7D32'),
                    [lb._t('預定時間已過 20 分鐘。做完請拍完成照片、寫一句回報，再按離場打卡（工時以打卡為準）；還沒做完不用理這則。', wrap=True)],
                    [lb._btn('拍完成照片・離場打卡', uri=url, primary=True)]))
                lb.to_worker(con, a['worker_id'], 'leave_nudge', m[0]['altText'], m, o['id']); n += 1
    d2 = (now.date() + dt.timedelta(days=2)).isoformat()
    for o in c.rows(con, "SELECT * FROM orders WHERE status IN ('review','draft') AND date=? AND COALESCE(customer_confirmed,0)=0 AND COALESCE(crew_ok,'')!=''", (d2,)):
        crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        if crew < (o['required_workers'] or 1) or _sent(con, o['id'], 'confirm_ask2') or now.strftime('%H:%M') < '10:00' or con.execute(
                "SELECT 1 FROM line_outbox WHERE order_id=? AND kind IN ('assigned','confirm_ask') AND created_at>?", (o['id'], (now - dt.timedelta(hours=3)).isoformat(timespec='seconds'))).fetchone():
            continue                       # 稽核：剛傳過承接卡／確認卡就不要再傳一張一樣的
        acc, uid = lb.customer_target(con, o['customer_id'])
        if uid:
            m = lb.card_assigned(con, o['id'])
            lb.queue(con, acc, uid, 'confirm_ask2', '後天的服務，請幫我們按「確認這個時間」🙏', [{'type': 'text', 'text': '您好 😊 後天的服務人員已經安排好了，麻煩按下面的「確認這個時間 ✅」，我們才能為您正式排定喔！'}] + m, o['id']); n += 1
    return n

def a_name(con, wid):
    r = con.execute('SELECT name FROM workers WHERE id=?', (wid,)).fetchone()
    return r[0] if r else ''

def eta_tap(con, oid, wid, v, who):
    """夥伴按「已出門」／「快到了」：記下來（首頁即時狀況看得到）；快到了＋業主在家開門 → 先通知業主準備開門。"""
    import line_bot as lb, board, door
    o, s = door.site_of(con, oid)
    if v not in ('out', 'near'):
        return '收到 🙏'
    if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='worker_eta' AND detail=?", (oid, f'{who}|{v}')).fetchone():
        return '收到了，路上小心 🙏'
    board.log(con, oid, 'worker_eta', f'{who}|{v}')
    if v == 'near' and door.mode_of(o, s) in ('home', '') and not o['door_open'] and not con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='near_c'", (oid,)).fetchone():
        acc, uid = lb.customer_target(con, o['customer_id'])
        if uid:                      # 每場只通知業主一次（兩位夥伴都按「快到了」也只一則）
            lb.queue(con, acc, uid, 'near_c', '服務人員快到了', lb._txt(f"您好 😊 今天 {o['start']} 的服務人員快到了（約 10 分鐘），麻煩準備幫忙開門，謝謝您！"), oid)
            return '收到 👍 已先通知業主準備開門，到了記得打到場卡。'
    return '收到 👍 路上小心，到了記得打到場卡。' if v == 'out' else '收到 👍 到了記得打到場卡。'

def late_tap(con, oid, wid, mins, who):
    """夥伴按「會晚到」：告訴業主＋管理群。"""
    import line_bot as lb, board
    o = c.get(con, 'orders', oid)
    board.log(con, oid, 'worker_late', f'{who} 約晚 {mins} 分')
    acc, uid = lb.customer_target(con, o['customer_id'])
    if uid and con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='late_c' AND created_at>?", (oid, (dt.datetime.now(c.TZ) - dt.timedelta(minutes=30)).isoformat(timespec='seconds'))).fetchone():
        uid = None; told = True             # 稽核：兩人同場都按「會晚到」→ 業主 30 分鐘內只收一次
    else:
        told = False
    if uid:
        lb.queue(con, acc, uid, 'late_c', '服務人員會晚一點到', lb._txt(f"您好，今天 {o['start']} 的服務人員路上耽擱，大約會晚 {mins} 分鐘到，很抱歉 🙏 到場時會再通知您。"), oid)
    lb.admin_notify(con, f'🕒 {who} 說會晚 {mins} 分鐘', [('工作', lb.when(dict(o))), ('業主', '已自動通知' if uid or told else '沒綁 LINE，請打電話告知')], '/#home', '#C47F00', oid=oid)
    return f'收到，已告訴業主大約晚 {mins} 分鐘，路上小心 🙏 到了記得打到場卡。'

# ───────────── 綁定待確認：管理群每天再提醒一次 ─────────────
def deliver_pending_finish(con):
    """業主之後才綁 LINE：14 天內審核過（photo_approved）但還沒傳到的完成照片，綁好就自動補傳（不用人記得重傳）。"""
    import line_bot as lb
    n = 0
    since = (_today() - dt.timedelta(days=14)).isoformat()
    for o in c.rows(con, "SELECT o.* FROM orders o WHERE o.status='completed' AND o.date>=? AND o.customer_id IS NOT NULL "      # 稽核 H1：免審核後，沒走工作頁離場的完工也要傳
                         "AND EXISTS(SELECT 1 FROM attachments a WHERE a.order_id=o.id AND a.label='after') "
                         "AND NOT EXISTS(SELECT 1 FROM dispatch_log d WHERE d.order_id=o.id AND d.action IN ('photo_approved','photo_redo')) "
                         "AND NOT EXISTS(SELECT 1 FROM line_outbox x WHERE x.order_id=o.id AND x.kind='finish_c' AND x.status NOT LIKE 'skipped%') "
                         "AND COALESCE(o.review_note,'')!=?", (max(since, '2026-10-13'), DEMO)):
        lb.on_finished(con, o, '系統（完工登記）', [], o.get('field_report') or ''); n += 1
    for o in c.rows(con, "SELECT o.* FROM orders o WHERE o.status='completed' AND o.date>=? AND o.customer_id IS NOT NULL "
                         "AND EXISTS(SELECT 1 FROM dispatch_log d WHERE d.order_id=o.id AND d.action='photo_approved') "
                         "AND NOT EXISTS(SELECT 1 FROM line_outbox x WHERE x.order_id=o.id AND x.kind='finish_c' AND x.status NOT LIKE 'skipped%')", (since,)):
        acc, uid = lb.customer_target(con, o['customer_id'])
        if not uid or (lb.mode(con, acc) != 'on' and uid not in lb.test_users(con)):
            continue                     # 這個品牌官方 LINE 還沒正式發送：等改成正式再補傳（以前每分鐘重排一次、管理群每分鐘收到通知）
        m = lb.card_finish_customer(con, o['id'])
        if o['date'] < _today().isoformat():
            m = [{'type': 'text', 'text': f"您好 😊 補傳 {lb.md(o['date'])} 的服務前後照片給您，請幫我們確認一下，謝謝！"}] + m
        lb.queue(con, acc, uid, 'finish_c', m[-1]['altText'], m, o['id']); n += 1
        lb.admin_notify(con, '📨 業主綁定後已自動補傳完成照片', [('工作', lb.when(dict(o)))], '', '#2E7D32', oid=o['id'])
    return n

def finish_chase(con):
    """每天 10:00 一則給派單管理群：①還沒綁 LINE、完成照片傳不過去的業主（按鈕拿綁定訊息轉傳）②滿意客戶還沒人親自回覆。"""
    import line_bot as lb, testimonials
    if dt.datetime.now(c.TZ).strftime('%H:%M') < '10:00' or ix.setting(con, 'finish_chase_day') == c.now()[:10]:
        return 0
    ix.set_setting(con, 'finish_chase_day', c.now()[:10])
    g = lb.admin_group(con)
    if not g:
        return 0
    since = (_today() - dt.timedelta(days=14)).isoformat()
    waiting = {}
    for o in c.rows(con, "SELECT o.* FROM orders o WHERE o.status='completed' AND o.date>=? AND o.customer_id IS NOT NULL "
                         "AND EXISTS(SELECT 1 FROM attachments a WHERE a.order_id=o.id AND a.label='after') "
                         "AND NOT EXISTS(SELECT 1 FROM line_outbox x WHERE x.order_id=o.id AND x.kind='finish_c' AND x.status NOT LIKE 'skipped%') ORDER BY o.date", (since,)):
        if not lb.customer_target(con, o['customer_id'])[1]:
            waiting.setdefault(o['customer_id'], []).append(o)
    import brands
    soon = {}
    for o in c.rows(con, "SELECT * FROM orders WHERE status IN ('review','draft','confirmed') AND customer_id IS NOT NULL AND date BETWEEN ? AND ? ORDER BY date,start",
                    (_today().isoformat(), (_today() + dt.timedelta(days=7)).isoformat())):
        if o['customer_id'] not in waiting and not lb.customer_target(con, o['customer_id'])[1]:
            soon.setdefault(o['customer_id'], []).append(o)
    wk = [w for w in c.rows(con, "SELECT * FROM workers WHERE status='active' AND name NOT LIKE '（示範）%' ORDER BY name") if not lb.worker_target(con, w['id'])[1] and not is_blacklisted(con, w['id'])]
    import pairing
    if not pairing.push_on(con):                     # 業主配對不推播（中控台「LINE 配對」看）
        waiting, soon = {}, {}
    staff_names = [pairing._norm(d) for (d,) in con.execute("SELECT display_name FROM line_users WHERE COALESCE(is_staff,0)=1 OR user_id IN (%s)" % (','.join('?' * len(lb.staff_users(con))) or "''"),
                                                         tuple(lb.staff_users(con))) if d]
    wk = [w for w in wk if not (len(pairing._norm(w['name'])) >= 2 and any(pairing._norm(w['name']) in d for d in staff_names))]      # 管理層自己不列
    # ↑ 夥伴還沒綁官方 LINE 的名單恢復（派工問不到他們），每天 10:00 一張
    # 「不用傳重複」「群發要全部一次發」：按過「已經傳給他了」的不列（傳過 14 天還沒綁才再列）；7 天內列過的不重列；要列的一次全部發
    wk, rest = worker_bind_due(con, wk) if ix.setting(con, 'standing_push') == 'on' else ([], 0)      # 常態的事 → 併進 10:30 那一則（中控台「夥伴 › LINE 還沒綁」處理）
    tms = testimonials.pending(con) if ix.setting(con, 'standing_push') == 'on' else []      # 併進 10:30 那一則
    if not waiting and not soon and not wk and not tms:
        return 0
    cards = worker_bind_msgs(con, wk)     # 負責人 業主改由店長對照「是哪個 LINE」（customer_pairing），這裡只報數字
    msg = []
    if waiting or soon:
        msg.append(f'📌 {len(waiting) + len(soon)} 位業主還沒對上 LINE，已請店長對照是哪個聊天室（對上就自動傳提醒、完成照片）。')
    if wk:
        msg.append(f'📌 {len(wk)} 位夥伴還沒綁官方 LINE（系統派工問不到他們）：' + '、'.join(w['name'] for w in wk)
                   + '。\n下面卡片（往左滑，全部都在）按「給我夥伴綁定訊息」轉傳給他，傳了按「已經傳給他了」就不會再列；沒處理的 7 天後再列一次。中控台「夥伴 › LINE 還沒綁」也可以直接處理'
                   + (f'（另外 {rest} 位已傳過或最近列過，在中控台看）。' if rest else '。'))
    if tms:
        cnt = {}
        for t in tms:
            cnt[t['customer'] or '—'] = cnt.get(t['customer'] or '—', 0) + 1           # 同一位只列一次
        msg.append(f"💬 {len(cnt)} 位滿意的客戶還沒人親自回覆謝謝：" + '、'.join(n + (f'（{k} 張）' if k > 1 else '') for n, k in list(cnt.items())[:8]) + '（回完在那張卡按「已親自回覆」）')
    m = [lb.ping(con, '\n'.join(msg))] + cards
    for i in range(0, len(m), 5):                                      # LINE 一次最多 5 則：卡片多就分幾次送（一次發完）
        lb.queue(con, lb.WORK_ACCOUNT, g, 'finish_chase', (msg[0] if i == 0 else f'夥伴還沒綁 LINE（續 {i // 5 + 1}）')[:300], m[i:i + 5])
    return len(waiting) + len(soon) + len(wk) + len(tms)

def unbound_workers(con):
    """可派工、還沒綁官方 LINE、沒停派的夥伴（管理層自己不算）。"""
    import line_bot as lb, pairing
    wk = [w for w in c.rows(con, "SELECT * FROM workers WHERE status='active' AND name NOT LIKE '（示範）%' ORDER BY name") if not lb.worker_target(con, w['id'])[1] and not is_blacklisted(con, w['id'])]
    staff_names = [pairing._norm(d) for (d,) in con.execute("SELECT display_name FROM line_users WHERE COALESCE(is_staff,0)=1 OR user_id IN (%s)" % (','.join('?' * len(lb.staff_users(con))) or "''"),
                                                         tuple(lb.staff_users(con))) if d]
    return [w for w in wk if not (len(pairing._norm(w['name'])) >= 2 and any(pairing._norm(w['name']) in d for d in staff_names))]

def worker_bind_due(con, wk):
    """要列的夥伴＝沒按過「已經傳給他了」（或傳過 14 天還沒綁）、7 天內沒列過；記下今天列過。回傳 (要列的, 其他還沒綁的人數)。"""
    import line_bot as lb
    d14, d7 = (_today() - dt.timedelta(days=14)).isoformat(), (_today() - dt.timedelta(days=7)).isoformat()
    due = [w for w in wk if ((lb.wbind_sent(con, w['id']) or {}).get('at') or '')[:10] <= d14 and (ix.setting(con, f"wbind_listed:{w['id']}") or '') <= d7]
    for w in due:
        ix.set_setting(con, f"wbind_listed:{w['id']}", _today().isoformat())
    return due, len(wk) - len(due)

def worker_bind_msgs(con, wk):
    """夥伴還沒綁 LINE 的卡片：每位一張（給我夥伴綁定訊息／已經傳給他了），10 張一組輪播。"""
    import line_bot as lb
    bs = []
    for w in wk:
        sent0 = lb.wbind_sent(con, w['id'])
        bs.append(lb._bubble(lb._header('夥伴還沒綁 LINE', w['name'], '#2E7D32'),
                  [lb._row('電話', w['phone'] or '—')] + ([lb._row('之前', f"{sent0['at'][5:10].replace('-', '/')} {sent0['by']} 傳過，還沒綁")] if sent0 else [])
                  + [lb._t('沒綁的夥伴收不到派工、提醒（現在是轉到這裡代問）。按「給我夥伴綁定訊息」轉傳給他，傳了按「已經傳給他了」就不會再列。', color=lb.SUB, size='xs', wrap=True, margin='md')],
                  [lb._btn('給我夥伴綁定訊息', data=f"a=wbind&w={w['id']}", primary=True), lb._btn('已經傳給他了 ✅', data=f"a=wsent&w={w['id']}")]))
    return [lb._carousel(f'夥伴還沒綁 LINE（{i // 10 + 1}）', bs[i:i + 10])[0] for i in range(0, len(bs), 10)]

def customer_pairing(con):
    """負責人 還沒對上 LINE 的業主、夥伴（最近有事要傳），和不知道是誰的 LINE 好友 → 每小時 @管理層三位請盡速配對（08:00–22:00；全部配好就停）。
    送派單管理群（誰按都可以）；沒有管理群才一對一送店長。settings pair_remind_minutes 可改間隔（預設 60）。"""
    import line_bot as lb, pairing
    now = dt.datetime.now(c.TZ)
    if not ('08:00' <= now.strftime('%H:%M') < '22:00') or not pairing.push_on(con):
        return 0                                   # 不要再推播，店長到中控台一個一個確認（也不自動配對）
    gap = float(ix.setting(con, 'pair_remind_minutes') or 60)
    last = ix.setting(con, 'cpair_at')
    if last and dt.datetime.fromisoformat(last) > now - dt.timedelta(minutes=gap - 1):
        return 0
    pairing.auto_pair(con)                         # 雙向證據夠強的先自動配對
    custs, wks, lines = pairing.customers_needing_line(con), pairing.workers_needing_line(con), pairing.todo(con)
    if not (custs or wks or lines):
        return 0
    ix.set_setting(con, 'cpair_at', now.isoformat(timespec='seconds'))
    bs = [pairing.customer_card(con, cid, why)[0]['contents'] for cid, why in custs[:4]] + [pairing.worker_card(con, wid, why)[0]['contents'] for wid, why in wks[:2]]
    shown = {x[0] for cid, _ in custs[:4] for x in pairing.customer_candidates(con, cid) if x[2] >= 60} | {x[0] for wid, _ in wks[:2] for x in pairing.worker_candidates(con, wid) if x[2] >= 60}
    bs += [pairing.card(con, u)[0]['contents'] for u in [u for u in lines if u['user_id'] not in shown][:max(0, min(2, 8 - len(bs)))]]      # 已經在上面猜到的 LINE 不重複出卡；一則最多 8 張
    parts = [f'{len(custs)} 位業主' if custs else '', f'{len(wks)} 位夥伴' if wks else '', f'{len(lines)} 個 LINE 好友不知道是誰' if lines else '']
    tip = ('🔗 還沒對上 LINE：' + '、'.join(p for p in parts if p) + '。\n請管理層三位盡速幫忙配對（點下面卡片，誰按都可以）；對上之後提醒、派工、完成照片都會自動傳給他。'
           '\n每小時提醒一次，全部配好就不再提醒。')
    g = lb.admin_group(con)
    for to in ([g] if g else lb.deciders(con)):
        lb.queue(con, lb.WORK_ACCOUNT, to, 'cust_pair', '還沒對上 LINE，請盡速配對', [lb.ping(con, tip, who=sorted(lb.staff_users(con))) if g else lb._txt(tip)[0]]
                 + lb._carousel('還沒對上 LINE，請盡速配對', bs))
    return len(custs) + len(wks) + len(lines)

def _estquiz(con):
    import estquiz
    return estquiz.tick(con)

def _leave_auto(con):
    import leave
    return leave.auto_handle(con)

def _door_auto(con):
    """開門：同業主「在家開門」自動沿用；明天以前還不知道怎麼進門的，馬上請管理群補（稽核 A1、A5）。"""
    import door
    return door.inherit_due(con) + door.fill_push(con)

def applicant_purge(con):
    """店長決定的保存期限（applicant_retention_days，預設 0＝不刪）：不錄取的應徵者，婉拒後滿 N 天刪良民證、作品集照片、應徵表內容（刪除不能復原，所以要她決定才開）。"""
    days = int(ix.setting(con, 'applicant_retention_days') or 0)
    if days <= 0 or ix.setting(con, 'purge_day') == c.now()[:10]:
        return 0
    ix.set_setting(con, 'purge_day', c.now()[:10])
    cut = (dt.datetime.now(c.TZ) - dt.timedelta(days=days)).isoformat()
    n = 0
    for iv in c.rows(con, "SELECT id,worker_id,decided_at FROM interviews WHERE status IN ('rejected','reject_pending') AND COALESCE(decided_at,'')!='' AND decided_at<? AND COALESCE(application,'')!='{}'", (cut,)):
        if con.execute("SELECT 1 FROM assignments WHERE worker_id=? AND status='accepted'", (iv['worker_id'],)).fetchone():
            continue                                  # 有做過工作的不是單純應徵者，不動
        if con.execute("SELECT 1 FROM interviews WHERE worker_id=? AND id!=? AND status NOT IN ('rejected','reject_pending') AND created_at>?", (iv['worker_id'], iv['id'], iv.get('decided_at') or '')).fetchone():
            continue                                  # 稽核 M6：又在應徵中（新的面試）→ 不刪他的資料
        con.execute("DELETE FROM worker_files WHERE worker_id=?", (iv['worker_id'],))
        con.execute("UPDATE interviews SET application='{}',police='{}' WHERE id=?", (iv['id'],))
        c.audit(con, 'applicant_purge', 'workers', iv['worker_id'], after={'days': days}); n += 1
    return n

def _campaign(con):
    import campaign
    return campaign.tick(con)

def _scope_chase(con):
    import scopereview
    return scopereview.chase(con)

def _materials_remind(con):
    import materials
    return materials.remind(con)

def photo_auto_approve(con):
    """決議：完成照片免審核，離場打卡就自動傳給業主（line_bot.on_finished）。
    這裡只收尾「改制前送出、還沒人按」的舊審核卡（2 天內）：直接自動傳給業主，不再 @管理層催。"""
    import line_bot as lb, worker_portal
    now = dt.datetime.now(c.TZ)
    n = 0
    for oid, at in con.execute("SELECT order_id,MAX(created_at) FROM line_outbox WHERE kind='photos' AND payload LIKE '%a=fin_ok%' AND COALESCE(order_id,'')!='' "
                               "GROUP BY order_id HAVING MAX(created_at)>?", ((now - dt.timedelta(days=2)).isoformat(timespec='seconds'),)).fetchall():
        o = con.execute('SELECT status FROM orders WHERE id=?', (oid,)).fetchone()
        if not o or o['status'] in ('cancelled', 'archived'):
            continue
        if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action IN ('photo_approved','photo_redo') AND at>=?", (oid, worker_portal._utc(at))).fetchone():
            continue
        if con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='finish_c' AND status NOT LIKE 'skipped%'", (oid,)).fetchone():
            continue
        sent, _ = lb.approve_finish(con, oid, '自動（免審核）')
        n += 1 if sent else 0
    return n

def house_month(con):
    """多館業主（例：客戶01 9／10／11 號館）每月要確定是哪一館 → 每月 20 號 10:00 起，把下個月的日期＋館別傳給業主確認；
    還沒排下個月的，請業主直接回覆哪幾天、哪一館（客戶留言超過 4 小時沒人回會提醒管理群）。業主沒對上 LINE → 交給每天 10:00「這位業主是哪個 LINE」配對。"""
    import line_bot as lb
    now = dt.datetime.now(c.TZ)
    if now.strftime('%H:%M') < '10:00':
        return 0
    if now.day < 20:                                  # 月初再問一次「這個月」還沒按「都正確」的（20 號沒按、或之後才排的）
        this = now.date().isoformat()[:7]
        if ix.setting(con, 'hm_month_start') == this:
            return 0
        ix.set_setting(con, 'hm_month_start', this); n = 0
        for (cid,) in con.execute("SELECT s.customer_id FROM sites s JOIN customers cu ON cu.id=s.customer_id WHERE cu.status='active' AND cu.name NOT LIKE '（示範）%' "
                                  "GROUP BY s.customer_id HAVING COUNT(*)>1").fetchall():
            if ix.setting(con, f'hm_ok:{cid}:{this}') or not con.execute(
                    "SELECT 1 FROM orders WHERE customer_id=? AND substr(date,1,7)=? AND date>=? AND status NOT IN ('cancelled','archived')", (cid, this, now.date().isoformat())).fetchone():
                continue
            acc, to = lb.customer_target(con, cid)
            if to:
                m = lb.card_house_month(con, cid, this)
                lb.queue(con, acc, to, 'house_month', m[0]['altText'], m); n += 1
        return n
    nxt = (now.date().replace(day=1) + dt.timedelta(days=32)).replace(day=1); key = nxt.isoformat()[:7]
    if ix.setting(con, 'hm_month') == key:
        return 0
    ix.set_setting(con, 'hm_month', key)
    this, n = now.date().isoformat()[:7], 0
    for (cid,) in con.execute("SELECT s.customer_id FROM sites s JOIN customers cu ON cu.id=s.customer_id WHERE cu.status='active' AND cu.name NOT LIKE '（示範）%' "
                              "GROUP BY s.customer_id HAVING COUNT(*)>1").fetchall():
        if not con.execute("SELECT 1 FROM orders WHERE customer_id=? AND substr(date,1,7) IN (?,?) AND status!='cancelled'", (cid, this, key)).fetchone():
            continue                                  # 這個月、下個月都沒服務 → 不問
        acc, to = lb.customer_target(con, cid)
        if to:
            m = lb.card_house_month(con, cid, key)
            lb.queue(con, acc, to, 'house_month', m[0]['altText'], m); n += 1
    return n

def crew_auto_ok(con):
    """負責人 派員審核 3 小時沒人按 → 自動通過（08:00–21:00；管理群照樣可以按「換掉」）。設定 crew_auto_hours=0 可關。"""
    import leave
    hrs = float(ix.setting(con, 'crew_auto_hours') or 3)
    now = dt.datetime.now(c.TZ)
    if hrs <= 0:
        return 0
    day_ok = '08:00' <= now.strftime('%H:%M') < '21:00'
    rush = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(10, 0), tzinfo=c.TZ)
    n = 0
    for o in c.rows(con, "SELECT o.*,(SELECT MAX(x.created_at) FROM line_outbox x WHERE x.order_id=o.id AND x.kind='crew_review') rv FROM orders o "
                         "WHERE o.status IN ('review','draft') AND COALESCE(o.crew_ok,'')='' AND o.date>=?", (now.date().isoformat(),)):
        try:
            start = dt.datetime.fromisoformat(f"{o['date']}T{o['start'] or '09:00'}").replace(tzinfo=c.TZ)
            sent = dt.datetime.fromisoformat(o['rv']) if o['rv'] else None
        except ValueError:
            continue
        if not sent:
            continue
        wait = min(hrs, max(0.5, (start - sent).total_seconds() / 3600 / 4))      # 稽核：離開工越近等越短（以前固定 3 小時）
        if now - sent < dt.timedelta(hours=wait) or not (day_ok or start <= rush):    # 明早 10:00 前開工的急單，晚上也照樣通過
            continue
        if ix.setting(con, f"dispatch_hold:{o['id']}"):
            continue                                   # 管理層擋住的單不自動通過
        crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        if crew >= (o['required_workers'] or 1):
            leave.crew_approve(con, o['id'], f'系統（{wait:g} 小時沒人審，自動通過）'); n += 1
    return n

def bind_chase(con):
    import line_bot as lb, pairing
    if dt.datetime.now(c.TZ).strftime('%H:%M') < '10:00' or ix.setting(con, 'bind_chase_day') == c.now()[:10] or not pairing.push_on(con):
        return 0                                       # 綁定待確認改在中控台首頁／LINE 配對頁，不推播
    old = c.rows(con, "SELECT * FROM bind_requests WHERE status='open' AND created_at<=? ORDER BY created_at", ((dt.datetime.now(c.TZ) - dt.timedelta(hours=12)).isoformat(),))
    ix.set_setting(con, 'bind_chase_day', c.now()[:10])
    g = lb.admin_group(con)
    if not old or not g:
        return 0
    bs = []
    for r in old[:9]:
        ref = con.execute(f"SELECT name FROM {'customers' if r['kind'] == 'customer' else 'workers'} WHERE id=?", (r['ref_id'],)).fetchone()
        bs.append(lb._bubble(lb._header('LINE 綁定待確認', r['name'], '#C47F00'),
                  [lb._row('類型', '夥伴' if r['kind'] == 'worker' else '客戶'), lb._row('填的電話', r['phone'] or '—'), lb._row('對到', ref[0] if ref else '—'),
                   lb._row('LINE 名稱', r['line_name'] or '—'), lb._row('等了', f"{(dt.datetime.now(c.TZ) - dt.datetime.fromisoformat(r['created_at'])).days} 天")],
                  [lb._btn('確認是本人，綁定', data=f"a=br_ok&r={r['id']}", primary=True), lb._btn('不是本人', data=f"a=br_no&r={r['id']}")]))
    m = lb._carousel(f'LINE 綁定還有 {len(old)} 筆待確認，請派單管理群處理', bs)
    lb.queue(con, lb.WORK_ACCOUNT, g, 'bind_chase', m[0]['altText'], [{'type': 'text', 'text': f'📌 LINE 綁定還有 {len(old)} 筆待確認（電話對到既有資料）。確認是本人就按「綁定」，任何一位管理層按都可以。'}] + m)
    return len(old)

# ───────────── 每月：夥伴回報可排時間、月初薪資與請款整理 ─────────────
def monthly(con):
    import line_bot as lb, worker_portal, board, reviews
    now = dt.datetime.now(c.TZ)
    if now.strftime('%H:%M') < '10:00':
        return 0
    n = 0
    nxt = (now.date().replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    if now.day >= 25 and ix.setting(con, 'avail_ask_month') != nxt.isoformat()[:7]:      # 25 號：請夥伴回報下個月可排時間
        ix.set_setting(con, 'avail_ask_month', nxt.isoformat()[:7])
        boss = lb.bosses(con)
        for w in c.rows(con, "SELECT DISTINCT w.id,w.name,l.account,l.user_id FROM workers w JOIN line_users l ON l.worker_id=w.id AND l.is_friend=1 WHERE w.status='active'"):
            if w['user_id'] in boss or is_blacklisted(con, w['id']):
                continue
            m = lb.flex(f'{nxt.month} 月可以排的時間，請回報 📅', lb._bubble(lb._header(f'{nxt.month} 月可排時間 📅', w['name'], '#2E7D32'),
                [lb._t(f'下個月哪幾天可以、哪幾天不行，點一下就好（30 秒）。有回報的人，派工會優先找你。', wrap=True)],
                [lb._btn('回報可排時間', uri=worker_portal.portal_url(con, w['id']) + '#avail', primary=True)]))
            lb.queue(con, w['account'], w['user_id'], 'avail_ask', m[0]['altText'], m); n += 1
    last = (now.date().replace(day=1) - dt.timedelta(days=1)).isoformat()[:7]
    if ix.setting(con, 'month_summary') != last:                                          # 月初：上個月薪資、待開帳、逾期帳單 → 店長＋負責人
        ix.set_setting(con, 'month_summary', last)
        b = board.billing_board(con, last)
        pay = sum(w['pay'] for w in b['payroll']); unbilled = sum(len(x['unbilled_completed']) for x in b['customers'])
        rows = [lb._row('夥伴薪資', f"{len(b['payroll'])} 位，共 NT$ {pay // 100:,}（含代墊、取消補貼）"), lb._row('完工待開帳', f'{unbilled} 筆'),
                lb._row('應收／已收', f"{b['totals']['expected'] // 100:,}／{b['totals']['paid'] // 100:,}"), lb._row('逾期未收', f"NT$ {b['totals']['overdue'] // 100:,}")]
        m = lb.flex(f'{int(last[5:])} 月結帳整理', lb._bubble(lb._header(f'{int(last[5:])} 月結帳整理 🧾', '月初對帳', '#0A84FF'), rows,
            [lb._btn('打開中控台結帳', uri=f'{lb.BASE_URL}/#billing', primary=True)], '薪資明細可以在結帳 › 夥伴薪資一鍵複製給每位夥伴；開帳後請款訊息會帶各品牌匯款帳號。'))
        for u in lb.money_users(con):
            lb.queue(con, lb.WORK_ACCOUNT, u, 'month_summary', m[0]['altText'], m); n += 1
    return n

def _safe(con, f):
    """跑一個自動化步驟：出錯只回復這一步（SAVEPOINT），錯誤記到「功能回報」（同一種只記一筆），其他步驟照跑。"""
    con.execute('SAVEPOINT st')
    try:
        f(con); con.execute('RELEASE st'); return True
    except Exception as e:
        con.execute('ROLLBACK TO st'); con.execute('RELEASE st')
        import traceback, sysreport
        traceback.print_exc()
        try:
            sysreport.auto(con, f"自動化 {getattr(f, '__module__', '')}.{getattr(f, '__name__', f)}", e, traceback.format_exc())
        except Exception:
            traceback.print_exc()
        return False

def _followers_tick(con):
    import followers                    # LINE 認證通過 → 自動同步全部好友（比對業主交給 LINE 配對）
    return followers.tick(con)

def _urgent_tick(con):
    import urgent                       # 緊急加派有人接／沒人接每 10 分鐘問下一級
    return urgent.tick(con)

def _crew_tick(con):
    import crewcheck                    # 前一天 21:00 還沒確認出勤 → 管理群一則
    return crewcheck.tick(con)

def _quota_retry(con):
    import quotaretry                   # LINE 額度用完 → 恢復後自動補發還有用的訊息
    return quotaretry.tick(con)

def _meetup_tick(con):
    import meetup                       # 夥伴大會（新綁好的補發邀請、截止送定案卡）
    return meetup.tick(con)

def _aib_tick(con):
    import aibrief                      # 業主傳新訊息 → AI 整理重點給管理群核對
    return aibrief.tick(con)

def _calendar_tick(con):
    return ix.calendar_tick(con)       # 雲端搬本機：Google 日曆直連同步（預設關，店長授權後開）

def _daidiu_tick(con):
    import daidiu                       # 垃圾代丟：固定夥伴、往後補 4 週、取消／逾期停
    return daidiu.tick(con)

def _rs_urgent(con):
    import reschedule                   # 稽核 優化：業主改到明天、還沒處理 → 立刻提醒
    return reschedule.urgent_tick(con)

def _geo_tick(con):
    import sitegeo                      # 14 天內有工作的案場查國土測繪精準定位（夥伴接單後導航用）
    return sitegeo.tick(con)

def _ncast_watch(con):
    import broadcast                    # 指定對象群發（扣掉夥伴／已綁定）LINE 處理完才上線、通知
    return broadcast.narrowcast_watch(con) + broadcast.bind_due_tick(con)      # 排定日期的綁定群發（企業線 11/1）

def step(con):
    """背景每分鐘：缺人詢問、自動完工、完工關心、LINE 額度檢查、待整理提醒……（每一步各自獨立，一步壞了不影響其他）。"""
    import leave, supplies, door, prep; leave.ensure(con); supplies.ensure(con); door.ensure(con); prep.ensure(con)      # 新欄位／資料表（重複呼叫不會怎樣）
    import followup, decisions, testimonials, interviews, shop, recruit, pending, site_report, mkinsight, mkbridge, dispatch, tiers, ledger
    testimonials.ensure(con); shop.ensure(con); recruit.ensure(con)
    for f in (ask_shortage, auto_complete, care, quota_watch, triage_auto, triage_digest,
              followup.run,                    # 客戶端報價／照片追蹤（過期沒簽列進人工追）
              chase, decisions.rotate_reminder, onsite, bind_chase, deliver_pending_finish, finish_chase, accept_chase,
              interviews.tick,                 # 夥伴面試：提醒、追時間、追結果
              door.watch, prep.digest, pending.digest, monthly, subscriptions, retention,
              site_report.remind,              # 現場回報 15 分鐘沒人處理 → 再提醒管理群
              stale_escalate, customer_waiting,    # 4 小時沒處理再 @；客戶留言 4 小時沒回提醒
              customer_pairing, photo_auto_approve, crew_auto_ok,      # 業主對 LINE、照片／人員沒人審自動通過
              _linepay_demo_cleanup,                                  # LINE Pay 示範款項隔天自動取消
              house_month,                                            # 多館業主每月 20 號確認下個月館別
              _guide_remind,                                          # 說明書新版 → @管理層請大家看一遍，每天提醒沒看完的
              quote_review_chase,                                     # 報價待確認 2 小時一對一店長、滿 1 天每天 @管理層
              _estquiz, _materials_remind, _scope_chase, _campaign, applicant_purge, _door_auto, _leave_auto, photo_hold_cards,
              shop.expire_unlinked,                                   # 網站訂單 24 小時沒連 LINE 自動取消（幽靈單）
              view_expire,
              mkinsight.daily,
              mkbridge.tick,
              dispatch.hold_tick,
              tiers.upgrade_check,
              ledger.tick,
              _ncast_watch,
              _geo_tick,
              dispatch.av_remind,
              _rs_urgent,
              _daidiu_tick,
              _urgent_tick,
              _crew_tick,
              _aib_tick,
              _meetup_tick,
              _quota_retry,
              _followers_tick,
              _calendar_tick):                                   # D／C 跟 A／B 做過 2 人案場 → 問店長升級（規則打開才跑）                                    # 找人中（先不要自動派）人到齊解除、開工前 12 小時提醒                                         # 行銷送審橋接：送審佇列 → 審核卡、核准的群發照時間發、待人工提醒                                       # 行銷：去識別的真實對話＋服務價格事實 → 行銷_客戶洞察（每天 03:00 後）
                                                        # 稽核 B8：測試視角 2 小時自動切回                          # 店長估價練習（30 天）；管理群素材準備提醒
        _safe(con, f)

def view_expire(con):
    """稽核 B8：管理層切到客戶／夥伴測試視角超過 2 小時 → 自動切回管理選單（示範款項一起收掉），免得真的通知傳錯人。"""
    import line_bot as lb
    cut = (dt.datetime.now(c.TZ) - dt.timedelta(hours=2)).isoformat(timespec='seconds'); n = 0
    for uid, v in list(lb.viewing(con).items()):
        if (v.get('at') or '') < cut:
            lb.view_back(con, lb.WORK_ACCOUNT, uid)
            lb.queue(con, lb.WORK_ACCOUNT, uid, 'view_back', '已自動切回管理選單', lb._txt('⏱ 測試視角超過 2 小時，已自動切回管理選單（示範款項已取消）。要再試就按「切換測試視角」。')); n += 1
    return n

def photo_hold_cards(con):
    """稽核 H3：完工了但沒有服務前／完成照片（補登、中控台完工登記、系統自動完工都不看照片）→ 薪資暫不發。
    服務日隔天起（白天）派單管理群一張卡「確認，照常發薪」，每張工單一次；夥伴補拍了就不用。"""
    import board, line_bot
    if not ('09:00' <= dt.datetime.now(c.TZ).strftime('%H:%M') <= '21:00') or ix.setting(con, 'standing_push') != 'on':
        return 0                          # 照片不齊放行是常態的事 → 10:30 那一則寫數字，中控台「管理群待處理」按「確認，照常發薪」
    start = ix.setting(con, 'photo_pay_from') or '2026-10-14'
    since = max(start, (_today() - dt.timedelta(days=14)).isoformat())
    n = 0
    for o in c.rows(con, "SELECT o.* FROM orders o LEFT JOIN sites s ON s.id=o.site_id WHERE o.status='completed' AND o.date>=? AND o.date<? "
                         "AND COALESCE(s.no_photo,0)=0 AND COALESCE(o.review_note,'')!=?", (since, _today().isoformat(), DEMO)):
        if ix.setting(con, f"photo_ok:{o['id']}") or ix.setting(con, f"photo_card:{o['id']}") or ix.setting(con, f"nophoto_job:{o['id']}"):      # 稽核 F2
            continue
        a = con.execute("SELECT a.*,o.date,o.status ostatus FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.order_id=? AND a.status='accepted' LIMIT 1", (o['id'],)).fetchone()
        hold = board.photo_hold(con, dict(a)) if a else ''
        if hold:
            ix.set_setting(con, f"photo_card:{o['id']}", c.now())
            line_bot.photo_hold_card(con, o, hold); n += 1
    return n

def quote_review_chase(con):
    """負責人 「這東西不可以拖那麼久」：報價卡在「待主管確認」——
    進入待確認滿 2 小時 → 派單管理群 @店長報價卡（LINE 一鍵：確認並傳給客戶／重打品項金額／改現場估價／已用其他方式給客戶／取消）；
    滿 1 天 → 每天 10:00 起在派單管理群 @管理層列出來，直到處理完。08:00–22:00 才發。"""
    import line_bot as lb, quickquote
    now = dt.datetime.now(c.TZ); hm = now.strftime('%H:%M'); today = now.date().isoformat()
    if not ('08:00' <= hm < '22:00'):
        return 0
    n, old = 0, []
    for q in c.rows(con, "SELECT id,created_at,date FROM quotes WHERE status='draft' AND stage='review' ORDER BY created_at"):
        k = f"qreview_at:{q['id']}"                 # 稽核：從「進入待確認」那刻算（以前從建立時間算，補完照片一轉待確認就發「等了 N 天」）
        since = ix.setting(con, k) or q['created_at']      # booking._review_now 在轉成待確認時記
        try:
            age = now - dt.datetime.fromisoformat(since)
        except ValueError:
            continue
        wait = f"等了 {age.days} 天" if age.days else f"等了 {int(age.total_seconds() // 3600)} 小時"
        if age >= dt.timedelta(hours=2) and not ix.setting(con, f"qrv1:{q['id']}"):
            ix.set_setting(con, f"qrv1:{q['id']}", c.now())
            m = quickquote.card(con, q['id'], wait)
            lb.to_managers(con, 'quote_chase', m[0]['altText'], m); n += 1          # 送管理群 @店長
        if age >= dt.timedelta(days=1) or (q['date'] and q['date'] <= (now.date() + dt.timedelta(days=2)).isoformat()):      # 滿 1 天，或服務日就在 2 天內
            old.append((q['id'], wait))
    g = lb.admin_group(con)
    if old and g and hm >= '10:00' and ix.setting(con, 'qrv_group_day') != today:
        ix.set_setting(con, 'qrv_group_day', today)
        bs = [quickquote.card(con, qid, wait)[0]['contents'] for qid, wait in old[:8]]
        tip = f"🧮 有 {len(old)} 張報價卡在「待主管確認」太久，請今天處理（點下面卡片：確認並傳給客戶、改金額、已用其他方式給客戶或取消；只有看得到金額的人按得動）。"
        lb.queue(con, lb.WORK_ACCOUNT, g, 'quote_chase', f'{len(old)} 張報價待確認太久', [lb.ping(con, tip, who=sorted(lb.staff_users(con)))] + lb._carousel('報價待確認太久', bs))
        n += 1
    return n

def _guide_remind(con):
    import guide
    return guide.remind(con)

def _linepay_demo_cleanup(con):
    import linepay
    return linepay.demo_cleanup(con)

def stale_items(con):
    """還沒處理的事（請假、用品、現場回報、面試）：[(key, 種類, 說明, order_id, 建立時間)]。"""
    out = []
    import leave, supplies, site_report, interviews
    leave.ensure(con); supplies.ensure(con); site_report.ensure(con); interviews.ensure(con)
    for r in c.rows(con, "SELECT l.*,w.name FROM worker_leaves l JOIN workers w ON w.id=l.worker_id JOIN orders o ON o.id=l.order_id "
                         "WHERE (l.status='pending' OR l.handling='') AND l.status!='withdrawn' AND o.status NOT IN ('cancelled','archived','completed')"):      # 稽核 B3：我方調整的也要 4 小時再提醒
        out.append((f"lv:{r['id']}", '請假', f"{r['name']}（{r['reason']}）{'還沒判斷' if r['status'] == 'pending' else ''}{'、這場還沒處理' if not r['handling'] else ''}", r['order_id'], r['created_at']))
    for r in c.rows(con, "SELECT s.*,w.name FROM supply_requests s JOIN workers w ON w.id=s.worker_id WHERE s.status='pending'"):
        out.append((f"sup:{r['id']}", '用品申請', f"{r['name']}：{r['item'][:30]}", r['order_id'] or None, r['created_at']))
    for r in c.rows(con, "SELECT r.*,w.name FROM site_reports r JOIN workers w ON w.id=r.worker_id JOIN orders o ON o.id=r.order_id WHERE r.status='open' AND o.status NOT IN ('cancelled','archived')"):
        out.append((f"srp:{r['id']}", '現場回報', f"{r['name']}：{site_report.summary(con, r)}", r['order_id'], r['created_at']))
    for r in c.rows(con, "SELECT i.*,w.name FROM interviews i JOIN workers w ON w.id=i.worker_id WHERE i.status IN ('screening','need_time')"):
        out.append((f"iv:{r['id']}", '面試', f"{r['name']}（{'待篩選' if r['status'] == 'screening' else '等我們排面試時間' if r['want'] == '等我們排電話面試時間' else '想約其他時間'}）", None, r['created_at']))
    for r in c.rows(con, "SELECT d.id,d.order_id,d.at,d.detail,o.title,o.date FROM dispatch_log d JOIN orders o ON o.id=d.order_id WHERE d.action='customer_change' AND d.at>datetime('now','-7 days') "
                         "AND o.status IN ('review','draft','confirmed') AND (COALESCE(o.feedback_done,'')='' OR o.feedback_done<d.at)"):      # 稽核：業主改期／取消申請也要追
        try:
            at = dt.datetime.fromisoformat(r['at'].replace(' ', 'T')).replace(tzinfo=dt.timezone.utc).astimezone(c.TZ).isoformat(timespec='seconds')
        except ValueError:
            at = ''
        out.append((f"chg:{r['id']}", '客戶改期／取消', f"{r['date']} {r['title']}：{r['detail'] or '想改時間'}", r['order_id'], at))
    return out

def stale_escalate(con):
    """負責人 請假、用品、現場回報、面試 4 小時沒處理 → 派單管理群再 @ 一次（每件一次；之後每天 10:30 待處理卡列到處理完）。"""
    import line_bot as lb
    if lb.quiet():
        return 0
    cut = (dt.datetime.now(c.TZ) - dt.timedelta(hours=4)).isoformat(timespec='seconds'); n = 0
    for key, kind, text, oid, at in stale_items(con):
        if at and at < cut and not ix.setting(con, f'esc4:{key}'):
            ix.set_setting(con, f'esc4:{key}', c.now())
            lb.admin_notify(con, f'⏰ {kind} 4 小時沒處理', [('項目', text), ('處理', '請按原本那張卡的按鈕，或到中控台處理')], '', '#C62828', oid=oid, tag=f'{kind}4 小時沒處理')
            n += 1
    return n

AUTO_KINDS = ('remind', 'urgent_offer', 'prep_ask', 'entry_ask', 'tools_ask', 'care', 'accept_remind', 'confirm_ask', 'door_morning', 'arrived_c', 'near_c',
              'finish', 'fu_', 'house_month', 'issue_summary', 'admin', 'aib', 'worker_notice', 'dispatch', 'materials', 'av_remind', 'eta_ask', 'arrive_nudge',
              'prestart', 'view_back', 'done_ask', 'pending', 'hello', 'rating', 'pay_remind', 'daidiu', 'att_', 'before_nudge', 'leave_nudge', 'bind')
DONE_RE = r'^\s*(好的?|好喔|好唷|好哦|好滴|嗯+|喔+|ok|okay|OK|Ok|收到|了解|瞭解|知道了|謝謝|感謝|多謝|辛苦了|沒問題|👍|🙏|😊|🙂|，|。|!|！|~|～|\s)+\s*(您|你|妳)?\s*(了|喔|唷|囉|啦)?\s*$'

def answered(con, acc, uid, since):
    """客戶留言之後，我們是不是已經回了 → 回原因（不用提醒）或 ''。
    ①系統之後有傳給他「不是例行提醒」的訊息（報價、改期結果、取消、收款…）②他最後一句只是「好的／謝謝／收到」這類收尾（代表我們回過了）。
    官方 LINE 後台手動回的訊息系統看不到，所以用②補。"""
    import re as _re
    for kind, at in con.execute("SELECT kind,created_at FROM line_outbox WHERE account=? AND to_id=? AND status='sent' AND created_at>? ORDER BY id DESC LIMIT 20", (acc, uid, since)).fetchall():
        if not str(kind or '').startswith(AUTO_KINDS):
            return f'系統之後已傳給客戶（{kind}）'
    last = ix.setting(con, f'cmsg_last:{acc}:{uid}') or ''
    if last and _re.match(DONE_RE, last):
        return f'客戶最後一句是「{last[:20]}」（收尾，代表已經回過）'
    return ''

def customer_waiting(con):
    """負責人 已綁客戶在官方 LINE 留言（系統沒處理的）→ 提醒派單管理群（附「已回覆」）；只在 08:00–21:00。
    改成「超過 4 小時還沒回」才提醒（settings cmsg_wait_min，預設 240），同一位 4 小時內只提醒一次。"""
    import line_bot as lb
    now = dt.datetime.now(c.TZ)
    if not ('08:00' <= now.strftime('%H:%M') < '21:00'):
        return 0
    n = 0
    wait = int(ix.setting(con, 'cmsg_wait_min') or 240)
    for k, at in con.execute("SELECT key,value FROM settings WHERE key LIKE 'cmsg:%' AND value!=''").fetchall():
        if at > (now - dt.timedelta(minutes=wait)).isoformat(timespec='seconds'):
            continue
        _, acc, uid = k.split(':', 2)
        if ix.setting(con, f'cmsg_rem:{acc}:{uid}') > (now - dt.timedelta(minutes=wait if not ix.setting(con, f'cmsg_open:{acc}:{uid}') else 24 * 60)).isoformat(timespec='seconds'):      # 精簡 → 同一位提醒過後一天最多再一次
            continue                       # 每日檢查：剛提醒過 → 先累積，2 小時後合併再提醒一次（以前直接丟掉，之後再也不提醒）
        lu = con.execute('SELECT customer_id,display_name FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone()
        cu = con.execute('SELECT name,phone FROM customers WHERE id=?', (lu['customer_id'],)).fetchone() if lu and lu['customer_id'] else None
        why_ = answered(con, acc, uid, at)
        if why_:                           # 送「還沒回」之前先確認我們是不是已經回了
            import board as _bd
            for k_ in (k, f'cmsg_txt:{acc}:{uid}', f'cmsg_last:{acc}:{uid}'):
                ix.set_setting(con, k_, '')
            c.audit(con, 'cmsg_auto_done', 'line_users', uid, after={'why': why_})
            continue
        txt = ix.setting(con, f'cmsg_txt:{acc}:{uid}').strip()
        rows = [lb._row('客戶', f"{cu['name'] if cu else '—'}（LINE：{lu['display_name'] if lu else '—'}）"), lb._row('品牌', __import__('brands').name(acc)),
                lb._row('留言', txt[-80:] or '（圖片或貼圖）'), lb._row('處理', '請到這個品牌的官方 LINE 聊天室回覆，回完按下面')]
        oq = con.execute("SELECT id,stage FROM quotes WHERE customer_id=? AND status='draft' AND stage IN ('review','sent','site_visit') ORDER BY created_at DESC LIMIT 1",
                         (lu['customer_id'],)).fetchone() if lu and lu['customer_id'] else None      # Lin：報價還沒成立，客戶在聊天說好了 → 提醒可以直接成立
        if oq:
            rows.append(lb._row('報價', f"{oq['id']} 還沒成立（{'待主管確認' if oq['stage'] == 'review' else '已傳給客戶、還沒簽'}）：客戶在聊天說好了，就打開報價按「客戶已同意，直接成立」，系統會建工單、自動找夥伴"))
        m = lb.flex(f"💬 客戶留言超過 {wait // 60} 小時還沒回：{cu['name'] if cu else ''}", lb._bubble(lb._header('客戶留言還沒回', cu['name'] if cu else '客戶', '#0A84FF'), rows,
                    [lb._btn('已回覆', data=f'a=cmsg_ok&u={acc}|{uid}', primary=True)] + ([lb._btn('打開報價（談好了可以直接成立）', uri=f"{lb.BASE_URL}/#q/{oq['id']}")] if oq else [])))
        g = lb.admin_group(con)
        if g:
            lb.queue(con, lb.WORK_ACCOUNT, g, 'cmsg', m[0]['altText'], m)
        old = json.loads(ix.setting(con, f'cmsg_open:{acc}:{uid}') or '{}') if ix.setting(con, f'cmsg_open:{acc}:{uid}') else {}
        ix.set_setting(con, f'cmsg_open:{acc}:{uid}', json.dumps({'at': old.get('at') or at, 'customer': cu['name'] if cu else (lu['display_name'] if lu else ''),
                                                                  'brand': __import__('brands').name(acc), 'text': ((old.get('text') or '') + ' ' + txt).strip()[-120:]}, ensure_ascii=False))      # 中控台「管理群待處理」看得到、可以按已回覆
        ix.set_setting(con, f'cmsg_rem:{acc}:{uid}', c.now()); ix.set_setting(con, k, ''); ix.set_setting(con, f'cmsg_txt:{acc}:{uid}', '')
        n += 1
    return n

def retention(con):
    """資安：客戶在 LINE 打的原文（可能有大門密碼）不永久保存。保留天數由店長決定（settings inbox_keep_days；
    沒設定＝先不清）。每天清一次：超過天數的 line_inbox 文字清空、已送出訊息的內容清空（只留時間、種類，統計照算）。"""
    days = int(ix.setting(con, 'inbox_keep_days') or 0)
    if days <= 0 or ix.setting(con, 'retention_day') == c.now()[:10]:
        return 0
    ix.set_setting(con, 'retention_day', c.now()[:10])
    cut = (dt.datetime.now(c.TZ) - dt.timedelta(days=days)).isoformat()
    n = con.execute("UPDATE line_inbox SET text='',data='' WHERE at<? AND (COALESCE(text,'')!='' OR COALESCE(data,'')!='')", (cut,)).rowcount
    import re as _re
    for oid_, pl in con.execute("SELECT id,payload FROM line_outbox WHERE created_at<? AND payload NOT LIKE '[{\"type\": \"text\", \"text\": \"（已清除）%' AND payload!='[]'", (cut,)).fetchall():
        keep = ' '.join(sorted(set(_re.findall(r'w=[A-Za-z0-9_-]+', pl or ''))))      # 留「問過哪位夥伴」的記號，其餘內容清掉
        con.execute('UPDATE line_outbox SET payload=? WHERE id=?', (json.dumps([{'type': 'text', 'text': f'（已清除）{keep}'}], ensure_ascii=False), oid_)); n += 1
    return n

def subscriptions(con):
    """每月 1 號 10:00 後：訂閱方案（每週／隔週／生活線月方案）建當月收款單、傳付款通知（稽核：以前只有第一個月有收款單）。"""
    import payments, line_bot as lb
    now = dt.datetime.now(c.TZ); month = now.strftime('%Y-%m')
    if now.strftime('%H:%M') < '10:00' or ix.setting(con, 'sub_day') == now.date().isoformat():
        return 0
    ix.set_setting(con, 'sub_day', now.date().isoformat())          # 稽核 B4：每天檢查（月中才排班的也會開；同月不會重複）
    made = payments.subscriptions(con, month)
    for pid in made:
        r = dict(con.execute('SELECT * FROM payment_requests WHERE id=?', (pid,)).fetchone())
        q = con.execute('SELECT account,token,inquiry_id FROM quotes WHERE id=?', (r['quote_id'],)).fetchone()
        inq = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone() if q and q['inquiry_id'] else None
        lb.on_payment_notice(con, q['account'], inq[0] if inq and inq[0] else None, r['customer_id'], q['token'], [r], '本月訂閱費 🧾', f"{int(month[5:])} 月的定期服務費用，請於 {int(month[5:])}/5 前付款")
    if made:
        rows = [(c.get(con, 'customers', r[0])['name'], f"NT$ {r[1] // 100:,}") for r in con.execute(
            f"SELECT customer_id,amount FROM payment_requests WHERE id IN ({','.join('?' * len(made))})", made)]
        lb.admin_notify(con, f'🧾 {int(month[5:])} 月訂閱費：已建 {len(made)} 筆收款單', rows[:12] + [('處理', '已自動傳付款通知給客戶；收到款項請按確認收款')], '/#billing', '#0A84FF')
    return len(made)
