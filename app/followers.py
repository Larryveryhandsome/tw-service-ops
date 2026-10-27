"""LINE 全部好友同步（申請 LINE 認證帳號；通過後把「從沒互動過」的好友也抓進來，用 LINE 名稱比對業主資料）。

- 每天 09:30 後各品牌檢查一次：/v2/bot/followers/ids 從「不能用」（403，未認證）變成可以用 → 管理群通知、背景同步。
- 同步：全部好友代號 → 系統沒有的才建（onboard='followers'）→ 抓 LINE 名稱、頭像。不會傳任何訊息給他們。
- 比對：交給「LINE 配對」（pairing.candidates），只依證據；店長在中控台一個一個確認，不自動綁定。
- 打 LINE 在鎖外（背景執行緒），寫資料庫才進鎖（ledger.LOCK＝server.LOCK）；測試環境不開執行緒。
"""
import contextlib
import datetime as dt
import os
import threading
import core as c
import integrations as ix

ACCS = ('demoshop', 'homeline', 'bizline')

def tick(con, now=None):
    import line_bot as lb, brands
    now = now or dt.datetime.now(c.TZ)
    if now.strftime('%H:%M') < '09:30':
        return 0
    today, n = now.date().isoformat(), 0
    for acc in ACCS:
        if ix.setting(con, f'followers_ok:{acc}') or ix.setting(con, f'followers_chk:{acc}') == today:
            continue
        ix.set_setting(con, f'followers_chk:{acc}', today)
        if not ix.secret(con, 'line_token_' + acc):
            continue
        try:
            lb.api(con, '/v2/bot/followers/ids?limit=1', acc=acc)
        except ValueError:
            continue                                   # 403＝還沒通過認證；其他錯誤明天再試
        ix.set_setting(con, f'followers_ok:{acc}', c.now()); n += 1
        lb.admin_notify(con, f'✅ {brands.name(acc)} 可以列出全部好友了（LINE 認證通過）',
                        [('接下來', '系統在背景把從沒互動過的好友抓進來，用 LINE 名稱比對業主資料；比對得上的會出現在中控台「LINE 配對」，請店長一個一個確認'),
                         ('不會', '不會傳任何訊息給這些好友')], '/#pairing', '#2E7D32')
        kick(acc)
    return n

def kick(acc):
    if os.environ.get('OPS_NO_PUSH'):
        return
    threading.Thread(target=_bg, args=(acc,), daemon=True).start()

def _bg(acc):
    try:
        sync(acc)
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            import ledger, line_bot as lb
            with ledger.LOCK, contextlib.closing(c.connect()) as con, con:
                lb.admin_notify(con, '⚠️ 全部好友同步失敗', [('品牌', acc), ('原因', str(e)[:120]), ('處理', '明天會再試；一直失敗請跟負責人說')], '', '#C62828')
                ix.set_setting(con, f'followers_ok:{acc}', '')
        except Exception:
            pass

def fetch(acc, tok, known):
    """鎖外：全部好友代號＋系統沒有的人的 LINE 名稱、頭像。回傳 (全部代號, {新代號: profile})。"""
    import line_bot as lb
    ids, start = [], None
    while True:
        r = lb.api(None, '/v2/bot/followers/ids?limit=1000' + (f'&start={start}' if start else ''), acc=acc, tok=tok)
        ids += r.get('userIds') or []
        start = r.get('next')
        if not start:
            break
    prof = {}
    for u in [x for x in ids if x not in known][:3000]:
        try:
            prof[u] = lb.api(None, f'/v2/bot/profile/{u}', acc=acc, tok=tok)
        except Exception:
            prof[u] = {}
    return ids, prof

def save(con, acc, ids, prof):
    """鎖裡：建新好友（不發訊息），系統以為封鎖了但其實還是好友的改回好友。回傳 {'all','new','matched'}。"""
    import pairing
    for u, p in prof.items():
        con.execute("INSERT OR IGNORE INTO line_users(account,user_id,display_name,picture_url,first_seen,last_seen,is_friend,onboard) VALUES(?,?,?,?,?,?,1,'followers')",
                    (acc, u, (p.get('displayName') or '')[:80], p.get('pictureUrl') or '', c.now(), ''))
    if ids:
        con.execute(f"UPDATE line_users SET is_friend=1 WHERE account=? AND is_friend=0 AND user_id IN ({','.join('?' * len(ids))})", (acc, *ids))
    matched = 0
    for u in prof:
        row = con.execute('SELECT * FROM line_users WHERE account=? AND user_id=?', (acc, u)).fetchone()
        if row and pairing.candidates(con, dict(row)):
            matched += 1
    c.audit(con, 'followers_sync', 'line_users', acc, after={'all': len(ids), 'new': len(prof), 'matched': matched})
    return {'all': len(ids), 'new': len(prof), 'matched': matched}

def sync(acc):
    import ledger, line_bot as lb, brands
    with ledger.LOCK, contextlib.closing(c.connect()) as con:
        tok = ix.secret(con, 'line_token_' + acc)
        known = {r[0] for r in con.execute('SELECT user_id FROM line_users WHERE account=?', (acc,))}
    ids, prof = fetch(acc, tok, known)
    with ledger.LOCK, contextlib.closing(c.connect()) as con, con:
        r = save(con, acc, ids, prof)
        lb.admin_notify(con, f'👥 {brands.name(acc)} 全部好友同步好了', [('全部好友', f"{r['all']} 位"), ('這次新認得', f"{r['new']} 位（以前從沒互動過）"),
                        ('比對得上業主／夥伴', f"{r['matched']} 位 → 中控台「LINE 配對」請店長確認（只依證據，不自動綁）")], '/#pairing', '#2E7D32')
    return r
