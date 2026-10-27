"""店長「示範服務店｜服務預約管理」試算表自動化（「店長現在仍不斷在用人工的方式弄這個表單…幫我檢視為甚麼你沒有讓她全部自動化」）。

做法：系統出一份即時表（CSV），試算表新分頁用 =IMPORTDATA(網址) 讀進去，Google 每小時左右自動更新 → 店長不用再手打。
- 欄位照她的「N月排序」：日期、時間、客戶、服務人員/時薪、進帳、支出、毛利、收款、排班狀態、地址、地圖、方案備註、中控台連結。
- 進帳＝結帳頁同一套（board.suggested_charge）；支出＝夥伴薪資同一套（board.est_pay，有打卡／核定用核定）。數字跟中控台結帳、夥伴收入一致。
- 期間照她的分頁：c=2026-10 → ～11/9（每月 10 號到下個月 9 號）。
- 網址帶隨機金鑰（settings sheetfeed_token），只放在她的試算表；不含客戶電話。
"""
import csv, datetime as dt, io, secrets
import core as c
import integrations as ix

ST = {'completed': '完成', 'confirmed': '已排定', 'draft': '待確認', 'review': '待確認', 'cancelled': '取消'}
WD = '一二三四五六日'

def token(con):
    t = ix.setting(con, 'sheetfeed_token')
    if not t:
        t = secrets.token_urlsafe(24); ix.set_setting(con, 'sheetfeed_token', t)
    return t

def period(cyc):
    y, m = map(int, cyc.split('-'))
    a = dt.date(y, m, 10)
    b = (dt.date(y + (m == 12), m % 12 + 1, 9))
    return a, b

def rows(con, cyc):
    import board, payments, line_bot
    a, b = period(cyc)
    sites = {s['id']: dict(s) for s in c.rows(con, 'SELECT * FROM sites')}
    out, tin, tout = [], 0, 0
    for o in c.rows(con, "SELECT o.*,cu.name cname FROM orders o LEFT JOIN customers cu ON cu.id=o.customer_id WHERE o.date BETWEEN ? AND ? "
                         "AND o.status IN ('review','draft','confirmed','completed','cancelled') AND COALESCE(o.review_note,'') NOT IN ('示範工單') "
                         "AND COALESCE(cu.name,'') NOT LIKE '%示範%' ORDER BY o.date,o.start", (a.isoformat(), b.isoformat())):
        o = dict(o); s = sites.get(o['site_id']) or {}
        crew = c.rows(con, "SELECT a.worker_id,w.name,COALESCE(a.rate,w.rate) rate FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND (a.status='accepted' OR (a.amount>0 AND ?='cancelled'))",
                      (o['id'], o['status']))
        pay = 0; who = []
        for w in crew:
            p, _ = board.est_pay(con, o, w['worker_id'])
            pay += p or 0; who.append(f"{w['name']}/{w['rate'] or ''}".rstrip('/'))
        try:
            inc, _ = board.suggested_charge(con, o, s)
        except Exception:
            inc = None
        inc = 0 if o['status'] == 'cancelled' else (inc or 0)
        prs = [r[0] for r in con.execute("SELECT status FROM payment_requests WHERE order_id=? AND status!='cancelled'", (o['id'],))]
        pre = bool(prs) and all(s == 'confirmed' for s in prs)
        pre_txt = '' if not prs or pre else '待核對' if 'reported' in prs else '未收（收款單）'
        inv = con.execute("SELECT i.status FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.order_id=? LIMIT 1", (o['id'],)).fetchone()
        paid = '取消' if o['status'] == 'cancelled' else '已收' if pre else pre_txt if pre_txt else ('已開帳' if inv and inv[0] == 'issued' else '已收' if inv and inv[0] == 'paid' else '未開帳' if o['status'] == 'completed' else '')
        d = dt.date.fromisoformat(o['date'])
        tin += inc; tout += pay
        out.append([f"{d.month}/{d.day}({WD[d.weekday()]})", f"{o['start']}-{o['end']}", o['cname'] or s.get('name') or o['title'], '、'.join(who) or '（還沒排人）',
                    inc // 100 if inc else '', pay // 100 if pay else '', (inc - pay) // 100 if inc else '', paid, ST.get(o['status'], o['status']),
                    s.get('address') or s.get('area') or '', __import__('sitegeo').nav_url(s) if s.get('address') else '', (o['scope'] or '').replace('\n', '、')[:80] if not o.get('scope_hold') else '',
                    f"{line_bot.BASE_URL}/#o/{o['id']}"])
    return out, tin, tout

def csv_text(con, cyc):
    a, b = period(cyc)
    rs, tin, tout = rows(con, cyc)
    f = io.StringIO(); w = csv.writer(f)
    w.writerow(['日期', '時間', '客戶', '服務人員/時薪', '進帳', '支出（夥伴薪資）', '毛利', '收款', '排班狀態', '服務地址', '地圖', '服務範圍／備註', '中控台（點了直接開工單）'])
    w.writerows(rs)
    w.writerow([]); w.writerow([f'合計（{a.month}/{a.day}～{b.month}/{b.day}）', f'{len(rs)} 場', '', '', tin // 100, tout // 100, (tin - tout) // 100])
    w.writerow([f"系統自動產生，{c.now()[:16].replace('T', ' ')} 更新；要改資料請到中控台（這頁改了會被蓋掉）"])
    return f.getvalue()
