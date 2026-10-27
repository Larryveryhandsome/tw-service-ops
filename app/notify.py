"""手機通知（Web Push）。只通知自己系統的使用者（夥伴、客服）；不碰 LINE、不發給客戶。
測試環境（OPS_DATA 指到暫存資料夾）不送。送出在背景執行緒，失敗不影響主流程。"""
import os
import core as c

_sender = None

def sender():
    global _sender
    if _sender is None and not os.environ.get('OPS_NO_PUSH'):
        import push
        vapid = push.Vapid(str(c.DATA / 'vapid_private.pem'))
        _sender = push.Sender(vapid, c.connect)
    return _sender

def public_key():
    import push
    return push.Vapid(str(c.DATA / 'vapid_private.pem')).public

def _send(user_ids, title, body, url):
    s = sender()
    if s and user_ids:
        s.push(user_ids, {'title': title, 'body': body, 'url': url, 'tag': url})

def users_of_workers(con, worker_ids):
    if not worker_ids:
        return []
    q = ','.join('?' * len(worker_ids))
    return [r[0] for r in con.execute(f"SELECT id FROM users WHERE active=1 AND role='cleaner' AND worker_id IN ({q})", list(worker_ids))]

def workers(con, worker_ids, title, body, url='/'):
    _send(users_of_workers(con, worker_ids), title, body, url)

def staff(con, title, body, url='/'):
    """管理層手機推播：「以後一律推到管理群通知即可」→ 預設不發（各事件都有派單管理群 LINE 卡）。
    settings staff_push=on 才恢復手機推播。"""
    import integrations as ix
    if ix.setting(con, 'staff_push') != 'on':
        return
    _send([r[0] for r in con.execute("SELECT id FROM users WHERE active=1 AND role IN ('admin','staff','finance')")], title, body, url)

def subscribe(con, user, p):
    if not str(p.get('endpoint', '')).startswith('https://'):
        raise ValueError('通知訂閱資料不符')
    con.execute('INSERT INTO push_subs(user_id,endpoint,p256dh,auth,ua) VALUES(?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,p256dh=excluded.p256dh,auth=excluded.auth',
                (user['id'], p['endpoint'], p['p256dh'], p['auth'], str(p.get('ua', ''))[:200]))
    return {'saved': True, 'devices': con.execute('SELECT COUNT(*) FROM push_subs WHERE user_id=?', (user['id'],)).fetchone()[0]}

def unsubscribe(con, user, p):
    con.execute('DELETE FROM push_subs WHERE endpoint=? AND user_id=?', (p.get('endpoint', ''), user['id']))
    return {'saved': True}
