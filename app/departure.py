"""夥伴離開（客戶06 刪了官方 LINE、退出群組說「我退出團隊了」，系統沒反應，她 的工作還掛著「已接」）。

偵測：夥伴刪除／封鎖官方 LINE（unfollow）、退出自己的夥伴群組或工作群（memberLeft）。
→ 他之後還有排班 → 派單管理群一張卡（一位夥伴一天一張）：
   ［暫停他，這些工作另外找人］＝夥伴改「暫停」、每一場拿掉他、照分級另外問人（跟「換人」同一套，但不再傳卡給他）
   ［先不用，我會聯絡他］＝只記下來
"""
import datetime as dt
import core as c
import integrations as ix

def future_jobs(con, wid):
    today = dt.datetime.now(c.TZ).date().isoformat()
    return c.rows(con, "SELECT o.id,o.date,o.start,o.end,s.name site FROM assignments a JOIN orders o ON o.id=a.order_id LEFT JOIN sites s ON s.id=o.site_id "
                       "WHERE a.worker_id=? AND a.status='accepted' AND o.date>=? AND o.status IN ('review','draft','confirmed') ORDER BY o.date,o.start", (wid, today))

def gone(con, wid, why):
    """偵測到夥伴離開 → 有之後的工作才通知管理群（一天一次）。回傳有沒有發。"""
    import line_bot as lb
    w = con.execute("SELECT * FROM workers WHERE id=? AND status='active'", (wid or '',)).fetchone()
    if not w:
        return False
    jobs = future_jobs(con, wid)
    key = f'wgone:{wid}'
    if not jobs or (ix.setting(con, key) or '')[:10] == c.now()[:10]:
        return False
    ix.set_setting(con, key, c.now())
    rows = [lb._row(lb.md(j['date']), f"{j['start']}–{j['end']} {j['site'] or ''}") for j in jobs[:8]]
    m = lb.flex(f"⚠️ 夥伴 {w['name']} 可能離開了", lb._bubble(lb._header(f"⚠️ {w['name']} 可能離開了", why, '#C62828'),
        rows + ([lb._row('還有', f'{len(jobs) - 8} 場')] if len(jobs) > 8 else []),
        [lb._btn('暫停他，這些工作另外找人', data=f'a=wgone&w={wid}&v=redo', primary=True), lb._btn('先不用，我會聯絡他', data=f'a=wgone&w={wid}&v=keep')],
        f'他之後還有 {len(jobs)} 場已接的工作。確定不做了就按上面：系統會照分級另外找人。'))
    lb.to_managers(con, 'worker_gone', m[0]['altText'], m, tag=f"⚠️ {w['name']} {why}，他之後還有 {len(jobs)} 場工作 👇")
    import board
    for j in jobs:
        board.log(con, j['id'], 'worker_gone', f"{w['name']}：{why}")
    return True

def decide(con, wid, v, by):
    import board, leave
    w = c.get(con, 'workers', wid)
    ix.set_setting(con, f'wgone:{wid}', '')
    if v != 'redo':
        c.audit(con, 'worker_gone_keep', 'workers', wid, after={'by': by})
        return f"好，{w['name']} 的工作先保留，記得聯絡他 🙏"
    jobs = future_jobs(con, wid)
    for j in jobs:
        con.execute("UPDATE assignments SET status='declined',notes=COALESCE(notes,'')||'（夥伴離開團隊）',version=version+1 WHERE order_id=? AND worker_id=? AND status='accepted'", (j['id'], wid))
        con.execute("UPDATE orders SET crew_ok='',dispatch=CASE WHEN dispatch='filled' THEN 'open' ELSE dispatch END,version=version+1 WHERE id=?", (j['id'],))
        leave.reopen(con, j['id'])
        board.log(con, j['id'], 'worker_gone_redo', f"{w['name']}（{by}）")
        leave.reask(con, j['id'])
    w = c.get(con, 'workers', wid)
    c.save(con, 'workers', {'id': wid, 'version': w['version'], 'status': 'paused'})      # 工作都拿掉之後才能暫停
    c.audit(con, 'worker_gone_redo', 'workers', wid, after={'by': by, 'jobs': [j['id'] for j in jobs]})
    return f"✅ {w['name']} 已暫停；{len(jobs)} 場工作已拿掉他、系統照分級另外找人"
