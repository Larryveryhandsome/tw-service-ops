"""店長待處理清單（送她的點選題、審核都還沒回，報價也卡 3 天，系統沒有追 → 要追）。

- 每天 10:30 一張卡：待回答的題目、待審核、報價待確認、收款待確認，各幾件、最久等了幾天。
  按「一題一題回答」→ 傳最舊的一題；回答完系統自動接著傳下一題（answered → next_question）。
- 負責人也收得到同一張（他是老闆，可以催）。
"""
import datetime as dt
import json
import core as c
import integrations as ix

def asks(con):
    """還沒回答的點選題（最舊的先）。"""
    out = []
    answered = {r[0][7:] for r in con.execute("SELECT key FROM settings WHERE key LIKE 'answer:%'")}
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'ask:%'"):
        t = k[4:]
        if t in answered:
            continue
        try:
            d = json.loads(v)
        except ValueError:
            continue
        out.append({'topic': t, **d})
    return sorted(out, key=lambda x: x.get('asked_at', ''))

def counts(con):
    import reviews, payments
    reviews.ensure(con); payments.ensure(con)
    q = con.execute("SELECT COUNT(*),MIN(created_at) FROM quotes WHERE status='draft' AND stage IN ('review','site_visit')").fetchone()
    import door, pairing
    return {'doors': len(door.fill_due(con)), 'pairs': len(pairing.todo(con)), 'asks': asks(con), 'reviews': reviews.pending(con),
            'quotes': (q[0], q[1] or ''), 'pays': con.execute("SELECT COUNT(*) FROM payment_requests WHERE status='reported'").fetchone()[0],
            'stale': _stale(con), 'cust_line': len(__import__('pairing').customers_needing_line(con)),
            'binds': con.execute("SELECT COUNT(*) FROM bind_requests WHERE status='open'").fetchone()[0],      # 綁定待確認只給數字
            'standing': standing(con),
            'disputes': con.execute("SELECT COUNT(*) FROM orders WHERE acceptance='disputed' AND COALESCE(feedback_done,'')='' AND date>=?",
                                    ((dt.datetime.now(c.TZ).date() - dt.timedelta(days=30)).isoformat(),)).fetchone()[0]}      # 稽核：客戶說要加強、還沒處理

def standing(con):
    """常態的事（不再一張張推）→ 10:30 那一則只寫數字，請管理層到中控台處理。"""
    import automation, guide, materials, testimonials, staffq
    out = {}
    wk = automation.unbound_workers(con)
    if wk:
        out['夥伴還沒綁 LINE'] = f"{len(wk)} 位（中控台「夥伴 › LINE 還沒綁」：複製綁定訊息、已傳給他了）"
    pm = staffq.pay_mismatch(con)          # 稽核 A1
    if pm:
        out['收款單金額跟工單不一樣'] = f"{len(pm)} 筆（中控台「管理群待處理」一鍵改成工單金額，或標「本來就不同」；看得到金額的人）"
    ph = [x for x in staffq.items(con, True) if x['kind'] == 'photo']
    if ph:
        out['照片不齊・薪資暫不發'] = f"{len(ph)} 場（確定正常完工就按「確認，照常發薪」）"
    mats = materials.pending(con)
    if mats:
        out['素材待準備'] = '、'.join(f"{it['label']}（{it.get('got', 0)}/{it['need']}）" for it in mats.values()) + '（中控台可以直接上傳）'
    tms = {t['customer'] or '—' for t in testimonials.pending(con)}
    if tms:
        out['滿意客戶還沒回謝'] = f"{len(tms)} 位（{'、'.join(list(tms)[:4])}）"
    late = con.execute("SELECT COUNT(*) FROM tasks WHERE status='open' AND due_date<?", (c.now()[:10],)).fetchone()[0] + \
        con.execute("SELECT COUNT(*) FROM service_cases WHERE status='open' AND due_date<?", (c.now()[:10],)).fetchone()[0]
    if late:                                           # 人工待辦、客訴逾期（以前藏在「待辦與品質」沒人看到）
        out['待辦／客訴已逾期'] = f'{late} 件（中控台「更多 › 管理群待處理」最下面，結案要寫處理結果）'
    import worker_portal, line_bot as _lb2          # 已綁 LINE 的夥伴基本資料要填完整
    inc = [t['name'] for t in _lb2.guide_targets(con) if worker_portal.profile_missing(c.get(con, 'workers', t['id']))]
    if inc:
        out['夥伴資料還沒填完整'] = f"{len(inc)} 位（{'、'.join(inc[:6])}{'…' if len(inc) > 6 else ''}）：中控台夥伴頁看得到缺哪些"
    import meetup
    mt = meetup.standing(con)
    if mt:
        out['夥伴大會報名'] = mt
    import reschedule                              # 稽核 優化：業主改期申請超過 4 小時沒處理 → 併進 10:30 這則
    rs = [r for r in reschedule.pending_items(con) if r['created_at'] < (dt.datetime.now(c.TZ) - dt.timedelta(hours=4)).isoformat(timespec='seconds')]
    if rs:
        out['業主申請改時間還沒處理'] = f"{len(rs)} 筆（群組卡或中控台「更多 › 管理群待處理」：改到新時間／這時間不行）"
    import mkpatrol                                # 海巡留言全部審過才發（每天 10:30、16:30 各審一批）
    pn = mkpatrol.count()
    if pn:
        out['海巡留言待審'] = f'{pn} 則（中控台「更多 › 行銷 › 海巡留言待審」：核准／改字／略過；超過 24 小時沒審自動過期）'
    try:
        dn = len(mkpatrol.drafts())
    except Exception:
        dn = 0
    if dn:
        out['Threads 草稿待審'] = f'{dn} 篇（中控台「更多 › 行銷 › Threads 草稿待審」：核准會排進空的發文時段）'
    import ledger
    lg = ledger.standing(con)
    if lg:
        out['記帳憑證待確認'] = lg
    unseen = [u['name'] for u in guide.managers(con) if not guide.seen(con, u['id'])]
    if unseen:
        out['新功能還沒看'] = '、'.join(unseen) + '（登入中控台會自動跳出，1–2 分鐘）'
    return out

def _stale(con):
    """還沒處理超過 4 小時的請假／用品／現場回報／面試（負責人 列進每天 10:30 待處理卡，直到處理完）。"""
    import automation, collections
    cut = (dt.datetime.now(c.TZ) - dt.timedelta(hours=4)).isoformat(timespec='seconds')
    return dict(collections.Counter(k for _, k, _, _, at in automation.stale_items(con) if at and at < cut))

def _days(ts):
    try:
        return max(0, (dt.datetime.now(c.TZ) - dt.datetime.fromisoformat(ts[:19]).replace(tzinfo=c.TZ)).days)
    except ValueError:
        return 0

def card(con, chores=True):
    import line_bot as lb
    n = counts(con)
    if not chores:                    # 負責人：誰按都行的雜事（開門待補、LINE 配對）不列給他
        n['doors'] = n['pairs'] = 0
    if ix.setting(con, 'pair_push') != 'on':          # 配對不推播，店長到中控台確認
        n['pairs'] = n['cust_line'] = 0
    rows = []
    if n['asks']:
        rows.append(lb._row('要妳決定', f"{len(n['asks'])} 題（最久等 {_days(n['asks'][0].get('asked_at', ''))} 天）"))
    if n['reviews']:
        rows.append(lb._row('要妳審核', f"{len(n['reviews'])} 件（最久等 {_days(n['reviews'][0]['created_at'])} 天）"))
    if n['quotes'][0]:
        rows.append(lb._row('報價待確認', f"{n['quotes'][0]} 張（最久等 {_days(n['quotes'][1])} 天）"))
    if n['pays']:
        rows.append(lb._row('收款待確認', f"{n['pays']} 筆（客戶已回報匯款）"))
    if n['doors']:
        rows.append(lb._row('開門方式待補', f"{n['doors']} 個案場（7 天內有服務）"))
    if n['pairs']:
        rows.append(lb._row('LINE 好友待配對', f"{n['pairs']} 位（配對好才能推播給他）"))
    if n.get('cust_line') and chores:
        rows.append(lb._row('業主待對 LINE', f"{n['cust_line']} 位（對上才能自動傳提醒、完成照片）"))
    if n.get('binds'):
        rows.append(lb._row('綁定待確認', f"{n['binds']} 筆（電話對到既有資料，到中控台「LINE 配對」確認是不是本人）"))
    if n.get('disputes'):
        rows.append(lb._row('客戶反映要加強', f"{n['disputes']} 件還沒按「已處理」（尾款通知暫停中）"))
    if n.get('stale') and chores:
        rows.append(lb._row('超過 4 小時沒處理', '、'.join(f'{k} {v} 件' for k, v in n['stale'].items())))
    for k, v in (n.get('standing') or {}).items():
        rows.append(lb._row(k, v))
    try:                                           # 管理群待處理（中控台）各類幾件
        import staffq
        grp = {}
        for x in staffq.items(con, True):
            if x['kind'] not in ('rv',):
                grp[x['group']] = grp.get(x['group'], 0) + 1
        if grp:
            rows.append(lb._row('管理群待處理', '、'.join(f'{g} {k_}' for g, k_ in sorted(grp.items(), key=lambda z: -z[1])[:6])))
    except Exception:
        pass
    if not rows:
        return None
    btns = [lb._btn('到中控台處理 ›', uri=f'{lb.BASE_URL}/#asks', primary=True)] + ([lb._btn('一題一題回答', data='a=pq_next')] if n['asks'] else []) + ([lb._btn('看審核', data='a=pq_reviews')] if n['reviews'] else []) + \
           ([lb._btn('補開門方式', data='a=ent_next')] if n['doors'] else []) + ([lb._btn('配對 LINE 好友', data='a=pr_next')] if n['pairs'] else []) + \
           ([lb._btn('看報價', uri=f'{lb.BASE_URL}/#quotes')] if n['quotes'][0] else []) + ([lb._btn('到中控台確認綁定', uri=f'{lb.BASE_URL}/#pairing')] if n.get('binds') else [])
    return lb.flex('📋 待處理的事', lb._bubble(lb._header('📋 待處理的事', '點一下就能一件一件處理', '#C47F00'), rows, btns[:6],
                   '不急的事一天提醒三次（10:30、14:30、18:30，省 LINE 額度）；中控台「更多 › 管理群待處理」都能直接處理。'))

SLOTS = ('10:30', '14:30', '18:30')      # 不急、要管理群處理的 → 一天三次集中提醒

def digest(con, now=None):
    """每天 10:30、14:30、18:30：有待處理才傳一則（管理群 @店長）。"""
    import line_bot as lb, reviews
    now = now or dt.datetime.now(c.TZ); hm = now.strftime('%H:%M')
    slot = max((s for s in SLOTS if s <= hm), default='')
    key = now.date().isoformat() + ' ' + slot
    if not slot or ix.setting(con, 'pending_slot') == key:
        return 0
    ix.set_setting(con, 'pending_slot', key); ix.set_setting(con, 'pending_day', now.date().isoformat())
    sent = 0
    m = card(con, chores=True)                     # 不私訊店長 → 一張送管理群 @店長
    if m:
        lb.to_managers(con, 'pending', m[0]['altText'], m); sent += 1
    return 1 if sent else 0

def next_question(con, skip=''):
    """下一題（訊息＋快速回覆），沒有就回 None。"""
    import line_bot as lb
    q = [a for a in asks(con) if a['topic'] != skip]
    if not q:
        return None
    a = q[0]
    items = [{'type': 'action', 'action': {'type': 'message', 'label': o[:20], 'text': f"【{a['topic']}】{o}"}} for o in a.get('options', [])][:12]
    items.append({'type': 'action', 'action': {'type': 'postback', 'label': '✏️ 我打字回答', 'data': f"a=ask_type&t={a['topic']}", 'displayText': f"【{a['topic']}】我打字回答"}})
    left = len(q) - 1
    bs = [{'type': 'button', 'action': {'type': 'message', 'label': o[:20], 'text': f"【{a['topic']}】{o}"}, 'style': 'primary' if i == 0 else 'secondary',
           'color': lb.BROWN if i == 0 else '#EFEDE8', 'height': 'sm'} for i, o in enumerate(a.get('options', [])[:12])] + [lb._btn('✏️ 我打字回答', data=f"a=ask_type&t={a['topic']}")]
    m = lb.flex(f"請決定：{a['topic']}", lb._bubble(lb._header(f'請決定 🗳（還有 {left} 題）', a['topic'][:30], '#C47F00'), [lb._t(a.get('question', '')[:1800], wrap=True, size='sm')], bs,
                                                    '點一個選項；要自己寫就按「✏️ 我打字回答」再打字。'))[0]
    m['quickReply'] = {'items': items[:13]}        # 稽核：改成卡片按鈕（快速回覆會被下一則訊息蓋掉），快速回覆照樣附上
    return m

def review_cards(con, limit=3):
    """待審核的卡片重新傳（最舊的先）。"""
    import line_bot as lb, reviews
    out = []
    for r in reviews.pending(con)[:limit]:
        out.append({'type': 'text', 'text': f"【送審】{r['title']}\n\n{r['body']}"[:4900]})
        out += lb.flex(f"請審核：{r['title']}", lb._bubble(lb._header(f"請審核・{r['category']}", r['title'][:40], '#0A84FF'),
                       [lb._t('內容在上一則訊息，看完請點選：', wrap=True, color=lb.SUB, size='sm')],
                       [lb._btn('✅ 核准', data=f"a=rv_ok&r={r['id']}", primary=True), lb._btn('✏️ 要修改（打字說明）', data=f"a=rv_edit&r={r['id']}")]
                       + ([] if r['category'] in reviews.NO_AUTO else [lb._btn('🟢 核准，這類以後不用審核', data=f"a=rv_auto&r={r['id']}")])))      # 稽核：群發、行銷內容不能免審
    return out[:5]
