"""收款紀錄（「要規劃一個可以讓管理群點選的、可以看到歷史付款紀錄的」）。

一個地方看全部進來的錢：收款單（LINE Pay、匯款確認、客戶回報已匯款）、商城付款、帳單收款、預收款。
- 中控台「更多 › 收款紀錄」（#payhist）：依月份、方式、品牌篩選，每筆點了直接開工單／報價單，上面有合計。
- 派單管理群：打「收款紀錄」→ 回一張卡（本月合計＋最近 8 筆＋按鈕打開中控台）；每張收款通知卡也有「看收款紀錄」按鈕。
只有看得到金額的人（money 權限）看得到。
"""
import core as c

METHOD = {'linepay': 'LINE Pay', 'transfer': '匯款', 'cash': '現金', 'shop': '商城', 'other': '其他'}

def rows(con, month=''):
    """[{at, kind, method, status, name, label, amount(元), brand, order_id, quote_token, ref}]，新的在前。month＝'2026-10'（空白＝全部）。"""
    import brands, payments
    payments.ensure(con)
    out = []
    like = (month or '') + '%'
    lp = {r[0]: r[1] for r in con.execute("SELECT pid,confirmed_at FROM linepay_tx WHERE status IN ('confirmed','partial_refund') AND mode='production'")} if con.execute("SELECT 1 FROM sqlite_master WHERE name='linepay_tx'").fetchone() else {}
    for r in c.rows(con, "SELECT p.*,cu.name cname,q.token qtoken,q.account qacc,cu.line_account cacc FROM payment_requests p LEFT JOIN customers cu ON cu.id=p.customer_id "
                         "LEFT JOIN quotes q ON q.id=p.quote_id WHERE p.status IN ('confirmed','reported') AND COALESCE(NULLIF(p.confirmed_at,''),p.reported_at) LIKE ? "
                         "AND COALESCE(cu.name,'') NOT LIKE '%示範%'", (like,)):
        at = r['confirmed_at'] or r['reported_at']
        m = 'linepay' if r['id'] in lp else 'transfer'
        out.append({'at': at, 'kind': 'request', 'method': METHOD[m], 'status': '已收款' if r['status'] == 'confirmed' else '客戶回報已匯款（待核對）',
                    'name': r['cname'] or '—', 'label': r['label'] or '', 'amount': (r['amount'] or 0) // 100, 'brand': brands.name(r['qacc'] or r['cacc'] or 'demoshop'),
                    'order_id': r['order_id'] or '', 'quote_token': r['qtoken'] or '', 'ref': r['ref'] or '', 'by': r['confirmed_by'] or '', 'id': r['id']})
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='shop_orders'").fetchone():
        for r in c.rows(con, "SELECT * FROM shop_orders WHERE COALESCE(paid_at,'')!='' AND paid_at LIKE ? AND status!='cancelled'", (like,)):
            out.append({'at': r['paid_at'], 'kind': 'shop', 'method': '商城・' + (r['paid_how'] or '付款'), 'status': '已收款', 'name': r['name'] or '—',
                        'label': f"商城訂單 {r['id']}", 'amount': r['total'] or 0, 'brand': brands.name(r['acc'] or 'demoshop'), 'order_id': '', 'quote_token': '', 'ref': '', 'by': r['handled_by'] or '', 'id': r['id']})
    has = lambda t: con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
    for r in (c.rows(con, "SELECT pm.*,i.customer_id,i.period,cu.name cname FROM payments pm JOIN invoices i ON i.id=pm.invoice_id LEFT JOIN customers cu ON cu.id=i.customer_id WHERE pm.date LIKE ? AND COALESCE(pm.receipt_id,'')=''", (like,)) if has('payments') else []):      # 每日檢查：預收撥過去的已經算在預收款
        out.append({'at': r['date'] + 'T00:00', 'kind': 'invoice', 'method': r['method'] or '帳單收款', 'status': '已收款', 'name': r['cname'] or '—',
                    'label': f"{r['period']} 帳單", 'amount': (r['amount'] or 0) // 100, 'brand': '', 'order_id': '', 'quote_token': '', 'ref': r['reference'] or '', 'by': '', 'id': r['id']})
    for r in (c.rows(con, "SELECT rc.*,cu.name cname FROM receipts rc LEFT JOIN customers cu ON cu.id=rc.customer_id WHERE rc.date LIKE ? AND rc.status!='void'", (like,)) if has('receipts') else []):
        out.append({'at': r['date'] + 'T00:00', 'kind': 'receipt', 'method': r['method'] or '預收款', 'status': {'verified': '已對帳'}.get(r['status'], '待對帳'), 'name': r['cname'] or '—',
                    'label': {'deposit': '保證金', 'prepay': '預收'}.get(r['kind'], '收款'), 'amount': (r['amount'] or 0) // 100, 'brand': '', 'order_id': '', 'quote_token': '', 'ref': r['reference'] or '', 'by': '', 'id': r['id']})
    out.sort(key=lambda x: x['at'], reverse=True)
    return out

def summary(rs):
    got = [x for x in rs if not x['status'].startswith('客戶回報')]
    by = {}
    for x in got:
        k = x['method'].split('・')[0]; by[k] = by.get(k, 0) + x['amount']
    return {'total': sum(x['amount'] for x in got), 'count': len(got), 'by_method': by,
            'waiting': sum(x['amount'] for x in rs if x['status'].startswith('客戶回報')), 'waiting_n': sum(1 for x in rs if x['status'].startswith('客戶回報'))}

def view(con, month=''):
    rs = rows(con, month)
    return {'month': month, 'rows': rs, 'summary': summary(rs)}

def card(con):
    """派單管理群打「收款紀錄」：本月合計＋最近 8 筆＋打開中控台。"""
    import line_bot as lb
    m = c.now()[:7]
    rs = rows(con, m); sm = summary(rs); recent = rows(con)[:8]
    body = [lb._row('本月已收', f"NT$ {sm['total']:,}（{sm['count']} 筆）")] + [lb._row(k, f'NT$ {v:,}') for k, v in sorted(sm['by_method'].items(), key=lambda x: -x[1])]
    if sm['waiting_n']:
        body.append(lb._row('待核對', f"NT$ {sm['waiting']:,}（{sm['waiting_n']} 筆客戶回報已匯款）"))
    body.append(lb._t('最近收款', weight='bold', size='sm', margin='lg'))
    body += [lb._t(f"{x['at'][5:10].replace('-', '/')}　{x['name'][:8]}　NT$ {x['amount']:,}　{x['method']}" + ('（待核對）' if x['status'].startswith('客戶回報') else ''), size='xs', wrap=True, color=lb.SUB) for x in recent] or [lb._t('還沒有收款紀錄', size='sm')]
    return lb.flex(f"💰 收款紀錄：本月 NT$ {sm['total']:,}", lb._bubble(lb._header('💰 收款紀錄', f'{int(m[5:])} 月', '#2E7D32'), body,
                   [lb._btn('看全部收款紀錄（中控台）', uri=f'{lb.BASE_URL}/#payhist', primary=True)]))
