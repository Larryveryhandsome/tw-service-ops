"""LINE 額度用完後自動補發（示範服務店 6,000 則用完，派工卡、提醒都送不出去）。

背景每 10 分鐘：有「額度用完」失敗的訊息 → 查一次該帳號額度；恢復了（加購／升級／下個月）→ 48 小時內、還有用的重新排隊送：
  ・跟工單有關的：工單已取消／已過（日期在今天以前）不補；當天的「出門了嗎」「到了嗎」這類時間性提醒超過 2 小時不補。
  ・管理群通知只補 12 小時內的（太舊的已經沒意義，中控台看得到）。
"""
import datetime as dt
import core as c
import integrations as ix

TIMELY = ('eta_ask', 'arrive_nudge', 'before_nudge', 'leave_nudge', 'prestart', 'near_c', 'door_morning', 'urgent_offer')
ADMIN = ('admin', 'admin_ask', 'admin_review', 'cmsg', 'aib', 'review_nudge', 'urgent_adjust', 'urgent_c', 'meetup_decide')

def tick(con, now=None):
    import line_bot as lb
    now = now or dt.datetime.now(c.TZ)
    since = (now - dt.timedelta(hours=48)).isoformat(timespec='seconds')
    accs = [r[0] for r in con.execute("SELECT DISTINCT account FROM line_outbox WHERE status='failed:quota' AND created_at>=?", (since,))]
    n = 0
    for acc in accs:
        k = f'quota_check:{acc}'
        if (ix.setting(con, k) or '') > (now - dt.timedelta(minutes=10)).isoformat(timespec='seconds'):
            continue
        ix.set_setting(con, k, now.isoformat(timespec='seconds'))
        q = lb.quota(con, acc)
        if q.get('error') or (q.get('type') == 'limited' and (q.get('used') or 0) >= (q.get('limit') or 0)):
            continue                                            # 還沒恢復
        today = now.date().isoformat()
        for m in c.rows(con, "SELECT * FROM line_outbox WHERE account=? AND status='failed:quota' AND created_at>=? ORDER BY id", (acc, since)):
            age = now - dt.datetime.fromisoformat(m['created_at'])
            if m['kind'] in TIMELY and age > dt.timedelta(hours=2):
                continue
            if m['kind'] in ADMIN and age > dt.timedelta(hours=12):
                continue
            if m['order_id']:
                o = con.execute('SELECT date,status FROM orders WHERE id=?', (m['order_id'],)).fetchone()
                if not o or o['status'] in ('cancelled', 'archived') or (o['date'] or '') < today:
                    continue
            con.execute("UPDATE line_outbox SET status='queued',attempts=0,next_at='',error='額度恢復後補發' WHERE id=?", (m['id'],))
            n += 1
        con.execute("UPDATE line_outbox SET status='skipped:quota_old' WHERE account=? AND status='failed:quota' AND created_at>=?", (acc, since))
        if n:
            lb.admin_notify(con, f'✅ LINE 額度恢復，補發 {n} 則', [('帳號', acc), ('說明', '額度用完期間沒送出去、現在還有用的訊息已自動補發（已過期、已取消的不補）')], '', '#2E7D32')
    return n
