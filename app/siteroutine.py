"""案場定期特殊服務（客戶01客訴：每雙月要拆洗冷氣濾網，夥伴沒做到；系統沒有這種規則的提醒）。

- 管理層在中控台客戶頁 → 服務地點「🔁 定期項目」設定：每 1／2／3／6 個月一次、名稱、要做哪些（一行一項）、上次做的日期。
- 週期照月曆切：每月＝每個月一次；每兩月＝1–2 月、3–4 月…各一次；每季＝1–3 月…。這一期還沒做 → 這期該案場的工單都會帶：
  派工卡、前一天提醒卡、現場須知頁寫「這次要做（每兩個月）：電梯、三合一門、冷氣拆洗…」；提醒卡多一顆「✅ 定期項目做完了」。
- 夥伴按「做完了」→ 記這一期已做（上次做的日期＝那場日期），之後同一期的工單不再帶。
- 工單日期過了、這期還沒按做完 → 中控台「管理群待處理」＋一天三次彙整：「已確認做了」／「這次沒做，下次再做」（下一場照樣帶）。
"""
import datetime as dt
import core as c

EVERY = {1: '每月', 2: '每兩個月', 3: '每季', 6: '每半年'}

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS site_routines(id TEXT PRIMARY KEY, site_id TEXT, every INTEGER DEFAULT 1, title TEXT DEFAULT '', tasks TEXT DEFAULT '', "
                "last_done TEXT DEFAULT '', last_order TEXT DEFAULT '', active INTEGER DEFAULT 1, created_at TEXT, created_by TEXT DEFAULT '')")

def period_start(every, day):
    d = dt.date.fromisoformat(str(day)[:10])
    i = (d.year * 12 + d.month - 1) // every * every
    return dt.date(i // 12, i % 12 + 1, 1).isoformat()

def all_for(con, sid):
    ensure(con)
    return c.rows(con, 'SELECT * FROM site_routines WHERE site_id=? AND active=1 ORDER BY every,created_at', (sid or '',))

def due(con, o):
    """這場工單要帶的定期項目（這一期還沒做）。"""
    if not o or not o.get('site_id') or not o.get('date'):
        return []
    return [r for r in all_for(con, o['site_id']) if (r['last_done'] or '') < period_start(r['every'], o['date'])]

def _line(r):
    tasks = '、'.join(t.strip(' ・-') for t in (r['tasks'] or '').splitlines() if t.strip(' ・-'))
    every = EVERY.get(r['every']) or '每 %s 個月' % r['every']
    return f"{r['title'] or '定期項目'}（{every}）：{tasks}"

def text(con, o):
    return '\n'.join(_line(r) for r in due(con, o))

def save(con, p, by):
    ensure(con)
    sid = str(p.get('site_id') or '')
    if not con.execute('SELECT 1 FROM sites WHERE id=?', (sid,)).fetchone():
        raise ValueError('找不到地點')
    every = int(p.get('every') or 0)
    if every not in EVERY:
        raise ValueError('週期要選每月／每兩個月／每季／每半年')
    tasks = '\n'.join(t.strip() for t in str(p.get('tasks') or '').splitlines() if t.strip())[:800]
    if not tasks:
        raise ValueError('請寫要做哪些（一行一項）')
    last = str(p.get('last_done') or '')[:10]
    if last:
        dt.date.fromisoformat(last)
    title = str(p.get('title') or '').strip()[:30] or EVERY[every] + '項目'
    if p.get('id'):
        con.execute('UPDATE site_routines SET every=?,title=?,tasks=?,last_done=? WHERE id=? AND site_id=?', (every, title, tasks, last, p['id'], sid))
        rid = p['id']
    else:
        rid = c.uid('rtn')
        con.execute('INSERT INTO site_routines(id,site_id,every,title,tasks,last_done,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)', (rid, sid, every, title, tasks, last, c.now(), by))
    c.audit(con, 'site_routine_save', 'sites', sid, after={'id': rid, 'every': every, 'title': title, 'tasks': tasks, 'last_done': last, 'by': by})
    return rid

def delete(con, rid, by):
    ensure(con)
    r = con.execute('SELECT site_id FROM site_routines WHERE id=?', (rid,)).fetchone()
    if not r:
        raise ValueError('找不到這個定期項目')
    con.execute('UPDATE site_routines SET active=0 WHERE id=?', (rid,))
    c.audit(con, 'site_routine_del', 'sites', r[0], after={'id': rid, 'by': by})

def done(con, oid, by, only=None):
    """這場做完了 → 這一期的定期項目記已做（only＝只記這幾組；夥伴說沒做到的 rtskip 不記）。回傳記了幾項。"""
    import integrations as ix
    o = c.get(con, 'orders', oid)
    rs = [r for r in due(con, o) if (only is None or r['id'] in only) and not ix.setting(con, f"rtskip:{r['id']}:{oid}")]
    for r in rs:
        con.execute('UPDATE site_routines SET last_done=?,last_order=? WHERE id=?', (o['date'], oid, r['id']))
    if rs:
        c.audit(con, 'site_routine_done', 'orders', oid, after={'routines': [r['id'] for r in rs], 'by': by})
        import board; board.log(con, oid, 'routine_done', f"定期項目做完了：{'、'.join(r['title'] for r in rs)}（{by}）")
    return len(rs)

def tasks(con, o):
    """工作頁逐項勾：[[routine_id, 名稱（週期）, 項目]]。"""
    out = []
    for r in due(con, o):
        for t in (r['tasks'] or '').splitlines():
            if t.strip(' ・-'):
                out.append([r['id'], f"{r['title']}（{EVERY.get(r['every']) or r['every']}）", t.strip(' ・-')])
    return out

def on_leave(con, o, worker, checked, why):
    """（沒做到也沒反應）：離場打卡時定期項目要逐項勾；沒勾的要寫原因（不能空白）→ 管理群卡片。全部勾 → 記做完。"""
    ts = tasks(con, o)
    if not ts:
        return ''
    done_ = {str(x) for x in (checked or [])}
    miss = [t for i, t in enumerate(ts) if str(i) not in done_]
    full = {t[0] for t in ts} - {t[0] for t in miss}          # 整組都勾的 → 記做完（勾一部分的那組不算）
    if full:
        import integrations as ix
        for rid in full:                               # 之前說沒做、後來補做完再離場 → 照樣記做完
            ix.set_setting(con, f"rtskip:{rid}:{o['id']}", '')
        done(con, o['id'], worker, only=full)
    if not miss:
        return '定期項目全部做完'
    why = str(why or '').strip()
    if not why:
        raise ValueError('定期項目還有沒勾的：做了請勾，沒做到請寫原因（例：冷氣濾網卡住拆不下來），才能離場打卡')
    import integrations as ix, line_bot as lb, board
    for rid in {t[0] for t in miss}:
        ix.set_setting(con, f"rtskip:{rid}:{o['id']}", worker)          # 夥伴已經說明了 → 管理群待處理不再重複列；下一場照樣帶
    board.log(con, o['id'], 'routine_miss', f"{worker}：定期項目沒做到 {'、'.join(t[2] for t in miss)}；原因：{why[:200]}")
    lb.admin_notify(con, f'⚠️ 定期項目沒做到：{worker}', [('工作', lb.when(dict(o))), ('沒做到', '、'.join(t[2] for t in miss)[:300]), ('夥伴說', why[:300]),
                                                       ('處理', '下一場會照樣帶這些項目；要補清請另外排')], '', '#C47F00', oid=o['id'])
    return '已回報沒做到的定期項目'

def open_items(con, days=14):
    """工單日期過了、這一期的定期項目還沒回報做完 → [(order, [routine])]（管理層「這次沒做」的那場不再列）。"""
    ensure(con)
    import integrations as ix
    t = dt.datetime.now(c.TZ).date()
    out = []
    for o in c.rows(con, "SELECT o.* FROM orders o JOIN site_routines r ON r.site_id=o.site_id AND r.active=1 WHERE o.date BETWEEN ? AND ? AND o.status IN ('confirmed','completed') GROUP BY o.id ORDER BY o.date",
                    ((t - dt.timedelta(days=days)).isoformat(), (t - dt.timedelta(days=1)).isoformat())):
        rs = [r for r in due(con, o) if not ix.setting(con, f"rtskip:{r['id']}:{o['id']}") and o['date'] >= (r['created_at'] or '')[:10]]      # 新增規則之前的工單不追
        if rs:
            out.append((o, rs))
    return out

def skip(con, oid, by):
    import integrations as ix
    o = c.get(con, 'orders', oid)
    for r in due(con, o):
        ix.set_setting(con, f"rtskip:{r['id']}:{oid}", by or '1')
    c.audit(con, 'site_routine_skip', 'orders', oid, after={'by': by})
    return '已記下：這次沒做，下一場照樣會帶這些項目'
