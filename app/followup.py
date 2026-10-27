"""客戶端報價追蹤（掃流程發現：報價傳出去客戶沒簽、估價照片沒傳齊，都沒人追）。

每天 10:00 後跑一次（同一張報價每種提醒只發一次）：
- 照片還沒傳齊、超過 1 天：提醒客戶「還差 N 張就能報價」
- 報價單傳出 2 天沒簽：提醒客戶（附查看並簽約）
- 報價單到期那天還沒簽：最後提醒
- 過期沒簽、照片 3 天沒傳 → 列進管理群「人工追」（automation.chase 的清單）
"""
import datetime as dt
import json
import core as c
import integrations as ix

def target(con, q):
    import line_bot as lb
    r = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone() if q['inquiry_id'] else None
    if r and r[0]:
        return q['account'] or 'demoshop', r[0]
    if q['customer_id']:
        acc, uid = lb.customer_target(con, q['customer_id'])
        if acc == (q['account'] or 'demoshop'):
            return acc, uid
    return q['account'] or 'demoshop', None

def _sent(con, qid, kind):
    return con.execute('SELECT 1 FROM line_outbox WHERE kind=? AND alt LIKE ?', (kind, f'%{qid}%')).fetchone()

def run(con):
    import line_bot as lb, booking
    now = dt.datetime.now(c.TZ)
    if now.strftime('%H:%M') < '10:00' or ix.setting(con, 'followup_day') == now.date().isoformat():
        return []
    ix.set_setting(con, 'followup_day', now.date().isoformat())
    today = now.date(); todo = []
    for q in c.rows(con, "SELECT * FROM quotes WHERE status='draft' AND stage IN ('photos','sent')"):
        f = json.loads(q['form'] or '{}')
        if f.get('source') == '同事開單':          # 同事手動開的報價（PDF 另外傳）不自動催
            continue
        acc, uid = target(con, q)
        name = f.get('company') or f.get('name') or '客戶'
        if q['stage'] == 'photos':
            try:
                mm = json.loads(ix.setting(con, f"qmore:{q['id']}") or '{}')
            except ValueError:
                mm = {}
            age = (today - dt.date.fromisoformat(max(q['created_at'][:10], (mm.get('at') or '')[:10]))).days      # 稽核：管理層請客戶補照片 → 從那天重算
            n = con.execute('SELECT COUNT(*) FROM quote_photos WHERE quote_id=?', (q['id'],)).fetchone()[0]
            need = f"還差您補的資料：{mm['why']}" if mm.get('why') else f"還差 {max(1, booking.MIN_PHOTOS - n)} 張照片（每個要服務的區域拍一張）"
            if 1 <= age < 3 and uid and not _sent(con, q['id'], 'fu_photos'):         # 一次只發一則（以前同一天可能 2～3 則一起來）
                m = lb.card_text('還差一點資料就能報價 📷', f"您好 😊 線上估價{need}，直接在這裡傳就可以，收齊後主管會盡快報價。（估價單 {q['id']}）", brand=acc)
                lb.queue(con, acc, uid, 'fu_photos', f"估價照片提醒 {q['id']}", m)
            elif 3 <= age < 7 and uid and not _sent(con, q['id'], 'fu_photos2'):          # 負責人 全部自動 → 第 3 天再提醒一次、第 7 天自動結案
                m = lb.card_text('估價還差一點資料 📷', f"您好 😊 估價單 {q['id']} {need}，直接在這裡傳就可以，收齊後主管就會報價 🙏", brand=acc)
                lb.queue(con, acc, uid, 'fu_photos2', f"估價照片提醒（第二次）{q['id']}", m)
            if age >= 7:
                con.execute("UPDATE quotes SET status='cancelled' WHERE id=? AND status='draft'", (q['id'],))
                c.audit(con, 'quote_photos_close', 'quotes', q['id'], after={'days': age})
                if uid and not _sent(con, q['id'], 'fu_photos_close'):
                    lb.queue(con, acc, uid, 'fu_photos_close', f"估價單先關閉 {q['id']}", lb.card_text('估價單先幫您關掉囉', f"您好 😊 估價單 {q['id']} 一直沒收到照片，先幫您關掉了；之後需要服務，直接在這裡跟我們說就好 🙏", brand=acc))
            continue
        sent = dt.date.fromisoformat((q['sent_at'] or q['created_at'])[:10])
        expire = dt.date.fromisoformat(booking.valid_until(q)) if q['sent_at'] or q['valid_until'] else sent + dt.timedelta(days=booking.VALID_DAYS)   # 管理層指定的期限優先
        if q['date']:                    # 服務日先到就簽不了了 → 以較早的為準
            expire = min(expire, dt.date.fromisoformat(q['date']) - dt.timedelta(days=1))
        if today >= expire + dt.timedelta(days=15):         # 負責人 過期 14 天自動結案（客戶之後按「請重新報價」一樣會通知）
            con.execute("UPDATE quotes SET status='cancelled' WHERE id=? AND status='draft'", (q['id'],))
            c.audit(con, 'quote_expired_close', 'quotes', q['id'], after={'expired': expire.isoformat()})
            continue
        link = f"{lb.BASE_URL}/q/{q['token']}"
        if today >= expire + dt.timedelta(days=1):          # 負責人 自動 → 告訴客戶過期了、要的話按「請重新報價」；14 天自動結案
            if uid and not _sent(con, q['id'], 'fu_expired'):
                m = lb.flex(f"報價單已過期 {q['id']}", lb._bubble(lb._header('報價單已過期', name, brand=acc),
                    [lb._t('您好 😊 上次的報價單已經過期了；還需要服務的話，打開按「請重新報價」，我們會再幫您確認時間和價格 🙏', wrap=True)],
                    [lb._btn('打開報價單', uri=link, primary=True)]))
                lb.queue(con, acc, uid, 'fu_expired', m[0]['altText'], m)
            continue
        if not uid:
            continue
        if today == expire and not _sent(con, q['id'], 'fu_quote_last'):
            m = lb.flex(f"報價單今天到期 {q['id']}", lb._bubble(lb._header('報價單今天到期 ⏰', name, brand=acc),
                [lb._t('您的報價單今天是最後一天有效，確認沒問題請線上簽約，我們就幫您保留檔期；有任何問題直接在這裡留言 🙏', wrap=True)],
                [lb._btn('查看並簽約', uri=link, primary=True)]))
            lb.queue(con, acc, uid, 'fu_quote_last', m[0]['altText'], m)
        elif (today - sent).days >= 2 and today < expire and not _sent(con, q['id'], 'fu_quote'):
            m = lb.flex(f"報價單等您確認 {q['id']}", lb._bubble(lb._header('報價單等您確認 🧾', name, brand=acc),
                [lb._t(f"您好 😊 前幾天傳給您的報價單還沒簽收，有效到 {lb.md(expire.isoformat())}。內容有疑問、想調整範圍或時間，點下面讓我們知道，專人會幫您調整。", wrap=True)],
                [lb._btn('查看並簽約', uri=link, primary=True), lb._btn('價格超出預算，想調整', data=f"a=qfb&q={q['id']}&r=budget"),
                 lb._btn('想分次做／先做重點區域', data=f"a=qfb&q={q['id']}&r=split"), lb._btn('想跟專人通話', data=f"a=qfb&q={q['id']}&r=call")]))      # 
            lb.queue(con, acc, uid, 'fu_quote', m[0]['altText'], m)
    ix.set_setting(con, 'followup_todo', json.dumps(todo, ensure_ascii=False))
    return todo
