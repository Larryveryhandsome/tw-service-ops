"""夥伴面試（建立夥伴面試系統）。

定的流程：加入 → 應徵（填表單）→ 管理層先看資料（派單管理群卡）→ OK：邀請電話面試／不 OK：婉拒（婉拒文案店長確認過才自動傳）。
官方 LINE 應徵 → 管理層看資料 OK → 系統請應徵者點選面試時間 → 派單管理群收到預約卡
→ 自動提醒：應徵者前一天 18:00、前 1 小時；派單管理群前 30 分鐘
→ 面試官在中控台「夥伴面試」填評分表 → 錄取（改可派工，系統自動傳歡迎＋系統介紹）／不錄取（暫停）／再考慮／沒出現（可再約）
→ 時間過了沒填結果、應徵者沒選時間：自動追（不靠人記）。
面試方式、時段、面試前能不能接單由店長決定（decisions：面試方式／面試時段／面試前接單）。
"""
import json
import datetime as dt
import core as c
import integrations as ix

ITEMS = [('ontime', '守時'), ('manner', '溝通禮貌'), ('exp', '服務經驗'), ('detail', '細心程度'), ('avail', '可排時間與交通'), ('fit', '對工作內容的理解')]
RESULT = {'hired': '面試通過', 'rejected': '不錄取', 'hold': '再考慮', 'no_show': '沒出現'}
APP_LABEL = [('role', '應徵職缺'), ('has_exp', '相關經驗'), ('years', '年資'), ('cert', '證照／訓練'), ('exp_desc', '過去工作經歷'), ('served', '服務過的客戶／場域'),
             ('portfolio_link', '作品集連結'), ('portfolio_text', '作品集說明'), ('ability', '能力與實際經驗'), ('feedback', '客戶回饋／推薦'), ('specialty', '特殊專長'),
             ('avail', '可做時段'), ('transport', '交通'), ('iv_time', '面試方便'),
             ('days', '每週可做'), ('start', '可以開始'), ('exp', '經驗'), ('exp_types', '做過'), ('abilities', '可以接受'), ('intro', '自我介紹')]
WD = '一二三四五六日'

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS interviews(id TEXT PRIMARY KEY, worker_id TEXT, line_uid TEXT DEFAULT '', line_acc TEXT DEFAULT 'demoshop', status TEXT DEFAULT 'invited', "
                "slot TEXT DEFAULT '', mode TEXT DEFAULT '', scores TEXT DEFAULT '{}', skills TEXT DEFAULT '', note TEXT DEFAULT '', want TEXT DEFAULT '', result_by TEXT DEFAULT '', "
                "flags TEXT DEFAULT '{}', created_at TEXT, decided_at TEXT DEFAULT '')")
    cols = {r[1] for r in con.execute('PRAGMA table_info(interviews)')}
    if 'application' not in cols:
        con.execute("ALTER TABLE interviews ADD COLUMN application TEXT DEFAULT '{}'")
    if 'opts' not in cols:                       # 應徵者選的面試時段（最多 3 個，主管挑一個）
        con.execute("ALTER TABLE interviews ADD COLUMN opts TEXT DEFAULT ''")

def required(con):
    """應徵者要面試錄取後才能接單（店長可改：面試前接單）。"""
    return ix.setting(con, 'interview_required', '1') != '0'

def mode(con):
    return ix.setting(con, 'interview_mode') or '電話面試（約 15 分鐘，示範服務店打給你）'      # 視訊改電話

def slots(con, now=None):
    """接下來 3 個面試日的時段（至少 3 小時後），預設平日 10:00／14:00／16:00。"""
    try:
        times = json.loads(ix.setting(con, 'interview_slots') or '["10:00","14:00","16:00"]')
    except ValueError:
        times = ['10:00', '14:00', '16:00']
    days = ix.setting(con, 'interview_days') or '12345'
    now = now or dt.datetime.now(c.TZ); out, used = [], set()
    for i in range(0, 21):
        day = now.date() + dt.timedelta(days=i)
        if str(day.isoweekday()) not in days:
            continue
        for t in times:
            when = dt.datetime.fromisoformat(f'{day.isoformat()}T{t}').replace(tzinfo=c.TZ)
            if when - now >= dt.timedelta(hours=3) and not slot_full(con, when):
                out.append(when); used.add(day)
        if len(used) >= 3:
            break
    return out[:12]

def slot_full(con, when, skip=''):
    """這個面試時段約滿了沒（預設每個時段 1 位，settings interview_slot_cap）。"""
    cap = int(ix.setting(con, 'interview_slot_cap') or 1)
    n = con.execute("SELECT COUNT(*) FROM interviews WHERE status='scheduled' AND substr(slot,1,16)=? AND id!=?", (when.strftime('%Y-%m-%dT%H:%M'), skip)).fetchone()[0]
    return n >= cap

def label(when):
    if isinstance(when, str):
        when = dt.datetime.fromisoformat(when)
    return f"{when.month}/{when.day}（{WD[when.weekday()]}）{when:%H:%M}"

def get(con, iid):
    ensure(con)
    r = con.execute('SELECT * FROM interviews WHERE id=?', (iid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆面試')
    return dict(r)

def _flag(con, iid, k):
    iv = get(con, iid); f = json.loads(iv['flags'] or '{}')
    if f.get(k):
        return False
    f[k] = c.now(); con.execute('UPDATE interviews SET flags=? WHERE id=?', (json.dumps(f), iid))
    return True

def invite(con, wid, uid='', acc='demoshop'):
    """新應徵者：建一筆面試、請他點選時間。同一位夥伴還有進行中的面試就沿用。"""
    ensure(con)
    old = con.execute("SELECT id FROM interviews WHERE worker_id=? AND status IN ('invited','scheduled','need_time')", (wid,)).fetchone()
    if old:
        send_invite(con, old[0]); return old[0]
    if not uid:
        r = con.execute('SELECT user_id,account FROM line_users WHERE worker_id=? AND is_friend=1 ORDER BY last_seen DESC LIMIT 1', (wid,)).fetchone()
        uid, acc = (r[0], r[1]) if r else ('', acc)
    iid = c.uid('iv')
    con.execute('INSERT INTO interviews(id,worker_id,line_uid,line_acc,status,mode,created_at) VALUES(?,?,?,?,?,?,?)', (iid, wid, uid, acc or 'demoshop', 'invited', mode(con), c.now()))
    send_invite(con, iid)
    return iid

def screen(con, wid, uid='', acc='demoshop', app=None):
    """新應徵：先給管理層看資料（派單管理群卡：OK 邀請電話面試／不適合婉拒），不直接約面試。"""
    ensure(con)
    iid = c.uid('iv')
    con.execute("INSERT INTO interviews(id,worker_id,line_uid,line_acc,status,mode,created_at,application) VALUES(?,?,?,?,'screening',?,?,?)",
                (iid, wid, uid, acc or 'demoshop', mode(con), c.now(), json.dumps(app or {}, ensure_ascii=False)))
    import recruit; recruit.ensure(con)
    con.execute('UPDATE interviews SET role=? WHERE id=?', ((app or {}).get('role') or '服務人員', iid))
    card_admin(con, iid, '🙋 新的應徵資料')
    return iid

def screen_ok(con, iid, by, slot='', self_pick=False):
    """資料審過 → 約電話面試。面試時間由我們填選 →
    slot＝我們選好的時間（直接排定、LINE 通知他）；self_pick＝舊做法，請他在 LINE 自己點時段；
    都沒有（管理群卡按 OK）＝先記「等我們排時間」，到中控台填時間。"""
    iv = get(con, iid)
    if iv['status'] != 'screening':
        return f"這位已經處理過了（{RESULT.get(iv['status'], iv['status'])}）"
    name = c.get(con, 'workers', iv['worker_id'])['name']
    if slot:
        t = dt.datetime.fromisoformat(str(slot)[:16]).replace(tzinfo=c.TZ)
        if t < dt.datetime.now(c.TZ):
            raise ValueError('這個時間已經過了，請挑別的時間')
        if slot_full(con, t, skip=iid):
            raise ValueError('這個時段已經約了別人，請挑別的時間')
        con.execute("UPDATE interviews SET status='invited',result_by=?,flags='{}' WHERE id=?", (by, iid))
        return confirm(con, iid, t.isoformat()[:16], by)
    if self_pick:
        con.execute("UPDATE interviews SET status='invited',result_by=?,flags='{}' WHERE id=?", (by, iid))
        send_invite(con, iid)
        return f"✅ 已邀請 {name} 電話面試（他會在 LINE 點選時間）"
    con.execute("UPDATE interviews SET status='need_time',want=?,result_by=?,flags='{}' WHERE id=?", (WE_PICK, by, iid))
    return f"✅ 已邀請 {name} 電話面試：請到中控台「更多 › 夥伴招募」點他，選好電話面試時間（系統會用 LINE 通知他）"

WE_PICK = '等我們排電話面試時間'

def reject(con, iid, by, note=''):
    """婉拒：暫停這位應徵者；婉拒文案店長確認過（settings interview_reject_msg）才自動傳，還沒確認先等著，確認後自動補傳。"""
    import line_bot as lb
    iv = get(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    msg = ix.setting(con, 'interview_reject_msg')
    status = 'rejected' if (msg or not iv['line_uid']) else 'reject_pending'
    con.execute('UPDATE interviews SET status=?,result_by=?,note=CASE WHEN ?!=\'\' THEN ? ELSE note END,decided_at=? WHERE id=?', (status, by, note, note[:1000], c.now(), iid))
    if w['status'] != 'paused':
        c.CTX.actor = by + '（應徵婉拒）'
        c.save(con, 'workers', {'id': w['id'], 'version': w['version'], 'status': 'paused', 'notes': ((w['notes'] or '') + f'；應徵婉拒 {c.now()[:10]}（{by}）')[:2000]})
        if w['status'] == 'active':               # 稽核：試派案起已可派工 → 手上的工作照暫停流程處理（另外派人／請管理群決定）
            import leave; leave.worker_unavailable(con, w['id'], '應徵婉拒')
    if iv['line_uid']:                        # 婉拒 → 拿掉夥伴選單，回到一般選單
        try:
            lb.api(con, f"/v2/bot/user/{iv['line_uid']}/richmenu", method='DELETE', acc=iv['line_acc'] or lb.WORK_ACCOUNT)
        except Exception:
            pass
    if msg and iv['line_uid']:
        lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'iv_reject', '應徵結果', lb._txt(msg.replace('{name}', w['name'])))
        return f"已婉拒 {w['name']}（已傳婉拒訊息）"
    return f"已婉拒 {w['name']}" + ('（婉拒文案店長還沒確認，確認後系統會自動傳給他）' if iv['line_uid'] else '（他沒有 LINE，請自己通知）')

def undo_reject(con, iid, by):
    """按錯婉拒 → 改回「待看資料」（夥伴改回待審、掛回夥伴選單）。已經傳出去的婉拒訊息收不回，要不要跟他說明由管理層決定。"""
    import line_bot as lb
    iv = get(con, iid)
    if iv['status'] not in ('rejected', 'reject_pending'):
        raise ValueError('這位不是婉拒狀態，不用改回')
    w = c.get(con, 'workers', iv['worker_id'])
    con.execute("UPDATE interviews SET status='screening',result_by='',decided_at=NULL WHERE id=?", (iid,))
    if w['status'] == 'paused':
        c.CTX.actor = by + '（改回：婉拒按錯）'
        c.save(con, 'workers', {'id': w['id'], 'version': w['version'], 'status': 'review', 'notes': ((w['notes'] or '') + f'；婉拒改回 {c.now()[:10]}（{by}）')[:2000]})
    if iv['line_uid']:
        lb.worker_menu_link(con, iv['line_uid'], iv['line_acc'] or lb.WORK_ACCOUNT)
    c.audit(con, 'interview_undo_reject', 'interviews', iid, after={'by': by})
    sent = iv['status'] == 'rejected' and iv['line_uid'] and con.execute(
        "SELECT 1 FROM line_outbox WHERE kind='iv_reject' AND to_id=? AND status='sent'", (iv['line_uid'],)).fetchone()
    return f"已改回「待看資料」：{w['name']}" + ('。⚠️ 婉拒訊息已經傳給他了（收不回），要通過的話建議打電話或在官方 LINE 跟他說明' if sent else '')

def flush_rejects(con):
    """店長核准婉拒文案後：補傳給先前等著的應徵者。"""
    import line_bot as lb
    msg = ix.setting(con, 'interview_reject_msg')
    n = 0
    for iv in c.rows(con, "SELECT i.*, w.name FROM interviews i JOIN workers w ON w.id=i.worker_id WHERE i.status='reject_pending'") if msg else []:
        lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'iv_reject', '應徵結果', lb._txt(msg.replace('{name}', iv['name'])))
        con.execute("UPDATE interviews SET status='rejected' WHERE id=?", (iv['id'],)); n += 1
    return n

def reinvite(con, iid, who):
    """請應徵者重新選面試時間（LINE「請他重選」、中控台面試頁共用）。"""
    iv = get(con, iid)
    if iv['status'] not in ('invited', 'proposed', 'scheduled', 'need_time', 'no_show'):
        raise ValueError('他已經過了約面試這一關，不能再請他重選')
    con.execute("UPDATE interviews SET status='invited',slot='',opts='',flags='{}' WHERE id=?", (iid,)); send_invite(con, iid)
    return f'已請他重新選面試時間（{who}）' + ('' if iv['line_uid'] else '（他沒有 LINE，系統已請管理群打電話約）')

def send_invite(con, iid, again=False):
    import line_bot as lb
    iv = get(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    if not again:
        ix.set_setting(con, f'iv_invited:{iid}', c.now())      # 稽核：24／48 小時從這次邀請算（以前從應徵時間算，按 OK 隔一分鐘就追）
    if not iv['line_uid']:
        lb.admin_notify(con, f"📞 {w['name']} 應徵了但沒有 LINE 一對一，請打電話約面試", [('電話', w['phone'] or '—'), ('處理', '約好時間後在中控台「夥伴面試」填時間')], f'/#interviews/{iid}', '#C47F00')
        return
    items = [{'type': 'action', 'action': {'type': 'postback', 'label': label(s)[:20], 'data': f"a=iv_pick&i={iid}&t={s:%Y-%m-%dT%H:%M}", 'displayText': f'面試時間：{label(s)}'}} for s in slots(con)]
    items.append({'type': 'action', 'action': {'type': 'postback', 'label': '這些時間都不行', 'data': f'a=iv_other&i={iid}', 'displayText': '這些時間都不行'}})
    ss = slots(con)[:11]               # 檢查：改卡片按鈕（快速回覆被後面的訊息蓋掉就按不到）；快速回覆照樣附上
    bs = [lb._btn(label(s)[:20], data=f"a=iv_pick&i={iid}&t={s:%Y-%m-%dT%H:%M}", primary=k == 0) for k, s in enumerate(ss)] + [lb._btn('這些時間都不行', data=f'a=iv_other&i={iid}')]
    msg = lb.flex('請選面試時間', lb._bubble(lb._header('面試邀請 😊', f'請選 {NEED} 個方便的時段'),
                  [lb._t(f"{w['name']} 你好，謝謝你應徵示範服務店 😊\n我們想先跟你聊聊：{iv['mode']}。\n請點下面選 {NEED} 個方便的時段，主管會挑一個跟你確認 👇", wrap=True)], bs))
    msg[0]['quickReply'] = {'items': items[:13]}
    lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'iv_invite', '請選面試時間', msg)

NEED = 3                                         # 應徵者選 3 個時段，主管挑一個

def opts(iv):
    try:
        return json.loads(iv.get('opts') or '[]')
    except ValueError:
        return []

def pick(con, iid, when, who=''):
    """應徵者點面試時段：記下來（最多 3 個）；選滿就送管理群挑。回傳給他看的一句話（含「就這幾個」＝還可以再選）。"""
    iv = get(con, iid)
    if iv['status'] == 'proposed':
        return '你選的時段已經送出了，主管挑好會在這裡通知你 🙏'
    if iv['status'] == 'scheduled' and iv['slot']:
        return f"面試時間已經約好了：{label(iv['slot'])}。要改時間直接在這裡留言 🙏"
    if iv['status'] not in ('invited', 'need_time', 'no_show'):
        return '這次面試已經有結果了，有問題請直接留言 🙏'
    t = dt.datetime.fromisoformat(when).replace(tzinfo=c.TZ)
    if t < dt.datetime.now(c.TZ):
        send_invite(con, iid, again=True)
        return '這個時間已經過了，請從新的時間裡選 🙏'
    if slot_full(con, t, skip=iid):
        send_invite(con, iid, again=True)
        return '這個時段剛好被約走了，請從新的時間裡再選 🙏'
    _now_ = dt.datetime.now(c.TZ).isoformat()
    ops = [x for x in opts(iv) if x > _now_]          # 每日檢查：過去的舊時段不算
    if t.isoformat() not in ops:
        ops.append(t.isoformat())
    con.execute("UPDATE interviews SET opts=? WHERE id=?", (json.dumps(ops[:NEED]), iid))
    if len(ops) >= NEED:
        return propose(con, iid)
    return f"已記下第 {len(ops)} 個：{label(t)} ✅\n請從剛剛的卡片再選 {NEED - len(ops)} 個時段；只有這幾個可以，就按下面「就這幾個」。"

def propose(con, iid):
    """選好的時段送管理群挑一個（選滿 3 個、按「就這幾個」、或選了 6 小時沒選完）。"""
    iv = get(con, iid); ops = opts(iv)
    if not ops:
        return '請先點卡片上的時間選一個 🙏'
    if iv['status'] not in ('invited', 'need_time', 'no_show'):
        return '你選的時段已經送出了，主管挑好會在這裡通知你 🙏'
    con.execute("UPDATE interviews SET status='proposed',flags='{}' WHERE id=?", (iid,))
    card_admin(con, iid, '📅 應徵者選了面試時段，請挑一個')
    return f"收到你選的時段 ✅\n" + '\n'.join('・' + label(x) for x in ops) + '\n主管挑好會在這裡通知你面試時間 🙏'

def confirm(con, iid, when, by=''):
    """主管挑時間（管理群卡「選這個時間」、中控台填時間）→ 排定，LINE 通知應徵者。"""
    import line_bot as lb
    iv = get(con, iid)
    if iv['status'] not in ('invited', 'proposed', 'need_time', 'no_show', 'scheduled'):
        return '這位已經過了約面試這一關'
    t = dt.datetime.fromisoformat(str(when)[:16]).replace(tzinfo=c.TZ)
    if t < dt.datetime.now(c.TZ):
        return '這個時間已經過了，請挑別的時間'
    if slot_full(con, t, skip=iid):
        return '這個時段已經約了別人，請挑別的時間'
    con.execute("UPDATE interviews SET status='scheduled',slot=?,opts='[]',flags='{}' WHERE id=?", (t.isoformat(), iid))
    c.audit(con, 'interview_confirm', 'interviews', iid, after={'slot': t.isoformat(), 'by': by})
    if iv['line_uid']:
        lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'iv_confirm', '面試時間確定',
                 lb._txt(f"✅ 面試時間確定：{label(t)}，{iv['mode']}。\n到時候見 🙏 要改時間直接在這裡留言。"))
    card_admin(con, iid, '📅 夥伴面試預約')
    return f"已排定面試 {label(t)}（{by}）" + ('，已在 LINE 通知他' if iv['line_uid'] else '（他沒有 LINE，請打電話告訴他）')

def want_time(con, iid, text):
    con.execute("UPDATE interviews SET status='need_time',want=? WHERE id=?", (text[:200], iid))
    card_admin(con, iid, '🕒 應徵者想約其他時間')
    return '收到！示範服務店會跟你聯絡約時間 🙏'

def card_admin(con, iid, kicker):
    import line_bot as lb
    iv = get(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    g = lb.admin_group(con)
    if not g:
        return
    app = json.loads(iv.get('application') or '{}')
    if iv['status'] == 'screening':
        import apply_form
        full = apply_form.complete(app)
        rows = [lb._row('名字', w['name']), lb._row('電話', w['phone'] or '—'), lb._row('應徵帳號', __import__('brands').name(iv['line_acc'] or lb.WORK_ACCOUNT))] + [lb._row(t, v) for t, v in apply_form.short_rows(app)]      # 
        if not apply_form.short_rows(app):
            rows += [lb._row('地區', (w['regions'] or '—')[:60]), lb._row('想做', (w['skills'] or '—')[:60])]
        if not full:
            rows.append(lb._t('⚠️ 資料不完整（沒填完整版應徵表），建議先按「請他補填完整應徵表」', color='#C62828', size='xs', wrap=True, margin='md'))
        btns = ([] if full else [lb._btn('請他補填完整應徵表', data=f'a=iv_more&i={iid}', primary=True)]) + \
               [lb._btn('OK，邀請電話面試', data=f'a=iv_ok&i={iid}', primary=full), lb._btn('不適合，婉拒', data=f'a=iv_rej&i={iid}'),
                lb._btn('看完整資料（中控台）', uri=f"{lb.BASE_URL}/#interviews/{iid}")]
        note = '看過資料：OK 就按「邀請電話面試」，再到中控台選電話面試時間（系統會 LINE 通知他）；不適合按「婉拒」，會再問一次才傳婉拒訊息。'
        tag = '有新的應徵資料，請看一下 👇'
    else:
        ops = opts(iv) if iv['status'] == 'proposed' else []; note = ''
        rows = [lb._row('名字', w['name']), lb._row('電話', w['phone'] or '—'), lb._row('應徵帳號', __import__('brands').name(iv['line_acc'] or lb.WORK_ACCOUNT)),
                lb._row('時間', label(iv['slot']) if iv['slot'] else ('、'.join(label(x) for x in ops) + '（請挑一個）' if ops else '想約：' + iv['want'] if iv['want'] else '還沒選')),
                lb._row('方式', iv['mode']), lb._row('區域', (w['regions'] or '—')[:40]), lb._row('會做', (w['skills'] or '—')[:40]), lb._row('可排', (w['availability'] or '—')[:30]),
                lb._row('面試方便', str(app.get('iv_time') or '—')[:40])]
        btns = [lb._btn('填面試結果（評分表）', uri=f"{lb.BASE_URL}/#interviews/{iid}", primary=True)]
        if ops:                                   # 應徵者選了時段 → 主管挑一個
            btns = [lb._btn(f'選 {label(x)}'[:20], data=f"a=iv_conf&i={iid}&t={x[:16]}", primary=k == 0) for k, x in enumerate(ops)] + [lb._btn('都不行，請他重選', data=f'a=iv_re&i={iid}')]
            note = '挑一個時間，系統會在 LINE 通知他；都不行就按「請他重選」。'
        if iv['status'] == 'scheduled':
            btns += [lb._btn('沒出現', data=f'a=iv_ns&i={iid}'), lb._btn('請他重選時間', data=f'a=iv_re&i={iid}')]
        note = note or '面試完請填評分表：錄取會自動改成可派工、傳歡迎和系統介紹給他。'
        tag = f"{kicker.split(' ', 1)[-1]}：{w['name']} 👇"
    if iv['line_uid'] and not con.execute('SELECT 1 FROM line_users WHERE account=? AND user_id=? AND is_friend=1', (iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'])).fetchone():
        rows.append(lb._t('⚠️ 他已經封鎖或刪除官方 LINE，系統訊息收不到，請直接打電話', color='#C62828', size='xs', wrap=True, margin='md'))      # 每日檢查 
    m = lb.flex(f"{kicker}：{w['name']}", lb._bubble(lb._header(kicker, w['name'], '#0A84FF'), rows, btns, note))
    lb.queue(con, lb.WORK_ACCOUNT, g, 'iv_admin', m[0]['altText'], [lb.ping(con, tag)] + m)

def ask_full_app(con, iid, by):
    """應徵資料不夠 → 傳完整版應徵表連結給他（姓名、電話、地區會先帶入）；補填送出後管理群會再收到一張「補填了完整應徵資料」。"""
    import line_bot as lb, worker_portal
    iv = get(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    uid, acc = iv['line_uid'], iv['line_acc'] or lb.WORK_ACCOUNT
    if not uid:
        r = con.execute('SELECT user_id,account FROM line_users WHERE worker_id=? AND is_friend=1 ORDER BY last_seen DESC LIMIT 1', (w['id'],)).fetchone()
        uid, acc = (r[0], r[1]) if r else ('', acc)
    if not uid:
        return f"{w['name']} 沒有綁官方 LINE，請打電話請他到官方 LINE 按「我要應徵」填表（電話 {w['phone'] or '—'}）"
    if not _flag(con, iid, 'more'):          # 第一次才傳（_flag 第一次回 True）
        return f"已經請 {w['name']} 補填了，他送出後這裡會收到通知"
    url = worker_portal.onboard_token(con, acc, uid, 'apply')
    m = lb.flex('請填完整的應徵資料 🙏', lb._bubble(lb._header('示範服務店夥伴應徵', '請填完整的應徵資料 🙏', '#0A84FF'),
        [lb._t(f"{w['name']} 你好！謝謝你報名示範服務店夥伴。為了安排面試，想請你花幾分鐘填完整的應徵表（大部分用點的；姓名、電話、地區已經幫你帶入）。", wrap=True)],
        [lb._btn('填應徵表', uri=url, primary=True)], '送出後示範服務店看過資料，會在這裡請你選電話面試的時間。'))
    lb.queue(con, acc, uid, 'iv_more', m[0]['altText'], m)
    c.audit(con, 'iv_ask_more', 'interviews', iid, after={'by': by})
    return f"✅ 已傳完整應徵表給 {w['name']}，他送出後這裡會收到「補填了完整應徵資料」"

def decide(con, iid, result, by, scores=None, skills='', note=''):
    """面試結果。錄取 → 可派工（line_bot.on_worker_approved 自動傳歡迎＋系統介紹）；不錄取 → 暫停；沒出現 → 可以再約。"""
    import line_bot as lb
    iv = get(con, iid)
    if result not in RESULT:
        raise ValueError('結果不符')
    if iv['status'] in ('check', 'trial', 'training', 'probation', 'hired', 'rejected', 'reject_pending'):        # 稽核：舊卡片／連按不能讓關卡倒退
        raise ValueError('面試結果已經定了（已進到後面的關卡），不能再改')
    w = c.get(con, 'workers', iv['worker_id'])
    con.execute('UPDATE interviews SET status=?,scores=?,skills=?,note=?,result_by=?,decided_at=? WHERE id=?',
                (result, json.dumps(scores or {}, ensure_ascii=False), skills[:200], note[:1000], by, c.now(), iid))
    c.CTX.actor = by + '（面試結果）'
    nxt = ''
    if result == 'hired':                       # 面試通過 → 良民證（只有陪玩員）→ 實作 → 訓練（只有沒經驗的）→ 試派案（recruit.py）
        if skills:
            c.save(con, 'workers', {'id': w['id'], 'version': w['version'], 'skills': skills})
        import recruit
        recruit.ensure(con); nxt = recruit.enter(con, iid, 'check', by)
    elif result == 'rejected':
        reject(con, iid, by, note)                                    # 婉拒文案店長確認過才自動傳
    elif result == 'no_show' and iv['line_uid']:
        con.execute("UPDATE interviews SET status='invited',slot='',opts='[]',flags='{}' WHERE id=?", (iid,))
        send_invite(con, iid)
    avg = round(sum(int(v) for v in (scores or {}).values()) / len(scores), 1) if scores else None
    lb.admin_notify(con, f"📝 面試結果：{w['name']}・{RESULT[result]}", [('面試官', by), ('平均分數', f'{avg} / 5' if avg else '—'), ('備註', note[:80] or '—'),
                    ('接下來', {'hired': f'下一關：{nxt}', 'rejected': '已暫停（不會派工）', 'hold': '先保留，之後再決定', 'no_show': '已請他重新選時間'}[result])],
                    '/#interviews', '#2E7D32' if result == 'hired' else '#8E8E93')
    return RESULT[result]

def stuck(con, now=None):
    """招募卡住的（以前面試之後的關卡都沒人追）：[(名字, 關卡, 卡多久, 要誰做什麼)]。"""
    now = now or dt.datetime.now(c.TZ); out = []
    def since(iv):
        r = con.execute("SELECT MAX(at) FROM audit WHERE action='recruit_stage' AND entity_id=?", (iv['worker_id'],)).fetchone()[0]
        t = r or iv.get('decided_at') or iv['created_at']
        try:
            d = dt.datetime.fromisoformat(t.replace(' ', 'T'))
        except ValueError:
            return 0
        return (now - (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc))).days
    for iv in c.rows(con, "SELECT i.*,w.name FROM interviews i JOIN workers w ON w.id=i.worker_id WHERE i.status IN ('invited','proposed','need_time','scheduled','hold','check','trial','training','reject_pending')"):
        d = since(iv)
        if iv['status'] == 'reject_pending':          # 稽核：婉拒文案還沒定 → 婉拒訊息傳不出去
            out.append((iv['name'], '婉拒等文案', d, '婉拒文案還沒核准：到審核卡核准「應徵婉拒文案」後會自動補傳'))
            continue
        if iv['status'] == 'invited':
            inv = ix.setting(con, f"iv_invited:{iv['id']}") or iv['created_at']
            dd = (now - dt.datetime.fromisoformat(inv)).days
            if dd >= 2:
                out.append((iv['name'], '約面試', dd, '他一直沒選時間：打電話約，或在中控台婉拒'))
        elif iv['status'] == 'proposed' and d >= 1:
            out.append((iv['name'], '挑面試時間', d, '他選好時段了：到群組卡「選這個時間」或中控台挑一個'))
        elif iv['status'] == 'need_time' and d >= 1:
            out.append((iv['name'], '排面試時間' if iv.get('want') == WE_PICK else '想約其他時間', d, '到中控台「夥伴招募」點他，選電話面試時間' if iv.get('want') == WE_PICK else '打電話約好後在中控台填時間'))
        elif iv['status'] == 'scheduled' and iv['slot'] and dt.datetime.fromisoformat(iv['slot']) < now - dt.timedelta(hours=12):
            out.append((iv['name'], '面試完沒填結果', (now - dt.datetime.fromisoformat(iv['slot'])).days, '面試官到中控台填結果（錄取／婉拒／沒出現）'))
        elif iv['status'] == 'hold' and d >= 3:
            out.append((iv['name'], '再考慮', d, '決定錄取或婉拒'))
        elif iv['status'] == 'check' and d >= 2:
            p = json.loads(iv.get('police') or '{}')
            out.append((iv['name'], '良民證', d, '照片傳了：管理層確認通過' if p.get('file') else '代辦中：收身分證正本、送件' if p.get('mode') == 'agent' else '他還沒選怎麼辦：打電話問'))
        elif iv['status'] in ('trial', 'training') and d >= 3:
            out.append((iv['name'], '實作' if iv['status'] == 'trial' else '訓練', d, '安排好、做完到中控台按「完成這關」'))
    return out

def tick(con):
    """每分鐘：提醒應徵者／管理群、追沒選時間的、追沒填結果的。"""
    import line_bot as lb
    ensure(con)
    now = dt.datetime.now(c.TZ); n = 0
    if '10:30' <= now.strftime('%H:%M') < '21:00' and ix.setting(con, 'iv_stuck_day') != now.date().isoformat():
        ix.set_setting(con, 'iv_stuck_day', now.date().isoformat())
        st = stuck(con, now)
        if st:                              # 卡住的每天一張彙整（不洗版）
            lb.admin_notify(con, f'🙋 招募卡住的 {len(st)} 位', [(f'{nm}・{stage}（{d} 天）', todo) for nm, stage, d, todo in st[:12]], '/#interviews', '#C47F00'); n += 1
        for iv in c.rows(con, "SELECT * FROM interviews WHERE status='check' AND COALESCE(line_uid,'')!=''"):
            p = json.loads(iv.get('police') or '{}')
            if not p.get('file') and p.get('mode') != 'agent' and (now - dt.datetime.fromisoformat(iv['decided_at'] or iv['created_at'])).days >= 2 and _flag(con, iv['id'], 'police_nudge'):
                import recruit; recruit.police_card(con, iv['id']); n += 1      # 良民證 2 天沒動作：再傳一次說明（只一次）
    for iv in c.rows(con, "SELECT * FROM interviews WHERE status='screening' AND created_at<=?", ((now - dt.timedelta(hours=24)).isoformat(),)):
        if _flag(con, iv['id'], 'screen_chase'):                       # 應徵資料 24 小時沒人看 → 再標記一次
            card_admin(con, iv['id'], '🙋 應徵資料還沒看'); n += 1
    for iv in c.rows(con, "SELECT * FROM interviews WHERE status IN ('invited','scheduled')"):
        w = con.execute('SELECT name,phone FROM workers WHERE id=?', (iv['worker_id'],)).fetchone()
        if not w:
            continue
        if iv['status'] == 'invited':
            age = now - dt.datetime.fromisoformat(ix.setting(con, f"iv_invited:{iv['id']}") or iv['created_at'])
            if opts(iv) and age >= dt.timedelta(hours=6):          # 選了 1～2 個就沒再選 → 直接送管理群挑
                propose(con, iv['id']); n += 1; continue
            if age >= dt.timedelta(hours=24) and iv['line_uid'] and _flag(con, iv['id'], 're_invite'):
                send_invite(con, iv['id'], again=True); n += 1
            elif age >= dt.timedelta(hours=48) and _flag(con, iv['id'], 'call'):
                lb.admin_notify(con, f"📞 {w['name']} 兩天沒選面試時間，請打電話約", [('電話', w['phone'] or '—'), ('處理', '約好後在中控台「夥伴面試」填時間')], f"/#interviews/{iv['id']}", '#C47F00'); n += 1
            continue
        slot = dt.datetime.fromisoformat(iv['slot'])
        left = slot - now
        if iv['line_uid'] and now.strftime('%H:%M') >= '18:00' and slot.date() == now.date() + dt.timedelta(days=1) and _flag(con, iv['id'], 'eve'):
            lb.queue(con, iv['line_acc'], iv['line_uid'], 'iv_remind', '明天面試提醒', lb._txt(f"提醒你：明天 {label(slot)} 面試（{iv['mode']}）😊 時間不方便請直接留言。")); n += 1
        if iv['line_uid'] and dt.timedelta(0) < left <= dt.timedelta(hours=1) and _flag(con, iv['id'], 'h1'):
            lb.queue(con, iv['line_acc'], iv['line_uid'], 'iv_remind', '1 小時後面試', lb._txt(f"提醒你：1 小時後（{slot:%H:%M}）面試，{iv['mode']}。")); n += 1
        if dt.timedelta(0) < left <= dt.timedelta(minutes=30) and _flag(con, iv['id'], 'staff30'):
            lb.admin_notify(con, f"⏰ 30 分鐘後面試：{w['name']}", [('時間', label(slot)), ('電話', w['phone'] or '—'), ('方式', iv['mode'])], f"/#interviews/{iv['id']}", '#0A84FF',
                            tag=f"30 分鐘後電話面試：{w['name']}"); n += 1
        if left <= -dt.timedelta(hours=1) and _flag(con, iv['id'], 'result'):
            card_admin(con, iv['id'], '📝 請填面試結果'); n += 1
    return n

def state(con):
    ensure(con)
    import recruit, apply_form; recruit.ensure(con)
    rows = _state_rows(con)
    for x in rows:
        app = json.loads(x.get('application') or '{}')
        x['app_view'] = apply_form.view(app); x['app_complete'] = apply_form.complete(app)
    return rows

def _state_rows(con):
    return c.rows(con, "SELECT i.*, w.name, w.phone, w.regions, w.skills wskills, w.availability, w.notes wnotes, w.status wstatus, "
                       "(SELECT group_concat(f.id || ':' || f.kind) FROM worker_files f WHERE f.worker_id=i.worker_id) files FROM interviews i "
                       "JOIN workers w ON w.id=i.worker_id ORDER BY CASE i.status WHEN 'screening' THEN 0 WHEN 'proposed' THEN 1 WHEN 'scheduled' THEN 1 WHEN 'check' THEN 2 WHEN 'trial' THEN 3 WHEN 'training' THEN 4 WHEN 'probation' THEN 5 WHEN 'need_time' THEN 6 WHEN 'invited' THEN 7 WHEN 'hold' THEN 8 ELSE 9 END, i.slot, i.created_at DESC LIMIT 200")
