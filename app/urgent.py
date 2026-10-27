"""緊急加派與業主緊急回報（業主突然改時間，要有緊急加派或調整的選項；業主在服務時間內打開官方 LINE 要能緊急回報）。

・緊急加派一位 add_one：人數＋1、派工注意寫🚨、原本的人照常（已排定的退回補人中，原本的人照常出發、看得到地址）；
  照分級先問 A 級，沒人接每 10 分鐘問下一級（B、C）；有人接 → 直接可以打卡，管理群收到「誰接了」。結束前 1 小時還沒人接就停，管理群收到。
  入口：中控台工單「🚨 緊急加派一位」、業主改時間的管理群卡「改時間＋緊急加派」、改了時間的「人力要調整嗎？」卡。
・業主緊急回報：服務時間（開始前 1 小時～結束後 1 小時）內，到場卡、按「真人客服」、打「緊急」或傳任何訊息（每場一次）→ 緊急回報卡
  （服務人員還沒到／需要加時／現場有狀況／找專人）→ 派單管理群 @管理層，回業主「已通知」。
"""
import datetime as dt
import json
import core as c
import integrations as ix

WAVE_MIN = 10
KINDS = {'late': '服務人員還沒到／會遲到嗎', 'extra': '需要加時', 'issue': '現場有狀況', 'human': '找專人'}

def _now():
    return dt.datetime.now(c.TZ)

def add_one(con, oid, by, why='緊急加派'):
    """緊急加派一位。回傳說明。"""
    import board, line_bot as lb
    o = c.get(con, 'orders', oid)
    if o['status'] not in ('review', 'draft', 'confirmed'):
        raise ValueError('這場已經完工或取消了')
    if ix.setting(con, f'urgent:{oid}'):                # 每日檢查：多按一次不要再多加一位
        return f"這場已經在緊急加派中（目前 {o['required_workers'] or 1} 位），有人接會通知這裡。"

    end = dt.datetime.fromisoformat(f"{o['date']}T{o['end'] or '18:00'}").replace(tzinfo=c.TZ)
    if end - _now() < dt.timedelta(hours=1):
        raise ValueError('離結束不到 1 小時，來不及加派')
    n = (o['required_workers'] or 1) + 1
    note = f"🚨緊急支援：{why}，請盡快到；" + (o['dispatch_note'] or '')
    con.execute("UPDATE orders SET required_workers=?,status=CASE WHEN status='confirmed' THEN 'review' ELSE status END,confirmed_at=CASE WHEN status='confirmed' THEN '' ELSE confirmed_at END,"
                "dispatch='open',dispatch_only=1,dispatch_note=?,version=version+1 WHERE id=?", (n, note[:300], oid))
    _go(con, oid)                                     # 原本的人照常出發、看得到地址、可以打卡（新時段有別場的不算）
    board.log(con, oid, 'urgent_add', f"{n - 1}→{n} 位（{by}：{why}）")
    c.audit(con, 'urgent_add', 'orders', oid, after={'required_workers': n, 'by': by, 'why': why})
    ix.set_setting(con, f'urgent:{oid}', json.dumps({'at': _now().isoformat(timespec='seconds'), 'by': by, 'waves': [], 'need': n, 'orig': n - 1,
                                                    'only': o['dispatch_only'] or 0, 'status': o['status']}, ensure_ascii=False))
    asked = _wave(con, oid)
    return f"🚨 已緊急加派：人數改成 {n} 位，先問了 A 級 {asked} 位（先按先得）；沒人接每 {WAVE_MIN} 分鐘問下一級，有人接會通知這裡。"

def _go(con, oid):
    """照常出發的名單：已接、這個時段沒有別場、沒回報不能排。"""
    import dispatch
    o = c.get(con, 'orders', oid); rest = []
    for (wid,) in con.execute("SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)).fetchall():
        busy = any(a['kind'] == 'busy' and (not a['start'] or (a['start'] < o['end'] and a['end'] > o['start']))
                   for a in c.rows(con, 'SELECT * FROM availability WHERE worker_id=? AND date=?', (wid, o['date'])))
        if not dispatch._clash(con, wid, o) and not busy:
            rest.append(wid)
    con.execute('UPDATE orders SET go_partial=? WHERE id=?', ((',' + ','.join(rest) + ',') if rest else '', oid))
    return rest

def _undo(con, oid, st):
    """沒人接、來不及 → 人數改回原本、取消「只派指定」；原本已排定且人數夠就回到已排定。"""
    o = c.get(con, 'orders', oid)
    orig = int(st.get('orig') or max(1, (o['required_workers'] or 1) - 1))
    crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (oid,)).fetchone()[0]
    back = st.get('status') == 'confirmed' and crew >= orig
    note = o['dispatch_note'] or ''
    if note.startswith('🚨緊急支援：'):
        note = note.split('；', 1)[1] if '；' in note else ''
    con.execute("UPDATE orders SET required_workers=?,dispatch_only=?,dispatch=?,dispatch_note=?" + (",status='confirmed',confirmed_at=?" if back else '') + ",version=version+1 WHERE id=?",
                (orig, int(st.get('only') or 0), 'filled' if crew >= orig else 'open', note) + ((c.now(),) if back else ()) + (oid,))
    con.execute("UPDATE assignments SET status='cancelled',notes=COALESCE(notes,'')||'（緊急加派結束）' WHERE order_id=? AND status='invited'", (oid,))
    import board; board.log(con, oid, 'urgent_undo', f'人數改回 {orig} 位')
    return orig

def _wave(con, oid):
    """問下一級（A → B → C）；回傳問了幾位。"""
    import tiers, dispatch
    st = json.loads(ix.setting(con, f'urgent:{oid}') or '{}')
    o = c.get(con, 'orders', oid)
    for wv in tiers.route(con, o):
        if wv in st.get('waves', []):
            continue
        st.setdefault('waves', []).append(wv)
        ids = [i for i in tiers._pick(con, o, wv)
               if not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status IN ('invited','accepted','declined')", (oid, i)).fetchone()]
        st['last'] = _now().isoformat(timespec='seconds')
        ix.set_setting(con, f'urgent:{oid}', json.dumps(st, ensure_ascii=False))
        if ids:
            c.CTX.actor = '緊急加派（分級）'
            dispatch.auto_dispatch(con, {'order_id': oid, 'worker_ids': ids, 'exclusive': True, 'batch': len(ids)})
            import board; board.log(con, oid, 'tier_ask', f'{wv} 級（緊急加派）：問了 {len(ids)} 位')
            return len(ids)
    return 0

def tick(con):
    """每分鐘：緊急加派有人接 → 可以打卡＋通知；沒人接每 10 分鐘問下一級；來不及就停。"""
    import board, line_bot as lb
    n = 0
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'urgent:%' AND value!=''").fetchall():
        oid = k[7:]
        try:
            st = json.loads(v)
        except ValueError:
            continue
        o = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not o or o['status'] in ('cancelled', 'archived', 'completed'):
            ix.set_setting(con, k, ''); continue
        crew = c.rows(con, "SELECT a.worker_id,w.name,a.notes FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (oid,))
        if (o['required_workers'] or 1) < int(st.get('need') or 0):
            _undo(con, oid, {**st, 'orig': o['required_workers'] or 1})      # 每日檢查：減少人數＝不加派了 → 原本已排定的回到已排定、取消「只派指定」
            ix.set_setting(con, k, ''); continue        # 每日檢查：不要通知「有人接了」
        if len(crew) >= (o['required_workers'] or 1):
            if o['status'] != 'confirmed':
                board.set_go_partial(con, oid)        # 新接的人也照常出發：工作頁打卡、拍照
            lb.admin_notify(con, '✅ 緊急加派有人接了', [('工作', lb.when(dict(o))), ('夥伴', '、'.join(x['name'] for x in crew)), ('說明', '新接的人已經可以打卡、看地址')], '', '#2E7D32', oid=oid)
            ix.set_setting(con, k, ''); n += 1; continue
        end = dt.datetime.fromisoformat(f"{o['date']}T{o['end'] or '18:00'}").replace(tzinfo=c.TZ)
        if end - _now() < dt.timedelta(hours=1):
            orig = _undo(con, oid, st)                 # 每日檢查：自動改回原本人數（以前留著多一位，完工跳缺人、請假不自動補）
            lb.admin_notify(con, '⚠️ 緊急加派沒人接（離結束不到 1 小時）', [('工作', lb.when(dict(o))), ('人數', f'已自動改回 {orig} 位'), ('處理', '還需要人就直接打電話找')], '', '#C62828', oid=oid)
            ix.set_setting(con, k, ''); n += 1; continue
        last = dt.datetime.fromisoformat(st.get('last') or st.get('at'))
        if _now() - last >= dt.timedelta(minutes=WAVE_MIN):
            if not _wave(con, oid) and len(st.get('waves', [])) >= 3:
                st['last'] = _now().isoformat(timespec='seconds'); ix.set_setting(con, k, json.dumps(st, ensure_ascii=False))
            n += 1
    return n

def staff_card(con, oid, title='📅 時間改了，人力要調整嗎？'):
    """改了時間（業主申請或中控台）→ 管理群：緊急加派一位／減少人數／不用調整。"""
    import line_bot as lb
    o = c.get(con, 'orders', oid)
    crew = c.rows(con, "SELECT w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (oid,))
    btns = [lb._btn('🚨 緊急加派一位', data=f'a=urg_add&o={oid}', primary=True)]
    if (o['required_workers'] or 1) > 1:
        btns.append(lb._btn('減少人數', data=f'a=crew_cutm&o={oid}'))
    btns.append(lb._btn('打開工單', uri=f'{lb.BASE_URL}/#o/{oid}'))
    return lb.flex(f'{title}：{lb.when(o)}', lb._bubble(lb._header(title, lb.when(o), '#C47F00'),
        [lb._row('人數', f"{o['required_workers'] or 1} 位"), lb._row('目前', '、'.join(x['name'] for x in crew) or '還沒有人')], btns,
        '新時間原本的人可能不行，或現場需要多一個人：按「緊急加派一位」，系統照分級找人、先按先得。'))

def active_order(con, cid, now=None):
    """這位業主現在服務中的那一場（開始前 1 小時～結束後 1 小時）。"""
    if not cid:
        return None
    now = now or _now()
    for o in c.rows(con, "SELECT * FROM orders WHERE customer_id=? AND date=? AND status IN ('review','draft','confirmed','completed') ORDER BY start", (cid, now.date().isoformat())):
        try:
            s = dt.datetime.fromisoformat(f"{o['date']}T{o['start']}").replace(tzinfo=c.TZ); e = dt.datetime.fromisoformat(f"{o['date']}T{o['end']}").replace(tzinfo=c.TZ)
        except ValueError:
            continue
        if s - dt.timedelta(hours=1) <= now <= e + dt.timedelta(hours=1):
            return o
    return None

def customer_card(con, o):
    """業主緊急回報卡。"""
    import line_bot as lb
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {})
    return lb.flex('服務進行中，需要協助嗎？', lb._bubble(lb._header('服務進行中 🧹', f"今天 {o['start']}–{o['end']}", brand=lb.brand_of(cu)),
        [lb._t('有任何狀況，點一下我們馬上處理：', wrap=True)],
        [lb._btn(KINDS['late'], data=f"a=urg&o={o['id']}&k=late", primary=True), lb._btn(KINDS['extra'], data=f"a=urg&o={o['id']}&k=extra"),
         lb._btn(KINDS['issue'], data=f"a=urg&o={o['id']}&k=issue"), lb._btn(KINDS['human'], data=f"a=urg&o={o['id']}&k=human")],
        '也可以直接在這裡打字告訴我們。'))

def offer_once(con, acc, uid, cid):
    """業主在服務時間傳訊息 → 推一次緊急回報卡（每場一次）。"""
    import line_bot as lb
    o = active_order(con, cid)
    if not o or ix.setting(con, f"urg_offer:{o['id']}"):
        return False
    ix.set_setting(con, f"urg_offer:{o['id']}", c.now())
    m = customer_card(con, o)
    lb.queue(con, acc, uid, 'urgent_offer', m[0]['altText'], m, o['id'])
    return True

def customer_report(con, acc, uid, lu, oid, kind):
    """業主按了緊急回報 → 管理群 @管理層；回業主一句話。"""
    import line_bot as lb, board
    o = con.execute('SELECT * FROM orders WHERE id=?', (oid or '',)).fetchone()
    if not o or ((lu or {}).get('customer_id') != o['customer_id'] and uid not in lb.test_users(con)):
        return '收到，專人馬上跟您聯絡 🙏'
    act = active_order(con, o['customer_id'])
    if not act or act['id'] != o['id']:                # 每日檢查：舊卡片、服務已結束 → 不再 @管理層
        return '這場服務已經結束了，有任何問題直接在這裡留言，專人會回覆您 🙏'
    key = f'urg_rep:{oid}:{kind}'; last = ix.setting(con, key) or ''
    if last and last >= (_now() - dt.timedelta(minutes=10)).isoformat(timespec='seconds'):
        import board; board.log(con, oid, 'customer_urgent', f'{KINDS.get(kind, "緊急協助")}（10 分鐘內重按）')
        return '收到 🙏 已經通知專人了，正在處理中。'
    ix.set_setting(con, key, _now().isoformat(timespec='seconds'))
    o = dict(o); what = KINDS.get(kind, '緊急協助')
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {})
    crew = c.rows(con, "SELECT w.name,w.phone,k.arrived_at FROM assignments a JOIN workers w ON w.id=a.worker_id LEFT JOIN checkins k ON k.assignment_id=a.id WHERE a.order_id=? AND a.status='accepted'", (o['id'],))
    cs = '、'.join(f"{x['name']}（{'已到 ' + x['arrived_at'][11:16] if x['arrived_at'] else '還沒打卡'}）" for x in crew) or '還沒有人'
    m = lb.flex(f'🚨 業主緊急回報：{what}', lb._bubble(lb._header('🚨 業主緊急回報', what, '#C62828'),
        [lb._row('業主', f"{cu.get('name', '')}（{cu.get('phone') or '—'}）"), lb._row('工作', lb.when(o)), lb._row('夥伴', cs)],
        [lb._btn('🚨 緊急加派一位', data=f"a=urg_add&o={o['id']}", primary=kind in ('extra', 'issue')), lb._btn('打開工單', uri=f"{lb.BASE_URL}/#o/{o['id']}")],
        '請馬上打給業主或夥伴；處理完在工單紀錄寫一下。'))
    lb.to_managers(con, 'urgent_c', m[0]['altText'], m, o['id'], tag=f'🚨 業主緊急回報：{what}，請馬上處理 👇')
    board.log(con, o['id'], 'customer_urgent', what)
    return {'late': '收到 🙏 我們馬上確認服務人員的位置，會盡快回覆您。', 'extra': '收到 🙏 專人馬上跟您確認加時（時間、費用）。',
            'issue': '收到 🙏 專人馬上處理，也可以直接打字或傳照片告訴我們狀況。', 'human': '收到 🙏 專人馬上跟您聯絡。'}.get(kind, '收到，專人馬上跟您聯絡 🙏')
