"""功能回報（系統內所有問題集中一個地方，每天固定時間自動讀取、修正，回到 Claude 的那個對話改）。

來源：
- 人回報：中控台「功能回報」頁、LINE 打「回報：…」（管理層、夥伴）
- 自動抓：伺服器錯誤（500）、背景排程錯誤、LINE webhook 錯誤、中控台／夥伴工作頁／報價單的 JavaScript 錯誤
- 每日健康檢查（tools/sysreport.py report）：LINE 發送失敗、單元測試、e2e 盤點
同樣的自動錯誤只記一筆（fp 指紋），次數累加；修好後同樣的錯誤再出現會重新打開。
每天 22:00 左右 Claude 讀 `python -X utf8 tools/sysreport.py report` → 修 → `tools/sysreport.py fix <編號> "說明"`（會通知回報的人）→ `tools/sysreport.py summary --send`。
"""
import hashlib
import re
import datetime as dt
import core as c

KIND = {'bug': '問題', 'feature': '想要的功能', 'question': '詢問'}

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS sys_reports(id TEXT PRIMARY KEY, source TEXT DEFAULT 'staff', kind TEXT DEFAULT 'bug', title TEXT, detail TEXT DEFAULT '', "
                "who TEXT DEFAULT '', notify_uid TEXT DEFAULT '', notify_acc TEXT DEFAULT '', ref TEXT DEFAULT '', fp TEXT UNIQUE, count INTEGER DEFAULT 1, "
                "status TEXT DEFAULT 'open', created_at TEXT, last_at TEXT, fixed_at TEXT DEFAULT '', fix_note TEXT DEFAULT '')")

def ensure_files(con):
    con.execute("CREATE TABLE IF NOT EXISTS sys_report_files(id TEXT PRIMARY KEY, report_id TEXT, mime TEXT DEFAULT 'image/jpeg', data BLOB, created_at TEXT, by TEXT DEFAULT '')")

def add_files(con, rid, jpegs, by=''):
    """回報附的截圖（已在鎖外壓成 JPEG；最多 6 張）。"""
    ensure_files(con)
    for j in (jpegs or [])[:6]:
        con.execute('INSERT INTO sys_report_files(id,report_id,data,created_at,by) VALUES(?,?,?,?,?)', (c.uid('rf'), rid, bytes(j), c.now(), by[:60]))
    return len((jpegs or [])[:6])

def files_map(con):
    """{回報編號: [截圖編號…]}"""
    ensure_files(con); out = {}
    for fid, rid in con.execute('SELECT id,report_id FROM sys_report_files ORDER BY created_at'):
        out.setdefault(rid, []).append(fid)
    return out

def _no():
    return 'R' + dt.datetime.now(c.TZ).strftime('%m%d') + c.uid('')[-4:].upper()

def report(con, title, detail='', who='', source='staff', kind='bug', ref='', notify_uid='', notify_acc='', fp=None):
    """記一筆回報；fp 相同（同一個自動錯誤）就累加次數，已修好的會重新打開。回傳編號。"""
    ensure(con)
    title = (title or '').strip()[:200] or '（沒寫標題）'
    if fp:
        old = con.execute('SELECT id,status FROM sys_reports WHERE fp=?', (fp,)).fetchone()
        if old:
            con.execute("UPDATE sys_reports SET count=count+1,last_at=?,detail=?,status=CASE WHEN status='fixed' THEN 'open' ELSE status END WHERE id=?",
                        (c.now(), (detail or '')[:4000], old['id']))
            return old['id']
    if source == 'staff' and who:            # 客服的「報價單排版」被送了 13 次 → 同一個人 10 分鐘內同樣內容只記一筆
        import datetime as _dt
        dup = con.execute("SELECT id FROM sys_reports WHERE who=? AND title=? AND COALESCE(detail,'')=? AND created_at>? ORDER BY created_at DESC LIMIT 1",
                          (who[:60], title, (detail or '')[:4000], (_dt.datetime.now(c.TZ) - _dt.timedelta(minutes=10)).isoformat(timespec='seconds'))).fetchone()
        if dup:
            return dup[0]
    iid = _no()
    con.execute('INSERT INTO sys_reports(id,source,kind,title,detail,who,notify_uid,notify_acc,ref,fp,created_at,last_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (iid, source, kind if kind in KIND else 'bug', title, (detail or '')[:4000], who[:60], notify_uid, notify_acc, ref[:80], fp or iid, c.now(), c.now()))
    return iid

def auto(con, where, err, detail='', source='auto'):
    """程式自動抓到的錯誤：同一個地方、同一種錯誤只記一筆（數字、編號不算差異）。source='public'＝公開頁送來的（不可信）。"""
    msg = f'{type(err).__name__}: {err}' if isinstance(err, BaseException) else str(err or '')
    frame = re.findall(r'File "[^"]*[\\/]([^"\\/]+)", line (\d+)', detail or '')
    key = re.sub(r'\d+', '#', f"{where}|{msg[:160]}|{frame[-1] if frame else ''}|{detail[:60] if source == 'public' else ''}")
    return report(con, f'{where}：{msg[:150]}', detail, '系統自動' if source == 'auto' else '公開頁面', source, 'bug', fp='auto:' + hashlib.sha1(key.encode()).hexdigest()[:16])

def fix(con, iid, note, by='Claude'):
    """標記已修好；有留 LINE 的回報人會收到通知。"""
    ensure(con)
    t = con.execute('SELECT * FROM sys_reports WHERE id=?', (iid,)).fetchone()
    if not t:
        raise ValueError(f'找不到回報 {iid}')
    con.execute("UPDATE sys_reports SET status='fixed',fixed_at=?,fix_note=? WHERE id=?", (c.now(), note[:1000], iid))
    if t['notify_uid']:
        import line_bot as lb
        lb.queue(con, t['notify_acc'] or lb.WORK_ACCOUNT, t['notify_uid'], 'issue_fixed', f'你回報的問題已處理：{t["title"][:40]}',
                 lb._txt(f"✅ 你回報的「{t['title'][:60]}」已處理（{iid}）：\n{note[:400]}\n\n謝謝你回報 🙏"))
    return iid

def set_status(con, iid, status, note=''):
    ensure(con)
    if status not in ('open', 'fixed', 'wontfix', 'need_decision'):
        raise ValueError('狀態不符')
    con.execute('UPDATE sys_reports SET status=?,fix_note=CASE WHEN ?!=\'\' THEN ? ELSE fix_note END WHERE id=?', (status, note, note[:1000], iid))

def listing(con, status=None, limit=300):
    ensure(con)
    if status:
        return c.rows(con, 'SELECT * FROM sys_reports WHERE status=? ORDER BY last_at DESC LIMIT ?', (status, limit))
    return c.rows(con, "SELECT * FROM sys_reports ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'need_decision' THEN 1 ELSE 2 END, last_at DESC LIMIT ?", (limit,))

def health(con, hours=24):
    """自動讀取：最近 LINE 發送失敗（依錯誤分組）。"""
    since = (dt.datetime.now(c.TZ) - dt.timedelta(hours=hours)).isoformat()
    return c.rows(con, "SELECT kind, substr(COALESCE(error,''),1,120) err, status, COUNT(*) n FROM line_outbox WHERE status LIKE 'failed%' AND created_at>=? "
                       "GROUP BY kind, err, status ORDER BY n DESC LIMIT 20", (since,))

LINE_TRIGGER = re.compile(r'^\s*(?:#?回報(?:問題)?|系統問題|功能建議|想要的功能)\s*[：:]\s*(.+)$', re.S)
