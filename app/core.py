"""Transactional operations. Money is integer TWD cents; hours use Decimal."""
import contextlib
import datetime as dt
import decimal
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('OPS_DATA', ROOT / 'data'))
SOURCE = ROOT.parent / '01_原始資料（勿改）'                      # 原始資料（Excel、LINE 匯出、派工對話）只讀
XLSX = SOURCE / '服務預約管理.xlsx'
CHATS = ROOT.parent / '示範服務店'                                      # 示範服務店官方 LINE 對話匯出（最新一次；跟 生活線群、企業線 同一層，搬出來）
TZ = dt.timezone(dt.timedelta(hours=8))
D = decimal.Decimal
CTX = threading.local()   # 每個請求的操作者，寫進 audit

def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')

def uid(prefix):
    return prefix + '-' + uuid.uuid4().hex[:12]

def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / 'operations.db', timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=20000')
    return con

def initialize():
    with contextlib.closing(connect()) as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        con.executescript((ROOT / 'schema_v2.sql').read_text(encoding='utf-8'))
        con.executescript((ROOT / 'schema_v3.sql').read_text(encoding='utf-8'))
        av=con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='availability'").fetchone()
        if av and "CHECK(kind IN ('free','busy'))" in av[0]:          # 可排時間加「不確定」→ 換掉舊的限制（資料原樣搬過去）
            new=av[0].replace("CHECK(kind IN ('free','busy'))","CHECK(kind IN ('free','busy','unsure'))").replace('CREATE TABLE availability','CREATE TABLE availability_new',1)
            cols=','.join(r[1] for r in con.execute('PRAGMA table_info(availability)'))
            con.executescript(f"BEGIN;{new};INSERT INTO availability_new({cols}) SELECT {cols} FROM availability;DROP TABLE availability;ALTER TABLE availability_new RENAME TO availability;COMMIT;")
        additions={
            'orders':{'plan_id':'TEXT REFERENCES plans(id)','parent_order_id':'TEXT REFERENCES orders(id)',
                      'acceptance':"TEXT DEFAULT 'pending'",'charge_approved':'INTEGER DEFAULT 0',
                      'approval_note':"TEXT DEFAULT ''",'customer_confirmed':'INTEGER DEFAULT 0'},
            'sites':{'navigation':"TEXT DEFAULT ''",'entry_notes':"TEXT DEFAULT ''",'checklist':"TEXT DEFAULT ''"},
            'payments':{'receipt_id':'TEXT REFERENCES receipts(id)'},
            'invoices':{'tax_note':"TEXT DEFAULT ''",'voucher':"TEXT DEFAULT ''"},
            'sessions':{'user_id':'TEXT','ip':"TEXT DEFAULT ''",'created':'INTEGER DEFAULT 0'},
            'customers':{'code':"TEXT DEFAULT ''",'channel':"TEXT DEFAULT ''"},
            'users':{'pw_set_at':'INTEGER DEFAULT 0'},
            'inquiries':{'line_code':"TEXT DEFAULT ''",'line_user_id':"TEXT DEFAULT ''",'account':"TEXT DEFAULT 'demoshop'",'kind':"TEXT DEFAULT ''"},
        }
        additions['orders'].update({'dispatch':"TEXT DEFAULT ''",'dispatch_note':"TEXT DEFAULT ''",'field_report':"TEXT DEFAULT ''",
                                    'field_reported_at':"TEXT DEFAULT ''",'reminded_at':"TEXT DEFAULT ''",'followed_up_at':"TEXT DEFAULT ''",
                                    'hist_income':'INTEGER','hist_expense':'INTEGER','hist_salary':'INTEGER'})
        additions['sites'].update({'visit_price':'INTEGER','area':"TEXT DEFAULT ''",'entry_mode':"TEXT DEFAULT ''",'entry_ok':"TEXT DEFAULT ''"})   # 開門方式（door.py）
        additions['sites'].update({'worker_rate':'TEXT'})
        additions['sites'].update({'worker_info':"TEXT DEFAULT ''"})      # 給夥伴的現場須知（鑰匙、備品櫃…；接單後才給）
        additions['sites'].update({'trash_mode':"TEXT DEFAULT ''",'signin_photo':'INTEGER DEFAULT 0'})      # 服務完垃圾怎麼處理（prepq）、要拍簽到表（客戶02）
        additions['sites'].update({'lat':'REAL','lng':'REAL','geo_level':"TEXT DEFAULT ''",'geo_addr':"TEXT DEFAULT ''",'geo_try':"TEXT DEFAULT ''"})      # 國土測繪精準定位（sitegeo.py）      # 這個案場的夥伴時薪（例：客戶02 200）；空白＝照夥伴本人時薪
        additions['orders'].update({'dispatch_stage':'INTEGER DEFAULT 0'})
        additions['orders'].update({'crew_ok':"TEXT DEFAULT ''"})
        additions['orders'].update({'go_partial':"TEXT DEFAULT ''"})      # 兩人單有人請假、補人中，已接的人照常出發
        additions['orders'].update({'extra':"TEXT DEFAULT ''"})     # 陪玩／管家額外欄位（JSON；店長營運手冊第六節）
        additions['orders'].update({'feedback_done':"TEXT DEFAULT ''"})
        additions['orders'].update({'acceptance_note':"TEXT DEFAULT ''"})    # 業主驗收的來源說明（LINE 確認、自動視為驗收）；請款核定依據另在 approval_note
        additions['orders'].update({'worker_note':"TEXT DEFAULT ''",'door_ack':"TEXT DEFAULT ''",'door_req':"TEXT DEFAULT ''",'door_open':"TEXT DEFAULT ''",'entry_mode':"TEXT DEFAULT ''"})   # 給夥伴的提醒（prep.py）、開門流程（door.py）
        additions.setdefault('assignments', {}).update({'reimburse':'INTEGER DEFAULT 0'})   # 夥伴代墊用品（supplies.py），併入薪資   # 客戶回饋（想改時間／要求加強／低分）處理完的時間       # 派員審核：管理群按「人員沒問題」的人與時間（leave.py）
        additions['customers'].update({'preferred_workers':"TEXT DEFAULT ''"})
        additions['customers'].update({'pay_method':"TEXT DEFAULT ''"})
        additions['customers'].update({'remit_last5':"TEXT DEFAULT ''"})      # 業主匯款末五碼記住，下次自動帶入
        additions.setdefault('settlements', {}).update({'fee':'INTEGER DEFAULT 0'})      # 非示範銀行跨行手續費（分）      # 每位客戶的結帳方式（空白＝預設先結；後結、訂金、月結；客戶不能自己選）   # 指定／優先夥伴（worker id，逗號分隔）
        # 報價審核：stage photos(等照片)／review(待店長確認)／sent(已傳客戶)／site_visit(現場估價)；items 為店長確認後的正式品項
        additions['quotes']={'link_sent':"TEXT DEFAULT ''",'stage':"TEXT DEFAULT 'review'",'items':"TEXT DEFAULT ''",'note':"TEXT DEFAULT ''",'taxed':'INTEGER DEFAULT 0',
                             'people':'INTEGER DEFAULT 1','hours':'REAL DEFAULT 0','scope':"TEXT DEFAULT ''",'photo_via':"TEXT DEFAULT 'upload'",
                             'mode':"TEXT DEFAULT 'quote'",'visit_fee':'INTEGER DEFAULT 0','reviewed_by':"TEXT DEFAULT ''",'sent_at':"TEXT DEFAULT ''",
                             'valid_until':"TEXT DEFAULT ''"}
        additions.setdefault('assignments', {}).update({'work_token':"TEXT DEFAULT ''"})   # 夥伴 LINE 工作頁連結碼
        additions['assignments'].update({'overlap_ok':'INTEGER DEFAULT 0'})             # 排班時間寫太長、實際錯開：管理層確認過就不再擋撞期
        additions['workers']={'portal_token':"TEXT DEFAULT ''",'roles':"TEXT DEFAULT ''",'av_note':"TEXT DEFAULT ''",'gender':"TEXT DEFAULT ''",'tools_own':"TEXT DEFAULT ''",'willing':"TEXT DEFAULT ''",'home':"TEXT DEFAULT ''",'home_lat':"REAL",'home_lng':"REAL",'home_geo':"TEXT DEFAULT ''",'home_try':"TEXT DEFAULT ''",'transport':"TEXT DEFAULT ''",'butler_skills':"TEXT DEFAULT ''",'play_skills':"TEXT DEFAULT ''",'bank_name':"TEXT DEFAULT ''",'bank_account':"TEXT DEFAULT ''",'bank_holder':"TEXT DEFAULT ''",'payout_cycle':"TEXT DEFAULT 'month'"}   # 夥伴主控板連結碼；職類（服務人員、陪玩員、家庭管家、整理師，頓號分隔；空白＝服務人員）
        additions['line_users']={'onboard':"TEXT DEFAULT ''",'nudged_at':"TEXT DEFAULT ''",'suggest_cid':"TEXT DEFAULT ''"}   # suggest_cid：系統猜的舊客戶，等本人確認   # 首次加入：asked／customer／applicant
        additions['orders'].update({'rating':'INTEGER','rating_at':"TEXT DEFAULT ''"})      # 客戶在 LINE 給的評價（1–5 星）
        additions['orders'].update({'scope':"TEXT DEFAULT ''",'dispatch_only':'INTEGER DEFAULT 0'})
        additions['orders'].update({'scope_hold':'INTEGER DEFAULT 0','scope_ok':"TEXT DEFAULT ''"})
        additions.setdefault('sites', {}).update({'tools':"TEXT DEFAULT ''",'tools_note':"TEXT DEFAULT ''",'no_photo':'INTEGER DEFAULT 0'})   # no_photo：場內不能拍照（例：客戶03）      # 現場工具（sitetools.py）      # 報價單來的詳細需求要管理層審核才給夥伴看（scopereview.py）   # scope：給夥伴看的服務範圍；dispatch_only：只有被點名的人看得到、接得到
        additions['line_outbox']={'attempts':'INTEGER DEFAULT 0','next_at':"TEXT DEFAULT ''"}      # 安靜時段延後發送也用 next_at（以前第一次發送才建）
        for table,columns in additions.items():
            existing={r[1] for r in con.execute(f'PRAGMA table_info({table})')}
            for column,definition in columns.items():
                if column not in existing: con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        import brands
        brands.init(con)
        con.execute("CREATE TABLE IF NOT EXISTS bind_requests(id TEXT PRIMARY KEY, created_at TEXT, account TEXT, user_id TEXT, kind TEXT, ref_id TEXT, "
                    "name TEXT, phone TEXT, line_name TEXT DEFAULT '', status TEXT DEFAULT 'open', decided_by TEXT DEFAULT '', decided_at TEXT DEFAULT '')")            # 品牌（業務線）設定表；第一次會把示範服務店／企業線／生活線搬進去
        con.commit()

def audit(con, action, table, key, before=None, after=None, actor=None):
    actor = actor or getattr(CTX, 'actor', None) or '系統'
    con.execute('INSERT INTO audit(actor,action,entity,entity_id,before_json,after_json) VALUES(?,?,?,?,?,?)',
                (actor, action, table, key, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))

def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]

def get(con, table, key):
    r = con.execute(f'SELECT * FROM {table} WHERE id=?', (key,)).fetchone()
    if not r:
        raise ValueError('找不到這筆資料')
    return dict(r)

def number(value, label, minimum=0):
    try:
        n = D(str(value))
        if not n.is_finite() or n < minimum:
            raise ValueError()
        return n
    except (decimal.InvalidOperation, ValueError):
        raise ValueError(f'{label}必須是有效數字（至少 {minimum}）')

def integer(value, label, minimum=0):
    n = number(value, label, minimum)
    if n != n.to_integral_value():
        raise ValueError(f'{label}必須是整數')
    return int(n)

def cents(value):
    return int((number(value, '金額') * 100).quantize(D('1'), rounding=decimal.ROUND_HALF_UP))

def date(value):
    return dt.date.fromisoformat(value)

def place(area):
    """地區顯示：已經有縣市（臺北市…、大安區、高雄市…）就原樣，沒有才補「臺北市」（稽核：以前會變成「臺北市新北市板橋區」）。"""
    import re
    a = (area or '').strip()
    return a if re.match(r'^.{2}[縣市]', a) else (f'臺北市{a}' if a else '臺北')

def minutes(start, end, pause=0):
    # Explicit same-day times: never convert reversed times into a 23-hour shift.
    a, b = dt.time.fromisoformat(start), dt.time.fromisoformat(end)
    n = (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute) - integer(pause, '休息分鐘')
    if n <= 0 or n > 16 * 60:
        raise ValueError('結束時間須晚於開始時間，工時須在 16 小時內；跨日請拆單')
    return n

FIELDS = {
 'customers': {'name','line_name','phone','status','notes','billing_rule','line_account','code','channel','preferred_workers','pay_method'},
 'sites': {'customer_id','name','address','service_type','notes','rate','pricing_note','navigation','entry_notes','checklist','visit_price','area','entry_mode','entry_ok','no_photo','worker_rate','trash_mode','signin_photo','worker_info'},
 'workers': {'name','phone','regions','skills','availability','rate','status','notes','roles','gender','tools_own','willing','home','transport','butler_skills','play_skills','bank_name','bank_account','bank_holder','payout_cycle'},
 'orders': {'site_id','customer_id','title','date','start','end','actual_start','actual_end','break_minutes','required_workers','billing_mode','rate','billable_hours','approved_amount','expense','adjustment','adjustment_reason','notes','review_note','plan_id','parent_order_id','customer_confirmed','dispatch_note','scope','extra','entry_mode'},
 'assignments': {'order_id','worker_id','status','pay_mode','rate','approved_minutes','amount','notes','overlap_ok'},
}
FIELDS['contacts']={'customer_id','name','role','phone','line_account','alias','notes'}
FIELDS['tasks']={'customer_id','title','detail','owner','due_date','status','resolution'}
FIELDS['service_cases']={'order_id','customer_id','title','detail','owner','due_date','status','resolution'}

def pay_rate(con, order_id, worker_id):
    """夥伴時薪：案場有設「夥伴時薪」就用案場的（客戶02 200），沒有就用夥伴本人的。"""
    r = con.execute('SELECT s.worker_rate FROM orders o JOIN sites s ON s.id=o.site_id WHERE o.id=?', (order_id or '',)).fetchone()
    if r and r[0] not in (None, ''):
        return r[0]
    w = con.execute('SELECT rate FROM workers WHERE id=?', (worker_id or '',)).fetchone()
    return w[0] if w else None

def save(con, table, payload):
    if table not in FIELDS:
        raise ValueError('不支援的資料類型')
    if table == 'assignments' and not payload.get('id') and payload.get('order_id') and payload.get('worker_id') \
            and payload.get('rate') in (None, '') and payload.get('amount') in (None, '') and payload.get('pay_mode', 'hourly') == 'hourly':
        payload = {**payload, 'rate': pay_rate(con, payload['order_id'], payload['worker_id'])}       # 新指派沒帶時薪 → 案場夥伴時薪／夥伴本人時薪
    if table == 'assignments' and not payload.get('id') and payload.get('order_id') and payload.get('worker_id'):
        old = con.execute('SELECT a.id,a.version,a.status,w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.worker_id=?',
                          (payload['order_id'], payload['worker_id'])).fetchone()
        if old and old['status'] in ('accepted', 'invited', 'applied'):
            raise ValueError(f"{old['name']} 已經在這筆工作了（{ {'accepted': '已接', 'invited': '邀請中', 'applied': '報名中'}[old['status']]}）")
        if old:      # 被拿掉（請假／婉拒／取消）的人要再排回 → 重新啟用舊紀錄（以前撞唯一限制，手動、自動都選不到）
            payload = {**payload, 'id': old['id'], 'version': old['version']}
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_leaves'").fetchone():
                pts = con.execute("UPDATE worker_leaves SET status='withdrawn',decided_by=?,decided_at=? WHERE order_id=? AND worker_id=? AND status='point'",
                                  (getattr(CTX, 'actor', '') or '管理層', now(), payload['order_id'], payload['worker_id'])).rowcount      # 稽核 M2：只算真的記點那筆
                con.execute("UPDATE worker_leaves SET status='withdrawn',decided_by=?,decided_at=? WHERE order_id=? AND worker_id=? AND status='pending'",
                            (getattr(CTX, 'actor', '') or '管理層', now(), payload['order_id'], payload['worker_id']))
                if pts:           # 排回就撤銷記點；這一點造成的自動停派，撤銷後不到 3 點就解除
                    import automation, leave
                    b_ = automation.blacklist(con).get(payload['worker_id'])
                    if b_ and b_.get('by') == '系統（請假記點）' and leave.history(con, payload['worker_id'])[1] < leave.POINT_LIMIT:
                        automation.set_blacklist(con, payload['worker_id'], False, '系統（排回撤銷記點）')
                        import line_bot as _lbx
                        _lbx.admin_notify(con, f"✅ {b_.get('name', '')} 已解除停派（排回撤銷記點）", [('原因', '請假被記點後又排回同一場：那一點撤銷，半年內不到 3 點')], f"/#w/{payload['worker_id']}", '#2E7D32')      # 稽核 優化
    key = payload.get('id') or uid(table[:3])
    before = get(con, table, key) if payload.get('id') else None
    if before and payload.get('version') != before['version']:
        raise ValueError('資料已被更新，請重新整理後再儲存')
    change = {k:v for k,v in payload.items() if k in FIELDS[table]}
    if not change:
        raise ValueError('沒有可儲存的欄位')
    combined = {**(before or {}), **change}
    if table in ('customers','sites','workers') and not str(combined.get('name','')).strip():
        raise ValueError('名稱必填')
    for k in ('rate','billable_hours','worker_rate'):
        if k in change:
            change[k] = None if change[k] in ('',None) else str(number(change[k], k))
    for k in ('approved_amount','expense','amount','approved_minutes','break_minutes','required_workers','visit_price'):
        if k in change:
            change[k] = None if change[k] in ('',None) and k in ('approved_amount','amount','approved_minutes','visit_price') else integer(change[k], k, 1 if k=='required_workers' else 0)
    if 'adjustment' in change:
        change['adjustment'] = integer(change['adjustment'], '調整金額', -100000000)
    combined.update(change)
    if table in ('tasks','service_cases'):
        if not all(str(combined.get(k,'')).strip() for k in ('title','owner','due_date')):
            raise ValueError('主旨、負責人、期限必填')
        date(combined['due_date'])
        if combined.get('status','open') not in ('open','resolved'): raise ValueError('無效待辦狀態')
        if combined.get('status')=='resolved' and not combined.get('resolution','').strip(): raise ValueError('結案須填處理結果')
    if table=='sites' and before and combined['customer_id']!=before['customer_id'] and con.execute('SELECT 1 FROM orders WHERE site_id=?',(key,)).fetchone():
        raise ValueError('已有工單的案場不可更換所屬客戶；請建立新案場保留歷史')
    if table=='workers':
        if combined.get('status','review') not in ('review','active','paused'): raise ValueError('無效人員狀態')
        if before and combined.get('status')!='active' and con.execute("SELECT 1 FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND a.status='accepted' AND o.status='confirmed'",(key,)).fetchone():
            raise ValueError('此人員仍有已排程工單，請先取消／替補派工')
    if table == 'orders':
        if before and before['status']=='archived':      # 歷史紀錄：只准補客戶／案場、名稱、備註（沒資料的要能補）；時間、金額、人員不動
            fixable={'site_id','customer_id','title','notes','review_note'}
            if any(k not in fixable and v!=before.get(k) for k,v in change.items()):
                raise ValueError('歷史紀錄只能補客戶／案場、服務名稱、備註；時間和金額不能改')
        if before and before['status']=='cancelled': raise ValueError('已取消工單鎖定；請另建關聯工單')
        if before and before['status']=='completed':
            commercial={'rate','billable_hours','approved_amount','expense','adjustment','adjustment_reason'}
            locked={'site_id','date','required_workers','customer_id','plan_id','parent_order_id','status','billing_mode'}      # 完工後也能改時間（延時）、備註；表單整包送，沒變的不算
            nz=lambda x: '' if x is None else str(x).strip()
            if any(k in locked and nz(v)!=nz(before.get(k)) for k,v in change.items()): raise ValueError('已完工的工單可以改時間、備註和還沒開帳的費用；日期、案場、人數要改請找負責人')
            change={k:v for k,v in change.items() if k not in locked}
            if any(k in commercial and nz(change[k])!=nz(before.get(k)) for k in change):
                change.update(charge_approved=0,approval_note='')
        if before and con.execute('SELECT 1 FROM invoice_lines WHERE order_id=?', (key,)).fetchone():
            raise ValueError('已入帳工單已鎖定，請以調整帳單處理差額')
        if not combined.get('title','').strip():
            raise ValueError('服務名稱必填')
        if combined.get('date'):
            date(combined['date'])
        for a,b in [('start','end'),('actual_start','actual_end')]:
            if combined.get(a) and combined.get(b):
                minutes(combined[a],combined[b],combined.get('break_minutes',0) if a=='actual_start' else 0)
        if combined.get('site_id'):
            site = get(con,'sites',combined['site_id'])
            change['customer_id'] = site['customer_id']
            combined['customer_id'] = site['customer_id']
        if 'customer_confirmed' in change:
            change['customer_confirmed']=integer(change['customer_confirmed'],'客戶確認')
            if change['customer_confirmed'] not in (0,1): raise ValueError('客戶確認值無效')
        if combined.get('parent_order_id'):
            parent=get(con,'orders',combined['parent_order_id'])
            if parent['customer_id']!=combined.get('customer_id') or parent['id']==key: raise ValueError('補掃原單必須屬同一客戶')
            if combined.get('plan_id'): raise ValueError('補掃不重複核銷方案；請清除方案連結')
        if combined.get('plan_id'):
            plan=get(con,'plans',combined['plan_id'])
            if plan['customer_id']!=combined.get('customer_id'): raise ValueError('方案與客戶不符')
        if combined.get('billing_mode','fixed') not in ('fixed','hourly','package'):
            raise ValueError('無效計費方式')
        if combined.get('adjustment',0) and not combined.get('adjustment_reason','').strip():
            raise ValueError('加減價請填寫原因')
        schedule_fields={'site_id','date','start','end','required_workers','plan_id','parent_order_id'}
        moved={k for k in schedule_fields if k in change and str(change[k] or '')!=str(before.get(k) or '')} if before else set()
        if before and before['status']=='confirmed' and moved:
            if moved=={'end'} and before['date']==now()[:10] and str(change['end'] or '')>str(before['end'] or ''):
                pass          # 稽核：服務當天現場延長時間 → 維持已排定（以前退回待確認，夥伴離場打卡、填工時都會失敗）
            elif moved=={'required_workers'} and integer(change['required_workers'] or 1,'人數')<integer(before['required_workers'] or 1,'人數'):
                pass          # 稽核：只是減人數（人還是夠）→ 維持已排定（以前退回待確認，沒人再按排定，夥伴前一天收到「先不要出發」）
            else:
                if not combined.get('review_note','').strip(): raise ValueError('改期請填寫變更原因')
                change.update(status='review',confirmed_at='')
                if moved-{'required_workers'}: change['customer_confirmed']=0      # 只改人數：時間沒變，業主的確認保留
        if before and 'required_workers' in moved and integer(change['required_workers'] or 1,'人數')>integer(before['required_workers'] or 1,'人數'):
            CTX.reset_crew=key           # 加人：新的人也要派單管理群審核
    if table == 'assignments':
        order = get(con,'orders',combined.get('order_id',''))
        if order['status']=='cancelled': raise ValueError('取消的工單不能派工')
        worker=get(con,'workers',combined.get('worker_id',''))
        if combined.get('status')=='accepted' and worker['status']!='active' and not in_trial(con,worker['id']): raise ValueError('人員身分未核對或已暫停，不能接受派工')
        if combined.get('status') in ('invited','accepted') and (not before or before['status']!=combined.get('status')):
            import automation
            if automation.is_blacklisted(con, worker['id']):          # 黑名單＝停派：後台直接排入、LINE 接單、邀請，全部擋
                raise ValueError(f"{worker['name']} 在黑名單（停派），不能派工；要排請先到夥伴頁移出黑名單")
        if order['status']=='completed' and (not before or combined.get('status')!=before['status']):
            raise ValueError('已執行工單不能增減人員，只可核定計酬')
        if combined.get('status','invited') not in ('applied','invited','accepted','declined','cancelled'):
            raise ValueError('無效派工狀態')
        if combined.get('pay_mode','hourly') not in ('hourly','fixed'):
            raise ValueError('無效計酬方式')
        if before and con.execute('SELECT 1 FROM settlement_lines WHERE assignment_id=?',(key,)).fetchone():
            raise ValueError('已結算派工已鎖定')
        if before and (combined['order_id']!=before['order_id'] or combined['worker_id']!=before['worker_id']):
            raise ValueError('請新增派工紀錄，不可變更既有紀錄的工單或人員')
    if before:
        change['version'] = before['version']+1
        con.execute(f'UPDATE {table} SET '+','.join(f'{k}=?' for k in change)+' WHERE id=?', (*change.values(),key))
    else:
        change['id'] = key
        con.execute(f'INSERT INTO {table}('+','.join(change)+') VALUES('+','.join('?' for _ in change)+')', tuple(change.values()))
    if table == 'assignments' and combined.get('status','invited')=='accepted':
        check_conflicts(con, combined['order_id'])
    if table=='assignments' and before and before['status']=='accepted' and combined.get('status')!='accepted':     # 資安：人員有異動 → 派員審核要重來
        con.execute("UPDATE orders SET crew_ok='' WHERE id=?", (order['id'],))
    if table=='assignments' and order['status']=='confirmed' and (not before or before['status']!=combined.get('status')):
        con.execute("UPDATE orders SET status='review',confirmed_at='',version=version+1 WHERE id=?",(order['id'],))     # 稽核：跟 LINE 請假換人同規則——時間沒變，業主確認保留
        import board; board.set_go_partial(con, order['id'], exclude=combined['worker_id'] if combined.get('status')=='accepted' else None)     # 原本的人照常出發
        audit(con,'assignment_requires_reconfirmation','orders',order['id'],order,get(con,'orders',order['id']))
    if table=='orders' and getattr(CTX,'reset_crew','')==key:
        CTX.reset_crew=''; con.execute("UPDATE orders SET crew_ok='' WHERE id=?",(key,))
    if table=='sites' and not before and not combined.get('entry_mode') and combined.get('customer_id'):
        import door                                   # 開門方式設定過不要一直問 → 新地點建立當下就沿用同業主其他地點的「在家開門」
        door.inherit(con, get(con,'sites',key), combined['customer_id'])
    after = get(con,table,key)
    audit(con,'update' if before else 'create',table,key,before,after)
    return after

def in_trial(con,wid):
    """招募「實作」關的應徵者：還不是可派工，但可以由管理層排一場試做（recruit.py）。"""
    try:
        return bool(con.execute("SELECT 1 FROM interviews WHERE worker_id=? AND status='trial'",(wid,)).fetchone())
    except sqlite3.OperationalError:
        return False

def check_conflicts(con, key):
    o=get(con,'orders',key)
    if not all(o.get(k) for k in ('date','start','end')):
        raise ValueError('請先填工單日期與開始、結束時間')
    start=dt.time.fromisoformat(o['start']); end=dt.time.fromisoformat(o['end'])
    lo=start.hour*60+start.minute; hi=end.hour*60+end.minute
    # 30 min is a visible initial operational setting, not an assumed historical fact.
    for other in rows(con,"SELECT o.*, w.name worker FROM assignments a JOIN assignments b ON a.worker_id=b.worker_id JOIN orders o ON o.id=b.order_id JOIN workers w ON w.id=b.worker_id WHERE a.order_id=? AND b.order_id!=? AND a.status='accepted' AND b.status='accepted' AND COALESCE(a.overlap_ok,0)=0 AND COALESCE(b.overlap_ok,0)=0 AND o.date=? AND o.status NOT IN ('cancelled','archived')",(key,key,o['date'])):
        if other['start'] and other['end']:
            a=dt.time.fromisoformat(other['start']); b=dt.time.fromisoformat(other['end'])
            if lo < b.hour*60+b.minute and hi > a.hour*60+a.minute:     # 只有時間真的重疊才擋；同一天跑多個案場、趕得過去就可以排
                raise ValueError(f"{other['worker']} 在 {other['start']}–{other['end']} 已經有其他工作，時間重疊")
            import decisions, board                  # 同天兩場間隔：店長說過這兩地這麼短趕不上才擋（其他的會問她，見 dispatch.gap_watch）
            area = lambda sid: board.area_of(get(con,'sites',sid)) if sid else ''
            gap = (lo-(b.hour*60+b.minute)) if hi > b.hour*60+b.minute else (a.hour*60+a.minute-hi)
            if decisions.gap_verdict(con, area(o['site_id']), area(other['site_id']), gap) == 'no':
                raise ValueError(f"{other['worker']} 同天 {other['start']}–{other['end']} 有工作，只隔 {gap} 分（店長說過這樣趕不上），時間太近")

def charge(o):
    if o['billing_mode']=='package':
        raise ValueError('包月／訂閱請由帳單加入核定方案金額，避免重複按次請款')
    if o['approved_amount'] is not None:
        base=o['approved_amount']
    elif o['billing_mode']=='hourly' and o['rate'] is not None and o['billable_hours'] is not None:
        base=cents(number(o['rate'],'時薪')*number(o['billable_hours'],'核定計費時數'))
    else:
        raise ValueError('請核定本次費用，或填寫時薪與核定計費時數')
    total=base+o['expense']+o['adjustment']
    if total<0:
        raise ValueError('工單應收不可為負；退款請從收款紀錄辦理')
    return total

def transition(con,key,action,payload):
    o=get(con,'orders',key)
    if payload.get('version')!=o['version']:
        raise ValueError('資料已更新，請重新整理')
    if con.execute('SELECT 1 FROM invoice_lines WHERE order_id=?',(key,)).fetchone():
        raise ValueError('已入帳工單不可變更狀態')
    if action=='confirm':
        if o['status'] not in ('review','draft'):
            raise ValueError('只有待核對或草稿可以確認排程')
        if not o['site_id'] or not o['date'] or not o['review_note'].strip():
            raise ValueError('請指定案場、日期，並填寫核對說明')
        if not o['customer_confirmed']: raise ValueError('尚未記錄客戶已確認本次日期與條件')
        if o['plan_id']:
            import workflow
            workflow.validate_plan_order(con,o)
        date(o['date']); minutes(o['start'],o['end'])
        site=get(con,'sites',o['site_id'])
        if not site['address'].strip():
            import re as _re
            nm=(site.get('name') or '').strip()
            if _re.search(r'[路街道巷段].{0,12}\d+\s*號',nm):          # 地址只寫在案場名稱裡 → 直接當地址用
                con.execute('UPDATE sites SET address=?,version=version+1 WHERE id=?',(nm,site['id']))
            else:
                raise ValueError('請先補齊案場地址（客戶 › 服務地點「編輯」→ 完整地址）')
        if con.execute("SELECT COUNT(*) FROM assignments WHERE order_id=? AND status='accepted'",(key,)).fetchone()[0]<o['required_workers']:
            raise ValueError('接單人數不足，請先完成派工確認')
        check_conflicts(con,key)
        changes={'status':'confirmed','confirmed_at':now()}
        import payments
        if not payments.cleared_for_schedule(con,key):      # 稽核：開工前的款（訂金／全額）沒收到，中控台手動排定也要擋（以前只有 LINE 自動排定會擋）
            why=str(payload.get('unpaid_reason','')).strip()
            if not why: raise ValueError('開工前要收的款（訂金／全額）還沒確認收到，確認收款後系統會自動排定。真的要先排定，請填「先排定的原因」')
            changes['review_note']=(o['review_note']+f'；款項未收先排定：{why}')[:2000]
    elif action=='complete':
        if not (o['status']=='confirmed' or (o['status'] in ('review','draft') and (o.get('go_partial') or ''))):      # 補人中、留下的人照常做完 → 也能完工
            raise ValueError('請先人工確認排程')
        minutes(o['actual_start'],o['actual_end'],o['break_minutes'])
        if not str(payload.get('report','')).strip(): raise ValueError('請填現場服務回報')
        assigned=rows(con,"SELECT * FROM assignments WHERE order_id=? AND status='accepted'",(key,))
        for a in assigned:
            if not con.execute('SELECT 1 FROM timesheets WHERE assignment_id=?',(a['id'],)).fetchone():
                if len(assigned)>1: raise ValueError('多人服務請先逐人記錄實際工時')
                con.execute('INSERT INTO timesheets(id,assignment_id,start,end,pause,minutes,notes) VALUES(?,?,?,?,?,?,?)',
                            (uid('ts'),a['id'],o['actual_start'],o['actual_end'],o['break_minutes'],minutes(o['actual_start'],o['actual_end'],o['break_minutes']),'單人服務依工單實績記錄'))
        acceptance=payload.get('acceptance') or ('accepted' if payload.get('customer_confirmed') else 'pending')
        if acceptance not in ('accepted','pending','disputed'): raise ValueError('驗收狀態無效')
        con.execute('INSERT INTO service_reports(id,order_id,notes,customer_confirmed) VALUES(?,?,?,?)',(uid('rpt'),key,payload['report'],int(acceptance=='accepted')))
        if o['plan_id']:
            import workflow
            workflow.consume_plan(con,o)
        changes={'status':'completed','completed_at':now(),'acceptance':acceptance}
    elif action=='approve-charge':
        if o['status']!='completed' or not payload.get('reason','').strip(): raise ValueError('已執行服務才可核定請款，並須填核定依據')
        if o['billing_mode']!='package': charge(o)
        changes={'charge_approved':1,'approval_note':payload['reason']}
    elif action=='cancel':
        if o['status']=='completed' or not payload.get('reason','').strip():
            raise ValueError('已完工不可取消；取消須填原因')
        changes={'status':'cancelled','review_note':payload['reason']}
        con.execute("UPDATE assignments SET notes=notes||'（工單取消時已接）' WHERE order_id=? AND status='accepted'",(key,))   # 取消通知只發給當時有接的人
        con.execute("UPDATE assignments SET status='cancelled',version=version+1 WHERE order_id=?",(key,))
    else:
        raise ValueError('無效操作')
    changes['version']=o['version']+1
    con.execute('UPDATE orders SET '+','.join(f'{k}=?' for k in changes)+' WHERE id=?',(*changes.values(),key))
    audit(con,action,'orders',key,o,get(con,'orders',key))
    return get(con,'orders',key)

def create_invoice(con,p):
    get(con,'customers',p['customer_id']); date(p['due_date'])
    period=p['period']; date(period+'-01')
    key=uid('inv'); lines=[]
    for oid in dict.fromkeys(p.get('order_ids',[])):
        o=get(con,'orders',oid)
        if o['customer_id']!=p['customer_id'] or o['status']!='completed':
            raise ValueError('帳單只能包含同一客戶、已驗收的工單')
        if not o['charge_approved']: raise ValueError('工單尚未人工核定請款')
        lines.append((uid('ln'),key,oid,f"{o['date']} {o['title']}",charge(o)))
    for line in p.get('manual_lines',[]):
        if not line.get('description','').strip():
            raise ValueError('方案或調整項目說明必填')
        lines.append((uid('ln'),key,None,line['description'],integer(line['amount'],'金額')))
    if not lines:
        raise ValueError('帳單至少需要一個工單或方案項目')
    con.execute('INSERT INTO invoices(id,customer_id,period,due_date,notes) VALUES(?,?,?,?,?)',(key,p['customer_id'],period,p['due_date'],p.get('notes','')))
    con.executemany('INSERT INTO invoice_lines(id,invoice_id,order_id,description,amount) VALUES(?,?,?,?,?)',lines)
    audit(con,'create','invoices',key,after={'lines':lines})
    return get(con,'invoices',key)

def invoice_totals(con,key):
    total=con.execute('SELECT COALESCE(SUM(amount),0) FROM invoice_lines WHERE invoice_id=?',(key,)).fetchone()[0]
    credit=con.execute('SELECT COALESCE(SUM(amount),0) FROM credits WHERE invoice_id=?',(key,)).fetchone()[0]
    paid=con.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?',(key,)).fetchone()[0]
    return {'gross':total,'credits':credit,'total':total-credit,'paid':paid,'balance':max(0,total-credit-paid),'refundable':max(0,paid-total+credit)}

def invoice_action(con,key,action,p):
    inv=get(con,'invoices',key)
    if action=='issue':
        if inv['status']!='draft':
            raise ValueError('帳單已核定')
        con.execute("UPDATE invoices SET status='issued',issued_at=?,version=version+1 WHERE id=?",(now(),key))
    elif action=='payment':
        if not p.get('reference','').strip(): raise ValueError('收退款請填銀行／現金核對憑證')
        if inv['status']!='issued':
            raise ValueError('請先核定帳單')
        req=p.get('request_key','')
        if not req or len(req)>100:
            raise ValueError('收款請求缺少唯一識別')
        old=con.execute('SELECT * FROM payments WHERE request_key=?',(req,)).fetchone()
        if old:
            if old['invoice_id']!=key or old['amount']!=p.get('amount'):
                raise ValueError('同一識別碼不能用於不同收款')
            return dict(old)
        n=integer(p['amount'],'金額',-100000000)
        if not n:
            raise ValueError('金額不能為零')
        totals=invoice_totals(con,key)
        if n>totals['balance'] or -n>totals['refundable']:
            raise ValueError('收款不能超過未收；退款須先建立減免且不能超過溢收。預收請使用收款池')
        if n<0 and not p.get('reason','').strip():
            raise ValueError('退款請填原因')
        date(p['date'])
        con.execute('INSERT INTO payments(id,invoice_id,amount,date,method,reference,reason,request_key) VALUES(?,?,?,?,?,?,?,?)',
                    (uid('pay'),key,n,p['date'],p.get('method',''),p.get('reference',''),p.get('reason',''),req))
    else:
        raise ValueError('無效帳單操作')
    audit(con,action,'invoices',key,inv,invoice_totals(con,key))
    return {**get(con,'invoices',key),**invoice_totals(con,key)}

def payout_fee(con, w):
    """夥伴帳戶不是示範銀行 → 跨行手續費 15 元（分）；payout_fee_bearer=company 時公司吸收（0）。"""
    import integrations as _ix
    w = dict(w or {})
    if _ix.setting(con, 'payout_fee_bearer') == 'company' or not (w.get('bank_account') or '').strip() or '示範銀行' in (w.get('bank_name') or '') or (w.get('bank_name') or '').strip().startswith('008'):
        return 0
    return int(_ix.setting(con, 'payout_fee') or 15) * 100

def create_settlement(con,p):
    w_=get(con,'workers',p['worker_id'])
    if str(p['period']).startswith('W'):         # 週領 → W+週一（例 W2026-10-12 ＝ 該週週一到週日）
        d0=date(p['period'][1:]).isoformat(); d1=(dt.date.fromisoformat(d0)+dt.timedelta(days=6)).isoformat()
        rng=("o.date BETWEEN ? AND ?",(d0,d1))
    else:
        date(p['period']+'-01'); rng=("o.date LIKE ?",(p['period']+'%',))
    candidates=rows(con,"SELECT a.* FROM assignments a JOIN orders o ON o.id=a.order_id LEFT JOIN settlement_lines l ON l.assignment_id=a.id WHERE a.worker_id=? AND ((a.status='accepted' AND o.status='completed') OR (o.status='cancelled' AND a.amount>0)) AND "+rng[0]+" AND l.id IS NULL",(p['worker_id'],)+rng[1])
    lines=[]; key=uid('set')
    import board
    held=0
    for a in candidates:
        if board.photo_hold(con,{**a,'ostatus':con.execute('SELECT status FROM orders WHERE id=?',(a['order_id'],)).fetchone()[0],'date':con.execute('SELECT date FROM orders WHERE id=?',(a['order_id'],)).fetchone()[0]}):
            held+=1; continue                   # 負責人 前後照片有拍才發放薪資
        amount=board.worker_pay(con,a)          # 核定金額 → 核定分鐘 → 打卡 → 預排，再加代墊用品（跟中控台月結、夥伴我的收入同一套算法）
        if amount is None:
            raise ValueError('尚有派工沒有時薪，請先填核定薪資或夥伴時薪')
        lines.append((uid('sl'),key,a['id'],amount))
    if not lines:
        raise ValueError('沒有符合條件的未結算派工' + (f'（{held} 場缺服務前／完成照片，暫不發放）' if held else ''))
    con.execute('INSERT INTO settlements(id,worker_id,period,fee) VALUES(?,?,?,?)',(key,p['worker_id'],p['period'],payout_fee(con,w_)))
    con.executemany('INSERT INTO settlement_lines(id,settlement_id,assignment_id,amount) VALUES(?,?,?,?)',lines)
    audit(con,'create','settlements',key,after={'count':len(lines)})
    return get(con,'settlements',key)

def snapshot(con):
    import workflow
    result={t:rows(con,f'SELECT * FROM {t}') for t in ('customers','sites','workers','orders','assignments','issues','service_reports','import_runs','line_accounts','conversation_notes')}
    result['invoices']=[{**i,**invoice_totals(con,i['id'])} for i in rows(con,'SELECT * FROM invoices ORDER BY created_at DESC')]
    result['payments']=rows(con,'SELECT * FROM payments ORDER BY date DESC')
    result['invoice_lines']=rows(con,'SELECT * FROM invoice_lines')
    result['settlements']=rows(con,'SELECT s.*,COALESCE(SUM(l.amount),0) total FROM settlements s LEFT JOIN settlement_lines l ON l.settlement_id=s.id GROUP BY s.id')
    result['audit']=rows(con,'SELECT id,at,actor,action,entity,entity_id FROM audit ORDER BY id DESC LIMIT 100')
    result['today']=dt.datetime.now(TZ).date().isoformat()
    for table in ('contacts','entitlements','timesheets','service_cases','tasks','chat_threads','credits','receipt_refunds','attachments','customer_aliases','checkins','availability','inquiries'):
        result[table]=rows(con,f'SELECT * FROM {table}' + (" WHERE label!='deleted'" if table=='attachments' else ''))      # 刪除的照片不顯示
    result['plans']=[{**p,**workflow.plan_balance(con,p['id'])} for p in rows(con,'SELECT * FROM plans')]
    result['receipts']=[{**r,**workflow.receipt_balance(con,r['id'])} for r in rows(con,'SELECT * FROM receipts')]
    return result
