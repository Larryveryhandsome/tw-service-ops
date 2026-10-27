"""個資法：蒐集告知、同意留存、當事人查詢／刪除。

- notice(brand)：告知事項（目的、類別、期間、地區、對象、方式、權利、不提供的影響）。/privacy?b= 顯示。
- require(p)：所有對外表單送出時要勾「已閱讀個資告知」，後端檢查（不只前端）；record() 留存版本、時間、IP。
- 客戶在官方 LINE「我的預約 › 我的資料」可以查看系統存了什麼、申請刪除；刪除由店長點選核准。
  刪除＝去識別化：姓名、電話、地址、LINE 綁定、對話文字、報價表單上的個資清掉；帳務金額依稅法保留（不含可識別資料）。
原則上的條文，最終以律師意見為準。
"""
import json, re
import core as c

VERSION = '2026-10-09'
FORMS = {'book': '線上估價／預約', 'hi': '留基本資料', 'apply': '夥伴應徵', 'join': '夥伴報到', 'site': '補案場資料'}

def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS privacy_consents(id TEXT PRIMARY KEY, at TEXT, form TEXT, version TEXT, ip TEXT, account TEXT, line_user TEXT, ref TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS privacy_requests(id TEXT PRIMARY KEY, at TEXT, account TEXT, line_user TEXT, customer_id TEXT, kind TEXT, status TEXT DEFAULT 'pending', decided_by TEXT DEFAULT '', decided_at TEXT DEFAULT '')")

def notice(brand_name='本公司'):
    b = brand_name or '本公司'
    return {'version': VERSION, 'title': f'{b} 個人資料蒐集告知', 'sections': [
        ('蒐集者', f'{b}（示範服務股份有限公司經營）。'),
        ('蒐集目的', '提供服務的估價、報價、簽約、排班派工、聯繫通知、收款與發票、售後關心與客訴處理；夥伴的應徵、派工與薪資結算。'),
        ('個資類別', '姓名、電話、服務地址與進場方式、LINE 顯示名稱與使用者識別碼、案場照片、線上簽名；夥伴另含可服務區域、技能、可排時間與工時紀錄。'),
        ('利用期間', '服務關係存續期間，及服務結束後原則上 5 年（帳務、發票資料依稅法規定保存期間保存）。'),
        ('利用地區', '中華民國境內（LINE、雲端主機等服務商可能於境外處理，僅限提供服務所需）。'),
        ('利用對象', '本公司同仁；執行該次服務的服務人員（僅限地址、進場方式、服務範圍，確認排定後才提供）；金流、LINE 等受託服務商。不會出售或提供給無關第三人。'),
        ('利用方式', '以電子方式建檔、傳送 LINE 通知、列印報價單與帳務文件。'),
        ('您的權利', '您可以查詢、閱覽、要求複製、補充或更正、停止蒐集利用、刪除您的個人資料：請在官方 LINE 選單「我的預約 › 我的資料」點選，或直接留言。法令規定須保存的帳務資料，會去除可識別您的部分後保存。'),
        ('不提供的影響', '若不提供必要資料（例如聯絡電話、服務地址），原則上將無法為您估價、派工或提供服務。'),
    ]}

def require(p):
    if p.get('consent') is not True:
        raise ValueError('請勾選「已閱讀並同意個人資料蒐集告知」後再送出（若已勾選，請重新整理頁面）')

def record(con, p, form, ip='', account='', line_user='', ref=''):
    ensure(con)
    con.execute('INSERT INTO privacy_consents(id,at,form,version,ip,account,line_user,ref) VALUES(?,?,?,?,?,?,?,?)',
                (c.uid('pc'), c.now(), form, str(p.get('consent_version') or VERSION)[:20], (ip or '')[:60], account or '', line_user or '', ref or ''))

# ───────────── 當事人權利：查詢、刪除 ─────────────
def summary(con, cid):
    """系統存了這位客戶哪些資料（給本人看／複製）。"""
    cu = c.get(con, 'customers', cid)
    sites = c.rows(con, 'SELECT name,address,entry_notes FROM sites WHERE customer_id=?', (cid,))
    n = con.execute("SELECT COUNT(*),MIN(date),MAX(date) FROM orders WHERE customer_id=?", (cid,)).fetchone()
    lines = [f"姓名／稱呼：{cu['name']}", f"電話：{cu['phone'] or '（未留）'}", f"LINE 名稱：{cu.get('line_name') or '—'}"]
    for s in sites:
        lines.append(f"服務地點：{s['name']}｜{s['address'] or '—'}" + (f"｜進場：{s['entry_notes'][:40]}" if s['entry_notes'] else ''))
    lines.append(f"服務紀錄：{n[0]} 筆" + (f"（{n[1]}～{n[2]}）" if n[0] else ''))
    lines.append(f"報價單：{con.execute('SELECT COUNT(*) FROM quotes WHERE customer_id=?', (cid,)).fetchone()[0]} 張（含簽名）")
    return '\n'.join(lines)

def request(con, acc, uid, cid, kind='erase'):
    ensure(con)
    old = con.execute("SELECT id FROM privacy_requests WHERE customer_id=? AND status='pending'", (cid,)).fetchone()
    if old:
        return old[0], False
    rid = c.uid('pr')
    con.execute('INSERT INTO privacy_requests(id,at,account,line_user,customer_id,kind) VALUES(?,?,?,?,?,?)', (rid, c.now(), acc, uid, cid, kind))
    c.audit(con, 'privacy_request', 'customers', cid, after={'kind': kind, 'line_user': uid})
    return rid, True

def blockers(con, cid):
    """還有進行中的服務或未收款時不能刪（要先處理完）。"""
    out = []
    live = con.execute("SELECT COUNT(*) FROM orders WHERE customer_id=? AND status IN ('review','draft','confirmed')", (cid,)).fetchone()[0]
    if live:
        out.append(f'還有 {live} 筆進行中的預約')
    try:
        unpaid = con.execute("SELECT COUNT(*) FROM payment_requests WHERE customer_id=? AND status IN ('pending','reported')", (cid,)).fetchone()[0]
    except Exception:
        unpaid = 0
    if unpaid:
        out.append(f'還有 {unpaid} 筆款項未結清')
    return out

def erase(con, cid, by):
    """去識別化：可識別個資清掉；帳務金額、服務日期保留（稅法）。"""
    b = blockers(con, cid)
    if b:
        raise ValueError('、'.join(b) + '，請先處理完再刪除')
    cu = c.get(con, 'customers', cid)
    digits = re.sub(r'\D', '', cu['phone'] or '')
    tag = f'已刪除客戶-{cid[-4:]}'
    con.execute("UPDATE customers SET name=?,phone='',line_name='',notes='（當事人申請刪除個資）',preferred_workers='',status='ended',version=version+1 WHERE id=?", (tag, cid))
    con.execute("UPDATE sites SET name=?,address='（已刪除）',entry_notes='',navigation='',notes='',checklist='',version=version+1 WHERE customer_id=?", (tag, cid))
    for q in c.rows(con, 'SELECT id,form FROM quotes WHERE customer_id=?', (cid,)):
        try:
            f = json.loads(q['form'] or '{}')
        except ValueError:
            f = {}
        for k in ('name', 'phone', 'address', 'line_name', 'company', 'tax_id', 'notes', 'entry', 'bnb_name'):
            if k in f:
                f[k] = '（已刪除）'
        con.execute("UPDATE quotes SET form=?,signature='',signer_name='（已刪除）' WHERE id=?", (json.dumps(f, ensure_ascii=False), q['id']))
    con.execute("DELETE FROM quote_photos WHERE quote_id IN (SELECT id FROM quotes WHERE customer_id=?)", (cid,))
    for (oid_,) in con.execute('SELECT id FROM orders WHERE customer_id=?', (cid,)).fetchall():          # 稽核 M7：照片授權紀錄改成「收回」→ 匯出工具會清掉已匯出的照片
        if con.execute('SELECT 1 FROM settings WHERE key=?', (f'photo_share:{oid_}',)).fetchone():
            con.execute('UPDATE settings SET value=? WHERE key=?', ('{"ok": false, "erased": true}', f'photo_share:{oid_}'))
    uids = [r[0] for r in con.execute('SELECT user_id FROM line_users WHERE customer_id=?', (cid,))]
    for u in uids:
        con.execute("UPDATE line_inbox SET text='',data='' WHERE user_id=?", (u,))
        con.execute("DELETE FROM settings WHERE key LIKE ? OR key LIKE ? OR key LIKE ?", (f'cmsg:%:{u}', f'cmsg_txt:%:{u}', f'cmsg_rem:%:{u}'))     # 客戶留言提醒暫存的原文
        con.execute("UPDATE line_users SET customer_id=NULL,display_name='',picture_url='',last_text='' WHERE user_id=?", (u,))
    con.execute("UPDATE inquiries SET name=?,phone='',line_name='',address='',notes='',detail='{}' WHERE customer_id=?", (tag, cid))
    # 資安：補齊——照片（含檔案）、作品集引用、見證、聯絡人、別名、舊對話、工單文字、已送出的 LINE 內容、綁定申請
    import automation, testimonials
    ensure(con); automation.ensure(con); testimonials.ensure(con)          # 個資申請、作品集、見證資料表（舊資料庫可能還沒建）
    oids = [r[0] for r in con.execute('SELECT id FROM orders WHERE customer_id=?', (cid,))]
    q = ','.join('?' * len(oids))
    if oids:
        aids = [r[0] for r in con.execute(f'SELECT id FROM attachments WHERE order_id IN ({q})', oids)]
        if aids:
            qa = ','.join('?' * len(aids))
            con.execute(f'DELETE FROM portfolio WHERE attachment_id IN ({qa})', aids)          # 夥伴作品集不能再用這位客戶家裡的照片
            con.execute(f'DELETE FROM attachment_blobs WHERE id IN ({qa})', aids)             # 照片檔案本身
            con.execute(f'DELETE FROM attachments WHERE id IN ({qa})', aids)                  # /p/ 網址跟著失效
        con.execute(f"UPDATE orders SET notes='',field_report='',worker_note='',extra='',title=? WHERE id IN ({q})", (tag, *oids))
        if 'original_json' in {r[1] for r in con.execute('PRAGMA table_info(orders)')}:
            con.execute(f"UPDATE orders SET original_json='' WHERE id IN ({q})", oids)          # Excel 匯入時留的原始列（有姓名、地址）
        con.execute(f"UPDATE service_reports SET notes='（已刪除）' WHERE order_id IN ({q})", oids)
        con.execute(f"UPDATE service_cases SET detail='（已刪除）',resolution='' WHERE order_id IN ({q})", oids)
        con.execute(f"UPDATE line_outbox SET payload='[]',alt='（已刪除）' WHERE order_id IN ({q})", oids)     # 給夥伴的卡片含地址、開門說明
    con.execute("DELETE FROM testimonials WHERE customer_id=?" + (f" OR order_id IN ({q})" if oids else ''), (cid, *oids))
    con.execute('DELETE FROM contacts WHERE customer_id=?', (cid,))
    con.execute('DELETE FROM customer_aliases WHERE customer_id=?', (cid,))
    con.execute('DELETE FROM conversation_notes WHERE customer_id=?', (cid,))
    con.execute("UPDATE tasks SET detail='（已刪除）',resolution='' WHERE customer_id=?", (cid,))
    for (tid,) in con.execute('SELECT id FROM chat_threads WHERE customer_id=?', (cid,)).fetchall():
        con.execute("UPDATE chat_messages SET content='',sender='（已刪除）',media='' WHERE thread_id=?", (tid,))
        con.execute("UPDATE chat_threads SET notes='',customer_id=NULL WHERE id=?", (tid,))
    for u in uids:
        con.execute("UPDATE line_outbox SET payload='[]',alt='（已刪除）' WHERE to_id=?", (u,))
        con.execute("UPDATE bind_requests SET name=?,phone='',line_name='' WHERE user_id=?", (tag, u))
    con.execute("UPDATE bind_requests SET name=?,phone='',line_name='' WHERE ref_id=?", (tag, cid))
    import shop
    shop.ensure(con)
    con.execute("UPDATE shop_orders SET name=?,phone='',note='',ip='' WHERE (buyer='customer' AND ref_id=?)" + (f" OR uid IN ({','.join('?' * len(uids))})" if uids else ''), (tag, cid, *uids))   # 商城訂單（金額保留）
    import pairing
    for (acc_, u) in con.execute('SELECT account,user_id FROM line_users WHERE user_id IN (%s)' % ','.join('?' * len(uids)), uids).fetchall() if uids else []:
        pairing.skip(con, acc_, u, 'no', by + '（個資刪除）')                 # 不要再請店長把剛刪掉的人配回去
    if len(digits) >= 8:                   # 稽核紀錄裡的電話號碼也遮掉
        for r in c.rows(con, "SELECT id AS rid,before_json,after_json FROM audit WHERE before_json LIKE ? OR after_json LIKE ?", (f'%{digits[-8:]}%', f'%{digits[-8:]}%')) if _has_audit_json(con) else []:
            con.execute('UPDATE audit SET before_json=?,after_json=? WHERE id=?', (_mask(r["before_json"], digits), _mask(r["after_json"], digits), r["rid"]))
    con.execute("UPDATE privacy_requests SET status='done',decided_by=?,decided_at=? WHERE customer_id=? AND status='pending'", (by, c.now(), cid))
    c.audit(con, 'privacy_erase', 'customers', cid, after={'by': by})
    return tag

def _has_audit_json(con):
    cols = {r[1] for r in con.execute('PRAGMA table_info(audit)')}
    return {'before_json', 'after_json'} <= cols

def _mask(s, digits):
    return (s or '').replace(digits, '＊' * len(digits)).replace(digits[-9:], '＊' * 9)
