"""海巡留言審核（Threads 海巡每天 20～30 則留言，全部要我們審過才發）。

- 草稿由海巡系統產（D:\\24小時AI 客服-房仲遠端助理AI客服\\outreach_bot，outreach_log status=awaiting_approval），臺北的貼文排前面。
- 中控台「更多 › 行銷 › 海巡留言待審」：看原貼文、改留言字、核准／略過；每天 10:30、16:30 各審一批。
- 核准 → status=approved、排發送時間（彼此間隔 12～25 分鐘），由海巡 poster_worker 發出。
- 超過 24 小時沒審的當天過期不發（對方貼文已經舊了）。
"""
import hashlib, random, sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(r'D:\24小時AI 客服-房仲遠端助理AI客服\outreach_bot\data\outreach_bot.db')
BRAND = 'demoshop'
YILAN = ('臺北', '板橋', '中壢', '頭城', '五結', '冬山', '蘇澳', '三星', '員山', '壯圍', '大同', '南澳')
STALE_H = 24


def _con():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c


def is_yilan(text):
    return any(k in (text or '') for k in YILAN)


def expire_stale(c):
    cut = (datetime.now() - timedelta(hours=STALE_H)).isoformat()
    return c.execute("UPDATE outreach_log SET status='expired', error='中控台超過 24 小時沒審，過期不發' "
                     "WHERE brand_key=? AND status='awaiting_approval' AND created_at<?", (BRAND, cut)).rowcount


def pending():
    if not DB.exists():
        return {'items': [], 'today': {}}
    with _con() as c:
        expire_stale(c)
        rows = [dict(r) for r in c.execute(
            "SELECT id,target_author,target_url,target_content,reply_text,created_at,post_like_count,post_reply_count "
            "FROM outreach_log WHERE brand_key=? AND status='awaiting_approval' ORDER BY created_at", (BRAND,))]
        day = datetime.now().strftime('%Y-%m-%d')
        today = {k: n for k, n in c.execute(
            "SELECT status,COUNT(*) FROM outreach_log WHERE brand_key=? AND (created_at LIKE ? OR posted_at LIKE ?) GROUP BY status",
            (BRAND, day + '%', day + '%'))}
    for r in rows:
        r['yilan'] = is_yilan(r.get('target_content'))
        r['target_content'] = (r.get('target_content') or '')[:400]
    rows.sort(key=lambda r: (not r['yilan'], r['created_at']))
    return {'items': rows, 'today': today}


def count():
    try:
        with _con() as c:
            cut = (datetime.now() - timedelta(hours=STALE_H)).isoformat()
            return c.execute("SELECT COUNT(*) FROM outreach_log WHERE brand_key=? AND status='awaiting_approval' AND created_at>=?",
                             (BRAND, cut)).fetchone()[0]
    except sqlite3.Error:
        return 0


def _next_slot(c):
    last = c.execute("SELECT MAX(scheduled_at) FROM outreach_log WHERE brand_key=? AND status='approved'", (BRAND,)).fetchone()[0]
    t = datetime.now() + timedelta(minutes=2)
    if last:
        t = max(t, datetime.fromisoformat(last[:19]) + timedelta(minutes=random.randint(12, 25)))
    if t.hour >= 23 or t.hour < 8:                                   # 半夜不發 → 隔天 08:xx
        t = (t if t.hour < 8 else t + timedelta(days=1)).replace(hour=8, minute=random.randint(5, 40), second=0)
    return t.isoformat(timespec='seconds')


def act(ids, decision, by, text=None):
    """decision：ok（核准，可帶改過的留言字）／skip（略過）。回處理筆數。"""
    if decision not in ('ok', 'skip'):
        raise ValueError('只能核准或略過')
    ids = [int(i) for i in (ids if isinstance(ids, (list, tuple)) else [ids])]
    n = 0
    with _con() as c:
        for i in ids:
            r = c.execute("SELECT status,reply_text FROM outreach_log WHERE id=? AND brand_key=?", (i, BRAND)).fetchone()
            if not r or r['status'] != 'awaiting_approval':
                continue
            if decision == 'skip':
                c.execute("UPDATE outreach_log SET status='rejected', error=? WHERE id=?", (f'中控台略過（{by}）', i))
                _feedback(c, 'outreach', 'reject', i, r['reply_text'], r['reply_text'])
            else:
                reply = (text if (text and len(ids) == 1) else r['reply_text'] or '').strip()
                if not reply:
                    raise ValueError('留言不能是空的')
                if len(reply) > 500:
                    raise ValueError('留言太長（最多 500 字）')
                c.execute("UPDATE outreach_log SET status='approved', reply_text=?, scheduled_at=?, error=? WHERE id=?",
                          (reply, _next_slot(c), f'中控台核准（{by}）', i))
                _feedback(c, 'outreach', 'edit' if reply != (r['reply_text'] or '').strip() else 'approve', i, r['reply_text'], reply)
            n += 1
    return n


def _feedback(c, kind, action, ref, original, final):
    """跟原本 5100 後台一樣寫 feedback_event（海巡學習用：哪些被改、被略過）。表不在就跳過。"""
    try:
        o = original or ''
        if kind == 'outreach':
            x = c.execute("SELECT trigger_id,target_author,target_content FROM outreach_log WHERE id=?", (ref,)).fetchone()
            extra = (x['trigger_id'], None, x['target_author'], (x['target_content'] or '')[:400])
        else:
            x = c.execute("SELECT topic FROM self_post_draft WHERE id=?", (ref,)).fetchone()
            extra = (None, x['topic'], None, None)
        c.execute("INSERT INTO feedback_event(kind,brand_key,action,trigger_id,topic,text_hash,original_text,final_text,was_edited,"
                  "target_author,target_content,ref_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (kind, BRAND, action, extra[0], extra[1], hashlib.sha1(o.encode()).hexdigest()[:12], o, final,
                   int(action == 'edit'), extra[2], extra[3], ref, datetime.now().isoformat()))
    except sqlite3.Error:
        pass


SLOTS = ('08:30', '11:45', '14:30', '18:30', '21:00')     # 示範服務店每天 5 篇的時段（跟行銷排程同一套）


def drafts():
    """5100 後台不用了 → 舊產文留下的示範服務店草稿（awaiting_approval）也在中控台審。"""
    if not DB.exists():
        return []
    with _con() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id,topic,content,created_at FROM self_post_draft WHERE brand_key=? AND status='awaiting_approval' ORDER BY created_at", (BRAND,))]
    return rows


def _free_slot(c):
    """找最早一個沒人佔的發文時段（今天還沒過的或之後幾天）。"""
    taken = {(r[0] or '')[:16] for r in c.execute(
        "SELECT scheduled_at FROM self_post_draft WHERE brand_key=? AND status='approved'", (BRAND,))}
    now = datetime.now() + timedelta(minutes=10)
    for d in range(0, 30):
        day = (now + timedelta(days=d)).strftime('%Y-%m-%d')
        for s in SLOTS:
            t = f'{day}T{s}'
            if t > now.isoformat()[:16] and t not in taken:
                return t + ':00'
    raise ValueError('30 天內的時段都排滿了')


def draft_act(i, decision, by, text=None):
    if decision not in ('ok', 'skip'):
        raise ValueError('只能核准或略過')
    with _con() as c:
        r = c.execute("SELECT status,content FROM self_post_draft WHERE id=? AND brand_key=?", (int(i), BRAND)).fetchone()
        if not r or r['status'] != 'awaiting_approval':
            return None
        if decision == 'skip':
            c.execute("UPDATE self_post_draft SET status='rejected' WHERE id=?", (int(i),))
            _feedback(c, 'self_post', 'reject', int(i), r['content'], r['content'])
            return '已略過'
        body = (text or r['content'] or '').strip()
        if not body:
            raise ValueError('內容不能是空的')
        if len(body) > 500:
            raise ValueError('Threads 一篇最多 500 字')
        t = _free_slot(c)
        c.execute("UPDATE self_post_draft SET status='approved', content=?, scheduled_at=? WHERE id=?", (body, t, int(i)))
        _feedback(c, 'self_post', 'edit' if body != (r['content'] or '').strip() else 'approve', int(i), r['content'], body)
        return '✅ 已核准，排在 ' + t[5:16].replace('T', ' ') + ' 發'


def posts(days_back=2, days_ahead=8):
    """發文也要在中控台看得到 → 示範服務店 Threads 自發文（排隊中／已發／失敗），含網址、置頂留言。"""
    if not DB.exists():
        return []
    a = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    b = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
    with _con() as c:
        cols = {r[1] for r in c.execute('PRAGMA table_info(self_post_draft)')}
        extra = ',pin_comment' if 'pin_comment' in cols else ",'' AS pin_comment"
        rows = [dict(r) for r in c.execute(
            f"SELECT id,topic,content,status,scheduled_at,posted_at,posted_url,error,image_path{extra} FROM self_post_draft "
            "WHERE brand_key=? AND status IN ('approved','posted','failed') AND COALESCE(posted_at,scheduled_at)>=? AND COALESCE(posted_at,scheduled_at)<? "
            "ORDER BY COALESCE(posted_at,scheduled_at)", (BRAND, a, b))]
    for r in rows:
        r['content'] = (r.get('content') or '')[:600]
        r['images'] = len([x for x in (r.pop('image_path') or '').split('|') if x])
    return rows
