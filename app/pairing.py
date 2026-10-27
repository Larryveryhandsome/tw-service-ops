"""LINE 好友配對（「現在 LINE 有聯繫的，都幫我確認他是哪一個業主、夥伴；不確定的請店長配對；配對好就可以推播」）。

- 對象：三個品牌官方 LINE 有互動過（系統拿得到 userId）、還沒對應到客戶／夥伴、不是管理層的人。
- 系統先猜：他自己填的表單、自己按的綁定申請、對話裡留的電話、LINE 名稱跟客戶／夥伴資料一樣或相近。
- 店長（派單管理群的人也可以）在 LINE 一題一題點選：是哪一位／打字找／不是客戶／下次再問。配對好系統就傳歡迎卡，之後提醒、完成照片都會推播。
- 新加入的人一律走官方 LINE 的綁定流程（三個品牌一樣），不用再配對。
"""
import datetime as dt
import json
import re
import core as c
import integrations as ix

def push_on(con):
    """負責人 「配對的東西太亂了，全部請店長上去一個一個確認，不要再推撥」→ 配對、綁定預設不推到群組，
    改在中控台「LINE 配對」確認。settings pair_push=on 才恢復推播。"""
    return ix.setting(con, 'pair_push') == 'on'

def web_tip(con, lead=''):
    """舊卡片還在群組裡，有人按了 → 不再接著推下一張，告訴他到中控台。"""
    import line_bot as lb
    return lb._txt((lead + '\n' if lead else '') + f'其他的 LINE 配對改在中控台一個一個確認（群組不再推播）：{lb.BASE_URL}/#pairing')

SKIP_KEY = 'pair_skip'           # {"acc|uid": "no"（不是客戶也不是夥伴）| "YYYY-MM-DD"（先跳過到這天）}

def _norm(s):
    return re.sub(r'[^0-9a-z一-鿿]', '', (s or '').lower())

def _digits(s):
    return re.sub(r'\D', '', s or '')

def _skips(con):
    try:
        return json.loads(ix.setting(con, SKIP_KEY) or '{}')
    except ValueError:
        return {}

def silent(con, u):
    """只加好友、1 對 1 跟群組都沒講過話。"""
    return not (u['last_text'] or '').strip() and not con.execute("SELECT 1 FROM line_inbox WHERE user_id=? AND type='message' LIMIT 1", (u['user_id'],)).fetchone()

def todo(con):
    """還沒配對、也沒被跳過的 LINE 好友（最近有互動的先）。"""
    sk, today = _skips(con), dt.datetime.now(c.TZ).date().isoformat()
    out = []
    for u in c.rows(con, "SELECT * FROM line_users WHERE is_friend=1 AND customer_id IS NULL AND worker_id IS NULL AND COALESCE(is_staff,0)=0 "
                         "AND user_id NOT LIKE 'demo%' ORDER BY last_seen DESC"):
        s = sk.get(f"{u['account']}|{u['user_id']}")
        if s == 'no' or (s and s > today):
            continue
        if con.execute("SELECT 1 FROM inquiries WHERE line_user_id=? AND COALESCE(account,'demoshop')=? AND status NOT IN ('lost','spam','cancelled')", (u['user_id'], u['account'])).fetchone():
            continue                 # 正在估價／報價的新客人：簽約時系統會自動綁定，不用配對
        if silent(con, u) and not candidates(con, u, limit=1):
            continue                 # 「有沒有我們自行可以解決的」：只加好友、從沒講過話、也找不到證據 → 沒辦法配，不算待辦（講話後自動出現）
        out.append(u)
    return out

_EXP = {'at': 0, 'data': None}

def exports(con):
    """官方 LINE 歷史對話匯出檔（chat_threads）：{(品牌, LINE 名稱正規化): {'name','phones','text'}}。60 秒快取。"""
    import time
    if _EXP['data'] is not None and time.time() - _EXP['at'] < 60 and _EXP.get('db') == id(con):
        return _EXP['data']
    out = {}
    for t in c.rows(con, 'SELECT id,account,source FROM chat_threads'):
        m = re.match(r'^\d+_\d{8}_\d{8}_(.+)\.csv$', t['source'] or '')
        name = m.group(1) if m else ''
        if not _norm(name):
            continue
        txt = ' '.join(r[0] or '' for r in con.execute("SELECT content FROM chat_messages WHERE thread_id=? AND sender_type='User'", (t['id'],)))
        d = out.setdefault((t['account'] or 'demoshop', _norm(name)), {'name': name, 'phones': set(), 'text': ''})
        d['phones'] |= {_digits(p)[-8:] for p in re.findall(r'09\d{2}[\- ]?\d{3}[\- ]?\d{3}', txt)}
        d['text'] += ' ' + txt[:20000]
    _EXP.update(at=time.time(), data=out, db=id(con))
    return out

def _addr_key(a):
    """地址比對用：路／街＋號（去空白）。"""
    a = re.sub(r'\s', '', a or '')
    m = re.search(r'([一-鿿]{1,6}(?:路|街|大道)(?:[一-鿿0-9]{1,3}段)?(?:\d+巷)?(?:\d+弄)?\d+號)', a)
    return m.group(1) if m else ''

def extra_evidence(con, acc, uid, display_name):
    """配對的新證據：[(kind 'c'|'w', id, 分數, 理由)]。"""
    out = []
    import brands
    custs = [cu for cu in c.rows(con, "SELECT id,name,phone FROM customers") if brands.of_customer(cu) == acc]
    wks = c.rows(con, "SELECT id,name,phone FROM workers WHERE name NOT LIKE '（示範）%'")      # 夥伴三個官方 LINE 都可能
    def by_phones(phones, score, why):
        for p in phones:
            if len(p) < 8:
                continue
            out.extend(('c', cu['id'], score, why) for cu in custs if _digits(cu['phone']).endswith(p))
            out.extend(('w', w['id'], score, why) for w in wks if _digits(w['phone']).endswith(p))
    ex = exports(con).get((acc, _norm(display_name)))
    if ex and len(_norm(display_name)) >= 2:          # 歷史對話：LINE 名稱一樣的聊天室裡留過的電話、地址
        by_phones(ex['phones'], 88, f"歷史對話「{ex['name']}」裡留的電話")
        for s in c.rows(con, "SELECT s.customer_id,s.address FROM sites s WHERE COALESCE(s.address,'')!=''"):
            k = _addr_key(s['address'])
            if k and k in re.sub(r'\s', '', ex['text']) and any(cu['id'] == s['customer_id'] for cu in custs):
                out.append(('c', s['customer_id'], 82, f"歷史對話「{ex['name']}」裡提到地址 {k}"))
    for g in c.rows(con, "SELECT g.name,g.worker_id,COUNT(*) n FROM line_inbox i JOIN line_groups g ON g.group_id=i.group_id WHERE i.user_id=? AND g.role='worker' "
                         "AND g.worker_id IS NOT NULL GROUP BY g.group_id", (uid,)):
        out.append(('w', g['worker_id'], 80, f"在夥伴群組「{g['name']}」發言 {g['n']} 則"))
    gtxt = ' '.join(r[0] or '' for r in con.execute("SELECT text FROM line_inbox WHERE user_id=? AND COALESCE(group_id,'')!='' AND text LIKE '%09%' ORDER BY id DESC LIMIT 50", (uid,)))
    by_phones({_digits(p)[-8:] for p in re.findall(r'09\d{2}[\- ]?\d{3}[\- ]?\d{3}', gtxt)}, 88, '他在群組裡留的電話')
    mtxt = re.sub(r'\s', '', ' '.join(r[0] or '' for r in con.execute("SELECT text FROM line_inbox WHERE user_id=? AND account=? ORDER BY id DESC LIMIT 60", (uid, acc))))
    if mtxt:
        for s in c.rows(con, "SELECT customer_id,address FROM sites WHERE COALESCE(address,'')!=''"):
            k = _addr_key(s['address'])
            if k and k in mtxt and any(cu['id'] == s['customer_id'] for cu in custs):
                out.append(('c', s['customer_id'], 82, f'他在對話裡提到地址 {k}'))
    return out

def candidates(con, u, limit=3):
    """系統猜他是誰：[(kind 'c'|'w', id, 顯示, 分數, 理由)]，分數高的在前。"""
    import brands
    acc, uid = u['account'], u['user_id']
    got = {}
    def add(kind, rid, score, why):
        if rid and ((kind, rid) not in got or got[(kind, rid)][0] < score):
            got[(kind, rid)] = (score, why)
    custs = [cu for cu in c.rows(con, "SELECT id,name,line_name,phone,line_account,channel FROM customers") if brands.of_customer(cu) == acc]
    wks = c.rows(con, "SELECT id,name,phone FROM workers WHERE name NOT LIKE '（示範）%'")      # 夥伴三個官方 LINE 都可能
    def by_phone(p, score, why):
        p = _digits(p)
        if len(p) < 8:
            return
        for cu in custs:
            if _digits(cu['phone']).endswith(p[-8:]):
                add('c', cu['id'], score, why)
        for w in wks:
            if _digits(w['phone']).endswith(p[-8:]):
                add('w', w['id'], score, why)
    for i in c.rows(con, "SELECT phone,customer_id FROM inquiries WHERE line_user_id=? AND COALESCE(account,'demoshop')=?", (uid, acc)):
        add('c', i['customer_id'], 95, '他自己填的預約表單')
        by_phone(i['phone'], 90, '他填的電話跟這位一樣')
    for b in c.rows(con, "SELECT kind,ref_id FROM bind_requests WHERE user_id=? AND account=? AND status='open'", (uid, acc)):
        add('c' if b['kind'] == 'customer' else 'w', b['ref_id'], 88, '他自己按了綁定（等確認）')
    if u.get('suggest_cid'):
        add('c', u['suggest_cid'], 70, '系統之前猜的（名稱相近）')
    for (t,) in con.execute("SELECT text FROM line_inbox WHERE user_id=? AND account=? AND text LIKE '%09%' ORDER BY id DESC LIMIT 30", (uid, acc)):
        for p in re.findall(r'09\d{2}[\- ]?\d{3}[\- ]?\d{3}', t or ''):
            by_phone(p, 90, '他在對話裡留的電話')
    for kind, rid, score, why in extra_evidence(con, acc, uid, u['display_name']):      # 歷史對話、群組、地址
        add(kind, rid, score, why)
    dn = _norm(u['display_name'])
    if len(dn) >= 2:
        for cu in custs:
            for nm in (cu['line_name'], cu['name']):
                n = _norm(nm)
                if n and n == dn:
                    add('c', cu['id'], 80, 'LINE 名稱跟客戶資料一樣')
                elif len(n) >= 2 and (n in dn or dn in n):
                    add('c', cu['id'], 55, 'LINE 名稱相近')
        for w in wks:
            n = _norm(w['name'])
            if n and n == dn:
                add('w', w['id'], 80, 'LINE 名稱跟夥伴名字一樣')
            elif len(n) >= 2 and (n in dn or dn in n):
                add('w', w['id'], 55, 'LINE 名稱相近')
    out = []
    for (kind, rid), (score, why) in sorted(got.items(), key=lambda kv: -kv[1][0]):
        r = con.execute(f"SELECT name,phone FROM {'customers' if kind == 'c' else 'workers'} WHERE id=?", (rid,)).fetchone()
        if not r:
            continue
        if _taken(con, kind, rid, acc, uid):
            continue                 # 稽核：同品牌已綁別的 LINE → 不推薦（以前只扣分，點下去會綁兩個 LINE，通知傳給陌生人）
        tail = _digits(r['phone'])[-3:]
        out.append((kind, rid, f"{'客戶' if kind == 'c' else '夥伴'}：{r['name']}" + (f"（…{tail}）" if tail else ''), score, why))
    return sorted(out, key=lambda x: -x[3])[:limit]

def _taken(con, kind, rid, acc, uid=''):
    """這位客戶／夥伴在同一個品牌已經綁了別的 LINE（還是好友）。"""
    return bool(con.execute(f"SELECT 1 FROM line_users WHERE {'customer_id' if kind == 'c' else 'worker_id'}=? AND account=? AND is_friend=1 AND user_id!=?", (rid, acc, uid)).fetchone())

def search(con, acc, text, limit=5):
    """店長打字找：名字或電話（同品牌客戶；示範服務店也找夥伴）。"""
    import brands
    q, d = _norm(text), _digits(text)
    out = []
    if len(q) < 2 and len(d) < 3:
        return out
    for cu in c.rows(con, "SELECT id,name,line_name,phone,line_account,channel FROM customers"):
        if brands.of_customer(cu) != acc:
            continue
        if (len(d) >= 3 and d in _digits(cu['phone'])) or (q and (q in _norm(cu['name']) or q in _norm(cu['line_name']))):
            out.append(('c', cu['id'], f"客戶：{cu['name']}" + (f"（…{_digits(cu['phone'])[-3:]}）" if cu['phone'] else '')))
    if True:                                              # 夥伴不分品牌
        for w in c.rows(con, "SELECT id,name,phone FROM workers WHERE name NOT LIKE '（示範）%'"):
            if (len(d) >= 3 and d in _digits(w['phone'])) or (q and q in _norm(w['name'])):
                out.append(('w', w['id'], f"夥伴：{w['name']}" + (f"（…{_digits(w['phone'])[-3:]}）" if w['phone'] else '')))
    return [x for x in out if not _taken(con, x[0], x[1], acc)][:limit]

def card(con, u, cands=None, kicker='🔗 這個 LINE 是誰？'):
    import line_bot as lb, brands
    acc, uid = u['account'], u['user_id']
    cands = candidates(con, u) if cands is None else cands
    texts = [r[0] for r in con.execute("SELECT text FROM line_inbox WHERE user_id=? AND account=? AND COALESCE(text,'')!='' ORDER BY id DESC LIMIT 2", (uid, acc))]
    left = len(todo(con))
    rows = [lb._row('官方 LINE', brands.name(acc)), lb._row('第一次互動', (u['first_seen'] or '')[:10] or '—'), lb._row('最近', (u['last_seen'] or '')[:10] or '—')]
    if texts:
        rows.append(lb._row('他說過', '／'.join(t.replace('\n', ' ')[:30] for t in texts)))
    for _, _, label, score, why in cands:
        rows.append(lb._row('系統猜', f"{label}：{why}"))
    if not cands:
        rows.append(lb._t('系統猜不出來。認得的話按「打字找」打名字或電話；不是客戶（只是來問問）就按「不是客戶」。', color=lb.SUB, size='xs', wrap=True, margin='md'))
    k = f"u={uid}&b={acc}"
    btns = [lb._btn(f"是 {label}"[:20], data=f"a=pr&{k}&k={kind}&r={rid}", primary=i == 0) for i, (kind, rid, label, _, _) in enumerate(cands)]
    btns += [lb._btn('✏️ 打字找（名字／電話）', data=f'a=pr_type&{k}'), lb._btn('不是客戶（只是來問問）', data=f'a=pr_skip&{k}&v=no'),
             lb._btn('不知道，下次再問', data=f'a=pr_skip&{k}&v=later')]
    return lb.flex(f"這個 LINE 是誰？{u['display_name']}", lb._bubble(lb._header(kicker, u['display_name'] or '（沒有名稱）', '#0A84FF', brand=acc), rows, btns,
                   f'配對好系統就會傳歡迎訊息給他，之後提醒、完成照片都會自動推播。還有 {left} 位要配對。'))

def next_card(con, skip_uid=''):
    """下一位要配對的人（卡片），沒有就回 None。"""
    for u in todo(con):
        if u['user_id'] != skip_uid:
            return card(con, u)
    return None

def pair(con, acc, uid, kind, rid, by):
    """店長點選配對 → 綁定、傳歡迎訊息。回傳給她看的一句話。"""
    import line_bot as lb
    u = con.execute('SELECT * FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone()
    if not u:
        raise ValueError('找不到這個 LINE')
    if u['customer_id'] or u['worker_id']:
        return '這個 LINE 已經配對過了 👍'
    col, tbl = ('customer_id', 'customers') if kind == 'c' else ('worker_id', 'workers')
    who = c.get(con, tbl, rid)
    import brands
    if kind == 'c' and __import__('board').brand_known(con, who) not in ('', acc):      # 稽核 B1：品牌要一致（L9：沒標品牌的看聯絡管道）
        raise ValueError(f"{who['name']} 是{brands.name(__import__('board').brand_known(con, who))}的客戶，不能配到{brands.name(acc)}的 LINE")
    if _taken(con, kind, rid, acc, uid):
        raise ValueError(f"{who['name']} 在這個品牌已經綁了另一個 LINE，不能再配（要換請先到中控台解除舊的）")
    con.execute(f"UPDATE line_users SET {col}=?,suggest_cid='',onboard=CASE WHEN onboard='asked' THEN '' ELSE onboard END WHERE account=? AND user_id=?", (rid, acc, uid))
    if kind == 'c':
        con.execute("UPDATE customers SET line_account=? WHERE id=? AND COALESCE(line_account,'')=''", (acc, rid))
        con.execute("UPDATE customers SET line_name=? WHERE id=? AND COALESCE(line_name,'')=''", (u['display_name'] or '', rid))
    con.execute("UPDATE bind_requests SET status='approved',decided_by=?,decided_at=? WHERE user_id=? AND account=? AND status='open' AND ref_id=?", (by + '（LINE 配對）', c.now(), uid, acc, rid))
    con.execute("UPDATE bind_requests SET status='superseded',decided_by=?,decided_at=? WHERE user_id=? AND account=? AND status='open'", (by + '（LINE 配對）', c.now(), uid, acc))
    c.audit(con, 'line_pair', 'line_users', uid, after={'account': acc, kind: rid, 'by': by})
    if kind == 'c':
        m = lb.card_next_step(con, acc, uid, rid)
        lb.queue(con, acc, uid, 'hello', m[0]['altText'], m)
        ix.set_setting(con, NONE_KEY.format(rid), '')
        lb.after_customer_linked(con, rid)          # 負責人 對上就自動補傳（等確認的時間卡；完成照片由 deliver_pending_finish 一分鐘內補）
    else:
        lb.worker_menu_link(con, uid, acc)
        lb.queue(con, acc, uid, 'hello', '已確認夥伴身分', lb._txt(f"✅ {who['name']}，已幫你把這個 LINE 跟示範服務店夥伴資料連起來了！之後派工、提醒都會在這裡通知你，下方也換成夥伴選單。"))
    return f"✅ 已配對：{u['display_name']} ↔ {who['name']}（{'客戶' if kind == 'c' else '夥伴'}），已傳歡迎訊息給他"

# ───── 反向：這位業主是哪個 LINE？（負責人 業主其實都在官方 LINE 聊過，請店長對照，對上就能推播） ─────
NONE_KEY = 'pcf_none:{}'            # 店長說「他沒加官方 LINE」→ 14 天內不再問，改請管理群打電話

def customer_candidates(con, cid, limit=4):
    """這位業主可能是哪個 LINE（同品牌、還沒配對的好友）：[(uid, LINE 名稱, 分數, 理由)]。"""
    import brands
    cu = c.get(con, 'customers', cid); acc = brands.of_customer(cu)
    names = [x for x in [cu['name'], cu['line_name']] + [r[0] for r in con.execute('SELECT name FROM contacts WHERE customer_id=?', (cid,))] if x]
    p8 = _digits(cu['phone'])[-8:]
    sk = _skips(con); out = []
    for u in c.rows(con, "SELECT * FROM line_users WHERE account=? AND is_friend=1 AND customer_id IS NULL AND worker_id IS NULL AND COALESCE(is_staff,0)=0 "
                         "AND user_id NOT LIKE 'demo%' ORDER BY last_seen DESC", (acc,)):
        if sk.get(f"{acc}|{u['user_id']}") == 'no':
            continue
        score, why = 0, ''
        dn = _norm(u['display_name'])
        for nm in names:
            n = _norm(nm)
            if n and dn and n == dn and score < 85:
                score, why = 85, 'LINE 名稱跟業主資料一樣'
            elif len(n) >= 2 and len(dn) >= 2 and (n in dn or dn in n) and score < 60:
                score, why = 60, 'LINE 名稱相近'
        if len(p8) == 8:
            if any(_digits(i[0]).endswith(p8) for i in con.execute("SELECT phone FROM inquiries WHERE line_user_id=? AND COALESCE(account,'demoshop')=?", (u['user_id'], acc))):
                score, why = 95, '他填的預約表單電話一樣'
            elif any(p8 in _digits(t or '') for (t,) in con.execute("SELECT text FROM line_inbox WHERE user_id=? AND account=? AND text LIKE '%09%' ORDER BY id DESC LIMIT 30", (u['user_id'], acc))):
                score, why = 95, '他在對話裡留了這支電話'
        for kind, rid, sc, w in extra_evidence(con, acc, u['user_id'], u['display_name']):      # 歷史對話、群組、地址
            if kind == 'c' and rid == cid and sc > score:
                score, why = sc, w
        if score:
            out.append((u['user_id'], u['display_name'] or '（沒有名稱）', score, why))
    out.sort(key=lambda x: -x[2])
    return out[:limit]

def customer_card(con, cid, why=''):
    import line_bot as lb, brands
    cu = c.get(con, 'customers', cid); acc = brands.of_customer(cu)
    cands = customer_candidates(con, cid)
    rows = [lb._row('業主', cu['name']), lb._row('品牌', brands.name(acc)), lb._row('電話', cu['phone'] or '—')]
    if why:
        rows.append(lb._row('為什麼', why))
    for _, dn, sc, w in cands:
        rows.append(lb._row('可能是', f'{dn}：{w}'))
    if not cands:
        rows.append(lb._t('系統比對了表單、對話、歷史對話、群組都找不到證據。按「看全部未配對的 LINE」自己挑，或「打字找」；他沒加官方 LINE 就按最下面。', color=lb.SUB, size='xs', wrap=True, margin='md'))
    btns = [lb._btn(f'是「{dn}」'[:20], data=f"a=pr&u={uid}&b={acc}&k=c&r={cid}&f=c", primary=i == 0) for i, (uid, dn, _, _) in enumerate(cands)]
    n_all = len(unpaired(con, acc))
    btns += [lb._btn(f'看全部未配對的 LINE（{n_all}）', data=f'a=pall&k=c&r={cid}&p=0'), lb._btn('✏️ 打字找 LINE 名稱', data=f'a=pcf_type&c={cid}'),
             lb._btn('他沒加官方 LINE', data=f'a=pcf_none&c={cid}')]
    return lb.flex(f'這位業主是哪個 LINE？{cu["name"]}', lb._bubble(lb._header('🔗 這位業主是哪個 LINE？', cu['name'], '#0A84FF', brand=acc), rows, btns,
                   '對上之後：提醒、完成照片都會自動傳給他，也會請他補資料。'))

def customers_needing_line(con):
    """還沒對上 LINE、但最近有事要傳給他的業主：[(cid, 為什麼)]（完成照片 14 天內沒傳到、7 天內有服務）。"""
    import line_bot as lb
    today = dt.datetime.now(c.TZ).date(); out = {}
    for o in c.rows(con, "SELECT o.* FROM orders o WHERE o.customer_id IS NOT NULL AND ((o.status='completed' AND o.date>=?) OR (o.status IN ('review','draft','confirmed') AND o.date BETWEEN ? AND ?)) "
                         "AND COALESCE(o.review_note,'')!='示範工單' ORDER BY o.date", ((today - dt.timedelta(days=14)).isoformat(), today.isoformat(), (today + dt.timedelta(days=7)).isoformat())):
        if o['customer_id'] in out or lb.customer_target(con, o['customer_id'])[1]:
            continue
        nn = ix.setting(con, NONE_KEY.format(o['customer_id']))
        if nn and nn >= (today - dt.timedelta(days=14)).isoformat():
            continue
        out[o['customer_id']] = f"{lb.md(o['date'])} " + ('完成照片要傳給他' if o['status'] == 'completed' else '有服務，提醒要傳給他')
    return list(out.items())

def next_customer_card(con, skip=''):
    for cid, why in customers_needing_line(con):
        if cid != skip:
            return customer_card(con, cid, why)
    return None

def search_line(con, cid, text, limit=5):
    """店長打 LINE 名稱找（同品牌、還沒配對的好友）。"""
    import line_bot as lb, brands
    cu = c.get(con, 'customers', cid); acc = brands.of_customer(cu); q = _norm(text)
    hits = [u for u in c.rows(con, "SELECT user_id,display_name FROM line_users WHERE account=? AND is_friend=1 AND customer_id IS NULL AND worker_id IS NULL AND COALESCE(is_staff,0)=0",
                              (acc,)) if q and q in _norm(u['display_name'])][:limit]
    if not hits:
        return lb._txt(f'找不到 LINE 名稱有「{text}」的人（他可能還沒加{brands.name(acc)}官方 LINE，或還沒傳過訊息）。') + customer_card(con, cid)
    btns = [lb._btn(f"是「{u['display_name']}」"[:20], data=f"a=pr&u={u['user_id']}&b={acc}&k=c&r={cid}&f=c", primary=i == 0) for i, u in enumerate(hits)]
    return lb.flex(f'找到 {len(hits)} 個 LINE', lb._bubble(lb._header('🔎 是哪一個？', cu['name'], '#0A84FF', brand=acc), [lb._t(f'LINE 名稱有「{text}」的人：', wrap=True)], btns))

def mark_none(con, cid, by):
    import line_bot as lb
    ix.set_setting(con, NONE_KEY.format(cid), dt.datetime.now(c.TZ).date().isoformat())
    c.audit(con, 'line_pair_none', 'customers', cid, after={'by': by})
    cu = c.get(con, 'customers', cid)
    return f"好，{cu['name']} 先記為「沒加官方 LINE」，派單管理群會改打電話（{cu['phone'] or '沒有電話'}）；他加了之後這裡會再問。"

# ───── 夥伴是哪個 LINE？（負責人 沒綁定的夥伴也要管理層盡速配對，派工、提醒、地址才傳得到） ─────
WNONE_KEY = 'pwf_none:{}'           # 管理層說「他沒加官方 LINE」→ 已給加好友訊息轉傳，14 天內不再列

def workers_needing_line(con):
    """還沒對上 LINE、前後 14 天有排班的夥伴：[(wid, 為什麼)]。"""
    import line_bot as lb
    today = dt.datetime.now(c.TZ).date(); since = (today - dt.timedelta(days=14)).isoformat(); out = []
    for r in c.rows(con, "SELECT a.worker_id,MIN(CASE WHEN o.date>=? THEN o.date END) nxt,MAX(o.date) last FROM assignments a JOIN orders o ON o.id=a.order_id "
                         "JOIN workers w ON w.id=a.worker_id WHERE a.status='accepted' AND o.status NOT IN ('cancelled','archived') AND o.date BETWEEN ? AND ? "
                         "AND w.name NOT LIKE '（示範）%' AND COALESCE(o.review_note,'')!='示範工單' GROUP BY a.worker_id ORDER BY COALESCE(nxt,last)",
                    (today.isoformat(), since, (today + dt.timedelta(days=14)).isoformat())):
        if lb.worker_target(con, r['worker_id'])[1]:
            continue
        wn = _norm((con.execute('SELECT name FROM workers WHERE id=?', (r['worker_id'],)).fetchone() or [''])[0])
        if len(wn) >= 2 and any(wn in _norm(d) for (d,) in con.execute("SELECT display_name FROM line_users WHERE COALESCE(is_staff,0)=1 OR user_id IN (%s)" % ','.join('?' * len(lb.staff_users(con)) or "''"), tuple(lb.staff_users(con)))):
            continue                     # 管理層自己（例：負責人）用的是管理層 LINE，不用再問
        nn = ix.setting(con, WNONE_KEY.format(r['worker_id']))
        if nn and nn >= since:
            continue
        out.append((r['worker_id'], f"{lb.md(r['nxt'])} 有排班，派工和提醒要傳給他" if r['nxt'] else f"{lb.md(r['last'])} 有排班（要傳薪資、下次派工）"))
    return out

def worker_candidates(con, wid, limit=4):
    """這位夥伴可能是示範服務店官方 LINE 的哪一位（還沒配對的好友）：[(uid, LINE 名稱, 分數, 理由)]。"""
    w = c.get(con, 'workers', wid); n = _norm(w['name']); p8 = _digits(w['phone'])[-8:]
    sk = _skips(con); out = []
    for u in c.rows(con, "SELECT * FROM line_users WHERE is_friend=1 AND customer_id IS NULL AND worker_id IS NULL AND COALESCE(is_staff,0)=0 "
                         "AND user_id NOT LIKE 'demo%' ORDER BY last_seen DESC"):      # 三個官方 LINE 都找
        if sk.get(f"{u['account']}|{u['user_id']}") == 'no':
            continue
        score, why = 0, ''
        dn = _norm(u['display_name'])
        if n and dn and n == dn:
            score, why = 85, 'LINE 名稱跟夥伴資料一樣'
        elif len(n) >= 2 and len(dn) >= 2 and (n in dn or dn in n):
            score, why = 60, 'LINE 名稱相近'
        if len(p8) == 8 and any(p8 in _digits(t or '') for (t,) in con.execute("SELECT text FROM line_inbox WHERE user_id=? AND account=? AND text LIKE '%09%' ORDER BY id DESC LIMIT 30", (u['user_id'], u['account']))):
            score, why = 95, '他在對話裡留了這支電話'
        for kind, rid, sc, wy in extra_evidence(con, u['account'], u['user_id'], u['display_name']):      # 群組發言、歷史對話
            if kind == 'w' and rid == wid and sc > score:
                score, why = sc, wy
        if score:
            out.append((u['user_id'], u['display_name'] or '（沒有名稱）', score, why, u['account']))
    out.sort(key=lambda x: -x[2])
    return out[:limit]

def worker_card(con, wid, why=''):
    import line_bot as lb
    w = c.get(con, 'workers', wid); cands = worker_candidates(con, wid)
    rows = [lb._row('夥伴', w['name']), lb._row('電話', w['phone'] or '—')] + ([lb._row('為什麼', why)] if why else [])
    import brands
    rows += [lb._row('可能是', f'{dn}（{brands.name(ac)}）：{wy}') for _, dn, _, wy, ac in cands]
    if not cands:
        rows.append(lb._t('示範服務店官方 LINE 裡找不到可能的人。他沒加官方 LINE 的話按最下面，系統給你加好友訊息轉傳給他。', color=lb.SUB, size='xs', wrap=True, margin='md'))
    btns = [lb._btn(f'是「{dn}」'[:20], data=f"a=pr&u={uid}&b={ac}&k=w&r={wid}&f=w", primary=i == 0) for i, (uid, dn, _, _, ac) in enumerate(cands)]
    btns += [lb._btn(f"看全部未配對的 LINE（{sum(len(unpaired(con, a)) for a in ('demoshop', 'homeline', 'bizline'))}）", data=f'a=pall&k=w&r={wid}&p=0'), lb._btn('他沒加官方 LINE', data=f'a=pwf_none&w={wid}')]
    return lb.flex(f"這位夥伴是哪個 LINE？{w['name']}", lb._bubble(lb._header('🔗 這位夥伴是哪個 LINE？', w['name'], '#2E7D32'), rows, btns,
                   '對上之後：派工卡、提醒、地址都會直接傳給他，下方也換成夥伴選單。'))

def unpaired(con, acc):
    """這個品牌還沒配對的 LINE（不含管理層、說過「不是客戶也不是夥伴」的）。"""
    sk = _skips(con)
    return [u for u in c.rows(con, "SELECT * FROM line_users WHERE account=? AND is_friend=1 AND customer_id IS NULL AND worker_id IS NULL AND COALESCE(is_staff,0)=0 "
                                   "AND user_id NOT LIKE 'demo%' ORDER BY last_seen DESC", (acc,)) if sk.get(f"{acc}|{u['user_id']}") != 'no']

def all_card(con, kind, rid, page=0):
    """負責人 選單要顯示「所有」還沒配對的 LINE 給我們選（不是只有猜的那幾個）。每頁 6 位、最多 5 頁一則。"""
    import line_bot as lb, brands
    who = c.get(con, 'customers' if kind == 'c' else 'workers', rid)
    acc = brands.of_customer(who) if kind == 'c' else 'demoshop'
    us = unpaired(con, acc) if kind == 'c' else [u for a in brands.ids() for u in unpaired(con, a)] if hasattr(brands, 'ids') else [u for a in ('demoshop', 'homeline', 'bizline') for u in unpaired(con, a)]      # 夥伴三個官方 LINE 都列
    per, start = 6, int(page) * 30
    chunk = us[start:start + 30]
    if not chunk:
        return lb._txt('沒有其他還沒配對的 LINE 了。他可能還沒加官方 LINE，或還沒傳過訊息（系統要對方先傳訊息才拿得到）。')
    bs = []
    for i in range(0, len(chunk), per):
        part = chunk[i:i + per]
        rows = [lb._t(f"{who['name']} 是哪一位？（{start + i + 1}–{start + i + len(part)}／共 {len(us)} 位）", size='sm', color=lb.SUB, wrap=True)]
        btns = [lb._btn(f"{(u['display_name'] or '（沒有名稱）')[:14]}・{(u['last_seen'] or '')[5:10].replace('-', '/')}", data=f"a=pr&u={u['user_id']}&b={u['account']}&k={kind}&r={rid}&f={kind}")
                for u in part]
        bs.append(lb._bubble(lb._header('🔗 全部未配對的 LINE', who['name'], '#0A84FF', brand=acc), rows, btns, '名字旁邊是最後一次傳訊息的日期'))
    if len(us) > start + 30:
        bs.append(lb._bubble(lb._header('還有更多', who['name'], '#0A84FF', brand=acc), [lb._t(f'還有 {len(us) - start - 30} 位', wrap=True)],
                             [lb._btn('下一頁', data=f'a=pall&k={kind}&r={rid}&p={int(page) + 1}')]))
    bs.append(lb._bubble(lb._header('到中控台配對', who['name'], '#0A84FF', brand=acc), [lb._t('中控台「更多 › LINE 配對」看得到每個聊天室的對話、群組、證據，配錯也可以解除。', wrap=True)],
                         [lb._btn('打開 LINE 配對', uri=f'{lb.BASE_URL}/#pairing')]))
    return lb._carousel(f"{who['name']} 是哪一位？", bs[:12])

def auto_pair(con):
    """證據夠強就自動配對（負責人 雙向判斷、看看能不能自己配對成功）：
    電話證據（他自己留的電話、表單）分數 ≥ 88，而且雙向唯一（這個 LINE 只對到這位、這位也只對到這個 LINE）。配好通知派單管理群，配錯可在中控台解除。"""
    import line_bot as lb
    done, by_target = [], {}
    cand = {}
    for u in todo(con):
        cs = candidates(con, u, limit=5)
        cand[(u['account'], u['user_id'])] = (u, cs)
        for k, rid, _, sc, _ in cs:
            if sc >= 80:
                by_target.setdefault((u['account'], k, rid), set()).add(u['user_id'])
    for (acc, uid), (u, cs) in cand.items():
        strong = [x for x in cs if x[3] >= 88 and ('電話' in x[4] or '表單' in x[4])]
        if len(strong) != 1 or len([x for x in cs if x[3] >= 80]) != 1:
            continue
        k, rid, label, sc, why = strong[0]
        if by_target.get((acc, k, rid), set()) != {uid}:
            continue
        try:
            pair(con, acc, uid, k, rid, f'系統自動配對（{why}）')
            done.append(f"{u['display_name'] or '（沒有名稱）'} ↔ {label}（{why}）")
        except ValueError:
            continue
    if done:
        lb.admin_notify(con, f'🔗 系統自動配對 {len(done)} 位（證據夠強）', [('配對', '\n'.join(done[:10])), ('配錯', '中控台「更多 › LINE 配對」可以解除')], '/#pairing', '#2E7D32')
    return len(done)

def unpair(con, acc, uid, by):
    u = con.execute('SELECT * FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone()
    if not u or not (u['customer_id'] or u['worker_id']):
        raise ValueError('這個 LINE 沒有配對')
    con.execute('UPDATE line_users SET customer_id=NULL,worker_id=NULL WHERE account=? AND user_id=?', (acc, uid))
    c.audit(con, 'line_unpair', 'line_users', uid, before={'customer_id': u['customer_id'], 'worker_id': u['worker_id']}, after={'by': by})
    return '已解除配對（不會通知對方）'

def unbound_hints(con):
    """客戶頁「未綁定 LINE」：服務中、還沒綁（同品牌）官方 LINE 的客戶，系統為什麼找不到他的聊天室（負責人 「這些都查不到對話？」）。
    系統只拿得到「在官方 LINE 傳過訊息」的人的 LINE 身分（userId）；歷史對話匯出檔只有名字和文字，沒有 userId，不能拿來傳訊息。"""
    import brands
    ex = exports(con)
    friends = {r[0]: r[1] for r in con.execute('SELECT account,COUNT(*) FROM line_users WHERE is_friend=1 GROUP BY account')}
    hist_n = {r[0]: r[1] for r in con.execute("SELECT COALESCE(account,'demoshop'),COUNT(*) FROM chat_threads GROUP BY 1")}
    out = {}
    for cu in c.rows(con, "SELECT * FROM customers WHERE status IN ('active','paused')"):
        acc = brands.of_customer(cu)
        if con.execute('SELECT 1 FROM line_users WHERE customer_id=? AND account=? AND is_friend=1', (cu['id'], acc)).fetchone():
            continue
        keys = {_norm(cu['name']), _norm(cu.get('line_name') or '')} - {''}
        hist = [d['name'] for (a, k), d in ex.items() if a == acc and any(len(x) >= 2 and (x in k or k in x) for x in keys)][:2]
        out[cu['id']] = {'cands': [{'name': n, 'score': s, 'why': w} for _, n, s, w in customer_candidates(con, cu['id'], limit=2)], 'hist': hist,
                         'none': bool(ix.setting(con, NONE_KEY.format(cu['id'])))}
    return {'hints': out, 'friends': friends, 'history': hist_n}

def overview(con):
    """中控台「LINE 配對」頁：偵測到還沒配對的聊天室（含對話、群組、證據）、還沒對上 LINE 的業主／夥伴、選單（全部還沒配對的客戶／夥伴）。"""
    import brands
    sk = _skips(con)
    lines = []
    for u in c.rows(con, "SELECT * FROM line_users WHERE is_friend=1 AND customer_id IS NULL AND worker_id IS NULL AND COALESCE(is_staff,0)=0 AND user_id NOT LIKE 'demo%' ORDER BY last_seen DESC"):
        s = sk.get(f"{u['account']}|{u['user_id']}")
        n1 = con.execute("SELECT COUNT(*) FROM line_inbox WHERE user_id=? AND account=? AND COALESCE(group_id,'')='' AND type='message'", (u['user_id'], u['account'])).fetchone()[0]
        gs = [f"{r[0]}（{r[1]} 則）" for r in con.execute("SELECT g.name,COUNT(*) FROM line_inbox i JOIN line_groups g ON g.group_id=i.group_id WHERE i.user_id=? GROUP BY g.group_id", (u['user_id'],))]
        last = [r[0][:50] for r in con.execute("SELECT text FROM line_inbox WHERE user_id=? AND account=? AND COALESCE(text,'')!='' ORDER BY id DESC LIMIT 3", (u['user_id'], u['account']))]
        ex = exports(con).get((u['account'], _norm(u['display_name'])))
        lines.append({'acc': u['account'], 'brand': brands.name(u['account']), 'uid': u['user_id'], 'name': u['display_name'] or '（沒有名稱）', 'last_seen': (u['last_seen'] or '')[:16].replace('T', ' '),
                      'n1': n1, 'groups': gs, 'silent': silent(con, u), 'last': last, 'history': ex['name'] if ex else '', 'skip': s or '',
                      'cands': [{'kind': k, 'id': rid, 'label': lab, 'score': sc, 'why': w} for k, rid, lab, sc, w in candidates(con, u, limit=4)]})
    need_c = [{'id': cid, 'why': why, 'name': c.get(con, 'customers', cid)['name']} for cid, why in customers_needing_line(con)]
    need_w = [{'id': wid, 'why': why, 'name': c.get(con, 'workers', wid)['name']} for wid, why in workers_needing_line(con)]
    bound_c = {r[0] for r in con.execute('SELECT DISTINCT customer_id FROM line_users WHERE customer_id IS NOT NULL AND is_friend=1')}
    bound_w = {r[0] for r in con.execute('SELECT DISTINCT worker_id FROM line_users WHERE worker_id IS NOT NULL AND is_friend=1')}
    pick_c = [{'id': cu['id'], 'name': cu['name'], 'tail': _digits(cu['phone'])[-3:], 'acc': brands.of_customer(cu)} for cu in c.rows(con, "SELECT id,name,phone,line_account,channel FROM customers WHERE COALESCE(status,'') NOT IN ('merged','deleted') ORDER BY name") if cu['id'] not in bound_c]
    pick_w = [{'id': w['id'], 'name': w['name'], 'tail': _digits(w['phone'])[-3:]} for w in c.rows(con, "SELECT id,name,phone FROM workers WHERE status IN ('active','review') AND name NOT LIKE '（示範）%' ORDER BY name") if w['id'] not in bound_w]
    paired = [{'acc': r['account'], 'uid': r['user_id'], 'name': r['display_name'] or '', 'who': r['who'] or '', 'kind': 'c' if r['customer_id'] else 'w'}
              for r in c.rows(con, "SELECT l.account,l.user_id,l.display_name,l.customer_id,l.worker_id,COALESCE(cu.name,w.name) who FROM line_users l LEFT JOIN customers cu ON cu.id=l.customer_id "
                                   "LEFT JOIN workers w ON w.id=l.worker_id WHERE l.is_friend=1 AND (l.customer_id IS NOT NULL OR l.worker_id IS NOT NULL) ORDER BY l.last_seen DESC LIMIT 300")]
    return {'lines': lines, 'need_c': need_c, 'need_w': need_w, 'pick_c': pick_c, 'pick_w': pick_w, 'paired': paired,
            'history_threads': len(exports(con))}

def next_worker_card(con, skip=''):
    for wid, why in workers_needing_line(con):
        if wid != skip:
            return worker_card(con, wid, why)
    return None

def mark_worker_none(con, wid, by):
    ix.set_setting(con, WNONE_KEY.format(wid), dt.datetime.now(c.TZ).date().isoformat())
    c.audit(con, 'line_pair_none', 'workers', wid, after={'by': by})

def skip(con, acc, uid, v, by):
    sk = _skips(con)
    sk[f'{acc}|{uid}'] = 'no' if v == 'no' else (dt.datetime.now(c.TZ).date() + dt.timedelta(days=7)).isoformat()
    ix.set_setting(con, SKIP_KEY, json.dumps(sk, ensure_ascii=False))
    c.audit(con, 'line_pair_skip', 'line_users', uid, after={'account': acc, 'v': v, 'by': by})
    return '好，標記為「不是客戶也不是夥伴」，之後不會再問 👌' if v == 'no' else '好，先跳過，7 天後再問 👌'

def typed(con, acc, uid, text):
    """店長打字找人 → 列出符合的（點一下就配對）。"""
    import line_bot as lb
    u = con.execute('SELECT * FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone()
    if not u:
        return lb._txt('找不到這個 LINE')
    found = search(con, acc, text)
    if not found:
        return [{'type': 'text', 'text': f'找不到「{text[:20]}」（只找{__import__("brands").name(acc)}的客戶' + ('、夥伴' if acc == 'demoshop' else '') + '）。可以再按「打字找」換個名字或電話試試。'}] + card(con, dict(u))
    return card(con, dict(u), [(k, r, lbl, 100, '妳打字找到的') for k, r, lbl in found], kicker='🔎 是這幾位其中一位嗎？')
