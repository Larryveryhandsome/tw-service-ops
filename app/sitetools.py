"""現場工具（「開單之前需要請業主回覆我們需不需要自備工具」）。

舊 Google 表單就有「現場工具」這題（有，具全／有，基本工具／僅有掃把畚斗、沒有 → 需自備所有工具），新系統補回來：
- 客人估價單：必填（時數制服務，例如陪玩陪讀，不用答）。報價單是管理層代開、客人沒填過 → 簽收頁要先回答才能簽。
- 簽收建工單 → 記在案場（sites.tools／tools_note），之後同一個案場都沿用；夥伴的派工卡、工作頁、排定卡都寫「工具」。
- 案場還沒有答案的工單（後台建立、舊客戶）：系統派工前先用 LINE 問業主（綁了 LINE 的），最多等 24 小時；
  3 天內開工、沒綁 LINE、或 24 小時沒回 → 不等，預設「請自備工具」（最保險）。業主之後回覆，已接的夥伴會收到更新。
"""
import datetime as dt
import json
import core as c
import integrations as ix

KINDS = ('full', 'basic', 'none')
CUSTOMER = {'full': '現場工具齊全，用現場的', 'basic': '只有基本工具，其他請自備', 'none': '沒有工具，請全部自備'}
STAFF = {'full': '業主工具齊全（用業主的）', 'basic': '業主只有基本工具，其他自備', 'none': '業主沒有工具，全部自備', '': '還沒問業主'}
WAIT_H = 24

def worker_text(s):
    """給夥伴看的一句話。"""
    t, note = (s or {}).get('tools') or '', ((s or {}).get('tools_note') or '').strip()
    base = {'full': '用業主的工具（不用自備）', 'basic': '請自備工具（業主只有基本工具）', 'none': '請自備所有工具'}.get(t, '業主還沒回覆，請自備工具')
    return base + (f'；業主說：{note[:60]}' if note else '')

def site_of(con, o):
    return dict(con.execute('SELECT * FROM sites WHERE id=?', (o.get('site_id') or '',)).fetchone() or {})

def needs_line(con, line_id, account):
    """這個業務線要不要問工具（時數制服務不用）。"""
    import brands
    try:
        return brands.line_for(con, brands.pick(account), line_id).get('kind') not in ('hourly', 'trash')      # 垃圾清收不用工具
    except Exception:
        return True

def set_tools(con, sid, kind, note='', by=''):
    if kind not in KINDS:
        raise ValueError('請選一個：工具齊全／只有基本的／沒有工具')
    s = con.execute('SELECT * FROM sites WHERE id=?', (sid,)).fetchone()
    if not s:
        raise ValueError('找不到案場')
    con.execute('UPDATE sites SET tools=?,tools_note=?,version=version+1 WHERE id=?', (kind, str(note or '').strip()[:200], sid))
    c.audit(con, 'site_tools', 'sites', sid, before={'tools': s['tools'], 'note': s['tools_note']}, after={'tools': kind, 'note': note, 'by': by})
    return kind

def ask(con, oid):
    """用業主自己品牌的官方 LINE 問一次（同一個案場 24 小時內不重問）。回傳有沒有傳出去。"""
    import line_bot as lb
    o = c.get(con, 'orders', oid); s = site_of(con, o)
    now = dt.datetime.now(c.TZ)
    if o['date'] < now.date().isoformat() or (o['date'] == now.date().isoformat() and (o['start'] or '00:00') <= now.strftime('%H:%M')):
        return False                     # 稽核 A4：已經開工了 → 不問（下一場再問）
    if not s or s.get('tools'):
        return False
    acc, uid = lb.customer_target(con, o['customer_id'])
    if not uid:
        return False
    k = f"tools_ask:{s['id']}"
    if ix.setting(con, k) and ix.setting(con, k) > (dt.datetime.now(c.TZ) - dt.timedelta(hours=WAIT_H)).isoformat():
        return True
    import prepq                           # 開門方式、垃圾、工具一張卡一起問（只問還不知道的）
    if not prepq.send(con, oid, acc, uid, 'tools_ask'):
        return False                       # 稽核 L1：測試／關閉模式沒真的傳出去 → 不算問過，不白等 24 小時
    ix.set_setting(con, k, c.now())
    return True

def tools_bubble(con, o, s):
    import line_bot as lb
    cu = dict(con.execute('SELECT * FROM customers WHERE id=?', (o['customer_id'],)).fetchone() or {}) if o.get('customer_id') else {}
    return lb._bubble(lb._header('服務前想先確認 🧹', lb.when(o), brand=lb.brand_of(cu)),
        [lb._t('為了讓服務人員帶對工具，想請問您家裡（現場）的工具：', wrap=True),
         lb._t('點一個就好；之後同一個地方會沿用，有變動再跟我們說。', size='sm', color=lb.SUB, wrap=True, margin='md')],
        [lb._btn(CUSTOMER['full'], data=f"a=tools&s={s['id']}&v=full", primary=True), lb._btn(CUSTOMER['basic'], data=f"a=tools&s={s['id']}&v=basic"),
         lb._btn(CUSTOMER['none'], data=f"a=tools&s={s['id']}&v=none"), lb._btn('我打字說明（家裡有哪些）', data=f"a=tools&s={s['id']}&v=note")])

def order_needs(con, o):
    """這張單要不要問工具：從報價單來的看業務線（時數制服務不用，稽核 M5）。"""
    q = con.execute('SELECT account,form FROM quotes WHERE order_id=? ORDER BY created_at DESC LIMIT 1', (o['id'],)).fetchone()
    if not q:
        return True
    try:
        f = json.loads(q['form'] or '{}')
    except ValueError:
        return True
    return needs_line(con, f.get('line'), q['account'])

def gate(con, o):
    """自動派工前：案場還不知道工具 → 先問業主、等回覆（回傳 True＝這次先不要派）。"""
    s = site_of(con, o)
    if not s or s.get('tools') or not order_needs(con, o):
        return False
    import prepq
    if prepq.old_customer(con, o.get('customer_id')):
        return False                          # 舊客戶不問業主、不等（管理層在中控台補）
    if o['date'] <= (dt.datetime.now(c.TZ).date() + dt.timedelta(days=3)).isoformat():
        return False                          # 3 天內開工：不等，夥伴卡寫「業主還沒回覆，請自備工具」
    asked = ix.setting(con, f"tools_ask:{s['id']}")
    if not asked:
        return ask(con, o['id'])              # 沒綁 LINE 問不到 → False（不等）
    return asked > (dt.datetime.now(c.TZ) - dt.timedelta(hours=WAIT_H)).isoformat()

def answered(con, acc, uid, lu, sid, kind, note=''):
    """業主回覆 → 記在案場；接下來已排的夥伴收到更新。回傳給業主的一句話。"""
    import line_bot as lb
    s = con.execute('SELECT * FROM sites WHERE id=?', (sid,)).fetchone()
    if not s or ((lu or {}).get('customer_id') != s['customer_id'] and uid not in lb.test_users(con)):
        return '收到，專人會跟您確認 🙏'
    set_tools(con, sid, kind, note, (lu or {}).get('display_name') or '業主')
    s = dict(con.execute('SELECT * FROM sites WHERE id=?', (sid,)).fetchone())
    today = dt.datetime.now(c.TZ).date().isoformat()
    for a in c.rows(con, "SELECT a.worker_id,o.id oid,o.date,o.start,o.end FROM assignments a JOIN orders o ON o.id=a.order_id WHERE o.site_id=? AND a.status='accepted' "
                         "AND o.date>=? AND o.status NOT IN ('cancelled','archived','completed')", (sid, today)):
        lb.to_worker(con, a['worker_id'], 'tools_update', '工具更新', lb._txt(f"🧹 {lb.md(a['date'])} {a['start']}–{a['end']} 那場的工具：{worker_text(s)}"), a['oid'])
    return {'full': '收到，謝謝您！服務人員會用您家裡的工具 🙏（請先放在好找的地方）',
            'basic': '收到，謝謝您！服務人員會自備其他工具 🙏', 'none': '收到，謝謝您！服務人員會自備所有工具 🙏'}[kind] + (f'\n已記下：{note[:60]}' if note else '')

def pending(con, days=14):
    """已排、days 天內的工作，案場還不知道工具：[(案場, 客戶, 最近一筆工單, 業主有沒有綁 LINE)]（一個案場一筆）。"""
    import line_bot as lb
    out, seen = [], set()
    for r in c.rows(con, "SELECT o.id oid,o.date,o.start,o.customer_id,s.id sid,s.name sname,cu.name cname FROM orders o JOIN sites s ON s.id=o.site_id "
                         "LEFT JOIN customers cu ON cu.id=o.customer_id WHERE COALESCE(s.tools,'')='' AND o.date BETWEEN ? AND ? "
                         "AND o.status NOT IN ('cancelled','archived','completed') AND COALESCE(o.review_note,'') NOT LIKE '%示範%' ORDER BY o.date,o.start",
                    (dt.datetime.now(c.TZ).date().isoformat(), (dt.datetime.now(c.TZ).date() + dt.timedelta(days=days)).isoformat())):
        if r['sid'] in seen:
            continue
        seen.add(r['sid'])
        out.append({**r, 'bound': bool(lb.customer_target(con, r['customer_id'])[1])})
    return out

def ask_pending(con):
    """店長決定「問」→ 已排的工作一個案場問一次（綁了 LINE 的）。回傳問到的客戶名。"""
    return [r['cname'] or r['sname'] for r in pending(con) if r['bound'] and ask(con, r['oid'])]
