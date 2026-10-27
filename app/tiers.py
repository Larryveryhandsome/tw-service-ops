"""派工分級：新單店長想發給阿瑋（客戶12，做事認真積極），但系統開單 40 秒就把 17 位全問了，文彥先接走。

規則
- 夥伴分 A／B／C，由店長排（LINE 點選卡，或管理層打「派工分級」叫出來）；沒排的算 B。
- 新單缺人：先等 10 分鐘（讓管理層手動指定，例如直接派給阿瑋）→ 業主指定／常配合的先問 → A 級 → B 級 → C 級。
- 每一級等多久，依「開單到開工還有多久」推算：等待時間＝開單到開工 ÷ 6，最短 30 分鐘、最長 24 小時；開工前 2 小時內＝全部一起問。
  例：下週要掃 → A 等一天、第二天問 B、第三天問 C；3 天後 → 各 12 小時；明天 → 各 4 小時；6 小時後 → 各 1 小時。
- 這一級問的人全部回覆了（婉拒）→ 不用等，直接問下一級；這一級沒有可問的人 → 直接下一級。
- 還沒輪到的等級看不到、也接不到這筆（只有被問到的人看得到）。
- 安靜時段（22:00–08:00）不發，等 08:00（明早 10 點前開工的除外）。
- 管理層在中控台「勾選名單」指定（只有他們看得到）→ 照原本規則不自動發給別人：婉拒／逾時通知管理群改派，服務前 3 天由第二輪接手。
  只有「不限名單」的手動邀請（例：多邀一位）才算第一輪 M，等滿等待時間後接著問還沒問過的等級。
- 三級都問完還缺人 → 照原本：服務前 3 天再問一次＋派單管理群提醒。
"""
import datetime as dt
import json
import core as c
import integrations as ix

TIERS = ('A', 'B', 'C')
ALL = ('A', 'B', 'C', 'D')          # D＝沒配合過的新人（tier_rules 打開才用）
KEY = 'worker_tiers'
CKEY = 'customer_tiers'             # 客戶 A／B／C（店長手動設；沒設＝B）
GRACE_MIN = 10

def jobs(con, wid):
    """做過幾場（完工／封存的接單）。"""
    return con.execute("SELECT COUNT(*) FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' "
                       "AND o.status IN ('completed','archived')", (wid,)).fetchone()[0]

def rules_on(con):
    return ix.setting(con, 'tier_rules') == '1'

def batch_size(con):
    try:
        return max(0, int(ix.setting(con, 'tier_batch') or 0))
    except ValueError:
        return 0

def all_ctiers(con):
    try:
        return json.loads(ix.setting(con, CKEY) or '{}') or {}
    except ValueError:
        return {}

def cust_tier(con, cid):
    t = all_ctiers(con).get(cid or '', '')
    return t if t in TIERS else 'B'

def set_cust_tier(con, cid, t, by):
    if t not in TIERS:
        raise ValueError('客戶等級只能是 A、B、C')
    cu = con.execute('SELECT name FROM customers WHERE id=?', (cid,)).fetchone()
    if not cu:
        raise ValueError('找不到這位客戶')
    d = all_ctiers(con); old = d.get(cid, ''); d[cid] = t
    ix.set_setting(con, CKEY, json.dumps(d, ensure_ascii=False))
    c.audit(con, 'customer_tier', 'customers', cid, before={'tier': old}, after={'tier': t, 'by': by})
    return cu['name']

def first_time(con, cid):
    return not cid or not con.execute("SELECT 1 FROM orders WHERE customer_id=? AND status IN ('completed','archived') LIMIT 1", (cid,)).fetchone()

def route(con, o):
    """這張單先問哪一級（負責人 重要的、初次的給 A；利潤低的給 B、C）。"""
    if ix.setting(con, 'tier_route') != '1':
        return TIERS
    ct = 'A' if first_time(con, o['customer_id']) else cust_tier(con, o['customer_id'])
    return {'A': ('A', 'B', 'C'), 'B': ('B', 'C', 'A'), 'C': ('C', 'B', 'A')}[ct]

def d_ok(con, o):
    """D 級新人：2 人以上、已經有 A／B 接了，才邀請一起做（試做）。"""
    if not rules_on(con) or (o['required_workers'] or 1) < 2:
        return False
    cache = all_tiers(con)
    return any(tier(con, r[0], cache) in ('A', 'B') for r in con.execute("SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)))

def all_tiers(con):
    try:
        return json.loads(ix.setting(con, KEY) or '{}') or {}
    except ValueError:
        return {}

def tier(con, wid, cache=None):
    t = (cache if cache is not None else all_tiers(con)).get(wid, '')
    if t in ALL:
        return t
    return 'D' if rules_on(con) and not jobs(con, wid) else 'B'      # 沒排過的——做過我們的案子＝B、沒做過＝D（規則打開才算 D）

def set_tier(con, wid, t, by):
    if t not in (ALL if rules_on(con) else TIERS):      # D 級要管理群同意新規則（tier_rules）才開
        raise ValueError('等級只能是 A、B、C' + ('、D' if rules_on(con) else ''))
    w = con.execute('SELECT name FROM workers WHERE id=?', (wid,)).fetchone()
    if not w:
        raise ValueError('找不到這位夥伴')
    d = all_tiers(con); old = d.get(wid, ''); d[wid] = t
    ix.set_setting(con, KEY, json.dumps(d, ensure_ascii=False))
    c.audit(con, 'worker_tier', 'workers', wid, before={'tier': old}, after={'tier': t, 'by': by})
    return w['name']

def _utc(s):
    """dispatch_log.at／orders.created_at：'YYYY-MM-DD HH:MM:SS'（UTC）或 ISO（有時區）。"""
    s = (s or '').strip()
    try:
        d = dt.datetime.fromisoformat(s.replace(' ', 'T'))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)

def window(lead):
    """每一級等多久（timedelta）。lead＝開單到開工。"""
    if lead <= dt.timedelta(hours=2):
        return dt.timedelta(0)
    return min(dt.timedelta(hours=24), max(dt.timedelta(minutes=30), lead / 6))

def grace(lead):
    return dt.timedelta(0) if lead <= dt.timedelta(hours=6) else dt.timedelta(minutes=GRACE_MIN)

def plan_text(lead):
    w = window(lead)
    if not w:
        return '開工前 2 小時內：A／B／C 一起問'
    h = w.total_seconds() / 3600
    each = f'約 {round(h):g} 小時' if h >= 1 else f'{int(w.total_seconds() // 60)} 分鐘'
    return f'先問 A 級，每一級等 {each}，沒人接再問 B 級、C 級'

def _waves(con, oid):
    """已經問過的輪次：[(輪, 時間, 問了幾位)]。輪：P 業主指定／常配合、M 管理層手動、A、B、C。"""
    out = []
    for at, d in con.execute("SELECT at,detail FROM dispatch_log WHERE order_id=? AND action='tier_ask' ORDER BY id", (oid,)):
        n = 0
        for part in (d or '').split('問了 ')[1:2]:
            n = int(''.join(ch for ch in part.split(' ')[0] if ch.isdigit()) or 0)
        out.append(((d or '?')[0], _utc(at), n))
    return out

def _pick(con, o, wave):
    import automation, dispatch
    cands = [x for x in dispatch.candidates(con, o['id']) if not x['blocked'] and automation.reachable(con, x['worker_id'])]
    if wave == 'P':
        pref = set(dispatch.preferred_workers(con, o))
        return [x['worker_id'] for x in cands if x['worker_id'] in pref]
    cache = all_tiers(con)
    ids = [x['worker_id'] for x in cands if tier(con, x['worker_id'], cache) == wave]
    return sorted(ids, key=lambda w: -jobs(con, w)) if batch_size(con) else ids      # 接過的案件數多的先問

def _stamp(con, oid, now):
    con.execute("UPDATE dispatch_log SET at=? WHERE id=(SELECT MAX(id) FROM dispatch_log WHERE order_id=? AND action='tier_ask')",
                (now.astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), oid))

def reask(con, o):
    """另外派工（請假、業主選另外派人）也走分級分批，不再一次問全部。回傳這次問了幾位。"""
    from board import log
    log(con, o['id'], 'tier_ask', 'R｜另外派工（照分級）：問了 0 位')
    before = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='invited'", (o['id'],)).fetchone()[0]
    step(con, o, force=True)
    return con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='invited'", (o['id'],)).fetchone()[0] - before

def step(con, o, now=None, force=False):
    """ask_shortage 每分鐘呼叫（第一輪）：到時間就問下一級。回傳 'asked'／'wait'／'done'。"""
    import dispatch
    from board import log
    now = now or dt.datetime.now(dt.timezone.utc)
    created = _utc(o['created_at']) or now
    start = dt.datetime.fromisoformat(f"{o['date']}T{o['start'] or '09:00'}:00").replace(tzinfo=c.TZ)
    lead = max(dt.timedelta(0), start - created)
    w = min(window(lead), window(max(dt.timedelta(0), start - now)))      # 稽核 L3：被工具等待、管理層擋住延後時，最後一級不會拖到開工前
    waves = _waves(con, o['id'])
    if not waves:
        manual = con.execute("SELECT MIN(at) FROM dispatch_log WHERE order_id=? AND action IN ('invite','publish')", (o['id'],)).fetchone()[0]
        if manual and con.execute("SELECT 1 FROM assignments WHERE order_id=? AND status IN ('invited','accepted')", (o['id'],)).fetchone():
            n = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status IN ('invited','accepted','declined')", (o['id'],)).fetchone()[0]
            log(con, o['id'], 'tier_ask', f'M｜管理層手動邀請：問了 {n} 位')
            con.execute("UPDATE dispatch_log SET at=? WHERE id=(SELECT MAX(id) FROM dispatch_log WHERE order_id=? AND action='tier_ask')", (manual, o['id']))
            return 'wait'
    bs = batch_size(con)
    wb = max(dt.timedelta(minutes=20), w / 3) if bs and w else w          # 分批問 → 每批等「每級等待」的 1/3（最少 20 分鐘）
    due = created + grace(lead)
    done = {x[0] for x in waves}
    for wv, at, n in waves:
        pending = con.execute("SELECT 1 FROM assignments WHERE order_id=? AND status='invited'", (o['id'],)).fetchone()
        due = max(due, (at or now) + (wb if n and pending else dt.timedelta(0)))
    if force:
        due = now
    for wv in ('P',) + route(con, o) + (('D',) if d_ok(con, o) else ()):
        if wv in done and (wv == 'P' or not bs):
            continue
        if now < due:
            return 'wait'
        ids = _pick(con, o, wv)
        if wv in done and not ids:
            continue                                  # 分批：這一級都問過了
        if bs and wv != 'P':
            ids = ids[:bs]
        if wv == 'P' and not ids:
            log(con, o['id'], 'tier_ask', 'P｜沒有業主指定／常配合的夥伴：問了 0 位'); _stamp(con, o['id'], now); done.add(wv); continue
        if ids:
            c.CTX.actor = '自動詢問缺人（分級）'
            try:
                dispatch.auto_dispatch(con, {'order_id': o['id'], 'worker_ids': ids, 'exclusive': True, 'batch': len(ids)})
            except ValueError as e:          # 稽核 H3：這張單資料不齊（例：沒案場）→ 只跳過這張、通知一次，不影響其他單
                if not ix.setting(con, f"tier_err:{o['id']}"):
                    ix.set_setting(con, f"tier_err:{o['id']}", c.now())
                    import line_bot as lb
                    lb.admin_notify(con, '⚠️ 這張單沒辦法自動找人', [('工作', lb.when(dict(o))), ('原因', str(e)[:80]), ('處理', '請在中控台補齊資料或手動派工')], '/#dispatch', '#C62828', oid=o['id'])
                return 'wait'
        k = sum(1 for x in waves if x[0] == wv) + 1
        name = 'P｜業主指定／常配合' if wv == 'P' else f'{wv} 級' + (f'（第 {k} 批）' if bs and k > 1 else '')
        log(con, o['id'], 'tier_ask', f"{name}：問了 {len(ids)} 位" + (f"（{plan_text(lead)}）" if wv == 'A' else ''))
        _stamp(con, o['id'], now)
        done.add(wv)
        if ids and w:
            return 'asked'
    log(con, o['id'], 'auto_ask', 'A／B／C 都問過了（分級派工）')
    return 'done'

def cards(con):
    """給店長排等級：可以推播到的夥伴（綁了官方 LINE），一人一張，按 A／B／C。"""
    import line_bot as lb, automation
    cache = all_tiers(con)
    ws = [dict(w) for w in con.execute("SELECT * FROM workers WHERE status='active' AND name NOT LIKE '（示範）%' ORDER BY name") if automation.reachable(con, w['id'])]
    ws.sort(key=lambda w: ({'A': 0, 'B': 1, 'C': 2}.get(cache.get(w['id'], ''), 3), w['name']))
    bubbles = []
    for w in ws:
        jobs = con.execute("SELECT COUNT(*), MAX(o.date) FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' "
                           "AND o.status IN ('completed','archived','confirmed')", (w['id'],)).fetchone()
        cur = cache.get(w['id'], '')
        bubbles.append(lb._bubble(lb._sh('派工分級', w['name'], {'A': '#2E7D32', 'B': '#0A84FF', 'C': '#8E8E93'}.get(cur, '#5A3A22')),
            [lb._row('現在', f'{cur} 級' if cur else '還沒排（算 B）'), lb._row('做過', f"{jobs[0]} 場" + (f"（最近 {jobs[1][5:].replace('-', '/')}）" if jobs[1] else '')),
             lb._row('區域', (w['regions'] or '—')[:30])],
            [lb._btn(f'{t} 級' + (' ✓' if cur == t else ''), data=f"a=tier&w={w['id']}&t={t}", primary=(t == 'A')) for t in (ALL if rules_on(con) else TIERS)]))
    head = {'type': 'text', 'text': ('📋 派工分級（請店長排）：新單會先問 A 級，沒人接再問 B 級、最後 C 級；離開工越久，每一級等越久（下週的單：A 一天、B 一天、第三天 C）。\n'
                                     f'下面是綁了官方 LINE、推播得到的 {len(ws)} 位，一人一張，按 A／B／C 就好；沒排的算 B。之後要改，打「派工分級」再叫出來。')}
    out = [head]
    for i in range(0, len(bubbles), 12):
        out += lb._carousel('派工分級', bubbles[i:i + 12])
    return out[:5]

UP = {'D': 'C', 'C': 'B'}

def upgrade_check(con):
    """D／C 級跟 A／B 級一起完成 2 人以上案場 → 卡片問店長要不要升一級（每天一次；同一位同一級只問一次）。tier_rules 打開才跑。"""
    if not rules_on(con):
        return 0
    today = dt.datetime.now(c.TZ).date().isoformat()
    if ix.setting(con, 'tier_up_day') == today:
        return 0
    ix.set_setting(con, 'tier_up_day', today)
    import line_bot as lb
    cache = all_tiers(con); n = 0
    for w in con.execute("SELECT id,name FROM workers WHERE status='active' AND name NOT LIKE '（示範）%'").fetchall():
        cur = tier(con, w['id'], cache); to = UP.get(cur)
        if not to or ix.setting(con, f"tier_up_asked:{w['id']}:{to}"):
            continue
        r = con.execute("SELECT o.id,o.date FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.status IN ('completed','archived') "
                        "AND COALESCE(o.required_workers,1)>=2 AND o.date>=? ORDER BY o.date DESC", (w['id'], (dt.date.today() - dt.timedelta(days=60)).isoformat())).fetchall()
        mates = []
        for oid, day in r:
            mates += [m[1] for m in con.execute("SELECT a.worker_id,w2.name FROM assignments a JOIN workers w2 ON w2.id=a.worker_id WHERE a.order_id=? AND a.status='accepted' AND a.worker_id!=?",
                                                (oid, w['id'])) if tier(con, m[0], cache) in ('A', 'B')]
            if mates:
                break
        if not mates:
            continue
        topic = f"夥伴升級 {w['name']}"
        ix.set_setting(con, f"tier_up_asked:{w['id']}:{to}", c.now()); ix.set_setting(con, f'askref:{topic}', f"{w['id']}|{to}")
        lb.ask_staff(con, topic, f"{w['name']} 現在 {cur} 級，跟 {'、'.join(dict.fromkeys(mates))}（A／B 級）一起完成過 2 人以上的案場（{day}）。\n照客服定的規則，要升成 {to} 級嗎？\n\n"
                     f"做過 {jobs(con, w['id'])} 場。升了之後新單會照 {to} 級的順序問他。", [f'升 {to} 級', '先不要'], ping=n == 0)
        n += 1
        if n >= 5:
            break                                     # 一天最多問 5 位（不洗版）
    return n

def upgrade_answer(con, topic, ans, who):
    ref = ix.setting(con, f'askref:{topic}') or ''
    wid, _, to = ref.partition('|')
    if not wid or to not in ALL:
        return ''
    if '先不要' in ans or '不' in ans:
        return '好，先不升級（之後有新的一起做的紀錄不會再問同一級）'
    name = set_tier(con, wid, to, who)
    return f'✅ {name} 已升成 {to} 級'
