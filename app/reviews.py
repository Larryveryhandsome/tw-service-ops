"""店長審核資料庫：要對外公告、改價、夥伴薪資等，先送店長在 LINE 點選審核。

- 每件送審都記錄（staff_reviews）：類別、標題、內容、結果、修改意見、誰、何時。
- 店長點「核准，這類以後不用審核」→ review_rules 把這個類別設成免審，之後同類直接通過（審核會越來越少）。
- 同一類連續核准、沒有修改達 AUTO_HINT 次 → 卡片上提示「要不要以後免審」。
- 她的修改意見會存下來，之後送審前先參考（learnings）。
"""
import json
import core as c

AUTO_HINT = 3
BROADCAST = ('群發公告', '年度保養詢問', '夥伴公告')       # 群發一律要管理層審核（內容和對象），不能設成免審
NO_AUTO = BROADCAST + ('行銷內容', '開發信文案', '官網文案')      # 每日檢查：對外大量發送的文案每次都要審                      # 行銷內容也每次都要審（店長審核是唯一人工關卡），不能設成免審
STATUS = {'pending': '待審核', 'approved': '已核准', 'changes': '要修改', 'auto': '免審直接通過', 'superseded': '已改版（看新版）'}

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS staff_reviews(id TEXT PRIMARY KEY, created_at TEXT, category TEXT, title TEXT, body TEXT, payload TEXT DEFAULT '{}', "
                "status TEXT DEFAULT 'pending', decided_by TEXT DEFAULT '', decided_at TEXT DEFAULT '', note TEXT DEFAULT '', requested_by TEXT DEFAULT '')")
    con.execute("CREATE TABLE IF NOT EXISTS review_rules(category TEXT PRIMARY KEY, auto_approve INTEGER DEFAULT 0, set_by TEXT DEFAULT '', set_at TEXT DEFAULT '')")

def is_auto(con, category):
    ensure(con)
    if category in NO_AUTO:
        return False
    r = con.execute('SELECT auto_approve FROM review_rules WHERE category=?', (category,)).fetchone()
    return bool(r and r[0])

def streak(con, category):
    """這個類別最近連續「直接核准（沒修改）」幾次。"""
    n = 0
    for r in con.execute("SELECT status FROM staff_reviews WHERE category=? AND status IN ('approved','changes') ORDER BY decided_at DESC", (category,)):
        if r[0] != 'approved':
            break
        n += 1
    return n

def learnings(con, category, limit=5):
    """這類過去被要求修改的意見（送審前先參考，減少來回）。"""
    ensure(con)
    return [dict(r) for r in con.execute("SELECT title,note,decided_at FROM staff_reviews WHERE category=? AND status IN ('changes','superseded') AND decided_by!='' AND note!='' ORDER BY decided_at DESC LIMIT ?", (category, limit))]

def submit(con, category, title, body, payload=None, requested_by='Claude'):
    """送審。免審類別直接通過（回傳 status='auto'）；否則傳卡片給店長。回傳 {'id','status'}。"""
    import line_bot as lb
    ensure(con)
    rid = c.uid('rv')
    auto = is_auto(con, category)
    con.execute('INSERT INTO staff_reviews(id,created_at,category,title,body,payload,status,requested_by,decided_by,decided_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (rid, c.now(), category, title[:120], body[:6000], json.dumps(payload or {}, ensure_ascii=False), 'auto' if auto else 'pending', requested_by,
                 '免審規則' if auto else '', c.now() if auto else ''))
    if auto:
        lb.queue(con, lb.WORK_ACCOUNT, lb.OWNER_UID, 'admin', f'免審通過：{title}', lb._txt(f'🟢 【{category}】已設為免審，直接通過：{title}'))
        after_approve(con, {'payload': json.dumps(payload or {}, ensure_ascii=False)}, '免審規則')
        return {'id': rid, 'status': 'auto'}
    send_card(con, rid, category, title, body, preview=(payload or {}).get('preview'))
    return {'id': rid, 'status': 'pending'}

def send_card(con, rid, category, title, body, preview=None):
    """審核卡：一律送派單管理群（所有審核都推管理群，中控台只是另一個入口），負責人、店長、客服誰核准都可以（只會核准一次）。
    群發類、行銷內容（NO_AUTO）沒有「以後不用審核」。"""
    import line_bot as lb
    bc = category in BROADCAST
    n = streak(con, category)
    hint = ('群發一律要管理層審核（內容和對象都在上一則），核准後才會發出。' if bc else
            f'這類已連續 {n} 次直接核准，可以考慮以後免審。' if n >= AUTO_HINT else '核准後才會發布／生效。')
    btns = [lb._btn('✅ 核准', data=f'a=rv_ok&r={rid}', primary=True), lb._btn('✏️ 要修改（打字說明）', data=f'a=rv_edit&r={rid}')]
    if category not in NO_AUTO:
        btns.append(lb._btn('🟢 核准，這類以後不用審核', data=f'a=rv_auto&r={rid}'))
    card = lb.flex(f'請審核：{title}', lb._bubble(lb._header(f'請審核・{category}', title[:40], '#0A84FF'),
                   [lb._t('內容在上一則訊息，看完請點選：', wrap=True, color=lb.SUB, size='sm')], btns, hint))
    pv = lb.preview_copy(preview or [{'type': 'text', 'text': body}])[:2] if bc else []
    note = '\n\n👇 下一則是客戶／夥伴實際會收到的樣子（按鈕可以點點看，只會說明會發生什麼，不會真的觸發）' if bc else ''
    msgs = [{'type': 'text', 'text': (f'【送審】{title}\n\n{body}'[:4800] + note)}] + pv + card
    lb.to_managers(con, 'admin_review', f'請審核：{title}', msgs,          # 不私訊店長；一律推管理群（沒有管理群才一對一給店長）
                   tag=(f'📣 群發送審：{title}。內容、發給誰、實際收到的樣子都在下面，管理層看過按「核准」才會發出。' if bc else f'請店長（或管理層）審核：{title} 👇'))

def reviewers(con):
    import line_bot as lb
    return list(lb.deciders(con)) or [lb.OWNER_UID]

def decide(con, rid, decision, by, note=''):
    """decision：approved／changes／auto（核准並把這類設為免審）。"""
    ensure(con)
    r = con.execute('SELECT * FROM staff_reviews WHERE id=?', (rid,)).fetchone()
    if not r:
        raise ValueError('找不到這件審核')
    if r['status'] == 'superseded':
        return dict(r), '這件已經改成新版了，請看最新一則送審 🙏'
    if r['status'] in ('approved', 'auto'):          # 稽核：已核准（已執行）的不能再改回「要修改」，不然再按核准會再執行一次
        return dict(r), f"這件已經核准過了（{r['decided_by']}）" + ('；要改請重新送審' if decision == 'changes' else '')
    if r['category'] in BROADCAST and r['status'] == 'changes' and decision in ('approved', 'auto'):
        return dict(r), f"已經有人按「要修改」了（{r['decided_by']}），請等改好的新版送審再核准 🙏"
    status = 'approved' if decision in ('approved', 'auto') else 'changes'
    if decision == 'auto' and r['category'] in NO_AUTO:
        decision = 'approved'                      # 群發、行銷內容不能設成免審（舊卡片按了也只算這次核准）
    con.execute("UPDATE staff_reviews SET status=?,decided_by=?,decided_at=?,note=CASE WHEN ?='' THEN note ELSE ? END WHERE id=?", (status, by, c.now(), note, note, rid))
    if decision == 'auto':
        con.execute('INSERT INTO review_rules(category,auto_approve,set_by,set_at) VALUES(?,1,?,?) ON CONFLICT(category) DO UPDATE SET auto_approve=1,set_by=excluded.set_by,set_at=excluded.set_at',
                    (r['category'], by, c.now()))
    c.audit(con, 'staff_review', 'staff_reviews', rid, after={'decision': decision, 'by': by, 'note': note})
    export_log(con)
    row = dict(con.execute('SELECT * FROM staff_reviews WHERE id=?', (rid,)).fetchone())
    if status == 'approved':
        after_approve(con, row, by)
    elif note:
        after_changes(con, row, by, note)
    return row, ''

def after_approve(con, row, by):
    """核准後要做的事（payload.action）：broadcast＝群發公告。"""
    try:
        p = json.loads(row.get('payload') or '{}')
    except ValueError:
        return
    if p.get('action') == 'broadcast':
        import broadcast
        broadcast.execute(con, p, by)
    elif p.get('action') == 'set_setting' and p.get('key') in ('interview_reject_msg', 'wbind_tpl_play', 'home_faq'):      # 陪玩招募範本      # 店長核准文案 → 生效
        import integrations as ix
        ix.set_setting(con, p['key'], p['value'])
        if p['key'] == 'home_faq':
            import seo; seo.clear_cache()                 # AEO：常見問答核准 → 官網馬上更新
        if p['key'] == 'interview_reject_msg':
            import interviews; interviews.flush_rejects(con)          # 先前等著的婉拒，補傳
    elif p.get('action') == 'bind_broadcast':            # 群發請還沒綁定的好友綁定（測試模式的品牌等改正式才發）
        import broadcast
        broadcast.execute_bind(con, p, by)
    elif p.get('action') == 'campaign_deep':             # 年度保養詢問（每個品牌一份）
        import campaign
        campaign.execute(con, p, by)
    elif p.get('action') == 'photo_share_on':           # 行銷：照片授權的詢問文字核准 → 開始在 4–5 星謝謝卡問
        import integrations as _ix
        _ix.set_setting(con, 'photo_share_ask', 'on')
    elif p.get('action') == 'richmenu_v2':               # LINE 下方六顆按鈕換新版（業主／夥伴／管理層）
        import richmenu2
        richmenu2.apply(con, by, only=p.get('only'))         # only：只換某幾份（例：示範服務店業主選單加「商城」小字）
    elif p.get('action') == 'meetup_open':               # 夥伴大會邀請（店長核准才發）
        import meetup
        meetup.open_(con, p.get('mid'), by)
    elif p.get('action') == 'typhoon_mail_ok':          # 颱風天開發信（關心＋服務）店長核准才啟用；檔案位置寫死，不從內容讀
        import pathlib
        f_ = pathlib.Path(c.ROOT).parent / '示範服務店' / '開發信_臺北工商' / 'template_typhoon.json'
        t_ = json.loads(f_.read_text(encoding='utf-8')); t_['approved'] = True; t_['_核准'] = f'{by} {c.now()[:16]}'
        f_.write_text(json.dumps(t_, ensure_ascii=False, indent=1), encoding='utf-8')
    elif p.get('action') == 'worker_broadcast':          # 夥伴公告（系統更新、新規定）
        import broadcast
        broadcast.execute_workers(con, p, by)
    elif p.get('action') in ('mk_package', 'mk_broadcast'):      # 行銷送審橋接：回寫審核結果（群發另外排時間發）
        import mkbridge
        mkbridge.on_decision(con, p, 'approved', by)

def after_changes(con, row, by, note):
    """按「要修改」打了意見之後：行銷送審 → 回寫 edit＋意見（行銷排程依意見改稿重送）。"""
    try:
        p = json.loads(row.get('payload') or '{}')
    except ValueError:
        return
    if p.get('action') in ('mk_package', 'mk_broadcast'):
        import mkbridge
        mkbridge.on_decision(con, p, 'edit', by, note)

def pending(con):
    ensure(con)
    return [dict(r) for r in con.execute("SELECT * FROM staff_reviews WHERE status='pending' ORDER BY created_at")]

def supersede(con, old_id, new_id):
    """舊的送審被新版取代（依她的意見改過）：舊卡片再按會提示看新版。"""
    ensure(con)
    con.execute("UPDATE staff_reviews SET status='superseded',note=TRIM(note || ' →新版 ' || ?) WHERE id=? AND status IN ('pending','changes')", (new_id, old_id))
    export_log(con)

def export_log(con):
    """店長的所有決定（審核＋點選回答）自動寫成 文件/店長決策紀錄.md，她一回覆就更新，不用人工整理。"""
    ensure(con)
    out = ['# 店長決策紀錄（系統自動產生，每次她回覆就更新）', '', '## 還在等她回覆', '']
    asked = [r[0][4:] for r in con.execute("SELECT key FROM settings WHERE key LIKE 'ask:%'")]
    answered = {r[0][7:] for r in con.execute("SELECT key FROM settings WHERE key LIKE 'answer:%'")}
    wait = [f'- 【提問】{t}' for t in asked if t not in answered] + [f'- 【審核】{r[0]}' for r in con.execute("SELECT title FROM staff_reviews WHERE status='pending'")]
    out += (wait or ['- （沒有）']) + ['', '## 點選提問的回答', '']
    for r in con.execute("SELECT key,value FROM settings WHERE key LIKE 'answer:%' ORDER BY key"):
        try:
            v = json.loads(r[1])
        except ValueError:
            continue
        out.append(f"- 【{r[0][7:]}】{v.get('answer')}（{v.get('by')} {str(v.get('at'))[5:16].replace('T', ' ')}）")
    out += ['', '## 審核紀錄', '']
    for r in con.execute('SELECT * FROM staff_reviews ORDER BY created_at DESC'):
        out.append(f"- {r['created_at'][5:16].replace('T', ' ')}【{r['category']}】{r['title']} → **{STATUS.get(r['status'], r['status'])}**"
                   + (f"（{r['decided_by']} {r['decided_at'][5:16].replace('T', ' ')}）" if r['decided_at'] else ''))
        if r['note']:
            out.append(f"  - 意見：{r['note']}")
    rules = [r[0] for r in con.execute('SELECT category FROM review_rules WHERE auto_approve=1')]
    out += ['', '## 已設為免審的類別', '', '- ' + ('、'.join(rules) if rules else '（還沒有）')]
    if c.DATA != c.ROOT / 'data':          # 測試資料不覆蓋正式紀錄
        return
    try:
        (c.ROOT / '文件' / '店長決策紀錄.md').write_text(chr(10).join(out) + chr(10), encoding='utf-8')
    except OSError:
        pass
