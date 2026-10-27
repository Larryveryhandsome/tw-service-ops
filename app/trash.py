"""待整理區與回收桶。
- 待整理（triage）：系統或同事判斷不了的客戶、夥伴、工單、預約需求放這裡，負責人點選：保留／移到某分類／合併／刪除。
- 刪除＝移到回收桶：整筆連同關聯資料（地點、別名、歷史工單、派工紀錄…）存成快照後才從系統移除，可原樣還原。
- 永久刪除只能在回收桶做（管理員）。有帳款、進行中工單、App 帳號的資料不能刪，系統會說明要先處理什麼。"""
import json
import core as c
import board

LABEL = {'customers': '客戶', 'workers': '夥伴', 'orders': '工單', 'inquiries': '預約需求', 'sites': '服務地點'}
MOVES = {   # 「移到」可選的位置
    'customers': [('status:active', '服務中客戶'), ('status:paused', '暫停'), ('status:lead', '潛在客戶'), ('status:ended', '歷史客戶'), ('channel:關係企業', '關係企業（不對外收款）')],
    'workers': [('status:active', '可派工'), ('status:review', '待核對'), ('status:paused', '暫停／不再合作')],
    'orders': [('archive', '歷史（只留紀錄）'), ('cancel', '取消')],
    'inquiries': [('status:lost', '未成交'), ('status:spam', '無效'), ('status:contacted', '已聯絡')],
}

def label_of(con, entity, ref):
    r = con.execute(f'SELECT * FROM {entity} WHERE id=?', (ref,)).fetchone()
    if not r:
        return '（已不存在）'
    r = dict(r)
    if entity == 'orders':
        return f"{r['date']} {r['start']} {r['title']}"
    return r.get('name') or ref

def flag(con, entity, ref, grp, reason, suggestion=''):
    if entity not in LABEL:
        raise ValueError('不支援的資料類型')
    c.get(con, entity, ref)
    con.execute('INSERT OR IGNORE INTO triage(id,entity,ref_id,grp,reason,suggestion,created_by) VALUES(?,?,?,?,?,?,?)',
                (c.uid('tri'), entity, ref, grp, reason, suggestion, getattr(c.CTX, 'actor', '系統')))

def listing(con):
    out = []
    for t in c.rows(con, "SELECT * FROM triage WHERE status='open' ORDER BY grp, created_at"):
        r = con.execute(f"SELECT * FROM {t['entity']} WHERE id=?", (t['ref_id'],)).fetchone()
        if not r:
            con.execute("UPDATE triage SET status='done',decision='資料已不存在' WHERE id=?", (t['id'],)); continue
        r = dict(r); info = []
        if t['entity'] == 'customers':
            n = con.execute('SELECT COUNT(*),MIN(date),MAX(date) FROM orders WHERE customer_id=?', (r['id'],)).fetchone()
            live = con.execute("SELECT COUNT(*) FROM orders WHERE customer_id=? AND status NOT IN ('archived','cancelled')", (r['id'],)).fetchone()[0]
            info = [f"服務紀錄 {n[0]} 筆" + (f"（{n[1]}～{n[2]}）" if n[0] else ''), f'進行中 {live}' if live else '', r['phone'], r['channel']]
        elif t['entity'] == 'workers':
            n = con.execute("SELECT COUNT(*),MAX(o.date) FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=?", (r['id'],)).fetchone()
            info = [f"排班 {n[0]} 次" + (f"，最近 {n[1]}" if n[1] else ''), r['phone'] or '沒有電話', r['regions']]
        elif t['entity'] == 'orders':
            ws = [x[0] for x in con.execute("SELECT w.name FROM assignments a JOIN workers w ON w.id=a.worker_id WHERE a.order_id=? AND a.status IN ('accepted','invited')", (r['id'],))]
            info = [f"{r['start']}–{r['end']}", '、'.join(ws), r['status']]
        else:
            info = [r['phone'], r['service_type'], r['status']]
        out.append({**t, 'label': label_of(con, t['entity'], t['ref_id']), 'info': ' · '.join(x for x in info if x), 'moves': MOVES[t['entity']]})
    return out

# ───────────── 回收桶：快照、刪除、還原 ─────────────
def _rows(con, table, where, args):
    return [dict(r) for r in con.execute(f'SELECT * FROM {table} WHERE {where}', args)]

def _key(table):
    return 'rowid' if table in ('line_users', 'line_groups') else 'alias' if table == 'customer_aliases' else 'id'      # 這幾張表沒有 id 欄

def _snapshot(con, entity, ref):
    """回傳 (快照 {表: [列]}, 刪除順序, 要清成 NULL 的參照 [(表, 欄, id)])。檢查不能刪的情況。"""
    snap, nulls = {}, []
    if entity == 'inquiries':
        snap['inquiries'] = _rows(con, 'inquiries', 'id=?', (ref,))
        nulls += [('quotes', 'inquiry_id', x[0]) for x in con.execute('SELECT id FROM quotes WHERE inquiry_id=?', (ref,))]
        return snap, ['inquiries'], nulls
    if entity == 'orders':
        o = c.get(con, 'orders', ref)
        if con.execute('SELECT 1 FROM invoice_lines WHERE order_id=?', (ref,)).fetchone():
            raise ValueError('這張工單已經開帳，不能刪除；請用帳單減免處理')
        if o['status'] in ('confirmed', 'completed'):
            raise ValueError('已排定或已完工的工單不能直接刪，請先「取消」')
        for t in ('attachments', 'entitlements', 'service_cases'):
            if con.execute(f'SELECT 1 FROM {t} WHERE order_id=?', (ref,)).fetchone():
                raise ValueError('這張工單有照片、方案次數或客訴紀錄，不能刪除；可移到「歷史」')
        if con.execute('SELECT 1 FROM settlement_lines l JOIN assignments a ON a.id=l.assignment_id WHERE a.order_id=?', (ref,)).fetchone():
            raise ValueError('這張工單的夥伴薪資已結算，不能刪除')
        ass = _rows(con, 'assignments', 'order_id=?', (ref,)); ids = [a['id'] for a in ass] or ['']
        q = ','.join('?' * len(ids))
        snap = {'orders': [o], 'assignments': ass, 'timesheets': _rows(con, 'timesheets', f'assignment_id IN ({q})', ids),
                'checkins': _rows(con, 'checkins', f'assignment_id IN ({q})', ids), 'dispatch_log': _rows(con, 'dispatch_log', 'order_id=?', (ref,)),
                'service_reports': _rows(con, 'service_reports', 'order_id=?', (ref,)), 'calendar_operations': _rows(con, 'calendar_operations', 'order_id=?', (ref,))}
        for t, col in (('orders', 'parent_order_id'), ('inquiries', 'order_id'), ('quotes', 'order_id')):
            nulls += [(t, col, x[0]) for x in con.execute(f'SELECT id FROM {t} WHERE {col}=?', (ref,))]
        return snap, ['checkins', 'timesheets', 'assignments', 'dispatch_log', 'service_reports', 'calendar_operations', 'orders'], nulls
    if entity == 'customers':
        cu = c.get(con, 'customers', ref)
        for t, msg in (('invoices', '有帳單'), ('receipts', '有收款紀錄'), ('plans', '有方案')):
            if con.execute(f'SELECT 1 FROM {t} WHERE customer_id=?', (ref,)).fetchone():
                raise ValueError(f'這位客戶{msg}，不能刪除；可以「移到歷史客戶」或合併')
        if con.execute("SELECT 1 FROM orders WHERE customer_id=? AND status NOT IN ('archived','cancelled')", (ref,)).fetchone():
            raise ValueError('這位客戶還有待核對／已排定／已完工的工單，請先處理工單，或改用「合併」')
        snap = {'customers': [cu], 'sites': _rows(con, 'sites', 'customer_id=?', (ref,)), 'customer_aliases': _rows(con, 'customer_aliases', 'customer_id=?', (ref,)),
                'contacts': _rows(con, 'contacts', 'customer_id=?', (ref,)), 'tasks': _rows(con, 'tasks', 'customer_id=?', (ref,))}
        order_ids = [x[0] for x in con.execute('SELECT id FROM orders WHERE customer_id=?', (ref,))]
        subs = [_snapshot(con, 'orders', oid) for oid in order_ids]
        for s, _, n in subs:
            for t, rows in s.items():
                snap.setdefault(t, []).extend(rows)
            nulls += n
        for t in ('chat_threads', 'inquiries', 'conversation_notes', 'service_cases', 'quotes'):
            nulls += [(t, 'customer_id', x[0]) for x in con.execute(f'SELECT id FROM {t} WHERE customer_id=?', (ref,))]
        nulls += [('line_users', 'customer_id', x[0]) for x in con.execute('SELECT rowid FROM line_users WHERE customer_id=?', (ref,))]   # LINE 綁定先解除，還原時補回
        order = ['checkins', 'timesheets', 'assignments', 'dispatch_log', 'service_reports', 'calendar_operations', 'orders', 'customer_aliases', 'contacts', 'tasks', 'sites', 'customers']
        return snap, order, nulls
    if entity == 'sites':                    # 業主自己新增的地點（重複、打錯）
        s = c.get(con, 'sites', ref)
        n = con.execute('SELECT COUNT(*) FROM orders WHERE site_id=?', (ref,)).fetchone()[0]
        if n:
            raise ValueError(f'這個地點有 {n} 張工單，不能直接刪；請用「合併」把工單移到正確的地點')
        nulls += [('customer_aliases', 'site_id', x[0]) for x in con.execute('SELECT alias FROM customer_aliases WHERE site_id=?', (ref,))]
        return {'sites': [s]}, ['sites'], nulls
    if entity == 'workers':
        w = c.get(con, 'workers', ref)
        if con.execute('SELECT 1 FROM users WHERE worker_id=?', (ref,)).fetchone():
            raise ValueError('這位夥伴有 App 帳號，請先到設定停用帳號，或改用「暫停」')
        if con.execute('SELECT 1 FROM settlements WHERE worker_id=?', (ref,)).fetchone():
            raise ValueError('這位夥伴有薪資結算紀錄，不能刪除；請改用「暫停」')
        if con.execute("SELECT 1 FROM assignments a JOIN orders o ON o.id=a.order_id WHERE a.worker_id=? AND o.status NOT IN ('archived','cancelled')", (ref,)).fetchone():
            raise ValueError('這位夥伴還有進行中的派工，請先換人，或改用「合併」')
        ass = _rows(con, 'assignments', 'worker_id=?', (ref,)); ids = [a['id'] for a in ass] or ['']
        q = ','.join('?' * len(ids))
        snap = {'workers': [w], 'assignments': ass, 'timesheets': _rows(con, 'timesheets', f'assignment_id IN ({q})', ids),
                'checkins': _rows(con, 'checkins', f'assignment_id IN ({q})', ids), 'availability': _rows(con, 'availability', 'worker_id=?', (ref,))}
        return snap, ['checkins', 'timesheets', 'assignments', 'availability', 'workers'], nulls
    raise ValueError('不支援的資料類型')

def to_trash(con, entity, ref, reason=''):
    label = f"{LABEL[entity]}：{label_of(con, entity, ref)}"
    if entity == 'orders':                  # 每日檢查：刪工單也要把沒付的款項作廢（以前客戶照收付款提醒）
        o0 = c.get(con, 'orders', ref)
        import payments; payments.on_order_change(con, o0, {**o0, 'status': 'cancelled'}, getattr(c.CTX, 'actor', '回收桶'))
    snap, order, nulls = _snapshot(con, entity, ref)
    if entity == 'workers':                 # 解除 LINE 綁定（不然刪不掉，也會留著夥伴選單）
        con.execute('UPDATE line_users SET worker_id=NULL WHERE worker_id=?', (ref,))
        con.execute('UPDATE line_groups SET worker_id=NULL WHERE worker_id=?', (ref,))
    for t, col, rid in nulls:
        con.execute(f'UPDATE {t} SET {col}=NULL WHERE {_key(t)}=?', (rid,))
    for t in order:
        for row in snap.get(t, []):
            key = 'assignment_id' if t in ('checkins',) else ('order_id' if t == 'calendar_operations' else ('alias' if t == 'customer_aliases' else 'id'))
            con.execute(f'DELETE FROM {t} WHERE {key}=?', (row[key],))
    key = c.uid('trash')
    con.execute('INSERT INTO trash(id,entity,ref_id,label,payload,deleted_by,reason) VALUES(?,?,?,?,?,?,?)',
                (key, entity, ref, label, json.dumps({'snap': snap, 'order': order, 'nulls': nulls}, ensure_ascii=False, default=str),
                 getattr(c.CTX, 'actor', '系統'), reason))
    con.execute("UPDATE triage SET status='done',decided_at=?,decided_by=?,decision='刪除（回收桶）' WHERE entity=? AND ref_id=? AND status='open'",
                (c.now(), getattr(c.CTX, 'actor', ''), entity, ref))
    c.audit(con, 'trash', entity, ref, after={'trash_id': key, 'rows': {t: len(v) for t, v in snap.items()}})
    return {'trash_id': key, 'label': label}

def restore(con, trash_id):
    t = c.get(con, 'trash', trash_id); data = json.loads(t['payload'])
    for table in reversed(data['order']):
        for row in data['snap'].get(table, []):
            cols = [k for k in row]
            try:
                con.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [row[k] for k in cols])
            except c.sqlite3.IntegrityError:
                raise ValueError(f'無法還原：{table} 已有相同資料（可能已重新建立），請先處理衝突')
    for table, col, rid in data['nulls']:
        con.execute(f'UPDATE {table} SET {col}=? WHERE {_key(table)}=? AND {col} IS NULL', (t['ref_id'], rid))
    con.execute('DELETE FROM trash WHERE id=?', (trash_id,))
    c.audit(con, 'restore', t['entity'], t['ref_id'], after={'trash_id': trash_id})
    return {'restored': t['label']}

def purge(con, trash_id):
    t = c.get(con, 'trash', trash_id)
    con.execute('DELETE FROM trash WHERE id=?', (trash_id,))
    c.audit(con, 'purge', t['entity'], t['ref_id'], before={'label': t['label']})
    return {'purged': t['label']}

def trash_list(con):
    return [{k: v for k, v in r.items() if k != 'payload'} | {'rows': sum(len(v) for v in json.loads(r['payload'])['snap'].values())}
            for r in c.rows(con, 'SELECT * FROM trash ORDER BY deleted_at DESC')]

# ───────────── 處理待整理 ─────────────
def decide(con, p):
    ids = p.get('ids') or [p['id']]
    action = p['action']; done, errors = 0, []
    for tid in ids[:300]:
        con.execute('SAVEPOINT t')
        try:
            t = c.get(con, 'triage', tid)
            if action == 'reopen':             # 處理紀錄 → 放回待決定（按錯「保留」、想再處理）
                if t['status'] == 'open':
                    raise ValueError('已經在待決定裡')
                con.execute("UPDATE triage SET status='open',decided_at='',decided_by='',decision='' WHERE id=?", (tid,))
                c.audit(con, 'triage_reopen', t['entity'], t['ref_id'], before={'decision': t['decision']})
                con.execute('RELEASE t'); done += 1; continue
            if t['status'] != 'open':
                raise ValueError('已處理過')
            ent, ref = t['entity'], t['ref_id']
            if action == 'keep':
                decision = '保留不動'
            elif action == 'fixed':            # 在待整理按「修改」補好資料
                decision = '補好資料後保留' + (f"（{str(p.get('note'))[:40]}）" if p.get('note') else '')
            elif action == 'move':
                target = p['to']
                if target not in [m[0] for m in MOVES[ent]]:
                    raise ValueError('移動位置不符')
                name = dict(MOVES[ent])[target]
                if ent == 'orders':
                    o = c.get(con, 'orders', ref)
                    if target == 'archive':
                        if o['status'] not in ('review', 'draft', 'cancelled') or con.execute('SELECT 1 FROM invoice_lines WHERE order_id=?', (ref,)).fetchone():
                            raise ValueError('只有待核對或已取消的工單可以移到歷史')
                        con.execute("UPDATE orders SET status='archived',version=version+1 WHERE id=?", (ref,))
                        import payments; payments.on_order_change(con, o, c.get(con, 'orders', ref), getattr(c.CTX, 'actor', '待整理區'))
                    else:
                        board.after_transition(con, o, c.transition(con, ref, 'cancel', {'version': o['version'], 'reason': p.get('note') or '待整理區決定取消'}),
                                               'cancel', getattr(c.CTX, 'actor', '待整理區'))      # 跟單筆取消一樣通知客戶、夥伴
                else:
                    col, val = target.split(':', 1)
                    was = con.execute(f'SELECT status FROM {ent} WHERE id=?', (ref,)).fetchone() if col == 'status' else None
                    con.execute(f'UPDATE {ent} SET {col}=?,version=version+1 WHERE id=?', (val, ref))
                    if ent == 'workers' and col == 'status' and val != 'active' and was and was[0] == 'active':
                        import leave; leave.worker_unavailable(con, ref, '已暫停（待整理）')     # 稽核：跟一般暫停一樣處理他手上的單
                decision = f'移到「{name}」'
            elif action == 'merge':
                if ent == 'customers':
                    board.merge_customers(con, p['target'], ref)
                elif ent == 'workers':
                    board.merge_workers(con, p['target'], ref)
                else:
                    raise ValueError('這類資料不能合併')
                decision = f"合併到 {label_of(con, ent, p['target'])}"
            elif action == 'trash':
                to_trash(con, ent, ref, t['reason']); con.execute('RELEASE t'); done += 1; continue
            else:
                raise ValueError('不支援的操作')
            con.execute("UPDATE triage SET status='done',decided_at=?,decided_by=?,decision=? WHERE id=?", (c.now(), getattr(c.CTX, 'actor', ''), decision, tid))
            c.audit(con, 'triage', ent, ref, after={'decision': decision})
            con.execute('RELEASE t'); done += 1
        except (ValueError, KeyError) as e:
            con.execute('ROLLBACK TO t'); con.execute('RELEASE t')
            errors.append({'id': tid, 'error': str(e)})
    return {'done': done, 'errors': errors}

def history(con):
    return [{**t, 'label': label_of(con, t['entity'], t['ref_id'])} for t in c.rows(con, "SELECT * FROM triage WHERE status='done' ORDER BY decided_at DESC LIMIT 200")]
