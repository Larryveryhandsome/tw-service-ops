"""官方 LINE 自動化（多品牌：示範服務店 demoshop、企業線 bizline、生活線 homeline；每個帳號各自的金鑰、客人、回覆）。
- 客戶永遠是「官方帳號一對一」：哪個品牌的客人就由哪個官方帳號發。
- 夥伴：官方帳號加入每位夥伴的個人群組（群組名「示範服務店-客戶11」）→ 系統依群組名自動對應夥伴；
  派工卡發到夥伴個人群組（一人一單），夥伴第一次按卡片就自動綁定 LINE。
- 管理群（群組名含「派單管理」）：新預約、報價簽收、緊急異動第一時間發到這裡。
- 接單大群（群組名含「接單管理」）：夥伴報到用。
- 發送先進 line_outbox（交易成功才送）；line_mode：off 不發／test 只發測試名單與管理群／on 正式。
- 回覆（reply）不計 LINE 每月則數，能用回覆就不推播。收到的事件原樣轉給舊示範服務店網站（4001）。"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import traceback
import urllib.error
import urllib.request
import core as c
import integrations as ix
import brands

BASE_URL = 'https://ops.example.com'
RELAY = {}          # 負責人：舊系統用不到了，不再轉送（以前轉給 127.0.0.1:4001）
APPLY_ACCOUNTS = ('demoshop', 'homeline', 'bizline')       # 生活線開放應徵；各品牌都要能正常招人（夥伴都是示範公司的，不分品牌）→ 企業線也開放
WORK_ACCOUNT = 'demoshop'                                           # 夥伴群組、管理群所在的官方帳號
BROWN, INK, SUB, CREAM, LINE_C = '#6B5A4E', '#1D1D1F', '#86868B', '#FAF9F6', '#E5E5EA'
WD = '一二三四五六日'
BRAND = brands.TITLES      # 品牌卡片抬頭：讀品牌設定表（新增品牌不用改程式）

# ───────────── 基本 ─────────────
def mode(con, acc=None):
    """每個品牌各自的發送模式（off／test／on）；沒設定就用總開關。"""
    return (ix.setting(con, f'line_mode_{acc}') if acc else '') or ix.setting(con, 'line_mode', 'test')

def _jset(con, key):
    try:
        return set(json.loads(ix.setting(con, key, '[]')))
    except ValueError:
        return set()

def test_users(con):
    return _jset(con, 'line_test_users')

def staff_users(con):
    s = _jset(con, 'line_staff_users') | {r[0] for r in con.execute('SELECT user_id FROM line_users WHERE is_staff=1')}
    v = getattr(c.CTX, 'viewing', None)            # 管理層正在用客戶／夥伴視角（一對一）：這次當一般使用者
    return s - {v} if v else s

VIEW_KEY = 'line_viewing'

def viewing(con):
    try:
        return json.loads(ix.setting(con, VIEW_KEY) or '{}')
    except ValueError:
        return {}

def _demo_ids(con):
    """示範客戶（示範服務店）＋示範案場＋示範夥伴：給管理層切換視角用，名字都有（示範），不會影響真客戶。"""
    cu = con.execute("SELECT id FROM customers WHERE name=?", (DEMO_TAG + '客戶',)).fetchone()
    cid = cu[0] if cu else c.save(con, 'customers', {'name': DEMO_TAG + '客戶', 'status': 'active', 'line_account': 'demoshop', 'channel': 'DEMO', 'notes': '示範用（管理層切換客戶視角）'})['id']
    site = con.execute('SELECT id FROM sites WHERE customer_id=?', (cid,)).fetchone()
    sid = site[0] if site else c.save(con, 'sites', {'customer_id': cid, 'name': '王小姐家（示範）', 'address': '○○縣○○市示範路 1 號',
                                                    'service_type': '一般到府服務', 'area': '大安區', 'checklist': '浴室水垢\n廚房油汙\n全室吸拖', 'entry_mode': 'home'})['id']
    wk = con.execute("SELECT id FROM workers WHERE name=?", (DEMO_TAG + '夥伴',)).fetchone()
    wid = wk[0] if wk else c.save(con, 'workers', {'name': DEMO_TAG + '夥伴', 'status': 'active', 'rate': '250', 'regions': '大安區', 'notes': '示範用（管理層切換夥伴視角）'})['id']
    return cid, sid, wid

def _demo_job(con, cid, sid, wid):
    """夥伴視角：今天一場示範工作（已排定），可以試打卡、拍照、補登、請業主開門。"""
    today = dt.datetime.now(c.TZ).date().isoformat()
    r = con.execute("SELECT o.id FROM orders o JOIN assignments a ON a.order_id=o.id AND a.worker_id=? AND a.status='accepted' WHERE o.date=? AND o.title LIKE ? AND o.status='confirmed'",
                    (wid, today, DEMO_TAG + '%')).fetchone()
    if r:
        return r[0]
    c.CTX.allow_overlap = True
    try:
        o = c.save(con, 'orders', {'title': DEMO_TAG + '一般到府服務', 'site_id': sid, 'date': today, 'start': '08:00', 'end': '20:00', 'required_workers': 1,
                                   'review_note': '示範工單', 'customer_confirmed': 1, 'scope': '浴室\n廚房\n客廳地板', 'notes': '這是示範工作，可以放心按'})
        c.save(con, 'assignments', {'order_id': o['id'], 'worker_id': wid, 'status': 'accepted', 'overlap_ok': 1})
        con.execute("UPDATE orders SET status='confirmed',confirmed_at=?,crew_ok='示範' WHERE id=?", (c.now(), o['id']))
    finally:
        c.CTX.allow_overlap = False
    return o['id']

def demo_booking(con, q):
    """測試視角送的估價：改掛（示範）客戶、報價標 demo（簽收後是示範工單，不派真的夥伴；2 天後自動取消）。q＝inquiries 那一列。"""
    cid = _demo_ids(con)[0]
    con.execute('UPDATE inquiries SET customer_id=? WHERE id=?', (cid, q['id']))
    for qid, form in con.execute('SELECT id,form FROM quotes WHERE inquiry_id=?', (q['id'],)).fetchall():
        f = json.loads(form or '{}'); f['demo'] = True
        con.execute('UPDATE quotes SET customer_id=?,form=? WHERE id=?', (cid, json.dumps(f, ensure_ascii=False), qid))
        admin_notify(con, f'🧪 估價 {qid} 是示範（管理層用客戶視角測試）', [('處理', '其他人不用理；測試的人可以照真的流程按（確認報價、傳給客戶），客戶視角會收到一樣的卡片'),
                                                               ('之後', '簽收後是示範工單，不會派給真的夥伴；2 天後自動取消')], '', '#8E8E93')
    c.audit(con, 'demo_booking', 'inquiries', q['id'], after={'customer_id': cid})
    return con.execute('SELECT * FROM inquiries WHERE id=?', (q['id'],)).fetchone()

def view_as(con, acc, uid, kind):
    """管理層切換成客戶／夥伴視角（負責人 隨時能用客戶、夥伴的視角操作，才知道問題在哪）。打「回管理」切回。"""
    if acc != WORK_ACCOUNT:
        return _txt('請在示範服務店官方帳號切換視角')
    st = viewing(con)
    lu = con.execute('SELECT customer_id,worker_id,is_staff FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone()
    orig = (st.get(uid) or {}).get('orig') or {'customer_id': lu['customer_id'] if lu else None, 'worker_id': lu['worker_id'] if lu else None, 'is_staff': lu['is_staff'] if lu else 0}
    cid, sid, wid = _demo_ids(con)
    if kind == 'worker':
        con.execute('UPDATE line_users SET worker_id=?,customer_id=NULL,is_staff=0 WHERE account=? AND user_id=?', (wid, acc, uid))
        _demo_job(con, cid, sid, wid)
        rid = ix.setting(con, 'worker_richmenu_id')
    else:
        con.execute('UPDATE line_users SET customer_id=?,worker_id=NULL,is_staff=0 WHERE account=? AND user_id=?', (cid, acc, uid))
        rid = ix.setting(con, 'customer_richmenu_id')
    st[uid] = {'as': kind, 'orig': orig, 'at': c.now()}
    ix.set_setting(con, VIEW_KEY, json.dumps(st))
    try:
        api(con, f'/v2/bot/user/{uid}/richmenu/{rid}', {}, acc=acc) if rid else api(con, f'/v2/bot/user/{uid}/richmenu', method='DELETE', acc=acc)
    except ValueError:
        pass
    who = '客戶' if kind != 'worker' else '夥伴'
    return _txt(f"👀 已切換成{who}視角（{DEMO_TAG}{who}）\n下面的選單、按到的卡片都跟{who}一樣；用的是示範資料，不會影響真的客戶和夥伴。"
                + ('\n今天有一場示範工作，可以試「今天的工作」：打卡、拍照、補登、請業主開門。' if kind == 'worker' else '\n可以試預約、報價、我的資料、商城（打「商城」）。')
                + '\n\n要切回管理選單：打「回管理」。')

def linepay_demo(con, acc, uid):
    """LINE Pay 審核示範（負責人 LINE Pay 要看客戶在 LINE 裡怎麼付款）：切成客戶視角 → 傳示範「付款通知」卡 → 按 LINE Pay 走一次測試付款。
    用示範客戶＋今天的示範工單；只在測試模式（sandbox，不會真的扣款），真的客戶看不到 LINE Pay 按鈕；打「回管理」切回並取消示範款項。"""
    import linepay
    if acc != WORK_ACCOUNT:
        return _txt('請在示範服務店官方帳號（一對一）打「LINE Pay 示範」')
    if not linepay.ready(con, 'demoshop'):
        return _txt('還沒貼 LINE Pay 測試金鑰 🙏\n1. 登入 LINE Pay 商家後台（測試帳號）› 開發者工具 › 管理連結金鑰，複製 Channel ID、Channel Secret Key\n'
                    '2. 中控台「更多 › 設定 › LINE Pay 線上付款」：品牌選示範服務店（或共用）、模式選「測試」，貼上、儲存，按「測試連線」\n3. 再打一次「LINE Pay 示範」')
    if linepay.mode(con) == 'production':
        return _txt('現在是正式模式（會真的扣款），示範只在測試模式用。要示範請先到中控台把 LINE Pay 模式改成「測試」。')
    view_as(con, acc, uid, 'customer')
    cid, sid, wid = _demo_ids(con)
    oid = _demo_job(con, cid, sid, wid)
    q, rows = linepay.demo_quote(con, cid, oid, c.get(con, 'sites', sid))
    return _txt('🧪 LINE Pay 示範（測試模式，不會真的扣款）\n已切成客戶視角，下面是客戶完工當天收到的「付款通知」。照這個順序截圖給 LINE Pay：\n'
                '① 這張付款通知卡\n② 按「用 LINE Pay 付款」→ 報價單頁的付款區\n③ 再按「用 LINE Pay 付款」→ LINE Pay 付款畫面\n④ 付款完成回到報價單（顯示已收款）\n⑤ 回到聊天室收到「已收到您的款項」\n\n'
                '截完打「回管理」切回，示範款項會自動取消（不算收款）。') + card_payment(con, acc, rows, q['token'], '付款通知 🧾', '感謝您，今天的服務已完成')

def view_back(con, acc, uid):
    st = viewing(con); v = st.pop(uid, None)
    if not v:
        return _txt('你現在就是管理選單 👍')
    o = v.get('orig') or {}
    cid_ = o.get('customer_id') if o.get('customer_id') and con.execute('SELECT 1 FROM customers WHERE id=?', (o['customer_id'],)).fetchone() else None      # 原本綁的客戶／夥伴後來被合併掉 → 不還原（以前外鍵錯誤，切不回管理）
    wid_ = o.get('worker_id') if o.get('worker_id') and con.execute('SELECT 1 FROM workers WHERE id=?', (o['worker_id'],)).fetchone() else None
    con.execute('UPDATE line_users SET customer_id=?,worker_id=?,is_staff=? WHERE account=? AND user_id=?', (cid_, wid_, o.get('is_staff') or 0, acc, uid))
    ix.set_setting(con, VIEW_KEY, json.dumps(st))
    import linepay; linepay.demo_cleanup(con, everything=True)        # LINE Pay 示範款項：取消（不算收款）
    if not any(x.get('as') == 'worker' for x in st.values()):        # 沒人在用夥伴視角 → 示範工作收掉（避免系統去提醒、追完工）
        for (oid,) in con.execute("SELECT id FROM orders WHERE title LIKE ? AND status IN ('review','draft','confirmed')", (DEMO_TAG + '%',)).fetchall():
            con.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
            con.execute("DELETE FROM line_outbox WHERE order_id=? AND status='queued'", (oid,))
    rid = ix.setting(con, 'staff_richmenu_id')
    try:
        if rid:
            api(con, f'/v2/bot/user/{uid}/richmenu/{rid}', {}, acc=acc)
    except ValueError:
        pass
    return _txt('✅ 已切回管理選單。剛剛看到哪裡怪怪的，可以直接打「回報：…」記下來。')

def basic_id(con, acc):
    r = con.execute('SELECT basic_id FROM line_accounts WHERE id=?', (acc,)).fetchone()
    return (r[0] if r and r[0] else '@000demo') if acc == 'demoshop' else (r[0] if r else '')

def api(con, path, payload=None, method=None, acc='demoshop', tok=None):
    tok = tok or ix.secret(con, 'line_token_' + acc)        # 背景發送先在鎖內取好金鑰，打 LINE 時不碰資料庫
    if not tok:
        raise ValueError(f'{acc} 官方帳號尚未設定金鑰')
    req = urllib.request.Request('https://api.line.me' + path, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'},
                                 method=method or ('POST' if payload is not None else 'GET'))
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read(); return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise ValueError(f'LINE 回應 {e.code}：{e.read()[:200].decode("utf-8", "replace")}') from None
    except (OSError, ValueError) as e:      # 斷線、逾時：當作失敗，不要讓整批交易回滾後重送
        raise ValueError(f'連線 LINE 失敗：{e}') from None

def api_headers(con, path, payload, acc='demoshop'):
    """跟 api 一樣，但也回傳回應標頭（指定對象群發要拿 X-Line-Request-Id 查進度）。測試環境不連線。"""
    import os
    if os.environ.get('OPS_NO_PUSH'):
        return {}, {'x-line-request-id': 'test-request'}
    tok = ix.secret(con, 'line_token_' + acc)
    req = urllib.request.Request('https://api.line.me' + path, data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read(); return (json.loads(raw) if raw else {}), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        raise ValueError(f'LINE 回應 {e.code}：{e.read()[:200].decode("utf-8", "replace")}') from None
    except (OSError, ValueError) as e:
        raise ValueError(f'連線 LINE 失敗：{e}') from None

def _cust_media(con, acc, ev, lu, kind):
    """業主在 LINE 傳的現場照片／影片 → 接到他 14 天內最近的工單（派工卡「看業主傳的現場照片」看得到）。回傳有沒有收。"""
    import siteinfo
    oid = siteinfo.nearest_order(con, lu['customer_id'])
    if not oid:
        return False
    try:
        raw = content(con, ev['message']['id'], acc, (25 if kind == 'video' else 8) * 1024 * 1024 + 1)
    except Exception:
        return False
    if len(raw) > (25 if kind == 'video' else 8) * 1024 * 1024:
        admin_notify(con, '⚠️ 業主傳的影片太大沒收進工單', [('處理', '請在官方 LINE 聊天室看，必要時存到工單')], f'/#o/{oid}', '#C47F00', oid=oid)
        return False
    mime = 'video/mp4' if kind == 'video' else ('image/png' if raw[:4] == b'\x89PNG' else 'image/jpeg')
    siteinfo.add_media(con, oid, lu['customer_id'], raw, mime)
    return True

def content(con, message_id, acc='demoshop', limit=2 * 1024 * 1024 + 1):
    """下載客戶傳的圖片（api-data.line.me）。limit：記帳收 PDF 掃描檔放大到 10MB。"""
    tok = ix.secret(con, 'line_token_' + acc)
    req = urllib.request.Request(f'https://api-data.line.me/v2/bot/message/{message_id}/content', headers={'Authorization': 'Bearer ' + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(limit)

def scope_text(o, n=90):
    if (o or {}).get('scope_hold'):          # 詳細需求還沒審核 → 夥伴看不到
        return ''
    """給夥伴看的服務範圍（只有範圍，不含價格、客戶資料）。"""
    import board
    lines = [x.strip() for x in board._no_money(o.get('scope') or '').splitlines() if x.strip()]      # 盤點：範圍也要過濾金額
    return '、'.join(lines)[:n] if lines else ''

def verify(con, raw, signature, acc='demoshop'):
    secret = ix.secret(con, 'line_secret_' + acc)
    if not secret or not signature:
        return False
    mac = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(mac, signature)

def md(d):
    x = dt.date.fromisoformat(d)
    return f'{x.month}/{x.day}（{WD[x.weekday()]}）'

def _tsig(o):
    """時間簽章（放在確認按鈕上）：日期＋開始時間；工單時間改了，舊卡片按「確認」就認得出來。"""
    return (str(o['date'] or '') + str(o['start'] or '')).replace('-', '').replace(':', '')

def when(o):
    return f"{md(o['date'])} {o['start']}–{o['end']}"

def order_ctx(con, order_id):
    o = c.get(con, 'orders', order_id)
    s = dict(con.execute('SELECT * FROM sites WHERE id=?', (o['site_id'],)).fetchone() or {}) if o['site_id'] else {}
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o['customer_id'] else {}
    crew = c.rows(con, "SELECT a.*,w.name,w.id wid FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (o['id'],))
    import board
    return o, s, cu, crew, board.area_of(s)

def brand_of(cu):
    return brands.of_customer(cu)

# ───────────── 對象（誰收、由哪個帳號發） ─────────────
def customer_target(con, customer_id):
    if not customer_id:
        return None, None
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone() or {})
    acc = brand_of(cu)
    r = con.execute('SELECT user_id FROM line_users WHERE account=? AND customer_id=? AND is_friend=1 ORDER BY last_seen DESC LIMIT 1', (acc, customer_id)).fetchone()
    if not r and not cu.get('line_account'):        # 舊資料還沒標品牌：才看它實際綁在哪個帳號
        r2 = con.execute('SELECT account,user_id FROM line_users WHERE customer_id=? AND is_friend=1 ORDER BY last_seen DESC LIMIT 1', (customer_id,)).fetchone()
        if r2:                                       # 稽核：順便記下品牌，之後卡片抬頭、發送帳號才會一致（以前抬頭示範服務店、卻從生活線帳號送）
            con.execute("UPDATE customers SET line_account=? WHERE id=? AND COALESCE(line_account,'')=''", (r2[0], customer_id))
        return (r2[0], r2[1]) if r2 else (acc, None)
    return acc, (r[0] if r else None)

def worker_target(con, worker_id):
    """負責人 夥伴一律官方 1 對 1（不再用個人群組）。沒綁定／封鎖 → (帳號, None)，由 to_worker 轉到派單管理群。"""
    u = con.execute('SELECT account,user_id FROM line_users WHERE worker_id=? AND is_friend=1 ORDER BY last_seen DESC LIMIT 1', (worker_id,)).fetchone()
    return (u[0], u[1]) if u else (WORK_ACCOUNT, None)

ONSITE_RELAY_SKIP = {'prestart', 'eta_ask', 'arrive_nudge', 'door', 'near', 'before_nudge', 'leave_nudge'}

def to_worker(con, worker_id, kind, alt, messages, order_id=''):
    """發給夥伴：有一對一就直接發；發不到（還沒綁、封鎖）→ 轉到派單管理群，請管理層打電話問／轉達。回傳收件代號（去重用）。"""
    acc, to = worker_target(con, worker_id)
    if to:
        queue(con, acc, to, kind, alt, messages, order_id); return to
    if order_id and kind in ONSITE_RELAY_SKIP and con.execute(
            "SELECT 1 FROM assignments a JOIN checkins k ON k.assignment_id=a.id WHERE a.order_id=? AND a.worker_id!=? AND a.status='accepted' AND COALESCE(k.arrived_at,'')!=''",
            (order_id, worker_id)).fetchone():      # 同組已經有人到場（一起去的）→ 現場提醒不用請管理群打電話代問
        c.audit(con, 'relay_skip', 'assignments', worker_id, after={'kind': kind, 'order': order_id, 'why': 'teammate_arrived'})
        return 'skip:' + worker_id
    g = admin_group(con)
    if g:
        w = dict(con.execute('SELECT name,phone FROM workers WHERE id=?', (worker_id,)).fetchone() or {})
        note = ping(con, f"📵 {w.get('name', '夥伴')} 的 LINE 收不到（還沒綁官方 LINE 或已封鎖），請打電話 {w.get('phone') or '（沒有電話）'} 問他／轉達下面這則；"
                         f"能接的話請到中控台幫他接單。也請他綁定官方 LINE（中控台「夥伴」點他 →「複製夥伴綁定訊息」轉傳給他），之後就會直接收到。")
        queue(con, WORK_ACCOUNT, g, kind if kind.startswith('relay_w') else f'relay_w:{kind}', f"📵 代問 {w.get('name', '夥伴')}：{alt}"[:390], ([note] + list(messages))[:5], order_id)
    return 'admin:' + worker_id

def worker_pic(con, worker_id):
    r = con.execute("SELECT picture_url FROM line_users WHERE worker_id=? AND picture_url!='' LIMIT 1", (worker_id,)).fetchone()
    return r[0] if r else ''

def ping(con, text, who=None):
    """派單管理群裡 @標記管理層（負責人 官方帳號要能標記我們）。預設標 settings line_ping，沒設＝店長、客服（負責人不收雜事）。"""
    ids = list(who) if who else (sorted(_jset(con, 'line_ping')) or sorted(u for u in staff_users(con) if u != OWNER_UID))
    subs = {f'u{i}': {'type': 'mention', 'mentionee': {'type': 'user', 'userId': u}} for i, u in enumerate(ids[:5])}
    return {'type': 'textV2', 'text': (' '.join('{' + k + '}' for k in subs) + ' ' if subs else '') + text[:4000], 'substitution': subs} if subs else {'type': 'text', 'text': text[:4900]}

def admin_group(con):
    return ix.setting(con, 'line_admin_group') or (con.execute("SELECT group_id FROM line_groups WHERE role='admin' AND active=1 ORDER BY joined_at DESC LIMIT 1").fetchone() or [''])[0]

QUIET = ('22:00', '08:00')        # 負責人 安靜時段，非緊急訊息延到 08:00
URGENT = {'door_req_c', 'near_c', 'late_c', 'door_wait', 'door_esc', 'srep_c', 'srep_reply', 'arrive_nudge', 'eta_ask', 'prestart', 'before_nudge', 'leave_nudge', 'crew_left', 'iv_remind'}

def urgent_order(o, now=None):
    """明早 10:00 前開工的單（今晚就要找到人／通知到）：安靜時段也照常處理。"""
    if not (o['date'] and o['start']):
        return False
    return f"{o['date']}T{o['start']}" <= _wake_at(now)[:11] + '10:00'

def held(con, oid):
    """這張單有卡片因為安靜時段還在等 08:00 才送 → 派工的等待時間還不能開始算。
    管理層「擋派工」（settings dispatch_hold:<工單>）也算 → 不自動詢問、不改邀下一批。"""
    if ix.setting(con, f'dispatch_hold:{oid}') or ix.setting(con, f'urgent:{oid}'):      # 每日檢查：緊急加派進行中由 urgent.tick 問
        return True
    return bool(con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND status='queued' AND COALESCE(next_at,'')>? AND COALESCE(error,'')!='throttle'", (oid, c.now())).fetchone())

def _stale_held(con, m):
    """延後到 08:00 的卡片送出前重新檢查：工單取消／完工了、派工卡的人已經不在邀請名單（額滿、被換掉）→ 不送。"""
    if not m['order_id']:
        return False
    o = con.execute('SELECT status,dispatch,required_workers FROM orders WHERE id=?', (m['order_id'],)).fetchone()
    if not o or o['status'] in ('cancelled', 'archived', 'completed'):
        return True
    if m['kind'] == 'dispatch':
        w = con.execute('SELECT worker_id FROM line_users WHERE user_id=? AND worker_id IS NOT NULL LIMIT 1', (m['to_id'],)).fetchone()
        crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (m['order_id'],)).fetchone()[0]
        if crew >= (o['required_workers'] or 1) or (w and not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status IN ('invited','applied')", (m['order_id'], w[0])).fetchone()
                                                     and o['dispatch'] != 'open'):
            return True
    if m['kind'] in THROTTLE and m['kind'] != 'dispatch':          # 延後的更新卡，送出前這位夥伴已經不在這場 → 不送
        w = con.execute('SELECT worker_id FROM line_users WHERE user_id=? AND worker_id IS NOT NULL AND customer_id IS NULL LIMIT 1', (m['to_id'],)).fetchone()
        if w and not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status IN ('accepted','invited','applied')", (m['order_id'], w[0])).fetchone():
            return True
    return False

def quiet(now=None):
    import os
    if os.environ.get('OPS_NO_PUSH') and not os.environ.get('OPS_QUIET_TEST'):      # 測試／稽核環境：結果不能看幾點跑
        return False
    t = (now or dt.datetime.now(c.TZ)).strftime('%H:%M')
    return t >= QUIET[0] or t < QUIET[1]

def _wake_at(now=None):
    now = now or dt.datetime.now(c.TZ)
    d = now.date() + dt.timedelta(days=1) if now.strftime('%H:%M') >= QUIET[0] else now.date()
    return dt.datetime.combine(d, dt.time(8, 0), tzinfo=c.TZ).isoformat(timespec='seconds')

def _hold_until(con, to, kind, order_id):
    """安靜時段要不要延後：管理群／管理層不延；開門、晚到等現場訊息不延；服務在早上 10 點前開始的（今晚就得知道）不延。"""
    if not quiet() or to == admin_group(con) or to in staff_users(con) or kind.split(':')[-1] in URGENT:
        return ''
    wake = _wake_at()
    if order_id:
        o = con.execute('SELECT date,start FROM orders WHERE id=?', (order_id,)).fetchone()
        if o and o['date'] and o['start'] and f"{o['date']}T{o['start']}" <= wake[:11] + '10:00':
            return ''
    return wake

THROTTLE = {'aib_w', 'change', 'site_change', 'confirmed_w', 'dispatch', 'scope_w'}      # 「更新類」給夥伴的訊息
THROTTLE_H = 2
THROTTLE_GROUP = {'site_change': 'change'}           # 時間異動、地點異動算同一類

def _throttle(con, to, kind, order_id):
    """（夥伴訊息太多、派工更新太頻繁）：同一場、同一位夥伴的更新類訊息 2 小時內最多送一則。
    - 只限夥伴（業主的改期卡要馬上到，業主要按「收到」才排定）。
    - 同一種還沒送的舊訊息一律作廢（只送最新那則；開工前 3 小時內也一樣，避免舊時間的卡比新卡晚到）。
    - 上一則送出不到 2 小時 → 延到上一則＋2 小時；開工前 3 小時內的更新不延。回傳延後到的時間（''＝照常送）。"""
    if kind not in THROTTLE or not order_id or not str(to).startswith('U') or to in staff_users(con) or ix.setting(con, 'worker_throttle') == 'off':
        return ''
    if not con.execute('SELECT 1 FROM line_users WHERE user_id=? AND worker_id IS NOT NULL AND customer_id IS NULL', (to,)).fetchone():
        return ''
    o = con.execute('SELECT date,start FROM orders WHERE id=?', (order_id,)).fetchone()
    now = dt.datetime.now(c.TZ)
    try:
        start = dt.datetime.fromisoformat(f"{o['date']}T{o['start']}").replace(tzinfo=c.TZ) if o and o['date'] and o['start'] else None
    except ValueError:
        start = None
    ks = sorted(k_ for k_ in THROTTLE if THROTTLE_GROUP.get(k_, k_) == THROTTLE_GROUP.get(kind, kind))      # 只跟「同一類」比（派工卡不會擋住已排定卡）
    same = con.execute("SELECT id,next_at FROM line_outbox WHERE to_id=? AND order_id=? AND kind=? AND status='queued'", (to, order_id, kind)).fetchall()
    for r in same:                                     # 還沒送的同一種舊訊息 → 作廢，只送最新的
        con.execute("UPDATE line_outbox SET status='skipped:merged',error='有更新的同一則，只送最新的' WHERE id=?", (r['id'],))
    waits = [dt.datetime.fromisoformat(r['next_at']) for r in same if r['next_at'] and r['next_at'] > now.isoformat()]
    last = con.execute(f"SELECT MAX(COALESCE(sent_at,created_at)) FROM line_outbox WHERE to_id=? AND order_id=? AND kind IN ({','.join('?' * len(ks))}) AND status IN ('sent','sending')",
                       (to, order_id, *ks)).fetchone()[0]
    if waits:
        due = max(waits)
    elif last and dt.datetime.fromisoformat(last) + dt.timedelta(hours=THROTTLE_H) > now:
        due = dt.datetime.fromisoformat(last) + dt.timedelta(hours=THROTTLE_H)
    else:
        return ''
    if start and start - due < dt.timedelta(hours=3):
        return ''
    return due.isoformat(timespec='seconds')

def _brand_worker(con, acc, uid):
    """這個人是不是在別的品牌帳號（不是示範服務店）應徵／上工的夥伴。"""
    if acc == WORK_ACCOUNT or not uid or uid[:1] != 'U':
        return False
    r = con.execute('SELECT worker_id,customer_id,onboard FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone()
    if r and not r['customer_id'] and (r['worker_id'] or r['onboard'] == 'applicant'):
        return True
    if r and r['customer_id']:
        return False
    try:                                   # 每日檢查：電話對到舊夥伴、從這個帳號再應徵（還沒綁）→ 面試訊息也寫這個品牌
        return bool(con.execute('SELECT 1 FROM interviews WHERE line_acc=? AND line_uid=?', (acc, uid)).fetchone())
    except Exception:
        return False

def rebrand(messages, acc):
    """在生活線帳號應徵、上工的夥伴 → 卡片抬頭和文字裡的「示範服務店」換成這個帳號的品牌（夥伴是在這個帳號加入的）。"""
    name = brands.name(acc)
    s = json.dumps(messages, ensure_ascii=False)
    s = s.replace(json.dumps(BRAND.get('demoshop') or 'DEMO', ensure_ascii=False), json.dumps(BRAND.get(acc) or name, ensure_ascii=False))
    return json.loads(re.sub('示範服務店|示範服務店|示範服務店', name, s))

QUIET_ADMIN = ('店長 在中控台回答了', '客服 在中控台回答了', '負責人 在中控台回答了', '🗑 工單取消', '✅ 已有夥伴接單', '📅 工單改期／異動', '👍 客戶確認完工',
               '🟢 ', '📣 夥伴公告已發出', '📣 ', '✅ 已自動排定', '🧹 示範服務店 年度保養', '🧹 生活小幫手 年度保養', '🧹 企業服務線 年度保養', '📷 ',
               '🛠 ', '🔧 ', '✅ 已同步', '🟢 【', '📦 ', '✅ LINE 額度恢復')      # 只是告知的管理群通知 → 不發 LINE（要人處理、要決定、要審核、緊急的照發）

DIGEST_KINDS = ('admin_review', 'admin_ask', 'cmsg', 'aib', 'quote_chase', 'mk_images')      # 一天三次彙整（審核、決定題、客戶留言、AI 交代、報價太久、行銷圖）

def queue(con, acc, to, kind, alt, messages, order_id=''):
    """放進發送佇列（跟著交易提交）。test 模式：只發測試名單和管理群；off：全部不發。安靜時段 22:00–08:00 非緊急的延到 08:00。"""
    if not to:
        return
    if _brand_worker(con, acc, to):
        messages = rebrand(messages, acc); alt = rebrand([alt], acc)[0]
    dd_skip = bool(order_id) and __import__('daidiu').customer_blocked(con, acc, to, kind, order_id)      # 垃圾代丟給業主只傳「已收走」
    m = mode(con, acc); status = 'queued'
    if dd_skip:
        status = 'skipped:daidiu'
    elif m == 'off':
        status = 'skipped:off'
    elif m == 'test' and to not in test_users(con) and to != admin_group(con):
        status = 'skipped:test'
    elif to == OWNER_UID and ix.setting(con, 'owner_push') != 'on':
        status = 'skipped:owner'           # 「未來私人這邊不用再推播訊息給我」（中控台看得到；owner_push=on 才恢復）
    elif to == admin_group(con) and kind in DIGEST_KINDS and ix.setting(con, 'digest_admin') != 'off':
        status = 'skipped:digest'          # 不急、要管理群處理的 → 不一件一則，一天三次彙整（pending.digest）
    elif to == admin_group(con) and ix.setting(con, 'quiet_admin') != 'off' and str(alt or '').lstrip().startswith(QUIET_ADMIN):
        status = 'skipped:quiet'           # 「訊息量控制，盡量精簡」→ 只是告知、不用人處理的不發 LINE（中控台、工單紀錄看得到）
    hold = _hold_until(con, to, kind, order_id) if status == 'queued' else ''
    thr = _throttle(con, to, kind, order_id) if status == 'queued' else ''
    err = 'quiet' if hold else ''
    if thr and thr > (hold or ''):
        hold, err = thr, 'throttle'                   # 送出前工單變了（取消、這位被拿掉）就不送；不算「派工等待」（held 不擋自動派工）
    con.execute('INSERT INTO line_outbox(created_at,account,to_id,kind,order_id,alt,payload,status,next_at,error) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (c.now(), acc, to, kind, order_id, alt[:390], json.dumps(messages, ensure_ascii=False), status, hold, err))
    return status

def flex(alt, bubble):
    return [{'type': 'flex', 'altText': alt[:390], 'contents': bubble}]

# ───────────── 圖卡元件 ─────────────
def _t(text, **kw):
    d = {'type': 'text', 'text': str(text) if text not in (None, '') else '—', 'wrap': True, 'color': INK, 'size': 'sm'}
    d.update(kw); return d

def _row(label, value):
    return {'type': 'box', 'layout': 'baseline', 'spacing': 'md', 'contents': [_t(label, color=SUB, size='sm', flex=2, wrap=False), _t(value, flex=6)]}

def _btn(label, *, data=None, uri=None, primary=False):
    action = {'type': 'postback', 'label': label[:20], 'data': data, 'displayText': label} if data else {'type': 'uri', 'label': label[:20], 'uri': uri}
    return {'type': 'button', 'action': action, 'style': 'primary' if primary else 'secondary', 'color': BROWN if primary else '#EFEDE8', 'height': 'sm'}

def _header(kicker, title, color=BROWN, brand='demoshop'):
    return {'type': 'box', 'layout': 'vertical', 'backgroundColor': CREAM, 'paddingAll': '18px', 'paddingBottom': '14px', 'contents':
            [_t(BRAND.get(brand) or BRAND.get('demoshop') or 'DEMO', size='xxs', color=SUB, weight='bold')] + ([_t(kicker, size='xs', color=color, weight='bold', margin='md')] if kicker else [])
            + ([_t(title, size='xl', weight='bold', margin='sm')] if title else [])}

def _bubble(header, rows, buttons=(), note='', avatar=None):
    body = []
    if avatar:
        body.append(avatar)
    body.append({'type': 'box', 'layout': 'vertical', 'spacing': 'sm', 'margin': 'md' if avatar else 'none', 'contents': rows})
    if note:
        body.append({'type': 'separator', 'margin': 'lg', 'color': LINE_C})
        body.append(_t(note, size='xs', color=SUB, margin='lg'))
    b = {'type': 'bubble', 'size': 'mega', 'header': header, 'body': {'type': 'box', 'layout': 'vertical', 'paddingAll': '18px', 'contents': body},
         'styles': {'header': {'backgroundColor': CREAM}}}
    if buttons:
        b['footer'] = {'type': 'box', 'layout': 'vertical', 'spacing': 'sm', 'paddingAll': '14px', 'contents': list(buttons)}
    return b

def _avatar(name, pic, title):
    img = ({'type': 'image', 'url': pic, 'size': '56px', 'aspectMode': 'cover', 'aspectRatio': '1:1'} if pic else
           {'type': 'box', 'layout': 'vertical', 'width': '56px', 'height': '56px', 'backgroundColor': BROWN, 'justifyContent': 'center',
            'contents': [_t(name[:1], color='#FFFFFF', align='center', size='xl', weight='bold')]})
    return {'type': 'box', 'layout': 'horizontal', 'spacing': 'lg', 'alignItems': 'center', 'contents': [
        {'type': 'box', 'layout': 'vertical', 'width': '56px', 'height': '56px', 'cornerRadius': '28px', 'contents': [img]},
        {'type': 'box', 'layout': 'vertical', 'contents': [_t(name, size='lg', weight='bold'), _t(title, size='xs', color=SUB)]}]}

# ───────────── 卡片 ─────────────
def card_dispatch(con, oid, preferred=False, wid=None):
    import board
    o, s, cu, crew, area = order_ctx(con, oid)
    need = o['required_workers'] - len(crew)
    hrs = round(c.minutes(o['start'], o['end']) / 60, 1) if o['start'] and o['end'] else ''
    rate = ''
    if wid:                  # 只顯示這位夥伴自己的時薪（不會看到別人的）
        r = con.execute('SELECT rate FROM assignments WHERE order_id=? AND worker_id=? AND rate IS NOT NULL', (oid, wid)).fetchone()
        rate = (r[0] if r else c.pay_rate(con, oid, wid)) or ''
    import garbage, daidiu
    if daidiu.is_daidiu(o):                   # 垃圾代丟：晚上門口收、固定酬勞
        rows = [_row('日期', when(o)), _row('地點', board.street_of(s)), _row('類型', f"垃圾代丟（{daidiu.time_text(o['start'])}門口收）"), _row('收的量', '正常家庭的量'),
                _row('酬勞', f'{daidiu.pay(con) // 100} 元（固定）')] + ([_row('注意', board._no_money(o['dispatch_note'])[:80])] if o['dispatch_note'] else [])
        btns = [_btn('我要接單', data=f'a=grab&o={oid}', primary=True), _btn('這次不行', data=f'a=pass&o={oid}')]
        return flex(f"【示範服務店派工】{when(o)} {area} 垃圾代丟", _bubble(_header('新的工作・先按先得', when(o)), rows, btns,
                    '接的人之後固定收這一戶（每週同一天），每次 100 元；哪次不行可以請假。接單後就看得到完整地址。'))
    tb = garbage.is_trash(o) and garbage.bags(con, o)[0]      # 垃圾清收：寫袋數、固定酬勞、清運方式（不寫時數、時薪、工具）
    rows = [_row('日期', when(o)), _row('地點', board.street_of(s)), _row('類型', o['title'] if tb else (s.get('service_type') or '服務')),
            _row('袋數', f'{tb} 袋（每袋約 100–120 公升）・缺 {max(need, 1)} 位') if tb else _row('時數', f'約 {hrs} 小時・缺 {max(need, 1)} 位')]
    if scope_text(o):
        rows.append(_row('範圍', scope_text(o)))
    import sitetools
    rows.append(_row('代丟', garbage.dispose(con)) if tb else _row('工具', sitetools.worker_text(s)))         # 要不要自備工具
    pay_t = board.est_pay(con, o, wid)[1] if wid else ''
    if tb:
        rows.append(_row('酬勞', garbage.pay_text(con, o)))
    elif pay_t:
        rows.append(_row('這場收入', pay_t))                 # 接單就看得到這場能賺多少（只有自己的）
    elif rate:
        rows.append(_row('時薪', f'{rate} 元'))
    if board._no_money(o['dispatch_note']):                 # 資安：派工卡的注意事項也過濾金額
        rows.append(_row('注意', board._no_money(o['dispatch_note'])[:80]))
    if board._no_money(s.get('checklist')) and not scope_text(o):      # 每次服務重點
        rows.append(_row('重點', board._no_money(s['checklist'])[:120]))
    import siteinfo
    if siteinfo.info(con, o):
        rows.append(_row('須知', '這裡有現場須知（鑰匙、備品等），接單後在提醒卡、工作頁看得到'))
    import siteroutine                           # 客戶01：這一期還沒做的定期特殊服務（冷氣濾網…）
    if siteroutine.text(con, o):
        rows.append(_row('定期', '這次要做 ' + siteroutine.text(con, o)[:200]))
    btns = [_btn('我要接單', data=f'a=grab&o={oid}', primary=True)]
    if siteinfo.has(con, o, 'q'):
        btns.append(_btn('看業主傳的現場照片', uri=siteinfo.url(con, oid, 'q')))      # 業主傳的現場說明照片同步到派工卡
    if preferred:
        btns.append(_btn('這時間不行，可改時間', data=f'a=alt&o={oid}'))
    else:
        btns.append(_btn('這次不行', data=f'a=pass&o={oid}'))      # 稽核 優化：回覆了，下一級可以早點問
    if wid:
        import worker_portal
        btns.append(_btn('看我的主控板', uri=worker_portal.portal_url(con, wid)))
    kicker = '業主指定您・優先邀請' if preferred else ('示範服務店指定邀請您' if o['dispatch_only'] else '新的工作・先按先得')
    if o['parent_order_id'] or (o['title'] or '').startswith('補清'):
        kicker = '🔁 補清・' + kicker                    # 補清的派工卡一眼看得出來
    note = ('這位業主平常都由您服務，先邀請您。時間不方便可以按「可改時間」，示範服務店會跟業主協調。' if preferred else
            '系統以按下的時間判定，先按先得。接單後 10 分鐘內可以按「臨時無法」取消。接單後就看得到完整地址和導航。')
    return flex(("【示範服務店派工・補清】" if kicker.startswith("🔁") else "【示範服務店派工】") + f"{when(o)} {area}" + ('（指定您）' if preferred else f' 缺 {max(need, 1)} 位'),
                _bubble(_header(kicker, when(o)), rows, btns, note))

def card_grabbed(con, oid, worker_name, wid=None):
    import worker_portal, board
    o, s, cu, crew, area = order_ctx(con, oid)
    pay_t = board.est_pay(con, o, wid)[1] if wid else ''
    return flex(f'✅ 接單成功：{when(o)}', _bubble(_header('✅ 接單成功', when(o), '#2E7D32'),
        [_row('地址', s.get('address') or board.street_of(s)), _row('類型', s.get('service_type') or '服務'), _row('夥伴', worker_name)] + ([_row('預估收入', pay_t)] if pay_t else []),
        [_btn('打開這場工作', uri=_work_link(con, oid, crew, wid) if wid else f'{BASE_URL}/#mine', primary=True), _btn('開導航（精準定位）', uri=__import__('sitegeo').nav_url(s)),
         _btn('臨時無法（10 分鐘內）', data=f'a=undo&o={oid}')],
        '進場方式、開門、現場聯絡會在客戶確認時間、排定後傳給你。前一天會再提醒。'))          # 接單後就看得到門牌＋精準定位

def card_taken(con, oid):
    o, *_ = order_ctx(con, oid)
    return flex('這筆已經有人接走了', _bubble(_header('已額滿', when(o), SUB), [_t('這筆已經有夥伴接走了，謝謝你！有新的工作會再通知你。', color=SUB)]))

def card_assigned(con, oid):
    import automation          # 負責人 業主不知道是誰去，改看夥伴經歷＋作品集
    o, s, cu, crew, area = order_ctx(con, oid)
    return flex(f"您的服務已安排好服務人員囉！{when(o)}", _bubble(
        _header('預約已安排', '專業服務人員已承接', brand=brand_of(cu)),
        [_row('日期', md(o['date'])), _row('時段', f"{o['start']}–{o['end']}"), _row('地點', s.get('name') or area),
         _row('服務', '🔁 補服務（不另收費）' if __import__('board').is_redo(o) else s.get('service_type') or '服務'),      # 每日檢查 #8：業主不會以為多訂一次要付錢
         _row('人員', automation.crew_summary(con, crew))],
        [_btn('確認這個時間 ✅', data=f'a=cok&o={oid}&t={_tsig(o)}', primary=True), _btn('看夥伴作品集', uri=automation.portfolio_url(con, oid)), _btn('需要改時間', data=f'a=cchg&o={oid}')]
        + ([_btn('換成別館', data=f'a=hx&o={oid}')] if multi_sites(con, o['customer_id']) else []),
        f"服務前一天（{md((dt.date.fromisoformat(o['date']) - dt.timedelta(days=1)).isoformat())}）會再通知您。若有異動，請提前告訴我們，謝謝！"))

def multi_sites(con, cid):
    """多館業主（同一位業主 2 個以上案場，例：客戶01 9／10／11 號館）：回傳全部案場；不是就回傳 []。"""
    rows = c.rows(con, 'SELECT id,name FROM sites WHERE customer_id=? ORDER BY name', (cid,)) if cid else []
    return rows if len(rows) > 1 else []

def set_house(con, oid, sid, who):
    """多館業主換館別：同一位業主、同一時間，只換館 → 不退回重排（人和時間都沒變）；已排的夥伴收到新地址，派單管理群知會（不用處理）。"""
    o0 = c.get(con, 'orders', oid)
    old = (con.execute('SELECT name FROM sites WHERE id=?', (o0['site_id'],)).fetchone() or ['—'])[0]
    new = c.get(con, 'sites', sid)
    con.execute('UPDATE orders SET site_id=?,version=version+1 WHERE id=?', (sid, oid))
    c.audit(con, 'order_house', 'orders', oid, before={'site_id': o0['site_id']}, after={'site_id': sid, 'by': who})
    import board
    board.log(con, oid, 'house_change', f"{old} → {new['name']}（{who}）")
    for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)):
        if o0['status'] == 'confirmed':          # 已排定才看得到地址：傳新的工作卡
            m = card_worker_remind(con, oid, f"📍 改到 {new['name']}", '#C47F00', a['worker_id'])
        else:
            m = card_text('館別更新', f"{when(o0)} 這場改到 {new['name']}，確認排定後會傳完整地址給你。", '#C47F00')
        to_worker(con, a['worker_id'], 'site_change', m[0]['altText'], m, oid)
    admin_notify(con, '🏠 業主換館別', [('工作', when(o0)), ('館別', f"{old} → {new['name']}"), ('處理', '系統已改好、通知夥伴新地址，不用處理')], '', '#0A84FF', oid=oid)
    return old, new['name']

def card_house_pick(con, oid):
    o, s, cu, crew, area = order_ctx(con, oid)
    others = [x for x in multi_sites(con, o['customer_id']) if x['id'] != o['site_id']]
    return _bubble(_header('換館別', f"{md(o['date'])}（{WD[dt.date.fromisoformat(o['date']).weekday()]}）{o['start']}", brand=brand_of(cu)),
                   [_row('目前', s.get('name') or '—')], [_btn(f"改成 {x['name']}", data=f"a=hxs&o={oid}&s={x['id']}") for x in others[:6]])

def card_house_month(con, cid, month):
    """每月 20 號：下個月的日期＋館別請業主確認；還沒排的請業主直接回覆哪幾天、哪一館。"""
    cu = c.get(con, 'customers', cid); acc = brand_of(cu); mo = int(month[5:])
    os_ = c.rows(con, "SELECT o.id,o.date,o.start,o.end,s.name sname FROM orders o JOIN sites s ON s.id=o.site_id WHERE o.customer_id=? AND substr(o.date,1,7)=? "
                      "AND o.status NOT IN ('cancelled','archived') ORDER BY o.date,o.start", (cid, month))
    if not os_:
        return flex(f'{mo} 月的服務要排哪幾天、哪一館？', _bubble(_header(f'{mo} 月服務排程 📅', cu['name'], brand=acc),
            [_t(f'{mo} 月還沒排服務。請直接在這裡回覆：哪幾天、哪一館（例如：{mo}/5 客戶08、{mo}/12 客戶10），我們排好會再傳給您確認 🙏', wrap=True)],
            [_btn('我知道了，等一下回覆', data='a=noop')]))
    rows = [_row(f"{md(o['date'])}（{WD[dt.date.fromisoformat(o['date']).weekday()]}）", f"{o['sname']}　{o['start']}–{o['end']}") for o in os_[:15]]
    if len(os_) > 15:
        rows.append(_t(f'…還有 {len(os_) - 15} 場', size='sm', color=SUB))
    return flex(f'{mo} 月服務排程：{len(os_)} 場，請確認館別', _bubble(_header(f'{mo} 月服務排程 📅', '請確認日期和館別', brand=acc), rows,
        [_btn('都正確 ✅', data=f'a=hm_ok&m={month}', primary=True), _btn('要換館別', data=f'a=hm_fix&m={month}')],
        '要加減場次或改日期，直接在這裡留言告訴我們。'))

def card_customer_remind(con, oid, word='明天'):
    import automation
    o, s, cu, crew, area = order_ctx(con, oid)
    import door
    em = door.mode_of(o, s)
    rows = [_row('地點', s.get('name') or area), _row('人員', automation.crew_summary(con, crew)), _row('進門', door.mode_text(s, o) if em else '還沒確認（我們會跟您確認）')]
    if em == 'home':               # 開門流程：業主在家開門 → 前一天先請他按「會來開門」
        rows.insert(0, _t(f"{word} {o['start']} 服務人員會到，需要您（或家人）開門 🚪", wrap=True, weight='bold'))
        btns = [_btn(f'我知道了，{word}會去開門 ✅', data=f'a=door_ack&o={oid}', primary=True), _btn('需要改期', data=f'a=cchg&o={oid}')]
    else:
        btns = [_btn('確認 ✅', data=f'a=cok&o={oid}&t={_tsig(o)}', primary=True), _btn('看夥伴作品集', uri=automation.portfolio_url(con, oid)), _btn('需要改期', data=f'a=cchg&o={oid}')]
    if multi_sites(con, o['customer_id']):           # 多館業主：前一天還能換館別
        btns.append(_btn('換成別館', data=f'a=hx&o={oid}'))
    return flex(f'{word} {o["start"]} 服務提醒', _bubble(_header(f'{word}見 👋', when(o), brand=brand_of(cu)), rows, btns,
        '需要開門、停車或特別交代的地方，直接在這裡留言給我們。'))

def card_worker_remind(con, oid, kicker='明天的工作 🧹', color=BROWN, worker_id=None):
    o, s, cu, crew, area = order_ctx(con, oid)
    crew = [x for x in crew if not worker_id or x['wid'] == worker_id] or crew
    nav = __import__('sitegeo').nav_url(s)              # 正確入口 → 國土測繪精準座標 → 地址搜尋
    rows = [_row('地區', c.place(area) if area else '—'), _row('地址', s.get('address') or '請看 App'), _row('類型', s.get('service_type') or o['title'] or '服務'),
            _row('時數', f"約 {round(c.minutes(o['start'], o['end']) / 60, 1):g} 小時")]
    if scope_text(o):
        rows.append(_row('範圍', scope_text(o, 120)))
    import sitetools
    rows.append(_row('工具', sitetools.worker_text(s)))
    import prepq
    if prepq.trash_text(s):
        rows.append(_row('垃圾', prepq.trash_text(s)))          # 業主說服務完垃圾怎麼處理
    if s.get('signin_photo'):
        rows.append(_row('簽到表', '這裡要拍簽到表（工作頁「簽到表照片」）'))
    import board
    if board._no_money(s.get('entry_notes')):
        rows.append(_row('進場', board._no_money(s['entry_notes'])[:120]))
    cts = [r for r in con.execute("SELECT name,role,phone FROM contacts WHERE customer_id=? AND role!='請款窗口' AND phone!='' LIMIT 2", (o['customer_id'],))] if o['customer_id'] and o['status'] == 'confirmed' else []
    if cts:                        # 盤點：現場聯絡人（家人、管理員、代管）——排定後才給夥伴
        rows.append(_row('現場聯絡', '、'.join(f"{r[0]}（{r[1]}）{r[2]}" for r in cts)))
    if board._no_money(s.get('checklist')) and not scope_text(o) and not o.get('scope_hold'):
        rows.append(_row('重點', board._no_money(s['checklist'])[:120]))
    if o.get('worker_note'):                     # 店長補充的提醒（prep.py）；AI 整理的業主交代獨立一行（不會把手寫的擠掉）
        import aibrief
        own_, aib_ = aibrief.split_note(o['worker_note'])
        if own_:
            rows.append(_row('提醒', own_[-160:]))
        if aib_:
            rows.append(_row('業主交代', aib_[-200:]))
    import door
    em = door.mode_of(o, s)
    if em:
        rows.append(_row('開門', door.mode_text(s, o) + ('（業主已確認會來開門 ✅）' if em == 'home' and o.get('door_ack') else '（業主還沒回覆，到了按「請業主開門」）' if em == 'home'
                                                    else '（到場打卡＝已進入，不用請業主開門）')))
    import siteinfo                              # 現場須知（接單後才給）
    if siteinfo.info(con, o):
        rows.append(_row('須知', board._no_money(siteinfo.info(con, o))[:200]))
    import siteroutine                           # 客戶01：定期特殊服務（這一期還沒做）
    rtn = siteroutine.text(con, o)
    if rtn:
        rows.append(_row('定期', '⚠️ 這次要做 ' + rtn[:240] + '\n做完按「定期項目做完了」'))
    import crewcheck                             # 工具雙向確認：現場＋自備還缺的，要確認會帶齊才算確認出勤
    miss = crewcheck.missing(con, o, worker_id) if worker_id else []
    if miss:
        rows.append(_row('還缺工具', '、'.join(miss) + '（業主現場沒有、你的自備工具也沒勾）'))
    ack_btns = ([_btn(f"我會帶齊，確認出勤 ✅", data=f'a=wok&o={oid}&r=1&t=1'), _btn('工具不夠，請公司協助', data=f'a=tlack&o={oid}')] if miss
                else [_btn('確認出勤 👍', data=f'a=wok&o={oid}&r=1')])
    return flex(f'{kicker} {when(o)}', _bubble(_header(kicker, when(o), color), rows,
        [_btn('打卡・拍照（工作頁）', uri=_work_link(con, oid, crew, worker_id), primary=True), _btn('開導航', uri=nav)] + ack_btns +
        ([_btn('現場須知・照片', uri=siteinfo.url(con, oid, 'f'))] if siteinfo.has(con, o, 'f') else []) +
        ([_btn('✅ 定期項目做完了', data=f'a=rtdone&o={oid}')] if rtn else []) +
        [_btn('臨時請假', data=f'a=lv&o={oid}'), _btn('要買耗材', data=f'a=sup&o={oid}')],
        '到場先在工作頁打卡、拍服務前照片；做完拍完成照片再離場打卡，照片會自動傳給示範服務店和業主。'))

def _work_link(con, oid, crew, worker_id=None):
    import worker_portal
    a = next((x for x in crew if not worker_id or x['wid'] == worker_id), None)
    return worker_portal.work_url(con, a['id']) if a else (worker_portal.portal_url(con, worker_id) if worker_id else f'{BASE_URL}/#mine')

def card_change(con, oid, before, worker=False):
    o, s, cu, crew, area = order_ctx(con, oid)
    rows = [_row('原本', f"{md(before['date'])} {before['start']}–{before['end']}" if before.get('date') else '—'), _row('改為', when(o)),
            _row('地點', __import__('board').street_of(s) if worker else (s.get('name') or area))]
    if worker and before.get('date') == o['date'] == dt.datetime.now(c.TZ).date().isoformat() and before.get('start') == o['start'] and str(o['end']) > str(before.get('end') or ''):
        # 服務當天現場延長：夥伴多半已在現場 → 只通知，不放「新時間不行」（按了會變成請假、整場被拿掉）
        return flex(f"今天延長到 {o['end']}", _bubble(_header('今天的工作延長了', when(o), '#C47F00'), rows, [_btn('收到 ✅', data=f'a=wok&o={oid}', primary=True)],
            '做完一樣拍完成照片、打離場卡。沒辦法延長，請按「現場回報」或直接在這裡說，管理層會處理。'))
    if worker:      # 夥伴版：可以回「這時間不行」，示範服務店會另外找人或再協調
        return flex(f'工作時間異動：{when(o)}', _bubble(_header('工作時間已更新', when(o), '#C47F00'), rows,
            [_btn('收到，可以 ✅', data=f'a=wok&o={oid}', primary=True), _btn('新時間不行', data=f'a=alt&o={oid}')], '新時間不方便請按「新時間不行」，示範服務店會再安排。'))
    return flex(f'時間異動：{when(o)}', _bubble(_header('預約時間已更新', when(o), '#C47F00', brand=brand_of(cu)), rows,
        [_btn('收到 ✅', data=f'a=cok&o={oid}&t={_tsig(o)}', primary=True)], '若新的時間不方便，請直接在這裡告訴我們。'))

def card_cancel(con, oid):
    o, s, cu, crew, area = order_ctx(con, oid)
    return flex(f'已取消：{when(o)}', _bubble(_header('預約已取消', when(o), '#C62828', brand=brand_of(cu)), [_row('地區', c.place(area) if area else '—')], (),
                                         '如果這不是您的意思，或想重新預約，請直接在這裡告訴我們。'))

def card_booking(con, q, brand='demoshop'):
    det = json.loads(q['detail'] or '{}')
    rows = [_row('服務', q['service_type']), _row('地區', q['area']), _row('希望', q['preferred'] or '—')]
    if det.get('規模'):
        rows.append(_row('規模', det['規模']))
    if det.get('報價'):
        rows.append(_row('報價', det['報價']))
    if q.get('kind') in ('quote', 'site_visit'):
        tip = ('需要現場估價的案件，專人會在這裡跟您約時間。' if q.get('kind') == 'site_visit' else
               '報價會先交給主管確認，確認後正式報價單會傳到這裡；照片還沒傳的話，也可以直接在這裡傳（每個要服務的區域都要拍）。')
        return flex('已收到您的估價需求', _bubble(_header('已收到您的估價需求 🙏', q['name'], brand=brand), rows, (), tip))
    return flex('已收到您的預約', _bubble(_header('已收到您的預約 🙏', q['name'], brand=brand), rows, (),
                                    '安排好服務人員後，會傳「承接卡」通知您由哪位專業服務人員服務；前一天也會再提醒。'))

def card_quote(con, q):
    import booking
    f = json.loads(q['form']); total = booking._totals(booking._items(q), q['taxed'])[2]
    return flex(f"您的報價單已確認：NT$ {total:,}", _bubble(_header('報價單已確認 🧾', f"NT$ {total:,}" + ('（含稅）' if q['taxed'] else '（未稅）'), brand=q['account']),
        [_row('報價單', q['id']), _row('服務', f.get('service_type') or '服務')]
        + ([_row('期望時間', booking.expect_text(q, f) + '（以夥伴確認為準）')] if booking.expect_text(q, f) else []),          # 加回客人填的期望日期／時段（簽約後照這個時間排）
        [_btn('查看並簽收報價單', uri=f"{BASE_URL}/q/{q['token']}", primary=True)],
        (f"報價有效至 {md(q['valid_until'])}。" if q['valid_until'] else f'報價有效 {booking.VALID_DAYS} 天。') + '簽收後我們會開始安排服務人員。'))

def approve_finish(con, oid, who):
    """完成照片審核通過 → 傳給業主。回傳 (傳到了沒, 說明)；業主沒綁 LINE → (False, '')（記下已審核，對上 LINE 後自動補傳）。"""
    import board
    if con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='finish_c' AND status NOT LIKE 'skipped%'", (oid,)).fetchone():
        return False, '這筆的完成照片已經傳給業主了。'
    order = c.get(con, 'orders', oid)
    import board as _bd; nophoto = _bd.no_photo(con, order)
    if not _finish_urls(con, oid) and not nophoto:
        return False, '還沒有完成照片。'
    board.log(con, oid, 'photo_approved', who)
    cacc, cuid = customer_target(con, order['customer_id'])
    if cuid:
        if nophoto and not _finish_urls(con, oid):   # 場內不能拍照 → 沒有照片的完工卡
            o2, s2, cu2, _, area2 = order_ctx(con, oid)
            m = flex('服務完成了，請幫我們確認 🙏', _bubble(_header('服務完成 ✨', f"{md(o2['date'])} {s2.get('name') or area2 or ''}".strip(), brand=brand_of(cu2)),
                     [_t('依您的規定，場內沒有拍照。沒問題請按「確認完工」；有需要加強的地方也請告訴我們 🙏', wrap=True)],
                     [_btn('確認完工 ✅', data=f"a=acc_ok&o={oid}", primary=True), _btn('有地方需要加強', data=f"a=acc_fix&o={oid}")]))
        else:
            m = card_finish_customer(con, oid)
        queue(con, cacc, cuid, 'finish_c', m[0]['altText'], m, oid)
        return True, f'✅ 已傳給業主（{who} 審核）'
    return False, ''

def after_customer_linked(con, cid):
    """業主剛對上 LINE（店長配對／綁定）：等他確認時間的單 → 馬上傳確認卡（完成照片由 deliver_pending_finish 補傳）。"""
    today = dt.datetime.now(c.TZ).date().isoformat()
    acc, uid = customer_target(con, cid)
    if not uid:
        return 0
    n = 0
    for o in c.rows(con, "SELECT * FROM orders WHERE customer_id=? AND status IN ('review','draft') AND COALESCE(customer_confirmed,0)=0 AND date>=? AND COALESCE(crew_ok,'')!=''", (cid, today)):
        crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        if crew >= (o['required_workers'] or 1):
            m = card_assigned(con, o['id']); queue(con, acc, uid, 'assigned', m[0]['altText'], m, o['id']); n += 1
    import payments                     # 稽核 A1：成立時還沒綁 LINE（例：Lin 合併前）→ 待付款通知補發一次
    for q in c.rows(con, "SELECT id,token,signed_at,account FROM quotes WHERE customer_id=? AND status='signed' AND account=?", (cid, acc)):
        rows = [r for r in payments.rows_for(con, quote_id=q['id']) if r['status'] == 'pending']
        if rows and not con.execute("SELECT 1 FROM line_outbox WHERE kind='payment' AND to_id=? AND created_at>=?", (uid, q['signed_at'] or '')).fetchone():
            on_payment_notice(con, acc, uid, cid, q['token'], rows, '付款通知 🧾', '您的預約已成立，請依期限付款'); n += 1
    return n

def card_text(title, text, color=BROWN, brand='demoshop'):
    return flex(title, _bubble(_header('', title, color, brand=brand or 'demoshop'), [_t(text)]))

STAFF_WAITS = ('pr_wait', 'entedit_wait', 'prep_wait', 'ask_wait', 'qa_wait', 'rv_wait', 'guide_wait', 'pcf_wait', 'scope_wait', 'rs_wait')

def _staff_wait(con, uid, key, val):
    """管理層「等打字」：開始等新的一題，就把其他等待中的清掉（稽核：以前會錯接，例如搜尋字被當成給夥伴的提醒傳出去）。val 空白＝只清這一個。"""
    if val:
        for k in STAFF_WAITS:
            if k != key and ix.setting(con, f'{k}:{uid}'):
                ix.set_setting(con, f'{k}:{uid}', '')
    ix.set_setting(con, f'{key}:{uid}', val)

SREP_WORDS = {'回報': '', '現場回報': '', '加時': 'extra', '要加時': 'extra', '需要加時': 'extra', '掃不完': 'extra', '清不完': 'extra', '做不完': 'extra',
              '趕不上': 'late', '下一場會晚到': 'late', '會晚到': 'late', '晚到': 'late', '來不及': 'late'}

USER_WAITS = {'lv_wait': 30, 'sup_wait': 30, 'supamt_wait': 30, 'ent_wait': 30, 'srep_wait': 30, 'iv_wait': 180, 'tm_wait': 180,      # 分鐘
              'qa_wait': 60, 'prep_wait': 60, 'entedit_wait': 60, 'pr_wait': 60, 'ask_wait': 60, 'guide_wait': 60, 'rv_wait': 60, 'pcf_wait': 60, 'scope_wait': 60, 'rs_wait': 60}     # 每日檢查：管理層的也要過期（以前 3 天後打「查 王小姐」把密碼蓋掉）

def _expire_waits(con, uid):
    """夥伴／客戶「等他打字」的狀態過期就作廢（沒有時間記錄的舊狀態也作廢）。管理層的等待不動。"""
    import time
    for k, mins in USER_WAITS.items():
        key = f'{k}:{uid}'
        if ix.setting(con, key):
            at = ix.setting(con, key + '@')
            if not at or time.time() - float(at) > mins * 60:
                ix.set_setting(con, key, '')

def _tap_worker(con, lu, gid, uid, data, oid):
    """夥伴卡片上的按鈕是誰按的：本人；或管理層在派單管理群代按（夥伴沒綁 LINE，打電話問完幫他按）。
    這場已取消、他已不在這場 → None（舊卡片不能再通知業主）。"""
    wid = (lu or {}).get('worker_id') or (_group_worker(con, gid) if gid else None)
    if (not wid or (data.get('w') and wid != data.get('w'))) and uid in staff_users(con):          # 代按：只有管理層（負責人 ）
        wid = data.get('w') or (con.execute("SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted' ORDER BY id LIMIT 1", (oid,)).fetchone() or [None])[0]
        if wid:
            c.CTX.actor = ((lu or {}).get('display_name') or '管理層') + '（代夥伴按）'
    elif data.get('w') and wid != data.get('w'):
        return None
    import board
    if not wid or not con.execute(f"SELECT 1 FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.order_id=? AND a.worker_id=? AND a.status='accepted' AND {board.GO_SQL}", (oid, wid)).fetchone():
        return None
    return wid

def _tap_error(con, act, e):
    """LINE 按鈕處理失敗：已回復（SAVEPOINT），預期中的錯誤講原因；其他錯誤自動記到功能回報。"""
    if isinstance(e, (ValueError, KeyError)):
        return f'沒辦法處理：{e}'
    import sysreport, traceback
    sysreport.auto(con, f'LINE 按鈕 {act}', e, traceback.format_exc())
    return '系統出了點問題，已自動回報，請先到中控台處理 🙏'

def card_admin(title, rows, url='', color=BROWN, uri=''):
    btns = ([_btn('打開營運中樞', uri=BASE_URL + url, primary=True)] if url else []) + ([_btn('看打卡位置', uri=uri)] if uri else [])
    return flex(title, _bubble(_header('營運通知', title, color), [_row(k, v) for k, v in rows], btns))

def card_photos(alt, kicker, title, urls, rows=(), buttons=(), color=BROWN, brand='demoshop', labels=None):
    """照片卡：第一張是說明（可帶按鈕），後面每張一頁照片，左右滑。一則推播最多 10 頁。labels：每張照片下面的小字（服務前／完工）。"""
    head = _bubble(_header(kicker, title, color, brand=brand), list(rows), list(buttons))
    pics = [{'type': 'bubble', 'hero': {'type': 'image', 'url': u, 'size': 'full', 'aspectRatio': '1:1', 'aspectMode': 'cover',
                                        'action': {'type': 'uri', 'uri': u}},
             **({'body': {'type': 'box', 'layout': 'vertical', 'paddingAll': '10px', 'contents': [_t(labels[i], weight='bold', size='sm',
                         color='#8E8E93' if labels[i] == '服務前' else '#2E7D32', align='center')]}} if labels and i < len(labels) and labels[i] else {})}
            for i, u in enumerate(urls[:9])]
    return [{'type': 'flex', 'altText': alt[:390], 'contents': {'type': 'carousel', 'contents': [head] + pics}}]

def card_bind_broadcast(brand='demoshop'):
    name = brands.name(brand)
    return flex(f'{name}：我們的新系統上線了！請花 10 秒綁定 🙏', _bubble(_header(f'您好，我是{name} 😊', '我們的新系統上線了 🎉', brand=brand),
        [_t('謝謝您一直以來的支持！之後的預約、服務人員安排、服務前一天提醒、完成照片，都會直接在這個 LINE 通知您，更方便、不用再來回確認。', wrap=True),
         {'type': 'separator', 'margin': 'lg'},
         _t('我們也整合了許多長期配合的專業廠商，各式各樣的工程都能一起安排：', wrap=True, margin='lg', weight='bold'),
         _t('・窗簾拆洗、送洗\n・高空清洗（外牆、高處玻璃）\n・一般到府服務、年度保養、進階專案、搬遷服務', wrap=True, color=SUB, margin='sm'),
         _t('請花 10 秒按下面按鈕綁定；最近有什麼想整理、想處理的，也歡迎直接留言告訴我們 🙌', wrap=True, margin='lg')],
        [_btn('綁定我的資料（10 秒）', data='a=bind', primary=True), _btn('我有需求想詢問', uri=f'{BASE_URL}/book?b={brand}')],
        '原則上以照片或現場確認後的報價為準。'))

BIND_REMINDER = '之後的預約、服務前一天提醒、服務人員到場通知、完成照片，都會直接在這個 LINE 通知您，不用再來回確認。請花 10 秒按下面按鈕綁定 🙏'

def card_bind_reminder(brand='demoshop'):
    """群發：請還沒綁定的好友綁定（負責人 沒互動過的好友，系統拿不到 userId，只能用群發請他們綁定）。已綁的人按了會說「已經綁定好了」。"""
    name = brands.name(brand)
    btns = [_btn('綁定我的資料（10 秒）', data='a=bind', primary=True), _btn('我有需求想詢問', uri=f'{BASE_URL}/book?b={brand}')]   # 稽核：群發不放夥伴報到（客戶點了會看到夥伴名單）
    return flex(f'{name}：請花 10 秒綁定您的資料 🙏', _bubble(_header(f'您好，我是{name} 😊', '請花 10 秒綁定 🙏', brand=brand),
        [_t(BIND_REMINDER, wrap=True), _t('已經綁定過的朋友請忽略這則，謝謝您！', wrap=True, color=SUB, size='sm', margin='lg')], btns,
        '綁定只用來通知您自己的預約與服務，不會另外打擾。'))

# 加入好友的歡迎訊息（每個品牌各自一份；取代官方帳號後台的歡迎訊息）
WELCOME = {
    'demoshop': ('真正輕鬆的生活，不只是把家整理乾淨，而是有人一起分擔家裡大大小小的事。',
              [('🏠 一般到府服務', '定期整理・年度保養・進階專案・搬遷前後服務'), ('👧 陪玩陪讀', '2–12 歲，給孩子有品質的陪伴'), ('🔑 居家私人管家', '協助打理家庭生活大小事'),
               ('🧺 配合廠商', '窗簾拆洗送洗・高空清洗・各式工程，一起幫您安排')]),
    'bizline': ('臺北店家服務＆代管：退房到入住前的房務服務、床品更換、服務前後拍照回報，讓您安心接待每一組客人。',
              [('🛏️ 定期駐點服務', '退房→入住前服務、床品更換、備品補充'), ('🧹 年度保養', '淡季深度服務，旺季前準備好'), ('📷 拍照回報', '服務前後照片，人不在現場也放心')]),
    'homeline': ('像家人一樣細心的一般到府服務，把家交給我們，您多一點時間陪家人。',
             [('🏠 一般到府服務', '定期整理、一次性服務都可以'), ('🧹 年度保養', '年節、換季、搬家前後')]),
}

def card_welcome(brand='demoshop', con=None):
    name = brands.name(brand)
    intro, items = WELCOME.get(brand) or (f'歡迎加入{name}！', [])
    b = brands.get(con, brand) if con else None
    if b and b.get('intro'):
        intro = b['intro']
    rows = [_t(intro, wrap=True)] + sum([[_t(t, weight='bold', margin='md'), _t(d, color=SUB, wrap=True, size='xs')] for t, d in items], [])
    rows.append(_t('點一下，專人才能更快幫您：', wrap=True, margin='lg', size='sm'))
    return flex(f'歡迎加入{name}！', _bubble(_header(f'歡迎加入 {name} 👋', '很高興認識您', brand=brand), rows,
        [_btn('我要詢問服務', data='a=role&r=c', primary=True), _btn('線上估價・預約', uri=f'{BASE_URL}/book?b={brand}')] + ([_btn('我要應徵夥伴', data='a=role&r=w')] if brand in APPLY_ACCOUNTS else [])
        + ([_btn('🧴 示範商城（耗材）', data='a=shop')] if brand == 'demoshop' else []),
        '有任何問題直接在這裡留言，我們會盡快回覆您 🙏'))

def card_join():
    return flex('示範服務店夥伴報到', _bubble(_header('示範服務店夥伴報到', '綁定一次，之後派工直接傳給你'),
        [_t('按下面按鈕，選你的名字、確認手機號碼，就完成報到。之後業主指定你、新的工作、前一天提醒都會自動傳給你。')],
        [_btn('我是示範服務店夥伴，報到', data='a=join', primary=True)]))

# ───────────── 觸發點（只放佇列） ─────────────
def order_people(con, oid):
    """派單管理群的通知一律講清楚：哪位客戶（電話）、哪個地點、哪位夥伴。"""
    o, s, cu, crew, area = order_ctx(con, oid)
    cust = f"{cu.get('name') or '（未建客戶）'}" + (f"（{cu['phone']}）" if cu.get('phone') else '')
    return [('客戶', cust), ('品牌', brands.name(brand_of(cu)) if cu else '—'), ('地點', ' '.join(x for x in (s.get('name'), area) if x) or o['title']),
            ('夥伴', '、'.join(a['name'] for a in crew) or '尚未安排')]

def admin_notify(con, title, rows, url='', color=BROWN, uri='', oid=None, tag=''):
    g = admin_group(con)
    if g:
        if oid:          # 跟工單有關：補上客戶／地點／夥伴，按鈕直接打開這張工單
            try:
                ppl = order_people(con, oid); keys = {k for k, _ in ppl}
                rows = ppl + [r for r in rows if r[0] not in keys]
                url = f'/#o/{oid}'
            except Exception:
                pass
        m = card_admin(title, rows, url, color, uri)
        queue(con, WORK_ACCOUNT, g, 'admin', m[0]['altText'], ([ping(con, tag)] if tag else []) + m)      # tag＝要人處理：@標記管理層

def request_bind(con, acc, uid, kind, ref, name, phone, line_name=''):
    """電話對到既有客戶／夥伴：不直接綁（可能冒用）。出現在中控台首頁和「LINE 配對」頁（起不推到群組；pair_push=on 才推）。"""
    import notify
    con.execute("UPDATE bind_requests SET status='superseded' WHERE account=? AND user_id=? AND kind=? AND status='open'", (acc, uid, kind))
    rid = c.uid('bnd')
    con.execute('INSERT INTO bind_requests(id,created_at,account,user_id,kind,ref_id,name,phone,line_name) VALUES(?,?,?,?,?,?,?,?,?)',
                (rid, c.now(), acc, uid, kind, ref, name, phone, line_name or ''))
    old = c.get(con, 'customers' if kind == 'customer' else 'workers', ref)
    g = admin_group(con) if ix.setting(con, 'pair_push') == 'on' else None
    if g:
        t = '夥伴 LINE 綁定待確認' if kind == 'worker' else '客戶 LINE 綁定待確認'
        m = flex(t, _bubble(_header(t, name, '#C47F00'),
            [_row('品牌', BRAND.get(acc, acc)), _row('填的名字', name), _row('填的電話', phone), _row('對到' + ('夥伴' if kind == 'worker' else '客戶'), old['name']), _row('LINE 名稱', line_name or '—')],
            [_btn('確認是本人，綁定', data=f'a=br_ok&r={rid}', primary=True), _btn('不是本人', data=f'a=br_no&r={rid}')], '中控台首頁也看得到，任一邊按都可以。'))
        queue(con, WORK_ACCOUNT, g, 'admin', m[0]['altText'], m)
    notify.staff(con, 'LINE 綁定待確認', f"{name} {phone} → {old['name']}", '/#home')
    return rid

def decide_bind(con, rid, ok, by):
    """確認／拒絕綁定申請（中控台按鈕與派單管理群按鈕共用）。回傳給操作者看的一句話。"""
    r = con.execute('SELECT * FROM bind_requests WHERE id=?', (rid,)).fetchone()
    if not r:
        raise ValueError('找不到這筆綁定申請')
    if r['status'] != 'open':
        return '這筆已經處理過了（' + {'approved': '已綁定', 'rejected': '已標記不是本人', 'superseded': '對方又送了新的申請'}.get(r['status'], r['status']) + f"・{r['decided_by']}）"
    acc, tu, ref, kind = r['account'], r['user_id'], r['ref_id'], r['kind']
    col, tbl = ('customer_id', 'customers') if kind == 'customer' else ('worker_id', 'workers')
    name = c.get(con, tbl, ref)['name']
    if ok and con.execute(f'SELECT 1 FROM line_users WHERE {col}=? AND user_id=? AND account=?', (ref, tu, acc)).fetchone():      # 稽核 H2：已經綁好了 → 只結案，不重複推
        con.execute("UPDATE bind_requests SET status='approved',decided_by=?,decided_at=? WHERE id=?", (by, c.now(), rid))
        return f'✅ {name} 已經綁定好了'
    if ok and kind == 'customer' and (tu in staff_users(con) or con.execute('SELECT 1 FROM line_users WHERE account=? AND user_id=? AND is_staff=1', (acc, tu)).fetchone()):
        raise ValueError(f"這個 LINE（{r['line_name'] or '管理層'}）是管理層的，不能綁成客戶 {name}；請按「不是本人」結案，把綁定連結轉傳給 {name} 本人")      #
    if ok and kind == 'customer':                     # 稽核 L9：品牌要一致
        import board
        bk = board.brand_known(con, c.get(con, 'customers', ref))
        if bk and bk != acc:
            raise ValueError(f"{name} 是{brands.name(bk)}的客戶，不能綁到{brands.name(acc)}的官方 LINE")
    if ok:
        if con.execute(f'SELECT 1 FROM line_users WHERE {col}=? AND user_id!=? AND account=? AND is_friend=1', (ref, tu, acc)).fetchone():
            raise ValueError('這份資料已經綁定其他 LINE 了，請先確認。')
        con.execute(f'UPDATE line_users SET {col}=NULL WHERE {col}=? AND account=? AND is_friend=0', (ref, acc))      # 稽核：換手機／新 LINE：舊的已封鎖帳號不擋
        con.execute(f"UPDATE line_users SET {col}=?,suggest_cid='' WHERE account=? AND user_id=?", (ref, acc, tu))
        if kind == 'customer':
            con.execute("UPDATE customers SET line_account=? WHERE id=? AND (line_account IS NULL OR line_account='')", (acc, ref))
        c.audit(con, 'line_bind_approved', tbl, ref, after={'line_user': tu, 'by': by})
        if kind == 'customer':
            m = card_next_step(con, acc, tu, ref); queue(con, acc, tu, 'hello', m[0]['altText'], m)
            after_customer_linked(con, ref)          # 稽核 C2：中控台確認綁定 → 等確認的時間卡馬上補傳
        else:
            import worker_portal
            worker_portal.rescreen_after_bind(con, acc, tu, ref)      # 再應徵：綁定核准後才審資料、掛照片
            if c.get(con, 'workers', ref)['status'] == 'review':      # 還在待核對才算新應徵者（之後錄取會收到歡迎）
                con.execute("UPDATE line_users SET onboard='applicant' WHERE account=? AND user_id=?", (acc, tu))
            worker_menu_link(con, tu, acc)
            queue(con, acc, tu, 'hello', '已確認夥伴身分', _txt(f'✅ {name}，已確認是你！下方已換成夥伴選單（今天的工作、班表、可排時間）。'))
        msg = f'✅ 已綁定：{name}'
    else:
        con.execute("UPDATE line_users SET suggest_cid='' WHERE account=? AND user_id=?", (acc, tu))
        c.audit(con, 'line_bind_rejected', tbl, ref, after={'line_user': tu, 'by': by})
        queue(con, acc, tu, 'hello', '資料確認中', _txt('謝謝您留資料 🙏 我們需要再跟您確認一下，專人會在這個聊天室跟您聯繫。'))
        msg = f"已標記不是本人（{r['name']} {r['phone']}），請在官方 LINE 聊天室跟對方確認。"
    con.execute('UPDATE bind_requests SET status=?,decided_by=?,decided_at=? WHERE id=?', ('approved' if ok else 'rejected', by, c.now(), rid))
    return msg

def allowed(con, uid, acc=None):
    """自動回覆客戶前先看模式：off 不回、test 只回測試名單、on 全部。"""
    m = mode(con, acc)
    return m == 'on' or (m == 'test' and uid in test_users(con))

def on_photos(con, o, who, label, urls):
    """夥伴傳完一批照片 → 派單管理群一張照片卡。"""
    g = admin_group(con)
    if not g or not urls:
        return
    name = {'before': '服務前', 'after': '完工', 'incident': '異常／損壞', 'signin': '簽到表'}.get(label, '現場')      # 簽到表傳完以前會在這裡出錯
    o2, s, cu, crew, area = order_ctx(con, o['id'])
    m = card_photos(f'📷 {who} 傳了{name}照片 {len(urls)} 張', f'📷 {name}照片', f"{who}・{len(urls)} 張", urls,
                    [_row('工作', f"{md(o2['date'])} {o2['start']}–{o2['end']}"), _row('客戶', order_people(con, o['id'])[0][1]), _row('地點', s.get('name') or area), _row('類型', s.get('service_type') or o2['title'])],
                    (), '#C62828' if label == 'incident' else BROWN)
    queue(con, WORK_ACCOUNT, g, 'photos', m[0]['altText'], m, o['id'])

def card_finish_customer(con, oid, urls=None, labels=None):
    o, s, cu, crew, area = order_ctx(con, oid)
    if urls is None:
        urls, labels = _ba(con, oid)
    import worker_portal
    total = con.execute("SELECT COUNT(*) FROM attachments WHERE order_id=? AND label IN ('before','after')", (oid,)).fetchone()[0]
    return card_photos('服務完成了，請幫我們確認 🙏', '服務完成 ✨', f"{md(o['date'])} {s.get('name') or area or ''}".strip(), urls,        # 多館業主要知道是哪一館
                       [_t('左右滑可以看「服務前」和「完工」照片' + (f'（這裡 {len(urls)} 張，全部 {total} 張請按「看全部照片」）' if total > len(urls) else '') + '。沒問題請按「確認完工」；有地方需要加強，按下面按鈕告訴我們。', color=SUB, wrap=True)],
                       [_btn('確認完工 ✅', data=f"a=acc_ok&o={oid}", primary=True), _btn('有地方需要加強', data=f"a=acc_fix&o={oid}"),
                        _btn(f'看全部照片（{total} 張）', uri=worker_portal.gallery_url(con, oid))], brand=brand_of(cu), labels=labels)

def _ba(con, oid):
    """服務前（最多 4 張）＋完工（最多 5 張）照片網址與標籤（BEFORE / AFTER 都要有）。"""
    import testimonials, worker_portal
    before, after = testimonials.photo_ids(con, oid)
    return [worker_portal.photo_url(con, i) for i in before + after], ['服務前'] * len(before) + ['完工'] * len(after)

def _finish_urls(con, oid):
    import worker_portal
    return [worker_portal.photo_url(con, r[0]) for r in con.execute("SELECT id FROM attachments WHERE order_id=? AND label='after' ORDER BY created_at DESC LIMIT 9", (oid,))][::-1]

def on_finished(con, o, who, urls, report=''):
    """離場打卡（完工）→ 完成照片自動傳給業主（決議：免審核）；派單管理群收一張知會卡（看全部照片／請夥伴補拍／主管代為驗收），不用按。
    已傳過又補拍 → 業主收「服務照片補充」（看全部照片）。業主沒綁 LINE → 知會卡 @管理層、附「給我業主綁定訊息」；綁好自動補傳（deliver_pending_finish）。"""
    import worker_portal, board
    oid = o['id']
    o2, s, cu, crew, area = order_ctx(con, oid)
    nall = {k: n for k, n in con.execute("SELECT label,COUNT(*) FROM attachments WHERE order_id=? AND label IN ('before','after') GROUP BY label", (oid,))}
    total, nb = sum(nall.values()), nall.get('before', 0)
    cacc, cuid = customer_target(con, o2['customer_id'])
    still = 0 if o2['status'] == 'completed' else con.execute("SELECT COUNT(*) FROM assignments a JOIN checkins ck ON ck.assignment_id=a.id WHERE a.order_id=? "
                        "AND a.status='accepted' AND ck.arrived_at!='' AND COALESCE(ck.left_at,'')=''", (oid,)).fetchone()[0]      # 已完工（例：有人忘了離場、系統完工）就不等
    first = not con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='finish_c' AND status NOT LIKE 'skipped%'", (oid,)).fetchone()
    if first and still:                      # 稽核 H2：還有夥伴在現場 → 先不告訴業主「服務完成」，最後一位離場再傳
        sent, state = False, f'還有 {still} 位夥伴在現場，最後一位離場後一起傳給業主'
    elif first:
        sent, msg = approve_finish(con, oid, '自動（免審核）')
        state = '已自動傳給業主（免審核）' if sent else ('還沒綁 LINE：綁好自動補傳' if not cuid else (msg or '—'))
        if sent:
            ix.set_setting(con, f'fin_n:{oid}', str(nall.get('after', 0)))
    else:                                     # 已傳過完工卡又補拍：補傳「看全部照片」
        board.log(con, oid, 'photo_approved', '自動（補拍）')                 # 夥伴工作頁的「請補拍」橫幅收掉
        recent = con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='finish_more' AND created_at>?",
                             (oid, (dt.datetime.now(c.TZ) - dt.timedelta(minutes=10)).isoformat(timespec='seconds'))).fetchone()
        grew = nall.get('after', 0) > int(ix.setting(con, f'fin_n:{oid}') or 0)      # 稽核 H2：完成照片沒變多就不用再傳業主
        sent = bool(cuid) and not recent and grew and not still
        if sent:
            ix.set_setting(con, f'fin_n:{oid}', str(nall.get('after', 0)))
            m = flex('服務照片補充', _bubble(_header('服務照片補充 📷', f"{md(o2['date'])} {s.get('name') or ''}", brand=brand_of(cu)),
                [_t(f"我們補上了這次的照片：現在服務前 {nb} 張、完工 {nall.get('after', 0)} 張。點下面可以放大、左右滑看。", wrap=True)],
                [_btn(f"看全部照片（{total} 張）", uri=worker_portal.gallery_url(con, oid), primary=True), _btn('有地方需要加強', data=f'a=acc_fix&o={oid}')],
                '有任何問題，直接在這裡留言給我們 🙏'))
            queue(con, cacc, cuid, 'finish_more', m[0]['altText'], m, oid)
        state = '已補傳給業主（看全部照片）' if sent else ('還沒綁 LINE' if not cuid else '照片沒有增加，不用再傳業主' if not grew else '還有夥伴在現場' if still else '10 分鐘內已補傳過')
    g = admin_group(con)
    if not g:
        return
    urls, labels = _ba(con, oid)
    import board as _bd2; nophoto = _bd2.no_photo(con, o2)          # 稽核 F2
    btns = ([] if nophoto else [_btn(f"看全部照片（{total} 張）", uri=worker_portal.gallery_url(con, oid), primary=True), _btn('請夥伴補拍', data=f"a=fin_redo&o={oid}")]) + \
           [_btn('主管代為驗收', data=f'a=acc_mgr&o={oid}', primary=nophoto)]          # 稽核 L8：場內不能拍照的不出現補拍
    if not cuid and o2['customer_id']:
        btns.append(_btn('給我業主綁定訊息', data=f"a=cbind&c={o2['customer_id']}"))
    m = card_photos(f"✅ {who} 完工了" + ('，照片已傳業主' if sent else ''), '✅ 完工・照片已傳業主' if sent else '✅ 完工', f"{md(o2['date'])} {s.get('name') or area}", urls,
                    [_row('客戶', order_people(con, oid)[0][1]), _row('夥伴', who), _row('回報', report or '—'),
                     _row('照片', f"服務前 {nb} 張・完工 {nall.get('after', 0)} 張" + ('（⚠️ 沒有服務前）' if not nb else '') + ('（卡片只放 9 張，請按「看全部照片」）' if total > len(urls) else '')),
                     _row('業主', state), _t('不用審核：完成照片自動傳給業主。覺得照片不夠再按「請夥伴補拍」，補好會自動補傳業主。', size='xs', color=SUB, wrap=True, margin='md')],
                    btns, '#2E7D32', labels=labels)
    pre = [ping(con, f"{who} 完工了（{md(o2['date'])} {s.get('name') or area}），業主還沒綁 LINE，照片傳不過去：請按「給我業主綁定訊息」轉傳業主")] if not cuid else []
    queue(con, WORK_ACCOUNT, g, 'photos', m[0]['altText'], pre + m, oid)

def bind_invite(con, cid, lead=''):
    """業主還沒綁 LINE：給派單管理群一則可以直接轉傳業主的綁定訊息（品牌分清楚：用業主自己品牌的官方帳號）。綁好後完成照片會自動補傳。"""
    import booking
    cu = con.execute('SELECT * FROM customers WHERE id=?', (cid or '',)).fetchone()
    if not cu:
        return _txt((lead + '\n' if lead else '') + '這筆沒有客戶資料，請先在中控台補客戶，再請業主綁定。')
    cu = dict(cu); a = brand_of(cu)
    if customer_target(con, cid)[1]:
        return _txt(f"{cu['name']} 已經綁定 LINE 了 👍")
    if not basic_id(con, a):
        return _txt((lead + '\n' if lead else '') + f"{brands.name(a)} 的官方 LINE 還沒接上系統，{cu['name']} 先沒辦法綁定（不會讓他綁到別的品牌）。")
    b = bind_code(con, 'customer', cid, a, days=14)
    head = (lead + '\n' if lead else '') + f"📌 請把下面這則訊息轉傳給業主 {cu['name']}" + (f"（{cu['phone']}）" if cu.get('phone') else '') + \
        f"【{brands.name(a)}的客戶，用{brands.name(a)}官方 LINE 綁定】：業主按連結、送出綁定碼就完成。綁好後，沒收到的完成照片系統會自動補傳給他。"
    return [{'type': 'text', 'text': head}, {'type': 'text', 'text': booking.bind_message(con, a, cu['name'], b['url'])}]

def _group_bind_request(con, acc, uid, wid, lu):
    """夥伴個人群組裡的人：不自動綁，送一次「綁定待確認」給派單管理群（同一個人不重複送）。"""
    if con.execute("SELECT 1 FROM bind_requests WHERE user_id=? AND kind='worker' AND status='open'", (uid,)).fetchone():
        return
    w = con.execute('SELECT name,phone FROM workers WHERE id=?', (wid,)).fetchone()
    if w:
        request_bind(con, acc, uid, 'worker', wid, w['name'], w['phone'] or '', (lu or {}).get('display_name') or '')

def wbind_sent(con, wid):
    """管理層按過「已經傳給他了」→ {'by','at'}；沒有回 None。"""
    v = ix.setting(con, f'wbind_sent:{wid}')
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None

def mark_wbind_sent(con, wid, by):
    w = con.execute('SELECT name FROM workers WHERE id=?', (wid or '',)).fetchone()
    if not w:
        raise ValueError('找不到這位夥伴')
    if worker_target(con, wid)[1]:
        return f'{w[0]} 已經綁定官方 LINE 了 👍'
    ix.set_setting(con, f'wbind_sent:{wid}', json.dumps({'by': by, 'at': c.now()}, ensure_ascii=False))
    c.audit(con, 'wbind_sent', 'workers', wid, after={'by': by})
    return f'✅ 已記下：綁定訊息已傳給 {w[0]}，之後每天不再列他（綁好自動消失；傳過 14 天還沒綁才會再提醒一次）'

def worker_bind_invite(con, wid, tpl=''):
    """夥伴還沒綁官方 LINE：給派單管理群一則可以轉傳給夥伴的綁定訊息（預設加示範服務店官方帳號；夥伴不分品牌，都是示範公司的夥伴，碼在生活線／企業線官方 LINE 傳也能綁）。
    tpl='play'：的陪玩招募文案（設定 wbind_tpl_play，店長審核通過才有）＋同樣的兩步驟綁定。"""
    w = con.execute('SELECT * FROM workers WHERE id=?', (wid or '',)).fetchone()
    if not w:
        return _txt('找不到這位夥伴')
    if worker_target(con, wid)[1]:
        return _txt(f"{w['name']} 已經綁定官方 LINE 了 👍")
    b = bind_code(con, 'worker', wid, WORK_ACCOUNT, days=14)
    if tpl == 'play':
        t_ = ix.setting(con, 'wbind_tpl_play')
        if not t_:
            raise ValueError('陪玩招募範本還在等店長審核，核准後就能用')
        msg = (t_.replace('{name}', w['name']).rstrip() + f"\n\n👉 兩步驟完成綁定（14 天內有效）：\n① 點連結加入好友：{b['add']}\n② 在聊天室傳送：綁定 {b['code']}\n\n有問題直接在 LINE 留言就好，謝謝你 🙏")
        return [{'type': 'text', 'text': f"📌 陪玩招募訊息（給 {w['name']}）"}, {'type': 'text', 'text': msg}]
    msg = (f"{w['name']} 你好 😊 我是示範服務店！\n之後的派工、班表、前一天提醒、打卡、請假，都會用示範服務店官方 LINE 一對一通知你，不用再看群組。\n"
           f"👉 兩步驟完成綁定（14 天內有效）：\n① 點連結加入好友：{b['add']}\n② 在聊天室傳送：綁定 {b['code']}\n\n有問題直接在 LINE 留言就好，謝謝你 🙏")
    return [{'type': 'text', 'text': f"📌 請把下面這則訊息轉傳給夥伴 {w['name']}" + (f"（{w['phone']}）" if w['phone'] else '') + '：他按連結、送出綁定碼就完成。'},
            {'type': 'text', 'text': msg}]

def _insp_site(con, order):
    for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (order['id'],)):
        to_worker(con, a['worker_id'], 'inspect_site', '業主會到現場驗收', _txt(f"🏠 業主說會到現場驗收（{when(dict(order))}）。\n做完還是照樣拍服務前後照片、傳上去；業主到了請陪他看一下 🙏"), order['id'])

def card_arrived(con, oid, who, door_btn=True):
    o, s, cu, crew, area = order_ctx(con, oid)
    pref = ix.setting(con, f"insp_pref:{cu.get('id')}") if cu.get('id') else ''
    if pref in ('site', 'online'):           # 驗收方式同一位業主只問一次 → 照上次選的
        if not ix.setting(con, f'insp:{oid}'):
            ix.set_setting(con, f'insp:{oid}', pref)
            if pref == 'site':
                _insp_site(con, o)
        how = '到現場驗收（已通知服務人員）' if pref == 'site' else '線上看服務前後照片'
        import door
        rows = [_t('服務人員已經到了，' + (f'會照您交代的方式進場（{door.mode_text(s, o)}）。' if not door_btn else '需要開門的話麻煩協助；不在家也沒關係，我們會照您交代的方式進場。'), wrap=True),
                _t(f'完工後照您之前選的：{how}。要改直接在這裡留言。', wrap=True, size='sm', color=SUB, margin='md')]
        return flex('服務人員已經到了 🙏', _bubble(_header('服務人員已抵達 🧹', f"{dt.datetime.now(c.TZ).strftime('%H:%M')} 到場", brand=brand_of(cu)), rows,
                    ([_btn('我來開門 👍', data=f'a=door&o={oid}', primary=True)] if door_btn else []) + [_btn('現場有狀況，緊急回報', data=f'a=urg&o={oid}&k=issue')], '進場方式有變動，直接在這裡留言給我們。'))
    if not door_btn:              # 按門鈴／密碼鎖／管理室 → 只告知到場＋驗收方式（不用請業主開門）
        import door
        return flex('服務人員已經到了 🙏', _bubble(_header('服務人員已抵達 🧹', f"{dt.datetime.now(c.TZ).strftime('%H:%M')} 到場", brand=brand_of(cu)),
            [_t(f'服務人員已經到了，會照您交代的方式進場（{door.mode_text(s, o)}）。', wrap=True),
             _t('完工後您想怎麼驗收？選「到現場驗收」我們會通知服務人員；選「線上看照片」完工後會傳服務前後照片給您。', wrap=True, size='sm', color=SUB, margin='md')],
            [_btn('我會到現場驗收', data=f'a=insp&o={oid}&m=site', primary=True), _btn('線上看照片驗收', data=f'a=insp&o={oid}&m=online'), _btn('現場有狀況，緊急回報', data=f'a=urg&o={oid}&k=issue')],
            '進場方式有變動，直接在這裡留言給我們。'))
    return flex('服務人員已經到了，需要開門請協助 🙏', _bubble(_header('服務人員已抵達 🚪', f"{dt.datetime.now(c.TZ).strftime('%H:%M')} 到場", brand=brand_of(cu)),
        [_t('服務人員已經到您家門口了。需要開門的話麻煩協助；不在家也沒關係，我們會照您交代的方式進場。', wrap=True),
         _t('完工後您想怎麼驗收？選「到現場驗收」我們會通知服務人員；選「線上看照片」完工後會傳服務前後照片給您。', wrap=True, size='sm', color=SUB, margin='md')],
        [_btn('我來開門 👍', data=f'a=door&o={oid}', primary=True), _btn('我會到現場驗收', data=f'a=insp&o={oid}&m=site'), _btn('線上看照片驗收', data=f'a=insp&o={oid}&m=online'),
         _btn('現場有狀況，緊急回報', data=f'a=urg&o={oid}&k=issue')],
        '有任何進場方式要補充，直接在這裡留言給我們。'))

def on_arrived(con, o, who, worker_id=None):
    """夥伴到場打卡 → 馬上通知業主（請開門或單純告知）。業主沒綁 LINE、而打卡的是測試者 → 傳預覽給他看。"""
    acc, uid = customer_target(con, o['customer_id'])
    import door
    o0, s = door.site_of(con, o['id'])
    em = door.mode_of(o0, s)
    nodoor = em in door.NO_DOOR
    if nodoor and not o0.get('door_open') and not (o0.get('door_req') or '').startswith(c.now()[:10]):     # 按門鈴／密碼鎖／管理室 → 打卡＝已進入；稽核 F1：打卡前已請業主開門的不自動記
        con.execute('UPDATE orders SET door_open=? WHERE id=?', (c.now(), o['id']))
        import board; board.log(con, o['id'], 'door_auto', f'{who} 到場打卡（{door.MODES[em]}：打卡＝已進入）')
    if em == 'home':          # 稽核：業主沒綁 LINE 也要走（request_open 會直接請管理群打電話）。開門流程：業主在家開門 → 到場打卡＝自動請業主開門（5 分鐘沒回應 → 管理群打電話）；方式不知道就只告知到場（稽核）
        return door.request_open(con, o['id'], worker_id, who, auto=True)
    m = card_arrived(con, o['id'], who, door_btn=not nodoor)
    if uid and con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='arrived_c' AND created_at LIKE ?", (o['id'], c.now()[:10] + '%')).fetchone():
        return None                       # 稽核：兩人同場，業主收一張就好
    if uid:
        return queue(con, acc, uid, 'arrived_c', m[0]['altText'], m, o['id'])
    if worker_id:
        u = con.execute('SELECT account,user_id FROM line_users WHERE worker_id=? ORDER BY last_seen DESC LIMIT 1', (worker_id,)).fetchone()
        if u and u[1] in test_users(con):
            queue(con, u[0], u[1], 'arrived_preview', '【預覽】業主會收到：' + m[0]['altText'],
                  [{'type': 'text', 'text': '【預覽】業主沒有綁 LINE，這是業主會收到的到場通知 👇'}] + m, o['id'])

def on_hello_done(con, acc, uid, name):
    cid = (con.execute('SELECT customer_id FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone() or [None])[0]
    if cid:
        m = card_next_step(con, acc, uid, cid, '謝謝您 🙏 資料收到了'); return queue(con, acc, uid, 'hello', m[0]['altText'], m)
    m = flex('謝謝您，資料收到了', _bubble(_header('謝謝您 🙏', name, brand=acc),
             [_t('資料收到了！可以直接在這裡留言，或用下面按鈕線上估價（拍幾張照片就好）。', wrap=True)],
             [_btn('線上估價・預約服務', uri=f'{BASE_URL}/book?b={acc}', primary=True)]))
    queue(con, acc, uid, 'hello', m[0]['altText'], m)

def worker_menu_link(con, uid, acc=WORK_ACCOUNT):
    """把夥伴專用圖文選單掛到這個 LINE（只在允許的人身上；客戶看到的選單不變）。"""
    rid = ix.setting(con, 'worker_richmenu_id' if acc == WORK_ACCOUNT else f'worker_richmenu_id_{acc}')      # 生活線帳號的夥伴有自己那份
    if rid and uid and acc in APPLY_ACCOUNTS and uid not in staff_users(con):        # 換選單不是發訊息：測試模式也換；管理層保留管理選單
        try:
            api(con, f'/v2/bot/user/{uid}/richmenu/{rid}', {}, acc=acc); return True
        except ValueError:
            return False
    return False

def on_worker_approved(con):
    """店長把應徵者改成「可派工」→ 傳錄取通知、掛上夥伴選單。背景每分鐘檢查。"""
    import worker_portal
    for u in c.rows(con, "SELECT l.account,l.user_id,w.id wid,w.name FROM line_users l JOIN workers w ON w.id=l.worker_id WHERE l.onboard='applicant' AND w.status='active' AND l.is_friend=1"):      # 每日檢查：封鎖了先不發，重新加好友再發
        if not allowed(con, u['user_id'], u['account']):
            continue                      # 測試模式先不發，等正式模式再發（不要標成已通知）
        bn = brands.name(u['account'])
        m = flex(f'歡迎加入{bn}，{u["name"]}！', _bubble(_header(f'🎉 歡迎加入{bn}', u['name'], '#2E7D32', brand=u['account']),
                 [_t(f'你已經是{bn}的夥伴了！下面的選單可以看今天的工作、班表、回報可排時間。有指定你的工作會直接傳到這裡。', wrap=True)],
                 [_btn('打開我的主控板', uri=worker_portal.portal_url(con, u['wid']), primary=True)]))
        queue(con, u['account'], u['user_id'], 'welcome_w', m[0]['altText'], m)
        worker_menu_link(con, u['user_id'], u['account'])
        if ix.setting(con, 'guide_sent_at'):          # 群發過系統介紹 → 之後新加入的夥伴自動補發
            g = card_worker_guide(con, u['wid']); queue(con, u['account'], u['user_id'], 'guide', g[0]['altText'], g)
        con.execute("UPDATE line_users SET onboard='worker' WHERE account=? AND user_id=?", (u['account'], u['user_id']))

def on_quote_sent(con, q):
    """店長確認報價 → 客戶有綁 LINE 就傳報價卡（回傳 True），否則 False（後台給連結自己傳）。"""
    r = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone()
    to = r[0] if r and r[0] else None
    if not to and q['customer_id']:                      # 同事開的報價：用客戶綁的 LINE（同品牌）
        acc, to = customer_target(con, q['customer_id'])
        if acc != (q['account'] or 'demoshop'):
            to = None
    if not to:
        return False
    m = card_quote(con, q)
    return queue(con, q['account'] or 'demoshop', to, 'quote', m[0]['altText'], m) == 'queued'      # 盤點：測試模式沒真的傳，不能回報「已傳給客戶」

def preview_copy(msgs):
    """群發送審用：同一份卡片，按鈕改成預覽（postback 加 pv=1、本系統網址加 preview=1），管理層可以點點看、不會真的觸發。"""
    m = json.loads(json.dumps(msgs, ensure_ascii=False))
    def walk(x):
        if isinstance(x, dict):
            a = x.get('action') if isinstance(x.get('action'), dict) else None
            if a and a.get('type') == 'postback':
                a['data'] = (a.get('data') or '') + '&pv=1'
                if a.get('displayText'):
                    a['displayText'] = '（預覽）' + a['displayText']
            if a and a.get('type') == 'uri' and str(a.get('uri', '')).startswith(BASE_URL):
                a['uri'] += ('&' if '?' in a['uri'] else '?') + 'preview=1'
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(m)
    for x in m:
        if x.get('altText'):
            x['altText'] = '【預覽】' + x['altText']
    return m

PREVIEW_TEXT = {      # 預覽按鈕：客戶／夥伴按了「會」怎樣（不會真的做）
    'camp:call': '客戶會收到：「好的 👍 專人會盡快跟您聯絡，也可以直接在這裡留下方便聯絡的時間和電話 🙏」\n派單管理群會收到：「有人想安排年度保養，請聯絡」（附名字、電話）。',
    'camp:no': '客戶會收到：「好的，謝謝您告訴我們 😊 有需要隨時在這裡找我們。」之後不會再提醒他。',
    'bind': '會打開綁定流程：客戶留稱呼、電話，系統對到他的資料後就綁定，之後提醒、完成照片都會直接傳給他。',
}

def preview_reply(data):
    k = data.get('a', '') + (f":{data['v']}" if data.get('v') else '')
    return _txt('👀 （預覽，沒有真的執行）\n' + PREVIEW_TEXT.get(k, PREVIEW_TEXT.get(data.get('a', ''), '這顆按鈕客戶按了才會真的執行；預覽不會排班、不會建單、不會通知任何人。')))

def card_quote_cancelled(con, q):
    """報價已取消（給客戶，用報價那個品牌的官方 LINE）。"""
    f = json.loads(q['form'] or '{}')
    svc = f.get('service_type') or '服務'
    w = ''          # 報價不寫服務日期時間（稽核 F4）
    return flex(f"您的報價已經幫您取消了（{q['id']}）", _bubble(_header('報價已取消', f"報價單 {q['id']}", brand=q['account'] or 'demoshop'),
        [_t(f"您好，您申請的{svc}{w}報價，已經幫您取消了 🙏", wrap=True),
         _t('之後有需要，隨時在這裡跟我們說，或重新填一份估價單；有任何問題也可以直接留言。', size='sm', color=SUB, wrap=True, margin='md')],
        [_btn('重新估價', uri=f"{BASE_URL}/book?b={q['account'] or 'demoshop'}", primary=True)]))

def on_quote_cancelled(con, q):
    """報價取消 → 自動通知客戶（找人的方式跟傳報價卡一樣）。回傳有沒有排進發送。"""
    r = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone()
    to = r[0] if r and r[0] else None
    if not to and q['customer_id']:
        acc, to = customer_target(con, q['customer_id'])
        if acc != (q['account'] or 'demoshop'):
            to = None
    if not to:
        return False
    m = card_quote_cancelled(con, q)
    return queue(con, q['account'] or 'demoshop', to, 'quote_cancel', m[0]['altText'], m) == 'queued'

def on_quote_more(con, q, why):
    r = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone()
    if not r or not r[0]:
        return False
    m = flex('請幫我們補一下照片／資料', _bubble(_header('請補一下資料 🙏', f"報價單 {q['id']}", '#C47F00', brand=q['account']),
        [_t(why, wrap=True), _t('可以直接在這裡傳照片，或按下面按鈕上傳。補齊後主管會盡快確認報價。', color=SUB, wrap=True)],
        [_btn('上傳照片', uri=f"{BASE_URL}/q/{q['token']}", primary=True)]))
    queue(con, q['account'] or 'demoshop', r[0], 'quote_more', m[0]['altText'], m)
    return True

def on_dispatch(con, oid, worker_ids=(), preferred=False):
    sent = set()
    for wid in worker_ids:
        m = card_dispatch(con, oid, preferred, wid); sent.add(to_worker(con, wid, 'dispatch', m[0]['altText'], m, oid))
    return len(sent)

def on_filled(con, oid):
    o = c.get(con, 'orders', oid)
    acc, uid = customer_target(con, o['customer_id'])
    if uid:
        m = card_assigned(con, oid); queue(con, acc, uid, 'assigned', m[0]['altText'], m, oid)
    o2, s, cu, crew, area = order_ctx(con, oid)
    admin_notify(con, '✅ 已有夥伴接單', [('時間', when(o2))], '/#dispatch', '#2E7D32', oid=oid)

def on_change(con, before, after):
    oid = after['id']; cancel = after['status'] == 'cancelled'; sent = set()
    if cancel and before['status'] != 'cancelled':
        import decisions; decisions.cancel_subsidy(con, before, after)          # 店長定的取消車馬費規則
    moved = not cancel and any(before[k] != after[k] for k in ('date', 'start', 'end'))
    if moved and after['status'] in ('review', 'draft') and after['customer_confirmed']:
        # 盤點：簽收／確認過的單改了時間 → 要客戶重新確認新時間（改期卡上按「收到」＝確認），不能直接用舊的確認排定
        con.execute('UPDATE orders SET customer_confirmed=0,version=version+1 WHERE id=?', (oid,))
    acc, uid = customer_target(con, after['customer_id'])
    if uid:
        m = card_cancel(con, oid) if cancel else card_change(con, oid, before)
        queue(con, acc, uid, 'cancel' if cancel else 'change', m[0]['altText'], m, oid); sent.add(uid)
    elif after['customer_id'] and (cancel or moved):       # 客戶沒綁 LINE／封鎖：不能靜默，請管理群打電話
        admin_notify(con, '📞 客戶收不到 LINE，請打電話告知' + ('取消' if cancel else '改期'),
                     [('原本', f"{md(before['date'])} {before['start']}–{before['end']}" if before.get('date') else '—'), ('改為', '取消' if cancel else when(after)),
                      ('處理', '打電話告知客戶' + ('' if cancel else '，客戶同意新時間後在中控台按「客戶已確認」'))], '/#home', '#C47F00', oid=oid)
    who = "status='accepted'" if not cancel else "(status='accepted' OR notes LIKE '%（工單取消時已接）%')"
    for a in c.rows(con, f"SELECT worker_id FROM assignments WHERE order_id=? AND {who}", (oid,)):
        if worker_target(con, a['worker_id'])[1] in sent:
            continue
        m = card_text('工作取消', f"{when(after)} 這筆工作取消了，請不要前往。", '#C62828') if cancel else card_change(con, oid, before, worker=True)
        sent.add(to_worker(con, a['worker_id'], 'cancel' if cancel else 'change', m[0]['altText'], m, oid))
    if not cancel and any(before[k] != after[k] for k in ('date', 'start', 'end')):      # 邀請中的夥伴：手上的派工卡是舊時間 → 重發
        inv = [r[0] for r in con.execute("SELECT worker_id FROM assignments WHERE order_id=? AND status='invited'", (oid,))]
        if inv:
            on_dispatch(con, oid, inv)

def on_confirmed(con, oid):
    """排定（客戶確認＋人到齊）→ 地址、進場方式立刻傳給夥伴。"""
    try:
        con.execute("UPDATE orders SET go_partial='' WHERE id=?", (oid,))      # 補到人、重新排定 → 不再是補人中
    except Exception:
        pass
    for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)):
        m = card_worker_remind(con, oid, '✅ 客戶已確認・已排定', '#2E7D32', a['worker_id']); to_worker(con, a['worker_id'], 'confirmed_w', m[0]['altText'], m, oid)

def on_tomorrow(con, day, word='明天'):
    """每天 18:00 自動：明天每一場，客戶與夥伴各一張提醒卡；業主還沒確認的，提醒夥伴先別出發。"""
    late_ok = lambda o: word != '今天' or ((o['start'] or '') > c.now()[11:16] and not con.execute(
        "SELECT 1 FROM checkins k JOIN assignments a ON a.id=k.assignment_id WHERE a.order_id=? AND k.arrived_at!=''", (o['id'],)).fetchone())     # 停機補發「今天」：只補還沒開始的
    for o in c.rows(con, "SELECT * FROM orders WHERE date=? AND status IN ('review','draft')", (day,)):
        if not late_ok(o) or con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind IN ('remind_wait','relay_w:remind_wait','remind_w','relay_w:remind_w') AND created_at LIKE ?",
                                         (o['id'], c.now()[:10] + '%')).fetchone():
            continue                     # 稽核：補人中發的是 remind_w，以前沒算進來 → 18:15–21:30 每 5 分鐘重發
        crew_n = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
        if crew_n >= (o['required_workers'] or 1) and not o['customer_confirmed'] and o.get('crew_ok'):      # 等客戶確認：前一天自動再請客戶確認
            acc, uid = customer_target(con, o['customer_id'])
            if uid:
                m = card_assigned(con, o['id']); queue(con, acc, uid, 'confirm_ask', '明天的服務，請幫我們確認時間 🙏', m, o['id'])
        import payments
        why = ('業主還沒確認時間' if not o['customer_confirmed'] else '還在找其他夥伴' if crew_n < (o['required_workers'] or 1)
               else '管理層還在確認人員安排' if not o.get('crew_ok') else '還在等款項確認' if not payments.cleared_for_schedule(con, o['id']) else '還在等正式排定')
        for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)):
            import board
            if board.goes(o, a['worker_id']):      # 同組有人請假、補人中：照常名單上的人照常出發（新補進來、還沒審核的不給地址，稽核）
                m = card_worker_remind(con, o['id'], '明天的工作 🧹（同組補人中，你照常出發）', BROWN, a['worker_id']); to_worker(con, a['worker_id'], 'remind_w', m[0]['altText'], m, o['id'])
                continue
            m = card_text('明天的工作：還沒正式排定', f"{when(o)} 這筆{why}，先不要出發 🙏 排定後會馬上傳地址和進場方式給你；今晚沒收到的話，示範服務店會再通知你。", '#C47F00')
            to_worker(con, a['worker_id'], 'remind_wait', m[0]['altText'], m, o['id'])
    for o in c.rows(con, "SELECT * FROM orders WHERE date=? AND status='confirmed'", (day,)):
        acc, uid = customer_target(con, o['customer_id'])
        if not late_ok(o) or con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind IN (" + ("'remind_c'" if uid else "'remind_c','remind_w','relay_w:remind_w'") + ") AND created_at LIKE ?",
                                         (o['id'], c.now()[:10] + '%')).fetchone():
            continue                     # 稽核：補人中先發過「照常出發」、晚上補到人排定 → 業主照樣收「明天見」
        if uid:
            import door
            ask = door.need_ask(con, o['id'])      # 先沿用同業主其他地點的開門方式，再組提醒卡（稽核 A3）
            m = card_customer_remind(con, o['id'], word); queue(con, acc, uid, 'remind_c', m[0]['altText'], m, o['id'])
            con.execute("UPDATE orders SET reminded_at=? WHERE id=? AND COALESCE(reminded_at,'')=''", (c.now(), o['id']))     # 已自動提醒 → 中控台不用再叫人提醒
            if ask or not ix.setting(con, f"trash_ask:{o['site_id']}"):      # 還不知道的一起問（開門、垃圾、工具同一張）
                import prepq; prepq.send(con, o['id'], acc, uid, 'entry_ask')
        for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)):
            m = card_worker_remind(con, o['id'], f'{word}的工作 🧹', worker_id=a['worker_id']); to_worker(con, a['worker_id'], 'remind_w', m[0]['altText'], m, o['id'])

# ───────────── 群組角色：管理群／接單大群／夥伴個人群 ─────────────
PREFIX = re.compile(r'^(示範服務店|企業線|生活線)[^-－]*[-－]\s*')

def classify(con, gid, name, confirmed=True):
    """依群組名稱判斷用途。confirmed=False（官方帳號剛被拉進群）時，夥伴／管理／接單群只先標「待確認」，
    要在派單管理群按「確認」才生效——避免有人自己建「示範服務店-某夥伴」群組冒用夥伴身分。"""
    role, wid = 'job', None
    if '派單管理' in name:
        role = 'admin'
    elif '接單管理' in name:
        role = 'pool'
    elif PREFIX.match(name):
        role = 'worker'
        wname = PREFIX.sub('', name).strip()
        w = con.execute('SELECT id FROM workers WHERE name=?', (wname,)).fetchone() or \
            con.execute('SELECT id FROM workers WHERE notes LIKE ? ORDER BY status', ('%' + name + '%',)).fetchone() or \
            con.execute("SELECT id FROM workers WHERE ? LIKE '%'||name||'%' AND length(name)>=2 ORDER BY length(name) DESC", (wname,)).fetchone()
        if w:
            wid = w[0]
        else:       # 有個人群組卻不在夥伴名單：自動建立（待核對），交給店長確認
            wid = c.save(con, 'workers', {'name': wname, 'status': 'review', 'notes': f'由 LINE 群組「{name}」自動建立，請確認後設為可派工'})['id']
            try:
                import trash
                trash.flag(con, 'workers', wid, '4. 新的夥伴群組', f'官方帳號加入了「{name}」群組，系統自動建立夥伴「{wname}」。', '確認資料後「移到 › 可派工」')
            except Exception:
                pass
    if not confirmed and role in ('worker', 'admin', 'pool'):
        con.execute('UPDATE line_groups SET role=?,worker_id=? WHERE group_id=?', ('pending_' + role, wid, gid))
        g = admin_group(con)
        if g and g != gid:
            label = {'worker': f"夥伴個人群（{c.get(con, 'workers', wid)['name'] if wid else ''}）", 'admin': '派單管理群', 'pool': '接單大群'}[role]
            m = flex('新的 LINE 群組待確認', _bubble(_header('新的群組待確認', name or '（未命名）', '#C47F00'),
                     [_row('判斷為', label), _t('確認是你們自己的群組才按確認；不認識的群組請按「不是」。（群組要用自己的 LINE 搜尋群組名；官方帳號後台的聊天列表不會顯示群組（LINE 的限制））', color=SUB, wrap=True)],
                     [_btn('確認', data=f'a=grp_ok&g={gid}', primary=True), _btn('不是我們的群組', data=f'a=grp_no&g={gid}')]))
            queue(con, WORK_ACCOUNT, g, 'admin', m[0]['altText'], m)
        return 'pending_' + role, wid
    con.execute('UPDATE line_groups SET role=?,worker_id=? WHERE group_id=?', (role, wid, gid))
    if role == 'admin' and not ix.setting(con, 'line_admin_group'):
        ix.set_setting(con, 'line_admin_group', gid)
    return role, wid

def classify_all(con):
    for g in c.rows(con, "SELECT * FROM line_groups WHERE active=1 AND COALESCE(role,'')=''"):
        if not g['name']:
            try:
                g['name'] = api(con, f"/v2/bot/group/{g['group_id']}/summary", acc=g['account'] or WORK_ACCOUNT).get('groupName', '')
                con.execute('UPDATE line_groups SET name=? WHERE group_id=?', (g['name'], g['group_id']))
            except ValueError:
                continue
        classify(con, g['group_id'], g['name'])

# ───────────── 綁定碼、預約編號、報到 ─────────────
def add_url(con, acc):
    """加好友連結（預設用官方帳號 Basic ID 組 line.me 連結；settings line_add_url_<品牌> 可改成自己的短網址）。"""
    return ix.setting(con, f'line_add_url_{acc}') or f'https://line.me/R/ti/p/{basic_id(con, acc)}'

def bind_code(con, kind, ref_id, acc=WORK_ACCOUNT, days=1):
    old = con.execute('SELECT code FROM line_bind WHERE kind=? AND ref_id=? AND expires>?', (kind, ref_id, int(time.time()) + 86400)).fetchone() if kind == 'customer' else None
    if old:                       # 客戶綁定碼：還有效就沿用（同一位客戶重複複製訊息，碼不會變）
        code = old[0]
    else:
        con.execute('DELETE FROM line_bind WHERE expires<? OR (kind=? AND ref_id=?)', (int(time.time()), kind, ref_id))
        gen = lambda: ''.join(secrets.choice('ABCDEFGHJKMNPQRSTUVWXYZ23456789') for _ in range(8))      # 資安：6 位數字可以猜 → 8 碼英數
        code = gen()
        while con.execute('SELECT 1 FROM line_bind WHERE code=?', (code,)).fetchone():
            code = gen()
        con.execute('INSERT INTO line_bind(code,kind,ref_id,expires,account) VALUES(?,?,?,?,?)', (code, kind, ref_id, int(time.time()) + 86400 * days, acc))
    return {'code': code, 'url': f'https://line.me/R/oaMessage/{basic_id(con, acc)}/?' + urllib.request.quote(f'綁定 {code}'), 'add': add_url(con, acc)}

def inquiry_code(con, inquiry_id, acc='demoshop'):
    code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
    con.execute('UPDATE inquiries SET line_code=? WHERE id=?', (code, inquiry_id))
    return {'code': code, 'url': f'https://line.me/R/oaMessage/{basic_id(con, acc)}/?' + urllib.request.quote(f'預約編號 {code}')}

def join_token(con, uid, acc=WORK_ACCOUNT):
    t = secrets.token_urlsafe(16)
    con.execute('INSERT INTO line_bind(code,kind,ref_id,expires,account) VALUES(?,?,?,?,?)', ('J' + t, 'join', uid, int(time.time()) + 3 * 86400, acc))
    return f'{BASE_URL}/join?t={t}'

# ───────────── webhook ─────────────
def _upsert_user(con, acc, uid, gid=None):
    if not uid:
        return None
    row = con.execute('SELECT * FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone()
    if not row or not row['display_name']:
        try:
            p = api(con, f'/v2/bot/group/{gid}/member/{uid}' if gid else f'/v2/bot/profile/{uid}', acc=acc)
        except ValueError:
            p = {}
        con.execute("INSERT INTO line_users(account,user_id,display_name,picture_url,first_seen,last_seen) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(account,user_id) DO UPDATE SET display_name=COALESCE(NULLIF(excluded.display_name,''),display_name),"
                    "picture_url=COALESCE(NULLIF(excluded.picture_url,''),picture_url)",
                    (acc, uid, p.get('displayName', ''), p.get('pictureUrl', ''), c.now(), c.now()))
    con.execute('UPDATE line_users SET last_seen=? WHERE account=? AND user_id=?', (c.now(), acc, uid))
    if uid in staff_users(con):
        con.execute('UPDATE line_users SET is_staff=1 WHERE account=? AND user_id=?', (acc, uid))
    return dict(con.execute('SELECT * FROM line_users WHERE account=? AND user_id=?', (acc, uid)).fetchone())

def reply(con, acc, token_, messages, kind='reply', order_id=''):
    # 盤點：回覆也要看品牌模式。off＝完全不回；test＝只回測試名單、管理層（企業線／生活線測試期間不打擾真的客戶）
    m = mode(con, acc); who = getattr(c.CTX, 'line_uid', None)
    if who and _brand_worker(con, acc, who):
        messages = rebrand(messages, acc)
    if m == 'off' or (m == 'test' and who and who not in test_users(con) and who not in staff_users(con) and who != admin_group(con)):
        st, err = f'skipped:{m}', ''
    else:
        try:
            api(con, '/v2/bot/message/reply', {'replyToken': token_, 'messages': messages[:5]}, acc=acc); st, err = 'sent', ''
        except ValueError as e:
            st, err = 'failed', str(e)
    con.execute('INSERT INTO line_outbox(created_at,account,to_id,kind,order_id,alt,payload,status,sent_at,error) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (c.now(), acc, 'reply', kind, order_id, messages[0].get('altText', messages[0].get('text', ''))[:390], json.dumps(messages, ensure_ascii=False), st, c.now(), err))

def rating_ask(oid):
    """確認完工後：請客戶點 1–5 星。回報「客戶沒看到星星」：快速回覆在電腦版看不到、後面有訊息就消失 → 改圖卡按鈕（一直在），快速回覆也留著。"""
    return {'type': 'flex', 'altText': '可以幫這次的服務打個分數嗎？⭐',
            'contents': _bubble(_header('謝謝您的確認 🙏', '幫這次的服務打個分數'), [_t('點一下星星就好（5 顆最滿意）', size='sm', color=SUB, wrap=True)],
                                [_btn('⭐' * n, data=f'a=rate&o={oid}&s={n}', primary=n == 5) for n in (5, 4, 3, 2, 1)]),
            'quickReply': {'items': [{'type': 'action', 'action': {'type': 'postback', 'label': '⭐' * n, 'data': f'a=rate&o={oid}&s={n}', 'displayText': '⭐' * n}}
                                     for n in (5, 4, 3, 2, 1)]}}

def _txt(s):
    return [{'type': 'text', 'text': s}]

def _group_worker(con, gid):
    r = con.execute("SELECT worker_id FROM line_groups WHERE group_id=? AND role='worker'", (gid,)).fetchone() if gid else None
    return r[0] if r else None

def handle(con, acc, ev):
    import board, dispatch, notify
    src = ev.get('source') or {}
    uid, gid = src.get('userId'), src.get('groupId') or src.get('roomId')
    c.CTX.viewing = uid if (uid and not gid and uid in viewing(con)) else None      # 管理層的客戶／夥伴視角（只在一對一）
    c.CTX.line_uid = gid or uid          # reply() 依品牌模式判斷能不能回
    con.execute('INSERT INTO line_inbox(at,account,user_id,group_id,type,text,data) VALUES(?,?,?,?,?,?,?)',
                (c.now(), acc, uid, gid, ev.get('type'), ((ev.get('message') or {}).get('text') or '')[:500], ((ev.get('postback') or {}).get('data') or '')[:200]))
    if ev['type'] == 'join' and gid:
        con.execute('INSERT INTO line_groups(group_id,joined_at,account,active) VALUES(?,?,?,1) ON CONFLICT(group_id) DO UPDATE SET active=1,account=excluded.account', (gid, c.now(), acc))
        try:
            name = api(con, f'/v2/bot/group/{gid}/summary', acc=acc).get('groupName', '')
        except ValueError:
            name = ''
        con.execute('UPDATE line_groups SET name=? WHERE group_id=?', (name, gid))
        role, wid = classify(con, gid, name, confirmed=False)
        if role == 'pool':
            reply(con, acc, ev['replyToken'], card_join(), 'join')
        notify.staff(con, '官方 LINE 加入群組', f'{name}（{ {"admin": "管理群", "pool": "接單大群", "worker": "夥伴個人群", "job": "工作群"}.get(role.replace("pending_", ""), role)}・待確認）' if role.startswith('pending_') else name, '/#line')
        return
    if ev['type'] == 'leave' and gid:
        con.execute('UPDATE line_groups SET active=0 WHERE group_id=?', (gid,)); return
    if ev['type'] == 'unfollow' and uid:
        con.execute('UPDATE line_users SET is_friend=0 WHERE account=? AND user_id=?', (acc, uid))
        w_ = con.execute('SELECT worker_id FROM line_users WHERE account=? AND user_id=? AND worker_id IS NOT NULL', (acc, uid)).fetchone()
        if w_ and uid not in staff_users(con) and not con.execute('SELECT 1 FROM line_users WHERE worker_id=? AND is_friend=1', (w_[0],)).fetchone():      # 夥伴刪除／封鎖官方 LINE → 他之後還有工作就通知管理群（別的官方 LINE 還是好友就不算）
            import departure; departure.gone(con, w_[0], '刪除或封鎖了官方 LINE')
        return
    if ev['type'] == 'memberLeft' and gid:               # 夥伴退出自己的群組或工作群 → 同上
        import departure
        for mb in ((ev.get('left') or {}).get('members') or []):
            u_ = mb.get('userId')
            if not u_ or u_ in staff_users(con):
                continue
            w_ = con.execute('SELECT worker_id FROM line_users WHERE user_id=? AND worker_id IS NOT NULL LIMIT 1', (u_,)).fetchone()
            wid_ = (w_[0] if w_ else None) or _group_worker(con, gid)
            if wid_:
                departure.gone(con, wid_, '退出了 LINE 群組')
        return
    if ev['type'] == 'follow' and uid:
        lu = _upsert_user(con, acc, uid); con.execute('UPDATE line_users SET is_friend=1 WHERE account=? AND user_id=?', (acc, uid))
        if lu.get('customer_id') or lu.get('worker_id'):        # 老朋友封鎖後又加回來：打個招呼就好
            who = c.get(con, 'customers', lu['customer_id'])['name'] if lu.get('customer_id') else c.get(con, 'workers', lu['worker_id'])['name']
            reply(con, acc, ev['replyToken'], _txt(f'{who}，歡迎回來 😊 有需要隨時按下方選單或直接留言。'), 'welcome')
        elif not lu.get('is_staff'):
            con.execute("UPDATE line_users SET onboard='asked',nudged_at=? WHERE account=? AND user_id=?", (c.now(), acc, uid))
            reply(con, acc, ev['replyToken'], card_welcome(acc, con), 'welcome')
        return
    lu = _upsert_user(con, acc, uid, gid) if uid else None
    if ev['type'] == 'message' and gid:
        wid = _group_worker(con, gid)          # 夥伴在自己的群組說話 → 資安：不自動綁（群組裡可能有家人、其他人），請派單管理群確認
        if wid and lu and not lu.get('is_staff') and not lu.get('worker_id'):
            _group_bind_request(con, acc, uid, wid, lu)
        t0 = ((ev.get('message') or {}).get('text') or '').strip()
        mt0 = (ev.get('message') or {}).get('type')
        lg0 = gid == admin_group(con) and uid in staff_users(con) and (t0.startswith('記帳') or (mt0 in ('image', 'file') and ix.setting(con, f'lg_wait:{uid}')))      # 記帳：管理群裡也能拍收據
        lg0 = lg0 or (gid == admin_group(con) and t0.strip() in ('收款紀錄', '收款', '付款紀錄', '收款查詢') and uid in money_users(con))      # 打「收款紀錄」
        if not lg0 and not (t0 and gid == admin_group(con) and uid in staff_users(con) and
                (t0.startswith('【') or any(ix.setting(con, f'{k}:{uid}') for k in STAFF_WAITS))):
            return                              # 管理群裡管理層的回答照樣收（點選題、我打字回答、要修改）；其他群組對話不處理
    if ev['type'] == 'message' and (ev.get('message') or {}).get('type') == 'image' and uid and ix.setting(con, f'police_wait:{uid}'):
        import recruit                   # 夥伴傳良民證照片（招募第三關）
        try:
            msg = recruit.police_photo(con, uid, content(con, ev['message']['id'], acc))
        except Exception as e:
            msg = f'照片收不到（{str(e)[:40]}），請再傳一次 🙏'
        return reply(con, acc, ev['replyToken'], _txt(msg))
    if ev['type'] == 'message' and (ev.get('message') or {}).get('type') == 'image' and uid and ix.setting(con, f'supr_wait:{uid}'):
        import supplies                  # 夥伴買用品：經理同意後傳的發票照片
        try:
            msg = supplies.receipt_photo(con, uid, content(con, ev['message']['id'], acc))
        except Exception as e:
            msg = f'發票照片收不到（{str(e)[:40]}），請再傳一次 🙏'
        if msg:
            return reply(con, acc, ev['replyToken'], _txt(msg))
    if ev['type'] == 'message' and (ev.get('message') or {}).get('type') == 'image' and uid and ix.setting(con, f'mat_wait:{uid}'):
        import materials                 # 管理層按了「我要傳」素材
        try:
            msg = materials.photo(con, uid, content(con, ev['message']['id'], acc), (lu or {}).get('display_name') or '')
        except Exception as e:
            msg = f'照片收不到（{str(e)[:40]}），請再傳一次 🙏'
        if msg:
            return reply(con, acc, ev['replyToken'], _txt(msg))
    if ev['type'] == 'message' and (ev.get('message') or {}).get('type') in ('image', 'file') and uid and (not gid or gid == admin_group(con)) and acc == WORK_ACCOUNT:
        import ledger                    # 記帳：管理層打「記帳」後 30 分鐘內傳的收據照片／PDF 掃描檔（一對一或派單管理群）
        if ledger.waiting(con, uid) and ledger.who_ok(con, uid):
            m0 = ev['message']
            try:
                if m0['type'] == 'file':
                    out = ledger.line_file(con, uid, m0.get('fileName') or '', content(con, m0['id'], acc, 10 * 1024 * 1024 + 1), ev['replyToken'], (lu or {}).get('display_name') or '管理層', gid or uid)
                else:
                    out = ledger.line_photo(con, uid, content(con, m0['id'], acc), ev['replyToken'], (lu or {}).get('display_name') or '管理層', gid or uid)
            except Exception as e:
                out = _txt(f'收不到（{str(e)[:40]}），請再傳一次 🙏')
            if out:
                return reply(con, acc, ev['replyToken'], out, 'ledger')
            return                       # 有金鑰：背景讀完用同一個 reply 回確認卡（不花訊息額度）
    if ev['type'] == 'message' and (ev.get('message') or {}).get('type') == 'video' and uid and not gid and (lu or {}).get('customer_id') and not (lu or {}).get('worker_id'):
        _cust_media(con, acc, ev, lu, 'video')              # 業主傳的現場影片 → 放進派工卡（不回覆業主，）
        return
    if ev['type'] == 'message' and (ev.get('message') or {}).get('type') == 'image' and uid:
        import booking
        try:
            r = booking.photos_from_line(con, uid, content(con, ev['message']['id'], acc))
        except Exception as e:          # 圖太大、格式不符：不打擾客戶，交給同事
            r = None; notify.staff(con, '客戶 LINE 照片沒收進報價', str(e)[:80], f"/#c/{lu['customer_id']}" if (lu or {}).get('customer_id') else '/#pairing')
            admin_notify(con, '⚠️ 客戶在 LINE 傳的照片沒收進報價', [('原因', str(e)[:80]), ('處理', '請在官方 LINE 聊天室看照片，必要時請客戶重傳')], f"/#c/{lu['customer_id']}" if (lu or {}).get('customer_id') else '/#pairing', '#C47F00')
        if r is None and not gid and (lu or {}).get('customer_id') and not (lu or {}).get('worker_id'):
            if _cust_media(con, acc, ev, lu, 'image'):
                return                           # 照樣存進工單，但不回覆業主
        if r == 'ready':
            return reply(con, acc, ev['replyToken'], _txt('📷 照片收到了，謝謝您！報價會先交給主管確認，確認後正式報價單會傳到這裡。'))
        if r and r.startswith('added:'):
            n = int(r.split(':')[1])
            if n < booking.MIN_PHOTOS:
                return reply(con, acc, ev['replyToken'], _txt(f'📷 已收到 {n}/{booking.MIN_PHOTOS} 張，再傳 {booking.MIN_PHOTOS - n} 張就可以送給主管報價（每個要服務的區域都拍一張）。'))
            if n % 5 == 0:          # 已送審後補傳：每 5 張回一次，不洗版
                return reply(con, acc, ev['replyToken'], _txt(f'📷 已收到（共 {n} 張），會一起給主管看 🙏'))
        return
    if ev['type'] == 'message' and (ev.get('message') or {}).get('type') == 'text' and uid:
        text = ev['message']['text'].strip()
        con.execute('UPDATE line_users SET last_text=? WHERE account=? AND user_id=?', (text[:200], acc, uid))
        if re.match(r'\s*我是(老|舊)客戶', text):        # 通用綁定連結（一條傳給很多客戶）→ 用 LINE 名稱／電話找舊資料；對到電話的要管理層在「LINE 配對」確認
            if (lu or {}).get('customer_id'):
                return reply(con, acc, ev['replyToken'], _txt('您已經綁定好了 👍 之後的提醒、完成照片都會傳到這裡。'))
            if not (lu or {}).get('worker_id') and not (lu or {}).get('is_staff'):
                return bind_prompt(con, acc, uid, lu, ev['replyToken'])
        m = re.search(r'綁定\s*([0-9A-Za-z]{6,10})(?![0-9A-Za-z])', text)
        if m:
            fails = con.execute("SELECT COUNT(*) FROM line_inbox WHERE account=? AND user_id=? AND type='bind_fail' AND at>?",
                                (acc, uid, (dt.datetime.now(c.TZ) - dt.timedelta(hours=1)).isoformat())).fetchone()[0]
            if con.execute("SELECT COUNT(*) FROM line_inbox WHERE type='bind_fail' AND at>?", ((dt.datetime.now(c.TZ) - dt.timedelta(hours=1)).isoformat(),)).fetchone()[0] >= 40:
                if ix.setting(con, 'bind_flood_day') != c.now()[:13]:      # 全系統一小時失敗 40 次：可能有人在猜碼 → 暫停、通知管理群
                    ix.set_setting(con, 'bind_flood_day', c.now()[:13])
                    admin_notify(con, '⚠️ 綁定碼一小時錯 40 次以上，已暫停綁定 1 小時', [('可能', '有人在猜綁定碼'), ('處理', '不用做什麼，1 小時後自動恢復')], '', '#C62828')
                return reply(con, acc, ev['replyToken'], _txt('系統暫時無法綁定，請 1 小時後再試，或直接留言請專人協助 🙏'))
            if fails >= 5:
                return reply(con, acc, ev['replyToken'], _txt('錯誤次數太多，請 1 小時後再試，或直接留言請專人協助 🙏'))
            b = con.execute("SELECT * FROM line_bind WHERE code=? AND expires>? AND (account=? OR kind='worker')", (m.group(1).upper(), int(time.time()), acc)).fetchone()      # 夥伴不分品牌（都是示範公司的夥伴）→ 夥伴碼在示範服務店／生活線／企業線哪個官方 LINE 傳都能綁
            if not b:
                con.execute("INSERT INTO line_inbox(at,account,user_id,type,text) VALUES(?,?,?,'bind_fail',?)", (c.now(), acc, uid, m.group(1)))
                return reply(con, acc, ev['replyToken'], _txt(f'綁定碼不對或已過期，請跟{brands.name(acc)}的專人重新索取 🙏'))
            if b['kind'] == 'customer':      # 店長一對一傳的客戶綁定碼
                if (lu or {}).get('customer_id') == b['ref_id']:
                    return reply(con, acc, ev['replyToken'], _txt('您已經綁定好了 👍 之後的提醒、完成照片都會傳到這裡。'))
                if (lu or {}).get('is_staff') or uid in staff_users(con):      # 管理層自己按了客戶專屬連結 → 說明，不建待確認（以前會被確認成客戶）
                    cu0 = c.get(con, 'customers', b['ref_id'])
                    return reply(con, acc, ev['replyToken'], _txt(
                        f"這是 {cu0['name']} 專屬的綁定連結（一條只給一位客戶）。你是管理層，系統不會把你的 LINE 綁成客戶，連結也還能用 👍\n"
                        f"・請把連結轉傳給 {cu0['name']} 本人，他送出就綁好了\n"
                        "・要一條傳給很多客戶：中控台「客戶」頁 →「通用綁定訊息」（客戶按了用 LINE 名稱／電話找自己的資料）\n"
                        "・想看客戶看到的畫面：管理選單「測試視角」"))
                if (lu or {}).get('customer_id') or (lu or {}).get('worker_id') or (lu or {}).get('is_staff') or uid in staff_users(con):      # 稽核 H1：這個 LINE 已經是別人 → 不直接改綁（連結被轉傳會認錯人）
                    cu0 = c.get(con, 'customers', b['ref_id'])
                    request_bind(con, acc, uid, 'customer', cu0['id'], cu0['name'], cu0['phone'] or '', (lu or {}).get('display_name') or '')
                    c.audit(con, 'bind_code_conflict', 'customers', cu0['id'], after={'line_user': uid})
                    return reply(con, acc, ev['replyToken'], _txt('這個 LINE 已經綁定其他資料了，專人確認後會通知您 🙏'))
                if con.execute('SELECT 1 FROM line_users WHERE customer_id=? AND account=? AND user_id!=? AND is_friend=1', (b['ref_id'], acc, uid)).fetchone():
                    return reply(con, acc, ev['replyToken'], _txt('這份資料已經綁定其他 LINE 了，專人會跟您確認 🙏'))
                con.execute('UPDATE line_users SET customer_id=NULL WHERE customer_id=? AND account=? AND is_friend=0', (b['ref_id'], acc))      # 舊的已封鎖帳號不擋
                con.execute("UPDATE line_users SET customer_id=?,onboard='customer',suggest_cid='' WHERE account=? AND user_id=?", (b['ref_id'], acc, uid))
                con.execute("UPDATE customers SET line_account=? WHERE id=? AND (line_account IS NULL OR line_account='')", (acc, b['ref_id']))
                con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
                _close_bind_requests(con, acc, uid, 'customer', '系統（綁定碼）')
                cu = c.get(con, 'customers', b['ref_id'])
                c.audit(con, 'line_customer_bind', 'customers', cu['id'], after={'line_user': uid, 'via': 'code'})
                admin_notify(con, '🔗 客戶完成 LINE 綁定', [('客戶', cu['name']), ('LINE', (lu or {}).get('display_name') or '')], f"/#c/{cu['id']}", '#2E7D32')
                after_customer_linked(con, cu['id'])          # 稽核 C2
                return reply(con, acc, ev['replyToken'], card_next_step(con, acc, uid, cu['id']))
            col = 'worker_id' if b['kind'] == 'worker' else 'staff_user'
            if b['kind'] == 'worker' and (((lu or {}).get('worker_id') and lu['worker_id'] != b['ref_id']) or con.execute(
                    'SELECT 1 FROM line_users WHERE worker_id=? AND account=? AND user_id!=? AND is_friend=1', (b['ref_id'], acc, uid)).fetchone()):      # 稽核 H1：夥伴碼也不直接改綁
                w0 = c.get(con, 'workers', b['ref_id'])
                request_bind(con, acc, uid, 'worker', w0['id'], w0['name'], w0['phone'] or '', (lu or {}).get('display_name') or '')
                return reply(con, acc, ev['replyToken'], _txt('這個 LINE（或這位夥伴）已經綁定其他資料了，管理層確認後會通知你 🙏'))
            con.execute(f'UPDATE line_users SET {col}=? WHERE account=? AND user_id=?', (b['ref_id'], acc, uid))
            if col == 'worker_id':
                worker_menu_link(con, uid, acc)
                _close_bind_requests(con, acc, uid, 'worker', '系統（綁定碼）')      # 每日檢查：同一個 LINE 之前的夥伴待確認一起結案
            con.execute('DELETE FROM line_bind WHERE code=?', (b['code'],))
            name = c.get(con, 'workers', b['ref_id'])['name'] if b['kind'] == 'worker' else b['ref_id']
            return reply(con, acc, ev['replyToken'], card_text('✅ 綁定成功', f'{name}，之後的派工、提醒都會傳到這裡。', '#2E7D32'))
        if (lu or {}).get('onboard') == 'verify' and lu.get('suggest_cid') and re.fullmatch(r'\d{4}', text):
            cid = lu['suggest_cid']; tries = con.execute("SELECT COUNT(*) FROM line_inbox WHERE account=? AND user_id=? AND type='bind_fail' AND at>?",
                                                         (acc, uid, (dt.datetime.now(c.TZ) - dt.timedelta(hours=1)).isoformat())).fetchone()[0]
            if tries < 3 and re.sub(r'\D', '', c.get(con, 'customers', cid)['phone'] or '')[-4:] == text:
                return _bind_customer(con, acc, uid, lu, cid, ev)
            con.execute("INSERT INTO line_inbox(at,account,user_id,type,text) VALUES(?,?,?,'bind_fail','last4')", (c.now(), acc, uid))
            if tries + 1 >= 3:
                con.execute("UPDATE line_users SET onboard='',suggest_cid='' WHERE account=? AND user_id=?", (acc, uid))
                return reply(con, acc, ev['replyToken'], _txt('末四碼不符，已暫停自動綁定。請直接留言，專人會協助您 🙏'))
            return reply(con, acc, ev['replyToken'], _txt('末四碼不符，請再確認一次 🙏'))
        if uid in staff_users(con) or (lu or {}).get('worker_id') or (lu or {}).get('is_staff') or (gid and gid == admin_group(con)):
            import sysreport as issues
            mi = issues.LINE_TRIGGER.match(text)
            if mi and (lu or {}).get('worker_id') and uid not in staff_users(con):          # 稽核：夥伴在現場打「回報：客廳有水漬」→ 現場回報給管理群，不是系統功能回報
                import site_report
                cur = site_report.current(con, lu['worker_id'])
                if cur:
                    try:
                        return reply(con, acc, ev['replyToken'], _txt(site_report.create(con, cur['id'], lu['worker_id'], 'other', 0, '', mi.group(1).strip()[:500], (lu or {}).get('display_name') or '')['message']))
                    except ValueError:
                        pass
            if mi:                                                                         # 功能回報：「回報：…」（管理層、夥伴）
                body = mi.group(1).strip()
                who = (lu or {}).get('display_name') or ('夥伴' if (lu or {}).get('worker_id') else '管理層')
                iid = issues.report(con, body.split('\n')[0][:80], body, who, 'worker' if (lu or {}).get('worker_id') and uid not in staff_users(con) else 'staff',
                                    'feature' if text.strip().startswith(('功能建議', '想要的功能')) else 'bug', notify_uid=gid or uid, notify_acc=acc)
                return reply(con, acc, ev['replyToken'], _txt(f'收到 ✅ 已記到功能回報（{iid}）。每天晚上 22:00 左右統一處理，處理好會在這裡通知 🙏'))
        if re.sub(r'\s', '', text).lower() in ('linepay示範', 'linepay測試', '付款示範') and not gid and (uid in viewing(con) or uid in staff_users(con)):      # LINE Pay 審核示範
            return reply(con, acc, ev['replyToken'], linepay_demo(con, acc, uid))
        if text.strip().startswith('記帳') and (not gid or gid == admin_group(con)) and acc == WORK_ACCOUNT:      # 記帳（傳收據照片／打「記帳 350 加油」）
            import ledger
            if ledger.who_ok(con, uid):
                if text.strip() == '記帳':
                    return reply(con, acc, ev['replyToken'], _txt(ledger.start(con, uid)), 'ledger')
                try:
                    out = ledger.line_text(con, uid, text, (lu or {}).get('display_name') or '管理層')
                except ValueError as e:
                    out = _txt(str(e))
                if out:
                    return reply(con, acc, ev['replyToken'], out, 'ledger')
        if text.strip() in ('收款紀錄', '收款', '付款紀錄', '收款查詢') and (not gid or gid == admin_group(con)) and acc == WORK_ACCOUNT and uid in money_users(con):      # 管理群點得到歷史付款
            import payhist
            return reply(con, acc, ev['replyToken'], payhist.card(con))
        if text.strip() in ('派工分級', '夥伴分級', '分級') and not gid and (uid in money_users(con) or uid in deciders(con)):      # 叫出派工分級卡
            import tiers
            return reply(con, acc, ev['replyToken'], tiers.cards(con))
        if text.strip() in ('回管理', '切回管理', '管理選單', '回到管理') and uid in viewing(con):      # 管理層：從客戶／夥伴視角切回
            c.CTX.viewing = None
            return reply(con, acc, ev['replyToken'], view_back(con, acc, uid))
        if text.strip() in ('商城', '示範商城', '買耗材', '耗材'):                    # 示範商城（業主、夥伴都可以買）
            import shop
            return reply(con, acc, ev['replyToken'], shop.menu(con, acc, lu, uid))
        if text.strip() in ('我的訂單', '商城訂單', '我的商城訂單'):
            import shop
            return reply(con, acc, ev['replyToken'], shop.my_orders(con, uid, acc))
        mo = re.match(r'^訂單\s*([Ss]\d{4}[A-Za-z0-9]{4})\s+([A-Za-z0-9_-]{6})$', text.strip())
        if mo and not gid:                                                               # 商城網站訂單 → 連到這個 LINE（訂單進度頁一鍵帶出這則訊息；電腦掃 QR code）
            import shop
            return reply(con, acc, ev['replyToken'], shop.link_line(con, acc, uid, mo.group(1).upper(), mo.group(2)))
        _expire_waits(con, uid)                                                          # 稽核：按了「其他」沒打字，之後的話不能被當成請假原因／用品申請
        if ix.setting(con, f'iv_wait:{uid}'):                                            # 夥伴面試：應徵者打方便的時間
            import interviews
            iid = ix.setting(con, f'iv_wait:{uid}'); ix.set_setting(con, f'iv_wait:{uid}', '')
            return reply(con, acc, ev['replyToken'], _txt(interviews.want_time(con, iid, text)))
        if ix.setting(con, f'tm_wait:{uid}'):                                            # 客戶見證：打的那幾句話
            import testimonials
            tid = ix.setting(con, f'tm_wait:{uid}'); ix.set_setting(con, f'tm_wait:{uid}', '')
            testimonials.add_text(con, tid, text)
            return reply(con, acc, ev['replyToken'], [{'type': 'text', 'text': '收到，謝謝您 🥰\n這段話可以不具名（不放姓名）放在我們的官網或社群，跟服務前後照片一起分享給其他人參考嗎？',
                'quickReply': {'items': [{'type': 'action', 'action': {'type': 'postback', 'label': '可以，不具名分享', 'data': f'a=tm_c&t={tid}&v=1', 'displayText': '可以，不具名分享'}},
                                         {'type': 'action', 'action': {'type': 'postback', 'label': '只給你們看就好', 'data': f'a=tm_c&t={tid}&v=0', 'displayText': '只給你們看就好'}}]}}])
        if ix.setting(con, f'supamt_wait:{uid}'):                                         # 買用品：發票金額
            import supplies
            return reply(con, acc, ev['replyToken'], _txt(supplies.receipt_amount(con, uid, text)))
        if ix.setting(con, f'sup_wait:{uid}') and (lu or {}).get('worker_id'):           # 買用品：要買什麼、多少錢
            import supplies
            oid = ix.setting(con, f'sup_wait:{uid}'); ix.set_setting(con, f'sup_wait:{uid}', '')
            try:
                supplies.request(con, lu['worker_id'], '' if oid == '-' else oid, text)
            except ValueError as e:
                return reply(con, acc, ev['replyToken'], _txt(str(e)))
            return reply(con, acc, ev['replyToken'], _txt('收到 🙏 已送經理確認，同意後會在這裡通知你；先不要買喔。'))
        if (lu or {}).get('worker_id') and re.search(r'(買|補).{0,6}(耗材|用品|垃圾袋|抹布|拖把|菜瓜布|手套|海綿)', text):   # 夥伴直接打「要買耗材」
            ix.set_setting(con, f'sup_wait:{uid}', '-')
            import supplies
            supplies.request(con, lu['worker_id'], '', text); ix.set_setting(con, f'sup_wait:{uid}', '')
            return reply(con, acc, ev['replyToken'], _txt('收到 🙏 已送經理確認要不要買，同意後會在這裡通知你；買好再傳發票照片。'))
        if ix.setting(con, f'qa_wait:{uid}') and uid in money_users(con):                # 店長在 LINE 打的報價金額
            import quickquote
            qid = ix.setting(con, f'qa_wait:{uid}'); _staff_wait(con, uid, 'qa_wait', '')
            try:
                return reply(con, acc, ev['replyToken'], quickquote.typed(con, qid, text, (lu or {}).get('display_name') or '管理層'))
            except ValueError as e:
                _staff_wait(con, uid, 'qa_wait', qid)
                return reply(con, acc, ev['replyToken'], _txt(str(e)))
        is_cmd = bool(re.match(r'^(查|找|查詢)\s*\S', text) or re.match(r'^(回報|功能建議|想要的功能)[:：]', text))      # 查詢／回報指令優先，不要被「等打字」吃掉
        if ix.setting(con, f'prep_wait:{uid}') and uid in staff_users(con) and not is_cmd:              # 店長：案件前補充提醒
            import prep
            oid = ix.setting(con, f'prep_wait:{uid}'); _staff_wait(con, uid, 'prep_wait', '')
            return reply(con, acc, ev['replyToken'], _txt(prep.add_note(con, oid, text, (lu or {}).get('display_name') or '管理層')))
        if ix.setting(con, f'tools_wait:{uid}'):                                          # 業主打字說明家裡有哪些工具
            import sitetools
            sid = ix.setting(con, f'tools_wait:{uid}'); ix.set_setting(con, f'tools_wait:{uid}', '')
            return reply(con, acc, ev['replyToken'], _txt(sitetools.answered(con, acc, uid, lu, sid, 'basic', text)))
        if ix.setting(con, f'ent_wait:{uid}'):                                            # 開門流程：客戶打字說明密碼／鑰匙／開門的人
            import door
            oid = ix.setting(con, f'ent_wait:{uid}'); ix.set_setting(con, f'ent_wait:{uid}', '')
            return reply(con, acc, ev['replyToken'], door.customer_detail(con, uid, oid, text))
        if ix.setting(con, f'entedit_wait:{uid}') and uid in staff_users(con) and not is_cmd:           # 開門流程：店長打字修改開門方式
            import door
            sid = ix.setting(con, f'entedit_wait:{uid}'); _staff_wait(con, uid, 'entedit_wait', '')
            nxt = door.next_fill(con, skip_sid=sid)
            return reply(con, acc, ev['replyToken'], _txt(door.staff_edit(con, sid, text, (lu or {}).get('display_name') or '管理層')) + (nxt or []))
        if ix.setting(con, f'scope_wait:{uid}') and uid in staff_users(con) and not is_cmd:    # 管理層打字修改詳細需求（一行一項）
            import scopereview
            soid = ix.setting(con, f'scope_wait:{uid}'); _staff_wait(con, uid, 'scope_wait', '')
            return reply(con, acc, ev['replyToken'], _txt(scopereview.approve(con, soid, (lu or {}).get('display_name') or '管理層', text)))
        if ix.setting(con, f'pcf_wait:{uid}') and uid in staff_users(con) and not is_cmd:      # 業主是哪個 LINE：店長打 LINE 名稱
            import pairing
            cid2 = ix.setting(con, f'pcf_wait:{uid}'); _staff_wait(con, uid, 'pcf_wait', '')
            return reply(con, acc, ev['replyToken'], pairing.search_line(con, cid2, text))
        if ix.setting(con, f'pr_wait:{uid}') and uid in staff_users(con):                 # LINE 配對：店長打字找人
            import pairing
            b, _, tu = ix.setting(con, f'pr_wait:{uid}').partition('|'); _staff_wait(con, uid, 'pr_wait', '')
            return reply(con, acc, ev['replyToken'], pairing.typed(con, b, tu, text))
        if ix.setting(con, f'ask_wait:{uid}') and uid in staff_users(con) and not text.startswith('【'):   # 點選題按了「我打字回答」後打的字
            topic = ix.setting(con, f'ask_wait:{uid}'); _staff_wait(con, uid, 'ask_wait', '')
            return _staff_answer(con, acc, uid, f'【{topic}】{text}', ev)
        if ix.setting(con, f'guide_wait:{uid}') and uid in staff_users(con):              # 夥伴介紹「要修改」後打的字
            ix.set_setting(con, f'guide_wait:{uid}', '')
            who = (lu or {}).get('display_name') or '管理層'
            notes = json.loads(ix.setting(con, 'guide_edit_notes') or '[]') + [{'by': who, 'at': c.now(), 'text': text[:800]}]
            ix.set_setting(con, 'guide_edit_notes', json.dumps(notes, ensure_ascii=False))
            if uid != OWNER_UID:
                queue(con, WORK_ACCOUNT, OWNER_UID, 'admin', f'{who} 對夥伴介紹的修改意見', _txt(f'✏️ {who} 對「夥伴系統介紹」的修改意見：\n{text[:800]}'))
            return reply(con, acc, ev['replyToken'], _txt('收到 ✏️ 已記下，改好會再請你們看一次。'))
        if ix.setting(con, f'lv_wait:{uid}') and (lu or {}).get('worker_id'):           # 請假原因選「其他」後打的字
            oid = ix.setting(con, f'lv_wait:{uid}'); ix.set_setting(con, f'lv_wait:{uid}', '')
            return _leave_tap(con, acc, uid, lu, None, 'lv2', {'o': oid, 'r': text[:100]}, ev)
        if ix.setting(con, f'srep_wait:{uid}') and (lu or {}).get('worker_id'):         # 現場回報：說明狀況
            import site_report
            oid2, k2, wid2 = (ix.setting(con, f'srep_wait:{uid}').split('|') + ['', '', ''])[:3]; ix.set_setting(con, f'srep_wait:{uid}', '')
            try:
                return reply(con, acc, ev['replyToken'], _txt(site_report.create(con, oid2, wid2, k2, 0, '', text[:500], (lu or {}).get('display_name') or '')['message']))
            except ValueError as e:
                return reply(con, acc, ev['replyToken'], _txt(f'沒辦法回報：{e}'))
        if (lu or {}).get('worker_id') and re.sub(r'\s', '', text) in SREP_WORDS:     # 夥伴打「回報／加時／趕不上」→ 點選
            import site_report
            wid2 = lu['worker_id']; cur = site_report.current(con, wid2); k2 = SREP_WORDS[re.sub(r'\s', '', text)]
            if not cur or not k2:
                return reply(con, acc, ev['replyToken'], site_report.start_card(con, wid2, cur['id'] if cur else None))
            if k2 == 'late' and not cur['arrived_at']:            # 還沒到這場 → 是「這場」會晚到（照原本的晚到流程，會通知業主）
                q = lambda m: {'type': 'action', 'action': {'type': 'postback', 'label': f'晚 {m} 分', 'data': f"a=wlate&o={cur['id']}&m={m}", 'displayText': f'會晚 {m} 分'}}
                return reply(con, acc, ev['replyToken'], [{'type': 'text', 'text': f"{cur['start']} 這場大約會晚多久？👇", 'quickReply': {'items': [q(15), q(30)]}}])
            return reply(con, acc, ev['replyToken'], site_report.options(con, wid2, cur['id'], k2, {})[0])
        if text.strip() in ('請假', '我要請假', '臨時請假') and (lu or {}).get('worker_id'):
            return _leave_tap(con, acc, uid, lu, None, 'lv', {}, ev)
        if uid in staff_users(con) and _review_note(con, acc, uid, text, ev) is not None:      # 審核「要修改」後打的字
            return
        if uid in staff_users(con) and ix.setting(con, f'rs_wait:{uid}') and not text.startswith('【') and not re.match(r'^(查|找|查詢|記帳)\b', text):          # 業主改時間「這時間不行」後打的字 → 傳給業主（稽核：60 分鐘過期、指令不算）
            import reschedule
            rid = ix.setting(con, f'rs_wait:{uid}'); _staff_wait(con, uid, 'rs_wait', '')
            return reply(con, acc, ev['replyToken'], _txt(reschedule.reject(con, rid, (lu or {}).get('display_name') or '管理層', text)))
        if text.startswith('【') and uid in staff_users(con) and _staff_answer(con, acc, uid, text, ev) is not None:
            return
        mq = re.match(r'^(查|找|查詢)\s*(.+)$', text)
        if mq and uid in staff_users(con):
            return _staff_search(con, acc, ev, mq.group(2).strip())
        if text in ('綁定', '綁定資料', '確認資料') and not (lu or {}).get('worker_id'):
            if (lu or {}).get('customer_id'):
                return reply(con, acc, ev['replyToken'], _txt(f"已經綁定好了：{c.get(con, 'customers', lu['customer_id'])['name']} 👍"))
            return bind_prompt(con, acc, uid, lu, ev['replyToken'])
        if text == '應徵' and acc in APPLY_ACCOUNTS and not (lu or {}).get('customer_id') and not (lu or {}).get('worker_id'):
            import worker_portal
            return reply(con, acc, ev['replyToken'], flex('應徵夥伴', _bubble(_header('應徵夥伴 🙋', '約 3–5 分鐘填完', brand=acc),
                [_t(f'點選可以接的地區、技能和時間（幾乎不用打字），{brands.name(acc)}確認後會在這裡通知你。', wrap=True)],
                [_btn('開始填應徵資料', uri=worker_portal.onboard_token(con, acc, uid, 'apply'), primary=True)])), 'role')
        if text in ('重新體驗', '我是新朋友') and uid in test_users(con):
            con.execute("UPDATE line_users SET onboard='asked',customer_id=NULL,worker_id=NULL,nudged_at=? WHERE account=? AND user_id=?", (c.now(), acc, uid))
            try:
                api(con, f'/v2/bot/user/{uid}/richmenu', method='DELETE', acc=acc)      # 拿掉夥伴選單，回到一般客戶看到的
            except ValueError:
                pass
            return reply(con, acc, ev['replyToken'], card_welcome(acc, con), 'welcome')
        mki = None if gid else re.search(r'我想問[^（(\n]{0,20}[（(]([a-z0-9][a-z0-9-]{0,39})[）)]', text)
        if mki:                              # 行銷連結帶進來的那句「您好，我想問XX（代號）」→ 記進 LINE＋回拍照估價卡
            import mklink
            return mklink.on_line(con, acc, uid, mki.group(1), ev, lu)
        m = re.search(r'預約編號\s*([A-Z0-9]{6})', text.upper())
        if m:
            fails = con.execute("SELECT COUNT(*) FROM line_inbox WHERE account=? AND user_id=? AND type='bind_fail' AND at>?",
                                (acc, uid, (dt.datetime.now(c.TZ) - dt.timedelta(hours=1)).isoformat())).fetchone()[0]
            if fails >= 5:
                return reply(con, acc, ev['replyToken'], _txt('錯誤次數太多，請 1 小時後再試，或直接留言請專人協助 🙏'))
            q = con.execute('SELECT * FROM inquiries WHERE line_code=? AND account=?', (m.group(1), acc)).fetchone()
            if not q:
                con.execute("INSERT INTO line_inbox(at,account,user_id,type,text) VALUES(?,?,?,'bind_fail',?)", (c.now(), acc, uid, m.group(1)))
                return reply(con, acc, ev['replyToken'], _txt('找不到這個預約編號，專人會盡快跟您聯絡 🙏'))
            if q['line_user_id'] and q['line_user_id'] != uid:          # 已經有人用這組編號綁過：不覆蓋，交給管理層
                admin_notify(con, '⚠️ 預約編號被另一個 LINE 使用', [('預約', f"{q['name']}（{q['phone']}）"), ('新的 LINE', (lu or {}).get('display_name') or uid[:8]), ('處理', '請在官方 LINE 確認是不是本人')], f"/#inq/{q['id']}", '#C62828')
                return reply(con, acc, ev['replyToken'], _txt('這個預約編號已經用其他 LINE 確認過了，專人會跟您聯絡確認 🙏'))
            con.execute('UPDATE inquiries SET line_user_id=? WHERE id=?', (uid, q['id']))
            import mklink; mklink.tag_quote(con, q['id'], acc, uid)        # 從行銷連結進 LINE 的客人，估價單補上代號
            if (viewing(con).get(uid) or {}).get('as') == 'customer':      # 測試視角送的估價＝示範（不碰真的客戶，照樣走客戶會看到的流程）
                q = demo_booking(con, q)
            if q['customer_id']:
                con.execute('UPDATE line_users SET customer_id=COALESCE(customer_id,?) WHERE account=? AND user_id=?', (q['customer_id'], acc, uid))
                con.execute("UPDATE customers SET line_account=? WHERE id=? AND (line_account IS NULL OR line_account='')", (acc, q['customer_id']))
            reply(con, acc, ev['replyToken'], card_booking(con, dict(q), acc))
            if q['order_id'] and not con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='assigned' AND status NOT LIKE 'skipped%'", (q['order_id'],)).fetchone():
                o = c.get(con, 'orders', q['order_id'])
                if con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0] >= o['required_workers'] and o.get('crew_ok'):
                    on_filled(con, o['id'])      # 夥伴已經先接了 → 補發承接卡
            return
        if not gid and (lu or {}).get('customer_id') and text.strip() in ('緊急', '緊急回報', '救命', '急'):
            import urgent
            o_ = urgent.active_order(con, lu['customer_id'])
            if o_:
                return reply(con, acc, ev['replyToken'], urgent.customer_card(con, o_))
        if not gid and (lu or {}).get('customer_id') and text.strip():
            import urgent; urgent.offer_once(con, acc, uid, lu['customer_id'])      # 服務時間內傳訊息 → 推一次緊急回報卡
        if not gid and text.strip() and ((lu or {}).get('customer_id') or (lu and not lu.get('worker_id') and not lu.get('is_staff') and uid not in staff_users(con))):
            # 負責人 已綁客戶留言，系統沒處理 → 30 分鐘後提醒派單管理群；稽核：沒綁定的好友也提醒（生活線那邊有人說已到現場在等，沒人看到）
            k = f'cmsg:{acc}:{uid}'
            if not ix.setting(con, k):
                ix.set_setting(con, k, c.now())
            ix.set_setting(con, f'cmsg_txt:{acc}:{uid}', (ix.setting(con, f'cmsg_txt:{acc}:{uid}') + ' ' + text.strip())[-120:])
            ix.set_setting(con, f'cmsg_last:{acc}:{uid}', text.strip()[:60])
        _gate(con, acc, uid, lu, ev)
        return
    if ev['type'] != 'postback':
        return
    data = dict(x.split('=', 1) for x in (ev['postback'].get('data') or '').split('&') if '=' in x)
    act, oid = data.get('a'), data.get('o', '')
    if data.get('pv') == '1':                 # 群發送審的預覽卡片 → 只說明會發生什麼，不真的執行
        return reply(con, acc, ev['replyToken'], preview_reply(data))
    if act == 'role' and data.get('r') == 'w' and acc not in APPLY_ACCOUNTS:
        return reply(con, acc, ev['replyToken'], _txt('謝謝您的詢問 🙏 這裡目前沒有開放夥伴應徵；有服務需求歡迎直接跟我們說！'), 'role')
    if act == 'role':
        import worker_portal
        if data.get('r') == 'w' and (lu or {}).get('customer_id'):
            admin_notify(con, '🙋 客戶想應徵夥伴', [('客戶', c.get(con, 'customers', lu['customer_id'])['name']), ('處理', '請在官方 LINE 跟他確認，確認後再請他填應徵')], f"/#c/{lu['customer_id']}", '#0A84FF')
            return reply(con, acc, ev['replyToken'], _txt('謝謝您有興趣加入 🙏 您目前是我們的客戶，專人會在這裡跟您確認應徵的事。'), 'role')
        if data.get('r') == 'w' and (lu or {}).get('worker_id'):          # 稽核 M1：已經是夥伴（或應徵中）按到舊卡 → 不再重送應徵表、不改 onboard
            w_ = c.get(con, 'workers', lu['worker_id'])
            return reply(con, acc, ev['replyToken'], _txt(f"{w_['name']}，你的資料已經在我們這裡了 👍 有問題直接在這裡留言就好。"), 'role')
        if data.get('r') == 'w':
            con.execute("UPDATE line_users SET onboard='asked' WHERE account=? AND user_id=?", (acc, uid))
            return reply(con, acc, ev['replyToken'], flex('應徵夥伴', _bubble(_header('應徵夥伴 🙋', '約 3–5 分鐘填完', brand=acc),
                [_t(f'點選可以接的地區、技能和時間（幾乎不用打字）。{brands.name(acc)}確認後會在這裡通知你，之後的工作、班表、打卡都在這個 LINE 完成。', wrap=True)],
                [_btn('開始填應徵資料', uri=worker_portal.onboard_token(con, acc, uid, 'apply'), primary=True)])), 'role')
        return reply(con, acc, ev['replyToken'], flex('請先留基本資料', _bubble(_header('您好 👋', '先留個基本資料', brand=acc),
            [_t('留下稱呼和電話（10 秒），專人就能馬上幫您處理。', wrap=True)],
            [_btn('留基本資料', uri=worker_portal.onboard_token(con, acc, uid, 'hi'), primary=True)])), 'role')
    if act and act.startswith('w_'):
        return _worker_menu(con, acc, uid, lu, gid, act, ev)
    if act in ('door', 'door_self', 'door_ack', 'ent', 'ent_again'):      # 開門流程（客戶）：我來開門／照說明進門／前一天會來開門／選開門方式
        import door
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order or order['status'] in ('cancelled', 'archived') or ((lu or {}).get('customer_id') != order['customer_id'] and uid not in test_users(con)):
            return reply(con, acc, ev['replyToken'], _txt('收到，專人會跟您確認 🙏'))
        if order['status'] == 'completed' and act in ('door', 'door_self', 'door_ack'):        # 稽核：完工後按舊卡片不要再推播夥伴、叫管理群
            return reply(con, acc, ev['replyToken'], _txt('這次服務已經完成了，謝謝您 🙏 有需要請直接留言。'))
        if act in ('door', 'door_self'):
            return reply(con, acc, ev['replyToken'], door.customer_open(con, oid, act == 'door_self'))
        if act == 'door_ack':
            return reply(con, acc, ev['replyToken'], door.customer_ack(con, oid))
        if act == 'ent_again':
            return reply(con, acc, ev['replyToken'], door.card_ask(con, oid))
        return reply(con, acc, ev['replyToken'], door.customer_pick(con, acc, uid, oid, data.get('m', '')))
    if act == 'pwf_none':                                     # 管理層：這位夥伴沒加官方 LINE → 給加好友（綁定）訊息轉傳，14 天內不再列
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        import pairing
        wid = data.get('w', '')
        if not con.execute('SELECT 1 FROM workers WHERE id=?', (wid,)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('找不到這位夥伴'))
        pairing.mark_worker_none(con, wid, (lu or {}).get('display_name') or '管理層')
        nx = pairing.next_worker_card(con, wid) if pairing.push_on(con) else pairing.web_tip(con)
        return reply(con, acc, ev['replyToken'], worker_bind_invite(con, wid)[:4] + (nx or []))
    if act == 'pall':                                         # 顯示全部還沒配對的 LINE 給我們選
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import pairing
        if not pairing.push_on(con):                         # 一長串選單太亂 → 到中控台選
            return reply(con, acc, ev['replyToken'], pairing.web_tip(con, '全部還沒配對的 LINE 在中控台看（有對話、群組、證據）。'))
        return reply(con, acc, ev['replyToken'], pairing.all_card(con, data.get('k', 'c'), data.get('r', ''), data.get('p', '0')))
    if act in ('pcf_type', 'pcf_none'):                      # 店長：這位業主是哪個 LINE（打字找／他沒加官方 LINE）
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        import pairing
        if act == 'pcf_type':
            _staff_wait(con, uid, 'pcf_wait', data.get('c', ''))
            return reply(con, acc, ev['replyToken'], _txt('請打他的 LINE 名稱（打幾個字就好），系統幫妳找 🔎'))
        msg = pairing.mark_none(con, data.get('c', ''), (lu or {}).get('display_name') or '管理層')
        nx = (pairing.next_customer_card(con, data.get('c', '')) or _txt('🎉 這批業主都處理好了，謝謝！')) if pairing.push_on(con) else pairing.web_tip(con)
        return reply(con, acc, ev['replyToken'], _txt(msg) + nx)
    if act in ('ent_next', 'ent_set', 'ent_skip', 'pr_next', 'pr', 'pr_skip', 'pr_type'):   # 店長（管理層）：補開門方式、LINE 好友配對（負責人 ）
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        import door, pairing
        who = (lu or {}).get('display_name') or '管理層'
        con.execute('SAVEPOINT pr')
        try:
            if act.startswith('ent'):
                msg = '' if act == 'ent_next' else door.staff_set(con, data.get('s', ''), data.get('m', ''), uid, who) if act == 'ent_set' else door.fill_skip(con, data.get('s', ''), who)
                nxt = door.next_fill(con, skip_sid=data.get('s', '') if act == 'ent_skip' else '') if act != 'ent_set' or data.get('m') == 'home' else None
                out = (_txt(msg) if msg else []) + (nxt or ([] if act == 'ent_set' and data.get('m') != 'home' else _txt('🎉 開門方式都補好了，謝謝！')))
            elif act == 'pr_type':
                _staff_wait(con, uid, 'pr_wait', f"{data.get('b', '')}|{data.get('u', '')}")
                out = _txt('請打這個人的名字或電話（例如：王小姐、0900000000 或 678），系統幫妳找 🔎')
            else:
                msg = ('' if act == 'pr_next' else pairing.pair(con, data.get('b', ''), data.get('u', ''), data.get('k', ''), data.get('r', ''), who) if act == 'pr'
                       else pairing.skip(con, data.get('b', ''), data.get('u', ''), data.get('v', ''), who))
                nxt = ((pairing.next_customer_card(con) if data.get('f') == 'c' else pairing.next_worker_card(con) if data.get('f') == 'w'      # 從「業主／夥伴是哪個 LINE」來的 → 下一位
                        else pairing.next_card(con)) if pairing.push_on(con) else pairing.web_tip(con))                     # 不接著推下一張
                out = (_txt(msg) if msg else []) + (nxt or _txt('🎉 都配對好了，謝謝！之後新加入的會走官方 LINE 綁定流程。'))
            con.execute('RELEASE pr')
        except Exception as e:
            con.execute('ROLLBACK TO pr'); con.execute('RELEASE pr')
            out = _txt(_tap_error(con, act, e))
        return reply(con, acc, ev['replyToken'], out)
    if act in ('ent_ok', 'ent_edit'):                         # 開門流程（店長）：確認／打字修改開門方式
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        import door
        who = (lu or {}).get('display_name') or '管理層'
        if act == 'ent_ok':
            return reply(con, acc, ev['replyToken'], _txt(door.staff_ok(con, data.get('s', ''), who)))
        _staff_wait(con, uid, 'entedit_wait', data.get('s', ''))
        return reply(con, acc, ev['replyToken'], _txt('好的，請直接打字寫正確的開門方式（例如：業主在家開門／大門密碼 1234#／管理員王先生 0912…）✏️'))
    if act == 'sup':                                          # 夥伴：要買耗材
        wid = (lu or {}).get('worker_id') or (_group_worker(con, gid) if gid else None)
        if not wid:
            return reply(con, acc, ev['replyToken'], _txt('你的 LINE 還沒綁定夥伴資料，請直接留言 🙏'))
        ix.set_setting(con, f'sup_wait:{uid}', oid or '-')
        return reply(con, acc, ev['replyToken'], _txt('要買什麼、大約多少錢？請直接打字（例如：地板耗材 1 罐 約 100 元），會送經理同意 🙏'))
    if act == 'gap':                                          # 店長：同天兩場間隔短，趕得上嗎？
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        import dispatch
        who = (lu or {}).get('display_name') or '管理層'
        return reply(con, acc, ev['replyToken'], _txt(dispatch.gap_answer(con, data.get('n', ''), data.get('x', ''), data.get('v') == '1', who)))
    if act in ('sup_ok', 'sup_no', 'prep_ok', 'prep_add'):     # 店長：用品同意／不用買；案件前提醒
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        who = (lu or {}).get('display_name') or '管理層'
        if act in ('sup_ok', 'sup_no'):
            import supplies
            try:
                return reply(con, acc, ev['replyToken'], _txt(supplies.decide(con, data.get('r', ''), act == 'sup_ok', who)))
            except ValueError as e:
                return reply(con, acc, ev['replyToken'], _txt(str(e)))
        if act == 'prep_ok':
            import board; board.log(con, oid, 'prep_ok', who)
            return reply(con, acc, ev['replyToken'], _txt('👍 好的，這場照常'))
        _staff_wait(con, uid, 'prep_wait', oid)
        return reply(con, acc, ev['replyToken'], _txt('請直接打字要提醒夥伴的事（例如：現場沒有工具、垃圾裝好放一樓門口）✏️'))
    if act == 'door_req':                                     # 開門流程（夥伴）：到門口了，請業主開門
        import door
        wid = _tap_worker(con, lu, gid, uid, data, oid)
        if not wid:
            return reply(con, acc, ev['replyToken'], _txt('這張卡片要由本人按喔 🙏'))
        return reply(con, acc, ev['replyToken'], _txt(door.request_open(con, oid, wid, c.get(con, 'workers', wid)['name'])))
    if act in ('hm_ok', 'hm_fix'):                             # 多館業主：每月館別確認
        cid = (lu or {}).get('customer_id'); mon = str(data.get('m', ''))[:7]
        if not cid or not re.fullmatch(r'\d{4}-\d{2}', mon):
            return reply(con, acc, ev['replyToken'], _txt('這張卡片要由業主本人按喔 🙏'))
        os_ = c.rows(con, "SELECT * FROM orders WHERE customer_id=? AND substr(date,1,7)=? AND status IN ('review','draft','confirmed') AND date>=? ORDER BY date,start",
                     (cid, mon, dt.datetime.now(c.TZ).date().isoformat()))
        if not os_:
            return reply(con, acc, ev['replyToken'], _txt('這個月的場次已經結束或還沒排，有需要直接留言告訴我們 🙏'))
        if act == 'hm_fix':
            return reply(con, acc, ev['replyToken'], _carousel('要換哪一場的館別？', [card_house_pick(con, o['id']) for o in os_]), 'hm_fix')
        if ix.setting(con, f'hm_ok:{cid}:{mon}'):          # 稽核：按第二次不重複確認、不重複通知
            return reply(con, acc, ev['replyToken'], _txt('這個月已經確認過了，謝謝您 👍 要換館別請按「要換館別」或直接留言。'))
        n = 0
        for o in os_:
            if not o['customer_confirmed']:
                con.execute('UPDATE orders SET customer_confirmed=1,version=version+1 WHERE id=?', (o['id'],)); n += 1
            _auto_confirm(con, o['id'])
            board.log(con, o['id'], 'customer_ok', '業主確認每月館別')
        ix.set_setting(con, f'hm_ok:{cid}:{mon}', c.now())
        by_w = {}                                  # 業主確認館別 → 同步讓配合夥伴知道（每人一張自己的場次清單）
        for o in os_:
            sname = (con.execute('SELECT name FROM sites WHERE id=?', (o['site_id'],)).fetchone() or [''])[0]
            for (wid_,) in con.execute("SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)):
                by_w.setdefault(wid_, []).append(f"・{md(o['date'])}（{WD[dt.date.fromisoformat(o['date']).weekday()]}）{o['start']}–{o['end']}　{sname}")
        for wid_, lines in by_w.items():
            to_worker(con, wid_, 'house_month_w', f"{int(mon[5:])} 月館別已確認", _txt(f"📅 業主已確認 {int(mon[5:])} 月的館別，你的場次：\n" + '\n'.join(lines) + "\n\n地址以前一天的工作卡為準，有換館會再通知你 🙏"))
        admin_notify(con, f"👍 業主確認 {int(mon[5:])} 月館別", [('業主', c.get(con, 'customers', cid)['name']), ('場次', f'{len(os_)} 場'), ('夥伴', f'已通知 {len(by_w)} 位' if by_w else '還沒排夥伴'), ('處理', '不用處理')], f'/#c/{cid}', '#2E7D32')
        return reply(con, acc, ev['replyToken'], _txt(f"✅ 謝謝您！{int(mon[5:])} 月 {len(os_)} 場都確認了，服務前一天會再提醒您。"), 'hm_ok')
    if act == 'noop':
        return reply(con, acc, ev['replyToken'], _txt('好的 👍 有需要隨時按下方選單「線上估價」或直接留言給我們。'))
    if act == 'view':                                         # 管理選單：客戶視角／夥伴視角
        if uid not in staff_users(con) or gid:
            return reply(con, acc, ev['replyToken'], _txt('切換視角是管理層在官方 LINE 一對一用的。'))
        return reply(con, acc, ev['replyToken'], view_as(con, acc, uid, 'worker' if data.get('v') == 'worker' else 'customer'))
    if act and act.startswith('m_'):
        return _staff_menu(con, acc, uid, lu, act, ev, data)
    if act == 'c_flow':
        steps = [('1. 線上估價', '回答幾個問題、拍幾張照片'), ('2. 主管確認報價', '正式報價單傳到這裡'), ('3. 線上簽收', '手機簽名就完成'),
                 ('4. 安排人員', '確定人員會通知您，前一天再提醒'), ('5. 完工回報', '服務前後照片傳給您確認')]
        return reply(con, acc, ev['replyToken'], flex('服務流程', _bubble(_header('服務流程', brands.name(acc), brand=acc),
            sum([[_t(a, weight='bold', margin='md'), _t(b, color=SUB, size='xs', wrap=True)] for a, b in steps], []),
            [_btn('開始線上估價', uri=book_url(con, acc, uid), primary=True)])), 'menu')
    if act in ('mt', 'mtp', 'mtin'):                          # 大會：報名時段、大合照入鏡、當天簽到
        import meetup
        if act == 'mt':
            t_, ask_ = meetup.answer(con, acc, uid, lu, data.get('m', ''), data.get('s', ''))
            msgs_ = _txt(t_) + (meetup.photo_card(con, meetup.get(con)) if ask_ else [])
            return reply(con, acc, ev['replyToken'], msgs_)
        if act == 'mtp':
            return reply(con, acc, ev['replyToken'], _txt(meetup.photo(con, acc, uid, lu, data.get('m', ''), data.get('v', ''))))
        return reply(con, acc, ev['replyToken'], _txt(meetup.checkin(con, acc, uid, lu, data.get('m', ''))))
    if act == 'qlink':                                        # 報價成立：沿用原本工單的人員／兩張都要
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import quotelink
        c.CTX.actor = ((lu or {}).get('display_name') or '管理層') + '（派單管理群）'
        return reply(con, acc, ev['replyToken'], _txt(quotelink.decide(con, oid, data.get('x', ''), data.get('v', ''), (lu or {}).get('display_name') or '管理層')))
    if act == 'wgone':                                        # 離開：暫停並另外找人／先保留
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import departure
        c.CTX.actor = ((lu or {}).get('display_name') or '管理層') + '（派單管理群）'
        return reply(con, acc, ev['replyToken'], _txt(departure.decide(con, data.get('w', ''), data.get('v', ''), (lu or {}).get('display_name') or '管理層')))
    if act == 'mtf':                                          # 夥伴大會：管理群定時間
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import meetup
        return reply(con, acc, ev['replyToken'], _txt(meetup.finalize(con, data.get('m', ''), data.get('s', '0'), (lu or {}).get('display_name') or '管理層')))
    if act == 'c_book':                                       # 選單「線上估價」：綁定的業主資料自動帶入
        return reply(con, acc, ev['replyToken'], card_book(con, acc, uid), 'menu')
    if act == 'c_human' and (lu or {}).get('customer_id'):
        import urgent
        o_ = urgent.active_order(con, lu['customer_id'])
        if o_:                                                # 服務進行中按「真人客服」→ 緊急回報卡
            return reply(con, acc, ev['replyToken'], urgent.customer_card(con, o_))
    if act in ('c_my', 'c_services', 'c_human', 'c_other', 'c_flow', 'bind', 'c_pay', 'c_chg_menu') and (lu or {}).get('worker_id') and acc in APPLY_ACCOUNTS \
            and (con.execute('SELECT status FROM workers WHERE id=?', (lu['worker_id'],)).fetchone() or [''])[0] in ('active', 'review'):      # 稽核：被婉拒（暫停）的不要再掛回夥伴選單
        worker_menu_link(con, uid, acc)            # 夥伴不給客戶介面
        return reply(con, acc, ev['replyToken'], _txt(f'你是{brands.name(acc)}夥伴，下方已換成夥伴選單（今天的工作、班表、可排時間）👍 如果還沒變，關掉聊天室再打開一次。'))
    if act in ('c_my', 'c_services', 'c_human', 'c_other', 'c_chg', 'c_resch', 'c_cancel', 'c_cancel_ok', 'c_pay', 'c_chg_menu', 'c_photos'):     # 客戶圖文選單＋我的預約的改期／取消（加付款、改期・取消、過去的完成照片）
        return _customer_menu(con, acc, uid, lu, act, data, ev)
    if act == 'bind':                 # 群發訊息上的「確認我的資料」
        if (lu or {}).get('worker_id'):
            return reply(con, acc, ev['replyToken'], _txt(f"{c.get(con, 'workers', lu['worker_id'])['name']}，你是{brands.name(acc)}的夥伴，已經綁定好了 👍"))
        if (lu or {}).get('customer_id'):
            return reply(con, acc, ev['replyToken'], _txt(f"已經綁定好了：{c.get(con, 'customers', lu['customer_id'])['name']} 👍"))
        return bind_prompt(con, acc, uid, lu, ev['replyToken'])
    if act in ('bind_ok', 'bind_no'):
        cid = (lu or {}).get('suggest_cid')
        if act == 'bind_no' or not cid:
            con.execute("UPDATE line_users SET suggest_cid='' WHERE account=? AND user_id=?", (acc, uid))
            import worker_portal
            return reply(con, acc, ev['replyToken'], flex('請留一下基本資料', _bubble(_header('沒關係 🙏', '請留一下基本資料', brand=acc),
                [_t('留下稱呼和電話，老客戶用電話就能自動找到您的資料。', wrap=True)],
                [_btn('留基本資料', uri=worker_portal.onboard_token(con, acc, uid, 'hi'), primary=True)])), 'bind')
        if con.execute('SELECT 1 FROM line_users WHERE customer_id=? AND account=? AND user_id!=?', (cid, acc, uid)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('這份資料已經被其他 LINE 綁定了，專人會跟您確認 🙏'))
        if con.execute("SELECT 1 FROM bind_requests WHERE account=? AND user_id=? AND kind='customer' AND status='open'", (acc, uid)).fetchone():      # 稽核 H2：留了電話在等管理層確認 → 舊卡「是我」不能繞過
            return reply(con, acc, ev['replyToken'], _txt('收到 🙏 您的資料專人確認中，完成會在這裡通知您。'))
        if len(re.sub(r'\D', '', c.get(con, 'customers', cid)['phone'] or '')) >= 8:      # 名稱可以改，要再確認電話末四碼才是本人
            con.execute("UPDATE line_users SET onboard='verify' WHERE account=? AND user_id=?", (acc, uid))
            return reply(con, acc, ev['replyToken'], _txt('為了確認是本人，請回覆您登記電話的「末四碼」（例如 5678）🙏'))
        request_bind(con, acc, uid, 'customer', cid, (lu or {}).get('display_name') or '', '（舊資料沒有電話）', (lu or {}).get('display_name') or '')
        return reply(con, acc, ev['replyToken'], _txt('收到 🙏 專人確認後就會幫您綁定，完成會在這裡通知您。'))
    if act in ('lv', 'lv2'):                                  # 夥伴：臨時請假（選場次 → 選原因）
        return _leave_tap(con, acc, uid, lu, gid, act, data, ev)
    if act == 'urg_add':                                      # 緊急加派一位（管理群卡、業主緊急回報卡）
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import urgent
        c.CTX.actor = ((lu or {}).get('display_name') or '管理層') + '（派單管理群）'
        try:
            return reply(con, acc, ev['replyToken'], _txt(urgent.add_one(con, oid, (lu or {}).get('display_name') or '管理層')))
        except ValueError as e:
            return reply(con, acc, ev['replyToken'], _txt(str(e)))
    if act == 'urg':                                          # 業主服務時間內緊急回報
        import urgent
        return reply(con, acc, ev['replyToken'], _txt(urgent.customer_report(con, acc, uid, lu, oid, data.get('k', ''))))
    if act in ('rs_ok', 'rs_no'):                             # 業主申請改時間：改到新時間／這時間不行（打字回業主）
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import reschedule
        who = (lu or {}).get('display_name') or '管理層'
        if act == 'rs_no':
            _staff_wait(con, uid, 'rs_wait', data.get('r', ''))
            return reply(con, acc, ev['replyToken'], _txt('好的，請直接打字：要跟業主說的話（例如「那天滿了，24、25 號下午可以」）✏️ 下一則會連同「再選一個時間」傳給業主。'))
        c.CTX.actor = who + '（派單管理群）'
        c.CTX.no_urgent_card = data.get('add') == '1'
        try:
            msg_ = reschedule.approve(con, data.get('r', ''), who)
        finally:
            c.CTX.no_urgent_card = False
        if data.get('add') == '1' and msg_.startswith('✅'):         # 改時間＋緊急加派一位
            import urgent
            r_ = reschedule._get(con, data.get('r', ''))
            try:
                msg_ += '\n' + urgent.add_one(con, r_['order_id'], who, '業主改時間')
            except ValueError as e:
                msg_ += f'\n（沒加派：{e}）'
        return reply(con, acc, ev['replyToken'], _txt(msg_))
    if act == 'crew_cutm':                                    # 減少人數 → 選拿掉誰
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        o0 = c.get(con, 'orders', oid)
        crew0 = c.rows(con, "SELECT w.id,w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (oid,))
        if not crew0 or (o0['required_workers'] or 1) <= 1:
            return reply(con, acc, ev['replyToken'], _txt('這場沒辦法再減少人數了。'))
        items = [{'type': 'action', 'action': {'type': 'postback', 'label': f"拿掉 {w['name']}"[:20], 'data': f"a=crew_cut&o={oid}&w={w['id']}&n={(o0['required_workers'] or 1) - 1}", 'displayText': f"拿掉 {w['name']}，改成 {(o0['required_workers'] or 1) - 1} 位"}} for w in crew0[:12]]
        return reply(con, acc, ev['replyToken'], [{'type': 'text', 'text': f"{when(dict(o0))} 改成 {(o0['required_workers'] or 1) - 1} 位：要拿掉誰？（拿掉的不會另外補人，系統會通知他不用前往）", 'quickReply': {'items': items}}])
    if act in ('lvd', 'lvh', 'crew_ok', 'crew_swap', 'crew_cut'):         # 派單管理群：請假判斷／處理、派員審核、減少人數
        if uid not in staff_users(con):          # 管理按鈕只認管理層（負責人 ）
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import leave
        who = (lu or {}).get('display_name') or '管理層'
        c.CTX.actor = who + '（派單管理群）'
        con.execute('SAVEPOINT lv')
        try:
            msg = (leave.decide(con, data.get('l', ''), data.get('k', ''), who) if act == 'lvd' else
                   leave.handle(con, data.get('l', ''), data.get('h', ''), who) if act == 'lvh' else
                   leave.crew_approve(con, oid, who) if act == 'crew_ok' else
                   leave.crew_reduce(con, oid, data.get('w', ''), who, data.get('n'), boss=uid in deciders(con) or uid == OWNER_UID) if act == 'crew_cut' else leave.crew_swap(con, oid, data.get('w', ''), who))
            con.execute('RELEASE lv')
        except Exception as e:
            con.execute('ROLLBACK TO lv'); con.execute('RELEASE lv')
            msg = _tap_error(con, act, e)
        return reply(con, acc, ev['replyToken'], _txt(msg))
    if act == 'cw':                                           # 業主：原人員請假後的選擇（另外派人／改期／等原人員）
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order or ((lu or {}).get('customer_id') != order['customer_id'] and uid not in test_users(con)):
            return reply(con, acc, ev['replyToken'], _txt('收到，專人會跟您確認 🙏'))
        last_h = con.execute("SELECT MAX(id) FROM dispatch_log WHERE order_id=? AND action='leave_handle'", (oid,)).fetchone()[0] or 0
        if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='customer_leave_choice' AND id>?", (oid, last_h)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('已經記下您的選擇了，專人會跟您確認 🙏'))      # 稽核：舊卡再按不重複處理
        if data.get('v') == 'resch':
            import board as _bd3; _bd3.log(con, oid, 'customer_leave_choice', 'resch')          # 稽核 B2：記下業主選了改期（不然 6 小時後系統又自動另外派人）
            return _customer_menu(con, acc, uid, lu, 'c_chg', {'o': oid}, ev)
        import leave
        con.execute('SAVEPOINT cw')
        try:
            msg = leave.customer_choice(con, oid, data.get('v', ''), (lu or {}).get('display_name') or '業主'); con.execute('RELEASE cw')
        except Exception as e:
            con.execute('ROLLBACK TO cw'); con.execute('RELEASE cw'); _tap_error(con, act, e)
            admin_notify(con, '⚠️ 業主選了人員異動方式，但系統沒處理成功', [('工作', when(dict(order))), ('業主選', {'other': '請另外派人', 'wait': '等原本的人員'}.get(data.get('v'), data.get('v', ''))),
                                                         ('處理', '請到中控台手動處理並回覆業主')], '/#dispatch', '#C62828', oid=oid)
            msg = '收到 🙏 專人會馬上跟您確認安排。'
        return reply(con, acc, ev['replyToken'], _txt(msg))
    if act == 'wdup':                                         # 夥伴去重：管理群確認是不是同一人
        if uid not in staff_users(con):          # 管理按鈕只認管理層（負責人 ）
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        return reply(con, acc, ev['replyToken'], _txt(worker_dup_decide(con, data.get('k', ''), data.get('d', ''), data.get('y') == '1', (lu or {}).get('display_name') or '管理層')))
    if act in ('c_pd', 'c_pd_del', 'c_pd_ok'):                 # 客戶：查詢／申請刪除個資（個資法當事人權利）
        import privacy
        cid = (lu or {}).get('customer_id')
        if not cid:
            return reply(con, acc, ev['replyToken'], _txt('您的 LINE 目前沒有對應的客戶資料 🙏 有任何個資問題請直接留言。'))
        if act == 'c_pd':
            return reply(con, acc, ev['replyToken'], [{'type': 'text', 'text': '這是我們目前存的您的資料：\n\n' + privacy.summary(con, cid)}] + flex('個資查詢／刪除', _bubble(
                _header('個資查詢／刪除', brands.name(acc), brand=acc), [_t('要更正請按「修改我的資料」；要刪除請按下面。依稅法須保存的帳務資料，會去除可識別您的部分後保存。', wrap=True, size='sm')],
                [_btn('修改我的資料', uri=f"{BASE_URL}/site?t={__import__('worker_portal').cust_token(con, acc, uid, cid)}"), _btn('申請刪除我的個資', data='a=c_pd_del'),
                 _btn('看個資告知', uri=f'{BASE_URL}/privacy?b={acc}')])))
        if act == 'c_pd_del':
            b = privacy.blockers(con, cid)
            return reply(con, acc, ev['replyToken'], flex('確定要刪除個資嗎？', _bubble(_header('確定要刪除個資嗎？', '刪除後無法復原', '#C62828', brand=acc),
                [_t('刪除後我們將無法查到您的預約紀錄、地址與聯絡方式，下次預約需要重新填寫。' + (f"\n\n⚠️ 目前{'、'.join(b)}，需要先處理完才能刪除，專人會跟您聯繫。" if b else ''), wrap=True)],
                [_btn('確定申請刪除', data='a=c_pd_ok', primary=True)])))
        rid, new = privacy.request(con, acc, uid, cid)
        if new:
            import reviews
            cu = c.get(con, 'customers', cid); b = privacy.blockers(con, cid)
            m = flex('客戶申請刪除個資', _bubble(_header('🔒 客戶申請刪除個資', cu['name'], '#C62828'),
                [_row('客戶', cu['name']), _row('品牌', brands.name(acc)), _row('狀況', '、'.join(b) if b else '沒有進行中的預約或欠款'),
                 _t('核准後系統會去識別化（姓名、電話、地址、LINE 綁定、對話、報價單個資清除；帳務金額保留）。原則上 30 天內處理。', wrap=True, size='xs', color=SUB, margin='md')],
                [_btn('核准刪除', data=f'a=pd_ok&r={rid}', primary=True), _btn('先不要（我聯絡客戶）', data=f'a=pd_no&r={rid}')]))
            to_managers(con, 'privacy', m[0]['altText'], m)          # 送管理群 @店長
        return reply(con, acc, ev['replyToken'], _txt('已收到您的刪除申請 🙏 專人確認後會盡快處理（原則上 30 天內），完成時會在這裡通知您。'))
    if act in ('pd_ok', 'pd_no'):                             # 店長：核准／暫緩客戶的個資刪除
        import privacy
        if uid not in money_users(con):
            return reply(con, acc, ev['replyToken'], _txt('個資刪除只有店長（和負責人）可以核准。'))
        return reply(con, acc, ev['replyToken'], _txt(privacy_decide(con, data.get('r', ''), act == 'pd_ok', (lu or {}).get('display_name') or '管理層')))      # 稽核：刪完才排通知
    if act == 'g_demo':                                       # 夥伴介紹卡上的示範按鈕
        return reply(con, acc, ev['replyToken'], _txt(GUIDE_DEMO.get(data.get('s', ''), '👍 這是示範按鈕。')))
    if act in ('g_ok', 'g_edit'):                             # 管理層：介紹 OK 發給所有夥伴／要修改
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        who = (lu or {}).get('display_name') or '管理層'
        if act == 'g_edit':
            _staff_wait(con, uid, 'guide_wait', '1')
            return reply(con, acc, ev['replyToken'], _txt('好的，請直接打字說哪裡要改 ✏️（下一則訊息會記下來，改好再請你們看）'))
        c.CTX.actor = who
        return reply(con, acc, ev['replyToken'], _txt(send_guide(con, who)))
    if act in ('srep_menu', 'srep'):                          # 夥伴：現場回報（加時、下一場晚到、現場狀況）→ 派單管理群可點的卡
        import site_report
        wid = _tap_worker(con, lu, gid, uid, data, oid) if oid else ((lu or {}).get('worker_id') or (_group_worker(con, gid) if gid else None))
        if not wid:
            return reply(con, acc, ev['replyToken'], _txt('這張卡片要由本人按喔 🙏（這場已取消或你不在這場，也不能回報）'))
        if act == 'srep_menu' or data.get('k') not in site_report.KINDS:
            return reply(con, acc, ev['replyToken'], site_report.start_card(con, wid, oid or None))
        msgs, ready = site_report.options(con, wid, oid, data['k'], data)
        if not ready:
            if data['k'] in site_report.NOTE_KINDS:
                ix.set_setting(con, f'srep_wait:{uid}', f"{oid}|{data['k']}|{wid}")
            return reply(con, acc, ev['replyToken'], msgs)
        try:
            r = site_report.create(con, oid, wid, data['k'], int(data.get('m') or 0), data.get('own', ''), '', (lu or {}).get('display_name') or '')
        except ValueError as e:
            return reply(con, acc, ev['replyToken'], _txt(str(e)))
        return reply(con, acc, ev['replyToken'], _txt(r['message']))
    if act == 'cmsg_ok':                                      # 管理層：客戶留言已回覆（停止提醒）
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        a2, _, u2 = data.get('u', '').partition('|')
        return reply(con, acc, ev['replyToken'], _txt(cmsg_done(con, a2, u2, (lu or {}).get('display_name') or '管理層')))
    if act == 'door_in':                                      # 夥伴：已經進門了（業主開了／自己進去了）
        import door
        wid = _tap_worker(con, lu, gid, uid, data, oid)
        if not wid:
            return reply(con, acc, ev['replyToken'], _txt('這張卡片要由本人按喔 🙏'))
        return reply(con, acc, ev['replyToken'], _txt(door.worker_inside(con, oid, c.get(con, 'workers', wid)['name'])))
    if act == 'srx':                                          # 管理層：處理現場回報（同意加時／通知業主／改天補／已處理）
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import site_report
        who = (lu or {}).get('display_name') or '管理層'; c.CTX.actor = who
        con.execute('SAVEPOINT srx')
        try:
            msg = site_report.act(con, data.get('r', ''), data.get('v', ''), who); con.execute('RELEASE srx')
        except Exception as e:          # 舊卡片、工單已完工等：講原因，不要沒回應
            con.execute('ROLLBACK TO srx'); con.execute('RELEASE srx'); msg = _tap_error(con, act, e)
        return reply(con, acc, ev['replyToken'], _txt(msg))
    if act == 'weta':                                         # 夥伴：已出門／快到了（首頁即時狀況、快到了先通知業主開門）
        import automation
        wid = _tap_worker(con, lu, gid, uid, data, oid)
        if not wid:
            return reply(con, acc, ev['replyToken'], _txt('這張卡片要由本人按喔 🙏'))
        return reply(con, acc, ev['replyToken'], _txt(automation.eta_tap(con, oid, wid, data.get('v', ''), c.get(con, 'workers', wid)['name'])))
    if act == 'wlate':                                        # 夥伴：會晚到 → 告訴業主＋管理群（晚到時間可以更新，例如 15 → 30 分）
        import automation
        wid = _tap_worker(con, lu, gid, uid, data, oid)
        if not wid:
            return reply(con, acc, ev['replyToken'], _txt('這張卡片要由本人按喔 🙏'))
        if con.execute("SELECT 1 FROM assignments a JOIN checkins k ON k.assignment_id=a.id WHERE a.order_id=? AND a.worker_id=? AND k.arrived_at!=''", (oid, wid)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('你已經打到場卡了，不用再通知晚到 👍'))
        if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='worker_late' AND detail=?", (oid, f"{c.get(con, 'workers', wid)['name']} 約晚 {int(data.get('m') or 15)} 分")).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('已經告訴業主了，路上小心 🙏'))
        return reply(con, acc, ev['replyToken'], _txt(automation.late_tap(con, oid, wid, int(data.get('m') or 15), c.get(con, 'workers', wid)['name'])))
    if act in ('pq_next', 'pq_reviews'):                      # 待處理清單：一題一題回答／看審核
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        import pending
        if act == 'pq_next':
            nq = pending.next_question(con)
            return reply(con, acc, ev['replyToken'], [nq] if nq else _txt('🎉 題目都回答完了，謝謝！'))
        rc = pending.review_cards(con)
        return reply(con, acc, ev['replyToken'], rc if rc else _txt('🎉 沒有待審核的了'))
    if act == 'magic':                                        # 中控台一次性登入連結（只寄給綁定的本人）
        import accounts
        u = ix.setting(con, f'login_user:{uid}')
        if not u:
            return reply(con, acc, ev['replyToken'], _txt('這個 LINE 沒有對應的中控台帳號，請找管理員開。'))
        return reply(con, acc, ev['replyToken'], card_magic(con, u))
    if act == 'qa_type':                                      # 店長在 LINE 打金額報價
        if uid not in money_users(con):
            return reply(con, acc, ev['replyToken'], _txt('報價只有店長（和負責人）可以確認。'))
        _staff_wait(con, uid, 'qa_wait', data.get('q', ''))
        return reply(con, acc, ev['replyToken'], _txt('請直接打報價，每行一項「品項 金額」（未稅），例如：\n年度保養 8000\n冷氣濾網 400\n打完我會整理成報價卡給妳再確認一次才傳給客戶 ✏️'))
    if act in ('qa_ok', 'qa_site', 'qa_manual', 'qa_cancel'):  # 店長在 LINE 確認報價／改現場估價／已用其他方式給客戶／取消（逾時卡）
        if uid not in money_users(con):
            return reply(con, acc, ev['replyToken'], _txt('報價只有店長（和負責人）可以確認。'))
        import quickquote
        who = (lu or {}).get('display_name') or '管理層'
        try:
            fn = {'qa_ok': quickquote.approve, 'qa_site': quickquote.site_visit, 'qa_manual': quickquote.manual_sent, 'qa_cancel': quickquote.cancel}[act]
            msg = fn(con, data.get('q', ''), who)
            if act == 'qa_manual' and msg.startswith('✅'):            # 接著問為什麼用其他方式（→ 功能回報 → 下次全自動）
                return reply(con, acc, ev['replyToken'], _txt(msg) + [quickquote.reason_ask(data.get('q', ''))])
        except ValueError as e:
            msg = f'沒辦法處理：{e}'
        return reply(con, acc, ev['replyToken'], _txt(msg))
    if act == 'fb_ok':                                        # 夥伴忘了拍服務前照片 → 管理層確認後這場薪資照常發
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        oid = data.get('o', '')
        if not con.execute('SELECT 1 FROM orders WHERE id=?', (oid,)).fetchone():      # 稽核 H3：補登、完工登記的單也能放行（不必先寫忘了拍）
            return reply(con, acc, ev['replyToken'], _txt('找不到這筆。'))
        if ix.setting(con, f'photo_ok:{oid}'):
            return reply(con, acc, ev['replyToken'], _txt('已經確認過了 ✅'))
        return reply(con, acc, ev['replyToken'], _txt(photo_release(con, oid, (lu or {}).get('display_name') or '管理層')))
    if act == 'ask_type':                                     # 點選題：「我打字回答」→ 下一則文字就是答案
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕。'))
        _staff_wait(con, uid, 'ask_wait', data.get('t', ''))
        return reply(con, acc, ev['replyToken'], _txt(f"好的，請直接打字回答【{data.get('t', '')}】✏️（下一則訊息就是答案）"))
    if act == 'tri':                                          # 待整理：店長在 LINE 點選決定
        return _triage_tap(con, acc, uid, data, ev)
    if act in ('rv_ok', 'rv_edit', 'rv_auto'):                # 店長審核（公告、改價、薪資⋯）
        return _review_tap(con, acc, uid, act, data, ev)
    if act == 'pay_ok':                                       # 確認收款（只有負責人、店長）
        return _pay_ok(con, acc, uid, data, ev)
    if act in ('br_ok', 'br_no'):                             # 派單管理群：確認／拒絕綁定申請（中控台同步）
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('只有管理層（負責人、店長、客服）可以確認。'))
        try:
            return reply(con, acc, ev['replyToken'], _txt(decide_bind(con, data.get('r', ''), act == 'br_ok', (lu or {}).get('display_name') or 'LINE 群組')))
        except ValueError as e:
            return reply(con, acc, ev['replyToken'], _txt(str(e)))
    if act == 'approve_bind' or act == 'approve_wbind':      # 舊版卡片：同意電話相符的綁定申請
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('只有管理層（負責人、店長、客服）可以確認。'))
        tu, ref = data.get('u', ''), data.get('c', '')
        col = 'customer_id' if act == 'approve_bind' else 'worker_id'
        if con.execute(f'SELECT 1 FROM line_users WHERE {col}=? AND user_id!=? AND account=?', (ref, tu, data.get('acc', acc))).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('這份資料已經綁定其他 LINE 了，請先確認。'))
        con.execute(f"UPDATE line_users SET {col}=?,suggest_cid='' WHERE account=? AND user_id=?", (ref, data.get('acc', acc), tu))
        if col == 'worker_id':
            worker_menu_link(con, tu, data.get('acc', acc))
        c.audit(con, 'line_bind_approved', 'customers' if col == 'customer_id' else 'workers', ref, after={'line_user': tu, 'by': (lu or {}).get('display_name')})
        name = c.get(con, 'customers' if col == 'customer_id' else 'workers', ref)['name']
        if col == 'customer_id':
            m = card_next_step(con, data.get('acc', acc), tu, ref); queue(con, data.get('acc', acc), tu, 'hello', m[0]['altText'], m)
        return reply(con, acc, ev['replyToken'], _txt(f'✅ 已綁定：{name}'))
    if act in ('grp_ok', 'grp_no'):
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('只有管理層（負責人、店長、客服）可以確認。'))
        return reply(con, acc, ev['replyToken'], _txt(group_decide(con, data.get('g', ''), act == 'grp_ok')))

    if act == 'wdone':                                        # 夥伴：照排班時間完成（自動完工登記）
        import automation
        gw = _group_worker(con, gid) if gid else None
        if gw and gw == data.get('w') and lu and not lu.get('is_staff') and not lu.get('worker_id'):     # 在他的個人群組按：這次當他按，綁定請管理群確認（資安）
            _group_bind_request(con, acc, uid, gw, lu); lu = {**lu, 'worker_id': gw}
        if ((lu or {}).get('worker_id') != data.get('w') or not data.get('w')) and uid not in staff_users(con):      # 本人，或管理層電話問過後在管理群代按
            return reply(con, acc, ev['replyToken'], _txt('這張卡片要由本人按喔 🙏'))
        return reply(con, acc, ev['replyToken'], _txt(automation.worker_done(con, oid, data['w'], c.get(con, 'workers', data['w'])['name'])))
    if act == 'care':                                         # 客戶：完工關心卡的回覆
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order or ((lu or {}).get('customer_id') != order['customer_id'] and uid not in test_users(con)):
            return reply(con, acc, ev['replyToken'], _txt('謝謝您 🙏'))
        import board
        board.log(con, oid, 'care_reply', data.get('v', ''))
        if data.get('v') == 'fix':
            admin_notify(con, '💬 客戶關心卡：有地方想建議', [('工作', when(dict(order))), ('地點', order['title']), ('處理', '客戶接下來打的字會在官方 LINE，請主動關心')], '/#home', '#C47F00', oid=oid)
            return reply(con, acc, ev['replyToken'], _txt('謝謝您願意告訴我們 🙏 請直接在這裡打字說明哪裡需要加強，主管會親自跟您聯繫。'))
        return reply(con, acc, ev['replyToken'], [rating_ask(oid)] if not order['rating'] else _txt('謝謝您 🥰 期待下次再為您服務！'))
    if act in ('wbl', 'wr_ok', 'wr_no'):                      # 管理層：黑名單（店長、客服都可以）、找回舊夥伴
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層專用功能。'))
        import automation
        who = (lu or {}).get('display_name') or '管理層'
        w = con.execute('SELECT * FROM workers WHERE id=?', (data.get('w', ''),)).fetchone()
        if not w:
            return reply(con, acc, ev['replyToken'], _txt('找不到這位夥伴。'))
        c.CTX.actor = who + '（LINE 管理選單）'
        if act == 'wbl':
            on = data.get('on') == '1'
            automation.set_blacklist(con, w['id'], on, who)
            return reply(con, acc, ev['replyToken'], _txt(f"{'🚫 已把 ' + w['name'] + ' 列入黑名單：之後不會自動詢問、也不能接單（邀請中的已收回）。' if on else '✅ 已把 ' + w['name'] + ' 移出黑名單。'}"))
        st = 'active' if act == 'wr_ok' else 'paused'
        if w['status'] == st:
            return reply(con, acc, ev['replyToken'], _txt(f"{w['name']} 已經是「{'可派工' if st == 'active' else '暫停'}」了。"))
        if st == 'active':
            import recruit
            iv = recruit.open_stage(con, w['id'])
            if iv:
                return reply(con, acc, ev['replyToken'], _txt(f"{w['name']} 還在招募流程（目前：{recruit.stage_name(iv)}），不能直接恢復可派工。\n請到中控台「夥伴招募」一關一關完成（面試→良民證→實作→訓練），到「試派案」系統會自動開放派工。"))
        if w['status'] == 'active' and st != 'active':
            import leave; moved, kept = leave.worker_unavailable(con, w['id'], '已暫停')
            if kept:
                return reply(con, acc, ev['replyToken'], _txt(f"{w['name']} 還有 {kept} 場已排定（地址已給他）的工作，先不能暫停。\n派單管理群剛收到一張卡：按「換掉」把這幾場換人後，再按一次暫停。"))
        c.save(con, 'workers', {'id': w['id'], 'version': w['version'], 'status': st})
        return reply(con, acc, ev['replyToken'], _txt(f"✅ {w['name']} 已恢復為可派工" + ('（他綁了官方 LINE 就會收到歡迎卡和派工）' if not con.execute('SELECT 1 FROM line_users WHERE worker_id=? AND is_friend=1', (w['id'],)).fetchone() else '')
                                                   if st == 'active' else f"已把 {w['name']} 改成暫停（不再派工）。"))
    if act == 'rate':
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order or ((lu or {}).get('customer_id') != order['customer_id'] and uid not in test_users(con)):
            return reply(con, acc, ev['replyToken'], _txt('謝謝您 🙏'))
        stars = max(1, min(5, int(data.get('s') or 5)))
        if order['rating']:
            return reply(con, acc, ev['replyToken'], _txt('您已經評過分了，謝謝您 🙏'))
        con.execute('UPDATE orders SET rating=?,rating_at=? WHERE id=?', (stars, c.now(), oid))
        import board, booking
        board.log(con, oid, 'customer_rating', str(stars))
        if stars <= 3:
            admin_notify(con, f"⚠️ 客戶給了 {'⭐' * stars}", [('工作', when(dict(order))), ('地點', order['title']), ('處理', '請盡快在官方 LINE 關心客戶')], '/#home', '#C62828', oid=oid)
            import notify
            notify.staff(con, f'客戶評價 {stars} 星', f"{order['date']} {order['title']}", '/#home')
            return reply(con, acc, ev['replyToken'], _txt('謝謝您的評價，也很抱歉這次沒有做到最好 🙏 可以直接在這裡告訴我們哪裡需要改進嗎？主管會親自跟您聯繫。'))
        import testimonials
        tid = testimonials.on_rating(con, oid, stars, acc)          # 滿意 → 派單管理群（附服務前後照片），我們到官方 LINE 親自回覆謝謝
        url = booking.company(con, acc).get('review_url', '')
        btns = [_btn('寫幾句話給我們 ✍️', data=f'a=tm_w&t={tid}', primary=True)] + ([_btn('到 Google 寫評論', uri=url)] if url.startswith('http') else [])
        ps = photo_share_ask(con, oid)          # 行銷：前後照片要客戶授權才能分享（店長核准文字後才問）
        if ps:
            btns += [_btn('同意分享前後照片（不具名）', data=f'a=psh&o={oid}&v=1'), _btn('照片不要分享', data=f'a=psh&o={oid}&v=0')]
        return reply(con, acc, ev['replyToken'], flex('謝謝您的好評！', _bubble(_header('謝謝您的好評 🥰', '⭐' * stars, brand=brand_of({'line_account': acc})),
            [_t('想對這次的服務、或對服務人員說幾句話嗎？一兩句就好，我們會轉達給夥伴 😊' + (f'\n也歡迎花 30 秒到 Google 幫{brands.name(acc)}留個評論，對我們幫助很大！' if url.startswith('http') else ''), wrap=True)]
            + ([_t(PHOTO_SHARE_Q.format(brand=brands.name(acc)), wrap=True, size='sm', color=SUB, margin='md')] if ps else []),
            btns)))
    if act == 'psh':                  # 行銷：客戶同不同意不具名分享這次的前後照片
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order or ((lu or {}).get('customer_id') != order['customer_id'] and uid not in test_users(con)):
            return reply(con, acc, ev['replyToken'], _txt('謝謝您 🙏'))
        ok = data.get('v') == '1'
        ix.set_setting(con, f'photo_share:{oid}', json.dumps({'ok': ok, 'at': c.now(), 'brand': acc}, ensure_ascii=False))
        c.audit(con, 'photo_share', 'orders', oid, after={'ok': ok})
        return reply(con, acc, ev['replyToken'], _txt('謝謝您願意分享 🙏 照片會遮掉門牌、人臉等可辨識的資訊，不會寫您的名字；想收回隨時在這裡告訴我們。' if ok
                                                       else '好的，這次的照片不會分享，只會留在您的服務紀錄裡 🙏'))
    if act == 'qfb':                  # 客戶對報價有疑問（預算／分次／通話）→ 派單管理群一張卡（打開報價調整）
        import booking
        try:
            q = booking._q(con, qid=data.get('q', ''))
        except ValueError:
            return reply(con, acc, ev['replyToken'], _txt('收到 🙏 專人會在這裡跟您聯絡。'))
        why = {'budget': '價格超出預算，想調整', 'split': '想分次做／先做重點區域', 'call': '想跟專人通話'}.get(data.get('r'), '對報價有疑問')
        if q['status'] != 'draft':           # 稽核 F6：已成立／已取消的舊卡
            return reply(con, acc, ev['replyToken'], _txt('這張報價已經處理好了 🙏 有其他問題直接在這裡留言。'))
        f = json.loads(q['form'] or '{}'); f['feedback'] = {'reason': why, 'at': c.now()}
        con.execute("UPDATE quotes SET form=?,stage=CASE WHEN stage='sent' THEN 'review' ELSE stage END WHERE id=?", (json.dumps(f, ensure_ascii=False), q['id']))      # 退回待主管確認：照常追蹤、不再催客戶簽
        if not con.execute("SELECT 1 FROM line_outbox WHERE kind='quote_fb' AND alt LIKE ? AND created_at>?", (f"%{q['id']}%", (dt.datetime.now(c.TZ) - dt.timedelta(hours=6)).isoformat())).fetchone():
            m = flex(f"💬 客戶對報價有疑問：{q['id']}", _bubble(_header('客戶對報價有疑問 💬', f.get('company') or f.get('name') or q['id'], '#0A84FF'),
                     [_row('報價', f"{q['id']}（NT$ {q['total'] // 100:,}）"), _row('客戶說', why), _row('電話', f.get('phone') or '—'), _row('處理', '打開報價調整範圍／金額後再傳一次；要通話請直接打電話')],
                     [_btn('打開報價調整', uri=f"{BASE_URL}/#q/{q['id']}", primary=True)]))
            to_managers(con, 'quote_fb', m[0]['altText'], m)
        return reply(con, acc, ev['replyToken'], _txt({'call': '收到 🙏 專人會盡快打電話給您（也可以直接在這裡留言方便的時間）。'}.get(data.get('r'), '收到 🙏 專人會幫您調整方案，調整好會在這裡傳給您；也可以直接留言預算或想先做的區域。')))
    if act in ('shop', 'shop_add', 'shop_co', 'shop_dl', 'shop_clear', 'shop_my'):     # 示範商城：點選購物
        import shop
        if act == 'shop':
            return reply(con, acc, ev['replyToken'], shop.menu(con, acc, lu, uid))
        if act == 'shop_my':
            return reply(con, acc, ev['replyToken'], shop.my_orders(con, uid, acc))
        if act == 'shop_add':
            return reply(con, acc, ev['replyToken'], shop.add(con, uid, acc, lu, data.get('p', ''), int(data.get('q') or 1)))
        if act == 'shop_co':
            return reply(con, acc, ev['replyToken'], shop.checkout(con, uid, acc, lu))
        if act == 'shop_clear':
            shop.clear(con, uid, acc); return reply(con, acc, ev['replyToken'], _txt('購物車已清空 👌'))
        return reply(con, acc, ev['replyToken'], shop.place(con, uid, acc, lu, data.get('v', '')))
    if act in ('shop_st', 'shop_pay'):                        # 管理群：商城訂單 備好了／已交付／取消／已收款
        if uid not in staff_users(con):          # 管理按鈕只認管理層（負責人 ）
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import shop
        who_ = (lu or {}).get('display_name') or '同事'
        if act == 'shop_pay':
            if uid not in money_users(con):
                return reply(con, acc, ev['replyToken'], _txt('確認收款只有看得到金額的管理層可以按。'))
            r0 = con.execute('SELECT paid_at FROM shop_orders WHERE id=?', (data.get('o', ''),)).fetchone()
            if not r0:
                return reply(con, acc, ev['replyToken'], _txt('找不到這筆訂單'))
            if r0[0]:
                return reply(con, acc, ev['replyToken'], _txt('這筆已經記過收款了 👍'))
            shop.paid(con, data.get('o', ''), '現場／匯款', who_)
            return reply(con, acc, ev['replyToken'], _txt(f"✅ {data.get('o', '')} 已記收款（{who_}）"))
        return reply(con, acc, ev['replyToken'], _txt(shop.set_status(con, data.get('o', ''), data.get('v', ''), who_)))
    if act in ('pol_up', 'pol_agent', 'pol_info', 'pol_ok', 'pol_redo', 'pol_paid', 'pol_sub'):     # 良民證（招募第三關）
        import interviews, recruit
        try:
            iv = interviews.get(con, data.get('i', ''))
        except ValueError:
            return reply(con, acc, ev['replyToken'], _txt('這筆已經不存在了，請直接留言 🙏'))
        staff = uid in staff_users(con)
        if act in ('pol_ok', 'pol_redo', 'pol_paid', 'pol_sub'):
            if not staff:
                return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
            return reply(con, acc, ev['replyToken'], _txt(recruit.police_mark(con, iv['id'], act[4:], (lu or {}).get('display_name') or '同事')))
        if uid != iv['line_uid'] and not staff:
            return reply(con, acc, ev['replyToken'], _txt('這是本人用的按鈕喔 🙏'))
        if act == 'pol_info':
            return reply(con, acc, ev['replyToken'], _txt(recruit.POLICE_INFO))
        if act == 'pol_up':
            ix.set_setting(con, f'police_wait:{uid}', iv['id'])
            return reply(con, acc, ev['replyToken'], _txt('好的！請直接在這裡傳良民證的照片（拍清楚、整張入鏡）📷\n只有示範服務店管理員看得到。'))
        return reply(con, acc, ev['replyToken'], recruit.police_agent(con, iv['id']))
    if act == 'iv_more':                                      # 管理群：應徵資料不夠 → 請他補填完整應徵表
        if uid not in staff_users(con):          # 管理按鈕只認管理層（負責人 ）
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import interviews
        try:
            return reply(con, acc, ev['replyToken'], _txt(interviews.ask_full_app(con, data.get('i', ''), (lu or {}).get('display_name') or '同事')))
        except ValueError:
            return reply(con, acc, ev['replyToken'], _txt('這筆應徵已經不存在了。'))
    if act in ('iv_ok', 'iv_rej', 'iv_rej2'):                 # 管理群：應徵資料 OK → 邀請電話面試／不適合 → 婉拒（誤觸：婉拒要再按一次確定）
        if uid not in staff_users(con):          # 管理按鈕只認管理層（負責人 ）
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import interviews
        who = (lu or {}).get('display_name') or '同事'
        try:
            iv = interviews.get(con, data.get('i', ''))
        except ValueError:
            return reply(con, acc, ev['replyToken'], _txt('這筆應徵已經不存在了。'))
        if iv['status'] != 'screening':
            return reply(con, acc, ev['replyToken'], _txt(f"這位已經處理過了（{iv['result_by'] or '—'}）"))
        if act == 'iv_rej':                      # 婉拒會直接傳訊息給應徵者、收不回 → 先問一次
            nm = (c.get(con, 'workers', iv['worker_id']) or {}).get('name', '這位應徵者')
            return reply(con, acc, ev['replyToken'], flex(f'確定婉拒 {nm}？', _bubble(_header('確定要婉拒嗎？', nm),
                         [_t(f'按「確定婉拒」會馬上用 LINE 傳婉拒訊息給 {nm}，傳出去就收不回。按錯了就按「按錯了，不婉拒」。', wrap=True)],
                         [_btn('確定婉拒', data=f"a=iv_rej2&i={iv['id']}"), _btn('按錯了，不婉拒', data='a=noop', primary=True)])))
        return reply(con, acc, ev['replyToken'], _txt(interviews.screen_ok(con, iv['id'], who) if act == 'iv_ok' else interviews.reject(con, iv['id'], who)))
    if act in ('iv_pick', 'iv_other', 'iv_ns', 'iv_re', 'iv_done', 'iv_conf'):      # 夥伴面試：應徵者選時段／管理群挑時間、沒出現、請他重選
        import interviews
        try:
            iv = interviews.get(con, data.get('i', ''))
        except ValueError:
            return reply(con, acc, ev['replyToken'], _txt('這筆面試已經不存在了，請直接留言 🙏'))
        staff = uid in staff_users(con)
        if act in ('iv_ns', 'iv_re'):
            if not staff:
                return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
            if iv['status'] not in ('invited', 'proposed', 'scheduled', 'need_time', 'no_show'):        # 稽核：舊卡片不能把已過面試的人打回去
                import recruit
                return reply(con, acc, ev['replyToken'], _txt(f"他已經在「{recruit.stage_name(iv['status']) if hasattr(recruit, 'stage_name') else iv['status']}」了，這張舊卡片不能再用"))
            who = (lu or {}).get('display_name') or '同事'
            if act == 'iv_ns':
                return reply(con, acc, ev['replyToken'], _txt(f"已記錄沒出現（{who}），系統已請他重新選時間。" if interviews.decide(con, iv['id'], 'no_show', who) else ''))
            return reply(con, acc, ev['replyToken'], _txt(interviews.reinvite(con, iv['id'], who)))
        if act == 'iv_conf':                      # 主管挑應徵者選的時段
            if not staff:
                return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
            return reply(con, acc, ev['replyToken'], _txt(interviews.confirm(con, iv['id'], data.get('t', ''), (lu or {}).get('display_name') or '同事')))
        if uid != iv['line_uid'] and not staff:
            return reply(con, acc, ev['replyToken'], _txt('這是應徵者本人用的按鈕喔 🙏'))
        if act == 'iv_done':
            return reply(con, acc, ev['replyToken'], _txt(interviews.propose(con, iv['id'])))
        if act == 'iv_other':
            ix.set_setting(con, f'iv_wait:{uid}', iv['id'])
            return reply(con, acc, ev['replyToken'], _txt('沒關係！請直接打字告訴我們你方便的時間（例如：週三下午、10/15 早上）'))
        r_ = interviews.pick(con, iv['id'], data.get('t', '')); m_ = _txt(r_)
        if '就這幾個' in r_:                      # 還可以再選 → 附「就這幾個」
            m_[0]['quickReply'] = {'items': [{'type': 'action', 'action': {'type': 'postback', 'label': '就這幾個', 'data': f"a=iv_done&i={iv['id']}", 'displayText': '就這幾個時段'}}]}
        return reply(con, acc, ev['replyToken'], m_)
    if act in ('tm_w', 'tm_c'):                               # 客戶：寫見證／同不同意公開
        import testimonials
        testimonials.ensure(con)
        t = con.execute('SELECT * FROM testimonials WHERE id=?', (data.get('t', ''),)).fetchone()
        if not t or ((lu or {}).get('customer_id') != t['customer_id'] and uid not in test_users(con)):
            return reply(con, acc, ev['replyToken'], _txt('謝謝您 🙏'))
        if act == 'tm_w':
            ix.set_setting(con, f'tm_wait:{uid}', t['id'])
            return reply(con, acc, ev['replyToken'], _txt('請直接在這裡打字告訴我們 😊（一兩句就好）'))
        testimonials.set_consent(con, t['id'], data.get('v') == '1')
        return reply(con, acc, ev['replyToken'], _txt('謝謝您 🥰 我們會把您的鼓勵轉達給服務人員！' if data.get('v') == '1' else '好的，只有我們看得到，謝謝您 🥰'))
    if act == 'tm_done':                                      # 管理群：已到官方 LINE 親自回覆滿意客戶
        if uid not in staff_users(con):          # 管理按鈕只認管理層（負責人 ）
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import testimonials
        return reply(con, acc, ev['replyToken'], _txt(testimonials.mark_replied(con, data.get('t', ''), (lu or {}).get('display_name') or '同事')))
    if act == 'wsent':                                        # 夥伴綁定訊息「已經傳給他了」→ 每天不再列他
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        try:
            return reply(con, acc, ev['replyToken'], _txt(mark_wbind_sent(con, data.get('w', ''), (lu or {}).get('display_name') or '管理層')))
        except ValueError as e:
            return reply(con, acc, ev['replyToken'], _txt(str(e)))
    if act == 'wbind':                                        # 管理群：給我這位夥伴的綁定訊息（轉傳夥伴）
        if uid not in staff_users(con):          # 管理按鈕只認管理層（負責人 ）
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        return reply(con, acc, ev['replyToken'], worker_bind_invite(con, data.get('w', '')))
    if act == 'camp':                                         # 年度保養詢問：請專人聯絡我／今年先不用
        import campaign
        return reply(con, acc, ev['replyToken'], _txt(campaign.answer(con, acc, uid, lu, data.get('k', ''), data.get('v', '')) or '收到，謝謝您 🙏'))
    if act == 'gb':                                           # 業主回覆服務完垃圾怎麼處理
        import prepq
        return reply(con, acc, ev['replyToken'], _txt(prepq.answer_trash(con, acc, uid, lu, data.get('s', ''), data.get('v', ''))))
    if act == 'tools':                                        # 業主回覆現場工具
        import sitetools
        if data.get('v') == 'note':
            ix.set_setting(con, f'tools_wait:{uid}', data.get('s', ''))
            return reply(con, acc, ev['replyToken'], _txt('好的，請直接打字告訴我們家裡有哪些工具、放在哪裡（例：有吸塵器和拖把，放在陽台）✏️'))
        return reply(con, acc, ev['replyToken'], _txt(sitetools.answered(con, acc, uid, lu, data.get('s', ''), data.get('v', ''))))
    if act in ('scok', 'scedit'):                              # 詳細需求審核（給夥伴看之前）
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import scopereview
        if act == 'scedit':
            _staff_wait(con, uid, 'scope_wait', oid)
            return reply(con, acc, ev['replyToken'], _txt('好的，請直接打字：服務範圍一行一項（下一則訊息會取代原本的範圍，並審核通過給夥伴看）✏️'))
        return reply(con, acc, ev['replyToken'], _txt(scopereview.approve(con, oid, (lu or {}).get('display_name') or '管理層')))
    if act == 'tier':                                         # 派工分級（店長排 A／B／C）
        if uid not in money_users(con) and uid not in deciders(con):
            return reply(con, acc, ev['replyToken'], _txt('派工分級只有店長（和負責人）可以改。'))
        import tiers
        try:
            name = tiers.set_tier(con, data.get('w', ''), data.get('t', ''), (lu or {}).get('display_name') or '管理層')
        except ValueError as e:
            return reply(con, acc, ev['replyToken'], _txt(str(e)))
        return reply(con, acc, ev['replyToken'], _txt(f"✅ {name} → {data.get('t')} 級（新單會{ {'A': '第一批', 'B': '第二批', 'C': '最後'}[data.get('t')] }問他）"))
    if act in ('lg_ok', 'lg_okall', 'lg_cat', 'lg_br', 'lg_set', 'lg_setb', 'lg_void'):      # 記帳確認卡
        import ledger
        out = ledger.postback(con, act, data, uid, (lu or {}).get('display_name') or '管理層')
        if out:
            return reply(con, acc, ev['replyToken'], out, 'ledger')
        return
    if act == 'lvex':                                         # 報價單級別範例，店長點等級
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import lvpick
        try:
            lv = int(data.get('l', '0'))
        except ValueError:
            lv = 0
        return reply(con, acc, ev['replyToken'], _txt(lvpick.pick(con, data.get('f', ''), lv, (lu or {}).get('display_name') or '管理層')))
    if act == 'mat':                                          # 管理群素材準備「我要傳」→ 一對一傳照片
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import materials
        name = (lu or {}).get('display_name') or ''
        msg, dm = materials.start(con, uid, data.get('k', ''), name)
        if dm:
            queue(con, WORK_ACCOUNT, uid, 'materials_dm', '請在這裡傳照片', _txt(dm))
        return reply(con, acc, ev['replyToken'], _txt(msg))
    if act == 'cbind':                                        # 管理群：給我這位業主的綁定訊息（轉傳業主）
        if uid not in staff_users(con):          # 管理按鈕只認管理層（負責人 ）
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        return reply(con, acc, ev['replyToken'], bind_invite(con, data.get('c', '')))
    if act in ('fin_ok', 'fin_redo'):
        if not (lu or {}).get('is_staff') and uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('只有派單管理群可以審核完成照片。'))
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order:
            return reply(con, acc, ev['replyToken'], _txt('這筆工作已經不存在了。'))
        import board
        who = (lu or {}).get('display_name') or '同事'
        if act == 'fin_redo':
            return reply(con, acc, ev['replyToken'], _txt(photo_redo(con, oid, who)))
        sent, msg = approve_finish(con, oid, who)
        if sent:                              # 業主不會看 LINE 的，管理群可以直接「主管代為驗收」
            return reply(con, acc, ev['replyToken'], flex(msg, _bubble(_header('', msg, '#2E7D32'), [_t('業主不常看官方 LINE、一直沒回的話，可以由主管代為驗收（會顯示「主管驗收」）。', wrap=True, size='sm', color=SUB)],
                                                                      [_btn('主管代為驗收', data=f'a=acc_mgr&o={oid}')])))
        if msg:
            return reply(con, acc, ev['replyToken'], _txt(msg))
        m = card_finish_customer(con, oid)
        if uid and (uid in test_users(con) or uid in staff_users(con)):     # 業主沒綁 LINE：把業主會看到的卡片當預覽傳給按的人
            queue(con, acc, uid, 'finish_preview', '【預覽】業主會收到：' + m[0]['altText'],
                  [{'type': 'text', 'text': '【預覽】業主沒有綁 LINE，這是業主會收到的樣子 👇'}] + m, oid)
        return reply(con, acc, ev['replyToken'], bind_invite(con, order['customer_id'], '業主還沒綁 LINE，完成照片傳不過去。'))
    if act == 'insp':                                         # 業主選驗收方式（現場驗收要同步通知夥伴；線上看照片）
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order or ((lu or {}).get('customer_id') != order['customer_id'] and uid not in test_users(con)):
            return reply(con, acc, ev['replyToken'], _txt('收到，專人會跟您確認 🙏'))
        site = data.get('m') == 'site'
        ix.set_setting(con, f'insp:{oid}', 'site' if site else 'online')
        ix.set_setting(con, f"insp_pref:{order['customer_id']}", 'site' if site else 'online')      # 記住，下次不再問
        import board; board.log(con, oid, 'inspect_mode', '業主到現場驗收' if site else '業主線上看照片驗收')
        if site:
            for a in c.rows(con, "SELECT worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)):
                to_worker(con, a['worker_id'], 'inspect_site', '業主會到現場驗收', _txt(f"🏠 業主說會到現場驗收（{when(dict(order))}）。\n做完還是照樣拍服務前後照片、傳上去；業主到了請陪同看一遍，有要加強的當場處理。"), oid)
            admin_notify(con, '🏠 業主會到現場驗收', [('工作', when(dict(order))), ('處理', '已通知夥伴；不用處理')], '', '#0A84FF', oid=oid)
            return reply(con, acc, ev['replyToken'], _txt('好的 👍 已經通知服務人員您會到現場驗收；完成照片一樣會傳給您留存。'))
        return reply(con, acc, ev['replyToken'], _txt('好的 👍 完工後會把服務前後照片傳給您，看過沒問題按「確認完工」就好。'))
    if act == 'acc_mgr':                                      # 主管代為驗收（業主不會看 LINE、一直沒回）→ 顯示「主管驗收」
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('主管代為驗收只有管理層可以按。'))
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order or order['status'] != 'completed':
            return reply(con, acc, ev['replyToken'], _txt('這筆還沒完工，不能驗收。'))
        if order['acceptance'] == 'accepted':
            return reply(con, acc, ev['replyToken'], _txt('這筆已經驗收過了 👍'))
        who = (lu or {}).get('display_name') or '管理層'
        con.execute("UPDATE orders SET acceptance='accepted',acceptance_note=?,version=version+1 WHERE id=?", (f'主管代為驗收（{who} {c.now()[:16]}）', oid))
        import board; board.log(con, oid, 'manager_accept', who)
        return reply(con, acc, ev['replyToken'], _txt(f'✅ 已記錄主管驗收（{who}）'))
    if act in ('acc_ok', 'acc_fix'):
        order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not order or ((lu or {}).get('customer_id') != order['customer_id'] and uid not in test_users(con)):
            return reply(con, acc, ev['replyToken'], _txt('收到，專人會跟您確認 🙏'))
        if order['status'] in ('cancelled', 'archived'):
            return reply(con, acc, ev['replyToken'], _txt('這筆服務已經取消了，有問題請直接留言 🙏'))
        import board
        if act == 'acc_ok' and order['acceptance'] == 'accepted':          # 稽核：連按兩次 → 管理群兩張卡、評分問兩次
            if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='customer_accept'", (oid,)).fetchone():
                return reply(con, acc, ev['replyToken'], _txt('已經收到您的確認了，謝謝您 🙏'))
            board.log(con, oid, 'customer_accept', '（主管已先代為驗收）')          # 稽核 A5：主管先代為驗收，業主自己按的確認照樣記、照樣問評分
            return reply(con, acc, ev['replyToken'], [rating_ask(oid)])
        if act == 'acc_ok':
            con.execute("UPDATE orders SET acceptance='accepted',acceptance_note=? WHERE id=?", (f'客戶在 LINE 確認完工 {c.now()[:16]}', oid))
            board.log(con, oid, 'customer_accept', '')
            admin_notify(con, '👍 客戶確認完工', [('工作', when(dict(order))), ('地點', order['title'])], '/#home', '#2E7D32', oid=oid)
            return reply(con, acc, ev['replyToken'], [rating_ask(oid)])
        con.execute("UPDATE orders SET acceptance='disputed',acceptance_note=? WHERE id=?", (f'客戶在 LINE 反映需要加強 {c.now()[:16]}', oid))
        board.log(con, oid, 'customer_issue', '')
        admin_notify(con, '⚠️ 客戶說有地方需要加強', [('工作', when(dict(order))), ('地點', order['title']), ('處理', '請盡快在官方 LINE 跟客戶確認；處理完到中控台首頁「客戶回饋待處理」按「已處理」（尾款通知才會照常發）')],
                     '/#home', '#C62828', oid=oid, tag='⚠️ 客戶反映要加強，請管理層盡快聯絡 👇')          # 稽核：@管理層
        import notify
        notify.staff(con, '⚠️ 客戶反映需要加強', f"{order['date']} {order['title']}", '/#home')
        return reply(con, acc, ev['replyToken'], _txt('收到，很抱歉！請直接告訴我們哪裡需要加強（可以拍照），專人會馬上跟您聯繫。'))
    if act == 'join':
        if lu and lu.get('worker_id'):
            return reply(con, acc, ev['replyToken'], _txt(f"{c.get(con, 'workers', lu['worker_id'])['name']}，你已經報到過了 👍"))
        if lu and lu.get('customer_id'):
            return reply(con, acc, ev['replyToken'], _txt('這是示範服務店夥伴報到用的按鈕 🙏 您的預約資料已經綁定好了，有需要直接在這裡留言就可以。'))
        return reply(con, acc, ev['replyToken'], flex('示範服務店夥伴報到', _bubble(_header('報到', '只有你能用這個連結'),
                     [_t('按下面按鈕，選你的名字、確認手機，完成報到。連結 3 天內有效。')], [_btn('開始報到', uri=join_token(con, uid, acc), primary=True)])), 'join')
    order = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
    if not order:
        return reply(con, acc, ev['replyToken'], _txt('這筆工作已經不存在了。'))
    if order['status'] in ('cancelled', 'archived') and act in ('grab', 'wok', 'alt', 'cok', 'cchg', 'hx', 'hxs', 'ccx_ok'):       # 盤點：舊卡片按鈕對已取消的單不能再生效
        return reply(con, acc, ev['replyToken'], _txt(f"這筆（{when(dict(order))}）已經取消了，這張卡片的按鈕不能再用 🙏 有需要請直接留言。"))
    if order['status'] == 'completed' and act in ('grab', 'wok', 'alt', 'cok', 'cchg', 'hx', 'hxs', 'ccx_ok'):
        return reply(con, acc, ev['replyToken'], _txt(f"這筆（{when(dict(order))}）已經完成了，謝謝 🙏 有需要請直接留言。"))
    if act == 'grab' and order['date'] and order['date'] < dt.datetime.now(c.TZ).date().isoformat():
        return reply(con, acc, ev['replyToken'], _txt('這筆工作的日期已經過了，不能再接 🙏'))
    if act in ('tlack', 'wok') and gid and gid == admin_group(con) and (data.get('r') == '1' or act == 'tlack'):
        return reply(con, acc, ev['replyToken'], _txt('這是轉給管理群的夥伴卡：請打電話問夥伴，確認了到中控台「更多 › 管理群待處理 › 出勤異常」按「已電話確認」🙏'))      # 每日檢查：以前會記成按的管理層自己
    if act == 'rtdone':                                       # 定期特殊服務做完了（客戶01冷氣濾網）
        import siteroutine
        wid_ = _group_worker(con, gid) or (lu or {}).get('worker_id')
        if not wid_ or not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid_)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('這場沒有排你喔，有問題直接在 LINE 問示範服務店 🙏'))
        wn_ = (con.execute('SELECT name FROM workers WHERE id=?', (wid_,)).fetchone() or ['夥伴'])[0]
        n_ = siteroutine.done(con, oid, wn_)
        return reply(con, acc, ev['replyToken'], _txt('已記下定期項目做完了，謝謝你 🙏' if n_ else '這一期的定期項目已經記過做完了 👍'))
    if act == 'tlack':                                        # 工具不夠，請公司協助
        import crewcheck
        wid_ = _group_worker(con, gid) or (lu or {}).get('worker_id')
        if wid_ and not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid_)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('這場沒有排你喔，有問題直接在 LINE 問示範服務店 🙏'))
        if not wid_:
            return reply(con, acc, ev['replyToken'], _txt('你的 LINE 還沒綁定示範服務店夥伴，請直接打電話跟示範服務店說 🙏'))
        return reply(con, acc, ev['replyToken'], _txt(crewcheck.lack(con, oid, wid_)))
    if act == 'aib':                                          # 核對 AI 整理的業主交代
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這是管理層用的按鈕（負責人、店長、客服）。'))
        import aibrief
        c.CTX.actor = ((lu or {}).get('display_name') or '管理層') + '（派單管理群）'
        return reply(con, acc, ev['replyToken'], _txt(aibrief.decide(con, oid, data.get('v', ''), (lu or {}).get('display_name') or '管理層', data.get('n'))))
    if act in ('grab', 'undo', 'wok', 'alt', 'pass'):
        gw = _group_worker(con, gid)
        is_staff = bool(lu and lu.get('is_staff'))
        wid = (lu or {}).get('worker_id')
        if gw and not is_staff and not wid:          # 在夥伴個人群組按：這次當他按，綁定請管理群確認（資安：不自動綁）
            _group_bind_request(con, acc, uid, gw, lu); wid = gw
        if gw and is_staff:
            wid = gw                                  # 同事在夥伴群組按 → 代接
        if not wid:
            return reply(con, acc, ev['replyToken'], _txt('你的 LINE 還沒綁定示範服務店夥伴：請在「示範服務店接單管理」群按「我是示範服務店夥伴，報到」。'))
        w = c.get(con, 'workers', wid); who = w['name'] + ('（同事代接）' if gw and is_staff else '')
        if act == 'alt' and con.execute("SELECT 1 FROM assignments a JOIN checkins k ON k.assignment_id=a.id WHERE a.order_id=? AND a.worker_id=? AND a.status='accepted' "
                                        "AND k.arrived_at!='' AND COALESCE(k.left_at,'')=''", (oid, wid)).fetchone():
            # 人已經在現場：不能走請假（會把他從這場拿掉）→ 請管理層處理
            admin_notify(con, f"{w['name']}（在現場）：新的時間沒辦法", [('工作', when(dict(order))), ('處理', '請跟夥伴確認能做到幾點，必要時照原時間收尾或另約')], '', '#C47F00', oid=oid, tag='夥伴在現場說時間不行')
            return reply(con, acc, ev['replyToken'], _txt('收到，已告訴管理層 🙏 你已經在現場，請先照原本的時間做，管理層會跟你確認。'))
        if act == 'alt' and con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone():
            import leave            # 已排到的人說新時間不行 → 走請假流程（管理群判斷記點、決定另外派工或問業主）
            leave.request(con, wid, oid, '時間異動後無法配合')
            return reply(con, acc, ev['replyToken'], _txt('收到！已告訴派單管理群，這場會另外安排，謝謝你說一聲 🙏'))
        if act == 'pass':                  # 稽核 優化：派工卡「這次不行」
            a0 = con.execute("SELECT id FROM assignments WHERE order_id=? AND worker_id=? AND status='invited'", (oid, wid)).fetchone()
            if a0:
                con.execute("UPDATE assignments SET status='declined',notes=notes||'（LINE 這次不行）',version=version+1 WHERE id=?", (a0[0],))
                board.log(con, oid, 'declined', w['name'] + '（LINE 這次不行）')
            return reply(con, acc, ev['replyToken'], _txt('收到，謝謝你回覆 🙏 有適合的工作會再通知你。'))
        if act == 'alt':
            board.log(con, oid, 'worker_alt', w['name'])
            admin_notify(con, f"{w['name']}：這時間不行", [('工作', when(dict(order))), ('地點', order['title']), ('處理', '請跟業主協調新時間後改期')], '/#dispatch', '#C47F00', oid=oid)
            notify.staff(con, f"{w['name']} 說這時間不行", f"{order['date']} {order['start']} {order['title']}，請跟業主協調", '/#dispatch')
            return reply(con, acc, ev['replyToken'], _txt('收到！示範服務店會跟業主協調時間，有新時間會再傳給你 🙏'))
        import automation
        if act == 'grab' and automation.is_blacklisted(con, wid) and not (gw and is_staff):
            return reply(con, acc, ev['replyToken'], _txt('這筆工作目前沒有開放給你，謝謝你 🙏'))
        if act == 'grab' and order['dispatch_only'] and not (gw and is_staff) and not con.execute(
                "SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status IN ('invited','accepted')", (oid, wid)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('這筆是示範服務店指定邀請的工作，沒有邀請到你喔。有適合你的工作會再通知你 🙏'))
        if act == 'grab':
            if order['status'] not in ('review', 'draft') or con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (oid,)).fetchone()[0] >= order['required_workers']:
                return reply(con, acc, ev['replyToken'], card_taken(con, oid), 'taken', oid)
            con.execute('SAVEPOINT g')
            try:
                old = con.execute('SELECT * FROM assignments WHERE order_id=? AND worker_id=?', (oid, wid)).fetchone()
                if old and old['status'] in ('cancelled', 'declined') and any(k in (old['notes'] or '') for k in ('取消邀請', '收回邀請', '管理群換人')) and not (gw and is_staff):      # 每日檢查：換掉的人也不能接回
                    con.execute('RELEASE g')       # 管理層取消邀請後，用舊卡按接單不能接回
                    return reply(con, acc, ev['replyToken'], _txt('這場已經安排好了，謝謝你 🙏 有適合你的工作會再通知你。'))
                if old and old['status'] == 'declined' and not (gw and is_staff) and con.execute(
                        "SELECT 1 FROM worker_leaves WHERE order_id=? AND worker_id=? AND status NOT IN ('withdrawn')", (oid, wid)).fetchone():
                    con.execute('RELEASE g')       # 每日檢查：請假後不能自己用舊卡接回（要回來請管理層排，請假才會撤回）
                    admin_notify(con, f"🙋 {w['name']} 請假後想回來做這場", [('工作', when(dict(order))), ('處理', '要讓他回來：中控台這張工單「直接排入」（請假自動撤回）')], '', '#0A84FF', oid=oid)
                    return reply(con, acc, ev['replyToken'], _txt('你這場已經請假了 🙏 想回來做的話已經幫你告訴管理層，由管理層幫你排回。'))
                p = {'order_id': oid, 'worker_id': wid, 'status': 'accepted', 'pay_mode': 'hourly', 'rate': (old['rate'] if old else None) or w['rate'],
                     'notes': f"LINE 接單 {c.now()}" + ('（同事代接）' if gw and is_staff else '')}
                if old:
                    p.update(id=old['id'], version=old['version'])
                c.CTX.actor = who + '（LINE）'
                c.save(con, 'assignments', p)
                con.execute('RELEASE g')
            except ValueError as e:
                con.execute('ROLLBACK TO g'); con.execute('RELEASE g')
                return reply(con, acc, ev['replyToken'], _txt(f'沒辦法接這筆：{e}'))
            board.log(con, oid, 'grab', f"{who}（LINE，{ev.get('timestamp')}）")
            dispatch.after_accept(con, oid)
            notify.staff(con, f"{who} 接到工作", f"{order['date']} {order['start']} {order['title']}", '/#dispatch')
            # 客戶已確認 → 人到齊自動排定：after_accept 已處理
            return reply(con, acc, ev['replyToken'], card_grabbed(con, oid, w['name'], wid), 'grabbed', oid)
        if act == 'undo':
            a = con.execute("SELECT * FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone()
            grabbed = con.execute("SELECT MAX(at) FROM dispatch_log WHERE order_id=? AND action='grab' AND (detail=? OR detail LIKE ?)", (oid, w['name'], w['name'] + '（%')).fetchone()[0]
            age = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.datetime.fromisoformat(grabbed)).total_seconds() if grabbed else 1e9
            if not a or age > 600:   # dispatch_log.at 是 UTC
                return reply(con, acc, ev['replyToken'], _txt('已超過 10 分鐘，請直接在群組跟示範服務店說，會幫你找人替補。'))
            con.execute("UPDATE assignments SET status='cancelled',notes=notes||'（10 分鐘內自行取消）',version=version+1 WHERE id=?", (a['id'],))
            import leave; leave.reopen(con, oid)          # 已排定又少了人：退回待確認（業主的確認保留）；新的人到齊、管理群審核過才再排定
            con.execute("UPDATE orders SET dispatch='open',crew_ok='' WHERE id=?", (oid,))
            board.log(con, oid, 'undo', w['name'])
            import automation as _au
            back = [r[0] for r in con.execute("SELECT a.worker_id FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='cancelled' AND a.notes LIKE '%（已額滿）%' AND w.status='active'", (oid,))
                    if not _au.is_blacklisted(con, r[0])]          # 稽核 B6：黑名單／暫停的不恢復
            if back:                         # 稽核 M2：同一批被問的人恢復可接（以前按舊卡會被擋，系統卻跳去問下一級）
                con.execute(f"UPDATE assignments SET status='invited',notes=REPLACE(notes,'（已額滿）',''),version=version+1 WHERE order_id=? AND status='cancelled' AND notes LIKE '%（已額滿）%' AND worker_id IN ({','.join('?' * len(back))})", (oid, *back))
                on_dispatch(con, oid, back)
            if order['dispatch_only'] and con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='tier_ask'", (oid,)).fetchone():
                admin_notify(con, f"{w['name']} 取消接單", [('工作', when(dict(order))), ('處理', f"不用處理：同一批 {len(back)} 位已恢復可接、重傳派工卡；系統照分級繼續問")], '/#dispatch', '#C47F00', oid=oid)
            elif order['dispatch_only']:       # 店長指定的名單：不自動發給別人，請她改派
                admin_notify(con, f"{w['name']} 取消接單", [('工作', when(dict(order))), ('處理', '請到派工重新挑人（指定派工不會自動發給別人）')], '/#dispatch', '#C62828', oid=oid)
            else:
                admin_notify(con, f"{w['name']} 取消接單", [('工作', when(dict(order))), ('處理', '系統已重新派工')], '/#dispatch', '#C62828', oid=oid)
                dispatch.auto_dispatch(con, {'order_id': oid})
            return reply(con, acc, ev['replyToken'], _txt('已取消，示範服務店會另外安排其他夥伴。'))
        if (data.get('r') == '1' or data.get('t') == '1') and not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt('這場沒有排你喔（可能已經換人），有問題直接在 LINE 問示範服務店 🙏'))
        if data.get('r') == '1' or data.get('t') == '1':
            import crewcheck
            ok_, why_ = crewcheck.ack(con, oid, wid, data.get('t') == '1')
            if not ok_:
                return reply(con, acc, ev['replyToken'], _txt(why_))
        board.log(con, oid, 'worker_ack', w['name'])
        td = dt.datetime.now(c.TZ).date(); tm = (td + dt.timedelta(days=1)).isoformat()
        msg = '收到，明天見 👍' if order['date'] == tm else '收到，今天辛苦了 👍' if order['date'] == td.isoformat() else f"收到，{md(order['date'])} 見 👍"
        return reply(con, acc, ev['replyToken'], _txt(msg))
    if act == 'ccx_ok':                                       # 管理層確認業主的取消申請
        if uid not in staff_users(con):
            return reply(con, acc, ev['replyToken'], _txt('確認取消只有管理層可以按。'))
        if order['actual_start']:
            return reply(con, acc, ev['replyToken'], _txt('夥伴已經到場打卡了，不能直接取消；請先跟業主、夥伴確認，再到中控台處理。'))
        days = (dt.date.fromisoformat(order['date']) - dt.datetime.now(c.TZ).date()).days if order['date'] else 99
        who = (lu or {}).get('display_name') or '管理層'
        before = c.get(con, 'orders', oid); c.CTX.actor = f'{who}（LINE 確認業主取消）'
        out = c.transition(con, oid, 'cancel', {'version': before['version'], 'reason': f"客戶取消（業主在 LINE 申請，離服務日 {days} 天；{who} 確認）"})
        board.after_transition(con, before, out, 'cancel', who)
        return reply(con, acc, ev['replyToken'], _txt(f"✅ 已取消 {when(dict(order))}，業主和夥伴都會收到 LINE 通知；收款單已自動處理（已付的訂金標給店長）。"), 'ccx_ok', oid)
    if act in ('hx', 'hxs'):                                   # 多館業主換館別（客戶01 9／10／11 號館）
        cid = (lu or {}).get('customer_id')
        if cid != order['customer_id'] and uid not in test_users(con):
            return reply(con, acc, ev['replyToken'], _txt('這張卡片要由業主本人按喔 🙏'))
        if order['actual_start'] or (order['date'] or '') < dt.datetime.now(c.TZ).date().isoformat():
            return reply(con, acc, ev['replyToken'], _txt('這場已經開始或過了，要調整請直接留言告訴我們 🙏'))
        sites = multi_sites(con, order['customer_id'])
        if act == 'hx':
            return reply(con, acc, ev['replyToken'], flex('要換成哪一館？', card_house_pick(con, oid)), 'hx', oid)
        new = next((x for x in sites if x['id'] == data.get('s')), None)
        if not new:
            return reply(con, acc, ev['replyToken'], _txt('找不到這一館，請直接留言告訴我們 🙏'))
        if new['id'] == order['site_id']:
            return reply(con, acc, ev['replyToken'], _txt(f"這場本來就是 {new['name']} 👍"))
        set_house(con, oid, new['id'], '業主在 LINE 換館別')
        return reply(con, acc, ev['replyToken'], _txt(f"✅ 已改成 {new['name']}（{when(dict(order))}），服務人員也會收到新的地址。"), 'hxs', oid)
    if act in ('cok', 'cchg'):
        cid = (lu or {}).get('customer_id')
        if cid != order['customer_id'] and uid not in test_users(con):
            return reply(con, acc, ev['replyToken'], _txt('收到，專人會跟您確認 🙏'))
        if act == 'cok' and data.get('t') and data['t'] != _tsig(order):       # 稽核：舊卡片的時間已經改了 → 不能直接確認新時間
            return reply(con, acc, ev['replyToken'], _txt(f"這張卡片的時間是舊的，服務時間已經改成 {when(dict(order))}，請確認新的時間 👇") + card_assigned(con, oid), 'cok_old', oid)
        if act == 'cok' and order['status'] == 'confirmed':
            return reply(con, acc, ev['replyToken'], _txt('✅ 這次服務已經排定了，謝謝您！服務前一天我們會再提醒您。'), 'cok', oid)
        if act == 'cok':
            if not order['customer_confirmed']:
                con.execute('UPDATE orders SET customer_confirmed=1,version=version+1 WHERE id=?', (oid,))
            import door
            ask_door = door.need_ask(con, oid)     # 先沿用開門方式，夥伴的「已排定」卡才有進門資訊（稽核 A3）
            note = _auto_confirm(con, oid)
            board.log(con, oid, 'customer_ok', note)
            notify.staff(con, '客戶在 LINE 確認時間', f"{order['date']} {order['start']} {order['title']} {note}", '/#home')
            if note != '已排定':                   # 已排定的會另外收到「已自動排定」卡
                admin_notify(con, '👍 業主在 LINE 確認時間', [('接下來', note or '等排定')], '/#home', '#2E7D32', oid=oid)
            else:                                  # 稽核：以前註解說會收到，其實沒有
                admin_notify(con, '✅ 業主確認時間，已自動排定', [('說明', '人員已審核、業主已確認，地址已傳給夥伴')], '/#schedule', '#2E7D32', oid=oid)
            import prepq
            pm, parts = prepq.message(con, oid)   # 確認時間後，開門、垃圾、工具還不知道的一起問
            if pm:
                prepq.mark(con, oid, parts)
                return reply(con, acc, ev['replyToken'], _txt('✅ 已確認，謝謝您！再麻煩點選這幾件事（左右滑）👇') + pm, 'cok', oid)
            return reply(con, acc, ev['replyToken'], _txt('✅ 已確認，謝謝您！服務前一天我們會再提醒您。'), 'cok', oid)
        import reschedule                # 「需要改時間」→ 請業主線上選新日期、時段，送回來給管理群確認（不再請他打字）
        return reply(con, acc, ev['replyToken'], reschedule.card_offer(con, dict(order), acc), 'cchg', oid)

def _mask_phone(p):
    d = re.sub(r'\D', '', p or '')
    return f'{d[:4]}-***-{d[-3:]}' if len(d) >= 8 else '—'

def match_customer(con, acc, display_name):
    """用 LINE 名稱找系統裡的舊客戶（客戶資料的 LINE 名稱、客戶名稱、別名）。只有唯一一位才回傳，避免認錯人。"""
    n = (display_name or '').strip()
    if len(n) < 1:
        return None
    ids = {r[0] for r in con.execute(f"SELECT id FROM customers WHERE status!='ended' AND (TRIM(line_name)=? OR TRIM(name)=?) AND {brands.cust_sql()}=?", (n, n, acc))}
    ids |= {r[0] for r in con.execute(f'SELECT a.customer_id FROM customer_aliases a JOIN customers c ON c.id=a.customer_id WHERE TRIM(a.alias)=? AND c.status!=? AND {brands.cust_sql("c")}=?', (n, 'ended', acc))}
    taken = {r[0] for r in con.execute('SELECT customer_id FROM line_users WHERE customer_id IS NOT NULL AND account=?', (acc,))}
    ids -= taken
    return c.get(con, 'customers', ids.pop()) if len(ids) == 1 else None

def card_bind_confirm(con, cu, acc='demoshop'):
    rows = [_row('稱呼', cu['name']), _row('電話', '已登記（下一步請回覆末四碼）' if len(re.sub(r'\D', '', cu['phone'] or '')) >= 8 else '—')]
    return flex('請確認您的資料', _bubble(_header('請確認您的資料 ✅', '系統已幫您帶入', brand=acc), rows,
        [_btn('是我，確認綁定', data='a=bind_ok', primary=True), _btn('不是我／資料有誤', data='a=bind_no')],
        '確認後，排班、服務人員、前一天提醒都會直接傳到這裡。'))

def bind_prompt(con, acc, uid, lu, token_):
    """舊客戶綁定：用 LINE 名稱找到人 → 帶入資料請他確認；找不到 → 請他留姓名電話（用電話自動對到舊資料）。"""
    cu = match_customer(con, acc, (lu or {}).get('display_name'))
    if cu:
        con.execute('UPDATE line_users SET suggest_cid=?,nudged_at=? WHERE account=? AND user_id=?', (cu['id'], c.now(), acc, uid))
        return reply(con, acc, token_, card_bind_confirm(con, cu, acc), 'bind')
    import worker_portal
    con.execute("UPDATE line_users SET suggest_cid='',onboard='asked',nudged_at=? WHERE account=? AND user_id=?", (c.now(), acc, uid))
    return reply(con, acc, token_, flex('請留一下基本資料', _bubble(_header('您好 👋', '先確認一下您是哪位', brand=acc),
        [_t('留下稱呼和電話（10 秒）。老客戶用電話就能自動找到您的資料。', wrap=True)],
        [_btn('留基本資料', uri=worker_portal.onboard_token(con, acc, uid, 'hi'), primary=True)] + ([_btn('我要應徵夥伴', data='a=role&r=w')] if acc in APPLY_ACCOUNTS else []),      # 
        '是要應徵夥伴嗎？按「我要應徵夥伴」或傳「應徵」給我們。' if acc in APPLY_ACCOUNTS else '')), 'bind')

def book_url(con, acc, uid, extra=''):
    """線上估價連結；綁定的業主帶連結碼 → 表單自動帶入自己的資料。"""
    url = f'{BASE_URL}/book?b={acc}' + extra
    cid = (con.execute('SELECT customer_id FROM line_users WHERE account=? AND user_id=?', (acc, uid or '')).fetchone() or [None])[0]
    if cid:
        import worker_portal
        url += '&t=' + worker_portal.cust_token(con, acc, uid, cid)
    return url

def card_book(con, acc, uid):
    """選單「線上估價」：綁定的 → 資料已帶入；沒綁的 → 直接填＋綁定一下之後都自動帶入。"""
    import brands
    cid = (con.execute('SELECT customer_id FROM line_users WHERE account=? AND user_id=?', (acc, uid or '')).fetchone() or [None])[0]
    if cid:
        cu = c.get(con, 'customers', cid)
        btns = [_btn('開始估價（資料已帶入）', uri=book_url(con, acc, uid), primary=True)]
        return flex('線上估價：資料已幫您帶入', _bubble(_header('線上估價・預約', f"{cu['name']} 您好", brand=acc),
            [_t('稱呼、電話、地址都已經幫您帶入，確認一下、拍幾張照片就好；報價會傳到這裡。', wrap=True)], btns))
    return flex('線上估價・預約', _bubble(_header('線上估價・預約', brands.name(acc), brand=acc),
        [_t('回答幾個問題、拍幾張照片，報價會傳到這裡。', wrap=True),
         _t('先綁定一下（10 秒），之後每次估價資料都自動帶入。', size='sm', color=SUB, wrap=True, margin='md')],
        [_btn('開始線上估價', uri=f'{BASE_URL}/book?b={acc}', primary=True), _btn('綁定我的資料（10 秒）', data='a=bind')]))

def card_next_step(con, acc, uid, cid, title='綁定完成 ✅'):
    """綁定完成後：問要不要現在預約（表單自動帶入資料）、留下案場資料。"""
    import worker_portal
    t = worker_portal.cust_token(con, acc, uid, cid); cu = c.get(con, 'customers', cid)
    return flex('綁定完成！要不要現在預約？', _bubble(_header(title, cu['name'], '#2E7D32', brand=acc),
        [_t('之後排班、服務人員、前一天提醒、完成照片都會直接傳到這裡。', wrap=True),
         _t('最近有需要服務嗎？可以直接預約（資料已幫您帶入），或先留下案場資料，下次預約更快。', wrap=True, margin='md', color=SUB)],
        [_btn('現在預約（資料已帶入）', uri=f'{BASE_URL}/book?b={acc}&t={t}', primary=True), _btn('留下案場資料', uri=f'{BASE_URL}/site?t={t}'),
         _btn('先不用，謝謝', data='a=noop')]))

def _close_bind_requests(con, acc, uid, kind, by):
    """稽核 H2：已經綁好 → 同一個 LINE 還開著的綁定待確認一起關（管理層之後再按也不會重複推）。"""
    con.execute("UPDATE bind_requests SET status='superseded',decided_by=?,decided_at=? WHERE account=? AND user_id=? AND kind=? AND status='open'", (by, c.now(), acc, uid, kind))

def _bind_customer(con, acc, uid, lu, cid, ev):
    con.execute("UPDATE line_users SET customer_id=?,suggest_cid='',onboard='customer' WHERE account=? AND user_id=?", (cid, acc, uid))
    _close_bind_requests(con, acc, uid, 'customer', '系統（本人確認）')
    con.execute("UPDATE customers SET line_account=? WHERE id=? AND (line_account IS NULL OR line_account='')", (acc, cid))
    cu = c.get(con, 'customers', cid)
    c.audit(con, 'line_customer_bind', 'customers', cid, after={'line_user': uid, 'display_name': (lu or {}).get('display_name')})
    admin_notify(con, '🔗 舊客戶完成 LINE 綁定', [('客戶', f"{cu['name']}（{cu['phone']}）" if cu['phone'] else cu['name']), ('LINE', (lu or {}).get('display_name') or '')], f"/#c/{cu['id']}", '#2E7D32')
    after_customer_linked(con, cid)          # 稽核 C2
    return reply(con, acc, ev['replyToken'], card_next_step(con, acc, uid, cid))

def _progress(con, o, q=None):
    """訂單目前在哪一步、接下來誰要做什麼。回傳 (步驟名, 說明, 按鈕們)。"""
    import payments
    tok = q['token'] if q else None
    if o['status'] == 'cancelled':
        return '已取消', '這筆預約已取消。', []
    if o['status'] == 'completed':
        if (o['acceptance'] if 'acceptance' in o.keys() else '') == 'pending':
            return '完工・待您驗收', '服務完成了，完成照片已傳給您，請確認是否滿意。', []
        return '已完成', '感謝您！有需要歡迎再預約。', []
    pays = payments.rows_for(con, order_id=o['id'])
    need = [r for r in pays if r['kind'] in ('deposit', 'full', 'month') and r['status'] != 'confirmed']
    if need:
        r = need[0]
        if r['status'] == 'reported':
            return '款項核對中', f"已收到您的匯款回報（{r['label']}），核對後會通知您。", []
        return '待付款', f"{r['label']} NT$ {r['amount'] // 100:,}，請於 {md(r['due'])} 前付款；收到款項後會正式排定。", [_btn('付款／回報匯款', uri=f'{BASE_URL}/q/{tok}#pay', primary=True)] if tok else []
    crew = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
    if crew < (o['required_workers'] or 1):
        return '安排人員中', '正在為您安排服務人員，找到人會立刻通知您。', []
    if o['status'] in ('review', 'draft') and not o['customer_confirmed']:
        return '請確認時間', '人員已安排好，請確認服務時間。', [_btn('確認這個時間 ✅', data=f"a=cok&o={o['id']}&t={_tsig(o)}", primary=True)]
    if o['status'] in ('review', 'draft'):           # 稽核：還沒正式排定（等人員審核／系統排定），不能跟夥伴那邊說法相反
        return ('人員確認中', '服務人員已經找到了，我們正在確認人員安排，完成後會通知您。', []) if not o['crew_ok'] else ('排定中', '時間、人員都確認了，正在為您正式排定，完成後會通知您。', [])
    if o['date'] == dt.datetime.now(c.TZ).date().isoformat():
        return '服務當天', '今天是服務日，服務人員到場會通知您。', []
    return '已排定', '一切就緒！服務前一天會再提醒您。', []

def _photo_orders(con, cid, limit=10):
    """已經傳過完成照片給業主的工單（新的在前）：「我的預約」看完成照片用。"""
    return c.rows(con, "SELECT o.*,s.name sname FROM orders o LEFT JOIN sites s ON s.id=o.site_id WHERE o.customer_id=? AND o.status='completed' "
                       "AND EXISTS(SELECT 1 FROM line_outbox x WHERE x.order_id=o.id AND x.kind='finish_c' AND x.status NOT LIKE 'skipped%') "
                       "ORDER BY o.date DESC,o.start DESC LIMIT ?", (cid, limit))

PHOTO_SHARE_Q = '另外想請問：這次的服務前後照片（會遮掉門牌、人臉等可辨識的資訊，也不寫您的名字），可以不具名分享在{brand}的作品集和社群嗎？想收回隨時可以告訴我們。'

def photo_share_ask(con, oid):
    """要不要在謝謝卡問照片授權：店長核准文字（photo_share_ask=on）、這場有服務前和完成照片、還沒問過。"""
    if ix.setting(con, 'photo_share_ask') != 'on' or ix.setting(con, f'photo_share:{oid}'):
        return False
    n = {r[0] for r in con.execute("SELECT DISTINCT label FROM attachments WHERE order_id=? AND label IN ('before','after')", (oid,))}
    return n == {'before', 'after'}

def card_past_photos(con, acc, cid):
    """過去的完成照片：最近 10 場，每場一張卡（日期、地點、看照片）。"""
    import worker_portal
    os_ = _photo_orders(con, cid)
    if not os_:
        return _txt('還沒有完成照片喔，每次服務完成後照片會傳到這裡，也可以在「我的預約」再看 😊')
    bs = [_bubble(_header('完成照片', when(o), brand=acc), [_row('地點', o['sname'] or o['title'] or '—'),
                  _row('照片', f"服務前 {n.get('before', 0)} 張・完工 {n.get('after', 0)} 張")],
                  [_btn('看照片', uri=worker_portal.gallery_url(con, o['id']), primary=True)])
          for o in os_ for n in [{k: v for k, v in con.execute("SELECT label,COUNT(*) FROM attachments WHERE order_id=? AND label IN ('before','after') GROUP BY label", (o['id'],))}]]
    return _carousel(f'過去的完成照片（{len(bs)} 場）', bs)

def card_my(con, acc, uid, cid):
    """我的預約：報價（估價中／確認中／待簽約）＋工單（付款、找人、排定、服務、驗收）各一張卡，最多 10 張。"""
    import worker_portal
    t = worker_portal.cust_token(con, acc, uid, cid)
    today = dt.datetime.now(c.TZ).date().isoformat()
    bubbles = []
    for q in c.rows(con, "SELECT q.* FROM quotes q LEFT JOIN inquiries i ON i.id=q.inquiry_id WHERE (q.customer_id=? OR i.line_user_id=?) AND q.status='draft' "
                         "AND q.stage!='cancelled' ORDER BY q.created_at DESC LIMIT 3", (cid, uid)):
        step, desc = {'photos': ('估價中', '請傳現況照片，收齊後交給主管報價。'), 'review': ('報價確認中', '主管正在確認報價，確認後正式報價單會傳到這裡。'),
                      'site_visit': ('現場估價安排中', '專人會跟您約現場估價的時間。'), 'sent': ('待簽約', '正式報價單已出爐，請查看並線上簽約。')}.get(q['stage'], (q['stage'], ''))
        f = json.loads(q['form'] or '{}')
        bubbles.append(_bubble(_header(step, f.get('service_type') or '報價', brand=acc), [_row('報價單', q['id']), _t(desc, wrap=True, color=SUB, size='sm', margin='md')],
                               [_btn('查看報價單' if q['stage'] != 'sent' else '查看並簽約', uri=f"{BASE_URL}/q/{q['token']}", primary=True)]))
    for o in c.rows(con, "SELECT * FROM orders WHERE customer_id=? AND (date>=? OR (status='completed' AND date>=?)) AND status NOT IN ('archived') ORDER BY date,start LIMIT 8",
                    (cid, today, (dt.datetime.now(c.TZ).date() - dt.timedelta(days=7)).isoformat())):
        q = con.execute('SELECT token FROM quotes WHERE order_id=?', (o['id'],)).fetchone()
        step, desc, btns = _progress(con, o, q)
        o2, s, cu, crew, area = order_ctx(con, o['id'])
        if o['status'] not in ('cancelled', 'completed'):
            btns = btns + [_btn('改期／取消', data=f"a=c_chg&o={o['id']}")]
        elif o['status'] == 'completed' and con.execute("SELECT 1 FROM line_outbox WHERE order_id=? AND kind='finish_c' AND status NOT LIKE 'skipped%'", (o['id'],)).fetchone():
            btns = btns + [_btn('看完成照片', uri=worker_portal.gallery_url(con, o['id']), primary=not btns)]      # 完成照片事後也找得到
        bubbles.append(_bubble(_header(step, when(dict(o)), brand=acc), [_row('地點', s.get('name') or area or '—'), _row('服務', s.get('service_type') or o['title']),
                               _t(desc, wrap=True, color=SUB, size='sm', margin='md')], btns or [_btn('打開報價單', uri=f"{BASE_URL}/q/{q['token']}")] if (btns or q) else []))
    tail = _bubble(_header('我的資料', c.get(con, 'customers', cid)['name'], brand=acc), [_t('地址、門禁、停車、寵物、注意事項都可以在這裡更新；電話要改請直接留言給我們。', wrap=True, color=SUB, size='sm')],
                   [_btn('修改我的資料', uri=f'{BASE_URL}/site?t={t}', primary=True), _btn('再預約一次', uri=f'{BASE_URL}/book?b={acc}&t={t}')]
                   + ([_btn('過去的完成照片', data='a=c_photos')] if _photo_orders(con, cid, 1) else [])
                   + [_btn('推薦給朋友 🎁', uri=__import__('referral').share_url(con, acc, cid)), _btn('個資查詢／刪除', data='a=c_pd')])      # 推薦給朋友
    if not bubbles:
        tail['body']['contents'].insert(0, _t('目前沒有進行中的預約。', weight='bold'))
    return [{'type': 'flex', 'altText': f'我的預約（{len(bubbles)} 筆）', 'contents': {'type': 'carousel', 'contents': (bubbles + [tail])[:10]}}]

def _ord_line(con, o):
    o2, s, cu, crew, area = order_ctx(con, o['id'])
    who = '、'.join(a['name'] for a in crew) or '缺人'
    return f"{o['start']}–{o['end']} {cu.get('name') or s.get('name') or o['title']}（{area or '—'}）・{who}"

TOWNS = ['大安區', '板橋區', '蘇澳鎮', '東區', '中壢區', '壯圍鄉', '東區', '三民區', '西屯區', '三星鄉', '大同鄉', '南澳鄉']

def _sh(kicker, title, color=BROWN):
    """管理層卡片的抬頭：右上角「✕ 重來」＝清掉目前的查詢，回到管理首頁（LINE 不能刪訊息，用這個重新開始）。"""
    h = _header(kicker, title, color)
    h['contents'] = [{'type': 'box', 'layout': 'horizontal', 'contents': [
        _t(BRAND.get('demoshop') or 'DEMO', size='xxs', color=SUB, weight='bold', flex=5, gravity='center'),
        {'type': 'box', 'layout': 'vertical', 'flex': 3, 'cornerRadius': '12px', 'backgroundColor': '#EFEDE8', 'paddingAll': '5px',
         'action': {'type': 'postback', 'label': '重來', 'data': 'a=m_home', 'displayText': '重來'},
         'contents': [_t('✕ 重來', size='xs', color=INK, align='center', weight='bold')]}]}] + h['contents'][1:]
    return h

def _carousel(alt, bubbles, more=None):
    """最多 10 張；還有更多就加一張「下一頁」。"""
    bs = bubbles[:10]
    if more:
        bs.append({'type': 'bubble', 'size': 'mega', 'body': {'type': 'box', 'layout': 'vertical', 'justifyContent': 'center', 'paddingAll': '18px', 'contents': [
            _t(f'還有 {more[1]} 筆', size='sm', color=SUB, align='center'), _btn('下一頁 ›', data=more[0], primary=True)]}})
    return [{'type': 'flex', 'altText': alt[:390], 'contents': {'type': 'carousel', 'contents': bs}}]

def _cust_bubble(con, cu, uid):
    today = dt.datetime.now(c.TZ).date().isoformat()
    nxt = con.execute("SELECT * FROM orders WHERE customer_id=? AND date>=? AND status NOT IN ('cancelled','archived') ORDER BY date,start LIMIT 1", (cu['id'], today)).fetchone()
    last = con.execute("SELECT date FROM orders WHERE customer_id=? AND date<? AND status NOT IN ('cancelled') ORDER BY date DESC LIMIT 1", (cu['id'], today)).fetchone()
    s = con.execute('SELECT * FROM sites WHERE customer_id=? LIMIT 1', (cu['id'],)).fetchone()
    import board
    lined = con.execute('SELECT 1 FROM line_users WHERE customer_id=?', (cu['id'],)).fetchone()
    rows = [_row('電話', cu['phone'] or '—'), _row('地點', ((board.area_of(dict(s)) or '') + ' ' + (s['address'] or '')).strip() if s else '—'),
            _row('上次', md(last[0]) if last else '—'), _row('下次', f"{md(nxt['date'])} {nxt['start']}" if nxt else '—'), _row('LINE', '已綁定' if lined else '還沒綁'),
            _row('結帳', (cu['billing_rule'] or '—')[:30] if uid in money_users(con) else '（店長可看）')]
    return _bubble(_sh('客戶・' + (cu['channel'] or brands.name(brands.of_customer(dict(cu)))), cu['name']), rows,
                   [_btn('打開下次工單', uri=f"{BASE_URL}/#o/{nxt['id']}", primary=True)] if nxt else [_btn('打開客戶', uri=f"{BASE_URL}/#c/{cu['id']}", primary=True)])

def _worker_bubble(con, w):
    n = con.execute("SELECT COUNT(*) FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.date>=?",
                    (w['id'], dt.datetime.now(c.TZ).date().isoformat())).fetchone()[0]
    import automation
    bl = automation.is_blacklisted(con, w['id'])
    line = bool(con.execute('SELECT 1 FROM line_users WHERE worker_id=? AND is_friend=1', (w['id'],)).fetchone())
    btns = []
    if w['status'] != 'active':          # 舊夥伴找回：店長打電話問，可以的「恢復可派」，不行的「不要了」
        btns += [_btn('恢復可派工', data=f"a=wr_ok&w={w['id']}", primary=True), _btn('不要了（暫停）', data=f"a=wr_no&w={w['id']}")]
    btns += [_btn('移出黑名單' if bl else '🚫 不再派給他', data=f"a=wbl&w={w['id']}&on={0 if bl else 1}")]
    if w['phone']:
        btns.append(_btn('打電話', uri='tel:' + re.sub(r'[^0-9+]', '', w['phone'])))
    btns.append(_btn('打開夥伴資料', uri=f"{BASE_URL}/#w/{w['id']}"))
    return _bubble(_sh('夥伴', w['name'], '#2E7D32'), [_row('電話', w['phone'] or '—'), _row('區域', (w['regions'] or '—')[:40]), _row('會做', (w['skills'] or '—')[:30]),
                   _row('接下來', f'{n} 場'), _row('LINE', '已綁定' if line else '還沒綁（推播不到）'),
                   _row('狀態', '🚫 停派（黑名單）' if bl else {'active': '可派工', 'review': '待核對', 'paused': '暫停'}.get(w['status'], w['status']))], btns[:5])

def _area_pick(con, acc, ev, act, title):
    """點選地區（快速回覆，免打字）。"""
    items = [{'type': 'action', 'action': {'type': 'postback', 'label': t, 'data': f'a={act}&area={t}', 'displayText': f'{title}：{t}'}} for t in TOWNS] + \
            [{'type': 'action', 'action': {'type': 'postback', 'label': '全部', 'data': f'a={act}&area=', 'displayText': f'{title}：全部'}}]
    return reply(con, acc, ev['replyToken'], [{'type': 'text', 'text': f'{title}：請點下方要看哪一區 👇', 'quickReply': {'items': items[:13]}}], 'menu')

def _overview(con, uid):
    """營運總覽：依身分給資料——客服看營運；負責人、店長（money_users）再加財務。"""
    td = dt.datetime.now(c.TZ).date(); t = td.isoformat(); wk = (td + dt.timedelta(days=6)).isoformat(); m = t[:7]
    live = "status NOT IN ('cancelled','archived')"
    n = lambda sql, a=(): con.execute(sql, a).fetchone()[0] or 0
    short = sum(1 for o in c.rows(con, f"SELECT id,required_workers FROM orders WHERE date>=? AND date<=? AND {live} AND status!='completed'", (t, wk))
                if n("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)) < (o['required_workers'] or 1))
    rows = [_row('今天', f"{n(f'SELECT COUNT(*) FROM orders WHERE date=? AND {live}', (t,))} 場"),
            _row('7 天內', f"{n(f'SELECT COUNT(*) FROM orders WHERE date>=? AND date<=? AND {live}', (t, wk))} 場・缺人 {short} 場"),
            _row('等客戶確認', f"{n(f'SELECT COUNT(*) FROM orders WHERE date>=? AND {live} AND status IN (?,?) AND COALESCE(customer_confirmed,0)=0', (t, 'review', 'draft'))} 場"),
            _row('待完工登記', f"{n('SELECT COUNT(*) FROM orders WHERE status=? AND date<?', ('confirmed', t))} 場"),
            _row('報價待確認', f"{n('SELECT COUNT(*) FROM quotes WHERE status=? AND stage IN (?,?)', ('draft', 'review', 'site_visit'))} 張"),
            _row('新預約需求', f"{n('SELECT COUNT(*) FROM inquiries WHERE status=?', ('new',))} 筆"),
            _row('可派工夥伴', f"{n('SELECT COUNT(*) FROM workers WHERE status=?', ('active',))} 位（待核對 {n('SELECT COUNT(*) FROM workers WHERE status=?', ('review',))}）"),
            _row('綁定待確認', f"{n('SELECT COUNT(*) FROM bind_requests WHERE status=?', ('open',))} 筆")]
    if uid in money_users(con):
        import payments
        payments.ensure(con)
        money = lambda cents: f"NT$ {int(cents or 0) // 100:,}"
        od = "SELECT {} FROM payment_requests WHERE status='pending' AND due!='' AND due<?"
        od_n, od_sum = n(od.format('COUNT(*)'), (t,)), n(od.format('SUM(amount)'), (t,))
        rows += [{'type': 'separator', 'margin': 'lg', 'color': LINE_C}, _t('財務', size='xs', color=SUB, weight='bold', margin='lg'),
                 _row('本月已完工', money(n(f"SELECT SUM(approved_amount) FROM orders WHERE status='completed' AND date LIKE ?", (m + '%',)))),
                 _row('本月預計', money(n(f"SELECT SUM(approved_amount) FROM orders WHERE date LIKE ? AND {live}", (m + '%',)))),
                 _row('待確認收款', f"{n('SELECT COUNT(*) FROM payment_requests WHERE status=?', ('reported',))} 筆・{money(n('SELECT SUM(amount) FROM payment_requests WHERE status=?', ('reported',)))}"),
                 _row('逾期未付', f"{od_n} 筆・{money(od_sum)}"),
                 _row('本月已收', money(n("SELECT SUM(amount) FROM payment_requests WHERE status='confirmed' AND confirmed_at LIKE ?", (m + '%',))))]
    return flex('營運總覽', _bubble(_sh('營運總覽', f"{md(t)}"), rows,
        [_btn('7 天內缺人', data='a=m_short', primary=True), _btn('報價待確認', data='a=m_quotes'), _btn('查詢客戶・夥伴', data='a=m_search')],
        '金額只有負責人、店長看得到。' if uid in money_users(con) else ''))

def _staff_menu(con, acc, uid, lu, act, ev, data=None):
    """管理層選單：直接在 LINE 看資料庫常用的東西，全部用點的；點了打開營運中樞對應頁面。"""
    if uid not in staff_users(con):
        return reply(con, acc, ev['replyToken'], _txt('這是管理層專用功能。'))
    data = data or {}
    today = dt.datetime.now(c.TZ).date().isoformat()
    page = max(1, int(data.get('p') or 1)); per = 10
    if act == 'm_overview':
        return reply(con, acc, ev['replyToken'], _overview(con, uid), 'menu')
    if act == 'm_view':                                       # 選單「測試視角」：客戶／夥伴二選一
        return reply(con, acc, ev['replyToken'], flex('切換測試視角', _bubble(_header('測試視角', '用示範資料試試看'),
            [_t('切過去之後，你的 LINE 會暫時變成客戶或夥伴看到的樣子（示範資料，不影響真的客戶）。打「回管理」切回來。', wrap=True, size='sm')],
            [_btn('客戶視角', data='a=view&v=customer', primary=True), _btn('夥伴視角', data='a=view&v=worker')])), 'menu')
    if act == 'm_dups':                                       # 夥伴可能重複（去重）
        return reply(con, acc, ev['replyToken'], card_worker_dups(con), 'menu')
    if act == 'm_tri':
        if uid not in money_users(con):
            return reply(con, acc, ev['replyToken'], _txt('待整理只有店長（和負責人）看得到。'))
        return reply(con, acc, ev['replyToken'], card_triage(con, page), 'menu')
    if act == 'm_home':
        return reply(con, acc, ev['replyToken'], flex('管理選單', _bubble(_sh('管理選單', '要看什麼？'), [_t('全部用點的，不用打字。', color=SUB, size='sm')],
            [_btn('營運總覽', data='a=m_overview', primary=True), _btn('今天的排班', data='a=m_today'), _btn('明天的排班', data='a=m_tomorrow'), _btn('7 天內缺人', data='a=m_short'),
             _btn('報價待確認', data='a=m_quotes'), _btn('查詢客戶・夥伴', data='a=m_search'), _btn('找回舊夥伴（待核對）', data='a=m_wk&s=review'), _btn('夥伴可能重複', data='a=m_dups'), *([_btn('待整理（交給你決定）', data='a=m_tri')] if uid in money_users(con) else []), _btn('新增資料', data='a=m_new')])), 'menu')
    if act in ('m_today', 'm_tomorrow', 'm_week'):
        d1 = today if act == 'm_today' else (dt.datetime.now(c.TZ).date() + dt.timedelta(days=1)).isoformat()
        d2 = d1 if act != 'm_week' else (dt.datetime.now(c.TZ).date() + dt.timedelta(days=6)).isoformat()
        rows = c.rows(con, "SELECT * FROM orders WHERE date>=? AND date<=? AND status NOT IN ('cancelled','archived') ORDER BY date,start", (d1, d2))
        name = {'m_today': '今天', 'm_tomorrow': '明天', 'm_week': '7 天內'}[act]
        body = [_t((md(o['date']) + ' ' if act == 'm_week' else '') + _ord_line(con, o), size='xs', wrap=True) for o in rows[:20]] or [_t(f'{name}沒有排服務。', color=SUB)]
        if len(rows) > 20:
            body.append(_t(f'還有 {len(rows) - 20} 場，請打開排班看全部。', size='xs', color=SUB))
        return reply(con, acc, ev['replyToken'], flex(f'{name} {len(rows)} 場', _bubble(_sh(f'{name}的排班', f"{md(d1)}{'～' + md(d2) if d2 != d1 else ''}・{len(rows)} 場"), body,
            [_btn('打開排班', uri=f'{BASE_URL}/#schedule', primary=True)])), 'menu')
    if act == 'm_today':
        rows = c.rows(con, "SELECT * FROM orders WHERE date=? AND status NOT IN ('cancelled','archived') ORDER BY start", (today,))
        body = [_t(_ord_line(con, o), size='xs', wrap=True) for o in rows[:15]] or [_t('今天沒有排服務。', color=SUB)]
        return reply(con, acc, ev['replyToken'], flex(f'今天 {len(rows)} 場', _bubble(_header('今天的排班', f'{md(today)}・{len(rows)} 場'), body,
            [_btn('打開排班', uri=f'{BASE_URL}/#schedule', primary=True), _btn('明天的排班', data='a=m_tomorrow'), _btn('營運總覽', data='a=m_overview')])), 'menu')
    if act == 'm_todo':                                        # 待處理：要決定、要審核、報價、收款、功能回報
        import pending, sysreport
        m = pending.card(con)
        nrep = len([r for r in sysreport.listing(con) if r['status'] in ('open', 'need_decision')])
        more = flex('其他待處理', _bubble(_sh('其他待處理', '點了直接處理'), [_row('功能回報', f'{nrep} 件待處理')],
            [_btn('報價待確認', data='a=m_quotes', primary=True), _btn('待整理（交給你決定）', data='a=m_tri'), _btn('功能回報', uri=f'{BASE_URL}/#sysreport'),
             _btn('夥伴面試', uri=f'{BASE_URL}/#interviews')]))
        return reply(con, acc, ev['replyToken'], (m or _txt('要妳決定、審核的事目前都處理完了 👍')) + more, 'menu')
    if act == 'm_short':
        wk = (dt.datetime.now(c.TZ).date() + dt.timedelta(days=7)).isoformat()
        rows = [o for o in c.rows(con, "SELECT * FROM orders WHERE date>=? AND date<=? AND status IN ('review','draft','confirmed') ORDER BY date,start", (today, wk))
                if con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0] < o['required_workers']]
        if not rows:
            return reply(con, acc, ev['replyToken'], _txt('7 天內沒有缺人的工作 👍'))
        bubbles = [_bubble(_sh('缺人', f"{md(o['date'])} {o['start']}", '#C62828'), [_t(_ord_line(con, o), wrap=True, size='sm')],
                           [_btn('挑人派工', uri=f"{BASE_URL}/#o/{o['id']}", primary=True)]) for o in rows[:10]]
        return reply(con, acc, ev['replyToken'], _carousel(f'7 天內缺人 {len(rows)} 場', bubbles), 'menu')
    if act == 'm_quotes':
        rows = c.rows(con, "SELECT id,form,total,stage,mode FROM quotes WHERE status='draft' AND stage IN ('review','site_visit','photos') ORDER BY created_at DESC LIMIT 12")
        mon = uid in money_users(con)
        body = [_row(r['id'][4:], f"{(json.loads(r['form']).get('company') or json.loads(r['form']).get('name'))}・{'現場估價' if r['mode'] == 'site_visit' else ('NT$ ' + format(r['total'] // 100, ',') if mon else '待確認')}"
                     + {'photos': '（等照片）'}.get(r['stage'], '')) for r in rows] or [_t('沒有待確認的報價 👍', color=SUB)]
        return reply(con, acc, ev['replyToken'], flex(f'待確認報價 {len(rows)} 張', _bubble(_sh('報價待確認', f'{len(rows)} 張'), body,
            [_btn('打開報價審核', uri=f'{BASE_URL}/#quotes', primary=True)])), 'menu')
    if act == 'm_search':        # 查詢：全部點選（也還是可以直接傳「查 王小姐」）
        return reply(con, acc, ev['replyToken'], [{'type': 'flex', 'altText': '要查什麼？', 'contents': {'type': 'carousel', 'contents': [
            _bubble(_sh('查客戶', '要看哪些客戶？'), [_t('點了就列出來，不用打字。', color=SUB, size='sm')],
                    [_btn('依地區', data='a=m_cs_area', primary=True), _btn('最近 30 天有服務', data='a=m_cs&k=recent'), _btn('最近新增', data='a=m_cs&k=new'), _btn('還沒綁 LINE', data='a=m_cs&k=unbound')]),
            _bubble(_sh('查夥伴', '要看哪些夥伴？', '#2E7D32'), [_t('點了就列出來，不用打字。', color=SUB, size='sm')],
                    [_btn('可派工', data='a=m_wk&s=active', primary=True), _btn('待核對（新應徵）', data='a=m_wk&s=review'), _btn('依地區', data='a=m_wk_area')]),
            _bubble(_sh('查工單', '要看哪段時間？'), [_t('點了就列出來，不用打字。', color=SUB, size='sm')],
                    [_btn('今天', data='a=m_today', primary=True), _btn('明天', data='a=m_tomorrow'), _btn('7 天內', data='a=m_week'), _btn('7 天內缺人', data='a=m_short')]),
            _bubble(_sh('新增資料', '點了直接打開表單'), [_t('新增或修改的資料，系統會自動通知相關的客戶、夥伴和派單管理群。', color=SUB, size='sm', wrap=True)],
                    [_btn('新增工單（排班）', uri=f'{BASE_URL}/#new/order', primary=True), _btn('新增客戶', uri=f'{BASE_URL}/#new/customer'),
                     _btn('新增夥伴', uri=f'{BASE_URL}/#new/worker'), _btn('新增報價單', uri=f'{BASE_URL}/#new/quote')])]}}], 'menu')
    if act == 'm_cs_area':
        return _area_pick(con, acc, ev, 'm_cs', '客戶地區')
    if act == 'm_wk_area':
        return _area_pick(con, acc, ev, 'm_wk', '夥伴地區')
    if act == 'm_cs':
        import board
        k, area = data.get('k', ''), data.get('area')
        if k == 'recent':
            cus = c.rows(con, "SELECT c.*, MAX(o.date) d FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.date>=? AND o.date<=? AND o.status NOT IN ('cancelled') "
                              "GROUP BY c.id ORDER BY d DESC", ((dt.datetime.now(c.TZ).date() - dt.timedelta(days=30)).isoformat(), today)); title = '最近 30 天有服務'
        elif k == 'new':
            cus = c.rows(con, "SELECT * FROM customers WHERE status!='ended' ORDER BY rowid DESC LIMIT 60"); title = '最近新增'
        elif k == 'unbound':
            cus = c.rows(con, "SELECT * FROM customers c WHERE status IN ('active','lead') AND NOT EXISTS (SELECT 1 FROM line_users l WHERE l.customer_id=c.id) ORDER BY status,name"); title = '還沒綁 LINE'
        else:
            cus = []
            for cu in c.rows(con, "SELECT * FROM customers WHERE status IN ('active','paused','lead') ORDER BY status,name"):
                s = con.execute('SELECT * FROM sites WHERE customer_id=? LIMIT 1', (cu['id'],)).fetchone()
                if not area or (s and area[:2] in (board.area_of(dict(s)) + (s['address'] or ''))):
                    cus.append(cu)
            title = f"客戶・{area or '全部地區'}"
        if not cus:
            return reply(con, acc, ev['replyToken'], _txt(f'{title}：目前沒有資料。'))
        chunk = cus[(page - 1) * per: page * per]; left = len(cus) - page * per
        more = (f"a=m_cs&k={k}&area={area or ''}&p={page + 1}", left) if left > 0 else None
        return reply(con, acc, ev['replyToken'], _carousel(f'{title}：{len(cus)} 位（第 {page} 頁）', [_cust_bubble(con, cu, uid) for cu in chunk], more), 'menu')
    if act == 'm_wk':
        st, area = data.get('s', ''), data.get('area')
        ws = c.rows(con, "SELECT * FROM workers WHERE status IN ('active','review','paused')" + (' AND status=?' if st else '') + ' ORDER BY status,name', (st,) if st else ())
        if area:
            ws = [w for w in ws if area[:2] in (w['regions'] or '') or '臺北市全區' in (w['regions'] or '')]
        if st == 'active':          # 「可派工」不含黑名單（停派）
            import automation
            ws = [w for w in ws if not automation.is_blacklisted(con, w['id'])]
        title = f"夥伴・{ {'active': '可派工', 'review': '待核對'}.get(st, '') or (area or '全部')}"
        if not ws:
            return reply(con, acc, ev['replyToken'], _txt(f'{title}：目前沒有資料。'))
        chunk = ws[(page - 1) * per: page * per]; left = len(ws) - page * per
        more = (f"a=m_wk&s={st}&area={area or ''}&p={page + 1}", left) if left > 0 else None
        return reply(con, acc, ev['replyToken'], _carousel(f'{title}：{len(ws)} 位（第 {page} 頁）', [_worker_bubble(con, w) for w in chunk], more), 'menu')
    if act == 'm_new':
        return reply(con, acc, ev['replyToken'], flex('新增資料', _bubble(_sh('新增資料', '點了直接打開表單'), [_t('新增或修改的資料，系統會自動通知相關的客戶、夥伴和派單管理群。', wrap=True, color=SUB, size='sm')],
            [_btn('新增工單（排班）', uri=f'{BASE_URL}/#new/order', primary=True), _btn('新增客戶', uri=f'{BASE_URL}/#new/customer'),
             _btn('新增夥伴', uri=f'{BASE_URL}/#new/worker'), _btn('新增報價單', uri=f'{BASE_URL}/#new/quote')])), 'menu')
    return reply(con, acc, ev['replyToken'], _txt('有需要直接打開營運中樞。'))

# ───────────── 收款（第二批） ─────────────
def card_payment(con, acc, rows, token, title='付款通知 🧾', kicker='簽約完成，請依期限付款'):
    co = booking_company(con, acc)
    body = [_row(r['label'], f"NT$ {r['amount'] // 100:,}" + (f"（{md(r['due'])} 前）" if r['due'] else f"（{r.get('note') or '完工後'}）")) for r in rows]
    if co.get('bank'):
        body += [{'type': 'separator', 'margin': 'lg', 'color': LINE_C}, _row('銀行', co['bank']), _row('戶名', co.get('holder', '')), _row('帳號', co.get('account', ''))]
    import linepay
    qd = con.execute('SELECT id,account FROM quotes WHERE token=?', (token,)).fetchone() if token else None
    lp = (linepay.live(con, acc) or bool(qd and linepay.demo_ok(con, qd))) and any(linepay.allowed(con, r) for r in rows) if token else False      # 服務前的訂金／全額也能用 LINE Pay（簽收完就能付）
    btns = ([_btn('用 LINE Pay 付款', uri=f'{BASE_URL}/q/{token}#pay', primary=True)] if lp else []) + ([_btn('我已匯款，回報末五碼', uri=f'{BASE_URL}/q/{token}#pay', primary=not lp)] if token else [])
    before = any(r['kind'] in ('deposit', 'full', 'month') for r in rows)      # 稽核：尾款、當日結算、月結不用等收款才排定
    return flex(f'{title}：' + '、'.join(f"{r['label']} NT$ {r['amount'] // 100:,}" for r in rows), _bubble(_header(kicker, title, brand=acc), body, btns,
                ('收到款項後會正式排定服務並通知服務人員。' if before else '') + '匯款後請按上面按鈕回報，方便我們對帳。'))

def booking_company(con, acc):
    import booking
    return booking.company(con, acc)

def _cust_to(con, acc, uid, cid):
    if uid:
        return uid
    a2, to = customer_target(con, cid) if cid else (None, None)
    return to if a2 == acc else None

def on_payment_notice(con, acc, uid, cid, token, rows, title='付款通知 🧾', kicker='簽約完成，請依期限付款'):
    to = _cust_to(con, acc, uid, cid)
    rows = [r for r in rows if r['status'] == 'pending'] if rows and 'status' in rows[0] else rows
    if to and rows:
        m = card_payment(con, acc, rows, token, title, kicker); queue(con, acc, to, 'payment', m[0]['altText'], m)
    return bool(to)

def on_payment_reported(con, row, cust_name):
    """客戶回報已匯款 → 派單群（和負責人、店長）一張「確認收款」卡；只有看得到金額的人按得動。"""
    g = admin_group(con)
    m = flex(f"💰 {cust_name} 回報已匯款 NT$ {row['amount'] // 100:,}", _bubble(_header('客戶回報已匯款', cust_name, '#2E7D32'),
             [_row('款項', row['label']), _row('金額', f"NT$ {row['amount'] // 100:,}"), _row('末五碼', row['ref'] or '—'), _row('期限', md(row['due']) if row['due'] else '—')],
             [_btn('確認收款', data=f"a=pay_ok&p={row['id']}", primary=True), _btn('看收款紀錄', uri=f'{BASE_URL}/#payhist')], '請先到網銀核對入帳，再按確認收款。'))
    for to in ([g] if g else []) + [u for u in money_users(con) if u not in staff_users(con) or not g]:
        queue(con, WORK_ACCOUNT, to, 'admin', m[0]['altText'], m)

def _pay_ok(con, acc, uid, data, ev):
    import payments
    if uid not in money_users(con):
        return reply(con, acc, ev['replyToken'], _txt('確認收款只有負責人、店長可以按。'))
    payments.ensure(con)
    r = con.execute('SELECT * FROM payment_requests WHERE id=?', (data.get('p', ''),)).fetchone()
    if not r:
        return reply(con, acc, ev['replyToken'], _txt('找不到這筆款項。'))
    if r['status'] == 'confirmed':
        return reply(con, acc, ev['replyToken'], _txt(f"這筆已經確認過了（{r['confirmed_by']}）。"))
    who = (con.execute('SELECT display_name FROM line_users WHERE user_id=? LIMIT 1', (uid,)).fetchone() or ['管理員'])[0]
    payments.confirm(con, r['id'], who)
    after_payment_confirmed(con, r['id'])
    return reply(con, acc, ev['replyToken'], _txt(f"✅ 已確認收款：{r['label']} NT$ {r['amount'] // 100:,}"))

def after_payment_confirmed(con, pid):
    """收款確認後：通知客戶；開工前款項都收齊了就試著排定（人到齊＋客戶確認）。"""
    import payments
    r = con.execute('SELECT * FROM payment_requests WHERE id=?', (pid,)).fetchone()
    q = con.execute('SELECT account,inquiry_id FROM quotes WHERE id=?', (r['quote_id'],)).fetchone() if r['quote_id'] else None
    acc = (q['account'] if q else None) or 'demoshop'
    inq = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone() if q and q['inquiry_id'] else None
    to = _cust_to(con, acc, inq[0] if inq and inq[0] else None, r['customer_id'])
    if to:
        ost = con.execute('SELECT status FROM orders WHERE id=?', (r['order_id'] or '',)).fetchone()
        tail = ('\n這筆服務已經取消，退款或抵用我們會另外跟您聯絡。' if ost and ost[0] == 'cancelled'      # 稽核：文字照實際狀況
                else '\n我們會盡快排定服務並通知您。' if r['kind'] in ('deposit', 'full', 'month') and not (ost and ost[0] in ('confirmed', 'completed')) else '')
        m = flex(f"已收到您的款項 NT$ {r['amount'] // 100:,}", _bubble(_header('✅ 已收到您的款項', md(c.now()[:10]), '#2E7D32', brand=acc),      # 業主訊息一律圖卡
                 [_row('款項', r['label']), _row('金額', f"NT$ {r['amount'] // 100:,}"), _row('方式', 'LINE Pay' if (r['ref'] or '').startswith('LP') or 'LINE Pay' in (r['note'] or '') else '匯款／收款')]
                 + [_t('謝謝您！' + tail.strip(), wrap=True, size='sm', color=SUB, margin='md')]))
        queue(con, acc, to, 'payment', m[0]['altText'], m)
    if r['order_id'] and payments.cleared_for_schedule(con, r['order_id']):
        _auto_confirm(con, r['order_id'])

def payments_daily(con):
    """每天一次：付款期限前 3 天、前 1 天提醒客戶；逾期通知派單群；完工後建尾款期限並通知客戶。"""
    import payments
    if c.now()[11:16] < '10:00' or ix.setting(con, 'pay_day') == c.now()[:10]:
        return
    ix.set_setting(con, 'pay_day', c.now()[:10])
    payments.ensure(con)
    today = dt.datetime.now(c.TZ).date().isoformat()
    for r in c.rows(con, "SELECT p.*,o.completed_at,o.date odate FROM payment_requests p JOIN orders o ON o.id=p.order_id WHERE p.kind IN ('balance','after') AND p.status='pending' AND p.due='' AND o.status='completed' "
                         "AND (COALESCE(o.acceptance,'')!='disputed' OR COALESCE(o.feedback_done,'')!='')"):        # 稽核：業主說要加強 → 先處理，暫緩尾款通知；按「已處理」後照常
        payments.balance_due_on_complete(con, r['order_id'], (r['completed_at'] or r['odate'] or today)[:10])
        r2 = dict(con.execute('SELECT * FROM payment_requests WHERE id=?', (r['id'],)).fetchone())
        q = con.execute('SELECT account,token,inquiry_id FROM quotes WHERE id=?', (r['quote_id'],)).fetchone()
        if q and payments.mark(con, r['id'], 'balance_notice'):
            inq = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone() if q['inquiry_id'] else None
            on_payment_notice(con, q['account'], inq[0] if inq and inq[0] else None, r['customer_id'], q['token'], [r2], '尾款通知 🧾' if r['kind'] == 'balance' else '付款通知 🧾', '感謝您，服務已完工')
    for r, k in payments.reminders(con, today):
        q = con.execute('SELECT account,token,inquiry_id FROM quotes WHERE id=?', (r['quote_id'],)).fetchone() if r['quote_id'] else None
        acc = q['account'] if q else 'demoshop'
        if k in ('remind_3', 'remind_1') and payments.mark(con, r['id'], k):
            inq = con.execute('SELECT line_user_id FROM inquiries WHERE id=?', (q['inquiry_id'],)).fetchone() if q and q['inquiry_id'] else None
            on_payment_notice(con, acc, inq[0] if inq and inq[0] else None, r['customer_id'], q['token'] if q else None, [r],
                              '付款提醒 ⏰', f"{'3' if k == 'remind_3' else '1'} 天後到期")
        elif k == 'overdue' and payments.mark(con, r['id'], 'overdue'):
            cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (r['customer_id'],)).fetchone() or {}) if r['customer_id'] else {}     # 客戶被合併／刪除也不要讓整批提醒出錯
            admin_notify(con, '⚠️ 款項逾期未付', [('客戶', f"{cu.get('name', '')}（{cu.get('phone', '')}）"), ('款項', f"{r['label']} NT$ {r['amount'] // 100:,}"),
                                             ('期限', md(r['due'])), ('處理', '請店長決定保留或取消這次服務')], '', '#C62828', oid=r['order_id'])

def gap_card(con, aid_new, aid_other, gap):
    """問店長：同一位夥伴同天兩場只隔 gap 分，趕得上嗎？（dispatch.gap_watch）"""
    import board
    def info(aid):
        return dict(con.execute("SELECT o.id,o.date,o.start,o.end,o.title,s.area,s.address,w.name FROM assignments a JOIN orders o ON o.id=a.order_id "
                                "LEFT JOIN sites s ON s.id=o.site_id JOIN workers w ON w.id=a.worker_id WHERE a.id=?", (aid,)).fetchone())
    n, x = info(aid_new), info(aid_other)
    first, second = sorted([n, x], key=lambda r: r['start'])
    place = lambda r: f"{r['start']}–{r['end']} {board.area_of(r)} {r['title'][:10]}".strip()
    m = flex(f"趕得上嗎？{n['name']} {md(n['date'])} 兩場只隔 {gap} 分", _bubble(_header('🚗 趕得上嗎？', f"{n['name']}・{md(n['date'])} 兩場只隔 {gap} 分", '#C47F00'),
        [_row('前一場', place(first)), _row('下一場', place(second)), _row('間隔', f'{gap} 分鐘')],
        [_btn('趕得上 ✅', data=f"a=gap&n={aid_new}&x={aid_other}&v=1", primary=True), _btn(f"趕不上，{n['title'][:6]}換人", data=f"a=gap&n={aid_new}&x={aid_other}&v=0"),
         _btn('看工單', uri=f"{BASE_URL}/#o/{n['id']}")],
        '妳回答後系統會記住：同樣兩地、這個間隔以上就不再問；趕不上會自動換人另外派。'))
    to_managers(con, 'gap_ask', m[0]['altText'], m, n['id'])          # 送管理群 @店長
    return m

def to_managers(con, kind, alt, msgs, order_id='', tag='', who=None):
    """不私訊店長 → 送派單管理群，第一則 @店長（tag 是一句話；空白就用 alt）。沒有管理群才一對一給店長。"""
    g = admin_group(con)
    if g:
        head = [ping(con, tag or alt, who=sorted(who or deciders(con)))]
        return queue(con, WORK_ACCOUNT, g, kind, alt, (head + list(msgs))[:5], order_id)
    for u in deciders(con):
        queue(con, WORK_ACCOUNT, u, kind, alt, list(msgs)[:5], order_id)

def photo_redo(con, oid, who):
    """請夥伴補拍完成照片（LINE「請夥伴補拍」、中控台工單共用）。"""
    import board, worker_portal
    order = con.execute('SELECT * FROM orders WHERE id=?', (oid or '',)).fetchone()
    if not order:
        raise ValueError('這筆工作已經不存在了')
    board.log(con, oid, 'photo_redo', who)
    for a in c.rows(con, "SELECT id,worker_id FROM assignments WHERE order_id=? AND status='accepted'", (oid,)):
        m = flex('完成照片請補拍', _bubble(_header('請補拍完成照片 📷', when(dict(order)), '#C47F00'),
                 [_t('示範服務店看過照片了，請再補拍幾張（每個服務的區域都要清楚），補完按工作頁上傳。', wrap=True)],
                 [_btn('打開工作頁補拍', uri=worker_portal.work_url(con, a['id']), primary=True)]))
        to_worker(con, a['worker_id'], 'photo_redo', m[0]['altText'], m, oid)
    return f'已請夥伴補拍（{who}）'

def privacy_decide(con, rid, ok, who):
    """客戶申請刪除個資：核准（刪除＋通知客戶）／先不要（LINE、中控台共用）。"""
    import privacy
    privacy.ensure(con)
    r = con.execute('SELECT * FROM privacy_requests WHERE id=?', (rid or '',)).fetchone()
    if not r or r['status'] != 'pending':
        return '這筆申請已經處理過了。'
    if not ok:
        con.execute("UPDATE privacy_requests SET status='hold',decided_by=?,decided_at=? WHERE id=?", (who, c.now(), r['id']))
        return '好的，先不刪，請記得聯絡客戶說明原因（例如還有預約或款項未結清）。'
    c.CTX.actor = who + '（核准個資刪除）'
    try:
        tag = privacy.erase(con, r['customer_id'], who)
    except ValueError as e:
        return f'還不能刪：{e}'
    queue(con, r['account'], r['line_user'], 'privacy_done', '您的個資已刪除', _txt('您申請刪除的個人資料已處理完成 🙏 之後如需服務，歡迎重新預約。'))
    return f'✅ 已刪除（去識別化為「{tag}」），也已通知客戶。'

def worker_dup_decide(con, k, d, same, who):
    """可能重複的夥伴：是同一人 → 合併；不是 → 記下不再問（LINE、中控台共用）。"""
    import board
    if not con.execute('SELECT 1 FROM workers WHERE id=?', (d,)).fetchone() or not con.execute('SELECT 1 FROM workers WHERE id=?', (k,)).fetchone():
        return '這組已經處理過了。'
    if same:
        c.CTX.actor = who + '（合併夥伴）'
        try:
            w = board.merge_workers(con, k, d)
        except ValueError as e:
            return f'沒辦法自動合併：{e}（請到中控台夥伴頁處理）'
        return f"✅ 已合併為「{w['name']}」（{who}）"
    no = _jset(con, 'worker_dup_no'); no.add('|'.join(sorted((k, d))))
    ix.set_setting(con, 'worker_dup_no', json.dumps(sorted(no)))
    return f'好的，記下來了：不是同一人（{who}）'

def group_decide(con, gid, ok):
    """新群組待確認：是我們的群組（照分類）／不是（不傳任何派工資料）。"""
    g = con.execute('SELECT * FROM line_groups WHERE group_id=?', (gid or '',)).fetchone()
    if not g or not (g['role'] or '').startswith('pending_'):
        return '這個群組已經處理過了。'
    if not ok:
        con.execute("UPDATE line_groups SET role='job',worker_id=NULL WHERE group_id=?", (g['group_id'],))
        return f"已標記「{g['name']}」不是我們的群組，不會傳任何派工資料過去。"
    classify(con, g['group_id'], g['name'], confirmed=True)
    return f"✅ 已確認「{g['name']}」"

def cmsg_done(con, acc, uid, by):
    """客戶留言已回覆（LINE「已回覆」、中控台「管理群待處理」共用）：停止提醒。"""
    for k in (f'cmsg:{acc}:{uid}', f'cmsg_txt:{acc}:{uid}', f'cmsg_rem:{acc}:{uid}', f'cmsg_open:{acc}:{uid}'):
        ix.set_setting(con, k, '')
    return f'✅ 已標記回覆（{by}）'

def photo_release(con, oid, who):
    """照片不齊、確認照常發薪（LINE「確認，照常發薪」、中控台共用）。"""
    if not con.execute('SELECT 1 FROM orders WHERE id=?', (oid or '',)).fetchone():
        raise ValueError('找不到這筆')
    if ix.setting(con, f'photo_ok:{oid}'):
        return '已經確認過了 ✅'
    ix.set_setting(con, f'photo_ok:{oid}', json.dumps({'by': who, 'at': c.now()}, ensure_ascii=False))
    c.audit(con, 'photo_ok', 'orders', oid, after={'by': who})
    return f'✅ {who} 已確認：這場薪資照常發放'

def photo_hold_card(con, o, hold):
    """稽核 H3：完工了但照片不齊（補登、中控台完工登記、系統自動完工）→ 薪資暫不發；派單管理群一張卡可以放行。"""
    g = admin_group(con)
    if not g:
        return
    _, s, cu, _, area = order_ctx(con, o['id'])
    m = flex('完工了但照片不齊，薪資暫不發', _bubble(_header('📷 照片不齊・薪資暫不發', f"{md(o['date'])} {s.get('name') or area or ''}".strip(), '#C47F00'),
             [_row(k, v) for k, v in order_people(con, o['id'])] + [_t(hold, wrap=True, margin='md'), _t('夥伴可以在工作頁補拍；確定是正常完工的，按「確認，照常發薪」這場薪資就會算。', wrap=True, size='sm', color=SUB)],
             [_btn('確認，照常發薪', data=f"a=fb_ok&o={o['id']}", primary=True), _btn('打開工單', uri=f"{BASE_URL}/#o/{o['id']}")]))
    queue(con, WORK_ACCOUNT, g, 'admin', m[0]['altText'], [ping(con, '完工了但照片不齊，請確認薪資 👇')] + m, o['id'])

def forgot_card(con, o, name, why):
    """夥伴忘了拍服務前照片（寫了原因）→ 派單管理群一張卡，管理層按「確認，照常發薪」這場薪資才發。"""
    g = admin_group(con)
    if not g:
        return
    _, s, cu, _, area = order_ctx(con, o['id'])
    m = flex(f'{name} 忘了拍服務前照片', _bubble(_header('📷 忘了拍服務前照片', f"{md(o['date'])} {s.get('name') or area or ''}".strip(), '#C47F00'),
             [_t(f'{name}：{why}', wrap=True), _t('完成照片照常拍、照常離場；這場薪資等管理層確認後才發。', wrap=True, size='sm')],
             [_btn('確認，照常發薪', data=f"a=fb_ok&o={o['id']}", primary=True), _btn('打開工單', uri=f"{BASE_URL}/#o/{o['id']}")]))
    queue(con, WORK_ACCOUNT, g, 'admin', m[0]['altText'], [ping(con, f'{name} 忘了拍服務前照片，請確認 👇')] + m, o['id'])

def ask_staff(con, topic, question, options, to=None, typed=True, ping=True, line=True):
    """用點選卡片問管理層（預設店長）：選項是快速回覆，回答會帶【topic】，系統記錄到 settings answer:<topic> 並通知負責人。
    typed＝多一顆「✏️ 我打字回答」：按了之後下一則文字就是答案（不用記得打【主題】）。"""
    explicit = bool(to)
    to = to or list(deciders(con))
    q = lambda t: {'type': 'action', 'action': {'type': 'message', 'label': t[:20], 'text': f'【{topic}】{t}'}}
    items = [q(o) for o in options][:12] + ([{'type': 'action', 'action': {'type': 'postback', 'label': '✏️ 我打字回答', 'data': f'a=ask_type&t={topic}', 'displayText': f'【{topic}】我打字回答'}}] if typed else [])
    # 快速回覆只有「最新一則」看得到 → 連發兩題時第一題的按鈕會被蓋掉（招募個資就是這樣沒人回）。
    # 改成卡片按鈕（一直都按得到；按了一樣送出【主題】答案），快速回覆照樣附在卡片上。
    bs = [{'type': 'button', 'action': {'type': 'message', 'label': o[:20], 'text': f'【{topic}】{o}'}, 'style': 'primary' if i == 0 else 'secondary',
           'color': BROWN if i == 0 else '#EFEDE8', 'height': 'sm'} for i, o in enumerate(options[:12])]
    bs += [_btn('✏️ 我打字回答', data=f'a=ask_type&t={topic}')] if typed else []
    msg = flex(f'請決定：{topic}', _bubble(_header('請決定 🗳', topic[:30], '#C47F00'), [_t(question[:1800], wrap=True, size='sm')], bs,
                                       '點一個選項；要自己寫就按「✏️ 我打字回答」再打字。管理層誰回答都算。'))
    msg[0]['quickReply'] = {'items': items}
    if not line:
        pass                                       # 只在中控台「管理群待處理 › 要決定」出現，不傳 LINE（例：估價練習）
    elif explicit:
        for u in to:
            queue(con, WORK_ACCOUNT, u, 'admin_ask', f'請決定：{topic}', msg)
    elif not ping and admin_group(con):           # 一次送好幾題 → 只有第一題 @，其他不重複 @（群組被洗版）
        queue(con, WORK_ACCOUNT, admin_group(con), 'admin_ask', f'請決定：{topic}', msg)
    else:                                          # 不私訊店長，送管理群 @店長（誰回答都可以）
        to_managers(con, 'admin_ask', f'請決定：{topic}', msg, tag=f'請店長（或管理層）決定：{topic} 👇')
    ix.set_setting(con, f'ask:{topic}', json.dumps({'question': question, 'options': options, 'asked_at': c.now()}, ensure_ascii=False))
    import reviews; reviews.export_log(con)          # 決策紀錄的「還在等她回覆」馬上更新
    return len(to)

OWNER_UID = __import__('os').environ.get('OPS_OWNER_LINE_UID', '').strip()      # 負責人的 LINE userId（環境變數設定，預設空值＝不推給負責人）

def record_answer(con, topic, ans, who):
    """記下管理層對題目卡的回答＋自動生效（LINE【主題】答案、中控台「管理層要決定」共用）。回傳套用說明。"""
    ix.set_setting(con, f'answer:{topic}', json.dumps({'answer': ans, 'by': who, 'at': c.now()}, ensure_ascii=False))
    c.audit(con, 'staff_answer', 'settings', f'answer:{topic}', after={'answer': ans, 'by': who})
    import reviews; reviews.export_log(con)
    import decisions
    return decisions.apply(con, topic, ans, who)           # 能自動生效的直接套用（評論連結、日曆隱私、估價費、取消補貼）

def _staff_answer(con, acc, uid, text, ev):
    """管理層回覆【主題】答案 → 記錄、回覆收到、通知負責人。"""
    m = re.match(r'^【([^】]{1,30})】\s*(.+)$', text, re.S)
    if not m:
        return None
    topic, ans = m.group(1), m.group(2).strip()[:300]
    who = (con.execute('SELECT display_name FROM line_users WHERE user_id=? LIMIT 1', (uid,)).fetchone() or ['管理層'])[0]
    done = record_answer(con, topic, ans, who)
    if uid != OWNER_UID:
        queue(con, WORK_ACCOUNT, OWNER_UID, 'admin', f'{who} 回覆了：{topic}', _txt(f'✅ {who} 回覆了【{topic}】：{ans}' + (f'\n→ {done}' if done else '')))
    import pending
    nq = pending.next_question(con, skip=topic) if uid in deciders(con) else None          # 回答完自動接下一題（只有決策者；客服回答原因時不會被丟店長的題目）
    if topic.startswith('其他方式原因 '):                 # 還有別張報價沒說原因 → 接著問（快速回覆只顯示在最後一則）
        import quickquote; nq = quickquote.next_reason_ask(con, skip=topic.split(' ', 1)[1].strip()) or nq
    return reply(con, acc, ev['replyToken'], _txt(f'收到 ✅ 已記錄【{topic}】：{ans}' + (f'\n→ {done}' if done else '') + ('' if nq else '\n\n🎉 題目都回答完了，謝謝！')) + ([nq] if nq else []))

def _review_tap(con, acc, uid, act, data, ev):
    import reviews
    if uid not in money_users(con):
        return reply(con, acc, ev['replyToken'], _txt('審核只有店長（和負責人）可以按。'))
    who = (con.execute('SELECT display_name FROM line_users WHERE user_id=? LIMIT 1', (uid,)).fetchone() or ['管理層'])[0]
    rid = data.get('r', '')
    if act == 'rv_edit':               # 下一則打字＝修改意見
        reviews.ensure(con)
        r = con.execute('SELECT title FROM staff_reviews WHERE id=?', (rid,)).fetchone()
        if not r:
            return reply(con, acc, ev['replyToken'], _txt('找不到這件審核。'))
        _staff_wait(con, uid, 'rv_wait', rid)
        return reply(con, acc, ev['replyToken'], _txt(f'好的，請直接打字告訴我「{r[0]}」要怎麼改 ✏️（下一則訊息會記成修改意見）'))
    try:
        row, msg = reviews.decide(con, rid, 'auto' if act == 'rv_auto' else 'approved', who)
    except ValueError as e:
        return reply(con, acc, ev['replyToken'], _txt(str(e)))
    if msg:
        return reply(con, acc, ev['replyToken'], _txt(msg))
    auto_ok = act == 'rv_auto' and row['category'] not in reviews.NO_AUTO            # 稽核：群發類（加行銷內容）不會設成免審，文字照實際
    if uid != OWNER_UID:
        queue(con, WORK_ACCOUNT, OWNER_UID, 'admin', f"{who} 核准了：{row['title']}", _txt(f"✅ {who} 核准了【{row['category']}】{row['title']}" + ('\n（這類以後不用再審核）' if auto_ok else '')))
    return reply(con, acc, ev['replyToken'], _txt('✅ 已核准' + ('，這類以後不用再審核 🟢' if auto_ok else '（這類每次都要審核）' if act == 'rv_auto' else '') + '，謝謝！'))

def _review_note(con, acc, uid, text, ev):
    """店長按了「要修改」後打的字 → 記成修改意見、通知負責人。"""
    import reviews
    rid = ix.setting(con, f'rv_wait:{uid}')
    if not rid:
        return None
    _staff_wait(con, uid, 'rv_wait', '')
    who = (con.execute('SELECT display_name FROM line_users WHERE user_id=? LIMIT 1', (uid,)).fetchone() or ['管理層'])[0]
    row, _ = reviews.decide(con, rid, 'changes', who, text[:1000])
    if uid != OWNER_UID:
        queue(con, WORK_ACCOUNT, OWNER_UID, 'admin', f"{who} 要修改：{row['title']}", _txt(f"✏️ {who} 對【{row['category']}】{row['title']} 的修改意見：\n{text[:800]}"))
    return reply(con, acc, ev['replyToken'], _txt('收到 ✏️ 已記下修改意見，改好會再請妳看一次。'))

TRI_SKIP = ('4. 沒有聯絡資料的夥伴',)          # 這組走「找回舊夥伴」，不在待整理卡片重複出現

def triage_items(con):
    import trash
    return [t for t in trash.listing(con) if t['grp'] not in TRI_SKIP]

def card_triage(con, page=1):
    """待整理（系統拿不準的資料）→ 一筆一張卡：保留／移到／刪除（回收桶，可還原），全部用點的。"""
    import trash
    items = triage_items(con); per = 9
    chunk = items[(page - 1) * per: page * per]; left = len(items) - page * per
    bubbles = []
    for t in chunk:
        moves = [m for m in trash.MOVES[t['entity']] if t['entity'] != 'workers' or m[0] != 'status:review'][:2]
        btns = [_btn('保留不動', data=f"a=tri&t={t['id']}&d=keep", primary=True)] + [_btn(f'移到：{n}', data=f"a=tri&t={t['id']}&d=move&to={k}") for k, n in moves]
        btns.append(_btn('刪除（回收桶，可還原）', data=f"a=tri&t={t['id']}&d=trash"))
        rows = [_t(t['reason'] or '', wrap=True, size='sm')]
        if t['info']:
            rows.append(_t(t['info'][:120], wrap=True, size='xs', color=SUB, margin='md'))
        if t['suggestion']:
            rows.append(_t('建議：' + t['suggestion'][:80], wrap=True, size='xs', color=SUB, margin='sm'))
        bubbles.append(_bubble(_sh(t['grp'][:30], f"{trash.LABEL[t['entity']]}・{t['label']}"[:40], '#C47F00'), rows, btns[:4]))
    if not bubbles:
        return _txt('待整理都處理完了 👍')
    more = (f'a=m_tri&p={page + 1}', left) if left > 0 else None
    return _carousel(f'待整理 {len(items)} 筆（第 {page} 頁）', bubbles, more)

def _triage_tap(con, acc, uid, data, ev):
    import trash
    if uid not in money_users(con):
        return reply(con, acc, ev['replyToken'], _txt('待整理只有店長（和負責人）可以按。'))
    who = (con.execute('SELECT display_name FROM line_users WHERE user_id=? LIMIT 1', (uid,)).fetchone() or ['管理層'])[0]
    c.CTX.actor = who + '（LINE 待整理）'
    d = data.get('d')
    p = {'id': data.get('t', ''), 'action': d}
    if d == 'move':
        p['to'] = data.get('to', '')
    r = trash.decide(con, p)
    if r['errors']:
        return reply(con, acc, ev['replyToken'], _txt('沒辦法這樣處理：' + r['errors'][0]['error'] + '（可以打開中控台「待整理」處理）'))
    n = len(triage_items(con))
    msg = {'keep': '✅ 已保留不動', 'move': '✅ 已移過去', 'trash': '🗑 已移到回收桶（中控台可以還原）'}.get(d, '✅ 已處理')
    return reply(con, acc, ev['replyToken'], [{'type': 'text', 'text': f'{msg}。還剩 {n} 筆。',
        'quickReply': {'items': [{'type': 'action', 'action': {'type': 'postback', 'label': '看下一批', 'data': 'a=m_tri', 'displayText': '看待整理'}}]}}] if n else _txt(msg + '，待整理都處理完了 👍'))

def _leave_tap(con, acc, uid, lu, gid, act, data, ev):
    """夥伴請假：沒指定場次 → 列出 14 天內的排班；選了場次 → 選原因；選了原因 → 送派單管理群。"""
    import leave
    wid = (lu or {}).get('worker_id') or (_group_worker(con, gid) if gid else None)
    if not wid:
        return reply(con, acc, ev['replyToken'], _txt('你的 LINE 還沒綁定夥伴資料，請直接在這裡留言告訴我們要請哪一天 🙏'))
    oid = data.get('o', '')
    if act == 'lv' and not oid:
        today = dt.datetime.now(c.TZ).date()
        rows = c.rows(con, "SELECT o.* FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.date BETWEEN ? AND ? "
                           "AND o.status NOT IN ('cancelled','archived','completed') ORDER BY o.date,o.start LIMIT 9", (wid, today.isoformat(), (today + dt.timedelta(days=14)).isoformat()))
        if not rows:
            return reply(con, acc, ev['replyToken'], _txt('你 14 天內沒有排工作，不用請假喔 ☕ 之後不方便的日子可以在主控板「可排時間」標「不行」。'))
        bs = [_bubble(_header('要請假的是這場嗎？', when(o), '#C47F00'), [_row('地區', c.place(order_ctx(con, o['id'])[4]))],
                      [_btn('這場要請假', data=f"a=lv&o={o['id']}", primary=True)]) for o in rows]
        return reply(con, acc, ev['replyToken'], _carousel('要請哪一場？', bs))
    o = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
    if not o or not con.execute("SELECT 1 FROM assignments WHERE order_id=? AND worker_id=? AND status='accepted'", (oid, wid)).fetchone():
        return reply(con, acc, ev['replyToken'], _txt('這場你目前沒有排到，不用請假喔 🙏'))
    if act == 'lv':
        q = lambda r: {'type': 'action', 'action': {'type': 'postback', 'label': r, 'data': f'a=lv2&o={oid}&r={r}', 'displayText': f'請假原因：{r}'}}
        return reply(con, acc, ev['replyToken'], [{'type': 'text', 'text': f"{when(dict(o))} 要請假，請點選原因 👇（派單管理群會看到，並另外安排人）", 'quickReply': {'items': [q(r) for r in leave.REASONS]}}])
    reason = data.get('r', '')
    if reason == '其他':
        ix.set_setting(con, f'lv_wait:{uid}', oid)
        return reply(con, acc, ev['replyToken'], _txt('請直接打字說明原因（下一則訊息會記成請假原因）✏️'))
    c.CTX.actor = c.get(con, 'workers', wid)['name'] + '（LINE 請假）'
    try:
        leave.request(con, wid, oid, reason or '未說明')
    except ValueError as e:
        return reply(con, acc, ev['replyToken'], _txt(str(e)))
    return reply(con, acc, ev['replyToken'], _txt(f"收到 🙏 {when(dict(o))} 的請假（{reason}）已送派單管理群，這場會另外安排；結果會再通知你。"))

def worker_dup_pairs(con):
    """可能重複的夥伴（至少一邊沒電話、名字相似）。管理群按「不是」的不再問。"""
    import board
    no = set(_jset(con, 'worker_dup_no'))
    out = []
    for d in board.duplicate_suggestions(con):
        if d['table'] != 'workers':
            continue
        a, b = (c.get(con, 'workers', d['a']['id']), c.get(con, 'workers', d['b']['id']))
        key = '|'.join(sorted((a['id'], b['id'])))
        if key in no or (a['phone'] and b['phone']):
            continue
        keep, drop = (a, b) if (con.execute("SELECT COUNT(*) FROM assignments WHERE worker_id=?", (a['id'],)).fetchone()[0] >=
                                con.execute("SELECT COUNT(*) FROM assignments WHERE worker_id=?", (b['id'],)).fetchone()[0]) else (b, a)
        out.append((keep, drop, d['why']))
    return out

def card_worker_dups(con):
    bs = []
    for keep, drop, why in worker_dup_pairs(con)[:10]:
        def info(w):
            n = con.execute("SELECT COUNT(*) FROM assignments WHERE worker_id=? AND status='accepted'", (w['id'],)).fetchone()[0]
            line = '有綁 LINE' if con.execute('SELECT 1 FROM line_users WHERE worker_id=? AND is_friend=1', (w['id'],)).fetchone() else '沒綁 LINE'
            return f"{w['phone'] or '沒電話'}・排班 {n} 次・{line}・{(w['regions'] or '')[:16]}"
        bs.append(_bubble(_sh('夥伴可能重複', f"{keep['name']}／{drop['name']}", '#C47F00'),
                          [_row(keep['name'][:8], info(keep)), _row(drop['name'][:8], info(drop)), _t(f'（{why}）合併後保留排班紀錄、電話與 LINE 綁定。', size='xs', color=SUB, wrap=True, margin='md')],
                          [_btn('是同一人，合併', data=f"a=wdup&k={keep['id']}&d={drop['id']}&y=1", primary=True), _btn('不是同一人', data=f"a=wdup&k={keep['id']}&d={drop['id']}&y=0")]))
    return _carousel(f'夥伴可能重複 {len(bs)} 組，請確認', bs) if bs else _txt('沒有可能重複的夥伴了 👍')

GUIDE_DEMO = {
    'grab': '👍 這是示範。真的派工卡按「我要接單」就接到了（先按先得），系統會回「接單成功」。人到齊後派單管理群會確認人員，再通知業主；業主確認時間後，地址和進場方式會傳給你。',
    'done': '👍 這是示範。真的卡片按「照排班時間完成」就登記好工時了；時間不一樣就按「我要補登」填實際時間。工時是月底結算的依據。',
    'leave': '👍 這是示範。真的按「臨時請假」會請你點選原因：生病／家裡有事／交通問題／其他（打字）。送出後派單管理群會另外安排人，結果會通知你。',
    'board': '👍 這是示範。夥伴按了會打開自己的主控板：我的資料、可排時間、作品集、班表、收入都在裡面。',
}

def card_worker_guide(con, wid=None, preview=False):
    """給夥伴的系統介紹＋操作示範（每張都可以按按看）。preview＝給管理層先試，最後一張是「OK，發給所有夥伴」。"""
    import worker_portal
    url = worker_portal.portal_url(con, wid) if wid else None
    board_btn = (lambda label, tag='': _btn(label, uri=url + tag, primary=True)) if url else (lambda label, tag='': _btn(label, data='a=g_demo&s=board', primary=True))
    B = lambda kicker, title, lines, btns, note='': _bubble(_header(kicker, title, '#2E7D32'), [_t(x, wrap=True, size='sm', margin='md') for x in lines], btns, note)
    bs = [
        B('示範服務店夥伴系統', '派工改用官方 LINE 👋', ['以後的派工、提醒、請假都在這個官方 LINE 一對一完成，不用再看群組。',
          '下面的選單有：今天的工作、我的主控板、可排時間、聯絡示範服務店。', '左右滑看完這幾張，每張都可以按按看 👉'], [board_btn('打開我的主控板')]),
        B('① 先建立你的資料（最重要）', '資料越完整，越優先派給你', ['【我的資料】可以服務的區域、會做的項目、平常可以的時間。',
          '【可排時間】每個月點一下哪幾天可以、哪幾天不行。', '【作品集】寫一段自我介紹、哪一年開始做服務，挑 3～12 張服務前後照片（不要有門牌、人臉、信件）。客戶看不到你的姓名電話，只看這些。'],
          [board_btn('現在去填資料', '#me')]),
        B('② 接單（示範卡）', '10/20（二）09:00–13:00', ['地區：臺北市大安區　類型：一般到府服務', '時數：約 4 小時・缺 1 位　時薪：你的時薪',
          '有適合你的工作，派工卡會傳到這裡，先按先得。'], [_btn('我要接單（示範）', data='a=g_demo&s=grab', primary=True)], '人到齊 → 派單管理群確認人員 → 通知業主 → 業主確認時間後傳地址給你。'),
        B('③ 服務當天', '打卡＋拍照，工時以打卡為準', ['到場：打開工作頁按「到場打卡」，拍服務前照片。', '做完：拍完成照片、寫一句回報，按「離場打卡」。',
          '忘了打卡沒關係：隔天會問你「照排班時間完成嗎？」，按一下就好。'], [_btn('照排班時間完成（示範）', data='a=g_demo&s=done', primary=True)]),
        B('④ 臨時請假', '盡量提早說 🙏', ['在工作卡按「臨時請假」，或直接在這裡打「請假」，點選原因。', '派單管理群會判斷：初次或特殊狀況可以通融不記點；記點的話，半年內滿 3 點會暫停派工半年。',
          '系統會幫你另外找人，也會通知業主。'], [_btn('臨時請假（示範）', data='a=g_demo&s=leave', primary=True)]),
        B('⑤ 收入與結算', '自己隨時看得到', ['主控板「我的收入」：本月做了幾場、幾小時、預估多少（只看得到自己的）。', '每月月底結算後匯款，實際金額以結算為準。'], [board_btn('看我的收入')]),
        B('⑥ 有問題找誰', '直接在這裡說就好', ['按選單「聯絡示範服務店」或直接留言，派單同事會回覆你。', '工作卡上的按鈕都要「本人」按；在群組按系統可能認不出你，請在這個一對一聊天室按。'],
          [board_btn('打開我的主控板')]),
    ]
    if preview:
        n = len(guide_targets(con))
        bs.append(_bubble(_header('管理群確認', f'要發給 {n} 位夥伴', '#0A84FF'), [_t('這是要發給已綁官方 LINE 的夥伴的介紹（每個人的按鈕會打開自己的主控板）。前面幾張的示範按鈕可以先按按看。', wrap=True, size='sm')],
                          [_btn('✅ OK，發給所有夥伴', data='a=g_ok', primary=True), _btn('✏️ 要修改（打字說明）', data='a=g_edit')], '還沒綁 LINE 的夥伴不會收到，系統會列給管理群打電話請他加官方 LINE。'))
    return [{'type': 'flex', 'altText': '示範服務店夥伴系統介紹：建立資料、接單、打卡、請假、收入', 'contents': {'type': 'carousel', 'contents': bs}}]

def guide_targets(con):
    """要收到介紹的夥伴：可派工、已綁官方 LINE 一對一（老闆不算）。"""
    boss = bosses(con)
    return [r for r in c.rows(con, "SELECT DISTINCT w.id,w.name,l.account,l.user_id FROM workers w JOIN line_users l ON l.worker_id=w.id AND l.is_friend=1 WHERE w.status='active' ORDER BY w.name")
            if r['user_id'] not in boss]

def send_guide(con, by):
    if ix.setting(con, 'guide_sent_at'):
        return f"介紹已經在 {ix.setting(con, 'guide_sent_at')[:16].replace('T', ' ')} 發過了（{ix.setting(con, 'guide_sent_by')}）"
    ts = guide_targets(con); seen = set()
    for t in ts:
        if t['id'] in seen:
            continue
        seen.add(t['id'])
        m = card_worker_guide(con, t['id']); queue(con, t['account'], t['user_id'], 'guide', m[0]['altText'], m)
    ix.set_setting(con, 'guide_sent_at', c.now()); ix.set_setting(con, 'guide_sent_by', by)
    miss = [r['name'] for r in c.rows(con, "SELECT name FROM workers w WHERE status='active' AND NOT EXISTS(SELECT 1 FROM line_users l WHERE l.worker_id=w.id AND l.is_friend=1) ORDER BY name")]
    if miss:
        admin_notify(con, '📵 這些夥伴沒收到介紹（還沒綁官方 LINE）', [('名單', '、'.join(miss)[:300]), ('處理', '請打電話請他們加官方 LINE、按選單完成綁定，之後系統會自動補發')], '/#workers', '#C47F00')
    return f'✅ 已發給 {len(seen)} 位夥伴（{by}）' + (f'；{len(miss)} 位還沒綁 LINE，名單已列在派單管理群' if miss else '')

def card_magic(con, user_id, hours=1):
    """中控台登入卡：一次性連結（不經手密碼），點了直接登入、自己設定密碼。"""
    import accounts
    u = c.get(con, 'users', user_id)
    t = accounts.magic_link(con, user_id, hours * 60)
    return flex('中控台登入連結', _bubble(_header('中控台登入 🔑', u['name'], '#0A84FF'),
        [_row('帳號', u['username']), _t(f'按下面直接登入（{hours} 小時內有效、只能用一次），進去會請妳設定自己的密碼，之後用帳號＋新密碼登入。', wrap=True, size='sm')],
        [_btn('登入中控台', uri=f'{BASE_URL}/login/line?t={t}', primary=True), _btn('連結過期了，重新寄一次', data='a=magic')],
        '這個連結只給妳本人，請不要轉傳。'))

def money_users(con):
    """LINE 管理選單裡看得到金額的人（負責人、店長；起客服也全部看得到）。"""
    return _jset(con, 'line_staff_money')

def deciders(con):
    """決策／審核卡要問的人（店長）。跟「看得到金額」分開：客服全部看得到，但決策卡仍只問店長。"""
    d = _jset(con, 'line_deciders')
    return d or ({u for u in money_users(con) if u != OWNER_UID} or set(money_users(con)))

def bosses(con):
    """老闆（負責人、店長）：有夥伴身分也不自動問缺人、可排時間、不收夥伴介紹。"""
    return _jset(con, 'line_bosses') or money_users(con)

def link_staff_menu(con):
    rid = ix.setting(con, 'staff_richmenu_id')
    for u in staff_users(con) if rid else ():
        try:
            api(con, f'/v2/bot/user/{u}/richmenu/{rid}', {})
        except ValueError:
            pass

def _staff_search(con, acc, ev, kw):
    uid = ev.get('source', {}).get('userId', '')
    """管理層「查 xxx」：客戶（名稱、LINE 名稱、電話）、夥伴、最近工單。"""
    like = f'%{kw}%'; digits = re.sub(r'\D', '', kw)
    cus = c.rows(con, "SELECT * FROM customers WHERE name LIKE ? OR line_name LIKE ? OR (?!='' AND REPLACE(REPLACE(phone,'-',''),' ','') LIKE ?) ORDER BY status LIMIT 5", (like, like, digits, f'%{digits}%'))
    wks = c.rows(con, "SELECT * FROM workers WHERE name LIKE ? OR (?!='' AND REPLACE(phone,'-','') LIKE ?) LIMIT 4", (like, digits, f'%{digits}%'))
    bubbles = [_cust_bubble(con, cu, uid) for cu in cus] + [_worker_bubble(con, w) for w in wks]
    if not bubbles:
        return reply(con, acc, ev['replyToken'], _txt(f'找不到「{kw}」。可以查客戶名稱、LINE 名稱、電話或夥伴名字。'))
    return reply(con, acc, ev['replyToken'], _carousel(f'查「{kw}」：{len(bubbles)} 筆', bubbles), 'menu')

def on_unassigned(con, assignment_id, actor=''):
    """同事在後台把已接的夥伴拿掉 → 通知那位夥伴不用去；人數不足就提醒派單管理群補人。"""
    a = con.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone()
    o = c.get(con, 'orders', a['order_id'])
    m = card_text('這筆工作改由其他人負責', f"{when(o)} 這筆工作示範服務店已調整人力，請不要前往。有疑問直接在這裡留言。", '#C62828')
    to_worker(con, a['worker_id'], 'cancel', m[0]['altText'], m, o['id'])
    n = con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (o['id'],)).fetchone()[0]
    if n < o['required_workers'] and o['status'] not in ('cancelled', 'archived', 'completed'):
        w = c.get(con, 'workers', a['worker_id'])
        admin_notify(con, f"⚠️ {w['name']} 已從這筆移除，還缺 {o['required_workers'] - n} 位", [('處理', '請到工單「挑人派工」補人'), ('操作', actor or '—')], '', '#C47F00', oid=o['id'])

def on_assigned_worker(con, assignment_id):
    """同事在後台直接把夥伴排進一場 → 推播給那位夥伴（已排定就給地址卡，還沒排定就先告知）。"""
    a = con.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone()
    if not a or a['status'] != 'accepted':
        return
    o = c.get(con, 'orders', a['order_id'])
    if o['status'] == 'confirmed':
        m = card_worker_remind(con, o['id'], '✅ 示範服務店幫你排了一場', '#2E7D32', a['worker_id'])
    else:
        o2, s, cu, crew, area = order_ctx(con, o['id'])
        import sitetools, board, sitegeo           # 寫服務類別＋工具（夥伴才知道帶什麼）；接單後就給完整地址＋導航
        m = flex(f'示範服務店幫你排了一場：{when(o)}', _bubble(_header('📅 幫你排了一場', when(o), '#2E7D32'),
                 [_row('類型', s.get('service_type') or o['title'] or '服務'), _row('地址', s.get('address') or board.street_of(s)),
                  _row('時數', f"約 {round(c.minutes(o['start'], o['end']) / 60, 1):g} 小時" if o['start'] and o['end'] else '—'), _row('工具', sitetools.worker_text(s))]
                 + ([_row('範圍', scope_text(o, 120))] if scope_text(o) else []),
                 [_btn('開導航（精準定位）', uri=sitegeo.nav_url(s)), _btn('這時間不行', data=f"a=alt&o={o['id']}")], '業主確認、排定後會再傳進場方式、開門給你。'))
    to_worker(con, a['worker_id'], 'assigned_w', m[0]['altText'], m, o['id'])

def _customer_menu(con, acc, uid, lu, act, data, ev):
    name = brands.name(acc)
    who = (lu or {}).get('display_name') or 'LINE 客戶'
    if act == 'c_my':                  # 我的預約：每一筆一張進度卡（估價→報價→簽約→付款→找人→排定→服務→驗收）
        cid = (lu or {}).get('customer_id')
        if not cid:
            return bind_prompt(con, acc, uid, lu, ev['replyToken'])
        return reply(con, acc, ev['replyToken'], card_my(con, acc, uid, cid), 'menu')
    if act == 'c_photos':              # 過去的完成照片（最近 10 場）
        cid = (lu or {}).get('customer_id')
        if not cid:
            return bind_prompt(con, acc, uid, lu, ev['replyToken'])
        return reply(con, acc, ev['replyToken'], card_past_photos(con, acc, cid), 'menu')
    if act == 'c_pay':                 # 選單「付款」：還沒付的款項＋匯款資訊＋回報末五碼；已回報的說核對中
        cid = (lu or {}).get('customer_id')
        if not cid:
            return bind_prompt(con, acc, uid, lu, ev['replyToken'])
        import payments
        rows = [r for r in payments.rows_for(con, customer_id=cid) if r['status'] in ('pending', 'reported')]
        if not rows:
            return reply(con, acc, ev['replyToken'], _txt('目前沒有要付的款項 👍 有疑問直接在這裡留言就好。'), 'menu')
        out = []
        for qid in dict.fromkeys(r['quote_id'] or '' for r in rows):
            rs = [r for r in rows if (r['quote_id'] or '') == qid]
            tok = (con.execute('SELECT token FROM quotes WHERE id=?', (qid,)).fetchone() or [None])[0] if qid else None
            pend = [r for r in rs if r['status'] == 'pending']
            if pend:
                out += card_payment(con, acc, pend, tok, '待付款項 🧾', '付款・匯款回報')
            rep_ = [r for r in rs if r['status'] == 'reported']
            if rep_:
                out += _txt('已收到您的匯款回報：' + '、'.join(f"{r['label']} NT$ {r['amount'] // 100:,}" for r in rep_) + '，核對後會通知您 🙏')
        return reply(con, acc, ev['replyToken'], out[:5], 'menu')
    if act == 'c_chg_menu':            # 選單「改期・取消」：列出接下來的預約，選一場再看規則
        cid = (lu or {}).get('customer_id')
        if not cid:
            return bind_prompt(con, acc, uid, lu, ev['replyToken'])
        rows = c.rows(con, "SELECT * FROM orders WHERE customer_id=? AND date>=? AND status IN ('review','draft','confirmed') ORDER BY date,start",
                      (cid, dt.datetime.now(c.TZ).date().isoformat()))
        if not rows:
            return reply(con, acc, ev['replyToken'], _txt('目前沒有接下來的預約 🙏 要預約請按選單的「線上估價」。'), 'menu')
        bs = [_bubble(_header('改期／取消', when(dict(o)), '#C47F00', brand=acc), [_t('按下面會先說明這一場的改期／取消規則，再讓您選。', wrap=True, size='sm')],
                      [_btn('改期或取消這一場', data=f"a=c_chg&o={o['id']}", primary=True)]) for o in rows[:10]]
        return reply(con, acc, ev['replyToken'], [{'type': 'flex', 'altText': '要改期或取消哪一場？', 'contents': {'type': 'carousel', 'contents': bs}}], 'menu')
    oid = (data or {}).get('o', '')
    if act == 'c_chg':                 # 改期／取消：先說明規則（依離服務日天數），再點選
        o = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not o or o['customer_id'] != (lu or {}).get('customer_id'):
            return reply(con, acc, ev['replyToken'], _txt('找不到這筆預約，請直接留言給我們 🙏'))
        days = (dt.date.fromisoformat(o['date']) - dt.datetime.now(c.TZ).date()).days if o['date'] else 99
        rule = ('離服務日還有 7 天以上：可以免費改期一次。' if days >= 7 else
                '離服務日 4～7 天：改期或取消送出後，專人會跟您確認訂金怎麼處理。' if days >= 4 else
                '離服務日 3 天內：取消的話已付訂金原則上不退；改期請先送出，專人會盡量協調。')
        import reschedule                # 改時間 → 線上選日期、時段（手機、電腦都能開），送回來給管理群確認
        return reply(con, acc, ev['replyToken'], reschedule.card_offer(con, dict(o), acc), 'menu')
    if act in ('c_resch', 'c_cancel', 'c_cancel_ok'):
        o = con.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not o or o['customer_id'] != (lu or {}).get('customer_id'):
            return reply(con, acc, ev['replyToken'], _txt('找不到這筆預約，請直接留言給我們 🙏'))
        if act == 'c_cancel':          # 取消要再按一次確認（避免誤按）
            return reply(con, acc, ev['replyToken'], flex('確定要取消嗎？', _bubble(_header('確定要取消嗎？', when(dict(o)), '#C62828', brand=acc),
                [_t('送出後專人會跟您確認；依合約，服務日前 3 天內取消，已付訂金原則上不退。', wrap=True)],
                [_btn('確定申請取消', data=f'a=c_cancel_ok&o={oid}', primary=True)])), 'menu')
        new_date = ((ev.get('postback') or {}).get('params') or {}).get('date', '')
        what = f'申請改期到 {md(new_date)}' if act == 'c_resch' else '申請取消'
        if act == 'c_resch' and not new_date:
            return reply(con, acc, ev['replyToken'], _txt('請按「申請改期：選新日期」選一個日期 🙏'))
        days = (dt.date.fromisoformat(o['date']) - dt.datetime.now(c.TZ).date()).days if o['date'] else 99
        if con.execute("SELECT 1 FROM dispatch_log WHERE order_id=? AND action='customer_change' AND detail=? AND at>datetime('now','-12 hours')", (oid, what)).fetchone():
            return reply(con, acc, ev['replyToken'], _txt(f'已經收到您的{what}了，專人確認後會在這裡通知您 🙏'))
        import board; board.log(con, oid, 'customer_change', what)          # 稽核：首頁「客戶回饋待處理」看得到，直到處理完
        c.audit(con, 'customer_change_request', 'orders', oid, after={'request': what, 'days_before': days, 'line_user': uid})
        con.execute("UPDATE orders SET review_note=TRIM(COALESCE(review_note,'') || '；客戶' || ? || '（' || ? || '，離服務日 ' || ? || ' 天）'),version=version+1 WHERE id=?",
                    (what, c.now()[:16].replace('T', ' '), days, oid))
        g = admin_group(con)
        if act == 'c_cancel_ok' and g:          # 「客戶取消怎麼取消」：管理群按一下就取消（通知業主、夥伴；收款單自動處理）
            m = flex(f"📅 業主申請取消 {when(dict(o))}", _bubble(_header('業主申請取消', when(dict(o)), '#C62828'),
                     [_row(k, v) for k, v in order_people(con, oid)] + [_row('離服務日', f'{days} 天'), _row('規則', '7 天前免費；4～7 天訂金由專人確認；3 天內取消訂金原則上不退')],
                     [_btn('確認取消（通知業主、夥伴）', data=f'a=ccx_ok&o={oid}', primary=True)], '按了系統會取消工單、用 LINE 通知業主和夥伴；沒付的收款單自動作廢，已付的訂金標給店長處理。要先協調就直接在官方 LINE 回業主。'))
            queue(con, WORK_ACCOUNT, g, 'admin', m[0]['altText'], [ping(con, '業主在 LINE 申請取消，請確認 👇')] + m, oid)
        else:
            admin_notify(con, f"📅 客戶{what}", [('離服務日', f'{days} 天'), ('規則', '7 天前免費改期；4～7 天由專人確認訂金；3 天內取消訂金不退'), ('處理', '請在中控台改期或取消，系統會自動通知客戶與夥伴；處理完在首頁按「已處理」')],
                         '', '#C47F00', oid=oid, tag=f'📅 客戶{what}，請管理層處理 👇')          # 稽核：@管理層；4 小時沒處理再 @
        return reply(con, acc, ev['replyToken'], _txt(f'已收到您的{what}，專人確認後會在這裡通知您 🙏（確認之前原本的預約仍有效）'))
    if act == 'c_services':            # 依這個品牌自己的業務線產生（品牌不混：企業線只會看到企業線的服務）
        rows, btns = [], []
        for ln in brands.lines(con, acc):
            h = ln.get('hourly') or {}
            if rows:
                rows.append({'type': 'separator', 'margin': 'lg'})
            rows += [_t(ln['name'], weight='bold', margin='lg' if len(rows) > 1 else 'none'),
                     _t('・'.join(ln.get('services') or []) or ln.get('intro') or ln['name'], color=SUB, wrap=True, size='xs')]
            if ln['kind'] == 'hourly' and h.get('rate'):
                rows.append(_t(f"每小時 NT$ {h['rate']:,} 起，最少 {h.get('min_hours', 2):g} 小時", color=SUB, size='xs'))
            if ln['kind'] == 'trash':                # 垃圾代丟
                import daidiu
                rows.append(_t(daidiu.price_line(), color=SUB, size='xs', wrap=True))
            if len(btns) < 3:
                btns.append(_btn(('線上估價・預約' if ln['kind'] == 'cleaning' else f"預約{ln['name']}"), uri=book_url(con, acc, uid, f"&l={ln['id']}"), primary=not btns))
        btns.append(_btn('官網：服務介紹・作品', uri=f'{BASE_URL}/home?b={acc}'))       # 客戶在 LINE 找不到官網
        if acc == 'demoshop':           # 示範服務店整合的配合廠商
            rows += [_t('配合廠商（一起幫您安排）', weight='bold', margin='lg'), _t('窗簾拆洗、送洗・高空清洗（外牆、高處玻璃）・各式工程', color=SUB, wrap=True, size='xs')]
            if len(btns) < 4:
                btns.append(_btn('詢問配合廠商工程', data='a=c_other&s=配合廠商工程'))
            rows += [_t('示範商城（耗材）', weight='bold', margin='lg'), _t('服務團隊在用的耗材，在 LINE 或網站直接下單；備好了在 LINE 通知您', color=SUB, wrap=True, size='xs')]
            btns.append(_btn('🧴 逛示範商城', data='a=shop'))          # 客戶在 LINE 找不到商城
        return reply(con, acc, ev['replyToken'], flex(f'{name}的服務項目', _bubble(_header('服務項目', name, brand=acc), rows or [_t(name)], btns or [_btn('線上估價', uri=f'{BASE_URL}/book?b={acc}', primary=True)],
                     '原則上以照片或現場確認後的報價為準。')), 'menu')
    # 真人客服／其他服務：通知派單管理群（同一人 30 分鐘內只通知一次），請客戶直接留言
    topic = data.get('s') or '找真人客服'
    recent = con.execute("SELECT 1 FROM line_outbox WHERE kind='admin' AND alt LIKE ? AND created_at>?",
                         (f'%{who}%{topic[:6]}%', (dt.datetime.now(c.TZ) - dt.timedelta(minutes=30)).isoformat())).fetchone()
    if not recent:
        cid = (lu or {}).get('customer_id')
        admin_notify(con, f"🙋 {who}：{topic}", [('客戶', c.get(con, 'customers', cid)['name'] if cid else f'{who}（未綁定）'), ('品牌', name), ('處理', '請到官方 LINE 聊天室回覆')], f'/#c/{cid}' if cid else '/#pairing', '#0A84FF')
    tip = {'找真人客服': '好的，已經通知專人 🙏 請直接在這裡留言您的問題，我們會盡快回覆您。',
           '配合廠商工程': '好的！請直接留言想處理的工程（例如窗簾、外牆），可以附幾張照片和地址，專人會幫您安排報價 🙏'}.get(
        topic, '好的！請直接留言您的需求（時間、地點、人數或小朋友年齡等），專人會盡快跟您聯繫 🙏')
    return reply(con, acc, ev['replyToken'], _txt(tip), 'menu')

def _gate(con, acc, uid, lu, ev):
    """還沒建檔的人（不是客戶、夥伴、同事）傳訊息 → 先請他選「詢問／應徵」並留基本資料；同一天只在第一則訊息回一次（客戶09每句都收到歡迎卡）。"""
    if not lu or lu.get('customer_id') or lu.get('worker_id') or lu.get('is_staff') or not allowed(con, uid, acc):
        return
    last = lu.get('nudged_at') or ''
    if last and last[:10] == c.now()[:10]:
        return
    if match_customer(con, acc, lu.get('display_name')):
        return bind_prompt(con, acc, uid, lu, ev['replyToken'])          # 看起來是舊客戶 → 直接請他確認資料
    con.execute("UPDATE line_users SET onboard='asked',nudged_at=? WHERE account=? AND user_id=?", (c.now(), acc, uid))
    reply(con, acc, ev['replyToken'], card_welcome(acc, con), 'welcome')

def _worker_menu(con, acc, uid, lu, gid, act, ev):
    """夥伴圖文選單：今天的工作／我的主控板／可排時間／聯絡示範服務店。"""
    import worker_portal
    wid = (lu or {}).get('worker_id') or _group_worker(con, gid)
    if not wid:
        return reply(con, acc, ev['replyToken'], card_welcome(acc, con), 'welcome')
    w = c.get(con, 'workers', wid)
    if act == 'w_today':
        today = dt.datetime.now(c.TZ).date().isoformat()
        rows = c.rows(con, "SELECT a.id aid,o.* FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.date=? "
                           "AND o.status NOT IN ('cancelled','archived') ORDER BY o.start", (wid, today))
        if not rows:
            return reply(con, acc, ev['replyToken'], _txt(f"{w['name']}，今天沒有排工作 ☕"))
        bubbles = []
        for o in rows[:5]:
            o2, s, cu, crew, area = order_ctx(con, o['id'])
            bubbles.append(_bubble(_header('今天的工作', f"{o['start']}–{o['end']}"), [_row('地區', c.place(area)), _row('範圍', scope_text(o) or s.get('service_type') or '—')],
                                   [_btn('打卡・拍照', uri=worker_portal.work_url(con, o['aid']), primary=True), _btn('現場回報（加時、晚到、狀況）', data=f"a=srep_menu&o={o['id']}")]))
        return reply(con, acc, ev['replyToken'], [{'type': 'flex', 'altText': f'今天有 {len(rows)} 筆工作', 'contents': {'type': 'carousel', 'contents': bubbles}}])
    if act == 'w_income':              # 選單「我的收入」：本月場次、時數、預估（只有自己看得到）
        url = worker_portal.portal_url(con, wid) + '#income'
        return reply(con, acc, ev['replyToken'], flex('我的收入', _bubble(_header('我的收入', w['name']),
            [_t('本月做了幾場、幾小時、預估多少都在這裡（只有你看得到）；月底結算後匯款，實際金額以結算為準。', wrap=True)], [_btn('打開我的收入', uri=url, primary=True)])))
    if act == 'w_me':                  # 請夥伴填完整基本資料（主控板「我的資料」）
        miss = worker_portal.profile_missing(w)
        return reply(con, acc, ev['replyToken'], flex('填我的資料', _bubble(_header('我的資料', w['name']),
            [_t(('還沒填：' + '、'.join(miss) + '。') if miss else '你的資料都填好了，謝謝你 🙏 有變動隨時可以改。', wrap=True),
             _t('為了更了解你的狀況、安排最適合你的工作，請每一題都填（沒經驗的勾「無經驗」）。', wrap=True, size='sm', color=SUB, margin='md')],
            [_btn('打開「我的資料」', uri=worker_portal.portal_url(con, wid) + '#me', primary=True)])))
    if act in ('w_board', 'w_avail'):
        url = worker_portal.portal_url(con, wid) + ('#avail' if act == 'w_avail' else '')
        return reply(con, acc, ev['replyToken'], flex('我的主控板', _bubble(_header('我的主控板', w['name']),
            [_t('班表、邀請中的工作、可排時間都在這裡。', wrap=True)], [_btn('打開' + ('可排時間' if act == 'w_avail' else '主控板'), uri=url, primary=True)])))
    last = ix.setting(con, f'wcontact:{wid}')
    if not last or last < (dt.datetime.now(c.TZ) - dt.timedelta(minutes=30)).isoformat():
        ix.set_setting(con, f'wcontact:{wid}', dt.datetime.now(c.TZ).isoformat())
        day = dt.datetime.now(c.TZ).date().isoformat(); tm = (dt.datetime.now(c.TZ).date() + dt.timedelta(days=1)).isoformat()
        jobs = c.rows(con, "SELECT o.date,o.start,o.end FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.date IN (?,?) "
                           "AND o.status NOT IN ('cancelled','archived') ORDER BY o.date,o.start", (wid, day, tm))
        admin_notify(con, f"🙋 夥伴 {w['name']} 想聯絡示範服務店", [('今明的工作', '、'.join(f"{md(j['date'])} {j['start']}" for j in jobs) or '沒有'), ('處理', '請到官方 LINE 聊天室回覆他')], f'/#w/{wid}', '#0A84FF')
    import site_report
    cur = site_report.current(con, wid)
    return reply(con, acc, ev['replyToken'], _txt(f'有事直接在這裡留言，{brands.name(acc)}會盡快回覆你 🙏（已通知派單同事）\n📖 使用教學（圖文）：{BASE_URL}/wguide' + (f'?b={acc}' if acc != WORK_ACCOUNT else '')) + (site_report.start_card(con, wid, cur['id']) if cur else []))

def _auto_confirm(con, oid):
    """客戶已確認＋人到齊＋開工前款項已確認 → 自動排定，並把地址傳給夥伴。"""
    o2 = c.get(con, 'orders', oid)
    if o2['status'] not in ('review', 'draft') or not o2['customer_confirmed']:
        return ''
    if con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'", (oid,)).fetchone()[0] < o2['required_workers']:
        return '等夥伴接單'
    if not o2.get('crew_ok'):                  # 稽核：業主按舊卡片「確認這個時間」也不能跳過派員審核（地址不能先給還沒審的人）
        return '等派單管理群審核人員'
    import automation
    bad = [r[1] for r in con.execute("SELECT w.id,w.name,w.status FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status='accepted'", (oid,))
           if (r[2] != 'active' and not c.in_trial(con, r[0])) or automation.is_blacklisted(con, r[0])]
    if bad:                        # 盤點：暫停／停派的人身上的單不能自動排定（不傳地址）
        return f"待人工排定（{'、'.join(bad)} 已暫停或停派）"
    import payments
    if not payments.cleared_for_schedule(con, oid):          # 負責人 簽約後先找人，店長確認收款後才排定（給地址）
        payments.ensure(con)
        r = con.execute("SELECT id FROM payment_requests WHERE order_id=? AND kind IN ('deposit','full','month') AND status!='confirmed' LIMIT 1", (oid,)).fetchone()
        if r and payments.mark(con, r[0], 'crew_ready'):
            admin_notify(con, '💰 人已到齊，等收款才能排定', [('處理', '確認收到款項後按「確認收款」，系統就會排定並把地址傳給夥伴')], '', '#C47F00', oid=oid)
        return '等收款'
    con.execute('SAVEPOINT k')
    try:
        if not o2['review_note']:
            con.execute("UPDATE orders SET review_note='客戶在 LINE 確認時間' WHERE id=?", (oid,)); o2 = c.get(con, 'orders', oid)
        c.CTX.actor = '系統（客戶確認＋夥伴接單）'
        c.transition(con, oid, 'confirm', {'version': o2['version']}); con.execute('RELEASE k')
        on_confirmed(con, oid)
        return '已排定'
    except ValueError as e:
        con.execute('ROLLBACK TO k'); con.execute('RELEASE k')
        return f'待人工排定（{e}）'

# ───────────── 背景送出 ─────────────
_wake = threading.Event()

RETRY_MIN = (1, 5, 30, 120)          # 暫時失敗（LINE 5xx、逾時、斷線）幾分鐘後重試；超過就標失敗並通知

def _outbox_cols(con):
    cols = {r[1] for r in con.execute('PRAGMA table_info(line_outbox)')}
    if 'attempts' not in cols:
        con.execute('ALTER TABLE line_outbox ADD COLUMN attempts INTEGER DEFAULT 0')
    if 'next_at' not in cols:
        con.execute("ALTER TABLE line_outbox ADD COLUMN next_at TEXT DEFAULT ''")

def _push(tok, m, con=None):
    """打 LINE（tok 已取好時不碰資料庫、不握鎖）。回傳 (結果, 錯誤)：ok／retry／quota／fail。"""
    kw = {'tok': tok} if tok else {}
    try:
        api(con, '/v2/bot/message/push', {'to': m['to_id'], 'messages': json.loads(m['payload'])}, acc=m['account'] or 'demoshop', **kw)
        return 'ok', ''
    except ValueError as e:
        err = str(e)
        if 'monthly limit' in err.lower():
            return 'quota', f'LINE 本月免費訊息額度用完了（{err[:150]}）'
        code = re.match(r'LINE 回應 (\d+)', err)
        if err.startswith('連線 LINE 失敗') or (code and (int(code[1]) >= 500 or code[1] == '429')):
            return 'retry', err
        return 'fail', err

def _claim(con, limit):
    """取出該送的訊息並標成 sending（握鎖時做，很快）；附上各帳號金鑰。"""
    _outbox_cols(con)
    rows = c.rows(con, "SELECT * FROM line_outbox WHERE status='queued' AND COALESCE(next_at,'')<=? ORDER BY id LIMIT ?", (c.now(), limit))
    stale = [m for m in rows if m.get('error') in ('quiet', 'throttle') and _stale_held(con, m)]          # 每日檢查：夜裡延後的卡，送出前工單變了就不送
    for m in stale:
        con.execute("UPDATE line_outbox SET status='skipped:stale',error='安靜時段延後，送出前工單已變動' WHERE id=?", (m['id'],))
    rows = [m for m in rows if m not in stale]
    toks = {}
    for m in rows:
        acc = m['account'] or 'demoshop'
        if acc not in toks:
            toks[acc] = ix.secret(con, 'line_token_' + acc)
        con.execute("UPDATE line_outbox SET status='sending' WHERE id=?", (m['id'],))
    return rows, toks

def _record(con, m, res, err):
    if res == 'ok':
        con.execute("UPDATE line_outbox SET status='sent',sent_at=?,error='' WHERE id=?", (c.now(), m['id']))
        if m['to_id'] == admin_group(con) and ix.setting(con, 'admin_group_fail'):
            ix.set_setting(con, 'admin_group_fail', '')
        return
    n = (m.get('attempts') or 0) + 1
    if res == 'retry' and n <= len(RETRY_MIN):
        nxt = (dt.datetime.now(c.TZ) + dt.timedelta(minutes=RETRY_MIN[n - 1])).isoformat(timespec='seconds')
        con.execute("UPDATE line_outbox SET status='queued',attempts=?,next_at=?,error=? WHERE id=?", (n, nxt, err[:300], m['id'])); return
    con.execute("UPDATE line_outbox SET status=?,attempts=?,error=? WHERE id=?", ('failed:quota' if res == 'quota' else 'failed', n, err[:300], m['id']))
    _fail_alert(con, m, res, err)

def _fail_alert(con, m, res, err):
    """送不出去不能靜默：額度用完（每帳號每天一次）或重要訊息失敗 → 通知管理群＋中控台。"""
    import notify, brands
    acc = m['account'] or 'demoshop'
    if res == 'quota':
        key = f'quota_alert:{acc}'
        if ix.setting(con, key) == c.now()[:10]:
            return
        ix.set_setting(con, key, c.now()[:10])
        title, rows = f'⚠️ {brands.name(acc)} LINE 額度用完', [('影響', '這個官方帳號本月推播都送不出去（回覆訊息不受影響）'), ('處理', '到 LINE 官方帳號後台升級方案或加購訊息')]
    else:
        if m['to_id'] == admin_group(con):          # 稽核：派單管理群收不到（機器人被踢、403）→ 中控台首頁紅條，不能靜默
            ix.set_setting(con, 'admin_group_fail', f"{c.now()[:16].replace('T', ' ')}｜{(m['alt'] or '')[:30]}｜{err[:60]}")
        if m['kind'] in ('admin', 'admin_ask', 'admin_review', 'menu', 'reply'):
            return
        title, rows = '⚠️ LINE 訊息送不出去', [('對象', m['to_id'][:10] + '…'), ('內容', (m['alt'] or '')[:40]), ('原因', err[:80]), ('處理', '請改用電話或在官方 LINE 手動傳')]
    notify.staff(con, title, rows[0][1], '/#line')
    w = con.execute('SELECT worker_id FROM line_users WHERE user_id=? AND worker_id IS NOT NULL', (m['to_id'],)).fetchone() if res != 'quota' else None
    if w and w[0] and '403' in (err or ''):     # 夥伴封鎖／不是好友 → 原卡片轉到管理群請人代問（負責人 ）
        con.execute('UPDATE line_users SET is_friend=0 WHERE account=? AND user_id=?', (m['account'] or WORK_ACCOUNT, m['to_id']))      # 每日檢查：只算這個帳號
        try:
            to_worker(con, w[0], m['kind'] or 'relay_w', m['alt'] or '', json.loads(m['payload']), m.get('order_id') or '')
        except ValueError:
            pass
        return
    if acc != WORK_ACCOUNT or res != 'quota':
        admin_notify(con, title, rows, '/#line', '#C62828', oid=m.get('order_id') or None)
    else:                              # 示範服務店額度用完：派單管理群也在示範服務店帳號上收不到 → 用企業線／生活線帳號一對一通知管理層
        for b in ('homeline', 'bizline'):
            for (su,) in con.execute('SELECT user_id FROM line_users WHERE account=? AND is_staff=1 AND is_friend=1', (b,)).fetchall():
                queue(con, b, su, 'admin', title, _txt(f"{title}\n{rows[0][1]}\n處理：{rows[1][1]}"))

def flush(con, limit=30):
    """同步送（測試、工具用）。正式環境由 sender_loop 送：打 LINE 時不握全站鎖。"""
    rows, toks = _claim(con, limit); sent = 0
    for m in rows:
        res, err = _push(None, m, con)              # 同步：照常走 api()（測試可替換）
        _record(con, m, res, err); sent += res == 'ok'
        con.commit()
    return sent

def kick():
    _wake.set()

def sender_loop(lock):
    import contextlib
    with lock, contextlib.closing(c.connect()) as con, con:     # 上次當機時正在送的：放回佇列（可能重送一次，總比漏送好）
        _outbox_cols(con)
        con.execute("UPDATE line_outbox SET status='queued' WHERE status='sending'")
    while True:
        _wake.wait(10); _wake.clear()
        try:
            with lock, contextlib.closing(c.connect()) as con, con:
                rows, toks = _claim(con, 30)
            for m in rows:                                      # 打 LINE 不握鎖：LINE 慢也不會卡住整個中控台
                tok = toks.get(m['account'] or 'demoshop')
                res, err = _push(tok, m) if tok else ('fail', f"{m['account']} 官方帳號尚未設定金鑰")
                with lock, contextlib.closing(c.connect()) as con, con:
                    _record(con, m, res, err)
        except Exception:
            traceback.print_exc()

def relay(acc, raw, signature):
    if acc not in RELAY:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(RELAY[acc], data=raw, headers={'Content-Type': 'application/json', 'X-Line-Signature': signature or ''}, method='POST'), timeout=8).read()
    except Exception:
        pass

def quota(con, acc='demoshop'):
    try:
        q = api(con, '/v2/bot/message/quota', acc=acc); u = api(con, '/v2/bot/message/quota/consumption', acc=acc)
        return {'limit': q.get('value'), 'type': q.get('type'), 'used': u.get('totalUsage')}
    except ValueError as e:
        return {'error': str(e)}

# ───────────── 示範 ─────────────
DEMO_TAG = '（示範）'

def demo(con, p):
    import board
    uid = p.get('user_id') or next(iter(sorted(test_users(con))), None)
    if not uid:
        raise ValueError('請先在測試名單放自己的 LINE')
    if p.get('step') == 'cleanup':
        import trash
        n = 0
        for (oid,) in con.execute("SELECT id FROM orders WHERE title LIKE ?", (DEMO_TAG + '%',)).fetchall():
            con.execute("UPDATE orders SET status='cancelled' WHERE id=? AND status IN ('confirmed','completed')", (oid,))
            con.execute("DELETE FROM line_outbox WHERE order_id=? AND status='queued'", (oid,))
            con.execute('UPDATE inquiries SET order_id=NULL WHERE order_id=?', (oid,))
            trash.to_trash(con, 'orders', oid, '示範資料'); n += 1
        con.execute('UPDATE line_users SET customer_id=NULL WHERE customer_id IN (SELECT id FROM customers WHERE name LIKE ?)', (DEMO_TAG + '%',))
        con.execute('UPDATE line_users SET worker_id=NULL WHERE worker_id IN (SELECT id FROM workers WHERE name LIKE ?)', (DEMO_TAG + '%',))
        for t in ('customers', 'workers'):
            for (rid,) in con.execute(f"SELECT id FROM {t} WHERE name LIKE ?", (DEMO_TAG + '%',)).fetchall():
                trash.to_trash(con, t, rid, '示範資料'); n += 1
        con.execute("DELETE FROM trash WHERE reason='示範資料'")
        return {'cleaned': n}
    _upsert_user(con, 'demoshop', uid)
    cu = con.execute("SELECT id FROM customers WHERE name=?", (DEMO_TAG + '客戶',)).fetchone()
    cid = cu[0] if cu else c.save(con, 'customers', {'name': DEMO_TAG + '客戶', 'status': 'active', 'line_account': 'demoshop', 'notes': '示範用，可刪除'})['id']
    site = con.execute('SELECT id FROM sites WHERE customer_id=?', (cid,)).fetchone()
    sid = site[0] if site else c.save(con, 'sites', {'customer_id': cid, 'name': '王小姐家（示範）', 'address': '○○縣○○市示範路 1 號',
                                                    'service_type': '一般到府服務', 'area': '大安區', 'checklist': '浴室水垢\n廚房油汙\n全室吸拖', 'visit_price': 180000})['id']
    wk = con.execute("SELECT id FROM workers WHERE name=?", (DEMO_TAG + '夥伴',)).fetchone()
    wid = wk[0] if wk else c.save(con, 'workers', {'name': DEMO_TAG + '夥伴', 'status': 'active', 'rate': '250', 'regions': '大安區', 'notes': '示範用，可刪除'})['id']
    con.execute("UPDATE line_users SET customer_id=?,worker_id=?,is_staff=0 WHERE account='demoshop' AND user_id=?", (cid, wid, uid))
    day = (dt.datetime.now(c.TZ).date() + dt.timedelta(days=3)).isoformat()
    o = c.save(con, 'orders', {'title': DEMO_TAG + '一般到府服務', 'site_id': sid, 'date': day, 'start': '09:00', 'end': '13:00', 'required_workers': 1,
                               'review_note': '示範工單', 'dispatch_note': '這是示範，可以放心按「我要接單」'})
    con.execute("UPDATE orders SET dispatch='open' WHERE id=?", (o['id'],))
    m = card_dispatch(con, o['id'], preferred=bool(p.get('preferred'))); queue(con, 'demoshop', uid, 'demo', m[0]['altText'], m, o['id'])
    board.log(con, o['id'], 'publish', '示範：派工卡傳到測試者 LINE')
    return {'order_id': o['id'], 'sent': 1}
