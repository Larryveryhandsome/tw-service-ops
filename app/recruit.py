"""夥伴招募六關＋良民證（招募六關：履歷、面試、資格與合法背景查核、實作、訓練、試派案；良民證可選代辦並用 LINE Pay 付費）。

關卡（interviews.status）：screening 履歷審核 → invited/scheduled 面試 → check 背景查核（良民證）→ trial 實作 → training 訓練 → probation 試派案（開始可派工）→ hired 正式夥伴
良民證三種方式（夥伴在 LINE 點）：
- 已經有／自己去辦 → 在 LINE 傳照片 → 派單管理群確認
- 請示範服務店代辦 → 規費（settings police_fee，預設 100 元）LINE Pay（正式開通後）或現場／匯款 → 管理群約時間收身分證正本、委託書簽名 → 送件 → 拿到良民證
良民證照片只有管理員看得到（worker_files kind=police）；不收身分證字號、不存證件照。
"""
import io
import json
import hmac
import hashlib
import core as c
import integrations as ix

STAGES = [('screening', '履歷審核'), ('invited', '約面試'), ('scheduled', '面試'), ('check', '背景查核（良民證）'), ('trial', '實作'), ('training', '訓練'),
          ('probation', '試派案'), ('hired', '正式夥伴')]
NEXT = {'check': 'trial', 'trial': 'training', 'training': 'probation', 'probation': 'hired'}
TRAINING = {'服務人員': ['系統操作：接單、今天的工作、打卡、拍服務前／完成照片、補登、請假', '示範服務店標準與範圍', '客戶禮儀與進出門', '個資與照片保密（不拍客戶臉、不外傳）'],
            '家庭管家': ['財物保密', '鑰匙管理與交接紀錄', '代購核銷（單筆上限）', '工作回報', '廠商協調'],
            '整理師': ['系統操作：接單、今天的工作、打卡、拍整理前／後照片', '整理收納的流程與標準', '客戶物品與隱私保密'],
            '陪玩員': ['兒童安全', '急救', '活動設計（依年齡）', '監護界線（家長在場、不單獨托育）', '個資與兒童影像（未經同意不拍不傳）']}
POLICE_INFO = ('良民證（警察刑事紀錄證明）怎麼辦：\n'
               '・自己辦：到各縣市警察局或分局臨櫃，帶身分證正本（沒帶可用戶口名簿正本＋有照片的證件），規費 100 元，同一次多辦一張加 20 元。\n'
               '・請人代辦：代辦人要帶申請人的身分證正本、代辦人自己的身分證、申請人簽名或蓋章的委託書（申請書內附委託書欄位）、規費。\n'
               '・辦好後在這裡傳照片給示範服務店就可以了（需要 3 個月內的）。\n'
               '（以上為一般規定，實際以警察局公告為準）')

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS worker_files(id TEXT PRIMARY KEY, worker_id TEXT, ref TEXT DEFAULT '', kind TEXT, mime TEXT DEFAULT 'image/jpeg', "
                "data BLOB, note TEXT DEFAULT '', created_at TEXT, by TEXT DEFAULT '')")
    import interviews; interviews.ensure(con)
    if 'police' not in {r[1] for r in con.execute('PRAGMA table_info(interviews)')}:
        con.execute("ALTER TABLE interviews ADD COLUMN police TEXT DEFAULT '{}'")
    if 'role' not in {r[1] for r in con.execute('PRAGMA table_info(interviews)')}:
        con.execute("ALTER TABLE interviews ADD COLUMN role TEXT DEFAULT '服務人員'")

OPEN = ('screening', 'invited', 'proposed', 'scheduled', 'need_time', 'hold', 'check', 'trial', 'training')

def open_stage(con, wid):
    """這位還在招募流程（試派案以前）→ 回傳目前關卡；沒有就回空字串。"""
    ensure(con)
    r = con.execute(f"SELECT status FROM interviews WHERE worker_id=? AND status IN ({','.join('?' * len(OPEN))}) ORDER BY created_at DESC LIMIT 1", (wid, *OPEN)).fetchone()
    return r[0] if r else ''

def stage_name(s):
    return dict(STAGES).get(s, {'rejected': '不錄取', 'reject_pending': '婉拒（等文案）', 'hold': '再考慮', 'no_show': '沒出現', 'need_time': '想約其他時間', 'proposed': '等主管挑面試時間'}.get(s, s))

# ───────── 檔案（作品集、良民證）─────────
class Jpeg(bytes):
    """已經用 shrink 壓好的照片（save_file 不再壓一次）。"""

def shrink(raw):
    """圖片一律重新壓成 JPEG（最長邊 1600、去掉 EXIF 定位）；只收圖片。純運算、不碰資料庫：
    ——以前在全域鎖裡壓，應徵者上傳時整個系統跟著卡，現在 server 先在鎖外壓好再進鎖。"""
    from PIL import Image
    import warnings
    if len(raw) > 6 * 1024 * 1024:
        raise ValueError('照片太大，請重新拍或選小一點的')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert('RGB'); im.thumbnail((1600, 1600))
            out = io.BytesIO(); im.save(out, 'JPEG', quality=82)
    return Jpeg(out.getvalue())

def save_file(con, kind, raw, worker_id=None, ref='', by='', note=''):
    ensure(con)
    data = raw if isinstance(raw, Jpeg) else shrink(raw)
    fid = c.uid('wf')
    con.execute('INSERT INTO worker_files(id,worker_id,ref,kind,data,note,created_at,by) VALUES(?,?,?,?,?,?,?,?)', (fid, worker_id, ref, kind, data, note[:200], c.now(), by))
    return fid

def files(con, worker_id, kind=None):
    ensure(con)
    return c.rows(con, 'SELECT id,kind,note,created_at,by FROM worker_files WHERE worker_id=?' + (' AND kind=?' if kind else '') + ' ORDER BY created_at', (worker_id, kind) if kind else (worker_id,))

def file_get(con, fid, user):
    import accounts
    r = con.execute('SELECT * FROM worker_files WHERE id=?', (fid or '',)).fetchone()
    if not r:
        raise PermissionError('找不到檔案')
    if r['kind'] == 'police' and 'admin' not in accounts.CAN.get(user['role'], set()):
        raise PermissionError('良民證只有管理員看得到')
    return r['mime'], r['data']

# ───────── 關卡 ─────────
def _iv(con, iid):
    import interviews
    ensure(con)
    return interviews.get(con, iid)

def _pol(iv):
    try:
        return json.loads(iv.get('police') or '{}')
    except ValueError:
        return {}

def _set_pol(con, iid, **kw):
    iv = _iv(con, iid); p = _pol(iv); p.update(kw)
    con.execute('UPDATE interviews SET police=? WHERE id=?', (json.dumps(p, ensure_ascii=False), iid))
    return p

ROLES = ('服務人員', '陪玩員', '家庭管家', '整理師')

def needs_police(iv, con=None):
    """良民證：只有陪玩員；回答「家庭管家也要」（settings police_roles，逗號分隔；沒設＝陪玩）。"""
    roles = [r for r in ((ix.setting(con, 'police_roles') if con is not None else '') or '陪玩').split(',') if r.strip()]
    have = iv.get('role') or ''
    if con is not None and iv.get('worker_id'):          # 每日檢查：中控台約面試建的面試職缺一律「服務人員」→ 也看夥伴本人的職類（陪玩員、管家）
        w = con.execute('SELECT roles FROM workers WHERE id=?', (iv['worker_id'],)).fetchone()
        have += ' ' + ((w[0] if w else '') or '')
    return any(r.strip() in have for r in roles)

def needs_training(iv):
    """訓練只有沒經驗的需要（應徵表「是否具備相關工作經驗」答「無」，或年資「無相關經驗」；沒填完整應徵表的照樣要訓練）。"""
    try:
        app = json.loads(iv.get('application') or '{}')
    except ValueError:
        app = {}
    return not (app.get('has_exp') == '有' and app.get('years') not in (None, '', '無相關經驗'))

def needs_trial(con, iv):
    """實作關（管理群決定 recruit_trial：keep＝每位都要／exp＝有經驗的免／skip＝全部免）。"""
    v = ix.setting(con, 'recruit_trial') or 'keep'
    return v == 'keep' or (v == 'exp' and needs_training(iv))

def enter(con, iid, stage, by=''):
    """進到某一關：改狀態＋自動通知該做的人。良民證、訓練不需要的自動跳過（記在紀錄裡）。"""
    import line_bot as lb
    iv = _iv(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    skipped = []
    if stage == 'check' and not needs_police(iv, con):
        skipped.append('良民證（這個職缺不用）'); stage = 'trial'
    if stage == 'trial' and not needs_trial(con, iv):
        skipped.append('實作（管理群決定）'); stage = 'training'
    if stage == 'training' and not needs_training(iv):
        skipped.append('訓練（有相關經驗）'); stage = 'probation'
    if skipped:
        c.audit(con, 'recruit_skip', 'workers', w['id'], after={'skipped': skipped, 'by': by})
    con.execute("UPDATE interviews SET status=?,flags='{}' WHERE id=?", (stage, iid))
    c.audit(con, 'recruit_stage', 'workers', w['id'], after={'stage': stage, 'by': by})
    if stage == 'check':
        police_card(con, iid)
    elif stage == 'trial':
        lb.admin_notify(con, f"🧹 {w['name']} 進到「實作」：請安排試做一場", [('角色', iv.get('role') or '服務人員'), ('電話', w['phone'] or '—'),
                        ('怎麼做', '排一場跟資深夥伴一起的工作（或在辦公室實作），做完到中控台「夥伴面試」按「完成這關」')], f"/#interviews/{iid}", '#0A84FF', tag=f"{w['name']} 要安排實作 👇")
    elif stage == 'training':
        items = TRAINING.get(iv.get('role') or '服務人員', TRAINING['服務人員'])
        if iv['line_uid']:
            lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'recruit_training', '示範服務店夥伴訓練', lb._txt(
                f"{w['name']} 恭喜 🎉 下一關是訓練，經理會跟你約時間，內容：\n" + '\n'.join('・' + x for x in items)))
        lb.admin_notify(con, f"📚 {w['name']} 進到「訓練」", [('內容', '、'.join(items))], f"/#interviews/{iid}", '#0A84FF', tag=f"{w['name']} 要安排訓練 👇")
    elif stage == 'probation':                       # 試派案：開始可以派工（系統自動傳歡迎＋夥伴系統介紹）
        c.CTX.actor = (by or '系統') + '（試派案）'
        c.save(con, 'workers', {'id': w['id'], 'version': w['version'], 'status': 'active', 'notes': ((w['notes'] or '') + f'；試派案 {c.now()[:10]}')[:2000]})
        con.execute("UPDATE line_users SET onboard='applicant' WHERE worker_id=? AND COALESCE(onboard,'') IN ('','applicant','asked')", (w['id'],))      # 稽核 M1
        lb.admin_notify(con, f"🚀 {w['name']} 開始試派案（已可派工）", [('提醒', '先排簡單的、跟資深夥伴一起的工作；做得好到中控台「夥伴面試」按「轉正式」')], f"/#interviews/{iid}", '#2E7D32')
    elif stage == 'hired':
        con.execute('UPDATE interviews SET decided_at=?,result_by=? WHERE id=?', (c.now(), by, iid))
        if iv['line_uid']:
            lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'recruit_hired', '正式成為示範服務店夥伴', lb._txt(f"{w['name']} 恭喜你正式成為示範服務店夥伴 🎉 謝謝你，接下來一起把服務做好！"))
    return stage_name(stage) + (f"（跳過：{'、'.join(skipped)}）" if skipped else '')

def advance(con, iid, by):
    iv = _iv(con, iid)
    nxt = NEXT.get(iv['status'])
    if not nxt:
        raise ValueError(f"這一關（{stage_name(iv['status'])}）沒有下一關")
    if iv['status'] == 'check' and not _pol(iv).get('ok'):
        raise ValueError('良民證還沒確認通過')
    return '已進到：' + enter(con, iid, nxt, by)

# ───────── 良民證 ─────────
def fee(con):
    v = ix.setting(con, 'police_fee')
    try:
        return int(v) if v not in (None, '') else 100
    except ValueError:
        return 100

def sig(con, iid):
    import worker_portal
    k = worker_portal._key(con)
    return hmac.new(k if isinstance(k, bytes) else str(k).encode(), ('police:' + iid).encode(), hashlib.sha256).hexdigest()[:20]

def police_card(con, iid):
    import line_bot as lb
    iv = _iv(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    if not iv['line_uid']:
        lb.admin_notify(con, f"📄 {w['name']} 要繳良民證，但沒有 LINE 一對一，請打電話說明", [('電話', w['phone'] or '—')], f"/#interviews/{iid}", '#C47F00', tag='請打電話說明良民證 👇')
        return
    m = lb.flex('下一關：背景查核（良民證）', lb._bubble(lb._header('📄 下一關：背景查核', f"{w['name']}・良民證", '#0A84FF'),
        [lb._t('恭喜通過面試 🎉 示範服務店的夥伴會到客戶家裡服務，需要提供 3 個月內的良民證（警察刑事紀錄證明）。選一個方式：', wrap=True)],
        [lb._btn('我已經有／自己去辦，傳照片', data=f'a=pol_up&i={iid}', primary=True), lb._btn(f'請示範服務店代辦（{fee(con)} 元）' if fee(con) else '請示範服務店代辦（免費）', data=f'a=pol_agent&i={iid}'),
         lb._btn('良民證怎麼辦？', data=f'a=pol_info&i={iid}')], '照片只有示範服務店管理員看得到；不需要傳身分證。'))
    lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'police_ask', m[0]['altText'], m)

def police_photo(con, uid, raw):
    """夥伴在 LINE 傳良民證照片（police_wait）。"""
    import line_bot as lb
    iid = ix.setting(con, f'police_wait:{uid}')
    ix.set_setting(con, f'police_wait:{uid}', '')
    iv = _iv(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    fid = save_file(con, 'police', raw, w['id'], iid, w['name'], '夥伴在 LINE 上傳')
    _set_pol(con, iid, mode=_pol(iv).get('mode') or 'self', file=fid, uploaded_at=c.now())
    g = lb.admin_group(con)
    if g:
        m = lb.flex(f"📄 {w['name']} 傳了良民證，請確認", lb._bubble(lb._header('📄 良民證待確認', w['name'], '#0A84FF'),
            [lb._row('電話', w['phone'] or '—'), lb._t('照片只放在中控台（管理員才看得到），請打開確認：3 個月內、本人、沒有紀錄。', color=lb.SUB, size='xs', wrap=True, margin='md')],
            [lb._btn('打開看照片（中控台）', uri=f"{lb.BASE_URL}/#interviews/{iid}", primary=True), lb._btn('確認通過', data=f'a=pol_ok&i={iid}'), lb._btn('看不清楚，請他重傳', data=f'a=pol_redo&i={iid}')]))
        lb.queue(con, lb.WORK_ACCOUNT, g, 'police_check', m[0]['altText'], [lb.ping(con, f"{w['name']} 傳了良民證 👇")] + m)
    return '收到了，謝謝你 🙏 經理確認後會通知你下一關。'

def police_agent(con, iid):
    """請示範服務店代辦：規費用 LINE Pay（正式開通後）或現場／匯款；管理群約時間收證件。"""
    import line_bot as lb, linepay, booking
    iv = _iv(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    _set_pol(con, iid, mode='agent', asked_at=c.now())
    if iv['line_uid']:
        ix.set_setting(con, f"police_wait:{iv['line_uid']}", '')          # 稽核 L1：改代辦 → 之後傳的照片不會被當良民證
    st = linepay.status(con, 'demoshop')
    lp = st['enabled'] and st['mode'] == 'production' and fee(con) > 0
    co = booking.company(con, 'demoshop')
    lines = [f"好的，示範服務店幫你代辦 👍 費用 {fee(con)} 元。" if fee(con) else '好的，示範服務店幫你代辦 👍 費用由示範服務店負擔。',
             '代辦需要：①身分證正本（交給經理，辦完歸還）②在委託書簽名或蓋章（經理會準備）。經理會跟你約時間。']
    if not lp and fee(con):
        lines.append('費用可以交證件時現場付給經理' + (f"，或匯款：{co['bank']} 戶名 {co['holder']} 帳號 {co['account']}" if co.get('bank') else '') + '。')
    btns = [lb._btn(f'用 LINE Pay 付 {fee(con)} 元', uri=f"{lb.BASE_URL}/pay/police?i={iid}&s={sig(con, iid)}", primary=True)] if lp else []
    msg = lb.flex('良民證代辦', lb._bubble(lb._header('📄 良民證代辦', w['name'], '#0A84FF'), [lb._t('\n'.join(lines), wrap=True)], btns)) if btns else lb._txt('\n'.join(lines))
    g = lb.admin_group(con)
    if g:
        m = lb.flex(f"📄 {w['name']} 請示範服務店代辦良民證", lb._bubble(lb._header('📄 良民證代辦', w['name'], '#0A84FF'),
            [lb._row('電話', w['phone'] or '—'), lb._row('規費', f"{fee(con)} 元（{'LINE Pay' if lp else '現場付／匯款'}）"),
             lb._t('請約時間收身分證正本＋委託書簽名、收規費，送件後按「已送件」，拿到良民證拍照存進中控台後按「通過」。', color=lb.SUB, size='xs', wrap=True, margin='md')],
            [lb._btn('已收規費', data=f'a=pol_paid&i={iid}', primary=True), lb._btn('已送件', data=f'a=pol_sub&i={iid}'), lb._btn('拿到良民證・通過', data=f'a=pol_ok&i={iid}')]))
        lb.queue(con, lb.WORK_ACCOUNT, g, 'police_agent', m[0]['altText'], [lb.ping(con, f"{w['name']} 要代辦良民證，請約時間 👇")] + m)
    return msg

def police_mark(con, iid, what, by, how=''):
    """管理群／中控台：paid 已收規費、sub 已送件、ok 通過（進下一關：實作）、redo 請重傳。"""
    import line_bot as lb
    iv = _iv(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    if what == 'paid':
        _set_pol(con, iid, paid_at=c.now(), paid_by=by, paid_how=how or '現場／匯款'); return f"✅ 已記錄 {w['name']} 的良民證規費（{by}）"
    if what == 'sub':
        _set_pol(con, iid, submitted_at=c.now(), submitted_by=by); return f"✅ 已記錄送件（{by}）"
    if what == 'redo':
        if iv['line_uid']:
            ix.set_setting(con, f"police_wait:{iv['line_uid']}", iid)
            lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'police_redo', '良民證請重傳', lb._txt('良民證照片看不太清楚，麻煩再拍一張清楚的傳過來 🙏'))
        return f"已請 {w['name']} 重傳（{by}）"
    if what == 'ok':
        if iv['status'] != 'check':
            return f"{w['name']} 已經不在良民證這關了（{stage_name(iv['status'])}）"
        if not _pol(iv).get('file'):          # 稽核 M4：沒有良民證照片存檔不能通過
            return f"{w['name']} 還沒有良民證照片存檔，不能通過：請他在 LINE 傳照片，或代辦拿到後在中控台「夥伴招募」上傳"
        if iv['line_uid']:
            ix.set_setting(con, f"police_wait:{iv['line_uid']}", '')          # 稽核 L1
        _set_pol(con, iid, ok=c.now(), ok_by=by)
        enter(con, iid, 'trial', by)
        if iv['line_uid']:
            lb.queue(con, iv['line_acc'] or lb.WORK_ACCOUNT, iv['line_uid'], 'police_ok', '良民證確認通過', lb._txt(f"{w['name']} 良民證確認通過 👍 下一關是實作，經理會跟你約時間。"))
        return f"✅ {w['name']} 良民證通過，進到「實作」（{by}）"
    raise ValueError('動作不符')

def police_paid_linepay(con, iid):
    _set_pol(con, iid, paid_at=c.now(), paid_by='LINE Pay', paid_how='LINE Pay')
    import line_bot as lb
    iv = _iv(con, iid); w = c.get(con, 'workers', iv['worker_id'])
    lb.admin_notify(con, f"💚 {w['name']} 用 LINE Pay 付了良民證規費 {fee(con)} 元", [('接下來', '約時間收身分證正本＋委託書簽名、送件')], f"/#interviews/{iid}", '#2E7D32')
