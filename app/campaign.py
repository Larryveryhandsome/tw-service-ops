"""年度保養詢問（「自動化詢問社區跟現有客戶要不要安排年度保養了」；社區她已經一個一個問過、在追蹤）。

- 對象（每個品牌各自發、只提自己的品牌）：
  ① 已綁官方 LINE、做過至少一次的現有客戶（社區客戶店長已經問過，這次不重複）
  ② 跟官方 LINE 互動過、還沒綁定的好友（不是夥伴、不是管理層）
  已經有年度保養報價或年度保養工單的跳過。
- 發之前：每個品牌一份送店長審核（人數、LINE 額度、卡片內容），核准才發；額度不夠不會發一半。
- 卡片按鈕：幫我估價（年度保養）／請專人聯絡我（管理群收通知去聯絡）／今年先不用（之後不再提醒）。
- 追蹤：7 天沒回、也沒填估價單的，再提醒一次（只一次）；發出後 14 天內每天 10:00 給派單管理群一份統計。
"""
import datetime as dt
import json
import core as c
import integrations as ix

FOLLOW_DAYS, REPORT_DAYS = 7, 14

def key_now():
    return f"deep{dt.datetime.now(c.TZ).year}"

def _community(con, cid, name):
    return any(w in (name or '') for w in ('社區', '管委會', '大樓')) or bool(con.execute(
        "SELECT 1 FROM sites WHERE customer_id=? AND service_type LIKE '%社區%'", (cid,)).fetchone())

def _has_deep(con, cid):
    return bool(cid and con.execute("SELECT 1 FROM orders WHERE customer_id=? AND (title LIKE '%年度保養%' OR title LIKE '%細清%') AND date>=? AND status NOT IN ('cancelled','archived')",
                                    (cid, dt.datetime.now(c.TZ).date().isoformat())).fetchone() or
                con.execute("SELECT 1 FROM quotes WHERE customer_id=? AND form LIKE '%年度保養%' AND status='draft'", (cid,)).fetchone())

def targets(con, brand):
    """[(user_id, customer_id 或 None, 稱呼)]：現有客戶（非社區、沒排年度保養）＋互動過還沒綁定的好友（不是夥伴、管理層）。"""
    import line_bot as lb
    staff = set(lb.staff_users(con)) | set(lb.bosses(con))
    out, seen = [], set()
    for r in c.rows(con, "SELECT l.user_id,l.customer_id,l.worker_id,COALESCE(l.is_staff,0) st,cu.name cname FROM line_users l LEFT JOIN customers cu ON cu.id=l.customer_id "
                         "WHERE l.account=? AND l.is_friend=1 ORDER BY l.last_seen DESC", (brand,)):
        if r['user_id'] in seen or r['worker_id'] or r['st'] or r['user_id'] in staff:
            continue
        seen.add(r['user_id'])
        if r['customer_id']:
            if (r['cname'] or '').startswith('（示範）') or _community(con, r['customer_id'], r['cname']) or _has_deep(con, r['customer_id']):
                continue
            if not con.execute("SELECT 1 FROM orders WHERE customer_id=? AND status IN ('completed','archived','confirmed')", (r['customer_id'],)).fetchone():
                continue
            out.append((r['user_id'], r['customer_id'], r['cname'] or ''))
        else:
            out.append((r['user_id'], None, ''))
    return out

def card(brand, name='', key='', again=False):
    import line_bot as lb, brands
    bn = brands.name(brand)
    hi = f"{name}您好 😊" if name else '您好 😊'
    body = ([lb._t(f"{hi}{'再跟您提醒一次：' if again else ''}{bn}年前年度保養開始預約了，越接近過年時段越滿，要不要幫您先安排？", wrap=True),
             lb._t('按「幫我估價」填一下需求、拍幾張照片，我們會盡快給您報價；想先聊聊也可以按「請專人聯絡我」。', size='sm', color=lb.SUB, wrap=True, margin='md')])
    return lb.flex(f'{bn}：年前年度保養開始預約了', lb._bubble(lb._header('年前年度保養開始預約 🧹', '越接近過年越滿', brand=brand), body,
        [lb._btn('幫我估價（年度保養）', uri=f"{lb.BASE_URL}/book?b={brand}", primary=True), lb._btn('請專人聯絡我', data=f'a=camp&k={key}&v=call'),
         lb._btn('今年先不用', data=f'a=camp&k={key}&v=no')], '按「今年先不用」就不會再提醒您。'))

def _state(con, key, brand):
    try:
        return json.loads(ix.setting(con, f'camp:{key}:{brand}') or '{}') or {}
    except ValueError:
        return {}

def _save(con, key, brand, st):
    ix.set_setting(con, f'camp:{key}:{brand}', json.dumps(st, ensure_ascii=False))

def prepare_broadcast(con, brand, by='負責人（店長提案，Claude 擬稿）'):
    """負責人 群發給官方 LINE 全部好友，一次全部（不是只發系統認得的人）。送管理層審核，附實際收到的卡片（可點預覽）。"""
    import reviews, brands, line_bot as lb, broadcast
    key = key_now(); n = broadcast.reach(con, brand)
    q = lb.quota(con, brand); left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else None
    partners = con.execute('SELECT COUNT(*) FROM line_users WHERE account=? AND worker_id IS NOT NULL AND is_friend=1', (brand,)).fetchone()[0]
    ok = left is None or (n or 0) <= left
    body = (f"要用【{brands.name(brand)}】官方 LINE 群發「年前年度保養開始預約」卡片給全部好友（LINE 統計約 {n or '?'} 位可送達，一次發完）。\n"
            + (f"⚠️ 示範服務店官方 LINE 的好友也包含夥伴（約 {partners} 位），他們也會收到這張卡。\n" if partners else '')
            + "發給誰：這個官方 LINE 的全部好友（現有客戶、社區、詢問過的人都會收到；群發看不到個別名單）。\n"
            "按鈕：幫我估價（年度保養）／請專人聯絡我（管理群收到通知去聯絡）／今年先不用。誰按了系統都記得，發出後 14 天內每天 10:00 派單管理群收到統計。\n"
            f"LINE 額度：本月已用 {q.get('used', '?')}／{q.get('limit', '?')} 則" + (f"，發完約剩 {left - (n or 0)} 則" if left is not None else '')
            + ('' if ok else '\n⚠️ 額度不夠，核准了也不會發（不會發一半）：請先到官方帳號後台升級／加購，或等下個月 1 號額度重算。')
            + "\n\n核准後系統會立刻群發；要改內容或對象請按「要修改」打字。")
    return reviews.submit(con, '年度保養詢問', f'{brands.name(brand)} 年前年度保養群發（全部好友 約 {n or "?"} 位）', body,
                          {'action': 'campaign_deep', 'brand': brand, 'key': key, 'mode': 'broadcast', 'preview': card(brand, '', key)}, by)

def prepare(con, brand, by='負責人（Claude 擬稿）'):
    """送店長審核（每個品牌一份）。"""
    import reviews, brands, line_bot as lb
    t = targets(con, brand); key = key_now()
    q = lb.quota(con, brand); left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else None
    cust, leads = sum(1 for x in t if x[1]), sum(1 for x in t if not x[1])
    names = [n for _, cid, n in t if cid]
    lead_names = [(con.execute('SELECT display_name FROM line_users WHERE user_id=? AND account=?', (u, brand)).fetchone() or [''])[0] or '（沒有名稱）' for u, cid, _ in t if not cid]
    who = ('\n\n發給誰：\n・現有客戶：' + ('、'.join(names[:80]) or '—') + (f' …等 {len(names)} 位' if len(names) > 80 else '')
           + '\n・還沒綁定的好友（LINE 名稱）：' + ('、'.join(lead_names[:80]) or '—') + (f' …等 {len(lead_names)} 位' if len(lead_names) > 80 else ''))
    body = (f"要用【{brands.name(brand)}】官方 LINE 一對一傳「年前年度保養開始預約」卡片給 {len(t)} 位：\n"
            f"・已綁定的現有客戶 {cust} 位（社區妳已經問過，這次不發；已經排年度保養的跳過）\n・互動過、還沒綁定的好友 {leads} 位（不含夥伴、管理層）\n\n"
            f"卡片內容：「（稱呼）您好 😊 {brands.name(brand)}年前年度保養開始預約了，越接近過年時段越滿，要不要幫您先安排？」\n"
            "按鈕：幫我估價（年度保養）／請專人聯絡我（管理群會收到通知去聯絡）／今年先不用（之後不再提醒）\n"
            f"7 天沒回、也沒填估價單的，會再提醒一次（只一次）。發出後 14 天內每天 10:00 派單管理群收到統計。\n\n"
            f"LINE 額度：本月已用 {q.get('used', '?')}／{q.get('limit', '?')} 則" + (f"，發完約剩 {left - len(t)} 則" if left is not None else '') + "（不夠的話不會發一半）。\n"
            "沒加官方 LINE 的老客戶這次傳不到，要的話請管理層電話詢問。" + who + "\n\n核准後系統會立刻發出；要改內容或對象請按「要修改」打字。")
    return reviews.submit(con, '年度保養詢問', f'{brands.name(brand)} 年前年度保養詢問（{len(t)} 位）', body, {'action': 'campaign_deep', 'brand': brand, 'key': key}, by)

def execute(con, payload, by=''):
    import line_bot as lb, brands
    brand, key = payload['brand'], payload.get('key') or key_now()
    if payload.get('mode') == 'broadcast':           # 群發給全部好友
        import broadcast
        if _state(con, key, brand).get('mode') == 'broadcast':          # 稽核 B3：同一次活動已經群發過 → 不重發（兩張審核卡各按一次也只發一次）
            lb.admin_notify(con, f'ℹ️ {brands.name(brand)} 年度保養已經群發過了，這次不再發', [('上次', _state(con, key, brand).get('at', '')[:16].replace('T', ' '))], '', '#8E8E93')
            return 0
        if lb.mode(con, brand) != 'on':                                   # 稽核 B2：官方 LINE 還在測試／關閉模式 → 不群發，改成正式後再送審
            ix.set_setting(con, WAIT.format(brand), json.dumps({'key': key, 'need': 0, 'at': c.now(), 'by': by, 'why': 'mode'}, ensure_ascii=False))
            lb.admin_notify(con, f'⚠️ {brands.name(brand)} 年度保養群發沒發出', [('原因', '這個官方 LINE 還在測試／關閉模式'), ('處理', '改成正式後系統會自動再送一次審核卡，按核准才會發')], '', '#C62828')
            return 0
        n, q = broadcast.reach(con, brand), lb.quota(con, brand)
        if n is None:                                                       # 稽核 B3：LINE 統計拿不到時不要跳過額度檢查（用系統知道的好友數估）
            n = con.execute('SELECT COUNT(*) FROM line_users WHERE account=? AND is_friend=1', (brand,)).fetchone()[0]
        left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else 10 ** 9
        if 'error' in q or (n and n > left):
            ix.set_setting(con, WAIT.format(brand), json.dumps({'key': key, 'need': n or 0, 'at': c.now(), 'by': by}, ensure_ascii=False))
            lb.admin_notify(con, f'⚠️ {brands.name(brand)} 年度保養群發沒發出', [('原因', q.get('error') or f'LINE 額度不夠：要 {n} 則，本月只剩 {left} 則'),
                            ('處理', '到官方帳號後台升級／加購；額度夠了系統會自動再送一次審核卡（最晚下個月 1 號），按核准才會發')], '', '#C62828')
            return 0
        m = card(brand, '', key)
        try:
            lb.api(con, '/v2/bot/message/validate/broadcast', {'messages': m}, acc=brand)
            lb.api(con, '/v2/bot/message/broadcast', {'messages': m}, acc=brand)
        except Exception as e:                                              # 稽核 B3：LINE 回錯誤 → 記等待，額度／狀況恢復後自動再送審
            ix.set_setting(con, WAIT.format(brand), json.dumps({'key': key, 'need': n or 0, 'at': c.now(), 'by': by, 'why': str(e)[:120]}, ensure_ascii=False))
            lb.admin_notify(con, f'⚠️ {brands.name(brand)} 年度保養群發沒發出', [('原因', f'LINE 回錯誤：{str(e)[:80]}'), ('處理', '系統每天 10:00 再看一次，狀況恢復會自動再送一次審核卡')], '', '#C62828')
            return 0
        con.execute('INSERT INTO line_outbox(created_at,account,to_id,kind,alt,payload,status,sent_at) VALUES(?,?,?,?,?,?,?,?)',
                    (c.now(), brand, 'broadcast', 'camp_deep_bc', m[0]['altText'], json.dumps(m, ensure_ascii=False), 'sent', c.now()))
        st = _state(con, key, brand); st.update(at=c.now(), by=by, mode='broadcast', reach=n or 0); st.setdefault('sent', {}); st.setdefault('answers', {})
        _save(con, key, brand, st)
        c.audit(con, 'campaign_deep_bc', 'line_users', brand, after={'reach': n, 'by': by})
        lb.admin_notify(con, f'🧹 {brands.name(brand)} 年度保養已群發', [('對象', f'全部好友（約 {n or "?"} 位）'), ('核准', by or '—'), ('之後', '有人按「請專人聯絡我」會在這裡通知；每天 10:00 統計')], '', '#2E7D32')
        return n or 1
    t = targets(con, brand)
    q = lb.quota(con, brand)
    left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else 10 ** 9
    if 'error' not in q and len(t) * 2 > left:            # 連提醒一起算，不夠就不發
        lb.admin_notify(con, f'⚠️ {brands.name(brand)} 年度保養詢問沒發出：LINE 額度不夠', [('要發', f'{len(t)} 位（含提醒最多 {len(t) * 2} 則）'), ('本月剩', f'{left} 則'),
                        ('處理', '到官方帳號後台加購訊息，或下個月 1 號額度重算後再核准一次')], '', '#C62828')
        return 0
    st = _state(con, key, brand); st.setdefault('sent', {}); st.setdefault('answers', {}); st['at'] = c.now(); st['by'] = by
    for uid, cid, name in t:
        if uid in st['sent']:
            continue
        m = card(brand, name, key)
        if lb.queue(con, brand, uid, 'camp_deep', m[0]['altText'], m) != 'queued':
            continue                                 # 稽核 B2：測試／關閉模式沒真的送出 → 不記成已傳（改成正式後再核准會補）
        st['sent'][uid] = {'cid': cid, 'name': name, 'at': c.now()}
    _save(con, key, brand, st)
    c.audit(con, 'campaign_deep', 'line_users', brand, after={'count': len(st['sent']), 'by': by})
    lb.admin_notify(con, f'🧹 {brands.name(brand)} 年度保養詢問已發出', [('對象', f"{len(st['sent'])} 位"), ('核准', by or '—'), ('之後', '有人按「請專人聯絡我」會在這裡通知；每天 10:00 統計')], '', '#2E7D32')
    return len(st['sent'])

def answer(con, acc, uid, lu, key, v):
    import line_bot as lb, brands
    st = _state(con, key, acc)
    if not st or (uid not in st.get('sent', {}) and st.get('mode') != 'broadcast'):
        return '收到，謝謝您 🙏 有需要隨時在這裡留言。'
    if uid not in st.get('sent', {}):                # 群發：誰按了就記下來（群發看不到名單）
        st['sent'][uid] = {'cid': (lu or {}).get('customer_id'), 'name': '', 'at': c.now()}
    if v not in ('call', 'no'):
        return ''
    st.setdefault('answers', {})[uid] = {'v': v, 'at': c.now()}
    _save(con, key, acc, st)
    if v == 'no':
        return '好的，謝謝您告訴我們 😊 有需要隨時在這裡找我們。'
    info = st['sent'][uid]
    cu = dict(con.execute('SELECT name,phone FROM customers WHERE id=?', (info.get('cid') or '',)).fetchone() or {})
    lb.admin_notify(con, f'🧹 {brands.name(acc)}：有人想安排年度保養，請聯絡', [('客戶', cu.get('name') or f"LINE：{(lu or {}).get('display_name') or '（還沒綁定）'}"),
                    ('電話', cu.get('phone') or '—（請在官方 LINE 聊天室問）'), ('處理', '請在官方 LINE 或電話跟他約時間、估價')], f"/#c/{info['cid']}" if info.get('cid') else '/#pairing', '#C47F00')
    return '好的 👍 專人會盡快跟您聯絡，也可以直接在這裡留下方便聯絡的時間和電話 🙏'

def _quoted(con, brand, uid, since):
    return bool(con.execute("SELECT 1 FROM quotes q JOIN inquiries i ON i.id=q.inquiry_id WHERE i.line_user_id=? AND q.account=? AND q.created_at>=?", (uid, brand, since)).fetchone())

WAIT = 'campwait:{}'             # 群發因額度不夠沒發：{'key','need','at','by','day'}

def quota_back(con):
    """群發核准了但額度不夠沒發 → 每天看一次額度，夠了（加購、下個月重算）自動再送一次審核卡（照樣要管理層核准才發，不自動發）。"""
    import line_bot as lb, broadcast
    today = dt.datetime.now(c.TZ).date().isoformat(); n = 0
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'campwait:%' AND COALESCE(value,'')!=''").fetchall():
        brand = k.split(':', 1)[1]
        try:
            w = json.loads(v)
        except ValueError:
            ix.set_setting(con, k, ''); continue
        if w.get('day') == today:
            continue
        w['day'] = today
        if _state(con, w.get('key') or key_now(), brand).get('mode') == 'broadcast':
            ix.set_setting(con, k, ''); continue                # 已經發出去了
        q = lb.quota(con, brand)
        left = (q.get('limit') or 0) - (q.get('used') or 0) if q.get('type') == 'limited' else 10 ** 9
        if lb.mode(con, brand) != 'on' or 'error' in q or (broadcast.reach(con, brand) or w.get('need') or 0) > left:
            ix.set_setting(con, k, json.dumps(w, ensure_ascii=False)); continue
        ix.set_setting(con, k, '')
        prepare_broadcast(con, brand, by='系統（LINE 額度夠了，再送一次審核）'); n += 1
    return n

def tick(con):
    """每天 10:00：7 天沒回的提醒一次；14 天內給派單管理群統計；額度不夠沒發的群發，額度夠了再送審。"""
    import line_bot as lb, brands
    now = dt.datetime.now(c.TZ)
    if not ('10:00' <= now.strftime('%H:%M') < '21:00'):
        return 0
    n = quota_back(con)
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'camp:%'").fetchall():
        _, key, brand = k.split(':', 2)
        try:
            st = json.loads(v or '{}')
        except ValueError:
            continue
        if not st.get('at') or st.get('day') == now.date().isoformat():
            continue
        st['day'] = now.date().isoformat()
        started = dt.datetime.fromisoformat(st['at'])
        for uid, info in st.get('sent', {}).items():
            if st.get('mode') == 'broadcast' or uid in st.get('answers', {}) or info.get('again') or _quoted(con, brand, uid, st['at']):
                continue
            if now - dt.datetime.fromisoformat(info['at']) >= dt.timedelta(days=FOLLOW_DAYS):
                m = card(brand, info.get('name', ''), key, again=True)
                lb.queue(con, brand, uid, 'camp_deep2', m[0]['altText'], m); info['again'] = c.now(); n += 1
        if now - started <= dt.timedelta(days=REPORT_DAYS):
            sent = st.get('sent', {}); ans = st.get('answers', {})
            call = sum(1 for x in ans.values() if x['v'] == 'call'); no = sum(1 for x in ans.values() if x['v'] == 'no')
            if st.get('mode') == 'broadcast':
                quoted = con.execute("SELECT COUNT(*) FROM quotes WHERE account=? AND created_at>=?", (brand, st['at'])).fetchone()[0]
                lb.admin_notify(con, f"🧹 {brands.name(brand)} 年度保養群發統計（第 {(now - started).days + 1} 天）",
                                [('群發', f"全部好友（約 {st.get('reach') or '?'} 位）"), ('群發後新的估價單', f'{quoted} 張'), ('請專人聯絡', f'{call} 位'), ('今年先不用', f'{no} 位')], '', '#2E7D32')
            else:
                quoted = sum(1 for u in sent if _quoted(con, brand, u, st['at']))
                lb.admin_notify(con, f"🧹 {brands.name(brand)} 年度保養詢問統計（第 {(now - started).days + 1} 天）",
                                [('已傳', f'{len(sent)} 位'), ('填了估價單', f'{quoted} 位'), ('請專人聯絡', f'{call} 位'), ('今年先不用', f'{no} 位'),
                                 ('還沒回', f'{len(sent) - len(ans) - quoted} 位（7 天沒回會自動再提醒一次）')], '', '#2E7D32')
        ix.set_setting(con, k, json.dumps(st, ensure_ascii=False))
    return n
