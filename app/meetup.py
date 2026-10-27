"""示範服務店夥伴大會（要發招募貼文前，先約全部夥伴出來開會、拍大合照，來參加就送指定工具；先給店長審核）。

流程：
1. Claude 擬好（候選時段、地點、出席禮、議程）→ 店長審核（reviews action 'meetup_open'）；要改時段／地點／禮物在審核卡按「要修改」寫，Claude 改好再送。
2. 核准 → 已綁 LINE 的夥伴收到邀請卡：可以的時段點一點（可以點多個）、「都不行」；點了時段再問「大合照可以入鏡嗎」（招募貼文會用）。
3. 還沒綁 LINE 的夥伴：管理層照舊在中控台「夥伴 › LINE 還沒綁」傳綁定訊息；綁好之後系統自動補發邀請（白天、一次最多 10 位）。
4. 每天 10:30 那一則寫報名人數；截止日 → 管理群一張卡：各時段幾位可以 → 按「定在這個時間」→ 全部已綁的夥伴收到「確定時間地點」卡（說可以的人寫「等你來」）。
5. 當天夥伴按「我到了，簽到」→ 記出席；管理層看出席名單發禮物。
"""
import datetime as dt
import json
import core as c
import integrations as ix

KEY = 'meetup'
WD = '一二三四五六日'

def get(con):
    try:
        return json.loads(ix.setting(con, KEY) or '{}')
    except ValueError:
        return {}

def _save(con, m):
    ix.set_setting(con, KEY, json.dumps(m, ensure_ascii=False))

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS meetup_rsvp(mid TEXT, worker_id TEXT, slots TEXT DEFAULT '', none INTEGER DEFAULT 0, photo TEXT DEFAULT '', "
                "attended TEXT DEFAULT '', at TEXT, PRIMARY KEY(mid, worker_id))")

def label(iso):
    t = dt.datetime.fromisoformat(iso)
    return f"{t.month}/{t.day}（{WD[t.weekday()]}）{t:%H:%M}"

def draft(con, slots, place, gift, deadline, agenda, by='負責人（Claude 擬稿）'):
    """擬好送店長審核。slots：['T14:00', ...]。"""
    import reviews, broadcast, line_bot as lb
    ensure(con)
    m = {'id': c.uid('mt'), 'status': 'draft', 'slots': slots, 'place': place, 'gift': gift, 'deadline': deadline, 'agenda': agenda, 'final': '', 'invited': {}}
    _save(con, m)
    n = len(lb.guide_targets(con))
    import automation
    nb = len(automation.unbound_workers(con))
    body = (f"示範服務店夥伴大會（負責人要約全部夥伴出來開會、拍大合照，出席送工具）。核准後立刻發給已綁 LINE 的夥伴 {n} 位；還沒綁的 {nb} 位綁好後系統自動補發。\n\n"
            f"候選時段（夥伴可以點多個，人最多的由管理群拍板）：\n" + '\n'.join('・' + label(s) for s in slots) +
            f"\n地點：{place}\n出席禮：{gift}\n報名截止：{deadline[5:].replace('-', '/')}\n議程：{agenda}\n"
            "大合照：會先問夥伴同不同意入鏡（招募貼文會用）；不同意的不入鏡。\n\n"
            "時段、地點、禮物要改，請按「要修改」直接寫（例：時段改 11/9 下午、地點寫公司地址），Claude 改好再送。")
    card = invite_card(con, m)
    return reviews.submit(con, '夥伴公告', '示範服務店夥伴大會：邀請＋報名（大合照、出席送工具）', body,
                          {'action': 'meetup_open', 'mid': m['id'], 'preview': card}, by)

def invite_card(con, m):
    import line_bot as lb
    btns = [lb._btn(f'{label(s)} 可以', data=f"a=mt&m={m['id']}&s={i}", primary=i == 0) for i, s in enumerate(m['slots'][:3])] + [lb._btn('都不行', data=f"a=mt&m={m['id']}&s=no")]
    return lb.flex('🎉 示範服務店夥伴大會：哪個時間可以？', lb._bubble(lb._header('🎉 示範服務店夥伴大會', '來參加就送工具', '#2E7D32'),
        [lb._row('做什麼', m['agenda']), lb._row('出席禮', m['gift']), lb._row('地點', m['place']), lb._row('報名到', m['deadline'][5:].replace('-', '/')),
         lb._t('下面可以的時間都點一下（可以點好幾個），人最多的那個就定案，會再通知你。', wrap=True, size='sm', color=lb.SUB, margin='md')], btns,
        '當天會拍一張大合照（用在示範服務店招募貼文），點完時間會問你要不要入鏡。'))

def photo_card(con, m):
    import line_bot as lb
    return lb.flex('大合照可以入鏡嗎？', lb._bubble(lb._header('📸 大合照可以入鏡嗎？', '示範服務店夥伴大會', '#2E7D32'),
        [lb._t('當天會拍一張全體大合照，會用在示範服務店的招募貼文（Threads、IG、官網）。', wrap=True),
         lb._t('不想入鏡也完全沒關係，一樣歡迎來、一樣有出席禮。', wrap=True, size='sm', color=lb.SUB, margin='md')],
        [lb._btn('可以入鏡 😊', data=f"a=mtp&m={m['id']}&v=1", primary=True), lb._btn('我不入鏡', data=f"a=mtp&m={m['id']}&v=0")]))

def open_(con, mid, by=''):
    """店長核准 → 發邀請。"""
    import line_bot as lb, board
    m = get(con)
    if m.get('id') != mid or m.get('status') != 'draft':
        return 0
    m['status'] = 'open'; m['opened_by'] = by; _save(con, m)
    return invite_new(con, limit=500)

def invite_new(con, limit=10):
    """還沒收到邀請、已綁 LINE 的夥伴 → 發邀請卡。"""
    import line_bot as lb
    m = get(con)
    if m.get('status') != 'open':
        return 0
    n = 0
    for t in lb.guide_targets(con):
        if t['id'] in m['invited'] or n >= limit:
            continue
        msg = invite_card(con, m)
        lb.to_worker(con, t['id'], 'meetup_invite', msg[0]['altText'], msg)
        m['invited'][t['id']] = c.now(); n += 1
    _save(con, m)
    return n

def _wid(con, acc, uid, lu):
    return (lu or {}).get('worker_id')

def answer(con, acc, uid, lu, mid, s):
    """夥伴點時段／都不行 → 回覆（點時段的第一次再問大合照）。回傳 (文字, 要不要接著問大合照)。"""
    ensure(con)
    m = get(con); wid = _wid(con, acc, uid, lu)
    if not wid:
        return '這是給示範服務店夥伴的報名卡，你的 LINE 還沒綁定夥伴，請跟示範服務店說一聲 🙏', False
    if m.get('id') != mid or m.get('status') not in ('open', 'final'):
        return '這次大會的報名已經結束了，謝謝你 🙏', False
    r = con.execute('SELECT * FROM meetup_rsvp WHERE mid=? AND worker_id=?', (mid, wid)).fetchone()
    slots = set(filter(None, (r['slots'] if r else '').split(',')))
    if s == 'no':
        con.execute("INSERT INTO meetup_rsvp(mid,worker_id,slots,none,at) VALUES(?,?,'',1,?) ON CONFLICT(mid,worker_id) DO UPDATE SET slots='',none=1,at=excluded.at", (mid, wid, c.now()))
        return '收到，這次時間都不方便沒關係 🙏 之後有活動再通知你。', False
    try:
        iso = m['slots'][int(s)]
    except (ValueError, IndexError):
        return '這個按鈕不對，請重新點一次 🙏', False
    slots.add(iso)
    con.execute("INSERT INTO meetup_rsvp(mid,worker_id,slots,none,at) VALUES(?,?,?,0,?) ON CONFLICT(mid,worker_id) DO UPDATE SET slots=excluded.slots,none=0,at=excluded.at",
                (mid, wid, ','.join(sorted(slots)), c.now()))
    ask_photo = not (r and r['photo'])
    return f"已記下：{'、'.join(label(x) for x in sorted(slots))} 可以 ✅\n其他時間也可以就再點；定案會再通知你。", ask_photo

def photo(con, acc, uid, lu, mid, v):
    ensure(con)
    wid = _wid(con, acc, uid, lu)
    if not wid:
        return '收到 🙏'
    con.execute("INSERT INTO meetup_rsvp(mid,worker_id,photo,at) VALUES(?,?,?,?) ON CONFLICT(mid,worker_id) DO UPDATE SET photo=excluded.photo", (mid, wid, 'yes' if v == '1' else 'no', c.now()))
    c.audit(con, 'meetup_photo_consent', 'workers', wid, after={'mid': mid, 'photo': 'yes' if v == '1' else 'no'})
    return '謝謝你 😊 大合照會用在示範服務店招募貼文。' if v == '1' else '好的，拍大合照時你不用入鏡，一樣歡迎來 🙏'

def tally(con):
    m = get(con)
    ensure(con)
    rows = c.rows(con, 'SELECT r.*,w.name FROM meetup_rsvp r JOIN workers w ON w.id=r.worker_id WHERE r.mid=?', (m.get('id', ''),))
    per = {s: [r['name'] for r in rows if s in (r['slots'] or '').split(',')] for s in m.get('slots', [])}
    return m, rows, per

def standing(con):
    """10:30 那一則：報名人數（開放中）。"""
    m, rows, per = tally(con)
    if m.get('status') != 'open':
        return ''
    return '、'.join(f'{label(s)} {len(v)} 位' for s, v in per.items()) + f"・都不行 {sum(1 for r in rows if r['none'])} 位・邀請了 {len(m.get('invited', {}))} 位"

def decide_card(con):
    import line_bot as lb
    m, rows, per = tally(con)
    btns = [lb._btn(f'定在 {label(s)}（{len(per[s])} 位）', data=f"a=mtf&m={m['id']}&s={i}", primary=i == 0)
            for i, s in sorted(enumerate(m['slots']), key=lambda x: -len(per[x[1]]))][:4]
    return lb.flex('夥伴大會報名截止：定哪個時間？', lb._bubble(lb._header('🎉 夥伴大會：定哪個時間？', f"報名截止 {m['deadline'][5:].replace('-', '/')}", '#2E7D32'),
        [lb._row(label(s), f"{len(v)} 位：{'、'.join(v[:12])}" + ('…' if len(v) > 12 else '')) for s, v in per.items()] +
        [lb._row('都不行', f"{sum(1 for r in rows if r['none'])} 位"), lb._row('可以入鏡', f"{sum(1 for r in rows if r['photo'] == 'yes')} 位")], btns,
        '按了之後，全部已綁 LINE 的夥伴收到確定的時間地點。'))

def finalize(con, mid, i, by):
    """管理層定案 → 全部已綁的夥伴收到確定卡。"""
    import line_bot as lb, board
    m, rows, per = tally(con)
    if m.get('id') != mid or m.get('status') != 'open':
        return '已經定案過了'
    iso = m['slots'][int(i)]
    m.update({'status': 'final', 'final': iso, 'final_by': by}); _save(con, m)
    yes = {r['worker_id'] for r in rows if iso in (r['slots'] or '').split(',')}
    n = 0
    for t in lb.guide_targets(con):
        msg = lb.flex(f'🎉 夥伴大會定案：{label(iso)}', lb._bubble(lb._header('🎉 夥伴大會定案', label(iso), '#2E7D32'),
            [lb._row('地點', m['place']), lb._row('出席禮', m['gift']), lb._row('做什麼', m['agenda']),
             lb._t('等你來 😊' if t['id'] in yes else '這個時間可以的話也歡迎來，直接到就好 😊', wrap=True, margin='md')],
            [lb._btn('我到了，簽到 ✅', data=f"a=mtin&m={m['id']}", primary=True)], '當天到現場按「我到了，簽到」，記下出席才能領出席禮。'))
        lb.to_worker(con, t['id'], 'meetup_final', msg[0]['altText'], msg); n += 1
    c.audit(con, 'meetup_final', 'settings', KEY, after={'slot': iso, 'by': by})
    return f'✅ 定在 {label(iso)}，已通知 {n} 位夥伴（說可以的 {len(yes)} 位）'

def checkin(con, acc, uid, lu, mid):
    ensure(con)
    m = get(con); wid = _wid(con, acc, uid, lu)
    if not wid or m.get('id') != mid or m.get('status') != 'final':
        return '簽到還沒開始喔 🙏'
    t = dt.datetime.fromisoformat(m['final']).replace(tzinfo=c.TZ); now = dt.datetime.now(c.TZ)
    if not (t - dt.timedelta(hours=2) <= now <= t + dt.timedelta(hours=4)):
        return f"簽到在大會當天（{label(m['final'])}）開放喔 🙏"
    con.execute("INSERT INTO meetup_rsvp(mid,worker_id,attended,at) VALUES(?,?,?,?) ON CONFLICT(mid,worker_id) DO UPDATE SET attended=excluded.attended", (mid, wid, c.now(), c.now()))
    return '簽到完成 ✅ 歡迎！記得找主管領出席禮 🎁'

def tick(con, now=None):
    """白天：新綁好的夥伴補發邀請；截止日 10:00 送管理群定案卡（一次）。"""
    import line_bot as lb
    now = now or dt.datetime.now(c.TZ)
    m = get(con)
    if m.get('status') != 'open' or not ('09:00' <= now.strftime('%H:%M') < '20:00'):
        return 0
    n = invite_new(con)
    if now.date().isoformat() >= m['deadline'] and now.strftime('%H:%M') >= '10:00' and not m.get('asked'):
        msg = decide_card(con)
        lb.to_managers(con, 'meetup_decide', msg[0]['altText'], msg, tag='🎉 夥伴大會報名截止，請定時間 👇')
        m = get(con); m['asked'] = c.now(); _save(con, m); n += 1
    return n
