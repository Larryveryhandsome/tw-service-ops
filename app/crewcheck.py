"""工具雙向確認＋出勤確認＋派工距離（排 ②③⑥⑦）。

・工具：業主那邊（案場 tools：齊全／基本／沒有＋「現場有：…」勾選或打字）對夥伴自備工具（夥伴主控板勾一次）→ 算這場還缺什麼。
  前一天提醒卡：缺工具的「收到」改成「我會帶齊：…，確認出勤」，另一顆「工具不夠，請公司協助」→ 管理群；只按舊的「收到」不算確認出勤。
・出勤確認：前一天 18:00 提醒卡要按確認；21:00 還沒確認的 → 管理群一則名單（一天一次）、中控台「管理群待處理」列異常。
・派工排序：加上工具（自備齊的優先）和距離（夥伴住處；沒填就用他最近做過的案場位置）。
"""
import datetime as dt
import math
import re
import core as c
import integrations as ix

ITEMS = ('基本工具組', '專業工具', '量測工具', '延長線', '手機／平板', '耗材', '防護用品', '推車', '收納箱')
NEED = ('基本工具組', '耗材', '防護用品')         # 一般服務必備（吸塵器看現場）
ALIAS = {'工具組': '基本工具組', '工具箱': '基本工具組', '專業工具': '專業工具', '量測': '量測工具', '延長線': '延長線', '平板': '手機／平板', '耗材': '耗材', '防護': '防護用品', '推車': '推車', '收納箱': '收納箱'}
CONFIRM_AT = '21:00'

def _items_in(text):
    """從文字找出有提到的工具；「沒有吸塵器」「無拖把」這種不算。"""
    out = set()
    for k, v in ALIAS.items():
        for m in re.finditer(k, text or ''):
            if not re.search(r'(沒有|沒|無|缺|不用)\s*$', (text or '')[max(0, m.start() - 3):m.start()]):
                out.add(v)
    return out

def site_has(s):
    """案場有的工具；還沒問過回 None。"""
    s = s or {}
    t = s.get('tools') or ''
    if not t:
        return None
    base = set(ITEMS) if t == 'full' else {'基本工具組'} if t == 'basic' else set()
    return base | _items_in(s.get('tools_note') or '')

def worker_has(w):
    """夥伴自備的工具；沒填回 None。"""
    v = ((w or {}).get('tools_own') or '').strip()
    if not v:
        return None
    return set() if v == '都沒有' else {x for x in re.split(r'[、,，\s]+', v) if x in ITEMS} | _items_in(v)

def needs_tools(con, o):
    import sitetools
    return sitetools.order_needs(con, o) and not str(o.get('title') or '').startswith(('垃圾代丟', '垃圾清收'))

def missing(con, o, wid):
    """這場、這位夥伴還缺的必備工具（案場有的＋他自備的都沒有）。不用工具的服務回 []。"""
    o = dict(o)
    if not needs_tools(con, o):
        return []
    s = dict(con.execute('SELECT * FROM sites WHERE id=?', (o.get('site_id') or '',)).fetchone() or {})
    w = dict(con.execute('SELECT * FROM workers WHERE id=?', (wid or '',)).fetchone() or {})
    have = (site_has(s) or set()) | (worker_has(w) or set())
    need = list(NEED) + (['吸塵器'] if '吸塵器' in (s.get('tools_note') or '') + (o.get('scope') or '') and '沒有吸塵器' not in (s.get('tools_note') or '') else [])
    return [x for x in need if x not in have]

def tools_ok(con, oid, wid):
    return bool(ix.setting(con, f'tool_ack:{oid}:{wid}'))

def _akey(con, oid, wid):
    o = con.execute('SELECT date FROM orders WHERE id=?', (oid,)).fetchone()
    return f"att_ack:{oid}:{wid}:{o[0] if o else ''}"

def acked(con, oid, wid):
    """這位夥伴有沒有確認出勤：前一天提醒卡按確認、或管理層電話確認（每日檢查：綁日期，改期後要重新確認；不再用姓名比對紀錄）。"""
    return bool(ix.setting(con, _akey(con, oid, wid)))

def mark_ack(con, oid, wid, how):
    ix.set_setting(con, _akey(con, oid, wid), f'{c.now()}｜{how}')

def ack(con, oid, wid, tools_flag):
    """夥伴按確認。缺工具又不是按「我會帶齊」→ 回 (False, 說明)。"""
    o = c.get(con, 'orders', oid)
    miss = missing(con, o, wid)
    if miss and not tools_flag and not tools_ok(con, oid, wid):
        return False, f"這場還缺：{'、'.join(miss)}。\n請按卡片上的「我會帶齊，確認出勤」；帶不齊請按「工具不夠，請公司協助」🙏"
    if miss and tools_flag:
        ix.set_setting(con, f'tool_ack:{oid}:{wid}', c.now())
    mark_ack(con, oid, wid, '夥伴按確認')
    return True, ''

def lack(con, oid, wid):
    """夥伴按「工具不夠，請公司協助」→ 管理群。"""
    import line_bot as lb, board
    o = c.get(con, 'orders', oid)
    w = c.get(con, 'workers', wid)
    miss = missing(con, o, wid)
    ix.set_setting(con, f'tool_lack:{oid}:{wid}', c.now())
    board.log(con, oid, 'tool_lack', f"{w['name']}：{'、'.join(miss)}")
    lb.admin_notify(con, f"🧹 {w['name']} 工具不夠", [('工作', lb.when(dict(o))), ('缺', '、'.join(miss) or '（他說不夠）'),
                    ('處理', '請準備好給他（或請業主準備）；處理完在中控台「管理群待處理」按已處理')], '', '#C47F00', oid=oid)
    return '收到，已告訴管理層，會幫你準備或跟業主確認 🙏 準備好會通知你。'

# ── 前一天 21:00：還沒確認出勤的 → 管理群一則（一天一次）──
def tomorrow_unacked(con, day=None):
    day = day or (dt.datetime.now(c.TZ).date() + dt.timedelta(days=1)).isoformat()
    out = []
    for r in c.rows(con, "SELECT a.worker_id,w.name,o.id oid FROM assignments a JOIN workers w ON w.id=a.worker_id JOIN orders o ON o.id=a.order_id "
                         "WHERE o.date=? AND o.status='confirmed' AND a.status='accepted' AND COALESCE(o.review_note,'')!='示範工單' ORDER BY o.start", (day,)):
        if not acked(con, r['oid'], r['worker_id']):
            out.append(r)
    return out

REMIND_AT = '20:00'

def remind(con, now):
    """20:00 還沒確認明天出勤 → 夥伴再收一張短卡（一天一次）。"""
    import line_bot as lb
    if now.strftime('%H:%M') < REMIND_AT or ix.setting(con, 'att_rem') == now.date().isoformat():
        return 0
    ix.set_setting(con, 'att_rem', now.date().isoformat()); n = 0
    for r in tomorrow_unacked(con):
        o = c.get(con, 'orders', r['oid']); miss = missing(con, o, r['worker_id'])
        btns = ([lb._btn('我會帶齊，確認出勤 ✅', data=f"a=wok&o={r['oid']}&r=1&t=1", primary=True), lb._btn('工具不夠，請公司協助', data=f"a=tlack&o={r['oid']}")] if miss
                else [lb._btn('確認出勤 👍', data=f"a=wok&o={r['oid']}&r=1", primary=True)]) + [lb._btn('臨時請假', data=f"a=lv&o={r['oid']}")]
        m = lb.flex('明天的工作還沒確認出勤', lb._bubble(lb._header('明天的工作，確認一下 🙏', lb.when(dict(o)), '#C47F00'),
            [lb._t('請按確認出勤（時間、地址、工具都 OK）。' + (f"還缺：{'、'.join(miss)}。" if miss else '') + '9 點還沒確認，示範服務店會打電話給你。', wrap=True)], btns))
        lb.to_worker(con, r['worker_id'], 'att_remind', m[0]['altText'], m, r['oid']); n += 1
    return n

def tick(con, now=None):
    import line_bot as lb
    now = now or dt.datetime.now(c.TZ)
    n0 = remind(con, now) if now.strftime('%H:%M') < CONFIRM_AT else 0
    if now.strftime('%H:%M') < CONFIRM_AT or ix.setting(con, 'att_check') == now.date().isoformat():
        return n0
    ix.set_setting(con, 'att_check', now.date().isoformat())
    rows = tomorrow_unacked(con)
    if not rows:
        return 0
    lines = []
    for r in rows:
        o = c.get(con, 'orders', r['oid'])
        lines.append((r['name'], lb.when(dict(o)) + ('・缺工具沒確認' if missing(con, o, r['worker_id']) and not tools_ok(con, r['oid'], r['worker_id']) else '')))
    lb.admin_notify(con, f'⏰ 明天還沒確認出勤：{len(rows)} 位', lines[:12] +
                    [('處理', '請打電話確認；確認了在中控台「管理群待處理」按「已電話確認」')], '/#asks', '#C62828')
    return len(rows)

def anomalies(con):
    """中控台「管理群待處理」：出勤確認逾期、工具不夠（今天、明天的）。"""
    now = dt.datetime.now(c.TZ); out = []
    tm = (now.date() + dt.timedelta(days=1)).isoformat()
    if now.strftime('%H:%M') >= CONFIRM_AT:
        for r in tomorrow_unacked(con, tm):
            if True:
                out.append({'kind': 'att', 'id': f"{r['oid']}|{r['worker_id']}", 'oid': r['oid'], 'title': f"{r['name']} 還沒確認明天出勤", 'reason': 'unacked'})
    for k, v in con.execute("SELECT key,value FROM settings WHERE key LIKE 'tool_lack:%' AND value!=''").fetchall():
        _, oid, wid = k.split(':', 2)
        o = con.execute('SELECT date,status FROM orders WHERE id=?', (oid,)).fetchone()
        if not o or o['date'] < now.date().isoformat() or o['status'] in ('cancelled', 'archived', 'completed'):
            continue
        w = con.execute('SELECT name FROM workers WHERE id=?', (wid,)).fetchone()
        out.append({'kind': 'att', 'id': f'{oid}|{wid}|lack', 'oid': oid, 'title': f"{w[0] if w else '夥伴'} 工具不夠：{'、'.join(missing(con, c.get(con, 'orders', oid), wid)) or '他說不夠'}", 'reason': 'lack'})
    return out

def resolve(con, iid, by):
    import board
    parts = iid.split('|')
    oid, wid = parts[0], parts[1]
    if len(parts) > 2:
        import line_bot as lb
        ix.set_setting(con, f'tool_lack:{oid}:{wid}', ''); ix.set_setting(con, f'tool_ack:{oid}:{wid}', c.now())
        mark_ack(con, oid, wid, f'工具準備好（{by}）')        # 每日檢查：準備好了＝確認出勤，並通知夥伴（以前說會通知卻沒有）
        o = c.get(con, 'orders', oid)
        lb.to_worker(con, wid, 'tool_ready', '工具準備好了', lb.card_text('🧹 工具準備好了', f"{lb.when(dict(o))} 那場缺的工具已經準備好了，有問題直接在 LINE 問示範服務店 🙏", '#2E7D32'), oid)
        board.log(con, oid, 'tool_ready', by); return '已記下：工具準備好了（已通知夥伴，也算他確認出勤）'
    mark_ack(con, oid, wid, f'{by} 電話確認')
    w = con.execute('SELECT name FROM workers WHERE id=?', (wid,)).fetchone()
    board.log(con, oid, 'worker_ack', f"{w[0] if w else ''}（{by} 電話確認）")
    return '已記下：電話確認過了'

# ── 距離 ──
def km(a, b):
    try:
        la1, lo1, la2, lo2 = map(math.radians, (float(a[0]), float(a[1]), float(b[0]), float(b[1])))
    except (TypeError, ValueError):
        return None
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))

def worker_spot(con, w):
    """夥伴的位置：住處座標；沒有就用他 90 天內做過的案場平均位置（至少 2 場）。回傳 ((lat,lng), 說明) 或 (None, '')。"""
    w = dict(w)
    if w.get('home_lat') and w.get('home_lng'):
        return (w['home_lat'], w['home_lng']), '住處'
    pts = con.execute("SELECT s.lat,s.lng FROM assignments a JOIN orders o ON o.id=a.order_id JOIN sites s ON s.id=o.site_id WHERE a.worker_id=? AND a.status='accepted' "
                      "AND o.status IN ('completed','confirmed') AND o.date>=? AND s.lat IS NOT NULL AND s.lat!=''",
                      (w['id'], (dt.date.today() - dt.timedelta(days=90)).isoformat())).fetchall()
    if len(pts) < 2:
        return None, ''
    return (sum(float(p[0]) for p in pts) / len(pts), sum(float(p[1]) for p in pts) / len(pts)), '常跑區域'

def rank(con, o, s, w):
    """派工排序加分：(分數, [說明])。"""
    score, why = 0, []
    if s and s.get('lat') and s.get('lng'):
        spot, src = worker_spot(con, w)
        d = km(spot, (s['lat'], s['lng'])) if spot else None
        if d is not None:
            score += 15 if d <= 5 else 10 if d <= 10 else 4 if d <= 20 else -8 if d > 30 else 0
            why.append(f'距離約 {d:.0f} 公里（{src}）')
    if needs_tools(con, dict(o)):
        have_s = site_has(s) or set()
        need = [x for x in NEED if x not in have_s]
        if need:
            hw = worker_has(dict(w))
            if hw is None:
                score -= 3; why.append('沒填自備工具')
            else:
                miss = [x for x in need if x not in hw]
                if not miss:
                    score += 10; why.append('自備工具齊')
                else:
                    score -= min(12, 3 * len(miss)); why.append('自備工具缺 ' + '、'.join(miss[:3]) + ('…' if len(miss) > 3 else ''))
    return score, why
