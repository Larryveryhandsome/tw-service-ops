"""Auditable service entitlements, actual work, receivables and chat archive."""
import core as c

def required(p,*fields):
    if any(not str(p.get(k,'')).strip() for k in fields):
        raise ValueError('必填：'+'、'.join(fields))

def replay(con,table,p):
    required(p,'request_key')
    old=con.execute(f'SELECT * FROM {table} WHERE request_key=?',(p['request_key'],)).fetchone()
    if old:
        for k in ('amount','customer_id','receipt_id','invoice_id'):
            if k in p and k in old.keys() and old[k]!=p[k]: raise ValueError('重複識別碼內容不一致')
        return dict(old)

def plan_balance(con,key):
    available=con.execute('SELECT COALESCE(SUM(units),0) FROM entitlements WHERE plan_id=?',(key,)).fetchone()[0]
    reserved=con.execute("SELECT COUNT(*) FROM orders WHERE plan_id=? AND status='confirmed'",(key,)).fetchone()[0]
    return {'remaining':available,'reserved':reserved,'available':available-reserved}

def validate_plan_order(con,o):
    plan=c.get(con,'plans',o['plan_id'])
    if plan['status']!='active' or plan['customer_id']!=o['customer_id'] or not plan['valid_from']<=o['date']<=plan['valid_to']:
        raise ValueError('方案狀態、客戶或有效期間不符')
    b=plan_balance(con,plan['id'])
    if b['available']+(1 if o['status']=='confirmed' else 0)<1: raise ValueError('方案可排次數不足')

def consume_plan(con,o):
    validate_plan_order(con,o)
    con.execute('INSERT INTO entitlements(id,plan_id,order_id,units,kind,reason) VALUES(?,?,?,-1,?,?)',
                (c.uid('ent'),o['plan_id'],o['id'],'consume','服務完成，驗收另行追蹤'))

def receipt_balance(con,key):
    r=c.get(con,'receipts',key)
    allocated=con.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE receipt_id=?',(key,)).fetchone()[0]
    refunded=con.execute('SELECT COALESCE(SUM(amount),0) FROM receipt_refunds WHERE receipt_id=?',(key,)).fetchone()[0]
    return {'allocated':allocated,'refunded':refunded,'available':r['amount']-allocated-refunded}

def command(con,kind,p):
    result=None
    if kind=='plan':
        required(p,'customer_id','name','valid_from','valid_to','terms')
        c.get(con,'customers',p['customer_id']); c.date(p['valid_from']); c.date(p['valid_to'])
        if p['valid_from']>p['valid_to']: raise ValueError('方案到期日不可早於開始日')
        key=c.uid('plan'); units=c.integer(p['units'],'次數',1); price=c.integer(p['price'],'方案金額')
        con.execute('INSERT INTO plans(id,customer_id,name,valid_from,valid_to,price,units,terms) VALUES(?,?,?,?,?,?,?,?)',
                    (key,p['customer_id'],p['name'],p['valid_from'],p['valid_to'],price,units,p['terms']))
        con.execute('INSERT INTO entitlements(id,plan_id,units,kind,reason) VALUES(?,?,?,?,?)',(c.uid('ent'),key,units,'opening','人工建立方案'))
        result=c.get(con,'plans',key)
    elif kind=='plan-adjust':
        plan=c.get(con,'plans',p['id']); required(p,'reason'); key=plan['id']
        delta=c.integer(p.get('units',0),'調整次數',-100000)
        if plan_balance(con,key)['available']+delta<0: raise ValueError('調整後不足已預約次數')
        end=p.get('valid_to') or plan['valid_to']; c.date(end)
        if end<plan['valid_from']: raise ValueError('到期日不符')
        if con.execute("SELECT 1 FROM orders WHERE plan_id=? AND status='confirmed' AND date>?",(key,end)).fetchone(): raise ValueError('已排程超過新到期日')
        con.execute('UPDATE plans SET valid_to=? WHERE id=?',(end,key))
        con.execute('INSERT INTO entitlements(id,plan_id,units,kind,reason) VALUES(?,?,?,?,?)',(c.uid('ent'),key,delta,'adjust',p['reason']))
        result={'id':key,**plan_balance(con,key)}
    elif kind=='receipt':
        old=replay(con,'receipts',p)
        if old:return old
        required(p,'customer_id','reference','date'); c.get(con,'customers',p['customer_id']); c.date(p['date'])
        key=c.uid('rec'); amount=c.integer(p['amount'],'收款金額',1)
        if p.get('kind') not in ('prepayment','payment'): raise ValueError('款項類型不符')
        con.execute('INSERT INTO receipts(id,customer_id,amount,kind,date,method,reference,request_key) VALUES(?,?,?,?,?,?,?,?)',
                    (key,p['customer_id'],amount,p['kind'],p['date'],p.get('method',''),p['reference'],p['request_key']))
        result=c.get(con,'receipts',key)
    elif kind=='receipt-verify':
        r=c.get(con,'receipts',p['id']); required(p,'reference'); key=r['id']
        con.execute("UPDATE receipts SET status='verified',reference=? WHERE id=?",(p['reference'],key))
        result=c.get(con,'receipts',key)
    elif kind=='allocate':
        old=replay(con,'payments',p)
        if old:return old
        r=c.get(con,'receipts',p['receipt_id']); inv=c.get(con,'invoices',p['invoice_id'])
        n=c.integer(p['amount'],'分攤金額',1); c.date(p['date'])
        if r['status']!='verified' or inv['status']!='issued' or r['customer_id']!=inv['customer_id']: raise ValueError('收款須已對帳，且帳單須核定並屬於相同客戶')
        if n>receipt_balance(con,r['id'])['available'] or n>c.invoice_totals(con,inv['id'])['balance']: raise ValueError('超過可分攤或待收金額')
        key=c.uid('pay')
        con.execute('INSERT INTO payments(id,invoice_id,receipt_id,amount,date,method,reference,request_key) VALUES(?,?,?,?,?,?,?,?)',
                    (key,inv['id'],r['id'],n,p['date'],r['method'],r['reference'],p['request_key']))
        result=c.get(con,'payments',key)
    elif kind=='credit':
        old=replay(con,'credits',p)
        if old:return old
        inv=c.get(con,'invoices',p['invoice_id']); required(p,'reason'); c.date(p['date'])
        n=c.integer(p['amount'],'減免金額',1)
        if inv['status']!='issued' or n>c.invoice_totals(con,inv['id'])['total']: raise ValueError('須為核定帳單且減免不可超過應收')
        key=c.uid('cr')
        con.execute('INSERT INTO credits VALUES(?,?,?,?,?,?)',(key,inv['id'],n,p['reason'],p['date'],p['request_key']))
        result=c.get(con,'credits',key)
    elif kind=='receipt-refund':
        old=replay(con,'receipt_refunds',p)
        if old:return old
        r=c.get(con,'receipts',p['receipt_id']); required(p,'reason','reference'); c.date(p['date'])
        n=c.integer(p['amount'],'退款金額',1)
        if r['status']!='verified' or n>receipt_balance(con,r['id'])['available']: raise ValueError('僅可退還已對帳且未分攤餘額')
        key=c.uid('ref')
        con.execute('INSERT INTO receipt_refunds VALUES(?,?,?,?,?,?,?)',(key,r['id'],n,p['date'],p['reference'],p['reason'],p['request_key']))
        result=c.get(con,'receipt_refunds',key)
    elif kind=='timesheet':
        a=c.get(con,'assignments',p['assignment_id']); o=c.get(con,'orders',a['order_id'])
        came=con.execute("SELECT 1 FROM checkins WHERE assignment_id=? AND arrived_at!=''",(a['id'],)).fetchone()
        if a['status']!='accepted' or not (o['status']=='confirmed' or (came and o['status'] in ('review','draft'))): raise ValueError('僅已排程且已接單人員可填實績')   # 已到場、中途退回待確認：工時是真的，照記
        required(p,'notes'); n=c.minutes(p['start'],p['end'],p.get('pause',0))
        old=con.execute('SELECT * FROM timesheets WHERE assignment_id=?',(a['id'],)).fetchone()
        if old and p.get('version')!=old['version']: raise ValueError('工時已更新，請重新整理')
        key=old['id'] if old else c.uid('ts')
        con.execute('INSERT INTO timesheets(id,assignment_id,start,end,pause,minutes,notes) VALUES(?,?,?,?,?,?,?) ON CONFLICT(assignment_id) DO UPDATE SET start=excluded.start,end=excluded.end,pause=excluded.pause,minutes=excluded.minutes,notes=excluded.notes,version=timesheets.version+1',
                    (key,a['id'],p['start'],p['end'],c.integer(p.get('pause',0),'休息'),n,p['notes']))
        result=c.get(con,'timesheets',key)
    elif kind=='acceptance':
        o=c.get(con,'orders',p['id']); required(p,'reason')
        if o['status']!='completed' or p.get('version')!=o['version'] or p.get('acceptance') not in ('accepted','pending','disputed'): raise ValueError('驗收資料或版本不符')
        key=o['id']; con.execute('UPDATE orders SET acceptance=?,acceptance_note=?,version=version+1 WHERE id=?',(p['acceptance'],str(p.get('reason') or '')[:200],key))
        con.execute('INSERT INTO service_reports(id,order_id,notes,customer_confirmed) VALUES(?,?,?,?)',(c.uid('rpt'),key,p['reason'],int(p['acceptance']=='accepted')))
        result=c.get(con,'orders',key)
    elif kind=='chat-link':
        old=c.get(con,'chat_threads',p['id']); key=old['id']
        if p.get('version')!=old['version']: raise ValueError('對話已更新')
        if p.get('classification') not in ('customer','recruit','vendor','test','review'): raise ValueError('分類不符')
        cid=p.get('customer_id') or None
        if cid:c.get(con,'customers',cid)
        con.execute('UPDATE chat_threads SET customer_id=?,classification=?,notes=?,version=version+1 WHERE id=?',(cid,p['classification'],p.get('notes',''),key))
        result=c.get(con,'chat_threads',key)
    else: raise ValueError('不支援操作')
    c.audit(con,kind,'workflow',key,after={**p,'result_id':result.get('id')})
    return result

def chat_search(con,p):
    q=p.get('q','')[:200]; thread=p.get('thread',''); offset=max(0,int(p.get('offset','0')))
    clause="WHERE (?='' OR m.thread_id=?) AND instr(lower(m.content),lower(?))>0"
    args=(thread,thread,q)
    total=con.execute('SELECT COUNT(*) FROM chat_messages m '+clause,args).fetchone()[0]
    return {'total':total,'offset':offset,'messages':c.rows(con,'SELECT m.*,t.source FROM chat_messages m JOIN chat_threads t ON t.id=m.thread_id '+clause+' ORDER BY local_time DESC,m.record DESC LIMIT 100 OFFSET ?',(*args,offset))}
